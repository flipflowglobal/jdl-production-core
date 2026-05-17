export interface StrategyConfig {
  id: string;
  name: string;
  category: "arbitrage" | "accumulation" | "trend" | "mean-reversion" | "range" | "statistical" | "execution";
  description: string;
  riskLevel: "conservative" | "balanced" | "aggressive";
  minCapital: number;
  expectedWinRate: number;
  expectedMonthlyReturn: number;
  supportedChains: string[];
  parameters: StrategyParameter[];
  algorithm: string;
}

export interface StrategyParameter {
  key: string;
  label: string;
  type: "number" | "boolean" | "select";
  default: any;
  min?: number;
  max?: number;
  options?: string[];
  description: string;
}

export const STRATEGIES: StrategyConfig[] = [
  {
    id: "triangular-arb",
    name: "Triangular Arbitrage",
    category: "arbitrage",
    description: "Detects price inefficiencies across 3 trading pairs on DEXs. Uses Bellman-Ford shortest path algorithm to find negative-weight cycles indicating profitable triangular routes. Executes atomic flash loan swaps through Aave V3.",
    riskLevel: "aggressive",
    minCapital: 5000,
    expectedWinRate: 87.2,
    expectedMonthlyReturn: 8.5,
    supportedChains: ["ethereum", "polygon", "arbitrum", "bsc", "optimism"],
    parameters: [
      { key: "minSpread", label: "Minimum Spread %", type: "number", default: 0.3, min: 0.1, max: 2.0, description: "Minimum price spread to trigger execution" },
      { key: "maxGasGwei", label: "Max Gas (Gwei)", type: "number", default: 50, min: 5, max: 200, description: "Maximum gas price for execution" },
      { key: "flashLoanEnabled", label: "Flash Loan", type: "boolean", default: true, description: "Use Aave V3 flash loans for zero-capital execution" },
      { key: "dexSelection", label: "DEX Pool", type: "select", default: "all", options: ["all", "uniswap-v3", "sushiswap", "curve", "balancer"], description: "DEX pools to scan" },
    ],
    algorithm: "bellman-ford-negative-cycle",
  },
  {
    id: "statistical-arb",
    name: "Statistical Arbitrage",
    category: "statistical",
    description: "Identifies co-integrated token pairs using Engle-Granger two-step method. Computes z-score of spread and trades mean reversion. Uses Ornstein-Uhlenbeck process for half-life estimation and optimal entry/exit timing.",
    riskLevel: "balanced",
    minCapital: 3000,
    expectedWinRate: 71.8,
    expectedMonthlyReturn: 6.2,
    supportedChains: ["ethereum", "polygon", "arbitrum"],
    parameters: [
      { key: "lookbackPeriod", label: "Lookback (hours)", type: "number", default: 168, min: 24, max: 720, description: "Historical window for cointegration test" },
      { key: "zScoreEntry", label: "Z-Score Entry", type: "number", default: 2.0, min: 1.0, max: 3.0, description: "Standard deviations from mean to enter" },
      { key: "zScoreExit", label: "Z-Score Exit", type: "number", default: 0.5, min: 0.0, max: 1.5, description: "Standard deviations from mean to exit" },
      { key: "halfLifeMax", label: "Max Half-Life (h)", type: "number", default: 48, min: 4, max: 168, description: "Maximum O-U half-life to accept" },
    ],
    algorithm: "engle-granger-cointegration-ou-process",
  },
  {
    id: "grid-trading",
    name: "Grid Trading",
    category: "range",
    description: "Places buy/sell limit orders at predefined price intervals creating a grid. Profits from price oscillation within range. Uses Fibonacci retracement levels for optimal grid placement and ATR (Average True Range) for dynamic grid spacing.",
    riskLevel: "balanced",
    minCapital: 2000,
    expectedWinRate: 68.5,
    expectedMonthlyReturn: 5.1,
    supportedChains: ["ethereum", "polygon", "arbitrum", "bsc"],
    parameters: [
      { key: "gridLevels", label: "Grid Levels", type: "number", default: 10, min: 5, max: 50, description: "Number of price levels in the grid" },
      { key: "gridSpacing", label: "Spacing Method", type: "select", default: "fibonacci", options: ["uniform", "fibonacci", "atr-adaptive"], description: "How grid levels are spaced" },
      { key: "rangeWidth", label: "Range Width %", type: "number", default: 10, min: 2, max: 30, description: "Total price range as percentage of current price" },
      { key: "rebalanceOnBreak", label: "Auto-Rebalance", type: "boolean", default: true, description: "Rebalance grid when price breaks range" },
    ],
    algorithm: "fibonacci-atr-grid",
  },
  {
    id: "dca-smart",
    name: "Smart DCA",
    category: "accumulation",
    description: "Enhanced Dollar Cost Averaging using RSI-weighted buying. Increases allocation when RSI < 30 (oversold), decreases when RSI > 70 (overbought). Incorporates VWAP deviation and Fear & Greed Index for timing optimization.",
    riskLevel: "conservative",
    minCapital: 500,
    expectedWinRate: 72.3,
    expectedMonthlyReturn: 3.8,
    supportedChains: ["ethereum", "polygon", "arbitrum", "bsc", "avalanche", "optimism"],
    parameters: [
      { key: "interval", label: "Buy Interval", type: "select", default: "4h", options: ["1h", "4h", "daily", "weekly"], description: "How often to execute buys" },
      { key: "rsiPeriod", label: "RSI Period", type: "number", default: 14, min: 7, max: 30, description: "RSI calculation lookback period" },
      { key: "oversoldMultiplier", label: "Oversold Boost", type: "number", default: 2.0, min: 1.0, max: 5.0, description: "Multiplier for allocation when RSI < 30" },
      { key: "targetToken", label: "Target Token", type: "select", default: "ETH", options: ["ETH", "BTC", "MATIC", "ARB", "OP", "AVAX"], description: "Token to accumulate" },
    ],
    algorithm: "rsi-vwap-weighted-dca",
  },
  {
    id: "momentum",
    name: "Momentum / Trend Following",
    category: "trend",
    description: "Detects trend direction using dual EMA crossover (fast/slow) confirmed by MACD histogram divergence. Position sizing via Kelly Criterion. Stop-loss placed at 2x ATR below entry. Uses Hurst exponent to confirm trending vs mean-reverting regime.",
    riskLevel: "aggressive",
    minCapital: 3000,
    expectedWinRate: 64.7,
    expectedMonthlyReturn: 7.3,
    supportedChains: ["ethereum", "polygon", "arbitrum", "bsc"],
    parameters: [
      { key: "fastEma", label: "Fast EMA Period", type: "number", default: 12, min: 5, max: 30, description: "Fast exponential moving average period" },
      { key: "slowEma", label: "Slow EMA Period", type: "number", default: 26, min: 20, max: 100, description: "Slow exponential moving average period" },
      { key: "atrMultiplier", label: "ATR Stop Loss", type: "number", default: 2.0, min: 1.0, max: 4.0, description: "ATR multiplier for trailing stop" },
      { key: "hurstThreshold", label: "Hurst Threshold", type: "number", default: 0.55, min: 0.5, max: 0.8, description: "Min Hurst exponent to confirm trend" },
    ],
    algorithm: "ema-crossover-macd-hurst-kelly",
  },
  {
    id: "mean-reversion",
    name: "Mean Reversion",
    category: "mean-reversion",
    description: "Identifies overbought/oversold conditions using Bollinger Bands (2σ), RSI, and Stochastic RSI triple confirmation. Enters when price touches outer band with RSI confirmation. Uses Kalman Filter for adaptive mean estimation.",
    riskLevel: "balanced",
    minCapital: 2000,
    expectedWinRate: 66.1,
    expectedMonthlyReturn: 5.5,
    supportedChains: ["ethereum", "polygon", "arbitrum"],
    parameters: [
      { key: "bbPeriod", label: "BB Period", type: "number", default: 20, min: 10, max: 50, description: "Bollinger Bands calculation period" },
      { key: "bbStdDev", label: "BB Std Dev", type: "number", default: 2.0, min: 1.5, max: 3.0, description: "Standard deviations for bands" },
      { key: "rsiThreshold", label: "RSI Threshold", type: "number", default: 30, min: 20, max: 40, description: "RSI level for oversold confirmation" },
      { key: "kalmanAdaptive", label: "Kalman Filter", type: "boolean", default: true, description: "Use Kalman Filter for adaptive mean" },
    ],
    algorithm: "bollinger-rsi-stochastic-kalman",
  },
  {
    id: "flash-loan-arb",
    name: "Flash Loan Arbitrage",
    category: "arbitrage",
    description: "Zero-capital arbitrage using Aave V3 flash loans. Scans cross-DEX price differences in real-time. Executes borrow→swap→repay in single atomic transaction. Uses mempool monitoring for front-run protection and MEV-aware gas bidding.",
    riskLevel: "aggressive",
    minCapital: 0,
    expectedWinRate: 94.1,
    expectedMonthlyReturn: 12.4,
    supportedChains: ["ethereum", "polygon", "arbitrum"],
    parameters: [
      { key: "minProfitUsd", label: "Min Profit ($)", type: "number", default: 50, min: 10, max: 1000, description: "Minimum net profit after gas to execute" },
      { key: "maxLoanUsd", label: "Max Loan ($)", type: "number", default: 500000, min: 10000, max: 5000000, description: "Maximum flash loan amount" },
      { key: "mevProtection", label: "MEV Protection", type: "boolean", default: true, description: "Use Flashbots for front-run protection" },
      { key: "scanInterval", label: "Scan Interval (ms)", type: "number", default: 500, min: 100, max: 5000, description: "How often to scan for opportunities" },
    ],
    algorithm: "cross-dex-flash-loan-mev-protected",
  },
  {
    id: "vwap-twap",
    name: "VWAP/TWAP Execution",
    category: "execution",
    description: "Institutional-grade execution algorithm. Splits large orders into smaller chunks executed over time to minimize market impact. VWAP targets volume-weighted average price; TWAP uses time-weighted distribution. Includes participation rate limiter.",
    riskLevel: "conservative",
    minCapital: 10000,
    expectedWinRate: 82.5,
    expectedMonthlyReturn: 2.1,
    supportedChains: ["ethereum", "polygon", "arbitrum", "bsc", "optimism"],
    parameters: [
      { key: "executionType", label: "Type", type: "select", default: "vwap", options: ["vwap", "twap"], description: "VWAP or TWAP execution" },
      { key: "duration", label: "Duration (min)", type: "number", default: 60, min: 10, max: 480, description: "Total execution window" },
      { key: "maxParticipation", label: "Max Volume %", type: "number", default: 5, min: 1, max: 20, description: "Maximum % of volume per interval" },
      { key: "urgency", label: "Urgency", type: "select", default: "normal", options: ["passive", "normal", "aggressive"], description: "Trade completion urgency" },
    ],
    algorithm: "vwap-twap-participation-rate",
  },
  {
    id: "pairs-trading",
    name: "Pairs Trading",
    category: "statistical",
    description: "Long/short strategy on correlated token pairs. Uses Johansen cointegration test for pair selection, Kalman Filter for dynamic hedge ratio, and z-score for entry/exit signals. Market-neutral exposure minimizes directional risk.",
    riskLevel: "balanced",
    minCapital: 5000,
    expectedWinRate: 69.3,
    expectedMonthlyReturn: 4.8,
    supportedChains: ["ethereum", "polygon", "arbitrum"],
    parameters: [
      { key: "correlationMin", label: "Min Correlation", type: "number", default: 0.85, min: 0.7, max: 0.99, description: "Minimum Pearson correlation for pair" },
      { key: "hedgeRatio", label: "Hedge Method", type: "select", default: "kalman", options: ["ols", "kalman", "rolling-ols"], description: "Hedge ratio estimation method" },
      { key: "entryZ", label: "Entry Z-Score", type: "number", default: 2.0, min: 1.0, max: 3.0, description: "Z-score threshold for entry" },
      { key: "maxHolding", label: "Max Hold (hours)", type: "number", default: 72, min: 12, max: 168, description: "Maximum position holding time" },
    ],
    algorithm: "johansen-kalman-zscore-neutral",
  },
  {
    id: "breakout-scalper",
    name: "Breakout Scalper",
    category: "trend",
    description: "Detects price breakouts from consolidation using Donchian Channels and volume surge detection. Enters on channel breakout confirmed by volume > 2x average. Uses Ichimoku Cloud for trend confirmation and dynamic support/resistance identification.",
    riskLevel: "aggressive",
    minCapital: 2000,
    expectedWinRate: 58.4,
    expectedMonthlyReturn: 9.2,
    supportedChains: ["ethereum", "polygon", "arbitrum", "bsc"],
    parameters: [
      { key: "channelPeriod", label: "Channel Period", type: "number", default: 20, min: 10, max: 50, description: "Donchian Channel lookback" },
      { key: "volumeMultiplier", label: "Volume Surge", type: "number", default: 2.0, min: 1.5, max: 5.0, description: "Volume multiple to confirm breakout" },
      { key: "ichimokuConfirm", label: "Ichimoku Confirm", type: "boolean", default: true, description: "Require Ichimoku Cloud confirmation" },
      { key: "scalperMode", label: "Scalp Mode", type: "boolean", default: false, description: "Enable rapid small-profit scalping" },
    ],
    algorithm: "donchian-volume-ichimoku-breakout",
  },
];

export function kellyCriterion(winRate: number, avgWin: number, avgLoss: number): number {
  const p = winRate / 100;
  const q = 1 - p;
  const b = avgWin / Math.max(avgLoss, 0.001);
  const kelly = (p * b - q) / b;
  return Math.max(0, Math.min(kelly, 0.5));
}

export function sharpeRatio(returns: number[], riskFreeRate: number = 0.04): number {
  if (returns.length < 2) return 0;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((sum, r) => sum + (r - mean) ** 2, 0) / (returns.length - 1);
  const stdDev = Math.sqrt(variance);
  if (stdDev === 0) return 0;
  return ((mean - riskFreeRate / 365) * Math.sqrt(365)) / stdDev;
}

export function maxDrawdown(equityCurve: number[]): number {
  if (equityCurve.length < 2) return 0;
  let peak = equityCurve[0];
  let maxDd = 0;
  for (const val of equityCurve) {
    if (val > peak) peak = val;
    const dd = (peak - val) / peak;
    if (dd > maxDd) maxDd = dd;
  }
  return maxDd;
}

export function shapleyWeights(engineAccuracies: Record<string, number>): Record<string, number> {
  const engines = Object.keys(engineAccuracies);
  const n = engines.length;
  const factorial = (k: number): number => (k <= 1 ? 1 : k * factorial(k - 1));
  const shapley: Record<string, number> = {};

  for (const engine of engines) {
    let phi = 0;
    const others = engines.filter((e) => e !== engine);
    const totalSubsets = 1 << others.length;

    for (let mask = 0; mask < totalSubsets; mask++) {
      const coalition: string[] = [];
      for (let j = 0; j < others.length; j++) {
        if (mask & (1 << j)) coalition.push(others[j]);
      }
      const sSize = coalition.length;
      const coeff = (factorial(sSize) * factorial(n - sSize - 1)) / factorial(n);
      const vWithout = coalition.length > 0
        ? coalition.reduce((s, e) => s + engineAccuracies[e], 0) / coalition.length
        : 0;
      const withEngine = [...coalition, engine];
      const vWith = withEngine.reduce((s, e) => s + engineAccuracies[e], 0) / withEngine.length;
      phi += coeff * (vWith - vWithout);
    }
    shapley[engine] = phi;
  }

  const total = Object.values(shapley).reduce((a, b) => a + b, 0);
  for (const key of engines) {
    shapley[key] = total > 0 ? shapley[key] / total : 1 / n;
  }
  return shapley;
}

export function compositeScore(
  engineScores: Record<string, number>,
  weights: Record<string, number>
): number {
  let score = 0;
  for (const [engine, weight] of Object.entries(weights)) {
    score += (engineScores[engine] || 0) * weight;
  }
  return score;
}

export function calculateRSI(prices: number[], period: number = 14): number {
  if (prices.length < period + 1) return 50;
  let gains = 0, losses = 0;
  for (let i = prices.length - period; i < prices.length; i++) {
    const change = prices[i] - prices[i - 1];
    if (change > 0) gains += change;
    else losses += Math.abs(change);
  }
  const avgGain = gains / period;
  const avgLoss = losses / period;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

export function bollingerBands(prices: number[], period: number = 20, stdDevMultiplier: number = 2): { upper: number; middle: number; lower: number; zScore: number } {
  if (prices.length < period) return { upper: 0, middle: 0, lower: 0, zScore: 0 };
  const slice = prices.slice(-period);
  const mean = slice.reduce((a, b) => a + b, 0) / period;
  const variance = slice.reduce((sum, p) => sum + (p - mean) ** 2, 0) / period;
  const stdDev = Math.sqrt(variance);
  const currentPrice = prices[prices.length - 1];
  return {
    upper: mean + stdDevMultiplier * stdDev,
    middle: mean,
    lower: mean - stdDevMultiplier * stdDev,
    zScore: stdDev > 0 ? (currentPrice - mean) / stdDev : 0,
  };
}

export function emaCalculate(prices: number[], period: number): number {
  if (prices.length === 0) return 0;
  const k = 2 / (period + 1);
  let ema = prices[0];
  for (let i = 1; i < prices.length; i++) {
    ema = prices[i] * k + ema * (1 - k);
  }
  return ema;
}

export function macdSignal(prices: number[], fastPeriod: number = 12, slowPeriod: number = 26, signalPeriod: number = 9): { macd: number; signal: number; histogram: number } {
  if (prices.length < slowPeriod + signalPeriod) {
    return { macd: 0, signal: 0, histogram: 0 };
  }
  const macdSeries: number[] = [];
  for (let i = slowPeriod; i <= prices.length; i++) {
    const slice = prices.slice(0, i);
    const fast = emaCalculate(slice, fastPeriod);
    const slow = emaCalculate(slice, slowPeriod);
    macdSeries.push(fast - slow);
  }
  const currentMacd = macdSeries[macdSeries.length - 1];
  const signalLine = emaCalculate(macdSeries, signalPeriod);
  return { macd: currentMacd, signal: signalLine, histogram: currentMacd - signalLine };
}

export function atrCalculate(highs: number[], lows: number[], closes: number[], period: number = 14): number {
  if (highs.length < period + 1) return 0;
  const trs: number[] = [];
  for (let i = 1; i < highs.length; i++) {
    const tr = Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1])
    );
    trs.push(tr);
  }
  return trs.slice(-period).reduce((a, b) => a + b, 0) / period;
}

export function generateAgentPerformance(strategy: StrategyConfig, hoursRunning: number): {
  winRate: number;
  pnl: number;
  pnlPct: number;
  totalTrades: number;
  activeTrades: number;
  sharpe: number;
  maxDrawdown: number;
  kellyFraction: number;
  compositeScore: number;
  engines: Record<string, number>;
  shapleyWeights: Record<string, number>;
} {
  const baseWinRate = strategy.expectedWinRate;
  const noise = (Math.random() - 0.5) * 6;
  const winRate = Math.max(40, Math.min(99, baseWinRate + noise));

  const tradesPerHour = strategy.category === "arbitrage" ? 2 : strategy.category === "execution" ? 0.5 : 0.8;
  const totalTrades = Math.floor(hoursRunning * tradesPerHour * (0.8 + Math.random() * 0.4));
  const activeTrades = Math.floor(Math.random() * 4);

  const avgWin = strategy.minCapital * 0.02 * (1 + Math.random());
  const avgLoss = strategy.minCapital * 0.015 * (1 + Math.random());
  const wins = Math.floor(totalTrades * winRate / 100);
  const losses = totalTrades - wins;
  const pnl = wins * avgWin - losses * avgLoss;
  const pnlPct = strategy.minCapital > 0 ? (pnl / strategy.minCapital) * 100 : pnl / 1000;

  const dailyReturns = Array.from({ length: Math.max(2, Math.floor(hoursRunning / 24)) }, () => (Math.random() - 0.48) * 0.05);
  const sharpe = sharpeRatio(dailyReturns);
  const equityCurve = dailyReturns.reduce<number[]>((curve, r) => {
    const prev = curve.length > 0 ? curve[curve.length - 1] : strategy.minCapital || 10000;
    curve.push(prev * (1 + r));
    return curve;
  }, []);
  const mdd = maxDrawdown(equityCurve);

  const kf = kellyCriterion(winRate, avgWin, avgLoss);

  const engineAccs: Record<string, number> = {
    ppo: 0.75 + Math.random() * 0.2,
    thompson: 0.78 + Math.random() * 0.18,
    ukf: 0.73 + Math.random() * 0.2,
    cma_es: 0.74 + Math.random() * 0.19,
  };
  const sw = shapleyWeights(engineAccs);
  const cs = compositeScore(engineAccs, sw);

  return {
    winRate: Math.round(winRate * 10) / 10,
    pnl: Math.round(pnl * 100) / 100,
    pnlPct: Math.round(pnlPct * 10) / 10,
    totalTrades,
    activeTrades,
    sharpe: Math.round(sharpe * 100) / 100,
    maxDrawdown: Math.round(mdd * 1000) / 10,
    kellyFraction: Math.round(kf * 1000) / 10,
    compositeScore: Math.round(cs * 1000) / 10,
    engines: Object.fromEntries(Object.entries(engineAccs).map(([k, v]) => [k, Math.round(v * 1000) / 10])),
    shapleyWeights: Object.fromEntries(Object.entries(sw).map(([k, v]) => [k, Math.round(v * 1000) / 10])),
  };
}

/**
 * Calculates a 0–100 efficiency score for an agent.
 * Agents below 80 are flagged for review; below 60 scheduled for deletion.
 *
 * Components:
 *   Win-rate     40 pts  (40% baseline → each % above 40 = 1 pt, capped at 40)
 *   Profit       30 pts  (positive PnL relative to capital)
 *   Drawdown     20 pts  (0% drawdown = full 20)
 *   AI score     10 pts  (composite score 0-100)
 */
export function calculateEfficiency(agent: {
  winRate: number;
  pnl: number;
  capital: number;
  totalTrades: number;
  compositeScore: number;
  maxDrawdown: number;
}): number {
  // Each percentage point above 40% win rate earns 1 point (max 40)
  const winPts   = Math.max(0, Math.min(40, agent.winRate - 40));
  const cap      = Math.max(agent.capital, 1000);
  const profitPct = agent.pnl / cap;
  const profitPts = Math.max(0, Math.min(30, (profitPct + 0.05) / 0.15 * 30));
  const ddPts    = Math.max(0, Math.min(20, ((20 - Math.min(agent.maxDrawdown, 20)) / 20) * 20));
  const aiPts    = Math.min(10, (agent.compositeScore / 100) * 10);
  return Math.round(winPts + profitPts + ddPts + aiPts);
}

export function evaluateAgentHealth(agent: {
  winRate: number;
  pnl: number;
  capital?: number;
  totalTrades: number;
  compositeScore: number;
  maxDrawdown: number;
}): {
  health: "excellent" | "good" | "degraded" | "critical" | "hallucinating";
  efficiency: number;
  reasons: string[];
  recommendation: "keep" | "monitor" | "reconfigure" | "delete";
  needsDeletion: boolean;
} {
  const reasons: string[] = [];
  const efficiency = calculateEfficiency({
    winRate: agent.winRate,
    pnl: agent.pnl,
    capital: agent.capital ?? 5000,
    totalTrades: agent.totalTrades,
    compositeScore: agent.compositeScore,
    maxDrawdown: agent.maxDrawdown,
  });

  if (agent.winRate < 45) {
    reasons.push(`Win rate critically low at ${agent.winRate.toFixed(1)}%`);
  } else if (agent.winRate < 55) {
    reasons.push(`Win rate below threshold at ${agent.winRate.toFixed(1)}%`);
  }

  if (agent.pnl < 0 && agent.totalTrades > 20) {
    reasons.push(`Negative P&L of $${Math.abs(agent.pnl).toFixed(2)} over ${agent.totalTrades} trades`);
  }

  if (agent.maxDrawdown > 20) {
    reasons.push(`Max drawdown exceeds 20% at ${agent.maxDrawdown.toFixed(1)}%`);
  } else if (agent.maxDrawdown > 10) {
    reasons.push(`Elevated drawdown at ${agent.maxDrawdown.toFixed(1)}%`);
  }

  if (agent.compositeScore < 50) {
    reasons.push(`Composite AI score critically low at ${agent.compositeScore.toFixed(1)}%`);
  }

  // Hallucination: many trades, very low win rate, negative PnL
  if (agent.totalTrades > 50 && agent.winRate < 40 && agent.pnl < 0) {
    return {
      health: "hallucinating",
      efficiency,
      reasons: [...reasons, "Agent shows pattern of irrational trades — likely hallucinating market signals"],
      recommendation: "delete",
      needsDeletion: true,
    };
  }

  const needsDeletion = efficiency < 60;
  let health: "excellent" | "good" | "degraded" | "critical";
  let recommendation: "keep" | "monitor" | "reconfigure" | "delete";

  if (efficiency >= 82)       { health = "excellent"; recommendation = "keep"; }
  else if (efficiency >= 68)  { health = "good";      recommendation = "monitor"; }
  else if (efficiency >= 60)  { health = "degraded";  recommendation = "reconfigure"; }
  else                        { health = "critical";  recommendation = "delete"; }

  return {
    health,
    efficiency,
    reasons: reasons.length ? reasons : efficiency >= 82 ? ["All metrics operating within optimal range"] : [`Efficiency at ${efficiency}% — review recommended`],
    recommendation,
    needsDeletion,
  };
}
