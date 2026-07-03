"""
Tests for revenue_reconciliation.py — fully offline (fake eth_call, temp sqlite
db built with the REAL revenue_log schema from flash_loan_engine.init_db()).
Run: cd python && python3 jdl_flash/test_revenue_reconciliation.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdl_flash import revenue_reconciliation as rr

CONTRACT = "0x0000000000000000000000000000000000FEED"
TOKENS = {
    "USDC": ("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6),
    "WETH": ("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", 18),
}


def _balance_word(raw_amount: int) -> bytes:
    return raw_amount.to_bytes(32, "big")


def _make_db(revenue_rows=()):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    # Mirror the REAL schema from flash_loan_engine.init_db()'s revenue_log table.
    con.execute("""
        CREATE TABLE revenue_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            source    TEXT,
            amount_usd REAL,
            token     TEXT,
            chain     TEXT,
            tx_hash   TEXT,
            timestamp TEXT
        );
    """)
    for source, amount, token, chain, tx, ts in revenue_rows:
        con.execute(
            "INSERT INTO revenue_log (source, amount_usd, token, chain, tx_hash, timestamp) VALUES (?,?,?,?,?,?)",
            (source, amount, token, chain, tx, ts),
        )
    con.commit()
    con.close()
    return path


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  ✓ {msg}")
        else:
            failed += 1; print(f"  ✗ {msg}")

    # ── eth_call encoding correctness ──
    calls = []
    def fake_eth_call_zero(tx):
        calls.append(tx)
        return _balance_word(0)

    db_path = _make_db()
    engine = rr.RevenueReconciliationEngine(fake_eth_call_zero, db_path, TOKENS)
    bal = engine.get_on_chain_balance(CONTRACT, "USDC")
    check(bal is not None and bal.on_chain_raw == 0, "zero balance decodes correctly")
    tx = calls[0]
    check(tx["to"] == TOKENS["USDC"][0], "eth_call targets the correct token address")
    check(tx["data"].startswith("0x70a08231"), "calldata uses the balanceOf(address) selector")
    check(tx["data"] == "0x70a08231" + CONTRACT[2:].lower().rjust(64, "0"),
          "calldata correctly left-pads the holder address to 32 bytes")

    # ── all-zero balances -> OK ──
    result = engine.reconcile(CONTRACT)
    check(result.status == "OK", "all-zero balances -> OK status")
    check(result.discrepancies == {}, "no discrepancies when balances are zero")

    # ── one token stuck above threshold -> WARNING ──
    def fake_eth_call_one_stuck(tx):
        if TOKENS["WETH"][0].lower() in tx["data"].lower() or tx["to"] == TOKENS["WETH"][0]:
            return _balance_word(int(2.5 * 10**18))  # 2.5 WETH stuck
        return _balance_word(0)

    engine2 = rr.RevenueReconciliationEngine(fake_eth_call_one_stuck, db_path, TOKENS)
    result2 = engine2.reconcile(CONTRACT, warn_usd=1.0)
    check(result2.status == "WARNING", "one stuck token (not all) -> WARNING")
    check("WETH" in result2.discrepancies and abs(result2.discrepancies["WETH"] - 2.5) < 1e-9,
          "stuck WETH amount reported correctly in human units")
    check("USDC" not in result2.discrepancies, "clean token not flagged")

    # ── every token stuck -> ERROR ──
    def fake_eth_call_all_stuck(tx):
        # Decimal-appropriate raw amounts per token so BOTH clear the $1 threshold
        # (USDC has 6 decimals, WETH has 18 — the same raw integer would mean
        # wildly different human amounts across them).
        if tx["to"] == TOKENS["WETH"][0]:
            return _balance_word(2 * 10**18)   # 2 WETH
        return _balance_word(5 * 10**6)         # 5 USDC

    engine3 = rr.RevenueReconciliationEngine(fake_eth_call_all_stuck, db_path, TOKENS)
    result3 = engine3.reconcile(CONTRACT, warn_usd=1.0)
    check(result3.status == "ERROR", "every tracked token stuck -> ERROR")
    check(len(result3.discrepancies) == len(TOKENS), "all tokens flagged in ERROR state")

    # ── dust below threshold is NOT flagged ──
    def fake_eth_call_dust(tx):
        return _balance_word(1)  # 1 wei-equivalent raw unit — dust

    engine4 = rr.RevenueReconciliationEngine(fake_eth_call_dust, db_path, TOKENS)
    result4 = engine4.reconcile(CONTRACT, warn_usd=1.0)
    check(result4.status == "OK", "sub-dust balances don't trigger a warning")

    # ── db_recorded_usd reads the REAL revenue_log schema correctly ──
    db_with_rows = _make_db(revenue_rows=[
        ("swarm", 12.5, "USDC", "arbitrum", "0xabc", "2026-01-01T00:00:00Z"),
        ("swarm", 7.25, "USDC", "arbitrum", "0xdef", "2026-01-02T00:00:00Z"),
        ("swarm", 3.0, "WETH", "arbitrum", "0x123", "2026-01-03T00:00:00Z"),
    ])
    engine5 = rr.RevenueReconciliationEngine(fake_eth_call_zero, db_with_rows, TOKENS)
    usdc_recorded = engine5.get_db_recorded_usd("USDC")
    weth_recorded = engine5.get_db_recorded_usd("WETH")
    check(abs(usdc_recorded - 19.75) < 1e-9, f"sums revenue_log USDC rows correctly (got {usdc_recorded})")
    check(abs(weth_recorded - 3.0) < 1e-9, f"sums revenue_log WETH rows correctly (got {weth_recorded})")

    # ── missing db file doesn't crash, returns 0 ──
    engine6 = rr.RevenueReconciliationEngine(fake_eth_call_zero, "/nonexistent/path.db", TOKENS)
    check(engine6.get_db_recorded_usd("USDC") == 0.0, "missing db file returns 0.0, doesn't crash")

    # ── balanceOf call failure (RPC error) is handled gracefully ──
    def fake_eth_call_fails(tx):
        raise ConnectionError("rpc down")
    engine7 = rr.RevenueReconciliationEngine(fake_eth_call_fails, db_path, TOKENS)
    result7 = engine7.reconcile(CONTRACT)
    check(result7.balances == {}, "RPC failure skips tokens rather than crashing")
    check(result7.status == "OK", "no balances read -> OK (nothing confirmed stuck)")

    for p in (db_path, db_with_rows):
        os.remove(p)

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
