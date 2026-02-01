"""Search and filter controls for the diff viewer."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QWidget


class FilterBar(QWidget):
    """Search box and change-type toggle checkboxes."""

    filter_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter by key, project, or table...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.filter_changed.emit)
        layout.addWidget(self.search_input, 1)

        self.show_added = QCheckBox("Added")
        self.show_added.setChecked(True)
        self.show_added.setStyleSheet("QCheckBox { color: #50c850; }")
        self.show_added.toggled.connect(self.filter_changed.emit)
        layout.addWidget(self.show_added)

        self.show_modified = QCheckBox("Modified")
        self.show_modified.setChecked(True)
        self.show_modified.setStyleSheet("QCheckBox { color: #dcb432; }")
        self.show_modified.toggled.connect(self.filter_changed.emit)
        layout.addWidget(self.show_modified)

        self.show_deleted = QCheckBox("Deleted")
        self.show_deleted.setChecked(True)
        self.show_deleted.setStyleSheet("QCheckBox { color: #dc5050; }")
        self.show_deleted.toggled.connect(self.filter_changed.emit)
        layout.addWidget(self.show_deleted)

    @property
    def search_text(self) -> str:
        return self.search_input.text().strip().lower()

    @property
    def visible_types(self) -> set[str]:
        types: set[str] = set()
        if self.show_added.isChecked():
            types.add("added")
        if self.show_modified.isChecked():
            types.add("modified")
        if self.show_deleted.isChecked():
            types.add("deleted")
        return types
