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
system reports. ``risk_events`` records every attempt — success, failure and
blocked — giving a true cost basis.

Pure stdlib (sqlite3 + time). No web3, no network.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

__all__ = [
    "RiskDecision",
    "RiskGovernor",
    "ALLOW",
    "BLOCK_HALT_FILE",
    "BLOCK_BREAKER",
    "BLOCK_DAILY_LOSS",
    "BLOCK_NOTIONAL",
    "BLOCK_MIN_PROFIT",
    "BLOCK_CONFIG",
]

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

_KEY_CONSECUTIVE = "consecutive_failures"
_KEY_COOLDOWN_UNTIL = "cooldown_until"


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
        day = _utc_day(self.clock() if ts is None else ts)
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(net_usd - gas_usd), 0.0) FROM risk_events "
                "WHERE day = ? AND outcome IN (?, ?)",
                (day, OUTCOME_SUCCESS, OUTCOME_FAILURE),
            ).fetchone()
        return float(row[0]) if row else 0.0

    def daily_loss_usd(self, ts: Optional[float] = None) -> float:
        """Realised loss for the current UTC day as a positive number (0 if up)."""
        return max(0.0, -self.daily_net_usd(ts))

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

        loss = self.daily_loss_usd()
        # `loss > 0` guard: with a cap of 0 ("stop the moment I'm down at all"),
        # a bare `>=` would block a fresh, flat day where nothing has happened yet.
        if loss > 0.0 and loss >= self.max_daily_loss_usd:
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
            over = failures - self.max_consecutive_failures
            cooldown = min(self.cooldown_base_s * (2.0 ** over), self.cooldown_max_s)
            self._set_state(_KEY_COOLDOWN_UNTIL, self.clock() + cooldown)
            return RiskDecision(
                False,
                BLOCK_BREAKER,
                f"circuit breaker opened after {failures} consecutive failures — "
                f"pausing execution for {cooldown:.0f}s",
                retry_after_s=cooldown,
            )

    def record_blocked(self, decision: RiskDecision) -> None:
        """Audit-log a trade this governor refused, for after-the-fact review."""
        with self._lock:
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
                "  COALESCE(SUM(gas_usd), 0.0) "
                "FROM risk_events WHERE day = ?",
                (OUTCOME_SUCCESS, OUTCOME_FAILURE, OUTCOME_BLOCKED, day),
            ).fetchone()
        successes, failures, blocked, gas = row if row else (0, 0, 0, 0.0)
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
            "today_gas_usd": float(gas),
        }
