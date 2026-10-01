"""
readiness.py — one command that answers "can this system actually trade money
right now, and if not, exactly what is missing?"

Why this exists
---------------
The engine's live path is gated by a conjunction of independent conditions:

    PRIVATE_KEY (or GELATO_ENABLED)      a way to sign
    FLASH_CONTRACT_ADDRESS               a deployed receiver
    funded native balance                enough ETH to pay for gas
    WEB3_OK and a reachable RPC          a working connection to the right chain
    LIVE_EXECUTION=1                     the operator's explicit consent
    MIN_GAS_ETH                          a balance floor, enforced pre-broadcast
    MIN_PROFIT_USD                       the size of edge worth trading

Every one of these has shipped broken *silently*. The engine sets ``WEB3_OK =
False`` in an ``except ImportError`` and then never broadcasts while still
printing cycle numbers; a zero-balance wallet fails ``eth_sendRawTransaction``
and the cycle scores it as a gas-charged loss; an empty
``FLASH_CONTRACT_ADDRESS`` means there is nowhere to send the trade. In each
case the daemon looked alive. This module collapses the whole conjunction into a
single verdict with a per-check reason, so "why isn't it earning?" is one
command rather than an investigation.

Design constraints
------------------
* **Read-only.** Only ``eth_chainId``, ``eth_blockNumber``, ``eth_gasPrice``,
  ``eth_getBalance``, ``eth_getCode`` and ``eth_call`` are issued. No
  transaction is signed, sent, or simulated against a state-changing call.
* **No key material, ever.** The signing key is only ever tested for presence
  and, when present, used to derive its *public* address locally for display.
  The key itself never reaches a return value, a log line, or the report.
* **Credential-safe.** RPC endpoints are reduced to scheme+host via
  ``connectivity.redact_endpoint`` before they appear in output.
* **Fails closed, never optimistic.** An unrunnable check is ``FAIL`` with the
  reason, not a skipped row that reads like a pass.

Verdict semantics
-----------------
``READY``       every blocking check passed — the engine can broadcast
``BLOCKED``     at least one blocking check failed; ``blockers`` names them
Each check carries ``blocking: bool``: non-blocking checks (advisory facts such
as observed gas price) inform the operator without gating the verdict.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from jdl_flash.connectivity import redact_endpoint

log = logging.getLogger(__name__)

# The gas budget a worst-case execution is assumed to need when deciding
# whether the wallet can pay for it. Mirrors the engine's own fallback estimate
# (NexusExecutor.send falls back to 1,200,000 when estimation fails) and, like
# the executor, adds the 25% estimation margin before comparing, so this is the
# ceiling rather than the expectation.
_READINESS_GAS_LIMIT = 1_500_000

__all__ = [
    "Check",
    "ReadinessReport",
    "ReadinessVerdict",
    "assess_readiness",
    "render_json",
    "render_text",
]


class ReadinessVerdict:
    """The two possible verdicts. Plain strings, compared by value."""

    READY = "READY"
    BLOCKED = "BLOCKED"


# ---------------------------------------------------------------------------
# Check results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One precondition and the evidence for its verdict.

    Attributes:
        name: Stable identifier, also used as the JSON key.
        title: Operator-facing label.
        ok: Whether the condition currently holds.
        blocking: False for advisory checks that inform without gating.
        detail: What was observed — never any key material.
        remedy: What to do when ``ok`` is False. Empty when the check passed.
    """

    name: str
    title: str
    ok: bool
    blocking: bool
    detail: str
    remedy: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "ok": self.ok,
            "blocking": self.blocking,
            "detail": self.detail,
            "remedy": self.remedy,
        }


@dataclass
class ReadinessReport:
    """Aggregate verdict plus every individual check."""

    verdict: str
    chain_id: int
    chain_label: str
    endpoint: str
    checks: List[Check] = field(default_factory=list)

    @property
    def blockers(self) -> List[Check]:
        """Failed checks that actually gate execution."""
        return [c for c in self.checks if c.blocking and not c.ok]

    @property
    def ready(self) -> bool:
        return self.verdict == ReadinessVerdict.READY

    def check(self, name: str) -> Optional[Check]:
        for c in self.checks:
            if c.name == name:
                return c
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "chainId": self.chain_id,
            "chainLabel": self.chain_label,
            "endpoint": self.endpoint,
            "ready": self.ready,
            "blockerCount": len(self.blockers),
            "blockers": [c.name for c in self.blockers],
            "checks": [c.to_dict() for c in self.checks],
        }


_CHAIN_LABELS: Dict[int, str] = {
    42161: "Arbitrum One",
    421614: "Arbitrum Sepolia",
    1: "Ethereum",
    11155111: "Ethereum Sepolia",
}


def _chain_label(chain_id: int) -> str:
    return _CHAIN_LABELS.get(chain_id, f"chain {chain_id}")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_config_ok(engine: Any) -> Check:
    """Config parsed cleanly (a malformed knob disables live execution)."""
    ok = bool(getattr(engine, "CONFIG_OK", True))
    issues = list(getattr(engine, "CONFIG_ISSUES", []) or [])
    if ok:
        return Check(
            "config", "Configuration parses", True, True,
            "every environment value parsed cleanly",
        )
    return Check(
        "config", "Configuration parses", False, True,
        "; ".join(issues) if issues else "config.ok() is False",
        "fix the reported value(s) in your .env; the engine refuses to "
        "broadcast from a config it could not fully read",
    )


def _check_web3(engine: Any) -> Check:
    """The web3 import contract holds — the driver is usable at all."""
    ok = bool(getattr(engine, "WEB3_OK", False))
    if ok:
        return Check(
            "web3", "web3 driver importable", True, True,
            f"web3 imported; pinned version {getattr(engine, 'WEB3_VERSION', 'n/a')}",
        )
    return Check(
        "web3", "web3 driver importable", False, True,
        "the engine's web3/middleware import failed and every RPC helper is "
        "bound to a stub",
        "install the pinned web3 (web3==6.20.4); run `jdl test --filter web3` "
        "to confirm the import contract",
    )


def _check_rpc(engine: Any) -> Tuple[Check, Optional[Any]]:
    """A live connection to the configured chain. Returns the provider too.

    The engine keeps a module-level singleton (``_W3_SINGLETON``) rather than a
    ``W3`` attribute, so the connection is obtained through ``get_w3()`` — which
    also means one dialled connection is reused by every later check instead of
    one connection per check.
    """
    name = "rpc"
    w3 = getattr(engine, "W3", None)
    if w3 is None:
        getter = getattr(engine, "get_w3", None)
        w3 = getter() if callable(getter) else None
    if w3 is None:
        return (
            Check(
                name, "RPC reachable", False, True,
                "no provider connected from any configured endpoint",
                "check RPC_ENDPOINTS / ARBITRUM_RPC in your .env and confirm "
                "the host is reachable from this machine",
            ),
            None,
        )

    chain_id = _safe_chain_id(engine, w3)
    expected = int(getattr(engine, "CHAIN_ID", chain_id))
    if chain_id != expected:
        return (
            Check(
                name, "RPC reachable", False, True,
                f"endpoint reports chain id {chain_id}, config expects {expected} "
                f"({_chain_label(expected)})",
                "point RPC_ENDPOINTS at a "
                f"{_chain_label(expected)} endpoint — a Sepolia endpoint against "
                "a mainnet config trades nothing",
            ),
            w3,
        )

    try:
        block = int(getattr(engine, "_blk", lambda _w: 0)(w3))
    except Exception as exc:  # noqa: BLE001 - any failure means not usable
        return (
            Check(
                name, "RPC reachable", False, True,
                f"connected but eth_blockNumber failed: {exc}",
                "the endpoint accepted the connection but cannot serve state",
            ),
            w3,
        )

    endpoint = _active_endpoint(engine)
    return (
        Check(
            name, "RPC reachable", True, True,
            f"{endpoint} on {_chain_label(chain_id)}, block {block:,}",
        ),
        w3,
    )


def _check_chain_support(engine: Any) -> Check:
    """The configured chain is one the engine actually supports."""
    chain_id = int(getattr(engine, "CHAIN_ID", 0))
    supported: Sequence[int] = getattr(engine, "_SUPPORTED_CHAIN_IDS", ()) or ()
    if chain_id in supported:
        return Check(
            "chain_supported", "Chain supported", True, True,
            f"{_chain_label(chain_id)} ({chain_id})",
        )
    return Check(
        "chain_supported", "Chain supported", False, True,
        f"chain id {chain_id} is not in the supported set "
        f"{tuple(supported)}",
        "set CHAIN_ID to 42161 (Arbitrum One) or 421614 (Arbitrum Sepolia)",
    )


def _check_signing_key(engine: Any) -> Check:
    """A signing key or a gasless relay is configured. Never shows the key."""
    priv = getattr(engine, "PRIV_KEY", "") or ""
    gelato = bool(getattr(engine, "GELATO_ENABLED", False))
    if priv:
        addr = _derive_address(priv)
        shown = addr or "address could not be derived locally"
        return Check(
            "signing_key", "Signing key present", True, True,
            f"PRIVATE_KEY set (address {shown})",
        )
    if gelato:
        return Check(
            "signing_key", "Signing key present", True, True,
            "gasless execution via GELATO_ENABLED — the wallet needs no ETH",
        )
    return Check(
        "signing_key", "Signing key present", False, True,
        "neither PRIVATE_KEY nor GELATO_ENABLED is set, so no transaction can "
        "be signed or relayed",
        "set PRIVATE_KEY (and fund it), or set GELATO_ENABLED=1 for gasless "
        "relay where the fee is reimbursed from trade profit",
    )


def _check_receiver(engine: Any, w3: Optional[Any]) -> Check:
    """FLASH_CONTRACT_ADDRESS is set and has code deployed on-chain.

    An address-shaped value is not enough: the engine simulates before it
    broadcasts, so an undeployed receiver surfaces as a permanently reverting
    ``eth_call`` rather than as an obvious configuration error. This check
    therefore reads the code at the address.
    """
    addr = (getattr(engine, "CONTRACT", "") or "").strip()
    if not addr:
        return Check(
            "receiver", "Receiver contract deployed", False, True,
            "FLASH_CONTRACT_ADDRESS is empty, so there is nowhere to send a trade",
            "deploy it with `jdl deploy receiver` and set FLASH_CONTRACT_ADDRESS "
            "to the deployed address",
        )
    if not (addr.startswith("0x") and len(addr) == 42):
        return Check(
            "receiver", "Receiver contract deployed", False, True,
            f"FLASH_CONTRACT_ADDRESS={addr!r} is not a 0x-prefixed 20-byte address",
            "set it to the address printed by `jdl deploy receiver`",
        )
    if w3 is None:
        return Check(
            "receiver", "Receiver contract deployed", False, True,
            f"cannot verify code at {addr}: no RPC connection",
            "restore RPC connectivity so the deployed receiver can be verified",
        )
    try:
        get_code = getattr(engine, "w3_get_code", None)
        if callable(get_code):
            code = get_code(w3, addr)
        else:
            code = w3.eth.get_code(engine._w3_cs(addr))
    except Exception as exc:  # noqa: BLE001
        return Check(
            "receiver", "Receiver contract deployed", False, True,
            f"eth_getCode failed for {addr}: {exc}",
            "restore RPC connectivity, or redeploy the receiver",
        )
    if not code or code in (b"", "0x", "0x0"):
        return Check(
            "receiver", "Receiver contract deployed", False, True,
            f"no contract code at {addr} — the address is set but nothing is "
            f"deployed there on {_chain_label(int(getattr(engine, 'CHAIN_ID', 0)))}",
            "run `jdl deploy receiver` on this chain and set "
            "FLASH_CONTRACT_ADDRESS to the result, or unset it to stay "
            "fail-closed (the engine will not broadcast)",
        )
    size = len(code) // 2 - 1 if isinstance(code, str) else len(code)
    return Check(
        "receiver", "Receiver contract deployed", True, True,
        f"code present at {addr} ({size} bytes)",
    )


def _check_funding(engine: Any, w3: Optional[Any]) -> Check:
    """The wallet can pay for its own gas.

    Advisory rather than blocking when gasless relay is enabled: with
    GELATO_ENABLED the operator spends no ETH, so a dry wallet is correct.
    """
    gasless = bool(getattr(engine, "GELATO_ENABLED", False))
    min_eth = float(getattr(engine, "MIN_GAS_ETH", 0.0) or 0.0)
    priv = getattr(engine, "PRIV_KEY", "") or ""
    addr = (getattr(engine, "WALLET", "") or "").strip() or _derive_address(priv)

    if not addr:
        if gasless:
            return Check(
                "funding", "Wallet funded for gas", True, False,
                "gasless relay enabled; no native balance required",
            )
        return Check(
            "funding", "Wallet funded for gas", False, True,
            "no wallet address is configured, so a gas balance cannot be read",
            "set WALLET_ADDRESS (or PRIVATE_KEY, from which it is derived)",
        )

    if w3 is None:
        return Check(
            "funding", "Wallet funded for gas", False, gasless,
            f"cannot read the balance of {addr}: no RPC connection",
            "restore RPC connectivity to check funding",
        )

    try:
        bal_wei = int(engine._balance(w3, engine._w3_cs(addr)))
    except Exception as exc:  # noqa: BLE001
        return Check(
            "funding", "Wallet funded for gas", False, gasless,
            f"eth_getBalance failed for {addr}: {exc}",
            "restore RPC connectivity to check funding",
        )

    bal_eth = bal_wei / 1e18
    # The funding floor is not just MIN_GAS_ETH. The executor refuses to sign a
    # transaction whose own max fee (gas_limit x gas_price) would not fit under
    # the balance even after paying the headroom, so a readiness check that
    # compared against MIN_GAS_ETH alone could hand the operator a green light
    # the executor then rejects on the first trade. Both sides must measure the
    # same quantity or the gate is worse than useless.
    gas_price_wei = 0
    if w3 is not None:
        try:
            gas_price_wei = int(float(engine._gas_p(w3)))
        except Exception:  # noqa: BLE001 - an unreadable price is advisory
            gas_price_wei = 0
    if gas_price_wei < 0:
        gas_price_wei = 0

    # Integer wei throughout, because integer wei is what the executor compares
    # and what balances arrive in. Summing floats and comparing in ETH lets a
    # wallet one wei short pass a gate that would reject it.
    head_wei = max(0, int(min_eth * 1e18))
    required_wei = _READINESS_GAS_LIMIT * gas_price_wei + head_wei
    shortfall_wei = max(0, required_wei - bal_wei)

    detail = (
        f"{addr} holds {bal_eth:.6f} ETH; needs {required_wei / 1e18:.6f} ETH "
        f"(worst-case tx cost {_READINESS_GAS_LIMIT * gas_price_wei / 1e18:.6f} "
        f"at {gas_price_wei / 1e9:.4f} gwei x {_READINESS_GAS_LIMIT:,} gas, "
        f"plus MIN_GAS_ETH={min_eth:g} headroom)"
    )
    if gasless:
        return Check(
            "funding", "Wallet funded for gas", True, False,
            detail + "; gasless relay enabled, so a low balance is not fatal",
        )
    if not shortfall_wei:
        return Check("funding", "Wallet funded for gas", True, True, detail)
    return Check(
        "funding", "Wallet funded for gas", False, True,
        f"{detail} — short by {shortfall_wei / 1e18:.6f} ETH, so every "
        f"broadcast would be rejected for insufficient funds and scored as a "
        f"lost trade",
        f"send at least {required_wei / 1e18:.6f} ETH to {addr} (Arbitrum One), "
        f"or set GELATO_ENABLED=1 to trade gasless",
    )


def _check_live_exec(engine: Any) -> Check:
    """The operator's explicit consent flag is on."""
    on = bool(getattr(engine, "LIVE_EXEC", False))
    if on:
        return Check(
            "live_exec", "Live execution enabled", True, True,
            "LIVE_EXECUTION=1 — the engine may broadcast",
        )
    return Check(
        "live_exec", "Live execution enabled", False, True,
        "LIVE_EXECUTION is off — calldata is built but never broadcast "
        "(this is the safe default)",
        "after `jdl ready` reports READY with your own funded wallet and "
        "deployed receiver, set LIVE_EXECUTION=1 to trade",
    )


def _check_profit_floor(engine: Any) -> Check:
    """The configured edge threshold is a sane, non-zero number."""
    try:
        floor = float(getattr(engine, "MIN_PROFIT_USD", 0.0))
    except (TypeError, ValueError):
        floor = -1.0
    loan = float(getattr(engine, "REAL_LOAN_USD", 0.0) or 0.0)
    if floor > 0:
        detail = f"MIN_PROFIT_USD=${floor:g} on a ${loan:,.0f} loan"
        if loan > 0:
            detail += f" ({floor / loan * 100:.3f}% of principal)"
        return Check("profit_floor", "Profit floor sane", True, True, detail)
    return Check(
        "profit_floor", "Profit floor sane", False, True,
        f"MIN_PROFIT_USD={floor:g} does not demand a positive edge",
        "set MIN_PROFIT_USD to cover the flash-loan premium plus gas "
        "(the engine's default, $0.50, is the floor for that arithmetic)",
    )


def _check_gas_observation(engine: Any, w3: Optional[Any]) -> Check:
    """Advisory: current gas price and a rough cost per arbitrage cycle."""
    if w3 is None:
        return Check(
            "gas_observation", "Gas price observed", False, False,
            "no RPC connection",
        )
    try:
        gas_wei = int(engine._gas_p(w3))
    except Exception as exc:  # noqa: BLE001
        return Check(
            "gas_observation", "Gas price observed", False, False,
            f"eth_gasPrice failed: {exc}",
        )
    gwei = gas_wei / 1e9
    per_tx_eth = 1_200_000 * gas_wei / 1e18
    return Check(
        "gas_observation", "Gas price observed", True, False,
        f"{gwei:.4f} gwei — roughly ${per_tx_eth * 3000:.2f} per ~1.2M-gas "
        f"transaction at $3,000/ETH (advisory)",
    )


def _check_sim_policy(engine: Any) -> Check:
    """On mainnet, simulation must be impossible.

    This is the invariant the whole revenue claim rests on: a reported profit
    that came from ``random.gauss`` is not a profit.
    """
    allow_sim = bool(getattr(engine, "ALLOW_SIM", False))
    is_testnet = bool(getattr(engine, "IS_TESTNET", False))
    if allow_sim and not is_testnet:
        return Check(
            "sim_policy", "Real-data-only on mainnet", False, True,
            "ALLOW_SIM is enabled off-testnet, so simulated opportunities "
            "would be treated as tradeable",
            "set CHAIN_ID=421614 to use simulated paths deliberately on "
            "Arbitrum Sepolia; mainnet forces real quotes",
        )
    if allow_sim:
        return Check(
            "sim_policy", "Real-data-only on mainnet", True, True,
            "Arbitrum Sepolia: simulation permitted by design (no real money)",
        )
    return Check(
        "sim_policy", "Real-data-only on mainnet", True, True,
        f"{_chain_label(int(getattr(engine, 'CHAIN_ID', 0)))} forces live "
        f"on-chain quotes (USE_REAL_QUOTES={getattr(engine, 'USE_REAL_QUOTES', None)})",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_chain_id(engine: Any, w3: Any) -> int:
    reader = getattr(engine, "_chain_id", None)
    if callable(reader):
        try:
            return int(reader(w3))
        except Exception as exc:  # noqa: BLE001
            log.debug("chain id read failed: %s", exc)
            return -1
    try:
        return int(w3.eth.chain_id)
    except Exception:  # noqa: BLE001
        return -1


def _active_endpoint(engine: Any) -> str:
    raw = getattr(engine, "ACTIVE_RPC", "") or ""
    redacted, _had_key = redact_endpoint(raw)
    return redacted


def _derive_address(priv_key: str) -> str:
    """Derive the public address for a key, or '' if eth_account is unavailable.

    Deliberately never returns the key itself, and never raises: a missing
    eth_account should not turn a readiness report into a traceback.
    """
    if not priv_key:
        return ""
    try:
        from eth_account import Account

        return Account.from_key(priv_key).address
    except Exception as exc:  # noqa: BLE001
        log.debug("could not derive address from key: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


def assess_readiness(
    engine: Optional[Any] = None,
    *,
    checks: Optional[Sequence[Callable[[Any], Any]]] = None,
) -> ReadinessReport:
    """Evaluate every precondition and return one verdict.

    Args:
        engine: The engine module to inspect. Defaults to importing
            ``jdl_flash.flash_loan_engine``, which is what production runs.
        checks: Override for the ordered check sequence. Each entry is called
            with the engine and returns a :class:`Check` or a
            ``(Check, provider)`` pair; the provider is threaded to later checks
            so the RPC connection is established exactly once. Intended for
            tests, which pass stubs instead of the live module.

    Returns:
        A :class:`ReadinessReport` whose verdict is ``READY`` only when no
        blocking check failed.
    """
    if engine is None:
        from jdl_flash import flash_loan_engine as engine  # noqa: PLC0415

    ordered: Sequence[Callable[..., Any]] = checks or (
        _check_config_ok,
        _check_web3,
        _check_chain_support,
        _check_rpc,
        _check_signing_key,
        _check_receiver,
        _check_funding,
        _check_live_exec,
        _check_profit_floor,
        _check_sim_policy,
        _check_gas_observation,
    )

    # Checks that need the provider receive (engine, w3); the rest are
    # single-argument. Binding the provider is what keeps the connection
    # established exactly once — the RPC is dialled by _check_rpc and reused.
    needs_provider = {
        _check_receiver,
        _check_funding,
        _check_gas_observation,
    }

    w3: Optional[Any] = None
    collected: List[Check] = []
    for fn in ordered:
        name = getattr(fn, "__name__", "check")
        try:
            result = fn(engine, w3) if fn in needs_provider else fn(engine)
        except Exception as exc:  # noqa: BLE001 - a broken check must not hide the rest
            log.warning("readiness check %s raised: %s", name, exc)
            collected.append(
                Check(
                    name.replace("_check_", ""), "Check ran", False, True,
                    f"{name} raised {type(exc).__name__}: {exc}",
                    "this is a defect in the check itself; the condition it "
                    "was meant to verify is UNKNOWN and treated as failed",
                )
            )
            continue
        if isinstance(result, tuple):
            check, provider = result
            w3 = provider if provider is not None else w3
        else:
            check = result
        collected.append(check)

    blockers = [c for c in collected if c.blocking and not c.ok]
    chain_id = int(getattr(engine, "CHAIN_ID", 0) or 0)
    return ReadinessReport(
        verdict=ReadinessVerdict.READY if not blockers else ReadinessVerdict.BLOCKED,
        chain_id=chain_id,
        chain_label=_chain_label(chain_id),
        endpoint=_active_endpoint(engine),
        checks=collected,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_text(report: ReadinessReport) -> str:
    """Operator-facing summary: one line per check, then the verdict."""
    lines: List[str] = []
    lines.append(f"Chain:   {report.chain_label} ({report.chain_id})")
    lines.append(f"RPC:     {report.endpoint}")
    lines.append("")
    for c in report.checks:
        mark = "PASS" if c.ok else ("FAIL" if c.blocking else "WARN")
        lines.append(f"  [{mark}] {c.title}")
        if c.detail:
            lines.append(f"         {c.detail}")
        if not c.ok and c.remedy:
            lines.append(f"         -> {c.remedy}")
    lines.append("")
    blockers = report.blockers
    if report.ready:
        lines.append(
            "READY: every blocking precondition holds — the engine can broadcast."
        )
    else:
        names = ", ".join(c.name for c in blockers)
        lines.append(
            f"BLOCKED: {len(blockers)} blocking check(s) failed: {names}"
        )
        lines.append(
            "This system cannot earn anything until these are resolved. It will "
            "not broadcast while any of them is failing."
        )
    return "\n".join(lines)


def render_json(report: ReadinessReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point. Exits 0 only when the system is READY.

    Registered as ``jdl ready`` in ``jdl_flash/cli.py``.
    """
    import argparse  # noqa: PLC0415 - keeps module import cost off the hot path

    parser = argparse.ArgumentParser(
        prog="jdl ready",
        description=(
            "Verify every precondition for profitable live trading: config, "
            "web3, chain, RPC, signing key, deployed receiver, gas funding, "
            "consent, profit floor, real-data policy."
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    report = assess_readiness()
    if args.json:
        print(render_json(report))
    else:
        print(render_text(report))
    return 0 if report.ready else 1