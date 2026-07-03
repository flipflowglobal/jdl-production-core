"""
Build the Cython native binding to the jdl-hotpath Rust engine.

Prereq — build the Rust cdylib first:
    cd ../../rust/hotpath && cargo build --release

Then:
    cd python/jdl_native && python3 setup.py build_ext --inplace

This links against libjdl_hotpath.so in the Rust target/release dir and bakes an
rpath so the extension finds it at runtime. If this build isn't run (or fails, e.g.
on Termux/Android where the toolchain is awkward), `jdl_native` still works via its
ctypes / subprocess / pure-Python fallbacks — see __init__.py.
"""
import os
from setuptools import setup, Extension

HERE = os.path.dirname(os.path.abspath(__file__))
RUST_RELEASE = os.path.abspath(os.path.join(HERE, "..", "..", "rust", "hotpath", "target", "release"))

try:
    from Cython.Build import cythonize
except ImportError:
    raise SystemExit("Cython is required to build the native binding: pip install Cython")

ext = Extension(
    # Built in-place from within the package dir, so the module is top-level "_jdl"
    # and lands next to __init__.py (imported as `from . import _jdl`).
    name="_jdl",
    sources=["_jdl.pyx"],
    include_dirs=[HERE],                 # for jdl_hotpath.h
    library_dirs=[RUST_RELEASE],
    libraries=["jdl_hotpath"],           # links libjdl_hotpath.so
    runtime_library_dirs=[RUST_RELEASE], # rpath so the .so is found at import
)

setup(
    name="jdl_native",
    ext_modules=cythonize([ext], language_level=3),
    zip_safe=False,
)
