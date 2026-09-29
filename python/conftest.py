"""
conftest.py — arm the live-database tripwire for pytest-driven runs.

`jdl test` starts each suite as its own interpreter and arms the guard through
python/sitecustomize.py (see that file for the marker/env design). This file
covers the other way the suites get executed: `pytest` run against python/,
which imports conftest.py once per session. Importing the guard installs the
process-wide `sqlite3.connect` tripwire as a module side effect, so every
collected test is protected without any individual test opting in.

Failure to arm is swallowed here but surfaced loudly by the guard's own suite,
jdl_flash/test_db_guard.py, and production processes never import conftest.py
(pytest-only), so this cannot affect the daemon.
"""

try:
    import jdl_flash.test_db_guard  # noqa: F401  (import arms the tripwire)
except Exception:
    pass