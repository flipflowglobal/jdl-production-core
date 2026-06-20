#!/usr/bin/env bash
# Flash Loan Zero-Gas Engine — One-Command Setup
# Usage: bash setup.sh         (install)
#        bash setup.sh run     (install + launch engine)
#        bash setup.sh test    (install + run tests)

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

echo -e "${CYAN}${BOLD}"
cat << 'BANNER'
 ███████╗██╗      █████╗ ███████╗██╗  ██╗
 ██╔════╝██║     ██╔══██╗██╔════╝██║  ██║
 █████╗  ██║     ███████║███████╗███████║
 ██╔══╝  ██║     ██╔══██║╚════██║██╔══██║
 ██║     ███████╗██║  ██║███████║██║  ██║
 ╚═╝     ╚══════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
  ZERO-GAS LOAN ENGINE  ·  SETUP
BANNER
echo -e "${RESET}"

VENV_DIR="$HOME/.flash_venv"
DATA_DIR="$HOME/.flash_loan_engine"
ENV_DIR="$HOME/jdl"
ENV_FILE="$ENV_DIR/.env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

step() { echo -e "${CYAN}▶ $1${RESET}"; }
ok()   { echo -e "${GREEN}✓ $1${RESET}"; }
warn() { echo -e "${YELLOW}⚠ $1${RESET}"; }
fail() { echo -e "${RED}✗ $1${RESET}"; exit 1; }

# ── 1. Python check ──────────────────────────────────────────
step "Checking Python version..."
if command -v python3 &>/dev/null; then
  PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  PY_MAJOR=$(echo $PY_VER | cut -d. -f1)
  PY_MINOR=$(echo $PY_VER | cut -d. -f2)
  if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
    ok "Python $PY_VER found"
  else
    fail "Python 3.10+ required, found $PY_VER. Install from python.org"
  fi
else
  fail "python3 not found. Install Python 3.10+ first."
fi

# ── 2. Create virtual environment ────────────────────────────
step "Setting up virtual environment at $VENV_DIR..."
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
  ok "Virtual environment created"
else
  ok "Virtual environment already exists"
fi

source "$VENV_DIR/bin/activate"

# ── 3. Install dependencies ───────────────────────────────────
step "Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "$SCRIPT_DIR/python/requirements_flash.txt"
ok "Dependencies installed"

# ── 4. Create data directory ──────────────────────────────────
step "Creating data directory at $DATA_DIR..."
mkdir -p "$DATA_DIR"
ok "Data directory ready"

# ── 5. Environment file ───────────────────────────────────────
step "Setting up environment file..."
mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
  cp "$SCRIPT_DIR/.env.template" "$ENV_FILE"
  warn "Created $ENV_FILE from template — EDIT THIS FILE before running!"
  echo -e "  ${YELLOW}Required: PRIVATE_KEY, RPC_URL, FLASHBOTS_AUTH_KEY${RESET}"
  echo -e "  ${YELLOW}Required after deploy: FLASH_CONTRACT_ADDRESS${RESET}"
else
  ok "$ENV_FILE already exists"
fi

# ── 6. Done ───────────────────────────────────────────────────
echo
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════╗${RESET}"
echo -e "${GREEN}${BOLD}║     Setup Complete!                  ║${RESET}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════╝${RESET}"
echo
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "  1. Edit your config: ${CYAN}nano $ENV_FILE${RESET}"
echo -e "  2. Deploy contracts: see ${CYAN}README_FLASH.md${RESET}"
echo -e "  3. Start engine:     ${CYAN}bash setup.sh run${RESET}"
echo

# ── Optional: run or test ─────────────────────────────────────
if [ "$1" = "run" ]; then
  echo -e "${CYAN}Launching Flash Loan Engine...${RESET}"
  python3 "$SCRIPT_DIR/python/flash_loan_engine.py"
elif [ "$1" = "test" ]; then
  echo -e "${CYAN}Running test suite...${RESET}"
  python3 "$SCRIPT_DIR/python/test_flash_engine.py"
fi
