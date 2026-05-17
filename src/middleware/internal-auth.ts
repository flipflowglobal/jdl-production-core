import { Request, Response, NextFunction } from "express";

const INTERNAL_KEY = process.env.API_KEY || "";

export function internalAuth(req: Request, res: Response, next: NextFunction): void {
  if (!INTERNAL_KEY) {
    res.status(503).json({ error: "Service unavailable: API_KEY not configured" });
    return;
  }
  const key = req.headers["x-internal-api-key"];
  if (key === INTERNAL_KEY) {
    return next();
  }
  res.status(403).json({ error: "Forbidden" });
}
