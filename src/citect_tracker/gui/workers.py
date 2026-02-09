"""Background QThread workers for snapshot and diff operations."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from ..core.dbf_writer import RecoverError, recover_record
from ..core.diff_engine import DiffEngine
from ..core.models import DiffSummary, RecordDiff, SnapshotMeta, TableType
from ..core.snapshot_engine import SnapshotEngine
from ..storage.database import Database


class SnapshotWorker(QThread):
    """Takes a snapshot in a background thread.

    Creates its own Database connection to avoid SQLite cross-thread errors.
    """

    progress = pyqtSignal(int, int, str)  # current, total, message
    finished = pyqtSignal(object)  # SnapshotMeta
    error = pyqtSignal(str)

    def __init__(
        self,
        db_path: Path,
        source_dir: Path,
        label: str = "",
        excluded_projects: Optional[set[str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.db_path = db_path
        self.source_dir = source_dir
        self.label = label
        self.excluded_projects = excluded_projects

    def run(self) -> None:
        try:
            db = Database(self.db_path)
            db.connect()
            try:
                engine = SnapshotEngine(db)
                meta = engine.take_snapshot(
                    self.source_dir,
                    label=self.label,
                    progress_callback=self._on_progress,
                    excluded_projects=self.excluded_projects,
                )
                self.finished.emit(meta)
            finally:
                db.close()
        except Exception as e:
            self.error.emit(str(e))

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self.progress.emit(current, total, message)


class DiffWorker(QThread):
    """Compares two snapshots in a background thread.

    Creates its own Database connection to avoid SQLite cross-thread errors.
    """

    finished = pyqtSignal(object)  # DiffSummary
    error = pyqtSignal(str)

    def __init__(
        self,
        db_path: Path,
        old_id: int,
        new_id: int,
        project_filter: Optional[set[str]] = None,
        table_filter: Optional[TableType] = None,
        excluded_projects: Optional[set[str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.db_path = db_path
        self.old_id = old_id
        self.new_id = new_id
        self.project_filter = project_filter
        self.table_filter = table_filter
        self.excluded_projects = excluded_projects

    def run(self) -> None:
        try:
            db = Database(self.db_path)
            db.connect()
            try:
                engine = DiffEngine(db)
                summary = engine.compare_snapshots(
                    self.old_id,
                    self.new_id,
                    project_filter=self.project_filter,
                    table_filter=self.table_filter,
                    excluded_projects=self.excluded_projects,
                )
                self.finished.emit(summary)
            finally:
                db.close()
        except Exception as e:
            self.error.emit(str(e))


class RecoverWorker(QThread):
    """Recovers records to old values in a background thread."""

    progress = pyqtSignal(int, int, str)  # current, total, message
    finished = pyqtSignal(list, list)  # successes, errors
    error = pyqtSignal(str)

    def __init__(
        self,
        source_dir: Path,
        diffs: list[RecordDiff],
        parent=None,
    ):
        super().__init__(parent)
        self.source_dir = source_dir
        self.diffs = diffs

    def run(self) -> None:
        try:
            successes: list[str] = []
            errors: list[str] = []
            total = len(self.diffs)

            for i, diff in enumerate(self.diffs):
                self.progress.emit(
                    i + 1, total,
                    f"Recovering {diff.record_key}..."
                )
                try:
                    msg = recover_record(self.source_dir, diff)
                    successes.append(msg)
                except RecoverError as e:
                    errors.append(str(e))

            self.finished.emit(successes, errors)
        except Exception as e:
            self.error.emit(str(e))
