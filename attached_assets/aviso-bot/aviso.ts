import { Router } from "express";

const router = Router();

const VALID_STATUSES = new Set([
  "starting", "working", "waiting-for-tasks", "long-sleep",
  "short-sleep", "offline", "sleeping", "done",
]);

const ALLOWED_STRING_FIELDS = new Set(["status", "balance", "currentTask"]);
const ALLOWED_NUMBER_FIELDS = new Set([
  "balanceRaw", "totalEarned", "totalTasks",
  "totalYtDone", "totalYtEarned", "sessionStart",
]);
const ALLOWED_ISO_FIELDS = new Set(["sleepUntil", "lastUpdated"]);
const ALLOWED_ARRAY_FIELDS = new Set(["log", "balanceHistory"]);

function sanitize(raw: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};

  for (const key of ALLOWED_STRING_FIELDS) {
    if (key in raw && typeof raw[key] === "string") {
      out[key] = (raw[key] as string).slice(0, 500);
    }
  }
  if (out.status && !VALID_STATUSES.has(out.status as string)) {
    out.status = "waiting-for-tasks";
  }

  for (const key of ALLOWED_NUMBER_FIELDS) {
    if (key in raw && typeof raw[key] === "number" && isFinite(raw[key] as number)) {
      out[key] = raw[key];
    }
  }

  for (const key of ALLOWED_ISO_FIELDS) {
    if (key in raw) {
      const val = raw[key];
      if (val === null) { out[key] = null; continue; }
      if (typeof val === "string" && !isNaN(Date.parse(val))) {
        out[key] = val;
      }
    }
  }

  for (const key of ALLOWED_ARRAY_FIELDS) {
    if (key in raw && Array.isArray(raw[key])) {
      out[key] = (raw[key] as unknown[]).slice(0, 200);
    }
  }

  return out;
}

let botState: Record<string, unknown> = {
  status: "offline",
  balance: "?",
  balanceRaw: 0,
  totalEarned: 0,
  totalTasks: 0,
  totalYtDone: 0,
  totalYtEarned: 0,
  currentTask: null,
  sleepUntil: null,
  lastUpdated: null,
  log: [],
  balanceHistory: [],
};

router.post("/aviso/update", (req, res) => {
  if (!req.body || typeof req.body !== "object" || Array.isArray(req.body)) {
    res.status(400).json({ ok: false, error: "Invalid body" });
    return;
  }
  const clean = sanitize(req.body as Record<string, unknown>);
  botState = { ...botState, ...clean, lastUpdated: new Date().toISOString() };
  res.json({ ok: true });
});

router.get("/aviso/status", (_req, res) => {
  res.json(botState);
});

export default router;
