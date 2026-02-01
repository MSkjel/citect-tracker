"""Discover Citect projects from MASTER.DBF and resolve include hierarchies."""

from __future__ import annotations

from pathlib import Path

from .dbf_reader import read_include_dbf, read_master_dbf
from .models import ProjectInfo


def discover_projects(master_dir: Path) -> dict[str, ProjectInfo]:
    """Discover all projects from MASTER.DBF and resolve include hierarchies.

    Args:
        master_dir: Directory containing MASTER.DBF and project subfolders.

    Returns:
        Dict mapping project name -> ProjectInfo with resolved includes.
    """
    master_path = master_dir / "MASTER.DBF"
    raw_projects = read_master_dbf(master_path)

    projects: dict[str, ProjectInfo] = {}
    for row in raw_projects:
        name = row["NAME"]
        local_path = master_dir / name

        # Read direct includes if the folder exists locally
        includes: list[str] = []
        if local_path.is_dir():
            include_path = local_path / "include.DBF"
            includes = read_include_dbf(include_path)

        projects[name] = ProjectInfo(
            name=name,
            title=row.get("TITLE", ""),
            path=row.get("PATH", ""),
            local_path=str(local_path),
            includes=includes,
        )

    # Resolve recursive includes with cycle detection
    for project in projects.values():
        project.all_includes = _resolve_includes_recursive(
            project.name, projects, set()
        )

    return projects


def _resolve_includes_recursive(
    project_name: str,
    all_projects: dict[str, ProjectInfo],
    visited: set[str],
) -> list[str]:
    """Recursively resolve all includes for a project.

    Uses a visited set for cycle detection. Returns a flat, deduplicated
    list of all transitively included project names.
    """
    if project_name not in all_projects:
        return []
    if project_name in visited:
        return []

    visited.add(project_name)
    project = all_projects[project_name]
    result: list[str] = []

    for included_name in project.includes:
        if included_name not in visited:
            if included_name not in result:
                result.append(included_name)
            sub_includes = _resolve_includes_recursive(
                included_name, all_projects, visited.copy()
            )
            for sub in sub_includes:
                if sub not in result:
                    result.append(sub)

    return result


def get_projects_with_data(projects: dict[str, ProjectInfo]) -> list[ProjectInfo]:
    """Return only projects that have a local folder on disk."""
    return [p for p in projects.values() if Path(p.local_path).is_dir()]


def build_project_tree(
    projects: dict[str, ProjectInfo],
) -> list[tuple[ProjectInfo, list[str]]]:
    """Build a tree structure for GUI display.

    Returns list of (project, children_names) for top-level projects.
    A top-level project is one that is not included by any other project.
    """
    included_by_others: set[str] = set()
    for p in projects.values():
        for inc in p.includes:
            included_by_others.add(inc)

    top_level = []
    for p in projects.values():
        if p.name not in included_by_others:
            top_level.append((p, p.includes))

    # Sort alphabetically
    top_level.sort(key=lambda t: t[0].name)
    return top_level
