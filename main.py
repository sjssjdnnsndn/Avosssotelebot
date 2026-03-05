"""
╔══════════════════════════════════════════╗
║       Telegram ID Finder Bot             ║
║       Replit Ready Version               ║
╚══════════════════════════════════════════╝

Install karo:
    pip uninstall telegram -y
    pip install python-telegram-bot==20.7

Phir run karo:
    python main.py
"""

import asyncio
import logging
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButtonRequestUsers,
    KeyboardButtonRequestChat,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ╔══════════════════════════════════════╗
# ║   🔑  APNA TOKEN YAHAN DAALO        ║
BOT_TOKEN    = "8541365111:AAFQEAi2lZMydLTYn4yvaPL0IK5ep5GBLjo"
BOT_USERNAME = "@Usernameid_finder_bot"
NEWS_CHANNEL = "@DmTechss"
# ╚══════════════════════════════════════╝

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─── ID ke saath Copy + Share buttons ────────────────────────────────────────
def id_inline_buttons(id_value: str) -> InlineKeyboardMarkup:
    share_text = f"My Telegram ID: {id_value}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📋 Copy {id_value}", callback_data=f"copy:{id_value}")],
        [InlineKeyboardButton("🚀 Share ID", switch_inline_query=share_text)],
    ])


# ─── Main menu keyboard (screenshot jaisi) ───────────────────────────────────
def main_menu() -> ReplyKeyboardMarkup:
    rows = [
        [
            KeyboardButton("👤 User"),
            KeyboardButton("⭐ Premium"),
            KeyboardButton("👾 Bot"),
        ],
        [
            KeyboardButton("👥 Group"),
            KeyboardButton("📢 Channel"),
            KeyboardButton("💬 Forum"),
        ],
        [
            KeyboardButton("👥 My Group"),
            KeyboardButton("📢 My Channel"),
            KeyboardButton("💬 My Forum"),
        ],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# ─── /start ───────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = str(user.id)

    welcome = (
        f"👋 Hi Welcome To {BOT_USERNAME} 🖐\n\n"
        f"📚 Help : /help\n\n"
        f"🔔 Bot News : {NEWS_CHANNEL}"
    )
    await update.message.reply_text(welcome, reply_markup=main_menu())

    # Apni ID dikhao
    await update.message.reply_text(
        f"Your ID : {uid}",
        reply_markup=id_inline_buttons(uid),
    )


# ─── /help ────────────────────────────────────────────────────────────────────
async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Bot Help*\n\n"
        "👤 *User* — Kisi user ka ID pata karo\n"
        "⭐ *Premium* — Premium user ka ID\n"
        "👾 *Bot* — Kisi bot ka ID\n"
        "👥 *Group* — Group ka Chat ID\n"
        "📢 *Channel* — Channel ka Chat ID\n"
        "💬 *Forum* — Forum ka Chat ID\n"
        "👥 *My Group* — Apne group ka ID\n"
        "📢 *My Channel* — Apne channel ka ID\n"
        "💬 *My Forum* — Apne forum ka ID\n\n"
        "➡️ Menu button dabao, share karo, ID lo! 🚀\n\n"
        "📨 *Forwarded msg* — Koi bhi message forward karo → sender ka ID"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── /myid ────────────────────────────────────────────────────────────────────
async def myid_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    await update.message.reply_text(
        f"Your ID : {uid}",
        reply_markup=id_inline_buttons(uid),
    )


# ─── Menu buttons handler ─────────────────────────────────────────────────────
async def menu_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # ── User / Premium / Bot share request ──
    if text in ("👤 User", "⭐ Premium", "👾 Bot"):
        request_users = KeyboardButtonRequestUsers(
            request_id=1,
            user_is_bot=(text == "👾 Bot"),
            user_is_premium=(text == "⭐ Premium"),
        )
        btn = KeyboardButton("📤 User Share Karo", request_users=request_users)
        kb  = ReplyKeyboardMarkup(
            [[btn], [KeyboardButton("🔙 Back")]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text(
            f"{text} share karo 👇",
            reply_markup=kb,
        )

    # ── Group / Channel / Forum share request ──
    elif text in ("👥 Group", "📢 Channel", "💬 Forum",
                  "👥 My Group", "📢 My Channel", "💬 My Forum"):

        is_channel   = "Channel" in text
        is_forum     = "Forum" in text
        creator_only = text.startswith("👥 My") or text.startswith("📢 My") or text.startswith("💬 My")

        request_chat = KeyboardButtonRequestChat(
            request_id=2,
            chat_is_channel=is_channel,
            chat_is_forum=is_forum,
            chat_is_created=creator_only,   # sirf apne wale
        )
        btn = KeyboardButton("📤 Chat Share Karo", request_chat=request_chat)
        kb  = ReplyKeyboardMarkup(
            [[btn], [KeyboardButton("🔙 Back")]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text(
            f"{text} share karo 👇",
            reply_markup=kb,
        )


# ─── Back button ──────────────────────────────────────────────────────────────
async def back_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Main menu 👇", reply_markup=main_menu())


# ─── User shared → ID nikalo ──────────────────────────────────────────────────
async def user_shared(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users_shared = update.message.users_shared
    if users_shared and users_shared.users:
        for u in users_shared.users:
            uid = str(u.user_id)
            await update.message.reply_text(
                f"User ID : {uid}",
                reply_markup=id_inline_buttons(uid),
            )
    await update.message.reply_text("Main menu 👇", reply_markup=main_menu())


# ─── Chat shared → ID nikalo ─────────────────────────────────────────────────
async def chat_shared(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_shared = update.message.chat_shared
    if chat_shared:
        cid = str(chat_shared.chat_id)
        await update.message.reply_text(
            f"Chat ID : {cid}",
            reply_markup=id_inline_buttons(cid),
        )
    await update.message.reply_text("Main menu 👇", reply_markup=main_menu())


# ─── Forwarded message → sender ka ID ────────────────────────────────────────
async def forwarded(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg    = update.message
    origin = getattr(msg, "forward_origin", None)
    uid    = None

    if origin:
        sender = getattr(origin, "sender_user", None)
        chat   = getattr(origin, "chat", None)
        if sender:
            uid = str(sender.id)
        elif chat:
            uid = str(chat.id)

    if uid:
        await msg.reply_text(
            f"User/Chat ID : {uid}",
            reply_markup=id_inline_buttons(uid),
        )
    else:
        await msg.reply_text(
            "❌ ID nahi mili.\n"
            "User ne apni privacy settings mein forward hide ki hain."
        )


# ─── Copy callback (popup show karo) ─────────────────────────────────────────
async def copy_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    value = query.data.split(":", 1)[1]
    await query.answer(f"✅ ID: {value}", show_alert=True)


# ─── App build karo ───────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_cmd))
    app.add_handler(CommandHandler("myid",  myid_cmd))

    # Menu text buttons
    menu_text_filter = filters.TEXT & filters.Regex(
        r"^(👤 User|⭐ Premium|👾 Bot|👥 Group|📢 Channel|💬 Forum"
        r"|👥 My Group|📢 My Channel|💬 My Forum)$"
    )
    app.add_handler(MessageHandler(menu_text_filter, menu_button))

    # Back button
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^🔙 Back$"), back_button
    ))

    # Shared user/chat updates
    app.add_handler(MessageHandler(filters.StatusUpdate.USERS_SHARED, user_shared))
    app.add_handler(MessageHandler(filters.StatusUpdate.CHAT_SHARED,  chat_shared))

    # Forwarded messages
    app.add_handler(MessageHandler(filters.FORWARDED, forwarded))

    # Inline copy callback
    app.add_handler(CallbackQueryHandler(copy_callback, pattern=r"^copy:"))

    print("✅ Bot chal raha hai... (Ctrl+C se band karo)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
