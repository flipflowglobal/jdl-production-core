from typing import Dict, List, Optional, Tuple
import math

class BellmanFord:
    """
    Bellman-Ford negative cycle detection for arbitrage.
    Converts exchange rates to log space: edge_weight = -log(rate)
    Negative cycle = product of rates > 1.0 = profit exists.
    Finds all profitable paths automatically.
    """

    @staticmethod
    def find_cycles(rates: Dict[str, Dict[str, float]]) -> List[List[str]]:
        tokens = list(rates.keys())
        n      = len(tokens)
        if n < 2:
            return []
        INF    = float("inf")
        dist   = {t: INF for t in tokens}
        pred   = {t: None for t in tokens}
        if not tokens:
            return []
        dist[tokens[0]] = 0.0
        edges = []
        for src, dests in rates.items():
            for dst, rate in dests.items():
                if rate > 0:
                    edges.append((src, dst, -math.log(rate)))
        for _ in range(n - 1):
            for src, dst, w in edges:
                if dist[src] + w < dist[dst]:
                    dist[dst] = dist[src] + w
                    pred[dst] = src
        cycles   = []
        in_cycle = set()
        for src, dst, w in edges:
            if dist[src] + w < dist[dst] and dst not in in_cycle:
                visited = set()
                node    = dst
                path    = []
                for _ in range(n + 1):
                    if node in visited:
                        start = node
                        seg   = []
                        cur   = dst
                        for _ in range(n):
                            if cur == start and seg:
                                break
                            seg.append(cur)
                            if pred[cur] is None:
                                break
                            cur = pred[cur]
                        seg.append(start)
                        seg.reverse()
                        if len(seg) > 2:
                            cycles.append(seg)
                            in_cycle.update(seg)
                        break
                    visited.add(node)
                    path.append(node)
                    if pred[node] is None:
                        break
                    node = pred[node]
        return cycles
