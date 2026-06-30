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
  forge test --match-path test/NexusFlashReceiver.t.sol -vv     # 7/7 mainnet-fork
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
