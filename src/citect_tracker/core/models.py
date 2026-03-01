"""Data models for Citect SCADA DBF change tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TableType(Enum):
    """Data table types tracked from each project."""

    VARIABLE = "variable"
    EQUIP = "equip"
    ADVALM = "advalm"
    DIGALM = "digalm"
    TREND = "trend"
    BOARDS = "boards"
    PORTS = "ports"
    EQSTATE = "eqstate"
    EVENTS = "events"
    UNITS = "units"
    USERS = "users"

    @property
    def key_field(self) -> str:
        """Primary key field name for this table type."""
        if self in (TableType.ADVALM, TableType.DIGALM):
            return "TAG"
        return "NAME"

    @property
    def filename(self) -> str:
        """DBF filename for this table type."""
        return f"{self.value}.DBF"

    @property
    def display_name(self) -> str:
        return self.value.capitalize()


class ChangeType(Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class ProjectInfo:
    """A project as discovered from MASTER.DBF."""

    name: str
    title: str
    path: str  # Original Windows path (for reference)
    local_path: str  # Resolved local directory path
    includes: list[str] = field(default_factory=list)
    all_includes: list[str] = field(default_factory=list)


@dataclass
class TableRecord:
    """A single record from a data table, with computed hash."""

    key: str
    fields: dict[str, str]
    record_hash: bytes


@dataclass
class SnapshotMeta:
    """Metadata about a stored snapshot."""

    snapshot_id: int
    timestamp: datetime
    label: str
    source_dir: str
    project_count: int
    total_records: int
    notes: str = ""
    taken_by: str = ""


@dataclass
class RecordDiff:
    """A single record change between two snapshots."""

    change_type: ChangeType
    project_name: str
    table_type: TableType
    record_key: str
    old_fields: Optional[dict[str, str]]
    new_fields: Optional[dict[str, str]]
    changed_fields: list[str] = field(default_factory=list)


@dataclass
class DiffSummary:
    """Summary of differences between two snapshots."""

    old_snapshot: SnapshotMeta
    new_snapshot: SnapshotMeta
    added_count: int
    modified_count: int
    deleted_count: int
    # {project_name: {table_type_value: [RecordDiff, ...]}}
    changes_by_project: dict[str, dict[str, list[RecordDiff]]] = field(
        default_factory=dict
    )

    @property
    def total_changes(self) -> int:
        return self.added_count + self.modified_count + self.deleted_count

    def all_changes(self) -> list[RecordDiff]:
        """Flat list of all changes."""
        result = []
        for tables in self.changes_by_project.values():
            for diffs in tables.values():
                result.extend(diffs)
        return result
