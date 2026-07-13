# jdl-production-core Repository Codebase Analysis

Generated repository inventory.

## File Tree

.
├── .env.example
├── .env.production.template
├── .env.template
├── .github
│   ├── dependabot.yml
│   └── workflows
│       ├── ci.yml
│       ├── release.yml
│       └── security.yml
├── .gitignore
├── LICENSE
├── POLYGLOT.md
├── README.md
├── README_FLASH.md
├── TERMUX.md
├── contracts
│   ├── .gitignore
│   ├── README.md
│   ├── contracts
│   │   ├── ArbitrageLib.sol
│   │   ├── FlashZeroGas.sol
│   │   ├── NexusFlashReceiver.sol
│   │   ├── ProfitPaymaster.sol
│   │   └── interfaces
│   │       ├── IAaveV3Pool.sol
│   │       ├── IBalancerVault.sol
│   │       ├── ICurvePool.sol
│   │       └── IUniswapV3Router.sol
│   ├── foundry.toml
│   ├── hardhat.config.js
│   ├── package-lock.json
│   ├── package.json
│   ├── scripts
│   │   ├── deploy-arb-lib.js
│   │   └── deploy-flash-receiver.js
│   └── test
│       ├── NexusFlashReceiver.t.sol
│       ├── NexusFlashReceiverInvariant.t.sol
│       ├── ProfitPaymaster.t.sol
│       └── fork-flash.test.js
├── database
│   └── revenue_schema.sql
├── docs
│   ├── INTEGRATION_GUIDE.txt
│   ├── README_REVENUE_SYSTEM.md
│   ├── REPOSITORY_CODEBASE_ANALYSIS.md
│   ├── SEPOLIA_DRYRUN.md
│   ├── TERMUX_DEEPDIVE.md
│   ├── TERMUX_WALKTHROUGH.md
│   └── USERLAND.md
├── node
│   ├── package-lock.json
│   ├── package.json
│   ├── src
│   │   ├── chain.js
│   │   ├── hotpath.js
│   │   └── index.js
│   └── test
│       ├── hotpath.test.js
│       └── scan_validation.test.js
├── python
│   ├── .env.production.template
│   ├── flash_supervisor.py
│   ├── gas_kernel.py
│   ├── jdl_flash
│   │   ├── __init__.py
│   │   ├── _paths.py
│   │   ├── advanced_math.py
│   │   ├── artifacts
│   │   │   └── NexusFlashReceiver.json
│   │   ├── bot_swarm.py
│   │   ├── cli.py
│   │   ├── deploy_gelato.py
│   │   ├── deploy_receiver.py
│   │   ├── env_autowire.py
│   │   ├── flash_loan_engine.py
│   │   ├── flash_pro.py
│   │   ├── gelato_relay.py
│   │   ├── integrate.py
│   │   ├── loan_optimizer.py
│   │   ├── market_analysis.py
│   │   ├── pattern_recognition.py
│   │   ├── platform_detect.py
│   │   ├── prediction.py
│   │   ├── realness_guard.py
│   │   ├── revenue_reconciliation.py
│   │   ├── rpc_endpoints.py
│   │   ├── swarm_daemon.py
│   │   ├── swarm_runtime.py
│   │   ├── test_bot_swarm.py
│   │   ├── test_cli.py
│   │   ├── test_config_validation.py
│   │   ├── test_env_autowire.py
│   │   ├── test_flash_engine.py
│   │   ├── test_integrate.py
│   │   ├── test_platform_detect.py
│   │   ├── test_revenue_reconciliation.py
│   │   ├── test_swarm_daemon.py
│   │   ├── test_swarm_runtime.py
│   │   ├── test_swarm_wiring.py
│   │   ├── test_wallet_lanes.py
│   │   ├── triangular_scanner.py
│   │   └── wallet_lanes.py
│   ├── jdl_native
│   │   ├── .gitignore
│   │   ├── __init__.py
│   │   ├── _ctypes_backend.py
│   │   ├── _jdl.pyx
│   │   ├── _pyfallback.py
│   │   ├── jdl_hotpath.h
│   │   ├── setup.py
│   │   └── test_jdl_native.py
│   ├── pyproject.toml
│   ├── requirements_flash.txt
│   ├── test_flash_supervisor.py
│   └── trading_core.py
├── revenue_system
│   ├── __init__.py
│   ├── chain_monitor_fixed.py
│   ├── revenue_reconciliation.py
│   └── revenue_recording.py
├── rust
│   └── hotpath
│       ├── Cargo.lock
│       ├── Cargo.toml
│       └── src
│           ├── evm
│           │   ├── cfg.rs
│           │   ├── decompiler.rs
│           │   ├── disasm.rs
│           │   ├── mod.rs
│           │   ├── opcodes.rs
│           │   ├── security.rs
│           │   ├── signatures.rs
│           │   ├── symbolic.rs
│           │   └── types.rs
│           ├── lib.rs
│           └── main.rs
├── scripts
│   ├── deploy_termux.sh
│   ├── push_revenue_system.sh
│   ├── run-all-tests.sh
│   ├── security-audit.sh
│   ├── setup.ps1
│   ├── start-swarm-daemon.sh
│   ├── termux-boot-swarm.sh
│   ├── termux-install.sh
│   ├── termux-verify.sh
│   └── userland-setup.sh
├── setup.sh
└── start.sh

23 directories, 129 files

## File List
./TERMUX.md
./.env.template
./start.sh
./POLYGLOT.md
./README.md
./python/gas_kernel.py
./python/pyproject.toml
./python/test_flash_supervisor.py
./python/jdl_flash/test_swarm_daemon.py
./python/jdl_flash/test_env_autowire.py
./python/jdl_flash/test_swarm_runtime.py
./python/jdl_flash/bot_swarm.py
./python/jdl_flash/test_swarm_wiring.py
./python/jdl_flash/swarm_daemon.py
./python/jdl_flash/gelato_relay.py
./python/jdl_flash/test_bot_swarm.py
./python/jdl_flash/market_analysis.py
./python/jdl_flash/cli.py
./python/jdl_flash/test_wallet_lanes.py
./python/jdl_flash/flash_loan_engine.py
./python/jdl_flash/rpc_endpoints.py
./python/jdl_flash/deploy_receiver.py
./python/jdl_flash/test_config_validation.py
./python/jdl_flash/_paths.py
./python/jdl_flash/test_flash_engine.py
./python/jdl_flash/platform_detect.py
./python/jdl_flash/advanced_math.py
./python/jdl_flash/test_integrate.py
./python/jdl_flash/test_platform_detect.py
./python/jdl_flash/test_cli.py
./python/jdl_flash/flash_pro.py
./python/jdl_flash/wallet_lanes.py
./python/jdl_flash/deploy_gelato.py
./python/jdl_flash/realness_guard.py
./python/jdl_flash/env_autowire.py
./python/jdl_flash/artifacts/NexusFlashReceiver.json
./python/jdl_flash/integrate.py
./python/jdl_flash/triangular_scanner.py
./python/jdl_flash/prediction.py
./python/jdl_flash/__init__.py
./python/jdl_flash/pattern_recognition.py
./python/jdl_flash/revenue_reconciliation.py
./python/jdl_flash/loan_optimizer.py
./python/jdl_flash/swarm_runtime.py
./python/jdl_flash/test_revenue_reconciliation.py
./python/trading_core.py
./python/requirements_flash.txt
./python/.env.production.template
./python/flash_supervisor.py
./python/jdl_native/jdl_hotpath.h
./python/jdl_native/test_jdl_native.py
./python/jdl_native/_jdl.pyx
./python/jdl_native/.gitignore
./python/jdl_native/setup.py
./python/jdl_native/__init__.py
./python/jdl_native/_ctypes_backend.py
./python/jdl_native/_pyfallback.py
./.env.production.template
./setup.sh
./contracts/package-lock.json
./contracts/contracts/NexusFlashReceiver.sol
./contracts/contracts/FlashZeroGas.sol
./contracts/contracts/ProfitPaymaster.sol
./contracts/contracts/interfaces/ICurvePool.sol
./contracts/contracts/interfaces/IAaveV3Pool.sol
./contracts/contracts/interfaces/IUniswapV3Router.sol
./contracts/contracts/interfaces/IBalancerVault.sol
./contracts/contracts/ArbitrageLib.sol
./contracts/foundry.toml
./contracts/scripts/deploy-arb-lib.js
./contracts/scripts/deploy-flash-receiver.js
./contracts/package.json
./contracts/README.md
./contracts/hardhat.config.js
./contracts/test/ProfitPaymaster.t.sol
./contracts/test/NexusFlashReceiver.t.sol
./contracts/test/fork-flash.test.js
./contracts/test/NexusFlashReceiverInvariant.t.sol
./contracts/.gitignore
./scripts/run-all-tests.sh
./scripts/termux-install.sh
./scripts/termux-boot-swarm.sh
./scripts/userland-setup.sh
./scripts/push_revenue_system.sh
./scripts/setup.ps1
./scripts/deploy_termux.sh
./scripts/security-audit.sh
./scripts/termux-verify.sh
./scripts/start-swarm-daemon.sh
./docs/USERLAND.md
./docs/INTEGRATION_GUIDE.txt
./docs/TERMUX_DEEPDIVE.md
./docs/SEPOLIA_DRYRUN.md
./docs/README_REVENUE_SYSTEM.md
./docs/TERMUX_WALKTHROUGH.md
./docs/REPOSITORY_CODEBASE_ANALYSIS.md
./.env.example
./LICENSE
./node/package-lock.json
./node/test/hotpath.test.js
./node/test/scan_validation.test.js
./node/src/chain.js
./node/src/index.js
./node/src/hotpath.js
./node/package.json
./.github/dependabot.yml
./.github/workflows/ci.yml
./.github/workflows/security.yml
./.github/workflows/release.yml
./.gitignore
./database/revenue_schema.sql
./revenue_system/revenue_reconciliation.py
./revenue_system/revenue_recording.py
./revenue_system/chain_monitor_fixed.py
./revenue_system/__init__.py
./README_FLASH.md
./rust/hotpath/Cargo.toml
./rust/hotpath/src/lib.rs
./rust/hotpath/src/main.rs
./rust/hotpath/src/evm/cfg.rs
./rust/hotpath/src/evm/disasm.rs
./rust/hotpath/src/evm/opcodes.rs
./rust/hotpath/src/evm/types.rs
./rust/hotpath/src/evm/mod.rs
./rust/hotpath/src/evm/security.rs
./rust/hotpath/src/evm/signatures.rs
./rust/hotpath/src/evm/decompiler.rs
./rust/hotpath/src/evm/symbolic.rs
./rust/hotpath/Cargo.lock
