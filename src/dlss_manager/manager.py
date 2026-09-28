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

from . import dlssg_sm86, library, optiscaler, paths
from .components import KEY_TO_COMPONENT
from .db import Database
from .dlssg_sm86 import InstallResult as DlssgSm86InstallResult
from .dlssg_sm86 import ReleaseInfo as DlssgSm86ReleaseInfo
from .hashutil import sha256_file
from .optiscaler import InstallResult as OptiScalerInstallResult
from .optiscaler import ReleaseInfo
from .pe_version import read_pe_version, version_sort_key
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

    def import_dlls(self, src_paths: list[Path]) -> tuple[list[library.LibraryEntry], list[tuple[Path, str]]]:
        """Best-effort bulk import (e.g. drag-and-drop of several files at once).
        Returns (entries, [(path, error_message), ...]) so callers can report both."""
        entries: list[library.LibraryEntry] = []
        errors: list[tuple[Path, str]] = []
        for p in src_paths:
            try:
                entries.append(self.import_dll(p))
            except (library.UnknownComponentError, OSError) as e:
                errors.append((p, str(e)))
        return entries, errors

    def library_versions(self, component_key: str):
        return self.db.library_files(component_key=component_key)

    def all_library_files(self):
        return self.db.library_files()

    def remove_library_file(self, file_id: int) -> None:
        library.remove_file(self.db, file_id)

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

    def optiscaler_sources(self) -> list[optiscaler.Source]:
        return list(optiscaler.SOURCES.values())

    def optiscaler_releases(self, source_key: str = optiscaler.DEFAULT_SOURCE_KEY, limit: int = 15) -> list[ReleaseInfo]:
        return optiscaler.fetch_releases(source_key=source_key, limit=limit)

    def latest_optiscaler_release(self, source_key: str = optiscaler.DEFAULT_SOURCE_KEY) -> ReleaseInfo:
        return optiscaler.fetch_latest_release(source_key=source_key)

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
        source_key: str = optiscaler.DEFAULT_SOURCE_KEY,
        overwrite_conflict: bool = False,
    ) -> OptiScalerInstallResult:
        game = self.db.game(app_id)
        if game is None:
            raise ValueError(f"unknown game app_id={app_id}")

        release = release or optiscaler.fetch_latest_release(source_key=source_key)
        archive = optiscaler.download_asset(release)

        # A fresh install into a folder that already has an active install recorded
        # is a reinstall of our own build, not a genuine conflict with some other
        # tool -- carry the ORIGINAL conflict backup (the true pre-OptiScaler file,
        # if any) forward instead of treating our own previous build as a new one.
        target_dir_str = str(target_dir)
        superseded = [
            r for r in self.db.active_optiscaler_installs(app_id) if r["target_dir"] == target_dir_str
        ]
        inherited_conflict_backup = superseded[0]["conflict_backup_path"] if superseded else None

        with tempfile.TemporaryDirectory(prefix="optiscaler-") as tmp:
            staging = Path(tmp) / "staging"
            optiscaler.extract_archive(archive, staging)
            result = optiscaler.install_to(
                staging, target_dir, proxy_filename, overwrite_conflict=overwrite_conflict or bool(superseded)
            )

        # Neural Rendering forks need nvngx_dlssnr.dll, which NVIDIA doesn't let
        # anyone redistribute (see Source.note) -- this app never downloads it,
        # but if the user already imported their own copy into the library (scan
        # of a game that ships it, or manual drag-and-drop), reuse it here rather
        # than leaving the game without it when it doesn't already have one.
        tracked_files = list(result.installed_files)
        source = optiscaler.SOURCES.get(release.source_key)
        if source and source.supports_neural_rendering:
            neural_filename = KEY_TO_COMPONENT["neural_rendering"].filename
            neural_dest = result.target_dir / neural_filename
            if not neural_dest.is_file():
                lib_rows = self.db.library_files(component_key="neural_rendering")
                if lib_rows:
                    best = max(lib_rows, key=lambda r: version_sort_key(r["version"] or "0"))
                    shutil.copy2(library.entry_path(best), neural_dest)
                    tracked_files.append(neural_filename)
                    result.installed_files[:] = tracked_files  # keep result truthful too

        now = _now()
        for row in superseded:
            self.db.mark_optiscaler_removed(row["id"], now)

        if superseded and result.conflict_backup_path:
            # That backup is just our own previous build's file, not worth keeping.
            result.conflict_backup_path.unlink(missing_ok=True)
            conflict_backup_path = inherited_conflict_backup
        else:
            conflict_backup_path = str(result.conflict_backup_path) if result.conflict_backup_path else None

        self.db.record_optiscaler_install(
            app_id=app_id,
            game_name=game["name"],
            target_dir=str(result.target_dir),
            proxy_filename=result.proxy_filename,
            source_key=release.source_key,
            version=release.tag,
            installed_files=json.dumps(tracked_files),
            conflict_backup_path=conflict_backup_path,
            installed_at=now,
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

    def rollback_optiscaler_to_clean_state(self, install_id: int) -> list[Path]:
        """Uninstall, then also sweep whatever OptiScaler/wrapper log & state
        files it left behind at runtime (OptiScaler.log and friends aren't
        part of the installed-files manifest -- they get created when the
        game actually runs). Returns the extra files that were removed."""
        row = self.db.optiscaler_install(install_id)
        if row is None or row["removed_at"] is not None:
            raise ValueError(f"no active optiscaler install with id={install_id}")
        app_id = row["app_id"]
        self.uninstall_optiscaler(install_id)
        return self.clean_wrapper_artifacts(app_id=app_id, dry_run=False)

    def update_optiscaler(self, install_id: int, release: ReleaseInfo | None = None) -> OptiScalerInstallResult:
        row = self.db.optiscaler_install(install_id)
        if row is None or row["removed_at"] is not None:
            raise ValueError(f"no active optiscaler install with id={install_id}")

        source_key = row["source_key"]
        release = release or optiscaler.fetch_latest_release(source_key=source_key)
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
            row["app_id"],
            target_dir,
            proxy_filename=proxy_filename,
            release=release,
            source_key=source_key,
            overwrite_conflict=True,
        )

    def check_optiscaler_updates(self) -> list[tuple]:
        """Returns (install_row, latest_release) pairs for installs whose
        version differs from the newest one on GitHub, grouped per source."""
        latest_by_source: dict[str, ReleaseInfo] = {}
        pending = []
        for row in self.db.active_optiscaler_installs():
            source_key = row["source_key"]
            if source_key not in latest_by_source:
                latest_by_source[source_key] = optiscaler.fetch_latest_release(source_key=source_key)
            latest = latest_by_source[source_key]
            if row["version"] != latest.tag:
                pending.append((row, latest))
        return pending

    # -- dlssg_for_sm86 (DLSS Frame Generation unlock for RTX 20/30) ---------

    def dlssg_sm86_releases(self, limit: int = 15) -> list[DlssgSm86ReleaseInfo]:
        return dlssg_sm86.fetch_releases(limit=limit)

    def latest_dlssg_sm86_release(self) -> DlssgSm86ReleaseInfo:
        return dlssg_sm86.fetch_latest_release()

    def suggest_dlssg_sm86_targets(self, app_id: str) -> list[Path]:
        game = self.db.game(app_id)
        if game is None:
            raise ValueError(f"unknown game app_id={app_id}")
        return optiscaler.suggest_install_targets(Path(game["install_dir"]))

    def install_dlssg_sm86(
        self,
        app_id: str,
        target_dir: Path,
        proxy_filename: str = dlssg_sm86.DEFAULT_PROXY_DLL,
        runtime_build: str = dlssg_sm86.DEFAULT_RUNTIME_BUILD,
        release: DlssgSm86ReleaseInfo | None = None,
        overwrite_conflict: bool = False,
    ) -> DlssgSm86InstallResult:
        game = self.db.game(app_id)
        if game is None:
            raise ValueError(f"unknown game app_id={app_id}")

        release = release or dlssg_sm86.fetch_latest_release()
        archive = dlssg_sm86.download_source_tarball(release)

        target_dir_str = str(target_dir)
        superseded = [
            r for r in self.db.active_dlssg_sm86_installs(app_id) if r["target_dir"] == target_dir_str
        ]
        inherited_conflict_backup = superseded[0]["conflict_backup_path"] if superseded else None

        with tempfile.TemporaryDirectory(prefix="dlssg-sm86-") as tmp:
            staging = Path(tmp) / "staging"
            source_root = dlssg_sm86.extract_source(archive, staging)
            result = dlssg_sm86.install_to(
                source_root,
                target_dir,
                proxy_filename,
                runtime_build,
                overwrite_conflict=overwrite_conflict or bool(superseded),
            )

        now = _now()
        for row in superseded:
            self.db.mark_dlssg_sm86_removed(row["id"], now)

        if superseded and result.conflict_backup_path:
            result.conflict_backup_path.unlink(missing_ok=True)
            conflict_backup_path = inherited_conflict_backup
        else:
            conflict_backup_path = str(result.conflict_backup_path) if result.conflict_backup_path else None

        self.db.record_dlssg_sm86_install(
            app_id=app_id,
            game_name=game["name"],
            target_dir=str(result.target_dir),
            proxy_filename=result.proxy_filename,
            runtime_build=result.runtime_build,
            version=release.tag,
            installed_files=json.dumps(result.installed_files),
            conflict_backup_path=conflict_backup_path,
            installed_at=now,
        )
        return result

    def uninstall_dlssg_sm86(self, install_id: int) -> None:
        row = self.db.dlssg_sm86_install(install_id)
        if row is None or row["removed_at"] is not None:
            raise ValueError(f"no active dlssg_sm86 install with id={install_id}")
        dlssg_sm86.uninstall_from(
            Path(row["target_dir"]), json.loads(row["installed_files"]), row["conflict_backup_path"]
        )
        self.db.mark_dlssg_sm86_removed(install_id, _now())

    def update_dlssg_sm86(
        self, install_id: int, release: DlssgSm86ReleaseInfo | None = None
    ) -> DlssgSm86InstallResult:
        row = self.db.dlssg_sm86_install(install_id)
        if row is None or row["removed_at"] is not None:
            raise ValueError(f"no active dlssg_sm86 install with id={install_id}")

        release = release or dlssg_sm86.fetch_latest_release()
        target_dir = Path(row["target_dir"])

        dlssg_sm86.uninstall_from(target_dir, json.loads(row["installed_files"]), row["conflict_backup_path"])
        self.db.mark_dlssg_sm86_removed(install_id, _now())

        return self.install_dlssg_sm86(
            row["app_id"],
            target_dir,
            proxy_filename=row["proxy_filename"],
            runtime_build=row["runtime_build"],
            release=release,
            overwrite_conflict=True,
        )

    def check_dlssg_sm86_updates(self) -> list[tuple]:
        latest = dlssg_sm86.fetch_latest_release()
        return [(row, latest) for row in self.db.active_dlssg_sm86_installs() if row["version"] != latest.tag]

    def rollback_dlssg_sm86_to_clean_state(self, install_id: int) -> None:
        """dlssg_for_sm86 has no runtime-generated log files of its own to
        sweep (unlike OptiScaler) -- uninstall already leaves the folder
        clean. Kept as a separate method for a consistent GUI/CLI shape."""
        self.uninstall_dlssg_sm86(install_id)
