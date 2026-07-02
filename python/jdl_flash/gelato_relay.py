"""
gelato_relay.py — Gasless execution via Gelato Relay (ERC-2771 callWithSyncFee).

The wallet never needs ETH: Gelato's executor pays the Arbitrum gas and is
reimbursed from the trade's profit (in the loan asset) inside the same atomic
transaction, via NexusFlashReceiver.initiateFlashLoanRelay(...).

Everything here is transcribed 1:1 from the official @gelatonetwork/relay-sdk
(v5.7.0) so the wire format matches exactly:
  • EIP-712 domain : name "GelatoRelayERC2771", version "1", chainId, verifyingContract
  • verifyingContract: 0xb539068872230f20456CF38EC52EF2f91AF4AE49  (GELATO_RELAY_ERC2771,
    the same address hard-coded in NexusFlashReceiver's relay context; V1 relay on
    Arbitrum One 42161 and Arbitrum Sepolia 421614)
  • type CallWithSyncFeeERC2771(chainId,target,data,user,userNonce,userDeadline)
  • POST {url}/relays/v2/call-with-sync-fee-erc2771
  • status GET {url}/tasks/status/{taskId}

No Gelato SDK / Node dependency — pure `requests` + `eth_account`.
"""
import time
import logging
from typing import Optional, Tuple

GELATO_URL             = "https://api.gelato.digital"
GELATO_RELAY_ERC2771   = "0xb539068872230f20456CF38EC52EF2f91AF4AE49"
DEFAULT_DEADLINE_GAP   = 86400  # 24h, matches SDK DEFAULT_DEADLINE_GAP
_USER_NONCE_SELECTOR   = "0x2e04b8e7"  # userNonce(address)

# EIP-712 typed-data types (verbatim from the SDK).
_TYPES = {
    "CallWithSyncFeeERC2771": [
        {"name": "chainId",      "type": "uint256"},
        {"name": "target",       "type": "address"},
        {"name": "data",         "type": "bytes"},
        {"name": "user",         "type": "address"},
        {"name": "userNonce",    "type": "uint256"},
        {"name": "userDeadline", "type": "uint256"},
    ],
}


def build_typed_data(chain_id: int, target: str, data_hex: str, user: str,
                     user_nonce: int, user_deadline: int) -> dict:
    """The full EIP-712 payload the user signs (structure identical to the SDK)."""
    if not data_hex.startswith("0x"):
        data_hex = "0x" + data_hex
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name",              "type": "string"},
                {"name": "version",           "type": "string"},
                {"name": "chainId",           "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            **_TYPES,
        },
        "domain": {
            "name": "GelatoRelayERC2771",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": GELATO_RELAY_ERC2771,
        },
        "primaryType": "CallWithSyncFeeERC2771",
        "message": {
            "chainId": chain_id,
            "target": target,
            "data": data_hex,          # bytes field: eth_account hashes the raw bytes
            "user": user,
            "userNonce": user_nonce,
            "userDeadline": user_deadline,
        },
    }


def sign_request(priv_key: str, typed_data: dict) -> str:
    """Sign the EIP-712 payload; returns 0x-hex signature. Uses eth_account."""
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    # The `data` (bytes) member must be raw bytes for correct EIP-712 hashing.
    msg = dict(typed_data["message"])
    d = msg["data"]
    msg["data"] = bytes.fromhex(d[2:] if d.startswith("0x") else d)
    full = {**typed_data, "message": msg}
    signable = encode_typed_data(full_message=full)
    signed = Account.sign_message(signable, private_key=priv_key)
    sig = signed.signature
    return sig.hex() if not isinstance(sig, str) else sig


def read_user_nonce(eth_call, user: str) -> int:
    """Read userNonce(user) from the Gelato relay contract via an eth_call fn.

    `eth_call(tx_dict) -> hex string` is injected so this stays web3-version-agnostic
    and unit-testable. Returns 0 if the call yields nothing (fresh user)."""
    addr = user.lower().replace("0x", "").rjust(64, "0")
    tx = {"to": GELATO_RELAY_ERC2771, "data": _USER_NONCE_SELECTOR + addr}
    raw = eth_call(tx)
    if raw is None:
        return 0
    h = raw.hex() if hasattr(raw, "hex") else str(raw)
    h = h.replace("0x", "")
    return int(h, 16) if h else 0


def submit(chain_id: int, target: str, data_hex: str, user: str, priv_key: str,
           fee_token: str, user_nonce: int, *, now_ts: int,
           deadline_gap: int = DEFAULT_DEADLINE_GAP,
           session=None) -> Optional[str]:
    """Sign + POST a callWithSyncFeeERC2771 request. Returns Gelato taskId or None.

    now_ts is passed in (not read here) so the caller controls time / testability.
    """
    import requests
    sess = session or requests
    deadline = now_ts + deadline_gap
    if not data_hex.startswith("0x"):
        data_hex = "0x" + data_hex
    typed = build_typed_data(chain_id, target, data_hex, user, user_nonce, deadline)
    signature = sign_request(priv_key, typed)
    if not signature.startswith("0x"):
        signature = "0x" + signature
    body = {
        "chainId": str(chain_id),
        "target": target,
        "data": data_hex,
        "user": user,
        "userNonce": str(user_nonce),
        "userDeadline": str(deadline),
        "feeToken": fee_token,
        "isRelayContext": True,
        "userSignature": signature,
        "isConcurrent": False,
    }
    url = f"{GELATO_URL}/relays/v2/call-with-sync-fee-erc2771"
    try:
        r = sess.post(url, json=body, timeout=30)
        r.raise_for_status()
        task_id = r.json().get("taskId")
        logging.info(f"Gelato: submitted relay task {task_id}")
        return task_id
    except Exception as e:
        logging.warning(f"Gelato submit failed: {e}")
        return None


def sponsored_deploy(chain_id: int, factory: str, data_hex: str,
                     sponsor_api_key: str, session=None) -> Optional[str]:
    """One-off gasless deployment via /relays/v2/sponsored-call (1Balance-funded).

    Targets a CREATE2 factory with `salt ++ initCode`; Gelato pays gas from your
    1Balance tank (no user signature — authenticated by the sponsor API key)."""
    import requests
    sess = session or requests
    if not data_hex.startswith("0x"):
        data_hex = "0x" + data_hex
    body = {
        "chainId": str(chain_id),
        "target": factory,
        "data": data_hex,
        "sponsorApiKey": sponsor_api_key,
    }
    url = f"{GELATO_URL}/relays/v2/sponsored-call"
    try:
        r = sess.post(url, json=body, timeout=30)
        r.raise_for_status()
        return r.json().get("taskId")
    except Exception as e:
        logging.warning(f"Gelato sponsored deploy failed: {e}")
        return None


def task_status(task_id: str, session=None) -> Tuple[str, Optional[str]]:
    """Poll a Gelato task. Returns (state, txHash|None).

    States: CheckPending, ExecPending, ExecSuccess, ExecReverted, Cancelled, ...
    """
    import requests
    sess = session or requests
    try:
        r = sess.get(f"{GELATO_URL}/tasks/status/{task_id}", timeout=20)
        r.raise_for_status()
        t = r.json().get("task", {})
        return t.get("taskState", "Unknown"), t.get("transactionHash")
    except Exception as e:
        logging.warning(f"Gelato status failed: {e}")
        return "Unknown", None


def wait_for_task(task_id: str, *, poll_fn, sleep_fn, max_polls: int = 40) -> Tuple[str, Optional[str]]:
    """Poll until terminal state. poll_fn(task_id)->(state,tx); sleep_fn(sec) injected."""
    terminal = {"ExecSuccess", "ExecReverted", "Cancelled", "Blacklisted"}
    state, tx = "CheckPending", None
    for _ in range(max_polls):
        state, tx = poll_fn(task_id)
        if state in terminal:
            return state, tx
        sleep_fn(3)
    return state, tx
