"""
deploy_receiver.py — Deploy NexusFlashReceiver to Arbitrum One from Termux.

Pure web3 — no Foundry or Node needed (their binaries don't run natively on
Android). Reads PRIVATE_KEY + RPC from ~/jdl/.env via the engine config,
broadcasts a REAL deployment transaction (costs a little Arbitrum gas), and
prints the deployed address to paste into FLASH_CONTRACT_ADDRESS.

Run:  python3 -m jdl_flash.deploy_receiver
"""
import json
import sys
from pathlib import Path

from jdl_flash import flash_loan_engine as e

# Canonical Arbitrum One addresses (NexusFlashReceiver constructor args)
AAVE_V3_POOL   = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
UNI_V3_ROUTER  = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"

ARTIFACT = Path(__file__).parent / "artifacts" / "NexusFlashReceiver.json"


def _raw(signed):
    """web3 v6 exposes .raw_transaction, v5 exposes .rawTransaction."""
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


def _wait(w3, txh):
    fn = getattr(w3.eth, "wait_for_transaction_receipt", None) or w3.eth.waitForTransactionReceipt
    return fn(txh, timeout=240)


def main():
    print("─── Deploy NexusFlashReceiver (Arbitrum One) ───\n")
    if not e.WEB3_OK:
        print("✗ web3 not importable — fix the install first "
              "(pip install --no-deps --upgrade 'parsimonious>=0.10')."); sys.exit(1)
    if not e.PRIV_KEY:
        print("✗ PRIVATE_KEY not set in ~/jdl/.env — required to deploy."); sys.exit(1)

    w3 = e.get_w3()
    if not w3:
        print("✗ No live RPC connection. Check ~/jdl/.env and try [c] in flashloan."); sys.exit(1)

    from eth_account import Account
    acct = Account.from_key(e.PRIV_KEY)

    chain = e._chain_id(w3)
    bal   = e._balance(w3, acct.address)
    print(f"Deployer : {acct.address}")
    print(f"Chain    : {chain}  {'(Arbitrum One ✓)' if chain == 42161 else '(!! not Arbitrum)'}")
    print(f"Balance  : {bal/1e18:.6f} ETH")
    if chain != 42161:
        print("✗ Not connected to Arbitrum One (chainId 42161). Aborting."); sys.exit(1)
    if bal == 0:
        print("\n✗ Wallet has 0 ETH. Fund it with a little Arbitrum ETH for gas "
              "(deployment costs ~$0.20–1.00), then re-run."); sys.exit(1)

    art = json.load(open(ARTIFACT))
    # Hand-roll the constructor encoding (3 addresses, each left-padded to 32 bytes)
    # to bypass eth-abi 2.x, whose parsimonious grammar is broken on modern Python —
    # the same reason the engine hand-rolls all its calldata.
    bytecode = art["bytecode"]
    if bytecode.startswith("0x"):
        bytecode = bytecode[2:]
    # Constructor: (address _owner, address _aavePool, address _uniswapV3Router,
    # address _balancerVault). On this direct-deploy path the deploying wallet is the
    # owner. (The gasless Gelato deploy path sets _owner to the configured wallet so a
    # relayer/factory can deploy while ownership still lands on the operator.)
    ctor_args = (e._abi_w_addr(acct.address)
                 + e._abi_w_addr(AAVE_V3_POOL)
                 + e._abi_w_addr(UNI_V3_ROUTER)
                 + e._abi_w_addr(BALANCER_VAULT)).hex()
    data = "0x" + bytecode + ctor_args

    tx = {
        "from":     acct.address,
        "nonce":    e._nonce(w3, acct.address),
        "gasPrice": int(e._gas_p(w3)),
        "chainId":  chain,
        "value":    0,
        "data":     data,
    }
    tx["gas"] = int(e._est_gas(w3, tx) * 1.25)
    cost_eth = tx["gas"] * int(e._gas_p(w3)) / 1e18
    print(f"Gas est  : {tx['gas']:,}  (~{cost_eth:.6f} ETH)")

    ans = input("\nBroadcast this REAL deployment tx? [y/N] ").strip().lower()
    if ans != "y":
        print("Aborted — nothing sent."); return

    signed = acct.sign_transaction(tx)
    txh = e._send_raw(w3, _raw(signed))
    txh_hex = txh.hex() if hasattr(txh, "hex") else str(txh)
    print(f"\ntx hash  : {txh_hex}")
    print("Waiting for confirmation (up to 4 min)…")
    rcpt = _wait(w3, txh)
    addr = rcpt["contractAddress"] if isinstance(rcpt, dict) else rcpt.contractAddress
    status = rcpt["status"] if isinstance(rcpt, dict) else rcpt.status
    if not status:
        print("✗ Deployment tx reverted. Check gas/balance and retry."); sys.exit(1)

    print("\n✓ DEPLOYED NexusFlashReceiver at:")
    print(f"    {addr}\n")
    print("Next steps:")
    print(f"  1. Add to ~/jdl/.env:   FLASH_CONTRACT_ADDRESS={addr}")
    print("  2. Set:                 LIVE_EXECUTION=1")
    print("  3. Keep a little ETH in the wallet for per-trade gas.")
    print("  4. Run `flashloan` → [1] to start the live daemon.")


if __name__ == "__main__":
    main()
