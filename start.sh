#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# start.sh — the ONE front door. Run the whole system from the main directory.
#
#   ./start.sh            first run: set everything up (platform-aware) and go
#   ./start.sh setup      (re)install deps, wire .env, verify
#   ./start.sh start      launch the interactive engine
#   ./start.sh test       run the test suite
#   ./start.sh verify     prove the install can actually execute
#   ./start.sh status     one-shot: daemon liveness, executions, revenue
#   ./start.sh update     git pull + reinstall
#   ./start.sh help       this message
#
# Architecture: this is a thin dispatcher. It detects the platform and delegates
# to the scripts that already do the work — no logic is duplicated here:
#   • Termux (Bionic)          → scripts/termux-install.sh   (Python-only stack)
#   • UserLAnd / Ubuntu / WSL  → scripts/userland-setup.sh   (full stack + .env prompts)
#   • test/verify/start/…      → scripts/run-all-tests.sh, scripts/termux-verify.sh, `jdl`
#
# Extra args after the verb are passed straight through, e.g.
#   ./start.sh setup --no-start        ./start.sh test --quick
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$HOME/.flash_venv}"
VENV_JDL="$VENV_DIR/bin/jdl"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
warn() { echo -e "${YELLOW}⚠ $1${RESET}"; }
fail() { echo -e "${RED}✗ $1${RESET}"; exit 1; }

# Detect Termux by its RUNTIME env, not the /data/data/com.termux directory —
# a co-installed UserLAnd (proot Ubuntu) can see that host dir and would be
# misdetected as Termux, then die on `pkg: command not found`.
is_termux() {
    [ -n "${TERMUX_VERSION:-}" ] && return 0
    case "${PREFIX:-}" in *com.termux*) return 0 ;; esac
    case "$(command -v pkg 2>/dev/null)" in */com.termux/*) return 0 ;; esac
    return 1
}

usage() { awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; }

# Verbs that need the engine installed resolve `jdl` into $JDL (prefer the venv,
# then PATH). Runs in the current shell — not a subshell — so a failure exits
# the whole script cleanly instead of being captured by command substitution.
JDL=""
resolve_jdl() {
    if [ -x "$VENV_JDL" ]; then JDL="$VENV_JDL"
    elif command -v jdl >/dev/null 2>&1; then JDL="$(command -v jdl)"
    else fail "not set up yet — run: ./start.sh setup"; fi
}

VERB="${1:-setup}"; shift 2>/dev/null || true

case "$VERB" in
    setup)
        if is_termux; then
            echo -e "${CYAN}${BOLD}Termux detected → scripts/termux-install.sh${RESET}"
            # Point the installer at THIS clone so it git-pulls/sets up in place
            # instead of cloning a second copy into ~/projects/… (its default).
            export JDL_DIR="$REPO_DIR"
            exec bash "$REPO_DIR/scripts/termux-install.sh" "$@"
        else
            # userland-setup.sh already derives its repo from its own location.
            echo -e "${CYAN}${BOLD}glibc (UserLAnd/Ubuntu/WSL) → scripts/userland-setup.sh${RESET}"
            exec bash "$REPO_DIR/scripts/userland-setup.sh" "$@"
        fi
        ;;
    start|run)
        resolve_jdl; exec "$JDL" start flashloan
        ;;
    test)
        exec bash "$REPO_DIR/scripts/run-all-tests.sh" "$@"
        ;;
    verify|doctor)
        exec bash "$REPO_DIR/scripts/termux-verify.sh" "$@"
        ;;
    status)
        resolve_jdl; exec "$JDL" status
        ;;
    update)
        resolve_jdl; exec "$JDL" update "$@"
        ;;
    integrate|config)
        resolve_jdl; exec "$JDL" integrate "$@"
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        warn "unknown command: $VERB"
        echo
        usage
        exit 2
        ;;
esac
