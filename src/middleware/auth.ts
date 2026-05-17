import { Request, Response, NextFunction } from "express";

const API_KEY = process.env.API_KEY || "";

export function apiKeyCheck(req: Request, res: Response, next: NextFunction): void {
  if (!API_KEY) {
    console.error("[CRITICAL] API_KEY is not configured — rejecting all requests");
    res.status(503).json({ error: "Service unavailable: API_KEY not configured" });
    return;
  }
  if (req.headers["x-api-key"] === API_KEY) {
    return next();
  }
  res.status(401).json({ error: "Unauthorized" });
}
