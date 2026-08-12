"""
Tests for risk_limits.py — the pre-trade risk governor.

Fully hermetic: an injected clock, a temp-file or in-memory SQLite database, and
a temp-dir kill-switch path. No chain, no network, no sleeping.
Run: cd python && python3 jdl_flash/test_risk_limits.py
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdl_flash.risk_limits import (
    ALLOW,
    BLOCK_BREAKER,
    BLOCK_CONFIG,
    BLOCK_DAILY_LOSS,
    BLOCK_HALT_FILE,
    BLOCK_MIN_PROFIT,
    BLOCK_NOTIONAL,
    RiskGovernor,
)


class FakeClock:
    """A controllable clock: starts at a fixed UTC instant, advanced explicitly."""

    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  ✓ {msg}")
        else:
            failed += 1; print(f"  ✗ {msg}")

    def gov(**kwargs):
        """A governor with permissive defaults; override per test."""
        clock = kwargs.pop("clock", FakeClock())
        params = dict(
            db_path=":memory:",
            max_consecutive_failures=3,
            cooldown_base_s=60.0,
            cooldown_max_s=3600.0,
            max_daily_loss_usd=25.0,
            max_notional_usd=500_000.0,
            min_profit_usd=0.50,
            clock=clock,
        )
        params.update(kwargs)
        g = RiskGovernor(**params)
        g._test_clock = clock  # convenience handle for the tests
        return g

    # ── the happy path ───────────────────────────────────────────────────────
    g = gov()
    d = g.check(loan_usd=10_000.0, profit_usd=5.0)
    check(d.allowed and d.code == ALLOW, "a normal trade inside every limit is allowed")
    check(bool(d) is True, "RiskDecision is truthy when allowed")

    # ── per-trade notional ceiling ───────────────────────────────────────────
    g = gov(max_notional_usd=100_000.0)
    d = g.check(loan_usd=100_000.01, profit_usd=5.0)
    check(not d.allowed and d.code == BLOCK_NOTIONAL, "a loan over the notional ceiling is blocked")
    check("MAX_LOAN_USD" in d.reason, "the notional block names the env var to change")
    check(g.check(loan_usd=100_000.0, profit_usd=5.0).allowed, "a loan exactly at the ceiling is allowed")

    # ── profit floor ─────────────────────────────────────────────────────────
    g = gov(min_profit_usd=1.0)
    check(not g.check(10_000.0, 0.99).allowed, "profit below the floor is blocked")
    check(g.check(10_000.0, 1.0).allowed, "profit exactly at the floor is allowed")
    check(g.check(10_000.0, 0.99).code == BLOCK_MIN_PROFIT, "the sub-floor block is reported as such")

    # ── consecutive-failure circuit breaker ──────────────────────────────────
    clock = FakeClock()
    g = gov(clock=clock, max_consecutive_failures=3, cooldown_base_s=60.0)
    check(g.record_failure(0.10, "revert").allowed, "1 failure does not open the breaker")
    check(g.record_failure(0.10, "revert").allowed, "2 failures do not open the breaker")
    tripped = g.record_failure(0.10, "revert")
    check(not tripped.allowed and tripped.code == BLOCK_BREAKER, "the 3rd consecutive failure opens the breaker")
    check(tripped.retry_after_s == 60.0, "the first trip waits the base cooldown")
    check(g.consecutive_failures() == 3, "the failure streak is tracked")

    d = g.check(10_000.0, 5.0)
    check(not d.allowed and d.code == BLOCK_BREAKER, "an open breaker blocks new trades")
    check(d.retry_after_s > 0, "the block reports how long is left")

    clock.advance(59.0)
    check(not g.check(10_000.0, 5.0).allowed, "the breaker is still open one second before expiry")
    clock.advance(2.0)
    check(g.check(10_000.0, 5.0).allowed, "the breaker closes once the cooldown elapses")

    # ── exponential backoff past the threshold ───────────────────────────────
    clock = FakeClock()
    g = gov(clock=clock, max_consecutive_failures=2, cooldown_base_s=10.0, cooldown_max_s=40.0)
    g.record_failure(0.0)
    check(g.record_failure(0.0).retry_after_s == 10.0, "1st trip -> base cooldown")
    check(g.record_failure(0.0).retry_after_s == 20.0, "2nd -> doubled")
    check(g.record_failure(0.0).retry_after_s == 40.0, "3rd -> doubled again")
    check(g.record_failure(0.0).retry_after_s == 40.0, "cooldown is capped at cooldown_max_s")

    # ── a success closes the breaker and clears the streak ───────────────────
    clock = FakeClock()
    g = gov(clock=clock, max_consecutive_failures=2, cooldown_base_s=60.0)
    g.record_failure(0.10)
    g.record_failure(0.10)
    check(not g.check(10_000.0, 5.0).allowed, "breaker open after the streak")
    g.record_success(net_usd=10.0, gas_usd=0.10)
    check(g.consecutive_failures() == 0, "a success resets the failure streak")
    check(g.cooldown_remaining_s() == 0.0, "a success clears an open cooldown")
    check(g.check(10_000.0, 5.0).allowed, "trading resumes immediately after a success")

    # ── daily loss cap ───────────────────────────────────────────────────────
    clock = FakeClock()
    g = gov(clock=clock, max_daily_loss_usd=5.0, max_consecutive_failures=1_000)
    for _ in range(49):
        g.record_failure(gas_usd=0.10)
    check(abs(g.daily_loss_usd() - 4.90) < 1e-6, "gas burned on failures accumulates as daily loss")
    check(g.check(10_000.0, 5.0).allowed, "under the cap, trading continues")
    g.record_failure(gas_usd=0.10)
    check(abs(g.daily_loss_usd() - 5.0) < 1e-6, "loss reaches the cap")
    d = g.check(10_000.0, 5.0)
    check(not d.allowed and d.code == BLOCK_DAILY_LOSS, "reaching the daily cap blocks trading")
    check("MAX_DAILY_LOSS_USD" in d.reason, "the daily-loss block names the env var to change")

    # Profit offsets loss within the same day — this is a net P&L, not a gross
    # spend counter.
    g.record_success(net_usd=20.0, gas_usd=0.10)
    check(g.daily_loss_usd() == 0.0, "profit within the day offsets accumulated loss")
    check(g.check(10_000.0, 5.0).allowed, "trading resumes once the day is net positive again")

    # ── the cap is per UTC day, and rolls over ───────────────────────────────
    clock = FakeClock()
    g = gov(clock=clock, max_daily_loss_usd=1.0, max_consecutive_failures=1_000)
    g.record_failure(gas_usd=1.50)
    check(not g.check(10_000.0, 5.0).allowed, "the cap blocks after a big loss")
    clock.advance(24 * 3600)
    check(g.daily_loss_usd() == 0.0, "the loss ledger is scoped to the UTC day")
    check(g.check(10_000.0, 5.0).allowed, "trading resumes after the UTC day rolls over")

    # A zero cap means "stop the moment I'm down at all" — it must not block a
    # fresh, flat day on which nothing has happened yet.
    g = gov(max_daily_loss_usd=0.0, max_consecutive_failures=1_000)
    check(g.check(10_000.0, 5.0).allowed, "a zero daily cap still allows trading on a flat day")
    g.record_failure(gas_usd=0.01)
    check(not g.check(10_000.0, 5.0).allowed, "a zero daily cap blocks as soon as anything is lost")

    # ── a 'successful' trade that cost more gas than it earned still counts ───
    g = gov(max_daily_loss_usd=1.0, max_consecutive_failures=1_000)
    g.record_success(net_usd=0.20, gas_usd=1.50)
    check(abs(g.daily_loss_usd() - 1.30) < 1e-6,
          "a profit smaller than its gas is recorded as a net loss, not a win")
    check(not g.check(10_000.0, 5.0).allowed, "such trades can trip the daily cap")

    # ── operator kill switch ─────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        halt = os.path.join(tmp, "HALT")
        g = gov(halt_file=halt)
        check(g.check(10_000.0, 5.0).allowed, "no halt file -> trading allowed")
        check(not g.halted(), "halted() is False with no file")
        open(halt, "w").close()
        d = g.check(10_000.0, 5.0)
        check(not d.allowed and d.code == BLOCK_HALT_FILE, "creating the halt file stops trading immediately")
        check(halt in d.reason, "the halt block tells the operator which file to remove")
        check(g.halted(), "halted() is True with the file present")
        os.remove(halt)
        check(g.check(10_000.0, 5.0).allowed, "removing the halt file resumes trading, no restart needed")

    # ── unparseable config fails closed ──────────────────────────────────────
    g = gov(config_ok=False)
    d = g.check(10_000.0, 5.0)
    check(not d.allowed and d.code == BLOCK_CONFIG, "a config with unparseable values blocks all execution")

    # ── persistence across restarts (the supervisor case) ────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "flash.db")
        clock = FakeClock()
        first = RiskGovernor(db_path=db, max_consecutive_failures=3,
                             cooldown_base_s=60.0, max_daily_loss_usd=25.0, clock=clock)
        first.record_failure(gas_usd=0.10)
        first.record_failure(gas_usd=0.10)
        first.record_failure(gas_usd=0.10)
        check(not first.check(10_000.0, 5.0).allowed, "breaker open before the 'restart'")

        # flash_supervisor.py restarts the process; a fresh governor over the same
        # database must inherit the breaker rather than starting from a clean
        # slate — otherwise a crash-looping bot bypasses the cap entirely.
        second = RiskGovernor(db_path=db, max_consecutive_failures=3,
                              cooldown_base_s=60.0, max_daily_loss_usd=25.0, clock=clock)
        check(second.consecutive_failures() == 3, "the failure streak survives a process restart")
        check(not second.check(10_000.0, 5.0).allowed, "the open breaker survives a process restart")
        check(abs(second.daily_loss_usd() - 0.30) < 1e-6, "the daily loss ledger survives a process restart")

        clock.advance(61.0)
        check(second.check(10_000.0, 5.0).allowed, "the restored cooldown still expires on schedule")

    # ── operator override ────────────────────────────────────────────────────
    g = gov(max_consecutive_failures=1)
    g.record_failure(gas_usd=0.0)
    check(not g.check(10_000.0, 5.0).allowed, "breaker open")
    g.reset_breaker()
    check(g.check(10_000.0, 5.0).allowed and g.consecutive_failures() == 0,
          "reset_breaker() clears the streak and the cooldown")

    # ── concurrency: the swarm runs lanes on parallel threads ────────────────
    # Without a lock around read-streak/increment, simultaneous failures lose
    # counts and the breaker trips late (or never).
    g = gov(max_consecutive_failures=1_000)
    threads = [threading.Thread(target=g.record_failure, args=(0.01,)) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(g.consecutive_failures() == 50, "50 concurrent failures are all counted (no lost updates)")
    check(abs(g.daily_loss_usd() - 0.50) < 1e-6, "concurrent gas costs all land in the ledger")

    # ── blocked trades are audited but cost nothing ──────────────────────────
    g = gov(min_profit_usd=10.0)
    d = g.check(10_000.0, 1.0)
    g.record_blocked(d)
    check(g.daily_loss_usd() == 0.0, "a blocked trade adds no loss (nothing was broadcast)")
    check(g.status()["today_blocked"] == 1, "a blocked trade is still recorded for review")

    # ── status report ────────────────────────────────────────────────────────
    clock = FakeClock()
    g = gov(clock=clock, max_consecutive_failures=2, cooldown_base_s=30.0)
    g.record_success(net_usd=12.0, gas_usd=0.20)
    g.record_failure(gas_usd=0.10)
    s = g.status()
    check(s["today_successes"] == 1 and s["today_failures"] == 1, "status counts today's outcomes")
    check(abs(s["today_gas_usd"] - 0.30) < 1e-6, "status totals today's gas spend")
    check(abs(s["daily_net_usd"] - 11.70) < 1e-6, "status reports net P&L (profit minus all gas)")
    check(s["executing"] is True and s["block_code"] is None, "status reports an unblocked governor")
    check(s["consecutive_failures"] == 1, "status reports the current failure streak")
    g.record_failure(gas_usd=0.10)
    s = g.status()
    check(s["executing"] is False and s["block_code"] == BLOCK_BREAKER,
          "status reports why the governor is blocked")
    check(s["cooldown_remaining_s"] == 30.0, "status reports the remaining cooldown")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
