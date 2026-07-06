"""
Tests for cli.py — the `jdl` dispatcher. Covers argument parsing/routing only
(each subcommand's own function is tested elsewhere, e.g. resolve_target() in
test_flash_supervisor.py, swarm_daemon.run_forever() in test_swarm_daemon.py);
here we just confirm `jdl <subcommand> ...` reaches the right handler with the
right args, without touching the network or spawning real subprocesses.

Run: cd python && python3 jdl_flash/test_cli.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jdl_flash.cli as cli


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

    parser = cli.build_parser()

    # ── routing: every subcommand resolves to its documented handler ──
    routing = {
        "run": cli.cmd_run,
        "pro": cli.cmd_pro,
        "swarm": cli.cmd_swarm,
        "status": cli.cmd_status,
        "test": cli.cmd_test,
        "install-swarm-boot": cli.cmd_install,
    }
    for name, expected_func in routing.items():
        args = parser.parse_args([name])
        check(args.func is expected_func, f"'{name}' routes to {expected_func.__name__}")

    args = parser.parse_args(["supervisor", "swarm"])
    check(args.func is cli.cmd_supervisor and args.target == "swarm",
          "'supervisor swarm' routes to cmd_supervisor with target='swarm'")
    args = parser.parse_args(["supervisor"])
    check(args.target is None, "'supervisor' with no target leaves target=None (resolve_target's own default applies)")

    args = parser.parse_args(["deploy", "receiver"])
    check(args.func is cli.cmd_deploy and args.target == "receiver", "'deploy receiver' routes correctly")
    args = parser.parse_args(["deploy", "gelato"])
    check(args.target == "gelato", "'deploy gelato' routes correctly")

    try:
        parser.parse_args(["deploy", "not-a-real-target"])
        check(False, "'deploy' rejects an unknown target")
    except SystemExit:
        check(True, "'deploy' rejects an unknown target")

    try:
        parser.parse_args([])
        check(False, "no subcommand at all is rejected (required=True)")
    except SystemExit:
        check(True, "no subcommand at all is rejected (required=True)")

    args = parser.parse_args(["test", "--filter", "swarm"])
    check(args.filter == "swarm", "'test --filter' is threaded through")
    args = parser.parse_args(["test"])
    check(args.filter is None, "'test' with no --filter defaults to None (run everything)")

    # ── _PACKAGED_TESTS stays in sync with what actually exists on disk ──
    python_dir = cli._python_dir()
    for name in cli._PACKAGED_TESTS:
        check((python_dir / "jdl_flash" / name).is_file(), f"listed suite exists on disk: jdl_flash/{name}")
    check((python_dir / "test_flash_supervisor.py").is_file(), "test_flash_supervisor.py exists at python/ root")
    check((python_dir / "jdl_native" / "test_jdl_native.py").is_file(), "jdl_native/test_jdl_native.py exists")

    # ── entry point actually installed and dispatches (real subprocess, --help only) ──
    result = subprocess.run(["jdl", "--help"], capture_output=True, text=True)
    check(result.returncode == 0, "installed `jdl` console-script runs")
    check("install-swarm-boot" in result.stdout, "`jdl --help` lists every subcommand")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
