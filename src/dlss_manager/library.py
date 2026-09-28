"""The local DLL library: a dedup'd, on-disk store of DLSS component versions
collected from installed games or imported by hand, plus the DB rows that
index them."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .components import Component, component_for_filename
from .db import Database
from .hashutil import sha256_file
from .pe_version import read_pe_version


class UnknownComponentError(ValueError):
    pass


@dataclass(frozen=True)
class LibraryEntry:
    component: Component
    version: str | None
    sha256: str
    path: Path
    is_new: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_version_tag(version: str | None) -> str:
    if not version:
        return "unknown"
    return re.sub(r"[^A-Za-z0-9.]+", "_", version)


def import_file(db: Database, src_path: Path, source: str = "import",
                 filename_hint: str | None = None) -> LibraryEntry:
    """Add a DLL to the local library. `filename_hint` lets callers (e.g. a
    scan of an installed game) pass the original on-disk filename when
    src_path itself might be named differently."""
    name_for_lookup = filename_hint or src_path.name
    component = component_for_filename(name_for_lookup)
    if component is None:
        raise UnknownComponentError(
            f"'{name_for_lookup}' is not a recognized DLSS component filename"
        )

    digest = sha256_file(src_path)
    existing = db.library_file_by_hash(digest)
    if existing:
        return LibraryEntry(
            component=component,
            version=existing["version"],
            sha256=digest,
            path=paths.LIBRARY_DIR / existing["component_key"] / existing["stored_filename"],
            is_new=False,
        )

    info = read_pe_version(src_path)
    version = info.display_version if info else None

    component_dir = paths.LIBRARY_DIR / component.key
    component_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{_safe_version_tag(version)}__{digest[:12]}.dll"
    dest = component_dir / stored_filename
    if not dest.exists():
        shutil.copy2(src_path, dest)

    db.add_library_file(
        component_key=component.key,
        version=version,
        sha256=digest,
        stored_filename=stored_filename,
        original_filename=name_for_lookup,
        source=source,
        added_at=_now(),
    )
    return LibraryEntry(component=component, version=version, sha256=digest, path=dest, is_new=True)


def entry_path(row) -> Path:
    return paths.LIBRARY_DIR / row["component_key"] / row["stored_filename"]


def remove_file(db: Database, file_id: int) -> None:
    row = db.library_file(file_id)
    if row is None:
        raise ValueError(f"no library file with id={file_id}")
    entry_path(row).unlink(missing_ok=True)
    db.delete_library_file(file_id)


def dedupe_library(db: Database) -> list[str]:
    """Clean up the on-disk library dir: remove files with no matching DB row,
    and DB rows whose file went missing. Returns human-readable notes."""
    notes: list[str] = []
    known_paths = {entry_path(row) for row in db.library_files()}

    if paths.LIBRARY_DIR.is_dir():
        for component_dir in paths.LIBRARY_DIR.iterdir():
            if not component_dir.is_dir():
                continue
            for f in component_dir.iterdir():
                if f.is_file() and f not in known_paths:
                    f.unlink()
                    notes.append(f"removed orphan library file {f}")

    for row in db.library_files():
        if not entry_path(row).exists():
            db.delete_library_file(row["id"])
            notes.append(f"removed stale library record for {row['stored_filename']}")

    return notes
