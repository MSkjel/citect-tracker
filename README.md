# Citect Config Tracker

A desktop tool for tracking changes to Citect SCADA project configurations over time. It reads the DBF files that Citect uses to store its configuration, takes snapshots of them, and lets you compare any two snapshots to see what changed.

## What it does

Citect SCADA stores project configuration (variables, equipment, alarms, trends) in DBF database files. These are binary files, so you can't just diff them with normal tools.

This application lets you:

- **Take snapshots** of a Citect project's configuration at any point in time
- **Compare snapshots** to see every record that was added, modified, or deleted
- **Drill into changes** to see exactly which fields changed and their old/new values
- **Filter results** by project, table type, change type, or free-text search

It reads the following Citect table types: `VARIABLE`, `EQUIP`, `ADVALM`, `DIGALM`, and `TREND`.

## How it works

1. Point the application at a directory containing `MASTER.DBF` (the Citect project registry).
2. It discovers all projects and their include hierarchies from that file.
3. When you take a snapshot, it reads all DBF files for every project, hashes each record, and stores everything in a local SQLite database (`citect_tracker.db` in the source directory).
4. When you compare two snapshots, it joins the stored records by key and identifies additions, modifications, and deletions based on hash differences.
5. For modified records, it computes field-level diffs so you can see exactly what changed.

Records are stored with content-addressable deduplication, so identical records across snapshots only take up space once.

## Building

Requires Python 3.10+.

**Linux:**
```
./build.sh
```

**Windows:**
```
build.bat
```

Both scripts create a virtual environment, install dependencies, and package the application into a standalone executable using PyInstaller. The output goes to `dist/`.

## Running from source

```
pip install .
python -m citect_tracker
```

Or pass the Citect directory directly:

```
python -m citect_tracker /path/to/citect/config
```

## Dependencies

- **PyQt5** - GUI
- **dbf** - DBF file support (the app also includes a fast custom binary parser for performance)
- **PyInstaller** - build-time only, for creating standalone executables
