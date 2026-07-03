# JDL polyglot stack — Rust · Node · Solidity

A server-side companion to the Termux Python engine. Each language does what it's
best at; nothing is duplicated across them.

```
┌──────────────────────────────────────────────────────────────┐
│  node/   Node.js API + orchestration server (ethers.js v6)     │
│          • HTTP API: /health /contract /scan                   │
│          • reads NexusFlashReceiver on-chain                   │
│          • spawns the Rust hot-path per scan                   │
│              │                         │                       │
│              ▼                         ▼                       │
│  rust/hotpath/  Rust CPU hot-path      contracts/  Solidity     │
│    • arbitrage-cycle detection           • NexusFlashReceiver   │
│      (bounded DFS over quoted rates)     • Gelato relay path    │
│    • net profit vs Aave premium+gas      (already hardened)     │
│    • stdin/stdout JSON filter            reused as-is           │
└──────────────────────────────────────────────────────────────┘
```

> **Why server-side?** Rust/cargo generally won't compile on Android and Node is
> awkward on Termux — the reason the core engine is stdlib-only Python. This stack
> targets a normal Linux box/VPS. The Python engine remains the Termux path; both
> talk to the **same** Solidity contracts, so they never diverge on-chain.

## Layout

| Path | Language | Role |
|------|----------|------|
| `rust/hotpath/` | Rust | `jdl-hotpath` — arbitrage-cycle detection **+ EVM bytecode analysis** (lib + CLI + cdylib) |
| `rust/hotpath/src/evm/` | Rust | recovered EVM engine: disassembler, CFG, signature recovery, security scan, decompiler, symbolic exec |
| `python/jdl_native/` | Python/Cython | Python access to the Rust engine with layered userland fallback |
| `node/` | Node.js | API/orchestration server; ethers.js reads; Rust bridge |
| `contracts/` | Solidity | on-chain execution (unchanged; see `contracts/README.md`) |

## EVM bytecode analysis (recovered engine)

`rust/hotpath/src/evm/` is the `rust-core` EVM analysis engine that was removed from
this repo, recovered from git history and refined into the crate. It lets a bot **vet a
pool/token contract before interacting with it** — disassemble, recover the dispatcher's
4-byte selectors, and scan for dangerous patterns (SELFDESTRUCT, DELEGATECALL, CREATE2).

`analyze_bytecode(hex) → AnalysisReport` returns a coarse `verdict` (`safe` / `caution` /
`danger`), a 0–100 `risk_score`, recovered `selectors`, and security `findings`:

```bash
echo '{"bytecode":"0x6000ff"}' | jdl-hotpath analyze
# {"verdict":"danger","has_selfdestruct":true,...}   ← PUSH1 0; SELFDESTRUCT
```

## Python access — `jdl_native` (Cython + userland fallback)

`python/jdl_native` exposes the Rust engine to Python with a **layered backend** so the
*same* API works on a Linux server and on Termux/Android. Best available wins (override
with `JDL_NATIVE_BACKEND`):

| Backend | How | Needs | `analyze()` |
|---------|-----|-------|-------------|
| `cython` | compiled extension → cdylib | Cython + C compiler + Rust build | ✅ |
| `ctypes` | loads the cdylib at runtime | just the `.so` (no compile) | ✅ |
| `subprocess` | spawns the `jdl-hotpath` CLI | just the binary | ✅ |
| `python` | pure-Python port of the arb scan | nothing (stdlib) | ✗ (raises `AnalysisUnavailable`) |

```python
import jdl_native
jdl_native.scan({"edges":[...], "base":"USDC", "loan_usd":100000, "gas_usd":1})
jdl_native.analyze("0x6000ff")          # needs a native backend or the CLI binary
jdl_native.active_backend()             # 'cython' | 'ctypes' | 'subprocess' | 'python'
```

Build the fast Cython path (optional — the fallbacks work without it):

```bash
cd rust/hotpath && cargo build --release          # produces libjdl_hotpath.so + jdl-hotpath
cd ../../python/jdl_native && python3 setup.py build_ext --inplace
python3 test_jdl_native.py                          # 11 checks across all available backends
```

> **Userland compatibility:** on Termux/Android — where the Rust cdylib and CLI binary
> generally won't build — `jdl_native` transparently uses the **pure-Python** backend for
> the arb scan (identical results, verified), and `analyze()` raises a clear
> `AnalysisUnavailable` instead of crashing. Nothing to compile, no root needed.

## Build & run

```bash
# 1) Rust hot-path (produces the binary the Node server spawns)
cd rust/hotpath
cargo build --release
cargo test                     # 6 unit tests

# 2) Node server
cd ../../node
npm install
npm test                       # bridges to the Rust binary
RPC_URL=https://arb1.arbitrum.io/rpc \
FLASH_CONTRACT_ADDRESS=0xYourContract \
npm start                      # listens on :8787
```

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | server + chain + hot-path status |
| GET | `/contract` | owner/pause/immutables of NexusFlashReceiver, relay-support flags |
| POST | `/scan` | run the Rust hot-path over a `ScanRequest` of quoted edges |

`/scan` body (a `ScanRequest`):

```json
{
  "edges": [
    { "from": "USDC", "to": "WETH", "rate": 0.0005, "fee_bps": 5 },
    { "from": "WETH", "to": "USDC", "rate": 2020,   "fee_bps": 5 }
  ],
  "base": "USDC",
  "loan_usd": 100000,
  "gas_usd": 1,
  "min_profit_usd": 0
}
```

Response (`ScanResult`): the best profitable loop, or `opportunity: null`.

```json
{ "opportunity": { "path": ["USDC","WETH","USDC"],
                   "gross_multiplier": 1.00899, "net_profit_usd": 848.03 },
  "tokens": 2, "edges": 2 }
```

The hot-path evaluates **real** quotes supplied by the caller (the Python engine or
your own feeder) — it computes profitability, it does not invent prices. Rates are the
quote-derived output-per-input for the trade size; `fee_bps` is the pool fee.

## Execution model

The Node server is **read + compute only** by default — it holds no private key and
never broadcasts. Execution stays with the Python engine or the Gelato gasless relay
path (`initiateFlashLoanRelay`), both of which drive the same contract. To let the
server execute directly, add an ethers signer and call `initiateFlashLoan` /
`initiateFlashLoanRelay` (ABI in `node/src/chain.js`) — opt-in, on purpose.

## What runs where

- **Termux/Android:** Python engine (`flashloan`) — unchanged.
- **Linux server/VPS:** this stack (`node/` + `rust/hotpath/`).
- **On-chain (any):** the Solidity contracts — one source of truth for both.
