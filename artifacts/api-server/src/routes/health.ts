import { Router, type IRouter } from "express";
import { HealthCheckResponse } from "@workspace/api-zod";

const router: IRouter = Router();

router.get("/healthz", (_req, res) => {
  const data = HealthCheckResponse.parse({ status: "ok" });
  res.json(data);
});

// Keep-alive ping endpoint — for external cron jobs
router.get("/ping", (_req, res) => {
  res.json({ ok: true, ts: Date.now(), message: "pong" });
});

export default router;
