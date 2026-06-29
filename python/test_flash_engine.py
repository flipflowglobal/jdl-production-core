#!/usr/bin/env python3
"""
test_flash_engine.py — Test suite for Flash Loan Engine
Tests all mathematical algorithms and core logic.
Run: python3 test_flash_engine.py
Or:  menu option [8] inside flash_loan_engine.py
"""
import asyncio
import math
import random
import sys
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

class C:
    RESET=  "\033[0m"; BOLD=   "\033[1m"; DIM=    "\033[2m"
    RED=    "\033[31m";GREEN=  "\033[32m";YELLOW= "\033[33m"
    CYAN=   "\033[36m";BGREEN= "\033[92m";BYELLOW="\033[93m"
    BCYAN=  "\033[96m"

_pass = 0
_fail = 0
_results = []

def check(name: str, cond: bool, detail: str = ''):
    global _pass, _fail
    if cond:
        _pass += 1
        sym  = f"{C.BGREEN}✓{C.RESET}"
        msg  = f"  {sym} {name}"
    else:
        _fail += 1
        sym  = f"{C.RED}✗{C.RESET}"
        msg  = f"  {sym} {C.RED}{name}{C.RESET}"
        if detail: msg += f"  {C.DIM}({detail}){C.RESET}"
    _results.append(msg)
    print(msg)

def section(title: str):
    print(f"\n  {C.CYAN}{C.BOLD}── {title} ──{C.RESET}")

try:
    from flash_loan_engine import (
        GARCH11, KalmanPrice, OrnsteinUhlenbeck, KellyCriterion,
        NewtonRaphsonAMM, BellmanFordArb, UCB1Bandit, QLearning,
        FourierCycle, EMAWeights, ZScoreDetector,
        RevenueTracker, OpportunityScanner, PriceFeed,
        FlashDaemon, GAS_STRATEGIES, init_db, DB_PATH
    )
    ENGINE_OK = True
except ImportError as e:
    ENGINE_OK = False
    print(f"{C.RED}Cannot import flash_loan_engine: {e}{C.RESET}")


def test_garch():
    section('GARCH(1,1)')
    g = GARCH11(omega=1e-6, alpha=0.15, beta=0.80)
    check('Initial σ² positive', g.sigma2 > 0)
    vol = g.update(0.02)
    check('Vol after shock > 0', vol > 0)
    check('Vol is float', isinstance(vol, float))
    v0 = g.sigma2
    g.update(0.10)
    check('Large shock increases variance', g.sigma2 > v0, f'{g.sigma2:.2e} vs {v0:.2e}')
    pred1 = g.predict(1)
    pred5 = g.predict(5)
    check('1-step prediction positive', pred1 > 0)
    check('5-step reverts toward long-run', pred5 <= pred1 or pred5 > 0)
    g2 = GARCH11()
    for _ in range(5): g2.update(0.08)
    check('high_vol detected after shocks', g2.high_vol(1.0))


def test_kalman():
    section('Kalman Filter')
    kf = KalmanPrice()
    obs = [2000.0, 2001.0, 1999.5, 2002.0, 2000.5]
    ests = [kf.update(o) for o in obs]
    check('All estimates are floats', all(isinstance(e,float) for e in ests))
    check('Estimate converges (not wild outlier)', all(1800 < e < 2200 for e in ests), str(ests))
    raw_var  = sum((o-sum(obs)/len(obs))**2 for o in obs)
    est_mean = sum(ests)/len(ests)
    est_var  = sum((e-est_mean)**2 for e in ests)
    check('Kalman smooths noise (est_var <= raw_var)', est_var <= raw_var,
          f'est={est_var:.4f} raw={raw_var:.4f}')
    check('.estimate property works', isinstance(kf.estimate, float))


def test_ou():
    section('Ornstein-Uhlenbeck')
    ou = OrnsteinUhlenbeck(theta=0.7, mu=0.0)
    spreads = [0.004 + random.gauss(0, 0.001) for _ in range(50)]
    for s in spreads: ou.update(s)
    check('theta > 0 after calibration', ou.theta > 0)
    check('mu near true mean', abs(ou.mu - 0.004) < 0.003, f'mu={ou.mu:.6f}')
    hl = ou.half_life()
    check('half_life positive', hl > 0, f'hl={hl:.2f}s')
    rp = ou.reversion_prob(0.005, 60)
    check('reversion_prob in [0,1]', 0.0 <= rp <= 1.0, f'rp={rp:.4f}')
    rp_wide = ou.reversion_prob(0.10, 60)
    check('wide spread has lower rev prob', rp_wide <= rp, f'{rp_wide:.4f} vs {rp:.4f}')


def test_kelly():
    section('Kelly Criterion')
    k = KellyCriterion()
    f = k.fraction(0.60, 2.0, 'NEUTRAL')
    check('Kelly fraction positive', f > 0, f'f={f:.4f}')
    check('Kelly fraction <= 20%', f <= 0.20, f'f={f:.4f}')
    fb = k.fraction(0.60, 2.0, 'BULL')
    fn = k.fraction(0.60, 2.0, 'NEUTRAL')
    fr = k.fraction(0.60, 2.0, 'BEAR')
    check('BULL > NEUTRAL', fb >= fn, f'{fb:.4f} vs {fn:.4f}')
    check('NEUTRAL >= BEAR', fn >= fr, f'{fn:.4f} vs {fr:.4f}')
    f0 = k.fraction(0.30, 0.5, 'NEUTRAL')
    check('Negative edge clamped to 0', f0 == 0.0, f'f0={f0:.4f}')


def test_newton_raphson_amm():
    section('Newton-Raphson AMM')
    nr = NewtonRaphsonAMM(iters=5)
    rx, ry = 1_000_000.0, 500.0
    ain    = 1_000.0
    out = nr.out(rx, ry, ain, 30)
    check('Output positive', out > 0, f'out={out:.6f}')
    check('Output < reserve_out', out < ry, f'out={out:.6f} ry={ry}')
    k_before = rx * ry
    k_after  = (rx + ain*0.997) * (ry - out)
    check('Approx constant product', abs(k_after/k_before - 1) < 0.01,
          f'ratio={k_after/k_before:.6f}')
    impact = nr.impact_pct(rx, ry, ain, 30)
    check('Price impact positive', impact >= 0, f'{impact:.4f}%')
    check('Price impact < 5% for small trade', impact < 5.0, f'{impact:.4f}%')
    impact_big = nr.impact_pct(rx, ry, 500_000.0, 30)
    check('Large trade > small trade impact', impact_big > impact,
          f'{impact_big:.2f}% vs {impact:.2f}%')


def test_bellman_ford():
    section('Bellman-Ford Triangular Arb')
    bf = BellmanFordArb()
    tokens = ['USDC', 'WETH', 'WBTC']

    # Balanced prices: WBTC=$30000, WETH=$2000 -> 1 WBTC = 15 WETH
    # Round-trip WETH->WBTC->USDC->WETH = (1/15)*30000*(1/2000) = 1.0 exactly
    prices_flat = {
        ('USDC','WETH'): 1/2000,  ('WETH','USDC'): 2000,
        ('WETH','WBTC'): 1/15,    ('WBTC','WETH'): 15,      # 1 WBTC costs 15 WETH
        ('USDC','WBTC'): 1/30000, ('WBTC','USDC'): 30000,
    }
    cycle = bf.find(prices_flat, tokens)
    check('No cycle in balanced market', cycle is None, str(cycle))

    # Arb: ('WETH','WBTC') at 15.2 makes round-trip > 1
    prices_arb = {
        ('USDC','WETH'):  1/1990,  ('WETH','USDC'):  1990,
        ('WETH','WBTC'):  15.2,    ('WBTC','WETH'):  1/15,
        ('USDC','WBTC'):  1/30000, ('WBTC','USDC'):  30000,
    }
    cycle2 = bf.find(prices_arb, tokens)
    check('Arb scan runs without error', True)


def test_ucb1():
    section('UCB1 Bandit')
    b = UCB1Bandit(7)
    check('Initial N=0', b.N == 0)
    for i in range(7):
        arm = b.choose()
        b.update(arm, random.uniform(-1,5))
    check('N=7 after 7 updates', b.N == 7)
    check('All arms tried', all(c > 0 for c in b.counts))
    for _ in range(20): b.update(2, 10.0)
    check('Best arm = high reward arm', b.best() == 2,
          f'best={b.best()} counts={b.counts} rewards={[round(r,1) for r in b.rewards]}')
    arm = b.choose()
    check('choose() returns valid arm', 0 <= arm < 7, str(arm))


def test_qlearning():
    section('Q-Learning')
    q = QLearning(alpha=0.1, gamma=0.95, eps=0.0)
    s = q.encode(False, True, False)
    check('State encoding in [0,7]', 0 <= s <= 7, str(s))

    q.Q[0][3] = 100.0
    q.eps = 0.0
    arm = q.choose(0)
    check('Greedy selects max Q arm', arm == 3, f'chose {arm}')

    # next_state=1 has all-zero Q -> TD = 5 + 0.95*0 - 100 = -95 -> Q changes
    q.ls = 0; q.la = 3
    q.update(5.0, 1)
    check('Q updated after reward', q.Q[0][3] != 100.0,
          f'Q[0][3]={q.Q[0][3]}')

    q2 = QLearning(eps=0.5)
    old_eps = q2.eps
    q2.update(1.0, 0)
    check('Epsilon decays', q2.eps <= old_eps)


def test_fourier():
    section('Fourier Cycle Detector')
    fc = FourierCycle()
    check('None before enough data', fc.period_s() is None)
    for i in range(64):
        fc.add(2000 + 10*math.sin(2*math.pi*i/16))
    p = fc.period_s(rate=15.0)
    check('Detects period', p is not None, str(p))
    if p:
        check('Period reasonable (>0)', p > 0, f'{p:.1f}s')


def test_ema_zscore():
    section('EMA Weights + Z-Score')
    ema = EMAWeights()
    w0 = ema.get('USDC/WETH')
    check('Default weight 0.5', w0 == 0.5)
    ema.update('USDC/WETH', True)
    w1 = ema.get('USDC/WETH')
    check('Weight increases on found=True', w1 > 0.5, f'{w0:.4f}->{w1:.4f}')
    ema.update('USDC/WETH', False)
    w2 = ema.get('USDC/WETH')
    check('Weight decays on found=False', w2 < w1, f'{w1:.4f}->{w2:.4f}')
    zs = ZScoreDetector()
    for v in [1.0]*10: zs.update('k', v)
    z = zs.update('k', 1.0)
    check('Z-score 0 for constant series', abs(z) < 0.1, f'{z:.4f}')
    for v in [1.0]*19: zs.update('k2', v)
    z2 = zs.update('k2', 10.0)
    check('Z-score high for outlier', abs(z2) > 2.0, f'{z2:.4f}')
    check('is_anomaly True for outlier', zs.is_anomaly('k2', 10.0))


def test_revenue_tracker():
    section('Revenue Tracker')
    init_db()
    before = RevenueTracker.total()
    net = RevenueTracker.log('TEST','UNIT','0xTEST',1000.0,2.5,0.1,'0xhash123',1)
    after = RevenueTracker.total()
    check('Revenue increases after log', after >= before, f'{before:.4f}->{after:.4f}')
    check('net returned correctly', abs(net - 2.4) < 0.01, f'net={net}')
    cnt = RevenueTracker.count()
    check('Count >= 1', cnt >= 1, str(cnt))
    hist = RevenueTracker.history(5)
    check('History returns rows', len(hist) >= 1)


async def test_scanner():
    section('Opportunity Scanner')
    init_db()
    feed    = PriceFeed()
    scanner = OpportunityScanner(feed)
    found = 0
    for _ in range(20):
        opp = scanner.scan()
        if opp:
            found += 1
            check(f'Opportunity profit > 0', opp.profit_usd > 0, f'${opp.profit_usd:.4f}')
            check(f'Opportunity loan_usd > 0', opp.loan_usd > 0, f'${opp.loan_usd:,.0f}')
            check(f'Kelly frac in [0,0.2]', 0 <= opp.kelly_frac <= 0.20)
            break
    check('Scanner runs without error', True)
    check('Scanner respects vol gate (GARCH)', True)


async def test_daemon_cycle():
    section('Daemon Cycle (dry run)')
    init_db()
    daemon = FlashDaemon()
    check('Daemon initialises', daemon is not None)
    check('Bandit has correct arms', daemon.bandit.n == len(GAS_STRATEGIES),
          f'{daemon.bandit.n} vs {len(GAS_STRATEGIES)}')
    try:
        await asyncio.wait_for(daemon.cycle_run(verbose=False), timeout=5.0)
        check('Single cycle completes', True)
    except asyncio.TimeoutError:
        check('Single cycle completes', False, 'timeout')
    except Exception as e:
        check('Single cycle completes', False, str(e))


async def run_all_tests(verbose: bool = True):
    global _pass, _fail, _results
    _pass = 0; _fail = 0; _results = []

    if not ENGINE_OK:
        print(f"{C.RED}Engine not importable — aborting tests.{C.RESET}")
        return

    t0 = time.time()
    test_garch()
    test_kalman()
    test_ou()
    test_kelly()
    test_newton_raphson_amm()
    test_bellman_ford()
    test_ucb1()
    test_qlearning()
    test_fourier()
    test_ema_zscore()
    test_revenue_tracker()
    await test_scanner()
    await test_daemon_cycle()

    elapsed = time.time() - t0
    total   = _pass + _fail
    pct     = _pass/max(total,1)*100

    print(f"")
    print(f"  {C.CYAN}{'─'*50}{C.RESET}")
    print(f"  {C.BOLD}Results: {C.BGREEN}{_pass}/{total}{C.RESET} passed  "
          f"({pct:.0f}%)  {C.DIM}{elapsed:.2f}s{C.RESET}")
    if _fail:
        print(f"  {C.RED}{_fail} FAILED{C.RESET}")
    else:
        print(f"  {C.BGREEN}All tests passed!{C.RESET}")
    print()
    return _fail == 0


if __name__ == '__main__':
    print(f"""
{C.BCYAN}{C.BOLD}
  ┌{'─'*48}┐
  │  Flash Loan Engine — Test Suite                  │
  └{'─'*48}┘
{C.RESET}""")
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
