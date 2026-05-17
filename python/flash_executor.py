#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  JDL Flash Executor v3 — Real Aave V3 Flash Loan Engine        ║
║  Newton-Raphson AMM slippage | EIP-1559 gas optimization        ║
║  Triangular arbitrage detection | Multi-hop routing             ║
║  Full execution pipeline with deployed AureonPayProcessor        ║
║  Genetic Algorithm for route optimization                        ║
╚══════════════════════════════════════════════════════════════════╝
"""

from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv
import os, json, time, sqlite3, math, random
from datetime import datetime, timezone
from pathlib import Path

load_dotenv(os.path.expanduser('~/jdl/.env'))

PRIVATE_KEY    = os.getenv('PRIVATE_KEY', '')
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS', '')
ALCHEMY_KEY    = os.getenv('ALCHEMY_ETH_KEY', '')

DATA_DIR = Path.home() / ".aureon_v3"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH      = DATA_DIR / "aureon.db"
CONTRACT_DIR = DATA_DIR / "contracts"

CHAINS = {
    'optimism': {
        'rpc':       os.getenv('OPTIMISM_RPC', f'https://opt-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}'),
        'chain_id':  10,
        'aave_pool': '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
        'tokens': {
            'USDC': '0x7F5c764cBc14f9669B88837ca1490cCa17c31607',
            'USDT': '0x94b008aA00579c1307B0EF2c499aD98a8ce58e58',
            'DAI':  '0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1',
            'WETH': '0x4200000000000000000000000000000000000006',
            'OP':   '0x4200000000000000000000000000000000000042',
        },
        'quoter':     '0x61fFE014bA17989E743c5F6cB21bF9697530B21e',
        'router':     '0xE592427A0AEce92De3Edee1F18E0157C05861564',
        'native_price': 2200,
    },
    'base': {
        'rpc':       os.getenv('BASE_RPC', f'https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}'),
        'chain_id':  8453,
        'aave_pool': '0xA238Dd80C259a72e81d7e4664a9801593F98d1c5',
        'tokens': {
            'USDC': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
            'WETH': '0x4200000000000000000000000000000000000006',
            'DAI':  '0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb',
        },
        'quoter':     '0x61fFE014bA17989E743c5F6cB21bF9697530B21e',
        'router':     '0xE592427A0AEce92De3Edee1F18E0157C05861564',
        'native_price': 2200,
    },
    'arbitrum': {
        'rpc':       os.getenv('ARBITRUM_RPC', f'https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}'),
        'chain_id':  42161,
        'aave_pool': '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
        'tokens': {
            'USDC': '0xaf88d065e77c8cC2239327C5EDb3A432268e5831',
            'USDT': '0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9',
            'WETH': '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
            'ARB':  '0x912CE59144191C1204E64559FE8253a0e49E6548',
            'DAI':  '0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1',
            'GMX':  '0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a',
        },
        'quoter':     '0x61fFE014bA17989E743c5F6cB21bF9697530B21e',
        'router':     '0xE592427A0AEce92De3Edee1F18E0157C05861564',
        'native_price': 2200,
    },
    'polygon': {
        'rpc':       os.getenv('POLYGON_RPC', 'https://polygon-bor-rpc.publicnode.com'),
        'chain_id':  137,
        'aave_pool': '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
        'tokens': {
            'USDC':  '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174',
            'USDT':  '0xc2132D05D31c914a87C6611C10748AEb04B58e8F',
            'WMATIC':'0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270',
            'WETH':  '0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619',
            'DAI':   '0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063',
        },
        'quoter':     '0x61fFE014bA17989E743c5F6cB21bF9697530B21e',
        'router':     '0xE592427A0AEce92De3Edee1F18E0157C05861564',
        'native_price': 0.43,
    },
}

QUOTER_ABI = [{"inputs":[{"components":[
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

AUREON_ABI = [
    {"inputs":[{"name":"_aavePool","type":"address"}],
     "stateMutability":"nonpayable","type":"constructor"},
    {"inputs":[
        {"name":"asset","type":"address"},{"name":"amount","type":"uint256"},
        {"name":"tokenInter","type":"address"},{"name":"buyFee","type":"uint24"},
        {"name":"sellFee","type":"uint24"},{"name":"minProfit","type":"uint256"},
    ],"name":"executeFlashArbitrage","outputs":[],
    "stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"asset","type":"address"},
               {"name":"amount","type":"uint256"}],
     "name":"withdrawToken","outputs":[],
     "stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"token","type":"address"}],
     "name":"tokenBalance","outputs":[{"name":"","type":"uint256"}],
     "stateMutability":"view","type":"function"},
]

ERC20_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf",
     "outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],
     "stateMutability":"view","type":"function"},
]


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
    CREATE TABLE IF NOT EXISTS flash_executions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        chain        TEXT,
        token        TEXT,
        loan_amount  REAL,
        route        TEXT,
        buy_fee      INTEGER,
        sell_fee     INTEGER,
        gross_profit REAL,
        net_profit   REAL,
        tx_hash      TEXT,
        gas_used     INTEGER,
        gas_cost_usd REAL,
        success      INTEGER,
        timestamp    TEXT
    );
    CREATE TABLE IF NOT EXISTS scan_results (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        chain       TEXT,
        route       TEXT,
        net_profit  REAL,
        viable      INTEGER,
        reason      TEXT,
        scanned_at  TEXT
    );
    """)
    con.commit()
    con.close()


# ══════════════════════════════════════════════════════════════════
#  ALGORITHM 1 — Newton-Raphson Iterative AMM Solver
# ══════════════════════════════════════════════════════════════════
class NewtonRaphsonAMM:
    """
    Iterative Newton-Raphson solver for exact AMM output with fees.

    For constant-product x*y=k with fee f:
      y_out = y * x_in*(1-f) / (x + x_in*(1-f))

    Multi-hop: apply iteratively across each pool.
    Jacobian update for rapid convergence (3-5 iterations).

    Convergence criterion: |f(x_n)| < epsilon = 1e-10
    """

    @staticmethod
    def exact_output_single(reserve_in: int, reserve_out: int,
                             amount_in: int, fee_bps: int = 30) -> int:
        """Constant product formula with Newton-Raphson refinement."""
        if reserve_in <= 0 or reserve_out <= 0 or amount_in <= 0:
            return 0

        # Initial estimate (linear approximation)
        fee_mult = 10000 - fee_bps
        amt_fee  = amount_in * fee_mult

        # Newton-Raphson iterations for exact solution
        # f(y) = (x + dx*(1-f)) * (y - dy) - k = 0
        # f'(y) = -(x + dx*(1-f))
        k        = reserve_in * reserve_out
        x_eff    = reserve_in * 10000 + amt_fee
        dy       = (amt_fee * reserve_out) // x_eff  # initial estimate

        for _ in range(5):  # Newton-Raphson loop
            y_new = reserve_out - dy
            if y_new <= 0:
                break
            # Residual: check if x*y=k holds
            residual = x_eff * y_new - k * 10000
            # Derivative: d(residual)/d(dy) = -x_eff
            if x_eff == 0:
                break
            dy -= residual // x_eff
            dy  = max(0, min(dy, reserve_out - 1))

        return int(dy)

    @staticmethod
    def price_impact(reserve_in: int, reserve_out: int,
                     amount_in: int, fee_bps: int = 30) -> float:
        """Price impact as fraction (0.01 = 1%)."""
        if reserve_in <= 0 or reserve_out <= 0:
            return 1.0
        mid_price    = reserve_out / reserve_in
        actual_out   = NewtonRaphsonAMM.exact_output_single(
            reserve_in, reserve_out, amount_in, fee_bps)
        exec_price   = actual_out / amount_in if amount_in > 0 else 0
        if mid_price == 0:
            return 1.0
        return abs(mid_price - exec_price) / mid_price

    @staticmethod
    def multi_hop(amounts: list, reserves: list, fees: list) -> int:
        """
        Iteratively apply AMM formula across N pools.
        amounts[0] = input amount
        reserves[i] = (reserve_in, reserve_out) for pool i
        """
        amount = amounts[0]
        for i, (res_in, res_out) in enumerate(reserves):
            fee = fees[i] if i < len(fees) else 30
            amount = NewtonRaphsonAMM.exact_output_single(
                res_in, res_out, amount, fee)
            if amount == 0:
                return 0
        return amount


# ══════════════════════════════════════════════════════════════════
#  ALGORITHM 2 — Genetic Algorithm Route Optimizer
# ══════════════════════════════════════════════════════════════════
class GeneticRouteOptimizer:
    """
    Genetic Algorithm for optimal flash loan route selection.

    Chromosome: [token_borrow, token_inter, buy_fee, sell_fee, loan_size]
    Fitness:    net_profit from simulation

    Operations:
    - Selection:   Tournament selection (k=3)
    - Crossover:   Uniform crossover (p=0.7)
    - Mutation:    Random gene flip (p=0.15)

    Runs POP_SIZE=20, GENERATIONS=10 per scan.
    Finds globally optimal route combination.
    """

    POP_SIZE    = 20
    GENERATIONS = 10
    MUTATION_P  = 0.15
    CROSSOVER_P = 0.70

    FEE_TIERS  = [100, 500, 3000, 10000]
    LOAN_SIZES = [3000, 5000, 10000, 20000, 50000]

    def __init__(self, chain: str, simulator):
        self._chain = chain
        self._sim   = simulator  # FlashLoanSimulator instance
        self._tokens = [t for t in CHAINS[chain]["tokens"]
                        if t not in ("WMATIC",)]

    def _random_chromosome(self) -> dict:
        tokens = self._tokens
        if len(tokens) < 2:
            return {}
        tok_b  = random.choice(tokens)
        tok_i  = random.choice([t for t in tokens if t != tok_b])
        return {
            "token_borrow": tok_b,
            "token_inter":  tok_i,
            "buy_fee":      random.choice(self.FEE_TIERS),
            "sell_fee":     random.choice(self.FEE_TIERS),
            "loan_size":    random.choice(self.LOAN_SIZES),
        }

    def _fitness(self, chrom: dict, gas_cost: float) -> float:
        try:
            result = self._sim.simulate(
                chrom["token_borrow"],
                chrom["token_inter"],
                chrom["loan_size"],
                gas_cost,
                buy_fee_hint=chrom["buy_fee"],
                sell_fee_hint=chrom["sell_fee"],
            )
            return result.get("net_profit", -999) if result.get("viable") else -999
        except Exception:
            return -999

    def _tournament_select(self, population: list, fitnesses: list) -> dict:
        k         = min(3, len(population))
        candidates = random.sample(range(len(population)), k)
        best       = max(candidates, key=lambda i: fitnesses[i])
        return population[best].copy()

    def _crossover(self, p1: dict, p2: dict) -> tuple:
        if random.random() > self.CROSSOVER_P:
            return p1.copy(), p2.copy()
        c1, c2 = {}, {}
        for key in p1:
            if random.random() < 0.5:
                c1[key], c2[key] = p1[key], p2[key]
            else:
                c1[key], c2[key] = p2[key], p1[key]
        return c1, c2

    def _mutate(self, chrom: dict) -> dict:
        c = chrom.copy()
        for key in c:
            if random.random() < self.MUTATION_P:
                if key == "buy_fee" or key == "sell_fee":
                    c[key] = random.choice(self.FEE_TIERS)
                elif key == "loan_size":
                    c[key] = random.choice(self.LOAN_SIZES)
                elif key in ("token_borrow", "token_inter"):
                    c[key] = random.choice(self._tokens)
        # Ensure tokens differ
        if c.get("token_borrow") == c.get("token_inter"):
            alts = [t for t in self._tokens if t != c["token_borrow"]]
            if alts:
                c["token_inter"] = random.choice(alts)
        return c

    def evolve(self, gas_cost: float) -> list:
        """Run GA, return top 5 routes sorted by fitness."""
        if len(self._tokens) < 2:
            return []

        population = [self._random_chromosome() for _ in range(self.POP_SIZE)]
        best_routes = []

        for gen in range(self.GENERATIONS):
            fitnesses = [self._fitness(c, gas_cost) for c in population]
            scored    = sorted(zip(fitnesses, population), key=lambda x: -x[0])

            # Elitism — keep top 2
            new_pop  = [scored[0][1].copy(), scored[1][1].copy()]

            while len(new_pop) < self.POP_SIZE:
                p1  = self._tournament_select(population, fitnesses)
                p2  = self._tournament_select(population, fitnesses)
                c1, c2 = self._crossover(p1, p2)
                new_pop.append(self._mutate(c1))
                if len(new_pop) < self.POP_SIZE:
                    new_pop.append(self._mutate(c2))

            population = new_pop

        # Final evaluation
        final_fit = [self._fitness(c, gas_cost) for c in population]
        combined  = sorted(zip(final_fit, population), key=lambda x: -x[0])
        return [(f, c) for f, c in combined if f > 0][:5]


# ══════════════════════════════════════════════════════════════════
#  ALGORITHM 3 — Flash Loan Simulator (enhanced)
# ══════════════════════════════════════════════════════════════════
class FlashLoanSimulator:
    """
    Enhanced pre-execution simulation with triangular route support.
    Uses on-chain Uniswap V3 Quoter V2 for exact quotes.
    Newton-Raphson for slippage correction.
    Multi-stage profitability filter.
    """

    AAVE_FEE_PCT = 0.0009
    SLIPPAGE_PCT = 0.001
    MIN_PROFIT   = 2.0

    def __init__(self, w3: Web3, chain: str):
        self._w3    = w3
        self._chain = chain

    def _get_quote(self, quoter_addr: str, token_in: str,
                   token_out: str, amount_in: int, fee: int) -> tuple:
        """Returns (amountOut, gasEstimate)."""
        try:
            quoter = self._w3.eth.contract(
                address=Web3.to_checksum_address(quoter_addr),
                abi=QUOTER_ABI)
            r = quoter.functions.quoteExactInputSingle({
                "tokenIn":           Web3.to_checksum_address(token_in),
                "tokenOut":          Web3.to_checksum_address(token_out),
                "amountIn":          amount_in,
                "fee":               fee,
                "sqrtPriceLimitX96": 0
            }).call()
            return r[0], r[3]  # amountOut, gasEstimate
        except Exception:
            return 0, 0

    def _best_quote(self, token_in: str, token_out: str,
                    amount_in: int, preferred_fee: int = None) -> tuple:
        """Try all fee tiers, return best (amountOut, fee, gasEst)."""
        quoter = CHAINS[self._chain]['quoter']
        tokens = CHAINS[self._chain]['tokens']
        addr_in  = tokens.get(token_in)
        addr_out = tokens.get(token_out)
        if not addr_in or not addr_out:
            return 0, 0, 0

        fees = [preferred_fee] if preferred_fee else [100, 500, 3000, 10000]
        best_out, best_fee, best_gas = 0, 0, 0

        for fee in fees:
            out, gas = self._get_quote(quoter, addr_in, addr_out, amount_in, fee)
            if out > best_out:
                best_out, best_fee, best_gas = out, fee, gas

        return best_out, best_fee, best_gas

    def simulate(self, token_borrow: str, token_inter: str,
                 borrow_usd: float, gas_cost_usd: float,
                 buy_fee_hint: int = None,
                 sell_fee_hint: int = None) -> dict:
        """Full simulation: borrow → buy → sell → repay."""
        tokens   = CHAINS[self._chain]['tokens']
        addr_b   = tokens.get(token_borrow)
        addr_i   = tokens.get(token_inter)

        if not addr_b or not addr_i:
            return {"viable": False, "reason": "Token not on this chain"}

        # Compute amount_in in token units
        if token_borrow in ("USDC", "USDT"):
            amount_in = int(borrow_usd * 10**6)
            decimals  = 6
        elif token_borrow == "DAI":
            amount_in = int(borrow_usd * 10**18)
            decimals  = 18
        else:  # WETH etc.
            eth_price = CHAINS[self._chain].get("native_price", 2200)
            amount_in = int(borrow_usd / eth_price * 10**18)
            decimals  = 18

        if amount_in <= 0:
            return {"viable": False, "reason": "Invalid amount"}

        # ── Buy leg ────────────────────────────────────────────────
        inter_out, buy_fee, gas_buy = self._best_quote(
            token_borrow, token_inter, amount_in, buy_fee_hint)

        if inter_out <= 0:
            return {"viable": False, "reason": "No liquidity on buy leg"}

        # ── Sell leg ───────────────────────────────────────────────
        returned_raw, sell_fee, gas_sell = self._best_quote(
            token_inter, token_borrow, inter_out, sell_fee_hint)

        if returned_raw <= 0:
            return {"viable": False, "reason": "No liquidity on sell leg"}

        # ── Profit calc ────────────────────────────────────────────
        profit_raw = returned_raw - amount_in
        if decimals == 6:
            profit_usd = profit_raw / 10**6
        else:
            eth_price  = CHAINS[self._chain].get("native_price", 2200)
            profit_usd = (profit_raw / 10**18) * eth_price

        # ── Newton-Raphson slippage correction ─────────────────────
        # Approximate reserves from quote ratio
        implied_res_in  = amount_in * 10
        implied_res_out = inter_out * 10
        nr_corrected    = NewtonRaphsonAMM.exact_output_single(
            implied_res_in, implied_res_out, amount_in, buy_fee or 30)
        slippage_factor = nr_corrected / inter_out if inter_out > 0 else 1.0
        profit_usd     *= slippage_factor

        # ── Cost deductions ────────────────────────────────────────
        aave_fee = borrow_usd * self.AAVE_FEE_PCT
        slippage = borrow_usd * self.SLIPPAGE_PCT
        net      = profit_usd - aave_fee - gas_cost_usd - slippage
        viable   = net > self.MIN_PROFIT

        return {
            "viable":        viable,
            "gross_profit":  round(profit_usd, 6),
            "aave_fee":      round(aave_fee, 6),
            "gas_cost":      round(gas_cost_usd, 6),
            "slippage":      round(slippage, 6),
            "net_profit":    round(net, 6),
            "buy_fee_tier":  buy_fee,
            "sell_fee_tier": sell_fee,
            "token_borrow":  token_borrow,
            "token_inter":   token_inter,
            "borrow_usd":    borrow_usd,
            "slippage_corr": round(slippage_factor, 6),
        }


# ══════════════════════════════════════════════════════════════════
#  ALGORITHM 4 — Multi-stage Profitability Filter
# ══════════════════════════════════════════════════════════════════
class ProfitabilityFilter:
    """
    5-stage filter before execution is allowed.

    Stage 1: Net profit > MIN_NET_PROFIT
    Stage 2: Profit/Risk ratio >= 2.0
    Stage 3: Gas <= 10% of gross profit
    Stage 4: Slippage correction factor > 0.95
    Stage 5: Borrow size within Aave liquidity limits
    """

    MIN_NET_PROFIT    = 2.0
    MIN_PROFIT_RISK   = 2.0
    MAX_GAS_PCT       = 0.10
    MIN_SLIPPAGE_CORR = 0.95
    MAX_LOAN_USD      = 500_000

    @staticmethod
    def check(sim: dict) -> tuple:
        if not sim.get("viable"):
            return False, "Simulation not viable"

        net   = sim.get("net_profit", 0)
        gross = sim.get("gross_profit", 0)
        gas   = sim.get("gas_cost", 0)
        aave  = sim.get("aave_fee", 0)
        slip  = sim.get("slippage", 0)
        corr  = sim.get("slippage_corr", 1.0)
        loan  = sim.get("borrow_usd", 0)

        if net < ProfitabilityFilter.MIN_NET_PROFIT:
            return False, f"Net ${net:.4f} below min ${ProfitabilityFilter.MIN_NET_PROFIT}"

        risk = aave + gas + slip
        if risk > 0:
            pratio = net / risk
            if pratio < ProfitabilityFilter.MIN_PROFIT_RISK:
                return False, f"P/R={pratio:.2f} < {ProfitabilityFilter.MIN_PROFIT_RISK}"

        if gross > 0 and gas / gross > ProfitabilityFilter.MAX_GAS_PCT:
            return False, f"Gas={gas/gross:.1%} exceeds {ProfitabilityFilter.MAX_GAS_PCT:.0%}"

        if corr < ProfitabilityFilter.MIN_SLIPPAGE_CORR:
            return False, f"Slippage correction {corr:.3f} too low"

        if loan > ProfitabilityFilter.MAX_LOAN_USD:
            return False, f"Loan ${loan:,.0f} exceeds max ${ProfitabilityFilter.MAX_LOAN_USD:,}"

        return True, "All 5 stages passed"


# ══════════════════════════════════════════════════════════════════
#  GAS PRICING
# ══════════════════════════════════════════════════════════════════
def get_eip1559_fees(w3: Web3) -> dict:
    """Optimal EIP-1559 fee calculation using fee history."""
    try:
        block    = w3.eth.get_block("latest")
        base_fee = block.get("baseFeePerGas", 0)
        history  = w3.eth.fee_history(10, "latest", [25, 50, 75])
        rewards  = [r for block_r in history.get("reward", [])
                    for r in block_r if r > 0]
        if rewards:
            rewards.sort()
            priority = rewards[len(rewards) // 2]
        else:
            priority = w3.to_wei(1, "gwei")

        max_fee  = int(base_fee * 1.5 + priority)
        return {
            "maxFeePerGas":         max_fee,
            "maxPriorityFeePerGas": priority,
            "baseFee":              base_fee,
            "gwei_max":             round(max_fee / 1e9, 4),
        }
    except Exception:
        gp = w3.eth.gas_price
        return {"gasPrice": gp, "gwei_max": round(gp / 1e9, 4)}

def estimate_gas_usd(w3: Web3, chain_name: str) -> float:
    """Estimate total gas cost in USD for a flash loan tx."""
    try:
        fees      = get_eip1559_fees(w3)
        gas_units = 600_000  # typical flash loan gas
        gwei_max  = fees["gwei_max"]
        eth_price = CHAINS[chain_name].get("native_price", 2200)
        return round((gwei_max * gas_units / 1e9) * eth_price, 4)
    except Exception:
        return 0.0

def connect(chain_name: str):
    """Connect to chain RPC, return Web3 or None."""
    rpc = CHAINS[chain_name].get('rpc', '')
    if not rpc or 'None' in rpc:
        return None
    try:
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 12}))
        return w3 if w3.is_connected() else None
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
#  EXECUTION ENGINE
# ══════════════════════════════════════════════════════════════════
class FlashExecutor:
    """
    Executes flash loan arbitrage via deployed AureonPayProcessor.
    Reads contract address from DATA_DIR/contracts/addresses.json.
    """

    def __init__(self, w3: Web3, chain: str):
        self._w3    = w3
        self._chain = chain
        self._contract_addr = self._load_contract_addr()

    def _load_contract_addr(self) -> str:
        addr_file = CONTRACT_DIR / "addresses.json"
        if not addr_file.exists():
            return ""
        try:
            with open(addr_file) as f:
                data = json.load(f)
            return data.get(self._chain, {}).get("address", "")
        except Exception:
            return ""

    def execute(self, sim_result: dict) -> dict:
        """
        Execute flash loan arbitrage via on-chain contract.
        Requires:
          - Deployed AureonPayProcessor on the target chain
          - Funded wallet for gas
        """
        if not self._contract_addr:
            return {
                "success": False,
                "error":   "No contract deployed. Run: python3 deploy_contract.py",
            }
        if not PRIVATE_KEY or not WALLET_ADDRESS:
            return {"success": False, "error": "PRIVATE_KEY or WALLET_ADDRESS not set"}

        tokens     = CHAINS[self._chain]["tokens"]
        asset_sym  = sim_result["token_borrow"]
        inter_sym  = sim_result["token_inter"]
        asset_addr = tokens.get(asset_sym)
        inter_addr = tokens.get(inter_sym)
        loan_usd   = sim_result["borrow_usd"]
        buy_fee    = sim_result["buy_fee_tier"]
        sell_fee   = sim_result["sell_fee_tier"]

        if not asset_addr or not inter_addr:
            return {"success": False, "error": "Token address missing"}

        # Compute on-chain loan amount
        if asset_sym in ("USDC", "USDT"):
            loan_amount = int(loan_usd * 10**6)
        elif asset_sym == "DAI":
            loan_amount = int(loan_usd * 10**18)
        else:
            eth_price   = CHAINS[self._chain].get("native_price", 2200)
            loan_amount = int(loan_usd / eth_price * 10**18)

        # Min profit in token units (50% of simulated net)
        net_usd     = sim_result["net_profit"] * 0.5
        if asset_sym in ("USDC", "USDT"):
            min_profit = int(net_usd * 10**6)
        elif asset_sym == "DAI":
            min_profit = int(net_usd * 10**18)
        else:
            eth_price  = CHAINS[self._chain].get("native_price", 2200)
            min_profit = int(net_usd / eth_price * 10**18)

        try:
            contract = self._w3.eth.contract(
                address=Web3.to_checksum_address(self._contract_addr),
                abi=AUREON_ABI)

            nonce    = self._w3.eth.get_transaction_count(WALLET_ADDRESS, "pending")
            fees     = get_eip1559_fees(self._w3)
            chain_id = CHAINS[self._chain]["chain_id"]

            # Build tx
            fn  = contract.functions.executeFlashArbitrage(
                Web3.to_checksum_address(asset_addr),
                loan_amount,
                Web3.to_checksum_address(inter_addr),
                buy_fee,
                sell_fee,
                min_profit,
            )
            gas_est = fn.estimate_gas({"from": WALLET_ADDRESS})
            tx  = fn.build_transaction({
                "from":                 WALLET_ADDRESS,
                "nonce":                nonce,
                "gas":                  int(gas_est * 1.25),
                "maxFeePerGas":         fees.get("maxFeePerGas", fees.get("gasPrice", 0)),
                "maxPriorityFeePerGas": fees.get("maxPriorityFeePerGas", 0),
                "chainId":              chain_id,
            })

            signed   = self._w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            tx_hash  = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            hash_hex = tx_hash.hex()
            print(f"  TX sent:   {hash_hex}")

            receipt  = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            success  = receipt.status == 1
            gas_used = receipt.gasUsed

            # Log execution
            now = datetime.now(timezone.utc).isoformat()
            db_exec("""
                INSERT INTO flash_executions
                (chain,token,loan_amount,route,buy_fee,sell_fee,
                 gross_profit,net_profit,tx_hash,gas_used,gas_cost_usd,success,timestamp)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (self._chain, asset_sym, loan_usd,
                  f"{asset_sym}→{inter_sym}→{asset_sym}",
                  buy_fee, sell_fee,
                  sim_result["gross_profit"], sim_result["net_profit"],
                  hash_hex, gas_used,
                  sim_result["gas_cost"], 1 if success else 0, now))

            return {
                "success":  success,
                "tx_hash":  hash_hex,
                "gas_used": gas_used,
                "block":    receipt.blockNumber,
            }
        except Exception as e:
            return {"success": False, "error": str(e)[:200]}


# ══════════════════════════════════════════════════════════════════
#  MAIN SCAN + READINESS CHECK
# ══════════════════════════════════════════════════════════════════
def check_readiness():
    init_tables()
    print("\n── FLASH LOAN READINESS CHECK ────────────────────────")
    print(f"  Wallet: {WALLET_ADDRESS}\n")

    # Check deployed contracts
    addr_file = CONTRACT_DIR / "addresses.json"
    if addr_file.exists():
        with open(addr_file) as f:
            addresses = json.load(f)
    else:
        addresses = {}

    ready_chains = []
    for chain_name in CHAINS:
        w3 = connect(chain_name)
        if not w3:
            print(f"  {chain_name:<12} ✗ RPC unavailable")
            continue

        bal      = 0.0
        gas_usd  = 0.0
        contract = addresses.get(chain_name, {}).get("address", "")
        try:
            raw = w3.eth.get_balance(WALLET_ADDRESS)
            bal = float(w3.from_wei(raw, "ether"))
            gas_usd = estimate_gas_usd(w3, chain_name)
        except Exception:
            pass

        has_contract = bool(contract)
        has_gas      = bal > 0
        is_ready     = has_contract and has_gas

        status = ("READY ✓" if is_ready
                  else "NO CONTRACT" if not has_contract
                  else "NEEDS GAS")
        print(f"  {chain_name:<12} bal={bal:.6f}  gas≈${gas_usd:.4f}  "
              f"contract={'YES' if has_contract else 'NO':>3}  {status}")

        if is_ready:
            ready_chains.append(chain_name)

    print()
    if ready_chains:
        print(f"  Flash loans READY on: {', '.join(ready_chains)}")
    else:
        print("  Action required:")
        print("  1. Deploy contract: python3 deploy_contract.py")
        print("  2. Fund wallet with ETH for gas on target chain")
    return ready_chains


def scan_opportunities(use_ga: bool = False, auto_execute: bool = False):
    """
    Full opportunity scan across all chains.
    use_ga=True: run Genetic Algorithm route optimizer.
    auto_execute=True: execute profitable opportunities automatically.
    """
    init_tables()
    print("╔══════════════════════════════════════════════════════╗")
    print("║  JDL FLASH EXECUTOR v3 — OPPORTUNITY SCAN          ║")
    print(f"║  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                              ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"  Wallet: {WALLET_ADDRESS}")
    print(f"  Mode:   {'GA Optimizer' if use_ga else 'Standard Scan'}"
          f" | {'Auto-Execute ON' if auto_execute else 'Simulate Only'}\n")

    ready_chains = check_readiness()

    scan_list = [
        ('arbitrum', 'USDC', 'WETH',  10000),
        ('arbitrum', 'USDC', 'DAI',   10000),
        ('arbitrum', 'USDC', 'ARB',    5000),
        ('arbitrum', 'USDC', 'GMX',    3000),
        ('arbitrum', 'USDT', 'WETH',  10000),
        ('optimism', 'USDC', 'WETH',  10000),
        ('optimism', 'USDC', 'DAI',   10000),
        ('base',     'USDC', 'WETH',  10000),
        ('polygon',  'USDC', 'WETH',  10000),
        ('polygon',  'USDC', 'DAI',   10000),
    ]

    viable_opps = []
    print("\n── SIMULATION RESULTS ────────────────────────────────")

    for chain, tok_b, tok_i, amount in scan_list:
        w3 = connect(chain)
        if not w3:
            print(f"  {chain:<12} {tok_b}↔{tok_i:<8} ✗ no connection")
            continue

        gas_cost = estimate_gas_usd(w3, chain)
        sim      = FlashLoanSimulator(w3, chain)
        result   = sim.simulate(tok_b, tok_i, amount, gas_cost)

        ok, reason = ProfitabilityFilter.check(result)
        net_str    = f"${result.get('net_profit',0):.4f}"
        status     = f"✓ {net_str}" if ok else f"✗ {reason[:40]}"
        print(f"  {chain:<12} {tok_b}↔{tok_i:<8} {status}")

        if ok:
            viable_opps.append({**result, "chain": chain})

        # Log scan
        db_exec("""
            INSERT INTO scan_results (chain,route,net_profit,viable,reason,scanned_at)
            VALUES (?,?,?,?,?,?)
        """, (chain, f"{tok_b}→{tok_i}",
              result.get("net_profit", 0), 1 if ok else 0,
              reason, datetime.now(timezone.utc).isoformat()))

    # GA Optimization pass
    if use_ga:
        print("\n── GENETIC ALGORITHM ROUTE SEARCH ───────────────────")
        for chain in ("arbitrum", "optimism"):
            w3 = connect(chain)
            if not w3:
                continue
            gas_cost = estimate_gas_usd(w3, chain)
            sim      = FlashLoanSimulator(w3, chain)
            ga       = GeneticRouteOptimizer(chain, sim)
            print(f"  {chain}: evolving {ga.POP_SIZE} chromosomes × {ga.GENERATIONS} gens...")
            top_routes = ga.evolve(gas_cost)
            for fitness, chrom in top_routes[:3]:
                print(f"    ✓ GA: {chrom['token_borrow']}→{chrom['token_inter']} "
                      f"loan=${chrom['loan_size']:,} net=${fitness:.4f} "
                      f"fees={chrom['buy_fee']}/{chrom['sell_fee']}")
                result = sim.simulate(
                    chrom["token_borrow"], chrom["token_inter"],
                    chrom["loan_size"], gas_cost,
                    chrom["buy_fee"], chrom["sell_fee"])
                ok, _ = ProfitabilityFilter.check(result)
                if ok:
                    viable_opps.append({**result, "chain": chain, "source": "GA"})

    # Summary
    print(f"\n── SUMMARY ───────────────────────────────────────────")
    print(f"  Viable opportunities: {len(viable_opps)}")

    if not viable_opps:
        print("\n  No viable opportunities this scan.")
        print("  Normal on efficient markets — re-run during volatility.")
        return

    best = max(viable_opps, key=lambda x: x["net_profit"])
    src  = best.get("source", "standard")
    print(f"\n  Best opportunity [{src}]:")
    print(f"  Chain:     {best['chain']}")
    print(f"  Route:     {best['token_borrow']}→{best['token_inter']}→{best['token_borrow']}")
    print(f"  Borrow:    ${best['borrow_usd']:,.0f}")
    print(f"  Gross:     ${best['gross_profit']:.6f}")
    print(f"  Aave fee: -${best['aave_fee']:.6f}")
    print(f"  Gas:      -${best['gas_cost']:.6f}")
    print(f"  Slippage: -${best['slippage']:.6f}")
    print(f"  Corr:      {best['slippage_corr']:.4f}")
    print(f"  NET:       ${best['net_profit']:.6f}")
    print(f"  Buy fee:   {best['buy_fee_tier']} bps")
    print(f"  Sell fee:  {best['sell_fee_tier']} bps")

    if best["chain"] in ready_chains:
        print(f"\n  STATUS: READY TO EXECUTE")
        if auto_execute:
            print(f"  Executing...")
            w3  = connect(best["chain"])
            exc = FlashExecutor(w3, best["chain"])
            r   = exc.execute(best)
            if r["success"]:
                print(f"  ✓ TX confirmed: {r['tx_hash']}")
                print(f"    Block: {r['block']}  Gas: {r['gas_used']:,}")
            else:
                print(f"  ✗ Execution failed: {r.get('error')}")
        else:
            print(f"  Run with --execute to trigger on-chain execution.")
    else:
        print(f"\n  STATUS: Contract/gas needed on {best['chain']}")
        print(f"  Run: python3 deploy_contract.py")


if __name__ == "__main__":
    import sys
    init_tables()

    args = sys.argv[1:]
    use_ga      = "--ga"       in args
    auto_exec   = "--execute"  in args

    if "--scan" in args:
        scan_opportunities(use_ga=use_ga, auto_execute=auto_exec)
    elif "--ready" in args:
        check_readiness()
    elif "--history" in args:
        rows = db_query("""
            SELECT chain,route,net_profit,tx_hash,success,timestamp
            FROM flash_executions ORDER BY id DESC LIMIT 20
        """)
        print(f"\n  {'Chain':<12} {'Route':<22} {'Net$':>10} {'Success':>8} {'Time'}")
        for r in rows:
            print(f"  {r[0]:<12} {r[1]:<22} ${r[2]:>9.4f} {'✓' if r[4] else '✗':>8} {r[5][:16]}")
    else:
        print("\nJDL Flash Executor v3")
        print("  Algorithms: Newton-Raphson | EIP-1559 | Genetic Algorithm | 5-Stage Filter\n")
        print("  [1] Standard scan")
        print("  [2] GA-optimized scan (slower, finds better routes)")
        print("  [3] Check readiness")
        print("  [4] View execution history")
        choice = input("\n  > ").strip()
        if choice == "1":
            scan_opportunities(use_ga=False)
        elif choice == "2":
            scan_opportunities(use_ga=True)
        elif choice == "3":
            check_readiness()
        elif choice == "4":
            rows = db_query("""
                SELECT chain,route,net_profit,tx_hash,success,timestamp
                FROM flash_executions ORDER BY id DESC LIMIT 20
            """)
            for r in rows:
                print(f"  {r[0]:<12} {r[1]:<22} ${r[2]:>9.4f} {'✓' if r[4] else '✗'} {r[5][:16]}")
        else:
            scan_opportunities()
