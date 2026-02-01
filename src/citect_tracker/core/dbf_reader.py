"""DBF file reading with field stripping and hash computation."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import xxhash

from .models import TableRecord, TableType


def _compute_hash(fields: dict[str, str]) -> bytes:
    """Compute xxh3_128 hash of canonical JSON representation."""
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=False)
    return xxhash.xxh3_128_digest(canonical.encode("utf-8"))


def read_table(file_path: Path, table_type: TableType) -> list[TableRecord]:
    """Read all active records from a DBF file using fast struct-based parsing.

    All Citect DBF fields are character type (C), so no complex type
    conversion is needed. This is significantly faster than the `dbf`
    library for large files (134MB+).
    """
    key_field = table_type.key_field
    records: list[TableRecord] = []

    with open(file_path, "rb") as f:
        header = f.read(32)
        if len(header) < 32:
            return records

        nrecs = struct.unpack("<I", header[4:8])[0]
        hlen, rlen = struct.unpack("<HH", header[8:12])

        # Parse field descriptors (each 32 bytes, terminated by 0x0D)
        f.seek(32)
        fields_def: list[tuple[str, int]] = []
        while f.tell() < hlen - 1:
            fdata = f.read(32)
            if len(fdata) < 32 or fdata[0] == 0x0D:
                break
            fname = fdata[:11].split(b"\x00")[0].decode("ascii")
            flen = fdata[16]
            fields_def.append((fname, flen))

        # Read all record data in one block for I/O efficiency
        f.seek(hlen)
        data = f.read(nrecs * rlen)

    for i in range(nrecs):
        offset = i * rlen
        if offset >= len(data):
            break
        # First byte is deletion flag: 0x2A ('*') = deleted
        if data[offset] == 0x2A:
            continue

        pos = offset + 1  # Skip deletion flag byte
        rec_fields: dict[str, str] = {}
        for fname, flen in fields_def:
            raw = data[pos : pos + flen]
            try:
                val = raw.decode("latin-1").rstrip()
            except (UnicodeDecodeError, ValueError):
                val = raw.decode("ascii", errors="replace").rstrip()
            if val:
                rec_fields[fname] = val
            pos += flen

        key = rec_fields.get(key_field, "")
        if not key:
            continue

        record_hash = _compute_hash(rec_fields)
        records.append(TableRecord(key=key, fields=rec_fields, record_hash=record_hash))

    return records


def read_master_dbf(file_path: Path) -> list[dict[str, str]]:
    """Read MASTER.DBF and return list of project dicts."""
    projects: list[dict[str, str]] = []

    with open(file_path, "rb") as f:
        header = f.read(32)
        if len(header) < 32:
            return projects

        nrecs = struct.unpack("<I", header[4:8])[0]
        hlen, rlen = struct.unpack("<HH", header[8:12])

        f.seek(32)
        fields_def: list[tuple[str, int]] = []
        while f.tell() < hlen - 1:
            fdata = f.read(32)
            if len(fdata) < 32 or fdata[0] == 0x0D:
                break
            fname = fdata[:11].split(b"\x00")[0].decode("ascii")
            flen = fdata[16]
            fields_def.append((fname, flen))

        f.seek(hlen)
        data = f.read(nrecs * rlen)

    for i in range(nrecs):
        offset = i * rlen
        if offset >= len(data):
            break
        if data[offset] == 0x2A:
            continue

        pos = offset + 1
        row: dict[str, str] = {}
        for fname, flen in fields_def:
            raw = data[pos : pos + flen]
            try:
                val = raw.decode("latin-1").rstrip()
            except (UnicodeDecodeError, ValueError):
                val = raw.decode("ascii", errors="replace").rstrip()
            if val:
                row[fname] = val
            pos += flen

        if row.get("NAME"):
            projects.append(row)

    return projects


def read_include_dbf(file_path: Path) -> list[str]:
    """Read include.DBF and return list of included project names."""
    if not file_path.exists():
        return []

    includes: list[str] = []

    with open(file_path, "rb") as f:
        header = f.read(32)
        if len(header) < 32:
            return includes

        nrecs = struct.unpack("<I", header[4:8])[0]
        hlen, rlen = struct.unpack("<HH", header[8:12])

        f.seek(32)
        fields_def: list[tuple[str, int]] = []
        while f.tell() < hlen - 1:
            fdata = f.read(32)
            if len(fdata) < 32 or fdata[0] == 0x0D:
                break
            fname = fdata[:11].split(b"\x00")[0].decode("ascii")
            flen = fdata[16]
            fields_def.append((fname, flen))

        f.seek(hlen)
        data = f.read(nrecs * rlen)

    # Find the NAME field position
    name_offset = 1  # skip deletion flag
    name_len = 0
    for fname, flen in fields_def:
        if fname == "NAME":
            name_len = flen
            break
        name_offset += flen

    if name_len == 0:
        return includes

    for i in range(nrecs):
        offset = i * rlen
        if offset >= len(data):
            break
        if data[offset] == 0x2A:
            continue

        raw = data[offset + name_offset : offset + name_offset + name_len]
        try:
            name = raw.decode("latin-1").rstrip()
        except (UnicodeDecodeError, ValueError):
            name = raw.decode("ascii", errors="replace").rstrip()
        if name:
            includes.append(name)

    return includes
