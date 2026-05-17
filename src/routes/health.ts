import { Router, type IRouter } from "express";
import { getSystemHealth, getEndpointWatchdogs } from "../services/health-monitor.js";
import { getSystemHealth as getModuleHealth } from "../modules/index.js";
import { isKillSwitchActive, activateKillSwitch, liftKillSwitch } from "../modules/index.js";

const router: IRouter = Router();

function healthHandler(_req: any, res: any) {
  res.json({ status: "ok", timestamp: new Date().toISOString() });
}

function detailedHealthHandler(_req: any, res: any) {
  try {
    const health = getSystemHealth();
    const statusCode = health.status === "critical" ? 503 : health.status === "degraded" ? 207 : 200;
    res.status(statusCode).json(health);
  } catch (err: any) {
    res.status(500).json({ status: "critical", error: err.message });
  }
}

function watchdogHandler(_req: any, res: any) {
  try {
    const report = getEndpointWatchdogs();
    const statusCode =
      report.overall_status === "critical" ? 503 :
      report.overall_status === "degraded" ? 207 : 200;
    res.status(statusCode).json(report);
  } catch (err: any) {
    res.status(500).json({ overall_status: "critical", error: err.message });
  }
}

function modulesHealthHandler(_req: any, res: any) {
  try {
    const snapshot = getModuleHealth();
    const statusCode = snapshot.overallStatus === "critical" ? 503
      : snapshot.overallStatus === "degraded" ? 207
      : 200;
    res.status(statusCode).json(snapshot);
  } catch (err: any) {
    res.status(500).json({ overallStatus: "critical", error: err.message });
  }
}

function killSwitchHandler(req: any, res: any) {
  const { action, reason } = req.body ?? {};
  if (action === "activate") {
    activateKillSwitch(reason ?? "Manual activation via API");
    res.json({ killSwitchActive: true, reason });
  } else if (action === "lift") {
    liftKillSwitch();
    res.json({ killSwitchActive: false });
  } else {
    res.json({ killSwitchActive: isKillSwitchActive() });
  }
}

router.get("/healthz", healthHandler);
router.get("/health", healthHandler);
router.get("/health/detailed", detailedHealthHandler);
router.get("/health/watchdogs", watchdogHandler);
router.get("/health/modules", modulesHealthHandler);
router.post("/health/kill-switch", killSwitchHandler);

export default router;
