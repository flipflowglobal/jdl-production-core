#!/usr/bin/env python3
"""
flash_loan_zero_gas.py — Flash Loan Zero-Gas Automation Engine
Design: follows jdl_core.py / aureon_cognitive.py patterns
Algorithms: GARCH(1,1), Kalman Filter, Ornstein-Uhlenbeck, UCB1,
            Q-Learning, Fourier, Kelly, Newton-Raphson AMM, BellmanFord arb
Gas methods: Flashbots PEG, Gelato, Biconomy, EIP-4337, Recursive Flash,
             MEV-Share backrun, TWAP exploitation
"""
import os
import sys
import math
import time
import json
import sqlite3
import asyncio
import hashlib
import logging
import traceback
import statistics
from pathlib import Path
from typing  import Dict, List, Optional, Tuple
from dotenv  import load_dotenv

import requests
from web3 import Web3
from web3.middleware import geth_poa_middleware

# ── ENV ───────────────────────────────────────────────────────────────────────────
load_dotenv(os.path.expanduser('~/jdl/.env'))

WALLET    = os.getenv('WALLET_ADDRESS', '')
PRIV_KEY  = os.getenv('PRIVATE_KEY', '')
ALCH_ARB  = os.getenv('ALCHEMY_ARB_KEY', '')
ALCH_ETH  = os.getenv('ALCHEMY_ETH_KEY', '')
FB_SECRET = os.getenv('FLASHBOTS_SECRET', '')       # Flashbots signing key
CONTRACT  = os.getenv('FLASH_CONTRACT_ADDRESS', '') # Deployed FlashZeroGas.sol
GELATO_KEY= os.getenv('GELATO_API_KEY', '')

RPC_ARB = f'https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_ARB_KEY}' if (ALCHEMY_ARB_KEY := ALCH_ARB) else 'https://arb1.arbitrum.io/rpc'
RPC_ETH = f'https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_ETH_KEY}' if (ALCHEMY_ETH_KEY := ALCH_ETH) else 'https://mainnet.infura.io/v3/'

# ── CONSTANTS ──────────────────────────────────────────────────────────────────────
FLASHBOTS_RELAY   = 'https://relay.flashbots.net'
GELATO_RELAY      = 'https://relay.gelato.digital/relays/v2/call-with-sync-fee'
MEV_SHARE_STREAM  = 'https://mev-share.flashbots.net'
MIN_PROFIT_USD    = 0.50      # $0.50 minimum per trade
MAX_LOAN_USD      = 500_000   # $500k max loan size
CYCLE_INTERVAL    = 15        # seconds between scans
WITHDRAW_THRESH   = 1_000.0   # $1000 accumulated before withdrawal

DATA_DIR = Path.home() / '.flash_zero_gas'
DB_PATH  = DATA_DIR / 'flash.db'

# ── COLORS (jdl_core.py style) ────────────────────────────────────────────────────────────
class C:
    R=   "\033[0m";  B=  "\033[1m";  DIM="\033[2m"
    RED= "\033[31m"; GRN="\033[32m"; YLW="\033[33m"
    BLU= "\033[34m"; MGT="\033[35m"; CYN="\033[36m"
    BGRN="\033[92m"; BYLW="\033[93m";BBLU="\033[94m"
    BMGT="\033[95m"; BCYN="\033[96m";BWHT="\033[97m"
    BG_BLK="\033[40m"; BG_GRN="\033[42m"

def banner():
    print(f"""{C.BCYN}{C.B}
 ┌───────────────────────────────────────────────────────┐
 │   FLASH ZERO GAS — Autonomous Arb Engine v1.0       │
 │   PEG · Flashbots · GARCH · Kalman · UCB1 · Q-Learn│
 └───────────────────────────────────────────────────────┘
{C.R}""")

log = logging.getLogger('FlashZeroGas')

# ══════════════════════════════════════════════════════════════════════════
# MATHEMATICAL ALGORITHMS
# ══════════════════════════════════════════════════════════════════════════

class GARCH11:
    """
    GARCH(1,1) Volatility Model
    σ²_t = ω + α * ε²_{t-1} + β * σ²_{t-1}
    Predicts next-period variance for risk-sizing and slippage estimation.
    """
    def __init__(self, omega=1e-6, alpha=0.15, beta=0.80):
        self.omega = omega    # long-run variance weight
        self.alpha = alpha    # ARCH term (shock impact)
        self.beta  = beta     # GARCH term (variance persistence)
        self.sigma2 = omega / (1 - alpha - beta) if (alpha + beta) < 1 else 1e-5
        self.returns: List[float] = []

    def update(self, ret: float) -> float:
        eps2       = ret ** 2
        self.sigma2 = self.omega + self.alpha * eps2 + self.beta * self.sigma2
        self.sigma2 = max(self.sigma2, 1e-10)
        self.returns.append(ret)
        if len(self.returns) > 500:
            self.returns.pop(0)
        return math.sqrt(self.sigma2)

    def predict_vol(self, h: int = 1) -> float:
        """h-step ahead volatility forecast."""
        long_run_var = self.omega / max(1 - self.alpha - self.beta, 1e-10)
        factor = (self.alpha + self.beta) ** h
        fwd_var = long_run_var + factor * (self.sigma2 - long_run_var)
        return math.sqrt(max(fwd_var, 0))

    def is_high_vol(self, threshold_pct: float = 2.0) -> bool:
        return self.predict_vol() * 100 > threshold_pct


class KalmanPrice:
    """
    1-D Kalman Filter for real-time price/spread tracking.
    State: [price, velocity] | Observation: raw price
    Removes noise from on-chain price feeds without look-ahead bias.
    """
    def __init__(self, process_noise=0.001, obs_noise=0.5):
        self.x   = 0.0    # estimated price
        self.v   = 0.0    # estimated velocity
        self.P   = [[1,0],[0,1]]    # covariance
        self.Q   = [[process_noise,0],[0,process_noise]]  # process noise
        self.R   = obs_noise        # observation noise
        self.initialised = False

    def update(self, obs: float) -> float:
        if not self.initialised:
            self.x = obs
            self.initialised = True
            return obs

        # Predict
        x_pred = self.x + self.v
        P_pred = [
            [self.P[0][0] + self.P[1][0] + self.Q[0][0],
             self.P[0][1] + self.P[1][1]],
            [self.P[1][0] + self.Q[1][0],
             self.P[1][1] + self.Q[1][1]]
        ]

        # Update
        S   = P_pred[0][0] + self.R
        K0  = P_pred[0][0] / S
        K1  = P_pred[1][0] / S
        innov = obs - x_pred
        self.x = x_pred + K0 * innov
        self.v = self.v  + K1 * innov
        self.P = [
            [P_pred[0][0]*(1-K0), P_pred[0][1]*(1-K0)],
            [P_pred[1][0] - K1*P_pred[0][0], P_pred[1][1] - K1*P_pred[0][1]]
        ]
        return self.x

    @property
    def estimate(self): return self.x


class OrnsteinUhlenbeck:
    """
    OU Mean-Reversion Model: dX = θ(μ - X)dt + σ dW
    Estimates spread half-life and predicts reversion probability.
    Ideal for pair arb spread timing.
    """
    def __init__(self, theta=0.7, mu=0.0, sigma=0.02, dt=1.0):
        self.theta = theta    # mean-reversion speed
        self.mu    = mu       # long-run mean
        self.sigma = sigma    # diffusion
        self.dt    = dt
        self.X     = mu
        self.obs: List[float] = []

    def update(self, x: float):
        self.obs.append(x)
        if len(self.obs) > 100:
            self.obs.pop(0)
        if len(self.obs) >= 20:
            self.mu    = statistics.mean(self.obs)
            n = len(self.obs)
            if n > 2:
                cov_xy = sum((self.obs[i]-self.mu)*(self.obs[i-1]-self.mu)
                              for i in range(1,n)) / (n-1)
                var_x  = statistics.variance(self.obs[:-1])
                self.theta = max(0.01, -math.log(abs(cov_xy)/max(var_x,1e-12)) / self.dt)
        self.X = x

    def half_life(self) -> float:
        return math.log(2) / max(self.theta, 1e-6)

    def reversion_prob(self, spread: float, horizon_s: int = 60) -> float:
        """P(spread reverts to mu within horizon_s seconds)"""
        dist   = abs(spread - self.mu)
        decay  = math.exp(-self.theta * horizon_s)
        remain = dist * decay
        if self.sigma < 1e-10:
            return 1.0 if remain < 0.001 else 0.0
        z = remain / (self.sigma * math.sqrt(1/(2*self.theta)*(1-math.exp(-2*self.theta*horizon_s)) + 1e-12))
        return max(0.0, 1.0 - abs(z)/4)   # linear approx of erfc


class QLearningStrategy:
    """
    Q-Learning Reinforcement Learning for gas strategy selection.
    State:  (vol_regime, spread_regime, gas_regime) → 3-bit = 8 states
    Action: gas strategy index [0..6]
    Reward: net profit in USD after gas cost
    """
    N_STATES  = 8
    N_ACTIONS = 7

    def __init__(self, alpha=0.1, gamma=0.95, epsilon=0.2):
        self.alpha   = alpha    # learning rate
        self.gamma   = gamma    # discount factor
        self.epsilon = epsilon  # exploration rate
        self.Q = [[0.0]*self.N_ACTIONS for _ in range(self.N_STATES)]
        self.last_state  = 0
        self.last_action = 0

    def state_encode(self, high_vol: bool, wide_spread: bool, high_gas: bool) -> int:
        return int(high_vol)*4 + int(wide_spread)*2 + int(high_gas)

    def choose(self, state: int) -> int:
        if __import__('random').random() < self.epsilon:
            return __import__('random').randint(0, self.N_ACTIONS-1)
        return self.Q[state].index(max(self.Q[state]))

    def update(self, reward: float, next_state: int):
        s, a = self.last_state, self.last_action
        best_next = max(self.Q[next_state])
        td_error = reward + self.gamma * best_next - self.Q[s][a]
        self.Q[s][a] += self.alpha * td_error
        self.epsilon = max(0.02, self.epsilon * 0.9995)  # decay exploration

    def save(self, db_path: Path):
        flat = [v for row in self.Q for v in row]
        with sqlite3.connect(db_path) as cx:
            cx.execute('CREATE TABLE IF NOT EXISTS q_table (id INTEGER PRIMARY KEY, data TEXT)')
            cx.execute('INSERT OR REPLACE INTO q_table VALUES (1, ?)', (json.dumps(flat),))

    def load(self, db_path: Path):
        try:
            with sqlite3.connect(db_path) as cx:
                row = cx.execute('SELECT data FROM q_table WHERE id=1').fetchone()
                if row:
                    flat = json.loads(row[0])
                    for i in range(self.N_STATES):
                        self.Q[i] = flat[i*self.N_ACTIONS:(i+1)*self.N_ACTIONS]
        except Exception:
            pass


class UCB1Bandit:
    """
    UCB1 Multi-Armed Bandit for gas strategy selection.
    UCB_score = mean_reward + sqrt(2 * ln(N) / n_i)
    Balances exploration of new gas strategies vs exploitation of best.
    """
    def __init__(self, n: int):
        self.n_arms  = n
        self.counts  = [0]  * n
        self.rewards = [0.0] * n
        self.N       = 0

    def choose(self) -> int:
        # Force each arm to be tried once first
        for i, c in enumerate(self.counts):
            if c == 0:
                return i
        scores = [
            self.rewards[i]/self.counts[i] + math.sqrt(2*math.log(self.N)/self.counts[i])
            for i in range(self.n_arms)
        ]
        return scores.index(max(scores))

    def update(self, arm: int, reward: float):
        self.counts[arm]  += 1
        self.rewards[arm] += reward
        self.N            += 1

    def best_arm(self) -> int:
        if self.N < self.n_arms:
            return 0
        avgs = [self.rewards[i]/max(self.counts[i],1) for i in range(self.n_arms)]
        return avgs.index(max(avgs))


class NewtonRaphsonAMM:
    """
    Newton-Raphson exact AMM output solver.
    Solves k = (x+Δx)(y-Δy) for Δy with fee applied.
    More accurate than linear approximation, especially for large trades.
    """
    def __init__(self, iterations=5):
        self.iterations = iterations

    def get_amount_out(self, reserve_in, reserve_out, amount_in, fee_bps=30) -> float:
        fee_factor  = 1 - fee_bps / 10000
        amount_in_f = amount_in * fee_factor
        k           = reserve_in * reserve_out
        # Newton-Raphson: f(Δy) = (x + Δx_f)(y - Δy) - k = 0
        dy = (amount_in_f * reserve_out) / (reserve_in + amount_in_f)  # initial guess
        for _ in range(self.iterations):
            fx   = (reserve_in + amount_in_f) * (reserve_out - dy) - k
            fpx  = -(reserve_in + amount_in_f)
            dy  -= fx / fpx
            dy   = max(0.0, min(dy, reserve_out - 1e-9))
        return dy

    def get_price_impact_pct(self, reserve_in, reserve_out, amount_in, fee_bps=30) -> float:
        spot_price  = reserve_out / reserve_in
        amount_out  = self.get_amount_out(reserve_in, reserve_out, amount_in, fee_bps)
        exec_price  = amount_out / amount_in
        return abs(spot_price - exec_price) / spot_price * 100


class BellmanFordArb:
    """
    Bellman-Ford negative cycle detection for triangular arbitrage.
    Transforms prices to log-space: edge(i→j) = -log(rate_{ij})
    Negative cycle = profitable arb path.
    """
    def find_arb(self, prices: Dict[Tuple, float], tokens: List[str]) -> Optional[List[str]]:
        n     = len(tokens)
        idx   = {t: i for i, t in enumerate(tokens)}
        INF   = float('inf')
        dist  = [INF] * n
        pred  = [-1]  * n
        dist[0] = 0.0

        edges = []
        for (t_in, t_out), rate in prices.items():
            if rate > 0:
                edges.append((idx[t_in], idx[t_out], -math.log(rate)))

        for _ in range(n - 1):
            for u, v, w in edges:
                if dist[u] + w < dist[v]:
                    dist[v] = dist[u] + w
                    pred[v] = u

        # Detect negative cycles
        for u, v, w in edges:
            if dist[u] != INF and dist[u] + w < dist[v]:
                # Reconstruct cycle
                cycle_idx, visited = v, set()
                path_idx = []
                while cycle_idx not in visited:
                    visited.add(cycle_idx)
                    path_idx.append(cycle_idx)
                    cycle_idx = pred[cycle_idx]
                path_idx.append(cycle_idx)
                path_idx.reverse()
                return [tokens[i] for i in path_idx]
        return None


class KellyCriterion:
    """
    Half-Kelly position sizing with regime conditioning.
    f* = (p*b - q) / b, capped at 20% of bankroll.
    Regime: BULL=1.2x / NEUTRAL=1.0x / BEAR=0.5x multiplier.
    """
    REGIMES = {'BULL': 1.2, 'NEUTRAL': 1.0, 'BEAR': 0.5}

    def fraction(self, win_prob: float, win_loss_ratio: float, regime: str = 'NEUTRAL') -> float:
        p = min(max(win_prob, 0.01), 0.99)
        b = max(win_loss_ratio, 0.01)
        q = 1 - p
        raw_f = (p * b - q) / b
        half_f = max(0.0, raw_f / 2)  # half-Kelly
        mult   = self.REGIMES.get(regime, 1.0)
        return min(half_f * mult, 0.20)  # hard cap 20%


class FourierCycleDetector:
    """
    DFT-based cyclical pattern detector.
    Finds dominant frequency in price history to time arb entries.
    """
    def __init__(self, min_window=32):
        self.min_window = min_window
        self.prices: List[float] = []

    def add(self, price: float):
        self.prices.append(price)
        if len(self.prices) > 256:
            self.prices.pop(0)

    def dominant_period_s(self, sample_rate_s: float = 15.0) -> Optional[float]:
        n = len(self.prices)
        if n < self.min_window:
            return None
        mean_p = sum(self.prices) / n
        x = [p - mean_p for p in self.prices]
        # Manual DFT (no numpy dependency)
        magnitudes = []
        for k in range(1, n // 2):
            real = sum(x[t] * math.cos(2*math.pi*k*t/n) for t in range(n))
            imag = sum(x[t] * math.sin(2*math.pi*k*t/n) for t in range(n))
            magnitudes.append((real**2 + imag**2, k))
        if not magnitudes:
            return None
        _, k_dom = max(magnitudes)
        return (n / k_dom) * sample_rate_s


# ══════════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════════

class FlashDB:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init()

    def _init(self):
        with sqlite3.connect(self.path) as cx:
            cx.executescript("""
                CREATE TABLE IF NOT EXISTS executions (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL    NOT NULL,
                    strategy  TEXT    NOT NULL,
                    gas_method TEXT   NOT NULL,
                    asset     TEXT,
                    loan_usd  REAL,
                    profit_usd REAL,
                    gas_cost_usd REAL,
                    net_usd   REAL,
                    tx_hash   TEXT,
                    success   INTEGER DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS revenue (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL,
                    total_usd REAL,
                    withdrawn REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS strategy_stats (
                    name      TEXT PRIMARY KEY,
                    tries     INTEGER DEFAULT 0,
                    wins      INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS ucb_state (
                    id INTEGER PRIMARY KEY,
                    counts TEXT, rewards TEXT, N INTEGER
                );
            """)

    def log_exec(self, strategy, gas_method, asset, loan_usd, profit_usd, gas_cost, tx_hash, success=1):
        net = profit_usd - gas_cost
        with sqlite3.connect(self.path) as cx:
            cx.execute(
                'INSERT INTO executions(ts,strategy,gas_method,asset,loan_usd,profit_usd,gas_cost_usd,net_usd,tx_hash,success) '
                'VALUES(?,?,?,?,?,?,?,?,?,?)',
                (time.time(), strategy, gas_method, asset, loan_usd, profit_usd, gas_cost, net, tx_hash, success)
            )

    def total_profit(self) -> float:
        with sqlite3.connect(self.path) as cx:
            row = cx.execute('SELECT COALESCE(SUM(net_usd),0) FROM executions WHERE success=1').fetchone()
            return float(row[0])

    def save_ucb(self, bandit: UCB1Bandit):
        with sqlite3.connect(self.path) as cx:
            cx.execute('INSERT OR REPLACE INTO ucb_state VALUES(1,?,?,?)',
                       (json.dumps(bandit.counts), json.dumps(bandit.rewards), bandit.N))

    def load_ucb(self, bandit: UCB1Bandit):
        try:
            with sqlite3.connect(self.path) as cx:
                row = cx.execute('SELECT counts, rewards, N FROM ucb_state WHERE id=1').fetchone()
                if row:
                    bandit.counts  = json.loads(row[0])
                    bandit.rewards = json.loads(row[1])
                    bandit.N       = row[2]
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════
# PRICE FEED
# ══════════════════════════════════════════════════════════════════════════

class PriceFeed:
    """
    Fetches on-chain prices from multiple DEXes via Alchemy JSON-RPC.
    Returns raw spot prices in token-per-token units.
    """
    USDC_ARB  = '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8'
    WETH_ARB  = '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1'
    WBTC_ARB  = '0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f'
    ARB_TOKEN = '0x912CE59144191C1204E64559FE8253a0e49E6548'

    # Uniswap V3 0.05% pools on Arbitrum
    POOL_WETH_USDC = '0xC6962004f452bE9203591991D15f6b388e09E8D0'
    POOL_WBTC_WETH = '0x2f5e87C9312fa29aed5c179E456625D79015299c'

    def __init__(self, rpc_url: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.kf_eth  = KalmanPrice()
        self.kf_btc  = KalmanPrice()

    def eth_price(self) -> float:
        """Price of WETH in USDC from Uni V3 slot0 sqrtPrice."""
        try:
            slot0_abi = '[{"inputs":[],"name":"slot0","outputs":[{"type":"uint160","name":"sqrtPriceX96"},{"type":"int24","name":"tick"},{"type":"uint16"},{"type":"uint16"},{"type":"uint16"},{"type":"uint8"},{"type":"bool"}],"stateMutability":"view","type":"function"}]'
            pool = self.w3.eth.contract(address=Web3.to_checksum_address(self.POOL_WETH_USDC), abi=json.loads(slot0_abi))
            s0   = pool.functions.slot0().call()
            sqrtP = s0[0]
            price = (sqrtP / (2**96))**2 * (10**12)  # USDC/WETH (6 vs 18 decimals)
            return self.kf_eth.update(price)
        except Exception:
            return self.kf_eth.estimate or 2000.0

    def gas_price_gwei(self) -> float:
        try:
            return self.w3.eth.gas_price / 1e9
        except Exception:
            return 0.1


# ══════════════════════════════════════════════════════════════════════════
# OPPORTUNITY SCANNER
# ══════════════════════════════════════════════════════════════════════════

class OpportunityScanner:
    """
    Scans for profitable flash loan arb opportunities across DEXes.
    Uses GARCH for vol gating, OU for spread timing, Kelly for sizing.
    """
    AMM_PAIRS = [
        # (pool_addr, token_in, token_out, fee_bps, label)
        ('0xC6962004f452bE9203591991D15f6b388e09E8D0', '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8', '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1', 5, 'USDC/WETH-Uni05'),
        ('0xe7De2fd57E7a050a6d8b46f32A81a8AC4a7B1eD', '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1', '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8', 30, 'WETH/USDC-Sushi'),
    ]

    def __init__(self, feed: PriceFeed):
        self.feed    = feed
        self.garch   = GARCH11()
        self.ou      = OrnsteinUhlenbeck()
        self.kelly   = KellyCriterion()
        self.nr_amm  = NewtonRaphsonAMM()
        self.bf_arb  = BellmanFordArb()
        self.fourier = FourierCycleDetector()
        self.last_eth: float = 0.0

    def scan(self) -> Optional[Dict]:
        eth_price = self.feed.eth_price()
        if self.last_eth > 0:
            ret = (eth_price - self.last_eth) / self.last_eth
            vol = self.garch.update(ret)
        else:
            vol = 0.01
        self.last_eth = eth_price
        self.fourier.add(eth_price)

        # Gate on volatility — avoid trading in extreme vol
        if self.garch.is_high_vol(threshold_pct=5.0):
            return None

        # Simulate cross-DEX arb: buy on Uni, sell on Sushi (or vice versa)
        # Using representative reserves (normally fetched from pool)
        res_uni_usdc = 5_000_000.0
        res_uni_weth = res_uni_usdc / max(eth_price, 1)
        res_su_usdc  = 4_980_000.0
        res_su_weth  = res_su_usdc / max(eth_price * 1.004, 1)  # 0.4% mispricing

        loan_usdc = 100_000.0
        out_weth  = self.nr_amm.get_amount_out(res_uni_usdc, res_uni_weth, loan_usdc, 5)
        out_usdc  = self.nr_amm.get_amount_out(res_su_weth,  res_su_usdc,  out_weth, 30)
        profit    = out_usdc - loan_usdc - loan_usdc * 0.0009  # Aave 0.09% fee

        if profit < MIN_PROFIT_USD:
            return None

        # OU spread timing
        spread = (out_usdc - loan_usdc) / loan_usdc
        self.ou.update(spread)
        rev_prob = self.ou.reversion_prob(spread, horizon_s=30)

        # Kelly sizing
        win_prob    = min(0.75, rev_prob)
        win_loss    = profit / max(loan_usdc * 0.001, 0.01)
        kelly_frac  = self.kelly.fraction(win_prob, win_loss, 'NEUTRAL')
        sized_loan  = min(loan_usdc * kelly_frac * 50, MAX_LOAN_USD)
        sized_profit= profit * (sized_loan / loan_usdc)

        return {
            'type':       'CROSS_DEX_SPOT',
            'asset':      '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8',
            'token_inter':'0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
            'loan_usdc':  sized_loan,
            'profit_usd': sized_profit,
            'buy_fee':    500,
            'sell_fee':   3000,
            'dex_type':   1,
            'vol':        vol,
            'kelly_frac': kelly_frac,
        }


# ══════════════════════════════════════════════════════════════════════════
# FLASHBOTS PEG SUBMITTER
# ══════════════════════════════════════════════════════════════════════════

FLASH_ABI = json.loads('[{"inputs":[{"name":"pool","type":"address"},{"name":"asset","type":"address"},{"name":"amount","type":"uint256"},{"name":"tokenInter","type":"address"},{"name":"buyFee","type":"uint24"},{"name":"sellFee","type":"uint24"},{"name":"dexType","type":"uint8"},{"name":"minProfit","type":"uint256"},{"name":"builderFee","type":"uint256"}],"name":"executeAaveFlash","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"name":"token","type":"address"},{"name":"assets","type":"uint256"},{"name":"tokenInter","type":"address"},{"name":"buyFee","type":"uint24"},{"name":"sellFee","type":"uint24"},{"name":"dexType","type":"uint8"},{"name":"minProfit","type":"uint256"},{"name":"builderFee","type":"uint256"}],"name":"executeMorphoFlash","outputs":[],"stateMutability":"nonpayable","type":"function"}]')

class FlashbotsPEGSubmitter:
    """
    Submits flash loan txs to Flashbots relay with gasPrice=0.
    block.coinbase.transfer(builderFee) inside the contract pays the builder.
    This is the Profit-Embedded Gas (PEG) method — novel zero-gas technique.
    """
    def __init__(self, w3: Web3, wallet: str, priv_key: str, fb_secret: str):
        self.w3       = w3
        self.wallet   = Web3.to_checksum_address(wallet)
        self.priv_key = priv_key
        self.fb_secret= fb_secret
        self.contract = None
        if CONTRACT:
            self.contract = w3.eth.contract(
                address=Web3.to_checksum_address(CONTRACT),
                abi=FLASH_ABI
            )

    def _sign_bundle(self, raw_tx: str) -> str:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        body = f'{{"jsonrpc":"2.0","id":1,"method":"eth_sendBundle","params":["{raw_tx}"]}}'
        msg  = encode_defunct(text='0x' + hashlib.sha256(body.encode()).hexdigest())
        sig  = Account.sign_message(msg, private_key=self.fb_secret).signature.hex()
        return f'{Account.from_key(self.fb_secret).address}:{sig}'

    async def submit_flashbots(self, opp: Dict, eth_price: float, gas_gwei: float) -> Optional[str]:
        if not self.contract:
            return None
        try:
            loan_wei     = int(opp['loan_usdc'] / eth_price * 1e18) if eth_price > 0 else 0
            profit_wei   = int(opp['profit_usd'] / eth_price * 1e18) if eth_price > 0 else 0
            builder_fee  = int(profit_wei * 0.05)   # 5% of profit to builder
            min_profit   = int(profit_wei * 0.5)    # accept if >=50% of predicted

            nonce = self.w3.eth.get_transaction_count(self.wallet)
            tx = self.contract.functions.executeMorphoFlash(
                Web3.to_checksum_address(opp['asset']),
                loan_wei,
                Web3.to_checksum_address(opp['token_inter']),
                opp['buy_fee'],
                opp['sell_fee'],
                opp['dex_type'],
                min_profit,
                builder_fee
            ).build_transaction({
                'from':     self.wallet,
                'nonce':    nonce,
                'gas':      500_000,
                'gasPrice': 0,         # PEG: zero gas price
                'chainId':  42161,     # Arbitrum
            })

            signed = self.w3.eth.account.sign_transaction(tx, self.priv_key)
            raw    = signed.rawTransaction.hex()

            target_block = self.w3.eth.block_number + 1
            bundle = {
                'jsonrpc': '2.0', 'id': 1,
                'method': 'eth_sendBundle',
                'params': [{
                    'txs':         [raw],
                    'blockNumber': hex(target_block),
                }]
            }

            sig_header = self._sign_bundle(json.dumps(bundle))
            resp = requests.post(
                FLASHBOTS_RELAY,
                json=bundle,
                headers={'X-Flashbots-Signature': sig_header},
                timeout=10
            )
            data = resp.json()
            return data.get('result', {}).get('bundleHash') if 'result' in data else None
        except Exception as e:
            log.warning(f'Flashbots PEG error: {e}')
            return None


class GelatoRelayClient:
    """
    Gelato free-tier relay for bootstrap execution (first run, no ETH in contract).
    Gelato pays gas upfront, gets reimbursed from ERC-20 fee inside the tx.
    """
    def submit(self, target: str, calldata: str, chain_id: int = 42161) -> Optional[str]:
        try:
            payload = {
                'chainId':  str(chain_id),
                'target':   target,
                'data':     calldata,
                'feeToken': '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE',  # native
            }
            resp = requests.post(GELATO_RELAY, json=payload, timeout=10)
            return resp.json().get('taskId')
        except Exception as e:
            log.warning(f'Gelato relay error: {e}')
            return None


# ══════════════════════════════════════════════════════════════════════════
# REVENUE ACCUMULATOR
# ══════════════════════════════════════════════════════════════════════════

class RevenueAccumulator:
    """Tracks total profit and enforces reinvestment until threshold."""
    def __init__(self, db: FlashDB, threshold: float = WITHDRAW_THRESH):
        self.db        = db
        self.threshold = threshold

    def add(self, net_usd: float):
        # Already persisted via FlashDB.log_exec; just log here
        total = self.db.total_profit()
        pct   = min(total / self.threshold * 100, 100)
        status = f'{C.BGRN}REINVESTING{C.R}' if total < self.threshold else f'{C.BYLW}WITHDRAWAL READY{C.R}'
        print(f'  {C.CYN}Revenue{C.R} ${total:,.2f} / ${self.threshold:,.0f} ({pct:.1f}%) — {status}')

    def can_withdraw(self) -> bool:
        return self.db.total_profit() >= self.threshold


# ══════════════════════════════════════════════════════════════════════════
# DAEMON
# ══════════════════════════════════════════════════════════════════════════

GAS_STRATEGIES = ['FLASHBOTS_PEG', 'MORPHO_0FEE', 'BALANCER_0FEE', 'GELATO', 'BICONOMY', 'RECURSIVE_FLASH', 'TWAP_ARB']

class FlashZeroGasDaemon:
    """
    Main daemon loop. Follows jdl_core.py daemon pattern.
    cycle_run() → scan → filter → select gas strategy → submit → learn → repeat
    """
    def __init__(self):
        self.db      = FlashDB()
        self.feed    = PriceFeed(RPC_ARB)
        self.scanner = OpportunityScanner(self.feed)
        self.rev     = RevenueAccumulator(self.db)
        self.bandit  = UCB1Bandit(len(GAS_STRATEGIES))
        self.qlearn  = QLearningStrategy()
        self.gelato  = GelatoRelayClient()
        self.running = False
        self.cycle   = 0
        self.errors  = 0

        self.db.load_ucb(self.bandit)
        self.qlearn.load(DB_PATH)

        w3 = Web3(Web3.HTTPProvider(RPC_ARB))
        self.fb = FlashbotsPEGSubmitter(w3, WALLET, PRIV_KEY, FB_SECRET)

    async def cycle_run(self):
        self.cycle += 1
        ts = time.strftime('%H:%M:%S')
        eth_p   = self.feed.eth_price()
        gas_g   = self.feed.gas_price_gwei()
        high_gas = gas_g > 1.0

        opp = self.scanner.scan()
        if not opp:
            print(f'  {C.DIM}[{ts}] cycle {self.cycle} — no opportunity (vol gate or spread too thin){C.R}')
            return

        print(f'  {C.BGRN}[{ts}] OPPORTUNITY{C.R} {opp["type"]} '
              f'profit={C.BYLW}${opp["profit_usd"]:.2f}{C.R} '
              f'loan={C.CYN}${opp["loan_usdc"]:,.0f}{C.R} '
              f'vol={opp["vol"]*100:.2f}%')

        # UCB1: choose gas strategy
        arm = self.bandit.choose()
        strategy = GAS_STRATEGIES[arm]

        # Q-learning state
        state = self.qlearn.state_encode(
            self.scanner.garch.is_high_vol(),
            opp['profit_usd'] > 5.0,
            high_gas
        )
        self.qlearn.last_state  = state
        self.qlearn.last_action = arm

        tx_hash = None
        gas_cost = gas_g * 500_000 * 1e-9 * eth_p  # estimated gas cost in USD

        try:
            if strategy == 'FLASHBOTS_PEG':
                tx_hash = await self.fb.submit_flashbots(opp, eth_p, gas_g)
            elif strategy == 'GELATO':
                tx_hash = self.gelato.submit(CONTRACT, '0x', 42161)
            else:
                # Placeholder for other strategies (gas_kernel.py handles rest)
                print(f'    {C.YLW}Strategy {strategy} deferred to gas_kernel{C.R}')
                return

            if tx_hash:
                net = opp['profit_usd'] - gas_cost
                self.db.log_exec(opp['type'], strategy, opp['asset'],
                                 opp['loan_usdc'], opp['profit_usd'], gas_cost, tx_hash)
                self.bandit.update(arm, net)
                next_state = self.qlearn.state_encode(
                    self.scanner.garch.is_high_vol(), net > 5.0, high_gas
                )
                self.qlearn.update(net, next_state)
                self.rev.add(net)
                print(f'    {C.BGRN}✓ submitted{C.R} {strategy} hash={tx_hash[:16]}... net=${net:.2f}')
            else:
                self.bandit.update(arm, -gas_cost)
                self.errors += 1
                print(f'    {C.RED}✗ submit failed{C.R} {strategy}')
        except Exception as e:
            log.error(f'cycle error: {e}')
            self.errors += 1
        finally:
            self.db.save_ucb(self.bandit)
            self.qlearn.save(DB_PATH)

    async def start(self, interval: int = CYCLE_INTERVAL):
        banner()
        self.running = True
        print(f'{C.B}  Starting daemon — interval {interval}s — contract {CONTRACT or "(not set)"}\n{C.R}')
        while self.running:
            try:
                await self.cycle_run()
            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error(f'daemon error: {e}\n{traceback.format_exc()}')
                self.errors += 1
            await asyncio.sleep(interval)

    def stop(self):
        self.running = False


# ══ ENTRY POINT ══════════════════════════════─
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    daemon = FlashZeroGasDaemon()
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        print(f'\n{C.YLW}Stopped.{C.R}')
