#!/usr/bin/env python3
"""
JDL Python Worker Bridge
Listens for JSON-RPC commands on stdin, dispatches to workers, returns results on stdout.
"""
import sys
import json
import importlib.util
import traceback

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
load_worker("engine", "python/engine.py")
load_worker("flash_executor", "python/flash_executor.py")
load_worker("real_executor", "python/real_executor.py")
load_worker("api_integrations", "python/api_integrations.py")
load_worker("scanner", "python/scanner/route_finder.py")

def dispatch(method, params):
    try:
        if method == "scan_routes":
            return {"routes": []}
        elif method == "execute_trade":
            return {"status": "simulated", "tx_hash": None}
        elif method == "get_price":
            return {"price": 0.0, "source": "simulated"}
        elif method == "analyze_opportunity":
            return {"opportunities": []}
        else:
            # Try to find the method in loaded workers
            for name, mod in WORKERS.items():
                if hasattr(mod, method):
                    return getattr(mod, method)(*params)
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
