"""
wallet_lanes.py — multi-wallet execution lanes for swarm mode.

Why this exists: NexusFlashReceiver.initiateFlashLoan / initiateFlashLoanRelay are
`onlyOwner`-gated (the relay path additionally requires `_getMsgSender() == owner()`),
and a deployed contract has exactly ONE owner. A single wallet's transactions are
also serialized on-chain by nonce order, so one wallet can never truly execute in
parallel no matter how many scan workers find opportunities.

The only way to get genuinely parallel ON-CHAIN EXECUTION is multiple independent
wallet+contract pairs, each wallet being the sole owner of its own deployed
NexusFlashReceiver instance. This requires NO Solidity change — deploy N instances
with deploy_receiver.py / deploy_gelato.py (both already take an explicit owner
argument), one per wallet, then configure the pairs here.

Configure via env:
  SWARM_KEYS      comma-separated private keys (0x...), one per lane
  SWARM_CONTRACTS comma-separated NexusFlashReceiver addresses, index-aligned with
                  SWARM_KEYS. contract[i] MUST be owned by the wallet derived from
                  keys[i] — a mismatch means every call from that lane reverts with
                  OwnableUnauthorizedAccount (verify_lane_ownership can check this
                  on-chain before trading starts).

If SWARM_KEYS is unset, callers should fall back to the single configured wallet
(PRIV_KEY/FLASH_CONTRACT_ADDRESS) — build_lanes returns that as a 1-lane list via
the fallback_* params, or [] if neither is configured.
"""
from dataclasses import dataclass
from typing import List, Optional

__all__ = ["Lane", "LaneConfigError", "build_lanes", "verify_lane_ownership"]


@dataclass(frozen=True)
class Lane:
    index: int
    priv_key: str
    address: str
    contract: str


class LaneConfigError(ValueError):
    """Raised when SWARM_KEYS / SWARM_CONTRACTS are malformed, mismatched, or
    contain a duplicate wallet."""


def _split_csv(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _address_of(priv_key: str) -> str:
    from eth_account import Account
    return Account.from_key(priv_key).address


def build_lanes(
    swarm_keys: str,
    swarm_contracts: str,
    fallback_key: str = "",
    fallback_contract: str = "",
) -> List[Lane]:
    """Parse SWARM_KEYS/SWARM_CONTRACTS (raw env-string values) into Lane objects.

    - SWARM_KEYS empty  -> single fallback lane from fallback_key/fallback_contract,
      or [] if fallback_key is also empty (nothing configured at all).
    - SWARM_CONTRACTS empty -> every lane uses fallback_contract (only sensible for
      a single lane; with >1 keys this almost certainly means misconfiguration,
      since one contract can't be owned by more than one wallet — still allowed
      here so callers can decide, but verify_lane_ownership will flag the mismatch).
    - SWARM_CONTRACTS with exactly 1 entry and >1 keys -> reused for all lanes
      (same "only one owner" caveat applies).
    - Otherwise lengths must match 1:1, else LaneConfigError.
    - Duplicate wallets across lanes raise LaneConfigError (defeats the purpose:
      two lanes on one wallet just race each other's nonce).
    """
    keys = _split_csv(swarm_keys)
    contracts = _split_csv(swarm_contracts)

    if not keys:
        if not fallback_key:
            return []
        # Same empty-contract check the multi-lane loop below enforces. Without
        # it, PRIVATE_KEY set with no FLASH_CONTRACT_ADDRESS produced a
        # Lane(contract="") that every executor silently returned None for —
        # indistinguishable from an unprofitable market, and (since the risk
        # governor landed) enough to walk the circuit breaker toward tripping on
        # what is purely a missing config value.
        if not fallback_contract:
            raise LaneConfigError(
                "PRIVATE_KEY is set but FLASH_CONTRACT_ADDRESS is empty — there is "
                "no deployed receiver to execute against. Deploy one (`jdl deploy "
                "receiver`) and set FLASH_CONTRACT_ADDRESS, or unset LIVE_EXECUTION."
            )
        return [Lane(index=0, priv_key=fallback_key, address=_address_of(fallback_key),
                      contract=fallback_contract)]

    if not contracts:
        contracts = [fallback_contract] * len(keys)
    elif len(contracts) == 1 and len(keys) > 1:
        contracts = contracts * len(keys)
    elif len(contracts) != len(keys):
        raise LaneConfigError(
            f"SWARM_KEYS has {len(keys)} entries but SWARM_CONTRACTS has "
            f"{len(contracts)} — they must be index-aligned 1:1 (each contract must "
            "be owned by its matching wallet), or SWARM_CONTRACTS must have exactly "
            "1 entry to reuse for all lanes."
        )

    lanes: List[Lane] = []
    seen_addrs = set()
    for i, (k, c) in enumerate(zip(keys, contracts)):
        addr = _address_of(k)
        if addr in seen_addrs:
            raise LaneConfigError(
                f"duplicate wallet in SWARM_KEYS at lane {i}: {addr} — two lanes on "
                "the same wallet would race each other's nonce, defeating parallel "
                "execution."
            )
        seen_addrs.add(addr)
        if not c:
            raise LaneConfigError(f"lane {i} ({addr}) has no contract address configured")
        lanes.append(Lane(index=i, priv_key=k, address=addr, contract=c))
    return lanes


_OWNER_SELECTOR = "0x8da5cb5b"  # owner()


def verify_lane_ownership(eth_call, lane: Lane) -> Optional[str]:
    """Read owner() from lane.contract via an injected eth_call(tx_dict)->hex fn and
    confirm it matches lane.address. Returns None if it matches OR if the on-chain
    read itself fails (ownership just can't be confirmed offline/rpc-down — not
    treated as a hard error). Returns a human-readable mismatch description
    otherwise, so callers can fail fast with a clear message instead of every trade
    silently reverting with OwnableUnauthorizedAccount.
    """
    try:
        raw = eth_call({"to": lane.contract, "data": _OWNER_SELECTOR})
        hexed = raw.hex() if hasattr(raw, "hex") else str(raw)
        hexed = hexed.replace("0x", "")
        if len(hexed) < 40:
            return None
        onchain_owner = "0x" + hexed[-40:]
        if onchain_owner.lower() != lane.address.lower():
            return (
                f"lane {lane.index}: contract {lane.contract} is owned by "
                f"{onchain_owner}, but the configured wallet is {lane.address} — "
                "every call from this lane will revert (OwnableUnauthorizedAccount). "
                "Deploy a NexusFlashReceiver with this wallet as owner, or fix "
                "SWARM_CONTRACTS."
            )
        return None
    except Exception:
        return None
