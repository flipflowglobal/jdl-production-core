"""
integrate.py — the checks behind `jdl integrate`: does every link between
system functions actually work end to end (env wired, wallet/RPC reachable,
contract deployed, supervised daemon alive)? The pure per-check logic lives
here, decoupled from argparse/looping, so each one is independently
unit-testable; cli.py's cmd_integrate is a thin --watch loop around
run_checks().
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from jdl_flash._paths import load_flash_supervisor
from jdl_flash.env_autowire import CANONICAL_ENV, is_placeholder, parse_env_file

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ZERO_ADDR = "0x" + "0" * 40


def check_env_file(env_path: Path = CANONICAL_ENV) -> Tuple[bool, str]:
    if not env_path.is_file():
        return False, f"{env_path} does not exist — run `jdl install`"
    return True, str(env_path)


def _has_rpc_source(values: dict) -> bool:
    # Mirrors flash_loan_engine.py's _build_rpc_endpoints(): RPC_URL is not the
    # only valid source — ALCHEMY_ARB_KEY (the key env_autowire actually fills,
    # since .env.template's RPC_URL default isn't a placeholder by itself) is
    # just as good, and the engine prefers it first.
    return not is_placeholder(values.get("RPC_URL", "")) or not is_placeholder(values.get("ALCHEMY_ARB_KEY", ""))


def check_required_keys(env_path: Path = CANONICAL_ENV) -> Tuple[bool, str]:
    values = parse_env_file(env_path)
    unresolved = [k for k in ("PRIVATE_KEY",) if is_placeholder(values.get(k, ""))]
    if not _has_rpc_source(values):
        unresolved.append("RPC_URL or ALCHEMY_ARB_KEY")
    if unresolved:
        return False, f"still unset: {', '.join(unresolved)} — run `jdl install` to auto-wire, or set by hand"
    return True, "PRIVATE_KEY and an RPC source are set"


def check_contract_address(env_path: Path = CANONICAL_ENV) -> Tuple[bool, str]:
    values = parse_env_file(env_path)
    addr = values.get("FLASH_CONTRACT_ADDRESS", "")
    if is_placeholder(addr) or addr == _ZERO_ADDR:
        return False, "not deployed yet (scan-only mode still works) — see `jdl deploy`"
    if not _ADDR_RE.match(addr):
        return False, f"malformed address: {addr!r}"
    return True, addr


def _resolve_rpc_url(values: dict) -> Optional[str]:
    # Same priority order as flash_loan_engine.py's _build_rpc_endpoints():
    # Alchemy key first, then RPC_URL.
    alch_arb = values.get("ALCHEMY_ARB_KEY", "")
    if not is_placeholder(alch_arb):
        return f"https://arb-mainnet.g.alchemy.com/v2/{alch_arb}"
    rpc_url = values.get("RPC_URL", "")
    if not is_placeholder(rpc_url):
        return rpc_url
    return None


def check_rpc_reachable(env_path: Path = CANONICAL_ENV, timeout: float = 4.0) -> Tuple[bool, str]:
    values = parse_env_file(env_path)
    rpc_url = _resolve_rpc_url(values)
    if rpc_url is None:
        return False, "RPC_URL or ALCHEMY_ARB_KEY not set"
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode()
    req = urllib.request.Request(
        rpc_url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — RPC_URL is user-configured, trusted
            body = json.loads(resp.read())
        chain_id = int(body["result"], 16)
        return True, f"chain id {chain_id}"
    except Exception as exc:  # noqa: BLE001 — best-effort reachability probe, any failure just means "unreachable"
        return False, f"unreachable ({exc})"


def _real(value: str) -> bool:
    return bool(value) and not is_placeholder(value) and " " not in value.strip()


def _first_real(values: dict, *names: str) -> str:
    """First non-placeholder value among alias names — mirrors the engine's
    _env(): first alias set wins, the rest of the group is ignored."""
    for name in names:
        if _real(values.get(name, "")):
            return values[name].strip()
    return ""


_PUBLIC_ARB_RPC = "https://arb1.arbitrum.io/rpc"


def _canonical_rpc_endpoints(values: dict) -> list:
    """The DEDUPLICATED Arbitrum RPC URL list the engine will actually try,
    reproducing flash_loan_engine._build_rpc_endpoints()'s alias precedence and
    de-duplication (that function is the source of truth — keep this in sync).
    Counting raw config vars would overstate redundancy, because the engine
    picks one value per alias group and drops duplicate URLs (including the
    always-appended public node)."""
    eps = []
    # 1) Alchemy — dedicated key group first, then every ALCHEMY_KEY_*.
    alch = _first_real(values, "ALCHEMY_ARB_KEY", "ALCHEMY_ARBITRUM_KEY")
    if alch:
        eps.append(f"https://arb-mainnet.g.alchemy.com/v2/{alch}")
    for name in sorted(values):
        if name.startswith("ALCHEMY_KEY_") and _real(values[name]):
            eps.append(f"https://arb-mainnet.g.alchemy.com/v2/{values[name].strip()}")
    # 2) Explicit URL group, then numbered RPC_URLn in numeric order.
    rpc = _first_real(values, "RPC_URL", "ARBITRUM_RPC_URL", "ARB_RPC_URL")
    if rpc:
        eps.append(rpc)
    numbered = []
    for name in values:
        if name.startswith("RPC_URL") and name != "RPC_URL" and _real(values[name]):
            suffix = name[len("RPC_URL"):]
            numbered.append((int(suffix) if suffix.isdigit() else 10 ** 9, values[name].strip()))
    for _, url in sorted(numbered):
        eps.append(url)
    # 3) RPC_FALLBACKS comma list.
    for url in (values.get("RPC_FALLBACKS", "") or "").split(","):
        if _real(url):
            eps.append(url.strip())
    # 4) Public node, always appended last.
    eps.append(_PUBLIC_ARB_RPC)
    seen, out = set(), []
    for url in eps:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def check_rpc_endpoints(env_path: Path = CANONICAL_ENV) -> Tuple[bool, str]:
    """Report how many DISTINCT Arbitrum RPC endpoints the engine will try —
    after alias-precedence selection and de-duplication — so duplicate URLs or
    redundant aliases never overstate redundancy. Informational (never fails),
    but >1 non-public endpoint means real failover if a provider rate-limits."""
    eps = _canonical_rpc_endpoints(parse_env_file(env_path))
    own = len(eps) - 1  # the public fallback is always present after de-dup
    if own <= 0:
        return True, "1 endpoint (public node only) — add ALCHEMY_ARB_KEY / RPC_URL for your own, ideally several"
    return True, f"{len(eps)} endpoints ({own} of yours + public fallback) — failover ready"


def check_daemon_liveness() -> Tuple[bool, str]:
    fs = load_flash_supervisor()
    if fs.PID_FILE.exists():
        return True, f"supervised daemon running (pid {fs.PID_FILE.read_text().strip()})"
    return False, "no supervised daemon running — `jdl supervisor` to start one"


CHECKS: List[Tuple[str, Callable[[], Tuple[bool, str]]]] = [
    ("Canonical .env file", check_env_file),
    ("Required keys wired", check_required_keys),
    ("RPC endpoints configured", check_rpc_endpoints),
    ("Flash contract deployed", check_contract_address),
    ("RPC endpoint reachable", check_rpc_reachable),
    ("Supervised daemon", check_daemon_liveness),
]


def run_checks() -> List[Tuple[str, bool, str]]:
    results = []
    for label, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 — one broken check must not crash the whole report
            ok, detail = False, f"check failed: {exc}"
        results.append((label, ok, detail))
    return results
