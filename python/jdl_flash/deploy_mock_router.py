"""
deploy_mock_router.py — Deploy + seed MockV3Router on Arbitrum Sepolia, from Termux.

Why this exists: NexusFlashReceiver's Uniswap leg calls the exact
IUniswapV3Router.exactInputSingle interface (with a `deadline` field — the
original SwapRouter, not SwapRouter02). Verified against Uniswap's own current
deployment docs: Arbitrum Sepolia has no live deployment of either — only
Factory and QuoterV2 exist there. Balancer's V2 Vault (the interface the
receiver's Balancer leg expects) isn't deployed on Arbitrum Sepolia at all
either — only unrelated Balancer V3 infrastructure is. So there is no real
contract on this testnet for the receiver's swap legs to call.

contracts/contracts/testnet/MockV3Router.sol fills that gap for testing: same
exactInputSingle signature, backed by a simple owner-seeded constant-product
pool per (tokenIn, tokenOut, fee). This script deploys it, then seeds two fee
tiers (500 and 3000) using Aave's real Arbitrum Sepolia test USDC/WETH — get
those first from https://bridge-testnet.aave.com/faucet/?marketName=proto_arbitrum_sepolia_v3
(connect the wallet at PRIVATE_KEY's address, claim both USDC and WETH).

Seeds the two pools with a deliberate price gap (buy cheap on the 0.05% pool,
sell rich on the 0.30% pool) so a subsequent real flash loan
(test_testnet_flashloan.py) has something genuine to profit from — this is a
test fixture you control, not a real market, so real-world price discovery
doesn't apply here the way it does on mainnet.

Run:  python3 -m jdl_flash.deploy_mock_router
"""
import json
import sys
from pathlib import Path

from jdl_flash import flash_loan_engine as e

ARTIFACT = Path(__file__).parent / "artifacts" / "MockV3Router.json"

# Aave's real Arbitrum Sepolia test tokens (bgd-labs/aave-address-book,
# AaveV3ArbitrumSepolia.sol — verified live on-chain: real bytecode, correct
# name()/symbol()/decimals() for both).
USDC_UNDERLYING = "0x75faf114eafb1BDbe2F0316DF893fd58CE46AA4d"  # 6dp
WETH_UNDERLYING = "0x1dF462e2712496373A347f8ad10802a5E95f053D"  # 18dp

_ERC20_ABI = json.loads(
    '[{"inputs":[{"type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],'
    '"stateMutability":"view","type":"function"},'
    '{"inputs":[{"type":"address"},{"type":"uint256"}],"name":"approve",'
    '"outputs":[{"type":"bool"}],"stateMutability":"nonpayable","type":"function"},'
    '{"inputs":[{"type":"address"},{"type":"address"},{"type":"uint24"}],"name":"reserveIn",'
    '"outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}]'
)

# Seed amounts (raw base units): 3,000,000 USDC / 1,000 WETH at fee=500 (~3,000
# USDC/WETH), 1,000 WETH / 3,300,000 USDC at fee=3000 (~3,300 USDC/WETH). Scale
# these down via env vars below if the faucet gives you less than this.
SEED_USDC_500  = int(e._env('MOCK_SEED_USDC_500', default=str(3_000_000 * 10**6)))
SEED_WETH_500  = int(e._env('MOCK_SEED_WETH_500', default=str(1_000 * 10**18)))
SEED_WETH_3000 = int(e._env('MOCK_SEED_WETH_3000', default=str(1_000 * 10**18)))
SEED_USDC_3000 = int(e._env('MOCK_SEED_USDC_3000', default=str(3_300_000 * 10**6)))


def _raw(signed):
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


def _wait(w3, txh):
    fn = getattr(w3.eth, "wait_for_transaction_receipt", None) or w3.eth.waitForTransactionReceipt
    return fn(txh, timeout=240)


def _encode(w3, acct, chain, fn):
    """ABI-encode a contract function call with every field pre-filled, so
    build_transaction() never tries to auto-discover fee/nonce data over RPC."""
    return fn.build_transaction(
        {"from": acct.address, "chainId": chain, "gas": 1, "gasPrice": 1, "nonce": 0}
    )["data"]


def _send(w3, acct, chain, to, data, value=0):
    tx = {
        "from": acct.address, "to": to, "value": value, "data": data,
        "nonce": e._nonce(w3, acct.address), "gasPrice": int(e._gas_p(w3)), "chainId": chain,
    }
    tx["gas"] = int(e._est_gas(w3, tx) * 1.3)
    signed = acct.sign_transaction(tx)
    txh = e._send_raw(w3, _raw(signed))
    rcpt = _wait(w3, txh)
    status = rcpt["status"] if isinstance(rcpt, dict) else rcpt.status
    txh_hex = txh.hex() if hasattr(txh, "hex") else str(txh)
    return status, txh_hex


def main():
    print("─── Deploy + seed MockV3Router (Arbitrum Sepolia) ───\n")
    if not e.WEB3_OK:
        print("✗ web3 not importable."); sys.exit(1)
    if not e.PRIV_KEY:
        print("✗ PRIVATE_KEY not set in ~/jdl/.env."); sys.exit(1)

    w3 = e.get_w3()
    if not w3:
        print("✗ No live RPC connection."); sys.exit(1)

    from eth_account import Account
    acct = Account.from_key(e.PRIV_KEY)
    chain = e._chain_id(w3)
    if chain != e.SEPOLIA_CHAIN_ID:
        print(f"✗ Connected to chain {chain}, not Arbitrum Sepolia ({e.SEPOLIA_CHAIN_ID}).\n"
              "  Set CHAIN_ID=421614 and an Arbitrum Sepolia RPC_URL in ~/jdl/.env first."); sys.exit(1)

    bal = e._balance(w3, acct.address)
    print(f"Deployer : {acct.address}")
    print(f"Balance  : {bal/1e18:.6f} ETH (Sepolia)")
    if bal == 0:
        print("\n✗ Wallet has 0 Sepolia ETH. Get some from a faucet, then re-run."); sys.exit(1)

    usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_UNDERLYING), abi=_ERC20_ABI)
    weth = w3.eth.contract(address=w3.to_checksum_address(WETH_UNDERLYING), abi=_ERC20_ABI)
    usdc_bal = usdc.functions.balanceOf(acct.address).call()
    weth_bal = weth.functions.balanceOf(acct.address).call()
    need_usdc = SEED_USDC_500 + SEED_USDC_3000
    need_weth = SEED_WETH_500 + SEED_WETH_3000
    print(f"Test USDC: {usdc_bal/1e6:,.2f}  (need {need_usdc/1e6:,.2f})")
    print(f"Test WETH: {weth_bal/1e18:,.4f}  (need {need_weth/1e18:,.4f})")
    if usdc_bal < need_usdc or weth_bal < need_weth:
        print("\n✗ Not enough test tokens. Claim from the Aave testnet faucet first:\n"
              "    https://bridge-testnet.aave.com/faucet/?marketName=proto_arbitrum_sepolia_v3\n"
              "  (connect this wallet, claim both USDC and WETH)\n"
              "  Or lower MOCK_SEED_USDC_500 / MOCK_SEED_WETH_500 / MOCK_SEED_WETH_3000 /\n"
              "  MOCK_SEED_USDC_3000 in ~/jdl/.env to match what you actually have."); sys.exit(1)

    art = json.load(open(ARTIFACT))
    bytecode = art["bytecode"]
    if bytecode.startswith("0x"):
        bytecode = bytecode[2:]
    tx = {
        "from": acct.address, "nonce": e._nonce(w3, acct.address),
        "gasPrice": int(e._gas_p(w3)), "chainId": chain, "value": 0, "data": "0x" + bytecode,
    }
    tx["gas"] = int(e._est_gas(w3, tx) * 1.25)
    cost_eth = tx["gas"] * int(e._gas_p(w3)) / 1e18
    print(f"\nDeploying MockV3Router — gas est ~{cost_eth:.6f} ETH")
    ans = input("Broadcast? [y/N] ").strip().lower()
    if ans != "y":
        print("Aborted — nothing sent."); return

    signed = acct.sign_transaction(tx)
    txh = e._send_raw(w3, _raw(signed))
    print(f"tx hash  : {txh.hex() if hasattr(txh, 'hex') else txh}")
    print("Waiting for confirmation…")
    rcpt = _wait(w3, txh)
    router_addr = rcpt["contractAddress"] if isinstance(rcpt, dict) else rcpt.contractAddress
    status = rcpt["status"] if isinstance(rcpt, dict) else rcpt.status
    if not status:
        print("✗ Deploy tx reverted."); sys.exit(1)
    print(f"✓ MockV3Router deployed at {router_addr}\n")

    router = w3.eth.contract(address=router_addr, abi=art["abi"])

    print("Approving + seeding fee=500 pool (buy side, ~3,000 USDC/WETH)…")
    status, txh_hex = _send(w3, acct, chain, usdc.address,
                             _encode(w3, acct, chain, usdc.functions.approve(router_addr, need_usdc)))
    if not status:
        print(f"✗ USDC approve failed ({txh_hex})."); sys.exit(1)
    status, txh_hex = _send(w3, acct, chain, weth.address,
                             _encode(w3, acct, chain, weth.functions.approve(router_addr, need_weth)))
    if not status:
        print(f"✗ WETH approve failed ({txh_hex})."); sys.exit(1)

    status, txh_hex = _send(
        w3, acct, chain, router_addr,
        _encode(w3, acct, chain,
                router.functions.seedLiquidity(usdc.address, SEED_USDC_500, weth.address, SEED_WETH_500, 500)),
    )
    if not status:
        print(f"✗ Seeding fee=500 pool failed ({txh_hex})."); sys.exit(1)
    print(f"✓ fee=500 pool seeded ({txh_hex})")

    print("Seeding fee=3000 pool (sell side, ~3,300 USDC/WETH)…")
    status, txh_hex = _send(
        w3, acct, chain, router_addr,
        _encode(w3, acct, chain,
                router.functions.seedLiquidity(weth.address, SEED_WETH_3000, usdc.address, SEED_USDC_3000, 3000)),
    )
    if not status:
        print(f"✗ Seeding fee=3000 pool failed ({txh_hex})."); sys.exit(1)
    print(f"✓ fee=3000 pool seeded ({txh_hex})\n")

    print("Next steps:")
    print(f"  1. Add to ~/jdl/.env:   TESTNET_MOCK_ROUTER={router_addr}")
    print("  2. python3 -m jdl_flash.deploy_receiver   (deploys against this router on Sepolia)")
    print("  3. python3 -m jdl_flash.test_testnet_flashloan   (broadcasts a real test flash loan)")


if __name__ == "__main__":
    main()
