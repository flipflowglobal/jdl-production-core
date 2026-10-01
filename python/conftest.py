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

The `check` fixture below is the second half of making `pytest` a first-class
runner for these suites. They were written for `jdl test`, which executes each
file as a script and injects a `check(cond, msg, detail)` helper from the
file's own `main()`. Under pytest that helper does not exist, so every suite
written in that style reported ERROR ('fixture 'check' not found') — the
assertions were never even reached. Providing `check` as a fixture means one
definition serves both runners.
"""

try:
    import jdl_flash.test_db_guard  # noqa: F401  (import arms the tripwire)
except Exception:
    pass

import pytest


@pytest.fixture
def check():
    """Assertion helper matching the signature suites use under `jdl test`.

    Suites call ``check(condition, message, detail)`` and rely on the run
    continuing past a failure so every assertion is reported, not just the
    first. The fixture therefore accumulates failures and raises once at
    teardown, listing all of them — same semantics as the harness in each
    suite's ``main()``.
    """
    failures = []

    def _check(condition, message, detail=""):
        if condition:
            print(f"  ✓ {message}")
        else:
            failures.append((message, detail))
            print(f"  ✗ {message}" + (f"  ({detail})" if detail else ""))
        return bool(condition)

    yield _check

    if failures:
        listed = "\n".join(
            f"    - {msg}" + (f"  ({detail})" if detail else "")
            for msg, detail in failures
        )
        raise AssertionError(
            f"{len(failures)} assertion(s) failed:\n{listed}"
        )