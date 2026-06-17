# Flash Zero Gas System

Autonomous flash loan arbitrage engine with **zero upfront ETH requirement**.

## Quick Start

```bash
bash setup.sh
# Edit ~/jdl/.env with your keys
# Deploy contracts, set FLASH_CONTRACT_ADDRESS
python3 python/flash_supervisor.py
```

## Architecture

```
flash_supervisor.py    ─ health monitor, auto-restart, withdrawal alerts
  └─ flash_loan_zero_gas.py  ─ main daemon: scan → filter → execute → learn
       ├─ GARCH(1,1)           volatility gating
       ├─ Kalman Filter        noise-free price tracking
       ├─ Ornstein-Uhlenbeck   spread half-life & reversion probability
       ├─ UCB1 Bandit          gas strategy selection
       ├─ Q-Learning           reinforcement-trained strategy optimizer
       ├─ Newton-Raphson AMM   exact slippage calculation
       ├─ Bellman-Ford         triangular arb negative cycle detection
       └─ Kelly Criterion      position sizing with regime conditioning

gas_kernel.py  ─ all 7 zero-gas execution methods
  1. Flashbots PEG        gasPrice=0 + block.coinbase.transfer(fee)
  2. MEV-Share Backrun    SSE event stream backrun bundles
  3. Gelato Free Relay    bootstrap relay (first execution)
  4. Biconomy Meta-Tx     ERC-20 fee relayer
  5. EIP-4337 Paymaster   profit-conditional gas sponsorship
  6. Recursive Flash      WETH flash → ETH → gas → arb → repay
  7. TWAP Lag Arb         30-min oracle lag exploitation

contracts/FlashZeroGas.sol    ─ on-chain arb + PEG payment
contracts/ProfitPaymaster.sol ─ EIP-4337 paymaster
```

## Zero-Gas Mechanism

The core innovation: **Profit-Embedded Gas (PEG)**.

```solidity
// Inside the flash loan callback, after profit is captured:
payable(block.coinbase).transfer(builderFee);
```

The tx is submitted with `gasPrice = 0` to Flashbots relay. The block builder
includes it because `block.coinbase.transfer()` pays them directly from within
the transaction — no upfront ETH needed.

## Revenue Model

- All profits auto-reinvest until **$1000** accumulated
- 10% of each profit goes to `gasReserve` for self-funding
- 5% of each profit pays the block builder (PEG)
- After $1000 threshold: call `withdrawToken()` on contract

## Environment Variables

See `.env.template` for all required and optional keys.
