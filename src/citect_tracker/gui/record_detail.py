"""Field-by-field comparison view for a single record change."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.models import ChangeType, RecordDiff

# Muted highlight colors that work on dark backgrounds
_CHANGED_FG = QColor(220, 180, 50)   # Amber text for field name
_OLD_FG = QColor(220, 100, 100)      # Red-ish for old value
_NEW_FG = QColor(100, 220, 100)      # Green-ish for new value


def _change_color(change_type: ChangeType) -> str:
    if change_type == ChangeType.ADDED:
        return "#50c850"
    elif change_type == ChangeType.MODIFIED:
        return "#dcb432"
    else:
        return "#dc5050"


class RecordDetailDialog(QDialog):
    """Dialog showing field-level comparison for a record change."""

    def __init__(self, diff: RecordDiff, parent=None):
        super().__init__(parent)
        self.diff = diff
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle(f"Record Detail: {self.diff.record_key}")
        self.setMinimumSize(700, 500)

        layout = QVBoxLayout(self)

        # Header
        header_layout = QHBoxLayout()
        type_label = QLabel(self.diff.change_type.value.upper())
        type_label.setStyleSheet(
            f"font-weight: bold; color: {_change_color(self.diff.change_type)}; "
            f"font-size: 14px;"
        )
        header_layout.addWidget(type_label)

        info_label = QLabel(
            f"  {self.diff.table_type.display_name} | "
            f"Project: {self.diff.project_name}"
        )
        header_layout.addWidget(info_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        key_label = QLabel(f"Key: {self.diff.record_key}")
        key_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(key_label)

        # Field comparison table
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Field", "Old Value", "New Value"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        self._populate_table()

    def _populate_table(self) -> None:
        old = self.diff.old_fields or {}
        new = self.diff.new_fields or {}
        all_fields = sorted(set(old.keys()) | set(new.keys()))

        self.table.setRowCount(len(all_fields))

        for row, field_name in enumerate(all_fields):
            old_val = old.get(field_name, "")
            new_val = new.get(field_name, "")

            field_item = QTableWidgetItem(field_name)
            old_item = QTableWidgetItem(old_val)
            new_item = QTableWidgetItem(new_val)

            # Highlight changed fields with text color (works on any bg)
            if field_name in self.diff.changed_fields or old_val != new_val:
                field_item.setForeground(_CHANGED_FG)
                old_item.setForeground(_OLD_FG)
                new_item.setForeground(_NEW_FG)

            self.table.setItem(row, 0, field_item)
            self.table.setItem(row, 1, old_item)
            self.table.setItem(row, 2, new_item)


class RecordDetailPanel(QTableWidget):
    """Inline table below the diff viewer showing field-level changes.

    When a record is selected in the diff table, this shows all fields
    with old and new values side by side.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["Field", "Old Value", "New Value"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.clear_detail()

    def clear_detail(self) -> None:
        self.setRowCount(0)

    def show_diff(self, diff: RecordDiff) -> None:
        old = diff.old_fields or {}
        new = diff.new_fields or {}

        # Show changed fields first, then unchanged
        changed_set = set(diff.changed_fields)
        all_fields = sorted(set(old.keys()) | set(new.keys()))
        changed_first = sorted(changed_set) + [f for f in all_fields if f not in changed_set]

        self.setRowCount(len(changed_first))

        for row, field_name in enumerate(changed_first):
            old_val = old.get(field_name, "")
            new_val = new.get(field_name, "")

            field_item = QTableWidgetItem(field_name)
            old_item = QTableWidgetItem(old_val)
            new_item = QTableWidgetItem(new_val)

            if field_name in changed_set or old_val != new_val:
                field_item.setForeground(_CHANGED_FG)
                old_item.setForeground(_OLD_FG)
                new_item.setForeground(_NEW_FG)

            self.setItem(row, 0, field_item)
            self.setItem(row, 1, old_item)
            self.setItem(row, 2, new_item)
