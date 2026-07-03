"""
Self-contained test for jdl_native's layered backends. Runnable directly:
    cd python && python3 jdl_native/test_jdl_native.py
Verifies every available backend agrees on the arb scan, and that native backends
analyze bytecode while the pure-Python fallback reports analysis unavailable.
"""
import importlib
import os
import sys

# Ensure the parent (python/) is importable so `import jdl_native` works from any cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REQ = {
    "edges": [
        {"from": "USDC", "to": "WETH", "rate": 0.0005, "fee_bps": 5},
        {"from": "WETH", "to": "USDC", "rate": 2020, "fee_bps": 5},
    ],
    "base": "USDC", "loan_usd": 100000, "gas_usd": 1,
}
SELFDESTRUCT = "0x6000ff"  # PUSH1 0 ; SELFDESTRUCT -> "danger"


def _load(backend):
    os.environ["JDL_NATIVE_BACKEND"] = backend
    import jdl_native
    importlib.reload(jdl_native)
    return jdl_native


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✓ {msg}")
        else:
            failed += 1
            print(f"  ✗ {msg}")

    # Which backends are actually available here?
    available = []
    for be in ["cython", "ctypes", "subprocess", "python"]:
        m = _load(be)
        if m.active_backend() == be:
            available.append(be)
    print(f"available backends: {available}")
    check("python" in available, "pure-Python fallback always available")

    # All available backends agree on the scan result.
    nets = {}
    for be in available:
        m = _load(be)
        r = m.scan(REQ)
        assert r["opportunity"], f"{be} found no opportunity"
        nets[be] = round(r["opportunity"]["net_profit_usd"], 4)
        check(r["opportunity"]["path"] == ["USDC", "WETH", "USDC"], f"{be}: correct path")
    check(len(set(nets.values())) == 1, f"all backends agree on net profit: {nets}")

    # Native backends analyze bytecode; pure-Python reports unavailable.
    for be in available:
        m = _load(be)
        if be == "python":
            try:
                m.analyze(SELFDESTRUCT)
                check(False, "python analyze should raise AnalysisUnavailable")
            except m.AnalysisUnavailable:
                check(True, "python analyze raises AnalysisUnavailable (as designed)")
        else:
            rep = m.analyze(SELFDESTRUCT)
            check(rep.get("verdict") == "danger" and rep.get("has_selfdestruct"),
                  f"{be}: flags SELFDESTRUCT bytecode as danger")

    # Non-profitable set -> no opportunity.
    m = _load("python")
    bad = dict(REQ, edges=[{"from": "USDC", "to": "WETH", "rate": 0.0005, "fee_bps": 5},
                           {"from": "WETH", "to": "USDC", "rate": 2000.2, "fee_bps": 5}],
               gas_usd=50)
    check(m.scan(bad)["opportunity"] is None, "python: rejects net-losing loop")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
