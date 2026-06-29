"""
realness_guard.py — Production flash-loan arbitrage fault-tolerance backbone.
Enforces real-values-only discipline, system health checks, and fault-tolerant
call wrappers. Pure stdlib; no external dependencies.
"""
from __future__ import annotations
import math
import time
import traceback
from typing import Any, Callable, Dict, Optional, Tuple

__all__ = ["RealnessGuard", "SystemDoctor", "safe", "retry"]

_SIM_TOKENS = ("sim", "dry", "fake", "random")
_MIN_AMOUNT = 0.0
_MAX_RATIO = 1_000_000.0  # max sane amount_out / amount_in ratio


class RealnessGuard:
    """Enforces that only real, live values flow through production code paths."""

    def __init__(self, simulated_sentinels: Optional[Tuple[Any, ...]] = None) -> None:
        """Args: simulated_sentinels — extra sentinel values to reject."""
        self._sentinels: Tuple[Any, ...] = simulated_sentinels or ()

    def assert_real(self, value: Any, name: str = "value") -> bool:
        """Return True iff value is a finite, non-None, non-sentinel number.

        Returns False (never raises) for None, NaN, inf, or flagged sentinels.
        """
        try:
            if value is None:
                return False
            for s in self._sentinels:
                if value is s or value == s:
                    return False
            if not isinstance(value, (int, float)):
                return False
            return not (math.isnan(value) or math.isinf(value))
        except Exception:
            return False

    def forbid_simulated(self, live: bool, source: str) -> bool:
        """Reject sources containing sim/dry/fake/random tokens when live=True.

        Raises ValueError on forbidden source; returns True when acceptable.
        """
        try:
            if not live:
                return True
            lower = source.lower() if isinstance(source, str) else ""
            for token in _SIM_TOKENS:
                if token in lower:
                    raise ValueError(
                        f"Simulated source '{source}' forbidden in live mode "
                        f"(matched token: '{token}')."
                    )
            return True
        except ValueError:
            raise
        except Exception:
            return False

    def validate_quote(self, amount_out: Any, amount_in: Any) -> bool:
        """Sanity-check a DEX quote: both real, amount_in>0, ratio within bounds."""
        try:
            if not self.assert_real(amount_out, "amount_out"):
                return False
            if not self.assert_real(amount_in, "amount_in"):
                return False
            if amount_in <= _MIN_AMOUNT or amount_out < _MIN_AMOUNT:
                return False
            return (amount_out / amount_in) <= _MAX_RATIO
        except Exception:
            return False


class SystemDoctor:
    """Registers named health checks and runs them all, capturing every failure.

    Checks are callables returning (ok: bool, detail: str).
    """

    def __init__(self) -> None:
        # name -> (callable, is_critical)
        self._checks: Dict[str, Tuple[Callable[[], Tuple[bool, str]], bool]] = {}

    def register(
        self,
        name: str,
        check: Callable[[], Tuple[bool, str]],
        critical: bool = True,
    ) -> None:
        """Register a health-check callable under *name*."""
        self._checks[name] = (check, critical)

    def run_all(self) -> Dict[str, Any]:
        """Execute every check; never raises. Returns consolidated report dict.

        Report keys: passed (int), failed (int), critical_failures (list[str]),
        results (dict of per-check detail dicts).
        """
        passed = 0
        failed = 0
        critical_failures = []
        results: Dict[str, Any] = {}

        for name, (check, is_critical) in self._checks.items():
            try:
                ok, detail = check()
            except Exception:
                ok = False
                detail = f"Check raised exception: {traceback.format_exc(limit=3)}"

            if ok:
                passed += 1
            else:
                failed += 1
                if is_critical:
                    critical_failures.append(name)

            results[name] = {"ok": ok, "detail": detail, "critical": is_critical}

        return {
            "passed": passed,
            "failed": failed,
            "critical_failures": critical_failures,
            "results": results,
        }


def safe(fn: Callable, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    """Project-wide fault-tolerance wrapper.

    Calls fn(*args, **kwargs); returns *default* on ANY exception — never raises.
    """
    try:
        return fn(*args, **kwargs)
    except Exception:
        return default


def retry(fn: Callable, attempts: int = 3, backoff_s: float = 0.0) -> Any:
    """Synchronous retry with optional backoff.

    Tries fn() up to *attempts* times; sleeps *backoff_s* seconds between
    failures. Returns fn's result on first success, or None after all failures.
    fn must be a zero-argument callable (use functools.partial or a lambda).
    """
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except Exception:
            if attempt < attempts - 1 and backoff_s > 0.0:
                time.sleep(backoff_s)
    return None
