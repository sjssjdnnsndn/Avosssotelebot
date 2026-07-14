import fs from "node:fs";
import path from "node:path";
import TelegramBot from "node-telegram-bot-api";
import { logger } from "../lib/logger";

const DOWNLOADS_DIR = path.resolve(process.cwd(), "downloads");

function ensureDownloadsDir() {
  if (!fs.existsSync(DOWNLOADS_DIR)) {
    fs.mkdirSync(DOWNLOADS_DIR, { recursive: true });
  }
}

function sanitizeFileName(name: string): string {
  return name.replace(/[^a-zA-Z0-9._-]/g, "_");
}

async function downloadFileToDisk(
  bot: TelegramBot,
  fileId: string,
  suggestedName: string,
): Promise<{ filePath: string; fileName: string }> {
  ensureDownloadsDir();

  const fileLink = await bot.getFileLink(fileId);
  const response = await fetch(fileLink);

  if (!response.ok || !response.body) {
    throw new Error(`Failed to download file: HTTP ${response.status}`);
  }

  const timestamp = Date.now();
  const safeName = sanitizeFileName(suggestedName);
  const fileName = `${timestamp}_${safeName}`;
  const filePath = path.join(DOWNLOADS_DIR, fileName);

  const arrayBuffer = await response.arrayBuffer();
  fs.writeFileSync(filePath, Buffer.from(arrayBuffer));

  return { filePath, fileName };
}

export function startTelegramBot(): TelegramBot | null {
  const token = process.env["TELEGRAM_BOT_TOKEN"];

  if (!token) {
    logger.warn(
      "TELEGRAM_BOT_TOKEN is not set — Telegram bot will not start.",
    );
    return null;
  }

  ensureDownloadsDir();

  const bot = new TelegramBot(token, { polling: true });

  logger.info("Telegram bot started and polling for messages");

  bot.on("polling_error", (err) => {
    logger.error({ err }, "Telegram bot polling error");
  });

  bot.onText(/^\/start$/, (msg) => {
    bot.sendMessage(
      msg.chat.id,
      "Hi! Send me any file, photo, video, or document and I'll save it for you.",
    );
  });

  bot.on("document", async (msg) => {
    const doc = msg.document;
    if (!doc) return;

    try {
      const { fileName } = await downloadFileToDisk(
        bot,
        doc.file_id,
        doc.file_name ?? `document_${doc.file_id}`,
      );
      logger.info({ fileName, chatId: msg.chat.id }, "Saved Telegram document");
      await bot.sendMessage(msg.chat.id, `Saved: ${doc.file_name ?? fileName}`);
    } catch (err) {
      logger.error({ err }, "Failed to save Telegram document");
      await bot.sendMessage(
        msg.chat.id,
        "Sorry, something went wrong saving that file.",
      );
    }
  });

  bot.on("photo", async (msg) => {
    const photos = msg.photo;
    if (!photos || photos.length === 0) return;

    const largest = photos[photos.length - 1];
    if (!largest) return;

    try {
      const { fileName } = await downloadFileToDisk(
        bot,
        largest.file_id,
        `photo_${largest.file_id}.jpg`,
      );
      logger.info({ fileName, chatId: msg.chat.id }, "Saved Telegram photo");
      await bot.sendMessage(msg.chat.id, `Saved photo: ${fileName}`);
    } catch (err) {
      logger.error({ err }, "Failed to save Telegram photo");
      await bot.sendMessage(
        msg.chat.id,
        "Sorry, something went wrong saving that photo.",
      );
    }
  });

  bot.on("video", async (msg) => {
    const video = msg.video;
    if (!video) return;

    try {
      const { fileName } = await downloadFileToDisk(
        bot,
        video.file_id,
        `video_${video.file_id}.mp4`,
      );
      logger.info({ fileName, chatId: msg.chat.id }, "Saved Telegram video");
      await bot.sendMessage(msg.chat.id, `Saved video: ${fileName}`);
    } catch (err) {
      logger.error({ err }, "Failed to save Telegram video");
      await bot.sendMessage(
        msg.chat.id,
        "Sorry, something went wrong saving that video.",
      );
    }
  });

  bot.on("audio", async (msg) => {
    const audio = msg.audio;
    if (!audio) return;

    try {
      const { fileName } = await downloadFileToDisk(
        bot,
        audio.file_id,
        audio.file_name ?? `audio_${audio.file_id}.mp3`,
      );
      logger.info({ fileName, chatId: msg.chat.id }, "Saved Telegram audio");
      await bot.sendMessage(msg.chat.id, `Saved audio: ${fileName}`);
    } catch (err) {
      logger.error({ err }, "Failed to save Telegram audio");
      await bot.sendMessage(
        msg.chat.id,
        "Sorry, something went wrong saving that audio.",
      );
    }
  });

  bot.on("voice", async (msg) => {
    const voice = msg.voice;
    if (!voice) return;

    try {
      const { fileName } = await downloadFileToDisk(
        bot,
        voice.file_id,
        `voice_${voice.file_id}.ogg`,
      );
      logger.info({ fileName, chatId: msg.chat.id }, "Saved Telegram voice note");
      await bot.sendMessage(msg.chat.id, `Saved voice note: ${fileName}`);
    } catch (err) {
      logger.error({ err }, "Failed to save Telegram voice note");
      await bot.sendMessage(
        msg.chat.id,
        "Sorry, something went wrong saving that voice note.",
      );
    }
  });

  return bot;
}
