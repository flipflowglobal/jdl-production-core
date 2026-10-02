"""
Tests for flash_supervisor.py's target-resolution logic (resolve_target), which
picks which module `python -m` supervises: the legacy single-process engine
('engine', the default/unchanged behavior) or the always-on swarm daemon ('swarm').

Also covers the B8 database-isolation guarantee: the supervisor sums the same
`executions` table the engine writes and arms a real withdrawToken() off that
sum, so it must never be pointed at the live revenue ledger by a test run.

Run: cd python && python3 test_flash_supervisor.py
"""
import importlib
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Imported first, before anything can open a database: importing the guard
# installs the process-wide tripwire that refuses the live ledger, so it covers
# every test in this file without any of them opting in.
from jdl_flash.test_db_guard import (
    DB_PATH_ENV_ALIASES,
    IsolatedDatabase,
    LiveDatabaseError,
    assert_not_live_db,
    default_db_path,
    describe_environment,
    guard_is_installed,
    install_test_db_guard,
    is_live_db,
    purge_execution_rows,
    resolve_db_path,
    resolve_engine_data_dir,
)

import flash_supervisor as fs


def _supervisor_extra_paths(database: IsolatedDatabase) -> dict:
    """Every other path the supervisor can write, pinned inside the isolated data dir.

    ``flash.db`` is the revenue ledger the supervisor sums; ``daemon.pid`` and
    ``daemon.log`` are written by :meth:`DaemonProc.start`. None of the three
    may land in the real home directory.
    """
    return {
        "PID_FILE": database.data_dir / "daemon.pid",
        "LOG_FILE": database.data_dir / "daemon.log",
    }


def _create_executions(db_path, rows, strategy="arb-uni-v3"):
    """Create the supervisor's read query target: a real `executions` table.

    Mirrors the subset of the schema flash_loan_engine.init_db() creates that
    flash_supervisor.total_profit() / exec_count() actually read.

    ``strategy`` defaults to a live-path name rather than 'TEST'. total_profit()
    and exec_count() now apply the same real-revenue clause the engine's
    RevenueTracker applies (see REAL_REVENUE_WHERE in flash_supervisor.py): a
    'TEST'/'sim_'/'dry_' row is retained for audit but excluded from revenue. A
    fixture that seeded only 'TEST' rows would therefore assert 0.0 and prove
    nothing, so the exclusion is asserted directly in
    test_synthetic_rows_are_not_revenue instead.
    """
    con = sqlite3.connect(db_path)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS executions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL,
                strategy     TEXT,
                gas_method   TEXT,
                asset        TEXT,
                loan_usd     REAL,
                profit_usd   REAL,
                gas_cost_usd REAL,
                net_usd      REAL,
                tx_hash      TEXT,
                success      INTEGER DEFAULT 1
            );
        """)
        for tx_hash, net_usd, success in rows:
            con.execute(
                'INSERT INTO executions(ts,strategy,gas_method,asset,loan_usd,'
                'profit_usd,gas_cost_usd,net_usd,tx_hash,success) '
                'VALUES(?,?,?,?,?,?,?,?,?,?)',
                (0.0, strategy, "UNIT", "0xTEST", 1000.0, net_usd, 0.0, net_usd, tx_hash, success),
            )
        con.commit()
    finally:
        con.close()


def test_target_resolution(check):
    """The original target-resolution suite, unchanged."""
    os.environ.pop("SUPERVISOR_TARGET", None)

    check(fs.resolve_target(None) == "jdl_flash.flash_loan_engine",
          "no spec, no env var -> defaults to the legacy engine (unchanged behavior)")
    check(fs.resolve_target("engine") == "jdl_flash.flash_loan_engine",
          "'engine' resolves to the legacy engine module")
    check(fs.resolve_target("swarm") == "jdl_flash.swarm_daemon",
          "'swarm' resolves to the always-on swarm daemon module")
    check(fs.resolve_target("some.custom.module") == "some.custom.module",
          "a raw dotted module path passes through unchanged")
    check(fs.resolve_target("garbage") == "jdl_flash.flash_loan_engine",
          "an unrecognized, non-dotted spec falls back to the legacy engine, not a crash")

    os.environ["SUPERVISOR_TARGET"] = "swarm"
    check(fs.resolve_target(None) == "jdl_flash.swarm_daemon",
          "SUPERVISOR_TARGET env var is honored when no explicit spec is passed")
    check(fs.resolve_target("engine") == "jdl_flash.flash_loan_engine",
          "an explicit spec overrides the SUPERVISOR_TARGET env var")
    os.environ.pop("SUPERVISOR_TARGET", None)

    check(fs.TARGETS == {"engine": "jdl_flash.flash_loan_engine", "swarm": "jdl_flash.swarm_daemon"},
          "TARGETS maps exactly the two supported keys")


def test_db_guard_active(check):
    """The tripwire is installed process-wide, not opt-in per test."""
    check(guard_is_installed(),
          "sqlite3.connect tripwire is installed on import (whole-suite coverage)")
    check(sqlite3.connect is not install_test_db_guard()._real_connect,
          "the installed connect is the guard, not the stdlib original")
    check(install_test_db_guard() is install_test_db_guard(),
          "install_test_db_guard() is idempotent")

    for alias in DB_PATH_ENV_ALIASES:
        check(alias.startswith("FLASH") or alias.startswith("JDL"),
              f"env alias {alias} follows the engine's FLASH_*/JDL_* naming")


def test_live_db_is_refused(check):
    """Every route to the live ledger fails loudly instead of corrupting it."""
    live = default_db_path()

    check(is_live_db(live), "the production database path is recognised as live")
    check(is_live_db(str(live)), "recognised from a plain string")
    check(is_live_db(str(live) + "/"), "recognised through a trailing slash")
    check(is_live_db(str(live.parent / ".." / live.parent.name / live.name)),
          "recognised through a '..' traversal")
    check(not is_live_db(live.parent / "flash_test.db"),
          "a sibling file in the same directory is not mistaken for the ledger")

    try:
        sqlite3.connect(live)
    except LiveDatabaseError as exc:
        blocked = True
        detail = str(exc)
    else:
        blocked = False
        detail = "connect() returned normally"
    check(blocked, f"sqlite3.connect(live) raises LiveDatabaseError ({detail.splitlines()[0]})")
    check("FLASH_DB_PATH" in detail or DB_PATH_ENV_ALIASES[0] in detail,
          "the refusal names the env var that would fix it")

    for bogus in (None, 5, object()):
        check(_connect_is_allowed(bogus), f"non-path connect argument is allowed ({bogus!r})")

    check(_connect_is_allowed(":memory:"), "in-memory databases are allowed")
    check(_connect_is_allowed(""), "SQLite's empty-string temporary database is allowed")

    try:
        assert_not_live_db(live, context="unit check")
    except LiveDatabaseError:
        raised = True
    else:
        raised = False
    check(raised, "assert_not_live_db() raises on the live path",
          _environment_summary())


def _environment_summary() -> str:
    """One-line resolution summary, for failure detail only.

    When the tripwire misfires the first question is always "which database did
    it think was live?", so the resolved paths are attached to the failure.
    """
    return "; ".join(f"{k}={v}" for k, v in describe_environment().items())


def _connect_is_allowed(database) -> bool:
    """True when the guard permits opening ``database`` (it may still fail for
    unrelated reasons — a missing parent directory, say — but never because of
    the live-database rule)."""
    try:
        con = sqlite3.connect(database)
    except LiveDatabaseError:
        return False
    except Exception:  # noqa: BLE001 - unrelated sqlite failure still means "allowed"
        return True
    con.close()
    return True


def test_supervisor_reads_only_the_isolated_db(check):
    """total_profit()/exec_count() must sum the temporary database, nothing else.

    This is the test that would have caught B8: it seeds a row worth more than
    the $1000 withdrawal threshold and asserts the supervisor's totals come from
    the isolated file, so the fixture can never arm a real withdrawal.
    """
    with IsolatedDatabase(prefix="jdl_supervisor_test_") as database:
        database.apply_to(fs, extra=_supervisor_extra_paths(database))

        check(fs.DB_PATH == database.db_path, "supervisor DB_PATH points at the temp db")
        check(not is_live_db(fs.DB_PATH), "supervisor DB_PATH is not the live ledger")
        check(fs.DATA_DIR == database.data_dir, "supervisor DATA_DIR is inside the temp tree")
        check(fs.PID_FILE.parent == database.data_dir, "PID_FILE is inside the temp tree")
        check(fs.LOG_FILE.parent == database.data_dir, "LOG_FILE is inside the temp tree")

        # The B8 fixture row, plus a failed row that must never be counted.
        _create_executions(
            database.db_path,
            [("0xhash123", 2.4, 1), ("0xreal", 7.5, 1), ("0xfailed", 999.0, 0)],
        )

        check(fs.total_profit() == 9.9,
              f"total_profit sums only success=1 rows in the temp db (got {fs.total_profit()})")
        check(fs.exec_count() == 2,
              f"exec_count counts only success=1 rows in the temp db (got {fs.exec_count()})")

        # The withdrawal threshold must trip on the temp total, never on the live one.
        check(fs.total_profit() < fs.THRESHOLD,
              "fixture rows alone stay under the $1000 withdrawal threshold")
        _create_executions(database.db_path, [("0xbig", 1500.0, 1)])
        check(fs.total_profit() >= fs.THRESHOLD,
              "threshold arithmetic is unchanged and reads the temp db")


def test_synthetic_rows_are_not_revenue(check):
    """TEST/sim_/dry_ rows are retained for audit but never summed as revenue.

    The live regression: three leftover 'TEST' rows ($2.40 net each) sat in the
    revenue ledger, and because total_profit() summed every success=1 row, `jdl
    status` reported $7.20 of earnings for a system that had never broadcast a
    transaction. Rows are still written (auditability); only the sums exclude them.
    """
    with IsolatedDatabase(prefix="jdl_supervisor_synthetic_") as database:
        database.apply_to(fs, extra=_supervisor_extra_paths(database))

        # One real win, plus synthetic rows in every reserved flavour.
        _create_executions(database.db_path, [("0xreal", 5.0, 1)])
        _create_executions(database.db_path, [("0xtest", 2.4, 1)], strategy="TEST")
        _create_executions(database.db_path, [("0xtest2", 3.0, 1)], strategy="TEST_sweep")
        _create_executions(database.db_path, [("0xsim", 100.0, 1)], strategy="sim_scan")
        _create_executions(database.db_path, [("0xdry", 50.0, 1)], strategy="dry_run")

        check(fs.total_profit() == 5.0,
              f"only the real row counts (got ${fs.total_profit():.2f})")
        check(fs.exec_count() == 1,
              f"only the real row is counted (got {fs.exec_count()})")

        # A synthetic row large enough to arm a withdrawal must still not arm it.
        _create_executions(database.db_path, [("0xsimbig", 5000.0, 1)], strategy="sim_scan")
        check(fs.total_profit() < fs.THRESHOLD,
              "a synthetic row can never arm the withdrawal threshold")

        # Excluded from revenue, but not deleted — the ledger stays auditable.
        con = sqlite3.connect(database.db_path)
        try:
            kept = con.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
        finally:
            con.close()
        check(kept == 6, f"every synthetic row is retained in the table (got {kept})")

    check(fs.DB_PATH == default_db_path(),
          "the supervisor's DB_PATH is restored when the fixture closes")


def test_fixture_rows_do_not_survive(check):
    """The cleanup gap that let '0xhash123' accumulate, closed and verified."""
    with IsolatedDatabase(prefix="jdl_supervisor_cleanup_") as database:
        _create_executions(database.db_path, [("0xhash123", 2.4, 1), ("0xkeep", 1.0, 1)])

        removed = purge_execution_rows(database.db_path, ["0xhash123"])
        check(removed == 1, f"purge by explicit tx_hash removed the fixture row ({removed})")

        con = sqlite3.connect(database.db_path)
        try:
            survivors = con.execute(
                "SELECT COUNT(*) FROM executions WHERE tx_hash='0xhash123'").fetchone()[0]
            kept = con.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
        finally:
            con.close()
        check(survivors == 0, "the '0xhash123' row no longer exists")
        check(kept == 1, "unrelated rows are left alone by a targeted purge")

        # init_db()'s own cleanup matches only 'sim_%'/'dry_%' — which is exactly
        # why the fixture row used to outlive every run.
        purge_execution_rows(database.db_path)
        con = sqlite3.connect(database.db_path)
        try:
            remaining = con.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
        finally:
            con.close()
        check(remaining == 0, "a full purge empties the table for a database we own")

    check(not database.root.exists(), "the whole temp database directory is deleted on close")


def test_env_override_wiring(check):
    """The env override redirects the supervisor without changing production."""
    saved = {alias: os.environ.get(alias) for alias in DB_PATH_ENV_ALIASES}
    with IsolatedDatabase(prefix="jdl_supervisor_env_") as database:
        try:
            for alias in DB_PATH_ENV_ALIASES:
                os.environ[alias] = str(database.db_path)

            check(resolve_db_path() == database.db_path,
                  "resolve_db_path() honours the env override")
            check(resolve_engine_data_dir() == database.data_dir,
                  "resolve_engine_data_dir() follows the overridden database")

            reloaded = importlib.reload(fs)
            check(reloaded.DB_PATH == database.db_path,
                  "the supervisor resolves DB_PATH from the env override at import")
            check(reloaded.DATA_DIR == database.data_dir,
                  "the supervisor's DATA_DIR follows the override")
        finally:
            for alias, value in saved.items():
                if value is None:
                    os.environ.pop(alias, None)
                else:
                    os.environ[alias] = value
            importlib.reload(fs)

    check(fs.DB_PATH == default_db_path(),
          "with no env var the supervisor is back on the production path (unchanged behaviour)")


def _seed_ledger(home, rows):
    """Create ``$HOME/.flash_loan_engine/flash.db`` with a known `executions` table.

    `rows` is a sequence of (tx_hash, net_usd, success). The schema is the
    subset flash_loan_engine.init_db() creates that total_profit() / exec_count()
    actually read.
    """
    data_dir = home / ".flash_loan_engine"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "flash.db"
    con = sqlite3.connect(db_path)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS executions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL,
                strategy     TEXT,
                gas_method   TEXT,
                asset        TEXT,
                loan_usd     REAL,
                profit_usd   REAL,
                gas_cost_usd REAL,
                net_usd      REAL,
                tx_hash      TEXT,
                success      INTEGER DEFAULT 1
            );
        """)
        for tx_hash, net_usd, success in rows:
            con.execute(
                'INSERT INTO executions(ts,strategy,gas_method,asset,loan_usd,'
                'profit_usd,gas_cost_usd,net_usd,tx_hash,success) '
                'VALUES(?,?,?,?,?,?,?,?,?,?)',
                (0.0, "SEED", "SEED", "0xSEED", 1000.0, net_usd, 0.0, net_usd, tx_hash, success),
            )
        con.commit()
    finally:
        con.close()
    return db_path


def _run_production_probe(home):
    """Import the supervisor the way production does and report what it sees.

    Runs in a fresh subprocess, because the properties under test — what
    importing the module does to `sqlite3.connect`, and what paths it resolves —
    are per-interpreter facts. This suite deliberately imported the guard first,
    so in here the tripwire is correctly armed and production behaviour cannot
    be observed in-process.

    `home` becomes the child's $HOME, so the child's *production* database is a
    scratch file this test controls. The child is given no database env
    override, so it resolves through exactly the code path a real daemon uses:
    Path.home()/.flash_loan_engine/flash.db.

    The probe deliberately does NOT import jdl_flash.test_db_guard. It reports
    the tripwire's state by observing the stdlib itself — whether sqlite3.connect
    is still the original function, and whether the test module was pulled in at
    all. Importing the guard just to ask "is the guard armed?" would arm it and
    make the question meaningless.

    Two env vars are deliberately stripped, not inherited. ``jdl test`` runs
    every suite (including this one) with JDL_TEST_DB_GUARD=1 and PYTHONPATH so
    that python/sitecustomize.py arms the tripwire in each suite's interpreter;
    the probe simulates a production daemon, where neither exists. Stripping
    the marker (and letting the probe's own PYTHONPATH stand) keeps the probe
    truthful when this suite runs under `jdl test` as well as standalone.
    """
    import subprocess

    child_env = {k: v for k, v in os.environ.items() if k not in DB_PATH_ENV_ALIASES}
    child_env.pop("JDL_TEST_DB_GUARD", None)
    python_dir = os.path.dirname(os.path.abspath(__file__))
    child_env["PYTHONPATH"] = python_dir
    child_env["HOME"] = str(home)
    probe = "\n".join([
        "import sqlite3, sys",
        f"sys.path.insert(0, {python_dir!r})",
        "original = sqlite3.connect",
        "import flash_supervisor as fs",
        "print('GUARD_MODULE_LOADED', 'jdl_flash.test_db_guard' in sys.modules)",
        "print('RESTORED', sqlite3.connect is original)",
        "print('DBPATH', fs.DB_PATH)",
        # A direct connect, independent of total_profit()'s bare `except`. If
        # anything blocked the configured ledger, this raises instead of being
        # silently converted to 0.0.
        "try:",
        "    con = sqlite3.connect(fs.DB_PATH); con.close()",
        "    print('DIRECT_CONNECT', True)",
        "except Exception as exc:",
        "    print('DIRECT_CONNECT', False); print('DIRECT_ERROR', type(exc).__name__)",
        "print('PROFIT', round(fs.total_profit(), 6))",
        "print('COUNT', fs.exec_count())",
    ]) + "\n"
    proc = subprocess.run(
        [sys.executable, "-c", probe], env=child_env,
        capture_output=True, text=True, timeout=60,
    )
    values = {}
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            key, _, value = line.partition(" ")
            values[key] = value.strip()
    values["_rc"] = str(proc.returncode)
    values["_stderr"] = (proc.stderr or "").strip()
    return values


def test_production_import_does_not_arm_tripwire(check):
    """Importing the supervisor in production must leave the ledger readable.

    Regression test, and the most important one here. The supervisor resolves
    its database through jdl_flash.paths, and total_profit() sums that same
    `executions` table the engine writes. If anything got between the two, the
    read would fail, total_profit()'s bare `except: return 0.0` would swallow
    the error, and the supervisor would report $0.00 revenue forever while the
    daemon kept trading.

    What is asserted here is the MECHANISM, never the contents of any real
    database. The probe runs against a scratch $HOME this test seeds with known
    values, so the expected total is a constant chosen by the test rather than
    whatever happens to be in ~/.flash_loan_engine/flash.db right now. An
    earlier revision asserted `profit != 0 or count != 0` against the *real*
    ledger, which passed only because three phantom '0xhash123' rows were
    sitting in it; against a pristine, correctly-schema'd, zero-row production
    ledger that assertion fails, which would have turned the (intended) removal
    of those rows into an apparent regression. The properties actually under
    test — the import has no side effects, and production reads the configured
    path — are data-independent and hold for an empty ledger too.
    """
    import tempfile

    python_dir = os.path.dirname(os.path.abspath(__file__))
    seeds = (("0xseed_a", 11.25, 1), ("0xseed_b", 30.5, 1), ("0xignored", 999.0, 0))
    expected_profit = 41.75   # success=1 rows only; the success=0 row must not count
    expected_count = 2

    roots = []
    try:
        for label, rows in (("populated", seeds), ("empty", ())):
            home = Path(tempfile.mkdtemp(prefix=f"jdl_supervisor_{label}_"))
            roots.append(home)
            _seed_ledger(home, rows)
            values = _run_production_probe(home)

            if values.get("_rc") != "0":
                check(False, f"Production import probe runs ({label} ledger)",
                      values.get("_stderr", "").splitlines()[-1:] or ["no stderr"])
                continue

            # 1. The import has no side effects on sqlite3 and does not drag a
            #    test module into production. Both hold for empty and populated.
            check(values.get("GUARD_MODULE_LOADED") == "False",
                  f"Production import pulls in no test module ({label} ledger)",
                  f"GUARD_MODULE_LOADED={values.get('GUARD_MODULE_LOADED')}")
            check(values.get("RESTORED") == "True",
                  f"sqlite3.connect is left as the stdlib original ({label} ledger)",
                  f"RESTORED={values.get('RESTORED')}")
            check(values.get("DIRECT_CONNECT") == "True",
                  f"the configured ledger opens without being blocked ({label} ledger)",
                  f"DIRECT_CONNECT={values.get('DIRECT_CONNECT')} "
                  f"ERROR={values.get('DIRECT_ERROR', '')}")

            # 2. It reads the path production would use, i.e. the one under the
            #    child's $HOME — not a hard-coded or stale one.
            check(values.get("DBPATH") == str(home / ".flash_loan_engine" / "flash.db"),
                  f"supervisor resolves the production path for this HOME ({label} ledger)",
                  f"DBPATH={values.get('DBPATH')}")

            # 3. The totals come from the seeded ledger. This is the assertion
            #    that would catch the $0.00-forever regression: if a block on
            #    the read appeared, PROFIT would silently read 0.0 and this
            #    would fail loudly rather than looking like an empty ledger.
            #
            #    Compared numerically, not as formatted text: the probe prints
            #    str(round(x, 6)), so the exact spelling of 41.75 is an
            #    artefact of float formatting and asserting on it would make
            #    this test fail for reasons that have nothing to do with the
            #    behaviour under test.
            try:
                profit = float(values.get("PROFIT", "nan"))
            except ValueError:  # pragma: no cover - only if the probe misbehaved
                profit = float("nan")
            if rows:
                check(abs(profit - expected_profit) < 1e-6,
                      f"total_profit() returns the seeded total exactly ({label} ledger)",
                      f"PROFIT={values.get('PROFIT')} expected={expected_profit}")
                check(values.get("COUNT") == str(expected_count),
                      f"exec_count() counts only success=1 seeded rows ({label} ledger)",
                      f"COUNT={values.get('COUNT')} expected={expected_count}")
            else:
                # An empty ledger is a legitimate production state, not a
                # failure: the totals must simply be zero, and the two
                # data-independent properties above must still hold. Asserting
                # "non-zero" here is exactly the defect being fixed.
                check(profit == 0.0,
                      "total_profit() is 0.00 for a genuinely empty ledger, not an error",
                      f"PROFIT={values.get('PROFIT')}")
                check(values.get("COUNT") == "0",
                      "exec_count() is 0 for a genuinely empty ledger, not an error",
                      f"COUNT={values.get('COUNT')}")
    finally:
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)


def main():
    passed = failed = 0

    def check(cond, msg, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✓ {msg}")
        else:
            failed += 1
            print(f"  ✗ {msg}" + (f"  ({detail})" if detail else ""))

    test_target_resolution(check)
    test_db_guard_active(check)
    test_live_db_is_refused(check)
    test_supervisor_reads_only_the_isolated_db(check)
    test_synthetic_rows_are_not_revenue(check)
    test_fixture_rows_do_not_survive(check)
    test_production_import_does_not_arm_tripwire(check)
    test_env_override_wiring(check)

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
