"""SQLite persistence: known games, their scanned components, the local DLL
library, and the log of changes we've applied (so we can roll them back)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    app_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    install_dir TEXT NOT NULL,
    library_path TEXT NOT NULL,
    last_scanned TEXT
);

CREATE TABLE IF NOT EXISTS installed_components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL REFERENCES games(app_id) ON DELETE CASCADE,
    component_key TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    version TEXT,
    sha256 TEXT NOT NULL,
    last_scanned TEXT NOT NULL,
    UNIQUE(app_id, relative_path)
);

CREATE TABLE IF NOT EXISTS library_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component_key TEXT NOT NULL,
    version TEXT,
    sha256 TEXT NOT NULL UNIQUE,
    stored_filename TEXT NOT NULL,
    original_filename TEXT,
    source TEXT NOT NULL,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS optiscaler_installs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    game_name TEXT NOT NULL,
    target_dir TEXT NOT NULL,
    proxy_filename TEXT NOT NULL,
    version TEXT NOT NULL,
    installed_files TEXT NOT NULL,
    conflict_backup_path TEXT,
    installed_at TEXT NOT NULL,
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS applied_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    game_name TEXT NOT NULL,
    component_key TEXT NOT NULL,
    target_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    previous_version TEXT,
    previous_sha256 TEXT NOT NULL,
    new_version TEXT,
    new_sha256 TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    rolled_back_at TEXT
);
"""


class Database:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- games / installed_components -----------------------------------

    def upsert_game(self, app_id: str, name: str, install_dir: str, library_path: str, scanned_at: str) -> None:
        with closing(self.conn.cursor()) as cur:
            cur.execute(
                """INSERT INTO games (app_id, name, install_dir, library_path, last_scanned)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(app_id) DO UPDATE SET
                       name=excluded.name,
                       install_dir=excluded.install_dir,
                       library_path=excluded.library_path,
                       last_scanned=excluded.last_scanned""",
                (app_id, name, install_dir, library_path, scanned_at),
            )
        self.conn.commit()

    def replace_installed_components(self, app_id: str, components: list[dict]) -> None:
        with closing(self.conn.cursor()) as cur:
            cur.execute("DELETE FROM installed_components WHERE app_id = ?", (app_id,))
            cur.executemany(
                """INSERT INTO installed_components
                       (app_id, component_key, relative_path, version, sha256, last_scanned)
                   VALUES (:app_id, :component_key, :relative_path, :version, :sha256, :last_scanned)""",
                components,
            )
        self.conn.commit()

    def all_games(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM games ORDER BY name COLLATE NOCASE").fetchall()

    def delete_game(self, app_id: str) -> None:
        self.conn.execute("DELETE FROM games WHERE app_id = ?", (app_id,))
        self.conn.commit()

    def game(self, app_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM games WHERE app_id = ?", (app_id,)).fetchone()

    def components_for_game(self, app_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM installed_components WHERE app_id = ? ORDER BY component_key", (app_id,)
        ).fetchall()

    # -- library ----------------------------------------------------------

    def add_library_file(self, component_key: str, version: str | None, sha256: str,
                          stored_filename: str, original_filename: str | None,
                          source: str, added_at: str) -> None:
        self.conn.execute(
            """INSERT INTO library_files
                   (component_key, version, sha256, stored_filename, original_filename, source, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sha256) DO NOTHING""",
            (component_key, version, sha256, stored_filename, original_filename, source, added_at),
        )
        self.conn.commit()

    def library_file_by_hash(self, sha256: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM library_files WHERE sha256 = ?", (sha256,)).fetchone()

    def library_files(self, component_key: str | None = None) -> list[sqlite3.Row]:
        if component_key:
            return self.conn.execute(
                "SELECT * FROM library_files WHERE component_key = ? ORDER BY version", (component_key,)
            ).fetchall()
        return self.conn.execute("SELECT * FROM library_files ORDER BY component_key, version").fetchall()

    def delete_library_file(self, file_id: int) -> None:
        self.conn.execute("DELETE FROM library_files WHERE id = ?", (file_id,))
        self.conn.commit()

    # -- applied changes ----------------------------------------------------

    def record_applied_change(self, **fields) -> int:
        cur = self.conn.execute(
            """INSERT INTO applied_changes
                   (app_id, game_name, component_key, target_path, relative_path, backup_path,
                    previous_version, previous_sha256, new_version, new_sha256, applied_at)
               VALUES (:app_id, :game_name, :component_key, :target_path, :relative_path, :backup_path,
                       :previous_version, :previous_sha256, :new_version, :new_sha256, :applied_at)""",
            fields,
        )
        self.conn.commit()
        return cur.lastrowid

    def active_changes(self, app_id: str | None = None) -> list[sqlite3.Row]:
        if app_id:
            return self.conn.execute(
                "SELECT * FROM applied_changes WHERE rolled_back_at IS NULL AND app_id = ? ORDER BY applied_at DESC",
                (app_id,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM applied_changes WHERE rolled_back_at IS NULL ORDER BY applied_at DESC"
        ).fetchall()

    def mark_rolled_back(self, change_id: int, rolled_back_at: str) -> None:
        self.conn.execute(
            "UPDATE applied_changes SET rolled_back_at = ? WHERE id = ?", (rolled_back_at, change_id)
        )
        self.conn.commit()

    # -- optiscaler installs ------------------------------------------------

    def record_optiscaler_install(self, **fields) -> int:
        cur = self.conn.execute(
            """INSERT INTO optiscaler_installs
                   (app_id, game_name, target_dir, proxy_filename, version,
                    installed_files, conflict_backup_path, installed_at)
               VALUES (:app_id, :game_name, :target_dir, :proxy_filename, :version,
                       :installed_files, :conflict_backup_path, :installed_at)""",
            fields,
        )
        self.conn.commit()
        return cur.lastrowid

    def active_optiscaler_installs(self, app_id: str | None = None) -> list[sqlite3.Row]:
        if app_id:
            return self.conn.execute(
                "SELECT * FROM optiscaler_installs WHERE removed_at IS NULL AND app_id = ? ORDER BY installed_at DESC",
                (app_id,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM optiscaler_installs WHERE removed_at IS NULL ORDER BY installed_at DESC"
        ).fetchall()

    def optiscaler_install(self, install_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM optiscaler_installs WHERE id = ?", (install_id,)
        ).fetchone()

    def mark_optiscaler_removed(self, install_id: int, removed_at: str) -> None:
        self.conn.execute(
            "UPDATE optiscaler_installs SET removed_at = ? WHERE id = ?", (removed_at, install_id)
        )
        self.conn.commit()
