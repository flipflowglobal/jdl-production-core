"""
Tests for config.py — the typed, validating env readers.

Pure: sets/clears os.environ entries in-process, no .env file, no chain, no
network. Run: cd python && python3 jdl_flash/test_config.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdl_flash import config as cfg


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  ✓ {msg}")
        else:
            failed += 1; print(f"  ✗ {msg}")

    def fresh(**env):
        """Clear the issue registry and install exactly `env` for the next read."""
        cfg.reset()
        for key in list(os.environ):
            if key.startswith("JDLTEST_"):
                del os.environ[key]
        for key, value in env.items():
            os.environ[key] = value

    # ── the bug that motivated the module ────────────────────────────────────
    # MIN_PROFIT_USD=0.o1 (letter 'o') used to raise ValueError during import of
    # flash_loan_engine, taking down every `jdl` command with a bare traceback.
    fresh(JDLTEST_MIN_PROFIT="0.o1")
    value = cfg.env_float("JDLTEST_MIN_PROFIT", default=0.50)
    check(value == 0.50, "a letter-'o' typo falls back to the default instead of raising")
    check(not cfg.ok(), "a malformed value makes config.ok() False (live execution fails closed)")
    check(len(cfg.issues()) == 1, "exactly one issue recorded")
    issue = cfg.issues()[0]
    check(issue.name == "JDLTEST_MIN_PROFIT", "the issue names the variable that carried the bad value")
    check("0.o1" in issue.line(), "the issue shows the offending value")
    check("letter 'o'" in issue.line(), "the issue hints at the specific typo (letter o vs digit 0)")

    # ── other malformed spellings get their own targeted hints ───────────────
    fresh(JDLTEST_N="10,000.5")
    cfg.env_float("JDLTEST_N", default=1.0)
    check("thousands separators" in cfg.issues()[0].line(), "a comma gets a thousands-separator hint")

    fresh(JDLTEST_N="$0.50")
    cfg.env_float("JDLTEST_N", default=1.0)
    check("currency symbol" in cfg.issues()[0].line(), "a currency prefix gets a currency hint")

    fresh(JDLTEST_N="5%")
    cfg.env_float("JDLTEST_N", default=1.0)
    check("unit suffix" in cfg.issues()[0].line(), "a unit suffix gets a suffix hint")

    # Python's own float()/int() accept underscore separators and exponents, so
    # these are legitimate values, not errors.
    fresh(JDLTEST_N="500_000")
    check(cfg.env_float("JDLTEST_N", default=1.0) == 500_000.0 and cfg.ok(),
          "an underscore digit separator is accepted (Python parses it)")
    fresh(JDLTEST_N="1e4")
    check(cfg.env_float("JDLTEST_N", default=1.0) == 10_000.0 and cfg.ok(),
          "exponent notation is accepted")

    # A hint must only fire when its repair actually yields a number. "not-a-number"
    # contains an 'o', but replacing it with '0' still doesn't parse — so the
    # operator gets the generic hint rather than a false lead about a typo.
    fresh(JDLTEST_N="not-a-number")
    cfg.env_float("JDLTEST_N", default=7.0)
    check(cfg.issues()[0].hint == "Expected a plain decimal number.",
          "a value whose letters aren't a digit typo gets the generic hint, not a false lead")

    fresh(JDLTEST_N="1.2.3")
    cfg.env_float("JDLTEST_N", default=7.0)
    check("more than one decimal point" in cfg.issues()[0].line(),
          "a double decimal point is called out specifically")

    # ── well-formed values are returned untouched, with no issue ─────────────
    fresh(JDLTEST_N="0.01")
    check(cfg.env_float("JDLTEST_N", default=0.50) == 0.01, "a well-formed float is read exactly")
    check(cfg.ok(), "a well-formed value records no issue")

    fresh(JDLTEST_N="  2.5  ")
    check(cfg.env_float("JDLTEST_N", default=0.0) == 2.5, "surrounding whitespace is stripped")

    fresh()
    check(cfg.env_float("JDLTEST_MISSING", default=3.25) == 3.25, "an unset variable yields the default")
    check(cfg.ok(), "an unset variable is not an issue — defaults are legitimate")

    fresh(JDLTEST_N="")
    check(cfg.env_float("JDLTEST_N", default=3.25) == 3.25, "an empty value is treated as unset")
    check(cfg.ok(), "an empty value is not an issue")

    # ── NaN / infinity parse via float() but are never valid config ──────────
    for spelling in ("nan", "inf", "-inf", "Infinity"):
        fresh(JDLTEST_N=spelling)
        got = cfg.env_float("JDLTEST_N", default=1.0)
        check(got == 1.0 and not cfg.ok(), f"{spelling!r} is rejected as non-finite")

    # ── range enforcement ────────────────────────────────────────────────────
    fresh(JDLTEST_N="-5")
    check(cfg.env_float("JDLTEST_N", default=0.5, minimum=0.0) == 0.5,
          "a below-minimum value falls back to the default")
    check("out of range" in cfg.issues()[0].line(), "the out-of-range issue says so")

    fresh(JDLTEST_N="999")
    check(cfg.env_float("JDLTEST_N", default=5.0, maximum=100.0) == 5.0,
          "an above-maximum value falls back to the default")

    fresh(JDLTEST_N="50")
    check(cfg.env_float("JDLTEST_N", default=5.0, minimum=0.0, maximum=100.0) == 50.0,
          "an in-range value passes both bounds")
    check(cfg.ok(), "an in-range value records no issue")

    # ── integers ─────────────────────────────────────────────────────────────
    fresh(JDLTEST_N="42161")
    check(cfg.env_int("JDLTEST_N", default=1) == 42161, "a well-formed int is read exactly")

    fresh(JDLTEST_N="42.0")
    check(cfg.env_int("JDLTEST_N", default=1) == 42, "an integral float spelling ('42.0') is accepted")
    check(cfg.ok(), "'42.0' as an int records no issue")

    fresh(JDLTEST_N="42.5")
    check(cfg.env_int("JDLTEST_N", default=7) == 7,
          "a fractional value is refused rather than silently truncated")
    check("whole number" in cfg.issues()[0].line(), "the fractional-int issue explains itself")

    fresh(JDLTEST_N="4216l")  # lowercase L for 1
    check(cfg.env_int("JDLTEST_N", default=42161) == 42161, "an 'l'-for-1 typo falls back to the default")
    check("digit '1'" in cfg.issues()[0].line(), "the 'l'-for-1 typo gets its own hint")

    fresh(JDLTEST_N="0")
    check(cfg.env_int("JDLTEST_N", default=5, minimum=1) == 5, "an int below its minimum falls back")

    # ── booleans ─────────────────────────────────────────────────────────────
    for spelling in ("1", "true", "TRUE", "yes", "on", "y", "enabled"):
        fresh(JDLTEST_B=spelling)
        check(cfg.env_bool("JDLTEST_B") is True and cfg.ok(), f"{spelling!r} reads as True")

    for spelling in ("0", "false", "no", "off", "n", "disabled"):
        fresh(JDLTEST_B=spelling)
        check(cfg.env_bool("JDLTEST_B", default=True) is False and cfg.ok(), f"{spelling!r} reads as False")

    # The real case found in a live environment: an Ethereum address pasted into
    # LIVE_EXECUTION. It used to resolve to False silently — fail-safe, but the
    # operator got no signal at all that their setting was being ignored.
    fresh(JDLTEST_B="0x750d4cb51aa2f0642bca974f6ac05f551b5bc618")
    check(cfg.env_bool("JDLTEST_B", default=False) is False,
          "an unrecognised boolean still resolves to the (fail-safe) default")
    check(not cfg.ok(), "an unrecognised boolean is reported rather than silently ignored")
    check("recognised boolean" in cfg.issues()[0].line(), "the boolean issue explains itself")

    # ── alias resolution: first non-blank wins, matching the engine's _env ────
    fresh(JDLTEST_B="", JDLTEST_A="9")
    check(cfg.env_float("JDLTEST_B", "JDLTEST_A", default=0.0) == 9.0,
          "a blank first alias falls through to the next one")

    fresh(JDLTEST_B="3", JDLTEST_A="9")
    check(cfg.env_float("JDLTEST_B", "JDLTEST_A", default=0.0) == 3.0,
          "the first non-blank alias wins")

    fresh(JDLTEST_B="bad", JDLTEST_A="9")
    cfg.env_float("JDLTEST_B", "JDLTEST_A", default=0.0)
    check(cfg.issues()[0].name == "JDLTEST_B",
          "the issue names the alias that was actually set, not the canonical name")

    # ── strings never fail ───────────────────────────────────────────────────
    fresh(JDLTEST_S="  hello  ")
    check(cfg.env_str("JDLTEST_S", default="x") == "hello", "env_str strips whitespace")
    fresh()
    check(cfg.env_str("JDLTEST_S", default="x") == "x", "env_str falls back to its default")

    # ── registry behaviour ───────────────────────────────────────────────────
    fresh(JDLTEST_N="bad")
    cfg.env_float("JDLTEST_N", default=1.0)
    cfg.env_float("JDLTEST_N", default=1.0)
    check(len(cfg.issues()) == 1, "the same bad value read twice records one issue, not two")

    fresh(JDLTEST_N="bad", JDLTEST_A="worse")
    cfg.env_float("JDLTEST_N", default=1.0)
    cfg.env_float("JDLTEST_A", default=1.0)
    check(len(cfg.issues()) == 2, "distinct bad values each record their own issue")
    check(len(cfg.issue_lines()) == 2, "issue_lines() renders every issue")

    cfg.reset()
    check(cfg.ok() and cfg.issues() == (), "reset() clears the registry")

    fresh()  # leave the environment clean for other suites in the same process
    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
