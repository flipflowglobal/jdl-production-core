/**
 * JDL Self-Healing System Monitor
 *
 * Layer 1 — Subsystem watchdog: continuously scans DB, RPCs, price feed,
 *            and agent executor every 30 / 60 seconds.
 *
 * Layer 2 — Endpoint watchdog: probes 5 groups × 4 API endpoints every 45 s.
 *            Considers an endpoint healthy if it returns any HTTP response
 *            (even 401 / 403 — route is alive). Only 5xx, timeouts, or
 *            network errors count as failures.
 *
 * Exposes:
 *   getSystemHealth()       → /api/health/detailed
 *   getEndpointWatchdogs()  → /api/health/watchdogs
 */

import http from "http";
import { logger } from "../lib/logger.js";
import { getPriceState } from "./price-feed.js";

// ── Subsystem types ──────────────────────────────────────────────────────────

export interface SubsystemStatus {
  ok: boolean;
  latencyMs?: number;
  error?: string;
  lastChecked: string;
  consecutive_failures: number;
}

export interface SystemHealth {
  status: "healthy" | "degraded" | "critical";
  uptime_seconds: number;
  checked_at: string;
  subsystems: {
    database: SubsystemStatus;
    price_feed: SubsystemStatus;
    ethereum_rpc: SubsystemStatus;
    polygon_rpc: SubsystemStatus;
    arbitrum_rpc: SubsystemStatus;
    bsc_rpc: SubsystemStatus;
    avalanche_rpc: SubsystemStatus;
    agent_executor: SubsystemStatus;
  };
  statistics: {
    total_checks: number;
    total_failures: number;
    auto_recoveries: number;
  };
}

// ── Endpoint watchdog types ──────────────────────────────────────────────────

export interface EndpointProbeResult {
  path: string;
  ok: boolean;
  latencyMs: number;
  statusCode?: number;
  error?: string;
  lastChecked: string;
  consecutive_failures: number;
}

export interface WatchdogGroup {
  name: string;
  description: string;
  status: "healthy" | "degraded" | "critical";
  endpoints: EndpointProbeResult[];
  last_probe: string;
  ok_count: number;
  total_count: number;
}

export interface EndpointWatchdogReport {
  checked_at: string;
  overall_status: "healthy" | "degraded" | "critical";
  groups: WatchdogGroup[];
  statistics: {
    total_probes: number;
    total_endpoint_failures: number;
    auto_recoveries: number;
  };
}

// ── Subsystem state ──────────────────────────────────────────────────────────

const START_TIME = Date.now();
let totalChecks = 0;
let totalFailures = 0;
let autoRecoveries = 0;

const subsystems: SystemHealth["subsystems"] = {
  database:       { ok: true, lastChecked: new Date().toISOString(), consecutive_failures: 0 },
  price_feed:     { ok: true, lastChecked: new Date().toISOString(), consecutive_failures: 0 },
  ethereum_rpc:   { ok: true, lastChecked: new Date().toISOString(), consecutive_failures: 0 },
  polygon_rpc:    { ok: true, lastChecked: new Date().toISOString(), consecutive_failures: 0 },
  arbitrum_rpc:   { ok: true, lastChecked: new Date().toISOString(), consecutive_failures: 0 },
  bsc_rpc:        { ok: true, lastChecked: new Date().toISOString(), consecutive_failures: 0 },
  avalanche_rpc:  { ok: true, lastChecked: new Date().toISOString(), consecutive_failures: 0 },
  agent_executor: { ok: true, lastChecked: new Date().toISOString(), consecutive_failures: 0 },
};

let lastAgentExecutorTick = Date.now();

export function reportAgentExecutorTick() {
  lastAgentExecutorTick = Date.now();
}

function updateStatus(key: keyof SystemHealth["subsystems"], ok: boolean, latencyMs?: number, error?: string) {
  const prev = subsystems[key];
  if (!ok) {
    totalFailures++;
    subsystems[key] = {
      ok: false,
      latencyMs,
      error,
      lastChecked: new Date().toISOString(),
      consecutive_failures: prev.consecutive_failures + 1,
    };
    if (prev.ok) {
      logger.warn({ subsystem: key, error }, `[HealthMonitor] ${key} went OFFLINE`);
    }
  } else {
    if (!prev.ok) {
      autoRecoveries++;
      logger.info({ subsystem: key, latencyMs }, `[HealthMonitor] ${key} RECOVERED`);
    }
    subsystems[key] = { ok: true, latencyMs, lastChecked: new Date().toISOString(), consecutive_failures: 0 };
  }
}

async function checkDatabase() {
  const t0 = Date.now();
  try {
    const { query } = await import("./database.js");
    await query("SELECT 1");
    updateStatus("database", true, Date.now() - t0);
  } catch (err: any) {
    updateStatus("database", false, Date.now() - t0, err.message);
    logger.error({ err }, "[HealthMonitor] DB check failed — pool may need reconnect");
  }
}

async function checkRpc(key: keyof SystemHealth["subsystems"], rpcUrl: string) {
  const t0 = Date.now();
  try {
    const { ethers } = await import("ethers");
    const provider = new ethers.JsonRpcProvider(rpcUrl);
    const block = await Promise.race([
      provider.getBlockNumber(),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error("RPC timeout")), 8000)),
    ]);
    if (typeof block !== "number" || block <= 0) throw new Error("Invalid block number");
    updateStatus(key, true, Date.now() - t0);
  } catch (err: any) {
    updateStatus(key, false, Date.now() - t0, err.message);
  }
}

const PRICE_FEED_STARTUP_GRACE_MS = 90_000; // allow 90s for first fetch on cold start

function checkPriceFeed() {
  try {
    const state = getPriceState();
    const staleThresholdMs = 5 * 60 * 1000;
    const serverUptimeMs   = Date.now() - START_TIME;
    const neverFetched     = state.lastFetchMs === 0;

    // During the startup grace window, a never-fetched feed is not yet "stale"
    if (neverFetched && serverUptimeMs < PRICE_FEED_STARTUP_GRACE_MS) {
      updateStatus("price_feed", true, undefined);
      return;
    }

    const isStale = neverFetched || (Date.now() - state.lastFetchMs > staleThresholdMs);
    const isError = state.status === "error";
    if (isError || isStale) {
      updateStatus("price_feed", false, undefined, isStale ? "Price feed data is stale" : "Price feed in error state");
    } else {
      updateStatus("price_feed", true, undefined);
    }
  } catch (err: any) {
    updateStatus("price_feed", false, undefined, err.message);
  }
}

function checkAgentExecutor() {
  const msSinceTick = Date.now() - lastAgentExecutorTick;
  const maxSilenceMs = 3 * 60 * 1000;
  if (msSinceTick > maxSilenceMs) {
    updateStatus("agent_executor", false, undefined, `No tick in ${Math.round(msSinceTick / 1000)}s`);
  } else {
    updateStatus("agent_executor", true, msSinceTick);
  }
}

const ALCHEMY_KEY = process.env.Alchemy_API_Key || "";
const RPC_URLS = {
  ethereum: process.env.ETH_RPC_URL || (ALCHEMY_KEY ? `https://eth-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}` : "https://eth.drpc.org"),
  polygon:  process.env.POLYGON_RPC_URL || (ALCHEMY_KEY ? `https://polygon-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}` : "https://polygon.drpc.org"),
  arbitrum: process.env.ARB_RPC_URL || (ALCHEMY_KEY ? `https://arb-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}` : "https://arb1.arbitrum.io/rpc"),
  bsc:      process.env.BSC_RPC_URL || "https://bsc-dataseed1.binance.org",
  avalanche:process.env.AVAX_RPC_URL || "https://api.avax.network/ext/bc/C/rpc",
};

async function runChecks() {
  totalChecks++;
  checkPriceFeed();
  checkAgentExecutor();
  await checkDatabase();
}

async function runRpcChecks() {
  await Promise.allSettled([
    checkRpc("ethereum_rpc", RPC_URLS.ethereum),
    checkRpc("polygon_rpc", RPC_URLS.polygon),
    checkRpc("arbitrum_rpc", RPC_URLS.arbitrum),
    checkRpc("bsc_rpc", RPC_URLS.bsc),
    checkRpc("avalanche_rpc", RPC_URLS.avalanche),
  ]);
}

export function getSystemHealth(): SystemHealth {
  const okCount = Object.values(subsystems).filter((s) => s.ok).length;
  const total = Object.values(subsystems).length;
  const criticalDown = !subsystems.database.ok;
  const status: SystemHealth["status"] = criticalDown
    ? "critical"
    : okCount < total
    ? "degraded"
    : "healthy";

  return {
    status,
    uptime_seconds: Math.round((Date.now() - START_TIME) / 1000),
    checked_at: new Date().toISOString(),
    subsystems,
    statistics: { total_checks: totalChecks, total_failures: totalFailures, auto_recoveries: autoRecoveries },
  };
}

// ── Layer 2: Endpoint watchdog groups (5 groups × 4 endpoints) ───────────────

const ENDPOINT_GROUPS_DEF: Array<{ name: string; description: string; paths: string[] }> = [
  {
    name: "Core Trading",
    description: "Agents, activity summary, dashboard summary, FX rates",
    paths: ["/api/agents", "/api/activity/summary", "/api/dashboard/summary", "/api/market/fx-rates"],
  },
  {
    name: "Markets",
    description: "Live prices, candles, all-prices feed, strategies list",
    paths: ["/api/market/prices", "/api/market/candles", "/api/market/all-prices", "/api/market/strategies"],
  },
  {
    name: "Wallets & Blockchain",
    description: "Wallet list, system wallet, chain connections, DEX exchanges",
    paths: ["/api/wallets", "/api/blockchain/system-wallet", "/api/blockchain/connections", "/api/blockchain/exchanges"],
  },
  {
    name: "Flash Loans & Credit",
    description: "Opportunities, flash loan stats, credit oracle schema, subscription status",
    paths: ["/api/flash-loans/opportunities", "/api/flash-loans/stats", "/api/credit-oracle/schema", "/api/subscriptions/status"],
  },
  {
    name: "User Services",
    description: "User profile, preferences, strategies catalogue, health ping",
    paths: ["/api/users/me", "/api/users/me/preferences", "/api/strategies", "/api/health"],
  },
];

function makeEndpointResult(path: string): EndpointProbeResult {
  return { path, ok: true, latencyMs: 0, lastChecked: new Date().toISOString(), consecutive_failures: 0 };
}

const endpointState: Map<string, EndpointProbeResult> = new Map();
ENDPOINT_GROUPS_DEF.forEach((g) => g.paths.forEach((p) => endpointState.set(p, makeEndpointResult(p))));

let totalEndpointProbes = 0;
let totalEndpointFailures = 0;
let endpointAutoRecoveries = 0;

const API_PORT = parseInt(process.env.PORT || "8080", 10);

function probeEndpoint(path: string): Promise<void> {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const prev = endpointState.get(path)!;
    const req = http.get(
      {
        hostname: "127.0.0.1",
        port: API_PORT,
        path,
        timeout: 6000,
        headers: { "X-Watchdog-Internal": "1" },
      },
      (res) => {
        res.resume();
        const latencyMs = Date.now() - t0;
        const ok = res.statusCode !== undefined && res.statusCode < 500;
        const now = new Date().toISOString();
        if (!ok) {
          totalEndpointFailures++;
          const next: EndpointProbeResult = {
            path,
            ok: false,
            latencyMs,
            statusCode: res.statusCode,
            error: `HTTP ${res.statusCode}`,
            lastChecked: now,
            consecutive_failures: prev.consecutive_failures + 1,
          };
          if (prev.ok) logger.warn({ path, statusCode: res.statusCode }, "[Watchdog] Endpoint DEGRADED");
          endpointState.set(path, next);
        } else {
          if (!prev.ok) {
            endpointAutoRecoveries++;
            logger.info({ path, latencyMs }, "[Watchdog] Endpoint RECOVERED");
          }
          endpointState.set(path, {
            path, ok: true, latencyMs, statusCode: res.statusCode,
            lastChecked: now, consecutive_failures: 0,
          });
        }
        resolve();
      }
    );
    req.on("error", (err) => {
      totalEndpointFailures++;
      const now = new Date().toISOString();
      if (prev.ok) logger.warn({ path, err: err.message }, "[Watchdog] Endpoint UNREACHABLE");
      endpointState.set(path, {
        path, ok: false, latencyMs: Date.now() - t0, error: err.message,
        lastChecked: now, consecutive_failures: prev.consecutive_failures + 1,
      });
      resolve();
    });
    req.on("timeout", () => {
      req.destroy();
      totalEndpointFailures++;
      const now = new Date().toISOString();
      endpointState.set(path, {
        path, ok: false, latencyMs: Date.now() - t0, error: "timeout",
        lastChecked: now, consecutive_failures: prev.consecutive_failures + 1,
      });
      resolve();
    });
    req.setTimeout(6000);
  });
}

async function probeGroup(paths: string[]): Promise<void> {
  totalEndpointProbes += paths.length;
  await Promise.allSettled(paths.map(probeEndpoint));
}

async function runEndpointWatchdogs() {
  for (const group of ENDPOINT_GROUPS_DEF) {
    await probeGroup(group.paths);
  }
}

export function getEndpointWatchdogs(): EndpointWatchdogReport {
  const groups: WatchdogGroup[] = ENDPOINT_GROUPS_DEF.map((def) => {
    const endpoints = def.paths.map((p) => endpointState.get(p)!);
    const okCount = endpoints.filter((e) => e.ok).length;
    const total = endpoints.length;
    const status: WatchdogGroup["status"] =
      okCount === total ? "healthy" : okCount >= total / 2 ? "degraded" : "critical";
    const lastProbe = endpoints.reduce((a, b) => (a > b.lastChecked ? a : b.lastChecked), "");
    return {
      name: def.name,
      description: def.description,
      status,
      endpoints,
      last_probe: lastProbe,
      ok_count: okCount,
      total_count: total,
    };
  });

  const okGroups = groups.filter((g) => g.status === "healthy").length;
  const total = groups.length;
  const overall_status: EndpointWatchdogReport["overall_status"] =
    okGroups === total ? "healthy" : okGroups >= total / 2 ? "degraded" : "critical";

  return {
    checked_at: new Date().toISOString(),
    overall_status,
    groups,
    statistics: {
      total_probes: totalEndpointProbes,
      total_endpoint_failures: totalEndpointFailures,
      auto_recoveries: endpointAutoRecoveries,
    },
  };
}

// ── Start both watchdog layers ────────────────────────────────────────────────

export function startHealthMonitor() {
  logger.info("[HealthMonitor] Starting self-healing system monitor");

  runChecks().catch(() => {});
  runRpcChecks().catch(() => {});

  setTimeout(() => {
    runEndpointWatchdogs().catch(() => {});
    setInterval(() => runEndpointWatchdogs().catch(() => {}), 45_000);
    logger.info("[HealthMonitor] Endpoint watchdog started — 5 groups × 4 endpoints every 45s");
  }, 8_000);

  setInterval(() => runChecks().catch(() => {}), 30_000);
  setInterval(() => runRpcChecks().catch(() => {}), 60_000);

  logger.info("[HealthMonitor] Watchdog active — subsystems every 30s | RPCs every 60s");
}
