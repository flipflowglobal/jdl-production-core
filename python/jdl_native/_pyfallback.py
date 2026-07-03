"""
Pure-Python fallback for the arbitrage hot-path — a faithful port of the Rust
`best_cycle` (rust/hotpath/src/lib.rs). Used when no native engine is available
(e.g. Termux/Android, where the Rust cdylib and CLI binary can't be built).

Only the arbitrage scan has a pure-Python fallback. EVM bytecode analysis is
~2,400 lines of Rust and is not reimplemented here — `analyze` requires the
native engine or the CLI binary.
"""
AAVE_PREMIUM_BPS = 5.0
_BPS = 10_000.0
_MAX_HOPS = 5


def _effective(edge):
    return edge["rate"] * (1.0 - edge.get("fee_bps", 0.0) / _BPS)


def _net_profit(mult, loan_usd, gas_usd):
    gross_gain = loan_usd * (mult - 1.0)
    premium = loan_usd * AAVE_PREMIUM_BPS / _BPS
    return gross_gain - premium - gas_usd


def scan(request: dict) -> dict:
    edges = request.get("edges", [])
    base = request["base"]
    loan_usd = float(request.get("loan_usd", 0))
    gas_usd = float(request.get("gas_usd", 0))
    min_profit = float(request.get("min_profit_usd", 0))

    adj = {}
    tokens = set()
    for e in edges:
        adj.setdefault(e["from"], []).append(e)
        tokens.add(e["from"])
        tokens.add(e["to"])

    best = {"net": None}

    def dfs(node, mult, depth, path):
        if depth > _MAX_HOPS:
            return
        if node == base and depth >= 2:
            net = _net_profit(mult, loan_usd, gas_usd)
            if net > min_profit and (best["net"] is None or net > best["net"]["net_profit_usd"]):
                best["net"] = {
                    "path": list(path),
                    "gross_multiplier": mult,
                    "net_profit_usd": net,
                }
            return
        for e in adj.get(node, []):
            if e["to"] != base and e["to"] in path:
                continue
            path.append(e["to"])
            dfs(e["to"], mult * _effective(e), depth + 1, path)
            path.pop()

    dfs(base, 1.0, 0, [base])
    return {"opportunity": best["net"], "tokens": len(tokens), "edges": len(edges)}


def analyze(bytecode: str) -> dict:
    raise NotImplementedError(
        "EVM bytecode analysis requires the native engine (build rust/hotpath, "
        "or install the jdl-hotpath binary). No pure-Python fallback exists."
    )


def backend():
    return "python"
