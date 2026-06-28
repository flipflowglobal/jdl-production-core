class KellyCriterion:
    """
    Half-Kelly optimal position sizing.
    f* = (bp - q) / b  then halved for safety.
    Maximises long-term growth while preventing ruin.
    """

    @staticmethod
    def fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
        if avg_loss <= 0 or win_rate <= 0:
            return 0.0
        b = avg_win / avg_loss
        p = win_rate
        q = 1.0 - p
        f = (b * p - q) / b
        return max(0.0, f * 0.5)

    @staticmethod
    def loan_size(win_rate: float, avg_win: float,
                  avg_loss: float, max_loan: float = 50000.0) -> float:
        return min(
            KellyCriterion.fraction(win_rate, avg_win, avg_loss) * max_loan,
            max_loan
        )
