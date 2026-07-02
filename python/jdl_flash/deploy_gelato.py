"""
deploy_gelato.py — Gasless deployment of NexusFlashReceiver via Gelato Relay.

The deploying wallet needs ZERO ETH. Gelato's executor pays the Arbitrum gas from
your 1Balance tank (funded once with ~$1 of USDC) and relays a CREATE2 deployment
through the canonical deterministic factory. The contract's explicit `_owner`
constructor arg means ownership lands on YOUR wallet even though a relayer/factory
sends the transaction.

Requires in ~/jdl/.env:
  WALLET_ADDRESS           the owner the contract will be deployed with
  GELATO_SPONSOR_API_KEY   sponsor key from app.gelato.network (1Balance funded)

Run:  python3 -m jdl_flash.deploy_gelato
"""
import json
import sys
import time
from pathlib import Path

from jdl_flash import flash_loan_engine as e
from jdl_flash import gelato_relay as gr

# Canonical Arbitrum One constructor args
AAVE_V3_POOL   = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
UNI_V3_ROUTER  = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"

# Arachnid's deterministic CREATE2 deployer — same address on every EVM chain,
# including Arbitrum One. Calldata = salt(32 bytes) ++ initCode; it CREATE2-deploys
# and the constructor's msg.sender is this factory (hence the explicit _owner arg).
CREATE2_FACTORY = "0x4e59b44847b379578588920cA78FbF26c0B4956C"
SALT = bytes(32)  # zero salt — deterministic address for these exact args + owner

ARTIFACT = Path(__file__).parent / "artifacts" / "NexusFlashReceiver.json"


def _compute_create2_address(factory: str, salt: bytes, init_code: bytes) -> str:
    from eth_hash.auto import keccak
    pre = b"\xff" + bytes.fromhex(factory[2:]) + salt + keccak(init_code)
    return "0x" + keccak(pre)[12:].hex()


def main():
    print("─── Gasless Deploy NexusFlashReceiver (Gelato Relay) ───\n")
    owner = e.WALLET or e._env('WALLET_ADDRESS')
    sponsor_key = e._env('GELATO_SPONSOR_API_KEY', 'GELATO_API_KEY')
    if not owner:
        print("✗ WALLET_ADDRESS not set in ~/jdl/.env — needed as the contract owner."); sys.exit(1)
    if not sponsor_key:
        print("✗ GELATO_SPONSOR_API_KEY not set. Create one at app.gelato.network and\n"
              "  fund 1Balance with ~$1 USDC, then add it to ~/jdl/.env."); sys.exit(1)
    if not e.WEB3_OK:
        print("✗ web3 not importable — fix the install first."); sys.exit(1)

    art = json.load(open(ARTIFACT))
    bytecode = art["bytecode"]
    if bytecode.startswith("0x"):
        bytecode = bytecode[2:]
    # initCode = creation bytecode ++ abi.encode(owner, aave, router, vault)
    ctor = (e._abi_w_addr(owner) + e._abi_w_addr(AAVE_V3_POOL)
            + e._abi_w_addr(UNI_V3_ROUTER) + e._abi_w_addr(BALANCER_VAULT))
    init_code = bytes.fromhex(bytecode) + ctor
    predicted = _compute_create2_address(CREATE2_FACTORY, SALT, init_code)

    # Factory calldata: salt ++ initCode
    factory_data = "0x" + (SALT + init_code).hex()

    print(f"Owner       : {owner}")
    print(f"Chain       : {e.CHAIN_ID}  {'(Arbitrum One ✓)' if e.CHAIN_ID == 42161 else '(!! not Arbitrum)'}")
    print(f"Factory     : {CREATE2_FACTORY}")
    print(f"Predicted   : {predicted}   ← the deployed address (deterministic)")
    if e.CHAIN_ID != 42161:
        print("✗ CHAIN_ID must be 42161 (Arbitrum One). Fix ~/jdl/.env."); sys.exit(1)

    ans = input("\nSubmit gasless deployment via Gelato? [y/N] ").strip().lower()
    if ans != "y":
        print("Aborted — nothing sent."); return

    task_id = gr.sponsored_deploy(e.CHAIN_ID, CREATE2_FACTORY, factory_data, sponsor_key)
    if not task_id:
        print("✗ Gelato did not accept the request. Check the sponsor key / 1Balance funding."); sys.exit(1)
    print(f"\nGelato task : {task_id}")
    print("Waiting for execution…")
    state, tx = gr.wait_for_task(task_id, poll_fn=gr.task_status, sleep_fn=time.sleep)
    if state != "ExecSuccess":
        print(f"✗ Deployment task ended {state}. Check status: "
              f"https://api.gelato.digital/tasks/status/{task_id}"); sys.exit(1)

    print(f"\n✓ DEPLOYED (gaslessly) — tx {tx}")
    print(f"    NexusFlashReceiver at: {predicted}\n")
    print("Next steps:")
    print(f"  1. Add to ~/jdl/.env:   FLASH_CONTRACT_ADDRESS={predicted}")
    print("  2. Set:                 GELATO_ENABLED=1  and  LIVE_MODE=1")
    print("  3. Run `flashloan` → [1]. Trades are gasless too (Gelato paid from profit).")


if __name__ == "__main__":
    main()
