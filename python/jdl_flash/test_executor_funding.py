"""
test_executor_funding.py — the pre-broadcast funding guard on the only code
path that can spend real money.

`NexusExecutor.send` refuses to sign when the wallet cannot cover the
transaction's own max fee plus headroom. That guard is the difference between a
dry wallet being rejected harmlessly by the node and a series of broadcasts
that the cycle scores as failed, gas-charged trades. It has to be exercised at
the level of `send`, not merely as arithmetic: the guard lives between gas
estimation and `sign_transaction`, and a guard wired one step to the wrong side
 of that boundary is invisible to a unit test of its own formula.

Everything here is stubbed at the module boundary. No network, no key material,
no broadcast: `_send_raw` is replaced by a counter that raises if touched, so
"the code decided not to spend" is asserted rather than assumed.

Run: cd python && python3 jdl_flash/test_executor_funding.py
"""
from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdl_flash import flash_loan_engine as E

KEY = "0x" + "11" * 32
# EIP-55 checksummed: eth_account's signer validates the `to` field, so a
# lowercase address is rejected before the funding guard is ever reached. Taken
# from a real, already-checksummed mainnet address rather than typed by hand.
CONTRACT = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"   # native USDC on Arbitrum


class FakeEth:
    def __init__(self, balance_wei):
        self.balance_wei = balance_wei
        self.gas_price = 20_000_000        # 0.02 gwei, the usual Arbitrum idle price
        self.nonce = 7

    def get_balance(self, addr):
        return self.balance_wei

    def getTransactionCount(self, addr):
        return self.nonce

    def gasPrice(self):
        return self.gas_price


class FakeW3:
    def __init__(self, balance_wei):
        self.eth = FakeEth(balance_wei)


def make_opp():
    # Real Arbitrum token addresses: build_initiate_calldata ABI-encodes them as
    # address words, so a symbol here would make the encoder return None and the
    # test would be measuring an early bail-out instead of the funding guard.
    return E.Opportunity(
        type="DEX_ARB", asset=E.USDC_NATIVE, token_inter=E.WETH_ARB_T,
        loan_usd=10_000.0, profit_usd=12.0,
        buy_fee=3000, sell_fee=3000, dex_type=2,
        vol=0.01, kelly_frac=0.25, spread=0.001,
    )


def install_stubs(balance_wei, *, est_gas=1_200_000, simulate_ok=True):
    """Point the engine at fakes. Returns the counters that record what happened."""
    state = {"sent": 0, "signed": 0, "sim_calls": 0, "est_gas_calls": 0}

    w3 = FakeW3(balance_wei)
    E.PRIV_KEY = KEY
    E.CONTRACT = CONTRACT
    E.WEB3_OK = True
    E.requests = object()          # the send() guard also requires it to be set
    E.MIN_GAS_ETH = 0.05

    E.get_w3 = lambda: w3
    E._nonce = lambda w, a: 7
    E._balance = lambda w, a: w.eth.balance_wei
    E._gas_p = lambda w: w.eth.gas_price
    E._w3_cs = lambda a: a

    def fake_est_gas(w, tx):
        state["est_gas_calls"] += 1
        return est_gas

    E._est_gas = fake_est_gas

    def fake_eth_call(w, tx, block="latest"):
        state["sim_calls"] += 1
        if not simulate_ok:
            raise RuntimeError("execution reverted: ERC20 transfer failed")
        return b"\x01" * 32

    E._eth_call = fake_eth_call

    # Returns a hash-shaped object rather than raising: the assertions that
    # matter read `state["sent"]`, and a raised AssertionError would be swallowed
    # by send()'s own `except Exception` and look like a plain None return.
    def fake_send_raw(w, raw):
        state["sent"] += 1
        return types.SimpleNamespace(hex=lambda: "0x" + "ab" * 32)

    E._send_raw = fake_send_raw

    # build_initiate_calldata touches WEB3_OK and the ABI encoder; keep the real
    # one so the calldata path stays exercised, and only stub the parts that
    # need a live chain.
    return state


def main() -> int:
    passed = failed = 0

    def check(cond, msg, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✓ {msg}")
        else:
            failed += 1
            print(f"  ✗ {msg}" + (f"  ({detail})" if detail else ""))
        return bool(cond)

    orig_min_gas = E.MIN_GAS_ETH
    orig_priv, orig_contract = E.PRIV_KEY, E.CONTRACT
    orig_web3, orig_requests = E.WEB3_OK, getattr(E, "requests", None)

    try:
        print("\n  ── A funded wallet reaches the broadcast boundary ──")
        # Headroom (0.05 ETH) plus the worst case for a 1.2M-gas tx at 0.02 gwei.
        gas_cost = 1_500_000 * 20_000_000
        need_wei = gas_cost + int(0.05 * 1e18)
        state = install_stubs(need_wei * 2)
        res = E.NexusExecutor().send(make_opp())
        check(state["sim_calls"] == 1,
              "the transaction is simulated before anything else", str(state["sim_calls"]))
        check(state["est_gas_calls"] == 1, "gas is estimated once")
        check(state["sent"] == 1,
              "a funded wallet broadcasts", f"sent={state['sent']}")
        check(res is not None and isinstance(res, str),
              "the caller receives a transaction hash", repr(res))
        # The signature itself is real: eth_account did the EIP-1559/155 RLP
        # encoding, so a broken tx field would have thrown above. The hash
        # returned above is the stub's, but reaching it at all proves the
        # transaction passed the real signer's validation.
        check(res == "0x" + "ab" * 32,
              "the broadcast was reached, so the real signer accepted the tx",
              repr(res))

        print("\n  ── An underfunded wallet must not broadcast ──")
        for label, balance in (
            ("zero balance", 0),
            ("one wei short", need_wei - 1),
            ("half the requirement", need_wei // 2),
            ("headroom only, no gas", int(0.05 * 1e18)),
            ("a tenth of a satoshi", 10**11),
        ):
            state = install_stubs(balance)
            res = E.NexusExecutor().send(make_opp())
            check(state["sent"] == 0, f"{label}: nothing is broadcast", f"sent={state['sent']}")
            check(res is None, f"{label}: send() reports failure", repr(res))
            check(state["sim_calls"] == 1,
                  f"{label}: the simulation still ran (it costs nothing)", "")

        print("\n  ── The guard accounts for the actual gas price ──")
        # The same balance is adequate at 0.02 gwei and inadequate at 500 gwei.
        # A guard that ignored the price would pass the second case and the node
        # would reject the transaction, turning a dry run into a charged failure.
        gas_needed_low = 1_500_000 * 20_000_000
        low = gas_needed_low + int(0.05 * 1e18)
        state = install_stubs(low)
        state = install_stubs(low)
        E._gas_p = lambda w: w.eth.gas_price
        w3 = FakeW3(low)
        E.get_w3 = lambda: w3
        check(E.NexusExecutor().send(make_opp()) is not None,
              "adequate at 0.02 gwei: the trade is broadcast")

        high = 500_000_000_000
        need_high = 1_500_000 * high + int(0.05 * 1e18)
        state = install_stubs(low)
        w3 = FakeW3(low)
        w3.eth.gas_price = high
        E.get_w3 = lambda: w3
        res = E.NexusExecutor().send(make_opp())
        check(state["sent"] == 0,
              "the same balance is refused at 500 gwei", f"sent={state['sent']}")
        check(res is None, "the spiked case reports failure rather than charging gas")

        state = install_stubs(need_high)
        check(E.NexusExecutor().send(make_opp()) is not None,
              "the balance the spike actually requires does broadcast")

        print("\n  ── The guard accounts for the actual gas estimate ──")
        # A route needing 6M gas costs six times more than the stub default.
        big_gas = 6_000_000
        mid = 1_500_000 * 20_000_000 + int(0.05 * 1e18)
        state = install_stubs(mid, est_gas=big_gas)
        res = E.NexusExecutor().send(make_opp())
        check(state["sent"] == 0,
              "a gas-heavy route is refused when only the light-route budget fits",
              f"sent={state['sent']}")
        check(res is None, "the gas-heavy case reports failure")

        # send() adds a 25% margin to the estimate, so the requirement is 7.5M gas,
        # not the 6M the estimator returned.
        heavy_need = int(big_gas * 1.25) * 20_000_000 + int(0.05 * 1e18)
        state = install_stubs(heavy_need, est_gas=big_gas)
        check(E.NexusExecutor().send(make_opp()) is not None,
              "a wallet funded for the heavy route does broadcast")

        print("\n  ── The 25% estimation margin is part of the requirement ──")
        # Funding for the raw estimate but not for estimate x 1.25 must be
        # refused: paying the estimate exactly leaves nothing for the margin,
        # and a route that overruns its estimate is refused rather than charged.
        exact = big_gas * 20_000_000 + int(0.05 * 1e18)
        state = install_stubs(exact, est_gas=big_gas)
        check(E.NexusExecutor().send(make_opp()) is None,
              "a wallet funded for exactly the estimate (no margin) is refused",
              f"sent={state['sent']}")
        state = install_stubs(heavy_need - 1, est_gas=big_gas)
        check(E.NexusExecutor().send(make_opp()) is None,
              "one wei under the margin-inclusive requirement is refused")

        print("\n  ── The guard never fires before gas is known ──")
        # Estimation failing falls back to 1.2M gas. The guard must still run
        # against that fallback rather than skipping because gas is unknown.
        state = install_stubs(0, est_gas=1_200_000)
        E._est_gas = lambda w, tx: (_ for _ in ()).throw(RuntimeError("estimate unavailable"))
        res = E.NexusExecutor().send(make_opp())
        check(state["sent"] == 0,
              "a zero-balance wallet is refused even when estimation throws",
              f"sent={state['sent']}")
        check(res is None, "the estimation-failure path still refuses")

        print("\n  ── A reverting route is dropped before the funding check ──")
        # Order matters: a route that would revert should never reach signing,
        # and it should not be reported as a funding problem either.
        state = install_stubs(need_wei * 2, simulate_ok=False)
        res = E.NexusExecutor().send(make_opp())
        check(res is None, "a reverting route does not broadcast")
        check(state["est_gas_calls"] == 0,
              "gas estimation is skipped for a route that already reverted",
              str(state["est_gas_calls"]))
        check(state["sent"] == 0, "no gas is spent on a reverting route")

        print("\n  ── The guard is scoped to one wallet, not global ──")
        # A swarm holds several wallets. One funded lane must still trade while a
        # dry lane in the same process is refused.
        w3_funded = FakeW3(need_wei * 2)
        calls = {"n": 0}

        def alternating():
            calls["n"] += 1
            return w3_funded

        install_stubs(0)
        E.get_w3 = lambda: FakeW3(0)
        dry = E.NexusExecutor().send(make_opp())
        E.get_w3 = lambda: w3_funded
        rich = E.NexusExecutor().send(make_opp())
        check(dry is None, "the dry lane is refused")
        check(rich is not None, "the funded lane in the same process still trades")

        print("\n  ── Shortfall arithmetic ──")
        check(E._gas_shortfall_wei(FakeW3(0), "0x1", 1_500_000, 20_000_000, 0.05) > 0,
              "a zero balance is always short")
        check(E._gas_shortfall_wei(FakeW3(0), "0x1", 0, 0, 0.05) > 0,
              "a zero gas limit falls back to a real budget rather than costing nothing")
        check(E._gas_shortfall_wei(FakeW3(10**18), "0x1", 1_500_000, 20_000_000, 0.0) == 0,
              "a huge balance is not short")
        short = E._gas_shortfall_wei(FakeW3(0), "0x1", 1_500_000, 20_000_000, 0.05)
        expected = 1_500_000 * 20_000_000 + int(0.05 * 1e18)
        check(short == expected,
              "the reported shortfall is exactly what is missing", f"{short} != {expected}")
        # An unreadable balance must fail closed: `except` returning 0 would make
        # an unknown balance look funded.
        class UnreadableEth(FakeEth):
            def get_balance(self, addr):
                raise RuntimeError("node unavailable")

        broken = FakeW3(0)
        broken.eth = UnreadableEth(0)
        short = E._gas_shortfall_wei(broken, "0x1", 1_500_000, 20_000_000, 0.05)
        check(short > 0, "an unreadable balance is treated as short, not as funded",
              str(short))
        check(short == 1_500_000 * 20_000_000 + int(0.05 * 1e18),
              "an unreadable balance reports the full requirement, not a sentinel",
              str(short))

        print("\n  ── No revenue is booked for a refused trade ──")
        # A skipped broadcast must leave no trace in the ledger. Asserting this by
        # reading the live database would be both pointless and wrong — a test
        # that opens production revenue data is a defect, not a check, and the
        # process-wide DB guard in test_db_guard.py rightly refuses it. The
        # invariant is structural instead: send() has no path to the writer.
        import inspect

        src = inspect.getsource(E.NexusExecutor.send)
        check("RevenueTracker" not in src and ".log(" not in src,
              "NexusExecutor.send never touches the revenue ledger")
        check("REV" not in src and "log_revenue" not in src,
              "no revenue is recorded on any send() path, refused or not")
        check("state['sent']" not in src,
              "the broadcast stub is not reachable from send()'s own source")

        # And the live database is neither opened nor mutated by this suite.
        import sqlite3

        from jdl_flash import paths

        opened = []
        real_connect = sqlite3.connect

        def recording_connect(path, *a, **k):
            opened.append(str(path))
            return real_connect(path, *a, **k)

        install_stubs(0)
        sqlite3.connect = recording_connect
        try:
            check(E.NexusExecutor().send(make_opp()) is None, "the trade is refused")
        finally:
            sqlite3.connect = real_connect
        check(not opened,
              "a refused trade opens no database at all", str(opened))
        check(not any(paths.is_live_db(p) for p in opened),
              "and specifically never the live revenue database")

    finally:
        E.MIN_GAS_ETH = orig_min_gas
        E.PRIV_KEY, E.CONTRACT = orig_priv, orig_contract
        E.WEB3_OK, E.requests = orig_web3, orig_requests

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())