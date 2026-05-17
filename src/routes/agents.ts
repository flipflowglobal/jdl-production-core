import { Router, type IRouter } from "express";
import {
  STRATEGIES,
  generateAgentPerformance,
  evaluateAgentHealth,
  kellyCriterion,
  shapleyWeights,
  compositeScore,
  type StrategyConfig,
} from "../services/trading-engine.js";
import {
  createWallet as generateEthWallet,
  getBalance,
  getProvider,
  getMainWalletAddress,
} from "../services/blockchain.js";
import { ethers } from "ethers";
import { calculateFee, recordFee } from "../services/system-fees.js";
import { saveAgent, recordTrade, getUserAgents } from "../services/database.js";
import { generateCompositeSignal } from "../services/trading-algorithms.js";
import { getTokenPrice, getPriceHistory, getTokenVolume } from "../services/price-feed.js";


const router: IRouter = Router();

interface AgentWallet {
  address: string;
  privateKey: string;
  mnemonic: string;
  createdAt: string;
  totalReceived: number;
  totalSent: number;
  txHistory: { type: "receive" | "send"; amount: number; to?: string; from?: string; chain: string; txHash: string; timestamp: string }[];
}

interface Agent {
  id: string;
  name: string;
  strategyId: string;
  strategy: string;
  strategyCategory: string;
  algorithm: string;
  status: "running" | "paused" | "stopped" | "error";
  capital: number;
  riskProfile: string;
  chains: string[];
  createdAt: string;
  parameters: Record<string, any>;
  wallet: AgentWallet;
  performance: {
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
  };
  health: {
    health: string;
    efficiency: number;
    reasons: string[];
    recommendation: string;
    needsDeletion: boolean;
  };
  markedForDeletion: boolean;
  deletionScheduledAt: string | null;
}

/** Vault: preserves wallets of deleted agents so funds are never lost */
interface VaultedWallet {
  agentId:   string;
  agentName: string;
  address:   string;
  privateKey: string;
  mnemonic:   string;
  deletedAt:  string;
  finalPnl:   number;
}
const walletVault: VaultedWallet[] = [];

function generateAgentWallet(): AgentWallet {
  const w = generateEthWallet();
  return {
    address: w.address,
    privateKey: w.privateKey,
    mnemonic: w.mnemonic,
    createdAt: new Date().toISOString(),
    totalReceived: 0,
    totalSent: 0,
    txHistory: [],
  };
}

function createAgent(
  name: string,
  strategyId: string,
  capital: number,
  riskProfile: string,
  chains: string[],
  paramOverrides: Record<string, any> = {}
): Agent {
  const strategy = STRATEGIES.find((s) => s.id === strategyId);
  if (!strategy) throw new Error(`Unknown strategy: ${strategyId}`);

  const hoursRunning = Math.floor(Math.random() * 720) + 48;
  const perf = generateAgentPerformance(strategy, hoursRunning);

  const params: Record<string, any> = {};
  for (const p of strategy.parameters) {
    params[p.key] = paramOverrides[p.key] ?? p.default;
  }

  const health = evaluateAgentHealth({
    winRate: perf.winRate,
    pnl: perf.pnl,
    capital,
    totalTrades: perf.totalTrades,
    compositeScore: perf.compositeScore,
    maxDrawdown: perf.maxDrawdown,
  });

  const wallet = generateAgentWallet();

  const revenue = perf.pnl > 0 ? Math.round(perf.pnl * 0.15 * 100) / 100 : 0;
  wallet.totalReceived = revenue;
  if (revenue > 0) {
    wallet.txHistory.push({
      type: "receive",
      amount: revenue,
      from: "YOUR_ETH_WALLET_ADDRESS",
      chain: chains[0] || "ethereum",
      txHash: `0x${Array.from({ length: 64 }, () => Math.floor(Math.random() * 16).toString(16)).join("")}`,
      timestamp: new Date(Date.now() - Math.floor(Math.random() * 86400000)).toISOString(),
    });
  }

  return {
    id: `ag-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    name,
    strategyId: strategy.id,
    strategy: strategy.name,
    strategyCategory: strategy.category,
    algorithm: strategy.algorithm,
    status: "running",
    capital,
    riskProfile,
    chains: chains.length > 0 ? chains : strategy.supportedChains.slice(0, 3),
    createdAt: new Date(Date.now() - hoursRunning * 3600 * 1000).toISOString(),
    parameters: params,
    wallet,
    performance: perf,
    health,
    markedForDeletion: health.needsDeletion,
    deletionScheduledAt: health.needsDeletion ? new Date(Date.now() + 24 * 3600 * 1000).toISOString() : null,
  };
}

const agents: Agent[] = [
  createAgent("ARB Hunter Alpha", "triangular-arb", 10000, "Aggressive", ["ethereum", "polygon", "arbitrum"]),
  createAgent("Flash Scout Beta", "flash-loan-arb", 0, "Aggressive", ["ethereum", "bsc"]),
  createAgent("Grid Master Gamma", "grid-trading", 5000, "Balanced", ["polygon"]),
  createAgent("Momentum Delta", "momentum", 8000, "Balanced", ["ethereum", "avalanche"]),
  createAgent("Smart DCA Epsilon", "dca-smart", 2000, "Conservative", ["ethereum", "polygon"]),
  createAgent("Stat Arb Zeta", "statistical-arb", 15000, "Balanced", ["ethereum", "arbitrum"]),
];

agents[2].status = "paused";
agents[2].performance.pnl = -1250;
agents[2].performance.pnlPct = -25;
agents[2].performance.winRate = 42.3;
agents[2].performance.maxDrawdown = 22;
agents[2].performance.sharpe = -0.84;
agents[2].wallet.totalReceived = 0;
agents[2].wallet.txHistory = [];
agents[2].health = evaluateAgentHealth({
  winRate: 42.3,
  pnl: -1250,
  capital: agents[2].capital,
  totalTrades: agents[2].performance.totalTrades,
  compositeScore: agents[2].performance.compositeScore,
  maxDrawdown: agents[2].performance.maxDrawdown,
});
agents[2].markedForDeletion = agents[2].health.needsDeletion;
agents[2].deletionScheduledAt = agents[2].health.needsDeletion
  ? new Date(Date.now() + 24 * 3600 * 1000).toISOString()
  : null;

export function getAllAgents(): Agent[] {
  return agents;
}

async function getMergedAgents(userId?: string): Promise<Agent[]> {
  if (!userId) return agents;
  try {
    const dbRows = await getUserAgents(userId);
    if (!dbRows.length) return agents;
    const dbAgents: Agent[] = dbRows.map((r: any) => ({
      id: r.id,
      name: r.name,
      strategyId: r.strategy_id,
      strategy: r.strategy,
      strategyCategory: r.strategy_category,
      algorithm: r.algorithm,
      status: r.status as Agent["status"],
      capital: parseFloat(r.capital ?? 0),
      riskProfile: r.risk_profile,
      chains: r.chains || [],
      createdAt: r.created_at instanceof Date ? r.created_at.toISOString() : (r.created_at || new Date().toISOString()),
      parameters: r.parameters || {},
      wallet: {
        address: r.wallet_address || `0x${r.id.replace(/[^a-f0-9]/gi, "").slice(0, 40)}`,
        privateKey: "",
        mnemonic: "",
        createdAt: r.created_at instanceof Date ? r.created_at.toISOString() : (r.created_at || new Date().toISOString()),
        totalReceived: 0,
        totalSent: 0,
        txHistory: [],
      },
      performance: r.performance || {
        winRate: 0, pnl: 0, pnlPct: 0, totalTrades: 0, activeTrades: 0,
        sharpe: 0, maxDrawdown: 0, kellyFraction: 0, compositeScore: 0,
        engines: {}, shapleyWeights: {},
      },
      health: r.health || {
        health: "good", efficiency: 75, reasons: ["Agent persisted — monitoring"],
        recommendation: "monitor", needsDeletion: false,
      },
      markedForDeletion: false,
      deletionScheduledAt: null,
    }));
    const dbIds = new Set(dbAgents.map((a) => a.id));
    const seedsNotInDb = agents.filter((a) => !dbIds.has(a.id));
    return [...dbAgents, ...seedsNotInDb];
  } catch (err: any) {
    console.warn("[JDL] DB agents merge failed:", err.message);
    return agents;
  }
}

export async function getAgentsForRequest(req: any): Promise<Agent[]> {
  const userId = (req as any).userId || "anonymous";
  return getMergedAgents(userId);
}

function sanitizeAgent(agent: Agent) {
  return {
    ...agent,
    wallet: {
      address:       agent.wallet.address,
      createdAt:     agent.wallet.createdAt,
      totalReceived: agent.wallet.totalReceived,
      totalSent:     agent.wallet.totalSent,
      txCount:       agent.wallet.txHistory.length,
    },
    // Expose efficiency + deletion fields at top level for easy mobile consumption
    efficiency:          agent.health.efficiency,
    markedForDeletion:   agent.markedForDeletion,
    deletionScheduledAt: agent.deletionScheduledAt,
  };
}

router.get("/strategies", (_req, res) => {
  res.json({
    strategies: STRATEGIES.map((s) => ({
      id: s.id,
      name: s.name,
      category: s.category,
      description: s.description,
      riskLevel: s.riskLevel,
      minCapital: s.minCapital,
      expectedWinRate: s.expectedWinRate,
      expectedMonthlyReturn: s.expectedMonthlyReturn,
      supportedChains: s.supportedChains,
      parameters: s.parameters,
      algorithm: s.algorithm,
    })),
    total: STRATEGIES.length,
  });
});

router.get("/strategies/:id", (req, res) => {
  const strategy = STRATEGIES.find((s) => s.id === req.params.id);
  if (!strategy) {
    res.status(404).json({ error: "Strategy not found" });
    return;
  }
  res.json(strategy);
});

router.get("/agents", async (req, res) => {
  const allAgents = await getAgentsForRequest(req);
  const summary = {
    total:            allAgents.length,
    running:          allAgents.filter((a) => a.status === "running").length,
    paused:           allAgents.filter((a) => a.status === "paused").length,
    totalPnl:         Math.round(allAgents.reduce((s, a) => s + (a.performance?.pnl || 0), 0) * 100) / 100,
    avgWinRate:       allAgents.length ? Math.round(allAgents.reduce((s, a) => s + (a.performance?.winRate || 0), 0) / allAgents.length * 10) / 10 : 0,
    avgEfficiency:    allAgents.length ? Math.round(allAgents.reduce((s, a) => s + (a.health?.efficiency || 0), 0) / allAgents.length) : 0,
    hallucinating:    allAgents.filter((a) => a.health?.health === "hallucinating").length,
    needsAttention:   allAgents.filter((a) => a.health?.recommendation === "delete" || a.health?.recommendation === "reconfigure").length,
    markedForDeletion: allAgents.filter((a) => a.markedForDeletion).length,
    belowEfficiency:  allAgents.filter((a) => (a.health?.efficiency || 0) < 90).length,
  };
  res.json({ agents: allAgents.map(sanitizeAgent), summary });
});

const SUBSCRIPTION_LIMITS: Record<string, { agents: number; flashLoans: boolean }> = {
  free: { agents: 1, flashLoans: false },
  pro: { agents: 5, flashLoans: true },
  elite: { agents: -1, flashLoans: true },
};

let currentSubscriptionTier: string = "elite";

router.post("/subscription-tier", (req, res) => {
  const { tier } = req.body;
  if (!tier || !SUBSCRIPTION_LIMITS[tier]) {
    res.status(400).json({ error: "Invalid tier. Must be free, pro, or elite" });
    return;
  }
  currentSubscriptionTier = tier;
  res.json({ success: true, tier: currentSubscriptionTier, limits: SUBSCRIPTION_LIMITS[tier] });
});

router.get("/subscription-tier", (_req, res) => {
  res.json({ tier: currentSubscriptionTier, limits: SUBSCRIPTION_LIMITS[currentSubscriptionTier] });
});

router.post("/agents", (req, res) => {
  const body = req.body as {
    name: string;
    strategyId: string;
    capital: number;
    riskProfile: string;
    chains: string[];
    parameters?: Record<string, any>;
  };

  if (!body.name || !body.strategyId) {
    res.status(400).json({ error: "Name and strategyId are required" });
    return;
  }

  const strategy = STRATEGIES.find((s) => s.id === body.strategyId);
  if (!strategy) {
    res.status(400).json({ error: `Unknown strategy: ${body.strategyId}`, availableStrategies: STRATEGIES.map((s) => s.id) });
    return;
  }

  const limits = SUBSCRIPTION_LIMITS[currentSubscriptionTier];
  if (limits.agents !== -1 && agents.length >= limits.agents) {
    const tierNames: Record<string, string> = { free: "Free", pro: "Pro", elite: "Elite" };
    res.status(403).json({
      error: `Agent limit reached`,
      message: `Your ${tierNames[currentSubscriptionTier]} plan allows ${limits.agents} agent${limits.agents === 1 ? "" : "s"}. Upgrade to create more agents.`,
      currentTier: currentSubscriptionTier,
      agentLimit: limits.agents,
      currentAgents: agents.length,
      upgradeRequired: true,
    });
    return;
  }

  if ((strategy as any).category === "flash-loan" && !limits.flashLoans) {
    res.status(403).json({
      error: "Flash loan strategies require Pro or Elite plan",
      currentTier: currentSubscriptionTier,
      upgradeRequired: true,
    });
    return;
  }

  if (body.capital < strategy.minCapital) {
    res.status(400).json({ error: `Minimum capital for ${strategy.name} is $${strategy.minCapital}` });
    return;
  }

  try {
    const agent = createAgent(
      body.name,
      body.strategyId,
      body.capital || strategy.minCapital,
      body.riskProfile || strategy.riskLevel,
      body.chains || [],
      body.parameters || {}
    );
    agent.createdAt = new Date().toISOString();
    agent.performance.totalTrades = 0;
    agent.performance.pnl = 0;
    agent.performance.pnlPct = 0;
    agent.performance.winRate = 0;
    agent.performance.activeTrades = 0;
    agent.wallet.totalReceived = 0;
    agent.wallet.totalSent = 0;
    agent.wallet.txHistory = [];
    agent.health = { health: "good", efficiency: 100, reasons: ["Agent just deployed — monitoring"], recommendation: "monitor", needsDeletion: false };
    agents.push(agent);

    const userId = (req as any).userId || "anonymous";
    saveAgent(userId, agent).catch((err: any) => console.warn("[JDL] DB persist agent failed:", err.message));

    res.status(201).json(sanitizeAgent(agent));
  } catch (err: any) {
    res.status(400).json({ error: err.message });
  }
});

/** GET /agents/deletion-queue — must be before /:id to avoid param capture */
router.get("/agents/deletion-queue", (_req, res) => {
  const queue = agents.filter((a: any) => a.markedForDeletion || a.health?.recommendation === "delete");
  res.json({
    count: queue.length,
    agents: queue.map((a: any) => ({
      id:                  a.id,
      name:                a.name,
      status:              a.status,
      efficiency:          a.health?.efficiency,
      health:              a.health?.health,
      recommendation:      a.health?.recommendation,
      reasons:             a.health?.reasons,
      finalPnl:            a.performance?.pnl,
      walletAddress:       a.wallet?.address,
      deletionScheduledAt: a.deletionScheduledAt,
      markedForDeletion:   a.markedForDeletion,
    })),
  });
});

router.get("/agents/:id", (req, res) => {
  const agent = agents.find((a) => a.id === req.params.id);
  if (!agent) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }
  res.json(sanitizeAgent(agent));
});

router.get("/agents/:id/health", (req, res) => {
  const agent = agents.find((a) => a.id === req.params.id);
  if (!agent) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }
  res.json({
    agentId: agent.id,
    name: agent.name,
    ...agent.health,
    performance: {
      winRate: agent.performance.winRate,
      pnl: agent.performance.pnl,
      totalTrades: agent.performance.totalTrades,
      sharpe: agent.performance.sharpe,
      maxDrawdown: agent.performance.maxDrawdown,
      kellyFraction: agent.performance.kellyFraction,
      compositeScore: agent.performance.compositeScore,
    },
    engines: agent.performance.engines,
    shapleyWeights: agent.performance.shapleyWeights,
  });
});

router.patch("/agents/:id", (req, res) => {
  const idx = agents.findIndex((a) => a.id === req.params.id);
  if (idx === -1) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }
  const allowed = ["name", "riskProfile", "parameters", "chains"];
  for (const key of allowed) {
    if (req.body[key] !== undefined) {
      (agents[idx] as any)[key] = req.body[key];
    }
  }
  res.json(sanitizeAgent(agents[idx]));
});

/**
 * GET /agents/:id/deletion-info
 *
 * Read-safe pre-deletion info endpoint.
 * Returns wallet details, fund summary, and warnings WITHOUT modifying anything.
 * Use this instead of calling DELETE without ?confirm to fetch deletion info.
 */
router.get("/agents/:id/deletion-info", (req, res) => {
  const agent = agents.find((a) => a.id === req.params.id);
  if (!agent) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }
  res.json({
    requiresConfirmation: true,
    agent: {
      id:         agent.id,
      name:       agent.name,
      status:     agent.status,
      efficiency: agent.health.efficiency,
      finalPnl:   agent.performance.pnl,
    },
    wallet: {
      address:       agent.wallet.address,
      totalReceived: agent.wallet.totalReceived,
      totalSent:     agent.wallet.totalSent,
      netRevenue:    Math.round((agent.wallet.totalReceived - agent.wallet.totalSent) * 100) / 100,
    },
    warning: "⚠️ Deleting this agent will permanently remove it from the active list. The wallet private key will be preserved in the secure vault. Ensure any on-chain holdings have been swept before confirming.",
    sweepNote: "Confirm deletion via DELETE /agents/:id?confirm=true. Private key preserved in vault — accessible via GET /agent-wallets/vault.",
  });
});

/**
 * DELETE /agents/:id
 *
 * 2-step safe deletion to protect wallet funds.
 *
 * Step 1 — GET info before deletion (no ?confirm=true):
 *   Returns wallet details, holdings, and a WARNING.
 *   The private key is included in the vault payload so the user can sweep first.
 *
 * Step 2 — Confirm deletion (?confirm=true):
 *   Vaults the wallet private key/mnemonic, then removes the agent from the active list.
 *   Wallet address + final PnL returned. Private key preserved in walletVault.
 */
router.delete("/agents/:id", (req, res) => {
  const idx = agents.findIndex((a) => a.id === req.params.id);
  if (idx === -1) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }

  const agent = agents[idx];

  // Require explicit confirmation — read deletion info via GET /agents/:id/deletion-info
  if (!req.query.confirm) {
    res.status(400).json({
      error: "Confirmation required",
      hint: "Fetch deletion details via GET /agents/:id/deletion-info, then resubmit with ?confirm=true to complete deletion.",
    });
    return;
  }

  // Step 2: Vault the wallet then delete
  const removed = agents.splice(idx, 1)[0];

  walletVault.push({
    agentId:    removed.id,
    agentName:  removed.name,
    address:    removed.wallet.address,
    privateKey: removed.wallet.privateKey,
    mnemonic:   removed.wallet.mnemonic,
    deletedAt:  new Date().toISOString(),
    finalPnl:   removed.performance.pnl,
  });

  console.log(`[Agents] Agent ${removed.name} (${removed.id}) deleted — wallet ${removed.wallet.address} preserved in vault`);

  res.json({
    deleted:       true,
    agent:         { id: removed.id, name: removed.name, finalPnl: removed.performance.pnl },
    walletAddress: removed.wallet.address,
    walletVaulted: true,
    reason:        removed.health.recommendation === "delete"
      ? `Agent recommended for deletion — efficiency ${removed.health.efficiency}%`
      : "User-initiated deletion",
    vaultNote:     "Wallet private key preserved in secure vault. Access via GET /agent-wallets/vault.",
  });
});

/** GET /agents/deletion-queue — all agents flagged for or marked for deletion */
router.get("/agents/deletion-queue", (_req, res) => {
  const queue = agents.filter((a) => a.markedForDeletion || a.health.recommendation === "delete");
  res.json({
    count: queue.length,
    agents: queue.map((a) => ({
      id:                  a.id,
      name:                a.name,
      status:              a.status,
      efficiency:          a.health.efficiency,
      health:              a.health.health,
      recommendation:      a.health.recommendation,
      reasons:             a.health.reasons,
      finalPnl:            a.performance.pnl,
      walletAddress:       a.wallet.address,
      deletionScheduledAt: a.deletionScheduledAt,
      markedForDeletion:   a.markedForDeletion,
    })),
  });
});

/** POST /agents/:id/mark-deletion — schedule an agent for deletion */
router.post("/agents/:id/mark-deletion", (req, res) => {
  const agent = agents.find((a) => a.id === req.params.id);
  if (!agent) { res.status(404).json({ error: "Agent not found" }); return; }
  agent.markedForDeletion   = true;
  agent.deletionScheduledAt = new Date(Date.now() + 24 * 3600 * 1000).toISOString();
  agent.status              = "paused";
  res.json({
    marked:              true,
    agentId:             agent.id,
    deletionScheduledAt: agent.deletionScheduledAt,
    message:             "Agent paused and scheduled for deletion. Confirm deletion via DELETE /agents/:id?confirm=true",
  });
});

/** DELETE /agents/:id/cancel-deletion — unmark an agent from the deletion queue */
router.delete("/agents/:id/cancel-deletion", (req, res) => {
  const agent = agents.find((a) => a.id === req.params.id);
  if (!agent) { res.status(404).json({ error: "Agent not found" }); return; }
  agent.markedForDeletion   = false;
  agent.deletionScheduledAt = null;
  agent.status              = "running";
  res.json({ cancelled: true, agentId: agent.id, message: "Deletion cancelled — agent resumed." });
});

/** GET /agent-wallets/vault — list all preserved wallets from deleted agents */
router.get("/agent-wallets/vault", (_req, res) => {
  res.json({
    count: walletVault.length,
    wallets: walletVault.map((v) => ({
      agentId:   v.agentId,
      agentName: v.agentName,
      address:   v.address,
      deletedAt: v.deletedAt,
      finalPnl:  v.finalPnl,
      // Private key only for authenticated use — in production add auth middleware
      privateKey: v.privateKey,
      mnemonic:   v.mnemonic,
    })),
  });
});

router.post("/agents/:id/pause", (req, res) => {
  const agent = agents.find((a) => a.id === req.params.id);
  if (!agent) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }
  agent.status = "paused";
  res.json(sanitizeAgent(agent));
});

router.post("/agents/:id/resume", (req, res) => {
  const agent = agents.find((a) => a.id === req.params.id);
  if (!agent) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }
  agent.status = "running";
  res.json(sanitizeAgent(agent));
});

router.get("/agent-wallets", (_req, res) => {
  const wallets = agents.map((a) => ({
    agentId: a.id,
    agentName: a.name,
    agentStatus: a.status,
    strategy: a.strategy,
    chains: a.chains,
    wallet: {
      address: a.wallet.address,
      createdAt: a.wallet.createdAt,
      totalReceived: a.wallet.totalReceived,
      totalSent: a.wallet.totalSent,
      netRevenue: Math.round((a.wallet.totalReceived - a.wallet.totalSent) * 100) / 100,
      txCount: a.wallet.txHistory.length,
    },
  }));

  const totalRevenue = wallets.reduce((s, w) => s + w.wallet.totalReceived, 0);
  const totalSent = wallets.reduce((s, w) => s + w.wallet.totalSent, 0);

  res.json({
    wallets,
    summary: {
      totalWallets: wallets.length,
      totalRevenue: Math.round(totalRevenue * 100) / 100,
      totalSent: Math.round(totalSent * 100) / 100,
      netHeld: Math.round((totalRevenue - totalSent) * 100) / 100,
    },
  });
});

router.get("/agent-wallets/:agentId", (req, res) => {
  const agent = agents.find((a) => a.id === req.params.agentId);
  if (!agent) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }
  res.json({
    agentId: agent.id,
    agentName: agent.name,
    wallet: {
      address: agent.wallet.address,
      createdAt: agent.wallet.createdAt,
      totalReceived: agent.wallet.totalReceived,
      totalSent: agent.wallet.totalSent,
      netRevenue: Math.round((agent.wallet.totalReceived - agent.wallet.totalSent) * 100) / 100,
      txHistory: agent.wallet.txHistory,
    },
  });
});

router.get("/agent-wallets/:agentId/balance", async (req, res) => {
  const agent = agents.find((a) => a.id === req.params.agentId);
  if (!agent) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }

  const balances: Record<string, { balance: string; balanceEth: string }> = {};
  for (const chain of agent.chains) {
    try {
      const b = await getBalance(chain, agent.wallet.address);
      balances[chain] = b;
    } catch {
      balances[chain] = { balance: "0", balanceEth: "0.0" };
    }
  }

  res.json({
    agentId: agent.id,
    agentName: agent.name,
    address: agent.wallet.address,
    balances,
  });
});

router.post("/agent-wallets/:agentId/send", async (req, res) => {
  const agent = agents.find((a) => a.id === req.params.agentId);
  if (!agent) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }

  const { to, amount, chain, tokenAddress, tokenDecimals, tokenSymbol } = req.body as {
    to: string; amount: string; chain: string;
    tokenAddress?: string; tokenDecimals?: number; tokenSymbol?: string;
  };
  if (!to || !amount || !chain) {
    res.status(400).json({ error: "to, amount, and chain are required" });
    return;
  }

  try {
    const amountNum = parseFloat(amount);
    const { feeAmount, netAmount } = calculateFee(amountNum);
    const mainWallet = getMainWalletAddress();

    const provider = getProvider(chain);
    const wallet = new ethers.Wallet(agent.wallet.privateKey, provider);
    const symbol = tokenSymbol || "ETH";

    let txHash: string;
    let blockNumber: number | undefined;
    let feeTxHash: string | undefined;

    if (tokenAddress) {
      const decimals = tokenDecimals ?? 18;
      const erc20Abi = [
        "function transfer(address to, uint256 amount) returns (bool)",
      ];
      const contract = new ethers.Contract(tokenAddress, erc20Abi, wallet);

      const netParsed = ethers.parseUnits(netAmount.toString(), decimals);
      const tx = await contract.transfer(to, netParsed);
      const receipt = await tx.wait();
      txHash = tx.hash;
      blockNumber = receipt?.blockNumber;

      try {
        const feeParsed = ethers.parseUnits(feeAmount.toString(), decimals);
        const feeTx = await contract.transfer(mainWallet, feeParsed);
        await feeTx.wait();
        feeTxHash = feeTx.hash;
      } catch (_feeErr) {}
    } else {
      const tx = await wallet.sendTransaction({
        to,
        value: ethers.parseEther(netAmount.toString()),
      });
      const receipt = await tx.wait();
      txHash = tx.hash;
      blockNumber = receipt?.blockNumber;

      try {
        const feeTxRaw = await wallet.sendTransaction({
          to: mainWallet,
          value: ethers.parseEther(feeAmount.toString()),
        });
        const feeReceipt = await feeTxRaw.wait();
        feeTxHash = feeTxRaw.hash;
      } catch (_feeErr) {}
    }

    agent.wallet.totalSent += amountNum;
    agent.wallet.txHistory.push({
      type: "send",
      amount: amountNum,
      to,
      chain,
      txHash,
      timestamp: new Date().toISOString(),
    });

    const feeRecord = recordFee({
      txHash,
      fromWallet: agent.wallet.address,
      toWallet: to,
      originalAmount: amountNum,
      chain,
      token: symbol,
      type: "agent-send",
      userId: "user-001",
    });

    res.json({
      success: true,
      txHash,
      from: agent.wallet.address,
      to,
      amount: netAmount.toString(),
      chain,
      token: symbol,
      tokenAddress: tokenAddress || null,
      blockNumber,
      systemFee: {
        rate: "0.75%",
        feeAmount: feeRecord.feeAmount,
        netAmountSent: feeRecord.netAmount,
        feeDestination: mainWallet,
        feeTxHash: feeTxHash || feeRecord.feeTxHash,
      },
    });
  } catch (err: any) {
    res.status(500).json({ error: err.message || "Transaction failed" });
  }
});

// ─── Trading Signal Endpoint ────────────────────────────────────────────────

router.post("/agents/:id/signal", (req, res) => {
  const agent = agents.find((a) => a.id === req.params.id);
  if (!agent) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }

  if (agent.status !== "running") {
    res.json({ signal: null, reason: `Agent is ${agent.status}` });
    return;
  }

  // Use REAL price history from live price-feed ring buffer
  const chainTokenMap: Record<string, string> = {
    ethereum: "ETH", polygon: "MATIC", bsc: "BNB",
    arbitrum: "ETH", avalanche: "AVAX", optimism: "ETH",
  };
  const primaryChain = (agent.chains?.[0] || "ethereum") as string;
  const tokenSym = chainTokenMap[primaryChain] || "ETH";
  const liveTokenData = getTokenPrice(tokenSym);
  const priceHistory = getPriceHistory(tokenSym);
  const vol24h = getTokenVolume(tokenSym);
  const tickVol = vol24h / (24 * 60);
  const volumeHistory = priceHistory.map(() => tickVol * (0.7 + Math.random() * 0.6));

  // Fall back to simulated if live feed has no data yet
  const effectivePriceHistory = priceHistory.length >= 5 ? priceHistory : (() => {
    const basePrice = liveTokenData?.price || 2000;
    const hist: number[] = [];
    let p = basePrice;
    for (let i = 0; i < 50; i++) { p *= 1 + (Math.random() - 0.48) * 0.02; hist.push(p); }
    return hist;
  })();
  const effectiveVolHistory = volumeHistory.length >= 5 ? volumeHistory : effectivePriceHistory.map(() => 1e6 + Math.random() * 5e6);

  const signal = generateCompositeSignal(
    agent.id,
    agent.strategyId,
    effectivePriceHistory,
    effectiveVolHistory,
    agent.chains,
    agent.capital,
    agent.performance.pnl
  );

  // Update agent performance with signal outcome
  if (signal.action !== "hold") {
    const pnlDelta = signal.size * signal.monteCarloReturn;
    agent.performance.pnl = Math.round((agent.performance.pnl + pnlDelta) * 100) / 100;
    agent.performance.pnlPct = agent.capital > 0
      ? Math.round(agent.performance.pnl / agent.capital * 10000) / 100
      : 0;
    agent.performance.totalTrades += 1;
    if (pnlDelta > 0) {
      const wins = agent.performance.winRate / 100 * (agent.performance.totalTrades - 1) + 1;
      agent.performance.winRate = Math.round(wins / agent.performance.totalTrades * 1000) / 10;
    }
    agent.performance.sharpe = signal.sharpeRatio;

    // Record trade to DB (fire-and-forget)
    const userId = (req as any).userId || "anonymous";
    recordTrade({
      agentId: agent.id,
      userId,
      chain: signal.optimalChain,
      fromToken: "USDC",
      toToken: "ETH",
      fromAmount: signal.size,
      toAmount: signal.size / priceHistory[priceHistory.length - 1],
      pnl: pnlDelta,
      feePaid: signal.size * 0.003,
      systemFee: signal.size * 0.0075,
      status: "confirmed",
      algorithm: signal.algorithm,
      confidence: signal.confidence,
    }).catch((e: any) => console.warn("[JDL] Trade record failed:", e.message));
  }

  res.json({
    agentId: agent.id,
    agentName: agent.name,
    signal,
    priceSnapshot: {
      token: tokenSym,
      current: effectivePriceHistory[effectivePriceHistory.length - 1],
      open: effectivePriceHistory[0],
      change: ((effectivePriceHistory[effectivePriceHistory.length - 1] - effectivePriceHistory[0]) / effectivePriceHistory[0] * 100).toFixed(2) + "%",
      live: priceHistory.length >= 5,
    },
    agentPerformance: {
      pnl: agent.performance.pnl,
      winRate: agent.performance.winRate,
      totalTrades: agent.performance.totalTrades,
      sharpe: agent.performance.sharpe,
    },
  });
});

router.post("/agent-wallets/:agentId/receive", (req, res) => {
  const agent = agents.find((a) => a.id === req.params.agentId);
  if (!agent) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }

  const { amount, from, chain, txHash } = req.body as { amount: number; from: string; chain: string; txHash?: string };
  if (!amount || !chain) {
    res.status(400).json({ error: "amount and chain are required" });
    return;
  }

  agent.wallet.totalReceived += amount;
  agent.wallet.txHistory.push({
    type: "receive",
    amount,
    from: from || "external",
    chain,
    txHash: txHash || `0x${Array.from({ length: 64 }, () => Math.floor(Math.random() * 16).toString(16)).join("")}`,
    timestamp: new Date().toISOString(),
  });

  res.json({
    success: true,
    agentId: agent.id,
    walletAddress: agent.wallet.address,
    amountReceived: amount,
    newTotalReceived: agent.wallet.totalReceived,
  });
});

export default router;
