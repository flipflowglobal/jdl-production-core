# JDL Flash-Loan Core

A self-contained **flash-loan arbitrage system for Arbitrum One**, built to run on **Termux/Android** with stdlib-only Python + a Termux-compatible web3, plus Solidity contracts testable with **Foundry or Hardhat**.

> History: this repo previously also held a Node.js API server, a Rust hot-path, and a
> Python AI ensemble ("Machine B"). That subsystem was removed to make this a single,
> focused Termux flash-loan product. It remains recoverable from git history if ever needed.

![CI](https://github.com/flipflowglobal/jdl-production-core/actions/workflows/ci.yml/badge.svg?branch=main)
![Security](https://github.com/flipflowglobal/jdl-production-core/actions/workflows/security.yml/badge.svg?branch=main)

## CI/CD

- **CI** (`ci.yml`, on every push/PR) — four jobs gate merges: **python** (flash engine,
  swarm, wallet-lanes, native-binding test suites), **rust** (`cargo test` +
  `cargo clippy -D warnings` for `rust/hotpath`), **node** (`npm test`), and **solidity**
  (Hardhat compile with solc 0.8.20; `continue-on-error` so a transient toolchain fetch
  doesn't fail the pipeline).
- **Security** (`security.yml`, on push/PR + a weekly Monday cron) — advisory dependency
  audits (`pip-audit`, `cargo-audit`, `npm audit`) and Slither static analysis, none of
  which block merges. The exception is **secret-scan** (gitleaks), which gates on
  push/PR by scanning only the commits just introduced — a leaked credential in a repo
  controlling a live, funded contract is always worth blocking on. The weekly cron run
  additionally sweeps full git history as a non-blocking reminder (this repo has one
  known pre-existing leaked credential pending rotation, so a full-history scan can't
  gate without going permanently red).
- **Dependabot** opens weekly dependency-update PRs, one day per ecosystem (pip, cargo,
  npm × node/contracts, GitHub Actions).
- **Release** (`release.yml`, only on `v*` tag push) builds and packages artifacts
  (Rust hot-path binary, Node source bundle, compiled contract ABI/bytecode) and attaches
  them to a GitHub Release. It never deploys contracts and never touches the live bot —
  deployment stays a deliberate, manual step performed by a human outside CI, unchanged.

Only `actions/*`-authored GitHub Actions are used across these workflows (org policy);
any other tooling is installed directly via pip/cargo/npm/curl.

## Architecture

```
┌──────────────────────────────────────────────────┐
│   python/jdl_flash/  —  Termux engine (package)    │
│   flash_loan_engine.py  · terminal UI, scan/exec   │
│   + advanced toolkit (real quotes only):           │
│     advanced_math · pattern_recognition            │
│     market_analysis · prediction · loan_optimizer  │
│     triangular_scanner · bot_swarm · realness_guard│
│   run anywhere:  pip install -e python/ → flashloan │
├──────────────────────────────────────────────────┤
│   contracts/  —  Solidity (Foundry + Hardhat)      │
│   · NexusFlashReceiver.sol — Aave V3 flash loan    │
│   · ArbitrageLib.sol — SwapStep + helpers          │
│   · FlashZeroGas.sol — zero-upfront-gas variant    │
│   · ProfitPaymaster.sol — EIP-4337 paymaster       │
│   compiles 0/0 · mainnet-fork tested 7/7           │
├──────────────────────────────────────────────────┤
│   revenue_system/  —  on-chain revenue tracking    │
│   python/flash_supervisor.py — auto-restart daemon │
└──────────────────────────────────────────────────┘
```

## Chains

Arbitrum One (primary). Revenue monitor covers multiple chains.

## Quick Start (Termux)

```bash
# Python flash engine
pkg install python git
pip install -e python/        # installs the `flashloan` command
flashloan                     # interactive engine — runs from any directory

# Contracts (Foundry — recommended on Termux)
curl -L https://foundry.paradigm.xyz | bash && foundryup
cd contracts && forge install foundry-rs/forge-std
ARB_RPC_URL=https://arb1.arbitrum.io/rpc forge test --match-path test/NexusFlashReceiver.t.sol -vv
```

Full guides: [`README_FLASH.md`](README_FLASH.md) · [`TERMUX.md`](TERMUX.md) · [`contracts/README.md`](contracts/README.md)

## Tests

| Suite | Command | Result |
|-------|---------|--------|
| Python engine | `python3 -m jdl_flash.test_flash_engine` | 79/79 |
| Contracts (Foundry) | `forge test` | 7/7 fork |
| Contracts (Hardhat) | `npm run test:fork` | 7/7 fork |

## License

Proprietary — Copyright © 2026 Darcel King. All rights reserved.

---

### Revenue Tracking & Monitoring

On-chain revenue tracking, RPC health monitoring, and reconciliation reporting.

**Key modules:**
- `revenue_system/revenue_recording.py` — Record flash arbitrage trades to SQLite
- `revenue_system/chain_monitor_fixed.py` — RPC health daemon (6 chains)
- `revenue_system/revenue_reconciliation.py` — On-chain balance verification
- `database/revenue_schema.sql` — Database schema (7 tables, auto-aggregation triggers)
- `scripts/deploy_termux.sh` — Universal Termux deployment script
