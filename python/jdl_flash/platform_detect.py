"""
platform_detect.py — one function, one answer: what OS/shell is `jdl install`
running under, so it can pick the right dependency installer (setup.sh's
Termux/UserLAnd/Ubuntu paths, or scripts/setup.ps1 for native Windows).

Order matters: Termux and UserLAnd both report as "Linux" via os.uname()/
sys.platform, so their marker checks must run before the generic Linux
fallback. WSL also reports as Linux but has a real bash, so it's routed to
setup.sh like any other POSIX box rather than getting its own script.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

TERMUX = "termux"
USERLAND = "userland"
WSL = "wsl"
WINDOWS = "windows"
MACOS = "macos"
LINUX = "linux"

# Platforms whose dependency install is driven by setup.sh (a real POSIX shell
# is available). WINDOWS is the only one routed to scripts/setup.ps1 instead.
POSIX_PLATFORMS = {TERMUX, USERLAND, WSL, MACOS, LINUX}


def _proc_version() -> str:
    try:
        return Path("/proc/version").read_text(errors="ignore").lower()
    except OSError:
        return ""


def detect_platform() -> str:
    if sys.platform.startswith("win"):
        return WINDOWS

    if os.environ.get("TERMUX_VERSION") or Path("/data/data/com.termux").exists():
        return TERMUX

    proc_version = _proc_version()
    if "userland" in proc_version:
        return USERLAND

    if "microsoft" in proc_version:
        return WSL

    if sys.platform == "darwin":
        return MACOS

    return LINUX


def is_posix_installer(platform: str | None = None) -> bool:
    """Whether `platform` should be driven by setup.sh (True) or
    scripts/setup.ps1 (False)."""
    return (platform or detect_platform()) in POSIX_PLATFORMS
