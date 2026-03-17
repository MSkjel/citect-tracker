"""Typed, centralised accessor for all application QSettings.

Import the module-level ``settings`` singleton and use its typed
properties instead of raw ``QSettings().value(...)`` calls.

    from .app_settings import settings

    if settings.auto_backup:
        ...
    settings.auto_backup = True
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QByteArray, QSettings


class AppSettings:
    """Typed read/write accessors for every application setting key."""

    # ------------------------------------------------------------------
    # Options (user preferences)
    # ------------------------------------------------------------------

    @property
    def auto_backup(self) -> bool:
        return QSettings().value("options/auto_backup", False, type=bool)

    @auto_backup.setter
    def auto_backup(self, v: bool) -> None:
        QSettings().setValue("options/auto_backup", v)

    @property
    def auto_restore(self) -> bool:
        return QSettings().value("options/auto_restore", False, type=bool)

    @auto_restore.setter
    def auto_restore(self, v: bool) -> None:
        QSettings().setValue("options/auto_restore", v)

    @property
    def auto_compare(self) -> bool:
        return QSettings().value("options/auto_compare", True, type=bool)

    @auto_compare.setter
    def auto_compare(self, v: bool) -> None:
        QSettings().setValue("options/auto_compare", v)

    @property
    def theme(self) -> str:
        return str(QSettings().value("options/theme", "System"))

    @theme.setter
    def theme(self, v: str) -> None:
        QSettings().setValue("options/theme", v)

    # ------------------------------------------------------------------
    # Paths / session
    # ------------------------------------------------------------------

    @property
    def last_dbf_directory(self) -> str:
        return str(QSettings().value("last_dbf_directory", ""))

    @last_dbf_directory.setter
    def last_dbf_directory(self, v: str) -> None:
        QSettings().setValue("last_dbf_directory", v)

    @property
    def db_path(self) -> str:
        return str(QSettings().value("db_path", ""))

    @db_path.setter
    def db_path(self, v: str) -> None:
        QSettings().setValue("db_path", v)

    @property
    def user_name(self) -> str:
        return str(QSettings().value("user_name", ""))

    @user_name.setter
    def user_name(self, v: str) -> None:
        QSettings().setValue("user_name", v)

    # ------------------------------------------------------------------
    # Project tree state
    # ------------------------------------------------------------------

    @property
    def excluded_projects(self) -> list[str]:
        v = QSettings().value("excluded_projects", [])
        return list(v) if v else []

    @excluded_projects.setter
    def excluded_projects(self, v: list[str]) -> None:
        QSettings().setValue("excluded_projects", v)

    @property
    def hidden_projects(self) -> list[str]:
        v = QSettings().value("hidden_projects", [])
        return list(v) if v else []

    @hidden_projects.setter
    def hidden_projects(self, v: list[str]) -> None:
        QSettings().setValue("hidden_projects", v)

    @property
    def project_flat_mode(self) -> bool:
        return QSettings().value("project_flat_mode", False, type=bool)

    @project_flat_mode.setter
    def project_flat_mode(self, v: bool) -> None:
        QSettings().setValue("project_flat_mode", v)

    # ------------------------------------------------------------------
    # Window / splitter geometry
    # ------------------------------------------------------------------

    def _get_state(self, key: str) -> Optional[QByteArray]:
        val = QSettings().value(key)
        if val is None:
            return None
        return val if isinstance(val, QByteArray) else QByteArray(val)

    def _set_state(self, key: str, val: QByteArray) -> None:
        QSettings().setValue(key, val)

    @property
    def window_geometry(self) -> Optional[QByteArray]:
        return self._get_state("window/geometry")

    @window_geometry.setter
    def window_geometry(self, v: QByteArray) -> None:
        self._set_state("window/geometry", v)

    @property
    def splitter_main(self) -> Optional[QByteArray]:
        return self._get_state("splitter/main")

    @splitter_main.setter
    def splitter_main(self, v: QByteArray) -> None:
        self._set_state("splitter/main", v)

    @property
    def splitter_left(self) -> Optional[QByteArray]:
        return self._get_state("splitter/left")

    @splitter_left.setter
    def splitter_left(self, v: QByteArray) -> None:
        self._set_state("splitter/left", v)

    @property
    def splitter_right(self) -> Optional[QByteArray]:
        return self._get_state("splitter/right")

    @splitter_right.setter
    def splitter_right(self, v: QByteArray) -> None:
        self._set_state("splitter/right", v)

    @property
    def header_diff_table(self) -> Optional[QByteArray]:
        return self._get_state("header/diff_table")

    @header_diff_table.setter
    def header_diff_table(self, v: QByteArray) -> None:
        self._set_state("header/diff_table", v)


settings = AppSettings()
