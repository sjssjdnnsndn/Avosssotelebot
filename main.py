"""
Instagram Video Downloader - Telegram Bot
Features:
- Download IG reels / posts / IGTV / photos via yt-dlp
- Per-user 3 free downloads / 24h, extra via referral credits (+5 per invite)
- Admin panel (stats + broadcast supporting text/photo/audio/photo+caption)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import yt_dlp

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
DAILY_FREE_LIMIT = int(os.environ.get("DAILY_FREE_LIMIT", "3"))
REFERRAL_BONUS = int(os.environ.get("REFERRAL_BONUS", "5"))

MAX_TG_FILE_BYTES = 50 * 1024 * 1024  # Telegram bot upload limit
COOKIES_FILE = str(ROOT_DIR / "ig_cookies.txt")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ig_bot")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client[DB_NAME]
users_col = db["users"]

BOT_USERNAME: str | None = None  # filled on startup

WELCOME_MESSAGE = (
    "🎬 *Instagram Video Downloader Bot* 🎬\n\n"
    "🌟 *Welcome!*\n\n"
    "Main aapke liye Instagram videos download kar sakta hoon! 📥\n\n"
    "📝 *Kaise use karein:*\n"
    "1️⃣ Mujhe Instagram video ka link bhejein\n"
    "2️⃣ Main video download karunga\n"
    "3️⃣ Aapko video send kar dunga! 🎉\n\n"
    "💡 *Example:*\n"
    "`https://www.instagram.com/reel/...`\n"
    "`https://www.instagram.com/p/...`\n\n"
    "✨ *Supported:*\n"
    "✅ Instagram Reels\n"
    "✅ Instagram Posts\n"
    "✅ IGTV Videos\n"
    "✅ Instagram Photos\n\n"
    "_Simple hai! Just link bhejo aur magic dekho!_ ✨\n\n"
    "⚠️ *Note:* Only public videos download ho sakti hain!\n\n"
    "🎁 Har 24 ghante mein *3 free downloads* milte hain.\n"
    "👥 Friends invite karke *+5 credits* per invite kamayein! /referral"
)

HELP_MESSAGE = (
    "ℹ️ *Help*\n\n"
    "Sirf Instagram ka public link bhejein - reel, post, IGTV ya photo.\n\n"
    "*Commands:*\n"
    "/start - Welcome message\n"
    "/help - Help\n"
    "/me - Aapke downloads aur credits\n"
    "/referral - Referral link\n"
)

INSTAGRAM_URL_REGEX = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv|share)/[^\s]+",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------
async def ensure_user(tg_user, referred_by: int | None = None, bot=None) -> dict:
    """Ensure user exists in DB. Returns the user document (without _id)."""
    existing = await users_col.find_one({"user_id": tg_user.id}, {"_id": 0})
    if existing:
        return existing

    doc = {
        "user_id": tg_user.id,
        "username": tg_user.username,
        "first_name": tg_user.first_name,
        "joined_at": datetime.now(timezone.utc).isoformat(),
        "referred_by": referred_by if referred_by and referred_by != tg_user.id else None,
        "credits": 0,
        "downloads_success": 0,
        "downloads_fail": 0,
        "recent_downloads": [],
        "referrals_count": 0,
    }
    await users_col.insert_one(dict(doc))

    if doc["referred_by"]:
        result = await users_col.update_one(
            {"user_id": doc["referred_by"]},
            {"$inc": {"credits": REFERRAL_BONUS, "referrals_count": 1}},
        )
        if result.matched_count and bot is not None:
            try:
                await bot.send_message(
                    chat_id=doc["referred_by"],
                    text=(
                        f"🎉 *Naya referral!*\n\n"
                        f"{tg_user.first_name or 'Koi'} aapke link se join hua.\n"
                        f"💰 +{REFERRAL_BONUS} credits add ho gaye!"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
    return doc


async def get_quota(user_id: int) -> tuple[int, int, int]:
    """Return (used_today, free_remaining, credits)."""
    user = await users_col.find_one({"user_id": user_id}, {"_id": 0})
    if not user:
        return 0, DAILY_FREE_LIMIT, 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = [t for t in (user.get("recent_downloads") or []) if datetime.fromisoformat(t) > cutoff]
    used = len(recent)
    free_remaining = max(0, DAILY_FREE_LIMIT - used)
    return used, free_remaining, user.get("credits", 0)


async def try_consume_quota(user_id: int) -> tuple[bool, str]:
    """Try to consume one download. Returns (allowed, source) where source is 'free' or 'credit'."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    user = await users_col.find_one({"user_id": user_id}, {"_id": 0})
    if not user:
        return False, ""
    recent = [t for t in (user.get("recent_downloads") or []) if datetime.fromisoformat(t) > cutoff]
    if len(recent) < DAILY_FREE_LIMIT:
        recent.append(now.isoformat())
        await users_col.update_one(
            {"user_id": user_id}, {"$set": {"recent_downloads": recent}}
        )
        return True, "free"
    if user.get("credits", 0) > 0:
        await users_col.update_one({"user_id": user_id}, {"$inc": {"credits": -1}})
        return True, "credit"
    return False, ""


def referral_link(user_id: int) -> str:
    uname = BOT_USERNAME or "this_bot"
    return f"https://t.me/{uname}?start=ref_{user_id}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    referrer = None
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer = int(arg[4:])
            except ValueError:
                pass
    await ensure_user(update.effective_user, referred_by=referrer, bot=context.bot)
    await update.message.reply_text(
        WELCOME_MESSAGE,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update.effective_user, bot=context.bot)
    await update.message.reply_text(HELP_MESSAGE, parse_mode=ParseMode.MARKDOWN)


async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await ensure_user(user, bot=context.bot)
    used, free_left, credits = await get_quota(user.id)
    doc = await users_col.find_one({"user_id": user.id}, {"_id": 0}) or {}
    text = (
        f"👤 *Aapka Account*\n\n"
        f"🆔 ID: `{user.id}`\n"
        f"📥 Last 24h downloads: *{used}/{DAILY_FREE_LIMIT}*\n"
        f"🆓 Free remaining: *{free_left}*\n"
        f"💰 Credits: *{credits}*\n"
        f"👥 Total referrals: *{doc.get('referrals_count', 0)}*\n"
        f"✅ Successful: *{doc.get('downloads_success', 0)}*\n"
        f"❌ Failed: *{doc.get('downloads_fail', 0)}*\n\n"
        f"🔗 Aapka referral link:\n`{referral_link(user.id)}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await ensure_user(user, bot=context.bot)
    doc = await users_col.find_one({"user_id": user.id}, {"_id": 0}) or {}
    link = referral_link(user.id)
    text = (
        f"🎁 *Referral Program*\n\n"
        f"Har naye user ke join hone par aapko *+{REFERRAL_BONUS} credits* milte hain!\n"
        f"1 credit = 1 extra download (24h limit ke baad bhi).\n\n"
        f"👥 Total referrals: *{doc.get('referrals_count', 0)}*\n"
        f"💰 Current credits: *{doc.get('credits', 0)}*\n\n"
        f"🔗 *Aapka link:*\n`{link}`\n\n"
        f"_Share karein aur unlimited downloads pao!_"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
def admin_keyboard() -> InlineKeyboardMarkup:
    cookie_label = "🍪 Cookies: ✅" if _cookies_present() else "🍪 Set Cookies"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Stats", callback_data="admin:stats")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin:broadcast")],
            [InlineKeyboardButton(cookie_label, callback_data="admin:cookies")],
            [InlineKeyboardButton("❌ Close", callback_data="admin:close")],
        ]
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Aap admin nahi hain.")
        return
    await update.message.reply_text("👑 *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cleared = []
    if context.user_data.pop("awaiting_broadcast", False):
        cleared.append("broadcast")
    if context.user_data.pop("awaiting_cookie", False):
        cleared.append("cookie")
    if cleared:
        await update.message.reply_text(f"❌ Cancelled: {', '.join(cleared)}")
    else:
        await update.message.reply_text("Kuch pending nahi tha.")


async def setcookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Aap admin nahi hain.")
        return
    context.user_data["awaiting_cookie"] = True
    await update.message.reply_text(
        "🍪 *Instagram Cookies Setup*\n\n"
        "Apne browser se Instagram cookies copy karke yahan paste karen.\n\n"
        "*Easy method:*\n"
        "1️⃣ Chrome/Firefox extension install karen: *Get cookies.txt LOCALLY* (free)\n"
        "2️⃣ instagram.com par login karen\n"
        "3️⃣ Extension kholen → Export cookies\n"
        "4️⃣ `sessionid`, `ds_user_id`, `csrftoken` values copy karen\n\n"
        "*Format:*\n"
        "`sessionid=ABCDxyz; ds_user_id=12345; csrftoken=XYZ`\n\n"
        "_Sirf sessionid bhejen to bhi chalega._\n\n"
        "⚠️ Ye cookies safe rahengi (sirf bot ke pas), kabhi share nahi hongi.\n"
        "Cancel: /cancel",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cookiestatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    if _cookies_present():
        try:
            with open(COOKIES_FILE) as f:
                content = f.read()
            names = re.findall(r"\t([a-z_]+)\t[^\t\n]+$", content, re.MULTILINE)
            await update.message.reply_text(
                f"🍪 Cookies set: ✅\nKeys: `{', '.join(names) or 'unknown'}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            await update.message.reply_text("🍪 Cookies file present but unreadable.")
    else:
        await update.message.reply_text("🍪 Koi cookies set nahi hain. /setcookie use karen.")


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("Not allowed", show_alert=True)
        return
    await q.answer()
    action = q.data.split(":", 1)[1]

    if action == "stats":
        total_users = await users_col.count_documents({})
        agg = await users_col.aggregate(
            [
                {
                    "$group": {
                        "_id": None,
                        "success": {"$sum": "$downloads_success"},
                        "fail": {"$sum": "$downloads_fail"},
                        "credits": {"$sum": "$credits"},
                        "refs": {"$sum": "$referrals_count"},
                    }
                }
            ]
        ).to_list(1)
        s = agg[0] if agg else {"success": 0, "fail": 0, "credits": 0, "refs": 0}
        active_24h = await users_col.count_documents(
            {"recent_downloads.0": {"$exists": True}}
        )
        text = (
            "📊 *Bot Statistics*\n\n"
            f"👥 Total Users: *{total_users}*\n"
            f"🟢 Active (24h): *{active_24h}*\n"
            f"✅ Successful Downloads: *{s.get('success', 0)}*\n"
            f"❌ Failed Downloads: *{s.get('fail', 0)}*\n"
            f"💰 Total Credits in circulation: *{s.get('credits', 0)}*\n"
            f"🔗 Total Referrals: *{s.get('refs', 0)}*\n"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())

    elif action == "broadcast":
        context.user_data["awaiting_broadcast"] = True
        await q.edit_message_text(
            "📢 *Broadcast Mode*\n\n"
            "Ab jo bhi message bhejenge wo *sabhi users* ko forward ho jayega.\n\n"
            "Supported: text, photo, photo+caption, audio, voice, video, document\n\n"
            "Cancel: /cancel",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action == "cookies":
        context.user_data["awaiting_cookie"] = True
        await q.edit_message_text(
            "🍪 *Instagram Cookies Setup*\n\n"
            "Apne browser se cookies copy karke yahan paste karen.\n\n"
            "*Steps:*\n"
            "1️⃣ Chrome extension install karen: *Get cookies.txt LOCALLY*\n"
            "2️⃣ instagram.com par login karen\n"
            "3️⃣ Extension se cookies export karen\n"
            "4️⃣ `sessionid`, `ds_user_id`, `csrftoken` copy karen\n\n"
            "*Paste format:*\n"
            "`sessionid=ABCxyz; ds_user_id=12345; csrftoken=ABCxyz`\n\n"
            "_Sirf sessionid bhejen to bhi chalega._\n\n"
            "Cancel: /cancel",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action == "close":
        await q.edit_message_text("👑 Admin panel closed.")


async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward whatever message admin just sent to all known users using copy_message."""
    context.user_data["awaiting_broadcast"] = False
    src_chat = update.effective_chat.id
    src_msg = update.message.message_id

    cursor = users_col.find({}, {"user_id": 1, "_id": 0})
    user_ids: list[int] = [u["user_id"] async for u in cursor]
    total = len(user_ids)

    status = await update.message.reply_text(f"📤 Broadcasting to {total} users…")
    sent = failed = 0

    for idx, uid in enumerate(user_ids, start=1):
        try:
            await context.bot.copy_message(chat_id=uid, from_chat_id=src_chat, message_id=src_msg)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.info("Broadcast skip %s: %s", uid, exc)
        # rate-limit: ~25 msg/sec to be safe
        await asyncio.sleep(0.04)
        if idx % 25 == 0:
            try:
                await status.edit_text(f"📤 Broadcasting… {idx}/{total}\n✅ {sent} | ❌ {failed}")
            except Exception:
                pass

    await status.edit_text(
        f"✅ *Broadcast complete*\n\n"
        f"Total: {total}\n✅ Sent: {sent}\n❌ Failed: {failed}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------------------------------------------------------------------------
# Download flow
# ---------------------------------------------------------------------------
def _write_cookies(raw: str) -> int:
    """Parse 'key=value; key=value' style cookie string and write Netscape cookies.txt.
    Returns number of cookies written."""
    pairs: dict[str, str] = {}
    for chunk in re.split(r"[;\n]", raw.strip()):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            k, v = k.strip(), v.strip().strip('"')
            if k and v:
                pairs[k] = v
    if not pairs:
        return 0
    expiry = int((datetime.now(timezone.utc) + timedelta(days=365)).timestamp())
    lines = ["# Netscape HTTP Cookie File"]
    for k, v in pairs.items():
        # domain, includeSubdomains, path, secure, expiry, name, value
        lines.append(f".instagram.com\tTRUE\t/\tTRUE\t{expiry}\t{k}\t{v}")
    with open(COOKIES_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(COOKIES_FILE, 0o600)
    return len(pairs)


def _cookies_present() -> bool:
    return os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 50


def _download_with_ytdlp(url: str, out_dir: str) -> list[str]:
    outtmpl = os.path.join(out_dir, "%(id)s_%(autonumber)s.%(ext)s")
    ydl_opts: dict = {
        "outtmpl": outtmpl,
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
        "merge_output_format": "mp4",
        "extractor_retries": 3,
        "sleep_interval_requests": 1,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-IG-App-ID": "936619743392459",
            "X-ASBD-ID": "129477",
            "X-IG-WWW-Claim": "0",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Origin": "https://www.instagram.com",
            "Referer": "https://www.instagram.com/",
        },
    }
    if _cookies_present():
        ydl_opts["cookiefile"] = COOKIES_FILE

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    files: list[str] = []
    if info is None:
        return files
    entries = info.get("entries") if info.get("_type") == "playlist" else [info]
    for entry in entries:
        if not entry:
            continue
        fp = entry.get("filepath") or entry.get("_filename")
        if not fp:
            rds = entry.get("requested_downloads") or []
            if rds:
                fp = rds[0].get("filepath") or rds[0].get("_filename")
        if fp and os.path.exists(fp):
            files.append(fp)
    if not files:
        for name in sorted(os.listdir(out_dir)):
            p = os.path.join(out_dir, name)
            if os.path.isfile(p):
                files.append(p)
    return files


async def process_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    allowed, source = await try_consume_quota(user_id)
    if not allowed:
        link = referral_link(user_id)
        await update.message.reply_text(
            f"🚫 *Limit reached!*\n\n"
            f"Aapke 24-hour mein {DAILY_FREE_LIMIT} free downloads khatam ho gaye "
            f"aur koi credits nahi hain.\n\n"
            f"🎁 Friends invite karen — har join pe *+{REFERRAL_BONUS} credits*:\n"
            f"`{link}`\n\n"
            f"_Ya 24h baad dobara try karen._",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        return

    status_msg = await update.message.reply_text("⏳ Download ho raha hai…")
    tmp_dir = tempfile.mkdtemp(prefix="igdl_", dir="/tmp")

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
        try:
            files = await asyncio.to_thread(_download_with_ytdlp, url, tmp_dir)
        except Exception as e:  # noqa: BLE001
            logger.exception("yt-dlp error: %s", e)
            await refund_and_count_fail(user_id, source)
            err = str(e).lower()
            if "login" in err or "private" in err or "rate-limit" in err or "rate limit" in err or "429" in err:
                if user_id == ADMIN_ID and not _cookies_present():
                    msg = (
                        "🔒 *Instagram ne is server ka IP block kar diya hai* (rate-limit).\n\n"
                        "Public endpoint bina login ke ab kaam nahi karta.\n"
                        "Solution: /setcookie command se apne Instagram cookies ek baar set karen.\n"
                        "Phir bot reliably kaam karega."
                    )
                elif _cookies_present():
                    msg = (
                        "🔒 Cookies set hain par fir bhi block ho raha hai.\n"
                        "Cookies expire ho gayi ho sakti hain — /setcookie se fresh cookies do."
                    )
                else:
                    msg = (
                        "🔒 Abhi server par rate-limit hit ho gaya. "
                        "Admin ko notify kar diya gaya hai, thodi der baad try karen."
                    )
            elif "not available" in err or "404" in err:
                msg = "⚠️ Content unavailable ya delete ho gaya hai."
            else:
                msg = "⚠️ Download fail ho gaya. Link check karen ya thodi der baad try karen."
            await status_msg.edit_text(msg, parse_mode=ParseMode.MARKDOWN)
            return

        if not files:
            await refund_and_count_fail(user_id, source)
            await status_msg.edit_text("⚠️ Koi media nahi mila is link mein.")
            return

        sendable: list[str] = []
        for f in files:
            try:
                size = os.path.getsize(f)
            except OSError:
                continue
            if size > MAX_TG_FILE_BYTES:
                await update.message.reply_text(
                    f"⚠️ `{os.path.basename(f)}` 50MB se badi hai (Telegram limit), skip.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                continue
            sendable.append(f)

        if not sendable:
            await refund_and_count_fail(user_id, source)
            await status_msg.edit_text("⚠️ Sabhi files 50MB se badi hain (Telegram bot limit).")
            return

        await status_msg.edit_text("📤 Upload ho raha hai…")

        media_items: list[tuple[str, str]] = []
        for f in sendable:
            ext = os.path.splitext(f)[1].lower()
            if ext in {".mp4", ".mov", ".mkv", ".webm"}:
                media_items.append(("video", f))
            elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
                media_items.append(("photo", f))
            else:
                media_items.append(("doc", f))

        used, free_left, credits = await get_quota(user_id)
        footer_caption = (
            "✅ Ye lijiye!\n"
            f"📊 Today: {used}/{DAILY_FREE_LIMIT} | 💰 Credits: {credits}"
        )

        i = 0
        while i < len(media_items):
            chunk = media_items[i : i + 10]
            i += 10
            if len(chunk) == 1:
                kind, path = chunk[0]
                with open(path, "rb") as fh:
                    if kind == "video":
                        await context.bot.send_video(
                            chat_id=chat_id, video=fh, caption=footer_caption,
                            supports_streaming=True, read_timeout=180, write_timeout=180,
                        )
                    elif kind == "photo":
                        await context.bot.send_photo(
                            chat_id=chat_id, photo=fh, caption=footer_caption,
                            read_timeout=120, write_timeout=120,
                        )
                    else:
                        await context.bot.send_document(
                            chat_id=chat_id, document=fh, caption=footer_caption,
                            read_timeout=180, write_timeout=180,
                        )
            else:
                media_group = []
                opened = []
                try:
                    for idx, (kind, path) in enumerate(chunk):
                        fh = open(path, "rb")
                        opened.append(fh)
                        cap = footer_caption if idx == 0 else None
                        if kind == "video":
                            media_group.append(InputMediaVideo(media=fh, caption=cap))
                        elif kind == "photo":
                            media_group.append(InputMediaPhoto(media=fh, caption=cap))
                        else:
                            await context.bot.send_document(chat_id=chat_id, document=fh)
                    if media_group:
                        await context.bot.send_media_group(
                            chat_id=chat_id, media=media_group,
                            read_timeout=240, write_timeout=240,
                        )
                finally:
                    for fh in opened:
                        try:
                            fh.close()
                        except Exception:
                            pass

        await users_col.update_one({"user_id": user_id}, {"$inc": {"downloads_success": 1}})

        try:
            await status_msg.delete()
        except Exception:
            pass

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def refund_and_count_fail(user_id: int, source: str) -> None:
    """Refund quota on failed download and bump fail counter."""
    update_doc: dict = {"$inc": {"downloads_fail": 1}}
    if source == "credit":
        update_doc["$inc"]["credits"] = 1
    await users_col.update_one({"user_id": user_id}, update_doc)
    if source == "free":
        # remove most recent timestamp from recent_downloads
        user = await users_col.find_one({"user_id": user_id}, {"_id": 0, "recent_downloads": 1})
        if user and user.get("recent_downloads"):
            recent = user["recent_downloads"][:-1]
            await users_col.update_one({"user_id": user_id}, {"$set": {"recent_downloads": recent}})


# ---------------------------------------------------------------------------
# Master message handler
# ---------------------------------------------------------------------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    user = update.effective_user
    await ensure_user(user, bot=context.bot)

    # Admin broadcast capture
    if user.id == ADMIN_ID and context.user_data.get("awaiting_broadcast"):
        await do_broadcast(update, context)
        return

    # Admin cookie capture
    if user.id == ADMIN_ID and context.user_data.get("awaiting_cookie"):
        context.user_data["awaiting_cookie"] = False
        raw = (update.message.text or "").strip()
        # Delete the cookie message for security
        try:
            await update.message.delete()
        except Exception:
            pass
        n = _write_cookies(raw)
        if n == 0:
            await context.bot.send_message(
                user.id,
                "❌ Cookies parse nahi ho paayin. `key=value; key=value` format mein bhejen.",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await context.bot.send_message(
                user.id,
                f"✅ {n} cookies save ho gayin! Ab downloads chalu honi chahiye.\n\n"
                f"(Original message security ke liye delete kar diya.)",
            )
        return

    text = update.message.text or update.message.caption or ""
    match = INSTAGRAM_URL_REGEX.search(text)
    if not match:
        if update.message.text:
            await update.message.reply_text(
                "❌ Ye valid Instagram link nahi lagta.\n\n"
                "Kripya aisa link bhejen:\n"
                "`https://www.instagram.com/reel/...`\n"
                "`https://www.instagram.com/p/...`",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    await process_download(update, context, match.group(0))


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
async def _post_init(app: Application) -> None:
    global BOT_USERNAME
    me = await app.bot.get_me()
    BOT_USERNAME = me.username
    logger.info("Bot online as @%s (admin=%s)", BOT_USERNAME, ADMIN_ID)

    # Indexes
    try:
        await users_col.create_index("user_id", unique=True)
        await users_col.create_index("referred_by")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Index creation skipped: %s", exc)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("me", me_command))
    app.add_handler(CommandHandler("referral", referral_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("setcookie", setcookie_command))
    app.add_handler(CommandHandler("cookiestatus", cookiestatus_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin:"))
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.AUDIO | filters.VOICE
             | filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND,
            message_handler,
        )
    )
    app.add_error_handler(error_handler)

    logger.info("Starting Instagram downloader bot in polling mode…")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
