"""Fetch and install dlssg_for_sm86 (github.com/sdli1995/dlssg_for_sm86) --
unlocks NVIDIA DLSS Frame Generation on RTX 20/30-series (SM75/SM86), which
officially only ship it on RTX 40+. A proxy DLL wraps the unmodified NVIDIA
DLSS-G runtime; the game's own calls to it are left unchanged.

Distribution differs from OptiScaler: there are no uploaded release assets,
just git tags with real GitHub Release metadata (dates, changelog) but no
binaries attached -- the actual files (the proxy DLL, alternates, the INI)
live directly in the repository tree. What we download is GitHub's own
auto-generated source tarball for a tag. There is no publisher-provided
digest to check that download against the way OptiScaler's release assets
carry one -- a real, if modest, drop in verifiability. We still cache by our
own computed hash, but there's nothing external to compare it to.
"""

from __future__ import annotations

import shutil
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import paths
from .hashutil import sha256_file

GITHUB_REPO = "sdli1995/dlssg_for_sm86"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "dlss-manager",
}
REQUEST_TIMEOUT = 20

# "version.dll" is the file as shipped at the tree root; the rest come from
# the alternatives/ folder alongside it, already named for the DLL they proxy.
PROXY_DLL_CHOICES = ["version.dll", "d3d12.dll", "dbghelp.dll", "dinput8.dll", "dxgi.dll", "winmm.dll"]
DEFAULT_PROXY_DLL = "version.dll"

# Two runtime builds ship side by side in every tagged tree: the newer one at
# the tree root, the older one kept in a same-named subfolder for games that
# need it. Both use the same alternatives/ layout underneath.
RUNTIME_BUILDS = {
    "310.9": "",
    "310.1": "310.1",
}
DEFAULT_RUNTIME_BUILD = "310.9"


class DlssgSm86Error(RuntimeError):
    pass


class ExtractionError(DlssgSm86Error):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    published_at: str | None
    body: str
    prerelease: bool


@dataclass(frozen=True)
class InstallResult:
    target_dir: Path
    proxy_filename: str
    runtime_build: str
    installed_files: list[str]
    conflict_backup_path: Path | None


# -- GitHub API -----------------------------------------------------------------

def _parse_release(data: dict) -> ReleaseInfo:
    return ReleaseInfo(
        tag=data["tag_name"],
        published_at=data.get("published_at"),
        body=data.get("body") or "",
        prerelease=bool(data.get("prerelease")),
    )


def fetch_latest_release() -> ReleaseInfo:
    resp = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    return _parse_release(resp.json())


def fetch_releases(limit: int = 15) -> list[ReleaseInfo]:
    resp = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases",
        headers=HEADERS,
        params={"per_page": limit},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return [_parse_release(e) for e in resp.json() if not e.get("draft")]


# -- download / extract -----------------------------------------------------------

def download_source_tarball(release: ReleaseInfo) -> Path:
    """Cached by tag. No publisher digest exists for this to be checked
    against (see module docstring) -- presence in the cache plus the tag
    pin is what integrity we have."""
    dest_dir = paths.DATA_DIR / "dlssg_sm86_cache" / release.tag
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{release.tag}.tar.gz"
    if dest.is_file():
        return dest

    tmp = dest.with_name(dest.name + ".part")
    url = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{release.tag}.tar.gz"
    with requests.get(url, headers=HEADERS, stream=True, timeout=180) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
    tmp.replace(dest)
    return dest


def extract_source(archive_path: Path, dest_dir: Path) -> Path:
    """Extracts the tag's source tarball and returns its single top-level
    folder (GitHub tarballs are always <repo>-<tag>/...)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(dest_dir, filter="data")
    except tarfile.TarError as e:
        raise ExtractionError(f"failed to extract {archive_path}: {e}") from e

    entries = [p for p in dest_dir.iterdir() if p.is_dir()]
    if len(entries) != 1:
        raise ExtractionError(f"unexpected tarball layout in {archive_path} (expected exactly one top folder)")
    return entries[0]


# -- install / uninstall ---------------------------------------------------------

def install_to(
    source_root: Path,
    target_dir: Path,
    proxy_filename: str = DEFAULT_PROXY_DLL,
    runtime_build: str = DEFAULT_RUNTIME_BUILD,
    overwrite_conflict: bool = False,
) -> InstallResult:
    if proxy_filename not in PROXY_DLL_CHOICES:
        raise ValueError(f"unknown proxy filename: {proxy_filename!r}")
    if runtime_build not in RUNTIME_BUILDS:
        raise ValueError(f"unknown runtime build: {runtime_build!r}")

    build_subdir = RUNTIME_BUILDS[runtime_build]
    build_root = source_root / build_subdir if build_subdir else source_root
    dll_src = build_root / "version.dll" if proxy_filename == "version.dll" else build_root / "alternatives" / proxy_filename
    if not dll_src.is_file():
        raise ExtractionError(f"{dll_src} not found -- unexpected layout for build {runtime_build!r}")

    ini_src = source_root / "dlssg_sm86.ini"  # shared across builds, lives at the tree root
    if not ini_src.is_file():
        raise ExtractionError("dlssg_sm86.ini not found in source tree")

    target_dir.mkdir(parents=True, exist_ok=True)

    dll_dest = target_dir / proxy_filename
    conflict_backup_path: Path | None = None
    if dll_dest.exists():
        if not overwrite_conflict:
            raise FileExistsError(f"{dll_dest} already exists")
        backup_dir = paths.BACKUPS_DIR / "dlssg_sm86_conflicts"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        conflict_backup_path = backup_dir / f"{stamp}__{proxy_filename}"
        shutil.copy2(dll_dest, conflict_backup_path)

    shutil.copy2(dll_src, dll_dest)
    shutil.copy2(ini_src, target_dir / "dlssg_sm86.ini")

    return InstallResult(
        target_dir=target_dir,
        proxy_filename=proxy_filename,
        runtime_build=runtime_build,
        installed_files=[proxy_filename, "dlssg_sm86.ini"],
        conflict_backup_path=conflict_backup_path,
    )


def uninstall_from(target_dir: Path, installed_files: list[str], conflict_backup_path: str | None) -> None:
    for rel in installed_files:
        (target_dir / rel).unlink(missing_ok=True)

    if conflict_backup_path:
        backup = Path(conflict_backup_path)
        if backup.is_file():
            proxy_name = backup.name.split("__", 1)[1]
            shutil.copy2(backup, target_dir / proxy_name)
