"""Orchestrate snapshot creation from DBF files into SQLite storage."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..storage.database import Database
from .dbf_reader import read_table
from .models import ProjectInfo, SnapshotMeta, TableRecord, TableType
from .project_discovery import discover_projects, get_projects_with_data

ProgressCallback = Optional[Callable[[int, int, str], None]]


def _read_table_task(
    project: ProjectInfo, table_type: TableType
) -> tuple[str, TableType, list[TableRecord]]:
    """Read a single table file. Used for parallel execution."""
    dbf_path = Path(project.local_path) / table_type.filename
    if not dbf_path.exists():
        return (project.name, table_type, [])
    records = read_table(dbf_path, table_type)
    return (project.name, table_type, records)


class SnapshotEngine:
    """Takes snapshots of Citect project data and manages snapshot lifecycle."""

    def __init__(self, db: Database):
        self.db = db

    def take_snapshot(
        self,
        source_dir: Path,
        label: str = "",
        progress_callback: ProgressCallback = None,
        excluded_projects: set[str] | None = None,
    ) -> SnapshotMeta:
        """Take a complete snapshot of all projects and their data tables.

        Reads MASTER.DBF, discovers projects, reads all data tables,
        and stores everything in SQLite with content-addressable dedup.

        Args:
            excluded_projects: Set of project names to skip during snapshot.
        """
        projects = discover_projects(source_dir)
        projects_with_data = get_projects_with_data(projects)

        # Filter out excluded projects
        if excluded_projects:
            projects_with_data = [
                p for p in projects_with_data if p.name not in excluded_projects
            ]

        if not label:
            label = f"Snapshot {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        table_types = list(TableType)
        total_steps = len(projects_with_data) * len(table_types)
        current_step = 0
        total_records = 0

        # Parallel read of all DBF files
        max_workers = min(8, (os.cpu_count() or 1) + 4)
        read_tasks = [
            (project, table_type)
            for project in projects_with_data
            for table_type in table_types
        ]

        with self.db.transaction():
            snapshot_id = self.db.create_snapshot(
                timestamp=datetime.now(),
                label=label,
                source_dir=str(source_dir),
                project_count=len(projects),
            )

            # Store project metadata
            for project in projects.values():
                self.db.store_project_info(snapshot_id, project)

            # Read files in parallel, write to DB as results complete
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_read_table_task, proj, tt): (proj, tt)
                    for proj, tt in read_tasks
                }

                for future in as_completed(futures):
                    current_step += 1
                    project_name, table_type, records = future.result()

                    if progress_callback:
                        progress_callback(
                            current_step,
                            total_steps,
                            f"Storing {project_name}/{table_type.filename}",
                        )

                    if records:
                        total_records += len(records)
                        self.db.store_records(
                            snapshot_id, project_name, table_type, records
                        )

            self.db.update_snapshot_total(snapshot_id, total_records)

        return self.db.get_snapshot_meta(snapshot_id)

    def list_snapshots(self) -> list[SnapshotMeta]:
        """Return all snapshots, newest first."""
        return self.db.list_snapshots()

    def delete_snapshot(self, snapshot_id: int) -> None:
        """Delete a snapshot and clean up orphaned record data."""
        self.db.delete_snapshot(snapshot_id)
