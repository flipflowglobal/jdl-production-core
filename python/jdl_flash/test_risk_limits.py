"""
Tests for risk_limits.py — the pre-trade risk governor.

Fully hermetic: an injected clock, a temp-file or in-memory SQLite database, and
a temp-dir kill-switch path. No chain, no network, no sleeping.
Run: cd python && python3 jdl_flash/test_risk_limits.py
"""
import logging
import os
import sqlite3
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# risk_limits logs a warning when a broadcast failure opens the breaker; this
# bare-script harness configures no handlers, so silence it to keep the Results
# clean. The warning is exercised deliberately; it is not under test here.
#
# Scoped to the module under test, and set on *that logger's own level* rather
# than via logging.disable(). disable() writes the process-global
# root.manager.disable threshold, after which no logger anywhere in the process
# can emit anything — not even CRITICAL — which is a spectacularly rude thing
# for a test harness to do to whatever imports it next. The test near the end
# of this file asserts that the global gate is still open.
logging.getLogger("jdl_flash.risk_limits").setLevel(logging.CRITICAL + 1)

from jdl_flash.risk_limits import (
    ALLOW,
    BLOCK_BREAKER,
    BLOCK_CONFIG,
    BLOCK_DAILY_LOSS,
    BLOCK_HALT_FILE,
    BLOCK_MIN_PROFIT,
    BLOCK_NOTIONAL,
    BENIGN_BROADCAST_FAILURES,
    BroadcastFailureKind,
    HARD_BROADCAST_FAILURES,
    OUTCOME_TRANSIENT,
    RiskGovernor,
    classify_broadcast_failure,
    loss_cap_tolerance_usd,
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

    # ── skips: the normal case, and they must cost nothing ───────────────────
    # A route whose pre-flight simulation says it would revert never reaches the
    # chain. Treating those as failures would trip the breaker every few cycles
    # and reach the daily cap within the hour, on a bot that spent nothing.
    g = gov(max_consecutive_failures=3, max_daily_loss_usd=25.0)
    for _ in range(500):
        g.record_skip("simulation would revert")
    check(g.consecutive_failures() == 0, "500 skips never advance the failure streak")
    check(g.daily_loss_usd() == 0.0, "skips add no loss — no gas was spent")
    check(g.check(10_000.0, 5.0).allowed, "the breaker stays closed through a long skip run")
    check(g.status()["today_skipped"] == 500, "skips are still counted for visibility")

    # A skip must not reset a real failure streak either — otherwise one skip
    # between two reverts would keep the breaker permanently disarmed.
    g = gov(max_consecutive_failures=3)
    g.record_failure(0.10)
    g.record_skip("simulation would revert")
    g.record_failure(0.10)
    check(g.consecutive_failures() == 2, "a skip between failures does not reset the streak")

    # ── breaker cooldown cannot overflow ─────────────────────────────────────
    # 2.0 ** 1024 raises OverflowError, and it would do so after the streak was
    # already committed — leaving the counter advanced and no cooldown armed.
    clock = FakeClock()
    g = gov(clock=clock, max_consecutive_failures=1, cooldown_base_s=60.0, cooldown_max_s=3600.0)
    last = None
    for _ in range(1200):
        last = g.record_failure(0.0)
    check(last is not None and not last.allowed, "the breaker is still armed after 1200 failures")
    check(last.retry_after_s == 3600.0, "cooldown saturates at the cap rather than overflowing")
    check(not g.check(10_000.0, 5.0).allowed, "an extreme streak leaves the breaker open, not disabled")

    # ── blocked trades are audited but cost nothing ──────────────────────────
    g = gov(min_profit_usd=10.0)
    d = g.check(10_000.0, 1.0)
    g.record_blocked(d)
    check(g.daily_loss_usd() == 0.0, "a blocked trade adds no loss (nothing was broadcast)")
    check(g.status()["today_blocked"] == 1, "a blocked trade is still recorded for review")

    # A halt is a state, not an event: an open cooldown or a tripped daily cap
    # refuses every opportunity found, once per cycle per worker, for as long as
    # it lasts. One row per refusal would be tens of thousands of identical rows
    # in the shared database during a single day-long halt.
    for _ in range(1000):
        g.record_blocked(g.check(10_000.0, 1.0))
    check(g.status()["today_blocked"] == 1, "1000 consecutive identical blocks collapse to one row")

    clock = FakeClock()
    g = gov(clock=clock, min_profit_usd=10.0, max_consecutive_failures=1, cooldown_base_s=60.0)
    g.record_blocked(g.check(10_000.0, 1.0))          # min_profit block
    g.record_failure(0.0)                              # opens the breaker
    g.record_blocked(g.check(10_000.0, 50.0))          # different code: breaker
    check(g.status()["today_blocked"] == 2, "a block with a different cause is recorded separately")

    clock.advance(120.0)
    check(g.check(10_000.0, 50.0).allowed, "breaker closed after the cooldown")
    g.record_skip("traded through")                    # real activity ends the halt
    g.record_blocked(g.check(10_000.0, 1.0))           # min_profit again, after trading resumed
    check(g.status()["today_blocked"] == 3,
          "a repeat cause is recorded again once real activity happened in between")

    # status() is a reporting call and must not disturb the dedup state it reads.
    g = gov(min_profit_usd=10.0)
    g.record_blocked(g.check(10_000.0, 1.0))
    g.status(); g.status(); g.status()
    g.record_blocked(g.check(10_000.0, 1.0))
    check(g.status()["today_blocked"] == 1, "calling status() does not reset block deduplication")

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

    # ── broadcast failure classification: every member is reachable ─────────
    # Real web3/JSON-RPC error shapes — the shapes a retry loop actually sees.
    BFC = classify_broadcast_failure
    KIND = BroadcastFailureKind
    representative = [
        (BFC("insufficient funds for gas * price + value: address 0xAbC... "
             "has 100000000000 wei but wants 500000000000"),
         KIND.INSUFFICIENT_FUNDS,
         "a raw gas/funds shortfall is recognised"),
        (BFC("insufficient funds for intrinsic transaction cost"),
         KIND.INSUFFICIENT_FUNDS,
         "geth's intrinsic-cost wording is recognised"),
        (BFC("nonce too low: address 0x1, tx: 42 state: 44"),
         KIND.NONCE_TOO_LOW,
         "a stale nonce is recognised as low"),
        (BFC("nonce too high: address 0x1, tx: 50 state: 44"),
         KIND.NONCE_TOO_HIGH,
         "a nonce ahead of the account is recognised as high"),
        (BFC("maxFeePerGas less than block baseFee"),
         KIND.GAS_TOO_LOW,
         "an EIP-1559 fee underbid is recognised"),
        (BFC("replacement transaction underpriced"),
         KIND.REPLACEMENT_UNDERPRICED,
         "a stuck-transaction replacement underbid is recognised"),
        (BFC("execution reverted: FlashLoanFailed()"),
         KIND.CONTRACT_REJECTION,
         "a mined-and-reverted contract call is recognised"),
        (BFC('{"jsonrpc":"2.0","error":{"code":-32603,'
             '"message":"invalid parameters"}}'),
         KIND.RPC_REJECTION,
         "a JSON-RPC -32603 rejection is recognised"),
        (BFC("HTTPConnectionPool(host='eth.rpc.example', port=443): "
             "Max retries exceeded with url: / (Caused by NewConnectionError("
             "'<...>: Failed to establish a new connection: [Errno -2] "
             "Name or service not known'))"),
         KIND.NETWORK_TIMEOUT,
         "a connection error with a hostname failure is a network timeout"),
        (BFC("already known"),
         KIND.ALREADY_KNOWN,
         "a duplicate-broadcast tell is recognised as benign"),
        (BFC("some novel error wording the node has never produced before"),
         KIND.UNKNOWN,
         "an unclassifiable failure is UNKNOWN, not a crash or a guess"),
    ]
    for got, expect, label in representative:
        check(got is expect, f"classify_broadcast_failure: {label}")

    # ── broadcast failure classification: input shapes ──────────────────────
    class Web3Error(Exception):
        """Lookalike for web3's exception objects carrying a .message."""

        def __init__(self, message, code=None, data=None):
            super().__init__(message)
            self.message = message
            self.code = code
            self.data = data

    check(BFC(Web3Error("execution reverted: ERC20InsufficientBalance")) is
          KIND.CONTRACT_REJECTION,
          "an exception object is unwrapped and classified")
    check(BFC(Web3Error("insufficient funds for gas * price + value", code=-32000))
          is KIND.INSUFFICIENT_FUNDS,
          "an exception whose .message is the funding error is classified")
    check(BFC({"error": {"code": -32000, "message": "nonce too low: tx 42 state 44"}})
          is KIND.NONCE_TOO_LOW,
          "a nested JSON-RPC error dict is classified")
    check(BFC(["outer wrapper", {"reason": "replacement transaction underpriced"}])
          is KIND.REPLACEMENT_UNDERPRICED,
          "a list wrapping the real reason is searched through")
    check(BFC(b"insufficient funds for gas * price + value")
          is KIND.INSUFFICIENT_FUNDS,
          "a bytes payload is decoded and classified")
    check(BFC(Web3Error("execution reverted: ERC20: transfer amount exceeds balance"))
          is KIND.CONTRACT_REJECTION,
          "revert wording outranks funding wording when they co-occur")
    check(BFC(None) is KIND.UNKNOWN and BFC(12345) is KIND.UNKNOWN,
          "garbage inputs (None, ints) never raise and never classify")

    # ── classify_broadcast_failure is total over hostile objects ─────────────
    # record_broadcast_failure is called from an `except` clause precisely so
    # that a failure is never lost. If the classifier itself raised while
    # introspecting the exception, the new error would escape the handler and
    # destroy both the broadcast failure and the original exception context —
    # the exact bug class this module exists to close. So every operation the
    # walk performs on a caller-supplied object is adversarial: getattr on a
    # property that raises, str() on a hostile __str__, `in` on a dict that
    # overrides __contains__. Each of the four shapes below raised before
    # _error_texts was made total.
    class RaisingStr:
        def __str__(self):
            raise RuntimeError("__str__ exploded")

    class RaisingGetattr:
        def __getattr__(self, name):
            raise RuntimeError("__getattr__ exploded for %r" % (name,))

    class RaisingMessageProperty(Exception):
        @property
        def message(self):
            raise RuntimeError(".message exploded")

    class RaisingContains(dict):
        def __contains__(self, key):
            raise RuntimeError("__contains__ exploded for %r" % (key,))

    class RaisingIter(list):
        def __iter__(self):
            raise RuntimeError("__iter__ exploded")

    class RaisingStrSubclass(str):
        def __str__(self):
            raise RuntimeError("__str__ exploded")

        def __bool__(self):
            raise RuntimeError("__bool__ exploded")

        def lower(self):
            raise RuntimeError(".lower exploded")

        def __getitem__(self, key):
            raise RuntimeError("__getitem__ exploded")

    class HostileTypeNameMeta(type):
        @property
        def __name__(cls):
            # The type name itself is a hostile str, so even the last-resort
            # fallback representation is booby-trapped.
            return RaisingStrSubclass("SSLError")

    class HostileTypeName(BaseException, metaclass=HostileTypeNameMeta):
        pass

    hostile = [
        (RaisingStr(), "an object whose __str__ raises"),
        (RaisingGetattr(), "an object whose __getattr__ raises"),
        (RaisingMessageProperty("boom"), "an exception whose .message property raises"),
        (RaisingContains({"message": "nonce too low"}), "a dict whose __contains__ raises"),
        (RaisingIter(["nonce too low"]), "a sequence whose __iter__ raises"),
        (RaisingStrSubclass("nonce too low: tx 42"),
         "a str subclass whose dunders all raise"),
        (HostileTypeName("nonce too low: tx 42"),
         "an exception whose type name is a hostile str"),
    ]

    def safe_classify(shape):
        """Classify, reporting a raise as a failed check instead of dying.

        A regression here must surface as one ✗ line, not as a traceback that
        aborts the run and hides every check after it.
        """
        try:
            got = BFC(shape)
        except BaseException as exc:  # noqa: BLE001 - the point of the test
            return None, "%s: %s" % (type(exc).__name__, exc)
        return got, None

    def safe_record_as_message(shape):
        """Push a shape through record_broadcast_failure(message=...) instead.

        That path calls _error_texts directly rather than through the
        classifier, so it is the one place a raise would escape the governor
        and lose the failure from inside the ledger write itself.
        """
        g = gov(max_consecutive_failures=1_000)
        try:
            g.record_broadcast_failure(KIND.UNKNOWN, gas_usd=0.10, message=shape)
        except BaseException as exc:  # noqa: BLE001 - the point of the test
            return None, "%s: %s" % (type(exc).__name__, exc)
        return g.consecutive_failures(), None

    for shape, label in hostile:
        got, err = safe_classify(shape)
        check(got is not None, f"a kind is returned for {label}")
        check(err is None, f"classify_broadcast_failure does not raise on {label}")
        streak, err2 = safe_record_as_message(shape)
        check(streak == 1,
              f"record_broadcast_failure(message=...) records {label} without raising")
        check(err2 is None, f"the ledger write survives {label}")

    # Degradation must be to UNKNOWN, which is HARD: an object whose every
    # representation is unreadable has still spent gas and must still be
    # charged, never quietly excused as a transport blip.
    unreadable, _ = safe_classify(RaisingStr())
    check(unreadable is KIND.UNKNOWN and KIND.UNKNOWN.is_hard,
          "an unreadable object degrades to UNKNOWN (hard), not to a benign kind")

    # Real TLS faults must still be recognised now that the bare "ssl" needle is
    # gone, or the fix in _SIGNATURES would have swapped one blind spot for
    # another. These are the shapes ssl / urllib3 / requests actually raise.
    for tls_text, label in [
        ("ssl.SSLError: [SSL: WRONG_VERSION_NUMBER]", "ssl.SSLError"),
        ("urllib3.exceptions.SSLError: HTTPSConnectionPool(host='rpc.example')",
         "urllib3 SSLError"),
        ("requests.exceptions.SSLError: ('The read operation timed out')",
         "requests SSLError"),
        ("SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] "
         "certificate verify failed: unable to get local issuer certificate')",
         "SSLCertVerificationError"),
        ("ssl.SSLEOFError: EOF occurred in violation of protocol", "SSLEOFError"),
    ]:
        got, _ = safe_classify(tls_text)
        check(got is KIND.NETWORK_TIMEOUT,
              f"a real TLS fault is still a network timeout: {label}")

    # And the reason the bare needle had to go: a hard fault whose message only
    # mentions a relay host called "...-ssl-..." must NOT be excused as benign,
    # because BENIGN means the breaker never opens for gas that was burned.
    for ssl_host, label in [
        ("MaxRetryError: my-ssl-proxy.example.com", "a bare ssl hostname"),
        ("execution reverted: FlashLoanFailed() via my-ssl-proxy.example.com",
         "a revert string embedding an ssl hostname"),
    ]:
        got, _ = safe_classify(ssl_host)
        check(got is not KIND.NETWORK_TIMEOUT,
              f"an 'ssl' hostname alone is not a network timeout: {label}")
    check(safe_classify("MaxRetryError: my-ssl-proxy.example.com")[0] is KIND.UNKNOWN,
          "an 'ssl' hostname leaves the failure UNKNOWN, so it still trips the breaker")

    # ── broadcast failure classification: partition is exhaustive and exact ─
    all_kinds = set(KIND)
    check(HARD_BROADCAST_FAILURES & BENIGN_BROADCAST_FAILURES == frozenset(),
          "HARD and BENIGN failure sets are disjoint")
    check(HARD_BROADCAST_FAILURES | BENIGN_BROADCAST_FAILURES == all_kinds,
          "every BroadcastFailureKind is in exactly one of HARD/BENIGN")
    check(KIND.UNKNOWN in HARD_BROADCAST_FAILURES,
          "UNKNOWN is HARD: an unrecognised failure is never silently free")
    check(KIND.INSUFFICIENT_FUNDS in HARD_BROADCAST_FAILURES
          and KIND.NONCE_TOO_LOW in HARD_BROADCAST_FAILURES
          and KIND.NONCE_TOO_HIGH in HARD_BROADCAST_FAILURES
          and KIND.GAS_TOO_LOW in HARD_BROADCAST_FAILURES
          and KIND.REPLACEMENT_UNDERPRICED in HARD_BROADCAST_FAILURES
          and KIND.RPC_REJECTION in HARD_BROADCAST_FAILURES
          and KIND.CONTRACT_REJECTION in HARD_BROADCAST_FAILURES,
          "systemic bot faults (funding, nonce, fees, contract) are all HARD")
    check(KIND.NETWORK_TIMEOUT in BENIGN_BROADCAST_FAILURES
          and KIND.ALREADY_KNOWN in BENIGN_BROADCAST_FAILURES,
          "transport blips and duplicate broadcasts are the only BENIGN kinds")
    check(all(not k.is_hard for k in BENIGN_BROADCAST_FAILURES)
          and all(k.is_hard for k in HARD_BROADCAST_FAILURES),
          "is_hard agrees with the HARD/BENIGN partition")
    check(str(KIND.INSUFFICIENT_FUNDS) == "insufficient_funds"
          and "{}".format(KIND.NONCE_TOO_LOW) == "nonce_too_low",
          "a failure kind stringifies to its stable slug")

    # ── record_broadcast_failure: hard kinds advance breaker and loss ───────
    clock = FakeClock()
    g = gov(clock=clock, max_consecutive_failures=3, max_daily_loss_usd=25.0)
    for hard_kind in sorted(HARD_BROADCAST_FAILURES, key=lambda k: k.value):
        reset = gov(clock=FakeClock(), max_consecutive_failures=3)
        d = reset.record_broadcast_failure(
            hard_kind, gas_usd=0.20, message="raw provider text")
        check(d.allowed, f"{hard_kind.value}: a single hard failure is allowed")
        check(reset.consecutive_failures() == 1,
              f"{hard_kind.value}: the consecutive-failure streak advances")
        check(abs(reset.daily_loss_usd() - 0.20) < 1e-9,
              f"{hard_kind.value}: its gas is charged to the daily loss")
        s = reset.status()
        check(s["today_failures"] == 1,
              f"{hard_kind.value}: the event is visible as a failure")

    # ── record_broadcast_failure: benign kinds advance nothing ──────────────
    for benign_kind in sorted(BENIGN_BROADCAST_FAILURES, key=lambda k: k.value):
        reset = gov(clock=FakeClock(), max_consecutive_failures=3)
        d = reset.record_broadcast_failure(
            benign_kind, gas_usd=9.99, message="transport blip")
        check(d.allowed and d.code == ALLOW,
              f"{benign_kind.value}: a benign failure always returns ALLOW")
        check(reset.consecutive_failures() == 0,
              f"{benign_kind.value}: the breaker streak is untouched")
        check(reset.daily_loss_usd() == 0.0,
              f"{benign_kind.value}: no loss is accrued for a transport blip")
        s = reset.status()
        check(s["today_transient"] == 1 and s["today_failures"] == 0,
              f"{benign_kind.value}: recorded as transient, not as a failure")

    # A long run of benign failures must never open the breaker.
    clock = FakeClock()
    g = gov(clock=clock, max_consecutive_failures=3)
    for _ in range(500):
        g.record_broadcast_failure(KIND.NETWORK_TIMEOUT)
    check(g.consecutive_failures() == 0 and g.daily_loss_usd() == 0.0,
          "500 network timeouts never open the breaker and cost nothing")
    check(g.status()["today_transient"] == 500,
          "500 network timeouts are still counted for the post-mortem")

    # A benign failure between hard ones must not reset the streak either.
    g = gov(clock=FakeClock(), max_consecutive_failures=3)
    g.record_broadcast_failure(KIND.INSUFFICIENT_FUNDS)
    g.record_broadcast_failure(KIND.ALREADY_KNOWN)
    g.record_broadcast_failure(KIND.NONCE_TOO_LOW)
    check(g.consecutive_failures() == 2,
          "benign failures do not reset a real hard-failure streak")

    # ── record_broadcast_failure: breaker opens and daily cap trips ─────────
    clock = FakeClock()
    g = gov(clock=clock, max_consecutive_failures=2, cooldown_base_s=60.0)
    g.record_broadcast_failure(KIND.INSUFFICIENT_FUNDS)
    tripped = g.record_broadcast_failure(KIND.REPLACEMENT_UNDERPRICED)
    check(not tripped.allowed and tripped.code == BLOCK_BREAKER,
          "two hard broadcast failures open the breaker like any two failures")
    check(tripped.retry_after_s == 60.0,
          "the breaker trip from a broadcast failure has a cooldown")

    clock = FakeClock()
    g = gov(clock=clock, max_daily_loss_usd=5.0, max_consecutive_failures=1_000)
    for _ in range(49):
        g.record_broadcast_failure(KIND.GAS_TOO_LOW, gas_usd=0.10)
    check(g.check(10_000.0, 5.0).allowed,
          "repeated hard broadcast failures under the cap still allow trading")
    g.record_broadcast_failure(KIND.GAS_TOO_LOW, gas_usd=0.10)
    d = g.check(10_000.0, 5.0)
    check(not d.allowed and d.code == BLOCK_DAILY_LOSS,
          "repeated hard broadcast failures can reach the daily cap and block")

    # ── record_broadcast_failure: kind coercion and detail audit trail ──────
    clock = FakeClock()
    g = gov(clock=clock, max_consecutive_failures=1_000)
    g.record_broadcast_failure("insufficient_funds", gas_usd=0.10,
                               message="wallet empty")
    d = g.record_broadcast_failure("nonce_too_low", gas_usd=0.10,
                                   message="stale nonce")
    check(d.allowed,
          "a kind may be passed as its slug string and is honoured")
    g.record_broadcast_failure(
        Web3Error("execution reverted: FlashLoanFailed()"), gas_usd=0.10)
    g.record_broadcast_failure("a totally novel raw error string", gas_usd=0.10)
    check(g.consecutive_failures() == 4,
          "an exception object or raw message is classified, then recorded")

    # The event rows must be greppable by kind for a post-mortem counting
    # failures per class, and the provider's own wording must survive.
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "audit.db")
        clock = FakeClock()
        g = RiskGovernor(db_path=db, clock=clock, max_consecutive_failures=1_000)
        g.record_broadcast_failure(
            KIND.CONTRACT_REJECTION, gas_usd=0.10,
            message="execution reverted: Ownable: caller is not the owner")
        g.record_broadcast_failure(KIND.NETWORK_TIMEOUT, gas_usd=0.0,
                                   message="read timed out")
        con = sqlite3.connect(db)
        rows = con.execute(
            "SELECT outcome, detail FROM risk_events ORDER BY id").fetchall()
        con.close()
        check(rows[0][0] == "failure"
              and "broadcast_failure:contract_rejection" in rows[0][1]
              and "Ownable: caller is not the owner" in rows[0][1],
              "a hard broadcast failure is an auditable failure row, kind-tagged")
        check(rows[1][0] == OUTCOME_TRANSIENT
              and "broadcast_failure:network_timeout" in rows[1][1]
              and "read timed out" in rows[1][1],
              "a benign broadcast failure is stored verbatim under OUTCOME_TRANSIENT")

    # ── OUTCOME_TRANSIENT is its own bucket in status(), never a loss ───────
    clock = FakeClock()
    g = gov(clock=clock, max_consecutive_failures=3)
    g.record_success(net_usd=12.0, gas_usd=0.20)
    g.record_failure(gas_usd=0.10)
    g.record_skip("simulation would revert")
    for _ in range(3):
        g.record_broadcast_failure(KIND.NETWORK_TIMEOUT, gas_usd=7.0)
    s = g.status()
    check(s["today_transient"] == 3,
          "OUTCOME_TRANSIENT is counted distinctly in status()")
    check(s["today_skipped"] == 1 and s["today_failures"] == 1,
          "transient, skipped and failed counts never bleed into each other")
    check(abs(s["daily_net_usd"] - 11.70) < 1e-6,
          "transient failures are never treated as a loss in the P&L")
    check(s["consecutive_failures"] == 1,
          "transient failures leave the breaker streak alone")
    check(abs(s["today_gas_usd"] - 0.30) < 1e-6,
          "uncommitted gas from benign failures is not charged to the day")
    check(g.check(10_000.0, 5.0).allowed,
          "a day of pure transient failures leaves trading open")

    # ── loss_cap_tolerance_usd scales with the ledger, not a fixed value ────
    t_10 = loss_cap_tolerance_usd(100.0, 1000.0, rows=10, gross_usd=1000.0)
    t_100 = loss_cap_tolerance_usd(100.0, 1000.0, rows=100, gross_usd=1000.0)
    check(t_100 == 10 * t_10,
          "tolerance scales linearly with the number of accumulated rows")
    t_small = loss_cap_tolerance_usd(5.0, 5.0, rows=50, gross_usd=5.0)
    t_large = loss_cap_tolerance_usd(1_000_000.0, 1_000_000.0,
                                     rows=5_760, gross_usd=1_000_000.0)
    check(t_large > 1e-9,
          "a large-cap day gets a tolerance far above the 1e-9 floor")
    check(t_large > t_small * 1e6,
          "tolerance scales with the magnitude of the money involved")
    check(loss_cap_tolerance_usd(0.0, 5.0, rows=0, gross_usd=0.0) == 0.0,
          "an empty day has zero tolerance")
    check(loss_cap_tolerance_usd(0.5, 0.0, rows=10, gross_usd=1.0) == 0.0,
          "a zero cap keeps an exact (not approximate) comparison")
    bound_ok = True
    for cap in (0.01, 5.0, 25.0, 1_000.0, 1e6, 1e9):
        for rows in (1, 50, 5_760, 100_000):
            t = loss_cap_tolerance_usd(cap * 0.9, cap, rows=rows, gross_usd=cap)
            if t > 1e-9 * abs(cap):
                bound_ok = False
    check(bound_ok,
          "the derived tolerance never exceeds its 1e-9-of-cap safety bound")

    # ── daily-loss cap: the B5 float regression is closed at two scales ─────
    # Non-round configured cap, where naive float accumulation sums $0.10 rows
    # to 4.899999999999999 — short of the cap — and the old bare `loss >= cap`
    # comparison would have let the trading continue past it.
    clock = FakeClock()
    g = gov(clock=clock, max_daily_loss_usd=4.90, max_consecutive_failures=1_000)
    for _ in range(49):
        g.record_failure(gas_usd=0.10)
    check(abs(g.daily_loss_usd() - 4.90) < 1e-6,
          "49 x $0.10 accumulates to the non-round cap 4.90")
    d = g.check(10_000.0, 5.0)
    check(not d.allowed and d.code == BLOCK_DAILY_LOSS,
          "a non-round cap blocks even when the float sum is a few ulp short")
    check(g.daily_loss_usd() < 4.90,
          "the ledger sum really is short of 4.90 (the regression is real)")

    # Large configured cap: the shortfall is larger than any fixed 1e-9
    # tolerance could ever see, so only a derived tolerance (which scales with
    # the row count and the gross magnitude) can close the same hole.
    clock = FakeClock()
    per_row = 806.4516129032259  # non-representable, so it accumulates with error
    g = gov(clock=clock, max_daily_loss_usd=250_000.0,
            max_consecutive_failures=1_000)
    for _ in range(310):
        g.record_failure(gas_usd=per_row)
    loss = g.daily_loss_usd()
    # Assert the property, not a pasted constant. SQLite's summation order and
    # any platform's libm are free to change the last ulp of this float, and a
    # test that hardcodes 1.7462298274040222e-09 would then fail on a correct
    # implementation. The property that matters is the one the next two checks
    # depend on: the shortfall is comfortably above 1e-9 (so no fixed tolerance
    # could have caught it) and comfortably below 1e-6 (so the derived one does).
    check(1e-9 < (250_000.0 - loss) < 1e-6,
          "a large float sum lands short of its cap by more than 1e-9")
    d = g.check(10_000.0, 5.0)
    check(not d.allowed and d.code == BLOCK_DAILY_LOSS,
          "the large-cap case is blocked by the derived tolerance")
    check(not (loss >= 250_000.0 - 1e-9),
          "a fixed 1e-9 tolerance would have let that trade through (proven)")

    # ── the harness must not silence the process ────────────────────────────
    # This file silences risk_limits' own logger so the Results stay clean. It
    # must not do so with logging.disable(), which sets the process-global
    # root.manager.disable threshold: after importing this harness, an unrelated
    # logger anywhere in the process would be unable to emit even CRITICAL.
    # These checks exist so the mistake cannot come back unnoticed.
    check(logging.getLogger().manager.disable < logging.CRITICAL,
          "importing the harness leaves the process-global logging gate open")
    unrelated = logging.getLogger("jdl_flash.test_risk_limits.unrelated")
    captured = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    _handler = _Capture()
    unrelated.addHandler(_handler)
    try:
        unrelated.critical("still audible")
    finally:
        unrelated.removeHandler(_handler)
    check(captured == ["still audible"],
          "an unrelated logger can still emit CRITICAL after importing the harness")
    check(logging.getLogger("jdl_flash.risk_limits").level > logging.CRITICAL,
          "the suppression is scoped to the module under test, and still in force")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
