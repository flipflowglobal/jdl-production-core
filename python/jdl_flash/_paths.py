"""
_paths.py — repo-path/module-loading helpers shared by cli.py and
integrate.py. Split out so the two can both load flash_supervisor.py (which
lives at python/ root, deliberately unpackaged — see pyproject.toml) without
importing each other.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def python_dir() -> Path:
    """The repo's python/ directory (this package's parent)."""
    import jdl_flash

    return Path(jdl_flash.__file__).resolve().parent.parent


def load_flash_supervisor() -> ModuleType:
    path = python_dir() / "flash_supervisor.py"
    spec = importlib.util.spec_from_file_location("flash_supervisor", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load flash_supervisor.py from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
