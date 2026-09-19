"""Fonts the corpora render with.

Two sets. The platform set is whatever the machine has installed, which is
what a real sign looks like on that machine's photos but differs between a
Mac and a Windows laptop, so numbers from the two do not compare. The bundled
set is fifteen open-licensed display faces committed under `eval/fonts/`
(licences alongside), identical everywhere, and deliberately awkward: script,
brush, marker, stencil, engraved. They are what packaging and shopfronts
actually use, and markedly harder than Helvetica.

    from fonts import bundled_fonts, platform_fonts, font_set
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent / "fonts"

_MAC_FONTS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Avenir.ttc",
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/System/Library/Fonts/Supplemental/Gill Sans.ttc",
    "/System/Library/Fonts/Supplemental/Optima.ttc",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/System/Library/Fonts/Supplemental/Verdana.ttf",
    "/System/Library/Fonts/Supplemental/Tahoma.ttf",
    "/System/Library/Fonts/Supplemental/Trebuchet MS.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/System/Library/Fonts/Supplemental/American Typewriter.ttc",
    "/System/Library/Fonts/Supplemental/Palatino.ttc",
    "/System/Library/Fonts/Supplemental/Rockwell.ttc",
]
_WINDOWS_FONTS = [
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/verdana.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/trebuc.ttf",
    "C:/Windows/Fonts/georgia.ttf",
    "C:/Windows/Fonts/times.ttf",
    "C:/Windows/Fonts/cour.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/bahnschrift.ttf",
    "C:/Windows/Fonts/candara.ttf",
    "C:/Windows/Fonts/constan.ttf",
    "C:/Windows/Fonts/pala.ttf",
]
_LINUX_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]


def bundled_fonts() -> list[str]:
    """The committed display faces, identical on every machine."""
    return sorted(str(p) for p in FONT_DIR.glob("*.ttf"))


def platform_fonts() -> list[str]:
    """System fonts present on this machine; the bundled set when none are."""
    candidates = (
        _MAC_FONTS if sys.platform == "darwin"
        else _WINDOWS_FONTS if sys.platform == "win32"
        else _LINUX_FONTS
    )
    present = [f for f in candidates if os.path.exists(f)]
    return present or bundled_fonts()


def font_set(name: str) -> list[str]:
    """`platform` (default), `bundled`, or `all`."""
    if name == "bundled":
        return bundled_fonts()
    if name == "all":
        return platform_fonts() + bundled_fonts()
    return platform_fonts()
