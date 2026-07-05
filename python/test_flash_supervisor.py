"""
Tests for flash_supervisor.py's target-resolution logic (resolve_target), which
picks which module `python -m` supervises: the legacy single-process engine
('engine', the default/unchanged behavior) or the always-on swarm daemon ('swarm').

Run: cd python && python3 test_flash_supervisor.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import flash_supervisor as fs


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

    os.environ.pop("SUPERVISOR_TARGET", None)

    check(fs.resolve_target(None) == "jdl_flash.flash_loan_engine",
          "no spec, no env var -> defaults to the legacy engine (unchanged behavior)")
    check(fs.resolve_target("engine") == "jdl_flash.flash_loan_engine",
          "'engine' resolves to the legacy engine module")
    check(fs.resolve_target("swarm") == "jdl_flash.swarm_daemon",
          "'swarm' resolves to the always-on swarm daemon module")
    check(fs.resolve_target("some.custom.module") == "some.custom.module",
          "a raw dotted module path passes through unchanged")
    check(fs.resolve_target("garbage") == "jdl_flash.flash_loan_engine",
          "an unrecognized, non-dotted spec falls back to the legacy engine, not a crash")

    os.environ["SUPERVISOR_TARGET"] = "swarm"
    check(fs.resolve_target(None) == "jdl_flash.swarm_daemon",
          "SUPERVISOR_TARGET env var is honored when no explicit spec is passed")
    check(fs.resolve_target("engine") == "jdl_flash.flash_loan_engine",
          "an explicit spec overrides the SUPERVISOR_TARGET env var")
    os.environ.pop("SUPERVISOR_TARGET", None)

    check(fs.TARGETS == {"engine": "jdl_flash.flash_loan_engine", "swarm": "jdl_flash.swarm_daemon"},
          "TARGETS maps exactly the two supported keys")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
