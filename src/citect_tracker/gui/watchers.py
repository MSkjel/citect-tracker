"""Process watcher for automatic ctback32.exe detection."""

from __future__ import annotations

import re

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


class ProcessWatcher(QObject):
    """Polls for ctback32.exe process starts using psutil.

    Emits backup_detected(project_name) when /b mode is detected.
    Emits restore_detected(project_name) when /r mode is detected.

    Falls back silently if psutil is not installed.
    """

    backup_detected = pyqtSignal(str)   # project_name from /D argument
    restore_detected = pyqtSignal(str)  # project_name from /D argument

    _TARGET = "ctback32.exe"
    _POLL_MS = 2000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._known_pids: set[int] = set()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)

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

            for pid in new_pids:
                if self._TARGET.lower() not in current.get(pid, ""):
                    continue
                try:
                    proc = psutil.Process(pid)
                    mode, project = _parse_ctback_cmdline(proc.cmdline())
                    if mode == "backup":
                        self.backup_detected.emit(project)
                    elif mode == "restore":
                        self.restore_detected.emit(project)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
