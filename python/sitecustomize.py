"""
sitecustomize.py — the durable half of the live-database tripwire.

``jdl_flash.cli._run_test_suites`` runs every suite as a fresh subprocess and
hands each one ``PYTHONPATH`` pointing at this directory plus the
``JDL_TEST_DB_GUARD=1`` marker. CPython imports ``sitecustomize`` from the
first directory on ``sys.path`` that contains it during interpreter startup —
before the script body, and before any module that could open
``~/.flash_loan_engine/flash.db`` — so this file is how the guard gets armed
for a suite that never imports the guard itself.

Deliberate limits
-----------------

* **Test-only by construction.** The marker env var is the only trigger. A
  production daemon (``flashloan``, ``jdl``, ``python -m jdl_flash.*``) never
  has ``JDL_TEST_DB_GUARD`` set, so this file is a strict no-op for it — no
  tripwire, no ``jdl_flash`` import, nothing on ``sys.modules``. Arming
  production would read the revenue ledger through a blocked
  ``sqlite3.connect`` and silently destroy the daemon's revenue, so the marker
  gate is what keeps that impossible.

* **Never writes to stderr.** A ``sitecustomize`` that prints a warning makes
  every interpreter started with this directory importable — including the
  probe subprocess in ``test_flash_supervisor.py``, which asserts a clean
  production import. Any failure to arm is swallowed here; a broken guard is
  surfaced loudly by its own suite, ``jdl_flash/test_db_guard.py``, which
  ``jdl test`` runs in the same list.

* **Complements, not replaces, the explicit import.** Suites under
  ``jdl_flash/`` still import the guard directly (that covers direct
  ``python3 jdl_flash/test_x.py`` runs, which never see this file). This hook
  covers what those explicit imports cannot: the subprocess model of
  ``jdl test`` and any future suite that forgets to import the guard.

``python/conftest.py`` arms the same tripwire for ``pytest``-driven runs.
"""
import os

_JDL_TEST_DB_GUARD_MARKER = "JDL_TEST_DB_GUARD"


if os.environ.get(_JDL_TEST_DB_GUARD_MARKER) == "1":
    # Importing the guard installs the process-wide sqlite3.connect tripwire as
    # a module side effect (see jdl_flash.test_db_guard.install_test_db_guard).
    try:
        import jdl_flash.test_db_guard  # noqa: F401  (import arms the tripwire)
    except Exception:
        # Start-up must never be made conditional on the guard's internal
        # health: a broken guard is reported by test_db_guard.py itself, and
        # swallowing here keeps unrelated suites (and, importantly, any
        # production launch that somehow inherits the marker) runnable.
        pass