import express, { type Express } from "express";
import cors from "cors";
import helmet from "helmet";
import rateLimit from "express-rate-limit";
import session from "express-session";
import pinoHttp from "pino-http";
import pino from 'pino-http';

function sanitizeHeaders(headers: any) {
  const safe = { ...headers };
  delete safe.authorization;  // Remove Bearer tokens
  delete safe['x-api-key'];
  return safe;
}
import { apiKeyCheck } from "./middleware/auth";
import router from "./routes";
import { setupHealth } from './health';
import { logger } from "./lib/logger";

const app: Express = express();

app.set("trust proxy", 1);

app.use(helmet({
  contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        scriptSrc: ["'self'"],      // No inline scripts
        styleSrc: ["'self'", "'unsafe-inline'"],  // CSS safe
        imgSrc: ["'self'", 'data:', 'https:'],
        connectSrc: ["'self'", 'https://api.alchemy.com', 'https://relay.flashbots.net'],
        fontSrc: ["'self'"],
      },
    },
  crossOriginEmbedderPolicy: false,
}));

app.use(cors({ origin: (origin, callback) => {
    const allowed = process.env.CORS_ORIGIN?.split(',') || ['http://localhost:3000'];
    if (!origin || allowed.includes(origin)) {
      callback(null, true);
    } else {
      callback(new Error('CORS not allowed'));
    }
  }, credentials: true }));

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
    httpOnly: true,      // Prevent JS access
    secure: true,        // HTTPS only
    sameSite: 'strict',  // CSRF protection
    maxAge: 3600000,     // 1 hour
  },
}));

app.use(
  pino({
    logger,
    serializers: {
      req(req) {
        return {
          method: req.method,
          url: req.url.split('?')[0], // Strip query params
          headers: sanitizeHeaders(req.headers),
        };
      },
    },
  })
);

app.use(express.json());
app.use(express.urlencoded({ extended: true }));

setupHealth(app);
app.use("/api", router);

export default app;
