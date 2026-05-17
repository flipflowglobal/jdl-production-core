import "dotenv/config";
import express, { type Express } from "express";
import cors from "cors";
import helmet from "helmet";
import rateLimit from "express-rate-limit";
import session from "express-session";
import pinoHttp from "pino-http";
import { createProxyMiddleware } from "http-proxy-middleware";
import { apiKeyCheck } from "./middleware/auth";
import { logger } from "./lib/logger";

const app: Express = express();

app.set("trust proxy", 1);

app.use(helmet({
  contentSecurityPolicy: false,
  crossOriginEmbedderPolicy: false,
}));

app.use(cors({ origin: process.env.CORS_ORIGIN?.split(",") || ["http://localhost:3000"], credentials: true }));

const limiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 500,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many requests, please try again later" },
});
app.use(limiter);

app.use('/api', apiKeyCheck);

const sessionSecret = process.env.SESSION_SECRET;
if (!sessionSecret) {
  if (process.env.NODE_ENV === "production") {
    logger.fatal("SESSION_SECRET must be set in production");
    process.exit(1);
  }
  logger.warn("SESSION_SECRET not set — using dev-only fallback");
}
const resolvedSecret: string = sessionSecret || "dev-only-secret-not-for-production";
app.use(session({
  secret: resolvedSecret,
  resave: false,
  saveUninitialized: false,
  cookie: {
    secure: process.env.NODE_ENV === "production",
    httpOnly: true,
    maxAge: 24 * 60 * 60 * 1000,
    sameSite: "lax",
  },
}));

app.use(pinoHttp({
  logger,
  serializers: {
    req(req) { return { id: req.id, method: req.method, url: req.url?.split("?")[0] }; },
    res(res) { return { statusCode: res.statusCode }; },
  },
}));

app.use(express.json());
app.use(express.urlencoded({ extended: true }));

const TRADING_TARGET = process.env.TRADING_URL || "http://trading:8501";
const WORKER_TARGET = process.env.WORKER_URL || "http://worker:8502";

app.use("/api/flash-loans", createProxyMiddleware({ target: TRADING_TARGET, changeOrigin: true }));
app.use("/api/agents", createProxyMiddleware({ target: TRADING_TARGET, changeOrigin: true }));
app.use("/api/agent-wallets", createProxyMiddleware({ target: TRADING_TARGET, changeOrigin: true }));
app.use("/api/strategies", createProxyMiddleware({ target: TRADING_TARGET, changeOrigin: true }));
app.use("/api/blockchain", createProxyMiddleware({ target: TRADING_TARGET, changeOrigin: true }));

app.use("/api", createProxyMiddleware({ target: WORKER_TARGET, changeOrigin: true }));

const port = Number(process.env.PORT) || 8500;
app.listen(port, () => {
  logger.info({ port, trading: TRADING_TARGET, worker: WORKER_TARGET }, "API Gateway started");
});
