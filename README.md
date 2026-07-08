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

Full guides: [`README_FLASH.md`](README_FLASH.md) · [`TERMUX.md`](TERMUX.md) · [`docs/TERMUX_WALKTHROUGH.md`](docs/TERMUX_WALKTHROUGH.md) · [`contracts/README.md`](contracts/README.md)

## CLI Command Reference

### `jdl` — unified CLI (`python/jdl_flash/cli.py`)

Installed by `pip install -e python/`. Run `jdl --help` or `jdl <command> --help` for details.

| Command | What it does |
|---------|--------------|
| `jdl run` | Launch the interactive engine (same as `flashloan`) |
| `jdl start flashloan` | Plain-English alias for `jdl run` |
| `jdl pro` | Launch the advanced 8-module integrator (same as `flashpro`) |
| `jdl swarm` | Run the always-on parallel scanner in the foreground (unattended, no menu) |
| `jdl supervisor [engine\|swarm]` | Run a target under the auto-restart supervisor (default: engine) |
| `jdl deploy receiver` | Deploy `NexusFlashReceiver.sol` |
| `jdl deploy gelato` | Deploy the Gelato relay integration |
| `jdl status` | One-shot snapshot: daemon liveness, execution count, revenue |
| `jdl show flashloans [--interval N] [--once]` | Stream live activity/logs (`--once` for a single snapshot) |
| `jdl integrate [--watch] [--interval N]` | Verify wiring: `.env`, RPC, contract, daemon (`--watch` loops) |
| `jdl install` | Detect platform, install every dependency (python/node/hardhat/foundry/rust), auto-wire `.env` |
| `jdl install-swarm-boot` | Install the always-on scanner's boot hook (Termux:Boot, or manual steps elsewhere) |
| `jdl update [--force]` | `git pull` + reinstall — brings `jdl`/`flashloan`/`flashpro` up to date |
| `jdl test [system] [--filter STR]` | Run the full test suite (same suites CI runs); `system` also auto-heals `.env` and retries |

### Console scripts (from `python/pyproject.toml`)

| Command | What it does |
|---------|--------------|
| `flashloan` | Interactive terminal engine (scan/exec) |
| `flashpro` | Advanced 8-module integrator |
| `jdl` | Unified CLI (table above) |

### `setup.sh` (repo root — Ubuntu/Debian, Termux, UserLAnd)

| Command | What it does |
|---------|--------------|
| `bash setup.sh` | Install only |
| `bash setup.sh run` | Install + launch engine |
| `bash setup.sh test` | Install + run the 79-test suite |
| `bash setup.sh termux` | Termux-specific guided install |
| `bash setup.sh swarm-boot` | Install the always-on parallel-scanner boot hook |

### Python tests (run directly, no `jdl` needed)

| Command | What it does |
|---------|--------------|
| `python3 -m jdl_flash.test_flash_engine` | Flash engine suite (79/79) |
| `python3 -m jdl_flash.test_swarm_runtime` | Swarm runtime suite |
| `python3 -m jdl_flash.test_bot_swarm` | Bot swarm suite |
| `python3 -m jdl_flash.test_wallet_lanes` | Wallet-lanes suite |
| `python3 -m jdl_flash.test_swarm_wiring` | Swarm wiring suite |
| `python3 -m jdl_flash.test_swarm_daemon` | Swarm daemon suite |
| `python3 -m jdl_flash.test_config_validation` | Config validation suite |
| `python3 -m jdl_flash.test_revenue_reconciliation` | Revenue reconciliation suite |
| `python3 -m jdl_flash.test_env_autowire` | `.env` auto-wire suite |
| `python3 -m jdl_flash.test_platform_detect` | Platform detection suite |
| `python3 -m jdl_flash.test_integrate` | `jdl integrate` suite |
| `python3 -m jdl_flash.test_cli` | `jdl` CLI suite |
| `python3 python/test_flash_supervisor.py` | Supervisor suite |
| `python3 python/jdl_native/test_jdl_native.py` | Native (Cython/ctypes) hot-path suite |

### Contracts — Foundry (`contracts/`, `foundry.toml`)

| Command | What it does |
|---------|--------------|
| `curl -L https://foundry.paradigm.xyz \| bash && foundryup` | Install Foundry |
| `forge install foundry-rs/forge-std` | Install the forge-std test lib |
| `forge test` | Run all tests |
| `ARB_RPC_URL=... forge test --match-path test/NexusFlashReceiver.t.sol -vv` | Run one file against a live Arbitrum fork, verbose |
| `forge build` | Compile contracts |

### Contracts — Hardhat (`contracts/package.json`)

| Command | What it does |
|---------|--------------|
| `npm run compile` | `hardhat compile` (solc 0.8.20) |
| `npm test` | `hardhat test` |
| `npm run test:fork` | Run `test/fork-flash.test.js` against a mainnet fork (7/7) |
| `npm run deploy:lib:arbitrum` | Deploy `ArbitrageLib` to Arbitrum |
| `npm run deploy:lib:ethereum` | Deploy `ArbitrageLib` to Ethereum |
| `npm run deploy:arbitrum` | Deploy the flash receiver to Arbitrum |
| `npm run deploy:ethereum` | Deploy the flash receiver to Ethereum |

### Node server (`node/package.json`, legacy/optional — skipped on Termux)

| Command | What it does |
|---------|--------------|
| `npm start` | Run the Node API/orchestration server (`src/index.js`) |
| `npm test` | Run the Node test suite (`node --test`) |

### Rust hot-path (`rust/hotpath/`, legacy/optional — skipped on Termux)

| Command | What it does |
|---------|--------------|
| `cargo build --release` | Build the `jdl-hotpath` binary + cdylib |
| `cargo test` | Run the Rust unit tests |
| `cargo clippy -- -D warnings` | Lint (same gate CI uses) |
| `cargo run --release` | Run the `jdl-hotpath` CLI binary |

### Utility scripts (`scripts/`)

| Command | What it does |
|---------|--------------|
| `bash scripts/deploy_termux.sh` | Universal Termux deployment for the revenue system |
| `bash scripts/security-audit.sh` | Grep-based scan for hardcoded secrets, disabled CSP, sensitive logs |
| `bash scripts/start-swarm-daemon.sh` | Foreground launcher for the swarm scanner, under supervision |
| `bash scripts/termux-boot-swarm.sh` | Termux:Boot entry point that starts the swarm on device boot |
| `bash scripts/push_revenue_system.sh` | Push revenue-system files to a deployment target |
| `powershell -File scripts/setup.ps1` | Native Windows dependency installer (used by `jdl install`) |

## Tests

| Suite | Command | Result |
|-------|---------|--------|
| Python engine | `python3 -m jdl_flash.test_flash_engine` | 79/79 |
| Python (everything — same as CI) | `jdl test` | all suites |
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
