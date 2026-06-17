#!/usr/bin/env bash
# setup.sh — One-command setup for Flash Loan Engine
# Usage: bash setup.sh
set -euo pipefail

RED='\033[31m'; GRN='\033[32m'; YLW='\033[33m'; CYN='\033[36m'; B='\033[1m'; R='\033[0m'

echo -e "${CYN}${B}"
echo "  ┌─────────────────────────────────────────────────┐"
echo "  │  Flash Loan Engine — Setup                      │"
echo "  └─────────────────────────────────────────────────┘"
echo -e "${R}"

# 1. Python
echo -e "${B}[1/5] Checking Python…${R}"
if python3 -c "import sys; exit(0 if sys.version_info>=(3,9) else 1)" 2>/dev/null; then
  echo -e "  ${GRN}✓ $(python3 --version)${R}"
else
  echo -e "  ${RED}✗ Python 3.9+ required${R}"; exit 1
fi

# 2. Virtual environment
echo -e "${B}[2/5] Creating virtual environment…${R}"
if [ ! -d ".venv" ]; then python3 -m venv .venv; echo -e "  ${GRN}✓ .venv created${R}"
else echo -e "  ${YLW}~ .venv already exists${R}"; fi
source .venv/bin/activate

# 3. Dependencies
echo -e "${B}[3/5] Installing dependencies…${R}"
pip install --quiet --upgrade pip
pip install --quiet -r python/requirements_flash.txt
echo -e "  ${GRN}✓ Dependencies installed${R}"

# 4. .env
echo -e "${B}[4/5] Setting up .env…${R}"
ENV=$HOME/jdl/.env; mkdir -p "$HOME/jdl"
if [ ! -f "$ENV" ]; then
  cp .env.template "$ENV"
  echo -e "  ${GRN}✓ Created $ENV — fill in your keys${R}"
else echo -e "  ${YLW}~ $ENV exists — skipping${R}"; fi

# 5. Data directory
echo -e "${B}[5/5] Creating data directory…${R}"
mkdir -p "$HOME/.flash_loan_engine"
echo -e "  ${GRN}✓ $HOME/.flash_loan_engine${R}"

echo ""
echo -e "${B}Setup complete! Next steps:${R}"
echo -e "  1. Edit ${CYN}$HOME/jdl/.env${R} — add wallet/keys/contract addresses"
echo -e "  2. Deploy ${CYN}contracts/FlashZeroGas.sol${R} on Arbitrum"
echo -e "  3. Set FLASH_CONTRACT_ADDRESS in .env"
echo -e "  4. Run: ${GRN}source .venv/bin/activate && python3 python/flash_loan_engine.py${R}"
echo -e "     Or via supervisor: ${GRN}python3 python/flash_supervisor.py${R}"
echo ""
echo -e "${YLW}Zero ETH needed to start. All profits reinvest until \$1000.${R}"
