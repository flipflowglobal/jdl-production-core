"""
NEXUS-ARB Route Finder
Bellman-Ford negative-cycle detection on a log-price directed graph.

Mathematical basis:
  Arbitrage exists iff there is a negative cycle in the graph where
  edge weight w(u,v) = -log(price(u→v)).

  A cycle u→v→w→u is profitable iff:
    price(u→v) × price(v→w) × price(w→u) > 1 + fees
  ⟺ log(p_uv) + log(p_vw) + log(p_wu) > 0
  ⟺ -w_uv - w_vw - w_wu < 0  (negative cycle)

Complexity: O(V × E) per scan — acceptable for |V| ≤ 20, |E| ≤ 200.
"""
import math
import logging
import itertools
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Optional

try:
    from core.config import (
        ACTIVE_TOKENS, MAX_ROUTES_PER_SCAN, ROUTE_TIMEOUT_SEC, MIN_PROFIT_USD
    )
    from core.multicall import MulticallEngine, PoolSnapshot
    from core.web3_manager import Web3Manager
except ImportError:
    # Stub definitions so the module can be loaded without the core package.
    # Replace these with real implementations when deploying.
    logger.warning("core package not found — using stubs in route_finder")
    ACTIVE_TOKENS: dict[str, str] = {
        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "DAI":  "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    }
    MAX_ROUTES_PER_SCAN = 10
    ROUTE_TIMEOUT_SEC   = 10
    MIN_PROFIT_USD      = 1.0

    from dataclasses import dataclass

    class MulticallEngine:
        def __init__(self, *args, **kwargs):
            pass
        def fetch_pool_snapshots(self, pools):
            return []

    @dataclass
    class PoolSnapshot:
        address: str = ""
        token0: str = ""
        token1: str = ""
        price_token1_per_token0: float = 0.0
        fee: int = 3000
        liquidity: int = 0
        has_liquidity: bool = False

    class Web3Manager:
        @staticmethod
        def get():
            return None

logger = logging.getLogger(__name__)

_AAVE_FLASH_FEE_BPS = 9    # 0.09% Aave V3 flash loan fee
_UNISWAP_FEE_BPS    = 30   # default 0.30% pool fee (3000 tier)
_GAS_COST_USD_EST   = 2.0  # conservative gas cost estimate in USD
_WETH_USD_PRICE     = 3000.0  # approximate — replace with live oracle in prod


@dataclass
class ArbitrageRoute:
    path: list[str]            # token symbols: ["WETH", "USDC", "DAI", "WETH"]
    pool_addresses: list[str]  # pool address per hop
    gross_profit_usd: float
    net_profit_usd: float
    profit_bps: float          # basis points above break-even
    loan_amount_eth: float
    confidence: float = 0.0    # set by AI composite brain
    route_id: str = ""

    def __post_init__(self):
        if not self.route_id:
            self.route_id = "_".join(self.path)


@dataclass
class PriceGraph:
    """
    Directed graph: node = token address, edge = log-price weight.
    Edge (u, v) represents swapping token u for token v via a pool.
    """
    nodes: list[str] = field(default_factory=list)
    # edges[u][v] = (log_weight, pool_address, fee_bps)
    edges: dict[str, dict[str, tuple[float, str, int]]] = field(
        default_factory=dict
    )

    def add_node(self, token: str) -> None:
        if token not in self.nodes:
            self.nodes.append(token)
            self.edges[token] = {}

    def add_edge(self, u: str, v: str, price: float, pool: str, fee_bps: int) -> None:
        if price <= 0:
            return
        net_price = price * (1 - fee_bps / 10_000)
        log_weight = -math.log(net_price)   # negative so profit = negative cycle
        self.edges[u][v] = (log_weight, pool, fee_bps)

    def bellman_ford_negative_cycles(self, source: str) -> list[list[str]]:
        """
        Run Bellman-Ford from source. Detect negative cycles by checking
        if relaxation is still possible after |V|-1 iterations.
        Returns list of cycle paths (token sequences).
        """
        n = len(self.nodes)
        if n == 0:
            return []

        INF = float("inf")
        dist: dict[str, float] = {v: INF for v in self.nodes}
        pred: dict[str, Optional[str]] = {v: None for v in self.nodes}
        dist[source] = 0.0

        # Relax all edges |V|-1 times
        for _ in range(n - 1):
            updated = False
            for u in self.nodes:
                if dist[u] == INF:
                    continue
                for v, (w, _, _) in self.edges[u].items():
                    if dist[u] + w < dist[v] - 1e-10:
                        dist[v] = dist[u] + w
                        pred[v] = u
                        updated = True
            if not updated:
                break   # Early termination if stable

        # Detect negative cycles: any further relaxation reveals one
        cycles = []
        in_cycle: set[str] = set()
        for u in self.nodes:
            if dist[u] == INF:
                continue
            for v, (w, _, _) in self.edges[u].items():
                if dist[u] + w < dist[v] - 1e-10 and v not in in_cycle:
                    cycle = self._trace_cycle(pred, v)
                    if cycle:
                        in_cycle.update(cycle)
                        cycles.append(cycle)

        return cycles

    def _trace_cycle(self, pred: dict, start: str) -> list[str]:
        """Trace predecessor chain to extract the cycle path."""
        visited = {}
        node = start
        for i in range(len(self.nodes) + 1):
            if node in visited:
                # Reconstruct cycle from this node
                cycle = []
                cur = node
                while True:
                    cycle.append(cur)
                    cur = pred.get(cur)
                    if cur == node or cur is None:
                        break
                cycle.append(node)
                cycle.reverse()
                return cycle
            visited[node] = i
            node = pred.get(node)
            if node is None:
                break
        return []

    def edge_pool(self, u: str, v: str) -> tuple[str, int]:
        """Returns (pool_address, fee_bps) for edge u→v."""
        if v in self.edges.get(u, {}):
            _, pool, fee = self.edges[u][v]
            return pool, fee
        return "", 0


class RouteScanner:
    """
    Scans liquidity pools via Multicall3, builds a price graph,
    and detects arbitrage cycles via Bellman-Ford.
    """

    def __init__(
        self,
        pool_addresses: list[str],
        manager: Optional[Web3Manager] = None,
    ):
        self._pools = pool_addresses
        self._mgr   = manager or Web3Manager.get()
        self._mc    = MulticallEngine(self._mgr)
        # Reverse-lookup: address → symbol
        self._addr_to_sym: dict[str, str] = {
            v.lower(): k for k, v in ACTIVE_TOKENS.items()
        }

    def scan(self) -> list[ArbitrageRoute]:
        """
        Main entry: fetch pool snapshots → build graph → find cycles → score.
        Returns routes sorted by net_profit_usd descending.
        """
        snapshots = self._mc.fetch_pool_snapshots(self._pools)
        liquid = [s for s in snapshots if s.has_liquidity]
        logger.info("%d/%d pools have liquidity", len(liquid), len(snapshots))

        if not liquid:
            return []

        graph = self._build_graph(liquid)
        routes = self._find_routes(graph, liquid)
        routes.sort(key=lambda r: r.net_profit_usd, reverse=True)
        return routes[:MAX_ROUTES_PER_SCAN]

    def _build_graph(self, snapshots: list[PoolSnapshot]) -> PriceGraph:
        g = PriceGraph()
        for snap in snapshots:
            sym0 = self._addr_to_sym.get(snap.token0)
            sym1 = self._addr_to_sym.get(snap.token1)
            if not sym0 or not sym1:
                continue
            g.add_node(sym0)
            g.add_node(sym1)
            price = snap.price_token1_per_token0
            if price <= 0:
                continue
            fee = snap.fee // 100  # Uniswap fee in bps (3000 → 30 bps)
            g.add_edge(sym0, sym1, price,       snap.address, fee)
            g.add_edge(sym1, sym0, 1.0 / price, snap.address, fee)
        return g

    def _find_routes(
        self, graph: PriceGraph, snapshots: list[PoolSnapshot]
    ) -> list[ArbitrageRoute]:
        routes = []
        snap_map = {s.address: s for s in snapshots}

        def scan_from(source: str) -> list[ArbitrageRoute]:
            found = []
            try:
                cycles = graph.bellman_ford_negative_cycles(source)
                for cycle in cycles:
                    route = self._cycle_to_route(cycle, graph, snap_map)
                    if route and route.net_profit_usd >= MIN_PROFIT_USD:
                        found.append(route)
            except Exception as exc:
                logger.debug("Bellman-Ford error from %s: %s", source, exc)
            return found

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(scan_from, node): node
                for node in graph.nodes
            }
            for future, node in futures.items():
                try:
                    routes.extend(future.result(timeout=ROUTE_TIMEOUT_SEC))
                except FuturesTimeout:
                    logger.warning("Route scan timed out for source=%s", node)
                except Exception as exc:
                    logger.debug("Route scan error for %s: %s", node, exc)

        # Deduplicate by sorted path
        seen = set()
        unique = []
        for r in routes:
            key = tuple(sorted(r.path))
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def _cycle_to_route(
        self,
        cycle: list[str],
        graph: PriceGraph,
        snap_map: dict,
    ) -> Optional[ArbitrageRoute]:
        """Convert a detected cycle into a priced ArbitrageRoute."""
        if len(cycle) < 3:
            return None

        # Simulate 1 ETH loan through the cycle
        loan_eth = 1.0
        amount = loan_eth
        pools_used = []
        total_fee_bps = _AAVE_FLASH_FEE_BPS  # flash loan fee

        for i in range(len(cycle) - 1):
            u, v = cycle[i], cycle[i + 1]
            pool_addr, fee_bps = graph.edge_pool(u, v)
            if not pool_addr:
                return None
            snap = snap_map.get(pool_addr)
            if not snap:
                return None

            # Price of this hop
            if u == self._addr_to_sym.get(snap.token0):
                price = snap.price_token1_per_token0
            else:
                price = 1.0 / snap.price_token1_per_token0 if snap.price_token1_per_token0 > 0 else 0.0

            if price <= 0:
                return None

            amount *= price * (1 - fee_bps / 10_000)
            pools_used.append(pool_addr)
            total_fee_bps += fee_bps

        gross_profit_eth  = amount - loan_eth
        flash_fee_eth     = loan_eth * (_AAVE_FLASH_FEE_BPS / 10_000)
        net_profit_eth    = gross_profit_eth - flash_fee_eth
        net_profit_usd    = net_profit_eth * _WETH_USD_PRICE - _GAS_COST_USD_EST
        gross_profit_usd  = gross_profit_eth * _WETH_USD_PRICE
        profit_bps        = (net_profit_eth / loan_eth) * 10_000

        return ArbitrageRoute(
            path=cycle,
            pool_addresses=pools_used,
            gross_profit_usd=gross_profit_usd,
            net_profit_usd=net_profit_usd,
            profit_bps=profit_bps,
            loan_amount_eth=loan_eth,
        )

    @property
    def addr_to_sym(self) -> dict[str, str]:
        return self._addr_to_sym
