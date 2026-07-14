import { Router } from "express";
import { spawn } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCRIPTS_DIR = path.resolve(__dirname, "../../../scripts/src");

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

function runScript(scriptFile: string, args: string[] = []): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn("node", [path.join(SCRIPTS_DIR, scriptFile), ...args], {
      env: { ...process.env },
      timeout: 90000,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    child.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });
    child.on("close", (code) => resolve({ code: code ?? 1, stdout, stderr }));
    child.on("error", (err) => resolve({ code: 1, stdout, stderr: err.message }));
  });
}

let loginInProgress = false;

router.post("/aviso/login/phase1", async (_req, res) => {
  if (loginInProgress) {
    res.json({ ok: false, needOtp: false, message: "Login already in progress — wait aur try karo" });
    return;
  }
  loginInProgress = true;
  try {
    const { code, stdout } = await runScript("aviso_login_phase1.cjs");
    loginInProgress = false;

    if (code === 0) {
      res.json({ ok: true, needOtp: false, message: "✅ Login successful! Fresh cookies save ho gayi — bot restart karo." });
    } else if (code === 2 || stdout.includes("NEED_OTP")) {
      res.json({ ok: true, needOtp: true, message: "📨 OTP bheja gaya — apna OTP enter karo." });
    } else {
      const lines = stdout.split("\n").filter(Boolean);
      const lastLines = lines.slice(-3).join(" | ");
      let hint = "";
      if (stdout.includes("aviso.bz/login")) hint = " (Captcha fail ya rate-limit — thodi der baad try karo)";
      else if (stdout.includes("dashboard markers")) hint = " (Credentials galat ya account blocked)";
      res.json({ ok: false, needOtp: false, message: `❌ Login failed${hint}: ${lastLines || "unknown error"}` });
    }
  } catch (err) {
    loginInProgress = false;
    res.json({ ok: false, needOtp: false, message: `❌ Server error: ${(err as Error).message}` });
  }
});

router.post("/aviso/login/phase2", async (req, res) => {
  const { otp } = req.body as { otp?: string };
  if (!otp || typeof otp !== "string" || !/^\d{4,8}$/.test(otp.trim())) {
    res.status(400).json({ ok: false, message: "Valid OTP (4-8 digits) do" });
    return;
  }
  if (loginInProgress) {
    res.json({ ok: false, message: "Login already in progress — wait karo" });
    return;
  }
  loginInProgress = true;
  try {
    const { code, stdout } = await runScript("aviso_login_phase2.cjs", [otp.trim()]);
    loginInProgress = false;
    if (code === 0) {
      res.json({ ok: true, message: "✅ OTP verified! Login successful — fresh cookies save ho gayi." });
    } else {
      res.json({ ok: false, message: `❌ OTP failed: ${stdout.split("\n").filter(Boolean).pop() ?? "invalid OTP"}` });
    }
  } catch (err) {
    loginInProgress = false;
    res.json({ ok: false, message: `❌ Server error: ${(err as Error).message}` });
  }
});

export default router;
