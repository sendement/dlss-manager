# dlss-manager

Scans installed Steam games, finds their DLSS components (Super Resolution /
Frame Generation / Ray Reconstruction / Neural Rendering, a.k.a. DLSS 5),
collects the versions it finds into a local library and lets you swap them
between games — with a backup of the original and full rollback.

Works with regular Windows Steam and with Proton games on Linux (the
`steamapps/common/...` layout is the same).

## Installation

**Arch / CachyOS (package):**

```bash
sudo pacman -U dlss-manager-*.pkg.tar.zst   # from GitHub Releases
# or build it yourself:
cd packaging/arch && makepkg -si
```

This also installs a `.desktop` file with an icon, so the app shows up in the
menu as "DLSS Manager".

**From source (any platform, with uv):**

```bash
uv sync
```

## CLI

```bash
uv run dlss-manager scan                    # scan Steam libraries
uv run dlss-manager list                     # games + detected components
uv run dlss-manager library                  # DLL versions in the local library
uv run dlss-manager import path/to/nvngx_dlss.dll
uv run dlss-manager library-remove <library_id>
uv run dlss-manager apply <app_id> super_resolution <library_id>
uv run dlss-manager history                  # active changes
uv run dlss-manager rollback <change_id>     # roll back one change
uv run dlss-manager rollback-all             # roll back everything
uv run dlss-manager clean-logs [--apply]     # wrapper logs/files (dry run by default)
uv run dlss-manager clean-library            # remove junk from the local library

uv run dlss-manager optiscaler-sources                    # available sources (official + forks)
uv run dlss-manager optiscaler-releases [--source KEY]     # releases from GitHub
uv run dlss-manager optiscaler-targets <app_id>            # where it can be installed
uv run dlss-manager optiscaler-install <app_id> [--source KEY] [--target PATH] [--proxy dxgi.dll] [--version TAG]
uv run dlss-manager optiscaler-list                        # active installs
uv run dlss-manager optiscaler-check                       # compare with the latest release (per source)
uv run dlss-manager optiscaler-update <install_id>
uv run dlss-manager optiscaler-uninstall <install_id>

uv run dlss-manager dlssg-sm86-releases                   # sdli1995/dlssg_for_sm86 releases
uv run dlss-manager dlssg-sm86-targets <app_id>
uv run dlss-manager dlssg-sm86-install <app_id> [--target PATH] [--proxy version.dll] [--runtime 310.9] [--version TAG]
uv run dlss-manager dlssg-sm86-list
uv run dlss-manager dlssg-sm86-check
uv run dlss-manager dlssg-sm86-update <install_id>
uv run dlss-manager dlssg-sm86-uninstall <install_id>
```

### OptiScaler sources

- `official` (default) — [optiscaler/OptiScaler](https://github.com/optiscaler/OptiScaler).
- `dagherbou-dlssnr` — a fork of upstream that adds DLSS Neural Rendering
  (DLSS 5) on top of DLSS/FSR/XeSS, without MFG unlock.
- `klebermotta-mfg` — a fork of `dagherbou-dlssnr` that also adds MFG unlock
  (3x/4x/6x Frame Generation on RTX 40 by patching `nvngx_dlssg.dll` in
  memory). Don't use it in multiplayer — ban risk.
- `wilsjo2-nr` — another fork of `dagherbou-dlssnr` that supports Neural
  Rendering on **RTX 20/30/40/50** (not only RTX 50) via a "pre-SR multipass"
  technique. Every release ships with its own `.sha256`. No MFG unlock.

All three forks need `nvngx_dlssnr.dll` (~165 MB). It is not in their archives
and their authors state they won't distribute it (it's an NVIDIA file). **This
app never downloads it** — take it from a game with official DLSS 5 support
(e.g. NBA 2K27), or add it yourself from a source you trust. If the file is
already in the local library (imported by hand or found by scanning another
game), installing any of the three forks copies it into the game folder — but
only if the game doesn't already have its own (games with native DLSS 5
support are left alone).

### dlssg_for_sm86 (DLSS Frame Generation on RTX 20/30)

[sdli1995/dlssg_for_sm86](https://github.com/sdli1995/dlssg_for_sm86) is a
separate tool, not an OptiScaler fork. It unlocks DLSS-G on RTX 20/30
(SM75/SM86) through a proxy DLL around NVIDIA's unmodified runtime. Its
release DLLs are signed (self-signed; the fingerprint is in the project's
README), but GitHub publishes no hash for the downloaded archive. Don't use it
in multiplayer — ban risk.

It installs the same way as OptiScaler (proxy DLL + ini next to the game's
executable), with two runtimes to choose from (310.9 — up to 6X, 310.1 — up to
4X) and its own set of proxy names (`version.dll` by default, or
d3d12/dbghelp/dinput8/dxgi/winmm).

## GUI

```bash
uv run dlss-manager gui
```

The "Library" tab lists every DLL in the library (scanned or imported) with
its size, hash and source, and lets you remove them. You can add your own
files three ways: drag a `.dll` onto the window (works on any tab), use the
"Import DLL..." button (on the "Library" tab or in the toolbar; both accept
several files at once), or use "File → Import DLL...".

## How it works

- `steam.py` — parses `libraryfolders.vdf` and `appmanifest_*.acf` and finds
  installed games on every Steam drive.
- `scanner.py` / `pe_version.py` — find `nvngx_dlss*.dll` by file name and read
  the version straight from the PE resources (without loading the library, so
  it works on Linux too).
- `library.py` — local DLL storage, deduplicated by sha256.
- `manager.py` — scanning, applying a version (with a backup), rollback (one
  change or all at once), cleaning up logs of known DLSS wrappers (Streamline,
  DLSSTweaks, OptiScaler, Special K) and junk in the library.
- Nothing is downloaded for DLSS itself: versions get into the library either
  by scanning installed games or by importing a file by hand.
- `optiscaler.py` — downloads [OptiScaler](https://github.com/optiscaler/OptiScaler)
  releases straight from GitHub Releases (verified against the sha256 from their
  API), unpacks the `.7z` with the system `7z`/`7za` (py7zr can't handle the
  BCJ2 filter their archives use) and installs it the same way their
  `setup_windows.bat`/`setup_linux.sh` do (renaming `OptiScaler.dll` to the
  chosen proxy DLL). Installs/updates/removals are tracked in their own DB
  table — our own manifest instead of their bash uninstaller, so they fit into
  the app's change history.
- `dlssg_sm86.py` — the same logic for dlssg_for_sm86, but it downloads the
  auto-generated GitHub source archive for a tag rather than a release asset
  (the project's releases have no attachments — the files live in the repo
  tree), so it unpacks with `tarfile` instead of the system `7z`.
