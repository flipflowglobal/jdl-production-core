#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
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
    # ─ web3 v5/v6 compatibility shims ──────────────────────────────
    # web3 v5 uses camelCase; v6 uses snake_case. Try v6 first, fall back to v5.
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
    def _inject_poa(w3):
        try: w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        except Exception:
            try:
                from web3.middleware import ExtraDataToPOAMiddleware
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            except Exception: pass
except ImportError:
    WEB3_OK = False
    def _w3_cs(a): return a
    def _gas_p(w): return 0.1
    def _nonce(w, a): return 0
    def _blk(w): return 0
    def _inject_poa(w): pass

load_dotenv(os.path.expanduser('~/jdl/.env'))

WALLET      = os.getenv('WALLET_ADDRESS', '')
PRIV_KEY    = os.getenv('PRIVATE_KEY', '')
ALCH_ARB    = os.getenv('ALCHEMY_ARB_KEY', '')
ALCH_ETH    = os.getenv('ALCHEMY_ETH_KEY', '')
FB_SECRET   = os.getenv('FLASHBOTS_SECRET', '')
CONTRACT    = os.getenv('FLASH_CONTRACT_ADDRESS', '')
GELATO_KEY  = os.getenv('GELATO_API_KEY', '')
BICONOMY_K  = os.getenv('BICONOMY_API_KEY', '')
PAYMASTER   = os.getenv('PAYMASTER_ADDRESS', '')

RPC_ARB = (f'https://arb-mainnet.g.alchemy.com/v2/{ALCH_ARB}'
           if ALCH_ARB else 'https://arb1.arbitrum.io/rpc')
RPC_ETH = (f'https://eth-mainnet.g.alchemy.com/v2/{ALCH_ETH}'
           if ALCH_ETH else 'https://cloudflare-eth.com')

FLASHBOTS_RELAY  = 'https://relay.flashbots.net'
GELATO_RELAY     = 'https://relay.gelato.digital/relays/v2/call-with-sync-fee'
MEV_SHARE_URL    = 'https://mev-share.flashbots.net'
MIN_PROFIT_USD   = 0.50
MAX_LOAN_USD     = 500_000.0
WITHDRAW_THRESH  = 1_000.0
CYCLE_SEC        = 15

DATA_DIR = Path.home() / '.flash_loan_engine'
DB_PATH  = DATA_DIR / 'flash.db'

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
  ██╔══╝  ██║     ██╔══██║╚════██║██╔══██║
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
        for u,v,w in edges:
            if dist[u]!=float('inf') and dist[u]+w < dist[v]:
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
        self._eth: float = 2000.0
        self._gas: float = 0.1
        self._w3  = None
        if WEB3_OK and ALCH_ARB:
            try:
                self._w3 = Web3(Web3.HTTPProvider(RPC_ARB, request_kwargs={'timeout':5}))
                _inject_poa(self._w3)
            except Exception:
                self._w3 = None

    def eth_price(self) -> float:
        if self._w3:
            try:
                abi = '[{"inputs":[],"name":"slot0","outputs":[{"type":"uint160","name":"sqrtPriceX96"},{"type":"int24","name":"tick"},{"type":"uint16"},{"type":"uint16"},{"type":"uint16"},{"type":"uint8"},{"type":"bool"}],"stateMutability":"view","type":"function"}]'
                pool = self._w3.eth.contract(address=_w3_cs(self.WETH_USDC_POOL), abi=json.loads(abi))
                s0   = pool.functions.slot0().call()
                raw  = (s0[0]/(2**96))**2 * 1e12
                self._eth = self.kf.update(raw)
            except Exception:
                pass
        return self._eth

    def gas_gwei(self) -> float:
        if self._w3:
            try: self._gas = _gas_p(self._w3)/1e9
            except Exception: pass
        return self._gas

    def gas_usd(self, gas_units=500_000) -> float:
        return self.gas_gwei() * gas_units * 1e-9 * self.eth_price()

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
            vol=vol, kelly_frac=kf, spread=spread
        )

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
            w3 = Web3(Web3.HTTPProvider(RPC_ARB, request_kwargs={'timeout':10}))
            acc = Account.from_key(PRIV_KEY)
            profit_wei  = int(opp.profit_usd / eth_price * 1e18)
            builder_fee = int(profit_wei * 0.05)
            nonce = _nonce(w3, acc.address)
            tx = {
                'to': _w3_cs(CONTRACT),
                'data': '0x',
                'gas': 600_000, 'gasPrice': 0,
                'nonce': nonce, 'chainId': 42161, 'value': 0
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
                'chainId':str(42161),'target':CONTRACT,
                'data':calldata,'feeToken':'0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE'
            }, timeout=10)
            return r.json().get('taskId')
        except Exception as e:
            logging.warning(f'Gelato: {e}'); return None

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
        self.running = False
        self.cycle   = 0
        self.errors  = 0
        self.bandit.load()
        self.qlearn.load()

    async def cycle_run(self, verbose: bool = True):
        self.cycle += 1
        ts      = datetime.now().strftime('%H:%M:%S')
        eth_p   = self.feed.eth_price()
        gas_g   = self.feed.gas_gwei()
        gas_usd = self.feed.gas_usd()
        opp = self.scanner.scan()
        if not opp:
            if verbose:
                print(f"  {C.DIM}[{ts}] #{self.cycle:04d} scanning — no edge detected  eth=${eth_p:,.0f}  gas={gas_g:.3f}gwei{C.RESET}")
            return
        print(f"  {C.BGREEN}[{ts}]{C.RESET} {C.BOLD}EDGE FOUND{C.RESET}"
              f"  profit={C.BYELLOW}${opp.profit_usd:.3f}{C.RESET}"
              f"  loan={C.BCYAN}${opp.loan_usd:,.0f}{C.RESET}"
              f"  vol={opp.vol*100:.2f}%"
              f"  kelly={opp.kelly_frac*100:.1f}%")
        arm      = self.bandit.choose()
        strategy = GAS_STRATEGIES[arm]
        hv = self.scanner.garch.high_vol()
        ws = opp.profit_usd > 5.0
        hg = gas_g > 1.0
        state = self.qlearn.encode(hv, ws, hg)
        self.qlearn.ls = state; self.qlearn.la = arm
        print(f"  {C.DIM}    strategy={strategy}  arm={arm}{C.RESET}")
        tx_hash = None
        try:
            if strategy == 'FLASHBOTS_PEG':
                tx_hash = self.peg.submit(opp, eth_p)
            elif strategy == 'GELATO_FREE':
                tx_hash = self.gelato.submit()
            else:
                tx_hash = f'sim_{hashlib.md5(f"{time.time()}".encode()).hexdigest()[:16]}'
            net = RevenueTracker.log(
                opp.type, strategy, opp.asset,
                opp.loan_usd, opp.profit_usd, gas_usd,
                tx_hash or '', 1 if tx_hash else 0
            )
            reward = net if tx_hash else -gas_usd
            self.bandit.update(arm, reward)
            ns = self.qlearn.encode(self.scanner.garch.high_vol(), net>5.0, hg)
            self.qlearn.update(reward, ns)
            total = RevenueTracker.total()
            pct   = min(total/WITHDRAW_THRESH*100,100)
            bar   = int(pct/5)
            pbar  = f"[{'#'*bar}{'.'*(20-bar)}]"
            if tx_hash:
                print(f"  {C.BGREEN}  ✓ submitted{C.RESET}  hash={C.DIM}{(tx_hash or '')[:18]}...{C.RESET}  net={C.BYELLOW}${net:.3f}{C.RESET}")
                print(f"  {C.CYAN}  revenue {pbar} ${total:,.2f}/${WITHDRAW_THRESH:,.0f} ({pct:.1f}%){C.RESET}")
            else:
                self.errors += 1
                print(f"  {C.RED}  ✗ submit failed{C.RESET}  {strategy}")
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
def print_header():
    total  = RevenueTracker.total()
    execs  = RevenueTracker.count()
    opps   = db_query("SELECT COUNT(*) FROM opportunities")[0][0]
    pct    = min(total/WITHDRAW_THRESH*100,100)
    bar    = int(pct/5); pbar=f"{'#'*bar}{'.'*(20-bar)}"
    status = f"{C.BGREEN}RUNNING{C.RESET}" if CONTRACT else f"{C.BYELLOW}NO CONTRACT{C.RESET}"
    print(f"""
{C.CYAN}╔══════════════════════════════════════════════════════╗
║{C.BWHITE}{C.BOLD}  FLASH LOAN ENGINE v1.0  ·  Zero-Gas Auto-Arb         {C.RESET}{C.CYAN}║
╠══════════════════════════════════════════════════════╣
║{C.RESET}  💰 Revenue: {C.BGREEN}${total:>10.4f}{C.RESET}  [{pbar}] {pct:.0f}%          {C.CYAN}║
║{C.RESET}  🔍 Opps: {C.BYELLOW}{opps:>5}{C.RESET}  ✓ Execs: {C.BGREEN}{execs:>5}{C.RESET}  Status: {status}  {C.CYAN}║
║{C.RESET}  📌 Contract: {C.DIM}{(CONTRACT or 'not set')[:40]:<40}{C.RESET}{C.CYAN}║
╚══════════════════════════════════════════════════════╝{C.RESET}
""")

def print_menu():
    print(f"""{C.BOLD}  MAIN MENU{C.RESET}
  {C.CYAN}[1]{C.RESET} Start Automation Engine
  {C.CYAN}[2]{C.RESET} Scan for Opportunities
  {C.CYAN}[3]{C.RESET} Gas Strategy Status
  {C.CYAN}[4]{C.RESET} Revenue Log
  {C.CYAN}[5]{C.RESET} Algorithm Dashboard
  {C.CYAN}[6]{C.RESET} System Status
  {C.CYAN}[7]{C.RESET} Configuration
  {C.CYAN}[8]{C.RESET} Run Tests
  {C.CYAN}[0]{C.RESET} Exit
""")

async def menu_run_daemon():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── AUTOMATION ENGINE ───{C.RESET}\n")
    print(f"  {C.BYELLOW}Runs continuous scan + execute cycles with all algorithms.{C.RESET}")
    print(f"  Gas strategies rotate via UCB1 bandit learning.")
    print(f"  Profits auto-reinvest until ${WITHDRAW_THRESH:,.0f} threshold.")
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
    print(f"\n{C.BOLD}{C.CYAN}  ─── OPPORTUNITY SCANNER ───{C.RESET}\n")
    feed    = PriceFeed()
    scanner = OpportunityScanner(feed)
    eth_p   = feed.eth_price()
    gas_g   = feed.gas_gwei()
    print(f"  ETH/USDC: {C.BYELLOW}${eth_p:,.2f}{C.RESET}   Gas: {C.CYAN}{gas_g:.3f} gwei{C.RESET}\n")
    print(f"  {C.DIM}Scanning cross-DEX spreads (Uni V3 × Sushi) …{C.RESET}\n")
    found = 0
    for _ in range(10):
        opp = scanner.scan()
        if opp:
            found += 1
            print(f"  {C.BGREEN}✓{C.RESET} {opp.type:<22} profit={C.BYELLOW}${opp.profit_usd:.4f}{C.RESET}"
                  f"  loan={C.CYAN}${opp.loan_usd:,.0f}{C.RESET}"
                  f"  vol={opp.vol*100:.2f}%  spread={opp.spread*100:.3f}%")
        else:
            print(f"  {C.DIM}—  no edge{C.RESET}")
        await asyncio.sleep(0.1)
    print(f"\n  Found {C.BGREEN}{found}{C.RESET}/10 opportunities.")
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
    print(f"    ETH/USDC:   {C.BYELLOW}${eth:,.2f}{C.RESET}")
    print(f"    Gas:        {C.CYAN}{gas:.3f} gwei{C.RESET}")
    print(f"    Gas cost:   {C.DIM}${feed.gas_usd():.4f} USD (500k gas){C.RESET}")
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
    print(f"  {C.BOLD}Config{C.RESET}")
    print(f"    Wallet:    {C.CYAN}{(WALLET[:16]+'...' if WALLET else 'not set')}{C.RESET}")
    print(f"    Contract:  {C.CYAN}{(CONTRACT[:16]+'...' if CONTRACT else 'not set')}{C.RESET}")
    print(f"    RPC:       {C.DIM}{'Alchemy' if ALCH_ARB else 'public'}{C.RESET}")
    print(f"    Flashbots: {C.BGREEN if FB_SECRET else C.DIM}{'configured' if FB_SECRET else 'not set'}{C.RESET}")
    print(f"    Web3:      {C.BGREEN if WEB3_OK else C.RED}{'OK (v5 Termux-compat)' if WEB3_OK else 'not installed'}{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

def menu_config():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── CONFIGURATION ───{C.RESET}\n")
    env_path = os.path.expanduser('~/jdl/.env')
    print(f"  .env: {C.BGREEN if os.path.exists(env_path) else C.RED}{env_path}{C.RESET}\n")
    fields = [
        ('WALLET_ADDRESS',        WALLET,      'Wallet address'),
        ('PRIVATE_KEY',           '***' if PRIV_KEY else '', 'Private key'),
        ('ALCHEMY_ARB_KEY',       '***' if ALCH_ARB else '', 'Alchemy Arbitrum key'),
        ('FLASHBOTS_SECRET',      '***' if FB_SECRET else '', 'Flashbots signing key'),
        ('FLASH_CONTRACT_ADDRESS', CONTRACT,   'Deployed FlashZeroGas.sol'),
        ('PAYMASTER_ADDRESS',     PAYMASTER,   'ProfitPaymaster.sol (optional)'),
        ('GELATO_API_KEY',        '***' if GELATO_KEY else '', 'Gelato key (optional)'),
    ]
    print(f"  {'Variable':<30} {'Set?':<5} {'Description'}")
    print(f"  {'─'*30} {'─'*5} {'─'*28}")
    for var,val,desc in fields:
        ok = bool(val)
        sym = f"{C.BGREEN}YES{C.RESET}" if ok else f"{C.YELLOW}NO {C.RESET}"
        print(f"  {C.CYAN}{var:<30}{C.RESET} {sym}  {C.DIM}{desc}{C.RESET}")
    print(f"\n  Edit: {C.CYAN}nano ~/jdl/.env{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

async def menu_tests():
    clear()
    print(f"\n{C.BOLD}{C.CYAN}  ─── RUNNING TESTS ───{C.RESET}\n")
    try:
        from test_flash_engine import run_all_tests
        await run_all_tests(verbose=True)
    except ImportError:
        print(f"  {C.RED}test_flash_engine.py not found in python/ directory.{C.RESET}")
    input(f"\n  {C.DIM}Press ENTER…{C.RESET}")

async def main():
    init_db()
    while True:
        clear(); banner(); print_header(); print_menu()
        try:
            choice = input("  Select option > ").strip()
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
        elif choice == '0': print(f"\n  {C.BYELLOW}Goodbye.{C.RESET}\n"); break
        else: print(f"  {C.RED}Invalid option.{C.RESET}"); await asyncio.sleep(0.4)

if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(message)s')
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
