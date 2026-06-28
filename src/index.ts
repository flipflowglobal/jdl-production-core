import "dotenv/config";
import { validateConfig, CONFIG } from './config';
import app from "./app";
import { logger } from "./lib/logger";
import { initDatabase } from "./services/database";
import { initSystemWallet } from "./services/blockchain";
import { startPriceFeed } from "./services/price-feed";
import { startHealthMonitor } from "./services/health-monitor";
import { pool } from "./services/database";
import http from "http";

let server: http.Server | undefined;

validateConfig();

process.on("uncaughtException", (err) => {
  logger.error({ err }, "[FATAL] Uncaught exception");
  process.exit(1);
});

process.on("unhandledRejection", (reason) => {
  logger.error({ reason }, "[WARN] Unhandled promise rejection");
});

process.on("SIGTERM", async () => {
  logger.info("Received SIGTERM — graceful shutdown");
  server?.close();
  await pool?.end?.();
  process.exit(0);
});

async function main() {
  const rawPort = process.env["PORT"];
  if (!rawPort) {
    throw new Error("PORT environment variable is required");
  }
  const port = Number(rawPort);
  if (Number.isNaN(port) || port <= 0) {
    throw new Error(`Invalid PORT value: "${rawPort}"`);
  }

  try {
    await pool.query("SELECT 1");
    logger.info("Database connection OK");
  } catch (err) {
    logger.warn({ err }, "Database unreachable on startup — deferring to health monitor");
  }

  await initDatabase();

  const systemWallet = initSystemWallet();
  logger.info({ address: systemWallet.address, isNew: systemWallet.isNew }, "System wallet initialized");
  if (systemWallet.isNew) {
    logger.warn("NEW system wallet generated — save SYSTEM_WALLET_PRIVATE_KEY to persist across restarts");
  }

  const isDryRun = process.env.DRY_RUN === "true" || process.env.DRY_RUN === "1";
  logger.info({ dryRun: isDryRun }, isDryRun ? "DRY RUN mode — no real transactions" : "LIVE mode — real transactions enabled");

  server = app.listen(port, () => {
    logger.info({ port }, "JDL Production Core started");
    startPriceFeed();
    startHealthMonitor();
  });
}

main();
