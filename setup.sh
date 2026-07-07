#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# FLASH LOAN ZERO-GAS ENGINE — SETUP
# Supports: Ubuntu/Debian · Termux (Android) · UserLAnd
# Usage:
#   bash setup.sh          — install only
#   bash setup.sh run      — install + launch engine
#   bash setup.sh test     — install + run 79-test suite
#   bash setup.sh termux   — Termux-specific guided install
#   bash setup.sh swarm-boot — install the always-on parallel-scanner boot hook
#                              (Termux:Boot on Termux; prints manual steps elsewhere)
# ═══════════════════════════════════════════════════════════
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

echo -e "${CYAN}${BOLD}"
cat << 'BANNER'
 ███████╗██╗      █████╗ ███████╗██╗  ██╗
 ██╔════╝██║     ██╔══██╗██╔════╝██║  ██║
 █████╗  ██║     ███████║███████╗███████║
 ██╔══╝  ██║     ██╔══██║╚════██║██╔══██║
 ██║     ███████╗██║  ██║███████║██║  ██║
 ╚═╝     ╚══════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
  ZERO-GAS FLASH LOAN ENGINE  ·  SETUP v2
BANNER
echo -e "${RESET}"

VENV_DIR="$HOME/.flash_venv"
DATA_DIR="$HOME/.flash_loan_engine"
ENV_DIR="$HOME/jdl"
ENV_FILE="$ENV_DIR/.env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

step()  { echo -e "${CYAN}▶ $1${RESET}"; }
ok()    { echo -e "${GREEN}✓ $1${RESET}"; }
warn()  { echo -e "${YELLOW}⚠ $1${RESET}"; }
fail()  { echo -e "${RED}✗ $1${RESET}"; exit 1; }
info()  { echo -e "${DIM}  $1${RESET}"; }

# ── Detect environment ───────────────────────────────────────
IS_TERMUX=0
IS_USERLAND=0
if [ -n "$TERMUX_VERSION" ] || [ -d "/data/data/com.termux" ]; then
    IS_TERMUX=1
elif grep -qi 'userland\|android' /proc/version 2>/dev/null; then
    IS_USERLAND=1
fi

if [ "$IS_TERMUX" = "1" ]; then
    echo -e "${MAGENTA}${BOLD}  Platform: Termux (Android)${RESET}"
elif [ "$IS_USERLAND" = "1" ]; then
    echo -e "${MAGENTA}${BOLD}  Platform: UserLAnd (Android)${RESET}"
else
    echo -e "${MAGENTA}${BOLD}  Platform: Linux/Ubuntu${RESET}"
fi
echo

# ── Termux pre-flight: ensure required packages installed ────
if [ "$IS_TERMUX" = "1" ]; then
    step "Checking Termux packages..."
    MISSING_PKGS=""
    for pkg in python git openssl libffi clang make; do
        if ! pkg list-installed 2>/dev/null | grep -q "^$pkg"; then
            MISSING_PKGS="$MISSING_PKGS $pkg"
        fi
    done
    if [ -n "$MISSING_PKGS" ]; then
        warn "Missing Termux packages:$MISSING_PKGS"
        echo -e "  ${YELLOW}Installing now with pkg...${RESET}"
        pkg install -y $MISSING_PKGS
    fi
    # Pre-compiled wheels for deps that CANNOT build from source on Android:
    #  • python-rpds-py  — rpds-py (jsonschema dep, needs Rust otherwise)
    #  • python-psutil   — psutil (web3 5.31.4 → ipfshttpclient → multiaddr → psutil;
    #                       its build backend rejects Android → "platform android is
    #                       not supported"). The --system-site-packages venv below
    #                       then sees these so pip never tries to compile them.
    pkg install -y python-rpds-py 2>/dev/null || true
    pkg install -y python-psutil  2>/dev/null || true
    ok "Termux packages ready"
fi

# ── 1. Python check ──────────────────────────────────────────
step "Checking Python..."
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    if [ "$IS_TERMUX" = "1" ]; then
        fail "Python not found. Run: pkg install python"
    else
        fail "Python not found. Run: sudo apt install python3 python3-venv"
    fi
fi

PY_VER=$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo $PY_VER | cut -d. -f1)
PY_MINOR=$(echo $PY_VER | cut -d. -f2)
if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 8 ]; then
    ok "Python $PY_VER"
else
    fail "Python 3.8+ required (found $PY_VER). On Termux: pkg install python"
fi

# ── 2. Virtual environment ───────────────────────────────────
step "Setting up virtual environment at $VENV_DIR..."
if [ "$IS_TERMUX" = "1" ]; then
    # --system-site-packages lets the venv see pkg-installed packages
    # (e.g. python-rpds-py) so pip doesn't build them from source on Android.
    EXISTING_SYSPKGS=0
    if [ -d "$VENV_DIR" ]; then
        EXISTING_SYSPKGS=$(grep -ic "include-system-site-packages = true" "$VENV_DIR/pyvenv.cfg" 2>/dev/null || echo 0)
    fi
    if [ ! -d "$VENV_DIR" ] || [ "$EXISTING_SYSPKGS" -eq 0 ]; then
        [ -d "$VENV_DIR" ] && { warn "Recreating venv with --system-site-packages for Termux..."; rm -rf "$VENV_DIR"; }
        $PYTHON -m venv --system-site-packages "$VENV_DIR" 2>/dev/null || \
        $PYTHON -m venv --system-site-packages "$VENV_DIR" --without-pip 2>/dev/null || \
        fail "venv creation failed. Try: pkg reinstall python"
        ok "Virtual environment created"
    else
        ok "Virtual environment already exists"
    fi
else
    if [ ! -d "$VENV_DIR" ]; then
        $PYTHON -m venv "$VENV_DIR" || \
        { warn "venv failed, trying with --without-pip"
          $PYTHON -m venv "$VENV_DIR" --without-pip; }
        ok "Virtual environment created"
    else
        ok "Virtual environment already exists"
    fi
fi

source "$VENV_DIR/bin/activate"

# Ensure pip is available inside venv
if ! command -v pip &>/dev/null; then
    step "Bootstrapping pip inside venv..."
    curl -sS https://bootstrap.pypa.io/get-pip.py | $PYTHON
    ok "pip bootstrapped"
fi

# ── 3. Install dependencies ───────────────────────────────────
step "Installing Python dependencies..."
info "Using web3 6.x — no psutil, no pydantic-core Rust build, Android-compatible"

PIP_FLAGS="--quiet"
[ "$IS_TERMUX" = "1" ] && PIP_FLAGS="--quiet --no-cache-dir"

pip install $PIP_FLAGS --upgrade pip
pip install $PIP_FLAGS -r "$SCRIPT_DIR/python/requirements_flash.txt"
# CRITICAL: web3 5.31.4 ships parsimonious 0.8.x (`from inspect import getargspec`),
# removed in Python 3.9+ → `import web3` crashes and the engine says
# "web3 not installed". Override it (can't be a normal pin: eth-abi 2.2.0 caps
# parsimonious<0.9.0). --no-deps avoids re-triggering the resolver.
pip install $PIP_FLAGS --no-deps --upgrade 'parsimonious>=0.10'
# Install the package so `jdl` (and flashloan/flashpro) are available everywhere.
pip install $PIP_FLAGS -e "$SCRIPT_DIR/python"
ok "Dependencies installed (web3 5.31.4 + parsimonious fix); 'jdl' command ready"

# ── 3b/3c/3d. Node.js + Foundry + Rust — the optional server-side polyglot
# stack (node/ + rust/hotpath/, see POLYGLOT.md) ──────────────────────
# Deliberately skipped on Termux: Foundry only ships glibc binaries (Termux
# is Bionic libc, so foundryup can never succeed there — it would just burn
# mobile data downloading something that can't run), and POLYGLOT.md/
# TERMUX.md both document this stack as a Linux server/VPS companion, not
# the on-device Termux path ("Termux/Android: Python engine — unchanged").
# jdl_native's ctypes/subprocess/pure-Python fallbacks mean the Python
# engine needs none of it. UserLAnd's proot rootfs is a real glibc Linux
# userspace, so it gets the same treatment as Ubuntu/WSL/macOS below.
if [ "$IS_TERMUX" = "1" ]; then
    step "Skipping Node/Foundry/Rust (optional server-side polyglot stack)..."
    info "Termux runs the Python engine only (POLYGLOT.md: \"Termux/Android:"
    info "Python engine — unchanged\"). node/ + rust/hotpath/ target a Linux"
    info "server/VPS instead; jdl_native's ctypes/subprocess/pure-Python"
    info "fallbacks mean nothing on-device needs them. Foundry in particular"
    info "ships glibc-only binaries that cannot run under Termux's Bionic libc."
else
    # ── 3b. Node.js / npm — contracts (Hardhat/solc) + node/ hotpath ──
    step "Checking Node.js..."
    if command -v npm &>/dev/null; then
        ok "npm found ($(npm --version))"
        for d in "$SCRIPT_DIR/contracts" "$SCRIPT_DIR/node"; do
            if [ -f "$d/package.json" ]; then
                step "npm install in $(basename "$d")..."
                (cd "$d" && npm install --no-audit --no-fund --quiet) \
                    && ok "$(basename "$d") dependencies installed" \
                    || warn "$(basename "$d") npm install failed — see above (non-fatal)"
            fi
        done
    else
        warn "npm not found — skipping contracts/node dependency install."
        info "Install with: sudo apt install nodejs npm  (or use nvm)"
    fi

    # ── 3c. Foundry (forge/cast) — Solidity toolchain ──
    step "Checking Foundry..."
    if command -v forge &>/dev/null; then
        ok "forge found ($(forge --version 2>/dev/null | head -1))"
    else
        warn "forge not found — attempting install via foundryup..."
        if curl -L https://foundry.paradigm.xyz 2>/dev/null | bash 2>/dev/null; then
            export PATH="$HOME/.foundry/bin:$PATH"
            "$HOME/.foundry/bin/foundryup" 2>/dev/null || true
        fi
        command -v forge &>/dev/null \
            && ok "Foundry installed" \
            || warn "Foundry auto-install didn't complete (no network, or unsupported platform) — install manually: https://getfoundry.sh"
    fi

    # ── 3d. Rust — jdl_native's optional hotpath extension ──
    step "Checking Rust..."
    if command -v cargo &>/dev/null; then
        ok "cargo found ($(cargo --version))"
    else
        warn "cargo not found — installing via rustup..."
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs 2>/dev/null | sh -s -- -y 2>/dev/null \
            && source "$HOME/.cargo/env" && ok "Rust installed" \
            || warn "Rust install failed (non-fatal — pure-Python/ctypes fallback still works, see POLYGLOT.md)"
    fi
    if command -v cargo &>/dev/null && [ -f "$SCRIPT_DIR/rust/hotpath/Cargo.toml" ]; then
        step "Building rust/hotpath (release, best-effort)..."
        (cd "$SCRIPT_DIR/rust/hotpath" && cargo build --release --quiet) \
            && ok "rust/hotpath built" \
            || warn "rust/hotpath build failed — fallbacks still work (see POLYGLOT.md)"
    fi
fi

# ── 4. Data directory ────────────────────────────────────────
step "Creating data directory at $DATA_DIR..."
mkdir -p "$DATA_DIR"
ok "Data directory ready"

# ── 5. Environment file — auto-wired, no manual copy-paste ────
step "Wiring $ENV_FILE from every .env file reachable on this machine..."
mkdir -p "$ENV_DIR"
AUTOWIRE_OUT=$($PYTHON -c "
from jdl_flash.env_autowire import autowire
report = autowire()
print('UNRESOLVED:' + ','.join(report['unresolved']))
" 2>&1)
echo "$AUTOWIRE_OUT" | grep -v '^UNRESOLVED:' || true
UNRESOLVED_LINE=$(echo "$AUTOWIRE_OUT" | grep '^UNRESOLVED:' || true)
UNRESOLVED="${UNRESOLVED_LINE#UNRESOLVED:}"
if [ -n "$UNRESOLVED" ]; then
    warn "Still need a human for: $UNRESOLVED"
    echo -e "  ${YELLOW}Nobody on this machine has ever set these — add them by hand:  nano $ENV_FILE${RESET}"
else
    ok "$ENV_FILE fully wired — no manual edits needed"
fi

# ── 6. Summary ───────────────────────────────────────────────
echo
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}${BOLD}║   Setup Complete!  Flash Loan Engine Ready       ║${RESET}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
echo
echo -e "  ${BOLD}Flash Loan Sources (pre-deployed, no contract needed):${RESET}"
echo -e "  ${GREEN}●${RESET} Balancer V2     0xBA12...2C8   ${GREEN}0% fee${RESET}"
echo -e "  ${CYAN}●${RESET} Aave V3         0x794a...4aD   0.09% fee"
echo -e "  ${CYAN}●${RESET} Radiant Capital 0xF4B1...9E1   0.09% fee"
echo -e "  ${DIM}●${RESET} Uniswap V3      multiple pools  0.05–0.30% fee"
echo
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "  1. ${CYAN}jdl integrate${RESET}       — verify every connection is wired (env/RPC/contract/daemon)"
echo -e "  2. ${CYAN}jdl start flashloan${RESET} — launch the engine (same as: jdl run)"
echo -e "  3. Press ${BOLD}[9]${RESET} in the menu  — discover live protocol liquidity"
echo -e "  4. ${CYAN}jdl test system${RESET}     — run the full test suite; auto-heals .env if a suite fails"
echo -e "  ${DIM}Any value \`jdl integrate\` flags as unresolved has never been set anywhere on this"
echo -e "  machine — add it by hand: nano $ENV_FILE${RESET}"
echo
echo -e "  ${BOLD}Plain-English CLI — 'jdl --help' lists every command:${RESET}"
echo -e "  ${DIM}install (this script) · start flashloan · test system · supervisor ·"
echo -e "  show flashloans · integrate · update · status · deploy${RESET}"
echo

if [ "$IS_TERMUX" = "1" ]; then
    echo -e "  ${MAGENTA}${BOLD}Termux tips:${RESET}"
    echo -e "  ${DIM}• Keep screen on while running: termux-wake-lock${RESET}"
    echo -e "  ${DIM}• Run in background (auto-restart on crash): nohup jdl supervisor &${RESET}"
    echo -e "  ${DIM}• View live activity/logs from a 2nd shell:   jdl show flashloans${RESET}"
    echo -e "  ${DIM}• Stop it:                                    pkill -f 'jdl supervisor'${RESET}"
    echo -e "  ${DIM}• Constant parallel scanning across reboots: bash setup.sh swarm-boot${RESET}"
    echo
fi

# ── Optional: run or test ─────────────────────────────────────
if [ "$1" = "run" ] || [ "$1" = "termux" ]; then
    echo -e "${CYAN}Launching Flash Loan Engine...${RESET}"
    python3 "$SCRIPT_DIR/python/trading_core.py"
elif [ "$1" = "test" ]; then
    echo -e "${CYAN}Running test suite (expect 79/79)...${RESET}"
    python3 "$SCRIPT_DIR/python/jdl_flash/test_flash_engine.py"
elif [ "$1" = "swarm-boot" ]; then
    # Wires up constant, unattended parallel-scanner opportunity scanning
    # (swarm_daemon.py, supervised by flash_supervisor.py) so it survives
    # reboots/crashes instead of only running while a terminal is open.
    if [ "$IS_TERMUX" = "1" ]; then
        step "Installing Termux:Boot hook for the always-on swarm scanner..."
        mkdir -p "$HOME/.termux/boot"
        ln -sf "$SCRIPT_DIR/scripts/termux-boot-swarm.sh" "$HOME/.termux/boot/start-flash-swarm.sh"
        chmod +x "$SCRIPT_DIR/scripts/termux-boot-swarm.sh" "$SCRIPT_DIR/scripts/start-swarm-daemon.sh"
        ok "Linked ~/.termux/boot/start-flash-swarm.sh -> $SCRIPT_DIR/scripts/termux-boot-swarm.sh"
        info "Requires the Termux:Boot app (github.com/termux/termux-boot) installed with autostart"
        info "permission granted, so Android actually runs this hook after a reboot. Optional but"
        info "recommended: 'pkg install termux-api' + the Termux:API app, so termux-wake-lock keeps"
        info "the scanner running instead of Android suspending it to save battery."
        echo
        step "Start it right now without waiting for a reboot:"
        info "nohup bash $SCRIPT_DIR/scripts/start-swarm-daemon.sh &"
    else
        chmod +x "$SCRIPT_DIR/scripts/start-swarm-daemon.sh"
        warn "Termux:Boot only exists on Termux — on $([ "$IS_USERLAND" = "1" ] && echo UserLAnd || echo this platform), start the always-on scanner yourself:"
        info "  • One-off (foreground):        bash $SCRIPT_DIR/scripts/start-swarm-daemon.sh"
        info "  • Background, survives logout: nohup bash $SCRIPT_DIR/scripts/start-swarm-daemon.sh &"
        info "  • systemd user service (if your distro/UserLAnd session has systemd):"
        info "      ExecStart=/bin/bash $SCRIPT_DIR/scripts/start-swarm-daemon.sh"
        info "      then: systemctl --user enable --now flash-swarm.service"
    fi
fi
