# JDL Production Core — Complete Codebase Reference

> Auto-generated descriptive index of every file, class, and function in the
> repository. Use this document to understand the architecture at a glance and to
> locate any specific piece of logic without reading individual source files.

---

## Table of Contents

1. [Repository Overview](#1-repository-overview)
2. [File Tree](#2-file-tree)
3. [Architecture Diagram](#3-architecture-diagram)
4. [File-by-File Reference](#4-file-by-file-reference)
   - [Root / Shell Layer](#41-root--shell-layer)
   - [Python — jdl_flash package](#42-python--jdl_flash-package)
   - [Python — jdl_native package](#43-python--jdl_native-package)
   - [Python — Root Modules](#44-python--root-modules)
   - [Revenue System](#45-revenue-system)
   - [Solidity Contracts](#46-solidity-contracts)
   - [Node.js Server](#47-nodejs-server)
   - [Rust Hot-Path](#48-rust-hot-path)
   - [Database](#49-database)
   - [Scripts](#410-scripts)
   - [CI / GitHub Actions](#411-ci--github-actions)
   - [Documentation](#412-documentation)
   - [Configuration & Templates](#413-configuration--templates)

---

## 1. Repository Overview

**JDL Production Core** is a polyglot, cross-platform flash-loan arbitrage
engine targeting Arbitrum One. It performs zero-capital arbitrage by borrowing
tokens from Aave V3 in a single atomic transaction, routing them through
Uniswap V3, Curve, and Balancer pools, and repaying the loan—all in one block.

### Technology Stack

| Layer | Language | Purpose |
|-------|----------|---------|
| Smart contracts | Solidity 0.8.20 | On-chain flash-loan receiver and swap execution |
| Arbitrage engine | Python 3 | Scanning, execution orchestration, CLI, swarm management |
| Hot-path scanner | Rust | CPU-intensive negative-cycle detection (Bellman-Ford over `−ln(rate)`) |
| Blockchain bridge | Node.js (ESM) | HTTP API, contract state reads via ethers.js v6 |
| Native FFI bridge | Cython / ctypes | Python → Rust via compiled extension or shared library |
| Database | SQLite | Revenue tracking, execution history |
| Infrastructure | Bash / PowerShell | Platform-aware install, setup, and CI scripts |

### Key Design Principles

- **Zero-gas execution**: `FlashZeroGas.sol` + Gelato Relay ERC-2771 lets the
  contract pay its own gas from arbitrage profit (ERC-4337-style paymaster).
- **Multi-wallet parallel execution**: `wallet_lanes.py` issues transactions from
  independent wallets concurrently to overcome single-wallet nonce serialization.
- **Graceful degradation**: All native (Rust/Cython) paths fall back to a pure
  Python implementation; the system runs on Termux/Android without a compiler.
- **Universal .env wiring**: `env_autowire.py` scans the whole filesystem for
  existing `.env` files and merges them—no manual copy-paste needed.

---

## 2. File Tree

```
jdl-production-core/
├── .env.example                        # minimal env sample
├── .env.production.template            # production env template (full)
├── .env.template                       # env template with inline docs
├── .gitignore
├── .github/
│   ├── dependabot.yml                  # dependency update schedule
│   └── workflows/
│       ├── ci.yml                      # main CI (Python + Node + Rust tests)
│       ├── release.yml                 # tagged release workflow
│       └── security.yml               # npm/pip security audit
├── LICENSE
├── POLYGLOT.md                         # notes on the multi-language native bridge
├── README.md                           # primary documentation
├── README_FLASH.md                     # flash-loan specific quickstart
├── TERMUX.md                           # Termux/Android install guide
├── CODEBASE.md                         # ← this file
│
├── contracts/                          # Solidity smart contracts
│   ├── .gitignore
│   ├── README.md
│   ├── foundry.toml                    # Foundry config (fork tests, ARB_RPC_URL)
│   ├── hardhat.config.js               # Hardhat config (fork tests, ARB_RPC_URL)
│   ├── package.json                    # npm deps + scripts (compile/test:fork)
│   ├── package-lock.json
│   ├── contracts/
│   │   ├── ArbitrageLib.sol            # pure library: swap encoding, profit calc
│   │   ├── FlashZeroGas.sol            # Gelato-based zero-gas paymaster wrapper
│   │   ├── NexusFlashReceiver.sol      # main flash-loan + arb execution contract
│   │   ├── ProfitPaymaster.sol         # ERC-4337 paymaster (profit-funded gas)
│   │   └── interfaces/
│   │       ├── IAaveV3Pool.sol         # flashLoanSimple interface
│   │       ├── IBalancerVault.sol      # Balancer V2 single-swap interface
│   │       ├── ICurvePool.sol          # Curve StableSwap interface
│   │       └── IUniswapV3Router.sol    # Uniswap V3 exactInputSingle interface
│   ├── scripts/
│   │   ├── deploy-arb-lib.js           # Hardhat deploy for ArbitrageLib
│   │   └── deploy-flash-receiver.js   # Hardhat deploy for NexusFlashReceiver
│   └── test/
│       ├── NexusFlashReceiver.t.sol    # Foundry unit tests
│       ├── NexusFlashReceiverInvariant.t.sol  # Foundry invariant/fuzz tests
│       ├── ProfitPaymaster.t.sol       # Foundry paymaster tests
│       └── fork-flash.test.js         # Hardhat mainnet-fork integration test
│
├── database/
│   └── revenue_schema.sql             # SQLite schema for revenue tracking
│
├── docs/
│   ├── INTEGRATION_GUIDE.txt          # step-by-step integration reference
│   ├── README_REVENUE_SYSTEM.md       # revenue system documentation
│   ├── SEPOLIA_DRYRUN.md              # Sepolia testnet dry-run guide
│   ├── TERMUX_DEEPDIVE.md             # advanced Termux setup
│   ├── TERMUX_WALKTHROUGH.md          # Termux step-by-step walkthrough
│   └── USERLAND.md                    # UserLAnd/proot-Ubuntu setup guide
│
├── node/                              # Node.js HTTP API + orchestration server
│   ├── package.json
│   ├── package-lock.json
│   └── src/
│   │   ├── chain.js                   # ethers.js contract reads (read-only)
│   │   ├── hotpath.js                 # bridge to Rust jdl-hotpath binary
│   │   └── index.js                   # Express HTTP server (/health /contract /scan)
│   └── test/
│       ├── hotpath.test.js            # Node built-in test: scan happy/no-opp paths
│       └── scan_validation.test.js    # Node built-in test: HTTP /scan input validation
│
├── python/
│   ├── .env.production.template       # copy of production env template
│   ├── flash_supervisor.py            # daemon supervisor (auto-restart + revenue watch)
│   ├── gas_kernel.py                  # gas estimation kernel (standalone)
│   ├── pyproject.toml                 # package metadata, entry-points, deps
│   ├── requirements_flash.txt         # pip requirements
│   ├── test_flash_supervisor.py       # tests for flash_supervisor
│   ├── trading_core.py                # legacy monolithic trading engine (v4.0)
│   │
│   ├── jdl_flash/                     # installable Python package
│   │   ├── __init__.py
│   │   ├── _paths.py                  # canonical path helpers (python_dir, etc.)
│   │   ├── advanced_math.py           # stdlib numerical toolkit (Cholesky, B-S, DFT…)
│   │   ├── artifacts/
│   │   │   └── NexusFlashReceiver.json  # compiled contract ABI + bytecode
│   │   ├── bot_swarm.py               # parallel worker pool (BotSwarm)
│   │   ├── cli.py                     # `jdl` unified CLI entry-point
│   │   ├── deploy_gelato.py           # deploy NexusFlashReceiver via Gelato
│   │   ├── deploy_receiver.py         # deploy NexusFlashReceiver directly
│   │   ├── env_autowire.py            # universal .env file discovery + merge
│   │   ├── flash_loan_engine.py       # core interactive engine (v1.0 terminal UI)
│   │   ├── flash_pro.py               # advanced 8-module integrator UI (v2.0)
│   │   ├── gelato_relay.py            # Gelato Relay ERC-2771 task submission
│   │   ├── integrate.py               # system health checks (`jdl integrate`)
│   │   ├── loan_optimizer.py          # optimal loan-size calculation
│   │   ├── market_analysis.py         # microstructure analysis (Hurst, VWAP, OFI…)
│   │   ├── pattern_recognition.py     # technical analysis (RSI, MACD, Bollinger…)
│   │   ├── platform_detect.py         # OS/shell detection (Termux/UserLAnd/WSL…)
│   │   ├── prediction.py              # ML forecasters (OnlineAR, RidgeForecaster…)
│   │   ├── realness_guard.py          # production fault-tolerance (guards + retries)
│   │   ├── revenue_reconciliation.py  # on-chain vs off-chain revenue reconciliation
│   │   ├── rpc_endpoints.py           # RPC endpoint list builder (single source of truth)
│   │   ├── swarm_daemon.py            # `jdl swarm` foreground daemon entry-point
│   │   ├── swarm_runtime.py           # SwarmCoordinator: route partitioning + execution
│   │   ├── triangular_scanner.py      # triangular arbitrage route scanner
│   │   ├── wallet_lanes.py            # multi-wallet execution lanes
│   │   ├── test_bot_swarm.py
│   │   ├── test_cli.py
│   │   ├── test_config_validation.py
│   │   ├── test_env_autowire.py
│   │   ├── test_flash_engine.py
│   │   ├── test_integrate.py
│   │   ├── test_platform_detect.py
│   │   ├── test_revenue_reconciliation.py
│   │   ├── test_swarm_daemon.py
│   │   ├── test_swarm_runtime.py
│   │   ├── test_swarm_wiring.py
│   │   └── test_wallet_lanes.py
│   │
│   └── jdl_native/                    # Python → Rust FFI bridge
│       ├── .gitignore
│       ├── __init__.py                # backend selector (cython/ctypes/subprocess/python)
│       ├── _ctypes_backend.py         # ctypes cdylib backend
│       ├── _jdl.pyx                   # Cython extension source
│       ├── _pyfallback.py             # pure-Python DFS arbitrage fallback
│       ├── jdl_hotpath.h              # C ABI header for libjdl_hotpath
│       ├── setup.py                   # Cython build script
│       └── test_jdl_native.py         # native bridge tests
│
├── revenue_system/                    # standalone revenue tracking subsystem
│   ├── __init__.py
│   ├── chain_monitor_fixed.py         # on-chain transaction monitor (fixed v2)
│   ├── revenue_reconciliation.py      # reconciliation engine
│   └── revenue_recording.py          # SQLite revenue recorder
│
├── rust/
│   └── hotpath/                       # jdl-hotpath Rust crate
│       ├── Cargo.lock
│       ├── Cargo.toml
│       └── src/
│           ├── lib.rs                 # cdylib entry: best_cycle, analyze_bytecode, C ABI
│           ├── main.rs                # CLI binary: stdin/stdout JSON filter
│           └── evm/
│               ├── mod.rs             # EVM module re-exports
│               ├── cfg.rs             # Control-flow graph builder
│               ├── decompiler.rs      # Pseudo-Solidity decompiler
│               ├── disasm.rs          # Linear-sweep EVM disassembler
│               ├── opcodes.rs         # Complete EVM opcode table (Frontier→Cancun)
│               ├── security.rs        # Bytecode security pattern detector
│               ├── signatures.rs      # Function dispatcher / 4-byte selector recovery
│               ├── symbolic.rs        # Inter-block symbolic execution engine
│               └── types.rs           # EVM type inference
│
├── scripts/
│   ├── deploy_termux.sh               # one-shot Termux deploy helper
│   ├── push_revenue_system.sh         # push revenue system to production
│   ├── run-all-tests.sh               # run every test suite in CI order
│   ├── security-audit.sh              # npm/pip CVE audit
│   ├── setup.ps1                      # Windows PowerShell setup
│   ├── start-swarm-daemon.sh          # start the swarm daemon (nohup/screen)
│   ├── termux-boot-swarm.sh           # Termux:Boot hook for auto-start
│   ├── termux-install.sh              # Termux dependency installer
│   ├── termux-verify.sh               # post-install verification (Termux)
│   └── userland-setup.sh              # UserLAnd/Ubuntu/WSL full installer
│
├── setup.sh                           # Termux-specific setup (venv + .env)
└── start.sh                           # universal front-door dispatcher
```

---

## 3. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        User / CI                                │
│   ./start.sh  ·  jdl <cmd>  ·  bash scripts/run-all-tests.sh  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
              ┌─────────────▼──────────────┐
              │     cli.py  (jdl)           │  unified CLI entry-point
              │  cmd_run / cmd_swarm /      │
              │  cmd_supervisor / cmd_test  │
              └──┬──────────┬──────────────┘
                 │          │
    ┌────────────▼─┐   ┌────▼──────────────────────────────┐
    │ flash_loan_  │   │  SwarmCoordinator (swarm_runtime) │
    │ engine.py    │   │  ├─ partition routes per worker    │
    │ (interactive │   │  ├─ BotSwarm (bot_swarm.py)        │
    │  terminal    │   │  │   └─ N worker threads           │
    │  dashboard)  │   │  ├─ jdl_native (Rust hot-path)    │
    └──────┬───────┘   │  └─ WalletLanes (parallel exec)   │
           │            └──────────────┬────────────────────┘
           │                          │
    ┌──────▼──────────────────────────▼────────────────────┐
    │               flash_loan_engine core                  │
    │  validate_env_config · get_w3 · chain_status          │
    │  EMAWeights · ZScoreDetector · GARCH11 · KalmanPrice  │
    │  BellmanFordArb · UCB1Bandit · QLearning               │
    └────────────────────────┬──────────────────────────────┘
                             │  web3 transactions
    ┌────────────────────────▼──────────────────────────────┐
    │           NexusFlashReceiver.sol (Arbitrum)           │
    │  initiateFlashLoan → executeOperation                 │
    │  └─ _executeStep (Uniswap V3 / Curve / Balancer)     │
    └───────────────────────────────────────────────────────┘
```

---

## 4. File-by-File Reference

---

### 4.1 Root / Shell Layer

#### `start.sh`
**Purpose:** Universal front-door dispatcher. Detects the platform
(Termux vs glibc) and routes every command to the right sub-script or `jdl`
subcommand. No business logic is duplicated here.

**Verbs handled:**
| Verb | Action |
|------|--------|
| `setup` | Termux → `scripts/termux-install.sh`; others → `scripts/userland-setup.sh` |
| `start` / `run` | `jdl start flashloan` |
| `test` | `scripts/run-all-tests.sh` |
| `verify` / `doctor` | `scripts/termux-verify.sh` |
| `status` | `jdl status` |
| `update` | `jdl update` |
| `integrate` / `config` | `jdl integrate` |

**Key functions:**
- `is_termux()` — detects Termux via `TERMUX_VERSION`, `PREFIX`, or `pkg` path; never uses `/data/data/com.termux` to avoid UserLAnd false-positives.
- `resolve_jdl()` — finds the `jdl` command (venv first, then PATH).
- `usage()` — prints the header comment block via `awk`.

#### `setup.sh`
**Purpose:** Termux-specific installer. Sets up the Python venv at
`~/.flash_venv`, installs system packages (`python git openssl libffi clang
make`), and wires `~/jdl/.env`.

---

### 4.2 Python — `jdl_flash` package

#### `python/jdl_flash/__init__.py`
Empty package marker.

#### `python/jdl_flash/_paths.py`
**Purpose:** Canonical path helpers shared across the package to avoid
hardcoding paths in multiple files.

**Functions:**
- `python_dir() -> Path` — returns the repo's `python/` directory.
- `load_flash_supervisor() -> ModuleType` — loads `flash_supervisor.py` by
  file path (it is intentionally unpackaged; lives at `python/` root).

---

#### `python/jdl_flash/advanced_math.py`
**Purpose:** Pure-stdlib numerical toolkit for flash-loan arbitrage
calculations. No external dependencies.

**Module-level functions:**
- `_ncdf(x)` — standard normal CDF via `math.erf`.
- `ewma(series, alpha)` — exponentially-weighted moving average.
- `zscore(series)` — z-score normalization of a float series.
- `softmax(v)` — numerically stable softmax.
- `sigmoid(x)` — sigmoid activation.
- `logit(p)` — log-odds (inverse sigmoid).
- `sma(series, window)` — simple moving average.
- `ema(series, span)` — exponential moving average with span.
- `stddev(series)` — population standard deviation.
- `covariance(x, y)` — sample covariance.
- `correlation(x, y)` — Pearson correlation.

**Class `AdvancedMath` (all static methods):**
- `cholesky(M)` — Cholesky decomposition of a symmetric positive-definite matrix.
- `solve_lower(L, b)` — forward substitution for lower-triangular system `Lx=b`.
- `solve_upper(U, b)` — back substitution for upper-triangular system `Ux=b`.
- `spd_solve(A, b)` — solve `Ax=b` for SPD `A` via Cholesky.
- `black_scholes_call(S, K, t, r, sigma)` — Black-Scholes European call price.
- `black_scholes_put(S, K, t, r, sigma)` — Black-Scholes European put price.
- `newton(f, df, x0, iters, tol)` — Newton-Raphson root finder.
- `secant(f, x0, x1, iters, tol)` — Secant method root finder.
- `rfft_mag(samples)` — real FFT magnitude spectrum (stdlib DFT).
- `ridge_fit(X, y, lam)` — ridge regression (returns coefficient vector).
- `ridge_predict(coef, row)` — ridge regression prediction for one row.

---

#### `python/jdl_flash/bot_swarm.py`
**Purpose:** Parallel worker pool that drives multiple scan+execute workers
concurrently using Python threads.

**Class `WorkerStats`:** Per-worker performance counters.
- `__init__()` — zero-initializes scans, hits, execs, errors, and timing.
- `to_dict() -> Dict[str, Any]` — serializes stats to a plain dict.

**Class `BotSwarm`:** Manages N worker threads, each running a scan/execute
loop over a disjoint slice of routes.
- `__init__(n_workers, scan_fn, exec_fn, nonce_base, …)` — constructs the
  swarm; `scan_fn(worker_id, n_workers)` returns opportunities;
  `exec_fn(opportunity, nonce)` executes one.
- `stats() -> Dict[str, Any]` — aggregated stats across all workers.
- (Internal) worker loop: each thread repeatedly calls `scan_fn`, feeds the
  best opportunity to `exec_fn`, and increments its `WorkerStats`.

---

#### `python/jdl_flash/cli.py`
**Purpose:** Single `jdl` CLI entry-point. Every command delegates to an
existing function or module; no logic is duplicated here.

**Module-level helpers:**
- `_python_dir() -> Path` — delegates to `_paths.python_dir()`.
- `_load_flash_supervisor() -> ModuleType` — delegates to `_paths.load_flash_supervisor()`.
- `_resolve_test_suites(filter_str) -> (Path, list[Path])` — builds the list of
  test suite paths matching an optional substring filter.
- `_run_test_suites(python_dir, suites) -> list[Path]` — executes each suite as
  a subprocess; returns the ones that failed.

**Command handlers (each returns an int exit code):**
- `cmd_run(args)` — calls `flash_loan_engine._run()` (interactive terminal dashboard).
- `cmd_start(args)` — plain-English alias; routes `flashloan` → `cmd_run`.
- `cmd_pro(args)` — calls `flash_pro.main()` (8-module advanced integrator).
- `cmd_swarm(args)` — calls `swarm_daemon.main()` (foreground parallel scanner).
- `cmd_supervisor(args)` — runs `flash_supervisor.FlashSupervisor(target).run()`.
- `cmd_deploy(args)` — calls `deploy_receiver.main()` or `deploy_gelato.main()`.
- `cmd_update(args)` — `git pull --ff-only` + `pip install -e` + optional Cython rebuild.
- `cmd_install(args)` — delegates to `setup.sh swarm-boot` (OS-level boot hook).
- `cmd_setup(args)` — detects platform, runs `setup.sh` or `setup.ps1`, then calls `autowire()`.
- `cmd_show(args)` — streams live daemon status + log tail; `--once` for a single snapshot.
- `cmd_integrate(args)` — runs `integrate.run_checks()`; `--watch` for a loop.
- `cmd_status(args)` — one-shot daemon liveness + execution count + revenue.
- `cmd_test(args)` — runs all test suites; `system` scope auto-wires `.env` and retries failures.

**Parser builder:**
- `build_parser() -> ArgumentParser` — constructs the full argparse tree.
- `main(argv=None) -> int` — parses and dispatches.

---

#### `python/jdl_flash/deploy_gelato.py`
**Purpose:** Deploys `NexusFlashReceiver` via the Gelato Relay ERC-2771 path.
Reads config from `~/jdl/.env`, signs with `PRIVATE_KEY`, and submits a
sponsored deploy task.

**Key function:**
- `main()` — full deploy flow: load config, build deployment tx, submit via
  Gelato API, poll for task status, write `FLASH_CONTRACT_ADDRESS` back to `.env`.

---

#### `python/jdl_flash/deploy_receiver.py`
**Purpose:** Deploys `NexusFlashReceiver` directly (no Gelato) using web3.py.

**Key function:**
- `main()` — loads ABI/bytecode from `artifacts/NexusFlashReceiver.json`, builds
  the constructor tx, signs, broadcasts, waits for receipt, updates `.env`.

---

#### `python/jdl_flash/env_autowire.py`
**Purpose:** Universal, zero-prompt `.env` wiring. Scans the filesystem for
any `.env` file, merges missing values into `~/jdl/.env`, and reports what
was filled vs. what still needs a human.

**Constants:**
- `CANONICAL_ENV` — path to `~/jdl/.env` (the single file the engine reads).

**Functions:**
- `is_placeholder(value: str) -> bool` — returns `True` if the value is empty,
  a template marker (`YOUR_…`, `<…>`, `CHANGE_ME`, `TODO`, etc.), or otherwise
  not a real value.
- `_strip_value(raw: str) -> str` — strips quotes and whitespace from a raw
  `.env` line value.
- `parse_env_file(path: Path) -> Dict[str, str]` — parses a `.env` file into a
  key→value dict; handles comments, blank lines, quoted values.
- `find_env_files(roots, max_depth) -> List[Path]` — recursively walks search
  roots up to `max_depth` and returns every `.env`-like file found.
- `repo_root() -> Path` — locates the repo root by walking up from `__file__`.
- `default_search_roots() -> List[Path]` — canonical search locations
  (home, repo, cwd, `~/jdl`).
- `default_template() -> Path` — path to `.env.template`.
- `_sort_key(path: Path) -> tuple` — priority sort: canonical `.env` first, then
  proximity to repo root, then path length.
- `autowire(canonical, roots, template, dry_run) -> dict` — main entry point:
  discovers all `.env` files, fills any placeholder keys in `canonical` from
  higher-priority sources, writes back, returns `{filled, unresolved, skipped}`.
- `set_values(target, updates) -> None` — writes specific key=value pairs into a
  `.env` file, creating it if necessary.
- `_write_back(target, filled) -> None` — writes filled key=value pairs back to
  the target `.env`.

---

#### `python/jdl_flash/flash_loan_engine.py`
**Purpose:** Core interactive engine (v1.0). Provides a terminal dashboard UI
with real-time arbitrage scanning, execution, and P&L tracking. This is the
file that runs when you execute `jdl run` or `flashloan`.

**Web3 compatibility shims** (at module level):
- `_w3_cs(addr)` — checksum address.
- `_gas_p(w3)` — current gas price in Gwei.
- `_nonce(w3, addr)` — account nonce.
- `_blk(w3)` — latest block number.
- `_chain_id(w3)` — chain ID.
- `_balance(w3, addr)` — ETH balance in Wei.
- `_is_connected(w3)` — provider connectivity check.
- `_inject_poa(w3)` — inject `geth_poa_middleware` if needed.
- `_send_raw(w3, raw)` — broadcast a signed raw tx.
- `_est_gas(w3, tx)` — gas estimation.
- `_eth_call(w3, tx, block)` — `eth_call`.
- `_wait_receipt(w3, tx_hash, timeout)` — poll until mined or timeout.

**Configuration / RPC:**
- `_env(*names, default)` — reads the first non-empty env var from a list of aliases.
- `_valid_rpc(u: str) -> bool` — rejects placeholder URLs.
- `_build_rpc_endpoints() -> list` — builds ordered, de-duplicated Arbitrum RPC list.
- `_mask_rpc_url(url: str) -> str` — masks the API key in a URL for safe logging.

**ABI helpers:**
- `_abi_w_uint(n)` — ABI-encodes a uint256.
- `_abi_w_addr(a)` — ABI-encodes an address.
- `_abi_w_b32(b)` — ABI-encodes a bytes32.

**Core functions:**
- `validate_env_config() -> list` — validates all required env vars; returns list of errors.
- `get_w3()` — constructs (or returns cached) Web3 instance; tries each RPC endpoint.
- `reset_w3()` — clears the cached Web3 instance.
- `chain_status(force) -> dict` — returns `{connected, block, chain_id, gas_gwei}`; cached.

**Terminal UI:**
- `class C` — ANSI color constants.
- `clear()` — clears the terminal.
- `banner()` — prints the ASCII art header.

**Database:**
- `init_db()` — initializes the SQLite database with execution and profit tables.
- `db_exec(sql, params)` — execute a write query.
- `db_query(sql, params) -> list` — execute a read query.

**Mathematical models:**
- `class EMAWeights` — exponential moving average weights for pair ranking.
  - `update(pair, found) -> float` — update weight based on whether an opportunity was found.
  - `get(pair) -> float` — current weight for a pair.
  - `ranked(pairs) -> list` — pairs sorted by descending weight.
- `class ZScoreDetector` — rolling z-score anomaly detector per key.
  - `update(key, v) -> float` — update and return current z-score.
  - `is_anomaly(key, v) -> bool` — True if `|z| > 2.0`.
- `class GARCH11` — GARCH(1,1) volatility estimator.
  - `update(ret) -> float` — update with a return and get new variance.
  - `predict(h) -> float` — h-step ahead variance prediction.
  - `high_vol(pct) -> bool` — True if predicted vol > threshold.
- `class KalmanPrice` — Kalman filter for price smoothing.
  - `update(obs) -> float` — update with observation; return filtered estimate.
  - `estimate` — property: current state estimate.
- `class OrnsteinUhlenbeck` — mean-reversion (OU) process estimator.
  - `update(x)` — update with new observation.
  - `half_life() -> float` — mean-reversion half-life in samples.
  - `reversion_prob(spread, horizon) -> float` — probability of reversion within `horizon`.
- `class KellyCriterion` — Kelly sizing.
  - `fraction(win_p, win_loss, regime) -> float` — Kelly fraction with regime scaling.
- `class NewtonRaphsonAMM` — Newton-Raphson AMM output calculator.
  - `out(rx, ry, ain, fee_bps) -> float` — amount out for given amount in.
  - `impact_pct(rx, ry, ain, fee_bps) -> float` — price impact percentage.
- `class BellmanFordArb` — Bellman-Ford negative-cycle arbitrage detector.
  - `find(prices, tokens) -> Optional[List[str]]` — returns the profitable cycle path or None.
- `class UCB1Bandit` — Upper Confidence Bound multi-armed bandit for route selection.
  - `choose() -> int` — select the arm with the highest UCB1 score.
  - `update(arm, r)` — record reward for arm.
  - `best() -> int` — current best arm.
  - `save() / load()` — SQLite persistence.
- `class QLearning` — Q-learning for execution timing.

**Entry point:**
- `_run()` — starts the interactive terminal dashboard event loop.

---

#### `python/jdl_flash/flash_pro.py`
**Purpose:** Advanced 8-module integrator UI (v2.0). Wraps `flash_loan_engine`
with a richer menu system covering: market analysis, pattern recognition,
loan optimization, triangular scanning, risk management, portfolio, reporting,
and live execution.

**Key function:**
- `main()` — entry point; presents the 8-module menu and dispatches to each.

---

#### `python/jdl_flash/gelato_relay.py`
**Purpose:** Gelato Relay ERC-2771 task submission for sponsored (zero-gas)
execution. Encodes calldata for `initiateFlashLoanRelay` and submits to the
Gelato API.

**Key functions:**
- `build_relay_calldata(asset, amount, encoded_steps, max_fee) -> bytes` — ABI-encodes the relay call.
- `submit_relay_task(calldata, contract, chain_id, api_key) -> str` — POSTs to Gelato API; returns task ID.
- `poll_task_status(task_id, api_key, timeout) -> dict` — polls until task completes or times out.
- `main()` — CLI wrapper for relay submission.

---

#### `python/jdl_flash/integrate.py`
**Purpose:** System health checks behind `jdl integrate`. Each check is a
standalone function returning `(ok: bool, detail: str)`.

**Functions:**
- `check_env_file(env_path) -> (bool, str)` — verifies `~/jdl/.env` exists.
- `_own_endpoint_count(values) -> int` — counts non-public RPC endpoints from the env.
- `check_required_keys(env_path) -> (bool, str)` — verifies `PRIVATE_KEY` and at least one RPC source are set and non-placeholder.
- `check_contract_address(env_path) -> (bool, str)` — verifies `FLASH_CONTRACT_ADDRESS` is a valid, non-zero 42-char hex address.
- `_expected_chain_id(values) -> int` — reads `CHAIN_ID` from env; defaults to 42161 (Arbitrum One).
- `_probe_chain_id(url, timeout) -> int` — POSTs `eth_chainId` JSON-RPC; raises on failure.
- `check_rpc_reachable(env_path, timeout) -> (bool, str)` — probes ALL configured RPC endpoints concurrently (via `ThreadPoolExecutor`); returns reachable if ANY responds on the correct chain.
- `check_rpc_endpoints(env_path) -> (bool, str)` — informational: counts configured endpoints.
- `check_daemon_liveness() -> (bool, str)` — checks if the PID file exists.
- `run_checks() -> List[(str, bool, str)]` — runs all `CHECKS` and returns results.

**Constants:**
- `CHECKS` — ordered list of `(label, fn)` tuples run by `run_checks()`.

---

#### `python/jdl_flash/loan_optimizer.py`
**Purpose:** Calculates the optimal flash-loan size that maximizes net profit
given pool liquidity, price impact, and gas costs.

**Class `LoanOptimizer`:**
- `__init__(liquidity_fn, quote_fn)` — `liquidity_fn(token)` returns available
  liquidity; `quote_fn(token_in, token_out, amount, fee)` returns output amount.
- `max_borrow(token, source_caps) -> int` — minimum of Aave availability and
  `source_caps` dict.
- `optimal_size(token_in, token_out, fee_tier, loan_range, gas_usd_est, aave_premium_bps, steps) -> dict` — grid search over `steps` sizes in `loan_range`; returns the size with maximum net profit.
- `kelly_cap(win_prob, win_loss_ratio, bankroll_usd, token_price_usd, token_decimals, fraction_cap) -> int` — Kelly-criterion position cap in token base units.
- `size_report(token, fee_tier, loan_range, gas_usd_est) -> dict` — full sizing report with optimal size, kelly cap, and max borrow.

---

#### `python/jdl_flash/market_analysis.py`
**Purpose:** Microstructure analysis toolkit.

**Class `MarketAnalysis`:**
- `hurst_exponent(series) -> Optional[float]` — rescaled range (R/S) analysis; H > 0.5 → trending, H < 0.5 → mean-reverting.
- `volatility_regime(returns) -> Dict[str, Any]` — classifies realized volatility as `low/medium/high/extreme` with annualized vol and GARCH estimate.
- `order_flow_imbalance(bid_volumes, ask_volumes) -> Optional[float]` — OFI = (bid_vol − ask_vol) / (bid_vol + ask_vol); ranges [−1, 1].
- `vwap(trades) -> Optional[float]` — volume-weighted average price from `(price, volume)` tuples.
- `twap(prices) -> Optional[float]` — time-weighted average price (simple mean).
- `liquidity_depth(order_book_side, price, depth_pct) -> dict` — sums order book volume within `depth_pct` of `price`.
- `spread_bps(best_bid, best_ask) -> Optional[float]` — bid-ask spread in basis points.
- `microprice(best_bid, bid_size, best_ask, ask_size) -> Optional[float]` — size-weighted midpoint.
- `summary(prices, returns, trades, bid_volumes, ask_volumes) -> dict` — runs all metrics and returns a combined report.

---

#### `python/jdl_flash/pattern_recognition.py`
**Purpose:** Technical analysis indicators and chart pattern detection.

**Module helpers:**
- `_ema(vals, p) -> List[float]` — EMA with period `p`.
- `_znorm(s) -> List[float]` — z-score normalize a series.

**Class `PatternRecognition`:**
- `rsi(prices, period=14) -> Optional[float]` — RSI via Wilder's smoothing.
- `macd(prices, fast, slow, signal) -> dict` — MACD line, signal line, histogram.
- `bollinger(prices, period, num_std) -> dict` — Bollinger Bands (`upper`, `middle`, `lower`, `bandwidth`, `%b`).
- `support_resistance(prices, window) -> dict` — finds local min/max pivots within a window.
- `detect_breakout(prices) -> dict` — detects breakout above resistance or below support.
- `matrix_profile_lite(prices, m) -> List[float]` — simplified matrix profile (nearest-neighbour distance) for motif/anomaly detection.
- `regime_shift(prices) -> bool` — True if the recent half of the series has significantly different variance than the earlier half (F-test).
- `candlestick(ohlc) -> List[str]` — identifies candlestick patterns: Doji, Hammer, Shooting Star, Engulfing Bullish/Bearish, Morning/Evening Star.
- `score(prices) -> Dict[str, Any]` — composite score combining RSI, MACD, Bollinger, breakout, and regime shift into a single `[-1, 1]` signal.

---

#### `python/jdl_flash/platform_detect.py`
**Purpose:** Detects which OS/shell the process is running under so `jdl
install` can pick the right dependency installer.

**Constants:** `TERMUX`, `USERLAND`, `WSL`, `WINDOWS`, `MACOS`, `LINUX`,
`POSIX_PLATFORMS`.

**Functions:**
- `_proc_version() -> str` — reads `/proc/version` lowercased (Linux only).
- `_is_termux() -> bool` — True only when actually running inside Termux (checks `TERMUX_VERSION`, `PREFIX`, and `pkg` path); never uses the `/data/data/com.termux` directory.
- `detect_platform() -> str` — returns one of the platform constants; checks in priority order: Windows → Termux → UserLAnd/Android → WSL → macOS → Linux.
- `is_posix_installer(platform) -> bool` — True if the platform should use `setup.sh`; False for native Windows (`setup.ps1`).

---

#### `python/jdl_flash/prediction.py`
**Purpose:** Lightweight online ML forecasters for price and edge prediction;
pure Python, no external deps.

**Helpers:** `_dot`, `_mat_vec`, `_outer_add`, `_vadd`, `_eye`, `_sig`.

**Class `OnlineAR`** — Online autoregressive model (RLS).
- `__init__(p=5, lam=0.99)` — AR order `p`, forgetting factor `lam`.
- `update(x)` — update with new observation using RLS.
- `predict() -> Optional[float]` — one-step-ahead prediction.

**Class `RidgeForecaster`** — Batch ridge regression forecaster.
- `__init__(p=5, alpha=1.0)` — AR lag `p`, L2 regularization `alpha`.
- `_solve()` — fits coefficients via normal equations (Gram matrix + `alpha*I`).
- `update(x)` — append observation; rebuild coefficients when enough data.
- `forecast(h=1)` — multi-step forecast by iterating the AR recursion.

**Class `EdgeClassifier`** — Online logistic regression for edge profitability.
- `__init__(n_features, lr, l2)` — feature dimension, learning rate, L2 weight.
- `_prep(features)` — converts feature dict to padded float vector.
- `update(features, label)` — SGD step on one example.
- `predict_proba(features)` — sigmoid probability that the edge is profitable.

**Class `EWMAForecast`** — Double EWMA (level + trend).
- `__init__(alpha, beta)` — level and trend smoothing factors.
- `update(x)` — update smoothed level and trend.
- `forecast() -> Optional[float]` — one-step-ahead prediction.

**Class `ConfidenceScorer`** — Aggregates ensemble probabilities + calibrates
via historical accuracy.
- `__init__(window, agreement_weight)` — rolling accuracy window, ensemble weight.
- `record_outcome(predicted_profitable, was_profitable)` — updates calibration.
- `score(probas) -> float` — weighted combination of ensemble probabilities and calibration.

---

#### `python/jdl_flash/realness_guard.py`
**Purpose:** Production fault-tolerance backbone. Enforces real-values-only
discipline and provides fault-tolerant call wrappers.

**Class `RealnessGuard`:**
- `__init__(simulated_sentinels)` — extra values to always reject.
- `assert_real(value, name) -> bool` — True iff value is finite, non-None, non-NaN, non-inf, and not a flagged sentinel.
- `forbid_simulated(live, source) -> bool` — raises `ValueError` if `source` contains `sim/dry/fake/random` tokens in live mode.
- `validate_quote(amount_out, amount_in) -> bool` — both values real, `amount_in > 0`, ratio within `_MAX_RATIO`.

**Class `SystemDoctor`:**
- `register(name, check, critical)` — register a named health-check callable.
- `run_all() -> dict` — execute all checks; never raises; returns `{passed, failed, critical_failures, results}`.

**Module functions:**
- `safe(fn, *args, default, **kwargs) -> Any` — calls `fn`; returns `default` on any exception.
- `retry(fn, attempts, backoff_s) -> Any` — retries `fn()` up to `attempts` times with optional sleep.

---

#### `python/jdl_flash/revenue_reconciliation.py`
**Purpose:** Reconciles on-chain token balances against the off-chain SQLite
revenue ledger to detect discrepancies.

**Class `TokenBalance`:**
- `on_chain_human() -> float` — converts raw on-chain balance to human-readable float.

**Class `ReconciliationResult`:** Holds reconciliation output
(`matched`, `discrepancy_usd`, `details`).

**Key functions:**
- `reconcile(w3, contract_address, token_list, db_path) -> ReconciliationResult` — fetches on-chain balances, queries DB, computes discrepancy.
- `format_report(result) -> str` — formats result as a human-readable string.

---

#### `python/jdl_flash/rpc_endpoints.py`
**Purpose:** Single canonical definition of the Arbitrum RPC endpoint list.
Extracted so the engine and `jdl integrate` always use identical rules.

**Constants:**
- `ALCHEMY_ARB_URL` — Alchemy Arbitrum mainnet URL template.
- `PUBLIC_ARB_RPC` — public Arbitrum One fallback.
- `_NON_NUMERIC_SUFFIX_SORT_KEY` — sort key for non-numeric `RPC_URL*` suffixes.

**Functions:**
- `_env_first(env, *names, default) -> str` — first non-empty value among alias names.
- `is_valid_rpc(url: str) -> bool` — rejects empty URLs, `YOUR_ALCHEMY`/`YOUR_KEY` placeholders, and URLs with spaces. Case-insensitive.
- `build_rpc_endpoints(env) -> List[str]` — builds de-duplicated, ordered Arbitrum RPC list: Alchemy key → ALCHEMY_KEY_* → RPC_URL group → RPC_URLn → RPC_FALLBACKS → public node.

---

#### `python/jdl_flash/swarm_daemon.py`
**Purpose:** `jdl swarm` foreground daemon. Runs the `BotSwarm` in an infinite
loop with signal-based graceful shutdown.

**Functions:**
- `_request_shutdown(signum, _frame)` — sets the shutdown flag on SIGTERM/SIGINT.
- `main() -> int` — builds the `SwarmCoordinator`, starts the `BotSwarm`, runs
  until shutdown signal, joins workers, returns exit code.

---

#### `python/jdl_flash/swarm_runtime.py`
**Purpose:** Wires the `BotSwarm` into the live engine for maximum-parallel
scanning and nonce-safe parallel execution.

**Module functions:**
- `resolve_workers(spec, cpu) -> int` — resolves worker count from `SWARM_WORKERS` env, `spec` string, or `cpu` count; clamps to `[1, 32]`.
- `build_route_universe(tokens, fee_tiers) -> List[dict]` — builds all directed token-pair × fee-tier combinations.
- `partition(routes, worker_id, n_workers) -> List[Any]` — returns the `worker_id`-th disjoint slice of routes.
- `route_key(route) -> str` — stable string key for a route dict.

**Class `SwarmCoordinator`:**
- `__init__(w3, contract, private_key, token_list, fee_tiers, min_profit_usd, gas_usd_est, loan_usd, …)` — full coordinator config; loads `jdl_native` for the hot-path.
- `_default_best_cycle(request) -> dict` — calls `jdl_native.scan()` for arbitrage cycle detection.
- `_fresh(key) -> bool` — deduplication: returns True if this opportunity key hasn't been seen within the TTL.
- `reset_dedup()` — clears dedup cache.
- `_record_leg_fee(frm, to, rate, fee_tier)` — records a per-leg fee tier observation.
- `_fees_for_path(path) -> List[Optional[int]]` — reconstructs the fee tier list for a path.
- `scan_fn(worker_id, n_workers) -> List[dict]` — fetches real quotes for this worker's route slice, builds `ScanRequest`, calls hot-path, deduplicates.
- `exec_fn(opportunity, nonce) -> Any` — builds and broadcasts the `initiateFlashLoan` tx.
- `make_swarm(n_workers, nonce_base) -> BotSwarm` — instantiates a `BotSwarm` wired to this coordinator.

---

#### `python/jdl_flash/triangular_scanner.py`
**Purpose:** Finds the best triangular arbitrage route (A→B→C→A) using
real on-chain quotes.

**Type aliases:** `TokenRegistry = Dict[str, Dict]`, `QuoteFn`.

**Class `TriangularScanner`:**
- `__init__(token_registry, quote_fn, fee_tiers, max_hops, aave_premium_bps)` — configures the scanner.
- `_to_base(symbol, amount_human) -> Optional[int]` — converts human amount to token base units.
- `_to_human(symbol, amount_base) -> Optional[float]` — converts base units to human amount.
- `_safe_quote(token_in, token_out, amount_in, fee) -> Optional[int]` — wraps `quote_fn`; returns `None` on error.
- `_aave_repay(amount_base) -> int` — amount to repay including Aave premium.
- `_probe_routes(start, amount_in_base, visited, depth) -> List[dict]` — recursive DFS over token hops; collects all profitable cycles.
- `best_triangle(start_symbol, amount_human, fee_tier) -> Optional[dict]` — returns the single best triangular route.
- `scan(start_symbol, amount_human, fee_tiers) -> List[dict]` — scans all configured fee tiers and returns all profitable routes sorted by profit.

---

#### `python/jdl_flash/wallet_lanes.py`
**Purpose:** Multi-wallet execution lanes for parallel on-chain execution.
Each `Lane` maps a private key + contract address + nonce to a single execution
slot, enabling true parallel execution beyond single-wallet nonce constraints.

**Class `Lane`:** Named tuple: `(private_key, contract_address, nonce, wallet_address)`.

**Class `LaneConfigError(ValueError)`:** Raised on invalid lane configuration.

**Functions:**
- `_split_csv(s) -> List[str]` — splits a comma-separated string, stripping whitespace.
- `_address_of(priv_key) -> str` — derives checksummed Ethereum address from a private key.
- `build_lanes(env, n_lanes) -> List[Lane]` — builds lanes from env vars `PRIVATE_KEY`, `PRIVATE_KEY_2`…`PRIVATE_KEY_N`, each with its own `FLASH_CONTRACT_ADDRESS_N`. Validates that the number of available wallets ≥ `n_lanes`.
- `verify_lane_ownership(eth_call, lane) -> Optional[str]` — calls `owner()` on the contract to verify the lane's wallet is the owner; returns error string or `None`.

---

### 4.3 Python — `jdl_native` package

#### `python/jdl_native/__init__.py`
**Purpose:** Backend selector for the Rust hot-path. Chooses the best
available backend at import time.

**Backend priority (overridable via `JDL_NATIVE_BACKEND`):**
1. `cython` — compiled `.so` Cython extension (fastest).
2. `ctypes` — loads the Rust cdylib via `_ctypes_backend.py`.
3. `subprocess` — spawns the `jdl-hotpath` CLI binary.
4. `python` — pure-Python DFS fallback.

**Public API:**
- `scan(request: dict) -> dict` — runs the arbitrage hot-path scan.
- `analyze(bytecode: str) -> dict` — analyzes EVM bytecode (requires native backend).
- `active_backend() -> str` — returns the name of the backend in use.
- `AnalysisUnavailable` — exception raised when `analyze()` has no native backend.

#### `python/jdl_native/_pyfallback.py`
**Purpose:** Pure-Python faithful port of the Rust `best_cycle`. DFS over the
adjacency graph of exchange edges; handles Aave premium and gas deduction.

- `_effective(edge) -> float` — edge rate after fee deduction.
- `_net_profit(mult, loan_usd, gas_usd) -> float` — net profit = gross gain − premium − gas.
- `scan(request: dict) -> dict` — DFS arbitrage scanner; returns `{opportunity, tokens, edges}`.
- `analyze(bytecode: str)` — raises `NotImplementedError` (no pure-Python EVM analysis).

#### `python/jdl_native/_ctypes_backend.py`
**Purpose:** Loads `libjdl_hotpath` (Rust cdylib) via ctypes. Calls `jdl_scan`
and `jdl_analyze` from the C ABI.

- `available() -> bool` — True if the shared library can be found and loaded.
- `scan(request: dict) -> dict` — marshals dict → JSON → C string → Rust → JSON → dict.
- `analyze(bytecode: str) -> dict` — same round-trip for `jdl_analyze`.

#### `python/jdl_native/_jdl.pyx`
**Purpose:** Cython extension source. Wraps the Rust cdylib via `cdef extern`
declarations from `jdl_hotpath.h`. Compiled to a `.so` by `setup.py`.

#### `python/jdl_native/jdl_hotpath.h`
**Purpose:** C ABI header for `libjdl_hotpath`. Declares:
- `char *jdl_scan(const char *input)` — ScanRequest JSON → ScanResult JSON.
- `char *jdl_analyze(const char *input)` — `{"bytecode":"0x.."}` → AnalysisReport JSON.
- `void jdl_string_free(char *ptr)` — frees strings returned by the above.

#### `python/jdl_native/setup.py`
Cython `build_ext` script. Compiles `_jdl.pyx` and links against
`libjdl_hotpath`.

---

### 4.4 Python — Root Modules

#### `python/flash_supervisor.py`
**Purpose:** Daemon supervisor with auto-restart and revenue threshold
monitoring. Runs a target script (`engine` or `swarm`) as a subprocess; if it
dies, restarts it after a backoff; when cumulative revenue passes
`THRESHOLD`, initiates a withdrawal.

**Constants:** `DATA_DIR`, `PID_FILE`, `LOG_FILE`, `THRESHOLD`.

**Class `FlashSupervisor`:**
- `__init__(script, …)` — target script path + restart policy.
- `run()` — main supervisor loop: spawn, monitor, restart, withdraw.

**Module-level helpers:**
- `resolve_target(target) -> Path` — maps `engine`/`swarm` names to scripts.
- `total_profit() -> float` — reads cumulative profit from SQLite.
- `exec_count() -> int` — reads execution count from SQLite.

#### `python/gas_kernel.py`
**Purpose:** Standalone gas estimation kernel. Fetches the current base fee and
priority fee from the chain and computes a recommended `maxFeePerGas` and
`maxPriorityFeePerGas` for EIP-1559 transactions.

**Key functions:**
- `estimate_gas_params(w3, urgency) -> dict` — returns `{maxFeePerGas, maxPriorityFeePerGas, baseFee, estimated_gwei}`.
- `gas_usd_estimate(gas_limit, gas_params, eth_price_usd) -> float` — converts gas cost to USD.

#### `python/trading_core.py`
**Purpose:** Legacy monolithic trading engine (v4.0). The predecessor to
`flash_loan_engine.py`. Contains a full terminal dashboard, DEX interaction
stubs, multi-chain config, and a large menu system. Kept for reference;
`flash_loan_engine.py` is the active engine.

---

### 4.5 Revenue System

#### `revenue_system/chain_monitor_fixed.py`
**Purpose:** On-chain transaction monitor (v2, fixed). Polls the Arbitrum node
for new blocks, filters transactions to/from the contract, and records revenue
events in SQLite with explicit error logging.

**Classes:**
- `TransactionRecord` — dataclass: tx hash, block, timestamp, value, gas, status.
- `RevenueEvent` — dataclass: event type, amount, token, timestamp, tx hash.
- `ChainMonitor` — main monitor class.
  - `__init__(rpc_url, contract_address, db_path)` — sets up web3 + DB.
  - `start() / stop()` — start/stop the polling loop.
  - `_process_block(block_number)` — fetches block, filters relevant txs, records events.
  - `_record_transaction(tx)` — inserts a `TransactionRecord` into SQLite.
  - `_record_revenue_event(event)` — inserts a `RevenueEvent` into SQLite.
  - `get_stats() -> dict` — returns `{total_revenue, tx_count, event_count, last_block}`.

#### `revenue_system/revenue_reconciliation.py`
**Purpose:** Compares on-chain state to the SQLite ledger to detect
discrepancies between recorded and actual balances.

**Functions:**
- `fetch_on_chain_balances(w3, contract_address, tokens) -> dict` — fetches ERC-20 balances.
- `fetch_db_balances(db_path, tokens) -> dict` — queries SQLite for recorded balances.
- `reconcile(w3, contract_address, tokens, db_path) -> dict` — returns discrepancy report.
- `format_discrepancy_report(report) -> str` — formats as human-readable string.

#### `revenue_system/revenue_recording.py`
**Purpose:** SQLite revenue recorder. All execution results are written here
for tracking, reporting, and reconciliation.

**Class `RevenueRecorder`:**
- `__init__(db_path)` — connects to SQLite; creates tables if needed.
- `record_execution(tx_hash, profit_usd, token, amount, gas_usd, timestamp)` — inserts execution record.
- `total_profit(since=None) -> float` — sums profit since optional timestamp.
- `execution_count(since=None) -> int` — counts executions.
- `get_history(limit=100) -> list` — returns recent execution records.
- `close()` — closes the DB connection.

---

### 4.6 Solidity Contracts

#### `contracts/contracts/NexusFlashReceiver.sol`
**Purpose:** Main on-chain contract. Receives Aave V3 flash loans and executes
multi-step arbitrage (Uniswap V3, Curve, Balancer) atomically. Also supports
Gelato Relay ERC-2771 for sponsored (zero-gas) execution.

**Inheritance:** `ReentrancyGuard`, `Pausable`, `Ownable`, `GelatoRelayERC2771Context`

**Abstract base `GelatoRelayERC2771Context`:**
- `onlyGelatoRelayERC2771` modifier — rejects calls not from the Gelato Relay.
- `_getFeeCollector() / _getFeeToken() / _getFee() / _getMsgSender()` — reads relay fee metadata from calldata.
- `_transferRelayFeeCapped(maxFee)` — transfers relay fee from contract balance, bounded by `maxFee`.

**Events:**
- `ArbitrageExecuted(asset, amount, profit, steps)` — emitted after a successful arb.
- `OwnerUpdated(oldOwner, newOwner)` — emitted on ownership transfer.
- `TokensRescued(token, amount, to)` — emitted on token rescue.

**Modifiers:**
- `onlyAavePool` — reverts if caller is not the configured Aave V3 pool.

**Core functions:**
- `constructor(aavePool, uniRouter, balancerVault, owner)` — sets immutable protocol addresses and explicit owner.
- `executeOperation(asset, amount, premium, initiator, params)` — Aave flash-loan callback; decodes and executes each step; verifies profit; repays loan.
- `_executeStep(asset, amount, step)` — dispatches one swap step to Uniswap V3, Curve, or Balancer.
- `_swapUniswapV3(tokenIn, tokenOut, fee, amountIn, amountOutMin)` — calls `exactInputSingle`.
- `_swapCurve(pool, i, j, amountIn, minOut)` — calls `exchange` on a Curve pool.
- `_swapBalancer(poolId, tokenIn, tokenOut, amountIn, minOut)` — calls `swap` on the Balancer Vault.
- `_sweep(asset, to)` — transfers all of `asset` to `to`.
- `rescueTokens(token, amount, to)` — owner-only ERC-20 rescue.
- `rescueETH(amount, to)` — owner-only ETH rescue.
- `pause() / unpause()` — owner-only circuit breaker.
- `initiateFlashLoan(asset, amount, encodedSteps)` — owner-only direct trigger; encodes steps and calls Aave `flashLoanSimple`.
- `initiateFlashLoanRelay(asset, amount, encodedSteps, maxFee)` — Gelato ERC-2771 trigger; verifies caller via `_getMsgSender()`, then calls Aave.

#### `contracts/contracts/ArbitrageLib.sol`
**Purpose:** Pure Solidity library for encoding swap steps and computing profit.
Used by `NexusFlashReceiver` to build `params` calldata.

**Functions:**
- `encodeStep(dex, tokenIn, tokenOut, …) -> bytes` — ABI-encodes one swap step.
- `encodeSteps(steps[]) -> bytes` — concatenates multiple encoded steps.
- `calcMinProfit(borrowed, premium, gasCost) -> uint256` — minimum output that covers loan repayment + gas.

#### `contracts/contracts/FlashZeroGas.sol`
**Purpose:** Gelato Relay ERC-2771-based wrapper that makes the contract
self-funding (the contract pays its own relay fee from profit).
Wraps calls to `NexusFlashReceiver.initiateFlashLoanRelay`.

#### `contracts/contracts/ProfitPaymaster.sol`
**Purpose:** ERC-4337 paymaster that sponsors gas for flash-loan transactions
using profit accumulated in the contract. Implements `validatePaymasterUserOp`
and `postOp`.

#### Interfaces
- `IAaveV3Pool.sol` — `flashLoanSimple(receiver, asset, amount, params, referralCode)`.
- `IBalancerVault.sol` — `swap(SingleSwap, FundManagement, limit, deadline) -> uint256`; enums `SwapKind`, structs `SingleSwap` / `FundManagement`.
- `ICurvePool.sol` — `exchange(i, j, dx, min_dy) -> uint256`; `get_dy(i, j, dx)`; `coins(i)`.
- `IUniswapV3Router.sol` — `exactInputSingle(ExactInputSingleParams) -> uint256`; struct `ExactInputSingleParams`.

---

### 4.7 Node.js Server

#### `node/src/chain.js`
**Purpose:** Ethers.js v6 read-only interactions with `NexusFlashReceiver`.

**Exports:**
- `NEXUS_ABI` — minimal ABI (owner, paused, immutables, flash-loan functions).
- `makeProvider(rpcUrl) -> JsonRpcProvider` — constructs an ethers provider.
- `chainHealth(provider, expectedChainId) -> Promise<{chainId, block, isArbitrum}>` — fetches chain ID and latest block.
- `contractState(provider, address) -> Promise<{configured, owner, paused, aavePool, uniswapV3Router, balancerVault, supportsRelay, supportsRescueEth}>` — reads all public state from the contract.

#### `node/src/hotpath.js`
**Purpose:** Bridge to the Rust `jdl-hotpath` binary. Spawns it as a child
process, writes a `ScanRequest` JSON to stdin, reads a `ScanResult` JSON from
stdout.

**Exports:**
- `HOTPATH_BIN` — path to the release binary (env-overridable via `HOTPATH_BIN`).
- `HOTPATH_TIMEOUT_MS` — kill timeout (default 15 s; env-overridable).
- `hotpathAvailable() -> bool` — True if the binary exists at `HOTPATH_BIN`.
- `scan(req) -> Promise<{opportunity, tokens, edges}>` — spawns the binary, sends `ScanRequest`, resolves with `ScanResult`; kills and rejects on timeout.

#### `node/src/index.js`
**Purpose:** Express HTTP API + orchestration server. Ties Rust, Solidity, and
Node together. Intentionally does NOT hold private keys.

**HTTP endpoints:**
- `GET /health` — `{ok, hotpath, chain, rpc, hotpathBin}`; 503 on chain error.
- `GET /contract` — `contractState(...)` result; 502 on error.
- `POST /scan` — validates body `{edges, base, loan_usd, gas_usd, [min_profit_usd]}`, calls `hotpath.scan()`, returns `ScanResult`; 400 on bad input, 500 on engine error.

---

### 4.8 Rust Hot-Path

#### `rust/hotpath/src/lib.rs`
**Purpose:** Main cdylib entry point. Exposes both the arbitrage algorithm and
the EVM bytecode analyzer, plus a C ABI for Python ctypes/Cython.

**Structs:**
- `Edge` — directed exchange edge: `from`, `to`, `rate`, `fee_bps`.
- `ScanRequest` — `{edges, base, loan_usd, gas_usd, min_profit_usd}`.
- `Opportunity` — `{path, gross_multiplier, net_profit_usd, fee_tiers}`.
- `ScanResult` — `{opportunity: Option<Opportunity>, tokens, edges}`.
- `FunctionSummary` — recovered function: selector, name, params, is_view, is_payable.
- `AnalysisReport` — full bytecode analysis: functions, security, storage, decompiled source, instructions, blocks.

**Public Rust functions:**
- `best_cycle(req: &ScanRequest) -> ScanResult` — runs Bellman-Ford over
  `−ln(rate × (1 − fee_bps/10000))` weights to find the most profitable
  arbitrage cycle; returns the top cycle after net profit filtering.
- `net_profit(mult, req) -> f64` — `loan_usd × (mult − 1) − premium − gas`.
- `analyze_bytecode(hex_code: &str) -> Result<AnalysisReport, String>` — full
  pipeline: disassemble → CFG → symbolic execution → decompile → security scan → function recovery.

**C ABI (for Python ctypes / Cython):**
- `jdl_scan(input: *const c_char) -> *mut c_char` — JSON ScanRequest → JSON ScanResult.
- `jdl_analyze(input: *const c_char) -> *mut c_char` — JSON `{bytecode}` → JSON AnalysisReport.
- `jdl_string_free(ptr: *mut c_char)` — frees strings allocated by the above two.

#### `rust/hotpath/src/main.rs`
**Purpose:** CLI binary entry point. Reads a JSON request from stdin, detects
whether it is a scan or analyze request, dispatches to `best_cycle` or
`analyze_bytecode`, and writes the JSON result to stdout.

#### `rust/hotpath/src/evm/mod.rs`
Re-exports all EVM sub-modules.

#### `rust/hotpath/src/evm/disasm.rs`
**Purpose:** Linear-sweep EVM disassembler.

**Structs:**
- `Instruction` — `{offset, opcode, mnemonic, imm, imm_u256, category, stack_in, stack_out}`.
- `Disassembly` — `{instructions, jumpdests, total_bytes, instruction_count}`.

**Functions:**
- `disassemble(bytecode: &[u8]) -> Disassembly` — decodes every instruction; populates JUMPDEST map.
- `jumpdest_set(disasm: &Disassembly) -> FxHashSet<usize>` — JUMPDEST offsets as a hash set.

#### `rust/hotpath/src/evm/opcodes.rs`
**Purpose:** Complete EVM opcode table (Frontier → Cancun, including PUSH0).

**Struct `Opcode`:** `{code, mnemonic, stack_in, stack_out, imm_bytes, category, description}`.

**Enum `OpcodeCategory`:** Stop, Arithmetic, Comparison, Bitwise, Sha3, EnvInfo,
BlockInfo, Stack, Memory, Storage, Flow, Log, System, Invalid.

**Functions:**
- `lookup(byte: u8) -> &'static Opcode` — O(1) table lookup.
- `push_size(byte: u8) -> u8` — returns 0 for non-PUSH, 1-32 for PUSH1-PUSH32.

#### `rust/hotpath/src/evm/cfg.rs`
**Purpose:** Builds a Control-Flow Graph from a disassembly.

**Structs:**
- `BasicBlock` — `{id, start_offset, end_offset, instructions, successors, predecessors, block_type}`.
- `CFG` — `{blocks, entry_block_id, offset_to_block}`.
  - `block_count() / edge_count()` — metrics.

**Enum `BlockType`:** Entry, Normal, JumpDest, Terminal, Invalid.

**Functions:**
- `build_cfg(disasm: &Disassembly) -> CFG` — marks block leaders, slices into BasicBlocks, resolves JUMP/JUMPI targets and fallthroughs.

#### `rust/hotpath/src/evm/symbolic.rs`
**Purpose:** Inter-block symbolic execution engine. Propagates symbolic state
across CFG blocks.

**Enum `Val`:** Rich symbolic value type: `Const`, `MsgSender`, `MsgValue`,
`CalldataLoad`, `SLoad`, `Add`, `Sub`, `Mul`, `Shr`, `And`, `Eq`, `Iszero`,
`Phi`, `Unknown`, and more.

**Struct `Stmt`:** Symbolic statement: `Assign`, `SStore`, `Revert`, `Return`, `Require`, `Log`, `Call`, `EmitEvent`.

**Struct `SymExec`:** Symbolic execution context.
- `run(cfg, disasm) -> HashMap<usize, BlockResult>` — executes all reachable blocks; folds constants; recovers calldata params, storage reads/writes, require patterns, and access-control patterns.

#### `rust/hotpath/src/evm/decompiler.rs`
**Purpose:** Pseudo-Solidity decompiler.

**Structs:**
- `DecompiledOutput` — `{pseudo_source, functions, storage_slots, total_params}`.
- `DecompiledFn` — `{selector, name, params, body, is_view, is_payable, start_block, block_ids}`.
- `StorageSlotOut` — `{slot, name, ty, reads, writes}`.

**Pipeline:**
1. `SymExec::run()` — inter-block symbolic execution + constant folding.
2. `lift_functions()` — groups blocks into functions via dispatcher + DFS.
3. `structurize()` — converts IfGoto chains to if/else/require.
4. `emit_solidity()` — renders clean pseudo-Solidity source.

#### `rust/hotpath/src/evm/security.rs`
**Purpose:** Bytecode-level security pattern detector.

**Enum `Severity`:** Critical, High, Medium, Low, Info.

**Struct `Finding`:** `{severity, title, description, offset, pattern}`.

**Struct `SecurityReport`:** `{findings, has_selfdestruct, has_delegatecall, has_create2, has_staticcall, sstore_count, sload_count, call_count, risk_score}`.

**Functions:**
- `analyze_security(disasm: &Disassembly) -> SecurityReport` — scans all
  instructions for dangerous patterns (SELFDESTRUCT, DELEGATECALL, unguarded
  CREATE2, tx.origin checks, unchecked calls) and computes a risk score 0-100.

#### `rust/hotpath/src/evm/signatures.rs`
**Purpose:** Recovers function dispatcher signatures from bytecode by scanning
for `PUSH4 → EQ → JUMPI` patterns.

**Structs:**
- `FunctionSig` — `{selector, selector_u32, known_name, jump_target, is_view}`.
- `SignatureReport` — `{functions, event_topics, has_dispatcher, fallback_offset}`.

**Built-in 4-byte lookup** covers common ERC-20, ERC-721, Uniswap, Aave, and
OpenZeppelin selectors.

**Function:**
- `recover_signatures(disasm: &Disassembly) -> SignatureReport` — finds all function selectors and looks them up in the built-in table.

#### `rust/hotpath/src/evm/types.rs`
**Purpose:** EVM type inference from usage patterns.

**Enum `EvmType`:** Address, Uint(n), Int(n), Bool, Bytes(n), BytesDynamic, StringType, Mapping(k,v), Array(t), Unknown.
- `solidity_name() -> String` — renders as Solidity type string.
- `from_shr_shift(shift) -> Option<Self>` — address from `>> 96`, selector from `>> 224`, etc.
- `from_and_mask(mask) -> Option<Self>` — infers type width from bitwise AND masks.

**Struct `TypeCtx`:** Tracks inferred types per stack slot and storage slot.
- `infer(val: &Val) -> EvmType` — infers type from a symbolic value.
- `record_storage_type(slot, ty)` — updates storage type map.

---

### 4.9 Database

#### `database/revenue_schema.sql`
**Purpose:** SQLite schema for the revenue tracking database.

**Tables:**
- `executions` — `(id, tx_hash, block_number, timestamp, asset, amount_wei, profit_wei, profit_usd, gas_used, gas_price_gwei, status)`.
- `revenue_events` — `(id, event_type, amount_usd, token, timestamp, tx_hash, notes)`.
- `daily_summary` — `(date, total_profit_usd, execution_count, avg_profit_usd, max_profit_usd)`.
- `reconciliation_log` — `(id, timestamp, on_chain_balance, db_balance, discrepancy, resolved)`.

**Views:**
- `v_recent_executions` — last 100 successful executions.
- `v_daily_stats` — daily aggregations.

---

### 4.10 Scripts

#### `scripts/termux-install.sh`
Full Termux installer: installs system packages, creates venv at `~/.flash_venv`,
installs Python deps, wires `~/jdl/.env`, optionally installs Termux:Boot hook.

#### `scripts/userland-setup.sh`
Full UserLAnd / Ubuntu / WSL installer: installs system packages (apt), Node.js,
Foundry, Rust, Python venv, wires `.env`, optionally starts the engine.

#### `scripts/run-all-tests.sh`
Runs every test suite in the same order as CI:
1. All Python test files (`test_*.py`) via `jdl test`.
2. Node.js tests (`npm test` in `node/`).
3. Rust tests (`cargo test` in `rust/hotpath/`).
Reports pass/fail per suite and exits non-zero if any fail.

#### `scripts/termux-verify.sh`
Post-install verification for Termux: checks Python/Node versions, venv
presence, `.env` keys, RPC reachability, and contract address.

#### `scripts/security-audit.sh`
Runs `npm audit` (contracts + node), `pip-audit` (python), and `cargo audit`
(rust). Fails the build if any high/critical CVEs are found.

#### `scripts/start-swarm-daemon.sh`
Starts the `jdl swarm` daemon in the background via `nohup` or `screen`;
writes the PID file.

#### `scripts/termux-boot-swarm.sh`
Termux:Boot hook: auto-starts the swarm daemon when the Android device boots.

#### `scripts/setup.ps1`
Windows PowerShell setup: installs Python (winget), Node.js, creates venv,
installs Python/Node deps.

#### `scripts/deploy_termux.sh`
One-shot Termux deploy helper: pulls latest, reinstalls, optionally deploys
the contract and starts the engine.

#### `scripts/push_revenue_system.sh`
Pushes the `revenue_system/` directory to the production server via rsync/git.

---

### 4.11 CI / GitHub Actions

#### `.github/workflows/ci.yml`
**Jobs:**
- `python-tests` — installs deps, runs all Python test suites.
- `node-tests` — `npm ci`, `npm test` in `node/`.
- `rust-tests` — `cargo test` in `rust/hotpath/`.
- `contracts-compile` — `npm ci`, `npx hardhat compile` in `contracts/`.

#### `.github/workflows/release.yml`
Triggered on version tags (`v*`). Builds Python wheel, Rust binary, and creates
a GitHub release with attached artifacts.

#### `.github/workflows/security.yml`
Runs `scripts/security-audit.sh` on push and on a weekly schedule.

#### `.github/dependabot.yml`
Configures Dependabot for npm (contracts + node), pip (python), cargo (rust),
and GitHub Actions — weekly schedule.

---

### 4.12 Documentation

| File | Contents |
|------|----------|
| `README.md` | Primary documentation: quickstart, architecture, env vars, commands |
| `README_FLASH.md` | Flash-loan engine quickstart (condensed) |
| `TERMUX.md` | Termux/Android install and usage guide |
| `POLYGLOT.md` | Explains the multi-language native bridge (Cython/ctypes/subprocess/Python fallback) |
| `docs/INTEGRATION_GUIDE.txt` | Step-by-step integration reference for all components |
| `docs/README_REVENUE_SYSTEM.md` | Revenue system documentation |
| `docs/SEPOLIA_DRYRUN.md` | Guide for a Sepolia testnet dry run |
| `docs/TERMUX_DEEPDIVE.md` | Advanced Termux setup (boot hooks, screen, battery optimization) |
| `docs/TERMUX_WALKTHROUGH.md` | Termux step-by-step walkthrough |
| `docs/USERLAND.md` | UserLAnd (proot-Ubuntu on Android) setup guide |

---

### 4.13 Configuration & Templates

| File | Purpose |
|------|---------|
| `.env.template` | Documented template with all supported env vars and inline comments |
| `.env.production.template` | Production-focused template (stricter) |
| `.env.example` | Minimal example |
| `python/.env.production.template` | Copy of production template inside the package |
| `python/pyproject.toml` | Package metadata, entry-points (`flashloan`, `flashpro`, `jdl`), dep groups |
| `python/requirements_flash.txt` | pip requirements for the flash engine |
| `contracts/foundry.toml` | Foundry config: `src`, `test`, `out`; fork RPC from `ARB_RPC_URL` |
| `contracts/hardhat.config.js` | Hardhat config: forking Arbitrum at `ARB_RPC_URL` |
| `contracts/package.json` | npm scripts; overrides to suppress hardhat CVEs |
| `node/package.json` | node server deps (express, ethers) and test script |
| `rust/hotpath/Cargo.toml` | crate metadata; cdylib + bin targets; deps: serde, ethnum, rustc-hash, hex |

---

### Key Environment Variables

| Variable | Where Used | Description |
|----------|-----------|-------------|
| `PRIVATE_KEY` | engine, deploy | Wallet private key (hex, no `0x` prefix) |
| `PRIVATE_KEY_2`…`N` | wallet_lanes | Additional wallets for parallel execution |
| `ALCHEMY_ARB_KEY` | engine, integrate | Alchemy Arbitrum API key |
| `RPC_URL` | engine, integrate | Arbitrum RPC URL (alias: `ARBITRUM_RPC_URL`, `ARB_RPC_URL`) |
| `ARB_RPC_URL` | Hardhat/Foundry | Arbitrum fork RPC for contract tests |
| `RPC_URL_2`…`N` | engine | Numbered fallback RPC URLs |
| `RPC_FALLBACKS` | engine | Comma-separated RPC fallback list |
| `FLASH_CONTRACT_ADDRESS` | engine, integrate | Deployed NexusFlashReceiver address |
| `FLASH_CONTRACT_ADDRESS_2`…`N` | wallet_lanes | Contracts for additional wallets |
| `CHAIN_ID` | engine, integrate | Expected chain ID (default: 42161, Arbitrum One) |
| `GELATO_API_KEY` | gelato_relay | Gelato Relay API key for sponsored execution |
| `SWARM_WORKERS` | swarm_runtime | Override worker count |
| `JDL_NATIVE_BACKEND` | jdl_native | Force backend: `cython`/`ctypes`/`subprocess`/`python` |
| `JDL_HOTPATH_BIN` | jdl_native | Override path to the `jdl-hotpath` binary |
| `HOTPATH_BIN` | node/hotpath.js | Override path to the binary in Node context |
| `HOTPATH_TIMEOUT_MS` | node/hotpath.js | Kill timeout for hot-path subprocess (default: 15000) |
| `TERMUX_VERSION` | platform_detect | Set by Termux; used to detect Termux runtime |

---

*End of CODEBASE.md*
