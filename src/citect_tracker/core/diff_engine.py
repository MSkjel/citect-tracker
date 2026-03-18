"""Compare two snapshots and produce detailed diffs."""

from __future__ import annotations

from typing import Optional

from ..storage.database import Database
from .models import ChangeType, DiffSummary, RecordDiff, TableType


class DiffEngine:
    """Compares two snapshots and produces structured diff results."""

    def __init__(self, db: Database):
        self.db = db

    def compare_snapshots(
        self,
        old_id: int,
        new_id: int,
        project_filter: Optional[set[str]] = None,
        table_filter: Optional[TableType] = None,
        excluded_projects: Optional[set[str]] = None,
        intermediate_snapshots: Optional[list] = None,
    ) -> DiffSummary:
        """Compare two snapshots and return all differences.

        Uses hash-based comparison via SQL JOINs for speed.
        For modified records, computes field-level diffs.

        Args:
            project_filter: Set of project names to include (None = all).
            excluded_projects: Set of project names to exclude from diff.
        """
        old_meta = self.db.get_snapshot_meta(old_id)
        new_meta = self.db.get_snapshot_meta(new_id)

        # Restrict to projects present in both snapshots to avoid noise from
        # partial snapshots (e.g. a ctback32 auto-snapshot of one project
        # compared against a full snapshot would otherwise show every record
        # in the other projects as "Added").
        old_projects = {p["name"] for p in self.db.get_snapshot_projects(old_id)}
        new_projects = {p["name"] for p in self.db.get_snapshot_projects(new_id)}
        if old_projects and new_projects:
            intersection = old_projects & new_projects
            if project_filter:
                effective_filter: Optional[set[str]] = project_filter & intersection
            else:
                effective_filter = intersection
        else:
            effective_filter = project_filter

        raw_changes = self.db.find_changes(
            old_id, new_id, effective_filter, table_filter
        )

        # Filter out excluded projects
        if excluded_projects:
            raw_changes = [
                c for c in raw_changes if c["project_name"] not in excluded_projects
            ]

        # Batch-load all record fields upfront to avoid N+1 queries
        old_hashes = {bytes(c["old_hash"]) for c in raw_changes if c["old_hash"] is not None}
        new_hashes = {bytes(c["new_hash"]) for c in raw_changes if c["new_hash"] is not None}
        fields_cache = self.db.get_record_fields_batch(old_hashes | new_hashes)

        changes_by_project: dict[str, dict[str, list[RecordDiff]]] = {}
        added = modified = deleted = 0

        for change in raw_changes:
            project = change["project_name"]
            table = change["table_type"]

            if project not in changes_by_project:
                changes_by_project[project] = {}
            if table not in changes_by_project[project]:
                changes_by_project[project][table] = []

            change_type_str = change["change_type"]

            if change_type_str == "modified":
                old_fields = fields_cache.get(bytes(change["old_hash"]), {})
                new_fields = fields_cache.get(bytes(change["new_hash"]), {})
                changed = _compute_changed_fields(old_fields, new_fields)

                diff = RecordDiff(
                    change_type=ChangeType.MODIFIED,
                    project_name=project,
                    table_type=TableType(table),
                    record_key=change["record_key"],
                    old_fields=old_fields,
                    new_fields=new_fields,
                    changed_fields=changed,
                )
                modified += 1

            elif change_type_str == "added":
                new_fields = fields_cache.get(bytes(change["new_hash"]), {})
                diff = RecordDiff(
                    change_type=ChangeType.ADDED,
                    project_name=project,
                    table_type=TableType(table),
                    record_key=change["record_key"],
                    old_fields=None,
                    new_fields=new_fields,
                    changed_fields=[],
                )
                added += 1

            else:  # deleted
                old_fields = fields_cache.get(bytes(change["old_hash"]), {})
                diff = RecordDiff(
                    change_type=ChangeType.DELETED,
                    project_name=project,
                    table_type=TableType(table),
                    record_key=change["record_key"],
                    old_fields=old_fields,
                    new_fields=None,
                    changed_fields=[],
                )
                deleted += 1

            changes_by_project[project][table].append(diff)

        # Rename detection: match Deleted+Added pairs with identical non-key fields.
        # Each matched pair becomes a single Modified diff (key field changed).
        renames = _detect_renames(changes_by_project)
        added -= renames
        deleted -= renames
        modified += renames

        # Tag each diff with the snapshot where that change last appeared.
        # If intermediate snapshots are provided, do sequential pairwise find_changes
        # (hash-only, no field fetches) to build a key→label map.
        all_diffs = [
            d
            for tables in changes_by_project.values()
            for diffs in tables.values()
            for d in diffs
        ]
        def _fmt(snap) -> str:  # type: ignore[no-untyped-def]
            return f"{snap.timestamp.strftime('%Y-%m-%d %H:%M')} | {snap.label}"

        if intermediate_snapshots and len(intermediate_snapshots) >= 2:
            key_to_label: dict[tuple[str, str, str], str] = {}
            for i in range(len(intermediate_snapshots) - 1):
                pair_old_id = intermediate_snapshots[i].snapshot_id
                pair_new_id = intermediate_snapshots[i + 1].snapshot_id
                pair_label = _fmt(intermediate_snapshots[i + 1])
                pair_changes = self.db.find_changes(
                    pair_old_id, pair_new_id, effective_filter, table_filter
                )
                for c in pair_changes:
                    if excluded_projects and c["project_name"] in excluded_projects:
                        continue
                    key_to_label[(c["project_name"], c["table_type"], c["record_key"])] = pair_label
            for diff in all_diffs:
                diff.snapshot_label = key_to_label.get(
                    (diff.project_name, diff.table_type.value, diff.record_key),
                    _fmt(new_meta),
                )
        else:
            for diff in all_diffs:
                diff.snapshot_label = _fmt(new_meta)

        return DiffSummary(
            old_snapshot=old_meta,
            new_snapshot=new_meta,
            added_count=added,
            modified_count=modified,
            deleted_count=deleted,
            changes_by_project=changes_by_project,
        )


def _detect_renames(
    changes_by_project: dict[str, dict[str, list[RecordDiff]]],
) -> int:
    """Match Deleted+Added pairs with identical non-key fields within each project/table.

    Converts matched pairs into a single Renamed diff with ``renamed_from`` set
    to the old key. Returns the number of renames detected.
    """
    renames = 0
    for tables in changes_by_project.values():
        for diffs in tables.values():
            if not diffs:
                continue
            key_field = diffs[0].table_type.key_field
            ignore = {key_field}

            deleted = [d for d in diffs if d.change_type == ChangeType.DELETED and d.old_fields]
            added = [d for d in diffs if d.change_type == ChangeType.ADDED and d.new_fields]
            if not deleted or not added:
                continue

            def _content(fields: dict[str, str]) -> tuple[tuple[str, str], ...]:
                return tuple(sorted((k, v) for k, v in fields.items() if k not in ignore))

            # Index deleted records by their non-key content (first match wins)
            deleted_by_content: dict[tuple[tuple[str, str], ...], RecordDiff] = {}
            for d in deleted:
                assert d.old_fields is not None
                key = _content(d.old_fields)
                if key not in deleted_by_content:
                    deleted_by_content[key] = d

            to_remove: list[RecordDiff] = []
            for a in added:
                assert a.new_fields is not None
                key = _content(a.new_fields)
                if key in deleted_by_content:
                    d = deleted_by_content.pop(key)
                    # Merge: convert the Added diff into a Modified diff (key field changed)
                    a.change_type = ChangeType.MODIFIED
                    a.old_fields = d.old_fields
                    a.changed_fields = [key_field]
                    to_remove.append(d)
                    renames += 1

            for d in to_remove:
                diffs.remove(d)

    return renames


def _compute_changed_fields(
    old_fields: dict[str, str],
    new_fields: dict[str, str],
) -> list[str]:
    """Identify which fields differ between two record versions."""
    all_keys = set(old_fields.keys()) | set(new_fields.keys())
    changed = []
    for key in sorted(all_keys):
        old_val = old_fields.get(key, "")
        new_val = new_fields.get(key, "")
        if old_val != new_val:
            changed.append(key)
    return changed
