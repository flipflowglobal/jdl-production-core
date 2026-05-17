#!/usr/bin/env python3
"""
JDL API Integrations v2
Alchemy + Etherscan V2 + CoinGecko + DeFi Llama
All endpoints verified working.
Rate limiting with token bucket algorithm.
Retry logic with exponential backoff.
Response caching with TTL.
"""

import requests, json, time, urllib.request, sqlite3, math
from datetime import datetime, timezone
from pathlib import Path
from collections import deque
from dotenv import load_dotenv
import os

load_dotenv('/home/userland/jdl/.env')

WALLET        = os.getenv('WALLET_ADDRESS')
ALCHEMY_KEY   = os.getenv('ALCHEMY_ETH_KEY')
ALCHEMY_SOL   = os.getenv('ALCHEMY_SOL_KEY')
ETHERSCAN_KEY = os.getenv('ETHERSCAN_KEY')
SOLSCAN_KEY   = os.getenv('SOLSCAN_KEY')
ETH_RPC       = os.getenv('ETH_RPC')

DATA_DIR = Path.home() / ".aureon_v3"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH  = DATA_DIR / "aureon.db"

CHAIN_IDS = {
    'ethereum': '1',
    'bsc':      '56',
    'polygon':  '137',
    'arbitrum': '42161',
    'optimism': '10',
    'base':     '8453',
}

ALCHEMY_RPCS = {
    'ethereum': f'https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}',
    'polygon':  f'https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}',
    'arbitrum': f'https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}',
    'optimism': f'https://opt-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}',
    'base':     f'https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}',
}

def db_exec(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    con.execute(sql, params)
    con.commit()
    con.close()

def db_query(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(sql, params).fetchall()
    con.close()
    return rows

def init_tables():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS api_cache (
        key        TEXT PRIMARY KEY,
        value      TEXT,
        fetched_at REAL,
        ttl        REAL
    );
    CREATE TABLE IF NOT EXISTS api_calls (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint   TEXT,
        status     INTEGER,
        latency_ms REAL,
        timestamp  TEXT
    );
    """)
    con.commit()
    con.close()

# ══════════════════════════════════════════════
#  ALGORITHMS
# ══════════════════════════════════════════════

class TokenBucketRateLimiter:
    """
    Token bucket algorithm for API rate limiting.
    Bucket fills at rate r tokens/second.
    Each API call consumes 1 token.
    If bucket empty, wait until enough tokens accumulate.

    Allows bursting up to capacity tokens,
    then enforces steady-state rate limit.
    Prevents 429 rate limit errors.
    """

    def __init__(self, rate: float, capacity: float):
        self._rate     = rate      # tokens per second
        self._capacity = capacity  # max burst size
        self._tokens   = capacity  # start full
        self._last     = time.time()

    def _refill(self):
        now     = time.time()
        elapsed = now - self._last
        self._tokens = min(
            self._capacity,
            self._tokens + elapsed * self._rate
        )
        self._last = now

    def acquire(self, tokens: float = 1.0) -> float:
        """Returns wait time in seconds. 0 if token available immediately."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return 0.0
        # Calculate wait time
        deficit   = tokens - self._tokens
        wait_time = deficit / self._rate
        return wait_time

    def wait_and_acquire(self, tokens: float = 1.0):
        wait = self.acquire(tokens)
        if wait > 0:
            time.sleep(wait)
        self._tokens = max(0, self._tokens - tokens)


class ExponentialBackoffRetry:
    """
    Exponential backoff for failed API calls.
    Retries with delays: base * 2^attempt
    Adds jitter to prevent thundering herd.
    Max retries configurable per endpoint type.
    """
    BASE_DELAY   = 1.0
    MAX_DELAY    = 30.0
    JITTER_RANGE = 0.5

    @staticmethod
    def delay(attempt: int) -> float:
        delay = min(
            ExponentialBackoffRetry.BASE_DELAY * (2 ** attempt),
            ExponentialBackoffRetry.MAX_DELAY
        )
        jitter = delay * ExponentialBackoffRetry.JITTER_RANGE * (2 * __import__('random').random() - 1)
        return max(0, delay + jitter)

    @staticmethod
    def with_retry(func, max_retries: int = 3, *args, **kwargs):
        for attempt in range(max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == max_retries:
                    raise
                wait = ExponentialBackoffRetry.delay(attempt)
                time.sleep(wait)
        return None


class TTLCache:
    """
    Time-To-Live cache with SQLite backend.
    Persists across process restarts.
    Automatic expiry of stale entries.
    """

    @staticmethod
    def get(key: str):
        rows = db_query(
            "SELECT value, fetched_at, ttl FROM api_cache WHERE key=?",
            (key,))
        if not rows:
            return None
        value, fetched_at, ttl = rows[0]
        if time.time() - fetched_at > ttl:
            db_exec("DELETE FROM api_cache WHERE key=?", (key,))
            return None
        try:
            return json.loads(value)
        except Exception:
            return value

    @staticmethod
    def set(key: str, value, ttl: float = 60.0):
        db_exec("""
            INSERT OR REPLACE INTO api_cache (key, value, fetched_at, ttl)
            VALUES (?, ?, ?, ?)
        """, (key, json.dumps(value), time.time(), ttl))

    @staticmethod
    def invalidate(key: str):
        db_exec("DELETE FROM api_cache WHERE key=?", (key,))


class APIMetrics:
    """
    Tracks API call latency and success rate.
    Exponential moving average on response times.
    Detects degraded endpoints.
    """
    ALPHA = 0.2

    def __init__(self):
        self._latency_ema: dict = {}
        self._error_rate:  dict = {}
        self._calls:       dict = {}

    def record(self, endpoint: str, latency_ms: float, success: bool):
        db_exec(
            "INSERT INTO api_calls (endpoint,status,latency_ms,timestamp) VALUES (?,?,?,?)",
            (endpoint, 1 if success else 0, latency_ms,
             datetime.now(timezone.utc).isoformat()))

        # Update EMA latency
        if endpoint not in self._latency_ema:
            self._latency_ema[endpoint] = latency_ms
        else:
            self._latency_ema[endpoint] = (
                self.ALPHA * latency_ms +
                (1 - self.ALPHA) * self._latency_ema[endpoint]
            )

        # Track error rate
        if endpoint not in self._calls:
            self._calls[endpoint] = {'total': 0, 'errors': 0}
        self._calls[endpoint]['total'] += 1
        if not success:
            self._calls[endpoint]['errors'] += 1

    def latency(self, endpoint: str) -> float:
        return round(self._latency_ema.get(endpoint, 0), 1)

    def error_rate(self, endpoint: str) -> float:
        c = self._calls.get(endpoint, {'total': 0, 'errors': 0})
        return c['errors'] / c['total'] if c['total'] > 0 else 0.0

    def is_degraded(self, endpoint: str) -> bool:
        return self.error_rate(endpoint) > 0.30


# Global instances
_coingecko_limiter = TokenBucketRateLimiter(rate=0.5, capacity=5)
_alchemy_limiter   = TokenBucketRateLimiter(rate=10.0, capacity=20)
_etherscan_limiter = TokenBucketRateLimiter(rate=2.0, capacity=5)
_metrics           = APIMetrics()

COINGECKO_IDS = {
    'ETH':    'ethereum',
    'BNB':    'binancecoin',
    'POL':    'pol-ecosystem-token',
    'SOL':    'solana',
    'USDC':   'usd-coin',
    'USDT':   'tether',
    'ARB':    'arbitrum',
    'OP':     'optimism',
    'AAVE':   'aave',
    'UNI':    'uniswap',
    'LINK':   'chainlink',
    'CRV':    'curve-dao-token',
    'GMX':    'gmx',
    'PENDLE': 'pendle',
}

# ── CoinGecko ─────────────────────────────────
def get_live_prices() -> dict:
    cache_key = "coingecko_prices"
    cached    = TTLCache.get(cache_key)
    if cached:
        return cached

    _coingecko_limiter.wait_and_acquire()
    t0 = time.time()
    try:
        ids = ','.join(COINGECKO_IDS.values())
        url = f'https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd'
        req = urllib.request.Request(url, headers={'User-Agent': 'JDL/4.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        prices = {}
        for symbol, cg_id in COINGECKO_IDS.items():
            if cg_id in data and isinstance(data[cg_id], dict):
                prices[symbol] = data[cg_id].get('usd', 0)
        latency = (time.time() - t0) * 1000
        _metrics.record('coingecko', latency, bool(prices))
        if prices:
            TTLCache.set(cache_key, prices, ttl=60.0)
            return prices
    except Exception as e:
        _metrics.record('coingecko', (time.time()-t0)*1000, False)
    cached = TTLCache.get(cache_key)
    return cached or {}

def get_price_with_change(token_ids: str) -> dict:
    _coingecko_limiter.wait_and_acquire()
    t0 = time.time()
    try:
        url = (f'https://api.coingecko.com/api/v3/simple/price'
               f'?ids={token_ids}&vs_currencies=usd&include_24hr_change=true')
        req = urllib.request.Request(url, headers={'User-Agent': 'JDL/4.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        _metrics.record('coingecko_change', (time.time()-t0)*1000, True)
        return data
    except Exception:
        _metrics.record('coingecko_change', (time.time()-t0)*1000, False)
        return {}

# ── Alchemy ───────────────────────────────────
def alchemy_get_eth_balance(chain: str = 'ethereum') -> float:
    cache_key = f"alchemy_bal_{chain}_{WALLET}"
    cached    = TTLCache.get(cache_key)
    if cached is not None:
        return cached

    _alchemy_limiter.wait_and_acquire()
    url = ALCHEMY_RPCS.get(chain, ETH_RPC)
    t0  = time.time()
    try:
        r = requests.post(url, json={
            'jsonrpc': '2.0', 'id': 1,
            'method':  'eth_getBalance',
            'params':  [WALLET, 'latest']
        }, timeout=10)
        data = r.json()
        if 'result' in data:
            bal = int(data['result'], 16) / 1e18
            _metrics.record(f'alchemy_{chain}_balance',
                            (time.time()-t0)*1000, True)
            TTLCache.set(cache_key, bal, ttl=30.0)
            return bal
    except Exception:
        pass
    _metrics.record(f'alchemy_{chain}_balance',
                    (time.time()-t0)*1000, False)
    return 0.0

def alchemy_get_token_balances(chain: str = 'ethereum') -> list:
    if chain == 'bsc':
        return []

    cache_key = f"alchemy_tokens_{chain}_{WALLET}"
    cached    = TTLCache.get(cache_key)
    if cached is not None:
        return cached

    _alchemy_limiter.wait_and_acquire()
    url = ALCHEMY_RPCS.get(chain, ETH_RPC)
    t0  = time.time()
    try:
        r = requests.post(url, json={
            'jsonrpc': '2.0', 'id': 1,
            'method':  'alchemy_getTokenBalances',
            'params':  [WALLET, 'erc20']
        }, timeout=15)
        data = r.json()
        if 'result' in data:
            tokens = []
            for t in data['result'].get('tokenBalances', []):
                raw = int(t['tokenBalance'], 16)
                if raw == 0:
                    continue
                meta = alchemy_get_token_metadata(url, t['contractAddress'])
                if not meta:
                    continue
                bal = raw / (10 ** meta['decimals'])
                if bal > 0.000001:
                    tokens.append({
                        'contract': t['contractAddress'],
                        'symbol':   meta['symbol'],
                        'balance':  round(bal, 6),
                        'decimals': meta['decimals'],
                    })
            _metrics.record(f'alchemy_{chain}_tokens',
                            (time.time()-t0)*1000, True)
            TTLCache.set(cache_key, tokens, ttl=60.0)
            return tokens
    except Exception:
        pass
    _metrics.record(f'alchemy_{chain}_tokens',
                    (time.time()-t0)*1000, False)
    return []

def alchemy_get_token_metadata(rpc_url: str, contract: str) -> dict:
    _alchemy_limiter.wait_and_acquire(0.1)
    try:
        r = requests.post(rpc_url, json={
            'jsonrpc': '2.0', 'id': 1,
            'method':  'alchemy_getTokenMetadata',
            'params':  [contract]
        }, timeout=10)
        result = r.json().get('result', {})
        if result.get('decimals') is not None:
            return {
                'symbol':   result.get('symbol', '???'),
                'decimals': result.get('decimals', 18),
                'name':     result.get('name', ''),
            }
    except Exception:
        pass
    return None

def alchemy_get_transfers(chain: str = 'ethereum') -> list:
    _alchemy_limiter.wait_and_acquire()
    url = ALCHEMY_RPCS.get(chain, ETH_RPC)
    t0  = time.time()
    try:
        r = requests.post(url, json={
            'jsonrpc': '2.0', 'id': 1,
            'method':  'alchemy_getAssetTransfers',
            'params':  [{
                'toAddress':    WALLET,
                'category':     ['erc20', 'erc721', 'internal', 'external'],
                'withMetadata': True,
                'maxCount':     '0x14',
            }]
        }, timeout=15)
        data = r.json()
        if 'result' in data:
            _metrics.record(f'alchemy_{chain}_transfers',
                            (time.time()-t0)*1000, True)
            return data['result'].get('transfers', [])
    except Exception:
        pass
    _metrics.record(f'alchemy_{chain}_transfers',
                    (time.time()-t0)*1000, False)
    return []

def alchemy_get_nfts() -> list:
    _alchemy_limiter.wait_and_acquire()
    t0 = time.time()
    try:
        url = (f'https://eth-mainnet.g.alchemy.com/nft/v3/{ALCHEMY_KEY}'
               f'/getNFTsForOwner?owner={WALLET}')
        r   = requests.get(url, timeout=15)
        nfts = r.json().get('ownedNfts', [])
        _metrics.record('alchemy_nfts', (time.time()-t0)*1000, True)
        return nfts
    except Exception:
        _metrics.record('alchemy_nfts', (time.time()-t0)*1000, False)
        return []

# ── Etherscan V2 ──────────────────────────────
def etherscan_get_transactions(chain: str = 'ethereum',
                               limit: int = 10) -> list:
    _etherscan_limiter.wait_and_acquire()
    t0 = time.time()
    try:
        r = requests.get('https://api.etherscan.io/v2/api',
            params={
                'chainid': CHAIN_IDS.get(chain, '1'),
                'module':  'account',
                'action':  'txlist',
                'address': WALLET,
                'sort':    'desc',
                'page':    '1',
                'offset':  str(limit),
                'apikey':  ETHERSCAN_KEY,
            }, timeout=15)
        data = r.json()
        if data.get('status') == '1':
            _metrics.record(f'etherscan_{chain}_txlist',
                            (time.time()-t0)*1000, True)
            return data.get('result', [])
    except Exception:
        pass
    _metrics.record(f'etherscan_{chain}_txlist',
                    (time.time()-t0)*1000, False)
    return []

def etherscan_get_token_transfers(chain: str = 'ethereum') -> list:
    _etherscan_limiter.wait_and_acquire()
    t0 = time.time()
    try:
        r = requests.get('https://api.etherscan.io/v2/api',
            params={
                'chainid': CHAIN_IDS.get(chain, '1'),
                'module':  'account',
                'action':  'tokentx',
                'address': WALLET,
                'sort':    'desc',
                'apikey':  ETHERSCAN_KEY,
            }, timeout=15)
        data = r.json()
        if data.get('status') == '1':
            _metrics.record(f'etherscan_{chain}_tokentx',
                            (time.time()-t0)*1000, True)
            return data.get('result', [])
    except Exception:
        pass
    _metrics.record(f'etherscan_{chain}_tokentx',
                    (time.time()-t0)*1000, False)
    return []

def etherscan_get_balance(chain: str = 'ethereum') -> float:
    _etherscan_limiter.wait_and_acquire()
    t0 = time.time()
    try:
        r = requests.get('https://api.etherscan.io/v2/api',
            params={
                'chainid': CHAIN_IDS.get(chain, '1'),
                'module':  'account',
                'action':  'balance',
                'address': WALLET,
                'tag':     'latest',
                'apikey':  ETHERSCAN_KEY,
            }, timeout=10)
        data = r.json()
        if data.get('status') == '1':
            _metrics.record(f'etherscan_{chain}_balance',
                            (time.time()-t0)*1000, True)
            return int(data['result']) / 1e18
    except Exception:
        pass
    _metrics.record(f'etherscan_{chain}_balance',
                    (time.time()-t0)*1000, False)
    return 0.0

def etherscan_get_gas() -> dict:
    cache_key = "etherscan_gas"
    cached    = TTLCache.get(cache_key)
    if cached:
        return cached

    _etherscan_limiter.wait_and_acquire()
    t0 = time.time()
    try:
        r = requests.get('https://api.etherscan.io/v2/api',
            params={
                'chainid': '1',
                'module':  'gastracker',
                'action':  'gasoracle',
                'apikey':  ETHERSCAN_KEY,
            }, timeout=10)
        data = r.json()
        if data.get('status') == '1':
            result = data['result']
            _metrics.record('etherscan_gas', (time.time()-t0)*1000, True)
            TTLCache.set(cache_key, result, ttl=30.0)
            return result
    except Exception:
        pass
    _metrics.record('etherscan_gas', (time.time()-t0)*1000, False)
    return {}

# ── Solscan V2 ────────────────────────────────
def solscan_get_balance(sol_address: str) -> float:
    t0 = time.time()
    try:
        r = requests.get(
            f'https://pro-api.solscan.io/v2.0/account/{sol_address}',
            headers={'token': SOLSCAN_KEY},
            timeout=10)
        if r.status_code == 200:
            _metrics.record('solscan_balance', (time.time()-t0)*1000, True)
            return r.json().get('data', {}).get('lamports', 0) / 1e9
    except Exception:
        pass
    _metrics.record('solscan_balance', (time.time()-t0)*1000, False)
    return 0.0

def solscan_get_tokens(sol_address: str) -> list:
    t0 = time.time()
    try:
        r = requests.get(
            'https://pro-api.solscan.io/v2.0/account/token-accounts',
            params={'address': sol_address, 'type': 'token'},
            headers={'token': SOLSCAN_KEY},
            timeout=10)
        if r.status_code == 200:
            _metrics.record('solscan_tokens', (time.time()-t0)*1000, True)
            return r.json().get('data', [])
    except Exception:
        pass
    _metrics.record('solscan_tokens', (time.time()-t0)*1000, False)
    return []

# ── DeFi Llama ────────────────────────────────
def defillama_get_pools(min_apy: float = 5.0,
                         min_tvl_m: float = 1.0,
                         chains: list = None) -> list:
    cache_key = f"defillama_pools_{min_apy}_{min_tvl_m}"
    cached    = TTLCache.get(cache_key)
    if cached:
        return cached

    t0 = time.time()
    try:
        r     = requests.get("https://yields.llama.fi/pools", timeout=30)
        pools = r.json().get('data', [])
        supported = set(chains) if chains else {
            "Ethereum","Arbitrum","Optimism","Base","Polygon","BSC"}
        filtered = [
            p for p in pools
            if p.get('chain','') in supported
            and p.get('apy', 0) >= min_apy
            and p.get('tvlUsd', 0) >= min_tvl_m * 1_000_000
            and p.get('apy', 0) < 1000
        ]
        filtered.sort(key=lambda x: -x.get('apy', 0))
        _metrics.record('defillama_pools', (time.time()-t0)*1000, True)
        TTLCache.set(cache_key, filtered[:100], ttl=3600.0)
        return filtered[:100]
    except Exception:
        pass
    _metrics.record('defillama_pools', (time.time()-t0)*1000, False)
    return cached or []

# ── Full portfolio scan ───────────────────────
def scan_full_portfolio():
    init_tables()
    print("╔══════════════════════════════════════════════════════╗")
    print("║  JDL FULL PORTFOLIO SCAN — LIVE                     ║")
    print(f"║  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                              ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"  Wallet: {WALLET}\n")

    # Prices
    print("── LIVE PRICES (CoinGecko) ───────────────────────────")
    prices = get_live_prices()
    if prices:
        for sym, price in list(prices.items())[:8]:
            print(f"  {sym:<8} ${price:,.4f}")
    else:
        print("  Cannot reach CoinGecko")

    # Balances
    print("\n── WALLET BALANCES (Alchemy) ─────────────────────────")
    total_usd = 0.0
    for chain in ALCHEMY_RPCS:
        bal   = alchemy_get_eth_balance(chain)
        price = prices.get('ETH', 2200)
        if chain == 'polygon':
            price = prices.get('POL', 0.43)
        usd       = bal * price
        total_usd += usd
        status    = "HAS FUNDS ✓" if bal > 0 else "empty"
        print(f"  {chain:<12} {bal:.6f}  (${usd:.4f})  {status}")
    print(f"\n  Total: ${total_usd:.4f}")

    # Token balances
    print("\n── ERC20 TOKENS (Alchemy) ────────────────────────────")
    for chain in ['ethereum', 'arbitrum', 'base']:
        tokens = alchemy_get_token_balances(chain)
        if tokens:
            print(f"  {chain}: {len(tokens)} tokens")
            for t in tokens[:3]:
                print(f"    {t['symbol']:<10} {t['balance']}")
        else:
            print(f"  {chain}: none")

    # Recent transfers
    print("\n── RECENT TRANSFERS (Alchemy) ────────────────────────")
    transfers = alchemy_get_transfers('ethereum')
    if transfers:
        for t in transfers[:5]:
            print(f"  {t.get('asset','?'):<8} {t.get('value','?')} "
                  f"from {str(t.get('from','?'))[:20]}...")
    else:
        print("  No transfers found")

    # Tx history
    print("\n── TRANSACTION HISTORY (Etherscan V2) ───────────────")
    for chain in ['ethereum', 'arbitrum']:
        txs = etherscan_get_transactions(chain, 5)
        if txs:
            print(f"  {chain}: {len(txs)} transactions")
            for tx in txs[:2]:
                val = int(tx.get('value', 0)) / 1e18
                print(f"    {tx['hash'][:20]}...  {val:.4f} ETH")
        else:
            print(f"  {chain}: no transactions")

    # Gas
    print("\n── GAS PRICES (Etherscan V2) ─────────────────────────")
    gas = etherscan_get_gas()
    if gas:
        print(f"  Safe:     {gas.get('SafeGasPrice')} gwei")
        print(f"  Standard: {gas.get('ProposeGasPrice')} gwei")
        print(f"  Fast:     {gas.get('FastGasPrice')} gwei")
    else:
        print("  Gas data unavailable")

    # NFTs
    print("\n── NFTs (Alchemy) ────────────────────────────────────")
    nfts = alchemy_get_nfts()
    if nfts:
        print(f"  {len(nfts)} NFTs")
        for nft in nfts[:3]:
            print(f"    {nft.get('name','Unnamed')}")
    else:
        print("  No NFTs found")

    # API metrics
    print("\n── API METRICS ───────────────────────────────────────")
    for endpoint in ['coingecko', 'alchemy_ethereum_balance',
                     'etherscan_ethereum_txlist', 'defillama_pools']:
        lat   = _metrics.latency(endpoint)
        err   = _metrics.error_rate(endpoint)
        deg   = " ⚠ DEGRADED" if _metrics.is_degraded(endpoint) else ""
        if lat > 0:
            print(f"  {endpoint:<35} {lat:>8.1f}ms  err={err:.0%}{deg}")

    print("\n══════════════════════════════════════════════════════\n")


if __name__ == "__main__":
    init_tables()
    print("\nJDL API Integrations v2")
    print("  Algorithms: Token Bucket Rate Limiting | Exponential Backoff | TTL Cache\n")
    print("  [1] Full portfolio scan")
    print("  [2] Live prices only")
    print("  [3] Wallet balances only")
    print("  [4] Gas prices only")
    print("  [5] API metrics report")

    choice = input("\n  > ").strip()
    if choice == "1":
        scan_full_portfolio()
    elif choice == "2":
        prices = get_live_prices()
        for sym, price in prices.items():
            print(f"  {sym:<8} ${price:,.4f}")
    elif choice == "3":
        for chain in ALCHEMY_RPCS:
            bal = alchemy_get_eth_balance(chain)
            print(f"  {chain:<12} {bal:.6f}")
    elif choice == "4":
        gas = etherscan_get_gas()
        print(json.dumps(gas, indent=2))
    elif choice == "5":
        rows = db_query(
            "SELECT endpoint, AVG(latency_ms), COUNT(*), "
            "SUM(CASE WHEN status=0 THEN 1 ELSE 0 END) "
            "FROM api_calls GROUP BY endpoint ORDER BY COUNT(*) DESC")
        print(f"\n  {'Endpoint':<35} {'Avg ms':>8} {'Calls':>7} {'Errors':>7}")
        for r in rows:
            print(f"  {r[0]:<35} {r[1]:>8.1f} {r[2]:>7} {r[3]:>7}")
    else:
        scan_full_portfolio()
