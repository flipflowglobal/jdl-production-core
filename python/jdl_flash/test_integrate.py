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
        )
        ok, detail = ig.check_rpc_endpoints(env_multi)
        # 2 alchemy + 2 rpc urls + 2 fallbacks = 6 of yours, + public = 7
        check(ok is True and "7 endpoints" in detail and "6 of yours" in detail,
              "check_rpc_endpoints: counts multiple Alchemy keys + RPC URLs + fallbacks")

        # ── matches the ENGINE exactly (shared build_rpc_endpoints), incl. its
        # quirks — two regression cases Copilot flagged: ──
        # (a) a placeholder RPC_URL shadows a later real ARB_RPC_URL, because the
        #     engine's _env picks the first NON-EMPTY alias before validating it. ──
        env_shadow = tmp / "shadow.env"
        env_shadow.write_text(
            "PRIVATE_KEY=0xabc\n"
            "RPC_URL=https://arb-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY_HERE\n"  # placeholder, non-empty
            "ARB_RPC_URL=https://real.rpc/x\n"                                       # ignored: RPC_URL won the group
        )
        ok, detail = ig.check_rpc_endpoints(env_shadow)
        check(ok is True and "public node only" in detail,
              "check_rpc_endpoints: placeholder RPC_URL shadows real ARB_RPC_URL (matches engine _env)")
        # (b) a placeholder ALCHEMY_KEY_* (YOUR_ALCHEMY_KEY_HERE) can only 401, so
        #     it is validity-filtered like an explicit URL and does NOT count as a
        #     real source — the redundancy report reflects usable endpoints only. ──
        env_alch_ph = tmp / "alch_ph.env"
        env_alch_ph.write_text(
            "PRIVATE_KEY=0xabc\n"
            "ALCHEMY_KEY_2=YOUR_ALCHEMY_KEY_HERE\n"   # placeholder -> filtered
        )
        ok, detail = ig.check_rpc_endpoints(env_alch_ph)
        check(ok is True and "public node only" in detail,
              "check_rpc_endpoints: placeholder ALCHEMY_KEY_* is filtered (not counted)")

        # (c) placeholder detection is case-INSENSITIVE (like is_placeholder), so a
        #     lower-case template value is filtered, not counted as usable. ──
        env_lower = tmp / "lower.env"
        env_lower.write_text(
            "PRIVATE_KEY=0xabc\n"
            "RPC_URL=https://arb-mainnet.g.alchemy.com/v2/your_alchemy_key_here\n"  # lower-case placeholder
        )
        ok, detail = ig.check_rpc_endpoints(env_lower)
        check(ok is True and "public node only" in detail,
              "check_rpc_endpoints: lower-case placeholder is filtered (case-insensitive)")
        ok, _ = ig.check_required_keys(env_lower)
        check(ok is False, "check_required_keys: lower-case placeholder RPC_URL is not a real source")

        # ── de-dup: duplicate URLs and the public fallback must NOT overstate
        # redundancy (mirrors the engine's own de-duplication) ──
        env_dup = tmp / "dup.env"
        env_dup.write_text(
            "PRIVATE_KEY=0xabc\n"
            "RPC_URL=https://dup.rpc/x\n"
            "RPC_URL2=https://dup.rpc/x\n"                 # exact duplicate of RPC_URL
            "RPC_URL3=https://arb1.arbitrum.io/rpc\n"      # equals the public fallback
        )
        ok, detail = ig.check_rpc_endpoints(env_dup)
        check(ok is True and "2 endpoints" in detail and "1 of yours" in detail,
              "check_rpc_endpoints: de-dupes duplicate URLs and the public node (no overstated redundancy)")

        # ── the public node is always LAST, even if a user configures it as a
        # numbered slot ahead of a private fallback ──
        from jdl_flash.rpc_endpoints import build_rpc_endpoints as _bre, PUBLIC_ARB_RPC as _PUB
        eps = _bre({"RPC_URL2": _PUB, "RPC_URL3": "https://private.fallback/x"})
        check(eps[-1] == _PUB and eps.index("https://private.fallback/x") < len(eps) - 1,
              "build_rpc_endpoints: public node forced last even when set as RPC_URLn (private fallback runs first)")

        # ── alias precedence: RPC_URL wins its group; ARB_RPC_URL alias is not
        # counted as a second endpoint (matches the engine's _env) ──
        env_alias = tmp / "alias.env"
        env_alias.write_text(
            "PRIVATE_KEY=0xabc\n"
            "RPC_URL=https://primary.rpc/a\n"
            "ARB_RPC_URL=https://ignored-alias.rpc/b\n"
        )
        ok, detail = ig.check_rpc_endpoints(env_alias)
        check(ok is True and "2 endpoints" in detail and "1 of yours" in detail,
              "check_rpc_endpoints: alias group counts once (ARB_RPC_URL not double-counted)")

        # ── check_required_keys: a SECONDARY-only source satisfies the RPC
        # requirement (RPC_URL2-only / ALCHEMY_KEY_2-only), matching the engine ──
        env_secondary = tmp / "secondary.env"
        env_secondary.write_text("PRIVATE_KEY=0xabc\nRPC_URL2=https://my.secondary/rpc\n")
        ok, _ = ig.check_required_keys(env_secondary)
        check(ok is True, "check_required_keys: RPC_URL2-only satisfies the RPC requirement")
        env_alch2 = tmp / "alch2.env"
        env_alch2.write_text("PRIVATE_KEY=0xabc\nALCHEMY_KEY_2=realkey\n")
        ok, _ = ig.check_required_keys(env_alch2)
        check(ok is True, "check_required_keys: ALCHEMY_KEY_2-only satisfies the RPC requirement")

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

        # ── check_rpc_reachable: probes the engine's endpoint list in order and
        # succeeds on the first Arbitrum responder (get_w3 failover). Probe is
        # monkeypatched so these are deterministic and never hit the network. ──
        real_probe = ig._probe_chain_id
        try:
            env_two = tmp / "two.env"          # -> [user rpc, public]
            env_two.write_text("PRIVATE_KEY=0xabc\nRPC_URL=https://user.rpc/x\n")

            # primary healthy -> ok via endpoint #1 (public never probed)
            ig._probe_chain_id = lambda url, timeout: 42161
            ok, detail = ig.check_rpc_reachable(env_two)
            check(ok is True and "endpoint #1" in detail, "check_rpc_reachable: healthy primary -> ok")

            # dead primary, healthy public fallback -> engine succeeds, so do we
            def _primary_dead(url, timeout):
                if url == "https://user.rpc/x":
                    raise OSError("connection refused")
                return 42161
            ig._probe_chain_id = _primary_dead
            ok, detail = ig.check_rpc_reachable(env_two)
            check(ok is True and "public fallback" in detail,
                  "check_rpc_reachable: dead primary + healthy fallback -> reachable (matches engine)")

            # everything dead -> not ok, doesn't raise
            def _all_dead(url, timeout):
                raise OSError("connection refused")
            ig._probe_chain_id = _all_dead
            ok, detail = ig.check_rpc_reachable(env_two)
            check(ok is False and "no endpoint reachable" in detail,
                  "check_rpc_reachable: all endpoints dead -> not ok")

            # CHAIN_ID drives the expected chain: a Sepolia endpoint on a Sepolia
            # config is reachable (not "wrong chain"), and a mainnet endpoint is not
            env_sep = tmp / "sepolia.env"
            env_sep.write_text("PRIVATE_KEY=0xabc\nRPC_URL=https://sep.rpc/x\nCHAIN_ID=421614\n")
            ig._probe_chain_id = lambda url, timeout: 421614
            ok, detail = ig.check_rpc_reachable(env_sep)
            check(ok is True and "endpoint #1" in detail,
                  "check_rpc_reachable: Sepolia endpoint on CHAIN_ID=421614 -> reachable (not wrong-chain)")
            ig._probe_chain_id = lambda url, timeout: 42161
            ok, detail = ig.check_rpc_reachable(env_sep)
            check(ok is False and "wrong chain" in detail,
                  "check_rpc_reachable: mainnet chain on a Sepolia config -> wrong chain")
        finally:
            ig._probe_chain_id = real_probe

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
