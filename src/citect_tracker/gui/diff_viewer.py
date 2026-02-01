"""Diff table view with custom model for displaying record changes."""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..core.models import ChangeType, DiffSummary, RecordDiff
from .filter_bar import FilterBar

# Colors that work on both light and dark backgrounds
TYPE_COLORS = {
    ChangeType.ADDED: QColor(80, 200, 80),      # Green
    ChangeType.MODIFIED: QColor(220, 180, 50),   # Amber
    ChangeType.DELETED: QColor(220, 80, 80),     # Red
}


class DiffTableModel(QAbstractTableModel):
    """Table model for diff results. Backed by a flat list of RecordDiff."""

    COLUMNS = ["Type", "Project", "Table", "Key", "Changed Fields", "Old Value", "New Value"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._diffs: list[RecordDiff] = []

    def set_diffs(self, diffs: list[RecordDiff]) -> None:
        self.beginResetModel()
        self._diffs = diffs
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._diffs = []
        self.endResetModel()

    def get_diff(self, row: int) -> Optional[RecordDiff]:
        if 0 <= row < len(self._diffs):
            return self._diffs[row]
        return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._diffs)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        diff = self._diffs[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return diff.change_type.value.upper()
            elif col == 1:
                return diff.project_name
            elif col == 2:
                return diff.table_type.display_name
            elif col == 3:
                return diff.record_key
            elif col == 4:
                return ", ".join(diff.changed_fields) if diff.changed_fields else "--"
            elif col == 5:
                return _summarize_old(diff)
            elif col == 6:
                return _summarize_new(diff)

        elif role == Qt.ItemDataRole.ForegroundRole:
            if col == 0:
                return TYPE_COLORS.get(diff.change_type)
            elif col == 5 and diff.change_type == ChangeType.DELETED:
                return QColor(220, 80, 80)
            elif col == 6 and diff.change_type == ChangeType.ADDED:
                return QColor(80, 200, 80)

        elif role == Qt.ItemDataRole.UserRole:
            return diff

        return None


def _summarize_old(diff: RecordDiff) -> str:
    """Summarize old values for changed fields."""
    if diff.change_type == ChangeType.ADDED:
        return ""
    if diff.change_type == ChangeType.DELETED:
        old = diff.old_fields or {}
        key_field = diff.table_type.key_field
        # Show a few key fields
        parts = []
        for f in list(old.keys())[:3]:
            if f != key_field:
                parts.append(f"{f}={old[f]}")
        return "; ".join(parts) if parts else "(deleted)"
    # Modified
    if not diff.changed_fields or not diff.old_fields:
        return ""
    parts = []
    for f in diff.changed_fields[:3]:
        val = diff.old_fields.get(f, "")
        parts.append(f"{f}={val}" if val else f"{f}=(empty)")
    if len(diff.changed_fields) > 3:
        parts.append(f"...+{len(diff.changed_fields) - 3}")
    return "; ".join(parts)


def _summarize_new(diff: RecordDiff) -> str:
    """Summarize new values for changed fields."""
    if diff.change_type == ChangeType.DELETED:
        return ""
    if diff.change_type == ChangeType.ADDED:
        new = diff.new_fields or {}
        key_field = diff.table_type.key_field
        parts = []
        for f in list(new.keys())[:3]:
            if f != key_field:
                parts.append(f"{f}={new[f]}")
        return "; ".join(parts) if parts else "(added)"
    # Modified
    if not diff.changed_fields or not diff.new_fields:
        return ""
    parts = []
    for f in diff.changed_fields[:3]:
        val = diff.new_fields.get(f, "")
        parts.append(f"{f}={val}" if val else f"{f}=(empty)")
    if len(diff.changed_fields) > 3:
        parts.append(f"...+{len(diff.changed_fields) - 3}")
    return "; ".join(parts)


class DiffFilterProxy(QSortFilterProxyModel):
    """Proxy model that filters diff rows by search text, change type, and project."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_text = ""
        self._visible_types: set[str] = {"added", "modified", "deleted"}
        self._project_filter: Optional[set[str]] = None

    def set_filter(self, search_text: str, visible_types: set[str]) -> None:
        self._search_text = search_text.lower()
        self._visible_types = visible_types
        self.invalidateFilter()

    def set_project_filter(self, projects: Optional[set[str]]) -> None:
        """Set which projects to show (None = all projects)."""
        self._project_filter = projects
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return True

        diff = model.get_diff(source_row)
        if diff is None:
            return True

        # Filter by project
        if self._project_filter and diff.project_name not in self._project_filter:
            return False

        # Filter by change type
        if diff.change_type.value not in self._visible_types:
            return False

        # Filter by search text
        if self._search_text:
            searchable = (
                f"{diff.record_key} {diff.project_name} "
                f"{diff.table_type.value} {' '.join(diff.changed_fields)}"
            ).lower()
            if self._search_text not in searchable:
                return False

        return True


class DiffViewer(QWidget):
    """Combined widget with filter bar and diff table."""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Filter bar
        self.filter_bar = FilterBar()
        self.filter_bar.filter_changed.connect(self._apply_filter)
        layout.addWidget(self.filter_bar)

        # Table view
        self.model = DiffTableModel()
        self.proxy = DiffFilterProxy()
        self.proxy.setSourceModel(self.model)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self.table)

    def set_diff_summary(self, summary: DiffSummary) -> None:
        """Load diff results into the table."""
        all_diffs = summary.all_changes()
        self.model.set_diffs(all_diffs)
        self._apply_filter()

    def clear(self) -> None:
        self.model.clear()

    def get_selected_diff(self) -> Optional[RecordDiff]:
        """Get the RecordDiff for the currently selected row."""
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return None
        source_index = self.proxy.mapToSource(indexes[0])
        return self.model.get_diff(source_index.row())

    def _apply_filter(self) -> None:
        self.proxy.set_filter(
            self.filter_bar.search_text,
            self.filter_bar.visible_types,
        )

    def set_project_filter(self, projects: Optional[set[str]]) -> None:
        """Filter diff view to only show specified projects."""
        self.proxy.set_project_filter(projects)
