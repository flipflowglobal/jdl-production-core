# Flash Loan Engine

Autonomous zero-gas flash loan arbitrage engine with full Termux/UserLAnd terminal UI.

## Quick Start

```bash
bash setup.sh
# Edit ~/jdl/.env with your keys
# Deploy contracts on Arbitrum, set FLASH_CONTRACT_ADDRESS
python3 python/flash_loan_engine.py
```

## Run Tests

```bash
python3 python/test_flash_engine.py
# Or select [8] inside the engine menu
```

## Architecture

```
flash_supervisor.py          ─ health monitor, auto-restart, withdrawal alerts
  └─ flash_loan_engine.py   ─ main menu-driven engine
       ├─ GARCH(1,1)            volatility gating
       ├─ Kalman Filter         noise-free price tracking
       ├─ Ornstein-Uhlenbeck    spread half-life + reversion probability
       ├─ UCB1 Bandit           gas strategy selection
       ├─ Q-Learning RL         strategy reinforcement optimizer
       ├─ Newton-Raphson AMM    exact slippage calculation
       ├─ Bellman-Ford          triangular arb negative cycle detection
       ├─ Kelly Criterion       half-Kelly position sizing
       ├─ Fourier Cycle         cyclical entry timing
       ├─ EMA Weights           pair performance tracking
       └─ Z-Score Detector      statistical anomaly filtering

gas_kernel.py   ─ 7 zero-gas execution strategies (UCB1 dispatched)
  1. Flashbots PEG          gasPrice=0 + block.coinbase.transfer(fee)
  2. MEV-Share Backrun      SSE event stream flash backruns
  3. Gelato Free Relay      bootstrap relay for first execution
  4. Biconomy Meta-Tx       ERC-20 fee relayer
  5. EIP-4337 Paymaster     profit-conditional gas sponsorship
  6. Recursive Flash Stack  WETH flash → ETH → gas → arb → repay
  7. TWAP Lag Arb           30-min oracle lag exploitation

contracts/FlashZeroGas.sol      ─ on-chain arb + PEG payment
contracts/ProfitPaymaster.sol   ─ EIP-4337 paymaster
```

## Zero-Gas Core (PEG)

```solidity
// Inside flash callback — after profit captured:
payable(block.coinbase).transfer(builderFee);
// Submit with gasPrice=0 via Flashbots — builder still includes it.
```

## Revenue

- 100% reinvest until **$1,000** reached  
- 10% per trade → `gasReserve` (self-funding)  
- 5% per trade → block builder (PEG fee)  
- After $1k: call `withdrawToken()` on contract

## Menu

```
[1] Start Automation Engine
[2] Scan for Opportunities
[3] Gas Strategy Status
[4] Revenue Log
[5] Algorithm Dashboard
[6] System Status
[7] Configuration
[8] Run Tests
[0] Exit
```
