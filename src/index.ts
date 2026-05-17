import app from "./app";
import { logger } from "./lib/logger";
import { initSystemWallet } from "./services/blockchain";
import { startPriceFeed } from "./services/price-feed";
import { startHealthMonitor } from "./services/health-monitor";

process.on("uncaughtException", (err) => {
  logger.error({ err }, "[FATAL] Uncaught exception");
});

process.on("unhandledRejection", (reason) => {
  logger.error({ reason }, "[WARN] Unhandled promise rejection");
});

process.on("SIGTERM", () => {
  logger.info("Received SIGTERM — graceful shutdown");
  process.exit(0);
});

const rawPort = process.env["PORT"];
if (!rawPort) {
  throw new Error("PORT environment variable is required");
}
const port = Number(rawPort);
if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

const systemWallet = initSystemWallet();
logger.info({ address: systemWallet.address, isNew: systemWallet.isNew }, "System wallet initialized");
if (systemWallet.isNew) {
  logger.warn("NEW system wallet generated — save SYSTEM_WALLET_PRIVATE_KEY to persist across restarts");
}

app.listen(port, () => {
  logger.info({ port }, "JDL Production Core started");
  startPriceFeed();
  startHealthMonitor();
});
