"""
test_readiness.py — the go-live preflight must be correct in both directions.

Two failure modes matter and they pull in opposite directions. A preflight that
reports READY when the system cannot trade is the dangerous one: it would send
an operator to set LIVE_EXECUTION=1 against an empty receiver address. A
preflight that reports BLOCKED for cosmetic reasons is merely annoying — it
costs a command, never money. The suite is therefore weighted toward proving
that nothing insufficient can ever produce READY.

Everything runs against stub engine objects. No network, no key material, no
database: a preflight that needed any of those to be *tested* would be untestable
in CI, which is where it most needs to be trusted.

Run: cd python && python3 jdl_flash/test_readiness.py
"""
from __future__ import annotations

import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdl_flash import readiness as R
from jdl_flash.readiness import (
    Check,
    ReadinessReport,
    ReadinessVerdict,
    assess_readiness,
    render_json,
    render_text,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class FakeEth:
    """Minimal stand-in for web3's eth namespace."""

    def __init__(self, *, chain_id=42161, block=510_000_000, gas_price=2e7,
                 balances=None, codes=None):
        self.chain_id = chain_id
        self.block_number = block
        self.gas_price = gas_price
        self._balances = balances or {}
        self._codes = codes or {}
        self.get_code_calls = []
        self.get_balance_calls = []

    def get_balance(self, addr):
        self.get_balance_calls.append(addr)
        return self._balances.get(addr.lower(), 0)

    def get_code(self, addr):
        self.get_code_calls.append(addr)
        return self._codes.get(addr.lower(), "0x")

    def gasPrice(self):  # web3 v5 spelling
        return self.gas_price


class FakeW3:
    def __init__(self, eth):
        self.eth = eth

    def is_connected(self):
        return True


def make_engine(**overrides):
    """A stub engine module. Every default is a *working* configuration, so a
    test only has to state the single thing it wants broken."""
    eth = FakeW3(FakeEth())
    engine = types.SimpleNamespace(
        CONFIG_OK=True,
        CONFIG_ISSUES=[],
        WEB3_OK=True,
        CHAIN_ID=42161,
        _SUPPORTED_CHAIN_IDS=(42161, 421614),
        ACTIVE_RPC="https://arb-mainnet.example/v2/abcdef0123456789abcdef0123456789",
        W3=eth,
        get_w3=lambda: eth,
        _chain_id=lambda w: w.eth.chain_id,
        _blk=lambda w: w.eth.block_number,
        _gas_p=lambda w: w.eth.gas_price,
        _balance=lambda w, a: w.eth.get_balance(a),
        _w3_cs=lambda a: a,
        PRIV_KEY="",
        WALLET="0x000000000000000000000000000000000000dEaD",
        CONTRACT="",
        GELATO_ENABLED=False,
        LIVE_EXEC=False,
        MIN_GAS_ETH=0.05,
        MIN_PROFIT_USD=2.5,
        REAL_LOAN_USD=10_000.0,
        ALLOW_SIM=False,
        IS_TESTNET=False,
        USE_REAL_QUOTES=True,
    )
    for key, value in overrides.items():
        setattr(engine, key, value)
    return engine


def deploy_receiver(engine, address="0x000000000000000000000000000000000000AbC0"):
    """Give the stub a deployed receiver so only one thing is ever blocking."""
    engine.CONTRACT = address
    engine.W3.eth._codes[address.lower()] = "0x6080604052" + "00" * 40
    return engine


def fund(engine, eth_amount, address=None):
    addr = (address or engine.WALLET).lower()
    engine.W3.eth._balances[addr] = int(eth_amount * 10**18)
    return engine


def ready_engine():
    """An engine where every blocking condition genuinely holds."""
    e = deploy_receiver(make_engine())
    fund(e, 1.0)
    e.LIVE_EXEC = True
    e.PRIV_KEY = "0x" + "11" * 32
    return e


# ---------------------------------------------------------------------------
# Suite
# ---------------------------------------------------------------------------


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

    def names(report):
        return [c.name for c in report.checks]

    def blocked_on(report):
        return [c.name for c in report.blockers]

    print("\n  ── Happy path: a fully configured engine is READY ──")
    rep = assess_readiness(ready_engine())
    check(rep.ready, "a fully funded, deployed, consented engine reports READY",
          f"verdict={rep.verdict} blockers={blocked_on(rep)}")
    check(rep.verdict == ReadinessVerdict.READY, "verdict is exactly READY")
    check(not rep.blockers, "no blockers on the happy path")
    check("receiver" in names(rep) and "funding" in names(rep),
          "receiver and funding checks both ran")
    check(rep.chain_label == "Arbitrum One", "chain label resolved",
          rep.chain_label)
    check(rep.check("funding").ok, "funding check passed on a funded wallet")

    print("\n  ── Every blocker must actually block ──")
    # Each case breaks exactly one precondition; the verdict must be BLOCKED and
    # must name that precondition.
    cases = [
        ("no receiver deployed", {"CONTRACT": ""}, "receiver"),
        ("receiver set but undeployed", {"CONTRACT": "0x" + "ab" * 20}, "receiver"),
        ("no signing key", {"PRIV_KEY": ""}, "signing_key"),
        ("no gas funding", {"_balances": {}}, "funding"),
        ("live exec off", {"LIVE_EXEC": False}, "live_exec"),
        ("unparseable config", {"CONFIG_OK": False, "CONFIG_ISSUES": ["bad"]}, "config"),
        ("web3 import failed", {"WEB3_OK": False}, "web3"),
        ("unsupported chain", {"CHAIN_ID": 137}, "chain_supported"),
        ("zero profit floor", {"MIN_PROFIT_USD": 0.0}, "profit_floor"),
        ("negative profit floor", {"MIN_PROFIT_USD": -1.0}, "profit_floor"),
    ]
    for label, overrides, expected in cases:
        e = ready_engine()
        # Balances are overridden on the eth object, not the engine namespace.
        if "_balances" in overrides:
            e.W3.eth._balances = {}
            overrides = {k: v for k, v in overrides.items() if k != "_balances"}
        for k, v in overrides.items():
            setattr(e, k, v)
        r = assess_readiness(e)
        names_blocked = blocked_on(r)
        check(not r.ready, f"{label}: verdict is BLOCKED", f"got {r.verdict}")
        check(expected in names_blocked,
              f"{label}: '{expected}' is named as a blocker", f"blockers={names_blocked}")

    print("\n  ── Chain / endpoint mismatch is caught ──")
    e = ready_engine()
    e.W3.eth.chain_id = 421614          # endpoint is Sepolia, config says mainnet
    r = assess_readiness(e)
    check(not r.ready, "endpoint on the wrong chain is BLOCKED")
    check("rpc" in blocked_on(r), "the mismatch is attributed to the rpc check",
          f"blockers={blocked_on(r)}")

    e = ready_engine()
    e.W3 = None
    e.get_w3 = lambda: None
    r = assess_readiness(e)
    check(not r.ready, "no RPC connection is BLOCKED")
    check("rpc" in blocked_on(r), "missing connection attributed to rpc",
          f"blockers={blocked_on(r)}")

    print("\n  ── Simulation must be impossible on mainnet ──")
    e = ready_engine()
    e.ALLOW_SIM = True
    e.IS_TESTNET = False
    r = assess_readiness(e)
    check(not r.ready, "ALLOW_SIM on mainnet is BLOCKED")
    check("sim_policy" in blocked_on(r), "the invariant is attributed to sim_policy",
          f"blockers={blocked_on(r)}")
    e = ready_engine()
    e.ALLOW_SIM = True
    e.IS_TESTNET = True
    e.CHAIN_ID = 421614
    r = assess_readiness(e)
    check(r.check("sim_policy").ok, "simulation on Sepolia is allowed by design")

    print("\n  ── Readiness and the executor must measure funding identically ──")
    # The executor refuses to sign when balance < gas_limit*gas_price + headroom.
    # A readiness check comparing against the headroom alone would call a wallet
    # READY that the executor then rejects on its very first trade.
    e = ready_engine()
    e.LIVE_EXEC = True
    e.W3.eth.gas_price = 500_000_000_000   # 500 gwei: a real spike, not the 0.02 idle price
    fund(e, R._READINESS_GAS_LIMIT * 500_000_000_000 / 1e18 * 0.5)
    r = assess_readiness(e)
    check(not r.ready, "a wallet covering only half the worst-case tx cost is BLOCKED")
    check("funding" in blocked_on(r),
          "the shortfall is attributed to funding, not reported as a pass",
          f"blockers={blocked_on(r)}")
    check("worst-case tx cost" in r.check("funding").detail,
          "the detail shows the tx cost the operator has to cover",
          r.check("funding").detail)
    check("short by" in r.check("funding").detail,
          "the detail names the exact shortfall")
    check(float(e.MIN_GAS_ETH) > 0, "the fixture's headroom is non-trivial")

    # Exactly at the requirement must pass; one wei under must not. Balances are
    # set in wei so the boundary is exercised exactly, not through a float.
    need_wei = R._READINESS_GAS_LIMIT * 500_000_000_000 + int(e.MIN_GAS_ETH * 1e18)
    e.W3.eth._balances[e.WALLET.lower()] = need_wei
    check(assess_readiness(e).check("funding").ok,
          "a wallet holding exactly the requirement passes")
    e.W3.eth._balances[e.WALLET.lower()] = need_wei - 1
    check(not assess_readiness(e).check("funding").ok,
          "one wei under the requirement fails (no rounding slack)")

    # The engine's own shortfall function must agree with the preflight at every
    # balance. This is the check that keeps the two gates from drifting apart: if
    # either one is edited without the other, one of these five fails.
    from jdl_flash.flash_loan_engine import _gas_shortfall_wei
    for bal_wei in (need_wei, need_wei - 1, need_wei // 2, need_wei // 100, 0):
        e2 = ready_engine()
        e2.LIVE_EXEC = True
        e2.W3.eth.gas_price = 500_000_000_000
        e2.W3.eth._balances[e2.WALLET.lower()] = bal_wei
        executor_short = _gas_shortfall_wei(
            e2.W3, e2.WALLET, R._READINESS_GAS_LIMIT, 500_000_000_000, 0.05
        )
        preflight_ok = assess_readiness(e2).check("funding").ok
        check(bool(executor_short) != preflight_ok,
              f"preflight and executor agree at {bal_wei / 1e18:.6f} ETH",
              f"short={bool(executor_short)} preflight_ok={preflight_ok}")
        if bal_wei == 0:
            # Zero ETH is only acceptable when the relay pays gas, and the
            # executor's own check is bypassed entirely in that mode.
            e2.GELATO_ENABLED = True
            check(assess_readiness(e2).check("funding").ok,
                  "zero ETH passes only under gasless relay")

    print("\n  ── Gasless execution: no ETH required, but consent still required ──")
    e = ready_engine()
    e.PRIV_KEY = ""
    e.GELATO_ENABLED = True
    e.W3.eth._balances = {}
    e.LIVE_EXEC = False   # consent deliberately withheld
    r = assess_readiness(e)
    check(r.check("funding").ok, "gasless mode passes the funding check with 0 ETH")
    check(not r.check("funding").blocking, "funding is advisory under gasless relay")
    check(not r.ready, "gasless mode still requires live_exec consent")
    check("live_exec" in blocked_on(r), "consent is still demanded",
          f"blockers={blocked_on(r)}")

    e = ready_engine()
    e.PRIV_KEY = ""
    e.GELATO_ENABLED = True
    e.LIVE_EXEC = True
    r = assess_readiness(e)
    check(r.ready, "gasless + funded-free + deployed + consented is READY",
          f"blockers={blocked_on(r)}")

    print("\n  ── No key material in any output path ──")
    secret_key = "0x" + "ab" * 32
    e = ready_engine()
    e.PRIV_KEY = secret_key
    r = assess_readiness(e)
    text = render_text(r)
    blob = render_json(r)
    check(secret_key not in text, "render_text never prints the key")
    check(secret_key not in blob, "render_json never prints the key")
    check(secret_key[2:] not in text and secret_key[2:] not in blob,
          "neither the key nor its bare hex body appears in output")
    # The derived *public* address is fine, and is what should be shown.
    addr = r.check("signing_key").detail
    check("0x" in addr, "the signing-key check still reports something useful")

    e = ready_engine()
    e.ACTIVE_RPC = "https://arb-mainnet.g.alchemy.com/v2/SUPERSECRETKEY1234567890"
    r = assess_readiness(e)
    check("SUPERSECRETKEY1234567890" not in render_text(r),
          "the RPC credential is redacted in text output")
    check("SUPERSECRETKEY1234567890" not in render_json(r),
          "the RPC credential is redacted in JSON output")
    check("alchemy.com" in r.endpoint, "the endpoint host is still shown",
          r.endpoint)

    print("\n  ── A raising check must not hide the others ──")
    def exploding(engine):
        raise RuntimeError("boom")

    e = ready_engine()
    r = assess_readiness(e, checks=[exploding, R._check_live_exec])
    check(len(r.checks) == 2, "both checks are represented", str(len(r.checks)))
    check(not r.ready, "an unknown condition is treated as failed, not skipped")
    check(r.checks[0].ok is False, "the exploded check is marked failed")
    check("boom" in r.checks[0].detail, "the failure reason is preserved",
          r.checks[0].detail)
    check(r.checks[1].name == "live_exec",
          "the check after the explosion still ran", r.checks[1].name)

    print("\n  ── The provider is bound once and threaded through ──")
    e = ready_engine()
    calls = {"get_w3": 0}
    e.W3 = None  # force the report to dial through the getter

    def counting_get_w3():
        calls["get_w3"] += 1
        return e.W3_CACHE

    e.W3_CACHE = FakeW3(FakeEth(balances={e.WALLET.lower(): 10**18},
                                codes={e.CONTRACT.lower(): "0x6080604052" + "00" * 40}))
    e.get_w3 = counting_get_w3
    r = assess_readiness(e)
    check(calls["get_w3"] == 1, "get_w3() is called exactly once for the whole report",
          str(calls["get_w3"]))
    check(r.check("receiver").ok, "the receiver check reused that connection")
    check(r.check("funding").ok, "the funding check reused that connection")

    print("\n  ── A read-only preflight: no transaction is ever sent ──")
    e = ready_engine()

    class ExplodingEth(FakeEth):
        def send_raw_transaction(self, *a, **k):
            raise AssertionError("readiness must never broadcast")

        def sendRawTransaction(self, *a, **k):  # web3 v5 spelling
            raise AssertionError("readiness must never broadcast")

        def call(self, *a, **k):
            raise AssertionError("readiness must never eth_call a state change")

    e.W3 = FakeW3(ExplodingEth(balances={e.WALLET.lower(): 10**18}))
    r = assess_readiness(e)
    check(r.check("funding").ok, "funding read works with a broadcast-hostile eth")
    check(r.check("sim_policy").ok, "sim policy is evaluated without any chain call")

    print("\n  ── Report shape ──")
    r = assess_readiness(ready_engine())
    d = r.to_dict()
    check(d["verdict"] == "READY", "to_dict reports the verdict")
    check(isinstance(d["checks"], list) and len(d["checks"]) == len(r.checks),
          "to_dict carries every check")
    check(all({"name", "title", "ok", "blocking", "detail"} <= set(c) for c in d["checks"]),
          "each serialized check has the documented keys")
    parsed = json.loads(render_json(r))
    check(parsed["verdict"] == "READY", "render_json output is valid JSON")
    check("READY" in render_text(r), "render_text states the verdict")
    check("BLOCKED" in render_text(assess_readiness(make_engine())),
          "render_text states BLOCKED when blocked")

    print("\n  ── Remedies point at real commands ──")
    blocked = assess_readiness(make_engine())
    rem = " ".join(c.remedy for c in blocked.blockers)
    check("jdl deploy receiver" in rem,
          "an undeployed receiver names the command that deploys it")
    check("GELATO_ENABLED" in rem or "ETH" in rem,
          "the funding remedy names a way to get gas")
    check("LIVE_EXECUTION" in rem, "the consent remedy names the flag to set")
    for c in blocked.checks:
        if c.ok:
            check(not c.remedy, f"a passing check carries no remedy ({c.name})")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())