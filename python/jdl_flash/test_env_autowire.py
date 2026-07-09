"""
Tests for env_autowire.py — the zero-prompt .env scraper/wirer. Everything
runs against a throwaway tmp directory tree standing in for "the whole
machine"; no real ~/jdl/.env or $HOME is ever touched.

Run: cd python && python3 jdl_flash/test_env_autowire.py
"""
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jdl_flash.env_autowire as ew


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

    # ── is_placeholder ──
    for v in ["", "0x", "0x0000000000000000000000000000000000000000", "<set-via-secrets-manager>",
              "YOUR_ALCHEMY_KEY_HERE", "CHANGE_ME", "changeme", "xxx", "TODO",
              # the *actual* .env.template default — marker is mid-string, not at
              # position 0, and must still be recognized (regression: an anchored
              # `.match()` over an unanchored alternative misses this entirely)
              "https://arb-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY_HERE"]:
        check(ew.is_placeholder(v), f"recognizes placeholder: {v!r}")
    for v in ["0xabc123", "https://example.com", "42161", "a-real-api-key-1234"]:
        check(not ew.is_placeholder(v), f"does not flag real value as placeholder: {v!r}")

    # ── parse_env_file ──
    tmp = Path(tempfile.mkdtemp(prefix="jdl_env_autowire_test_"))
    try:
        f1 = tmp / ".env"
        f1.write_text("# comment\nFOO=bar\nQUOTED=\"hello world\"\nexport BAZ=1\n\nBAD LINE\n")
        parsed = ew.parse_env_file(f1)
        check(parsed.get("FOO") == "bar", "parses plain KEY=value")
        check(parsed.get("QUOTED") == "hello world", "strips surrounding quotes")
        check(parsed.get("BAZ") == "1", "parses `export KEY=value` lines")
        check("BAD" not in parsed, "ignores lines that aren't KEY=value")

        # ── find_env_files: discovers nested .env* files, skips noise dirs ──
        (tmp / "proj_a").mkdir()
        (tmp / "proj_a" / ".env").write_text("PRIVATE_KEY=0xdeadbeef\n")
        (tmp / "proj_b" / "node_modules" / "pkg").mkdir(parents=True)
        (tmp / "proj_b" / "node_modules" / "pkg" / ".env").write_text("PRIVATE_KEY=should_not_be_found\n")
        (tmp / "proj_b" / ".env.local").write_text("ALCHEMY_ARB_KEY=abc123\n")
        (tmp / "proj_b" / ".git" / "hooks").mkdir(parents=True)
        (tmp / "proj_b" / ".git" / "hooks" / ".env").write_text("PRIVATE_KEY=should_also_not_be_found\n")

        found = ew.find_env_files([tmp])
        found_names = {str(p.relative_to(tmp)) for p in found}
        check(str(Path("proj_a") / ".env") in found_names, "finds .env in a nested project dir")
        check(str(Path("proj_b") / ".env.local") in found_names, "finds .env.local (dotted variant)")
        check(not any("node_modules" in n for n in found_names), "skips node_modules")
        check(not any(".git" in n for n in found_names), "skips .git")

        # ── find_env_files: bounded depth — a fresh install must not crawl an
        # entire unbounded $HOME before wiring a single value ──
        deep = tmp / "proj_c"
        for i in range(10):
            deep = deep / f"level{i}"
        deep.mkdir(parents=True)
        (deep / ".env").write_text("PRIVATE_KEY=too_deep_to_matter\n")
        (tmp / "proj_c" / "level0" / ".env").write_text("PRIVATE_KEY=shallow_and_findable\n")

        found_bounded = ew.find_env_files([tmp], max_depth=3)
        bounded_names = {str(p.relative_to(tmp)) for p in found_bounded}
        check(str(Path("proj_c") / "level0" / ".env") in bounded_names,
              "find_env_files: still finds a shallow .env within max_depth")
        check(not any("level9" in n for n in bounded_names),
              "find_env_files: does not descend past max_depth")

        found_unbounded = ew.find_env_files([tmp], max_depth=100)
        unbounded_names = {str(p.relative_to(tmp)) for p in found_unbounded}
        check(any("level9" in n for n in unbounded_names),
              "find_env_files: a larger max_depth does reach the deep file (sanity check on the bound itself)")

        # ── autowire: end-to-end fill from scattered files, no duplication ──
        target_dir = tmp / "canonical"
        target = target_dir / ".env"
        template = tmp / ".env.template"
        template.write_text(
            "PRIVATE_KEY=<set-via-secrets-manager>\n"
            "ALCHEMY_ARB_KEY=\n"
            "FLASH_CONTRACT_ADDRESS=0x0000000000000000000000000000000000000000\n"
            "CHAIN_ID=42161\n"
        )

        report = ew.autowire(target=target, template=template, search_roots=[tmp], quiet=True)
        check(target.is_file(), "creates the canonical target file if missing")
        check("PRIVATE_KEY" in report["filled"], "fills PRIVATE_KEY from a scattered .env")
        check("ALCHEMY_ARB_KEY" in report["filled"], "fills ALCHEMY_ARB_KEY from a scattered .env.local")
        check("FLASH_CONTRACT_ADDRESS" in report["unresolved"],
              "leaves a key unresolved when no real value exists anywhere")

        final = ew.parse_env_file(target)
        check(final["PRIVATE_KEY"] == "0xdeadbeef", "target now holds the real PRIVATE_KEY value")
        check(final["ALCHEMY_ARB_KEY"] == "abc123", "target now holds the real ALCHEMY_ARB_KEY value")
        check(final["CHAIN_ID"] == "42161", "untouched non-placeholder key is left exactly as-is")

        text_lines = target.read_text().splitlines()
        check(sum(1 for l in text_lines if l.startswith("PRIVATE_KEY=")) == 1,
              "no duplicate PRIVATE_KEY= line after autowiring")

        # ── re-running autowire on an already-filled file is a no-op ──
        report2 = ew.autowire(target=target, template=template, search_roots=[tmp], quiet=True)
        check(report2["filled"] == {}, "second run finds nothing left to fill for already-resolved keys")
        check(report2["unresolved"] == ["FLASH_CONTRACT_ADDRESS"], "still reports the one genuinely-missing key")

        # ── never invents a value: a key present nowhere never appears ──
        check(all(v != "" for v in final.values()), "no key was ever filled with an empty string")

        # ── set_values: persist explicit user input in place (interactive entry) ──
        sv_target = tmp / "canonical_sv" / ".env"
        written = ew.set_values(
            {"PRIVATE_KEY": "0xfeed", "ALCHEMY_ARB_KEY": "realkey",
             "FLASH_CONTRACT_ADDRESS": "", "CHAIN_ID": "<placeholder>"},
            target=sv_target, template=template,
        )
        check(set(written) == {"PRIVATE_KEY", "ALCHEMY_ARB_KEY"},
              "set_values writes only real inputs, skipping blank/placeholder")
        sv = ew.parse_env_file(sv_target)
        check(sv.get("PRIVATE_KEY") == "0xfeed" and sv.get("ALCHEMY_ARB_KEY") == "realkey",
              "set_values persists the entered values")
        check(sum(1 for l in sv_target.read_text().splitlines() if l.startswith("PRIVATE_KEY=")) == 1,
              "set_values does not duplicate an existing key line")
        if os.name == "posix":
            check(stat.S_IMODE(os.stat(sv_target).st_mode) == 0o600,
                  "set_values enforces 0600 perms on the env file")
        # a blank answer must never clobber an already-real value
        ew.set_values({"PRIVATE_KEY": ""}, target=sv_target, template=template)
        check(ew.parse_env_file(sv_target).get("PRIVATE_KEY") == "0xfeed",
              "set_values: a blank answer leaves the existing value intact")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
