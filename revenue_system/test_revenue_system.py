#!/usr/bin/env python3
"""
Self-contained tests for the standalone revenue_system package.

Fully offline, stdlib-only (no requests/web3 needed): builds a temp SQLite db
from the REAL database/revenue_schema.sql, then exercises the recording API and
verifies the auto-aggregating triggers and helper views behave.

Run:  python3 revenue_system/test_revenue_system.py
"""
import os
import sqlite3
import sys
import tempfile

# Repo root = parent of this file's directory (…/revenue_system/ -> repo root)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from revenue_system.revenue_recording import (  # noqa: E402
    record_flash_arbitrage,
    record_withdrawal,
    get_database_path,
)

SCHEMA = os.path.join(_REPO_ROOT, "database", "revenue_schema.sql")

passed = 0
failed = 0


def check(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {msg}")
    else:
        failed += 1
        print(f"  ✗ {msg}")


def _fresh_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    with open(SCHEMA, "r", encoding="utf-8") as fh:
        con.executescript(fh.read())
    con.commit()
    con.close()
    return path


def _names(db, kind):
    """Set of table/view names in the db (short-lived connection)."""
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type=?", (kind,))}
    finally:
        con.close()


def _one(db, sql, params=()):
    """Fetch one row as a dict via a short-lived connection so we never hold a
    lock across the recording API (which opens its own writer connection)."""
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(sql, params).fetchone()
        return dict(r) if r is not None else None
    finally:
        con.close()


def _exec(db, sql, params=()):
    con = sqlite3.connect(db)
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def main() -> int:
    db = _fresh_db()

    # ── schema: the 6 documented tables + 3 views all materialize ──
    tables = _names(db, "table")
    for t in ("flash_trades", "withdrawals", "revenue_summary",
              "reconciliation_log", "chain_health", "rpc_diagnostics"):
        check(t in tables, f"schema creates table {t}")
    views = _names(db, "view")
    for v in ("vw_profit_by_chain", "vw_all_projects_total",
              "vw_pending_withdrawals"):
        check(v in views, f"schema creates view {v}")

    # ── record_flash_arbitrage writes a row and the trigger aggregates it ──
    trade = {
        "project": "Aureon", "chain": "arbitrum", "asset_borrowed": "USDC",
        "amount_borrowed": 1000.0, "fee_paid": 5.0, "gross_profit": 8.5,
        "gas_cost": 0.5, "net_profit": 8.0, "tx_hash": "0xaaa1",
        "contract_address": "0xContract", "initiator": "0xWallet",
    }
    check(record_flash_arbitrage(db, trade) is True, "record_flash_arbitrage returns True")

    row = _one(db, "SELECT * FROM flash_trades WHERE tx_hash='0xaaa1'")
    check(row is not None and row["status"] == "success", "trade row stored with status=success")

    summ = _one(db, "SELECT * FROM revenue_summary WHERE project='Aureon' AND chain='arbitrum'")
    check(summ is not None, "trigger created a revenue_summary row")
    check(summ["total_trades"] == 1, "summary total_trades == 1")
    check(abs(summ["total_net_profit"] - 8.0) < 1e-9, "summary total_net_profit == 8.0")
    check(summ["successful_trades"] == 1, "summary successful_trades == 1")

    # ── a second trade accumulates ──
    trade2 = dict(trade, tx_hash="0xaaa2", net_profit=2.0, gross_profit=3.0, gas_cost=1.0)
    record_flash_arbitrage(db, trade2)
    summ = _one(db, "SELECT * FROM revenue_summary WHERE project='Aureon' AND chain='arbitrum'")
    check(summ["total_trades"] == 2, "second trade accumulates total_trades == 2")
    check(abs(summ["total_net_profit"] - 10.0) < 1e-9, "net profit accumulates to 10.0")

    # ── duplicate tx_hash is rejected (UNIQUE constraint) without corrupting totals ──
    check(record_flash_arbitrage(db, trade) is False, "duplicate tx_hash rejected -> False")
    summ = _one(db, "SELECT total_trades FROM revenue_summary WHERE project='Aureon' AND chain='arbitrum'")
    check(summ["total_trades"] == 2, "duplicate did not inflate total_trades")

    # ── record_withdrawal writes a row and the trigger sums total_withdrawn ──
    wd = {
        "project": "Aureon", "chain": "arbitrum", "token": "USDC",
        "amount": 6.0, "from_contract": "0xContract", "to_address": "0xWallet",
        "tx_hash": "0xwd1", "gas_cost": 0.25,
    }
    check(record_withdrawal(db, wd) is True, "record_withdrawal returns True")
    summ = _one(db, "SELECT total_withdrawn FROM revenue_summary WHERE project='Aureon' AND chain='arbitrum'")
    check(abs(summ["total_withdrawn"] - 6.0) < 1e-9, "withdrawal trigger sets total_withdrawn == 6.0")

    # ── views return the recorded data ──
    vrow = _one(db, "SELECT * FROM vw_profit_by_chain WHERE project='Aureon' AND chain='arbitrum'")
    check(vrow is not None and vrow["success_rate_pct"] == 100.0, "vw_profit_by_chain shows 100% success rate")
    trow = _one(db, "SELECT * FROM vw_all_projects_total WHERE project='Aureon'")
    check(trow is not None and abs(trow["total_net_profit"] - 10.0) < 1e-9, "vw_all_projects_total aggregates project")

    # a pending (non-success) withdrawal surfaces in vw_pending_withdrawals
    _exec(db,
          "INSERT INTO withdrawals (project, chain, token, amount, from_contract, "
          "to_address, tx_hash, status) VALUES "
          "('Aureon','arbitrum','USDC',1.0,'0xC','0xW','0xwd2','pending')")
    prow = _one(db, "SELECT * FROM vw_pending_withdrawals WHERE tx_hash='0xwd2'")
    check(prow is not None, "vw_pending_withdrawals surfaces non-success withdrawals")

    # ── get_database_path locates a .db under <project>/data/ ──
    proj = tempfile.mkdtemp()
    os.makedirs(os.path.join(proj, "data"))
    open(os.path.join(proj, "data", "found.db"), "w").close()
    check(get_database_path(proj) == os.path.join(proj, "data", "found.db"),
          "get_database_path finds the db in <project>/data/")
    check(get_database_path(os.path.join(proj, "nope")) is None,
          "get_database_path returns None for a missing project dir")

    # ── recording against a nonexistent db fails gracefully (no crash) ──
    check(record_flash_arbitrage("/nonexistent/none.db", trade) is False,
          "recording to a missing db returns False, doesn't crash")

    os.remove(db)
    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
