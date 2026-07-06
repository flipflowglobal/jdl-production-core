"""
Tests for swarm_daemon.py, the headless always-on scanning entrypoint.

Fully hermetic: no RPC/network, and no real asyncio.run(forever) — run_forever()
takes injectable engine_module/coordinator_factory so a fake coordinator with a
tiny, deterministic route universe stands in for the live one, and the module-level
shutdown flag is set after a couple of batches to bound the loop instead of running
until a real signal arrives.

Run: cd python && python3 jdl_flash/test_swarm_daemon.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jdl_flash.swarm_daemon as d


class FakeEngineModule:
    """Stands in for flash_loan_engine: PriceFeed()/REAL_LOAN_USD/LIVE_EXEC/
    build_live_coordinator are the only attributes swarm_daemon touches."""

    LIVE_EXEC = False
    REAL_LOAN_USD = 10_000.0

    class PriceFeed:
        def __init__(self):
            pass

    @staticmethod
    def build_live_coordinator(feed, loan_usd, execute):
        return None  # overridden per-test via coordinator_factory


class FakeSwarm:
    def __init__(self, on_run):
        self._on_run = on_run
        self.run_calls = 0

    async def run(self, rounds, interval):
        self.run_calls += 1
        self._on_run(rounds, interval)

    def stats(self):
        return {0: {"scans": 3, "found": 1, "executed": 1, "errors": []}}


class FakeCoordinator:
    def __init__(self, on_run):
        self.reset_calls = 0
        self._swarm = FakeSwarm(on_run)

    def reset_dedup(self):
        self.reset_calls += 1

    def make_swarm(self, n_workers):
        return self._swarm


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✓ {msg}")
        else:
            failed += 1
            print(f"  ✗ {msg}")

    # ── 1) unavailable coordinator -> clean error return, no crash ──
    d._shutdown_requested = False
    rc = asyncio.run(d.run_forever(engine_module=FakeEngineModule, coordinator_factory=lambda feed: None))
    check(rc == 1, "run_forever returns 1 when the coordinator can't be built (no live quoter)")

    # ── 2) runs batches back-to-back until shutdown is requested ──
    d._shutdown_requested = False
    batch_calls = []

    def on_run(rounds, interval):
        batch_calls.append((rounds, interval))
        if len(batch_calls) >= 3:
            d._shutdown_requested = True  # simulate SIGTERM landing mid-run

    fake_coord = FakeCoordinator(on_run)
    os.environ["SWARM_BATCH_ROUNDS"] = "5"
    os.environ["SWARM_INTERVAL"] = "0.0"
    rc = asyncio.run(
        d.run_forever(engine_module=FakeEngineModule, coordinator_factory=lambda feed: fake_coord)
    )
    check(rc == 0, "run_forever returns 0 on a clean shutdown")
    check(len(batch_calls) == 3, f"stopped after the batch that requested shutdown (got {len(batch_calls)})")
    check(all(r == 5 and i == 0.0 for r, i in batch_calls), "SWARM_BATCH_ROUNDS/SWARM_INTERVAL env vars are honored")
    check(fake_coord.reset_calls == 3, "dedup is reset once per batch")
    check(fake_coord._swarm.run_calls == 3, "swarm.run() called once per batch")

    # ── 3) default batch size/interval apply when the env vars are unset ──
    del os.environ["SWARM_BATCH_ROUNDS"]
    del os.environ["SWARM_INTERVAL"]
    d._shutdown_requested = False
    batch_calls2 = []

    def on_run2(rounds, interval):
        batch_calls2.append((rounds, interval))
        d._shutdown_requested = True

    fake_coord2 = FakeCoordinator(on_run2)
    asyncio.run(d.run_forever(engine_module=FakeEngineModule, coordinator_factory=lambda feed: fake_coord2))
    check(batch_calls2 == [(30, 1.0)], f"defaults are batch_rounds=30, interval=1.0s (got {batch_calls2})")

    # ── 4) signal handler flips the module-level shutdown flag ──
    d._shutdown_requested = False
    d._request_shutdown(15, None)  # SIGTERM's numeric value; frame arg is unused
    check(d._shutdown_requested is True, "_request_shutdown sets the shutdown flag")
    d._shutdown_requested = False  # reset for any test run after this one

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
