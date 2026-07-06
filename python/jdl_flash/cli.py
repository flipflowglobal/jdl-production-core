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
import importlib.util
import subprocess
import sys
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
    "test_cli.py",
]


def _python_dir() -> Path:
    """The repo's python/ directory (this package's parent)."""
    import jdl_flash

    return Path(jdl_flash.__file__).resolve().parent.parent


def _load_flash_supervisor() -> ModuleType:
    path = _python_dir() / "flash_supervisor.py"
    spec = importlib.util.spec_from_file_location("flash_supervisor", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load flash_supervisor.py from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cmd_run(_args: argparse.Namespace) -> int:
    from jdl_flash.flash_loan_engine import _run

    _run()
    return 0


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


def cmd_install(args: argparse.Namespace) -> int:
    # The Termux:Boot hook is an OS-level install step (symlinks, permission
    # checks) that setup.sh already does correctly per-platform — call straight
    # through rather than re-implementing any of that here.
    setup_sh = _python_dir().parent / "setup.sh"
    return subprocess.run(["bash", str(setup_sh), "swarm-boot"]).returncode


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


def cmd_test(args: argparse.Namespace) -> int:
    python_dir = _python_dir()
    suites = [python_dir / "jdl_flash" / name for name in _PACKAGED_TESTS]
    suites.append(python_dir / "test_flash_supervisor.py")
    suites.append(python_dir / "jdl_native" / "test_jdl_native.py")
    if args.filter:
        suites = [s for s in suites if args.filter in s.name]

    failed = []
    for suite in suites:
        print(f"\n{'=' * 60}\n  {suite.relative_to(python_dir)}\n{'=' * 60}")
        rc = subprocess.run([sys.executable, str(suite)], cwd=python_dir).returncode
        if rc != 0:
            failed.append(str(suite.relative_to(python_dir)))

    print(f"\n{'=' * 60}")
    if failed:
        print(f"  FAILED: {len(failed)}/{len(suites)} suite(s) — {', '.join(failed)}")
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

    p_test = sub.add_parser("test", help="run the full test suite (same suites CI runs)")
    p_test.add_argument("--filter", default=None, help="only run suites whose filename contains this substring")
    p_test.set_defaults(func=cmd_test)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
