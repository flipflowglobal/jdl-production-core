"""
Tests for wallet_lanes.py — lane parsing/validation, no live chain required
(eth_account derives addresses locally from private keys).
Run: cd python && python3 jdl_flash/test_wallet_lanes.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdl_flash import wallet_lanes as wl

# Arbitrary deterministic test-only private keys — never used for real funds,
# not tied to any specific tool's default accounts. ADDR_0 is derived from KEY_0
# below (verified at test time, not hardcoded blindly).
KEY_0 = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
KEY_1 = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690e"
KEY_2 = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690f"
from eth_account import Account as _Account
ADDR_0 = _Account.from_key(KEY_0).address

CONTRACT_A = "0x0000000000000000000000000000000000AAAA"
CONTRACT_B = "0x0000000000000000000000000000000000BBBB"
CONTRACT_C = "0x0000000000000000000000000000000000CCCC"


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  ✓ {msg}")
        else:
            failed += 1; print(f"  ✗ {msg}")

    # ── no config at all -> no lanes ──
    check(wl.build_lanes("", "") == [], "empty everything -> no lanes")

    # ── fallback: single wallet, no SWARM_KEYS set ──
    lanes = wl.build_lanes("", "", fallback_key=KEY_0, fallback_contract=CONTRACT_A)
    check(len(lanes) == 1 and lanes[0].address.lower() == ADDR_0.lower(),
          "falls back to single configured wallet when SWARM_KEYS unset")
    check(lanes[0].contract == CONTRACT_A, "fallback lane uses fallback contract")

    # ── multi-wallet: keys + matching contracts ──
    lanes = wl.build_lanes(f"{KEY_0},{KEY_1},{KEY_2}", f"{CONTRACT_A},{CONTRACT_B},{CONTRACT_C}")
    check(len(lanes) == 3, "3 keys + 3 contracts -> 3 lanes")
    check(lanes[0].address.lower() == ADDR_0.lower(), "lane 0 address derived correctly")
    check([l.contract for l in lanes] == [CONTRACT_A, CONTRACT_B, CONTRACT_C],
          "lanes index-aligned to their contracts")
    check(len({l.address for l in lanes}) == 3, "all 3 derived wallet addresses are distinct")

    # ── single contract reused across multiple keys ──
    lanes = wl.build_lanes(f"{KEY_0},{KEY_1}", CONTRACT_A)
    check(len(lanes) == 2 and lanes[0].contract == CONTRACT_A and lanes[1].contract == CONTRACT_A,
          "single SWARM_CONTRACTS entry reused for all lanes")

    # ── mismatched lengths (>1 contracts, wrong count) -> error ──
    try:
        wl.build_lanes(f"{KEY_0},{KEY_1},{KEY_2}", f"{CONTRACT_A},{CONTRACT_B}")
        check(False, "mismatched key/contract counts should raise")
    except wl.LaneConfigError:
        check(True, "mismatched key/contract counts raises LaneConfigError")

    # ── duplicate wallet -> error ──
    try:
        wl.build_lanes(f"{KEY_0},{KEY_0}", f"{CONTRACT_A},{CONTRACT_B}")
        check(False, "duplicate wallet should raise")
    except wl.LaneConfigError:
        check(True, "duplicate wallet in SWARM_KEYS raises LaneConfigError")

    # ── missing contract for a lane -> error ──
    try:
        wl.build_lanes(f"{KEY_0},{KEY_1}", "")  # empty contracts, no fallback -> ""
        check(False, "missing contract should raise")
    except wl.LaneConfigError:
        check(True, "lane with no contract configured raises LaneConfigError")

    # ── whitespace tolerance ──
    lanes = wl.build_lanes(f" {KEY_0} , {KEY_1} ", f" {CONTRACT_A} , {CONTRACT_B} ")
    check(len(lanes) == 2, "tolerates whitespace around comma-separated entries")

    # ── verify_lane_ownership: matches, mismatches, and RPC failure ──
    lane = wl.Lane(index=0, priv_key=KEY_0, address=ADDR_0, contract=CONTRACT_A)

    def eth_call_matches(tx):
        return bytes.fromhex("00" * 12 + ADDR_0[2:])

    def eth_call_mismatch(tx):
        return bytes.fromhex("00" * 12 + "1111111111111111111111111111111111111111")

    def eth_call_fails(tx):
        raise ConnectionError("rpc down")

    check(wl.verify_lane_ownership(eth_call_matches, lane) is None,
          "verify_lane_ownership: None when owner() matches the lane wallet")
    err = wl.verify_lane_ownership(eth_call_mismatch, lane)
    check(err is not None and "OwnableUnauthorizedAccount" in err,
          "verify_lane_ownership: clear error message on mismatch")
    check(wl.verify_lane_ownership(eth_call_fails, lane) is None,
          "verify_lane_ownership: None (not a hard error) when RPC read fails")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
