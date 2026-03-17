"""Diff table view with custom model for displaying record changes."""

from __future__ import annotations

import csv
import re
from typing import Optional, cast

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence, QTextDocument
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QApplication,
    QHeaderView,
    QLineEdit,
    QMenu,
    QShortcut,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.models import ChangeType, DiffSummary, RecordDiff
from .filter_bar import FilterBar

# Colors that work on both light and dark backgrounds
TYPE_COLORS = {
    ChangeType.ADDED:    QColor(80, 200, 80),    # Green
    ChangeType.MODIFIED: QColor(220, 180, 50),   # Amber
    ChangeType.DELETED:  QColor(220, 80, 80),    # Red
}

# Maps table column index → DiffFilterProxy field key (used by HighlightDelegate)
_COL_FIELD: dict[int, str] = {
    1: "snapshot",
    2: "project",
    3: "table",
    4: "key",
    5: "field",
    6: "old_value",
    7: "new_value",
}


class HighlightDelegate(QStyledItemDelegate):
    """Draws cells normally, then overlays amber highlights on matched text."""

    def __init__(self, proxy: "DiffFilterProxy", parent=None):
        super().__init__(parent)
        self._proxy = proxy

    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        if painter is None:
            return
        col = index.column()
        field = _COL_FIELD.get(col)
        text: str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        pattern: str = self._proxy._field_patterns.get(field, "").strip() if field else ""

        if not pattern or not text:
            super().paint(painter, option, index)
            return

        # Find match spans
        if field in self._proxy._regex_fields:
            compiled = self._proxy._compiled.get(field)
            if compiled is None:
                super().paint(painter, option, index)
                return
            spans = [(m.start(), m.end()) for m in compiled.finditer(text)]
        else:
            spans: list[tuple[int, int]] = []
            lower, pl = text.lower(), pattern.lower()
            start = 0
            while (pos := lower.find(pl, start)) != -1:
                spans.append((pos, pos + len(pl)))
                start = pos + len(pl)

        if not spans:
            super().paint(painter, option, index)
            return

        # Build HTML: preserve ForegroundRole colour, highlight spans in amber
        def esc(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;")

        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        color_style = f"color:{fg.name()};" if fg else ""
        parts: list[str] = []
        prev = 0
        for s, e in spans:
            parts.append(f'<span style="{color_style}">{esc(text[prev:s])}</span>')
            parts.append(f'<span style="background:#c8a000;color:#000;">{esc(text[s:e])}</span>')
            prev = e
        parts.append(f'<span style="{color_style}">{esc(text[prev:])}</span>')

        # Draw the cell background / selection state without text
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = QApplication.style()
        assert style is not None
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter)

        # Render highlighted HTML into the text rect
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt)
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setHtml("".join(parts))
        painter.save()
        painter.translate(text_rect.topLeft())
        doc.drawContents(painter)
        painter.restore()


class DiffTableModel(QAbstractTableModel):
    """Table model for diff results. Backed by a flat list of RecordDiff."""

    COLUMNS = ["Type", "Snapshot", "Project", "Table", "Key", "Changed Fields", "Old Value", "New Value"]

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
                return diff.snapshot_label
            elif col == 2:
                return diff.project_name
            elif col == 3:
                return diff.table_type.display_name
            elif col == 4:
                return diff.record_key
            elif col == 5:
                return ", ".join(diff.changed_fields) if diff.changed_fields else "--"
            elif col == 6:
                return _summarize_old(diff)
            elif col == 7:
                return _summarize_new(diff)

        elif role == Qt.ItemDataRole.ForegroundRole:
            if col == 0:
                return TYPE_COLORS.get(diff.change_type)
            elif col == 6 and diff.change_type == ChangeType.DELETED:
                return QColor(220, 80, 80)
            elif col == 7 and diff.change_type == ChangeType.ADDED:
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


class FilterHeaderView(QHeaderView):
    """Horizontal header with a QLineEdit filter input embedded under each column label.

    Clicking the label area still sorts; the input sits below the label text.
    Each input has a small '.*' toggle button on the right edge to enable regex
    for that field individually. Invalid patterns highlight red and match nothing.
    """

    filter_changed = pyqtSignal()

    _LABEL_HEIGHT = 20   # pixels reserved for the column label text
    _INPUT_HEIGHT = 18   # pixels for the embedded QLineEdit
    _PAD = 1             # gap between label area and input
    _BTN_W = 24          # width of the per-field .* toggle button

    # col_index -> (field_key, base_label)
    _COL_FIELDS: dict[int, tuple[str, str]] = {
        1: ("snapshot",  "Snapshot"),
        2: ("project",   "Project"),
        3: ("table",     "Table"),
        4: ("key",       "Key"),
        5: ("field",     "Field"),
        6: ("old_value", "Old value"),
        7: ("new_value", "New value"),
    }

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._inputs: dict[str, QLineEdit] = {}
        self._col_inputs: dict[int, QLineEdit] = {}
        self._regex_btns: dict[str, QToolButton] = {}

        total_h = self._LABEL_HEIGHT + self._PAD + self._INPUT_HEIGHT
        self.setFixedHeight(total_h)
        self.setDefaultAlignment(Qt.Alignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop))  # type: ignore[arg-type]

        for col, (field, label) in self._COL_FIELDS.items():
            inp = QLineEdit(self)
            inp.setPlaceholderText(f"{label}...")
            inp.setToolTip(
                f"Filter by {label.lower()}. "
                "Click .* to enable regex for this field."
            )
            inp.setClearButtonEnabled(True)
            inp.setFrame(False)
            inp.setTextMargins(0, 0, self._BTN_W + 2, 0)
            inp.textChanged.connect(self._on_input_changed)
            inp.hide()  # hidden until _reposition places them correctly
            self._inputs[field] = inp
            self._col_inputs[col] = inp

            btn = QToolButton(inp)
            btn.setText(".*")
            btn.setCheckable(True)
            btn.setChecked(False)
            btn.setFixedSize(self._BTN_W, self._INPUT_HEIGHT)
            btn.setStyleSheet(
                "QToolButton { font-size: 10px; padding: 0px; border: none; color: #888; }"
                "QToolButton:checked { color: #50aaff; font-weight: bold; }"
            )
            btn.setCursor(Qt.CursorShape.ArrowCursor)
            btn.toggled.connect(self._on_input_changed)
            inp.textChanged.connect(lambda text, b=btn: b.setVisible(not bool(text)))
            self._regex_btns[field] = btn

        self.sectionResized.connect(self._reposition)
        self.sectionMoved.connect(self._reposition)
        self.geometriesChanged.connect(self._reposition)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def showEvent(self, a0) -> None:
        super().showEvent(a0)
        self._reposition()

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(self._LABEL_HEIGHT + self._PAD + self._INPUT_HEIGHT)
        return hint

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def regex_fields(self) -> set[str]:
        """Return the set of field keys that have regex mode enabled."""
        return {field for field, btn in self._regex_btns.items() if btn.isChecked()}

    @property
    def field_patterns(self) -> dict[str, str]:
        return {field: inp.text().strip() for field, inp in self._inputs.items()}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reposition(self) -> None:
        y = self._LABEL_HEIGHT + self._PAD
        h = self._INPUT_HEIGHT
        for col, inp in self._col_inputs.items():
            if self.isSectionHidden(col):
                inp.hide()
                continue
            x = self.sectionViewportPosition(col)
            w = self.sectionSize(col)
            inp.setGeometry(x + 1, y, w - 2, h)
            inp.show()
            field = self._COL_FIELDS[col][0]
            btn = self._regex_btns.get(field)
            if btn is not None:
                btn.setGeometry(inp.width() - self._BTN_W, 0, self._BTN_W, h)

    def _validate(self) -> None:
        for field, inp in self._inputs.items():
            text = inp.text().strip()
            btn = self._regex_btns.get(field)
            if btn is not None and btn.isChecked() and text:
                try:
                    re.compile(text)
                    inp.setStyleSheet("")
                except re.error:
                    inp.setStyleSheet("QLineEdit { border: 1.5px solid #dc5050; }")
            else:
                inp.setStyleSheet("")

    def _on_input_changed(self) -> None:
        self._validate()
        self.filter_changed.emit()


class DiffFilterProxy(QSortFilterProxyModel):
    """Proxy model that filters diff rows by per-field patterns, change type, and project.

    Each field (key, project, table, field, old_value, new_value) has its own pattern.
    All non-empty patterns must match (AND logic).
    When regex mode is on, each pattern is compiled independently as a regex.
    Invalid patterns cause that field to match nothing.
    Plain mode uses case-insensitive substring matching.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._field_patterns: dict[str, str] = {}
        self._regex_fields: set[str] = set()
        self._visible_types: set[str] = {"added", "modified", "deleted"}
        self._project_filter: Optional[set[str]] = None
        self._compiled: dict[str, Optional[re.Pattern]] = {}

    def set_filter(
        self,
        field_patterns: dict[str, str],
        visible_types: set[str],
        regex_fields: set[str] | None = None,
    ) -> None:
        self._field_patterns = field_patterns
        self._visible_types = visible_types
        self._regex_fields = regex_fields or set()
        self._compiled = {}
        for field in self._regex_fields:
            pattern = field_patterns.get(field, "")
            if pattern:
                try:
                    self._compiled[field] = re.compile(pattern, re.IGNORECASE)
                except re.error:
                    self._compiled[field] = None  # Invalid — will match nothing
        self.invalidateFilter()

    def set_project_filter(self, projects: Optional[set[str]]) -> None:
        """Set which projects to show (None = all projects)."""
        self._project_filter = projects
        self.invalidateFilter()

    def _matches(self, field: str, pattern: str, candidates: list[str]) -> bool:
        if field in self._regex_fields:
            compiled = self._compiled.get(field)
            if compiled is None:
                return False
            return any(compiled.search(c) for c in candidates)
        return any(pattern.lower() in c.lower() for c in candidates)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return True

        diff = cast(DiffTableModel, model).get_diff(source_row)
        if diff is None:
            return True

        # Filter by project
        if self._project_filter and diff.project_name not in self._project_filter:
            return False

        # Filter by change type
        if diff.change_type.value not in self._visible_types:
            return False

        # Filter by per-field patterns (all non-empty must match)
        if not any(self._field_patterns.values()):
            return True

        field_candidates: dict[str, list[str]] = {
            "key":       [diff.record_key],
            "project":   [diff.project_name],
            "table":     [diff.table_type.value],
            "field":     diff.changed_fields,
            "old_value": list((diff.old_fields or {}).values()),
            "new_value": list((diff.new_fields or {}).values()),
            "snapshot":  [diff.snapshot_label],
        }

        for field, pattern in self._field_patterns.items():
            if not pattern:
                continue
            if not self._matches(field, pattern, field_candidates.get(field, [])):
                return False

        return True


class DiffViewer(QWidget):
    """Combined widget with filter bar and diff table."""

    recover_requested = pyqtSignal(list)  # list[RecordDiff]

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Kept as an attribute so the parent can place it in the summary row
        self.filter_bar = FilterBar()
        self.filter_bar.filter_changed.connect(self._apply_filter)

        # Models
        self.model = DiffTableModel()
        self.proxy = DiffFilterProxy()
        self.proxy.setSourceModel(self.model)

        # Table with filter inputs embedded in the header
        self.table = QTableView()

        self.filter_header = FilterHeaderView()
        self.filter_header.filter_changed.connect(self._apply_filter)
        self.table.setHorizontalHeader(self.filter_header)

        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        # Context menu
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        self.filter_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.filter_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.filter_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.filter_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.filter_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.filter_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.filter_header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.filter_header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)

        # Set up column sorting manually — setSortingEnabled() uses private Qt slots
        # that don't reliably wire up when the header is replaced after construction.
        self.filter_header.setSectionsClickable(True)
        self.filter_header.setSortIndicatorShown(True)
        # Qt toggles the indicator internally on mouse-release; we just react to the result.
        self.filter_header.sortIndicatorChanged.connect(self.proxy.sort)

        # Reposition filter inputs when the user scrolls horizontally
        hbar = self.table.horizontalScrollBar()
        if hbar is not None:
            hbar.valueChanged.connect(self.filter_header._reposition)

        # Highlight matched filter text in cells
        self.table.setItemDelegate(HighlightDelegate(self.proxy))

        # Keyboard row navigation
        QShortcut(QKeySequence("Ctrl+Down"), self.table).activated.connect(self._go_next)
        QShortcut(QKeySequence("Ctrl+Up"), self.table).activated.connect(self._go_prev)

        layout.addWidget(self.table)

    def set_diff_summary(self, summary: DiffSummary) -> None:
        """Load diff results into the table."""
        all_diffs = summary.all_changes()
        self.model.set_diffs(all_diffs)
        self._apply_filter()
        for col in (0, 2, 3, 4, 5):  # Type, Project, Table, Key, Changed Fields
            self.table.resizeColumnToContents(col)

    def clear(self) -> None:
        self.model.clear()

    def get_selected_diff(self) -> Optional[RecordDiff]:
        """Get the RecordDiff for the currently selected row."""
        sel = self.table.selectionModel()
        if sel is None:
            return None
        indexes = sel.selectedRows()
        if not indexes:
            return None
        source_index = self.proxy.mapToSource(indexes[0])
        return self.model.get_diff(source_index.row())

    def get_selected_diffs(self) -> list[RecordDiff]:
        """Get RecordDiff objects for all selected rows."""
        sel = self.table.selectionModel()
        if sel is None:
            return []
        indexes = sel.selectedRows()
        diffs = []
        for idx in indexes:
            source_index = self.proxy.mapToSource(idx)
            diff = self.model.get_diff(source_index.row())
            if diff:
                diffs.append(diff)
        return diffs

    def _show_context_menu(self, position) -> None:
        """Show right-click context menu."""
        selected = self.get_selected_diffs()
        if not selected:
            return

        menu = QMenu(self)

        count = len(selected)
        label = f"Revert {count} selected change(s)"
        recover_action = QAction(label, self)
        recover_action.triggered.connect(
            lambda: self.recover_requested.emit(selected)
        )
        menu.addAction(recover_action)

        vp = self.table.viewport()
        if vp is not None:
            menu.exec_(vp.mapToGlobal(position))

    def _apply_filter(self) -> None:
        self.proxy.set_filter(
            self.filter_header.field_patterns,
            self.filter_bar.visible_types,
            self.filter_header.regex_fields,
        )
        vp = self.table.viewport()
        if vp is not None:
            vp.update()

    def export_to_csv(self, file_path: str) -> None:
        """Export the current filtered/sorted diff view to a CSV file."""
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(DiffTableModel.COLUMNS)
            for row in range(self.proxy.rowCount()):
                writer.writerow([
                    self.proxy.data(self.proxy.index(row, col), Qt.ItemDataRole.DisplayRole) or ""
                    for col in range(self.proxy.columnCount())
                ])

    def _go_next(self) -> None:
        n = self.proxy.rowCount()
        if not n:
            return
        row = (self.table.currentIndex().row() + 1) % n
        self.table.selectRow(row)
        self.table.scrollTo(self.proxy.index(row, 0))

    def _go_prev(self) -> None:
        n = self.proxy.rowCount()
        if not n:
            return
        row = (self.table.currentIndex().row() - 1) % n
        self.table.selectRow(row)
        self.table.scrollTo(self.proxy.index(row, 0))

    def set_project_filter(self, projects: Optional[set[str]]) -> None:
        """Filter diff view to only show specified projects."""
        self.proxy.set_project_filter(projects)
