"""
Tests for bot_swarm.py — BotSwarm orchestration, including the concurrency fix
in _call() (sync blocking functions must run via asyncio.to_thread, not inline).

Run: cd python && python3 jdl_flash/test_bot_swarm.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdl_flash.bot_swarm import BotSwarm


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  ✓ {msg}")
        else:
            failed += 1; print(f"  ✗ {msg}")

    # ── THE core fix: sync blocking scan_fn must genuinely overlap across workers ──
    DELAY = 0.15
    N_WORKERS = 6

    def blocking_scan(worker_id, n_workers):
        time.sleep(DELAY)  # simulates a blocking RPC round-trip
        return []

    async def time_it():
        swarm = BotSwarm(n_workers=N_WORKERS, scan_fn=blocking_scan)
        t0 = time.monotonic()
        await swarm.run(rounds=1, interval=0.0)
        return time.monotonic() - t0

    elapsed = asyncio.run(time_it())
    serial_time = N_WORKERS * DELAY
    # If truly parallel, elapsed should be close to one DELAY (plus scheduling
    # overhead), NOT anywhere near N_WORKERS * DELAY (which would mean the old bug
    # — sync calls blocking the event loop — is back).
    check(elapsed < serial_time * 0.6,
          f"{N_WORKERS} blocking workers overlap in wall time: "
          f"{elapsed:.3f}s (serial would be {serial_time:.3f}s)")

    # ── correctness: results still flow through and stats are recorded ──
    def scan_with_result(worker_id, n_workers):
        return [{"worker": worker_id}]

    executed = []
    def record_exec(opp, nonce):
        executed.append((opp["worker"], nonce))

    swarm2 = BotSwarm(n_workers=3, scan_fn=scan_with_result, exec_fn=record_exec, nonce_base=100)
    asyncio.run(swarm2.run(rounds=2, interval=0.0))
    stats = swarm2.stats()
    check(sum(s["scans"] for s in stats.values()) == 6, "3 workers x 2 rounds = 6 scans")
    check(sum(s["found"] for s in stats.values()) == 6, "each scan found 1 opportunity")
    check(sum(s["executed"] for s in stats.values()) == 6, "all 6 opportunities executed")

    # nonce lanes: worker i uses nonce_base + i, then + n_workers, strictly increasing
    # per worker, never colliding across workers.
    nonces_by_worker = {}
    for w, n in executed:
        nonces_by_worker.setdefault(w, []).append(n)
    check(all(len(set(v)) == len(v) for v in nonces_by_worker.values()),
          "no duplicate nonces within a worker's lane")
    all_nonces = [n for _, n in executed]
    check(len(set(all_nonces)) == len(all_nonces), "no nonce collisions across ANY worker/lane")
    for w, ns in nonces_by_worker.items():
        expected = [100 + w + k * 3 for k in range(len(ns))]
        check(sorted(ns) == expected, f"worker {w} nonce sequence matches base+worker+k*n: {ns}")

    # ── async scan_fn / exec_fn still work (not everything needs to be sync) ──
    async def async_scan(worker_id, n_workers):
        await asyncio.sleep(0.001)
        return [{"worker": worker_id}]

    async_executed = []
    async def async_exec(opp, nonce):
        await asyncio.sleep(0.001)
        async_executed.append((opp["worker"], nonce))

    swarm3 = BotSwarm(n_workers=2, scan_fn=async_scan, exec_fn=async_exec)
    asyncio.run(swarm3.run(rounds=1, interval=0.0))
    check(len(async_executed) == 2, "async scan_fn/exec_fn still work correctly")

    # ── errors in one worker don't take down others ──
    def flaky_scan(worker_id, n_workers):
        if worker_id == 1:
            raise RuntimeError("simulated RPC failure")
        return []

    swarm4 = BotSwarm(n_workers=3, scan_fn=flaky_scan)
    asyncio.run(swarm4.run(rounds=2, interval=0.0))
    stats4 = swarm4.stats()
    check(len(stats4[1]["errors"]) == 2, "worker 1's errors captured (2 rounds)")
    check(stats4[0]["scans"] == 2 and stats4[2]["scans"] == 2,
          "workers 0 and 2 unaffected by worker 1's failures")

    # ── n_workers validation ──
    try:
        BotSwarm(n_workers=0, scan_fn=lambda w, n: [])
        check(False, "n_workers=0 should raise")
    except ValueError:
        check(True, "n_workers=0 raises ValueError")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
