#!/usr/bin/env python3
"""
JDL Python Worker Bridge
Listens for JSON-RPC commands on stdin, dispatches to workers, returns results on stdout.
"""
import sys
import json
import asyncio
import importlib.util
import traceback
from typing import Any

WORKERS = {}

def load_worker(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        WORKERS[name] = mod
        return True
    return False

# Load available workers
load_worker("engine",              "python/engine.py")
load_worker("flash_executor",      "python/flash_executor.py")
load_worker("real_executor",       "python/real_executor.py")
load_worker("api_integrations",    "python/api_integrations.py")
load_worker("scanner",             "python/scanner/route_finder.py")
load_worker("composite_brain",     "python/composite_brain.py")
load_worker("ppo_engine",          "python/ppo_engine.py")
load_worker("thompson_engine",     "python/thompson_engine.py")
load_worker("ukf_engine",          "python/ukf_engine.py")
load_worker("cma_es_engine",       "python/cma_es_engine.py")

# ── Lazy-instantiated engine singletons ───────────────────────────────────
_BRAIN     = None
_PPO      = None
_THOMPSON = None
_UKF      = None
_CMA      = None

def _get_brain():
    global _BRAIN
    if _BRAIN is None:
        mod = WORKERS.get("composite_brain")
        if mod:
            _BRAIN = mod.CompositeBrain()
    return _BRAIN

def _get_ppo():
    global _PPO
    if _PPO is None:
        mod = WORKERS.get("ppo_engine")
        if mod:
            _PPO = mod.PPOEngine()
    return _PPO

def _get_thompson():
    global _THOMPSON
    if _THOMPSON is None:
        mod = WORKERS.get("thompson_engine")
        if mod:
            _THOMPSON = mod.ThompsonEngine()
    return _THOMPSON

def _get_ukf():
    global _UKF
    if _UKF is None:
        mod = WORKERS.get("ukf_engine")
        if mod:
            _UKF = mod.UKFEngine()
    return _UKF

def _get_cma():
    global _CMA
    if _CMA is None:
        mod = WORKERS.get("cma_es_engine")
        if mod:
            _CMA = mod.CMAESEngine()
    return _CMA

# ── Method dispatch table ──────────────────────────────────────────────────
METHOD_ROUTES = {}

def _register(methods: list[str]):
    """Decorator that registers a handler for one or more method names."""
    def wrapper(fn):
        for m in methods:
            METHOD_ROUTES[m] = fn
        return fn
    return wrapper

# ── Engine / price / trade methods ────────────────────────────────────────

@_register(["get_price"])
def _handle_get_price(params):
    mod = WORKERS.get("engine")
    if mod:
        token = params[0] if params else "ETH"
        price = mod.PriceOracle.get_price(token)
        return {"price": price, "source": "coingecko"}
    return {"price": 0.0, "source": "unavailable"}

@_register(["execute_trade"])
def _handle_execute_trade(params):
    return {"status": "simulated", "tx_hash": None,
            "note": "Use flash_executor for real execution"}

@_register(["scan_routes"])
def _handle_scan_routes(params):
    mod = WORKERS.get("scanner")
    if mod:
        scan_cls = getattr(mod, "RouteScanner", None)
        if scan_cls:
            try:
                pools = params[0] if params else []
                scanner = scan_cls(pool_addresses=pools)
                routes = scanner.scan()
                return {"routes": [r.__dict__ for r in routes]}
            except Exception:
                pass
    return {"routes": []}

@_register(["analyze_opportunity"])
def _handle_analyze_opportunity(params):
    return {"opportunities": []}

@_register(["scan_arb"])
def _handle_scan_arb(params):
    """Trigger an arbitrage scan via engineer's ArbitrageScanner."""
    mod = WORKERS.get("engine")
    if mod:
        try:
            scanner = mod.ArbitrageScanner()
            results = asyncio.run(scanner.scan_all())
            return {"opportunities": results}
        except Exception as e:
            return {"error": str(e)}
    return {"opportunities": []}

# ── Composite Brain methods ────────────────────────────────────────────────

@_register(["brain_evaluate"])
def _handle_brain_evaluate(params):
    brain = _get_brain()
    if brain is None:
        return {"error": "composite_brain not loaded"}
    brain_mod = WORKERS.get("composite_brain")
    route_mod = WORKERS.get("scanner")
    ArbitrageRoute = getattr(route_mod, "ArbitrageRoute", None) if route_mod else None
    if not ArbitrageRoute:
        from scanner.route_finder import ArbitrageRoute
    # Build a minimal ArbitrageRoute from params
    route_data = params[0] if params else {}
    route = ArbitrageRoute(
        path=route_data.get("path", []),
        pool_addresses=route_data.get("pool_addresses", []),
        gross_profit_usd=route_data.get("gross_profit_usd", 0.0),
        net_profit_usd=route_data.get("net_profit_usd", 0.0),
        profit_bps=route_data.get("profit_bps", 0.0),
        loan_amount_eth=route_data.get("loan_amount_eth", 0.0),
    )
    gas_gwei      = params[1] if len(params) > 1 else 30.0
    hour          = params[2] if len(params) > 2 else 12
    vol_5min      = params[3] if len(params) > 3 else 0.5
    success_rate  = params[4] if len(params) > 4 else 0.5
    current_price = params[5] if len(params) > 5 else 2000.0
    decision = brain.evaluate(route, gas_gwei, hour, vol_5min, success_rate, current_price)
    return decision.to_dict()

@_register(["brain_record_outcome"])
def _handle_brain_record_outcome(params):
    brain = _get_brain()
    if brain is None:
        return {"error": "composite_brain not loaded"}
    route_id = params[0] if params else ""
    profit   = params[1] if len(params) > 1 else 0.0
    brain.record_outcome(route_id, profit)
    return {"status": "ok"}

@_register(["brain_weight_report"])
def _handle_brain_weight_report(params):
    brain = _get_brain()
    if brain is None:
        return {"error": "composite_brain not loaded"}
    return brain.weight_report()

# ── PPO Engine methods ────────────────────────────────────────────────────

@_register(["ppo_predict"])
def _handle_ppo_predict(params):
    ppo = _get_ppo()
    if ppo is None:
        return {"error": "ppo_engine not loaded"}
    state = params[0] if params else None
    if state is None:
        state = ppo.build_state(profit_bps=0, gas_gwei=30, liquidity_log=10,
                                route_len=3, hour=12, vol_5min=0.5, success_rate=0.5)
    import numpy as np
    state_arr = np.array(state, dtype=np.float64)
    score = ppo.predict(state_arr)
    return {"score": score}

@_register(["ppo_predict_deterministic"])
def _handle_ppo_predict_det(params):
    ppo = _get_ppo()
    if ppo is None:
        return {"error": "ppo_engine not loaded"}
    state = params[0] if params else None
    if state is None:
        state = ppo.build_state(profit_bps=0, gas_gwei=30, liquidity_log=10,
                                route_len=3, hour=12, vol_5min=0.5, success_rate=0.5)
    import numpy as np
    state_arr = np.array(state, dtype=np.float64)
    score = ppo.predict_deterministic(state_arr)
    return {"score": score}

@_register(["ppo_build_state"])
def _handle_ppo_build_state(params):
    ppo = _get_ppo()
    if ppo is None:
        return {"error": "ppo_engine not loaded"}
    state = ppo.build_state(
        profit_bps=params[0] if len(params) > 0 else 0,
        gas_gwei=params[1] if len(params) > 1 else 30,
        liquidity_log=params[2] if len(params) > 2 else 10,
        route_len=params[3] if len(params) > 3 else 3,
        hour=params[4] if len(params) > 4 else 12,
        vol_5min=params[5] if len(params) > 5 else 0.5,
        success_rate=params[6] if len(params) > 6 else 0.5,
    )
    return {"state": state.tolist()}

# ── Thompson Engine methods ───────────────────────────────────────────────

@_register(["thompson_select"])
def _handle_thompson_select(params):
    eng = _get_thompson()
    if eng is None:
        return {"error": "thompson_engine not loaded"}
    candidates = params[0] if params else []
    k = params[1] if len(params) > 1 else 1
    if k > 1:
        selected = eng.select_top_k(candidates, k)
    else:
        selected = eng.select(candidates)
    return {"selected": selected}

@_register(["thompson_update"])
def _handle_thompson_update(params):
    eng = _get_thompson()
    if eng is None:
        return {"error": "thompson_engine not loaded"}
    arm_id = params[0] if params else ""
    reward = params[1] if len(params) > 1 else 0.0
    eng.update(arm_id, reward)
    return {"status": "ok"}

@_register(["thompson_stats"])
def _handle_thompson_stats(params):
    eng = _get_thompson()
    if eng is None:
        return {"error": "thompson_engine not loaded"}
    arm_id = params[0] if params else ""
    stats = eng.arm_stats(arm_id)
    if stats:
        return stats
    return eng.all_stats()

@_register(["thompson_top_arms"])
def _handle_thompson_top_arms(params):
    eng = _get_thompson()
    if eng is None:
        return {"error": "thompson_engine not loaded"}
    n = params[0] if params else 10
    return {"top_arms": eng.top_arms(n)}

# ── UKF Engine methods ────────────────────────────────────────────────────

@_register(["ukf_predict"])
def _handle_ukf_predict(params):
    ukf = _get_ukf()
    if ukf is None:
        return {"error": "ukf_engine not loaded"}
    ukf.predict()
    return {"status": "ok"}

@_register(["ukf_update"])
def _handle_ukf_update(params):
    ukf = _get_ukf()
    if ukf is None:
        return {"error": "ukf_engine not loaded"}
    price = params[0] if params else 0.0
    ukf.update(price)
    return {"status": "ok"}

@_register(["ukf_diagnostics"])
def _handle_ukf_diagnostics(params):
    ukf = _get_ukf()
    if ukf is None:
        return {"error": "ukf_engine not loaded"}
    return ukf.diagnostics()

# ── CMA-ES Engine methods ─────────────────────────────────────────────────

@_register(["cma_optimize"])
def _handle_cma_optimize(params):
    cma = _get_cma()
    if cma is None:
        return {"error": "cma_es_engine not loaded"}
    n_generations = params[0] if params else 20
    # If a serialised fitness function is provided as JSON callable metadata,
    # use a default objective: prefer high min_profit_bps with moderate hops
    result = cma.optimize(fitness_func=cma._default_fitness, n_generations=n_generations)
    return result

@_register(["cma_best_params"])
def _handle_cma_best_params(params):
    cma = _get_cma()
    if cma is None:
        return {"error": "cma_es_engine not loaded"}
    return cma.best_params

# ── Flash Loan methods ────────────────────────────────────────────────────

@_register(["flash_scan"])
def _handle_flash_scan(params):
    mod = WORKERS.get("flash_executor")
    if mod:
        try:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mod.scan_opportunities(use_ga=False, auto_execute=False)
            return {"output": buf.getvalue()[:2000]}
        except Exception as e:
            return {"error": str(e)}
    return {"scan": "flash_executor not loaded"}

@_register(["flash_status"])
def _handle_flash_status(params):
    mod = WORKERS.get("flash_executor")
    if mod:
        try:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mod.check_readiness()
            return {"output": buf.getvalue()[:2000]}
        except Exception as e:
            return {"error": str(e)}
    return {"status": "flash_executor not loaded"}

# ── Real Executor methods ─────────────────────────────────────────────────

@_register(["real_status"])
def _handle_real_status(params):
    mod = WORKERS.get("real_executor")
    if mod:
        try:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mod.full_status()
            return {"output": buf.getvalue()[:2000]}
        except Exception as e:
            return {"error": str(e)}
    return {"status": "real_executor not loaded"}

# ── API Integration methods ───────────────────────────────────────────────

@_register(["api_prices"])
def _handle_api_prices(params):
    mod = WORKERS.get("api_integrations")
    if mod:
        prices = mod.get_live_prices()
        return {"prices": prices}
    return {"prices": {}}

@_register(["api_portfolio"])
def _handle_api_portfolio(params):
    mod = WORKERS.get("api_integrations")
    if mod:
        try:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mod.scan_full_portfolio()
            return {"output": buf.getvalue()[:3000]}
        except Exception as e:
            return {"error": str(e)}
    return {"portfolio": "api_integrations not loaded"}

@_register(["api_balances"])
def _handle_api_balances(params):
    mod = WORKERS.get("api_integrations")
    if mod:
        chain = params[0] if params else "ethereum"
        bal = mod.alchemy_get_eth_balance(chain)
        return {"chain": chain, "balance": bal}
    return {"balance": 0.0}

# ── Main dispatcher ───────────────────────────────────────────────────────

def dispatch(method: str, params: list) -> dict:
    try:
        handler = METHOD_ROUTES.get(method)
        if handler:
            return handler(params)
        # Fallback: search loaded modules for a matching function
        for name, mod in WORKERS.items():
            if hasattr(mod, method):
                result = getattr(mod, method)(*params)
                return {"result": result}
        raise ValueError(f"Unknown method: {method}")
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            req_id = msg.get("id", "0")
            method = msg.get("method", "")
            params = msg.get("params", [])
            result = dispatch(method, params)
            response = {"id": req_id, "result": result}
        except Exception as e:
            response = {"id": "error", "error": str(e)}
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
