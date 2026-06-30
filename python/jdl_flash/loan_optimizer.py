"""
loan_optimizer.py - Flash-loan sizing optimizer for production arbitrage systems.

Provides LoanOptimizer class for maximising flash-loan size against real on-chain
liquidity using injected callables for all external data access.
"""

__all__ = ["LoanOptimizer"]

import math


class LoanOptimizer:
    """Optimises flash-loan sizing for arbitrage using real on-chain data.

    All on-chain access is performed exclusively through injected callables,
    keeping this module standalone and parallel-safe.

    Args:
        liquidity_fn: Callable(token: str) -> int
            Returns base units available in the lending source for a token.
        quote_fn: Callable(token_in: str, token_out: str,
                           amount_in_baseunits: int, fee: int) -> int | None
            Returns on-chain quote for a swap leg, or None if unavailable.
    """

    def __init__(self, liquidity_fn, quote_fn):
        self._liquidity_fn = liquidity_fn
        self._quote_fn = quote_fn

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def max_borrow(self, token: str, source_caps: dict) -> int:
        """Return the largest safe borrow: min(pool_available, hard_cap).

        Args:
            token: Token identifier string.
            source_caps: Dict mapping token -> configured hard cap (int, base units).

        Returns:
            Largest safe borrow amount in base units, or 0 on any error.
        """
        try:
            pool_available = self._liquidity_fn(token)
            if pool_available is None:
                return 0
            pool_available = int(pool_available)
            hard_cap = source_caps.get(token)
            if hard_cap is None:
                return max(0, pool_available)
            return max(0, min(pool_available, int(hard_cap)))
        except Exception:
            return 0

    def optimal_size(
        self,
        token_in: str,
        token_out: str,
        buy_fee: int,
        sell_fee: int,
        lo: int,
        hi: int,
        aave_bps: int = 5,
    ) -> dict:
        """Find the loan size in [lo, hi] that maximises net profit via ternary search.

        Net = round_trip_out(size) - size - premium
        Premium = size * aave_bps / 10000 (Aave-style flash-loan fee).

        Both legs use quote_fn. If either leg returns None the size is infeasible
        and treated as net = -infinity for search purposes.

        Args:
            token_in: Input token identifier.
            token_out: Intermediate token identifier.
            buy_fee: Fee in integer basis points for the buy leg.
            sell_fee: Fee in integer basis points for the sell leg.
            lo: Lower bound for loan size search (base units).
            hi: Upper bound for loan size search (base units).
            aave_bps: Flash-loan premium in basis points (default 5 = 0.05%).

        Returns:
            Dict with keys 'size' (int), 'net' (int), 'gross' (int).
            net may be <= 0; reported truthfully.
        """
        _INFEASIBLE = -(2 ** 63)

        def net_at(size: int) -> int:
            try:
                size = int(size)
                if size <= 0:
                    return _INFEASIBLE
                mid = self._quote_fn(token_in, token_out, size, buy_fee)
                if mid is None:
                    return _INFEASIBLE
                out = self._quote_fn(token_out, token_in, int(mid), sell_fee)
                if out is None:
                    return _INFEASIBLE
                premium = math.ceil(size * aave_bps / 10_000)
                return int(out) - size - premium
            except Exception:
                return _INFEASIBLE

        try:
            lo = int(lo)
            hi = int(hi)
            if lo > hi or hi <= 0:
                return {"size": 0, "net": 0, "gross": 0}

            # Ternary search (unimodal assumption over loan size)
            _ITERATIONS = 200
            left, right = lo, hi
            for _ in range(_ITERATIONS):
                if right - left < 3:
                    break
                m1 = left + (right - left) // 3
                m2 = right - (right - left) // 3
                if net_at(m1) < net_at(m2):
                    left = m1
                else:
                    right = m2

            best_size = left
            best_net = _INFEASIBLE
            for candidate in range(left, min(right + 1, left + 4)):
                n = net_at(candidate)
                if n > best_net:
                    best_net = n
                    best_size = candidate

            if best_net == _INFEASIBLE:
                return {"size": 0, "net": 0, "gross": 0}

            # Compute gross (round-trip out before subtracting principal+premium)
            gross = best_net + best_size + math.ceil(best_size * aave_bps / 10_000)
            return {"size": best_size, "net": best_net, "gross": gross}

        except Exception:
            return {"size": 0, "net": 0, "gross": 0}

    def kelly_cap(
        self,
        bankroll: float,
        win_p: float,
        win_loss: float,
        frac: float = 0.5,
    ) -> float:
        """Return fractional Kelly position size.

        Kelly fraction = win_p - (1 - win_p) / win_loss
        Final size = bankroll * kelly_fraction * frac

        Args:
            bankroll: Available capital in base units.
            win_p: Probability of a win (0 < win_p < 1).
            win_loss: Win/loss ratio (expected win / expected loss magnitude).
            frac: Fractional Kelly multiplier (default 0.5 = half-Kelly).

        Returns:
            Recommended position size (float), or 0.0 on invalid inputs / error.
        """
        try:
            bankroll = float(bankroll)
            win_p = float(win_p)
            win_loss = float(win_loss)
            frac = float(frac)

            if not (0.0 < win_p < 1.0):
                return 0.0
            if win_loss <= 0.0:
                return 0.0
            if bankroll <= 0.0 or frac <= 0.0:
                return 0.0

            kelly_f = win_p - (1.0 - win_p) / win_loss
            if kelly_f <= 0.0:
                return 0.0

            return bankroll * kelly_f * frac
        except Exception:
            return 0.0

    def size_report(
        self,
        token_in: str,
        token_out: str,
        buy_fee: int,
        sell_fee: int,
        lo: int,
        hi: int,
        source_caps: dict,
        bankroll: float,
        win_p: float,
        win_loss: float,
        aave_bps: int = 5,
        kelly_frac: float = 0.5,
    ) -> dict:
        """Produce a consolidated sizing report combining all optimizer outputs.

        Returns a dict with keys:
            'max_borrow': int - max safe borrow for token_in
            'optimal': dict - result from optimal_size
            'kelly': float - kelly-capped position size
            'recommended': int - min(max_borrow, optimal['size'], kelly)
            'feasible': bool - True if optimal net > 0 and recommended > 0

        Returns empty labelled dict on error; never raises.
        """
        _EMPTY = {
            "max_borrow": 0,
            "optimal": {"size": 0, "net": 0, "gross": 0},
            "kelly": 0.0,
            "recommended": 0,
            "feasible": False,
        }
        try:
            mb = self.max_borrow(token_in, source_caps)
            opt = self.optimal_size(
                token_in, token_out, buy_fee, sell_fee, lo, hi, aave_bps
            )
            kc = self.kelly_cap(bankroll, win_p, win_loss, kelly_frac)

            recommended = int(min(mb, opt["size"], kc)) if kc > 0 else min(mb, opt["size"])
            feasible = bool(opt["net"] > 0 and recommended > 0)

            return {
                "max_borrow": mb,
                "optimal": opt,
                "kelly": kc,
                "recommended": recommended,
                "feasible": feasible,
            }
        except Exception:
            return _EMPTY
