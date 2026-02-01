"""Snapshot timeline list and snapshot management controls."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.models import SnapshotMeta


class SnapshotPanel(QWidget):
    """Panel showing snapshot timeline and comparison controls."""

    take_snapshot_requested = pyqtSignal()
    compare_requested = pyqtSignal(int, int)  # old_id, new_id
    delete_requested = pyqtSignal(int)  # snapshot_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snapshots: list[SnapshotMeta] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Snapshot list
        list_label = QLabel("Snapshot Timeline")
        list_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(list_label)

        self.snapshot_list = QListWidget()
        self.snapshot_list.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection
        )
        layout.addWidget(self.snapshot_list)

        # Delete button
        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._on_delete)
        layout.addWidget(delete_btn)

        # Take snapshot button
        take_btn = QPushButton("Take Snapshot")
        take_btn.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; "
            "font-weight: bold; padding: 8px; }"
            "QPushButton:hover { background-color: #1565c0; }"
        )
        take_btn.clicked.connect(self.take_snapshot_requested.emit)
        layout.addWidget(take_btn)

    def set_snapshots(self, snapshots: list[SnapshotMeta]) -> None:
        """Update the snapshot list."""
        self._snapshots = snapshots
        self.snapshot_list.clear()

        for snap in snapshots:
            text = (
                f"{snap.timestamp.strftime('%Y-%m-%d %H:%M')} | "
                f"{snap.label}\n"
                f"  {snap.project_count} projects, "
                f"{snap.total_records:,} records"
            )
            item = QListWidgetItem(text)
            item.setData(256, snap.snapshot_id)  # UserRole
            self.snapshot_list.addItem(item)

    def get_selected_snapshot_id(self) -> int | None:
        items = self.snapshot_list.selectedItems()
        if items:
            return items[0].data(256)
        return None

    def _on_delete(self) -> None:
        sid = self.get_selected_snapshot_id()
        if sid is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete Snapshot",
            "Delete the selected snapshot? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_requested.emit(sid)


class SnapshotCompareBar(QWidget):
    """Horizontal bar with two snapshot dropdowns for comparison."""

    compare_requested = pyqtSignal(int, int)  # old_id, new_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snapshots: list[SnapshotMeta] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("Compare:"))

        self.combo_old = QComboBox()
        self.combo_old.setMinimumWidth(200)
        layout.addWidget(self.combo_old)

        layout.addWidget(QLabel("<->"))

        self.combo_new = QComboBox()
        self.combo_new.setMinimumWidth(200)
        layout.addWidget(self.combo_new)

        self.compare_btn = QPushButton("Compare")
        self.compare_btn.clicked.connect(self._on_compare)
        layout.addWidget(self.compare_btn)

        layout.addStretch()

    def set_snapshots(self, snapshots: list[SnapshotMeta]) -> None:
        self._snapshots = snapshots
        self.combo_old.clear()
        self.combo_new.clear()

        for snap in snapshots:
            label = f"{snap.timestamp.strftime('%Y-%m-%d %H:%M')} - {snap.label}"
            self.combo_old.addItem(label, snap.snapshot_id)
            self.combo_new.addItem(label, snap.snapshot_id)

        # Default: compare two most recent (if available)
        if len(snapshots) >= 2:
            self.combo_old.setCurrentIndex(1)  # Second newest
            self.combo_new.setCurrentIndex(0)  # Newest

    def set_selection(self, old_id: int, new_id: int) -> None:
        """Set the combo box selections to specific snapshot IDs."""
        old_idx = self.combo_old.findData(old_id)
        new_idx = self.combo_new.findData(new_id)
        if old_idx >= 0:
            self.combo_old.setCurrentIndex(old_idx)
        if new_idx >= 0:
            self.combo_new.setCurrentIndex(new_idx)

    def _on_compare(self) -> None:
        old_id = self.combo_old.currentData()
        new_id = self.combo_new.currentData()
        if old_id is not None and new_id is not None and old_id != new_id:
            self.compare_requested.emit(old_id, new_id)
        elif old_id == new_id:
            QMessageBox.warning(
                self,
                "Same Snapshot",
                "Please select two different snapshots to compare.",
            )
