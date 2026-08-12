"""
config.py — typed, validated environment configuration.

Why this exists
---------------
Every numeric knob in this system used to be read with a bare coercion at module
import time:

    MIN_PROFIT_USD = float(_env('MIN_PROFIT_USD', default='0.50') or '0.50')

That has three production failure modes, all of which were live:

1. **A typo takes the whole product down.** A `.env` containing
   ``MIN_PROFIT_USD=0.o1`` (letter "o", not digit "0") raises
   ``ValueError: could not convert string to float: '0.o1'`` *during import of
   flash_loan_engine*. Because every command — ``jdl status``, ``jdl test``,
   ``jdl integrate``, the engine, the swarm daemon, the supervisor — imports that
   module, a single mistyped character bricks the entire CLI with a bare
   traceback and no indication of which variable is at fault. This is not
   hypothetical; it is the exact failure this module was written in response to.

2. **``validate_env_config()`` never got a chance to run.** The existing
   validator is good, but it is a *function* that runs after import. A malformed
   value kills the process before any validation can report anything.

3. **Out-of-range values were accepted silently.** ``MAX_LOAN_USD=-1`` or
   ``CHAIN_ID=0`` parsed fine and produced nonsense downstream.

What this module guarantees
---------------------------
* **Import never raises.** A malformed or out-of-range value is recorded as a
  :class:`ConfigIssue` and the documented default is used instead, so the CLI,
  the test suite and read-only commands keep working and can *tell the operator
  what is wrong*.
* **Errors are actionable.** Each issue names the variable, shows the offending
  value, and gives a concrete hint ("contains the letter 'o' — did you mean the
  digit '0'?").
* **Every issue is collected, not just the first.** An operator fixing a `.env`
  sees the whole list in one pass instead of re-running after each fix.
* **Fail closed on money paths.** :func:`ok` is False whenever any issue was
  recorded. The engine consults it to refuse live execution: scanning and
  reporting still work, but nothing is broadcast on a config the system could
  not fully parse. Falling back to a default is safe for a read-only path and
  emphatically *not* safe for one that signs transactions.

Pure stdlib, no imports from the rest of the package — it is the lowest layer
and must be importable from anywhere, including before web3 is available.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

__all__ = [
    "ConfigIssue",
    "env_str",
    "env_bool",
    "env_int",
    "env_float",
    "issues",
    "issue_lines",
    "ok",
    "reset",
    "record_issue",
]


@dataclass(frozen=True)
class ConfigIssue:
    """One malformed or out-of-range environment variable.

    ``name`` is the alias that actually carried the bad value (not the canonical
    name), because that is the line the operator has to go edit.
    """

    name: str
    value: str
    reason: str
    hint: str
    using: str

    def line(self) -> str:
        """One-line, operator-facing rendering of this issue."""
        return (
            f"{self.name}={self.value!r} — {self.reason}. {self.hint} "
            f"Using {self.using} instead; live execution is disabled until this is fixed."
        )


# Module-level registry. Populated at import time by the engine's constant
# definitions, read afterwards by startup banners and `jdl` commands.
_ISSUES: List[ConfigIssue] = []


def record_issue(issue: ConfigIssue) -> None:
    """Append an issue to the registry (deduplicated by name+value)."""
    for existing in _ISSUES:
        if existing.name == issue.name and existing.value == issue.value:
            return
    _ISSUES.append(issue)


def issues() -> Tuple[ConfigIssue, ...]:
    """Every configuration issue recorded so far, in discovery order."""
    return tuple(_ISSUES)


def issue_lines() -> List[str]:
    """``issues()`` rendered as operator-facing strings."""
    return [i.line() for i in _ISSUES]


def ok() -> bool:
    """True when every parsed value was well-formed and in range.

    Callers that sign or broadcast transactions must gate on this.
    """
    return not _ISSUES


def reset() -> None:
    """Clear the registry. For tests and for re-reading config after an edit."""
    _ISSUES.clear()


# ── value lookup ────────────────────────────────────────────────────────────

def _first_set(names: Sequence[str]) -> Tuple[Optional[str], str]:
    """First alias in ``names`` with a non-blank value.

    Returns ``(alias_name, stripped_value)``, or ``(None, "")`` when none is set.
    Matches the alias semantics the engine has always used: first non-empty wins,
    values are stripped.
    """
    for name in names:
        raw = os.getenv(name, "")
        if raw and raw.strip():
            return name, raw.strip()
    return None, ""


def env_str(*names: str, default: str = "") -> str:
    """First non-blank value among ``names``, else ``default``. Never fails."""
    _, value = _first_set(names)
    return value or default


# ── diagnostics ─────────────────────────────────────────────────────────────

def _parses(candidate: str) -> bool:
    try:
        float(candidate)
        return True
    except (TypeError, ValueError):
        return False


# Each entry is (repair, hint). A hint is offered only when applying its repair
# to the operator's value actually yields a parseable number — so we never
# misdiagnose. Without that test, the letter-'o' rule would fire on a value like
# "not-a-number" (which contains an 'o') and send the operator hunting for a
# typo that isn't there. Ordered most-specific first.
_NUMERIC_REPAIRS: Sequence[Tuple[object, str]] = (
    (
        lambda s: re.sub(r"[oO]", "0", s),
        "That looks like the letter 'o' where a digit '0' belongs "
        "(e.g. '0.01', not '0.o1').",
    ),
    (
        lambda s: re.sub(r"[lI]", "1", s),
        "That looks like a letter 'l'/'I' where a digit '1' belongs "
        "(e.g. '1.5', not 'l.5').",
    ),
    (
        lambda s: s.replace(",", ""),
        "Use a plain decimal point with no thousands separators "
        "(e.g. '10000.5', not '10,000.5').",
    ),
    (
        lambda s: re.sub(r"^[^\d+\-.]+", "", s),
        "Drop any currency symbol or prefix (e.g. '0.50', not '$0.50').",
    ),
    (
        lambda s: re.sub(r"[%a-zA-Z\s]+$", "", s),
        "Give a bare number with no unit suffix (e.g. '5', not '5%' or '5usd').",
    ),
)

_NUMERIC_FALLBACK_HINT = "Expected a plain decimal number."


def _numeric_hint(raw: str) -> str:
    """A concrete, actionable hint for why ``raw`` is not a number."""
    for repair, hint in _NUMERIC_REPAIRS:
        repaired = repair(raw)
        if repaired != raw and _parses(repaired):
            return hint
    if raw.count(".") > 1:
        return "There is more than one decimal point."
    return _NUMERIC_FALLBACK_HINT


def _range_text(minimum: Optional[float], maximum: Optional[float]) -> str:
    if minimum is not None and maximum is not None:
        return f"must be between {minimum:g} and {maximum:g}"
    if minimum is not None:
        return f"must be >= {minimum:g}"
    if maximum is not None:
        return f"must be <= {maximum:g}"
    return ""


# ── typed readers ───────────────────────────────────────────────────────────

def env_float(
    *names: str,
    default: float,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    """Read a float from the first alias set among ``names``.

    Returns ``default`` — and records a :class:`ConfigIssue` — when the value is
    malformed or outside ``[minimum, maximum]``. Never raises.
    """
    name, raw = _first_set(names)
    if name is None:
        return default

    try:
        value = float(raw)
    except (TypeError, ValueError):
        record_issue(
            ConfigIssue(
                name=name,
                value=raw,
                reason="is not a valid number",
                hint=_numeric_hint(raw),
                using=f"the default {default:g}",
            )
        )
        return default

    # NaN/inf parse successfully via float() but are never valid config.
    if value != value or value in (float("inf"), float("-inf")):
        record_issue(
            ConfigIssue(
                name=name,
                value=raw,
                reason="is not a finite number",
                hint="Expected a plain decimal number.",
                using=f"the default {default:g}",
            )
        )
        return default

    bounds = _range_text(minimum, maximum)
    if bounds and (
        (minimum is not None and value < minimum)
        or (maximum is not None and value > maximum)
    ):
        record_issue(
            ConfigIssue(
                name=name,
                value=raw,
                reason=f"is out of range ({bounds})",
                hint=f"Set a value that {bounds}.",
                using=f"the default {default:g}",
            )
        )
        return default

    return value


def env_int(
    *names: str,
    default: int,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    """Read an int from the first alias set among ``names``.

    A value like ``"42.0"`` is accepted (it is exactly an integer); ``"42.5"`` is
    not, and is reported rather than silently truncated — truncating a loan size
    or a chain id is exactly the kind of quiet wrongness this module exists to
    prevent. Never raises.
    """
    name, raw = _first_set(names)
    if name is None:
        return default

    try:
        value = int(raw, 10)
    except (TypeError, ValueError):
        # Accept an integral float spelling ("42.0"), reject a fractional one.
        try:
            as_float = float(raw)
        except (TypeError, ValueError):
            record_issue(
                ConfigIssue(
                    name=name,
                    value=raw,
                    reason="is not a valid integer",
                    hint=_numeric_hint(raw),
                    using=f"the default {default}",
                )
            )
            return default
        if as_float != as_float or as_float in (float("inf"), float("-inf")) or as_float != int(as_float):
            record_issue(
                ConfigIssue(
                    name=name,
                    value=raw,
                    reason="is not a whole number",
                    hint="Expected a whole number with no fractional part.",
                    using=f"the default {default}",
                )
            )
            return default
        value = int(as_float)

    bounds = _range_text(minimum, maximum)
    if bounds and (
        (minimum is not None and value < minimum)
        or (maximum is not None and value > maximum)
    ):
        record_issue(
            ConfigIssue(
                name=name,
                value=raw,
                reason=f"is out of range ({bounds})",
                hint=f"Set a value that {bounds}.",
                using=f"the default {default}",
            )
        )
        return default

    return value


_TRUE_WORDS = ("1", "true", "yes", "on", "y", "t", "enable", "enabled")
_FALSE_WORDS = ("0", "false", "no", "off", "n", "f", "disable", "disabled")


def env_bool(*names: str, default: bool = False) -> bool:
    """Read a boolean from the first alias set among ``names``.

    Historically an unrecognised value (``LIVE_EXECUTION=ture``) silently meant
    False. That is fail-safe for the live-execution gate but baffling for the
    operator who set it, so an unrecognised value is now *reported* while still
    resolving to ``default``. Never raises.
    """
    name, raw = _first_set(names)
    if name is None:
        return default

    lowered = raw.lower()
    if lowered in _TRUE_WORDS:
        return True
    if lowered in _FALSE_WORDS:
        return False

    record_issue(
        ConfigIssue(
            name=name,
            value=raw,
            reason="is not a recognised boolean",
            hint="Use one of: 1/0, true/false, yes/no, on/off.",
            using=f"the default {str(default).lower()}",
        )
    )
    return default
