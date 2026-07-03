"""
bot_swarm.py - Asyncio parallel flash-loan arbitrage bot orchestrator.

Partitioning scheme:
    Worker i (0-indexed) scans opportunity buckets where bucket_index % n_workers == i.
    The scan_fn receives (partition_index=i, n_workers=n) to filter its universe to that
    disjoint slice (e.g. only token-pair hashes whose index mod n equals i).
    This guarantees zero overlap: every bucket belongs to exactly one worker.

Nonce lane assignment:
    Worker i uses nonces: nonce_base + i, nonce_base + i + n_workers, nonce_base + i + 2*n_workers, ...
    (stride = n_workers).  Because the executor is serialised per worker lane, nonces
    within a lane are consumed strictly in order, and different lanes never share a nonce.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

__all__ = ["BotSwarm"]


class WorkerStats:
    """Per-worker mutable counters."""

    __slots__ = ("scans", "found", "executed", "errors")

    def __init__(self) -> None:
        self.scans: int = 0
        self.found: int = 0
        self.executed: int = 0
        self.errors: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scans": self.scans,
            "found": self.found,
            "executed": self.executed,
            "errors": list(self.errors),
        }


class BotSwarm:
    """
    Parallel flash-loan arbitrage orchestrator.

    Parameters
    ----------
    n_workers : int
        Number of concurrent scanning workers (>= 1).
    scan_fn : callable
        Async or sync callable with signature scan_fn(partition_index, n_workers) ->
        iterable-of-opportunities (or None/empty on no finds).
    exec_fn : callable, optional
        Async or sync callable with signature exec_fn(opportunity, nonce) -> Any.
        Serialised per-worker nonce lane to prevent double-spend.
    nonce_base : int
        Starting nonce offset; worker i uses nonce_base + i + k*n_workers for k=0,1,2,...
    """

    def __init__(
        self,
        n_workers: int,
        scan_fn: Callable,
        exec_fn: Optional[Callable] = None,
        nonce_base: int = 0,
    ) -> None:
        if n_workers < 1:
            raise ValueError("n_workers must be >= 1")
        self._n = n_workers
        self._scan_fn = scan_fn
        self._exec_fn = exec_fn
        self._nonce_base = nonce_base
        self._stats: List[WorkerStats] = [WorkerStats() for _ in range(n_workers)]
        # Each worker has its own queue so execution is serialised per nonce lane.
        self._queues: List[asyncio.Queue] = [asyncio.Queue() for _ in range(n_workers)]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call(self, fn: Callable, *args: Any) -> Any:
        """Await fn if it is a coroutine function; otherwise run it in a worker
        thread via asyncio.to_thread.

        This matters: scan_fn/exec_fn in this codebase are typically SYNCHRONOUS
        functions wrapping blocking network I/O (web3.py's HTTPProvider, requests).
        Calling a blocking sync function directly inside a coroutine (`return
        fn(*args)`) has NO await point, so it runs to completion before the event
        loop can schedule any other worker's task — N "concurrent" workers would
        then execute strictly one-at-a-time, each paying full RPC round-trip
        latency, giving ZERO real parallelism (just async bookkeeping overhead).
        Routing through a thread lets the GIL release during actual socket I/O, so
        multiple workers' blocking RPC calls genuinely overlap in wall-clock time.
        """
        if inspect.iscoroutinefunction(fn):
            return await fn(*args)
        return await asyncio.to_thread(fn, *args)

    async def _worker(self, worker_id: int, rounds: int, interval: float) -> None:
        """Scan loop for a single worker."""
        stats = self._stats[worker_id]
        queue = self._queues[worker_id]
        for _ in range(rounds):
            t0 = time.monotonic()
            try:
                result = await self._call(self._scan_fn, worker_id, self._n)
                stats.scans += 1
                opps = result if result else []
                for opp in opps:
                    stats.found += 1
                    await queue.put(opp)
            except Exception as exc:  # noqa: BLE001
                stats.errors.append(f"scan error: {exc!r}")
            elapsed = time.monotonic() - t0
            sleep_for = max(0.0, interval - elapsed)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        # Signal executor that this worker is done.
        await queue.put(None)

    async def _executor(self, worker_id: int) -> None:
        """
        Drain the per-worker queue and call exec_fn.

        Serialised within the nonce lane: nonces are handed out strictly in order
        nonce_base + worker_id, then + n_workers, then + 2*n_workers, etc.
        """
        if self._exec_fn is None:
            # Still drain the queue so it doesn't block workers.
            while True:
                item = await self._queues[worker_id].get()
                if item is None:
                    break
            return

        stats = self._stats[worker_id]
        nonce = self._nonce_base + worker_id
        stride = self._n
        while True:
            item = await self._queues[worker_id].get()
            if item is None:
                break
            try:
                await self._call(self._exec_fn, item, nonce)
                stats.executed += 1
            except Exception as exc:  # noqa: BLE001
                stats.errors.append(f"exec error (nonce={nonce}): {exc!r}")
            finally:
                nonce += stride

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, rounds: int = 1, interval: float = 1.0) -> None:
        """
        Run the swarm for *rounds* scan cycles per worker.

        Workers scan concurrently; each worker's executor coroutine drains
        its own queue serially (preserving nonce order).  One worker crashing
        does not affect the others — errors are captured in stats().

        Parameters
        ----------
        rounds : int
            How many scan rounds each worker performs.
        interval : float
            Target seconds between the start of successive scan rounds.
        """
        if rounds < 1:
            return

        tasks: List[asyncio.Task] = []
        for i in range(self._n):
            tasks.append(asyncio.ensure_future(self._worker(i, rounds, interval)))
            tasks.append(asyncio.ensure_future(self._executor(i)))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Capture any unexpected task-level exceptions into worker stats.
        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                worker_id = idx // 2
                self._stats[worker_id].errors.append(f"task error: {res!r}")

    def stats(self) -> Dict[str, Any]:
        """
        Return per-worker statistics.

        Returns
        -------
        dict
            Keys are worker indices (int); values are dicts with keys:
            scans, found, executed, errors.
        """
        try:
            return {i: self._stats[i].to_dict() for i in range(self._n)}
        except Exception:  # noqa: BLE001
            return {}
