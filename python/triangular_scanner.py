"""triangular_scanner - Multi-token 3-hop arbitrage scanner for flash-loan systems.

Enumerates A->B->C->A cycles via an injected quote_fn, computes net profit
after Aave 5bps premium, returns ranked opportunities. No synthetic prices.
"""
__all__ = ["TriangularScanner"]

import math
import logging
import itertools
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_AAVE_PREMIUM_BPS = 5   # Aave V3 flash-loan fee: 5 bps
_BPS_DENOM = 10_000

QuoteFn = Callable[[str, str, int, int], Optional[int]]
TokenRegistry = Dict[str, Tuple[str, int]]  # symbol -> (address, decimals)


class TriangularScanner:
    """Scans A->B->C->A arbitrage cycles using a dependency-injected quote function.

    Args:
        token_registry: Maps symbol -> (address, decimals).
        quote_fn: (token_in, token_out, amount_in_baseunits, fee) -> int|None.
        fee_tiers: Uniswap V3 fee tiers (hundredths of a bip). Default [100,500,3000,10000].
    """

    DEFAULT_FEE_TIERS: List[int] = [100, 500, 3000, 10_000]

    def __init__(self, token_registry: TokenRegistry, quote_fn: QuoteFn,
                 fee_tiers: Optional[List[int]] = None) -> None:
        self._registry = token_registry
        self._quote_fn = quote_fn
        self._fee_tiers = fee_tiers if fee_tiers is not None else self.DEFAULT_FEE_TIERS

    def _to_base(self, symbol: str, amount_human: float) -> Optional[int]:
        """Convert human-readable amount to integer base units."""
        entry = self._registry.get(symbol)
        if entry is None:
            return None
        try:
            return int(amount_human * (10 ** entry[1]))
        except Exception:
            return None

    def _to_human(self, symbol: str, amount_base: int) -> Optional[float]:
        """Convert integer base units to human-readable float."""
        entry = self._registry.get(symbol)
        if entry is None:
            return None
        try:
            return amount_base / (10 ** entry[1])
        except Exception:
            return None

    def _safe_quote(self, token_in: str, token_out: str, amount_in: int, fee: int) -> Optional[int]:
        """Call quote_fn with full exception isolation."""
        try:
            result = self._quote_fn(token_in, token_out, amount_in, fee)
            if result is None or not isinstance(result, int) or result <= 0:
                return None
            return result
        except Exception as exc:
            logger.debug("quote_fn raised %s->%s fee=%d: %s", token_in, token_out, fee, exc)
            return None

    def _aave_repay(self, amount_base: int) -> int:
        """Principal + Aave 5bps premium owed at flash-loan end."""
        return amount_base + math.ceil(amount_base * _AAVE_PREMIUM_BPS / _BPS_DENOM)

    def _probe_routes(self, start: str, amount_in_base: int,
                      intermediates: List[str], fees: List[int]) -> Tuple[List[dict], int]:
        """Inner loop: enumerate all (B,C,fee_ab,fee_bc,fee_ca) combos, return routes & count."""
        repay = self._aave_repay(amount_in_base)
        routes: List[dict] = []
        probed = 0
        for sym_b, sym_c in itertools.permutations(intermediates, 2):
            for fee_ab in fees:
                out_ab = self._safe_quote(start, sym_b, amount_in_base, fee_ab)
                if out_ab is None:
                    probed += 1
                    continue
                for fee_bc in fees:
                    out_bc = self._safe_quote(sym_b, sym_c, out_ab, fee_bc)
                    if out_bc is None:
                        probed += 1
                        continue
                    for fee_ca in fees:
                        probed += 1
                        out_ca = self._safe_quote(sym_c, start, out_bc, fee_ca)
                        if out_ca is None:
                            continue
                        net_base = out_ca - repay
                        net_human = self._to_human(start, net_base)
                        if net_human is None:
                            continue
                        routes.append({
                            "path": [start, sym_b, sym_c, start],
                            "fees": [fee_ab, fee_bc, fee_ca],
                            "net_base": net_base,
                            "net_human": net_human,
                        })
        return routes, probed

    def best_triangle(self, start_symbol: str, amount_human: float,
                      tokens: List[str], fee_tiers: Optional[List[int]] = None) -> Optional[dict]:
        """Find the single best A->B->C->A route by net profit after flash-loan repayment.

        Args:
            start_symbol: Token A (must be in token_registry).
            amount_human: Flash-loan size in human units of token A.
            tokens: Universe of symbols for B and C legs.
            fee_tiers: Override instance-level fee tiers for this call.

        Returns:
            dict(path, fees, net_base, net_human, routes_probed) or None.
        """
        try:
            fees = fee_tiers if fee_tiers is not None else self._fee_tiers
            amount_in_base = self._to_base(start_symbol, amount_human)
            if not amount_in_base or amount_in_base <= 0:
                logger.warning("Cannot convert %s %s to base units", amount_human, start_symbol)
                return None
            intermediates = [t for t in tokens if t != start_symbol and t in self._registry]
            if len(intermediates) < 2:
                logger.warning("Need >= 2 intermediates; got %d", len(intermediates))
                return None
            routes, probed = self._probe_routes(start_symbol, amount_in_base, intermediates, fees)
            best = max(routes, key=lambda r: r["net_base"]) if routes else None
            if best:
                best = dict(best, routes_probed=probed)
                logger.info("best_triangle: %d routes probed; best net=%.6f %s via %s",
                            probed, best["net_human"], start_symbol, best["path"])
            else:
                logger.info("best_triangle: %d routes probed; none valid", probed)
            return best
        except Exception as exc:
            logger.error("best_triangle failed: %s", exc, exc_info=True)
            return None

    def scan(self, start_symbol: str, amount_human: float,
             tokens: List[str], fee_tiers: Optional[List[int]] = None) -> dict:
        """Return all profitable routes (net>0) sorted descending plus the absolute best.

        Args:
            start_symbol: Token A.
            amount_human: Flash-loan size in human units.
            tokens: Universe of symbols.
            fee_tiers: Override instance-level fee tiers.

        Returns:
            dict(profitable: list[dict], best: dict|None, routes_probed: int).
        """
        _empty = {"profitable": [], "best": None, "routes_probed": 0}
        try:
            fees = fee_tiers if fee_tiers is not None else self._fee_tiers
            amount_in_base = self._to_base(start_symbol, amount_human)
            if not amount_in_base or amount_in_base <= 0:
                return _empty
            intermediates = [t for t in tokens if t != start_symbol and t in self._registry]
            if len(intermediates) < 2:
                return _empty
            routes, probed = self._probe_routes(start_symbol, amount_in_base, intermediates, fees)
            profitable = sorted(
                [r for r in routes if r["net_base"] > 0],
                key=lambda r: r["net_base"], reverse=True,
            )
            best = max(routes, key=lambda r: r["net_base"]) if routes else None
            logger.info("scan: %d routes probed; %d profitable", probed, len(profitable))
            return {"profitable": profitable, "best": best, "routes_probed": probed}
        except Exception as exc:
            logger.error("scan failed: %s", exc, exc_info=True)
            return _empty
