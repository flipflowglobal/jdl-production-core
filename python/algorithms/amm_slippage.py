class AMMSlippage:
    """
    Newton-Raphson constant product AMM slippage model.
    Formula: (x + dx)(y - dy) = k  =>  dy = y*dx / (x+dx)
    Calculates exact output accounting for LP fee.
    """

    @staticmethod
    def exact_output(reserve_in: int, reserve_out: int,
                     amount_in: int, fee_bps: int = 30) -> int:
        if reserve_in <= 0 or reserve_out <= 0:
            return 0
        amt_with_fee = amount_in * (10000 - fee_bps)
        numerator    = amt_with_fee * reserve_out
        denominator  = (reserve_in * 10000) + amt_with_fee
        return numerator // denominator if denominator > 0 else 0

    @staticmethod
    def price_impact(reserve_in: int, reserve_out: int,
                     amount_in: int, fee_bps: int = 30) -> float:
        if reserve_in <= 0 or amount_in <= 0:
            return 1.0
        actual = AMMSlippage.exact_output(reserve_in, reserve_out, amount_in, fee_bps)
        ideal  = amount_in * reserve_out // reserve_in
        if ideal == 0:
            return 1.0
        return max(0.0, (ideal - actual) / ideal)

    @staticmethod
    def acceptable(reserve_in: int, reserve_out: int,
                   amount_in: int, max_impact: float = 0.003) -> bool:
        return AMMSlippage.price_impact(reserve_in, reserve_out, amount_in) <= max_impact
