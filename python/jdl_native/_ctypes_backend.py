"""
ctypes backend — loads the Rust cdylib (libjdl_hotpath) directly, no compilation.

This is the most userland-portable *native* path: if the shared library exists
anywhere on the search path (or JDL_HOTPATH_LIB points at it), we call straight
into it without needing Cython or a C compiler on the target machine.
"""
import ctypes
import ctypes.util
import json
import os
from pathlib import Path

_LIB = None


def _candidate_paths():
    env = os.environ.get("JDL_HOTPATH_LIB")
    if env:
        yield env
    here = Path(__file__).resolve()
    # here = <repo>/python/jdl_native/_ctypes_backend.py → parents[2] is the repo root
    release = here.parents[2] / "rust" / "hotpath" / "target" / "release"
    for name in ("libjdl_hotpath.so", "libjdl_hotpath.dylib", "jdl_hotpath.dll"):
        yield str(release / name)
    found = ctypes.util.find_library("jdl_hotpath")
    if found:
        yield found


def _load():
    global _LIB
    if _LIB is not None:
        return _LIB
    for path in _candidate_paths():
        if path and os.path.exists(path):
            try:
                lib = ctypes.CDLL(path)
            except OSError:
                continue
            lib.jdl_scan.argtypes = [ctypes.c_char_p]
            lib.jdl_scan.restype = ctypes.c_void_p
            lib.jdl_analyze.argtypes = [ctypes.c_char_p]
            lib.jdl_analyze.restype = ctypes.c_void_p
            lib.jdl_string_free.argtypes = [ctypes.c_void_p]
            lib.jdl_string_free.restype = None
            _LIB = lib
            return lib
    raise OSError("libjdl_hotpath not found (build rust/hotpath, or set JDL_HOTPATH_LIB)")


def available() -> bool:
    try:
        _load()
        return True
    except OSError:
        return False


def _call(fn, payload: bytes) -> dict:
    ptr = fn(payload)
    if not ptr:
        return {"error": "native returned NULL"}
    try:
        return json.loads(ctypes.string_at(ptr).decode("utf-8"))
    finally:
        _load().jdl_string_free(ptr)


def scan(request: dict) -> dict:
    lib = _load()
    return _call(lib.jdl_scan, json.dumps(request).encode("utf-8"))


def analyze(bytecode: str) -> dict:
    lib = _load()
    return _call(lib.jdl_analyze, json.dumps({"bytecode": bytecode}).encode("utf-8"))


def backend():
    return "ctypes"
