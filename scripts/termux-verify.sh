#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# termux-verify.sh — ASSURED METHOD OF EXECUTION
#
# A layered "doctor" that proves the flash engine can actually run on this
# device. `jdl integrate` checks the config/runtime layer (.env, RPC, contract,
# daemon) but assumes the package already imports — it can't catch the classic
# Termux install-layer failures (no venv, `jdl` not on PATH, `import web3`
# crashing). This script checks that layer first, then defers to `jdl integrate`
# for live-trading readiness, and prints one clear verdict.
#
# Usage:
#   bash scripts/termux-verify.sh            # verify only
#   bash scripts/termux-verify.sh --fix      # on failure, re-run setup.sh once, re-verify
#   bash scripts/termux-verify.sh --run      # after a PASS, launch the engine
#   bash scripts/termux-verify.sh --quick    # skip the (slow) network RPC probe
#
# Env overrides:
#   JDL_DIR    repo root      (default: this script's parent)
#   VENV_DIR   virtualenv     (default: ~/.flash_venv)
#
# Exit code: 0 if every BLOCKING check passes (engine can execute), else 1.
# Config/live-readiness items (contract deployed, RPC reachable, daemon) are
# ADVISORY — scan-only mode runs without them, so they never fail the verdict.
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JDL_DIR="${JDL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$HOME/.flash_venv}"
VENV_PY="$VENV_DIR/bin/python"
VENV_JDL="$VENV_DIR/bin/jdl"

DO_FIX=0; DO_RUN=0; QUICK=0
for arg in "$@"; do
    case "$arg" in
        --fix)   DO_FIX=1 ;;
        --run)   DO_RUN=1 ;;
        --quick) QUICK=1 ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

BLOCKING_FAIL=0
declare -a REMEDIES=()

pass()    { echo -e "  ${GREEN}✓${RESET} $1${2:+  ${DIM}$2${RESET}}"; }
fail()    { echo -e "  ${RED}✗${RESET} $1${2:+  ${DIM}$2${RESET}}"; BLOCKING_FAIL=$((BLOCKING_FAIL+1)); [ -n "${3:-}" ] && REMEDIES+=("$3"); }
advisory(){ echo -e "  ${YELLOW}○${RESET} $1${2:+  ${DIM}$2${RESET}}"; }
hdr()     { echo; echo -e "${CYAN}${BOLD}$1${RESET}"; }

run_verify() {
    BLOCKING_FAIL=0
    REMEDIES=()

    echo -e "${CYAN}${BOLD}"
    echo "  ASSURED EXECUTION CHECK — JDL Flash Engine"
    echo -e "${RESET}${DIM}  repo: $JDL_DIR   venv: $VENV_DIR${RESET}"

    # ── Layer 1: environment ────────────────────────────────────────────
    hdr "1. Environment"
    if [ -n "${TERMUX_VERSION:-}" ] || [ -d "/data/data/com.termux" ]; then
        pass "Platform is Termux (Android)"
    else
        advisory "Not Termux" "this doctor is Termux-focused, but the checks below still apply"
    fi

    local sys_py=""
    if command -v python3 >/dev/null 2>&1; then sys_py=python3
    elif command -v python >/dev/null 2>&1; then sys_py=python; fi
    if [ -n "$sys_py" ]; then
        local pv; pv="$($sys_py -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)"
        if $sys_py -c 'import sys;sys.exit(0 if sys.version_info[:2]>=(3,9) else 1)' 2>/dev/null; then
            pass "Python $pv (>= 3.9)"
        else
            fail "Python $pv is too old (need 3.9+)" "" "pkg install python   # then re-run: bash setup.sh"
        fi
    else
        fail "No python found" "" "pkg install python"
    fi

    # ── Layer 2: install ────────────────────────────────────────────────
    hdr "2. Install"
    local venv_ok=0
    if [ -x "$VENV_PY" ]; then
        pass "Virtualenv present" "$VENV_DIR"
        venv_ok=1
    else
        # Nothing below can pass without the venv; skip the deeper layers but
        # still fall through to the verdict so the run is reported and gated.
        fail "Virtualenv missing at $VENV_DIR" "" "bash $JDL_DIR/setup.sh"
    fi

    if [ "$venv_ok" = "1" ]; then
        if "$VENV_PY" -c 'import web3' 2>/dev/null; then
            local w3; w3="$("$VENV_PY" -c 'import web3;print(getattr(web3,"__version__","?"))' 2>/dev/null)"
            pass "web3 importable" "v$w3"
        else
            fail "web3 not importable" "the #1 Termux failure" "bash $JDL_DIR/setup.sh   # installs the Android-safe web3 6.x"
        fi

        if "$VENV_PY" -c 'import jdl_flash, jdl_flash.flash_loan_engine' 2>/dev/null; then
            pass "jdl_flash package imports" "engine module loads"
        else
            fail "jdl_flash package not importable" "" "pip install -e $JDL_DIR/python   (or: bash $JDL_DIR/setup.sh)"
        fi

        if [ -x "$VENV_JDL" ]; then
            pass "jdl command on PATH" "$VENV_JDL"
        else
            fail "jdl console-script missing" "" "pip install -e $JDL_DIR/python"
        fi

        # ── Layer 3: execution smoke ────────────────────────────────────
        hdr "3. Execution smoke test"
        if [ -x "$VENV_JDL" ] && "$VENV_JDL" --help >/dev/null 2>&1; then
            pass "\`jdl --help\` dispatches"
        else
            fail "\`jdl --help\` failed to run" "" "bash $JDL_DIR/setup.sh"
        fi
        if [ -x "$VENV_JDL" ] && "$VENV_JDL" status >/dev/null 2>&1; then
            pass "\`jdl status\` runs non-interactively" "state/supervisor layer OK"
        else
            advisory "\`jdl status\` did not return cleanly" "non-fatal — usually just no data dir yet"
        fi

        # ── Layer 4: live-trading readiness (advisory) ──────────────────
        hdr "4. Live-trading readiness ${DIM}(advisory — scan-only mode runs without these)${RESET}"
        if [ ! -f "$HOME/jdl/.env" ]; then
            advisory "~/jdl/.env not present" "run: bash setup.sh  (auto-wires it)"
        elif [ "$QUICK" = "1" ]; then
            advisory "~/jdl/.env present" "--quick: skipped the network RPC probe (run \`jdl integrate\` for the full check)"
        elif [ -x "$VENV_JDL" ]; then
            # `jdl integrate` prints one line per link; show it verbatim. Its
            # exit code is intentionally NOT gated on (it returns non-zero in
            # scan-only mode because no contract is deployed — a valid state).
            "$VENV_JDL" integrate 2>&1 | sed 's/^/  /' || true
        fi
    fi

    # ── Verdict ─────────────────────────────────────────────────────────
    echo
    if [ "$BLOCKING_FAIL" -eq 0 ]; then
        echo -e "${GREEN}${BOLD}  ✓ ASSURED — the engine can execute on this device.${RESET}"
        echo -e "${DIM}  Launch it:  source $VENV_DIR/bin/activate && jdl start flashloan${RESET}"
        return 0
    fi
    echo -e "${RED}${BOLD}  ✗ NOT READY — $BLOCKING_FAIL blocking check(s) failed.${RESET}"
    echo -e "${BOLD}  Fix:${RESET}"
    local seen=""
    for r in "${REMEDIES[@]}"; do
        case "$seen" in *"|$r|"*) continue ;; esac
        seen="$seen|$r|"
        echo -e "    ${CYAN}$r${RESET}"
    done
    return 1
}

run_verify
VERDICT=$?

# ── --fix: re-run setup once, then re-verify ────────────────────────────
if [ "$VERDICT" -ne 0 ] && [ "$DO_FIX" = "1" ]; then
    echo
    echo -e "${YELLOW}${BOLD}  --fix: re-running setup.sh, then re-verifying…${RESET}"
    if [ -f "$JDL_DIR/setup.sh" ]; then
        bash "$JDL_DIR/setup.sh" || true
        echo; echo -e "${CYAN}${BOLD}  Re-verifying after --fix…${RESET}"
        run_verify
        VERDICT=$?
    else
        echo -e "${RED}  setup.sh not found at $JDL_DIR — cannot --fix.${RESET}"
    fi
fi

# ── --run: launch the engine on a clean bill of health ──────────────────
if [ "$VERDICT" -eq 0 ] && [ "$DO_RUN" = "1" ]; then
    echo; echo -e "${CYAN}${BOLD}  --run: launching the engine…${RESET}"
    exec "$VENV_JDL" start flashloan
fi

exit $VERDICT
