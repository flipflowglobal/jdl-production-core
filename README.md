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
  (Hardhat compile with solc 0.8.20 — gating; plus the Arbitrum mainnet-fork test suite
  as a non-gating step, since it forks from a public RPC that can rate-limit).
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
│   config.py      · typed, validating env readers   │
│   risk_limits.py · pre-trade risk governor         │
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
│   compiles 20 files · mainnet-fork tested 7/7      │
├──────────────────────────────────────────────────┤
│   revenue_system/  —  on-chain revenue tracking    │
│   python/flash_supervisor.py — auto-restart daemon │
└──────────────────────────────────────────────────┘
```

## Chains

Arbitrum One (primary). Revenue monitor covers multiple chains.

## Get started

### Fresh device — clone, then go

This repo is **private**, so clone it with `git` (which uses your GitHub credentials;
a plain `curl` of the raw URL returns 404). One line takes you from nothing to running:

**Termux (Android):**
```bash
pkg install -y git && \
git clone https://github.com/flipflowglobal/jdl-production-core.git ~/jdl-production-core && \
cd ~/jdl-production-core && ./start.sh
```

**UserLAnd / Ubuntu / WSL:**
```bash
sudo apt update && sudo apt install -y git && \
git clone https://github.com/flipflowglobal/jdl-production-core.git ~/jdl-production-core && \
cd ~/jdl-production-core && ./start.sh
```

> `git clone` on a private repo needs GitHub auth on the device — a Personal Access Token
> (repo scope) at the HTTPS password prompt, `gh auth login`, or an SSH key with the
> `git@github.com:…` URL. `./start.sh` then does the rest (installs deps in place, wires
> `.env`, and runs) — it reuses the clone you just made, it won't clone again.

### Already cloned — one command from the main directory

```bash
./start.sh          # detects your platform, sets everything up, and runs
```

`start.sh` is the single front door. It's a thin, platform-aware dispatcher — it
detects Termux vs. glibc (UserLAnd/Ubuntu/WSL) and delegates to the right installer,
with no duplicated logic. Other verbs:

| Command | What it does |
|---------|--------------|
| `./start.sh` / `./start.sh setup` | First-run setup for your platform (deps, `.env`, verify) |
| `./start.sh start` | Launch the interactive engine |
| `./start.sh test` | Run the test suite (full suite on glibc; Python-only on Termux) |
| `./start.sh verify` | Prove the install can actually execute |
| `./start.sh status` | Daemon liveness, executions, revenue |
| `./start.sh update` | `git pull` + reinstall |

Extra args pass through: `./start.sh setup --no-start`, `./start.sh test --quick`.

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

Full guides: [`README_FLASH.md`](README_FLASH.md) · [`TERMUX.md`](TERMUX.md) · [`docs/TERMUX_WALKTHROUGH.md`](docs/TERMUX_WALKTHROUGH.md) · [`docs/TERMUX_DEEPDIVE.md`](docs/TERMUX_DEEPDIVE.md) · [`docs/USERLAND.md`](docs/USERLAND.md) · [`contracts/README.md`](contracts/README.md)

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
| `bash setup.sh run` | Install + launch engine (`jdl start flashloan`) |
| `bash setup.sh test` | Install + run the full test suite (`jdl test`) |
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

### Using as a dependency

The `contracts/` directory is published as the **`jdl-production-core`** npm package.
Install it from GitHub in any downstream Solidity project:

```bash
npm install flipflowglobal/jdl-production-core
# or pin to a tag:
npm install flipflowglobal/jdl-production-core#v1.0.0
```

Then import in Solidity (Hardhat or Foundry):

```solidity
import "jdl-production-core/contracts/NexusFlashReceiver.sol";
import "jdl-production-core/contracts/ArbitrageLib.sol";
import "jdl-production-core/contracts/interfaces/IAaveV3Pool.sol";
```

For **Foundry**, add the remapping to your `foundry.toml`:

```toml
remappings = [
    "jdl-production-core/=node_modules/jdl-production-core/",
    "@openzeppelin/=node_modules/@openzeppelin/",
]
```

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
| `bash scripts/userland-setup.sh` | UserLAnd/glibc one command: setup → **interactive `.env` entry** → verify → start (`--no-apt`, `--no-start`, `-y`) |
| `bash scripts/run-all-tests.sh` | Full cross-language test suite (python + rust + node + solidity), mirroring CI (`--quick`, `--strict`) |
| `bash scripts/termux-install.sh` | One-command Termux install: packages → clone → setup → verify (bootstrap via `git clone`, since this repo is private) |
| `bash scripts/termux-verify.sh` | Assured-execution doctor: proves the engine can run (`--fix`, `--run`, `--quick`) |
| `bash scripts/deploy_termux.sh` | Universal Termux deployment for the revenue system |
| `bash scripts/security-audit.sh` | Grep-based scan for hardcoded secrets, disabled CSP, sensitive logs |
| `bash scripts/start-swarm-daemon.sh` | Foreground launcher for the swarm scanner, under supervision |
| `bash scripts/termux-boot-swarm.sh` | Termux:Boot entry point that starts the swarm on device boot |
| `bash scripts/push_revenue_system.sh` | Push revenue-system files to a deployment target |
| `powershell -File scripts/setup.ps1` | Native Windows dependency installer (used by `jdl install`) |

## Risk controls

Flash loans are atomic, so an unprofitable route reverts on-chain and the
principal is never at risk. What that guarantee does *not* cover is gas: every
reverting broadcast still costs it, and a route broken for a systemic reason (a
stale contract address, a drained pool, an RPC serving wrong state) reverts on
every cycle. Unattended — which is the point of `jdl swarm` under
`flash_supervisor` — that is thousands of gas-burning attempts a day with
nothing to notice or stop it.

`risk_limits.py` sits between "edge found" and "transaction signed", on both the
interactive daemon and the swarm path:

| Control | Env var | Default | Behaviour |
|---------|---------|---------|-----------|
| Circuit breaker | `MAX_CONSECUTIVE_FAILURES` | 3 | N consecutive failed broadcasts pause execution; one success closes it |
| Breaker cooldown | `RISK_COOLDOWN_SEC` / `RISK_COOLDOWN_MAX_SEC` | 60s / 3600s | Doubles per further failure, capped |
| Daily loss cap | `MAX_DAILY_LOSS_USD` | 25 | Halts for the rest of the UTC day once realised P&L is that far down |
| Per-trade ceiling | `MAX_LOAN_USD` | 500000 | No single loan exceeds it |
| Profit floor | `MIN_PROFIT_USD` | 0.50 | Enforced in the swarm's hot-path filter *and* re-checked at the gate |
| Kill switch | `HALT_FILE` | `~/.flash_loan_engine/HALT` | `touch` to halt, `rm` to resume — no signal, no restart |

Two properties make these real rather than decorative:

* **State is persisted in SQLite, not memory.** `flash_supervisor.py` restarts the
  engine on crash; an in-memory breaker would reset on every restart, so the one
  case that most needs the cap — a crash-looping bot — would bypass it entirely.
* **Failures are ledgered.** `executions` only ever recorded *successful* trades,
  so gas burned on failures was invisible to every revenue figure. `risk_events`
  records every attempt (success, failure, blocked), giving a true cost basis.

Config parsing fails closed alongside them: a malformed value (`MIN_PROFIT_USD=0.o1`)
no longer aborts the import of `flash_loan_engine` — and with it every `jdl`
command — but it does disable live execution until fixed, while leaving scanning
and reporting fully usable. `jdl status` shows the current posture and any
malformed values; see `config.py`.

## Tests

| Suite | Command | Result |
|-------|---------|--------|
| **Full suite (all languages, mirrors CI)** | `bash scripts/run-all-tests.sh` | python + rust + node + solidity |
| Python engine | `python3 -m jdl_flash.test_flash_engine` | 79/79 |
| Config readers | `python3 jdl_flash/test_config.py` | 62/62 |
| Risk governor | `python3 jdl_flash/test_risk_limits.py` | 77/77 |
| Python (everything — same as CI) | `jdl test` | all suites |
| Contracts (Foundry) | `forge test` | 7/7 fork |
| Contracts (Hardhat) | `npm run test:fork` | 7/7 fork |

On glibc platforms (UserLAnd / Ubuntu / WSL / macOS) `scripts/run-all-tests.sh` runs every
suite in one shot — see [`docs/USERLAND.md`](docs/USERLAND.md). On Termux only the Python
suite applies (Rust/Node/Foundry are skipped by design).

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
