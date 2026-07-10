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
from typing import Callable, List, Tuple

from jdl_flash._paths import load_flash_supervisor
from jdl_flash.env_autowire import CANONICAL_ENV, is_placeholder, parse_env_file

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ZERO_ADDR = "0x" + "0" * 40


def check_env_file(env_path: Path = CANONICAL_ENV) -> Tuple[bool, str]:
    if not env_path.is_file():
        return False, f"{env_path} does not exist — run `jdl install`"
    return True, str(env_path)


def _own_endpoint_count(values: dict) -> int:
    """How many of the user's OWN Arbitrum RPC endpoints the engine would build
    (the deduped list minus the always-appended public node). Uses the engine's
    canonicalizer, so RPC_URL2 / ALCHEMY_KEY_* count exactly as the engine sees
    them — not just RPC_URL / ALCHEMY_ARB_KEY."""
    from jdl_flash.rpc_endpoints import build_rpc_endpoints

    return len(build_rpc_endpoints(values)) - 1


def check_required_keys(env_path: Path = CANONICAL_ENV) -> Tuple[bool, str]:
    values = parse_env_file(env_path)
    unresolved = [k for k in ("PRIVATE_KEY",) if is_placeholder(values.get(k, ""))]
    if _own_endpoint_count(values) <= 0:
        # Any real source counts: RPC_URL, ALCHEMY_ARB_KEY, RPC_URLn, ALCHEMY_KEY_*.
        unresolved.append("an RPC source (RPC_URL / ALCHEMY_ARB_KEY / RPC_URLn / ALCHEMY_KEY_*)")
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


_DEFAULT_CHAIN_ID = 42161  # Arbitrum One; overridden by CHAIN_ID (e.g. 421614 Sepolia)


def _expected_chain_id(values: dict) -> int:
    """The chain the engine expects — CHAIN_ID from the same .env, so a Sepolia
    (421614) config isn't reported as 'wrong chain'. Falls back to Arbitrum One."""
    raw = (values.get("CHAIN_ID", "") or "").strip()
    return int(raw) if raw else _DEFAULT_CHAIN_ID


def _probe_chain_id(url: str, timeout: float) -> int:
    """POST eth_chainId to `url` and return the decoded chain id, or raise. Module
    level so tests can monkeypatch it instead of hitting the network."""
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — endpoints are user-configured, trusted
        return int(json.loads(resp.read())["result"], 16)


def check_rpc_reachable(env_path: Path = CANONICAL_ENV, timeout: float = 4.0) -> Tuple[bool, str]:
    """Probe the engine's endpoint list and report reachable if ANY responds on
    the CONFIGURED chain — so a dead primary with a healthy fallback reads as
    reachable, like the running engine. Endpoints are probed CONCURRENTLY, so
    total latency stays ~one `timeout` no matter how many failover entries are
    configured (a sequential probe would grow linearly and stall --watch/setup).
    The result still reports the first reachable endpoint in engine order."""
    import concurrent.futures

    from jdl_flash.rpc_endpoints import build_rpc_endpoints, PUBLIC_ARB_RPC

    values = parse_env_file(env_path)
    expected = _expected_chain_id(values)
    endpoints = build_rpc_endpoints(values)

    results: dict = {}  # 1-based index -> chain id (int) or Exception
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(endpoints), 12)) as pool:
        futures = {pool.submit(_probe_chain_id, url, timeout): i for i, url in enumerate(endpoints, 1)}
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:  # noqa: BLE001 — a failed probe just means that endpoint is down
                results[i] = exc

    last_err: object = "none tried"
    for i, url in enumerate(endpoints, 1):
        outcome = results.get(i)
        if isinstance(outcome, int):
            if outcome == expected:
                where = "public fallback" if url == PUBLIC_ARB_RPC else f"endpoint #{i}/{len(endpoints)}"
                return True, f"chain {outcome} via {where}"
            last_err = f"wrong chain {outcome} (expected {expected})"
        else:
            last_err = outcome
    return False, f"no endpoint reachable ({len(endpoints)} tried; last: {last_err})"


def check_rpc_endpoints(env_path: Path = CANONICAL_ENV) -> Tuple[bool, str]:
    """Report how many DISTINCT Arbitrum RPC endpoints the engine will try. Uses
    the engine's own canonicalizer (jdl_flash.rpc_endpoints.build_rpc_endpoints)
    against the parsed .env, so the count matches exactly what the engine builds
    — same alias precedence, same validity rules, same de-duplication (duplicate
    URLs and the always-appended public node never overstate redundancy).
    Informational (never fails); >1 non-public endpoint means real failover."""
    from jdl_flash.rpc_endpoints import build_rpc_endpoints

    eps = build_rpc_endpoints(parse_env_file(env_path))
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
