"""
Tests for platform_detect.py — pure function over monkeypatched os/sys/Path
state, no real platform probing (so this passes identically on every CI
runner regardless of what it actually is).

Run: cd python && python3 jdl_flash/test_platform_detect.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jdl_flash.platform_detect as pd


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✓ {msg}")
        else:
            failed += 1
            print(f"  ✗ {msg}")

    real_platform = sys.platform
    real_environ_termux = os.environ.pop("TERMUX_VERSION", None)
    real_exists = pd.Path.exists
    real_proc_version = pd._proc_version

    try:
        # ── Windows: sys.platform alone decides it, before any other check ──
        sys.platform = "win32"
        check(pd.detect_platform() == pd.WINDOWS, "win32 -> windows")
        check(not pd.is_posix_installer(), "windows is not a POSIX installer target")

        sys.platform = "linux"

        # ── Termux: env var marker ──
        os.environ["TERMUX_VERSION"] = "0.118"
        pd.Path.exists = lambda self: False
        check(pd.detect_platform() == pd.TERMUX, "TERMUX_VERSION env var -> termux")
        os.environ.pop("TERMUX_VERSION")

        # ── Termux: filesystem marker (no env var set) ──
        pd.Path.exists = lambda self: str(self) == "/data/data/com.termux"
        check(pd.detect_platform() == pd.TERMUX, "/data/data/com.termux exists -> termux")
        pd.Path.exists = lambda self: False

        # ── UserLAnd: /proc/version marker ──
        pd._proc_version = lambda: "linux version ... userland ..."
        check(pd.detect_platform() == pd.USERLAND, "'userland' in /proc/version -> userland")

        # ── WSL: /proc/version marker ──
        pd._proc_version = lambda: "linux version ... microsoft-standard-wsl2 ..."
        check(pd.detect_platform() == pd.WSL, "'microsoft' in /proc/version -> wsl")

        # ── plain Linux: no markers at all ──
        pd._proc_version = lambda: "linux version 6.1.0-generic ..."
        check(pd.detect_platform() == pd.LINUX, "no special markers -> plain linux")
        check(pd.is_posix_installer(), "linux is a POSIX installer target")

        # ── macOS ──
        sys.platform = "darwin"
        check(pd.detect_platform() == pd.MACOS, "darwin -> macos")
        check(pd.is_posix_installer("macos"), "explicit platform arg bypasses detection")
    finally:
        sys.platform = real_platform
        pd.Path.exists = real_exists
        pd._proc_version = real_proc_version
        if real_environ_termux is not None:
            os.environ["TERMUX_VERSION"] = real_environ_termux

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
