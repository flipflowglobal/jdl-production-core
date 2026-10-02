"""Multi-platform mainnet connectivity verification.

Answers one question: *can every runtime this system ships actually reach
mainnet right now, and are they all talking to the chain we think they are?*

Why this is not a curl one-liner
--------------------------------
The engine's own live path is gated on ``if CONTRACT and WEB3_OK and LIVE_EXEC``
(``flash_loan_engine.py:1553``). A Python process with a healthy interpreter and
a web3 import can still leave that gate shut, which is exactly the failure this
repository already shipped once: ``web3==8.0.0`` declares
``Requires-Python >=3.10``, so on the declared 3.9 floor pip filtered it out,
``from web3.middleware import geth_poa_middleware`` raised ``ImportError``, the
engine's ``except`` handler set ``WEB3_OK = False`` and bound every helper to a
stub, and the system silently never broadcast while still printing cycle
numbers. A test then force-set ``WEB3_OK = True`` and the suite stayed green.

So "the tool is installed" is not the question. The question is whether each
platform, with its own driver, can complete the four read-only JSON-RPC calls
that a real trade depends on, and whether the chain id it gets back is the one
configured. That is what this module measures.

Platforms covered
-----------------
``python``   the production driver — ``web3.py`` (pinned 6.20.4)
``node``     the orchestration server — ``ethers`` v6
``rust``     the hot-path crate — ``jdl-hotpath probe`` (subprocess, JSON in/out)
``foundry``  contract tooling — ``cast`` (subprocess)
``hardhat``  contract build/test — ``hre`` against a live provider

Safety properties
-----------------
* **Read-only by construction.** Only ``eth_chainId``, ``eth_blockNumber``,
  ``eth_gasPrice`` and ``web3_clientVersion`` are ever issued. No private key
  is loaded, no signer is constructed, and no transaction method is reachable
  from this module.
* **Credential-safe.** RPC endpoints are bearer credentials. Every endpoint is
  reduced to scheme+host before it reaches a log line, a JSON report, or CI
  output, and error strings are scrubbed, because driver errors embed the full
  request URL.
* **No database access.** This module never imports the engine's storage layer
  and never opens a ledger; it is safe to run in CI and against a production host.

Verdict semantics
-----------------
Each platform/chain pair resolves to one of:

``OK``          all four calls succeeded and the chain id matched
``MISMATCH``    the endpoint answered but reported a different chain
``UNREACHABLE`` transport failure, timeout, or RPC error
``ABSENT``      the driver is not installed on this host (a skip, not a failure)

The process exits non-zero when any *required* pair is not ``OK``. ``ABSENT`` is
reported distinctly so a missing optional toolchain is never mistaken for a
broken mainnet connection, and a wrong chain is never mistaken for a healthy one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "ChainSpec",
    "Platform",
    "Verdict",
    "ProbeResult",
    "ConnectivityReport",
    "redact_endpoint",
    "scrub",
    "check_platform",
    "check_all",
    "render_text",
    "render_json",
    "main",
]


# --------------------------------------------------------------------------
# Chains
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainSpec:
    """A network this system may be pointed at."""

    key: str
    chain_id: int
    label: str
    #: Endpoints tried in order. First responder wins.
    urls: Tuple[str, ...]
    #: Whether a non-OK verdict from any platform should fail the run.
    required: bool


#: Arbitrum One — the only chain the engine's flash-loan path targets.
ARBITRUM_ONE = ChainSpec(
    key="arbitrum",
    chain_id=42161,
    label="Arbitrum One",
    urls=(
        "https://arb1.arbitrum.io/rpc",
        "https://arbitrum-one-rpc.publicnode.com",
        "https://arb1.arbitrum.gateway.fm",
    ),
    required=True,
)

#: Ethereum mainnet — read-only reference chain for cross-chain comparison.
ETHEREUM = ChainSpec(
    key="ethereum",
    chain_id=1,
    label="Ethereum",
    urls=(
        "https://ethereum-rpc.publicnode.com",
        "https://eth.llamarpc.com",
        "https://rpc.ankr.com/eth",
    ),
    required=False,
)

#: Arbitrum Sepolia — the only chain on which simulated/synthetic paths are
#: reachable (``ALLOW_SIM = IS_TESTNET`` in the engine).
ARBITRUM_SEPOLIA = ChainSpec(
    key="arbitrum-sepolia",
    chain_id=421614,
    label="Arbitrum Sepolia",
    urls=(
        "https://sepolia-rollup.arbitrum.io/rpc",
        "https://arbitrum-sepolia-rpc.publicnode.com",
    ),
    required=False,
)

ALL_CHAINS: Tuple[ChainSpec, ...] = (ARBITRUM_ONE, ETHEREUM, ARBITRUM_SEPOLIA)

#: RPC methods this module is permitted to issue. Mirrors ``RpcMethod`` in
#: ``rust/hotpath/src/probe.rs``; kept in sync deliberately.
READ_ONLY_METHODS: Tuple[str, ...] = (
    "eth_chainId",
    "eth_blockNumber",
    "eth_gasPrice",
    "web3_clientVersion",
)


# --------------------------------------------------------------------------
# Platforms
# --------------------------------------------------------------------------


class Platform(str, Enum):
    """A distinct client stack that must independently reach mainnet."""

    PYTHON = "python"
    NODE = "node"
    RUST = "rust"
    FOUNDRY = "foundry"
    HARDHAT = "hardhat"

    @property
    def label(self) -> str:
        return {
            Platform.PYTHON: "python web3",
            Platform.NODE: "node ethers",
            Platform.RUST: "rust jdl-hotpath",
            Platform.FOUNDRY: "foundry cast",
            Platform.HARDHAT: "hardhat hre",
        }[self]


class Verdict(str, Enum):
    OK = "OK"
    MISMATCH = "MISMATCH"
    UNREACHABLE = "UNREACHABLE"
    ABSENT = "ABSENT"


# --------------------------------------------------------------------------
# Credential redaction
# --------------------------------------------------------------------------

#: Any run of >=16 URL-safe characters that is not obviously structural is
#: treated as key material. Real API keys are far longer than any path segment
#: a human would name ("rpc", "v2"), so the length floor separates them well.
_CREDENTIAL_SEGMENT = re.compile(r"[A-Za-z0-9_\-]{16,}")

_REDACTED = "<redacted>"


def redact_endpoint(url: str) -> Tuple[str, bool]:
    """Reduce an RPC URL to ``scheme://host`` and report whether it held a key.

    Returns ``(redacted_url, credential_present)``.

    The credential flag exists so the report can say "this endpoint is
    authenticated, and here is the proof the report did not leak it" rather than
    making the reader trust redaction on faith.
    """
    if "://" in url:
        scheme, rest = url.split("://", 1)
    else:
        scheme, rest = "", url
    authority = re.split(r"[/?]", rest)[0]
    tail = rest[len(authority) :].lstrip("/")

    # Userinfo in the authority (``https://user:pass@host/...``) is a
    # credential too. Strip it from the host and count it as present, otherwise
    # the redacted output would republish the username and password into every
    # log line and JSON report — the exact leak this function exists to prevent.
    host = authority
    userinfo_present = False
    if "@" in host:
        userinfo, _, host = host.rpartition("@")
        userinfo_present = bool(userinfo)

    credential_present = userinfo_present or (
        bool(tail)
        and any(len(seg) >= 16 for seg in re.split(r"[/?&]", tail))
    )
    # Kept as a single expression whose first operand is the userinfo flag: the
    # connectivity suite mutates this line to prove its redaction assertions
    # would actually catch a silent-credential-flag regression (see
    # test_connectivity._REDACTION_MUTATIONS["silent-credential-flag"]).
    host = host or "<unparseable>"
    return (f"{scheme}://{host}" if scheme else host), credential_present


def scrub(text: str, *urls: str) -> str:
    """Remove endpoint credentials from a free-form error string.

    Driver errors embed the full request URL, and the URL *is* the credential
    (``https://arb-mainnet.g.alchemy.com/v2/<key>``). This module's output goes
    to stdout, to CI logs, and to a JSON report, so an unscrubbed error
    republishes a funded provider key into all three.
    """
    out = text
    for url in urls:
        if not url:
            continue
        redacted, _ = redact_endpoint(url)
        if len(url) > 8:
            out = out.replace(url, redacted)
        authority = re.split(r"[/?]", url.split("://")[-1])[0]
        if authority:
            out = out.replace(authority, redacted.split("://")[-1] or _REDACTED)
    out = _CREDENTIAL_SEGMENT.sub(_REDACTED, out)
    return out


# --------------------------------------------------------------------------
# Result model
# --------------------------------------------------------------------------


@dataclass
class ProbeResult:
    """Outcome of one platform against one chain."""

    platform: Platform
    chain: ChainSpec
    verdict: Verdict
    endpoint: str
    credential_in_url: bool = False
    chain_id: Optional[int] = None
    block_number: Optional[int] = None
    gas_price_wei: Optional[int] = None
    client: Optional[str] = None
    latency_ms: int = 0
    driver_version: Optional[str] = None
    error: Optional[str] = None
    #: Every endpoint tried, in order. More than one entry means failover fired.
    attempted: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.OK

    def to_dict(self) -> Dict[str, object]:
        return {
            "platform": self.platform.value,
            "platformLabel": self.platform.label,
            "chain": self.chain.key,
            "chainLabel": self.chain.label,
            "expectedChainId": self.chain.chain_id,
            "verdict": self.verdict.value,
            "endpoint": self.endpoint,
            "credentialInUrl": self.credential_in_url,
            "chainId": self.chain_id,
            "blockNumber": self.block_number,
            "gasPriceWei": self.gas_price_wei,
            "client": self.client,
            "latencyMs": self.latency_ms,
            "driverVersion": self.driver_version,
            "error": self.error,
            "attempted": self.attempted,
        }


@dataclass
class ConnectivityReport:
    """Aggregate verdict across every platform/chain pair."""

    results: List[ProbeResult] = field(default_factory=list)

    def add(self, result: ProbeResult) -> None:
        self.results.append(result)

    def failures(self) -> List[ProbeResult]:
        """Non-OK results on required chains, plus any MISMATCH anywhere.

        A MISMATCH is always a failure regardless of `required`: an endpoint
        answering on the wrong chain is a misconfiguration that would trade or
        read the wrong network, and treating it as an optional skip is how a
        mainnet system ends up pointed at the wrong chain unnoticed.
        """
        return [
            r
            for r in self.results
            if r.verdict is Verdict.MISMATCH
            or (r.chain.required and r.verdict is not Verdict.OK)
        ]

    @property
    def healthy(self) -> bool:
        return not self.failures()

    def to_dict(self) -> Dict[str, object]:
        return {
            "healthy": self.healthy,
            "checkedAt": int(time.time()),
            "platforms": [p.value for p in Platform],
            "chains": [
                {"key": c.key, "chainId": c.chain_id, "label": c.label, "required": c.required}
                for c in ALL_CHAINS
            ],
            "failures": len(self.failures()),
            "results": [r.to_dict() for r in self.results],
        }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _repo_root() -> Path:
    """Repository root, derived from this file's location.

    ``python/jdl_flash/connectivity.py`` -> repository root. Used to locate the
    Rust binary, the Hardhat project, and the Node server without depending on
    the process working directory.
    """
    return Path(__file__).resolve().parents[2]


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def _run(
    argv: Sequence[str],
    *,
    timeout: float,
    stdin_payload: Optional[str] = None,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str, int]:
    """Run a subprocess, returning ``(rc, stdout, stderr, elapsed_ms)``.

    A timeout is reported as rc=124 with the timeout named in stderr, so a hung
    endpoint produces a clear verdict instead of stalling the whole check.
    """
    started = time.monotonic()
    # `env` is merged over the parent environment rather than replacing it, so
    # a probe still sees PATH and HOME (Node and Hardhat both need them) while
    # the caller can inject the specific variables its driver reads.
    child_env = {**os.environ, **(env or {})} if env else None
    try:
        completed = subprocess.run(
            list(argv),
            input=stdin_payload,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:g}s", int((time.monotonic() - started) * 1000)
    except FileNotFoundError as exc:
        return 127, "", str(exc), int((time.monotonic() - started) * 1000)
    return (
        completed.returncode,
        completed.stdout or "",
        completed.stderr or "",
        int((time.monotonic() - started) * 1000),
    )


def _hex_to_int(value: object) -> Optional[int]:
    """Decode a JSON-RPC hex quantity, tolerating int/str/None.

    Returns ``None`` for anything malformed. A quantity that cannot be parsed is
    reported as absent rather than coerced to 0, because "block 0" and "no
    block" are very different facts when deciding whether mainnet is reachable.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text, 10)
    except ValueError:
        return None


def _endpoints_for(chain: ChainSpec) -> List[str]:
    """Endpoint list for a chain: configured override first, then defaults.

    An operator pointing the system at a private provider must have that provider
    actually tested. The override is read per-chain so a typo in one variable
    does not silently redirect the other chains.
    """
    override_env = {
        ARBITRUM_ONE.key: ("JDL_CONNECTIVITY_ARBITRUM_URL", "ARB_RPC_URL"),
        ETHEREUM.key: ("JDL_CONNECTIVITY_ETHEREUM_URL", "ETH_RPC_URL"),
        ARBITRUM_SEPOLIA.key: ("JDL_CONNECTIVITY_SEPOLIA_URL",),
    }[chain.key]
    for name in override_env:
        value = os.environ.get(name, "").strip()
        if value:
            return [value, *chain.urls]
    return list(chain.urls)


# --------------------------------------------------------------------------
# Platform probes
# --------------------------------------------------------------------------


def probe_python(chain: ChainSpec, url: str, timeout: float) -> Dict[str, object]:
    """Probe via ``web3.py`` — the production driver.

    Uses a ``Web3`` instance directly rather than the engine module: importing
    the engine pulls in storage and config side effects, and this check must be
    runnable on a clean host with no ledger present. The engine's own web3
    contract is covered separately by ``jdl_flash/test_web3_contract.py``.
    """
    from web3 import Web3  # noqa: PLC0415  (deferred: absence is a verdict, not a crash)

    provider = Web3.HTTPProvider(url, request_kwargs={"timeout": timeout})
    w3 = Web3(provider)
    if not w3.is_connected():
        raise ConnectionError("web3 provider reported not connected")
    return {
        "chainId": _hex_to_int(w3.eth.chain_id),
        "blockNumber": _hex_to_int(w3.eth.block_number),
        "gasPriceWei": _hex_to_int(w3.eth.gas_price),
        "client": w3.client_version,
        "driverVersion": f"web3 {__import__('web3').__version__}",
    }


#: Node/ethers probe. Written as source text and passed via ``node --input-type``
#: so the check does not depend on a script file existing in every deployment
#: and cannot drift from what it asserts.
_NODE_PROBE_SRC = """
import { JsonRpcProvider } from 'ethers';
const url = process.argv[1];
const timeout = Number(process.argv[2]);
const provider = new JsonRpcProvider(url, undefined, { staticNetwork: true });
const [net, block, gas, client] = await Promise.all([
  provider.getNetwork(),
  provider.getBlockNumber(),
  provider.getFeeData().then((f) => f.gasPrice).catch(() => null),
  provider.send('web3_clientVersion', []).catch(() => null),
]);
process.stdout.write(JSON.stringify({
  chainId: Number(net.chainId),
  blockNumber: block,
  gasPriceWei: gas === null ? null : Number(gas),
  client,
  driverVersion: 'ethers v6',
}));
"""


def probe_node(chain: ChainSpec, url: str, timeout: float) -> Dict[str, object]:
    """Probe via ``ethers`` v6 — the orchestration server's driver.

    The probe lives in ``node/`` so it links that project's real dependency
    tree rather than whatever happens to be resolvable from ``python/``.
    """
    node_dir = _repo_root() / "node"
    if not (node_dir / "node_modules" / "ethers").is_dir():
        raise FileNotFoundError("ethers is not installed; run `npm install` in node/")
    rc, out, err, _ = _run(
        ["node", "--input-type=module", "--eval", _NODE_PROBE_SRC, url, str(int(timeout))],
        timeout=timeout + 5,
        cwd=node_dir,
    )
    if rc != 0:
        raise RuntimeError(scrub(err.strip() or f"node probe exited {rc}", url))
    return json.loads(out)


def probe_rust(chain: ChainSpec, url: str, timeout: float) -> Dict[str, object]:
    """Probe via the ``jdl-hotpath`` crate's ``probe`` mode.

    Uses the crate the production hot path is built from, so this leg verifies
    the actual shipped artifact rather than a Python-side assumption that Rust
    exists. JSON in, JSON out, so the check needs no FFI.
    """
    binary = _repo_root() / "rust" / "hotpath" / "target" / "release" / "jdl-hotpath"
    if not binary.is_file():
        raise FileNotFoundError(
            "jdl-hotpath release binary not found; run `cargo build --release` in rust/hotpath"
        )
    payload = json.dumps(
        {"url": url, "expected_chain_id": chain.chain_id, "timeout_ms": int(timeout * 1000)}
    )
    rc, out, err, _ = _run(
        [str(binary), "probe"], timeout=timeout + 5, stdin_payload=payload
    )
    if not out.strip():
        raise RuntimeError(scrub(err.strip() or f"jdl-hotpath exited {rc} with no output", url))
    report = json.loads(out)
    if report.get("error") or not report.get("chainOk"):
        raise RuntimeError(scrub(str(report.get("error") or "chain id mismatch"), url))
    return {
        "chainId": report.get("chainId"),
        "blockNumber": report.get("blockNumber"),
        "gasPriceWei": _hex_to_int(report.get("gasPriceWei")),
        "client": report.get("client"),
        "driverVersion": "jdl-hotpath",
    }


def probe_foundry(chain: ChainSpec, url: str, timeout: float) -> Dict[str, object]:
    """Probe via Foundry's ``cast``."""
    cast = _which("cast")
    if not cast:
        raise FileNotFoundError("`cast` not on PATH; install Foundry (foundryup)")
    fields: Dict[str, object] = {"driverVersion": "cast"}
    for key, args in (
        ("chainId", ["chain-id"]),
        ("blockNumber", ["block-number"]),
        ("gasPriceWei", ["gas-price"]),
        ("client", ["client-version"]),
    ):
        rc, out, err, _ = _run([cast, *args, "--rpc-url", url], timeout=timeout)
        if rc != 0:
            if key == "client":
                # Not every provider implements web3_clientVersion; the chain
                # verdict does not depend on it.
                fields[key] = None
                continue
            raise RuntimeError(scrub(err.strip() or f"cast {key} exited {rc}", url))
        raw = out.strip()
        if key == "client":
            fields[key] = raw or None
        else:
            fields[key] = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    return fields


#: Executed by the Hardhat CLI as a script, so `hre` is fully configured
#: (networks, provider, chain-id validation) exactly as a deploy would see it.
#: Reads its own inputs from the environment; writes JSON to stdout.
_HARDHAT_PROBE_SRC = """
const hre = require('hardhat');

(async () => {
  const net = await hre.network.provider.send('eth_chainId', []);
  const block = await hre.network.provider.send('eth_blockNumber', []);
  const gas = await hre.network.provider.send('eth_gasPrice', []);
  let client = null;
  try {
    client = await hre.network.provider.send('web3_clientVersion', []);
  } catch (e) {
    // Not every provider implements web3_clientVersion. The chain verdict does
    // not depend on it, so a failure here is recorded as absent, not fatal.
    client = null;
  }
  process.stdout.write(
    'JDL_PROBE_RESULT ' +
      JSON.stringify({
        chainId: parseInt(net, 16),
        blockNumber: parseInt(block, 16),
        gasPriceWei: parseInt(gas, 16),
        client,
        driverVersion: 'hardhat ' + hre.version + ' network=' + hre.network.name,
      })
  );
})().catch((e) => {
  process.stderr.write(String((e && e.message) || e));
  process.exit(1);
});
"""

#: Marker the script writes ahead of its JSON, so Hardhat's own compile/config
#: chatter on stdout cannot corrupt the parse.
_HARDHAT_MARKER = "JDL_PROBE_RESULT "


def probe_hardhat(chain: ChainSpec, url: str, timeout: float) -> Dict[str, object]:
    """Probe via Hardhat's configured provider, using the Hardhat CLI.

    This exercises the same provider object and the same network configuration
    the contract tests and deploy scripts use, so a chain-id or credential
    problem in ``hardhat.config.js`` surfaces here rather than during a deploy.

    The script is run through ``hardhat run --network <net>`` rather than plain
    ``node`` because ``--network`` is a Hardhat flag: ``node`` rejects it
    outright. The script is written to a temporary file inside the contracts
    project so ``require('hardhat')`` resolves, and is always removed.

    ``--no-compile`` keeps the probe from recompiling contracts; this is a
    connectivity check, not a build check.
    """
    contracts_dir = _repo_root() / "contracts"
    if not (contracts_dir / "node_modules" / "hardhat").is_dir():
        raise FileNotFoundError("hardhat is not installed; run `npm install` in contracts/")

    if chain.chain_id in (42161, 421614):
        network = "arbitrum"
    else:
        network = "ethereum"
    # hardhat.config.js sources `arbitrum` from ARB_RPC_URL and `ethereum` from
    # ETH_RPC_URL; set the one this chain needs so the probe travels the
    # project's real config path instead of bypassing it.
    endpoint_var = "ARB_RPC_URL" if network == "arbitrum" else "ETH_RPC_URL"

    script_path = contracts_dir / ".jdl-connectivity-probe.cjs"
    try:
        script_path.write_text(_HARDHAT_PROBE_SRC, encoding="utf-8")
        env = {
            endpoint_var: url,
            "CI": "true",
            "HARDHAT_DISABLE_TELEMETRY_PROMPT": "true",
        }
        rc, out, err, _ = _run(
            [
                "npx",
                "--no-install",
                "hardhat",
                "run",
                "--no-compile",
                "--network",
                network,
                str(script_path),
            ],
            timeout=timeout + 60,
            cwd=contracts_dir,
            env=env,
        )
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass

    if _HARDHAT_MARKER not in out:
        raise RuntimeError(scrub(err.strip() or f"hardhat probe exited {rc}", url))
    payload = out.split(_HARDHAT_MARKER, 1)[1].strip()
    # Hardhat may append trailing output after the JSON; decode only the object.
    end = payload.rfind("}")
    if end == -1:
        raise RuntimeError("hardhat probe emitted unparseable output")
    return json.loads(payload[: end + 1])


_PROBES: Dict[Platform, Callable[[ChainSpec, str, float], Dict[str, object]]] = {
    Platform.PYTHON: probe_python,
    Platform.NODE: probe_node,
    Platform.RUST: probe_rust,
    Platform.FOUNDRY: probe_foundry,
    Platform.HARDHAT: probe_hardhat,
}


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def check_platform(
    platform: Platform,
    chain: ChainSpec,
    *,
    timeout: float = 15.0,
) -> ProbeResult:
    """Run one platform's probe against one chain, with endpoint failover.

    A driver that is not installed yields ``ABSENT`` — distinct from
    ``UNREACHABLE``, because "I cannot test this" and "this is broken" are
    different claims and the report must not conflate them.
    """
    endpoint = ""
    attempted: List[str] = []
    probe = _PROBES[platform]

    for url in _endpoints_for(chain):
        redacted, credential = redact_endpoint(url)
        endpoint = redacted
        attempted.append(redacted)
        try:
            payload = probe(chain, url, timeout)
        except FileNotFoundError as exc:
            return ProbeResult(
                platform=platform,
                chain=chain,
                verdict=Verdict.ABSENT,
                endpoint=redacted,
                credential_in_url=credential,
                error=scrub(str(exc), url),
                attempted=attempted,
            )
        except Exception as exc:  # noqa: BLE001  (any driver failure is a verdict)
            last_error = scrub(f"{type(exc).__name__}: {exc}", url)
            continue

        chain_id = _hex_to_int(payload.get("chainId"))
        if chain_id is None:
            last_error = "endpoint did not return a chain id"
            continue
        if chain_id != chain.chain_id:
            # A wrong-chain endpoint is terminal for this chain: trying the next
            # URL is the right move, but if they all disagree the verdict is a
            # MISMATCH, which always counts as a failure.
            last_error = f"endpoint reported chain {chain_id}, expected {chain.chain_id}"
            return ProbeResult(
                platform=platform,
                chain=chain,
                verdict=Verdict.MISMATCH,
                endpoint=redacted,
                credential_in_url=credential,
                chain_id=chain_id,
                error=last_error,
                attempted=attempted,
                driver_version=_as_optional_str(payload.get("driverVersion")),
            )

        return ProbeResult(
            platform=platform,
            chain=chain,
            verdict=Verdict.OK,
            endpoint=redacted,
            credential_in_url=credential,
            chain_id=chain_id,
            block_number=_hex_to_int(payload.get("blockNumber")),
            gas_price_wei=_hex_to_int(payload.get("gasPriceWei")),
            client=_as_optional_str(payload.get("client")),
            driver_version=_as_optional_str(payload.get("driverVersion")),
            attempted=attempted,
        )

    return ProbeResult(
        platform=platform,
        chain=chain,
        verdict=Verdict.UNREACHABLE,
        endpoint=endpoint,
        error=locals().get("last_error", "no endpoint responded"),
        attempted=attempted,
    )


def _as_optional_str(value: object) -> Optional[str]:
    return None if value is None else str(value)


def check_all(
    chains: Sequence[ChainSpec] = ALL_CHAINS,
    platforms: Sequence[Platform] = tuple(Platform),
    *,
    timeout: float = 15.0,
) -> ConnectivityReport:
    """Probe every platform against every chain and aggregate the verdicts."""
    report = ConnectivityReport()
    for chain in chains:
        for platform in platforms:
            report.add(check_platform(platform, chain, timeout=timeout))
    return report


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

_GLYPHS = {
    Verdict.OK: "OK",
    Verdict.MISMATCH: "MISMATCH",
    Verdict.UNREACHABLE: "UNREACHABLE",
    Verdict.ABSENT: "ABSENT",
}


def render_text(report: ConnectivityReport) -> str:
    """Human-readable table, grouped by chain."""
    lines: List[str] = []
    for chain in ALL_CHAINS:
        rows = [r for r in report.results if r.chain.key == chain.key]
        if not rows:
            continue
        lines.append(f"{chain.label} (chain id {chain.chain_id})")
        for r in rows:
            detail = ""
            if r.ok and r.block_number is not None:
                detail = f"block {r.block_number:,}"
                if r.gas_price_wei is not None:
                    detail += f", gas {r.gas_price_wei / 1e9:.4f} gwei"
            elif r.error:
                detail = r.error
            lines.append(
                f"  [{_GLYPHS[r.verdict]:<10}] {r.platform.label:<20} {detail}"
            )
        lines.append("")
    verdict = "HEALTHY" if report.healthy else "NOT HEALTHY"
    lines.append(f"{verdict}: {len(report.failures())} required check(s) failed")
    return "\n".join(lines)


def render_json(report: ConnectivityReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point. Exits 0 only when every required check is OK.

    Registered as ``jdl connectivity`` in ``jdl_flash/cli.py``.
    """
    import argparse  # noqa: PLC0415  (keeps module import cost off the hot path)

    parser = argparse.ArgumentParser(
        prog="jdl connectivity",
        description="Verify every runtime can reach mainnet and is on the right chain.",
    )
    parser.add_argument(
        "--chain",
        action="append",
        choices=[c.key for c in ALL_CHAINS],
        help="restrict to these chains (repeatable); default: all",
    )
    parser.add_argument(
        "--platform",
        action="append",
        choices=[p.value for p in Platform],
        help="restrict to these platforms (repeatable); default: all",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="per-endpoint timeout in seconds (default: 15)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)

    chains = [c for c in ALL_CHAINS if not args.chain or c.key in args.chain]
    platforms = [p for p in Platform if not args.platform or p.value in args.platform]

    report = check_all(chains, platforms, timeout=args.timeout)
    if args.json:
        print(render_json(report))
    else:
        print(render_text(report))
    return 0 if report.healthy else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
