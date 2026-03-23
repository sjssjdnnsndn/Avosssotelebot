import os
import re
import asyncio
import random
import string
import logging
import json
import time
import aiohttp
from io import BytesIO
from datetime import datetime
from typing import Optional

import qrcode
from motor.motor_asyncio import AsyncIOMotorClient
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
    FloodWaitError,
)
from telethon.network import ConnectionTcpAbridged
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

logger = logging.getLogger(__name__)

# ==================== CONSTANTS ====================
ACCT_USD_TO_INR_RATE = 83.0
ACCT_POINTS_PER_DOLLAR = 10
ACCT_REDEMPTION_RATE = 100
ACCT_MIN_DEPOSIT_USD = 0.1
ACCT_MIN_DEPOSIT_INR = 1.0
ACCT_DEFAULT_WELCOME_BONUS = 1.0
ACCT_DEFAULT_REFERRAL_COMMISSION_RATE = 10.0
ACCT_PAYMENT_AMOUNT_TOLERANCE = 0.01
ACCT_UPI_VERIFICATION_DELAY = 10
ACCT_UPI_VERIFICATION_TIMEOUT = 600

ACCT_DEFAULT_VIP_TIERS = {
    "bronze": {"min_points": 0, "discount": 5},
    "silver": {"min_points": 1000, "discount": 10},
    "gold": {"min_points": 5000, "discount": 15},
    "platinum": {"min_points": 15000, "discount": 20},
}

ACCT_UPI_TRACKING_FILE = "transaction/acct_upi_tracking.json"
ACCT_OXAPAY_TRACKING_FILE = "transaction/acct_oxapay_tracking.json"
ACCT_OXAPAY_BASE_URL = "https://api.oxapay.com/merchants/request"
ACCT_OXAPAY_INQUIRY_URL = "https://api.oxapay.com/merchants/inquiry"

# ==================== MODULE-LEVEL SHARED STATE ====================
_bot = None
_admin_id = None
_api_id = None
_api_hash = None
_acct_db = None
_acct_oxapay_key = None
_acct_ngrok_url = None

ACCT_SUPPORT_CHAT = {}
ACCT_OTP_WATCHERS = {}
ACCT_STATES = {}
ACCT_DATA = {}


def setup_accounts_shop(bot, admin_id, api_id, api_hash, mongo_url, oxapay_key=None, ngrok_url=None):
    global _bot, _admin_id, _api_id, _api_hash, _acct_db, _acct_oxapay_key, _acct_ngrok_url
    _bot = bot
    _admin_id = admin_id
    _api_id = api_id
    _api_hash = api_hash
    _acct_oxapay_key = oxapay_key or "EALKPY-X9V3MF-AYHLXY-TVWXXX"
    _acct_ngrok_url = ngrok_url
    mongo_client = AsyncIOMotorClient(mongo_url)
    _acct_db = mongo_client.acct_bot_db


# ==================== STATE HELPERS ====================
def acct_clear_state(user_id: int):
    ACCT_STATES.pop(user_id, None)
    ACCT_DATA.pop(user_id, None)


def acct_set_state(user_id: int, state: str):
    ACCT_STATES[user_id] = state
    if user_id not in ACCT_DATA:
        ACCT_DATA[user_id] = {}


def acct_get_state(user_id: int):
    return ACCT_STATES.get(user_id)


def acct_update_data(user_id: int, **kwargs):
    if user_id not in ACCT_DATA:
        ACCT_DATA[user_id] = {}
    ACCT_DATA[user_id].update(kwargs)


def acct_get_data(user_id: int):
    return ACCT_DATA.get(user_id, {})


# ==================== SECURITY HELPERS ====================
def acct_verify_payment_amount(expected: float, actual: float, tolerance: float = ACCT_PAYMENT_AMOUNT_TOLERANCE):
    if actual <= 0:
        return False, 0
    diff = abs(expected - actual)
    allowed = expected * tolerance
    return diff <= allowed, actual


def acct_extract_oxapay_amount(data: dict) -> Optional[float]:
    try:
        if "payAmount" in data:
            return float(data["payAmount"])
        if "amount" in data:
            return float(data["amount"])
    except Exception:
        pass
    return None


def acct_extract_paytm_amount(data: dict) -> Optional[float]:
    try:
        if "TXNAMOUNT" in data:
            return float(data["TXNAMOUNT"])
        if "amount" in data:
            return float(data["amount"])
    except Exception:
        pass
    return None


# ==================== FORMAT HELPER ====================
def acct_fmt(usd: float) -> str:
    inr = usd * ACCT_USD_TO_INR_RATE
    return f"${usd:.2f} USD (₹{inr:.2f} INR)"


# ==================== VIP / LOYALTY HELPERS ====================
async def acct_get_vip_tiers() -> dict:
    cfg = await _acct_db.config.find_one({"key": "vip_tiers"})
    if cfg and cfg.get("value"):
        return cfg["value"]
    return ACCT_DEFAULT_VIP_TIERS


async def acct_get_user_tier(user_id: int) -> dict:
    user = await _acct_db.users.find_one({"id": user_id})
    if not user:
        return {"tier": "bronze", "discount": 5}
    points = user.get("loyalty_points", 0)
    tiers = await acct_get_vip_tiers()
    current = {"tier": "bronze", "discount": 5}
    for name, data in tiers.items():
        if points >= data["min_points"] and data["discount"] >= current["discount"]:
            current = {"tier": name, "discount": data["discount"]}
    return current


async def acct_award_loyalty_points(user_id: int, amount_usd: float) -> int:
    points = int(amount_usd * ACCT_POINTS_PER_DOLLAR)
    await _acct_db.users.update_one({"id": user_id}, {"$inc": {"loyalty_points": points}})
    await _acct_db.transactions.insert_one({
        "user_id": user_id, "type": "loyalty_points_earned",
        "points": points, "amount_usd": amount_usd, "timestamp": datetime.now()
    })
    return points


async def acct_redeem_loyalty_points(user_id: int, points: int) -> dict:
    user = await _acct_db.users.find_one({"id": user_id})
    if not user:
        return {"success": False, "error": "User not found"}
    available = user.get("loyalty_points", 0)
    if points > available:
        return {"success": False, "error": "Insufficient points"}
    if points < ACCT_REDEMPTION_RATE:
        return {"success": False, "error": f"Minimum {ACCT_REDEMPTION_RATE} points required"}
    usd = points / ACCT_REDEMPTION_RATE
    await _acct_db.users.update_one(
        {"id": user_id},
        {"$inc": {"loyalty_points": -points, "balance": usd}}
    )
    await _acct_db.transactions.insert_one({
        "user_id": user_id, "type": "loyalty_points_redeemed",
        "points": points, "usd_value": usd, "timestamp": datetime.now()
    })
    return {"success": True, "points": points, "usd_value": usd}


# ==================== REFERRAL HELPERS ====================
def acct_generate_referral_code() -> str:
    return "REF" + "".join(random.choices(string.ascii_uppercase + string.digits, k=7))


async def acct_ensure_referral_code(user_id: int) -> str:
    user = await _acct_db.users.find_one({"id": user_id})
    if user and user.get("referral_code"):
        return user["referral_code"]
    while True:
        code = acct_generate_referral_code()
        if not await _acct_db.users.find_one({"referral_code": code}):
            break
    await _acct_db.users.update_one(
        {"id": user_id},
        {"$set": {"referral_code": code,
                  "referral_stats": {"total_referred": 0, "completed_purchases": 0, "commission_earned": 0.0}}},
        upsert=True
    )
    return code


async def acct_get_welcome_bonus() -> float:
    cfg = await _acct_db.config.find_one({"key": "welcome_bonus"})
    if cfg and cfg.get("value"):
        return float(cfg["value"])
    return ACCT_DEFAULT_WELCOME_BONUS


async def acct_get_referral_commission_rate() -> float:
    cfg = await _acct_db.config.find_one({"key": "referral_commission_rate"})
    if cfg and cfg.get("value"):
        return float(cfg["value"])
    return ACCT_DEFAULT_REFERRAL_COMMISSION_RATE


async def acct_award_welcome_bonus(user_id: int) -> float:
    bonus = await acct_get_welcome_bonus()
    await _acct_db.users.update_one({"id": user_id}, {"$inc": {"balance": bonus}})
    await _acct_db.transactions.insert_one({
        "user_id": user_id, "type": "welcome_bonus",
        "amount": bonus, "timestamp": datetime.now(), "status": "completed"
    })
    return bonus


async def acct_award_referral_commission(referrer_id: int, referee_id: int, purchase_amount: float):
    rate = await acct_get_referral_commission_rate()
    commission = purchase_amount * (rate / 100)
    await _acct_db.users.update_one(
        {"id": referrer_id},
        {"$inc": {"balance": commission,
                  "referral_stats.commission_earned": commission,
                  "referral_stats.completed_purchases": 1}}
    )
    await _acct_db.transactions.insert_one({
        "user_id": referrer_id, "type": "referral_commission",
        "amount": commission, "referee_id": referee_id,
        "purchase_amount": purchase_amount, "commission_rate": rate,
        "timestamp": datetime.now(), "status": "completed"
    })
    try:
        referrer = await _acct_db.users.find_one({"id": referrer_id})
        new_bal = referrer.get("balance", 0) if referrer else 0
        await _bot.send_message(
            referrer_id,
            f"🎉 <b>Referral Commission Earned!</b>\n\n"
            f"💰 Commission ({rate}%): <code>${commission:.2f} USD</code>\n"
            f"📊 From: User ID {referee_id}\n"
            f"💵 Your New Balance: <b>{acct_fmt(new_bal)}</b>\n\n"
            f"Keep sharing your referral link! 🚀",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error notifying referrer: {e}")


async def acct_get_referral_stats(user_id: int) -> dict:
    user = await _acct_db.users.find_one({"id": user_id})
    if not user:
        return {"total_referred": 0, "completed_purchases": 0, "commission_earned": 0.0}
    return user.get("referral_stats", {"total_referred": 0, "completed_purchases": 0, "commission_earned": 0.0})


# ==================== UPI HELPERS ====================
def acct_load_upi_tracking():
    try:
        if os.path.exists(ACCT_UPI_TRACKING_FILE):
            with open(ACCT_UPI_TRACKING_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"pending": {}, "completed": {}}


def acct_save_upi_tracking(data):
    try:
        os.makedirs("transaction", exist_ok=True)
        with open(ACCT_UPI_TRACKING_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


def acct_generate_upi_qr(upi_id: str, amount_inr: float, order_id: str, name: str = "TelegramBot") -> BytesIO:
    upi_link = f"upi://pay?pa={upi_id}&pn={name}&am={amount_inr:.2f}&cu=INR&tn={order_id}&tr={order_id}"
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(upi_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


async def acct_fetch_paytm_data(order_id, mid):
    url = f"https://paytm-api.lightdns.me/?mid={mid}&oid={order_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = json.loads(await resp.text())
                if data.get("STATUS") == "TXN_SUCCESS":
                    return True, data
    return False, None


async def acct_verify_upi_payment_auto(order_id: str, mid: str, user_id: int, exp_usd: float, exp_inr: float):
    start = time.time()
    while (time.time() - start) < ACCT_UPI_VERIFICATION_TIMEOUT:
        try:
            success, data = await acct_fetch_paytm_data(order_id, mid)
            if success and data:
                actual_inr = acct_extract_paytm_amount(data)
                if actual_inr is None:
                    await asyncio.sleep(ACCT_UPI_VERIFICATION_DELAY)
                    continue
                is_valid, verified_inr = acct_verify_payment_amount(exp_inr, actual_inr)
                if not is_valid:
                    try:
                        await _bot.send_message(
                            _admin_id,
                            f"🚨 <b>UPI FRAUD DETECTED!</b>\nUser: {user_id}\nOrder: {order_id}\n"
                            f"Expected: ₹{exp_inr:.2f}\nActual: ₹{actual_inr:.2f}",
                            parse_mode="HTML"
                        )
                        await _bot.send_message(
                            user_id,
                            f"❌ <b>Payment Verification Failed</b>\n\nAmount mismatch. Contact support.",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                    return False
                actual_usd = verified_inr / ACCT_USD_TO_INR_RATE
                user = await _acct_db.users.find_one({"id": user_id})
                if user:
                    new_bal = user.get("balance", 0) + actual_usd
                    await _acct_db.users.update_one({"id": user_id}, {"$set": {"balance": new_bal}})
                    points = await acct_award_loyalty_points(user_id, actual_usd)
                    tier = await acct_get_user_tier(user_id)
                    if user.get("referred_by"):
                        await acct_award_referral_commission(user["referred_by"], user_id, actual_usd)
                    await _acct_db.transactions.insert_one({
                        "user_id": user_id, "type": "upi_deposit", "amount": actual_usd,
                        "amount_inr": verified_inr, "order_id": order_id,
                        "txn_id": data.get("TXNID"), "timestamp": datetime.now(), "status": "completed"
                    })
                    try:
                        await _bot.send_message(
                            user_id,
                            f"✅ <b>UPI Payment Confirmed!</b>\n\n"
                            f"💰 Amount: ₹{verified_inr:.2f} INR (${actual_usd:.2f} USD)\n"
                            f"💵 New Balance: {acct_fmt(new_bal)}\n"
                            f"💎 Loyalty Points Earned: {points}\n"
                            f"🏆 VIP Tier: {tier['tier'].upper()} ({tier['discount']}% discount)\n\n"
                            f"Thank you! 🎉",
                            parse_mode="HTML"
                        )
                        await _bot.send_message(
                            _admin_id,
                            f"✅ <b>Acct Shop UPI Payment</b>\nUser: {user_id}\n"
                            f"Amount: ₹{verified_inr:.2f} INR (${actual_usd:.2f} USD)\n"
                            f"Order: {order_id}",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                return True
        except Exception as e:
            logger.error(f"UPI check error: {e}")
        await asyncio.sleep(ACCT_UPI_VERIFICATION_DELAY)
    try:
        await _bot.send_message(
            user_id,
            f"❌ <b>Payment Timeout</b>\nOrder: {order_id}\nContact support if you paid.",
            parse_mode="HTML"
        )
    except Exception:
        pass
    return False


# ==================== OXAPAY HELPERS ====================
def acct_load_oxapay_tracking():
    try:
        if os.path.exists(ACCT_OXAPAY_TRACKING_FILE):
            with open(ACCT_OXAPAY_TRACKING_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"pending": {}, "completed": {}}


def acct_save_oxapay_tracking(data):
    try:
        os.makedirs("transaction", exist_ok=True)
        with open(ACCT_OXAPAY_TRACKING_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


async def acct_create_oxapay_payment(user_id: int, amount: float) -> dict:
    try:
        order_id = f"acct_oxapay_{user_id}_{int(datetime.now().timestamp())}"
        payment_data = {
            "merchant": _acct_oxapay_key,
            "amount": amount,
            "currency": "USD",
            "orderId": order_id,
            "description": f"Acct Shop Deposit ${amount} USD"
        }
        if _acct_ngrok_url:
            payment_data["callbackUrl"] = f"{_acct_ngrok_url}/acct_oxapay_webhook"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ACCT_OXAPAY_BASE_URL, json=payment_data,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                result = await resp.json()
                if resp.status == 200 and result.get("result") == 100:
                    tracking = acct_load_oxapay_tracking()
                    tracking["pending"][order_id] = {
                        "user_id": user_id, "amount": amount,
                        "trackId": result.get("trackId"),
                        "payLink": result.get("payLink"),
                        "created": datetime.now().isoformat(), "status": "pending"
                    }
                    acct_save_oxapay_tracking(tracking)
                    return {"success": True, "order_id": order_id,
                            "payment_url": result.get("payLink"), "amount": amount}
                return {"success": False, "error": result.get("message", "Failed")}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def acct_check_oxapay_status(order_id: str) -> dict:
    try:
        tracking = acct_load_oxapay_tracking()
        if order_id in tracking["completed"]:
            return {"success": True, "status": "Paid", "payment": tracking["completed"][order_id]}
        if order_id not in tracking["pending"]:
            return {"success": False, "error": "Order not found"}
        payment = tracking["pending"][order_id]
        track_id = payment.get("trackId")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ACCT_OXAPAY_INQUIRY_URL,
                json={"merchant": _acct_oxapay_key, "trackId": track_id},
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                result = await resp.json()
                if resp.status == 200:
                    status = result.get("status", "Waiting")
                    if status == "Paid":
                        user_id = payment["user_id"]
                        exp_amount = payment["amount"]
                        actual_usd = acct_extract_oxapay_amount(result)
                        if actual_usd is None:
                            return {"success": False, "error": "Cannot verify amount"}
                        is_valid, verified = acct_verify_payment_amount(exp_amount, actual_usd)
                        if not is_valid:
                            return {"success": False, "error": "Amount mismatch"}
                        user = await _acct_db.users.find_one({"id": user_id})
                        if user:
                            new_bal = user.get("balance", 0) + verified
                            await _acct_db.users.update_one({"id": user_id}, {"$set": {"balance": new_bal}})
                            points = await acct_award_loyalty_points(user_id, verified)
                            if user.get("referred_by"):
                                await acct_award_referral_commission(user["referred_by"], user_id, verified)
                        await _acct_db.transactions.insert_one({
                            "user_id": user_id, "type": "oxapay_deposit", "amount": verified,
                            "order_id": order_id, "timestamp": datetime.now(), "status": "completed"
                        })
                        tracking["completed"][order_id] = payment
                        tracking["completed"][order_id]["status"] = "completed"
                        del tracking["pending"][order_id]
                        acct_save_oxapay_tracking(tracking)
                        try:
                            await _bot.send_message(
                                user_id,
                                f"✅ <b>Crypto Payment Confirmed!</b>\n\n"
                                f"💰 Amount: ${verified:.2f} USD\n"
                                f"💵 New Balance: {acct_fmt(new_bal)}\n"
                                f"💎 Points Earned: {points}\n\nThank you! 🎉",
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass
                    return {"success": True, "status": status}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== TELETHON CLIENT ====================
async def acct_create_telethon_client(session_string=None, max_retries=5):
    for attempt in range(max_retries):
        try:
            sess = StringSession(session_string) if session_string else StringSession()
            client = TelegramClient(
                sess, _api_id, _api_hash,
                connection=ConnectionTcpAbridged,
                connection_retries=5, retry_delay=1,
                timeout=30, request_retries=5,
                auto_reconnect=True, sequential_updates=True
            )
            await asyncio.wait_for(client.connect(), timeout=30)
            if client.is_connected():
                return client
            await client.disconnect()
            raise ConnectionError("Not connected")
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            if attempt < max_retries - 1:
                await asyncio.sleep((attempt + 1) * 2)
            else:
                raise
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep((attempt + 1) * 2)
            else:
                raise


# ==================== KEYBOARDS ====================
def acct_buy_section_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="💳 Buy Telegram Accounts"), KeyboardButton(text="📊 Acct Stats")],
        [KeyboardButton(text="💱 Acct Deposit"), KeyboardButton(text="💵 Acct Balance")],
        [KeyboardButton(text="🎁 Acct Referrals"), KeyboardButton(text="💎 Acct Loyalty")],
        [KeyboardButton(text="🛟 Acct Support"), KeyboardButton(text="📚 Acct How To Use")],
        [KeyboardButton(text="⬅️ Back to Main Menu")],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def acct_admin_panel_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="➕ Add Account", callback_data="acct_admin_add_account"),
         InlineKeyboardButton(text="💳 Add Balance", callback_data="acct_admin_add_balance")],
        [InlineKeyboardButton(text="📊 Stats", callback_data="acct_admin_view_stats"),
         InlineKeyboardButton(text="🧑 User Details", callback_data="acct_admin_user_details")],
        [InlineKeyboardButton(text="📂 All Accounts", callback_data="acct_admin_all_accounts"),
         InlineKeyboardButton(text="💳 Sold Accounts", callback_data="acct_admin_sold_accounts")],
        [InlineKeyboardButton(text="📊 Sales Report", callback_data="acct_admin_sales_report"),
         InlineKeyboardButton(text="📢 Broadcast", callback_data="acct_admin_broadcast")],
        [InlineKeyboardButton(text="📝 Set Welcome", callback_data="acct_admin_set_welcome"),
         InlineKeyboardButton(text="🎹 User Manual Video", callback_data="acct_admin_user_manual")],
        [InlineKeyboardButton(text="🇮🇳 Set UPI ID", callback_data="acct_admin_set_upi"),
         InlineKeyboardButton(text="🔑 Set MID", callback_data="acct_admin_set_mid")],
        [InlineKeyboardButton(text="🎁 Set Welcome Bonus", callback_data="acct_admin_set_welcome_bonus"),
         InlineKeyboardButton(text="📈 Set Commission %", callback_data="acct_admin_set_commission_rate")],
        [InlineKeyboardButton(text="💎 Configure VIP Tiers", callback_data="acct_admin_vip_tiers")],
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="acct_admin_refresh"),
         InlineKeyboardButton(text="❌ Close", callback_data="acct_admin_close")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ==================== USER HANDLERS ====================
async def acct_show_main_menu(message: types.Message):
    user_id = message.from_user.id
    acct_clear_state(user_id)
    user = await _acct_db.users.find_one({"id": user_id})
    if not user:
        await _acct_db.users.insert_one({
            "id": user_id, "balance": 0.0, "purchases": 0,
            "loyalty_points": 0, "is_first_purchase": True,
            "referred_by": None,
            "referral_stats": {"total_referred": 0, "completed_purchases": 0, "commission_earned": 0.0}
        })
        await acct_ensure_referral_code(user_id)
    else:
        await acct_ensure_referral_code(user_id)
    welcome = await _acct_db.welcome.find_one()
    txt = "🌎 <b>Welcome to the Telegram Account Shop!</b>\n\n💡 Buy virtual accounts • Pay in crypto/UPI • Professional & Secure\n💎 Earn Loyalty Points on every purchase!\n\n👇 Select an option:"
    if welcome and welcome.get("photo_file_id") and welcome.get("description"):
        try:
            await _bot.send_photo(
                chat_id=user_id,
                photo=welcome["photo_file_id"],
                caption=welcome["description"],
                reply_markup=acct_buy_section_keyboard(),
                parse_mode="HTML"
            )
            return
        except Exception:
            txt = welcome.get("description", txt)
    await message.answer(txt, reply_markup=acct_buy_section_keyboard(), parse_mode="HTML")


async def acct_show_accounts(message: types.Message):
    acct_clear_state(message.from_user.id)
    accounts = await _acct_db.accounts.find({"available": True}).to_list(1000)
    if not accounts:
        await message.answer("❌ No accounts available at the moment.\n\nPlease check back later!", parse_mode="HTML")
        return
    countries = {}
    for acc in accounts:
        c = acc.get("country", "Unknown")
        countries.setdefault(c, []).append(acc)
    msg = "💳 <b>Available Telegram Accounts</b>\n\n"
    keyboard = []
    for country, accs in countries.items():
        msg += f"🌎 <b>{country}</b>: {len(accs)} account(s)\n"
        for acc in accs[:5]:
            p = acc.get("price_usd", 0)
            keyboard.append([InlineKeyboardButton(
                text=f"📱 {country} - ${p:.2f} (₹{p * ACCT_USD_TO_INR_RATE:.0f})",
                callback_data=f"acct_buy_{acc['id']}"
            )])
    keyboard.append([InlineKeyboardButton(text="🔄 Refresh", callback_data="acct_refresh_accounts")])
    await message.answer(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")


async def acct_show_stats(message: types.Message):
    acct_clear_state(message.from_user.id)
    user_id = message.from_user.id
    user = await _acct_db.users.find_one({"id": user_id})
    if not user:
        await message.answer("❌ User not found. Please use /start first.")
        return
    balance = user.get("balance", 0)
    purchases = user.get("purchases", 0)
    points = user.get("loyalty_points", 0)
    tier = await acct_get_user_tier(user_id)
    ref = user.get("referral_stats", {})
    tier_emoji = {"bronze": "🥉", "silver": "🥈", "gold": "🥇", "platinum": "💎"}.get(tier["tier"], "🎖️")
    await message.answer(
        f"📊 <b>YOUR STATISTICS</b>\n\n"
        f"💵 Balance: {acct_fmt(balance)}\n"
        f"🛒 Total Purchases: {purchases}\n"
        f"💎 Loyalty Points: {points}\n"
        f"🏆 VIP Tier: {tier_emoji} {tier['tier'].upper()} ({tier['discount']}% discount)\n\n"
        f"🎁 <b>Referral Stats:</b>\n"
        f"👥 Total Referred: {ref.get('total_referred', 0)}\n"
        f"✅ Completed Purchases: {ref.get('completed_purchases', 0)}\n"
        f"💰 Commission Earned: {acct_fmt(ref.get('commission_earned', 0))}",
        parse_mode="HTML"
    )


async def acct_show_balance(message: types.Message):
    acct_clear_state(message.from_user.id)
    user_id = message.from_user.id
    user = await _acct_db.users.find_one({"id": user_id})
    if not user:
        await message.answer("❌ User not found. Please use /start first.")
        return
    balance = user.get("balance", 0)
    points = user.get("loyalty_points", 0)
    tier = await acct_get_user_tier(user_id)
    await message.answer(
        f"💵 <b>YOUR BALANCE</b>\n\n"
        f"Balance: <b>{acct_fmt(balance)}</b>\n"
        f"💎 Loyalty Points: <b>{points}</b>\n"
        f"🏆 VIP Tier: <b>{tier['tier'].upper()}</b> ({tier['discount']}% discount)\n\n"
        f"Use 💱 Acct Deposit to add funds!",
        parse_mode="HTML"
    )


async def acct_deposit_menu(message: types.Message):
    acct_clear_state(message.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 OxaPay (Crypto)", callback_data="acct_deposit_oxapay")],
        [InlineKeyboardButton(text="🇮🇳 UPI (India)", callback_data="acct_deposit_upi")],
    ])
    await message.answer(
        "💱 <b>Deposit Funds</b>\n\nSelect your payment method:",
        reply_markup=keyboard, parse_mode="HTML"
    )


async def acct_show_loyalty(message: types.Message):
    acct_clear_state(message.from_user.id)
    user_id = message.from_user.id
    user = await _acct_db.users.find_one({"id": user_id})
    if not user:
        await message.answer("❌ User not found.")
        return
    points = user.get("loyalty_points", 0)
    tier = await acct_get_user_tier(user_id)
    tiers = await acct_get_vip_tiers()
    redeemable = points / ACCT_REDEMPTION_RATE
    tier_emoji = {"bronze": "🥉", "silver": "🥈", "gold": "🥇", "platinum": "💎"}
    next_tier = None
    for name, data in sorted(tiers.items(), key=lambda x: x[1]["min_points"]):
        if points < data["min_points"]:
            next_tier = {"name": name, **data}
            break
    msg = (
        f"💎 <b>LOYALTY POINTS DASHBOARD</b>\n\n"
        f"<b>Your Points:</b> {points}\n"
        f"💵 <b>Redeemable Balance:</b> ${redeemable:.2f} USD\n\n"
        f"🏆 <b>Current Tier:</b> {tier_emoji.get(tier['tier'], '🎖️')} {tier['tier'].upper()}\n"
        f"🎯 <b>Discount:</b> {tier['discount']}%\n\n"
    )
    if next_tier:
        msg += f"📈 <b>Next Tier:</b> {tier_emoji.get(next_tier['name'], '🎖️')} {next_tier['name'].upper()} ({next_tier['min_points'] - points} pts needed)\n\n"
    else:
        msg += "🌟 <b>You're at the highest tier!</b>\n\n"
    msg += (
        f"<b>💎 VIP TIERS:</b>\n"
        f"🥉 Bronze: 0+ pts ({tiers['bronze']['discount']}% off)\n"
        f"🥈 Silver: {tiers['silver']['min_points']}+ pts ({tiers['silver']['discount']}% off)\n"
        f"🥇 Gold: {tiers['gold']['min_points']}+ pts ({tiers['gold']['discount']}% off)\n"
        f"💎 Platinum: {tiers['platinum']['min_points']}+ pts ({tiers['platinum']['discount']}% off)\n\n"
        f"• Earn {ACCT_POINTS_PER_DOLLAR} points per $1 spent\n"
        f"• Redeem {ACCT_REDEMPTION_RATE} points = $1 USD"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Redeem Points", callback_data="acct_loyalty_redeem")],
        [InlineKeyboardButton(text="📜 Points History", callback_data="acct_loyalty_history")],
    ])
    await message.answer(msg, reply_markup=keyboard, parse_mode="HTML")


async def acct_show_referrals(message: types.Message):
    acct_clear_state(message.from_user.id)
    user_id = message.from_user.id
    code = await acct_ensure_referral_code(user_id)
    stats = await acct_get_referral_stats(user_id)
    bot_info = await _bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=acct_{code}"
    commission_rate = await acct_get_referral_commission_rate()
    welcome_bonus = await acct_get_welcome_bonus()
    share_text = f"🎁 Join this Telegram Account Shop and get ${welcome_bonus:.2f} welcome bonus!"
    share_url = f"https://t.me/share/url?url={ref_link}&text={share_text}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Share Referral Link", url=share_url)],
        [InlineKeyboardButton(text="📋 Copy Link", callback_data=f"acct_copy_ref_{code}")],
    ])
    await message.answer(
        f"🎁 <b>YOUR REFERRAL DASHBOARD</b>\n\n"
        f"🔗 Code: <code>{code}</code>\n"
        f"🔗 Link: <code>{ref_link}</code>\n\n"
        f"📊 <b>STATISTICS</b>\n"
        f"👥 Total Referred: {stats['total_referred']}\n"
        f"✅ Completed Purchases: {stats['completed_purchases']}\n"
        f"💰 Commission Earned: {acct_fmt(stats['commission_earned'])}\n\n"
        f"🎯 <b>HOW IT WORKS:</b>\n"
        f"• They get ${welcome_bonus:.2f} USD welcome bonus\n"
        f"• You earn {commission_rate}% commission on ALL their purchases\n"
        f"• Commission credited instantly!",
        reply_markup=keyboard, parse_mode="HTML"
    )


async def acct_show_support(message: types.Message):
    user_id = message.from_user.id
    acct_clear_state(user_id)
    if ACCT_SUPPORT_CHAT.get(user_id):
        await message.answer("ℹ️ You are already in a support session.")
        return
    ACCT_SUPPORT_CHAT[user_id] = True
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ End Support", callback_data="acct_end_support")]
    ])
    await message.answer(
        "💬 <b>You are now connected to admin support.\nType your question or send images.</b>",
        reply_markup=keyboard, parse_mode="HTML"
    )
    try:
        await _bot.send_message(
            _admin_id,
            f"[Acct Shop] User {user_id} started support. Use /acctreply {user_id} <message> to reply."
        )
    except Exception:
        pass


async def acct_show_user_manual(message: types.Message):
    acct_clear_state(message.from_user.id)
    manual = await _acct_db.user_manual.find_one()
    if manual and manual.get("video_file_id"):
        try:
            await _bot.send_video(
                chat_id=message.chat.id,
                video=manual["video_file_id"],
                caption="📚 <b>Acct Shop - How To Use</b>",
                parse_mode="HTML"
            )
            return
        except Exception:
            pass
    await message.answer(
        "📚 <b>How To Use</b>\n\n"
        "1. Deposit funds using UPI or Crypto\n"
        "2. Browse available accounts\n"
        "3. Purchase account using OTP or Session File\n"
        "4. Earn loyalty points on every purchase\n"
        "5. Refer friends and earn commission!",
        parse_mode="HTML"
    )


# ==================== CALLBACK HANDLERS ====================
async def acct_handle_callback(callback: types.CallbackQuery):
    data = callback.data
    user_id = callback.from_user.id
    await callback.answer()

    # ---- Account browse ----
    if data == "acct_refresh_accounts":
        accounts = await _acct_db.accounts.find({"available": True}).to_list(1000)
        if not accounts:
            await callback.message.answer("❌ No accounts available.")
            return
        countries = {}
        for acc in accounts:
            c = acc.get("country", "Unknown")
            countries.setdefault(c, []).append(acc)
        msg = "💳 <b>Available Telegram Accounts</b>\n\n"
        keyboard = []
        for country, accs in countries.items():
            msg += f"🌎 <b>{country}</b>: {len(accs)} account(s)\n"
            for acc in accs[:5]:
                p = acc.get("price_usd", 0)
                keyboard.append([InlineKeyboardButton(
                    text=f"📱 {country} - ${p:.2f} (₹{p * ACCT_USD_TO_INR_RATE:.0f})",
                    callback_data=f"acct_buy_{acc['id']}"
                )])
        keyboard.append([InlineKeyboardButton(text="🔄 Refresh", callback_data="acct_refresh_accounts")])
        try:
            await callback.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")
        except Exception:
            await callback.message.answer(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")
        return

    # ---- Buy account ----
    if data.startswith("acct_buy_"):
        account_id = int(data.replace("acct_buy_", ""))
        user = await _acct_db.users.find_one({"id": user_id})
        account = await _acct_db.accounts.find_one({"id": account_id, "available": True})
        if not account:
            await callback.message.answer("❌ Account no longer available.")
            return
        if not user:
            await callback.message.answer("❌ User not found. Please use /start first.")
            return
        tier = await acct_get_user_tier(user_id)
        discount = tier["discount"]
        price = account.get("price_usd", 0)
        final_price = price * (1 - discount / 100)
        balance = user.get("balance", 0)
        if balance < final_price:
            await callback.message.answer(
                f"❌ <b>Insufficient Balance</b>\n\n"
                f"Price: ${price:.2f} USD\n"
                f"🏆 Your discount ({tier['tier'].upper()}): {discount}%\n"
                f"💰 Final price: <b>${final_price:.2f} USD</b>\n"
                f"Your balance: ${balance:.2f} USD\n"
                f"Need: ${final_price - balance:.2f} more\n\n"
                f"Please deposit funds first!",
                parse_mode="HTML"
            )
            return
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔐 OTP Login System", callback_data=f"acct_otp_login_{account_id}")],
            [InlineKeyboardButton(text="📄 Session File System", callback_data=f"acct_session_file_{account_id}")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="acct_cancel_purchase")],
        ])
        await callback.message.answer(
            f"🎯 <b>Select Login Method</b>\n\n"
            f"📱 Phone: <code>{account['phone']}</code>\n"
            f"🌎 Country: {account['country']}\n"
            f"💰 Price: ${price:.2f} USD\n"
            f"🏆 Your discount: -{discount}%\n"
            f"💵 Final price: <b>${final_price:.2f} USD (₹{final_price * ACCT_USD_TO_INR_RATE:.2f} INR)</b>\n\n"
            f"🔐 <b>OTP Login:</b> I'll forward OTPs automatically\n"
            f"📄 <b>Session File:</b> Get session file directly",
            reply_markup=keyboard, parse_mode="HTML"
        )
        return

    # ---- OTP Login ----
    if data.startswith("acct_otp_login_"):
        account_id = int(data.split("_")[3])
        user = await _acct_db.users.find_one({"id": user_id})
        account = await _acct_db.accounts.find_one({"id": account_id, "available": True})
        if not (user and account):
            await callback.message.answer("❌ Account not available.")
            return
        tier = await acct_get_user_tier(user_id)
        price = account.get("price_usd", 0)
        final_price = price * (1 - tier["discount"] / 100)
        savings = price - final_price
        if user["balance"] < final_price:
            await callback.message.answer("⚠️ <b>Insufficient balance!</b>", parse_mode="HTML")
            return
        try:
            tc = await acct_create_telethon_client(account["session"])

            async def otp_handler(event):
                try:
                    full_msg = event.raw_text or str(event.message)
                    otp_match = re.search(r'\b(\d{5})\b', full_msg)
                    code = otp_match.group(1) if otp_match else (max(re.findall(r'\d+', full_msg), key=len) if re.findall(r'\d+', full_msg) else full_msg)
                    await _bot.send_message(
                        user_id,
                        f"🔐 <b>OTP for login:</b> <code>{code}</code>\n\nUse this when logging in.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"OTP forward error: {e}")

            tc.add_event_handler(otp_handler, events.NewMessage(from_users=777000))
            ACCT_OTP_WATCHERS[user_id] = (tc, otp_handler)
            kb = [[InlineKeyboardButton(text="✅ Login Done", callback_data=f"acct_login_done_{account_id}")]]
            if account.get("twofa_pass"):
                kb.append([InlineKeyboardButton(text="🔐 Need 2FA Password?", callback_data=f"acct_login_2fa_{account_id}")])
            await callback.message.answer(
                f"🚀 <b>Login to <code>{account['phone']}</code> in Telegram. I'll forward OTPs instantly.</b>\n\n"
                f"📱 Phone: <code>{account['phone']}</code>\n\n"
                f"Tap <b>✅ Login Done</b> when finished.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML"
            )
            acct_set_state(user_id, "waiting_for_login_done")
            acct_update_data(user_id, acc_id=account_id, price_usd=price, discounted_price=final_price,
                             discount_percent=tier["discount"], savings=savings, twofa_pass=account.get("twofa_pass"))
        except Exception as e:
            await callback.message.answer(f"❌ <b>Failed to connect to account:</b> {str(e)}", parse_mode="HTML")
        return

    # ---- 2FA Password ----
    if data.startswith("acct_login_2fa_"):
        d = acct_get_data(user_id)
        twofa = d.get("twofa_pass")
        if twofa:
            await callback.message.answer(f"🔑 <b>2FA password:</b>\n\n<code>{twofa}</code>", parse_mode="HTML")
        else:
            await callback.answer("No 2FA set for this account.", show_alert=True)
        return

    # ---- Login Done ----
    if data.startswith("acct_login_done_"):
        account_id = int(data.split("_")[3])
        d = acct_get_data(user_id)
        user = await _acct_db.users.find_one({"id": user_id})
        account = await _acct_db.accounts.find_one({"id": account_id})
        if not (user and account and account.get("available")):
            await callback.message.answer("❌ Account unavailable. Contact admin!")
            acct_clear_state(user_id)
            return
        final_price = d.get("discounted_price", 0)
        price = d.get("price_usd", 0)
        savings = d.get("savings", 0)
        discount = d.get("discount_percent", 0)
        new_bal = user["balance"] - final_price
        await _acct_db.users.update_one(
            {"id": user_id},
            {"$set": {"balance": new_bal, "purchases": user.get("purchases", 0) + 1}}
        )
        await _acct_db.accounts.update_one(
            {"id": account_id},
            {"$set": {"available": False, "sold_to": user_id, "sold_at": datetime.now()}}
        )
        points = await acct_award_loyalty_points(user_id, final_price)
        if user.get("referred_by"):
            await acct_award_referral_commission(user["referred_by"], user_id, final_price)
        await _acct_db.transactions.insert_one({
            "user_id": user_id, "type": "account_purchase", "account_id": account_id,
            "amount": final_price, "original_price": price, "discount": savings, "timestamp": datetime.now()
        })
        tc_info = ACCT_OTP_WATCHERS.pop(user_id, (None, None))
        if tc_info[0]:
            try:
                tc_info[0].remove_event_handler(tc_info[1])
            except Exception:
                pass
            try:
                await tc_info[0].log_out()
            except Exception:
                pass
            try:
                await tc_info[0].disconnect()
            except Exception:
                pass
        tier = await acct_get_user_tier(user_id)
        tier_emoji = {"bronze": "🥉", "silver": "🥈", "gold": "🥇", "platinum": "💎"}.get(tier["tier"], "🎖️")
        stars_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⭐ 1", callback_data=f"acct_rate_1_{account_id}"),
            InlineKeyboardButton(text="⭐⭐⭐ 3", callback_data=f"acct_rate_3_{account_id}"),
            InlineKeyboardButton(text="⭐⭐⭐⭐⭐ 5", callback_data=f"acct_rate_5_{account_id}"),
        ]])
        await callback.message.answer(
            f"🎉 <b>Purchase Complete!</b>\n\n"
            f"📱 {account['country']} ({account['phone']})\n"
            f"💰 Price: ${price:.2f} | Discount: -{discount}%\n"
            f"💵 Paid: <b>${final_price:.2f} USD</b>\n"
            f"💎 Loyalty Points: <b>{points}</b>\n"
            f"💵 New Balance: {acct_fmt(new_bal)}\n"
            f"🏆 Tier: {tier_emoji} {tier['tier'].upper()}\n\n"
            f"✅ Please update credentials & terminate other sessions!\n\n"
            f"💬 Please rate our service:",
            reply_markup=stars_kb, parse_mode="HTML"
        )
        acct_clear_state(user_id)
        return

    # ---- Session File ----
    if data.startswith("acct_session_file_"):
        account_id = int(data.split("_")[3])
        user = await _acct_db.users.find_one({"id": user_id})
        account = await _acct_db.accounts.find_one({"id": account_id, "available": True})
        if not (user and account):
            await callback.message.answer("❌ Account not available.")
            return
        tier = await acct_get_user_tier(user_id)
        price = account.get("price_usd", 0)
        final_price = price * (1 - tier["discount"] / 100)
        savings = price - final_price
        if user["balance"] < final_price:
            await callback.message.answer("⚠️ <b>Insufficient balance!</b>", parse_mode="HTML")
            return
        new_bal = user["balance"] - final_price
        await _acct_db.users.update_one(
            {"id": user_id},
            {"$set": {"balance": new_bal}, "$inc": {"purchases": 1}}
        )
        await _acct_db.accounts.update_one(
            {"id": account_id},
            {"$set": {"available": False, "sold_to": user_id, "sold_at": datetime.now()}}
        )
        points = await acct_award_loyalty_points(user_id, final_price)
        if user.get("referred_by"):
            await acct_award_referral_commission(user["referred_by"], user_id, final_price)
        await _acct_db.transactions.insert_one({
            "user_id": user_id, "type": "account_purchase", "account_id": account_id,
            "amount": final_price, "original_price": price, "discount": savings, "timestamp": datetime.now()
        })
        msg = (
            f"✅ <b>Purchase Successful!</b>\n\n"
            f"📱 Phone: <code>{account['phone']}</code>\n"
            f"🌎 Country: {account['country']}\n"
            f"💰 Price: ${price:.2f} | Discount: -{tier['discount']}%\n"
            f"💵 Paid: <b>${final_price:.2f} USD</b>\n"
            f"💎 Loyalty Points Earned: <b>{points}</b>\n"
            f"💵 New Balance: {acct_fmt(new_bal)}\n\n"
        )
        if account.get("twofa_pass"):
            msg += f"🔐 2FA Password: <code>{account['twofa_pass']}</code>\n\n"
        msg += "📄 Session file below:"
        await callback.message.answer(msg, parse_mode="HTML")
        try:
            session_data = account.get("session", "")
            session_file = BytesIO(session_data.encode())
            session_file.name = f"account_{account_id}.session"
            await _bot.send_document(
                chat_id=user_id, document=session_file,
                caption="📄 Your session file"
            )
        except Exception as e:
            await callback.message.answer("❌ Error sending session file. Contact admin.")
        stars_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⭐ 1", callback_data=f"acct_rate_1_{account_id}"),
            InlineKeyboardButton(text="⭐⭐⭐ 3", callback_data=f"acct_rate_3_{account_id}"),
            InlineKeyboardButton(text="⭐⭐⭐⭐⭐ 5", callback_data=f"acct_rate_5_{account_id}"),
        ]])
        await callback.message.answer("💬 <b>Please rate our service:</b>", reply_markup=stars_kb, parse_mode="HTML")
        return

    # ---- Rating ----
    if data.startswith("acct_rate_"):
        parts = data.split("_")
        stars = int(parts[2])
        account_id = int(parts[3])
        await _acct_db.ratings.insert_one({
            "id": random.randint(1, 1000000), "account_id": account_id,
            "buyer_id": user_id, "stars": stars, "timestamp": datetime.now()
        })
        await callback.message.answer("⭐ Thank you for rating us! Please share our bot with friends 😁")
        return

    # ---- Cancel purchase ----
    if data == "acct_cancel_purchase":
        acct_clear_state(user_id)
        await callback.message.answer("❌ Purchase cancelled.")
        return

    # ---- Deposit callbacks ----
    if data == "acct_deposit_upi":
        upi_cfg = await _acct_db.config.find_one({"key": "upi_id"})
        if not upi_cfg or not upi_cfg.get("value"):
            await callback.message.answer("❌ UPI not configured. Contact admin.")
            return
        await callback.message.answer(
            f"🇮🇳 <b>UPI Deposit</b>\n\nMinimum: ₹{ACCT_MIN_DEPOSIT_INR:.0f} INR\n\nEnter the amount in INR:",
            parse_mode="HTML"
        )
        acct_set_state(user_id, "acct_upi_waiting_amount")
        return

    if data == "acct_deposit_oxapay":
        await callback.message.answer(
            f"💳 <b>OxaPay Crypto Deposit</b>\n\nMinimum: ${ACCT_MIN_DEPOSIT_USD:.2f} USD\n\nEnter amount in USD:",
            parse_mode="HTML"
        )
        acct_set_state(user_id, "acct_oxapay_waiting_amount")
        return

    if data.startswith("acct_oxapay_check_"):
        order_id = data.replace("acct_oxapay_check_", "")
        await callback.answer("Checking...", show_alert=False)
        result = await acct_check_oxapay_status(order_id)
        if result.get("success") and result.get("status") == "Paid":
            await callback.message.answer("✅ <b>Payment Confirmed!</b> Balance updated.", parse_mode="HTML")
        else:
            status = result.get("status", "Waiting")
            await callback.message.answer(f"⏳ <b>Status:</b> {status}\n\nComplete payment then check again.", parse_mode="HTML")
        return

    # ---- Loyalty ----
    if data == "acct_loyalty_redeem":
        user = await _acct_db.users.find_one({"id": user_id})
        if not user:
            await callback.message.answer("❌ User not found.")
            return
        points = user.get("loyalty_points", 0)
        if points < ACCT_REDEMPTION_RATE:
            await callback.message.answer(
                f"❌ Insufficient Points\n\nYou have: {points}\nMinimum: {ACCT_REDEMPTION_RATE}",
                parse_mode="HTML"
            )
            return
        max_pts = (points // ACCT_REDEMPTION_RATE) * ACCT_REDEMPTION_RATE
        await callback.message.answer(
            f"💎 <b>Redeem Loyalty Points</b>\n\nYour points: {points}\nMax redeemable: {max_pts} pts (${max_pts / ACCT_REDEMPTION_RATE:.2f})\n\nEnter points to redeem (multiple of {ACCT_REDEMPTION_RATE}):",
            parse_mode="HTML"
        )
        acct_set_state(user_id, "acct_redeem_points")
        return

    if data == "acct_loyalty_history":
        txns = await _acct_db.transactions.find(
            {"user_id": user_id, "type": {"$in": ["loyalty_points_earned", "loyalty_points_redeemed"]}}
        ).sort("timestamp", -1).limit(10).to_list(10)
        if not txns:
            await callback.message.answer("📜 No points history yet.")
            return
        msg = "📜 <b>Recent Loyalty Points Transactions</b>\n\n"
        for t in txns:
            date = t["timestamp"].strftime("%Y-%m-%d %H:%M")
            if t["type"] == "loyalty_points_earned":
                msg += f"✅ Earned {t['points']} points | ${t['amount_usd']:.2f} | {date}\n"
            else:
                msg += f"🔄 Redeemed {t['points']} pts | ${t['usd_value']:.2f} | {date}\n"
        await callback.message.answer(msg, parse_mode="HTML")
        return

    # ---- Referral copy ----
    if data.startswith("acct_copy_ref_"):
        await callback.answer("✅ Copy the link shown above to share!", show_alert=True)
        return

    # ---- Support end ----
    if data == "acct_end_support":
        ACCT_SUPPORT_CHAT.pop(user_id, None)
        await callback.message.answer("✅ Support session ended.")
        return

    # ---- Admin panel ----
    if data == "acct_admin_refresh":
        await callback.message.edit_reply_markup(reply_markup=acct_admin_panel_keyboard())
        await callback.answer("✅ Refreshed")
        return

    if data == "acct_admin_close":
        await callback.message.delete()
        return

    if data == "acct_admin_add_account":
        await callback.message.answer("📱 Send phone number with country code (e.g. +1234567890):")
        acct_set_state(user_id, "acct_add_account_phone")
        return

    if data == "acct_admin_add_balance":
        await callback.message.answer("Enter the user ID to add balance to:")
        acct_set_state(user_id, "acct_add_balance_user")
        return

    if data == "acct_admin_view_stats":
        total_users = await _acct_db.users.count_documents({})
        total_acc = await _acct_db.accounts.count_documents({})
        avail = await _acct_db.accounts.count_documents({"available": True})
        sold = await _acct_db.accounts.count_documents({"available": False})
        await callback.message.answer(
            f"📊 <b>Acct Shop Statistics</b>\n\n"
            f"👥 Total Users: {total_users}\n"
            f"💳 Total Accounts: {total_acc}\n"
            f"✅ Available: {avail}\n"
            f"💰 Sold: {sold}",
            parse_mode="HTML"
        )
        return

    if data == "acct_admin_user_details":
        await callback.message.answer("Enter the user ID to view:")
        acct_set_state(user_id, "acct_view_user_details")
        return

    if data == "acct_admin_all_accounts":
        accounts = await _acct_db.accounts.find().limit(20).to_list(20)
        msg = "📂 <b>All Accounts (First 20)</b>\n\n"
        for acc in accounts:
            status = "✅" if acc.get("available") else "❌ Sold"
            msg += f"ID: {acc['id']} | {acc['country']} | ${acc['price_usd']:.2f} | {status}\n"
        await callback.message.answer(msg or "No accounts.", parse_mode="HTML")
        return

    if data == "acct_admin_sold_accounts":
        sold = await _acct_db.accounts.find({"available": False}).limit(20).to_list(20)
        msg = "💳 <b>Sold Accounts (First 20)</b>\n\n"
        for acc in sold:
            msg += f"ID: {acc['id']} | {acc['country']} | Sold to: {acc.get('sold_to', 'N/A')}\n"
        await callback.message.answer(msg or "No sold accounts.", parse_mode="HTML")
        return

    if data == "acct_admin_sales_report":
        total = await _acct_db.transactions.count_documents({"type": "account_purchase"})
        result = await _acct_db.transactions.aggregate([
            {"$match": {"type": "account_purchase"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]).to_list(1)
        revenue = result[0]["total"] if result else 0
        await callback.message.answer(
            f"📊 <b>Sales Report</b>\n\nTotal Sales: {total}\nRevenue: ${revenue:.2f} USD (₹{revenue * ACCT_USD_TO_INR_RATE:.2f} INR)",
            parse_mode="HTML"
        )
        return

    if data == "acct_admin_broadcast":
        await callback.message.answer("📢 Enter broadcast message:")
        acct_set_state(user_id, "acct_broadcast_message")
        return

    if data == "acct_admin_set_welcome":
        await callback.message.answer("📝 Send a photo for the welcome message (then add caption):")
        acct_set_state(user_id, "acct_welcome_photo")
        return

    if data == "acct_admin_user_manual":
        await callback.message.answer("🎹 Send a video for the user manual:")
        acct_set_state(user_id, "acct_user_manual_video")
        return

    if data == "acct_admin_set_upi":
        await callback.message.answer("🇮🇳 Enter the UPI ID (e.g., username@paytm):")
        acct_set_state(user_id, "acct_set_upi_id")
        return

    if data == "acct_admin_set_mid":
        await callback.message.answer("🔑 Enter the MID (Merchant ID):")
        acct_set_state(user_id, "acct_set_mid")
        return

    if data == "acct_admin_set_welcome_bonus":
        cur = await acct_get_welcome_bonus()
        await callback.message.answer(
            f"🎁 Current bonus: ${cur:.2f} USD\n\nEnter new bonus amount in USD:", parse_mode="HTML"
        )
        acct_set_state(user_id, "acct_set_welcome_bonus")
        return

    if data == "acct_admin_set_commission_rate":
        cur = await acct_get_referral_commission_rate()
        await callback.message.answer(
            f"📈 Current rate: {cur}%\n\nEnter new commission rate (0-100):", parse_mode="HTML"
        )
        acct_set_state(user_id, "acct_set_commission_rate")
        return

    if data == "acct_admin_vip_tiers":
        tiers = await acct_get_vip_tiers()
        msg = (
            f"💎 <b>VIP Tier Configuration</b>\n\n"
            f"🥉 Bronze: {tiers['bronze']['min_points']}+ pts ({tiers['bronze']['discount']}% off)\n"
            f"🥈 Silver: {tiers['silver']['min_points']}+ pts ({tiers['silver']['discount']}% off)\n"
            f"🥇 Gold: {tiers['gold']['min_points']}+ pts ({tiers['gold']['discount']}% off)\n"
            f"💎 Platinum: {tiers['platinum']['min_points']}+ pts ({tiers['platinum']['discount']}% off)\n\n"
            f"Select a tier to configure:"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🥉 Bronze", callback_data="acct_config_tier_bronze"),
             InlineKeyboardButton(text="🥈 Silver", callback_data="acct_config_tier_silver")],
            [InlineKeyboardButton(text="🥇 Gold", callback_data="acct_config_tier_gold"),
             InlineKeyboardButton(text="💎 Platinum", callback_data="acct_config_tier_platinum")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="acct_admin_refresh")],
        ])
        try:
            await callback.message.edit_text(msg, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer(msg, reply_markup=kb, parse_mode="HTML")
        return

    if data.startswith("acct_config_tier_"):
        tier_name = data.replace("acct_config_tier_", "")
        tiers = await acct_get_vip_tiers()
        t = tiers.get(tier_name, {})
        await callback.message.answer(
            f"💎 <b>Configure {tier_name.upper()} Tier</b>\n\n"
            f"Min Points: {t.get('min_points', 0)}\nDiscount: {t.get('discount', 0)}%\n\n"
            f"Enter: <code>min_points,discount</code> (e.g., <code>1000,10</code>)",
            parse_mode="HTML"
        )
        acct_set_state(user_id, f"acct_config_tier_{tier_name}")
        return

    if data.startswith("acct_currency_"):
        currency = data.split("_")[2].upper()
        acct_update_data(user_id, add_bal_currency=currency)
        await callback.message.answer(f"Selected {currency}. Enter the amount:")
        acct_set_state(user_id, "acct_add_balance_amount")
        return


# ==================== STATE MESSAGE HANDLER ====================
async def acct_handle_state_messages(message: types.Message) -> bool:
    user_id = message.from_user.id
    state = acct_get_state(user_id)
    if not state:
        return False

    text = message.text or ""

    # ---- Commission rate ----
    if state == "acct_set_commission_rate":
        try:
            rate = float(text.strip())
            if not (0 <= rate <= 100):
                await message.answer("❌ Rate must be 0-100. Try again:")
                return True
            await _acct_db.config.update_one({"key": "referral_commission_rate"}, {"$set": {"value": rate}}, upsert=True)
            await message.answer(f"✅ Commission rate set to {rate}%", parse_mode="HTML")
            acct_clear_state(user_id)
        except ValueError:
            await message.answer("❌ Invalid number. Enter e.g. 10 or 15.5:")
        return True

    # ---- VIP tier config ----
    if state.startswith("acct_config_tier_"):
        tier_name = state.replace("acct_config_tier_", "")
        try:
            parts = text.strip().split(",")
            if len(parts) != 2:
                raise ValueError()
            min_pts = int(parts[0].strip())
            disc = int(parts[1].strip())
            if min_pts < 0 or not (0 <= disc <= 100):
                raise ValueError()
            tiers = await acct_get_vip_tiers()
            tiers[tier_name] = {"min_points": min_pts, "discount": disc}
            await _acct_db.config.update_one({"key": "vip_tiers"}, {"$set": {"value": tiers}}, upsert=True)
            await message.answer(f"✅ {tier_name.upper()} Tier: {min_pts}+ pts, {disc}% discount")
            acct_clear_state(user_id)
        except Exception:
            await message.answer("❌ Format: <code>min_points,discount</code> e.g. <code>1000,10</code>", parse_mode="HTML")
        return True

    # ---- Add account flow ----
    if state == "acct_add_account_phone":
        phone = text.strip()
        if not (phone.startswith("+") and phone[1:].replace(" ", "").replace("-", "").isdigit()):
            await message.answer("❌ Invalid phone. Must start with + and country code.")
            return True
        acct_update_data(user_id, phone=phone)
        try:
            await message.answer("📱 Connecting to Telegram...")
            tc = await acct_create_telethon_client()
            sent = await asyncio.wait_for(tc.send_code_request(phone), timeout=30)
            acct_update_data(user_id, telethon_str=tc.session.save(), phone_code_hash=sent.phone_code_hash)
            await tc.disconnect()
            await message.answer("✅ OTP sent! Enter the code:")
            acct_set_state(user_id, "acct_add_account_otp")
        except PhoneNumberInvalidError:
            await message.answer("❌ Invalid phone number.")
            acct_clear_state(user_id)
        except FloodWaitError as e:
            await message.answer(f"❌ Rate limited. Try again in {e.seconds}s.")
            acct_clear_state(user_id)
        except Exception as e:
            await message.answer(f"❌ Error: {str(e)}")
            acct_clear_state(user_id)
        return True

    if state == "acct_add_account_otp":
        code = text.strip()
        d = acct_get_data(user_id)
        try:
            tc = await acct_create_telethon_client(d["telethon_str"])
            await tc.sign_in(d["phone"], code, phone_code_hash=d.get("phone_code_hash"))
            session_str = tc.session.save()
            await tc.disconnect()
            acct_update_data(user_id, session_str=session_str)
            await message.answer("🌎 Enter country for this account (e.g. India, USA):")
            acct_set_state(user_id, "acct_add_account_country")
        except SessionPasswordNeededError:
            tc2 = await acct_create_telethon_client(d["telethon_str"])
            acct_update_data(user_id, telethon_str=tc2.session.save())
            await tc2.disconnect()
            await message.answer("🔐 2FA enabled! Enter 2FA password:")
            acct_set_state(user_id, "acct_add_account_2fa")
        except Exception as e:
            await message.answer(f"❌ Sign-in error: {e}")
            acct_clear_state(user_id)
        return True

    if state == "acct_add_account_2fa":
        password = text.strip()
        d = acct_get_data(user_id)
        try:
            tc = await acct_create_telethon_client(d.get("telethon_str"))
            await tc.sign_in(password=password)
            session_str = tc.session.save()
            await tc.disconnect()
            acct_update_data(user_id, session_str=session_str, twofa_pass=password)
            await message.answer("🌎 Enter country for this account:")
            acct_set_state(user_id, "acct_add_account_country")
        except Exception as e:
            await message.answer(f"❌ 2FA error: {e}")
            acct_clear_state(user_id)
        return True

    if state == "acct_add_account_country":
        acct_update_data(user_id, country=text.strip())
        await message.answer("If this account has a 2FA password, send it (or type 'none'):")
        acct_set_state(user_id, "acct_add_account_2fa_pass")
        return True

    if state == "acct_add_account_2fa_pass":
        acct_update_data(user_id, twofa_pass=(None if text.strip().lower() == "none" else text.strip()))
        await message.answer("💲 Set price in USD (e.g. 0.5 or 1):")
        acct_set_state(user_id, "acct_add_account_price")
        return True

    if state == "acct_add_account_price":
        try:
            price = float(text.strip())
            if price <= 0:
                raise ValueError()
        except Exception:
            await message.answer("❌ Invalid price. Enter a positive number:")
            return True
        d = acct_get_data(user_id)
        acc_id = random.randint(1, 10000000)
        await _acct_db.accounts.insert_one({
            "id": acc_id, "phone": d["phone"], "country": d["country"],
            "price_usd": price, "available": True,
            "session": d.get("session_str") or d.get("telethon_str"),
            "twofa_pass": d.get("twofa_pass")
        })
        await message.answer(f"✅ Account {d['country']} ({d['phone']}) added at ${price:.2f}!")
        acct_clear_state(user_id)
        return True

    # ---- Add balance flow ----
    if state == "acct_add_balance_user":
        try:
            target_id = int(text.strip())
        except Exception:
            await message.answer("❌ Invalid user ID.")
            return True
        exists = await _acct_db.users.find_one({"id": target_id})
        if not exists:
            await message.answer("❌ User not found.")
            acct_clear_state(user_id)
            return True
        acct_update_data(user_id, add_bal_user=target_id)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💵 USD", callback_data="acct_currency_usd"),
            InlineKeyboardButton(text="₹ INR", callback_data="acct_currency_inr"),
        ]])
        await message.answer("💱 Select currency:", reply_markup=kb)
        acct_set_state(user_id, "acct_add_balance_currency")
        return True

    if state == "acct_add_balance_currency":
        return True

    if state == "acct_add_balance_amount":
        try:
            amount = float(text.strip())
            if amount <= 0:
                raise ValueError()
        except Exception:
            await message.answer("❌ Invalid amount.")
            return True
        d = acct_get_data(user_id)
        target_id = d.get("add_bal_user")
        currency = d.get("add_bal_currency", "USD")
        usd_amount = amount / ACCT_USD_TO_INR_RATE if currency == "INR" else amount
        display = f"₹{amount:.2f} INR" if currency == "INR" else f"${amount:.2f} USD"
        u = await _acct_db.users.find_one({"id": target_id})
        if not u:
            await _acct_db.users.insert_one({"id": target_id, "balance": usd_amount, "purchases": 0, "loyalty_points": 0})
            new_bal = usd_amount
        else:
            new_bal = u["balance"] + usd_amount
            await _acct_db.users.update_one({"id": target_id}, {"$set": {"balance": new_bal}})
        await _acct_db.transactions.insert_one({
            "user_id": target_id, "type": "manual_deposit",
            "amount": usd_amount, "display": display,
            "added_by": user_id, "timestamp": datetime.now()
        })
        await message.answer(f"✅ Added {display} to user {target_id}. New balance: {acct_fmt(new_bal)}", parse_mode="HTML")
        try:
            await _bot.send_message(target_id, f"✅ <b>Balance Added!</b>\n\n{display} has been added to your account.\nNew balance: {acct_fmt(new_bal)}", parse_mode="HTML")
        except Exception:
            pass
        acct_clear_state(user_id)
        return True

    # ---- UPI deposit amount ----
    if state == "acct_upi_waiting_amount":
        try:
            amount_inr = float(text.strip())
            if amount_inr < ACCT_MIN_DEPOSIT_INR:
                await message.answer(f"❌ Minimum deposit: ₹{ACCT_MIN_DEPOSIT_INR:.2f}\nEnter a higher amount:")
                return True
        except ValueError:
            await message.answer("❌ Invalid amount. Enter a number (e.g., 100):")
            return True
        amount_usd = amount_inr / ACCT_USD_TO_INR_RATE
        upi_cfg = await _acct_db.config.find_one({"key": "upi_id"})
        mid_cfg = await _acct_db.config.find_one({"key": "mid"})
        if not upi_cfg or not upi_cfg.get("value"):
            await message.answer("❌ UPI not configured. Contact admin.")
            acct_clear_state(user_id)
            return True
        if not mid_cfg or not mid_cfg.get("value"):
            await message.answer("❌ MID not configured. Contact admin.")
            acct_clear_state(user_id)
            return True
        upi_id = upi_cfg["value"]
        mid = mid_cfg["value"]
        order_id = f"ACCT{user_id}{int(datetime.now().timestamp())}"
        await message.answer("⏳ Generating UPI QR code...")
        try:
            qr_image = acct_generate_upi_qr(upi_id, amount_inr, order_id)
            tracking = acct_load_upi_tracking()
            tracking["pending"][order_id] = {
                "user_id": user_id, "amount_usd": amount_usd,
                "amount_inr": amount_inr, "upi_id": upi_id,
                "mid": mid, "created": datetime.now().isoformat(), "status": "pending"
            }
            acct_save_upi_tracking(tracking)
            await _bot.send_photo(
                chat_id=user_id, photo=qr_image,
                caption=f"🇮🇳 <b>UPI Payment</b>\n\n"
                        f"💰 Amount: ₹{amount_inr:.2f} INR (${amount_usd:.2f} USD)\n"
                        f"📋 Order ID: <code>{order_id}</code>\n"
                        f"💳 UPI ID: <code>{upi_id}</code>\n\n"
                        f"📱 Scan QR or pay via any UPI app\n"
                        f"⏳ Auto-verifying payment...",
                parse_mode="HTML"
            )
            acct_clear_state(user_id)
            asyncio.create_task(acct_verify_upi_payment_auto(order_id, mid, user_id, amount_usd, amount_inr))
        except Exception as e:
            await message.answer(f"❌ Error generating QR: {str(e)}")
            acct_clear_state(user_id)
        return True

    # ---- OxaPay deposit amount ----
    if state == "acct_oxapay_waiting_amount":
        try:
            amount = float(text.strip())
            if amount < ACCT_MIN_DEPOSIT_USD:
                await message.answer(f"❌ Minimum: ${ACCT_MIN_DEPOSIT_USD:.2f} USD")
                return True
        except ValueError:
            await message.answer("❌ Invalid amount. Enter a number (e.g., 1 or 5):")
            return True
        await message.answer("⏳ Creating payment link...")
        result = await acct_create_oxapay_payment(user_id, amount)
        if result["success"]:
            order_id = result["order_id"]
            payment_url = result["payment_url"]
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Pay Now", url=payment_url)],
                [InlineKeyboardButton(text="🔄 Check Payment", callback_data=f"acct_oxapay_check_{order_id}")],
            ])
            await message.answer(
                f"✅ <b>Payment Link Created!</b>\n\n"
                f"💰 Amount: ${amount:.2f} USD (₹{amount * ACCT_USD_TO_INR_RATE:.2f} INR)\n"
                f"📋 Order ID: <code>{order_id}</code>\n\n"
                f"Click 'Pay Now' then 'Check Payment' after paying.",
                reply_markup=kb, parse_mode="HTML"
            )
        else:
            await message.answer(f"❌ Failed: {result.get('error', 'Unknown')}\nTry again or contact admin.")
        acct_clear_state(user_id)
        return True

    # ---- Set UPI ID ----
    if state == "acct_set_upi_id":
        upi_id = text.strip()
        if "@" not in upi_id:
            await message.answer("❌ Invalid UPI ID. Must contain @")
            return True
        await _acct_db.config.update_one({"key": "upi_id"}, {"$set": {"value": upi_id}}, upsert=True)
        await message.answer(f"✅ UPI ID set to: <code>{upi_id}</code>", parse_mode="HTML")
        acct_clear_state(user_id)
        return True

    # ---- Set MID ----
    if state == "acct_set_mid":
        mid = text.strip()
        await _acct_db.config.update_one({"key": "mid"}, {"$set": {"value": mid}}, upsert=True)
        await message.answer(f"✅ MID set to: <code>{mid}</code>", parse_mode="HTML")
        acct_clear_state(user_id)
        return True

    # ---- Set welcome bonus ----
    if state == "acct_set_welcome_bonus":
        try:
            bonus = float(text.strip())
            if bonus < 0:
                await message.answer("❌ Must be positive.")
                return True
            await _acct_db.config.update_one({"key": "welcome_bonus"}, {"$set": {"value": bonus}}, upsert=True)
            await message.answer(f"✅ Welcome bonus set to ${bonus:.2f} USD (₹{bonus * ACCT_USD_TO_INR_RATE:.2f} INR)", parse_mode="HTML")
            acct_clear_state(user_id)
        except ValueError:
            await message.answer("❌ Invalid amount.")
        return True

    # ---- Redeem points ----
    if state == "acct_redeem_points":
        try:
            points = int(text.strip())
            if points % ACCT_REDEMPTION_RATE != 0:
                await message.answer(f"❌ Must be a multiple of {ACCT_REDEMPTION_RATE}. Try again:")
                return True
            result = await acct_redeem_loyalty_points(user_id, points)
            if result["success"]:
                user = await _acct_db.users.find_one({"id": user_id})
                nb = user.get("balance", 0) if user else 0
                await message.answer(
                    f"✅ <b>Points Redeemed!</b>\n\n"
                    f"🔄 Points: {points}\n"
                    f"💵 Value: ${result['usd_value']:.2f} USD\n"
                    f"💵 New Balance: {acct_fmt(nb)}\n"
                    f"💎 Remaining Points: {user.get('loyalty_points', 0) if user else 0}",
                    parse_mode="HTML"
                )
            else:
                await message.answer(f"❌ {result.get('error', 'Failed')}")
            acct_clear_state(user_id)
        except ValueError:
            await message.answer(f"❌ Invalid number. Enter e.g. {ACCT_REDEMPTION_RATE}:")
        return True

    # ---- View user details ----
    if state == "acct_view_user_details":
        try:
            target_id = int(text.strip())
        except Exception:
            await message.answer("❌ Invalid user ID.")
            return True
        u = await _acct_db.users.find_one({"id": target_id})
        if not u:
            await message.answer(f"❌ User {target_id} not found.")
        else:
            await message.answer(
                f"👤 <b>User Details</b>\n\n"
                f"ID: {u['id']}\n"
                f"Balance: {acct_fmt(u.get('balance', 0))}\n"
                f"Purchases: {u.get('purchases', 0)}\n"
                f"Loyalty Points: {u.get('loyalty_points', 0)}\n"
                f"Referral Code: {u.get('referral_code', 'N/A')}",
                parse_mode="HTML"
            )
        acct_clear_state(user_id)
        return True

    # ---- Broadcast ----
    if state == "acct_broadcast_message":
        broadcast_text = text.strip()
        users = await _acct_db.users.find().to_list(10000)
        sent_count = 0
        failed_count = 0
        for u in users:
            try:
                await _bot.send_message(u["id"], f"📢 <b>Broadcast</b>\n\n{broadcast_text}", parse_mode="HTML")
                sent_count += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed_count += 1
        await message.answer(f"✅ Broadcast sent to {sent_count} users ({failed_count} failed).")
        acct_clear_state(user_id)
        return True

    # ---- Welcome photo (admin) ----
    if state == "acct_welcome_photo":
        if message.photo and message.caption:
            file_id = message.photo[-1].file_id
            desc = message.caption
            await _acct_db.welcome.update_one({}, {"$set": {"photo_file_id": file_id, "description": desc}}, upsert=True)
            await message.answer("✅ Welcome message updated!")
            acct_clear_state(user_id)
        elif not message.photo:
            await message.answer("⚠️ Please send a photo with caption.")
        return True

    # ---- User manual video (admin) ----
    if state == "acct_user_manual_video":
        if message.video:
            video_id = message.video.file_id
            await _acct_db.user_manual.update_one({}, {"$set": {"video_file_id": video_id}}, upsert=True)
            await message.answer("✅ User manual video updated!")
            acct_clear_state(user_id)
        else:
            await message.answer("⚠️ Please send a video file.")
        return True

    return False


# ==================== TEXT MESSAGE DISPATCHER ====================
async def acct_handle_text(message: types.Message) -> bool:
    text = message.text or ""
    user_id = message.from_user.id

    if await acct_handle_state_messages(message):
        return True

    if ACCT_SUPPORT_CHAT.get(user_id) and user_id != _admin_id:
        try:
            await _bot.send_message(_admin_id, f"[Acct Shop] 📩 From {user_id}: {text}")
        except Exception:
            pass
        return True

    if text == "💳 Buy Telegram Accounts":
        await acct_show_accounts(message)
        return True
    if text == "📊 Acct Stats":
        await acct_show_stats(message)
        return True
    if text == "💱 Acct Deposit":
        await acct_deposit_menu(message)
        return True
    if text == "💵 Acct Balance":
        await acct_show_balance(message)
        return True
    if text == "🎁 Acct Referrals":
        await acct_show_referrals(message)
        return True
    if text == "💎 Acct Loyalty":
        await acct_show_loyalty(message)
        return True
    if text == "🛟 Acct Support":
        await acct_show_support(message)
        return True
    if text == "📚 Acct How To Use":
        await acct_show_user_manual(message)
        return True

    return False


# ==================== MEDIA MESSAGE DISPATCHER ====================
async def acct_handle_media(message: types.Message) -> bool:
    user_id = message.from_user.id
    state = acct_get_state(user_id)

    if state in ["acct_welcome_photo", "acct_user_manual_video"]:
        return await acct_handle_state_messages(message)

    if ACCT_SUPPORT_CHAT.get(user_id) and user_id != _admin_id:
        try:
            if message.photo:
                caption = message.caption or "📷 Image"
                await _bot.send_photo(_admin_id, message.photo[-1].file_id,
                                      caption=f"[Acct Shop] 📩 From {user_id}: {caption}")
            elif message.video:
                caption = message.caption or "🎥 Video"
                await _bot.send_video(_admin_id, message.video.file_id,
                                      caption=f"[Acct Shop] 📩 From {user_id}: {caption}")
            elif message.document:
                caption = message.caption or "📄 Document"
                await _bot.send_document(_admin_id, message.document.file_id,
                                         caption=f"[Acct Shop] 📩 From {user_id}: {caption}")
        except Exception:
            pass
        return True

    return False
