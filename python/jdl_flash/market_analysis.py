"""Market microstructure analytics for flash-loan arbitrage systems. Stdlib only."""
__all__ = ["MarketAnalysis"]

import math
import statistics
from typing import List, Optional, Tuple, Dict, Any


class MarketAnalysis:
    """Market microstructure signal library for arbitrage systems."""

    def hurst_exponent(self, series: List[float]) -> Optional[float]:
        """Estimate Hurst exponent via R/S analysis. Returns ~[0,1] or None."""
        try:
            n = len(series)
            if n < 20:
                return None
            series = [float(v) for v in series]

            def _rs(sub):
                m = statistics.mean(sub)
                dev = [x - m for x in sub]
                cum, run = [], 0.0
                for d in dev:
                    run += d
                    cum.append(run)
                r = max(cum) - min(cum)
                try:
                    s = statistics.stdev(sub)
                except statistics.StatisticsError:
                    return None
                return (r / s) if s else None

            lags, rs_vals = [], []
            for lag in [n // 4, n // 3, n // 2, n]:
                if lag < 4:
                    continue
                chunks = [series[i:i + lag] for i in range(0, n - lag + 1, lag)]
                vals = [_rs(c) for c in chunks if len(c) == lag]
                vals = [v for v in vals if v and v > 0]
                if vals:
                    lags.append(math.log(lag))
                    rs_vals.append(math.log(statistics.mean(vals)))
            if len(lags) < 2:
                return None
            mx, my = statistics.mean(lags), statistics.mean(rs_vals)
            num = sum((lags[i] - mx) * (rs_vals[i] - my) for i in range(len(lags)))
            den = sum((x - mx) ** 2 for x in lags)
            return max(0.0, min(1.0, num / den)) if den else None
        except Exception:
            return None

    def volatility_regime(self, returns: List[float]) -> Dict[str, Any]:
        """Classify volatility regime: low/normal/high via rolling stdev percentile."""
        default: Dict[str, Any] = {"regime": None, "sigma": None}
        try:
            if not returns or len(returns) < 5:
                return default
            returns = [float(r) for r in returns]
            window = max(5, len(returns) // 5)
            stds = []
            for i in range(window, len(returns) + 1):
                try:
                    stds.append(statistics.stdev(returns[i - window:i]))
                except statistics.StatisticsError:
                    pass
            if not stds:
                return default
            sigma = stds[-1]
            s = sorted(stds)
            k = len(s)
            p33, p66 = s[max(0, int(k * 0.33) - 1)], s[max(0, int(k * 0.66) - 1)]
            regime = "low" if sigma <= p33 else ("normal" if sigma <= p66 else "high")
            return {"regime": regime, "sigma": sigma}
        except Exception:
            return default

    def order_flow_imbalance(
        self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]
    ) -> float:
        """(bid_vol - ask_vol) / (bid_vol + ask_vol) -> [-1, 1]. Returns 0.0 on error."""
        try:
            bv = sum(float(sz) for _, sz in bids if sz > 0)
            av = sum(float(sz) for _, sz in asks if sz > 0)
            total = bv + av
            return (bv - av) / total if total else 0.0
        except Exception:
            return 0.0

    def vwap(self, trades: List[Tuple[float, float]]) -> Optional[float]:
        """Volume-weighted average price from (price, size) trades."""
        try:
            if not trades:
                return None
            num = sum(float(p) * float(s) for p, s in trades if s > 0)
            den = sum(float(s) for _, s in trades if s > 0)
            return (num / den) if den > 0 else None
        except Exception:
            return None

    def twap(self, prices: List[float]) -> Optional[float]:
        """Time-weighted average price: arithmetic mean of price series."""
        try:
            return statistics.mean([float(p) for p in prices]) if prices else None
        except Exception:
            return None

    def liquidity_depth(
        self, reserve_x: float, reserve_y: float, price_impact_bps: float
    ) -> Optional[float]:
        """Max notional (y) tradeable before exceeding price_impact_bps on constant-product AMM."""
        try:
            rx, ry, bps = float(reserve_x), float(reserve_y), float(price_impact_bps)
            if rx <= 0 or ry <= 0 or bps <= 0:
                return None
            alpha = bps / 10_000.0
            dx = alpha * rx
            new_ry = (rx * ry) / (rx + dx)
            return max(0.0, ry - new_ry)
        except Exception:
            return None

    def spread_bps(self, best_bid: float, best_ask: float) -> Optional[float]:
        """Bid-ask spread in basis points: (ask - bid) / mid * 10000."""
        try:
            bid, ask = float(best_bid), float(best_ask)
            if bid <= 0 or ask <= bid:
                return None
            return (ask - bid) / ((bid + ask) / 2.0) * 10_000.0
        except Exception:
            return None

    def microprice(
        self, best_bid: float, bid_sz: float, best_ask: float, ask_sz: float
    ) -> Optional[float]:
        """Size-weighted midpoint: (ask*bid_sz + bid*ask_sz) / (bid_sz + ask_sz)."""
        try:
            bid, ask = float(best_bid), float(best_ask)
            bsz, asz = float(bid_sz), float(ask_sz)
            if bsz < 0 or asz < 0 or (bsz + asz) == 0:
                return None
            return (ask * bsz + bid * asz) / (bsz + asz)
        except Exception:
            return None

    def summary(
        self,
        series: Optional[List[float]] = None,
        returns: Optional[List[float]] = None,
        bids: Optional[List[Tuple[float, float]]] = None,
        asks: Optional[List[Tuple[float, float]]] = None,
        trades: Optional[List[Tuple[float, float]]] = None,
        prices: Optional[List[float]] = None,
        reserve_x: Optional[float] = None,
        reserve_y: Optional[float] = None,
        price_impact_bps: Optional[float] = None,
        best_bid: Optional[float] = None,
        best_ask: Optional[float] = None,
        bid_sz: Optional[float] = None,
        ask_sz: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Combine all available signals. All inputs optional; missing -> None."""
        out: Dict[str, Any] = {}
        _safe = lambda f, *a, **k: _call(f, *a, **k)

        def _call(fn, *a, **k):
            try:
                return fn(*a, **k)
            except Exception:
                return None

        out["hurst_exponent"] = _call(self.hurst_exponent, series) if series is not None else None
        out["volatility_regime"] = (
            _call(self.volatility_regime, returns)
            if returns is not None
            else {"regime": None, "sigma": None}
        )
        out["order_flow_imbalance"] = (
            _call(self.order_flow_imbalance, bids or [], asks or [])
            if (bids is not None or asks is not None)
            else None
        )
        out["vwap"] = _call(self.vwap, trades) if trades is not None else None
        out["twap"] = _call(self.twap, prices) if prices is not None else None
        out["liquidity_depth"] = (
            _call(self.liquidity_depth, reserve_x, reserve_y, price_impact_bps)
            if all(v is not None for v in [reserve_x, reserve_y, price_impact_bps])
            else None
        )
        out["spread_bps"] = (
            _call(self.spread_bps, best_bid, best_ask)
            if (best_bid is not None and best_ask is not None)
            else None
        )
        out["microprice"] = (
            _call(self.microprice, best_bid, bid_sz, best_ask, ask_sz)
            if all(v is not None for v in [best_bid, bid_sz, best_ask, ask_sz])
            else None
        )
        return out
