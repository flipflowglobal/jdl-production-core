"""
paths.py — production resolution of the flash-loan engine's data locations.

This module is the single source of truth for *where* the engine stores its
data. It is production code and is imported by production code: the supervisor
sums the same ``executions`` table the engine writes, so the two must never
disagree about which database they are looking at.

Why this file exists at all
---------------------------
The path resolution used to live in ``jdl_flash/test_db_guard.py``, and
``flash_supervisor.py`` imported that *test* module to borrow it. Two problems
with that arrangement, both of which this module removes:

1. **A test module shipped into the production wheel.** ``pyproject.toml``
   packages ``jdl_flash``, so ``test_db_guard`` was installed alongside the
   engine, and every production start imported a module whose import side
   effect *arms a global monkeypatch of ``sqlite3.connect``* and then has to
   disarm it again. Arming and disarming a process-wide monkeypatch on every
   daemon start is exactly the fragility that made the supervisor's
   ``total_profit()`` silently report ``$0.00`` once — the bare
   ``except: return 0.0`` in that function swallowed the error and the daemon
   carried on trading against a number that was not real.

2. **Inverted dependency direction.** Production depending on test code means a
   test file can break production. The rule in this package is that test
   modules may depend on production modules, never the reverse; this file is
   what makes that rule enforceable rather than aspirational.

What is deliberately *not* here
-------------------------------
The test tripwire. :mod:`jdl_flash.test_db_guard` still owns the
``sqlite3.connect`` monkeypatch, the isolated-database harness and the purge
helper — all of which are test concerns. This module ships path resolution and
predicates that are safe to evaluate in production: it reads the environment and
compares paths, and it never mutates interpreter state on import.

Contract
--------
With no environment override, every function here returns exactly the
pre-existing production location (``Path.home()/'.flash_loan_engine'`` and
``'flash.db'`` inside it). Importing this module therefore cannot change
production behaviour, and setting an override redirects every consumer
together. That property is what makes it safe for both the daemon and the test
suite to depend on.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from jdl_flash.config import env_str

__all__ = [
    "DATA_DIR_NAME",
    "DB_FILE_NAME",
    "DB_PATH_ENV_ALIASES",
    "LiveDatabaseError",
    "account_home",
    "assert_not_live_db",
    "configured_db_path",
    "default_data_dir",
    "default_db_path",
    "env_alias_names",
    "is_live_db",
    "normalise",
    "production_db_paths",
    "resolve_db_path",
    "resolve_engine_data_dir",
]

#: Directory the engine creates under the user's home for all of its state.
DATA_DIR_NAME = ".flash_loan_engine"

#: The revenue ledger's filename inside :data:`DATA_DIR_NAME`.
DB_FILE_NAME = "flash.db"

#: Environment aliases for the database path, in precedence order.
#:
#: The engine already accepts multiple aliases for every other setting
#: (``_env('PRIVATE_KEY', 'WALLET_PRIVATE_KEY')``, ``env_bool('LIVE_EXECUTION',
#: 'LIVE_EXEC', ...)``); this follows that convention rather than inventing a
#: new mechanism, so an operator who has already wired one of these names for
#: another tool keeps getting the same answer here.
DB_PATH_ENV_ALIASES: Tuple[str, ...] = (
    "FLASH_DB_PATH",
    "JDL_FLASH_DB_PATH",
    "FLASH_LOAN_DB_PATH",
)


class LiveDatabaseError(RuntimeError):
    """Raised when something tries to open the live revenue database under test.

    Deliberately loud: it names the offending path and how to fix it, rather
    than silently redirecting, because a silent redirect is exactly the failure
    mode that let a test corrupt the revenue ledger in the first place.

    This exception is *declared* here so that the error type has a stable
    production home and the test guard can import it rather than the reverse.
    Nothing in this module raises it — see :func:`assert_not_live_db`, which is
    called by the test guard and by whole-suite safety checks.
    """


def account_home() -> Path:
    """The account's real home directory, independent of ``$HOME``.

    ``Path.home()`` honours ``$HOME``, so a harness that redirects ``$HOME`` to a
    scratch directory can make the production default look harmless. A passwd
    entry cannot be redirected that way, which makes it a useful second opinion
    when deciding whether a path is the live database.

    Falls back to :meth:`Path.home` where the passwd database is unavailable
    (non-POSIX, or a container with no entry for the current uid).
    """
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, AttributeError, OSError):
        return Path.home()


def default_data_dir() -> Path:
    """Where the engine stores its data when nothing overrides it."""
    return Path.home() / DATA_DIR_NAME


def default_db_path() -> Path:
    """The database the engine opens when nothing overrides it."""
    return default_data_dir() / DB_FILE_NAME


def production_db_paths() -> Tuple[Path, ...]:
    """Every path that counts as the live revenue database, de-duplicated.

    Two candidates, because either one being wrong is a real hazard:

    * ``$HOME``-derived — what :meth:`Path.home` yields *right now*. This is the
      path the engine defaults to in this process, so a test resolving to it is
      writing where the engine would write.
    * passwd-derived — the genuine account home, unaffected by ``$HOME``. This
      catches a suite that redirects ``$HOME`` to a scratch directory and then
      points the database straight back at the real ledger.
    """
    candidates = (default_db_path(), account_home() / DATA_DIR_NAME / DB_FILE_NAME)
    seen: List[Path] = []
    for candidate in candidates:
        try:
            expanded = candidate.expanduser()
        except (RuntimeError, OSError):
            expanded = candidate
        if expanded not in seen:
            seen.append(expanded)
    return tuple(seen)


def normalise(path: "os.PathLike[str] | str") -> Path:
    """Absolute, user-expanded, symlink-resolved form of ``path``.

    :meth:`Path.resolve` is what makes comparison meaningful: ``/tmp/x/../x/f.db``,
    a symlinked scratch directory and a trailing-slash spelling of the same file
    all collapse to one identity. It is non-strict, so a database that does not
    exist yet still resolves.
    """
    expanded = Path(path).expanduser()
    try:
        return expanded.resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(str(expanded)))


def is_live_db(path: "os.PathLike[str] | str | None") -> bool:
    """True when ``path`` resolves to the live revenue database.

    Pure predicate — it evaluates no side effects and is safe to call from
    production code such as the supervisor's threshold arithmetic.
    """
    if path is None:
        return False
    try:
        resolved = normalise(path)
    except (TypeError, ValueError, OSError):
        return False
    return any(normalise(live) == resolved for live in production_db_paths())


def assert_not_live_db(
    path: "os.PathLike[str] | str | None", *, context: str = "database access"
) -> Path:
    """Return ``path`` as a resolved :class:`Path`, or raise if it is the live database.

    ``context`` names the caller, so a failure message points at the code that
    tried to open the production ledger rather than at this helper.
    """
    if path is None:
        raise LiveDatabaseError(f"{context} requires a database path, got None")
    resolved = normalise(path)
    if is_live_db(resolved):
        live = ", ".join(str(p) for p in production_db_paths())
        raise LiveDatabaseError(
            f"refusing to open the live revenue database during {context}: {resolved}\n"
            f"  This file is the real revenue ledger (production path: {live}).\n"
            f"  Opening it from a test inflates recorded real revenue and can arm a\n"
            f"  real withdrawToken() once the total crosses $1000.\n"
            f"  Use jdl_flash.test_db_guard.isolated_database(), or set one of\n"
            f"  {', '.join(DB_PATH_ENV_ALIASES)} to a temporary path."
        )
    return resolved


def env_alias_names() -> Tuple[str, ...]:
    """The env var names that can redirect the database, for help text and tests."""
    return DB_PATH_ENV_ALIASES


def configured_db_path() -> Optional[Path]:
    """The environment override for the database path, or ``None`` when unset.

    Read through :func:`jdl_flash.config.env_str` so the semantics are exactly
    the engine's own: first non-blank alias wins, values are stripped, and a
    malformed value can never raise.
    """
    raw = env_str(*DB_PATH_ENV_ALIASES, default="")
    if not raw:
        return None
    return normalise(raw)


def resolve_db_path(default: "os.PathLike[str] | str | None" = None) -> Path:
    """Resolve the database path: environment override, else ``default``, else production.

    ``default`` lets a caller supply the production location it would otherwise
    hard-code, keeping the "no override means unchanged behaviour" guarantee in
    one place rather than at every call site.
    """
    override = configured_db_path()
    if override is not None:
        return override
    if default is not None:
        return normalise(default)
    return default_db_path()


def resolve_engine_data_dir(default: "os.PathLike[str] | str | None" = None) -> Path:
    """Resolve the data directory that holds the database.

    When the database path is overridden, the data directory becomes that
    database's parent. That keeps everything a test can cause to be written
    (``flash.db``, the ``HALT`` sentinel, ``daemon.pid``, ``daemon.log``) inside
    the same scratch tree instead of leaving the sentinel in the real home
    directory. With no override this is byte-for-byte today's
    ``Path.home() / '.flash_loan_engine'``.
    """
    override = configured_db_path()
    if override is not None:
        return override.parent
    if default is not None:
        return normalise(default)
    return default_data_dir()
