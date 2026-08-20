"""
Tests for validate_env_config() — pure function over already-loaded module
constants, monkeypatched here (no live chain, no .env file needed).
Run: cd python && python3 jdl_flash/test_config_validation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jdl_flash.flash_loan_engine as e


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  ✓ {msg}")
        else:
            failed += 1; print(f"  ✗ {msg}")

    def reset():
        e.CHAIN_ID = 42161
        e.CONTRACT = "0x1234567890123456789012345678901234567890"
        e.LIVE_EXEC = False
        e.PRIV_KEY = ""
        e.GELATO_ENABLED = False
        # Malformed-env issues are collected at import time from the real
        # environment (see config.py). Clear them so these cases exercise
        # validate_env_config()'s own logic rather than whatever the machine
        # running the tests happens to have in its .env — the parsing of those
        # values is covered by test_config.py instead.
        e.CONFIG_ISSUES = []

    # ── clean config -> no warnings ──
    reset()
    check(e.validate_env_config() == [], "well-formed mainnet config produces no warnings")

    # ── the exact bug this session hit: a duplicate CHAIN_ID line resolving to
    # a wrong/unrelated chain (Gnosis Chain, 100) ──
    reset()
    e.CHAIN_ID = 100
    warnings = e.validate_env_config()
    check(any("CHAIN_ID=100" in w for w in warnings), "flags an unsupported CHAIN_ID (e.g. 100 from a duplicate .env line)")
    check(any("duplicate CHAIN_ID" in w for w in warnings), "warning message hints at the duplicate-.env-line cause")

    # ── Arbitrum Sepolia is accepted, not just mainnet ──
    reset()
    e.CHAIN_ID = e.SEPOLIA_CHAIN_ID
    check(e.validate_env_config() == [], "Arbitrum Sepolia (testnet) chain id produces no warning")
    check(e.SEPOLIA_CHAIN_ID == 421614, "SEPOLIA_CHAIN_ID is Arbitrum Sepolia (421614), not Ethereum Sepolia (11155111)")

    # ── malformed contract address ──
    reset()
    e.CONTRACT = "0x1234"  # truncated
    warnings = e.validate_env_config()
    check(any("doesn't look like a valid address" in w for w in warnings), "flags a truncated contract address")

    reset()
    e.CONTRACT = "not-even-hex-formatted-at-all"
    warnings = e.validate_env_config()
    check(any("doesn't look like a valid address" in w for w in warnings), "flags a non-hex contract address")

    reset()
    e.CONTRACT = ""
    check(e.validate_env_config() == [], "empty CONTRACT (not yet deployed) produces no warning")

    # ── LIVE_EXECUTION on with no way to actually broadcast ──
    reset()
    e.LIVE_EXEC = True
    e.PRIV_KEY = ""
    e.GELATO_ENABLED = False
    warnings = e.validate_env_config()
    check(any("no way to actually broadcast" in w for w in warnings),
          "flags LIVE_EXECUTION=1 with neither PRIVATE_KEY nor GELATO_ENABLED")

    reset()
    e.LIVE_EXEC = True
    e.PRIV_KEY = "0xabc"
    check(e.validate_env_config() == [], "LIVE_EXECUTION=1 with PRIVATE_KEY set is fine")

    reset()
    e.LIVE_EXEC = True
    e.GELATO_ENABLED = True
    check(e.validate_env_config() == [], "LIVE_EXECUTION=1 with GELATO_ENABLED is fine (gasless path)")

    # ── multiple simultaneous problems all surface ──
    reset()
    e.CHAIN_ID = 1  # Ethereum mainnet, not supported by this Arbitrum-only system
    e.CONTRACT = "bad"
    e.LIVE_EXEC = True
    warnings = e.validate_env_config()
    check(len(warnings) == 3, f"multiple distinct problems all reported (got {len(warnings)})")

    # ── the fail-closed contract must cover EVERY env-parsed constant ──
    # Regression: CONFIG_ISSUES/CONFIG_OK were snapshotted partway down the
    # module, so MAXIMISE_REVENUE — parsed below the snapshot — sat outside the
    # contract entirely. `MAXIMISE_REVENUE=ture LIVE_EXECUTION=1` produced
    # CONFIG_OK=True and live execution enabled while config.ok() was False.
    # Re-import the module under a malformed value for each knob it parses.
    import subprocess

    # Assert the value is actually *detected*, not merely that nothing blew up —
    # a weaker probe would pass for a variable that skipped validation entirely.
    probe = (
        "import jdl_flash.flash_loan_engine as e, sys;"
        "var = sys.argv[1];"
        "print('DETECTED' if any(l.startswith(var + '=') for l in e.CONFIG_ISSUES) else 'MISSED');"
        "print('CLOSED' if not e.CONFIG_OK and not e.LIVE_EXEC else 'OPEN')"
    )
    # A controlled baseline: this machine's own environment carries malformed
    # values (that is what motivated config.py), which would mask the variable
    # under test.
    clean = {k: v for k, v in os.environ.items()
             if k not in {"MIN_PROFIT_USD", "LIVE_EXECUTION", "MAXIMISE_REVENUE",
                          "CHAIN_ID", "USE_REAL_QUOTES", "GELATO_ENABLED"}}
    for var, bad in (
        ("MIN_PROFIT_USD", "0.o1"),
        ("MAX_LOAN_USD", "not-a-number"),
        ("REAL_LOAN_USD", "1O000"),
        ("CHAIN_ID", "4216l"),
        ("GELATO_MAX_FEE_USD6", "1.5"),
        ("MAXIMISE_REVENUE", "ture"),
        ("USE_REAL_QUOTES", "yep"),
        ("GELATO_ENABLED", "maybe"),
        ("MAX_CONSECUTIVE_FAILURES", "three"),
        ("RISK_COOLDOWN_SEC", "60s"),
        ("RISK_COOLDOWN_MAX_SEC", "$3600"),
        ("MAX_DAILY_LOSS_USD", "25,00"),
    ):
        env = {**clean, var: bad, "LIVE_EXECUTION": "1", "PRIVATE_KEY": "0x" + "11" * 32}
        out = subprocess.run([sys.executable, "-c", probe, var], capture_output=True, text=True,
                             env=env, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        check(out.returncode == 0, f"a malformed {var} does not crash the import")
        check("DETECTED" in out.stdout, f"a malformed {var} is reported as a config issue")
        check("CLOSED" in out.stdout, f"a malformed {var} disables live execution (fails closed)")

    # ── the kill switch must survive a tilde ──
    # Path('~/jdl/HALT').exists() is always False, so an unexpanded HALT_FILE
    # would mean the emergency stop silently never engages — the worst possible
    # failure mode for an emergency stop. The templates document it with a `~`.
    halt_probe = (
        "import jdl_flash.flash_loan_engine as e;"
        "print('EXPANDED' if e.HALT_FILE and '~' not in e.HALT_FILE else 'RAW:' + str(e.HALT_FILE))"
    )
    out = subprocess.run([sys.executable, "-c", halt_probe], capture_output=True, text=True,
                         env={**clean, "HALT_FILE": "~/jdl/HALT"},
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    check("EXPANDED" in out.stdout, f"a tilde in HALT_FILE is expanded ({out.stdout.strip()})")

    reset()  # leave module state clean for any other test run in the same process
    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
