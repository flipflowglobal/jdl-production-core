# Termux Quick-Start Guide

Zero errors, zero ETH in wallet, works on Android mobile data.

> Want the long-form, step-by-step version with explanations, verification, and
> per-step troubleshooting? See [`docs/TERMUX_WALKTHROUGH.md`](docs/TERMUX_WALKTHROUGH.md).

## Install in order — copy-paste each block

### 1. Install Termux packages
```bash
pkg update -y
pkg install -y python git openssl libffi
```

### 2. Clone the main branch (Hardened)
```bash
mkdir -p ~/projects
git clone https://github.com/flipflowglobal/jdl-production-core.git \
    ~/projects/jdl-production-core \
    --branch main --single-branch --depth 1
cd ~/projects/jdl-production-core
```

### 3. Run setup (auto-detects Termux — same as `jdl install`)
```bash
bash setup.sh
```
This installs `web3==5.31.4` — pure Python, **no Rust/Cargo**, safe on mobile data
(the optional `node/`/`rust/hotpath/` server-side stack is skipped entirely on
Termux — see `POLYGLOT.md`). It also **auto-wires `~/jdl/.env`**: it scans every
`.env*` file already on your device and copies in any real value it finds for a
key that's still a placeholder — no manual copy-paste, nothing to remember.

### 4. Fill in whatever's still missing
```bash
jdl integrate
```
This prints exactly which values (if any) nobody on the device has ever set —
almost always just:
```
PRIVATE_KEY=0x...          # your wallet private key
ALCHEMY_ARB_KEY=...        # free key from alchemy.com → Arbitrum One
```
Add those to `~/jdl/.env` (`nano ~/jdl/.env`). `FLASH_CONTRACT_ADDRESS` is
**optional** — the engine runs in scan mode without it.

### 5. Launch
```bash
source ~/.flash_venv/bin/activate
jdl start flashloan        # same as: jdl run
```

### 6. Keep running (optional)
```bash
termux-wake-lock                  # prevent CPU sleep
nohup jdl supervisor &            # auto-restart on crash
jdl show flashloans               # watch live activity from a 2nd shell
```

## Pull updates
```bash
cd ~/projects/jdl-production-core
jdl update                        # git pull + reinstall, one command
# Run security audit after update
bash scripts/security-audit.sh
```

## Menu options
| Key | Action |
|-----|--------|
| `[1]` | Start automation engine |
| `[2]` | Scan for opportunities |
| `[3]` | Gas strategy status |
| `[4]` | Revenue log |
| `[5]` | Algorithm dashboard |
| `[6]` | System status |
| `[7]` | Configuration |
| `[8]` | Run tests (or from a shell: `jdl test`) |
| `[9]` | Discover flash loan protocols (live on-chain) |
| `[0]` | Exit |

## Zero-wallet-funding explained

The engine borrows from already-deployed protocols:
- **Balancer V2** (`0xBA12...2C8`) — **0% fee**, best source
- **Aave V3** (`0x794a...4aD`) — 0.09% fee
- **Radiant Capital** (`0xF4B1...9E1`) — 0.09% fee

Gas is paid by:
1. **Gelato Free Relay** — Gelato sponsors gas, deducts from output token
2. **Flashbots PEG** — builder fee embedded in flash callback, `gasPrice=0`

Your wallet needs **zero ETH** to start scanning. To execute live trades, set `FLASH_CONTRACT_ADDRESS` after deploying `FlashZeroGas.sol`.

## Troubleshooting

| Error | Fix |
|-------|-----|
| `error: maturin failed` | You have old `web3>=6`. Run: `pip install web3==5.31.4` |
| `No module named 'web3'` | Run: `source ~/.flash_venv/bin/activate` |
| `venv creation failed` | Run: `pkg reinstall python` |
| `Connection refused` (RPC) | Add `ALCHEMY_ARB_KEY` to `~/jdl/.env`, then check with `jdl integrate` |
| Screen goes to sleep | Run: `termux-wake-lock` before launching |
| Not sure what's broken | Run `jdl test system` — it auto-heals `.env` and retries any failed test |

## The plain-English CLI

Every command above is a `jdl` subcommand (`jdl --help` lists them all):

| Command | What it does |
|---------|--------------|
| `jdl install` | Re-run setup: install deps, auto-wire `.env` (same as `bash setup.sh`) |
| `jdl start flashloan` | Launch the interactive engine |
| `jdl test system` | Run the full test suite; auto-heals `.env` and retries on failure |
| `jdl supervisor` | Auto-restart supervisor with live status (run from a 2nd shell) |
| `jdl show flashloans` | Stream live activity/logs |
| `jdl integrate` | Check every connection (`.env`, RPC, contract, daemon) is wired |
| `jdl update` | `git pull` + reinstall in one command |
