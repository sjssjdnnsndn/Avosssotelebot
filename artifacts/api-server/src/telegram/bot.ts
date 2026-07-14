import TelegramBot from "node-telegram-bot-api";
import { logger } from "../lib/logger";

// This is imported from the aviso route — we read the same in-memory state
let botStateRef: () => Record<string, unknown>;

export function setBotStateRef(fn: () => Record<string, unknown>) {
  botStateRef = fn;
}

function statusEmoji(status: string): string {
  switch (status) {
    case "working": return "🟢";
    case "starting": return "🟡";
    case "waiting-for-tasks": return "🟡";
    case "short-sleep": return "💤";
    case "long-sleep": return "😴";
    case "sleeping": return "😴";
    case "done": return "✅";
    case "offline": return "🔴";
    default: return "⚪";
  }
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) + " IST";
}

function buildStatusMessage(state: Record<string, unknown>): string {
  const status = (state["status"] as string) ?? "unknown";
  const balance = (state["balance"] as string) ?? "?";
  const balanceRaw = (state["balanceRaw"] as number) ?? 0;
  const totalEarned = (state["totalEarned"] as number) ?? 0;
  const totalTasks = (state["totalTasks"] as number) ?? 0;
  const totalYtDone = (state["totalYtDone"] as number) ?? 0;
  const totalYtEarned = (state["totalYtEarned"] as number) ?? 0;
  const currentTask = (state["currentTask"] as string) ?? null;
  const lastUpdated = state["lastUpdated"] as string | null;
  const sleepUntil = state["sleepUntil"] as string | null;

  let msg = `🤖 *Aviso Bot Status*\n\n`;
  msg += `${statusEmoji(status)} *Status:* \`${status}\`\n`;
  msg += `💰 *Balance:* \`${balance}\` (raw: ${balanceRaw})\n`;
  msg += `📈 *Total Earned:* \`${totalEarned}\`\n`;
  msg += `📋 *Total Tasks:* \`${totalTasks}\`\n`;
  msg += `▶️ *YT Done:* \`${totalYtDone}\` | *YT Earned:* \`${totalYtEarned}\`\n`;

  if (currentTask) {
    msg += `⚙️ *Current Task:* \`${currentTask}\`\n`;
  }

  if (sleepUntil) {
    msg += `⏰ *Sleep Until:* ${formatDate(sleepUntil)}\n`;
  }

  msg += `\n🕐 *Last Updated:* ${formatDate(lastUpdated)}`;

  return msg;
}

export function startTelegramBot(): TelegramBot | null {
  const token = process.env["TELEGRAM_BOT_TOKEN"];

  if (!token) {
    logger.warn("TELEGRAM_BOT_TOKEN is not set — Telegram bot will not start.");
    return null;
  }

  const bot = new TelegramBot(token, { polling: true });

  logger.info("Telegram bot started and polling for messages");

  bot.on("polling_error", (err) => {
    logger.error({ err }, "Telegram bot polling error");
  });

  const sendStatus = async (chatId: number) => {
    const state = botStateRef ? botStateRef() : { status: "offline" };
    const text = buildStatusMessage(state);
    await bot.sendMessage(chatId, text, { parse_mode: "Markdown" });
  };

  bot.onText(/^\/start$/, async (msg) => {
    const text =
      `👋 *Aviso Monitor Bot*\n\n` +
      `Use these commands:\n` +
      `• /status — aviso bot ka status dekho\n` +
      `• /balance — current balance dekho\n` +
      `• /info — full details`;
    await bot.sendMessage(msg.chat.id, text, { parse_mode: "Markdown" });
  });

  bot.onText(/^\/(status|info|check)$/, async (msg) => {
    await sendStatus(msg.chat.id);
  });

  bot.onText(/^\/balance$/, async (msg) => {
    const state = botStateRef ? botStateRef() : { status: "offline", balance: "?" };
    const balance = (state["balance"] as string) ?? "?";
    const balanceRaw = (state["balanceRaw"] as number) ?? 0;
    const status = (state["status"] as string) ?? "unknown";
    const text =
      `💰 *Aviso Balance*\n\n` +
      `Balance: \`${balance}\`\n` +
      `Raw: \`${balanceRaw}\`\n` +
      `${statusEmoji(status)} Status: \`${status}\``;
    await bot.sendMessage(msg.chat.id, text, { parse_mode: "Markdown" });
  });

  // Handle any other message
  bot.on("message", async (msg) => {
    if (msg.text?.startsWith("/")) return; // already handled above
    await bot.sendMessage(
      msg.chat.id,
      "Use /status, /balance, or /info to check aviso bot.",
    );
  });

  return bot;
}
