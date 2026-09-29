"""
test_db_guard.py — the durable fix for B8: tests must never touch the live
revenue database.

The bug this exists to prevent
------------------------------
``flash_loan_engine.py`` derives its database location from ``Path.home()``::

    DATA_DIR = Path.home() / '.flash_loan_engine'
    DB_PATH  = DATA_DIR / 'flash.db'

and ``test_flash_engine.py`` called ``init_db()`` and then
``RevenueTracker.log('TEST','UNIT','0xTEST',1000.0,2.5,0.1,'0xhash123',1)``
with ``success=1``. With no override in between, that row landed in the *live*
revenue database at ``~/.flash_loan_engine/flash.db``.

That is not a harmless scratch row. ``flash_supervisor.total_profit()`` runs
``SELECT COALESCE(SUM(net_usd),0) FROM executions WHERE success=1`` over that
same table, and the supervisor arms a real ``withdrawToken()`` once the total
crosses $1000. Every ``jdl test`` therefore inflated *recorded real revenue* and
could trigger a premature on-chain withdrawal of real money.

The cleanup could not save it either: ``init_db()`` only purges rows whose
``tx_hash`` matches ``'sim_%'`` or ``'dry_%'``. ``'0xhash123'`` matches neither,
so the row survived every subsequent run and accumulated.

What this module provides
-------------------------
1. **Injectable path resolution.** :func:`resolve_db_path` honours the
   ``FLASH_DB_PATH`` / ``JDL_FLASH_DB_PATH`` / ``FLASH_LOAN_DB_PATH`` env
   aliases, read through :func:`jdl_flash.config.env_str` — the same
   first-non-blank-alias reader the engine already uses for every other setting.
   With no env var set it returns the production default unchanged, so wiring
   this in cannot alter production behaviour.

   The implementation now lives in :mod:`jdl_flash.paths`, which is production
   code. It used to live here, which meant ``flash_supervisor.py`` had to import
   this *test* module to resolve its own database — putting a test module and its
   ``sqlite3.connect`` tripwire into the production wheel, and forcing an
   arm-then-disarm cycle of a global monkeypatch on every daemon start. This
   module now imports the resolver from production and re-exports it, so the
   names below keep working for existing callers.

2. **A process-wide tripwire.** :func:`install_test_db_guard` wraps
   ``sqlite3.connect`` so that *any* code in the process which tries to open the
   live database raises :class:`LiveDatabaseError`. This is the durable part: it
   does not depend on a test remembering to request a fixture. A test written
   next year that forgets the fixture fails loudly instead of silently
   corrupting the revenue ledger. It is installed when this module is imported,
   so it is active for the whole suite rather than opt-in per test.

3. **A real isolated database.** :func:`isolated_database` hands out a private
   temporary directory, rebinds the engine's ``DATA_DIR`` / ``DB_PATH`` module
   globals to point at it, and removes the whole directory on exit — so no
   fixture-created row can survive even inside a temporary database.

Why module globals
------------------
``init_db()``, ``db_exec()`` and ``db_query()`` all read ``DB_PATH`` as a
module global *at call time* (they do not capture it in a default argument), so
reassigning ``flash_loan_engine.DB_PATH`` is a real, supported seam — no change
to the engine is needed to use this module. See ``HANDOFF`` in the report for
the one-line engine-side change that would make the env var authoritative
outside tests as well.

Pure stdlib plus :mod:`jdl_flash.config` and :mod:`jdl_flash.paths` (both
themselves pure stdlib): importable on an interpreter with no third-party
packages, and importable *before* the engine, which matters because the guard has
to be in place before anything can connect.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
import sys
import tempfile
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

# Path bootstrap. Every other suite in jdl_flash/ starts with this; without it a
# bare `python3 jdl_flash/test_db_guard.py` (which is how `jdl test` runs every
# suite, and how CI runs it) puts jdl_flash/ — not its parent — on sys.path and
# dies with ModuleNotFoundError before a single assertion runs. This module was
# the one suite missing it, which is part of why it was dead code: it could not
# be run the documented way at all.
_PYTHON_DIR = str(Path(__file__).resolve().parent.parent)
if _PYTHON_DIR not in sys.path:
    sys.path.insert(0, _PYTHON_DIR)

# Path resolution is production code (jdl_flash.paths); re-exported here so the
# names this module has always exposed keep working for existing callers.
from jdl_flash.paths import (  # noqa: E402
    DATA_DIR_NAME,
    DB_FILE_NAME,
    DB_PATH_ENV_ALIASES,
    LiveDatabaseError,
    account_home,
    assert_not_live_db,
    configured_db_path,
    default_data_dir,
    default_db_path,
    is_live_db,
    normalise as _normalise,
    production_db_paths,
    resolve_db_path,
    resolve_engine_data_dir,
)

__all__ = [
    "DATA_DIR_NAME",
    "DB_FILE_NAME",
    "DB_PATH_ENV_ALIASES",
    "IsolatedDatabase",
    "LiveDatabaseError",
    "TestDbGuard",
    "assert_engine_isolated",
    "assert_not_live_db",
    "configured_db_path",
    "default_data_dir",
    "default_db_path",
    "describe_environment",
    "guard_is_installed",
    "import_engine_or_none",
    "install_test_db_guard",
    "is_live_db",
    "isolated_database",
    "production_db_paths",
    "purge_execution_rows",
    "resolve_db_path",
    "resolve_engine_data_dir",
    "uninstall_test_db_guard",
]

#: Backwards-compatible alias. The implementation is now in
#: jdl_flash.paths.account_home; the private name is kept because the guard's
#: own helpers and its tests refer to it.
_account_home = account_home

_MEMORY = ":memory:"


# ---------------------------------------------------------------------------
#  Production path resolution
# ---------------------------------------------------------------------------
#
#  Re-exported from jdl_flash.paths (production code) rather than defined here.
#  flash_supervisor.py needs exactly this resolution and must not have to import
#  a test module to get it: doing so shipped the tripwire into the production
#  wheel and forced an arm-then-disarm of a global sqlite3.connect monkeypatch on
#  every daemon start -- the fragility behind the "$0.00 revenue forever" bug.
#  Importing the names here keeps every existing `from jdl_flash.test_db_guard
#  import resolve_db_path` working, so the dependency direction is now
#  test -> production, which is the only safe direction for it to run in.



# ────────────────────────────────────────────────────────────────────────────
#  The process-wide tripwire
# ────────────────────────────────────────────────────────────────────────────

def _database_argument_to_path(database: Any) -> Optional[Path]:
    """Best-effort filesystem path for a ``sqlite3.connect`` first argument.

    Returns ``None`` for anything that is not a filesystem database — ``None``,
    ``":memory:"``, an empty string (SQLite's own temporary database), a file
    descriptor, or a value that cannot be interpreted as a path. Those are all
    legitimate and none of them can reach the live ledger.
    """
    if database is None or isinstance(database, int):
        return None
    if isinstance(database, bytes):
        try:
            database = database.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(database, (str, os.PathLike)):
        return None
    try:
        raw = os.fspath(database)
    except TypeError:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not raw or raw == _MEMORY:
        return None
    if raw.startswith("file:"):
        # A SQLite URI, "file:/path/to.db?mode=ro". Only the leading path
        # component can name the live ledger, so drop the query and the scheme.
        raw = raw[len("file:"):].split("?", 1)[0].split("#", 1)[0]
        if not raw:
            return None
    if raw.startswith("\x00"):
        # SQLite's private temporary database; never a real path.
        return None
    try:
        return _normalise(raw)
    except (TypeError, ValueError, OSError):
        return None


class TestDbGuard:
    """A process-wide tripwire that refuses to open the live revenue database.

    Installed by :func:`install_test_db_guard` and active for every subsequent
    ``sqlite3.connect`` in the interpreter, no matter which module calls it.
    That is the property that makes the fix durable: it does not rely on any
    individual test having asked for an isolated database.
    """

    def __init__(self, real_connect: Any) -> None:
        self._real_connect = real_connect
        self.armed = True
        self.blocked: List[str] = []
        # Bound once and stored on the instance: a bound method is a fresh
        # object on every attribute access, so identity checks (which decide
        # whether the guard is installed) would never match otherwise.
        self._connect: Any = self._connect_guarded

    def _connect_guarded(self, database: Any = _MEMORY, *args: Any, **kwargs: Any) -> Any:
        """``sqlite3.connect`` with the live database rejected."""
        if self.armed:
            candidate = _database_argument_to_path(database)
            if candidate is not None and is_live_db(candidate):
                self.blocked.append(str(candidate))
                assert_not_live_db(candidate, context="sqlite3.connect")
        return self._real_connect(database, *args, **kwargs)

    def disarm(self) -> None:
        """Stop intercepting. Only for this guard's own tests."""
        self.armed = False

    def arm(self) -> None:
        """Resume intercepting. Only for this guard's own tests."""
        self.armed = True

    def install(self) -> None:
        """Make this guard the process's ``sqlite3.connect``."""
        sqlite3.connect = self._connect  # type: ignore[assignment]

    def uninstall(self) -> None:
        """Restore the interpreter's original ``sqlite3.connect``."""
        if sqlite3.connect is self._connect:  # type: ignore[comparison-overlap]
            sqlite3.connect = self._real_connect  # type: ignore[assignment]
        self.armed = False


_GUARD: Optional[TestDbGuard] = None


def guard_is_installed() -> bool:
    """True when the process-wide tripwire is active."""
    return _GUARD is not None and _GUARD.armed and sqlite3.connect is _GUARD._connect


def install_test_db_guard() -> TestDbGuard:
    """Install the process-wide tripwire and validate the current environment.

    Idempotent, and called automatically when this module is imported so the
    guard covers the whole suite without any test opting in.

    Also fails immediately if the environment is already configured to point at
    the live database, so the operator sees the problem at import time rather
    than at the first write.
    """
    global _GUARD
    if _GUARD is not None:
        return _GUARD

    override = configured_db_path()
    if override is not None:
        assert_not_live_db(override, context="test database configuration")

    guard = TestDbGuard(real_connect=sqlite3.connect)
    guard.install()
    _GUARD = guard
    return guard


def uninstall_test_db_guard() -> None:
    """Remove the process-wide tripwire and restore ``sqlite3.connect``."""
    global _GUARD
    if _GUARD is not None:
        _GUARD.uninstall()
    _GUARD = None


# Installed on import: the guard is active for the entire suite, and in place
# before any test module has had the chance to import the engine.
install_test_db_guard()


# ────────────────────────────────────────────────────────────────────────────
#  Isolated databases
# ────────────────────────────────────────────────────────────────────────────

def purge_execution_rows(
    db_path: "os.PathLike[str] | str",
    tx_hashes: Optional[Iterable[str]] = None,
) -> int:
    """Delete revenue rows from ``db_path`` and return how many were removed.

    ``init_db()``'s own cleanup only matches ``'sim_%'`` and ``'dry_%'``, which
    is why a fixture row such as ``'0xhash123'`` outlived every subsequent run.
    This takes the exact hashes a fixture wrote, so a test can clean up after
    itself precisely instead of relying on a naming convention it does not
    control.

    ``None`` for ``tx_hashes`` removes every row, which is what the
    isolated-database teardown wants for a database it owns outright.
    """
    path = assert_not_live_db(db_path, context="purge_execution_rows")
    if not path.exists():
        return 0
    con = sqlite3.connect(path)
    try:
        if tx_hashes is None:
            cursor = con.execute("DELETE FROM executions")
        elif isinstance(tx_hashes, str):
            cursor = con.execute("DELETE FROM executions WHERE tx_hash = ?", (tx_hashes,))
        else:
            hashes = list(tx_hashes)
            if not hashes:
                return 0
            placeholders = ",".join("?" * len(hashes))
            cursor = con.execute(
                f"DELETE FROM executions WHERE tx_hash IN ({placeholders})", tuple(hashes)
            )
        con.commit()
        return int(cursor.rowcount or 0)
    finally:
        con.close()


class IsolatedDatabase:
    """A private temporary database, with the engine pointed at it.

    The engine's ``DATA_DIR`` and ``DB_PATH`` are module globals read at call
    time, so pointing them here is enough to redirect every write the suite
    performs — ``init_db()``, ``db_exec()``, ``db_query()`` and
    ``RevenueTracker.log()`` all follow. The originals are restored on close
    and the whole directory is deleted, so no fixture row survives.
    """

    def __init__(self, prefix: str = "jdl_test_db_") -> None:
        self.root = Path(tempfile.mkdtemp(prefix=prefix))
        self.data_dir = self.root / DATA_DIR_NAME
        self.db_path = self.data_dir / DB_FILE_NAME
        assert_not_live_db(self.db_path, context="isolated database creation")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._originals: Dict[int, Tuple[types.ModuleType, Dict[str, Any]]] = {}
        self.closed = False

    def apply_to(
        self,
        module: types.ModuleType,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> "IsolatedDatabase":
        """Point ``module``'s path globals at this database.

        ``DATA_DIR`` and ``DB_PATH`` are always redirected. ``extra`` maps
        further attribute names to values for modules that keep other writable
        paths alongside the database — ``flash_supervisor`` also holds
        ``PID_FILE`` and ``LOG_FILE``, which a stray daemon start would
        otherwise write into the real home directory.
        """
        assignments: Dict[str, Any] = {"DATA_DIR": self.data_dir, "DB_PATH": self.db_path}
        if extra:
            assignments.update(extra)
        previous: Dict[str, Any] = {}
        for attribute, value in assignments.items():
            if hasattr(module, attribute):
                previous[attribute] = getattr(module, attribute)
            setattr(module, attribute, value)
        self._originals[id(module)] = (module, previous)
        return self

    def restore(self) -> None:
        """Put every module this database was applied to back as it was."""
        while self._originals:
            _, (module, previous) = self._originals.popitem()
            for attribute, value in previous.items():
                setattr(module, attribute, value)
            for attribute in ("DATA_DIR", "DB_PATH"):
                if attribute not in previous and hasattr(module, attribute):
                    delattr(module, attribute)

    def close(self) -> None:
        """Restore the modules and delete the database. Idempotent."""
        if self.closed:
            return
        self.closed = True
        self.restore()
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "IsolatedDatabase":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"IsolatedDatabase(db_path={self.db_path})"


@contextmanager
def isolated_database(
    *modules: types.ModuleType,
    prefix: str = "jdl_test_db_",
    initialize: Optional[Any] = None,
    extra_paths: Optional[Mapping[str, Any]] = None,
) -> Iterator[IsolatedDatabase]:
    """Context manager yielding an :class:`IsolatedDatabase` applied to ``modules``.

    ``modules`` are the engine-like modules whose ``DATA_DIR`` / ``DB_PATH``
    should follow the temporary database. ``extra_paths`` optionally redirects
    further writable path globals on every module (see
    :meth:`IsolatedDatabase.apply_to`). ``initialize`` is an optional callable
    invoked with the database once it is installed — typically the engine's own
    ``init_db()``, so the schema is created by the real code under test rather
    than by a hand-copied ``CREATE TABLE``.

    On exit the modules are restored and the directory removed, so a test cannot
    leave rows behind even in its own temporary database.
    """
    database = IsolatedDatabase(prefix=prefix)
    try:
        for module in modules:
            database.apply_to(module, extra=extra_paths)
        if initialize is not None:
            initialize(database)
        yield database
    finally:
        database.close()


def import_engine_or_none() -> Optional[types.ModuleType]:
    """Import ``jdl_flash.flash_loan_engine``, or ``None`` if it is unavailable.

    The engine has a hard third-party dependency (``python-dotenv``) that is not
    installed on every interpreter, and importing it has side effects: it reads
    ``~/jdl/.env`` and may dial an RPC. Callers that only need the guard get
    ``None`` rather than an exception, so a missing optional dependency is
    reported honestly instead of crashing the whole suite.
    """
    try:
        return importlib.import_module("jdl_flash.flash_loan_engine")
    except Exception:  # noqa: BLE001 - any import failure means "not available"
        return None


def assert_engine_isolated(engine: types.ModuleType) -> Path:
    """Assert ``engine`` is pointed at a safe database and return that path.

    Used as a whole-suite assertion: if anything has restored or reassigned the
    engine's globals back to the live ledger, this fails loudly.
    """
    return assert_not_live_db(getattr(engine, "DB_PATH", None), context="engine DB_PATH check")


def describe_environment() -> Dict[str, str]:
    """A short, printable summary of how the database is currently resolved.

    Returned rather than printed, so callers choose their own formatting and no
    library code ever writes to stdout.
    """
    engine = import_engine_or_none()
    return {
        "home": str(Path.home()),
        "account_home": str(_account_home()),
        "default_db_path": str(default_db_path()),
        "configured_db_path": str(configured_db_path() or ""),
        "resolved_db_path": str(resolve_db_path()),
        "engine_db_path": str(getattr(engine, "DB_PATH", "(engine not importable)")),
        "guard_installed": "yes" if guard_is_installed() else "no",
    }


# ────────────────────────────────────────────────────────────────────────────
#  Self-check
# ────────────────────────────────────────────────────────────────────────────
#
# This module is both a helper library and a suite. It has to be the latter: it
# was originally shipped as dead code — present in the package, absent from
# jdl_flash.cli._PACKAGED_TESTS, and missing the sys.path bootstrap — so nothing
# ever executed it and its own claims were never checked by anything. The
# checks below are what `jdl test` (and CI, which runs the same list) now
# executes, and they deliberately avoid asserting anything about the contents
# of the real ledger: the suite must stay green both before and after the
# phantom '0xhash123' rows are purged from it.

def _self_check(check: Any) -> None:
    """Exercise every public guarantee this module makes, in-process."""
    live = default_db_path()

    check(guard_is_installed(),
          "the tripwire is installed on import (whole-suite coverage, no fixture needed)")
    check(sqlite3.connect is not install_test_db_guard()._real_connect,
          "the process's sqlite3.connect is the guard, not the stdlib original")
    check(install_test_db_guard() is install_test_db_guard(),
          "install_test_db_guard() is idempotent")

    check(is_live_db(live), "the production database path is recognised as live")
    check(is_live_db(str(live)), "recognised from a plain string")
    check(is_live_db(str(live) + "/"), "recognised through a trailing slash")
    check(is_live_db(str(live.parent / ".." / live.parent.name / live.name)),
          "recognised through a '..' traversal")
    check(is_live_db(_account_home() / DATA_DIR_NAME / DB_FILE_NAME),
          "the passwd-derived home is recognised even when $HOME is redirected")
    check(not is_live_db(live.parent / "flash_test.db"),
          "a sibling file in the same directory is not mistaken for the ledger")

    try:
        sqlite3.connect(live)
    except LiveDatabaseError as exc:
        blocked, detail = True, str(exc)
    except Exception:  # noqa: BLE001 - any other error still means it did not connect
        blocked, detail = False, "connect() raised an unrelated error"
    else:
        blocked, detail = False, "connect() returned normally"
    check(blocked, f"sqlite3.connect(live) raises LiveDatabaseError ({detail.splitlines()[0]})")
    check(any(alias in detail for alias in DB_PATH_ENV_ALIASES),
          "the refusal names the env var that would fix it")

    for allowed, label in (
        (":memory:", "in-memory databases"),
        ("", "SQLite's empty-string temporary database"),
        (None, "a None database argument"),
        (5, "a file-descriptor argument"),
    ):
        try:
            con = sqlite3.connect(allowed)
        except LiveDatabaseError:
            check(False, f"{label} are allowed")
        except Exception:  # noqa: BLE001 - unrelated sqlite failure still means "allowed"
            check(True, f"{label} are allowed")
        else:
            con.close()
            check(True, f"{label} are allowed")

    # Path resolution: identical to the pre-existing production default with no
    # override, which is the guarantee that lets production depend on this.
    saved = {alias: os.environ.get(alias) for alias in DB_PATH_ENV_ALIASES}
    try:
        for alias in DB_PATH_ENV_ALIASES:
            os.environ.pop(alias, None)
        check(configured_db_path() is None, "no env var means no configured override")
        check(resolve_db_path() == default_db_path(),
              "resolve_db_path() defaults to the production database")
        check(resolve_engine_data_dir() == default_data_dir(),
              "resolve_engine_data_dir() defaults to the production data dir")

        with IsolatedDatabase(prefix="jdl_guard_selfcheck_") as database:
            for alias in DB_PATH_ENV_ALIASES:
                os.environ[alias] = str(database.db_path)
            check(resolve_db_path() == database.db_path,
                  f"resolve_db_path() honours {DB_PATH_ENV_ALIASES[0]}")
            check(resolve_engine_data_dir() == database.data_dir,
                  "resolve_engine_data_dir() follows the overridden database")
            check(not is_live_db(database.db_path),
                  "an isolated database is never mistaken for the live ledger")
    finally:
        for alias, value in saved.items():
            if value is None:
                os.environ.pop(alias, None)
            else:
                os.environ[alias] = value

    # The purge helper that closes the '0xhash123' accumulation gap.
    with IsolatedDatabase(prefix="jdl_guard_purge_") as database:
        database.apply_to(types.SimpleNamespace(DB_PATH=database.db_path))
        con = sqlite3.connect(database.db_path)
        try:
            con.execute("CREATE TABLE executions (tx_hash TEXT, net_usd REAL, success INTEGER)")
            con.executemany("INSERT INTO executions VALUES(?,?,?)",
                            [("0xhash123", 2.4, 1), ("0xkeep", 1.0, 1)])
            con.commit()
        finally:
            con.close()
        check(purge_execution_rows(database.db_path, ["0xhash123"]) == 1,
              "purge_execution_rows() removes exactly the named fixture row")
        con = sqlite3.connect(database.db_path)
        try:
            remaining = con.execute("SELECT tx_hash FROM executions").fetchall()
        finally:
            con.close()
        check(remaining == [("0xkeep",)], "a targeted purge leaves other rows alone")
        check(purge_execution_rows(database.db_path) == 1,
              "purge_execution_rows(None) empties a database we own")
    check(not database.root.exists(), "the isolated database directory is removed on close")

    try:
        purge_execution_rows(live)
    except LiveDatabaseError:
        check(True, "purge_execution_rows() refuses the live ledger")
    else:
        check(False, "purge_execution_rows() refuses the live ledger", "it returned normally")


def main() -> int:
    passed = failed = 0

    def check(cond: Any, msg: str, detail: str = "") -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✓ {msg}")
        else:
            failed += 1
            print(f"  ✗ {msg}" + (f"  ({detail})" if detail else ""))

    print("test_db_guard — live-database guard self-check")
    _self_check(check)
    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
