"""Application entry point and setup."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

from .gui.main_window import MainWindow
from .storage.database import Database


def main() -> None:
    """Launch the Citect Tracker application."""
    app = QApplication(sys.argv)
    app.setApplicationName("Citect Tracker")
    app.setOrganizationName("CitectTracker")

    settings = QSettings()

    # Determine source directory from command line or saved setting
    source_dir = None
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1])
        if (candidate / "MASTER.DBF").exists():
            source_dir = candidate
        elif candidate.name == "MASTER.DBF" and candidate.exists():
            source_dir = candidate.parent

    # Try saved directory if no CLI argument
    if source_dir is None:
        saved = settings.value("last_dbf_directory", "")
        if saved:
            candidate = Path(saved)
            if (candidate / "MASTER.DBF").exists():
                source_dir = candidate

    if source_dir is None:
        # Ask user to select directory
        dir_path = QFileDialog.getExistingDirectory(
            None, "Select directory containing MASTER.DBF"
        )
        if dir_path:
            candidate = Path(dir_path)
            if (candidate / "MASTER.DBF").exists():
                source_dir = candidate
            else:
                QMessageBox.critical(
                    None,
                    "No MASTER.DBF",
                    f"No MASTER.DBF found in:\n{dir_path}\n\n"
                    "Please select a directory containing MASTER.DBF.",
                )
                sys.exit(1)
        else:
            sys.exit(0)

    # Save the directory for next launch
    settings.setValue("last_dbf_directory", str(source_dir))

    # Open database alongside the source directory
    db_path = source_dir / "citect_tracker.db"
    db = Database(db_path)
    db.connect()

    try:
        window = MainWindow(db, source_dir)
        window.show()
        exit_code = app.exec()
    finally:
        db.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
