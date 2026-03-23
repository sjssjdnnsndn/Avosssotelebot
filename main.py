import os
import random
import asyncio
import sys
import requests
import json
import threading
import zipfile
import tempfile
import shutil
import glob
import accounts_shop
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import IndexModel, ASCENDING, DESCENDING
from pymongo.errors import PyMongoError
from bson import ObjectId
from telethon import TelegramClient, events, functions, errors
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel, InputPeerChannel, ReactionEmoji, Channel
from telethon.tl.functions.messages import GetMessagesViewsRequest
from telethon.errors import (
    ApiIdInvalidError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
    PasswordHashInvalidError
)
from telethon.tl.types import Channel, ChatInviteAlready

from telethon.errors import ChannelPrivateError
from telethon.errors import ChannelInvalidError, UserNotParticipantError
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, GetFullChatRequest

from telethon.tl.types import Chat, ChannelForbidden

from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.types import InputPeerNotifySettings, InputNotifyPeer

from html import escape as html_escape
from flask import Flask, request
from pyngrok import ngrok
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ===== ROBUST SESSION MANAGER =====
from session_manager import SessionManager

# ===== CONFIGURATION ===== #
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8387013883:AAFR14_ONq2_v44zpp3qH2sTs-8SpGMDTbE")
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://sanjana928828_db_user:JejejjeejejeieiEuueueye_ywyYwywywy736633366262_yehevefhwuwjbevegEuvegehheheben@cluster0.gcwanr2.mongodb.net/?appName=Cluster0")
DB_NAME = "newviewsbohst"
ADMIN_ID = 6498333937  # Admin ID
PAYMENT_ADMIN_ID = 8094204927  # Admin to receive payment notifications
MAJOR_ADMIN_ID = 6498333937  # Major admin who can make/remove other admins and manage powers

# Telegram API credentials
API_ID = 23026955
API_HASH = "1efec7fe2abe4e2a0dc4c07d0fdd6593"

# OxaPay Configuration
OXAPAY_API_KEY = "QRQPGP-46A7PP-MZNKDG-ODWF8V"
NGROK_AUTH_TOKEN = "2zB5BrTG8WPnlCpxe1BKUG6l14h_2EYzJjDeNMksfunzDsnyY"

# Initialize bot and dispatcher
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Initialize MongoDB
client = AsyncIOMotorClient(MONGODB_URI)
db = client[DB_NAME]

# Collections
users_collection = db.users
orders_collection = db.orders
payments_collection = db.payments
channels_collection = db.channels
pricing_collection = db.pricing
sessions_collection = db.sessions
settings_collection = db.settings
api_credentials_collection = db.api_credentials

# Global variables
active_clients = []       # legacy alias — populated via session_mgr.active_clients
session_mgr: "SessionManager" = None   # Robust session manager
user_oxapay_orders = {}
public_url = None
scheduler = None  # APScheduler for night/day mode notifications

def get_active_clients():
    """Always use this helper to get live clients."""
    if session_mgr is not None:
        return session_mgr.active_clients
    return active_clients

# Reaction pools — used to randomise per-account so deliveries look natural.
# Only emojis Telegram actually accepts as reactions are listed here.
POSITIVE_REACTIONS = [
    "\u2764\ufe0f", "\U0001f525", "\U0001f44d", "\U0001f44f", "\U0001f389",
    "\U0001f929", "\U0001f60d", "\U0001f970", "\U0001f4af", "\u26a1",
    "\U0001f3c6", "\U0001f4aa", "\U0001f64c", "\U0001f601", "\U0001f31a",
    "\U0001f60e", "\U0001f917", "\U0001f64f", "\U0001f91d", "\U0001f54a",
    "\U0001f433", "\U0001f984"
]
NEGATIVE_REACTIONS = [
    "\U0001f44e", "\U0001f92c", "\U0001f92e", "\U0001f4a9", "\U0001f921",
    "\U0001f971", "\U0001f494", "\U0001f928", "\U0001f610", "\U0001f621",
    "\U0001f92a", "\U0001f608", "\U0001f32d", "\U0001f34c", "\U0001f595",
    "\U0001f485", "\U0001f47e", "\U0001f937"
]

# Initialize Flask server for OxaPay webhook
flask_app = Flask(__name__)

# Use a different port for Flask to avoid conflict with Replit's default port 5000
FLASK_PORT = 5001

# ===== OXAPAY WEBHOOK HANDLER ===== #
@flask_app.route("/verify_payment", methods=["POST", "GET"])
def verify_payment():
    """Receive payment verification from OxaPay"""
    print("\n" + "=" * 70)
    print("OXAPAY WEBHOOK RECEIVED!")
    print("=" * 70)

    if request.method == "GET":
        return "Webhook endpoint is alive!", 200

    try:
        data = request.json
        print("Received Payload:")
        print(json.dumps(data, indent=2))
    except Exception as e:
        print("JSON parsing error: " + str(e))
        return {"ok": True}, 400

    # Extract payment details
    order_id = data.get("order_id") or data.get("orderId")
    status = data.get("status") or data.get("payment_status") or "unknown"

    # OxaPay uses result code 100 for success
    if data.get("result") == 100:
        status = "success"

    print("\nExtracted Payment Info:")
    print(f"  Order ID: {order_id}")
    print(f"  Payment Status: {status}")
    print("\nCurrent Active Orders:")
    print(json.dumps(user_oxapay_orders, indent=2))

    matched = False
    for chat_id, info in user_oxapay_orders.items():
        stored_order = info.get("order_id")
        print("\nMatching Orders:")
        print(f"  Stored Order ID: {stored_order}")
        print(f"  Received Order ID: {order_id}")

        if stored_order == order_id:
            matched = True
            print("\n*** MATCH FOUND! Sending notification... ***")
            print(f"Target Chat ID: {chat_id}")

            # Get user details
            user_id = chat_id
            username = "N/A"

            # Try to get username from bot API
            try:
                telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/getChat"
                user_response = requests.post(
                    telegram_url,
                    json={"chat_id": chat_id},
                    timeout=10
                )
                if user_response.json().get("ok"):
                    user_data = user_response.json().get("result", {})
                    username = user_data.get("username") or user_data.get("first_name") or "N/A"
            except Exception as e:
                print(f"Could not fetch user details: {e}")

            if status.lower() in ["success", "paid", "completed", "100"]:
                message = (
                    "✅ *Payment Successful!*\n\n"
                    f"💳 Order ID: `{order_id}`\n"
                    f"💰 Amount: ${info.get('amount')}\n\n"
                    "🎉 Thank you for your payment!"
                )

                # Admin notification for successful payment
                admin_message = (
                    "✅ *Payment Received*\n\n"
                    f"👤 *User:* @{username}\n"
                    f"🆔 *User ID:* `{user_id}`\n"
                    f"💳 *Order ID:* `{order_id}`\n"
                    f"💰 *Amount:* ${info.get('amount')}\n"
                    f"📊 *Status:* SUCCESS\n"
                    f"🔗 *Payment Method:* OxaPay\n\n"
                    f"📦 *Payment Data:*\n"
                    f"```json\n{json.dumps(data, indent=2)[:500]}...\n```"
                )

                # Update user balance
                asyncio.create_task(update_user_balance(chat_id, float(info.get('amount'))))
            else:
                message = (
                    "⚠️ *Payment Status Update*\n\n"
                    f"Order ID: `{order_id}`\n"
                    f"Status: *{status.upper()}*\n\n"
                    "Please check your payment details."
                )

                # Admin notification for unsuccessful payment
                admin_message = (
                    "⚠️ *Payment Failed/Pending*\n\n"
                    f"👤 *User:* @{username}\n"
                    f"🆔 *User ID:* `{user_id}`\n"
                    f"💳 *Order ID:* `{order_id}`\n"
                    f"💰 *Amount:* ${info.get('amount')}\n"
                    f"📊 *Status:* {status.upper()}\n"
                    f"🔗 *Payment Method:* OxaPay\n\n"
                    f"📦 *Payment Data:*\n"
                    f"```json\n{json.dumps(data, indent=2)[:500]}...\n```"
                )

            try:
                telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"

                # Send notification to user
                response = requests.post(
                    telegram_url,
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "Markdown"
                    },
                    timeout=10,
                )
                print("\nTelegram API Response (User):")
                print("HTTP Status:", response.status_code)
                print(response.text)

                result = response.json()
                if result.get("ok"):
                    print("\n*** SUCCESS! User notification sent! ***")
                else:
                    print(
                        "\n*** FAILED! Telegram API Error: "
                        + str(result.get("description"))
                        + " ***"
                    )

                # Send notification to admin
                admin_response = requests.post(
                    telegram_url,
                    json={
                        "chat_id": PAYMENT_ADMIN_ID,
                        "text": admin_message,
                        "parse_mode": "Markdown"
                    },
                    timeout=10,
                )
                print("\nTelegram API Response (Admin):")
                print("HTTP Status:", admin_response.status_code)
                print(admin_response.text)

                admin_result = admin_response.json()
                if admin_result.get("ok"):
                    print("\n*** SUCCESS! Admin notification sent! ***")
                else:
                    print(
                        "\n*** FAILED! Admin Telegram API Error: "
                        + str(admin_result.get("description"))
                        + " ***"
                    )

            except Exception as e:
                print("\n*** ERROR sending to Telegram: " + str(e) + " ***")
            break

    if not matched:
        print("\n*** NO MATCHING ORDER FOUND ***")
        print(f"Order ID '{order_id}' not found in active orders.")

    print("=" * 70 + "\n")
    return {"ok": True}, 200


# ===== SPEED CONFIGURATION FUNCTIONS ===== #
def calculate_delay_for_speed(speed_multiplier, base_delay=1.0):
    """
    Calculate delay between actions based on speed multiplier
    Adjusted to match realistic completion times:
    - Ultra Fast (2-3 hrs): ~9-10 seconds per action
    - Fast (3-4 hrs): ~12-13 seconds per action  
    - Normal (5-6 hrs): ~19-20 seconds per action
    - Slow (7-8 hrs): ~25-27 seconds per action

    Args:
        speed_multiplier: 0.5 (Slow), 1.0 (Normal), 1.5 (Fast), 2.0 (Ultra Fast)
        base_delay: Base delay in seconds (default 1.0)
    Returns:
        Calculated delay in seconds
    """
    if speed_multiplier == 0.5:  # Slow (7-8 hrs)
        return random.uniform(25, 27)
    elif speed_multiplier == 1.0:  # Normal (5-6 hrs)
        return random.uniform(19, 20)
    elif speed_multiplier == 1.5:  # Fast (3-4 hrs)
        return random.uniform(12, 13)
    elif speed_multiplier == 2.0:  # Ultra Fast (2-3 hrs)
        return random.uniform(9, 10)
    else:
        return random.uniform(19, 20)  # Default to Normal

def estimate_delivery_duration(total_quantity, speed_multiplier, clients_count=1):
    """
    Estimate how long it will take to deliver views/reactions
    Args:
        total_quantity: Number of views/reactions to deliver
        speed_multiplier: Speed setting (0.5, 1.0, 1.5, 2.0)
        clients_count: Number of active clients
    Returns:
        Tuple of (hours, minutes) for estimated duration
    """
    base_delay = 1.0
    if speed_multiplier == 0.5:  # Slow
        base_delay = 26.0
    elif speed_multiplier == 1.0:  # Normal
        base_delay = 19.5
    elif speed_multiplier == 1.5:  # Fast
        base_delay = 12.5
    elif speed_multiplier == 2.0:  # Ultra Fast
        base_delay = 9.5

    # Calculate total time in seconds
    total_time_seconds = (total_quantity / max(clients_count, 1)) * base_delay

    # Convert to hours and minutes
    hours = int(total_time_seconds // 3600)
    minutes = int((total_time_seconds % 3600) // 60)

    return hours, minutes

def get_delay_text(total_quantity, speed_multiplier, clients_count=1):
    """Get formatted delay and delivery estimation text"""
    hours, minutes = estimate_delivery_duration(total_quantity, speed_multiplier, clients_count)

    base_delay = "25-27s" if speed_multiplier == 0.5 else ("19-20s" if speed_multiplier == 1.0 else ("12-13s" if speed_multiplier == 1.5 else "9-10s"))

    time_str = ""
    if hours > 0:
        time_str += f"{hours}h "
    time_str += f"{minutes}m"

    return f"Delay: {base_delay} (Delivers {total_quantity} Views in {time_str})"

def get_speed_name(speed_multiplier):
    """Get friendly name and emoji for speed multiplier"""
    if speed_multiplier == 0.5:
        return "Slow (7-8 hrs)", "🐌"
    elif speed_multiplier == 1.0:
        return "Normal (5-6 hrs)", "🐢"
    elif speed_multiplier == 1.5:
        return "Fast (3-4 hrs)", "🚀"
    elif speed_multiplier == 2.0:
        return "Ultra Fast (2-3 hrs)", "⚡"
    else:
        return "Normal (5-6 hrs)", "🐢"

def is_night_hours():
    """Check if current IST time is in night hours (11 PM to 7 AM IST).
    IST = UTC + 5:30. Night window: 23:00–07:00 IST."""
    from datetime import timezone, timedelta as td
    IST = timezone(td(hours=5, minutes=30))
    ist_now = datetime.now(IST)
    hour = ist_now.hour
    return hour >= 23 or hour < 7

def get_night_mode_delay_multiplier():
    """Get the night mode divisor — delivery quantity is divided by 3 (≈70% slower)."""
    return 3

# ===== NIGHT MODE NOTIFICATION FUNCTIONS (NEW FIX) ===== #
async def send_night_mode_notification():
    """Send notification when night mode starts (11 PM IST)"""
    try:
        message = (
            "🌙 <b>Night Mode Activated</b>\n\n"
            "⏰ Time: 11:00 PM IST\n"
            "🐌 Order processing speed is now 70% slower\n"
            "📊 All active orders will be completed at reduced speed\n\n"
            "💤 Normal speed will resume at 7:00 AM IST"
        )
        await bot.send_message(ADMIN_ID, message, parse_mode="HTML")
        print(f"[{datetime.now()}] ✅ Night mode notification sent")
    except Exception as e:
        print(f"❌ Error sending night mode notification: {e}")

async def send_day_mode_notification():
    """Send notification when day mode starts (7 AM IST)"""
    try:
        message = (
            "☀️ <b>Day Mode Activated</b>\n\n"
            "⏰ Time: 7:00 AM IST\n"
            "🚀 Order processing speed is back to normal\n"
            "📊 All active orders will be completed at full speed\n\n"
            "✅ Have a productive day!"
        )
        await bot.send_message(ADMIN_ID, message, parse_mode="HTML")
        print(f"[{datetime.now()}] ✅ Day mode notification sent")
    except Exception as e:
        print(f"❌ Error sending day mode notification: {e}")

def setup_night_mode_scheduler():
    """Setup APScheduler for night/day mode notifications"""
    global scheduler
    scheduler = AsyncIOScheduler()

    # Schedule night mode notification at 11 PM IST (17:30 UTC = 11 PM IST)
    scheduler.add_job(
        send_night_mode_notification,
        CronTrigger(hour=17, minute=30, timezone='UTC'),
        id='night_mode_notification',
        replace_existing=True
    )

    # Schedule day mode notification at 7 AM IST (1:30 UTC = 7 AM IST)
    scheduler.add_job(
        send_day_mode_notification,
        CronTrigger(hour=1, minute=30, timezone='UTC'),
        id='day_mode_notification',
        replace_existing=True
    )

    scheduler.start()
    print("✅ Night mode scheduler started - Notifications at 11 PM and 7 AM IST")



def calculate_delivery_time(quantity, delay_seconds, clients_count=1):
    """
    Calculate estimated delivery time based on quantity, delay, and active clients
    Args:
        quantity: Total views/reactions to deliver
        delay_seconds: Delay between each action in seconds
        clients_count: Number of active Telegram clients
    Returns:
        Formatted string like "3h 30m 0s"
    """
    # Calculate total time in seconds
    # Each client can deliver independently, so divide by client count
    total_seconds = (quantity / max(clients_count, 1)) * delay_seconds

    # Convert to hours, minutes, seconds
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)

    # Format the output
    return f"{hours}h {minutes}m {seconds}s"


def get_available_account_count() -> int:
    """Return currently available Telegram account count."""
    return max(0, len(get_active_clients()))


def get_per_post_limit() -> int:
    """Per-post max limit should match available accounts."""
    return max(1, get_available_account_count())


def clamp_per_post_quantity(value: int) -> int:
    """Clamp per-post quantity between 1 and available account limit."""
    return max(1, min(int(value), get_per_post_limit()))


def get_base_delay_seconds(speed_multiplier: float) -> int:
    """Base delay in seconds from speed multiplier."""
    if speed_multiplier == 0.5:
        return 26
    if speed_multiplier == 1.0:
        return 19
    if speed_multiplier == 1.5:
        return 12
    return 9


def get_order_delay_seconds(order: dict) -> int:
    """Resolve effective delay seconds for an order."""
    custom_delay_seconds = order.get("custom_delay_seconds")
    if custom_delay_seconds is not None:
        try:
            return max(1, int(custom_delay_seconds))
        except Exception:
            pass

    base_delay = get_base_delay_seconds(order.get("speed_multiplier", 1.0))
    legacy_custom = int(order.get("custom_delay", 0) or 0)
    return max(1, base_delay + legacy_custom)


def get_daily_delivery_snapshot(order: dict, per_post_quantity: int):
    """Return (remaining_today_posts, daily_quantity, eta_text)."""
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    posts_per_day = max(0, int(order.get("posts_per_day", 0) or 0))
    processed_today = order.get("processed_today", {}) or {}
    processed_posts = len(processed_today.get(today_str, []))
    remaining_today_posts = max(0, posts_per_day - processed_posts)

    daily_quantity = max(0, int(per_post_quantity) * remaining_today_posts)
    delay_seconds = get_order_delay_seconds(order)
    clients_count = max(1, get_available_account_count())
    eta_text = calculate_delivery_time(daily_quantity, delay_seconds, clients_count)

    return remaining_today_posts, daily_quantity, eta_text

async def calculate_order_price(views_per_post, posts_per_day, days):
    """Calculate order price based on parameters"""
    pricing = await pricing_collection.find_one({"service_type": "views_by_followers"})
    if not pricing:
        return 0.0

    price_per_view = pricing.get("price_per_view", 0.0001)
    total_views = views_per_post * posts_per_day * days
    total_price = total_views * price_per_view
    return round(total_price, 4)

async def get_delivered_amount(order):
    """Get the amount already delivered for an order"""
    delivered = order.get("delivered_views", 0) or order.get("delivered_reactions", 0)
    return delivered


# ===== TELEGRAM ACCOUNT MANAGEMENT ===== #
class TelegramAccountStates(StatesGroup):
    # Legacy login states
    LOGIN_API_ID = State()
    LOGIN_API_HASH = State()
    LOGIN_PHONE = State()
    LOGIN_CODE = State()
    LOGIN_PASSWORD = State()
    CONFIRM_REMOVE_ACCOUNT = State()
    # Session file import states (old)
    IMPORT_SESSION_STRING = State()
    IMPORT_API_ID = State()
    IMPORT_API_HASH = State()
    IMPORT_PHONE = State()
    # New: Generate Session States
    GENERATE_SESSION_API_ID = State()
    GENERATE_SESSION_API_HASH = State()
    GENERATE_SESSION_PHONE = State()
    GENERATE_SESSION_CODE = State()
    GENERATE_SESSION_PASSWORD = State()
    # New: Login with String Session States
    LOGIN_STRING_SESSION_INPUT = State()
    LOGIN_STRING_SESSION_API_ID = State()
    LOGIN_STRING_SESSION_API_HASH = State()
    # NEW: Bulk ZIP Import State
    BULK_IMPORT_ZIP = State()
    # API Credentials Management
    ADD_API_ID = State()
    ADD_API_HASH = State()

async def create_telegram_client(session_string=None, api_id=None, api_hash=None):
    """Create Telegram client with optional custom API credentials"""
    use_api_id = int(api_id) if api_id else int(API_ID)
    use_api_hash = api_hash if api_hash else API_HASH
    return TelegramClient(
        StringSession(session_string),
        use_api_id,
        use_api_hash
    )

async def create_telegram_client_from_file(session_file, api_id=None, api_hash=None):
    """Create Telegram client using session file (more stable)"""
    use_api_id = int(api_id) if api_id else int(API_ID)
    use_api_hash = api_hash if api_hash else API_HASH
    return TelegramClient(
    session_file,
    use_api_id,
    use_api_hash,
    connection_retries=5,
    retry_delay=3
)

def normalize_session_phone(phone=None, session_name=None, user_id=None):
    """Return a stable unique identifier when phone is missing."""
    cleaned_phone = (phone or "").strip()
    if cleaned_phone:
        return cleaned_phone

    session_name = (session_name or "").replace(".session", "").strip()
    if session_name:
        return f"session:{session_name}"

    if user_id:
        return f"uid:{user_id}"

    return f"session:{int(datetime.utcnow().timestamp())}"


def build_session_label(phone=None, username=None, session_name=None, user_id=None):
    if phone:
        return str(phone)

    uname = (username or "").strip()
    if uname:
        return f"@{uname}"

    sname = (session_name or "").replace(".session", "").strip()
    if sname:
        return f"session-{sname}"

    if user_id:
        return f"session-{user_id}"

    return "session-unknown"


def get_session_status_meta(session):
    status = (session.get('status') or '').lower().strip()

    if status == 'active':
        return "✅", "Active"
    if status in {'unauthorized', 'expired'}:
        return "⚠️", "Expired - Needs Re-login"
    if status == 'connection_error':
        return "🔌", "Connection Error"
    if status in {'error', 'load_error'}:
        return "❌", "Error"

    # Robust fallback: do not show Unknown when no explicit error is stored.
    if session.get('last_error'):
        return "⚠️", "Needs Recheck"
    return "✅", "Active"


def get_session_username_text(session):
    username = (session.get('username') or '').strip()
    return f"@{username}" if username else "No username"


def get_session_display_label(session):
    return (
        session.get('session_label')
        or session.get('phone')
        or build_session_label(
            phone=session.get('phone'),
            username=session.get('username'),
            session_name=session.get('session_name'),
            user_id=session.get('user_id')
        )
    )


def annotate_client(client, phone=None, user_data=None, session_name=None):
    user_id = user_data.id if user_data else None
    username = user_data.username if user_data else None

    client._session_phone = normalize_session_phone(phone, session_name=session_name, user_id=user_id)
    client._session_user_id = user_id
    client._session_username = username or ""
    client._session_label = build_session_label(
        phone=client._session_phone,
        username=username,
        session_name=session_name,
        user_id=user_id
    )
    client._session_name = (session_name or "").replace(".session", "")


async def disconnect_active_client_by_identity(phone=None, user_id=None):
    """Remove already-loaded duplicate account and keep only the latest usable one."""
    survivors = []
    pool = get_active_clients()

    for existing in list(pool):
        existing_phone = getattr(existing, "_session_phone", None)
        existing_user_id = getattr(existing, "_session_user_id", None)

        is_same_phone = bool(phone and existing_phone == phone)
        is_same_user = bool(user_id and existing_user_id == user_id)

        if is_same_phone or is_same_user:
            try:
                if existing.is_connected():
                    await existing.disconnect()
            except Exception:
                pass
        else:
            survivors.append(existing)

    # Mutate in-place so all references to this list stay consistent
    pool.clear()
    pool.extend(survivors)


async def register_client_as_active(client, user_data=None, phone=None, session_name=None):
    if user_data is None:
        try:
            user_data = await client.get_me()
        except Exception:
            user_data = None

    normalized_phone = normalize_session_phone(
        phone or (user_data.phone if user_data else None),
        session_name=session_name,
        user_id=(user_data.id if user_data else None)
    )
    user_id = user_data.id if user_data else None

    await disconnect_active_client_by_identity(phone=normalized_phone, user_id=user_id)
    annotate_client(client, phone=normalized_phone, user_data=user_data, session_name=session_name)
    # Always append to the live pool returned by get_active_clients()
    # (session_mgr.active_clients when session_mgr is set, else global active_clients)
    get_active_clients().append(client)
    return normalized_phone


def should_cleanup_and_retry(error_message: str) -> bool:
    msg = (error_message or "").lower()
    retry_keywords = [
        "same ip",
        "already",
        "duplicate",
        "auth key",
        "phone number occupied",
        "authorization key",
        "key duplicated"
    ]
    return any(k in msg for k in retry_keywords)


def remove_session_file_by_name(session_name):
    clean_name = (session_name or "").replace('.session', '').strip()
    if not clean_name:
        return

    try:
        sessions_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
        file_path = os.path.join(sessions_dir, f"{clean_name}.session")
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        print(f"⚠️ Could not remove session file {session_name}: {e}")


async def store_session(phone, session_string, user_data, api_id=None, api_hash=None, session_name=None, status="active"):
    now = datetime.utcnow()
    normalized_phone = normalize_session_phone(
        phone,
        session_name=session_name,
        user_id=(user_data.id if user_data else None)
    )

    session_data = {
        "phone": normalized_phone,
        "session_string": session_string,
        "user_id": user_data.id if user_data else None,
        "first_name": user_data.first_name if user_data else "",
        "last_name": user_data.last_name if user_data else "",
        "username": user_data.username if user_data else "",
        "session_name": (session_name or "").replace(".session", ""),
        "session_label": build_session_label(
            phone=normalized_phone,
            username=(user_data.username if user_data else None),
            session_name=session_name,
            user_id=(user_data.id if user_data else None)
        ),
        "status": status,
        "last_error": None,
        "last_check": now,
        "last_successful_load": now,
        "updated_at": now,
        "api_id": api_id,
        "api_hash": api_hash,
    }

    await sessions_collection.update_one(
        {"phone": normalized_phone},
        {
            "$set": session_data,
            "$setOnInsert": {"created_at": now}
        },
        upsert=True
    )

    return normalized_phone

async def get_all_sessions():
    return await sessions_collection.find({}).to_list(None)

async def remove_session(phone):
    await sessions_collection.delete_one({"phone": phone})


# ===== API CREDENTIALS MANAGEMENT ===== #

async def get_all_api_credentials():
    """Return all stored API ID/HASH pairs sorted by insertion order."""
    return await api_credentials_collection.find({}).sort("created_at", 1).to_list(None)


async def add_api_credential(api_id: int, api_hash: str) -> str:
    """Add a new API ID/HASH pair. Returns the inserted document ID."""
    now = datetime.utcnow()
    doc = {
        "api_id": int(api_id),
        "api_hash": str(api_hash).strip(),
        "session_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    result = await api_credentials_collection.insert_one(doc)
    return str(result.inserted_id)


async def delete_api_credential(cred_id: str):
    """Delete an API credential by its ObjectId string."""
    await api_credentials_collection.delete_one({"_id": ObjectId(cred_id)})


async def rebalance_sessions_across_apis():
    """
    Redistribute all sessions evenly among all stored API credentials.
    Updates each session's api_id and api_hash in the database.
    Also reloads the in-memory active_clients with the correct API pairs.
    Returns (total_sessions, total_apis, distribution_text)
    """
    all_apis = await get_all_api_credentials()
    if not all_apis:
        return 0, 0, "No API credentials stored."

    all_sessions = await get_all_sessions()
    total_sessions = len(all_sessions)
    total_apis = len(all_apis)

    if total_sessions == 0:
        return 0, total_apis, "No sessions to distribute."

    # Calculate distribution: ceil(total_sessions / total_apis) per API
    base = total_sessions // total_apis
    remainder = total_sessions % total_apis

    distribution_lines = []
    session_idx = 0

    for api_idx, api_cred in enumerate(all_apis):
        # First `remainder` APIs get one extra session
        count = base + (1 if api_idx < remainder else 0)
        assigned_sessions = all_sessions[session_idx: session_idx + count]
        session_idx += count

        api_id = api_cred["api_id"]
        api_hash = api_cred["api_hash"]
        api_label = f"API {api_idx + 1} (ID: {api_id})"
        distribution_lines.append(f"{api_label} → {count} sessions")

        # Update each assigned session in DB
        for sess in assigned_sessions:
            await sessions_collection.update_one(
                {"_id": sess["_id"]},
                {"$set": {"api_id": api_id, "api_hash": api_hash, "updated_at": datetime.utcnow()}}
            )

        # Update session_count on the API credential
        await api_credentials_collection.update_one(
            {"_id": api_cred["_id"]},
            {"$set": {"session_count": count, "updated_at": datetime.utcnow()}}
        )

    return total_sessions, total_apis, "\n".join(distribution_lines)


def get_api_for_session_index(session_index: int, all_apis: list, total_sessions: int) -> dict:
    """
    Given a 0-based session_index, return the correct api credential dict
    from all_apis using even distribution logic.
    """
    total_apis = len(all_apis)
    if total_apis == 0:
        return {"api_id": API_ID, "api_hash": API_HASH}

    base = total_sessions // total_apis
    remainder = total_sessions % total_apis

    # Find which API bucket this session falls into
    cumulative = 0
    for api_idx, api_cred in enumerate(all_apis):
        count = base + (1 if api_idx < remainder else 0)
        cumulative += count
        if session_index < cumulative:
            return api_cred

    # Fallback
    return all_apis[-1]


async def load_all_clients_from_files():
    """Load clients from .session files - MORE STABLE approach"""
    get_active_clients().clear()

    print("\n" + "="*50)
    print("🔄 Loading Telegram Accounts from Session Files...")
    print("="*50)

    import glob
    # Create sessions directory if it doesn't exist
    sessions_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
    os.makedirs(sessions_dir, exist_ok=True)

    session_files = sorted(glob.glob(f"{sessions_dir}/*.session"))

    if not session_files:
        print(f"⚠️ No .session files found in {sessions_dir}")
        print(f"💡 Place your .session files in {sessions_dir} directory")
        print("="*50 + "\n")
        return

    print(f"📊 Found {len(session_files)} session file(s)\n")

    loaded_count = 0
    failed_count = 0

    for idx, session_file in enumerate(session_files, 1):
        session_name = os.path.basename(session_file).replace('.session', '')
        print(f"[{idx}/{len(session_files)}] Processing: {session_name}")

        client = None
        handled = False

        for attempt in range(2):
            try:
                # Create client from file
                client = await create_telegram_client_from_file(
                    session_file,
                    api_id=API_ID,
                    api_hash=API_HASH
                )


                await asyncio.wait_for(client.connect(), timeout=15)

                print(f"  🔗 Connected successfully")

                # Verify authorization (only explicit false means expired)
                if not await client.is_user_authorized():
                    print("  ⚠️ Session unauthorized (explicit check failed)")
                    placeholder_phone = normalize_session_phone(None, session_name=session_name)
                    await sessions_collection.update_one(
                        {"phone": placeholder_phone},
                        {
                            "$set": {
                                "session_name": session_name,
                                "session_label": build_session_label(session_name=session_name),
                                "status": "unauthorized",
                                "last_error": "Session unauthorized (explicit check)",
                                "last_check": datetime.utcnow(),
                                "updated_at": datetime.utcnow()
                            },
                            "$setOnInsert": {"created_at": datetime.utcnow()}
                        },
                        upsert=True
                    )
                    await client.disconnect()
                    failed_count += 1
                    handled = True
                    break

                me = await client.get_me()
                normalized_phone = normalize_session_phone(me.phone, session_name=session_name, user_id=me.id)

                # Persist healthy session as active regardless of username availability
                await store_session(
                    phone=normalized_phone,
                    session_string=client.session.save(),
                    user_data=me,
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_name=session_name,
                    status="active"
                )

                # Replace any previously loaded duplicate account and keep this live client
                await register_client_as_active(
                    client,
                    user_data=me,
                    phone=normalized_phone,
                    session_name=session_name
                )

                name = me.first_name or "No name"
                username = f"@{me.username}" if me.username else "No username"
                print(f"  ✅ Loaded: {name} {username} ({normalized_phone})")

                loaded_count += 1
                handled = True
                break

            except Exception as e:
                err_text = str(e)
                is_last_attempt = attempt == 1

                if client:
                    try:
                        if client.is_connected():
                            await client.disconnect()
                    except Exception:
                        pass

                if not is_last_attempt and should_cleanup_and_retry(err_text):
                    print(f"  ♻️ Duplicate/IP issue detected. Clearing stale auth_key and retrying...")
                    # Clear the cached auth_key from the SQLite session file so Telethon
                    # negotiates a fresh one on the next connect attempt.
                    try:
                        import sqlite3 as _sqlite3
                        sq_path = session_file  # already ends with .session
                        sq_conn = _sqlite3.connect(sq_path)
                        sq_conn.execute("UPDATE sessions SET auth_key = NULL")
                        sq_conn.commit()
                        sq_conn.close()
                        print(f"  🧹 Cleared auth_key from {os.path.basename(sq_path)}")
                    except Exception as sq_err:
                        print(f"  ⚠️ Could not clear auth_key: {sq_err}")
                    try:
                        await sessions_collection.delete_many({"session_name": session_name, "status": {"$ne": "active"}})
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    continue

                print(f"  ❌ Failed to load: {err_text}")
                error_status = "connection_error" if any(x in err_text.lower() for x in ["timeout", "connection", "network", "flood", "reset"]) else "load_error"
                placeholder_phone = normalize_session_phone(None, session_name=session_name)
                await sessions_collection.update_one(
                    {"phone": placeholder_phone},
                    {
                        "$set": {
                            "session_name": session_name,
                            "session_label": build_session_label(session_name=session_name),
                            "status": error_status,
                            "last_error": err_text[:300],
                            "last_check": datetime.utcnow(),
                            "updated_at": datetime.utcnow()
                        },
                        "$setOnInsert": {"created_at": datetime.utcnow()}
                    },
                    upsert=True
                )
                failed_count += 1
                handled = True
                break

        if not handled:
            failed_count += 1

    print("\n" + "="*50)
    print(f"✅ Successfully loaded: {loaded_count} account(s)")
    if failed_count > 0:
        print(f"❌ Failed to load: {failed_count} account(s)")
    print("="*50)

    if get_active_clients():
        print(f"👑 MASTER CLIENT: Client #0")
        if len(get_active_clients()) > 1:
            print(f"👷 WORKER CLIENTS: {len(get_active_clients()) - 1} workers")
    else:
        print("⚠️ WARNING: No active clients available!")
    print("="*50 + "\n")

async def load_all_clients():
    """Load clients from database (legacy method - kept for backward compatibility)"""
    get_active_clients().clear()

    print("\n" + "="*50)
    print("🔄 Loading Telegram Accounts from Database...")
    print("="*50)

    sessions = await get_all_sessions()

    if not sessions:
        print("⚠️ No sessions found in database")
        print("="*50 + "\n")
        return

    print(f"📊 Found {len(sessions)} session(s) in database\n")

    loaded_count = 0
    failed_count = 0

    for idx, session in enumerate(sessions, 1):
        phone = session.get('phone', 'Unknown')
        print(f"[{idx}/{len(sessions)}] Processing: {phone}")

        try:
            # Create and connect client with stored API credentials
            api_id = session.get('api_id')
            api_hash = session.get('api_hash')
            session_str = session.get('session_string')

            if not session_str:
                print(f"  ⚠️ No session_string found — skipping (malformed entry)")
                failed_count += 1
                continue

            if not api_id or not api_hash:
                print(f"  ⚠️ Missing API credentials, using defaults")

            client = await create_telegram_client(
                session_str,
                api_id=api_id,
                api_hash=api_hash
            )

            # Connect with error handling
            try:
                await client.connect()
                print(f"  🔗 Connected successfully")
            except Exception as conn_err:
                print(f"  ❌ Connection failed: {conn_err}")
                print(f"  💾 Session kept in database - may recover on next restart")

                # Mark connection error but DON'T delete
                await sessions_collection.update_one(
                    {"phone": phone},
                    {
                        "$set": {
                            "status": "connection_error",
                            "last_error": f"Connection failed: {str(conn_err)}",
                            "last_check": datetime.utcnow()
                        }
                    }
                )
                failed_count += 1
                continue

            # Verify authorization
            try:
                if not await client.is_user_authorized():
                    print(f"  ❌ Session not authorized (expired/invalid)")
                    print(f"  💾 Session kept in database for re-authentication")

                    # Mark session as inactive but DON'T delete
                    await sessions_collection.update_one(
                        {"phone": phone},
                        {
                            "$set": {
                                "status": "unauthorized",
                                "last_error": "Session expired or unauthorized",
                                "last_check": datetime.utcnow()
                            }
                        }
                    )
                    await client.disconnect()
                    failed_count += 1
                    continue
            except Exception as auth_err:
                print(f"  ❌ Auth check failed: {auth_err}")
                print(f"  💾 Session kept in database for troubleshooting")

                # Mark session with error but DON'T delete
                await sessions_collection.update_one(
                    {"phone": phone},
                    {
                        "$set": {
                            "status": "error",
                            "last_error": str(auth_err),
                            "last_check": datetime.utcnow()
                        }
                    }
                )
                await client.disconnect()
                failed_count += 1
                continue

            # Get user info and add to active clients
            try:
                me = await client.get_me()
                name = me.first_name or "Unknown"
                username = f"@{me.username}" if me.username else "No username"
                print(f"  ✅ Loaded: {name} {username} ({phone})")

                # Mark session as active and working
                await sessions_collection.update_one(
                    {"phone": phone},
                    {
                        "$set": {
                            "status": "active",
                            "last_error": None,
                            "last_check": datetime.utcnow(),
                            "last_successful_load": datetime.utcnow()
                        }
                    }
                )

                # Add to active clients (without full reload)
                await register_client_as_active(
                    client,
                    user_data=me,
                    phone=phone,
                    session_name=session.get('session_name')
                )
                loaded_count += 1
            except Exception as e:
                print(f"  ⚠️ Error getting user info: {e}")
                # Still add to active clients if connection is valid
                await register_client_as_active(
                    client,
                    user_data=None,
                    phone=phone,
                    session_name=session.get('session_name')
                )
                loaded_count += 1

        except Exception as e:
            print(f"  ❌ Failed to load: {e}")
            print(f"  💾 Session kept in database for debugging")

            # Mark general error but DON'T delete
            await sessions_collection.update_one(
                {"phone": phone},
                {
                    "$set": {
                        "status": "load_error",
                        "last_error": f"Failed to load: {str(e)}",
                        "last_check": datetime.utcnow()
                    }
                }
            )
            failed_count += 1

    print("\n" + "="*50)
    print(f"✅ Successfully loaded: {loaded_count} account(s)")
    if failed_count > 0:
        print(f"❌ Failed to load: {failed_count} account(s)")
    print("="*50)

    # Set master client for monitoring if available
    if get_active_clients():
        print(f"👑 MASTER CLIENT: Client #0")
        if len(get_active_clients()) > 1:
            print(f"👷 WORKER CLIENTS: {len(get_active_clients()) - 1} workers")
    else:
        print("⚠️ WARNING: No active clients available!")
    print("="*50 + "\n")

# ===== ORDER PROCESSING FUNCTIONS ===== #
async def process_view_order(client, channel_id, message_id):
    try:
        await client(functions.account.UpdateStatusRequest(offline=False))
        channel_entity = await client.get_entity(PeerChannel(channel_id))
        await client(GetMessagesViewsRequest(
            peer=channel_entity,
            id=[int(message_id)],
            increment=True
        ))
        return True
    except Exception as e:
        print(f"Error sending view: {e}")
        return False


async def process_vote_order(client, channel_id, message_id, option_index, poll_options_count=None):
    try:
        await client(functions.account.UpdateStatusRequest(offline=False))
        # Get the input peer
        channel_entity = await client.get_entity(PeerChannel(channel_id))
        input_channel = InputPeerChannel(channel_entity.id, channel_entity.access_hash)

        # First, fetch the actual poll message to get correct option data
        messages = await client.get_messages(channel_entity, ids=message_id)
        if not messages or not messages.media or not hasattr(messages.media, 'poll'):
            print(f"Error: Message {message_id} doesn't contain a valid poll")
            return False

        poll = messages.media.poll
        poll_results = messages.media.results

        # Handle random vote selection
        if option_index == "random":
            if poll_options_count:
                option_index = random.randint(0, poll_options_count - 1)
            else:
                option_index = random.randint(0, len(poll.answers) - 1)

        # Validate option index
        if option_index >= len(poll.answers):
            print(f"Error: Invalid option index {option_index}, poll has {len(poll.answers)} options")
            return False

        # Get the correct option bytes from the poll
        selected_option = poll.answers[option_index].option

        # Create the vote request with correct option format
        await client(functions.messages.SendVoteRequest(
            peer=input_channel,
            msg_id=message_id,
            options=[selected_option]  # Use actual option bytes from poll
        ))
        return True
    except Exception as e:
        print(f"Error sending vote: {e}")
        import traceback
        traceback.print_exc()
        return False



# ===== PRICING INITIALIZATION ===== #
async def init_pricing():
    if await pricing_collection.count_documents({}) == 0:
        default_pricing = {
            "manual_views": 0.05,  # per view
            "manual_reactions": 0.07,  # per reaction
            "poll_votes": 0.03,  # per vote
            "views_by_followers": {
                "view_coeff": 0.0345,
                "post_coeff": 0.1081,
                "day_coeff": 0.0039
            },
            "reactions_by_followers": {
                "view_coeff": 0.314,
                "post_coeff": 0.108,
                "day_coeff": 0.0036
            },
            "exchange_rate": 83.50,  # USD to INR
            "last_updated": datetime.utcnow()
        }
        await pricing_collection.insert_one(default_pricing)

# ===== DATABASE MODELS ===== #
async def create_indexes():
    await users_collection.create_indexes([
        IndexModel([("user_id", ASCENDING)], unique=True),
        IndexModel([("username", ASCENDING)], sparse=True)
    ])

    await orders_collection.create_indexes([
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("status", ASCENDING)]),
        IndexModel([("created_at", DESCENDING)]),
        IndexModel([("service_identifier", ASCENDING)])
    ])

    await payments_collection.create_indexes([
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("status", ASCENDING)]),
        IndexModel([("created_at", DESCENDING)])
    ])

    await channels_collection.create_indexes([
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("channel_id", ASCENDING)], unique=True)
    ])

    await sessions_collection.create_indexes([
        IndexModel([("phone", ASCENDING)], unique=True),
        IndexModel([("user_id", ASCENDING)])
    ])

    # Initialize pricing
    await init_pricing()


async def get_or_create_user(user_id: int, username: str = None, first_name: str = None):
    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        user_data = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "balance": 0.0,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "is_admin": False
        }
        await users_collection.insert_one(user_data)
        return user_data
    return user

async def update_user_balance(user_id: int, amount: float):
    return await users_collection.update_one(
        {"user_id": user_id},
        {
            "$inc": {"balance": amount},
            "$set": {"updated_at": datetime.utcnow()}
        }
    )

async def create_order(
    user_id: int,
    service_identifier: str,
    service_type: str,
    channel_id: int = None,
    channel_title: str = None,
    content_id: int = None,
    quantity: int = 0,
    emoji: str = None,
    charge: float = 0.0,
    status: str = "pending",
    poll_id: str = None,
    poll_question: str = None,
    option_index: int = None,
    option_text: str = None,
    total_views: int = None,
    total_reactions: int = None,
    posts_per_day: int = None,
    days: int = None,
    delivered_views: int = 0,
    delivered_reactions: int = 0
):
    order_data = {
        "user_id": user_id,
        "service_identifier": service_identifier,
        "service_type": service_type,
        "channel_id": channel_id,
        "channel_title": channel_title,
        "content_id": content_id,
        "quantity": quantity,
        "emoji": emoji,
        "charge": charge,
        "status": status,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "delivered_views": delivered_views,
        "delivered_reactions": delivered_reactions,
        "custom_delay_seconds": None  # Default delay of 19 seconds
    }

    if poll_id:
        order_data["poll_id"] = poll_id
    if poll_question:
        order_data["poll_question"] = poll_question
    if option_index is not None:
        order_data["option_index"] = option_index
    if option_text:
        order_data["option_text"] = option_text
    if total_views is not None:
        order_data["total_views"] = total_views
    if total_reactions is not None:
        order_data["total_reactions"] = total_reactions
    if posts_per_day is not None:
        order_data["posts_per_day"] = posts_per_day
    if days is not None:
        order_data["days"] = days

    result = await orders_collection.insert_one(order_data)
    return result.inserted_id

async def create_payment(
    user_id: int,
    amount: float,
    method: str,
    status: str = "pending",
    transaction_id: str = None,
    screenshot_file_id: str = None
):
    payment_data = {
        "user_id": user_id,
        "amount": amount,
        "method": method,
        "status": status,
        "transaction_id": transaction_id,
        "screenshot_file_id": screenshot_file_id,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    result = await payments_collection.insert_one(payment_data)
    return result.inserted_id

async def add_user_channel(user_id: int, channel_id: int, channel_title: str, is_public: bool, invite_link: str = None):
    channel_data = {
        "user_id": user_id,
        "channel_id": channel_id,
        "channel_title": channel_title,
        "is_public": is_public,
        "invite_link": invite_link,
        "created_at": datetime.utcnow()
    }
    await channels_collection.update_one(
        {"channel_id": channel_id},
        {"$set": channel_data},
        upsert=True
    )

async def delete_user_channel(channel_id: int):
    await channels_collection.delete_one({"channel_id": channel_id})

async def get_user_channels(user_id: int):
    return await channels_collection.find({"user_id": user_id}).to_list(None)

# ===== PRICING UTILITIES ===== #
async def get_pricing():
    return await pricing_collection.find_one({})

async def get_live_exchange_rate():
    # Try to get from database first
    try:
        pricing = await get_pricing()
        if pricing:
            return pricing['exchange_rate']
    except:
        pass

    # Fallback to API
    try:
        api_url = "https://api.exchangerate-api.com/v4/latest/USD"
        response = requests.get(api_url).json()
        return response["rates"]["INR"]
    except:
        print("Error fetching live rate. Using default rate (83.50)")
        return 83.50

async def usd_to_inr_converter(usd_amount):
    exchange_rate = await get_live_exchange_rate()
    inr_amount = usd_amount * exchange_rate
    return f"₹{inr_amount:.2f}"

async def calculate_views_charge(total_views: int, posts_per_day: int, days: int) -> float:
    pricing = await get_pricing()
    coeffs = pricing['views_by_followers']

    view_total = total_views * coeffs['view_coeff']
    post_total = posts_per_day * coeffs['post_coeff']
    day_total = days * coeffs['day_coeff']
    return round(view_total + post_total + day_total, 4)

async def calculate_reactions_charge(total_views: int, posts_per_day: int, days: int) -> float:
    pricing = await get_pricing()
    coeffs = pricing['reactions_by_followers']

    react_total = total_views * coeffs['view_coeff']
    post_total = posts_per_day * coeffs['post_coeff']
    day_total = days * coeffs['day_coeff']
    return round(react_total + post_total + day_total, 4)

# ===== KEYBOARDS ===== #
def get_main_menu_keyboard():
    keyboard = [
        [
            KeyboardButton(text="📊 Views By Followers"),
            KeyboardButton(text="❤️‍🔥 Reactions By Followers")
        ],
        [
            KeyboardButton(text="🗳️ Order Votes")
        ],
        [
            KeyboardButton(text="📝📊 Manual Views"),
            KeyboardButton(text="📝❤️‍🔥 Manual Reactions")
        ],
        [
            KeyboardButton(text="💳 Add Balance")
        ],
        [
            KeyboardButton(text="👤 My Account"),
            KeyboardButton(text="🆘 Support")
        ],
        [
            KeyboardButton(text="🛒 Buy Telegram Accounts")
        ]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Select an option"
    )

def get_confirmation_keyboard():
    keyboard = [
        [
            KeyboardButton(text="✅ Yes"),
            KeyboardButton(text="⬅️ Back")
        ],
            [KeyboardButton(text="⬅️ Cancel Order")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Select an option"
    )

def contact_button():
    keyboard = [
        [
            InlineKeyboardButton(
                text="☎️ Contact Admin",
                url=f"tg://user?id=6617707066"
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_emoji_keyboard():
    emoji_categories = [
        "🤗 Custom",
        "❤️ Positive",
        "😂 Negative",
    ]

    builder = ReplyKeyboardBuilder()

    for category in emoji_categories:
        builder.add(KeyboardButton(text=category))
    builder.adjust(1,2)

    builder.row(KeyboardButton(text="⬅️ Cancel Order"))

    return builder.as_markup(
        resize_keyboard=True,
        input_field_placeholder="Select a reaction..."
    )

# Create a custom emoji keyboard
def get_custom_emoji_keyboard():
    custom_emojis = [
        "👍", "👎", "❤", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
        "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊", "🤡",
        "🥱", "🥴", "😍", "🐳", "❤‍🔥", "🌚", "🌭", "💯", "🤣", "⚡",
        "🍌", "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈",
        "😴", "😭", "🤓", "👻", "👨‍💻", "👀", "🎃", "🙈", "😇", "😨",
        "🤝", "✍", "🤗", "🫡", "🎅", "🎄", "☃", "💅", "🤪", "🗿",
        "🆒", "💘", "🙉", "🦄", "😘", "💊", "🙊", "😎", "👾", "🤷‍♂",
        "🤷", "🤷‍♀", "😡"
    ]

    builder = ReplyKeyboardBuilder()
    # Add emojis in rows of 6
    for i in range(0, len(custom_emojis), 6):
        row = custom_emojis[i:i+6]
        for emoji in row:
            builder.add(KeyboardButton(text=emoji))
        builder.adjust(6)

    # Add back button
    builder.row(KeyboardButton(text="⬅️ Back"))

    return builder.as_markup(
        resize_keyboard=True,
        input_field_placeholder="Select a custom reaction..."
    )

def get_quantity_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="⬅️ Cancel Order"))
    return builder.as_markup(resize_keyboard=True)

def get_payment_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🇮🇳 UPI ~ All in One Deposit")],
            # [KeyboardButton(text="🪙 Crypto All (Auto)")],
            [KeyboardButton(text="💎 Crypto Deposit")],
            [KeyboardButton(text="⬅️ Back")]
        ],
        resize_keyboard=True
    )

def get_channel_select_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="➕ Add Channels",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=1,
                        chat_is_channel=True,
                        bot_is_member=True,
                        request_title=True,
                        request_username=True
                    )
                ),
                KeyboardButton(text="📢 My Channels")
            ],
            [KeyboardButton(text="⬅️ Back")]
        ],
        resize_keyboard=True
    )

def get_my_channels_keyboard(channels, active_channel_ids: set = None, service_type: str = ""):
    """Create inline channel picker for My Channels with green/red status dots."""
    if active_channel_ids is None:
        active_channel_ids = set()
    builder = InlineKeyboardBuilder()
    for channel in channels:
        cid = channel['channel_id']
        dot = "🟢" if cid in active_channel_ids else "🔴"
        # Encode service_type so the callback can filter orders correctly
        cb_data = f"my_channel:{cid}:{service_type}" if service_type else f"my_channel:{cid}"
        builder.row(
            InlineKeyboardButton(
                text=f"{dot} {channel['channel_title']}",
                callback_data=cb_data
            )
        )
    builder.row(InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_menu"))
    return builder.as_markup()

def get_payment_confirmation_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Confirm Payment")],
            [KeyboardButton(text="✖️ Cancel")]
        ],
        resize_keyboard=True
    )

def get_admin_payment_approval_keyboard(payment_id: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Approve Payment", callback_data=f"approve_payment:{payment_id}")
    builder.button(text="❌ Decline Payment", callback_data=f"decline_payment:{payment_id}")
    return builder.as_markup()

# ===== STATES ===== #
class OrderStates(StatesGroup):
    waiting_for_content = State()
    waiting_for_quantity = State()
    waiting_for_confirmation = State()
    waiting_for_emoji = State()
    waiting_for_custom_emoji = State()
    service_type = State()
    CONFIGURING_VIEWS = State()
    CONFIGURING_REACTIONS = State()
    waiting_for_option = State()
    waiting_for_vote_type = State()  # NEW: Ask user for specific or random votes
    SELECTING_CHANNEL = State()
    WAITING_FOR_INVITE_LINK = State()
    ADJUST_DELAY = State()  # For custom delay adjustment
    EDITING_ORDER = State()  # For editing order settings
    EDIT_CONFIRMING = State()  # For confirming order edits

class PaymentStates(StatesGroup):
    SELECT_METHOD = State()
    ENTER_AMOUNT = State()
    PAYMENT_DETAILS = State()
    WAITING_FOR_SCREENSHOT = State()
    OXAPAY_AMOUNT_SELECTION = State()
    OXAPAY_CONFIRM = State()

class AdminStates(StatesGroup):
    VIEWING_USER = State()
    EDITING_BALANCE = State()
    VIEWING_ORDERS = State()
    BROADCASTING = State()
    CONFIRM_BROADCAST = State()
    PRICING_MENU = State()
    EDITING_PRICE = State()
    EDITING_COEFF = State()
    EDITING_EXCHANGE = State()
    MAKE_ADMIN = State()
    REMOVE_ADMIN = State()
    POWERS_MANAGEMENT = State()
    SELECTING_ADMIN_FOR_POWERS = State()
    MANAGING_SPECIFIC_ADMIN_POWERS = State()
    SETTING_UPI_QR = State()
    SETTING_UPI_ID = State()

class VoteOrderStates(StatesGroup):
    WAITING_FOR_CHANNEL = State()
    WAITING_FOR_INVITE_LINK = State()
    WAITING_FOR_POST_FORWARD = State()
    WAITING_FOR_OPTION_SELECTION = State()
    WAITING_FOR_QUANTITY = State()
    WAITING_FOR_CONFIRMATION = State()

class SupportStates(StatesGroup):
    WAITING_FOR_QUERY = State()
    CONFIRMING_QUERY = State()

class AdminSupportStates(StatesGroup):
    WAITING_FOR_REPLY = State()

# ===== CONFIGURATION CLASS ===== #
class ConfigData:
    def __init__(self):
        default_per_post = min(10, get_per_post_limit())
        self.max_views = get_per_post_limit()
        self.total_views = default_per_post
        self.total_reactions = default_per_post  # used only in reactions
        self.posts_per_day = 20
        self.days = 30
        self.charge = 2.6240
        self.channel_id = None
        self.channel_title = ""
        self.final_total_views = 0
        self.final_total_reactions = 0
        self.speed_multiplier = 1.0  # 1x = Normal, 1.5x = Fast, 2.0x = Ultra Fast
        self.speed_name = "Normal"
        self.invite_link = None


client_reactions = {}
# Store configurations
user_configs = {}
# Track users currently processing a payment (prevents duplicate order spam)
payment_processing_users = set()

async def get_config_text(config: ConfigData, is_reactions: bool = False) -> str:
    pricing = await get_pricing()
    # Use speed config function to get emoji
    speed_name, speed_emoji = get_speed_name(config.speed_multiplier)
    max_limit = get_per_post_limit()

    config.total_views = clamp_per_post_quantity(config.total_views)
    if hasattr(config, "total_reactions"):
        config.total_reactions = clamp_per_post_quantity(config.total_reactions)

    if is_reactions:
        reactions_count = config.total_reactions if hasattr(config, "total_reactions") else config.total_views
        return (
            "*⚙️ System Configuration:*\n"
            "_Please configure the necessary parameters to initiate the process!_\n\n"
            f"❤️‍🔥 *Maximum Reactions Limit:* `Max {max_limit}`\n\n"
            f"📊 *Reaction Per Post:* `{reactions_count}`\n"
            f"📝 *Posts Per Day:* `{config.posts_per_day}`\n"
            f"📆 *Number of Days:* `{config.days}`\n\n"
            f"💰 *Total Charge:* `${config.charge:.4f}` (`{await usd_to_inr_converter(config.charge)}`)"
        )
    else:
        return (
            "*⚙️ System Configuration:*\n"
            "_Please configure the necessary parameters to initiate the process!_\n\n"
            f"👁️ *Maximum View Limit:* `Max {max_limit}`\n\n"
            f"📊 *Views Per Post:* `{config.total_views}`\n"
            f"📝 *Posts Per Day:* `{config.posts_per_day}`\n"
            f"📆 *Number of Days:* `{config.days}`\n\n"
            f"💰 *Total Charge:* `${config.charge:.4f}` (`{await usd_to_inr_converter(config.charge)}`)"
        )

def get_views_config_markup(config: ConfigData):
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text=f"📊 Total Views: {config.total_views}", callback_data="show_views")
    )
    builder.row(
        InlineKeyboardButton(text="-100", callback_data="views:-100"),
        InlineKeyboardButton(text="-10", callback_data="views:-10"),
        InlineKeyboardButton(text="-1", callback_data="views:-1"),
        InlineKeyboardButton(text="+1", callback_data="views:1"),
        InlineKeyboardButton(text="+10", callback_data="views:10"),
        InlineKeyboardButton(text="+100", callback_data="views:100"),
        width=6
    )

    builder.row(
        InlineKeyboardButton(text=f"📝 Posts Per Day: {config.posts_per_day}", callback_data="show_posts")
    )
    builder.row(
        InlineKeyboardButton(text="-50", callback_data="posts:-50"),
        InlineKeyboardButton(text="-10", callback_data="posts:-10"),
        InlineKeyboardButton(text="-1", callback_data="posts:-1"),
        InlineKeyboardButton(text="+1", callback_data="posts:1"),
        InlineKeyboardButton(text="+10", callback_data="posts:10"),
        InlineKeyboardButton(text="+50", callback_data="posts:50"),
        width=6
    )

    builder.row(
        InlineKeyboardButton(text=f"📅 No. of Days: {config.days}", callback_data="show_days")
    )
    builder.row(
        InlineKeyboardButton(text="-30", callback_data="days:-30"),
        InlineKeyboardButton(text="-10", callback_data="days:-10"),
        InlineKeyboardButton(text="-1", callback_data="days:-1"),
        InlineKeyboardButton(text="+1", callback_data="days:1"),
        InlineKeyboardButton(text="+10", callback_data="days:10"),
        InlineKeyboardButton(text="+30", callback_data="days:30"),
        width=6
    )

    # Speed button removed - users can adjust delay directly from Active Orders
    builder.row(
        InlineKeyboardButton(text="✅ Confirm Order", callback_data="action:order"),
        InlineKeyboardButton(text="🔙 Back", callback_data="action:back"),
        width=2
    )

    return builder.as_markup()

def get_reaction_config_markup(config: ConfigData):
    builder = InlineKeyboardBuilder()

    reactions_count = config.total_reactions if hasattr(config, "total_reactions") else config.total_views
    builder.row(
        InlineKeyboardButton(text=f"❤️‍🔥 Total Reactions: {reactions_count}", callback_data="show_reactions")
    )
    builder.row(
        InlineKeyboardButton(text="-100", callback_data="reactions:-100"),
        InlineKeyboardButton(text="-10", callback_data="reactions:-10"),
        InlineKeyboardButton(text="-1", callback_data="reactions:-1"),
        InlineKeyboardButton(text="+1", callback_data="reactions:1"),
        InlineKeyboardButton(text="+10", callback_data="reactions:10"),
        InlineKeyboardButton(text="+100", callback_data="reactions:100"),
        width=6
    )

    builder.row(
        InlineKeyboardButton(text=f"📝 Posts Per Day: {config.posts_per_day}", callback_data="show_posts")
    )
    builder.row(
        InlineKeyboardButton(text="-50", callback_data="r_posts:-50"),
        InlineKeyboardButton(text="-10", callback_data="r_posts:-10"),
        InlineKeyboardButton(text="-1", callback_data="r_posts:-1"),
        InlineKeyboardButton(text="+1", callback_data="r_posts:1"),
        InlineKeyboardButton(text="+10", callback_data="r_posts:10"),
        InlineKeyboardButton(text="+50", callback_data="r_posts:50"),
        width=6
    )

    builder.row(
        InlineKeyboardButton(text=f"📅 No. of Days: {config.days}", callback_data="show_days")
    )
    builder.row(
        InlineKeyboardButton(text="-30", callback_data="r_days:-30"),
        InlineKeyboardButton(text="-10", callback_data="r_days:-10"),
        InlineKeyboardButton(text="-1", callback_data="r_days:-1"),
        InlineKeyboardButton(text="+1", callback_data="r_days:1"),
        InlineKeyboardButton(text="+10", callback_data="r_days:10"),
        InlineKeyboardButton(text="+30", callback_data="r_days:30"),
        width=6
    )

    # Speed button removed - users can adjust delay directly from Active Orders
    builder.row(
        InlineKeyboardButton(text="✅ Confirm Order", callback_data="action:order"),
        InlineKeyboardButton(text="🔙 Back", callback_data="action:back"),
        width=2
    )

    return builder.as_markup()

# ===== ORDER STATUS HANDLERS ===== #
@dp.message(F.text == "📦 Active Orders")
async def active_orders_handler(message: types.Message):
    user_id = message.from_user.id

    # Get active auto orders (views, reactions, and poll votes)
    active_orders = await orders_collection.find({
        "user_id": user_id,
        "status": {"$in": ["confirmed", "processing"]},
        "service_identifier": {"$in": ["views_by_followers", "reactions_by_followers", "poll_votes"]}
    }).to_list(None)

    if not active_orders:
        await message.answer(
            "📦 You have no active orders.\n\n"
            "Create a new order from the main menu!",
            reply_markup=get_main_menu_keyboard()
        )
        return

    for order in active_orders:
        channel_title = order.get("channel_title", "Unknown Channel")
        channel_id = order.get("channel_id")
        posts_per_day = order.get("posts_per_day", 0)
        charge = order.get("charge", 0.0)
        days = order.get("days", 0)
        created = order.get("created_at")
        is_paused = order.get("is_paused", False)
        speed_multiplier = order.get("speed_multiplier", 1.0)
        speed_name = order.get("speed_name", "Normal")

        is_muted = order.get("is_muted", False)
        mute_str = "🔕 ON" if is_muted else "🔔 OFF"
        random_views = order.get("random_views", 0)
        high_low = order.get("high_low_descending", False)
        high_low_str = "🟢 ON" if high_low else "🔴 OFF"
        night_mode = order.get("night_mode_enabled", False)
        night_str = "🌙 ON" if night_mode else "🌙 OFF"

        days_passed = (datetime.utcnow() - created).days
        days_remaining = max(0, days - days_passed)

        if order["service_identifier"] == "views_by_followers":
            service_label = "Auto Follower Views"
            per_post = order.get("views_per_post", 0)
            per_post_label = "Views"
        elif order["service_identifier"] == "reactions_by_followers":
            service_label = "Auto Follower Reactions"
            per_post = order.get("reactions_per_post", 0)
            per_post_label = "Reactions"
        elif order["service_identifier"] == "poll_votes":
            service_label = "Poll Votes"
            per_post = 0  # Poll votes don't have per_post
            per_post_label = "Votes"
        else:
            service_label = "Unknown Service"
            per_post = 0
            per_post_label = "Views"

        total_delay_sec = get_order_delay_seconds(order)
        base_delay_sec = get_base_delay_seconds(speed_multiplier)
        custom_adjustment = total_delay_sec - base_delay_sec
        adj_str = f" ({custom_adjustment:+d}s custom)" if custom_adjustment != 0 else ""
        remaining_today_posts, daily_quantity, daily_eta = get_daily_delivery_snapshot(order, per_post)

        night_note = "\n🌙 <i>Night Mode: delivery ÷3 during 11 PM–7 AM IST</i>" if night_mode else ""

        # Different text format for poll votes
        if order["service_identifier"] == "poll_votes":
            quantity = order.get("quantity", 0)
            delivered = order.get("delivered_votes", 0)
            remaining = max(0, quantity - delivered)
            poll_question = order.get("poll_question", "N/A")
            option_text = order.get("option_text", "N/A")

            status_emoji = "🟢" if order["status"] == "processing" else "🟡"
            status_text = "Active" if order["status"] == "processing" else "Confirmed"

            text = (
                f"🗳️ <b>Poll Vote Order</b>\n\n"
                f"{status_emoji} <b>Status:</b> {status_text}\n"
                f"📛 <b>Channel/Group:</b> {channel_title}\n"
                f"🆔 <b>Channel ID:</b> {channel_id}\n\n"
                f"❓ <b>Poll Question:</b> {html_escape(poll_question[:100])}\n"
                f"✅ <b>Selected Option:</b> {html_escape(option_text[:50])}\n\n"
                f"📊 <b>Total Votes:</b> {quantity}\n"
                f"✅ <b>Delivered:</b> {delivered}\n"
                f"⏳ <b>Remaining:</b> {remaining}\n"
                f"💵 <b>Cost:</b> ${charge:.4f}\n"
            )

            # Buttons for poll votes (with delay and cancel)
            vote_delay_sec = order.get("custom_delay_seconds", 10)
            text += f"⏱️ <b>Delivery Delay:</b> {vote_delay_sec}s\n"
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text="⏱️ Delay Adjustment", callback_data=f"vote_delay:{order['_id']}")
            )
            builder.row(
                InlineKeyboardButton(text="❌ Cancel Order", callback_data=f"cancel_order:{order['_id']}")
            )
            builder.row(
                InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_menu")
            )
        else:
            text = (
                f"📺 <b>Your Channels:</b>\n\n"
                f"<b>Channel Status :</b> 🟢 ON\n\n"
                f"🟢 <b>Active Session:</b> {user_id}\n"
                f"🆔 <b>Channel ID:</b> {channel_id}\n"
                f"📛 <b>Channel Name:</b> {channel_title}\n"
                f"🔗 <b>Channel Username:</b> @{channel_title}\n"
                f"👀 <b>{per_post_label} per Post:</b> {per_post}\n"
                f"📝 <b>Posts per Day:</b> {posts_per_day}\n"
                f"🔀 <b>Random Views:</b> {random_views}\n"
                f"🔔 <b>Mute/Unmute:</b> {mute_str}\n"
                f"📅 <b>Number of Days Left:</b> {days_remaining}\n"
                f"⏳ <b>Delay:</b> {total_delay_sec}s{adj_str} (Today {daily_quantity} {per_post_label.lower()} in {daily_eta})\n"
                f"📉 <b>High ➔ Low (Descending Views):</b> {high_low_str}\n"
                f"🌙 <b>Night Mode:</b> {night_str}"
                f"{night_note}"
            )

            markup = get_order_control_markup(order, is_paused, high_low_str, night_str)

        if order["service_identifier"] == "poll_votes":
            await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=markup)

    await message.answer("Main Menu", reply_markup=get_main_menu_keyboard())

@dp.message(F.text == "👤 My Account")
async def account_handler(message: types.Message):
    user = await users_collection.find_one({"user_id": message.from_user.id})
    if not user:
        user = await get_or_create_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name
        )
    balance = user.get('balance', 0)

    # Get active orders
    active_orders = await orders_collection.find({
        "user_id": message.from_user.id,
        "status": {"$in": ["pending", "processing", "partial"]}
    }).to_list(None)

    response = (
        f"👤 User: `{message.from_user.first_name}`\n"
        f"🆔 User ID: `{message.from_user.id}`\n\n"
        f"💰 Balance: `${user.get('balance', 0):.2f}` (`{await usd_to_inr_converter(balance)}`)\n\n"
    )

    if active_orders:
        response += "📦 Active Orders:\n"
        for i, order in enumerate(active_orders, 1):
            if order['service_identifier'] in ['manual_views', 'views_by_followers']:
                delivered = order.get('delivered_views', 0)
                total = order.get('quantity') or order.get('total_views', 0)
                service = "Views" if order['service_identifier'] == 'manual_views' else "Auto Views"
            elif order['service_identifier'] in ['manual_reactions', 'reactions_by_followers']:
                delivered = order.get('delivered_reactions', 0)
                total = order.get('quantity') or order.get('total_reactions', 0)
                service = "Reactions" if order['service_identifier'] == 'manual_reactions' else "Auto Reactions"
            else:
                delivered = order.get('delivered_quantity', 0)
                total = order.get('quantity', 0)
                service = "Votes"

            response += (
                f"{i}. {service} - {delivered}/{total} ({int(delivered/total*100)}%)\n"
                f"   Status: {order['status']}\n"
            )
    else:
        response += "📦 No active orders"

    await message.answer(
        response,
        parse_mode='markdown',
        reply_markup=contact_button()
    )

# ===== HANDLERS ===== #
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

    await message.answer(
        "Welcome! Please select a service:",
        reply_markup=get_main_menu_keyboard()
    )

# ===== BUY TELEGRAM ACCOUNTS - ENTRY POINT ===== #
@dp.message(F.text == "🛒 Buy Telegram Accounts")
async def buy_accounts_entry(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        await accounts_shop.acct_show_main_menu(message)
    except Exception as e:
        logger.error(f"[AcctShop] acct_show_main_menu error: {e}", exc_info=True)
        await message.answer("⚠️ Failed to open the shop. Please try again.", reply_markup=get_main_menu_keyboard())


@dp.message(F.text == "⬅️ Back to Main Menu")
async def back_to_main_from_accounts(message: types.Message, state: FSMContext):
    await state.clear()
    accounts_shop.acct_clear_state(message.from_user.id)
    await message.answer("🏠 Main Menu", reply_markup=get_main_menu_keyboard())


@dp.message(Command("acctreply"))
async def acct_admin_reply(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            raise ValueError()
        _, uid_str, reply_text = parts
        target_uid = int(uid_str)
        await bot.send_message(target_uid, f"✉️ Admin: {reply_text}")
        await message.answer(f"✅ Replied to {target_uid}")
    except Exception:
        await message.answer("Usage: /acctreply user_id your_message")


@dp.message(Command("acct_admin"))
async def acct_admin_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Unauthorized")
        return
    accounts_shop.acct_clear_state(message.from_user.id)
    await message.answer(
        "🔧 <b>Accounts Shop Admin Panel</b>\n\nSelect an option:",
        reply_markup=accounts_shop.acct_admin_panel_keyboard(),
        parse_mode="HTML"
    )


# ===== ACCOUNTS SHOP - CALLBACK HANDLER ===== #
@dp.callback_query(lambda c: c.data and (
    c.data.startswith("acct_") or
    c.data.startswith("acct_buy_") or
    c.data.startswith("acct_otp_login_") or
    c.data.startswith("acct_session_file_") or
    c.data.startswith("acct_login_done_") or
    c.data.startswith("acct_login_2fa_") or
    c.data.startswith("acct_rate_") or
    c.data.startswith("acct_config_tier_") or
    c.data.startswith("acct_currency_") or
    c.data.startswith("acct_oxapay_check_") or
    c.data.startswith("acct_copy_ref_")
))
async def accounts_shop_callback(callback: types.CallbackQuery):
    await accounts_shop.acct_handle_callback(callback)


# ===== ACCOUNTS SHOP - BUTTON HANDLERS ===== #
@dp.message(F.text == "💳 Buy Telegram Accounts")
async def acct_buy_accounts_btn(message: types.Message):
    await accounts_shop.acct_show_accounts(message)


@dp.message(F.text == "📊 Acct Stats")
async def acct_stats_btn(message: types.Message):
    await accounts_shop.acct_show_stats(message)


@dp.message(F.text == "💱 Acct Deposit")
async def acct_deposit_btn(message: types.Message):
    await accounts_shop.acct_deposit_menu(message)


@dp.message(F.text == "💵 Acct Balance")
async def acct_balance_btn(message: types.Message):
    await accounts_shop.acct_show_balance(message)


@dp.message(F.text == "🎁 Acct Referrals")
async def acct_referrals_btn(message: types.Message):
    await accounts_shop.acct_show_referrals(message)


@dp.message(F.text == "💎 Acct Loyalty")
async def acct_loyalty_btn(message: types.Message):
    await accounts_shop.acct_show_loyalty(message)


@dp.message(F.text == "🛟 Acct Support")
async def acct_support_btn(message: types.Message):
    await accounts_shop.acct_show_support(message)


@dp.message(F.text == "📚 Acct How To Use")
async def acct_how_to_use_btn(message: types.Message):
    await accounts_shop.acct_show_user_manual(message)


# ===== ACCOUNTS SHOP - STATE-BASED TEXT HANDLER ===== #
from aiogram.filters import BaseFilter

class HasAcctStateFilter(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        return accounts_shop.acct_get_state(message.from_user.id) is not None


@dp.message(HasAcctStateFilter(), F.text)
async def accounts_shop_state_text_handler(message: types.Message):
    await accounts_shop.acct_handle_text(message)


@dp.message(HasAcctStateFilter(), F.photo | F.video | F.document)
async def accounts_shop_state_media_handler(message: types.Message):
    await accounts_shop.acct_handle_media(message)


@dp.message(F.text == "⬅️ Back")
async def back_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🔹Returning to main menu:",
        reply_markup=get_main_menu_keyboard()
    )

@dp.message(F.text == "⬅️ Cancel Order")
async def cancel_order(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Order cancelled",
        reply_markup=get_main_menu_keyboard()
    )

@dp.message(F.text == "📝📊 Manual Views")
async def start_manual_views(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    # Check if user has any connected channels
    user_channels = await get_user_channels(user_id)
    if not user_channels:
        await message.answer(
            "⚠️ You need to connect at least one channel before placing manual view orders.\n\n"
            "Please connect a channel first:",
            reply_markup=get_channel_select_keyboard()
        )
        return

    await state.update_data(service_type="manual_views")
    await state.set_state(OrderStates.waiting_for_content)
    await message.answer(
        "⚠️ Please forward a post from one of your connected channels\n"
        "Or press '⬅️ Cancel Order' to go back",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
            resize_keyboard=True
        )
    )

@dp.message(F.text == "📝❤️‍🔥 Manual Reactions")
async def start_manual_reactions(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    # Check if user has any connected channels
    user_channels = await get_user_channels(user_id)
    if not user_channels:
        await message.answer(
            "⚠️ You need to connect at least one channel before placing manual reaction orders.\n\n"
            "Please connect a channel first:",
            reply_markup=get_channel_select_keyboard()
        )
        return

    await state.update_data(service_type="manual_reactions")
    await state.set_state(OrderStates.waiting_for_content)
    await message.answer(
        "⚠️ Please forward a post from one of your connected channels\n"
        "Or press '⬅️ Cancel Order' to go back",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
            resize_keyboard=True
        )
    )

@dp.message(F.text == "🗳️ Order Votes")
async def start_votes_order(message: types.Message, state: FSMContext):
    """Start vote ordering - Step 1: Share channel/group"""
    await state.clear()
    await state.update_data(service_type="poll_votes")

    await message.answer(
        "🗳️ <b>Order Votes - Step 1</b>\n\n"
        "📢 Select the channel or group where the poll is posted:\n\n"
        "👇 Tap the button below to add channel/group",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text="➕ Add Channel",
                        request_chat=KeyboardButtonRequestChat(
                            request_id=98,
                            chat_is_channel=True,
                            bot_is_member=False
                        )
                    ),
                    KeyboardButton(
                        text="➕ Add Group",
                        request_chat=KeyboardButtonRequestChat(
                            request_id=99,
                            chat_is_channel=False,
                            bot_is_member=False
                        )
                    )
                ],
                [KeyboardButton(text="📢 My Vote Orders")],
                [KeyboardButton(text="⬅️ Cancel Order")]
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(VoteOrderStates.WAITING_FOR_CHANNEL)

# Vote Order Handler - Step 2 alt: My Vote Orders
@dp.message(VoteOrderStates.WAITING_FOR_CHANNEL, F.text == "📢 My Vote Orders")
async def vote_my_orders(message: types.Message, state: FSMContext):
    """Show active vote orders from vote section."""
    user_id = message.from_user.id
    active_orders = await orders_collection.find({
        "user_id": user_id,
        "status": {"$in": ["confirmed", "processing"]},
        "service_identifier": "poll_votes"
    }).to_list(None)

    await state.clear()

    if not active_orders:
        await message.answer(
            "📢 You have no active vote orders.",
            reply_markup=get_main_menu_keyboard()
        )
        return

    await message.answer(f"📢 <b>Your Active Vote Orders ({len(active_orders)}):</b>", parse_mode="HTML")
    for order in active_orders:
        channel_title = order.get("channel_title", "Unknown")
        poll_question = order.get("poll_question", "N/A")
        option_text = order.get("option_text", "N/A")
        quantity = order.get("quantity", 0)
        delivered = order.get("delivered_votes", 0)
        remaining = max(0, quantity - delivered)
        charge = order.get("charge", 0.0)
        vote_delay_sec = order.get("custom_delay_seconds", 10) or 10

        status_emoji = "🟢" if order["status"] == "processing" else "🟡"
        status_text = "Active" if order["status"] == "processing" else "Confirmed"

        text = (
            f"🗳️ <b>Poll Vote Order</b>\n\n"
            f"{status_emoji} <b>Status:</b> {status_text}\n"
            f"📛 <b>Channel/Group:</b> {channel_title}\n\n"
            f"❓ <b>Poll Question:</b> {html_escape(poll_question[:100])}\n"
            f"✅ <b>Selected Option:</b> {html_escape(option_text[:50])}\n\n"
            f"📊 <b>Total Votes:</b> {quantity}\n"
            f"✅ <b>Delivered:</b> {delivered}\n"
            f"⏳ <b>Remaining:</b> {remaining}\n"
            f"⏱️ <b>Delivery Delay:</b> {vote_delay_sec}s\n"
            f"💵 <b>Cost:</b> ${charge:.4f}\n"
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="⏱️ Delay Adjustment", callback_data=f"vote_delay:{order['_id']}")
        )
        builder.row(
            InlineKeyboardButton(text="❌ Cancel Order", callback_data=f"cancel_order:{order['_id']}")
        )
        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

    await message.answer("Main Menu", reply_markup=get_main_menu_keyboard())


# Vote Order Handler - Step 2: Channel Shared
@dp.message(VoteOrderStates.WAITING_FOR_CHANNEL, F.chat_shared)
async def vote_channel_shared(message: types.Message, state: FSMContext):
    """Handle channel/group share for vote orders"""
    chat_info = message.chat_shared
    channel_id = chat_info.chat_id
    channel_title = chat_info.title or "Shared Channel"

    await state.update_data(
        channel_id=channel_id,
        channel_title=channel_title
    )

    await message.answer(
        f"✅ <b>Channel Selected:</b> {html_escape(channel_title)}\n\n"
        "🔗 <b>Step 2:</b> Please send the <b>public link or invite link</b> of this channel/group\n\n"
        "📝 Example:\n"
        "• https://t.me/yourchannel\n"
        "• https://t.me/+xxxxx (private link)",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
            resize_keyboard=True
        )
    )
    await state.set_state(VoteOrderStates.WAITING_FOR_INVITE_LINK)

# Vote Order Handler - Step 3: Invite Link Received
@dp.message(VoteOrderStates.WAITING_FOR_INVITE_LINK, F.text)
async def vote_invite_link_received(message: types.Message, state: FSMContext):
    """Handle invite link and request post forward"""
    if message.text == "⬅️ Cancel Order":
        await state.clear()
        await message.answer(
            "❌ Order cancelled",
            reply_markup=get_main_menu_keyboard()
        )
        return

    invite_link = message.text.strip()

    # Validate link format
    if not any(x in invite_link.lower() for x in ['t.me/', 'telegram.me/', 'telegram.dog/']):
        await message.answer(
            "❌ Invalid link format.\n\n"
            "Please send a valid Telegram link:\n"
            "• https://t.me/yourchannel\n"
            "• https://t.me/+xxxxx"
        )
        return

    await state.update_data(invite_link=invite_link)

    data = await state.get_data()
    channel_title = data.get('channel_title', 'Channel')

    await message.answer(
        f"✅ <b>Link Saved</b>\n\n"
        f"📢 <b>Channel/Group:</b> {html_escape(channel_title)}\n"
        f"🔗 <b>Link:</b> {html_escape(invite_link)}\n\n"
        "📮 <b>Step 3:</b> Please <b>forward the poll post</b> from your channel or group\n\n"
        "⚠️ Make sure the forwarded message contains a poll!",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
            resize_keyboard=True
        )
    )
    await state.set_state(VoteOrderStates.WAITING_FOR_POST_FORWARD)

# Vote Order Handler - Step 4: Post Forwarded with Poll
@dp.message(VoteOrderStates.WAITING_FOR_POST_FORWARD, F.forward_from_chat)
async def vote_post_forwarded(message: types.Message, state: FSMContext):
    """Handle forwarded post with poll detection"""
    if not message.poll:
        await message.answer(
            "❌ This post doesn't contain a poll!\n\n"
            "Please forward a post that has a poll in it.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
                resize_keyboard=True
            )
        )
        return

    # Extract poll information
    poll = message.poll
    poll_options = [opt.text for opt in poll.options]
    content_id = message.forward_from_message_id if message.forward_from_message_id else message.message_id

    await state.update_data(
        poll_id=poll.id,
        poll_question=poll.question,
        poll_options=poll_options,
        content_id=content_id
    )

    # Show poll options for selection
    builder = ReplyKeyboardBuilder()
    for i, option in enumerate(poll_options):
        option_display = option[:20] + "..." if len(option) > 20 else option
        builder.add(KeyboardButton(text=f"{i+1}. {option_display}"))
    builder.adjust(1)  # One option per row
    builder.row(KeyboardButton(text="⬅️ Cancel Order"))

    await message.answer(
        f"✅ <b>Poll Detected!</b>\n\n"
        f"❓ <b>Question:</b> {html_escape(poll.question)}\n\n"
        f"📊 <b>Available Options ({len(poll_options)}):</b>\n"
        + "\n".join([f"{i+1}. {html_escape(opt)}" for i, opt in enumerate(poll_options)]) +
        "\n\n👇 <b>Select which option you want to boost:</b>",
        parse_mode="HTML",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(VoteOrderStates.WAITING_FOR_OPTION_SELECTION)

# Vote Order Handler - Step 5: Option Selected
@dp.message(VoteOrderStates.WAITING_FOR_OPTION_SELECTION, F.text)
async def vote_option_selected(message: types.Message, state: FSMContext):
    """Handle poll option selection"""
    if message.text == "⬅️ Cancel Order":
        await state.clear()
        await message.answer(
            "❌ Order cancelled",
            reply_markup=get_main_menu_keyboard()
        )
        return

    data = await state.get_data()
    poll_options = data.get('poll_options', [])

    # Parse selected option number
    try:
        # Extract number from format "1. Option Text"
        option_num = int(message.text.split(".")[0].strip())
        if option_num < 1 or option_num > len(poll_options):
            raise ValueError("Invalid option number")

        selected_option_index = option_num - 1
        selected_option_text = poll_options[selected_option_index]

    except:
        await message.answer(
            "❌ Invalid selection!\n\n"
            "Please select an option from the keyboard below:",
            reply_markup=message.reply_markup
        )
        return

    await state.update_data(
        option_index=selected_option_index,
        option_text=selected_option_text
    )

    # Build dynamic vote quantity buttons based on active accounts
    max_votes = max(1, len(get_active_clients()))
    if max_votes > 3:
        mid1 = random.randint(1, max(1, max_votes // 2))
        mid2 = random.randint(max(mid1 + 1, max_votes // 2), max_votes - 1)
        vote_btns = [1, mid1, mid2, max_votes]
    elif max_votes > 1:
        vote_btns = [1, max_votes]
    else:
        vote_btns = [1]

    vote_keyboard = []
    row = []
    for v in vote_btns:
        row.append(KeyboardButton(text=str(v)))
        if len(row) == 2:
            vote_keyboard.append(row)
            row = []
    if row:
        vote_keyboard.append(row)
    vote_keyboard.append([KeyboardButton(text="⬅️ Cancel Order")])

    await message.answer(
        f"✅ <b>Option Selected:</b>\n{html_escape(selected_option_text)}\n\n"
        "🔢 <b>Step 5:</b> How many votes do you want?\n\n"
        f"💡 Minimum: 1 | Maximum: {max_votes} (based on active accounts)\n"
        "You can also type a custom number:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=vote_keyboard,
            resize_keyboard=True
        )
    )
    await state.set_state(VoteOrderStates.WAITING_FOR_QUANTITY)

# Vote Order Handler - Step 6: Quantity Entered
@dp.message(VoteOrderStates.WAITING_FOR_QUANTITY, F.text)
async def vote_quantity_entered(message: types.Message, state: FSMContext):
    """Handle vote quantity and show confirmation"""
    if message.text == "⬅️ Cancel Order":
        await state.clear()
        await message.answer(
            "❌ Order cancelled",
            reply_markup=get_main_menu_keyboard()
        )
        return

    try:
        quantity = int(message.text)
        if quantity < 1:
            await message.answer(
                "❌ Minimum order is 1 vote!\n\n"
                "Please enter a valid quantity:"
            )
            return

        max_allowed = max(1, len(get_active_clients()))
        if quantity > max_allowed:
            await message.answer(
                f"❌ Maximum order is {max_allowed} votes (based on active accounts)!\n\n"
                "Please enter a valid quantity:"
            )
            return

    except ValueError:
        await message.answer(
            "❌ Invalid number!\n\n"
            "Please enter a valid quantity (e.g., 100, 500, 1000):"
        )
        return

    # Calculate price
    pricing = await get_pricing()
    price_per_vote = pricing.get('poll_votes', 0.03)
    total_price = round(quantity * price_per_vote, 2)

    data = await state.get_data()
    channel_title = data.get('channel_title', 'Channel')
    poll_question = data.get('poll_question', 'Poll')
    option_text = data.get('option_text', 'Option')

    await state.update_data(
        quantity=quantity,
        total_price=total_price
    )

    # Get user balance
    user = await get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )
    balance = user.get('balance', 0)

    # Show confirmation
    confirmation_msg = (
        "📋 <b>Order Summary</b>\n\n"
        f"📢 <b>Channel:</b> {html_escape(channel_title)}\n"
        f"❓ <b>Poll:</b> {html_escape(poll_question[:50])}\n"
        f"✅ <b>Option:</b> {html_escape(option_text[:30])}\n"
        f"🗳️ <b>Votes:</b> {quantity}\n"
        f"💰 <b>Total Cost:</b> ${total_price}\n"
        f"💳 <b>Your Balance:</b> ${balance:.2f}\n\n"
    )

    if balance >= total_price:
        confirmation_msg += "✅ Sufficient balance! Confirm to proceed."
        keyboard = [
            [KeyboardButton(text="✅ Confirm Order")],
            [KeyboardButton(text="⬅️ Cancel Order")]
        ]
    else:
        needed = total_price - balance
        confirmation_msg += f"❌ Insufficient balance!\n💵 You need ${needed:.2f} more.\n\n" \
                          "Please add balance first."
        keyboard = [
            [KeyboardButton(text="💳 Add Balance")],
            [KeyboardButton(text="⬅️ Cancel Order")]
        ]

    await message.answer(
        confirmation_msg,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=keyboard,
            resize_keyboard=True
        )
    )
    await state.set_state(VoteOrderStates.WAITING_FOR_CONFIRMATION)

# Vote Order Handler - Step 7: Confirmation
@dp.message(VoteOrderStates.WAITING_FOR_CONFIRMATION, F.text)
async def vote_order_confirmed(message: types.Message, state: FSMContext):
    """Handle final vote order confirmation and processing"""
    if message.text == "⬅️ Cancel Order":
        await state.clear()
        await message.answer(
            "❌ Order cancelled",
            reply_markup=get_main_menu_keyboard()
        )
        return

    if message.text == "💳 Add Balance":
        await state.clear()
        await message.answer(
            "💳 <b>Add Balance</b>\n\nSelect payment method:",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard()
        )
        # Trigger add balance flow
        await message.answer("Please use '💳 Add Balance' from main menu")
        return

    if message.text != "✅ Confirm Order":
        return

    data = await state.get_data()
    user_id = message.from_user.id

    # FIX: Delete the confirmation message to prevent double-click
    try:
        await message.delete()
    except Exception:
        pass  # Ignore if message already deleted

    # Get all required data
    channel_id = data.get('channel_id')
    channel_title = data.get('channel_title')
    invite_link = data.get('invite_link')
    poll_id = data.get('poll_id')
    poll_question = data.get('poll_question')
    option_index = data.get('option_index')
    option_text = data.get('option_text')
    content_id = data.get('content_id')
    quantity = data.get('quantity')
    total_price = data.get('total_price')
    poll_options = data.get('poll_options', [])

    # Check balance again
    user = await get_or_create_user(user_id, message.from_user.username, message.from_user.first_name)
    balance = user.get('balance', 0)

    if balance < total_price:
        await message.answer(
            "❌ Insufficient balance!\n\n"
            "Please add balance first.",
            reply_markup=get_main_menu_keyboard()
        )
        await state.clear()
        return

    # Deduct balance
    await update_user_balance(user_id, -total_price)

    # NOW join all clients to channel AFTER payment confirmation
    processing_msg = await message.answer(
        "⏳ Processing your order...\n"
        "Clients are joining the channel...",
        reply_markup=get_main_menu_keyboard()
    )

    # Join all clients to channel
    if get_active_clients() and invite_link:
        await join_all_clients_to_channel(channel_id, invite_link)

    # Create order in database
    order_id = await create_order(
        user_id=user_id,
        service_identifier="poll_votes",
        service_type="manual",
        channel_id=channel_id,
        channel_title=channel_title,
        content_id=content_id,
        quantity=quantity,
        charge=total_price,
        status="confirmed",
        poll_id=poll_id,
        poll_question=poll_question,
        option_index=option_index,
        option_text=option_text
    )

    # Store invite link for joining
    await add_user_channel(
        user_id=user_id,
        channel_id=channel_id,
        channel_title=channel_title,
        is_public='t.me/+' not in invite_link and 'joinchat' not in invite_link,
        invite_link=invite_link
    )

    # FIX: Delete processing message and send new confirmation
    try:
        await processing_msg.delete()
    except Exception:
        pass

    await message.answer(
        "✅ <b>Order Confirmed!</b>\n\n"
        f"🗳️ <b>Votes:</b> {quantity}\n"
        f"💰 <b>Charged:</b> ${total_price}\n"
        f"💳 <b>New Balance:</b> ${balance - total_price:.2f}\n\n"
        "📊 Your order is now being processed!\n"
        "⏱️ Delivery will start shortly using master-worker method.\n\n"
        "Check '📢 My Channels' to monitor progress.",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard()
    )

    await state.clear()

    # Start vote delivery in background
    asyncio.create_task(deliver_votes_master_worker(order_id))

@dp.message(OrderStates.waiting_for_content, F.media_group_id)
async def handle_album(message: types.Message):
    """Handle album posts by ignoring them and instructing the user"""
    await message.answer(
        "⚠️ Albums are not supported. Please send a single post at a time.\n"
        "Press '⬅️ Cancel Order' and try again with a single post.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
            resize_keyboard=True
        )
    )


@dp.message(OrderStates.waiting_for_content)
async def process_regular_post(message: types.Message, state: FSMContext):
    """Handle non-forwarded messages or polls sent directly"""
    if message.poll:
        # It's a poll sent directly (not forwarded)
        data = await state.get_data()
        if data.get('service_type') == 'poll_votes':
            # Use the poll information directly from the message
            try:
                poll_options = [opt.text for opt in message.poll.options]
                await state.update_data(
                    channel_id=message.chat.id,
                    poll_id=message.poll.id,
                    poll_question=message.poll.question,
                    poll_options=poll_options,
                    content_id=message.message_id
                )

                builder = ReplyKeyboardBuilder()
                for i, option in enumerate(poll_options, start=1):
                    builder.add(KeyboardButton(text=f"{i}️⃣ {option[:15]}"))
                builder.adjust(2)
                builder.row(KeyboardButton(text="⬅️ Cancel Order"))

                await message.answer(
                    f"🗳️ Selected: <b>{message.poll.question[:50]}</b>\n"
                    "👉 Which option do you want to boost?",
                    reply_markup=builder.as_markup(resize_keyboard=True),
                    parse_mode="HTML"
                )
                await state.set_state(OrderStates.waiting_for_option)
                return
            except Exception as e:
                print(f"Error processing direct poll: {e}")
                await message.answer("❌ Error processing poll. Please ensure the bot has access to this poll.")
                return

    # If it's not a poll or not the right service
    await message.answer(
        "Please send the post properly as mentioned!",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
            resize_keyboard=True
        )
    )

@dp.message(OrderStates.waiting_for_content, F.forward_from_chat)
async def process_forwarded_post(message: types.Message, state: FSMContext):
    # Check if this is part of an album
    if message.media_group_id:
        return

    data = await state.get_data()
    user_id = message.from_user.id
    channel_id = message.forward_from_chat.id

    try:
        # Get channel info using Telethon
        client = get_active_clients()[0] if get_active_clients() else None
        if not client:
            await message.answer("❌ No active Telegram clients available. Please contact admin.")
            return

        try:
            # Get channel entity with Telethon
            entity = await client.get_entity(PeerChannel(channel_id))
            channel_title = entity.title
            is_public = hasattr(entity, 'username') and entity.username is not None

            # Get more details if possible
            try:
                full_channel = await client(GetFullChannelRequest(channel=entity))
                is_member = full_channel.full_chat.participants_count > 0
            except:
                is_member = True  # Assume member if we can't check

            print(f"Telethon channel info: {channel_title}, public: {is_public}, member: {is_member}")

        except Exception as e:
            print(f"Error fetching channel info with Telethon: {e}")
            # Fallback to aiogram
            try:
                channel_info = await bot.get_chat(channel_id)
                channel_title = channel_info.title
                is_public = hasattr(channel_info, 'username') and channel_info.username is not None
            except Exception as e:
                print(f"Error fetching channel info: {e}")
                await message.answer("❌ Could not fetch channel information. Please try again.")
                return

        # For manual services, verify channel connection
        if data['service_type'] in ['manual_views', 'manual_reactions']:
            is_connected = await channels_collection.find_one({
                "user_id": user_id,
                "channel_id": channel_id
            })

            if not is_connected:
                await message.answer(
                    f"❌ Channel is not connected to your account!\n\n"
                    "Please connect this channel first:",
                    reply_markup=get_channel_select_keyboard()
                )
                return

        # Update state with channel info
        await state.update_data(
            channel_id=channel_id,
            channel_title=channel_title,
            content_id=message.forward_from_message_id
        )

        # Handle different service types
        if data['service_type'] == 'manual_reactions':
            await message.answer(
                "❤️ Choose a reaction type:",
                reply_markup=get_emoji_keyboard()
            )
            await state.set_state(OrderStates.waiting_for_emoji)

        elif data['service_type'] == 'manual_views':
            await message.answer(
                "👀 Selected post\n"
                "🔢 How many views would you like?\n"
                "Or press '⬅️ Cancel Order' to go back",
                reply_markup=get_quantity_keyboard()
            )
            await state.set_state(OrderStates.waiting_for_quantity)

        elif data['service_type'] == 'poll_votes':
            try:
                # For polls, we handle both forwarded and direct
                # First try to use the message as is if it's a poll
                target_poll = message.poll
                target_chat_id = message.chat.id
                target_message_id = message.message_id

                if not target_poll:
                    # If it's not a poll but forwarded, it might be a forwarded poll
                    if message.forward_from_chat or message.forward_from:
                        try:
                            # Try forwarding to internal channel as fallback/verification
                            destination_chat_id = -1002859595679
                            forwarded = await message.forward(chat_id=destination_chat_id)
                            if forwarded.poll:
                                target_poll = forwarded.poll
                                target_chat_id = destination_chat_id
                                target_message_id = forwarded.message_id
                        except Exception as forward_err:
                            print(f"Forwarding failed: {forward_err}")

                if not target_poll:
                    await message.answer("❌ This message doesn't contain a poll. Please forward the specific poll message.")
                    return

                poll_options = [opt.text for opt in target_poll.options]

                await state.update_data(
                    poll_id=target_poll.id,
                    poll_question=target_poll.question,
                    poll_options=poll_options,
                    content_id=target_message_id
                )

                builder = ReplyKeyboardBuilder()
                for i, option in enumerate(poll_options, start=1):
                    builder.add(KeyboardButton(text=f"{i}️⃣ {option[:15]}"))
                builder.adjust(2)
                builder.row(KeyboardButton(text="⬅️ Cancel Order"))

                await message.answer(
                    f"🗳️ Selected Poll: <b>{target_poll.question}</b>\n\n"
                    "👉 <b>Step 4: Select Option</b>\n"
                    "Which option do you want to boost?",
                    reply_markup=builder.as_markup(resize_keyboard=True),
                    parse_mode="HTML"
                )
                await state.set_state(OrderStates.waiting_for_option)

            except Exception as e:
                print(f"Error in poll handler: {e}")
                await message.answer("❌ Failed to process poll. Please ensure you forwarded the correct poll message.")


    except Exception as e:
        print(f"Error processing forwarded post: {e}")
        import traceback
        traceback.print_exc()
        await message.answer(
            "Please send the post properly as mentioned!",
            reply_markup=get_main_menu_keyboard()
        )
        await state.clear()

@dp.message(OrderStates.waiting_for_option, F.text.regexp(r'^\d+️⃣'))
async def process_option_selection(message: types.Message, state: FSMContext):
    # Extract option number from button text (e.g., "1️⃣ Option Text")
    option_index = int(message.text.split('️⃣')[0]) - 1

    data = await state.get_data()
    options = data.get('poll_options', [])

    # Validate selection
    if option_index < 0 or option_index >= len(options):
        await message.answer("Invalid option selection. Please choose from the buttons.")
        return

    selected_option = options[option_index]
    await state.update_data(selected_option=selected_option, option_index=option_index)

    # NEW: Ask user if they want specific or random votes
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎯 Specific Option (Selected)")],
            [KeyboardButton(text="🎲 Random Votes (All Options)")],
            [KeyboardButton(text="⬅️ Cancel Order")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        f"✅ Selected: {selected_option}\n\n"
        "🗳️ Vote Delivery Type:\n"
        "• 🎯 Specific - All votes on selected option\n"
        "• 🎲 Random - Votes randomly distributed on all options\n\n"
        "Choose delivery type:",
        reply_markup=keyboard
    )
    await state.set_state(OrderStates.waiting_for_vote_type)


@dp.message(OrderStates.waiting_for_vote_type)
async def process_vote_type(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel Order":
        await cancel_order(message, state)
        return

    data = await state.get_data()

    if message.text == "🎯 Specific Option (Selected)":
        # Keep the selected option_index as is
        vote_mode = "specific"
        await state.update_data(vote_mode=vote_mode)

        await message.answer(
            f"✅ Specific votes on: {data.get('selected_option')}\n"
            "🔢 How many votes would you like?\n"
            "Or press '⬅️ Cancel Order' to go back",
            reply_markup=get_quantity_keyboard()
        )
        await state.set_state(OrderStates.waiting_for_quantity)

    elif message.text == "🎲 Random Votes (All Options)":
        # Set option_index to "random" for random distribution
        await state.update_data(vote_mode="random", option_index="random", selected_option="Random (All Options)")

        await message.answer(
            "✅ Random votes on all poll options\n"
            "🔢 How many votes would you like?\n"
            "Or press '⬅️ Cancel Order' to go back",
            reply_markup=get_quantity_keyboard()
        )
        await state.set_state(OrderStates.waiting_for_quantity)
    else:
        await message.answer("❌ Please choose from the keyboard buttons.")
        return


@dp.message(OrderStates.waiting_for_emoji)
async def process_emoji_selection(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel Order":
        await cancel_order(message, state)
        return

    if message.text == "❤️ Positive":
        # reaction_list = ["❤️", "🔥", "👍", "👏", "🎉", "🤩", "😍"]
        # reaction = random.choice(reaction_list)
        await state.update_data(reaction_emoji=message.text)
        await message.answer(
            f"Selected reaction: {message.text}\n"
            "🔢 How many reactions would you like?\n"
            "Or press '⬅️ Cancel Order' to go back",
            reply_markup=get_quantity_keyboard()
        )
        await state.set_state(OrderStates.waiting_for_quantity)

    elif message.text == "😂 Negative":
        # reaction_list = ["👎", "🤬", "🤮", "💩", "🤡", "🥱", "🌭", "🤣", "🍌", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈", "💅", "🤪", "👾", "🤷‍♂", "🤷", "🤷‍♀", "😡"]
        # reaction = random.choice(reaction_list)
        await state.update_data(reaction_emoji=message.text)
        await message.answer(
            f"Selected reaction: {message.text}\n"
            "🔢 How many reactions would you like?\n"
            "Or press '⬅️ Cancel Order' to go back",
            reply_markup=get_quantity_keyboard()
        )
        await state.set_state(OrderStates.waiting_for_quantity)

    elif message.text == "🤗 Custom":
        await message.answer(
            "🤗 Select a custom reaction:",
            reply_markup=get_custom_emoji_keyboard()
        )
        await state.set_state(OrderStates.waiting_for_custom_emoji)

    else:
        await message.answer(
            "❌ Please select an option from the keyboard",
            reply_markup=get_emoji_keyboard()
        )


# Handle custom emoji selection
@dp.message(OrderStates.waiting_for_custom_emoji)
async def process_custom_emoji(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Back":
        await message.answer(
            "❤️ Choose a reaction type:",
            reply_markup=get_emoji_keyboard()
        )
        await state.set_state(OrderStates.waiting_for_emoji)
        return

    # Validate it's a single emoji
    if len(message.text) > 2:
        await message.answer(
            "❌ Please select a single emoji from the keyboard",
            reply_markup=get_custom_emoji_keyboard()
        )
        return

    await state.update_data(reaction_emoji=message.text)
    await message.answer(
        f"✅ Selected custom reaction: {message.text}\n"
        "🔢 How many reactions would you like?\n"
        "Or press '⬅️ Cancel Order' to go back",
        reply_markup=get_quantity_keyboard()
    )
    await state.set_state(OrderStates.waiting_for_quantity)

@dp.message(OrderStates.waiting_for_quantity, F.text.regexp(r'^\d+$'))
async def process_quantity(message: types.Message, state: FSMContext):
    data = await state.get_data()
    quantity = int(message.text)
    await state.set_state(OrderStates.waiting_for_confirmation)
    await state.update_data(quantity=quantity)

    # Calculate charge based on service type using pricing from DB
    pricing = await get_pricing()

    if data['service_type'] == 'manual_reactions':
        charge = quantity * pricing['manual_reactions']
    elif data['service_type'] == 'manual_views':
        charge = quantity * pricing['manual_views']
    elif data['service_type'] == 'poll_votes':
        charge = quantity * pricing['poll_votes']

    # Check balance before confirmation
    user_id = message.from_user.id
    user = await users_collection.find_one({"user_id": user_id})
    if user.get('balance', 0) < charge:
        await message.answer(
            f"❌ Insufficient balance to place this order.\n"
            f"💰 Required: ${charge:.4f}\n"
            f"👤 Your Balance: ${user.get('balance', 0):.4f}\n\n"
            f"Please add funds to your account.",
            reply_markup=get_main_menu_keyboard()
        )
        await state.clear()
        return

    await state.update_data(charge=charge)

    if data['service_type'] == 'manual_reactions':
        response = (
            f"✅ <b>Reaction Order Confirmation</b>\n\n"
            f"🎉 <b>Quantity:</b> <code>{quantity} {data.get('reaction_emoji', '❤️')}</code>\n"
            f"📌 <b>Channel:</b> <i>{data['channel_title']}</i>\n"
            f"💵 <b>Total Cost:</b> <code>${charge:.4f}</code>\n\n"
            f"Confirm this order?"
        )
    elif data['service_type'] == 'manual_views':
        response = (
            f"✅ <b>View Order Confirmation</b>\n\n"
            f"👁️ <b>Quantity:</b> <code>{quantity} views</code>\n"
            f"📌 <b>Channel:</b> <i>{data['channel_title']}</i>\n"
            f"💵 <b>Total Cost:</b> <code>${charge:.4f}</code>\n\n"
            f"Confirm this order?"
        )
    else:  # poll_votes
        response = (
            f"🗳️ <b>Vote Order Confirmation</b>\n\n"
            f"📌 <b>Poll:</b> <i>{data['poll_question'][:50]}...</i>\n"
            f"👉 <b>Option:</b> <code>{data.get('selected_option', '')}</code>\n"
            f"🗳️ <b>Votes:</b> <code>{quantity}</code>\n"
            f"💵 <b>Total Cost:</b> <code>${charge:.4f}</code> ({await usd_to_inr_converter(charge)})\n\n"
            f"Do you want to confirm this order?"
        )

    await message.answer(
        response,
        parse_mode="HTML",
        reply_markup=get_confirmation_keyboard()
    )

@dp.message(OrderStates.waiting_for_confirmation, F.text == "✅ Yes")
async def process_confirmation(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id

    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        await message.answer("User not found. Please start again.")
        await state.clear()
        return

    charge = data.get('charge', 0)
    if user.get('balance', 0) < charge:
        await message.answer(
            "❌ Insufficient balance. Please add funds to your account.",
            reply_markup=get_main_menu_keyboard()
        )
        await state.clear()
        return

    service_identifier = data['service_type']

    order_id = await create_order(
        user_id=user_id,
        service_identifier=service_identifier,
        service_type=data['service_type'],
        channel_id=data.get('channel_id'),
        channel_title=data.get('channel_title'),
        content_id=data.get('content_id'),
        quantity=data.get('quantity', 0),
        emoji=data.get('reaction_emoji'),
        charge=charge,
        status='pending',
        poll_id=data.get('poll_id'),
        poll_question=data.get('poll_question'),
        option_index=data.get('option_index'),
        option_text=data.get('selected_option')
    )

    # Store vote mode and poll options for random voting
    if service_identifier == 'poll_votes':
        vote_mode = data.get('vote_mode', 'specific')
        poll_options = data.get('poll_options', [])
        invite_link = data.get('invite_link')

        await orders_collection.update_one(
            {"_id": order_id},
            {"$set": {
                "vote_mode": vote_mode,
                "poll_options": poll_options,
                "poll_options_count": len(poll_options),
                "invite_link": invite_link
            }}
        )

    await update_user_balance(user_id, -charge)

    # FIXED: Join channel with workers ONLY after balance deduction
    if service_identifier == 'poll_votes':
        invite_link = data.get('invite_link')
        if invite_link and get_active_clients():
            asyncio.create_task(join_all_clients_to_channel(data.get('channel_id'), invite_link))

    # --- Styled Confirmation Message ---
    if service_identifier == 'manual_reactions':
        response = (
            f"✅ <b>Reaction Order Confirmed!</b>\n\n"
            f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n"
            f"⚙️ <b>Service:</b> <code>Manual Reactions</code>\n"
            f"🎉 <b>Reactions:</b> <code>{data['quantity']} {data.get('reaction_emoji', '')}</code>\n"
            f"📌 <b>Channel:</b> <i>{data['channel_title']}</i>\n"
            f"💵 <b>Charge:</b> <code>${charge:.4f}</code>\n\n"
            f"🔄 <b>Status:</b> Processing your reaction order..."
        )
    elif service_identifier == 'manual_views':
        response = (
            f"✅ <b>View Order Confirmed!</b>\n\n"
            f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n"
            f"⚙️ <b>Service:</b> <code>Manual Views</code>\n"
            f"👁️ <b>Views:</b> <code>{data['quantity']}</code>\n"
            f"📌 <b>Channel:</b> <i>{data['channel_title']}</i>\n"
            f"💵 <b>Charge:</b> <code>${charge:.4f}</code>\n\n"
            f"🔄 <b>Status:</b> Processing your view order..."
        )
    else:  # poll_votes
        response = (
            f"✅ <b>Vote Order Confirmed!</b>\n\n"
            f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n"
            f"⚙️ <b>Service:</b> <code>Poll Votes</code>\n"
            f"🗳️ <b>Votes:</b> <code>{data['quantity']}</code>\n"
            f"📌 <b>Poll:</b> <i>{data['poll_question'][:50]}...</i>\n"
            f"👉 <b>Selected Option:</b> <code>{data.get('selected_option', '')}</code>\n"
            f"💵 <b>Charge:</b> <code>${charge:.4f}</code>\n\n"
            f"🔄 <b>Status:</b> Processing your vote order..."
        )

    await message.answer(response, parse_mode="HTML", reply_markup=get_main_menu_keyboard())
    await state.clear()


@dp.message(F.text == "📢 My Channels")
async def my_channels_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    channels = await get_user_channels(user_id)

    if not channels:
        await message.answer("You haven't added any channels yet.")
        return

    # Read service_type BEFORE clearing state
    data = await state.get_data()
    service_type = data.get("service_type", "")
    await state.clear()

    # Build the active-order query — filter by service_type when known
    order_query = {
        "user_id": user_id,
        "status": {"$in": ["confirmed", "processing"]}
    }
    if service_type in ("views_by_followers", "reactions_by_followers"):
        order_query["service_identifier"] = service_type

    active_orders = await orders_collection.find(order_query, {"channel_id": 1}).to_list(None)
    active_channel_ids = {o["channel_id"] for o in active_orders}

    # Human-readable service label for the heading
    if service_type == "views_by_followers":
        svc_label = "📊 Views"
    elif service_type == "reactions_by_followers":
        svc_label = "❤️‍🔥 Reactions"
    else:
        svc_label = "All Services"

    await message.answer(
        f"📢 Select a channel to view its <b>{svc_label}</b> orders:",
        parse_mode="HTML",
        reply_markup=get_my_channels_keyboard(channels, active_channel_ids, service_type)
    )


@dp.callback_query(F.data.startswith("my_channel:"))
async def my_channel_select_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    # Format: my_channel:{channel_id} or my_channel:{channel_id}:{service_type}
    parts = callback.data.split(":")
    try:
        channel_id = int(parts[1])
    except (IndexError, ValueError):
        return await callback.answer("❌ Invalid channel selection", show_alert=True)
    service_type = parts[2] if len(parts) >= 3 else ""

    selected_channel = await channels_collection.find_one({
        "user_id": user_id,
        "channel_id": channel_id
    })
    if not selected_channel:
        return await callback.answer("❌ Channel not found", show_alert=True)

    await callback.answer()

    try:
        await callback.message.delete()
    except Exception:
        pass

    await show_channel_order_details(
        chat_id=callback.message.chat.id,
        user_id=user_id,
        channel_id=channel_id,
        channel_title=selected_channel.get("channel_title", "Unknown Channel"),
        service_type=service_type
    )


@dp.callback_query(F.data.startswith("change_speed:"))
async def change_order_speed(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    order_id = parts[1]
    speed_type = parts[2]

    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("❌ Order not found", show_alert=True)

    # Set speed multiplier and name
    if speed_type == "normal":
        speed_multiplier = 1.0
        speed_name = "Normal (5-6 hrs)"
    elif speed_type == "fast":
        speed_multiplier = 1.5
        speed_name = "Fast (3-4 hrs)"
    elif speed_type == "ultra":
        speed_multiplier = 2.0
        speed_name = "Ultra Fast (2-3 hrs)"
    elif speed_type == "slow":
        speed_multiplier = 0.5
        speed_name = "Slow (7-8 hrs)"
    else:
        await callback.answer("❌ Invalid speed selection")
        return

    old_speed = order.get("speed_multiplier", 1.0)
    old_base = get_base_delay_seconds(old_speed)
    current_delay = get_order_delay_seconds(order)
    custom_adjustment = current_delay - old_base
    new_base = get_base_delay_seconds(speed_multiplier)
    new_delay = max(1, new_base + custom_adjustment)

    # Update order speed (NO CHARGE - FREE feature)
    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {
            "speed_multiplier": speed_multiplier,
            "speed_name": speed_name,
            "custom_delay_seconds": new_delay,
            "updated_at": datetime.utcnow()
        }}
    )

    await send_updated_order_message(callback, order_id, f"✅ Speed changed to {speed_name} (FREE)")

@dp.callback_query(F.data.startswith("show_speed_options:"))
async def show_speed_options(callback: types.CallbackQuery):
    order_id = callback.data.split(":")[1]

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🐌 Slow (7-8 hrs)", callback_data=f"change_speed:{order_id}:slow"),
        InlineKeyboardButton(text="🐢 Normal (5-6 hrs)", callback_data=f"change_speed:{order_id}:normal"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text="🚀 Fast (3-4 hrs)", callback_data=f"change_speed:{order_id}:fast"),
        InlineKeyboardButton(text="⚡ Ultra Fast (2-3 hrs)", callback_data=f"change_speed:{order_id}:ultra"),
        width=2
    )
    builder.row(InlineKeyboardButton(text="⬅️ Back", callback_data=f"back_to_order:{order_id}"))

    await callback.message.edit_reply_markup(reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("back_to_order:"))
async def back_to_order(callback: types.CallbackQuery):
    order_id = callback.data.split(":")[1]
    await send_updated_order_message(callback, order_id, "Back")

@dp.callback_query(F.data.startswith("pause:"))
async def pause_campaign(callback: types.CallbackQuery):
    order_id = callback.data.split(":")[1]
    await orders_collection.update_one({"_id": ObjectId(order_id)}, {"$set": {"is_paused": True}})
    await send_updated_order_message(callback, order_id, "⏸ Paused.")

@dp.callback_query(F.data.startswith("resume:"))
async def resume_campaign(callback: types.CallbackQuery):
    order_id = callback.data.split(":")[1]
    await orders_collection.update_one({"_id": ObjectId(order_id)}, {"$set": {"is_paused": False}})
    await send_updated_order_message(callback, order_id, "▶️ Resumed.")

async def send_updated_order_message(callback: types.CallbackQuery, order_id: str, alert_text: str):
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    user_id = order.get("user_id")
    channel_id = order.get("channel_id")
    channel_title = order.get("channel_title", "Unknown")
    posts_per_day = order.get("posts_per_day", 0)
    charge = order.get("charge", 0.0)
    days = order.get("days", 0)
    created = order.get("created_at")
    is_paused = order.get("is_paused", False)
    speed_multiplier = order.get("speed_multiplier", 1.0)

    is_muted = order.get("is_muted", False)
    mute_str = "🔕 ON" if is_muted else "🔔 OFF"
    random_views = order.get("random_views", 0)
    high_low = order.get("high_low_descending", False)
    high_low_str = "🟢 ON" if high_low else "🔴 OFF"
    night_mode = order.get("night_mode_enabled", False)
    night_str = "🌙 ON" if night_mode else "🌙 OFF"

    days_passed = (datetime.utcnow() - created).days
    days_remaining = max(0, days - days_passed)

    if order["service_identifier"] == "views_by_followers":
        service_label = "Auto Follower Views"
        per_post = order.get("views_per_post", 0)
        per_post_label = "Views"
    else:
        service_label = "Auto Follower Reactions"
        per_post = order.get("reactions_per_post", 0)
        per_post_label = "Reactions"

    total_delay_sec = get_order_delay_seconds(order)
    base_delay_sec = get_base_delay_seconds(speed_multiplier)
    custom_adjustment = total_delay_sec - base_delay_sec
    adj_str = f" ({custom_adjustment:+d}s custom)" if custom_adjustment != 0 else ""
    remaining_today_posts, daily_quantity, daily_eta = get_daily_delivery_snapshot(order, per_post)

    # Night mode note
    night_note = ""
    if night_mode:
        night_note = "\n🌙 <i>Night Mode: delivery ÷3 during 11 PM–7 AM IST</i>"

    text = (
        f"📺 <b>Your Channels:</b>\n\n"
        f"<b>Channel Status :</b> 🟢 ON\n\n"
        f"🟢 <b>Active Session:</b> {user_id}\n"
        f"🆔 <b>Channel ID:</b> {channel_id}\n"
        f"📛 <b>Channel Name:</b> {channel_title}\n"
        f"🔗 <b>Channel Username:</b> @{channel_title}\n"
        f"👀 <b>{per_post_label} per Post:</b> {per_post}\n"
        f"📝 <b>Posts per Day:</b> {posts_per_day}\n"
        f"🔀 <b>Random Views:</b> {random_views}\n"
        f"🔔 <b>Mute/Unmute:</b> {mute_str}\n"
        f"📅 <b>Number of Days Left:</b> {days_remaining}\n"
        f"⏳ <b>Delay:</b> {total_delay_sec}s{adj_str} (Today {daily_quantity} {per_post_label.lower()} in {daily_eta})\n"
        f"📉 <b>High ➔ Low (Descending Views):</b> {high_low_str}\n"
        f"🌙 <b>Night Mode:</b> {night_str}"
        f"{night_note}"
    )

    if order["status"] != "completed":
        markup = get_order_control_markup(order, is_paused, high_low_str, night_str)
    else:
        markup = None

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    if alert_text:
        await callback.answer(alert_text)

@dp.callback_query(F.data.startswith("toggle_mute:"))
async def toggle_mute_campaign(callback: types.CallbackQuery):
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    current_mute = order.get("is_muted", False)
    new_mute = not current_mute
    await orders_collection.update_one({"_id": ObjectId(order_id)}, {"$set": {"is_muted": new_mute}})

    mute_label = "🔕 Muted" if new_mute else "🔔 Unmuted"
    await send_updated_order_message(callback, order_id, f"{mute_label}")

@dp.callback_query(F.data.startswith("toggle_highlow:"))
async def toggle_highlow_setting(callback: types.CallbackQuery):
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    current_highlow = order.get("high_low_descending", False)
    await orders_collection.update_one({"_id": ObjectId(order_id)}, {"$set": {"high_low_descending": not current_highlow}})

    await send_updated_order_message(callback, order_id, "High -> Low toggled")

@dp.callback_query(F.data.startswith("speed_menu:"))
async def open_speed_menu(callback: types.CallbackQuery):
    order_id = callback.data.split(":")[1]
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🐌 Slow (7-8 hrs)", callback_data=f"change_speed:{order_id}:slow"),
        InlineKeyboardButton(text="🐢 Normal (5-6 hrs)", callback_data=f"change_speed:{order_id}:normal"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text="🚀 Fast (3-4 hrs)", callback_data=f"change_speed:{order_id}:fast"),
        InlineKeyboardButton(text="⚡ Ultra Fast (2-3 hrs)", callback_data=f"change_speed:{order_id}:ultra"),
        width=2
    )
    builder.row(InlineKeyboardButton(text="⬅️ Back", callback_data=f"back_to_order:{order_id}"))
    await callback.message.edit_reply_markup(reply_markup=builder.as_markup())

# ===== DYNAMIC DELAY ADJUSTMENT =====
@dp.callback_query(F.data.startswith("edit_settings:"))
async def edit_order_settings(callback: types.CallbackQuery):
    """Show delay adjustment options for today's deliverable quantity."""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})

    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    total_delay_seconds = get_order_delay_seconds(order)

    builder = InlineKeyboardBuilder()

    # Simple delay display
    delay_display = f"{total_delay_seconds}s"

    # Determine service type from order
    service_identifier = order.get('service_identifier', '')
    if 'view' in service_identifier.lower():
        service_type = 'views'
        per_post_qty = int(order.get("views_per_post", 0) or 0)
    else:
        service_type = 'reactions'
        per_post_qty = int(order.get("reactions_per_post", 0) or 0)

    remaining_today_posts, daily_quantity, daily_eta = get_daily_delivery_snapshot(order, per_post_qty)

    # Simple -1s and +1s buttons
    builder.row(
        InlineKeyboardButton(text="◀️ -1s", callback_data=f"adjust_seconds:{order_id}:-1"),
        InlineKeyboardButton(text="▶️ +1s", callback_data=f"adjust_seconds:{order_id}:1"),
        width=2
    )

    builder.row(InlineKeyboardButton(text="◀️ Back", callback_data=f"back_to_order:{order_id}"))

    text = (
        f"<b>Delay {delay_display}</b>\n\n"
        f"⏳ Today remaining: {remaining_today_posts} post(s)\n"
        f"📦 You will receive {daily_quantity} {service_type} in {daily_eta}"
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("adjust_seconds:"))
async def adjust_seconds_handler(callback: types.CallbackQuery):
    """Adjust delay by seconds - SIMPLE VERSION"""
    parts = callback.data.split(":")
    order_id = parts[1]
    adjustment = int(parts[2])

    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    current_delay_seconds = order.get("custom_delay_seconds", 19)
    new_delay_seconds = max(1, current_delay_seconds + adjustment)

    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {"custom_delay_seconds": new_delay_seconds, "updated_at": datetime.utcnow()}}
    )

    await callback.answer(f"✅ Delay: {new_delay_seconds}s")
    await edit_order_settings(callback)

@dp.callback_query(F.data == "back_to_menu")
async def back_to_main_menu(callback: types.CallbackQuery):
    """Handle back to main menu callback"""
    await callback.message.delete()
    await callback.message.answer(
        "Welcome! Please select a service:",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer("🏠 Returned to main menu")


@dp.callback_query(F.data.startswith("delay_info:"))
async def delay_info(callback: types.CallbackQuery):
    await callback.answer("ℹ️ Adjust delay using -1s or +1s buttons", show_alert=True)


# ===== VOTE ORDER DELAY ADJUSTMENT =====
@dp.callback_query(F.data.startswith("vote_delay:"))
async def vote_delay_settings(callback: types.CallbackQuery):
    """Show delay adjustment for vote orders (same UI as views/reactions)."""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})

    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    current_delay = order.get("custom_delay_seconds", 10)
    if current_delay is None:
        current_delay = 10

    quantity = order.get("quantity", 0)
    delivered = order.get("delivered_votes", 0)
    remaining = max(0, quantity - delivered)
    clients_count = max(1, get_available_account_count())
    eta = calculate_delivery_time(remaining, current_delay, clients_count)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="◀️ -1s", callback_data=f"vote_adj:{order_id}:-1"),
        InlineKeyboardButton(text="▶️ +1s", callback_data=f"vote_adj:{order_id}:1"),
        width=2
    )
    builder.row(InlineKeyboardButton(text="◀️ Back", callback_data=f"vote_back:{order_id}"))

    text = (
        f"<b>⏱️ Vote Delay: {current_delay}s</b>\n\n"
        f"⏳ Remaining votes: {remaining}\n"
        f"📦 Estimated delivery: {eta}\n\n"
        f"<i>Minimum delay: 10s | No maximum limit</i>"
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()


@dp.callback_query(F.data.startswith("vote_adj:"))
async def vote_adjust_delay(callback: types.CallbackQuery):
    """Adjust vote order delay by seconds."""
    parts = callback.data.split(":")
    order_id = parts[1]
    adjustment = int(parts[2])

    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    current_delay = order.get("custom_delay_seconds", 10)
    if current_delay is None:
        current_delay = 10

    new_delay = max(10, current_delay + adjustment)  # minimum 10s for votes

    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {"custom_delay_seconds": new_delay, "updated_at": datetime.utcnow()}}
    )

    await callback.answer(f"✅ Delay: {new_delay}s")
    # Refresh the delay screen
    order["custom_delay_seconds"] = new_delay
    quantity = order.get("quantity", 0)
    delivered = order.get("delivered_votes", 0)
    remaining = max(0, quantity - delivered)
    clients_count = max(1, get_available_account_count())
    eta = calculate_delivery_time(remaining, new_delay, clients_count)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="◀️ -1s", callback_data=f"vote_adj:{order_id}:-1"),
        InlineKeyboardButton(text="▶️ +1s", callback_data=f"vote_adj:{order_id}:1"),
        width=2
    )
    builder.row(InlineKeyboardButton(text="◀️ Back", callback_data=f"vote_back:{order_id}"))

    text = (
        f"<b>⏱️ Vote Delay: {new_delay}s</b>\n\n"
        f"⏳ Remaining votes: {remaining}\n"
        f"📦 Estimated delivery: {eta}\n\n"
        f"<i>Minimum delay: 10s | No maximum limit</i>"
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("vote_back:"))
async def vote_back_from_delay(callback: types.CallbackQuery):
    """Go back from vote delay screen - delete and show main menu."""
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()
    await bot.send_message(
        callback.message.chat.id,
        "Use '📦 Active Orders' to view your vote orders.",
        reply_markup=get_main_menu_keyboard()
    )


# ===== EDIT ORDER PARAMETERS =====
async def show_edit_interface(message, state: FSMContext, order_id):
    """Display the order editing interface — supports both increase and decrease."""
    data = await state.get_data()
    new_views = data.get("new_views_per_post", 0)
    new_posts = data.get("new_posts_per_day", 0)
    new_days  = data.get("new_days", 0)
    delivered = data.get("delivered_amount", 0)
    service_type  = data.get("service_type")
    metric_label  = "Reactions" if service_type == "reactions_by_followers" else "Views"
    max_limit     = get_per_post_limit()

    # Clamp per-post quantity to system max
    new_views = clamp_per_post_quantity(new_views)
    await state.update_data(new_views_per_post=new_views)

    original_total = data.get("original_total_views", 0)
    new_total      = new_views * new_posts * new_days

    # Floor: cannot reduce below already-delivered units
    min_total = max(1, int(delivered))
    if new_total < min_total:
        # Clamp — try to fix by bumping views_per_post
        new_views = max(1, min_total // max(1, new_posts * new_days))
        new_views = clamp_per_post_quantity(new_views)
        new_total = new_views * new_posts * new_days
        await state.update_data(new_views_per_post=new_views)

    # ── Pricing ───────────────────────────────────────────────────────────
    is_reactions = service_type == "reactions_by_followers"
    if is_reactions:
        original_price = await calculate_reactions_charge(
            data.get("original_views_per_post"),
            data.get("original_posts_per_day"),
            data.get("original_days")
        )
        new_price = await calculate_reactions_charge(new_views, new_posts, new_days)
    else:
        original_price = await calculate_views_charge(
            data.get("original_views_per_post") * data.get("original_posts_per_day") * data.get("original_days"),
            data.get("original_posts_per_day"),
            data.get("original_days")
        )
        new_price = await calculate_views_charge(new_total, new_posts, new_days)

    # Pro-rata refund for any cancelled undelivered units
    if new_total < original_total and original_total > 0:
        units_cancelled = original_total - new_total
        price_per_unit  = original_price / original_total
        refund_amount   = units_cancelled * price_per_unit
        price_diff      = -refund_amount          # negative = money back
        refund_inr      = await usd_to_inr_converter(refund_amount)
        price_text = (
            f"💚 <b>Refund:</b> ${refund_amount:.4f} ({refund_inr})\n"
            f"<i>(Pro-rata for {units_cancelled} undelivered {metric_label})</i>"
        )
    elif new_total > original_total:
        price_diff   = new_price - original_price
        extra_inr    = await usd_to_inr_converter(price_diff)
        price_text   = f"💰 <b>Extra Charge:</b> ${price_diff:.4f} ({extra_inr})"
    else:
        price_diff = 0
        price_text = f"💰 <b>Extra Charge:</b> $0.0000 (No changes)"

    undelivered = max(0, original_total - int(delivered))
    text = (
        f"⚙️ <b>Edit Order Settings</b>\n"
        f"<i>Increase or reduce undelivered quantity.\nAlready delivered cannot be refunded.</i>\n\n"
        f"<b>📊 Max {metric_label} Limit:</b> {max_limit}\n\n"
        f"📊 <b>Total {metric_label}:</b> {new_total}\n"
        f"👀 <b>{metric_label} per Post:</b> {new_views}\n"
        f"📝 <b>Posts Per Day:</b> {new_posts}\n"
        f"📅 <b>No. of Days:</b> {new_days}\n\n"
        f"✅ <b>Already Delivered:</b> {int(delivered)}\n"
        f"⏳ <b>Undelivered:</b> {undelivered}\n\n"
        f"{price_text}"
    )

    # ── Keyboard ──────────────────────────────────────────────────────────
    builder = InlineKeyboardBuilder()

    # Per-post row — label shows per-post value; buttons adjust it (total updates accordingly)
    builder.row(InlineKeyboardButton(
        text=f"👀 {metric_label} per Post: {new_views}  (Total: {new_total})",
        callback_data="info:total"
    ))
    builder.row(
        InlineKeyboardButton(text="-100", callback_data="edit_adjust:views:-100"),
        InlineKeyboardButton(text="-10",  callback_data="edit_adjust:views:-10"),
        InlineKeyboardButton(text="+10",  callback_data="edit_adjust:views:10"),
        InlineKeyboardButton(text="+100", callback_data="edit_adjust:views:100"),
        InlineKeyboardButton(text="+500", callback_data="edit_adjust:views:500"),
        width=5
    )

    # Posts Per Day row
    builder.row(InlineKeyboardButton(text=f"📝 Posts Per Day: {new_posts}", callback_data="info:posts"))
    builder.row(
        InlineKeyboardButton(text="-10", callback_data="edit_adjust:posts:-10"),
        InlineKeyboardButton(text="-1",  callback_data="edit_adjust:posts:-1"),
        InlineKeyboardButton(text="+1",  callback_data="edit_adjust:posts:1"),
        InlineKeyboardButton(text="+10", callback_data="edit_adjust:posts:10"),
        InlineKeyboardButton(text="+50", callback_data="edit_adjust:posts:50"),
        width=5
    )

    # Days row
    builder.row(InlineKeyboardButton(text=f"📅 No. of Days: {new_days}", callback_data="info:days"))
    builder.row(
        InlineKeyboardButton(text="-15", callback_data="edit_adjust:days:-15"),
        InlineKeyboardButton(text="-1",  callback_data="edit_adjust:days:-1"),
        InlineKeyboardButton(text="+1",  callback_data="edit_adjust:days:1"),
        InlineKeyboardButton(text="+15", callback_data="edit_adjust:days:15"),
        InlineKeyboardButton(text="+30", callback_data="edit_adjust:days:30"),
        width=5
    )

    # Confirm button — show for any real change
    if new_total != original_total:
        label = "✅ Confirm Reduction & Refund" if new_total < original_total else "✅ Confirm & Pay Extra"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"confirm_edit:{order_id}"))

    builder.row(InlineKeyboardButton(text="⬅️ Back", callback_data=f"back_to_order:{order_id}"))

    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    except Exception:
        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("edit_order_params:"))
async def edit_order_parameters(callback: types.CallbackQuery, state: FSMContext):
    """Show order parameter editing interface"""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})

    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    # Get current parameters
    service_type = order.get("service_identifier")
    per_post_field = "reactions_per_post" if service_type == "reactions_by_followers" else "views_per_post"
    views_per_post = int(order.get(per_post_field, 0) or 0)
    views_per_post = clamp_per_post_quantity(views_per_post)
    posts_per_day = order.get("posts_per_day", 0)
    days = order.get("days", 0)
    delivered = await get_delivered_amount(order)

    # Calculate current total views
    current_total_views = views_per_post * posts_per_day * days

    # Store in state
    await state.update_data(
        order_id=str(order_id),
        service_type=service_type,
        per_post_field=per_post_field,
        original_views_per_post=views_per_post,
        original_posts_per_day=posts_per_day,
        original_days=days,
        original_total_views=current_total_views,
        delivered_amount=delivered,
        new_views_per_post=views_per_post,
        new_posts_per_day=posts_per_day,
        new_days=days
    )
    await state.set_state(OrderStates.EDITING_ORDER)

    # Show editing interface
    await show_edit_interface(callback.message, state, order_id)
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_adjust:"))
async def edit_adjustment_handler(callback: types.CallbackQuery, state: FSMContext):
    """Handle parameter adjustments with proper limit feedback."""
    parts = callback.data.split(":")
    param_type = parts[1]   # views, posts, days
    adjustment  = int(parts[2])

    data = await state.get_data()
    order_id = data.get("order_id")

    new_views = data.get("new_views_per_post", 0)
    new_posts = data.get("new_posts_per_day", 0)
    new_days  = data.get("new_days", 0)

    if param_type == "views":
        max_limit = get_per_post_limit()
        desired   = new_views + adjustment

        # ── Hard ceiling: cannot exceed active account count ──────────────
        if desired > max_limit:
            await callback.answer(
                f"⚠️ Cannot increase beyond {max_limit} per post.\n"
                f"That is the current number of active accounts available for delivery.\n"
                f"Add more accounts to raise this limit.",
                show_alert=True
            )
            return

        # ── Hard floor: minimum 1 per post ───────────────────────────────
        if desired < 1:
            await callback.answer(
                "⚠️ Minimum is 1 per post. Cannot reduce further.",
                show_alert=True
            )
            return

        new_views = desired
        await state.update_data(new_views_per_post=new_views)

    elif param_type == "posts":
        desired = new_posts + adjustment
        if desired < 1:
            await callback.answer("⚠️ Minimum is 1 post per day.", show_alert=True)
            return
        new_posts = desired
        await state.update_data(new_posts_per_day=new_posts)

    elif param_type == "days":
        desired = new_days + adjustment
        if desired < 1:
            await callback.answer("⚠️ Minimum is 1 day.", show_alert=True)
            return
        new_days = desired
        await state.update_data(new_days=new_days)

    # Refresh the interface with updated values
    await show_edit_interface(callback.message, state, order_id)
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_edit:"))
async def confirm_edit_handler(callback: types.CallbackQuery, state: FSMContext):
    """Confirm and apply order edits — supports both increase (charge) and decrease (refund)."""
    order_id = callback.data.split(":")[1]
    data = await state.get_data()

    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        await state.clear()
        return await callback.answer("❌ Order not found.", show_alert=True)

    new_views      = data.get("new_views_per_post", 0)
    new_posts      = data.get("new_posts_per_day", 0)
    new_days       = data.get("new_days", 0)
    delivered      = int(data.get("delivered_amount", 0))
    per_post_field = data.get("per_post_field", "views_per_post")
    service_type   = data.get("service_type", "")
    metric_label   = "Reactions" if service_type == "reactions_by_followers" else "Views"
    original_total = data.get("original_total_views", 0)
    new_total      = new_views * new_posts * new_days

    # Hard floor: cannot go below already delivered
    if new_total < delivered:
        await callback.answer(
            f"⚠️ Cannot reduce below already delivered {delivered} {metric_label}.",
            show_alert=True
        )
        return

    if new_total == original_total:
        await callback.answer("ℹ️ No changes made.", show_alert=True)
        return

    # ── Price calculation ─────────────────────────────────────────────────
    is_reactions = service_type == "reactions_by_followers"
    if is_reactions:
        original_price = await calculate_reactions_charge(
            data.get("original_views_per_post"),
            data.get("original_posts_per_day"),
            data.get("original_days")
        )
        new_price = await calculate_reactions_charge(new_views, new_posts, new_days)
    else:
        original_price = await calculate_views_charge(
            data.get("original_views_per_post") * data.get("original_posts_per_day") * data.get("original_days"),
            data.get("original_posts_per_day"),
            data.get("original_days")
        )
        new_price = await calculate_views_charge(new_total, new_posts, new_days)

    is_decrease = new_total < original_total

    if is_decrease:
        # Pro-rata refund only for cancelled UNDELIVERED units
        units_cancelled = original_total - new_total
        price_per_unit  = original_price / max(1, original_total)
        refund_amount   = units_cancelled * price_per_unit
        balance_delta   = refund_amount          # positive = added to wallet
        refund_inr      = await usd_to_inr_converter(refund_amount)
        balance_msg     = (
            f"💚 <b>Refund:</b> ${refund_amount:.4f} ({refund_inr}) credited to your wallet\n"
            f"<i>({units_cancelled} cancelled {metric_label} × ${price_per_unit:.6f}/unit)</i>"
        )
    else:
        # Increase — charge extra
        extra_cost    = new_price - original_price
        if extra_cost <= 0:
            await callback.answer("❌ No additional charge calculated.", show_alert=True)
            return
        balance_delta = -extra_cost              # negative = deducted from wallet
        refund_amount = 0
        extra_inr     = await usd_to_inr_converter(extra_cost)
        balance_msg   = f"💳 <b>Charged:</b> ${extra_cost:.4f} ({extra_inr}) deducted from your wallet"

    # ── Balance check for increases ───────────────────────────────────────
    user_id = order.get("user_id")
    user    = await users_collection.find_one({"user_id": user_id})
    current_balance = user.get("balance", 0) if user else 0

    if not is_decrease and current_balance < abs(balance_delta):
        await callback.answer(
            f"❌ Insufficient Balance!\n\n"
            f"Required: ₹{abs(balance_delta):.4f}\n"
            f"Available: ₹{current_balance:.4f}\n"
            f"Short by: ₹{abs(balance_delta) - current_balance:.4f}\n\n"
            f"Please recharge your account.",
            show_alert=True
        )
        await state.clear()
        return

    # ── Apply DB updates ──────────────────────────────────────────────────
    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {
            "$set": {
                per_post_field:   new_views,
                "posts_per_day":  new_posts,
                "days":           new_days,
                "updated_at":     datetime.utcnow()
            }
        }
    )

    await users_collection.update_one(
        {"user_id": user_id},
        {"$inc": {"balance": balance_delta}}
    )

    new_balance = current_balance + balance_delta
    await state.clear()

    new_balance_inr = await usd_to_inr_converter(new_balance)
    action_label = "Reduced & Refunded" if is_decrease else "Upgraded"
    await callback.message.edit_text(
        f"✅ <b>Order {action_label} Successfully!</b>\n\n"
        f"📊 <b>New Settings:</b>\n"
        f"👀 {metric_label} per Post: {new_views}\n"
        f"📝 Posts per Day: {new_posts}\n"
        f"📅 Days: {new_days}\n"
        f"📊 Total {metric_label}: {new_total}\n"
        f"✅ Already Delivered: {delivered}\n"
        f"⏳ Remaining to Deliver: {max(0, new_total - delivered)}\n\n"
        f"{balance_msg}\n"
        f"💰 New Balance: ${new_balance:.4f} ({new_balance_inr})",
        parse_mode="HTML"
    )
    await callback.answer(f"✅ Order {action_label.lower()}!")

    await asyncio.sleep(2)
    await send_updated_order_message(callback, order_id, f"Order {action_label.lower()}")


# ===== AUTO POLL/VOTES FEATURE =====
@dp.callback_query(F.data.startswith("auto_poll:"))
async def auto_poll_votes_menu(callback: types.CallbackQuery):
    """Auto Poll/Votes quantity management"""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})

    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    current_quantity = order.get("auto_poll_votes_quantity", 0)
    auto_poll_enabled = order.get("auto_poll_enabled", False)
    status_text = "🟢 ON" if auto_poll_enabled else "🔴 OFF"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="-100", callback_data=f"poll_qty:{order_id}:-100"),
        InlineKeyboardButton(text="-10", callback_data=f"poll_qty:{order_id}:-10"),
        InlineKeyboardButton(text="-1", callback_data=f"poll_qty:{order_id}:-1"),
        width=3
    )
    builder.row(
        InlineKeyboardButton(text="+1", callback_data=f"poll_qty:{order_id}:+1"),
        InlineKeyboardButton(text="+10", callback_data=f"poll_qty:{order_id}:+10"),
        InlineKeyboardButton(text="+100", callback_data=f"poll_qty:{order_id}:+100"),
        width=3
    )

    toggle_text = "🔴 Turn OFF" if auto_poll_enabled else "🟢 Turn ON"
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data=f"toggle_poll:{order_id}"))
    builder.row(
        InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm_poll:{order_id}"),
        width=1
    )
    builder.row(InlineKeyboardButton(text="⬅️ Back", callback_data=f"back_to_order:{order_id}"))

    text = (
        f"🗳️ <b>Auto Poll/Votes Configuration</b>\n\n"
        f"<b>Status:</b> {status_text}\n"
        f"<b>Quantity:</b> {current_quantity}\n\n"
        f"ℹ️ This feature will randomly send votes to all options in your poll.\n"
        f"The vote speed will match your views speed.\n\n"
        f"Adjust quantity using buttons below:\n"
        f"Click ✅ Confirm to apply changes."
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("poll_qty:"))
async def adjust_poll_quantity(callback: types.CallbackQuery):
    """Adjust auto poll votes quantity"""
    parts = callback.data.split(":")
    order_id = parts[1]
    adjustment = int(parts[2])

    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    current_qty = order.get("auto_poll_votes_quantity", 0)
    new_qty = max(0, current_qty + adjustment)

    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {"auto_poll_votes_quantity": new_qty, "updated_at": datetime.utcnow()}}
    )

    await callback.answer(f"✅ Poll Quantity: {new_qty}")
    await auto_poll_votes_menu(callback)

@dp.callback_query(F.data.startswith("confirm_poll:"))
async def confirm_poll_handler(callback: types.CallbackQuery):
    """Confirm auto poll settings and return to order view"""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})

    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    poll_qty = order.get("auto_poll_votes_quantity", 0)
    poll_status = "ON" if order.get("auto_poll_enabled", False) else "OFF"
    await send_updated_order_message(callback, order_id, f"✅ Auto Poll confirmed: {poll_status} ({poll_qty})")


@dp.callback_query(F.data.startswith("toggle_poll:"))
async def toggle_auto_poll(callback: types.CallbackQuery):
    """Toggle auto poll votes ON/OFF"""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})

    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    current_status = order.get("auto_poll_enabled", False)
    new_status = not current_status

    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {"auto_poll_enabled": new_status, "updated_at": datetime.utcnow()}}
    )

    await auto_poll_votes_menu(callback)


# ===== NIGHT MODE FEATURE =====
@dp.callback_query(F.data.startswith("night_mode:"))
async def night_mode_toggle(callback: types.CallbackQuery):
    """Toggle night mode for slower delivery during night hours"""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})

    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    current_status = order.get("night_mode_enabled", False)
    new_status = not current_status

    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {"night_mode_enabled": new_status, "updated_at": datetime.utcnow()}}
    )

    status_text = "🟢 ON" if new_status else "🔴 OFF"
    await send_updated_order_message(callback, order_id, f"🌙 Night Mode: {status_text}")


# ===== EDIT RANDOM VIEWS =====
@dp.callback_query(F.data.startswith("edit_random:"))
async def edit_random_views(callback: types.CallbackQuery):
    """Edit random views setting"""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})

    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    current_random = order.get("random_views", 0)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="-5", callback_data=f"random_adj:{order_id}:-5"),
        InlineKeyboardButton(text="-1", callback_data=f"random_adj:{order_id}:-1"),
        InlineKeyboardButton(text=f"📊 {current_random}", callback_data=f"random_info:{order_id}"),
        InlineKeyboardButton(text="+1", callback_data=f"random_adj:{order_id}:+1"),
        InlineKeyboardButton(text="+5", callback_data=f"random_adj:{order_id}:+5"),
        width=5
    )
    builder.row(
        InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm_random:{order_id}"),
        width=1
    )
    builder.row(InlineKeyboardButton(text="⬅️ Back", callback_data=f"back_to_order:{order_id}"))

    text = (
        f"🔀 <b>Random Views Adjustment</b>\n\n"
        f"Current Random Views: {current_random}\n\n"
        f"Adjust using buttons below:\n"
        f"Click ✅ Confirm to apply changes."
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("random_adj:"))
async def adjust_random_views(callback: types.CallbackQuery):
    """Adjust random views"""
    parts = callback.data.split(":")
    order_id = parts[1]
    adjustment = int(parts[2])

    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    current_random = order.get("random_views", 0)
    new_random = max(0, current_random + adjustment)  # Prevent negative values

    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {"random_views": new_random, "updated_at": datetime.utcnow()}}
    )

    await callback.answer(f"✅ Random Views: ±{new_random}")
    await edit_random_views(callback)


@dp.callback_query(F.data.startswith("confirm_random:"))
async def confirm_random_handler(callback: types.CallbackQuery):
    """Confirm random views adjustments and return to order view"""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})

    if not order:
        return await callback.answer("❌ Order not found.", show_alert=True)

    random_views = order.get("random_views", 0)
    await callback.answer(f"✅ Random Views confirmed: ±{random_views}")
    await send_updated_order_message(callback, order_id, f"✅ Random Views confirmed: ±{random_views}")

@dp.callback_query(F.data.startswith("random_info:"))
async def random_info(callback: types.CallbackQuery):
    await callback.answer("ℹ️ Adjust random views variation", show_alert=True)


# ===== COMBI SPEED =====
@dp.callback_query(F.data.startswith("combi_speed:"))
async def combi_speed_feature(callback: types.CallbackQuery):
    """Combination speed settings"""
    order_id = callback.data.split(":")[1]

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 Normal + Night Mode", callback_data=f"combi_apply:{order_id}:normal_night"))
    builder.row(InlineKeyboardButton(text="⚡ Fast + High→Low", callback_data=f"combi_apply:{order_id}:fast_highlow"))
    builder.row(InlineKeyboardButton(text="🚀 Ultra Fast + Auto Polls", callback_data=f"combi_apply:{order_id}:ultra_poll"))
    builder.row(InlineKeyboardButton(text="⬅️ Back", callback_data=f"back_to_order:{order_id}"))

    text = "🔄 <b>Combi Speed Settings</b>\n\nSelect a combination preset:"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("combi_apply:"))
async def apply_combi_speed(callback: types.CallbackQuery):
    """Apply combination speed preset"""
    parts = callback.data.split(":")
    order_id = parts[1]
    preset = parts[2]

    update_data = {"updated_at": datetime.utcnow()}

    if preset == "normal_night":
        update_data.update({
            "speed_multiplier": 1.0,
            "speed_name": "Normal (5-6 hrs)",
            "night_mode_enabled": True
        })
        msg = "✅ Applied: Normal Speed + Night Mode"
    elif preset == "fast_highlow":
        update_data.update({
            "speed_multiplier": 1.5,
            "speed_name": "Fast (3-4 hrs)",
            "high_low_descending": True
        })
        msg = "✅ Applied: Fast Speed + High→Low Views"
    elif preset == "ultra_poll":
        update_data.update({
            "speed_multiplier": 2.0,
            "speed_name": "Ultra Fast (2-3 hrs)",
            "auto_poll_enabled": True
        })
        msg = "✅ Applied: Ultra Fast + Auto Polls"
    else:
        msg = "✅ Settings applied"

    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": update_data}
    )

    await send_updated_order_message(callback, order_id, msg)


@dp.callback_query(F.data.startswith("cancel_order:"))
async def cancel_order_callback(callback: types.CallbackQuery):
    """Step 1: Ask user for confirmation before cancelling."""
    order_id = callback.data.split(":")[1]

    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("Order not found.", show_alert=True)

    if order["status"] in ["completed", "cancelled"]:
        return await callback.answer("This order is already completed or cancelled.", show_alert=True)

    service_id = order.get("service_identifier", "")
    if service_id == "views_by_followers":
        service_label = "Views By Followers"
    elif service_id == "reactions_by_followers":
        service_label = "Reactions By Followers"
    else:
        service_label = "Poll Votes"

    channel_title = order.get("channel_title", "Unknown Channel")

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Yes, Cancel", callback_data=f"cancel_confirm_yes:{order_id}"),
        InlineKeyboardButton(text="❌ No, Go Back", callback_data=f"cancel_confirm_no:{order_id}"),
        width=2
    )

    await callback.message.edit_text(
        f"⚠️ <b>Cancel Subscription?</b>\n\n"
        f"📢 <b>Channel:</b> {channel_title}\n"
        f"🎯 <b>Service:</b> {service_label}\n\n"
        f"Are you sure you want to cancel this service?\n"
        f"A partial refund will be calculated based on what has been delivered so far.",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("cancel_confirm_no:"))
async def cancel_confirm_no_callback(callback: types.CallbackQuery):
    """User chose not to cancel — go back to order details."""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("Order not found.", show_alert=True)

    channel_id = order.get("channel_id")
    channel_title = order.get("channel_title", "Unknown Channel")
    user_id = callback.from_user.id

    try:
        await callback.message.delete()
    except Exception:
        pass

    await show_channel_order_details(
        chat_id=callback.message.chat.id,
        user_id=user_id,
        channel_id=channel_id,
        channel_title=channel_title
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("cancel_confirm_yes:"))
async def cancel_confirm_yes_callback(callback: types.CallbackQuery):
    """Step 2: Show delivery details and refund breakdown, ask for final confirmation."""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("Order not found.", show_alert=True)

    if order["status"] in ["completed", "cancelled"]:
        return await callback.answer("This order is already completed or cancelled.", show_alert=True)

    charge = order.get("charge", 0.0)
    service_id = order.get("service_identifier", "")
    channel_title = order.get("channel_title", "Unknown")
    now = datetime.utcnow()

    # Determine delivered vs total
    if service_id == "views_by_followers":
        delivered = order.get("delivered_views", 0)
        total = order.get("total_views", 0) or (order.get("views_per_post", 0) * order.get("posts_per_day", 0) * order.get("days", 0))
        metric = "Views"
        service_label = "Views By Followers"
    elif service_id == "reactions_by_followers":
        delivered = order.get("delivered_reactions", 0)
        total = order.get("total_reactions", 0) or (order.get("reactions_per_post", 0) * order.get("posts_per_day", 0) * order.get("days", 0))
        metric = "Reactions"
        service_label = "Reactions By Followers"
    else:
        delivered = order.get("delivered_votes", 0)
        total = order.get("quantity", 0)
        metric = "Votes"
        service_label = "Poll Votes"

    # Calculate refund
    if total > 0:
        delivery_progress = min(1.0, max(0.0, delivered / total))
    else:
        created_at = order.get("created_at", now)
        days = order.get("days", 1)
        days_passed = (now - created_at).days
        delivery_progress = min(1.0, max(0.0, days_passed / days))

    cost_delivered = round(charge * delivery_progress, 4)
    refund_amount = round(charge - cost_delivered, 4)
    remaining = max(0, total - delivered)

    refund_inr = await usd_to_inr_converter(refund_amount)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Confirm Cancel", callback_data=f"cancel_final:{order_id}"),
        InlineKeyboardButton(text="🔙 Go Back", callback_data=f"cancel_confirm_no:{order_id}"),
        width=2
    )

    await callback.message.edit_text(
        f"📋 <b>Cancellation Summary</b>\n\n"
        f"📢 <b>Channel:</b> {channel_title}\n"
        f"🎯 <b>Service:</b> {service_label}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Total {metric} Ordered:</b> <code>{total}</code>\n"
        f"✅ <b>Delivered:</b> <code>{delivered}</code>\n"
        f"⏳ <b>Remaining:</b> <code>{remaining}</code>\n\n"
        f"💵 <b>Total Paid:</b> <code>${charge:.4f}</code>\n"
        f"📉 <b>Cost of Delivered:</b> <code>${cost_delivered:.4f}</code>\n"
        f"💰 <b>Refund Amount:</b> <code>${refund_amount:.4f}</code> (<b>{refund_inr}</b>)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Press <b>Confirm Cancel</b> to finalize. The refund will be added to your balance immediately.",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("cancel_final:"))
async def cancel_final_callback(callback: types.CallbackQuery):
    """Step 3: Actually cancel the order and process refund."""
    order_id = callback.data.split(":")[1]
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    if not order:
        return await callback.answer("Order not found.", show_alert=True)

    if order["status"] in ["completed", "cancelled"]:
        return await callback.answer("This order is already completed or cancelled.", show_alert=True)

    charge = order.get("charge", 0.0)
    service_id = order.get("service_identifier", "")
    now = datetime.utcnow()

    if service_id == "views_by_followers":
        delivered = order.get("delivered_views", 0)
        total = order.get("total_views", 0) or (order.get("views_per_post", 0) * order.get("posts_per_day", 0) * order.get("days", 0))
    elif service_id == "reactions_by_followers":
        delivered = order.get("delivered_reactions", 0)
        total = order.get("total_reactions", 0) or (order.get("reactions_per_post", 0) * order.get("posts_per_day", 0) * order.get("days", 0))
    else:
        delivered = order.get("delivered_votes", 0)
        total = order.get("quantity", 0)

    if total > 0:
        delivery_progress = min(1.0, max(0.0, delivered / total))
    else:
        created_at = order.get("created_at", now)
        days = order.get("days", 1)
        days_passed = (now - created_at).days
        delivery_progress = min(1.0, max(0.0, days_passed / days))

    cost_delivered = round(charge * delivery_progress, 4)
    refund_amount = round(charge - cost_delivered, 4)

    # Cancel the order in DB
    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {
            "status": "cancelled",
            "cancellation_time": now,
            "refund_amount": refund_amount,
            "updated_at": now
        }}
    )

    # Credit refund to user balance
    if refund_amount > 0:
        await update_user_balance(order["user_id"], refund_amount)

    refund_inr = await usd_to_inr_converter(refund_amount)

    # Remove channel from "My Channels" if no remaining active orders for this channel
    user_id_for_channel = order.get("user_id")
    channel_id_for_check = order.get("channel_id")
    remaining_active = await orders_collection.count_documents({
        "user_id": user_id_for_channel,
        "channel_id": channel_id_for_check,
        "status": {"$in": ["confirmed", "processing"]}
    })
    if remaining_active == 0:
        await channels_collection.delete_one({
            "user_id": user_id_for_channel,
            "channel_id": channel_id_for_check
        })

    try:
        await callback.message.edit_text(
            f"✅ <b>Subscription Cancelled!</b>\n\n"
            f"📢 <b>Channel:</b> {order.get('channel_title', 'Unknown')}\n\n"
            f"💰 <b>Refund Added to Balance:</b> <code>${refund_amount:.4f}</code> (<b>{refund_inr}</b>)\n\n"
            f"You can start a new service anytime from the main menu.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.answer("✅ Order cancelled and refund processed!", show_alert=True)

def get_order_control_markup(order, is_paused: bool, high_low_str: str, night_str: str):
    """Unified controls for auto orders."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚙️ Edit Settings", callback_data=f"edit_order_params:{order['_id']}"),
        InlineKeyboardButton(text="⏱️ Delay Adjustment", callback_data=f"edit_settings:{order['_id']}"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text="▶️ Resume" if is_paused else "⏸ Pause", callback_data=f"resume:{order['_id']}" if is_paused else f"pause:{order['_id']}"),
        width=1
    )
    builder.row(
        InlineKeyboardButton(text=f"High➔Low- {high_low_str}", callback_data=f"toggle_highlow:{order['_id']}"),
        InlineKeyboardButton(text="Edit Random Views", callback_data=f"edit_random:{order['_id']}"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text="🔔 Mute/Unmute", callback_data=f"toggle_mute:{order['_id']}"),
        InlineKeyboardButton(text="🔘 Auto Poll/Votes", callback_data=f"auto_poll:{order['_id']}"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text=f"🌙 Night Mode: {night_str}", callback_data=f"night_mode:{order['_id']}"),
        InlineKeyboardButton(text="❌ Cancel Subscription", callback_data=f"cancel_order:{order['_id']}"),
        width=2
    )
    builder.row(InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_menu"))
    return builder.as_markup()


async def show_channel_order_details(chat_id: int, user_id: int, channel_id: int, channel_title: str, service_type: str = ""):
    # Filter by specific service when coming from Views or Reactions section
    if service_type in ("views_by_followers", "reactions_by_followers"):
        svc_filter = service_type
    else:
        svc_filter = {"$in": ["views_by_followers", "reactions_by_followers"]}

    orders = await orders_collection.find({
        "user_id": user_id,
        "channel_id": channel_id,
        "service_identifier": svc_filter,
        "status": {"$in": ["confirmed", "processing"]}
    }).sort("created_at", -1).to_list(None)

    svc_label = (
        "Views" if service_type == "views_by_followers"
        else "Reactions" if service_type == "reactions_by_followers"
        else "auto"
    )

    if not orders:
        await bot.send_message(
            chat_id,
            f"ℹ️ No active {svc_label} orders found for channel: {channel_title}",
            reply_markup=get_main_menu_keyboard()
        )
        return

    now = datetime.utcnow()

    for order in orders:
        created = order["created_at"]
        days = order.get("days", 0)
        posts_per_day = order.get("posts_per_day", 0)
        days_passed = (now - created).days
        days_remaining = max(0, days - days_passed)
        charge = order.get("charge", 0.0)
        is_paused = order.get("is_paused", False)

        if order["status"] == "completed":
            continue

        if is_paused:
            status_text = "⏸ <i>This campaign is currently paused.</i>"
        else:
            status_text = "▶️ <i>Your campaign is currently active.</i>"

        speed_multiplier = order.get("speed_multiplier", 1.0)
        speed_name, speed_emoji = get_speed_name(speed_multiplier)
        is_muted = order.get("is_muted", False)
        mute_text = "🔕 ON" if is_muted else "🔔 OFF"
        night_mode = order.get("night_mode_enabled", False)
        night_text = "🌙 ON" if night_mode else "🌙 OFF"
        high_low = order.get("high_low_descending", False)
        high_low_str = "🟢 ON" if high_low else "🔴 OFF"

        if order["service_identifier"] == "views_by_followers":
            per_post_qty = order.get("views_per_post", 0)
            metric_label = "Views"
            service_name = "views_by_followers"
        else:
            per_post_qty = order.get("reactions_per_post", 0)
            metric_label = "Reactions"
            service_name = "reactions_by_followers"

        total_delay_sec = get_order_delay_seconds(order)
        base_delay_sec = get_base_delay_seconds(speed_multiplier)
        custom_adjustment = total_delay_sec - base_delay_sec
        adj_str = f" ({custom_adjustment:+d}s custom)" if custom_adjustment != 0 else ""
        remaining_today_posts, daily_quantity, daily_eta = get_daily_delivery_snapshot(order, per_post_qty)
        delay_info = f"Delay: {total_delay_sec}s{adj_str} (Today {daily_quantity} {metric_label.lower()} in {daily_eta})"

        night_note = "\n🌙 <i>Night Mode: delivery ÷3 during 11 PM–7 AM IST</i>" if night_mode else ""

        text = (
            f"📢 <b>Channel:</b> <i>{channel_title}</i> <code>(ID: {channel_id})</code>\n\n"
            f"🎯 <b>Service:</b> <code>{service_name}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>{metric_label} Per Post:</b> <code>{per_post_qty}</code>\n"
            f"📝 <b>Daily Posts:</b> <code>{posts_per_day}</code>\n"
            f"📆 <b>Plan Duration:</b> <code>{days} Days</code>\n"
            f"🔔 <b>Mute/Unmute:</b> <code>{mute_text}</code>\n"
            f"🌙 <b>Night Mode:</b> <code>{night_text}</code>\n"
            f"⏳ <b>{delay_info}</b>\n"
            f"💰 <b>Price:</b> <code>${charge:.3f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🟡 <b>Remaining Today:</b> <code>{remaining_today_posts} Post(s)</code>\n"
            f"⏳ <b>Time Left:</b> <code>{days_remaining} Day(s)</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_text}"
            f"{night_note}"
        )

        markup = get_order_control_markup(order, is_paused, high_low_str, night_text)
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)

    await bot.send_message(chat_id, "Main Menu", reply_markup=get_main_menu_keyboard())


@dp.message(OrderStates.SELECTING_CHANNEL)
async def handle_channel_selection(message: types.Message, state: FSMContext):
    """Legacy text-based selection support."""
    data = await state.get_data()
    channels = data.get('available_channels', [])
    selected_title = message.text.replace("📢 ", "")

    selected_channel = next((ch for ch in channels if ch['channel_title'] == selected_title), None)
    if not selected_channel:
        await message.answer("❌ Invalid channel selection. Please choose from the list.")
        return

    await show_channel_order_details(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        channel_id=selected_channel['channel_id'],
        channel_title=selected_channel['channel_title']
    )
    await state.clear()


@dp.message(F.text == "💳 Add Balance")
async def start_payment(message: types.Message, state: FSMContext):
    await state.set_state(PaymentStates.SELECT_METHOD)
    await message.answer(
        "Select your payment method:",
        reply_markup=get_payment_keyboard()
    )

@dp.message(F.text.in_(["🇮🇳 UPI ~ All in One Deposit", "🪙 Crypto All (Auto)", "💎 Crypto Deposit"]), PaymentStates.SELECT_METHOD)
async def select_method(message: types.Message, state: FSMContext):
    method = message.text
    await state.update_data(method=method)

    # Check if Crypto Deposit selected - use OxaPay
    if method == "💎 Crypto Deposit":
        keyboard = [
            [
                InlineKeyboardButton(text="$0.1", callback_data="oxapay_amount_0.1"),
                InlineKeyboardButton(text="$1", callback_data="oxapay_amount_1"),
                InlineKeyboardButton(text="$5", callback_data="oxapay_amount_5"),
            ],
            [
                InlineKeyboardButton(text="$10", callback_data="oxapay_amount_10"),
                InlineKeyboardButton(text="$15", callback_data="oxapay_amount_15"),
                InlineKeyboardButton(text="$20", callback_data="oxapay_amount_20"),
            ],
            [
                InlineKeyboardButton(text="$25", callback_data="oxapay_amount_25"),
                InlineKeyboardButton(text="$30", callback_data="oxapay_amount_30"),
                InlineKeyboardButton(text="$50", callback_data="oxapay_amount_50"),
            ],
        ]

        welcome_msg = (
            "💎 *Crypto Deposit*\n\n"
            "Please select the amount you want to pay:"
        )

        await message.answer(
            welcome_msg,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode="Markdown"
        )
        await state.set_state(PaymentStates.OXAPAY_AMOUNT_SELECTION)
        return

    await state.set_state(PaymentStates.ENTER_AMOUNT)
    await message.answer(
        f"Enter amount you want to add (USD):\n"
        f"Min: $1, Max: $1000",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.callback_query(F.data.startswith("oxapay_amount_"), PaymentStates.OXAPAY_AMOUNT_SELECTION)
async def oxapay_amount_selected(callback: types.CallbackQuery, state: FSMContext):
    """User selects amount for OxaPay"""
    await callback.answer()

    amount = callback.data.split("_")[2]
    chat_id = callback.message.chat.id
    user_oxapay_orders[chat_id] = {"amount": amount}

    print("\n*** User Selected OxaPay Amount ***")
    print("Chat ID:", chat_id)
    print("Selected Amount: $" + amount)
    print("Updated Orders Database:")
    print(json.dumps(user_oxapay_orders, indent=2))

    button = [[InlineKeyboardButton(text="Generate Payment Invoice", callback_data="oxapay_generate_invoice")]]

    confirmation_msg = (
        f"✅ Selected Amount: *${amount}*\n\n"
        "Click below to generate your crypto payment invoice:"
    )

    await callback.message.edit_text(
        text=confirmation_msg,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=button),
        parse_mode="Markdown",
    )
    await state.set_state(PaymentStates.OXAPAY_CONFIRM)


@dp.callback_query(F.data == "oxapay_generate_invoice", PaymentStates.OXAPAY_CONFIRM)
async def generate_oxapay_invoice(callback: types.CallbackQuery, state: FSMContext):
    """Generate OxaPay invoice"""
    await callback.answer()

    chat_id = callback.message.chat.id
    amount = user_oxapay_orders[chat_id]["amount"]

    order_id = f"ORD-{chat_id}-{amount}"
    user_oxapay_orders[chat_id]["order_id"] = order_id

    # Use ngrok public URL as callback (if available)
    if public_url:
        callback_url = public_url + "/verify_payment"
    else:
        callback_url = "http://localhost:5000/verify_payment"  # Fallback
        print("⚠️ Warning: Using localhost callback - payment webhooks won't work externally")

    print("\n*** Generating OxaPay Invoice ***")
    print(f"Chat ID: {chat_id}")
    print(f"Order ID: {order_id}")
    print(f"Amount: ${amount}")
    print(f"Callback URL: {callback_url}")
    print("Updated Orders Database:")
    print(json.dumps(user_oxapay_orders, indent=2))

    invoice_data = {
        "amount": float(amount),
        "currency": "USD",
        "lifetime": 30,
        "fee_paid_by_payer": 1,
        "under_paid_coverage": 2.5,
        "to_currency": "USDT",
        "auto_withdrawal": False,
        "mixed_payment": True,
        "callback_url": callback_url,
        "return_url": "https://example.com/success",
        "order_id": order_id,
        "thanks_message": "Thank you for your payment!",
        "description": "Payment for order " + order_id,
        "sandbox": False,
    }

    headers = {
        "merchant_api_key": OXAPAY_API_KEY,
        "Content-Type": "application/json",
    }

    try:
        oxapay_url = "https://api.oxapay.com/v1/payment/invoice"
        response = requests.post(
            oxapay_url, headers=headers, data=json.dumps(invoice_data), timeout=15
        )
        result = response.json()

        print("\nOxaPay API Response:")
        print(json.dumps(result, indent=2))

        if result.get("status") == 200:
            payment_url = result["data"]["payment_url"]
            success_msg = (
                f"✅ *Invoice Generated Successfully!*\n\n"
                f"💰 Amount: *${amount}*\n"
                f"🧾 Order ID: `{order_id}`\n\n"
                f"🔗 Click to pay:\n{payment_url}\n\n"
                "_You'll receive a confirmation here once payment is verified._"
            )
            print("\n*** Invoice Created Successfully ***")
            print("Payment URL:", payment_url)
        else:
            error_detail = result.get("message", "Unknown error")
            success_msg = (
                "❌ *Failed to create invoice*\n\n"
                f"Error: {error_detail}\n\n"
                "Please try again."
            )
            print("\n*** Invoice Creation Failed ***")
            print("Error:", result)

    except Exception as e:
        success_msg = (
            "⚠️ *Error generating invoice*\n\n"
            f"Technical Error: {e}\n\n"
            "Please try again later."
        )
        print("\n*** Exception occurred ***")
        print("Error:", e)

    await callback.message.edit_text(success_msg, parse_mode="Markdown")
    await state.clear()
    await callback.message.answer("Main Menu", reply_markup=get_main_menu_keyboard())


@dp.message(PaymentStates.ENTER_AMOUNT, F.text.regexp(r'^\d+$'))
async def enter_amount(message: types.Message, state: FSMContext):
    amount = float(message.text)

    if amount < 1 or amount > 1000:
        await message.answer("❌ <b>Amount must be between $1 and $1000.</b>\nPlease try again:", parse_mode="HTML")
        return

    await state.update_data(amount=amount)
    data = await state.get_data()
    method = data.get('method', 'Unknown')

    await state.set_state(PaymentStates.PAYMENT_DETAILS)

    if "UPI" in method:
        inr_amount = await usd_to_inr_converter(amount)
        # Load QR and UPI ID from DB; fall back to hardcoded if not set
        upi_qr_setting = await settings_collection.find_one({"key": "upi_qr"})
        upi_id_setting = await settings_collection.find_one({"key": "upi_id"})
        qr_photo = (upi_qr_setting.get("file_id") if upi_qr_setting else None) or "https://i.ibb.co/Fb5hpJSn/image.jpg"
        upi_id_val = (upi_id_setting.get("value") if upi_id_setting else None) or "paytm.s1lmr2p@pty"
        caption = (
            f"🇮🇳 <b>UPI Payment</b>\n\n"
            f"💰 <b>Amount:</b> <code>${amount:.2f}</code> (~{inr_amount})\n"
            f"🏦 <b>UPI ID:</b> <code>{html_escape(upi_id_val)}</code>\n\n"
            f"📩 <i>Send the payment screenshot here after completing the transaction.</i>"
        )
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=qr_photo,
            caption=caption,
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Back")]],
                resize_keyboard=True
            )
        )

    elif "Crypto" in method:
        await message.answer(
            f"🪙 <b>Crypto Payment Details</b>\n\n"
            f"💵 <b>Amount:</b> <code>${amount:.2f}</code>\n\n"
            f"🔸 <b>BTC:</b> <code>bc1qxyz...your_btc_address</code>\n"
            f"🔸 <b>ETH:</b> <code>0x123...your_eth_address</code>\n\n"
            f"📩 <i>Send a screenshot after confirming your crypto transaction.</i>",
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Back")]],
                resize_keyboard=True
            )
        )

    else:  # Crypto Deposit
        await message.answer(
            f"💎 <b>Crypto Deposit</b>\n\n"
            f"💵 <b>Amount:</b> <code>${amount:.2f}</code>\n"
            f"🆔 <b>Binance ID:</b> <code>985048396</code>\n"
            f"👤 <b>Name:</b> <code>TG - VIEW BOT</code>\n"
            f"📩 <i>Send a screenshot after completing the payment.</i>",
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Back")]],
                resize_keyboard=True
            )
        )



@dp.message(PaymentStates.PAYMENT_DETAILS, F.photo)
async def handle_payment_screenshot(message: types.Message, state: FSMContext):
    # Get the highest resolution photo (last in the array)
    file_id = message.photo[-1].file_id
    await state.update_data(screenshot_file_id=file_id)
    await state.set_state(PaymentStates.WAITING_FOR_SCREENSHOT)

    await message.answer(
        "Screenshot received! Please confirm payment:",
        reply_markup=get_payment_confirmation_keyboard()
    )

@dp.message(PaymentStates.WAITING_FOR_SCREENSHOT, F.text == "✅ Confirm Payment")
async def confirm_payment(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()

    if not data:
        await message.answer("Session expired. Please start again.")
        await state.clear()
        return

    method = data.get("method")
    amount = data.get("amount")
    screenshot_file_id = data.get("screenshot_file_id")

    # Create payment record
    payment_id = await create_payment(
        user_id=user_id,
        amount=amount,
        method=method,
        status="pending_approval",
        screenshot_file_id=screenshot_file_id
    )

    # Notify admin
    user = await users_collection.find_one({"user_id": user_id})
    username = user.get('username', 'N/A')
    first_name = user.get('first_name', 'N/A')

    # Then modify the caption creation
    caption = (
        f"📬 New Payment Request!\n\n"
        f"👤 User: {first_name} (@{username})\n"
        f"🆔 ID: {user_id}\n"
        f"💳 Method: {method}\n"
        f"💰 Amount: ${amount:.2f}\n"
        f"📋 Payment ID: <code>{html_escape(str(payment_id))}</code>"
    )

    # Then send the photo with HTML parsing
    await bot.send_photo(
        chat_id=ADMIN_ID,
        photo=screenshot_file_id,
        caption=caption,
        parse_mode="HTML",  # Changed from Markdown to HTML
        reply_markup=get_admin_payment_approval_keyboard(str(payment_id))
    )

    await message.answer(
        "✅ Payment submitted for admin approval!\n\n"
        "Your payment is now pending review. You'll be notified when it's approved.",
        reply_markup=get_main_menu_keyboard()
    )
    await state.clear()

@dp.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment(callback: types.CallbackQuery):
    payment_id = callback.data.split(":")[1]

    # Update payment status
    payment = await payments_collection.find_one({"_id": ObjectId(payment_id)})
    if not payment:
        await callback.answer("Payment not found")
        return

    if payment['status'] != 'pending_approval':
        await callback.answer("Payment already processed")
        return

    # Update payment status
    await payments_collection.update_one(
        {"_id": ObjectId(payment_id)},
        {"$set": {"status": "approved", "updated_at": datetime.utcnow()}}
    )

    # Add funds to user balance
    await update_user_balance(payment['user_id'], payment['amount'])

    # Notify user
    await bot.send_message(
        payment['user_id'],
        f"✅ Your payment of ${payment['amount']:.2f} has been approved!\n\n"
        f"💰 Your balance has been updated."
    )

    # Update admin message
    await callback.message.edit_caption(
        caption=f"{callback.message.caption}\n\n✅ APPROVED by {callback.from_user.first_name}",
        reply_markup=None
    )
    await callback.answer("Payment approved!")

@dp.callback_query(F.data.startswith("decline_payment:"))
async def decline_payment(callback: types.CallbackQuery):
    payment_id = callback.data.split(":")[1]

    # Update payment status
    payment = await payments_collection.find_one({"_id": ObjectId(payment_id)})
    if not payment:
        await callback.answer("Payment not found")
        return

    if payment['status'] != 'pending_approval':
        await callback.answer("Payment already processed")
        return

    # Update payment status
    await payments_collection.update_one(
        {"_id": ObjectId(payment_id)},
        {"$set": {"status": "declined", "updated_at": datetime.utcnow()}}
    )

    # Notify user
    await bot.send_message(
        payment['user_id'],
        f"❌ Your payment of ${payment['amount']:.2f} has been declined.\n\n"
        f"Please contact admin for more information."
    )

    # Update admin message
    await callback.message.edit_caption(
        caption=f"{callback.message.caption}\n\n❌ DECLINED by {callback.from_user.first_name}",
        reply_markup=None
    )
    await callback.answer("Payment declined!")

@dp.message(F.text == "📊 Views By Followers")
async def views_follower_handler(message: types.Message, state: FSMContext):
    await state.update_data(service_type="views_by_followers")
    await message.answer(
        "🔹 Please select a channel:",
        reply_markup=get_channel_select_keyboard()
    )

@dp.message(F.text == "❤️‍🔥 Reactions By Followers")
async def reaction_follower_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    # Initialize config with reactions
    if user_id in user_configs:
        user_configs[user_id].total_reactions = user_configs[user_id].total_views
    await state.update_data(service_type="reactions_by_followers")
    await message.answer(
        "🔹 Please select a channel:",
        reply_markup=get_channel_select_keyboard()
    )


# ===== VOTE ORDERING - CHANNEL SELECTION HANDLER (NEW FIX) ===== #
@dp.message(OrderStates.SELECTING_CHANNEL, F.chat_shared)
async def handle_vote_channel_selection(message: types.Message, state: FSMContext):
    """Handle channel selection specifically for vote ordering (FIXED WORKFLOW)"""
    data = await state.get_data()
    service_type = data.get('service_type')

    # Only handle if this is for votes
    if service_type != 'poll_votes':
        # For other services, let default handler take care
        await handle_channel_shared(message, state)
        return

    chat_id = message.chat_shared.chat_id
    user_id = message.from_user.id

    try:
        # Get channel info using first active client
        client = get_active_clients()[0] if get_active_clients() else None
        if not client:
            await message.answer("❌ No active Telegram clients available. Please contact admin.")
            return

        try:
            entity = await client.get_entity(PeerChannel(chat_id))
            channel_title = entity.title
            is_public = hasattr(entity, 'username') and entity.username is not None

            # Try to get full channel info
            try:
                full_channel = await client(GetFullChannelRequest(channel=entity))
                print(f"Vote channel selected: {channel_title}, Public: {is_public}")
            except:
                pass

            # Save channel info
            await state.update_data(
                channel_id=chat_id,
                channel_title=channel_title,
                is_public_channel=is_public
            )

            # If private channel, ask for invite link so workers can join
            if not is_public:
                await message.answer(
                    f"✅ Channel selected: <b>{channel_title}</b>\n\n"
                    "🔒 This is a private channel/group.\n\n"
                    "📎 <b>Step 2: Send Invite Link</b>\n"
                    "Please send the invite link so worker accounts can join and vote:\n\n"
                    "Example: <code>https://t.me/+xxxxxxxxxxx</code>",
                    parse_mode="HTML",
                    reply_markup=ReplyKeyboardMarkup(
                        keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
                        resize_keyboard=True
                    )
                )
                await state.set_state(OrderStates.WAITING_FOR_INVITE_LINK)
            else:
                # Public channel - proceed to poll forwarding
                await message.answer(
                    f"✅ Channel selected: <b>{channel_title}</b>\n\n"
                    "📨 <b>Step 2: Forward Vote Post</b>\n"
                    "Please forward the specific poll/vote post from this channel\n"
                    "that you want to boost.\n\n"
                    "Or press '⬅️ Cancel Order' to go back",
                    parse_mode="HTML",
                    reply_markup=ReplyKeyboardMarkup(
                        keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
                        resize_keyboard=True
                    )
                )
                await state.set_state(OrderStates.waiting_for_content)

        except Exception as e:
            print(f"Error getting channel info: {e}")
            await message.answer(
                "❌ Could not access channel information.\n"
                "Please make sure the bot is added as admin in the channel."
            )
            return

    except Exception as e:
        print(f"Error in vote channel selection: {e}")
        await message.answer("❌ An error occurred. Please try again.")


@dp.message(F.chat_shared)
async def handle_channel_shared(message: types.Message, state: FSMContext):
    chat = message.chat_shared
    user_id = message.from_user.id
    chat_id = chat.chat_id

    await state.update_data(
        channel_id=chat_id,
        channel_title=f"Unknown Channel (ID: {chat_id})"
    )

    await message.answer(
        "🔗 Please send the invite link for this channel (t.me/... or telegram.me/...)\n\n"
        "⬅️ Press 'Cancel' to stop.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
            resize_keyboard=True
        )
    )
    await state.set_state(OrderStates.WAITING_FOR_INVITE_LINK)


@dp.message(OrderStates.WAITING_FOR_INVITE_LINK, F.text)
async def handle_invite_link(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel Order":
        await cancel_order(message, state)
        return

    link = message.text.strip()
    data = await state.get_data()
    service_type = data.get('service_type')

    # ===== VOTE-SPECIFIC HANDLING (NEW FIX) ===== #
    if service_type == 'poll_votes':
        # For votes, we need private invite links only
        # Validate that it's a private invite link
        if not (link.startswith('https://t.me/+') or link.startswith('https://t.me/joinchat/')):
            await message.answer(
                "❌ Invalid invite link format.\n\n"
                "For private channels, please send the invite link:\n"
                "Example: <code>https://t.me/+xxxxxxxxxxx</code>",
                parse_mode="HTML"
            )
            return

        # Save invite link
        await state.update_data(invite_link=link)

        # Proceed to poll forwarding first (DON'T JOIN YET)
        await message.answer(
            "✅ Invite link saved.\n\n"
            "📨 <b>Step 3: Forward Vote Post</b>\n"
            "Now please forward the specific poll/vote post from this channel\n"
            "that you want to boost.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
                resize_keyboard=True
            )
        )
        await state.set_state(OrderStates.waiting_for_content)
        return

    # ===== EXISTING LOGIC FOR OTHER SERVICES ===== #
    # ✅ Accept multiple formats: @username, username, t.me/username, telegram.me/username
    # Normalize the input
    if link.startswith('@'):
        # Remove @ and validate
        link = link[1:]

    # If it's just a username without any protocol, convert to t.me format
    if not any(x in link.lower() for x in ['t.me/', 'telegram.me/', 'telegram.dog/']):
        # Assume it's a username and convert to t.me format
        link = f"https://t.me/{link}"

    # Now validate that it's a proper telegram link
    if not any(x in link.lower() for x in ['t.me/', 'telegram.me/', 'telegram.dog/']):
        await message.answer(
            "❌ Invalid format. Please send one of these:\n"
            "• @channelname\n"
            "• channelname\n"
            "• https://t.me/channelname\n"
            "• https://t.me/+xxxx (private link)"
        )
        return

    user_id = message.from_user.id
    shared_chat_id = data.get('channel_id')

    # ✅ Fixed: Properly check if active_clients list is empty
    if not get_active_clients():
        await message.answer(
            "❌ No active Telegram clients available.\n\n"
            "Please contact the admin to add Telegram accounts for the bot to work.",
            reply_markup=get_main_menu_keyboard()
        )
        await state.clear()
        return

    client = get_active_clients()[0]

    try:
        validated = await validate_invite_link_only(client, link)
        print(validated)
        if not validated["success"]:
            error_msg = validated.get("error", "Unknown error")
            await message.answer(error_msg)
            await message.answer(
                "Main Menu",
                reply_markup=get_main_menu_keyboard()
            )
            return

        actual_chat_id = int(f'-100{validated["channel_id"]}')
        channel_title = validated["channel_title"]
        username = validated["username"]
        is_public = validated["is_public"]

        if actual_chat_id != shared_chat_id:
            await message.answer("⚠️ This invite link does not match the originally shared channel. Please send the correct link.")
            await message.answer(
                "Main Menu",
                reply_markup=get_main_menu_keyboard()
            )
            return

        await add_user_channel(
            user_id=user_id,
            channel_id=shared_chat_id,
            channel_title=channel_title,
            is_public=is_public,
            invite_link=link
        )

        config = ConfigData()
        config.channel_id = shared_chat_id
        config.channel_title = channel_title
        config.invite_link = link
        user_configs[user_id] = config

        service_type = data.get("service_type", "views_by_followers")

        if service_type == "views_by_followers":
            config.final_total_views = config.total_views * config.posts_per_day * config.days
            await state.set_state(OrderStates.CONFIGURING_VIEWS)
            await message.answer(
                await get_config_text(config),
                parse_mode="markdown",
                reply_markup=get_views_config_markup(config)
            )
        else:
            config.final_total_reactions = config.total_reactions * config.posts_per_day * config.days
            await state.set_state(OrderStates.CONFIGURING_REACTIONS)
            await message.answer(
                await get_config_text(config, is_reactions=True),
                parse_mode="markdown",
                reply_markup=get_reaction_config_markup(config)
            )

    except Exception as e:
        print(f"❌ Invite link handling failed: {e}")
        import traceback
        traceback.print_exc()
        await delete_user_channel(shared_chat_id)
        await message.answer("❌ An error occurred while processing your channel. Please try again.")
        await message.answer(
            "Main Menu",
            reply_markup=get_main_menu_keyboard()
        )


# === View Callback === #
@dp.callback_query(F.data.startswith(("views:", "posts:", "days:", "speed:")))
async def handle_views_adjustment(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    config = user_configs.get(user_id)

    if not config:
        return await callback.answer("Session expired. Please start again.", show_alert=True)

    try:
        field, value = callback.data.split(":")
        max_limit = get_per_post_limit()

        if field == "speed":
            # Handle speed adjustment
            if value == "slow":
                config.speed_multiplier = 0.5
                config.speed_name = "Slow (7-8 hrs)"
            elif value == "normal":
                config.speed_multiplier = 1.0
                config.speed_name = "Normal (5-6 hrs)"
            elif value == "fast":
                config.speed_multiplier = 1.5
                config.speed_name = "Fast (3-4 hrs)"
            elif value == "ultra":
                config.speed_multiplier = 2.0
                config.speed_name = "Ultra Fast (2-3 hrs)"
        else:
            value = int(value)

            if field == "views":
                new_total = config.total_views + value
                if new_total < 1:
                    raise ValueError("Below minimum")
                if new_total > max_limit:
                    raise OverflowError("Above max limit")
                config.total_views = new_total

            elif field == "posts":
                config.posts_per_day = max(1, config.posts_per_day + value)
            elif field == "days":
                config.days = max(1, config.days + value)

        # Calculate base charge (speed is FREE, no multiplier applied)
        base_charge = await calculate_views_charge(
            config.total_views,
            config.posts_per_day,
            config.days
        )
        config.charge = base_charge

        config.final_total_views = config.total_views * config.posts_per_day * config.days

        current_state = await state.get_state()
        is_reactions = current_state == OrderStates.CONFIGURING_REACTIONS

        await callback.message.edit_text(
            await get_config_text(config, is_reactions),
            parse_mode='markdown',
            reply_markup=get_reaction_config_markup(config) if is_reactions else get_views_config_markup(config)
        )
        await callback.answer()

    except OverflowError:
        await callback.answer(f"❌ Max limit reached: {get_per_post_limit()} (available accounts)", show_alert=True)
    except Exception:
        await callback.answer("❌ Minimum order value reached!", show_alert=True)


# === Reaction Callback === #
@dp.callback_query(F.data.startswith(("r_posts:", "r_days:", "reactions:", "r_speed:")))
async def handle_reaction_adjustment(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    config = user_configs.get(user_id)

    if not config:
        return await callback.answer("Session expired. Please start again.", show_alert=True)

    try:
        field, value = callback.data.split(":")
        max_limit = get_per_post_limit()

        if field == "r_speed":
            # Handle speed adjustment for reactions — uses separate r_speed: prefix
            if value == "slow":
                config.speed_multiplier = 0.5
                config.speed_name = "Slow (7-8 hrs)"
            elif value == "normal":
                config.speed_multiplier = 1.0
                config.speed_name = "Normal (5-6 hrs)"
            elif value == "fast":
                config.speed_multiplier = 1.5
                config.speed_name = "Fast (3-4 hrs)"
            elif value == "ultra":
                config.speed_multiplier = 2.0
                config.speed_name = "Ultra Fast (2-3 hrs)"
        else:
            value = int(value)

            if field == "reactions":
                if not hasattr(config, "total_reactions"):
                    config.total_reactions = config.total_views
                new_total = config.total_reactions + value
                if new_total < 1:
                    raise ValueError("Below minimum")
                if new_total > max_limit:
                    raise OverflowError("Above max limit")
                config.total_reactions = new_total

            elif field == "r_posts":
                config.posts_per_day = max(1, config.posts_per_day + value)
            elif field == "r_days":
                config.days = max(1, config.days + value)

        # Calculate base charge (speed is FREE, no multiplier applied)
        if hasattr(config, "total_reactions"):
            base_charge = await calculate_reactions_charge(
                config.total_reactions,
                config.posts_per_day,
                config.days
            )
            config.final_total_reactions = config.total_reactions * config.posts_per_day * config.days
        else:
            base_charge = await calculate_reactions_charge(
                config.total_views,
                config.posts_per_day,
                config.days
            )
            config.final_total_reactions = config.total_views * config.posts_per_day * config.days

        config.charge = base_charge

        current_state = await state.get_state()
        is_reactions = current_state == OrderStates.CONFIGURING_REACTIONS

        await callback.message.edit_text(
            await get_config_text(config, is_reactions),
            parse_mode='markdown',
            reply_markup=get_reaction_config_markup(config) if is_reactions else get_views_config_markup(config)
        )
        await callback.answer()

    except OverflowError:
        await callback.answer(f"❌ Max limit reached: {get_per_post_limit()} (available accounts)", show_alert=True)
    except Exception as e:
        print(f"Error in reaction adjustment: {e}")
        await callback.answer("❌ Minimum order value reached!", show_alert=True)


@dp.callback_query(F.data == "action:order")
async def handle_order(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    config = user_configs.get(user_id)
    if not config:
        return await callback.answer("Session expired. Please start again.", show_alert=True)

    current_state = await state.get_state()
    is_reactions = current_state == OrderStates.CONFIGURING_REACTIONS

    service_identifier = "reactions_by_followers" if is_reactions else "views_by_followers"
    order_type = "Reactions" if is_reactions else "Views"
    per_post_value = config.total_reactions if is_reactions else config.total_views

    await callback.message.edit_text(
        f"📝 {order_type} Order Summary\n\n"
        f"🔹 Channel: {config.channel_title}\n"
        f"🔹 {order_type} Per Post: {per_post_value}\n"
        f"🔹 Posts/Day: {config.posts_per_day}\n"
        f"🔹 Duration: {config.days} days\n\n"
        f"💰 Total Charge: ${config.charge:.4f} ({await usd_to_inr_converter(config.charge)})\n\n"
        "Proceed to payment?",
        reply_markup=InlineKeyboardBuilder()
        .button(text="💳 Pay Now", callback_data="payment:confirm")
        .button(text="✖️ Cancel", callback_data="payment:cancel")
        .adjust(2)
        .as_markup()
    )

    # Store service identifier in state
    await state.update_data(service_identifier=service_identifier)
    await callback.answer()

@dp.callback_query(F.data == "action:back")
async def handle_back(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "🔹 Main Menu:",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("show_"))
async def handle_show_info(callback: types.CallbackQuery):
    await callback.answer("🔻 Select the Options Below to Modify 🔻", show_alert=False)

@dp.callback_query(F.data == "payment:confirm")
async def handle_payment_confirm(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    # Prevent duplicate order creation from rapid/spam button clicks
    if user_id in payment_processing_users:
        return await callback.answer("⏳ Your order is already being processed, please wait...", show_alert=True)

    config = user_configs.get(user_id)
    if not config:
        return await callback.answer("Session expired. Please start again.", show_alert=True)

    # Lock this user from submitting another payment until this one finishes
    payment_processing_users.add(user_id)

    try:
        # Check user balance
        user = await users_collection.find_one({"user_id": user_id})
        if not user:
            await callback.answer("User not found. Please start again.")
            await state.clear()
            return

        if user.get('balance', 0) < config.charge:
            return await callback.answer("❌ Insufficient balance. Please add funds to your account.", show_alert=True)

        # Get service identifier
        data = await state.get_data()
        service_identifier = data.get('service_identifier', 'views_by_followers')

        # === Construct Order ===
        order_data = {
        "user_id": user_id,
        "service_identifier": service_identifier,
        "service_type": "by_followers",
        "channel_id": config.channel_id,
        "channel_title": config.channel_title,
        "charge": config.charge,
        "status": "confirmed",
        "posts_per_day": config.posts_per_day,
        "days": config.days,
        "is_paused": False,
        "speed_multiplier": config.speed_multiplier,
        "speed_name": config.speed_name,
        "custom_delay_seconds": get_base_delay_seconds(config.speed_multiplier),
        "created_at": datetime.utcnow(),
        "processed_today": {}, # Initialize for daily tracking
        "total_posts_processed": 0, # Initialize for total posts tracked
    }

        if service_identifier == "views_by_followers":
            order_data.update({
                "views_per_post": config.total_views,
                "total_views": config.final_total_views,
                "delivered_views": 0
            })
            metric = "Views"
            service_name = "Views By Followers"
        else: # reactions_by_followers
            order_data.update({
                "reactions_per_post": config.total_reactions if hasattr(config, "total_reactions") else config.total_views,
                "total_reactions": config.final_total_reactions,
                "delivered_reactions": 0
            })
            metric = "Reactions"
            service_name = "Reactions By Followers"

        # === Save Order ===
        result = await orders_collection.insert_one(order_data)
        order_id = result.inserted_id

        # Deduct balance
        await update_user_balance(user_id, -config.charge)

        speed_emoji = "🐌" if config.speed_multiplier == 0.5 else ("🐢" if config.speed_multiplier == 1.0 else ("🚀" if config.speed_multiplier == 1.5 else "⚡"))

        # Get correct per-post value
        if service_identifier == "reactions_by_followers":
            per_post_value = config.total_reactions if hasattr(config, 'total_reactions') else config.total_views
        else:
            per_post_value = config.total_views

        await callback.message.edit_text(
            f"✅ <b>Order Confirmed!</b>\n"

            f"⚪️ <b>Order ID:</b> <code>{str(order_id)}</code>\n"
            f"🖥️ <b>Service:</b> <code>{service_name}</code>\n"
            f"🆔 <b>Channel:</b> {'Private Channel' if config.channel_id < 0 else 'Public Channel'} "
            f"(ID: <code>{config.channel_id}</code>)\n\n"

            f"👁️ <b>{metric} Per Post:</b> <code>{per_post_value}</code>\n"
            f"📝 <b>Post Per Day:</b> <code>{config.posts_per_day}</code>\n"
            f"⏳ <b>Duration:</b> <code>{config.days} Days</code>\n\n"

            f"💰 <b>Charge:</b> <code>${config.charge:.4f}</code>\n\n"

            f"🔄 <i>Auto-service started! {metric.capitalize()} will be delivered automatically.</i>",
            parse_mode="HTML"
        )

        invite_link = getattr(config, 'invite_link', None)
        if invite_link and get_active_clients():
            asyncio.create_task(join_all_clients_to_channel(config.channel_id, invite_link))

        await callback.message.answer("Main Menu", reply_markup=get_main_menu_keyboard())
        await callback.answer()
        await state.clear()

    finally:
        # Always release the lock so user can place a new order later
        payment_processing_users.discard(user_id)


@dp.callback_query(F.data == "payment:cancel")
async def handle_payment_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await callback.message.answer(
        "❌ Payment cancelled",
        reply_markup=get_main_menu_keyboard()
    )
    await state.clear()
    await callback.answer()

# ===== ADMIN ACCOUNT MANAGEMENT HANDLERS ===== #

ACCOUNTS_PER_PAGE = 10  # You can adjust this


def get_live_identifiers():
    """
    Return two sets: live phone numbers and live session names,
    built from the currently connected clients in memory.
    """
    live_phones = set()
    live_names = set()
    for c in get_active_clients():
        p = getattr(c, "_session_phone", None) or getattr(c, "_tg_phone", None)
        n = getattr(c, "_session_name", None) or getattr(c, "_tg_session_name", None)
        if p:
            live_phones.add(str(p).strip())
        if n:
            live_names.add(str(n).strip())
    return live_phones, live_names


def is_session_live(session: dict, live_phones: set, live_names: set) -> bool:
    """Return True only if this DB session has a real connected client in memory."""
    phone = str(session.get("phone", "")).strip()
    name = str(session.get("session_name", "")).strip()
    return phone in live_phones or name in live_names


def filter_live_sessions(sessions: list) -> list:
    """Keep only sessions that have a currently connected client."""
    live_phones, live_names = get_live_identifiers()
    return [s for s in sessions if is_session_live(s, live_phones, live_names)]


def render_account_page(sessions, page: int, total_pages: int):
    start = page * ACCOUNTS_PER_PAGE
    end = start + ACCOUNTS_PER_PAGE
    text = f"📱 <b>Telegram Account Management</b>\n\n"
    text += f"🟢 <b>Live Accounts (Page {page+1}/{total_pages})</b>:\n\n"

    if not sessions:
        text += "⚠️ No live accounts connected right now.\n\n"
        text += "Add accounts via 📦 Bulk Import or 📝 Generate Session.\n"
        return text

    for i, session in enumerate(sessions[start:end], start + 1):
        # Format last check time
        last_check = session.get('last_check')
        if last_check:
            time_diff = datetime.utcnow() - last_check
            if time_diff.days > 0:
                last_check_str = f"{time_diff.days}d ago"
            elif time_diff.seconds > 3600:
                last_check_str = f"{time_diff.seconds // 3600}h ago"
            else:
                last_check_str = f"{time_diff.seconds // 60}m ago"
        else:
            last_check_str = "Never"

        text += (
            f"{i}. {get_session_display_label(session)} - {get_session_username_text(session)}\n"
            f"   🟢 <i>Live &amp; Active</i> (Checked: {last_check_str})\n"
        )
        text += "\n"

    text += (
        "\n<blockquote expandable>"
        "💡 <b>Status Guide:</b>\n"
        "✅ Active - Working normally\n"
        "⚠️ Expired - Session needs re-authentication\n"
        "🔌 Connection Error - Network/API issue (may auto-recover)\n"
        "❌ Error - Needs attention\n"
        "ℹ️ No username does NOT mean expired; active session stays active\n\n"
        "<i>Sessions are NEVER auto-deleted. You have full control.</i>"
        "</blockquote>"
    )

    return text

@dp.message(F.text == "📱 Telegram Accounts")
async def telegram_accounts_menu(message: types.Message):
    user = await users_collection.find_one({"user_id": message.from_user.id})
    if message.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID] and (not user or not user.get('is_admin', False)):
        await message.answer("⛔️ This command is only available to admins.")
        return

    # Check permission for non-major admins
    if message.from_user.id != MAJOR_ADMIN_ID and message.from_user.id != ADMIN_ID:
        user_permissions = user.get('admin_permissions', {})
        if not user_permissions.get('telegram_accounts', True):
            await message.answer("⛔️ You don't have permission to manage Telegram accounts.")
            return

    sessions = filter_live_sessions(await get_all_sessions())
    total_pages = max(1, (len(sessions) + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE)
    page = 0

    text = render_account_page(sessions, page, total_pages)

    # Navigation buttons (Previous/Next)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"acc_page:{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="➡️ Next", callback_data=f"acc_page:{page + 1}"))

    buttons_row1 = [
        InlineKeyboardButton(text="👤 Login New Account (Legacy)", callback_data="login_account")
    ]
    buttons_row2 = [
        InlineKeyboardButton(text="📝 Generate Session (New)", callback_data="generate_session"),
        InlineKeyboardButton(text="🔑 Login with String", callback_data="login_string_session")
    ]
    buttons_row3 = [
        InlineKeyboardButton(text="📦 Bulk Import (ZIP)", callback_data="bulk_import_zip")
    ]
    buttons_row4 = [
        InlineKeyboardButton(text="🗑️ Remove Account", callback_data="remove_account")
    ]
    buttons_row5 = [
        InlineKeyboardButton(text="🔑 Manage API Credentials", callback_data="manage_apis")
    ]

    # Build keyboard layout
    keyboard_layout = []
    if nav_buttons:  # Add navigation buttons if present
        keyboard_layout.append(nav_buttons)
    keyboard_layout.extend([buttons_row1, buttons_row2, buttons_row3, buttons_row4, buttons_row5])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_layout)
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("acc_page:"))
async def handle_account_pagination(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[1])
    sessions = filter_live_sessions(await get_all_sessions())
    total_pages = max(1, (len(sessions) + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE)

    # Clamp page index
    page = max(0, min(page, total_pages - 1))

    text = render_account_page(sessions, page, total_pages)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"acc_page:{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="➡️ Next", callback_data=f"acc_page:{page + 1}"))

    action_buttons_row1 = [
        InlineKeyboardButton(text="👤 Login New Account (Legacy)", callback_data="login_account")
    ]
    action_buttons_row2 = [
        InlineKeyboardButton(text="📝 Generate Session (New)", callback_data="generate_session"),
        InlineKeyboardButton(text="🔑 Login with String", callback_data="login_string_session")
    ]
    action_buttons_row3 = [
        InlineKeyboardButton(text="📦 Bulk Import (ZIP)", callback_data="bulk_import_zip")
    ]
    action_buttons_row4 = [
        InlineKeyboardButton(text="🗑️ Remove Account", callback_data="remove_account")
    ]
    action_buttons_row5 = [
        InlineKeyboardButton(text="🔑 Manage API Credentials", callback_data="manage_apis")
    ]

    keyboard_layout = []
    if nav_buttons:
        keyboard_layout.append(nav_buttons)
    keyboard_layout.extend([action_buttons_row1, action_buttons_row2, action_buttons_row3, action_buttons_row4, action_buttons_row5])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_layout)

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "generate_session_quick")
async def start_generate_session_quick(callback: types.CallbackQuery, state: FSMContext):
    """Quick session generation using default API credentials"""
    # Use default API credentials
    await state.update_data(api_id=str(API_ID), api_hash=API_HASH)

    await callback.message.answer(
        "🔑 <b>Generate Session (Quick)</b>\n\n"
        f"✅ Using Default API Credentials:\n"
        f"• API ID: <code>{API_ID}</code>\n"
        f"• API Hash: <code>{API_HASH[:8]}...</code>\n\n"
        "📱 <b>Enter your phone number</b> in international format:\n"
        "Example: <code>+919876543210</code>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(TelegramAccountStates.LOGIN_PHONE)
    await callback.answer()

@dp.callback_query(F.data == "login_account")
async def start_login_account(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "🔑 <b>Step 1: API Credentials</b>\n\n"
        "Enter your Telegram API ID:\n\n"
        "<i>Get it from https://my.telegram.org/apps</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(TelegramAccountStates.LOGIN_API_ID)
    await callback.answer()

@dp.message(TelegramAccountStates.LOGIN_API_ID)
async def process_login_api_id(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message, state)
        return

    api_id = message.text.strip()

    # Validate API ID (should be numeric)
    if not api_id.isdigit():
        await message.answer("❌ Invalid API ID. It should be numeric.\n\nPlease enter a valid API ID:")
        return

    await state.update_data(api_id=api_id)
    await message.answer(
        "🔑 <b>Step 2: API Hash</b>\n\n"
        "Enter your Telegram API Hash:\n\n"
        "<i>Get it from https://my.telegram.org/apps</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(TelegramAccountStates.LOGIN_API_HASH)

@dp.message(TelegramAccountStates.LOGIN_API_HASH)
async def process_login_api_hash(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message, state)
        return

    api_hash = message.text.strip()

    # Validate API Hash (should be alphanumeric, typically 32 characters)
    if len(api_hash) < 16:
        await message.answer("❌ Invalid API Hash. It should be at least 16 characters.\n\nPlease enter a valid API Hash:")
        return

    await state.update_data(api_hash=api_hash)
    await message.answer(
        "📱 <b>Step 3: Phone Number</b>\n\n"
        "Enter phone number in international format (e.g., +1234567890):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(TelegramAccountStates.LOGIN_PHONE)

@dp.message(TelegramAccountStates.LOGIN_PHONE)
async def process_login_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    data = await state.get_data()
    api_id = data.get('api_id')
    api_hash = data.get('api_hash')

    try:
        # Validate phone number format
        if not phone.startswith('+') or not phone[1:].isdigit():
            raise ValueError("Phone number must be in international format (e.g., +1234567890)")

        # Check for duplicate phone number in DB
        existing = await sessions_collection.find_one({"phone": phone})
        if existing:
            await message.answer(
                "⚠️ <b>This phone number is already logged in!</b>\n"
                "You cannot log in the same number twice.",
                parse_mode="HTML",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
                    resize_keyboard=True
                )
            )
            await state.clear()
            return

        # Start Telegram client with custom API credentials
        client = await create_telegram_client(api_id=api_id, api_hash=api_hash)
        await client.connect()

        # Send login code
        try:
            sent = await client.send_code_request(phone)
        except ApiIdInvalidError:
            await message.answer("❌ Invalid API ID or API Hash. Please check your credentials and try again.")
            await state.clear()
            return
        except PhoneNumberInvalidError:
            await message.answer("❌ Invalid phone number format. Please use international format.")
            await state.clear()
            return

        # Save state with API credentials
        await state.update_data(
            phone=phone,
            phone_code_hash=sent.phone_code_hash,
            session_string=client.session.save(),
            send_code_time=datetime.utcnow(),
            resend_count=0,
            api_id=api_id,
            api_hash=api_hash
        )
        await client.disconnect()

        await message.answer(
            "🔑 <b>Step 4: Verification Code</b>\n\n"
            "Enter the 5-digit verification code you received:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="↩️ Resend Code")],
                    [KeyboardButton(text="⬅️ Cancel")]
                ],
                resize_keyboard=True
            )
        )
        await state.set_state(TelegramAccountStates.LOGIN_CODE)

    except ValueError as ve:
        await message.answer(f"❌ {str(ve)}")
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}")
        await state.clear()


@dp.message(TelegramAccountStates.LOGIN_CODE)
async def process_login_code(message: types.Message, state: FSMContext):
    data = await state.get_data()
    phone = data['phone']
    phone_code_hash = data['phone_code_hash']
    session_string = data['session_string']

    # Handle resend request
    if message.text == "↩️ Resend Code":
        # Check if we've resent too many times
        if data.get('resend_count', 0) >= 3:
            await message.answer("❌ Too many resend attempts. Please start over.")
            await state.clear()
            return

        try:
            api_id = data.get('api_id')
            api_hash = data.get('api_hash')
            client = await create_telegram_client(session_string, api_id=api_id, api_hash=api_hash)
            await client.connect()
            sent = await client.send_code_request(phone)

            await state.update_data(
                phone_code_hash=sent.phone_code_hash,
                send_code_time=datetime.utcnow(),
                resend_count=data.get('resend_count', 0) + 1
            )
            await client.disconnect()
            await message.answer("🔄 New verification code sent! Please enter it:")
            return
        except Exception as e:
            await message.answer(f"❌ Error resending code: {str(e)}")
            await state.clear()
            return

    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message,state)
        return

    code = message.text.strip().replace(" ", "")  # Remove spaces if user entered "1 2 3 4 5"

    # Validate code format
    if not code.isdigit() or len(code) != 5:
        await message.answer("❌ Invalid code format. Please enter 5 digits.")
        return

    api_id = data.get('api_id')
    api_hash = data.get('api_hash')
    client = await create_telegram_client(session_string, api_id=api_id, api_hash=api_hash)
    await client.connect()

    try:
        # Check if code is expired
        if (datetime.utcnow() - data['send_code_time']).seconds > 120:
            await message.answer("⚠️ Code has expired. Please request a new one.")
            return

        # Try to sign in
        try:
            user = await client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=phone_code_hash
            )
        except PhoneCodeExpiredError:
            await message.answer("❌ Code has expired. Please request a new one.")
            return
        except PhoneCodeInvalidError:
            await message.answer("❌ Invalid code. Please try again.")
            return

        # If we get here, login was successful
        session_string = client.session.save()
        stored_phone = await store_session(
            phone,
            session_string,
            user,
            api_id=api_id,
            api_hash=api_hash
        )
        await register_client_as_active(client, user_data=user, phone=stored_phone)

        await message.answer(f"✅ Account {phone} logged in successfully with custom API credentials!")
        await state.clear()
    except SessionPasswordNeededError:
        # Save current session state before moving to password step
        await state.update_data(session_string=client.session.save())
        await client.disconnect()

        await message.answer(
            "🔒 This account has 2FA enabled. Please enter your password:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
                resize_keyboard=True
            )
        )
        await state.set_state(TelegramAccountStates.LOGIN_PASSWORD)
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}")
        await state.clear()

@dp.message(TelegramAccountStates.LOGIN_PASSWORD)
async def process_login_password(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message,state)
        return

    password = message.text.strip()
    data = await state.get_data()
    phone = data['phone']
    session_string = data['session_string']
    api_id = data.get('api_id')
    api_hash = data.get('api_hash')

    client = await create_telegram_client(session_string, api_id=api_id, api_hash=api_hash)
    await client.connect()

    try:
        try:
            user = await client.sign_in(password=password)
        except PasswordHashInvalidError:
            await message.answer("❌ Invalid password. Please try again.")
            return

        session_string = client.session.save()
        stored_phone = await store_session(
            phone,
            session_string,
            user,
            api_id=api_id,
            api_hash=api_hash
        )
        await register_client_as_active(client, user_data=user, phone=stored_phone)

        await message.answer(f"✅ Account {phone} logged in successfully with 2FA and custom API credentials!")
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}")
        await state.clear()


# ===== NEW: GENERATE SESSION HANDLERS ===== #
@dp.callback_query(F.data == "generate_session")
async def start_generate_session(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "📝 <b>Generate Telethon String Session</b>\n\n"
        "🔑 <b>Step 1: API Credentials</b>\n\n"
        "Enter your Telegram API ID:\n\n"
        "<i>Get it from https://my.telegram.org/apps</i>\n\n"
        "⚠️ <b>Note:</b> This will generate a string session that can be reused without OTP every time!",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(TelegramAccountStates.GENERATE_SESSION_API_ID)
    await callback.answer()

@dp.message(TelegramAccountStates.GENERATE_SESSION_API_ID)
async def process_generate_session_api_id(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message, state)
        return

    api_id = message.text.strip()

    # Validate API ID (should be numeric)
    if not api_id.isdigit():
        await message.answer("❌ Invalid API ID. It should be numeric.\n\nPlease enter a valid API ID:")
        return

    await state.update_data(api_id=api_id)
    await message.answer(
        "🔑 <b>Step 2: API Hash</b>\n\n"
        "Enter your Telegram API Hash:\n\n"
        "<i>Get it from https://my.telegram.org/apps</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(TelegramAccountStates.GENERATE_SESSION_API_HASH)

@dp.message(TelegramAccountStates.GENERATE_SESSION_API_HASH)
async def process_generate_session_api_hash(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message, state)
        return

    api_hash = message.text.strip()

    # Validate API Hash
    if len(api_hash) < 16:
        await message.answer("❌ Invalid API Hash. It should be at least 16 characters.\n\nPlease enter a valid API Hash:")
        return

    await state.update_data(api_hash=api_hash)
    await message.answer(
        "📱 <b>Step 3: Phone Number</b>\n\n"
        "Enter phone number in international format (e.g., +1234567890):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(TelegramAccountStates.GENERATE_SESSION_PHONE)

@dp.message(TelegramAccountStates.GENERATE_SESSION_PHONE)
async def process_generate_session_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    data = await state.get_data()
    api_id = data.get('api_id')
    api_hash = data.get('api_hash')

    try:
        # Validate phone number format
        if not phone.startswith('+') or not phone[1:].isdigit():
            raise ValueError("Phone number must be in international format (e.g., +1234567890)")

        # Check for duplicate phone number in DB
        existing = await sessions_collection.find_one({"phone": phone})
        if existing:
            await message.answer(
                "⚠️ <b>This phone number is already logged in!</b>\n"
                "You cannot log in the same number twice.",
                parse_mode="HTML",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
                    resize_keyboard=True
                )
            )
            await state.clear()
            return

        # Start Telegram client
        client = await create_telegram_client(api_id=api_id, api_hash=api_hash)
        await client.connect()

        # Send login code
        try:
            sent = await client.send_code_request(phone)
        except ApiIdInvalidError:
            await message.answer("❌ Invalid API ID or API Hash. Please check your credentials and try again.")
            await state.clear()
            return
        except PhoneNumberInvalidError:
            await message.answer("❌ Invalid phone number format. Please use international format.")
            await state.clear()
            return

        # Save state
        await state.update_data(
            phone=phone,
            phone_code_hash=sent.phone_code_hash,
            session_string=client.session.save(),
            send_code_time=datetime.utcnow(),
            api_id=api_id,
            api_hash=api_hash
        )
        await client.disconnect()

        await message.answer(
            "🔑 <b>Step 4: Verification Code</b>\n\n"
            "Enter the 5-digit verification code you received:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
                resize_keyboard=True
            )
        )
        await state.set_state(TelegramAccountStates.GENERATE_SESSION_CODE)

    except ValueError as ve:
        await message.answer(f"❌ {str(ve)}")
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}")
        await state.clear()

@dp.message(TelegramAccountStates.GENERATE_SESSION_CODE)
async def process_generate_session_code(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message, state)
        return

    code = message.text.strip().replace(" ", "")
    data = await state.get_data()
    phone = data['phone']
    phone_code_hash = data['phone_code_hash']
    session_string = data['session_string']
    api_id = data.get('api_id')
    api_hash = data.get('api_hash')

    # Validate code format
    if not code.isdigit() or len(code) != 5:
        await message.answer("❌ Invalid code format. Please enter 5 digits.")
        return

    client = await create_telegram_client(session_string, api_id=api_id, api_hash=api_hash)
    await client.connect()

    try:
        # Check if code is expired
        if (datetime.utcnow() - data['send_code_time']).seconds > 120:
            await message.answer("⚠️ Code has expired. Please start over.")
            await state.clear()
            return

        # Try to sign in
        try:
            user = await client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=phone_code_hash
            )
        except PhoneCodeExpiredError:
            await message.answer("❌ Code has expired. Please start over.")
            await state.clear()
            return
        except PhoneCodeInvalidError:
            await message.answer("❌ Invalid code. Please try again.")
            return

        # Success! Generate and display string session
        final_session_string = client.session.save()

        # Store in database
        stored_phone = await store_session(
            phone,
            final_session_string,
            user,
            api_id=api_id,
            api_hash=api_hash
        )
        await register_client_as_active(client, user_data=user, phone=stored_phone)

        # Display the string session to user
        await message.answer(
            f"✅ <b>Session Generated Successfully!</b>\n\n"
            f"📱 <b>Phone:</b> {phone}\n"
            f"👤 <b>Name:</b> {user.first_name or 'N/A'}\n"
            f"🆔 <b>Username:</b> @{user.username or 'N/A'}\n\n"
            f"🔑 <b>Your String Session:</b>\n"
            f"<code>{final_session_string}</code>\n\n"
            f"⚠️ <b>IMPORTANT:</b>\n"
            f"• Save this string session securely\n"
            f"• You can use it to login directly without OTP\n"
            f"• Never share this with anyone\n"
            f"• Account has been added to active clients",
            parse_mode="HTML"
        )
        await state.clear()

    except SessionPasswordNeededError:
        # Save session state before moving to password step
        await state.update_data(session_string=client.session.save())
        await client.disconnect()

        await message.answer(
            "🔒 This account has 2FA enabled. Please enter your password:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
                resize_keyboard=True
            )
        )
        await state.set_state(TelegramAccountStates.GENERATE_SESSION_PASSWORD)
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}")
        await state.clear()

@dp.message(TelegramAccountStates.GENERATE_SESSION_PASSWORD)
async def process_generate_session_password(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message, state)
        return

    password = message.text.strip()
    data = await state.get_data()
    phone = data['phone']
    session_string = data['session_string']
    api_id = data.get('api_id')
    api_hash = data.get('api_hash')

    client = await create_telegram_client(session_string, api_id=api_id, api_hash=api_hash)
    await client.connect()

    try:
        try:
            user = await client.sign_in(password=password)
        except PasswordHashInvalidError:
            await message.answer("❌ Invalid password. Please try again.")
            return

        # Success! Generate and display string session
        final_session_string = client.session.save()

        # Store in database
        stored_phone = await store_session(
            phone,
            final_session_string,
            user,
            api_id=api_id,
            api_hash=api_hash
        )
        await register_client_as_active(client, user_data=user, phone=stored_phone)

        # Display the string session to user
        await message.answer(
            f"✅ <b>Session Generated Successfully (with 2FA)!</b>\n\n"
            f"📱 <b>Phone:</b> {phone}\n"
            f"👤 <b>Name:</b> {user.first_name or 'N/A'}\n"
            f"🆔 <b>Username:</b> @{user.username or 'N/A'}\n\n"
            f"🔑 <b>Your String Session:</b>\n"
            f"<code>{final_session_string}</code>\n\n"
            f"⚠️ <b>IMPORTANT:</b>\n"
            f"• Save this string session securely\n"
            f"• You can use it to login directly without OTP\n"
            f"• Never share this with anyone\n"
            f"• Account has been added to active clients",
            parse_mode="HTML"
        )
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}")
        await state.clear()


# ===== NEW: LOGIN WITH STRING SESSION HANDLERS ===== #
@dp.callback_query(F.data == "login_string_session")
async def start_login_string_session(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "🔑 <b>Login with String Session</b>\n\n"
        "📝 <b>Step 1: String Session</b>\n\n"
        "Paste your Telethon string session here:\n\n"
        "<i>This is the long string you generated earlier</i>\n\n"
        "⚠️ <b>Note:</b> Make sure you have the correct API ID and API Hash that were used to generate this session!",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(TelegramAccountStates.LOGIN_STRING_SESSION_INPUT)
    await callback.answer()

@dp.message(TelegramAccountStates.LOGIN_STRING_SESSION_INPUT)
async def process_login_string_session_input(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message, state)
        return

    session_string = message.text.strip()

    # Basic validation - Telethon string sessions are long base64 strings
    if len(session_string) < 100:
        await message.answer("❌ Invalid string session. It should be a long base64 encoded string.\n\nPlease paste a valid string session:")
        return

    await state.update_data(session_string=session_string)
    await message.answer(
        "🔑 <b>Step 2: API ID</b>\n\n"
        "Enter the API ID that was used to generate this session:\n\n"
        "<i>This must match the API ID used during session generation</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(TelegramAccountStates.LOGIN_STRING_SESSION_API_ID)

@dp.message(TelegramAccountStates.LOGIN_STRING_SESSION_API_ID)
async def process_login_string_session_api_id(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message, state)
        return

    api_id = message.text.strip()

    # Validate API ID
    if not api_id.isdigit():
        await message.answer("❌ Invalid API ID. It should be numeric.\n\nPlease enter a valid API ID:")
        return

    await state.update_data(api_id=api_id)
    await message.answer(
        "🔑 <b>Step 3: API Hash</b>\n\n"
        "Enter the API Hash that was used to generate this session:\n\n"
        "<i>This must match the API Hash used during session generation</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(TelegramAccountStates.LOGIN_STRING_SESSION_API_HASH)

@dp.message(TelegramAccountStates.LOGIN_STRING_SESSION_API_HASH)
async def process_login_string_session_api_hash(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message, state)
        return

    api_hash = message.text.strip()
    data = await state.get_data()
    session_string = data['session_string']
    api_id = data.get('api_id')

    # Validate API Hash
    if len(api_hash) < 16:
        await message.answer("❌ Invalid API Hash. It should be at least 16 characters.\n\nPlease enter a valid API Hash:")
        return

    try:
        # Create client with the string session
        client = await create_telegram_client(session_string=session_string, api_id=api_id, api_hash=api_hash)

        await message.answer("🔄 Connecting and validating session...")

        await client.connect()

        # Check if authorized
        if not await client.is_user_authorized():
            await message.answer(
                "❌ <b>Session is not authorized or expired!</b>\n\n"
                "This can happen if:\n"
                "• The session has expired\n"
                "• Wrong API ID/Hash combination\n"
                "• Session was revoked\n\n"
                "Please generate a new session or use the legacy login method.",
                parse_mode="HTML"
            )
            await client.disconnect()
            await state.clear()
            return

        # Get user info
        try:
            user = await client.get_me()
            phone = normalize_session_phone(user.phone, user_id=user.id)

            # Check for duplicate
            existing = await sessions_collection.find_one({"phone": phone})
            if existing:
                await message.answer(
                    f"⚠️ <b>This phone number ({phone}) is already logged in!</b>\n"
                    "You cannot log in the same number twice.",
                    parse_mode="HTML"
                )
                await client.disconnect()
                await state.clear()
                return

            # Store session
            stored_phone = await store_session(
                phone,
                session_string,
                user,
                api_id=api_id,
                api_hash=api_hash
            )
            await register_client_as_active(client, user_data=user, phone=stored_phone)

            await message.answer(
                f"✅ <b>Successfully logged in with string session!</b>\n\n"
                f"📱 <b>Phone:</b> {phone}\n"
                f"👤 <b>Name:</b> {user.first_name or 'N/A'}\n"
                f"🆔 <b>Username:</b> @{user.username or 'N/A'}\n"
                f"🆔 <b>User ID:</b> {user.id}\n\n"
                f"✅ Account has been added to active clients!",
                parse_mode="HTML"
            )
            await state.clear()

        except Exception as e:
            await message.answer(f"❌ Error getting user info: {str(e)}")
            await client.disconnect()
            await state.clear()

    except Exception as e:
        await message.answer(
            f"❌ <b>Failed to connect with string session!</b>\n\n"
            f"Error: {str(e)}\n\n"
            "Please check:\n"
            "• String session is correct and complete\n"
            "• API ID and API Hash match the ones used during generation\n"
            "• Session hasn't expired or been revoked",
            parse_mode="HTML"
        )
        await state.clear()


@dp.callback_query(F.data == "refresh_accounts")
async def check_logged_in_accounts(callback: types.CallbackQuery):
    sessions = filter_live_sessions(await get_all_sessions())
    if not sessions:
        await callback.answer("No live accounts connected right now.", show_alert=True)
        return

    # Refresh only the UI view. Do NOT reconnect/reload sessions repeatedly.
    total_pages = max(1, (len(sessions) + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE)
    page = 0

    text = render_account_page(sessions, page, total_pages)

    # Navigation buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"acc_page:{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="➡️ Next", callback_data=f"acc_page:{page + 1}"))

    action_buttons_row1 = [
        InlineKeyboardButton(text="👤 Login New Account (Legacy)", callback_data="login_account")
    ]
    action_buttons_row2 = [
        InlineKeyboardButton(text="📝 Generate Session (New)", callback_data="generate_session"),
        InlineKeyboardButton(text="🔑 Login with String", callback_data="login_string_session")
    ]
    action_buttons_row3 = [
        InlineKeyboardButton(text="📦 Bulk Import (ZIP)", callback_data="bulk_import_zip")
    ]
    action_buttons_row4 = [
        InlineKeyboardButton(text="🗑️ Remove Account", callback_data="remove_account")
    ]
    action_buttons_row5 = [
        InlineKeyboardButton(text="🔑 Manage API Credentials", callback_data="manage_apis")
    ]

    keyboard_layout = []
    if nav_buttons:
        keyboard_layout.append(nav_buttons)
    keyboard_layout.extend([action_buttons_row1, action_buttons_row2, action_buttons_row3, action_buttons_row4, action_buttons_row5])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_layout)

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer("ℹ️ Session reload disabled. Using initially loaded active clients.", show_alert=True)



# ===== BULK ZIP IMPORT HANDLERS ===== #
@dp.callback_query(F.data == "bulk_import_zip")
async def start_bulk_import(callback: types.CallbackQuery, state: FSMContext):
    """Start bulk import process"""
    await state.set_state(TelegramAccountStates.BULK_IMPORT_ZIP)

    await callback.message.answer(
        "📦 <b>Bulk Session Import</b>\n\n"
        "📁 Please send me a ZIP file containing your Telegram session files (.session files).\n\n"
        "ℹ️ <b>Requirements:</b>\n"
        "• File must be a ZIP archive\n"
        "• Maximum 1000 sessions per ZIP\n"
        "• Sessions will be imported with 20-25 sec intervals\n"
        "• You'll receive notifications for each session\n\n"
        "⏳ Send the ZIP file now...",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(TelegramAccountStates.BULK_IMPORT_ZIP, F.document)
async def handle_bulk_import_zip(message: types.Message, state: FSMContext):
    """Handle ZIP file upload and process sessions"""
    import zipfile
    import tempfile
    import shutil
    import glob

    # Check if it's a ZIP file
    if not message.document.file_name.lower().endswith('.zip'):
        await message.answer(
            "❌ <b>Invalid File Type</b>\n\n"
            "Please send a valid ZIP file containing .session files.",
            parse_mode="HTML"
        )
        return

    # Check file size (max 50MB)
    max_size = 50 * 1024 * 1024  # 50MB in bytes
    if message.document.file_size > max_size:
        await message.answer(
            f"❌ <b>File Too Large</b>\n\n"
            f"Maximum file size: 50MB\n"
            f"Your file: {message.document.file_size / (1024*1024):.2f}MB",
            parse_mode="HTML"
        )
        return

    # Send initial processing message
    processing_msg = await message.answer(
        "⏳ <b>Processing ZIP file...</b>\n\n"
        "📥 Downloading and extracting sessions...",
        parse_mode="HTML"
    )

    try:
        # Create temp directory
        temp_dir = tempfile.mkdtemp(prefix="telegram_sessions_")

        # Download the ZIP file
        file = await bot.get_file(message.document.file_id)
        zip_path = f"{temp_dir}/sessions.zip"
        await bot.download_file(file.file_path, zip_path)

        # Extract ZIP
        extract_dir = f"{temp_dir}/extracted"
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        # Find all .session files
        session_files = []
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    session_files.append(os.path.join(root, file))

        if not session_files:
            await processing_msg.edit_text(
                "❌ <b>No Session Files Found</b>\n\n"
                "The ZIP file doesn't contain any .session files.\n"
                "Please check your ZIP and try again.",
                parse_mode="HTML"
            )
            shutil.rmtree(temp_dir)
            await state.clear()
            return

        # Check session count
        if len(session_files) > 1000:
            await processing_msg.edit_text(
                f"❌ <b>Too Many Sessions</b>\n\n"
                f"Found: {len(session_files)} sessions\n"
                f"Maximum: 1000 sessions per ZIP\n\n"
                f"Please split into smaller batches.",
                parse_mode="HTML"
            )
            shutil.rmtree(temp_dir)
            await state.clear()
            return

        # Fetch stored API credentials for distribution
        all_apis = await get_all_api_credentials()
        total_session_count = len(session_files)
        api_info_text = (
            f"🔑 Using {len(all_apis)} API credential(s) for distribution"
            if all_apis
            else "🔑 Using default API credentials (no custom APIs stored)"
        )

        # Update message with count
        await processing_msg.edit_text(
            f"✅ <b>Found {len(session_files)} Session Files</b>\n\n"
            f"🚀 Starting bulk import...\n"
            f"⏱️ Estimated time: {len(session_files) * 23 // 60} minutes\n"
            f"{api_info_text}\n\n"
            f"📊 Progress: 0/{len(session_files)}",
            parse_mode="HTML"
        )

        # Process each session with delay
        success_count = 0
        failed_count = 0
        failed_sessions = []

        for idx, session_file in enumerate(session_files, 1):
            session_name = os.path.basename(session_file).replace('.session', '')
            sessions_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
            os.makedirs(sessions_dir, exist_ok=True)

            session_name_clean = session_name
            dest_path = os.path.join(sessions_dir, f"{session_name_clean}.session")
            client = None
            imported_ok = False

            # Determine which API credentials to use for this session (0-based index)
            if all_apis:
                api_cred = get_api_for_session_index(idx - 1, all_apis, total_session_count)
                use_api_id = api_cred["api_id"]
                use_api_hash = api_cred["api_hash"]
            else:
                use_api_id = API_ID
                use_api_hash = API_HASH

            # Always replace stale file before validating
            if os.path.exists(dest_path):
                os.remove(dest_path)
            shutil.copy2(session_file, dest_path)

            for attempt in range(2):
                try:
                    client = await create_telegram_client_from_file(
                        os.path.join(sessions_dir, session_name_clean),
                        api_id=use_api_id,
                        api_hash=use_api_hash
                    )
                    await client.connect()

                    if not await client.is_user_authorized():
                        raise PermissionError("Session unauthorized (explicit auth check failed)")

                    me = await client.get_me()
                    normalized_phone = normalize_session_phone(me.phone, session_name=session_name_clean, user_id=me.id)

                    # If same number already exists with old session, replace old file and keep latest session.
                    existing_doc = await sessions_collection.find_one({"phone": normalized_phone})
                    if existing_doc and existing_doc.get("session_name") and existing_doc.get("session_name") != session_name_clean:
                        remove_session_file_by_name(existing_doc.get("session_name"))

                    stored_phone = await store_session(
                        phone=normalized_phone,
                        session_string=client.session.save(),
                        user_data=me,
                        api_id=use_api_id,
                        api_hash=use_api_hash,
                        session_name=session_name_clean,
                        status="active"
                    )

                    await register_client_as_active(
                        client,
                        user_data=me,
                        phone=stored_phone,
                        session_name=session_name_clean
                    )

                    name = me.first_name or "No name"
                    username = f"@{me.username}" if me.username else "No username"

                    success_count += 1
                    imported_ok = True

                    await message.answer(
                        f"✅ <b>Session {idx}/{len(session_files)} Success</b>\n\n"
                        f"🆔 Session ID: <code>{session_name_clean}</code>\n"
                        f"👤 Name: {name}\n"
                        f"📱 Username: {username}\n"
                        f"☎️ Account: {stored_phone}\n"
                        f"🔑 API ID: <code>{use_api_id}</code>\n"
                        f"🟢 Status: Active\n\n"
                        f"✅ Session validated and added.",
                        parse_mode="HTML"
                    )
                    break

                except PermissionError as auth_error:
                    failed_count += 1
                    auth_msg = str(auth_error)
                    failed_sessions.append({
                        'name': session_name,
                        'error': auth_msg
                    })

                    placeholder_phone = normalize_session_phone(None, session_name=session_name_clean)
                    await sessions_collection.update_one(
                        {"phone": placeholder_phone},
                        {
                            "$set": {
                                "session_name": session_name_clean,
                                "session_label": build_session_label(session_name=session_name_clean),
                                "status": "unauthorized",
                                "last_error": auth_msg,
                                "last_check": datetime.utcnow(),
                                "updated_at": datetime.utcnow()
                            },
                            "$setOnInsert": {"created_at": datetime.utcnow()}
                        },
                        upsert=True
                    )

                    if client:
                        try:
                            if client.is_connected():
                                await client.disconnect()
                        except Exception:
                            pass

                    remove_session_file_by_name(session_name_clean)

                    await message.answer(
                        f"❌ <b>Session {idx}/{len(session_files)} Failed</b>\n\n"
                        f"📝 Name: <code>{session_name}</code>\n"
                        f"❗ Error: {auth_msg}\n\n"
                        f"This session is not authorized.",
                        parse_mode="HTML"
                    )
                    break

                except Exception as e:
                    err_text = str(e)
                    if client:
                        try:
                            if client.is_connected():
                                await client.disconnect()
                        except Exception:
                            pass

                    should_retry = attempt == 0 and should_cleanup_and_retry(err_text)
                    if should_retry:
                        # Cleanup and retry once for duplicate/IP/same-number conflicts
                        remove_session_file_by_name(session_name_clean)
                        if os.path.exists(dest_path):
                            os.remove(dest_path)
                        shutil.copy2(session_file, dest_path)
                        await sessions_collection.delete_many({"session_name": session_name_clean, "status": {"$ne": "active"}})
                        await asyncio.sleep(1.5)
                        continue

                    failed_count += 1
                    error_msg = err_text[:120]
                    failed_sessions.append({
                        'name': session_name,
                        'error': error_msg
                    })

                    await message.answer(
                        f"❌ <b>Session {idx}/{len(session_files)} Failed</b>\n\n"
                        f"📝 Name: <code>{session_name}</code>\n"
                        f"❗ Error: {error_msg}\n\n"
                        f"Check the session file and try again.",
                        parse_mode="HTML"
                    )
                    break

            if not imported_ok and os.path.exists(dest_path):
                # Keep only validated active sessions in sessions directory
                if not await sessions_collection.find_one({"session_name": session_name_clean, "status": "active"}):
                    os.remove(dest_path)

            # Update progress
            if idx % 5 == 0 or idx == len(session_files):
                await processing_msg.edit_text(
                    f"⏳ <b>Bulk Import in Progress</b>\n\n"
                    f"📊 Progress: {idx}/{len(session_files)}\n"
                    f"✅ Success: {success_count}\n"
                    f"❌ Failed: {failed_count}\n\n"
                    f"⏱️ Processing...",
                    parse_mode="HTML"
                )

            # Wait before next session (20-25 seconds)
            if idx < len(session_files):
                await asyncio.sleep(random.uniform(20, 25))

        # Run rebalance to ensure even distribution is persisted to DB
        if all_apis and success_count > 0:
            total_s, total_a, dist_text = await rebalance_sessions_across_apis()
            rebalance_summary = (
                f"\n\n🔑 <b>API Distribution ({total_a} APIs, {total_s} sessions):</b>\n"
                f"{dist_text}"
            )
        else:
            rebalance_summary = ""

        # Final summary
        summary_text = (
            f"🎉 <b>Bulk Import Complete!</b>\n\n"
            f"📊 <b>Summary:</b>\n"
            f"• Total Sessions: {len(session_files)}\n"
            f"• ✅ Successfully Imported: {success_count}\n"
            f"• ❌ Failed: {failed_count}\n"
            f"• 🔥 Active Clients: {len(get_active_clients())}\n"
            f"• 🔑 API Credentials Used: {len(all_apis) if all_apis else 'Default'}\n"
        )

        if failed_sessions:
            summary_text += f"\n⚠️ <b>Failed Sessions:</b>\n"
            for fs in failed_sessions[:10]:  # Show first 10
                summary_text += f"• {fs['name']}: {fs['error'][:50]}\n"

            if len(failed_sessions) > 10:
                summary_text += f"\n... and {len(failed_sessions) - 10} more\n"

        summary_text += f"\n✅ All successful sessions are now active and monitoring channels!"
        summary_text += rebalance_summary

        await message.answer(summary_text, parse_mode="HTML")

        # Send notification to admin
        await bot.send_message(
            ADMIN_ID,
            f"📦 <b>Bulk Import Completed</b>\n\n"
            f"👤 User: {message.from_user.username or message.from_user.first_name}\n"
            f"🆔 User ID: <code>{message.from_user.id}</code>\n\n"
            f"📊 Results:\n"
            f"• Total: {len(session_files)}\n"
            f"• Success: {success_count}\n"
            f"• Failed: {failed_count}",
            parse_mode="HTML"
        )

        # Cleanup
        shutil.rmtree(temp_dir)
        await processing_msg.delete()

    except zipfile.BadZipFile:
        await processing_msg.edit_text(
            "❌ <b>Invalid ZIP File</b>\n\n"
            "The file is corrupted or not a valid ZIP archive.",
            parse_mode="HTML"
        )
    except Exception as e:
        await processing_msg.edit_text(
            f"❌ <b>Error Processing ZIP</b>\n\n"
            f"Error: {str(e)[:200]}\n\n"
            f"Please try again or contact support.",
            parse_mode="HTML"
        )
        print(f"Bulk import error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await state.clear()


@dp.message(TelegramAccountStates.BULK_IMPORT_ZIP)
async def bulk_import_invalid_input(message: types.Message):
    """Handle invalid input during bulk import"""
    await message.answer(
        "❌ <b>Invalid Input</b>\n\n"
        "Please send a ZIP file containing .session files.\n\n"
        "Or send /cancel to abort.",
        parse_mode="HTML"
    )



# ===== API CREDENTIALS MANAGEMENT HANDLERS ===== #

async def send_manage_apis_menu(target, edit=False):
    """Send (or edit) the API credentials management menu."""
    all_apis = await get_all_api_credentials()
    total = len(all_apis)

    if total == 0:
        text = (
            "🔑 <b>API Credentials Manager</b>\n\n"
            "No custom API credentials stored yet.\n\n"
            "Add your Telegram API ID and HASH pairs below.\n"
            "Sessions will be automatically distributed evenly across all stored APIs.\n\n"
            "<i>Default API is used when no custom credentials are stored.</i>"
        )
    else:
        lines = []
        for i, cred in enumerate(all_apis, 1):
            lines.append(
                f"{i}. <code>{cred['api_id']}</code> — {cred.get('session_count', 0)} sessions assigned"
            )
        text = (
            f"🔑 <b>API Credentials Manager</b>\n\n"
            f"<b>Stored APIs ({total}):</b>\n" +
            "\n".join(lines) +
            "\n\n<i>Sessions are distributed evenly across all APIs during ZIP import.</i>"
        )

    # Build keyboard: one row per API with delete button, then action rows
    inline_rows = []
    for cred in all_apis:
        cred_id = str(cred["_id"])
        inline_rows.append([
            InlineKeyboardButton(
                text=f"🗑️ Delete API {cred['api_id']}",
                callback_data=f"del_api:{cred_id}"
            )
        ])

    inline_rows.append([
        InlineKeyboardButton(text="➕ Add New API", callback_data="add_api")
    ])
    if all_apis:
        inline_rows.append([
            InlineKeyboardButton(text="⚖️ Rebalance Sessions", callback_data="rebalance_apis")
        ])
    inline_rows.append([
        InlineKeyboardButton(text="🔙 Back to Accounts", callback_data="manage_accounts")
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=inline_rows)
    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=keyboard)


@dp.callback_query(F.data == "manage_apis")
async def manage_apis_handler(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID]:
        await callback.answer("⛔ Admin only.", show_alert=True)
        return
    await state.clear()
    await send_manage_apis_menu(callback.message, edit=True)
    await callback.answer()


@dp.callback_query(F.data == "add_api")
async def add_api_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID]:
        await callback.answer("⛔ Admin only.", show_alert=True)
        return
    await state.set_state(TelegramAccountStates.ADD_API_ID)
    await callback.message.edit_text(
        "🔑 <b>Add New API Credentials</b>\n\n"
        "<b>Step 1/2 — Enter API ID:</b>\n\n"
        "Send your Telegram API ID (numbers only).\n"
        "Get it from <a href='https://my.telegram.org'>my.telegram.org</a>\n\n"
        "Send /cancel to abort.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Cancel", callback_data="manage_apis")
        ]])
    )
    await callback.answer()


@dp.message(TelegramAccountStates.ADD_API_ID)
async def add_api_id_received(message: types.Message, state: FSMContext):
    if message.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID]:
        return
    raw = (message.text or "").strip()
    if raw == "/cancel":
        await state.clear()
        await send_manage_apis_menu(message)
        return
    if not raw.isdigit():
        await message.answer(
            "❌ <b>Invalid API ID</b>\n\n"
            "The API ID must contain numbers only. Please try again.\n"
            "Send /cancel to abort.",
            parse_mode="HTML"
        )
        return
    await state.update_data(new_api_id=int(raw))
    await state.set_state(TelegramAccountStates.ADD_API_HASH)
    await message.answer(
        "🔑 <b>Add New API Credentials</b>\n\n"
        "<b>Step 2/2 — Enter API HASH:</b>\n\n"
        "Send your Telegram API HASH (32-character hex string).\n\n"
        "Send /cancel to abort.",
        parse_mode="HTML"
    )


@dp.message(TelegramAccountStates.ADD_API_HASH)
async def add_api_hash_received(message: types.Message, state: FSMContext):
    if message.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID]:
        return
    raw = (message.text or "").strip()
    if raw == "/cancel":
        await state.clear()
        await send_manage_apis_menu(message)
        return

    # Basic validation: 32-char hex string
    import re as _re
    if not _re.fullmatch(r"[0-9a-fA-F]{32}", raw):
        await message.answer(
            "❌ <b>Invalid API HASH</b>\n\n"
            "The API HASH must be a 32-character hexadecimal string.\n"
            "Please try again or send /cancel to abort.",
            parse_mode="HTML"
        )
        return

    data = await state.get_data()
    new_api_id = data.get("new_api_id")
    new_api_hash = raw.lower()

    # Check for duplicates
    existing = await api_credentials_collection.find_one({"api_id": new_api_id})
    if existing:
        await state.clear()
        await message.answer(
            f"⚠️ <b>Duplicate API ID</b>\n\n"
            f"API ID <code>{new_api_id}</code> already exists.\n"
            f"Delete it first if you want to replace it.",
            parse_mode="HTML"
        )
        await send_manage_apis_menu(message)
        return

    cred_id = await add_api_credential(new_api_id, new_api_hash)
    await state.clear()

    await message.answer(
        f"✅ <b>API Credential Added!</b>\n\n"
        f"🆔 API ID: <code>{new_api_id}</code>\n"
        f"🔑 API HASH: <code>{new_api_hash}</code>\n\n"
        f"This API will be used to distribute sessions on next ZIP import.",
        parse_mode="HTML"
    )
    await send_manage_apis_menu(message)


@dp.callback_query(F.data.startswith("del_api:"))
async def delete_api_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID]:
        await callback.answer("⛔ Admin only.", show_alert=True)
        return

    cred_id = callback.data.split(":", 1)[1]
    cred = await api_credentials_collection.find_one({"_id": ObjectId(cred_id)})
    if not cred:
        await callback.answer("⚠️ API credential not found.", show_alert=True)
        await send_manage_apis_menu(callback.message, edit=True)
        return

    await delete_api_credential(cred_id)
    await callback.answer(f"✅ Deleted API ID {cred['api_id']}", show_alert=True)
    await send_manage_apis_menu(callback.message, edit=True)


@dp.callback_query(F.data == "rebalance_apis")
async def rebalance_apis_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID]:
        await callback.answer("⛔ Admin only.", show_alert=True)
        return

    await callback.answer("⚖️ Rebalancing sessions...", show_alert=False)
    await callback.message.edit_text(
        "⏳ <b>Rebalancing sessions across APIs...</b>\n\nPlease wait.",
        parse_mode="HTML"
    )

    total_sessions, total_apis, dist_text = await rebalance_sessions_across_apis()

    if total_apis == 0:
        result_text = (
            "⚠️ <b>No API credentials stored.</b>\n\n"
            "Add at least one API credential first."
        )
    elif total_sessions == 0:
        result_text = "ℹ️ <b>No sessions to distribute.</b>"
    else:
        result_text = (
            f"✅ <b>Rebalance Complete!</b>\n\n"
            f"🔢 Total Sessions: {total_sessions}\n"
            f"🔑 Total APIs: {total_apis}\n\n"
            f"<b>Distribution:</b>\n{dist_text}"
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔙 Back to API Manager", callback_data="manage_apis")
    ]])
    await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=keyboard)


@dp.callback_query(F.data == "manage_accounts")
async def manage_accounts_callback(callback: types.CallbackQuery):
    """Back to Accounts button — re-renders the Telegram Accounts page via callback edit."""
    if callback.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID]:
        user = await users_collection.find_one({"user_id": callback.from_user.id})
        if not user or not user.get("is_admin", False):
            await callback.answer("⛔ Admin only.", show_alert=True)
            return

    sessions = filter_live_sessions(await get_all_sessions())
    total_pages = max(1, (len(sessions) + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE)
    page = 0
    text = render_account_page(sessions, page, total_pages)

    nav_buttons = []
    if total_pages > 1:
        nav_buttons.append(InlineKeyboardButton(text="➡️ Next", callback_data="acc_page:1"))

    keyboard_layout = []
    if nav_buttons:
        keyboard_layout.append(nav_buttons)
    keyboard_layout.extend([
        [InlineKeyboardButton(text="👤 Login New Account (Legacy)", callback_data="login_account")],
        [
            InlineKeyboardButton(text="📝 Generate Session (New)", callback_data="generate_session"),
            InlineKeyboardButton(text="🔑 Login with String",      callback_data="login_string_session")
        ],
        [InlineKeyboardButton(text="📦 Bulk Import (ZIP)",         callback_data="bulk_import_zip")],
        [InlineKeyboardButton(text="🗑️ Remove Account",            callback_data="remove_account")],
        [InlineKeyboardButton(text="🔑 Manage API Credentials",    callback_data="manage_apis")]
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_layout)

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("remove_session:"))
async def remove_session_handler(callback: types.CallbackQuery):
    session_id = callback.data.split(":")[1]

    # Get session info before deletion
    session = await sessions_collection.find_one({"_id": ObjectId(session_id)})
    if not session:
        await callback.answer("⚠️ Session not found.", show_alert=True)
        return

    # Delete the session
    result = await sessions_collection.delete_one({"_id": ObjectId(session_id)})

    if result.deleted_count:
        # Remove from active memory without full reload
        await disconnect_active_client_by_identity(
            phone=session.get('phone'),
            user_id=session.get('user_id')
        )
        remove_session_file_by_name(session.get('session_name'))

        await callback.answer("✅ Account removed successfully.")
        await callback.message.edit_text(
            f"❌ <b>Account Removed</b>\n\n"
            f"📞 Phone/Session: {get_session_display_label(session)}\n"
            f"👤 Username: {get_session_username_text(session)}\n\n"
            f"This account has been removed from the system.",
            parse_mode="HTML"
        )
    else:
        await callback.answer("⚠️ Failed to remove account.", show_alert=True)


@dp.callback_query(F.data == "remove_account")
async def start_remove_account(callback: types.CallbackQuery, state: FSMContext):
    sessions = await get_all_sessions()

    if not sessions:
        await callback.answer("No accounts to remove.")
        return

    # Build text with status indicators
    text = "📱 <b>Select an account to remove:</b>\n\n"
    for i, session in enumerate(sessions, 1):
        status_emoji, status_text = get_session_status_meta(session)
        text += f"{i}. {get_session_display_label(session)} ({get_session_username_text(session)}) {status_emoji} {status_text}\n"

    text += (
        "\n<blockquote expandable>"
        "💡 <b>Status Guide:</b>\n"
        "✅ Active - Working normally\n"
        "⚠️ Expired - Session needs re-authentication\n"
        "🔌 Connection Error - Network/API issue (may auto-recover)\n"
        "❌ Error - Needs attention\n\n"
        "<i>Sessions are NEVER auto-deleted. You have full control.</i>"
        "</blockquote>"
    )

    builder = ReplyKeyboardBuilder()
    for session in sessions:
        status_emoji, _ = get_session_status_meta(session)
        button_text = f"{get_session_display_label(session)} {status_emoji}"
        builder.add(KeyboardButton(text=button_text))
    builder.adjust(2)
    builder.row(KeyboardButton(text="⬅️ Cancel"))

    await callback.message.answer(
        text,
        parse_mode="HTML",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(TelegramAccountStates.CONFIRM_REMOVE_ACCOUNT)
    await callback.answer()

@dp.message(F.text == "🗑️ Remove Account")
async def remove_account_from_admin(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    sessions = await get_all_sessions()

    if not sessions:
        await message.answer("❌ No accounts found to remove.")
        await admin_command(message, state)
        return

    text = "📱 <b>Telegram Accounts to Remove:</b>\n\n"
    for i, session in enumerate(sessions, 1):
        status_emoji, status_text = get_session_status_meta(session)
        text += (
            f"{i}. {get_session_display_label(session)} - "
            f"{get_session_username_text(session)} {status_emoji} {status_text}\n"
        )

    text += (
        "\n<blockquote expandable>"
        "💡 <b>Status Guide:</b>\n"
        "✅ Active - Working normally\n"
        "⚠️ Expired - Session needs re-authentication\n"
        "🔌 Connection Error - Network/API issue (may auto-recover)\n"
        "❌ Error - Needs attention\n\n"
        "<i>Sessions are NEVER auto-deleted. You have full control.</i>"
        "</blockquote>\n\n"
    )
    text += "📞 <b>Enter the phone/session ID you want to remove:</b>\n<i>(Example: +1234567890 or session:abc123)</i>"

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(TelegramAccountStates.CONFIRM_REMOVE_ACCOUNT)

@dp.message(TelegramAccountStates.CONFIRM_REMOVE_ACCOUNT)
async def confirm_remove_account(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message,state)
        return

    # Extract phone/session id (button text format: "<id> <emoji>")
    raw_value = message.text.strip()
    phone = raw_value.split()[0] if raw_value else ""

    session = await sessions_collection.find_one({"phone": phone})

    # Backward-compatible fallback for plain manual phone input formatting
    if not session:
        numeric_phone = ''.join(c for c in raw_value if c.isdigit() or c == '+')
        if numeric_phone:
            phone = numeric_phone
            session = await sessions_collection.find_one({"phone": phone})

    if not session:
        await message.answer(
            f"❌ Account <code>{phone}</code> not found in database.\n\n"
            "Please check the phone number and try again.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
                resize_keyboard=True
            )
        )
        return

    await state.update_data(phone=phone)

    status_emoji, status_text = get_session_status_meta(session)

    confirmation_text = (
        f"⚠️ <b>Confirm Account Removal</b>\n\n"
        f"📞 <b>Phone/Session:</b> <code>{get_session_display_label(session)}</code>\n"
        f"👤 <b>Name:</b> {session.get('first_name', 'N/A')} {session.get('last_name', '')}\n"
        f"🆔 <b>Username:</b> {get_session_username_text(session)}\n"
        f"🆔 <b>User ID:</b> <code>{session.get('user_id', 'N/A')}</code>\n"
        f"📊 <b>Status:</b> {status_emoji} {status_text}\n"
        f"📅 <b>Added:</b> {session['created_at'].strftime('%Y-%m-%d %H:%M')}\n\n"
        f"❗️ This will permanently remove this account from the bot.\n"
        f"Are you sure you want to proceed?"
    )

    await message.answer(
        confirmation_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, Remove", callback_data="confirm_remove_yes")],
            [InlineKeyboardButton(text="❌ No, Keep It", callback_data="confirm_remove_no")]
        ])
    )

@dp.callback_query(F.data == "confirm_remove_yes")
async def remove_account_confirm(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    phone = data.get('phone')

    if not phone:
        await callback.answer("Phone number not found")
        return

    # Get session info before deletion
    session = await sessions_collection.find_one({"phone": phone})

    # Remove from database
    await remove_session(phone)

    # Remove from active memory without reloading all accounts
    await disconnect_active_client_by_identity(
        phone=session.get('phone') if session else phone,
        user_id=session.get('user_id') if session else None
    )
    if session:
        remove_session_file_by_name(session.get('session_name'))

    success_text = (
        f"✅ <b>Account Removed Successfully!</b>\n\n"
        f"📞 <b>Phone/Session:</b> <code>{phone}</code>\n"
        f"👤 <b>Account:</b> {get_session_username_text(session) if session else 'N/A'}\n\n"
        f"🟢 Active clients now: {len(get_active_clients())}\n"
        f"This account will no longer be used by the bot."
    )

    await callback.message.edit_text(success_text, parse_mode="HTML")
    await callback.message.answer("Main Menu", reply_markup=get_main_menu_keyboard())
    await state.clear()

    # Return to admin panel
    await callback.message.answer(
        "🔐 Admin Panel",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📊 Statistics"), KeyboardButton(text="👤 Manage User")],
                [KeyboardButton(text="📦 User Orders"), KeyboardButton(text="📢 Broadcast")],
                [KeyboardButton(text="💰 Pricing"), KeyboardButton(text="📱 Telegram Accounts")],
                [KeyboardButton(text="🗑️ Remove Account"), KeyboardButton(text="⬅️ Main Menu")]
            ],
            resize_keyboard=True
        )
    )

@dp.callback_query(F.data == "confirm_remove_no")
async def remove_account_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ Account removal cancelled.")
    await state.clear()
    await callback.message.answer(
        "🔐 Admin Panel",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📊 Statistics"), KeyboardButton(text="👤 Manage User")],
                [KeyboardButton(text="📦 User Orders"), KeyboardButton(text="📢 Broadcast")],
                [KeyboardButton(text="💰 Pricing"), KeyboardButton(text="📱 Telegram Accounts")],
                [KeyboardButton(text="🗑️ Remove Account"), KeyboardButton(text="⬅️ Main Menu")]
            ],
            resize_keyboard=True
        )
    )

# ===== UPDATE ADMIN COMMAND MENU ===== #
@dp.message(Command("admin"))
async def admin_command(message: types.Message, state: FSMContext):
    await state.clear()
    # Check if user is admin (either main admin or has is_admin flag)
    user = await users_collection.find_one({"user_id": message.from_user.id})
    if message.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID] and (not user or not user.get('is_admin', False)):
        await message.answer("⛔️ This command is only available to admins.")
        return

    # Different buttons for major admin vs other admins
    if message.from_user.id == MAJOR_ADMIN_ID:
        # Major admin can see make/remove admin buttons and powers management
        keyboard = [
            [KeyboardButton(text="📊 Statistics"), KeyboardButton(text="👤 Manage User")],
            [KeyboardButton(text="📦 User Orders"), KeyboardButton(text="📢 Broadcast")],
            [KeyboardButton(text="💰 Pricing"), KeyboardButton(text="📱 Telegram Accounts")],
            [KeyboardButton(text="👑 Make Admins"), KeyboardButton(text="🗑️ Remove Admin")],
            [KeyboardButton(text="⚡️ Powers"), KeyboardButton(text="🖼️ Set UPI QR")],
            [KeyboardButton(text="⬅️ Main Menu")]
        ]
    else:
        # Other admins - check their permissions for button visibility
        user_permissions = user.get('admin_permissions', {
            "statistics": True,
            "manage_user": True,
            "user_orders": True,
            "broadcast": True,
            "pricing": True,
            "telegram_accounts": True
        })

        keyboard = []
        row1 = []
        if user_permissions.get("statistics", True):
            row1.append(KeyboardButton(text="📊 Statistics"))
        if user_permissions.get("manage_user", True):
            row1.append(KeyboardButton(text="👤 Manage User"))
        if row1:
            keyboard.append(row1)

        row2 = []
        if user_permissions.get("user_orders", True):
            row2.append(KeyboardButton(text="📦 User Orders"))
        if user_permissions.get("broadcast", True):
            row2.append(KeyboardButton(text="📢 Broadcast"))
        if row2:
            keyboard.append(row2)

        row3 = []
        if user_permissions.get("pricing", True):
            row3.append(KeyboardButton(text="💰 Pricing"))
        if user_permissions.get("telegram_accounts", True):
            row3.append(KeyboardButton(text="📱 Telegram Accounts"))
        if row3:
            keyboard.append(row3)

        keyboard.append([KeyboardButton(text="🖼️ Set UPI QR")])
        keyboard.append([KeyboardButton(text="⬅️ Main Menu")])

    await message.answer(
        "🔐 Admin Panel",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=keyboard,
            resize_keyboard=True
        )
    )


@dp.message(F.text == "📊 Statistics")
async def admin_stats(message: types.Message, state: FSMContext):
    await state.clear()
    # Check if user is admin
    user = await users_collection.find_one({"user_id": message.from_user.id})
    if message.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID] and (not user or not user.get('is_admin', False)):
        return

    # Check permission for non-major admins
    if message.from_user.id != MAJOR_ADMIN_ID and message.from_user.id != ADMIN_ID:
        user_permissions = user.get('admin_permissions', {})
        if not user_permissions.get('statistics', True):
            await message.answer("⛔️ You don't have permission to view statistics.")
            return

    # Get counts
    users_count = await users_collection.count_documents({})
    orders_count = await orders_collection.count_documents({})
    completed_orders = await orders_collection.count_documents({"status": "completed"})
    pending_orders = await orders_collection.count_documents({"status": "pending"})

    # Get revenue
    revenue_data = await payments_collection.aggregate([
        {"$match": {"status": "completed"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]).to_list(1)
    revenue = revenue_data[0]['total'] if revenue_data else 0

    # Get top users by balance
    top_users = await users_collection.find().sort("balance", -1).limit(3).to_list(None)

    text = (
        "📊 Bot Statistics\n\n"
        f"👥 Total Users: {users_count}\n"
        f"📦 Total Orders: {orders_count}\n"
        f"✅ Completed Orders: {completed_orders}\n"
        f"⏳ Pending Orders: {pending_orders}\n"
        f"💰 Total Revenue: ${revenue:.2f}\n\n"
        "🏆 Top Users by Balance:\n"
    )

    for i, user in enumerate(top_users, 1):
        text += f"{i}. {user.get('first_name', 'User')} - ${user.get('balance', 0):.2f}\n"

    await message.answer(text)
    await admin_command(message,state)

@dp.message(F.text == "👑 Make Admins")
async def make_admin_handler(message: types.Message, state: FSMContext):
    # Only MAJOR_ADMIN_ID can make other admins
    if message.from_user.id != MAJOR_ADMIN_ID:
        await message.answer("⛔️ Only the major admin can promote users to admin.")
        return

    await message.answer(
        "👑 *Make New Admin*\n\n"
        "Please send the new admin's *User ID* or *@username*\n\n"
        "Example:\n"
        "• `123456789`\n"
        "• `@username`",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.MAKE_ADMIN)

@dp.message(AdminStates.MAKE_ADMIN)
async def process_make_admin(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await admin_command(message, state)
        await state.clear()
        return

    # Only MAJOR_ADMIN_ID can make admins
    if message.from_user.id != MAJOR_ADMIN_ID:
        await message.answer("⛔️ You don't have permission to make admins.")
        await state.clear()
        return

    # Extract user ID or username
    input_text = message.text.strip()
    target_user_id = None
    target_username = None

    # Check if it's a user ID (numeric)
    if input_text.isdigit():
        target_user_id = int(input_text)
    # Check if it's a username
    elif input_text.startswith("@"):
        target_username = input_text[1:]  # Remove @ symbol
    else:
        await message.answer(
            "❌ Invalid format!\n\n"
            "Please send either:\n"
            "• User ID (numeric): `123456789`\n"
            "• Username: `@username`",
            parse_mode="Markdown"
        )
        return

    # Find the user in database
    query = {}
    if target_user_id:
        query = {"user_id": target_user_id}
    elif target_username:
        query = {"username": target_username}

    target_user = await users_collection.find_one(query)

    # If user not found, create new entry
    if not target_user:
        if target_user_id:
            # Create new user entry with default permissions
            default_permissions = {
                "statistics": True,
                "manage_user": True,
                "user_orders": True,
                "broadcast": True,
                "pricing": True,
                "telegram_accounts": True
            }
            user_data = {
                "user_id": target_user_id,
                "username": None,
                "first_name": "Unknown",
                "balance": 0.0,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "is_admin": True,
                "admin_permissions": default_permissions
            }
            await users_collection.insert_one(user_data)

            await message.answer(
                f"✅ *Admin Created Successfully!*\n\n"
                f"🆔 *User ID:* `{target_user_id}`\n"
                f"👑 *Status:* Admin\n\n"
                f"_Note: User not found in database, created new entry with admin rights._",
                parse_mode="Markdown"
            )
        else:
            await message.answer(
                f"❌ User `@{target_username}` not found in database.\n\n"
                f"Please ask them to start the bot first with /start command.",
                parse_mode="Markdown"
            )
            return
    else:
        # Check if already admin
        if target_user.get('is_admin', False):
            await message.answer(
                f"ℹ️ User is already an admin!\n\n"
                f"🆔 *User ID:* `{target_user['user_id']}`\n"
                f"👤 *Name:* {target_user.get('first_name', 'Unknown')}\n"
                f"👑 *Status:* Already Admin",
                parse_mode="Markdown"
            )
        else:
            # Update user to admin with default permissions
            default_permissions = {
                "statistics": True,
                "manage_user": True,
                "user_orders": True,
                "broadcast": True,
                "pricing": True,
                "telegram_accounts": True
            }
            await users_collection.update_one(
                {"user_id": target_user['user_id']},
                {
                    "$set": {
                        "is_admin": True,
                        "admin_permissions": default_permissions,
                        "updated_at": datetime.utcnow()
                    }
                }
            )

            await message.answer(
                f"✅ *Admin Promoted Successfully!*\n\n"
                f"🆔 *User ID:* `{target_user['user_id']}`\n"
                f"👤 *Name:* {target_user.get('first_name', 'Unknown')}\n"
                f"📱 *Username:* @{target_user.get('username', 'N/A')}\n"
                f"👑 *New Status:* Admin\n\n"
                f"_This user now has full admin privileges!_",
                parse_mode="Markdown"
            )

            # Notify the new admin
            try:
                await bot.send_message(
                    target_user['user_id'],
                    f"🎉 *Congratulations!*\n\n"
                    f"You have been promoted to *Admin* by {message.from_user.first_name}!\n\n"
                    f"You now have access to admin features. Use /admin to access the admin panel.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Failed to notify new admin: {e}")

    await state.clear()
    await admin_command(message, state)


@dp.message(F.text == "🗑️ Remove Admin")
async def remove_admin_handler(message: types.Message, state: FSMContext):
    # Only MAJOR_ADMIN_ID can remove other admins
    if message.from_user.id != MAJOR_ADMIN_ID:
        await message.answer("⛔️ Only the major admin can remove admins.")
        return

    # Get list of current admins
    admins = await users_collection.find({"is_admin": True}).to_list(None)

    if not admins:
        await message.answer("ℹ️ No admins found in the system.")
        await admin_command(message, state)
        return

    admin_list = "👥 *Current Admins:*\n\n"
    for admin in admins:
        admin_list += f"🆔 `{admin['user_id']}` - {admin.get('first_name', 'Unknown')}"
        if admin.get('username'):
            admin_list += f" (@{admin['username']})"
        admin_list += "\n"

    await message.answer(
        f"{admin_list}\n"
        "🗑️ *Remove Admin*\n\n"
        "Please send the admin's *User ID* or *@username* to remove admin privileges\n\n"
        "Example:\n"
        "• `123456789`\n"
        "• `@username`",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.REMOVE_ADMIN)

@dp.message(AdminStates.REMOVE_ADMIN)
async def process_remove_admin(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await admin_command(message, state)
        await state.clear()
        return

    # Only MAJOR_ADMIN_ID can remove admins
    if message.from_user.id != MAJOR_ADMIN_ID:
        await message.answer("⛔️ You don't have permission to remove admins.")
        await state.clear()
        return

    # Extract user ID or username
    input_text = message.text.strip()
    target_user_id = None
    target_username = None

    # Check if it's a user ID (numeric)
    if input_text.isdigit():
        target_user_id = int(input_text)
    # Check if it's a username
    elif input_text.startswith("@"):
        target_username = input_text[1:]  # Remove @ symbol
    else:
        await message.answer(
            "❌ Invalid format!\n\n"
            "Please send either:\n"
            "• User ID (numeric): `123456789`\n"
            "• Username: `@username`",
            parse_mode="Markdown"
        )
        return

    # Find the user in database
    query = {}
    if target_user_id:
        query = {"user_id": target_user_id}
    elif target_username:
        query = {"username": target_username}

    target_user = await users_collection.find_one(query)

    if not target_user:
        await message.answer(
            f"❌ User not found in database.\n\n"
            f"Please check the ID or username and try again.",
            parse_mode="Markdown"
        )
        return

    # Prevent removing major admin
    if target_user['user_id'] == MAJOR_ADMIN_ID:
        await message.answer(
            "❌ *Cannot remove Major Admin!*\n\n"
            "The major admin cannot be demoted.",
            parse_mode="Markdown"
        )
        return

    # Prevent removing main admin
    if target_user['user_id'] == ADMIN_ID:
        await message.answer(
            "❌ *Cannot remove Main Admin!*\n\n"
            "The main admin cannot be demoted.",
            parse_mode="Markdown"
        )
        return

    # Check if user is actually an admin
    if not target_user.get('is_admin', False):
        await message.answer(
            f"ℹ️ User is not an admin!\n\n"
            f"🆔 *User ID:* `{target_user['user_id']}`\n"
            f"👤 *Name:* {target_user.get('first_name', 'Unknown')}\n"
            f"📊 *Status:* Regular User",
            parse_mode="Markdown"
        )
    else:
        # Remove admin privileges
        await users_collection.update_one(
            {"user_id": target_user['user_id']},
            {
                "$set": {
                    "is_admin": False,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        await message.answer(
            f"✅ *Admin Removed Successfully!*\n\n"
            f"🆔 *User ID:* `{target_user['user_id']}`\n"
            f"👤 *Name:* {target_user.get('first_name', 'Unknown')}\n"
            f"📱 *Username:* @{target_user.get('username', 'N/A')}\n"
            f"📊 *New Status:* Regular User\n\n"
            f"_This user no longer has admin privileges._",
            parse_mode="Markdown"
        )

        # Notify the demoted user
        try:
            await bot.send_message(
                target_user['user_id'],
                f"📢 *Admin Status Update*\n\n"
                f"Your admin privileges have been removed by {message.from_user.first_name}.\n\n"
                f"You are now a regular user.",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Failed to notify removed admin: {e}")

    await state.clear()
    await admin_command(message, state)


# ===== POWERS MANAGEMENT HANDLERS ===== #
@dp.message(F.text == "⚡️ Powers")
async def powers_management_handler(message: types.Message, state: FSMContext):
    # Only MAJOR_ADMIN_ID can manage powers
    if message.from_user.id != MAJOR_ADMIN_ID:
        await message.answer("⛔️ Only the major admin can manage admin powers.")
        return

    # Get list of all admins (excluding MAJOR_ADMIN_ID himself)
    admins = await users_collection.find({
        "is_admin": True,
        "user_id": {"$ne": MAJOR_ADMIN_ID}
    }).to_list(None)

    if not admins:
        await message.answer(
            "ℹ️ No other admins found in the system.\n\n"
            "Use '👑 Make Admins' to create new admins first.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Back to Admin Panel")]],
                resize_keyboard=True
            )
        )
        return

    # Create inline keyboard with admin list
    keyboard = []
    for admin in admins:
        admin_name = admin.get('first_name', 'Unknown')
        admin_username = admin.get('username', '')
        display_name = f"{admin_name}"
        if admin_username:
            display_name += f" (@{admin_username})"

        keyboard.append([
            InlineKeyboardButton(
                text=display_name,
                callback_data=f"powers_admin_{admin['user_id']}"
            )
        ])

    keyboard.append([InlineKeyboardButton(text="❌ Cancel", callback_data="powers_cancel")])

    await message.answer(
        "⚡️ *Powers Management*\n\n"
        "Select an admin to manage their powers:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@dp.callback_query(F.data.startswith("powers_admin_"))
async def show_admin_powers(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MAJOR_ADMIN_ID:
        await callback.answer("⛔️ Only major admin can manage powers!", show_alert=True)
        return

    # Extract admin user_id
    admin_user_id = int(callback.data.split("_")[-1])

    # Get admin details
    admin = await users_collection.find_one({"user_id": admin_user_id})
    if not admin:
        await callback.answer("❌ Admin not found!", show_alert=True)
        return

    # Get current permissions (with defaults)
    default_permissions = {
        "statistics": True,
        "manage_user": True,
        "user_orders": True,
        "broadcast": True,
        "pricing": True,
        "telegram_accounts": True
    }

    current_permissions = admin.get('admin_permissions', default_permissions)

    # If admin_permissions doesn't exist, set it with defaults
    if 'admin_permissions' not in admin:
        await users_collection.update_one(
            {"user_id": admin_user_id},
            {"$set": {"admin_permissions": default_permissions}}
        )
        current_permissions = default_permissions

    # Create inline keyboard with all powers
    power_names = {
        "statistics": "📊 Statistics",
        "manage_user": "👤 Manage User",
        "user_orders": "📦 User Orders",
        "broadcast": "📢 Broadcast",
        "pricing": "💰 Pricing",
        "telegram_accounts": "📱 Telegram Accounts"
    }

    keyboard = []
    for power_key, power_label in power_names.items():
        is_enabled = current_permissions.get(power_key, True)
        status = "✅ Enabled" if is_enabled else "❌ Disabled"
        action = "disable" if is_enabled else "enable"

        keyboard.append([
            InlineKeyboardButton(
                text=f"{power_label}: {status}",
                callback_data=f"toggle_{action}_{power_key}_{admin_user_id}"
            )
        ])

    keyboard.append([InlineKeyboardButton(text="⬅️ Back to Admin List", callback_data="powers_back_to_list")])

    admin_name = admin.get('first_name', 'Unknown')
    admin_username = admin.get('username', '')
    display_name = f"{admin_name}"
    if admin_username:
        display_name += f" (@{admin_username})"

    await callback.message.edit_text(
        f"⚡️ *Powers Management*\n\n"
        f"👤 *Admin:* {display_name}\n"
        f"🆔 *User ID:* `{admin_user_id}`\n\n"
        f"Select a power to toggle:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_admin_power(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MAJOR_ADMIN_ID:
        await callback.answer("⛔️ Only major admin can manage powers!", show_alert=True)
        return

    # Parse callback data: toggle_{action}_{power_key}_{admin_user_id}
    # Handle power_keys with underscores properly
    parts = callback.data.split("_")
    action = parts[1]  # enable or disable
    # admin_user_id is always the last part
    admin_user_id = int(parts[-1])
    # power_key is everything between action and admin_user_id
    power_key = "_".join(parts[2:-1])

    # Get admin details
    admin = await users_collection.find_one({"user_id": admin_user_id})
    if not admin:
        await callback.answer("❌ Admin not found!", show_alert=True)
        return

    # Get current permissions
    default_permissions = {
        "statistics": True,
        "manage_user": True,
        "user_orders": True,
        "broadcast": True,
        "pricing": True,
        "telegram_accounts": True
    }
    current_permissions = admin.get('admin_permissions', default_permissions)

    # Toggle the power
    new_value = (action == "enable")
    current_permissions[power_key] = new_value

    # Update in database
    await users_collection.update_one(
        {"user_id": admin_user_id},
        {
            "$set": {
                "admin_permissions": current_permissions,
                "updated_at": datetime.utcnow()
            }
        }
    )

    # Send notification to the admin
    power_names = {
        "statistics": "📊 Statistics",
        "manage_user": "👤 Manage User",
        "user_orders": "📦 User Orders",
        "broadcast": "📢 Broadcast",
        "pricing": "💰 Pricing",
        "telegram_accounts": "📱 Telegram Accounts"
    }

    power_display = power_names.get(power_key, power_key)
    status_text = "enabled" if new_value else "disabled"
    emoji_status = "✅" if new_value else "❌"

    try:
        await bot.send_message(
            admin_user_id,
            f"⚡️ *Power Update Notification*\n\n"
            f"Your admin power has been {status_text}:\n"
            f"{emoji_status} *{power_display}*\n\n"
            f"Updated by: {callback.from_user.first_name}",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Failed to notify admin about power change: {e}")

    # Show updated powers
    await show_admin_powers(callback, state)
    await callback.answer(f"✅ {power_display} {status_text}!")


@dp.callback_query(F.data == "powers_back_to_list")
async def powers_back_to_list(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MAJOR_ADMIN_ID:
        await callback.answer("⛔️ Only major admin can access this!", show_alert=True)
        return

    # Get list of all admins (excluding MAJOR_ADMIN_ID)
    admins = await users_collection.find({
        "is_admin": True,
        "user_id": {"$ne": MAJOR_ADMIN_ID}
    }).to_list(None)

    if not admins:
        await callback.message.edit_text(
            "ℹ️ No other admins found in the system."
        )
        await callback.answer()
        return

    # Create inline keyboard with admin list
    keyboard = []
    for admin in admins:
        admin_name = admin.get('first_name', 'Unknown')
        admin_username = admin.get('username', '')
        display_name = f"{admin_name}"
        if admin_username:
            display_name += f" (@{admin_username})"

        keyboard.append([
            InlineKeyboardButton(
                text=display_name,
                callback_data=f"powers_admin_{admin['user_id']}"
            )
        ])

    keyboard.append([InlineKeyboardButton(text="❌ Cancel", callback_data="powers_cancel")])

    await callback.message.edit_text(
        "⚡️ *Powers Management*\n\n"
        "Select an admin to manage their powers:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@dp.callback_query(F.data == "powers_cancel")
async def powers_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await callback.answer("❌ Powers management cancelled")



@dp.message(F.text == "👤 Manage User")
async def manage_user(message: types.Message, state: FSMContext):
    # Check if user is admin
    user = await users_collection.find_one({"user_id": message.from_user.id})
    if message.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID] and (not user or not user.get('is_admin', False)):
        return

    # Check permission for non-major admins
    if message.from_user.id != MAJOR_ADMIN_ID and message.from_user.id != ADMIN_ID:
        user_permissions = user.get('admin_permissions', {})
        if not user_permissions.get('manage_user', True):
            await message.answer("⛔️ You don't have permission to manage users.")
            return

    await message.answer(
        "Enter User ID or @username:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.VIEWING_USER)

@dp.message(AdminStates.VIEWING_USER)
async def view_user(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message,state)
        return

    identifier = message.text
    user = None

    # Try to find user by ID or username
    if identifier.isdigit():
        user = await users_collection.find_one({"user_id": int(identifier)})
    else:
        # Remove @ if present
        username = identifier.lstrip('@')
        user = await users_collection.find_one({"username": username})

    if not user:
        await message.answer("❌ User not found")
        return

    # Get user's orders count
    orders_count = await orders_collection.count_documents({"user_id": user['user_id']})

    # Get user's channels
    channels = await channels_collection.find({"user_id": user['user_id']}).to_list(None)
    channel_list = "\n".join([f"- {ch['channel_title']}" for ch in channels]) if channels else "None"

    text = (
        f"👤 User Information\n\n"
        f"🆔 ID: {user['user_id']}\n"
        f"👤 Name: {user.get('first_name', 'N/A')}\n"
        f"👤 Username: @{user.get('username', 'N/A')}\n"
        f"💰 Balance: ${user.get('balance', 0):.2f}\n"
        f"📦 Total Orders: {orders_count}\n"
        f"📅 Joined: {user['created_at'].strftime('%Y-%m-%d')}\n\n"
        f"📢 Connected Channels:\n{channel_list}\n\n"
        "Options:"
    )

    # Store user ID for future actions
    await state.update_data(target_user_id=user['user_id'])

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Edit Balance", callback_data="edit_balance")],
        [InlineKeyboardButton(text="📦 View Orders", callback_data="view_orders")],
        [InlineKeyboardButton(text="📢 Message User", url=f"tg://user?id={user['user_id']}")]
    ])

    await message.answer(text, reply_markup=keyboard)

@dp.callback_query(F.data == "edit_balance")
async def edit_balance_prompt(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = data.get('target_user_id')

    if not user_id:
        await callback.answer("User not found")
        return

    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        await callback.answer("User not found")
        return

    await callback.message.answer(
        f"Current balance: ${user.get('balance', 0):.2f}\n"
        "Enter new balance:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.EDITING_BALANCE)
    await callback.answer()

@dp.message(AdminStates.EDITING_BALANCE)
async def update_balance(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message,state)
        return

    try:
        new_balance = float(message.text)
    except ValueError:
        await message.answer("❌ Invalid amount. Please enter a number.")
        return

    data = await state.get_data()
    user_id = data.get('target_user_id')

    if not user_id:
        await message.answer("❌ User not found")
        await state.clear()
        return

    # Update balance
    await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"balance": new_balance}}
    )

    await message.answer(f"✅ Balance updated to ${new_balance:.2f}")
    await state.clear()

    # Notify user
    try:
        await bot.send_message(
            user_id,
            f"🔄 Your account balance has been updated by admin\n"
            f"💰 New Balance: ${new_balance:.2f}"
        )
    except Exception:
        pass  # User might have blocked the bot

    await admin_command(message,state)

@dp.callback_query(F.data == "view_orders")
async def view_user_orders(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = data.get('target_user_id')

    if not user_id:
        await callback.answer("User not found")
        return

    # Get latest 5 orders
    orders = await orders_collection.find({"user_id": user_id}).sort("created_at", -1).limit(5).to_list(None)

    if not orders:
        await callback.answer("No orders found for this user")
        return

    text = f"📦 Last 5 Orders for User {user_id}\n\n"

    for order in orders:
        status_icon = "✅" if order['status'] == 'completed' else "⏳"
        text += (
            f"{status_icon} Order ID: {order['_id']}\n"
            f"📦 Service: {order.get('service_identifier', 'N/A')}\n"
            f"💰 Amount: ${order.get('charge', 0):.4f}\n"
            f"📅 Date: {order['created_at'].strftime('%Y-%m-%d')}\n\n"
        )

    await callback.message.answer(text)
    await callback.answer()

@dp.message(F.text == "📦 User Orders")
async def user_orders_prompt(message: types.Message, state: FSMContext):
    user = await users_collection.find_one({"user_id": message.from_user.id})
    if message.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID] and (not user or not user.get('is_admin', False)):
        return

    # Check permission for non-major admins
    if message.from_user.id != MAJOR_ADMIN_ID and message.from_user.id != ADMIN_ID:
        user_permissions = user.get('admin_permissions', {})
        if not user_permissions.get('user_orders', True):
            await message.answer("⛔️ You don't have permission to view user orders.")
            return

    await message.answer(
        "Enter User ID to view orders:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.VIEWING_ORDERS)

@dp.message(AdminStates.VIEWING_ORDERS)
async def show_user_orders(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message,state)
        return

    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Invalid User ID. Please enter numbers only.")
        return

    # Get orders
    orders = await orders_collection.find({"user_id": user_id}).sort("created_at", -1).limit(10).to_list(None)

    if not orders:
        await message.answer(f"❌ No orders found for user {user_id}")
        return

    text = f"📦 Orders for User {user_id}\n\n"
    total_spent = 0

    for order in orders:
        status_icon = "✅" if order['status'] == 'completed' else "⏳"
        text += (
            f"{status_icon} Order ID: {order['_id']}\n"
            f"📦 Service: {order.get('service_identifier', 'N/A')}\n"
            f"💰 Amount: ${order.get('charge', 0):.4f}\n"
            f"📅 Date: {order['created_at'].strftime('%Y-%m-%d')}\n\n"
        )
        total_spent += order.get('charge', 0)

    text += f"💵 Total Spent: ${total_spent:.4f}"

    await message.answer(text)
    await state.clear()
    await admin_command(message,state)

@dp.message(F.text == "📢 Broadcast")
async def broadcast_prompt(message: types.Message, state: FSMContext):
    user = await users_collection.find_one({"user_id": message.from_user.id})
    if message.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID] and (not user or not user.get('is_admin', False)):
        return

    if message.from_user.id != MAJOR_ADMIN_ID and message.from_user.id != ADMIN_ID:
        user_permissions = user.get('admin_permissions', {})
        if not user_permissions.get('broadcast', True):
            await message.answer("⛔️ You don't have permission to broadcast messages.")
            return

    await message.answer(
        "📢 <b>Broadcast</b>\n\n"
        "Send the message you want to broadcast to all users.\n"
        "Supported types: <b>Text, Photo, Video, Voice, Audio, Document, Sticker, Animation (GIF)</b>\n\n"
        "<i>You can also send a photo/video with a caption.</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.BROADCASTING)


@dp.message(AdminStates.BROADCASTING)
async def confirm_broadcast(message: types.Message, state: FSMContext):
    # Cancel shortcut
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message, state)
        return

    # Detect content type and build a label for preview
    if message.photo:
        content_type = "photo"
        label = "🖼️ Photo" + (f" + caption: {message.caption}" if message.caption else "")
    elif message.video:
        content_type = "video"
        label = "🎥 Video" + (f" + caption: {message.caption}" if message.caption else "")
    elif message.voice:
        content_type = "voice"
        label = "🎤 Voice message"
    elif message.audio:
        content_type = "audio"
        label = f"🎵 Audio: {message.audio.file_name or 'audio'}"
    elif message.document:
        content_type = "document"
        label = f"📄 Document: {message.document.file_name or 'file'}"
    elif message.sticker:
        content_type = "sticker"
        label = f"🎭 Sticker: {message.sticker.emoji or ''}"
    elif message.animation:
        content_type = "animation"
        label = "🎞️ Animation (GIF)"
    elif message.text:
        content_type = "text"
        label = f"📝 Text:\n\n{message.text}"
    else:
        await message.answer("❌ Unsupported message type. Please send text, photo, video, voice, audio, document, sticker, or animation.")
        return

    # Store reference to original message for copy_message later
    await state.update_data(
        broadcast_from_chat_id=message.chat.id,
        broadcast_message_id=message.message_id,
        broadcast_content_type=content_type,
        broadcast_label=label
    )
    await state.set_state(AdminStates.CONFIRM_BROADCAST)

    # Show preview with confirm/cancel
    total_users = await users_collection.count_documents({})
    await message.answer(
        f"📢 <b>Broadcast Preview</b>\n\n"
        f"<b>Type:</b> {label}\n\n"
        f"<b>Will be sent to:</b> {total_users} users\n\n"
        f"Confirm broadcast?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Confirm & Send", callback_data="broadcast_confirm")],
            [InlineKeyboardButton(text="❌ Cancel",         callback_data="broadcast_cancel")]
        ])
    )


@dp.callback_query(F.data == "broadcast_confirm", AdminStates.CONFIRM_BROADCAST)
async def send_broadcast(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    from_chat_id = data.get("broadcast_from_chat_id")
    message_id   = data.get("broadcast_message_id")

    if not from_chat_id or not message_id:
        await callback.answer("❌ Broadcast message not found. Please try again.", show_alert=True)
        await state.clear()
        return

    users    = await users_collection.find({}, {"user_id": 1}).to_list(None)
    user_ids = [u["user_id"] for u in users]
    total    = len(user_ids)

    await callback.message.edit_text(f"📢 Sending broadcast to {total} users…")

    success = 0
    failed  = 0

    for user_id in user_ids:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=from_chat_id,
                message_id=message_id
            )
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)   # ~20 msgs/sec — safe for Telegram rate limits

    await state.clear()
    await callback.message.answer(
        f"📢 <b>Broadcast Completed!</b>\n\n"
        f"✅ Delivered: {success}\n"
        f"❌ Failed: {failed}\n"
        f"👥 Total: {total}",
        parse_mode="HTML"
    )
    await admin_command(callback.message, state)


@dp.callback_query(F.data == "broadcast_cancel", AdminStates.CONFIRM_BROADCAST)
async def cancel_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Broadcast cancelled.")
    await admin_command(callback.message, state)

@dp.message(F.text == "💰 Pricing")
async def pricing_menu(message: types.Message):
    user = await users_collection.find_one({"user_id": message.from_user.id})
    if message.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID] and (not user or not user.get('is_admin', False)):
        return

    # Check permission for non-major admins
    if message.from_user.id != MAJOR_ADMIN_ID and message.from_user.id != ADMIN_ID:
        user_permissions = user.get('admin_permissions', {})
        if not user_permissions.get('pricing', True):
            await message.answer("⛔️ You don't have permission to manage pricing.")
            return

    pricing = await pricing_collection.find_one({})

    text = (
        "💰 Current Pricing\n\n"
        "📝 Manual Services:\n"
        f"👁️ Views: ${pricing['manual_views']:.4f} per view\n"
        f"❤️ Reactions: ${pricing['manual_reactions']:.4f} per reaction\n"
        f"🗳️ Votes: ${pricing['poll_votes']:.4f} per vote\n\n"
        "📊 Followers Services:\n"
        f"👁️ Views Coefficients: "
        f"View: {pricing['views_by_followers']['view_coeff']}, "
        f"Post: {pricing['views_by_followers']['post_coeff']}, "
        f"Day: {pricing['views_by_followers']['day_coeff']}\n"
        f"❤️ Reactions Coefficients: "
        f"View: {pricing['reactions_by_followers']['view_coeff']}, "
        f"Post: {pricing['reactions_by_followers']['post_coeff']}, "
        f"Day: {pricing['reactions_by_followers']['day_coeff']}\n\n"
        f"💱 Exchange Rate: 1 USD = ₹{pricing['exchange_rate']:.2f}\n"
        f"📅 Last Updated: {pricing['last_updated'].strftime('%Y-%m-%d %H:%M')}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Manual Views", callback_data="edit_price:manual_views"),
            InlineKeyboardButton(text="✏️ Manual Reactions", callback_data="edit_price:manual_reactions")
        ],
        [
            InlineKeyboardButton(text="✏️ Poll Votes", callback_data="edit_price:poll_votes"),
            InlineKeyboardButton(text="✏️ Exchange Rate", callback_data="edit_exchange")
        ],
        [
            InlineKeyboardButton(text="✏️ Views Coefficients", callback_data="edit_coeff:views"),
            InlineKeyboardButton(text="✏️ Reactions Coefficients", callback_data="edit_coeff:reactions")
        ]
    ])

    await message.answer(text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("edit_price:"))
async def edit_price_prompt(callback: types.CallbackQuery, state: FSMContext):
    service_type = callback.data.split(":")[1]
    service_names = {
        "manual_views": "Manual Views",
        "manual_reactions": "Manual Reactions",
        "poll_votes": "Poll Votes"
    }

    pricing = await pricing_collection.find_one({})
    current_price = pricing[service_type]
    await state.update_data(service_type=service_type)
    await callback.message.answer(
        f"✏️ Editing {service_names[service_type]}\n"
        f"Current price: ${current_price:.4f} per unit\n\n"
        "Enter new price per unit (USD):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.EDITING_PRICE)
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_coeff:"))
async def edit_coeff_prompt(callback: types.CallbackQuery, state: FSMContext):
    service_type = callback.data.split(":")[1]
    service_names = {"views": "Views", "reactions": "Reactions"}

    pricing = await pricing_collection.find_one({})
    coeffs = pricing[f"{service_type}_by_followers"]

    text = (
        f"✏️ Editing {service_names[service_type]} Coefficients\n\n"
        "Current values:\n"
        f"👁️ View Coefficient: {coeffs['view_coeff']}\n"
        f"📝 Post Coefficient: {coeffs['post_coeff']}\n"
        f"📅 Day Coefficient: {coeffs['day_coeff']}\n\n"
        "Enter new values in the format:\n"
        "<view_coeff> <post_coeff> <day_coeff>\n"
        "Example: 0.035 0.11 0.004"
    )

    await state.update_data(service_type=service_type)
    await callback.message.answer(
        text,
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.EDITING_COEFF)
    await callback.answer()

@dp.callback_query(F.data == "edit_exchange")
async def edit_exchange_prompt(callback: types.CallbackQuery, state: FSMContext):
    pricing = await pricing_collection.find_one({})
    current_rate = pricing['exchange_rate']

    await callback.message.answer(
        f"✏️ Editing Exchange Rate\n"
        f"Current rate: 1 USD = ₹{current_rate:.2f}\n\n"
        "Enter new exchange rate (INR per USD):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.EDITING_EXCHANGE)
    await callback.answer()

@dp.message(AdminStates.EDITING_PRICE)
async def update_price(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await admin_command(message,state)
        await state.clear()
        return

    try:
        new_price = float(message.text)
        if new_price <= 0:
            raise ValueError("Price must be positive")
    except ValueError:
        await message.answer("❌ Invalid price. Please enter a positive number.")
        return

    data = await state.get_data()
    service_type = data.get('service_type')

    # Update pricing
    await pricing_collection.update_one(
        {},
        {"$set": {
            service_type: new_price,
            "last_updated": datetime.utcnow()
        }}
    )

    service_names = {
        "manual_views": "Manual Views",
        "manual_reactions": "Manual Reactions",
        "poll_votes": "Poll Votes"
    }

    await message.answer(
        f"✅ {service_names[service_type]} price updated to ${new_price:.4f} per unit"
    )
    await state.clear()
    await pricing_menu(message)

@dp.message(AdminStates.EDITING_COEFF)
async def update_coefficients(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await admin_command(message,state)
        await state.clear()
        return

    try:
        parts = message.text.split()
        if len(parts) != 3:
            raise ValueError("Exactly 3 values required")

        view_coeff = float(parts[0])
        post_coeff = float(parts[1])
        day_coeff = float(parts[2])

        if any(coeff <= 0 for coeff in [view_coeff, post_coeff, day_coeff]):
            raise ValueError("All coefficients must be positive")
    except ValueError as e:
        await message.answer(f"❌ Invalid input: {str(e)}\nPlease enter 3 positive numbers separated by spaces.")
        return

    data = await state.get_data()
    service_type = data.get('service_type')
    field_name = f"{service_type}_by_followers"

    # Update pricing
    await pricing_collection.update_one(
        {},
        {"$set": {
            f"{field_name}.view_coeff": view_coeff,
            f"{field_name}.post_coeff": post_coeff,
            f"{field_name}.day_coeff": day_coeff,
            "last_updated": datetime.utcnow()
        }}
    )

    service_names = {"views": "Views", "reactions": "Reactions"}
    await message.answer(
        f"✅ {service_names[service_type]} coefficients updated:\n"
        f"👁️ View: {view_coeff}\n"
        f"📝 Post: {post_coeff}\n"
        f"📅 Day: {day_coeff}"
    )
    await state.clear()
    await pricing_menu(message)

@dp.message(AdminStates.EDITING_EXCHANGE)
async def update_exchange_rate(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await admin_command(message,state)
        await state.clear()
        return

    try:
        new_rate = float(message.text)
        if new_rate <= 0:
            raise ValueError("Rate must be positive")
    except ValueError:
        await message.answer("❌ Invalid exchange rate. Please enter a positive number.")
        return

    # Update pricing
    await pricing_collection.update_one(
        {},
        {"$set": {
            "exchange_rate": new_rate,
            "last_updated": datetime.utcnow()
        }}
    )

    await message.answer(f"✅ Exchange rate updated to 1 USD = ₹{new_rate:.2f}")
    await state.clear()
    await pricing_menu(message)


# ===== UPI QR MANAGEMENT ===== #

@dp.message(F.text == "🖼️ Set UPI QR")
async def set_upi_qr_start(message: types.Message, state: FSMContext):
    user = await users_collection.find_one({"user_id": message.from_user.id})
    if message.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID] and (not user or not user.get('is_admin', False)):
        return

    # Show current QR if set
    setting = await settings_collection.find_one({"key": "upi_qr"})
    if setting and setting.get("file_id"):
        await bot.send_photo(
            message.chat.id,
            photo=setting["file_id"],
            caption=(
                "🖼️ <b>Current UPI QR Code</b>\n\n"
                "Send a new photo to replace it, or /cancel to keep it."
            ),
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
                resize_keyboard=True
            )
        )
    else:
        await message.answer(
            "🖼️ <b>Set UPI QR Code</b>\n\n"
            "No QR is set yet. Send a photo of the UPI QR code to save it.\n\n"
            "Type /cancel to abort.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
                resize_keyboard=True
            )
        )

    await state.set_state(AdminStates.SETTING_UPI_QR)


@dp.message(AdminStates.SETTING_UPI_QR, F.photo)
async def set_upi_qr_receive(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id

    await settings_collection.update_one(
        {"key": "upi_qr"},
        {"$set": {"key": "upi_qr", "file_id": file_id, "updated_at": datetime.utcnow()}},
        upsert=True
    )

    # Now ask for UPI ID
    upi_id_setting = await settings_collection.find_one({"key": "upi_id"})
    current_upi_id = upi_id_setting.get("value", "") if upi_id_setting else ""
    current_hint = f"\n\n📌 <b>Current UPI ID:</b> <code>{html_escape(current_upi_id)}</code>" if current_upi_id else ""

    await state.set_state(AdminStates.SETTING_UPI_ID)
    await message.answer(
        f"✅ <b>QR Code saved!</b>{current_hint}\n\n"
        "🏦 Now send the <b>UPI ID</b> (e.g. <code>name@upi</code>) that users should pay to.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )


@dp.message(AdminStates.SETTING_UPI_QR)
async def set_upi_qr_invalid(message: types.Message, state: FSMContext):
    if message.text in ["/cancel", "⬅️ Cancel"]:
        await state.clear()
        await admin_command(message, state)
        return
    await message.answer("❌ Please send a <b>photo</b> of the UPI QR code.", parse_mode="HTML")


@dp.message(AdminStates.SETTING_UPI_ID)
async def set_upi_id_receive(message: types.Message, state: FSMContext):
    if message.text in ["/cancel", "⬅️ Cancel"]:
        await state.clear()
        await admin_command(message, state)
        return

    upi_id = message.text.strip()
    if not upi_id or " " in upi_id:
        await message.answer(
            "❌ Invalid UPI ID. It should look like <code>name@upi</code> with no spaces.\n\nTry again:",
            parse_mode="HTML"
        )
        return

    await settings_collection.update_one(
        {"key": "upi_id"},
        {"$set": {"key": "upi_id", "value": upi_id, "updated_at": datetime.utcnow()}},
        upsert=True
    )

    await state.clear()
    await message.answer(
        f"✅ <b>UPI Setup Complete!</b>\n\n"
        f"🖼️ QR Code: Saved\n"
        f"🏦 UPI ID: <code>{html_escape(upi_id)}</code>\n\n"
        "Users will now see this QR and UPI ID when making payments.",
        parse_mode="HTML"
    )
    await admin_command(message, state)


@dp.message(F.text == "⬅️ Main Menu")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Returning to main menu:",
        reply_markup=get_main_menu_keyboard()
    )

# Add this handler to clear states when returning to admin menu
@dp.message(F.text == "⬅️ Cancel")
async def cancel_admin_action(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    await admin_command(message,state)

# ===== MANUAL ORDER PROCESSING ===== #
async def process_manual_views(order, to_deliver):
    tasks = []
    for i in range(to_deliver):
        client = get_active_clients()[i % len(get_active_clients())]
        await ensure_client_in_channel(client,order['channel_id'])
        tasks.append(process_view_order(client, order['channel_id'], order['content_id']))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    return sum(1 for r in results if r is True)

async def process_manual_reactions(order, to_deliver):
    tasks = []
    emoji = order['emoji']
    for i in range(to_deliver):
        client = get_active_clients()[i % len(get_active_clients())]
        await ensure_client_in_channel(client,order['channel_id'])
        tasks.append(process_reaction_order(client, order['channel_id'], order['content_id'], emoji))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return sum(1 for r in results if r is True)

async def process_poll_votes_master_worker(order, to_deliver):
    """Distributed Vote Delivery - matches views/reactions pattern with delays"""
    try:
        channel_id = order['channel_id']
        message_id = order['content_id']
        option_index = order.get('option_index', 0)
        poll_options_count = order.get('poll_options_count', 10)

        # Use random delay between 10-15s per worker
        delay_seconds = random.uniform(10, 15)

        print(f"🗳️ Distributing {to_deliver} votes across {len(get_active_clients())} clients with ~{delay_seconds}s delay")

        tasks = []
        for i in range(to_deliver):
            client = get_active_clients()[i % len(get_active_clients())]

            # Ensure client is in channel
            if not await ensure_client_in_channel(client, channel_id):
                continue

            tasks.append(delayed_vote_order(client, channel_id, message_id, option_index, poll_options_count, delay_seconds * i))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for r in results if r is True)
        return success_count
    except Exception as e:
        print(f"❌ Vote processing failed: {e}")
        return 0

async def delayed_vote_order(client, channel_id, message_id, option_index, poll_options_count, delay):
    await asyncio.sleep(delay)
    return await process_vote_order(client, channel_id, message_id, option_index, poll_options_count)


async def process_poll_votes(order, to_deliver):
    tasks = []
    poll_options_count = order.get('poll_options_count', 10)  # Default 10 options
    option_index = order.get('option_index', 0)

    for i in range(to_deliver):
        client = get_active_clients()[i % len(get_active_clients())]
        await ensure_client_in_channel(client, order['channel_id'])
        tasks.append(process_vote_order(client, order['channel_id'], order['content_id'], option_index, poll_options_count))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    return sum(1 for r in results if r is True)


async def task_process_manual_orders():
    while True:
        try:
            # Find all pending manual orders
            orders = await orders_collection.find({
                "status": "pending",
                "service_identifier": {"$in": ["manual_views", "manual_reactions", "poll_votes"]}
            }).to_list(None)

            for order in orders:
                # Calculate remaining quantity to deliver
                if order['service_identifier'] == "manual_views":
                    delivered = order.get('delivered_views', 0)
                    remaining = order['quantity'] - delivered
                elif order['service_identifier'] == "manual_reactions":
                    delivered = order.get('delivered_reactions', 0)
                    remaining = order['quantity'] - delivered
                else:  # poll_votes
                    delivered = order.get('delivered_quantity', 0)
                    remaining = order['quantity'] - delivered

                if remaining <= 0:
                    # Mark as completed
                    await orders_collection.update_one(
                        {"_id": order['_id']},
                        {"$set": {"status": "completed"}}
                    )
                    continue

                # ===== NIGHT MODE ADJUSTMENT (NEW FIX) ===== #
                # Check if it's night hours and adjust delivery quantity
                if is_night_hours():
                    # Reduce delivery speed by 70% (divide by 3)
                    night_divisor = get_night_mode_delay_multiplier()
                    to_deliver = min(remaining, max(1, len(get_active_clients()) // night_divisor))
                    print(f"🌙 Night mode active - Reduced delivery: {to_deliver} (was {len(get_active_clients())})")
                else:
                    # Normal daytime delivery
                    to_deliver = min(remaining, len(get_active_clients()))

                # Process the batch
                if order['service_identifier'] == "manual_views":
                    success_count = await process_manual_views(order, to_deliver)
                    await orders_collection.update_one(
                        {"_id": order['_id']},
                        {"$inc": {"delivered_views": success_count}}
                    )
                elif order['service_identifier'] == "manual_reactions":
                    success_count = await process_manual_reactions(order, to_deliver)
                    await orders_collection.update_one(
                        {"_id": order['_id']},
                        {"$inc": {"delivered_reactions": success_count}}
                    )
                else:  # poll_votes
                    # Use the master-worker pattern with delays for votes too
                    success_count = await process_poll_votes_master_worker(order, to_deliver)
                    await orders_collection.update_one(
                        {"_id": order['_id']},
                        {"$inc": {"delivered_quantity": success_count}}
                    )

                # Check if order is completed
                new_delivered = delivered + success_count
                if new_delivered >= order['quantity']:
                    await orders_collection.update_one(
                        {"_id": order['_id']},
                        {"$set": {"status": "completed"}}
                    )
        except Exception as e:
            print(f"Error processing manual orders: {e}")

        await asyncio.sleep(30)  # Run every 30 seconds



async def expire_due_orders():
    """
    Auto-completes orders whose duration is over, unless paused.
    Notifies user and performs cleanup.
    """
    while True:
        today = datetime.utcnow().date()
        cursor = orders_collection.find({
            "status": {"$ne": "completed"}
        })

        async for order in cursor:
            if order.get("is_paused", False):
                continue  # Don't expire if paused

            created = order["created_at"]
            expiry_date = created.date() + timedelta(days=order.get("days", 0))

            if today >= expiry_date:
                await orders_collection.update_one(
                    {"_id": order["_id"]},
                    {"$set": {
                        "status": "completed",
                        "updated_at": datetime.utcnow()
                    }}
                )

                print(f"✅ Auto-completed order: {order['_id']}")
                user_id = order.get("user_id")
                channel_title = order.get("channel_title", "Unknown Channel")
                channel_id = order.get('channel_id')

                if order.get("service_identifier") == "views_by_followers":
                    service_name = "Auto Views"
                    total = order.get("total_views", 0)
                    delivered = order.get("delivered_views", 0)
                else:
                    service_name = "Auto Reactions"
                    total = order.get("total_reactions", 0)
                    delivered = order.get("delivered_reactions", 0)

                try:
                    await bot.send_message(
                        user_id,
                        (
                            f"🔔 <b>Your Order Has Been Completed!</b>\n\n"
                            f"📢 <b>Channel:</b> {channel_title}\n"
                            f"🛠️ <b>Service:</b> {service_name}\n"
                            f"📆 <b>Duration Completed:</b> {order['days']} days\n"
                            f"✅ <b>Delivered:</b> {delivered}/{total}\n\n"
                            f"Thanks for using our service! 😊"
                        ),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    print(f"❌ Failed to notify user {user_id}: {e}")

                # Leave from all except client 0
                await leave_channel_from_all_clients(channel_id)

        await asyncio.sleep(3600)  # Run every hour (adjust as needed)



async def task_reset_daily_posts():
    while True:
        try:
            # Calculate next UTC midnight
            now = datetime.utcnow()
            next_midnight = now + timedelta(days=1)
            next_midnight = next_midnight.replace(hour=0, minute=0, second=0, microsecond=0)
            wait_seconds = (next_midnight - now).total_seconds()

            await asyncio.sleep(wait_seconds)

            # Reset daily post counts
            await orders_collection.update_many(
                {
                    "service_identifier": {"$in": ["views_by_followers", "reactions_by_followers"]},
                    "status": {"$in": ["confirmed", "processing"]}
                },
                {"$set": {"posts_processed_today": 0}}
            )
            print("Reset daily posts for auto orders")
        except Exception as e:
            print(f"Error resetting daily posts: {e}")


async def reliable_update_processed_today(order_id, message_id, today_str, max_retries=5):
    for attempt in range(1, max_retries + 1):
        try:
            order = await orders_collection.find_one({"_id": order_id})
            if not order:
                print(f"⚠️ Order {order_id} not found.")
                return False

            processed_today = order.get("processed_today", {})
            today_list = processed_today.get(today_str, [])

            if message_id in today_list:
                return True  # Already stored

            today_list.append(message_id)
            processed_today[today_str] = today_list

            result = await orders_collection.update_one(
                {"_id": order_id},
                {"$set": {
                    "processed_today": processed_today,
                    "updated_at": datetime.utcnow()
                }}
            )

            if result.modified_count > 0:
                print(f"✅ Post {message_id} stored in processed_today (Attempt {attempt})")
                return True

        except PyMongoError as e:
            print(f"❌ Mongo error during processed_today update: {e}")

        await asyncio.sleep(0.3 * attempt)  # exponential backoff

    print(f"🚨 Failed to store post {message_id} after {max_retries} attempts.")
    return False

# Main Monitor Function - All clients monitor together
async def ub_moniter():
    if not get_active_clients():
        print("⚠️ No active clients for monitoring")
        return

    print(f"👑 Setting up monitoring with {len(get_active_clients())} clients...")

    async def monitor_channel_telethon(event):
        try:
            if not isinstance(event.chat, Channel):
                return

            channel_id = event.chat_id
            message = event.message
            message_id = message.id
            message_date = message.date.replace(tzinfo=None)
            today_str = datetime.utcnow().strftime('%Y-%m-%d')

            print(f"👑 MASTER detected new post in channel {channel_id}, message_id: {message_id}")

            # Get all active orders
            orders = await orders_collection.find({
                "channel_id": channel_id,
                "status": {"$in": ["confirmed", "processing"]},
                "service_identifier": {"$in": ["views_by_followers", "reactions_by_followers"]}
            }).to_list(None)

            if not orders:
                return

            for order in orders:
                if order.get("is_paused", False):
                    continue  # Order is paused

                order_created = order.get('created_at', datetime.utcnow()).replace(tzinfo=None)
                if message_date < order_created:
                    continue

                last_processed = order.get('last_processed_post_id', 0)
                if message_id <= last_processed:
                    continue

                processed_today_dict = order.get('processed_today', {})
                today_posts = processed_today_dict.get(today_str, [])

                if message_id in set(today_posts):
                    continue

                if len(today_posts) >= order['posts_per_day']:
                    continue

                # Expiry check
                expiry_day = order_created.date() + timedelta(days=order['days'])
                expiry_time = datetime.combine(expiry_day + timedelta(days=1), datetime.min.time())
                if datetime.utcnow() >= expiry_time:
                    continue

                # Setup delivery
                if order['service_identifier'] == "views_by_followers":
                    metric = "views"
                    update_field = "delivered_views"
                    per_post_quantity = order.get("views_per_post", 10)
                    total_quantity = order.get("total_views", 0)
                    process_func = process_auto_views_master_worker
                else:
                    metric = "reactions"
                    update_field = "delivered_reactions"
                    per_post_quantity = order.get("reactions_per_post", 10)
                    total_quantity = order.get("total_reactions", 0)
                    process_func = process_auto_reactions_master_worker

                delivered = order.get(update_field, 0)
                remaining = total_quantity - delivered
                if remaining <= 0:
                    continue

                desired_quantity = min(
                    per_post_quantity,
                    remaining,
                    len(get_active_clients()),
                    max(0, order['posts_per_day'] * order['days'] - order.get('total_posts_processed', 0))
                )
                if desired_quantity < 1:
                    continue

                # Night Mode: if enabled AND currently in night hours (11 PM–7 AM IST),
                # divide delivery quantity by 3 (≈70% slower) so views trickle in naturally
                if order.get("night_mode_enabled", False) and is_night_hours():
                    original_quantity = desired_quantity
                    desired_quantity = max(1, desired_quantity // 3)
                    print(f"🌙 Night Mode active: reduced {metric} from {original_quantity} → {desired_quantity} (÷3)")

                print(f"👑 MASTER: Distributing {desired_quantity} {metric} to workers for post {message_id}")

                # 1️⃣ Pre-store post count
                await reliable_update_processed_today(order['_id'], message_id, today_str)

                # 2️⃣ Master distributes work to all workers
                success_count = await process_func(order, message_id, desired_quantity)

                if success_count == 0:
                    print(f"⚠️ Post {message_id} delivered 0 {metric}. Skipping update.")
                    continue

                # 3️⃣ Re-store after delivery
                await reliable_update_processed_today(order['_id'], message_id, today_str)

                # 4️⃣ Final DB fetch and final update
                order = await orders_collection.find_one({"_id": order["_id"]})
                processed_today = order.get("processed_today", {})
                today_list = processed_today.get(today_str, [])
                if message_id not in today_list:
                    today_list.append(message_id)
                    processed_today[today_str] = today_list

                new_delivered = order.get(update_field, 0) + success_count

                update_data = {
                    update_field: new_delivered,
                    "last_processed_post_id": message_id,
                    "processed_today": processed_today,
                    "total_posts_processed": order.get("total_posts_processed", 0) + 1,
                    "updated_at": datetime.utcnow()
                }

                if (new_delivered >= total_quantity or
                    update_data["total_posts_processed"] >= order['posts_per_day'] * order['days'] or
                    datetime.utcnow() >= expiry_time):
                    update_data["status"] = "completed"

                await orders_collection.update_one(
                    {"_id": order["_id"]},
                    {"$set": update_data}
                )

                print(f"✅ Finished post {message_id} — Delivered {success_count} {metric} — Total: {new_delivered}/{total_quantity}")

            # ===== AUTO POLL/VOTES HANDLING =====
            # Check if this is a poll message
            if hasattr(message, 'media') and hasattr(message.media, 'poll'):
                poll = message.media.poll
                poll_options_count = len(poll.answers) if hasattr(poll, 'answers') else 10

                print(f"🗳️ MASTER detected new POLL in channel {channel_id}, message_id: {message_id}")
                print(f"   Poll Question: {poll.question if hasattr(poll, 'question') else 'N/A'}")
                print(f"   Options Count: {poll_options_count}")

                # Find all active orders with auto_poll enabled
                poll_orders = await orders_collection.find({
                    "channel_id": channel_id,
                    "status": {"$in": ["confirmed", "processing"]},
                    "service_identifier": {"$in": ["views_by_followers", "reactions_by_followers"]},
                    "auto_poll_enabled": True
                }).to_list(None)

                for poll_order in poll_orders:
                    if poll_order.get("is_paused", False):
                        continue

                    # Check if order is still valid
                    order_created = poll_order.get('created_at', datetime.utcnow()).replace(tzinfo=None)
                    expiry_day = order_created.date() + timedelta(days=poll_order.get('days', 0))
                    expiry_time = datetime.combine(expiry_day + timedelta(days=1), datetime.min.time())
                    if datetime.utcnow() >= expiry_time:
                        continue

                    auto_poll_quantity = poll_order.get("auto_poll_votes_quantity", 0)
                    if auto_poll_quantity < 1:
                        continue

                    # Check if already processed
                    last_processed_poll = poll_order.get('last_processed_poll_id', 0)
                    if message_id <= last_processed_poll:
                        continue

                    # Deliver votes
                    print(f"🗳️ Delivering {auto_poll_quantity} auto poll votes for order {poll_order['_id']}")

                    # Use speed multiplier from order
                    success_count = await process_auto_poll_votes_master_worker(
                        poll_order, message_id, channel_id, auto_poll_quantity, poll_options_count
                    )

                    if success_count > 0:
                        # Update order with delivered votes
                        await orders_collection.update_one(
                            {"_id": poll_order["_id"]},
                            {
                                "$set": {
                                    "last_processed_poll_id": message_id,
                                    "updated_at": datetime.utcnow()
                                },
                                "$inc": {
                                    "delivered_poll_votes": success_count
                                }
                            }
                        )
                        print(f"✅ Delivered {success_count} auto poll votes")

        except Exception as e:
            print(f"❌ Error in monitor: {e}")
            import traceback
            traceback.print_exc()

    # Register monitor on all clients
    for i, client in enumerate(get_active_clients()):
        try:
            client.add_event_handler(monitor_channel_telethon, events.NewMessage)
            print(f"✅ Monitoring enabled on client {i}")
        except Exception as e:
            print(f"⚠️ Failed to set monitor on client {i}: {e}")


# Distributed View Delivery - All clients work together
async def process_auto_views_master_worker(order, message_id, quantity):
    try:
        channel_id = order['channel_id']
        delay_seconds = get_order_delay_seconds(order)
        _clients = get_active_clients()

        print(f"📊 Distributing {quantity} views across {len(_clients)} clients with {delay_seconds}s delay")

        tasks = []
        for i in range(quantity):
            client = _clients[i % len(_clients)]

            # Ensure client is in channel
            if not await ensure_client_in_channel(client, channel_id):
                print(f"⚠️ Client {i % len(_clients)} couldn't join channel")
                continue

            # Use custom delay
            tasks.append(delayed_view_order(client, channel_id, message_id, delay_seconds * i))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for r in results if r is True)
        print(f"✅ Delivered {success_count}/{quantity} views successfully with {delay_seconds}s delay")
        return success_count
    except Exception as e:
        print(f"❌ View processing failed: {e}")
        import traceback
        traceback.print_exc()
        return 0

async def delayed_view_order(client, channel_id, message_id, delay):
    """Delayed view delivery for speed control"""
    await asyncio.sleep(delay)
    return await process_view_order(client, channel_id, message_id)


# Distributed Reaction Delivery - All clients work together
async def process_auto_reactions_master_worker(order, message_id, quantity):
    try:
        channel_id = order['channel_id']
        delay_seconds = get_order_delay_seconds(order)
        _clients = get_active_clients()

        print(f"❤️ Distributing {quantity} reactions across {len(_clients)} clients with {delay_seconds}s delay")

        tasks = []
        for i in range(quantity):
            client = _clients[i % len(_clients)]

            # Ensure client is in channel
            if not await ensure_client_in_channel(client, channel_id):
                print(f"⚠️ Client {i % len(_clients)} couldn't join channel")
                continue

            # Assign emoji for this client-post combination
            session_str = client.session.save()
            key = (channel_id, message_id, session_str)
            if key not in client_reactions:
                client_reactions[key] = random.choice(["❤️", "🔥", "👍", "👏", "🎉", "🤩", "😍"])
            emoji = client_reactions[key]

            # Use custom delay
            tasks.append(delayed_reaction_order(client, channel_id, message_id, emoji, delay_seconds * i))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for r in results if r is True)
        print(f"✅ Delivered {success_count}/{quantity} reactions successfully with {delay_seconds}s delay")
        return success_count
    except Exception as e:
        print(f"❌ Reaction processing failed: {e}")
        import traceback
        traceback.print_exc()
        return 0

async def delayed_reaction_order(client, channel_id, message_id, emoji, delay):
    """Delayed reaction delivery for speed control"""
    await asyncio.sleep(delay)
    return await process_reaction_order(client, channel_id, message_id, emoji=emoji)


# Distributed Poll Votes Delivery - All clients work together (for Auto Poll/Votes)
async def process_auto_poll_votes_master_worker(order, message_id, channel_id, quantity, poll_options_count):
    try:
        delay_seconds = get_order_delay_seconds(order)
        _clients = get_active_clients()

        print(f"🗳️ Distributing {quantity} poll votes across {len(_clients)} clients with ~{delay_seconds}s delay")

        tasks = []
        for i in range(quantity):
            client_index = i % len(_clients)
            client = _clients[client_index]

            # Ensure client is in channel
            if not await ensure_client_in_channel(client, channel_id):
                print(f"    ⚠️ Client {client_index} couldn't join channel")
                continue

            # Random option selection for each vote
            option_index = "random"

            # Calculate delay for this specific action
            current_delay = delay_seconds * i
            tasks.append(delayed_vote_order(client, channel_id, message_id, option_index, poll_options_count, current_delay))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for r in results if r is True)
        print(f"✅ Delivered {success_count}/{quantity} poll votes successfully with {delay_seconds}s base delay")
        return success_count
    except Exception as e:
        print(f"❌ Poll vote processing failed: {e}")
        import traceback
        traceback.print_exc()
        return 0

async def delayed_vote_order(client, channel_id, message_id, option_index, poll_options_count, delay):
    """Delayed vote delivery for speed control"""
    await asyncio.sleep(delay)
    return await process_vote_order(client, channel_id, message_id, option_index, poll_options_count)

async def deliver_votes_master_worker(order_id):
    """
    Deliver poll votes sequentially, one account per vote.

    Key rules:
    - Each Telegram account can vote only ONCE per poll.
    - To avoid same account being reused across multiple orders targeting the same poll,
      we use a rotating start index based on the order_id hash.
    - Votes are delivered one at a time with a configurable delay (custom_delay_seconds).
    - If quantity > available accounts, delivery stops at available account count.
    """
    try:
        order = await orders_collection.find_one({"_id": order_id})
        if not order:
            print(f"❌ Vote order {order_id} not found")
            return

        channel_id = order.get('channel_id')
        content_id = order.get('content_id')
        quantity = order.get('quantity', 0)
        option_index = order.get('option_index', 0)
        user_id = order.get('user_id')
        poll_options = order.get('poll_options', [])
        poll_options_count = len(poll_options)
        # Use order's custom delay, minimum 10s
        base_delay = max(10, order.get('custom_delay_seconds', 15) or 15)

        if not get_active_clients():
            print(f"❌ No active clients for vote order {order_id}")
            await orders_collection.update_one(
                {"_id": order_id},
                {"$set": {"status": "failed", "error": "No active clients"}}
            )
            return

        _clients = get_active_clients()
        total_clients = len(_clients)
        # Limit quantity to available accounts (1 vote per account per poll)
        effective_quantity = min(quantity, total_clients)

        # Rotate start index by order_id hash so different orders use different accounts
        order_id_str = str(order_id)
        start_index = int(order_id_str[-4:], 16) % total_clients if len(order_id_str) >= 4 else 0

        print(f"\n{'='*60}")
        print(f"🗳️ VOTE DELIVERY STARTED - Order {order_id}")
        print(f"📊 Target: {effective_quantity} votes on option {option_index}")
        print(f"👥 Available: {total_clients} client(s) | Start index: {start_index}")
        print(f"⏱️ Delay between votes: {base_delay}s")
        print(f"{'='*60}\n")

        await orders_collection.update_one(
            {"_id": order_id},
            {"$set": {"status": "processing", "updated_at": datetime.utcnow()}}
        )

        delivered = 0
        failed = 0

        for i in range(effective_quantity):
            client_index = (start_index + i) % total_clients
            client = _clients[client_index]

            try:
                # Add delay before each vote (except the very first)
                if i > 0:
                    await asyncio.sleep(base_delay)

                success = await process_vote_order(
                    client,
                    channel_id,
                    content_id,
                    option_index,
                    poll_options_count
                )

                if success:
                    delivered += 1
                    print(f"  ✅ Vote {i+1}/{effective_quantity} - Account idx {client_index} - Total delivered: {delivered}")
                else:
                    failed += 1
                    print(f"  ❌ Vote {i+1}/{effective_quantity} - Account idx {client_index} - FAILED (returned False)")

                # Update DB progress every 5 votes
                if delivered % 5 == 0 and delivered > 0:
                    await orders_collection.update_one(
                        {"_id": order_id},
                        {"$set": {"delivered_votes": delivered, "updated_at": datetime.utcnow()}}
                    )

            except Exception as e:
                failed += 1
                print(f"  ❌ Vote {i+1}/{effective_quantity} - Account idx {client_index} - EXCEPTION: {e}")
                import traceback
                traceback.print_exc()

        # Final update
        await orders_collection.update_one(
            {"_id": order_id},
            {
                "$set": {
                    "status": "completed",
                    "delivered_votes": delivered,
                    "completed_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                }
            }
        )

        success_rate = (delivered / effective_quantity * 100) if effective_quantity > 0 else 0
        print(f"\n{'='*60}")
        print(f"✅ VOTE DELIVERY COMPLETED")
        print(f"📊 Delivered: {delivered}/{effective_quantity} votes ({success_rate:.1f}%) | Failed: {failed}")
        print(f"{'='*60}\n")

        try:
            await bot.send_message(
                user_id,
                f"✅ <b>Vote Order Completed!</b>\n\n"
                f"🗳️ <b>Delivered:</b> {delivered}/{effective_quantity} votes\n"
                f"📊 <b>Success Rate:</b> {success_rate:.1f}%\n\n"
                f"Thank you for using our service!",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Failed to send completion notification: {e}")

    except Exception as e:
        print(f"❌ Vote delivery error for order {order_id}: {e}")
        import traceback
        traceback.print_exc()

        await orders_collection.update_one(
            {"_id": order_id},
            {"$set": {"status": "failed", "error": str(e)}}
        )



async def process_reaction_order(client, channel_id, message_id, emoji=None):
    """Send reaction with retry and error handling.
    Each account picks a random emoji from the appropriate pool so deliveries
    look natural instead of all accounts sending the identical reaction.
    """
    retries = 2

    # Per-account emoji randomisation — pick fresh from pool on every call
    if not emoji or emoji in ("❤️ Positive", "🤗 Custom"):
        # Default / positive type → random positive
        emoji = random.choice(POSITIVE_REACTIONS)
    elif emoji == "😂 Negative":
        emoji = random.choice(NEGATIVE_REACTIONS)
    elif emoji in POSITIVE_REACTIONS:
        # Specific positive emoji selected — still randomise across pool for variety
        emoji = random.choice(POSITIVE_REACTIONS)
    elif emoji in NEGATIVE_REACTIONS:
        # Specific negative emoji selected — randomise across negative pool
        emoji = random.choice(NEGATIVE_REACTIONS)
    # else: truly custom emoji not in either pool → use as-is

    await client(functions.account.UpdateStatusRequest(offline=False))

    for attempt in range(retries + 1):
        try:
            # Get channel entity
            entity = await client.get_entity(PeerChannel(channel_id))

            print(f"    😊 Attempt {attempt+1}: Sending {emoji} reaction to message {message_id}")

            # Send reaction using the consistent method
            await client(functions.messages.SendReactionRequest(
                    peer=channel_id,
                    msg_id=message_id,
                    reaction=[ReactionEmoji(emoji)],
                    big=False,
                    add_to_recent=True
                ))

            print(f"    ✅ Reaction {emoji} sent successfully")
            return True

        except errors.FloodWaitError as e:
            print(f"    ⚠️ Flood wait error: {e.seconds} seconds")
            if attempt < retries:
                wait_time = min(e.seconds + 5, 60)
                print(f"    ⏳ Waiting {wait_time} seconds before retry...")
                await asyncio.sleep(wait_time)
            else:
                print("    ❌ Max retries exceeded for flood wait")
                return False

        except errors.ChatWriteForbiddenError:
            print("    ❌ No permission to send reactions in this channel")
            return False

        except errors.MessageIdInvalidError:
            print("    ❌ Invalid message ID")
            return False

        except (errors.ReactionInvalidError, errors.RPCError) as e:
            print(f"    ❌ Reaction error: {e}")
            # Try a different emoji on next attempt
            if attempt < retries:
                session_str = client.session.save()
                key = (channel_id, message_id, session_str)
                emoji = random.choice(["❤️", "🔥", "👍", "👏", "🎉", "🤩", "😍"])
                client_reactions[key] = emoji
                print(f"    🆕 Trying different emoji: {emoji}")
                await asyncio.sleep(2)
                continue
            return False

        except Exception as e:
            print(f"    ❌ Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            if attempt < retries:
                print(f"    ⏳ Retrying in 2 seconds...")
                await asyncio.sleep(2)
            else:
                return False

    return False

async def ensure_client_in_channel(client, channel_id):
    """Ensure a client is in a specific channel, using stored invite link if available"""
    try:
        # Try to get the channel entity to check if we're already a member
        try:
            channel_entity = await client.get_entity(PeerChannel(channel_id))
            # Check if we have send permission
            try:
                await client.get_permissions(channel_entity)
                return True
            except ValueError:
                pass  # Not in channel, will try to join
        except (ValueError, errors.ChannelPrivateError):
            pass  # Channel not accessible, will try to join via invite

        # Get channel data from database
        channel_data = await channels_collection.find_one({"channel_id": channel_id})
        if not channel_data:
            print(f"    ❌ Channel data not found in database")
            return False

        invite_link = channel_data.get('invite_link')
        if not invite_link:
            print(f"    ❌ No invite link available for channel")
            return False

        # Join based on invite link type
        if 't.me/joinchat/' in invite_link or 't.me/+' in invite_link:
            # Private channel invite
            if 't.me/joinchat/' in invite_link:
                hash = invite_link.split('/')[-1]
            else:  # t.me/+ links
                hash = invite_link.split('/')[-1].replace('+', '')

            print(f"    🔑 Joining private channel with hash: {hash}")
            await client(ImportChatInviteRequest(hash))
            print(f"    ✅ Joined private channel via invite link")
        else:
            # Public channel
            username = invite_link.split('/')[-1] if 't.me/' in invite_link else invite_link
            print(f"    🔑 Joining public channel: @{username}")
            await client(JoinChannelRequest(username))
            print(f"    ✅ Joined public channel")

        # Verify join success
        try:
            channel_entity = await client.get_entity(PeerChannel(channel_id))
            await client.get_permissions(channel_entity)
            print(f"    ✅ Successfully joined and verified channel membership")
            return True
        except Exception as e:
            print(f"    ❌ Failed to verify channel membership: {e}")
            return False

    except errors.FloodWaitError as e:
        print(f"    ⚠️ Flood wait error: {e.seconds} seconds - cannot join now")
        return False
    except Exception as e:
        print(f"    ❌ Error joining channel: {e}")
        return False


async def ensure_client_in_channel_2(client, invite_link: str):
    """
    Attempts to join a Telegram channel using the invite link.
    Returns a dictionary with full channel info if successful.
    """
    result = {
        "success": False,
        "channel_id": None,
        "channel_title": None,
        "username": None,
        "is_public": None,
        "error": None
    }

    try:
        await client(functions.account.UpdateStatusRequest(offline=False))
        if not invite_link:
            result["error"] = "No invite link provided."
            return result

        # Validate link format
        if not any(x in invite_link.lower() for x in ['t.me/', 'telegram.me/', 'telegram.dog/']):
            result["error"] = "Invalid Telegram link format. Please use a valid t.me/ or telegram.me/ link."
            return result

        # Normalize link and extract invite code/username
        invite_code = invite_link.strip().split("/")[-1]

        # Handle different link formats
        is_private_link = False
        if "joinchat" in invite_link.lower():
            # Old style private link: https://t.me/joinchat/xxxxx
            invite_code = invite_code.replace("joinchat", "").strip()
            is_private_link = True
        elif "+" in invite_link and "t.me/+" in invite_link:
            # New style private link: https://t.me/+xxxxx
            invite_code = invite_code.replace("+", "")
            is_private_link = True

        # Private Link
        if is_private_link:
            try:
                updates = await client(ImportChatInviteRequest(invite_code))
                print(f"✅ Joined private channel via invite link")

                # Extract channel from updates
                if hasattr(updates, 'chats') and updates.chats:
                    entity = updates.chats[0]
                    print(f"✅ Got channel entity from join response: {entity.title if hasattr(entity, 'title') else 'Unknown'}")
                else:
                    entity = None

            except UserAlreadyParticipantError:
                print(f"ℹ️ Already a member of this private channel")
                entity = None  # Will need to find it later
                pass  # We're already in
            except FloodWaitError as e:
                result["error"] = f"Flood wait: {e.seconds}s. Please try again later."
                return result
            except errors.InviteHashExpiredError:
                result["error"] = "This invite link has expired. Please get a new invite link."
                return result
            except errors.InviteHashInvalidError:
                result["error"] = "Invalid invite link. Please check the link and try again."
                return result
            except Exception as e:
                result["error"] = f"Failed to join via invite: {str(e)}"
                return result
        else:
            # Public channel - validate username format
            if not invite_code or len(invite_code) < 5:
                result["error"] = "Invalid channel username. Username must be at least 5 characters."
                return result

            # Check for invalid characters in username
            if not all(c.isalnum() or c == '_' for c in invite_code):
                result["error"] = "Invalid username format. Only letters, numbers, and underscores are allowed."
                return result

            # ✅ First, try to check if the channel exists before joining
            try:
                # Try to get the entity to verify it exists
                entity_check = await client.get_entity(invite_code)
                print(f"✅ Channel @{invite_code} exists, proceeding to join...")
            except errors.UsernameNotOccupiedError:
                result["error"] = f"❌ Channel @{invite_code} does not exist.\n\n💡 Please verify:\n• The channel username is correct\n• The channel is public (not private)\n• Try using the full link: https://t.me/{invite_code}"
                return result
            except errors.UsernameInvalidError:
                result["error"] = f"❌ Invalid username format.\n\n💡 Username '{invite_code}' contains invalid characters."
                return result
            except ValueError as e:
                # This can happen if the username format is wrong
                result["error"] = f"❌ Cannot find channel @{invite_code}.\n\n💡 Please check:\n• Spelling is correct\n• Channel is public\n• For private channels, use the invite link (https://t.me/+xxxxx)"
                return result
            except Exception as e:
                print(f"⚠️ Error checking entity: {e}")
                # Continue to try joining anyway
                pass

            # Now try to join the channel
            try:
                await client(JoinChannelRequest(invite_code))
                print(f"✅ Successfully joined channel @{invite_code}")
            except UserAlreadyParticipantError:
                print(f"ℹ️ Already a member of @{invite_code}")
                pass  # We're already in
            except FloodWaitError as e:
                result["error"] = f"⏳ Flood wait: {e.seconds}s. Please try again later."
                return result
            except errors.UsernameNotOccupiedError:
                result["error"] = f"❌ Channel @{invite_code} does not exist.\n\n💡 Please verify the username and try again."
                return result
            except errors.UsernameInvalidError:
                result["error"] = f"❌ Invalid username format.\n\n💡 Please provide a valid channel username."
                return result
            except errors.ChannelPrivateError:
                result["error"] = f"❌ This channel is private.\n\n💡 Please use the private invite link (https://t.me/+xxxxx)"
                return result
            except Exception as e:
                result["error"] = f"❌ Failed to join channel: {str(e)}"
                return result

        # Try to get entity by invite link (skip if we already have entity from join response)
        if not entity:
            try:
                # Try direct link first
                entity = await client.get_entity(invite_link)
            except ValueError as ve:
                # If direct link doesn't work, try alternative methods
                if is_private_link:
                    # For private channels, try to get from dialogs
                    try:
                        print(f"🔍 Searching for private channel in dialogs...")
                        async for dialog in client.iter_dialogs(limit=200):
                            if isinstance(dialog.entity, Channel):
                                # Check if this is the channel we just joined
                                # Private channels don't have usernames, so we need to find by recent activity
                                entity = dialog.entity
                                print(f"✅ Found potential channel: {dialog.name}")
                                # For private channels, we'll use the first channel we find
                                # This works because we just joined it
                                break

                        if not entity:
                            result["error"] = "Could not find the private channel after joining. Please try again."
                            return result
                    except Exception as e:
                        result["error"] = f"Could not locate private channel: {str(e)}"
                        return result
                else:
                    # Public channel - try by username
                    try:
                        entity = await client.get_entity(invite_code)
                    except Exception as e:
                        result["error"] = f"Could not find channel. Please verify the link is correct and try again."
                        return result
            except Exception as e:
                result["error"] = f"Error accessing channel: {str(e)}"
                return result

        # Handle different entity types
        if isinstance(entity, (Channel, ChannelForbidden)):
            # It's a channel
            try:
                full = await client(GetFullChannelRequest(channel=entity))
                channel_obj = full.chats[0]

                result.update({
                    "success": True,
                    "channel_id": channel_obj.id,
                    "channel_title": getattr(channel_obj, "title", "Unknown Channel"),
                    "username": getattr(channel_obj, "username", None),
                    "is_public": bool(getattr(channel_obj, "username", None)),
                })
            except ChannelPrivateError:
                result["error"] = "Channel is private and you don't have access to it."
            except Exception as e:
                result["error"] = f"Channel error: {str(e)}"

        elif isinstance(entity, Chat):
            # It's a group chat
            result.update({
                "success": True,
                "channel_id": entity.id,
                "channel_title": getattr(entity, "title", "Unknown Group"),
                "username": None,
                "is_public": False,
            })
        else:
            result["error"] = "Unsupported chat type. Only channels and groups are supported."

        return result

    except Exception as e:
        result["error"] = f"Unexpected error: {str(e)}"
        return result


# ===== SUPPORT SYSTEM ===== #

@dp.message(F.text == "🆘 Support")
async def support_handler(message: types.Message, state: FSMContext):
    await state.set_state(SupportStates.WAITING_FOR_QUERY)
    await message.answer(
        "🆘 <b>Support</b>\n\n"
        "Please send your query — you can send <b>text</b>, or a <b>photo with caption</b>.\n\n"
        "Type /cancel to go back.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="/cancel")]],
            resize_keyboard=True
        )
    )


@dp.message(SupportStates.WAITING_FOR_QUERY, F.text)
async def support_receive_text(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Cancelled.", reply_markup=get_main_menu_keyboard())
        return

    await state.update_data(query_text=message.text, query_photo=None, query_voice=None)
    await state.set_state(SupportStates.CONFIRMING_QUERY)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Yes, Send", callback_data="support_yes"),
        InlineKeyboardButton(text="❌ No, Cancel", callback_data="support_no"),
        width=2
    )
    await message.answer(
        "📨 Should I send this query to the admin?",
        reply_markup=builder.as_markup()
    )


@dp.message(SupportStates.WAITING_FOR_QUERY, F.photo)
async def support_receive_photo(message: types.Message, state: FSMContext):
    photo_file_id = message.photo[-1].file_id
    caption = message.caption or ""
    await state.update_data(query_text=caption, query_photo=photo_file_id, query_voice=None)
    await state.set_state(SupportStates.CONFIRMING_QUERY)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Yes, Send", callback_data="support_yes"),
        InlineKeyboardButton(text="❌ No, Cancel", callback_data="support_no"),
        width=2
    )
    await message.answer(
        "📨 Should I send this query (with photo) to the admin?",
        reply_markup=builder.as_markup()
    )


@dp.message(SupportStates.WAITING_FOR_QUERY, F.voice)
async def support_receive_voice(message: types.Message, state: FSMContext):
    voice_file_id = message.voice.file_id
    await state.update_data(query_text="(voice message)", query_photo=None, query_voice=voice_file_id)
    await state.set_state(SupportStates.CONFIRMING_QUERY)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Yes, Send", callback_data="support_yes"),
        InlineKeyboardButton(text="❌ No, Cancel", callback_data="support_no"),
        width=2
    )
    await message.answer(
        "📨 Should I send this voice query to the admin?",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(SupportStates.CONFIRMING_QUERY, F.data == "support_no")
async def support_no_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Query cancelled.")
    await callback.message.answer("Main Menu", reply_markup=get_main_menu_keyboard())
    await callback.answer()


@dp.callback_query(SupportStates.CONFIRMING_QUERY, F.data == "support_yes")
async def support_yes_callback(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    query_text = data.get("query_text", "")
    query_photo = data.get("query_photo")
    query_voice = data.get("query_voice")
    user = callback.from_user
    user_id = user.id
    username = user.full_name or user.first_name

    reply_btn = InlineKeyboardBuilder()
    reply_btn.row(
        InlineKeyboardButton(text="↩️ Reply User", callback_data=f"support_reply:{user_id}")
    )

    user_link = f"<a href='tg://user?id={user_id}'>{html_escape(username)}</a>"

    # Build header text
    if query_photo:
        msg_label = "(photo attached)" if not query_text else query_text
        admin_text = (
            f"📩 <b>New Support Message</b>\n\n"
            f"👤 <b>User:</b> {user_link}\n"
            f"🆔 <b>User ID:</b> {user_id}\n\n"
            f"📝 <b>Message:</b>\n"
            f"<blockquote expandable>{html_escape(msg_label)}</blockquote>"
        )
    elif query_voice:
        admin_text = (
            f"📩 <b>New Support Message</b>\n\n"
            f"👤 <b>User:</b> {user_link}\n"
            f"🆔 <b>User ID:</b> {user_id}\n\n"
            f"🎤 <b>Message:</b> (voice message below)"
        )
    else:
        admin_text = (
            f"📩 <b>New Support Message</b>\n\n"
            f"👤 <b>User:</b> {user_link}\n"
            f"🆔 <b>User ID:</b> {user_id}\n\n"
            f"📝 <b>Message:</b>\n"
            f"<blockquote expandable>{html_escape(query_text)}</blockquote>"
        )

    try:
        if query_photo:
            await bot.send_photo(
                ADMIN_ID,
                photo=query_photo,
                caption=admin_text,
                parse_mode="HTML",
                reply_markup=reply_btn.as_markup()
            )
        elif query_voice:
            # Send text header first, then the voice
            await bot.send_message(
                ADMIN_ID,
                admin_text,
                parse_mode="HTML"
            )
            await bot.send_voice(
                ADMIN_ID,
                voice=query_voice,
                reply_markup=reply_btn.as_markup()
            )
        else:
            await bot.send_message(
                ADMIN_ID,
                admin_text,
                parse_mode="HTML",
                reply_markup=reply_btn.as_markup()
            )
    except Exception:
        await callback.message.edit_text("⚠️ Could not send your query. Please try again later.")
        await state.clear()
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "✅ <b>Your query has been sent to the admin!</b>\n\nYou will receive a reply soon.",
        parse_mode="HTML"
    )
    await callback.message.answer("Main Menu", reply_markup=get_main_menu_keyboard())
    await callback.answer()


# Admin clicks "↩️ Reply User" button
@dp.callback_query(F.data.startswith("support_reply:"))
async def support_reply_button(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in [ADMIN_ID, MAJOR_ADMIN_ID, PAYMENT_ADMIN_ID]:
        return await callback.answer("Not authorized.", show_alert=True)

    target_user_id = int(callback.data.split(":")[1])
    await state.update_data(support_target_user_id=target_user_id)
    await state.set_state(AdminSupportStates.WAITING_FOR_REPLY)

    await callback.message.answer(
        f"✏️ <b>Reply to User <code>{target_user_id}</code></b>\n\n"
        "Send your reply now — <b>text</b> or a <b>photo with caption</b> both work.\n\n"
        "Type /cancel to abort.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="/cancel")]],
            resize_keyboard=True
        )
    )
    await callback.answer()


# Admin sends text reply
@dp.message(AdminSupportStates.WAITING_FOR_REPLY, F.text)
async def admin_support_reply_text(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Reply cancelled.")
        return

    data = await state.get_data()
    target_user_id = data.get("support_target_user_id")

    try:
        await bot.send_message(
            target_user_id,
            f"📨 <b>Reply from Support:</b>\n\n{html_escape(message.text)}",
            parse_mode="HTML"
        )
        await message.answer(
            f"✅ Reply sent to user <code>{target_user_id}</code>.",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Failed to send reply: {e}")

    await state.clear()


# Admin sends photo reply
@dp.message(AdminSupportStates.WAITING_FOR_REPLY, F.photo)
async def admin_support_reply_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_user_id = data.get("support_target_user_id")
    caption = message.caption or ""
    photo_file_id = message.photo[-1].file_id

    try:
        await bot.send_photo(
            target_user_id,
            photo=photo_file_id,
            caption=(
                f"📨 <b>Reply from Support:</b>\n\n{html_escape(caption)}"
                if caption else "📨 <b>Reply from Support</b>"
            ),
            parse_mode="HTML"
        )
        await message.answer(
            f"✅ Photo reply sent to user <code>{target_user_id}</code>.",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Failed to send photo reply: {e}")

    await state.clear()


@dp.message(Command("restart"))
async def restart_command(message: types.Message):
    # Check if user is authorized (any admin)
    user = await users_collection.find_one({"user_id": message.from_user.id})
    is_major_admin = message.from_user.id in [ADMIN_ID, MAJOR_ADMIN_ID]
    is_admin = user and user.get('is_admin', False)

    if not (is_major_admin or is_admin):
        await message.answer("⛔️ This command is only available to admins.")
        return

    # Send restart notification
    await message.reply(
        "🔄 *Bot is restarting...*\n\n"
        "✅ Bot will be back online in 5-10 seconds!",
        parse_mode='Markdown'
    )

    # Give time for message to send
    await asyncio.sleep(1)

    # Get current script path
    script_path = os.path.abspath(__file__)

    # Restart using subprocess and then exit current process
    import subprocess
    subprocess.Popen([sys.executable, script_path], 
                     stdout=open('/app/bot_output.log', 'a'),
                     stderr=subprocess.STDOUT,
                     env=os.environ.copy())

    # Stop the bot gracefully
    await dp.stop_polling()

    # Exit current process
    os._exit(0)


async def validate_invite_link_only(client, invite_link: str):
    result = {
        "success": False,
        "channel_id": None,
        "channel_title": None,
        "username": None,
        "is_public": None,
        "error": None
    }

    try:
        if not invite_link:
            result["error"] = "No invite link provided."
            return result

        if not any(x in invite_link.lower() for x in ['t.me/', 'telegram.me/', 'telegram.dog/']):
            result["error"] = "Invalid Telegram link format. Please use a valid t.me/ or telegram.me/ link."
            return result

        invite_code = invite_link.strip().split("/")[-1]

        is_private_link = False
        if "joinchat" in invite_link.lower():
            invite_code = invite_code.replace("joinchat", "").strip()
            is_private_link = True
        elif "+" in invite_link and "t.me/+" in invite_link:
            invite_code = invite_code.replace("+", "")
            is_private_link = True

        if is_private_link:
            try:
                from telethon.tl.functions.messages import CheckChatInviteRequest
                from telethon.tl.functions.messages import ImportChatInviteRequest
                from telethon.tl.types import ChatInviteAlready as TLChatInviteAlready

                chat_invite = await client(CheckChatInviteRequest(invite_code))

                # Case 1: client is already a member
                if isinstance(chat_invite, (ChatInviteAlready, TLChatInviteAlready)):
                    channel = chat_invite.chat
                    result.update({
                        "success": True,
                        "channel_id": channel.id,
                        "channel_title": getattr(channel, "title", "Private Channel"),
                        "username": getattr(channel, "username", None),
                        "is_public": bool(getattr(channel, "username", None)),
                    })
                    return result

                # Case 2: CheckChatInviteRequest returned an object with .chat or .channel
                entity = None
                if hasattr(chat_invite, 'chat') and chat_invite.chat:
                    entity = chat_invite.chat
                elif hasattr(chat_invite, 'channel') and chat_invite.channel:
                    entity = chat_invite.channel

                if entity and isinstance(entity, (Channel, ChannelForbidden)):
                    result.update({
                        "success": True,
                        "channel_id": entity.id,
                        "channel_title": getattr(entity, "title", "Private Channel"),
                        "username": getattr(entity, "username", None),
                        "is_public": bool(getattr(entity, "username", None)),
                    })
                    return result

                # Case 3: ChatInvite type — client is not yet a member and the response
                # has no .chat/.channel entity (only title/photo). Join to get real channel ID.
                try:
                    join_result = await client(ImportChatInviteRequest(invite_code))
                    if hasattr(join_result, 'chats') and join_result.chats:
                        channel = join_result.chats[0]
                        result.update({
                            "success": True,
                            "channel_id": channel.id,
                            "channel_title": getattr(channel, "title", "Private Channel"),
                            "username": getattr(channel, "username", None),
                            "is_public": bool(getattr(channel, "username", None)),
                        })
                        return result
                except UserAlreadyParticipantError:
                    # Already a member but CheckChatInviteRequest returned no entity.
                    # Try to get entity by invite hash directly.
                    try:
                        entity = await client.get_entity(f"t.me/+{invite_code}")
                        result.update({
                            "success": True,
                            "channel_id": entity.id,
                            "channel_title": getattr(entity, "title", "Private Channel"),
                            "username": getattr(entity, "username", None),
                            "is_public": bool(getattr(entity, "username", None)),
                        })
                        return result
                    except Exception:
                        pass
                except Exception as join_err:
                    print(f"[validate_invite_link_only] Could not join via ImportChatInviteRequest: {join_err}")

                # Fallback: return partial info using invite metadata (title only, no ID)
                invite_title = getattr(chat_invite, "title", "Private Channel")
                result["error"] = (
                    f"Could not resolve channel ID for private link. "
                    f"Channel title from invite: '{invite_title}'. "
                    "Make sure the bot account has joined the channel, or share the channel directly."
                )
                return result

            except errors.InviteHashExpiredError:
                result["error"] = "This invite link has expired. Please get a new invite link."
                return result
            except errors.InviteHashInvalidError:
                result["error"] = "Invalid invite link. Please check the link and try again."
                return result
            except FloodWaitError as e:
                result["error"] = f"Flood wait: {e.seconds}s. Please try again later."
                return result
            except Exception as e:
                result["error"] = f"Failed to validate invite link: {str(e)}"
                return result
        else:
            if not invite_code or len(invite_code) < 5:
                result["error"] = "Invalid channel username. Username must be at least 5 characters."
                return result

            if not all(c.isalnum() or c == '_' for c in invite_code):
                result["error"] = "Invalid username format. Only letters, numbers, and underscores are allowed."
                return result

            try:
                entity = await client.get_entity(invite_code)
            except errors.UsernameNotOccupiedError:
                result["error"] = f"Channel @{invite_code} does not exist."
                return result
            except errors.UsernameInvalidError:
                result["error"] = f"Invalid username format."
                return result
            except ValueError:
                result["error"] = f"Cannot find channel @{invite_code}."
                return result
            except Exception as e:
                result["error"] = f"Error accessing channel: {str(e)}"
                return result

            if isinstance(entity, (Channel, ChannelForbidden)):
                try:
                    full = await client(GetFullChannelRequest(channel=entity))
                    channel_obj = full.chats[0]
                    result.update({
                        "success": True,
                        "channel_id": channel_obj.id,
                        "channel_title": getattr(channel_obj, "title", "Unknown Channel"),
                        "username": getattr(channel_obj, "username", None),
                        "is_public": bool(getattr(channel_obj, "username", None)),
                    })
                except ChannelPrivateError:
                    result["error"] = "Channel is private. Use a private invite link."
                except Exception as e:
                    result["error"] = f"Channel error: {str(e)}"
            elif isinstance(entity, Chat):
                result.update({
                    "success": True,
                    "channel_id": entity.id,
                    "channel_title": getattr(entity, "title", "Unknown Group"),
                    "username": None,
                    "is_public": False,
                })
            else:
                result["error"] = "Unsupported chat type. Only channels and groups are supported."

        return result

    except Exception as e:
        result["error"] = f"Unexpected error: {str(e)}"
        return result


async def join_all_clients_to_channel(channel_id, invite_link):
    if not get_active_clients():
        return

    print(f"🔗 Joining {len(get_active_clients())} clients to channel {channel_id} after payment confirmation...")

    async def join_single_client(i, client):
        try:
            # Check if already in channel
            already_in = False
            try:
                entity = await client.get_entity(PeerChannel(channel_id))
                await client.get_permissions(entity)
                already_in = True
            except Exception:
                pass

            if already_in:
                print(f"  Client {i}: Already in channel")
                await mute_channel_for_client(client, channel_id)
                return True

            link = invite_link.strip()

            # Determine if this is a private invite link or a public channel/group link
            is_private = (
                "joinchat" in link.lower() or
                ("t.me/+" in link) or
                ("+joinchat" in link)
            )

            if is_private:
                # Extract invite hash for private links
                # Handles: https://t.me/+HASH, https://t.me/joinchat/HASH
                invite_hash = link.split("/")[-1].lstrip("+")
                try:
                    result = await client(ImportChatInviteRequest(invite_hash))
                    print(f"  Client {i}: Joined via private invite link")
                except UserAlreadyParticipantError:
                    print(f"  Client {i}: Already a participant (private link)")
                except errors.InviteHashExpiredError:
                    print(f"  Client {i}: Invite hash expired - skipping")
                    return False
                except errors.InviteHashInvalidError:
                    print(f"  Client {i}: Invite hash invalid - skipping")
                    return False
            else:
                # Public channel/group: extract username from URL
                # Handles: https://t.me/username, https://telegram.me/username
                username = link.split("/")[-1].lstrip("@")
                try:
                    # Resolve the entity first, then join
                    resolved = await client.get_entity(username)
                    await client(JoinChannelRequest(resolved))
                    print(f"  Client {i}: Joined public channel @{username}")
                except UserAlreadyParticipantError:
                    print(f"  Client {i}: Already a participant (public)")
                except Exception as e:
                    print(f"  Client {i}: Failed to join public channel - {e}")
                    return False

            await mute_channel_for_client(client, channel_id)
            return True

        except FloodWaitError as e:
            print(f"  Client {i}: Flood wait {e.seconds}s")
            await asyncio.sleep(min(e.seconds, 30))
            return False
        except Exception as e:
            print(f"  Client {i}: Failed to join - {e}")
            return False

    tasks = [join_single_client(i, c) for i, c in enumerate(get_active_clients())]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    joined = sum(1 for r in results if r is True)
    print(f"✅ {joined}/{len(get_active_clients())} clients joined channel {channel_id}")


async def leave_channel_from_all_clients(channel_id):
    if not get_active_clients():
        return

    print(f"🚪 Leaving channel {channel_id} for all clients except client[0]...")

    for i, client in enumerate(get_active_clients()):
        if i == 0:
            continue
        try:
            entity = await client.get_entity(PeerChannel(channel_id))
            await client(LeaveChannelRequest(entity))
            print(f"  Client {i}: Left channel {channel_id}")
        except (ValueError, errors.ChannelPrivateError):
            print(f"  Client {i}: Not in channel (already left or never joined)")
        except FloodWaitError as e:
            print(f"  Client {i}: Flood wait {e.seconds}s while leaving")
            await asyncio.sleep(min(e.seconds, 10))
        except Exception as e:
            print(f"  Client {i}: Error leaving channel - {e}")


async def mute_channel_for_client(client, channel_id):
    try:
        entity = await client.get_entity(PeerChannel(channel_id))
        await client(UpdateNotifySettingsRequest(
            peer=InputNotifyPeer(peer=entity),
            settings=InputPeerNotifySettings(
                mute_until=2147483647,
                show_previews=False,
                silent=True
            )
        ))
        print(f"  🔇 Muted channel {channel_id}")
    except Exception as e:
        print(f"  ⚠️ Failed to mute channel {channel_id}: {e}")


async def unmute_channel_for_client(client, channel_id):
    try:
        entity = await client.get_entity(PeerChannel(channel_id))
        await client(UpdateNotifySettingsRequest(
            peer=InputNotifyPeer(peer=entity),
            settings=InputPeerNotifySettings(
                mute_until=0,
                show_previews=True,
                silent=False
            )
        ))
        print(f"  🔔 Unmuted channel {channel_id}")
    except Exception as e:
        print(f"  ⚠️ Failed to unmute channel {channel_id}: {e}")


def run_flask_server():
    """Run Flask webhook server"""
    print(f"\n🌐 Flask webhook server running on port {FLASK_PORT}...")
    if public_url:
        print(f"Endpoint: {public_url}/verify_payment\n")
    else:
        print(f"Endpoint: http://localhost:{FLASK_PORT}/verify_payment (webhook disabled)\n")
    flask_app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, use_reloader=False)


# ===== NIGHT MODE AUTOMATION =====
async def enable_night_mode_auto():
    """Automatically enable night mode at 11 PM IST for all active orders"""
    try:
        print("🌙 Auto-enabling Night Mode at 11 PM IST...")

        # Get all active orders
        active_orders = await orders_collection.find({
            "status": {"$in": ["confirmed", "processing"]},
            "service_identifier": {"$in": ["views_by_followers", "reactions_by_followers"]}
        }).to_list(None)

        updated_count = 0
        for order in active_orders:
            # Enable night mode
            await orders_collection.update_one(
                {"_id": order["_id"]},
                {
                    "$set": {
                        "night_mode_enabled": True,
                        "updated_at": datetime.utcnow()
                    }
                }
            )

            # Send notification to user
            user_id = order.get("user_id")
            try:
                await bot.send_message(
                    user_id,
                    "🌙 <b>Night Mode Activated Automatically</b>\n\n"
                    "⏰ Time: 11:00 PM IST\n"
                    "📊 Your order speed has been reduced to 70% for the night.\n"
                    "☀️ Day mode will be activated automatically at 7:00 AM IST.",
                    parse_mode="HTML"
                )
                updated_count += 1
            except Exception as e:
                print(f"Failed to send night mode notification to user {user_id}: {e}")

        print(f"✅ Night Mode enabled for {updated_count} active orders")
    except Exception as e:
        print(f"❌ Error in enable_night_mode_auto: {e}")

async def disable_night_mode_auto():
    """Automatically disable night mode at 7 AM IST for all active orders"""
    try:
        print("☀️ Auto-disabling Night Mode at 7 AM IST...")

        # Get all active orders with night mode enabled
        active_orders = await orders_collection.find({
            "status": {"$in": ["confirmed", "processing"]},
            "service_identifier": {"$in": ["views_by_followers", "reactions_by_followers"]},
            "night_mode_enabled": True
        }).to_list(None)

        updated_count = 0
        for order in active_orders:
            # Disable night mode
            await orders_collection.update_one(
                {"_id": order["_id"]},
                {
                    "$set": {
                        "night_mode_enabled": False,
                        "updated_at": datetime.utcnow()
                    }
                }
            )

            # Send notification to user
            user_id = order.get("user_id")
            try:
                await bot.send_message(
                    user_id,
                    "☀️ <b>Day Mode Activated</b>\n\n"
                    "⏰ Time: 7:00 AM IST\n"
                    "📊 Your order speed has been restored to normal.\n"
                    "🌙 Night mode will be activated automatically at 11:00 PM IST.",
                    parse_mode="HTML"
                )
                updated_count += 1
            except Exception as e:
                print(f"Failed to send day mode notification to user {user_id}: {e}")

        print(f"✅ Day Mode enabled for {updated_count} active orders")
    except Exception as e:
        print(f"❌ Error in disable_night_mode_auto: {e}")

def setup_night_mode_scheduler():
    """Setup APScheduler for automatic night mode toggling"""
    from datetime import timezone, timedelta as td

    scheduler = AsyncIOScheduler(timezone=timezone(td(hours=5, minutes=30)))  # IST timezone

    # Schedule night mode ON at 11:00 PM IST
    scheduler.add_job(
        enable_night_mode_auto,
        CronTrigger(hour=23, minute=0),
        id='night_mode_on',
        name='Enable Night Mode at 11 PM IST',
        replace_existing=True
    )

    # Schedule night mode OFF at 7:00 AM IST
    scheduler.add_job(
        disable_night_mode_auto,
        CronTrigger(hour=7, minute=0),
        id='night_mode_off',
        name='Disable Night Mode at 7 AM IST',
        replace_existing=True
    )

    scheduler.start()
    print("✅ Night Mode Scheduler initialized (11 PM ON | 7 AM OFF IST)")
    return scheduler


async def on_dead_session_alert(session_name: str, error: str):
    """Admin ko alert karo jab koi session permanently dead ho jaye (AuthKeyDuplicated etc.)"""
    try:
        await bot.send_message(
            ADMIN_ID,
            f"⚠️ <b>Session Permanently Dead!</b>\n\n"
            f"📁 <b>Session:</b> <code>{session_name}</code>\n"
            f"💀 <b>Reason:</b> <code>{error[:300] if error else 'Unknown'}</code>\n\n"
            f"❌ Auth key was used from 2 different IPs simultaneously — Telegram blacklisted it.\n"
            f"🔧 <b>Action:</b> Delete old .session file and re-add this account via /admin → Telegram Accounts",
            parse_mode="HTML"
        )
    except Exception:
        pass


async def main():
    global public_url, session_mgr

    try:
        print("🚀 Starting bot initialization...")

        # Check critical environment variables
        if not API_TOKEN or API_TOKEN == "8387013883:AAFXOLdxeouYLhCxwO0bGvoPW0rJDi9DTO8":
            print("⚠️ WARNING: Using default bot token. Please set TELEGRAM_BOT_TOKEN environment variable.")

        if not MONGODB_URI or MONGODB_URI == "mongodb+srv://shuvamoy1:shuvamoy1@bableview.5yaumiu.mongodb.net":
            print("⚠️ WARNING: Using default MongoDB URI. Please set MONGODB_URI environment variable.")

        print("📊 Creating database indexes...")
        await create_indexes()

        # Start ngrok tunnel for webhook integration (optional)
        print("🔗 Starting ngrok tunnel...")
        try:
            ngrok.set_auth_token(NGROK_AUTH_TOKEN)
            public_url = ngrok.connect(FLASK_PORT, "http").public_url
            print(f"✅ Ngrok Public URL: {public_url}")
            print(f"🔔 Webhook Endpoint: {public_url}/verify_payment")
        except Exception as e:
            print(f"⚠️ Ngrok failed (webhook disabled): {e}")
            public_url = None
            print("ℹ️ Bot will run without webhook support")

        # Start Flask in separate thread
        flask_thread = threading.Thread(target=run_flask_server, daemon=True)
        flask_thread.start()
        print("✅ Flask webhook server started")

        # ===== NEW: ROBUST SESSION MANAGER =====
        print("👥 Loading Telegram client sessions (Robust Session Manager)...")
        sessions_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
        os.makedirs(sessions_dir, exist_ok=True)

        session_mgr = SessionManager(
            sessions_collection=sessions_collection,
            sessions_dir=sessions_dir,
            api_id=API_ID,
            api_hash=API_HASH,
            health_check_interval=480,   # ping every 8 minutes
            connect_stagger=1.5,         # 1.5s delay between each session connect
            startup_wait=5.0,            # 5s wait before first connect (IP stabilize)
            on_dead_session=on_dead_session_alert,
        )

        # Share the same list object so register_client_as_active and
        # session_mgr both read/write to the same pool.
        session_mgr.active_clients = active_clients

        await session_mgr.load_all()

        print(f"📁 Session files directory: {sessions_dir}")
        print("   OR add accounts via /admin -> Telegram Accounts")

        if get_active_clients():
            print(f"✅ {len(get_active_clients())} Telegram client(s) loaded and alive")
            print("📡 Setting up monitoring system...")
            await ub_moniter()
            print("✅ All clients are now monitoring channels")
        else:
            print("⚠️ No active Telegram clients found. Add .session files to sessions/ folder.")

        # Start background health monitor (auto-reconnect + dead session detection)
        asyncio.create_task(session_mgr.health_monitor_loop())
        print("🔍 Session health monitor started (8-min interval)")

        print("⚙️ Starting background tasks...")
        asyncio.create_task(task_process_manual_orders())
        asyncio.create_task(task_reset_daily_posts())
        asyncio.create_task(expire_due_orders())

        # Setup night mode automation scheduler
        print("🌙 Setting up Night Mode automation...")
        setup_night_mode_scheduler()

        # ===== ACCOUNTS SHOP SETUP ===== #
        print("🛒 Setting up Accounts Shop module...")
        try:
            accounts_shop.setup_accounts_shop(
                bot=bot,
                admin_id=ADMIN_ID,
                api_id=API_ID,
                api_hash=API_HASH,
                mongo_url=MONGODB_URI,
                oxapay_key=OXAPAY_API_KEY,
                ngrok_url=public_url or ""
            )
            print("✅ Accounts Shop module initialized")
        except Exception as e:
            print(f"⚠️ Accounts Shop init warning: {e}")

        print("✅ Bot started successfully!")
        print(f"👤 Admin ID: {ADMIN_ID}")
        print("📱 Waiting for updates...")

        await dp.start_polling(bot)

    except Exception as e:
        print(f"❌ FATAL ERROR during startup: {e}")
        import traceback
        traceback.print_exc()
        raise


async def shutdown():
    """Cleanup function to properly close all clients"""
    print("🔄 Shutting down gracefully...")

    # Cancel all pending tasks
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()

    # Wait for tasks to be cancelled
    await asyncio.gather(*tasks, return_exceptions=True)

    # Disconnect all clients via session manager
    if session_mgr is not None:
        await session_mgr.disconnect_all()
    else:
        for i, client in enumerate(get_active_clients()):
            try:
                if client.is_connected():
                    await client.disconnect()
                    print(f"✅ Client {i} disconnected")
            except Exception as e:
                print(f"⚠️ Error disconnecting client {i}: {e}")

    print("✅ All clients disconnected")
    print("✅ Cleanup completed")

if __name__ == "__main__":
    import signal

    def signal_handler(sig, frame):
        print("\n⏹️ Received shutdown signal")
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n⏹️ Bot stopped by user")
    except Exception as e:
        print(f"❌ Bot crashed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("🔄 Running cleanup...")
        try:
            loop.run_until_complete(shutdown())
        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")
        finally:
            loop.close()
            print("✅ Event loop closed")