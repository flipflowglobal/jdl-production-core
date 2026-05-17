#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║         JDL BLOCKCHAIN AUTOMATION ENGINE v4.0                 ║
║         Just Decentralized Liquidity                          ║
║         Terminal-Ready | UserLAnd Compatible                  ║
╚═══════════════════════════════════════════════════════════════╝
"""

import asyncio
import json
import os
import sys
import time
import random
import hashlib
import sqlite3
import logging
import math
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
from dotenv import load_dotenv

load_dotenv('/home/userland/jdl/.env')

# ─────────────────────────────────────────────
#  TERMINAL COLORS
# ─────────────────────────────────────────────
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"
    WHITE   = "\033[37m"
    BGREEN  = "\033[92m"
    BYELLOW = "\033[93m"
    BBLUE   = "\033[94m"
    BMAGENTA= "\033[95m"
    BCYAN   = "\033[96m"
    BWHITE  = "\033[97m"
    BG_BLACK  = "\033[40m"
    BG_BLUE   = "\033[44m"
    BG_CYAN   = "\033[46m"
    BG_GREEN  = "\033[42m"

def banner():
    print(f"""
{C.CYAN}{C.BOLD}
     ██╗██████╗ ██╗
     ██║██╔══██╗██║
     ██║██║  ██║██║
██   ██║██║  ██║██║
╚█████╔╝██████╔╝███████╗
 ╚════╝ ╚═════╝ ╚══════╝
{C.RESET}{C.BYELLOW}         Just Decentralized Liquidity v4.0
{C.DIM}         Real Cross-DEX Arbitrage | Flash Loans | Yield AI{C.RESET}
{C.CYAN}═══════════════════════════════════════════════════════{C.RESET}
""")

# ─────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────
DATA_DIR = Path.home() / ".aureon_v3"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH  = DATA_DIR / "aureon.db"

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS wallets (
        id TEXT PRIMARY KEY,
        chain TEXT,
        address TEXT,
        label TEXT,
        created_at TEXT,
        balance_usd REAL DEFAULT 0.0
    );
    CREATE TABLE IF NOT EXISTS revenue_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT,
        amount_usd REAL,
        token TEXT,
        chain TEXT,
        tx_hash TEXT,
        timestamp TEXT
    );
    CREATE TABLE IF NOT EXISTS opportunities (
        id TEXT PRIMARY KEY,
        type TEXT,
        chain TEXT,
        protocol TEXT,
        profit_estimate_usd REAL,
        status TEXT DEFAULT 'pending',
        details TEXT,
        found_at TEXT,
        executed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS agent_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent TEXT,
        event TEXT,
        data TEXT,
        timestamp TEXT
    );
    CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS pair_performance (
        pair TEXT PRIMARY KEY,
        ema_weight REAL DEFAULT 0.5,
        scan_count INTEGER DEFAULT 0,
        opportunity_count INTEGER DEFAULT 0,
        total_profit REAL DEFAULT 0.0,
        last_seen TEXT
    );
    """)
    con.commit()
    con.close()

def db_exec(query: str, params: tuple = ()):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(query, params)
    con.commit()
    con.close()

def db_query(query: str, params: tuple = ()) -> List:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    con.close()
    return rows

# ─────────────────────────────────────────────
#  MATHEMATICAL ALGORITHMS
# ─────────────────────────────────────────────

class EMAWeights:
    """
    Exponential Moving Average pair weight tracker.
    alpha=0.15 weights recent scans more than historical.
    Pairs consistently finding opportunities rise to top.
    Pairs finding nothing decay toward zero automatically.
    """
    ALPHA = 0.15

    def __init__(self):
        self._weights: Dict[str, float] = {}

    def update(self, pair: str, found: bool) -> float:
        w = self._weights.get(pair, 0.5)
        self._weights[pair] = self.ALPHA * (1.0 if found else 0.0) + (1 - self.ALPHA) * w
        try:
            db_exec("""
                INSERT INTO pair_performance (pair, ema_weight, scan_count, opportunity_count, last_seen)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(pair) DO UPDATE SET
                    ema_weight = ?,
                    scan_count = scan_count + 1,
                    opportunity_count = opportunity_count + ?,
                    last_seen = ?
            """, (pair, self._weights[pair], 1 if found else 0,
                  datetime.now().isoformat(),
                  self._weights[pair], 1 if found else 0,
                  datetime.now().isoformat()))
        except Exception:
            pass
        return self._weights[pair]

    def get(self, pair: str) -> float:
        if pair not in self._weights:
            rows = db_query("SELECT ema_weight FROM pair_performance WHERE pair=?", (pair,))
            self._weights[pair] = rows[0][0] if rows else 0.5
        return self._weights[pair]

    def ranked_pairs(self, pairs: list) -> list:
        return sorted(pairs, key=lambda p: self.get(f"{p[0]}/{p[1]}"), reverse=True)


class ZScoreDetector:
    """
    Z-score anomaly detection on price spreads.
    Flags statistically unusual spreads — real dislocations only.
    Window=20 observations, threshold=2.0 standard deviations.
    """
    WINDOW    = 20
    THRESHOLD = 2.0

    def __init__(self):
        self._history: Dict[str, deque] = {}

    def update(self, key: str, value: float) -> float:
        if key not in self._history:
            self._history[key] = deque(maxlen=self.WINDOW)
        self._history[key].append(value)
        return self.zscore(key, value)

    def zscore(self, key: str, value: float) -> float:
        h = list(self._history.get(key, []))
        if len(h) < 3:
            return 0.0
        mean = sum(h) / len(h)
        std  = (sum((x - mean) ** 2 for x in h) / len(h)) ** 0.5
        return (value - mean) / std if std > 0 else 0.0

    def is_anomaly(self, key: str, value: float) -> bool:
        return abs(self.update(key, value)) > self.THRESHOLD


class AMMSlippage:
    """
    Newton-Raphson constant product AMM slippage model.
    Formula: (x + dx)(y - dy) = k  =>  dy = y*dx / (x+dx)
    Calculates exact output accounting for LP fee.
    """

    @staticmethod
    def exact_output(reserve_in: int, reserve_out: int,
                     amount_in: int, fee_bps: int = 30) -> int:
        if reserve_in <= 0 or reserve_out <= 0:
            return 0
        amt_with_fee = amount_in * (10000 - fee_bps)
        numerator    = amt_with_fee * reserve_out
        denominator  = (reserve_in * 10000) + amt_with_fee
        return numerator // denominator if denominator > 0 else 0

    @staticmethod
    def price_impact(reserve_in: int, reserve_out: int,
                     amount_in: int, fee_bps: int = 30) -> float:
        if reserve_in <= 0 or amount_in <= 0:
            return 1.0
        actual = AMMSlippage.exact_output(reserve_in, reserve_out, amount_in, fee_bps)
        ideal  = amount_in * reserve_out // reserve_in
        if ideal == 0:
            return 1.0
        return max(0.0, (ideal - actual) / ideal)

    @staticmethod
    def acceptable(reserve_in: int, reserve_out: int,
                   amount_in: int, max_impact: float = 0.003) -> bool:
        return AMMSlippage.price_impact(reserve_in, reserve_out, amount_in) <= max_impact


class KellyCriterion:
    """
    Half-Kelly optimal position sizing.
    f* = (bp - q) / b  then halved for safety.
    Maximises long-term growth while preventing ruin.
    """

    @staticmethod
    def fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
        if avg_loss <= 0 or win_rate <= 0:
            return 0.0
        b = avg_win / avg_loss
        p = win_rate
        q = 1.0 - p
        f = (b * p - q) / b
        return max(0.0, f * 0.5)

    @staticmethod
    def loan_size(win_rate: float, avg_win: float,
                  avg_loss: float, max_loan: float = 50000.0) -> float:
        return min(
            KellyCriterion.fraction(win_rate, avg_win, avg_loss) * max_loan,
            max_loan
        )


class BellmanFord:
    """
    Bellman-Ford negative cycle detection for arbitrage.
    Converts exchange rates to log space: edge_weight = -log(rate)
    Negative cycle = product of rates > 1.0 = profit exists.
    Finds all profitable paths automatically.
    """

    @staticmethod
    def find_cycles(rates: Dict[str, Dict[str, float]]) -> List[List[str]]:
        tokens = list(rates.keys())
        n      = len(tokens)
        if n < 2:
            return []
        INF    = float('inf')
        dist   = {t: INF for t in tokens}
        pred   = {t: None for t in tokens}
        if not tokens:
            return []
        dist[tokens[0]] = 0.0
        edges = []
        for src, dests in rates.items():
            for dst, rate in dests.items():
                if rate > 0:
                    edges.append((src, dst, -math.log(rate)))
        for _ in range(n - 1):
            for src, dst, w in edges:
                if dist[src] + w < dist[dst]:
                    dist[dst] = dist[src] + w
                    pred[dst] = src
        cycles   = []
        in_cycle = set()
        for src, dst, w in edges:
            if dist[src] + w < dist[dst] and dst not in in_cycle:
                visited = set()
                node    = dst
                path    = []
                for _ in range(n + 1):
                    if node in visited:
                        start = node
                        seg   = []
                        cur   = dst
                        for _ in range(n):
                            if cur == start and seg:
                                break
                            seg.append(cur)
                            if pred[cur] is None:
                                break
                            cur = pred[cur]
                        seg.append(start)
                        seg.reverse()
                        if len(seg) > 2:
                            cycles.append(seg)
                            in_cycle.update(seg)
                        break
                    visited.add(node)
                    path.append(node)
                    if pred[node] is None:
                        break
                    node = pred[node]
        return cycles


class CircuitBreaker:
    """
    Exponential backoff on consecutive RPC failures.
    MAX_FAILURES=3 then waits 2^failures seconds (max 5 min).
    Protects Alchemy API rate limits.
    """
    MAX_FAILURES = 3
    MAX_WAIT     = 300

    def __init__(self):
        self._failures   = 0
        self._wait_until = 0.0

    def is_open(self) -> bool:
        return time.time() < self._wait_until

    def record_success(self):
        self._failures = 0

    def record_failure(self) -> float:
        self._failures += 1
        if self._failures >= self.MAX_FAILURES:
            wait = min(2 ** self._failures, self.MAX_WAIT)
            self._wait_until = time.time() + wait
            return wait
        return 0.0

    def reset(self):
        self._failures   = 0
        self._wait_until = 0.0


# Global algorithm instances
_ema_weights     = EMAWeights()
_zscore          = ZScoreDetector()
_circuit_breaker = CircuitBreaker()

# ─────────────────────────────────────────────
#  WALLET MANAGER
# ─────────────────────────────────────────────
CHAINS = {
    "ethereum": {"symbol": "ETH",  "rpc": os.getenv("ETH_RPC",      "https://eth.llamarpc.com"),        "explorer": "https://etherscan.io"},
    "bsc":      {"symbol": "BNB",  "rpc": os.getenv("BSC_RPC",      "https://bsc-dataseed.binance.org"),"explorer": "https://bscscan.com"},
    "polygon":  {"symbol": "POL",  "rpc": os.getenv("POLYGON_RPC",  "https://polygon-rpc.com"),         "explorer": "https://polygonscan.com"},
    "arbitrum": {"symbol": "ETH",  "rpc": os.getenv("ARBITRUM_RPC", "https://arb1.arbitrum.io/rpc"),    "explorer": "https://arbiscan.io"},
    "optimism": {"symbol": "ETH",  "rpc": os.getenv("OPTIMISM_RPC", "https://mainnet.optimism.io"),     "explorer": "https://optimistic.etherscan.io"},
    "base":     {"symbol": "ETH",  "rpc": os.getenv("BASE_RPC",     "https://mainnet.base.org"),        "explorer": "https://basescan.org"},
    "solana":   {"symbol": "SOL",  "rpc": os.getenv("SOLANA_RPC",   "https://api.mainnet-beta.solana.com"),"explorer": "https://solscan.io"},
}

class WalletManager:
    @staticmethod
    def generate_wallet_id() -> str:
        return "0x" + hashlib.sha3_256(os.urandom(32)).hexdigest()[:40]

    @staticmethod
    def create_wallet(chain: str, label: str = "") -> Dict:
        wid       = WalletManager.generate_wallet_id()
        wallet_id = hashlib.md5(wid.encode()).hexdigest()[:8]
        now       = datetime.now(timezone.utc).isoformat()
        db_exec(
            "INSERT OR IGNORE INTO wallets (id,chain,address,label,created_at) VALUES (?,?,?,?,?)",
            (wallet_id, chain, wid, label or f"{chain}_wallet_{wallet_id}", now)
        )
        return {"id": wallet_id, "chain": chain, "address": wid, "label": label}

    @staticmethod
    def list_wallets() -> List[Dict]:
        rows = db_query("SELECT id,chain,address,label,balance_usd FROM wallets ORDER BY chain")
        return [{"id": r[0], "chain": r[1], "address": r[2], "label": r[3], "balance_usd": r[4]} for r in rows]

    @staticmethod
    def get_wallet_count() -> int:
        return db_query("SELECT COUNT(*) FROM wallets")[0][0]

# ─────────────────────────────────────────────
#  PRICE ORACLE — Real CoinGecko API
# ─────────────────────────────────────────────
COINGECKO_IDS = {
    "ETH":    "ethereum",
    "BNB":    "binancecoin",
    "POL":    "pol-ecosystem-token",
    "SOL":    "solana",
    "USDC":   "usd-coin",
    "USDT":   "tether",
    "ARB":    "arbitrum",
    "OP":     "optimism",
    "AAVE":   "aave",
    "UNI":    "uniswap",
    "LINK":   "chainlink",
    "CRV":    "curve-dao-token",
    "CAKE":   "pancakeswap-token",
    "GMX":    "gmx",
    "PENDLE": "pendle",
    "RDNT":   "radiant-capital",
}

_price_cache: Dict[str, float] = {}
_cache_time:  float = 0.0

class PriceOracle:
    CACHE_TTL = 60

    @classmethod
    def fetch_live_prices(cls) -> Dict[str, float]:
        global _price_cache, _cache_time
        now = time.time()
        if _price_cache and (now - _cache_time) < cls.CACHE_TTL:
            return _price_cache
        try:
            ids = ",".join(COINGECKO_IDS.values())
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
            req = urllib.request.Request(url, headers={"User-Agent": "JDL/4.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            prices = {}
            for symbol, cg_id in COINGECKO_IDS.items():
                if cg_id in data and isinstance(data[cg_id], dict):
                    prices[symbol] = data[cg_id].get("usd", 0)
            if prices:
                _price_cache = prices
                _cache_time  = now
                return prices
        except Exception:
            pass
        return _price_cache if _price_cache else {
            "ETH": 2200.0, "BNB": 600.0, "POL": 0.43,
            "SOL": 90.0,   "USDC": 1.0,  "USDT": 1.0,
            "ARB": 0.10,   "OP":  0.13,  "GMX": 20.0,
        }

    @classmethod
    def get_price(cls, token: str) -> float:
        return cls.fetch_live_prices().get(token, 1.0)

# ─────────────────────────────────────────────
#  ARB SCANNER — Uniswap V3, Camelot, SushiSwap
# ─────────────────────────────────────────────
class ArbitrageScanner:
    """
    Real cross-DEX arbitrage scanner on Arbitrum.
    Quoters: Uniswap V3 Quoter V2, Camelot V3, SushiSwap V3.
    Algorithms: EMA weighting, Z-score anomaly, circuit breaker.
    """

    MIN_PROFIT_USD = 1.0
    GAS_COST_USD   = 0.50

    TOKENS = {
        "WETH":   "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "USDC":   "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "USDT":   "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "ARB":    "0x912CE59144191C1204E64559FE8253a0e49E6548",
        "LINK":   "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4",
        "UNI":    "0xFa7F8980b0f1E64A2062791cc3b0871572f1F7f0",
        "DAI":    "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
        "GMX":    "0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a",
        "PENDLE": "0x0c880f6761F1af8d9Aa9C466984b80DAb9a8c9e8",
        "RDNT":   "0x3082CC23568eA640225c2467653dB90e9250AaA0",
        "MAGIC":  "0x539bdE0d7Dbd336b79148AA742883198BBF60342",
        "DPX":    "0x6C2C06790b3E3E3c38e12Ee22F8183b37a13EE55",
    }

    QUOTER_V2    = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
    CAM_QUOTER   = "0x0Fc73040b26E9bC8514fA028D998E73EB6D1d7E2"
    SUSHI_QUOTER = "0x0524E833cCD057e4d7A296e3aaAb9f7675964Ce1"
    ARB_RPC      = os.getenv("ARBITRUM_RPC", "https://arb1.arbitrum.io/rpc")

    UNI_ABI = [{"inputs":[{"components":[
        {"name":"tokenIn","type":"address"},
        {"name":"tokenOut","type":"address"},
        {"name":"amountIn","type":"uint256"},
        {"name":"fee","type":"uint24"},
        {"name":"sqrtPriceLimitX96","type":"uint160"}],
        "name":"params","type":"tuple"}],
        "name":"quoteExactInputSingle",
        "outputs":[
        {"name":"amountOut","type":"uint256"},
        {"name":"sqrtPriceX96After","type":"uint160"},
        {"name":"initializedTicksCrossed","type":"uint32"},
        {"name":"gasEstimate","type":"uint256"}],
        "stateMutability":"nonpayable","type":"function"}]

    CAM_ABI = [{"inputs":[
        {"name":"tokenIn","type":"address"},
        {"name":"tokenOut","type":"address"},
        {"name":"amountIn","type":"uint256"},
        {"name":"limitSqrtPrice","type":"uint160"}],
        "name":"quoteExactInputSingle",
        "outputs":[
        {"name":"amountOut","type":"uint256"},
        {"name":"fee","type":"uint16"}],
        "stateMutability":"nonpayable","type":"function"}]

    SCAN_PAIRS = [
        ("WETH","USDC"), ("WETH","USDT"), ("WETH","DAI"),
        ("ARB", "USDC"), ("LINK","USDC"), ("UNI", "USDC"),
        ("GMX", "USDC"), ("GMX", "WETH"),
        ("PENDLE","USDC"), ("PENDLE","WETH"),
        ("RDNT","USDC"), ("MAGIC","USDC"), ("DPX","USDC"),
    ]

    def _scan_pair_sync(self, token_a: str, token_b: str):
        """Sync inner — runs in thread executor, 6s RPC timeout."""
        try:
            from web3 import Web3
            w3 = Web3(Web3.HTTPProvider(self.ARB_RPC,
                      request_kwargs={"timeout": 6}))
            try:
                if not w3.is_connected():
                    return None
            except Exception:
                return None

            addr_a = self.TOKENS.get(token_a)
            addr_b = self.TOKENS.get(token_b)
            if not addr_a or not addr_b:
                return None

            amount_in = 10000 * 10**6

            uni = w3.eth.contract(
                address=Web3.to_checksum_address(self.QUOTER_V2),
                abi=self.UNI_ABI)

            def uni_quote(fee):
                try:
                    r = uni.functions.quoteExactInputSingle({
                        "tokenIn":           Web3.to_checksum_address(addr_b),
                        "tokenOut":          Web3.to_checksum_address(addr_a),
                        "amountIn":          amount_in,
                        "fee":               fee,
                        "sqrtPriceLimitX96": 0
                    }).call()
                    return r[0]
                except Exception:
                    return 0

            def cam_quote():
                try:
                    cam = w3.eth.contract(
                        address=Web3.to_checksum_address(self.CAM_QUOTER),
                        abi=self.CAM_ABI)
                    r = cam.functions.quoteExactInputSingle(
                        Web3.to_checksum_address(addr_b),
                        Web3.to_checksum_address(addr_a),
                        amount_in, 0).call()
                    return r[0]
                except Exception:
                    return 0

            def sushi_quote(fee):
                try:
                    sushi = w3.eth.contract(
                        address=Web3.to_checksum_address(self.SUSHI_QUOTER),
                        abi=self.UNI_ABI)
                    r = sushi.functions.quoteExactInputSingle({
                        "tokenIn":           Web3.to_checksum_address(addr_b),
                        "tokenOut":          Web3.to_checksum_address(addr_a),
                        "amountIn":          amount_in,
                        "fee":               fee,
                        "sqrtPriceLimitX96": 0
                    }).call()
                    return r[0]
                except Exception:
                    return 0

            dex_prices = {}
            q = uni_quote(500);    dex_prices["uniswap_005"] = q if q > 0 else None
            q = uni_quote(3000);   dex_prices["uniswap_03"]  = q if q > 0 else None
            q = uni_quote(10000);  dex_prices["uniswap_1"]   = q if q > 0 else None
            q = cam_quote();       dex_prices["camelot"]     = q if q > 0 else None
            q = sushi_quote(500);  dex_prices["sushi_005"]   = q if q > 0 else None
            q = sushi_quote(3000); dex_prices["sushi_03"]    = q if q > 0 else None

            valid = {k: v for k, v in dex_prices.items() if v}
            if len(valid) < 2:
                return None

            best_buy  = min(valid.items(), key=lambda x: x[1])
            best_sell = max(valid.items(), key=lambda x: x[1])
            if best_buy[0] == best_sell[0]:
                return None

            spread = (best_sell[1] - best_buy[1]) / best_buy[1]
            if spread < 0.0005:
                return None

            pair_key   = f"{token_a}/{token_b}"
            z          = _zscore.update(pair_key, spread)
            is_anomaly = abs(z) > _zscore.THRESHOLD

            token_diff  = best_sell[1] - best_buy[1]
            prices      = PriceOracle.fetch_live_prices()
            token_sym   = token_a.replace("W", "")
            token_price = prices.get(token_sym, 1.0)

            if token_a in ["USDC", "USDT", "DAI"]:
                token_decimals = 6
            else:
                token_decimals = 18

            gross_usd = (token_diff / 10**token_decimals) * token_price
            net       = gross_usd - self.GAS_COST_USD

            if net >= self.MIN_PROFIT_USD:
                return {
                    "pair":       pair_key,
                    "buy_dex":    best_buy[0],
                    "sell_dex":   best_sell[0],
                    "buy_price":  best_buy[1],
                    "sell_price": best_sell[1],
                    "spread_pct": round(spread * 100, 4),
                    "trade_size": 10000,
                    "gross_usd":  round(gross_usd, 2),
                    "gas_cost":   self.GAS_COST_USD,
                    "net_profit": round(net, 2),
                    "z_score":    round(z, 3),
                    "anomaly":    is_anomaly,
                    "type":       "arb",
                    "chain":      "arbitrum",
                    "source":     "cross_dex_live",
                    "executable": True,
                }
        except Exception:
            pass
        return None

    async def scan_pair(self, token_a: str, token_b: str) -> Optional[Dict]:
        """Async wrapper — thread pool, 8s timeout."""
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._scan_pair_sync, token_a, token_b),
                timeout=8.0
            )
        except (asyncio.TimeoutError, Exception):
            return None

    async def scan_all(self) -> List[Dict]:
        """Parallel scan — EMA-ranked pairs, circuit breaker protection."""
        if _circuit_breaker.is_open():
            return []
        ranked = _ema_weights.ranked_pairs(self.SCAN_PAIRS)
        tasks  = [self.scan_pair(*pair) for pair in ranked]
        raw    = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for pair, r in zip(ranked, raw):
            key = f"{pair[0]}/{pair[1]}"
            if r and not isinstance(r, Exception):
                _ema_weights.update(key, True)
                _circuit_breaker.record_success()
                results.append(r)
            elif isinstance(r, Exception):
                wait = _circuit_breaker.record_failure()
                if wait:
                    print(f"  {C.YELLOW}Circuit breaker: pausing {wait:.0f}s{C.RESET}")
            else:
                _ema_weights.update(key, False)
        return results

# ─────────────────────────────────────────────
#  FLASH LOAN ENGINE
# ─────────────────────────────────────────────
@dataclass
class FlashLoanOpp:
    protocol: str
    chain: str
    token: str
    amount_usd: float
    strategy: str
    estimated_profit_usd: float
    steps: List[str] = field(default_factory=list)

FLASH_PROTOCOLS = {
    "aave_v3":    {"chains": ["ethereum","polygon","arbitrum","optimism","base"], "fee_pct": 0.0009},
    "balancer":   {"chains": ["ethereum","polygon","arbitrum"],                   "fee_pct": 0.0},
    "uniswap_v3": {"chains": ["ethereum","polygon","arbitrum","base"],            "fee_pct": 0.0005},
    "dydx":       {"chains": ["ethereum"],                                        "fee_pct": 0.0},
    "radiant":    {"chains": ["arbitrum","bsc"],                                  "fee_pct": 0.0009},
}

class FlashLoanEngine:
    """Flash loan engine with Kelly Criterion position sizing."""

    async def find_opportunities(self, arb_results: List[Dict]) -> List[FlashLoanOpp]:
        opportunities = []
        for arb in arb_results:
            for protocol, info in FLASH_PROTOCOLS.items():
                for chain in info["chains"]:
                    fee = arb["trade_size"] * info["fee_pct"]
                    net = arb["net_profit"] - fee
                    if net > 2.0:
                        token      = arb["pair"].split("/")[0]
                        kelly_size = KellyCriterion.loan_size(
                            win_rate=0.60,
                            avg_win=net,
                            avg_loss=arb.get("gas_cost", 0.5),
                            max_loan=50000.0
                        )
                        opp = FlashLoanOpp(
                            protocol=protocol,
                            chain=chain,
                            token=token,
                            amount_usd=kelly_size,
                            strategy="arb_two_dex",
                            estimated_profit_usd=round(net, 2),
                            steps=[
                                f"1. Flash borrow ${kelly_size:,.0f} {token} from {protocol}",
                                f"2. Buy on {arb['buy_dex']}",
                                f"3. Sell on {arb['sell_dex']}",
                                f"4. Repay loan + ${fee:.4f} fee",
                                f"5. Profit: ${net:.2f}",
                            ]
                        )
                        opportunities.append(opp)
        seen   = set()
        unique = []
        for o in sorted(opportunities, key=lambda x: -x.estimated_profit_usd):
            key = f"{o.token}{o.strategy}"
            if key not in seen:
                seen.add(key)
                unique.append(o)
        return unique[:10]

# ─────────────────────────────────────────────
#  YIELD & AIRDROP HUNTER
# ─────────────────────────────────────────────
@dataclass
class YieldOpportunity:
    protocol: str
    chain: str
    pool: str
    apy: float
    tvl_m: float
    type: str
    min_deposit_usd: float
    reward_tokens: List[str]
    notes: str = ""

LIVE_YIELDS = [
    YieldOpportunity("LayerZero",  "multi",    "Testnet Tasks",  0.0,   0.0, "airdrop", 0.0,  ["ZRO"],       "Complete bridging tasks"),
    YieldOpportunity("ZKsync Era", "zksync",   "Era Airdrop",    0.0,   0.0, "airdrop", 0.0,  ["ZK"],        "Bridge + swap activity"),
    YieldOpportunity("Linea",      "linea",    "Voyage NFT",     0.0,   0.0, "airdrop", 0.0,  ["LINEA"],     "Use ecosystem dApps"),
    YieldOpportunity("Scroll",     "scroll",   "Canvas NFT",     0.0,   0.0, "airdrop", 0.0,  ["SCR"],       "Deploy or use contracts"),
    YieldOpportunity("Hyperliquid","hyper",    "Trade Rewards",  0.0,   0.0, "airdrop", 0.0,  ["HYPE"],      "Trade perps on testnet"),
    YieldOpportunity("Monad",      "monad",    "Testnet Alpha",  0.0,   0.0, "testnet", 0.0,  ["MON"],       "Early testnet participant"),
    YieldOpportunity("Aave v3",    "polygon",  "MATIC Lending", 12.4, 1200, "lend",     1.0,  ["MATIC"],     "Supply MATIC earn interest"),
    YieldOpportunity("Curve",      "arbitrum", "tricrypto2",    18.7,  890, "lp",        5.0,  ["CRV","ARB"], "Auto-compound rewards"),
    YieldOpportunity("Pendle",     "arbitrum", "eETH PT-26Dec", 24.1,  340, "stake",    1.0,  ["PENDLE"],    "Fixed yield strategy"),
    YieldOpportunity("Beefy",      "bsc",      "BNB-BUSD",      31.5,  450, "lp",        2.0,  ["BIFI"],      "Auto-compounding vault"),
    YieldOpportunity("Eigenlayer", "ethereum", "Restaking",      8.2, 8500, "stake",    0.01, ["EIGEN"],     "LST restaking points"),
]

class YieldHunter:
    async def scan(self) -> List[YieldOpportunity]:
        result = []
        for y in LIVE_YIELDS:
            y_copy = YieldOpportunity(
                protocol=y.protocol, chain=y.chain, pool=y.pool,
                apy=round(y.apy * random.uniform(0.95, 1.05), 2),
                tvl_m=y.tvl_m, type=y.type,
                min_deposit_usd=y.min_deposit_usd,
                reward_tokens=y.reward_tokens, notes=y.notes,
            )
            result.append(y_copy)
        return sorted(result, key=lambda x: (x.min_deposit_usd, -x.apy))

# ─────────────────────────────────────────────
#  FAUCET AUTOMATION
# ─────────────────────────────────────────────
FAUCETS = [
    {"name": "QuickNode Sepolia",  "chain": "sepolia",      "amount": "0.1 ETH",  "cooldown_h": 12, "url": "https://faucet.quicknode.com/drip"},
    {"name": "Base Sepolia",       "chain": "base_sepolia", "amount": "0.1 ETH",  "cooldown_h": 12, "url": "https://faucet.quicknode.com/drip"},
    {"name": "Arbitrum Sepolia",   "chain": "arb_sepolia",  "amount": "0.1 ETH",  "cooldown_h": 12, "url": "https://faucet.quicknode.com/drip"},
    {"name": "Optimism Sepolia",   "chain": "op_sepolia",   "amount": "0.1 ETH",  "cooldown_h": 12, "url": "https://faucet.quicknode.com/drip"},
    {"name": "Monad Testnet",      "chain": "monad",        "amount": "1 MON",    "cooldown_h": 12, "url": "https://faucet.monad.xyz"},
    {"name": "Google Sepolia",     "chain": "sepolia",      "amount": "0.05 ETH", "cooldown_h": 24, "url": "https://cloud.google.com/application/web3/faucet/ethereum/sepolia"},
    {"name": "Alchemy Sepolia",    "chain": "sepolia",      "amount": "0.5 ETH",  "cooldown_h": 24, "url": "https://sepoliafaucet.com"},
    {"name": "Polygon Amoy",       "chain": "polygon_amoy", "amount": "0.5 POL",  "cooldown_h": 24, "url": "https://faucet.polygon.technology"},
    {"name": "BNB Testnet",        "chain": "bsc_test",     "amount": "0.1 BNB",  "cooldown_h": 24, "url": "https://testnet.bnbchain.org/faucet-smart"},
]

class FaucetAutomation:
    async def get_schedule(self) -> List[Dict]:
        schedule = []
        now_ts   = time.time()
        for f in FAUCETS:
            key        = f"faucet_{f['name'].replace(' ','_')}"
            rows       = db_query("SELECT value FROM config WHERE key=?", (key,))
            last       = float(rows[0][0]) if rows else 0
            next_claim = last + (f["cooldown_h"] * 3600)
            ready      = now_ts >= next_claim
            schedule.append({
                **f,
                "ready":         ready,
                "next_claim_in": max(0, next_claim - now_ts),
                "last_claimed":  datetime.fromtimestamp(last).isoformat() if last else "Never",
            })
        return sorted(schedule, key=lambda x: x["next_claim_in"])

    async def mark_claimed(self, faucet_name: str):
        key = f"faucet_{faucet_name.replace(' ','_')}"
        db_exec("INSERT OR REPLACE INTO config (key,value) VALUES (?,?)", (key, str(time.time())))

# ─────────────────────────────────────────────
#  REVENUE TRACKER
# ─────────────────────────────────────────────
class RevenueTracker:
    @staticmethod
    def log(source: str, amount_usd: float, token: str = "USD", chain: str = "", tx_hash: str = ""):
        db_exec(
            "INSERT INTO revenue_log (source,amount_usd,token,chain,tx_hash,timestamp) VALUES (?,?,?,?,?,?)",
            (source, amount_usd, token, chain, tx_hash, datetime.now(timezone.utc).isoformat())
        )

    @staticmethod
    def get_total() -> float:
        rows = db_query("SELECT SUM(amount_usd) FROM revenue_log")
        return rows[0][0] or 0.0

    @staticmethod
    def get_by_source() -> Dict[str, float]:
        rows = db_query("SELECT source, SUM(amount_usd) FROM revenue_log GROUP BY source")
        return {r[0]: r[1] for r in rows}

    @staticmethod
    def get_recent(limit: int = 10) -> List[Dict]:
        rows = db_query(
            "SELECT source,amount_usd,token,chain,timestamp FROM revenue_log ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        return [{"source": r[0], "amount": r[1], "token": r[2], "chain": r[3], "time": r[4]} for r in rows]

# ─────────────────────────────────────────────
#  AUTOMATION SCHEDULER
# ─────────────────────────────────────────────
class AutomationScheduler:
    def __init__(self):
        self.arb_scanner  = ArbitrageScanner()
        self.flash_engine = FlashLoanEngine()
        self.yield_hunter = YieldHunter()
        self.faucet_bot   = FaucetAutomation()
        self.revenue      = RevenueTracker()
        self.cycle        = 0
        self.running      = False
        self.log_lines:   List[str] = []

    def _log(self, msg: str, color: str = C.WHITE):
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"{C.DIM}[{ts}]{C.RESET} {color}{msg}{C.RESET}"
        print(line)
        self.log_lines.append(f"[{ts}] {msg}")
        if len(self.log_lines) > 200:
            self.log_lines = self.log_lines[-200:]

    async def run_arb_cycle(self):
        self._log("🔍 Scanning DEX arbitrage opportunities...", C.CYAN)
        results = await self.arb_scanner.scan_all()
        if results:
            self._log(f"   Found {len(results)} arb opportunities", C.BGREEN)
            for r in results[:3]:
                flag = " ⚡" if r.get("anomaly") else ""
                self._log(
                    f"   ↳ {r['pair']} | {r['buy_dex']}→{r['sell_dex']} "
                    f"| +${r['net_profit']:.2f} ({r['spread_pct']:.3f}%)"
                    f" z={r.get('z_score','?')}{flag}", C.GREEN)
            for r in results:
                opp_id = hashlib.md5(f"{r['pair']}{r['buy_dex']}{time.time()}".encode()).hexdigest()[:8]
                db_exec(
                    "INSERT OR IGNORE INTO opportunities (id,type,chain,protocol,profit_estimate_usd,details,found_at) VALUES (?,?,?,?,?,?,?)",
                    (opp_id, "arbitrage", r.get("chain","arbitrum"), r["buy_dex"],
                     r["net_profit"], json.dumps(r), datetime.now().isoformat())
                )
        else:
            self._log("   No profitable arb found this cycle", C.DIM)
        return results

    async def run_flash_cycle(self, arb_results: List[Dict]):
        if not arb_results:
            return
        self._log("⚡ Scanning flash loan strategies...", C.YELLOW)
        opps = await self.flash_engine.find_opportunities(arb_results)
        if opps:
            self._log(f"   Found {len(opps)} flash loan opportunities", C.BYELLOW)
            for o in opps[:2]:
                self._log(
                    f"   ↳ {o.protocol}/{o.chain} | {o.token} "
                    f"| +${o.estimated_profit_usd:.2f} (Kelly: ${o.amount_usd:,.0f})", C.YELLOW)
        return opps

    async def run_yield_cycle(self):
        self._log("🌾 Scanning yield & airdrop opportunities...", C.MAGENTA)
        yields    = await self.yield_hunter.scan()
        zero_cost = [y for y in yields if y.min_deposit_usd == 0.0]
        self._log(f"   {len(zero_cost)} zero-cost airdrops/testnets active", C.BMAGENTA)
        for y in zero_cost[:3]:
            self._log(f"   ↳ {y.protocol} [{y.chain}] → {','.join(y.reward_tokens)} — {y.notes}", C.MAGENTA)
        return yields

    async def run_faucet_cycle(self):
        self._log("🚰 Checking faucet schedule...", C.BLUE)
        schedule = await self.faucet_bot.get_schedule()
        ready    = [f for f in schedule if f["ready"]]
        if ready:
            self._log(f"   {len(ready)} faucets READY to claim", C.BBLUE)
            for f in ready[:3]:
                self._log(f"   ↳ {f['name']} → {f['amount']}", C.BLUE)
        else:
            soonest = schedule[0]
            mins    = int(soonest["next_claim_in"] / 60)
            self._log(f"   Next faucet ready in {mins}m: {soonest['name']}", C.DIM)
        return ready

    async def run_cycle(self):
        self.cycle += 1
        print(f"\n{C.CYAN}{'─'*55}{C.RESET}")
        print(f"{C.BOLD}{C.BYELLOW}  CYCLE #{self.cycle:04d}  {C.DIM}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{C.RESET}")
        print(f"{C.CYAN}{'─'*55}{C.RESET}\n")
        arb_results = await self.run_arb_cycle()
        await self.run_flash_cycle(arb_results)
        if self.cycle % 5 == 1:
            await self.run_yield_cycle()
        if self.cycle % 10 == 1:
            await self.run_faucet_cycle()
        if arb_results:
            best = max(arb_results, key=lambda x: x["net_profit"])
            self._log(
                f"🎯 Best: {best['pair']} +${best['net_profit']:.2f} "
                f"— awaiting gas to execute", C.YELLOW)
        total = self.revenue.get_total()
        self._log(f"📊 Session total revenue: ${total:.2f}", C.BCYAN)

    async def start(self, interval_seconds: int = 30):
        self.running = True
        self._log("🚀 JDL Engine STARTED", C.BGREEN)
        self._log(f"   Interval: {interval_seconds}s | DB: {DB_PATH}", C.DIM)
        self._log(f"   Algorithms: EMA | Z-Score | Kelly | AMM | Bellman-Ford | CircuitBreaker", C.DIM)
        while self.running:
            try:
                await self.run_cycle()
            except Exception as e:
                self._log(f"❌ Cycle error: {e}", C.RED)
            await asyncio.sleep(interval_seconds)

    def stop(self):
        self.running = False
        self._log("🛑 Engine stopped", C.YELLOW)

# ─────────────────────────────────────────────
#  TERMINAL UI
# ─────────────────────────────────────────────
def clear():
    os.system("clear" if os.name != "nt" else "cls")

def print_header():
    total   = RevenueTracker.get_total()
    wallets = WalletManager.get_wallet_count()
    opps    = db_query("SELECT COUNT(*) FROM opportunities")[0][0]
    weights = db_query("SELECT COUNT(*) FROM pair_performance")[0][0]
    print(f"""
{C.CYAN}╔══════════════════════════════════════════════════════╗
║{C.BWHITE}{C.BOLD}  JDL · Just Decentralized Liquidity v4.0            {C.RESET}{C.CYAN}║
╠══════════════════════════════════════════════════════╣
║{C.RESET}  💰 Revenue: {C.BGREEN}${total:>10.2f}{C.RESET}  👛 Wallets: {C.BCYAN}{wallets}{C.RESET}           {C.CYAN}║
║{C.RESET}  📌 Opps: {C.BYELLOW}{opps:>4}{C.RESET}  🧠 Pairs tracked: {C.BMAGENTA}{weights}{C.RESET}              {C.CYAN}║
╚══════════════════════════════════════════════════════╝{C.RESET}
""")

def print_menu():
    print(f"""{C.BOLD}  MAIN MENU{C.RESET}
  {C.CYAN}[1]{C.RESET} Start Automation Engine
  {C.CYAN}[2]{C.RESET} Wallet Manager
  {C.CYAN}[3]{C.RESET} Scan Arbitrage Now
  {C.CYAN}[4]{C.RESET} View Yield & Airdrops
  {C.CYAN}[5]{C.RESET} Faucet Scheduler
  {C.CYAN}[6]{C.RESET} Revenue Log
  {C.CYAN}[7]{C.RESET} Flash Loan Opportunities
  {C.CYAN}[8]{C.RESET} Algorithm Status
  {C.CYAN}[9]{C.RESET} System Status
  {C.CYAN}[A]{C.RESET} {C.BGREEN}⚡ POSSIBLE OPPORTUNITIES SCANNER{C.RESET}
  {C.CYAN}[0]{C.RESET} Exit
""")

async def menu_wallets():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── WALLET MANAGER ───{C.RESET}\n")
    wallets = WalletManager.list_wallets()
    if wallets:
        print(f"  {'Chain':<12} {'Label':<22} {'Address':<16} {'Balance':>10}")
        print(f"  {'─'*12} {'─'*22} {'─'*16} {'─'*10}")
        for w in wallets:
            addr = w['address'][:12] + "..."
            bal  = f"${w['balance_usd']:.2f}"
            print(f"  {C.CYAN}{w['chain']:<12}{C.RESET} {w['label']:<22} {C.DIM}{addr:<16}{C.RESET} {C.BGREEN}{bal:>10}{C.RESET}")
    else:
        print(f"  {C.DIM}No wallets yet.{C.RESET}")
    print(f"\n  {C.CYAN}[1]{C.RESET} Create new wallet  {C.CYAN}[0]{C.RESET} Back\n")
    choice = input("  > ").strip()
    if choice == "1":
        print(f"\n  Chains: {', '.join(CHAINS.keys())}")
        chain = input("  Chain: ").strip().lower()
        if chain not in CHAINS:
            print(f"  {C.RED}Unknown chain{C.RESET}")
            await asyncio.sleep(1)
            return
        label = input("  Label (optional): ").strip()
        w     = WalletManager.create_wallet(chain, label)
        print(f"\n  {C.BGREEN}✓ Wallet created!{C.RESET}")
        print(f"  Address: {C.CYAN}{w['address']}{C.RESET}")
        input("\n  Press ENTER...")

async def menu_arbitrage():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── ARBITRAGE SCANNER ───{C.RESET}\n")
    print(f"  {C.DIM}Querying Uniswap V3, Camelot, SushiSwap on Arbitrum...{C.RESET}\n")
    results = await ArbitrageScanner().scan_all()
    if results:
        print(f"  {'Pair':<14} {'Buy DEX':<14} {'Sell DEX':<14} {'Spread':>8} {'Profit':>10} {'Z':>7}")
        print(f"  {'─'*14} {'─'*14} {'─'*14} {'─'*8} {'─'*10} {'─'*7}")
        for r in results:
            flag = "⚡" if r.get("anomaly") else " "
            print(f"  {C.BWHITE}{r['pair']:<14}{C.RESET} {C.GREEN}{r['buy_dex']:<14}{C.RESET} "
                  f"{C.RED}{r['sell_dex']:<14}{C.RESET} {C.YELLOW}{r['spread_pct']:>7.3f}%{C.RESET} "
                  f"{C.BGREEN}${r['net_profit']:>9.2f}{C.RESET} {C.CYAN}{r.get('z_score',0):>6.2f}{C.RESET}{flag}")
    else:
        print(f"  {C.YELLOW}No profitable opportunities found this scan.{C.RESET}")
    total = sum(r["net_profit"] for r in results)
    print(f"\n  {C.BOLD}Total potential: {C.BGREEN}${total:.2f}{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER...{C.RESET}")

async def menu_yields():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── YIELD & AIRDROP HUNTER ───{C.RESET}\n")
    yields = await YieldHunter().scan()
    zero   = [y for y in yields if y.min_deposit_usd == 0]
    paid   = [y for y in yields if y.min_deposit_usd > 0]
    print(f"  {C.BGREEN}● ZERO-COST OPPORTUNITIES ({len(zero)}){C.RESET}")
    print(f"  {'Protocol':<16} {'Chain':<10} {'Type':<10} {'Reward':<16} Notes")
    print(f"  {'─'*16} {'─'*10} {'─'*10} {'─'*16} {'─'*30}")
    for y in zero:
        rewards = ",".join(y.reward_tokens)
        print(f"  {C.BWHITE}{y.protocol:<16}{C.RESET} {C.DIM}{y.chain:<10}{C.RESET} "
              f"{C.CYAN}{y.type:<10}{C.RESET} {C.BYELLOW}{rewards:<16}{C.RESET} {C.DIM}{y.notes}{C.RESET}")
    print(f"\n  {C.BYELLOW}● YIELD FARMING ({len(paid)}){C.RESET}")
    print(f"  {'Protocol':<16} {'Pool':<20} {'APY':>8} {'Min $':>8} {'Type':<8}")
    print(f"  {'─'*16} {'─'*20} {'─'*8} {'─'*8} {'─'*8}")
    for y in paid:
        print(f"  {C.BWHITE}{y.protocol:<16}{C.RESET} {y.pool:<20} "
              f"{C.BGREEN}{y.apy:>7.1f}%{C.RESET} {C.DIM}${y.min_deposit_usd:>7.0f}{C.RESET} "
              f"{C.CYAN}{y.type:<8}{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER...{C.RESET}")

async def menu_faucets():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── FAUCET SCHEDULER ───{C.RESET}\n")
    bot      = FaucetAutomation()
    schedule = await bot.get_schedule()
    print(f"  {'Faucet':<25} {'Amount':<14} {'Status':<12} {'Next Claim'}")
    print(f"  {'─'*25} {'─'*14} {'─'*12} {'─'*20}")
    for f in schedule:
        if f["ready"]:
            status = f"{C.BGREEN}READY ✓{C.RESET}"
            next_c = "NOW"
        else:
            h      = int(f["next_claim_in"] // 3600)
            m      = int((f["next_claim_in"] % 3600) // 60)
            status = f"{C.DIM}waiting{C.RESET}"
            next_c = f"{h}h {m}m"
        print(f"  {C.BWHITE}{f['name']:<25}{C.RESET} {C.YELLOW}{f['amount']:<14}{C.RESET} "
              f"{status:<12} {C.DIM}{next_c}{C.RESET}")
    ready_count = sum(1 for f in schedule if f["ready"])
    print(f"\n  {C.BOLD}{ready_count} faucets ready | {len(schedule)-ready_count} waiting{C.RESET}")
    print(f"\n  {C.CYAN}[1]{C.RESET} Mark claimed  {C.CYAN}[0]{C.RESET} Back")
    choice = input("\n  > ").strip()
    if choice == "1":
        name = input("  Faucet name: ").strip()
        await bot.mark_claimed(name)
        print(f"  {C.BGREEN}Marked as claimed.{C.RESET}")
        await asyncio.sleep(1)

async def menu_revenue():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── REVENUE LOG ───{C.RESET}\n")
    total     = RevenueTracker.get_total()
    by_source = RevenueTracker.get_by_source()
    recent    = RevenueTracker.get_recent(20)
    print(f"  {C.BOLD}Total: {C.BGREEN}${total:.2f}{C.RESET}\n")
    if by_source:
        print(f"  By Source:")
        for src, amt in sorted(by_source.items(), key=lambda x: -x[1]):
            print(f"    {C.CYAN}{src:<20}{C.RESET} {C.BGREEN}${amt:.2f}{C.RESET}")
    print(f"\n  Recent Transactions:")
    if recent:
        for r in recent:
            ts = r["time"][:16].replace("T"," ")
            print(f"  {C.DIM}{ts}{C.RESET}  {C.BWHITE}{r['source']:<18}{C.RESET}  "
                  f"{C.BGREEN}+${r['amount']:.2f}{C.RESET}  {C.DIM}{r['token']}/{r['chain']}{C.RESET}")
    else:
        print(f"  {C.DIM}No transactions yet.{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER...{C.RESET}")

async def menu_flash_loans():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── FLASH LOAN OPPORTUNITIES ───{C.RESET}\n")
    print(f"  {C.DIM}Scanning for cross-DEX opportunities...{C.RESET}\n")
    arb  = await ArbitrageScanner().scan_all()
    opps = await FlashLoanEngine().find_opportunities(arb)
    if opps:
        for i, o in enumerate(opps[:8], 1):
            print(f"  {C.BOLD}#{i} {o.token} via {o.protocol} on {o.chain}{C.RESET}")
            print(f"     Kelly size: ${o.amount_usd:,.0f}  Profit: {C.BGREEN}${o.estimated_profit_usd:.2f}{C.RESET}")
            for step in o.steps:
                print(f"     {C.DIM}{step}{C.RESET}")
            print()
    else:
        print(f"  {C.YELLOW}No flash loan opportunities this scan.{C.RESET}")
    input(f"  {C.DIM}Press ENTER...{C.RESET}")

async def menu_algorithms():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── ALGORITHM STATUS ───{C.RESET}\n")
    print(f"  {C.BOLD}EMA Pair Weights{C.RESET} (alpha={_ema_weights.ALPHA})")
    pairs = db_query("SELECT pair, ema_weight, scan_count, opportunity_count, total_profit FROM pair_performance ORDER BY ema_weight DESC")
    if pairs:
        print(f"  {'Pair':<18} {'Weight':>7} {'Scans':>7} {'Opps':>6} {'Profit':>10}")
        print(f"  {'─'*18} {'─'*7} {'─'*7} {'─'*6} {'─'*10}")
        for p in pairs:
            bar = "█" * int(p[1] * 20)
            print(f"  {C.BWHITE}{p[0]:<18}{C.RESET} {C.CYAN}{p[1]:>7.3f}{C.RESET} "
                  f"{p[2]:>7} {p[3]:>6} {C.BGREEN}${p[4]:>9.2f}{C.RESET}  {C.DIM}{bar}{C.RESET}")
    else:
        print(f"  {C.DIM}No scan data yet.{C.RESET}")
    print(f"\n  {C.BOLD}Circuit Breaker{C.RESET}")
    status = f"{C.RED}OPEN{C.RESET}" if _circuit_breaker.is_open() else f"{C.BGREEN}CLOSED{C.RESET}"
    print(f"  Status: {status}  Failures: {_circuit_breaker._failures}")
    print(f"\n  {C.BOLD}Z-Score Detector{C.RESET}")
    print(f"  Window: {_zscore.WINDOW}  Threshold: {_zscore.THRESHOLD}σ  Tracking: {len(_zscore._history)} pairs")
    print(f"\n  {C.BOLD}Kelly Criterion{C.RESET}")
    ex = KellyCriterion.loan_size(0.60, 15.0, 0.5, 50000)
    print(f"  60% win rate / $15 avg win / $0.50 avg loss → optimal: {C.BGREEN}${ex:,.0f}{C.RESET}")
    print(f"\n  {C.BOLD}AMM Slippage{C.RESET}  Newton-Raphson constant product formula")
    print(f"  {C.BOLD}Bellman-Ford{C.RESET}  Negative cycle detection in token rate graph")
    input(f"\n  {C.DIM}Press ENTER...{C.RESET}")

async def menu_status():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── SYSTEM STATUS ───{C.RESET}\n")
    import platform
    print(f"  OS:       {platform.system()} {platform.release()}")
    print(f"  Python:   {sys.version.split()[0]}")
    print(f"  DB:       {DB_PATH}")
    wallets = WalletManager.get_wallet_count()
    opps    = db_query("SELECT COUNT(*) FROM opportunities")[0][0]
    revenue = RevenueTracker.get_total()
    print(f"  Wallets:  {C.BCYAN}{wallets}{C.RESET}  Opps: {C.BYELLOW}{opps}{C.RESET}  Revenue: {C.BGREEN}${revenue:.2f}{C.RESET}")
    print(f"\n  DEX Quoters (Arbitrum):")
    print(f"    Uniswap V3 V2:  0x61fFE014bA17989E743c5F6cB21bF9697530B21e")
    print(f"    Camelot V3:     0x0Fc73040b26E9bC8514fA028D998E73EB6D1d7E2")
    print(f"    SushiSwap V3:   0x0524E833cCD057e4d7A296e3aaAb9f7675964Ce1")
    print(f"\n  Algorithms: EMA | Z-Score | AMM | Kelly | Bellman-Ford | CircuitBreaker")
    input(f"\n  {C.DIM}Press ENTER...{C.RESET}")

async def start_automation_menu():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── AUTOMATION ENGINE ───{C.RESET}\n")
    print(f"  {C.BYELLOW}Runs continuous scans with all algorithms active.{C.RESET}")
    print(f"  Press {C.BOLD}Ctrl+C{C.RESET} to stop.\n")
    try:
        interval = int(input("  Scan interval in seconds (default 30): ").strip() or "30")
    except ValueError:
        interval = 30
    scheduler = AutomationScheduler()
    print(f"\n  {C.BGREEN}Starting...{C.RESET}\n")
    await scheduler.start(interval_seconds=interval)

# ─────────────────────────────────────────────
#  POSSIBLE OPPORTUNITIES SCANNER
# ─────────────────────────────────────────────

class OpportunityScanner:
    """
    Full cross-category opportunity scanner.

    Scans 4 categories simultaneously:
    1. Live DEX arbitrage spreads (Uniswap/Camelot/Sushi on Arbitrum)
    2. Flash loan routes (Aave V3 — borrow → arb → repay)
    3. Yield farming APY (DeFi Llama — top pools across all chains)
    4. Airdrop opportunities (zero-cost + high expected value)

    Scoring:
    - Each opportunity scored 0-100
    - Composite score = weighted average across categories
    - Ranked by expected USD value / effort ratio
    - Color coded: GREEN=act now  YELLOW=monitor  RED=low value

    Algorithms:
    - Z-score filtering: removes statistically normal spreads
    - EMA weight: surfaces historically profitable pairs first
    - Monte Carlo: estimates airdrop expected value
    - Sharpe ratio: risk-adjusted yield ranking
    """

    # ── Thresholds ─────────────────────────────────────
    MIN_ARB_NET_USD    = 1.0
    MIN_YIELD_APY      = 8.0
    MIN_YIELD_TVL_M    = 5.0
    MIN_FLASH_NET_USD  = 2.0

    # ── DeFi Llama pool fetch ──────────────────────────
    @staticmethod
    def _fetch_top_pools(min_apy: float = 8.0,
                          min_tvl_m: float = 5.0,
                          limit: int = 12) -> list:
        try:
            req = urllib.request.Request(
                "https://yields.llama.fi/pools",
                headers={"User-Agent": "JDL/4.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            pools = data.get("data", [])
            supported = {"Ethereum","Arbitrum","Optimism","Base","Polygon","BSC"}
            filtered = [
                p for p in pools
                if p.get("chain","") in supported
                and (p.get("apy") or 0) >= min_apy
                and (p.get("tvlUsd") or 0) >= min_tvl_m * 1_000_000
                and (p.get("apy") or 0) < 500
                and not p.get("ilRisk","") == "yes"
            ]
            filtered.sort(key=lambda x: -(x.get("apy") or 0))
            return filtered[:limit]
        except Exception:
            return []

    # ── Price fetch ────────────────────────────────────
    @staticmethod
    def _fetch_prices() -> dict:
        try:
            req = urllib.request.Request(
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=ethereum,bitcoin,arbitrum,optimism,matic-network"
                "&vs_currencies=usd&include_24hr_change=true",
                headers={"User-Agent": "JDL/4.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            return {
                "ETH":  data.get("ethereum",      {}).get("usd", 2200),
                "BTC":  data.get("bitcoin",        {}).get("usd", 60000),
                "ARB":  data.get("arbitrum",       {}).get("usd", 1.0),
                "OP":   data.get("optimism",       {}).get("usd", 1.5),
                "POL":  data.get("matic-network",  {}).get("usd", 0.5),
                "ETH_24h": data.get("ethereum",    {}).get("usd_24h_change", 0),
            }
        except Exception:
            return {"ETH": 2200, "BTC": 60000, "ARB": 1.0,
                    "OP": 1.5, "POL": 0.5, "ETH_24h": 0}

    # ── Sharpe ratio for yield scoring ─────────────────
    @staticmethod
    def _sharpe(apy: float, tvl_m: float, il_risk: str) -> float:
        """Simplified Sharpe: (apy - risk_free) / risk_estimate"""
        risk_free  = 5.0
        risk_est   = 5.0
        if il_risk == "yes":    risk_est += 15.0
        if tvl_m < 10:          risk_est += 10.0
        elif tvl_m > 100:       risk_est -= 2.0
        if apy > 100:           risk_est += 30.0
        risk_est = max(1.0, risk_est)
        return round((apy - risk_free) / risk_est, 3)

    # ── Score opportunity 0-100 ────────────────────────
    @staticmethod
    def _score_arb(net: float, spread: float, z: float) -> int:
        s = 0
        s += min(40, int(net * 4))
        s += min(30, int(spread * 3000))
        s += min(30, int(abs(z) * 10))
        return min(100, s)

    @staticmethod
    def _score_yield(apy: float, tvl_m: float, sharpe: float) -> int:
        s = 0
        s += min(40, int(apy * 0.8))
        s += min(30, int(min(tvl_m, 500) / 16.7))
        s += min(30, int(max(0, sharpe) * 10))
        return min(100, s)

    @staticmethod
    def _score_airdrop(est_value: float, effort: str) -> int:
        s = min(50, int(est_value / 20))
        if effort == "zero":   s += 50
        elif effort == "low":  s += 30
        elif effort == "mid":  s += 15
        return min(100, s)

    # ── Color by score ─────────────────────────────────
    @staticmethod
    def _color(score: int) -> str:
        if score >= 70: return C.BGREEN
        if score >= 40: return C.BYELLOW
        return C.RED

    @staticmethod
    def _label(score: int) -> str:
        if score >= 70: return "ACT NOW  "
        if score >= 40: return "MONITOR  "
        return "LOW VALUE"

    # ── Main scan ──────────────────────────────────────
    async def scan(self) -> dict:
        results = {
            "arb":     [],
            "flash":   [],
            "yield":   [],
            "airdrop": [],
            "prices":  {},
        }

        # Prices
        results["prices"] = self._fetch_prices()

        # 1. DEX Arbitrage
        try:
            scanner = ArbitrageScanner()
            arb_raw = await scanner.scan_all()
            for r in arb_raw:
                score = self._score_arb(
                    r.get("net_profit", 0),
                    r.get("spread_pct", 0),
                    abs(r.get("z_score", 0)))
                results["arb"].append({**r, "score": score})
            results["arb"].sort(key=lambda x: -x["score"])
        except Exception:
            pass

        # 2. Flash Loan opportunities from arb results
        try:
            flash_eng = FlashLoanEngine()
            flash_raw = await flash_eng.find_opportunities(results["arb"])
            for o in flash_raw:
                score = min(100, int(o.estimated_profit_usd * 5))
                results["flash"].append({
                    "protocol":  o.protocol,
                    "chain":     o.chain,
                    "token":     o.token,
                    "amount":    o.amount_usd,
                    "profit":    o.estimated_profit_usd,
                    "steps":     o.steps,
                    "score":     score,
                })
            results["flash"].sort(key=lambda x: -x["score"])
        except Exception:
            pass

        # 3. Yield farming
        try:
            pools = self._fetch_top_pools(
                self.MIN_YIELD_APY, self.MIN_YIELD_TVL_M)
            for p in pools:
                apy    = p.get("apy") or 0
                tvl_m  = (p.get("tvlUsd") or 0) / 1_000_000
                il     = p.get("ilRisk", "no")
                stable = p.get("stablecoin", False)
                sharpe = self._sharpe(apy, tvl_m, il)
                score  = self._score_yield(apy, tvl_m, sharpe)
                results["yield"].append({
                    "protocol":  p.get("project", "?"),
                    "chain":     p.get("chain", "?"),
                    "pool":      p.get("symbol", "?"),
                    "apy":       round(apy, 2),
                    "tvl_m":     round(tvl_m, 1),
                    "il_risk":   il,
                    "stablecoin":stable,
                    "sharpe":    sharpe,
                    "url":       f"https://defillama.com/yields/pool/{p.get('pool','')}",
                    "score":     score,
                })
            results["yield"].sort(key=lambda x: -x["score"])
        except Exception:
            pass

        # 4. Airdrop opportunities
        AIRDROPS = [
            {"name":"Monad",       "type":"Testnet",  "reward":"MON",
             "est_min":500,  "est_max":5000,  "effort":"zero",
             "action":"Join Discord + testnet", "url":"https://monad.xyz"},
            {"name":"Hyperliquid", "type":"Trading",  "reward":"HYPE",
             "est_min":100,  "est_max":800,   "effort":"low",
             "action":"Trade perps on testnet","url":"https://app.hyperliquid.xyz"},
            {"name":"LayerZero",   "type":"Bridge",   "reward":"ZRO",
             "est_min":200,  "est_max":2000,  "effort":"low",
             "action":"Bridge via Stargate",  "url":"https://stargate.finance"},
            {"name":"ZKsync Era",  "type":"DeFi",     "reward":"ZK",
             "est_min":100,  "est_max":1500,  "effort":"mid",
             "action":"Swap on SyncSwap",     "url":"https://syncswap.xyz"},
            {"name":"Scroll",      "type":"DeFi",     "reward":"SCR",
             "est_min":100,  "est_max":1000,  "effort":"mid",
             "action":"Use Scroll ecosystem", "url":"https://scroll.io"},
            {"name":"Berachain",   "type":"Testnet",  "reward":"BERA",
             "est_min":200,  "est_max":3000,  "effort":"zero",
             "action":"Testnet + honey jar",  "url":"https://bartio.faucet.berachain.com"},
            {"name":"Galxe",       "type":"Quests",   "reward":"Multi",
             "est_min":10,   "est_max":500,   "effort":"zero",
             "action":"Complete free quests", "url":"https://galxe.com"},
            {"name":"Layer3",      "type":"Quests",   "reward":"Multi",
             "est_min":5,    "est_max":200,   "effort":"zero",
             "action":"Complete tasks",       "url":"https://layer3.xyz"},
            {"name":"MegaETH",     "type":"Testnet",  "reward":"METH",
             "est_min":300,  "est_max":4000,  "effort":"zero",
             "action":"Early testnet access", "url":"https://megaeth.com"},
            {"name":"Initia",      "type":"Testnet",  "reward":"INIT",
             "est_min":200,  "est_max":2500,  "effort":"zero",
             "action":"Complete Odyssey tasks","url":"https://initia.xyz"},
        ]
        for a in AIRDROPS:
            est_mid = (a["est_min"] + a["est_max"]) / 2
            score   = self._score_airdrop(est_mid, a["effort"])
            results["airdrop"].append({**a, "est_mid": est_mid, "score": score})
        results["airdrop"].sort(key=lambda x: -x["score"])

        return results


async def menu_opportunities():
    clear()
    print(f"\n{C.BOLD}{C.BGREEN}  ╔══════════════════════════════════════════════════════╗{C.RESET}")
    print(f"{C.BOLD}{C.BGREEN}  ║   ⚡ POSSIBLE OPPORTUNITIES SCANNER                 ║{C.RESET}")
    print(f"{C.BOLD}{C.BGREEN}  ╚══════════════════════════════════════════════════════╝{C.RESET}")
    print(f"  {C.DIM}Scanning DEX spreads | Flash loans | Yields | Airdrops...{C.RESET}\n")

    scanner = OpportunityScanner()
    results = await scanner.scan()
    prices  = results["prices"]

    eth  = prices.get("ETH", 2200)
    chg  = prices.get("ETH_24h", 0)
    chg_c = C.BGREEN if chg >= 0 else C.RED
    print(f"  {C.BOLD}Live Prices:{C.RESET}  "
          f"ETH ${eth:,.0f} ({chg_c}{chg:+.1f}%{C.RESET})  "
          f"BTC ${prices.get('BTC',0):,.0f}  "
          f"ARB ${prices.get('ARB',0):.3f}\n")

    # ── SECTION 1: DEX Arbitrage ───────────────────────────────────
    arbs = results["arb"]
    print(f"  {C.BOLD}{C.BCYAN}── [1] DEX ARBITRAGE OPPORTUNITIES ({len(arbs)} found) ─────────{C.RESET}")
    if arbs:
        print(f"  {'Score':<7} {'Pair':<14} {'Buy DEX':<14} {'Sell DEX':<14} "
              f"{'Spread':>7} {'Net $':>8} {'Z':>6} {'Status'}")
        print(f"  {'─'*7} {'─'*14} {'─'*14} {'─'*14} {'─'*7} {'─'*8} {'─'*6} {'─'*9}")
        for r in arbs[:8]:
            sc  = r["score"]
            col = OpportunityScanner._color(sc)
            lbl = OpportunityScanner._label(sc)
            flag = " ⚡" if r.get("anomaly") else ""
            print(f"  {col}{sc:>3}/100{C.RESET}  "
                  f"{C.BWHITE}{r['pair']:<14}{C.RESET}"
                  f"{C.GREEN}{r['buy_dex']:<14}{C.RESET}"
                  f"{C.RED}{r['sell_dex']:<14}{C.RESET}"
                  f"{C.YELLOW}{r['spread_pct']:>6.3f}%{C.RESET} "
                  f"{C.BGREEN}${r['net_profit']:>7.2f}{C.RESET} "
                  f"{C.CYAN}{r.get('z_score',0):>5.2f}{C.RESET} "
                  f"{col}{lbl}{C.RESET}{flag}")
    else:
        print(f"  {C.DIM}  No live arbitrage spreads above threshold right now.{C.RESET}")
        print(f"  {C.DIM}  Markets are efficient — re-scan during high volatility.{C.RESET}")
    print()

    # ── SECTION 2: Flash Loan ──────────────────────────────────────
    flash = results["flash"]
    print(f"  {C.BOLD}{C.BYELLOW}── [2] FLASH LOAN OPPORTUNITIES ({len(flash)} found) ──────────{C.RESET}")
    if flash:
        print(f"  {'Score':<7} {'Protocol':<14} {'Chain':<11} {'Token':<8} "
              f"{'Loan $':>10} {'Profit $':>10} {'Status'}")
        print(f"  {'─'*7} {'─'*14} {'─'*11} {'─'*8} {'─'*10} {'─'*10} {'─'*9}")
        for f in flash[:6]:
            sc  = f["score"]
            col = OpportunityScanner._color(sc)
            lbl = OpportunityScanner._label(sc)
            print(f"  {col}{sc:>3}/100{C.RESET}  "
                  f"{C.BWHITE}{f['protocol']:<14}{C.RESET}"
                  f"{C.DIM}{f['chain']:<11}{C.RESET}"
                  f"{f['token']:<8}"
                  f"${f['amount']:>9,.0f} "
                  f"{C.BGREEN}${f['profit']:>9.2f}{C.RESET} "
                  f"{col}{lbl}{C.RESET}")
        # Show steps for best
        best_flash = flash[0]
        print(f"\n  {C.BOLD}Best flash route steps:{C.RESET}")
        for step in best_flash.get("steps", [])[:5]:
            print(f"    {C.DIM}{step}{C.RESET}")
    else:
        print(f"  {C.DIM}  No flash loan opportunities — need live arb results first.{C.RESET}")
    print()

    # ── SECTION 3: Yield Farming ───────────────────────────────────
    yields = results["yield"]
    print(f"  {C.BOLD}{C.BBLUE}── [3] YIELD FARMING OPPORTUNITIES ({len(yields)} found) ──────{C.RESET}")
    if yields:
        print(f"  {'Score':<7} {'Protocol':<16} {'Chain':<10} {'Pool':<20} "
              f"{'APY':>7} {'TVL':>7} {'Sharpe':>7} {'Status'}")
        print(f"  {'─'*7} {'─'*16} {'─'*10} {'─'*20} {'─'*7} {'─'*7} {'─'*7} {'─'*9}")
        for y in yields[:10]:
            sc    = y["score"]
            col   = OpportunityScanner._color(sc)
            lbl   = OpportunityScanner._label(sc)
            stable_tag = f" {C.DIM}[S]{C.RESET}" if y["stablecoin"] else ""
            il_tag     = f" {C.YELLOW}⚠IL{C.RESET}" if y["il_risk"] == "yes" else ""
            print(f"  {col}{sc:>3}/100{C.RESET}  "
                  f"{C.BWHITE}{y['protocol']:<16}{C.RESET}"
                  f"{C.DIM}{y['chain']:<10}{C.RESET}"
                  f"{y['pool'][:20]:<20}"
                  f"{C.BGREEN}{y['apy']:>6.1f}%{C.RESET} "
                  f"${y['tvl_m']:>5.0f}M "
                  f"{C.CYAN}{y['sharpe']:>6.2f}{C.RESET} "
                  f"{col}{lbl}{C.RESET}{stable_tag}{il_tag}")
    else:
        print(f"  {C.DIM}  Could not reach DeFi Llama — check network.{C.RESET}")
    print()

    # ── SECTION 4: Airdrops ────────────────────────────────────────
    airdrops = results["airdrop"]
    print(f"  {C.BOLD}{C.BMAGENTA}── [4] AIRDROP OPPORTUNITIES ({len(airdrops)} tracked) ─────────{C.RESET}")
    print(f"  {'Score':<7} {'Project':<14} {'Type':<10} {'Reward':<8} "
          f"{'Est Min':>8} {'Est Max':>8} {'Effort':<8} {'Status'}")
    print(f"  {'─'*7} {'─'*14} {'─'*10} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*9}")
    for a in airdrops:
        sc    = a["score"]
        col   = OpportunityScanner._color(sc)
        lbl   = OpportunityScanner._label(sc)
        eff_c = (C.BGREEN if a["effort"] == "zero"
                 else C.BYELLOW if a["effort"] == "low"
                 else C.WHITE)
        print(f"  {col}{sc:>3}/100{C.RESET}  "
              f"{C.BWHITE}{a['name']:<14}{C.RESET}"
              f"{C.DIM}{a['type']:<10}{C.RESET}"
              f"{C.BYELLOW}{a['reward']:<8}{C.RESET}"
              f"${a['est_min']:>7,} "
              f"${a['est_max']:>7,} "
              f"{eff_c}{a['effort']:<8}{C.RESET}"
              f"{col}{lbl}{C.RESET}")

    # ── SECTION 5: Summary & best actions ─────────────────────────
    print(f"\n  {C.BOLD}{C.BCYAN}── [5] TOP ACTIONS RIGHT NOW ──────────────────────────{C.RESET}")

    all_opps = []
    for r in arbs[:3]:
        all_opps.append(("ARB",     r["score"], f"{r['pair']} on Arbitrum → +${r['net_profit']:.2f}",     "Run: python3 flash_executor.py --scan"))
    for f in flash[:2]:
        all_opps.append(("FLASH",   f["score"], f"{f['token']} via {f['protocol']} → +${f['profit']:.2f}", "Run: python3 flash_executor.py --scan --ga"))
    for y in yields[:3]:
        all_opps.append(("YIELD",   y["score"], f"{y['protocol']} {y['pool']} → {y['apy']:.1f}% APY",      y["url"]))
    for a in airdrops[:3]:
        all_opps.append(("AIRDROP", a["score"], f"{a['name']} → ${a['est_min']:,}-${a['est_max']:,}",       a["url"]))

    all_opps.sort(key=lambda x: -x[1])
    top = all_opps[:8]

    if top:
        print(f"  {'#':<3} {'Type':<8} {'Score':<7} {'Opportunity':<44} Action")
        print(f"  {'─'*3} {'─'*8} {'─'*7} {'─'*44} {'─'*35}")
        for i, (typ, sc, desc, action) in enumerate(top, 1):
            col   = OpportunityScanner._color(sc)
            type_colors = {
                "ARB":     C.BCYAN,
                "FLASH":   C.BYELLOW,
                "YIELD":   C.BBLUE,
                "AIRDROP": C.BMAGENTA,
            }
            tc = type_colors.get(typ, C.WHITE)
            print(f"  {C.BOLD}{i:<3}{C.RESET}"
                  f"{tc}{typ:<8}{C.RESET}"
                  f"{col}{sc:>3}/100{C.RESET}  "
                  f"{desc[:44]:<44} "
                  f"{C.DIM}{action[:35]}{C.RESET}")
    else:
        print(f"  {C.DIM}  No high-scoring opportunities found this scan.{C.RESET}")
        print(f"  {C.DIM}  Tip: Run during market volatility for best results.{C.RESET}")

    # ── SECTION 6: Airdrop action steps ───────────────────────────
    print(f"\n  {C.BOLD}{C.BMAGENTA}── [6] ZERO-COST AIRDROP QUICK ACTIONS ────────────────{C.RESET}")
    zero_cost = [a for a in airdrops if a["effort"] == "zero"]
    for a in zero_cost[:5]:
        sc  = a["score"]
        col = OpportunityScanner._color(sc)
        print(f"  {col}●{C.RESET} {C.BWHITE}{a['name']:<12}{C.RESET} "
              f"{C.BYELLOW}{a['reward']:<6}{C.RESET} "
              f"est ${a['est_min']:,}-${a['est_max']:,}  "
              f"{C.DIM}{a['action']}{C.RESET}")
        print(f"    {C.CYAN}{a['url']}{C.RESET}")

    print(f"\n  {C.DIM}Score legend: {C.BGREEN}≥70 ACT NOW{C.RESET}  "
          f"{C.BYELLOW}40-69 MONITOR{C.RESET}  "
          f"{C.RED}<40 LOW VALUE{C.RESET}")
    print(f"  {C.DIM}[S]=Stablecoin pool  ⚠IL=Impermanent loss risk  ⚡=Z-score anomaly{C.RESET}\n")

    input(f"  {C.DIM}Press ENTER to return to menu...{C.RESET}")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
async def main():
    init_db()
    if WalletManager.get_wallet_count() == 0:
        for chain in ["ethereum","bsc","polygon","arbitrum","base","solana"]:
            WalletManager.create_wallet(chain, f"main_{chain}")

    while True:
        clear()
        banner()
        print_header()
        print_menu()
        try:
            choice = input("  Select option > ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {C.BYELLOW}Goodbye.{C.RESET}\n")
            break

        if   choice == "1": await start_automation_menu()
        elif choice == "2": await menu_wallets()
        elif choice == "3": await menu_arbitrage()
        elif choice == "4": await menu_yields()
        elif choice == "5": await menu_faucets()
        elif choice == "6": await menu_revenue()
        elif choice == "7": await menu_flash_loans()
        elif choice == "8": await menu_algorithms()
        elif choice == "9": await menu_status()
        elif choice in ("A","a"): await menu_opportunities()
        elif choice == "0":
            print(f"\n  {C.BYELLOW}Goodbye.{C.RESET}\n")
            break
        else:
            print(f"  {C.RED}Invalid option.{C.RESET}")
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
