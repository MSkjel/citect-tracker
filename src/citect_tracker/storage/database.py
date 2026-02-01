"""SQLite database for snapshot storage with content-addressable deduplication."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from ..core.models import (
    ChangeType,
    ProjectInfo,
    SnapshotMeta,
    TableRecord,
    TableType,
)

SCHEMA_VERSION = 2

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    label           TEXT NOT NULL,
    source_dir      TEXT NOT NULL,
    project_count   INTEGER NOT NULL DEFAULT 0,
    total_records   INTEGER NOT NULL DEFAULT 0,
    notes           TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS snapshot_projects (
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    project_name    TEXT NOT NULL,
    title           TEXT DEFAULT '',
    includes_json   TEXT DEFAULT '[]',
    PRIMARY KEY (snapshot_id, project_name)
);

CREATE TABLE IF NOT EXISTS record_data (
    hash            BLOB PRIMARY KEY,
    fields_json     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_records (
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    project_name    TEXT NOT NULL,
    table_type      TEXT NOT NULL,
    record_key      TEXT NOT NULL,
    record_hash     BLOB NOT NULL,
    PRIMARY KEY (snapshot_id, project_name, table_type, record_key)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_records_hash
    ON snapshot_records(snapshot_id, record_hash);
CREATE INDEX IF NOT EXISTS idx_snapshot_records_project
    ON snapshot_records(snapshot_id, project_name, table_type);
"""


class Database:
    """SQLite database for storing and querying snapshots."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        """Context manager for explicit transaction control."""
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        # Check/set schema version
        cur = self.conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
        self.conn.commit()

    # -- Snapshot CRUD --

    def create_snapshot(
        self,
        timestamp: datetime,
        label: str,
        source_dir: str,
        project_count: int,
    ) -> int:
        """Create a new snapshot and return its ID."""
        cur = self.conn.execute(
            "INSERT INTO snapshots (timestamp, label, source_dir, project_count) "
            "VALUES (?, ?, ?, ?)",
            (timestamp.isoformat(), label, source_dir, project_count),
        )
        return cur.lastrowid  # type: ignore[return-value]

    def update_snapshot_total(self, snapshot_id: int, total_records: int) -> None:
        self.conn.execute(
            "UPDATE snapshots SET total_records = ? WHERE id = ?",
            (total_records, snapshot_id),
        )

    def get_snapshot_meta(self, snapshot_id: int) -> SnapshotMeta:
        cur = self.conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"Snapshot {snapshot_id} not found")
        return SnapshotMeta(
            snapshot_id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            label=row["label"],
            source_dir=row["source_dir"],
            project_count=row["project_count"],
            total_records=row["total_records"],
            notes=row["notes"] or "",
        )

    def list_snapshots(self) -> list[SnapshotMeta]:
        """Return all snapshots, newest first."""
        cur = self.conn.execute("SELECT * FROM snapshots ORDER BY timestamp DESC")
        return [
            SnapshotMeta(
                snapshot_id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                label=row["label"],
                source_dir=row["source_dir"],
                project_count=row["project_count"],
                total_records=row["total_records"],
                notes=row["notes"] or "",
            )
            for row in cur.fetchall()
        ]

    def delete_snapshot(self, snapshot_id: int, vacuum: bool = True) -> None:
        """Delete a snapshot and optionally compact the database."""
        self.conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        self.conn.commit()
        self.cleanup_orphaned_records()
        if vacuum:
            self.conn.execute("VACUUM")

    # -- Project info --

    def store_project_info(self, snapshot_id: int, project: ProjectInfo) -> None:
        self.conn.execute(
            "INSERT INTO snapshot_projects (snapshot_id, project_name, title, includes_json) "
            "VALUES (?, ?, ?, ?)",
            (
                snapshot_id,
                project.name,
                project.title,
                json.dumps(project.includes),
            ),
        )

    def get_snapshot_projects(self, snapshot_id: int) -> list[dict]:
        """Get project info for a snapshot."""
        cur = self.conn.execute(
            "SELECT * FROM snapshot_projects WHERE snapshot_id = ?", (snapshot_id,)
        )
        return [
            {
                "name": row["project_name"],
                "title": row["title"],
                "includes": json.loads(row["includes_json"]),
            }
            for row in cur.fetchall()
        ]

    # -- Record storage --

    def store_records(
        self,
        snapshot_id: int,
        project_name: str,
        table_type: TableType,
        records: list[TableRecord],
    ) -> None:
        """Store records with content-addressable deduplication."""
        # Batch insert record data (ignore duplicates)
        record_data_rows = [
            (r.record_hash, json.dumps(r.fields, ensure_ascii=False))
            for r in records
        ]
        self.conn.executemany(
            "INSERT OR IGNORE INTO record_data (hash, fields_json) VALUES (?, ?)",
            record_data_rows,
        )

        # Batch insert snapshot_records references
        ref_rows = [
            (snapshot_id, project_name, table_type.value, r.key, r.record_hash)
            for r in records
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO snapshot_records "
            "(snapshot_id, project_name, table_type, record_key, record_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            ref_rows,
        )

    def get_record_fields(self, record_hash: bytes) -> dict[str, str]:
        """Fetch the fields for a record by its content hash."""
        cur = self.conn.execute(
            "SELECT fields_json FROM record_data WHERE hash = ?", (record_hash,)
        )
        row = cur.fetchone()
        if row is None:
            return {}
        return json.loads(row["fields_json"])

    # -- Diff queries --

    def find_changes(
        self,
        old_id: int,
        new_id: int,
        project_filter: Optional[set[str]] = None,
        table_filter: Optional[TableType] = None,
    ) -> list[dict]:
        """Find all record changes between two snapshots using SQL JOINs.

        Args:
            project_filter: Set of project names to include (None = all projects).

        Returns list of dicts with keys:
            change_type, project_name, table_type, record_key, old_hash, new_hash
        """
        conditions = []
        params: dict = {"old_id": old_id, "new_id": new_id}

        if project_filter:
            # Build IN clause with named parameters
            placeholders = ", ".join(f":p{i}" for i in range(len(project_filter)))
            conditions.append(f"AND project_name IN ({placeholders})")
            for i, name in enumerate(project_filter):
                params[f"p{i}"] = name
        if table_filter:
            conditions.append("AND table_type = :table_filter")
            params["table_filter"] = table_filter.value

        extra = " ".join(conditions)

        query = f"""
        -- DELETED: in old but not in new
        SELECT 'deleted' as change_type,
               old_r.project_name as project_name,
               old_r.table_type as table_type,
               old_r.record_key as record_key,
               old_r.record_hash as old_hash, NULL as new_hash
        FROM snapshot_records old_r
        LEFT JOIN snapshot_records new_r
            ON old_r.project_name = new_r.project_name
            AND old_r.table_type = new_r.table_type
            AND old_r.record_key = new_r.record_key
            AND new_r.snapshot_id = :new_id
        WHERE old_r.snapshot_id = :old_id
            AND new_r.record_key IS NULL
            {extra.replace('project_name', 'old_r.project_name').replace('table_type', 'old_r.table_type') if extra else ''}

        UNION ALL

        -- ADDED: in new but not in old
        SELECT 'added' as change_type,
               new_r.project_name as project_name,
               new_r.table_type as table_type,
               new_r.record_key as record_key,
               NULL as old_hash, new_r.record_hash as new_hash
        FROM snapshot_records new_r
        LEFT JOIN snapshot_records old_r
            ON new_r.project_name = old_r.project_name
            AND new_r.table_type = old_r.table_type
            AND new_r.record_key = old_r.record_key
            AND old_r.snapshot_id = :old_id
        WHERE new_r.snapshot_id = :new_id
            AND old_r.record_key IS NULL
            {extra.replace('project_name', 'new_r.project_name').replace('table_type', 'new_r.table_type') if extra else ''}

        UNION ALL

        -- MODIFIED: in both but different hash
        SELECT 'modified' as change_type,
               old_r.project_name as project_name,
               old_r.table_type as table_type,
               old_r.record_key as record_key,
               old_r.record_hash as old_hash, new_r.record_hash as new_hash
        FROM snapshot_records old_r
        INNER JOIN snapshot_records new_r
            ON old_r.project_name = new_r.project_name
            AND old_r.table_type = new_r.table_type
            AND old_r.record_key = new_r.record_key
            AND new_r.snapshot_id = :new_id
        WHERE old_r.snapshot_id = :old_id
            AND old_r.record_hash != new_r.record_hash
            {extra.replace('project_name', 'old_r.project_name').replace('table_type', 'old_r.table_type') if extra else ''}

        ORDER BY 2, 3, 4
        """

        cur = self.conn.execute(query, params)
        return [
            {
                "change_type": row["change_type"],
                "project_name": row["project_name"],
                "table_type": row["table_type"],
                "record_key": row["record_key"],
                "old_hash": row["old_hash"],
                "new_hash": row["new_hash"],
            }
            for row in cur.fetchall()
        ]

    def cleanup_orphaned_records(self) -> None:
        """Remove record_data entries not referenced by any snapshot."""
        self.conn.execute(
            "DELETE FROM record_data WHERE hash NOT IN "
            "(SELECT DISTINCT record_hash FROM snapshot_records)"
        )
        self.conn.commit()
