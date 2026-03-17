"""Options/preferences dialog."""

from __future__ import annotations

from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QVBoxLayout,
)

from .app_settings import settings


def apply_theme(theme: str) -> None:
    """Apply 'light', 'dark', or 'system' theme to the application."""
    app = QApplication.instance()
    assert app is not None
    if theme == "dark":
        app.setStyle("Fusion")  # type: ignore[attr-defined]
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(45, 45, 45))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(45, 45, 45))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(45, 45, 45))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Button, QColor(60, 60, 60))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 100, 100))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(120, 120, 120))
        app.setPalette(palette)  # type: ignore[attr-defined]
    elif theme == "light":
        app.setStyle("Fusion")  # type: ignore[attr-defined]
        app.setPalette(QPalette())  # type: ignore[attr-defined]
    else:  # system — no-op, leave platform default
        pass


class OptionsDialog(QDialog):
    """Application options/preferences dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setMinimumWidth(380)
        self._setup_ui()
        self._load()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # -- General --
        general_group = QGroupBox("General")
        general_layout = QFormLayout(general_group)

        self._auto_backup_cb = QCheckBox(
            "Take snapshot automatically on ctback32 backup"
        )
        general_layout.addRow(self._auto_backup_cb)

        self._auto_restore_cb = QCheckBox(
            "Take snapshot automatically after ctback32 restore"
        )
        general_layout.addRow(self._auto_restore_cb)

        self._auto_compare_cb = QCheckBox(
            "Auto-compare two most recent snapshots on startup"
        )
        general_layout.addRow(self._auto_compare_cb)

        layout.addWidget(general_group)

        # -- Appearance --
        appearance_group = QGroupBox("Appearance")
        appearance_layout = QFormLayout(appearance_group)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["System", "Light", "Dark"])
        appearance_layout.addRow("Theme:", self._theme_combo)

        layout.addWidget(appearance_group)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load(self) -> None:
        self._auto_backup_cb.setChecked(settings.auto_backup)
        self._auto_restore_cb.setChecked(settings.auto_restore)
        self._auto_compare_cb.setChecked(settings.auto_compare)
        idx = self._theme_combo.findText(settings.theme)
        self._theme_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _save_and_accept(self) -> None:
        settings.auto_backup = self._auto_backup_cb.isChecked()
        settings.auto_restore = self._auto_restore_cb.isChecked()
        settings.auto_compare = self._auto_compare_cb.isChecked()
        theme = self._theme_combo.currentText()
        settings.theme = theme
        apply_theme(theme.lower())
        self.accept()
