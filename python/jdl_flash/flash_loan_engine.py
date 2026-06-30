#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║         FLASH LOAN AUTOMATION ENGINE v1.0                     ║
║         Zero-Gas · Self-Funding · Auto-Reinvest               ║
║         Terminal-Ready | UserLAnd Compatible                  ║
╚═══════════════════════════════════════════════════════════════╝
"""

import asyncio
import json
import os
import sys
import time
import math
import hashlib
import sqlite3
import logging
import random
import statistics
import traceback
import urllib.request
from collections import deque
from datetime   import datetime, timezone
from pathlib    import Path
from typing     import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from dotenv     import load_dotenv

try:
    import requests
except ImportError:
    requests = None

try:
    from web3 import Web3
    from web3.middleware import geth_poa_middleware
    WEB3_OK = True
    # ─ web3 v5/v6 compatibility shims ──────────────────────────────────
    def _w3_cs(addr: str) -> str:
        try: return Web3.to_checksum_address(addr)
        except AttributeError: return Web3.toChecksumAddress(addr)
    def _gas_p(w3) -> float:
        try: return w3.eth.gas_price
        except AttributeError: return w3.eth.gasPrice
    def _nonce(w3, addr) -> int:
        try: return w3.eth.get_transaction_count(addr)
        except AttributeError: return w3.eth.getTransactionCount(addr)
    def _blk(w3) -> int:
        try: return w3.eth.block_number
        except AttributeError: return w3.eth.blockNumber
    def _chain_id(w3) -> int:
        try: return w3.eth.chain_id
        except AttributeError: return w3.eth.chainId
    def _balance(w3, addr) -> int:
        try: return w3.eth.get_balance(addr)
        except AttributeError: return w3.eth.getBalance(addr)
    def _is_connected(w3) -> bool:
        try: return bool(w3.is_connected())
        except AttributeError:
            try: return bool(w3.isConnected())
            except Exception: return False
    def _inject_poa(w3):
        try: w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        except Exception:
            try:
                from web3.middleware import ExtraDataToPOAMiddleware
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            except Exception: pass
    def _send_raw(w3, raw):
        try: return w3.eth.send_raw_transaction(raw)
        except AttributeError: return w3.eth.sendRawTransaction(raw)
    def _est_gas(w3, tx):
        try: return w3.eth.estimate_gas(tx)
        except AttributeError: return w3.eth.estimateGas(tx)
except ImportError:
    WEB3_OK = False
    def _w3_cs(a): return a
    def _gas_p(w): return 0.1
    def _nonce(w, a): return 0
    def _blk(w): return 0
    def _chain_id(w): return 0
    def _balance(w, a): return 0
    def _is_connected(w): return False
    def _inject_poa(w): pass
    def _send_raw(w, raw): return None
    def _est_gas(w, tx): return 1_200_000

load_dotenv(os.path.expanduser('~/jdl/.env'))

def _env(*names, default=''):
    """Return the first non-empty env var from the given aliases."""
    for n in names:
        v = os.getenv(n, '')
        if v and v.strip():
            return v.strip()
    return default

# ─ Credentials (accept multiple naming conventions so any .env wires up) ─
PRIV_KEY    = _env('PRIVATE_KEY', 'WALLET_PRIVATE_KEY')
WALLET      = _env('WALLET_ADDRESS', 'WALLET')
ALCH_ARB    = _env('ALCHEMY_ARB_KEY', 'ALCHEMY_ARBITRUM_KEY')
ALCH_ETH    = _env('ALCHEMY_ETH_KEY', 'ALCHEMY_ETHEREUM_KEY')
FB_SECRET   = _env('FLASHBOTS_SECRET', 'FLASHBOTS_AUTH_KEY', 'FLASHBOTS_SIGNER_KEY')
CONTRACT    = _env('FLASH_CONTRACT_ADDRESS', 'CONTRACT_ADDRESS')
GELATO_KEY  = _env('GELATO_API_KEY')
BICONOMY_K  = _env('BICONOMY_API_KEY')
PAYMASTER   = _env('PAYMASTER_ADDRESS')
CHAIN_ID    = int(_env('CHAIN_ID', default='42161') or '42161')

# ─ RPC resolution: build a PRIORITISED list with automatic failover ─
# Order: your RPC_URL → your Alchemy key → any RPC_FALLBACKS → public node (last resort).
# get_w3() tries each in turn and uses the first that actually connects, so one
# provider rate-limiting or going down never takes the engine offline.
def _valid_rpc(u: str) -> bool:
    return bool(u) and 'YOUR_ALCHEMY' not in u and 'YOUR_KEY' not in u and ' ' not in u.strip()

_RPC_URL    = _env('RPC_URL', 'ARBITRUM_RPC_URL', 'ARB_RPC_URL')
def _build_rpc_endpoints() -> list:
    """Collect every usable Arbitrum RPC from the environment, in priority order.

    Supports rich .env schemas:
      • ALCHEMY_ARB_KEY / ALCHEMY_ARBITRUM_KEY and ANY var named ALCHEMY_KEY_*
        (e.g. ALCHEMY_KEY_FLASHLOAN) → built into arb-mainnet Alchemy URLs.
      • RPC_URL, ARB_RPC_URL, ARBITRUM_RPC_URL.
      • Numbered RPC_URL1, RPC_URL2, … RPC_URLn (any count).
      • RPC_FALLBACKS (comma-separated).
      • Public Arbitrum node, always appended last.
    get_w3() then connects to the first that responds AND reports chainId==CHAIN_ID,
    so non-Arbitrum or dead endpoints in the pool are skipped automatically.
    """
    eps = []
    # 1) Alchemy keys — the dedicated ALCHEMY_ARB_KEY first, then every ALCHEMY_KEY_*.
    if ALCH_ARB:
        eps.append(f'https://arb-mainnet.g.alchemy.com/v2/{ALCH_ARB}')
    for name, val in os.environ.items():
        if name.startswith('ALCHEMY_KEY_') and val and val.strip():
            eps.append(f'https://arb-mainnet.g.alchemy.com/v2/{val.strip()}')
    # 2) Explicit URLs: RPC_URL/ARB_RPC_URL/ARBITRUM_RPC_URL, then numbered RPC_URLn.
    if _valid_rpc(_RPC_URL):
        eps.append(_RPC_URL.strip())
    _numbered = []
    for name, val in os.environ.items():
        if name.startswith('RPC_URL') and name != 'RPC_URL' and _valid_rpc(val):
            suffix = name[len('RPC_URL'):]
            _numbered.append((int(suffix) if suffix.isdigit() else 1e9, val.strip()))
    for _, u in sorted(_numbered):
        eps.append(u)
    # 3) RPC_FALLBACKS comma list.
    for u in (_env('RPC_FALLBACKS', default='') or '').split(','):
        if _valid_rpc(u):
            eps.append(u.strip())
    # 4) Public last resort.
    eps.append('https://arb1.arbitrum.io/rpc')
    seen, out = set(), []
    for u in eps:
        if u not in seen:
            seen.add(u); out.append(u)
    return out

RPC_ENDPOINTS = _build_rpc_endpoints()
RPC_ARB = RPC_ENDPOINTS[0]               # primary (display + first connection attempt)

_RPC_ETH_URL = _env('ETH_RPC_URL', 'ETHEREUM_RPC_URL')
if _RPC_ETH_URL and 'YOUR' not in _RPC_ETH_URL:
    RPC_ETH = _RPC_ETH_URL
elif ALCH_ETH:
    RPC_ETH = f'https://eth-mainnet.g.alchemy.com/v2/{ALCH_ETH}'
else:
    RPC_ETH = 'https://cloudflare-eth.com'

# ─ Derive wallet address from private key if not explicitly set ─
if not WALLET and PRIV_KEY and WEB3_OK:
    try:
        from eth_account import Account
        WALLET = Account.from_key(PRIV_KEY).address
    except Exception:
        pass

RPC_USING_KEY = bool((_RPC_URL and 'YOUR_ALCHEMY' not in _RPC_URL and 'YOUR_KEY' not in _RPC_URL) or ALCH_ARB)

FLASHBOTS_RELAY  = 'https://relay.flashbots.net'
GELATO_RELAY     = 'https://relay.gelato.digital/relays/v2/call-with-sync-fee'
MEV_SHARE_URL    = 'https://mev-share.flashbots.net'
MIN_PROFIT_USD   = float(_env('MIN_PROFIT_USD', default='0.50') or '0.50')
MAX_LOAN_USD     = float(_env('MAX_LOAN_USD', default='500000') or '500000')
WITHDRAW_THRESH  = 1_000.0
CYCLE_SEC        = 15

# Live execution gate: when off, the engine builds real calldata but never broadcasts.
LIVE_EXEC = _env('LIVE_EXECUTION', 'LIVE_EXEC', 'LIVE_MODE', default='').lower() in ('1','true','yes','on')

# eth_abi: v3+ exposes encode(); web3 v5 ships eth_abi v2 with encode_abi().
try:
    from eth_abi import encode as _abi_encode
except ImportError:
    try:
        from eth_abi import encode_abi as _abi_encode
    except ImportError:
        _abi_encode = None

# Pure-python ABI encoding for our struct paths. eth-abi 2.x (pinned by web3 5)
# is broken on Python 3.11+/parsimonious 0.11 for tuple types, so we never rely
# on its grammar; these helpers are byte-identical to a correct eth-abi encode.
_INIT_SELECTOR  = bytes.fromhex('e95437aa')  # initiateFlashLoan(address,uint256,bytes)
_QUOTE_SELECTOR = bytes.fromhex('c6a5026a')  # quoteExactInputSingle((address,address,uint256,uint24,uint160))
def _abi_w_uint(n) -> bytes: return int(n).to_bytes(32, 'big')
def _abi_w_addr(a) -> bytes: return bytes(12) + bytes.fromhex(_w3_cs(a)[2:])
def _abi_w_b32(b)  -> bytes: return b if len(b) == 32 else b.rjust(32, b'\x00')

ZERO_ADDR  = '0x0000000000000000000000000000000000000000'
# ArbitrageLib.SwapStep field order (MUST match contracts/ArbitrageLib.sol):
#   protocol, pool, tokenIn, tokenOut, fee, minAmountOut, curveIndexIn, curveIndexOut, balancerPoolId
_STEP_TUPLE = '(uint8,address,address,address,uint24,uint256,uint8,uint8,bytes32)'

# Use real Uniswap V3 QuoterV2 prices instead of the simulated spread model.
USE_REAL_QUOTES = _env('USE_REAL_QUOTES', 'REAL_QUOTES', default='').lower() in ('1','true','yes','on')
REAL_LOAN_USD   = float(_env('REAL_LOAN_USD', default='10000') or '10000')

# ── Real-data-only policy ───────────────────────────────────────────────────
# Simulated / sample / fabricated data (the OpportunityScanner spread model, fake
# tx hashes, mock profit) is FORBIDDEN on any mainnet. It is permitted ONLY on the
# Sepolia testnet (chainId 11155111), where there is no real money at stake.
SEPOLIA_CHAIN_ID = 11155111
IS_TESTNET  = CHAIN_ID == SEPOLIA_CHAIN_ID
ALLOW_SIM   = IS_TESTNET                 # simulated paths reachable only on Sepolia
# On mainnet, force live on-chain quotes regardless of the USE_REAL_QUOTES flag.
if not ALLOW_SIM:
    USE_REAL_QUOTES = True

# Maximum-revenue mode: when set, the daemon sizes each loan with LoanOptimizer
# (ternary search up to MAX_LOAN_USD) instead of the fixed REAL_LOAN_USD, to
# capture the largest real profit a pool will fill. Small-revenue mode is just a
# low MIN_PROFIT_USD + small REAL_LOAN_USD. Both use real quotes only.
MAXIMISE_REVENUE = _env('MAXIMISE_REVENUE', 'MAX_REVENUE', default='').lower() in ('1','true','yes','on')

# Uniswap V3 QuoterV2 (same canonical address on Arbitrum/Ethereum/Optimism/Polygon)
QUOTER_V2 = '0x61fFE014bA17989E743c5F6cB21bF9697530B21e'
# Native USDC on Arbitrum (Quoter pools use native USDC, not bridged USDC.e)
USDC_NATIVE = '0xaf88d065e77c8cC2239327C5EDb3A432268e5831'
WETH_ARB_T  = '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1'
_QUOTER_ABI = json.loads('[{"inputs":[{"components":[{"name":"tokenIn","type":"address"},{"name":"tokenOut","type":"address"},{"name":"amountIn","type":"uint256"},{"name":"fee","type":"uint24"},{"name":"sqrtPriceLimitX96","type":"uint160"}],"name":"params","type":"tuple"}],"name":"quoteExactInputSingle","outputs":[{"name":"amountOut","type":"uint256"},{"name":"sqrtPriceX96After","type":"uint160"},{"name":"initializedTicksCrossed","type":"uint32"},{"name":"gasEstimate","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]')

# ─────────────────────────────────────────────
#  DEPLOYED FLASH LOAN PROTOCOLS — ARBITRUM ONE
#  No custom contract needed — system uses these directly.
# ─────────────────────────────────────────────
PROTOCOLS: Dict[str, dict] = {
    'BALANCER_V2': {
        'address': '0xBA12222222228d8Ba445958a75a0704d566BF2C8',
        'fee_bps':  0,
        'type':    'balancer',
        'tokens':  ['USDC','WETH','WBTC','DAI','USDT'],
        'desc':    'Balancer V2 Vault  (0% fee)',
    },
    'AAVE_V3': {
        'address': '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
        'fee_bps':  9,
        'type':    'aave',
        'tokens':  ['USDC','WETH','WBTC','DAI','USDT','ARB'],
        'desc':    'Aave V3 Pool  (0.09% fee)',
    },
    'RADIANT': {
        'address': '0xF4B1486DD74D07706052A33d31d7c0AAFD0659E1',
        'fee_bps':  9,
        'type':    'aave',
        'tokens':  ['USDC','WETH','WBTC'],
        'desc':    'Radiant Capital  (0.09% fee, Aave-fork)',
    },
    'UNI_V3_USDC_WETH_005': {
        'address': '0xC6962004f452bE9203591991D15f6b388e09E8D0',
        'fee_bps':  5,
        'type':    'uniswap_v3',
        'tokens':  ['USDC','WETH'],
        'desc':    'Uniswap V3 USDC/WETH 0.05%',
    },
    'UNI_V3_WBTC_WETH_030': {
        'address': '0x2f5e87C9312fa29aed5c179E456625D79015299c',
        'fee_bps': 30,
        'type':    'uniswap_v3',
        'tokens':  ['WBTC','WETH'],
        'desc':    'Uniswap V3 WBTC/WETH 0.30%',
    },
    'UNI_V3_USDC_WETH_030': {
        'address': '0x17c14D2c404D167802b16C450d3c99F88F2c4F4d',
        'fee_bps': 30,
        'type':    'uniswap_v3',
        'tokens':  ['USDC','WETH'],
        'desc':    'Uniswap V3 USDC/WETH 0.30%',
    },
}

# token addresses on Arbitrum One
_TOKEN_ADDR = {
    'USDC': '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8',
    'WETH': '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
    'WBTC': '0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f',
    'DAI':  '0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1',
    'USDT': '0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9',
    'ARB':  '0x912CE59144191C1204E64559FE8253a0e49E6548',
}
_ERC20_ABI = json.loads('[{"inputs":[{"type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}]')
_TOKEN_DEC = {'USDC':6,'USDT':6,'WBTC':8,'WETH':18,'DAI':18,'ARB':18}

DATA_DIR = Path.home() / '.flash_loan_engine'
DB_PATH  = DATA_DIR / 'flash.db'

# ─────────────────────────────────────────────
#  SHARED WEB3 + LIVE CHAIN STATUS
# ─────────────────────────────────────────────
_W3_SINGLETON = None
ACTIVE_RPC    = RPC_ARB          # the endpoint actually serving requests
def get_w3():
    """Connect with automatic failover: try each RPC in RPC_ENDPOINTS and reuse
    the first that genuinely connects. One provider being down/rate-limited never
    takes the engine offline — it transparently falls through to the next."""
    global _W3_SINGLETON, ACTIVE_RPC
    if not WEB3_OK:
        return None
    if _W3_SINGLETON is not None:
        return _W3_SINGLETON
    for ep in RPC_ENDPOINTS:
        try:
            w3 = Web3(Web3.HTTPProvider(ep, request_kwargs={'timeout': 8}))
            _inject_poa(w3)
            if not _is_connected(w3):    # must actually answer, not just construct
                continue
            # Skip endpoints on the wrong chain (a mixed RPC pool may include other
            # networks): only accept one whose chainId matches CHAIN_ID.
            try:
                if _chain_id(w3) != CHAIN_ID:
                    continue
            except Exception:
                continue
            _W3_SINGLETON = w3
            ACTIVE_RPC = ep
            return w3
        except Exception:
            continue
    return None

def reset_w3():
    """Drop the cached connection so the next get_w3() re-runs failover. Call
    after repeated RPC errors to fail over to the next endpoint at runtime."""
    global _W3_SINGLETON
    _W3_SINGLETON = None

_CHAIN_CACHE = {'ts': 0.0, 'data': None}
def chain_status(force: bool = False) -> dict:
    """Live on-chain status. Cached 30s to avoid hammering the RPC on every redraw."""
    now = time.time()
    if not force and _CHAIN_CACHE['data'] and (now - _CHAIN_CACHE['ts'] < 30):
        return _CHAIN_CACHE['data']
    out = {'connected': False, 'chain_id': None, 'block': None,
           'gas_gwei': None, 'balance_eth': None, 'rpc': ACTIVE_RPC,
           'using_key': RPC_USING_KEY, 'error': None}
    if not WEB3_OK:
        out['error'] = 'web3 not installed'
    else:
        try:
            w3 = get_w3()
            if w3 is not None and _is_connected(w3):
                out['connected'] = True
                out['chain_id']  = _chain_id(w3)
                out['block']     = _blk(w3)
                out['gas_gwei']  = _gas_p(w3) / 1e9
                if WALLET:
                    try:
                        out['balance_eth'] = _balance(w3, _w3_cs(WALLET)) / 1e18
                    except Exception:
                        pass
            else:
                out['error'] = 'RPC not reachable'
        except Exception as e:
            out['error'] = str(e)[:60]
    _CHAIN_CACHE['ts'] = now
    _CHAIN_CACHE['data'] = out
    return out

# ─────────────────────────────────────────────
#  TERMINAL COLORS  (identical to jdl_engine.py)
# ─────────────────────────────────────────────
class C:
    RESET    = "\033[0m"
    BOLD     = "\033[1m"
    DIM      = "\033[2m"
    RED      = "\033[31m"
    GREEN    = "\033[32m"
    YELLOW   = "\033[33m"
    BLUE     = "\033[34m"
    MAGENTA  = "\033[35m"
    CYAN     = "\033[36m"
    WHITE    = "\033[37m"
    BGREEN   = "\033[92m"
    BYELLOW  = "\033[93m"
    BBLUE    = "\033[94m"
    BMAGENTA = "\033[95m"
    BCYAN    = "\033[96m"
    BWHITE   = "\033[97m"
    BG_BLACK = "\033[40m"
    BG_BLUE  = "\033[44m"
    BG_CYAN  = "\033[46m"
    BG_GREEN = "\033[42m"

def clear():
    os.system("clear" if os.name != "nt" else "cls")

def banner():
    print(f"""
{C.CYAN}{C.BOLD}
  ███████╗██╗      █████╗ ███████╗██╗  ██╗
  ██╔════╝██║     ██╔══██╗██╔════╝██║  ██║
  █████╗  ██║     ███████║███████╗███████║
  ██╔══╝  ██║     ██╔══██║╚════██╗██╔══██║
  ██║     ███████╗██║  ██║███████║██║  ██║
  ╚═╝     ╚══════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
{C.RESET}{C.BYELLOW}    Zero-Gas Flash Loan Arbitrage Engine v1.0
{C.DIM}    PEG · Flashbots · GARCH · Kalman · UCB1 · Q-Learn{C.RESET}
{C.CYAN}═══════════════════════════════════════════════════════{C.RESET}
""")

# ─────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────
def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS executions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           REAL,
            strategy     TEXT,
            gas_method   TEXT,
            asset        TEXT,
            loan_usd     REAL,
            profit_usd   REAL,
            gas_cost_usd REAL,
            net_usd      REAL,
            tx_hash      TEXT,
            success      INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS revenue_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            source    TEXT,
            amount_usd REAL,
            token     TEXT,
            chain     TEXT,
            tx_hash   TEXT,
            timestamp TEXT
        );
        CREATE TABLE IF NOT EXISTS opportunities (
            id           TEXT PRIMARY KEY,
            type         TEXT,
            chain        TEXT,
            protocol     TEXT,
            profit_usd   REAL,
            status       TEXT DEFAULT 'pending',
            details      TEXT,
            found_at     TEXT
        );
        CREATE TABLE IF NOT EXISTS pair_performance (
            pair          TEXT PRIMARY KEY,
            ema_weight    REAL DEFAULT 0.5,
            scan_count    INTEGER DEFAULT 0,
            opp_count     INTEGER DEFAULT 0,
            total_profit  REAL DEFAULT 0.0,
            last_seen     TEXT
        );
        CREATE TABLE IF NOT EXISTS ucb_state (
            id      INTEGER PRIMARY KEY,
            counts  TEXT,
            rewards TEXT,
            N       INTEGER
        );
        CREATE TABLE IF NOT EXISTS q_table (
            id   INTEGER PRIMARY KEY,
            data TEXT
        );
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    # Real-data-only: purge any legacy simulated/dry-run rows so revenue totals
    # only ever reflect real on-chain executions. (Sepolia testnet rows are kept.)
    con.execute("DELETE FROM executions WHERE tx_hash LIKE 'sim_%' OR tx_hash LIKE 'dry_%'")
    con.execute("DELETE FROM opportunities WHERE protocol = 'uni_v3+sushi'")  # old simulated scanner
    con.commit()
    con.close()

def db_exec(sql: str, params: tuple = ()):
    con = sqlite3.connect(DB_PATH)
    con.execute(sql, params)
    con.commit()
    con.close()

def db_query(sql: str, params: tuple = ()) -> list:
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(sql, params).fetchall()
    con.close()
    return rows

# ─────────────────────────────────────────────
#  MATHEMATICAL ALGORITHMS
# ─────────────────────────────────────────────

class EMAWeights:
    ALPHA = 0.15
    def __init__(self):  self._w: Dict[str,float] = {}
    def update(self, pair: str, found: bool) -> float:
        w = self._w.get(pair, 0.5)
        self._w[pair] = self.ALPHA*(1.0 if found else 0.0) + (1-self.ALPHA)*w
        return self._w[pair]
    def get(self, pair: str) -> float: return self._w.get(pair, 0.5)
    def ranked(self, pairs: list) -> list:
        return sorted(pairs, key=lambda p: self.get(str(p)), reverse=True)


class ZScoreDetector:
    WINDOW = 20; THRESHOLD = 2.0
    def __init__(self): self._h: Dict[str,deque] = {}
    def update(self, key: str, v: float) -> float:
        if key not in self._h: self._h[key] = deque(maxlen=self.WINDOW)
        self._h[key].append(v)
        h = list(self._h[key])
        if len(h) < 3: return 0.0
        mu = sum(h)/len(h)
        sd = (sum((x-mu)**2 for x in h)/len(h))**0.5
        return (v-mu)/sd if sd > 0 else 0.0
    def is_anomaly(self, key: str, v: float) -> bool:
        return abs(self.update(key, v)) > self.THRESHOLD


class GARCH11:
    def __init__(self, omega=1e-6, alpha=0.15, beta=0.80):
        self.omega = omega; self.alpha = alpha; self.beta = beta
        self.sigma2 = omega / max(1 - alpha - beta, 1e-9)
    def update(self, ret: float) -> float:
        self.sigma2 = max(self.omega + self.alpha*ret**2 + self.beta*self.sigma2, 1e-10)
        return math.sqrt(self.sigma2)
    def predict(self, h: int = 1) -> float:
        lr = self.omega / max(1 - self.alpha - self.beta, 1e-9)
        return math.sqrt(max(lr + (self.alpha+self.beta)**h*(self.sigma2-lr), 0))
    def high_vol(self, pct: float = 4.0) -> bool: return self.predict()*100 > pct


class KalmanPrice:
    def __init__(self, q=0.001, r=0.5):
        self.x=0.0; self.v=0.0; self.P=[[1,0],[0,1]]; self.Q=q; self.R=r; self._init=False
    def update(self, obs: float) -> float:
        if not self._init: self.x=obs; self._init=True; return obs
        xp = self.x+self.v
        Pp = [[self.P[0][0]+self.P[1][0]+self.Q, self.P[0][1]+self.P[1][1]],
              [self.P[1][0]+self.Q,              self.P[1][1]+self.Q]]
        S  = Pp[0][0]+self.R; K0=Pp[0][0]/S; K1=Pp[1][0]/S
        inn = obs-xp
        self.x = xp+K0*inn; self.v = self.v+K1*inn
        self.P = [[Pp[0][0]*(1-K0), Pp[0][1]*(1-K0)],
                  [Pp[1][0]-K1*Pp[0][0], Pp[1][1]-K1*Pp[0][1]]]
        return self.x
    @property
    def estimate(self): return self.x


class OrnsteinUhlenbeck:
    def __init__(self, theta=0.7, mu=0.0, sigma=0.02):
        self.theta=theta; self.mu=mu; self.sigma=sigma; self.X=mu; self._obs: List[float]=[]
    def update(self, x: float):
        self._obs.append(x)
        if len(self._obs) > 100: self._obs.pop(0)
        if len(self._obs) >= 20:
            self.mu = statistics.mean(self._obs)
            n = len(self._obs)
            cov = sum((self._obs[i]-self.mu)*(self._obs[i-1]-self.mu) for i in range(1,n))/(n-1)
            var = statistics.variance(self._obs[:-1])
            self.theta = max(0.01, -math.log(abs(cov)/max(var,1e-12)))
        self.X = x
    def half_life(self) -> float: return math.log(2)/max(self.theta,1e-6)
    def reversion_prob(self, spread: float, horizon: int = 60) -> float:
        decay = math.exp(-self.theta*horizon)
        rem   = abs(spread-self.mu)*decay
        denom = self.sigma*math.sqrt(max(1/(2*self.theta)*(1-math.exp(-2*self.theta*horizon)),1e-12))
        z = rem/max(denom, 1e-9)
        return max(0.0, 1.0 - z/4)


class KellyCriterion:
    REGIMES = {'BULL':1.2, 'NEUTRAL':1.0, 'BEAR':0.5}
    def fraction(self, win_p: float, win_loss: float, regime: str = 'NEUTRAL') -> float:
        p = min(max(win_p,0.01),0.99); b = max(win_loss,0.01); q = 1-p
        raw = max(0.0,(p*b-q)/b/2)
        return min(raw * self.REGIMES.get(regime,1.0), 0.20)


class NewtonRaphsonAMM:
    def __init__(self, iters=5): self.iters=iters
    def out(self, rx, ry, ain, fee_bps=30) -> float:
        af = ain*(1-fee_bps/10000); k=rx*ry
        dy = af*ry/(rx+af)
        for _ in range(self.iters):
            fx = (rx+af)*(ry-dy)-k; dy -= fx/(-(rx+af)); dy=max(0.0,min(dy,ry-1e-9))
        return dy
    def impact_pct(self, rx, ry, ain, fee_bps=30) -> float:
        spot=ry/max(rx,1e-12); ex=self.out(rx,ry,ain,fee_bps)/max(ain,1e-12)
        return abs(spot-ex)/spot*100


class BellmanFordArb:
    def find(self, prices: Dict[Tuple,float], tokens: List[str]) -> Optional[List[str]]:
        idx={t:i for i,t in enumerate(tokens)}; n=len(tokens)
        dist=[float('inf')]*n; pred=[-1]*n; dist[0]=0.0
        edges=[(idx[a],idx[b],-math.log(r)) for (a,b),r in prices.items() if r>0 and a in idx and b in idx]
        for _ in range(n-1):
            for u,v,w in edges:
                if dist[u]+w < dist[v]: dist[v]=dist[u]+w; pred[v]=u
        EPS = 1e-9
        for u,v,w in edges:
            if dist[u]!=float('inf') and dist[u]+w < dist[v]-EPS:
                path=[]; vis=set(); ci=v
                while ci not in vis: vis.add(ci); path.append(ci); ci=pred[ci]
                path.append(ci); path.reverse()
                return [tokens[i] for i in path]
        return None


class UCB1Bandit:
    def __init__(self, n: int):
        self.n=n; self.counts=[0]*n; self.rewards=[0.0]*n; self.N=0
    def choose(self) -> int:
        for i,c in enumerate(self.counts):
            if c==0: return i
        return max(range(self.n), key=lambda i:
            self.rewards[i]/self.counts[i] + math.sqrt(2*math.log(self.N)/self.counts[i]))
    def update(self, arm: int, r: float):
        self.counts[arm]+=1; self.rewards[arm]+=r; self.N+=1
    def best(self) -> int:
        return max(range(self.n), key=lambda i: self.rewards[i]/max(self.counts[i],1))
    def save(self):
        db_exec('INSERT OR REPLACE INTO ucb_state VALUES(1,?,?,?)',
                (json.dumps(self.counts), json.dumps(self.rewards), self.N))
    def load(self):
        row = db_query('SELECT counts,rewards,N FROM ucb_state WHERE id=1')
        if row: self.counts=json.loads(row[0][0]); self.rewards=json.loads(row[0][1]); self.N=row[0][2]


class QLearning:
    NS=8; NA=7
    def __init__(self, alpha=0.1, gamma=0.95, eps=0.2):
        self.a=alpha; self.g=gamma; self.eps=eps
        self.Q=[[0.0]*self.NA for _ in range(self.NS)]
        self.ls=0; self.la=0
    def encode(self, hv: bool, ws: bool, hg: bool) -> int: return int(hv)*4+int(ws)*2+int(hg)
    def choose(self, s: int) -> int:
        if random.random()<self.eps: return random.randint(0,self.NA-1)
        return self.Q[s].index(max(self.Q[s]))
    def update(self, r: float, ns: int):
        td = r + self.g*max(self.Q[ns]) - self.Q[self.ls][self.la]
        self.Q[self.ls][self.la] += self.a*td
        self.eps = max(0.02, self.eps*0.9995)
    def save(self):
        db_exec('INSERT OR REPLACE INTO q_table VALUES(1,?)',
                (json.dumps([v for row in self.Q for v in row]),))
    def load(self):
        row = db_query('SELECT data FROM q_table WHERE id=1')
        if row:
            flat=json.loads(row[0][0])
            for i in range(self.NS): self.Q[i]=flat[i*self.NA:(i+1)*self.NA]


class FourierCycle:
    def __init__(self): self.prices: List[float]=[]
    def add(self, p: float): self.prices.append(p); self.prices=self.prices[-256:]
    def period_s(self, rate=CYCLE_SEC) -> Optional[float]:
        n=len(self.prices)
        if n<32: return None
        mu=sum(self.prices)/n; x=[p-mu for p in self.prices]
        mags=[]
        for k in range(1,n//2):
            re=sum(x[t]*math.cos(2*math.pi*k*t/n) for t in range(n))
            im=sum(x[t]*math.sin(2*math.pi*k*t/n) for t in range(n))
            mags.append((re**2+im**2,k))
        if not mags: return None
        _,kd=max(mags)
        return (n/kd)*rate

# ─────────────────────────────────────────────
#  REVENUE TRACKER
# ─────────────────────────────────────────────
class RevenueTracker:
    @staticmethod
    def total() -> float:
        row = db_query('SELECT COALESCE(SUM(net_usd),0) FROM executions WHERE success=1')
        return float(row[0][0]) if row else 0.0
    @staticmethod
    def log(strategy, gas_m, asset, loan, profit, gas_cost, tx_hash, success=1):
        net = profit - gas_cost
        db_exec(
            'INSERT INTO executions(ts,strategy,gas_method,asset,loan_usd,profit_usd,gas_cost_usd,net_usd,tx_hash,success) '
            'VALUES(?,?,?,?,?,?,?,?,?,?)',
            (time.time(),strategy,gas_m,asset,loan,profit,gas_cost,net,tx_hash,success)
        )
        return net
    @staticmethod
    def history(limit=20) -> list:
        return db_query(
            'SELECT ts,strategy,gas_method,loan_usd,profit_usd,net_usd,tx_hash,success '
            'FROM executions ORDER BY ts DESC LIMIT ?', (limit,)
        )
    @staticmethod
    def count() -> int:
        row = db_query('SELECT COUNT(*) FROM executions WHERE success=1')
        return int(row[0][0]) if row else 0

# ─────────────────────────────────────────────
#  PRICE FEED
# ─────────────────────────────────────────────
class PriceFeed:
    WETH_USDC_POOL = '0xC6962004f452bE9203591991D15f6b388e09E8D0'
    def __init__(self):
        self.kf = KalmanPrice()
        # Real-data-only: 0.0 means "no real read yet" (never a fabricated price).
        # Display layers must check `online` and show "unavailable" rather than 0.
        self._eth: float = 0.0
        self._gas: float = 0.0
        self.online: bool = False
        # Always try to connect — public RPC works for reads even without a key.
        self._w3 = get_w3()

    def eth_price(self) -> float:
        """Real ETH/USDC from the live Uniswap V3 pool. 0.0 if no real read (offline)."""
        if self._w3:
            try:
                abi = '[{"inputs":[],"name":"slot0","outputs":[{"type":"uint160","name":"sqrtPriceX96"},{"type":"int24","name":"tick"},{"type":"uint16"},{"type":"uint16"},{"type":"uint16"},{"type":"uint8"},{"type":"bool"}],"stateMutability":"view","type":"function"}]'
                pool = self._w3.eth.contract(address=_w3_cs(self.WETH_USDC_POOL), abi=json.loads(abi))
                s0   = pool.functions.slot0().call()
                raw  = (s0[0]/(2**96))**2 * 1e12
                self._eth = self.kf.update(raw)
                self.online = True
            except Exception:
                pass
        return self._eth

    def gas_gwei(self) -> float:
        """Real gas price (gwei). 0.0 if no real read (offline)."""
        if self._w3:
            try:
                self._gas = _gas_p(self._w3)/1e9
                self.online = True
            except Exception: pass
        return self._gas

    def gas_usd(self, gas_units=500_000) -> float:
        eth = self.eth_price()
        if not eth:        # no real price → no fabricated gas cost
            return 0.0
        return self.gas_gwei() * gas_units * 1e-9 * eth

# ─────────────────────────────────────────────
#  PROTOCOL FINDER
#  Discovers available flash loan sources on-chain.
#  Works with no custom contract deployed.
# ─────────────────────────────────────────────
class ProtocolFinder:
    """Query Arbitrum One for available flash loan pools.
    No custom contract required — reads balances from already-deployed protocol addresses."""

    def __init__(self, w3=None):
        self._w3 = w3 if w3 is not None else get_w3()

    def _token_balance(self, token_sym: str, holder: str) -> float:
        """Returns token balance of holder in human-readable units."""
        if not (self._w3 and WEB3_OK):
            return -1.0
        addr = _TOKEN_ADDR.get(token_sym)
        if not addr:
            return -1.0
        try:
            c = self._w3.eth.contract(address=_w3_cs(addr), abi=_ERC20_ABI)
            raw = c.functions.balanceOf(_w3_cs(holder)).call()
            dec = _TOKEN_DEC.get(token_sym, 18)
            return raw / (10 ** dec)
        except Exception:
            return -1.0

    def _score(self, fee_bps: int, usdc_liq: float) -> float:
        fee_score = (100 - fee_bps) * 0.6
        liq_score = min(usdc_liq / 1_000_000, 10.0) * 40.0
        return fee_score + liq_score

    def discover(self, eth_price: float = 2000.0) -> List[dict]:
        """Query each protocol and return results ranked by score (lower fee + higher liquidity)."""
        results = []
        for name, cfg in PROTOCOLS.items():
            entry = {
                'name':         name,
                'desc':         cfg['desc'],
                'fee_bps':      cfg['fee_bps'],
                'type':         cfg['type'],
                'address':      cfg['address'],
                'tokens':       cfg['tokens'],
                'liquidity':    {},
                'available':    True,
                'live':         False,
            }
            usdc_liq = 0.0
            if self._w3 and WEB3_OK:
                for sym in cfg['tokens'][:4]:
                    bal = self._token_balance(sym, cfg['address'])
                    if bal >= 0:
                        entry['liquidity'][sym] = bal
                        entry['live'] = True
                usdc_liq = entry['liquidity'].get('USDC', 0.0)
                weth_liq = entry['liquidity'].get('WETH', 0.0)
                entry['available'] = usdc_liq > 500 or weth_liq > 0.1
            entry['score'] = self._score(cfg['fee_bps'], usdc_liq)
            results.append(entry)
        results.sort(key=lambda x: x['score'], reverse=True)
        return results

    def best(self, eth_price: float = 2000.0) -> dict:
        sources = self.discover(eth_price)
        return sources[0] if sources else PROTOCOLS['BALANCER_V2']

# ─────────────────────────────────────────────
#  OPPORTUNITY SCANNER
# ─────────────────────────────────────────────
GAS_STRATEGIES = [
    'FLASHBOTS_PEG', 'MORPHO_0FEE', 'BALANCER_0FEE',
    'GELATO_FREE',   'BICONOMY',    'RECURSIVE_FLASH', 'TWAP_LAG_ARB'
]

@dataclass
class Opportunity:
    type:        str
    asset:       str
    token_inter: str
    loan_usd:    float
    profit_usd:  float
    buy_fee:     int
    sell_fee:    int
    dex_type:    int
    vol:         float
    kelly_frac:  float
    spread:      float = 0.0
    source:      str   = 'BALANCER_V2'

class OpportunityScanner:
    def __init__(self, feed: PriceFeed):
        self.feed    = feed
        self.garch   = GARCH11()
        self.ou      = OrnsteinUhlenbeck()
        self.kelly   = KellyCriterion()
        self.nr      = NewtonRaphsonAMM()
        self.bf      = BellmanFordArb()
        self.fourier = FourierCycle()
        self.ema     = EMAWeights()
        self.zscore  = ZScoreDetector()
        self._last_eth = 0.0
        self._scan_n   = 0

    def scan(self) -> Optional[Opportunity]:
        eth = self.feed.eth_price()
        self.fourier.add(eth)
        self._scan_n += 1
        if self._last_eth > 0:
            ret = (eth - self._last_eth) / self._last_eth
            vol = self.garch.update(ret)
        else:
            vol = 0.01
        self._last_eth = eth
        if self.garch.high_vol(4.0):
            return None
        mispricing = 0.004 + random.gauss(0, 0.001)
        zscore_val = self.zscore.update('WETH_USDC', mispricing)
        if abs(zscore_val) < 0.5:
            return None
        loan_usdc = 100_000.0
        rx_uni    = 5_000_000.0; ry_uni = rx_uni / max(eth, 1)
        rx_su     = 4_980_000.0; ry_su  = rx_su  / max(eth*(1+mispricing), 1)
        out_weth  = self.nr.out(rx_uni, ry_uni, loan_usdc, 5)
        out_usdc  = self.nr.out(ry_su,  rx_su,  out_weth,  30)
        gross     = out_usdc - loan_usdc
        aave_fee  = loan_usdc * 0.0009
        profit    = gross - aave_fee
        if profit < MIN_PROFIT_USD:
            return None
        spread = profit / loan_usdc
        self.ou.update(spread)
        rev_p   = self.ou.reversion_prob(spread, 30)
        kf      = self.kelly.fraction(min(0.75, rev_p), profit/max(loan_usdc*0.001,0.01))
        sized   = min(loan_usdc * kf * 50, MAX_LOAN_USD)
        s_prof  = profit * sized / loan_usdc
        pair = 'USDC/WETH'
        self.ema.update(pair, True)
        db_exec("""
            INSERT INTO opportunities(id,type,chain,protocol,profit_usd,status,details,found_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (hashlib.md5(f'{time.time()}{pair}'.encode()).hexdigest(),
             'CROSS_DEX_SPOT','arbitrum','uni_v3+sushi',
             round(s_prof,4),'pending',json.dumps({'spread':spread,'vol':vol}),
             datetime.now().isoformat()))
        return Opportunity(
            type='CROSS_DEX_SPOT',
            asset='0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8',
            token_inter='0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
            loan_usd=sized, profit_usd=s_prof,
            buy_fee=500, sell_fee=3000, dex_type=1,
            vol=vol, kelly_frac=kf, spread=spread,
            source='BALANCER_V2'
        )

# ─────────────────────────────────────────────
#  REAL UNISWAP V3 QUOTER  (live on-chain prices)
# ─────────────────────────────────────────────
class UniV3Quoter:
    """Uniswap V3 QuoterV2.quoteExactInputSingle via a raw eth_call with hand-rolled
    calldata — avoids eth-abi tuple encoding (broken under web3 5 on Python 3.11+)."""
    def __init__(self, w3=None):
        self._w3 = w3 if w3 is not None else get_w3()
    def ready(self) -> bool:
        return self._w3 is not None and WEB3_OK
    def quote(self, token_in: str, token_out: str, amount_in: int, fee: int) -> Optional[int]:
        """Exact-input single-hop quote. Returns amountOut (base units) or None."""
        if not (self._w3 and WEB3_OK):
            return None
        try:
            data = '0x' + (_QUOTE_SELECTOR
                           + _abi_w_addr(token_in) + _abi_w_addr(token_out)
                           + _abi_w_uint(int(amount_in)) + _abi_w_uint(int(fee))
                           + _abi_w_uint(0)).hex()
            raw = self._w3.eth.call({'to': _w3_cs(QUOTER_V2), 'data': data})
            raw = bytes(raw)
            if not raw or len(raw) < 32:
                return None
            return int.from_bytes(raw[:32], 'big')   # amountOut is the first return word
        except Exception:
            return None

class RealQuoteScanner:
    """Real cross-fee-tier arbitrage scan via QuoterV2:
    USDC --(feeA)--> WETH --(feeB)--> USDC.  profit = out - loan - aave_premium.
    In efficient markets this is usually <= 0; that is the honest, correct result."""
    PAIRS = [(500,3000),(3000,500),(500,10000),(10000,500),(100,500),(500,100),(3000,10000),(10000,3000)]
    def __init__(self, feed: 'PriceFeed'):
        self.feed   = feed
        self.quoter = UniV3Quoter(feed._w3)
        self.kelly  = KellyCriterion()
    def ready(self) -> bool:
        return self.quoter.ready()
    def best_roundtrip(self, loan_usdc: float):
        """Return (profit_base, buy_fee, sell_fee, usdc_out, weth_mid) for the best
        fee-tier pair, or None if no pool answered. profit_base may be negative."""
        if not self.quoter.ready():
            return None
        amount_in = int(loan_usdc * 1e6)            # native USDC = 6 decimals
        aave_fee  = amount_in * 5 // 10000          # Aave V3 flash premium = 0.05%
        best = None
        for buy_fee, sell_fee in self.PAIRS:
            weth_out = self.quoter.quote(USDC_NATIVE, WETH_ARB_T, amount_in, buy_fee)
            if not weth_out:
                continue
            usdc_out = self.quoter.quote(WETH_ARB_T, USDC_NATIVE, weth_out, sell_fee)
            if not usdc_out:
                continue
            profit_base = usdc_out - amount_in - aave_fee
            if best is None or profit_base > best[0]:
                best = (profit_base, buy_fee, sell_fee, usdc_out, weth_out)
        return best
    def scan(self, loan_usdc: float = None) -> Optional[Opportunity]:
        loan_usdc = loan_usdc if loan_usdc is not None else REAL_LOAN_USD
        best = self.best_roundtrip(loan_usdc)
        if not best:
            return None
        profit_base, buy_fee, sell_fee, usdc_out, weth_mid = best
        profit_usd = profit_base / 1e6
        if profit_usd < MIN_PROFIT_USD:
            return None                              # no real edge (expected, efficient market)
        spread = profit_usd / max(loan_usdc, 1e-9)
        kf = self.kelly.fraction(0.60, max(profit_usd, 0.01) / max(loan_usdc*0.0005, 0.01))
        db_exec("""
            INSERT INTO opportunities(id,type,chain,protocol,profit_usd,status,details,found_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (hashlib.md5(f'{time.time()}rqs'.encode()).hexdigest(),
             'UNIV3_FEE_ARB','arbitrum','uni_v3_quoter',
             round(profit_usd,4),'pending',
             json.dumps({'buy_fee':buy_fee,'sell_fee':sell_fee,'usdc_out':usdc_out}),
             datetime.now().isoformat()))
        return Opportunity(
            type='UNIV3_FEE_ARB', asset=USDC_NATIVE, token_inter=WETH_ARB_T,
            loan_usd=loan_usdc, profit_usd=profit_usd,
            buy_fee=buy_fee, sell_fee=sell_fee, dex_type=0,
            vol=0.0, kelly_frac=kf, spread=spread, source='UNI_V3_QUOTER')

# ─────────────────────────────────────────────
#  ADVANCED MODULE INTEGRATION  (real quotes only)
#  Wires advanced_math / pattern_recognition / market_analysis / prediction /
#  loan_optimizer / triangular_scanner / bot_swarm / realness_guard into the
#  live engine. Every quote flows through the engine's real UniV3Quoter — no
#  simulated values. Degrades gracefully if a module file is absent.
# ─────────────────────────────────────────────
try:
    from jdl_flash.realness_guard      import RealnessGuard
    from jdl_flash.loan_optimizer      import LoanOptimizer
    from jdl_flash.triangular_scanner  import TriangularScanner
    from jdl_flash.pattern_recognition import PatternRecognition
    from jdl_flash.market_analysis     import MarketAnalysis
    from jdl_flash.prediction          import EWMAForecast, ConfidenceScorer
    ADV_MODULES_OK = True
except Exception as _adv_err:        # missing file / import error → engine still runs
    ADV_MODULES_OK = False
    _ADV_IMPORT_ERR = str(_adv_err)

# Real Arbitrum One token registry: symbol -> (checksummed address, decimals).
# Used by the triangular scanner; all addresses are mainnet (Arbitrum One).
ADV_TOKENS = {
    'USDC': (USDC_NATIVE,                                   6),   # native USDC
    'WETH': (WETH_ARB_T,                                    18),
    'USDT': ('0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9',  6),
    'WBTC': ('0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f',  8),
    'DAI':  ('0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1',  18),
    'ARB':  ('0x912CE59144191C1204E64559FE8253a0e49E6548',  18),
}

class AdvancedEngine:
    """Bridges the engine's live UniV3Quoter to the advanced modules.

    All sizing/scanning uses real on-chain QuoterV2 output. Profit results may
    be <= 0 in efficient markets — that is the honest result, reported as-is.
    """
    def __init__(self, feed: 'PriceFeed' = None):
        self.feed   = feed if feed is not None else PriceFeed()
        self.quoter = UniV3Quoter(getattr(self.feed, '_w3', None))
        self.guard  = RealnessGuard()        if ADV_MODULES_OK else None
        self.pat    = PatternRecognition()   if ADV_MODULES_OK else None
        self.mkt    = MarketAnalysis()       if ADV_MODULES_OK else None

    def ready(self) -> bool:
        return ADV_MODULES_OK and self.quoter.ready()

    def _quote_fn(self, token_in: str, token_out: str, amount_in: int, fee: int):
        """Real quote adapter with realness validation. Returns int|None."""
        out = self.quoter.quote(token_in, token_out, int(amount_in), int(fee))
        if out is None:
            return None
        # Validate the output is a real, finite, positive number. We do NOT use
        # validate_quote's ratio check here: token_in/token_out can have different
        # decimals (e.g. USDC 6dp → WETH 18dp), so base-unit ratios are legitimately
        # huge and a ratio bound would reject every valid cross-decimal quote.
        if self.guard is not None and not (self.guard.assert_real(out) and out > 0):
            return None
        return int(out)

    def optimal_loan_size(self, lo_usd: float, hi_usd: float,
                          buy_fee: int = 500, sell_fee: int = 3000) -> dict:
        """Find the USDC loan size in [lo,hi] maximising real net profit
        (USDC→WETH→USDC) via ternary search against live quotes. Maximises
        profit up to the highest size the pool still fills."""
        if not self.ready():
            return {'size': 0, 'net': 0, 'gross': 0}
        opt = LoanOptimizer(liquidity_fn=lambda t: int(hi_usd * 1e6),
                            quote_fn=self._quote_fn)
        lo = int(lo_usd * 1e6); hi = int(hi_usd * 1e6)
        res = opt.optimal_size(USDC_NATIVE, WETH_ARB_T, buy_fee, sell_fee, lo, hi, aave_bps=5)
        # Convert base units (6dp USDC) back to human USD for display.
        return {
            'size_usd': res['size'] / 1e6,
            'net_usd':  res['net']  / 1e6,
            'gross_usd':res['gross']/ 1e6,
            'raw': res,
        }

    def triangular_scan(self, start: str = 'USDC', amount_human: float = None) -> dict:
        """Real 3-hop A→B→C→A scan across ADV_TOKENS using live QuoterV2."""
        if not self.ready():
            return {'profitable': [], 'best': None, 'routes_probed': 0}
        amt = amount_human if amount_human is not None else REAL_LOAN_USD
        scanner = TriangularScanner(ADV_TOKENS, self._quote_fn)
        return scanner.scan(start, amt, list(ADV_TOKENS.keys()))

    def price_history(self, samples: int = 40, fee: int = 500) -> list:
        """Build a real WETH/USDC price series from repeated QuoterV2 reads
        (1 WETH → USDC). Used to feed pattern/market analytics."""
        series = []
        one_weth = int(1e18)
        for _ in range(max(2, samples)):
            out = self.quoter.quote(WETH_ARB_T, USDC_NATIVE, one_weth, fee)
            if out:
                series.append(out / 1e6)
        return series


# ─────────────────────────────────────────────
#  GAS SUBMITTER
# ─────────────────────────────────────────────
class FlashbotsPEG:
    def submit(self, opp: Opportunity, eth_price: float) -> Optional[str]:
        if not (PRIV_KEY and CONTRACT and WEB3_OK and requests):
            return None
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct
            w3 = get_w3() or Web3(Web3.HTTPProvider(RPC_ARB, request_kwargs={'timeout':10}))
            acc = Account.from_key(PRIV_KEY)
            profit_wei  = int(opp.profit_usd / eth_price * 1e18)
            builder_fee = int(profit_wei * 0.05)
            nonce = _nonce(w3, acc.address)
            tx = {
                'to': _w3_cs(CONTRACT),
                'data': build_initiate_calldata(opp) or '0x',
                'gas': 900_000, 'gasPrice': 0,
                'nonce': nonce, 'chainId': CHAIN_ID, 'value': 0
            }
            signed  = acc.sign_transaction(tx)
            raw_tx  = getattr(signed, 'raw_transaction', None) or getattr(signed, 'rawTransaction', None)
            raw     = raw_tx.hex() if raw_tx else '0x'
            fb_acc  = Account.from_key(FB_SECRET) if FB_SECRET else acc
            target  = _blk(w3) + 1
            bundle  = {'jsonrpc':'2.0','id':1,'method':'eth_sendBundle',
                       'params':[{'txs':[raw],'blockNumber':hex(target)}]}
            body    = json.dumps(bundle)
            msg     = encode_defunct(text='0x'+hashlib.sha256(body.encode()).hexdigest())
            sig     = fb_acc.sign_message(msg).signature.hex()
            hdr     = {'X-Flashbots-Signature':f'{fb_acc.address}:{sig}','Content-Type':'application/json'}
            resp    = requests.post(FLASHBOTS_RELAY, data=body, headers=hdr, timeout=10)
            return resp.json().get('result',{}).get('bundleHash')
        except Exception as e:
            logging.warning(f'FlashbotsPEG: {e}')
            return None

class GelatoSubmitter:
    def submit(self, calldata: str = '0x') -> Optional[str]:
        if not (CONTRACT and requests): return None
        try:
            r = requests.post(GELATO_RELAY, json={
                'chainId':str(CHAIN_ID),'target':CONTRACT,
                'data':calldata,'feeToken':'0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE'
            }, timeout=10)
            return r.json().get('taskId')
        except Exception as e:
            logging.warning(f'Gelato: {e}'); return None

# ─────────────────────────────────────────────
#  NEXUS FLASH RECEIVER — calldata builder + executor
#  Targets contracts/NexusFlashReceiver.sol :: initiateFlashLoan
# ─────────────────────────────────────────────
def _selector(sig: str) -> bytes:
    return Web3.keccak(text=sig)[:4] if WEB3_OK else b'\x00\x00\x00\x00'

def build_swap_steps(opp: 'Opportunity') -> list:
    """Build an ArbitrageLib.SwapStep[] round-trip route from an opportunity:
    buy asset->intermediate, then sell intermediate->asset (both Uniswap V3)."""
    amount     = int(opp.loan_usd * 1e6)            # USDC has 6 decimals
    premium    = amount * 5 // 10000                # Aave V3 flash fee = 0.05%
    min_profit = int(max(MIN_PROFIT_USD, 0.0) * 1e6)
    floor_out  = amount + premium + min_profit      # final leg must clear loan+premium+profit
    z = b'\x00' * 32
    return [
        # protocol=0 (UniV3); pool unused for UniV3 (router fixed in contract)
        (0, ZERO_ADDR, _w3_cs(opp.asset),       _w3_cs(opp.token_inter), int(opp.buy_fee),  0,         0, 0, z),
        (0, ZERO_ADDR, _w3_cs(opp.token_inter), _w3_cs(opp.asset),       int(opp.sell_fee), floor_out, 0, 0, z),
    ]

def build_initiate_calldata(opp: 'Opportunity') -> Optional[str]:
    """ABI-encode NexusFlashReceiver.initiateFlashLoan(asset, amount, abi.encode(steps)).
    Hand-rolled (pure python) so it works under web3 5's eth-abi 2.x on Python 3.11+."""
    if not WEB3_OK:
        return None
    try:
        amount = int(opp.loan_usd * 1e6)
        steps  = build_swap_steps(opp)
        def _enc_step(st):
            p, pool, ti, to, fee, mo, ci, co, bp = st
            return (_abi_w_uint(p) + _abi_w_addr(pool) + _abi_w_addr(ti) + _abi_w_addr(to) +
                    _abi_w_uint(fee) + _abi_w_uint(mo) + _abi_w_uint(ci) + _abi_w_uint(co) + _abi_w_b32(bp))
        enc_steps = _abi_w_uint(0x20) + _abi_w_uint(len(steps)) + b''.join(_enc_step(s) for s in steps)
        enc_bytes = _abi_w_uint(len(enc_steps)) + enc_steps + b'\x00' * ((-len(enc_steps)) % 32)
        args = _abi_w_addr(opp.asset) + _abi_w_uint(amount) + _abi_w_uint(0x60) + enc_bytes
        return '0x' + (_INIT_SELECTOR + args).hex()
    except Exception as e:
        logging.warning(f'calldata: {e}')
        return None

class NexusExecutor:
    """Broadcasts a real flash-loan arbitrage tx to NexusFlashReceiver on Arbitrum.
    Atomic safety: an unprofitable encoded route reverts on-chain (only gas is lost)."""
    def send(self, opp: 'Opportunity') -> Optional[str]:
        if not (PRIV_KEY and CONTRACT and WEB3_OK and requests is not None):
            return None
        try:
            from eth_account import Account
            w3   = get_w3()
            acc  = Account.from_key(PRIV_KEY)
            data = build_initiate_calldata(opp)
            if not data:
                return None
            tx = {'to': _w3_cs(CONTRACT), 'from': acc.address, 'data': data,
                  'nonce': _nonce(w3, acc.address), 'chainId': CHAIN_ID, 'value': 0}
            try:
                tx['gas'] = int(_est_gas(w3, tx) * 1.25)
            except Exception:
                tx['gas'] = 1_200_000
            tx['gasPrice'] = _gas_p(w3)
            signed = acc.sign_transaction(tx)
            raw    = getattr(signed, 'raw_transaction', None) or getattr(signed, 'rawTransaction', None)
            h      = _send_raw(w3, raw)
            return h.hex() if hasattr(h, 'hex') else (str(h) if h else None)
        except Exception as e:
            logging.warning(f'NexusExecutor: {e}')
            return None

# ─────────────────────────────────────────────
#  AUTOMATION DAEMON
# ─────────────────────────────────────────────
class FlashDaemon:
    def __init__(self):
        self.feed    = PriceFeed()
        self.scanner = OpportunityScanner(self.feed)
        self.bandit  = UCB1Bandit(len(GAS_STRATEGIES))
        self.qlearn  = QLearning()
        self.peg     = FlashbotsPEG()
        self.gelato  = GelatoSubmitter()
        self.nexus   = NexusExecutor()
        self.rqs     = RealQuoteScanner(self.feed)
        self.finder  = ProtocolFinder(self.feed._w3)
        self.running = False
        self.cycle   = 0
        self.errors  = 0
        self.bandit.load()
        self.qlearn.load()

    def _optimal_loan(self) -> float:
        """Maximise-revenue sizing: largest real-profit loan via LoanOptimizer
        (ternary search REAL_LOAN_USD..MAX_LOAN_USD against live quotes). Falls
        back to REAL_LOAN_USD if the optimizer finds no positive-net size."""
        try:
            if ADV_MODULES_OK:
                r = AdvancedEngine(self.feed).optimal_loan_size(REAL_LOAN_USD, MAX_LOAN_USD)
                if r.get('size_usd', 0) > 0 and r.get('net_usd', 0) > 0:
                    return float(r['size_usd'])
        except Exception:
            pass
        return REAL_LOAN_USD

    async def cycle_run(self, verbose: bool = True):
        self.cycle += 1
        ts      = datetime.now().strftime('%H:%M:%S')
        eth_p   = self.feed.eth_price()
        gas_g   = self.feed.gas_gwei()
        gas_usd = self.feed.gas_usd()
        if self.rqs.ready():
            loan = self._optimal_loan() if MAXIMISE_REVENUE else REAL_LOAN_USD
            opp = self.rqs.scan(loan)              # real on-chain QuoterV2 prices only
        elif ALLOW_SIM:
            opp = self.scanner.scan()              # Sepolia testnet ONLY — simulated model
        else:
            if verbose:
                print(f"  {C.YELLOW}[{ts}] #{self.cycle:04d} no live RPC — refusing to "
                      f"fabricate data (real-data-only). Set RPC_URL/ALCHEMY_ARB_KEY.{C.RESET}")
            self.errors += 1
            reset_w3()                 # drop dead connection → next cycle re-runs failover
            self.feed = PriceFeed()    # rebuild feed against the next endpoint
            self.rqs  = RealQuoteScanner(self.feed)
            return
        if not opp:
            if verbose:
                print(f"  {C.DIM}[{ts}] #{self.cycle:04d} scanning — no edge detected  eth=${eth_p:,.0f}  gas={gas_g:.3f}gwei{C.RESET}")
            return
        print(f"  {C.BGREEN}[{ts}]{C.RESET} {C.BOLD}EDGE FOUND{C.RESET}"
              f"  profit={C.BYELLOW}${opp.profit_usd:.3f}{C.RESET}"
              f"  loan={C.BCYAN}${opp.loan_usd:,.0f}{C.RESET}"
              f"  vol={opp.vol*100:.2f}%"
              f"  kelly={opp.kelly_frac*100:.1f}%"
              f"  src={C.DIM}{opp.source}{C.RESET}")
        arm      = self.bandit.choose()
        strategy = GAS_STRATEGIES[arm]
        hv = self.scanner.garch.high_vol()
        ws = opp.profit_usd > 5.0
        hg = gas_g > 1.0
        state = self.qlearn.encode(hv, ws, hg)
        self.qlearn.ls = state; self.qlearn.la = arm
        print(f"  {C.DIM}    strategy={strategy}  arm={arm}{C.RESET}")
        tx_hash = None
        mode    = 'SCAN'
        try:
            if CONTRACT and WEB3_OK and LIVE_EXEC:
                if strategy == 'FLASHBOTS_PEG' and CHAIN_ID == 1:
                    tx_hash = self.peg.submit(opp, eth_p)
                else:
                    tx_hash = self.nexus.send(opp)           # real on-chain broadcast
                mode = 'LIVE' if tx_hash else 'FAILED'
            elif CONTRACT and WEB3_OK and not LIVE_EXEC:
                build_initiate_calldata(opp)                 # prove calldata path, never broadcast
                mode = 'DRY'
            else:
                mode = 'SCAN'                                # no contract — observe real edge only

            # Revenue is recorded ONLY for real on-chain executions. SIM/DRY/SCAN
            # are never written as revenue on mainnet (real-data-only). Sepolia
            # (ALLOW_SIM) may record testnet results for end-to-end testing.
            record = (mode == 'LIVE' and tx_hash) or (ALLOW_SIM and mode != 'FAILED')
            if record:
                net = RevenueTracker.log(
                    opp.type, strategy, opp.asset,
                    opp.loan_usd, opp.profit_usd, gas_usd,
                    tx_hash or 'sepolia_testnet', 1 if tx_hash else 0)
                reward = net if tx_hash else -gas_usd
                self.bandit.update(arm, reward)
                ns = self.qlearn.encode(self.scanner.garch.high_vol(), net>5.0, hg)
                self.qlearn.update(reward, ns)
                total = RevenueTracker.total(); pct = min(total/WITHDRAW_THRESH*100,100)
                bar = int(pct/5); pbar = f"[{'#'*bar}{'.'*(20-bar)}]"
                print(f"  {C.BGREEN}  ✓ {mode}{C.RESET}  hash={C.DIM}{(tx_hash or 'testnet')[:18]}...{C.RESET}  net={C.BYELLOW}${net:.3f}{C.RESET}")
                print(f"  {C.CYAN}  revenue {pbar} ${total:,.2f}/${WITHDRAW_THRESH:,.0f} ({pct:.1f}%){C.RESET}")
            elif mode == 'FAILED':
                self.errors += 1
                print(f"  {C.RED}  ✗ broadcast failed{C.RESET}  {strategy}")
            elif mode == 'DRY':
                print(f"  {C.BYELLOW}  • DRY-RUN{C.RESET} real edge · calldata built · not broadcast — "
                      f"{C.DIM}not recorded as revenue{C.RESET}")
            else:  # SCAN — real edge seen but no contract to execute it
                print(f"  {C.BYELLOW}  • REAL EDGE (observe-only){C.RESET} — set FLASH_CONTRACT_ADDRESS + "
                      f"LIVE_EXECUTION=1 to execute — {C.DIM}not recorded{C.RESET}")
        except Exception as e:
            logging.error(f'cycle: {e}')
            self.errors += 1
        finally:
            self.bandit.save()
            self.qlearn.save()

    async def start(self, interval: int = CYCLE_SEC, verbose: bool = True):
        self.running = True
        while self.running:
            try:
                await self.cycle_run(verbose)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logging.error(f'daemon: {e}\n{traceback.format_exc()}')
                self.errors += 1
            await asyncio.sleep(interval)

    def stop(self):
        self.running = False

# ─────────────────────────────────────────────
#  UI
# ─────────────────────────────────────────────
def _chain_line() -> str:
    cs = chain_status()
    if cs['connected']:
        bal = f"  bal={C.BGREEN}{cs['balance_eth']:.5f} ETH{C.RESET}" if cs.get('balance_eth') is not None else ""
        key = f"{C.BGREEN}keyed{C.RESET}" if cs['using_key'] else f"{C.YELLOW}public{C.RESET}"
        return (f"  {C.BGREEN}● ON-CHAIN{C.RESET}  chain={C.BCYAN}{cs['chain_id']}{C.RESET}"
                f"  block={C.CYAN}{cs['block']:,}{C.RESET}  gas={C.CYAN}{cs['gas_gwei']:.3f} gwei{C.RESET}"
                f"  rpc={key}{bal}")
    return (f"  {C.RED}● OFFLINE{C.RESET}  {C.DIM}{cs.get('error') or 'no RPC'}{C.RESET}"
            f"  — set RPC_URL or ALCHEMY_ARB_KEY in ~/jdl/.env")

def print_header():
    total  = RevenueTracker.total()
    execs  = RevenueTracker.count()
    opps   = db_query("SELECT COUNT(*) FROM opportunities")[0][0]
    pct    = min(total/WITHDRAW_THRESH*100,100)
    bar    = int(pct/5); pbar=f"{'#'*bar}{'.'*(20-bar)}"
    status = f"{C.BGREEN}RUNNING{C.RESET}" if CONTRACT else f"{C.BYELLOW}SCAN MODE{C.RESET}"
    print(f"""
{C.CYAN}╔══════════════════════════════════════════════════════╗
║{C.BWHITE}{C.BOLD}  FLASH LOAN ENGINE v1.0  ·  Zero-Gas Auto-Arb         {C.RESET}{C.CYAN}║
╠══════════════════════════════════════════════════════╣
║{C.RESET}  💰 Revenue: {C.BGREEN}${total:>10.4f}{C.RESET}  [{pbar}] {pct:.0f}%          {C.CYAN}║
║{C.RESET}  🔍 Opps: {C.BYELLOW}{opps:>5}{C.RESET}  ✓ Execs: {C.BGREEN}{execs:>5}{C.RESET}  Status: {status}  {C.CYAN}║
║{C.RESET}  📌 Contract: {C.DIM}{(CONTRACT or 'not set — scan mode only')[:40]:<40}{C.RESET}{C.CYAN}║
╚══════════════════════════════════════════════════════╝{C.RESET}""")
    print(_chain_line())

def print_menu():
    print(f"""
{C.BOLD}  MAIN MENU{C.RESET}
  {C.CYAN}[1]{C.RESET} Start Automation Engine
  {C.CYAN}[2]{C.RESET} Scan for Opportunities
  {C.CYAN}[3]{C.RESET} Gas Strategy Status
  {C.CYAN}[4]{C.RESET} Revenue Log
  {C.CYAN}[5]{C.RESET} Algorithm Dashboard
  {C.CYAN}[6]{C.RESET} System Status
  {C.CYAN}[7]{C.RESET} Configuration
  {C.CYAN}[8]{C.RESET} Run Tests
  {C.CYAN}[9]{C.RESET} Discover Flash Loan Protocols
  {C.CYAN}[c]{C.RESET} Test On-Chain Connection
  {C.CYAN}[x]{C.RESET} Build Execution Calldata (dry-run)
  {C.CYAN}[q]{C.RESET} Real Quote Scan (Uniswap V3)
  {C.CYAN}[o]{C.RESET} Optimal Loan Sizing (maximise profit, real)
  {C.CYAN}[t]{C.RESET} Triangular Scan (real 3-hop)
  {C.CYAN}[v]{C.RESET} Advanced Analytics (pattern/market/prediction)
  {C.CYAN}[0]{C.RESET} Exit
""")

async def menu_run_daemon():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── AUTOMATION ENGINE ───{C.RESET}\n")
    print(_chain_line())
    print()
    print(f"  {C.BYELLOW}Runs continuous scan + execute cycles with all algorithms.{C.RESET}")
    print(f"  Gas strategies rotate via UCB1 bandit learning.")
    print(f"  Profits auto-reinvest until ${WITHDRAW_THRESH:,.0f} threshold.")
    if not CONTRACT:
        print(f"  {C.YELLOW}No contract set — running in SCAN MODE (opportunities logged, not submitted).{C.RESET}")
    elif not LIVE_EXEC:
        print(f"  {C.YELLOW}LIVE_EXECUTION off — DRY-RUN: real calldata built, not broadcast.{C.RESET}")
    else:
        print(f"  {C.BGREEN}LIVE — broadcasting initiateFlashLoan txs to {CONTRACT[:12]}…{C.RESET}")
    if USE_REAL_QUOTES:
        print(f"  {C.BCYAN}Real Uniswap V3 quotes ON (loan ${REAL_LOAN_USD:,.0f}).{C.RESET}")
    print(f"  Press {C.BOLD}Ctrl+C{C.RESET} to stop.\n")
    try:
        raw = input(f"  Scan interval seconds [{CYCLE_SEC}]: ").strip()
        interval = int(raw) if raw.isdigit() else CYCLE_SEC
    except (ValueError, EOFError):
        interval = CYCLE_SEC
    daemon = FlashDaemon()
    print(f"\n  {C.BGREEN}Engine starting…{C.RESET}  interval={interval}s\n")
    try:
        await daemon.start(interval=interval)
    except KeyboardInterrupt:
        daemon.stop()
        print(f"\n  {C.BYELLOW}Engine stopped.  Cycles={daemon.cycle}  Errors={daemon.errors}{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

async def menu_scan_now():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── OPPORTUNITY SCANNER (real on-chain) ───{C.RESET}\n")
    feed = PriceFeed()
    rqs  = RealQuoteScanner(feed)
    print(_chain_line())
    if not rqs.ready():
        if ALLOW_SIM:
            print(f"\n  {C.YELLOW}Sepolia testnet: no live mainnet quoter — simulated scan.{C.RESET}")
        else:
            print(f"\n  {C.RED}No live RPC connection — cannot fetch real quotes.{C.RESET}")
            print(f"  {C.DIM}Set RPC_URL or ALCHEMY_ARB_KEY in ~/jdl/.env, then test with [c].{C.RESET}")
            print(f"  {C.DIM}Real-data-only: refusing to show fabricated opportunities.{C.RESET}")
            input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    eth_p = feed.eth_price(); gas_g = feed.gas_gwei()
    eth_s = f"${eth_p:,.2f}" if eth_p else f"{C.DIM}unavailable{C.RESET}"
    gas_s = f"{gas_g:.3f} gwei" if gas_g else f"{C.DIM}unavailable{C.RESET}"
    print(f"\n  ETH/USDC: {C.BYELLOW}{eth_s}{C.RESET}   Gas: {C.CYAN}{gas_s}{C.RESET}")
    print(f"  {C.DIM}Probing real USDC→WETH→USDC round-trips across fee tiers (loan ${REAL_LOAN_USD:,.0f})…{C.RESET}\n")
    best = rqs.best_roundtrip(REAL_LOAN_USD)
    if best:
        profit_base, buy_fee, sell_fee, usdc_out, _ = best
        profit_usd = profit_base / 1e6
        col = C.BGREEN if profit_usd >= MIN_PROFIT_USD else C.YELLOW
        print(f"  Best real round-trip: {col}${profit_usd:+.4f}{C.RESET} net  "
              f"(buy {buy_fee/10000:.2f}% → sell {sell_fee/10000:.2f}%)")
        if profit_usd < MIN_PROFIT_USD:
            print(f"  {C.DIM}Below MIN_PROFIT_USD (${MIN_PROFIT_USD}). No edge — efficient market (honest result).{C.RESET}")
    else:
        print(f"  {C.DIM}No pool answered — RPC may be rate-limiting. No fabricated result shown.{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

def menu_gas_strategies():
    clear()
    daemon = FlashDaemon()
    print(f"\n{C.BOLD}{C.CYAN}  ─── GAS STRATEGY STATUS ───{C.RESET}\n")
    print(f"  {'#':<4} {'Strategy':<22} {'Tries':>6} {'Avg $':>8}  {'UCB Score':>10}")
    print(f"  {'─'*4} {'─'*22} {'─'*6} {'─'*8}  {'─'*10}")
    N = max(daemon.bandit.N, 1)
    for i, name in enumerate(GAS_STRATEGIES):
        c = daemon.bandit.counts[i]; r = daemon.bandit.rewards[i]
        avg = r/max(c,1)
        ucb = avg + math.sqrt(2*math.log(N)/max(c,1))
        best = " <" if i == daemon.bandit.best() else ""
        col  = C.BGREEN if best else C.RESET
        print(f"  {C.CYAN}{i+1:<4}{C.RESET} {col}{name:<22}{C.RESET} {c:>6} {avg:>+8.3f}  {ucb:>10.4f}{best}")
    print(f"\n  {C.DIM}UCB1: score = mean_reward + √(2·ln(N)/n)  Best arm marked <{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

def menu_revenue():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── REVENUE LOG ───{C.RESET}\n")
    total = RevenueTracker.total()
    pct   = min(total/WITHDRAW_THRESH*100,100)
    bar   = int(pct/5); pbar=f"[{'#'*bar}{'.'*(20-bar)}]"
    print(f"  Total net profit: {C.BGREEN}${total:,.4f}{C.RESET}")
    print(f"  Threshold:        {C.CYAN}${WITHDRAW_THRESH:,.0f}{C.RESET}")
    print(f"  Progress:         {C.BYELLOW}{pbar} {pct:.1f}%{C.RESET}")
    if total >= WITHDRAW_THRESH:
        print(f"  {C.BGREEN}{C.BOLD}→ WITHDRAWAL READY! Call withdrawToken() on contract.{C.RESET}")
    print()
    rows = RevenueTracker.history(15)
    if rows:
        print(f"  {'Time':<10} {'Strategy':<22} {'Loan$':>10} {'Profit$':>8} {'Net$':>8}")
        print(f"  {'─'*10} {'─'*22} {'─'*10} {'─'*8} {'─'*8}")
        for r in rows:
            ts = datetime.fromtimestamp(r[0]).strftime('%H:%M:%S') if r[0] else '-'
            ok = f"{C.BGREEN}✓{C.RESET}" if r[7] else f"{C.RED}✗{C.RESET}"
            print(f"  {ts:<10} {r[1]:<22} {r[3]:>10,.0f} {r[4]:>+8.3f} {C.BYELLOW}{r[5]:>+8.3f}{C.RESET}  {ok}")
    else:
        print(f"  {C.DIM}No executions yet. Run the automation engine first.{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

def menu_algorithms():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── ALGORITHM DASHBOARD ───{C.RESET}\n")
    feed = PriceFeed(); eth = feed.eth_price(); gas = feed.gas_gwei()
    g = GARCH11(); g.update(0.002); g.update(-0.001); g.update(0.003)
    print(f"  {C.BOLD}Market State{C.RESET}")
    if feed.online:
        print(f"    ETH/USDC:   {C.BYELLOW}${eth:,.2f}{C.RESET}")
        print(f"    Gas:        {C.CYAN}{gas:.3f} gwei{C.RESET}")
        print(f"    Gas cost:   {C.DIM}${feed.gas_usd():.4f} USD (500k gas){C.RESET}")
    else:
        print(f"    ETH/USDC:   {C.DIM}unavailable (no live RPC){C.RESET}")
        print(f"    Gas:        {C.DIM}unavailable{C.RESET}")
    print()
    print(f"  {C.BOLD}GARCH(1,1){C.RESET}   σ²_t = ω + α·ε²_{{t-1}} + β·σ²_{{t-1}}")
    print(f"    ω={g.omega:.1e}  α={g.alpha}  β={g.beta}")
    print(f"    1-step vol: {C.CYAN}{g.predict(1)*100:.4f}%{C.RESET}  high_vol: {C.RED if g.high_vol() else C.BGREEN}{g.high_vol()}{C.RESET}")
    print()
    ou = OrnsteinUhlenbeck()
    [ou.update(0.004 + random.gauss(0,0.001)) for _ in range(30)]
    print(f"  {C.BOLD}Ornstein-Uhlenbeck{C.RESET}   θ={ou.theta:.4f}  μ={ou.mu:.6f}")
    print(f"    half-life: {C.CYAN}{ou.half_life():.1f}s{C.RESET}   rev_prob: {C.CYAN}{ou.reversion_prob(0.005,60)*100:.1f}%{C.RESET} (60s)")
    print()
    k = KellyCriterion()
    print(f"  {C.BOLD}Kelly Criterion{C.RESET}")
    print(f"    BULL {C.BGREEN}{k.fraction(0.65,2.5,'BULL')*100:.2f}%{C.RESET}   NEUTRAL {C.CYAN}{k.fraction(0.65,2.5,'NEUTRAL')*100:.2f}%{C.RESET}   BEAR {C.RED}{k.fraction(0.65,2.5,'BEAR')*100:.2f}%{C.RESET}")
    print()
    print(f"  {C.BOLD}Active:{C.RESET} GARCH ✓  Kalman ✓  OU ✓  UCB1 ✓  Q-Learn ✓  Newton-Raphson ✓")
    print(f"          Bellman-Ford ✓  Kelly ✓  Fourier ✓  EMA ✓  Z-Score ✓")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

def menu_status():
    clear()
    import platform
    print(f"\n{C.BOLD}{C.CYAN}  ─── SYSTEM STATUS ───{C.RESET}\n")
    print(f"  OS:       {platform.system()} {platform.release()}")
    print(f"  Python:   {sys.version.split()[0]}")
    print(f"  DB:       {DB_PATH}")
    total = RevenueTracker.total(); execs = RevenueTracker.count()
    opps  = db_query('SELECT COUNT(*) FROM opportunities')[0][0]
    print(f"  Revenue:  {C.BGREEN}${total:.4f}{C.RESET}   Execs: {C.BYELLOW}{execs}{C.RESET}   Opps: {C.CYAN}{opps}{C.RESET}")
    print()
    print(f"  {C.BOLD}On-Chain{C.RESET}")
    print(_chain_line())
    print()
    print(f"  {C.BOLD}Config{C.RESET}")
    print(f"    Wallet:    {C.CYAN}{(WALLET[:16]+'...' if WALLET else 'not set')}{C.RESET}")
    print(f"    Contract:  {C.CYAN}{(CONTRACT[:16]+'...' if CONTRACT else 'not set — scan mode')}{C.RESET}")
    print(f"    RPC:       {C.DIM}{RPC_ARB}{C.RESET}")
    print(f"    Flashbots: {C.BGREEN if FB_SECRET else C.DIM}{'configured' if FB_SECRET else 'not set'}{C.RESET}")
    print(f"    Web3:      {C.BGREEN if WEB3_OK else C.RED}{'OK (v5 Termux-compat)' if WEB3_OK else 'not installed'}{C.RESET}")
    print(f"    Execution: {(C.BGREEN+'LIVE (broadcasting)') if LIVE_EXEC else (C.YELLOW+'dry-run (set LIVE_EXECUTION=1)')}{C.RESET}")
    print(f"    Quotes:    {(C.BCYAN+'real Uniswap V3 QuoterV2') if USE_REAL_QUOTES else (C.YELLOW+'Sepolia testnet (sim allowed)')}{C.RESET}")
    print(f"    Network:   {(C.BCYAN+'Sepolia TESTNET (sim ok)') if IS_TESTNET else (C.BGREEN+'mainnet — REAL-DATA-ONLY')}{C.RESET}")
    print(f"    Sizing:    {(C.BCYAN+'maximise (LoanOptimizer)') if MAXIMISE_REVENUE else (C.DIM+f'fixed ${REAL_LOAN_USD:,.0f} (set MAXIMISE_REVENUE=1)')}{C.RESET}")
    print(f"    Min profit:{C.CYAN} ${MIN_PROFIT_USD}{C.RESET}  {C.DIM}(lower = small-revenue gathering){C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

def menu_config():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── CONFIGURATION ───{C.RESET}\n")
    env_path = os.path.expanduser('~/jdl/.env')
    print(f"  .env: {C.BGREEN if os.path.exists(env_path) else C.RED}{env_path}{C.RESET}")
    print(f"  RPC:  {C.DIM}{RPC_ARB}{C.RESET}\n")
    print(_chain_line())
    print()
    fields = [
        ('PRIVATE_KEY',           '***' if PRIV_KEY else '', 'Wallet private key (required to execute)'),
        ('WALLET_ADDRESS',        WALLET,      'Auto-derived from PRIVATE_KEY if blank'),
        ('RPC_URL / ALCHEMY_ARB_KEY', 'set' if RPC_USING_KEY else '', 'Arbitrum RPC (public fallback if blank)'),
        ('FLASHBOTS_SECRET',      '***' if FB_SECRET else '', 'Flashbots signing key (a.k.a. FLASHBOTS_AUTH_KEY)'),
        ('FLASH_CONTRACT_ADDRESS', CONTRACT,   'Deployed NexusFlashReceiver / FlashZeroGas (optional)'),
        ('PAYMASTER_ADDRESS',     PAYMASTER,   'ProfitPaymaster.sol (optional)'),
        ('GELATO_API_KEY',        '***' if GELATO_KEY else '', 'Gelato relay key (optional)'),
    ]
    print(f"  {'Variable':<28} {'Set?':<5} {'Description'}")
    print(f"  {'─'*28} {'─'*5} {'─'*34}")
    for var,val,desc in fields:
        ok = bool(val)
        sym = f"{C.BGREEN}YES{C.RESET}" if ok else f"{C.YELLOW}NO {C.RESET}"
        print(f"  {C.CYAN}{var:<28}{C.RESET} {sym}  {C.DIM}{desc}{C.RESET}")
    exec_line = f"{C.BGREEN}LIVE{C.RESET}" if LIVE_EXEC else f"{C.YELLOW}dry-run{C.RESET}"
    quote_line = f"{C.BCYAN}real{C.RESET}" if USE_REAL_QUOTES else f"{C.YELLOW}sepolia-sim{C.RESET}"
    net_line = f"{C.BCYAN}Sepolia testnet{C.RESET}" if IS_TESTNET else f"{C.BGREEN}mainnet (real-data-only){C.RESET}"
    print(f"\n  Network: {net_line}   LIVE_EXECUTION: {exec_line}   Quotes: {quote_line}   {C.DIM}(edit ~/jdl/.env){C.RESET}")
    print(f"  Edit: {C.CYAN}nano ~/jdl/.env{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

async def menu_tests():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── RUNNING TESTS ───{C.RESET}\n")
    try:
        from jdl_flash.test_flash_engine import run_all_tests
        await run_all_tests(verbose=True)
    except ImportError:
        print(f"  {C.RED}test_flash_engine.py not found in python/ directory.{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

async def menu_protocols():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── DISCOVER FLASH LOAN PROTOCOLS ───{C.RESET}\n")
    print(_chain_line())
    print(f"\n  {C.DIM}Querying Arbitrum One for deployed flash loan sources…{C.RESET}\n")
    feed   = PriceFeed()
    eth_p  = feed.eth_price()
    finder = ProtocolFinder(feed._w3)

    sources = finder.discover(eth_p)

    print(f"  {'#':<3} {'Protocol':<30} {'Fee':>5} {'USDC Liq':>12} {'WETH Liq':>10} {'Score':>7} {'Live'}")
    print(f"  {'─'*3} {'─'*30} {'─'*5} {'─'*12} {'─'*10} {'─'*7} {'─'*4}")
    for i, s in enumerate(sources, 1):
        usdc = s['liquidity'].get('USDC', 0.0)
        weth = s['liquidity'].get('WETH', 0.0)
        fee_str = f"{s['fee_bps']/100:.2f}%" if s['fee_bps'] > 0 else f"{C.BGREEN}FREE{C.RESET}"
        live_sym = f"{C.BGREEN}✓{C.RESET}" if s['live'] else f"{C.DIM}?{C.RESET}"
        avail_col = C.BGREEN if s['available'] else C.DIM
        usdc_str = f"${usdc:>10,.0f}" if usdc > 0 else f"{C.DIM}{'unknown':>10}{C.RESET}"
        weth_str = f"{weth:>9.2f}" if weth > 0 else f"{C.DIM}{'unknown':>9}{C.RESET}"
        print(f"  {C.CYAN}{i:<3}{C.RESET} {avail_col}{s['desc']:<30}{C.RESET} {fee_str:>5}  {usdc_str}  {weth_str}  {s['score']:>7.1f}  {live_sym}")

    print(f"\n  {C.BOLD}Best source:{C.RESET} {C.BGREEN}{sources[0]['desc']}{C.RESET}  "
          f"(fee {sources[0]['fee_bps']/100:.2f}%)")
    print(f"  Address: {C.CYAN}{sources[0]['address']}{C.RESET}")
    print(f"  Tokens:  {C.DIM}{', '.join(sources[0]['tokens'])}{C.RESET}")

    print(f"""
  {C.BOLD}How flash loans work without a funded wallet:{C.RESET}
  {C.DIM}1. Borrow from protocol (e.g. Balancer V2 at 0% fee){C.RESET}
  {C.DIM}2. Execute arbitrage swap — profit stays in the callback{C.RESET}
  {C.DIM}3. Repay loan + fee from profit — net gain kept{C.RESET}
  {C.DIM}4. Gas paid via Gelato relay (free) or embedded in profit (PEG){C.RESET}
  {C.BYELLOW}  → Set FLASH_CONTRACT_ADDRESS in .env to go live.{C.RESET}
  {C.BYELLOW}  → Until then, system runs in scan mode (logging opps).{C.RESET}
""")
    input(f"  {C.DIM}Press ENTER…{C.RESET}")

def menu_build_calldata():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── BUILD EXECUTION CALLDATA (DRY-RUN) ───{C.RESET}\n")
    print(_chain_line()); print()
    if not CONTRACT:
        print(f"  {C.YELLOW}FLASH_CONTRACT_ADDRESS not set — deploy NexusFlashReceiver and set it in ~/jdl/.env{C.RESET}")
        input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    if not WEB3_OK:
        print(f"  {C.RED}web3 unavailable — pip install -r python/requirements_flash.txt{C.RESET}")
        input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    feed = PriceFeed()
    if USE_REAL_QUOTES:
        rqs = RealQuoteScanner(feed); opp = rqs.scan(REAL_LOAN_USD) if rqs.ready() else None
        if not opp:
            print(f"  {C.YELLOW}No profitable real-quote edge right now (efficient market). Nothing to build.{C.RESET}")
            input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    else:
        scanner = OpportunityScanner(feed); opp = None
        for _ in range(30):
            opp = scanner.scan()
            if opp: break
        if not opp:
            print(f"  {C.YELLOW}No opportunity surfaced this pass — try again.{C.RESET}")
            input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    amount = int(opp.loan_usd*1e6); premium = amount*5//10000
    steps  = build_swap_steps(opp); data = build_initiate_calldata(opp)
    print(f"  Target contract : {C.CYAN}{CONTRACT}{C.RESET}")
    print(f"  Function        : {C.BWHITE}initiateFlashLoan(address,uint256,bytes){C.RESET}")
    print(f"  Source          : {C.DIM}{opp.source}{C.RESET}")
    print(f"  Asset (USDC)    : {C.CYAN}{opp.asset}{C.RESET}")
    print(f"  Loan amount     : {C.BYELLOW}{amount:,}{C.RESET} base units  ({C.DIM}${opp.loan_usd:,.0f}{C.RESET})")
    print(f"  Aave premium    : {premium:,}  {C.DIM}(0.05%){C.RESET}")
    print(f"\n  {C.BOLD}Route ({len(steps)} hops){C.RESET}")
    for i,st in enumerate(steps):
        print(f"    {i}: proto={st[0]}  {st[2][:8]}…→{st[3][:8]}…  fee={st[4]}  minOut={st[5]:,}")
    if data:
        print(f"\n  {C.BOLD}Encoded calldata{C.RESET} ({len(data)//2 - 1} bytes):")
        print(f"  {C.DIM}{data[:138]}…{C.RESET}")
    live = f"{C.BGREEN}ON{C.RESET}" if LIVE_EXEC else f"{C.YELLOW}OFF{C.RESET}"
    print(f"\n  LIVE_EXECUTION: {live}   {C.DIM}(set LIVE_EXECUTION=1 in ~/jdl/.env to broadcast){C.RESET}")
    print(f"  {C.DIM}Flash loans are atomic: an unprofitable route reverts on-chain (only gas lost).{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

def menu_real_quote():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── REAL QUOTE SCAN  (Uniswap V3 QuoterV2) ───{C.RESET}\n")
    print(_chain_line()); print()
    feed = PriceFeed(); rqs = RealQuoteScanner(feed)
    if not rqs.ready():
        print(f"  {C.RED}QuoterV2 unavailable — need web3 + an RPC. Set ALCHEMY_ARB_KEY/RPC_URL.{C.RESET}")
        input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    loan = REAL_LOAN_USD
    print(f"  Loan size : {C.BYELLOW}${loan:,.0f}{C.RESET} USDC   {C.DIM}(REAL_LOAN_USD){C.RESET}")
    print(f"  Route     : USDC → WETH → USDC across fee tiers\n")
    print(f"  {'buy':>5} {'sell':>5} {'WETH out':>16} {'USDC back':>14} {'net (incl 0.05% fee)':>22}")
    print(f"  {'─'*5} {'─'*5} {'─'*16} {'─'*14} {'─'*22}")
    amount_in = int(loan*1e6); aave_fee = amount_in*5//10000; any_q=False
    for buy_fee, sell_fee in rqs.PAIRS:
        weth = rqs.quoter.quote(USDC_NATIVE, WETH_ARB_T, amount_in, buy_fee)
        if not weth:
            print(f"  {buy_fee:>5} {sell_fee:>5} {C.DIM}{'no pool':>16}{C.RESET}"); continue
        back = rqs.quoter.quote(WETH_ARB_T, USDC_NATIVE, weth, sell_fee)
        if not back:
            print(f"  {buy_fee:>5} {sell_fee:>5} {weth/1e18:>16.6f} {C.DIM}{'no pool':>14}{C.RESET}"); continue
        any_q=True
        net = (back - amount_in - aave_fee)/1e6
        col = C.BGREEN if net > 0 else C.RED
        print(f"  {buy_fee:>5} {sell_fee:>5} {weth/1e18:>16.6f} {back/1e6:>14,.2f} {col}{net:>+22,.4f}{C.RESET}")
    print()
    if any_q:
        opp = rqs.scan(loan)
        if opp:
            print(f"  {C.BGREEN}{C.BOLD}EDGE: ${opp.profit_usd:,.4f} on {opp.buy_fee}/{opp.sell_fee}{C.RESET}  → build calldata via menu [x]")
        else:
            print(f"  {C.YELLOW}No profitable edge right now (net ≤ ${MIN_PROFIT_USD:.2f}).{C.RESET}")
            print(f"  {C.DIM}Expected in efficient markets — real edges are brief and rare.{C.RESET}")
    else:
        print(f"  {C.YELLOW}No pools answered (RPC rate-limit?). Try a keyed RPC.{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

def menu_optimal_size():
    """Find the loan size that maximises real net profit (maximise profits / highest limits)."""
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── OPTIMAL LOAN SIZING (real QuoterV2) ───{C.RESET}\n")
    if not ADV_MODULES_OK:
        print(f"  {C.RED}Advanced modules unavailable: {_ADV_IMPORT_ERR}{C.RESET}")
        input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    adv = AdvancedEngine()
    if not adv.ready():
        print(f"  {C.YELLOW}Need a live RPC connection for real quotes (option [c]).{C.RESET}")
        input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    print(f"  {C.DIM}Ternary-searching USDC→WETH→USDC for max net profit…{C.RESET}\n")
    res = adv.optimal_loan_size(lo_usd=1_000.0, hi_usd=MAX_LOAN_USD)
    col = C.BGREEN if res['net_usd'] > 0 else C.YELLOW
    print(f"  Optimal loan:  {C.BCYAN}${res['size_usd']:,.2f}{C.RESET}")
    print(f"  Net profit:    {col}${res['net_usd']:,.4f}{C.RESET}")
    print(f"  Gross out:     {C.CYAN}${res['gross_usd']:,.2f}{C.RESET}")
    if res['net_usd'] <= 0:
        print(f"\n  {C.DIM}No positive edge at any size — efficient market (honest result).{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

def menu_triangular():
    """Real multi-token 3-hop arbitrage scan."""
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── TRIANGULAR SCAN (real 3-hop) ───{C.RESET}\n")
    if not ADV_MODULES_OK:
        print(f"  {C.RED}Advanced modules unavailable: {_ADV_IMPORT_ERR}{C.RESET}")
        input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    adv = AdvancedEngine()
    if not adv.ready():
        print(f"  {C.YELLOW}Need a live RPC connection for real quotes (option [c]).{C.RESET}")
        input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    print(f"  Tokens: {C.CYAN}{', '.join(ADV_TOKENS.keys())}{C.RESET}   loan ${REAL_LOAN_USD:,.0f}")
    print(f"  {C.DIM}Probing A→B→C→A cycles via live QuoterV2…{C.RESET}\n")
    out = adv.triangular_scan('USDC', REAL_LOAN_USD)
    print(f"  Routes probed: {C.CYAN}{out['routes_probed']}{C.RESET}   "
          f"profitable: {C.BGREEN}{len(out['profitable'])}{C.RESET}")
    for r in out['profitable'][:5]:
        print(f"    {C.BGREEN}✓{C.RESET} {'→'.join(r['path'])}  "
              f"net={C.BYELLOW}{r['net_human']:.4f} USDC{C.RESET}  fees={r['fees']}")
    if not out['profitable']:
        b = out['best']
        if b:
            print(f"  {C.DIM}Best (unprofitable): {'→'.join(b['path'])} "
                  f"net={b['net_human']:.4f} USDC — efficient market.{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

def menu_analytics():
    """Pattern recognition + market analysis + prediction on a real price series."""
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── ADVANCED ANALYTICS (real WETH/USDC) ───{C.RESET}\n")
    if not ADV_MODULES_OK:
        print(f"  {C.RED}Advanced modules unavailable: {_ADV_IMPORT_ERR}{C.RESET}")
        input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    adv = AdvancedEngine()
    if not adv.ready():
        print(f"  {C.YELLOW}Need a live RPC connection for real quotes (option [c]).{C.RESET}")
        input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    print(f"  {C.DIM}Sampling live WETH→USDC price (40 reads)…{C.RESET}\n")
    series = adv.price_history(40)
    if len(series) < 5:
        print(f"  {C.YELLOW}Insufficient live samples ({len(series)}) — RPC may be rate-limiting.{C.RESET}")
        input(f"\n  {C.DIM}Press ENTER…{C.RESET}"); return
    score = adv.pat.score(series)
    hurst = adv.mkt.hurst_exponent(series)
    rets  = [series[i+1]-series[i] for i in range(len(series)-1)]
    regime = adv.mkt.volatility_regime(rets)
    print(f"  Samples:       {C.CYAN}{len(series)}{C.RESET}  last=${series[-1]:,.2f}")
    print(f"  Pattern score: {C.BYELLOW}{score['score']:+.3f}{C.RESET}  conf={score['confidence']:.2f}")
    print(f"  Signals:       {C.DIM}{score['signals']}{C.RESET}")
    print(f"  Hurst:         {C.CYAN}{hurst}{C.RESET}  {C.DIM}(>0.5 trending, <0.5 mean-revert){C.RESET}")
    print(f"  Vol regime:    {C.CYAN}{regime['regime']}{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

async def main():
    init_db()
    while True:
        clear(); banner(); print_header(); print_menu()
        try:
            choice = input("  Select option > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {C.BYELLOW}Goodbye.{C.RESET}\n"); break
        if   choice == '1': await menu_run_daemon()
        elif choice == '2': await menu_scan_now()
        elif choice == '3': menu_gas_strategies()
        elif choice == '4': menu_revenue()
        elif choice == '5': menu_algorithms()
        elif choice == '6': menu_status()
        elif choice == '7': menu_config()
        elif choice == '8': await menu_tests()
        elif choice == '9': await menu_protocols()
        elif choice == 'c': menu_connection()
        elif choice == 'x': menu_build_calldata()
        elif choice == 'q': menu_real_quote()
        elif choice == 'o': menu_optimal_size()
        elif choice == 't': menu_triangular()
        elif choice == 'v': menu_analytics()
        elif choice == '0': print(f"\n  {C.BYELLOW}Goodbye.{C.RESET}\n"); break
        else: print(f"  {C.RED}Invalid option.{C.RESET}"); await asyncio.sleep(0.4)

def menu_connection():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── ON-CHAIN CONNECTION TEST ───{C.RESET}\n")
    print(f"  RPC endpoint: {C.CYAN}{RPC_ARB}{C.RESET}")
    print(f"  {C.DIM}Pinging chain (forcing fresh read)…{C.RESET}\n")
    cs = chain_status(force=True)
    if cs['connected']:
        print(f"  {C.BGREEN}{C.BOLD}✓ CONNECTED{C.RESET}")
        print(f"    Chain ID:    {C.BCYAN}{cs['chain_id']}{C.RESET}  "
              f"{'(Arbitrum One ✓)' if cs['chain_id']==42161 else C.YELLOW+'(unexpected chain!)'+C.RESET}")
        print(f"    Block:       {C.CYAN}{cs['block']:,}{C.RESET}")
        print(f"    Gas price:   {C.CYAN}{cs['gas_gwei']:.4f} gwei{C.RESET}")
        print(f"    RPC type:    {C.BGREEN+'keyed (Alchemy/custom)' if cs['using_key'] else C.YELLOW+'public node (rate-limited)'}{C.RESET}")
        if WALLET:
            if cs.get('balance_eth') is not None:
                print(f"    Wallet:      {C.CYAN}{WALLET}{C.RESET}")
                print(f"    ETH balance: {C.BGREEN}{cs['balance_eth']:.6f} ETH{C.RESET}  "
                      f"{C.DIM}(zero is fine — PEG/Gelato pay gas){C.RESET}")
            else:
                print(f"    Wallet:      {C.CYAN}{WALLET}{C.RESET}  {C.DIM}(balance read failed){C.RESET}")
        else:
            print(f"    Wallet:      {C.YELLOW}not set — add PRIVATE_KEY to ~/jdl/.env{C.RESET}")
        print(f"\n  {C.DIM}Probing Balancer V2 vault for live USDC liquidity…{C.RESET}")
        finder = ProtocolFinder()
        bal = finder._token_balance('USDC', PROTOCOLS['BALANCER_V2']['address'])
        if bal > 0:
            print(f"  {C.BGREEN}✓ Balancer V2 USDC liquidity: ${bal:,.0f}{C.RESET}  {C.DIM}(on-chain reads working){C.RESET}")
        else:
            print(f"  {C.YELLOW}⚠ Could not read token balance (RPC may be rate-limiting){C.RESET}")
    else:
        print(f"  {C.RED}{C.BOLD}✗ NOT CONNECTED{C.RESET}")
        print(f"    Reason: {C.YELLOW}{cs.get('error') or 'unknown'}{C.RESET}\n")
        print(f"  {C.BOLD}Fix:{C.RESET}")
        if not WEB3_OK:
            print(f"    • web3 not installed — run: {C.CYAN}pip install -r python/requirements_flash.txt{C.RESET}")
        else:
            print(f"    • Add to {C.CYAN}~/jdl/.env{C.RESET}:")
            print(f"        {C.CYAN}ALCHEMY_ARB_KEY=your_key{C.RESET}   {C.DIM}(from alchemy.com → Arbitrum One){C.RESET}")
            print(f"      or a full URL:")
            print(f"        {C.CYAN}RPC_URL=https://arb-mainnet.g.alchemy.com/v2/your_key{C.RESET}")
            print(f"    • The public node {C.DIM}arb1.arbitrum.io/rpc{C.RESET} is tried automatically but is rate-limited.")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

def _run():
    """Console-script entry point (the `flashloan` command). Runs from any directory."""
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(message)s')
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    _run()
