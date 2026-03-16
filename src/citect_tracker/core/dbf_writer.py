"""DBF file writing for recovering records to old values.

Supports in-place modification of existing records and soft-deletion.
All Citect DBF fields are character type (C) with fixed widths.
"""

from __future__ import annotations

import struct
from pathlib import Path

from .models import ChangeType, RecordDiff


class RecoverError(Exception):
    """Raised when a record recovery operation fails."""


def _parse_header(data: bytes) -> tuple[int, int, int, list[tuple[str, int]]]:
    """Parse DBF header and return (nrecs, hlen, rlen, fields_def)."""
    if len(data) < 32:
        raise RecoverError("DBF file too small to contain a valid header")

    nrecs = struct.unpack("<I", data[4:8])[0]
    hlen, rlen = struct.unpack("<HH", data[8:12])
    fields_def: list[tuple[str, int]] = []

    pos = 32
    while pos < hlen - 1:
        if pos + 32 > len(data):
            break
        fdata = data[pos : pos + 32]
        if fdata[0] == 0x0D:
            break
        fname = fdata[:11].split(b"\x00")[0].decode("ascii")
        flen = fdata[16]
        fields_def.append((fname, flen))
        pos += 32

    return nrecs, hlen, rlen, fields_def


def _find_field_offsets(
    fields_def: list[tuple[str, int]],
) -> dict[str, tuple[int, int]]:
    """Build a map of field_name -> (offset_within_record, field_length).

    Offset is relative to the start of the record (after deletion flag byte).
    """
    offsets: dict[str, tuple[int, int]] = {}
    pos = 0
    for fname, flen in fields_def:
        offsets[fname] = (pos, flen)
        pos += flen
    return offsets


def recover_record(
    source_dir: Path,
    diff: RecordDiff,
) -> str:
    """Recover a single record to its old values by modifying the DBF file.

    For MODIFIED records: overwrites field values with old_fields.
    For ADDED records: sets the deletion flag to soft-delete the record.

    Returns a description of what was done.
    """
    dbf_path = source_dir / diff.project_name / diff.table_type.filename
    if not dbf_path.exists():
        raise RecoverError(f"DBF file not found: {dbf_path}")

    with open(dbf_path, "rb") as f:
        file_data = bytearray(f.read())

    nrecs, hlen, rlen, fields_def = _parse_header(bytes(file_data))
    field_offsets = _find_field_offsets(fields_def)
    key_field = diff.table_type.key_field

    if key_field not in field_offsets:
        raise RecoverError(
            f"Key field '{key_field}' not found in {dbf_path.name}"
        )

    key_offset, key_len = field_offsets[key_field]

    # Find the record by key
    target_row = _find_record(
        file_data, nrecs, hlen, rlen, key_offset, key_len, diff.record_key
    )

    if target_row is None:
        raise RecoverError(
            f"Record '{diff.record_key}' not found in {dbf_path.name}"
        )

    record_start = hlen + target_row * rlen

    if diff.change_type == ChangeType.ADDED:
        # Soft-delete: set deletion flag to 0x2A ('*')
        file_data[record_start] = 0x2A
        _write_file(dbf_path, file_data)
        return f"Deleted '{diff.record_key}' from {diff.project_name}/{diff.table_type.filename}"

    elif diff.change_type == ChangeType.MODIFIED:
        if not diff.old_fields:
            raise RecoverError(
                f"No old field data available for '{diff.record_key}'"
            )

        # Overwrite changed fields with old values
        changed_count = 0
        for field_name in diff.changed_fields:
            if field_name not in field_offsets:
                continue
            old_val = diff.old_fields.get(field_name, "")
            f_offset, f_len = field_offsets[field_name]
            # Encode and pad/truncate to field length
            encoded = old_val.encode("latin-1", errors="replace")
            padded = encoded[:f_len].ljust(f_len, b" ")
            # Write into file data (offset + 1 for deletion flag byte)
            write_pos = record_start + 1 + f_offset
            file_data[write_pos : write_pos + f_len] = padded
            changed_count += 1

        _write_file(dbf_path, file_data)
        return (
            f"Recovered {changed_count} field(s) for '{diff.record_key}' "
            f"in {diff.project_name}/{diff.table_type.filename}"
        )

    elif diff.change_type == ChangeType.DELETED:
        if not diff.old_fields:
            raise RecoverError(
                f"No old field data available for '{diff.record_key}'"
            )

        # Try to find a soft-deleted record with this key and undelete it
        deleted_row = _find_record(
            file_data, nrecs, hlen, rlen, key_offset, key_len,
            diff.record_key, include_deleted=True,
        )

        if deleted_row is not None and file_data[hlen + deleted_row * rlen] == 0x2A:
            # Undelete and overwrite all fields with old values
            rec_start = hlen + deleted_row * rlen
            file_data[rec_start] = 0x20  # Clear deletion flag
            _write_all_fields(
                file_data, rec_start, field_offsets, fields_def, diff.old_fields
            )
            _write_file(dbf_path, file_data)
            return (
                f"Undeleted '{diff.record_key}' in "
                f"{diff.project_name}/{diff.table_type.filename}"
            )

        # Record not found at all — append a new record
        new_record = bytearray(rlen)
        new_record[0] = 0x20  # Active record flag
        _write_all_fields(
            new_record, 0, field_offsets, fields_def, diff.old_fields
        )
        # Insert before EOF marker (0x1A) if present, otherwise append
        if file_data[-1] == 0x1A:
            file_data[-1:] = new_record + b"\x1a"
        else:
            file_data.extend(new_record)
        # Update record count in header
        new_nrecs = nrecs + 1
        struct.pack_into("<I", file_data, 4, new_nrecs)
        _write_file(dbf_path, file_data)
        return (
            f"Re-added '{diff.record_key}' to "
            f"{diff.project_name}/{diff.table_type.filename}"
        )

    else:
        raise RecoverError(
            f"Unsupported change type: {diff.change_type.value}"
        )


def _write_all_fields(
    data: bytearray,
    record_start: int,
    field_offsets: dict[str, tuple[int, int]],
    fields_def: list[tuple[str, int]],
    fields: dict[str, str],
) -> None:
    """Write all field values into a record buffer.

    Fills every field — fields not in the dict are written as spaces.
    """
    for fname, flen in fields_def:
        if fname not in field_offsets:
            continue
        f_offset, _ = field_offsets[fname]
        val = fields.get(fname, "")
        encoded = val.encode("latin-1", errors="replace")
        padded = encoded[:flen].ljust(flen, b" ")
        write_pos = record_start + 1 + f_offset
        data[write_pos : write_pos + flen] = padded


def _find_record(
    data: bytearray,
    nrecs: int,
    hlen: int,
    rlen: int,
    key_offset: int,
    key_len: int,
    target_key: str,
    include_deleted: bool = False,
) -> int | None:
    """Find a record by key value. Returns row index or None."""
    for i in range(nrecs):
        rec_start = hlen + i * rlen
        if rec_start >= len(data):
            break
        # Skip deleted records unless we're looking for them
        if data[rec_start] == 0x2A and not include_deleted:
            continue
        # +1 to skip deletion flag
        raw_key = data[rec_start + 1 + key_offset : rec_start + 1 + key_offset + key_len]
        try:
            key_val = raw_key.decode("latin-1").rstrip()
        except (UnicodeDecodeError, ValueError):
            key_val = raw_key.decode("ascii", errors="replace").rstrip()
        if key_val == target_key:
            return i

    return None


def _write_file(path: Path, data: bytearray) -> None:
    """Write modified data back to the DBF file."""
    with open(path, "wb") as f:
        f.write(data)
