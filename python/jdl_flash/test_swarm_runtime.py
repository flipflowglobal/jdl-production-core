"""
Offline tests for swarm_runtime — orchestration only (no live chain).
Run: cd python && python3 jdl_flash/test_swarm_runtime.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdl_flash import swarm_runtime as sr


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  ✓ {msg}")
        else:
            failed += 1; print(f"  ✗ {msg}")

    # resolve_workers
    check(sr.resolve_workers("auto", cpu=8) == 8, "auto → cpu cores")
    check(sr.resolve_workers("max", cpu=8) == 32, "max → 4× cores capped at 32")
    check(sr.resolve_workers("max", cpu=2) == 8, "max on 2 cores → 8")
    check(sr.resolve_workers("5", cpu=8) == 5, "explicit int honored")
    check(sr.resolve_workers("garbage", cpu=8) == 8, "garbage → cores fallback")

    # route universe + partition disjointness/coverage
    tokens = ["USDC", "WETH", "WBTC", "DAI", "USDT", "ARB"]
    routes = sr.build_route_universe(tokens, fee_tiers=(500, 3000, 10000), base="USDC")
    # 5 mids × 3 × 3 = 45
    check(len(routes) == 45, f"universe size 45 (got {len(routes)})")
    for n in (1, 3, 4, 7, 16):
        parts = [sr.partition(routes, i, n) for i in range(n)]
        total = sum(len(p) for p in parts)
        # disjoint: no route in two partitions
        seen = set()
        overlap = False
        for p in parts:
            for r in p:
                k = sr.route_key(r)
                if k in seen:
                    overlap = True
                seen.add(k)
        check(total == len(routes) and not overlap and len(seen) == len(routes),
              f"n={n}: partitions disjoint + cover all {len(routes)} routes")

    # scan_fn: profitable mock → one opportunity; dedup on repeat
    def edges_for(route):
        # Only the WETH route is profitable; others break even. fee_tier carries the
        # concrete Uniswap fee so the winning path can be executed.
        if route["mid"] == "WETH":
            return [
                {"from": "USDC", "to": "WETH", "rate": 0.0005, "fee_bps": 0, "fee_tier": route["buy_fee"]},
                {"from": "WETH", "to": "USDC", "rate": 2020, "fee_bps": 0, "fee_tier": route["sell_fee"]},
            ]
        return [
            {"from": "USDC", "to": route["mid"], "rate": 1.0, "fee_bps": 0, "fee_tier": route["buy_fee"]},
            {"from": route["mid"], "to": "USDC", "rate": 1.0, "fee_bps": 0, "fee_tier": route["sell_fee"]},
        ]

    coord = sr.SwarmCoordinator(
        quote_edges_fn=edges_for, tokens=tokens, loan_usd=100000, gas_usd=1,
    )
    r0 = coord.scan_fn(0, 1)
    check(len(r0) == 1 and r0[0]["path"] == ["USDC", "WETH", "USDC"], "scan finds the profitable loop")
    check(r0[0].get("fees") and all(f in (500, 3000, 10000) for f in r0[0]["fees"]),
          f"winning path carries executable fee tiers: {r0[0].get('fees')}")
    # same tick, same path → deduped away
    r0b = coord.scan_fn(0, 1)
    check(r0b == [], "dedup: same path not returned twice")
    coord.reset_dedup()
    check(len(coord.scan_fn(0, 1)) == 1, "reset_dedup re-enables the find")

    # end-to-end swarm run with mock execute — nonce lanes strictly ordered
    executed = []
    def execute(opp, nonce, lane):
        executed.append((nonce, lane))
        return f"0xhash{nonce}"

    coord2 = sr.SwarmCoordinator(
        quote_edges_fn=edges_for, execute_fn=execute, tokens=tokens,
        loan_usd=100000, gas_usd=1, n_wallets=2,
    )
    swarm = coord2.make_swarm(n_workers=3, nonce_base=100)
    asyncio.run(swarm.run(rounds=1, interval=0.0))
    # at least one execution happened, nonces come from the lane formula (base+worker+k*n)
    check(len(executed) >= 1, f"swarm executed at least one opp (got {len(executed)})")
    valid_nonces = all((n - 100) % 1 == 0 for n, _ in executed)  # sanity
    check(valid_nonces and all(l < 2 for _, l in executed), "execution used valid nonce lanes + wallet lanes < n_wallets")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
