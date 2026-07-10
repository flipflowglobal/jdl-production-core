#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# userland-setup.sh — ONE command: full setup → interactive .env → start.
#
# For UserLAnd (glibc Android) and any glibc Linux (Ubuntu/WSL/Debian). Unlike
# the Termux path, the whole stack installs here. This script:
#   1. Installs system packages (apt: git, python3, python3-venv, build-essential)
#   2. Runs setup.sh — venv, deps, the `jdl` command, and auto-wires ~/jdl/.env
#      from any .env already on the machine
#   3. INTERACTIVELY PROMPTS you to type in any .env value still missing
#      (PRIVATE_KEY masked; RPC key; optional contract address) and saves them
#      to ~/jdl/.env (0600), reusing the engine's own writer
#   4. Verifies wiring with `jdl integrate`
#   5. Starts the engine
#
# Because it PROMPTS, run it in a real terminal — do NOT pipe it into bash.
# This repo is private, so obtain it by cloning first (git uses your creds):
#
#   sudo apt update && sudo apt install -y git && \
#   git clone https://github.com/flipflowglobal/jdl-production-core.git ~/jdl-production-core && \
#   bash ~/jdl-production-core/scripts/userland-setup.sh
#
# Flags:
#   --no-apt     skip the apt install step (deps already present / non-Debian)
#   --no-start   set everything up but don't launch the engine at the end
#   -y, --yes    don't ask "start now?" — just start (implies starting)
#
# Env overrides:  VENV_DIR (default ~/.flash_venv)
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$HOME/.flash_venv}"
VENV_PY="$VENV_DIR/bin/python"
VENV_JDL="$VENV_DIR/bin/jdl"

DO_APT=1; DO_START=1; ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --no-apt)   DO_APT=0 ;;
        --no-start) DO_START=0 ;;
        -y|--yes)   ASSUME_YES=1 ;;
        -h|--help)  awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
step() { echo -e "${CYAN}▶ $1${RESET}"; }
ok()   { echo -e "${GREEN}✓ $1${RESET}"; }
warn() { echo -e "${YELLOW}⚠ $1${RESET}"; }
fail() { echo -e "${RED}✗ $1${RESET}"; exit 1; }
hdr()  { echo; echo -e "${CYAN}${BOLD}━━ $1 ━━${RESET}"; }

# Termux from its RUNTIME env, not the /data/data/com.termux dir — otherwise a
# co-installed UserLAnd (which can see that host dir) would wrongly trip this
# guard and refuse to run on the very platform it's meant for.
is_termux() {
    [ -n "${TERMUX_VERSION:-}" ] && return 0
    case "${PREFIX:-}" in *com.termux*) return 0 ;; esac
    case "$(command -v pkg 2>/dev/null)" in */com.termux/*) return 0 ;; esac
    return 1
}

echo -e "${CYAN}${BOLD}"
echo "  JDL FLASH ENGINE · UserLAnd one-command setup + start"
echo -e "${RESET}"

# ── Guard: this is the glibc path, not Termux ────────────────────────────
if is_termux; then
    warn "This looks like Termux (Bionic libc), not UserLAnd."
    echo -e "  ${DIM}Use the Termux installer instead: bash scripts/termux-install.sh${RESET}"
    fail "Wrong platform for userland-setup.sh."
fi

# ── 1. System packages (apt) ─────────────────────────────────────────────
if [ "$DO_APT" = "1" ]; then
    hdr "1. System packages"
    if command -v apt-get >/dev/null 2>&1; then
        SUDO=""
        [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
        export DEBIAN_FRONTEND=noninteractive
        $SUDO apt-get update -y || warn "apt-get update had issues — continuing"
        $SUDO apt-get install -y git python3 python3-venv build-essential curl \
            && ok "System packages installed" \
            || warn "apt-get install had issues — continuing (setup.sh will report anything missing)"
    else
        warn "no apt-get — skipping (install git + python3 + a compiler yourself if setup.sh complains)"
    fi
else
    step "Skipping apt (--no-apt)"
fi

# ── 2. Run the full platform installer ───────────────────────────────────
hdr "2. Setup (venv, deps, jdl command, .env auto-wire)"
[ -f "$REPO_DIR/setup.sh" ] || fail "setup.sh not found at $REPO_DIR — run this from inside the cloned repo."
bash "$REPO_DIR/setup.sh"
[ -x "$VENV_PY" ] || fail "venv not created at $VENV_DIR — see setup.sh output above."

# ── 3. Interactive .env entry ────────────────────────────────────────────
hdr "3. Configure ~/jdl/.env"
if [ ! -t 0 ]; then
    warn "Not an interactive terminal — skipping prompts."
    echo -e "  ${DIM}Edit ~/jdl/.env by hand, or re-run this from a real terminal.${RESET}"
else
    # Ask the engine which required keys are still unset (placeholder), so we
    # only prompt for what's actually missing after setup.sh's auto-wire.
    status_of() { # KEY -> "SET"|"UNSET"
        "$VENV_PY" - "$1" <<'PY'
import sys
from jdl_flash.env_autowire import parse_env_file, is_placeholder, CANONICAL_ENV
key = sys.argv[1]
val = parse_env_file(CANONICAL_ENV).get(key, "")
print("SET" if val and not is_placeholder(val) else "UNSET")
PY
    }

    echo -e "  ${DIM}Values are saved to ~/jdl/.env (0600). Press Enter to keep an existing"
    echo -e "  value or skip an optional one. Nothing you type is echoed for the key.${RESET}\n"

    IN_PK=""; IN_ALCH=""; IN_FCA=""

    # PRIVATE_KEY (masked)
    if [ "$(status_of PRIVATE_KEY)" = "SET" ]; then
        printf "  PRIVATE_KEY is already set. Replace it? [y/N] "; read -r ans
        case "$ans" in [Yy]*) printf "  New PRIVATE_KEY (hidden): "; read -rs IN_PK; echo ;; esac
    else
        printf "  ${BOLD}PRIVATE_KEY${RESET} (0x…, hidden): "; read -rs IN_PK; echo
    fi

    # RPC source — ALCHEMY_ARB_KEY (visible)
    if [ "$(status_of ALCHEMY_ARB_KEY)" = "SET" ]; then
        printf "  ALCHEMY_ARB_KEY is already set. Replace it? [y/N] "; read -r ans
        case "$ans" in [Yy]*) printf "  New ALCHEMY_ARB_KEY: "; read -r IN_ALCH ;; esac
    else
        printf "  ${BOLD}ALCHEMY_ARB_KEY${RESET} (free key from alchemy.com → Arbitrum One): "; read -r IN_ALCH
    fi

    # FLASH_CONTRACT_ADDRESS (optional)
    printf "  FLASH_CONTRACT_ADDRESS (optional — Enter to skip, scan-only works without it): "; read -r IN_FCA

    # ── Extra RPC sources for redundancy/failover ────────────────────────
    # The engine reads any number of ALCHEMY_KEY_* and numbered RPC_URLn and
    # tries each in turn (get_w3()), so one provider rate-limiting never takes
    # the bot offline. Collect them as KEY=VALUE lines for one write.
    #
    # Slot allocation is re-run safe: a slot is "taken" if it's already real in
    # ~/jdl/.env OR already chosen this run, and we always pick the next FREE
    # index — so re-running adds redundancy instead of overwriting existing keys.
    ENTRIES=""; PENDING=" "
    add_entry() {   # KEY VALUE
        [ -n "$2" ] || return 0
        ENTRIES="${ENTRIES}${1}=${2}
"
        PENDING="${PENDING}${1} "
    }
    slot_taken() {  # KEY -> 0 if already real on disk or pending this run
        case "$PENDING" in *" $1 "*) return 0 ;; esac
        [ "$(status_of "$1")" = "SET" ]
    }
    next_free() {   # PREFIX START -> first index whose PREFIX<idx> is free
        local prefix="$1" i="$2"
        while slot_taken "${prefix}${i}"; do i=$((i + 1)); done
        echo "$i"
    }
    add_entry PRIVATE_KEY "$IN_PK"
    add_entry ALCHEMY_ARB_KEY "$IN_ALCH"
    add_entry FLASH_CONTRACT_ADDRESS "$IN_FCA"

    echo -e "\n  ${DIM}Add more Alchemy keys / RPC URLs for failover (recommended). Enter to stop.${RESET}"
    while : ; do
        printf "  Additional Alchemy key (Enter to stop): "; read -r extra_k
        [ -z "$extra_k" ] && break
        add_entry "ALCHEMY_KEY_$(next_free ALCHEMY_KEY_ 2)" "$extra_k"
    done
    while : ; do
        printf "  Additional RPC URL (full https://… , Enter to stop): "; read -r extra_u
        [ -z "$extra_u" ] && break
        if slot_taken RPC_URL; then
            # RPC_URL already has a real value (or was set this run) — use the
            # next free numbered slot.
            add_entry "RPC_URL$(next_free RPC_URL 2)" "$extra_u"
        else
            # RPC_URL is still the template placeholder: put the first URL there
            # so the required-keys / reachability checks recognize it as primary.
            add_entry RPC_URL "$extra_u"
        fi
    done

    # Persist through the engine's own writer (in-place, 0600, skips blanks).
    JDL_ENTRIES="$ENTRIES" "$VENV_PY" - <<'PY'
import os
from jdl_flash.env_autowire import set_values
vals = {}
for line in os.environ.get("JDL_ENTRIES", "").splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        vals[k.strip()] = v
written = set_values(vals)
print("  ✓ saved to ~/jdl/.env:", ", ".join(written) if written else "(no new values)")
PY
    unset IN_PK IN_ALCH IN_FCA ENTRIES JDL_ENTRIES
fi

# ── 4. Verify wiring ─────────────────────────────────────────────────────
hdr "4. Verify wiring (jdl integrate)"
"$VENV_JDL" integrate || warn "some links are not green yet — scan-only mode still runs; see the ✗ lines above"

# ── 5. Start ─────────────────────────────────────────────────────────────
hdr "5. Start"
if [ "$DO_START" = "0" ]; then
    ok "Setup complete. Start later with:  source $VENV_DIR/bin/activate && jdl start flashloan"
    exit 0
fi
if [ "$ASSUME_YES" = "0" ] && [ -t 0 ]; then
    printf "  Start the engine now? [Y/n] "; read -r start_ans
    case "$start_ans" in [Nn]*) ok "Not starting. Run later:  source $VENV_DIR/bin/activate && jdl start flashloan"; exit 0 ;; esac
fi
step "Launching the engine…"
exec "$VENV_JDL" start flashloan
