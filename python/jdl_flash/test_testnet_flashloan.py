"""
test_testnet_flashloan.py — Broadcast a real flash loan on Arbitrum Sepolia.

Exercises the exact live path this engine uses on mainnet — Aave V3
flashLoanSimple -> two swap legs -> profit check -> repay -> profit to owner —
as a real, confirmed transaction against Aave's real Arbitrum Sepolia Pool and
your deployed MockV3Router (see deploy_mock_router.py for why a mock router is
needed on this testnet, and how the two swap legs are seeded with a
deliberate price gap so the round trip is genuinely profitable).

Prerequisites (in order):
  1. python3 -m jdl_flash.deploy_mock_router     (needs Aave Sepolia test USDC/WETH)
  2. Add TESTNET_MOCK_ROUTER=<addr> to ~/jdl/.env
  3. python3 -m jdl_flash.deploy_receiver         (chain-aware: uses your mock router on Sepolia)
  4. Add FLASH_CONTRACT_ADDRESS=<addr> to ~/jdl/.env
  5. python3 -m jdl_flash.test_testnet_flashloan  (this script)

Run:  python3 -m jdl_flash.test_testnet_flashloan
"""
import json
import sys

from jdl_flash import flash_loan_engine as e

USDC_UNDERLYING = "0x75faf114eafb1BDbe2F0316DF893fd58CE46AA4d"  # 6dp
WETH_UNDERLYING = "0x1dF462e2712496373A347f8ad10802a5E95f053D"  # 18dp

# Matches deploy_mock_router.py's default seeding exactly: buy WETH on the
# fee=500 pool (~3,000 USDC/WETH), sell it back on the fee=3000 pool
# (~3,300 USDC/WETH). Loan size kept well under the seeded pool depth so the
# constant-product price impact stays predictable.
LOAN_USDC = int(e._env('TESTNET_LOAN_USDC', default=str(10_000 * 10**6)))  # 10,000 USDC default

_RECEIVER_ABI = json.loads('['
    '{"inputs":[{"type":"address"},{"type":"uint256"},{"type":"bytes"}],'
    '"name":"initiateFlashLoan","outputs":[],"stateMutability":"nonpayable","type":"function"},'
    '{"anonymous":false,"inputs":['
    '{"indexed":true,"type":"address","name":"token"},'
    '{"indexed":false,"type":"uint256","name":"loanAmount"},'
    '{"indexed":false,"type":"uint256","name":"premium"},'
    '{"indexed":false,"type":"uint256","name":"profit"},'
    '{"indexed":false,"type":"uint256","name":"gasUsed"},'
    '{"indexed":false,"type":"uint256","name":"stepCount"}],'
    '"name":"ArbitrageExecuted","type":"event"}'
    ']')


def _raw(signed):
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


def _wait(w3, txh):
    fn = getattr(w3.eth, "wait_for_transaction_receipt", None) or w3.eth.waitForTransactionReceipt
    return fn(txh, timeout=240)


def _encode_steps(w3, usdc, weth):
    """ArbitrageLib.SwapStep[] tuple: (protocol, poolOrRouter, tokenIn, tokenOut,
    fee, minOut, ?, ?, salt). protocol=0 is Uniswap-style exactInputSingle,
    routed through the receiver's immutable UNISWAP_V3_ROUTER (your deployed
    MockV3Router on this chain) — poolOrRouter is unused for protocol=0, same
    shape the mainnet fork test (contracts/test/fork-flash.test.js) uses."""
    from eth_abi import encode as abi_encode
    step_type = "(uint8,address,address,address,uint24,uint256,uint8,uint8,bytes32)[]"
    steps = [
        (0, "0x0000000000000000000000000000000000000000", usdc, weth, 500, 0, 0, 0, b"\x00" * 32),
        (0, "0x0000000000000000000000000000000000000000", weth, usdc, 3000, 0, 0, 0, b"\x00" * 32),
    ]
    return "0x" + abi_encode([step_type], [steps]).hex()


def main():
    print("─── Test flash loan (Arbitrum Sepolia) ───\n")
    if not e.WEB3_OK:
        print("✗ web3 not importable."); sys.exit(1)
    if not e.PRIV_KEY:
        print("✗ PRIVATE_KEY not set in ~/jdl/.env."); sys.exit(1)
    if not e.CONTRACT or int(e.CONTRACT, 16) == 0:
        print("✗ FLASH_CONTRACT_ADDRESS not set in ~/jdl/.env — run "
              "`python3 -m jdl_flash.deploy_receiver` first."); sys.exit(1)

    w3 = e.get_w3()
    if not w3:
        print("✗ No live RPC connection."); sys.exit(1)

    from eth_account import Account
    acct = Account.from_key(e.PRIV_KEY)
    chain = e._chain_id(w3)
    if chain != e.SEPOLIA_CHAIN_ID:
        print(f"✗ Connected to chain {chain}, not Arbitrum Sepolia ({e.SEPOLIA_CHAIN_ID})."); sys.exit(1)

    receiver_addr = w3.to_checksum_address(e.CONTRACT)
    usdc_addr = w3.to_checksum_address(USDC_UNDERLYING)
    weth_addr = w3.to_checksum_address(WETH_UNDERLYING)
    receiver = w3.eth.contract(address=receiver_addr, abi=_RECEIVER_ABI)

    print(f"Caller   : {acct.address}")
    print(f"Receiver : {receiver_addr}")
    print(f"Loan     : {LOAN_USDC/1e6:,.2f} USDC")

    steps = _encode_steps(w3, usdc_addr, weth_addr)

    tx = {
        "from": acct.address, "to": receiver_addr,
        "data": receiver.functions.initiateFlashLoan(usdc_addr, LOAN_USDC, steps).build_transaction(
            {"from": acct.address, "chainId": chain, "gas": 1, "gasPrice": 1, "nonce": 0}
        )["data"],
        "nonce": e._nonce(w3, acct.address), "gasPrice": int(e._gas_p(w3)), "chainId": chain, "value": 0,
    }
    try:
        tx["gas"] = int(e._est_gas(w3, tx) * 1.3)
    except Exception as ex:
        print(f"\n✗ Gas estimation reverted — the trade would fail on-chain: {ex}\n"
              "  Check: is TESTNET_MOCK_ROUTER seeded (deploy_mock_router.py)? Does the "
              "receiver's UNISWAP_V3_ROUTER match it? Is the Aave Sepolia pool holding "
              f"at least {LOAN_USDC/1e6:,.0f} USDC in reserves right now?")
        sys.exit(1)

    print(f"Gas est  : {tx['gas']:,}")
    ans = input("\nBroadcast this REAL testnet flash loan tx? [y/N] ").strip().lower()
    if ans != "y":
        print("Aborted — nothing sent."); return

    signed = acct.sign_transaction(tx)
    txh = e._send_raw(w3, _raw(signed))
    txh_hex = txh.hex() if hasattr(txh, "hex") else str(txh)
    print(f"\ntx hash  : {txh_hex}")
    print(f"explorer : https://sepolia.arbiscan.io/tx/{txh_hex}")
    print("Waiting for confirmation…")
    rcpt = _wait(w3, txh)
    status = rcpt["status"] if isinstance(rcpt, dict) else rcpt.status
    if not status:
        print("✗ Transaction reverted on-chain (funds safe — flash loans are atomic)."); sys.exit(1)

    logs = rcpt["logs"] if isinstance(rcpt, dict) else rcpt.logs
    profit = None
    for log in logs:
        try:
            ev = receiver.events.ArbitrageExecuted().process_log(log)
            profit = ev["args"]["profit"]
        except Exception:
            continue

    print("\n✓ REAL testnet flash loan confirmed.")
    if profit is not None:
        print(f"  Profit: {profit/1e6:,.4f} USDC (sent to owner)")
    print(f"  See it on-chain: https://sepolia.arbiscan.io/tx/{txh_hex}")


if __name__ == "__main__":
    main()
