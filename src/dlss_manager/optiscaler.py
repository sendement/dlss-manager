"""Fetch, install, update and remove OptiScaler (github.com/optiscaler/OptiScaler)
in a game's folder.

OptiScaler ships as a single .7z per release containing OptiScaler.dll (which
gets renamed to whatever system DLL the game will load, e.g. dxgi.dll) plus a
handful of companion DLLs/config. There's no installer API -- we replicate
what the project's own setup_windows.bat / setup_linux.sh scripts do (see
their source, bundled in every release) rather than guessing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import paths
from .hashutil import sha256_file

GITHUB_REPO = "optiscaler/OptiScaler"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "dlss-manager",
}
REQUEST_TIMEOUT = 20


@dataclass(frozen=True)
class Source:
    key: str
    repo: str
    label: str
    note: str = ""


# Known places to get an OptiScaler build from. "official" is the upstream
# project; anything else is a third-party fork and gets labeled as such
# everywhere the UI shows it -- see README/memory for how each one was vetted.
SOURCES: dict[str, Source] = {
    "official": Source(
        key="official",
        repo=GITHUB_REPO,
        label="OptiScaler (официальный)",
    ),
    "dagherbou-dlssnr": Source(
        key="dagherbou-dlssnr",
        repo="Dagherbou/OptiScaler_DLSSNR",
        label="OptiScaler + DLSS Neural Rendering / DLSS 5 (сторонний форк, без MFG-unlock)",
        note=(
            "Неофициальный форк upstream OptiScaler (тот же круг контрибьюторов, что и в "
            "официальном проекте). Добавляет DLSS Neural Rendering (DLSS 5 -- реальная технология "
            "NVIDIA, анонсирована на GTC 2026, но официальной интеграции 'в любую игру' NVIDIA не "
            "публикует) поверх DLSS/FSR/XeSS. Без MFG-unlock и без патчинга NVIDIA-кода в памяти.\n"
            "Требует файл nvngx_dlssnr.dll (~165 МБ) -- в архиве его НЕТ, автор прямо пишет, что "
            "распространять его не будет (это файл NVIDIA). Это приложение никогда не будет само "
            "его скачивать откуда-либо -- добавить его в папку игры нужно вручную.\n"
            "Это база, на которой сделан форк klebermotta-mfg (тот добавляет поверх ещё и MFG-unlock)."
        ),
    ),
    "klebermotta-mfg": Source(
        key="klebermotta-mfg",
        repo="KleberMotta/OptiScaler-DLSS5-MFG-RTX40",
        label="OptiScaler + Neural Rendering + MFG unlock RTX40 (эксперимент, сторонний форк)",
        note=(
            "Неофициальный форк форка Dagherbou/OptiScaler_DLSSNR (см. выше), добавляет ещё один "
            "кусок:\n"
            "MFG-unlock -- патчит nvngx_dlssg.dll В ПАМЯТИ, чтобы разрешить 3x/4x/6x Frame "
            "Generation на RTX 40. Не использовать в мультиплеере -- это модификация кода "
            "NVIDIA внутри процесса игры, риск бана.\n"
            "Neural Rendering здесь работает так же, как в dagherbou-dlssnr: требует nvngx_dlssnr.dll "
            "(~165 МБ), которого в архиве нет и который это приложение никогда не будет само "
            "скачивать откуда-либо -- нужно добавить его в папку игры самостоятельно.\n"
            "Проверено автором лично на одной игре, поддерживается одним человеком."
        ),
    ),
}
DEFAULT_SOURCE_KEY = "official"

# The DLL names OptiScaler.dll can be renamed to, per its own setup scripts --
# whichever one the target game actually loads and doesn't already use.
PROXY_DLL_CHOICES = [
    "dxgi.dll",
    "winmm.dll",
    "version.dll",
    "dbghelp.dll",
    "d3d12.dll",
    "wininet.dll",
    "winhttp.dll",
    "OptiScaler.asi",
]
DEFAULT_PROXY_DLL = "dxgi.dll"

# Files the upstream archive includes purely for its own bash/bat installer;
# we replicate that installer's logic ourselves so these never get copied.
_SKIP_FILENAMES = {"setup_windows.bat", "setup_linux.sh", "remove_optiscaler.sh", "remove_optiscaler.bat"}


class OptiScalerError(RuntimeError):
    pass


class ExtractionError(OptiScalerError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    source_key: str
    tag: str
    name: str
    published_at: str | None
    body: str
    asset_name: str
    asset_url: str
    asset_size: int
    asset_sha256: str | None


@dataclass(frozen=True)
class InstallResult:
    target_dir: Path
    proxy_filename: str
    installed_files: list[str]
    conflict_backup_path: Path | None


# -- GitHub API -----------------------------------------------------------------

def _resolve_source(source_key: str) -> Source:
    try:
        return SOURCES[source_key]
    except KeyError:
        raise OptiScalerError(f"unknown OptiScaler source: {source_key!r}") from None


def _parse_release(data: dict, source_key: str) -> ReleaseInfo:
    assets = data.get("assets", [])
    archive_asset = next((a for a in assets if a["name"].lower().endswith((".7z", ".zip"))), None)
    if archive_asset is None:
        raise OptiScalerError(f"release {data.get('tag_name')} has no .7z/.zip asset")
    digest = archive_asset.get("digest") or ""
    sha256 = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
    return ReleaseInfo(
        source_key=source_key,
        tag=data["tag_name"],
        name=data.get("name") or data["tag_name"],
        published_at=data.get("published_at"),
        body=data.get("body") or "",
        asset_name=archive_asset["name"],
        asset_url=archive_asset["browser_download_url"],
        asset_size=archive_asset["size"],
        asset_sha256=sha256,
    )


def fetch_latest_release(source_key: str = DEFAULT_SOURCE_KEY) -> ReleaseInfo:
    source = _resolve_source(source_key)
    resp = requests.get(
        f"https://api.github.com/repos/{source.repo}/releases/latest", headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    return _parse_release(resp.json(), source_key)


def fetch_releases(source_key: str = DEFAULT_SOURCE_KEY, limit: int = 15) -> list[ReleaseInfo]:
    source = _resolve_source(source_key)
    resp = requests.get(
        f"https://api.github.com/repos/{source.repo}/releases",
        headers=HEADERS,
        params={"per_page": limit},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    out = []
    for entry in resp.json():
        if entry.get("draft"):
            continue
        try:
            out.append(_parse_release(entry, source_key))
        except OptiScalerError:
            continue
    return out


# -- download / extract -----------------------------------------------------------

def download_asset(release: ReleaseInfo) -> Path:
    """Cached by release tag; verifies against GitHub's published sha256 digest."""
    dest_dir = paths.DATA_DIR / "optiscaler_cache" / release.source_key / release.tag
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / release.asset_name

    if dest.is_file() and (release.asset_sha256 is None or sha256_file(dest) == release.asset_sha256):
        return dest

    tmp = dest.with_name(dest.name + ".part")
    with requests.get(release.asset_url, headers=HEADERS, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    if release.asset_sha256:
        actual = sha256_file(tmp)
        if actual != release.asset_sha256:
            tmp.unlink(missing_ok=True)
            raise OptiScalerError(
                f"downloaded file hash mismatch for {release.asset_name}: "
                f"expected {release.asset_sha256}, got {actual}"
            )
    tmp.replace(dest)
    return dest


def find_7z_binary() -> str | None:
    names = ["7z.exe", "7za.exe"] if sys.platform.startswith("win") else ["7z", "7za", "7zr"]
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    if sys.platform.startswith("win"):
        import os

        for env_var in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_var)
            if base and (Path(base) / "7-Zip" / "7z.exe").is_file():
                return str(Path(base) / "7-Zip" / "7z.exe")
    return None


def extract_archive(archive_path: Path, dest_dir: Path) -> None:
    binary = find_7z_binary()
    if binary is None:
        raise ExtractionError(
            "7-Zip не найден в PATH. Установите 7-Zip (Windows) или p7zip/7zip (Linux) "
            "и повторите попытку."
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [binary, "x", str(archive_path), f"-o{dest_dir}", "-y"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ExtractionError(f"7z extraction failed ({proc.returncode}): {proc.stderr or proc.stdout}")
    _fix_backslash_paths(dest_dir)


def _fix_backslash_paths(root: Path) -> None:
    """Some Windows-built archives (seen from at least one third-party
    OptiScaler fork) embed literal backslashes in zip entry names instead of
    proper '/' separators. 7z then extracts them as one oddly-named file
    sitting flat in the output dir rather than nested folders -- split those
    back into real subdirectories."""
    for p in list(root.rglob("*")):
        if p.is_file() and "\\" in p.name:
            dest = p.parent.joinpath(*p.name.split("\\"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            p.rename(dest)


# -- install target detection --------------------------------------------------

def suggest_install_targets(install_dir: Path) -> list[Path]:
    """Best-guess folders where the game's render process actually lives.
    OptiScaler's own installer doesn't auto-detect this reliably either
    (it just warns when it sees an Engine/ folder) -- treat these as
    suggestions for the user to confirm, not a silent choice."""
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        if not p.is_dir():
            return
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            candidates.append(rp)

    # Unreal Engine: <Project>/Binaries/Win64/*-Shipping.exe -- note this is
    # never the top-level Engine/ folder, that's just editor/tool binaries.
    for exe in install_dir.glob("*/Binaries/Win64/*-Shipping.exe"):
        if exe.relative_to(install_dir).parts[0].lower() != "engine":
            add(exe.parent)
    for exe in install_dir.glob("*/Binaries/Win64/*.exe"):
        if exe.relative_to(install_dir).parts[0].lower() != "engine":
            add(exe.parent)

    # Common non-UE layouts
    for exe in install_dir.glob("*.exe"):
        add(exe.parent)
    for exe in install_dir.glob("bin/*.exe"):
        add(exe.parent)
    for exe in install_dir.glob("bin/*/*.exe"):
        add(exe.parent)
    for exe in install_dir.glob("*/*.exe"):
        if "Binaries" not in exe.parts:
            add(exe.parent)

    add(install_dir)
    return candidates


# -- install / uninstall ---------------------------------------------------------

def _should_skip(rel: Path) -> bool:
    if len(rel.parts) != 1:
        return False
    return rel.name.lower() in _SKIP_FILENAMES or rel.name.startswith("!!")


def install_to(staging_dir: Path, target_dir: Path, proxy_filename: str, overwrite_conflict: bool = False) -> InstallResult:
    if proxy_filename not in PROXY_DLL_CHOICES:
        raise ValueError(f"unknown proxy filename: {proxy_filename!r}")

    optiscaler_dll = staging_dir / "OptiScaler.dll"
    if not optiscaler_dll.is_file():
        raise ExtractionError("OptiScaler.dll not found in the extracted archive -- unexpected release layout")

    target_dir.mkdir(parents=True, exist_ok=True)

    conflict_path = target_dir / proxy_filename
    conflict_backup_path: Path | None = None
    if conflict_path.exists():
        if not overwrite_conflict:
            raise FileExistsError(f"{conflict_path} already exists")
        backup_dir = paths.BACKUPS_DIR / "optiscaler_conflicts"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        conflict_backup_path = backup_dir / f"{stamp}__{proxy_filename}"
        shutil.copy2(conflict_path, conflict_backup_path)

    installed_files: list[str] = []
    for src in sorted(staging_dir.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(staging_dir)
        if _should_skip(rel):
            continue
        dest_rel = Path(proxy_filename) if rel == Path("OptiScaler.dll") else rel
        dest = target_dir / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        installed_files.append(str(dest_rel))

    return InstallResult(
        target_dir=target_dir,
        proxy_filename=proxy_filename,
        installed_files=installed_files,
        conflict_backup_path=conflict_backup_path,
    )


def uninstall_from(target_dir: Path, installed_files: list[str], conflict_backup_path: str | None) -> None:
    dirs_touched: set[Path] = set()
    for rel in installed_files:
        p = target_dir / rel
        p.unlink(missing_ok=True)
        if p.parent != target_dir:
            dirs_touched.add(p.parent)

    for d in sorted(dirs_touched, key=lambda p: -len(p.parts)):
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass

    if conflict_backup_path:
        backup = Path(conflict_backup_path)
        if backup.is_file():
            proxy_name = backup.name.split("__", 1)[1]
            shutil.copy2(backup, target_dir / proxy_name)
