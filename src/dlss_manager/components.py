"""Known DLSS suite components we know how to detect and swap.

Nvidia ships DLSS as a small family of NGX plugin DLLs, each dropped next to
the game executable (or inside an engine's ThirdParty binaries folder). We
key everything off the filename since that's the one stable identifier
across engines and versions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Component:
    key: str
    filename: str
    display_name: str


COMPONENTS: list[Component] = [
    Component("super_resolution", "nvngx_dlss.dll", "DLSS Super Resolution"),
    Component("frame_generation", "nvngx_dlssg.dll", "DLSS Frame Generation"),
    Component("ray_reconstruction", "nvngx_dlssd.dll", "DLSS Ray Reconstruction"),
]

FILENAME_TO_COMPONENT: dict[str, Component] = {c.filename.lower(): c for c in COMPONENTS}
KEY_TO_COMPONENT: dict[str, Component] = {c.key: c for c in COMPONENTS}


def component_for_filename(filename: str) -> Component | None:
    return FILENAME_TO_COMPONENT.get(filename.lower())
