#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# FLASH LOAN ZERO-GAS ENGINE — SETUP
# Supports: Ubuntu/Debian · Termux (Android) · UserLAnd
# Usage:
#   bash setup.sh          — install only
#   bash setup.sh run      — install + launch engine
#   bash setup.sh test     — install + run 79-test suite
#   bash setup.sh termux   — Termux-specific guided install
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
# Install the package so the `flashloan` command is available everywhere.
pip install $PIP_FLAGS -e "$SCRIPT_DIR/python"
ok "Dependencies installed (web3 5.31.4 + parsimonious fix); 'flashloan' command ready"

# ── 4. Data directory ────────────────────────────────────────
step "Creating data directory at $DATA_DIR..."
mkdir -p "$DATA_DIR"
ok "Data directory ready"

# ── 5. Environment file ───────────────────────────────────────
step "Setting up environment config at $ENV_FILE..."
mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
    cp "$SCRIPT_DIR/.env.template" "$ENV_FILE"
    warn "Created $ENV_FILE from template"
    echo -e "  ${YELLOW}Edit it now:  nano $ENV_FILE${RESET}"
    echo -e "  ${YELLOW}Required:     PRIVATE_KEY  ALCHEMY_ARB_KEY${RESET}"
    echo -e "  ${YELLOW}Optional:     FLASH_CONTRACT_ADDRESS (scan mode works without it)${RESET}"
else
    ok "$ENV_FILE already exists"
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
echo -e "  1. ${CYAN}nano $ENV_FILE${RESET}      — add PRIVATE_KEY + ALCHEMY_ARB_KEY"
echo -e "  2. ${CYAN}bash setup.sh run${RESET}  — launch engine"
echo -e "  3. Press ${BOLD}[9]${RESET} in the menu  — discover live protocol liquidity"
echo -e "  4. Press ${BOLD}[8]${RESET} in the menu  — run tests (79/79 should pass)"
echo

if [ "$IS_TERMUX" = "1" ]; then
    echo -e "  ${MAGENTA}${BOLD}Termux tips:${RESET}"
    echo -e "  ${DIM}• Keep screen on while running: termux-wake-lock${RESET}"
    echo -e "  ${DIM}• Run in background:  nohup bash setup.sh run &${RESET}"
    echo -e "  ${DIM}• View logs:          tail -f ~/.flash_loan_engine/flash.log${RESET}"
    echo -e "  ${DIM}• Stop engine:        pkill -f trading_core${RESET}"
    echo
fi

# ── Optional: run or test ─────────────────────────────────────
if [ "$1" = "run" ] || [ "$1" = "termux" ]; then
    echo -e "${CYAN}Launching Flash Loan Engine...${RESET}"
    python3 "$SCRIPT_DIR/python/trading_core.py"
elif [ "$1" = "test" ]; then
    echo -e "${CYAN}Running test suite (expect 79/79)...${RESET}"
    python3 "$SCRIPT_DIR/python/jdl_flash/test_flash_engine.py"
fi
