"""
Tests for integrate.py — the per-check logic behind `jdl integrate`. Each
check takes an explicit env_path so these run against throwaway tmp files,
never the real ~/jdl/.env.

Run: cd python && python3 jdl_flash/test_integrate.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jdl_flash.integrate as ig


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✓ {msg}")
        else:
            failed += 1
            print(f"  ✗ {msg}")

    tmp = Path(tempfile.mkdtemp(prefix="jdl_integrate_test_"))
    try:
        missing = tmp / "does_not_exist" / ".env"
        ok, detail = ig.check_env_file(missing)
        check(ok is False, "check_env_file: missing file -> not ok")
        check("jdl install" in detail, "check_env_file: hints at the fix")

        present = tmp / ".env"
        present.write_text("PRIVATE_KEY=0xdeadbeef\n")
        ok, _ = ig.check_env_file(present)
        check(ok is True, "check_env_file: existing file -> ok")

        # ── check_required_keys ──
        env_unset = tmp / "unset.env"
        env_unset.write_text("PRIVATE_KEY=<set-via-secrets-manager>\nRPC_URL=\n")
        ok, detail = ig.check_required_keys(env_unset)
        check(ok is False, "check_required_keys: placeholder PRIVATE_KEY/RPC_URL -> not ok")
        check("PRIVATE_KEY" in detail and "RPC_URL" in detail, "check_required_keys: names both unresolved keys")

        env_set = tmp / "set.env"
        env_set.write_text("PRIVATE_KEY=0xabc123\nRPC_URL=https://example.com/rpc\n")
        ok, _ = ig.check_required_keys(env_set)
        check(ok is True, "check_required_keys: real values -> ok")

        # ── ALCHEMY_ARB_KEY satisfies the RPC requirement even with RPC_URL
        # left at .env.template's own default (the engine's actual RPC
        # priority order — see flash_loan_engine.py's _build_rpc_endpoints) ──
        env_alchemy = tmp / "alchemy.env"
        env_alchemy.write_text(
            "PRIVATE_KEY=0xabc123\n"
            "RPC_URL=https://arb-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY_HERE\n"
            "ALCHEMY_ARB_KEY=real-key-abc123\n"
        )
        ok, detail = ig.check_required_keys(env_alchemy)
        check(ok is True, "check_required_keys: ALCHEMY_ARB_KEY alone satisfies the RPC requirement")

        env_neither = tmp / "neither.env"
        env_neither.write_text(
            "PRIVATE_KEY=0xabc123\n"
            "RPC_URL=https://arb-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY_HERE\n"
        )
        ok, detail = ig.check_required_keys(env_neither)
        check(ok is False, "check_required_keys: template-default RPC_URL with no ALCHEMY_ARB_KEY -> not ok")
        check("RPC_URL" in detail, "check_required_keys: flags the missing RPC source")

        # ── check_rpc_endpoints: counts every real RPC source for failover ──
        ok, detail = ig.check_rpc_endpoints(env_neither)
        check(ok is True and "public node only" in detail,
              "check_rpc_endpoints: no real sources -> public-only, still ok (never fails)")
        env_multi = tmp / "multi.env"
        env_multi.write_text(
            "PRIVATE_KEY=0xabc123\n"
            "ALCHEMY_ARB_KEY=key-one\n"
            "ALCHEMY_KEY_2=key-two\n"
            "RPC_URL=https://my.rpc/one\n"
            "RPC_URL2=https://my.rpc/two\n"
            "RPC_FALLBACKS=https://fb.rpc/a,https://fb.rpc/b\n"
            "ALCHEMY_KEY_BAD=YOUR_ALCHEMY_KEY_HERE\n"   # placeholder must NOT count
        )
        ok, detail = ig.check_rpc_endpoints(env_multi)
        # 2 alchemy + 2 rpc urls + 2 fallbacks = 6 of yours, + public = 7
        check(ok is True and "7 endpoints" in detail and "6 of yours" in detail,
              "check_rpc_endpoints: counts multiple Alchemy keys + RPC URLs + fallbacks, skips placeholders")

        # ── _resolve_rpc_url: pure priority logic, no network ──
        check(ig._resolve_rpc_url({"ALCHEMY_ARB_KEY": "abc", "RPC_URL": "https://example.com"})
              == "https://arb-mainnet.g.alchemy.com/v2/abc",
              "_resolve_rpc_url: prefers ALCHEMY_ARB_KEY over RPC_URL, same as the engine")
        check(ig._resolve_rpc_url({"RPC_URL": "https://example.com/rpc"}) == "https://example.com/rpc",
              "_resolve_rpc_url: falls back to RPC_URL when no Alchemy key")
        check(ig._resolve_rpc_url({"RPC_URL": "https://x/v2/YOUR_ALCHEMY_KEY_HERE"}) is None,
              "_resolve_rpc_url: template-default RPC_URL with nothing else -> None")

        # ── check_contract_address ──
        env_zero = tmp / "zero.env"
        env_zero.write_text("FLASH_CONTRACT_ADDRESS=0x0000000000000000000000000000000000000000\n")
        ok, detail = ig.check_contract_address(env_zero)
        check(ok is False, "check_contract_address: zero address -> not ok (not deployed yet)")
        check("not deployed" in detail, "check_contract_address: zero-address message is the 'not deployed' hint")

        env_bad = tmp / "bad.env"
        env_bad.write_text("FLASH_CONTRACT_ADDRESS=not-an-address\n")
        ok, detail = ig.check_contract_address(env_bad)
        check(ok is False, "check_contract_address: malformed address -> not ok")
        check("malformed" in detail, "check_contract_address: flags malformed distinctly from not-deployed")

        env_good = tmp / "good.env"
        env_good.write_text("FLASH_CONTRACT_ADDRESS=0x1234567890123456789012345678901234567890\n")
        ok, detail = ig.check_contract_address(env_good)
        check(ok is True, "check_contract_address: well-formed address -> ok")
        check(detail == "0x1234567890123456789012345678901234567890", "check_contract_address: detail echoes the address")

        # ── check_rpc_reachable ──
        env_no_rpc = tmp / "no_rpc.env"
        env_no_rpc.write_text("RPC_URL=\n")
        ok, detail = ig.check_rpc_reachable(env_no_rpc)
        check(ok is False, "check_rpc_reachable: no RPC_URL -> not ok, no network call attempted")

        env_bad_rpc = tmp / "bad_rpc.env"
        env_bad_rpc.write_text("RPC_URL=http://127.0.0.1:1/\n")
        ok, detail = ig.check_rpc_reachable(env_bad_rpc, timeout=1.0)
        check(ok is False, "check_rpc_reachable: unreachable endpoint -> not ok, doesn't raise")

        # ── check_daemon_liveness: never raises, always returns a 2-tuple ──
        ok, detail = ig.check_daemon_liveness()
        check(isinstance(ok, bool) and isinstance(detail, str), "check_daemon_liveness: returns (bool, str)")

        # ── run_checks: shape is stable regardless of what's actually wired ──
        results = ig.run_checks()
        check(len(results) == len(ig.CHECKS), "run_checks: one result per registered check")
        check(all(isinstance(r, tuple) and len(r) == 3 for r in results), "run_checks: every result is a (label, ok, detail) triple")
        check(all(isinstance(r[1], bool) for r in results), "run_checks: every 'ok' is a real bool")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
