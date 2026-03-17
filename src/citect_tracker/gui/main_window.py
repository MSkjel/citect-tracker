"""Main application window with splitter layout."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..core.diff_engine import DiffEngine
from ..core.models import DiffSummary, RecordDiff, SnapshotMeta
from ..core.project_discovery import discover_projects
from ..core.snapshot_engine import SnapshotEngine
from ..storage.database import Database
from .diff_viewer import DiffViewer
from .app_settings import settings
from .options_dialog import OptionsDialog, apply_theme
from .project_tree import ProjectTree
from .record_detail import RecordDetailDialog, RecordDetailPanel
from .snapshot_panel import SnapshotCompareBar, SnapshotPanel
from .watchers import ProcessWatcher
from .workers import DiffWorker, RecoverWorker, SnapshotWorker


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, db: Database, source_dir: Optional[Path] = None, user_name: str = ""):
        super().__init__()
        self.db = db
        self.source_dir = source_dir
        self._user_name = user_name
        self.snapshot_engine = SnapshotEngine(db)
        self.diff_engine = DiffEngine(db)
        self._current_diff: Optional[DiffSummary] = None
        self._active_worker: Optional[SnapshotWorker | DiffWorker | RecoverWorker] = None
        self._project_filter: Optional[set[str]] = None
        self._snapshots: list[SnapshotMeta] = []

        self._proc_watcher = ProcessWatcher(self)
        self._proc_watcher.backup_detected.connect(self._on_ctback_backup)
        self._proc_watcher.restore_completed.connect(self._on_ctback_restore)
        if source_dir:
            self._proc_watcher.source_dir = str(source_dir)

        apply_theme(settings.theme.lower())

        self.setMinimumSize(1200, 700)
        self._setup_ui()
        self._setup_menu()
        self._load_initial_data()
        self._update_window_title()
        if settings.auto_backup or settings.auto_restore:
            self._proc_watcher.start()

    def _update_window_title(self) -> None:
        db_name = self.db.db_path.name
        self.setWindowTitle(f"Citect Tracker. {db_name}  [{self._user_name}]")

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # Main splitter: left panel | right panel
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter)

        # -- Left panel --
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Left vertical splitter: project tree | snapshot panel
        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter = self.left_splitter

        self.project_tree = ProjectTree()
        self.project_tree.project_filter_changed.connect(self._on_project_filter_changed)
        self.project_tree.exclusions_changed.connect(self._on_exclusions_changed)
        self.project_tree.hidden_changed.connect(self._on_hidden_changed)
        self.project_tree.view_mode_changed.connect(self._on_view_mode_changed)
        left_splitter.addWidget(self.project_tree)

        self.snapshot_panel = SnapshotPanel()
        self.snapshot_panel.take_snapshot_requested.connect(self._take_snapshot)
        self.snapshot_panel.delete_requested.connect(self._delete_snapshot)
        self.snapshot_panel.rename_requested.connect(self._rename_snapshot)
        self.snapshot_panel.notes_changed.connect(self._edit_snapshot_notes)
        left_splitter.addWidget(self.snapshot_panel)

        left_splitter.setSizes([400, 300])
        left_layout.addWidget(left_splitter)
        self.main_splitter.addWidget(left_widget)

        # -- Right panel --
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Compare bar
        self.compare_bar = SnapshotCompareBar()
        self.compare_bar.compare_requested.connect(self._on_compare_requested)
        right_layout.addWidget(self.compare_bar)

        # Summary bar + change-type checkboxes on the same row
        self.diff_viewer = DiffViewer()

        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 4, 0)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(
            "QLabel { padding: 2px 6px; font-size: 13px; }"
        )
        summary_row.addWidget(self.summary_label)
        summary_row.addStretch()
        summary_row.addWidget(self.diff_viewer.filter_bar)
        right_layout.addLayout(summary_row)

        # Right vertical splitter: diff viewer | record detail
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter = self.right_splitter
        diff_sel = self.diff_viewer.table.selectionModel()
        assert diff_sel is not None
        diff_sel.selectionChanged.connect(self._on_diff_selection_changed)
        self.diff_viewer.table.doubleClicked.connect(self._on_diff_double_clicked)
        self.diff_viewer.recover_requested.connect(self._on_recover_requested)
        right_splitter.addWidget(self.diff_viewer)

        self.record_detail = RecordDetailPanel()
        right_splitter.addWidget(self.record_detail)

        right_splitter.setSizes([500, 100])
        right_layout.addWidget(right_splitter)

        self.main_splitter.addWidget(right_widget)
        self.main_splitter.setSizes([300, 900])

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # Restore geometry and splitter positions
        if (geom := settings.window_geometry) is not None:
            self.restoreGeometry(geom)
        for state, widget in [
            (settings.splitter_main, self.main_splitter),
            (settings.splitter_left, self.left_splitter),
            (settings.splitter_right, self.right_splitter),
            (settings.header_diff_table, self.diff_viewer.filter_header),
        ]:
            if state is not None:
                widget.restoreState(state)

    def closeEvent(self, a0) -> None:  # type: ignore[override]
        settings.window_geometry = self.saveGeometry()
        settings.splitter_main = self.main_splitter.saveState()
        settings.splitter_left = self.left_splitter.saveState()
        settings.splitter_right = self.right_splitter.saveState()
        settings.header_diff_table = self.diff_viewer.filter_header.saveState()
        super().closeEvent(a0)

    def _setup_menu(self) -> None:
        menu_bar = self.menuBar()
        assert menu_bar is not None

        file_menu = menu_bar.addMenu("&File")
        assert file_menu is not None
        file_menu.addAction("&Open DBF Directory...", self._open_directory)
        file_menu.addAction("Open &Database...", self._open_database)
        file_menu.addSeparator()
        file_menu.addAction("&Take Snapshot", self._take_snapshot)
        file_menu.addSeparator()
        file_menu.addAction("&Export Diff to CSV...", self._export_diff_csv)
        file_menu.addSeparator()
        file_menu.addAction("Change &User Name...", self._change_user_name)
        file_menu.addSeparator()
        file_menu.addAction("&Options...", self._open_options)
        file_menu.addSeparator()
        file_menu.addAction("&Quit", self.close)

        help_menu = menu_bar.addMenu("&Help")
        assert help_menu is not None
        help_menu.addAction("&About", self._show_about)

    def _load_initial_data(self) -> None:
        """Load snapshots and project tree on startup."""
        snapshots = self.snapshot_engine.list_snapshots()
        self._snapshots = snapshots
        self.snapshot_panel.set_snapshots(snapshots)
        self.compare_bar.set_snapshots(snapshots)

        if self.source_dir and self.source_dir.is_dir():
            self._load_project_tree()

        self.status_bar.showMessage(
            f"{len(snapshots)} snapshot(s) available"
        )

        if len(snapshots) >= 2 and settings.auto_compare:
            QTimer.singleShot(0, lambda: self._compare_snapshots(
                snapshots[1].snapshot_id, snapshots[0].snapshot_id
            ))

    def _load_project_tree(self) -> None:
        """Load project hierarchy from the source directory."""
        if not self.source_dir:
            return
        try:
            # Restore exclusions and hidden from settings before loading
            if excluded := settings.excluded_projects:
                self.project_tree.set_excluded_projects(set(excluded))
            if hidden := settings.hidden_projects:
                self.project_tree.set_hidden_projects(set(hidden))
            if settings.project_flat_mode:
                self.project_tree.set_flat_mode(True)

            projects = discover_projects(self.source_dir)
            self.project_tree.set_projects(projects)
        except Exception as e:
            self.status_bar.showMessage(f"Error loading projects: {e}")

    def _open_directory(self) -> None:
        """Open a directory containing MASTER.DBF."""
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select directory containing MASTER.DBF"
        )
        if dir_path:
            path = Path(dir_path)
            master = path / "MASTER.DBF"
            if not master.exists():
                QMessageBox.warning(
                    self,
                    "No MASTER.DBF",
                    f"No MASTER.DBF found in {dir_path}.\n"
                    "Please select a directory containing MASTER.DBF.",
                )
                return
            self.source_dir = path
            self._proc_watcher.source_dir = str(path)
            settings.last_dbf_directory = str(path)
            self._load_project_tree()
            self.status_bar.showMessage(f"Loaded: {dir_path}")

    def _open_database(self) -> None:
        """Open (or create) a shared tracker database file."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Open / Create Tracker Database",
            "",
            "SQLite Database (*.db)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not path:
            return
        db_path = Path(path)
        if db_path.suffix.lower() != ".db":
            db_path = db_path.with_suffix(".db")
        self.db.close()
        self.db = Database(db_path)
        self.db.connect()
        self.snapshot_engine = SnapshotEngine(self.db)
        self.diff_engine = DiffEngine(self.db)
        settings.db_path = str(db_path)
        self._update_window_title()
        self._current_diff = None
        self.diff_viewer.clear()
        self.record_detail.clear_detail()
        self.summary_label.setText("")
        self.project_tree.clear_change_indicators()
        self._load_initial_data()
        self.status_bar.showMessage(f"Database: {db_path}")

    def _change_user_name(self) -> None:
        """Prompt the user to change their display name."""
        new_name, ok = QInputDialog.getText(
            self, "Change User Name", "Your name:", text=self._user_name
        )
        if ok and new_name.strip():
            self._user_name = new_name.strip()
            settings.user_name = self._user_name
            self._update_window_title()
            self.status_bar.showMessage(f"User name set to: {self._user_name}")

    def _take_snapshot(self) -> None:
        """Take a new snapshot of the current DBF data."""
        if not self.source_dir:
            self._open_directory()
            if not self.source_dir:
                return

        label, ok = QInputDialog.getText(
            self, "Snapshot Label", "Enter a label for this snapshot:",
        )
        if not ok:
            return

        progress = QProgressDialog(
            "Taking snapshot...", "Cancel", 0, 100, self
        )
        progress.setWindowTitle("Snapshot Progress")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        excluded = self.project_tree.get_excluded_projects()
        worker = SnapshotWorker(
            self.db.db_path,
            self.source_dir,
            label=label,
            excluded_projects=excluded or None,
            taken_by=self._user_name,
            parent=self,
        )

        def on_progress(current: int, total: int, message: str) -> None:
            if total > 0:
                progress.setMaximum(total)
                progress.setValue(current)
            progress.setLabelText(message)

        def on_finished(meta: SnapshotMeta) -> None:
            progress.close()
            self._active_worker = None
            self._refresh_snapshots()

            # Auto-compare with previous snapshot if one exists
            snapshots = self.snapshot_engine.list_snapshots()
            if len(snapshots) >= 2:
                # snapshots are sorted newest first, so [0] is new, [1] is previous
                new_id = snapshots[0].snapshot_id
                old_id = snapshots[1].snapshot_id
                self.compare_bar.set_selection(old_id, new_id)
                self._compare_snapshots(old_id, new_id)
                self.status_bar.showMessage(
                    f"Snapshot taken: {meta.total_records:,} records - "
                    f"comparing with previous..."
                )
            else:
                self.status_bar.showMessage(
                    f"Snapshot taken: {meta.total_records:,} records "
                    f"from {meta.project_count} projects"
                )

        def on_error(msg: str) -> None:
            progress.close()
            self._active_worker = None
            QMessageBox.critical(self, "Snapshot Error", msg)

        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        self._active_worker = worker
        worker.start()

    def _delete_snapshot(self, snapshot_id: int) -> None:
        """Delete a snapshot."""
        try:
            self.snapshot_engine.delete_snapshot(snapshot_id)
            # Clear stale diff state that may reference the deleted snapshot
            self._current_diff = None
            self.diff_viewer.clear()
            self.record_detail.clear_detail()
            self.summary_label.setText("")
            self.project_tree.clear_change_indicators()
            self._refresh_snapshots()
            self.status_bar.showMessage("Snapshot deleted")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _refresh_snapshots(self) -> None:
        """Reload snapshot lists."""
        snapshots = self.snapshot_engine.list_snapshots()
        self._snapshots = snapshots
        self.snapshot_panel.set_snapshots(snapshots)
        self.compare_bar.set_snapshots(snapshots)

    def _compare_snapshots(self, old_id: int, new_id: int) -> None:
        """Compare two snapshots and show results.

        Always fetches all projects - filtering is applied client-side.
        """
        self.status_bar.showMessage("Comparing snapshots...")
        self.diff_viewer.clear()
        self.record_detail.clear_detail()
        self.summary_label.setText("")

        excluded = self.project_tree.get_excluded_projects()

        # Determine snapshots between old and new (oldest first) for snapshot labelling
        id_to_snap = {s.snapshot_id: s for s in self._snapshots}
        old_snap = id_to_snap.get(old_id)
        new_snap = id_to_snap.get(new_id)
        intermediates: Optional[list[SnapshotMeta]] = None
        if old_snap and new_snap:
            old_ts = min(old_snap.timestamp, new_snap.timestamp)
            new_ts = max(old_snap.timestamp, new_snap.timestamp)
            between = sorted(
                (s for s in self._snapshots if old_ts <= s.timestamp <= new_ts),
                key=lambda s: s.timestamp,
            )
            if len(between) >= 2:
                intermediates = between

        worker = DiffWorker(
            self.db.db_path,
            old_id,
            new_id,
            excluded_projects=excluded or None,
            intermediate_snapshots=intermediates,
            parent=self,
        )

        def on_finished(summary: DiffSummary) -> None:
            self._active_worker = None
            self._current_diff = summary
            self.diff_viewer.set_diff_summary(summary)
            self.diff_viewer.set_project_filter(self._project_filter)
            self._update_summary(summary)
            self.project_tree.update_change_indicators(summary)
            self.status_bar.showMessage(
                f"Diff complete: {summary.total_changes} changes"
            )

        def on_error(msg: str) -> None:
            self._active_worker = None
            QMessageBox.critical(self, "Diff Error", msg)

        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        self._active_worker = worker
        worker.start()

    def _update_summary(self, summary: DiffSummary) -> None:
        """Update the summary label with diff counts."""
        parts = []
        if summary.added_count:
            parts.append(
                f'<span style="color: #2e7d32; font-weight: bold;">'
                f"Added: {summary.added_count}</span>"
            )
        if summary.modified_count:
            parts.append(
                f'<span style="color: #f57f17; font-weight: bold;">'
                f"Modified: {summary.modified_count}</span>"
            )
        if summary.deleted_count:
            parts.append(
                f'<span style="color: #c62828; font-weight: bold;">'
                f"Deleted: {summary.deleted_count}</span>"
            )

        if parts:
            old_ts = summary.old_snapshot.timestamp.strftime("%Y-%m-%d %H:%M")
            new_ts = summary.new_snapshot.timestamp.strftime("%Y-%m-%d %H:%M")
            self.summary_label.setText(
                f"{old_ts} -> {new_ts}  |  " + "  |  ".join(parts)
            )
        else:
            self.summary_label.setText("No changes detected.")

    def _on_compare_requested(self, old_id: int, new_id: int) -> None:
        """Handle compare button click - compare keeping current filter."""
        self._compare_snapshots(old_id, new_id)

    def _on_project_filter_changed(self, projects) -> None:
        """Filter diffs to selected projects (None = show all)."""
        self._project_filter = projects
        self.diff_viewer.set_project_filter(self._project_filter)

    def _on_exclusions_changed(self, excluded: set[str]) -> None:
        """Save exclusion settings when checkboxes change."""
        settings.excluded_projects = list(excluded)

    def _on_hidden_changed(self, hidden: set[str]) -> None:
        """Save hidden projects when they change."""
        settings.hidden_projects = list(hidden)

    def _on_view_mode_changed(self, flat: bool) -> None:
        """Save view mode when it changes."""
        settings.project_flat_mode = flat

    def _on_diff_selection_changed(self) -> None:
        """Update record detail panel when selection changes."""
        diff = self.diff_viewer.get_selected_diff()
        if diff:
            self.record_detail.show_diff(diff)
        else:
            self.record_detail.clear_detail()

    def _on_diff_double_clicked(self, index) -> None:
        """Show full record detail dialog on double-click."""
        source_index = self.diff_viewer.proxy.mapToSource(index)
        diff = self.diff_viewer.model.get_diff(source_index.row())
        if diff:
            dialog = RecordDetailDialog(diff, self)
            dialog.exec()

    def _on_recover_requested(self, diffs: list[RecordDiff]) -> None:
        """Handle recovery request from diff viewer context menu."""
        if not self.source_dir:
            QMessageBox.warning(self, "No Source", "No source directory set.")
            return

        if not diffs:
            return

        # Build confirmation message
        lines = []
        for d in diffs[:10]:
            action = "Delete" if d.change_type.value == "added" else "Recover"
            lines.append(
                f"  {action}: {d.record_key} "
                f"({d.project_name}/{d.table_type.display_name})"
            )
        if len(diffs) > 10:
            lines.append(f"  ... and {len(diffs) - 10} more")

        reply = QMessageBox.warning(
            self,
            "Confirm Recovery",
            f"This will modify {len(diffs)} record(s) in the DBF files "
            f"on disk:\n\n" + "\n".join(lines) + "\n\n"
            "This cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,  # type: ignore[arg-type]
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        progress = QProgressDialog(
            "Recovering records...", "Cancel", 0, len(diffs), self
        )
        progress.setWindowTitle("Recovery Progress")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        worker = RecoverWorker(
            self.source_dir, diffs, parent=self
        )

        def on_progress(current: int, total: int, message: str) -> None:
            progress.setMaximum(total)
            progress.setValue(current)
            progress.setLabelText(message)

        def on_finished(successes: list[str], errors: list[str]) -> None:
            progress.close()
            self._active_worker = None

            msg_parts = []
            if successes:
                msg_parts.append(f"Successfully recovered {len(successes)} record(s).")
            if errors:
                msg_parts.append(
                    f"\n\n{len(errors)} error(s):\n" + "\n".join(errors[:10])
                )

            if errors:
                QMessageBox.warning(
                    self, "Recovery Complete", "\n".join(msg_parts)
                )
            else:
                QMessageBox.information(
                    self, "Recovery Complete", "\n".join(msg_parts)
                )

            self.status_bar.showMessage(
                f"Recovery: {len(successes)} succeeded, {len(errors)} failed"
            )

        def on_error(msg: str) -> None:
            progress.close()
            self._active_worker = None
            QMessageBox.critical(self, "Recovery Error", msg)

        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        self._active_worker = worker
        worker.start()

    def _rename_snapshot(self, snapshot_id: int, new_label: str) -> None:
        self.db.update_snapshot_label(snapshot_id, new_label)
        self._refresh_snapshots()

    def _edit_snapshot_notes(self, snapshot_id: int, new_notes: str) -> None:
        self.db.update_snapshot_notes(snapshot_id, new_notes)
        self._refresh_snapshots()

    def _export_diff_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Diff to CSV", "", "CSV Files (*.csv)"
        )
        if path:
            self.diff_viewer.export_to_csv(path)

    def _on_ctback_backup(self, project_name: str) -> None:
        """ctback32.exe /b detected. Take a partial snapshot of the project."""
        if not self.source_dir:
            return
        if not settings.auto_backup:
            return
        label = f"Auto(backup){f' ({project_name})' if project_name else ''}"
        include = {project_name} if project_name else None
        self.status_bar.showMessage(
            f"ctback32 backup detected{f' for {project_name}' if project_name else ''}. Taking snapshot..."
        )
        excluded = self.project_tree.get_excluded_projects()
        worker = SnapshotWorker(
            self.db.db_path,
            self.source_dir,
            label=label,
            excluded_projects=excluded or None,
            include_projects=include,
            taken_by=self._user_name,
            parent=self,
        )

        def on_finished(meta: SnapshotMeta) -> None:
            self._active_worker = None
            self._refresh_snapshots()
            self.status_bar.showMessage(
                f"Auto-snapshot taken: {meta.label} ({meta.total_records:,} records)"
            )

        def on_error(msg: str) -> None:
            self._active_worker = None
            self.status_bar.showMessage(f"Auto-snapshot failed: {msg}")

        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        self._active_worker = worker
        worker.start()

    def _on_ctback_restore(self, project_name: str) -> None:
        """ctback32.exe /r completed. Take a snapshot to record the restored state."""
        if not self.source_dir:
            return
        if not settings.auto_restore:
            return
        label = f"Auto(restore){f' ({project_name})' if project_name else ''}"
        include = {project_name} if project_name else None
        self.status_bar.showMessage(
            f"ctback32 restore complete{f' for {project_name}' if project_name else ''}. Taking snapshot..."
        )
        excluded = self.project_tree.get_excluded_projects()
        worker = SnapshotWorker(
            self.db.db_path,
            self.source_dir,
            label=label,
            excluded_projects=excluded or None,
            include_projects=include,
            taken_by=self._user_name,
            parent=self,
        )

        def on_finished(meta: SnapshotMeta) -> None:
            self._active_worker = None
            self._refresh_snapshots()
            self.status_bar.showMessage(
                f"Auto-snapshot taken: {meta.label} ({meta.total_records:,} records)"
            )

        def on_error(msg: str) -> None:
            self._active_worker = None
            self.status_bar.showMessage(f"Auto-snapshot failed: {msg}")

        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        self._active_worker = worker
        worker.start()

    def _open_options(self) -> None:
        dlg = OptionsDialog(self)
        if dlg.exec():
            if settings.auto_backup or settings.auto_restore:
                self._proc_watcher.start()
            else:
                self._proc_watcher.stop()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Citect Tracker",
            "Citect Tracker\n\n"
            "Track changes to Citect SCADA project DBF files over time.\n"
            "Take snapshots and compare projects like git diff.\n\n"
            "Created by MSkjel",
        )
