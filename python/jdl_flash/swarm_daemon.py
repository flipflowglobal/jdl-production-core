"""
swarm_daemon.py — headless entrypoint for constant, unattended parallel opportunity
scanning.

menu_swarm() (in flash_loan_engine.py) is interactive: it prompts for a fixed number
of scan rounds via input() and returns once they're done. That's fine at a terminal,
but there was no way to point a process supervisor or a Termux:Boot hook at the swarm
scanner and have it just run — this module is that entrypoint. It wraps the same
SwarmCoordinator/BotSwarm machinery (swarm_runtime.py) in an outer loop that runs
batches back-to-back until SIGINT/SIGTERM, logging per-batch stats instead of prompting.

Usage:
    python3 -m jdl_flash.swarm_daemon

Config (env vars; all already-established elsewhere in the engine, reused as-is):
    RPC_URL / RPC_URL_1.. , SWARM_WORKERS, SWARM_KEYS, SWARM_CONTRACTS,
    LIVE_EXECUTION, REAL_LOAN_USD  — see flash_loan_engine.py / swarm_runtime.py.
    SWARM_BATCH_ROUNDS (default 30) — scan rounds per batch before stats are logged
        and the dedup set is reset. Kept modest by default (rather than "infinite")
        so a SIGTERM is honored within roughly BATCH_ROUNDS * SWARM_INTERVAL seconds
        instead of only between arbitrarily long runs — matters on Android, where the
        OS can send SIGTERM on a tight budget when reclaiming memory.
    SWARM_INTERVAL (default 1.0) — target seconds between the start of successive
        scan rounds within a batch.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

log = logging.getLogger("SwarmDaemon")

_shutdown_requested = False


def _request_shutdown(signum: int, _frame: object) -> None:
    global _shutdown_requested
    log.info(f"signal {signum} received — stopping after the current batch")
    _shutdown_requested = True


async def run_forever(engine_module=None, coordinator_factory=None) -> int:
    """Run swarm batches back-to-back until a shutdown signal arrives.

    engine_module / coordinator_factory are injectable for tests; production use
    (main(), below) leaves both None and imports the real engine + swarm_runtime.
    """
    if engine_module is None:
        from jdl_flash import flash_loan_engine as engine_module  # noqa: PLC0415
    from jdl_flash import swarm_runtime as sr  # noqa: PLC0415

    batch_rounds = max(1, int(os.getenv("SWARM_BATCH_ROUNDS", "30")))
    interval = max(0.0, float(os.getenv("SWARM_INTERVAL", "1.0")))

    feed = engine_module.PriceFeed()
    if coordinator_factory is not None:
        coord = coordinator_factory(feed)
    else:
        coord = engine_module.build_live_coordinator(
            feed, engine_module.REAL_LOAN_USD, execute=engine_module.LIVE_EXEC
        )
    if coord is None:
        log.error("live quoter not ready (check RPC/web3) — cannot start swarm daemon")
        return 1

    workers = sr.resolve_workers()
    live = bool(getattr(engine_module, "LIVE_EXEC", False))
    log.info(
        f"swarm daemon starting: workers={workers} execute={'LIVE' if live else 'DRY'} "
        f"batch_rounds={batch_rounds} interval={interval}s"
    )
    swarm = coord.make_swarm(workers)

    batch = 0
    while not _shutdown_requested:
        batch += 1
        coord.reset_dedup()
        await swarm.run(rounds=batch_rounds, interval=interval)
        stats = swarm.stats()
        total_scans = sum(s["scans"] for s in stats.values())
        total_found = sum(s["found"] for s in stats.values())
        total_exec = sum(s["executed"] for s in stats.values())
        total_errors = sum(len(s["errors"]) for s in stats.values())
        log.info(
            f"batch {batch}: scans={total_scans} found={total_found} "
            f"executed={total_exec} errors={total_errors}"
        )
    log.info("swarm daemon stopped")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)
    return asyncio.run(run_forever())


if __name__ == "__main__":
    sys.exit(main())
