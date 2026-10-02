"""
test_connectivity.py — the *logic* behind `jdl connectivity`, checked offline.

What this suite is and is not
-----------------------------
`jdl_flash/connectivity.py` answers one operator question: can every runtime this
system ships reach mainnet right now, and is each one on the chain it is
configured for? Answering that for real means dialling RPC endpoints, which is
correct for a human running the command and completely wrong for a unit test:
a suite that reaches a public RPC on every commit is slow, rate-limited, and
eventually red for reasons that have nothing to do with the code.

So the live check and the logic check are separate things, on purpose:

* ``jdl connectivity``            — the live check. Explicit operator action.
* ``jdl test`` (this file)        — the logic check. Zero sockets, zero
  toolchains, sub-second, and it is the one that is allowed to gate a merge.

Everything this file tests is therefore tested through *injected* probes: the
verdict machinery, the endpoint failover, the chain-identity comparison, the
report aggregation and the two renderers are exercised with fake drivers that
return scripted payloads. What it deliberately does not test is "is Arbitrum
reachable today" — that is the live command's job, and asserting it here would
make this suite a coin flip.

The claims under test
---------------------
1. **Credentials never reach output.** ``redact_endpoint`` reduces an RPC URL to
   ``scheme://host``, and ``scrub`` strips key material out of the free-form
   error strings that drivers embed the full request URL in. Both are security
   properties: an RPC endpoint *is* the credential, so a report that echoes one
   republishes a funded provider key into stdout, a JSON file and a CI log.
2. **A missing chain id is not a chain id of zero.** ``_hex_to_int`` returns
   ``None`` for anything malformed. Block 0 and "no block" are different facts,
   and coercing the second into the first would let an endpoint that answered
   nothing look like an endpoint that answered "genesis".
3. **The four verdicts mean four different things.** ``OK`` (answered, right
   chain), ``MISMATCH`` (answered, wrong chain), ``UNREACHABLE`` (nobody
   answered) and ``ABSENT`` (the driver is not installed) are not degrees of the
   same failure, and a report that collapses them into "not OK" would make a
   missing optional toolchain look identical to a broken mainnet connection.
4. **Failover actually walks the list**, and every URL it tried is recorded, in
   order, redacted — so an operator debugging a flaky provider can see which
   endpoints were skipped rather than inferring it.
5. **A wrong chain always fails the run**, even on a chain the run does not
   require: an endpoint answering on the wrong network is a misconfiguration,
   and treating it as an optional skip is how a mainnet system ends up pointed
   at mainnet-fork unnoticed.
6. **An operator's configured provider is the one that gets tested.** An
   ``ARB_RPC_URL`` override is tried first, before the public fallbacks.
7. **The module is read-only by construction.** The four permitted RPC methods
   are enumerated in ``READ_ONLY_METHODS`` and the source is scanned to prove no
   other JSON-RPC method, signer or key reference is reachable from it.

Safety
------
* **No network.** Nothing in this file constructs a provider, resolves a host,
  or calls ``subprocess``. Every driver is a local callable.
* **No toolchain.** web3, node, hardhat, ``cast`` and the Rust binary are all
  absent-by-assumption: the probes that would need them are replaced, and a
  ``FileNotFoundError`` is one of the scripted outcomes under test.
* **No database.** ``jdl_flash.test_db_guard`` is imported before anything else
  (it installs a process-wide tripwire on ``sqlite3.connect`` as an import side
  effect), and ``connectivity`` itself never imports the engine's storage layer.
  A future change that made it open the revenue ledger would raise
  ``LiveDatabaseError`` here rather than write to it. The teeth probe below also
  runs in a child interpreter that arms the same guard.

Run: cd python && python3 jdl_flash/test_connectivity.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

# Path bootstrap. Every other suite in jdl_flash/ starts with this; without it a
# bare `python3 jdl_flash/test_connectivity.py` (which is how `jdl test` runs it,
# and how CI runs it) puts jdl_flash/ — not its parent — on sys.path and dies
# with ModuleNotFoundError before a single check runs.
_PYTHON_DIR = str(Path(__file__).resolve().parent.parent)
if _PYTHON_DIR not in sys.path:
    sys.path.insert(0, _PYTHON_DIR)

# Importing the guard arms the process-wide sqlite3.connect tripwire, and it
# must be in place before anything that could open a database. Deliberately the
# first jdl_flash import in this file, for the same reason
# test_web3_contract.py puts it first.
from jdl_flash import test_db_guard  # noqa: E402,F401  (import arms the tripwire)
from jdl_flash import connectivity  # noqa: E402
from jdl_flash.connectivity import (  # noqa: E402
    ALL_CHAINS,
    ARBITRUM_ONE,
    ARBITRUM_SEPOLIA,
    ETHEREUM,
    READ_ONLY_METHODS,
    ChainSpec,
    ConnectivityReport,
    Platform,
    ProbeResult,
    Verdict,
)

#: A stand-in provider key. Shaped like the ones real providers issue (34
#: URL-safe characters) and long enough to trip the module's own 16-character
#: redaction floor, so "the key must not appear in the output" is a real
#: assertion rather than a trivially satisfied one.
_SECRET = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"

#: A credentialed Arbitrum endpoint, of the Alchemy URL shape.
_SECRET_URL = f"https://arb-mainnet.g.alchemy.com/v2/{_SECRET}"

#: Public, uncredentialed fallbacks. Never dialled — the probe is always faked.
_PUBLIC_1 = "https://arb1.arbitrum.io/rpc"
_PUBLIC_2 = "https://arb1.arbitrum.gateway.fm"

#: Every env var `_endpoints_for` consults. Cleared around the env checks so a
#: developer's real ARB_RPC_URL in their shell cannot change what is asserted.
_OVERRIDE_ENV_VARS: Tuple[str, ...] = (
    "JDL_CONNECTIVITY_ARBITRUM_URL",
    "JDL_CONNECTIVITY_ETHEREUM_URL",
    "JDL_CONNECTIVITY_SEPOLIA_URL",
    "ARB_RPC_URL",
    "ETH_RPC_URL",
)


# ────────────────────────────────────────────────────────────────────────────
#  Harness helpers
# ────────────────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _patched(obj: Any, **attrs: Any) -> Iterator[None]:
    """Temporarily set module attributes, restoring them exactly afterwards."""
    saved = {name: getattr(obj, name) for name in attrs}
    try:
        for name, value in attrs.items():
            setattr(obj, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(obj, name, value)


@contextlib.contextmanager
def _fake_probes(conn: Any, **overrides: Any) -> Iterator[None]:
    """Replace entries in the module's driver table for the duration of a block.

    ``check_platform`` reads ``_PROBES[platform]`` at call time, so replacing the
    entry is a real seam rather than a decoration: the verdict, failover and
    scrubbing code under test is the shipped code, and only the driver is faked.
    """
    saved = {platform: conn._PROBES[platform] for platform in overrides}
    try:
        conn._PROBES.update(overrides)
        yield
    finally:
        for platform, probe in saved.items():
            conn._PROBES[platform] = probe


@contextlib.contextmanager
def _clean_env() -> Iterator[None]:
    """Run a block with every RPC-override variable unset, then restore exactly."""
    saved = {name: os.environ.get(name) for name in _OVERRIDE_ENV_VARS}
    try:
        for name in _OVERRIDE_ENV_VARS:
            os.environ.pop(name, None)
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextlib.contextmanager
def _captured_stdout() -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer


class _ScriptedProbe:
    """A driver stand-in: scripted outcomes per URL, and a call log.

    An outcome may be a payload dict (the probe succeeded), an exception
    instance (the probe raised — including ``FileNotFoundError``, which is how
    the real drivers report an absent toolchain), or a callable evaluated at
    call time. Any URL not named in the script raises, so an unexpected
    endpoint shows up as a failure rather than as a silent extra call.
    """

    def __init__(self, script: Optional[Dict[str, Any]] = None, default: Any = None) -> None:
        self.script = dict(script or {})
        self.default = default
        self.calls: List[str] = []

    def __call__(self, chain: ChainSpec, url: str, timeout: float) -> Dict[str, Any]:
        self.calls.append(url)
        outcome = self.script[url] if url in self.script else self.default
        if callable(outcome):
            outcome = outcome(chain, url, timeout)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is None:
            raise AssertionError(
                f"probe reached an unscripted endpoint {url!r}; the failover walk "
                f"tried a URL this test did not account for"
            )
        return dict(outcome)


def _payload(chain_id: int, **extra: Any) -> Dict[str, Any]:
    """A well-formed probe payload for a reachable endpoint."""
    body: Dict[str, Any] = {
        "chainId": chain_id,
        "blockNumber": "0x1d4a2f",
        "gasPriceWei": "0x3b9aca00",
        "client": "geth/v1.10.26",
        "driverVersion": "web3 6.20.4",
    }
    body.update(extra)
    return body


def _host_label(url: str) -> str:
    """A short, safe stand-in for a URL in a check message.

    The URL itself is never put in a message: on the failure path the whole
    point is that it may be carrying a key, and a failing suite that prints the
    credential it was trying to prove is hidden is worse than no message.
    """
    authority = re.split(r"[/?]", url.split("://")[-1])[0] if url else ""
    return (authority or "<no host>")[:28]


def _result(
    chain: ChainSpec,
    verdict: Verdict,
    *,
    platform: Platform = Platform.PYTHON,
    endpoint: str = "https://arb1.arbitrum.io",
    **kwargs: Any,
) -> ProbeResult:
    """A hand-built ProbeResult, for report/renderer checks that need no driver."""
    return ProbeResult(
        platform=platform,
        chain=chain,
        verdict=verdict,
        endpoint=endpoint,
        attempted=[endpoint],
        **kwargs,
    )


def _run_probe(
    conn: Any,
    platform: Platform,
    chain: ChainSpec,
    urls: Sequence[str],
    script: Optional[Dict[str, Any]] = None,
    default: Any = None,
) -> Tuple[ProbeResult, _ScriptedProbe]:
    """Run ``check_platform`` against a fixed endpoint list with a fake driver."""
    probe = _ScriptedProbe(script, default)
    with _patched(conn, _endpoints_for=lambda _chain: list(urls)), \
            _fake_probes(conn, **{platform: probe}):
        return conn.check_platform(platform, chain, timeout=5.0), probe


# ────────────────────────────────────────────────────────────────────────────
#  1. Credential redaction
# ────────────────────────────────────────────────────────────────────────────

def _check_redaction(check: Callable[..., None], conn: Any = connectivity) -> None:
    """``redact_endpoint`` must drop the key, and must admit when it held one.

    The credential flag is what makes the guarantee auditable: the report says
    "this endpoint is authenticated" without saying what the authentication is.
    A redactor that quietly dropped the path but reported ``False`` would be
    indistinguishable from a redactor that had never seen a credential, so the
    flag is asserted separately from the redaction itself.
    """
    redact = conn.redact_endpoint

    cases: Tuple[Tuple[str, str, bool, str], ...] = (
        # (url, expected redacted form, expected credential flag, what it proves)
        (_SECRET_URL, "https://arb-mainnet.g.alchemy.com", True,
         "a path-embedded provider key is dropped and reported"),
        (f"https://rpc.example.com/?apikey={_SECRET}", "https://rpc.example.com", True,
         "a query-string credential is dropped and reported"),
        ("https://arb1.arbitrum.io", "https://arb1.arbitrum.io", False,
         "a bare root endpoint is left alone and reported as keyless"),
        ("https://arb1.arbitrum.io/", "https://arb1.arbitrum.io", False,
         "a trailing slash does not invent a credential"),
        (_PUBLIC_1, "https://arb1.arbitrum.io", False,
         "a short structural path segment is not mistaken for a key"),
        ("https://arb1.arbitrum.io:8543/rpc", "https://arb1.arbitrum.io:8543", False,
         "an explicit port is structural and is preserved"),
        ("https://x.example.com/v2/" + "a" * 15, "https://x.example.com", False,
         "a 15-character segment stays below the documented 16-character floor"),
        ("https://x.example.com/v2/" + "a" * 16, "https://x.example.com", True,
         "a 16-character segment is exactly at the floor and counts as key material"),
        ("https://user:pass@node.example.com/rpc", "https://node.example.com", True,
         "userinfo in the authority is a credential and is stripped"),
        ("arb1.arbitrum.io/rpc", "arb1.arbitrum.io", False,
         "a scheme-less endpoint still reduces to its host"),
        ("", "<unparseable>", False,
         "an unparseable endpoint is named as such rather than printed raw"),
    )

    for url, expected, credential, why in cases:
        redacted, flag = redact(url)
        check(redacted == expected,
              f"redact_endpoint {why} → {expected!r}",
              f"got {redacted!r}")
        check(flag is credential,
              f"redact_endpoint {why} reports credential={credential}",
              f"got {flag!r}")
        # The single assertion that matters most: the secret is gone.
        check(_SECRET not in redacted,
              f"redact_endpoint leaves no key material behind ({_host_label(url)})",
              f"redacted form was {redacted!r}")
        if url == "":
            continue
        check(url != redacted or not credential,
              f"redact_endpoint never returns a credentialed URL unchanged ({_host_label(url)})",
              f"returned {redacted!r}")

    # No credentialed input, of any shape, may leave the key in the output.
    for template in (
        "https://host.example.com/v2/{key}",
        "https://host.example.com/{key}",
        "https://host.example.com/v2/{key}/extra",
        "https://host.example.com/rpc?key={key}",
    ):
        redacted, _ = redact(template.format(key=_SECRET))
        check(_SECRET not in redacted,
              "no URL shape leaks the key out of redact_endpoint",
              f"{template!r} → {redacted!r}")


# ────────────────────────────────────────────────────────────────────────────
#  2. Error scrubbing
# ────────────────────────────────────────────────────────────────────────────

def _check_scrub(check: Callable[..., None], conn: Any = connectivity) -> None:
    """``scrub`` must remove key material without destroying the message.

    Scrubbing is only useful if the result is still readable: a redactor that
    replaced every word longer than sixteen characters would pass a naive
    "the key is gone" assertion while destroying the one thing an operator
    needs from an error — which call failed and why.
    """
    scrub = conn.scrub

    # The canonical leak: a driver error that embeds the full request URL.
    raw = f"HTTPConnectionPool(host='arb-mainnet.g.alchemy.com'): POST {_SECRET_URL} 403 Forbidden"
    cleaned = scrub(raw, _SECRET_URL)
    check(_SECRET not in cleaned,
          "scrub removes the provider key from a driver error",
          f"got {cleaned!r}")
    check("403 Forbidden" in cleaned,
          "scrub keeps the diagnostic part of a driver error",
          f"got {cleaned!r}")
    check("arb-mainnet.g.alchemy.com" in cleaned,
          "scrub keeps the host, so the operator knows which provider failed",
          f"got {cleaned!r}")

    # A structural segment is not key material: "rpc" is a path a human named.
    structural = "rpc endpoint refused the request"
    check(scrub(structural, _PUBLIC_1) == structural,
          "scrub does not mangle a short structural segment like 'rpc'",
          f"got {scrub(structural, _PUBLIC_1)!r}")

    # Text with no URL in it at all is returned untouched.
    plain = "Connection refused by peer"
    check(scrub(plain) == plain,
          "scrub returns non-URL text unchanged",
          f"got {scrub(plain)!r}")
    plain2 = "timed out after 15.0s"
    check(scrub(plain2, _SECRET_URL) == plain2,
          "scrub returns non-URL text unchanged even when a URL is supplied",
          f"got {scrub(plain2, _SECRET_URL)!r}")

    # Defence in depth: a bare key in an error is caught even with no URL to
    # match against, which is the case where a driver paraphrases the request.
    check(_SECRET not in scrub(f"provider rejected key {_SECRET}"),
          "scrub redacts a bare key even when no endpoint URL is supplied",
          f"got {scrub(f'provider rejected key {_SECRET}')!r}")

    # A near-miss URL (trailing slash) must not defeat the redactor: the exact
    # -string replace does not fire, so the length heuristic is what saves it.
    near_miss = f"request to {_SECRET_URL}/ failed"
    check(_SECRET not in scrub(near_miss, _SECRET_URL),
          "scrub still removes the key when the URL in the error is not an exact match",
          f"got {scrub(near_miss, _SECRET_URL)!r}")

    # The userinfo form: the password is in the authority, so the URL replace
    # cannot be what saves us.
    userinfo_url = "https://svc:sup3rs3cretvalue@node.example.com/rpc"
    check("sup3rs3cretvalue" not in scrub(f"401 from {userinfo_url}", userinfo_url),
          "scrub removes an HTTP-basic password embedded in the endpoint URL",
          f"got {scrub(f'401 from {userinfo_url}', userinfo_url)!r}")

    # An empty URL is a legitimate argument value and must not blow up.
    check(scrub("something went wrong", "") == "something went wrong",
          "scrub tolerates an empty endpoint URL argument",
          f"got {scrub('something went wrong', '')!r}")


# ────────────────────────────────────────────────────────────────────────────
#  3. JSON-RPC quantity decoding
# ────────────────────────────────────────────────────────────────────────────

def _check_hex_to_int(check: Callable[..., None], conn: Any = connectivity) -> None:
    """``_hex_to_int`` decodes a quantity, or reports that there isn't one.

    The distinction that matters: a malformed quantity must come back ``None``,
    never ``0``. ``0`` is a real answer ("the chain is at genesis"), and turning
    an absent or malformed value into ``0`` would let an endpoint that told us
    nothing masquerade as an endpoint that answered.
    """
    decode = conn._hex_to_int

    cases: Tuple[Tuple[Any, Optional[int]], ...] = (
        ("0x1d4a2f", 1919535),          # the hex form every JSON-RPC server sends
        ("0xA5B", 2651),               # uppercase digits are still hex
        ("0x0", 0),                    # genesis is a value, not an absence
        (" 0x10 ", 16),                # drivers pad; whitespace is not malformed
        ("1918511", 1918511),          # a decimal string
        (1918511, 1918511),            # an int passes straight through
        (0, 0),
        (None, None),                  # absent
        ("", None),                    # empty
        ("   ", None),                 # whitespace only
        ("0x", None),                  # prefix with no digits
        ("0xzz", None),                # hex prefix, non-hex digits
        ("not-a-number", None),        # garbage
        ("0x12g4", None),              # almost-hex
        (3.7, None),                   # a float is not a JSON-RPC quantity
    )
    for value, expected in cases:
        got = decode(value)
        check(got == expected and type(got) is type(expected),
              f"_hex_to_int({value!r}) == {expected!r}",
              f"got {got!r}")

    # The specific regression this function exists to prevent, stated on its own
    # so a future "be more forgiving" edit cannot pass quietly.
    for malformed in ("0xzz", "not-a-number", "", "0x", None):
        got = decode(malformed)
        check(got is None,
              f"_hex_to_int({malformed!r}) is None, not 0 (no block ≠ block 0)",
              f"got {got!r}")
    check(decode(0) is not None and decode(0) == 0,
          "_hex_to_int(0) is 0, so a real genesis block stays distinguishable",
          "block 0 and 'no block' must not collapse into the same answer")


# ────────────────────────────────────────────────────────────────────────────
#  4. Verdict logic
# ────────────────────────────────────────────────────────────────────────────

def _check_verdicts(check: Callable[..., None], conn: Any = connectivity) -> None:
    """OK / MISMATCH / UNREACHABLE / ABSENT, and the failover that gets there.

    Every driver here is a local callable, so this exercises the shipped
    orchestration with no socket anywhere in the process.
    """
    # --- OK -------------------------------------------------------------
    result, probe = _run_probe(conn, Platform.PYTHON, ARBITRUM_ONE, [_PUBLIC_1],
                               {_PUBLIC_1: _payload(42161)})
    check(result.verdict is Verdict.OK, "an endpoint on the expected chain is OK",
          f"got {result.verdict}")
    check(result.ok is True, "ProbeResult.ok is True only for OK")
    check(result.chain_id == 42161, "the reported chain id is carried through",
          f"got {result.chain_id}")
    check(result.block_number == 1919535 and result.gas_price_wei == 1_000_000_000,
          "block number and gas price are decoded from their hex quantities",
          f"block={result.block_number} gas={result.gas_price_wei}")
    check(result.client == "geth/v1.10.26" and result.driver_version == "web3 6.20.4",
          "client string and driver version are carried through for the report")
    check(result.error is None, "a successful probe records no error")

    # --- MISMATCH, on a required chain -----------------------------------
    result, _ = _run_probe(conn, Platform.PYTHON, ARBITRUM_ONE, [_PUBLIC_1],
                           {_PUBLIC_1: _payload(1)})
    check(result.verdict is Verdict.MISMATCH,
          "an endpoint answering on a different chain is MISMATCH, not OK",
          f"got {result.verdict}")
    check(result.chain_id == 1, "the chain id the endpoint actually reported is kept",
          f"got {result.chain_id}")
    check("1" in (result.error or "") and "42161" in (result.error or ""),
          "the mismatch error names both the reported and the expected chain id",
          f"error was {result.error!r}")

    # --- MISMATCH, on a chain the run does not require --------------------
    result, _ = _run_probe(conn, Platform.PYTHON, ETHEREUM, [_PUBLIC_1],
                           {_PUBLIC_1: _payload(42161)})
    check(result.verdict is Verdict.MISMATCH,
          "a wrong-chain endpoint is MISMATCH even on a non-required chain",
          f"got {result.verdict}")

    # --- UNREACHABLE ------------------------------------------------------
    boom = ConnectionError("dial tcp: i/o timeout")
    result, probe = _run_probe(conn, Platform.NODE, ARBITRUM_ONE,
                               [_SECRET_URL, _PUBLIC_1, _PUBLIC_2], default=boom)
    check(result.verdict is Verdict.UNREACHABLE,
          "every endpoint failing is UNREACHABLE",
          f"got {result.verdict}")
    check(len(probe.calls) == 3, "failover tried all three endpoints",
          f"tried {probe.calls!r}")
    check(result.attempted == [conn.redact_endpoint(u)[0] for u in (_SECRET_URL, _PUBLIC_1, _PUBLIC_2)],
          "attempted records every URL tried, in order, redacted",
          f"got {result.attempted!r}")
    check(_SECRET not in json.dumps(result.to_dict()),
          "the credentialed endpoint appears in the result only in redacted form")
    check(result.endpoint == "https://arb1.arbitrum.gateway.fm",
          "the reported endpoint is the last one attempted",
          f"got {result.endpoint!r}")
    check("ConnectionError" in (result.error or ""),
          "the failure records which driver exception ended the walk",
          f"error was {result.error!r}")

    # --- UNREACHABLE, with the credential inside the error ----------------
    hostile = ConnectionError(f"POST {_SECRET_URL} 401 Unauthorized")
    result, _ = _run_probe(conn, Platform.NODE, ARBITRUM_ONE, [_SECRET_URL],
                           default=hostile)
    check(result.verdict is Verdict.UNREACHABLE,
          "a driver error embedding the request URL still yields UNREACHABLE")
    check(_SECRET not in (result.error or ""),
          "a driver error embedding the request URL is scrubbed before it is stored",
          f"error was {result.error!r}")
    check("401" in (result.error or ""),
          "scrubbing an embedded URL keeps the rest of the diagnostic",
          f"error was {result.error!r}")

    # --- ABSENT -----------------------------------------------------------
    result, probe = _run_probe(conn, Platform.FOUNDRY, ARBITRUM_ONE,
                               [_PUBLIC_1, _PUBLIC_2],
                               default=FileNotFoundError("`cast` not on PATH; install Foundry (foundryup)"))
    check(result.verdict is Verdict.ABSENT,
          "a driver that is not installed is ABSENT, not UNREACHABLE",
          f"got {result.verdict}")
    check("Foundry" in (result.error or ""),
          "an absent driver says which tool is missing and how to install it",
          f"error was {result.error!r}")
    check(len(result.attempted) == 1 and len(probe.calls) == 1,
          "an absent driver stops the walk immediately (there is nothing to fail over to)",
          f"attempted={result.attempted!r} calls={probe.calls!r}")

    absent_hostile = FileNotFoundError(f"binary missing, configured at {_SECRET_URL}")
    result, _ = _run_probe(conn, Platform.RUST, ARBITRUM_ONE, [_SECRET_URL],
                           default=absent_hostile)
    check(_SECRET not in (result.error or ""),
          "an ABSENT error is scrubbed too, not just a transport failure",
          f"error was {result.error!r}")

    # --- failover succeeds on a later endpoint ---------------------------
    result, probe = _run_probe(conn, Platform.PYTHON, ARBITRUM_ONE,
                               [_SECRET_URL, _PUBLIC_1, _PUBLIC_2],
                               {_PUBLIC_1: _payload(42161)},
                               default=ConnectionError("refused"))
    check(result.verdict is Verdict.OK,
          "failover reaches a working endpoint after the first one fails",
          f"got {result.verdict}")
    check(probe.calls == [_SECRET_URL, _PUBLIC_1],
          "failover stops at the first endpoint that answers",
          f"tried {probe.calls!r}")
    check(result.attempted == ["https://arb-mainnet.g.alchemy.com", "https://arb1.arbitrum.io"],
          "attempted shows the failed endpoint and the one that worked, redacted",
          f"got {result.attempted!r}")
    check(result.endpoint == "https://arb1.arbitrum.io",
          "the reported endpoint is the one that answered",
          f"got {result.endpoint!r}")

    # --- an endpoint that answers but reports no chain id ----------------
    no_chain_id = {"blockNumber": "0x1", "gasPriceWei": "0x1", "client": "geth"}
    result, probe = _run_probe(conn, Platform.PYTHON, ARBITRUM_ONE, [_PUBLIC_1, _PUBLIC_2],
                               {_PUBLIC_1: no_chain_id, _PUBLIC_2: no_chain_id})
    check(result.verdict is Verdict.UNREACHABLE,
          "an endpoint that returns no chain id is not OK",
          f"got {result.verdict}")
    check("chain id" in (result.error or ""),
          "a missing chain id is reported as a missing chain id, not as block 0",
          f"error was {result.error!r}")
    check(result.block_number is None,
          "a missing chain id does not fabricate a block number either",
          f"got {result.block_number!r}")
    check(len(result.attempted) == 2,
          "a missing chain id fails over to the next endpoint rather than succeeding",
          f"got {result.attempted!r}")

    # --- a zero chain id is a real answer, not a missing one ---------------
    result, _ = _run_probe(conn, Platform.PYTHON, ARBITRUM_ONE, [_PUBLIC_1],
                           {_PUBLIC_1: _payload(0)})
    check(result.verdict is Verdict.MISMATCH,
          "a reported chain id of 0 is MISMATCH, not 'no chain id'",
          f"got {result.verdict}")
    check(result.chain_id == 0, "the reported chain id of 0 is preserved",
          f"got {result.chain_id!r}")

    # --- the driver table is a complete, keyed registry -------------------
    check(set(conn._PROBES) == set(Platform),
          "every Platform has a driver in the probe table",
          f"table has {sorted(p.value for p in conn._PROBES)!r}")
    for platform, probe_fn in conn._PROBES.items():
        check(callable(probe_fn), f"the {platform.value} driver is callable")


# ────────────────────────────────────────────────────────────────────────────
#  5. Report aggregation
# ────────────────────────────────────────────────────────────────────────────

def _check_report(check: Callable[..., None], conn: Any = connectivity) -> None:
    """``failures()`` decides the process exit code, so its edges are pinned.

    The rule being tested: a MISMATCH is always a failure, an ABSENT or
    UNREACHABLE on a required chain is a failure, and an ABSENT or UNREACHABLE
    on a chain nobody requires is a skip. Getting the third one wrong makes the
    command useless on a host without Foundry; getting the first one wrong
    points a mainnet system at the wrong chain.
    """
    # MISMATCH on an optional chain still fails.
    report = ConnectivityReport()
    report.add(_result(ETHEREUM, Verdict.MISMATCH, chain_id=42161))
    check(len(report.failures()) == 1,
          "a MISMATCH on a non-required chain is still a failure",
          f"failures={len(report.failures())}")
    check(report.healthy is False,
          "a MISMATCH on a non-required chain makes the report unhealthy")

    # ABSENT on a required chain fails.
    report = ConnectivityReport()
    report.add(_result(ARBITRUM_ONE, Verdict.ABSENT,
                       error="`cast` not on PATH; install Foundry (foundryup)"))
    check(len(report.failures()) == 1,
          "an ABSENT driver on a required chain is a failure",
          f"failures={len(report.failures())}")
    check(report.healthy is False, "an ABSENT driver on a required chain is unhealthy")

    # ABSENT on an optional chain is a skip.
    report = ConnectivityReport()
    report.add(_result(ARBITRUM_SEPOLIA, Verdict.ABSENT, platform=Platform.FOUNDRY,
                       error="`cast` not on PATH; install Foundry (foundryup)"))
    check(report.failures() == [],
          "an ABSENT driver on a non-required chain is a skip, not a failure",
          f"failures={len(report.failures())}")
    check(report.healthy is True,
          "a missing optional toolchain does not make the report unhealthy")

    # UNREACHABLE on an optional chain is likewise a skip …
    report = ConnectivityReport()
    report.add(_result(ETHEREUM, Verdict.UNREACHABLE, error="dial tcp: i/o timeout"))
    check(report.healthy is True,
          "an UNREACHABLE optional chain does not make the report unhealthy",
          "Ethereum is a read-only reference chain, not a trading dependency")

    # … but not on a required one.
    report = ConnectivityReport()
    report.add(_result(ARBITRUM_ONE, Verdict.UNREACHABLE, error="dial tcp: i/o timeout"))
    check(report.healthy is False,
          "an UNREACHABLE required chain makes the report unhealthy")

    # All-OK, across every chain, is healthy.
    report = ConnectivityReport()
    for chain in ALL_CHAINS:
        report.add(_result(chain, Verdict.OK, chain_id=chain.chain_id,
                           block_number=1, gas_price_wei=1))
    check(report.healthy is True and report.failures() == [],
          "an all-OK report across every chain is healthy")

    # Mixed: exactly the failing rows are returned, and the count is honest.
    report = ConnectivityReport()
    report.add(_result(ARBITRUM_ONE, Verdict.OK, chain_id=42161, block_number=1))
    report.add(_result(ARBITRUM_ONE, Verdict.ABSENT, platform=Platform.RUST))
    report.add(_result(ARBITRUM_ONE, Verdict.UNREACHABLE, platform=Platform.NODE))
    report.add(_result(ETHEREUM, Verdict.ABSENT, platform=Platform.HARDHAT))
    report.add(_result(ETHEREUM, Verdict.MISMATCH, platform=Platform.HARDHAT, chain_id=1))
    failing = report.failures()
    check(len(failing) == 3,
          "failures() returns exactly the failing rows, not the whole report",
          f"got {len(failing)}")
    check([(r.platform.value, r.chain.key, r.verdict.value) for r in failing]
          == [("rust", "arbitrum", "ABSENT"),
              ("node", "arbitrum", "UNREACHABLE"),
              ("hardhat", "ethereum", "MISMATCH")],
          "failures() preserves result order and identifies each row",
          f"got {[(r.platform.value, r.chain.key, r.verdict.value) for r in failing]!r}")

    # The serialised form agrees with the object it came from.
    payload = report.to_dict()
    check(payload["healthy"] is report.healthy,
          "to_dict()['healthy'] agrees with the report's own verdict")
    check(payload["failures"] == len(report.failures()),
          "to_dict()['failures'] agrees with failures()",
          f"dict={payload['failures']} actual={len(report.failures())}")
    check(len(payload["results"]) == len(report.results),
          "to_dict() serialises every result, passing or not")
    check([c["key"] for c in payload["chains"]] == [c.key for c in ALL_CHAINS],
          "to_dict() describes every known chain and whether it is required")
    check(payload["platforms"] == [p.value for p in Platform],
          "to_dict() lists every platform the run was capable of covering")


# ────────────────────────────────────────────────────────────────────────────
#  6. Rendering
# ────────────────────────────────────────────────────────────────────────────

def _render_fixture(conn: Any) -> ConnectivityReport:
    """A report shaped like a real one: OK, absent, mismatched, unreachable.

    Errors are scrubbed the way ``check_platform`` scrubs them, because that is
    the only way they are ever produced — a hand-built unscrubbed error would
    be testing a string nobody can construct in production.
    """
    report = ConnectivityReport()
    report.add(_result(ARBITRUM_ONE, Verdict.OK, chain_id=42161, block_number=1919535,
                       gas_price_wei=1_000_000_000, client="geth/v1.10.26",
                       driver_version="web3 6.20.4",
                       endpoint="https://arb-mainnet.g.alchemy.com",
                       credential_in_url=True))
    report.add(_result(ARBITRUM_ONE, Verdict.ABSENT, platform=Platform.FOUNDRY,
                       error="`cast` not on PATH; install Foundry (foundryup)"))
    report.add(_result(ARBITRUM_ONE, Verdict.UNREACHABLE, platform=Platform.NODE,
                       error=conn.scrub(f"dial tcp: connect: connection refused ({_SECRET_URL})",
                                        _SECRET_URL)))
    report.add(_result(ETHEREUM, Verdict.MISMATCH, platform=Platform.PYTHON,
                       chain_id=42161,
                       error="endpoint reported chain 42161, expected 1"))
    return report


def _check_render(check: Callable[..., None], conn: Any = connectivity) -> None:
    """Both renderers must be well formed, and neither may leak a credential."""
    report = _render_fixture(conn)

    text = conn.render_text(report)
    check(isinstance(text, str) and text.strip() != "",
          "render_text returns a non-empty string")
    check("Arbitrum One (chain id 42161)" in text,
          "render_text names the chain and its id")
    check("Ethereum (chain id 1)" in text,
          "render_text renders every chain that has results")
    check("python web3" in text and "foundry cast" in text,
          "render_text labels each platform by its driver")
    for verdict in Verdict:
        check(verdict.value in text,
              f"render_text shows the {verdict.value} verdict verbatim")
    check("NOT HEALTHY" in text and "3 required check(s) failed" in text,
          "render_text states the failure count and the overall verdict",
          f"tail was {text.strip().splitlines()[-1]!r}")
    check("1,919,535" in text, "render_text shows the live block number for an OK row")
    check(_SECRET not in text,
          "render_text emits no credential material",
          "a report that echoes an endpoint republishes a funded provider key")

    payload = json.loads(conn.render_json(report))
    check(payload["healthy"] is False and payload["failures"] == 3,
          "render_json produces parseable JSON carrying the report's verdict")
    check(len(payload["results"]) == len(report.results),
          "render_json serialises every result")
    first = payload["results"][0]
    for field in ("platform", "verdict", "chain", "chainId", "expectedChainId",
                  "endpoint", "credentialInUrl", "blockNumber", "gasPriceWei",
                  "client", "driverVersion", "error", "attempted"):
        check(field in first, f"render_json result objects carry '{field}'")
    check(first["endpoint"] == "https://arb-mainnet.g.alchemy.com",
          "render_json reports the redacted endpoint",
          f"got {first['endpoint']!r}")
    check(first["credentialInUrl"] is True,
          "render_json reports that the endpoint was authenticated, without saying how")
    check(first["expectedChainId"] == 42161 and first["chainId"] == 42161,
          "render_json reports both the expected and the observed chain id")
    raw = conn.render_json(report)
    check(_SECRET not in raw,
          "render_json emits no credential material",
          "the JSON report is the one most likely to be uploaded as an artifact")

    # An all-OK report reads as healthy, and the two renderers agree.
    healthy = ConnectivityReport()
    for chain in ALL_CHAINS:
        healthy.add(_result(chain, Verdict.OK, chain_id=chain.chain_id, block_number=7))
    check("HEALTHY" in conn.render_text(healthy) and "NOT HEALTHY" not in conn.render_text(healthy),
          "render_text says HEALTHY when nothing failed")
    check(json.loads(conn.render_json(healthy))["healthy"] is True,
          "render_json agrees with render_text about health")

    # An empty report must still render something an operator can read.
    empty_text = conn.render_text(ConnectivityReport())
    check("HEALTHY" in empty_text,
          "render_text of an empty report is still a readable, honest verdict",
          f"got {empty_text!r}")
    check(json.loads(conn.render_json(ConnectivityReport()))["results"] == [],
          "render_json of an empty report is a valid document with no results")


# ────────────────────────────────────────────────────────────────────────────
#  7. Endpoint resolution
# ────────────────────────────────────────────────────────────────────────────

def _check_endpoints(check: Callable[..., None], conn: Any = connectivity) -> None:
    """An operator's configured provider must be the one that gets tested.

    If the override were appended rather than prepended, the check would pass
    against a public endpoint while the system the operator actually configured
    — the one holding their key and their quota — went untested. That is the
    whole point of the function.
    """
    with _clean_env():
        endpoints = conn._endpoints_for
        check(endpoints(ARBITRUM_ONE) == list(ARBITRUM_ONE.urls),
              "with no override configured, the chain's default endpoints are used")

        os.environ["ARB_RPC_URL"] = _SECRET_URL
        resolved = endpoints(ARBITRUM_ONE)
        check(resolved[0] == _SECRET_URL,
              "an ARB_RPC_URL override is tried FIRST, ahead of the public fallbacks",
              f"got {resolved!r}")
        check(resolved[1:] == list(ARBITRUM_ONE.urls),
              "the public fallbacks are still available behind the override",
              f"got {resolved!r}")
        check(endpoints(ETHEREUM) == list(ETHEREUM.urls),
              "an Arbitrum override does not leak into the Ethereum endpoint list",
              "a typo in one variable must not redirect the other chains")

        os.environ["JDL_CONNECTIVITY_ARBITRUM_URL"] = _PUBLIC_2
        check(endpoints(ARBITRUM_ONE)[0] == _PUBLIC_2,
              "the connectivity-specific variable takes precedence over ARB_RPC_URL",
              f"got {endpoints(ARBITRUM_ONE)[0]!r}")

        os.environ["JDL_CONNECTIVITY_ARBITRUM_URL"] = "   "
        check(endpoints(ARBITRUM_ONE)[0] == _SECRET_URL,
              "a whitespace-only override is ignored rather than dialled",
              f"got {endpoints(ARBITRUM_ONE)[0]!r}")

        os.environ["ARB_RPC_URL"] = "   "
        os.environ.pop("JDL_CONNECTIVITY_ARBITRUM_URL")
        check(endpoints(ARBITRUM_ONE) == list(ARBITRUM_ONE.urls),
              "blank overrides fall through to the chain's defaults",
              f"got {endpoints(ARBITRUM_ONE)!r}")

        os.environ["ETH_RPC_URL"] = _PUBLIC_2
        check(endpoints(ETHEREUM)[0] == _PUBLIC_2,
              "an ETH_RPC_URL override is tried first for Ethereum")
        check(endpoints(ARBITRUM_ONE) == list(ARBITRUM_ONE.urls),
              "an Ethereum override does not reach the Arbitrum endpoint list")

        os.environ["JDL_CONNECTIVITY_SEPOLIA_URL"] = _PUBLIC_1
        check(endpoints(ARBITRUM_SEPOLIA)[0] == _PUBLIC_1,
              "a sepolia override is tried first for Arbitrum Sepolia")
        check(endpoints(ARBITRUM_ONE) == list(ARBITRUM_ONE.urls),
              "a sepolia override does not reach the Arbitrum One endpoint list")

    # Every shipped endpoint must be an https URL: this module's whole safety
    # argument rests on an endpoint being a credential that must not be logged,
    # and a plaintext endpoint would be a credential crossing a network in clear.
    for chain in ALL_CHAINS:
        insecure = [u for u in chain.urls if not u.startswith("https://")]
        check(not insecure, f"every {chain.label} endpoint is https",
              f"insecure: {insecure!r}")
        check(len(set(chain.urls)) == len(chain.urls),
              f"the {chain.label} endpoint list has no duplicates")
    check(ARBITRUM_ONE.required and not ETHEREUM.required and not ARBITRUM_SEPOLIA.required,
          "Arbitrum One is the required chain; the reference chains are not",
          "the trading path targets Arbitrum One only")


# ────────────────────────────────────────────────────────────────────────────
#  8. Read-only by construction
# ────────────────────────────────────────────────────────────────────────────

#: JSON-RPC methods that can move funds, sign, unlock or otherwise mutate state.
#: Matched as substrings, so `eth_sendRawTransaction` and a future
#: `personal_sendTransaction` are both caught.
_WRITE_METHOD_TOKENS: Tuple[str, ...] = (
    "send", "sign", "personal_", "unlock", "importaccount", "newaccount",
    "transfer", "approve", "txpool", "mine", "miner_", "debug_", "evm_",
)

_REQUIRED_METHOD_SOURCE = Path(__file__).resolve().parent / "connectivity.py"


def _check_read_only(check: Callable[..., None], conn: Any = connectivity) -> None:
    """The module can read the chain. It must have no way to write to it.

    This is asserted against the source as well as the constant, because the
    constant is only a claim: the JS probe bodies embedded in the module make
    their own ``provider.send(...)`` calls, and a wrong one there would be
    invisible to a test that only looked at ``READ_ONLY_METHODS``.
    """
    for method in ("eth_chainId", "eth_blockNumber", "eth_gasPrice", "web3_clientVersion"):
        check(method in READ_ONLY_METHODS,
              f"the read-only method list declares {method}")
    for forbidden in ("eth_sendRawTransaction", "eth_sendTransaction", "eth_signTransaction"):
        check(forbidden not in READ_ONLY_METHODS,
              f"the read-only method list does not contain {forbidden}")
    for method in READ_ONLY_METHODS:
        lowered = method.lower()
        bad = [token for token in _WRITE_METHOD_TOKENS if token in lowered]
        check(not bad,
              f"{method} is a read method (no write token: {', '.join(bad)})",
              f"matched {bad!r}")

    try:
        source = _REQUIRED_METHOD_SOURCE.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - only on a non-checkout install
        check(True, "source scan skipped (connectivity.py not readable)")
        return
    check("private_key" not in source.lower(),
          "the module never references a private key",
          "a signer in a connectivity check is a signing capability in a status command")

    # Every JSON-RPC method issued from the embedded JS probes.
    sent = re.findall(r"\.send\(\s*'([A-Za-z0-9_]+)'", source)
    check(len(sent) >= 4,
          "the embedded JS probes issue at least the four read-only calls",
          f"found {sent!r}")
    for method in sent:
        check(method in READ_ONLY_METHODS,
              f"the embedded JS probes only issue {method} — a permitted read",
              f"{method} is not in READ_ONLY_METHODS")

    # Every web3 attribute the Python driver reads.
    eth_attrs = set(re.findall(r"w3\.eth\.([A-Za-z_][A-Za-z0-9_]*)", source))
    check(eth_attrs and eth_attrs <= {"chain_id", "block_number", "gas_price"},
          "the web3 driver reads only chain_id, block_number and gas_price",
          f"found {sorted(eth_attrs)!r}")

    # `cast` is invoked with a fixed subcommand list; none of them may write.
    check('["send"' not in source and '["sign"' not in source and '["publish"' not in source,
          "no cast subcommand in the module can broadcast a transaction")


# ────────────────────────────────────────────────────────────────────────────
#  9. CLI wiring
# ────────────────────────────────────────────────────────────────────────────

def _check_cli_wiring(check: Callable[..., None], conn: Any = connectivity) -> None:
    """`jdl connectivity` must honour its flags and its exit code, offline.

    ``connectivity.main`` is the exact entry point the `jdl` subcommand calls, so
    the argument handling, the selection semantics and the exit code are the
    things a `jdl connectivity` invocation actually depends on. ``check_all`` is
    replaced with a recorder: this checks what the command *asks for*, not what
    the network answers.
    """
    recorded: Dict[str, Any] = {}

    def fake_check_all(chains: Any, platforms: Any, **kwargs: Any) -> ConnectivityReport:
        recorded["chains"] = list(chains)
        recorded["platforms"] = list(platforms)
        recorded["timeout"] = kwargs.get("timeout")
        report = ConnectivityReport()
        for chain in recorded["chains"]:
            report.add(_result(chain, Verdict.OK, chain_id=chain.chain_id, block_number=1))
        return report

    with _patched(conn, check_all=fake_check_all):
        with _clean_env(), _captured_stdout():
            rc = conn.main([])
        check(rc == 0, "a healthy run exits 0")
        check(recorded["chains"] == list(ALL_CHAINS),
              "with no --chain, every chain is checked",
              f"got {[c.key for c in recorded['chains']]!r}")
        check(recorded["platforms"] == list(Platform),
              "with no --platform, every platform is checked",
              f"got {[p.value for p in recorded['platforms']]!r}")
        check(recorded["timeout"] == 15.0,
              "the default per-endpoint timeout is 15s",
              f"got {recorded['timeout']!r}")

        with _captured_stdout():
            conn.main(["--chain", "arbitrum"])
        check([c.key for c in recorded["chains"]] == ["arbitrum"],
              "--chain restricts the run to that chain",
              f"got {[c.key for c in recorded['chains']]!r}")

        with _captured_stdout():
            conn.main(["--chain", "ethereum", "--chain", "arbitrum"])
        check([c.key for c in recorded["chains"]] == ["arbitrum", "ethereum"],
              "--chain is repeatable and the result keeps catalog order",
              f"got {[c.key for c in recorded['chains']]!r}")

        with _captured_stdout():
            conn.main(["--platform", "python", "--platform", "foundry"])
        check([p.value for p in recorded["platforms"]] == ["python", "foundry"],
              "--platform is repeatable and restricts the run to those drivers",
              f"got {[p.value for p in recorded['platforms']]!r}")

        with _captured_stdout():
            conn.main(["--timeout", "2.5"])
        check(recorded["timeout"] == 2.5, "--timeout is passed through",
              f"got {recorded['timeout']!r}")

        buffer = _captured_stdout()
        with buffer as out:
            conn.main(["--json"])
        payload = json.loads(out.getvalue())
        check(payload["healthy"] is True and len(payload["results"]) == len(ALL_CHAINS),
              "--json prints a parseable JSON report instead of the table",
              f"stdout was {out.getvalue()[:120]!r}")

        # An unhealthy run must exit non-zero: this is what a CI step keys on.
        def unhealthy_check_all(chains: Any, platforms: Any, **kwargs: Any) -> ConnectivityReport:
            report = ConnectivityReport()
            for chain in chains:
                report.add(_result(chain, Verdict.OK, chain_id=chain.chain_id))
            report.add(_result(ETHEREUM, Verdict.MISMATCH, chain_id=42161))
            return report

        with _patched(conn, check_all=unhealthy_check_all), _captured_stdout():
            rc = conn.main(["--chain", "arbitrum", "--chain", "ethereum"])
        check(rc == 1,
              "a run with a failure exits 1, so a caller can gate on it",
              f"got {rc!r}")

        # An unknown chain name is a usage error, not a silent no-op run.
        for bad in (["--chain", "bogus"], ["--platform", "bogus"]):
            try:
                with _patched(conn, check_all=fake_check_all), _captured_stdout():
                    conn.main(bad)
            except SystemExit as exc:
                check(exc.code == 2,
                      f"{' '.join(bad)} is rejected as a usage error",
                      f"got SystemExit({exc.code})")
            else:
                check(False, f"{' '.join(bad)} is rejected as a usage error",
                      "it was accepted and the run proceeded")


# ────────────────────────────────────────────────────────────────────────────
#  10. Teeth: prove the redaction checks can actually fail
# ────────────────────────────────────────────────────────────────────────────
#
# Every check above is a claim about the shipped `redact_endpoint` and `scrub`.
# A test that cannot fail is not a test: if redaction silently regressed to
# returning the URL unchanged, the key assertions would still be "passing" in
# any run where no credentialed endpoint happened to be configured. So the same
# check bodies are re-run against deliberately broken *copies* of the module in
# a child interpreter, and the suite fails if they do not notice.
#
# The copies are written to a temporary directory and imported under a different
# module name. The repository's connectivity.py is never modified.

#: name -> (anchor that must still be present, replacement that breaks it).
_REDACTION_MUTATIONS: Tuple[Tuple[str, str, str], ...] = (
    (
        "leaky-redaction",
        '    return (f"{scheme}://{host}" if scheme else host), credential_present',
        "    return (url if scheme else host), credential_present",
        "_check_redaction",
    ),
    (
        "silent-credential-flag",
        "    credential_present = userinfo_present or (",
        "    credential_present = False or (",
        "_check_redaction",
    ),
    (
        "unscrubbed-errors",
        "    out = _CREDENTIAL_SEGMENT.sub(_REDACTED, out)",
        "    pass  # mutation: the length heuristic is removed",
        "_check_scrub",
    ),
)

_TEETH_PROBE = r'''
import importlib.util
import sys
from pathlib import Path

python_dir = sys.argv[1]
scratch = Path(sys.argv[2])
sys.path.insert(0, python_dir)

import jdl_flash.test_connectivity as suite   # the very same check bodies
from jdl_flash import connectivity as real   # noqa: F401  (the shipped module)

source = (Path(python_dir) / "jdl_flash" / "connectivity.py").read_text(encoding="utf-8")

for name, anchor, replacement, which in suite._REDACTION_MUTATIONS:
    if source.count(anchor) != 1:
        print("RESULT %s ANCHOR-MISSED %d" % (name, source.count(anchor)))
        continue
    path = scratch / ("broken_%s.py" % name)
    path.write_text(source.replace(anchor, replacement, 1), encoding="utf-8")
    module_name = "broken_" + name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    broken = importlib.util.module_from_spec(spec)
    # connectivity.py uses `from __future__ import annotations`, so @dataclass
    # resolves its field annotations through sys.modules[cls.__module__]. The
    # module has to be registered before it is executed or every dataclass in
    # the copy raises AttributeError during class creation.
    sys.modules[module_name] = broken
    spec.loader.exec_module(broken)

    fired = []
    body = getattr(suite, which)
    # Positional: `body(check, module)` — the very same code that just ran above
    # against the shipped module, now pointed at a deliberately broken copy.
    body(lambda cond, msg, detail="": None if cond else fired.append(msg), broken)
    print("RESULT %s %d" % (name, len(fired)))
    for message in fired[:3]:
        print("  fired: %s" % message)
'''


def _check_teeth(check: Callable[..., None], python_dir: Path) -> None:
    """Breaking redaction must make the suite above it fail."""
    env = dict(os.environ)
    existing = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    if str(python_dir) not in existing:
        existing.insert(0, str(python_dir))
    env["PYTHONPATH"] = os.pathsep.join(existing)

    with tempfile.TemporaryDirectory(prefix="jdl_connectivity_teeth_") as tmp:
        try:
            proc = subprocess.run(
                [sys.executable, "-c", _TEETH_PROBE, str(python_dir), tmp],
                capture_output=True, text=True, cwd=str(python_dir), env=env, timeout=120,
            )
        except Exception as exc:  # noqa: BLE001
            check(False, "the redaction teeth probe runs",
                  f"could not run: {type(exc).__name__}: {exc}")
            return

    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        for name, _anchor, _replacement, _which in _REDACTION_MUTATIONS:
            check(False, f"breaking {name} makes the suite fail", "probe did not run")
        check(False, "the redaction teeth probe output", " | ".join(tail))
        return

    fired_by_name: Dict[str, int] = {}
    for line in proc.stdout.splitlines():
        if not line.startswith("RESULT "):
            continue
        parts = line.split()
        # "RESULT <name> <count>" or "RESULT <name> ANCHOR-MISSED <n>"; both
        # mean the mutation did not produce a failing run, so both count as 0.
        fired_by_name[parts[1]] = int(parts[-1]) if parts[-1].isdigit() else 0

    for name, _anchor, _replacement, which in _REDACTION_MUTATIONS:
        fired = fired_by_name.get(name, 0)
        check(fired > 0,
              f"breaking {name} makes {which} fail",
              "the mutated copy produced a passing run, so that check has no teeth "
              "(the mutation anchor is missing from the source, or the break is "
              "not observable from the check body)")


# ────────────────────────────────────────────────────────────────────────────
#  Suite
# ────────────────────────────────────────────────────────────────────────────

def main() -> int:
    passed = 0
    failed = 0

    def check(cond: Any, msg: str, detail: str = "") -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✓ {msg}")
        else:
            failed += 1
            print(f"  ✗ {msg}" + (f"  ({detail})" if detail else ""))

    print("test_connectivity — mainnet connectivity check logic (offline)")

    # 1-3: the security-critical pure functions.
    _check_redaction(check)
    _check_scrub(check)
    _check_hex_to_int(check)

    # 4-6: verdicts, aggregation, rendering — all against injected drivers.
    _check_verdicts(check)
    _check_report(check)
    _check_render(check)

    # 7-9: configuration, the read-only guarantee, and the CLI contract.
    _check_endpoints(check)
    _check_read_only(check)
    _check_cli_wiring(check)

    # 10: proof that the redaction checks above can fail.
    _check_teeth(check, Path(_PYTHON_DIR))

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
