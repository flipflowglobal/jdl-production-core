"""
Tests for the engine's live-swarm wiring (build_live_coordinator + multi-wallet
execution lanes), with the live quoter and executor replaced by fakes so this
runs with no RPC/network. Verifies:
  - single-wallet fallback when SWARM_KEYS is unset (unchanged behavior)
  - multi-wallet lanes are built and execution is routed to the correct
    priv_key/contract pair per lane
  - a bad SWARM_KEYS/SWARM_CONTRACTS config degrades to the single-wallet
    fallback instead of crashing the coordinator
  - the risk gate blocks swarm broadcasts when a limit is breached

Run: cd python && python3 jdl_flash/test_swarm_wiring.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jdl_flash.flash_loan_engine as e
from jdl_flash.risk_limits import RiskGovernor
from eth_account import Account

KEY_A = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
KEY_B = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690e"
ADDR_A = Account.from_key(KEY_A).address
ADDR_B = Account.from_key(KEY_B).address
CONTRACT_A = "0x0000000000000000000000000000000000AAAA"
CONTRACT_B = "0x0000000000000000000000000000000000BBBB"


class FakeQuoter:
    """Stands in for UniV3Quoter: WETH round trips are profitable, others break even."""
    def __init__(self, w3=None):
        pass

    def ready(self):
        return True

    def quote(self, token_in, token_out, amount_in, fee):
        # crude but deterministic: profitable only for the WETH leg pair
        if token_in == e.USDC_NATIVE and token_out == e.WETH_ARB_T:
            return int(amount_in / 2000 * 1.01)  # nets ~1% after round trip
        if token_in == e.WETH_ARB_T and token_out == e.USDC_NATIVE:
            return int(amount_in * 2000 * 1.01)
        return amount_in  # everything else breaks even


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  ✓ {msg}")
        else:
            failed += 1; print(f"  ✗ {msg}")

    # Patch out the live quoter everywhere build_live_coordinator uses it.
    e.UniV3Quoter = FakeQuoter
    e.WEB3_OK = True

    # Risk gate: give these lane-routing cases a permissive, in-memory governor.
    # Two reasons this must be injected rather than left to the default —
    #   1. the default writes to the operator's real ~/.flash_loan_engine/flash.db,
    #      and a test run must never touch live risk state;
    #   2. CONFIG_OK is derived from the ambient environment at import, so a
    #      malformed .env on the machine running the tests would (correctly)
    #      block every broadcast and make this suite fail for the wrong reason.
    # The gate's own behaviour is covered by test_risk_limits.py and by case 4 below.
    # `_receipt_status` returns None here (no live w3), so every broadcast counts
    # as a failure — hence a failure threshold high enough not to trip mid-run.
    def permissive_governor():
        return RiskGovernor(db_path=":memory:", max_consecutive_failures=10**6,
                            max_daily_loss_usd=10**9, max_notional_usd=10**9,
                            min_profit_usd=0.0, config_ok=True)

    e.build_risk_governor = permissive_governor
    e.MIN_PROFIT_USD = 0.0

    calls = []

    class FakeExecutor:
        def send(self, opp, priv_key=None, contract=None):
            calls.append((priv_key, contract))
            return f"0xhash{len(calls)}"

    e.NexusExecutor = FakeExecutor
    e.GELATO_ENABLED = False

    # ── 1) single-wallet fallback (SWARM_KEYS unset) ──
    e.SWARM_KEYS = ""
    e.SWARM_CONTRACTS = ""
    e.PRIV_KEY = KEY_A
    e.CONTRACT = CONTRACT_A
    coord = e.build_live_coordinator(feed=None, loan_usd=10000.0, execute=True)
    check(coord is not None, "coordinator builds with fake quoter")
    check(coord.n_wallets == 1, "no SWARM_KEYS -> single fallback lane")

    swarm = coord.make_swarm(n_workers=2)
    coord.reset_dedup()
    asyncio.run(swarm.run(rounds=2, interval=0.0))
    check(len(calls) >= 1, f"single-wallet path executed at least once (got {len(calls)})")
    check(all(pk == KEY_A and c == CONTRACT_A for pk, c in calls),
          "single-wallet path always uses the fallback wallet/contract")

    # ── 2) multi-wallet lanes ──
    calls.clear()
    e.SWARM_KEYS = f"{KEY_A},{KEY_B}"
    e.SWARM_CONTRACTS = f"{CONTRACT_A},{CONTRACT_B}"
    coord2 = e.build_live_coordinator(feed=None, loan_usd=10000.0, execute=True)
    check(coord2.n_wallets == 2, "SWARM_KEYS with 2 entries -> 2 lanes")

    swarm2 = coord2.make_swarm(n_workers=4)
    coord2.reset_dedup()
    asyncio.run(swarm2.run(rounds=3, interval=0.0))
    used_pairs = set(calls)
    valid_pairs = {(KEY_A, CONTRACT_A), (KEY_B, CONTRACT_B)}
    check(used_pairs.issubset(valid_pairs) and len(used_pairs) >= 1,
          f"execution only ever used configured (key,contract) pairs: {used_pairs}")
    # no cross-wiring: lane A's key never paired with lane B's contract or vice versa
    check(not any((pk == KEY_A and c == CONTRACT_B) or (pk == KEY_B and c == CONTRACT_A)
                  for pk, c in calls),
          "no cross-wiring between lanes (key always paired with its OWN contract)")

    # ── 3) bad config degrades gracefully to single-wallet fallback ──
    # 2 keys but 3 contracts: lengths don't match 1:1 and there isn't exactly one
    # contract to reuse -> LaneConfigError, which build_live_coordinator catches.
    calls.clear()
    e.SWARM_KEYS = f"{KEY_A},{KEY_B}"
    e.SWARM_CONTRACTS = f"{CONTRACT_A},{CONTRACT_B},{CONTRACT_A}"
    coord3 = e.build_live_coordinator(feed=None, loan_usd=10000.0, execute=True)
    check(coord3.n_wallets == 1, "malformed SWARM_CONTRACTS falls back to single wallet, not a crash")
    check(coord3 is not None, "coordinator still builds despite bad lane config")

    # ── 4) the risk gate actually stops the unattended swarm ──
    # The swarm daemon runs with no operator watching, so the gate is the only
    # thing that can stop a systematically-reverting route from burning gas
    # around the clock. Verify it reaches the broadcast path, not just cycle_run.
    calls.clear()
    e.SWARM_KEYS = ""
    e.SWARM_CONTRACTS = ""
    e.PRIV_KEY = KEY_A
    e.CONTRACT = CONTRACT_A

    def blocked_governor():
        # An already-open circuit breaker: max_consecutive_failures=1 and one
        # recorded failure, so check() refuses everything for the cooldown.
        g = RiskGovernor(db_path=":memory:", max_consecutive_failures=1,
                         cooldown_base_s=3600.0, min_profit_usd=0.0, config_ok=True)
        g.record_failure(0.0, "pre-existing failure")
        return g

    e.build_risk_governor = blocked_governor
    coord4 = e.build_live_coordinator(feed=None, loan_usd=10000.0, execute=True)
    swarm4 = coord4.make_swarm(n_workers=2)
    coord4.reset_dedup()
    asyncio.run(swarm4.run(rounds=2, interval=0.0))
    check(len(calls) == 0, f"an open circuit breaker stops every swarm broadcast (got {len(calls)})")

    def config_blocked_governor():
        return RiskGovernor(db_path=":memory:", min_profit_usd=0.0, config_ok=False)

    calls.clear()
    e.build_risk_governor = config_blocked_governor
    coord5 = e.build_live_coordinator(feed=None, loan_usd=10000.0, execute=True)
    swarm5 = coord5.make_swarm(n_workers=2)
    coord5.reset_dedup()
    asyncio.run(swarm5.run(rounds=2, interval=0.0))
    check(len(calls) == 0, f"unparseable config stops every swarm broadcast (got {len(calls)})")

    calls.clear()
    e.build_risk_governor = permissive_governor
    coord6 = e.build_live_coordinator(feed=None, loan_usd=10000.0, execute=True)
    swarm6 = coord6.make_swarm(n_workers=2)
    coord6.reset_dedup()
    asyncio.run(swarm6.run(rounds=2, interval=0.0))
    check(len(calls) >= 1, "a permissive governor lets the same route through (the block was the gate, not the fake)")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
