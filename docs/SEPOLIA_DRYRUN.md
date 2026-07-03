# Arbitrum Sepolia dry-run — validating the Gelato gasless path

**Goal:** prove the *new, untested* code — the Gelato Relay (ERC-2771) integration — works
end to end, **before** you ever enable `GELATO_ENABLED=1` on Arbitrum One with real funds.

## Read this first — what Sepolia can and cannot validate

The arbitrage swap logic is already proven on an Arbitrum **One** mainnet fork (7 example
tests + fuzz + stateful invariants). The only unproven part is the **Gelato relay layer**:
EIP-712 request signing, Gelato accepting and forwarding the request, the contract's
`onlyGelatoRelayERC2771` + owner gating, and the capped fee payment.

Two hard facts mean **a profitable arbitrage cannot actually execute on Sepolia**, so
"ExecSuccess on a real trade" is *not* the goal here:

1. **Router ABI mismatch.** Arbitrum Sepolia only has Uniswap **SwapRouter02**
   (`0x101F443B4d1b059569D643917553c771E1b9663E`), whose `exactInputSingle` has **no
   `deadline` field**. `NexusFlashReceiver` targets the original **SwapRouter** ABI (with
   `deadline`), which exists on Arbitrum One (`0xE592…`) but not on Sepolia. The swap leg
   will revert on testnet regardless of your code.
2. **No liquidity.** Testnet pools have little/no depth, so a round-trip can't clear a
   profit — and the contract *requires* profit (`InsufficientProfit`).

So we validate the relay layer by **reading the Gelato task's revert reason**, not by
getting a green trade. The logic:

| Gelato task ends with revert reason… | Meaning |
|---|---|
| `onlyGelatoRelayERC2771` | ❌ BUG — forwarder address wrong, or Gelato didn't call the contract |
| `relay: not owner` | ❌ BUG — EIP-712 signing / `_getMsgSender()` wrong |
| `relay: fee token != asset` | ⚠️ config — fee token ≠ loan asset (fix `GELATO_FEE_TOKEN`) |
| an **Aave / swap / `InsufficientProfit`** revert (deeper) | ✅ **relay layer fully works** — it passed every relay gate and only failed on the (expected) testnet swap/profit |
| `ExecSuccess` | ✅ everything, including a profitable route (only reachable with the optional smoke-test below) |

Reaching a **deep** revert (Aave/swap/profit) is the success signal: it proves the
signature, Gelato acceptance, forwarder address, owner gating, and fee-token check all
passed. A clean `ExecSuccess` additionally needs a profitable route, which on testnet is
only realistic via the optional smoke-test in the last section.

---

## 0. One-time setup

- **Gelato account:** create one at [app.gelato.network](https://app.gelato.network).
  - Get a **sponsor API key** (for the gasless deploy). On testnets Gelato sponsorship is
    free — you do **not** need to fund 1Balance for Sepolia.
- **A throwaway owner wallet** (can be zero-ETH — the whole point). You already have
  `0x750d4cb51aa2f0642bca974f6ac05f551b5bc618`; use its private key, or generate a fresh
  test key. Never reuse the burned key.
- **An Arbitrum Sepolia RPC.** Free from Alchemy/Infura, or public
  `https://sepolia-rollup.arbitrum.io/rpc`.

## 1. Point the engine at Arbitrum Sepolia

Make a **separate** env file so you never mix testnet and mainnet config:

```bash
cp ~/jdl-production-core/.env  ~/jdl/.env.sepolia   # start from your real one, then edit
nano ~/jdl/.env.sepolia
```

Set exactly these (leave your real secrets, change the rest):

```ini
CHAIN_ID=421614
RPC_URL1=https://sepolia-rollup.arbitrum.io/rpc      # or your Alchemy Sepolia URL
PRIVATE_KEY=<test wallet private key>
WALLET_ADDRESS=0x750d4cb51aa2f0642bca974f6ac05f551b5bc618

# Sepolia constructor addresses (env-overrides read by deploy_gelato.py)
RECEIVER_AAVE_POOL=<Arbitrum Sepolia Aave V3 Pool>   # see step 2
RECEIVER_UNI_ROUTER=0x101F443B4d1b059569D643917553c771E1b9663E   # SwapRouter02 (Sepolia)
RECEIVER_BALANCER_VAULT=0xBA12222222228d8Ba445958a75a0704d566BF2C8  # unused on this path; any non-zero addr

# Gelato
GELATO_ENABLED=1
GELATO_SPONSOR_API_KEY=<your sponsor key>
GELATO_FEE_TOKEN=<Sepolia test USDC>                 # see step 2; must equal the loan asset
```

Point the loader at this file for the session:

```bash
ln -sf ~/jdl/.env.sepolia ~/jdl/.env       # engine always loads ~/jdl/.env
```

> Remember to `ln -sf ~/jdl-production-core/.env ~/jdl/.env` to switch back to mainnet later.

## 2. Fill in the Sepolia addresses you must verify yourself

These change over time, so confirm them from the source rather than trusting any list:

- **Aave V3 Pool (Arbitrum Sepolia):** from Aave's address book —
  <https://aave.com/docs/resources/addresses> (filter chain = Arbitrum Sepolia, contract =
  Pool). Put it in `RECEIVER_AAVE_POOL`.
- **Test USDC / a flash-loanable test asset on Aave Sepolia:** use the reserve Aave lists
  on that market. Put it in `GELATO_FEE_TOKEN` and use it as the loan asset in step 4.
- Uniswap Sepolia addresses (already filled above) are from Uniswap's official deployments:
  <https://developers.uniswap.org/contracts/v3/reference/deployments/arbitrum-deployments>.

Confirm the engine picked everything up:

```bash
cd ~/jdl-production-core/python
python3 -c "from jdl_flash import flash_loan_engine as e; \
print('chain', e.CHAIN_ID, '| gasless', e.GELATO_ENABLED, '| owner', e.WALLET, '| RPCs', len(e.RPC_ENDPOINTS))"
```
Expect `chain 421614 | gasless True`.

## 3. Gasless deploy to Sepolia (validates deploy-side Gelato + CREATE2)

```bash
cd ~/jdl-production-core/python
python3 -m jdl_flash.deploy_gelato
```

- It prints the **predicted CREATE2 address** and submits a Gelato **sponsored-call**.
- ✅ Success looks like: `✓ DEPLOYED (gaslessly) — tx 0x…` and `NexusFlashReceiver at: 0x…`.
- Verify on-chain that the owner is your wallet and the relay function exists:

```bash
python3 - <<'PY'
from jdl_flash import flash_loan_engine as e
addr = input("deployed address: ").strip()
code = e.get_w3().eth.get_code(e._w3_cs(addr)).hex().lower()
print("has initiateFlashLoanRelay:", "d55c394c" in code)
print("has initiateFlashLoan     :", "e95437aa" in code)
PY
```

Put the deployed address in `~/jdl/.env.sepolia` as `FLASH_CONTRACT_ADDRESS=…`.

> If the deploy task itself reverts or Gelato rejects it, that's a real signal — check the
> task at `https://api.gelato.digital/tasks/status/<taskId>` and fix before continuing.

## 4. Submit one relayed call and read the revert reason (validates the relay layer)

Run a single dry cycle with `GELATO_ENABLED=1`. The engine will build
`initiateFlashLoanRelay` calldata, sign the EIP-712 request, and submit it to Gelato:

```bash
cd ~/jdl-production-core/python
python3 -c "
import asyncio
from jdl_flash.flash_loan_engine import FlashDaemon
d = FlashDaemon()
asyncio.run(d.cycle_run())
"
```

Or just start the daemon normally and let it run one scan: `flashloan` → `[1]`, watch for
the `Gelato: submitted relay task <id>` line, then stop it.

Grab the `taskId` the engine logs (`Gelato: submitted relay task <id>`) and inspect it:

```bash
curl -s https://api.gelato.digital/tasks/status/<taskId> | python3 -m json.tool
```

Read `task.taskState` and `task.lastCheckMessage` against the table at the top:

- **`lastCheckMessage` mentions Aave / a swap error / `InsufficientProfit`** → ✅ the relay
  layer is fully validated. You're clear to use it on mainnet (where the swap + liquidity
  are real and the pre-submit simulation only lets profitable trades broadcast).
- **`onlyGelatoRelayERC2771` or `relay: not owner`** → ❌ stop; that's a real bug in the
  relay integration. Capture the message and I'll fix it.

## 5. Switch back to mainnet when done

```bash
ln -sf ~/jdl-production-core/.env ~/jdl/.env      # restore Arbitrum One config
```

On mainnet, keep `GELATO_ENABLED=1`, set `CHAIN_ID=42161`, redeploy with
`python3 -m jdl_flash.deploy_gelato` (fund Gelato 1Balance with ~$1 USDC — mainnet
sponsorship isn't free), set the new `FLASH_CONTRACT_ADDRESS`, and start `flashloan`.

---

## Optional: a clean `ExecSuccess` on Sepolia (relay smoke-test)

If you want a green `ExecSuccess` end-to-end on testnet — not just a deep revert — the swap
dependency has to be removed, because Sepolia can't run a profitable arb. That needs a tiny
diagnostic function on the contract (`relayPing`) that exercises the exact relay path
(`onlyGelatoRelayERC2771` → owner check → `_transferRelayFeeCapped`) and pays a bounded fee
from a pre-seeded test-token balance, with no Aave/Uniswap call. Say the word and I'll add
it behind a PR; then step 4 becomes a clean `ExecSuccess` instead of a revert-reason read.
