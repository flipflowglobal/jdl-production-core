# JDL Flash Loan — Termux Edition

Zero-gas flash-loan arbitrage engine, packaged to run on **Termux (Android)** with
zero ETH in the wallet and no Rust/Cargo toolchain. Pure-Python hot path.

> This is the **Termux fork** of JDL Production Core. It contains only the latest
> flash-loan engine and the pieces needed to run it on a phone — the Node.js API
> server, Rust hot-path crate, and Docker/systemd deployment have been removed.
> See `TERMUX.md` for the full quick-start.

## Quick Start (Termux / Android)

```bash
pkg install -y python git openssl libffi
git clone https://github.com/flipflowglobal/jdl-production-core.git ~/projects/jdl
cd ~/projects/jdl
bash setup.sh           # auto-detects Termux, installs web3 6.x (no Rust build)
nano ~/jdl/.env         # add PRIVATE_KEY + ALCHEMY_ARB_KEY
bash setup.sh run       # launch the engine
```

Full step-by-step (including background running and troubleshooting): **[`TERMUX.md`](TERMUX.md)**.

## What's in this fork

```
python/             Flash-loan engine, scanners, executors, and AI strategy modules
  trading_core.py     Main entrypoint (menu-driven engine)
  flash_loan_engine.py, flash_pro.py, flash_*           Flash-loan orchestration
  scanner/, triangular_scanner.py, algorithms/          Arbitrage discovery
  test_flash_engine.py                                  56-test suite
contracts/          Solidity flash-loan receivers (FlashZeroGas, NexusFlashReceiver, …)
revenue_system/     On-chain revenue recording, RPC health, reconciliation
database/           SQLite revenue schema
scripts/            Termux deploy, revenue push, security audit
setup.sh            Termux/Linux installer
```

## Zero-wallet-funding

The engine borrows from already-deployed protocols, so the wallet needs **zero ETH**
to start scanning:

- **Balancer V2** — 0% fee (best source)
- **Aave V3** — 0.09% fee
- **Radiant Capital** — 0.09% fee
- **Uniswap V3** — 0.05–0.30% fee

Gas can be sponsored via Gelato Free Relay or a Flashbots PEG builder fee. To execute
live trades, deploy `contracts/FlashZeroGas.sol` and set `FLASH_CONTRACT_ADDRESS`.

## Revenue Tracking & Monitoring

Integrates the Omaga Revenue System for on-chain revenue tracking, RPC health
monitoring, and reconciliation reporting.

- `revenue_system/revenue_recording.py` — Record flash arbitrage trades to SQLite
- `revenue_system/chain_monitor_fixed.py` — RPC health daemon
- `revenue_system/revenue_reconciliation.py` — On-chain balance verification
- `database/revenue_schema.sql` — Database schema (auto-aggregation triggers)
- `scripts/deploy_termux.sh` — Universal Termux deployment script

See `docs/README_REVENUE_SYSTEM.md` for full documentation.

## License

Proprietary — Copyright © 2026 Darcel King. All rights reserved.
