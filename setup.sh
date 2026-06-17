#!/usr/bin/env bash
# setup.sh — One-command setup for Flash Zero Gas System
# Usage: bash setup.sh
set -euo pipefail

RED='\033[31m'; GRN='\033[32m'; YLW='\033[33m'
CYN='\033[36m'; B='\033[1m';   R='\033[0m'

echo -e "${CYN}${B}"
echo " ┌─────────────────────────────────────────────────┐"
echo " │  Flash Zero Gas — Setup                       │"
echo " └─────────────────────────────────────────────────┘"
echo -e "${R}"

# ── 1. Python version check
echo -e "${B}[1/6] Checking Python version…${R}"
PYV=$(python3 --version 2>&1 | awk '{print $2}')
REQ="3.9"
if python3 -c "import sys; exit(0 if sys.version_info>=(3,9) else 1)"; then
  echo -e "  ${GRN}✓ Python $PYV${R}"
else
  echo -e "  ${RED}✗ Python $PYV < $REQ required. Install Python 3.9+.${R}"
  exit 1
fi

# ── 2. Create virtualenv
echo -e "${B}[2/6] Creating virtual environment…${R}"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo -e "  ${GRN}✓ .venv created${R}"
else
  echo -e "  ${YLW}~ .venv already exists${R}"
fi

source .venv/bin/activate

# ── 3. Install dependencies
echo -e "${B}[3/6] Installing dependencies…${R}"
pip install --quiet --upgrade pip
pip install --quiet -r python/requirements_flash.txt
echo -e "  ${GRN}✓ Dependencies installed${R}"

# ── 4. Create .env if not exists
echo -e "${B}[4/6] Setting up .env…${R}"
ENV_FILE="$HOME/jdl/.env"
mkdir -p "$HOME/jdl"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" << 'EOF'
# Flash Zero Gas — Environment Variables
# Fill in all values before running.

WALLET_ADDRESS=0xYourWalletAddressHere
PRIVATE_KEY=your_private_key_hex_no_0x
ALCHEMY_ARB_KEY=your_alchemy_arbitrum_key
ALCHEMY_ETH_KEY=your_alchemy_eth_key
FLASHBOTS_SECRET=your_flashbots_signing_key
FLASH_CONTRACT_ADDRESS=0xDeployedFlashZeroGasAddress
PAYMASTER_ADDRESS=0xDeployedProfitPaymasterAddress
GELATO_API_KEY=optional_gelato_key
BICONOMY_API_KEY=optional_biconomy_key
EOF
  echo -e "  ${GRN}✓ Created $ENV_FILE — fill in your keys before running${R}"
else
  echo -e "  ${YLW}~ $ENV_FILE already exists — skipping${R}"
fi

# ── 5. Create data directory
echo -e "${B}[5/6] Creating data directory…${R}"
mkdir -p "$HOME/.flash_zero_gas"
echo -e "  ${GRN}✓ $HOME/.flash_zero_gas${R}"

# ── 6. Summary
echo -e "${B}[6/6] Setup complete!${R}"
echo ""
echo -e "${B}Next steps:${R}"
echo -e "  1. Edit ${CYN}$ENV_FILE${R} — add your wallet/keys/contract addresses"
echo -e "  2. Deploy contracts: ${CYN}contracts/FlashZeroGas.sol${R} and ${CYN}contracts/ProfitPaymaster.sol${R}"
echo -e "  3. Set FLASH_CONTRACT_ADDRESS in .env"
echo -e "  4. Run: ${GRN}source .venv/bin/activate && python3 python/flash_supervisor.py${R}"
echo -e "     Or run daemon directly: ${GRN}python3 python/flash_loan_zero_gas.py${R}"
echo ""
echo -e "${YLW}All profits auto-reinvest until \$1000 threshold. Zero ETH required to start.${R}"
