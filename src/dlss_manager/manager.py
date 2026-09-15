"""High-level orchestration: scanning, applying/rolling back DLL swaps, and
cleanup. This is the one module both the CLI and the GUI drive."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import library, optiscaler, paths
from .db import Database
from .hashutil import sha256_file
from .optiscaler import InstallResult as OptiScalerInstallResult
from .optiscaler import ReleaseInfo
from .pe_version import read_pe_version
from .scanner import find_component_dlls, inspect_dll
from .steam import SteamGame, list_installed_games

# Known log/state files left behind by tools commonly used alongside DLSS
# swapping (Streamline, DLSSTweaks, OptiScaler, Special K). Matched by exact
# filename only (case-insensitive) so we never sweep up unrelated game logs.
WRAPPER_ARTIFACT_FILENAMES = {
    "dlsstweaks.log",
    "sl.interposer.log",
    "sl.log",
    "nvngx.log",
    "nvngx_dlss.log",
    "optiscaler.log",
    "specialk.log",
    "skif_verbose.log",
    "dxgi.log",
    "d3d11.log",
    "d3d12.log",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ApplyResult:
    change_id: int
    backup_path: Path
    previous_version: str | None
    new_version: str | None


class Manager:
    def __init__(self, db: Database | None = None):
        paths.ensure_dirs()
        self.db = db or Database(paths.DB_PATH)

    def close(self) -> None:
        self.db.close()

    # -- scanning -----------------------------------------------------------

    def scan_all(self, collect_into_library: bool = True) -> list[SteamGame]:
        """Rescan every Steam library, refresh the DB, and (by default) pull
        any DLSS DLLs we find into the local library for reuse elsewhere."""
        games = list_installed_games()
        now = _now()
        seen_app_ids = set()

        for game in games:
            seen_app_ids.add(game.app_id)
            self.db.upsert_game(game.app_id, game.name, str(game.install_dir), str(game.library_path), now)

            found = find_component_dlls(game.install_dir)
            rows = []
            for f in found:
                installed = inspect_dll(f)
                rows.append(
                    {
                        "app_id": game.app_id,
                        "component_key": installed.component.key,
                        "relative_path": str(installed.relative_path),
                        "version": installed.version,
                        "sha256": installed.sha256,
                        "last_scanned": now,
                    }
                )
                if collect_into_library:
                    try:
                        library.import_file(
                            self.db, installed.path, source=f"scan:{game.name}",
                            filename_hint=installed.path.name,
                        )
                    except library.UnknownComponentError:
                        pass
            self.db.replace_installed_components(game.app_id, rows)

        # Drop games that are no longer installed so the game list stays honest.
        # (applied_changes / library history is untouched -- rollback still works
        # even after an uninstall/reinstall.)
        for row in self.db.all_games():
            if row["app_id"] not in seen_app_ids:
                self.db.delete_game(row["app_id"])

        return games

    # -- library --------------------------------------------------------------

    def import_dll(self, src_path: Path) -> library.LibraryEntry:
        return library.import_file(self.db, src_path, source="manual-import")

    def library_versions(self, component_key: str):
        return self.db.library_files(component_key=component_key)

    # -- apply / rollback -----------------------------------------------------

    def apply_version(self, app_id: str, component_key: str, library_file_id: int) -> ApplyResult:
        game = self.db.game(app_id)
        if game is None:
            raise ValueError(f"unknown game app_id={app_id}")

        current = None
        for row in self.db.components_for_game(app_id):
            if row["component_key"] == component_key:
                current = row
                break
        if current is None:
            raise ValueError(
                f"{game['name']} has no installed {component_key} component; run a scan first"
            )

        lib_row = next((r for r in self.db.library_files() if r["id"] == library_file_id), None)
        if lib_row is None:
            raise ValueError("library file not found")
        if lib_row["component_key"] != component_key:
            raise ValueError("library file component doesn't match target component")

        target_path = Path(game["install_dir"]) / current["relative_path"]
        if not target_path.is_file():
            raise FileNotFoundError(f"{target_path} no longer exists; rescan")

        previous_sha256 = sha256_file(target_path)
        previous_version = current["version"]

        backup_dir = paths.BACKUPS_DIR / app_id / component_key
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        backup_path = backup_dir / f"{stamp}__{previous_sha256[:12]}.dll"
        shutil.copy2(target_path, backup_path)

        source_path = library.entry_path(lib_row)
        shutil.copy2(source_path, target_path)

        new_sha256 = sha256_file(target_path)
        new_info = read_pe_version(target_path)
        new_version = new_info.display_version if new_info else lib_row["version"]

        change_id = self.db.record_applied_change(
            app_id=app_id,
            game_name=game["name"],
            component_key=component_key,
            target_path=str(target_path),
            relative_path=current["relative_path"],
            backup_path=str(backup_path),
            previous_version=previous_version,
            previous_sha256=previous_sha256,
            new_version=new_version,
            new_sha256=new_sha256,
            applied_at=_now(),
        )

        self.db.replace_installed_components(
            app_id,
            [
                {
                    "app_id": app_id,
                    "component_key": r["component_key"],
                    "relative_path": r["relative_path"],
                    "version": new_version if r["component_key"] == component_key else r["version"],
                    "sha256": new_sha256 if r["component_key"] == component_key else r["sha256"],
                    "last_scanned": _now(),
                }
                for r in self.db.components_for_game(app_id)
            ],
        )

        return ApplyResult(
            change_id=change_id,
            backup_path=backup_path,
            previous_version=previous_version,
            new_version=new_version,
        )

    def rollback_change(self, change_id: int) -> None:
        row = self.db.conn.execute(
            "SELECT * FROM applied_changes WHERE id = ? AND rolled_back_at IS NULL", (change_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no active applied_changes row with id={change_id}")
        self._restore_backup(row)
        self.db.mark_rolled_back(change_id, _now())

    def rollback_all(self) -> int:
        """Undo every DLL swap we've ever made, restoring each target file to
        the state it was in before our very first change to it."""
        active = self.db.active_changes()
        by_target: dict[str, list] = {}
        for row in active:
            by_target.setdefault(row["target_path"], []).append(row)

        restored = 0
        for target_path, rows in by_target.items():
            rows.sort(key=lambda r: r["applied_at"])
            earliest = rows[0]
            try:
                self._restore_backup(earliest)
                restored += 1
            except FileNotFoundError:
                pass
            now = _now()
            for r in rows:
                self.db.mark_rolled_back(r["id"], now)
        return restored

    def _restore_backup(self, applied_change_row) -> None:
        backup_path = Path(applied_change_row["backup_path"])
        target_path = Path(applied_change_row["target_path"])
        if not backup_path.is_file():
            raise FileNotFoundError(f"backup missing: {backup_path}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, target_path)

    # -- cleanup ------------------------------------------------------------

    def find_wrapper_artifacts(self, app_id: str | None = None) -> list[Path]:
        games = self.db.all_games() if app_id is None else [self.db.game(app_id)]
        found: list[Path] = []
        for game in games:
            if game is None:
                continue
            install_dir = Path(game["install_dir"])
            if not install_dir.is_dir():
                continue
            for root, _dirs, files in os.walk(install_dir, followlinks=False):
                for fname in files:
                    if fname.lower() in WRAPPER_ARTIFACT_FILENAMES:
                        found.append(Path(root) / fname)
        return found

    def clean_wrapper_artifacts(self, app_id: str | None = None, dry_run: bool = True) -> list[Path]:
        artifacts = self.find_wrapper_artifacts(app_id)
        if not dry_run:
            for p in artifacts:
                p.unlink(missing_ok=True)
        return artifacts

    def clean_app_log(self) -> bool:
        if paths.APP_LOG_FILE.is_file():
            paths.APP_LOG_FILE.unlink()
            return True
        return False

    def dedupe_library(self) -> list[str]:
        return library.dedupe_library(self.db)

    # -- OptiScaler -----------------------------------------------------------

    def optiscaler_releases(self, limit: int = 15) -> list[ReleaseInfo]:
        return optiscaler.fetch_releases(limit=limit)

    def latest_optiscaler_release(self) -> ReleaseInfo:
        return optiscaler.fetch_latest_release()

    def suggest_optiscaler_targets(self, app_id: str) -> list[Path]:
        game = self.db.game(app_id)
        if game is None:
            raise ValueError(f"unknown game app_id={app_id}")
        return optiscaler.suggest_install_targets(Path(game["install_dir"]))

    def install_optiscaler(
        self,
        app_id: str,
        target_dir: Path,
        proxy_filename: str = optiscaler.DEFAULT_PROXY_DLL,
        release: ReleaseInfo | None = None,
        overwrite_conflict: bool = False,
    ) -> OptiScalerInstallResult:
        game = self.db.game(app_id)
        if game is None:
            raise ValueError(f"unknown game app_id={app_id}")

        release = release or optiscaler.fetch_latest_release()
        archive = optiscaler.download_asset(release)

        with tempfile.TemporaryDirectory(prefix="optiscaler-") as tmp:
            staging = Path(tmp) / "staging"
            optiscaler.extract_archive(archive, staging)
            result = optiscaler.install_to(
                staging, target_dir, proxy_filename, overwrite_conflict=overwrite_conflict
            )

        self.db.record_optiscaler_install(
            app_id=app_id,
            game_name=game["name"],
            target_dir=str(result.target_dir),
            proxy_filename=result.proxy_filename,
            version=release.tag,
            installed_files=json.dumps(result.installed_files),
            conflict_backup_path=str(result.conflict_backup_path) if result.conflict_backup_path else None,
            installed_at=_now(),
        )
        return result

    def uninstall_optiscaler(self, install_id: int) -> None:
        row = self.db.optiscaler_install(install_id)
        if row is None or row["removed_at"] is not None:
            raise ValueError(f"no active optiscaler install with id={install_id}")
        optiscaler.uninstall_from(
            Path(row["target_dir"]), json.loads(row["installed_files"]), row["conflict_backup_path"]
        )
        self.db.mark_optiscaler_removed(install_id, _now())

    def update_optiscaler(self, install_id: int, release: ReleaseInfo | None = None) -> OptiScalerInstallResult:
        row = self.db.optiscaler_install(install_id)
        if row is None or row["removed_at"] is not None:
            raise ValueError(f"no active optiscaler install with id={install_id}")

        release = release or optiscaler.fetch_latest_release()
        target_dir = Path(row["target_dir"])
        proxy_filename = row["proxy_filename"]

        # OptiScaler.ini may hold user tweaks (upscaler choice, FG options, ...);
        # keep a dated copy before the reinstall overwrites it with the new default.
        ini_path = target_dir / "OptiScaler.ini"
        if ini_path.is_file():
            backup_dir = paths.BACKUPS_DIR / "optiscaler_ini"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            shutil.copy2(ini_path, backup_dir / f"{stamp}__{row['app_id']}__OptiScaler.ini")

        optiscaler.uninstall_from(target_dir, json.loads(row["installed_files"]), row["conflict_backup_path"])
        self.db.mark_optiscaler_removed(install_id, _now())

        return self.install_optiscaler(
            row["app_id"], target_dir, proxy_filename=proxy_filename, release=release, overwrite_conflict=True
        )

    def check_optiscaler_updates(self) -> list[tuple]:
        """Returns (install_row, latest_release) pairs for installs whose
        version differs from the newest one on GitHub."""
        latest = optiscaler.fetch_latest_release()
        return [
            (row, latest)
            for row in self.db.active_optiscaler_installs()
            if row["version"] != latest.tag
        ]
