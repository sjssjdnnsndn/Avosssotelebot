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

# ===== CONFIGURATION ===== #
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8387013883:AAG0HiQYlK2GaoAOijqrj81bO6VA3vGfAWg")
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://shuvamoy1:shuvamoy1@bableview.5yaumiu.mongodb.net/")
DB_NAME = "newviewsbot"
ADMIN_ID = 6498333937  # Admin ID
PAYMENT_ADMIN_ID = 8094204927  # Admin to receive payment notifications
MAJOR_ADMIN_ID = 6498333937  # Major admin who can make/remove other admins and manage powers

# Telegram API credentials
API_ID = 23026955
API_HASH = "1efec7fe2abe4e2a0dc4c07d0fdd6593"

# OxaPay Configuration
OXAPAY_API_KEY = "QRQPGP-46A7PP-MZNKDG-ODWF8V"
NGROK_AUTH_TOKEN = "31GMWCtWZPp6tOq5S0IKTjo0jdo_ihmpkxvFydGBoWWvC19U"

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

# Global variables
active_clients = []
user_oxapay_orders = {}
public_url = None

# Initialize Flask server for OxaPay webhook
flask_app = Flask(__name__)

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
    base_delay = calculate_delay_for_speed(speed_multiplier)
    
    # Calculate total time in seconds
    # If multiple clients, they work in parallel
    total_time_seconds = (total_quantity / max(clients_count, 1)) * base_delay
    
    # Convert to hours and minutes
    hours = int(total_time_seconds // 3600)
    minutes = int((total_time_seconds % 3600) // 60)
    
    return hours, minutes

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
        session_file,  # Direct file path
        use_api_id,
        use_api_hash
    )

async def store_session(phone, session_string, user_data, api_id=None, api_hash=None):
    session_data = {
        "phone": phone,
        "session_string": session_string,
        "user_id": user_data.id if user_data else None,
        "first_name": user_data.first_name if user_data else "",
        "last_name": user_data.last_name if user_data else "",
        "username": user_data.username if user_data else "",
        "api_id": api_id,
        "api_hash": api_hash,
        "created_at": datetime.utcnow()
    }
    await sessions_collection.insert_one(session_data)

async def get_all_sessions():
    return await sessions_collection.find({}).to_list(None)

async def remove_session(phone):
    await sessions_collection.delete_one({"phone": phone})


async def load_all_clients_from_files():
    """Load clients from .session files - MORE STABLE approach"""
    global active_clients
    active_clients = []
    
    print("\n" + "="*50)
    print("🔄 Loading Telegram Accounts from Session Files...")
    print("="*50)
    
    import glob
    session_files = glob.glob("/app/backend/sessions/*.session")
    
    if not session_files:
        print("⚠️ No .session files found in /app/backend/sessions/")
        print("💡 Place your .session files in /app/backend/sessions/ directory")
        print("="*50 + "\n")
        return
    
    print(f"📊 Found {len(session_files)} session file(s)\n")
    
    loaded_count = 0
    failed_count = 0
    
    for idx, session_file in enumerate(session_files, 1):
        session_name = os.path.basename(session_file).replace('.session', '')
        print(f"[{idx}/{len(session_files)}] Processing: {session_name}")
        
        try:
            # Create client from file
            client = await create_telegram_client_from_file(
                session_file.replace('.session', ''),  # Telethon adds .session automatically
                api_id=API_ID,
                api_hash=API_HASH
            )
            
            # Connect
            try:
                await client.connect()
                print(f"  🔗 Connected successfully")
            except Exception as conn_err:
                print(f"  ❌ Connection failed: {conn_err}")
                failed_count += 1
                continue
            
            # Verify authorization
            try:
                if not await client.is_user_authorized():
                    print(f"  ❌ Session not authorized (expired/invalid)")
                    await client.disconnect()
                    failed_count += 1
                    continue
            except Exception as auth_err:
                print(f"  ❌ Auth check failed: {auth_err}")
                await client.disconnect()
                failed_count += 1
                continue
            
            # Get user info
            try:
                me = await client.get_me()
                name = me.first_name or "Unknown"
                username = f"@{me.username}" if me.username else "No username"
                phone = me.phone or "No phone"
                print(f"  ✅ Loaded: {name} {username} ({phone})")
                
                # Add to active clients
                active_clients.append(client)
                loaded_count += 1
            except Exception as e:
                print(f"  ⚠️ Error getting user info: {e}")
                # Still add if connection is valid
                active_clients.append(client)
                loaded_count += 1
        
        except Exception as e:
            print(f"  ❌ Failed to load: {e}")
            failed_count += 1
    
    print("\n" + "="*50)
    print(f"✅ Successfully loaded: {loaded_count} account(s)")
    if failed_count > 0:
        print(f"❌ Failed to load: {failed_count} account(s)")
    print("="*50)
    
    if active_clients:
        print(f"👑 MASTER CLIENT: Client #0")
        if len(active_clients) > 1:
            print(f"👷 WORKER CLIENTS: {len(active_clients) - 1} workers")
    else:
        print("⚠️ WARNING: No active clients available!")
    print("="*50 + "\n")

async def load_all_clients():
    """Load clients from database (legacy method - kept for backward compatibility)"""
    global active_clients
    active_clients = []  # Clear existing clients
    
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
            
            if not api_id or not api_hash:
                print(f"  ⚠️ Missing API credentials, using defaults")
            
            client = await create_telegram_client(
                session['session_string'],
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
                
                # Add to active clients
                active_clients.append(client)
                loaded_count += 1
            except Exception as e:
                print(f"  ⚠️ Error getting user info: {e}")
                # Still add to active clients if connection is valid
                active_clients.append(client)
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
    if active_clients:
        print(f"👑 MASTER CLIENT: Client #0")
        if len(active_clients) > 1:
            print(f"👷 WORKER CLIENTS: {len(active_clients) - 1} workers")
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


async def process_vote_order(client, channel_id, message_id, option_index):
    try:
        await client(functions.account.UpdateStatusRequest(offline=False))
        # Get the input peer
        channel_entity = await client.get_entity(PeerChannel(channel_id))
        input_channel = InputPeerChannel(channel_entity.id, channel_entity.access_hash)

        # Create the vote request
        await client(functions.messages.SendVoteRequest(
            peer=input_channel,
            msg_id=message_id,
            options=str(option_index)  # This is the byte representation of the option index
        ))
        return True
    except Exception as e:
        print(f"Error sending vote: {e}")
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
        "delivered_reactions": delivered_reactions
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
            KeyboardButton(text="👤 My Account"),
            KeyboardButton(text="📦 Active Orders")
        ],
        [
            KeyboardButton(text="📝📊 Manual Views"),
            KeyboardButton(text="📝❤️‍🔥 Manual Reactions")
        ],
        [
            KeyboardButton(text="💳 Add Balance")
        ],
        [
            KeyboardButton(text="🗳️ Order Votes")
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

def get_my_channels_keyboard(channels):
    """Create a keyboard with user's connected channels"""
    builder = ReplyKeyboardBuilder()
    for channel in channels:
        builder.add(KeyboardButton(text=f"📢 {channel['channel_title']}"))
    builder.adjust(1)
    builder.row(KeyboardButton(text="⬅️ Back"))
    return builder.as_markup(resize_keyboard=True)

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
    SELECTING_CHANNEL = State()
    WAITING_FOR_INVITE_LINK = State()

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

# ===== CONFIGURATION CLASS ===== #
class ConfigData:
    def __init__(self):
        self.max_views = float('inf')  # Unlimited
        self.total_views = 10
        self.total_reactions = 10  # used only in reactions
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

async def get_config_text(config: ConfigData, is_reactions: bool = False) -> str:
    pricing = await get_pricing()
    # Use speed config function to get emoji
    speed_name, speed_emoji = get_speed_name(config.speed_multiplier)
    if is_reactions:
        reactions_count = config.total_reactions if hasattr(config, "total_reactions") else config.total_views
        return (
            "*⚙️ System Configuration:*\n"
            "_Please configure the necessary parameters to initiate the process!_\n\n"
            f"❤️‍🔥 *Maximum Reactions Limit:* `Unlimited ∞`\n\n"
            f"📊 *Reaction Per Post:* `{reactions_count}`\n"
            f"📝 *Posts Per Day:* `{config.posts_per_day}`\n"
            f"📆 *Number of Days:* `{config.days}`\n"
            f"{speed_emoji} *Delivery Speed:* `{config.speed_name}` (x{config.speed_multiplier})\n\n"
            f"💰 *Total Charge:* `${config.charge:.4f}` (`{await usd_to_inr_converter(config.charge)}`)"
        )
    else:
        return (
            "*⚙️ System Configuration:*\n"
            "_Please configure the necessary parameters to initiate the process!_\n\n"
            f"👁️ *Maximum View Limit:* `Unlimited ∞`\n\n"
            f"📊 *Views Per Post:* `{config.total_views}`\n"
            f"📝 *Posts Per Day:* `{config.posts_per_day}`\n"
            f"📆 *Number of Days:* `{config.days}`\n"
            f"{speed_emoji} *Delivery Speed:* `{config.speed_name}` (x{config.speed_multiplier})\n\n"
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

    # Speed adjustment buttons
    speed_emoji = "🐌" if config.speed_multiplier == 0.5 else ("🐢" if config.speed_multiplier == 1.0 else ("🚀" if config.speed_multiplier == 1.5 else "⚡"))
    builder.row(
        InlineKeyboardButton(text=f"{speed_emoji} Speed: {config.speed_name}", callback_data="show_speed")
    )
    builder.row(
        InlineKeyboardButton(text="🐌 Slow (7-8 hrs)", callback_data="speed:slow"),
        InlineKeyboardButton(text="🐢 Normal (5-6 hrs)", callback_data="speed:normal"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text="🚀 Fast (3-4 hrs)", callback_data="speed:fast"),
        InlineKeyboardButton(text="⚡ Ultra Fast (2-3 hrs)", callback_data="speed:ultra"),
        width=2
    )

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

    # Speed adjustment buttons
    speed_emoji = "🐌" if config.speed_multiplier == 0.5 else ("🐢" if config.speed_multiplier == 1.0 else ("🚀" if config.speed_multiplier == 1.5 else "⚡"))
    builder.row(
        InlineKeyboardButton(text=f"{speed_emoji} Speed: {config.speed_name}", callback_data="show_speed")
    )
    builder.row(
        InlineKeyboardButton(text="🐌 Slow (7-8 hrs)", callback_data="speed:slow"),
        InlineKeyboardButton(text="🐢 Normal (5-6 hrs)", callback_data="speed:normal"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text="🚀 Fast (3-4 hrs)", callback_data="speed:fast"),
        InlineKeyboardButton(text="⚡ Ultra Fast (2-3 hrs)", callback_data="speed:ultra"),
        width=2
    )

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

    # Get active auto orders
    active_orders = await orders_collection.find({
        "user_id": user_id,
        "status": {"$in": ["confirmed", "processing"]},
        "service_identifier": {"$in": ["views_by_followers", "reactions_by_followers"]}
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

        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        days_passed = (datetime.utcnow() - created).days
        days_remaining = max(0, days - days_passed)
        posts_done_today = len(order.get("processed_today", {}).get(today_str, []))
        posts_left_today = max(0, posts_per_day - posts_done_today)

        if order["service_identifier"] == "views_by_followers":
            service_label = "Auto Follower Views"
            per_post = order.get("views_per_post", 0)
            delivered = order.get("delivered_views", 0)
            total = order.get("total_views", 0)
        else:
            service_label = "Auto Follower Reactions"
            per_post = order.get("reactions_per_post", 0)
            delivered = order.get("delivered_reactions", 0)
            total = order.get("total_reactions", 0)

        if is_paused:
            status_text = "⏸ <i>This campaign is currently paused.</i>"
        else:
            status_text = "▶️ <i>Your campaign is currently active.</i>"

        speed_emoji = "🐌" if speed_multiplier == 0.5 else ("🐢" if speed_multiplier == 1.0 else ("🚀" if speed_multiplier == 1.5 else "⚡"))

        text = (
            f"📢 <b>Channel:</b> <i>{channel_title}</i> <code>(ID: {channel_id})</code>\n\n"
            f"🎯 <b>Service:</b> <code>{service_label}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Per Post:</b> <code>{per_post}</code>\n"
            f"📝 <b>Daily Posts:</b> <code>{posts_per_day}</code>\n"
            f"📆 <b>Plan Duration:</b> <code>{days} Days</code>\n"
            f"{speed_emoji} <b>Speed:</b> <code>{speed_name}</code> (x{speed_multiplier}) - FREE\n"
            f"💰 <b>Price:</b> <code>${charge:.3f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🟡 <b>Remaining Today:</b> <code>{posts_left_today} Post(s)</code>\n"
            f"⏳ <b>Time Left:</b> <code>{days_remaining} Day(s)</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_text}"
        )

        # Create speed control buttons
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🐌 Slow (7-8 hrs)", callback_data=f"change_speed:{order['_id']}:slow"),
            InlineKeyboardButton(text="🐢 Normal (5-6 hrs)", callback_data=f"change_speed:{order['_id']}:normal"),
            width=2
        )
        builder.row(
            InlineKeyboardButton(text="🚀 Fast (3-4 hrs)", callback_data=f"change_speed:{order['_id']}:fast"),
            InlineKeyboardButton(text="⚡ Ultra Fast (2-3 hrs)", callback_data=f"change_speed:{order['_id']}:ultra"),
            width=2
        )

        # Add pause/resume button
        if is_paused:
            builder.row(InlineKeyboardButton(text="▶️ Resume", callback_data=f"resume:{order['_id']}"))
        else:
            builder.row(InlineKeyboardButton(text="⏸ Pause", callback_data=f"pause:{order['_id']}"))

        # Add cancel order button
        builder.row(InlineKeyboardButton(text="❌ Cancel Order", callback_data=f"cancel_order:{order['_id']}"))

        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

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
    await state.update_data(service_type="poll_votes")
    await state.set_state(OrderStates.waiting_for_content)
    await message.answer(
        "📨 Please forward the poll you want to boost\n"
        "Or press '⬅️ Cancel Order' to go back",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel Order")]],
            resize_keyboard=True
        )
    )

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
        client = active_clients[0] if active_clients else None
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

                destination_chat_id = -1002859595679

                forwarded = await message.forward(
                    chat_id=destination_chat_id,
                )
                # print(forwarded)
                if not forwarded.poll:
                    await message.answer("❌ This forwarded message doesn't contain a poll.")
                    return

                poll_options = [opt.text for opt in forwarded.poll.options]

                await state.update_data(
                    channel_id=destination_chat_id,
                    poll_id=forwarded.poll.id,
                    poll_question=forwarded.poll.question,
                    poll_options=poll_options,
                    content_id=forwarded.message_id  # ✅ Correct: This is the destination message ID
                )

                builder = ReplyKeyboardBuilder()
                for i, option in enumerate(poll_options, start=1):
                    builder.add(KeyboardButton(text=f"{i}️⃣ {option[:15]}"))
                builder.adjust(2)
                builder.row(KeyboardButton(text="⬅️ Cancel Order"))

                await message.answer(
                    f"🗳️ Selected: <b>{forwarded.poll.question[:50]}</b>\n"
                    "👉 Which option do you want to boost?",
                    reply_markup=builder.as_markup(resize_keyboard=True),
                    parse_mode="HTML"
                )
                await state.set_state(OrderStates.waiting_for_option)


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

    await message.answer(
        f"✅ Selected: {selected_option}\n"
        "🔢 How many votes would you like?\n"
        "Or press '⬅️ Cancel Order' to go back",
        reply_markup=get_quantity_keyboard()
    )
    await state.set_state(OrderStates.waiting_for_quantity)


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

    await state.update_data(charge=charge)

    if data['service_type'] == 'manual_reactions':
        response = (
            f"✅ Do You Want To Place Reaction Order!\n\n"
            f"{data.get('reaction_emoji', '❤️')} {quantity} reactions\n"
            f"📌 Channel: {data['channel_title']}\n"
            f"🔄 Charges: ${charge:.4f}"
        )
    elif data['service_type'] == 'manual_views':
        response = (
            f"✅ Do You Want To Place View Order!\n\n"
            f"👀 {quantity} views\n"
            f"📌 Channel: {data['channel_title']}\n"
            f"🔄 Charges: ${charge:.4f}"
        )
    else:  # votes
        response = (
            f"✅ Do You Want To Place Vote Order!\n\n"
            f"🗳️ {quantity} votes\n"
            f"📌 Poll: {data['poll_question'][:30]}...\n"
            f"👉 Selected Option: {data.get('selected_option', '')}\n"
            f"🔄 Charges: ${charge:.4f}"
        )

    await message.answer(
        response,
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

    await update_user_balance(user_id, -charge)

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

    # Store channels in state for selection
    await state.update_data(available_channels=channels)
    await state.set_state(OrderStates.SELECTING_CHANNEL)

    await message.answer(
        "📢 Select a channel to view its order details:",
        reply_markup=get_my_channels_keyboard(channels)
    )


@dp.callback_query(F.data.startswith("change_speed:"))
async def change_order_speed(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    order_id = parts[1]
    speed_type = parts[2]

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

    # Update order speed (NO CHARGE - FREE feature)
    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {
            "speed_multiplier": speed_multiplier,
            "speed_name": speed_name,
            "updated_at": datetime.utcnow()
        }}
    )

    # Get updated order
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})

    # Prepare updated message
    channel_title = order.get("channel_title", "Unknown Channel")
    channel_id = order.get("channel_id")
    posts_per_day = order.get("posts_per_day", 0)
    charge = order.get("charge", 0.0)
    days = order.get("days", 0)
    created = order.get("created_at")
    is_paused = order.get("is_paused", False)

    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    days_passed = (datetime.utcnow() - created).days
    days_remaining = max(0, days - days_passed)
    posts_done_today = len(order.get("processed_today", {}).get(today_str, []))
    posts_left_today = max(0, posts_per_day - posts_done_today)

    if order["service_identifier"] == "views_by_followers":
        service_label = "Auto Follower Views"
        per_post = order.get("views_per_post", 0)
        delivered = order.get("delivered_views", 0)
        total = order.get("total_views", 0)
    else:
        service_label = "Auto Follower Reactions"
        per_post = order.get("reactions_per_post", 0)
        delivered = order.get("delivered_reactions", 0)
        total = order.get("total_reactions", 0)

    if is_paused:
        status_text = "⏸ <i>This campaign is currently paused.</i>"
    else:
        status_text = "▶️ <i>Your campaign is currently active.</i>"

    speed_emoji = "🐌" if speed_multiplier == 0.5 else ("🐢" if speed_multiplier == 1.0 else ("🚀" if speed_multiplier == 1.5 else "⚡"))

    text = (
        f"📢 <b>Channel:</b> <i>{channel_title}</i> <code>(ID: {channel_id})</code>\n\n"
        f"🎯 <b>Service:</b> <code>{service_label}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Per Post:</b> <code>{per_post}</code>\n"
        f"📝 <b>Daily Posts:</b> <code>{posts_per_day}</code>\n"
        f"📆 <b>Plan Duration:</b> <code>{days} Days</code>\n"
        f"{speed_emoji} <b>Speed:</b> <code>{speed_name}</code> (x{speed_multiplier}) - FREE\n"
        f"💰 <b>Price:</b> <code>${charge:.3f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🟡 <b>Remaining Today:</b> <code>{posts_left_today} Post(s)</code>\n"
        f"⏳ <b>Time Left:</b> <code>{days_remaining} Day(s)</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_text}"
    )

    # Create speed control buttons
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

    # Add pause/resume button
    if is_paused:
        builder.row(InlineKeyboardButton(text="▶️ Resume", callback_data=f"resume:{order_id}"))
    else:
        builder.row(InlineKeyboardButton(text="⏸ Pause", callback_data=f"pause:{order_id}"))

    # Add cancel order button
    builder.row(InlineKeyboardButton(text="❌ Cancel Order", callback_data=f"cancel_order:{order_id}"))

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer(f"✅ Speed changed to {speed_name} (FREE)", show_alert=True)

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

    channel_id = order.get("channel_id")
    channel_title = order.get("channel_title", "Unknown")
    posts_per_day = order.get("posts_per_day", 0)
    charge = order.get("charge", 0.0)
    days = order.get("days", 0)
    created = order.get("created_at")
    is_paused = order.get("is_paused", False)

    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    days_passed = (datetime.utcnow() - created).days
    days_remaining = max(0, days - days_passed)
    posts_done_today = len(order.get("processed_today", {}).get(today_str, []))
    posts_left_today = max(0, posts_per_day - posts_done_today)

    if order["service_identifier"] == "views_by_followers":
        service_label = "Auto Follower Views"
        per_post = order.get("views_per_post", 0)
        delivered = order.get("delivered_views", 0)
        total = order.get("total_views", 0)
    else:
        service_label = "Auto Follower Reactions"
        per_post = order.get("reactions_per_post", 0)
        delivered = order.get("delivered_reactions", 0)
        total = order.get("total_reactions", 0)

    if order["status"] == "completed":
        status_text = "☑️ <i>This campaign is completed.</i>"
    elif is_paused:
        status_text = "⏸ <i>This campaign is currently paused.</i>"
    else:
        status_text = "▶️ <i>Your campaign is currently active.</i>"

    text = (
        f"📢 <b>Channel:</b> <i>{channel_title}</i> <code>(ID: {channel_id})</code>\n\n"
        f"🎯 <b>Service:</b> <code>{service_label}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Per Post:</b> <code>{per_post}</code>\n"
        f"📝 <b>Daily Posts:</b> <code>{posts_per_day}</code>\n"
        f"📆 <b>Plan Duration:</b> <code>{days} Days</code>\n"
        f"💰 <b>Price:</b> <code>${charge:.3f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🟡 <b>Remaining Today:</b> <code>{posts_left_today} Post(s)</code>\n"
        f"⏳ <b>Time Left:</b> <code>{days_remaining} Day(s)</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_text}"
    )

    # Generate dynamic button
    if order["status"] != "completed":
        button_text = "▶️ Resume" if is_paused else "⏸ Pause"
        button_action = "resume" if is_paused else "pause"
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=button_text, callback_data=f"{button_action}:{order_id}")
        ]])
    else:
        markup = None

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await callback.answer(alert_text)

@dp.callback_query(F.data.startswith("cancel_order:"))
async def cancel_order_callback(callback: types.CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":")[1]

    # Find the order
    order = await orders_collection.find_one({"_id": ObjectId(order_id)})

    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return

    # Check if order is already completed or cancelled
    if order["status"] in ["completed", "cancelled"]:
        await callback.answer("This order is already completed or cancelled.", show_alert=True)
        return

    # Calculate partial refund
    charge = order.get("charge", 0.0)
    created_at = order.get("created_at")
    days = order.get("days", 0)
    now = datetime.utcnow()

    if days > 0:
        # Calculate portion delivered based on days passed
        days_passed = (now - created_at).days
        delivery_progress = min(1.0, max(0.0, days_passed / days))
    else:
        delivery_progress = 0.0  # Avoid division by zero if days is 0

    # Calculate the cost of delivered service (pro-rated)
    cost_of_delivered_service = charge * delivery_progress

    # Calculate the refund amount
    refund_amount = charge - cost_of_delivered_service

    # Update order status to 'cancelled' and set refund amount
    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {
            "$set": {
                "status": "cancelled",
                "cancellation_time": now,
                "refund_amount": round(refund_amount, 4),
                "updated_at": now
            }
        }
    )

    # Grant refund to user balance
    if refund_amount > 0:
        await update_user_balance(order["user_id"], refund_amount)

    # Notify user
    await bot.send_message(
        order["user_id"],
        f"✅ <b>Order Cancelled!</b>\n\n"
        f"ID: <code>{order_id}</code>\n"
        f"Status: <b>Cancelled</b>\n"
        f"💰 Refund granted: <b>${refund_amount:.4f}</b>\n\n"
        "The amount has been added to your balance.",
        parse_mode="HTML"
    )

    await callback.answer("Order cancelled successfully!", show_alert=True)
    await callback.message.delete() # Remove the original message
    # Optionally, you can send a message back to the user indicating cancellation

@dp.message(OrderStates.SELECTING_CHANNEL)
async def handle_channel_selection(message: types.Message, state: FSMContext):
    data = await state.get_data()
    channels = data.get('available_channels', [])
    selected_title = message.text.replace("📢 ", "")

    selected_channel = next((ch for ch in channels if ch['channel_title'] == selected_title), None)

    if not selected_channel:
        await message.answer("❌ Invalid channel selection. Please choose from the list.")
        return

    channel_id = selected_channel['channel_id']
    channel_title = selected_channel['channel_title']

    orders = await orders_collection.find({
        "channel_id": channel_id,
        "service_identifier": {"$in": ["views_by_followers", "reactions_by_followers"]},
        "status": {"$in": ["confirmed", "processing"]}
    }).sort("created_at", -1).to_list(None)

    if not orders:
        await message.answer(
            f"ℹ️ No auto orders found for channel: {channel_title}",
            reply_markup=get_main_menu_keyboard()
        )
        await state.clear()
        return

    today_str = datetime.utcnow().strftime("%Y-%m-%d")
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
            continue # Skip completed orders

        remaining_today_posts = 0
        if order.get("processed_today"):
            posts_done_today = len(order.get("processed_today", {}).get(today_str, []))
            remaining_today_posts = max(0, posts_per_day - posts_done_today)
        else:
            remaining_today_posts = posts_per_day # If processed_today is not set yet, assume all are remaining

        # Status line
        if is_paused:
            status_text = "⏸ <i>This campaign is currently paused.</i>"
        else:
            status_text = "▶️ <i>Your campaign is currently active.</i>"

        # Compose message
        text = (
            f"📢 <b>Channel:</b> <i>{channel_title}</i> <code>(ID: {channel_id})</code>\n\n"
            f"🎯 <b>Service:</b> <code>{order.get('service_identifier', 'Unknown')}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Per Post:</b> <code>{order.get('views_per_post', order.get('reactions_per_post', 0))}</code>\n"
            f"📝 <b>Daily Posts:</b> <code>{posts_per_day}</code>\n"
            f"📆 <b>Plan Duration:</b> <code>{days} Days</code>\n"
            f"💰 <b>Price:</b> <code>${charge:.3f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🟡 <b>Remaining Today:</b> <code>{remaining_today_posts} Post(s)</code>\n"
            f"⏳ <b>Time Left:</b> <code>{days_remaining} Day(s)</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_text}"
        )

        # Buttons
        buttons = []
        if not is_paused:
            buttons.append(InlineKeyboardButton(text="⏸ Pause", callback_data=f"pause:{order['_id']}"))
        else:
            buttons.append(InlineKeyboardButton(text="▶️ Resume", callback_data=f"resume:{order['_id']}"))

        # Add cancel order button
        buttons.append(InlineKeyboardButton(text="❌ Cancel Order", callback_data=f"cancel_order:{order['_id']}"))

        markup = InlineKeyboardMarkup(inline_keyboard=[buttons])

        await message.answer(text, parse_mode="HTML", reply_markup=markup)
    await message.answer('Main Menu', reply_markup=get_main_menu_keyboard())

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
        caption = (
            f"🇮🇳 <b>UPI Payment</b>\n\n"
            f"💰 <b>Amount:</b> <code>${amount:.2f}</code> (~{inr_amount})\n"
            f"🏦 <b>UPI ID:</b> <code>paytm.s1lmr2p@pty</code>\n\n"
            f"📩 <i>Send the payment screenshot here after completing the transaction.</i>"
        )
        await bot.send_photo(
            chat_id=message.chat.id,
            photo="https://i.ibb.co/Fb5hpJSn/image.jpg",
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

    data = await state.get_data()
    user_id = message.from_user.id
    shared_chat_id = data.get('channel_id')

    # ✅ Fixed: Properly check if active_clients list is empty
    if not active_clients:
        await message.answer(
            "❌ No active Telegram clients available.\n\n"
            "Please contact the admin to add Telegram accounts for the bot to work.",
            reply_markup=get_main_menu_keyboard()
        )
        await state.clear()
        return
    
    client = active_clients[0]

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
                if new_total < 10:
                    raise ValueError("Below minimum")
                # No max limit check - unlimited
                config.total_views = new_total

            elif field == "posts":
                config.posts_per_day = max(10, config.posts_per_day + value)
            elif field == "days":
                config.days = max(10, config.days + value)

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

    except Exception:
        await callback.answer("❌ Minimum order value reached!", show_alert=True)


# === Reaction Callback === #
@dp.callback_query(F.data.startswith(("r_posts:", "r_days:", "reactions:", "speed:")))
async def handle_reaction_adjustment(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    config = user_configs.get(user_id)

    if not config:
        return await callback.answer("Session expired. Please start again.", show_alert=True)

    try:
        field, value = callback.data.split(":")

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

            if field == "reactions":
                if not hasattr(config, "total_reactions"):
                    config.total_reactions = config.total_views
                new_total = config.total_reactions + value
                if new_total < 10:
                    raise ValueError("Below minimum")
                # No max limit check - unlimited
                config.total_reactions = new_total

            elif field == "r_posts":
                config.posts_per_day = max(10, config.posts_per_day + value)
            elif field == "r_days":
                config.days = max(10, config.days + value)

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

    await callback.message.edit_text(
        f"📝 {order_type} Order Summary\n\n"
        f"🔹 Channel: {config.channel_title}\n"
        f"🔹 Total {order_type}: {config.total_views}\n"
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
    await callback.message.delete()
    await callback.message.answer(
        "Main Menu",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("show_"))
async def handle_show_info(callback: types.CallbackQuery):
    await callback.answer("🔻 Select the Options Below to Modify 🔻", show_alert=False)

@dp.callback_query(F.data == "payment:confirm")
async def handle_payment_confirm(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    config = user_configs.get(user_id)
    if not config:
        return await callback.answer("Session expired. Please start again.", show_alert=True)

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
            "reactions_per_post": config.total_reactions if hasattr(config, "total_reactions") else config.total_views, # Use total_reactions if available, else default to views_per_post
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
        f"⏳ <b>Duration:</b> <code>{config.days} Days</code>\n"
        f"{speed_emoji} <b>Speed:</b> <code>{config.speed_name}</code> (x{config.speed_multiplier})\n\n"

        f"💰 <b>Charge:</b> <code>${config.charge:.4f}</code>\n\n"

        f"🔄 <i>Auto-service started! {metric.capitalize()} will be delivered at {config.speed_name} speed.</i>",
        parse_mode="HTML"
    )

    invite_link = getattr(config, 'invite_link', None)
    if invite_link and active_clients:
        asyncio.create_task(join_all_clients_to_channel(config.channel_id, invite_link))

    await callback.message.answer("Main Menu", reply_markup=get_main_menu_keyboard())
    await callback.answer()
    await state.clear()


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

def render_account_page(sessions, page: int, total_pages: int):
    start = page * ACCOUNTS_PER_PAGE
    end = start + ACCOUNTS_PER_PAGE
    text = f"📱 <b>Telegram Account Management</b>\n\n"
    text += f"🔹 <b>Logged-in Accounts (Page {page+1}/{total_pages})</b>:\n\n"

    for i, session in enumerate(sessions[start:end], start + 1):
        # Get status with emoji indicator
        status = session.get('status', 'unknown')
        if status == 'active':
            status_emoji = "✅"
            status_text = "Active"
        elif status == 'unauthorized':
            status_emoji = "⚠️"
            status_text = "Expired - Needs Re-login"
        elif status == 'connection_error':
            status_emoji = "🔌"
            status_text = "Connection Error"
        elif status == 'error':
            status_emoji = "❌"
            status_text = "Error"
        elif status == 'load_error':
            status_emoji = "⚠️"
            status_text = "Load Error"
        else:
            status_emoji = "❓"
            status_text = "Unknown"
        
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
            f"{i}. {session['phone']} - @{session.get('username', 'N/A')}\n"
            f"   {status_emoji} <i>{status_text}</i> (Checked: {last_check_str})\n"
        )
        
        # Show error if present
        last_error = session.get('last_error')
        if last_error and status != 'active':
            error_preview = last_error[:50] + "..." if len(last_error) > 50 else last_error
            text += f"   💬 <code>{error_preview}</code>\n"
        text += "\n"

    text += "\n💡 <b>Status Guide:</b>\n"
    text += "✅ Active - Working normally\n"
    text += "⚠️ Expired - Session needs re-authentication\n"
    text += "🔌 Connection Error - Network/API issue (may auto-recover)\n"
    text += "❌ Error - Needs attention\n\n"
    text += "<i>Sessions are NEVER auto-deleted. You have full control.</i>"

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

    sessions = await get_all_sessions()
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
        InlineKeyboardButton(text="🗑️ Remove Account", callback_data="remove_account"),
        InlineKeyboardButton(text="🔁 Refresh", callback_data="refresh_accounts")
    ]

    # Build keyboard layout
    keyboard_layout = []
    if nav_buttons:  # Add navigation buttons if present
        keyboard_layout.append(nav_buttons)
    keyboard_layout.extend([buttons_row1, buttons_row2, buttons_row3, buttons_row4])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_layout)
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("acc_page:"))
async def handle_account_pagination(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[1])
    sessions = await get_all_sessions()
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
        InlineKeyboardButton(text="🗑️ Remove Account", callback_data="remove_account"),
        InlineKeyboardButton(text="🔁 Refresh", callback_data="refresh_accounts")
    ]


    keyboard_layout = []
    if nav_buttons:
        keyboard_layout.append(nav_buttons)
    keyboard_layout.extend([action_buttons_row1, action_buttons_row2, action_buttons_row3, action_buttons_row4])
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
        await store_session(phone, session_string, user, api_id=api_id, api_hash=api_hash)
        await client.disconnect()

        # Reload clients
        await load_all_clients()

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
        await store_session(phone, session_string, user, api_id=api_id, api_hash=api_hash)
        await client.disconnect()

        # Reload clients
        await load_all_clients()

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
        await store_session(phone, final_session_string, user, api_id=api_id, api_hash=api_hash)
        await client.disconnect()

        # Reload clients
        await load_all_clients()

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
        await store_session(phone, final_session_string, user, api_id=api_id, api_hash=api_hash)
        await client.disconnect()

        # Reload clients
        await load_all_clients()

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
            phone = user.phone or "Unknown"
            
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
            await store_session(phone, session_string, user, api_id=api_id, api_hash=api_hash)
            await client.disconnect()

            # Reload clients
            await load_all_clients()

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
    sessions = await get_all_sessions()
    if not sessions:
        await callback.answer("No accounts found in DB.")
        return

    # Reload all clients from database
    await load_all_clients()

    # After reload, refresh the current page view
    total_pages = max(1, (len(sessions) + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE)
    page = 0  # Go back to first page after refresh
    
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
        InlineKeyboardButton(text="🗑️ Remove Account", callback_data="remove_account"),
        InlineKeyboardButton(text="🔁 Refresh", callback_data="refresh_accounts")
    ]
    
    keyboard_layout = []
    if nav_buttons:
        keyboard_layout.append(nav_buttons)
    keyboard_layout.extend([action_buttons_row1, action_buttons_row2, action_buttons_row3, action_buttons_row4])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_layout)
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer("✅ Accounts refreshed!", show_alert=False)



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
        "• Maximum 200 sessions per ZIP\n"
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
        if len(session_files) > 200:
            await processing_msg.edit_text(
                f"❌ <b>Too Many Sessions</b>\n\n"
                f"Found: {len(session_files)} sessions\n"
                f"Maximum: 200 sessions per ZIP\n\n"
                f"Please split into smaller batches.",
                parse_mode="HTML"
            )
            shutil.rmtree(temp_dir)
            await state.clear()
            return
        
        # Update message with count
        await processing_msg.edit_text(
            f"✅ <b>Found {len(session_files)} Session Files</b>\n\n"
            f"🚀 Starting bulk import...\n"
            f"⏱️ Estimated time: {len(session_files) * 23 // 60} minutes\n\n"
            f"📊 Progress: 0/{len(session_files)}",
            parse_mode="HTML"
        )
        
        # Process each session with delay
        success_count = 0
        failed_count = 0
        failed_sessions = []
        
        for idx, session_file in enumerate(session_files, 1):
            session_name = os.path.basename(session_file).replace('.session', '')
            
            try:
                # Copy session file to sessions directory
                dest_path = f"/app/backend/sessions/{session_name}.session"
                shutil.copy2(session_file, dest_path)
                
                # Create client from file
                client = await create_telegram_client_from_file(
                    f"/app/backend/sessions/{session_name}",
                    api_id=API_ID,
                    api_hash=API_HASH
                )
                
                # Try to connect
                await client.connect()
                
                # Check authorization
                if not await client.is_user_authorized():
                    failed_count += 1
                    failed_sessions.append({
                        'name': session_name,
                        'error': 'Session not authorized (expired/invalid)'
                    })
                    
                    # Send failure notification
                    await message.answer(
                        f"❌ <b>Session {idx}/{len(session_files)} Failed</b>\n\n"
                        f"📝 Name: <code>{session_name}</code>\n"
                        f"❗ Error: Session not authorized (expired/invalid)\n\n"
                        f"This session may have expired or been revoked.",
                        parse_mode="HTML"
                    )
                    
                    await client.disconnect()
                    os.remove(dest_path)  # Remove invalid session
                    
                    # Wait before next session
                    await asyncio.sleep(random.uniform(20, 25))
                    continue
                
                # Get user info
                me = await client.get_me()
                name = me.first_name or "Unknown"
                username = f"@{me.username}" if me.username else "No username"
                phone = me.phone or "No phone"
                
                # Save session string to database
                session_string = client.session.save()
                await store_session(
                    phone=phone,
                    session_string=session_string,
                    user_data=me,
                    api_id=API_ID,
                    api_hash=API_HASH
                )
                
                # Add to active clients
                active_clients.append(client)
                success_count += 1
                
                # Send success notification
                await message.answer(
                    f"✅ <b>Session {idx}/{len(session_files)} Success</b>\n\n"
                    f"👤 Name: {name}\n"
                    f"📱 Username: {username}\n"
                    f"☎️ Phone: {phone}\n"
                    f"🆔 User ID: <code>{me.id}</code>\n\n"
                    f"✅ Logged in successfully!",
                    parse_mode="HTML"
                )
                
            except ApiIdInvalidError:
                failed_count += 1
                failed_sessions.append({
                    'name': session_name,
                    'error': 'Invalid API credentials'
                })
                
                await message.answer(
                    f"❌ <b>Session {idx}/{len(session_files)} Failed</b>\n\n"
                    f"📝 Name: <code>{session_name}</code>\n"
                    f"❗ Error: Invalid API ID/Hash\n\n"
                    f"The session was created with different API credentials.",
                    parse_mode="HTML"
                )
                
            except Exception as e:
                failed_count += 1
                error_msg = str(e)[:100]
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
        
        # Final summary
        summary_text = (
            f"🎉 <b>Bulk Import Complete!</b>\n\n"
            f"📊 <b>Summary:</b>\n"
            f"• Total Sessions: {len(session_files)}\n"
            f"• ✅ Successfully Imported: {success_count}\n"
            f"• ❌ Failed: {failed_count}\n"
            f"• 🔥 Active Clients: {len(active_clients)}\n\n"
        )
        
        if failed_sessions:
            summary_text += f"⚠️ <b>Failed Sessions:</b>\n"
            for fs in failed_sessions[:10]:  # Show first 10
                summary_text += f"• {fs['name']}: {fs['error'][:50]}\n"
            
            if len(failed_sessions) > 10:
                summary_text += f"\n... and {len(failed_sessions) - 10} more\n"
        
        summary_text += f"\n✅ All successful sessions are now active and monitoring channels!"
        
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
        # Reload clients after removal
        await load_all_clients()

        await callback.answer("✅ Account removed successfully.")
        await callback.message.edit_text(
            f"❌ <b>Account Removed</b>\n\n"
            f"📞 Phone: {session.get('phone', 'N/A')}\n"
            f"👤 Username: @{session.get('username', 'N/A')}\n\n"
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

    builder = ReplyKeyboardBuilder()
    for session in sessions:
        builder.add(KeyboardButton(text=session['phone']))
    builder.adjust(2)
    builder.row(KeyboardButton(text="⬅️ Cancel"))

    await callback.message.answer(
        "Select an account to remove:",
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
        text += f"{i}. {session['phone']} - @{session.get('username', 'N/A')}\n"

    text += "\n📞 <b>Enter the phone number you want to remove:</b>\n<i>(Example: +1234567890)</i>"

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

    phone = message.text.strip()
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

    confirmation_text = (
        f"⚠️ <b>Confirm Account Removal</b>\n\n"
        f"📞 <b>Phone:</b> <code>{session['phone']}</code>\n"
        f"👤 <b>Name:</b> {session.get('first_name', 'N/A')} {session.get('last_name', '')}\n"
        f"🆔 <b>Username:</b> @{session.get('username', 'N/A')}\n"
        f"🆔 <b>User ID:</b> <code>{session.get('user_id', 'N/A')}</code>\n"
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

    # Reload active clients (disconnect removed client)
    global active_clients
    active_clients = []
    await load_all_clients()

    success_text = (
        f"✅ <b>Account Removed Successfully!</b>\n\n"
        f"📞 <b>Phone:</b> <code>{phone}</code>\n"
        f"👤 <b>Account:</b> @{session.get('username', 'N/A')}\n\n"
        f"🔄 Active clients reloaded: {len(active_clients)}\n"
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
            [KeyboardButton(text="⚡️ Powers")],
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
    
    # Check permission for non-major admins
    if message.from_user.id != MAJOR_ADMIN_ID and message.from_user.id != ADMIN_ID:
        user_permissions = user.get('admin_permissions', {})
        if not user_permissions.get('broadcast', True):
            await message.answer("⛔️ You don't have permission to broadcast messages.")
            return

    await message.answer(
        "📢 Enter broadcast message:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.BROADCASTING)

@dp.message(AdminStates.BROADCASTING)
async def confirm_broadcast(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Cancel":
        await state.clear()
        await admin_command(message,state)
        return

    # Store message and show confirmation
    await state.update_data(broadcast_message=message.text)
    await state.set_state(AdminStates.CONFIRM_BROADCAST)

    await message.answer(
        f"📢 Broadcast Message:\n\n{message.text}\n\n"
        "Send to all users?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Confirm", callback_data="broadcast_confirm")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="broadcast_cancel")]
        ])
    )

@dp.callback_query(F.data == "broadcast_confirm", AdminStates.CONFIRM_BROADCAST)
async def send_broadcast(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    message_text = data.get('broadcast_message')

    if not message_text:
        await callback.answer("No message found")
        return

    # Get all user IDs
    users = await users_collection.find({}, {"user_id": 1}).to_list(None)
    user_ids = [user['user_id'] for user in users]

    await callback.message.edit_text("📢 Sending broadcast...")

    success = 0
    failed = 0

    for user_id in user_ids:
        try:
            await bot.send_message(user_id, message_text)
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.1)  # Rate limiting

    await callback.message.answer(
        f"📢 Broadcast completed!\n"
        f"✅ Success: {success}\n"
        f"❌ Failed: {failed}"
    )
    await state.clear()
    await admin_command(callback.message,state)

@dp.callback_query(F.data == "broadcast_cancel", AdminStates.CONFIRM_BROADCAST)
async def cancel_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Broadcast cancelled")
    await admin_command(callback.message,state)

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
        client = active_clients[i % len(active_clients)]
        await ensure_client_in_channel(client,order['channel_id'])
        tasks.append(process_view_order(client, order['channel_id'], order['content_id']))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    return sum(1 for r in results if r is True)

async def process_manual_reactions(order, to_deliver):
    tasks = []
    emoji = order['emoji']
    for i in range(to_deliver):
        client = active_clients[i % len(active_clients)]
        await ensure_client_in_channel(client,order['channel_id'])
        tasks.append(process_reaction_order(client, order['channel_id'], order['content_id'], emoji))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return sum(1 for r in results if r is True)

async def process_poll_votes(order, to_deliver):
    tasks = []
    for i in range(to_deliver):
        client = active_clients[i % len(active_clients)]
        await ensure_client_in_channel(client,order['channel_id'])
        tasks.append(process_vote_order(client, order['channel_id'], order['content_id'], order['option_index']))

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

                # Calculate how many to deliver in this batch
                to_deliver = min(remaining, len(active_clients))

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
                    success_count = await process_poll_votes(order, to_deliver)
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
    if not active_clients:
        print("⚠️ No active clients for monitoring")
        return

    print(f"👑 Setting up monitoring with {len(active_clients)} clients...")

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
                    len(active_clients),
                    max(0, order['posts_per_day'] * order['days'] - order.get('total_posts_processed', 0))
                )
                if desired_quantity < 1:
                    continue

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

        except Exception as e:
            print(f"❌ Error in monitor: {e}")
            import traceback
            traceback.print_exc()

    # Register monitor on all clients
    for i, client in enumerate(active_clients):
        try:
            client.add_event_handler(monitor_channel_telethon, events.NewMessage)
            print(f"✅ Monitoring enabled on client {i}")
        except Exception as e:
            print(f"⚠️ Failed to set monitor on client {i}: {e}")


# Distributed View Delivery - All clients work together
async def process_auto_views_master_worker(order, message_id, quantity):
    try:
        channel_id = order['channel_id']
        speed_multiplier = order.get('speed_multiplier', 1.0)
        speed_name = order.get('speed_name', 'Normal')

        print(f"📊 Distributing {quantity} views across {len(active_clients)} clients at {speed_name} speed")

        tasks = []
        for i in range(quantity):
            client = active_clients[i % len(active_clients)]

            # Ensure client is in channel
            if not await ensure_client_in_channel(client, channel_id):
                print(f"⚠️ Client {i % len(active_clients)} couldn't join channel")
                continue

            # Apply speed delay using speed config function
            delay = calculate_delay_for_speed(speed_multiplier, base_delay=1.0)

            tasks.append(delayed_view_order(client, channel_id, message_id, delay * i))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for r in results if r is True)
        print(f"✅ Delivered {success_count}/{quantity} views successfully at {speed_name} speed")
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
        speed_multiplier = order.get('speed_multiplier', 1.0)
        speed_name = order.get('speed_name', 'Normal')

        print(f"❤️ Distributing {quantity} reactions across {len(active_clients)} clients at {speed_name} speed")

        tasks = []
        for i in range(quantity):
            client = active_clients[i % len(active_clients)]

            # Ensure client is in channel
            if not await ensure_client_in_channel(client, channel_id):
                print(f"⚠️ Client {i % len(active_clients)} couldn't join channel")
                continue

            # Assign emoji for this client-post combination
            session_str = client.session.save()
            key = (channel_id, message_id, session_str)
            if key not in client_reactions:
                client_reactions[key] = random.choice(["❤️", "🔥", "👍", "👏", "🎉", "🤩", "😍"])
            emoji = client_reactions[key]

            # Apply speed delay using speed config function
            delay = calculate_delay_for_speed(speed_multiplier, base_delay=1.0)

            tasks.append(delayed_reaction_order(client, channel_id, message_id, emoji, delay * i))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for r in results if r is True)
        print(f"✅ Delivered {success_count}/{quantity} reactions successfully at {speed_name} speed")
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


async def process_reaction_order(client, channel_id, message_id, emoji=None):
    """Send reaction with retry and error handling using consistent emoji"""
    retries = 2

    # Process emoji selection BEFORE the retry loop
    if emoji == "❤️ Positive":
        reaction_list = ["❤️", "🔥", "👍", "👏", "🎉", "🤩", "😍"]
        emoji = random.choice(reaction_list)
    elif emoji == "😂 Negative":
        reaction_list = ["👎", "🤬", "🤮", "💩", "🤡", "🥱", "🌭", "🤣", "🍌", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈", "💅", "🤪", "👾", "🤷‍♂", "🤷", "🤷‍♀", "😡"]
        emoji = random.choice(reaction_list)
    elif not emoji or emoji == "🤗 Custom":
        # Use assigned emoji or default
        session_str = client.session.save()
        key = (channel_id, message_id, session_str)
        emoji = client_reactions.get(key, random.choice(["❤️", "🔥", "👍", "👏", "🎉", "🤩", "😍"]))

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
                await client(ImportChatInviteRequest(invite_code))
            except UserAlreadyParticipantError:
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

        # Try to get entity by invite link
        try:
            entity = await client.get_entity(invite_link)
        except ValueError as ve:
            # If direct link doesn't work, try by username (for public channels)
            if "joinchat" not in invite_link.lower() and "+" not in invite_link:
                try:
                    entity = await client.get_entity(invite_code)
                except Exception as e:
                    result["error"] = f"Could not find channel. Please verify the link is correct and try again."
                    return result
            else:
                result["error"] = "Could not resolve channel from invite link. The link may be invalid or expired."
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
                chat_invite = await client(CheckChatInviteRequest(invite_code))
                if hasattr(chat_invite, 'chat'):
                    entity = chat_invite.chat
                elif hasattr(chat_invite, 'channel'):
                    entity = chat_invite.channel
                else:
                    entity = None

                if entity and isinstance(entity, (Channel, ChannelForbidden)):
                    result.update({
                        "success": True,
                        "channel_id": entity.id,
                        "channel_title": getattr(entity, "title", "Unknown Channel"),
                        "username": getattr(entity, "username", None),
                        "is_public": bool(getattr(entity, "username", None)),
                    })
                    return result
                elif isinstance(chat_invite, ChatInviteAlready):
                    channel = chat_invite.chat
                    result.update({
                        "success": True,
                        "channel_id": channel.id,
                        "channel_title": getattr(channel, "title", "Unknown Channel"),
                        "username": getattr(channel, "username", None),
                        "is_public": bool(getattr(channel, "username", None)),
                    })
                    return result
                else:
                    result.update({
                        "success": True,
                        "channel_id": getattr(entity, 'id', None) if entity else None,
                        "channel_title": getattr(entity, "title", "Private Channel") if entity else "Private Channel",
                        "username": None,
                        "is_public": False,
                    })
                    if result["channel_id"]:
                        return result
                    result["error"] = "Could not resolve private channel info."
                    result["success"] = False
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
    if not active_clients:
        return

    print(f"🔗 Joining {len(active_clients)} clients to channel {channel_id} after payment confirmation...")

    async def join_single_client(i, client):
        try:
            already_in = False
            try:
                entity = await client.get_entity(PeerChannel(channel_id))
                await client.get_permissions(entity)
                already_in = True
            except (ValueError, errors.ChannelPrivateError):
                pass

            if already_in:
                print(f"  Client {i}: Already in channel")
                await mute_channel_for_client(client, channel_id)
                return True

            invite_code = invite_link.strip().split("/")[-1]
            is_private = False
            if "joinchat" in invite_link.lower():
                invite_code = invite_code.replace("joinchat", "").strip()
                is_private = True
            elif "+" in invite_link and "t.me/+" in invite_link:
                invite_code = invite_code.replace("+", "")
                is_private = True

            if is_private:
                try:
                    await client(ImportChatInviteRequest(invite_code))
                except UserAlreadyParticipantError:
                    pass
            else:
                try:
                    await client(JoinChannelRequest(invite_code))
                except UserAlreadyParticipantError:
                    pass

            print(f"  Client {i}: Joined channel successfully")
            await mute_channel_for_client(client, channel_id)
            return True

        except FloodWaitError as e:
            print(f"  Client {i}: Flood wait {e.seconds}s")
            await asyncio.sleep(min(e.seconds, 30))
            return False
        except Exception as e:
            print(f"  Client {i}: Failed to join - {e}")
            return False

    tasks = [join_single_client(i, c) for i, c in enumerate(active_clients)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    joined = sum(1 for r in results if r is True)
    print(f"✅ {joined}/{len(active_clients)} clients joined channel {channel_id}")


async def leave_channel_from_all_clients(channel_id):
    if not active_clients:
        return

    print(f"🚪 Leaving channel {channel_id} for all clients except client[0]...")

    for i, client in enumerate(active_clients):
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
    print("\n🌐 Flask webhook server running...")
    if public_url:
        print(f"Endpoint: {public_url}/verify_payment\n")
    else:
        print("Endpoint: http://localhost:5000/verify_payment (webhook disabled)\n")
    flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


async def main():
    global public_url

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
            public_url = ngrok.connect(5000, "http").public_url
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

        print("👥 Loading Telegram client sessions...")
        # Try loading from session files first (more stable)
        await load_all_clients_from_files()
        
        # If no clients loaded, try database method as fallback
        if not active_clients:
            print("\n💡 No session files found, trying database method...")
            await load_all_clients()

        if active_clients:
            print(f"✅ Loaded {len(active_clients)} Telegram clients")
            print("📡 Setting up monitoring system...")
            await ub_moniter()  # All clients monitor
            print("✅ All clients are now monitoring channels")
        else:
            print("⚠️ No active Telegram clients found.")
            print("📁 Place .session files in /app/backend/sessions/ directory")
            print("   OR add accounts via /admin -> Telegram Accounts")

        print("⚙️ Starting background tasks...")
        asyncio.create_task(task_process_manual_orders())
        asyncio.create_task(task_reset_daily_posts())
        asyncio.create_task(expire_due_orders())

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

    # Disconnect all clients
    for i, client in enumerate(active_clients):
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