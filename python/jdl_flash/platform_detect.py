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
import shutil
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


def _is_termux() -> bool:
    """True only when actually *running under* Termux — detected from its
    runtime environment, never from the mere existence of /data/data/com.termux.

    That directory belongs to the Android host, and a co-installed UserLAnd (a
    proot Ubuntu on the same device) can see it too — so testing for the path
    misdetects UserLAnd as Termux and then fails on `pkg: command not found`.
    Termux always exports TERMUX_VERSION and points PREFIX at its own usr dir,
    and its `pkg` wrapper lives under that prefix; UserLAnd has none of these.
    """
    if os.environ.get("TERMUX_VERSION"):
        return True
    if "com.termux" in os.environ.get("PREFIX", ""):
        return True
    pkg = shutil.which("pkg")
    return bool(pkg and "com.termux" in pkg)


def detect_platform() -> str:
    if sys.platform.startswith("win"):
        return WINDOWS

    if _is_termux():
        return TERMUX

    proc_version = _proc_version()
    # NB: /proc/version says "android" on BOTH real Termux and UserLAnd, so it
    # can't distinguish them — that's why the Termux check above keys off the
    # runtime env, and this "android" case is reached only when we're on an
    # Android device but NOT inside Termux, i.e. UserLAnd.
    if "userland" in proc_version or "android" in proc_version:
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
