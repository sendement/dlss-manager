"""Filesystem locations used by the app (data dir, library, backups, db, logs)."""

from __future__ import annotations

from pathlib import Path

from platformdirs import PlatformDirs

_dirs = PlatformDirs(appname="dlss-manager", appauthor=False)

DATA_DIR = Path(_dirs.user_data_dir)
LIBRARY_DIR = DATA_DIR / "library"
BACKUPS_DIR = DATA_DIR / "backups"
LOG_DIR = Path(_dirs.user_log_dir)
DB_PATH = DATA_DIR / "dlss_manager.db"
APP_LOG_FILE = LOG_DIR / "dlss-manager.log"


def ensure_dirs() -> None:
    for d in (DATA_DIR, LIBRARY_DIR, BACKUPS_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
