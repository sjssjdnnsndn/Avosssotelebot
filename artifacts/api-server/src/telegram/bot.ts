import TelegramBot from "node-telegram-bot-api";
import { logger } from "../lib/logger";

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
  const status = (state["status"] as string) ?? "offline";
  const balance = (state["balance"] as string) ?? "?";
  const totalEarned = (state["totalEarned"] as number) ?? 0;
  const totalTasks = (state["totalTasks"] as number) ?? 0;
  const lastUpdated = state["lastUpdated"] as string | null;

  let msg = `🤖 *Aviso Bot*\n\n`;
  msg += `${statusEmoji(status)} *Status:* \`${status}\`\n`;
  msg += `💰 *Balance:* \`${balance}\`\n`;
  msg += `📈 *Total Earned:* \`${totalEarned}\`\n`;
  msg += `📋 *Tasks Done:* \`${totalTasks}\`\n`;
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
    await bot.sendMessage(chatId, buildStatusMessage(state), { parse_mode: "Markdown" });
  };

  bot.onText(/^\/start$/, async (msg) => {
    await bot.sendMessage(
      msg.chat.id,
      `👋 *Aviso Monitor*\n\n/status — balance aur status dekho`,
      { parse_mode: "Markdown" }
    );
  });

  bot.onText(/^\/(status|balance|info|check)$/, async (msg) => {
    await sendStatus(msg.chat.id);
  });

  bot.on("message", async (msg) => {
    if (msg.text?.startsWith("/")) return;
    await sendStatus(msg.chat.id);
  });

  return bot;
}
