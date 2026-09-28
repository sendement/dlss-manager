"""Discover Steam library folders and installed games (Windows + Linux/Proton)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .vdf import parse_vdf


@dataclass(frozen=True)
class SteamGame:
    app_id: str
    name: str
    install_dir: Path
    library_path: Path


def default_steam_roots() -> list[Path]:
    """Candidate Steam install roots for the current platform, existing ones only."""
    candidates: list[Path] = []
    home = Path.home()

    if sys.platform.startswith("win"):
        for env_var in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(env_var)
            if base:
                candidates.append(Path(base) / "Steam")
        candidates.append(Path("C:/Program Files (x86)/Steam"))
    else:
        candidates += [
            home / ".local/share/Steam",
            home / ".steam/steam",
            home / ".steam/root",
            home / ".var/app/com.valvesoftware.Steam/data/Steam",  # flatpak
            home / "snap/steam/common/.local/share/Steam",  # snap
        ]

    seen: set[Path] = set()
    roots: list[Path] = []
    for c in candidates:
        if c.is_dir():
            resolved = c.resolve()
            if resolved not in seen:
                seen.add(resolved)
                roots.append(resolved)
    return roots


def find_library_folders(steam_root: Path) -> list[Path]:
    """Every Steam library folder (the root itself plus any extra drives)."""
    libs: list[Path] = []
    if (steam_root / "steamapps").is_dir():
        libs.append(steam_root)

    vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
    if vdf_path.is_file():
        try:
            data = parse_vdf(vdf_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            data = {}
        entries = data.get("libraryfolders", {})
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            path_str = entry.get("path")
            if not path_str:
                continue
            p = Path(path_str)
            if p.is_dir() and p not in libs:
                libs.append(p)

    seen: set[Path] = set()
    unique: list[Path] = []
    for lib in libs:
        resolved = lib.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(lib)
    return unique


# Steam lists its own runtime/compat tooling as "apps" too. These never
# contain game content, so there's no point scanning them.
_NON_GAME_PREFIXES = ("Proton ", "Proton-", "SteamLinuxRuntime", "Steamworks Shared")


def _looks_like_tool(install_dir_name: str) -> bool:
    return install_dir_name.startswith(_NON_GAME_PREFIXES)


def list_installed_games(steam_roots: list[Path] | None = None) -> list[SteamGame]:
    roots = steam_roots if steam_roots is not None else default_steam_roots()

    games: dict[str, SteamGame] = {}
    for root in roots:
        for library in find_library_folders(root):
            steamapps = library / "steamapps"
            common = steamapps / "common"
            for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
                try:
                    data = parse_vdf(manifest.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                state = data.get("AppState", {})
                app_id = state.get("appid")
                name = state.get("name")
                install_dir_name = state.get("installdir")
                if not (app_id and name and install_dir_name):
                    continue
                if _looks_like_tool(install_dir_name):
                    continue
                install_dir = common / install_dir_name
                if not install_dir.is_dir():
                    continue
                if app_id not in games:
                    games[app_id] = SteamGame(
                        app_id=app_id,
                        name=name,
                        install_dir=install_dir,
                        library_path=library,
                    )
    return sorted(games.values(), key=lambda g: g.name.lower())
