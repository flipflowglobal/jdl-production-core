# JDL Production Core

Multi-chain trading bot with Rust hot-path acceleration, Node.js API server, and Python AI ensemble.

## Architecture

```
┌──────────────────────────────────────────────────┐
│               Node.js API Server                  │
│  Express + TypeScript + PostgreSQL                │
│  Port 8420 · API key auth                         │
├──────────────────────────────────────────────────┤
│            Rust CLI (stdin/stdout JSON-RPC)        │
│  Bellman-Ford arb finder · DEX quoting · EVM      │
│  Flash loan building · 56x faster hot paths       │
├──────────────────────────────────────────────────┤
│            Python Workers (child_process)          │
│  · jdl_engine — state machine executor            │
│  · composite_brain — multi-strategy AI ensemble   │
│  · ppo_engine — reinforcement learning agent       │
│  · thompson_engine — Bayesian bandit explorer      │
│  · ukf_engine — Kalman filter signal estimator    │
│  · cma_es_engine — evolutionary param optimizer   │
├──────────────────────────────────────────────────┤
│            Smart Contracts (Solidity)              │
│  · NexusFlashReceiver.sol — Aave V3 flash loan    │
│  · ArbitrageLib.sol — path splitting lib          │
└──────────────────────────────────────────────────┘
```

## Chains

Ethereum · Arbitrum · Polygon · BSC · Optimism · Avalanche

## Quick Start

```bash
cp .env.example .env   # edit with your RPC keys
./scripts/setup.sh      # install deps, compile, build
docker compose up       # or systemd for production
```

## License

Proprietary — Copyright © 2026 Darcel King. All rights reserved.
