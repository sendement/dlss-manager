"""Walk a game's install directory looking for known DLSS component DLLs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .components import Component, component_for_filename
from .hashutil import sha256_file
from .pe_version import read_pe_version


@dataclass(frozen=True)
class FoundDll:
    component: Component
    path: Path
    relative_path: Path


@dataclass(frozen=True)
class InstalledComponent:
    component: Component
    path: Path
    relative_path: Path
    version: str | None
    sha256: str


def find_component_dlls(install_dir: Path) -> list[FoundDll]:
    """Filename-only pass: cheap, just a directory walk."""
    found: list[FoundDll] = []
    for root, _dirs, files in os.walk(install_dir, followlinks=False):
        for fname in files:
            comp = component_for_filename(fname)
            if comp is None:
                continue
            full = Path(root) / fname
            found.append(
                FoundDll(
                    component=comp,
                    path=full,
                    relative_path=full.relative_to(install_dir),
                )
            )
    return found


def inspect_dll(found: FoundDll) -> InstalledComponent:
    """Hash + read version. Only called for the handful of files find_component_dlls matched."""
    info = read_pe_version(found.path)
    return InstalledComponent(
        component=found.component,
        path=found.path,
        relative_path=found.relative_path,
        version=info.display_version if info else None,
        sha256=sha256_file(found.path),
    )


def scan_game(install_dir: Path) -> list[InstalledComponent]:
    return [inspect_dll(f) for f in find_component_dlls(install_dir)]
