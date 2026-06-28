# Termux Quick-Start Guide

Zero errors, zero ETH in wallet, works on Android mobile data.

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

### 3. Run setup (auto-detects Termux)
```bash
bash setup.sh
```
This installs `web3==5.31.4` — pure Python, **no Rust/Cargo**, safe on mobile data.

### 4. Edit config
```bash
mkdir -p ~/jdl
nano ~/jdl/.env
```
Minimum required fields:
```
PRIVATE_KEY=0x...          # your wallet private key
ALCHEMY_ARB_KEY=...        # free key from alchemy.com → Arbitrum One
```
`FLASH_CONTRACT_ADDRESS` is **optional** — engine runs in scan mode without it.

### 5. Launch
```bash
source ~/.flash_venv/bin/activate
# Note: engine.py has been modularized to trading_core.py
python3 python/trading_core.py
```

### 6. Keep running (optional)
```bash
termux-wake-lock                          # prevent CPU sleep
nohup python3 python/trading_core.py > ~/flash.log 2>&1 &
tail -f ~/flash.log                       # watch live output
```

## Pull updates
```bash
cd ~/projects/jdl-production-core
git pull origin main
source ~/.flash_venv/bin/activate
pip install --no-cache-dir -r python/requirements.txt
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
| `[8]` | Run 56 tests |
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
| `Connection refused` (RPC) | Add `ALCHEMY_ARB_KEY` to `~/jdl/.env` |
| Screen goes to sleep | Run: `termux-wake-lock` before launching |
