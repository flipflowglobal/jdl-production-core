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


def check_required_keys(env_path: Path = CANONICAL_ENV) -> Tuple[bool, str]:
    values = parse_env_file(env_path)
    unresolved = [k for k in ("PRIVATE_KEY", "RPC_URL") if is_placeholder(values.get(k, ""))]
    if unresolved:
        return False, f"still unset: {', '.join(unresolved)} — run `jdl install` to auto-wire, or set by hand"
    return True, "PRIVATE_KEY, RPC_URL set"


def check_contract_address(env_path: Path = CANONICAL_ENV) -> Tuple[bool, str]:
    values = parse_env_file(env_path)
    addr = values.get("FLASH_CONTRACT_ADDRESS", "")
    if is_placeholder(addr) or addr == _ZERO_ADDR:
        return False, "not deployed yet (scan-only mode still works) — see `jdl deploy`"
    if not _ADDR_RE.match(addr):
        return False, f"malformed address: {addr!r}"
    return True, addr


def check_rpc_reachable(env_path: Path = CANONICAL_ENV, timeout: float = 4.0) -> Tuple[bool, str]:
    values = parse_env_file(env_path)
    rpc_url = values.get("RPC_URL", "")
    if is_placeholder(rpc_url):
        return False, "RPC_URL not set"
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


def check_daemon_liveness() -> Tuple[bool, str]:
    fs = load_flash_supervisor()
    if fs.PID_FILE.exists():
        return True, f"supervised daemon running (pid {fs.PID_FILE.read_text().strip()})"
    return False, "no supervised daemon running — `jdl supervisor` to start one"


CHECKS: List[Tuple[str, Callable[[], Tuple[bool, str]]]] = [
    ("Canonical .env file", check_env_file),
    ("Required keys wired", check_required_keys),
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
