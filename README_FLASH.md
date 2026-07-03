# Flash Loan Zero-Gas Automation Engine

> **Zero capital. Zero upfront gas. Autonomous profit reinvestment until $1,000 threshold.**

A production-grade flash loan MEV system that funds its own gas costs from within each transaction using the **Profit-Embedded Gas (PEG)** technique, submitted through Flashbots so miners/builders receive payment from inside the tx — not from your wallet.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Prerequisites](#prerequisites)
3. [Quick Install (One Command)](#quick-install)
4. [Environment Variables — Complete Reference](#environment-variables)
5. [Deploy the Smart Contracts](#deploy-the-smart-contracts)
6. [Verify Contracts on Arbiscan](#verify-contracts)
7. [First Run — Bootstrap with Zero ETH](#first-run-bootstrap)
8. [Running the Engine](#running-the-engine)
9. [Running the Supervisor (Auto-Restart)](#supervisor)
10. [Revenue & Withdrawal](#revenue-and-withdrawal)
11. [Math Algorithms Reference](#math-algorithms)
12. [Strategy Reference](#strategy-reference)
13. [Monitoring & Logs](#monitoring)
14. [Troubleshooting](#troubleshooting)
15. [Architecture Diagram](#architecture)
16. [Security Checklist](#security)

---

## How It Works

### Profit-Embedded Gas (PEG)

Normal flash loans require ETH in your wallet before you can transact. PEG eliminates this:

```
1. Submit tx to Flashbots relay with gasPrice = 0
2. Inside the tx callback:
   a. Borrow WETH via flash loan (Aave/Balancer/Morpho — 0% or 0.09% fee)
   b. Execute arbitrage → earn profit
   c. Unwrap a slice of profit to ETH
   d. Transfer ETH to block.coinbase (the builder)
   e. Repay flash loan principal
3. Builder includes tx because they got paid from INSIDE it
4. Net result: your wallet spent $0 in gas
```

### Revenue Model

| Allocation | Amount | Purpose |
|---|---|---|
| Gas Reserve | 10% of profit | Auto-funds future PEG payments |
| Builder Fee | 5% of profit | Pays Flashbots builder |
| Reinvestment | 85% of profit | Compounds until $1,000 threshold |
| Withdrawal | $0 until $1,000 reached | On-chain enforced |

---

## Prerequisites

### System Requirements

| Component | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu 20.04 / Termux / UserLAnd | Ubuntu 22.04 LTS |
| Python | 3.10+ | 3.11+ |
| RAM | 512 MB | 2 GB |
| Disk | 500 MB | 2 GB |
| Network | Any | Low-latency VPS near Arbitrum RPC |

### Accounts & Keys You Will Need

Before installing, gather these. Instructions for each are in the [Environment Variables](#environment-variables) section.

- [ ] An Ethereum wallet private key (NEW wallet recommended)
- [ ] An Alchemy (or Infura) API key for Arbitrum RPC
- [ ] A Flashbots auth private key (can be same wallet or separate)
- [ ] (Optional) Gelato relay API key for first-execution bootstrap
- [ ] (Optional) Biconomy dashboard API key for gasless meta-tx

### Install System Dependencies

**Ubuntu / Debian:**
```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git curl
```

**Termux (Android):**
```bash
pkg update && pkg install python git curl
```

**macOS:**
```bash
brew install python3 git
```

### Install Foundry (for contract deployment)

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

Verify:
```bash
forge --version   # should show 0.2.x or newer
cast --version
```

---

## Quick Install

```bash
# 1. Clone the repository
git clone https://github.com/flipflowglobal/jdl-production-core.git
cd jdl-production-core
git checkout flashloan

# 2. Run the automated setup
bash setup.sh
```

The setup script will:
- Check Python version
- Create a virtual environment at `~/.flash_venv/`
- Install all Python dependencies
- Create the data directory at `~/.flash_loan_engine/`
- Copy `.env.template` → `~/jdl/.env` (if it doesn't exist)
- Print next steps

After setup, **edit your .env file** (see below), then:
```bash
bash setup.sh run
```

---

## Environment Variables

Your config file lives at `~/jdl/.env`. Every variable is documented here.

### How to get each key

---

### PRIVATE_KEY
```
PRIVATE_KEY=0xabc123...your64hexchars
```
**What it is:** The private key of the wallet that owns your deployed `FlashZeroGas` contract.

**How to get it:**
1. Create a **NEW dedicated wallet** — never reuse your main wallet
2. MetaMask → Account menu → Account details → Export private key
3. Or via CLI: `cast wallet new` (Foundry)

**Security:** This key can call `withdrawToken` once $1,000 threshold is reached. Keep it secret. Use a hardware wallet for large amounts.

**Format:** `0x` followed by 64 hex characters

---

### RPC_URL
```
RPC_URL=https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY_HERE
```
**What it is:** Your Arbitrum mainnet JSON-RPC endpoint.

**How to get it:**
1. Go to [alchemy.com](https://www.alchemy.com)
2. Create a free account → Create App → Network: **Arbitrum One**
3. Copy the HTTPS URL from the dashboard

**Free tier:** 300M compute units/month — sufficient for this engine.

**Alternatives:**
- Infura: `https://arbitrum-mainnet.infura.io/v3/YOUR_KEY`
- QuickNode: `https://your-endpoint.arbitrum-mainnet.quiknode.pro/YOUR_KEY/`
- Public (slower): `https://arb1.arbitrum.io/rpc`

---

### FLASHBOTS_AUTH_KEY
```
FLASHBOTS_AUTH_KEY=0xdeadbeef...your64hexchars
```
**What it is:** A private key used to sign Flashbots bundle submissions. This does NOT need ETH — it's only for authentication with the Flashbots relay.

**How to get it:**
1. Generate a fresh key: `cast wallet new`
2. Copy the private key output
3. You do NOT need to fund this wallet

**What it does:** The Flashbots relay uses this signature to identify your bundles and build your reputation score. A good reputation = higher inclusion probability.

---

### FLASHBOTS_RELAY_URL
```
FLASHBOTS_RELAY_URL=https://relay.flashbots.net
```
**What it is:** The Flashbots MEV relay endpoint.

**For Arbitrum:** Flashbots relay works on Ethereum mainnet. For Arbitrum, use the Arbitrum sequencer or a compatible relay:
- Arbitrum: `https://arb1.arbitrum.io/rpc` (sequencer, no bundle support)
- Flashbots Protect (works on Arb): `https://rpc.flashbots.net/fast`
- Bloxroute on Arbitrum: check bloxroute.com for current endpoint

**Recommendation:** Start with Flashbots Protect RPC for Arbitrum as a bundler-compatible endpoint.

---

### FLASH_CONTRACT_ADDRESS
```
FLASH_CONTRACT_ADDRESS=0x0000000000000000000000000000000000000000
```
**What it is:** The deployed address of your `FlashZeroGas.sol` contract.

**How to get it:** This is filled in AFTER you deploy (see [Deploy section](#deploy-the-smart-contracts)).

**Leave as zeros** until you deploy.

---

### PAYMASTER_ADDRESS
```
PAYMASTER_ADDRESS=0x0000000000000000000000000000000000000000
```
**What it is:** The deployed address of your `ProfitPaymaster.sol` (EIP-4337 paymaster).

**How to get it:** Filled in after deploying `ProfitPaymaster.sol`.

**Optional:** The engine runs without a paymaster using PEG mode. Paymaster adds EIP-4337 Account Abstraction as a secondary gas strategy.

---

### AAVE_POOL_ADDRESS
```
AAVE_POOL_ADDRESS=0x794a61358D6845594F94dc1DB02A252b5b4814aD
```
**What it is:** The Aave V3 Pool contract address on Arbitrum One.

**This is a fixed address** — do not change it unless Aave deploys a new version.

| Network | Aave V3 Pool Address |
|---|---|
| Arbitrum One | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` |
| Ethereum | `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2` |
| Optimism | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` |
| Base | `0xA238Dd80C259a72e81d7e4664a9801593F98d1c5` |
| Polygon | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` |

---

### BALANCER_VAULT_ADDRESS
```
BALANCER_VAULT_ADDRESS=0xBA12222222228d8Ba445958a75a0704d566BF2C8
```
**What it is:** Balancer V2 Vault on Arbitrum. Balancer flash loans have **0% fee** — preferred protocol.

**Fixed address** — same across all Balancer V2 deployments:
- Arbitrum, Ethereum, Optimism, Polygon, Base: `0xBA12222222228d8Ba445958a75a0704d566BF2C8`

---

### WETH_ADDRESS
```
WETH_ADDRESS=0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
```
**What it is:** Wrapped ETH contract on Arbitrum One.

| Network | WETH Address |
|---|---|
| Arbitrum One | `0x82aF49447D8a07e3bd95BD0d56f35241523fBab1` |
| Ethereum | `0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2` |
| Optimism | `0x4200000000000000000000000000000000000006` |
| Base | `0x4200000000000000000000000000000000000006` |

---

### USDC_ADDRESS
```
USCD_ADDRESS=0xaf88d065e77c8cC2239327C5EDb3A432268e5831
```
**What it is:** Native USDC on Arbitrum One (not bridged).

| Network | USDC Address |
|---|---|
| Arbitrum One (native) | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` |
| Ethereum | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` |

---

### UNISWAP_V3_FACTORY
```
UNISWAP_V3_FACTORY=0x1F98431c8aD98523631AE4a59f267346ea31F984
```
**What it is:** Uniswap V3 Factory (same address on all chains).

---

### GELATO_RELAY_URL
```
GELATO_RELAY_URL=https://relay.gelato.digital/relays/v2/call-with-sync-fee
```
**What it is:** Gelato's gasless relay endpoint. Used for **bootstrap** — the very first execution when the contract has no ETH for PEG.

**Gelato API Key (optional):**
```
GELATO_API_KEY=your_key_here
```
1. Go to [app.gelato.network](https://app.gelato.network)
2. Create account → API Keys → Generate
3. The free tier covers bootstrap executions

---

### CHAIN_ID
```
CHAIN_ID=42161
```
**What it is:** EVM chain ID.

| Network | Chain ID |
|---|---|
| Arbitrum One | 42161 |
| Ethereum | 1 |
| Optimism | 10 |
| Base | 8453 |
| Polygon | 137 |

---

### MEV_SHARE_URL
```
MEV_SHARE_URL=https://mev-share.flashbots.net
```
**What it is:** Flashbots MEV-Share SSE stream. The engine subscribes to this to detect backrun opportunities.

**No key needed** — it's a public stream.

---

### MIN_PROFIT_USD6
```
MIN_PROFIT_USD6=500000
```
**What it is:** Minimum profit per flash loan execution in USDC units (6 decimals). `500000` = $0.50.

**Tune this based on gas costs.** On Arbitrum, $0.50 minimum is a safe starting point.

---

### BUILDER_FEE_BPS
```
BUILDER_FEE_BPS=500
```
**What it is:** Basis points of profit paid to the block builder as the embedded gas fee. `500` = 5%.

**Range:** 300–800 bps. Higher = more likely inclusion. Lower = more profit kept.

---

### GAS_RESERVE_BPS
```
GAS_RESERVE_BPS=1000
```
**What it is:** Basis points of profit set aside in the contract as a gas reserve for self-funding. `1000` = 10%.

---

### WITHDRAW_THRESHOLD_USD6
```
WITHDRAW_THRESHOLD_USD6=1000000000
```
**What it is:** Minimum accumulated profit (USDC-6 decimals) before withdrawal is allowed. `1000000000` = $1,000.

**This is enforced on-chain** — the contract will revert `withdrawToken()` calls until this threshold is reached.

---

### LOG_LEVEL
```
LOG_LEVEL=INFO
```
**Options:** `DEBUG`, `INFO`, `WARNING`, `ERROR`

---

### Complete .env.template

```bash
# ─────────────────────────────────────────────
# FLASH LOAN ENGINE — ENVIRONMENT CONFIGURATION
# ─────────────────────────────────────────────
# Copy this to ~/jdl/.env and fill in all values
# NEVER commit this file to git

# ── WALLET ──────────────────────────────────
# Your executor wallet private key (0x + 64 hex chars)
# Generate: cast wallet new
PRIVATE_KEY=0x

# ── RPC ENDPOINTS ───────────────────────────
# Arbitrum mainnet RPC (Alchemy recommended)
# Get at: alchemy.com → Create App → Arbitrum One
RPC_URL=https://arb-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY

# ── FLASHBOTS ───────────────────────────────
# Auth key for Flashbots relay (no ETH needed)
# Generate: cast wallet new
FLASHBOTS_AUTH_KEY=0x
FLASHBOTS_RELAY_URL=https://relay.flashbots.net

# ── DEPLOYED CONTRACTS ──────────────────────
# Fill in after running: forge script ...
FLASH_CONTRACT_ADDRESS=0x0000000000000000000000000000000000000000
PAYMASTER_ADDRESS=0x0000000000000000000000000000000000000000

# ── PROTOCOL ADDRESSES (Arbitrum One) ───────
AAVE_POOL_ADDRESS=0x794a61358D6845594F94dc1DB02A252b5b4814aD
BALANCER_VAULT_ADDRESS=0xBA12222222228d8Ba445958a75a0704d566BF2C8
WETH_ADDRESS=0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
USDC_ADDRESS=0xaf88d065e77c8cC2239327C5EDb3A432268e5831
UNISWAP_V3_FACTORY=0x1F98431c8aD98523631AE4a59f267346ea31F984

# ── GELATO BOOTSTRAP (optional) ─────────────
# Get at: app.gelato.network → API Keys
GELATO_RELAY_URL=https://relay.gelato.digital/relays/v2/call-with-sync-fee
GELATO_API_KEY=

# ── NETWORK ─────────────────────────────────
CHAIN_ID=42161
MEV_SHARE_URL=https://mev-share.flashbots.net

# ── ENGINE PARAMETERS ───────────────────────
# Minimum profit per execution ($0.50 = 500000)
MIN_PROFIT_USD6=500000
# Builder gas fee: 5% of profit
BUILDER_FEE_BPS=500
# Gas reserve: 10% of profit
GAS_RESERVE_BPS=1000
# Withdrawal lock until $1000 reached
WITHDRAW_THRESHOLD_USD6=1000000000

# ── LOGGING ─────────────────────────────────
LOG_LEVEL=INFO
```

---

## Deploy the Smart Contracts

### Step 1: Install Foundry and dependencies

```bash
# Install Foundry (if not already installed)
curl -L https://foundry.paradigm.xyz | bash && foundryup

# Install OpenZeppelin (needed by ProfitPaymaster)
forge install OpenZeppelin/openzeppelin-contracts --no-commit
```

### Step 2: Set up environment for deployment

```bash
export PRIVATE_KEY=0xYOUR_PRIVATE_KEY
export RPC_URL=https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY
export AAVE_POOL=0x794a61358D6845594F94dc1DB02A252b5b4814aD
export BALANCER_VAULT=0xBA12222222228d8Ba445958a75a0704d566BF2C8
export WETH=0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
```

### Step 3: Deploy FlashZeroGas.sol

```bash
cd jdl-production-core/contracts

forge create FlashZeroGas \
  --rpc-url $RPC_URL \
  --private-key $PRIVATE_KEY \
  --constructor-args $AAVE_POOL $BALANCER_VAULT $WETH \
  --broadcast \
  --verify \
  --verifier etherscan \
  --etherscan-api-key YOUR_ARBISCAN_KEY
```

**Expected output:**
```
Deployer: 0xYourAddress
Deployed to: 0xNEW_CONTRACT_ADDRESS    ← copy this
Transaction hash: 0x...
```

Copy the `Deployed to` address and add it to your `.env`:
```bash
FLASH_CONTRACT_ADDRESS=0xNEW_CONTRACT_ADDRESS
```

### Step 4: Deploy ProfitPaymaster.sol (optional)

```bash
# You need an ERC-4337 EntryPoint address
export ENTRY_POINT=0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789

forge create ProfitPaymaster \
  --rpc-url $RPC_URL \
  --private-key $PRIVATE_KEY \
  --constructor-args $ENTRY_POINT $FLASH_CONTRACT_ADDRESS \
  --broadcast
```

Copy the deployed address to `.env`:
```bash
PAYMASTER_ADDRESS=0xPAYMASTER_ADDRESS
```

### Step 5: Fund the Paymaster's EntryPoint deposit (if using EIP-4337)

```bash
# Deposit 0.01 ETH into EntryPoint for the paymaster
cast send $ENTRY_POINT \
  "depositTo(address)" $PAYMASTER_ADDRESS \
  --value 0.01ether \
  --private-key $PRIVATE_KEY \
  --rpc-url $RPC_URL
```

### Step 6: Approve flash contract in paymaster

```bash
cast send $PAYMASTER_ADDRESS \
  "setApprovedContract(address,bool)" $FLASH_CONTRACT_ADDRESS true \
  --private-key $PRIVATE_KEY \
  --rpc-url $RPC_URL
```

---

## Verify Contracts

### Get an Arbiscan API Key

1. Go to [arbiscan.io](https://arbiscan.io)
2. Create an account → Profile → API Keys → Add
3. Free tier: 5 calls/sec, sufficient

### Verify FlashZeroGas

```bash
forge verify-contract \
  --chain-id 42161 \
  --num-of-optimizations 200 \
  --compiler-version v0.8.20 \
  $FLASH_CONTRACT_ADDRESS \
  contracts/FlashZeroGas.sol:FlashZeroGas \
  --etherscan-api-key YOUR_ARBISCAN_KEY
```

### Verify ProfitPaymaster

```bash
forge verify-contract \
  --chain-id 42161 \
  --num-of-optimizations 200 \
  --compiler-version v0.8.20 \
  $PAYMASTER_ADDRESS \
  contracts/ProfitPaymaster.sol:ProfitPaymaster \
  --etherscan-api-key YOUR_ARBISCAN_KEY
```

---

## First Run — Bootstrap with Zero ETH

On first execution, the contract has no ETH for PEG. The engine auto-detects this and falls back to **Gelato Free Relay** for the bootstrap transaction:

```
Gelato sponsors gas → First flash loan executes → Profit generated
→ 10% goes to gasReserve → Contract now self-funds future PEG payments
```

**To trigger bootstrap manually:**

```bash
source ~/.flash_venv/bin/activate
flashloan
# Select [1] Start Daemon → the UCB1 bandit selects GelatoFreeRelay for round 1
```

**What happens after first profit:**
- `gasReserve` in the contract accumulates ETH
- All subsequent transactions use PEG (self-funded)
- Gelato relay is no longer needed

---

## Running the Engine

### One-time install (makes the `flashloan` command available anywhere)

```bash
source ~/.flash_venv/bin/activate
pip install -e python/          # installs the jdl_flash package + the flashloan command
```

### Start the interactive terminal UI

```bash
flashloan                       # runs from ANY directory after the install above
# equivalent: python3 -m jdl_flash.flash_loan_engine
```

You will see the FLASH ASCII banner and this menu:

```
  ╔══════════════════════════════════════════════╗
  ║  FLASH LOAN ENGINE  │  Arbitrum One          ║
  ║  Revenue: [$   0.00 / $1000.00] ██░░░░  0%  ║
  ║  Executions: 0      │  Contract: 0x0000...   ║
  ╚══════════════════════════════════════════════╝

  [1] Start Daemon (auto-cycle)
  [2] Scan Opportunities Now
  [3] Gas Strategies Status
  [4] Revenue & Profit Tracker
  [5] Algorithm Outputs
  [6] System Status
  [7] Configuration
  [8] Run Tests

  Select option >
```

### Menu Options

| Option | What it does |
|---|---|
| `[1]` Start Daemon | Launches the async scan-execute-reinvest loop |
| `[2]` Scan Now | One-shot opportunity scan with algorithm output |
| `[3]` Gas Strategies | Shows UCB1 bandit scores for all 7 gas strategies |
| `[4]` Revenue | SQLite-backed profit history, progress bar |
| `[5]` Algorithms | Live GARCH/Kalman/OU/Kelly outputs |
| `[6]` Status | Contract state, gas reserve, chain info |
| `[7]` Config | Edit .env values without leaving the UI |
| `[8]` Tests | Run the full 79-test suite inline |

---

## Supervisor

The supervisor runs the engine as a managed background process with auto-restart:

```bash
source ~/.flash_venv/bin/activate
python3 python/flash_supervisor.py
```

**Features:**
- Health check every 30 seconds
- Auto-restarts on crash (max 20 restarts/hour)
- Alerts when withdrawal threshold is reached
- PID file at `~/.flash_loan_engine/flash_engine.pid`
- Log file at `~/.flash_loan_engine/supervisor.log`

### Run as a system service (optional)

Create `/etc/systemd/system/flash-engine.service`:

```ini
[Unit]
Description=Flash Loan Zero-Gas Engine
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/path/to/jdl-production-core
ExecStart=/bin/bash -c 'source ~/.flash_venv/bin/activate && python3 python/flash_supervisor.py'
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable flash-engine
sudo systemctl start flash-engine
sudo systemctl status flash-engine
```

---

## Revenue and Withdrawal

### Tracking Progress

Revenue is tracked in SQLite at `~/.flash_loan_engine/flash.db`:

```bash
# View raw data
sqlite3 ~/.flash_loan_engine/flash.db \
  "SELECT timestamp, profit_usd6/1e6 as profit_usd, tx_hash FROM revenue ORDER BY timestamp DESC LIMIT 20;"
```

Or use the engine UI → `[4] Revenue`.

### Withdrawing After $1,000

Once `totalProfitRaw >= 1_000_000_000` (USDC-6), call:

```bash
# Withdraw USDC
cast send $FLASH_CONTRACT_ADDRESS \
  "withdrawToken(address,uint256)" \
  $USDC_ADDRESS 500000000 \
  --private-key $PRIVATE_KEY \
  --rpc-url $RPC_URL

# Withdraw ETH (gas reserve)
cast send $FLASH_CONTRACT_ADDRESS \
  "withdrawETH(uint256)" 0.01ether \
  --private-key $PRIVATE_KEY \
  --rpc-url $RPC_URL
```

### Emergency Withdrawal (bypass threshold)

```bash
cast send $FLASH_CONTRACT_ADDRESS \
  "emergencyWithdrawToken(address,uint256)" \
  $USDC_ADDRESS 0 \
  --private-key $PRIVATE_KEY \
  --rpc-url $RPC_URL
```

> Emergency withdrawal sends ALL of the token balance.

---

## Math Algorithms

All algorithms run inline in Python — no external ML libraries needed.

| Algorithm | Purpose | Key Parameters |
|---|---|---|
| **GARCH(1,1)** | Volatility gate — only trade in favorable vol regimes | ω=1e-6, α=0.1, β=0.85 |
| **Kalman Filter** | Price noise reduction, velocity estimation | Q=1e-4, R=0.1 |
| **Ornstein-Uhlenbeck** | Mean-reversion timing, optimal entry | θ=0.5, μ=0, σ=0.02 |
| **Kelly Criterion** | Position sizing, half-Kelly for regime safety | Half-Kelly = kelly/2 |
| **Newton-Raphson AMM** | Precise output amount from AMM curves | 5 iterations |
| **Bellman-Ford** | Negative-cycle arbitrage detection on DEX graph | Nodes = token pairs |
| **UCB1 Bandit** | Gas strategy selection (exploration vs exploitation) | c=2.0 |
| **Q-Learning** | State-action policy for trade decisions | α=0.1, γ=0.95, ε=0.1 |
| **Fourier DFT** | Cycle detection in price series | Top 3 frequency components |
| **EMA Weights** | Exponential moving average signal | α=0.15 |
| **Z-Score Detector** | Spread anomaly detection | Window=20, threshold=2σ |

---

## Strategy Reference

The engine has 7 zero-gas execution strategies. UCB1 learns which performs best:

| Strategy | Gas Cost | How It Works |
|---|---|---|
| **FlashbotsPEG** | 0 (embedded) | `block.coinbase.transfer(fee)` inside callback, submitted via Flashbots |
| **MEVShareBackrun** | 0 (share) | Subscribes to MEV-Share stream, backruns pending txs |
| **GelatoFreeRelay** | 0 (sponsored) | Gelato pays gas, deducted from callback profit (bootstrap only) |
| **BiconomyMetaTx** | 0 (meta-tx) | EIP-2771 gasless via Biconomy relayer |
| **EIP4337Paymaster** | 0 (paymaster) | ProfitPaymaster covers gas only when profit projection passes threshold |
| **RecursiveFlashStack** | 0 (embedded) | Borrow WETH → unwrap → fund gas → arb → wrap → repay, all atomic |
| **TWAPLagArb** | 0 (embedded) | Exploit 30-min TWAP oracle lag vs spot when `|spot_tick − twap_tick| > 50` |

---

## Monitoring

### Live Logs

```bash
# Supervisor log
tail -f ~/.flash_loan_engine/supervisor.log

# Engine output (if running in screen/tmux)
screen -r flash
```

### Run in tmux (recommended for servers)

```bash
tmux new-session -s flash
source ~/.flash_venv/bin/activate
flashloan
# Detach: Ctrl+B then D
# Reattach: tmux attach -t flash
```

### Check contract state on-chain

```bash
# Total profit accumulated
cast call $FLASH_CONTRACT_ADDRESS "totalProfitRaw()" --rpc-url $RPC_URL

# Gas reserve balance
cast call $FLASH_CONTRACT_ADDRESS "gasReserve()" --rpc-url $RPC_URL

# Execution count
cast call $FLASH_CONTRACT_ADDRESS "executionCount()" --rpc-url $RPC_URL

# USDC balance in contract
cast call $USDC_ADDRESS "balanceOf(address)" $FLASH_CONTRACT_ADDRESS --rpc-url $RPC_URL
```

### Arbiscan Contract Page

After deployment and verification:
```
https://arbiscan.io/address/YOUR_FLASH_CONTRACT_ADDRESS
```

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'web3'`

```bash
source ~/.flash_venv/bin/activate
pip install -r python/requirements_flash.txt
```

### `Error: execution reverted`

Common causes:
- Contract USDC balance insufficient to repay flash loan — check pool liquidity
- `totalProfitRaw < withdrawThresholdUSD6` — threshold not reached yet
- Slippage too high — reduce `MAX_SLIPPAGE_BPS` in config

### `Connection refused` on RPC

- Check your Alchemy key is valid and for Arbitrum network
- Try the public RPC as fallback: `https://arb1.arbitrum.io/rpc`

### `Insufficient funds for gas`

On first run, this means bootstrap hasn't happened yet:
- Ensure `GELATO_API_KEY` is set
- Or manually send 0.001 ETH to the contract for bootstrap:
  ```bash
  cast send $FLASH_CONTRACT_ADDRESS --value 0.001ether --private-key $PRIVATE_KEY --rpc-url $RPC_URL
  ```

### Flashbots bundles not included

- Increase `BUILDER_FEE_BPS` (try 700–800)
- Check your bundle simulation via: `eth_callBundle` on Flashbots relay
- Ensure `FLASHBOTS_AUTH_KEY` is correctly formatted (0x + 64 hex)

### Tests fail with `ImportError`

```bash
# Run math-only tests (no web3 needed)
python3 -c "
import sys
sys.modules['web3'] = type(sys)('web3')
exec(open('python/test_flash_engine.py').read())
"
```

### `sqlite3.OperationalError: unable to open database`

```bash
mkdir -p ~/.flash_loan_engine
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 flash_loan_engine.py                │
│          (Termux UI  ·  Menu  ·  Orchestrator)      │
└─────┬────────────────┬──────────────────┬───────────┘
      │                │                  │
      ▼                ▼                  ▼
┌──────────┐  ┌─────────────────┐  ┌─────────────┐
│ Opport.  │  │   FlashDaemon   │  │  Revenue    │
│ Scanner  │  │  (async cycle)  │  │  Tracker    │
│ GARCH+OU │  │  UCB1 dispatch  │  │  (SQLite)   │
└──────────┘  └────────┬────────┘  └─────────────┘
                       │
                       ▼
             ┌─────────────────┐
             │   gas_kernel.py │
             │  7 strategies   │
             │  UCB1 bandit    │
             └────────┬────────┘
                      │
           ┌──────────┼──────────┐
           ▼          ▼          ▼
     Flashbots    Gelato     MEV-Share
       Relay      Relay       Stream
           │
           ▼
  ┌─────────────────────┐
  │  FlashZeroGas.sol   │  ← on Arbitrum One
  │  PEG + flash loans  │
  │  Aave/Balancer/Uni  │
  └──────────┬──────────┘
             │
             ▼
  ┌─────────────────────┐
  │  ProfitPaymaster    │  ← EIP-4337 (optional)
  │  .sol               │
  └─────────────────────┘

             flash_supervisor.py
             └── health checks, auto-restart
```

---

## Security

- [ ] Use a **dedicated wallet** — never reuse your main wallet as `PRIVATE_KEY`
- [ ] Never commit `.env` or `~/jdl/.env` to git (`.gitignore` already covers this)
- [ ] The `emergencyWithdrawToken` function has no threshold — protect `PRIVATE_KEY`
- [ ] Verify contract source on Arbiscan before trusting it holds funds
- [ ] Set `MIN_PROFIT_USD6` above your expected gas cost to avoid negative-profit executions
- [ ] Monitor `supervisord.log` — repeated crashes may indicate an on-chain attack
- [ ] After reaching $1,000, consider migrating to a multisig for withdrawal

### Swarm mode — maximum parallel scanning

`flashloan` → `[s]` runs **N concurrent scan workers**, each sweeping a *disjoint* slice
of the route universe (token pairs × Uniswap V3 fee tiers), so N workers cover N× the
market per tick with zero overlap. Each worker feeds its real quotes to the Rust hot-path
(`jdl_native`) for fast cycle detection; a shared dedup set stops two workers chasing the
same loop (which would just make one revert). Set `SWARM_WORKERS=auto` (CPU cores),
`max` (4×cores≤32), or an explicit count.

**The scanning is genuinely parallel**, not just cosmetic async bookkeeping: worker
scan/execute calls are blocking network I/O (web3 HTTP calls), and `BotSwarm` runs each
one in its own thread (`asyncio.to_thread`), so N workers' RPC round-trips actually
overlap in wall-clock time — verified in `test_bot_swarm.py` (6 blocking workers finish
in ~1 round-trip, not 6×).

**Genuinely parallel execution** (not just scanning) requires multiple wallets — a single
wallet's transactions are always serialized on-chain by nonce order no matter how many
workers find opportunities. Configure `SWARM_KEYS` + `SWARM_CONTRACTS` (see
`.env.template`): each wallet must own its **own** deployed `NexusFlashReceiver`
instance (deploy one per wallet with `deploy_receiver.py`/`deploy_gelato.py`, passing
that wallet as owner — no Solidity change needed, since a contract just has exactly one
owner). Leave both unset to keep the single-wallet fallback (unchanged default
behavior). A per-wallet lock serializes nonce-fetch+sign+broadcast for that wallet if
more scan workers than wallets are configured, so two threads never race one wallet's
nonce.

Swarm scan is dry (scan-only) unless `LIVE_EXECUTION=1`.

### Stuck-funds check — `flashloan` → `[r]`

`NexusFlashReceiver` sweeps 100% of profit to the owner on every call, so it should
hold ~0 of every token between trades. `[r]` reads the contract's live on-chain
balance of each tracked token and flags anything above a small dust threshold as
stuck (a partially-failed sequence) — that's the signal to reach for
`rescueTokens()`/`rescueETH()`. Read-only; makes no transactions.

### Gasless mode (Gelato Relay) — zero-ETH wallet

Set `GELATO_ENABLED=1` in `~/jdl/.env` to run without ever holding ETH. Every arbitrage
is submitted through Gelato Relay (ERC-2771 `callWithSyncFee`): Gelato pays the Arbitrum
gas and is reimbursed from the trade's profit (in the loan asset), atomically. The fee is
bounded by an owner-signed `maxFee`, so it can never exceed the profit — if it would, the
trade reverts and nothing moves. One-time gasless deploy: `python3 -m jdl_flash.deploy_gelato`
(needs `GELATO_SPONSOR_API_KEY` + ~$1 USDC in Gelato 1Balance). **Dry-run on Arbitrum
Sepolia first** — Gelato sponsors testnet gas for free.

### On-chain / broadcast protections

- Every broadcast runs a **pre-submit `eth_call` simulation** (`NexusExecutor.simulate()`):
  a transaction that would revert is skipped, so no gas is ever spent on a doomed tx.
- The `NexusFlashReceiver` profit invariant is enforced on-chain (`InsufficientProfit`) and
  proven off the example tests by Foundry fuzz + stateful invariant runs — see
  `contracts/README.md` → "Security hardening notes".
- `FlashbotsPEG` is **L1-only by design** (gated to `CHAIN_ID == 1`). Arbitrum has a single
  FIFO sequencer and no public mempool, so there is nothing to front-run; the simulation gate
  above is the practical protection there, not a private relay.

---

## File Reference

```
jdl-production-core/
├── python/
│   ├── flash_loan_engine.py     # Main engine + Termux UI
│   ├── gas_kernel.py            # 7 gas strategies + UCB1
│   ├── flash_supervisor.py      # Process supervisor
│   ├── test_flash_engine.py     # 79-test suite
│   └── requirements_flash.txt  # Python dependencies
├── contracts/
│   ├── FlashZeroGas.sol         # Main flash loan executor
│   └── ProfitPaymaster.sol      # EIP-4337 paymaster
├── .env.template                # Environment variable template
├── setup.sh                     # One-command installer
└── README_FLASH.md              # This file
```

---

*Built for Arbitrum One. Designed for zero capital. Profits compound until $1,000 before any withdrawal is possible.*
