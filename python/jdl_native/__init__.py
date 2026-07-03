"""
jdl_native — Python access to the jdl-hotpath Rust engine, with graceful,
userland-friendly fallback so the SAME API works on a Linux server and on
Termux/Android.

Backend selection (best available wins, override with JDL_NATIVE_BACKEND):
  1. cython    — compiled extension (fastest; needs setup.py build_ext)
  2. ctypes    — loads the Rust cdylib directly (native, no compile step)
  3. subprocess— spawns the jdl-hotpath CLI binary (portable if the binary exists)
  4. python    — pure-Python port of the arb scan (works everywhere; no EVM analysis)

Public API:
  scan(request: dict) -> dict          # arbitrage hot-path (ScanRequest -> ScanResult)
  analyze(bytecode: str) -> dict       # EVM bytecode analysis (needs a native backend)
  active_backend() -> str              # which backend is in use
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

__all__ = ["scan", "analyze", "active_backend", "AnalysisUnavailable"]


class AnalysisUnavailable(RuntimeError):
    """Raised when analyze() has no native backend and no CLI binary."""


# ── subprocess backend (CLI binary) ─────────────────────────────────────────
def _cli_path():
    env = os.environ.get("JDL_HOTPATH_BIN")
    if env and os.path.exists(env):
        return env
    here = Path(__file__).resolve()
    cand = here.parents[2] / "rust" / "hotpath" / "target" / "release" / "jdl-hotpath"
    if cand.exists():
        return str(cand)
    return shutil.which("jdl-hotpath")


def _cli_available():
    return _cli_path() is not None


def _cli_run(args, payload: dict) -> dict:
    proc = subprocess.run(
        [_cli_path(), *args],
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    )
    return json.loads(proc.stdout.decode("utf-8"))


# ── backend resolution ───────────────────────────────────────────────────────
def _resolve():
    forced = os.environ.get("JDL_NATIVE_BACKEND")
    order = [forced] if forced else ["cython", "ctypes", "subprocess", "python"]
    for name in order:
        if name == "cython":
            try:
                from . import _jdl  # compiled extension
                return "cython", _jdl.scan, _jdl.analyze
            except Exception:
                continue
        if name == "ctypes":
            from . import _ctypes_backend as cb
            if cb.available():
                return "ctypes", cb.scan, cb.analyze
        if name == "subprocess":
            if _cli_available():
                return (
                    "subprocess",
                    lambda req: _cli_run([], req),
                    lambda bc: _cli_run(["analyze"], {"bytecode": bc}),
                )
        if name == "python":
            from . import _pyfallback as pf
            return "python", pf.scan, pf.analyze
    # last resort
    from . import _pyfallback as pf
    return "python", pf.scan, pf.analyze


_BACKEND, _scan_impl, _analyze_impl = _resolve()


def active_backend() -> str:
    """Name of the backend in use: cython | ctypes | subprocess | python."""
    return _BACKEND


def scan(request: dict) -> dict:
    """Run the arbitrage hot-path. `request` is a ScanRequest dict."""
    return _scan_impl(request)


def analyze(bytecode: str) -> dict:
    """Analyze contract bytecode (hex). Needs a native backend or the CLI binary."""
    try:
        return _analyze_impl(bytecode)
    except NotImplementedError as e:
        raise AnalysisUnavailable(str(e)) from e
