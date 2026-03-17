"""Process watcher for automatic ctback32.exe detection."""

from __future__ import annotations

import os
import re
from typing import Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal


def _parse_ctback_cmdline(cmdline: list[str]) -> tuple[str, str]:
    """Parse ctback32.exe arguments.

    Returns:
        (mode, project_name) where mode is 'backup', 'restore', or ''.
        project_name is the value of the /D argument, or '' if not found.
    """
    joined = " ".join(cmdline)
    lower = joined.lower()

    if " /b" in lower or lower.endswith("/b"):
        mode = "backup"
    elif " /r" in lower or lower.endswith("/r"):
        mode = "restore"
    else:
        mode = ""

    m = re.search(r'/[Dd]\s*"([^"]+)"', joined)
    if not m:
        m = re.search(r"/[Dd](\S+)", joined)
    project = m.group(1).strip('"\'') if m else ""

    return mode, project


def _scan_dbf_mtimes(source_dir: str) -> dict[str, float]:
    """Return {subdir_name: max_mtime_of_any_dbf_file} for all subdirs in source_dir."""
    result: dict[str, float] = {}
    try:
        for entry in os.scandir(source_dir):
            if not entry.is_dir():
                continue
            max_mtime = 0.0
            try:
                for f in os.scandir(entry.path):
                    if f.name.lower().endswith(".dbf") and f.is_file():
                        mtime = f.stat().st_mtime
                        if mtime > max_mtime:
                            max_mtime = mtime
            except OSError:
                pass
            if max_mtime > 0.0:
                result[entry.name] = max_mtime
    except OSError:
        pass
    return result


def _find_changed_project(source_dir: str, before: dict[str, float]) -> str:
    """Return the single project whose DBF mtimes changed since before-scan.

    Returns '' if zero or multiple projects changed (ambiguous).
    """
    after = _scan_dbf_mtimes(source_dir)
    changed = [name for name, mtime in after.items() if before.get(name, 0.0) < mtime]
    return changed[0] if len(changed) == 1 else ""


class ProcessWatcher(QObject):
    """Polls for ctback32.exe process starts using psutil.

    Emits backup_detected(project_name) immediately when /b mode is detected.
    Emits restore_completed(project_name) after a /r process exits and DBF
    changes are resolved.

    Falls back silently if psutil is not installed.
    """

    backup_detected = pyqtSignal(str)    # project_name from /D argument
    restore_completed = pyqtSignal(str)  # project_name resolved from DBF diff

    _TARGET = "ctback32.exe"
    _POLL_MS = 2000

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._known_pids: set[int] = set()
        # pid -> {"source_dir": str, "before": dict, "cmdline_project": str}
        self._restore_pids: dict[int, dict[str, object]] = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self.source_dir: str = ""

    def start(self) -> bool:
        """Start polling. Returns False if psutil is unavailable."""
        try:
            import psutil  # type: ignore[import]
            self._known_pids = {p.pid for p in psutil.process_iter(["pid"])}
            self._timer.start(self._POLL_MS)
            return True
        except ImportError:
            return False

    def stop(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        try:
            import psutil  # type: ignore[import]
        except ImportError:
            self._timer.stop()
            return

        try:
            current: dict[int, str] = {
                p.info["pid"]: (p.info["name"] or "").lower()
                for p in psutil.process_iter(["pid", "name"])
            }
            new_pids = set(current) - self._known_pids
            self._known_pids = set(current)

            # Detect new ctback32 processes
            for pid in new_pids:
                if self._TARGET.lower() not in current.get(pid, ""):
                    continue
                try:
                    proc = psutil.Process(pid)
                    mode, project = _parse_ctback_cmdline(proc.cmdline())
                    if mode == "backup":
                        self.backup_detected.emit(project)
                    elif mode == "restore":
                        before: dict[str, float] = (
                            _scan_dbf_mtimes(self.source_dir) if self.source_dir else {}
                        )
                        self._restore_pids[pid] = {
                            "source_dir": self.source_dir,
                            "before": before,
                            "cmdline_project": project,
                        }
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # Check if any tracked restore processes have now exited
            for pid in list(self._restore_pids):
                if pid in current:
                    continue  # still running
                info = self._restore_pids.pop(pid)
                src = str(info["source_dir"])
                before_state = dict(info["before"])  # type: ignore[arg-type]
                project = ""
                if src and before_state:
                    project = _find_changed_project(src, before_state)
                if not project:
                    project = str(info["cmdline_project"])
                self.restore_completed.emit(project)

        except Exception:
            pass
