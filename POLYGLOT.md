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
| `rust/hotpath/` | Rust | `jdl-hotpath` — fast arbitrage-cycle detection (lib + CLI) |
| `node/` | Node.js | API/orchestration server; ethers.js reads; Rust bridge |
| `contracts/` | Solidity | on-chain execution (unchanged; see `contracts/README.md`) |

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
