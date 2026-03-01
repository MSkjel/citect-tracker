"""Snapshot timeline list and snapshot management controls."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAction,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
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
    rename_requested = pyqtSignal(int, str)   # snapshot_id, new_label
    notes_changed = pyqtSignal(int, str)       # snapshot_id, new_notes

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
        self.snapshot_list.itemDoubleClicked.connect(self._on_rename)
        self.snapshot_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.snapshot_list.customContextMenuRequested.connect(self._on_context_menu)
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
            header = f"{snap.timestamp.strftime('%Y-%m-%d %H:%M')} | {snap.label}"
            if snap.taken_by:
                header += f"  (by {snap.taken_by})"
            lines = [
                header,
                f"  {snap.project_count} projects, {snap.total_records:,} records",
            ]
            if snap.notes:
                first_line = snap.notes.split("\n")[0]
                suffix = "…" if "\n" in snap.notes or len(first_line) > 60 else ""
                if len(first_line) > 60:
                    first_line = first_line[:60]
                lines.append(f"  Note: {first_line}{suffix}")
            item = QListWidgetItem("\n".join(lines))
            item.setData(256, snap.snapshot_id)  # UserRole
            self.snapshot_list.addItem(item)

    def get_selected_snapshot_id(self) -> int | None:
        items = self.snapshot_list.selectedItems()
        if items:
            return items[0].data(256)
        return None

    def _get_snapshot_meta(self, snapshot_id: int) -> SnapshotMeta | None:
        for snap in self._snapshots:
            if snap.snapshot_id == snapshot_id:
                return snap
        return None

    def _on_rename(self, item: QListWidgetItem) -> None:
        sid = item.data(256)
        snap = self._get_snapshot_meta(sid)
        current_label = snap.label if snap else ""
        new_label, ok = QInputDialog.getText(
            self, "Edit Label", "Snapshot label:", text=current_label
        )
        if ok:
            self.rename_requested.emit(sid, new_label)

    def _on_edit_notes(self) -> None:
        sid = self.get_selected_snapshot_id()
        if sid is None:
            return
        snap = self._get_snapshot_meta(sid)
        current_notes = snap.notes if snap else ""
        new_notes, ok = QInputDialog.getMultiLineText(
            self, "Edit Notes", "Notes:", text=current_notes
        )
        if ok:
            self.notes_changed.emit(sid, new_notes)

    def _on_context_menu(self, pos) -> None:
        if self.get_selected_snapshot_id() is None:
            return
        menu = QMenu(self)
        menu.addAction(QAction("Edit label", self, triggered=lambda: self._on_rename(
            self.snapshot_list.currentItem()
        )))
        menu.addAction(QAction("Edit notes", self, triggered=self._on_edit_notes))
        menu.exec_(self.snapshot_list.mapToGlobal(pos))

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
