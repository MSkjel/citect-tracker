"""Field-by-field comparison view for a single record change."""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
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


def _setup_table(table: QTableWidget) -> None:
    """Apply common table settings and clipboard context menu."""
    table.setColumnCount(3)
    table.setHorizontalHeaderLabels(["Field", "Old Value", "New Value"])
    header = table.horizontalHeader()
    assert header is not None
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    table.customContextMenuRequested.connect(lambda pos: _show_copy_menu(table, pos))


def _show_copy_menu(table: QTableWidget, pos) -> None:
    item = table.itemAt(pos)
    if item is None:
        return
    menu = QMenu(table)
    copy_cell = QAction("Copy cell", table)
    cb = QApplication.clipboard()
    assert cb is not None
    copy_cell.triggered.connect(lambda: cb.setText(item.text()))
    menu.addAction(copy_cell)

    row = item.row()
    parts = []
    for col in range(table.columnCount()):
        cell = table.item(row, col)
        parts.append(cell.text() if cell else "")
    copy_row = QAction("Copy row", table)
    copy_row.triggered.connect(lambda: cb.setText("\t".join(parts)))
    menu.addAction(copy_row)

    vp = table.viewport()
    if vp is not None:
        menu.exec_(vp.mapToGlobal(pos))


def _populate_table(
    table: QTableWidget,
    diff: RecordDiff,
    changed_only: bool,
    changed_first: bool = False,
) -> None:
    """Fill table with field comparison rows."""
    old = diff.old_fields or {}
    new = diff.new_fields or {}
    changed_set = set(diff.changed_fields)
    all_fields = sorted(set(old.keys()) | set(new.keys()))

    if changed_first:
        ordered = sorted(changed_set) + [f for f in all_fields if f not in changed_set]
    else:
        ordered = all_fields

    if changed_only:
        ordered = [f for f in ordered if f in changed_set or old.get(f) != new.get(f)]

    table.setRowCount(len(ordered))
    for row, field_name in enumerate(ordered):
        old_val = old.get(field_name, "")
        new_val = new.get(field_name, "")

        field_item = QTableWidgetItem(field_name)
        old_item = QTableWidgetItem(old_val)
        new_item = QTableWidgetItem(new_val)

        if field_name in changed_set or old_val != new_val:
            field_item.setForeground(_CHANGED_FG)
            old_item.setForeground(_OLD_FG)
            new_item.setForeground(_NEW_FG)

        table.setItem(row, 0, field_item)
        table.setItem(row, 1, old_item)
        table.setItem(row, 2, new_item)


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

        # "Changed fields only" toggle
        self._changed_only_cb = QCheckBox("Changed fields only")
        self._changed_only_cb.toggled.connect(self._repopulate)
        layout.addWidget(self._changed_only_cb)

        # Field comparison table
        self.table = QTableWidget()
        _setup_table(self.table)
        layout.addWidget(self.table)

        self._repopulate()

    def _repopulate(self) -> None:
        _populate_table(
            self.table,
            self.diff,
            changed_only=self._changed_only_cb.isChecked(),
        )


class RecordDetailPanel(QWidget):
    """Inline panel below the diff viewer showing field-level changes.

    When a record is selected in the diff table, this shows all fields
    with old and new values side by side. Changed fields are shown first.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_diff: Optional[RecordDiff] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._changed_only_cb = QCheckBox("Changed fields only")
        self._changed_only_cb.toggled.connect(self._repopulate)
        layout.addWidget(self._changed_only_cb)

        self._table = QTableWidget()
        _setup_table(self._table)
        layout.addWidget(self._table)

    def clear_detail(self) -> None:
        self._last_diff = None
        self._table.setRowCount(0)

    def show_diff(self, diff: RecordDiff) -> None:
        self._last_diff = diff
        self._repopulate()

    def _repopulate(self) -> None:
        if self._last_diff is None:
            return
        _populate_table(
            self._table,
            self._last_diff,
            changed_only=self._changed_only_cb.isChecked(),
            changed_first=True,
        )
