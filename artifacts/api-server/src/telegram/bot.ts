import TelegramBot from "node-telegram-bot-api";
import http from "http";
import { logger } from "../lib/logger";

let botStateRef: () => Record<string, unknown>;

export function setBotStateRef(fn: () => Record<string, unknown>) {
  botStateRef = fn;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function statusEmoji(status: string): string {
  switch (status) {
    case "working":          return "🟢";
    case "starting":         return "🟡";
    case "waiting-for-tasks":return "🟡";
    case "short-sleep":      return "💤";
    case "long-sleep":       return "😴";
    case "sleeping":         return "😴";
    case "done":             return "✅";
    case "offline":          return "🔴";
    default:                 return "⚪";
  }
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) + " IST";
}

function buildStatusMessage(state: Record<string, unknown>): string {
  const status      = (state["status"]      as string) ?? "offline";
  const balance     = (state["balance"]     as string) ?? "?";
  const totalEarned = (state["totalEarned"] as number) ?? 0;
  const totalTasks  = (state["totalTasks"]  as number) ?? 0;
  const lastUpdated = state["lastUpdated"]  as string | null;

  let msg = `🤖 *Aviso Bot*\n\n`;
  msg += `${statusEmoji(status)} *Status:* \`${status}\`\n`;
  msg += `💰 *Balance:* \`${balance}\`\n`;
  msg += `📈 *Total Earned:* \`${totalEarned}\`\n`;
  msg += `📋 *Tasks Done:* \`${totalTasks}\`\n`;
  msg += `\n🕐 *Last Updated:* ${formatDate(lastUpdated)}`;
  return msg;
}

// ── Local API caller (same process, localhost) ────────────────────────────────

interface ApiResponse { ok: boolean; needOtp?: boolean; message?: string; error?: string }

function callLocalPost(path: string, body: Record<string, unknown> = {}): Promise<ApiResponse> {
  return new Promise((resolve) => {
    const port = parseInt(process.env["PORT"] ?? "5000", 10);
    const data = JSON.stringify(body);
    const req  = http.request(
      { hostname: "127.0.0.1", port, path, method: "POST",
        headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) } },
      (res) => {
        let raw = "";
        res.on("data", (c: Buffer) => { raw += c.toString(); });
        res.on("end", () => {
          try { resolve(JSON.parse(raw) as ApiResponse); }
          catch { resolve({ ok: false, message: "Invalid response from server" }); }
        });
      }
    );
    req.on("error", (err) => resolve({ ok: false, message: err.message }));
    req.setTimeout(90_000, () => { req.destroy(); resolve({ ok: false, message: "Timeout (90s)" }); });
    req.write(data);
    req.end();
  });
}

// ── Re-login state ────────────────────────────────────────────────────────────

// chatIds currently waiting for an OTP reply
const waitingForOtp = new Set<number>();

// ── Bot init ──────────────────────────────────────────────────────────────────

export function startTelegramBot(): TelegramBot | null {
  const token = process.env["TELEGRAM_BOT_TOKEN"];
  if (!token) {
    logger.warn("TELEGRAM_BOT_TOKEN is not set — Telegram bot will not start.");
    return null;
  }

  // Only start polling on port 5000 to avoid 409 Conflict
  const port = process.env["PORT"];
  if (port && port !== "5000") {
    logger.info({ port }, "Telegram bot skipped — only starts on port 5000");
    return null;
  }

  const bot = new TelegramBot(token, { polling: true });
  logger.info("Telegram bot started and polling for messages");

  bot.on("polling_error", (err) => {
    logger.error({ err }, "Telegram bot polling error");
  });

  // ── /start ──────────────────────────────────────────────────────────────────
  bot.onText(/^\/start$/, async (msg) => {
    await bot.sendMessage(
      msg.chat.id,
      `👋 *Aviso Monitor*\n\n` +
      `/status — balance aur status\n` +
      `/relogin — cookies expire hon to re-login karo`,
      {
        parse_mode: "Markdown",
        reply_markup: {
          inline_keyboard: [[
            { text: "📊 Status", callback_data: "status" },
            { text: "🔄 Re-Login", callback_data: "relogin" },
          ]],
        },
      }
    );
  });

  // ── /status ─────────────────────────────────────────────────────────────────
  const sendStatus = async (chatId: number) => {
    const state = botStateRef ? botStateRef() : { status: "offline" };
    await bot.sendMessage(chatId, buildStatusMessage(state), { parse_mode: "Markdown" });
  };

  bot.onText(/^\/(status|balance|info|check)$/, async (msg) => {
    await sendStatus(msg.chat.id);
  });

  // ── /relogin ─────────────────────────────────────────────────────────────────
  bot.onText(/^\/relogin$/, async (msg) => {
    await handleRelogin(msg.chat.id);
  });

  // ── Inline keyboard callbacks ─────────────────────────────────────────────
  bot.on("callback_query", async (query) => {
    const chatId = query.message?.chat.id;
    if (!chatId) return;
    await bot.answerCallbackQuery(query.id);

    if (query.data === "status") {
      await sendStatus(chatId);
    } else if (query.data === "relogin") {
      await handleRelogin(chatId);
    }
  });

  // ── Generic messages (OTP input or status) ───────────────────────────────
  bot.on("message", async (msg) => {
    if (msg.text?.startsWith("/")) return;
    const chatId = msg.chat.id;
    const text   = msg.text?.trim() ?? "";

    // OTP mode — user ki next message OTP hai
    if (waitingForOtp.has(chatId)) {
      if (!/^\d{4,8}$/.test(text)) {
        await bot.sendMessage(chatId, "⚠️ Sirf 4-8 digit ka OTP bhejo (e.g. `123456`)", { parse_mode: "Markdown" });
        return;
      }

      waitingForOtp.delete(chatId);
      await bot.sendMessage(chatId, "🔐 OTP verify kar raha hun, thoda ruko...");

      const res = await callLocalPost("/api/aviso/login/phase2", { otp: text });

      if (res.ok) {
        await bot.sendMessage(
          chatId,
          "✅ *Login successful!*\n\nOTP verified. Fresh cookies save ho gayi — bot ab naye session se kaam karega.",
          { parse_mode: "Markdown" }
        );
      } else {
        await bot.sendMessage(
          chatId,
          `❌ *OTP fail ho gaya*\n\n${res.message ?? "Unknown error"}\n\n/relogin se dobara try karo.`,
          { parse_mode: "Markdown" }
        );
      }
      return;
    }

    // Default: show status
    await sendStatus(chatId);
  });

  // ── Re-login handler ──────────────────────────────────────────────────────
  async function handleRelogin(chatId: number) {
    await bot.sendMessage(chatId, "🔄 Re-login shuru kar raha hun... (30-60 sec lagenge)", { parse_mode: "Markdown" });

    const res = await callLocalPost("/api/aviso/login/phase1");

    if (res.needOtp) {
      // 2FA detected — OTP maango
      waitingForOtp.add(chatId);
      await bot.sendMessage(
        chatId,
        "📨 *OTP chahiye!*\n\nAviso ne 2FA maanga hai.\nApna OTP yahan bhejo (sirf digits):",
        {
          parse_mode: "Markdown",
          reply_markup: { force_reply: true, selective: true },
        }
      );
    } else if (res.ok) {
      await bot.sendMessage(
        chatId,
        "✅ *Login successful!*\n\nFresh cookies save ho gayi — 2FA nahi tha.\nBot naye session se kaam karega.",
        { parse_mode: "Markdown" }
      );
    } else {
      await bot.sendMessage(
        chatId,
        `❌ *Login fail ho gaya*\n\n${res.message ?? "Unknown error"}\n\nThodi der baad /relogin dobara try karo.`,
        { parse_mode: "Markdown" }
      );
    }
  }

  return bot;
}
