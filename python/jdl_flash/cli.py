"""
cli.py — single `jdl` entry point for every command in this system.

Before this, each piece had its own separate invocation style: `flashloan` /
`flashpro` (installed console-scripts), `python3 flash_supervisor.py [target]`,
`python3 -m jdl_flash.swarm_daemon`, `python3 jdl_flash/deploy_receiver.py`, and
five different `python3 jdl_flash/test_*.py` files run one at a time. `jdl
--help` now lists all of it as subcommands of one command; nothing here
duplicates logic — every subcommand is a thin call into the existing function
or module that already did the work.

flash_supervisor.py lives at the repo's python/ root, not inside this package
(deliberately unpackaged — see pyproject.toml's comment on why), so it can't be
`import`ed the normal way from here; `_load_flash_supervisor()` loads it by
file path instead, same module, no copy-paste.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

# All test files this system has, in the same order/grouping as ci.yml — `jdl
# test` and CI should never drift out of sync with each other.
_PACKAGED_TESTS = [
    "test_flash_engine.py",
    "test_swarm_runtime.py",
    "test_bot_swarm.py",
    "test_wallet_lanes.py",
    "test_swarm_wiring.py",
    "test_swarm_daemon.py",
    "test_config_validation.py",
    "test_revenue_reconciliation.py",
    "test_env_autowire.py",
    "test_platform_detect.py",
    "test_integrate.py",
    "test_cli.py",
]


def _python_dir() -> Path:
    """The repo's python/ directory (this package's parent)."""
    from jdl_flash._paths import python_dir

    return python_dir()


def _load_flash_supervisor() -> ModuleType:
    from jdl_flash._paths import load_flash_supervisor

    return load_flash_supervisor()


def cmd_run(_args: argparse.Namespace) -> int:
    from jdl_flash.flash_loan_engine import _run

    _run()
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    """`jdl start flashloan` — plain-English alias for `jdl run` (the
    interactive engine's terminal dashboard)."""
    if args.target == "flashloan":
        return cmd_run(args)
    raise ValueError(f"unknown start target: {args.target!r}")  # unreachable — argparse enforces choices


def cmd_pro(_args: argparse.Namespace) -> int:
    from jdl_flash.flash_pro import main as flash_pro_main

    flash_pro_main()
    return 0


def cmd_swarm(_args: argparse.Namespace) -> int:
    from jdl_flash.swarm_daemon import main as swarm_daemon_main

    return swarm_daemon_main()


def cmd_supervisor(args: argparse.Namespace) -> int:
    fs = _load_flash_supervisor()
    fs.FlashSupervisor(script=fs.resolve_target(args.target)).run()
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    if args.target == "receiver":
        from jdl_flash.deploy_receiver import main as deploy_main
    else:
        from jdl_flash.deploy_gelato import main as deploy_main
    deploy_main()
    return 0


# Module-level so tests can monkeypatch it to a fake instead of touching real
# git/pip/network — every cmd_update step goes through this one name.
_run_subprocess = subprocess.run


def cmd_update(args: argparse.Namespace) -> int:
    """git pull + reinstall, so every installed command (flashloan/flashpro/jdl,
    and the jdl_native fast path if buildable) is brought up to date in one call
    instead of four separate manual steps."""
    repo_dir = _python_dir().parent

    if _run_subprocess(["git", "-C", str(repo_dir), "rev-parse", "--is-inside-work-tree"],
                        capture_output=True, text=True).returncode != 0:
        print("  ✗ not a git checkout — clone the repo with git to use `jdl update`.")
        return 1

    status = _run_subprocess(["git", "-C", str(repo_dir), "status", "--porcelain"],
                              capture_output=True, text=True)
    if status.stdout.strip() and not args.force:
        print("  ✗ local changes detected — refusing to `git pull` over them:\n")
        print(status.stdout)
        print("    Commit or stash your changes first, or re-run with --force to pull anyway.")
        return 1

    print("  ▶ git pull --ff-only …")
    if _run_subprocess(["git", "-C", str(repo_dir), "pull", "--ff-only"]).returncode != 0:
        print("  ✗ git pull failed — resolve manually (see above).")
        return 1

    print("  ▶ reinstalling jdl_flash (updates flashloan/flashpro/jdl on PATH) …")
    if _run_subprocess([sys.executable, "-m", "pip", "install", "-e", str(_python_dir())]).returncode != 0:
        print("  ✗ pip install -e failed — see above.")
        return 1

    native_setup = _python_dir() / "jdl_native" / "setup.py"
    if native_setup.is_file():
        print("  ▶ rebuilding jdl_native's Cython extension (best-effort — the ctypes/")
        print("    subprocess/pure-python fallbacks keep working either way, see POLYGLOT.md) …")
        _run_subprocess([sys.executable, "setup.py", "build_ext", "--inplace"], cwd=str(native_setup.parent))

    print("  ✓ update complete — run `jdl test` to confirm everything still passes.")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    # The Termux:Boot hook is an OS-level install step (symlinks, permission
    # checks) that setup.sh already does correctly per-platform — call straight
    # through rather than re-implementing any of that here.
    setup_sh = _python_dir().parent / "setup.sh"
    return subprocess.run(["bash", str(setup_sh), "swarm-boot"]).returncode


def cmd_setup(_args: argparse.Namespace) -> int:
    """`jdl install` — the plain-English one-shot setup: detect the platform
    (Termux/UserLAnd/WSL/Ubuntu/macOS/native Windows), run the matching
    dependency installer (setup.sh's Python/Node/Foundry/Rust steps on every
    POSIX platform, scripts/setup.ps1 on native Windows), then auto-wire
    ~/jdl/.env from every .env file reachable on this machine — no directory
    names needed, no manual copy-paste."""
    from jdl_flash.platform_detect import detect_platform, is_posix_installer

    plat = detect_platform()
    print(f"  Platform detected: {plat}")
    repo_root = _python_dir().parent

    if is_posix_installer(plat):
        rc = subprocess.run(["bash", str(repo_root / "setup.sh")]).returncode
    else:
        rc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(repo_root / "scripts" / "setup.ps1")]
        ).returncode

    if rc != 0:
        print("  ✗ dependency install failed — see output above.")
        return rc

    print("\n  ▶ wiring .env from every .env file reachable on this machine …")
    from jdl_flash.env_autowire import autowire

    report = autowire()
    if report["unresolved"]:
        print(f"\n  {len(report['unresolved'])} value(s) still need a human: {', '.join(report['unresolved'])}")
        print("  (nobody on this machine has ever set them — add them to ~/jdl/.env by hand)")
    else:
        print("\n  ✓ .env fully wired — no manual edits needed.")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """`jdl show flashloans` — live activity: a status line on a loop plus a
    tail of the supervisor's daemon.log if one exists. Reads state only
    (never starts anything), so it's safe to run from a second shell/screen
    alongside `jdl supervisor` or `jdl run`."""
    fs = _load_flash_supervisor()
    log_file = fs.LOG_FILE
    print(f"  Watching {fs.DATA_DIR}  (Ctrl+C to stop)\n")
    pos = log_file.stat().st_size if log_file.exists() else 0

    def _tick() -> None:
        nonlocal pos
        tot = fs.total_profit()
        ex = fs.exec_count()
        alive = fs.PID_FILE.exists()
        pid = fs.PID_FILE.read_text().strip() if alive else None
        state = f"RUNNING (pid {pid})" if alive else "not running"
        print(f"  [{time.strftime('%H:%M:%S')}] daemon={state}  execs={ex}  revenue=${tot:,.2f}")
        if log_file.exists():
            with log_file.open() as f:
                f.seek(pos)
                new = f.read()
                pos = f.tell()
            for line in new.splitlines():
                print(f"    {line}")

    if args.once:
        _tick()
        return 0

    try:
        while True:
            _tick()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n  stopped.")
    return 0


def cmd_integrate(args: argparse.Namespace) -> int:
    """`jdl integrate` — verifies the wiring between every system function
    (.env, wallet/RPC reachability, deployed contract, supervised daemon) and
    prints one line per link so a broken connection is obvious at a glance.
    --watch re-checks on a loop, so it can be left running in a second
    shell/screen."""
    from jdl_flash.integrate import run_checks

    def _once() -> bool:
        results = run_checks()
        for label, ok, detail in results:
            mark = "✓" if ok else "✗"
            print(f"  {mark} {label}: {detail}")
        return all(ok for _label, ok, _detail in results)

    if not args.watch:
        return 0 if _once() else 1

    try:
        while True:
            print(f"\n{'=' * 60}\n  {time.strftime('%H:%M:%S')}\n{'=' * 60}")
            _once()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n  stopped.")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    fs = _load_flash_supervisor()
    tot = fs.total_profit()
    ex = fs.exec_count()
    alive = fs.PID_FILE.exists()
    pid = fs.PID_FILE.read_text().strip() if alive else None
    print(f"  Supervised daemon: {'RUNNING (pid ' + pid + ')' if alive else 'not running'}")
    print(f"  Executions:        {ex}")
    print(f"  Revenue:           ${tot:,.2f} / ${fs.THRESHOLD:,.0f} withdrawal threshold")
    print(f"  Data dir:          {fs.DATA_DIR}")
    return 0


def _resolve_test_suites(filter_str: str | None) -> tuple[Path, list[Path]]:
    python_dir = _python_dir()
    suites = [python_dir / "jdl_flash" / name for name in _PACKAGED_TESTS]
    suites.append(python_dir / "test_flash_supervisor.py")
    suites.append(python_dir / "jdl_native" / "test_jdl_native.py")
    if filter_str:
        suites = [s for s in suites if filter_str in s.name]
    return python_dir, suites


def _run_test_suites(python_dir: Path, suites: list[Path]) -> list[Path]:
    """Runs each suite, prints its header, returns the ones that failed."""
    failed = []
    for suite in suites:
        print(f"\n{'=' * 60}\n  {suite.relative_to(python_dir)}\n{'=' * 60}")
        rc = subprocess.run([sys.executable, str(suite)], cwd=python_dir).returncode
        if rc != 0:
            failed.append(suite)
    return failed


def cmd_test(args: argparse.Namespace) -> int:
    python_dir, suites = _resolve_test_suites(args.filter)
    failed = _run_test_suites(python_dir, suites)

    # `jdl test system` also probes every connection: if a suite failed, that
    # may just be an unwired .env (missing key, stale value) rather than a
    # real regression — auto-wire from every .env file on the machine and
    # give the failed suites one more chance before reporting red.
    if failed and getattr(args, "scope", None) == "system":
        print(f"\n{'=' * 60}\n  {len(failed)} suite(s) failed — checking for broken/missing .env "
              f"wiring …\n{'=' * 60}")
        from jdl_flash.env_autowire import autowire

        report = autowire()
        if report["filled"]:
            print(f"\n  ▶ re-running the {len(failed)} suite(s) that failed, now that "
                  f"{len(report['filled'])} more value(s) are wired …")
            failed = _run_test_suites(python_dir, failed)
        elif report["unresolved"]:
            print(f"\n  no new values found anywhere on this machine for: {', '.join(report['unresolved'])}")

    print(f"\n{'=' * 60}")
    if failed:
        names = [str(s.relative_to(python_dir)) for s in failed]
        print(f"  FAILED: {len(failed)}/{len(suites)} suite(s) — {', '.join(names)}")
        return 1
    print(f"  All {len(suites)} suites passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jdl", description="JDL flash-loan arbitrage engine — one CLI for every command."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="launch the interactive engine (same as `flashloan`)").set_defaults(func=cmd_run)
    sub.add_parser("pro", help="launch the advanced 8-module integrator (same as `flashpro`)").set_defaults(
        func=cmd_pro
    )
    sub.add_parser(
        "swarm", help="run the always-on parallel scanner in the foreground (unattended, no menu)"
    ).set_defaults(func=cmd_swarm)

    p_start = sub.add_parser("start", help="plain-English: 'jdl start flashloan' launches the terminal dashboard")
    p_start.add_argument("target", nargs="?", default="flashloan", choices=["flashloan"])
    p_start.set_defaults(func=cmd_start)

    p_super = sub.add_parser("supervisor", help="run a target under the auto-restart supervisor")
    p_super.add_argument(
        "target", nargs="?", default=None, choices=["engine", "swarm"],
        help="what to supervise (default: engine, or $SUPERVISOR_TARGET)",
    )
    p_super.set_defaults(func=cmd_supervisor)

    p_deploy = sub.add_parser("deploy", help="deploy a contract")
    p_deploy.add_argument("target", choices=["receiver", "gelato"], help="which deploy script to run")
    p_deploy.set_defaults(func=cmd_deploy)

    sub.add_parser("status", help="one-shot snapshot: daemon liveness, execution count, revenue").set_defaults(
        func=cmd_status
    )

    sub.add_parser(
        "install-swarm-boot", help="install the always-on scanner's boot hook (Termux:Boot, or nohup/systemd steps elsewhere)"
    ).set_defaults(func=cmd_install)

    sub.add_parser(
        "install",
        help="plain-English: detect the platform, install every dependency "
             "(python/node/hardhat/foundry/rust), auto-wire .env",
    ).set_defaults(func=cmd_setup)

    p_update = sub.add_parser("update", help="git pull + reinstall — brings every jdl/flashloan/flashpro command up to date")
    p_update.add_argument("--force", action="store_true", help="pull even if the working tree has local changes")
    p_update.set_defaults(func=cmd_update)

    p_test = sub.add_parser("test", help="run the full test suite (same suites CI runs)")
    p_test.add_argument(
        "scope", nargs="?", default=None, choices=["system"],
        help="'system' also auto-wires .env and retries any suite that failed because of it",
    )
    p_test.add_argument("--filter", default=None, help="only run suites whose filename contains this substring")
    p_test.set_defaults(func=cmd_test)

    p_show = sub.add_parser("show", help="plain-English: 'jdl show flashloans' streams live activity")
    p_show.add_argument("target", nargs="?", default="flashloans", choices=["flashloans"])
    p_show.add_argument("--interval", type=float, default=5.0, help="seconds between refreshes (default: 5)")
    p_show.add_argument("--once", action="store_true", help="print a single snapshot and exit")
    p_show.set_defaults(func=cmd_show)

    p_integrate = sub.add_parser(
        "integrate", help="verify the wiring between every system function (.env, RPC, contract, daemon)"
    )
    p_integrate.add_argument("--watch", action="store_true", help="keep re-checking on a loop (for a second shell/screen)")
    p_integrate.add_argument("--interval", type=float, default=10.0, help="seconds between checks in --watch mode")
    p_integrate.set_defaults(func=cmd_integrate)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
