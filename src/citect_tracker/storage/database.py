"""SQLite database for snapshot storage with content-addressable deduplication."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Collection, Generator, Optional

import xxhash

from ..core.models import (
    ProjectInfo,
    SnapshotMeta,
    TableRecord,
    TableType,
)

SCHEMA_VERSION = 6

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
    notes           TEXT DEFAULT '',
    taken_by        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS project_data (
    hash            BLOB PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',
    includes_json   TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS snapshot_projects (
    project_name        TEXT NOT NULL,
    data_hash           BLOB NOT NULL REFERENCES project_data(hash),
    first_snapshot_id   INTEGER NOT NULL,
    last_snapshot_id    INTEGER NOT NULL,
    PRIMARY KEY (project_name, first_snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_sp_active
    ON snapshot_projects(project_name, last_snapshot_id, first_snapshot_id);

CREATE TABLE IF NOT EXISTS record_data (
    hash            BLOB PRIMARY KEY,
    fields_json     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS record_versions (
    project_name      TEXT NOT NULL,
    table_type        TEXT NOT NULL,
    record_key        TEXT NOT NULL,
    record_hash       BLOB NOT NULL,
    first_snapshot_id INTEGER NOT NULL,
    last_snapshot_id  INTEGER NOT NULL,
    PRIMARY KEY (project_name, table_type, record_key, first_snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_rv_lookup
    ON record_versions(project_name, table_type, last_snapshot_id, first_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_rv_hash
    ON record_versions(record_hash);
CREATE INDEX IF NOT EXISTS idx_rv_active
    ON record_versions(project_name, table_type, record_key, last_snapshot_id);

CREATE TABLE IF NOT EXISTS record_latest (
    project_name      TEXT NOT NULL,
    table_type        TEXT NOT NULL,
    record_key        TEXT NOT NULL,
    record_hash       BLOB NOT NULL,
    first_snapshot_id INTEGER NOT NULL,
    PRIMARY KEY (project_name, table_type, record_key)
);
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
        self._conn.execute("PRAGMA cache_size = -32000")
        self._conn.execute("PRAGMA temp_store = MEMORY")
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
        # Check for incompatible existing schema before running SCHEMA_SQL,
        # which would fail on old tables with different columns.
        existing = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if existing:
            row = self.conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if row is not None and row["version"] != SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {row['version']} is incompatible with "
                    f"the required version {SCHEMA_VERSION}. "
                    "Please create a new database."
                )

        self.conn.executescript(SCHEMA_SQL)
        # Set version for fresh databases
        row = self.conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
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
        taken_by: str = "",
    ) -> int:
        """Create a new snapshot and return its ID."""
        cur = self.conn.execute(
            "INSERT INTO snapshots (timestamp, label, source_dir, project_count, taken_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (timestamp.isoformat(), label, source_dir, project_count, taken_by),
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
            taken_by=row["taken_by"] or "",
        )

    def update_snapshot_label(self, snapshot_id: int, label: str) -> None:
        self.conn.execute(
            "UPDATE snapshots SET label=? WHERE id=?", (label, snapshot_id)
        )

    def update_snapshot_notes(self, snapshot_id: int, notes: str) -> None:
        self.conn.execute(
            "UPDATE snapshots SET notes=? WHERE id=?", (notes, snapshot_id)
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
                taken_by=row["taken_by"] or "",
            )
            for row in cur.fetchall()
        ]

    def delete_snapshot(self, snapshot_id: int, vacuum: bool = True) -> None:
        """Delete a snapshot, adjusting version ranges accordingly."""
        # Find adjacent snapshot IDs
        prev_row = self.conn.execute(
            "SELECT id FROM snapshots WHERE id < ? ORDER BY id DESC LIMIT 1",
            (snapshot_id,),
        ).fetchone()
        next_row = self.conn.execute(
            "SELECT id FROM snapshots WHERE id > ? ORDER BY id ASC LIMIT 1",
            (snapshot_id,),
        ).fetchone()
        prev_id = prev_row["id"] if prev_row else None
        next_id = next_row["id"] if next_row else None

        # Single-snapshot versions — delete entirely
        self.conn.execute(
            "DELETE FROM record_versions "
            "WHERE first_snapshot_id = ? AND last_snapshot_id = ?",
            (snapshot_id, snapshot_id),
        )

        # Versions starting at this snapshot
        if next_id is not None:
            # Advance first_snapshot_id to next snapshot
            self.conn.execute(
                "UPDATE record_versions SET first_snapshot_id = ? "
                "WHERE first_snapshot_id = ?",
                (next_id, snapshot_id),
            )
        else:
            # No next snapshot — remove any remaining versions starting here
            self.conn.execute(
                "DELETE FROM record_versions WHERE first_snapshot_id = ?",
                (snapshot_id,),
            )

        # Versions ending at this snapshot
        if prev_id is not None:
            # Retreat last_snapshot_id to previous snapshot
            self.conn.execute(
                "UPDATE record_versions SET last_snapshot_id = ? "
                "WHERE last_snapshot_id = ?",
                (prev_id, snapshot_id),
            )
        else:
            # No previous snapshot — remove any remaining versions ending here
            self.conn.execute(
                "DELETE FROM record_versions WHERE last_snapshot_id = ?",
                (snapshot_id,),
            )

        # Adjust project version ranges for this snapshot
        # Single-snapshot project versions — delete entirely
        self.conn.execute(
            "DELETE FROM snapshot_projects "
            "WHERE first_snapshot_id = ? AND last_snapshot_id = ?",
            (snapshot_id, snapshot_id),
        )
        # Versions starting at this snapshot
        if next_id is not None:
            self.conn.execute(
                "UPDATE snapshot_projects SET first_snapshot_id = ? "
                "WHERE first_snapshot_id = ?",
                (next_id, snapshot_id),
            )
        else:
            self.conn.execute(
                "DELETE FROM snapshot_projects WHERE first_snapshot_id = ?",
                (snapshot_id,),
            )
        # Versions ending at this snapshot
        if prev_id is not None:
            self.conn.execute(
                "UPDATE snapshot_projects SET last_snapshot_id = ? "
                "WHERE last_snapshot_id = ?",
                (prev_id, snapshot_id),
            )
        else:
            self.conn.execute(
                "DELETE FROM snapshot_projects WHERE last_snapshot_id = ?",
                (snapshot_id,),
            )

        # Delete snapshot row
        self.conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))

        # Rebuild record_latest from record_versions
        self.conn.execute("DELETE FROM record_latest")
        self.conn.execute(
            "INSERT INTO record_latest "
            "(project_name, table_type, record_key, record_hash, first_snapshot_id) "
            "SELECT rv.project_name, rv.table_type, rv.record_key, "
            "rv.record_hash, rv.first_snapshot_id "
            "FROM record_versions rv "
            "INNER JOIN ("
            "  SELECT project_name, table_type, record_key, "
            "  MAX(last_snapshot_id) AS max_last "
            "  FROM record_versions "
            "  GROUP BY project_name, table_type, record_key"
            ") latest ON rv.project_name = latest.project_name "
            "  AND rv.table_type = latest.table_type "
            "  AND rv.record_key = latest.record_key "
            "  AND rv.last_snapshot_id = latest.max_last"
        )

        self.conn.commit()

        self.cleanup_orphaned_records()
        if vacuum:
            self.conn.execute("VACUUM")

    # -- Project info --

    def store_project_info(self, snapshot_id: int, project: ProjectInfo) -> None:
        """Store project info with content-addressable dedup and version ranges."""
        includes_json = json.dumps(project.includes)
        h = xxhash.xxh3_128_digest(
            f"{project.title}\x00{includes_json}".encode()
        )

        # Find the latest version for this project (handles partial snapshot gaps)
        prev_ver = self.conn.execute(
            "SELECT data_hash, first_snapshot_id FROM snapshot_projects "
            "WHERE project_name = ? ORDER BY last_snapshot_id DESC LIMIT 1",
            (project.name,),
        ).fetchone()

        if prev_ver is not None and bytes(prev_ver["data_hash"]) == h:
            # Same data - extend range
            self.conn.execute(
                "UPDATE snapshot_projects SET last_snapshot_id = ? "
                "WHERE project_name = ? AND first_snapshot_id = ?",
                (snapshot_id, project.name, prev_ver["first_snapshot_id"]),
            )
            return

        # New or changed — insert data + new version row
        self.conn.execute(
            "INSERT OR IGNORE INTO project_data (hash, title, includes_json) VALUES (?, ?, ?)",
            (h, project.title, includes_json),
        )
        self.conn.execute(
            "INSERT INTO snapshot_projects "
            "(project_name, data_hash, first_snapshot_id, last_snapshot_id) "
            "VALUES (?, ?, ?, ?)",
            (project.name, h, snapshot_id, snapshot_id),
        )

    def get_snapshot_projects(self, snapshot_id: int) -> list[dict]:
        """Get project info for a snapshot."""
        cur = self.conn.execute(
            "SELECT sp.project_name, pd.title, pd.includes_json "
            "FROM snapshot_projects sp "
            "JOIN project_data pd ON sp.data_hash = pd.hash "
            "WHERE sp.first_snapshot_id <= ? AND sp.last_snapshot_id >= ?",
            (snapshot_id, snapshot_id),
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
        """Store records with content-addressable dedup and version ranges."""
        if not records:
            return

        records_by_key = {r.key: r for r in records}

        # Fetch the latest version of each record from record_latest (no GROUP BY).
        cur = self.conn.execute(
            "SELECT record_key, record_hash, first_snapshot_id "
            "FROM record_latest "
            "WHERE project_name = ? AND table_type = ?",
            (project_name, table_type.value),
        )
        prev_versions: dict[str, tuple[bytes, int]] = {
            row["record_key"]: (bytes(row["record_hash"]), row["first_snapshot_id"])
            for row in cur.fetchall()
        }

        if prev_versions:
            new_records: list[TableRecord] = []
            extend_keys: list[str] = []

            for key, rec in records_by_key.items():
                prev = prev_versions.get(key)
                if prev is not None and prev[0] == rec.record_hash:
                    extend_keys.append(key)
                else:
                    new_records.append(rec)

            # Detect keys that disappeared (deleted/renamed away from DBF)
            disappeared = set(prev_versions) - set(records_by_key)

            # Remove stale entries from record_latest
            if disappeared:
                for i in range(0, len(disappeared), 900):
                    chunk = list(disappeared)[i : i + 900]
                    placeholders = ",".join("?" * len(chunk))
                    self.conn.execute(
                        f"DELETE FROM record_latest "
                        f"WHERE project_name = ? AND table_type = ? "
                        f"AND record_key IN ({placeholders})",
                        [project_name, table_type.value] + chunk,
                    )

            # Bulk extend version ranges for unchanged records
            if extend_keys:
                if not new_records and not disappeared:
                    # Fast path: ALL records unchanged, nothing gone
                    self.conn.execute(
                        "UPDATE record_versions SET last_snapshot_id = ? "
                        "WHERE project_name = ? AND table_type = ? "
                        "AND (record_key, first_snapshot_id) IN ("
                        "  SELECT record_key, first_snapshot_id FROM record_latest "
                        "  WHERE project_name = ? AND table_type = ?"
                        ")",
                        (snapshot_id, project_name, table_type.value,
                         project_name, table_type.value),
                    )
                else:
                    # Exclude changed/new keys AND disappeared keys from bulk update
                    exclude_keys = [r.key for r in new_records] + list(disappeared)
                    if len(exclude_keys) < 900:
                        placeholders = ",".join("?" * len(exclude_keys))
                        self.conn.execute(
                            f"UPDATE record_versions SET last_snapshot_id = ? "
                            f"WHERE project_name = ? AND table_type = ? "
                            f"AND (record_key, first_snapshot_id) IN ("
                            f"  SELECT record_key, first_snapshot_id FROM record_latest "
                            f"  WHERE project_name = ? AND table_type = ?"
                            f"  AND record_key NOT IN ({placeholders})"
                            f")",
                            [snapshot_id, project_name, table_type.value,
                             project_name, table_type.value] + exclude_keys,
                        )
                    else:
                        # Too many excluded keys, fall back to executemany
                        extend_set = set(extend_keys)
                        extend_rows = [
                            (snapshot_id, project_name, table_type.value, key, prev_versions[key][1])
                            for key in extend_set
                        ]
                        self.conn.executemany(
                            "UPDATE record_versions SET last_snapshot_id = ? "
                            "WHERE project_name = ? AND table_type = ? "
                            "AND record_key = ? AND first_snapshot_id = ?",
                            extend_rows,
                        )

            if new_records:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO record_data (hash, fields_json) VALUES (?, ?)",
                    [(r.record_hash, r.fields_json) for r in new_records],
                )
                self.conn.executemany(
                    "INSERT INTO record_versions "
                    "(project_name, table_type, record_key, record_hash, "
                    "first_snapshot_id, last_snapshot_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(project_name, table_type.value, r.key, r.record_hash, snapshot_id, snapshot_id)
                     for r in new_records],
                )
                # Update record_latest for changed/new records
                self.conn.executemany(
                    "INSERT OR REPLACE INTO record_latest "
                    "(project_name, table_type, record_key, record_hash, first_snapshot_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(project_name, table_type.value, r.key, r.record_hash, snapshot_id)
                     for r in new_records],
                )
        else:
            # First snapshot — all records are new
            unique_records = list(records_by_key.values())
            self.conn.executemany(
                "INSERT OR IGNORE INTO record_data (hash, fields_json) VALUES (?, ?)",
                [(r.record_hash, r.fields_json) for r in unique_records],
            )
            self.conn.executemany(
                "INSERT INTO record_versions "
                "(project_name, table_type, record_key, record_hash, "
                "first_snapshot_id, last_snapshot_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(project_name, table_type.value, r.key, r.record_hash, snapshot_id, snapshot_id)
                 for r in unique_records],
            )
            self.conn.executemany(
                "INSERT INTO record_latest "
                "(project_name, table_type, record_key, record_hash, first_snapshot_id) "
                "VALUES (?, ?, ?, ?, ?)",
                [(project_name, table_type.value, r.key, r.record_hash, snapshot_id)
                 for r in unique_records],
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

    def get_record_fields_batch(
        self, hashes: Collection[bytes]
    ) -> dict[bytes, dict[str, str]]:
        """Fetch fields for multiple hashes in one (or more) queries.

        Chunks into groups of 900 to respect SQLite's 999-parameter limit.
        """
        unique = list(set(hashes))
        result: dict[bytes, dict[str, str]] = {}
        for i in range(0, len(unique), 900):
            chunk = unique[i : i + 900]
            placeholders = ", ".join("?" * len(chunk))
            cur = self.conn.execute(
                f"SELECT hash, fields_json FROM record_data WHERE hash IN ({placeholders})",
                chunk,
            )
            for row in cur.fetchall():
                result[bytes(row["hash"])] = json.loads(row["fields_json"])
        return result

    # -- Diff queries --

    def find_changes(
        self,
        old_id: int,
        new_id: int,
        project_filter: Optional[set[str]] = None,
        table_filter: Optional[TableType] = None,
    ) -> list[dict]:
        """Find all record changes between two snapshots.

        Materializes each snapshot's state into temp tables, then joins them.
        This avoids repeated full-table scans on record_versions.

        Args:
            project_filter: Set of project names to include (None = all projects).

        Returns list of dicts with keys:
            change_type, project_name, table_type, record_key, old_hash, new_hash
        """
        # Build optional WHERE clauses for project/table filtering
        extra_where = ""
        filter_params: list = []
        if project_filter:
            placeholders = ", ".join("?" * len(project_filter))
            extra_where += f" AND project_name IN ({placeholders})"
            filter_params.extend(project_filter)
        if table_filter:
            extra_where += " AND table_type = ?"
            filter_params.append(table_filter.value)

        # Materialize old and new snapshot states into temp tables
        self.conn.execute("DROP TABLE IF EXISTS _diff_old")
        self.conn.execute("DROP TABLE IF EXISTS _diff_new")

        self.conn.execute(
            "CREATE TEMP TABLE _diff_old AS "
            "SELECT project_name, table_type, record_key, record_hash "
            "FROM record_versions "
            f"WHERE first_snapshot_id <= ? AND last_snapshot_id >= ?{extra_where}",
            [old_id, old_id] + filter_params,
        )
        self.conn.execute(
            "CREATE TEMP TABLE _diff_new AS "
            "SELECT project_name, table_type, record_key, record_hash, first_snapshot_id "
            "FROM record_versions "
            f"WHERE first_snapshot_id <= ? AND last_snapshot_id >= ?{extra_where}",
            [new_id, new_id] + filter_params,
        )

        self.conn.execute(
            "CREATE INDEX _idx_diff_old ON _diff_old(project_name, table_type, record_key)"
        )
        self.conn.execute(
            "CREATE INDEX _idx_diff_new ON _diff_new(project_name, table_type, record_key)"
        )

        query = """
        SELECT 'deleted' as change_type,
               o.project_name, o.table_type, o.record_key,
               o.record_hash as old_hash, NULL as new_hash,
               NULL as new_first_snap
        FROM _diff_old o
        LEFT JOIN _diff_new n
            ON o.project_name = n.project_name
            AND o.table_type = n.table_type
            AND o.record_key = n.record_key
        WHERE n.record_key IS NULL

        UNION ALL

        SELECT 'added',
               n.project_name, n.table_type, n.record_key,
               NULL, n.record_hash,
               n.first_snapshot_id
        FROM _diff_new n
        LEFT JOIN _diff_old o
            ON n.project_name = o.project_name
            AND n.table_type = o.table_type
            AND n.record_key = o.record_key
        WHERE o.record_key IS NULL

        UNION ALL

        SELECT 'modified',
               o.project_name, o.table_type, o.record_key,
               o.record_hash, n.record_hash,
               n.first_snapshot_id
        FROM _diff_old o
        INNER JOIN _diff_new n
            ON o.project_name = n.project_name
            AND o.table_type = n.table_type
            AND o.record_key = n.record_key
        WHERE o.record_hash != n.record_hash

        ORDER BY 2, 3, 4
        """

        cur = self.conn.execute(query)
        results = [
            {
                "change_type": row["change_type"],
                "project_name": row["project_name"],
                "table_type": row["table_type"],
                "record_key": row["record_key"],
                "old_hash": row["old_hash"],
                "new_hash": row["new_hash"],
                "new_first_snap": row["new_first_snap"],
            }
            for row in cur.fetchall()
        ]

        self.conn.execute("DROP TABLE IF EXISTS _diff_old")
        self.conn.execute("DROP TABLE IF EXISTS _diff_new")

        return results

    def cleanup_orphaned_records(self) -> None:
        """Remove record_data and project_data entries not referenced by any version."""
        self.conn.execute(
            "DELETE FROM record_data WHERE NOT EXISTS "
            "(SELECT 1 FROM record_versions WHERE record_versions.record_hash = record_data.hash)"
        )
        self.conn.execute(
            "DELETE FROM project_data WHERE NOT EXISTS "
            "(SELECT 1 FROM snapshot_projects WHERE snapshot_projects.data_hash = project_data.hash)"
        )
        self.conn.commit()
