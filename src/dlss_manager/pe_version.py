"""Read the FileVersion out of a PE (.dll) file's VERSIONINFO resource.

This works on any platform pefile supports (it just parses bytes), which
matters since this app also has to work against Proton game installs on
Linux where the .dll is a plain file, not something loadable by the OS.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pefile


@dataclass(frozen=True)
class PeVersionInfo:
    file_version: str | None
    product_version: str | None

    @property
    def display_version(self) -> str | None:
        """Nvidia embeds these with commas (e.g. '310,5,3,0'); normalize for display/sorting."""
        v = self.file_version or self.product_version
        return v.replace(",", ".").replace(" ", "") if v else None


def version_sort_key(version: str) -> tuple[int, ...]:
    parts = []
    for p in version.replace(",", ".").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _numeric_version(ms: int, ls: int) -> str:
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def read_pe_version(path: Path) -> PeVersionInfo | None:
    """Best-effort extraction. Returns None if the file isn't a readable PE."""
    try:
        pe = pefile.PE(str(path), fast_load=True)
    except pefile.PEFormatError:
        return None
    try:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )

        file_version: str | None = None
        product_version: str | None = None

        for fileinfo in getattr(pe, "FileInfo", []):
            for entry in fileinfo:
                if entry.Key == b"StringFileInfo":
                    for st in entry.StringTable:
                        for key, value in st.entries.items():
                            if key == b"FileVersion" and value:
                                file_version = value.decode("utf-8", "replace").strip()
                            elif key == b"ProductVersion" and value:
                                product_version = value.decode("utf-8", "replace").strip()

        if file_version is None and getattr(pe, "VS_FIXEDFILEINFO", None):
            ffi = pe.VS_FIXEDFILEINFO[0]
            file_version = _numeric_version(ffi.FileVersionMS, ffi.FileVersionLS)
        if product_version is None and getattr(pe, "VS_FIXEDFILEINFO", None):
            ffi = pe.VS_FIXEDFILEINFO[0]
            product_version = _numeric_version(ffi.ProductVersionMS, ffi.ProductVersionLS)

        if file_version is None and product_version is None:
            return None
        return PeVersionInfo(file_version=file_version, product_version=product_version)
    finally:
        pe.close()
