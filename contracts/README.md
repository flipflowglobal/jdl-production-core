# JDL Flash-Loan Contracts

Solidity contracts for the flash-loan arbitrage system on **Arbitrum One**.
Both **Foundry** and **Hardhat** are supported — use whichever fits your machine.

| Contract | Purpose |
|----------|---------|
| `contracts/NexusFlashReceiver.sol` | Aave V3 flash-loan executor (Uniswap V3 / Curve / Balancer routing, profit-or-revert) |
| `contracts/ArbitrageLib.sol` | SwapStep struct + encoding/quoting/EIP-712 helpers |
| `contracts/FlashZeroGas.sol` | Zero-upfront-gas (PEG) flash-loan variant |
| `contracts/ProfitPaymaster.sol` | EIP-4337 paymaster — sponsors gas only when projected profit ≥ gas cost |

Compiler: solc **0.8.20**, optimizer `runs=200`, `via_ir = true` (identical in both toolchains).

---

## Foundry (recommended on Termux / Android)

Foundry's `forge` is a single static binary — no Node required, which is why it's
the better fit for Termux. Hardhat needs a full Node toolchain that can be awkward there.

### Install (once)
```bash
curl -L https://foundry.paradigm.xyz | bash && foundryup
forge install foundry-rs/forge-std            # test framework → lib/forge-std
forge install OpenZeppelin/openzeppelin-contracts@v5.0.2   # OR reuse npm (see note)
```
> **OpenZeppelin note:** `foundry.toml` remaps `@openzeppelin/` to `node_modules/@openzeppelin/`
> so it reuses the same OZ that Hardhat uses. If you want a Node-free setup, run the
> `forge install OpenZeppelin/...` above and change that remapping line to
> `@openzeppelin/=lib/openzeppelin-contracts/`.

### Build & test
```bash
forge build
ARB_RPC_URL=https://arb1.arbitrum.io/rpc \
  forge test --match-path test/NexusFlashReceiver.t.sol -vv     # 9 example + 1 fuzz, mainnet-fork
```

### Deploy
```bash
forge create contracts/NexusFlashReceiver.sol:NexusFlashReceiver \
  --rpc-url "$ARB_RPC_URL" --private-key "$PRIVATE_KEY" \
  --constructor-args \
    0x794a61358D6845594F94dc1DB02A252b5b4814aD \
    0xE592427A0AEce92De3Edee1F18E0157C05861564 \
    0xBA12222222228d8Ba445958a75a0704d566BF2C8
```

---

## Hardhat (Node environments)

```bash
npm install
npm run compile
ARB_RPC_URL=https://arb1.arbitrum.io/rpc npm run test:fork   # 7/7 mainnet-fork
npm run deploy:arbitrum                                       # needs PRIVATE_KEY + addresses
```

Both fork tests do the same thing: deploy against live Arbitrum, prove the
crypto-moving path (Aave `flashLoanSimple` → real Uniswap V3 swaps → reverts on an
unprofitable round-trip so funds are never lost), and check owner/pause gating.

---

## Constructor addresses (Arbitrum One)

| Arg | Address |
|-----|---------|
| Aave V3 Pool | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` |
| Uniswap V3 SwapRouter | `0xE592427A0AEce92De3Edee1F18E0157C05861564` |
| Balancer V2 Vault | `0xBA12222222228d8Ba445958a75a0704d566BF2C8` |

---

## Security hardening notes

The on-chain profit invariant lives in `executeOperation`: after the swap sequence
runs, the contract compares its `balanceOf(asset)` against the loan + Aave premium and
reverts `InsufficientProfit` if the round-trip did not clear a profit, so borrowed funds
can never leave at a loss. `executeOperation` is `nonReentrant onlyAavePool whenNotPaused`;
`initiateFlashLoan` is owner-only.

That invariant is proven three ways beyond the nine example fork tests:

```bash
# Property fuzz: no fuzzed round-trip completes while leaving the receiver poorer.
ARB_RPC_URL=https://arb1.arbitrum.io/rpc \
  forge test --match-test testFuzz_RoundTripNeverLeavesLoss --fuzz-runs 10000 -vv

# Stateful invariants: receiver never retains a token balance, owner never changes.
ARB_RPC_URL=https://arb1.arbitrum.io/rpc \
  forge test --match-path test/NexusFlashReceiverInvariant.t.sol -vv
```

Fuzz/invariant depth is configured in `foundry.toml` (`runs = 10000`; invariant
`runs = 256`, `depth = 15`, `fail_on_revert = false` — the handler deliberately swallows
the expected reverts of unprofitable routes so the invariant is still asserted after each
call).

Static analysis (one-time, manual — no CI wired):

```bash
pip install slither-analyzer solc-select && solc-select install 0.8.20 && solc-select use 0.8.20
slither . --filter-paths "FlashZeroGas.sol|ProfitPaymaster.sol|lib/|node_modules/" --exclude-informational
```

`ArbitrageLib`'s inline assembly (Uniswap V3 path encoding) is intentional; if Slither
flags it, document rather than remove. Mythril is optional/best-effort only.

**Slither triage (0.8.20, run 2026-07-02): 0 HIGH.** One MEDIUM was real and is fixed —
`locked-ether` (`receive()` existed with no ETH withdrawal; `rescueETH` added; instances
deployed *before* that function cannot rescue ETH — never send ETH to them). The remaining
findings are documented intentional patterns, not defects:

| Detector | Why it's intentional |
|----------|---------------------|
| `divide-before-multiply` (ArbitrageLib) | Byte-offset arithmetic in Uniswap V3 path decoding (`numHops * 23`) and the standard remainder computation in `splitSwap` — reconstructing offsets, not losing precision. |
| `unused-return` (`_swapCurve`) | Curve pools don't return amounts consistently, so output is measured as a `balanceOf` delta — the return value is deliberately ignored. |
| `write-after-write` (`safeApproveMax`) | The classic `approve(0)` → `approve(amount)` two-step required by USDT-like tokens. |
| `calls-loop`, `timestamp` (LOW) | Inherent to multi-hop arbitrage execution; deadline checks use `block.timestamp` by design. |

### Gasless operation via Gelato Relay (ERC-2771)

The contract has two entry points for the same flash-loan arbitrage:

| Function | Caller | Who pays gas |
|----------|--------|--------------|
| `initiateFlashLoan(asset, amount, steps)` | owner (EOA) | the owner, in ETH |
| `initiateFlashLoanRelay(asset, amount, steps, maxFee)` | Gelato Relay (ERC-2771) | Gelato, reimbursed from profit |

The relay path lets the operator run the system with a **zero-ETH wallet**. Mechanics:

- `executeOperation` no longer sweeps profit to the owner — it leaves profit in the
  contract after Aave is repaid. The initiating function then forwards it: the direct
  path sends all profit to the owner; the relay path first pays Gelato, then sweeps the
  rest.
- `initiateFlashLoanRelay` is gated by `onlyGelatoRelayERC2771` (only Gelato's forwarder,
  `0xb539068872230f20456CF38EC52EF2f91AF4AE49` on Arbitrum, can call it) **and**
  `require(_getMsgSender() == owner())` — the signer Gelato forwards must be the owner.
  So only owner-signed requests execute.
- The relayer fee is charged in the loan `asset` and bounded by an **owner-signed
  `maxFee`** (`_transferRelayFeeCapped`). If profit can't cover the fee, the whole trade
  reverts atomically — no funds move, no loss.
- The Gelato relay context is **vendored** (`GelatoRelayERC2771Context` in the source),
  not imported, because `@gelatonetwork/relay-context@4.1.1` uses `SafeERC20.safePermit`
  which OpenZeppelin v5 removed. The forwarder address and calldata offsets are copied
  verbatim from that package and cross-checked against `@gelatonetwork/relay-sdk@5.7.0`.

The constructor takes an explicit `_owner` (not `msg.sender`) so the contract can be
deployed **gaslessly** via a relayer/CREATE2 factory while ownership still lands on the
operator. The Python side (`jdl_flash/gelato_relay.py`, `deploy_gelato.py`) builds and
signs the EIP-712 relay requests; the wire format is transcribed 1:1 from the Gelato SDK
and the signature is verified to recover to the owner offline.

> ⚠️ The relay path must be dry-run on **Arbitrum Sepolia** (Gelato sponsors testnet gas
> for free) before mainnet use — the off-chain Gelato submission cannot be exercised by
> the fork tests.

### Why there is no private-relay / Flashbots integration

On Ethereum L1, arbitrage bots submit through a private relay (Flashbots) to avoid being
front-run in the public mempool. **That threat model does not apply on Arbitrum One.**
Arbitrum has a single sequencer with FIFO ordering and no public gossip mempool, so there
is no pending-tx stream for a searcher to observe and front-run. The `FlashbotsPEG` helper
in the Python engine is real but gated to `CHAIN_ID == 1` and is dead code on Arbitrum by
design. The practical pre-broadcast protection on Arbitrum is a pre-submit `eth_call`
simulation (implemented in `NexusExecutor.simulate()`), which skips broadcasting — and
spending gas on — any transaction that would revert. Arbitrum Timeboost (timed express-lane
auctions) is a distinct, un-built future item, not a substitute for the simulation gate.
