"""
swarm_runtime.py — wire the BotSwarm into the live engine for maximum-parallel
scanning and nonce-safe parallel execution.

What it does
------------
- Enumerates the full arbitrage route universe (token pairs × Uniswap V3 fee tiers)
  once, then hands each worker a DISJOINT slice (index % n_workers == worker_id) so
  N workers scan N× the market per tick with zero overlap.
- Each worker turns its slice's real quotes into hot-path edges and calls the Rust
  engine (jdl_native) to pick the best cycle fast.
- A shared, thread-safe dedup set keys opportunities by route so two workers never
  submit the SAME arb (which would just make one revert and waste gas).
- Execution runs on per-worker nonce lanes (BotSwarm), optionally on distinct
  wallets for genuinely parallel on-chain execution.

Honest limits (see resolve_workers / docs)
------------------------------------------
- Scanning parallelism is real and safe, bounded by CPU cores and RPC rate limits
  (the engine already fails over across its RPC pool).
- Execution from a SINGLE wallet is serialized on-chain by nonce order — nonce lanes
  allow parallel *submission* but the sequencer mines them in order. For genuinely
  parallel execution, supply multiple keys via SWARM_KEYS (one lane per wallet).
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from jdl_flash.bot_swarm import BotSwarm


# ── config ───────────────────────────────────────────────────────────────────
def resolve_workers(spec: Optional[str] = None, cpu: Optional[int] = None) -> int:
    """Resolve SWARM_WORKERS: an int, or 'auto'/'max'.

    'auto'  → number of CPU cores (the sweet spot: parallel without thrashing).
    'max'   → 4× cores, capped at 32 (I/O-bound quote fetching tolerates oversubscription,
              but past this you just hit RPC rate limits — see docs).
    """
    spec = (spec if spec is not None else os.getenv("SWARM_WORKERS", "auto")).strip().lower()
    cores = cpu if cpu is not None else (os.cpu_count() or 4)
    if spec in ("auto", "", "0"):
        return max(1, cores)
    if spec == "max":
        return max(1, min(cores * 4, 32))
    try:
        return max(1, int(spec))
    except ValueError:
        return max(1, cores)


# ── route universe ───────────────────────────────────────────────────────────
def build_route_universe(
    tokens: List[str],
    fee_tiers: Tuple[int, ...] = (500, 3000, 10000),
    base: str = "USDC",
) -> List[Dict[str, Any]]:
    """Every 2-leg round trip base → mid → base across the given fee tiers.

    Each route is a self-contained scan task. Kept flat (not nested) so slicing by
    `index % n_workers` gives perfectly balanced, disjoint partitions.
    """
    routes: List[Dict[str, Any]] = []
    for mid in tokens:
        if mid == base:
            continue
        for buy_fee in fee_tiers:
            for sell_fee in fee_tiers:
                routes.append({"base": base, "mid": mid, "buy_fee": buy_fee, "sell_fee": sell_fee})
    return routes


def partition(routes: List[Any], worker_id: int, n_workers: int) -> List[Any]:
    """Disjoint slice for a worker: exactly the routes whose index mod n == worker_id."""
    return [r for i, r in enumerate(routes) if i % n_workers == worker_id]


def route_key(route: Dict[str, Any]) -> str:
    return f"{route['base']}>{route['mid']}:{route['buy_fee']}/{route['sell_fee']}"


# ── coordinator ──────────────────────────────────────────────────────────────
class SwarmCoordinator:
    """Builds the partition-aware scan/exec callables the BotSwarm drives.

    Dependency-injected so it unit-tests without a live chain:
      quote_edges_fn(route)  -> list[edge dict] | None   (real quotes → hot-path edges)
      best_cycle_fn(request) -> ScanResult dict          (Rust hot-path; default jdl_native)
      execute_fn(opp, nonce, lane) -> tx hash | None     (broadcast; None = no-op/dry)
    """

    def __init__(
        self,
        quote_edges_fn: Callable[[Dict[str, Any]], Optional[List[dict]]],
        execute_fn: Optional[Callable[[dict, int, int], Any]] = None,
        best_cycle_fn: Optional[Callable[[dict], dict]] = None,
        tokens: Optional[List[str]] = None,
        base: str = "USDC",
        loan_usd: float = 10_000.0,
        gas_usd: float = 0.5,
        min_profit_usd: float = 0.0,
        n_wallets: int = 1,
    ) -> None:
        self.quote_edges_fn = quote_edges_fn
        self.execute_fn = execute_fn
        self.base = base
        self.loan_usd = loan_usd
        self.gas_usd = gas_usd
        self.min_profit_usd = min_profit_usd
        self.n_wallets = max(1, n_wallets)
        self._tokens = tokens or ["USDC", "WETH", "WBTC", "DAI", "USDT", "ARB"]
        self.routes = build_route_universe(self._tokens, base=base)
        # Global dedup so workers never submit the same route in the same tick window.
        self._seen: set = set()
        self._seen_lock = threading.Lock()
        # Best fee tier seen per directed leg, so a winning path can be executed with
        # the exact Uniswap fee for each hop. Keyed "from>to" -> (best_rate, fee_tier).
        self._leg_fee: Dict[str, Tuple[float, int]] = {}
        self._leg_lock = threading.Lock()

        if best_cycle_fn is not None:
            self._best_cycle = best_cycle_fn
        else:
            self._best_cycle = self._default_best_cycle

    @staticmethod
    def _default_best_cycle(request: dict) -> dict:
        # Lazy import so the module loads even where jdl_native isn't built.
        import jdl_native
        return jdl_native.scan(request)

    def _fresh(self, key: str) -> bool:
        with self._seen_lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            return True

    def reset_dedup(self) -> None:
        """Clear per-tick state (dedup set + recorded leg fees). Call between ticks."""
        with self._seen_lock:
            self._seen.clear()
        with self._leg_lock:
            self._leg_fee.clear()

    # scan_fn(partition_index, n_workers) — the BotSwarm calls this per worker/tick.
    def _record_leg_fee(self, frm: str, to: str, rate: float, fee_tier: Optional[int]) -> None:
        if fee_tier is None:
            return
        k = f"{frm}>{to}"
        with self._leg_lock:
            cur = self._leg_fee.get(k)
            if cur is None or rate > cur[0]:
                self._leg_fee[k] = (rate, fee_tier)

    def _fees_for_path(self, path: List[str]) -> List[Optional[int]]:
        fees = []
        with self._leg_lock:
            for i in range(len(path) - 1):
                entry = self._leg_fee.get(f"{path[i]}>{path[i + 1]}")
                fees.append(entry[1] if entry else None)
        return fees

    def scan_fn(self, worker_id: int, n_workers: int) -> List[dict]:
        my_routes = partition(self.routes, worker_id, n_workers)
        edges: List[dict] = []
        for route in my_routes:
            e = self.quote_edges_fn(route)
            if e:
                for edge in e:
                    # Record the fee tier behind each directed leg for execution.
                    self._record_leg_fee(edge["from"], edge["to"], edge.get("rate", 0.0),
                                         edge.get("fee_tier"))
                    edges.append(edge)
        if not edges:
            return []
        result = self._best_cycle({
            "edges": edges,
            "base": self.base,
            "loan_usd": self.loan_usd,
            "gas_usd": self.gas_usd,
            "min_profit_usd": self.min_profit_usd,
        })
        opp = result.get("opportunity")
        if not opp:
            return []
        # Dedup on the concrete path so two workers finding the same loop only act once.
        path_key = ">".join(opp.get("path", []))
        if not self._fresh(path_key):
            return []
        opp["_worker"] = worker_id
        # Attach the executable fee tiers per hop (None where unknown).
        opp["fees"] = self._fees_for_path(opp.get("path", []))
        return [opp]

    # exec_fn(opportunity, nonce) — serialized per nonce lane by BotSwarm.
    def exec_fn(self, opportunity: dict, nonce: int) -> Any:
        if self.execute_fn is None:
            return None
        # Route the lane to a wallet when multiple are configured.
        lane = opportunity.get("_worker", 0) % self.n_wallets
        return self.execute_fn(opportunity, nonce, lane)

    def make_swarm(self, n_workers: int, nonce_base: int = 0) -> BotSwarm:
        return BotSwarm(
            n_workers=n_workers,
            scan_fn=self.scan_fn,
            exec_fn=self.exec_fn if self.execute_fn else None,
            nonce_base=nonce_base,
        )
