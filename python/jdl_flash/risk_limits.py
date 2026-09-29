"""
risk_limits.py — pre-trade risk governor for the live execution path.

Why this exists
---------------
``FlashDaemon.cycle_run`` went straight from "edge found" to "broadcast" with
nothing in between. The only loss-tracking was ``self.errors += 1``, a counter
nothing ever read. That is survivable for an attended terminal session and is
not survivable for the thing this repo actually ships: an *unattended* daemon
(``jdl swarm`` / ``swarm_daemon.py``) kept alive by an auto-restarting
supervisor (``flash_supervisor.py``), executing 24/7 on mainnet.

The concrete failure it leaves open: every reverting broadcast still costs real
gas. A route that reverts for a systemic reason — a stale contract address, a
drained pool, a mispriced quote path, an RPC returning wrong state — reverts
*every* cycle. At a 15-second cycle that is 5,760 gas-burning attempts a day,
unattended, with no mechanism anywhere in the system to notice or stop it. The
existing on-chain "profit-or-revert" guarantee protects the *principal*; it does
nothing about bleeding out through gas.

What this module adds
---------------------
Four independent gates, checked before any transaction is signed:

* **Consecutive-failure circuit breaker** with exponential cooldown. N failures
  in a row open the breaker for a cooldown that doubles with each further
  failure, capped. A single success closes it.
* **Daily realised-loss cap.** Net USD across the current UTC day; once losses
  reach the cap, execution stops until the day rolls over.
* **Per-trade notional ceiling.** No single loan exceeds ``max_notional_usd``.
* **Operator kill switch.** Presence of a file (``~/.flash_loan_engine/HALT``)
  halts execution immediately. It needs no signal, no process access and no
  redeploy — ``touch``-ing a file from another shell (or from Termux:Widget on
  a phone) is enough, which is the only kind of emergency stop that reliably
  works against a supervised daemon that restarts itself.

**State is persisted in SQLite, not in memory.** This is the part that makes the
breaker real rather than decorative: ``flash_supervisor.py`` restarts the engine
on crash, and an in-memory breaker resets to zero on every restart — so exactly
the scenario that most needs the cap (a crash-looping bot) would bypass it
entirely. Failure counts, cooldown deadlines and the daily loss ledger survive
restarts.

It also closes an accounting hole: ``executions`` only records *successful*
trades, so gas burned on failures was invisible to every revenue figure the
system reports. ``risk_events`` records every attempt — success, failure,
skipped, transient and blocked — giving a true cost basis.

Two things this module is deliberately careful about
---------------------------------------------------

**The daily cap compares with a derived float tolerance.** The day's loss is a
float64 ``SUM`` over N rows, so it is only known to within that accumulation's
rounding error (~N * M * DBL_EPSILON for a cap of magnitude M) and the cap
comparison has to allow for it. A bare ``loss >= cap`` therefore lets a trade
through whenever the sum lands a few ulp short — which is not a rare
astronomical event, it is what 50 rows of $0.10 actually do: they sum to
4.999999999999998, not 5.0. See ``loss_cap_tolerance_usd`` for the derivation.

**A failed broadcast is not a skip.** When ``send()`` returns ``None`` the
engine cannot currently tell "the pre-flight simulation said this would revert"
(which is the normal case, costs nothing and must never trip anything) from
"the node rejected this for insufficient funds / a stale nonce / an
underpriced replacement" (which is a systemic bot fault that will repeat every
cycle). ``BroadcastFailureKind`` and ``classify_broadcast_failure`` tell them
apart, and ``record_broadcast_failure`` charges the hard ones to the same
ledger ``record_failure`` uses, so the breaker can finally see them.

Pure stdlib (sqlite3 + time + logging). No web3, no network.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple, Union

__all__ = [
    "RiskDecision",
    "RiskGovernor",
    "BroadcastFailureKind",
    "classify_broadcast_failure",
    "loss_cap_tolerance_usd",
    "HARD_BROADCAST_FAILURES",
    "BENIGN_BROADCAST_FAILURES",
    "ALLOW",
    "BLOCK_HALT_FILE",
    "BLOCK_BREAKER",
    "BLOCK_DAILY_LOSS",
    "BLOCK_NOTIONAL",
    "BLOCK_MIN_PROFIT",
    "BLOCK_CONFIG",
    "OUTCOME_SUCCESS",
    "OUTCOME_FAILURE",
    "OUTCOME_SKIPPED",
    "OUTCOME_TRANSIENT",
    "OUTCOME_BLOCKED",
]

_LOG = logging.getLogger(__name__)

ALLOW = "ok"
BLOCK_HALT_FILE = "halt_file"
BLOCK_BREAKER = "breaker_open"
BLOCK_DAILY_LOSS = "daily_loss"
BLOCK_NOTIONAL = "notional"
BLOCK_MIN_PROFIT = "min_profit"
BLOCK_CONFIG = "config"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    day     TEXT NOT NULL,
    outcome TEXT NOT NULL,
    net_usd REAL NOT NULL DEFAULT 0.0,
    gas_usd REAL NOT NULL DEFAULT 0.0,
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_risk_events_day ON risk_events(day);
CREATE TABLE IF NOT EXISTS risk_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_BLOCKED = "blocked"
# A trade the engine declined to broadcast at all — most often the pre-flight
# eth_call simulation showing the route would revert. Nothing reached the chain,
# so no gas was spent. Kept distinct from OUTCOME_FAILURE because conflating the
# two is actively harmful: skips are the *normal* result in an efficient market,
# and charging them gas would trip the breaker every few cycles and reach the
# daily cap within an hour of ordinary scanning.
OUTCOME_SKIPPED = "skipped"
# A broadcast attempt that failed for a reason that provably committed no gas
# and changed no on-chain state — an RPC read that timed out, a node that had
# already seen the transaction. Kept apart from OUTCOME_SKIPPED (never
# attempted) and OUTCOME_FAILURE (gas burned) because "we could not tell" and
# "it cost nothing" are different post-mortem facts, and an operator chasing a
# stuck bot needs to be able to count them without string-matching details.
OUTCOME_TRANSIENT = "transient"

_KEY_CONSECUTIVE = "consecutive_failures"
_KEY_COOLDOWN_UNTIL = "cooldown_until"

# ── daily-cap float tolerance ───────────────────────────────────────────────
#
# DBL_EPSILON (2**-52) is the unit roundoff of a float64. Summing N of them
# carries a worst-case relative error of (N-1)*u, and each row's own
# ``net_usd - gas_usd`` contributes another u before it ever reaches the SUM.
# Measured against the magnitude actually being compared, the slack that has to
# be allowed is therefore ~N * M * DBL_EPSILON, where M bounds the running
# total. The safety factor absorbs the per-row subtraction and the 1/(1-k*u)
# term of the standard bound (gamma_k = k*u/(1-k*u), not k*u).
#
# This has to be *derived* rather than fixed. A hardcoded 1e-9 is wrong at both
# ends of the scale this daemon is configured at: it is a tenth of a percent of
# a $0.01 cap (a false halt of a perfectly healthy bot) and a hundredth of a
# billionth of a $1e6 cap (useless as a bound, because that day's sum really
# can be 2e-7 away from the cap). Deriving it from the real row count and the
# real gross keeps the window a few parts in 1e13 of the money involved, so the
# cap blocks exactly when it should at every scale.
_LOSS_TOLERANCE_SAFETY = 8.0
# Backstop only. If the derived bound ever exceeded this the governor would
# start permitting trades measurably past the cap, and the correct failure
# direction for a risk limit is to stop early rather than late: an
# under-tolerance costs a fraction of a basis point of trading, an
# over-tolerance costs real money.
_LOSS_TOLERANCE_MAX_RELATIVE = 1e-9


def loss_cap_tolerance_usd(
    loss_usd: float,
    cap_usd: float,
    rows: int,
    gross_usd: float,
) -> float:
    """Float slack to allow when asking "has the day's loss reached the cap?".

    ``rows`` and ``gross_usd`` come from the ledger itself (COUNT and
    ``SUM(ABS(...))`` over the day's contributing rows) so the bound tracks the
    accumulation that actually happened instead of guessing a row count.

    Returns 0.0 for an empty day and for a cap of 0.0 — a zero cap means "stop
    the moment I am down at all", which is an exact comparison, not an
    approximate one.
    """
    n = int(rows)
    if n <= 0:
        return 0.0
    cap = abs(float(cap_usd))
    if cap == 0.0:
        return 0.0
    # sum|terms| is what the summation error is relative to; the cap bounds how
    # large the day's loss can grow before the governor halts, and `loss` is
    # what is being compared right now. Take the largest of the three.
    scale = max(abs(float(gross_usd)), cap, abs(float(loss_usd)))
    derived = _LOSS_TOLERANCE_SAFETY * n * scale * sys.float_info.epsilon
    # The clamp is a *backstop*, and it is load-bearing in the configuration this
    # module actually ships with — it must not be read as a no-op, nor as a
    # property the caller may rely on. It binds exactly when
    #
    #     8.0 * n * scale * 2**-52  >  1e-9 * cap
    #  =>  n * scale               >  5.6295e5 * cap        (562,949.95 exactly)
    #
    # The two consequences a future reader needs:
    #
    # * It binds far sooner than the row count alone suggests. At scale == cap
    #   that is n > ~563,000 rows in a day, which will never happen. But the
    #   condition is on the *product* n * scale, and this daemon's default
    #   configuration is a $25 daily-loss cap with a $500k per-trade ceiling
    #   over ~5,760 cycles/day: one day of ordinary flash-loan turnover is
    #   n*scale = 5760 * 500,000 = 2.88e9 against a 1.41e7 threshold, ~200x over.
    #   The derived bound there is 5.1e-6 and the clamp returns 2.5e-8. In short:
    #   a day with real turnover has the clamp active, a day with none does not.
    #
    # * So the return value is NOT guaranteed to be <= 1e-9 * cap by
    #   construction — that only holds on the clamped branch, while the inert
    #   branch returns the much larger derived figure. Nothing downstream may
    #   reason about the tolerance as a fraction of the cap. What the clamp buys
    #   when it binds is the deliberate trade stated on
    #   _LOSS_TOLERANCE_MAX_RELATIVE: the cap check becomes the tighter of the
    #   two, so a day at that scale may halt a few ulp later than a pure
    #   float-error analysis would demand. That is the accepted direction to be
    #   wrong in. If turnover ever grows enough for the clamp to bind routinely,
    #   the fix is to re-derive the bound for that scale, not to widen the clamp.
    return min(derived, _LOSS_TOLERANCE_MAX_RELATIVE * cap)


@dataclass(frozen=True)
class RiskDecision:
    """The verdict on one proposed trade.

    ``code`` is a stable machine-readable constant (``ALLOW`` or one of the
    ``BLOCK_*`` values) so callers and tests never have to match on prose;
    ``reason`` is the operator-facing explanation.
    """

    allowed: bool
    code: str
    reason: str
    retry_after_s: float = 0.0

    def __bool__(self) -> bool:
        return self.allowed


def _utc_day(ts: float) -> str:
    """UTC calendar day (``YYYY-MM-DD``) for a POSIX timestamp.

    UTC rather than local time so the daily cap is deterministic across the
    device timezone changes a mobile deployment actually experiences.
    """
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class _DayLedger:
    """One UTC day's worth of P&L, plus what is needed to reason about rounding.

    ``net_usd`` is the P&L (negative when down). ``gross_usd`` is the sum of the
    absolute per-row movements and ``rows`` their count: together they bound the
    float error in ``net_usd`` (see ``loss_cap_tolerance_usd``).
    """

    net_usd: float
    gross_usd: float
    rows: int


class BroadcastFailureKind(Enum):
    """Why a broadcast failed, and therefore whether it should stop the bot.

    The engine's failure path cannot distinguish "no opportunity" from "this bot
    is broken" — every one of them currently returns ``None`` from ``send()`` and
    lands on ``record_skip``. That is survivable for a skip and not survivable
    for a fault: a wallet that cannot pay its own gas, or a nonce manager that
    has lost track of the account, will fail identically on every one of the
    ~5,760 cycles in a day, and nothing in the system notices.

    Every member is classified as **hard** (counts toward the consecutive-failure
    streak, and its gas toward the daily cap) or **benign** (recorded, visible,
    but costs nothing and never advances the breaker).
    """

    #: The wallet cannot cover ``gas * price + value``. Systemic and permanent
    #: until an operator tops it up, so retrying every cycle is pure waste.
    #: HARD.
    INSUFFICIENT_FUNDS = "insufficient_funds"
    #: The nonce the bot signed with has already been consumed by another
    #: transaction. The local nonce manager is out of sync with the chain, and
    #: it will keep producing stale nonces until reconciled. HARD.
    NONCE_TOO_LOW = "nonce_too_low"
    #: The nonce is ahead of the account's transaction count, so the
    #: transaction is stuck in the mempool and every later one queues behind it.
    #: Resubmitting does not help; the gap has to be closed. HARD.
    NONCE_TOO_HIGH = "nonce_too_high"
    #: Fee below the node's floor, or below what a pending transaction already
    #: bid. The bot is not paying enough to be mined, so it silently trades
    #: nothing while appearing healthy. HARD.
    GAS_TOO_LOW = "gas_too_low"
    #: A pending transaction could not be replaced because the replacement bid
    #: did not beat it. This is the classic stuck-transaction signature. HARD.
    REPLACEMENT_UNDERPRICED = "replacement_underpriced"
    #: The node refused the request for a reason that is not one of the above
    #: (rate limits, invalid params, unsupported method, provider errors). The
    #: submission is genuinely unknown, so it must not be ignored. HARD.
    RPC_REJECTION = "rpc_rejection"
    #: The transaction was mined and reverted, or the contract refused it —
    #: owner-only, paused, bad parameter, slippage. Real gas was burned. HARD.
    CONTRACT_REJECTION = "contract_rejection"
    #: A read timed out, the socket dropped, or the endpoint was unreachable.
    #: Benign, and deliberately so: an RPC blip commits no gas, changes no
    #: on-chain state, and a bot that halted on one would stop a profitable
    #: strategy for a network hiccup. See BENIGN_BROADCAST_FAILURES.
    NETWORK_TIMEOUT = "network_timeout"
    #: "Already known" — a node or mempool already holds this exact
    #: transaction, i.e. the broadcast succeeded earlier and the retry is the
    #: tell, not the failure. Treating this as a fault would double-count a
    #: trade that is already in flight. Benign.
    ALREADY_KNOWN = "already_known"
    #: Not recognised. Assumed HARD, because the whole point of classifying is
    #: that an unexamined failure must never be silently free. A new error
    #: shape stopping the bot is a visible, recoverable inconvenience; the same
    #: error shape costing gas 5,760 times a day is not.
    UNKNOWN = "unknown"

    @property
    def is_hard(self) -> bool:
        """True when this failure must count against the breaker and the cap."""
        return self not in BENIGN_BROADCAST_FAILURES

    def __str__(self) -> str:
        return self.value


#: Kinds that must advance the breaker. The default for anything unrecognised —
#: see ``BroadcastFailureKind.UNKNOWN`` for why the asymmetry is deliberate.
HARD_BROADCAST_FAILURES = frozenset(
    kind
    for kind in BroadcastFailureKind
    if kind
    not in (
        BroadcastFailureKind.NETWORK_TIMEOUT,
        BroadcastFailureKind.ALREADY_KNOWN,
    )
)

#: Kinds that are recorded but never counted. Every one of these is a statement
#: about the *transport*, not about the bot's trading: no gas was committed and
#: no on-chain state changed, so there is nothing for a risk limit to protect.
#: Counting them is how a bot ends up halted for a whole afternoon because one
#: provider in a failover list was slow.
BENIGN_BROADCAST_FAILURES = frozenset(
    (
        BroadcastFailureKind.NETWORK_TIMEOUT,
        BroadcastFailureKind.ALREADY_KNOWN,
    )
)

# Ordered most-specific first: the first signature that matches any of the
# strings extracted from the error wins. On-chain revert evidence is checked
# ahead of the funding signatures, because a revert is the more specific and more
# expensive fact: the transaction *was* mined and gas *was* burned, so
# "execution reverted: insufficient funds" — an ERC20 balance check inside the
# contract — is a contract rejection, not the pre-inclusion funding rejection
# that "insufficient funds for gas * price + value" (which contains no revert
# marker and so still matches INSUFFICIENT_FUNDS below) describes.
_SIGNATURES: Tuple[Tuple[BroadcastFailureKind, Tuple[str, ...]], ...] = (
    (
        BroadcastFailureKind.CONTRACT_REJECTION,
        (
            "execution reverted",
            "reverted",
            "revert",
            "custom error",
            "panic code",
            "assertion failed",
            "transfer failed",
            "not owner",
            "only owner",
            "ownable",
            "paused",
            "slippage",
        ),
    ),
    (
        BroadcastFailureKind.INSUFFICIENT_FUNDS,
        (
            "insufficient funds",
            "insufficient balance",
            "insufficient eth",
            "exceeds balance",
            "balance is insufficient",
            "gas required exceeds allowance",
        ),
    ),
    (
        BroadcastFailureKind.NONCE_TOO_LOW,
        (
            "nonce too low",
            "nonce has already been used",
            "oldnonce",
            "transaction nonce is too low",
        ),
    ),
    (
        BroadcastFailureKind.NONCE_TOO_HIGH,
        (
            "nonce too high",
            "nonce is too high",
            "invalid nonce too high",
            "transaction nonce is too high",
            "nonce has a gap",
            "pending nonce",
            "oversized nonce",
            "nonce too far in the future",
        ),
    ),
    (
        BroadcastFailureKind.REPLACEMENT_UNDERPRICED,
        (
            "replacement transaction underpriced",
            "replacement fee too low",
            "replacement underpriced",
            "replacement fee",
        ),
    ),
    (
        BroadcastFailureKind.GAS_TOO_LOW,
        (
            "transaction underpriced",
            "gas price too low",
            "gas price is too low",
            "maxfeepergas less than block basefee",
            "max fee per gas less than block",
            "maxfeepergas too low",
            "tip too low",
            "fee cap too low",
            "fee too low",
            "gas price below",
            "underpriced",
        ),
    ),
    (
        BroadcastFailureKind.ALREADY_KNOWN,
        (
            "already known",
            "known transaction",
            "already imported",
            "already exists",
            "duplicate transaction",
        ),
    ),
    (
        BroadcastFailureKind.NETWORK_TIMEOUT,
        (
            "timed out",
            "timeout",
            "timeouterror",
            "connection aborted",
            "connection reset",
            "connection refused",
            "connectionerror",
            "remotedisconnected",
            "broken pipe",
            "temporary failure in name resolution",
            "name or service not known",
            "max retries exceeded",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "eof occurred",
            # TLS faults are matched by class name and by the OpenSSL failure
            # strings, never by the bare token "ssl". A 3-letter substring
            # needle is a substring match against anything the failure text
            # happens to embed, and RPC endpoints are routinely named
            # `*-ssl-proxy.example.com` — so a hard fault whose message merely
            # mentions its own relay host ("execution reverted: ... via
            # my-ssl-proxy.example.com") would classify as NETWORK_TIMEOUT,
            # which is BENIGN, and the breaker would never open for a broadcast
            # that burned gas. Every marker below is either a real exception
            # class name (SSLError across ssl/urllib3/requests,
            # SSLCertVerificationError, SSLEOFError, SSLSyscallError) or an
            # OpenSSL reason string, and the spaced/colon/underscore forms
            # cannot occur inside a hostname at all.
            "sslerror",
            "ssl error",
            "ssl:",
            "sslcertverificationerror",
            "ssleoferror",
            "sslsyscallerror",
            "sslzeroreturnerror",
            "certificate verify failed",
            "certificate_verify_failed",
        ),
    ),
    (
        BroadcastFailureKind.RPC_REJECTION,
        (
            "rpc error",
            "rpc",
            "-32000",
            "-32005",
            "-32602",
            "-32603",
            "invalid json response",
            "bad json-rpc response",
            "jsonrpc error",
            "response error",
            "web3exception",
            "rate limit",
            "limit exceeded",
            "status code 429",
            "too many requests",
            "http 401",
            "http 403",
            "method not found",
            "unsupported method",
            "server error",
            "forbidden",
            # HTTP auth rejections from the provider's edge. Checked last so a
            # revert reason that happens to be worded the same way still wins
            # as a contract rejection — "unauthorized" and "access denied" are
            # far more often an API key than an Ownable guard.
            "unauthorized",
            "access denied",
        ),
    ),
)

_MAX_ERROR_DEPTH = 4
_JSON_KEYS = ("message", "error", "detail", "reason", "data", "code")
_OBJECT_ATTRS = ("args", "data", "response", "reason", "detail", "text")

#: Interpreter control-flow exceptions. :func:`_error_texts` swallows every other
#: ``BaseException`` raised by a failure object's own dunders, but these three
#: must keep propagating: on an unattended daemon nothing should be able to
#: swallow Ctrl-C, an operator's ``sys.exit()``, or generator teardown — not
#: even a failure object with a malicious ``__getattr__``.
_CONTROL_FLOW = (KeyboardInterrupt, SystemExit, GeneratorExit)

#: The last-resort text, returned when not even a type name is readable.
#: Deliberately matches no needle in :data:`_SIGNATURES`, so a failure whose
#: every representation is unreadable degrades to
#: :attr:`BroadcastFailureKind.UNKNOWN` — hard, and therefore charged — rather
#: than to some accidental match that might be benign.
_UNREADABLE = "<unreadable>"


def _as_text(value: Any) -> str:
    """``value`` as a plain, well-behaved ``str`` — never a ``str`` subclass.

    Everything collected here is subsequently truth-tested and lowercased, and
    both are dispatch points a ``str`` subclass can override to raise, so no
    subclass instance may survive into the result. Copying via a slice normally
    produces a true ``str`` without touching ``__str__``/``__bool__``/``lower``
    — but a subclass may override ``__getitem__`` too, so the slice is guarded
    and its product re-checked.

    This sits at the bottom of the coercion chain, so it has to be total in its
    own right: :func:`_error_texts` cannot promise to degrade gracefully if the
    value it degrades *to* can still raise. It is reached from the
    ``message=`` path of :meth:`RiskGovernor.record_broadcast_failure`, which
    calls :func:`_error_texts` directly rather than through the classifier.
    """
    if type(value) is str:
        return value
    if isinstance(value, str):
        try:
            copied = value[:]
        except _CONTROL_FLOW:
            raise
        except BaseException:
            return _UNREADABLE
        if type(copied) is str:
            return copied
    return _UNREADABLE


def _type_name(obj: Any) -> str:
    """``type(obj).__name__`` as a plain ``str``, or :data:`_UNREADABLE`.

    This sits at the bottom of every fallback chain in the module, so it is
    guarded too: a metaclass may shadow ``__name__`` with a property that
    raises, and an unreadable *type* name must not be able to defeat the very
    mechanism that exists to survive unreadable objects. A class name is also
    genuinely informative here — ``SSLError``, ``ContractLogicError``,
    ``ValidationError`` are often the entire signal.
    """
    try:
        name = type(obj).__name__
    except _CONTROL_FLOW:
        raise
    except BaseException:
        return _UNREADABLE
    return _as_text(name)


def _collect_texts(error: Any, depth: int, out: List[str]) -> None:
    """Recursive body of :func:`_error_texts`; appends into ``out``.

    web3, ``requests`` and the JSON-RPC providers all bury the real cause at a
    different depth depending on version and client: a bare string, an exception
    whose ``args[0]`` is a dict, a nested ``{"error": {"message": ...}}`` body,
    or a ``requests.Response`` carrying JSON text. Rather than depend on any one
    of those shapes, walk the small object graph and collect every string found.

    Nearly every statement here is a call into foreign code — ``getattr`` on an
    attribute that may be a property, ``in`` on a dict that may override
    ``__contains__``, ``str()`` on an object that may override ``__str__`` — so
    none of it is individually defensive. The caller wraps the whole call.
    """
    if error is None or depth > _MAX_ERROR_DEPTH:
        return
    if isinstance(error, BaseException):
        out.append(_type_name(error))
        message = getattr(error, "message", None)
        if isinstance(message, (str, bytes)):
            _collect_texts(message, depth + 1, out)
        for attr in _OBJECT_ATTRS:
            value = getattr(error, attr, None)
            if value is not None:
                _collect_texts(value, depth + 1, out)
    elif isinstance(error, bytes):
        out.append(_as_text(error[:].decode("utf-8", "replace")))
    elif isinstance(error, str):
        out.append(_as_text(error))
    elif isinstance(error, dict):
        for key in _JSON_KEYS:
            if key in error:
                _collect_texts(error[key], depth + 1, out)
    elif isinstance(error, (list, tuple)):
        for item in error:
            _collect_texts(item, depth + 1, out)
    else:
        body = getattr(error, "text", None)
        if isinstance(body, (str, bytes)):
            _collect_texts(body, depth + 1, out)
            if depth < 2 and isinstance(body, str):
                # A requests.Response from Flashbots/Gelato carries its reason as
                # a JSON body, not as text.
                try:
                    _collect_texts(json.loads(_as_text(body)), depth + 1, out)
                except _CONTROL_FLOW:
                    raise
                except (ValueError, TypeError):
                    pass
        out.append(_as_text(str(error)))


def _error_texts(error: Any, depth: int = 0) -> List[str]:
    """Every string in a failure that might carry its reason, broad first.

    Total over ``Exception``: it inspects an arbitrary object through
    ``getattr``/``__str__``/``__contains__``/iteration, all of which the object
    itself controls and any of which may raise, and it degrades instead of
    propagating. Whatever was collected before the failure is kept, and the
    object's type name is appended as the fallback representation. The only
    exceptions it defers to are ``KeyboardInterrupt``, ``SystemExit`` and
    ``GeneratorExit``; see :data:`_CONTROL_FLOW`.

    Every element of the result is a plain ``str`` by construction (see
    :func:`_as_text`), so the caller's truth test and ``.lower()`` cannot be
    subverted either. Duplicates and filler are harmless, because the caller
    only ever tests substrings against the result.
    """
    out: List[str] = []
    try:
        _collect_texts(error, depth, out)
    except _CONTROL_FLOW:
        raise
    except BaseException:
        # Introspection of this object failed part-way. Degrading to the type
        # name is not a graceful no-op: losing the broadcast failure here
        # escapes an ``except`` clause and takes the original exception context
        # with it, which is exactly the bug class this module exists to close.
        out.append(_type_name(error))
    return [t for t in out if t]


def classify_broadcast_failure(error: Any) -> BroadcastFailureKind:
    """Classify a web3/JSON-RPC failure into a :class:`BroadcastFailureKind`.

    Accepts anything a caller might realistically have in hand at an ``except``
    clause — an exception, a message string, a JSON-RPC error dict, a
    ``requests.Response`` — and is total over ``Exception``: for any object,
    however hostile, it returns a kind and never propagates a failure of the
    inspection itself. The only exceptions it defers to are
    ``KeyboardInterrupt``, ``SystemExit`` and ``GeneratorExit``.

    An unrecognised failure is :attr:`BroadcastFailureKind.UNKNOWN`, which is
    hard, so an error shape nobody anticipated can never be silently free.
    That also makes UNKNOWN the right answer for the fallback below, and it is
    the safe direction: an unreadable failure is charged, never excused.
    """
    try:
        texts = _error_texts(error)
        for text in texts:
            lowered = text.lower()
            for kind, needles in _SIGNATURES:
                if any(needle in lowered for needle in needles):
                    return kind
    except _CONTROL_FLOW:
        raise
    except BaseException:
        # Unreachable while _error_texts returns only plain strs. Kept so this
        # function's advertised totality does not rest on an invariant that
        # merely happens to hold in another function, and to stay correct if a
        # future edit to _error_texts breaks it.
        return BroadcastFailureKind.UNKNOWN
    return BroadcastFailureKind.UNKNOWN


def _coerce_kind(kind: Union[BroadcastFailureKind, str]) -> BroadcastFailureKind:
    """Accept a kind, its slug, or a raw error to classify."""
    if isinstance(kind, BroadcastFailureKind):
        return kind
    if isinstance(kind, str):
        try:
            return BroadcastFailureKind(kind.strip().lower())
        except ValueError:
            return classify_broadcast_failure(kind)
    return classify_broadcast_failure(kind)


def _uncommitted_gas(gas_usd: Any) -> float:
    """Coerce a caller-supplied gas figure to a chargeable amount.

    A failure path is exactly where a value arrives malformed, and the ledger
    must not be corrupted by it: a negative or NaN figure would *increase*
    the day's net P&L, and a non-numeric one would raise out of an ``except``
    clause and lose the failure entirely. Anything unusable becomes 0.0, which
    still trips the breaker — the streak is what must survive, not the cents.
    """
    try:
        gas = float(gas_usd)
    except (TypeError, ValueError):
        return 0.0
    return gas if gas > 0.0 else 0.0  # NaN fails the comparison, so it lands here


class RiskGovernor:
    """Pre-trade gate and post-trade ledger for live execution.

    Every parameter is injectable so the whole class is testable with no clock,
    no filesystem and no real database:

        gov = RiskGovernor(db_path=":memory:", clock=fake_clock)
    """

    def __init__(
        self,
        *,
        db_path: Union[str, Path],
        max_consecutive_failures: int = 3,
        cooldown_base_s: float = 60.0,
        cooldown_max_s: float = 3600.0,
        max_daily_loss_usd: float = 25.0,
        max_notional_usd: float = 500_000.0,
        min_profit_usd: float = 0.50,
        halt_file: Optional[Union[str, Path]] = None,
        clock: Callable[[], float] = time.time,
        config_ok: bool = True,
    ) -> None:
        self.db_path = str(db_path)
        self.max_consecutive_failures = max(1, int(max_consecutive_failures))
        self.cooldown_base_s = max(0.0, float(cooldown_base_s))
        self.cooldown_max_s = max(self.cooldown_base_s, float(cooldown_max_s))
        self.max_daily_loss_usd = abs(float(max_daily_loss_usd))
        self.max_notional_usd = float(max_notional_usd)
        self.min_profit_usd = float(min_profit_usd)
        self.halt_file = Path(halt_file) if halt_file is not None else None
        self.clock = clock
        self.config_ok = bool(config_ok)
        # The swarm executes on several lanes concurrently (build_live_coordinator
        # runs send() on background threads), so the read-modify-write inside
        # record_failure — read streak, increment, maybe open the breaker — must
        # be atomic. Without this, two lanes failing at once can each read the
        # same streak and the breaker trips one failure late, every time.
        self._lock = threading.RLock()
        # Last block code written to risk_events, so a sustained halt collapses
        # to one row instead of one per refused opportunity. Cleared as soon as
        # check() allows again, so the *next* halt is recorded afresh.
        self._last_blocked_code: Optional[str] = None
        # A ":memory:" database is per-connection, so tests that use it need the
        # one connection to stay open for state to persist across calls.
        self._shared: Optional[sqlite3.Connection] = (
            sqlite3.connect(self.db_path, check_same_thread=False)
            if self.db_path == ":memory:"
            else None
        )
        with self._connect() as con:
            con.executescript(_SCHEMA)

    # ── storage ─────────────────────────────────────────────────────────────

    class _Conn:
        """Context manager that commits, and only closes non-shared connections."""

        def __init__(self, con: sqlite3.Connection, owned: bool) -> None:
            self._con = con
            self._owned = owned

        def __enter__(self) -> sqlite3.Connection:
            return self._con

        def __exit__(self, exc_type, exc, tb) -> None:
            if exc_type is None:
                self._con.commit()
            if self._owned:
                self._con.close()

    def _connect(self) -> "RiskGovernor._Conn":
        if self._shared is not None:
            return RiskGovernor._Conn(self._shared, owned=False)
        con = sqlite3.connect(self.db_path, timeout=10.0)
        return RiskGovernor._Conn(con, owned=True)

    def close(self) -> None:
        """Release the shared in-memory connection, if any."""
        if self._shared is not None:
            self._shared.close()
            self._shared = None

    def _get_state(self, key: str, default: float = 0.0) -> float:
        with self._connect() as con:
            row = con.execute(
                "SELECT value FROM risk_state WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return default
        try:
            return float(row[0])
        except (TypeError, ValueError):
            # A corrupted row must not take the daemon down; treat it as unset.
            return default

    def _set_state(self, key: str, value: float) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO risk_state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, repr(float(value))),
            )

    # ── read-side ───────────────────────────────────────────────────────────

    def consecutive_failures(self) -> int:
        """Failures since the last recorded success (survives restarts)."""
        return int(self._get_state(_KEY_CONSECUTIVE, 0.0))

    def cooldown_remaining_s(self) -> float:
        """Seconds until the breaker closes; 0.0 when it is not open."""
        return max(0.0, self._get_state(_KEY_COOLDOWN_UNTIL, 0.0) - self.clock())

    def daily_net_usd(self, ts: Optional[float] = None) -> float:
        """Net USD across the current UTC day: profit earned minus gas burned.

        Counts failures (gas with no profit) as well as successes, which is what
        makes this a real P&L rather than the success-only view ``executions``
        gives.
        """
        return self._day_ledger(_utc_day(self.clock() if ts is None else ts)).net_usd

    def daily_loss_usd(self, ts: Optional[float] = None) -> float:
        """Realised loss for the current UTC day as a positive number (0 if up)."""
        return max(0.0, -self.daily_net_usd(ts))

    def _day_ledger(self, day: str) -> "_DayLedger":
        """Net, gross and row count for one UTC day, in a single query.

        ``gross_usd`` (the sum of *absolute* per-row movements) and ``rows`` are
        not reporting metrics: they are the two facts the float tolerance on the
        daily cap is derived from, and a cap check that had to issue a second
        query to get them would race the ledger it is deciding on.
        """
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(net_usd - gas_usd), 0.0), "
                "       COALESCE(SUM(ABS(net_usd - gas_usd)), 0.0), "
                "       COUNT(*) "
                "FROM risk_events WHERE day = ? AND outcome IN (?, ?)",
                (day, OUTCOME_SUCCESS, OUTCOME_FAILURE),
            ).fetchone()
        if not row:
            return _DayLedger(0.0, 0.0, 0)
        return _DayLedger(float(row[0]), float(row[1]), int(row[2]))

    def halted(self) -> bool:
        """True when the operator kill-switch file is present."""
        return self.halt_file is not None and self.halt_file.exists()

    # ── the gate ────────────────────────────────────────────────────────────

    def check(self, loan_usd: float, profit_usd: float) -> RiskDecision:
        """Decide whether this trade may be broadcast.

        Gates are evaluated cheapest-and-most-absolute first: the kill switch and
        the config gate short-circuit before any database read.
        """
        with self._lock:
            return self._check_locked(loan_usd, profit_usd)

    def _check_locked(self, loan_usd: float, profit_usd: float) -> RiskDecision:
        if not self.config_ok:
            return RiskDecision(
                False,
                BLOCK_CONFIG,
                "configuration has unparseable values — refusing to execute on a "
                "config the engine could not fully read (run `jdl integrate` to see them)",
            )

        if self.halted():
            return RiskDecision(
                False,
                BLOCK_HALT_FILE,
                f"kill switch engaged — remove {self.halt_file} to resume",
            )

        if loan_usd > self.max_notional_usd:
            return RiskDecision(
                False,
                BLOCK_NOTIONAL,
                f"loan ${loan_usd:,.2f} exceeds the per-trade ceiling "
                f"${self.max_notional_usd:,.2f} (MAX_LOAN_USD)",
            )

        if profit_usd < self.min_profit_usd:
            return RiskDecision(
                False,
                BLOCK_MIN_PROFIT,
                f"projected profit ${profit_usd:,.4f} is below the floor "
                f"${self.min_profit_usd:,.4f} (MIN_PROFIT_USD)",
            )

        remaining = self.cooldown_remaining_s()
        if remaining > 0.0:
            return RiskDecision(
                False,
                BLOCK_BREAKER,
                f"circuit breaker open after {self.consecutive_failures()} "
                f"consecutive failures — {remaining:.0f}s remaining",
                retry_after_s=remaining,
            )

        ledger = self._day_ledger(_utc_day(self.clock()))
        loss = max(0.0, -ledger.net_usd)
        # `loss > 0` guard: with a cap of 0 ("stop the moment I'm down at all"),
        # a bare `>=` would block a fresh, flat day where nothing has happened yet.
        #
        # The tolerance is subtracted from the cap rather than added to the loss
        # so the comparison stays inclusive at the cap: reaching the cap blocks,
        # and so does landing a few ulp short of it — which is not a theoretical
        # concern but what the ledger actually produces, since 50 rows of $0.10
        # sum to 4.999999999999998 and never to 5.0.
        tolerance = loss_cap_tolerance_usd(
            loss, self.max_daily_loss_usd, ledger.rows, ledger.gross_usd
        )
        if loss > 0.0 and loss >= self.max_daily_loss_usd - tolerance:
            return RiskDecision(
                False,
                BLOCK_DAILY_LOSS,
                f"daily loss ${loss:,.2f} has reached the cap "
                f"${self.max_daily_loss_usd:,.2f} (MAX_DAILY_LOSS_USD) — "
                f"execution resumes at the next UTC day rollover",
            )

        return RiskDecision(True, ALLOW, "within all risk limits")

    # ── write-side ──────────────────────────────────────────────────────────

    def _record(self, outcome: str, net_usd: float, gas_usd: float, detail: str) -> None:
        # Any non-blocked outcome means the gate let something through, so the
        # current halt (if any) is over and the next block starts a fresh row.
        # Deliberately keyed off recorded activity rather than off check()
        # returning allowed: status() calls check(), and a reporting method must
        # never mutate the state it reports on.
        if outcome != OUTCOME_BLOCKED:
            self._last_blocked_code = None
        ts = self.clock()
        with self._connect() as con:
            con.execute(
                "INSERT INTO risk_events(ts, day, outcome, net_usd, gas_usd, detail) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (ts, _utc_day(ts), outcome, float(net_usd), float(gas_usd), detail),
            )

    def record_success(self, net_usd: float, gas_usd: float = 0.0, detail: str = "") -> None:
        """Log a confirmed on-chain execution and close the breaker.

        ``net_usd`` is profit *before* gas; gas is subtracted by the ledger so a
        "successful" trade that cost more in gas than it earned still counts
        against the daily cap, which is the only honest accounting.
        """
        with self._lock:
            self._record(OUTCOME_SUCCESS, net_usd, gas_usd, detail)
            self._set_state(_KEY_CONSECUTIVE, 0.0)
            self._set_state(_KEY_COOLDOWN_UNTIL, 0.0)

    def record_failure(self, gas_usd: float = 0.0, detail: str = "") -> RiskDecision:
        """Log a failed/reverted attempt and advance the breaker.

        Returns the resulting state as a :class:`RiskDecision` so the caller can
        log the cooldown without a second round-trip.
        """
        with self._lock:
            self._record(OUTCOME_FAILURE, 0.0, gas_usd, detail)
            failures = self.consecutive_failures() + 1
            self._set_state(_KEY_CONSECUTIVE, float(failures))

            if failures < self.max_consecutive_failures:
                return RiskDecision(
                    True,
                    ALLOW,
                    f"{failures}/{self.max_consecutive_failures} consecutive failures",
                )

            # Exponential backoff past the threshold, capped: the 1st trip waits
            # base, the 2nd 2*base, the 3rd 4*base … so a systemically broken
            # route backs off toward the cap instead of retrying at cycle speed
            # forever.
            #
            # `over` is clamped before exponentiation, not after: 2.0 ** 1024
            # raises OverflowError, and it would do so *after* the streak was
            # already committed to the database — leaving a long-running broken
            # route with an incremented counter and no armed cooldown, i.e. the
            # breaker permanently disabled. 64 doublings is already far past
            # cooldown_max_s, so the clamp changes nothing observable.
            over = min(failures - self.max_consecutive_failures, 64)
            cooldown = min(self.cooldown_base_s * (2.0 ** over), self.cooldown_max_s)
            self._set_state(_KEY_COOLDOWN_UNTIL, self.clock() + cooldown)
            return RiskDecision(
                False,
                BLOCK_BREAKER,
                f"circuit breaker opened after {failures} consecutive failures — "
                f"pausing execution for {cooldown:.0f}s",
                retry_after_s=cooldown,
            )

    def record_skip(self, detail: str = "") -> None:
        """Log an opportunity the engine declined to broadcast.

        Nothing reached the chain, so this costs no gas, does not count toward
        the daily loss cap, and does **not** advance the circuit breaker. The row
        exists purely so the skip rate is visible next to the executions.

        This is the common case in an efficient market — the pre-flight
        simulation showing a route would revert — and treating it as a failure
        would stop the bot trading within minutes of ordinary scanning.
        """
        with self._lock:
            self._record(OUTCOME_SKIPPED, 0.0, 0.0, detail)

    def record_broadcast_failure(
        self,
        kind: Union[BroadcastFailureKind, str],
        *,
        gas_usd: float = 0.0,
        detail: str = "",
        message: Any = None,
    ) -> RiskDecision:
        """Record a transaction that failed to broadcast, classified by cause.

        This is the counterpart to :meth:`record_skip`, and it exists because
        "not broadcast" was doing two incompatible jobs. A pre-flight simulation
        saying the route would revert is the *normal* result of scanning an
        efficient market and must cost nothing. A node answering "insufficient
        funds" or "nonce too low" is a *systemic bot fault* that will recur on
        every cycle for as long as it is unaddressed, and it used to be
        indistinguishable from the skip — so the circuit breaker never opened
        and no operator was ever told.

        ``kind`` may be a :class:`BroadcastFailureKind`, its slug, or the raw
        error/exception/message to classify (see
        :func:`classify_broadcast_failure`). ``message`` is the provider's own
        wording; it is stored verbatim in the event detail so a post-mortem can
        read what the node actually said, and the kind slug is prefixed so the
        row is greppable without parsing prose.

        Hard kinds (see :data:`HARD_BROADCAST_FAILURES`) are charged to the
        consecutive-failure streak and the daily loss exactly as
        :meth:`record_failure` would charge them — they share its code path, so
        there is no second implementation of the breaker to drift out of sync.
        ``gas_usd`` is the gas actually committed, so a failure the node rejected
        before inclusion passes 0.0 and still trips the breaker; that is the
        common case for insufficient funds.

        Benign kinds (:data:`BENIGN_BROADCAST_FAILURES`) are recorded and visible
        but cost nothing and cannot move the breaker, because a transport blip
        commits no gas and changes no on-chain state. A ``gas_usd`` passed for
        one of them is recorded in the detail rather than charged, so the loss
        ledger is never made to disagree with the classification.

        Returns the resulting posture as a :class:`RiskDecision` (the same
        contract as :meth:`record_failure`) so callers can report a trip without
        a second round-trip; benign kinds always return an allowed decision.
        """
        resolved = _coerce_kind(kind)
        parts = [f"broadcast_failure:{resolved.value}"]
        if message is not None:
            parts.extend(_error_texts(message))
        if detail:
            parts.append(detail)
        event_detail = " | ".join(p for p in parts if p)

        if not resolved.is_hard:
            dropped = _uncommitted_gas(gas_usd)
            note = f" (uncommitted gas ${dropped:.4f} not charged)" if dropped else ""
            with self._lock:
                self._record(OUTCOME_TRANSIENT, 0.0, 0.0, f"{event_detail}{note}")
            return RiskDecision(
                True,
                ALLOW,
                f"{resolved.value} is a transport-level failure — no gas "
                f"committed, breaker unchanged",
            )

        gas = _uncommitted_gas(gas_usd)
        decision = self.record_failure(gas, event_detail)
        if not decision.allowed:
            _LOG.warning("risk: broadcast failure (%s) — %s", resolved.value, decision.reason)
        return decision

    def record_blocked(self, decision: RiskDecision) -> None:
        """Audit-log a trade this governor refused, for after-the-fact review.

        Consecutive blocks carrying the same code collapse to a single row. A
        halt is not a moment but a state — an open cooldown or a tripped daily
        cap keeps refusing every opportunity found, once per scan cycle per
        worker, for as long as it lasts. Writing one row each would add tens of
        thousands of identical rows to the shared database during a single
        day-long halt while telling the operator nothing the first row didn't.
        """
        with self._lock:
            if decision.code == self._last_blocked_code:
                return
            self._last_blocked_code = decision.code
            self._record(OUTCOME_BLOCKED, 0.0, 0.0, f"{decision.code}: {decision.reason}")

    def reset_breaker(self) -> None:
        """Operator override: clear the failure count and any open cooldown."""
        with self._lock:
            self._set_state(_KEY_CONSECUTIVE, 0.0)
            self._set_state(_KEY_COOLDOWN_UNTIL, 0.0)

    # ── reporting ───────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Current risk posture, for ``jdl status`` and the engine's banner."""
        decision = self.check(0.0, self.min_profit_usd)
        day = _utc_day(self.clock())
        with self._connect() as con:
            row = con.execute(
                "SELECT "
                "  COALESCE(SUM(outcome = ?), 0), "
                "  COALESCE(SUM(outcome = ?), 0), "
                "  COALESCE(SUM(outcome = ?), 0), "
                "  COALESCE(SUM(outcome = ?), 0), "
                "  COALESCE(SUM(outcome = ?), 0), "
                "  COALESCE(SUM(gas_usd), 0.0) "
                "FROM risk_events WHERE day = ?",
                (
                    OUTCOME_SUCCESS,
                    OUTCOME_FAILURE,
                    OUTCOME_BLOCKED,
                    OUTCOME_SKIPPED,
                    OUTCOME_TRANSIENT,
                    day,
                ),
            ).fetchone()
        successes, failures, blocked, skipped, transient, gas = (
            row if row else (0, 0, 0, 0, 0, 0.0)
        )
        return {
            "day": day,
            "executing": decision.allowed,
            "block_code": None if decision.allowed else decision.code,
            "block_reason": None if decision.allowed else decision.reason,
            "consecutive_failures": self.consecutive_failures(),
            "max_consecutive_failures": self.max_consecutive_failures,
            "cooldown_remaining_s": self.cooldown_remaining_s(),
            "daily_net_usd": self.daily_net_usd(),
            "daily_loss_usd": self.daily_loss_usd(),
            "max_daily_loss_usd": self.max_daily_loss_usd,
            "max_notional_usd": self.max_notional_usd,
            "min_profit_usd": self.min_profit_usd,
            "halted": self.halted(),
            "halt_file": str(self.halt_file) if self.halt_file else None,
            "today_successes": int(successes),
            "today_failures": int(failures),
            "today_blocked": int(blocked),
            "today_skipped": int(skipped),
            "today_transient": int(transient),
            "today_gas_usd": float(gas),
        }
