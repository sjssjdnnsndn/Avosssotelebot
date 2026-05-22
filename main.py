"""
╔══════════════════════════════════════════════════╗
║       TELEGRAM ACCOUNT MANAGER BOT               ║
║  Phone → OTP → 2FA → Name → Session → Done ✅   ║
╚══════════════════════════════════════════════════╝
"""

import os, json, logging, asyncio, signal, atexit
from datetime import datetime
from dotenv import load_dotenv

from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeInvalidError, PhoneCodeExpiredError,
    SessionPasswordNeededError, PasswordHashInvalidError, FloodWaitError,
)
from telethon.tl.functions.auth import ResendCodeRequest
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.types.auth import (
    SentCodeTypeSms, SentCodeTypeApp, SentCodeTypeCall,
    SentCodeTypeFlashCall, SentCodeTypeMissedCall,
    SentCodeTypeEmailCode, SentCodeTypeFragmentSms,
    SentCodeTypeSmsWord, SentCodeTypeFirebaseSms,
    CodeTypeSms, CodeTypeCall, CodeTypeFlashCall,
    CodeTypeFragmentSms, CodeTypeMissedCall,
)

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

load_dotenv()
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Env ───────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
API_ID       = int(os.getenv("API_ID", "0"))
API_HASH     = os.getenv("API_HASH", "")
ACCOUNTS_DB  = "accounts.json"
SESSIONS_DIR = "sessions"

# ── Allowed users — read from ALLOWED_USER_IDS (10-digit chunks) ──────────────
# Format: concatenate all 10-digit Telegram IDs back to back
# Example: "12345678900987654321" → [1234567890, 987654321]  (two users)
# Fallback: legacy single ALLOWED_USER_ID is also supported
def _parse_allowed_ids() -> set[int]:
    ids: set[int] = set()
    raw = os.getenv("ALLOWED_USER_IDS", "").strip()
    if raw:
        for i in range(0, len(raw), 10):
            chunk = raw[i:i+10].strip()
            if chunk.isdigit():
                ids.add(int(chunk))
    # legacy fallback
    legacy = os.getenv("ALLOWED_USER_ID", "0").strip()
    if legacy.isdigit() and int(legacy) != 0:
        ids.add(int(legacy))
    return ids

ALLOWED_IDS: set[int] = _parse_allowed_ids()
logger.info(f"Allowed user IDs: {ALLOWED_IDS if ALLOWED_IDS else 'ALL (open access)'}")

os.makedirs(SESSIONS_DIR, exist_ok=True)

# ── States ────────────────────────────────────────────────────────────────────
(ASK_PHONE, ASK_OTP, ASK_EXIST_2FA,
 ASK_NEW_2FA, ASK_HINT, ASK_FIRSTNAME, ASK_LASTNAME) = range(7)

# ── Active Telethon clients per chat ──────────────────────────────────────────
_clients: dict[int, TelegramClient] = {}


# ══════════════════════════════════════════════════════════════════════════════
#  CODE-TYPE HELPER
# ══════════════════════════════════════════════════════════════════════════════
# SentCodeType* — current delivery type (returned in sent.type)
_SENT_CODE_TYPE_MAP = {
    SentCodeTypeSms:         ("SMS",          "📱"),
    SentCodeTypeApp:         ("Telegram App", "📲"),
    SentCodeTypeCall:        ("Phone Call",   "📞"),
    SentCodeTypeFlashCall:   ("Flash Call",   "⚡"),
    SentCodeTypeMissedCall:  ("Missed Call",  "📳"),
    SentCodeTypeEmailCode:   ("Email",        "📧"),
    SentCodeTypeFragmentSms: ("Fragment SMS", "🔗"),
    SentCodeTypeSmsWord:     ("SMS Word",     "🔤"),
    SentCodeTypeFirebaseSms: ("Firebase SMS", "🔥"),
}

# CodeType* — next available delivery type (returned in sent.next_type)
_NEXT_CODE_TYPE_MAP = {
    CodeTypeSms:         ("SMS",          "📱"),
    CodeTypeCall:        ("Phone Call",   "📞"),
    CodeTypeFlashCall:   ("Flash Call",   "⚡"),
    CodeTypeFragmentSms: ("Fragment SMS", "🔗"),
    CodeTypeMissedCall:  ("Missed Call",  "📳"),
}

def _code_type_info(t) -> tuple[str, str]:
    """Return (label, icon) for any SentCodeType* or CodeType* object."""
    for cls, info in {**_SENT_CODE_TYPE_MAP, **_NEXT_CODE_TYPE_MAP}.items():
        if isinstance(t, cls):
            return info
    name = t.__class__.__name__
    for prefix in ("SentCodeType", "CodeType"):
        name = name.replace(prefix, "")
    return (name, "❓")

def _otp_keyboard(sent) -> InlineKeyboardMarkup:
    """Build inline keyboard with resend/switch buttons."""
    cur_type  = getattr(sent, "type", None)
    next_type = getattr(sent, "next_type", None)
    cur_label, cur_icon = _code_type_info(cur_type) if cur_type else ("Code", "🔑")

    rows = []

    # Button 1 — switch_code: cycles to next available delivery method
    if next_type:
        nl, ni = _code_type_info(next_type)
        rows.append([InlineKeyboardButton(
            f"{ni}  Switch to {nl}",
            callback_data="switch_code"
        )])
    else:
        # Fallback switch button based on current type
        if isinstance(cur_type, (SentCodeTypeSms, SentCodeTypeFragmentSms,
                                  SentCodeTypeFirebaseSms, SentCodeTypeSmsWord)):
            rows.append([InlineKeyboardButton(
                "📞  Request via Phone Call",
                callback_data="switch_code"
            )])
        elif isinstance(cur_type, SentCodeTypeApp):
            rows.append([InlineKeyboardButton(
                "📱  Switch to SMS",
                callback_data="switch_code"
            )])
        elif isinstance(cur_type, (SentCodeTypeCall, SentCodeTypeFlashCall,
                                    SentCodeTypeMissedCall)):
            rows.append([InlineKeyboardButton(
                "📱  Switch to SMS",
                callback_data="switch_code"
            )])

    # Button 2 — resend_same: fresh request via same current method
    rows.append([InlineKeyboardButton(
        f"🔄  Resend via {cur_icon} {cur_label}",
        callback_data="resend_same"
    )])

    return InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS DATABASE  (simple JSON)
# ══════════════════════════════════════════════════════════════════════════════
def load_accounts() -> list[dict]:
    if not os.path.exists(ACCOUNTS_DB):
        return []
    with open(ACCOUNTS_DB, "r") as f:
        return json.load(f)

def save_account(record: dict):
    db = load_accounts()
    db.append(record)
    with open(ACCOUNTS_DB, "w") as f:
        json.dump(db, f, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════
def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕  Setup New Account", callback_data="new_account")],
        [InlineKeyboardButton("📋  My Accounts",       callback_data="my_accounts")],
    ])

def skip_kb(cb: str):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏭  Skip", callback_data=cb)]])

def accounts_kb(accounts: list[dict]):
    rows = []
    for i, acc in enumerate(accounts):
        label = f"👤  {acc['name']}  |  {acc['phone']}  |  {acc['created_at'][:10]}"
        rows.append([InlineKeyboardButton(label, callback_data=f"get_session:{i}")])
    rows.append([InlineKeyboardButton("🏠  Back to Menu", callback_data="back_home")])
    return InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════════════
#  WELCOME MESSAGE
# ══════════════════════════════════════════════════════════════════════════════
WELCOME = (
    "┌─────────────────────────────────────┐\n"
    "│   <b>⚡ TELEGRAM ACCOUNT MANAGER ⚡</b>   │\n"
    "└─────────────────────────────────────┘\n\n"
    "Welcome! This bot helps you set up Telegram accounts\n"
    "completely from within Telegram — no terminal needed.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🟢  <b>What this bot can do:</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "  📱  Send OTP to any phone number\n"
    "  🔑  Verify OTP and sign in\n"
    "  🔐  Set Two-Step Verification password\n"
    "  👤  Set a custom profile name\n"
    "  💾  Save session for passwordless login\n"
    "  📋  View all created accounts\n"
    "  📥  Download session files anytime\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "👇  <b>Choose an option to get started:</b>"
)


# ══════════════════════════════════════════════════════════════════════════════
#  GUARDS
# ══════════════════════════════════════════════════════════════════════════════
def allowed(update: Update) -> bool:
    if not ALLOWED_IDS:
        return True
    uid = update.effective_user.id if update.effective_user else 0
    return uid in ALLOWED_IDS

async def deny(update: Update):
    target = update.message or (update.callback_query and update.callback_query.message)
    if target:
        await target.reply_text("⛔ <b>Unauthorized.</b> This bot is private.", parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════════
#  /start  ──  Main Menu
# ══════════════════════════════════════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not allowed(update):
        await deny(update); return ConversationHandler.END

    await update.message.reply_text(WELCOME, parse_mode="HTML", reply_markup=main_menu_kb())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK — "Setup New Account" button
# ══════════════════════════════════════════════════════════════════════════════
async def cb_new_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if not allowed(update):
        await deny(update); return ConversationHandler.END

    await q.edit_message_text(
        "┌──────────────────────────────────┐\n"
        "│  <b>➕ NEW ACCOUNT SETUP</b>            │\n"
        "└──────────────────────────────────┘\n\n"
        "I'll guide you through every step.\n"
        "Type /cancel anytime to stop.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📱  <b>STEP 1 of 5 — Phone Number</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send the phone number with country code:\n"
        "<code>Example: +919876543210</code>",
        parse_mode="HTML",
    )
    return ASK_PHONE


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK — "My Accounts" button
# ══════════════════════════════════════════════════════════════════════════════
async def cb_my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not allowed(update):
        await deny(update); return

    accounts = load_accounts()
    if not accounts:
        await q.edit_message_text(
            "📋  <b>My Accounts</b>\n\n"
            "No accounts created yet.\n\n"
            "Tap <b>Setup New Account</b> to get started!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠  Back to Menu", callback_data="back_home")]
            ]),
        )
        return

    await q.edit_message_text(
        f"📋  <b>My Accounts</b>  ({len(accounts)} total)\n\n"
        "Tap any account to download its session file\n"
        "<i>(session file lets you log in without entering credentials again)</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode="HTML",
        reply_markup=accounts_kb(accounts),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK — Download session file for a specific account
# ══════════════════════════════════════════════════════════════════════════════
async def cb_get_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not allowed(update):
        await deny(update); return

    idx = int(q.data.split(":")[1])
    accounts = load_accounts()

    if idx >= len(accounts):
        await q.answer("Account not found.", show_alert=True); return

    acc = accounts[idx]
    session_path = acc.get("session_file", "")

    if not session_path or not os.path.exists(session_path):
        await q.answer("⚠️ Session file not found on disk.", show_alert=True); return

    # Send account info + session file
    info_text = (
        "┌──────────────────────────────────┐\n"
        "│  <b>💾 SESSION FILE</b>                  │\n"
        "└──────────────────────────────────┘\n\n"
        f"👤  <b>Name:</b>      <code>{acc['name']}</code>\n"
        f"📱  <b>Phone:</b>     <code>{acc['phone']}</code>\n"
        f"🆔  <b>User ID:</b>   <code>{acc['user_id']}</code>\n"
        f"📅  <b>Created:</b>   {acc['created_at'][:19]}\n"
        f"🔐  <b>2FA Set:</b>   {'✅ Yes' if acc.get('2fa_set') else '❌ No'}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📥  <b>Session file attached below.</b>\n"
        "<i>Use this with Telethon to log in without credentials.</i>"
    )

    await context.bot.send_message(
        chat_id=q.message.chat_id,
        text=info_text,
        parse_mode="HTML",
    )
    with open(session_path, "rb") as f:
        await context.bot.send_document(
            chat_id=q.message.chat_id,
            document=InputFile(f, filename=os.path.basename(session_path)),
            caption=f"📎 Session: <code>{acc['phone']}</code>",
            parse_mode="HTML",
        )


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK — Back to home menu
# ══════════════════════════════════════════════════════════════════════════════
async def cb_back_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text(WELCOME, parse_mode="HTML", reply_markup=main_menu_kb())


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 1 — Receive Phone, Send OTP
# ══════════════════════════════════════════════════════════════════════════════
async def recv_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not allowed(update):
        await deny(update); return ConversationHandler.END

    phone = update.message.text.strip()
    chat_id = update.effective_chat.id

    if not phone.startswith("+"):
        await update.message.reply_text(
            "⚠️  Phone number must start with <b>+</b> (country code)\n\n"
            "<code>Example: +919876543210</code>",
            parse_mode="HTML",
        )
        return ASK_PHONE

    await update.message.reply_text("⏳  Requesting OTP from Telegram…")

    session_file = os.path.join(SESSIONS_DIR, f"session_{phone.replace('+','').replace(' ','')}")
    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()
    _clients[chat_id] = client
    context.user_data.update({"phone": phone, "session_file": session_file + ".session"})

    try:
        sent = await client.send_code_request(phone)
        context.user_data["phone_code_hash"] = sent.phone_code_hash

        cur_label, cur_icon = _code_type_info(sent.type)
        logger.info(f"Initial code sent to {phone} via {cur_label}")

        # ── Auto-force SMS if code went to Telegram App ───────────────────────
        # Telegram sends to App first when another device is logged in.
        # We immediately ResendCodeRequest to cycle to SMS automatically.
        sms_forced = False
        if isinstance(sent.type, SentCodeTypeApp):
            try:
                resent = await client(ResendCodeRequest(
                    phone=phone,
                    phone_code_hash=sent.phone_code_hash
                ))
                context.user_data["phone_code_hash"] = resent.phone_code_hash
                sent = resent          # use updated sent object for display
                cur_label, cur_icon = _code_type_info(sent.type)
                sms_forced = True
                logger.info(f"Auto-switched to {cur_label} for {phone}")
            except Exception as force_err:
                logger.warning(f"Auto-SMS switch failed: {force_err}")
        # ─────────────────────────────────────────────────────────────────────

        forced_note = "\n⚡  <i>Auto-switched from Telegram App to SMS</i>" if sms_forced else ""

        await update.message.reply_text(
            "✅  <b>OTP Sent!</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔑  <b>STEP 2 of 5 — OTP Verification</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📬  <b>Delivery method:</b>  {cur_icon}  <b>{cur_label}</b>"
            f"{forced_note}\n\n"
            "Enter the OTP code below, or switch delivery method:",
            parse_mode="HTML",
            reply_markup=_otp_keyboard(sent),
        )
        return ASK_OTP

    except FloodWaitError as e:
        await client.disconnect()
        await update.message.reply_text(
            f"⏳  <b>Flood Wait!</b>\n\nTelegram has temporarily blocked requests.\n"
            f"Please wait <b>{e.seconds} seconds</b> and try again with /start.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    except Exception as e:
        await client.disconnect()
        logger.error(f"send_code_request: {e}")
        await update.message.reply_text(
            f"❌  Failed to send OTP:\n<code>{e}</code>\n\nType /start to retry.",
            parse_mode="HTML",
        )
        return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2 — Receive OTP, Sign In
# ══════════════════════════════════════════════════════════════════════════════
async def recv_otp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not allowed(update):
        await deny(update); return ConversationHandler.END

    otp = update.message.text.strip().replace(" ", "")
    chat_id = update.effective_chat.id
    phone = context.user_data.get("phone")
    client: TelegramClient = _clients.get(chat_id)

    if not client:
        await update.message.reply_text("❌ Session expired. Type /start to restart.")
        return ConversationHandler.END

    try:
        await client.sign_in(phone, otp)
        me = await client.get_me()
        context.user_data["user_id"] = me.id

        await update.message.reply_text(
            f"✅  <b>Phone Verified!</b>\n\n"
            f"Signed in as: <code>{me.first_name or 'N/A'}</code>  (ID: <code>{me.id}</code>)\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔐  <b>STEP 3 of 5 — Two-Step Verification</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Set a new 2FA password for this account.\n"
            "Or tap <b>Skip</b> to leave 2FA disabled.",
            parse_mode="HTML",
            reply_markup=skip_kb("skip_2fa"),
        )
        return ASK_NEW_2FA

    except PhoneCodeInvalidError:
        await update.message.reply_text("❌  <b>Wrong OTP!</b> Please try again:", parse_mode="HTML")
        return ASK_OTP

    except PhoneCodeExpiredError:
        await client.disconnect()
        await update.message.reply_text(
            "⏳  <b>OTP Expired.</b>\n\nType /start to request a new one.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    except SessionPasswordNeededError:
        await update.message.reply_text(
            "⚠️  <b>This account already has 2FA enabled.</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔑  Enter the <b>existing 2FA password</b> to continue:",
            parse_mode="HTML",
        )
        return ASK_EXIST_2FA

    except Exception as e:
        logger.error(f"sign_in: {e}")
        await update.message.reply_text(
            f"❌  Sign-in error:\n<code>{e}</code>\n\nType /start to retry.",
            parse_mode="HTML",
        )
        return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2a — Switch to next delivery method  (switch_code button)
# ══════════════════════════════════════════════════════════════════════════════
async def cb_switch_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer("🔄 Switching delivery method…")
    if not allowed(update):
        await deny(update); return ConversationHandler.END

    chat_id  = update.effective_chat.id
    phone    = context.user_data.get("phone")
    old_hash = context.user_data.get("phone_code_hash")
    client: TelegramClient = _clients.get(chat_id)

    if not client or not phone or not old_hash:
        await q.edit_message_text("❌ Session expired. Type /start to restart.")
        return ConversationHandler.END

    try:
        resent = await client(ResendCodeRequest(phone=phone, phone_code_hash=old_hash))
        context.user_data["phone_code_hash"] = resent.phone_code_hash

        cur_label, cur_icon = _code_type_info(resent.type)
        logger.info(f"Switched code for {phone} → {cur_label}")

        await q.edit_message_text(
            "✅  <b>Delivery Method Switched!</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔑  <b>STEP 2 of 5 — OTP Verification</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📬  <b>Delivery method:</b>  {cur_icon}  <b>{cur_label}</b>\n\n"
            "Enter the OTP code below, or switch delivery method:",
            parse_mode="HTML",
            reply_markup=_otp_keyboard(resent),
        )
        return ASK_OTP

    except Exception as e:
        logger.error(f"switch_code: {e}")
        await q.answer(f"❌ Failed to switch: {e}", show_alert=True)
        return ASK_OTP


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2b — Resend via same current method  (resend_same button)
# ══════════════════════════════════════════════════════════════════════════════
async def cb_resend_same(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer("📨 Resending code…")
    if not allowed(update):
        await deny(update); return ConversationHandler.END

    chat_id = update.effective_chat.id
    phone   = context.user_data.get("phone")
    client: TelegramClient = _clients.get(chat_id)

    if not client or not phone:
        await q.edit_message_text("❌ Session expired. Type /start to restart.")
        return ConversationHandler.END

    try:
        # Fresh send_code_request → same method Telegram chooses (usually same as before)
        sent = await client.send_code_request(phone)
        context.user_data["phone_code_hash"] = sent.phone_code_hash

        cur_label, cur_icon = _code_type_info(sent.type)
        logger.info(f"Code resent (same) for {phone} via {cur_label}")

        await q.edit_message_text(
            "🔄  <b>Code Resent!</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔑  <b>STEP 2 of 5 — OTP Verification</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📬  <b>Delivery method:</b>  {cur_icon}  <b>{cur_label}</b>\n\n"
            "Enter the OTP code below, or switch delivery method:",
            parse_mode="HTML",
            reply_markup=_otp_keyboard(sent),
        )
        return ASK_OTP

    except FloodWaitError as e:
        await q.answer(f"⏳ Flood Wait! Please wait {e.seconds}s", show_alert=True)
        return ASK_OTP
    except Exception as e:
        logger.error(f"resend_same: {e}")
        await q.answer(f"❌ Failed to resend: {e}", show_alert=True)
        return ASK_OTP


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2b — Existing 2FA password
# ══════════════════════════════════════════════════════════════════════════════
async def recv_exist_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not allowed(update):
        await deny(update); return ConversationHandler.END

    pw = update.message.text.strip()
    chat_id = update.effective_chat.id
    client: TelegramClient = _clients.get(chat_id)

    try:
        await client.sign_in(password=pw)
        me = await client.get_me()
        context.user_data["user_id"] = me.id

        await update.message.reply_text(
            f"✅  <b>2FA Verified!</b>  Signed in as <code>{me.first_name}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔐  <b>STEP 3 of 5 — Change 2FA Password</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Enter a new 2FA password, or tap <b>Skip</b> to keep the current one.",
            parse_mode="HTML",
            reply_markup=skip_kb("skip_2fa"),
        )
        return ASK_NEW_2FA

    except PasswordHashInvalidError:
        await update.message.reply_text("❌  <b>Wrong password!</b> Try again:", parse_mode="HTML")
        return ASK_EXIST_2FA

    except Exception as e:
        logger.error(f"existing 2fa: {e}")
        await update.message.reply_text(
            f"❌  Error: <code>{e}</code>\n\nType /start to retry.", parse_mode="HTML"
        )
        return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 3 — New 2FA Password
# ══════════════════════════════════════════════════════════════════════════════
async def recv_new_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not allowed(update):
        await deny(update); return ConversationHandler.END

    text = update.message.text.strip()
    context.user_data["new_2fa"] = text
    await update.message.reply_text(
        "✅  Password saved.\n\n"
        "💬  Enter a <b>hint</b> for your password,\nor tap <b>Skip</b> for no hint.",
        parse_mode="HTML",
        reply_markup=skip_kb("skip_hint"),
    )
    return ASK_HINT

async def skip_2fa_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    context.user_data["new_2fa"] = None
    await q.edit_message_text(
        "⏭  2FA skipped.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👤  <b>STEP 4 of 5 — Profile Name</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Enter the <b>First Name</b> for this account:",
        parse_mode="HTML",
    )
    return ASK_FIRSTNAME


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 3b — 2FA Hint, then apply
# ══════════════════════════════════════════════════════════════════════════════
async def recv_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not allowed(update):
        await deny(update); return ConversationHandler.END

    hint = update.message.text.strip()
    return await _apply_2fa_and_next(update, context, hint)

async def skip_hint_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    return await _apply_2fa_and_next(update, context, "", via_cb=True)

async def _apply_2fa_and_next(update, context, hint: str, via_cb=False) -> int:
    chat_id = update.effective_chat.id
    client: TelegramClient = _clients.get(chat_id)
    new_pw = context.user_data.get("new_2fa")

    msg_fn = (update.callback_query.edit_message_text
              if via_cb else update.message.reply_text)

    if new_pw:
        try:
            await client.edit_2fa(new_password=new_pw, hint=hint)
            context.user_data["2fa_set"] = True
            status = f"✅  <b>2FA Set!</b>  Hint: <code>{hint or 'none'}</code>\n\n"
        except Exception as e:
            logger.error(f"edit_2fa: {e}")
            context.user_data["2fa_set"] = False
            status = f"⚠️  2FA could not be set: <code>{e}</code>\n\n"
    else:
        context.user_data["2fa_set"] = False
        status = ""

    await msg_fn(
        status +
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👤  <b>STEP 4 of 5 — Profile Name</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Enter the <b>First Name</b> for this account:",
        parse_mode="HTML",
    )
    return ASK_FIRSTNAME


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 4 — First Name
# ══════════════════════════════════════════════════════════════════════════════
async def recv_firstname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not allowed(update):
        await deny(update); return ConversationHandler.END

    fn = update.message.text.strip()
    if not fn:
        await update.message.reply_text("❌  Name cannot be empty. Please enter a first name:")
        return ASK_FIRSTNAME

    context.user_data["first_name"] = fn
    await update.message.reply_text(
        "✅  First name saved.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👤  <b>STEP 5 of 5 — Last Name</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Enter the <b>Last Name</b>, or tap <b>Skip</b>:",
        parse_mode="HTML",
        reply_markup=skip_kb("skip_lastname"),
    )
    return ASK_LASTNAME

async def skip_lastname_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    context.user_data["last_name"] = ""
    return await _finalize(update, context, via_cb=True)


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 5 — Last Name + Finalize
# ══════════════════════════════════════════════════════════════════════════════
async def recv_lastname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not allowed(update):
        await deny(update); return ConversationHandler.END

    context.user_data["last_name"] = update.message.text.strip()
    return await _finalize(update, context)

async def _finalize(update, context, via_cb=False) -> int:
    chat_id = update.effective_chat.id
    client: TelegramClient = _clients.get(chat_id)

    fn        = context.user_data.get("first_name", "")
    ln        = context.user_data.get("last_name", "")
    phone     = context.user_data.get("phone", "")
    uid       = context.user_data.get("user_id", "")
    two_fa    = context.user_data.get("2fa_set", False)
    sess_file = context.user_data.get("session_file", "")

    msg_fn = (update.callback_query.edit_message_text
              if via_cb else update.message.reply_text)

    await msg_fn("⏳  Finalizing profile…", parse_mode="HTML")

    try:
        await client(UpdateProfileRequest(first_name=fn, last_name=ln))
        me = await client.get_me()
        uid = me.id
    except Exception as e:
        logger.error(f"UpdateProfile: {e}")

    # Save to accounts DB
    record = {
        "phone": phone,
        "name": f"{fn} {ln}".strip(),
        "user_id": uid,
        "2fa_set": two_fa,
        "session_file": sess_file,
        "created_at": datetime.now().isoformat(),
    }
    save_account(record)

    await client.disconnect()
    _clients.pop(chat_id, None)

    # ── Success Screen ────────────────────────────────────────────────────────
    success_msg = (
        "┌──────────────────────────────────────┐\n"
        "│  <b>🎉 ACCOUNT SETUP COMPLETE! ✅</b>      │\n"
        "└──────────────────────────────────────┘\n\n"
        f"📱  <b>Phone:</b>    <code>{phone}</code>\n"
        f"👤  <b>Name:</b>     <code>{fn} {ln}</code>\n"
        f"🆔  <b>User ID:</b>  <code>{uid}</code>\n"
        f"🔐  <b>2FA:</b>      {'✅ Enabled' if two_fa else '❌ Not set'}\n"
        f"💾  <b>Session:</b>  Saved ✅\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "You can find this account in <b>📋 My Accounts</b>\n"
        "and download its session file anytime.\n\n"
        "👇  <b>What would you like to do next?</b>"
    )

    target_chat = (update.callback_query.message.chat_id
                   if via_cb else update.effective_chat.id)

    await context.bot.send_message(
        chat_id=target_chat,
        text=success_msg,
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
#  /cancel
# ══════════════════════════════════════════════════════════════════════════════
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    client = _clients.pop(chat_id, None)
    if client:
        await client.disconnect()
    context.user_data.clear()
    await update.message.reply_text(
        "❌  <b>Setup cancelled.</b>\n\nType /start to begin again.",
        parse_mode="HTML",
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLE-INSTANCE LOCK  (PID file)
# ══════════════════════════════════════════════════════════════════════════════
PID_FILE = "bot.pid"

def _acquire_lock():
    """Kill any previously running instance, then write our own PID."""
    if os.path.exists(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
            if old_pid != os.getpid():
                os.kill(old_pid, signal.SIGTERM)
                logger.info(f"Stopped old instance (PID {old_pid})")
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # process already gone
        try:
            os.remove(PID_FILE)
        except OSError:
            pass

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    atexit.register(_release_lock)

def _release_lock():
    try:
        if os.path.exists(PID_FILE):
            pid = int(open(PID_FILE).read().strip())
            if pid == os.getpid():
                os.remove(PID_FILE)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  LAUNCH
# ══════════════════════════════════════════════════════════════════════════════
def main():
    _acquire_lock()

    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN missing in .env")
    if not API_ID or not API_HASH:
        raise ValueError("API_ID / API_HASH missing in .env  →  my.telegram.org")

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Conversation ──────────────────────────────────────────────────────────
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(cb_new_account, pattern="^new_account$"),
        ],
        states={
            ASK_PHONE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_phone)],
            ASK_OTP:       [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_otp),
                CallbackQueryHandler(cb_switch_code, pattern="^switch_code$"),
                CallbackQueryHandler(cb_resend_same,  pattern="^resend_same$"),
            ],
            ASK_EXIST_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_exist_2fa)],
            ASK_NEW_2FA:   [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_new_2fa),
                CallbackQueryHandler(skip_2fa_cb, pattern="^skip_2fa$"),
            ],
            ASK_HINT:      [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_hint),
                CallbackQueryHandler(skip_hint_cb, pattern="^skip_hint$"),
            ],
            ASK_FIRSTNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_firstname)],
            ASK_LASTNAME:  [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_lastname),
                CallbackQueryHandler(skip_lastname_cb, pattern="^skip_lastname$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)

    # ── Standalone callbacks (outside conversation) ───────────────────────────
    app.add_handler(CallbackQueryHandler(cb_my_accounts, pattern="^my_accounts$"))
    app.add_handler(CallbackQueryHandler(cb_get_session,  pattern="^get_session:"))
    app.add_handler(CallbackQueryHandler(cb_back_home,    pattern="^back_home$"))
    app.add_handler(CallbackQueryHandler(cb_new_account,  pattern="^new_account$"))

    logger.info("🤖 Bot is running!  Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
