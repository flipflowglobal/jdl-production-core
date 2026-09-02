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
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jdl_flash.cli as cli


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


class FakeRunner:
    """Stands in for cli._run_subprocess. `outcomes` maps a keyword found in the
    command (e.g. "rev-parse", "status", "pull", "pip") to a FakeCompletedProcess;
    unmatched commands default to success. Records every call for assertions."""

    def __init__(self, outcomes=None):
        self.outcomes = outcomes or {}
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        for keyword, result in self.outcomes.items():
            if any(keyword in str(part) for part in cmd):
                return result
        return FakeCompletedProcess(returncode=0)


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
        "install": cli.cmd_setup,
        "update": cli.cmd_update,
        "integrate": cli.cmd_integrate,
    }
    for name, expected_func in routing.items():
        args = parser.parse_args([name])
        check(args.func is expected_func, f"'{name}' routes to {expected_func.__name__}")

    args = parser.parse_args(["supervisor", "swarm"])
    check(args.func is cli.cmd_supervisor and args.target == "swarm",
          "'supervisor swarm' routes to cmd_supervisor with target='swarm'")
    args = parser.parse_args(["supervisor"])
    check(args.target is None, "'supervisor' with no target leaves target=None (resolve_target's own default applies)")

    # ── plain-English multi-word phrasing ──
    args = parser.parse_args(["start", "flashloan"])
    check(args.func is cli.cmd_start and args.target == "flashloan", "'start flashloan' routes to cmd_start")
    args = parser.parse_args(["start"])
    check(args.target == "flashloan", "'start' with no target defaults to 'flashloan'")

    args = parser.parse_args(["test", "system"])
    check(args.func is cli.cmd_test and args.scope == "system", "'test system' routes to cmd_test with scope='system'")
    args = parser.parse_args(["test"])
    check(args.scope is None, "'test' with no scope leaves scope=None (plain suite run)")

    args = parser.parse_args(["show", "flashloans"])
    check(args.func is cli.cmd_show and args.target == "flashloans", "'show flashloans' routes to cmd_show")
    args = parser.parse_args(["show"])
    check(args.target == "flashloans", "'show' with no target defaults to 'flashloans'")
    check(args.interval == 5.0 and args.once is False, "'show' defaults: interval=5.0, once=False")

    args = parser.parse_args(["integrate", "--watch", "--interval", "3"])
    check(args.watch is True and args.interval == 3.0, "'integrate --watch --interval 3' threads both through")
    args = parser.parse_args(["integrate"])
    check(args.watch is False, "'integrate' defaults --watch to False")

    # ── cmd_start actually dispatches to cmd_run ──
    real_cmd_run = cli.cmd_run
    try:
        calls = []
        cli.cmd_run = lambda a: calls.append(a) or 0
        rc = cli.cmd_start(SimpleNamespace(target="flashloan"))
        check(rc == 0 and len(calls) == 1, "cmd_start('flashloan') calls cmd_run exactly once")
    finally:
        cli.cmd_run = real_cmd_run

    args = parser.parse_args(["deploy", "receiver"])
    check(args.func is cli.cmd_deploy and args.target == "receiver", "'deploy receiver' routes correctly")
    args = parser.parse_args(["deploy", "gelato"])
    check(args.target == "gelato", "'deploy gelato' routes correctly")
    args = parser.parse_args(["deploy", "mock-router"])
    check(args.target == "mock-router", "'deploy mock-router' routes correctly")

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

    args = parser.parse_args(["update"])
    check(args.force is False, "'update' defaults --force to False")
    args = parser.parse_args(["update", "--force"])
    check(args.force is True, "'update --force' sets force=True")

    # ── cmd_update behavior, fully offline (fake git/pip subprocess calls) ──
    real_runner = cli._run_subprocess
    try:
        cli._run_subprocess = FakeRunner({"rev-parse": FakeCompletedProcess(returncode=1)})
        rc = cli.cmd_update(SimpleNamespace(force=False))
        check(rc == 1, "not a git checkout -> cmd_update returns 1")

        fake = FakeRunner({"status": FakeCompletedProcess(returncode=0, stdout=" M some_file.py\n")})
        cli._run_subprocess = fake
        rc = cli.cmd_update(SimpleNamespace(force=False))
        check(rc == 1, "dirty working tree without --force -> refuses to update")
        check(not any("pull" in str(c) for call in fake.calls for c in call), "dirty tree -> git pull never attempted")

        fake = FakeRunner({"status": FakeCompletedProcess(returncode=0, stdout=" M some_file.py\n")})
        cli._run_subprocess = fake
        rc = cli.cmd_update(SimpleNamespace(force=True))
        check(rc == 0, "dirty working tree WITH --force -> proceeds")
        check(any("pull" in str(c) for call in fake.calls for c in call), "--force -> git pull is attempted")

        fake = FakeRunner({"pull": FakeCompletedProcess(returncode=1)})
        cli._run_subprocess = fake
        rc = cli.cmd_update(SimpleNamespace(force=False))
        check(rc == 1, "git pull failure -> cmd_update returns 1, stops there")
        check(not any("pip" in str(c) for call in fake.calls for c in call), "pull failed -> pip install never attempted")

        fake = FakeRunner({"pip": FakeCompletedProcess(returncode=1)})
        cli._run_subprocess = fake
        rc = cli.cmd_update(SimpleNamespace(force=False))
        check(rc == 1, "pip install failure -> cmd_update returns 1")

        fake = FakeRunner()
        cli._run_subprocess = fake
        rc = cli.cmd_update(SimpleNamespace(force=False))
        check(rc == 0, "clean tree, everything succeeds -> cmd_update returns 0")
        check(any("git" in str(c) for call in fake.calls for c in call), "git was invoked")
        check(any("pip" in str(c) for call in fake.calls for c in call), "pip was invoked")
    finally:
        cli._run_subprocess = real_runner

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
    for name in ("install", "start", "show", "integrate"):
        check(name in result.stdout, f"`jdl --help` lists the '{name}' subcommand")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
