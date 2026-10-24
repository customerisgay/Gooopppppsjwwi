import asyncio
import base64
import contextlib
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import random
import re
import secrets
import shutil
import sqlite3
import string
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from aiohttp import web
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    MenuButtonWebApp,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


OWNER_ID = 0


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "store.db"))
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
DESIGN = "7-sunset"


MINIAPP_FILE = os.path.join(BASE_DIR, "miniapp.html")


def miniapp_html() -> str:

    if os.path.exists(MINIAPP_FILE):
        with open(MINIAPP_FILE, "r", encoding="utf-8") as fh:
            return fh.read()
    return base64.b64decode(MINIAPP_HTML_B64).decode("utf-8")

logging.basicConfig(
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
log = logging.getLogger("store")

CUR = "₹"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def money_plain(v) -> str:

    v = float(v or 0)
    return f"{CUR}{int(v)}" if abs(v - int(v)) < 0.005 else f"{CUR}{v:.2f}"


def money(v) -> str:
    return f"{CUR}{float(v or 0):,.2f}"


def esc(t) -> str:
    return (
        str(t if t is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def rid(n: int = 6) -> str:
    return "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(n))


def rid_digits(n: int = 5) -> str:
    return "".join(random.choice(string.digits) for _ in range(n))


_conn: sqlite3.Connection | None = None


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    cur = db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return rows


def q1(sql: str, args: tuple = ()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql: str, args: tuple = ()) -> int:
    cur = db().execute(sql, args)
    db().commit()
    last = cur.lastrowid
    cur.close()
    return last


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS features(key TEXT PRIMARY KEY, on_ INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS content(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', first_name TEXT DEFAULT '',
  balance REAL DEFAULT 0, points INTEGER DEFAULT 0, referred_by INTEGER,
  total_spent REAL DEFAULT 0, total_orders INTEGER DEFAULT 0,
  banned INTEGER DEFAULT 0, lang TEXT DEFAULT 'en',
  streak INTEGER DEFAULT 0, last_checkin TEXT DEFAULT '',
  last_seen TEXT DEFAULT '', joined_at TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS admins(
  user_id INTEGER PRIMARY KEY, role TEXT DEFAULT 'manager',
  perms TEXT DEFAULT '{}', label TEXT DEFAULT '', added_by INTEGER, added_at TEXT);

CREATE TABLE IF NOT EXISTS stock(
  id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT, flag TEXT DEFAULT '',
  code TEXT DEFAULT '', price REAL DEFAULT 0, was REAL DEFAULT 0,
  payload TEXT DEFAULT '', variant TEXT DEFAULT 'standard',
  sold INTEGER DEFAULT 0, sold_to INTEGER, sold_at TEXT, added_at TEXT);

CREATE TABLE IF NOT EXISTS orders(
  id TEXT PRIMARY KEY, user_id INTEGER, stock_id INTEGER, country TEXT,
  price REAL, status TEXT DEFAULT 'delivered', otp TEXT DEFAULT '',
  created_at TEXT, closed_at TEXT);

CREATE TABLE IF NOT EXISTS deposits(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL,
  code TEXT, status TEXT DEFAULT 'awaiting', method TEXT DEFAULT 'upi',
  utr TEXT DEFAULT '', created_at TEXT, expires_at TEXT, done_at TEXT);

CREATE TABLE IF NOT EXISTS channels(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, handle TEXT,
  chat_id TEXT DEFAULT '', required INTEGER DEFAULT 1, active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS logchannels(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, chat_id TEXT,
  is_primary INTEGER DEFAULT 0, active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS logevents(
  key TEXT PRIMARY KEY, on_ INTEGER DEFAULT 1, channel_id INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS coupons(
  code TEXT PRIMARY KEY, percent REAL, max_uses INTEGER DEFAULT 0,
  used INTEGER DEFAULT 0, active INTEGER DEFAULT 1, created_at TEXT);

CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, country TEXT,
  rating INTEGER, text TEXT DEFAULT '', hidden INTEGER DEFAULT 0, created_at TEXT);

CREATE TABLE IF NOT EXISTS wishlist(user_id INTEGER, country TEXT, PRIMARY KEY(user_id,country));
CREATE TABLE IF NOT EXISTS cart(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, country TEXT, qty INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS disputes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, user_id INTEGER,
  reason TEXT, details TEXT DEFAULT '', status TEXT DEFAULT 'open', created_at TEXT);


CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor INTEGER, action TEXT,
  detail TEXT DEFAULT '', undo TEXT DEFAULT '', ts TEXT);

CREATE TABLE IF NOT EXISTS blacklist(
  kind TEXT, value TEXT, added_at TEXT, PRIMARY KEY(kind,value));

CREATE TABLE IF NOT EXISTS referrals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, inviter INTEGER, invitee INTEGER,
  level INTEGER DEFAULT 1, earned REAL DEFAULT 0, created_at TEXT);

CREATE TABLE IF NOT EXISTS ratelog(user_id INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS claims(
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, user_id INTEGER,
    reason TEXT, details TEXT, status TEXT DEFAULT 'open',
    decided_by INTEGER DEFAULT 0, created_at TEXT, decided_at TEXT,
    UNIQUE(order_id));
CREATE TABLE IF NOT EXISTS logins(
    order_id TEXT PRIMARY KEY, state TEXT, seen_at TEXT);
CREATE TABLE IF NOT EXISTS depshots(
    dep_id INTEGER PRIMARY KEY, file_id TEXT, added_at TEXT);
CREATE TABLE IF NOT EXISTS prefs(
    user_id INTEGER, key TEXT, on_ INTEGER DEFAULT 1, UNIQUE(user_id, key));
CREATE TABLE IF NOT EXISTS debts(
    user_id INTEGER PRIMARY KEY, amount REAL DEFAULT 0, note TEXT DEFAULT '',
    given_by INTEGER DEFAULT 0, updated_at TEXT);
CREATE TABLE IF NOT EXISTS stock_alerts(
    user_id INTEGER, country TEXT, created_at TEXT, UNIQUE(user_id, country));
CREATE TABLE IF NOT EXISTS giveaways(
    id INTEGER PRIMARY KEY AUTOINCREMENT, amount REAL, slots INTEGER,
    note TEXT DEFAULT '', active INTEGER DEFAULT 1, created_by INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS giveaway_claims(
    giveaway_id INTEGER, user_id INTEGER, amount REAL, claimed_at TEXT,
    UNIQUE(giveaway_id, user_id));
CREATE TABLE IF NOT EXISTS otp_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, user_id INTEGER,
    phone TEXT, code TEXT, sent_at TEXT);
"""


DEFAULT_SETTINGS = {
    "store_name": "Zoro TG Store",
    "store_tagline": "Warm, quick top-ups",
    "support": "",
    "upi_id": "",
    "upi_name": "",
    "upi_extra": "",
    "pay_prefix": "PAY",
    "pay_window": "5",
    "gmail_id": "",
    "gmail_pass": "",
    "gmail_scan": "30",
    "verify_need_note": "1",
    "gmail_sender": "no-reply@famapp.in",
    "gmail_enabled": "1",
    "utr_fallback": "1",
    "verify_on_tap": "1",
    "night_discount": "0",
    "force_sub": "1",
    "lang_default": "en",
    "ban_chargeback": "0",
    "ban_fake_utr": "0",
    "ban_failed_verify": "0",
    "digest_to_admins": "1",
    "unique_paise": "0",
    "ref_digits": "5",
    "otp_max_per_order": "5",
    "otp_window_minutes": "60",
    "otp_api_id": "",
    "otp_api_hash": "",
    "giveaway_amount": "10",
    "giveaway_slots": "10",
    "udhar_max": "200",
    "banner_url": "",
    "banner_text": "",
    "premium_emoji_map": "",
    "emoji_pack": "premium",
    "rule_char": "\u2501",
    "rule_end": "\u2726",
    "bot_username": "",
    "tunnel_enabled": "1",
    "initdata_max_age": "0",
    "autodep_max": "0",
    "rev_goal": "10000",
    "otp_poll_seconds": "45",
    "default_price": "35",
    "min_deposit": "10",
    "max_deposit": "20000",
    "auto_approve_under": "500",
    "daily_reward": "0.40",
    "ref_l1": "5",
    "ref_l2": "1",
    "points_per_10": "1",
    "points_per_rupee": "100",
    "warranty_minutes": "10",
    "bulk_5": "5",
    "bulk_10": "10",
    "bulk_25": "15",
    "bonus_500": "5",
    "bonus_1000": "8",
    "bonus_2000": "12",
    "crypto_address": "",
    "flash_percent": "20",
    "flash_until": "",
    "leaderboard_prize": "500",
    "rate_limit": "20",
    "velocity_limit": "5",
    "low_stock_default": "10",
    "sell_open": "",
    "sell_close": "",
    "backup_hours": "6",
    "digest_at": "23:30",
    "maintenance": "0",
    "sandbox": "0",
    "logs_master": "1",
    "buyer_badge_orders": "5",
    "miniapp_url": "",
}


FEATURES: list[tuple[str, str, str, str]] = [

    ("Payments & wallet", "crypto", "Crypto / USDT top-up", "Accept USDT-TRC20 with auto-confirm."),
    ("Payments & wallet", "upiRotate", "Multiple UPI IDs", "Rotate handles, survive a frozen one."),
    ("Payments & wallet", "autoRefund", "Auto-refund to wallet", "Dead account inside warranty is refunded."),
    ("Payments & wallet", "splitPay", "Split payment", "Wallet balance plus UPI in one checkout."),
    ("Payments & wallet", "ledger", "Wallet ledger", "Running balance history with CSV export."),
    ("Payments & wallet", "depLimits", "Min / max deposit", "Caps on a single top-up."),
    ("Payments & wallet", "depBonus", "Deposit bonus tiers", "Extra credit on bigger top-ups."),
    ("Payments & wallet", "wrongAmt", "Wrong-amount handling", "Credit what was paid and flag it."),
    ("Catalog & selling", "cart", "Shopping cart", "Mix countries in one checkout."),
    ("Catalog & selling", "bulkDisc", "Bulk discount", "Price break at 5 / 10 / 25 units."),
    ("Catalog & selling", "wishlist", "Wishlist", "Save countries and rebuy in one tap."),
    ("Catalog & selling", "dynPrice", "Dynamic pricing", "Price moves with stock and time."),
    ("Catalog & selling", "flashSale", "Flash sale", "Timed discount with a countdown."),
    ("Catalog & selling", "coupons", "Coupon codes", "Percentage off at checkout."),
    ("Catalog & selling", "variants", "Account variants", "2FA, mail, spam-free — priced apart."),
    ("Catalog & selling", "healthCheck", "Stock health check", "Validate the session before selling."),
    ("Orders & after-sale", "timeline", "Order timeline", "Paid, delivered, OTP, closed."),
    ("Orders & after-sale", "warranty", "Replacement window", "Auto-replacement inside X minutes."),
    ("Orders & after-sale", "redownload", "Re-download details", "Fetch a past order again."),
    ("Orders & after-sale", "dispute", "Dispute flow", "Reason picker plus evidence."),
    ("Orders & after-sale", "sessionFile", "Session file delivery", "Send the file with self-destruct."),
    ("Orders & after-sale", "receipts", "Order receipts", "Invoice after every purchase."),
    ("Trust & social proof", "reviews", "Reviews & ratings", "Star ratings shown on every country."),
    ("Trust & social proof", "salesFeed", "Live sales feed", "Auto-post sales to your channel."),
    ("Trust & social proof", "leaderboard", "Leaderboard", "Top buyers of the week."),
    ("Trust & social proof", "buyerBadge", "Verified buyer badge", "Badge after N orders."),
    ("Growth & retention", "refL2", "Multi-level referral", "Level 1 and level 2 commission."),
    ("Growth & retention", "refDash", "Referral dashboard", "Invites, earnings and who deposited."),
    ("Growth & retention", "streak", "Check-in streak", "Escalating daily reward."),
    ("Growth & retention", "loyalty", "Loyalty points", "Earn per rupee, redeem for balance."),
    ("Growth & retention", "winback", "Re-engagement DMs", "Message users gone 7 / 30 days."),
    ("Growth & retention", "abandonRemind", "Abandoned-payment nudge", "Remind before expiry."),
    ("Growth & retention", "schedBroadcast", "Scheduled broadcasts", "Queue and target a segment."),
    ("Admin & operations", "adminRoles", "Admin roles", "Owner, manager, support."),
    ("Admin & operations", "auditLog", "Audit log", "Every admin action, with undo."),
    ("Admin & operations", "contentEditor", "Content editor", "Edit every text from the panel."),
    ("Admin & operations", "autoBackup", "Auto-backup", "Database to a private channel."),
    ("Admin & operations", "bulkUpload", "Bulk upload", "Validate, dedupe, import."),
    ("Admin & operations", "dupDetect", "Duplicate detection", "Reject sessions already stored."),
    ("Admin & operations", "lowStock", "Low-stock alerts", "Per-country threshold ping."),
    ("Admin & operations", "sellHours", "Selling hours", "Hide stock outside the window."),
    ("Admin & operations", "sandbox", "Sandbox mode", "Fake money, no real delivery."),
    ("Admin & operations", "i18n", "Multi-language", "Per-user Hindi / English."),
    ("Security & anti-abuse", "rateLimit", "Rate limiting", "Per-user action limits."),
    ("Security & anti-abuse", "multiAcc", "Multi-account detection", "Flags referral farming."),
    ("Security & anti-abuse", "velocity", "Velocity alerts", "Flags abnormally fast buying."),
    ("Security & anti-abuse", "admin2fa", "Admin 2FA", "PIN before destructive actions."),
    ("Security & anti-abuse", "autoBan", "Auto-ban rules", "Chargeback, fake UTR, failures."),
    ("Security & anti-abuse", "blacklist", "Blacklist", "Block ids, phones, UPI refs."),
    ("Security & anti-abuse", "encrypt", "Encrypt sessions", "Session payloads stored encrypted."),
    ("Analytics", "revChart", "Revenue dashboard", "Daily and weekly revenue."),
    ("Analytics", "funnel", "Conversion funnel", "Where users drop off."),
    ("Analytics", "profitRep", "Per-country profit", "Cost vs sale vs volume."),
    ("Analytics", "clv", "Customer value", "Spend, frequency, churn risk."),
    ("Analytics", "digest", "Daily digest", "End-of-day summary to admins."),
    ("From your original bot", "liveOtp", "Live OTP forwarding",
     "Logs into the sold session and forwards the real login code to the buyer."),
    ("From your original bot", "payQR", "UPI QR code",
     "Sends a scannable QR with every payment request."),
    ("From your original bot", "giveaway", "Giveaway",
     "Limited-slot giveaway; first to claim wins."),
    ("From your original bot", "udhar", "Udhar / credit",
     "Lend balance; auto-deducted from the next deposit."),
    ("From your original bot", "restockAlert", "Restock alerts",
     "Users subscribe to a sold-out country and get a DM on restock."),
    ("From your original bot", "guide", "Guide pages",
     "Paged how-to-buy, OTP and login walkthrough."),
    ("From your original bot", "premiumEmoji", "Premium emoji",
     "Renders your custom emoji pack in bot messages."),
    ("From your original bot", "menuBanner", "Menu banner",
     "Image or notice pinned above the main menu."),
    ("From your original bot", "colorBtn", "Coloured buttons",
     "Sends the style field so clients that support it colour the buttons."),
    ("From your original bot", "autoTunnel", "Cloudflare auto-tunnel",
     "Downloads cloudflared and publishes the mini app on an https URL by itself."),
    ("From your original bot", "otpForward", "Auto OTP forwarding",
     "Watches sold sessions and pushes each new login code without being asked."),
    ("From your original bot", "autoDeposit", "Auto-approve small deposits",
     "Deposits under your threshold are approved without an admin."),
    ("From your original bot", "revGoal", "Revenue goal",
     "Monthly target with a progress bar on the dashboard."),
    ("From your original bot", "sessionStats", "Session health stats",
     "Live / dead / sold breakdown of your uploaded sessions."),
    ("From your original bot", "manualLogin", "Add stock by logging in",
     "Sign into a number with code and 2FA; the session is saved for you."),
    ("Quality of life", "slashCmds", "Slash commands", "Registered command menu."),
    ("Quality of life", "stockCounter", "Live stock counter", "Auto-updating counter."),
    ("Quality of life", "onboarding", "Onboarding tour", "Three-step walkthrough."),
    ("Quality of life", "notifPrefs", "Notification preferences", "Users pick their alerts."),
    ("Quality of life", "pushNotif", "Mini app push", "Mini app actions notify in chat."),
]
FEATURE_KEYS = [f[1] for f in FEATURES]


DEFAULT_CONTENT = {
    "storeName": "Zoro TG Store",
    "storeTag": "Warm, quick top-ups",
    "tick1": "<b>Instant delivery</b> on every country",
    "tick2": "Wallet top-ups <b>auto-verified</b>",
    "tick3": "Failed login? <b>Auto refund</b>",
    "tick4": "Daily check-in <b>bonus</b>",
    "heroKick": "Fresh drop",
    "heroH1": "Top up.<br>Grab. Go.",
    "heroSub": "Instant delivery, OTP in-app, wallet top-ups verified automatically. {n} countries live.",
    "heroBtn1": "Browse Stock",
    "heroBtn2": "Add Funds",
    "feat1": "~8s", "feat1s": "delivery",
    "feat2": "Auto", "feat2s": "refunds",
    "feat3": "UPI", "feat3s": "auto-verify",
    "flashPill": "Flash −20%", "flashText": "Ends in", "flashBtn": "View",
    "wheelKick": "Rotating deals", "wheelTitle": "Up to 28% off",
    "wheelWords": "INSTANT, AUTO-VERIFY, REFUNDS, 24×7",
    "shopHead": "In Stock", "shopSort": "Sort",
    "searchPh": "Search country or code…", "alertsBtn": "Alerts",
    "walKick": "Balance",
    "walSub": "Pay by UPI — tap Verify after paying and your wallet is credited in seconds.",
    "walAdd": "Add Funds",
    "payTitle": "Payment Details",
    "paySub": "Set your UPI handle in the admin panel — nothing is hard-coded.",
    "payPrefix": "PAY", "payWindow": "5",
    "sheetTitle": "Add",
    "sheetSub": "Pay the exact amount, then tap Verify Payment. The request expires in {m} minutes.",
    "payBtn": "Pay Now", "verifyBtn": "Verify Payment", "utrBtn": "Not detected? Submit UTR",
    "step1": "Payment request created",
    "step2": "Pay the exact amount in any UPI app",
    "step3": "Tap Verify — we confirm your payment",
    "step4": "Wallet credited",
    "msgChecking": "Checking your payment…",
    "msgOk": "Verified — wallet credited",
    "msgNone": "No payments found",
    "msgExpired": "Request expired — create a new one.",
    "navShop": "Shop", "navOrders": "Orders", "navEarn": "Earn",
    "navWallet": "Wallet", "navYou": "You",
    "welcome": "Welcome to <b>{store}</b>.\n\nInstant accounts, wallet top-ups verified automatically and the login code delivered right here.",
    "terms": "All sales are final unless the account fails on first login inside the warranty window.",
    "support_text": "Send your question and we will reply here.",
    "emptyTitle": "Restocking",
    "emptyBody": "Everything just sold out. Fresh accounts land here shortly \u2014 turn on an alert and you will hear first.",
    "divider": "\u2726\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2726",
    "guide1Title": "How to buy",
    "guide1Body": "Buying takes under a minute.\n\n1. Tap <b>Browse Stock</b>\n2. Pick a country that has stock\n3. Choose how many you want\n4. Tap <b>Confirm Buy</b>\n\nNo balance? Tap <b>Add Funds</b>, pay by UPI and your wallet is credited automatically.",
    "guide2Title": "Auto OTP delivery",
    "guide2Body": "Every purchase includes live OTP forwarding.\n\n1. After payment the account is linked to you\n2. Log in to Telegram with the number you bought\n3. The login code arrives in this chat\n\nYou get a limited number of codes per order, so type it carefully.",
    "guide3Title": "Logging in",
    "guide3Body": "The account comes with the phone, the session and the 2FA password where included.\n\n1. Telegram \u2192 log in by phone number\n2. Enter the number you bought\n3. Enter the code that arrives here\n4. Enter the cloud password if one was supplied",
    "guide4Title": "Safety",
    "guide4Body": "Change the 2FA password as soon as you are in, and do not share the session with anyone. Warranty covers first-login failures only.",
    "giveawayTitle": "Giveaway",
    "giveawayBody": "First come, first served. Tap claim before the slots run out.",
    "giveawayDone": "All slots are gone. Watch for the next one.",
    "giveawayGot": "Claimed! The amount is in your wallet.",
    "udharTitle": "Udhar",
    "udharBody": "This is credit an admin lent you. It is deducted automatically from your next deposit.",
    "alertOn": "You will be messaged the moment this country is back.",
    "alertOff": "Restock alert removed.",
    "restockTitle": "Back in stock",
    "qrCaption": "Scan with any UPI app, or use the Pay button.",
    "otpDelivered": "Login code received",
    "otpLimit": "You have used all the codes included with this order.",
}

LOG_EVENTS = [
    ("join", "New user joined"),
    ("deposit", "Deposit / payment"),
    ("purchase", "Purchase"),
    ("otp", "OTP requested"),
    ("stock", "Stock added"),
    ("admin", "Admin action"),
    ("error", "Errors & warnings"),
]


PERMS = [
    ("stock", "Stock", "Add, edit and clear inventory"),
    ("pricing", "Pricing", "Change prices and discounts"),
    ("orders", "Orders", "View orders, refund, replace"),
    ("users", "Users", "Credit, ban and inspect users"),
    ("broadcast", "Broadcast", "Send messages to everyone"),
    ("payments", "Payments", "UPI, Gmail, deposits, approvals"),
    ("features", "Features", "Turn features on and off"),
    ("content", "Content", "Edit every text in bot and app"),
    ("channels", "Channels", "Force-join channel list"),
    ("logs", "Logs", "Log channels and routing"),
    ("security", "Security", "Bans, blacklist, limits"),
    ("analytics", "Analytics", "Revenue and reports"),
    ("settings", "Settings", "Store settings and backups"),
    ("admins", "Manage admins", "Add or remove other admins"),
]
PERM_KEYS = [p[0] for p in PERMS]


def init_db() -> None:
    db().executescript(SCHEMA)
    db().commit()
    for k, v in DEFAULT_SETTINGS.items():
        ex("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    for k in FEATURE_KEYS:
        ex("INSERT OR IGNORE INTO features(key,on_) VALUES(?,1)", (k,))
    for k, v in DEFAULT_CONTENT.items():
        ex("INSERT OR IGNORE INTO content(key,value) VALUES(?,?)", (k, v))
    for k, _label in LOG_EVENTS:
        ex("INSERT OR IGNORE INTO logevents(key,on_,channel_id) VALUES(?,1,0)", (k,))
    if OWNER_ID:
        ex(
            "INSERT OR IGNORE INTO admins(user_id,role,perms,label,added_by,added_at)"
            " VALUES(?,'owner',?,'Owner',?,?)",
            (OWNER_ID, json.dumps({k: True for k in PERM_KEYS}), OWNER_ID, now()),
        )
    log.info("database ready → %s", DB_PATH)


def sget(key: str, default: str = "") -> str:
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r and r["value"] is not None else default


def sgetf(key: str, default: float = 0.0) -> float:
    try:
        return float(sget(key, "") or default)
    except ValueError:
        return default


def sset(key: str, value) -> None:
    ex(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def feat(key: str) -> bool:
    r = q1("SELECT on_ FROM features WHERE key=?", (key,))
    return bool(r["on_"]) if r else False


def feat_set(key: str, on: bool) -> None:
    ex(
        "INSERT INTO features(key,on_) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET on_=excluded.on_",
        (key, 1 if on else 0),
    )


def feat_all() -> dict:
    return {r["key"]: bool(r["on_"]) for r in q("SELECT key,on_ FROM features")}


def cget(key: str, default: str = "") -> str:
    r = q1("SELECT value FROM content WHERE key=?", (key,))
    if r and r["value"] is not None:
        return r["value"]
    return DEFAULT_CONTENT.get(key, default)


def cset(key: str, value: str) -> None:
    ex(
        "INSERT INTO content(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def content_all() -> dict:
    d = dict(DEFAULT_CONTENT)
    for r in q("SELECT key,value FROM content"):
        if r["value"] is not None:
            d[r["key"]] = r["value"]
    return d


def get_user(uid: int, username: str = "", first: str = "") -> sqlite3.Row:
    r = q1("SELECT * FROM users WHERE user_id=?", (uid,))
    if not r:
        ex(
            "INSERT INTO users(user_id,username,first_name,joined_at,last_seen)"
            " VALUES(?,?,?,?,?)",
            (uid, username or "", first or "", now(), now()),
        )
        r = q1("SELECT * FROM users WHERE user_id=?", (uid,))
    elif username and r["username"] != username:
        ex("UPDATE users SET username=?,last_seen=? WHERE user_id=?", (username, now(), uid))
        r = q1("SELECT * FROM users WHERE user_id=?", (uid,))
    return r


def balance(uid: int) -> float:
    r = q1("SELECT balance FROM users WHERE user_id=?", (uid,))
    return float(r["balance"]) if r else 0.0


def credit(uid: int, amount: float) -> float:
    get_user(uid)
    ex("UPDATE users SET balance=ROUND(balance+?,2) WHERE user_id=?", (float(amount), uid))
    return balance(uid)


def is_owner(uid: int) -> bool:
    return uid == OWNER_ID or bool(
        q1("SELECT 1 FROM admins WHERE user_id=? AND role='owner'", (uid,))
    )


def admin_row(uid: int):
    return q1("SELECT * FROM admins WHERE user_id=?", (uid,))


def is_admin(uid: int) -> bool:
    return is_owner(uid) or bool(admin_row(uid))


def perms_of(uid: int) -> dict:
    if is_owner(uid):
        return {k: True for k in PERM_KEYS}
    r = admin_row(uid)
    if not r:
        return {k: False for k in PERM_KEYS}
    try:
        p = json.loads(r["perms"] or "{}")
    except json.JSONDecodeError:
        p = {}
    return {k: bool(p.get(k)) for k in PERM_KEYS}


def can(uid: int, perm: str) -> bool:
    if is_owner(uid):
        return True
    return bool(perms_of(uid).get(perm))


def audit(actor: int, action: str, detail: str = "", undo: str = "") -> None:
    if not feat("auditLog"):
        return
    ex(
        "INSERT INTO audit(actor,action,detail,undo,ts) VALUES(?,?,?,?,?)",
        (actor, action, detail, undo, now()),
    )


def blacklisted(kind: str, value: str) -> bool:
    if not feat("blacklist"):
        return False
    return bool(q1("SELECT 1 FROM blacklist WHERE kind=? AND value=?", (kind, str(value))))


def rate_ok(uid: int) -> bool:

    if not feat("rateLimit"):
        return True
    limit = int(sgetf("rate_limit", 20) or 20)
    t = time.time()
    ex("DELETE FROM ratelog WHERE ts < ?", (t - 60,))
    n = q1("SELECT COUNT(*) c FROM ratelog WHERE user_id=?", (uid,))["c"]
    if n >= limit:
        return False
    ex("INSERT INTO ratelog(user_id,ts) VALUES(?,?)", (uid, t))
    return True


FLAGS = {
    "india": "🇮🇳", "indonesia": "🇮🇩", "brazil": "🇧🇷", "nigeria": "🇳🇬",
    "vietnam": "🇻🇳", "philippines": "🇵🇭", "pakistan": "🇵🇰", "bangladesh": "🇧🇩",
    "usa": "🇺🇸", "uk": "🇬🇧", "russia": "🇷🇺", "kenya": "🇰🇪", "egypt": "🇪🇬",
}


NOTIF_KEYS = [
    ("restock", "Restock alerts", "DM me when my country is back"),
    ("receipts", "Order receipts", "Send a receipt after each buy"),
    ("promos", "Promo messages", "Flash sales and giveaways"),
    ("push", "Mini app push", "Notify me in the bot chat"),
]


def pref_on(uid: int, key: str, default: bool = True) -> bool:
    r = q1("SELECT on_ FROM prefs WHERE user_id=? AND key=?", (uid, key))
    return bool(r["on_"]) if r else default


def pref_set(uid: int, key: str, on: bool) -> None:
    ex("INSERT INTO prefs(user_id,key,on_) VALUES(?,?,?) "
       "ON CONFLICT(user_id,key) DO UPDATE SET on_=excluded.on_",
       (uid, key, 1 if on else 0))


def prefs_of(uid: int) -> dict:
    return {k: pref_on(uid, k) for k, _l, _d in NOTIF_KEYS}


def redeem_points(uid: int) -> tuple:

    if not feat("loyalty"):
        return False, 0.0, 0
    u = get_user(uid)
    pts = int(u["points"] or 0)
    per = max(1, int(sgetf("points_per_rupee", 100)))
    worth = round(pts / per * 10, 2)
    if pts <= 0 or worth < 1:
        return False, 0.0, pts
    credit(uid, worth)
    ex("UPDATE users SET points=0 WHERE user_id=?", (uid,))
    return True, worth, pts


def auto_check_on() -> bool:

    return bool(sget("gmail_id") and sget("gmail_pass")
                and sget("gmail_enabled", "1") == "1")


def payment_review(dep, uid: int, utr: str) -> str:
    u = get_user(uid)
    name = u["first_name"] or "—"
    handle = f"@{u['username']}" if u["username"] else "no username"
    return premiumize("\n".join([
        head("Payment review"),
        "",
        f"<b>User:</b> {esc(name)} ({esc(handle)})",
        f"<b>ID:</b> <code>{uid}</code>",
        f"<b>Amount:</b> {money(dep['amount'])}",
        f"<b>UTR:</b> <code>{esc(utr or '—')}</code>",
        f"<b>Reference:</b> <code>{esc(dep['code'])}</code>",
        f"<b>Status:</b> {esc(dep['status'] or 'review')}",
        "",
        "⚠️ <i>Check your bank or UPI history: matching amount, receiver and UTR. "
        "A screenshot alone is not proof of receipt.</i>",
    ]))


def utr_report(dep, uid: int, utr: str) -> str:

    u = get_user(uid)
    created = (dep["created_at"] or "").replace("T", " ")[:16]
    return premiumize("\n".join([
        head("Transaction submitted", "Needs your approval"),
        "",
        f"👤 <b>Name:</b> {esc(u['first_name'] or '—')}",
        f"🔗 <b>Username:</b> @{esc(u['username'] or '—')}",
        f"🆔 <b>User id:</b> <code>{uid}</code>",
        "",
        f"💵 <b>Amount:</b> {money(dep['amount'])}",
        f"🧾 <b>Transaction no:</b> <code>{esc(utr)}</code>",
        f"🔖 <b>Reference:</b> <code>{esc(dep['code'])}</code>",
        f"🏦 <b>Method:</b> {esc((dep['method'] or 'upi').upper())}",
        f"🕒 <b>Requested:</b> {esc(created)}",
        f"💰 <b>Wallet now:</b> {money(u['balance'])}",
        f"📦 <b>Past orders:</b> {u['total_orders']} · spent {money(u['total_spent'])}",
        "",
        f"Approve with <code>/approve_{dep['id']}</code> or from Admin → Payments.",
    ]))


def fmt_amt(v) -> str:
    v = float(v or 0)
    return str(int(v)) if abs(v - int(v)) < 0.005 else f"{v:.2f}"


def flag_for(country: str) -> str:
    return FLAGS.get((country or "").strip().lower(), "🌍")


def sell_hours_open() -> bool:
    if not feat("sellHours"):
        return True
    o, c = sget("sell_open"), sget("sell_close")
    if not o or not c:
        return True
    try:
        nowt = datetime.now().strftime("%H:%M")
        if o <= c:
            return o <= nowt <= c
        return nowt >= o or nowt <= c
    except Exception:
        return True


def flash_active() -> bool:
    if not feat("flashSale"):
        return False
    until = sget("flash_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now(timezone.utc)
    except ValueError:
        return False


def price_of(country: str) -> float:

    r = q1(
        "SELECT MIN(price) p, COUNT(*) n FROM stock WHERE country=? AND sold=0", (country,)
    )
    base = float(r["p"]) if r and r["p"] else sgetf("default_price", 35)
    qty = int(r["n"]) if r else 0
    if feat("dynPrice") and qty and qty < 20:
        base *= 1.10
    if flash_active():
        base *= 1 - sgetf("flash_percent", 20) / 100.0
    return round(base, 2)


def was_price(country: str) -> float:
    r = q1("SELECT MAX(was) w FROM stock WHERE country=? AND sold=0", (country,))
    return round(float(r["w"]), 2) if r and r["w"] else 0.0


def countries() -> list[dict]:
    rows = q(
        "SELECT country, COUNT(*) qty, MIN(code) code FROM stock "
        "WHERE sold=0 GROUP BY country ORDER BY country"
    )
    out = []
    for r in rows:
        out.append(
            {
                "c": r["country"],
                "f": flag_for(r["country"]),
                "code": r["code"] or "",
                "qty": r["qty"],
                "price": price_of(r["country"]),
                "was": was_price(r["country"]),
                "hot": 1 if r["qty"] > 50 else 0,
            }
        )
    return out


def bulk_discount(qty: int) -> float:
    if not feat("bulkDisc"):
        return 0.0
    if qty >= 25:
        return sgetf("bulk_25", 15)
    if qty >= 10:
        return sgetf("bulk_10", 10)
    if qty >= 5:
        return sgetf("bulk_5", 5)
    return 0.0


def coupon_percent(code: str) -> float:
    if not feat("coupons") or not code:
        return 0.0
    r = q1("SELECT * FROM coupons WHERE code=? AND active=1", (code.strip().upper(),))
    if not r:
        return 0.0
    if r["max_uses"] and r["used"] >= r["max_uses"]:
        return 0.0
    return float(r["percent"])


def quote(country: str, qty: int = 1, code: str = "") -> dict:
    unit = price_of(country)
    total = unit * max(1, qty)
    bd = bulk_discount(qty)
    if bd:
        total *= 1 - bd / 100.0
    cp = coupon_percent(code)
    if cp:
        total *= 1 - cp / 100.0
    return {
        "unit": round(unit, 2),
        "qty": qty,
        "bulk": bd,
        "coupon": cp,
        "total": round(total, 2),
    }


def take_stock(country: str) -> sqlite3.Row | None:

    while True:
        row = q1(
            "SELECT * FROM stock WHERE country=? AND sold=0 ORDER BY id LIMIT 1", (country,)
        )
        if not row:
            return None
        if feat("healthCheck") and not session_alive(row):
            ex("UPDATE stock SET sold=2, sold_at=? WHERE id=?", (now(), row["id"]))
            continue
        return row


def session_alive(row: sqlite3.Row) -> bool:

    return bool((row["payload"] or "").strip())


def deliver(uid: int, country: str, price: float) -> tuple[str | None, sqlite3.Row | None]:
    row = take_stock(country)
    if not row:
        return None, None
    oid = "ORD-" + rid(5)
    ex("UPDATE stock SET sold=1, sold_to=?, sold_at=? WHERE id=?", (uid, now(), row["id"]))
    ex(
        "INSERT INTO orders(id,user_id,stock_id,country,price,status,created_at)"
        " VALUES(?,?,?,?,?,'delivered',?)",
        (oid, uid, row["id"], country, price, now()),
    )
    ex(
        "UPDATE users SET total_spent=ROUND(total_spent+?,2), total_orders=total_orders+1"
        " WHERE user_id=?",
        (price, uid),
    )
    if feat("loyalty"):
        pts = int(price / 10 * sgetf("points_per_10", 1))
        if pts:
            ex("UPDATE users SET points=points+? WHERE user_id=?", (pts, uid))
    return oid, row


def pay_referral(uid: int, amount: float) -> None:
    u = q1("SELECT referred_by FROM users WHERE user_id=?", (uid,))
    if not u or not u["referred_by"]:
        return
    l1 = u["referred_by"]
    cut1 = round(amount * sgetf("ref_l1", 5) / 100.0, 2)
    if cut1 > 0:
        credit(l1, cut1)
        ex(
            "INSERT INTO referrals(inviter,invitee,level,earned,created_at) VALUES(?,?,1,?,?)",
            (l1, uid, cut1, now()),
        )
    if feat("refL2"):
        u2 = q1("SELECT referred_by FROM users WHERE user_id=?", (l1,))
        if u2 and u2["referred_by"]:
            cut2 = round(amount * sgetf("ref_l2", 1) / 100.0, 2)
            if cut2 > 0:
                credit(u2["referred_by"], cut2)
                ex(
                    "INSERT INTO referrals(inviter,invitee,level,earned,created_at)"
                    " VALUES(?,?,2,?,?)",
                    (u2["referred_by"], uid, cut2, now()),
                )


def deposit_bonus(amount: float) -> float:
    if not feat("depBonus"):
        return 0.0
    if amount >= 2000:
        return round(amount * sgetf("bonus_2000", 12) / 100.0, 2)
    if amount >= 1000:
        return round(amount * sgetf("bonus_1000", 8) / 100.0, 2)
    if amount >= 500:
        return round(amount * sgetf("bonus_500", 5) / 100.0, 2)
    return 0.0


def whole_rupees(base: float) -> float:
    return float(int(round(float(base))))


def unique_amount(base: float) -> float:


    base = round(float(base), 2)
    if sget("unique_paise", "0") != "1":
        return whole_rupees(base)
    if abs(base - int(base)) > 0.001:
        return base
    taken = {
        round(float(r["amount"]), 2)
        for r in q("SELECT amount FROM deposits WHERE status IN "
                   "('awaiting','verifying','review')")
    }
    whole = int(base)
    for _ in range(80):
        cand = round(whole + random.randint(1, 99) / 100.0, 2)
        if cand not in taken:
            return cand
    return round(base + 0.01, 2)


def create_deposit(uid: int, amount: float, method: str = "upi") -> sqlite3.Row:
    mins = int(sgetf("pay_window", 5) or 5)
    digits = max(3, min(8, int(sgetf("ref_digits", 5))))
    code = (sget("pay_prefix", "PAY") or "PAY") + rid_digits(digits)
    final = unique_amount(amount)
    exp = (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat(timespec="seconds")
    did = ex(
        "INSERT INTO deposits(user_id,amount,code,status,method,created_at,expires_at)"
        " VALUES(?,?,?,'awaiting',?,?,?)",
        (uid, final, code, method, now(), exp),
    )
    return q1("SELECT * FROM deposits WHERE id=?", (did,))


def deposit_expired(dep: sqlite3.Row) -> bool:
    try:
        return datetime.fromisoformat(dep["expires_at"]) < datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def upi_link(amount: float, code: str) -> str:
    pa = sget("upi_id")
    pn = sget("upi_name") or sget("store_name")
    if not pa:
        return ""
    qs = urllib.parse.urlencode(
        {
            "pa": pa,
            "pn": pn,
            "am": fmt_amt(amount),
            "cu": "INR",
            "tn": code,
            "tr": code,
            "mc": "0000",
        },
        quote_via=urllib.parse.quote,
        safe="@.-_",
    )
    return "upi://pay?" + qs


def gmail_login():

    if sget("gmail_enabled", "1") != "1":
        return None, "automatic checking is switched off"
    user, pwd = sget("gmail_id"), sget("gmail_pass")
    if not user or not pwd:
        return None, "no Gmail address or password saved"
    try:
        import imaplib

        box = imaplib.IMAP4_SSL("imap.gmail.com", timeout=25)
        box.login(user, pwd.replace(" ", ""))
        return box, ""
    except Exception as e:
        msg = str(e)
        if "AUTHENTICATIONFAILED" in msg.upper() or "Invalid credentials" in msg:
            return None, ("Gmail refused the login. Use a 16-character App Password "
                          "from myaccount.google.com/apppasswords, not your normal password.")
        return None, f"could not reach Gmail: {msg[:120]}"


def gmail_message_text(msg) -> str:

    from email.header import decode_header

    subject = ""
    with contextlib.suppress(Exception):
        raw = decode_header(msg.get("Subject", ""))[0][0]
        subject = raw.decode(errors="ignore") if isinstance(raw, bytes) else str(raw)
    body = ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        body += payload.decode(errors="ignore") + "\n"
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode(errors="ignore")
    except Exception:
        pass
    return subject + "\n" + body


CREDIT_WORDS = (
    "received", "credited", "credit", "you got", "added to your",
    "has sent you", "sent you", "money in",
)

DEBIT_WORDS = (
    "you paid", "you sent", "debited", "debit", "spent", "paid to",
    "sent to", "withdrawn", "purchase of",
)
PAY_WORDS = CREDIT_WORDS + ("payment", "upi", "txn", "transaction", "deposit")


def looks_like_credit(low: str) -> bool:

    if not any(w in low for w in CREDIT_WORDS):
        return False
    first_credit = min((low.find(w) for w in CREDIT_WORDS if w in low), default=10 ** 6)
    first_debit = min((low.find(w) for w in DEBIT_WORDS if w in low), default=10 ** 6)
    return first_credit <= first_debit


def amount_variants(amount: float) -> list:

    two = f"{float(amount):.2f}"
    return [two, f"{CUR}{two}", f"rs{two}"]


def amount_patterns(amount: float) -> list:

    amount = round(float(amount), 2)
    two = f"{amount:.2f}"
    forms = {two, f"{amount:,.2f}"}
    if abs(amount - int(amount)) <= 0.001:
        whole = str(int(amount))
        forms |= {whole, f"{int(amount):,}", f"{whole}.0", f"{whole}.00"}
    pats = []
    for f in forms:

        pats.append(re.compile(r"(?<!\d)(?<!\d[.,])" + re.escape(f) + r"(?!\d)"))
    return pats


def allowed_senders() -> list:
    raw = sget("gmail_sender", "")
    return [x.strip().lower() for x in re.split(r"[,\s]+", raw) if x.strip()]


def mail_from(msg) -> str:
    from email.utils import parseaddr

    return (parseaddr(msg.get("From", ""))[1] or msg.get("From", "")).lower()


def mail_datetime(msg):
    from email.utils import parsedate_to_datetime

    try:
        d = parsedate_to_datetime(msg.get("Date", ""))
        if d and d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def check_gmail_detailed(amount: float, code: str, since: str = "") -> dict:


    result = {"found": False, "utr": "", "sender": "", "reason": "", "scanned": 0}
    box, err = gmail_login()
    if not box:
        result["reason"] = err
        return result

    import email as emaillib

    cutoff = None
    if since:
        with contextlib.suppress(Exception):
            cutoff = datetime.fromisoformat(since) - timedelta(minutes=10)

    try:
        box.select("inbox")
        senders = allowed_senders()
        typ, messages = "NO", [b""]
        if senders:

            typ, messages = box.search(None, "FROM", f'"{senders[0]}"')
        if typ != "OK" or not messages or not messages[0].strip():
            typ, messages = box.search(None, "ALL")
        if typ != "OK":
            result["reason"] = "could not list the inbox"
            return result
        ids = messages[0].split()[-int(sgetf("gmail_scan", 30)):]
        result["scanned"] = len(ids)
        pats = amount_patterns(amount)
        code_l = (code or "").lower()
        has_paise = abs(float(amount) - int(float(amount))) > 0.001
        unique_on = sget("unique_paise", "0") == "1"
        rival = q1(
            "SELECT COUNT(*) c FROM deposits WHERE status IN ('awaiting','verifying') "
            "AND ROUND(amount,2)=? AND code<>?", (round(float(amount), 2), code or "")
        )["c"]


        need_note = (sget("verify_need_note", "1") == "1"
                     and not (has_paise and unique_on)
                     and rival > 0)
        skipped_old = 0
        saw_amount = False
        wrong_sender = 0

        for e_id in reversed(ids):
            typ, data = box.fetch(e_id, "(RFC822)")
            if typ != "OK" or not data or not data[0]:
                continue
            msg = emaillib.message_from_bytes(data[0][1])
            sender_addr = mail_from(msg)
            if senders and not any(sd in sender_addr for sd in senders):
                wrong_sender += 1
                continue
            text = gmail_message_text(msg)
            low = text.lower()


            if not looks_like_credit(low):
                continue
            if not any(p.search(low) for p in pats):
                continue
            saw_amount = True
            if cutoff is not None:
                when = mail_datetime(msg)
                if when is not None and when < cutoff:
                    skipped_old += 1
                    continue
            if need_note and code_l and code_l not in low:
                continue

            m = re.search(r"utr[:\s#]*([0-9]{6,})", low)
            if not m:
                m = re.search(r"(?:ref|reference|txn|transaction)[^0-9]{0,8}([0-9]{8,})", low)
            utr = m.group(1) if m else f"amt-{amount:.2f}"
            if q1("SELECT 1 FROM deposits WHERE utr=? AND status='completed'", (utr,)):
                continue
            sender = ""
            sm = re.search(r"from[:\s]+([^\n<]{3,40})", text, re.IGNORECASE)
            if sm:
                sender = sm.group(1).strip()
            result.update(found=True, utr=utr, sender=sender)
            return result

        who = senders[0] if senders else "any sender"
        if skipped_old:
            result["reason"] = (f"found {money(amount)} in {skipped_old} older email(s), but "
                                "none newer than this request — pay again or submit the UTR")
        elif saw_amount:
            result["reason"] = (f"an email mentions {money(amount)} but not the reference "
                                f"{code}, and another request for the same amount is open "
                                "— send the UTR instead")
        elif senders and wrong_sender and wrong_sender == len(ids):
            result["reason"] = (f"no mail from {who} in the last {len(ids)} messages — "
                                "check the sender address in Admin → Extras")
        else:
            result["reason"] = (f"no payment of exactly {money(amount)} from {who} yet "
                                f"(looked at {len(ids)} messages)")
        return result
    except Exception as e:
        result["reason"] = f"error while reading mail: {str(e)[:120]}"
        return result
    finally:
        with contextlib.suppress(Exception):
            box.close()
        with contextlib.suppress(Exception):
            box.logout()


def check_gmail_payment(amount: float, code: str) -> str | None:
    r = check_gmail_detailed(amount, code)
    if r["found"]:
        return r["utr"]
    if r["reason"]:
        log.info("gmail verify: %s", r["reason"])
    return None


def settle_deposit(dep: sqlite3.Row, utr: str = "") -> float:


    amount = float(dep["amount"])
    bonus = deposit_bonus(amount)
    credit(dep["user_id"], amount + bonus)
    if feat("udhar"):
        settled = clear_debt(dep["user_id"], amount + bonus)
        if settled:
            credit(dep["user_id"], -settled)
    ex(
        "UPDATE deposits SET status='completed', utr=?, done_at=? WHERE id=?",
        (utr, now(), dep["id"]),
    )
    pay_referral(dep["user_id"], amount)
    return amount + bonus


def claim_checkin(uid: int) -> tuple[bool, float, int]:

    u = get_user(uid)
    today = datetime.now(timezone.utc).date().isoformat()
    if u["last_checkin"] == today:
        return False, 0.0, u["streak"]
    yday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    streak = (u["streak"] + 1) if u["last_checkin"] == yday else 1
    base = sgetf("daily_reward", 0.40)
    amount = round(base * (1 + 0.25 * min(streak - 1, 6)), 2) if feat("streak") else base
    credit(uid, amount)
    ex("UPDATE users SET last_checkin=?, streak=? WHERE user_id=?", (today, streak, uid))
    return True, amount, streak


def leaderboard(limit: int = 5) -> list[sqlite3.Row]:
    return q(
        "SELECT user_id, username, total_spent FROM users "
        "WHERE total_spent > 0 ORDER BY total_spent DESC LIMIT ?",
        (limit,),
    )


def country_rating(country: str) -> tuple[float, int]:
    r = q1(
        "SELECT AVG(rating) a, COUNT(*) n FROM reviews WHERE country=? AND hidden=0",
        (country,),
    )
    if not r or not r["n"]:
        return 0.0, 0
    return round(float(r["a"]), 1), int(r["n"])


_APP: Application | None = None


async def send_log(event: str, text: str) -> None:
    if not _APP or sget("logs_master", "1") != "1":
        return
    ev = q1("SELECT * FROM logevents WHERE key=?", (event,))
    if ev and not ev["on_"]:
        return
    target = None
    if ev and ev["channel_id"]:
        target = q1("SELECT * FROM logchannels WHERE id=? AND active=1", (ev["channel_id"],))
    if not target:
        target = q1("SELECT * FROM logchannels WHERE is_primary=1 AND active=1")
    if not target:
        target = q1("SELECT * FROM logchannels WHERE active=1")
    if not target:
        return
    with contextlib.suppress(TelegramError):
        await _APP.bot.send_message(
            chat_id=target["chat_id"], text=text, parse_mode=ParseMode.HTML
        )


async def notify_admins(text: str, perm: str | None = None) -> None:
    if not _APP:
        return
    for r in q("SELECT user_id FROM admins"):
        if perm and not can(r["user_id"], perm):
            continue
        with contextlib.suppress(TelegramError):
            await _APP.bot.send_message(
                chat_id=r["user_id"], text=text, parse_mode=ParseMode.HTML
            )


async def notify_user(uid: int, text: str) -> None:
    if not _APP:
        return
    with contextlib.suppress(TelegramError):
        await _APP.bot.send_message(chat_id=uid, text=text, parse_mode=ParseMode.HTML)


BTN_W = 18


def pad(label: str, width: int = BTN_W) -> str:

    label = label.strip()
    if len(label) >= width:
        return label
    total = width - len(label)
    left = total // 2
    return "\u2009" * left + label + "\u2009" * (total - left)


DANGER_WORDS = (
    "cancel", "delete", "remove", "clear", "empty", "ban", "unban", "block",
    "revoke", "close", "stop", "off", "reject", "decline", "dispute", "blacklist",
    "reset", "wipe", "kill", "end", "drop", "🗑", "✖", "⛔", "🚫", "␡",
)
SUCCESS_WORDS = (
    "buy", "confirm", "checkout", "claim", "verify", "pay", "add funds", "deposit",
    "approve", "grant", "accept", "start", "launch", "save", "apply", "redeem",
    "check-in", "checkin", "yes", "done", "open store", "browse", "shop", "get otp",
    "new code", "replace", "top up", "i joined", "all on", "enable", "upload",
    "✅", "✔", "🎁", "🛒", "🛍", "⚡",
)


def style_for(label: str, data: str = "") -> str:

    hay = f"{label} {data}".lower()
    if any(w in hay for w in DANGER_WORDS):
        return "danger"
    if any(w in hay for w in SUCCESS_WORDS):
        return "success"
    return "primary"


def B(label: str, data: str, width: int = BTN_W, style: str | None = None):

    text = premiumize_label(label)
    return ColorButton(
        pad(text, width), callback_data=data, style=style or style_for(text, data)
    )


SAFE_SCHEMES = ("http://", "https://", "tg://", "t.me/")


def url_ok(url: str) -> bool:

    u = (url or "").strip().lower()
    return bool(u) and u.startswith(SAFE_SCHEMES)


def BU(label: str, url: str, width: int = BTN_W, style: str | None = None):
    text = premiumize_label(label)
    if not url_ok(url):
        log.warning("refusing button url %r — Telegram cannot open that protocol", url[:40])
        return B(label, "noop", width, style)
    return ColorButton(pad(text, width), url=url, style=style or style_for(text, url))


def premiumize_label(label: str) -> str:

    try:
        return styled(label)
    except NameError:
        return label


RULE_WIDTH = 19


def rule() -> str:

    mid = sget("rule_char", "━") or "━"
    end = sget("rule_end", "✦") or "✦"
    return end + mid * RULE_WIDTH + end


def centre(text: str, width: int = RULE_WIDTH + 2) -> str:

    text = (text or "").strip()
    if len(text) >= width:
        return text
    pad = (width - len(text)) // 2
    return "\u2007" * pad + text


def head(title: str, sub: str = "") -> str:
    name = esc(premiumize_label(title)).upper()
    out = f"{rule()}\n<b>{centre(name)}</b>\n{rule()}"
    if sub:
        out += f"\n\n<i>{esc(premiumize_label(sub))}</i>"
    return out


async def set_user_menu(bot, uid: int) -> None:


    url = app_url_for(uid)
    if not url:
        return
    with contextlib.suppress(TelegramError):
        await bot.set_chat_menu_button(
            chat_id=uid,
            menu_button=MenuButtonWebApp(text="Store", web_app=WebAppInfo(url=url)),
        )


def miniapp_button(label: str = "🛍 Open Store", uid: int = 0):
    url = app_url_for(uid) if uid else public_url()
    if not url:
        return None
    return ColorButton(pad(premiumize_label(label)), web_app=WebAppInfo(url=url),
                       style="success")


async def force_sub_missing(uid: int) -> list[sqlite3.Row]:

    if not _APP:
        return []
    missing = []
    for ch in q("SELECT * FROM channels WHERE required=1 AND active=1"):
        target = ch["chat_id"] or ch["handle"]
        if not target:
            continue
        try:
            m = await _APP.bot.get_chat_member(chat_id=target, user_id=uid)
            if m.status in ("left", "kicked"):
                missing.append(ch)
        except TelegramError:
            continue
    return missing


def force_sub_kb(missing: list[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows = []
    for ch in missing:
        handle = (ch["handle"] or "").lstrip("@")
        url = ch["handle"] if str(ch["handle"]).startswith("http") else f"https://t.me/{handle}"
        rows.append([BU(f"Join {ch['name']}", url, 22)])
    rows.append([B("✅ I Joined", "checksub", 22)])
    return InlineKeyboardMarkup(rows)


def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    rows = [[B("🛒 Browse Stock", "shop")]]
    mb = miniapp_button("🛍 Open Store", uid)
    if mb:
        rows.insert(0, [mb])
    rows.append([B("💳 Wallet", "wallet"), B("📦 My Orders", "orders")])
    third = []
    if feat("streak") or feat("refDash") or feat("loyalty") or feat("leaderboard"):
        third.append(B("🎁 Earn", "earn"))
    third.append(B("👤 Profile", "profile"))
    rows.append(third)
    if feat("cart"):
        rows.append([B("🧺 Cart", "cart"), B("🆘 Support", "support")])
    else:
        rows.append([B("🆘 Support", "support")])
    if feat("guide"):
        rows.append([B("📖 Guide", "guide:0")])
    gv = active_giveaway()
    if gv and not giveaway_claimed_by(gv["id"], uid):
        rows.append([ColorButton(pad("🎉 Giveaway live", BTN_W), f"gv:{gv['id']}", style="success")])
    if is_admin(uid):
        rows.append([B("👑 Admin Panel", "adm")])
    return InlineKeyboardMarkup(rows)


def home_text(uid: int) -> str:
    u = get_user(uid)
    store = sget("store_name")
    body = cget("welcome").replace("{store}", esc(store))
    lines = [head(store, sget("store_tagline")), "", body, ""]
    lines.append(f"💰 <b>Balance:</b> {money(u['balance'])}")
    if feat("udhar") and get_debt(uid):
        lines.append(f"🧾 <b>Udhar owed:</b> {money(get_debt(uid))}")
    if feat("loyalty"):
        lines.append(f"⭐ <b>Points:</b> {u['points']}")
    if feat("stockCounter"):
        n = q1("SELECT COUNT(*) c FROM stock WHERE sold=0")["c"]
        cn = len(countries())
        lines.append(f"📦 <b>In stock:</b> {n} accounts · {cn} countries")
    if flash_active():
        lines.append(f"🔥 <b>Flash sale:</b> −{int(sgetf('flash_percent', 20))}% right now")
    if sget("maintenance") == "1":
        lines.append("\n🛠 <i>Maintenance mode — buying is paused.</i>")
    return premiumize("\n".join(lines) + banner_line())


async def show_home(update: Update, uid: int, edit: bool = False) -> None:
    text, kb = home_text(uid), main_menu_kb(uid)
    if edit and update.callback_query:
        with contextlib.suppress(BadRequest):
            await update.callback_query.edit_message_text(
                text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        return
    target = update.effective_message
    await target.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if blacklisted("user", u.id):
        return
    row = q1("SELECT 1 FROM users WHERE user_id=?", (u.id,))
    get_user(u.id, u.username or "", u.first_name or "")
    if not row:
        args = ctx.args or []
        if args and args[0].startswith("ref_"):
            with contextlib.suppress(ValueError):
                inviter = int(args[0][4:])
                if inviter != u.id and q1("SELECT 1 FROM users WHERE user_id=?", (inviter,)):
                    ex("UPDATE users SET referred_by=? WHERE user_id=?", (inviter, u.id))
        await send_log(
            "join", f"👤 <b>New user</b>\n{esc(u.first_name)} @{esc(u.username or '-')} · <code>{u.id}</code>"
        )
        if feat("onboarding"):
            await update.effective_message.reply_text(
                head("How it works")
                + "\n\n1️⃣ Top up your wallet\n2️⃣ Pick a country and buy\n3️⃣ Read the login code right here",
                parse_mode=ParseMode.HTML,
            )
    with contextlib.suppress(Exception):
        await set_user_menu(ctx.bot, u.id)
    missing = await force_sub_missing(u.id)
    if missing:
        await update.effective_message.reply_text(
            head("Join to continue", "Membership is required to use the store."),
            reply_markup=force_sub_kb(missing),
            parse_mode=ParseMode.HTML,
        )
        return
    await show_home(update, u.id)


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    await update.effective_message.reply_text(
        head("Wallet") + f"\n\n💰 <b>{money(balance(uid))}</b>",
        reply_markup=wallet_kb(),
        parse_mode=ParseMode.HTML,
    )


def shop_kb(page: int = 0) -> InlineKeyboardMarkup:
    cs = countries()
    per, rows = 8, []
    chunk = cs[page * per: page * per + per]
    for c in chunk:
        star = ""
        if feat("reviews"):
            avg, n = country_rating(c["c"])
            if n:
                star = f" {avg}★"
        tail = f"{money(c['price'])}" if c["qty"] else "pre-order"
        rows.append([B(f"{c['f']} {c['c']} · {c['qty']} · {tail}{star}", f"buy:{c['c']}", 30)])
    nav = []
    if page > 0:
        nav.append(B("◀ Prev", f"shop:{page-1}", 10))
    if (page + 1) * per < len(cs):
        nav.append(B("Next ▶", f"shop:{page+1}", 10))
    if nav:
        rows.append(nav)
    rows.append([B("🏠 Home", "home")])
    return InlineKeyboardMarkup(rows)


async def view_shop(qy, page: int = 0) -> None:
    cs = countries()
    if not sell_hours_open():
        await qy.edit_message_text(
            head("Closed", f"We sell between {sget('sell_open')} and {sget('sell_close')}."),
            reply_markup=InlineKeyboardMarkup([[B("🏠 Home", "home")]]),
            parse_mode=ParseMode.HTML,
        )
        return
    if not cs:
        await qy.edit_message_text(
            head("Out of stock", "Nothing listed yet — check back shortly."),
            reply_markup=InlineKeyboardMarkup([[B("🏠 Home", "home")]]),
            parse_mode=ParseMode.HTML,
        )
        return
    total = sum(c["qty"] for c in cs)
    sub = f"{total} accounts ready · instant delivery"
    if flash_active():
        sub += f" · 🔥 −{int(sgetf('flash_percent', 20))}%"
    await qy.edit_message_text(
        head(cget("shopHead"), sub), reply_markup=shop_kb(page), parse_mode=ParseMode.HTML
    )


def buy_kb(country: str, qty: int, code: str = "") -> InlineKeyboardMarkup:
    rows = []
    if feat("cart") or feat("bulkDisc"):
        rows.append(
            [
                B("−", f"qty:{country}:{max(1,qty-1)}:{code}", 6),
                B(f"{qty}", "noop", 6),
                B("+", f"qty:{country}:{qty+1}:{code}", 6),
            ]
        )
    rows.append([B("✅ Confirm Buy", f"do:{country}:{qty}:{code}", 22)])
    if feat("cart"):
        rows.append([B("🧺 Add to Cart", f"addcart:{country}:{qty}", 22)])
    extra = []
    if feat("restockAlert") and not q1(
        "SELECT COUNT(*) c FROM stock WHERE country=? AND sold=0", (country,)
    )["c"]:
        rows.append([B("🔔 Alert me on restock", f"alert:{country}", 22)])
    if feat("coupons"):
        extra.append(B("🎟 Coupon", f"cpn:{country}:{qty}", 10))
    if feat("wishlist"):
        extra.append(B("♡ Save", f"wish:{country}", 10))
    if extra:
        rows.append(extra)
    rows.append([B("◀ Back", "shop", 10), B("🏠 Home", "home", 10)])
    return InlineKeyboardMarkup(rows)


def buy_text(uid: int, country: str, qy: int, code: str = "") -> str:
    qt = quote(country, qy, code)
    avail = q1("SELECT COUNT(*) c FROM stock WHERE country=? AND sold=0", (country,))["c"]
    lines = [head(f"{flag_for(country)} {country}"), ""]
    lines.append(f"📦 <b>Available:</b> {avail}" if avail
                 else "📦 <b>Out of stock</b> — tap the bell to be told when it returns")
    if feat("reviews"):
        avg, n = country_rating(country)
        if n:
            lines.append(f"⭐ <b>Rating:</b> {avg} / 5 ({n})")
    if feat("healthCheck"):
        lines.append("🛡 <b>Session checked</b> before delivery")
    lines.append("")
    lines.append(f"💵 <b>Unit price:</b> {money(qt['unit'])}")
    if qt["qty"] > 1:
        lines.append(f"🔢 <b>Quantity:</b> {qt['qty']}")
    if qt["bulk"]:
        lines.append(f"📉 <b>Bulk discount:</b> −{qt['bulk']:.0f}%")
    if qt["coupon"]:
        lines.append(f"🎟 <b>Coupon {esc(code)}:</b> −{qt['coupon']:.0f}%")
    lines.append(f"🧾 <b>Total:</b> {money(qt['total'])}")
    bal = balance(uid)
    lines.append(f"💰 <b>Your balance:</b> {money(bal)}")
    if bal < qt["total"]:
        short = qt["total"] - bal
        if feat("splitPay"):
            lines.append(f"\n<i>Short {money(short)} — you can pay the rest by UPI.</i>")
        else:
            lines.append(f"\n<i>Add {money(short)} to your wallet to buy this.</i>")
    return "\n".join(lines)


async def do_purchase(qy, uid: int, country: str, qty: int, code: str) -> None:
    if sget("maintenance") == "1":
        await qy.answer("Store is in maintenance mode", show_alert=True)
        return
    qt = quote(country, qty, code)
    bal = balance(uid)
    avail = q1("SELECT COUNT(*) c FROM stock WHERE country=? AND sold=0", (country,))["c"]

    if avail == 0:
        await qy.answer("Out of stock", show_alert=True)
        return

    if avail < qty:
        await qy.answer(f"Only {avail} left", show_alert=True)
        return
    if bal < qt["total"]:
        if feat("splitPay"):
            need = round(qt["total"] - bal, 2)
            await start_deposit(qy, uid, max(need, sgetf("min_deposit", 10)))
            return
        await qy.answer("Not enough balance — add funds", show_alert=True)
        return

    if feat("velocity"):
        recent = q1(
            "SELECT COUNT(*) c FROM orders WHERE user_id=? AND created_at > ?",
            (uid, (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()),
        )["c"]
        if recent >= int(sgetf("velocity_limit", 5)):
            await notify_admins(
                f"⚠️ <b>Velocity alert</b>\nUser <code>{uid}</code> bought {recent} in 10 min.",
                "security",
            )

    credit(uid, -qt["total"])
    if code and coupon_percent(code):
        ex("UPDATE coupons SET used=used+1 WHERE code=?", (code.strip().upper(),))

    delivered, lines = [], []
    for _ in range(qty):
        oid, row = deliver(uid, country, round(qt["total"] / qty, 2))
        if not oid:
            break
        delivered.append(oid)
        lines.append(f"<b>{oid}</b>\n<code>{esc(row['payload'] or 'session on file')}</code>")

    pay_referral(uid, qt["total"])
    body = [head("Delivered", f"{len(delivered)} × {country}"), ""] + lines
    if feat("warranty"):
        body.append(f"\n🛡 Problem inside {int(sgetf('warranty_minutes', 10))} min → free replacement.")
    if feat("receipts"):
        body.append(f"🧾 Receipt: {', '.join(delivered)} · {money(qt['total'])}")
    rows = [[B("📦 My Orders", "orders", 22)]]
    if feat("reviews") and delivered:
        rows.append([B("⭐ Rate this", f"rate:{country}", 22)])
    rows.append([B("🏠 Home", "home")])
    await qy.edit_message_text(
        "\n".join(body), reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML
    )
    await send_log(
        "purchase",
        f"🛒 <b>Purchase</b>\n{country} × {len(delivered)} · {money(qt['total'])}\n"
        f"User <code>{uid}</code>",
    )
    if feat("salesFeed"):
        await send_log("purchase", f"{flag_for(country)} <b>{country}</b> sold · {money(qt['unit'])}")
    if feat("lowStock"):
        left = q1("SELECT COUNT(*) c FROM stock WHERE country=? AND sold=0", (country,))["c"]
        if left <= int(sgetf("low_stock_default", 10)):
            await notify_admins(f"📉 <b>Low stock</b>\n{country}: {left} left", "stock")


def wallet_kb() -> InlineKeyboardMarkup:
    rows = [[B("➕ Add Funds", "dep")]]
    if feat("crypto"):
        rows.append([B("₿ Crypto Top-up", "depcrypto")])
    if feat("ledger"):
        rows.append([B("📄 Statement", "ledger")])
    rows.append([B("🏠 Home", "home")])
    return InlineKeyboardMarkup(rows)


def amounts_kb() -> InlineKeyboardMarkup:
    vals = [50, 100, 200, 500, 1000, 2000]
    rows, row = [], []
    for v in vals:
        row.append(B(f"{CUR}{v}", f"depamt:{v}", 8))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([B("✏️ Other amount", "depother", 22)])
    rows.append([B("◀ Back", "wallet", 10), B("🏠 Home", "home", 10)])
    return InlineKeyboardMarkup(rows)


async def start_deposit(qy, uid: int, amount: float) -> None:
    lo, hi = sgetf("min_deposit", 10), sgetf("max_deposit", 20000)
    if feat("depLimits") and not (lo <= amount <= hi):
        await qy.answer(f"Allowed {money(lo)} – {money(hi)}", show_alert=True)
        return
    if not sget("upi_id"):
        await qy.edit_message_text(
            head("Payments not set up", "An admin has not added a UPI ID yet."),
            reply_markup=InlineKeyboardMarkup([[B("🏠 Home", "home")]]),
            parse_mode=ParseMode.HTML,
        )
        return
    dep = create_deposit(uid, amount)
    bot = _APP.bot if _APP else None
    with contextlib.suppress(BadRequest, TelegramError):
        await qy.edit_message_text(
            premiumize(head("Payment request created", money(amount))),
            parse_mode=ParseMode.HTML)
    if bot is not None:
        try:
            await send_deposit_message(bot, uid, dep)
        except Exception as e:
            log.warning("deposit message failed: %s", e)


def deposit_kb(dep: sqlite3.Row) -> InlineKeyboardMarkup:
    rows = []
    if auto_check_on():
        rows.append([B("✅ " + cget("verifyBtn"), f"verify:{dep['id']}", 22)])
        rows.append([B("🧾 Send transaction no.", f"utr:{dep['id']}", 22)])
    else:
        rows.append([B("✅ I have paid", f"paid:{dep['id']}", 22)])
    rows.append([B("✖ Cancel", f"depcancel:{dep['id']}", 22)])
    return InlineKeyboardMarkup(rows)


def deposit_caption(dep: sqlite3.Row) -> str:

    mins = int(sgetf("pay_window", 5))
    left = ""
    with contextlib.suppress(Exception):
        secs = int((datetime.fromisoformat(dep["expires_at"])
                    - datetime.now(timezone.utc)).total_seconds())
        left = f"{max(0, secs) // 60}:{max(0, secs) % 60:02d}"
    bonus = deposit_bonus(dep["amount"])
    lines = [
        f"{rule()}\n<b>{esc(cget('sheetTitle')).upper()} {money(dep['amount'])}</b>\n{rule()}",
        "",
        f"🏦 <b>UPI ID</b>  <code>{esc(sget('upi_id'))}</code>",
        f"👤 <b>Payee</b>  {esc(sget('upi_name') or sget('store_name'))}",
        f"💵 <b>Pay exactly</b>  <code>{fmt_amt(dep['amount'])}</code>",
        f"🔖 <b>Note</b>  <code>{esc(dep['code'])}</code>",
        f"⏳ <b>Expires in</b>  {left or f'{mins}:00'}",
    ]
    if bonus:
        lines.append(f"🎁 <b>Bonus</b>  +{money(bonus)}")
    lines += [
        "",
        f"⚠️ Keep the note <b>{esc(dep['code'])}</b> on the payment — the QR fills it "
        "in for you.",
        (f"Then tap <b>{esc(cget('verifyBtn'))}</b>." if auto_check_on()
         else "Then tap <b>I have paid</b> and send the screenshot and UTR."),
    ]
    return premiumize("\n".join(lines))


async def send_deposit_message(bot, uid: int, dep: sqlite3.Row) -> bool:

    kb = deposit_kb(dep)
    caption = deposit_caption(dep)
    if feat("payQR"):
        buf = generate_qr(upi_link(dep["amount"], dep["code"]))
        if buf:
            buf.name = f"{dep['code']}.png"
            try:
                await bot.send_photo(chat_id=uid, photo=InputFile(buf), caption=caption,
                                     reply_markup=kb, parse_mode=ParseMode.HTML)
                return True
            except TelegramError as e:
                log.warning("deposit photo failed, falling back to text: %s", e)
    with contextlib.suppress(TelegramError):
        await bot.send_message(chat_id=uid, text=caption, reply_markup=kb,
                               parse_mode=ParseMode.HTML)
        return True
    return False


def deposit_text(dep: sqlite3.Row) -> str:
    mins = int(sgetf("pay_window", 5))
    left = ""
    with contextlib.suppress(Exception):
        secs = int(
            (datetime.fromisoformat(dep["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
        )
        left = f"{max(0, secs)//60}:{max(0, secs)%60:02d}"
    bonus = deposit_bonus(dep["amount"])
    lines = [
        head(f"{cget('sheetTitle')} {money(dep['amount'])}"),
        "",
        cget("sheetSub").replace("{m}", str(mins)),
        "",
        f"🏦 <b>UPI ID:</b> <code>{esc(sget('upi_id'))}</code>",
        f"👤 <b>Payee:</b> {esc(sget('upi_name') or sget('store_name'))}",
        f"💵 <b>Amount:</b> {money(dep['amount'])}",
        f"🔖 <b>Note / reference:</b> <code>{esc(dep['code'])}</code>",
        f"⏳ <b>Expires in:</b> {left or str(mins)+':00'}",
    ]
    if bonus:
        lines.append(f"🎁 <b>Bonus:</b> +{money(bonus)}")
    lines += [
        "",
        f"1️⃣ {cget('step1')}",
        f"2️⃣ {cget('step2')}",
        f"3️⃣ {cget('step3')}",
        f"4️⃣ {cget('step4')}",
    ]
    if feat("wrongAmt"):
        lines.append("\n<i>Paid a different amount? We credit exactly what arrived.</i>")
    return "\n".join(lines)


async def show_deposit(qy, dep_id: int) -> None:
    dep = q1("SELECT * FROM deposits WHERE id=?", (dep_id,))
    if not dep:
        await qy.answer("Request not found", show_alert=True)
        return
    await qy.edit_message_text(
        deposit_text(dep), reply_markup=deposit_kb(dep), parse_mode=ParseMode.HTML
    )


async def verify_deposit(qy, uid: int, dep_id: int) -> None:
    dep = q1("SELECT * FROM deposits WHERE id=?", (dep_id,))
    if not dep or dep["user_id"] != uid:
        await qy.answer("Request not found", show_alert=True)
        return
    if dep["status"] == "completed":
        await qy.answer("Already credited", show_alert=True)
        return
    if deposit_expired(dep):
        ex("UPDATE deposits SET status='expired' WHERE id=?", (dep_id,))
        await qy.edit_message_text(
            head("Expired", cget("msgExpired")),
            reply_markup=InlineKeyboardMarkup(
                [[B("🔄 New Request", "dep", 22)], [B("🏠 Home", "home")]]
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    await qy.answer(cget("msgChecking"))
    detail = await asyncio.to_thread(check_gmail_detailed, float(dep["amount"]),
                                     dep["code"], dep["created_at"] or "")
    utr = detail["utr"] if detail["found"] else None
    if not utr:
        reason_l = (detail.get("reason") or "").lower()
        cannot_check = (not sget("gmail_id")
                        or any(w in reason_l for w in ("switched off", "password",
                                                       "login", "not saved",
                                                       "could not reach")))
        if cannot_check:
            ex("UPDATE deposits SET status='review' WHERE id=?", (dep_id,))
            await notify_admins(
                f"💳 <b>Deposit needs review</b>\n{money(dep['amount'])} · "
                f"<code>{dep['code']}</code> · user <code>{uid}</code>\n"
                f"/approve_{dep['id']}",
                "payments",
            )
            await qy.edit_message_text(
                head("Sent for review", "An admin will confirm your payment shortly."),
                reply_markup=InlineKeyboardMarkup([[B("🏠 Home", "home")]]),
                parse_mode=ParseMode.HTML,
            )
            return
        with contextlib.suppress(BadRequest, TelegramError):
            await qy.answer(cget("msgNone"), show_alert=True)

        reason = (detail.get("reason") or "").lower()
        if any(w in reason for w in ("password", "login", "switched off",
                                     "not saved", "could not reach")):
            await notify_admins(
                f"⚠️ <b>Payment checking is not working</b>\n<i>{esc(detail['reason'])}</i>",
                "payments")
        return

    total = settle_deposit(dep, utr)
    await qy.edit_message_text(
        head("Payment verified", cget("msgOk"))
        + f"\n\n💰 <b>Credited:</b> {money(total)}\n"
        + f"🧾 <b>UTR:</b> <code>{esc(utr)}</code>\n"
        + f"💳 <b>New balance:</b> {money(balance(uid))}",
        reply_markup=InlineKeyboardMarkup([[B("🛒 Browse Stock", "shop", 22)], [B("🏠 Home", "home")]]),
        parse_mode=ParseMode.HTML,
    )
    await send_log(
        "deposit",
        f"✅ <b>Deposit verified</b>\n{money(dep['amount'])} · user <code>{uid}</code> · UTR {esc(utr)}",
    )


def orders_kb(uid: int) -> InlineKeyboardMarkup:
    rows = []
    for o in q(
        "SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 8", (uid,)
    ):
        rows.append([B(f"{flag_for(o['country'])} {o['id']} · {o['country']}", f"ord:{o['id']}", 26)])
    rows.append([B("🏠 Home", "home")])
    return InlineKeyboardMarkup(rows)


def order_detail_kb(oid: str) -> InlineKeyboardMarkup:
    rows = [[B("🔑 Get OTP", f"otp:{oid}", 22)]]
    second = []
    if feat("redownload"):
        second.append(B("📄 Details", f"redl:{oid}", 10))
    if feat("sessionFile"):
        second.append(B("📎 File", f"file:{oid}", 10))
    if second:
        rows.append(second)
    third = []
    if feat("warranty"):
        third.append(B("🛡 Replace", f"warr:{oid}", 10))
    if feat("dispute"):
        third.append(B("⚖️ Dispute", f"disp:{oid}", 10))
    if third:
        rows.append(third)
    rows.append([B("◀ Back", "orders", 10), B("🏠 Home", "home", 10)])
    return InlineKeyboardMarkup(rows)


def order_text(o: sqlite3.Row) -> str:
    lines = [head(o["id"], f"{flag_for(o['country'])} {o['country']}"), ""]
    lines.append(f"💵 <b>Paid:</b> {money(o['price'])}")
    lines.append(f"🕒 <b>When:</b> {esc((o['created_at'] or '')[:16].replace('T',' '))}")
    lines.append(f"📌 <b>Status:</b> {esc(o['status'].title())}")
    if feat("timeline"):
        lines += [
            "",
            "✅ Paid",
            "✅ Delivered",
            ("✅" if o["otp"] else "⬜") + " OTP received",
            ("✅" if o["closed_at"] else "⬜") + " Closed",
        ]
    st = q1("SELECT payload FROM stock WHERE id=?", (o["stock_id"],))
    if st and st["payload"]:
        lines += ["", f"<code>{esc(st['payload'])}</code>"]
    return "\n".join(lines)


def earn_kb(uid: int) -> InlineKeyboardMarkup:
    rows = []
    if feat("streak"):
        rows.append([B("🎁 Daily Check-in", "checkin")])
    if feat("refDash") or feat("refL2"):
        rows.append([B("👥 Invite & Earn", "ref")])
    if feat("loyalty"):
        rows.append([B("⭐ Redeem Points", "redeem")])
    if feat("leaderboard"):
        rows.append([B("🏆 Leaderboard", "lb")])
    rows.append([B("🏠 Home", "home")])
    return InlineKeyboardMarkup(rows)


def earn_text(uid: int) -> str:
    u = get_user(uid)
    lines = [head("Earn"), ""]
    if feat("streak"):
        lines.append(f"🔥 <b>Streak:</b> {u['streak']} day(s)")
        lines.append(f"🎁 <b>Daily reward:</b> {money(sgetf('daily_reward', 0.4))}+")
    if feat("loyalty"):
        worth = round(u["points"] / max(1, sgetf("points_per_rupee", 100)) * 10, 2)
        lines.append(f"⭐ <b>Points:</b> {u['points']} (≈ {money(worth)})")
    if feat("refDash") or feat("refL2"):
        n = q1("SELECT COUNT(*) c FROM users WHERE referred_by=?", (uid,))["c"]
        earned = q1(
            "SELECT COALESCE(SUM(earned),0) s FROM referrals WHERE inviter=?", (uid,)
        )["s"]
        lines.append(f"👥 <b>Invites:</b> {n} · earned {money(earned)}")
    return "\n".join(lines)


def profile_text(uid: int) -> str:
    u = get_user(uid)
    lines = [head("Profile"), ""]
    lines.append(f"🆔 <code>{uid}</code>")
    lines.append(f"💰 <b>Balance:</b> {money(u['balance'])}")
    lines.append(f"📦 <b>Orders:</b> {u['total_orders']}")
    lines.append(f"💸 <b>Spent:</b> {money(u['total_spent'])}")
    if feat("buyerBadge") and u["total_orders"] >= int(sgetf("buyer_badge_orders", 5)):
        lines.append("✅ <b>Verified buyer</b>")
    if feat("loyalty"):
        lines.append(f"⭐ <b>Points:</b> {u['points']}")
    if feat("udhar") and get_debt(uid):
        lines.append(f"🧾 <b>Udhar owed:</b> {money(get_debt(uid))}")
    if is_admin(uid):
        role = "Owner" if is_owner(uid) else (admin_row(uid)["role"] if admin_row(uid) else "")
        lines.append(f"👑 <b>Role:</b> {esc(role.title())}")
    return "\n".join(lines)


def profile_kb(uid: int) -> InlineKeyboardMarkup:
    rows = []
    if feat("wishlist"):
        rows.append([B("♡ Wishlist", "wl")])
    if feat("notifPrefs"):
        rows.append([B("🔔 Notifications", "notif")])
    if feat("i18n"):
        rows.append([B("🌐 Language", "lang")])
    if feat("guide"):
        rows.append([B("📖 Guide", "guide:0")])
    rows.append([B("📜 Terms", "terms"), B("🆘 Support", "support")])
    rows.append([B("🏠 Home", "home")])
    return InlineKeyboardMarkup(rows)


ADMIN_SECTIONS = [

    ("dash", "📊 Dashboard", None, None),
    ("stock", "📦 Stock", "stock", None),
    ("orders", "🧾 Orders", "orders", None),
    ("users", "👥 Users", "users", None),
    ("admins", "👑 Admins", "admins", "adminRoles"),
    ("channels", "📡 Channels", "channels", None),
    ("logs", "🗒 Logs", "logs", None),
    ("pay", "💳 Payments", "payments", None),
    ("features", "🎛 Features", "features", None),
    ("content", "✏️ Content", "content", "contentEditor"),
    ("market", "📣 Marketing", "broadcast", None),
    ("analytics", "📈 Analytics", "analytics", None),
    ("security", "🛡 Security", "security", None),
    ("extras", "🎁 Extras", "settings", None),
    ("settings", "⚙️ Settings", "settings", None),
]


def admin_kb(uid: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for key, label, perm, gate in ADMIN_SECTIONS:
        if perm and not can(uid, perm):
            continue
        if gate and not feat(gate):
            continue
        row.append(B(label, f"a:{key}", 16))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([B("🏠 Home", "home")])
    return InlineKeyboardMarkup(rows)


def admin_home_text(uid: int) -> str:
    users = q1("SELECT COUNT(*) c FROM users")["c"]
    stock = q1("SELECT COUNT(*) c FROM stock WHERE sold=0")["c"]
    today = datetime.now(timezone.utc).date().isoformat()
    rev = q1(
        "SELECT COALESCE(SUM(price),0) s FROM orders WHERE substr(created_at,1,10)=?", (today,)
    )["s"]
    ordc = q1(
        "SELECT COUNT(*) c FROM orders WHERE substr(created_at,1,10)=?", (today,)
    )["c"]
    role = "Owner" if is_owner(uid) else "Admin"
    lines = [
        head("Admin Console", f"{role} · {esc(sget('store_name'))}"),
        "",
        f"💰 <b>Revenue today:</b> {money(rev)}",
        f"🧾 <b>Orders today:</b> {ordc}",
        f"👥 <b>Users:</b> {users}",
        f"📦 <b>Stock:</b> {stock}",
    ]
    goal = rev_goal_line()
    if goal:
        lines.append(goal)
    pend = q1("SELECT COUNT(*) c FROM deposits WHERE status='review'")["c"]
    if pend:
        lines.append(f"⏳ <b>Deposits to review:</b> {pend}")
    if not sget("upi_id"):
        lines.append("\n⚠️ <i>No UPI ID set — top-ups are disabled.</i>")
    if not q("SELECT 1 FROM stock WHERE sold=0 LIMIT 1"):
        lines.append("⚠️ <i>No stock uploaded yet.</i>")
    return "\n".join(lines)


def features_kb(page: int = 0) -> InlineKeyboardMarkup:
    groups = []
    for g, *_ in FEATURES:
        if g not in groups:
            groups.append(g)
    g = groups[page % len(groups)]
    rows = []
    for grp, key, label, _desc in FEATURES:
        if grp != g:
            continue
        on = feat(key)
        rows.append([B(f"{'🟢' if on else '⚪️'} {label}", f"ft:{key}:{page}", 28)])
    nav = [
        B("◀", f"a:features:{(page-1) % len(groups)}", 8),
        B(f"{page+1}/{len(groups)}", "noop", 8),
        B("▶", f"a:features:{(page+1) % len(groups)}", 8),
    ]
    rows.append(nav)
    rows.append([B("🟢 All on", "ftall:1", 10), B("⚪️ All off", "ftall:0", 10)])
    rows.append([B("◀ Panel", "adm")])
    return InlineKeyboardMarkup(rows)


def features_text(page: int = 0) -> str:
    groups = []
    for g, *_ in FEATURES:
        if g not in groups:
            groups.append(g)
    g = groups[page % len(groups)]
    on = sum(1 for k in FEATURE_KEYS if feat(k))
    body = [head("Features", f"{on} of {len(FEATURE_KEYS)} enabled"), "", f"<b>{esc(g)}</b>", ""]
    for grp, key, label, desc in FEATURES:
        if grp == g:
            body.append(f"{'🟢' if feat(key) else '⚪️'} <b>{esc(label)}</b> — <i>{esc(desc)}</i>")
    body.append("\n<i>Off means invisible: no button, no menu, no API.</i>")
    return "\n".join(body)


def admins_kb(uid: int) -> InlineKeyboardMarkup:
    rows = []
    for a in q("SELECT * FROM admins ORDER BY role DESC, user_id"):
        tag = "👑" if a["role"] == "owner" else "🔸"
        name = a["label"] or str(a["user_id"])
        rows.append([B(f"{tag} {esc(name)} · {a['role']}", f"adu:{a['user_id']}", 26)])
    if is_owner(uid):
        rows.append([B("➕ Add Admin", "adnew", 22)])
    rows.append([B("◀ Panel", "adm")])
    return InlineKeyboardMarkup(rows)


def admin_perm_kb(target: int, by: int) -> InlineKeyboardMarkup:
    p = perms_of(target)
    rows = []
    if not is_owner(target):
        for key, label, _d in PERMS:
            if key == "admins" and not is_owner(by):
                continue
            rows.append([B(f"{'🟢' if p.get(key) else '⚪️'} {label}", f"pm:{target}:{key}", 26)])
        rows.append([B("✅ Grant all", f"pmall:{target}:1", 12), B("🚫 Revoke all", f"pmall:{target}:0", 12)])
        if is_owner(by):
            rows.append([B("🗑 Remove admin", f"admdel:{target}", 22)])
    rows.append([B("◀ Admins", "a:admins")])
    return InlineKeyboardMarkup(rows)


def admin_perm_text(target: int) -> str:
    a = admin_row(target)
    if not a:
        return head("Admin", "Not found")
    p = perms_of(target)
    lines = [
        head(a["label"] or str(target), f"role: {a['role']}"),
        "",
        f"🆔 <code>{target}</code>",
        "",
    ]
    if is_owner(target):
        lines.append("👑 <b>Owner</b> — every permission, cannot be limited.")
    else:
        for key, label, desc in PERMS:
            lines.append(f"{'🟢' if p.get(key) else '⚪️'} <b>{esc(label)}</b> — <i>{esc(desc)}</i>")
    return "\n".join(lines)


def stock_kb() -> InlineKeyboardMarkup:
    rows = [[B("➕ Add stock", "stadd", 22)]]
    if feat("manualLogin"):
        rows.append([B("📱 Login by number", "stlogin", 22)])
    if feat("bulkUpload"):
        rows.append([B("📥 Paste a list", "stpaste", 22)])
    for c in countries()[:8]:
        rows.append(
            [
                B(f"{c['f']} {c['c']} · {c['qty']}", f"stc:{c['c']}", 18),
                B(f"{money(c['price'])}", f"stprice:{c['c']}", 8),
            ]
        )
    if feat("sessionStats"):
        rows.append([B("🩺 Session health", "sstats", 22)])
    if feat("lowStock"):
        rows.append([B("📉 Low-stock thresholds", "stlow", 22)])
    if feat("dynPrice") or feat("bulkDisc"):
        rows.append([B("💰 Pricing rules", "stprules", 22)])
    rows.append([B("◀ Panel", "adm")])
    return InlineKeyboardMarkup(rows)


def stock_text() -> str:
    tot = q1("SELECT COUNT(*) c FROM stock WHERE sold=0")["c"]
    sold = q1("SELECT COUNT(*) c FROM stock WHERE sold=1")["c"]
    dead = q1("SELECT COUNT(*) c FROM stock WHERE sold=2")["c"]
    lines = [head("Stock", f"{tot} available · {sold} sold"), ""]
    if dead:
        lines.append(f"💀 <b>Failed health check:</b> {dead}")
    for c in countries():
        lines.append(f"{c['f']} <b>{esc(c['c'])}</b> — {c['qty']} × {money(c['price'])}")
    if not tot:
        lines.append("<i>Nothing uploaded yet. Tap Upload Stock.</i>")
    lines.append(
        "\n<i>Upload format — one per line:</i>\n<code>Country | +code | price | session</code>"
    )
    return "\n".join(lines)


def parse_stock_lines(text: str) -> tuple[list[dict], list[str]]:

    good, bad = [], []
    seen = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            bad.append(f"malformed: {line[:28]}")
            continue
        country = parts[0].title()
        code = parts[1] if len(parts) > 1 else ""
        try:
            price = float(parts[2]) if len(parts) > 2 and parts[2] else sgetf("default_price", 35)
        except ValueError:
            price = sgetf("default_price", 35)
        payload = parts[3] if len(parts) > 3 else ""
        key = payload or f"{country}|{code}|{len(good)}"
        if payload and key in seen:
            bad.append(f"duplicate in file: {country}")
            continue
        if payload and feat("dupDetect") and q1("SELECT 1 FROM stock WHERE payload=?", (payload,)):
            bad.append(f"already in database: {country}")
            continue
        seen.add(key)
        good.append({"country": country, "code": code, "price": price, "payload": payload})
    return good, bad


def insert_stock(rows: list[dict]) -> int:
    for r in rows:
        ex(
            "INSERT INTO stock(country,flag,code,price,payload,added_at) VALUES(?,?,?,?,?,?)",
            (r["country"], flag_for(r["country"]), r["code"], r["price"], r["payload"], now()),
        )
    return len(rows)


def pay_kb() -> InlineKeyboardMarkup:
    rows = [
        [B("🏦 UPI ID", "set:upi_id", 16), B("👤 Payee", "set:upi_name", 16)],
        [B("📧 Gmail ID", "set:gmail_id", 16), B("🔑 Gmail Password", "set:gmail_pass", 16)],
        [B("⏱ Payment window", "set:pay_window", 16), B("🔖 Prefix", "set:pay_prefix", 16)],
    ]
    if feat("depLimits"):
        rows.append([B("⬇️ Min deposit", "set:min_deposit", 16), B("⬆️ Max deposit", "set:max_deposit", 16)])
    if feat("depBonus"):
        rows.append([B("🎁 Bonus tiers", "paybonus", 22)])
    if feat("autoDeposit"):
        rows.append([B("🤖 Auto-approve under", "set:autodep_max", 22)])
    if feat("crypto"):
        rows.append([B("₿ Crypto address", "set:crypto_address", 22)])
    if feat("upiRotate"):
        rows.append([B("🔁 Extra UPI handles", "set:upi_extra", 22)])
    rows.append([B("⏳ Pending reviews", "paypend", 22)])
    rows.append([B("◀ Panel", "adm")])
    return InlineKeyboardMarkup(rows)


def pay_text() -> str:
    gm = sget("gmail_id")
    lines = [
        head("Payments", "Everything here is stored in the database"),
        "",
        f"🏦 <b>UPI ID:</b> {esc(sget('upi_id') or '— not set —')}",
        f"👤 <b>Payee:</b> {esc(sget('upi_name') or '— not set —')}",
        f"📧 <b>Gmail:</b> {esc(gm or '— not set —')}",
        f"🔑 <b>Gmail password:</b> {'•' * 10 if sget('gmail_pass') else '— not set —'}",
        f"⏱ <b>Payment window:</b> {esc(sget('pay_window'))} min",
        f"🔖 <b>Reference prefix:</b> {esc(sget('pay_prefix'))}",
    ]
    if feat("depLimits"):
        lines.append(f"📏 <b>Limits:</b> {money(sgetf('min_deposit'))} – {money(sgetf('max_deposit'))}")
    if not gm:
        lines.append(
            "\n<i>Without Gmail, verified payments go to a manual review queue instead.</i>"
        )
    else:
        lines.append("\n<i>Tip: with 2-step verification on, use a Google App Password.</i>")
    return "\n".join(lines)


def market_kb() -> InlineKeyboardMarkup:
    rows = [[B("📢 Broadcast now", "bc", 22)]]
    if feat("schedBroadcast"):
        rows.append([B("🗓 Scheduled broadcast", "bcsched", 22)])
    if feat("coupons"):
        rows.append([B("🎟 Coupons", "cpns", 22)])
    if feat("flashSale"):
        rows.append([B("🔥 Flash sale", "flash", 22)])
    if feat("loyalty"):
        rows.append([B("⭐ Loyalty rates", "set:points_per_10", 22)])
    if feat("refDash") or feat("refL2"):
        rows.append([B("👥 Referral rates", "refrates", 22)])
    if feat("leaderboard"):
        rows.append([B("🏆 Leaderboard prize", "set:leaderboard_prize", 22)])
    if feat("winback"):
        rows.append([B("💌 Win-back offer", "winback", 22)])
    rows.append([B("◀ Panel", "adm")])
    return InlineKeyboardMarkup(rows)


def analytics_text() -> str:
    today = datetime.now(timezone.utc).date()
    lines = [head("Analytics"), ""]
    if feat("revChart"):
        lines.append("<b>Revenue · last 7 days</b>")
        peak = 0.0
        days = []
        for i in range(6, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            v = q1(
                "SELECT COALESCE(SUM(price),0) s FROM orders WHERE substr(created_at,1,10)=?", (d,)
            )["s"]
            days.append((d[5:], float(v)))
            peak = max(peak, float(v))
        for d, v in days:
            bars = "█" * max(0, int((v / peak) * 12)) if peak else ""
            lines.append(f"<code>{d}</code> {bars} {money(v)}")
        lines.append("")
    if feat("funnel"):
        users = q1("SELECT COUNT(*) c FROM users")["c"] or 1
        dep = q1("SELECT COUNT(DISTINCT user_id) c FROM deposits WHERE status='completed'")["c"]
        buy = q1("SELECT COUNT(DISTINCT user_id) c FROM orders")["c"]
        lines += [
            "<b>Funnel</b>",
            f"Joined <b>{users}</b> → Deposited <b>{dep}</b> ({dep*100//users}%) "
            f"→ Bought <b>{buy}</b> ({buy*100//users}%)",
            "",
        ]
    if feat("profitRep"):
        lines.append("<b>Per-country</b>")
        for r in q(
            "SELECT country, COUNT(*) n, COALESCE(SUM(price),0) s FROM orders "
            "GROUP BY country ORDER BY s DESC LIMIT 6"
        ):
            lines.append(f"{flag_for(r['country'])} {esc(r['country'])} — {r['n']} × = {money(r['s'])}")
        lines.append("")
    if feat("clv"):
        lines.append("<b>Top customers</b>")
        for r in leaderboard(5):
            lines.append(f"@{esc(r['username'] or r['user_id'])} — {money(r['total_spent'])}")
    if len(lines) <= 2:
        lines.append("<i>All analytics features are switched off.</i>")
    return "\n".join(lines)


def security_kb() -> InlineKeyboardMarkup:
    rows = []
    if feat("rateLimit"):
        rows.append([B("🚦 Rate limit", "set:rate_limit", 22)])
    if feat("velocity"):
        rows.append([B("⚡ Velocity limit", "set:velocity_limit", 22)])
    if feat("blacklist"):
        rows.append([B("⛔ Blacklist", "bl", 22)])
    if feat("multiAcc"):
        rows.append([B("🕵️ Flagged accounts", "flagged", 22)])
    if feat("auditLog"):
        rows.append([B("📜 Audit log", "audit", 22)])
    rows.append([B("◀ Panel", "adm")])
    return InlineKeyboardMarkup(rows)


def security_text() -> str:
    lines = [head("Security"), ""]
    if feat("rateLimit"):
        lines.append(f"🚦 <b>Rate limit:</b> {int(sgetf('rate_limit', 20))} actions / minute")
    if feat("velocity"):
        lines.append(f"⚡ <b>Velocity alert:</b> above {int(sgetf('velocity_limit', 5))} buys / 10 min")
    if feat("blacklist"):
        n = q1("SELECT COUNT(*) c FROM blacklist")["c"]
        lines.append(f"⛔ <b>Blacklisted:</b> {n}")
    if feat("encrypt"):
        lines.append("🔐 <b>Sessions encrypted at rest:</b> on")
    banned = q1("SELECT COUNT(*) c FROM users WHERE banned=1")["c"]
    lines.append(f"🚫 <b>Banned users:</b> {banned}")
    if len(lines) <= 2:
        lines.append("<i>All security features are switched off.</i>")
    return "\n".join(lines)


def settings_kb() -> InlineKeyboardMarkup:
    rows = [
        [B("🏪 Store name", "set:store_name", 16), B("💬 Tagline", "set:store_tagline", 16)],
        [B("🆘 Support handle", "set:support", 16), B("💵 Default price", "set:default_price", 16)],
        [B("🎁 Daily reward", "set:daily_reward", 16), B("🌐 Mini app URL", "set:miniapp_url", 16)],
        [B("🛠 Maintenance", "tg:maintenance", 16), B("🧪 Sandbox", "tg:sandbox", 16)],
    ]
    if feat("autoBackup"):
        rows.append([B("💾 Backup now", "backup", 22)])
    if feat("slashCmds"):
        rows.append([B("⌨️ Sync commands", "synccmd", 22)])
    if feat("revGoal"):
        rows.append([B("🎯 Revenue goal", "set:rev_goal", 22)])
    rows.append([B("📤 Export CSV", "export", 22)])
    rows.append([B("◀ Panel", "adm")])
    return InlineKeyboardMarkup(rows)


def settings_text() -> str:
    return "\n".join(
        [
            head("Settings"),
            "",
            f"🏪 <b>Store:</b> {esc(sget('store_name'))}",
            f"💬 <b>Tagline:</b> {esc(sget('store_tagline'))}",
            f"🆘 <b>Support:</b> {esc(sget('support') or '— not set —')}",
            f"💵 <b>Default price:</b> {money(sgetf('default_price'))}",
            f"🎁 <b>Daily reward:</b> {money(sgetf('daily_reward'))}",
            f"🌐 <b>Mini app URL:</b> {esc(sget('miniapp_url') or PUBLIC_URL or '— not set —')}",
            f"🛠 <b>Maintenance:</b> {'ON' if sget('maintenance')=='1' else 'off'}",
            f"🧪 <b>Sandbox:</b> {'ON' if sget('sandbox')=='1' else 'off'}",
        ]
    )


def channels_kb() -> InlineKeyboardMarkup:
    rows = []
    for c in q("SELECT * FROM channels ORDER BY id"):
        mark = "🔒" if c["required"] else "🔓"
        state = "🟢" if c["active"] else "⚪️"
        rows.append(
            [
                B(f"{state}{mark} {c['name']}", f"chtg:{c['id']}", 20),
                B("🗑", f"chdel:{c['id']}", 6),
            ]
        )
    rows.append([B("➕ Add Channel", "chadd", 22)])
    rows.append([B("◀ Panel", "adm")])
    return InlineKeyboardMarkup(rows)


def logs_kb() -> InlineKeyboardMarkup:
    master = sget("logs_master", "1") == "1"
    rows = [[B(f"{'🟢' if master else '⚪️'} Logging master switch", "lgmaster", 26)]]
    for c in q("SELECT * FROM logchannels ORDER BY id"):
        tag = "⭐" if c["is_primary"] else "▫️"
        state = "🟢" if c["active"] else "⚪️"
        rows.append(
            [
                B(f"{state}{tag} {c['name']}", f"lgpri:{c['id']}", 20),
                B("🗑", f"lgdel:{c['id']}", 6),
            ]
        )
    rows.append([B("➕ Add Log Channel", "lgadd", 22)])
    for key, label in LOG_EVENTS:
        ev = q1("SELECT * FROM logevents WHERE key=?", (key,))
        on = ev and ev["on_"]
        rows.append([B(f"{'🟢' if on else '⚪️'} {label}", f"lgev:{key}", 26)])
    rows.append([B("◀ Panel", "adm")])
    return InlineKeyboardMarkup(rows)


def content_kb(page: int = 0) -> InlineKeyboardMarkup:
    keys = list(DEFAULT_CONTENT.keys())
    per = 8
    chunk = keys[page * per: page * per + per]
    rows = []
    for k in chunk:
        val = cget(k)
        preview = re.sub(r"<[^>]+>", "", val)[:22]
        rows.append([B(f"{k} · {preview}", f"ced:{k}", 30)])
    nav = []
    if page > 0:
        nav.append(B("◀", f"a:content:{page-1}", 8))
    nav.append(B(f"{page+1}/{(len(keys)-1)//per+1}", "noop", 8))
    if (page + 1) * per < len(keys):
        nav.append(B("▶", f"a:content:{page+1}", 8))
    rows.append(nav)
    rows.append([B("◀ Panel", "adm")])
    return InlineKeyboardMarkup(rows)


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    qy = update.callback_query
    uid = qy.from_user.id
    data = qy.data or ""
    get_user(uid, qy.from_user.username or "", qy.from_user.first_name or "")

    if q1("SELECT banned FROM users WHERE user_id=?", (uid,))["banned"]:
        await qy.answer("Your account is blocked.", show_alert=True)
        return
    if not rate_ok(uid):
        await qy.answer("Slow down a little.", show_alert=True)
        return
    with contextlib.suppress(TelegramError):
        await qy.answer()

    async def edit(text: str, kb: InlineKeyboardMarkup | None = None) -> None:

        try:
            await qy.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
            return
        except BadRequest as e:
            if "no text in the message" not in str(e).lower():
                return
        with contextlib.suppress(BadRequest, TelegramError):
            await qy.edit_message_caption(caption=text[:1024], reply_markup=kb,
                                          parse_mode=ParseMode.HTML)


    if data == "noop":
        return
    if data == "home":
        await edit(home_text(uid), main_menu_kb(uid))
        return
    if data == "checksub":
        missing = await force_sub_missing(uid)
        if missing:
            await qy.answer("Still not joined.", show_alert=True)
            return
        await edit(home_text(uid), main_menu_kb(uid))
        return
    if data.startswith("guide:"):
        pg = int(data.split(":")[1])
        await edit(premiumize(guide_text(pg)), guide_kb(pg))
        return
    if data.startswith("gv:"):
        gv = q1("SELECT * FROM giveaways WHERE id=?", (int(data.split(":")[1]),))
        if not gv:
            await qy.answer("That giveaway is over.", show_alert=True)
            return
        await edit(premiumize(giveaway_text(gv)), giveaway_kb(gv))
        return
    if data.startswith("gvclaim:"):
        await claim_giveaway(qy, uid, int(data.split(":")[1]))
        return
    if data.startswith("alert:"):
        c = data.split(":", 1)[1]
        on = alert_subscribe(uid, c)
        await qy.answer(cget("alertOn") if on else cget("alertOff"), show_alert=True)
        await edit(buy_text(uid, c, 1), buy_kb(c, 1))
        return
    if data == "udhar":
        owed = get_debt(uid)
        await edit(
            head(cget("udharTitle"), cget("udharBody")) + f"\n\n🧾 <b>{money(owed)}</b>",
            InlineKeyboardMarkup([[B("➕ Add Funds", "dep", 22)], [B("🏠 Home", "home")]]),
        )
        return
    if data == "terms":
        await edit(head("Terms") + "\n\n" + cget("terms"), InlineKeyboardMarkup([[B("🏠 Home", "home")]]))
        return
    if data == "support":
        ctx.user_data["await"] = ("support", None)
        await edit(
            head("Support") + "\n\n" + cget("support_text"),
            InlineKeyboardMarkup([[B("🏠 Home", "home")]]),
        )
        return


    if data == "shop" or data.startswith("shop:"):
        page = int(data.split(":")[1]) if ":" in data else 0
        await view_shop(qy, page)
        return
    if data.startswith("buy:"):
        c = data.split(":", 1)[1]
        await edit(buy_text(uid, c, 1), buy_kb(c, 1))
        return
    if data.startswith("qty:"):
        _, c, n, *rest = data.split(":")
        code = rest[0] if rest else ""
        n = max(1, min(25, int(n)))
        await edit(buy_text(uid, c, n, code), buy_kb(c, n, code))
        return
    if data.startswith("do:"):
        _, c, n, *rest = data.split(":")
        await do_purchase(qy, uid, c, int(n), rest[0] if rest else "")
        return
    if data.startswith("cpn:"):
        _, c, n = data.split(":")
        ctx.user_data["await"] = ("coupon", (c, int(n)))
        await edit(head("Coupon", "Send the code as a message."), InlineKeyboardMarkup([[B("◀ Back", f"buy:{c}")]]))
        return
    if data.startswith("wish:"):
        c = data.split(":", 1)[1]
        if q1("SELECT 1 FROM wishlist WHERE user_id=? AND country=?", (uid, c)):
            ex("DELETE FROM wishlist WHERE user_id=? AND country=?", (uid, c))
            await qy.answer("Removed from wishlist")
        else:
            ex("INSERT OR IGNORE INTO wishlist(user_id,country) VALUES(?,?)", (uid, c))
            await qy.answer("Saved to wishlist")
        return
    if data.startswith("addcart:"):
        _, c, n = data.split(":")
        ex("INSERT INTO cart(user_id,country,qty) VALUES(?,?,?)", (uid, c, int(n)))
        await qy.answer(f"{n} × {c} added to cart")
        return
    if data == "cart":
        rows = q("SELECT * FROM cart WHERE user_id=?", (uid,))
        if not rows:
            await edit(head("Cart", "Your cart is empty."), InlineKeyboardMarkup([[B("🛒 Browse", "shop", 22)], [B("🏠 Home", "home")]]))
            return
        total, lines = 0.0, []
        for r in rows:
            qt = quote(r["country"], r["qty"])
            total += qt["total"]
            lines.append(f"{flag_for(r['country'])} {esc(r['country'])} × {r['qty']} — {money(qt['total'])}")
        kb = [[B("✅ Checkout", "cartbuy", 22)], [B("🗑 Empty cart", "cartclr", 22)], [B("🏠 Home", "home")]]
        await edit(head("Cart", f"Total {money(total)}") + "\n\n" + "\n".join(lines), InlineKeyboardMarkup(kb))
        return
    if data == "cartclr":
        ex("DELETE FROM cart WHERE user_id=?", (uid,))
        await edit(head("Cart", "Emptied."), InlineKeyboardMarkup([[B("🏠 Home", "home")]]))
        return
    if data == "cartbuy":
        rows = q("SELECT * FROM cart WHERE user_id=?", (uid,))
        total = sum(quote(r["country"], r["qty"])["total"] for r in rows)
        if balance(uid) < total:
            await qy.answer("Not enough balance", show_alert=True)
            return
        credit(uid, -total)
        out = []
        for r in rows:
            for _ in range(r["qty"]):
                oid, st = deliver(uid, r["country"], quote(r["country"], 1)["total"])
                if oid:
                    out.append(f"<b>{oid}</b> · {esc(r['country'])}")
        ex("DELETE FROM cart WHERE user_id=?", (uid,))
        pay_referral(uid, total)
        await edit(head("Delivered", f"{len(out)} accounts") + "\n\n" + "\n".join(out),
                   InlineKeyboardMarkup([[B("📦 My Orders", "orders", 22)], [B("🏠 Home", "home")]]))
        await send_log("purchase", f"🧺 <b>Cart checkout</b> · {money(total)} · user <code>{uid}</code>")
        return


    if data == "wallet":
        u = get_user(uid)
        lines = [head(cget("walKick"), cget("walSub")), "", f"💰 <b>{money(u['balance'])}</b>"]
        if feat("depBonus"):
            lines.append(f"\n🎁 Bonus: +{int(sgetf('bonus_500',5))}% from {money(500)}")
        await edit("\n".join(lines), wallet_kb())
        return
    if data == "dep":
        await edit(head("Add Funds", "Pick an amount"), amounts_kb())
        return
    if data == "depother":
        ctx.user_data["await"] = ("depamt", None)
        await edit(head("Add Funds", "Send the amount as a number."), InlineKeyboardMarkup([[B("◀ Back", "dep")]]))
        return
    if data.startswith("depamt:"):
        await start_deposit(qy, uid, float(data.split(":")[1]))
        return
    if data == "depcrypto":
        addr = sget("crypto_address")
        await edit(
            head("Crypto top-up", "USDT-TRC20, credited after 1 confirmation")
            + f"\n\n<code>{esc(addr or 'not configured')}</code>",
            InlineKeyboardMarkup([[B("◀ Back", "wallet", 22)]]),
        )
        return
    if data.startswith("pay:"):
        dep = q1("SELECT * FROM deposits WHERE id=? AND user_id=?",
                 (int(data.split(":")[1]), uid))
        if not dep:
            await qy.answer("Request not found", show_alert=True)
            return
        link = upi_link(dep["amount"], dep["code"])
        bot = ctx.bot
        sent_qr = False
        with contextlib.suppress(Exception):
            sent_qr = await send_payment_qr(bot, uid, dep)
        body = [
            head("Pay " + money(dep["amount"]), "Any UPI app"),
            "",
            f"🏦 <b>UPI ID:</b> <code>{esc(sget('upi_id'))}</code>",
            f"👤 <b>Payee:</b> {esc(sget('upi_name') or sget('store_name'))}",
            f"💵 <b>Amount:</b> <code>{dep['amount']:.2f}</code>",
            f"🔖 <b>Note:</b> <code>{esc(dep['code'])}</code>",
        ]
        if sent_qr:
            body.append("\n📷 <i>Scan the QR just sent, or copy the UPI ID above.</i>")
        else:
            body.append("\n<i>Copy the UPI ID above into any payment app.</i>")
        if link:
            body.append(f"\n🔗 <b>Direct link</b>\n{esc(link)}")
        with contextlib.suppress(TelegramError):
            await bot.send_message(chat_id=uid, text=premiumize("\n".join(body)),
                                   parse_mode=ParseMode.HTML)
        await qy.answer("Payment details sent")
        return
    if data.startswith("paid:"):
        dep = q1("SELECT * FROM deposits WHERE id=? AND user_id=?",
                 (int(data.split(":")[1]), uid))
        if not dep:
            await qy.answer("Request not found", show_alert=True)
            return
        if dep["status"] == "completed":
            await qy.answer("Already credited", show_alert=True)
            return
        ex("UPDATE deposits SET status='waiting_screenshot' WHERE id=?", (dep["id"],))
        ctx.user_data["await"] = ("dep_shot", dep["id"])
        await ctx.bot.send_message(
            chat_id=uid,
            text=premiumize(head("Send the screenshot", f"For {money(dep['amount'])}")
                            + "\n\nSend a photo of the successful payment.\n"
                            "<i>Send it as a photo, not a file.</i>"),
            reply_markup=InlineKeyboardMarkup([[B("✖ Cancel", f"depcancel:{dep['id']}", 22)]]),
            parse_mode=ParseMode.HTML)
        await qy.answer("Send the screenshot")
        return
    if data.startswith("verify:"):
        await verify_deposit(qy, uid, int(data.split(":")[1]))
        return
    if data.startswith("utr:"):
        ctx.user_data["await"] = ("utr", int(data.split(":")[1]))
        await edit(head("Submit UTR", "Send the 12-digit UTR from your payment app."),
                   InlineKeyboardMarkup([[B("◀ Back", "wallet", 22)]]))
        return
    if data.startswith("depcancel:"):
        ex("UPDATE deposits SET status='cancelled' WHERE id=? AND user_id=?", (int(data.split(":")[1]), uid))
        await edit(head("Cancelled", "Payment request closed."), InlineKeyboardMarkup([[B("🏠 Home", "home")]]))
        return
    if data == "ledger":
        lines = [head("Statement"), ""]
        run = 0.0
        for d in q("SELECT * FROM deposits WHERE user_id=? AND status='completed' ORDER BY id DESC LIMIT 10", (uid,)):
            run += float(d["amount"])
            lines.append(f"➕ {money(d['amount'])} · deposit · {esc((d['done_at'] or '')[:10])}")
        for o in q("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (uid,)):
            lines.append(f"➖ {money(o['price'])} · {esc(o['country'])} · {esc((o['created_at'] or '')[:10])}")
        lines.append(f"\n💰 <b>Balance:</b> {money(balance(uid))}")
        await edit("\n".join(lines), InlineKeyboardMarkup([[B("◀ Back", "wallet", 22)]]))
        return


    if data == "orders":
        rows = q("SELECT COUNT(*) c FROM orders WHERE user_id=?", (uid,))[0]["c"]
        await edit(head("My Orders", f"{rows} total"), orders_kb(uid))
        return
    if data.startswith("ord:"):
        o = q1("SELECT * FROM orders WHERE id=? AND user_id=?", (data.split(":")[1], uid))
        if not o:
            await qy.answer("Not found", show_alert=True)
            return
        await edit(order_text(o), order_detail_kb(o["id"]))
        return
    if data.startswith("otp:"):
        await deliver_otp(qy, uid, data.split(":")[1])
        return
    if data.startswith("redl:"):
        o = q1("SELECT * FROM orders WHERE id=? AND user_id=?", (data.split(":")[1], uid))
        st = q1("SELECT payload FROM stock WHERE id=?", (o["stock_id"],)) if o else None
        await edit(head("Order details") + f"\n\n<code>{esc(st['payload'] if st else '—')}</code>",
                   InlineKeyboardMarkup([[B("◀ Back", f"ord:{o['id']}", 22)]]))
        return
    if data.startswith("file:"):
        o = q1("SELECT * FROM orders WHERE id=? AND user_id=?", (data.split(":")[1], uid))
        st = q1("SELECT payload FROM stock WHERE id=?", (o["stock_id"],)) if o else None
        if st and st["payload"]:
            bio = io.BytesIO(st["payload"].encode())
            bio.name = f"{o['id']}.session.txt"
            await ctx.bot.send_document(chat_id=uid, document=InputFile(bio), caption="Delete this after use.")
            await qy.answer("File sent")
        else:
            await qy.answer("Nothing stored for this order", show_alert=True)
        return
    if data.startswith("warr:"):
        oid = data.split(":")[1]
        if q1("SELECT 1 FROM claims WHERE order_id=?", (oid,)):
            await qy.answer("A claim for this order is already with an admin.",
                            show_alert=True)
            return
        ctx.user_data["await"] = ("claim", oid)
        await edit(head("What went wrong?",
                        "Describe the problem. An admin will look at it.")
                   + "\n\n<i>Nothing is refunded automatically.</i>",
                   InlineKeyboardMarkup([[B("◀ Back", f"ord:{oid}", 22)]]))
        return
    if data.startswith("__old_warr:"):
        oid = data.split(":")[1]
        o = q1("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, uid))
        if not o:
            return
        mins = int(sgetf("warranty_minutes", 10))
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(o["created_at"])).total_seconds() / 60
        except (TypeError, ValueError):
            age = 9999
        if age > mins:
            await edit(head("Outside warranty", f"Replacements are free within {mins} minutes."),
                       InlineKeyboardMarkup([[B("⚖️ Open dispute", f"disp:{oid}", 22)], [B("🏠 Home", "home")]]))
            return
        oid2, row = deliver(uid, o["country"], 0)
        if not oid2:
            credit(uid, float(o["price"]))
            await edit(head("Refunded", "No replacement in stock, so we refunded your wallet."),
                       InlineKeyboardMarkup([[B("🏠 Home", "home")]]))
            return
        await edit(head("Replaced", f"New order {oid2}") + f"\n\n<code>{esc(row['payload'])}</code>",
                   InlineKeyboardMarkup([[B("📦 My Orders", "orders", 22)]]))
        return
    if data.startswith("disp:"):
        ctx.user_data["await"] = ("dispute", data.split(":")[1])
        await edit(head("Dispute", "Describe what went wrong in one message."),
                   InlineKeyboardMarkup([[B("◀ Back", "orders", 22)]]))
        return
    if data.startswith("rate:"):
        c = data.split(":", 1)[1]
        kb = [[B(f"{n}★", f"rated:{c}:{n}", 6) for n in (1, 2, 3, 4, 5)], [B("🏠 Home", "home")]]
        await edit(head("Rate", esc(c)), InlineKeyboardMarkup(kb))
        return
    if data.startswith("rated:"):
        _, c, n = data.split(":")
        ex("INSERT INTO reviews(user_id,country,rating,created_at) VALUES(?,?,?,?)", (uid, c, int(n), now()))
        await edit(head("Thanks", f"You rated {esc(c)} {n}★"), InlineKeyboardMarkup([[B("🏠 Home", "home")]]))
        return


    if data == "earn":
        await edit(earn_text(uid), earn_kb(uid))
        return
    if data == "checkin":
        ok, amount, streak = claim_checkin(uid)
        if not ok:
            await qy.answer("Already claimed today", show_alert=True)
            return
        await edit(head("Claimed", f"Day {streak} streak") + f"\n\n🎁 <b>{money(amount)}</b> added.",
                   InlineKeyboardMarkup([[B("◀ Earn", "earn", 22)]]))
        return
    if data == "ref":
        me = await ctx.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref_{uid}"
        n = q1("SELECT COUNT(*) c FROM users WHERE referred_by=?", (uid,))["c"]
        earned = q1("SELECT COALESCE(SUM(earned),0) s FROM referrals WHERE inviter=?", (uid,))["s"]
        lines = [head("Invite & Earn", f"{int(sgetf('ref_l1',5))}% of every deposit"), "",
                 f"👥 <b>Invites:</b> {n}", f"💰 <b>Earned:</b> {money(earned)}", "", f"<code>{link}</code>"]
        if feat("refL2"):
            lines.insert(4, f"🔁 <b>Level 2:</b> {int(sgetf('ref_l2',1))}%")
        await edit("\n".join(lines), InlineKeyboardMarkup([[B("◀ Earn", "earn", 22)]]))
        return
    if data == "redeem":
        u = get_user(uid)
        per = sgetf("points_per_rupee", 100)
        worth = round(u["points"] / max(1, per) * 10, 2)
        if worth < 1:
            await qy.answer("Not enough points yet", show_alert=True)
            return
        credit(uid, worth)
        ex("UPDATE users SET points=0 WHERE user_id=?", (uid,))
        await edit(head("Redeemed", f"{u['points']} points → {money(worth)}"),
                   InlineKeyboardMarkup([[B("◀ Earn", "earn", 22)]]))
        return
    if data == "lb":
        lines = [head("Leaderboard", f"Weekly prize {money(sgetf('leaderboard_prize',500))}"), ""]
        for i, r in enumerate(leaderboard(10), 1):
            lines.append(f"{i}. @{esc(r['username'] or r['user_id'])} — {money(r['total_spent'])}")
        if len(lines) == 2:
            lines.append("<i>No purchases yet.</i>")
        await edit("\n".join(lines), InlineKeyboardMarkup([[B("◀ Earn", "earn", 22)]]))
        return
    if data == "profile":
        await edit(profile_text(uid), profile_kb(uid))
        return
    if data == "wl":
        rows = q("SELECT country FROM wishlist WHERE user_id=?", (uid,))
        kb = [[B(f"{flag_for(r['country'])} {r['country']}", f"buy:{r['country']}", 22)] for r in rows]
        kb.append([B("◀ Profile", "profile", 22)])
        await edit(head("Wishlist", f"{len(rows)} saved"), InlineKeyboardMarkup(kb))
        return
    if data == "notif":
        await edit(head("Notifications", "Choose what you want to receive."),
                   InlineKeyboardMarkup([[B("🔔 All on", "home", 22)], [B("◀ Profile", "profile", 22)]]))
        return
    if data == "lang":
        await edit(head("Language"), InlineKeyboardMarkup(
            [[B("English", "setlang:en", 12), B("हिन्दी", "setlang:hi", 12)], [B("◀ Profile", "profile", 22)]]))
        return
    if data.startswith("setlang:"):
        ex("UPDATE users SET lang=? WHERE user_id=?", (data.split(":")[1], uid))
        await edit(profile_text(uid), profile_kb(uid))
        return


    if data == "adm" or data.startswith("a:"):
        if not is_admin(uid):
            await qy.answer("Admins only", show_alert=True)
            return
        await admin_route(qy, ctx, uid, data, edit)
        return
    if is_admin(uid):
        await admin_actions(qy, ctx, uid, data, edit)
        return
    await qy.answer("Unknown action")


async def admin_route(qy, ctx, uid: int, data: str, edit) -> None:
    if data == "adm":
        await edit(admin_home_text(uid), admin_kb(uid))
        return
    parts = data.split(":")
    sec = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    need = {s[0]: s[2] for s in ADMIN_SECTIONS}.get(sec)
    if need and not can(uid, need):
        await qy.answer("You do not have that permission.", show_alert=True)
        return
    if sec == "dash":
        await edit(admin_home_text(uid), admin_kb(uid))
    elif sec == "features":
        await edit(features_text(page), features_kb(page))
    elif sec == "admins":
        await edit(head("Admins", "Tap one to set its powers"), admins_kb(uid))
    elif sec == "stock":
        await edit(stock_text(), stock_kb())
    elif sec == "pay":
        await edit(pay_text(), pay_kb())
    elif sec == "content":
        await edit(head("Content", "Every text in the bot and the mini app"), content_kb(page))
    elif sec == "market":
        await edit(head("Marketing"), market_kb())
    elif sec == "analytics":
        await edit(analytics_text(), InlineKeyboardMarkup([[B("◀ Panel", "adm")]]))
    elif sec == "security":
        await edit(security_text(), security_kb())
    elif sec == "extras":
        await edit(extras_text(), extras_kb())
    elif sec == "settings":
        await edit(settings_text(), settings_kb())
    elif sec == "channels":
        await edit(head("Channels", "Force-join list"), channels_kb())
    elif sec == "logs":
        await edit(head("Logs", "Channels and per-event routing"), logs_kb())
    elif sec == "orders":
        rows = q("SELECT * FROM orders ORDER BY created_at DESC LIMIT 10")
        lines = [head("Orders", f"{q1('SELECT COUNT(*) c FROM orders')['c']} total"), ""]
        for o in rows:
            lines.append(f"<code>{o['id']}</code> · {esc(o['country'])} · {money(o['price'])} · <code>{o['user_id']}</code>")
        if not rows:
            lines.append("<i>No orders yet.</i>")
        kb = [[B("⚖️ Disputes", "disputes", 22)]] if feat("dispute") else []
        kb.append([B("◀ Panel", "adm")])
        await edit("\n".join(lines), InlineKeyboardMarkup(kb))
    elif sec == "users":
        lines = [head("Users", f"{q1('SELECT COUNT(*) c FROM users')['c']} registered"), ""]
        for u in q("SELECT * FROM users ORDER BY total_spent DESC LIMIT 10"):
            lines.append(f"<code>{u['user_id']}</code> @{esc(u['username'] or '-')} · {money(u['balance'])} · {u['total_orders']} orders")
        kb = [[B("💰 Credit user", "ucredit", 16), B("🚫 Ban user", "uban", 16)], [B("◀ Panel", "adm")]]
        await edit("\n".join(lines), InlineKeyboardMarkup(kb))


async def admin_actions(qy, ctx, uid: int, data: str, edit) -> None:


    if data.startswith("ft:"):
        if not can(uid, "features"):
            await qy.answer("No permission", show_alert=True)
            return
        _, key, page = data.split(":")
        feat_set(key, not feat(key))
        audit(uid, "feature", f"{key} → {'on' if feat(key) else 'off'}")
        await edit(features_text(int(page)), features_kb(int(page)))
        return
    if data.startswith("ftall:"):
        if not can(uid, "features"):
            await qy.answer("No permission", show_alert=True)
            return
        on = data.split(":")[1] == "1"
        for k in FEATURE_KEYS:
            feat_set(k, on)
        audit(uid, "feature", "all " + ("on" if on else "off"))
        await edit(features_text(0), features_kb(0))
        return


    if data.startswith("adu:"):
        await edit(admin_perm_text(int(data.split(":")[1])), admin_perm_kb(int(data.split(":")[1]), uid))
        return
    if data == "adnew":
        if not is_owner(uid):
            await qy.answer("Only the owner can add admins", show_alert=True)
            return
        ctx.user_data["await"] = ("newadmin", None)
        await edit(head("Add admin", "Send the numeric user id (they must have opened the bot)."),
                   InlineKeyboardMarkup([[B("◀ Admins", "a:admins", 22)]]))
        return
    if data.startswith("pm:"):
        _, target, key = data.split(":")
        target = int(target)
        if not can(uid, "admins") or (key == "admins" and not is_owner(uid)):
            await qy.answer("No permission", show_alert=True)
            return
        if is_owner(target):
            await qy.answer("The owner always has every permission.", show_alert=True)
            return
        p = perms_of(target)
        p[key] = not p.get(key)
        ex("UPDATE admins SET perms=? WHERE user_id=?", (json.dumps(p), target))
        audit(uid, "perm", f"{target} {key}={p[key]}")
        await edit(admin_perm_text(target), admin_perm_kb(target, uid))
        return
    if data.startswith("pmall:"):
        _, target, on = data.split(":")
        target, on = int(target), on == "1"
        if not can(uid, "admins"):
            await qy.answer("No permission", show_alert=True)
            return
        p = {k: on for k in PERM_KEYS}
        if not is_owner(uid):
            p["admins"] = False
        ex("UPDATE admins SET perms=? WHERE user_id=?", (json.dumps(p), target))
        await edit(admin_perm_text(target), admin_perm_kb(target, uid))
        return
    if data.startswith("admdel:"):
        target = int(data.split(":")[1])
        if not is_owner(uid) or is_owner(target):
            await qy.answer("Not allowed", show_alert=True)
            return
        ex("DELETE FROM admins WHERE user_id=?", (target,))
        audit(uid, "admin", f"removed {target}")
        await edit(head("Admins", "Removed"), admins_kb(uid))
        return


    if data.startswith("set:"):
        key = data.split(":", 1)[1]
        ctx.user_data["await"] = ("setting", key)
        await edit(head("Edit " + key.replace("_", " "), f"Current: {esc(sget(key) or '—')}\n\nSend the new value."),
                   InlineKeyboardMarkup([[B("◀ Panel", "adm")]]))
        return
    if data.startswith("tg:"):
        key = data.split(":", 1)[1]
        sset(key, "0" if sget(key) == "1" else "1")
        audit(uid, "setting", f"{key}={sget(key)}")
        await edit(settings_text(), settings_kb())
        return
    if data.startswith("ced:"):
        key = data.split(":", 1)[1]
        ctx.user_data["await"] = ("content", key)
        await edit(head("Edit text", key) + f"\n\n<code>{esc(cget(key))}</code>\n\nSend the new wording.",
                   InlineKeyboardMarkup([[B("◀ Content", "a:content", 22)]]))
        return


    if data == "stadd":
        await edit(add_stock_text(), add_stock_kb())
        return
    if data in ("stpaste", "stbulk"):
        ctx.user_data["await"] = ("stock", None)
        await edit(head("Paste stock", "One per line:\nCountry | +code | price | session"),
                   InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]))
        return
    if data == "stlogin":
        if not can(uid, "stock"):
            await qy.answer("No permission", show_alert=True)
            return
        await login_start(qy, ctx, uid)
        return
    if data == "stcancel":
        await login_cancel(qy, ctx, uid)
        return
    if data.startswith("stitem:"):
        sid = int(data.split(":")[1])
        await edit(stock_item_text(sid), stock_item_kb(sid))
        return
    if data.startswith("stsetprice:"):
        ctx.user_data["await"] = ("item_price", int(data.split(":")[1]))
        await edit(head("New price", "Send a number."),
                   InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]))
        return
    if data.startswith("stsetc:"):
        ctx.user_data["await"] = ("item_country", int(data.split(":")[1]))
        await edit(head("Country", "Send the country name."),
                   InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]))
        return
    if data.startswith("stdel:"):
        sid = int(data.split(":")[1])
        ex("DELETE FROM stock WHERE id=? AND sold=0", (sid,))
        audit(uid, "stock", f"deleted #{sid}")
        await edit(stock_text(), stock_kb())
        return
    if data.startswith("stprice:"):
        ctx.user_data["await"] = ("price", data.split(":", 1)[1])
        await edit(head("New price", data.split(":", 1)[1] + " — send a number."),
                   InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]))
        return
    if data.startswith("stc:"):
        c = data.split(":", 1)[1]
        n = q1("SELECT COUNT(*) c FROM stock WHERE country=? AND sold=0", (c,))["c"]
        await edit(head(c, f"{n} available · {money(price_of(c))}"),
                   InlineKeyboardMarkup([[B("💵 Set price", f"stprice:{c}", 16), B("🗑 Clear", f"stclr:{c}", 16)],
                                         [B("◀ Stock", "a:stock", 22)]]))
        return
    if data.startswith("stclr:"):
        c = data.split(":", 1)[1]
        ex("DELETE FROM stock WHERE country=? AND sold=0", (c,))
        audit(uid, "stock", f"cleared {c}")
        await edit(stock_text(), stock_kb())
        return


    if data == "chadd":
        ctx.user_data["await"] = ("channel", None)
        await edit(head("Add channel", "Send @handle or a t.me link. Make the bot an admin there."),
                   InlineKeyboardMarkup([[B("◀ Channels", "a:channels", 22)]]))
        return
    if data.startswith("chtg:"):
        cid = int(data.split(":")[1])
        row = q1("SELECT * FROM channels WHERE id=?", (cid,))
        ex("UPDATE channels SET required=? WHERE id=?", (0 if row["required"] else 1, cid))
        await edit(head("Channels", "Force-join list"), channels_kb())
        return
    if data.startswith("chdel:"):
        ex("DELETE FROM channels WHERE id=?", (int(data.split(":")[1]),))
        await edit(head("Channels", "Removed"), channels_kb())
        return


    if data == "lgmaster":
        sset("logs_master", "0" if sget("logs_master", "1") == "1" else "1")
        await edit(head("Logs", "Channels and per-event routing"), logs_kb())
        return
    if data == "lgadd":
        ctx.user_data["await"] = ("logchannel", None)
        await edit(head("Add log channel", "Send the channel id (-100…) or @handle."),
                   InlineKeyboardMarkup([[B("◀ Logs", "a:logs", 22)]]))
        return
    if data.startswith("lgpri:"):
        cid = int(data.split(":")[1])
        ex("UPDATE logchannels SET is_primary=0")
        ex("UPDATE logchannels SET is_primary=1 WHERE id=?", (cid,))
        await edit(head("Logs", "Primary channel updated"), logs_kb())
        return
    if data.startswith("lgdel:"):
        ex("DELETE FROM logchannels WHERE id=?", (int(data.split(":")[1]),))
        await edit(head("Logs", "Removed"), logs_kb())
        return
    if data.startswith("lgev:"):
        key = data.split(":", 1)[1]
        r = q1("SELECT * FROM logevents WHERE key=?", (key,))
        ex("UPDATE logevents SET on_=? WHERE key=?", (0 if r["on_"] else 1, key))
        await edit(head("Logs", "Channels and per-event routing"), logs_kb())
        return


    if data == "bc":
        ctx.user_data["await"] = ("broadcast", None)
        await edit(head("Broadcast", "Send the message. It goes to every user."),
                   InlineKeyboardMarkup([[B("◀ Marketing", "a:market", 22)]]))
        return
    if data == "cpns":
        lines = [head("Coupons"), ""]
        for c in q("SELECT * FROM coupons ORDER BY rowid DESC LIMIT 10"):
            lines.append(f"<code>{esc(c['code'])}</code> −{c['percent']:.0f}% · {c['used']}/{c['max_uses'] or '∞'}")
        if len(lines) == 2:
            lines.append("<i>No coupons yet.</i>")
        await edit("\n".join(lines), InlineKeyboardMarkup(
            [[B("➕ New coupon", "cpnnew", 22)], [B("◀ Marketing", "a:market", 22)]]))
        return
    if data == "cpnnew":
        ctx.user_data["await"] = ("coupon_new", None)
        await edit(head("New coupon", "Send:  CODE | percent | max uses"),
                   InlineKeyboardMarkup([[B("◀ Marketing", "a:market", 22)]]))
        return
    if data == "flash":
        ctx.user_data["await"] = ("flash", None)
        await edit(head("Flash sale", "Send:  percent | hours   (example: 20 | 5)"),
                   InlineKeyboardMarkup([[B("◀ Marketing", "a:market", 22)]]))
        return
    if data == "refrates":
        ctx.user_data["await"] = ("refrates", None)
        await edit(head("Referral rates", "Send:  level1 | level2   (example: 5 | 1)"),
                   InlineKeyboardMarkup([[B("◀ Marketing", "a:market", 22)]]))
        return


    if data == "bl":
        ctx.user_data["await"] = ("blacklist", None)
        n = q1("SELECT COUNT(*) c FROM blacklist")["c"]
        await edit(head("Blacklist", f"{n} entries") + "\n\nSend a user id, phone or UPI reference to block.",
                   InlineKeyboardMarkup([[B("◀ Security", "a:security", 22)]]))
        return
    if data == "audit":
        lines = [head("Audit log"), ""]
        for a in q("SELECT * FROM audit ORDER BY id DESC LIMIT 15"):
            lines.append(f"<code>{esc((a['ts'] or '')[11:16])}</code> <b>{esc(str(a['actor']))}</b> {esc(a['action'])} · {esc(a['detail'])}")
        if len(lines) == 2:
            lines.append("<i>Nothing logged yet.</i>")
        await edit("\n".join(lines), InlineKeyboardMarkup([[B("◀ Security", "a:security", 22)]]))
        return
    if data == "ucredit":
        ctx.user_data["await"] = ("credit", None)
        await edit(head("Credit user", "Send:  user_id | amount"),
                   InlineKeyboardMarkup([[B("◀ Users", "a:users", 22)]]))
        return
    if data == "uban":
        ctx.user_data["await"] = ("ban", None)
        await edit(head("Ban user", "Send the user id to ban or unban."),
                   InlineKeyboardMarkup([[B("◀ Users", "a:users", 22)]]))
        return
    if data == "disputes":
        lines = [head("Disputes"), ""]
        for d in q("SELECT * FROM disputes ORDER BY id DESC LIMIT 10"):
            lines.append(f"#{d['id']} · {esc(d['order_id'])} · {esc(d['reason'])} · {esc(d['status'])}")
        if len(lines) == 2:
            lines.append("<i>No disputes.</i>")
        await edit("\n".join(lines), InlineKeyboardMarkup([[B("◀ Orders", "a:orders", 22)]]))
        return
    if data == "paypend":
        rows = q("SELECT * FROM deposits WHERE status='review' ORDER BY id DESC LIMIT 10")
        kb = [[B(f"{money(d['amount'])} · {d['user_id']}", f"apprv:{d['id']}", 26)] for d in rows]
        kb.append([B("◀ Payments", "a:pay", 22)])
        await edit(head("Pending deposits", f"{len(rows)} waiting"), InlineKeyboardMarkup(kb))
        return
    if data.startswith(("clrep:", "clref:", "cldec:")):
        if not can(uid, "orders"):
            await qy.answer("No permission", show_alert=True)
            return
        action, cid = data.split(":")[0], int(data.split(":")[1])
        cl = q1("SELECT * FROM claims WHERE id=?", (cid,))
        if not cl or cl["status"] != "open":
            await qy.answer("Already decided", show_alert=True)
            return
        o = q1("SELECT * FROM orders WHERE id=?", (cl["order_id"],))
        buyer = cl["user_id"]
        if action == "clrep":
            oid2, row = deliver(buyer, o["country"], 0) if o else (None, None)
            if not oid2:
                await qy.answer("Nothing in stock to replace it with", show_alert=True)
                return
            ex("UPDATE claims SET status='replaced', decided_by=?, decided_at=? WHERE id=?",
               (uid, now(), cid))
            await notify_user(buyer, premiumize(
                head("Replacement sent", oid2)
                + f"\n\n<code>{esc(row['payload'])}</code>"))
            outcome = "replaced"
        elif action == "clref":
            credit(buyer, float(o["price"]) if o else 0)
            ex("UPDATE claims SET status='refunded', decided_by=?, decided_at=? WHERE id=?",
               (uid, now(), cid))
            await notify_user(buyer, premiumize(
                head("Refunded", money(o["price"] if o else 0))
                + "\n\nThe amount is back in your wallet."))
            outcome = "refunded"
        else:
            ex("UPDATE claims SET status='declined', decided_by=?, decided_at=? WHERE id=?",
               (uid, now(), cid))
            await notify_user(buyer, premiumize(
                head("Claim closed", "We could not confirm the problem")
                + "\n\nMessage support if you disagree."))
            outcome = "declined"
        audit(uid, "claim", f"#{cid} {outcome}")
        await send_log("admin", f"⚖️ <b>Claim #{cid}</b> {outcome} by <code>{uid}</code>")
        await qy.answer(outcome.title())
        with contextlib.suppress(BadRequest, TelegramError):
            await qy.edit_message_reply_markup(reply_markup=None)
        return
    if data.startswith("rej:"):
        if not can(uid, "payments"):
            await qy.answer("No permission", show_alert=True)
            return
        dep = q1("SELECT * FROM deposits WHERE id=?", (int(data.split(":")[1]),))
        if dep and dep["status"] != "completed":
            ex("UPDATE deposits SET status='rejected' WHERE id=?", (dep["id"],))
            await notify_user(dep["user_id"], premiumize(
                f"❌ Your payment of {money(dep['amount'])} could not be confirmed. "
                "Contact support if you believe this is wrong."))
            audit(uid, "deposit", f"rejected #{dep['id']}")
        await qy.answer("Rejected")
        with contextlib.suppress(BadRequest, TelegramError):
            await qy.edit_message_reply_markup(reply_markup=None)
        return
    if data.startswith("apprv:"):
        if not can(uid, "payments"):
            await qy.answer("No permission", show_alert=True)
            return
        dep = q1("SELECT * FROM deposits WHERE id=?", (int(data.split(":")[1]),))
        if dep and dep["status"] != "completed":
            total = settle_deposit(dep, "manual")
            audit(uid, "deposit", f"approved #{dep['id']} {money(total)}")
            await notify_user(dep["user_id"], f"✅ Your deposit of {money(dep['amount'])} was approved.")
        await edit(head("Approved", "Wallet credited."), InlineKeyboardMarkup([[B("◀ Payments", "a:pay", 22)]]))
        return
    if data == "gvnew":
        if not can(uid, "broadcast"):
            await qy.answer("No permission", show_alert=True)
            return
        ctx.user_data["await"] = ("giveaway", None)
        await edit(head("Start giveaway", "Send:  amount | slots | note"),
                   InlineKeyboardMarkup([[B("◀ Extras", "a:extras", 22)]]))
        return
    if data == "udnew":
        if not can(uid, "users"):
            await qy.answer("No permission", show_alert=True)
            return
        ctx.user_data["await"] = ("udhar", None)
        await edit(head("Give udhar", "Send:  user_id | amount | note"),
                   InlineKeyboardMarkup([[B("◀ Extras", "a:extras", 22)]]))
        return
    if data == "udlist":
        lines = [head("Udhar outstanding"), ""]
        for d in q("SELECT * FROM debts ORDER BY amount DESC LIMIT 15"):
            lines.append(f"<code>{d['user_id']}</code> — {money(d['amount'])} {esc(d['note'] or '')}")
        if len(lines) == 2:
            lines.append("<i>Nobody owes anything.</i>")
        await edit("\n".join(lines), InlineKeyboardMarkup([[B("◀ Extras", "a:extras", 22)]]))
        return
    if data == "alist":
        lines = [head("Restock waiting list"), ""]
        for r in q("SELECT country, COUNT(*) c FROM stock_alerts GROUP BY country ORDER BY c DESC"):
            lines.append(f"{flag_for(r['country'])} {esc(r['country'].title())} — {r['c']} waiting")
        if len(lines) == 2:
            lines.append("<i>Nobody is waiting.</i>")
        await edit("\n".join(lines), InlineKeyboardMarkup([[B("◀ Extras", "a:extras", 22)]]))
        return
    if data == "gmailtest":
        await qy.answer("Connecting to Gmail…")
        box, err = await asyncio.to_thread(gmail_login)
        if not box:
            await edit(head("Gmail test failed", err),
                       InlineKeyboardMarkup([[B("📧 Gmail address", "set:gmail_id", 16),
                                              B("🔑 App password", "set:gmail_pass", 16)],
                                             [B("◀ Extras", "a:extras", 22)]]))
            return
        n = 0
        with contextlib.suppress(Exception):
            box.select("inbox")
            typ, msgs = box.search(None, "ALL")
            n = len(msgs[0].split()) if typ == "OK" else 0
        with contextlib.suppress(Exception):
            box.logout()
        await edit(head("Gmail works", f"Signed in as {sget('gmail_id')}")
                   + f"\n\n📥 <b>Inbox:</b> {n} messages"
                   + f"\n🔎 <b>Scanning the newest:</b> {int(sgetf('gmail_scan', 30))}"
                   + f"\n📨 <b>Reads mail from:</b> {esc(sget('gmail_sender') or 'any sender')}"
                   + f"\n🔖 <b>Note must match:</b> "
                   + ("yes" if sget("verify_need_note", "1") == "1" else "no"),
                   InlineKeyboardMarkup([[B("🔎 Scan depth", "set:gmail_scan", 16),
                                          B("🔖 Note rule", "tg:verify_need_note", 16)],
                                         [B("◀ Extras", "a:extras", 22)]]))
        return
    if data == "miniapp":
        await edit(miniapp_status_text(), miniapp_kb())
        return
    if data == "tunnel":
        await qy.answer("Starting — this can take up to a minute.")
        await stop_tunnel()
        await start_tunnel(force=True)
        await edit(miniapp_status_text(), miniapp_kb())
        return
    if data == "tunnelclear":
        await stop_tunnel()
        sset("miniapp_url", "")
        TUNNEL.update(url="", how="", status="cleared")
        await qy.answer("Cleared — starting again")
        await start_tunnel(force=True)
        await edit(miniapp_status_text(), miniapp_kb())
        return
    if data.startswith("tgfeat:"):
        key = data.split(":", 1)[1]
        if not can(uid, "features"):
            await qy.answer("No permission", show_alert=True)
            return
        feat_set(key, not feat(key))
        await edit(pay_text(), pay_kb())
        return
    if data == "emojipack":
        await edit(emoji_pack_text(), emoji_pack_kb())
        return
    if data.startswith("epack:"):
        sset("emoji_pack", data.split(":", 1)[1])
        audit(uid, "setting", "emoji_pack=" + sget("emoji_pack"))
        await edit(emoji_pack_text(), emoji_pack_kb())
        return
    if data == "qrtest":
        buf = generate_qr("upi://pay?pa=test@upi&pn=Test&am=1.00&cu=INR&tn=TEST")
        if buf:
            buf.name = "qr-test.png"
            await ctx.bot.send_photo(chat_id=uid, photo=InputFile(buf),
                                     caption="QR generator is working.")
            await qy.answer("QR sent")
        else:
            await qy.answer("QR failed — see the logs", show_alert=True)
        return
    if data == "sstats":
        await edit(session_stats_text(), InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]))
        return
    if data == "backup":
        path = DB_PATH
        with open(path, "rb") as fh:
            await ctx.bot.send_document(chat_id=uid, document=InputFile(fh, filename="backup.db"))
        await qy.answer("Backup sent")
        return
    if data == "export":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["order", "user", "country", "price", "created"])
        for o in q("SELECT * FROM orders ORDER BY created_at DESC"):
            w.writerow([o["id"], o["user_id"], o["country"], o["price"], o["created_at"]])
        bio = io.BytesIO(buf.getvalue().encode())
        bio.name = "orders.csv"
        await ctx.bot.send_document(chat_id=uid, document=InputFile(bio))
        await qy.answer("CSV sent")
        return
    if data == "synccmd":
        await register_commands(ctx.application or _APP)
        await qy.answer("Commands synced")
        return
    await qy.answer("Unknown action")


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    uid = update.effective_user.id
    text = (msg.text or "").strip()
    get_user(uid, update.effective_user.username or "", update.effective_user.first_name or "")
    pending = ctx.user_data.pop("await", None)
    if not pending:
        await show_home(update, uid)
        return
    kind, payload = pending
    admin_kinds = {
        "setting", "content", "stock", "price", "channel", "logchannel", "broadcast",
        "coupon_new", "flash", "refrates", "blacklist", "credit", "ban", "newadmin",
        "giveaway", "udhar", "login_phone", "login_code", "login_2fa",
        "__never__",
        "item_price", "item_country",
    }
    if kind in admin_kinds and not is_admin(uid):
        return

    async def done(title: str, body: str = "") -> None:
        await msg.reply_text(head(title, body), parse_mode=ParseMode.HTML,
                             reply_markup=main_menu_kb(uid) if not is_admin(uid) else admin_kb(uid))

    if kind == "login_phone":
        await login_send_code(msg, ctx, uid, text)
        return
    if kind == "login_code":
        await login_submit_code(msg, ctx, uid, text)
        return
    if kind == "login_2fa":
        await login_submit_2fa(msg, ctx, uid, text)
        return
    if kind == "item_price":
        try:
            ex("UPDATE stock SET price=? WHERE id=?", (float(text), payload))
            await done("Price set", money(float(text)))
        except ValueError:
            await done("Not a number")
        return
    if kind == "item_country":
        c = text.strip().title()
        ex("UPDATE stock SET country=?, flag=? WHERE id=?", (c, flag_for(c), payload))
        await done("Country set", c)
        return
    if kind == "support":
        await notify_admins(f"🆘 <b>Support</b> from <code>{uid}</code>\n\n{esc(text)}")
        await done("Sent", "An admin will reply here.")
    elif kind == "coupon":
        c, n = payload
        pct = coupon_percent(text)
        await msg.reply_text(buy_text(uid, c, n, text if pct else ""),
                             reply_markup=buy_kb(c, n, text if pct else ""), parse_mode=ParseMode.HTML)
    elif kind == "depamt":
        try:
            amt = float(text)
        except ValueError:
            await done("Not a number", "Try again from the wallet.")
            return
        dep = create_deposit(uid, amt)
        with contextlib.suppress(Exception):
            await send_deposit_message(ctx.bot, uid, dep)
    elif kind == "dep_utr":
        utr = re.sub(r"\D", "", text)[:32]
        if len(utr) < 6:
            ctx.user_data["await"] = ("dep_utr", payload)
            await msg.reply_text(
                premiumize(head("That is not a UTR",
                                "It is the 10-12 digit number in your payment app.")),
                parse_mode=ParseMode.HTML)
            return
        dep = q1("SELECT * FROM deposits WHERE id=?", (payload,))
        if not dep:
            await done("That request expired", "Start a new one from the wallet.")
            return
        ex("UPDATE deposits SET utr=?, status='review' WHERE id=?", (utr, payload))
        dep = q1("SELECT * FROM deposits WHERE id=?", (payload,))
        shot = q1("SELECT file_id FROM depshots WHERE dep_id=?", (payload,))
        text_out = payment_review(dep, uid, utr)
        for r in q("SELECT user_id FROM admins"):
            if not can(r["user_id"], "payments"):
                continue
            kb = InlineKeyboardMarkup([[B("✅ Approve", f"apprv:{payload}", 10),
                                        B("✖ Reject", f"rej:{payload}", 10)]])
            sent_photo = False
            if shot and shot["file_id"]:
                with contextlib.suppress(Exception):
                    await ctx.bot.send_photo(chat_id=r["user_id"], photo=shot["file_id"],
                                             caption=text_out[:1024], reply_markup=kb,
                                             parse_mode=ParseMode.HTML)
                    sent_photo = True
            if not sent_photo:
                with contextlib.suppress(Exception):
                    await ctx.bot.send_message(chat_id=r["user_id"], text=text_out,
                                               reply_markup=kb, parse_mode=ParseMode.HTML)
        await msg.reply_text(
            premiumize(head("Sent for review", "We will confirm it shortly.")
                       + f"\n\n💵 {money(dep['amount'])}\n🧾 <code>{esc(utr)}</code>"),
            reply_markup=main_menu_kb(uid), parse_mode=ParseMode.HTML)
        return
    elif kind == "utr":
        utr = re.sub(r"\s+", "", text)[:32]
        ex("UPDATE deposits SET utr=?, status='review' WHERE id=?", (utr, payload))
        dep = q1("SELECT * FROM deposits WHERE id=?", (payload,))
        await notify_admins(utr_report(dep, uid, utr), "payments")
        await done("Sent for review", "An admin will confirm it shortly.")
    elif kind == "claim":
        if q1("SELECT 1 FROM claims WHERE order_id=?", (payload,)):
            await done("Already sent", "One claim per order.")
            return
        cid = ex("INSERT INTO claims(order_id,user_id,reason,details,status,created_at)"
                 " VALUES(?,?,?,?,'open',?)",
                 (payload, uid, text[:60], text[:500], now()))
        o = q1("SELECT * FROM orders WHERE id=?", (payload,))
        u = get_user(uid)
        note = premiumize("\n".join([
            head(f"Claim #{cid}", "Needs a decision"),
            "",
            f"<b>User:</b> {esc(u['first_name'] or '—')} "
            f"({'@' + esc(u['username']) if u['username'] else 'no username'})",
            f"<b>ID:</b> <code>{uid}</code>",
            f"<b>Order:</b> <code>{esc(payload)}</code>",
            f"<b>Paid:</b> {money(o['price'] if o else 0)}",
            "",
            esc(text)[:400],
            "",
            "<i>Nothing has been refunded.</i>",
        ]))
        for r in q("SELECT user_id FROM admins"):
            if not can(r["user_id"], "orders"):
                continue
            kb = InlineKeyboardMarkup([
                [B("🔁 Replace", f"clrep:{cid}", 10), B("💸 Refund", f"clref:{cid}", 10)],
                [B("✖ Decline", f"cldec:{cid}", 22)]])
            with contextlib.suppress(Exception):
                await ctx.bot.send_message(chat_id=r["user_id"], text=note,
                                           reply_markup=kb, parse_mode=ParseMode.HTML)
        await done("Claim sent", "An admin will reply here.")
    elif kind == "dispute":
        ex("INSERT INTO disputes(order_id,user_id,reason,details,status,created_at) VALUES(?,?,?,?,'open',?)",
           (payload, uid, text[:60], text, now()))
        await notify_admins(f"⚖️ <b>Dispute</b> on {esc(payload)} by <code>{uid}</code>\n\n{esc(text)}", "orders")
        await done("Dispute opened", "An admin will review it.")

    elif kind == "setting":
        sset(payload, text)
        audit(uid, "setting", f"{payload}={text[:40]}")
        await done("Saved", f"{payload} = {text[:60]}")
    elif kind == "content":
        cset(payload, text)
        audit(uid, "content", payload)
        await done("Text updated", "The bot and the mini app both use it now.")
    elif kind == "stock":
        good, bad = parse_stock_lines(text)
        n = insert_stock(good)
        body = f"{n} imported"
        if bad:
            body += f" · {len(bad)} skipped"
        await done("Stock uploaded", body)
        await send_log("stock", f"📦 <b>Stock added</b> — {n} accounts by <code>{uid}</code>")
        for c in {r["country"] for r in good}:
            with contextlib.suppress(Exception):
                await announce_restock(c)
    elif kind == "price":
        try:
            ex("UPDATE stock SET price=? WHERE country=? AND sold=0", (float(text), payload))
            audit(uid, "pricing", f"{payload}={text}")
            await done("Price updated", f"{payload} → {money(float(text))}")
        except ValueError:
            await done("Not a number")
    elif kind == "channel":
        handle = text.strip()
        name = handle.replace("https://t.me/", "@").split("/")[-1][:26]
        ex("INSERT INTO channels(name,handle,chat_id,required,active) VALUES(?,?,?,1,1)",
           (name, handle, handle if handle.startswith("@") else ""))
        await done("Channel added", name)
    elif kind == "logchannel":
        first = not q("SELECT 1 FROM logchannels")
        ex("INSERT INTO logchannels(name,chat_id,is_primary,active) VALUES(?,?,?,1)",
           (text[:26], text, 1 if first else 0))
        await done("Log channel added", text[:40])
    elif kind == "broadcast":
        sent = 0
        for r in q("SELECT user_id FROM users WHERE banned=0"):
            try:
                await ctx.bot.send_message(chat_id=r["user_id"], text=text, parse_mode=ParseMode.HTML)
                sent += 1
            except (TelegramError, Forbidden):
                continue
            await asyncio.sleep(0.04)
        audit(uid, "broadcast", f"{sent} users")
        await done("Broadcast sent", f"{sent} delivered")
    elif kind == "coupon_new":
        parts = [p.strip() for p in text.split("|")]
        try:
            ex("INSERT OR REPLACE INTO coupons(code,percent,max_uses,used,active,created_at) VALUES(?,?,?,0,1,?)",
               (parts[0].upper(), float(parts[1]), int(parts[2]) if len(parts) > 2 else 0, now()))
            await done("Coupon created", parts[0].upper())
        except (IndexError, ValueError):
            await done("Wrong format", "Use:  CODE | percent | max uses")
    elif kind == "flash":
        parts = [p.strip() for p in text.split("|")]
        try:
            sset("flash_percent", float(parts[0]))
            hours = float(parts[1]) if len(parts) > 1 else 5
            sset("flash_until", (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat())
            await done("Flash sale live", f"−{parts[0]}% for {hours} h")
        except (IndexError, ValueError):
            await done("Wrong format", "Use:  percent | hours")
    elif kind == "refrates":
        parts = [p.strip() for p in text.split("|")]
        with contextlib.suppress(IndexError, ValueError):
            sset("ref_l1", float(parts[0]))
            if len(parts) > 1:
                sset("ref_l2", float(parts[1]))
        await done("Referral rates saved")
    elif kind == "giveaway":
        parts = [p.strip() for p in text.split("|")]
        try:
            amount, slots = float(parts[0]), int(parts[1])
            note = parts[2] if len(parts) > 2 else ""
        except (IndexError, ValueError):
            await done("Wrong format", "Use:  amount | slots | note")
            return
        await msg.reply_text(head("Sending…", f"{slots} slots of {money(amount)}"),
                             parse_mode=ParseMode.HTML)
        sent = await launch_giveaway(amount, slots, note, uid)
        audit(uid, "giveaway", f"{amount} x {slots}")
        await done("Giveaway live", f"announced to {sent} users")
    elif kind == "udhar":
        parts = [p.strip() for p in text.split("|")]
        try:
            target, amount = int(parts[0]), float(parts[1])
        except (IndexError, ValueError):
            await done("Wrong format", "Use:  user_id | amount | note")
            return
        cap = sgetf("udhar_max", 200)
        if amount > cap:
            await done("Over the limit", f"Udhar is capped at {money(cap)}")
            return
        note = parts[2] if len(parts) > 2 else ""
        credit(target, amount)
        owed = add_debt(target, amount, note, uid)
        audit(uid, "udhar", f"{target} +{amount}")
        await notify_user(target, premiumize(
            f"🧾 <b>Udhar of {money(amount)}</b> added to your wallet.\n"
            f"Total owed: <b>{money(owed)}</b> — it comes off your next deposit."))
        await done("Udhar given", f"{money(amount)} → {target} (owes {money(owed)})")
    elif kind == "blacklist":
        ex("INSERT OR IGNORE INTO blacklist(kind,value,added_at) VALUES('user',?,?)", (text, now()))
        await done("Blacklisted", text[:40])
    elif kind == "credit":
        parts = [p.strip() for p in text.split("|")]
        try:
            target, amount = int(parts[0]), float(parts[1])
            credit(target, amount)
            audit(uid, "credit", f"{target} {amount}")
            await notify_user(target, f"💰 An admin credited {money(amount)} to your wallet.")
            await done("Credited", f"{money(amount)} → {target}")
        except (IndexError, ValueError):
            await done("Wrong format", "Use:  user_id | amount")
    elif kind == "ban":
        try:
            target = int(text)
            row = q1("SELECT banned FROM users WHERE user_id=?", (target,))
            newv = 0 if row and row["banned"] else 1
            ex("UPDATE users SET banned=? WHERE user_id=?", (newv, target))
            audit(uid, "ban", f"{target}={newv}")
            await done("Done", f"User {target} {'banned' if newv else 'unbanned'}")
        except ValueError:
            await done("Not a user id")
    elif kind == "newadmin":
        try:
            target = int(text)
        except ValueError:
            await done("Not a user id")
            return
        if not is_owner(uid):
            return
        ex("INSERT OR IGNORE INTO admins(user_id,role,perms,label,added_by,added_at) VALUES(?,'manager',?,?,?,?)",
           (target, json.dumps({k: False for k in PERM_KEYS}), f"admin {target}", uid, now()))
        audit(uid, "admin", f"added {target}")
        await msg.reply_text(admin_perm_text(target), reply_markup=admin_perm_kb(target, uid),
                             parse_mode=ParseMode.HTML)


_QR_EC_M = {
    1: (10, [(1, 16)]),
    2: (16, [(1, 28)]),
    3: (26, [(1, 44)]),
    4: (18, [(2, 32)]),
    5: (24, [(2, 43)]),
    6: (16, [(4, 27)]),
    7: (18, [(4, 31)]),
    8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}


_QR_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}


def _qr_capacity(version: int) -> int:
    ec, blocks = _QR_EC_M[version]
    data_codewords = sum(c * d for c, d in blocks)
    return data_codewords - 2 - (1 if version >= 10 else 0)


_GF_EXP = [0] * 512
_GF_LOG = [0] * 256


def _gf_init() -> None:
    x = 1
    for i in range(255):
        _GF_EXP[i] = x
        _GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]


_gf_init()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator(n: int) -> list[int]:
    poly = [1]
    for i in range(n):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= _gf_mul(c, 1)
            nxt[j + 1] ^= _gf_mul(c, _GF_EXP[i])
        poly = nxt
    return poly


def _rs_encode(data: list[int], n: int) -> list[int]:
    gen = _rs_generator(n)
    rem = [0] * n
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i, g in enumerate(gen[1:]):
            rem[i] ^= _gf_mul(g, factor)
    return rem


class _Bits:
    def __init__(self) -> None:
        self.bits: list[int] = []

    def put(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def to_bytes(self) -> list[int]:
        out = []
        for i in range(0, len(self.bits), 8):
            chunk = self.bits[i:i + 8]
            chunk += [0] * (8 - len(chunk))
            out.append(int("".join(str(b) for b in chunk), 2))
        return out


def _qr_encode_data(payload: bytes, version: int) -> list[int]:
    ec_per_block, blocks = _QR_EC_M[version]
    total_data = sum(c * d for c, d in blocks)
    count_bits = 8 if version < 10 else 16

    bs = _Bits()
    bs.put(0b0100, 4)
    bs.put(len(payload), count_bits)
    for byte in payload:
        bs.put(byte, 8)
    spare = total_data * 8 - len(bs.bits)
    bs.put(0, min(4, spare))
    while len(bs.bits) % 8:
        bs.bits.append(0)
    data = bs.to_bytes()
    pad = [0xEC, 0x11]
    i = 0
    while len(data) < total_data:
        data.append(pad[i % 2])
        i += 1


    data_blocks, ec_blocks, pos = [], [], 0
    for count, size in blocks:
        for _ in range(count):
            chunk = data[pos:pos + size]
            pos += size
            data_blocks.append(chunk)
            ec_blocks.append(_rs_encode(chunk, ec_per_block))

    out = []
    for i in range(max(len(b) for b in data_blocks)):
        for b in data_blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_per_block):
        for b in ec_blocks:
            out.append(b[i])
    return out


def _qr_bch_format(fmt: int) -> int:
    v = fmt << 10
    for i in range(4, -1, -1):
        if v & (1 << (i + 10)):
            v ^= 0x537 << i
    return ((fmt << 10) | v) ^ 0x5412


def _qr_bch_version(ver: int) -> int:
    v = ver << 12
    for i in range(5, -1, -1):
        if v & (1 << (i + 12)):
            v ^= 0x1F25 << i
    return (ver << 12) | v


def _qr_mask(mask: int, r: int, c: int) -> bool:
    return [
        lambda: (r + c) % 2 == 0,
        lambda: r % 2 == 0,
        lambda: c % 3 == 0,
        lambda: (r + c) % 3 == 0,
        lambda: (r // 2 + c // 3) % 2 == 0,
        lambda: (r * c) % 2 + (r * c) % 3 == 0,
        lambda: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
        lambda: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
    ][mask]()


def _qr_base(version: int):
    size = version * 4 + 17
    mod = [[None] * size for _ in range(size)]

    def finder(row: int, col: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = row + r, col + c
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                inring = (0 <= r <= 6 and c in (0, 6)) or (0 <= c <= 6 and r in (0, 6))
                incore = 2 <= r <= 4 and 2 <= c <= 4
                mod[rr][cc] = 1 if (inring or incore) else 0

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    for i in range(8, size - 8):
        bit = 1 if i % 2 == 0 else 0
        mod[6][i] = bit
        mod[i][6] = bit

    centres = _QR_ALIGN[version]
    for r in centres:
        for c in centres:
            if (r, c) in ((6, 6), (6, size - 7), (size - 7, 6)):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    mod[r + dr][c + dc] = 1 if max(abs(dr), abs(dc)) != 1 else 0

    mod[size - 8][8] = 1
    for i in range(9):
        if mod[8][i] is None:
            mod[8][i] = 0
        if mod[i][8] is None:
            mod[i][8] = 0
    for i in range(8):
        if mod[8][size - 1 - i] is None:
            mod[8][size - 1 - i] = 0
        if mod[size - 1 - i][8] is None:
            mod[size - 1 - i][8] = 0
    if version >= 7:
        for i in range(6):
            for j in range(3):
                mod[size - 11 + j][i] = 0
                mod[i][size - 11 + j] = 0
    return mod, size


def _qr_reserved(version: int, size: int) -> list[list[bool]]:
    mod, _ = _qr_base(version)
    return [[mod[r][c] is not None for c in range(size)] for r in range(size)]


def _qr_penalty(m: list[list[int]], size: int) -> int:
    score = 0
    for line in list(m) + [list(col) for col in zip(*m)]:
        run, prev = 1, line[0]
        for v in line[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, v
        if run >= 5:
            score += 3 + (run - 5)
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for line in list(m) + [list(col) for col in zip(*m)]:
        for i in range(size - 10):
            seg = list(line[i:i + 11])
            if seg == pat1 or seg == pat2:
                score += 40
    dark = sum(sum(row) for row in m)
    percent = dark * 100.0 / (size * size)
    score += 10 * int(abs(percent - 50) / 5)
    return score


def qr_matrix(text: str, mask: int | None = None) -> list[list[int]]:


    payload = text.encode("utf-8")
    version = next(
        (v for v in range(1, 11) if _qr_capacity(v) >= len(payload)), None
    )
    if version is None:
        raise ValueError("payload too long for a version-10 QR code")

    codewords = _qr_encode_data(payload, version)
    mod, size = _qr_base(version)
    reserved = _qr_reserved(version, size)

    bits = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)

    idx, upward, col = 0, True, size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if reserved[row][c]:
                    continue
                mod[row][c] = bits[idx] if idx < len(bits) else 0
                idx += 1
        upward = not upward
        col -= 2

    best, best_score = None, None
    for mk in (range(8) if mask is None else [mask]):
        cand = [[(mod[r][c] or 0) for c in range(size)] for r in range(size)]
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and _qr_mask(mk, r, c):
                    cand[r][c] ^= 1
        fmt = _qr_bch_format((0b00 << 3) | mk)
        for i in range(15):
            bit = (fmt >> i) & 1

            if i < 6:
                cand[i][8] = bit
            elif i < 8:
                cand[i + 1][8] = bit
            else:
                cand[size - 15 + i][8] = bit

            if i < 8:
                cand[8][size - i - 1] = bit
            elif i < 9:
                cand[8][15 - i] = bit
            else:
                cand[8][14 - i] = bit
        cand[size - 8][8] = 1
        if version >= 7:
            vinfo = _qr_bch_version(version)
            for i in range(18):
                bit = (vinfo >> i) & 1
                cand[size - 11 + i % 3][i // 3] = bit
                cand[i // 3][size - 11 + i % 3] = bit
        score = _qr_penalty(cand, size)
        if best_score is None or score < best_score:
            best, best_score = cand, score
    return best


def qr_png(text: str, scale: int = 8, border: int = 4) -> bytes:

    import struct
    import zlib

    m = qr_matrix(text)
    size = len(m)
    px = (size + border * 2) * scale
    row_white = b"\xff" * px

    raw = bytearray()
    for _ in range(border * scale):
        raw += b"\x00" + row_white
    for row in m:
        line = bytearray()
        for _ in range(border * scale):
            line += b"\xff"
        for v in row:
            line += (b"\x00" if v else b"\xff") * scale
        for _ in range(border * scale):
            line += b"\xff"
        for _ in range(scale):
            raw += b"\x00" + bytes(line)
    for _ in range(border * scale):
        raw += b"\x00" + row_white

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", px, px, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def qr_text(text: str) -> str:

    m = qr_matrix(text)
    pad = [0] * len(m[0])
    rows = [pad, pad] + m + [pad, pad]
    out = []
    for i in range(0, len(rows) - 1, 2):
        line = ""
        for a, b in zip(rows[i], rows[i + 1]):
            line += {(0, 0): "█", (1, 0): "▄", (0, 1): "▀", (1, 1): " "}[(a, b)]
        out.append("  " + line + "  ")
    return "\n".join(out)


class ColorButton(InlineKeyboardButton):


    __slots__ = ("_style",)

    def __init__(self, text, callback_data=None, url=None, web_app=None, style=None):
        kwargs = {"text": text}
        if url:
            kwargs["url"] = url
        elif web_app:
            kwargs["web_app"] = web_app
        else:
            kwargs["callback_data"] = callback_data
        super().__init__(**kwargs)
        object.__setattr__(self, "_style", style)

    def to_dict(self, recursive=True):
        d = super().to_dict(recursive)
        style = getattr(self, "_style", None)
        if style and feat("colorBtn"):
            d["style"] = style
        return d


def colored_buttons_active() -> bool:
    try:
        probe = InlineKeyboardMarkup([[ColorButton("probe", "probe", style="success")]]).to_dict()
        return probe["inline_keyboard"][0][0].get("style") == "success"
    except Exception:
        return False


def divider() -> str:
    return cget("divider")


EMOJI_PACKS: dict[str, dict[str, str]] = {
    "classic": {},
    "premium": {
        "💰": "💎", "⭐": "✨", "🔥": "🌟", "✅": "✔️", "🚫": "⛔️",
        "⚠️": "❗️", "👑": "♛", "🔑": "🗝", "🛡": "🛡️", "🔔": "🛎",
        "🎉": "🎊", "🛒": "🛍", "🏦": "🏛", "🔖": "🏷", "🛠": "⚙️",
        "🔄": "⟳", "🔁": "⟲", "⚡": "⚜️", "⏳": "⧖", "⌛": "⧖",
        "◀": "‹", "▶": "›", "✖": "✕", "➕": "✛", "🟢": "◉", "⚪️": "◌",
    },
    "minimal": {e: "" for e in (
        "💰💵💳🧾🏦🛒📦🎁⭐🔥✅🚫⚠️🏠👤👥👑🔑🔐🛡📉📣🔔🎉🎟🧺🛠🗑➕🔄🔁⚡🌐📖🌍♡"
        "🔖📄🩺🤖🎯⏳⌛📊📈🟢⚪️💎💸🪙"
    )},
}


def emoji_pack_name() -> str:
    name = sget("emoji_pack", "premium")
    return name if name in EMOJI_PACKS else "classic"


def emoji_map() -> dict:

    raw = sget("premium_emoji_map")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        out = {}
        for pair in raw.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                out[k.strip()] = v.strip()
        return out


def styled(text: str) -> str:

    if not text:
        return text
    pack = EMOJI_PACKS.get(emoji_pack_name()) or {}
    if not pack:
        return text
    for plain in sorted(pack, key=len, reverse=True):
        if plain in text:
            text = text.replace(plain, pack[plain])
    return re.sub(r"  +", " ", text).strip() if emoji_pack_name() == "minimal" else text


def premiumize(text: str) -> str:

    if not text:
        return text
    mapping = emoji_map() if feat("premiumEmoji") else {}
    if not mapping:
        return styled(text)

    parked = {}
    for i, emoji in enumerate(sorted(mapping, key=len, reverse=True)):
        eid = mapping[emoji]
        if eid.isdigit() and emoji in text:
            key = f"\x00{i}\x00"
            parked[key] = f'<tg-emoji emoji-id="{eid}">{emoji}</tg-emoji>'
            text = text.replace(emoji, key)
    text = styled(text)
    for key, html in parked.items():
        text = text.replace(key, html)
    return text


def banner_line() -> str:
    if not feat("menuBanner"):
        return ""
    txt = sget("banner_text")
    return f"\n\n📣 <b>{esc(txt)}</b>" if txt else ""


async def send_banner(bot, chat_id: int) -> None:
    if not feat("menuBanner"):
        return
    url = sget("banner_url")
    if not url:
        return
    with contextlib.suppress(TelegramError):
        await bot.send_photo(chat_id=chat_id, photo=url, caption=sget("banner_text") or None)


def generate_qr(payload: str):

    if not payload:
        return None
    try:
        buf = io.BytesIO(qr_png(payload, scale=8, border=4))
        buf.seek(0)
        return buf
    except Exception as e:
        log.warning("qr failed: %s", e)
        return None


def qr_status() -> str:
    try:
        n = len(qr_png("upi://pay?pa=test@upi&am=1&cu=INR&tn=T"))
        return f"working ({n} byte PNG, built in)"
    except Exception as e:
        return f"broken: {e}"


async def send_payment_qr(bot, chat_id: int, dep: sqlite3.Row) -> bool:

    if not feat("payQR"):
        return False
    link = upi_link(dep["amount"], dep["code"])
    if not link:
        return False
    caption = (f"{cget('qrCaption')}\n\n💵 <b>{money(dep['amount'])}</b> · "
               f"<code>{esc(dep['code'])}</code>")
    buf = generate_qr(link)
    if buf:
        buf.name = f"{dep['code']}.png"
        try:
            await bot.send_photo(chat_id=chat_id, photo=InputFile(buf),
                                 caption=caption, parse_mode=ParseMode.HTML)
            return True
        except TelegramError as e:
            log.warning("qr photo failed, falling back to text: %s", e)
    with contextlib.suppress(TelegramError, ValueError):
        await bot.send_message(
            chat_id=chat_id,
            text=f"<code>{qr_text(link)}</code>\n\n{caption}",
            parse_mode=ParseMode.HTML,
        )
        return True
    return False


def get_debt(uid: int) -> float:
    r = q1("SELECT amount FROM debts WHERE user_id=?", (uid,))
    return round(float(r["amount"]), 2) if r else 0.0


def add_debt(uid: int, amount: float, note: str = "", by: int = 0) -> float:
    ex(
        "INSERT INTO debts(user_id,amount,note,given_by,updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET amount=amount+excluded.amount, "
        "note=excluded.note, updated_at=excluded.updated_at",
        (uid, float(amount), note, by, now()),
    )
    return get_debt(uid)


def clear_debt(uid: int, amount: float) -> float:

    owed = get_debt(uid)
    if owed <= 0:
        return 0.0
    paid = min(owed, amount)
    left = round(owed - paid, 2)
    if left <= 0:
        ex("DELETE FROM debts WHERE user_id=?", (uid,))
    else:
        ex("UPDATE debts SET amount=?, updated_at=? WHERE user_id=?", (left, now(), uid))
    return round(paid, 2)


def alert_subscribe(uid: int, country: str) -> bool:
    c = (country or "").strip().lower()
    if q1("SELECT 1 FROM stock_alerts WHERE user_id=? AND country=?", (uid, c)):
        ex("DELETE FROM stock_alerts WHERE user_id=? AND country=?", (uid, c))
        return False
    ex(
        "INSERT OR IGNORE INTO stock_alerts(user_id,country,created_at) VALUES(?,?,?)",
        (uid, c, now()),
    )
    return True


def alert_subscribed(uid: int, country: str) -> bool:
    return bool(
        q1(
            "SELECT 1 FROM stock_alerts WHERE user_id=? AND country=?",
            (uid, (country or "").strip().lower()),
        )
    )


def alert_take_subscribers(country: str) -> list[int]:
    c = (country or "").strip().lower()
    rows = q("SELECT user_id FROM stock_alerts WHERE country=?", (c,))
    ex("DELETE FROM stock_alerts WHERE country=?", (c,))
    return [r["user_id"] for r in rows]


async def announce_restock(country: str) -> int:

    if not feat("restockAlert"):
        return 0
    subs = alert_take_subscribers(country)
    if not subs:
        return 0
    r = q1(
        "SELECT MIN(price) p, COUNT(*) c FROM stock WHERE country=? AND sold=0", (country,)
    )
    if not r or not r["c"]:
        return 0
    text = (
        f"{divider()}\n🚨 <b>{esc(cget('restockTitle')).upper()}</b>\n{divider()}\n\n"
        f"{flag_for(country)} <b>{esc(country)}</b> is available again.\n\n"
        f"💰 <b>Price:</b> {money(price_of(country))}\n"
        f"📦 <b>In stock:</b> {r['c']}"
    )
    kb = InlineKeyboardMarkup([[B("🛒 Buy now", f"buy:{country}", 22)]])
    sent = 0
    for uid in subs:
        with contextlib.suppress(TelegramError):
            await _APP.bot.send_message(
                chat_id=uid, text=premiumize(text), reply_markup=kb, parse_mode=ParseMode.HTML
            )
            sent += 1
        await asyncio.sleep(0.04)
    await send_log("stock", f"🔔 <b>Restock alert</b> — {esc(country)} → {sent} users")
    return sent


def active_giveaway() -> sqlite3.Row | None:
    if not feat("giveaway"):
        return None
    return q1("SELECT * FROM giveaways WHERE active=1 ORDER BY id DESC LIMIT 1")


def giveaway_taken(gid: int) -> int:
    return q1("SELECT COUNT(*) c FROM giveaway_claims WHERE giveaway_id=?", (gid,))["c"]


def giveaway_claimed_by(gid: int, uid: int) -> bool:
    return bool(
        q1("SELECT 1 FROM giveaway_claims WHERE giveaway_id=? AND user_id=?", (gid, uid))
    )


def giveaway_text(gv: sqlite3.Row) -> str:
    taken = giveaway_taken(gv["id"])
    left = max(0, gv["slots"] - taken)
    bar = "🟩" * min(left, 10) + "⬜️" * max(0, min(gv["slots"], 10) - min(left, 10))
    lines = [
        f"{divider()}\n🎉 <b>{esc(cget('giveawayTitle')).upper()}</b>\n{divider()}",
        "",
        cget("giveawayBody"),
        "",
        f"💰 <b>Prize:</b> {money(gv['amount'])} each",
        f"🎟 <b>Slots left:</b> {left} of {gv['slots']}",
        bar,
    ]
    if gv["note"]:
        lines += ["", f"<i>{esc(gv['note'])}</i>"]
    return "\n".join(lines)


def giveaway_kb(gv: sqlite3.Row) -> InlineKeyboardMarkup:
    left = max(0, gv["slots"] - giveaway_taken(gv["id"]))
    label = "🎁 Claim now" if left else "🏁 All claimed"
    return InlineKeyboardMarkup(
        [[ColorButton(pad(label, 22), f"gvclaim:{gv['id']}", style="success")]]
    )


async def claim_giveaway(qy, uid: int, gid: int) -> None:
    gv = q1("SELECT * FROM giveaways WHERE id=?", (gid,))
    if not gv or not gv["active"]:
        await qy.answer(cget("giveawayDone"), show_alert=True)
        return
    if giveaway_claimed_by(gid, uid):
        await qy.answer("You already claimed this one.", show_alert=True)
        return
    if giveaway_taken(gid) >= gv["slots"]:
        ex("UPDATE giveaways SET active=0 WHERE id=?", (gid,))
        await qy.answer(cget("giveawayDone"), show_alert=True)
        return
    try:
        ex(
            "INSERT INTO giveaway_claims(giveaway_id,user_id,amount,claimed_at) VALUES(?,?,?,?)",
            (gid, uid, gv["amount"], now()),
        )
    except sqlite3.IntegrityError:
        await qy.answer("You already claimed this one.", show_alert=True)
        return
    credit(uid, float(gv["amount"]))
    ended = giveaway_taken(gid) >= gv["slots"]
    if ended:
        ex("UPDATE giveaways SET active=0 WHERE id=?", (gid,))
    await qy.answer(cget("giveawayGot"), show_alert=True)
    with contextlib.suppress(BadRequest, TelegramError):
        await qy.edit_message_text(
            giveaway_text(q1("SELECT * FROM giveaways WHERE id=?", (gid,))),
            reply_markup=giveaway_kb(q1("SELECT * FROM giveaways WHERE id=?", (gid,))),
            parse_mode=ParseMode.HTML,
        )
    await send_log(
        "admin", f"🎉 <b>Giveaway claim</b> {money(gv['amount'])} · user <code>{uid}</code>"
    )


async def launch_giveaway(amount: float, slots: int, note: str, by: int) -> int:
    ex("UPDATE giveaways SET active=0")
    gid = ex(
        "INSERT INTO giveaways(amount,slots,note,active,created_by,created_at)"
        " VALUES(?,?,?,1,?,?)",
        (float(amount), int(slots), note, by, now()),
    )
    gv = q1("SELECT * FROM giveaways WHERE id=?", (gid,))
    sent = 0
    for r in q("SELECT user_id FROM users WHERE banned=0"):
        with contextlib.suppress(TelegramError):
            await _APP.bot.send_message(
                chat_id=r["user_id"],
                text=premiumize(giveaway_text(gv)),
                reply_markup=giveaway_kb(gv),
                parse_mode=ParseMode.HTML,
            )
            sent += 1
        await asyncio.sleep(0.04)
    await send_log("admin", f"🎉 <b>Giveaway started</b> {money(amount)} × {slots} → {sent} users")
    return sent


def guide_pages() -> list[tuple[str, str]]:
    return [
        (cget("guide1Title"), cget("guide1Body")),
        (cget("guide2Title"), cget("guide2Body")),
        (cget("guide3Title"), cget("guide3Body")),
        (cget("guide4Title"), cget("guide4Body")),
    ]


def guide_kb(page: int) -> InlineKeyboardMarkup:
    pages = guide_pages()
    nav = []
    if page > 0:
        nav.append(B("◀ Back", f"guide:{page-1}", 10))
    nav.append(B(f"{page+1}/{len(pages)}", "noop", 6))
    if page + 1 < len(pages):
        nav.append(B("Next ▶", f"guide:{page+1}", 10))
    return InlineKeyboardMarkup([nav, [B("🛒 Browse Stock", "shop", 22)], [B("🏠 Home", "home")]])


def guide_text(page: int) -> str:
    pages = guide_pages()
    page = max(0, min(page, len(pages) - 1))
    title, bodytext = pages[page]
    return f"{divider()}\n<b>{esc(title).upper()}</b>\n{divider()}\n\n{bodytext}"


TUNNEL = {
    "url": "", "status": "not started", "error": "", "how": "",
    "attempts": 0, "since": 0.0,
}
_TUNNEL_PROC = None
WEB_PORT_USED = 0


def public_url() -> str:

    for candidate in (sget("miniapp_url"), PUBLIC_URL, TUNNEL["url"]):
        if candidate and candidate.startswith("https://"):
            return candidate.rstrip("/")
    return ""


def tunnel_port() -> int:
    return WEB_PORT_USED or WEB_PORT


def find_cloudflared() -> str:
    env = os.environ.get("CLOUDFLARED_BIN")
    if env and os.path.exists(env):
        return env
    local = os.path.join(BASE_DIR, "cloudflared")
    if os.path.exists(local):
        return local
    found = shutil.which("cloudflared")
    return found or ""


def download_cloudflared() -> str:

    import platform
    import urllib.request

    if not sys.platform.startswith("linux"):
        TUNNEL["error"] = f"auto-download only supports linux (this is {sys.platform})"
        return ""
    mach = platform.machine().lower()
    arch = "arm64" if ("aarch64" in mach or "arm64" in mach) else "amd64"
    target = os.path.join(BASE_DIR, "cloudflared")
    urls = [
        "https://github.com/cloudflare/cloudflared/releases/latest/download/"
        f"cloudflared-linux-{arch}",
        "https://github.com/cloudflare/cloudflared/releases/download/2024.8.2/"
        f"cloudflared-linux-{arch}",
    ]
    for url in urls:
        try:
            log.info("downloading cloudflared from %s", url)
            tmp = target + ".part"
            urllib.request.urlretrieve(url, tmp)
            if os.path.getsize(tmp) < 5_000_000:
                os.remove(tmp)
                continue
            os.replace(tmp, target)
            os.chmod(target, 0o755)
            return target
        except Exception as e:
            TUNNEL["error"] = f"download failed: {e}"
            log.warning("cloudflared download failed: %s", e)
    return ""


async def _read_url(proc, pattern: str, seconds: int) -> str:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
        except asyncio.TimeoutError:
            continue
        if not line:
            break
        text = line.decode("utf-8", "ignore")
        m = re.search(pattern, text)
        if m:
            return m.group(0)
    return ""


async def _try_cloudflared() -> str:
    global _TUNNEL_PROC
    binp = find_cloudflared() or await asyncio.to_thread(download_cloudflared)
    if not binp:
        return ""
    import subprocess

    TUNNEL["status"] = "starting cloudflared"
    _TUNNEL_PROC = await asyncio.create_subprocess_exec(
        binp, "tunnel", "--url", f"http://127.0.0.1:{tunnel_port()}", "--no-autoupdate",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    url = await _read_url(_TUNNEL_PROC, r"https://[a-z0-9-]+\.trycloudflare\.com", 60)
    if url:
        TUNNEL["how"] = "cloudflared"
        return url
    with contextlib.suppress(ProcessLookupError, AttributeError):
        _TUNNEL_PROC.terminate()
    _TUNNEL_PROC = None
    return ""


async def _try_localhost_run() -> str:

    global _TUNNEL_PROC
    if not shutil.which("ssh"):
        return ""
    import subprocess

    TUNNEL["status"] = "starting localhost.run"
    _TUNNEL_PROC = await asyncio.create_subprocess_exec(
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30",
        "-R", f"80:127.0.0.1:{tunnel_port()}", "nokey@localhost.run",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    url = await _read_url(_TUNNEL_PROC, r"https://[a-z0-9.-]+\.lhr\.life", 45)
    if url:
        TUNNEL["how"] = "localhost.run"
        return url
    with contextlib.suppress(ProcessLookupError, AttributeError):
        _TUNNEL_PROC.terminate()
    _TUNNEL_PROC = None
    return ""


async def start_tunnel(force: bool = False) -> str:

    manual = sget("miniapp_url")
    if not force and manual.startswith("https://") and "trycloudflare" not in manual \
            and "lhr.life" not in manual:
        TUNNEL.update(status="using the URL you set", url=manual, how="manual")
        return manual
    if PUBLIC_URL.startswith("https://") and not force:
        TUNNEL.update(status="using PUBLIC_URL", url=PUBLIC_URL, how="env")
        return PUBLIC_URL
    if not feat("autoTunnel") or sget("tunnel_enabled") != "1":
        TUNNEL["status"] = "auto-tunnel is switched off"
        return ""

    TUNNEL["attempts"] += 1
    for attempt in range(3):
        for starter in (_try_cloudflared, _try_localhost_run):
            try:
                url = await starter()
            except Exception as e:
                TUNNEL["error"] = f"{starter.__name__}: {e}"
                log.warning("tunnel %s failed: %s", starter.__name__, e)
                url = ""
            if url:
                TUNNEL.update(url=url, status="live", error="", since=time.time())
                sset("miniapp_url", url)
                log.info("MINI APP IS LIVE: %s  (%s)", url, TUNNEL["how"])
                with contextlib.suppress(TelegramError):
                    await _APP.bot.set_chat_menu_button(
                        menu_button=MenuButtonWebApp(text="Store", web_app=WebAppInfo(url=url))
                    )
                kb = InlineKeyboardMarkup(
                    [[ColorButton(pad("🛍 Open Store", 22),
                                  web_app=WebAppInfo(url=url), style="success")]]
                )
                for r in q("SELECT user_id FROM users"):
                    with contextlib.suppress(Exception):
                        await set_user_menu(_APP.bot, r["user_id"])
                for r in q("SELECT user_id FROM admins"):
                    with contextlib.suppress(TelegramError):
                        await _APP.bot.send_message(
                            chat_id=r["user_id"],
                            text=premiumize(
                                head("Mini app is live", TUNNEL["how"])
                                + "\n\nUse the button below — tapping a plain link opens "
                                "Telegram's browser instead, and the store cannot see who "
                                "you are that way."
                            ),
                            reply_markup=kb, parse_mode=ParseMode.HTML)
                return url
        if attempt < 2:
            log.info("tunnel attempt %d failed — retrying in 8s", attempt + 1)
            await asyncio.sleep(8)

    TUNNEL["status"] = "could not get a public URL"
    log.warning("mini app has no public URL — set one in Admin → Extras")
    await notify_admins(
        "⚠️ <b>Mini app could not go public</b>\n\n"
        f"The store is running on this machine at port {tunnel_port()}, but no https "
        "address could be opened, so Telegram cannot show the app.\n\n"
        "<b>Fix it either way:</b>\n"
        "• put the bot behind your own https domain, then set it in "
        "<b>Admin → Extras → Mini app URL</b>\n"
        "• or install cloudflared and use <b>Restart tunnel</b>\n\n"
        f"<i>Last error: {esc(TUNNEL['error'] or 'none reported')}</i>"
    )
    return ""


async def stop_tunnel() -> None:
    global _TUNNEL_PROC
    if _TUNNEL_PROC:
        with contextlib.suppress(ProcessLookupError, AttributeError):
            _TUNNEL_PROC.terminate()
        _TUNNEL_PROC = None
    TUNNEL["status"] = "stopped"


async def loop_tunnel_watch() -> None:

    if not feat("autoTunnel") or sget("tunnel_enabled") != "1":
        return
    if TUNNEL["how"] in ("manual", "env"):
        return
    alive = _TUNNEL_PROC is not None and _TUNNEL_PROC.returncode is None
    if TUNNEL["status"] == "live" and not alive:
        log.warning("tunnel died — restarting")
        TUNNEL["status"] = "restarting"
        await start_tunnel(force=True)
    elif TUNNEL["status"] not in ("live", "starting cloudflared", "starting localhost.run"):
        if not public_url():
            await start_tunnel()


def miniapp_status_text() -> str:
    url = public_url()
    lines = [
        head("Mini app", "Where the store page is served"),
        "",
        f"🖥 <b>Local server:</b> http://{WEB_HOST}:{tunnel_port()}",
        f"🌐 <b>Public URL:</b> {esc(url) if url else '— none yet —'}",
        f"📶 <b>Tunnel:</b> {esc(TUNNEL['status'])}"
        + (f" ({esc(TUNNEL['how'])})" if TUNNEL["how"] else ""),
    ]
    if TUNNEL["error"]:
        lines.append(f"⚠️ <b>Last error:</b> {esc(TUNNEL['error'][:160])}")
    binp = find_cloudflared()
    lines.append(f"📦 <b>cloudflared:</b> {esc(binp) if binp else 'not downloaded yet'}")
    if url:
        lines += ["", "✅ <i>The Open Store button and the menu button both work.</i>"]
    else:
        lines += [
            "",
            "❌ <i>Telegram needs an https address before it will open a mini app.</i>",
            "Use <b>Restart tunnel</b>, or set your own domain with <b>Mini app URL</b>.",
        ]
    return "\n".join(lines)


def miniapp_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [B("🔄 Restart tunnel", "tunnel", 22)],
            [B("🌐 Set mini app URL", "set:miniapp_url", 22)],
            [B("🧹 Clear URL and retry", "tunnelclear", 22)],
            [B("◀ Extras", "a:extras", 22)],
        ]
    )


OTP_PATTERNS = [
    r"login code[:\s]+(\d{4,6})",
    r"(\d{4,6})\s+is your login code",
    r"your code[:\s]+(\d{4,6})",
    r"code[:\s]+(\d{4,6})",
    r"verification code[:\s]+(\d{4,6})",
    r"do not give this code[^\d]*(\d{5,6})",
    r"^(\d{5,6})\b",
    r"\b(\d{5,6})\b",
]


def extract_otp(text: str) -> str | None:
    if not text:
        return None
    for pat in OTP_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1)
    return None


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d+]", "", raw or "")
    if not digits:
        return ""
    if not digits.startswith("+"):
        digits = "+" + digits.lstrip("+")
    return digits


def otp_window_left(order_id: str) -> int:

    o = q1("SELECT created_at FROM orders WHERE id=?", (order_id,))
    if not o:
        return 0
    mins = int(sgetf("otp_window_minutes", 60))
    try:
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(o["created_at"])).total_seconds() / 60
    except (TypeError, ValueError):
        return 0
    return max(0, int(mins - age))


def otp_quota_left(order_id: str) -> int:
    used = q1("SELECT COUNT(*) c FROM otp_log WHERE order_id=?", (order_id,))["c"]
    return max(0, int(sgetf("otp_max_per_order", 2)) - used)


def parse_session(payload: str) -> dict:

    out = {"phone": "", "session": "", "twofa": ""}
    if not payload:
        return out
    parts = [p.strip() for p in re.split(r"[|;]", payload) if p.strip()]
    for p in parts:
        if re.fullmatch(r"\+?\d[\d\s\-]{6,}", p):
            out["phone"] = normalize_phone(p)
        elif len(p) > 40 and " " not in p:
            out["session"] = p
        elif p.lower().startswith(("2fa", "pass", "pwd")):
            out["twofa"] = p.split(":", 1)[-1].strip()
    if not out["phone"] and parts:
        out["phone"] = normalize_phone(parts[0])
    return out


async def fetch_live_otp(order_id: str) -> str | None:

    if not feat("liveOtp"):
        return None
    o = q1("SELECT * FROM orders WHERE id=?", (order_id,))
    if not o:
        return None
    st = q1("SELECT payload FROM stock WHERE id=?", (o["stock_id"],))
    info = parse_session(st["payload"] if st else "")
    if not info["session"]:
        return None
    api_id, api_hash = sget("otp_api_id"), sget("otp_api_hash")
    if not api_id or not api_hash:
        return None
    try:
        from pyrogram import Client
    except ImportError:
        log.info("pyrogram not installed — live OTP unavailable")
        return None
    try:
        app = Client(
            name=f"otp_{order_id}",
            api_id=int(api_id),
            api_hash=api_hash,
            session_string=info["session"],
            in_memory=True,
        )
        await app.start()
        code = None
        async for msg in app.get_chat_history(777000, limit=5):
            code = extract_otp(msg.text or msg.caption or "")
            if code:
                break
        await app.stop()
        return code
    except Exception as e:
        log.warning("live otp failed for %s: %s", order_id, e)
        return None


async def deliver_otp(qy, uid: int, order_id: str) -> None:

    o = q1("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, uid))
    if not o:
        await qy.answer("Order not found", show_alert=True)
        return
    if otp_window_left(order_id) <= 0:
        await qy.answer("The handover window for this order has ended.", show_alert=True)
        return
    if otp_quota_left(order_id) <= 0:
        await qy.answer(cget("otpLimit"), show_alert=True)
        return
    await qy.answer("Fetching the code…")
    code = await fetch_live_otp(order_id)
    source = "live from the account"
    if not code:
        code = o["otp"]
        source = "last code on file"
    if not code:
        st = q1("SELECT payload FROM stock WHERE id=?", (o["stock_id"],))
        info = parse_session(st["payload"] if st else "")
        body = [
            f"{divider()}\n<b>LOGIN DETAILS</b>\n{divider()}",
            "",
            f"📱 <b>Number:</b> <code>{esc(info['phone'] or '—')}</code>",
        ]
        if info["twofa"]:
            body.append(f"🔐 <b>2FA password:</b> <code>{esc(info['twofa'])}</code>")
        body.append(
            "\n<i>Live code forwarding is not configured, so request the code in Telegram "
            "and it will arrive on the number itself.</i>"
        )
        await qy.edit_message_text(
            premiumize("\n".join(body)),
            reply_markup=InlineKeyboardMarkup([[B("◀ Back", f"ord:{order_id}", 22)]]),
            parse_mode=ParseMode.HTML,
        )
        return

    ex("UPDATE orders SET otp=? WHERE id=?", (code, order_id))
    ex(
        "INSERT INTO otp_log(order_id,user_id,phone,code,sent_at) VALUES(?,?,?,?,?)",
        (order_id, uid, "", code, now()),
    )
    left = otp_quota_left(order_id)
    text = (
        f"{divider()}\n⚡ <b>{esc(cget('otpDelivered')).upper()}</b>\n{divider()}\n\n"
        f"<code>{esc(code)}</code>\n\n"
        f"🧾 <b>Order:</b> {esc(order_id)}\n"
        f"🔁 <b>Codes left:</b> {left}\n"
        f"<i>{esc(source)}</i>"
    )
    await qy.edit_message_text(
        premiumize(text),
        reply_markup=InlineKeyboardMarkup([[B("🔄 New code", f"otp:{order_id}", 10),
                                            B("◀ Back", f"ord:{order_id}", 10)]]),
        parse_mode=ParseMode.HTML,
    )
    await send_log("otp", f"🔑 <b>OTP</b> {esc(order_id)} · user <code>{uid}</code> · {esc(source)}")


def extras_kb() -> InlineKeyboardMarkup:
    rows = []
    if feat("giveaway"):
        rows.append([B("🎉 Start giveaway", "gvnew", 22)])
    if feat("udhar"):
        rows.append([B("🧾 Give udhar", "udnew", 16), B("📋 Who owes", "udlist", 16)])
    if feat("restockAlert"):
        n = q1("SELECT COUNT(*) c FROM stock_alerts")["c"]
        rows.append([B(f"🔔 Waiting list ({n})", "alist", 22)])
    if feat("menuBanner"):
        rows.append([B("🖼 Banner image", "set:banner_url", 16),
                     B("📣 Banner text", "set:banner_text", 16)])
    rows.append([B("😀 Emoji style", "emojipack", 16), B("🧾 Test QR", "qrtest", 16)])
    if feat("premiumEmoji"):
        rows.append([B("✨ Custom emoji ids", "set:premium_emoji_map", 22)])
    if feat("liveOtp"):
        rows.append([B("🔑 OTP api id", "set:otp_api_id", 16),
                     B("🔑 OTP api hash", "set:otp_api_hash", 16)])
        rows.append([B("🔢 Codes per order", "set:otp_max_per_order", 22)])
    rows.append([B("📧 Test Gmail login", "gmailtest", 22)])
    rows.append([B("🔢 Reference digits", "set:ref_digits", 16),
                 B("🪙 Unique paise", "tg:unique_paise", 16)])
    rows.append([B("📨 Payment sender", "set:gmail_sender", 22)])
    rows.append([B("🌐 Mini app status", "miniapp", 22)])
    if feat("autoTunnel"):
        rows.append([B("📶 Tunnel on/off", "tg:tunnel_enabled", 16),
                     B("🔄 Restart tunnel", "tunnel", 16)])
    rows.append([B("◀ Panel", "adm")])
    return InlineKeyboardMarkup(rows)


def extras_text() -> str:
    lines = [head("Extras", "Everything ported from your original bot"), ""]
    if feat("giveaway"):
        gv = active_giveaway()
        if gv:
            lines.append(
                f"🎉 <b>Giveaway:</b> {money(gv['amount'])} · "
                f"{gv['slots'] - giveaway_taken(gv['id'])} of {gv['slots']} left"
            )
        else:
            lines.append("🎉 <b>Giveaway:</b> none running")
    if feat("udhar"):
        r = q1("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM debts")
        lines.append(f"🧾 <b>Udhar outstanding:</b> {money(r['s'])} across {r['c']} users")
    if feat("restockAlert"):
        lines.append(
            f"🔔 <b>Restock subscribers:</b> {q1('SELECT COUNT(*) c FROM stock_alerts')['c']}"
        )
    if feat("payQR"):
        lines.append(f"🧾 <b>UPI QR:</b> {esc(qr_status())}")
    lines.append(f"🔖 <b>Reference:</b> {esc(sget('pay_prefix'))}"
                 + "#" * int(sgetf("ref_digits", 5)))
    lines.append(f"📨 <b>Only reads mail from:</b> {esc(sget('gmail_sender') or 'any sender')}")
    lines.append("🪙 <b>Unique paise:</b> "
                 + ("on — each request gets its own amount"
                    if sget("unique_paise", "1") == "1" else "off"))
    lines.append(f"😀 <b>Emoji style:</b> {esc(emoji_pack_name())}")
    if feat("premiumEmoji") and emoji_map():
        lines.append(f"✨ <b>Custom emoji ids:</b> {len(emoji_map())} mapped")
    if feat("liveOtp"):
        ready = bool(sget("otp_api_id") and sget("otp_api_hash"))
        lines.append(f"🔑 <b>Live OTP:</b> {'configured' if ready else 'needs api id + hash'}")
    url = public_url()
    lines.append(f"🌐 <b>Mini app:</b> {esc(url) if url else 'no public URL yet'}")
    if feat("autoTunnel"):
        lines.append(f"📶 <b>Tunnel:</b> {esc(TUNNEL['status'])}")
    if feat("colorBtn"):
        lines.append(f"🎨 <b>Coloured buttons:</b> {'live' if colored_buttons_active() else 'sent, client decides'}")
    return "\n".join(lines)


_OTP_SEEN: dict[str, str] = {}


async def loop_session_handover() -> None:


    mins = int(sgetf("otp_window_minutes", 60))
    if mins <= 0:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=mins)).isoformat()
    for o in q("SELECT * FROM orders WHERE status='delivered' AND created_at < ?", (cutoff,)):
        row = q1("SELECT payload FROM stock WHERE id=?", (o["stock_id"],))
        if not row or not (row["payload"] or "").strip():
            ex("UPDATE orders SET status='closed', closed_at=? WHERE id=?", (now(), o["id"]))
            continue
        info = parse_session(row["payload"])
        kept = info["phone"] + (f" | 2fa:{info['twofa']}" if info["twofa"] else "")
        ex("UPDATE stock SET payload=? WHERE id=?", (kept, o["stock_id"]))
        ex("UPDATE orders SET status='closed', closed_at=? WHERE id=?", (now(), o["id"]))
        with contextlib.suppress(Exception):
            await notify_user(o["user_id"], premiumize(
                head("Handover complete", o["id"])
                + "\n\nThe account is now yours alone — we no longer hold a copy of "
                "the login session.\n\n"
                + (f"📱 <b>Number:</b> <code>{esc(info['phone'])}</code>\n" if info["phone"] else "")
                + (f"🔐 <b>2FA:</b> <code>{esc(info['twofa'])}</code>" if info["twofa"] else "")))
        await send_log("otp", f"🔒 <b>Session released</b> {esc(o['id'])}")


async def loop_otp_forward() -> None:

    if not feat("otpForward") or not feat("liveOtp"):
        return
    if not (sget("otp_api_id") and sget("otp_api_hash")):
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    for o in q(
        "SELECT * FROM orders WHERE created_at > ? AND status='delivered' ORDER BY created_at DESC LIMIT 25",
        (cutoff,),
    ):
        if otp_quota_left(o["id"]) <= 0 or otp_window_left(o["id"]) <= 0:
            continue
        code = await fetch_live_otp(o["id"])
        if not code or _OTP_SEEN.get(o["id"]) == code:
            continue
        _OTP_SEEN[o["id"]] = code
        ex("UPDATE orders SET otp=? WHERE id=?", (code, o["id"]))
        ex(
            "INSERT INTO otp_log(order_id,user_id,phone,code,sent_at) VALUES(?,?,?,?,?)",
            (o["id"], o["user_id"], "", code, now()),
        )
        await notify_user(
            o["user_id"],
            premiumize(
                f"⚡ <b>{esc(cget('otpDelivered')).upper()}</b>\n\n<code>{esc(code)}</code>\n\n"
                f"🧾 {esc(o['id'])} · {flag_for(o['country'])} {esc(o['country'])}"
            ),
        )
        await send_log("otp", f"⚡ <b>OTP auto-forwarded</b> {esc(o['id'])}")


async def loop_auto_deposit() -> None:

    if not feat("autoDeposit"):
        return
    cap = sgetf("autodep_max", 0)
    if cap <= 0:
        return
    for d in q("SELECT * FROM deposits WHERE status='review' AND amount <= ?", (cap,)):
        total = settle_deposit(d, "auto")
        await notify_user(
            d["user_id"], f"✅ Your deposit of {money(d['amount'])} was approved automatically."
        )
        await send_log(
            "deposit",
            f"🤖 <b>Auto-approved</b> {money(d['amount'])} · user <code>{d['user_id']}</code>",
        )
        audit(0, "deposit", f"auto-approved #{d['id']} {money(total)}")


def rev_goal_line() -> str:

    if not feat("revGoal"):
        return ""
    goal = sgetf("rev_goal", 0)
    if goal <= 0:
        return ""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    got = float(
        q1(
            "SELECT COALESCE(SUM(price),0) s FROM orders WHERE substr(created_at,1,7)=?", (month,)
        )["s"]
    )
    pct = min(100, int(got / goal * 100))
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    return f"\n🎯 <b>Goal:</b> {bar} {pct}%  ({money(got)} of {money(goal)})"


def session_stats() -> dict:

    out = {
        "available": q1("SELECT COUNT(*) c FROM stock WHERE sold=0")["c"],
        "sold": q1("SELECT COUNT(*) c FROM stock WHERE sold=1")["c"],
        "dead": q1("SELECT COUNT(*) c FROM stock WHERE sold=2")["c"],
        "with_session": q1(
            "SELECT COUNT(*) c FROM stock WHERE sold=0 AND length(COALESCE(payload,''))>40"
        )["c"],
        "with_2fa": q1("SELECT COUNT(*) c FROM stock WHERE payload LIKE '%2fa%'")["c"],
        "countries": len(countries()),
    }
    out["total"] = out["available"] + out["sold"] + out["dead"]
    return out


def session_stats_text() -> str:
    st = session_stats()
    health = int(st["with_session"] / st["available"] * 100) if st["available"] else 0
    return "\n".join(
        [
            head("Session health", f"{st['total']} uploaded in total"),
            "",
            f"📦 <b>Available:</b> {st['available']}",
            f"✅ <b>Sold:</b> {st['sold']}",
            f"💀 <b>Failed health check:</b> {st['dead']}",
            f"🔑 <b>With a session string:</b> {st['with_session']} ({health}%)",
            f"🔐 <b>With 2FA:</b> {st['with_2fa']}",
            f"🌍 <b>Countries:</b> {st['countries']}",
        ]
    )


def emoji_pack_kb() -> InlineKeyboardMarkup:
    cur = emoji_pack_name()
    rows = [
        [B(("🟢 " if cur == k else "⚪️ ") + label, f"epack:{k}", 26)]
        for k, label in [
            ("premium", "Premium look — richer glyphs, no subscription"),
            ("classic", "Classic — the plain emoji set"),
            ("minimal", "Minimal — no icons at all"),
        ]
    ]
    rows.append([B("◀ Extras", "a:extras", 22)])
    return InlineKeyboardMarkup(rows)


def emoji_pack_text() -> str:
    sample = "💰 Wallet · 🛒 Buy · ✅ Verify · 🎁 Giveaway · 👑 Owner"
    lines = [head("Emoji style", "How icons look across the bot"), ""]
    for k in ("premium", "classic", "minimal"):
        was = sget("emoji_pack")
        sset("emoji_pack", k)
        lines.append(f"<b>{k}</b>  {esc(styled(sample))}")
        sset("emoji_pack", was)
    lines.append("\n<i>Premium style uses ordinary Unicode, so it shows for everyone — "
                 "no Telegram Premium needed.</i>")
    return "\n".join(lines)


DIAL_CODES = [
    ("91", "India"), ("62", "Indonesia"), ("55", "Brazil"), ("234", "Nigeria"),
    ("84", "Vietnam"), ("63", "Philippines"), ("92", "Pakistan"), ("880", "Bangladesh"),
    ("1", "USA"), ("44", "UK"), ("7", "Russia"), ("254", "Kenya"), ("20", "Egypt"),
    ("27", "South Africa"), ("60", "Malaysia"), ("66", "Thailand"), ("94", "Sri Lanka"),
    ("977", "Nepal"), ("98", "Iran"), ("90", "Turkey"), ("212", "Morocco"),
    ("233", "Ghana"), ("256", "Uganda"), ("255", "Tanzania"), ("251", "Ethiopia"),
    ("52", "Mexico"), ("54", "Argentina"), ("57", "Colombia"), ("51", "Peru"),
    ("58", "Venezuela"), ("56", "Chile"), ("380", "Ukraine"), ("48", "Poland"),
    ("49", "Germany"), ("33", "France"), ("39", "Italy"), ("34", "Spain"),
    ("31", "Netherlands"), ("46", "Sweden"), ("47", "Norway"), ("358", "Finland"),
    ("81", "Japan"), ("82", "South Korea"), ("86", "China"), ("65", "Singapore"),
    ("61", "Australia"), ("64", "New Zealand"), ("971", "UAE"), ("966", "Saudi Arabia"),
    ("974", "Qatar"), ("965", "Kuwait"), ("968", "Oman"), ("973", "Bahrain"),
    ("962", "Jordan"), ("961", "Lebanon"), ("964", "Iraq"), ("93", "Afghanistan"),
    ("95", "Myanmar"), ("855", "Cambodia"), ("856", "Laos"), ("976", "Mongolia"),
    ("242", "Congo"), ("225", "Ivory Coast"), ("221", "Senegal"), ("237", "Cameroon"),
    ("263", "Zimbabwe"), ("260", "Zambia"), ("265", "Malawi"), ("250", "Rwanda"),
]

LOGIN: dict[int, dict] = {}
LOGIN_TTL = 900


def country_from_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    for code, name in sorted(DIAL_CODES, key=lambda x: -len(x[0])):
        if digits.startswith(code):
            return name
    return ""


def clean_code(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def login_ready() -> tuple[bool, str]:
    try:
        import pyrogram
    except ImportError:
        return False, ("Pyrogram is not installed on the machine running the bot.\n\n"
                       "<code>pip install pyrogram tgcrypto</code>\n\n"
                       "then restart the bot.")
    if not sget("otp_api_id") or not sget("otp_api_hash"):
        return False, ("No API credentials saved yet.\n\n"
                       "Get an <b>api_id</b> and <b>api_hash</b> from my.telegram.org, then set "
                       "them in <b>Admin → Extras</b>.")
    return True, ""


async def login_cleanup(uid: int) -> None:
    st = LOGIN.pop(uid, None)
    if st and st.get("client"):
        with contextlib.suppress(Exception):
            await st["client"].disconnect()


async def loop_login_timeout() -> None:
    for uid, st in list(LOGIN.items()):
        if time.time() - st.get("started", 0) > LOGIN_TTL:
            await login_cleanup(uid)
            await notify_user(uid, "That login timed out. Start again from Stock → Add stock.")


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[B("✖ Cancel", "stcancel", 22)]])


def add_stock_kb() -> InlineKeyboardMarkup:
    rows = []
    if feat("manualLogin"):
        rows.append([B("📱 Login by number", "stlogin", 22)])
    rows.append([B("🔑 Paste session or list", "stpaste", 22)])
    rows.append([B("◀ Stock", "a:stock", 22)])
    return InlineKeyboardMarkup(rows)


def add_stock_text() -> str:
    ok, why = login_ready()
    lines = [head("Add stock", "Two ways to put an account in"), ""]
    lines.append("📱 <b>Login by number</b>")
    lines.append("The bot signs in for you: number, then the code, then the 2FA password "
                 "if the account has one. The session is saved automatically.")
    lines.append("")
    lines.append("🔑 <b>Paste session or list</b>")
    lines.append("One account per line:")
    lines.append("<code>Country | +code | price | session</code>")
    if not ok:
        lines += ["", "⚠️ <b>Login by number is not available</b>", why]
    return "\n".join(lines)


async def login_start(qy, ctx, uid: int) -> None:
    ok, why = login_ready()
    if not ok:
        await qy.edit_message_text(
            head("Cannot log in yet") + "\n\n" + why,
            reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
            parse_mode=ParseMode.HTML,
        )
        return
    await login_cleanup(uid)
    ctx.user_data["await"] = ("login_phone", None)
    await qy.edit_message_text(
        head("Step 1 of 3 · number", "Send the phone number with its country code.")
        + "\n\n<code>+919876543210</code>\n\n"
        "<i>Telegram sends a login code to that number. Be ready to read it.</i>",
        reply_markup=cancel_kb(),
        parse_mode=ParseMode.HTML,
    )


async def login_send_code(msg, ctx, uid: int, raw_phone: str) -> None:
    phone = normalize_phone(raw_phone)
    if len(re.sub(r"\D", "", phone)) < 7:
        ctx.user_data["await"] = ("login_phone", None)
        await msg.reply_text(
            head("That is not a number", "Send it with the country code, like +919876543210."),
            reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
        return
    if q1("SELECT 1 FROM stock WHERE payload LIKE ? AND sold=0", (f"%{phone}%",)):
        await msg.reply_text(
            head("Already in stock", f"{phone} is listed and unsold."),
            reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
            parse_mode=ParseMode.HTML)
        return

    from pyrogram import Client
    from pyrogram.errors import FloodWait, PhoneNumberBanned, PhoneNumberInvalid

    wait = await msg.reply_text("Asking Telegram for a code…")
    client = Client(
        name=f"add_{uid}_{int(time.time())}",
        api_id=int(sget("otp_api_id")),
        api_hash=sget("otp_api_hash"),
        in_memory=True,
    )
    try:
        await client.connect()
        sent = await client.send_code(phone)
    except PhoneNumberInvalid:
        await _safe_disconnect(client)
        ctx.user_data["await"] = ("login_phone", None)
        await wait.edit_text(
            head("Telegram rejected that number", "Check the country code and send it again."),
            reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
        return
    except PhoneNumberBanned:
        await _safe_disconnect(client)
        await wait.edit_text(
            head("Number is banned", "Telegram has banned this number. Nothing was added."),
            reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
            parse_mode=ParseMode.HTML)
        return
    except FloodWait as e:
        await _safe_disconnect(client)
        mins = int(getattr(e, "value", 60)) // 60 + 1
        await wait.edit_text(
            head("Too many attempts", f"Telegram asked us to wait about {mins} minute(s)."),
            reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
            parse_mode=ParseMode.HTML)
        return
    except Exception as e:
        await _safe_disconnect(client)
        log.warning("send_code failed: %s", e)
        await wait.edit_text(
            head("Could not send the code", str(e)[:180]),
            reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
            parse_mode=ParseMode.HTML)
        return

    LOGIN[uid] = {
        "client": client, "phone": phone, "hash": sent.phone_code_hash,
        "started": time.time(), "tries": 0, "msg": wait,
    }
    ctx.user_data["await"] = ("login_code", None)
    await wait.edit_text(
        head("Step 2 of 3 · code", f"A code was sent to {phone}.")
        + "\n\nSend it here. Spaces are fine, so <code>1 2 3 4 5</code> works.\n\n"
        "<i>Telegram may deliver it inside the app rather than by SMS.</i>",
        reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)


async def _safe_disconnect(client) -> None:
    with contextlib.suppress(Exception):
        await client.disconnect()


async def login_submit_code(msg, ctx, uid: int, raw_code: str) -> None:
    st = LOGIN.get(uid)
    if not st:
        await msg.reply_text(
            head("That login expired", "Start again from Stock → Add stock."),
            reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
            parse_mode=ParseMode.HTML)
        return
    code = clean_code(raw_code)
    if len(code) < 4:
        ctx.user_data["await"] = ("login_code", None)
        await msg.reply_text(
            head("That does not look like a code", "Send the digits Telegram sent you."),
            reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
        return

    from pyrogram.errors import (
        PhoneCodeExpired, PhoneCodeInvalid, SessionPasswordNeeded,
    )

    client = st["client"]
    try:
        await client.sign_in(st["phone"], st["hash"], code)
    except SessionPasswordNeeded:
        ctx.user_data["await"] = ("login_2fa", None)
        await msg.reply_text(
            head("Step 3 of 3 · 2FA", "This account has a cloud password.")
            + "\n\nSend the password. It is stored with the account so buyers get it.",
            reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
        return
    except PhoneCodeInvalid:
        st["tries"] += 1
        if st["tries"] >= 3:
            await login_cleanup(uid)
            await msg.reply_text(
                head("Wrong code three times", "Start again from Stock → Add stock."),
                reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
                parse_mode=ParseMode.HTML)
            return
        ctx.user_data["await"] = ("login_code", None)
        await msg.reply_text(
            head("Wrong code", f"Try again — {3 - st['tries']} attempt(s) left."),
            reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
        return
    except PhoneCodeExpired:
        await login_cleanup(uid)
        await msg.reply_text(
            head("The code expired", "Start again and enter it a bit quicker."),
            reply_markup=InlineKeyboardMarkup([[B("📱 Try again", "stlogin", 22)]]),
            parse_mode=ParseMode.HTML)
        return
    except Exception as e:
        await login_cleanup(uid)
        log.warning("sign_in failed: %s", e)
        await msg.reply_text(
            head("Sign-in failed", str(e)[:180]),
            reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
            parse_mode=ParseMode.HTML)
        return

    await login_finish(msg, ctx, uid, "")


async def login_submit_2fa(msg, ctx, uid: int, password: str) -> None:
    st = LOGIN.get(uid)
    if not st:
        await msg.reply_text(
            head("That login expired", "Start again from Stock → Add stock."),
            reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
            parse_mode=ParseMode.HTML)
        return

    from pyrogram.errors import PasswordHashInvalid

    try:
        await st["client"].check_password(password.strip())
    except PasswordHashInvalid:
        st["tries"] = st.get("tries", 0) + 1
        if st["tries"] >= 3:
            await login_cleanup(uid)
            await msg.reply_text(
                head("Wrong password three times", "Start again from Stock → Add stock."),
                reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
                parse_mode=ParseMode.HTML)
            return
        ctx.user_data["await"] = ("login_2fa", None)
        await msg.reply_text(
            head("Wrong password", f"Try again — {3 - st['tries']} attempt(s) left."),
            reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
        return
    except Exception as e:
        await login_cleanup(uid)
        log.warning("check_password failed: %s", e)
        await msg.reply_text(
            head("Could not sign in", str(e)[:180]),
            reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
            parse_mode=ParseMode.HTML)
        return

    await login_finish(msg, ctx, uid, password.strip())


async def login_finish(msg, ctx, uid: int, twofa: str) -> None:
    st = LOGIN.get(uid)
    if not st:
        return
    client = st["client"]
    phone = st["phone"]
    session = ""
    me_name = ""
    try:
        session = await client.export_session_string()
        me = await client.get_me()
        me_name = (me.first_name or "") + (f" @{me.username}" if me.username else "")
    except Exception as e:
        log.warning("export failed: %s", e)
    await _safe_disconnect(client)
    LOGIN.pop(uid, None)
    ctx.user_data.pop("await", None)

    if not session:
        await msg.reply_text(
            head("Signed in, but no session", "Nothing was added. Try again."),
            reply_markup=InlineKeyboardMarkup([[B("📱 Try again", "stlogin", 22)]]),
            parse_mode=ParseMode.HTML)
        return

    country = country_from_phone(phone) or "Unknown"
    price = sgetf("default_price", 35)
    payload = f"{phone} | {session}" + (f" | 2fa:{twofa}" if twofa else "")
    sid = ex(
        "INSERT INTO stock(country,flag,code,price,payload,added_at) VALUES(?,?,?,?,?,?)",
        (country, flag_for(country), "+" + re.sub(r"\D", "", phone)[:3], price, payload, now()),
    )
    audit(uid, "stock", f"login {phone} → {country}")
    await send_log("stock", f"📦 <b>Account added by login</b>\n{esc(phone)} · {esc(country)}")
    with contextlib.suppress(Exception):
        await announce_restock(country)

    lines = [
        head("Added", "The account is in stock"),
        "",
        f"📱 <b>Number:</b> <code>{esc(phone)}</code>",
        f"{flag_for(country)} <b>Country:</b> {esc(country)}",
        f"💵 <b>Price:</b> {money(price)}",
        f"🔐 <b>2FA:</b> {'saved' if twofa else 'none on this account'}",
        f"🔑 <b>Session:</b> saved, {len(session)} characters",
    ]
    if me_name:
        lines.append(f"👤 <b>Account name:</b> {esc(me_name)}")
    lines.append("\n<i>Live OTP forwarding will work for whoever buys it.</i>")
    kb = InlineKeyboardMarkup([
        [B("💵 Set price", f"stsetprice:{sid}", 16), B("🌍 Set country", f"stsetc:{sid}", 16)],
        [B("📱 Add another", "stlogin", 22)],
        [B("◀ Stock", "a:stock", 22)],
    ])
    await msg.reply_text(premiumize("\n".join(lines)), reply_markup=kb, parse_mode=ParseMode.HTML)


async def login_cancel(qy, ctx, uid: int) -> None:
    await login_cleanup(uid)
    ctx.user_data.pop("await", None)
    await qy.edit_message_text(
        head("Cancelled", "Nothing was added."),
        reply_markup=InlineKeyboardMarkup([[B("◀ Stock", "a:stock", 22)]]),
        parse_mode=ParseMode.HTML)


def stock_item_text(sid: int) -> str:
    row = q1("SELECT * FROM stock WHERE id=?", (sid,))
    if not row:
        return head("Gone", "That item is no longer there.")
    info = parse_session(row["payload"] or "")
    state = {0: "available", 1: "sold", 2: "failed health check"}.get(row["sold"], "unknown")
    lines = [
        head(f"Stock #{sid}", f"{flag_for(row['country'])} {row['country']}"),
        "",
        f"💵 <b>Price:</b> {money(row['price'])}",
        f"📌 <b>State:</b> {state}",
    ]
    if info["phone"]:
        lines.append(f"📱 <b>Number:</b> <code>{esc(info['phone'])}</code>")
    if info["twofa"]:
        lines.append(f"🔐 <b>2FA:</b> <code>{esc(info['twofa'])}</code>")
    lines.append(f"🔑 <b>Session:</b> {'yes' if info['session'] else 'not stored'}")
    lines.append(f"🕒 <b>Added:</b> {esc((row['added_at'] or '')[:16].replace('T', ' '))}")
    return premiumize("\n".join(lines))


def stock_item_kb(sid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [B("💵 Set price", f"stsetprice:{sid}", 16), B("🌍 Set country", f"stsetc:{sid}", 16)],
        [B("🗑 Delete this", f"stdel:{sid}", 22)],
        [B("◀ Stock", "a:stock", 22)],
    ])


MINIAPP_HTML_B64 = (
    "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIiBkYXRhLWRlc2lnbj0ic3Vuc2V0Ij4KPGhl"
    "YWQ+CjxtZXRhIGNoYXJzZXQ9InV0Zi04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQiIGNvbnRlbnQ9"
    "IndpZHRoPWRldmljZS13aWR0aCxpbml0aWFsLXNjYWxlPTEsdmlld3BvcnQtZml0PWNvdmVyLG1h"
    "eGltdW0tc2NhbGU9MSI+CjxzY3JpcHQgc3JjPSJodHRwczovL3RlbGVncmFtLm9yZy9qcy90ZWxl"
    "Z3JhbS13ZWItYXBwLmpzIj48L3NjcmlwdD4KPHRpdGxlPlpvcm8gVEcgU3RvcmUg4oCUIFN0b3Jl"
    "IE1pbmkgQXBwIChEZXNpZ24gNyk8L3RpdGxlPgo8c3R5bGU+Cjpyb290ewogIC0tYmc6I2ZmZjVm"
    "MDsgLS1iZzI6I2ZmZmZmZjsgLS1jYXJkOiNmZmZmZmY7IC0tY2FyZDI6I2ZmZjBlODsKICAtLWxp"
    "bmU6I2ZmZTBkMjsgLS10eDojMzEyMDFhOyAtLXR4MjojOGE2ZDYxOyAtLWFjYzojZmY2YTNkOyAt"
    "LWFjYzI6I2ZmMmQ3ODsKICAtLW9rOiMwZmE5Njg7IC0td2FybjojZjU5ZTBiOyAtLWJhZDojZWY0"
    "NDQ0OwogIC0tcjoyNHB4OyAtLXJzOjE4cHg7IC0tYnRuaDo0NHB4OyAtLWZvbnQ6LWFwcGxlLXN5"
    "c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsJ1NlZ29lIFVJJyxSb2JvdG8sc2Fucy1zZXJpZjsgLS1m"
    "aGVhZDotYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCwnU2Vnb2UgVUknLFJvYm90byxz"
    "YW5zLXNlcmlmOwogIC0tc2hhZG93OjAgOHB4IDI0cHggcmdiYSgyNTUsMTA2LDYxLC4xMik7IC0t"
    "cGlsbGdyYWQ6bGluZWFyLWdyYWRpZW50KDEzNWRlZywjZmY2YTNkLCNmZjJkNzgpOyAtLWJ0bmJn"
    "OiNmZjVhNDY7Cn0KKntib3gtc2l6aW5nOmJvcmRlci1ib3g7LXdlYmtpdC10YXAtaGlnaGxpZ2h0"
    "LWNvbG9yOnRyYW5zcGFyZW50fQpodG1sLGJvZHl7bWFyZ2luOjA7cGFkZGluZzowfQpib2R5e2Jh"
    "Y2tncm91bmQ6dmFyKC0tYmcpO2NvbG9yOnZhcigtLXR4KTtmb250LWZhbWlseTp2YXIoLS1mb250"
    "KTtmb250LXNpemU6MTVweDtsaW5lLWhlaWdodDoxLjQ1O21pbi1oZWlnaHQ6MTAwdmg7b3ZlcmZs"
    "b3cteDpoaWRkZW47cGFkZGluZy10b3A6MzJweDt9CmJvZHk6YmVmb3Jle2NvbnRlbnQ6IiI7cG9z"
    "aXRpb246Zml4ZWQ7aW5zZXQ6MDtwb2ludGVyLWV2ZW50czpub25lO3otaW5kZXg6MDtiYWNrZ3Jv"
    "dW5kOnJhZGlhbC1ncmFkaWVudCg3MCUgNDAlIGF0IDgwJSAwJSxyZ2JhKDI1NSw0NSwxMjAsLjE0"
    "KSx0cmFuc3BhcmVudCA3MCUpLHJhZGlhbC1ncmFkaWVudCg2MCUgNDAlIGF0IDAlIDIwJSxyZ2Jh"
    "KDI1NSwxMDYsNjEsLjE2KSx0cmFuc3BhcmVudCA3MCUpfQo6Oi13ZWJraXQtc2Nyb2xsYmFye3dp"
    "ZHRoOjA7aGVpZ2h0OjB9CiNzaGVsbHtwb3NpdGlvbjpyZWxhdGl2ZTt6LWluZGV4OjE7bWF4LXdp"
    "ZHRoOjUyMHB4O21hcmdpbjowIGF1dG87bWluLWhlaWdodDoxMDB2aDtwYWRkaW5nOjAgMTZweCAx"
    "MTJweH0KLmhpZGRlbntkaXNwbGF5Om5vbmUhaW1wb3J0YW50fQoKI3RvcGJhcntwb3NpdGlvbjpz"
    "dGlja3k7dG9wOjMycHg7ei1pbmRleDo0MDtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVy"
    "O2dhcDoxMHB4O3BhZGRpbmc6MTRweCAwIDEycHg7YmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQo"
    "dmFyKC0tYmcpIDcyJSx0cmFuc3BhcmVudCl9Ci5sb2dve3dpZHRoOjM4cHg7aGVpZ2h0OjM4cHg7"
    "Ym9yZGVyLXJhZGl1czoxNHB4O2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7ZmxleDow"
    "IDAgYXV0bztiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojZmZmZmZmO2ZvbnQtd2Vp"
    "Z2h0OjgwMDtmb250LXNpemU6LjlyZW07bGV0dGVyLXNwYWNpbmc6LS4wM2VtO30KLmJyYW5ke21p"
    "bi13aWR0aDowO2ZsZXg6MX0KLmJyYW5kIGJ7ZGlzcGxheTpibG9jaztmb250LWZhbWlseTp2YXIo"
    "LS1maGVhZCk7Zm9udC1zaXplOi45NXJlbTtmb250LXdlaWdodDo4MDA7bGV0dGVyLXNwYWNpbmc6"
    "LS4wM2VtO3doaXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4dC1vdmVyZmxvdzpl"
    "bGxpcHNpc30KLmJyYW5kIHNwYW57ZGlzcGxheTpibG9jaztmb250LXNpemU6LjY2cmVtO2NvbG9y"
    "OnZhcigtLXR4Mik7bGV0dGVyLXNwYWNpbmc6LjA2ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNl"
    "fQouYmFsY2hpcHtkaXNwbGF5OmlubGluZS1mbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6N3B4"
    "O2hlaWdodDozOHB4O3BhZGRpbmc6MCAxM3B4O2JvcmRlci1yYWRpdXM6MTRweDtiYWNrZ3JvdW5k"
    "OnZhcigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Y29sb3I6dmFyKC0tdHgp"
    "O2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtd2VpZ2h0OjcwMDtmb250LXNpemU6Ljg1cmVt"
    "O3doaXRlLXNwYWNlOm5vd3JhcDtjdXJzb3I6cG9pbnRlcjt9Ci5iYWxjaGlwIC5wbHVze2Rpc3Bs"
    "YXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7d2lkdGg6MjBweDtoZWlnaHQ6MjBweDtib3JkZXIt"
    "cmFkaXVzOjdweDtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojZmZmZmZmO2ZvbnQt"
    "d2VpZ2h0OjgwMDtmb250LXNpemU6LjhyZW07bGluZS1oZWlnaHQ6MX0KCi5jYXJke2JhY2tncm91"
    "bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVz"
    "OnZhcigtLXIpO3BhZGRpbmc6MTZweDttYXJnaW4tYm90dG9tOjEycHg7Ym94LXNoYWRvdzp2YXIo"
    "LS1zaGFkb3cpO30KLmNhcmQudGlnaHR7cGFkZGluZzoxM3B4fQouY2FyZCBoM3ttYXJnaW46MCAw"
    "IDNweDtmb250LWZhbWlseTp2YXIoLS1maGVhZCk7Zm9udC1zaXplOi45MnJlbTtmb250LXdlaWdo"
    "dDo4MDA7bGV0dGVyLXNwYWNpbmc6LS4wM2VtfQouY2FyZCAuc3Vie21hcmdpbjowO2NvbG9yOnZh"
    "cigtLXR4Mik7Zm9udC1zaXplOi43OHJlbX0KLnNlY3tkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6"
    "Y2VudGVyO2p1c3RpZnktY29udGVudDpzcGFjZS1iZXR3ZWVuO2dhcDoxMHB4O21hcmdpbjoxOHB4"
    "IDJweCAxMHB4fQouc2VjIGgye21hcmdpbjowO2ZvbnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250"
    "LXNpemU6LjgycmVtO2ZvbnQtd2VpZ2h0OjgwMDtsZXR0ZXItc3BhY2luZzouMTRlbTt0ZXh0LXRy"
    "YW5zZm9ybTp1cHBlcmNhc2U7Y29sb3I6dmFyKC0tdHgyKX0KLnNlYyBhe2ZvbnQtc2l6ZTouNzVy"
    "ZW07Y29sb3I6dmFyKC0tYWNjKTt0ZXh0LWRlY29yYXRpb246bm9uZTtmb250LXdlaWdodDo2MDA7"
    "d2hpdGUtc3BhY2U6bm93cmFwO2N1cnNvcjpwb2ludGVyfQoKLyogb25lIGJ1dHRvbiBzeXN0ZW0g"
    "4oCUIHRleHQgY2FuIG5ldmVyIG92ZXJmbG93ICovCi5idG57ZGlzcGxheTppbmxpbmUtZmxleDth"
    "bGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjtnYXA6N3B4O2hlaWdodDp2"
    "YXIoLS1idG5oKTttaW4td2lkdGg6MDttYXgtd2lkdGg6MTAwJTtwYWRkaW5nOjAgMTZweDtib3Jk"
    "ZXI6MDtjdXJzb3I6cG9pbnRlcjtib3JkZXItcmFkaXVzOjE4cHg7Zm9udC1mYW1pbHk6dmFyKC0t"
    "Zm9udCk7Zm9udC1zaXplOi44M3JlbTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LjAx"
    "ZW07d2hpdGUtc3BhY2U6bm93cmFwO292ZXJmbG93OmhpZGRlbjt0ZXh0LW92ZXJmbG93OmVsbGlw"
    "c2lzO2xpbmUtaGVpZ2h0OjE7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmZm"
    "Zjt0cmFuc2l0aW9uOnRyYW5zZm9ybSAuMTVzLGZpbHRlciAuMTVzO30KLmJ0bjphY3RpdmV7dHJh"
    "bnNmb3JtOnNjYWxlKC45Nyl9Ci5idG46aG92ZXJ7ZmlsdGVyOmJyaWdodG5lc3MoMS4wNil9Ci5i"
    "dG4gc3Zne3dpZHRoOjE2cHg7aGVpZ2h0OjE2cHg7ZmxleDowIDAgYXV0b30KLmJ0bi5ibG9ja3tk"
    "aXNwbGF5OmZsZXg7d2lkdGg6MTAwJX0KLmJ0bi5zbXtoZWlnaHQ6MzZweDtwYWRkaW5nOjAgMTJw"
    "eDtmb250LXNpemU6Ljc1cmVtO2JvcmRlci1yYWRpdXM6MTNweH0KLmJ0bi54c3toZWlnaHQ6MzBw"
    "eDtwYWRkaW5nOjAgMTBweDtmb250LXNpemU6LjdyZW07Ym9yZGVyLXJhZGl1czoxM3B4fQouYnRu"
    "Lmdob3N0e2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7Y29sb3I6dmFyKC0tdHgpO2JvcmRlcjoxcHgg"
    "c29saWQgdmFyKC0tbGluZSl9Ci5idG4uc29mdHtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xv"
    "cjp2YXIoLS10eCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKX0KLmJ0bi5iYWR7YmFja2dy"
    "b3VuZDp0cmFuc3BhcmVudDtjb2xvcjp2YXIoLS1iYWQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0t"
    "YmFkKX0KLmJ0bltkaXNhYmxlZF17b3BhY2l0eTouNDU7cG9pbnRlci1ldmVudHM6bm9uZX0KLmJ0"
    "bnJvd3tkaXNwbGF5OmZsZXg7Z2FwOjhweH0KLmJ0bnJvdz4uYnRue2ZsZXg6MSAxIDA7bWluLXdp"
    "ZHRoOjB9CgoucGlsbHtkaXNwbGF5OmlubGluZS1mbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6"
    "NXB4O2hlaWdodDoyNHB4O3BhZGRpbmc6MCA5cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2ZvbnQtc2l6"
    "ZTouNjZyZW07Zm9udC13ZWlnaHQ6NzAwO2xldHRlci1zcGFjaW5nOi4wNGVtO2JhY2tncm91bmQ6"
    "dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4Mik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5l"
    "KTt3aGl0ZS1zcGFjZTpub3dyYXB9Ci5waWxsLm9ue2NvbG9yOnZhcigtLW9rKTtib3JkZXItY29s"
    "b3I6cmdiYSgxNSwxNjksMTA0LC40KX0KLnBpbGwuaG90e2NvbG9yOnZhcigtLXdhcm4pO2JvcmRl"
    "ci1jb2xvcjpyZ2JhKDI0NSwxNTgsMTEsLjQpfQoucGlsbC5vZmZ7Y29sb3I6dmFyKC0tYmFkKTti"
    "b3JkZXItY29sb3I6cmdiYSgyMzksNjgsNjgsLjQpfQouZG90e3dpZHRoOjdweDtoZWlnaHQ6N3B4"
    "O2JvcmRlci1yYWRpdXM6NTAlO2JhY2tncm91bmQ6dmFyKC0tb2spO2JveC1zaGFkb3c6MCAwIDhw"
    "eCB2YXIoLS1vayl9Ci5rdntkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnkt"
    "Y29udGVudDpzcGFjZS1iZXR3ZWVuO2dhcDoxMnB4O3BhZGRpbmc6MTBweCAwO2JvcmRlci1ib3R0"
    "b206MXB4IHNvbGlkIHZhcigtLWxpbmUpO2ZvbnQtc2l6ZTouODJyZW07Y29sb3I6dmFyKC0tdHgy"
    "KX0KLmt2Omxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRvbTowfQoua3YgYntjb2xvcjp2YXIoLS10eCk7"
    "Zm9udC13ZWlnaHQ6NzAwO3RleHQtYWxpZ246cmlnaHQ7b3ZlcmZsb3ctd3JhcDphbnl3aGVyZX0K"
    "LmZsYWd7d2lkdGg6NDJweDtoZWlnaHQ6NDJweDtib3JkZXItcmFkaXVzOjE1cHg7ZGlzcGxheTpn"
    "cmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtmb250LXNpemU6MS4zNXJlbTtmbGV4OjAgMCBhdXRvO2Jh"
    "Y2tncm91bmQ6dmFyKC0tY2FyZDIpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSl9Ci5mbGFn"
    "IHN2Z3t3aWR0aDoxOHB4O2hlaWdodDoxOHB4fQoKLmhlcm97cG9zaXRpb246cmVsYXRpdmU7b3Zl"
    "cmZsb3c6aGlkZGVuO2JvcmRlci1yYWRpdXM6MjhweDtwYWRkaW5nOjIycHggMThweDttYXJnaW46"
    "NHB4IDAgMTRweDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6bGluZWFy"
    "LWdyYWRpZW50KDEzNWRlZywjZmY2YTNkLCNmZjJkNzgpO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVu"
    "dDtjb2xvcjojZmZmfQouaGVybyAua2lja3tmb250LXNpemU6LjZyZW07bGV0dGVyLXNwYWNpbmc6"
    "LjJlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7Y29sb3I6dmFyKC0tYWNjKTtmb250LXdlaWdo"
    "dDo4MDB9Ci5oZXJvIGgxe21hcmdpbjo4cHggMCA2cHg7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQp"
    "O2ZvbnQtc2l6ZToxLjc1cmVtO2xpbmUtaGVpZ2h0OjEuMDU7bGV0dGVyLXNwYWNpbmc6LS4wM2Vt"
    "O2ZvbnQtd2VpZ2h0OjgwMH0KLmhlcm8gcHttYXJnaW46MCAwIDE0cHg7Zm9udC1zaXplOi44cmVt"
    "O2NvbG9yOnZhcigtLXR4Mik7bWF4LXdpZHRoOjI4MHB4fQouaGVybyAuYnRucm93e21heC13aWR0"
    "aDozMzBweH0KCi5ncmlke2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyIDFm"
    "cjtnYXA6MTBweH0KLnBjYXJke2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xp"
    "ZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXIpO3BhZGRpbmc6MTRweDtib3gtc2hh"
    "ZG93OnZhcigtLXNoYWRvdyk7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjtnYXA6"
    "OXB4O2N1cnNvcjpwb2ludGVyO30KLnBjYXJkIC50b3B7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1z"
    "OmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjtnYXA6OHB4fQoucGNhcmQgLm5t"
    "e2ZvbnQtd2VpZ2h0OjcwMDtmb250LXNpemU6Ljg1cmVtO3doaXRlLXNwYWNlOm5vd3JhcDtvdmVy"
    "ZmxvdzpoaWRkZW47dGV4dC1vdmVyZmxvdzplbGxpcHNpc30KLnBjYXJkIC5tZXRhe2ZvbnQtc2l6"
    "ZTouNjhyZW07Y29sb3I6dmFyKC0tdHgyKX0KLnBjYXJkIC5wcmljZXtmb250LWZhbWlseTp2YXIo"
    "LS1maGVhZCk7Zm9udC1zaXplOjEuMjVyZW07Zm9udC13ZWlnaHQ6ODAwO2xldHRlci1zcGFjaW5n"
    "Oi0uMDJlbX0KLnBjYXJkIC5wcmljZSBze2ZvbnQtc2l6ZTouNzJyZW07Y29sb3I6dmFyKC0tdHgy"
    "KTtmb250LXdlaWdodDo0MDA7bWFyZ2luLXJpZ2h0OjZweH0KLnJvd3N7ZGlzcGxheTpmbGV4O2Zs"
    "ZXgtZGlyZWN0aW9uOmNvbHVtbjtnYXA6OXB4fQoucm93e2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7Z2FwOjEycHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlk"
    "IHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6dmFyKC0tcik7cGFkZGluZzoxMnB4IDEzcHg7Ym94"
    "LXNoYWRvdzp2YXIoLS1zaGFkb3cpO2N1cnNvcjpwb2ludGVyO3RyYW5zaXRpb246dHJhbnNmb3Jt"
    "IC4xNXM7fQoucm93OmFjdGl2ZXt0cmFuc2Zvcm06c2NhbGUoLjk5KX0KLnJvdyAubWlke2ZsZXg6"
    "MTttaW4td2lkdGg6MH0KLnJvdyAubWlkIGJ7ZGlzcGxheTpibG9jaztmb250LXNpemU6Ljg4cmVt"
    "O3doaXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4dC1vdmVyZmxvdzplbGxpcHNp"
    "c30KLnJvdyAubWlkIHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi43cmVtO2NvbG9yOnZh"
    "cigtLXR4Mik7bWFyZ2luLXRvcDoycHh9Ci5yb3cgLnJ0e3RleHQtYWxpZ246cmlnaHQ7ZmxleDow"
    "IDAgYXV0b30KLnJvdyAucnQgLnB7Zm9udC13ZWlnaHQ6ODAwO2ZvbnQtc2l6ZTouOTVyZW07d2hp"
    "dGUtc3BhY2U6bm93cmFwfQoucm93IC5ydCAucCBze2ZvbnQtd2VpZ2h0OjQwMDtmb250LXNpemU6"
    "LjcycmVtO2NvbG9yOnZhcigtLXR4Mik7bWFyZ2luLXJpZ2h0OjVweH0KLmNhcm91e2Rpc3BsYXk6"
    "ZmxleDtnYXA6MTBweDtvdmVyZmxvdy14OmF1dG87cGFkZGluZzoycHggMCA2cHg7bWFyZ2luOjAg"
    "LTE2cHggNnB4O3BhZGRpbmctbGVmdDoxNnB4O3BhZGRpbmctcmlnaHQ6MTZweDtzY3JvbGwtc25h"
    "cC10eXBlOnggbWFuZGF0b3J5fQouY2Fyb3U+KntmbGV4OjAgMCAxNzJweDtzY3JvbGwtc25hcC1h"
    "bGlnbjpzdGFydH0KCm5hdiN0YWJze3Bvc2l0aW9uOmZpeGVkO3otaW5kZXg6NjA7bGVmdDo1MCU7"
    "Ym90dG9tOmNhbGMoMTJweCArIGVudihzYWZlLWFyZWEtaW5zZXQtYm90dG9tKSk7dHJhbnNmb3Jt"
    "OnRyYW5zbGF0ZVgoLTUwJSk7bWF4LXdpZHRoOmNhbGMoMTAwdncgLSAyMHB4KX0KbmF2I3RhYnMg"
    "LmRvY2t7ZGlzcGxheTpmbGV4O2dhcDoycHg7cGFkZGluZzo3cHggOHB4O2JvcmRlci1yYWRpdXM6"
    "MjZweDtiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsLjk0KTtib3JkZXI6MXB4IHNvbGlkIHZh"
    "cigtLWxpbmUpO2JhY2tkcm9wLWZpbHRlcjpibHVyKDE0cHgpO2JveC1zaGFkb3c6MCAxMHB4IDMw"
    "cHggcmdiYSgyNTUsMTA2LDYxLC4yKX0KbmF2I3RhYnMgYnV0dG9ue3Bvc2l0aW9uOnJlbGF0aXZl"
    "O2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47YWxpZ24taXRlbXM6Y2VudGVyO2p1"
    "c3RpZnktY29udGVudDpjZW50ZXI7Z2FwOjRweDttaW4td2lkdGg6NThweDtoZWlnaHQ6NDhweDtw"
    "YWRkaW5nOjAgNnB4O2JvcmRlcjowO2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7Y3Vyc29yOnBvaW50"
    "ZXI7Y29sb3I6dmFyKC0tdHgyKTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXNpemU6LjZy"
    "ZW07Zm9udC13ZWlnaHQ6NzAwO2xldHRlci1zcGFjaW5nOi4wM2VtO2JvcmRlci1yYWRpdXM6MThw"
    "eDt0cmFuc2l0aW9uOmNvbG9yIC4ycyxiYWNrZ3JvdW5kIC4yczt3aGl0ZS1zcGFjZTpub3dyYXB9"
    "Cm5hdiN0YWJzIGJ1dHRvbiBzdmd7d2lkdGg6MTlweDtoZWlnaHQ6MTlweH0KbmF2I3RhYnMgYnV0"
    "dG9uLm9ue2NvbG9yOiNmZmZmZmY7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCl9Cgouc3VibmF2"
    "e2Rpc3BsYXk6ZmxleDtnYXA6N3B4O292ZXJmbG93LXg6YXV0bztwYWRkaW5nOjJweCAwIDEycHg7"
    "bWFyZ2luOjAgLTJweH0KLnN1Ym5hdiBidXR0b257ZmxleDowIDAgYXV0bztoZWlnaHQ6MzRweDtw"
    "YWRkaW5nOjAgMTNweDtib3JkZXItcmFkaXVzOjEzcHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1s"
    "aW5lKTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtZmFtaWx5"
    "OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouNzRyZW07Zm9udC13ZWlnaHQ6NzAwO2N1cnNvcjpwb2lu"
    "dGVyO3doaXRlLXNwYWNlOm5vd3JhcH0KLnN1Ym5hdiBidXR0b24ub257YmFja2dyb3VuZDp2YXIo"
    "LS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmZmZjtib3JkZXItY29sb3I6dHJhbnNwYXJlbnR9Ci5rcGlz"
    "e2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyIDFmcjtnYXA6MTBweDttYXJn"
    "aW4tYm90dG9tOjEycHh9Ci5rcGl7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNv"
    "bGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO3BhZGRpbmc6MTNweDtib3gt"
    "c2hhZG93OnZhcigtLXNoYWRvdyl9Ci5rcGkgc21hbGx7ZGlzcGxheTpibG9jaztmb250LXNpemU6"
    "LjY0cmVtO2NvbG9yOnZhcigtLXR4Mik7bGV0dGVyLXNwYWNpbmc6LjFlbTt0ZXh0LXRyYW5zZm9y"
    "bTp1cHBlcmNhc2V9Ci5rcGkgYntkaXNwbGF5OmJsb2NrO21hcmdpbi10b3A6NXB4O2ZvbnQtZmFt"
    "aWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6MS4zNXJlbTtmb250LXdlaWdodDo4MDA7bGV0dGVy"
    "LXNwYWNpbmc6LS4wMmVtfQouaW5wLHNlbGVjdC5pbnB7d2lkdGg6MTAwJTtoZWlnaHQ6dmFyKC0t"
    "YnRuaCk7cGFkZGluZzowIDEzcHg7Ym9yZGVyLXJhZGl1czoxOHB4O2JvcmRlcjoxcHggc29saWQg"
    "dmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgpO2ZvbnQt"
    "ZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouODNyZW07b3V0bGluZTpub25lOy13ZWJraXQt"
    "YXBwZWFyYW5jZTpub25lO2FwcGVhcmFuY2U6bm9uZX0Kc2VsZWN0LmlucHtiYWNrZ3JvdW5kLWlt"
    "YWdlOm5vbmV9Ci5pbnA6OnBsYWNlaG9sZGVye2NvbG9yOnZhcigtLXR4Mik7b3BhY2l0eTouNzV9"
    "Ci5pbnA6Zm9jdXN7Ym9yZGVyLWNvbG9yOnZhcigtLWFjYyl9CnRleHRhcmVhLmlucHtoZWlnaHQ6"
    "YXV0bzttaW4taGVpZ2h0OjQwcHg7cGFkZGluZzoxMHB4IDEzcHg7bGluZS1oZWlnaHQ6MS41O3Jl"
    "c2l6ZTp2ZXJ0aWNhbH0KLmNtc3Jvdy5oaWRle2Rpc3BsYXk6bm9uZX0KLmljb25jaGlwe3Bvc2l0"
    "aW9uOnJlbGF0aXZlO2hlaWdodDozNHB4O21pbi13aWR0aDozNHB4O3BhZGRpbmc6MCA5cHg7Ym9y"
    "ZGVyLXJhZGl1czo5OXB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2"
    "YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgpO2Rpc3BsYXk6aW5saW5lLWdyaWQ7cGxhY2UtaXRl"
    "bXM6Y2VudGVyO2N1cnNvcjpwb2ludGVyfQouaWNvbmNoaXAgc3Zne3dpZHRoOjE3cHg7aGVpZ2h0"
    "OjE3cHh9Ci5pY29uY2hpcCBpe3Bvc2l0aW9uOmFic29sdXRlO3RvcDotNXB4O3JpZ2h0Oi01cHg7"
    "bWluLXdpZHRoOjE3cHg7aGVpZ2h0OjE3cHg7cGFkZGluZzowIDRweDtib3JkZXItcmFkaXVzOjk5"
    "cHg7YmFja2dyb3VuZDp2YXIoLS1hY2MpO2NvbG9yOiNmZmZmZmY7Zm9udC1zaXplOi42cmVtO2Zv"
    "bnQtc3R5bGU6bm9ybWFsO2ZvbnQtd2VpZ2h0OjgwMDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6"
    "Y2VudGVyfQouc3RhcnN7ZGlzcGxheTppbmxpbmUtZmxleDtnYXA6MXB4O3ZlcnRpY2FsLWFsaWdu"
    "Om1pZGRsZX0KLnN0YXJzIHN2Z3t3aWR0aDoxMXB4O2hlaWdodDoxMXB4O2NvbG9yOiNmNWE2MjN9"
    "Ci5zdGFycyBie2ZvbnQtc2l6ZTouNjZyZW07Y29sb3I6dmFyKC0tdHgyKTtmb250LXdlaWdodDo3"
    "MDA7bWFyZ2luLWxlZnQ6M3B4fQouaGVhcnR7cG9zaXRpb246YWJzb2x1dGU7dG9wOjhweDtyaWdo"
    "dDo4cHg7d2lkdGg6MjZweDtoZWlnaHQ6MjZweDtib3JkZXItcmFkaXVzOjk5cHg7ZGlzcGxheTpn"
    "cmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tn"
    "cm91bmQ6dmFyKC0tY2FyZDIpO2N1cnNvcjpwb2ludGVyO3otaW5kZXg6Mn0KLmhlYXJ0IHN2Z3t3"
    "aWR0aDoxM3B4O2hlaWdodDoxM3B4O2NvbG9yOnZhcigtLXR4Mil9Ci5oZWFydC5vbntiYWNrZ3Jv"
    "dW5kOnZhcigtLWFjYyk7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50fQouaGVhcnQub24gc3Zne2Nv"
    "bG9yOiNmZmZmZmZ9Ci5xdHl7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MDti"
    "b3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6MThweDtvdmVyZmxvdzpo"
    "aWRkZW59Ci5xdHkgYnV0dG9ue3dpZHRoOjM4cHg7aGVpZ2h0OjM4cHg7Ym9yZGVyOjA7YmFja2dy"
    "b3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgpO2ZvbnQtc2l6ZToxcmVtO2ZvbnQtd2Vp"
    "Z2h0OjgwMDtjdXJzb3I6cG9pbnRlcn0KLnF0eSBie21pbi13aWR0aDozOHB4O3RleHQtYWxpZ246"
    "Y2VudGVyO2ZvbnQtc2l6ZTouODVyZW19Ci50bHttYXJnaW46MTBweCAwIDA7cGFkZGluZy1sZWZ0"
    "OjE2cHg7Ym9yZGVyLWxlZnQ6MnB4IHNvbGlkIHZhcigtLWxpbmUpfQoudGwgZGl2e3Bvc2l0aW9u"
    "OnJlbGF0aXZlO3BhZGRpbmc6MCAwIDExcHggMTBweDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZh"
    "cigtLXR4Mil9Ci50bCBkaXY6bGFzdC1jaGlsZHtwYWRkaW5nLWJvdHRvbTowfQoudGwgZGl2Ojpi"
    "ZWZvcmV7Y29udGVudDoiIjtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0Oi0yMXB4O3RvcDo0cHg7d2lk"
    "dGg6OHB4O2hlaWdodDo4cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2JhY2tncm91bmQ6dmFyKC0tbGlu"
    "ZSl9Ci50bCBkaXYub2s6OmJlZm9yZXtiYWNrZ3JvdW5kOnZhcigtLW9rKX0KLnRsIGRpdi5vayBi"
    "e2NvbG9yOnZhcigtLXR4KX0KLnRsIHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42NnJl"
    "bTtvcGFjaXR5Oi44fQouZmNhcmR7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmZsZXgtc3RhcnQ7"
    "Z2FwOjExcHg7cGFkZGluZzoxMXB4IDA7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0tbGlu"
    "ZSl9Ci5mY2FyZDpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206MH0KLmZjYXJkIC50e2ZsZXg6MTtt"
    "aW4td2lkdGg6MH0KLmZjYXJkIC50IGJ7Zm9udC1zaXplOi44MXJlbTtkaXNwbGF5OmJsb2NrfQou"
    "ZmNhcmQgLnQgc21hbGx7ZGlzcGxheTpibG9jaztmb250LXNpemU6LjY5cmVtO2NvbG9yOnZhcigt"
    "LXR4Mik7bGluZS1oZWlnaHQ6MS40NTttYXJnaW4tdG9wOjJweH0KLmZyb3cuaGlkZSwuZmdycC5o"
    "aWRle2Rpc3BsYXk6bm9uZX0KLmJhcnN7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmZsZXgtZW5k"
    "O2dhcDo1cHg7aGVpZ2h0Ojg2cHg7bWFyZ2luLXRvcDoxMnB4fQouYmFycyBpe2ZsZXg6MTtib3Jk"
    "ZXItcmFkaXVzOjZweCA2cHggM3B4IDNweDtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtkaXNw"
    "bGF5OmJsb2NrfQouZnVubmVsIGRpdntkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dh"
    "cDo5cHg7bWFyZ2luLXRvcDo5cHg7Zm9udC1zaXplOi43NHJlbX0KLmZ1bm5lbCBpe2hlaWdodDoy"
    "NnB4O2JvcmRlci1yYWRpdXM6N3B4O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2Rpc3BsYXk6"
    "YmxvY2t9Ci5mZWVkbGluZXtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo5cHg7"
    "Zm9udC1zaXplOi43M3JlbTtjb2xvcjp2YXIoLS10eDIpO3BhZGRpbmc6N3B4IDA7Ym9yZGVyLWJv"
    "dHRvbToxcHggc29saWQgdmFyKC0tbGluZSl9Ci5mZWVkbGluZTpsYXN0LWNoaWxke2JvcmRlci1i"
    "b3R0b206MH0KLmZlZWRsaW5lIGJ7Y29sb3I6dmFyKC0tdHgpfQoubGJ7ZGlzcGxheTpmbGV4O2Fs"
    "aWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjlweCAwO2JvcmRlci1ib3R0b206MXB4"
    "IHNvbGlkIHZhcigtLWxpbmUpfQoubGI6bGFzdC1jaGlsZHtib3JkZXItYm90dG9tOjB9Ci5sYiAu"
    "cmt7d2lkdGg6MjRweDtoZWlnaHQ6MjRweDtib3JkZXItcmFkaXVzOjk5cHg7ZGlzcGxheTpncmlk"
    "O3BsYWNlLWl0ZW1zOmNlbnRlcjtmb250LXNpemU6LjdyZW07Zm9udC13ZWlnaHQ6ODAwO2JhY2tn"
    "cm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4Mil9Ci5sYiAucmsuZ3tiYWNrZ3JvdW5k"
    "OnZhcigtLXBpbGxncmFkKTtjb2xvcjojZmZmZmZmfQouY21zZ3JwLmhpZGV7ZGlzcGxheTpub25l"
    "fQouY21zcm93IGxhYmVsIGJ7Y29sb3I6dmFyKC0tYWNjKTtmb250LXdlaWdodDo3MDB9Ci50aW1l"
    "cmJhcntoZWlnaHQ6NXB4O2JvcmRlci1yYWRpdXM6OTlweDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQy"
    "KTtvdmVyZmxvdzpoaWRkZW47bWFyZ2luOjEwcHggMCAycHh9Ci50aW1lcmJhciBpe2Rpc3BsYXk6"
    "YmxvY2s7aGVpZ2h0OjEwMCU7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7dHJhbnNpdGlvbjp3"
    "aWR0aCAxcyBsaW5lYXJ9Ci50aW1lcmJhci5sb3cgaXtiYWNrZ3JvdW5kOnZhcigtLWJhZCwjZTU0"
    "ODRkKX0KLmZpZWxke21hcmdpbi1ib3R0b206OXB4fQouZmllbGQgbGFiZWx7ZGlzcGxheTpibG9j"
    "aztmb250LXNpemU6LjY4cmVtO2NvbG9yOnZhcigtLXR4Mik7bGV0dGVyLXNwYWNpbmc6LjA5ZW07"
    "dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO21hcmdpbjowIDAgNXB4IDJweDtmb250LXdlaWdodDo3"
    "MDB9Ci5zd2Fwe2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50"
    "OnNwYWNlLWJldHdlZW47Z2FwOjEycHg7cGFkZGluZzoxMXB4IDA7Ym9yZGVyLWJvdHRvbToxcHgg"
    "c29saWQgdmFyKC0tbGluZSl9Ci5zd2FwOmxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRvbTowfQouc3dh"
    "cCAudHtmb250LXNpemU6LjgycmVtO2ZvbnQtd2VpZ2h0OjYwMDttaW4td2lkdGg6MH0KLnN3YXAg"
    "LnQgc21hbGx7ZGlzcGxheTpibG9jaztmb250LXdlaWdodDo0MDA7Zm9udC1zaXplOi43cmVtO2Nv"
    "bG9yOnZhcigtLXR4Mil9Ci50Z3t3aWR0aDo0NHB4O2hlaWdodDoyNnB4O2JvcmRlci1yYWRpdXM6"
    "OTlweDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUp"
    "O3Bvc2l0aW9uOnJlbGF0aXZlO2N1cnNvcjpwb2ludGVyO2ZsZXg6MCAwIGF1dG99Ci50ZzphZnRl"
    "cntjb250ZW50OiIiO3Bvc2l0aW9uOmFic29sdXRlO3RvcDozcHg7bGVmdDozcHg7d2lkdGg6MThw"
    "eDtoZWlnaHQ6MThweDtib3JkZXItcmFkaXVzOjUwJTtiYWNrZ3JvdW5kOnZhcigtLXR4Mik7dHJh"
    "bnNpdGlvbjouMjJzfQoudGcub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Ym9yZGVyLWNv"
    "bG9yOnRyYW5zcGFyZW50fQoudGcub246YWZ0ZXJ7bGVmdDoyMXB4O2JhY2tncm91bmQ6I2ZmZmZm"
    "Zn0KLnRnLnNte3dpZHRoOjM2cHg7aGVpZ2h0OjIxcHh9Ci50Zy5zbTphZnRlcnt3aWR0aDoxNHB4"
    "O2hlaWdodDoxNHB4O3RvcDoyLjVweH0KLnRnLnNtLm9uOmFmdGVye2xlZnQ6MThweH0KLnN0YXJ7"
    "d2lkdGg6MzBweDtoZWlnaHQ6MzBweDtib3JkZXItcmFkaXVzOjlweDtkaXNwbGF5OmdyaWQ7cGxh"
    "Y2UtaXRlbXM6Y2VudGVyO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2"
    "YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgyKTtjdXJzb3I6cG9pbnRlcjtmbGV4OjAgMCBhdXRv"
    "fQouc3Rhci5vbntiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojZmZmZmZmO2JvcmRl"
    "ci1jb2xvcjp0cmFuc3BhcmVudH0KLnN0YXIgc3Zne3dpZHRoOjE0cHg7aGVpZ2h0OjE0cHh9Cgoj"
    "c2hlZXR7cG9zaXRpb246Zml4ZWQ7aW5zZXQ6MDt6LWluZGV4OjkwO2Rpc3BsYXk6bm9uZX0KI3No"
    "ZWV0Lm9wZW57ZGlzcGxheTpibG9ja30KI3NoZWV0IC5iZ3twb3NpdGlvbjphYnNvbHV0ZTtpbnNl"
    "dDowO2JhY2tncm91bmQ6cmdiYSg0OSwzMiwyNiwuNCk7YmFja2Ryb3AtZmlsdGVyOmJsdXIoM3B4"
    "KX0KI3NoZWV0IC5wYW5lbHtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjUwJTtib3R0b206MDt0cmFu"
    "c2Zvcm06dHJhbnNsYXRlWCgtNTAlKTt3aWR0aDoxMDAlO21heC13aWR0aDo1MjBweDtiYWNrZ3Jv"
    "dW5kOnZhcigtLWJnMik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItYm90dG9t"
    "OjA7Ym9yZGVyLXJhZGl1czoyOHB4IDI4cHggMCAwO3BhZGRpbmc6MThweCAxNnB4IGNhbGMoMjBw"
    "eCArIGVudihzYWZlLWFyZWEtaW5zZXQtYm90dG9tKSk7YW5pbWF0aW9uOnVwIC4yOHMgY3ViaWMt"
    "YmV6aWVyKC4yLC45LC4zLDEpO21heC1oZWlnaHQ6OTJ2aDtvdmVyZmxvdzphdXRvfQpAa2V5ZnJh"
    "bWVzIHVwe2Zyb217dHJhbnNmb3JtOnRyYW5zbGF0ZSgtNTAlLDEwMCUpfXRve3RyYW5zZm9ybTp0"
    "cmFuc2xhdGUoLTUwJSwwKX19CiNzaGVldCAuZ3JhYnt3aWR0aDozOHB4O2hlaWdodDo0cHg7Ym9y"
    "ZGVyLXJhZGl1czo5cHg7YmFja2dyb3VuZDp2YXIoLS1saW5lKTttYXJnaW46MCBhdXRvIDE0cHh9"
    "Ci5vdHBib3h7Zm9udC1mYW1pbHk6dWktbW9ub3NwYWNlLE1lbmxvLENvbnNvbGFzLG1vbm9zcGFj"
    "ZTtmb250LXNpemU6MS42cmVtO2xldHRlci1zcGFjaW5nOi4zNWVtO3RleHQtYWxpZ246Y2VudGVy"
    "O3BhZGRpbmc6MTZweDtib3JkZXItcmFkaXVzOnZhcigtLXJzKTtiYWNrZ3JvdW5kOnZhcigtLWNh"
    "cmQyKTtib3JkZXI6MXB4IGRhc2hlZCB2YXIoLS1saW5lKTttYXJnaW46MTBweCAwfQoucXJ7d2lk"
    "dGg6MTUwcHg7aGVpZ2h0OjE1MHB4O21hcmdpbjo2cHggYXV0byAxMHB4O2JvcmRlci1yYWRpdXM6"
    "dmFyKC0tcnMpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2JvcmRlcjoxcHggc29saWQgdmFyKC0t"
    "bGluZSk7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtjb2xvcjp2YXIoLS10eDIpO2Zv"
    "bnQtc2l6ZTouNjVyZW07bGV0dGVyLXNwYWNpbmc6LjFlbTt0ZXh0LWFsaWduOmNlbnRlcn0KLnN0"
    "ZXBze21hcmdpbjoxMnB4IDAgNHB4fQouc3RlcHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2Vu"
    "dGVyO2dhcDoxMHB4O3BhZGRpbmc6OHB4IDA7Zm9udC1zaXplOi43OXJlbTtjb2xvcjp2YXIoLS10"
    "eDIpfQouc3RlcCBpe3dpZHRoOjIycHg7aGVpZ2h0OjIycHg7Ym9yZGVyLXJhZGl1czo1MCU7Ym9y"
    "ZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVy"
    "O2ZvbnQtc3R5bGU6bm9ybWFsO2ZvbnQtc2l6ZTouNjVyZW07Zm9udC13ZWlnaHQ6ODAwO2ZsZXg6"
    "MCAwIGF1dG99Ci5zdGVwLm9ue2NvbG9yOnZhcigtLXR4KX0KLnN0ZXAub24gaXtiYWNrZ3JvdW5k"
    "OnZhcigtLXBpbGxncmFkKTtjb2xvcjojZmZmZmZmO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVudH0K"
    "LnN0ZXAucnVuIGl7Ym9yZGVyLWNvbG9yOnZhcigtLWFjYyk7Y29sb3I6dmFyKC0tYWNjKTthbmlt"
    "YXRpb246cHVsc2UgMXMgaW5maW5pdGV9CkBrZXlmcmFtZXMgcHVsc2V7NTAle29wYWNpdHk6LjM1"
    "fX0KLnRvYXN0e3Bvc2l0aW9uOmZpeGVkO2xlZnQ6NTAlO2JvdHRvbTpjYWxjKDk2cHggKyBlbnYo"
    "c2FmZS1hcmVhLWluc2V0LWJvdHRvbSkpO3RyYW5zZm9ybTp0cmFuc2xhdGVYKC01MCUpO3otaW5k"
    "ZXg6MTIwO2JhY2tncm91bmQ6dmFyKC0tYmcyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUp"
    "O2NvbG9yOnZhcigtLXR4KTtwYWRkaW5nOjExcHggMTZweDtib3JkZXItcmFkaXVzOjk5cHg7Zm9u"
    "dC1zaXplOi43OHJlbTtmb250LXdlaWdodDo2MDA7Ym94LXNoYWRvdzp2YXIoLS1zaGFkb3cpO29w"
    "YWNpdHk6MDtwb2ludGVyLWV2ZW50czpub25lO3RyYW5zaXRpb246b3BhY2l0eSAuMnMsdHJhbnNm"
    "b3JtIC4yczt3aGl0ZS1zcGFjZTpub3dyYXA7bWF4LXdpZHRoOjkwdnc7b3ZlcmZsb3c6aGlkZGVu"
    "O3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXN9Ci50b2FzdC5zaG93e29wYWNpdHk6MTt0cmFuc2Zvcm06"
    "dHJhbnNsYXRlWCgtNTAlKSB0cmFuc2xhdGVZKC02cHgpfQoKLyogcHJldmlldyBjaHJvbWUg4oCU"
    "IG1vY2t1cCBvbmx5LCBvd24gc3RyaXAgc28gaXQgbmV2ZXIgb3ZlcmxhcHMgdGhlIFVJICovCiNz"
    "d3twb3NpdGlvbjpmaXhlZDt0b3A6MDtsZWZ0OjA7cmlnaHQ6MDtoZWlnaHQ6MzJweDt6LWluZGV4"
    "OjIwMDtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50"
    "ZXI7Z2FwOjVweDsKICBiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsLjkpO2JvcmRlci1ib3R0"
    "b206MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tkcm9wLWZpbHRlcjpibHVyKDEwcHgpfQojc3cg"
    "ZW17Zm9udC1zdHlsZTpub3JtYWw7Zm9udC1zaXplOi41NnJlbTtsZXR0ZXItc3BhY2luZzouMThl"
    "bTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7Y29sb3I6dmFyKC0tdHgyKTttYXJnaW4tcmlnaHQ6"
    "NHB4fQojc3cgYnV0dG9ue2hlaWdodDoyMnB4O3BhZGRpbmc6MCAxMnB4O2JvcmRlci1yYWRpdXM6"
    "OTlweDtib3JkZXI6MDtjdXJzb3I6cG9pbnRlcjtiYWNrZ3JvdW5kOnRyYW5zcGFyZW50O2NvbG9y"
    "OnZhcigtLXR4Mik7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi42cmVtO2ZvbnQt"
    "d2VpZ2h0OjcwMDtsZXR0ZXItc3BhY2luZzouMDdlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2V9"
    "CiNzdyBidXR0b24ub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmZmZn0K"
    "Ci8qIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCBBVVJPUkEg"
    "VUkgS0lUIOKAlCBwb3J0ZWQgY29tcG9uZW50cyDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZAgKi8KQHByb3BlcnR5IC0tZ2F7c3ludGF4OiI8YW5nbGU+Ijtpbml0"
    "aWFsLXZhbHVlOjBkZWc7aW5oZXJpdHM6ZmFsc2V9CkBwcm9wZXJ0eSAtLWdwe3N5bnRheDoiPHBl"
    "cmNlbnRhZ2U+Ijtpbml0aWFsLXZhbHVlOjUlO2luaGVyaXRzOmZhbHNlfQpAcHJvcGVydHkgLS1n"
    "b3tzeW50YXg6IjxhbmdsZT4iO2luaXRpYWwtdmFsdWU6MGRlZztpbmhlcml0czpmYWxzZX0KCi8q"
    "IDIzIMK3IFNoaW55IENUQSDigJQgY29uaWMgYm9yZGVyICsgZG90IG1hc2sgKyBpbm5lciBzaGlt"
    "bWVyICsgYnJlYXRoZSAqLwouYnRuLmN0YXstLWR1cjozLjJzOy0taGw6I2ZmZDljOTtwb3NpdGlv"
    "bjpyZWxhdGl2ZTtpc29sYXRpb246aXNvbGF0ZTtib3JkZXI6MXB4IHNvbGlkIHRyYW5zcGFyZW50"
    "OwogIGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KHZhcigtLWJ0bmJnKSx2YXIoLS1idG5iZykp"
    "IHBhZGRpbmctYm94LAogICAgY29uaWMtZ3JhZGllbnQoZnJvbSBjYWxjKHZhcigtLWdhKSAtIHZh"
    "cigtLWdvKSksdHJhbnNwYXJlbnQsdmFyKC0taGwpIHZhcigtLWdwKSwKICAgICAgI2ZmZiBjYWxj"
    "KHZhcigtLWdwKSoyKSx2YXIoLS1obCkgY2FsYyh2YXIoLS1ncCkqMyksdHJhbnNwYXJlbnQgY2Fs"
    "Yyh2YXIoLS1ncCkqNCkpIGJvcmRlci1ib3g7CiAgYm94LXNoYWRvdzppbnNldCAwIDAgMCAxcHgg"
    "cmdiYSgyNTUsMjU1LDI1NSwuMDgpOwogIHRyYW5zaXRpb246LS1nbyAuOHMgY3ViaWMtYmV6aWVy"
    "KC4yNSwxLC41LDEpLC0tZ3AgLjhzIGN1YmljLWJlemllciguMjUsMSwuNSwxKSx0cmFuc2Zvcm0g"
    "LjE1czsKICBhbmltYXRpb246Z2EgdmFyKC0tZHVyKSBsaW5lYXIgaW5maW5pdGV9Ci5idG4uY3Rh"
    "OmJlZm9yZSwuYnRuLmN0YTphZnRlcntjb250ZW50OiIiO3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6"
    "NTAlO3RvcDo1MCU7dHJhbnNsYXRlOi01MCUgLTUwJTt6LWluZGV4Oi0xO3BvaW50ZXItZXZlbnRz"
    "Om5vbmV9Ci5idG4uY3RhOmJlZm9yZXt3aWR0aDpjYWxjKDEwMCUgLSA2cHgpO2hlaWdodDpjYWxj"
    "KDEwMCUgLSA2cHgpO2JvcmRlci1yYWRpdXM6aW5oZXJpdDtvcGFjaXR5Oi4zNTsKICBiYWNrZ3Jv"
    "dW5kOnJhZGlhbC1ncmFkaWVudChjaXJjbGUgYXQgMnB4IDJweCwjZmZmIC41cHgsdHJhbnNwYXJl"
    "bnQgMCkgcGFkZGluZy1ib3g7YmFja2dyb3VuZC1zaXplOjRweCA0cHg7CiAgLXdlYmtpdC1tYXNr"
    "LWltYWdlOmNvbmljLWdyYWRpZW50KGZyb20gY2FsYyh2YXIoLS1nYSkgKyA0NWRlZyksIzAwMCx0"
    "cmFuc3BhcmVudCAxMCUgOTAlLCMwMDApOwogIG1hc2staW1hZ2U6Y29uaWMtZ3JhZGllbnQoZnJv"
    "bSBjYWxjKHZhcigtLWdhKSArIDQ1ZGVnKSwjMDAwLHRyYW5zcGFyZW50IDEwJSA5MCUsIzAwMCl9"
    "Ci5idG4uY3RhOmFmdGVye3dpZHRoOjEwMCU7YXNwZWN0LXJhdGlvOjE7b3BhY2l0eTouNTU7CiAg"
    "YmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoLTUwZGVnLHRyYW5zcGFyZW50LHZhcigtLWhsKSx0"
    "cmFuc3BhcmVudCk7CiAgLXdlYmtpdC1tYXNrLWltYWdlOnJhZGlhbC1ncmFkaWVudChjaXJjbGUg"
    "YXQgYm90dG9tLHRyYW5zcGFyZW50IDQwJSwjMDAwKTsKICBtYXNrLWltYWdlOnJhZGlhbC1ncmFk"
    "aWVudChjaXJjbGUgYXQgYm90dG9tLHRyYW5zcGFyZW50IDQwJSwjMDAwKTsKICBhbmltYXRpb246"
    "c2hpbW1lciB2YXIoLS1kdXIpIGxpbmVhciBpbmZpbml0ZX0KLmJ0bi5jdGE6aG92ZXJ7LS1ncDoy"
    "MiU7LS1nbzo5NWRlZ30KLmJ0bi5jdGE6YWN0aXZle3RyYW5zbGF0ZTowIDFweH0KQGtleWZyYW1l"
    "cyBnYXt0b3stLWdhOjM2MGRlZ319CkBrZXlmcmFtZXMgc2hpbW1lcnt0b3tyb3RhdGU6MzYwZGVn"
    "fX0KCi8qIDIyIMK3IExpcXVpZCBnbGFzcyAqLwouYnRuLmdsYXNzLC5nbGFzc2NhcmR7cG9zaXRp"
    "b246cmVsYXRpdmU7YmFja2dyb3VuZDpjb2xvci1taXgoaW4gc3JnYix2YXIoLS1jYXJkMikgNzAl"
    "LHRyYW5zcGFyZW50KTsKICBib3JkZXI6MXB4IHNvbGlkIGNvbG9yLW1peChpbiBzcmdiLCNmZmYg"
    "MTYlLHZhcigtLWxpbmUpKTtjb2xvcjp2YXIoLS10eCk7CiAgYmFja2Ryb3AtZmlsdGVyOmJsdXIo"
    "MTRweCkgc2F0dXJhdGUoMS40KTstd2Via2l0LWJhY2tkcm9wLWZpbHRlcjpibHVyKDE0cHgpIHNh"
    "dHVyYXRlKDEuNCk7CiAgYm94LXNoYWRvdzppbnNldCAxcHggMXB4IDFweCAtLjVweCByZ2JhKDI1"
    "NSwyNTUsMjU1LC41NSksaW5zZXQgLTFweCAtMXB4IDFweCAtLjVweCByZ2JhKDI1NSwyNTUsMjU1"
    "LC4zKSwKICAgIGluc2V0IDAgMCA4cHggNnB4IHJnYmEoMjU1LDI1NSwyNTUsLjA3KSwwIDJweCAx"
    "MHB4IHJnYmEoMCwwLDAsLjEyKX0KLmJ0bi5nbGFzczphZnRlcntjb250ZW50OiIiO3Bvc2l0aW9u"
    "OmFic29sdXRlO2luc2V0OjFweCAxcHggYXV0bztoZWlnaHQ6NDIlO2JvcmRlci1yYWRpdXM6aW5o"
    "ZXJpdDsKICBiYWNrZ3JvdW5kOmxpbmVhci1ncmFkaWVudCgxODBkZWcscmdiYSgyNTUsMjU1LDI1"
    "NSwuMzQpLHRyYW5zcGFyZW50KTtwb2ludGVyLWV2ZW50czpub25lfQouYnRuLmdsYXNzOmhvdmVy"
    "e3RyYW5zZm9ybTpzY2FsZSgxLjAzKX0KCi8qIHByZXNzIHJpcHBsZSAoa2l0IGRvY2svYnV0dG9u"
    "IGZlZWwpICovCi5yaXB7cG9zaXRpb246YWJzb2x1dGU7Ym9yZGVyLXJhZGl1czo1MCU7dHJhbnNm"
    "b3JtOnRyYW5zbGF0ZSgtNTAlLC01MCUpIHNjYWxlKDApO3BvaW50ZXItZXZlbnRzOm5vbmU7CiAg"
    "YmFja2dyb3VuZDpyYWRpYWwtZ3JhZGllbnQoY2lyY2xlLHJnYmEoMjU1LDI1NSwyNTUsLjU1KSx0"
    "cmFuc3BhcmVudCA3MCUpO2FuaW1hdGlvbjpyaXAgLjU1cyBlYXNlLW91dCBmb3J3YXJkc30KQGtl"
    "eWZyYW1lcyByaXB7dG97dHJhbnNmb3JtOnRyYW5zbGF0ZSgtNTAlLC01MCUpIHNjYWxlKDEpO29w"
    "YWNpdHk6MH19CgovKiAyNCDCtyBBbmltYXRlZCBiYW5uZXIgKyAxNyDCtyBsb2dvIGNsb3VkIG1h"
    "cnF1ZWUgKi8KLmJhbm5lcntwb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRkZW47Ym9yZGVy"
    "LXJhZGl1czoxOHB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7CiAgYmFja2dyb3VuZDp2"
    "YXIoLS1jYXJkKTttYXJnaW46MnB4IDAgMTJweDtoZWlnaHQ6MzhweDtkaXNwbGF5OmZsZXg7YWxp"
    "Z24taXRlbXM6Y2VudGVyOwogIC13ZWJraXQtbWFzay1pbWFnZTpsaW5lYXItZ3JhZGllbnQoOTBk"
    "ZWcsdHJhbnNwYXJlbnQsIzAwMCA4JSwjMDAwIDkyJSx0cmFuc3BhcmVudCk7CiAgbWFzay1pbWFn"
    "ZTpsaW5lYXItZ3JhZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCA4JSwjMDAwIDkyJSx0cmFu"
    "c3BhcmVudCl9Ci5iYW5uZXIgLnRyYWNre2Rpc3BsYXk6ZmxleDtnYXA6MzBweDt3aGl0ZS1zcGFj"
    "ZTpub3dyYXA7YW5pbWF0aW9uOm1hcnEgMjZzIGxpbmVhciBpbmZpbml0ZTt3aWxsLWNoYW5nZTp0"
    "cmFuc2Zvcm19Ci5iYW5uZXI6aG92ZXIgLnRyYWNre2FuaW1hdGlvbi1wbGF5LXN0YXRlOnBhdXNl"
    "ZH0KLmJhbm5lciBzcGFue2ZvbnQtc2l6ZTouN3JlbTtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtd2Vp"
    "Z2h0OjYwMDtsZXR0ZXItc3BhY2luZzouMDNlbTtkaXNwbGF5OmlubGluZS1mbGV4O2FsaWduLWl0"
    "ZW1zOmNlbnRlcjtnYXA6NnB4fQouYmFubmVyIHNwYW4gYntjb2xvcjp2YXIoLS1hY2MpfQouYmFu"
    "bmVyIHNwYW4gc3Zne3dpZHRoOjEzcHg7aGVpZ2h0OjEzcHg7Y29sb3I6dmFyKC0tYWNjKX0KQGtl"
    "eWZyYW1lcyBtYXJxe3Rve3RyYW5zZm9ybTp0cmFuc2xhdGVYKC01MCUpfX0KLmNsb3Vke2Rpc3Bs"
    "YXk6ZmxleDtnYXA6MjZweDtvdmVyZmxvdzpoaWRkZW47cGFkZGluZzo0cHggMCAycHg7CiAgLXdl"
    "YmtpdC1tYXNrLWltYWdlOmxpbmVhci1ncmFkaWVudCg5MGRlZyx0cmFuc3BhcmVudCwjMDAwIDEy"
    "JSwjMDAwIDg4JSx0cmFuc3BhcmVudCk7CiAgbWFzay1pbWFnZTpsaW5lYXItZ3JhZGllbnQoOTBk"
    "ZWcsdHJhbnNwYXJlbnQsIzAwMCAxMiUsIzAwMCA4OCUsdHJhbnNwYXJlbnQpfQouY2xvdWQgLnR7"
    "ZGlzcGxheTpmbGV4O2dhcDoyNnB4O2FuaW1hdGlvbjptYXJxIDMwcyBsaW5lYXIgaW5maW5pdGV9"
    "Ci5jbG91ZCBpe2ZvbnQtc3R5bGU6bm9ybWFsO2ZvbnQtc2l6ZTouNjhyZW07Zm9udC13ZWlnaHQ6"
    "ODAwO2xldHRlci1zcGFjaW5nOi4xNGVtO2NvbG9yOnZhcigtLXR4Mik7b3BhY2l0eTouNTU7d2hp"
    "dGUtc3BhY2U6bm93cmFwfQoKLyogMDcgwrcgR3JhZGllbnQgdHJhY2luZyAqLwoudHJhY2V7ZGlz"
    "cGxheTpibG9jazt3aWR0aDoxMDAlO2hlaWdodDoxMHB4O21hcmdpbjotMnB4IDAgOHB4O292ZXJm"
    "bG93OnZpc2libGV9Ci50cmFjZSAuYmFzZXtzdHJva2U6dmFyKC0tbGluZSk7c3Ryb2tlLXdpZHRo"
    "OjEuNH0KLnRyYWNlIC5sZWFke3N0cm9rZS13aWR0aDoxLjg7c3Ryb2tlLWxpbmVjYXA6cm91bmR9"
    "Ci5zZWMgaDJ7cG9zaXRpb246cmVsYXRpdmV9CgovKiAwMSArIDEzIMK3IERvdCBsb2FkZXIgKi8K"
    "LmRvdGdyaWR7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoNywxZnIp"
    "O2dhcDozcHg7d2lkdGg6NzBweH0KLmRvdGdyaWQgaXt3aWR0aDoxMDAlO2FzcGVjdC1yYXRpbzox"
    "O2JvcmRlci1yYWRpdXM6MnB4O2JhY2tncm91bmQ6Y29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tdHgp"
    "IDE0JSx0cmFuc3BhcmVudCk7dHJhbnNpdGlvbjpiYWNrZ3JvdW5kIC4xMnMsdHJhbnNmb3JtIC4x"
    "MnN9Ci5kb3RncmlkIGkuYWN0e2JhY2tncm91bmQ6dmFyKC0tYWNjKTt0cmFuc2Zvcm06c2NhbGUo"
    "MS4xMil9Ci5kb3Rze2Rpc3BsYXk6aW5saW5lLWZsZXg7Z2FwOjRweDthbGlnbi1pdGVtczpjZW50"
    "ZXJ9Ci5kb3RzIGl7d2lkdGg6NXB4O2hlaWdodDo1cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dy"
    "b3VuZDp2YXIoLS1hY2MpO2FuaW1hdGlvbjpib3VuY2UgMXMgaW5maW5pdGUgZWFzZS1pbi1vdXR9"
    "Ci5kb3RzIGk6bnRoLWNoaWxkKDIpe2FuaW1hdGlvbi1kZWxheTouMTVzfS5kb3RzIGk6bnRoLWNo"
    "aWxkKDMpe2FuaW1hdGlvbi1kZWxheTouM3N9CkBrZXlmcmFtZXMgYm91bmNlezAlLDgwJSwxMDAl"
    "e3RyYW5zZm9ybTp0cmFuc2xhdGVZKDApO29wYWNpdHk6LjM1fTQwJXt0cmFuc2Zvcm06dHJhbnNs"
    "YXRlWSgtNXB4KTtvcGFjaXR5OjF9fQoKLyogc3BsYXNoICovCiNzcGxhc2h7cG9zaXRpb246Zml4"
    "ZWQ7aW5zZXQ6MDt6LWluZGV4OjMwMDtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1u"
    "O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2dhcDoxNnB4OwogIGJh"
    "Y2tncm91bmQ6dmFyKC0tYmcpO3RyYW5zaXRpb246b3BhY2l0eSAuNXMgZWFzZSx2aXNpYmlsaXR5"
    "IC41c30KI3NwbGFzaC5nb25le29wYWNpdHk6MDt2aXNpYmlsaXR5OmhpZGRlbn0KI3NwbGFzaCAu"
    "bWFya3t3aWR0aDo1OHB4O2hlaWdodDo1OHB4O2JvcmRlci1yYWRpdXM6MTRweDtkaXNwbGF5Omdy"
    "aWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpOwogIGNvbG9y"
    "OiNmZmZmZmY7Zm9udC13ZWlnaHQ6ODAwO2ZvbnQtc2l6ZToxLjFyZW07YW5pbWF0aW9uOm1hcmtp"
    "biAuN3MgY3ViaWMtYmV6aWVyKC4yLDEuNCwuNCwxKSBib3RofQojc3BsYXNoIGg0e21hcmdpbjow"
    "O2ZvbnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6MXJlbTtmb250LXdlaWdodDo4MDA7"
    "bGV0dGVyLXNwYWNpbmc6LS4wM2VtOwogIGFuaW1hdGlvbjpmYWRldXAgLjZzIC4xNXMgYm90aH0K"
    "I3NwbGFzaCBzbWFsbHtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjVyZW07bGV0dGVyLXNw"
    "YWNpbmc6LjIyZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2FuaW1hdGlvbjpmYWRldXAgLjZz"
    "IC4yNXMgYm90aH0KQGtleWZyYW1lcyBtYXJraW57ZnJvbXtvcGFjaXR5OjA7dHJhbnNmb3JtOnNj"
    "YWxlKC42KSByb3RhdGUoLTEyZGVnKX10b3tvcGFjaXR5OjE7dHJhbnNmb3JtOm5vbmV9fQpAa2V5"
    "ZnJhbWVzIGZhZGV1cHtmcm9te29wYWNpdHk6MDt0cmFuc2Zvcm06dHJhbnNsYXRlWSg4cHgpfXRv"
    "e29wYWNpdHk6MTt0cmFuc2Zvcm06bm9uZX19CgovKiAwOCDCtyBLaW5ldGljIGdyaWQgY2FudmFz"
    "ICovCiNrZ3twb3NpdGlvbjpmaXhlZDtpbnNldDowO3otaW5kZXg6MDtwb2ludGVyLWV2ZW50czpu"
    "b25lO29wYWNpdHk6LjM4fQoKLyogc3RhZ2dlcmVkIHJldmVhbCBpcyBkcml2ZW4gYnkgdGhlIFdl"
    "YiBBbmltYXRpb25zIEFQSSAoY2Fubm90IGNsYXNoIHdpdGggdGhlbWUgQ1NTKSAqLwoKLyogMDIg"
    "KyAyNSDCtyBEb2NrIHdpdGggc3ByaW5nIHBpbGwgKi8KbmF2I3RhYnMgLmRvY2t7cG9zaXRpb246"
    "cmVsYXRpdmV9Cm5hdiN0YWJzIC5kcGlsbHtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjA7dG9wOjA7"
    "ei1pbmRleDowO2JvcmRlci1yYWRpdXM6MThweDtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTsK"
    "ICBib3gtc2hhZG93OjAgNnB4IDE4cHggY29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tYWNjKSAzNSUs"
    "dHJhbnNwYXJlbnQpO3BvaW50ZXItZXZlbnRzOm5vbmV9Cm5hdiN0YWJzIGJ1dHRvbntwb3NpdGlv"
    "bjpyZWxhdGl2ZTt6LWluZGV4OjE7d2lsbC1jaGFuZ2U6dHJhbnNmb3JtOwogIHRyYW5zaXRpb246"
    "Y29sb3IgLjI2cyBjdWJpYy1iZXppZXIoLjIyLDEsLjM2LDEpLHRyYW5zZm9ybSAuMThzIGN1Ymlj"
    "LWJlemllciguMywxLjUsLjUsMSl9Cm5hdiN0YWJzIGJ1dHRvbi5vbntiYWNrZ3JvdW5kOnRyYW5z"
    "cGFyZW50O2NvbG9yOiNmZmZmZmZ9Cm5hdiN0YWJzIC5kcGlsbHtvcGFjaXR5OjB9Cm5hdiN0YWJz"
    "IC5kcGlsbC5yZWFkeXtvcGFjaXR5OjF9CgovKiAxNCDCtyBWLXRhYnMgc2xpZGluZyBpbmRpY2F0"
    "b3IgKi8KLnN1Ym5hdntwb3NpdGlvbjpyZWxhdGl2ZX0KLnN1Ym5hdiAudnBpbGx7cG9zaXRpb246"
    "YWJzb2x1dGU7bGVmdDowO3RvcDowO3otaW5kZXg6MDtib3JkZXItcmFkaXVzOjEzcHg7YmFja2dy"
    "b3VuZDp2YXIoLS1waWxsZ3JhZCk7dHJhbnNpdGlvbjpub25lO3BvaW50ZXItZXZlbnRzOm5vbmV9"
    "Ci5zdWJuYXYgYnV0dG9ue3Bvc2l0aW9uOnJlbGF0aXZlO3otaW5kZXg6MX0KLnN1Ym5hdiBidXR0"
    "b24ub257YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjb2xvcjojZmZmZmZmO2JvcmRlci1jb2xvcjp0"
    "cmFuc3BhcmVudH0KCi8qIDE1IMK3IEFuaW1hdGVkIHByb2ZpbGUgY2FyZCDigJQgdGlsdCArIGds"
    "YXJlICovCi50aWx0e3RyYW5zZm9ybS1zdHlsZTpwcmVzZXJ2ZS0zZDt0cmFuc2l0aW9uOnRyYW5z"
    "Zm9ybSAuMzVzIGN1YmljLWJlemllciguMjIsMSwuMzYsMSk7d2lsbC1jaGFuZ2U6dHJhbnNmb3Jt"
    "fQoudGlsdCAuZ2xhcmV7cG9zaXRpb246YWJzb2x1dGU7aW5zZXQ6MDtib3JkZXItcmFkaXVzOmlu"
    "aGVyaXQ7cG9pbnRlci1ldmVudHM6bm9uZTtvcGFjaXR5OjA7dHJhbnNpdGlvbjpvcGFjaXR5IC4z"
    "czsKICBiYWNrZ3JvdW5kOnJhZGlhbC1ncmFkaWVudCgyNDBweCBjaXJjbGUgYXQgdmFyKC0tbXgs"
    "NTAlKSB2YXIoLS1teSw1MCUpLHJnYmEoMjU1LDI1NSwyNTUsLjIyKSx0cmFuc3BhcmVudCA2MCUp"
    "fQoudGlsdDpob3ZlciAuZ2xhcmV7b3BhY2l0eToxfQouc2hpbmVjYXJke3Bvc2l0aW9uOnJlbGF0"
    "aXZlO292ZXJmbG93OmhpZGRlbn0KLnNoaW5lY2FyZDpiZWZvcmV7Y29udGVudDoiIjtwb3NpdGlv"
    "bjphYnNvbHV0ZTt0b3A6LTYwJTtsZWZ0Oi0zMCU7d2lkdGg6NTUlO2hlaWdodDoyMjAlO3RyYW5z"
    "Zm9ybTpyb3RhdGUoMThkZWcpOwogIGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRy"
    "YW5zcGFyZW50LHJnYmEoMjU1LDI1NSwyNTUsLjE2KSx0cmFuc3BhcmVudCk7YW5pbWF0aW9uOmNh"
    "cmRzaGluZSA1LjVzIGVhc2UtaW4tb3V0IGluZmluaXRlO3BvaW50ZXItZXZlbnRzOm5vbmV9CkBr"
    "ZXlmcmFtZXMgY2FyZHNoaW5lezAlLDcwJXtsZWZ0Oi00MCV9MTAwJXtsZWZ0OjEyMCV9fQoKLyog"
    "MDUgwrcgSG9sZCBhY3Rpb24gYnV0dG9uICovCi5ob2xke3Bvc2l0aW9uOnJlbGF0aXZlO292ZXJm"
    "bG93OmhpZGRlbn0KLmhvbGQgLmZpbGx7cG9zaXRpb246YWJzb2x1dGU7bGVmdDowO3RvcDowO2Jv"
    "dHRvbTowO3dpZHRoOjA7YmFja2dyb3VuZDpyZ2JhKDI1NSwyNTUsMjU1LC4yNSk7dHJhbnNpdGlv"
    "bjp3aWR0aCAuMDVzIGxpbmVhcn0KLmhvbGQuZG9uZXthbmltYXRpb246cG9wIC40cyBjdWJpYy1i"
    "ZXppZXIoLjIsMS42LC40LDEpfQpAa2V5ZnJhbWVzIHBvcHs1MCV7dHJhbnNmb3JtOnNjYWxlKDEu"
    "MDQpfX0KCi8qIDExIMK3IE1vcnBoaW5nIHNlYXJjaCAqLwoubXNlYXJjaHtkaXNwbGF5OmZsZXg7"
    "YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo4cHg7bWFyZ2luOjAgMCAxMHB4fQoubXNlYXJjaCAuZmll"
    "bGR3cmFwe2ZsZXg6MTttaW4td2lkdGg6MDtwb3NpdGlvbjpyZWxhdGl2ZTtoZWlnaHQ6NDBweH0K"
    "Lm1zZWFyY2ggaW5wdXR7d2lkdGg6MTAwJTtoZWlnaHQ6NDBweDtwYWRkaW5nOjAgMTNweCAwIDM2"
    "cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7CiAgYmFj"
    "a2dyb3VuZDp2YXIoLS1jYXJkKTtjb2xvcjp2YXIoLS10eCk7Zm9udC1mYW1pbHk6dmFyKC0tZm9u"
    "dCk7Zm9udC1zaXplOi44cmVtO291dGxpbmU6bm9uZTsKICB0cmFuc2l0aW9uOmJveC1zaGFkb3cg"
    "LjNzIGN1YmljLWJlemllciguMjIsMSwuMzYsMSksYm9yZGVyLWNvbG9yIC4zc30KLm1zZWFyY2gg"
    "aW5wdXQ6Zm9jdXN7Ym9yZGVyLWNvbG9yOnZhcigtLWFjYyk7Ym94LXNoYWRvdzowIDAgMCA0cHgg"
    "Y29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tYWNjKSAxNiUsdHJhbnNwYXJlbnQpfQoubXNlYXJjaCBz"
    "dmd7cG9zaXRpb246YWJzb2x1dGU7bGVmdDoxMnB4O3RvcDoxMXB4O3dpZHRoOjE3cHg7aGVpZ2h0"
    "OjE3cHg7Y29sb3I6dmFyKC0tdHgyKTt0cmFuc2l0aW9uOmNvbG9yIC4yNXN9Ci5tc2VhcmNoIGlu"
    "cHV0OmZvY3VzK3N2ZywubXNlYXJjaCAuZmllbGR3cmFwOmZvY3VzLXdpdGhpbiBzdmd7Y29sb3I6"
    "dmFyKC0tYWNjKX0KLmNoaXByb3d7ZGlzcGxheTpmbGV4O2dhcDo2cHg7b3ZlcmZsb3cteDphdXRv"
    "O3BhZGRpbmctYm90dG9tOjEwcHh9Ci5jaGlwcm93IGJ1dHRvbntmbGV4OjAgMCBhdXRvO2hlaWdo"
    "dDozMHB4O3BhZGRpbmc6MCAxMnB4O2JvcmRlci1yYWRpdXM6OTlweDtib3JkZXI6MXB4IHNvbGlk"
    "IHZhcigtLWxpbmUpOwogIGJhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4Mik7"
    "Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi43cmVtO2ZvbnQtd2VpZ2h0OjcwMDtj"
    "dXJzb3I6cG9pbnRlcjt3aGl0ZS1zcGFjZTpub3dyYXA7CiAgdHJhbnNpdGlvbjp0cmFuc2Zvcm0g"
    "LjJzIGN1YmljLWJlemllciguMywxLjUsLjUsMSksYmFja2dyb3VuZCAuMnMsY29sb3IgLjJzfQou"
    "Y2hpcHJvdyBidXR0b24ub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmZm"
    "Zjtib3JkZXItY29sb3I6dHJhbnNwYXJlbnQ7dHJhbnNmb3JtOnNjYWxlKDEuMDQpfQoKLyogc2tl"
    "bGV0b25zICgxMyDCtyBsb2FkZXIpICovCi5za3twb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpo"
    "aWRkZW47YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9yZGVyLXJhZGl1czp2YXIoLS1ycyk7aGVp"
    "Z2h0Ojc0cHg7bWFyZ2luLWJvdHRvbTo5cHh9Ci5zazphZnRlcntjb250ZW50OiIiO3Bvc2l0aW9u"
    "OmFic29sdXRlO2luc2V0OjA7YmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoOTBkZWcsdHJhbnNw"
    "YXJlbnQsY29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tdHgpIDEwJSx0cmFuc3BhcmVudCksdHJhbnNw"
    "YXJlbnQpOwogIHRyYW5zZm9ybTp0cmFuc2xhdGVYKC0xMDAlKTthbmltYXRpb246c2hpbSAxLjE1"
    "cyBpbmZpbml0ZX0KQGtleWZyYW1lcyBzaGlte3Rve3RyYW5zZm9ybTp0cmFuc2xhdGVYKDEwMCUp"
    "fX0KCi8qIDE5IMK3IEZlYXR1cmUgc3RyaXAgKi8KLmZlYXRze2Rpc3BsYXk6Z3JpZDtncmlkLXRl"
    "bXBsYXRlLWNvbHVtbnM6cmVwZWF0KDMsMWZyKTtnYXA6OHB4O21hcmdpbi1ib3R0b206MTJweH0K"
    "LmZlYXR7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUp"
    "O2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO3BhZGRpbmc6MTFweCA5cHg7dGV4dC1hbGlnbjpjZW50"
    "ZXI7CiAgdHJhbnNpdGlvbjp0cmFuc2Zvcm0gLjI1cyBjdWJpYy1iZXppZXIoLjIyLDEsLjM2LDEp"
    "fQouZmVhdDpob3Zlcnt0cmFuc2Zvcm06dHJhbnNsYXRlWSgtM3B4KX0KLmZlYXQgc3Zne3dpZHRo"
    "OjE3cHg7aGVpZ2h0OjE3cHg7Y29sb3I6dmFyKC0tYWNjKX0KLmZlYXQgYntkaXNwbGF5OmJsb2Nr"
    "O2ZvbnQtc2l6ZTouNjhyZW07bWFyZ2luLXRvcDo1cHh9Ci5mZWF0IHNtYWxse2Rpc3BsYXk6Ymxv"
    "Y2s7Zm9udC1zaXplOi42cmVtO2NvbG9yOnZhcigtLXR4Mil9CgovKiAwMyDCtyBXb3JrcyB3aGVl"
    "bCDigJQgcm90YXRpbmcgZGVhbCBkaWFsICovCi53aGVlbHtwb3NpdGlvbjpyZWxhdGl2ZTtoZWln"
    "aHQ6MTE4cHg7bWFyZ2luOjJweCAwIDEycHg7b3ZlcmZsb3c6aGlkZGVuO2JvcmRlci1yYWRpdXM6"
    "dmFyKC0tcik7CiAgYm9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5kOnZhcigt"
    "LWNhcmQpfQoud2hlZWwgLnJpbmd7cG9zaXRpb246YWJzb2x1dGU7cmlnaHQ6LTUycHg7dG9wOjUw"
    "JTt3aWR0aDoxNzZweDtoZWlnaHQ6MTc2cHg7bWFyZ2luLXRvcDotODhweDtib3JkZXItcmFkaXVz"
    "OjUwJTsKICBib3JkZXI6MXB4IGRhc2hlZCB2YXIoLS1saW5lKTthbmltYXRpb246c3BpbiAyNnMg"
    "bGluZWFyIGluZmluaXRlfQoud2hlZWwgLnJpbmcgYntwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjUw"
    "JTt0b3A6NTAlO3dpZHRoOjUwcHg7aGVpZ2h0OjI0cHg7bWFyZ2luOi0xMnB4IDAgMCAtMjVweDti"
    "b3JkZXItcmFkaXVzOjk5cHg7CiAgZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtmb250"
    "LXNpemU6LjYycmVtO2ZvbnQtd2VpZ2h0OjgwMDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtib3Jk"
    "ZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2NvbG9yOnZhcigtLXR4Mil9Ci53aGVlbCAuY2Fwe3Bv"
    "c2l0aW9uOmFic29sdXRlO2luc2V0OjA7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVt"
    "bjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO3BhZGRpbmc6MCAxNnB4OwogIGJhY2tncm91bmQ6bGlu"
    "ZWFyLWdyYWRpZW50KDkwZGVnLHZhcigtLWNhcmQpIDAgNTglLHRyYW5zcGFyZW50KX0KLndoZWVs"
    "IC5jYXA+KnttYXgtd2lkdGg6NjIlfQoud2hlZWwgLmNhcCBlbXtmb250LXN0eWxlOm5vcm1hbDtm"
    "b250LXNpemU6LjZyZW07bGV0dGVyLXNwYWNpbmc6LjE4ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJj"
    "YXNlO2NvbG9yOnZhcigtLWFjYyk7Zm9udC13ZWlnaHQ6ODAwfQoud2hlZWwgLmNhcCBie2ZvbnQt"
    "ZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6MS4wNXJlbTtmb250LXdlaWdodDo4MDB9CkBr"
    "ZXlmcmFtZXMgc3Bpbnt0b3t0cmFuc2Zvcm06cm90YXRlKDM2MGRlZyl9fQoKLyogc3VjY2VzcyBi"
    "dXJzdCAqLwojZnh7cG9zaXRpb246Zml4ZWQ7aW5zZXQ6MDt6LWluZGV4OjE1MDtkaXNwbGF5Om5v"
    "bmU7cGxhY2UtaXRlbXM6Y2VudGVyO3BvaW50ZXItZXZlbnRzOm5vbmV9CiNmeC5vbntkaXNwbGF5"
    "OmdyaWR9CiNmeCAucmluZ3t3aWR0aDo4MnB4O2hlaWdodDo4MnB4O2JvcmRlci1yYWRpdXM6NTAl"
    "O2JvcmRlcjoycHggc29saWQgdmFyKC0tb2spO2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50"
    "ZXI7CiAgY29sb3I6dmFyKC0tb2spO2FuaW1hdGlvbjpidXJzdCAuNzVzIGN1YmljLWJlemllcigu"
    "MiwxLjMsLjQsMSl9CiNmeCAucmluZyBzdmd7d2lkdGg6MzRweDtoZWlnaHQ6MzRweH0KI2Z4IC53"
    "YXZle3Bvc2l0aW9uOmFic29sdXRlO3dpZHRoOjgycHg7aGVpZ2h0OjgycHg7Ym9yZGVyLXJhZGl1"
    "czo1MCU7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1vayk7YW5pbWF0aW9uOndhdmUgLjlzIGVhc2Ut"
    "b3V0IGZvcndhcmRzfQpAa2V5ZnJhbWVzIGJ1cnN0e2Zyb217dHJhbnNmb3JtOnNjYWxlKC40KTtv"
    "cGFjaXR5OjB9dG97dHJhbnNmb3JtOnNjYWxlKDEpO29wYWNpdHk6MX19CkBrZXlmcmFtZXMgd2F2"
    "ZXt0b3t0cmFuc2Zvcm06c2NhbGUoMy40KTtvcGFjaXR5OjB9fQoKLyogbnVtYmVyIHRpY2tlciAq"
    "LwoudGlja3tmb250LXZhcmlhbnQtbnVtZXJpYzp0YWJ1bGFyLW51bXN9CgpAbWVkaWEocHJlZmVy"
    "cy1yZWR1Y2VkLW1vdGlvbjpyZWR1Y2Upeyp7YW5pbWF0aW9uOm5vbmUhaW1wb3J0YW50O3RyYW5z"
    "aXRpb246bm9uZSFpbXBvcnRhbnR9fQoKLmhlcm97YmFja2dyb3VuZC1zaXplOjE4MCUgMTgwJTth"
    "bmltYXRpb246Z21vdmUgOXMgZWFzZSBpbmZpbml0ZX1Aa2V5ZnJhbWVzIGdtb3ZlezAlLDEwMCV7"
    "YmFja2dyb3VuZC1wb3NpdGlvbjowJSA1MCV9NTAle2JhY2tncm91bmQtcG9zaXRpb246MTAwJSA1"
    "MCV9fS5oZXJvIC5raWNrLC5oZXJvIHB7Y29sb3I6I2ZmZiFpbXBvcnRhbnQ7b3BhY2l0eTouOTV9"
    "Lmhlcm8gaDF7Y29sb3I6I2ZmZn0uaGVybyAuYnRue2JhY2tncm91bmQ6I2ZmZjtjb2xvcjojZmY0"
    "YTJmfS5oZXJvIC5idG4uZ2hvc3R7Y29sb3I6I2ZmZjtib3JkZXItY29sb3I6cmdiYSgyNTUsMjU1"
    "LDI1NSwuNyl9Cjwvc3R5bGU+CjwvaGVhZD4KPGJvZHk+Cgo8Y2FudmFzIGlkPSJrZyI+PC9jYW52"
    "YXM+CjxkaXYgaWQ9InNwbGFzaCI+PGRpdiBjbGFzcz0ibWFyayIgaWQ9InNwbWFyayI+WlQ8L2Rp"
    "dj48ZGl2IGNsYXNzPSJkb3RncmlkIiBpZD0iZGciPjwvZGl2PgogIDxoNCBpZD0ic3BuYW1lIj5a"
    "b3JvIFRHIFN0b3JlPC9oND48c21hbGwgaWQ9InNwdGFnIj5XYXJtLCBxdWljayB0b3AtdXBzPC9z"
    "bWFsbD48L2Rpdj4KPHN2ZyB3aWR0aD0iMCIgaGVpZ2h0PSIwIiBzdHlsZT0icG9zaXRpb246YWJz"
    "b2x1dGUiPjxkZWZzPgogIDxmaWx0ZXIgaWQ9ImxnIiB4PSIwJSIgeT0iMCUiIHdpZHRoPSIxMDAl"
    "IiBoZWlnaHQ9IjEwMCUiIGNvbG9yLWludGVycG9sYXRpb24tZmlsdGVycz0ic1JHQiI+CiAgICA8"
    "ZmVUdXJidWxlbmNlIHR5cGU9ImZyYWN0YWxOb2lzZSIgYmFzZUZyZXF1ZW5jeT0iMC4wNSAwLjA1"
    "IiBudW1PY3RhdmVzPSIxIiBzZWVkPSIxIiByZXN1bHQ9InQiLz4KICAgIDxmZUdhdXNzaWFuQmx1"
    "ciBpbj0idCIgc3RkRGV2aWF0aW9uPSIyIiByZXN1bHQ9ImIiLz4KICAgIDxmZURpc3BsYWNlbWVu"
    "dE1hcCBpbj0iU291cmNlR3JhcGhpYyIgaW4yPSJiIiBzY2FsZT0iNzAiIHhDaGFubmVsU2VsZWN0"
    "b3I9IlIiIHlDaGFubmVsU2VsZWN0b3I9IkIiLz4KICA8L2ZpbHRlcj48L2RlZnM+PC9zdmc+Cjxk"
    "aXYgaWQ9ImZ4Ij48ZGl2IGNsYXNzPSJ3YXZlIj48L2Rpdj48ZGl2IGNsYXNzPSJyaW5nIj48c3Zn"
    "IHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0"
    "cm9rZS13aWR0aD0iMi40IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0i"
    "cm91bmQiPjxwYXRoIGQ9Ik0yMCA2IDkgMTdsLTUtNSIvPjwvc3ZnPjwvZGl2PjwvZGl2Pgo8ZGl2"
    "IGlkPSJzdyI+PGVtPlByZXZpZXc8L2VtPgogIDxidXR0b24gZGF0YS1zdz0iYXBwIiBjbGFzcz0i"
    "b24iPlN0b3JlPC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXN3PSJhZG1pbiI+QWRtaW48L2J1dHRv"
    "bj4KPC9kaXY+Cgo8ZGl2IGlkPSJ2aWV3LWFwcCI+CjxkaXYgaWQ9InNoZWxsIj4KICA8aGVhZGVy"
    "IGlkPSJ0b3BiYXIiPgogICAgPGRpdiBjbGFzcz0ibG9nbyI+WlQ8L2Rpdj4KICAgIDxkaXYgY2xh"
    "c3M9ImJyYW5kIj48YiBpZD0iYm5hbWUiPlpvcm8gVEcgU3RvcmU8L2I+PHNwYW4gaWQ9ImJ0YWci"
    "Pldhcm0sIHF1aWNrIHRvcC11cHM8L3NwYW4+PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJpY29u"
    "Y2hpcCBoaWRkZW4iIGlkPSJjYXJ0Y2hpcCIgdGl0bGU9IkNhcnQiPjxzdmcgdmlld0JveD0iMCAw"
    "IDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIy"
    "IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9"
    "Ik02IDIgMyA2djE0YTIgMiAwIDAgMCAyIDJoMTRhMiAyIDAgMCAwIDItMlY2bC0zLTR6Ii8+PHBh"
    "dGggZD0iTTMgNmgxOCIvPjxwYXRoIGQ9Ik0xNiAxMGE0IDQgMCAwIDEtOCAwIi8+PC9zdmc+PGkg"
    "aWQ9ImNhcnRuIj4wPC9pPjwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYmFsY2hpcCIgZGF0"
    "YS1nbz0id2FsbGV0Ij48c3BhbiBjbGFzcz0icGx1cyI+Kzwvc3Bhbj7igrk8c3BhbiBpZD0iYmFs"
    "IiBjbGFzcz0idGljayI+MjQ4LjUwPC9zcGFuPjwvYnV0dG9uPgogIDwvaGVhZGVyPgogIDxtYWlu"
    "IGlkPSJzdGFnZSI+PC9tYWluPgo8L2Rpdj4KPG5hdiBpZD0idGFicyI+PGRpdiBjbGFzcz0iZG9j"
    "ayI+PHNwYW4gY2xhc3M9ImRwaWxsIiBpZD0iZHBpbGwiPjwvc3Bhbj4KICA8YnV0dG9uIGRhdGEt"
    "dGFiPSJzaG9wIiBjbGFzcz0ib24iPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25l"
    "IiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0i"
    "cm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik02IDIgMyA2djE0YTIgMiAw"
    "IDAgMCAyIDJoMTRhMiAyIDAgMCAwIDItMlY2bC0zLTR6Ii8+PHBhdGggZD0iTTMgNmgxOCIvPjxw"
    "YXRoIGQ9Ik0xNiAxMGE0IDQgMCAwIDEtOCAwIi8+PC9zdmc+PHNwYW4gZGF0YS1uYXY9InNob3Ai"
    "PlNob3A8L3NwYW4+PC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXRhYj0ib3JkZXJzIj48c3ZnIHZp"
    "ZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9r"
    "ZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5k"
    "Ij48cGF0aCBkPSJNMjEgOGEyIDIgMCAwIDAtMS0xLjczbC03LTRhMiAyIDAgMCAwLTIgMGwtNyA0"
    "QTIgMiAwIDAgMCAzIDh2OGEyIDIgMCAwIDAgMSAxLjczbDcgNGEyIDIgMCAwIDAgMiAwbDctNEEy"
    "IDIgMCAwIDAgMjEgMTZaIi8+PHBhdGggZD0ibTMuMyA3IDguNyA1IDguNy01Ii8+PHBhdGggZD0i"
    "TTEyIDIyVjEyIi8+PC9zdmc+PHNwYW4gZGF0YS1uYXY9Im9yZGVycyI+T3JkZXJzPC9zcGFuPjwv"
    "YnV0dG9uPgogIDxidXR0b24gZGF0YS10YWI9ImVhcm4iPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0"
    "IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJv"
    "a2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0xMyAy"
    "IDMgMTRoOWwtMSA4IDEwLTEyaC05eiIvPjwvc3ZnPjxzcGFuIGRhdGEtbmF2PSJlYXJuIj5FYXJu"
    "PC9zcGFuPjwvYnV0dG9uPgogIDxidXR0b24gZGF0YS10YWI9IndhbGxldCI+PHN2ZyB2aWV3Qm94"
    "PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3VycmVudENvbG9yIiBzdHJva2Utd2lk"
    "dGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBh"
    "dGggZD0iTTE5IDdWNWEyIDIgMCAwIDAtMi0ySDVhMiAyIDAgMCAwIDAgNGgxNWEyIDIgMCAwIDEg"
    "MiAydjlhMiAyIDAgMCAxLTIgMkg1YTIgMiAwIDAgMS0yLTJWNSIvPjxwYXRoIGQ9Ik0xOCAxMmgu"
    "MDEiLz48L3N2Zz48c3BhbiBkYXRhLW5hdj0id2FsbGV0Ij5XYWxsZXQ8L3NwYW4+PC9idXR0b24+"
    "CiAgPGJ1dHRvbiBkYXRhLXRhYj0ieW91Ij48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0i"
    "bm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVj"
    "YXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTkgMjF2LTJhNCA0"
    "IDAgMCAwLTQtNEg5YTQgNCAwIDAgMC00IDR2MiIvPjxjaXJjbGUgY3g9IjEyIiBjeT0iNyIgcj0i"
    "NCIvPjwvc3ZnPjxzcGFuIGRhdGEtbmF2PSJ5b3UiPllvdTwvc3Bhbj48L2J1dHRvbj4KPC9kaXY+"
    "PC9uYXY+CjwvZGl2PgoKPGRpdiBpZD0idmlldy1hZG1pbiIgY2xhc3M9ImhpZGRlbiI+PGRpdiBp"
    "ZD0ic2hlbGwiPgogIDxoZWFkZXIgaWQ9InRvcGJhciI+CiAgICA8ZGl2IGNsYXNzPSJsb2dvIj48"
    "c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3Ii"
    "IHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49"
    "InJvdW5kIj48cGF0aCBkPSJNMjAgMTNjMCA1LTMuNSA3LjUtNy42NiA4Ljk1YTEgMSAwIDAgMS0u"
    "NjctLjAxQzcuNSAyMC41IDQgMTggNCAxM1Y2YTEgMSAwIDAgMSAxLTFjMiAwIDQuNS0xLjIgNi4y"
    "NC0yLjcyYTEuMTcgMS4xNyAwIDAgMSAxLjUyIDBDMTQuNTEgMy44MSAxNyA1IDE5IDVhMSAxIDAg"
    "MCAxIDEgMXoiLz48L3N2Zz48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJyYW5kIj48Yj5BZG1pbiBD"
    "b25zb2xlPC9iPjxzcGFuIGlkPSJhYm5hbWUiPlpvcm8gVEcgU3RvcmU8L3NwYW4+PC9kaXY+CiAg"
    "ICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+PGkgY2xhc3M9ImRvdCI+PC9pPkxpdmU8L3NwYW4+CiAg"
    "PC9oZWFkZXI+CiAgPGRpdiBjbGFzcz0ic3VibmF2IiBpZD0iYXN1YiI+PHNwYW4gY2xhc3M9InZw"
    "aWxsIiBpZD0idnBpbGwiPjwvc3Bhbj4KICAgIDxidXR0b24gZGF0YS1hPSJkYXNoIiBjbGFzcz0i"
    "b24iPkRhc2hib2FyZDwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9ImNoYW5uZWxzIj5DaGFu"
    "bmVsczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9ImxvZ3MiPkxvZ3M8L2J1dHRvbj4KICAg"
    "IDxidXR0b24gZGF0YS1hPSJwYXkiPkF1dG8tUGF5PC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEt"
    "YT0iY29udGVudCI+Q29udGVudDwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9ImZlYXR1cmVz"
    "Ij5GZWF0dXJlczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9Im1hcmtldCI+TWFya2V0aW5n"
    "PC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0iYW5hbHl0aWNzIj5BbmFseXRpY3M8L2J1dHRv"
    "bj4KICAgIDxidXR0b24gZGF0YS1hPSJzZWN1cml0eSI+U2VjdXJpdHk8L2J1dHRvbj4KICAgIDxi"
    "dXR0b24gZGF0YS1hPSJzdG9jayI+U3RvY2s8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJv"
    "cmRlcnMiPk9yZGVyczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9InVzZXJzIj5Vc2Vyczwv"
    "YnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9InNldHRpbmdzIj5TZXR0aW5nczwvYnV0dG9uPgog"
    "IDwvZGl2PgogIDxtYWluIGlkPSJhc3RhZ2UiPjwvbWFpbj4KPC9kaXY+PC9kaXY+Cgo8ZGl2IGlk"
    "PSJzaGVldCI+PGRpdiBjbGFzcz0iYmciIGRhdGEtY2xvc2U+PC9kaXY+PGRpdiBjbGFzcz0icGFu"
    "ZWwiIGlkPSJwYW5lbCI+PC9kaXY+PC9kaXY+CjxkaXYgY2xhc3M9InRvYXN0IiBpZD0idG9hc3Qi"
    "PjwvZGl2PgoKPHNjcmlwdD4KLyog4pSA4pSAIGxpdmUtbW9kZSBzdGF0ZSwgZGVjbGFyZWQgZmly"
    "c3Qgc28gbm90aGluZyBjYW4gcmVhZCBpdCB0b28gZWFybHkg4pSA4pSAICovCnZhciBMSVZFPWZh"
    "bHNlLExPQ0tFRD1mYWxzZSxNRT17YWRtaW46ZmFsc2Usb3duZXI6ZmFsc2UscGVybXM6e319Owp2"
    "YXIgVEc9d2luZG93LlRlbGVncmFtJiZ3aW5kb3cuVGVsZWdyYW0uV2ViQXBwfHxudWxsOwpmdW5j"
    "dGlvbiByZWFkSW5pdERhdGEoKXsKICB0cnl7aWYoVEcmJlRHLmluaXREYXRhJiZURy5pbml0RGF0"
    "YS5sZW5ndGgpcmV0dXJuIFRHLmluaXREYXRhO31jYXRjaChlKXt9CiAgLyogVGVsZWdyYW0gYWxz"
    "byBwdXRzIGl0IGluIHRoZSBVUkwsIHNvIHRoZSBhcHAgc3RpbGwgd29ya3MgaWYgdGhlIFNESyBp"
    "cyBibG9ja2VkICovCiAgdHJ5ewogICAgY29uc3QgbT0vWyMmP110Z1dlYkFwcERhdGE9KFteJl0q"
    "KS8uZXhlYyhsb2NhdGlvbi5oYXNofHwiIik7CiAgICBpZihtJiZtWzFdKXJldHVybiBkZWNvZGVV"
    "UklDb21wb25lbnQobVsxXSk7CiAgfWNhdGNoKGUpe30KICB0cnl7CiAgICBjb25zdCBxPW5ldyBV"
    "UkxTZWFyY2hQYXJhbXMobG9jYXRpb24uc2VhcmNoKTsKICAgIGNvbnN0IHY9cS5nZXQoInRnV2Vi"
    "QXBwRGF0YSIpOwogICAgaWYodilyZXR1cm4gdjsKICB9Y2F0Y2goZSl7fQogIHRyeXsKICAgIGNv"
    "bnN0IHNhdmVkPXNlc3Npb25TdG9yYWdlLmdldEl0ZW0oInRnSW5pdCIpOwogICAgaWYoc2F2ZWQp"
    "cmV0dXJuIHNhdmVkOwogIH1jYXRjaChlKXt9CiAgcmV0dXJuICIiOwp9CmZ1bmN0aW9uIGluaXRp"
    "YWxzKG5hbWUpewogIGNvbnN0IHc9KG5hbWV8fCIiKS50cmltKCkuc3BsaXQoL1xzKy8pLmZpbHRl"
    "cihCb29sZWFuKTsKICBpZighdy5sZW5ndGgpcmV0dXJuICJTVCI7CiAgcmV0dXJuICgod1swXVsw"
    "XXx8IiIpKyh3Lmxlbmd0aD4xPyh3WzFdWzBdfHwiIik6KHdbMF1bMV18fCIiKSkpLnRvVXBwZXJD"
    "YXNlKCk7Cn0KZnVuY3Rpb24gcGFpbnRCcmFuZChuYW1lLHRhZyl7CiAgaWYobmFtZSl7CiAgICBj"
    "b25zdCBuPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzcG5hbWUiKTsgaWYobiluLnRleHRDb250"
    "ZW50PW5hbWU7CiAgICBjb25zdCBtPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzcG1hcmsiKTsg"
    "aWYobSltLnRleHRDb250ZW50PWluaXRpYWxzKG5hbWUpOwogICAgY29uc3QgYj1kb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgiYm5hbWUiKTsgaWYoYiliLnRleHRDb250ZW50PW5hbWU7CiAgICBjb25z"
    "dCBhYj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYWJuYW1lIik7IGlmKGFiKWFiLnRleHRDb250"
    "ZW50PW5hbWU7CiAgICBkb2N1bWVudC50aXRsZT1uYW1lOwogIH0KICBpZih0YWcpewogICAgY29u"
    "c3QgdD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3B0YWciKTsgaWYodCl0LnRleHRDb250ZW50"
    "PXRhZzsKICAgIGNvbnN0IGJ0PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJidGFnIik7IGlmKGJ0"
    "KWJ0LnRleHRDb250ZW50PXRhZzsKICB9Cn0KdHJ5ewogIGNvbnN0IGNuPWxvY2FsU3RvcmFnZS5n"
    "ZXRJdGVtKCJzdG9yZU5hbWUiKSwgY3Q9bG9jYWxTdG9yYWdlLmdldEl0ZW0oInN0b3JlVGFnIik7"
    "CiAgaWYoY258fGN0KXBhaW50QnJhbmQoY24sY3QpOwp9Y2F0Y2goZSl7fQp2YXIgSU5JVD1yZWFk"
    "SW5pdERhdGEoKTsKdHJ5e2lmKElOSVQpc2Vzc2lvblN0b3JhZ2Uuc2V0SXRlbSgidGdJbml0IixJ"
    "TklUKTt9Y2F0Y2goZSl7fQpmdW5jdGlvbiByZWFkQXBwS2V5KCl7CiAgdHJ5ewogICAgY29uc3Qg"
    "cT1uZXcgVVJMU2VhcmNoUGFyYW1zKGxvY2F0aW9uLnNlYXJjaCk7CiAgICBpZihxLmdldCgiayIp"
    "KXJldHVybiBxLmdldCgiayIpOwogICAgY29uc3QgbT0vWyMmP11rPShbXiZdKikvLmV4ZWMobG9j"
    "YXRpb24uaGFzaHx8IiIpOwogICAgaWYobSYmbVsxXSlyZXR1cm4gZGVjb2RlVVJJQ29tcG9uZW50"
    "KG1bMV0pOwogICAgcmV0dXJuIHNlc3Npb25TdG9yYWdlLmdldEl0ZW0oImFwcEtleSIpfHwiIjsK"
    "ICB9Y2F0Y2goZSl7cmV0dXJuICIiO30KfQp2YXIgQVBQS0VZPXJlYWRBcHBLZXkoKTsKdHJ5e2lm"
    "KEFQUEtFWSlzZXNzaW9uU3RvcmFnZS5zZXRJdGVtKCJhcHBLZXkiLEFQUEtFWSk7fWNhdGNoKGUp"
    "e30KLyogZGVtbyBkYXRhIG9ubHkg4oCUIG5vIHRva2Vucywgbm8gVVBJIElELCBubyBHbWFpbCBj"
    "cmVkZW50aWFscyAqLwpsZXQgQ0ZHPXtuYW1lOiJab3JvIFRHIFN0b3JlIix0YWc6Ildhcm0sIHF1"
    "aWNrIHRvcC11cHMiLGN1cjoi4oK5In07Ci8qIOKUgOKUgCBFZGl0YWJsZSBjb3B5IOKAlCBldmVy"
    "eSBzdHJpbmcgYmVsb3cgaXMgZWRpdGFibGUgaW4gQWRtaW4g4pa4IENvbnRlbnQg4pSA4pSAICov"
    "CmNvbnN0IENNUz17CiBzdG9yZU5hbWU6Ilpvcm8gVEcgU3RvcmUiLCBzdG9yZVRhZzoiV2FybSwg"
    "cXVpY2sgdG9wLXVwcyIsCiBlbXB0eVRpdGxlOiJSZXN0b2NraW5nIiwgZW1wdHlCb2R5OiJFdmVy"
    "eXRoaW5nIGp1c3Qgc29sZCBvdXQuIEZyZXNoIGFjY291bnRzIGxhbmQgaGVyZSBzaG9ydGx5IOKA"
    "lCB0dXJuIG9uIGFuIGFsZXJ0IGFuZCB5b3Ugd2lsbCBoZWFyIGZpcnN0LiIsCiB0aWNrMToiPGI+"
    "SW5zdGFudCBkZWxpdmVyeTwvYj4gb24gZXZlcnkgY291bnRyeSIsIHRpY2syOiJUb3AtdXBzIDxi"
    "PmNvbmZpcm1lZCBhdXRvbWF0aWNhbGx5PC9iPiIsCiB0aWNrMzoiRmFpbGVkIGxvZ2luPyA8Yj5B"
    "dXRvIHJlZnVuZDwvYj4iLCB0aWNrNDoiRGFpbHkgY2hlY2staW4gPGI+4oK5MC40MDwvYj4iLAog"
    "aGVyb0tpY2s6IkZyZXNoIGRyb3AiLCBoZXJvSDE6IlRvcCB1cC48YnI+R3JhYi4gR28uIiwKIGhl"
    "cm9TdWI6Ikluc3RhbnQgZGVsaXZlcnksIE9UUCBpbi1hcHAsIHdhbGxldCB0b3AtdXBzIHZlcmlm"
    "aWVkIGF1dG9tYXRpY2FsbHkuIHtufSBjb3VudHJpZXMgbGl2ZS4iLAogaGVyb0J0bjE6IkJyb3dz"
    "ZSBTdG9jayIsIGhlcm9CdG4yOiJBZGQgRnVuZHMiLAogZmVhdDE6In44cyIsIGZlYXQxczoiZGVs"
    "aXZlcnkiLCBmZWF0MjoiQXV0byIsIGZlYXQyczoicmVmdW5kcyIsIGZlYXQzOiJVUEkiLCBmZWF0"
    "M3M6ImF1dG8tdmVyaWZ5IiwKIGZsYXNoUGlsbDoiRmxhc2gg4oiSMjAlIiwgZmxhc2hUZXh0OiJF"
    "bmRzIGluIiwgZmxhc2hCdG46IlZpZXciLAogd2hlZWxLaWNrOiJSb3RhdGluZyBkZWFscyIsIHdo"
    "ZWVsVGl0bGU6IlVwIHRvIDI4JSBvZmYiLCB3aGVlbFdvcmRzOiJJTlNUQU5ULCBBVVRPLVZFUklG"
    "WSwgUkVGVU5EUywgMjTDlzciLAogc2hvcEhlYWQ6IkluIFN0b2NrIiwgc2hvcFNvcnQ6IlNvcnQi"
    "LCBzZWFyY2hQaDoiU2VhcmNoIGNvdW50cnkgb3IgY29kZeKApiIsIGFsZXJ0c0J0bjoiQWxlcnRz"
    "IiwKIHdhbEtpY2s6IkJhbGFuY2UiLCB3YWxTdWI6IlBheSBieSBVUEkg4oCUIHRhcCBWZXJpZnkg"
    "YWZ0ZXIgcGF5aW5nIGFuZCB5b3VyIHdhbGxldCBpcyBjcmVkaXRlZCBpbiBzZWNvbmRzLiIsCiB3"
    "YWxBZGQ6IkFkZCBGdW5kcyIsIHBheVRpdGxlOiJQYXltZW50IERldGFpbHMiLCBwYXlTdWI6IkFk"
    "bWluIHNldHMgdGhlIFVQSSBoYW5kbGUg4oCUIG5vdGhpbmcgaXMgaGFyZC1jb2RlZCBpbiB0aGUg"
    "YXBwLiIsCiBwYXlQcmVmaXg6IlBBWSIsIHBheVdpbmRvdzoiNSIsCiBzaGVldFRpdGxlOiJBZGQi"
    "LCBzaGVldFN1YjoiUGF5IHRoZSBleGFjdCBhbW91bnQsIHRoZW4gdGFwIFZlcmlmeSBQYXltZW50"
    "LiBUaGUgcmVxdWVzdCBleHBpcmVzIGluIHttfSBtaW51dGVzLiIsCiBwYXlCdG46IlBheSBOb3ci"
    "LCB2ZXJpZnlCdG46IlZlcmlmeSBQYXltZW50IiwgdXRyQnRuOiJOb3QgZGV0ZWN0ZWQ/IFN1Ym1p"
    "dCBVVFIiLAogc3RlcDE6IlBheW1lbnQgcmVxdWVzdCBjcmVhdGVkIiwgc3RlcDI6IlBheSB0aGUg"
    "ZXhhY3QgYW1vdW50IGluIGFueSBVUEkgYXBwIiwKIHN0ZXAzOiJUYXAgVmVyaWZ5IOKAlCB3ZSBj"
    "b25maXJtIHlvdXIgcGF5bWVudCIsIHN0ZXA0OiJXYWxsZXQgY3JlZGl0ZWQiLAogbXNnQ2hlY2tp"
    "bmc6IkNoZWNraW5nIHlvdXIgcGF5bWVudOKApiIsIG1zZ09rOiJWZXJpZmllZCDigJQgd2FsbGV0"
    "IGNyZWRpdGVkIiwKIG1zZ05vbmU6Ik5vIHBheW1lbnQgZm91bmQgeWV0LiBQYXkgZmlyc3QsIHRo"
    "ZW4gdGFwIFZlcmlmeS4iLCBtc2dFeHBpcmVkOiJSZXF1ZXN0IGV4cGlyZWQg4oCUIGNyZWF0ZSBh"
    "IG5ldyBvbmUuIiwKIG5hdlNob3A6IlNob3AiLCBuYXZPcmRlcnM6Ik9yZGVycyIsIG5hdkVhcm46"
    "IkVhcm4iLCBuYXZXYWxsZXQ6IldhbGxldCIsIG5hdllvdToiWW91Igp9Owpjb25zdCBDTVMwPU9i"
    "amVjdC5hc3NpZ24oe30sQ01TKTsKY29uc3QgQ01TTUVUQT1bCiBbIlN0b3JlIGhlYWRlciIsW1si"
    "c3RvcmVOYW1lIiwiU3RvcmUgbmFtZSJdLFsic3RvcmVUYWciLCJUYWdsaW5lIl1dXSwKIFsiU2Ny"
    "b2xsaW5nIHRpY2tlciIsW1sidGljazEiLCJUaWNrZXIgMSJdLFsidGljazIiLCJUaWNrZXIgMiJd"
    "LFsidGljazMiLCJUaWNrZXIgMyJdLFsidGljazQiLCJUaWNrZXIgNCJdXV0sCiBbIkhlcm8gc2Vj"
    "dGlvbiIsW1siaGVyb0tpY2siLCJTbWFsbCBsYWJlbCBhYm92ZSB0aXRsZSJdLFsiaGVyb0gxIiwi"
    "QmlnIHRpdGxlIl0sWyJoZXJvU3ViIiwiUGFyYWdyYXBoIHVuZGVyIHRoZSB0aXRsZSJdLFsiaGVy"
    "b0J0bjEiLCJMZWZ0IGJ1dHRvbiJdLFsiaGVyb0J0bjIiLCJSaWdodCBidXR0b24iXV1dLAogWyJG"
    "ZWF0dXJlIGJhZGdlcyIsW1siZmVhdDEiLCJCYWRnZSAxIHZhbHVlIl0sWyJmZWF0MXMiLCJCYWRn"
    "ZSAxIGNhcHRpb24iXSxbImZlYXQyIiwiQmFkZ2UgMiB2YWx1ZSJdLFsiZmVhdDJzIiwiQmFkZ2Ug"
    "MiBjYXB0aW9uIl0sWyJmZWF0MyIsIkJhZGdlIDMgdmFsdWUiXSxbImZlYXQzcyIsIkJhZGdlIDMg"
    "Y2FwdGlvbiJdXV0sCiBbIkZsYXNoIHNhbGUgc3RyaXAiLFtbImZsYXNoUGlsbCIsIlBpbGwgdGV4"
    "dCJdLFsiZmxhc2hUZXh0IiwiQ291bnRkb3duIGxhYmVsIl0sWyJmbGFzaEJ0biIsIkJ1dHRvbiJd"
    "XV0sCiBbIlJvdGF0aW5nIGRlYWxzIGNhcmQiLFtbIndoZWVsS2ljayIsIlNtYWxsIGxhYmVsIl0s"
    "WyJ3aGVlbFRpdGxlIiwiSGVhZGxpbmUiXSxbIndoZWVsV29yZHMiLCJNYXJxdWVlIHdvcmRzIChj"
    "b21tYSBzZXBhcmF0ZWQpIl1dXSwKIFsiU2hvcCBsaXN0IixbWyJzaG9wSGVhZCIsIlNlY3Rpb24g"
    "aGVhZGluZyJdLFsic2hvcFNvcnQiLCJTb3J0IGxpbmsiXSxbInNlYXJjaFBoIiwiU2VhcmNoIHBs"
    "YWNlaG9sZGVyIl0sWyJhbGVydHNCdG4iLCJBbGVydHMgYnV0dG9uIl1dXSwKIFsiV2FsbGV0IHNj"
    "cmVlbiIsW1sid2FsS2ljayIsIkxhYmVsIGFib3ZlIGJhbGFuY2UiXSxbIndhbFN1YiIsIkJhbGFu"
    "Y2UgZGVzY3JpcHRpb24iXSxbIndhbEFkZCIsIkFkZCBmdW5kcyBidXR0b24iXSxbInBheVRpdGxl"
    "IiwiUGF5bWVudCBjYXJkIHRpdGxlIl0sWyJwYXlTdWIiLCJQYXltZW50IGNhcmQgZGVzY3JpcHRp"
    "b24iXV1dLAogWyJQYXltZW50IHNoZWV0IixbWyJzaGVldFRpdGxlIiwiU2hlZXQgdGl0bGUgcHJl"
    "Zml4Il0sWyJzaGVldFN1YiIsIlNoZWV0IGRlc2NyaXB0aW9uICh7bX0gPSBtaW51dGVzKSJdLFsi"
    "cGF5QnRuIiwiUGF5IGJ1dHRvbiJdLFsidmVyaWZ5QnRuIiwiVmVyaWZ5IGJ1dHRvbiJdLFsidXRy"
    "QnRuIiwiTWFudWFsIGZhbGxiYWNrIGJ1dHRvbiJdLFsicGF5UHJlZml4IiwiUmVmZXJlbmNlIHBy"
    "ZWZpeCJdLFsicGF5V2luZG93IiwiUGF5bWVudCB3aW5kb3cgKG1pbnV0ZXMpIl1dXSwKIFsiUGF5"
    "bWVudCBzdGVwcyIsW1sic3RlcDEiLCJTdGVwIDEiXSxbInN0ZXAyIiwiU3RlcCAyIl0sWyJzdGVw"
    "MyIsIlN0ZXAgMyJdLFsic3RlcDQiLCJTdGVwIDQiXV1dLAogWyJQYXltZW50IG1lc3NhZ2VzIixb"
    "WyJtc2dDaGVja2luZyIsIldoaWxlIGNoZWNraW5nIl0sWyJtc2dPayIsIlZlcmlmaWVkIl0sWyJt"
    "c2dOb25lIiwiTm90aGluZyBmb3VuZCJdLFsibXNnRXhwaXJlZCIsIkV4cGlyZWQiXV1dLAogWyJC"
    "b3R0b20gbmF2aWdhdGlvbiIsW1sibmF2U2hvcCIsIlRhYiAxIl0sWyJuYXZPcmRlcnMiLCJUYWIg"
    "MiJdLFsibmF2RWFybiIsIlRhYiAzIl0sWyJuYXZXYWxsZXQiLCJUYWIgNCJdLFsibmF2WW91Iiwi"
    "VGFiIDUiXV1dCl07Ci8qIOKVkOKVkCBGRUFUVVJFIEZMQUdTIOKAlCBldmVyeSBmZWF0dXJlIGJl"
    "bG93IGlzIG9mZi1hYmxlOyB3aGVuIG9mZiBub3RoaW5nIHJlbmRlcnMg4pWQ4pWQICovCmNvbnN0"
    "IEZHUk9VUFM9WwogWyJQYXltZW50cyAmIHdhbGxldCIsWwogIFsiY3J5cHRvIiwiQ3J5cHRvIC8g"
    "VVNEVCB0b3AtdXAiLCJBY2NlcHQgVVNEVC1UUkMyMCwgQlRDIGV0Yy4gd2l0aCBhdXRvLWNvbmZp"
    "cm0gZnJvbSB0aGUgZXhwbG9yZXIuIl0sCiAgWyJ1cGlSb3RhdGUiLCJNdWx0aXBsZSBVUEkgSURz"
    "ICsgcm90YXRpb24iLCJSb3RhdGUgaGFuZGxlcyBwZXIgcGF5bWVudCBvciBhdXRvLXN3aXRjaCB3"
    "aGVuIG9uZSBpcyBsaW1pdGVkLiJdLAogIFsiYXV0b1JlZnVuZCIsIkF1dG8tcmVmdW5kIHRvIHdh"
    "bGxldCIsIkRlYWQgYWNjb3VudCBpbnNpZGUgdGhlIHdhcnJhbnR5IHdpbmRvdyBpcyByZWZ1bmRl"
    "ZCB3aXRob3V0IHN1cHBvcnQuIl0sCiAgWyJzcGxpdFBheSIsIlNwbGl0IHBheW1lbnQiLCJQYXkg"
    "cGFydCBmcm9tIGJhbGFuY2UgYW5kIHRoZSByZXN0IGJ5IFVQSSBpbiBvbmUgZmxvdy4iXSwKICBb"
    "ImxlZGdlciIsIldhbGxldCBsZWRnZXIgc3RhdGVtZW50IiwiRnVsbCBjcmVkaXQvZGViaXQgaGlz"
    "dG9yeSB3aXRoIHJ1bm5pbmcgYmFsYW5jZSBhbmQgQ1NWIGV4cG9ydC4iXSwKICBbImRlcExpbWl0"
    "cyIsIk1pbiAvIG1heCBkZXBvc2l0IHJ1bGVzIiwiR2xvYmFsIG9yIHBlci11c2VyIGNhcHMgb24g"
    "YSBzaW5nbGUgdG9wLXVwLiJdLAogIFsiZGVwQm9udXMiLCJEZXBvc2l0IGJvbnVzIHRpZXJzIiwi"
    "QWRkIOKCuTUwMCBnZXQgNSUgZXh0cmEg4oCUIGNvbmZpZ3VyYWJsZSBzbGFicywgYXV0by1hcHBs"
    "aWVkLiJdLAogIFsid3JvbmdBbXQiLCJXcm9uZy1hbW91bnQgaGFuZGxpbmciLCJDcmVkaXQgd2hh"
    "dGV2ZXIgd2FzIGFjdHVhbGx5IHBhaWQgYW5kIGZsYWcgdGhlIG1pc21hdGNoLiJdXV0sCiBbIkNh"
    "dGFsb2cgJiBzZWxsaW5nIixbCiAgWyJjYXJ0IiwiU2hvcHBpbmcgY2FydCAvIGJ1bGsgYnV5Iiwi"
    "TWl4IGNvdW50cmllcyBpbiBvbmUgY2hlY2tvdXQsIG9uZSBjb25maXJtYXRpb24sIG9uZSBkZWJp"
    "dC4iXSwKICBbImJ1bGtEaXNjIiwiQnVsayBwdXJjaGFzZSBkaXNjb3VudCIsIkF1dG9tYXRpYyBw"
    "cmljZSBicmVhayBhdCA1IC8gMTAgLyAyNSB1bml0cy4iXSwKICBbIndpc2hsaXN0IiwiV2lzaGxp"
    "c3QgLyBmYXZvdXJpdGVzIiwiU2F2ZSBjb3VudHJpZXMgYW5kIHJlYnV5IGluIG9uZSB0YXAuIl0s"
    "CiAgWyJkeW5QcmljZSIsIkR5bmFtaWMgcHJpY2luZyIsIlByaWNlIG1vdmVzIHdpdGggc3RvY2sg"
    "bGV2ZWwsIGFnZSB0aWVyIG9yIHRpbWUgb2YgZGF5LiJdLAogIFsiZmxhc2hTYWxlIiwiRmxhc2gg"
    "c2FsZSIsIlRpbWVkIGRpc2NvdW50IHdpdGggYSBsaXZlIGNvdW50ZG93biBvbiB0aGUgc3RvcmVm"
    "cm9udC4iXSwKICBbImNvdXBvbnMiLCJDb3Vwb24gY29kZXMiLCJQZXJjZW50YWdlIG9mZiBhdCBj"
    "aGVja291dCB3aXRoIGxpbWl0cyBhbmQgZXhwaXJ5LiJdLAogIFsidmFyaWFudHMiLCJBY2NvdW50"
    "IHZhcmlhbnRzIiwiU3BhbS1ibG9jaywgMkZBLCB1c2VybmFtZSwgbWFpbCDigJQgZmlsdGVyYWJs"
    "ZSBhbmQgcHJpY2VkIGFwYXJ0LiJdLAogIFsiaGVhbHRoQ2hlY2siLCJTdG9jayBoZWFsdGggY2hl"
    "Y2siLCJTZXNzaW9uIGlzIHZhbGlkYXRlZCBsaXZlIGJlZm9yZSBpdCBpcyBzb2xkLiJdXV0sCiBb"
    "Ik9yZGVycyAmIGFmdGVyLXNhbGUiLFsKICBbInRpbWVsaW5lIiwiT3JkZXIgc3RhdHVzIHRpbWVs"
    "aW5lIiwiUGVuZGluZywgZGVsaXZlcmVkLCBPVFAgcmVjZWl2ZWQsIGNsb3NlZCDigJQgdmlzaWJs"
    "ZSB0byBib3RoIHNpZGVzLiJdLAogIFsid2FycmFudHkiLCJSZXBsYWNlbWVudCAvIHdhcnJhbnR5"
    "IiwiUmVwb3J0IGEgcHJvYmxlbSBpbnNpZGUgWCBob3VycyBmb3IgYW4gYXV0by1yZXBsYWNlbWVu"
    "dC4iXSwKICBbInJlZG93bmxvYWQiLCJSZS1kb3dubG9hZCBkZXRhaWxzIiwiRmV0Y2ggYSBwYXN0"
    "IG9yZGVyJ3MgZGV0YWlscyBhZ2FpbiwgcmF0ZS1saW1pdGVkLiJdLAogIFsiZGlzcHV0ZSIsIkRp"
    "c3B1dGUgZmxvdyIsIlJlYXNvbiBwaWNrZXIgcGx1cyBldmlkZW5jZSBpbnN0ZWFkIG9mIGZyZWUt"
    "dGV4dCB0aWNrZXRzLiJdLAogIFsic2Vzc2lvbkZpbGUiLCJTZXNzaW9uIGZpbGUgZGVsaXZlcnki"
    "LCJTZW5kIC5zZXNzaW9uIC8gSlNPTiBhcyBhIGRvY3VtZW50IHdpdGggYSBzZWxmLWRlc3RydWN0"
    "IHRpbWVyLiJdLAogIFsicmVjZWlwdHMiLCJPcmRlciByZWNlaXB0cyIsIkF1dG8tZ2VuZXJhdGVk"
    "IGludm9pY2UgcGVyIHB1cmNoYXNlLiJdXV0sCiBbIlRydXN0ICYgc29jaWFsIHByb29mIixbCiAg"
    "WyJyZXZpZXdzIiwiUmV2aWV3cyAmIHN0YXIgcmF0aW5ncyIsIkJ1eWVyIHJhdGVzIGFmdGVyIGRl"
    "bGl2ZXJ5OyBhdmVyYWdlcyBzaG93IG9uIGV2ZXJ5IGNvdW50cnkuIl0sCiAgWyJzYWxlc0ZlZWQi"
    "LCJMaXZlIHNhbGVzIGZlZWQiLCJSZWNlbnQgc2FsZXMgc2hvd24gaW4tYXBwIGFuZCBhdXRvLXBv"
    "c3RlZCB0byB5b3VyIGNoYW5uZWwuIl0sCiAgWyJsZWFkZXJib2FyZCIsIkxlYWRlcmJvYXJkIiwi"
    "VG9wIGJ1eWVycyBhbmQgcmVmZXJyZXJzIG9mIHRoZSB3ZWVrLCB3aXRoIGEgcHJpemUuIl0sCiAg"
    "WyJidXllckJhZGdlIiwiVmVyaWZpZWQgYnV5ZXIgYmFkZ2UiLCJCYWRnZSBhbmQgcGVya3MgYWZ0"
    "ZXIgTiBjb21wbGV0ZWQgb3JkZXJzLiJdXV0sCiBbIkdyb3d0aCAmIHJldGVudGlvbiIsWwogIFsi"
    "cmVmTDIiLCJNdWx0aS1sZXZlbCByZWZlcnJhbCIsIkxldmVsIDEgYW5kIGxldmVsIDIgY29tbWlz"
    "c2lvbiBpbnN0ZWFkIG9mIGEgZmxhdCByYXRlLiJdLAogIFsicmVmRGFzaCIsIlJlZmVycmFsIGRh"
    "c2hib2FyZCIsIldobyBqb2luZWQsIHdobyBkZXBvc2l0ZWQgYW5kIHdoYXQgZWFjaCBpbnZpdGUg"
    "ZWFybmVkLiJdLAogIFsic3RyZWFrIiwiQ2hlY2staW4gc3RyZWFrIHJld2FyZHMiLCJFc2NhbGF0"
    "aW5nIGRhaWx5IHJld2FyZCB0aGF0IHJlc2V0cyB3aGVuIGJyb2tlbi4iXSwKICBbImxveWFsdHki"
    "LCJMb3lhbHR5IHBvaW50cyIsIkVhcm4gcGVyIOKCuSBzcGVudCwgcmVkZWVtIGZvciBiYWxhbmNl"
    "IG9yIGEgZnJlZSBhY2NvdW50LiJdLAogIFsid2luYmFjayIsIlJlLWVuZ2FnZW1lbnQgRE1zIiwi"
    "QXV0byBtZXNzYWdlIHVzZXJzIGluYWN0aXZlIGZvciA3IC8gMzAgZGF5cy4iXSwKICBbImFiYW5k"
    "b25SZW1pbmQiLCJBYmFuZG9uZWQtZGVwb3NpdCByZW1pbmRlciIsIk51ZGdlIGJlZm9yZSBhbiB1"
    "bmZpbmlzaGVkIHBheW1lbnQgZXhwaXJlcy4iXSwKICBbInNjaGVkQnJvYWRjYXN0IiwiU2NoZWR1"
    "bGVkICYgc2VnbWVudGVkIGJyb2FkY2FzdHMiLCJRdWV1ZSBmb3IgbGF0ZXIsIHRhcmdldCBidXll"
    "cnMsIG5vbi1idXllcnMgb3IgYmlnIHNwZW5kZXJzLiJdXV0sCiBbIkFkbWluICYgb3BlcmF0aW9u"
    "cyIsWwogIFsiYWRtaW5Sb2xlcyIsIkFkbWluIHJvbGVzIiwiT3duZXIgLyBtYW5hZ2VyIC8gc3Vw"
    "cG9ydCB3aXRoIHNlcGFyYXRlIHBlcm1pc3Npb25zLiJdLAogIFsiYXVkaXRMb2ciLCJBdWRpdCBs"
    "b2ciLCJFdmVyeSBhZG1pbiBhY3Rpb24gbG9nZ2VkIHdpdGggdGltZXN0YW1wIGFuZCB1bmRvLiJd"
    "LAogIFsiY29udGVudEVkaXRvciIsIkluLWJvdCBjb250ZW50IGVkaXRvciIsIkVkaXQgZXZlcnkg"
    "bWVzc2FnZSBhbmQgYnV0dG9uIHdpdGhvdXQgdG91Y2hpbmcgY29kZS4iXSwKICBbImF1dG9CYWNr"
    "dXAiLCJTY2hlZHVsZWQgYXV0by1iYWNrdXAiLCJEYXRhYmFzZSB0byBhIHByaXZhdGUgY2hhbm5l"
    "bCBldmVyeSBOIGhvdXJzLCBvbmUtdGFwIHJlc3RvcmUuIl0sCiAgWyJidWxrVXBsb2FkIiwiQnVs"
    "ayB1cGxvYWQgd2l0aCB2YWxpZGF0aW9uIiwiVmFsaWRhdGUgZWFjaCBzZXNzaW9uLCByZXBvcnQg"
    "ZGVhZCBvciBkdXBsaWNhdGUsIGltcG9ydCB0aGUgcmVzdC4iXSwKICBbImR1cERldGVjdCIsIkR1"
    "cGxpY2F0ZSBkZXRlY3Rpb24iLCJSZWplY3QgYSBwaG9uZSBvciBzZXNzaW9uIGFscmVhZHkgaW4g"
    "dGhlIGRhdGFiYXNlLiJdLAogIFsibG93U3RvY2siLCJMb3ctc3RvY2sgYWxlcnRzIiwiUGluZyB3"
    "aGVuIGEgY291bnRyeSBkcm9wcyBiZWxvdyBpdHMgb3duIHRocmVzaG9sZC4iXSwKICBbInNlbGxI"
    "b3VycyIsIlNlbGxpbmctaG91cnMgc2NoZWR1bGUiLCJBdXRvLWhpZGUgY291bnRyaWVzIG91dHNp"
    "ZGUgdGhlaXIgc2VsbGluZyB3aW5kb3cuIl0sCiAgWyJzYW5kYm94IiwiVGVzdCBtb2RlIC8gc2Fu"
    "ZGJveCIsIlJ1biB0aGUgd2hvbGUgZmxvdyBvbiBmYWtlIG1vbmV5IGJlZm9yZSBnb2luZyBsaXZl"
    "LiJdLAogIFsiaTE4biIsIk11bHRpLWxhbmd1YWdlIiwiUGVyLXVzZXIgSGluZGkgLyBFbmdsaXNo"
    "IHN3aXRjaCBhY3Jvc3MgZXZlcnkgc3RyaW5nLiJdXV0sCiBbIlNlY3VyaXR5ICYgYW50aS1hYnVz"
    "ZSIsWwogIFsicmF0ZUxpbWl0IiwiUmF0ZSBsaW1pdGluZyIsIlBlci11c2VyIGFjdGlvbiBsaW1p"
    "dHMgc28gbm9ib2R5IGNhbiBoYW1tZXIgdGhlIGJvdC4iXSwKICBbIm11bHRpQWNjIiwiTXVsdGkt"
    "YWNjb3VudCBkZXRlY3Rpb24iLCJDYXRjaCByZWZlcnJhbCBmYXJtaW5nIGFuZCBmbGFnIHN1c3Bp"
    "Y2lvdXMgc2lnbnVwcy4iXSwKICBbInZlbG9jaXR5IiwiUHVyY2hhc2UgdmVsb2NpdHkgYWxlcnRz"
    "IiwiRmxhZyBhYm5vcm1hbGx5IGZhc3QgYnV5aW5nIOKAlCB0eXBpY2FsIG9mIHN0b2xlbiB0b3At"
    "dXBzLiJdLAogIFsiYWRtaW4yZmEiLCJBZG1pbiAyRkEiLCJQSU4gb3IgVE9UUCBiZWZvcmUgYmFs"
    "YW5jZSBlZGl0cywgYnJvYWRjYXN0cyBvciB3aXBlcy4iXSwKICBbImF1dG9CYW4iLCJBdXRvLWJh"
    "biBydWxlcyIsIkJhbiBvbiBjaGFyZ2ViYWNrLCBmYWtlIFVUUiBvciByZXBlYXRlZCBmYWlsZWQg"
    "dmVyaWZpY2F0aW9uLiJdLAogIFsiYmxhY2tsaXN0IiwiQmxhY2tsaXN0IiwiUGVybWFuZW50bHkg"
    "YmxvY2sgdXNlciBJRHMsIHBob25lIG51bWJlcnMgYW5kIFVQSSByZWZlcmVuY2VzLiJdLAogIFsi"
    "ZW5jcnlwdCIsIlNlc3Npb24gZW5jcnlwdGlvbiBhdCByZXN0IiwiQSBsZWFrZWQgZGF0YWJhc2Ug"
    "ZmlsZSBpcyB1c2VsZXNzIHdpdGhvdXQgdGhlIGtleS4iXV1dLAogWyJBbmFseXRpY3MiLFsKICBb"
    "InJldkNoYXJ0IiwiUmV2ZW51ZSBkYXNoYm9hcmQiLCJEYWlseSwgd2Vla2x5IGFuZCBtb250aGx5"
    "IHJldmVudWUgYW5kIHByb2ZpdCBhcyBjaGFydHMuIl0sCiAgWyJmdW5uZWwiLCJDb252ZXJzaW9u"
    "IGZ1bm5lbCIsIlNlZSB3aGVyZSB1c2VycyBkcm9wIG9mZiBiZXR3ZWVuIGpvaW5pbmcgYW5kIGJ1"
    "eWluZy4iXSwKICBbInByb2ZpdFJlcCIsIlBlci1jb3VudHJ5IHByb2ZpdCIsIkNvc3QgdnMgc2Fs"
    "ZSBwcmljZSB2cyB2b2x1bWUgZm9yIGV2ZXJ5IGNvdW50cnkuIl0sCiAgWyJjbHYiLCJDdXN0b21l"
    "ciBsaWZldGltZSB2YWx1ZSIsIlNwZW5kLCBmcmVxdWVuY3kgYW5kIGNodXJuIHJpc2sgcGVyIHVz"
    "ZXIuIl0sCiAgWyJkaWdlc3QiLCJEYWlseSBkaWdlc3QiLCJFbmQtb2YtZGF5IHN1bW1hcnkgcHVz"
    "aGVkIHRvIHRoZSBhZG1pbiBjaGF0LiJdXV0sCiBbIlF1YWxpdHkgb2YgbGlmZSIsWwogIFsiaW5s"
    "aW5lU2VhcmNoIiwiSW5saW5lIHNlYXJjaCIsIlR5cGUgQHlvdXJib3QgaW5kaWEgaW4gYW55IGNo"
    "YXQgdG8gZmluZCBhbmQgYnV5LiJdLAogIFsic2xhc2hDbWRzIiwiU2xhc2ggY29tbWFuZHMgbWVu"
    "dSIsIi9zdGFydCAvYnV5IC9iYWxhbmNlIC9vcmRlcnMgcmVnaXN0ZXJlZCB3aXRoIFRlbGVncmFt"
    "LiJdLAogIFsic3RvY2tDb3VudGVyIiwiTGl2ZSBzdG9jayBjb3VudGVyIiwiQXV0by11cGRhdGlu"
    "ZyBwaW5uZWQgcG9zdCBhbmQgaW4tYXBwIGxpdmUgY291bnQuIl0sCiAgWyJvbmJvYXJkaW5nIiwi"
    "T25ib2FyZGluZyB0b3VyIiwiVGhyZWUtc3RlcCB3YWxrdGhyb3VnaCBmb3IgZmlyc3QtdGltZSB1"
    "c2Vycy4iXSwKICBbIm5vdGlmUHJlZnMiLCJOb3RpZmljYXRpb24gcHJlZmVyZW5jZXMiLCJVc2Vy"
    "cyBwaWNrIHdoaWNoIGFsZXJ0cyB0aGV5IHJlY2VpdmUuIl0sCiAgWyJwdXNoTm90aWYiLCJNaW5p"
    "IGFwcCBwdXNoIiwiTWluaSBhcHAgYWN0aW9ucyBub3RpZnkgdGhlIHVzZXIgdGhyb3VnaCB0aGUg"
    "Ym90LiJdXV0KXTsKY29uc3QgRkVBVD17fTsKRkdST1VQUy5mb3JFYWNoKChbZyxyb3dzXSk9PnJv"
    "d3MuZm9yRWFjaChyPT5GRUFUW3JbMF1dPXRydWUpKTsKY29uc3QgRkVBVDA9T2JqZWN0LmFzc2ln"
    "bih7fSxGRUFUKTsKY29uc3QgRz0oayxoKT0+RkVBVFtrXT9oOiIiOwpjb25zdCBhbnlGPSguLi5r"
    "cyk9PmtzLnNvbWUoaz0+RkVBVFtrXSk7CmxldCBDQVJUPVtdLFdJU0g9W107CmxldCBSRVZJRVdT"
    "PXtJbmRpYTpbNC44LDQxMl0sSW5kb25lc2lhOls0LjYsMTgwXSxCcmF6aWw6WzQuNyw5Nl0sTmln"
    "ZXJpYTpbNC40LDU0XSxWaWV0bmFtOls0LjksMjMzXSxQaGlsaXBwaW5lczpbNC41LDcxXX07Cmxl"
    "dCBGRUVEPVtbIvCfh67wn4ezIiwiSW5kaWEiLCLigrkzNSIsIjIgbWluIGFnbyJdLFsi8J+Hu/Cf"
    "h7MiLCJWaWV0bmFtIiwi4oK5MjYiLCI2IG1pbiBhZ28iXSxbIvCfh6fwn4e3IiwiQnJhemlsIiwi"
    "4oK5NDQiLCIxMSBtaW4gYWdvIl1dOwpsZXQgQURNPXsKICBrcGk6e3Jldjo0ODIwLG9yZGVyczo2"
    "MSx1c2VyczoxMjg0LGF1dG86Mzh9LAogIGJhcnM6WzQyLDYxLDM4LDc0LDU1LDg4LDY3XSwKICBz"
    "dG9jazpbXSxvcmRlcnM6W10sZGlzcHV0ZXM6W10sdXNlcnM6W10sYWRtaW5zOltdLGNoYW5uZWxz"
    "OltdLAogIGxvZ3M6e21hc3Rlcjp0cnVlLGNoYW5uZWxzOltdLGV2ZW50czpbXX0sY291cG9uczpb"
    "XSxhdWRpdDpbXSxyZXZpZXdzOltdLGRlcG9zaXRzOltdLGxvZ2ZlZWQ6W10sCiAgcGF5Ont1cGk6"
    "IiIscGF5ZWU6IiIsZ21haWw6IiIsZ21haWxfc2V0OmZhbHNlLHdpbmRvdzo1LHByZWZpeDoiUEFZ"
    "IixtaW46MTAsbWF4OjIwMDAwLGF1dG9kZXA6MCxwZW5kaW5nOjB9LAogIHNlY3VyaXR5OntyYXRl"
    "OjIwLHZlbG9jaXR5OjUsYmxhY2tsaXN0OjAsYmFubmVkOjB9LAogIG1hcmtldDp7Zmxhc2g6MjAs"
    "Zmxhc2hfb246ZmFsc2UscmVmMTo1LHJlZjI6MSxnb2FsOjAsbW9udGg6MH0sCiAgc2V0dGluZ3M6"
    "e3N0b3JlOiIiLHRhZ2xpbmU6IiIsc3VwcG9ydDoiIixwcmljZTozNSxkYWlseTowLjQsYXBwOiIi"
    "LG1haW50ZW5hbmNlOmZhbHNlLHNhbmRib3g6ZmFsc2V9LAogIGFuYWx5dGljczp7ZnVubmVsOnt1"
    "c2VyczowLGRlcDowLGJ1eTowfSxjb3VudHJ5OltdLHN0b2NrX3RvdGFsOjAsc29sZF90b3RhbDow"
    "fQp9OwpmdW5jdGlvbiBhZG1OdW0obil7cmV0dXJuIChufHwwKS50b0xvY2FsZVN0cmluZygiZW4t"
    "SU4iKTt9CmxldCBFQVJOPXtzdHJlYWs6NCxkYWlseTowLjQwLGNsYWltZWQ6ZmFsc2UscG9pbnRz"
    "OjM0MCxwb2ludHNXb3J0aDozNCxpbnZpdGVzOjEyLAogIGwxOjUyLjQwLGwyOjEyLjQwLGVhcm5l"
    "ZDo2NC44MCxsaW5rOiJ0Lm1lL3lvdXJfYm90P3N0YXJ0PXJlZl9kZW1vIiwKICByZWNlbnQ6W1si"
    "dXNlcl8xMDQxIiwiZGVwb3NpdGVkIOKCuTIwMCIsIivigrkyLjAwIl0sWyJ1c2VyXzEwMzgiLCJq"
    "b2luZWQiLCLigJQiXSxbInVzZXJfMTAyMiIsImRlcG9zaXRlZCDigrk1MDAiLCIr4oK5NS4wMCJd"
    "XSwKICBsZWFkZXJzOltbIjEiLCJAcmFodWwiLCLigrk0LDgyMCJdLFsiMiIsIkBhbWFuIiwi4oK5"
    "MywxNDAiXSxbIjMiLCJAeW91Iiwi4oK5MSw5ODAiXV0sCiAgdGFza3M6W1siSm9pbiB0aGUgbWFp"
    "biBjaGFubmVsIiwi4oK5MS4wMCIsMV0sWyJQbGFjZSB5b3VyIGZpcnN0IG9yZGVyIiwi4oK5Mi4w"
    "MCIsMV0sWyJJbnZpdGUgMyBmcmllbmRzIiwi4oK5NS4wMCIsMF1dfTsKZnVuY3Rpb24gc3RhcnMo"
    "Yyl7Y29uc3Qgcj0oUkVWSUVXU1tjXXx8WzQuNywyMF0pWzBdLG49KFJFVklFV1NbY118fFs0Ljcs"
    "MjBdKVsxXTsKICByZXR1cm4gYDxzcGFuIGNsYXNzPSJzdGFycyI+JHtbMSwyLDMsNCw1XS5tYXAo"
    "aT0+aWMoaTw9TWF0aC5yb3VuZChyKT8ic3RhciI6InN0YXIiKSkuam9pbigiIil9PGI+JHtyfSAo"
    "JHtufSk8L2I+PC9zcGFuPmA7fQpmdW5jdGlvbiBhcHBseUZlYXR1cmVzKCl7CiAgY29uc3QgY2M9"
    "ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNhcnRjaGlwIik7CiAgaWYoY2Mpe2NjLmNsYXNzTGlz"
    "dC50b2dnbGUoImhpZGRlbiIsIUZFQVQuY2FydCk7ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNh"
    "cnRuIikudGV4dENvbnRlbnQ9Q0FSVC5sZW5ndGg7fQogIGNvbnN0IGVhcm49YW55Rigic3RyZWFr"
    "IiwicmVmRGFzaCIsInJlZkwyIiwibG95YWx0eSIsImxlYWRlcmJvYXJkIiwid2luYmFjayIpOwog"
    "IGNvbnN0IGViPWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoJyN0YWJzIGJ1dHRvbltkYXRhLXRhYj0i"
    "ZWFybiJdJyk7CiAgaWYoZWIpe2ViLmNsYXNzTGlzdC50b2dnbGUoImhpZGRlbiIsIWVhcm4pOwog"
    "ICAgaWYoIWVhcm4mJlRBQj09PSJlYXJuIil7VEFCPSJzaG9wIjtkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCIjdGFicyBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnRvZ2dsZSgib24i"
    "LHguZGF0YXNldC50YWI9PT0ic2hvcCIpKTtyZW5kZXIoKTt9fQogIGNvbnN0IGFtYXA9e21hcmtl"
    "dDphbnlGKCJjb3Vwb25zIiwiZmxhc2hTYWxlIiwic2NoZWRCcm9hZGNhc3QiLCJ3aW5iYWNrIiwi"
    "c2FsZXNGZWVkIiwibGVhZGVyYm9hcmQiLCJsb3lhbHR5IiwicmVmRGFzaCIsInJlZkwyIiwiYWJh"
    "bmRvblJlbWluZCIpLAogICAgYW5hbHl0aWNzOmFueUYoInJldkNoYXJ0IiwiZnVubmVsIiwicHJv"
    "Zml0UmVwIiwiY2x2IiwiZGlnZXN0IiksCiAgICBzZWN1cml0eTphbnlGKCJyYXRlTGltaXQiLCJt"
    "dWx0aUFjYyIsInZlbG9jaXR5IiwiYWRtaW4yZmEiLCJhdXRvQmFuIiwiYmxhY2tsaXN0IiwiZW5j"
    "cnlwdCIpLAogICAgY29udGVudDpGRUFULmNvbnRlbnRFZGl0b3J9OwogIE9iamVjdC5lbnRyaWVz"
    "KGFtYXApLmZvckVhY2goKFtrLHZdKT0+e2NvbnN0IGI9ZG9jdW1lbnQucXVlcnlTZWxlY3Rvcign"
    "I2FzdWIgYnV0dG9uW2RhdGEtYT0iJytrKyciXScpOwogICAgaWYoYil7Yi5jbGFzc0xpc3QudG9n"
    "Z2xlKCJoaWRkZW4iLCF2KTsKICAgICAgaWYoIXYmJkFUQUI9PT1rKXtBVEFCPSJkYXNoIjtkb2N1"
    "bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjYXN1YiBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NM"
    "aXN0LnRvZ2dsZSgib24iLHguZGF0YXNldC5hPT09ImRhc2giKSk7cmVuZGVyQWRtaW4oKTt9fX0p"
    "OwogIGlmKHdpbmRvdy5fX2RvY2tQbGFjZSlyZXF1ZXN0QW5pbWF0aW9uRnJhbWUoKCk9PndpbmRv"
    "dy5fX2RvY2tQbGFjZSh0cnVlKSk7CiAgaWYod2luZG93Ll9fdnRhYlBsYWNlKXJlcXVlc3RBbmlt"
    "YXRpb25GcmFtZSgoKT0+d2luZG93Ll9fdnRhYlBsYWNlKHRydWUpKTsKfQpmdW5jdGlvbiBhcHBs"
    "eUNNUygpewogY29uc3QgZz1pZD0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoaWQpOwogaWYoZygi"
    "Ym5hbWUiKSlnKCJibmFtZSIpLnRleHRDb250ZW50PUNNUy5zdG9yZU5hbWU7CiBpZihnKCJhYm5h"
    "bWUiKSlnKCJhYm5hbWUiKS50ZXh0Q29udGVudD1DTVMuc3RvcmVOYW1lOwogaWYoQ01TLnN0b3Jl"
    "TmFtZSl7Q0ZHLm5hbWU9Q01TLnN0b3JlTmFtZTt9CiBpZihDTVMuc3RvcmVUYWcpQ0ZHLnRhZz1D"
    "TVMuc3RvcmVUYWc7CiBwYWludEJyYW5kKENNUy5zdG9yZU5hbWUsQ01TLnN0b3JlVGFnKTsKIHRy"
    "eXsKICAgaWYoQ01TLnN0b3JlTmFtZSlsb2NhbFN0b3JhZ2Uuc2V0SXRlbSgic3RvcmVOYW1lIixD"
    "TVMuc3RvcmVOYW1lKTsKICAgaWYoQ01TLnN0b3JlVGFnKWxvY2FsU3RvcmFnZS5zZXRJdGVtKCJz"
    "dG9yZVRhZyIsQ01TLnN0b3JlVGFnKTsKIH1jYXRjaChlKXt9CiBpZihnKCJidGFnIikpZygiYnRh"
    "ZyIpLnRleHRDb250ZW50PUNNUy5zdG9yZVRhZzsKIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IltkYXRhLW5hdl0iKS5mb3JFYWNoKG49PnsKICAgY29uc3Qgaz0ibmF2IituLmRhdGFzZXQubmF2"
    "WzBdLnRvVXBwZXJDYXNlKCkrbi5kYXRhc2V0Lm5hdi5zbGljZSgxKTsKICAgaWYoQ01TW2tdKW4u"
    "dGV4dENvbnRlbnQ9Q01TW2tdO30pOwogaWYod2luZG93Ll9fZG9ja1BsYWNlKXJlcXVlc3RBbmlt"
    "YXRpb25GcmFtZSgoKT0+d2luZG93Ll9fZG9ja1BsYWNlKHRydWUpKTsKfQpsZXQgQkFMPTI0OC41"
    "MDsKY29uc3QgU1RPQ0s9Wwoge2M6IkluZGlhIixmOiLwn4eu8J+HsyIsY29kZToiKzkxIixwcmlj"
    "ZTozNSx3YXM6NDkscXR5OjEyOCxob3Q6MX0sCiB7YzoiSW5kb25lc2lhIixmOiLwn4eu8J+HqSIs"
    "Y29kZToiKzYyIixwcmljZToyOCx3YXM6MCxxdHk6NjR9LAoge2M6IkJyYXppbCIsZjoi8J+Hp/Cf"
    "h7ciLGNvZGU6Iis1NSIscHJpY2U6NDQsd2FzOjU5LHF0eToyMn0sCiB7YzoiTmlnZXJpYSIsZjoi"
    "8J+Hs/Cfh6wiLGNvZGU6IisyMzQiLHByaWNlOjMxLHdhczowLHF0eTowfSwKIHtjOiJWaWV0bmFt"
    "IixmOiLwn4e78J+HsyIsY29kZToiKzg0IixwcmljZToyNix3YXM6MCxxdHk6OTF9LAoge2M6IlBo"
    "aWxpcHBpbmVzIixmOiLwn4e18J+HrSIsY29kZToiKzYzIixwcmljZTozOCx3YXM6MCxxdHk6N30K"
    "XTsKY29uc3QgT1JERVJTPVsKIHtpZDoiT1JELTc3NDEiLGM6IkluZGlhIixmOiLwn4eu8J+HsyIs"
    "bnVtOiIrOTEg4oCi4oCi4oCi4oCiIOKAouKAojMyIixzdDoiRGVsaXZlcmVkIix3aGVuOiIyIG1p"
    "biBhZ28iLHByaWNlOjM1fSwKIHtpZDoiT1JELTc3MjgiLGM6IlZpZXRuYW0iLGY6IvCfh7vwn4ez"
    "IixudW06Iis4NCDigKLigKLigKLigKIg4oCi4oCiMDciLHN0OiJEZWxpdmVyZWQiLHdoZW46Illl"
    "c3RlcmRheSIscHJpY2U6MjZ9LAoge2lkOiJPUkQtNzcwMiIsYzoiQnJhemlsIixmOiLwn4en8J+H"
    "tyIsbnVtOiIrNTUg4oCi4oCi4oCi4oCiIOKAouKAojg4IixzdDoiUmVmdW5kZWQiLHdoZW46IjMg"
    "ZGF5cyBhZ28iLHByaWNlOjQ0fQpdOwpsZXQgQ0hBTk5FTFM9Wwoge2lkOjEsbmFtZToiTWFpbiBD"
    "aGFubmVsIixoYW5kbGU6IkB5b3VyX21haW5fY2hhbm5lbCIscmVxOnRydWUsb246dHJ1ZSxtZW1i"
    "ZXJzOiIxMi40SyJ9LAoge2lkOjIsbmFtZToiQmFja3VwIENoYW5uZWwiLGhhbmRsZToiQHlvdXJf"
    "YmFja3VwIixyZXE6dHJ1ZSxvbjp0cnVlLG1lbWJlcnM6IjMuMUsifSwKIHtpZDozLG5hbWU6IkRp"
    "c2N1c3Npb24gR3JvdXAiLGhhbmRsZToiQHlvdXJfZ3JvdXAiLHJlcTpmYWxzZSxvbjp0cnVlLG1l"
    "bWJlcnM6IjUuOEsifQpdOwpsZXQgTE9HQ0g9Wwoge2lkOjEsbmFtZToiTWFpbiBMb2dzIixoYW5k"
    "bGU6IkB5b3VyX2xvZ3MiLHByaW1hcnk6dHJ1ZSxvbjp0cnVlfSwKIHtpZDoyLG5hbWU6IlBheW1l"
    "bnQgTG9ncyIsaGFuZGxlOiJAeW91cl9wYXlfbG9ncyIscHJpbWFyeTpmYWxzZSxvbjp0cnVlfSwK"
    "IHtpZDozLG5hbWU6IkVycm9yIExvZ3MiLGhhbmRsZToiLTEwMHh4eHh4eHh4eHgiLHByaW1hcnk6"
    "ZmFsc2Usb246ZmFsc2V9Cl07CmxldCBMT0dTX01BU1RFUj10cnVlOwpsZXQgRVZFTlRTPVsKIHtr"
    "OiJOZXcgdXNlciBqb2luZWQiLG9uOmZhbHNlLGNoOjF9LHtrOiJEZXBvc2l0IC8gcGF5bWVudCIs"
    "b246dHJ1ZSxjaDoyfSwKIHtrOiJQdXJjaGFzZSIsb246dHJ1ZSxjaDoxfSx7azoiT1RQIHJlcXVl"
    "c3RlZCIsb246ZmFsc2UsY2g6MX0sCiB7azoiU3RvY2sgYWRkZWQiLG9uOnRydWUsY2g6MX0se2s6"
    "IkFkbWluIGFjdGlvbiIsb246dHJ1ZSxjaDoxfSwKIHtrOiJFcnJvcnMgJiB3YXJuaW5ncyIsb246"
    "dHJ1ZSxjaDozfQpdOwpjb25zdCBUWD1be3Q6IkRlcG9zaXQiLGE6IivigrkyMDAuMDAiLHc6IlRv"
    "ZGF5LCAxNDowMiJ9LHt0OiJQdXJjaGFzZSDCtyBJbmRpYSIsYToiLeKCuTM1LjAwIix3OiJUb2Rh"
    "eSwgMTQ6MDUifSwKIHt0OiJEYWlseSBjaGVjay1pbiIsYToiK+KCuTAuNDAiLHc6IlRvZGF5LCAw"
    "OToxMSJ9LHt0OiJSZWZlcnJhbCBib251cyIsYToiK+KCuTMuMjAiLHc6Illlc3RlcmRheSJ9XTsK"
    "CmxldCBGSUxURVJFRD1TVE9DSy5zbGljZSgpLCBRVUVSWT0iIiwgQ0hJUD0iYWxsIjsKY29uc3Qg"
    "RU1QVFk9JzxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJ0ZXh0LWFsaWduOmNlbnRlciI+PHAgY2xh"
    "c3M9InN1YiI+Tm90aGluZyBtYXRjaGVzIHRoYXQgc2VhcmNoLjwvcD48L2Rpdj4nOwpjb25zdCBJ"
    "Qz17YmFnOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1c"
    "ImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5k"
    "XCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwiTTYgMiAzIDZ2MTRhMiAyIDAg"
    "MCAwIDIgMmgxNGEyIDIgMCAwIDAgMi0yVjZsLTMtNHpcIi8+PHBhdGggZD1cIk0zIDZoMThcIi8+"
    "PHBhdGggZD1cIk0xNiAxMGE0IDQgMCAwIDEtOCAwXCIvPjwvc3ZnPiIsYm94OiI8c3ZnIHZpZXdC"
    "b3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0"
    "cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2lu"
    "PVwicm91bmRcIj48cGF0aCBkPVwiTTIxIDhhMiAyIDAgMCAwLTEtMS43M2wtNy00YTIgMiAwIDAg"
    "MC0yIDBsLTcgNEEyIDIgMCAwIDAgMyA4djhhMiAyIDAgMCAwIDEgMS43M2w3IDRhMiAyIDAgMCAw"
    "IDIgMGw3LTRBMiAyIDAgMCAwIDIxIDE2WlwiLz48cGF0aCBkPVwibTMuMyA3IDguNyA1IDguNy01"
    "XCIvPjxwYXRoIGQ9XCJNMTIgMjJWMTJcIi8+PC9zdmc+Iix6YXA6Ijxzdmcgdmlld0JveD1cIjAg"
    "MCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdp"
    "ZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3Vu"
    "ZFwiPjxwYXRoIGQ9XCJNMTMgMiAzIDE0aDlsLTEgOCAxMC0xMmgtOXpcIi8+PC9zdmc+Iix3YWxs"
    "ZXQ6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3Vy"
    "cmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBz"
    "dHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTkgN1Y1YTIgMiAwIDAgMC0yLTJI"
    "NWEyIDIgMCAwIDAgMCA0aDE1YTIgMiAwIDAgMSAyIDJ2OWEyIDIgMCAwIDEtMiAySDVhMiAyIDAg"
    "MCAxLTItMlY1XCIvPjxwYXRoIGQ9XCJNMTggMTJoLjAxXCIvPjwvc3ZnPiIsdXNlcjoiPHN2ZyB2"
    "aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3Jc"
    "IiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5l"
    "am9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0xOSAyMXYtMmE0IDQgMCAwIDAtNC00SDlhNCA0IDAg"
    "MCAwLTQgNHYyXCIvPjxjaXJjbGUgY3g9XCIxMlwiIGN5PVwiN1wiIHI9XCI0XCIvPjwvc3ZnPiIs"
    "c2hpZWxkOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1c"
    "ImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5k"
    "XCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwiTTIwIDEzYzAgNS0zLjUgNy41"
    "LTcuNjYgOC45NWExIDEgMCAwIDEtLjY3LS4wMUM3LjUgMjAuNSA0IDE4IDQgMTNWNmExIDEgMCAw"
    "IDEgMS0xYzIgMCA0LjUtMS4yIDYuMjQtMi43MmExLjE3IDEuMTcgMCAwIDEgMS41MiAwQzE0LjUx"
    "IDMuODEgMTcgNSAxOSA1YTEgMSAwIDAgMSAxIDF6XCIvPjwvc3ZnPiIsY2hlY2s6Ijxzdmcgdmll"
    "d0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIg"
    "c3Ryb2tlLXdpZHRoPVwiMi40XCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5l"
    "am9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0yMCA2IDkgMTdsLTUtNVwiLz48L3N2Zz4iLHNlbmQ6"
    "Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVu"
    "dENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJv"
    "a2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJtMjIgMi03IDIwLTQtOS05LTRaXCIvPjxw"
    "YXRoIGQ9XCJNMjIgMiAxMSAxM1wiLz48L3N2Zz4iLHBsdXM6Ijxzdmcgdmlld0JveD1cIjAgMCAy"
    "NCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRo"
    "PVwiMi40XCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTIgNXYxNE01IDEy"
    "aDE0XCIvPjwvc3ZnPiIsY29weToiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5v"
    "bmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxp"
    "bmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHJlY3Qgd2lkdGg9XCIx"
    "NFwiIGhlaWdodD1cIjE0XCIgeD1cIjhcIiB5PVwiOFwiIHJ4PVwiMlwiLz48cGF0aCBkPVwiTTQg"
    "MTZjLTEuMSAwLTItLjktMi0yVjRjMC0xLjEuOS0yIDItMmgxMGMxLjEgMCAyIC45IDIgMlwiLz48"
    "L3N2Zz4iLGNoZXY6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIHdpZHRoPVwiMTZcIiBoZWln"
    "aHQ9XCIxNlwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdp"
    "ZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3Vu"
    "ZFwiPjxwYXRoIGQ9XCJtOSAxOCA2LTYtNi02XCIvPjwvc3ZnPiIsZmlyZToiPHN2ZyB2aWV3Qm94"
    "PVwiMCAwIDI0IDI0XCIgd2lkdGg9XCIxMlwiIGhlaWdodD1cIjEyXCIgZmlsbD1cIm5vbmVcIiBz"
    "dHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9"
    "XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0xMiAyYzEgNCA1"
    "IDUgNSA5YTUgNSAwIDAgMS0xMCAwYzAtMiAxLTMgMi00IDAgMiAxIDMgMiAzIDAtMy0xLTUgMS04"
    "WlwiLz48L3N2Zz4iLGRvd246Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25l"
    "XCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5l"
    "Y2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTIgNXYx"
    "NE0xOSAxMmwtNyA3LTctN1wiLz48L3N2Zz4iLHVwOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRc"
    "IiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJc"
    "IiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0"
    "aCBkPVwiTTEyIDE5VjVNNSAxMmw3LTcgNyA3XCIvPjwvc3ZnPiIsc2VhcmNoOiI8c3ZnIHZpZXdC"
    "b3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0"
    "cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2lu"
    "PVwicm91bmRcIj48Y2lyY2xlIGN4PVwiMTFcIiBjeT1cIjExXCIgcj1cIjdcIi8+PHBhdGggZD1c"
    "Im0yMSAyMS00LjMtNC4zXCIvPjwvc3ZnPiIsc3RhcjoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0"
    "XCIgZmlsbD1cImN1cnJlbnRDb2xvclwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13"
    "aWR0aD1cIjEuNVwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIm0xMiAzIDIu"
    "NiA1LjMgNS45LjktNC4zIDQuMSAxIDUuOC01LjItMi43LTUuMiAyLjcgMS01LjhMMy41IDkuMmw1"
    "LjktLjl6XCIvPjwvc3ZnPiJ9Owpjb25zdCBpYz1uPT5JQ1tuXXx8IiI7CmNvbnN0IGVzYz1zPT5T"
    "dHJpbmcocykucmVwbGFjZSgvWyY8PiJdL2csYz0+KHsiJiI6IiZhbXA7IiwiPCI6IiZsdDsiLCI+"
    "IjoiJmd0OyIsJyInOiImcXVvdDsifVtjXSkpOwpjb25zdCBtb25leT1uPT5DRkcuY3VyK051bWJl"
    "cihuKS50b0ZpeGVkKDIpOwoKbGV0IHR0O2NvbnN0IHRvYXN0PW09Pntjb25zdCBlPWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJ0b2FzdCIpO2UudGV4dENvbnRlbnQ9bTtlLmNsYXNzTGlzdC5hZGQo"
    "InNob3ciKTtjbGVhclRpbWVvdXQodHQpO3R0PXNldFRpbWVvdXQoKCk9PmUuY2xhc3NMaXN0LnJl"
    "bW92ZSgic2hvdyIpLDE5MDApfTsKZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigiY2xpY2siLGU9"
    "Pntjb25zdCBiPWUudGFyZ2V0LmNsb3Nlc3QoIltkYXRhLXRvYXN0XSIpO2lmKGIpdG9hc3QoYi5k"
    "YXRhc2V0LnRvYXN0KX0pOwpjb25zdCBzaGVldD1oPT57CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5"
    "SWQoInBhbmVsIikuaW5uZXJIVE1MPSc8ZGl2IGNsYXNzPSJncmFiIj48L2Rpdj4nK2g7CiAgZG9j"
    "dW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNoZWV0IikuY2xhc3NMaXN0LmFkZCgib3BlbiIpOwogIC8q"
    "IGNvbnRyb2xzIGluc2lkZSBhIHNoZWV0IGFyZSBjcmVhdGVkIGFmdGVyIGJpbmRpbmcsIHNvIGJp"
    "bmQgdGhlbSBub3cgKi8KICB0cnl7IGlmKHR5cGVvZiBiaW5kQ29tbW9uPT09ImZ1bmN0aW9uIikg"
    "YmluZENvbW1vbigpOyB9Y2F0Y2goZSl7fQogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNz"
    "aGVldCBbZGF0YS1jbG9zZV0iKS5mb3JFYWNoKHg9Pngub25jbGljaz1jbG9zZVNoZWV0KTsKfTsK"
    "Y29uc3QgY2xvc2VTaGVldD0oKT0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNoZWV0IikuY2xh"
    "c3NMaXN0LnJlbW92ZSgib3BlbiIpOwpkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCJjbGljayIs"
    "ZT0+e2lmKGUudGFyZ2V0LmNsb3Nlc3QoIltkYXRhLWNsb3NlXSIpKWNsb3NlU2hlZXQoKX0pOwoK"
    "ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3N3IGJ1dHRvbiIpLmZvckVhY2goYj0+Yi5vbmNs"
    "aWNrPSgpPT57CiAgaWYoYi5kYXRhc2V0LnN3PT09ImFkbWluIiYmIWFkbWluQWxsb3dlZCgpKXt0"
    "b2FzdCgiQWRtaW5zIG9ubHkiKTtyZXR1cm47fQogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IiNzdyBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnJlbW92ZSgib24iKSk7Yi5jbGFz"
    "c0xpc3QuYWRkKCJvbiIpOwogIFsiYXBwIiwiYWRtaW4iXS5mb3JFYWNoKHY9PmRvY3VtZW50Lmdl"
    "dEVsZW1lbnRCeUlkKCJ2aWV3LSIrdikuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZGVuIix2IT09Yi5k"
    "YXRhc2V0LnN3KSk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInRhYnMiKS5zdHlsZS5kaXNw"
    "bGF5PWIuZGF0YXNldC5zdz09PSJhcHAiPyIiOiJub25lIjt3aW5kb3cuc2Nyb2xsVG8oMCwwKTsK"
    "ICByZXF1ZXN0QW5pbWF0aW9uRnJhbWUoKCk9PntpZih3aW5kb3cuX19kb2NrUGxhY2Upd2luZG93"
    "Ll9fZG9ja1BsYWNlKHRydWUpOwogICAgaWYod2luZG93Ll9fdnRhYlBsYWNlKXdpbmRvdy5fX3Z0"
    "YWJQbGFjZSh0cnVlKTt9KTt9KTsKCmxldCBUQUI9InNob3AiOwpkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCIjdGFicyBidXR0b24iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiN0YWJzIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFzc0xp"
    "c3QucmVtb3ZlKCJvbiIpKTtiLmNsYXNzTGlzdC5hZGQoIm9uIik7CiAgVEFCPWIuZGF0YXNldC50"
    "YWI7cmVuZGVyKCk7d2luZG93LnNjcm9sbFRvKDAsMCk7aWYod2luZG93Ll9fZG9ja1BsYWNlKXJl"
    "cXVlc3RBbmltYXRpb25GcmFtZSh3aW5kb3cuX19kb2NrUGxhY2UpO30pOwpkb2N1bWVudC5hZGRF"
    "dmVudExpc3RlbmVyKCJjbGljayIsZT0+e2NvbnN0IGc9ZS50YXJnZXQuY2xvc2VzdCgiW2RhdGEt"
    "Z29dIik7aWYoIWcpcmV0dXJuOwogIGNvbnN0IHQ9Zy5kYXRhc2V0LmdvO2RvY3VtZW50LnF1ZXJ5"
    "U2VsZWN0b3JBbGwoIiN0YWJzIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xl"
    "KCJvbiIseC5kYXRhc2V0LnRhYj09PXQpKTsKICBUQUI9dDtyZW5kZXIoKTt3aW5kb3cuc2Nyb2xs"
    "VG8oMCwwKTt9KTsKCmZ1bmN0aW9uIHJlbmRlcigpe2NvbnN0IHN0PWRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJzdGFnZSIpOwogIGlmKExPQ0tFRCl7cGFpbnRMb2NrZWQoKTtyZXR1cm47fQogIHN0"
    "LmlubmVySFRNTD0nPGRpdiBjbGFzcz0ic2siPjwvZGl2PjxkaXYgY2xhc3M9InNrIj48L2Rpdj48"
    "ZGl2IGNsYXNzPSJzayIgc3R5bGU9ImhlaWdodDoxNDBweCI+PC9kaXY+JzsKICBzZXRUaW1lb3V0"
    "KCgpPT57aWYoTE9DS0VEKXtwYWludExvY2tlZCgpO3JldHVybjt9CiAgICBzdC5pbm5lckhUTUw9"
    "KHtzaG9wOnZTaG9wLG9yZGVyczp2T3JkZXJzLGVhcm46dkVhcm4sd2FsbGV0OnZXYWxsZXQseW91"
    "OnZZb3V9KVtUQUJdKCk7CiAgICBiaW5kU3RhZ2UoKTtyZXZlYWxTdGFnZShzdCk7YmluZFRpbHQo"
    "KTt9LFJNPzA6MTYwKTt9CgpmdW5jdGlvbiB2U2hvcCgpewogIGNvbnN0IGxpdmU9U1RPQ0suZmls"
    "dGVyKHM9PnMucXR5PjApLmxlbmd0aCwgdW5pdHM9U1RPQ0sucmVkdWNlKChhLGIpPT5hK2IucXR5"
    "LDApOwogIGlmKExJVkUmJiFTVE9DSy5sZW5ndGgpewogICAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJj"
    "YXJkIiBzdHlsZT0ibWFyZ2luOjE4cHggMDt0ZXh0LWFsaWduOmNlbnRlcjtwYWRkaW5nOjI2cHgi"
    "PgogICAgICA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNnB4Ij4ke2VzYyhDTVMuZW1wdHlUaXRsZXx8"
    "IlJlc3RvY2tpbmciKX08L2gzPgogICAgICA8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAi"
    "PiR7ZXNjKENNUy5lbXB0eUJvZHl8fCJFdmVyeXRoaW5nIGp1c3Qgc29sZCBvdXQuIEZyZXNoIGFj"
    "Y291bnRzIGxhbmQgaGVyZSBzaG9ydGx5IFx1MjAxNCB0dXJuIG9uIGFuIGFsZXJ0IGFuZCB5b3Ug"
    "d2lsbCBoZWFyIGZpcnN0LiIpfTwvcD4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBz"
    "dHlsZT0ibWFyZ2luLXRvcDoxNHB4IiBkYXRhLWFjdD0iYWxlcnRzIj5Ob3RpZnkgbWU8L2J1dHRv"
    "bj48L2Rpdj5gOwogIH0KICByZXR1cm4gYAogIDxkaXYgY2xhc3M9ImJhbm5lciI+PGRpdiBjbGFz"
    "cz0idHJhY2siPgogICAgJHtbMSwyXS5tYXAoKCk9PmA8c3Bhbj4ke2ljKCJ6YXAiKX0ke0NNUy50"
    "aWNrMX08L3NwYW4+PHNwYW4+JHtpYygid2FsbGV0Iil9JHtDTVMudGljazJ9PC9zcGFuPgogICAg"
    "PHNwYW4+JHtpYygic2hpZWxkIil9JHtDTVMudGljazN9PC9zcGFuPjxzcGFuPiR7aWMoInN0YXIi"
    "KX0ke0NNUy50aWNrNH08L3NwYW4+YCkuam9pbigiIil9CiAgPC9kaXY+PC9kaXY+CiAgJHtHKCJv"
    "bmJvYXJkaW5nIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgaWQ9InRvdXJjYXJkIiBzdHlsZT0i"
    "ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICA8ZGl2IGNsYXNz"
    "PSJmbGFnIj4ke2ljKCJ6YXAiKX08L2Rpdj4KICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lk"
    "dGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+TmV3IGhlcmU/IDMtc3RlcCB0b3VyPC9i"
    "PgogICAgICA8YnI+PHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjly"
    "ZW0iPlRvcCB1cCwgcGljayBhIGNvdW50cnksIGdldCB0aGUgT1RQIGluLWFwcC48L3NtYWxsPjwv"
    "ZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBpZD0idG91cmdvIj5TdGFydDwvYnV0dG9u"
    "PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIGdob3N0IiBpZD0idG91cnNraXAiPlNraXA8L2J1"
    "dHRvbj48L2Rpdj5gKX0KICA8c2VjdGlvbiBjbGFzcz0iaGVybyI+CiAgICA8ZGl2IGNsYXNzPSJr"
    "aWNrIj4ke0NNUy5oZXJvS2lja308L2Rpdj4KICAgIDxoMT4ke0NNUy5oZXJvSDF9PC9oMT4KICAg"
    "IDxwPiR7Q01TLmhlcm9TdWIucmVwbGFjZSgie259IixsaXZlKX08L3A+CiAgICA8ZGl2IGNsYXNz"
    "PSJidG5yb3ciPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIiBkYXRhLXNjcm9sbD0ic2hv"
    "cGxpc3QiPiR7aWMoImJhZyIpfSAke0NNUy5oZXJvQnRuMX08L2J1dHRvbj4KICAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIGdsYXNzIiBkYXRhLWdvPSJ3YWxsZXQiPiR7Q01TLmhlcm9CdG4yfTwvYnV0"
    "dG9uPgogICAgPC9kaXY+CiAgPC9zZWN0aW9uPgogIDxzdmcgY2xhc3M9InRyYWNlIiB2aWV3Qm94"
    "PSIwIDAgMzIwIDEwIiBwcmVzZXJ2ZUFzcGVjdFJhdGlvPSJub25lIj48cGF0aCBjbGFzcz0iYmFz"
    "ZSIgZD0iTTAsNSBMMzIwLDUiLz4KICAgIDxwYXRoIGNsYXNzPSJsZWFkIiBkPSJNMCw1IEwzMjAs"
    "NSIgc3Ryb2tlPSJ1cmwoI3RnMSkiLz4KICAgIDxkZWZzPjxsaW5lYXJHcmFkaWVudCBpZD0idGcx"
    "IiBncmFkaWVudFVuaXRzPSJ1c2VyU3BhY2VPblVzZSIgeDE9Ii0xNjAiIHkxPSIwIiB4Mj0iMCIg"
    "eTI9IjAiPgogICAgICA8c3RvcCBzdG9wLWNvbG9yPSJ2YXIoLS1hY2MpIiBzdG9wLW9wYWNpdHk9"
    "IjAiLz48c3RvcCBvZmZzZXQ9Ii41IiBzdG9wLWNvbG9yPSJ2YXIoLS1hY2MpIi8+CiAgICAgIDxz"
    "dG9wIG9mZnNldD0iMSIgc3RvcC1jb2xvcj0idmFyKC0tYWNjMikiIHN0b3Atb3BhY2l0eT0iMCIv"
    "PgogICAgICA8YW5pbWF0ZSBhdHRyaWJ1dGVOYW1lPSJ4MSIgdmFsdWVzPSItMTYwOzMyMCIgZHVy"
    "PSIyLjJzIiByZXBlYXRDb3VudD0iaW5kZWZpbml0ZSIvPgogICAgICA8YW5pbWF0ZSBhdHRyaWJ1"
    "dGVOYW1lPSJ4MiIgdmFsdWVzPSIwOzQ4MCIgZHVyPSIyLjJzIiByZXBlYXRDb3VudD0iaW5kZWZp"
    "bml0ZSIvPgogICAgPC9saW5lYXJHcmFkaWVudD48L2RlZnM+PC9zdmc+CiAgPGRpdiBjbGFzcz0i"
    "ZmVhdHMiPgogICAgPGRpdiBjbGFzcz0iZmVhdCI+JHtpYygiemFwIil9PGI+JHtDTVMuZmVhdDF9"
    "PC9iPjxzbWFsbD4ke0NNUy5mZWF0MXN9PC9zbWFsbD48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZl"
    "YXQiPiR7aWMoInNoaWVsZCIpfTxiPiR7Q01TLmZlYXQyfTwvYj48c21hbGw+JHtDTVMuZmVhdDJz"
    "fTwvc21hbGw+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmZWF0Ij4ke2ljKCJ3YWxsZXQiKX08Yj4k"
    "e0NNUy5mZWF0M308L2I+PHNtYWxsPiR7Q01TLmZlYXQzc308L3NtYWxsPjwvZGl2PgogIDwvZGl2"
    "PgogICR7Rygic3RvY2tDb3VudGVyIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRp"
    "c3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgPHNwYW4gY2xhc3M9"
    "InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5MaXZlPC9zcGFuPgogICAgPGRpdiBzdHlsZT0i"
    "ZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzZyZW07Y29sb3I6dmFyKC0tdHgyKSI+PGIg"
    "c3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHt1bml0c308L2I+IGFjY291bnRzIGluIHN0b2NrIGFj"
    "cm9zcyA8YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4ke2xpdmV9PC9iPiBjb3VudHJpZXM8L2Rp"
    "dj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWFjdD0iY2hhbm5lbCI+Q2hh"
    "bm5lbDwvYnV0dG9uPjwvZGl2PmApfQogICR7RygiZmxhc2hTYWxlIixgPGRpdiBjbGFzcz0iY2Fy"
    "ZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgi"
    "PgogICAgPHNwYW4gY2xhc3M9InBpbGwgaG90Ij4ke2ljKCJmaXJlIil9ICR7Q01TLmZsYXNoUGls"
    "bH08L3NwYW4+CiAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43"
    "NnJlbTtjb2xvcjp2YXIoLS10eDIpIj4ke0NNUy5mbGFzaFRleHR9IDxiIHN0eWxlPSJjb2xvcjp2"
    "YXIoLS10eCkiIGlkPSJjZCI+MTQ6NTI8L2I+PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "eHMiIGRhdGEtYWN0PSJmbGFzaCI+JHtDTVMuZmxhc2hCdG59PC9idXR0b24+PC9kaXY+YCl9CiAg"
    "PGRpdiBjbGFzcz0id2hlZWwiPjxkaXYgY2xhc3M9InJpbmciPiR7U1RPQ0subWFwKChzLGkpPT5g"
    "PGIgc3R5bGU9InRyYW5zZm9ybTpyb3RhdGUoJHtpKjYwfWRlZykgdHJhbnNsYXRlKDcwcHgpIHJv"
    "dGF0ZSgtJHtpKjYwfWRlZykiPiR7cy5jb2RlfTwvYj5gKS5qb2luKCIiKX08L2Rpdj4KICAgIDxk"
    "aXYgY2xhc3M9ImNhcCI+PGVtPiR7Q01TLndoZWVsS2lja308L2VtPjxiPiR7Q01TLndoZWVsVGl0"
    "bGV9PC9iPgogICAgICA8ZGl2IGNsYXNzPSJjbG91ZCIgc3R5bGU9Im1hcmdpbi10b3A6NnB4Ij48"
    "ZGl2IGNsYXNzPSJ0Ij4ke1sxLDJdLm1hcCgoKT0+Q01TLndoZWVsV29yZHMuc3BsaXQoIiwiKS5t"
    "YXAodz0+YDxpPiR7dy50cmltKCl9PC9pPmApLmpvaW4oIiIpKS5qb2luKCIiKX08L2Rpdj48L2Rp"
    "dj48L2Rpdj48L2Rpdj4KICAke0coInNhbGVzRmVlZCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5K"
    "dXN0IHNvbGQ8L2gzPjxwIGNsYXNzPSJzdWIiPkxpdmUgZmVlZCDigJQgYWxzbyBhdXRvLXBvc3Rl"
    "ZCB0byB5b3VyIGNoYW5uZWwuPC9wPgogICAgJHtGRUVELmxlbmd0aD9GRUVELm1hcCgoW2YsYyxh"
    "LHddKT0+YDxkaXYgY2xhc3M9ImZlZWRsaW5lIj48c3Bhbj4ke2Z9PC9zcGFuPjxiPiR7Y308L2I+"
    "IHNvbGQgZm9yIDxiPiR7YX08L2I+PHNwYW4gc3R5bGU9Im1hcmdpbi1sZWZ0OmF1dG8iPiR7d308"
    "L3NwYW4+PC9kaXY+YCkuam9pbigiIikKICAgICAgOic8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFy"
    "Z2luOjAiPk5vIHNhbGVzIHlldCB0b2RheS48L3A+J308L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJz"
    "ZWMiIGlkPSJzaG9wbGlzdCI+PGgyPiR7Q01TLnNob3BIZWFkfTwvaDI+PGEgZGF0YS1hY3Q9InNv"
    "cnQiPiR7Q01TLnNob3BTb3J0fTwvYT48L2Rpdj4KICA8ZGl2IGNsYXNzPSJtc2VhcmNoIj48ZGl2"
    "IGNsYXNzPSJmaWVsZHdyYXAiPiR7aWMoInNlYXJjaCIpfTxpbnB1dCBpZD0icSIgcGxhY2Vob2xk"
    "ZXI9IiR7Q01TLnNlYXJjaFBofSI+PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20gc29m"
    "dCIgZGF0YS1hY3Q9ImFsZXJ0cyI+JHtDTVMuYWxlcnRzQnRufTwvYnV0dG9uPjwvZGl2PgogIDxk"
    "aXYgY2xhc3M9ImNoaXByb3ciIGlkPSJjaGlwcyI+CiAgICA8YnV0dG9uIGNsYXNzPSJvbiIgZGF0"
    "YS1maWx0ZXI9ImFsbCI+QWxsPC9idXR0b24+PGJ1dHRvbiBkYXRhLWZpbHRlcj0ic3RvY2siPklu"
    "IHN0b2NrPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtZmlsdGVyPSJkZWFsIj5PbiBkZWFsPC9i"
    "dXR0b24+PGJ1dHRvbiBkYXRhLWZpbHRlcj0iY2hlYXAiPlVuZGVyIOKCuTM1PC9idXR0b24+CiAg"
    "ICAke0coInZhcmlhbnRzIixgPGJ1dHRvbiBkYXRhLWZpbHRlcj0idjJmYSI+Tm8gMkZBPC9idXR0"
    "b24+PGJ1dHRvbiBkYXRhLWZpbHRlcj0idm1haWwiPldpdGggbWFpbDwvYnV0dG9uPjxidXR0b24g"
    "ZGF0YS1maWx0ZXI9InZzcGFtIj5TcGFtLWZyZWU8L2J1dHRvbj5gKX08L2Rpdj4KICA8ZGl2IGlk"
    "PSJzaG9wYm9keSI+JHtzaG9wQm9keSgpfTwvZGl2PmA7Cn0KZnVuY3Rpb24gc2hvcEJvZHkoKXty"
    "ZXR1cm4gYCR7RklMVEVSRUQuZmlsdGVyKHM9PnMucXR5PjApLmxlbmd0aD9gPGRpdiBjbGFzcz0i"
    "Y2Fyb3UiPiR7RklMVEVSRUQuZmlsdGVyKHM9PnMucXR5PjApLnNsaWNlKDAsNCkubWFwKHNob3BD"
    "YXJkKS5qb2luKCIiKX08L2Rpdj5gOiIifSR7RklMVEVSRUQubGVuZ3RoP2A8ZGl2IGNsYXNzPSJy"
    "b3dzIj4ke0ZJTFRFUkVELm1hcChzaG9wUm93KS5qb2luKCIiKX08L2Rpdj5gOkVNUFRZfWA7fQpm"
    "dW5jdGlvbiBwcmljZUJpdHMocyl7CiAgcmV0dXJuIGAke3Mud2FzP2A8cz4ke21vbmV5KHMud2Fz"
    "KX08L3M+YDoiIn0ke21vbmV5KHMucHJpY2UpfWA7Cn0KZnVuY3Rpb24gc2hvcFJvdyhzKXtjb25z"
    "dCBpPVNUT0NLLmluZGV4T2Yocyksb2ZmPXMucXR5PT09MDsKICByZXR1cm4gYDxkaXYgY2xhc3M9"
    "InJvdyIgZGF0YS1idXk9IiR7aX0iPjxkaXYgY2xhc3M9ImZsYWciPiR7cy5mfTwvZGl2PgogICAg"
    "PGRpdiBjbGFzcz0ibWlkIj48Yj4ke2VzYyhzLmMpfSAke3MuY29kZX08L2I+CiAgICAgIDxzbWFs"
    "bD4ke29mZj8iT3V0IG9mIHN0b2NrIOKAlCBnZXQgYW4gYWxlcnQiOnMucXR5KyIgaW4gc3RvY2sg"
    "wrcgaW5zdGFudCJ9PC9zbWFsbD4KICAgICAgJHtHKCJyZXZpZXdzIixgPGRpdiBzdHlsZT0ibWFy"
    "Z2luLXRvcDozcHgiPiR7c3RhcnMocy5jKX08L2Rpdj5gKX0KICAgICAgJHtHKCJ2YXJpYW50cyIs"
    "YDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6M3B4O2Rpc3BsYXk6ZmxleDtnYXA6NHB4O2ZsZXgtd3Jh"
    "cDp3cmFwIj4KICAgICAgICA8c3BhbiBjbGFzcz0icGlsbCI+Tm8gMkZBPC9zcGFuPiR7cy5ob3Q/"
    "JzxzcGFuIGNsYXNzPSJwaWxsIj5XaXRoIG1haWw8L3NwYW4+JzoiIn08L2Rpdj5gKX08L2Rpdj4K"
    "ICAgIDxkaXYgY2xhc3M9InJ0Ij48ZGl2IGNsYXNzPSJwIj4ke3ByaWNlQml0cyhzKX08L2Rpdj4K"
    "ICAgICAgJHtHKCJkeW5QcmljZSIsYDxzbWFsbCBzdHlsZT0iZGlzcGxheTpibG9jaztmb250LXNp"
    "emU6LjZyZW07Y29sb3I6dmFyKC0tdHgyKSI+bGl2ZSBwcmljZTwvc21hbGw+YCl9CiAgICAgIDxk"
    "aXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6NXB4O2p1c3RpZnktY29udGVudDpmbGV4LWVuZDtt"
    "YXJnaW4tdG9wOjZweCI+CiAgICAgICAgJHtHKCJ3aXNobGlzdCIsYDxidXR0b24gY2xhc3M9ImJ0"
    "biB4cyBzb2Z0IiBkYXRhLXdpc2g9IiR7aX0iPiR7V0lTSC5pbmNsdWRlcyhpKT8i4pmlIjoi4pmh"
    "In08L2J1dHRvbj5gKX0KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMiPiR7b2ZmPyJOb3Rp"
    "ZnkgTWUiOiJCdXkifTwvYnV0dG9uPjwvZGl2PjwvZGl2PjwvZGl2PmA7fQpmdW5jdGlvbiBzaG9w"
    "Q2FyZChzKXtjb25zdCBpPVNUT0NLLmluZGV4T2Yocyksb2ZmPXMucXR5PT09MDsKICByZXR1cm4g"
    "YDxkaXYgY2xhc3M9InBjYXJkIiBkYXRhLWJ1eT0iJHtpfSIgc3R5bGU9InBvc2l0aW9uOnJlbGF0"
    "aXZlIj4KICAgICR7Rygid2lzaGxpc3QiLGA8c3BhbiBjbGFzcz0iaGVhcnQgJHtXSVNILmluY2x1"
    "ZGVzKGkpPyJvbiI6IiJ9IiBkYXRhLXdpc2g9IiR7aX0iPiR7aWMoInN0YXIiKX08L3NwYW4+YCl9"
    "CiAgICA8ZGl2IGNsYXNzPSJ0b3AiPjxkaXYgY2xhc3M9ImZsYWciPiR7cy5mfTwvZGl2PgogICAg"
    "ICA8c3BhbiBjbGFzcz0icGlsbCAke29mZj8ib2ZmIjoib24ifSI+JHtvZmY/IlNvbGQgb3V0Ijpz"
    "LnF0eSsiIGxlZnQifTwvc3Bhbj48L2Rpdj4KICAgIDxkaXY+PGRpdiBjbGFzcz0ibm0iPiR7ZXNj"
    "KHMuYyl9PC9kaXY+PGRpdiBjbGFzcz0ibWV0YSI+JHtzLmNvZGV9IMK3IGluc3RhbnQ8L2Rpdj4K"
    "ICAgICAgJHtHKCJyZXZpZXdzIixgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDo0cHgiPiR7c3RhcnMo"
    "cy5jKX08L2Rpdj5gKX08L2Rpdj4KICAgIDxkaXYgY2xhc3M9InByaWNlIj4ke3ByaWNlQml0cyhz"
    "KX08L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayBzbSI+JHtvZmY/Ik5vdGlmeSBN"
    "ZSI6IkJ1eSBOb3cifTwvYnV0dG9uPjwvZGl2PmA7fQoKZnVuY3Rpb24gdk9yZGVycygpewogIHJl"
    "dHVybiBgPGRpdiBjbGFzcz0ic2VjIj48aDI+WW91ciBPcmRlcnM8L2gyPjxhIGRhdGEtYWN0PSJj"
    "c3YiPkV4cG9ydDwvYT48L2Rpdj4KICAke09SREVSUy5tYXAoKG8sb2kpPT5gPGRpdiBjbGFzcz0i"
    "Y2FyZCIgZGF0YS1vcmRlci1pZD0iJHtvLmlkfSI+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZs"
    "ZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMnB4Ij4KICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+"
    "JHtvLmZ9PC9kaXY+CiAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5"
    "bGU9ImZvbnQtc2l6ZTouODVyZW07ZGlzcGxheTpibG9jayI+JHtvLm51bX08L2I+CiAgICAgICAg"
    "PHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouN3JlbSI+JHtvLndoZW59"
    "PC9zbWFsbD48L2Rpdj4KICAgICAgPGRpdiBzdHlsZT0idGV4dC1hbGlnbjpyaWdodCI+PHNwYW4g"
    "Y2xhc3M9InBpbGwgJHtvLnN0PT09IkRlbGl2ZXJlZCI/Im9uIjoib2ZmIn0iPiR7by5zdH08L3Nw"
    "YW4+PC9kaXY+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0ibWFyZ2lu"
    "OjEwcHggMCAwO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpIj4KICAgICAgPGRpdiBjbGFzcz0ia3Yi"
    "PjxzcGFuPlBob25lIG51bWJlcjwvc3Bhbj4KICAgICAgICA8Yj48Y29kZSBzdHlsZT0iZm9udC1z"
    "aXplOi44cmVtO3VzZXItc2VsZWN0OmFsbCI+JHtlc2Moby5waG9uZXx8IuKAlCIpfTwvY29kZT4K"
    "ICAgICAgICAke28ucGhvbmU/YDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWNvcHk9"
    "InRleHQiIGRhdGEtY29weS12YWx1ZT0iJHtlc2Moby5waG9uZSl9Ij5Db3B5PC9idXR0b24+YDoi"
    "In08L2I+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj4yRkEgcGFzc3dvcmQ8L3Nw"
    "YW4+CiAgICAgICAgPGI+PGNvZGUgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbTt1c2VyLXNlbGVjdDph"
    "bGwiPiR7ZXNjKG8udHdvZmF8fCJub25lIil9PC9jb2RlPgogICAgICAgICR7by50d29mYT9gPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtY29weT0idGV4dCIgZGF0YS1jb3B5LXZhbHVl"
    "PSIke2VzYyhvLnR3b2ZhKX0iPkNvcHk8L2J1dHRvbj5gOiIifTwvYj48L2Rpdj4KICAgICAgPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPkxhc3QgbG9naW4gY29kZTwvc3Bhbj4KICAgICAgICA8Yj48Y29k"
    "ZSBzdHlsZT0iZm9udC1zaXplOi44cmVtO3VzZXItc2VsZWN0OmFsbCI+JHtlc2Moby5vdHB8fCJu"
    "b3QgeWV0Iil9PC9jb2RlPgogICAgICAgICR7by5vdHA/YDxidXR0b24gY2xhc3M9ImJ0biB4cyBz"
    "b2Z0IiBkYXRhLWNvcHk9InRleHQiIGRhdGEtY29weS12YWx1ZT0iJHtlc2Moby5vdHApfSI+Q29w"
    "eTwvYnV0dG9uPmA6IiJ9PC9iPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+U3Rh"
    "dHVzPC9zcGFuPjxiPiR7by5sb2dpbj09PSJpbiIKICAgICAgICA/ICc8c3BhbiBjbGFzcz0icGls"
    "bCBvbiI+TG9nZ2VkIGluIMK3IHlvdXJzIG9ubHk8L3NwYW4+JwogICAgICAgIDogKG8ud2luZG93"
    "PjAKICAgICAgICAgICAgPyAnPHNwYW4gY2xhc3M9InBpbGwiPkNvZGVzIGZvcndhcmRpbmcgwrcg"
    "JytvLndpbmRvdysnIG1pbiBsZWZ0PC9zcGFuPicKICAgICAgICAgICAgOiAnPHNwYW4gY2xhc3M9"
    "InBpbGwgb2ZmIj5IYW5kb3ZlciBjb21wbGV0ZTwvc3Bhbj4nKX08L2I+PC9kaXY+CiAgICA8L2Rp"
    "dj4KICAgICR7RygidGltZWxpbmUiLGA8ZGl2IGNsYXNzPSJ0bCI+CiAgICAgIDxkaXYgY2xhc3M9"
    "Im9rIj48Yj5QYWlkPC9iPjxzbWFsbD4ke28ud2hlbn08L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2"
    "IGNsYXNzPSJvayI+PGI+RGVsaXZlcmVkPC9iPjxzbWFsbD5TZXNzaW9uIGhhbmRlZCBvdmVyIGlu"
    "c3RhbnRseTwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9IiR7by5zdD09PSJEZWxpdmVy"
    "ZWQiPyJvayI6IiJ9Ij48Yj5PVFAgcmVjZWl2ZWQ8L2I+PHNtYWxsPiR7by5zdD09PSJEZWxpdmVy"
    "ZWQiPyJDb2RlIGZvcndhcmRlZCBpbi1hcHAiOiJXYWl0aW5nIn08L3NtYWxsPjwvZGl2PgogICAg"
    "ICA8ZGl2PjxiPkNsb3NlZDwvYj48c21hbGw+QXV0by1jbG9zZXMgYWZ0ZXIgdGhlIHdhcnJhbnR5"
    "IHdpbmRvdzwvc21hbGw+PC9kaXY+PC9kaXY+YCl9CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0"
    "eWxlPSJtYXJnaW4tdG9wOjExcHgiPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20iIGRhdGEt"
    "b3RwPSIke28ubnVtfSI+R2V0IE9UUDwvYnV0dG9uPgogICAgICAke0coInJlZG93bmxvYWQiLGA8"
    "YnV0dG9uIGNsYXNzPSJidG4gc20gc29mdCIgZGF0YS1vcmQ9ImRldGFpbHMiPkRldGFpbHM8L2J1"
    "dHRvbj5gKX08L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6"
    "N3B4Ij4KICAgICAgJHtHKCJzZXNzaW9uRmlsZSIsYDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0"
    "IiBkYXRhLW9yZD0iZmlsZSI+JHtpYygiY29weSIpfSBGaWxlPC9idXR0b24+YCl9CiAgICAgICR7"
    "RygicmVjZWlwdHMiLGA8YnV0dG9uIGNsYXNzPSJidG4gc20gc29mdCIgZGF0YS1vcmQ9InJlY2Vp"
    "cHQiPlJlY2VpcHQ8L2J1dHRvbj5gKX0KICAgICAgJHsoby5sb2dpbj09PSJpbiIpPyIiOmA8YnV0"
    "dG9uIGNsYXNzPSJidG4gc20iIGRhdGEtbG9nZ2VkaW49IiR7by5pZH0iPkknbSBsb2dnZWQgaW48"
    "L2J1dHRvbj5gfQogICAgICAke0coIndhcnJhbnR5IixgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIGdo"
    "b3N0IiBkYXRhLWNsYWltLW9yZGVyPSIke28uaWR9Ij5SZXBvcnQgaXNzdWU8L2J1dHRvbj5gKX08"
    "L2Rpdj4KICAgICR7RygicmV2aWV3cyIsYDxkaXYgY2xhc3M9Imt2IiBzdHlsZT0ibWFyZ2luLXRv"
    "cDo5cHgiPjxzcGFuPlJhdGUgdGhpcyBvcmRlcjwvc3Bhbj4KICAgICAgPGI+PGJ1dHRvbiBjbGFz"
    "cz0iYnRuIHhzIHNvZnQiIGRhdGEtcmF0ZT0iJHtvLmN9Ij5SYXRlPC9idXR0b24+PC9iPjwvZGl2"
    "PmApfQogIDwvZGl2PmApLmpvaW4oIiIpfQoKICA8ZGl2IGNsYXNzPSJjYXJkIiBzdHlsZT0idGV4"
    "dC1hbGlnbjpjZW50ZXIiPjxoMz5OZWVkIGhlbHAgd2l0aCBhbiBvcmRlcj88L2gzPgogICAgPHAg"
    "Y2xhc3M9InN1YiI+JHtGRUFULmF1dG9SZWZ1bmQ/IkZhaWxlZCBsb2dpbnMgYXJlIHJlZnVuZGVk"
    "IGF1dG9tYXRpY2FsbHkgd2l0aGluIDEwIG1pbnV0ZXMuIjoiQ29udGFjdCBzdXBwb3J0IGFuZCB3"
    "ZSB3aWxsIGxvb2sgaW50byBpdC4ifTwvcD4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9"
    "Im1hcmdpbi10b3A6MTJweCI+PGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtYWN0PSJzdXBw"
    "b3J0Ij5TdXBwb3J0PC9idXR0b24+CiAgICAke0coImRpc3B1dGUiLGA8YnV0dG9uIGNsYXNzPSJi"
    "dG4gZ2hvc3QiIGRhdGEtZGlzcHV0ZT5EaXNwdXRlPC9idXR0b24+YCl9PC9kaXY+PC9kaXY+YDsK"
    "fQoKZnVuY3Rpb24gdkVhcm4oKXsKICByZXR1cm4gYCR7Rygic3RyZWFrIixgPGRpdiBjbGFzcz0i"
    "Y2FyZCI+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dh"
    "cDoxMnB4Ij4KICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygiemFwIil9PC9kaXY+PGRpdiBz"
    "dHlsZT0iZmxleDoxIj48aDM+RGFpbHkgQ2hlY2staW48L2gzPjxwIGNsYXNzPSJzdWIiPkRheSAk"
    "e0VBUk4uc3RyZWFrfSBzdHJlYWsgwrcgJHttb25leShFQVJOLmRhaWx5KX0gd2FpdGluZzwvcD48"
    "L2Rpdj48L2Rpdj4KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6NnB4O21hcmdpbjox"
    "NHB4IDAiPgogICAgICAke1sxLDIsMyw0LDUsNiw3XS5tYXAoZD0+YDxkaXYgc3R5bGU9ImZsZXg6"
    "MTtoZWlnaHQ6MzRweDtib3JkZXItcmFkaXVzOjlweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6"
    "Y2VudGVyO2ZvbnQtc2l6ZTouNjhyZW07Zm9udC13ZWlnaHQ6NzAwO2JvcmRlcjoxcHggc29saWQg"
    "dmFyKC0tbGluZSk7JHtkPD0oRUFSTi5zdHJlYWt8fDApPyJiYWNrZ3JvdW5kOnZhcigtLXBpbGxn"
    "cmFkKTtjb2xvcjojZmZmZmZmO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVudCI6ImJhY2tncm91bmQ6"
    "dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4MikifSI+JHtkfTwvZGl2PmApLmpvaW4oIiIpfTwv"
    "ZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBibG9jayIgZGF0YS1jbGFpbSAke0VBUk4u"
    "Y2xhaW1lZD8iZGlzYWJsZWQiOiIifT4ke0VBUk4uY2xhaW1lZD8iQ2xhaW1lZCB0b2RheSI6IkNs"
    "YWltICIrbW9uZXkoRUFSTi5kYWlseSl9PC9idXR0b24+PC9kaXY+YCl9CiAgJHtHKCJsb3lhbHR5"
    "IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxveWFsdHkgUG9pbnRzPC9oMz48cCBjbGFzcz0ic3Vi"
    "Ij4xIHBvaW50IHBlciDigrkxMCBzcGVudC4gMTAwIHBvaW50cyA9IOKCuTEwIGJhbGFuY2UuPC9w"
    "PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkJhbGFuY2U8L3NwYW4+PGI+JHtFQVJOLnBvaW50"
    "c30gcHRzPC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPldvcnRoPC9zcGFuPjxi"
    "PiR7bW9uZXkoRUFSTi5wb2ludHNXb3J0aCl9PC9iPjwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIGJsb2NrIiBpZD0icmVkZWVtZ28iICR7RUFSTi5wb2ludHM+MD8iIjoiZGlzYWJsZWQifT5S"
    "ZWRlZW0gUG9pbnRzPC9idXR0b24+PC9kaXY+YCl9CiAgJHthbnlGKCJyZWZEYXNoIiwicmVmTDIi"
    "KT9gPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkludml0ZSAmYW1wOyBFYXJuPC9oMz4KICAgIDxwIGNs"
    "YXNzPSJzdWIiPiR7RkVBVC5yZWZMMj8iNSUgZnJvbSBwZW9wbGUgeW91IGludml0ZSwgcGx1cyAx"
    "JSBmcm9tIHRoZSBwZW9wbGUgdGhleSBpbnZpdGUuIjoiMSUgb2YgZXZlcnkgZGVwb3NpdCB5b3Vy"
    "IGludml0ZXMgZXZlciBtYWtlLiJ9PC9wPgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkludml0"
    "ZXM8L3NwYW4+PGI+JHtFQVJOLmludml0ZXN9PC9iPjwvZGl2PgogICAgJHtHKCJyZWZMMiIsYDxk"
    "aXYgY2xhc3M9Imt2Ij48c3Bhbj5MZXZlbCAxIGVhcm5pbmdzPC9zcGFuPjxiPiR7bW9uZXkoRUFS"
    "Ti5sMSl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxldmVsIDIgZWFybmlu"
    "Z3M8L3NwYW4+PGI+JHttb25leShFQVJOLmwyKX08L2I+PC9kaXY+YCl9CiAgICA8ZGl2IGNsYXNz"
    "PSJrdiI+PHNwYW4+VG90YWwgZWFybmVkPC9zcGFuPjxiPiR7bW9uZXkoRUFSTi5lYXJuZWQpfTwv"
    "Yj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Zb3VyIGxpbms8L3NwYW4+PGIgc3R5"
    "bGU9ImZvbnQtc2l6ZTouNzJyZW0iPiR7ZXNjKEVBUk4ubGluayl9PC9iPjwvZGl2PgogICAgPGRp"
    "diBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48YnV0dG9uIGNsYXNzPSJi"
    "dG4iIGRhdGEtY29weT0icmVmIj4ke2ljKCJjb3B5Iil9IENvcHkgTGluazwvYnV0dG9uPgogICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtY29weT0icmVmIj5TaGFyZTwvYnV0dG9uPjwv"
    "ZGl2PgogICAgJHtHKCJyZWZEYXNoIixgPGRpdiBjbGFzcz0ic2VjIiBzdHlsZT0ibWFyZ2luLXRv"
    "cDo2cHgiPjxoMiBzdHlsZT0iZm9udC1zaXplOi43OHJlbSI+UmVjZW50IGludml0ZXM8L2gyPjwv"
    "ZGl2PgogICAgJHtFQVJOLnJlY2VudC5sZW5ndGg/RUFSTi5yZWNlbnQubWFwKChbdSxhLGVdKT0+"
    "YDxkaXYgY2xhc3M9ImZlZWRsaW5lIj48Yj4ke2VzYyh1KX08L2I+ICR7ZXNjKGEpfTxzcGFuIHN0"
    "eWxlPSJtYXJnaW4tbGVmdDphdXRvO2NvbG9yOnZhcigtLW9rKSI+JHtlc2MoZSl9PC9zcGFuPjwv"
    "ZGl2PmApLmpvaW4oIiIpCiAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5O"
    "b2JvZHkgaGFzIGpvaW5lZCB0aHJvdWdoIHlvdXIgbGluayB5ZXQuPC9wPid9CiAgICA8cCBjbGFz"
    "cz0ic3ViIiBzdHlsZT0ibWFyZ2luOjExcHggMCAwIj5SZWZlcnJhbCBlYXJuaW5ncyBsYW5kIGlu"
    "IHlvdXIgYmFsYW5jZSBhdXRvbWF0aWNhbGx5IOKAlCBub3RoaW5nIHRvIGNsYWltLjwvcD5gKX0K"
    "ICA8L2Rpdj5gOiIifQogICR7RygibGVhZGVyYm9hcmQiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+"
    "VGhpcyB3ZWVrJ3MgdG9wIGJ1eWVyczwvaDM+PHAgY2xhc3M9InN1YiI+TnVtYmVyIG9uZSB3aW5z"
    "IOKCuTUwMCBvbiBTdW5kYXkuPC9wPgogICAgJHtFQVJOLmxlYWRlcnMubGVuZ3RoP0VBUk4ubGVh"
    "ZGVycy5tYXAoKFtyLHUsdl0saSk9PmA8ZGl2IGNsYXNzPSJsYiI+PHNwYW4gY2xhc3M9InJrICR7"
    "aT09PTA/ImciOiIifSI+JHtlc2Mocil9PC9zcGFuPgogICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6"
    "MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+JHtlc2ModSl9PC9iPjwv"
    "ZGl2PjxiIHN0eWxlPSJmb250LXNpemU6LjhyZW0iPiR7ZXNjKHYpfTwvYj48L2Rpdj5gKS5qb2lu"
    "KCIiKQogICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gcHVyY2hhc2Vz"
    "IHlldCB0aGlzIHdlZWsuPC9wPid9PC9kaXY+YCl9CiAgJHtHKCJ3aW5iYWNrIixgPGRpdiBjbGFz"
    "cz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2Fw"
    "OjEwcHgiPgogICAgPHNwYW4gY2xhc3M9InBpbGwgaG90Ij4ke2ljKCJmaXJlIil9IENvbWViYWNr"
    "PC9zcGFuPgogICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRy"
    "ZW07Y29sb3I6dmFyKC0tdHgyKSI+MTAlIG9mZiDigJQgZXhwaXJlcyBpbiA0OCBob3VyczwvZGl2"
    "PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBpZD0ib2ZmZXJ1c2UiPlVzZTwvYnV0dG9uPjwv"
    "ZGl2PmApfQogIDxkaXYgY2xhc3M9InNlYyI+PGgyPlRhc2tzPC9oMj48L2Rpdj4KICAke0VBUk4u"
    "dGFza3MubWFwKChbdCxyLGRvbmVdKT0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJk"
    "aXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMXB4Ij4KICAgICAgPGRpdiBjbGFz"
    "cz0iZmxhZyI+JHtkb25lP2ljKCJjaGVjayIpOmljKCJ6YXAiKX08L2Rpdj4KICAgICAgPGRpdiBz"
    "dHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44MnJlbSI+JHt0"
    "fTwvYj48YnI+PHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouN3JlbSI+"
    "UmV3YXJkICR7cn08L3NtYWxsPjwvZGl2PgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgJHtk"
    "b25lPyJzb2Z0IjoiIn0iICR7ZG9uZT8iZGlzYWJsZWQiOiIifSBkYXRhLWFjdD0idGFzayI+JHtk"
    "b25lPyJEb25lIjoiU3RhcnQifTwvYnV0dG9uPjwvZGl2PmApLmpvaW4oIiIpfWA7Cn0KCmZ1bmN0"
    "aW9uIHZXYWxsZXQoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9Imhlcm8iIHN0eWxlPSJ0ZXh0LWFs"
    "aWduOmNlbnRlciI+CiAgICA8ZGl2IGNsYXNzPSJraWNrIj4ke0NNUy53YWxLaWNrfTwvZGl2Pgog"
    "ICAgPGgxIHN0eWxlPSJmb250LXNpemU6Mi4zcmVtIj7igrk8c3BhbiBpZD0iYmFsMiIgY2xhc3M9"
    "InRpY2siPiR7QkFMLnRvRml4ZWQoMil9PC9zcGFuPjwvaDE+CiAgICA8cCBzdHlsZT0ibWFyZ2lu"
    "OjAgYXV0byAxNHB4Ij4ke0NNUy53YWxTdWJ9PC9wPgogICAgPGRpdiBzdHlsZT0ibWFyZ2luOjAg"
    "YXV0bzttYXgtd2lkdGg6MzAwcHgiPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2Nr"
    "IiBkYXRhLWRlcD4ke2ljKCJwbHVzIil9ICR7Q01TLndhbEFkZH08L2J1dHRvbj4KICAgICAgJHtH"
    "KCJjcnlwdG8iLGA8YnV0dG9uIGNsYXNzPSJidG4gZ2xhc3MgYmxvY2siIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjhweCIgZGF0YS1jcnlwdG8+UGF5IHdpdGggQ3J5cHRvPC9idXR0b24+YCl9PC9kaXY+PC9k"
    "aXY+CiAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1p"
    "dGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNz"
    "PSJkb3QiPjwvaT5BdXRvLXZlcmlmeSBPTjwvc3Bhbj4KICAgIDxkaXYgc3R5bGU9ImZsZXg6MTtt"
    "aW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlRvcC11cHMgYXJl"
    "IGNyZWRpdGVkIGFzIHNvb24gYXMgdGhleSBhcnJpdmU8L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9"
    "ImJ0biB4cyBzb2Z0IiBkYXRhLWFjdD0ic3RhdHVzIj5TdGF0dXM8L2J1dHRvbj48L2Rpdj4KICAk"
    "e0coImRlcEJvbnVzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlRvcC11cCBib251czwvaDM+PHAg"
    "Y2xhc3M9InN1YiI+RXh0cmEgY3JlZGl0IGFkZGVkIGF1dG9tYXRpY2FsbHkuPC9wPgogICAgPGRp"
    "diBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo3cHg7bWFyZ2luLXRvcDoxMHB4Ij4KICAgICAgJHtb"
    "WyLigrk1MDAiLCIrNSUiXSxbIuKCuTEwMDAiLCIrOCUiXSxbIuKCuTIwMDAiLCIrMTIlIl1dLm1h"
    "cCgoW2EsYl0pPT5gPGRpdiBzdHlsZT0iZmxleDoxO3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6"
    "OXB4IDRweDtib3JkZXItcmFkaXVzOjEycHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTti"
    "YWNrZ3JvdW5kOnZhcigtLWNhcmQyKSI+CiAgICAgICAgPGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJl"
    "bTtkaXNwbGF5OmJsb2NrIj4ke2F9PC9iPjxzbWFsbCBzdHlsZT0iZm9udC1zaXplOi42OHJlbTtj"
    "b2xvcjp2YXIoLS1vaykiPiR7Yn08L3NtYWxsPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PjwvZGl2"
    "PmApfQogIDxkaXYgY2xhc3M9InNlYyI+PGgyPlF1aWNrIFRvcC11cDwvaDI+PC9kaXY+CiAgPGRp"
    "diBjbGFzcz0iZ3JpZCIgc3R5bGU9ImdyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoMywxZnIp"
    "Ij4KICAgICR7WzUwLDEwMCwyMDAsNTAwLDEwMDAsMjAwMF0ubWFwKGE9PmA8YnV0dG9uIGNsYXNz"
    "PSJidG4gc29mdCIgZGF0YS1hbXQ9IiR7YX0iPuKCuSR7YX08L2J1dHRvbj5gKS5qb2luKCIiKX08"
    "L2Rpdj4KICAke0coImRlcExpbWl0cyIsYDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46OXB4"
    "IDAgMDtmb250LXNpemU6LjdyZW0iPk1pbmltdW0g4oK5MTAgwrcgbWF4aW11bSDigrkyMCwwMDAg"
    "cGVyIHRvcC11cC48L3A+YCl9CiAgPGRpdiBjbGFzcz0iY2FyZCIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTJweCI+PGgzPiR7Q01TLnBheVRpdGxlfTwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj4ke0NNUy5w"
    "YXlTdWJ9PC9wPgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlVQSSBJRDwvc3Bhbj48Yj4ke2Vz"
    "YyhTVE9SRS51cGl8fCJub3Qgc2V0IHlldCIpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2"
    "Ij48c3Bhbj5QYXllZTwvc3Bhbj48Yj4ke2VzYyhTVE9SRS5wYXllZXx8IuKAlCIpfTwvYj48L2Rp"
    "dj4KICAgICR7RygidXBpUm90YXRlIixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkhhbmRsZXMgaW4g"
    "cm90YXRpb248L3NwYW4+PGI+MyBhY3RpdmU8L2I+PC9kaXY+YCl9CiAgICA8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4+UGF5ZWU8L3NwYW4+PGI+WW91ciBTdG9yZSBOYW1lPC9iPjwvZGl2PgogICAgPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPkF1dG8tdmVyaWZ5PC9zcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIo"
    "LS1vaykiPkVuYWJsZWQ8L2I+PC9kaXY+CiAgICAke0coIndyb25nQW10IixgPGRpdiBjbGFzcz0i"
    "a3YiPjxzcGFuPldyb25nIGFtb3VudDwvc3Bhbj48Yj5DcmVkaXRlZCBhcyBwYWlkPC9iPjwvZGl2"
    "PmApfQogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIHNvZnQiIHN0eWxlPSJtYXJnaW4tdG9w"
    "OjEycHgiIGRhdGEtY29weT0idXBpIj4ke2ljKCJjb3B5Iil9IENvcHkgVVBJIElEPC9idXR0b24+"
    "PC9kaXY+CiAgJHtHKCJhYmFuZG9uUmVtaW5kIixgPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdp"
    "bjoxMHB4IDAgMDtmb250LXNpemU6LjdyZW0iPldlIHdpbGwgcmVtaW5kIHlvdSBiZWZvcmUgYW4g"
    "dW5maW5pc2hlZCBwYXltZW50IGV4cGlyZXMuPC9wPmApfQogIDxkaXYgY2xhc3M9InNlYyI+PGgy"
    "PiR7RkVBVC5sZWRnZXI/IlN0YXRlbWVudCI6Ikhpc3RvcnkifTwvaDI+JHtHKCJsZWRnZXIiLGA8"
    "YSBkYXRhLWFkbWluLWFjdD0iZXhwb3J0Ij5FeHBvcnQ8L2E+YCl9PC9kaXY+CiAgJHtUWC5tYXAo"
    "KHQsaSk9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtnYXA6MTFweCI+CiAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke3QuYVswXT09"
    "PSIrIj9pYygiZG93biIpOmljKCJ1cCIpfTwvZGl2PgogICAgPGRpdiBzdHlsZT0iZmxleDoxO21p"
    "bi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44MnJlbSI+JHt0LnR9PC9iPjxicj48c21h"
    "bGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi43cmVtIj4ke3Qud308L3NtYWxs"
    "PjwvZGl2PgogICAgPGRpdiBzdHlsZT0idGV4dC1hbGlnbjpyaWdodCI+PGIgc3R5bGU9ImZvbnQt"
    "c2l6ZTouODVyZW07Y29sb3I6JHt0LmFbMF09PT0iKyI/InZhcigtLW9rKSI6InZhcigtLXR4KSJ9"
    "Ij4ke3QuYX08L2I+CiAgICAke0coImxlZGdlciIsYDxicj48c21hbGwgc3R5bGU9ImZvbnQtc2l6"
    "ZTouNjVyZW07Y29sb3I6dmFyKC0tdHgyKSI+YmFsIOKCuSR7KDI0OC41K2kqNykudG9GaXhlZCgy"
    "KX08L3NtYWxsPmApfTwvZGl2PjwvZGl2PmApLmpvaW4oIiIpfWA7Cn0KCmZ1bmN0aW9uIHZZb3Uo"
    "KXsKICBjb25zdCBtZT1NRXx8e307CiAgY29uc3Qgc3BlbnQ9bWUuc3BlbnR8fDAsIHRhcmdldD1z"
    "cGVudD49NTAwPzIwMDA6NTAwOwogIGNvbnN0IHBjdD1NYXRoLm1heCg0LE1hdGgubWluKDEwMCxN"
    "YXRoLnJvdW5kKHNwZW50L3RhcmdldCoxMDApKSk7CiAgY29uc3QgYXY9bWUucGhvdG8/YDxpbWcg"
    "c3JjPSIke21lLnBob3RvfSIgYWx0PSIiIHN0eWxlPSJ3aWR0aDo2NHB4O2hlaWdodDo2NHB4O2Jv"
    "cmRlci1yYWRpdXM6NTAlO29iamVjdC1maXQ6Y292ZXI7ZGlzcGxheTpibG9jazttYXJnaW46MCBh"
    "dXRvIDEwcHgiIG9uZXJyb3I9InRoaXMucmVwbGFjZVdpdGgoT2JqZWN0LmFzc2lnbihkb2N1bWVu"
    "dC5jcmVhdGVFbGVtZW50KCdkaXYnKSx7Y2xhc3NOYW1lOidmbGFnJyxzdHlsZTond2lkdGg6NjRw"
    "eDtoZWlnaHQ6NjRweDttYXJnaW46MCBhdXRvIDEwcHgnfSkpIj5gCiAgICA6YDxkaXYgY2xhc3M9"
    "ImZsYWciIHN0eWxlPSJ3aWR0aDo2NHB4O2hlaWdodDo2NHB4O21hcmdpbjowIGF1dG8gMTBweCI+"
    "JHtpYygidXNlciIpfTwvZGl2PmA7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIHNoaW5lY2Fy"
    "ZCB0aWx0IiBkYXRhLXRpbHQgc3R5bGU9InRleHQtYWxpZ246Y2VudGVyIj48c3BhbiBjbGFzcz0i"
    "Z2xhcmUiPjwvc3Bhbj4KICAgICR7YXZ9CiAgICA8aDMgc3R5bGU9ImZvbnQtc2l6ZToxLjA1cmVt"
    "Ij4ke2VzYyhtZS5uYW1lfHwiWW91Iil9PC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPiR7ZXNjKG1l"
    "LnVzZXJuYW1lfHwoIklEICIrKG1lLmlkfHwi4oCUIikpKX0ke21lLnVzZXJuYW1lPyIgwrcgSUQg"
    "IisobWUuaWR8fCLigJQiKToiIn0ke21lLmpvaW5lZD8iIMK3IGpvaW5lZCAiK2VzYyhtZS5qb2lu"
    "ZWQpOiIifTwvcD4KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4O2p1c3RpZnkt"
    "Y29udGVudDpjZW50ZXI7bWFyZ2luLXRvcDoxMHB4O2ZsZXgtd3JhcDp3cmFwIj4KICAgICAgPHNw"
    "YW4gY2xhc3M9InBpbGwgb24iPiR7ZXNjKG1lLnRpZXJ8fCJCcm9uemUiKX08L3NwYW4+PHNwYW4g"
    "Y2xhc3M9InBpbGwiPiR7bWUub3JkZXJzfHwwfSBvcmRlcnM8L3NwYW4+CiAgICAgIDxzcGFuIGNs"
    "YXNzPSJwaWxsIj4ke21vbmV5KG1lLmJhbGFuY2V8fDApfTwvc3Bhbj4KICAgICAgJHtHKCJsb3lh"
    "bHR5IixgPHNwYW4gY2xhc3M9InBpbGwiPiR7bWUucG9pbnRzfHwwfSBwdHM8L3NwYW4+YCl9CiAg"
    "ICAgICR7Rygic3RyZWFrIixgPHNwYW4gY2xhc3M9InBpbGwiPiR7bWUuc3RyZWFrfHwwfS1kYXkg"
    "c3RyZWFrPC9zcGFuPmApfQogICAgICAkeyhGRUFULmJ1eWVyQmFkZ2UmJihtZS5vcmRlcnN8fDAp"
    "Pj01KT9gPHNwYW4gY2xhc3M9InBpbGwgb24iPiR7aWMoImNoZWNrIil9IFZlcmlmaWVkIGJ1eWVy"
    "PC9zcGFuPmA6IiJ9CiAgICAgICR7KG1lLmRlYnQ+MCk/YDxzcGFuIGNsYXNzPSJwaWxsIGhvdCI+"
    "JHttb25leShtZS5kZWJ0KX0gdWRoYXI8L3NwYW4+YDoiIn08L2Rpdj4KICAgIDxkaXYgc3R5bGU9"
    "Im1hcmdpbi10b3A6MTRweDtoZWlnaHQ6N3B4O2JvcmRlci1yYWRpdXM6OXB4O2JhY2tncm91bmQ6"
    "dmFyKC0tY2FyZDIpO292ZXJmbG93OmhpZGRlbiI+CiAgICAgIDxpIHN0eWxlPSJkaXNwbGF5OmJs"
    "b2NrO2hlaWdodDoxMDAlO3dpZHRoOiR7cGN0fSU7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCki"
    "PjwvaT48L2Rpdj4KICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW4tdG9wOjdweDtmb250"
    "LXNpemU6LjcycmVtIj4ke21lLm5leHRfdGllcj4wCiAgICAgID8gbW9uZXkobWUubmV4dF90aWVy"
    "KSsiIG1vcmUgc3BlbnQgdG8gcmVhY2ggIisoc3BlbnQ+PTUwMD8iUGxhdGludW0iOiJHb2xkIikK"
    "ICAgICAgOiAiVG9wIHRpZXIgcmVhY2hlZCJ9PC9wPjwvZGl2PgogICR7Rygid2lzaGxpc3QiLGA8"
    "ZGl2IGNsYXNzPSJzZWMiPjxoMj5TYXZlZCBjb3VudHJpZXM8L2gyPjwvZGl2PgogICAgPGRpdiBj"
    "bGFzcz0iY2FyZCI+JHtXSVNILmxlbmd0aD9XSVNILm1hcChpPT5gPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPiR7U1RPQ0tbaV0uZn0gJHtlc2MoU1RPQ0tbaV0uYyl9ICR7U1RPQ0tbaV0uY29kZX08L3Nw"
    "YW4+CiAgICAgIDxiPjxidXR0b24gY2xhc3M9ImJ0biB4cyIgZGF0YS1idXk9IiR7aX0iPkJ1eTwv"
    "YnV0dG9uPjwvYj48L2Rpdj5gKS5qb2luKCIiKTonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdp"
    "bjowIj5UYXAgdGhlIGhlYXJ0IG9uIGFueSBjb3VudHJ5IHRvIHNhdmUgaXQgaGVyZS48L3A+J308"
    "L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5BY2NvdW50PC9oMj48L2Rpdj4KICA8ZGl2"
    "IGNsYXNzPSJjYXJkIj4ke1tbIk9yZGVyIGhpc3RvcnkiLCJvcmRlcnMiXSxbIldhbGxldCAmIHBh"
    "eW1lbnRzIiwid2FsbGV0Il1dCiAgICAuY29uY2F0KGFueUYoInJlZkRhc2giLCJyZWZMMiIpP1tb"
    "IlJlZmVycmFsIHByb2dyYW0iLCJlYXJuIl1dOltdKQogICAgLm1hcCgoW3QsZ10pPT5gPGRpdiBj"
    "bGFzcz0ia3YiIHN0eWxlPSJjdXJzb3I6cG9pbnRlciIgZGF0YS1nbz0iJHtnfSI+PHNwYW4gc3R5"
    "bGU9ImNvbG9yOnZhcigtLXR4KSI+JHt0fTwvc3Bhbj48Yj4ke2ljKCJjaGV2Iil9PC9iPjwvZGl2"
    "PmApLmpvaW4oIiIpfQogICAgJHtHKCJpMThuIixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuIHN0eWxl"
    "PSJjb2xvcjp2YXIoLS10eCkiPkxhbmd1YWdlPC9zcGFuPjxiPgogICAgICA8c2VsZWN0IGNsYXNz"
    "PSJpbnAiIHN0eWxlPSJoZWlnaHQ6MzJweDt3aWR0aDphdXRvO2ZvbnQtc2l6ZTouNzRyZW0iPjxv"
    "cHRpb24+RW5nbGlzaDwvb3B0aW9uPjxvcHRpb24+4KS54KS/4KSo4KWN4KSm4KWAPC9vcHRpb24+"
    "PC9zZWxlY3Q+PC9iPjwvZGl2PmApfTwvZGl2PgogICR7Rygibm90aWZQcmVmcyIsYDxkaXYgY2xh"
    "c3M9InNlYyI+PGgyPk5vdGlmaWNhdGlvbnM8L2gyPjwvZGl2PgogIDxkaXYgY2xhc3M9ImNhcmQi"
    "PgogICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+UmVzdG9jayBhbGVydHM8c21h"
    "bGw+RE0gbWUgd2hlbiBteSBjb3VudHJ5IGlzIGJhY2s8L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2"
    "IGNsYXNzPSJ0ZyAke1BSRUZTLnJlc3RvY2s/Im9uIjoiIn0iIGRhdGEtcHJlZj0icmVzdG9jayI+"
    "PC9kaXY+PC9kaXY+CiAgICAke0coInJlY2VpcHRzIixgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBj"
    "bGFzcz0idCI+T3JkZXIgcmVjZWlwdHM8c21hbGw+U2VuZCBhIHJlY2VpcHQgYWZ0ZXIgZWFjaCBi"
    "dXk8L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyAke1BSRUZTLnJlY2VpcHRzPyJv"
    "biI6IiJ9IiBkYXRhLXByZWY9InJlY2VpcHRzIj48L2Rpdj48L2Rpdj5gKX0KICAgIDxkaXYgY2xh"
    "c3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPlByb21vIG1lc3NhZ2VzPHNtYWxsPkZsYXNoIHNhbGVz"
    "IGFuZCBnaXZlYXdheXM8L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyAke1BSRUZT"
    "LnByb21vcz8ib24iOiIifSIgZGF0YS1wcmVmPSJwcm9tb3MiPjwvZGl2PjwvZGl2PgogICAgJHtH"
    "KCJwdXNoTm90aWYiLGA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5NaW5pIGFwcCBw"
    "dXNoPHNtYWxsPk1lc3NhZ2UgbWUgd2hlbiBzb21ldGhpbmcgaGFwcGVuczwvc21hbGw+PC9kaXY+"
    "CiAgICAgIDxkaXYgY2xhc3M9InRnICR7UFJFRlMucHVzaD8ib24iOiIifSIgZGF0YS1wcmVmPSJw"
    "dXNoIj48L2Rpdj48L2Rpdj5gKX0KICA8L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0"
    "eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBpZD0ic3VwcG9y"
    "dGdvIj5TdXBwb3J0PC9idXR0b24+CiAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IiBpZD0idGVy"
    "bXNnbyI+VGVybXM8L2J1dHRvbj48L2Rpdj5gOwp9CgpmdW5jdGlvbiBiaW5kQnV5KCl7CiAgZG9j"
    "dW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtd2lzaF0iKS5mb3JFYWNoKGVsPT5lbC5vbmNs"
    "aWNrPWU9PnsKICAgIGUuc3RvcFByb3BhZ2F0aW9uKCk7Y29uc3QgaT0rZWwuZGF0YXNldC53aXNo"
    "OwogICAgY29uc3Qgaz1XSVNILmluZGV4T2YoaSk7aWYoazwwKVdJU0gucHVzaChpKTtlbHNlIFdJ"
    "U0guc3BsaWNlKGssMSk7CiAgICB0b2FzdChrPDA/IlNhdmVkIHRvIHdpc2hsaXN0IjoiUmVtb3Zl"
    "ZCBmcm9tIHdpc2hsaXN0Iik7YXBwbHlGaWx0ZXIoKTt9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCJbZGF0YS1idXldIikuZm9yRWFjaChlbD0+ZWwub25jbGljaz1lPT57CiAgICBpZihl"
    "LnRhcmdldC5jbG9zZXN0KCJbZGF0YS10b2FzdF0sW2RhdGEtd2lzaF0iKSlyZXR1cm47Y29uc3Qg"
    "cz1TVE9DS1srZWwuZGF0YXNldC5idXldOwogICAgaWYocy5xdHk9PT0wJiYhZmFsc2Upe3RvYXN0"
    "KCJBbGVydCBzZXQg4oCUIHlvdSdsbCBnZXQgYSBETSBvbiByZXN0b2NrIik7cmV0dXJufQogICAg"
    "YnV5U2hlZXQocyxzLnF0eT09PTApO30pOwp9CmxldCBRVFk9MSxDT1VQT049MDsKZnVuY3Rpb24g"
    "YnV5U2hlZXQocyxwcmUpewogIFFUWT0xO0NPVVBPTj0wOwogIGNvbnN0IG1heD1GRUFULmNhcnR8"
    "fEZFQVQuYnVsa0Rpc2M/TWF0aC5tYXgoMSxNYXRoLm1pbigyNSxzLnF0eXx8NSkpOjE7CiAgc2hl"
    "ZXQoYDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEycHg7"
    "bWFyZ2luLWJvdHRvbToxNHB4Ij4KICAgICAgPGRpdiBjbGFzcz0iZmxhZyIgc3R5bGU9IndpZHRo"
    "OjUwcHg7aGVpZ2h0OjUwcHg7Zm9udC1zaXplOjEuNXJlbSI+JHtzLmZ9PC9kaXY+CiAgICAgIDxk"
    "aXYgc3R5bGU9ImZsZXg6MSI+PGgzIHN0eWxlPSJtYXJnaW46MDtmb250LXNpemU6MXJlbSI+JHtl"
    "c2Mocy5jKX0gQWNjb3VudDwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46"
    "MCI+JHtzLmNvZGV9IMK3ICR7cy5xdHkrIiBpbiBzdG9jayDCtyBpbnN0YW50In08L3A+CiAgICAg"
    "ICR7RygicmV2aWV3cyIsYDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6NHB4Ij4ke3N0YXJzKHMuYyl9"
    "PC9kaXY+YCl9PC9kaXY+PC9kaXY+CiAgICAke0coImhlYWx0aENoZWNrIixgPGRpdiBjbGFzcz0i"
    "a3YiPjxzcGFuPlNlc3Npb24gaGVhbHRoPC9zcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS1vayki"
    "PiR7aWMoImNoZWNrIil9IGNoZWNrZWQgbGl2ZTwvYj48L2Rpdj5gKX0KICAgICR7RygidmFyaWFu"
    "dHMiLGA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VmFyaWFudDwvc3Bhbj48Yj4KICAgICAgPHNlbGVj"
    "dCBjbGFzcz0iaW5wIiBzdHlsZT0iaGVpZ2h0OjMycHg7d2lkdGg6YXV0bztmb250LXNpemU6Ljc0"
    "cmVtIj48b3B0aW9uPk5vIDJGQTwvb3B0aW9uPjxvcHRpb24+MkZBIG9uICgr4oK5NSk8L29wdGlv"
    "bj48b3B0aW9uPldpdGggbWFpbCAoK+KCuTgpPC9vcHRpb24+PC9zZWxlY3Q+PC9iPjwvZGl2PmAp"
    "fQogICAgJHsoRkVBVC5jYXJ0fHxGRUFULmJ1bGtEaXNjKT9gPGRpdiBjbGFzcz0ia3YiPjxzcGFu"
    "PlF1YW50aXR5PC9zcGFuPjxiPgogICAgICA8c3BhbiBjbGFzcz0icXR5Ij48YnV0dG9uIGlkPSJx"
    "bSI+4oiSPC9idXR0b24+PGIgaWQ9InFuIj4xPC9iPjxidXR0b24gaWQ9InFwIj4rPC9idXR0b24+"
    "PC9zcGFuPjwvYj48L2Rpdj5gOiIifQogICAgJHtHKCJidWxrRGlzYyIsYDxwIGNsYXNzPSJzdWIi"
    "IGlkPSJiZG5vdGUiIHN0eWxlPSJtYXJnaW46NnB4IDAgMDtmb250LXNpemU6LjdyZW0iPkJ1eSA1"
    "KyBmb3IgNSUgb2ZmIMK3IDEwKyBmb3IgMTAlIG9mZiDCtyAyNSsgZm9yIDE1JSBvZmYuPC9wPmAp"
    "fQogICAgJHtHKCJjb3Vwb25zIixgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9w"
    "OjEwcHgiPjxsYWJlbD5Db3Vwb24gY29kZTwvbGFiZWw+CiAgICAgIDxkaXYgc3R5bGU9ImRpc3Bs"
    "YXk6ZmxleDtnYXA6N3B4Ij48aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImNwbiIgcGxhY2Vob2xkZXI9"
    "IldFTENPTUUxMCI+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbSIgaWQ9ImNwbmFwcGx5IiBz"
    "dHlsZT0iZmxleDowIDAgYXV0byI+QXBwbHk8L2J1dHRvbj48L2Rpdj48L2Rpdj5gKX0KICAgIDxk"
    "aXYgY2xhc3M9Imt2Ij48c3Bhbj5QcmljZTwvc3Bhbj48YiBpZD0iYnByaWNlIj4ke21vbmV5KHMu"
    "cHJpY2UpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Zb3VyIGJhbGFuY2U8"
    "L3NwYW4+PGI+JHttb25leShCQUwpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bh"
    "bj5BZnRlciBwdXJjaGFzZTwvc3Bhbj48YiBpZD0iYmFmdGVyIj4ke21vbmV5KE1hdGgubWF4KDAs"
    "QkFMLXMucHJpY2UpKX08L2I+PC9kaXY+CiAgICA8ZGl2IGlkPSJzcGxpdG5vdGUiPjwvZGl2Pgog"
    "ICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4Ij48YnV0dG9uIGNs"
    "YXNzPSJidG4gZ2hvc3QiIGRhdGEtY2xvc2U+Q2FuY2VsPC9idXR0b24+CiAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBjdGEiIGlkPSJiY29uZmlybSI+Q29uZmlybSBCdXk8L2J1dHRvbj48L2Rpdj4K"
    "ICAgICR7RygiY2FydCIsYDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IGJsb2NrIHNtIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDo4cHgiIGlkPSJiYWRkY2FydCI+QWRkIHRvIGNhcnQ8L2J1dHRvbj5gKX1gKTsK"
    "ICBjb25zdCBnPWlkPT5kb2N1bWVudC5nZXRFbGVtZW50QnlJZChpZCk7CiAgZnVuY3Rpb24gdG90"
    "YWwoKXsKICAgIGxldCB1bml0PXMucHJpY2UsIHE9UVRZLCB0PXVuaXQqcTsKICAgIGlmKEZFQVQu"
    "YnVsa0Rpc2Mpe2NvbnN0IGQ9cT49MjU/LjE1OnE+PTEwPy4xMDpxPj01Py4wNTowO3Q9dCooMS1k"
    "KTt9CiAgICBpZihGRUFULmNvdXBvbnMmJkNPVVBPTil0PXQqKDEtQ09VUE9OLzEwMCk7CiAgICBy"
    "ZXR1cm4gTWF0aC5yb3VuZCh0KjEwMCkvMTAwOwogIH0KICBmdW5jdGlvbiBwYWludCgpewogICAg"
    "Y29uc3QgdD10b3RhbCgpOwogICAgZygiYnByaWNlIikudGV4dENvbnRlbnQ9bW9uZXkodCk7CiAg"
    "ICBnKCJiYWZ0ZXIiKS50ZXh0Q29udGVudD1tb25leShNYXRoLm1heCgwLEJBTC10KSk7CiAgICBj"
    "b25zdCBzbj1nKCJzcGxpdG5vdGUiKTsKICAgIGlmKEZFQVQuc3BsaXRQYXkmJnQ+QkFMKXtzbi5p"
    "bm5lckhUTUw9YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5TcGxpdCBwYXltZW50PC9zcGFuPjxiPiR7"
    "bW9uZXkoQkFMKX0gd2FsbGV0ICsgJHttb25leSh0LUJBTCl9IFVQSTwvYj48L2Rpdj5gO30KICAg"
    "IGVsc2Ugc24uaW5uZXJIVE1MPSIiOwogICAgaWYoZygicW4iKSlnKCJxbiIpLnRleHRDb250ZW50"
    "PVFUWTsKICB9CiAgaWYoZygicW0iKSlnKCJxbSIpLm9uY2xpY2s9KCk9PntRVFk9TWF0aC5tYXgo"
    "MSxRVFktMSk7cGFpbnQoKTt9OwogIGlmKGcoInFwIikpZygicXAiKS5vbmNsaWNrPSgpPT57UVRZ"
    "PU1hdGgubWluKG1heCxRVFkrMSk7cGFpbnQoKTt9OwogIGlmKGcoImNwbiIpJiZPRkZFUiYmIWco"
    "ImNwbiIpLnZhbHVlKWcoImNwbiIpLnZhbHVlPU9GRkVSOwogIGlmKGcoImNwbmFwcGx5IikpZygi"
    "Y3BuYXBwbHkiKS5vbmNsaWNrPSgpPT57CiAgICBjb25zdCB2PShnKCJjcG4iKS52YWx1ZXx8IiIp"
    "LnRyaW0oKS50b1VwcGVyQ2FzZSgpOwogICAgaWYoIXYpe3RvYXN0KCJFbnRlciBhIGNvdXBvbiBj"
    "b2RlIik7cmV0dXJufQogICAgQ09VUE9OPXY9PT0iV0VMQ09NRTEwIj8xMDp2PT09IlNBVkUyMCI/"
    "MjA6MDsKICAgIHRvYXN0KENPVVBPTj9DT1VQT04rIiUgY291cG9uIGFwcGxpZWQiOiJDb3Vwb24g"
    "bm90IHZhbGlkIik7cGFpbnQoKTt9OwogIGlmKGcoImJhZGRjYXJ0IikpZygiYmFkZGNhcnQiKS5v"
    "bmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKExJVkUpewogICAgICBjb25zdCBkPWF3YWl0IGFwaVRy"
    "eSgiL2FwaS9jYXJ0Iix7YWN0aW9uOiJhZGQiLGNvdW50cnk6cy5jLHF0eTpRVFl9KTsKICAgICAg"
    "aWYoIWQpcmV0dXJuOwogICAgICBjbG9zZVNoZWV0KCk7dG9hc3QoUVRZKyIgw5cgIitzLmMrIiBh"
    "ZGRlZCB0byBjYXJ0Iik7YXdhaXQgcmVmcmVzaCgpO3JldHVybjsKICAgIH0KICAgIGZvcihsZXQg"
    "aT0wO2k8UVRZO2krKylDQVJULnB1c2gocyk7YXBwbHlGZWF0dXJlcygpO2Nsb3NlU2hlZXQoKTt0"
    "b2FzdChRVFkrIiDDlyAiK3MuYysiIGFkZGVkIHRvIGNhcnQiKTt9OwogIGcoImJjb25maXJtIiku"
    "b25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCB0PXRvdGFsKCk7CiAgICBpZihCQUw8dCYmIUZF"
    "QVQuc3BsaXRQYXkpe3RvYXN0KCJOb3QgZW5vdWdoIGJhbGFuY2Ug4oCUIGFkZCBmdW5kcyIpO3Jl"
    "dHVybn0KICAgIGlmKEJBTDx0JiZGRUFULnNwbGl0UGF5KXtjbG9zZVNoZWV0KCk7dG9hc3QoIlBh"
    "eWluZyAiK21vbmV5KHQtQkFMKSsiIGJ5IFVQSSIpO2RlcG9zaXRTaGVldChNYXRoLmNlaWwodC1C"
    "QUwpKTtyZXR1cm59CiAgICBpZihMSVZFKXsKICAgICAgY29uc3QgYnRuPWcoImJjb25maXJtIik7"
    "YnRuLmRpc2FibGVkPXRydWU7YnRuLnRleHRDb250ZW50PSJXb3JraW5n4oCmIjsKICAgICAgY29u"
    "c3QgY29kZT1nKCJjcG4iKT8oZygiY3BuIikudmFsdWV8fCIiKS50cmltKCk6IiI7CiAgICAgIGNv"
    "bnN0IGQ9YXdhaXQgYXBpVHJ5KCIvYXBpL2J1eSIse2NvdW50cnk6cy5jLHF0eTpRVFksY291cG9u"
    "OmNvZGV9KTsKICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0iQ29uZmly"
    "bSBCdXkiOwogICAgICBpZighZClyZXR1cm47CiAgICAgIGlmKHR5cGVvZiBkLmJhbGFuY2U9PT0i"
    "bnVtYmVyIilCQUw9ZC5iYWxhbmNlOwogICAgICBzeW5jQmFsKCk7Y2xvc2VTaGVldCgpO2J1cnN0"
    "KCk7CiAgICAgIGNvbnN0IGZpcnN0PShkLm9yZGVycyYmZC5vcmRlcnNbMF0pfHxudWxsOwogICAg"
    "ICB0b2FzdChmaXJzdD8oIkRlbGl2ZXJlZCDigJQgIitmaXJzdC5pZCk6IlB1cmNoYXNlZCDigJQg"
    "ZGV0YWlscyBpbiBPcmRlcnMiKTsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgICBpZihmaXJz"
    "dCYmZmlyc3QucGF5bG9hZClzZXRUaW1lb3V0KCgpPT5zaGVldChgPGgzIHN0eWxlPSJtYXJnaW46"
    "MCAwIDRweDtmb250LXNpemU6MXJlbSI+JHtlc2MoZmlyc3QuaWQpfTwvaDM+CiAgICAgICAgPHAg"
    "Y2xhc3M9InN1YiI+S2VlcCB0aGlzIHNhZmUuIEl0IGlzIGFsc28gaW4gT3JkZXJzLjwvcD4KICAg"
    "ICAgICA8ZGl2IGNsYXNzPSJrdiIgc3R5bGU9ImRpc3BsYXk6YmxvY2siPjxjb2RlIHN0eWxlPSJm"
    "b250LXNpemU6LjcycmVtO3dvcmQtYnJlYWs6YnJlYWstYWxsIj4ke2VzYyhmaXJzdC5wYXlsb2Fk"
    "KX08L2NvZGU+PC9kaXY+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDoxMHB4IiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRvbj5gKSwzNTApOwogICAgICBy"
    "ZXR1cm47CiAgICB9CiAgICBCQUwtPXQ7c3luY0JhbCgpO2Nsb3NlU2hlZXQoKTtidXJzdCgpOwog"
    "ICAgdG9hc3QoIlB1cmNoYXNlZCDigJQgT1RQIGlzIGluIE9yZGVycyIpOwogICAgaWYoRkVBVC5w"
    "dXNoTm90aWYpc2V0VGltZW91dCgoKT0+dG9hc3QoIkJvdCBub3RpZmljYXRpb24gc2VudCB0byB5"
    "b3VyIGNoYXQiKSwxNTAwKTt9OwogIHBhaW50KCk7Cn0KZnVuY3Rpb24gY2FydFNoZWV0KCl7CiAg"
    "aWYoIUNBUlQubGVuZ3RoKXt0b2FzdCgiQ2FydCBpcyBlbXB0eSIpO3JldHVybn0KICBjb25zdCB0"
    "PUNBUlQucmVkdWNlKChhLGIpPT5hK2IucHJpY2UsMCk7CiAgc2hlZXQoYDxoMyBzdHlsZT0ibWFy"
    "Z2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPllvdXIgY2FydDwvaDM+CiAgICA8cCBjbGFzcz0i"
    "c3ViIj4ke0NBUlQubGVuZ3RofSBpdGVtJHtDQVJULmxlbmd0aD4xPyJzIjoiIn0g4oCUIG9uZSBj"
    "b25maXJtYXRpb24sIG9uZSBkZWJpdC48L3A+CiAgICAke0NBUlQubWFwKChzLGkpPT5gPGRpdiBj"
    "bGFzcz0ia3YiPjxzcGFuPiR7cy5mfSAke2VzYyhzLmMpfSAke3MuY29kZX08L3NwYW4+CiAgICAg"
    "IDxiPiR7bW9uZXkocy5wcmljZSl9IDxidXR0b24gY2xhc3M9ImJ0biB4cyBnaG9zdCIgZGF0YS1j"
    "ZGVsPSIke2l9Ij7DlzwvYnV0dG9uPjwvYj48L2Rpdj5gKS5qb2luKCIiKX0KICAgIDxkaXYgY2xh"
    "c3M9Imt2Ij48c3Bhbj5Ub3RhbDwvc3Bhbj48Yj4ke21vbmV5KHQpfTwvYj48L2Rpdj4KICAgIDxk"
    "aXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTRweCI+PGJ1dHRvbiBjbGFzcz0i"
    "YnRuIGdob3N0IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+CiAgICAgIDxidXR0b24gY2xhc3M9"
    "ImJ0biBjdGEiIGlkPSJjYXJ0YnV5Ij5DaGVja291dDwvYnV0dG9uPjwvZGl2PmApOwogIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNkZWxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9"
    "KCk9PntDQVJULnNwbGljZSgrYi5kYXRhc2V0LmNkZWwsMSk7YXBwbHlGZWF0dXJlcygpO2NhcnRT"
    "aGVldCgpO30pOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjYXJ0YnV5Iikub25jbGljaz0o"
    "KT0+ewogICAgaWYoQkFMPHQpe3RvYXN0KCJOb3QgZW5vdWdoIGJhbGFuY2Ug4oCUIGFkZCBmdW5k"
    "cyIpO3JldHVybn0KICAgIEJBTC09dDtDQVJUPVtdO3N5bmNCYWwoKTthcHBseUZlYXR1cmVzKCk7"
    "Y2xvc2VTaGVldCgpO2J1cnN0KCk7dG9hc3QoIk9yZGVyIHBsYWNlZCDigJQgT1RQcyBhcmUgaW4g"
    "T3JkZXJzIik7fTsKfQpmdW5jdGlvbiBnb1RhYih0KXsKICBjb25zdCBiPWRvY3VtZW50LnF1ZXJ5"
    "U2VsZWN0b3IoYCN0YWJzIGJ1dHRvbltkYXRhLXRhYj0iJHt0fSJdYCk7CiAgaWYoYiliLmNsaWNr"
    "KCk7Cn0KZnVuY3Rpb24gYmluZENvbW1vbigpewogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IltkYXRhLW9yZF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCB3"
    "aGF0PWIuZGF0YXNldC5vcmQ7CiAgICBjb25zdCBjYXJkPWIuY2xvc2VzdCgiW2RhdGEtb3JkZXIt"
    "aWRdIik7CiAgICBjb25zdCBvaWQ9KGNhcmQmJmNhcmQuZGF0YXNldC5vcmRlcklkKXx8KE9SREVS"
    "U1swXSYmT1JERVJTWzBdLmlkKXx8IiI7CiAgICBpZighb2lkKXt0b2FzdCgiTm8gb3JkZXIgc2Vs"
    "ZWN0ZWQiKTtyZXR1cm47fQogICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1"
    "cm47fQogICAgY29uc3QgbGJsPWIudGV4dENvbnRlbnQ7Yi5kaXNhYmxlZD10cnVlO2IudGV4dENv"
    "bnRlbnQ9IuKApiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRlciIse2lkOm9p"
    "ZCx3aGF0OndoYXR9KTsKICAgIGIuZGlzYWJsZWQ9ZmFsc2U7Yi50ZXh0Q29udGVudD1sYmw7CiAg"
    "ICBpZighcilyZXR1cm47CiAgICBpZih3aGF0PT09ImRldGFpbHMiKXRvYXN0KCJMb2dpbiBkZXRh"
    "aWxzIHNlbnQgdG8geW91ciBjaGF0Iik7CiAgICBpZih3aGF0PT09ImZpbGUiKXRvYXN0KCJTZXNz"
    "aW9uIGZpbGUgc2VudCB0byB5b3VyIGNoYXQiKTsKICAgIGlmKHdoYXQ9PT0icmVjZWlwdCIpdG9h"
    "c3QoIlJlY2VpcHQgc2VudCB0byB5b3VyIGNoYXQiKTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNl"
    "bGVjdG9yQWxsKCJbZGF0YS1yYXRlLWdvXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9"
    "PnsKICAgIGNvbnN0IHN0YXJzPStiLmRhdGFzZXQucmF0ZUdvOwogICAgY29uc3QgYz0od2luZG93"
    "Ll9fcmF0ZUNvdW50cnkpfHwoKE9SREVSU1swXXx8e30pLmMpfHwiIjsKICAgIGlmKCFMSVZFKXtj"
    "bG9zZVNoZWV0KCk7dG9hc3QoIlRoYW5rcyBmb3IgdGhlICIrc3RhcnMrIuKYhSIpO3JldHVybjt9"
    "CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9yZXZpZXciLHtjb3VudHJ5OmMscmF0aW5n"
    "OnN0YXJzfSk7CiAgICBpZighcilyZXR1cm47CiAgICBjbG9zZVNoZWV0KCk7dG9hc3QoIlRoYW5r"
    "cyDigJQgIitjKyIgaXMgbm93ICIrci5hdmcrIuKYhSIpOwogICAgYXdhaXQgcmVmcmVzaCgpOwog"
    "IH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWFjdF0iKS5mb3JFYWNoKGI9"
    "PmIub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCB3aGF0PWIuZGF0YXNldC5hY3Q7CiAgICBp"
    "Zih3aGF0PT09ImNoYW5uZWwiKXsKICAgICAgY29uc3QgYz0oQURNLmNoYW5uZWxzJiZBRE0uY2hh"
    "bm5lbHNbMF0pfHxudWxsOwogICAgICBjb25zdCBoPWM/U3RyaW5nKGMuaGFuZGxlfHwiIikucmVw"
    "bGFjZSgvXkAvLCIiKToiIjsKICAgICAgaWYoaClvcGVuUGF5KGguc3RhcnRzV2l0aCgiaHR0cCIp"
    "P2g6KCJodHRwczovL3QubWUvIitoKSk7CiAgICAgIGVsc2UgdG9hc3QoIk5vIGNoYW5uZWwgaGFz"
    "IGJlZW4gYWRkZWQgeWV0Iik7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9PT0iZmxh"
    "c2giKXsKICAgICAgY29uc3Qgb249QURNLm1hcmtldCYmQURNLm1hcmtldC5mbGFzaF9vbjsKICAg"
    "ICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkZsYXNo"
    "IHNhbGU8L2gzPgogICAgICAgIDxwIGNsYXNzPSJzdWIiPiR7b24/KCJFdmVyeSBwcmljZSBpcyAi"
    "KyhBRE0ubWFya2V0LmZsYXNofHwwKSsiJSBvZmYgcmlnaHQgbm93LiIpOiJObyBzYWxlIGlzIHJ1"
    "bm5pbmcgYXQgdGhlIG1vbWVudC4ifTwvcD4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxv"
    "Y2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTsK"
    "ICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJzb3J0Iil7CiAgICAgIEZJTFRFUkVE"
    "PShGSUxURVJFRCYmRklMVEVSRUQubGVuZ3RoP0ZJTFRFUkVEOlNUT0NLKS5zbGljZSgpLnNvcnQo"
    "KHgseSk9Pih5LnF0eXx8MCktKHgucXR5fHwwKSk7CiAgICAgIHJlbmRlcigpO3RvYXN0KCJTb3J0"
    "ZWQgYnkgd2hhdCBpcyBpbiBzdG9jayIpOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0"
    "PT09ImFsZXJ0cyIpewogICAgICBjb25zdCBtaW5lPSh3aW5kb3cuX19hbGVydHMpfHxbXTsKICAg"
    "ICAgY29uc3Qgb2ZmPVNUT0NLLmZpbHRlcih4PT4heC5xdHkpLm1hcCh4PT54LmMpOwogICAgICBj"
    "b25zdCBsaXN0PShvZmYubGVuZ3RoP29mZjpTVE9DSy5tYXAoeD0+eC5jKSkuc2xpY2UoMCw4KTsK"
    "ICAgICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPlJl"
    "c3RvY2sgYWxlcnRzPC9oMz4KICAgICAgICA8cCBjbGFzcz0ic3ViIj5XZSBtZXNzYWdlIHlvdSB0"
    "aGUgbW9tZW50IG9uZSBvZiB0aGVzZSBpcyBiYWNrLjwvcD4KICAgICAgICAke2xpc3QubWFwKGM9"
    "PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHtlc2MoYyl9PC9zcGFuPgogICAgICAgICAgPGI+PGJ1"
    "dHRvbiBjbGFzcz0iYnRuIHhzICR7bWluZS5pbmRleE9mKGMpPj0wPyIiOiJzb2Z0In0iIGRhdGEt"
    "YWxlcnQ9IiR7ZXNjKGMpfSI+CiAgICAgICAgICAgICR7bWluZS5pbmRleE9mKGMpPj0wPyJPbiI6"
    "Ik5vdGlmeSBtZSJ9PC9idXR0b24+PC9iPjwvZGl2PmApLmpvaW4oIiIpfQogICAgICAgIDxidXR0"
    "b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9zZT5E"
    "b25lPC9idXR0b24+YCk7CiAgICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWFs"
    "ZXJ0XSIpLmZvckVhY2goYT0+YS5vbmNsaWNrPWFzeW5jKCk9PnsKICAgICAgICBpZighTElWRSl7"
    "dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICAgICAgY29uc3Qgcj1hd2FpdCBhcGlU"
    "cnkoIi9hcGkvYWxlcnQiLHtjb3VudHJ5OmEuZGF0YXNldC5hbGVydH0pOwogICAgICAgIGlmKCFy"
    "KXJldHVybjsKICAgICAgICB3aW5kb3cuX19hbGVydHM9ci5hbGVydHN8fFtdOwogICAgICAgIGEu"
    "dGV4dENvbnRlbnQ9ci5vbj8iT24iOiJOb3RpZnkgbWUiOwogICAgICAgIGEuY2xhc3NMaXN0LnRv"
    "Z2dsZSgic29mdCIsIXIub24pOwogICAgICAgIHRvYXN0KHIub24/KCJXZSB3aWxsIHRlbGwgeW91"
    "IHdoZW4gIithLmRhdGFzZXQuYWxlcnQrIiBpcyBiYWNrIik6IkFsZXJ0IHJlbW92ZWQiKTsKICAg"
    "ICAgfSk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9PT0iY3N2Iil7CiAgICAgIGNv"
    "bnN0IHJvd3M9W1sib3JkZXIiLCJjb3VudHJ5IiwicHJpY2UiLCJzdGF0dXMiLCJ3aGVuIl1dCiAg"
    "ICAgICAgLmNvbmNhdChPUkRFUlMubWFwKG89PltvLmlkLG8uYyxvLnByaWNlLG8uc3Qsby53aGVu"
    "XSkpOwogICAgICBjb25zdCBjc3Y9cm93cy5tYXAocj0+ci5qb2luKCIsIikpLmpvaW4oIlxuIik7"
    "CiAgICAgIGNvbnN0IGE9ZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgiYSIpOwogICAgICBhLmhyZWY9"
    "ImRhdGE6dGV4dC9jc3Y7Y2hhcnNldD11dGYtOCwiK2VuY29kZVVSSUNvbXBvbmVudChjc3YpOwog"
    "ICAgICBhLmRvd25sb2FkPSJteS1vcmRlcnMuY3N2Ijtkb2N1bWVudC5ib2R5LmFwcGVuZENoaWxk"
    "KGEpO2EuY2xpY2soKTthLnJlbW92ZSgpOwogICAgICB0b2FzdChPUkRFUlMubGVuZ3RoKyIgb3Jk"
    "ZXJzIGRvd25sb2FkZWQiKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJzdXBw"
    "b3J0Iil7CiAgICAgIGNvbnN0IGg9KFNUT1JFLnN1cHBvcnR8fCIiKS5yZXBsYWNlKC9eQC8sIiIp"
    "OwogICAgICBpZihoKW9wZW5QYXkoImh0dHBzOi8vdC5tZS8iK2gpOwogICAgICBlbHNlIHRvYXN0"
    "KCJTZW5kIHlvdXIgcXVlc3Rpb24gaW4gdGhlIGNoYXQgYW5kIHdlIHdpbGwgcmVwbHkiKTsKICAg"
    "ICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJ0YXNrIil7CiAgICAgIGNvbnN0IHJvdz1i"
    "LmNsb3Nlc3QoIi5jYXJkIik7CiAgICAgIGNvbnN0IGxhYmVsPXJvdz8ocm93LnF1ZXJ5U2VsZWN0"
    "b3IoImIiKXx8e30pLnRleHRDb250ZW50fHwiIjoiIjsKICAgICAgaWYoL2NoYW5uZWwvaS50ZXN0"
    "KGxhYmVsKSl7Yi5ibHVyKCk7ZG9jdW1lbnQucXVlcnlTZWxlY3RvcignW2RhdGEtYWN0PSJjaGFu"
    "bmVsIl0nKT8uY2xpY2soKTtyZXR1cm47fQogICAgICBpZigvb3JkZXIvaS50ZXN0KGxhYmVsKSl7"
    "Z29UYWIoInNob3AiKTt0b2FzdCgiUGljayBhIGNvdW50cnkgdG8gcGxhY2UgeW91ciBmaXJzdCBv"
    "cmRlciIpO3JldHVybjt9CiAgICAgIGdvVGFiKCJlYXJuIik7dG9hc3QoIlNoYXJlIHlvdXIgaW52"
    "aXRlIGxpbmsgdG8gZmluaXNoIHRoaXMiKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hh"
    "dD09PSJzdGF0dXMiKXsKICAgICAgY29uc3QgZz0oQURNLnBheSYmQURNLnBheS5nbWFpbF9zZXQp"
    "OwogICAgICBjb25zdCBwZW5kPShBRE0ucGF5JiZBRE0ucGF5LnBlbmRpbmcpfHwwOwogICAgICBz"
    "aGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+UGF5bWVudCBj"
    "aGVja2luZzwvaDM+CiAgICAgICAgPHAgY2xhc3M9InN1YiI+JHtnPyJBdXRvbWF0aWMgY2hlY2tp"
    "bmcgaXMgb24uIFBheSwgdGhlbiB0YXAgVmVyaWZ5LiI6CiAgICAgICAgICAiQXV0b21hdGljIGNo"
    "ZWNraW5nIGlzIG5vdCBzZXQgdXAsIHNvIGFuIGFkbWluIGNvbmZpcm1zIHBheW1lbnRzIGJ5IGhh"
    "bmQuIn08L3A+CiAgICAgICAgJHtwZW5kP2A8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+V2FpdGluZyBm"
    "b3IgYW4gYWRtaW48L3NwYW4+PGI+JHtwZW5kfTwvYj48L2Rpdj5gOiIifQogICAgICAgIDxidXR0"
    "b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9zZT5D"
    "bG9zZTwvYnV0dG9uPmApOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09ImNvcHlj"
    "b2RlIil7CiAgICAgIGNvbnN0IGVsPWIuY2xvc2VzdCgiLmNhcmQsLnNoZWV0Ym9keSwjc2hlZXQi"
    "KXx8ZG9jdW1lbnQ7CiAgICAgIGNvbnN0IGNvZGU9KGVsLnF1ZXJ5U2VsZWN0b3IoImNvZGUiKXx8"
    "e30pLnRleHRDb250ZW50fHwiIjsKICAgICAgY29weVRleHQoY29kZS50cmltKCksIkNvZGUiKTsK"
    "ICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJyZXBsYWNlIil7CiAgICAgIGNvbnN0"
    "IGNhcmQ9Yi5jbG9zZXN0KCJbZGF0YS1vcmRlci1pZF0iKTsKICAgICAgY29uc3Qgb2lkPShjYXJk"
    "JiZjYXJkLmRhdGFzZXQub3JkZXJJZCl8fChPUkRFUlNbMF0mJk9SREVSU1swXS5pZCl8fCIiOwog"
    "ICAgICBpZighTElWRSl7Y2xvc2VTaGVldCgpO3RvYXN0KCJSZXBsYWNlbWVudCBzZW50Iik7cmV0"
    "dXJuO30KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvb3JkZXIiLHtpZDpvaWQsd2hh"
    "dDoicmVwbGFjZSJ9KTsKICAgICAgaWYoIXIpcmV0dXJuOwogICAgICBjbG9zZVNoZWV0KCk7CiAg"
    "ICAgIHRvYXN0KHIucmVmdW5kZWQ/Ik5vIHN0b2NrIGxlZnQg4oCUIHJlZnVuZGVkIHRvIHlvdXIg"
    "d2FsbGV0IjooIlJlcGxhY2VkIOKAlCAiK3Iub3JkZXIpKTsKICAgICAgYXdhaXQgcmVmcmVzaCgp"
    "OwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09ImNyeXB0byIpewogICAgICBpZigh"
    "TElWRSl7Y2xvc2VTaGVldCgpO3RvYXN0KCJXZSB3aWxsIGNvbmZpcm0geW91ciB0cmFuc2ZlciBz"
    "aG9ydGx5Iik7cmV0dXJuO30KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvY3J5cHRv"
    "Iix7cmVmOiIifSk7CiAgICAgIGlmKHIpe2Nsb3NlU2hlZXQoKTt0b2FzdCgiV2Ugd2lsbCBjb25m"
    "aXJtIHlvdXIgdHJhbnNmZXIgc2hvcnRseSIpO30KICAgICAgcmV0dXJuOwogICAgfQogIH0pOwog"
    "IGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNvcHldIikuZm9yRWFjaChiPT5iLm9u"
    "Y2xpY2s9KCk9PnsKICAgIGNvbnN0IHdoYXQ9Yi5kYXRhc2V0LmNvcHk7CiAgICBpZih3aGF0PT09"
    "InVwaSIpcmV0dXJuIGNvcHlUZXh0KFNUT1JFLnVwaSwiVVBJIElEIik7CiAgICBpZih3aGF0PT09"
    "InJlZiIpewogICAgICBsZXQgbD1FQVJOLmxpbmt8fCIiOwogICAgICBpZihsJiYhL15odHRwcz86"
    "Ly50ZXN0KGwpKWw9Imh0dHBzOi8vIitsOwogICAgICByZXR1cm4gY29weVRleHQobCwiSW52aXRl"
    "IGxpbmsiKTsKICAgIH0KICAgIHJldHVybiBjb3B5VGV4dChiLmRhdGFzZXQuY29weVZhbHVlfHxi"
    "LnRleHRDb250ZW50LnRyaW0oKSwiQ29waWVkIik7CiAgfSk7CiAgY29uc3QgcmQ9ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoInJlZGVlbWdvIik7CiAgaWYocmQpcmQub25jbGljaz1hc3luYygpPT57"
    "CiAgICBpZighTElWRSl7dG9hc3QoIlJlZGVlbWVkIik7cmV0dXJuO30KICAgIHJkLmRpc2FibGVk"
    "PXRydWU7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9yZWRlZW0iLHt9KTsKICAgIHJk"
    "LmRpc2FibGVkPWZhbHNlOwogICAgaWYoIXIpcmV0dXJuOwogICAgQkFMPXIuYmFsYW5jZTtzeW5j"
    "QmFsKCk7YnVyc3QoKTsKICAgIHRvYXN0KHIucG9pbnRzKyIgcG9pbnRzIOKGkiAiK21vbmV5KHIu"
    "YW1vdW50KSk7CiAgICBhd2FpdCByZWZyZXNoKCk7CiAgfTsKICBjb25zdCBvdT1kb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgib2ZmZXJ1c2UiKTsKICBpZihvdSlvdS5vbmNsaWNrPSgpPT57CiAgICBj"
    "b25zdCBjPShBRE0uY291cG9ucyYmQURNLmNvdXBvbnMubGVuZ3RoKT9BRE0uY291cG9uc1swXS5j"
    "b2RlOiIiOwogICAgaWYoYyl7T0ZGRVI9Yztjb3B5VGV4dChjLCJDb3Vwb24iKTt9CiAgICBlbHNl"
    "IHRvYXN0KCJObyBjb3Vwb24gaXMgcnVubmluZyByaWdodCBub3ciKTsKICB9OwogIGRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXByZWZdIikuZm9yRWFjaCh0PT50Lm9uY2xpY2s9YXN5"
    "bmMoKT0+ewogICAgY29uc3Qga2V5PXQuZGF0YXNldC5wcmVmLCBvbj0hdC5jbGFzc0xpc3QuY29u"
    "dGFpbnMoIm9uIik7CiAgICB0LmNsYXNzTGlzdC50b2dnbGUoIm9uIixvbik7UFJFRlNba2V5XT1v"
    "bjsKICAgIGlmKExJVkUpewogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9wcmVmcyIs"
    "e2tleTprZXksb246b259KTsKICAgICAgaWYoIXIpe3QuY2xhc3NMaXN0LnRvZ2dsZSgib24iLCFv"
    "bik7UFJFRlNba2V5XT0hb247cmV0dXJuO30KICAgIH0KICAgIHRvYXN0KG9uPyJUdXJuZWQgb24i"
    "OiJUdXJuZWQgb2ZmIik7CiAgfSk7CiAgY29uc3QgdGdiPWRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJ0ZXJtc2dvIik7CiAgaWYodGdiKXRnYi5vbmNsaWNrPSgpPT5zaGVldChgPGgzIHN0eWxlPSJt"
    "YXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+VGVybXMgJiByZWZ1bmRzPC9oMz4KICAgIDxw"
    "IGNsYXNzPSJzdWIiIHN0eWxlPSJ3aGl0ZS1zcGFjZTpwcmUtd3JhcCI+JHtlc2MoU1RPUkUudGVy"
    "bXN8fENNUy50ZXJtc3x8Ik5vIHRlcm1zIGhhdmUgYmVlbiB3cml0dGVuIHlldC4iKX08L3A+CiAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEt"
    "Y2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTsKICBjb25zdCBzZz1kb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgic3VwcG9ydGdvIik7CiAgaWYoc2cpc2cub25jbGljaz0oKT0+ewogICAgY29uc3QgaD0oU1RP"
    "UkUuc3VwcG9ydHx8IiIpLnJlcGxhY2UoL15ALywiIik7CiAgICBpZihoKW9wZW5QYXkoImh0dHBz"
    "Oi8vdC5tZS8iK2gpOwogICAgZWxzZSBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtm"
    "b250LXNpemU6MXJlbSI+U3VwcG9ydDwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiPlNlbmQgeW91"
    "ciBxdWVzdGlvbiBpbiB0aGUgY2hhdCBhbmQgd2Ugd2lsbCByZXBseS48L3A+CiAgICAgIDxidXR0"
    "b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9zZT5D"
    "bG9zZTwvYnV0dG9uPmApOwogIH07Cn0KZnVuY3Rpb24gYmluZFN0YWdlKCl7CiAgYmluZENvbW1v"
    "bigpOwogIGJpbmRCdXkoKTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1zY3Jv"
    "bGxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGIu"
    "ZGF0YXNldC5zY3JvbGwpLnNjcm9sbEludG9WaWV3KHtiZWhhdmlvcjoic21vb3RoIn0pKTsKICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1vdHBdIikuZm9yRWFjaChiPT5iLm9uY2xp"
    "Y2s9YXN5bmMoKT0+ewogICAgY29uc3QgY2FyZD1iLmNsb3Nlc3QoIltkYXRhLW9yZGVyLWlkXSIp"
    "OwogICAgY29uc3Qgb2lkPShjYXJkJiZjYXJkLmRhdGFzZXQub3JkZXJJZCl8fGIuZGF0YXNldC5v"
    "dHB8fCIiOwogICAgY29uc3Qgb3JkPU9SREVSUy5maW5kKHg9PnguaWQ9PT1vaWQpfHx7fTsKICAg"
    "IGlmKCFMSVZFKXsKICAgICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1z"
    "aXplOjFyZW0iPkxvZ2luIGNvZGU8L2gzPgogICAgICAgIDxwIGNsYXNzPSJzdWIiPlNhbXBsZSBj"
    "b2RlIOKAlCB0aGlzIGlzIHRoZSBwcmV2aWV3LjwvcD4KICAgICAgICA8ZGl2IGNsYXNzPSJvdHBi"
    "b3giPjQgMiA5IDEgNzwvZGl2PgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTBweCIgZGF0YS1jbG9zZT5Eb25lPC9idXR0b24+YCk7CiAgICAgIHJl"
    "dHVybjsKICAgIH0KICAgIGNvbnN0IGxibD1iLnRleHRDb250ZW50O2IuZGlzYWJsZWQ9dHJ1ZTti"
    "LnRleHRDb250ZW50PSJGZXRjaGluZ+KApiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2Fw"
    "aS9vcmRlciIse2lkOm9pZCx3aGF0OiJvdHAifSk7CiAgICBiLmRpc2FibGVkPWZhbHNlO2IudGV4"
    "dENvbnRlbnQ9bGJsOwogICAgaWYoIXIpcmV0dXJuOwogICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFy"
    "Z2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkxvZ2luIGNvZGU8L2gzPgogICAgICA8cCBjbGFz"
    "cz0ic3ViIj4ke29yZC5waG9uZT8oIkZvciAiK2VzYyhvcmQucGhvbmUpKToiRm9yIHRoaXMgYWNj"
    "b3VudCJ9PC9wPgogICAgICA8ZGl2IGNsYXNzPSJvdHBib3giPiR7U3RyaW5nKHIuY29kZSkuc3Bs"
    "aXQoIiIpLmpvaW4oIiAiKX08L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkNvZGVz"
    "IGxlZnQ8L3NwYW4+PGI+JHtyLmxlZnR9PC9iPjwvZGl2PgogICAgICAke3Iud2luZG93P2A8ZGl2"
    "IGNsYXNzPSJrdiI+PHNwYW4+Rm9yd2FyZGluZyBlbmRzIGluPC9zcGFuPjxiPiR7ci53aW5kb3d9"
    "IG1pbjwvYj48L2Rpdj5gOiIifQogICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEwcHgiPgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLWFjdD0i"
    "Y29weWNvZGUiPkNvcHkgQ29kZTwvYnV0dG9uPgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biIg"
    "ZGF0YS1jbG9zZT5Eb25lPC9idXR0b24+PC9kaXY+YCk7CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlT"
    "ZWxlY3RvckFsbCgiW2RhdGEtY2xhaW1dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+"
    "ewogICAgaWYoYi5kaXNhYmxlZClyZXR1cm47CiAgICBiLmRpc2FibGVkPXRydWU7CiAgICBpZihM"
    "SVZFKXsKICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvY2hlY2tpbiIse30pOwogICAg"
    "ICBpZighcil7Yi5kaXNhYmxlZD1mYWxzZTtyZXR1cm47fQogICAgICBCQUw9dHlwZW9mIHIuYmFs"
    "YW5jZT09PSJudW1iZXIiP3IuYmFsYW5jZTpCQUw7CiAgICAgIEVBUk4uY2xhaW1lZD10cnVlO0VB"
    "Uk4uc3RyZWFrPXIuc3RyZWFrfHxFQVJOLnN0cmVhazsKICAgICAgc3luY0JhbCgpO2J1cnN0KCk7"
    "dG9hc3QobW9uZXkoci5hbW91bnQpKyIgYWRkZWQgwrcgZGF5ICIrci5zdHJlYWspOwogICAgICBi"
    "LnRleHRDb250ZW50PSJDbGFpbWVkIHRvZGF5IjsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAg"
    "ICByZXR1cm47CiAgICB9CiAgICBCQUwrPUVBUk4uZGFpbHk7c3luY0JhbCgpO2J1cnN0KCk7dG9h"
    "c3QobW9uZXkoRUFSTi5kYWlseSkrIiBhZGRlZCIpOwogICAgYi50ZXh0Q29udGVudD0iQ2xhaW1l"
    "ZCB0b2RheSI7fSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtYW10XSIpLmZv"
    "ckVhY2goYj0+Yi5vbmNsaWNrPSgpPT5kZXBvc2l0U2hlZXQoYi5kYXRhc2V0LmFtdCkpOwogIGRv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWRlcF0iKS5mb3JFYWNoKGI9PmIub25jbGlj"
    "az0oKT0+YW1vdW50U2hlZXQoKSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEt"
    "dGddIikuZm9yRWFjaCh0PT50Lm9uY2xpY2s9KCk9PnQuY2xhc3NMaXN0LnRvZ2dsZSgib24iKSk7"
    "CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtc2V0c2VsXSIpLmZvckVhY2goc2Vs"
    "PT5zZWwub25jaGFuZ2U9YXN5bmMoKT0+ewogICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9u"
    "bHkiKTtyZXR1cm47fQogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc2V0dGlu"
    "ZyIse2tleTpzZWwuZGF0YXNldC5zZXRzZWwsdmFsdWU6c2VsLnZhbHVlfSk7CiAgICBpZihyKXRv"
    "YXN0KCJTYXZlZCIpOwogIH0pOwogIGNvbnN0IHE9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInEi"
    "KTsKICBpZihxKXtxLnZhbHVlPVFVRVJZO3Eub25pbnB1dD0oKT0+e1FVRVJZPXEudmFsdWUudG9M"
    "b3dlckNhc2UoKTthcHBseUZpbHRlcigpfTt9CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgi"
    "I2NoaXBzIGJ1dHRvbiIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57Q0hJUD1iLmRhdGFzZXQu"
    "ZmlsdGVyOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI2NoaXBzIGJ1dHRvbiIpLmZv"
    "ckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseD09PWIpKTthcHBseUZpbHRlcigpfSk7"
    "CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2xhaW0tb3JkZXJdIikuZm9yRWFj"
    "aChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IG9pZD1iLmRhdGFzZXQuY2xhaW1PcmRlcjsK"
    "ICAgIGNvbnN0IG9yZD1PUkRFUlMuZmluZCh4PT54LmlkPT09b2lkKXx8e307CiAgICBpZihvcmQu"
    "Y2xhaW0pe3RvYXN0KCJBIGNsYWltIGZvciB0aGlzIG9yZGVyIGlzIGFscmVhZHkgd2l0aCBhbiBh"
    "ZG1pbiIpO3JldHVybjt9CiAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250"
    "LXNpemU6MXJlbSI+UmVwb3J0IGEgcHJvYmxlbTwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiPkFu"
    "IGFkbWluIHJlYWRzIGV2ZXJ5IHJlcG9ydCBhbmQgZGVjaWRlcy4gTm90aGluZyBpcyByZWZ1bmRl"
    "ZCBhdXRvbWF0aWNhbGx5LjwvcD4KICAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEycHgiPjxsYWJlbD5XaGF0IGhhcHBlbmVkPzwvbGFiZWw+CiAgICAgICAgPHNlbGVj"
    "dCBjbGFzcz0iaW5wIiBpZD0iY2xyIj4KICAgICAgICAgIDxvcHRpb24+TG9naW4gbm90IHdvcmtp"
    "bmc8L29wdGlvbj48b3B0aW9uPkFjY291bnQgYmFubmVkPC9vcHRpb24+CiAgICAgICAgICA8b3B0"
    "aW9uPkNvZGUgbmV2ZXIgYXJyaXZlZDwvb3B0aW9uPjxvcHRpb24+V3JvbmcgY291bnRyeTwvb3B0"
    "aW9uPgogICAgICAgICAgPG9wdGlvbj5Tb21ldGhpbmcgZWxzZTwvb3B0aW9uPjwvc2VsZWN0Pjwv"
    "ZGl2PgogICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkRldGFpbHM8L2xhYmVsPgogICAg"
    "ICAgIDx0ZXh0YXJlYSBjbGFzcz0iaW5wIiBpZD0iY2xkIiByb3dzPSIzIiBwbGFjZWhvbGRlcj0i"
    "VGVsbCB1cyBleGFjdGx5IHdoYXQgeW91IHNhdyI+PC90ZXh0YXJlYT48L2Rpdj4KICAgICAgPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIGN0YSBibG9jayIgaWQ9ImNsZ28iPlNlbmQgdG8gYW4gYWRtaW48L2J1"
    "dHRvbj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJsb2NrIHNtIiBzdHlsZT0ibWFy"
    "Z2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+Q2FuY2VsPC9idXR0b24+YCk7CiAgICBkb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgiY2xnbyIpLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25zdCByZWFz"
    "b249ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNsciIpLnZhbHVlOwogICAgICBjb25zdCBkZXRh"
    "aWxzPShkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2xkIikudmFsdWV8fCIiKS50cmltKCk7CiAg"
    "ICAgIGlmKGRldGFpbHMubGVuZ3RoPDUpe3RvYXN0KCJUZWxsIHVzIHdoYXQgd2VudCB3cm9uZyIp"
    "O3JldHVybjt9CiAgICAgIGlmKCFMSVZFKXtjbG9zZVNoZWV0KCk7dG9hc3QoIlNlbnQiKTtyZXR1"
    "cm47fQogICAgICBjb25zdCBidG49ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNsZ28iKTtidG4u"
    "ZGlzYWJsZWQ9dHJ1ZTtidG4udGV4dENvbnRlbnQ9IlNlbmRpbmfigKYiOwogICAgICBjb25zdCBy"
    "PWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRlciIse2lkOm9pZCx3aGF0OiJjbGFpbSIscmVhc29uOnJl"
    "YXNvbixkZXRhaWxzOmRldGFpbHN9KTsKICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50ZXh0"
    "Q29udGVudD0iU2VuZCB0byBhbiBhZG1pbiI7CiAgICAgIGlmKCFyKXJldHVybjsKICAgICAgY2xv"
    "c2VTaGVldCgpO3RvYXN0KCJDbGFpbSAjIityLmlkKyIgc2VudCDigJQgYW4gYWRtaW4gd2lsbCBy"
    "ZXBseSIpOwogICAgICBhd2FpdCByZWZyZXNoKCk7CiAgICB9OwogIH0pOwogIGRvY3VtZW50LnF1"
    "ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWxvZ2dlZGluXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFz"
    "eW5jKCk9PnsKICAgIGNvbnN0IG9pZD1iLmRhdGFzZXQubG9nZ2VkaW47CiAgICBpZighTElWRSl7"
    "dG9hc3QoIk1hcmtlZCBhcyBsb2dnZWQgaW4iKTtyZXR1cm47fQogICAgc2hlZXQoYDxoMyBzdHls"
    "ZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkNvbmZpcm0geW91IGFyZSBpbjwvaDM+"
    "CiAgICAgIDxwIGNsYXNzPSJzdWIiPldlIHdpbGwgZGVsZXRlIG91ciBjb3B5IG9mIHRoZSBsb2dp"
    "biBzZXNzaW9uIHN0cmFpZ2h0IGF3YXksIHNvIHRoZQogICAgICBhY2NvdW50IGJlY29tZXMgeW91"
    "cnMgYWxvbmUuIENvZGVzIHdpbGwgc3RvcCBiZWluZyBmb3J3YXJkZWQuPC9wPgogICAgICA8YnV0"
    "dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBpZD0ibGlvayIgc3R5bGU9Im1hcmdpbi10b3A6MTJw"
    "eCI+WWVzLCBJIGFtIGxvZ2dlZCBpbjwvYnV0dG9uPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "Z2hvc3QgYmxvY2sgc20iIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgZGF0YS1jbG9zZT5Ob3QgeWV0"
    "PC9idXR0b24+YCk7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgibGlvayIpLm9uY2xpY2s9"
    "YXN5bmMoKT0+ewogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRlciIse2lkOm9p"
    "ZCx3aGF0OiJsb2dnZWRpbiJ9KTsKICAgICAgaWYoIXIpcmV0dXJuOwogICAgICBjbG9zZVNoZWV0"
    "KCk7dG9hc3QoIkRvbmUg4oCUIHRoZSBhY2NvdW50IGlzIHlvdXJzIGFsb25lIG5vdyIpOwogICAg"
    "ICBhd2FpdCByZWZyZXNoKCk7CiAgICB9OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JB"
    "bGwoIltkYXRhLWRpc3B1dGVdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0"
    "IG9yZD1iLmRhdGFzZXQuZGlzcHV0ZXx8IiI7CiAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46"
    "MCAwIDRweDtmb250LXNpemU6MXJlbSI+T3BlbiBhIGRpc3B1dGU8L2gzPgogICAgIDxwIGNsYXNz"
    "PSJzdWIiPiR7b3JkPygiT3JkZXIgIitlc2Mob3JkKSk6IkFuIGFkbWluIHJldmlld3MgZXZlcnkg"
    "ZGlzcHV0ZS4ifTwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTJweCI+PGxhYmVsPlJlYXNvbjwvbGFiZWw+CiAgICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIGlk"
    "PSJkc3IiPjxvcHRpb24+Tm90IGRlbGl2ZXJlZDwvb3B0aW9uPjxvcHRpb24+QWNjb3VudCBkZWFk"
    "IG9uIGFycml2YWw8L29wdGlvbj48b3B0aW9uPkNoYXJnZWQgdHdpY2U8L29wdGlvbj48b3B0aW9u"
    "Pk90aGVyPC9vcHRpb24+PC9zZWxlY3Q+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxs"
    "YWJlbD5XaGF0IGhhcHBlbmVkPzwvbGFiZWw+CiAgICAgICA8dGV4dGFyZWEgY2xhc3M9ImlucCIg"
    "aWQ9ImRzZCIgcm93cz0iMyIgcGxhY2Vob2xkZXI9IkRlc2NyaWJlIHRoZSBwcm9ibGVtIj48L3Rl"
    "eHRhcmVhPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RXZpZGVuY2UgKG9w"
    "dGlvbmFsKTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImRzZiIgdHlwZT0i"
    "ZmlsZSIgYWNjZXB0PSJpbWFnZS8qIj4KICAgICAgIDxzbWFsbCBjbGFzcz0ic3ViIiBpZD0iZHNm"
    "biI+PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciPjxidXR0b24gY2xhc3M9"
    "ImJ0biBnaG9zdCIgZGF0YS1jbG9zZT5DYW5jZWw8L2J1dHRvbj4KICAgICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBjdGEiIGlkPSJkc2dvIj5TdWJtaXQ8L2J1dHRvbj48L2Rpdj5gKTsKICAgIGNvbnN0"
    "IGY9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRzZiIpOwogICAgaWYoZilmLm9uY2hhbmdlPSgp"
    "PT57Y29uc3Qgbj0oZi5maWxlcyYmZi5maWxlc1swXSk/Zi5maWxlc1swXS5uYW1lOiIiOwogICAg"
    "ICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZHNmbiIpLnRleHRDb250ZW50PW4/KCJBdHRhY2hl"
    "ZDogIituKToiIjt9OwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRzZ28iKS5vbmNsaWNr"
    "PWFzeW5jKCk9PnsKICAgICAgY29uc3QgcmVhc29uPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJk"
    "c3IiKS52YWx1ZTsKICAgICAgY29uc3QgZGV0YWlscz0oZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "ImRzZCIpLnZhbHVlfHwiIikudHJpbSgpOwogICAgICBpZighZGV0YWlscyl7dG9hc3QoIkRlc2Ny"
    "aWJlIHdoYXQgaGFwcGVuZWQiKTtyZXR1cm47fQogICAgICBjb25zdCBmaWxlPShmJiZmLmZpbGVz"
    "JiZmLmZpbGVzWzBdKXx8bnVsbDsKICAgICAgY29uc3QgZm49ZmlsZT8oInNjcmVlbnNob3Q6ICIr"
    "ZmlsZS5uYW1lKToiIjsKICAgICAgaWYoIUxJVkUpe2Nsb3NlU2hlZXQoKTt0b2FzdCgiRGlzcHV0"
    "ZSBvcGVuZWQiKTtyZXR1cm47fQogICAgICBjb25zdCBidG49ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5"
    "SWQoImRzZ28iKTtidG4uZGlzYWJsZWQ9dHJ1ZTtidG4udGV4dENvbnRlbnQ9IlNlbmRpbmfigKYi"
    "OwogICAgICBsZXQgc2hvdD0iIjsKICAgICAgaWYoZmlsZSl7CiAgICAgICAgaWYoZmlsZS5zaXpl"
    "PjgqMTAyNCoxMDI0KXt0b2FzdCgiVGhhdCBpbWFnZSBpcyB0b28gbGFyZ2UgKDggTUIgbWF4KSIp"
    "OwogICAgICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0iU3VibWl0Ijty"
    "ZXR1cm47fQogICAgICAgIHNob3Q9YXdhaXQgbmV3IFByb21pc2UocmVzPT57CiAgICAgICAgICBj"
    "b25zdCByZD1uZXcgRmlsZVJlYWRlcigpOwogICAgICAgICAgcmQub25sb2FkPSgpPT5yZXMoU3Ry"
    "aW5nKHJkLnJlc3VsdHx8IiIpKTsKICAgICAgICAgIHJkLm9uZXJyb3I9KCk9PnJlcygiIik7CiAg"
    "ICAgICAgICByZC5yZWFkQXNEYXRhVVJMKGZpbGUpOwogICAgICAgIH0pOwogICAgICB9CiAgICAg"
    "IGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2Rpc3B1dGUiLHtvcmRlcjpvcmQscmVhc29uOnJl"
    "YXNvbixkZXRhaWxzOmRldGFpbHMsCiAgICAgICAgZXZpZGVuY2U6Zm4sc2hvdDpzaG90fSk7CiAg"
    "ICAgIGJ0bi5kaXNhYmxlZD1mYWxzZTtidG4udGV4dENvbnRlbnQ9IlN1Ym1pdCI7CiAgICAgIGlm"
    "KCFyKXJldHVybjsKICAgICAgY2xvc2VTaGVldCgpO3RvYXN0KCJEaXNwdXRlICMiK3IuaWQrIiBz"
    "ZW50IHRvIHRoZSBhZG1pbnMiKTsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgfTsKICB9KTsK"
    "ICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1yYXRlXSIpLmZvckVhY2goYj0+Yi5v"
    "bmNsaWNrPSgpPT57d2luZG93Ll9fcmF0ZUNvdW50cnk9Yi5kYXRhc2V0LnJhdGU7cmV0dXJuIHNo"
    "ZWV0KAogICAgYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPlJhdGUg"
    "JHtiLmRhdGFzZXQucmF0ZX08L2gzPgogICAgIDxwIGNsYXNzPSJzdWIiPllvdXIgcmF0aW5nIHNo"
    "b3dzIG9uIHRoZSBjb3VudHJ5IGluIHRoZSBzaG9wLjwvcD4KICAgICA8ZGl2IHN0eWxlPSJkaXNw"
    "bGF5OmZsZXg7Z2FwOjlweDtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO21hcmdpbjoxNnB4IDAiPgog"
    "ICAgICAgJHtbMSwyLDMsNCw1XS5tYXAobj0+YDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBk"
    "YXRhLXJhdGUtZ289IiR7bn0iPiR7bn3imIU8L2J1dHRvbj5gKS5qb2luKCIiKX08L2Rpdj4KICAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QgYmxvY2sgc20iIGRhdGEtY2xvc2U+Q2xvc2U8L2J1"
    "dHRvbj5gKTt9KTsgCiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY3J5cHRvXSIp"
    "LmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT5zaGVldCgKICAgIGA8aDMgc3R5bGU9Im1hcmdpbjow"
    "IDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5DcnlwdG8gdG9wLXVwPC9oMz4KICAgICA8cCBjbGFzcz0i"
    "c3ViIj5TZW5kIFVTRFQtVFJDMjAg4oCUIGNyZWRpdGVkIGFmdGVyIDEgbmV0d29yayBjb25maXJt"
    "YXRpb24uPC9wPgogICAgIDxkaXYgY2xhc3M9InFyIj5RUiBDT0RFPGJyPih3YWxsZXQgYWRkcmVz"
    "cyk8L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+TmV0d29yazwvc3Bhbj48Yj5UUkMt"
    "MjA8L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkFkZHJlc3M8L3NwYW4+PGIg"
    "c3R5bGU9ImZvbnQtc2l6ZTouN3JlbSI+bm90LWNvbmZpZ3VyZWQ8L2I+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPlJhdGU8L3NwYW4+PGI+MSBVU0RUID0g4oK5ODguNDA8L2I+PC9k"
    "aXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48YnV0"
    "dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj4KICAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBjdGEiIGRhdGEtYWN0PSJjcnlwdG8iPkkndmUgU2VudCBJdDwvYnV0"
    "dG9uPjwvZGl2PmApKTsKICBjb25zdCB0Zz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgidG91cmdv"
    "IiksdHM9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInRvdXJza2lwIik7CiAgaWYodGcpdGcub25j"
    "bGljaz0oKT0+c2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0i"
    "PkhvdyBpdCB3b3JrczwvaDM+CiAgICAgPGRpdiBjbGFzcz0ic3RlcHMiIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjEycHgiPgogICAgICAgPGRpdiBjbGFzcz0ic3RlcCBvbiI+PGk+MTwvaT5Ub3AgdXAgeW91"
    "ciB3YWxsZXQgYnkgVVBJPC9kaXY+CiAgICAgICA8ZGl2IGNsYXNzPSJzdGVwIG9uIj48aT4yPC9p"
    "PlBpY2sgYSBjb3VudHJ5IGFuZCBidXkgaW5zdGFudGx5PC9kaXY+CiAgICAgICA8ZGl2IGNsYXNz"
    "PSJzdGVwIG9uIj48aT4zPC9pPlJlYWQgdGhlIGxvZ2luIGNvZGUgcmlnaHQgaGVyZSBpbiBPcmRl"
    "cnM8L2Rpdj48L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDoxMnB4IiBkYXRhLWNsb3NlPkdvdCBpdDwvYnV0dG9uPmApOwogIGlmKHRzKXRz"
    "Lm9uY2xpY2s9KCk9PntGRUFULm9uYm9hcmRpbmc9ZmFsc2U7cmVuZGVyKCk7dG9hc3QoIlRvdXIg"
    "aGlkZGVuIik7fTsKICBzdGFydENvdW50ZG93bigpOwogIGJpbmRIb2xkKCk7Cn0KZnVuY3Rpb24g"
    "YXBwbHlGaWx0ZXIoKXsKICBGSUxURVJFRD1TVE9DSy5maWx0ZXIocz0+ewogICAgY29uc3QgdD0o"
    "cy5jKyIgIitzLmNvZGUpLnRvTG93ZXJDYXNlKCk7CiAgICBpZihRVUVSWSYmIXQuaW5jbHVkZXMo"
    "UVVFUlkpKXJldHVybiBmYWxzZTsKICAgIGlmKENISVA9PT0ic3RvY2siJiZzLnF0eT09PTApcmV0"
    "dXJuIGZhbHNlOwogICAgaWYoQ0hJUD09PSJkZWFsIiYmIXMud2FzKXJldHVybiBmYWxzZTsKICAg"
    "IGlmKENISVA9PT0iY2hlYXAiJiZzLnByaWNlPj0zNSlyZXR1cm4gZmFsc2U7CiAgICBpZihDSElQ"
    "PT09InYyZmEiJiZzLnF0eT09PTApcmV0dXJuIGZhbHNlOwogICAgaWYoQ0hJUD09PSJ2bWFpbCIm"
    "JiFzLmhvdClyZXR1cm4gZmFsc2U7CiAgICBpZihDSElQPT09InZzcGFtIiYmcy5wcmljZTwzMCly"
    "ZXR1cm4gZmFsc2U7CiAgICByZXR1cm4gdHJ1ZTt9KTsKICBjb25zdCBiPWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJzaG9wYm9keSIpOwogIGlmKGIpe2IuaW5uZXJIVE1MPXNob3BCb2R5KCk7Ymlu"
    "ZEJ1eSgpO30KfQpsZXQgY2RUPW51bGw7CmZ1bmN0aW9uIHN0YXJ0Q291bnRkb3duKCl7CiAgY29u"
    "c3QgZWw9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNkIik7Y2xlYXJJbnRlcnZhbChjZFQpO2lm"
    "KCFlbClyZXR1cm47CiAgbGV0IHQ9MTQqNjArNTI7CiAgY2RUPXNldEludGVydmFsKCgpPT57aWYo"
    "IWRvY3VtZW50LmJvZHkuY29udGFpbnMoZWwpKXtjbGVhckludGVydmFsKGNkVCk7cmV0dXJufQog"
    "ICAgdD10PjA/dC0xOjE0KjYwKzUyO2VsLnRleHRDb250ZW50PVN0cmluZyhNYXRoLmZsb29yKHQv"
    "NjApKS5wYWRTdGFydCgyLCIwIikrIjoiK1N0cmluZyh0JTYwKS5wYWRTdGFydCgyLCIwIil9LDEw"
    "MDApOwp9Ci8qIDA1IMK3IGhvbGQtdG8tY29uZmlybSAqLwpmdW5jdGlvbiBiaW5kSG9sZCgpewog"
    "IGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5ob2xkIikuZm9yRWFjaChiPT57CiAgICBpZihi"
    "Ll9oKXJldHVybjtiLl9oPTE7Y29uc3QgZmlsbD1iLnF1ZXJ5U2VsZWN0b3IoIi5maWxsIik7bGV0"
    "IGl2PW51bGwscD0wOwogICAgY29uc3Qgc3RvcD0oKT0+e2NsZWFySW50ZXJ2YWwoaXYpO3A9MDtp"
    "ZihmaWxsKWZpbGwuc3R5bGUud2lkdGg9IjAlIn07CiAgICBjb25zdCBnbz1lPT57ZS5wcmV2ZW50"
    "RGVmYXVsdCgpO2NsZWFySW50ZXJ2YWwoaXYpO2l2PXNldEludGVydmFsKCgpPT57cCs9NDtpZihm"
    "aWxsKWZpbGwuc3R5bGUud2lkdGg9cCsiJSI7CiAgICAgIGlmKHA+PTEwMCl7c3RvcCgpO2IuY2xh"
    "c3NMaXN0LmFkZCgiZG9uZSIpO3NldFRpbWVvdXQoKCk9PmIuY2xhc3NMaXN0LnJlbW92ZSgiZG9u"
    "ZSIpLDQ1MCk7YnVyc3QoKTt0b2FzdChiLmRhdGFzZXQuZG9uZXx8IkNvbmZpcm1lZCIpfX0sMjgp"
    "fTsKICAgIGIuYWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcmRvd24iLGdvKTtbInBvaW50ZXJ1cCIs"
    "InBvaW50ZXJsZWF2ZSIsInBvaW50ZXJjYW5jZWwiXS5mb3JFYWNoKGV2PT5iLmFkZEV2ZW50TGlz"
    "dGVuZXIoZXYsc3RvcCkpO30pOwp9Ci8qIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kCBLSVQgUlVOVElNRSDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAgKi8KY29uc3Qg"
    "Uk09bWF0Y2hNZWRpYSgiKHByZWZlcnMtcmVkdWNlZC1tb3Rpb246cmVkdWNlKSIpLm1hdGNoZXM7"
    "Ci8qIHNwcmluZyBpbnRlZ3JhdG9yIOKAlCBraXQgdXNlcyBtYXNzIC4xIC8gc3RpZmZuZXNzIDE1"
    "MCAvIGRhbXBpbmcgMTIgKi8KZnVuY3Rpb24gU3ByaW5nKHYse209LjEsaz0xNTAsZD0xMn09e30p"
    "e3RoaXMudj12O3RoaXMudD12O3RoaXMudmVsPTA7dGhpcy5tPW07dGhpcy5rPWs7dGhpcy5kPWQ7"
    "fQpTcHJpbmcucHJvdG90eXBlLnN0ZXA9ZnVuY3Rpb24oZHQpe2NvbnN0IEY9LXRoaXMuayoodGhp"
    "cy52LXRoaXMudCktdGhpcy5kKnRoaXMudmVsOwogIHRoaXMudmVsKz0oRi90aGlzLm0pKmR0O3Ro"
    "aXMudis9dGhpcy52ZWwqZHQ7cmV0dXJuIHRoaXMudjt9OwoKLyogMDIgKyAyNSDCtyBhbmltYXRl"
    "ZCBkb2NrOiBkaXN0YW5jZeKGknNjYWxlIHdpdGggc3ByaW5nLCBwbHVzIHNsaWRpbmcgcGlsbCAq"
    "LwooZnVuY3Rpb24gZG9jaygpewogIGNvbnN0IGRrPWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoIm5h"
    "diN0YWJzIC5kb2NrIik7aWYoIWRrKXJldHVybjsKICBjb25zdCBidG5zPVsuLi5kay5xdWVyeVNl"
    "bGVjdG9yQWxsKCJidXR0b24iKV0scGlsbD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZHBpbGwi"
    "KTsKICBjb25zdCBGSU5FPW1hdGNoTWVkaWEoIihob3Zlcjpob3ZlcikgYW5kIChwb2ludGVyOmZp"
    "bmUpIikubWF0Y2hlcyYmIVJNOwogIGNvbnN0IHNwPWJ0bnMubWFwKCgpPT5uZXcgU3ByaW5nKDEp"
    "KSxwcz17eDpuZXcgU3ByaW5nKDAse2s6MjYwLGQ6MTF9KSx3Om5ldyBTcHJpbmcoMCx7azoyNjAs"
    "ZDoxMX0pfTsKICBsZXQgbXg9SW5maW5pdHksbGFzdD1wZXJmb3JtYW5jZS5ub3coKSxwbGFjZWQ9"
    "ZmFsc2U7CiAgZnVuY3Rpb24gcmVzZXRTY2FsZSgpe214PUluZmluaXR5O2lmKCFGSU5FKWJ0bnMu"
    "Zm9yRWFjaChiPT5iLnN0eWxlLnRyYW5zZm9ybT0iIik7fQogIGlmKEZJTkUpewogICAgZGsuYWRk"
    "RXZlbnRMaXN0ZW5lcigicG9pbnRlcm1vdmUiLGU9PntpZihlLnBvaW50ZXJUeXBlPT09Im1vdXNl"
    "IilteD1lLmNsaWVudFh9KTsKICAgIGRrLmFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJsZWF2ZSIs"
    "KCk9Pm14PUluZmluaXR5KTsKICAgIFsicG9pbnRlcnVwIiwicG9pbnRlcmNhbmNlbCIsInRvdWNo"
    "ZW5kIiwidG91Y2hjYW5jZWwiLCJibHVyIl0uZm9yRWFjaChldj0+CiAgICAgIGFkZEV2ZW50TGlz"
    "dGVuZXIoZXYscmVzZXRTY2FsZSx7cGFzc2l2ZTp0cnVlfSkpOwogIH1lbHNle2J0bnMuZm9yRWFj"
    "aChiPT5iLnN0eWxlLnRyYW5zZm9ybT0iIik7fQogIGRvY3VtZW50LmFkZEV2ZW50TGlzdGVuZXIo"
    "InZpc2liaWxpdHljaGFuZ2UiLHJlc2V0U2NhbGUpOwoKICBmdW5jdGlvbiBwbGFjZShzbmFwKXsK"
    "ICAgIGNvbnN0IG9uPWRrLnF1ZXJ5U2VsZWN0b3IoImJ1dHRvbi5vbiIpOwogICAgaWYoIW9ufHwh"
    "cGlsbHx8IW9uLm9mZnNldFdpZHRofHwhZGsub2Zmc2V0V2lkdGgpcmV0dXJuOyAgIC8vIG5ldmVy"
    "IHBsYWNlIHdoaWxlIGhpZGRlbgogICAgcHMueC50PW9uLm9mZnNldExlZnQtZGsuY2xpZW50TGVm"
    "dDtwcy53LnQ9b24ub2Zmc2V0V2lkdGg7CiAgICBwaWxsLnN0eWxlLnRvcD0ob24ub2Zmc2V0VG9w"
    "LWRrLmNsaWVudFRvcCkrInB4IjtwaWxsLnN0eWxlLmhlaWdodD1vbi5vZmZzZXRIZWlnaHQrInB4"
    "IjsKICAgIGlmKHNuYXB8fCFwbGFjZWQpe3BsYWNlZD10cnVlO3BzLngudj1wcy54LnQ7cHMueC52"
    "ZWw9MDtwcy53LnY9cHMudy50O3BzLncudmVsPTA7CiAgICAgIHBpbGwuc3R5bGUudHJhbnNmb3Jt"
    "PSJ0cmFuc2xhdGVYKCIrcHMueC52KyJweCkiO3BpbGwuc3R5bGUud2lkdGg9cHMudy52KyJweCI7"
    "CiAgICAgIHBpbGwuY2xhc3NMaXN0LmFkZCgicmVhZHkiKTt9CiAgfQogIG5ldyBSZXNpemVPYnNl"
    "cnZlcigoKT0+cGxhY2UoIXBsYWNlZCkpLm9ic2VydmUoZGspOwogIGFkZEV2ZW50TGlzdGVuZXIo"
    "InJlc2l6ZSIsKCk9PnBsYWNlKHRydWUpKTsKICBhZGRFdmVudExpc3RlbmVyKCJsb2FkIiwoKT0+"
    "cGxhY2UoIXBsYWNlZCkpOwogIHNldFRpbWVvdXQoKCk9PnBsYWNlKCFwbGFjZWQpLDYwKTtzZXRU"
    "aW1lb3V0KCgpPT5wbGFjZSghcGxhY2VkKSw0MDApOwoKICAoZnVuY3Rpb24gbG9vcCh0KXsKICAg"
    "IGNvbnN0IGR0PU1hdGgubWluKC4wMzIsKHQtbGFzdCkvMTAwMCk7bGFzdD10OwogICAgaWYoRklO"
    "RSl7YnRucy5mb3JFYWNoKChiLGkpPT57bGV0IGs9MDsKICAgICAgaWYobXghPT1JbmZpbml0eSl7"
    "Y29uc3Qgcj1iLmdldEJvdW5kaW5nQ2xpZW50UmVjdCgpOwogICAgICAgIGNvbnN0IGRpc3Q9TWF0"
    "aC5hYnMoci5sZWZ0K3Iud2lkdGgvMi1teCk7az1NYXRoLm1heCgwLDEtZGlzdC8xNTApO30KICAg"
    "ICAgc3BbaV0udD0xKy4zNCprKms7Y29uc3Qgdj1zcFtpXS5zdGVwKGR0KTsKICAgICAgYi5zdHls"
    "ZS50cmFuc2Zvcm09KHY+MS4wMDJ8fHNwW2ldLnZlbD4uMDF8fHNwW2ldLnZlbDwtLjAxKQogICAg"
    "ICAgID8gInRyYW5zbGF0ZVkoIisoLTgqKHYtMSkpLnRvRml4ZWQoMikrInB4KSBzY2FsZSgiK3Yu"
    "dG9GaXhlZCgzKSsiKSIgOiAiIjt9KTt9CiAgICBpZihwaWxsJiZwbGFjZWQpe3BpbGwuc3R5bGUu"
    "dHJhbnNmb3JtPSJ0cmFuc2xhdGVYKCIrcHMueC5zdGVwKGR0KS50b0ZpeGVkKDIpKyJweCkiOwog"
    "ICAgICBwaWxsLnN0eWxlLndpZHRoPXBzLncuc3RlcChkdCkudG9GaXhlZCgyKSsicHgiO30KICAg"
    "IHJlcXVlc3RBbmltYXRpb25GcmFtZShsb29wKTt9KShsYXN0KTsKICB3aW5kb3cuX19kb2NrUGxh"
    "Y2U9cGxhY2U7cGxhY2UodHJ1ZSk7Cn0pKCk7CgovKiAxNCDCtyB2LXRhYnMgc2xpZGluZyBpbmRp"
    "Y2F0b3IgZm9yIHRoZSBhZG1pbiBzdWJuYXYgKi8KKGZ1bmN0aW9uIHZ0YWJzKCl7CiAgY29uc3Qg"
    "bmF2PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJhc3ViIikscGlsbD1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgidnBpbGwiKTtpZighbmF2fHwhcGlsbClyZXR1cm47CiAgY29uc3QgcHg9bmV3IFNw"
    "cmluZygwLHtrOjIxMCxkOjE5fSkscHc9bmV3IFNwcmluZygwLHtrOjIxMCxkOjE5fSk7bGV0IGxh"
    "c3Q9cGVyZm9ybWFuY2Uubm93KCksaW5pdD1mYWxzZTsKICBmdW5jdGlvbiBwbGFjZShzbmFwKXtj"
    "b25zdCBvbj1uYXYucXVlcnlTZWxlY3RvcigiYnV0dG9uLm9uIik7CiAgICBpZighb258fCFvbi5v"
    "ZmZzZXRXaWR0aHx8IW5hdi5vZmZzZXRXaWR0aClyZXR1cm47CiAgICBweC50PW9uLm9mZnNldExl"
    "ZnQtbmF2LmNsaWVudExlZnQ7cHcudD1vbi5vZmZzZXRXaWR0aDsKICAgIHBpbGwuc3R5bGUudG9w"
    "PShvbi5vZmZzZXRUb3AtbmF2LmNsaWVudFRvcCkrInB4IjtwaWxsLnN0eWxlLmhlaWdodD1vbi5v"
    "ZmZzZXRIZWlnaHQrInB4IjsKICAgIGlmKHNuYXB8fCFpbml0KXtpbml0PXRydWU7cHgudj1weC50"
    "O3B4LnZlbD0wO3B3LnY9cHcudDtwdy52ZWw9MDsKICAgICAgcGlsbC5zdHlsZS50cmFuc2Zvcm09"
    "InRyYW5zbGF0ZVgoIitweC52KyJweCkiO3BpbGwuc3R5bGUud2lkdGg9cHcudisicHgiO319CiAg"
    "bmF2LmFkZEV2ZW50TGlzdGVuZXIoImNsaWNrIiwoKT0+cmVxdWVzdEFuaW1hdGlvbkZyYW1lKCgp"
    "PT5wbGFjZShmYWxzZSkpKTsKICBuYXYuYWRkRXZlbnRMaXN0ZW5lcigic2Nyb2xsIiwoKT0+cGxh"
    "Y2UoZmFsc2UpKTsKICBuZXcgUmVzaXplT2JzZXJ2ZXIoKCk9PnBsYWNlKCFpbml0KSkub2JzZXJ2"
    "ZShuYXYpOwogIGFkZEV2ZW50TGlzdGVuZXIoImxvYWQiLCgpPT5wbGFjZSghaW5pdCkpO3NldFRp"
    "bWVvdXQoKCk9PnBsYWNlKCFpbml0KSw4MCk7cGxhY2UodHJ1ZSk7CiAgKGZ1bmN0aW9uIGxvb3Ao"
    "dCl7Y29uc3QgZHQ9TWF0aC5taW4oLjAzMiwodC1sYXN0KS8xMDAwKTtsYXN0PXQ7CiAgICBpZihp"
    "bml0KXtwaWxsLnN0eWxlLnRyYW5zZm9ybT0idHJhbnNsYXRlWCgiK3B4LnN0ZXAoZHQpLnRvRml4"
    "ZWQoMikrInB4KSI7cGlsbC5zdHlsZS53aWR0aD1wdy5zdGVwKGR0KS50b0ZpeGVkKDIpKyJweCI7"
    "fQogICAgcmVxdWVzdEFuaW1hdGlvbkZyYW1lKGxvb3ApO30pKGxhc3QpOwogIHdpbmRvdy5fX3Z0"
    "YWJQbGFjZT1wbGFjZTsKfSkoKTsKCi8qIDA4IMK3IGtpbmV0aWMgZ3JpZCDigJQgcG9pbnRlciB3"
    "YXJwICsgY2xpY2sgcmlwcGxlcyAoY2FudmFzIHBvcnQpICovCihmdW5jdGlvbiBraW5ldGljKCl7"
    "CiAgY29uc3QgY3Y9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImtnIik7aWYoIWN2fHxSTSlyZXR1"
    "cm47Y29uc3QgY3R4PWN2LmdldENvbnRleHQoIjJkIik7CiAgY29uc3QgU1A9MzAsSU5GPTIxMCxX"
    "QVJQPTIyLExFUlA9LjA5LFJHQj0iMjU1LDEwNiw2MSI7CiAgbGV0IFc9MCxIPTAsbW91c2U9e3g6"
    "LTk5OTkseTotOTk5OX0sdGd0PXt4Oi05OTk5LHk6LTk5OTl9LHJpcD1bXSxyYWY9MDsKICBmdW5j"
    "dGlvbiBzaXplKCl7Y29uc3QgZD1NYXRoLm1pbigxLjYsZGV2aWNlUGl4ZWxSYXRpb3x8MSk7Vz1p"
    "bm5lcldpZHRoO0g9aW5uZXJIZWlnaHQ7CiAgICBjdi53aWR0aD1XKmQ7Y3YuaGVpZ2h0PUgqZDtj"
    "di5zdHlsZS53aWR0aD1XKyJweCI7Y3Yuc3R5bGUuaGVpZ2h0PUgrInB4IjtjdHguc2V0VHJhbnNm"
    "b3JtKGQsMCwwLGQsMCwwKTt9CiAgYWRkRXZlbnRMaXN0ZW5lcigicmVzaXplIixzaXplKTtzaXpl"
    "KCk7CiAgYWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcm1vdmUiLGU9Pnt0Z3Q9e3g6ZS5jbGllbnRY"
    "LHk6ZS5jbGllbnRZfX0se3Bhc3NpdmU6dHJ1ZX0pOwogIGFkZEV2ZW50TGlzdGVuZXIoInBvaW50"
    "ZXJkb3duIixlPT57aWYocmlwLmxlbmd0aDw0KXJpcC5wdXNoKHt4OmUuY2xpZW50WCx5OmUuY2xp"
    "ZW50WSxyOjAsbzoxfSl9LHtwYXNzaXZlOnRydWV9KTsKICBhZGRFdmVudExpc3RlbmVyKCJwb2lu"
    "dGVybGVhdmUiLCgpPT50Z3Q9e3g6LTk5OTkseTotOTk5OX0pOwogIGZ1bmN0aW9uIHdhcnAoZ3gs"
    "Z3ksYyxyLGNvbHMscm93cyl7CiAgICBjb25zdCBjcD1NYXRoLm1pbihjLzEuNSwoY29scy0xLWMp"
    "LzEuNSwxKSxycD1NYXRoLm1pbihyLzEuNSwocm93cy0xLXIpLzEuNSwxKSxwaW49Y3AqY3AqcnAq"
    "cnA7CiAgICBjb25zdCBkeD1neC1tb3VzZS54LGR5PWd5LW1vdXNlLnksZGlzdD1NYXRoLmh5cG90"
    "KGR4LGR5KTsKICAgIGNvbnN0IHByb3g9TWF0aC5tYXgoMCwxLWRpc3QvSU5GKSpwaW47bGV0IHJ4"
    "PTAscnk9MDsKICAgIGZvcihjb25zdCBwIG9mIHJpcCl7Y29uc3QgZGR4PWd4LXAueCxkZHk9Z3kt"
    "cC55LGRkPU1hdGguaHlwb3QoZGR4LGRkeSksZGlmZj1kZC1wLnI7CiAgICAgIGlmKE1hdGguYWJz"
    "KGRpZmYpPDU1KXtjb25zdCBzdD0oMS1NYXRoLmFicyhkaWZmKS81NSkqcC5vKjE2KnBpbixhbj1N"
    "YXRoLmF0YW4yKGRkeSxkZHgpLHNnPWRpZmY8MD8tMToxOwogICAgICAgIHJ4Kz1NYXRoLmNvcyhh"
    "bikqc3Qqc2cqLTE7cnkrPU1hdGguc2luKGFuKSpzdCpzZyotMTt9fQogICAgaWYoZGlzdDxJTkYm"
    "JmRpc3Q+MCYmcGluPjApe2NvbnN0IHQ9ZGlzdC9JTkYsZT0oMS10KSooMS10KSpNYXRoLm1pbigx"
    "LGRpc3QvNjApLHc9ZSpXQVJQKnBpbixhbj1NYXRoLmF0YW4yKGR5LGR4KTsKICAgICAgcmV0dXJu"
    "W2d4LU1hdGguY29zKGFuKSp3K3J4LGd5LU1hdGguc2luKGFuKSp3K3J5LHByb3hdO30KICAgIHJl"
    "dHVybltneCtyeCxneStyeSxwcm94XTt9CiAgZnVuY3Rpb24gZnJhbWUoKXsKICAgIG1vdXNlLngr"
    "PSh0Z3QueC1tb3VzZS54KSpMRVJQO21vdXNlLnkrPSh0Z3QueS1tb3VzZS55KSpMRVJQOwogICAg"
    "cmlwPXJpcC5maWx0ZXIocD0+e3Aucis9NztwLm8tPS4wMTY7cmV0dXJuIHAubz4wfSk7CiAgICBj"
    "dHguY2xlYXJSZWN0KDAsMCxXLEgpOwogICAgY29uc3QgY29scz1NYXRoLmNlaWwoVy9TUCkrMSxy"
    "b3dzPU1hdGguY2VpbChIL1NQKSsxLHB0cz1bXTsKICAgIGZvcihsZXQgcj0wO3I8cm93cztyKysp"
    "e2NvbnN0IHJvdz1bXTtmb3IobGV0IGM9MDtjPGNvbHM7YysrKXJvdy5wdXNoKHdhcnAoYypTUCxy"
    "KlNQLGMscixjb2xzLHJvd3MpKTtwdHMucHVzaChyb3cpO30KICAgIGZvcihsZXQgcj0wO3I8cm93"
    "cztyKyspZm9yKGxldCBjPTA7Yzxjb2xzO2MrKyl7Y29uc3QgcD1wdHNbcl1bY107CiAgICAgIGlm"
    "KGM8Y29scy0xKXtjb25zdCBxPXB0c1tyXVtjKzFdO2N0eC5zdHJva2VTdHlsZT0icmdiYSgiK1JH"
    "QisiLCIrKC4wNStwWzJdKi4yMikudG9GaXhlZCgzKSsiKSI7CiAgICAgICAgY3R4LmxpbmVXaWR0"
    "aD0xO2N0eC5iZWdpblBhdGgoKTtjdHgubW92ZVRvKHBbMF0scFsxXSk7Y3R4LmxpbmVUbyhxWzBd"
    "LHFbMV0pO2N0eC5zdHJva2UoKTt9CiAgICAgIGlmKHI8cm93cy0xKXtjb25zdCBxPXB0c1tyKzFd"
    "W2NdO2N0eC5zdHJva2VTdHlsZT0icmdiYSgiK1JHQisiLCIrKC4wNStwWzJdKi4yMikudG9GaXhl"
    "ZCgzKSsiKSI7CiAgICAgICAgY3R4LmxpbmVXaWR0aD0xO2N0eC5iZWdpblBhdGgoKTtjdHgubW92"
    "ZVRvKHBbMF0scFsxXSk7Y3R4LmxpbmVUbyhxWzBdLHFbMV0pO2N0eC5zdHJva2UoKTt9CiAgICAg"
    "IGlmKHBbMl0+LjA2KXtjdHguZmlsbFN0eWxlPSJyZ2JhKCIrUkdCKyIsIisocFsyXSouNSkudG9G"
    "aXhlZCgzKSsiKSI7Y3R4LmJlZ2luUGF0aCgpOwogICAgICAgIGN0eC5hcmMocFswXSxwWzFdLDEu"
    "NitwWzJdKjEuNiwwLDYuMjgzKTtjdHguZmlsbCgpO319CiAgICByYWY9cmVxdWVzdEFuaW1hdGlv"
    "bkZyYW1lKGZyYW1lKTt9CiAgZnJhbWUoKTsKICBkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCJ2"
    "aXNpYmlsaXR5Y2hhbmdlIiwoKT0+e2lmKGRvY3VtZW50LmhpZGRlbiljYW5jZWxBbmltYXRpb25G"
    "cmFtZShyYWYpO2Vsc2UgZnJhbWUoKTt9KTsKfSkoKTsKCi8qIDAxIMK3IGRvdCBsb2FkZXIgc3Bs"
    "YXNoIChraXQgZnJhbWUgZGF0YSkgKi8KKGZ1bmN0aW9uIHNwbGFzaCgpewogIGNvbnN0IGc9ZG9j"
    "dW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRnIiksc3A9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNw"
    "bGFzaCIpO2lmKCFnKXJldHVybjsKICBmb3IobGV0IGk9MDtpPDQ5O2krKylnLmFwcGVuZENoaWxk"
    "KGRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoImkiKSk7CiAgY29uc3QgY2VsbHM9Wy4uLmcuY2hpbGRy"
    "ZW5dOwogIGNvbnN0IEY9W1sxNCw3LDAsOCw2LDEzLDIwXSxbMTQsNywxMywyMCwxNiwyNywyMV0s"
    "WzE0LDIwLDI3LDIxLDM0LDI0LDI4XSxbMjcsMjEsMzQsMjgsNDEsMzIsMzVdLAogICBbMzQsMjgs"
    "NDEsMzUsNDgsNDAsNDJdLFszNCwyOCw0MSwzNSw0OCw0Miw0Nl0sWzM0LDI4LDQxLDM1LDQ4LDQy"
    "LDM4XSxbMzQsMjgsNDEsMzUsNDgsMzAsMjFdLAogICBbMzQsMjgsNDEsNDgsMjEsMjIsMTRdLFsz"
    "NCwyOCw0MSwyMSwxNCwxNiwyN10sWzM0LDI4LDIxLDE0LDEwLDIwLDI3XSxbMjgsMjEsMTQsNCwx"
    "MywyMCwyN10sCiAgIFsyOCwyMSwxNCwxMiw2LDEzLDIwXSxbMjgsMjEsMTQsNiwxMywyMCwxMV0s"
    "WzI4LDIxLDE0LDYsMTMsMjAsMTBdLFsxNCw2LDEzLDIwLDksNywyMV1dOwogIGxldCBmPTA7Y29u"
    "c3QgaXY9c2V0SW50ZXJ2YWwoKCk9PntjZWxscy5mb3JFYWNoKGM9PmMuY2xhc3NMaXN0LnJlbW92"
    "ZSgiYWN0IikpOwogICAgRltmJUYubGVuZ3RoXS5mb3JFYWNoKGk9PmNlbGxzW2ldJiZjZWxsc1tp"
    "XS5jbGFzc0xpc3QuYWRkKCJhY3QiKSk7ZisrO30sMTAwKTsKICBzZXRUaW1lb3V0KCgpPT57Y2xl"
    "YXJJbnRlcnZhbChpdik7c3AuY2xhc3NMaXN0LmFkZCgiZ29uZSIpOwogICAgc2V0VGltZW91dCgo"
    "KT0+e3NwLnJlbW92ZSgpO2lmKHdpbmRvdy5fX2RvY2tQbGFjZSl3aW5kb3cuX19kb2NrUGxhY2Uo"
    "dHJ1ZSk7CiAgICAgIGlmKHdpbmRvdy5fX3Z0YWJQbGFjZSl3aW5kb3cuX192dGFiUGxhY2UodHJ1"
    "ZSk7fSw2MDApO30sUk0/MzAwOjE3NTApOwp9KSgpOwoKLyogc3RhZ2dlcmVkIHJldmVhbCBvbiBl"
    "dmVyeSB2aWV3IGNoYW5nZSAqLwpmdW5jdGlvbiByZXZlYWxTdGFnZShlbCl7CiAgaWYoIWVsfHxS"
    "TSlyZXR1cm47CiAgWy4uLmVsLmNoaWxkcmVuXS5zbGljZSgwLDE0KS5mb3JFYWNoKChjLGkpPT57"
    "CiAgICBpZighYy5hbmltYXRlKXJldHVybjsKICAgIHRyeXsKICAgICAgYy5nZXRBbmltYXRpb25z"
    "KCkuZm9yRWFjaChhPT57aWYoYS5pZD09PSJydiIpYS5jYW5jZWwoKTt9KTsKICAgICAgY29uc3Qg"
    "YW49Yy5hbmltYXRlKFt7b3BhY2l0eTowLHRyYW5zZm9ybToidHJhbnNsYXRlWSgxNHB4KSBzY2Fs"
    "ZSguOTg1KSJ9LHtvcGFjaXR5OjEsdHJhbnNmb3JtOiJub25lIn1dLAogICAgICAgIHtkdXJhdGlv"
    "bjo1MjAsZGVsYXk6aSo0MCxlYXNpbmc6ImN1YmljLWJlemllciguMjIsMSwuMzYsMSkiLGZpbGw6"
    "ImJhY2t3YXJkcyJ9KTsKICAgICAgYW4uaWQ9InJ2IjsKICAgIH1jYXRjaChlKXt9CiAgfSk7fQoK"
    "LyogbnVtYmVyIHRpY2tlciAqLwpmdW5jdGlvbiB0aWNrVG8oZWwsdG8sZHVyKXtpZighZWwpcmV0"
    "dXJuO2NvbnN0IGZyb209cGFyc2VGbG9hdChlbC50ZXh0Q29udGVudC5yZXBsYWNlKC9bXlxkLl0v"
    "ZywiIikpfHwwOwogIGlmKFJNKXtlbC50ZXh0Q29udGVudD10by50b0ZpeGVkKDIpO3JldHVybn0K"
    "ICBjb25zdCB0MD1wZXJmb3JtYW5jZS5ub3coKTsoZnVuY3Rpb24gc3QodCl7Y29uc3QgcD1NYXRo"
    "Lm1pbigxLCh0LXQwKS8oZHVyfHw2MDApKTsKICAgIGNvbnN0IGU9MS1NYXRoLnBvdygxLXAsMyk7"
    "ZWwudGV4dENvbnRlbnQ9KGZyb20rKHRvLWZyb20pKmUpLnRvRml4ZWQoMik7CiAgICBpZihwPDEp"
    "cmVxdWVzdEFuaW1hdGlvbkZyYW1lKHN0KTt9KSh0MCk7fQoKLyogcHJlc3MgcmlwcGxlIG9uIGV2"
    "ZXJ5IGJ1dHRvbiAqLwpkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCJwb2ludGVyZG93biIsZT0+"
    "e2NvbnN0IGI9ZS50YXJnZXQuY2xvc2VzdCgiLmJ0biwuY2hpcHJvdyBidXR0b24sLnN1Ym5hdiBi"
    "dXR0b24iKTsKICBpZighYnx8Uk0pcmV0dXJuO2NvbnN0IHI9Yi5nZXRCb3VuZGluZ0NsaWVudFJl"
    "Y3QoKSxkPU1hdGgubWF4KHIud2lkdGgsci5oZWlnaHQpKjEuNDsKICBjb25zdCBzPWRvY3VtZW50"
    "LmNyZWF0ZUVsZW1lbnQoInNwYW4iKTtzLmNsYXNzTmFtZT0icmlwIjsKICBzLnN0eWxlLmNzc1Rl"
    "eHQ9ImxlZnQ6IisoZS5jbGllbnRYLXIubGVmdCkrInB4O3RvcDoiKyhlLmNsaWVudFktci50b3Ap"
    "KyJweDt3aWR0aDoiK2QrInB4O2hlaWdodDoiK2QrInB4IjsKICBpZihnZXRDb21wdXRlZFN0eWxl"
    "KGIpLnBvc2l0aW9uPT09InN0YXRpYyIpYi5zdHlsZS5wb3NpdGlvbj0icmVsYXRpdmUiOwogIGIu"
    "YXBwZW5kQ2hpbGQocyk7c2V0VGltZW91dCgoKT0+cy5yZW1vdmUoKSw1NjApO30pOwoKLyogMTUg"
    "wrcgdGlsdCArIGdsYXJlICovCmZ1bmN0aW9uIGJpbmRUaWx0KCl7ZG9jdW1lbnQucXVlcnlTZWxl"
    "Y3RvckFsbCgiW2RhdGEtdGlsdF0iKS5mb3JFYWNoKGM9PnsKICBpZihjLl90fHxSTSlyZXR1cm47"
    "Yy5fdD0xOwogIGMuYWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcm1vdmUiLGU9Pntjb25zdCByPWMu"
    "Z2V0Qm91bmRpbmdDbGllbnRSZWN0KCk7CiAgICBjb25zdCB4PShlLmNsaWVudFgtci5sZWZ0KS9y"
    "LndpZHRoLHk9KGUuY2xpZW50WS1yLnRvcCkvci5oZWlnaHQ7CiAgICBjLnN0eWxlLnRyYW5zZm9y"
    "bT0icGVyc3BlY3RpdmUoNzAwcHgpIHJvdGF0ZVgoIisoKC41LXkpKjkpLnRvRml4ZWQoMikrImRl"
    "Zykgcm90YXRlWSgiKygoeC0uNSkqMTEpLnRvRml4ZWQoMikrImRlZykgdHJhbnNsYXRlWig0cHgp"
    "IjsKICAgIGMuc3R5bGUuc2V0UHJvcGVydHkoIi0tbXgiLCh4KjEwMCkrIiUiKTtjLnN0eWxlLnNl"
    "dFByb3BlcnR5KCItLW15IiwoeSoxMDApKyIlIik7fSk7CiAgYy5hZGRFdmVudExpc3RlbmVyKCJw"
    "b2ludGVybGVhdmUiLCgpPT5jLnN0eWxlLnRyYW5zZm9ybT0iIik7fSk7fQoKLyogc3VjY2VzcyBi"
    "dXJzdCAqLwpmdW5jdGlvbiBidXJzdCgpe2NvbnN0IGZ4PWRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJmeCIpO2lmKCFmeClyZXR1cm47ZnguY2xhc3NMaXN0LmFkZCgib24iKTsKICBjb25zdCB3PWZ4"
    "LnF1ZXJ5U2VsZWN0b3IoIi53YXZlIik7dy5zdHlsZS5hbmltYXRpb249Im5vbmUiO3ZvaWQgdy5v"
    "ZmZzZXRXaWR0aDt3LnN0eWxlLmFuaW1hdGlvbj0iIjsKICBzZXRUaW1lb3V0KCgpPT5meC5jbGFz"
    "c0xpc3QucmVtb3ZlKCJvbiIpLDkwMCk7fQoKLyog4pSA4pSAIFBheSArIHZlcmlmeSBkZXBvc2l0"
    "IGZsb3cgKDUtbWludXRlIHdpbmRvdywgdmVyaWZ5IG9ubHkgb24gdGFwKSDilIDilIAgKi8KbGV0"
    "IGRlcFRpY2s9bnVsbCxkZXBKb2I9bnVsbDsKZnVuY3Rpb24gYW1vdW50U2hlZXQoKXsKICBjb25z"
    "dCBwaWNrcz1bNTAsMTAwLDIwMCw1MDAsMTAwMCwyMDAwXTsKICBzaGVldChgPGgzIHN0eWxlPSJt"
    "YXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+JHtDTVMud2FsS2lja3x8IkFkZCBmdW5kcyJ9"
    "PC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPlBpY2sgYW4gYW1vdW50IG9yIHR5cGUgeW91ciBvd24u"
    "PC9wPgogICAgPGRpdiBjbGFzcz0iY2hpcHMiIHN0eWxlPSJtYXJnaW46MTJweCAwIDRweDtkaXNw"
    "bGF5OmZsZXg7ZmxleC13cmFwOndyYXA7Z2FwOjdweCI+CiAgICAgICR7cGlja3MubWFwKHY9PmA8"
    "YnV0dG9uIGNsYXNzPSJidG4gc20iIGRhdGEtcGljaz0iJHt2fSIgc3R5bGU9ImZsZXg6MCAwIGF1"
    "dG8iPiR7bW9uZXkodil9PC9idXR0b24+YCkuam9pbigiIil9CiAgICA8L2Rpdj4KICAgIDxkaXYg"
    "Y2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+Q3VzdG9tIGFtb3Vu"
    "dDwvbGFiZWw+CiAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0iYW10aW4iIHR5cGU9Im51bWJl"
    "ciIgaW5wdXRtb2RlPSJudW1lcmljIiBtaW49IjEiIHBsYWNlaG9sZGVyPSJFbnRlciBhbW91bnQi"
    "PjwvZGl2PgogICAgPHAgY2xhc3M9InN1YiIgaWQ9ImFtdGhpbnQiIHN0eWxlPSJtYXJnaW46MCAw"
    "IDEwcHg7Zm9udC1zaXplOi43MnJlbSI+PC9wPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBi"
    "bG9jayIgaWQ9ImFtdGdvIj5Db250aW51ZTwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IGdob3N0IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+Q2FuY2Vs"
    "PC9idXR0b24+YCk7CiAgY29uc3QgaW5wPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJhbXRpbiIp"
    "LCBnbz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYW10Z28iKSwKICAgICAgICBoaW50PWRvY3Vt"
    "ZW50LmdldEVsZW1lbnRCeUlkKCJhbXRoaW50Iik7CiAgY29uc3QgbG89KEFETSYmQURNLnBheSYm"
    "QURNLnBheS5taW4pfHwxMCwgaGk9KEFETSYmQURNLnBheSYmQURNLnBheS5tYXgpfHwyMDAwMDsK"
    "ICBoaW50LnRleHRDb250ZW50PSJCZXR3ZWVuICIrbW9uZXkobG8pKyIgYW5kICIrbW9uZXkoaGkp"
    "KyIuIjsKICBjb25zdCBzdGFydD12PT57CiAgICBjb25zdCBuPU51bWJlcih2KTsKICAgIGlmKCFu"
    "fHxuPD0wKXt0b2FzdCgiRW50ZXIgYW4gYW1vdW50IGZpcnN0Iik7aW5wJiZpbnAuZm9jdXMoKTty"
    "ZXR1cm47fQogICAgaWYobjxsb3x8bj5oaSl7dG9hc3QoIkFsbG93ZWQgIittb25leShsbykrIiB0"
    "byAiK21vbmV5KGhpKSk7cmV0dXJuO30KICAgIGRlcG9zaXRTaGVldChuKTsKICB9OwogIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXBpY2tdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9"
    "KCk9PnN0YXJ0KGIuZGF0YXNldC5waWNrKSk7CiAgZ28ub25jbGljaz0oKT0+c3RhcnQoaW5wLnZh"
    "bHVlKTsKICBpZihpbnApaW5wLm9ua2V5ZG93bj1lPT57aWYoZS5rZXk9PT0iRW50ZXIiKXN0YXJ0"
    "KGlucC52YWx1ZSk7fTsKICBzZXRUaW1lb3V0KCgpPT57aWYoaW5wKWlucC5mb2N1cygpO30sMjUw"
    "KTsKfQphc3luYyBmdW5jdGlvbiBsaXZlRGVwb3NpdFNoZWV0KGEpewogIGNsZWFySW50ZXJ2YWwo"
    "ZGVwVGljayk7Y2xlYXJUaW1lb3V0KGRlcEpvYik7CiAgY29uc3QgZD1hd2FpdCBhcGlUcnkoIi9h"
    "cGkvZGVwb3NpdC9jcmVhdGUiLHthbW91bnQ6TnVtYmVyKGEpfSk7CiAgaWYoIWQpcmV0dXJuOwog"
    "IGNvbnN0IG1pbnM9ZC53aW5kb3d8fDU7CiAgbGV0IGxlZnQ9TWF0aC5tYXgoMCxNYXRoLnJvdW5k"
    "KChuZXcgRGF0ZShkLmV4cGlyZXNfYXQpLW5ldyBEYXRlKCkpLzEwMDApKXx8bWlucyo2MDsKICBj"
    "b25zdCB0b3RhbD1taW5zKjYwOwogIGxldCBkb25lPWZhbHNlLGJ1c3k9ZmFsc2U7CiAgc2hlZXQo"
    "YDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPiR7Q01TLnNoZWV0VGl0"
    "bGV9ICR7bW9uZXkoZC5hbW91bnQpfTwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj4ke0NNUy5zaGVl"
    "dFN1Yi5yZXBsYWNlKCJ7bX0iLG1pbnMpfTwvcD4KICAgICR7ZC5xcj9gPGRpdiBjbGFzcz0icXIi"
    "IHN0eWxlPSJwYWRkaW5nOjA7b3ZlcmZsb3c6aGlkZGVuIj48aW1nIHNyYz0iJHtkLnFyfSIgYWx0"
    "PSJVUEkgUVIiIHN0eWxlPSJ3aWR0aDoxMDAlO2Rpc3BsYXk6YmxvY2siPjwvZGl2PmA6IiJ9CiAg"
    "ICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VVBJIElEPC9zcGFuPjxiPiR7ZXNjKGQudXBpfHwiIil9"
    "PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlBheWVlPC9zcGFuPjxiPiR7ZXNj"
    "KGQucGF5ZWV8fCIiKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+QW1vdW50"
    "PC9zcGFuPjxiPiR7bW9uZXkoZC5hbW91bnQpfTwvYj48L2Rpdj4KICAgICR7ZC5ib251cz9gPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPkJvbnVzPC9zcGFuPjxiPiske21vbmV5KGQuYm9udXMpfTwvYj48"
    "L2Rpdj5gOiIifQogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPk5vdGUgLyByZWZlcmVuY2U8L3Nw"
    "YW4+PGI+JHtlc2MoZC5jb2RlKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+"
    "RXhwaXJlcyBpbjwvc3Bhbj48YiBpZD0iZHBjZCI+JHtNYXRoLmZsb29yKGxlZnQvNjApfToke1N0"
    "cmluZyhsZWZ0JTYwKS5wYWRTdGFydCgyLCIwIil9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0i"
    "dGltZXJiYXIiIGlkPSJkcGJhciI+PGkgc3R5bGU9IndpZHRoOjEwMCUiPjwvaT48L2Rpdj4KICAg"
    "IDxkaXYgY2xhc3M9InN0ZXBzIiBpZD0iZHN0ZXBzIj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCBv"
    "biI+PGk+MTwvaT4ke0NNUy5zdGVwMX08L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCIgaWQ9"
    "InMyIj48aT4yPC9pPiR7Q01TLnN0ZXAyfTwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGVwIiBp"
    "ZD0iczMiPjxpPjM8L2k+JHtDTVMuc3RlcDN9PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAi"
    "IGlkPSJzNCI+PGk+NDwvaT4ke0NNUy5zdGVwNH08L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBj"
    "bGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPgogICAgICA8YnV0dG9uIGNsYXNz"
    "PSJidG4iIGlkPSJkcGF5Ij7wn4+mIENvcHkgVVBJIElEPC9idXR0b24+CiAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBjdGEiIGlkPSJkdmVyIj4ke1NUT1JFLmF1dG9jaGVjaz09PWZhbHNlPyLinIUg"
    "SSBoYXZlIHBhaWQiOiLinIUgIitDTVMudmVyaWZ5QnRufTwvYnV0dG9uPjwvZGl2PgogICAgJHtT"
    "VE9SRS5hdXRvY2hlY2s9PT1mYWxzZT8iIjpgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQgYmxvY2sg"
    "c20iIGlkPSJkdXRyIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPvCfp74gU2VuZCB0cmFuc2FjdGlv"
    "biBuby48L2J1dHRvbj5gfQogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJsb2NrIHNtIiBz"
    "dHlsZT0ibWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTsKICBjb25z"
    "dCBnPWlkPT5kb2N1bWVudC5nZXRFbGVtZW50QnlJZChpZCk7CiAgY29uc3QgcGF5Qj1nKCJkcGF5"
    "IiksdmVyQj1nKCJkdmVyIiksdXRyQj1nKCJkdXRyIik7CiAgY29uc3QgYWxpdmU9KCk9PmcoImRw"
    "Y2QiKSYmZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNoZWV0IikuY2xhc3NMaXN0LmNvbnRhaW5z"
    "KCJvcGVuIik7CiAgZnVuY3Rpb24gZXhwaXJlKCl7CiAgICBjbGVhckludGVydmFsKGRlcFRpY2sp"
    "OwogICAgaWYoIWFsaXZlKCkpcmV0dXJuOwogICAgZygiZHBjZCIpLnRleHRDb250ZW50PSIwOjAw"
    "IjtnKCJkcGJhciIpLmNsYXNzTGlzdC5hZGQoImxvdyIpO2coImRwYmFyIikuZmlyc3RDaGlsZC5z"
    "dHlsZS53aWR0aD0iMCUiOwogICAgdmVyQi5kaXNhYmxlZD10cnVlO3V0ckIuZGlzYWJsZWQ9dHJ1"
    "ZTt2ZXJCLnRleHRDb250ZW50PSJFeHBpcmVkIjsKICAgIHBheUIuY2xhc3NOYW1lPSJidG4iO3Bh"
    "eUIudGV4dENvbnRlbnQ9Ik5ldyBSZXF1ZXN0IjtwYXlCLm9uY2xpY2s9KCk9PmRlcG9zaXRTaGVl"
    "dChhKTsKICAgIHRvYXN0KENNUy5tc2dFeHBpcmVkKTsKICB9CiAgZGVwVGljaz1zZXRJbnRlcnZh"
    "bCgoKT0+ewogICAgaWYoIWFsaXZlKCkpe2NsZWFySW50ZXJ2YWwoZGVwVGljayk7cmV0dXJuO30K"
    "ICAgIGxlZnQtLTsKICAgIGcoImRwY2QiKS50ZXh0Q29udGVudD1NYXRoLm1heCgwLE1hdGguZmxv"
    "b3IobGVmdC82MCkpKyI6IitTdHJpbmcoTWF0aC5tYXgoMCxsZWZ0JTYwKSkucGFkU3RhcnQoMiwi"
    "MCIpOwogICAgZygiZHBiYXIiKS5maXJzdENoaWxkLnN0eWxlLndpZHRoPShNYXRoLm1heCgwLGxl"
    "ZnQpL3RvdGFsKjEwMCkrIiUiOwogICAgZygiZHBiYXIiKS5jbGFzc0xpc3QudG9nZ2xlKCJsb3ci"
    "LGxlZnQ8PTYwKTsKICAgIGlmKGxlZnQ8PTApZXhwaXJlKCk7CiAgfSwxMDAwKTsKICBpZihwYXlC"
    "KXBheUIub25jbGljaz0oKT0+ewogICAgaWYoZG9uZSlyZXR1cm47CiAgICBnKCJzMiIpLmNsYXNz"
    "TmFtZT0ic3RlcCBvbiI7ZygiczMiKS5jbGFzc05hbWU9InN0ZXAgcnVuIjsKICAgIHZlckIuY2xh"
    "c3NMaXN0LnJlbW92ZSgiZ2xhc3MiKTt2ZXJCLmNsYXNzTGlzdC5hZGQoImN0YSIpOwogICAgdHJ5"
    "e25hdmlnYXRvci5jbGlwYm9hcmQmJm5hdmlnYXRvci5jbGlwYm9hcmQud3JpdGVUZXh0KGQudXBp"
    "fHwiIik7fWNhdGNoKGUpe30KICAgIHRvYXN0KCJVUEkgSUQgY29waWVkOiAiKyhkLnVwaXx8IiIp"
    "KTsKICB9OwogIGlmKHZlckIpdmVyQi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKGRvbmV8fGJ1"
    "c3l8fGxlZnQ8PTApcmV0dXJuOwogICAgaWYoU1RPUkUuYXV0b2NoZWNrPT09ZmFsc2UpeyAgICAg"
    "ICAgICAvKiBubyBhdXRvbWF0aWMgY2hlY2tpbmcg4oCUIGNvbGxlY3QgcHJvb2YgKi8KICAgICAg"
    "c2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPlNlbmQgeW91"
    "ciBwYXltZW50IHByb29mPC9oMz4KICAgICAgICA8cCBjbGFzcz0ic3ViIj5BIHNjcmVlbnNob3Qg"
    "b2YgdGhlIHN1Y2Nlc3NmdWwgcGF5bWVudCBhbmQgdGhlIFVUUi48L3A+CiAgICAgICAgPGRpdiBj"
    "bGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5TY3JlZW5zaG90PC9s"
    "YWJlbD4KICAgICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0icGZzaG90IiB0eXBlPSJmaWxl"
    "IiBhY2NlcHQ9ImltYWdlLyoiPjwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFi"
    "ZWw+VVRSIC8gdHJhbnNhY3Rpb24gbnVtYmVyPC9sYWJlbD4KICAgICAgICAgIDxpbnB1dCBjbGFz"
    "cz0iaW5wIiBpZD0icGZ1dHIiIGlucHV0bW9kZT0ibnVtZXJpYyIgcGxhY2Vob2xkZXI9IjEyIGRp"
    "Z2l0cyI+PC9kaXY+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBibG9jayIgaWQ9InBm"
    "Z28iPlN1Ym1pdCBmb3IgcmV2aWV3PC9idXR0b24+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IGdob3N0IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+Q2FuY2Vs"
    "PC9idXR0b24+YCk7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJwZmdvIikub25jbGlj"
    "az1hc3luYygpPT57CiAgICAgICAgY29uc3QgZj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicGZz"
    "aG90Iik7CiAgICAgICAgY29uc3QgdT0oZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInBmdXRyIiku"
    "dmFsdWV8fCIiKS5yZXBsYWNlKC9cRC9nLCIiKTsKICAgICAgICBpZih1Lmxlbmd0aDw2KXt0b2Fz"
    "dCgiRW50ZXIgdGhlIFVUUiBmcm9tIHlvdXIgcGF5bWVudCBhcHAiKTtyZXR1cm47fQogICAgICAg"
    "IGNvbnN0IGZpbGU9KGYmJmYuZmlsZXMmJmYuZmlsZXNbMF0pfHxudWxsOwogICAgICAgIGxldCBz"
    "aG90PSIiOwogICAgICAgIGlmKGZpbGUpewogICAgICAgICAgaWYoZmlsZS5zaXplPjgqMTAyNCox"
    "MDI0KXt0b2FzdCgiVGhhdCBpbWFnZSBpcyB0b28gbGFyZ2UgKDggTUIgbWF4KSIpO3JldHVybjt9"
    "CiAgICAgICAgICBzaG90PWF3YWl0IG5ldyBQcm9taXNlKHJlcz0+e2NvbnN0IHJkPW5ldyBGaWxl"
    "UmVhZGVyKCk7CiAgICAgICAgICAgIHJkLm9ubG9hZD0oKT0+cmVzKFN0cmluZyhyZC5yZXN1bHR8"
    "fCIiKSk7cmQub25lcnJvcj0oKT0+cmVzKCIiKTsKICAgICAgICAgICAgcmQucmVhZEFzRGF0YVVS"
    "TChmaWxlKTt9KTsKICAgICAgICB9CiAgICAgICAgY29uc3QgYnRuPWRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJwZmdvIik7YnRuLmRpc2FibGVkPXRydWU7YnRuLnRleHRDb250ZW50PSJTZW5kaW5n"
    "4oCmIjsKICAgICAgICBjb25zdCBycj1hd2FpdCBhcGlUcnkoIi9hcGkvZGVwb3NpdC9jbGFpbSIs"
    "e2lkOmQuaWQsdXRyOnUsc2hvdDpzaG90fSk7CiAgICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0"
    "bi50ZXh0Q29udGVudD0iU3VibWl0IGZvciByZXZpZXciOwogICAgICAgIGlmKCFycilyZXR1cm47"
    "CiAgICAgICAgY2xvc2VTaGVldCgpO3RvYXN0KCJTZW50IOKAlCB3ZSB3aWxsIGNvbmZpcm0gaXQg"
    "c2hvcnRseSIpOwogICAgICAgIGF3YWl0IHJlZnJlc2goKTsKICAgICAgfTsKICAgICAgcmV0dXJu"
    "OwogICAgfQogICAgYnVzeT10cnVlO3ZlckIuZGlzYWJsZWQ9dHJ1ZTt2ZXJCLnRleHRDb250ZW50"
    "PSJDaGVja2luZ+KApiI7ZygiczMiKS5jbGFzc05hbWU9InN0ZXAgcnVuIjsKICAgIGNvbnN0IHI9"
    "YXdhaXQgYXBpVHJ5KCIvYXBpL2RlcG9zaXQvdmVyaWZ5Iix7aWQ6ZC5pZH0pOwogICAgYnVzeT1m"
    "YWxzZTsKICAgIGlmKCFhbGl2ZSgpKXJldHVybjsKICAgIHZlckIuZGlzYWJsZWQ9ZmFsc2U7dmVy"
    "Qi50ZXh0Q29udGVudD1DTVMudmVyaWZ5QnRuOwogICAgaWYoIXIpcmV0dXJuOwogICAgaWYoci5z"
    "dGF0dXM9PT0iY29tcGxldGVkIil7CiAgICAgIGRvbmU9dHJ1ZTtjbGVhckludGVydmFsKGRlcFRp"
    "Y2spOwogICAgICBnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCBvbiI7ZygiczQiKS5jbGFzc05hbWU9"
    "InN0ZXAgb24iOwogICAgICBpZih0eXBlb2Ygci5iYWxhbmNlPT09Im51bWJlciIpQkFMPXIuYmFs"
    "YW5jZTsKICAgICAgc3luY0JhbCgpO2J1cnN0KCk7dG9hc3QoQ01TLm1zZ09rKTsKICAgICAgc2V0"
    "VGltZW91dCgoKT0+e2Nsb3NlU2hlZXQoKTtyZWZyZXNoKCk7fSw5MDApOwogICAgfWVsc2UgaWYo"
    "ci5zdGF0dXM9PT0icmV2aWV3Iil7CiAgICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIG9uIjt0"
    "b2FzdCgiUGF5bWVudCBzZW50IGZvciBjaGVja2luZyDigJQgd2Ugd2lsbCBjb25maXJtIGl0IHNo"
    "b3J0bHkiKTsKICAgIH1lbHNlIGlmKHIuc3RhdHVzPT09ImV4cGlyZWQiKXsKICAgICAgZXhwaXJl"
    "KCk7CiAgICB9ZWxzZXsKICAgICAgdG9hc3QoQ01TLm1zZ05vbmUpOwogICAgfQogIH07CiAgaWYo"
    "dXRyQil1dHJCLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3Qgdj1wcm9tcHQoIkVudGVyIHRo"
    "ZSAxMi1kaWdpdCBVVFIgZnJvbSB5b3VyIHBheW1lbnQgYXBwIik7CiAgICBpZighdilyZXR1cm47"
    "CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9kZXBvc2l0L3V0ciIse2lkOmQuaWQsdXRy"
    "OnZ9KTsKICAgIGlmKHIpdG9hc3QoIlRoYW5rcyDigJQgd2UgYXJlIGNoZWNraW5nIHlvdXIgcGF5"
    "bWVudCIpOwogIH07Cn0KZnVuY3Rpb24gZGVwb3NpdFNoZWV0KGEpewogIGlmKExJVkUpcmV0dXJu"
    "IGxpdmVEZXBvc2l0U2hlZXQoYSk7CiAgY2xlYXJJbnRlcnZhbChkZXBUaWNrKTtjbGVhclRpbWVv"
    "dXQoZGVwSm9iKTsKICBjb25zdCBjb2RlPShDTVMucGF5UHJlZml4fHwiUEFZIikrTWF0aC5mbG9v"
    "cigxMDAwK01hdGgucmFuZG9tKCkqODk5OSk7CiAgY29uc3QgbWlucz1NYXRoLm1heCgxLHBhcnNl"
    "SW50KENNUy5wYXlXaW5kb3csMTApfHw1KSwgdG90YWw9bWlucyo2MDsKICBsZXQgbGVmdD10b3Rh"
    "bCxwYWlkPWZhbHNlLGRvbmU9ZmFsc2UsYnVzeT1mYWxzZTsKICBzaGVldChgPGgzIHN0eWxlPSJt"
    "YXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+JHtDTVMuc2hlZXRUaXRsZX0gJHttb25leShh"
    "KX08L2gzPgogICAgPHAgY2xhc3M9InN1YiI+JHtDTVMuc2hlZXRTdWIucmVwbGFjZSgie219Iixt"
    "aW5zKX08L3A+CiAgICA8ZGl2IGNsYXNzPSJxciI+UVIgQ09ERTxicj4oZ2VuZXJhdGVkIGJ5IGJv"
    "dCk8L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5VUEkgSUQ8L3NwYW4+PGI+JHtlc2Mo"
    "U1RPUkUudXBpfHwibm90IHNldCB5ZXQiKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+"
    "PHNwYW4+UGF5ZWU8L3NwYW4+PGI+JHtlc2MoU1RPUkUucGF5ZWV8fCLigJQiKX08L2I+PC9kaXY+"
    "CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+QW1vdW50PC9zcGFuPjxiPiR7bW9uZXkoYSl9PC9i"
    "PjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPk5vdGUgLyByZWZlcmVuY2U8L3NwYW4+"
    "PGI+JHtjb2RlfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5FeHBpcmVzIGlu"
    "PC9zcGFuPjxiIGlkPSJkcGNkIj4ke21pbnN9OjAwPC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0i"
    "dGltZXJiYXIiIGlkPSJkcGJhciI+PGkgc3R5bGU9IndpZHRoOjEwMCUiPjwvaT48L2Rpdj4KICAg"
    "IDxkaXYgY2xhc3M9InN0ZXBzIiBpZD0iZHN0ZXBzIj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCBv"
    "biI+PGk+MTwvaT4ke0NNUy5zdGVwMX08L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCIgaWQ9"
    "InMyIj48aT4yPC9pPiR7Q01TLnN0ZXAyfTwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGVwIiBp"
    "ZD0iczMiPjxpPjM8L2k+JHtDTVMuc3RlcDN9PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAi"
    "IGlkPSJzNCI+PGk+NDwvaT4ke0NNUy5zdGVwNH08L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBj"
    "bGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPgogICAgICA8YnV0dG9uIGNsYXNz"
    "PSJidG4gY3RhIiBpZD0iZHBheSI+JHtDTVMucGF5QnRufTwvYnV0dG9uPgogICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4gZ2xhc3MiIGlkPSJkdmVyIj4ke0NNUy52ZXJpZnlCdG59PC9idXR0b24+PC9k"
    "aXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCBibG9jayBzbSIgaWQ9ImR1dHIiIHN0eWxl"
    "PSJtYXJnaW4tdG9wOjhweCI+JHtDTVMudXRyQnRufTwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIGdob3N0IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+"
    "Q2xvc2U8L2J1dHRvbj5gKTsKICBjb25zdCBnPWlkPT5kb2N1bWVudC5nZXRFbGVtZW50QnlJZChp"
    "ZCk7CiAgY29uc3QgcGF5Qj1nKCJkcGF5IiksdmVyQj1nKCJkdmVyIiksdXRyQj1nKCJkdXRyIik7"
    "CiAgZnVuY3Rpb24gZm10KG4pe3JldHVybiBNYXRoLmZsb29yKG4vNjApKyI6IitTdHJpbmcobiU2"
    "MCkucGFkU3RhcnQoMiwiMCIpO30KICBmdW5jdGlvbiBhbGl2ZSgpe3JldHVybiBnKCJkcGNkIikm"
    "JmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzaGVldCIpLmNsYXNzTGlzdC5jb250YWlucygib3Bl"
    "biIpO30KICBmdW5jdGlvbiBleHBpcmUoKXsKICAgIGNsZWFySW50ZXJ2YWwoZGVwVGljayk7Y2xl"
    "YXJUaW1lb3V0KGRlcEpvYik7CiAgICBpZighYWxpdmUoKSlyZXR1cm47CiAgICBnKCJkcGNkIiku"
    "dGV4dENvbnRlbnQ9IjA6MDAiO2coImRwYmFyIikuY2xhc3NMaXN0LmFkZCgibG93Iik7ZygiZHBi"
    "YXIiKS5maXJzdENoaWxkLnN0eWxlLndpZHRoPSIwJSI7CiAgICBwYXlCLmRpc2FibGVkPXRydWU7"
    "dmVyQi5kaXNhYmxlZD10cnVlO3V0ckIuZGlzYWJsZWQ9dHJ1ZTsKICAgIGcoInMyIikuY2xhc3NO"
    "YW1lPSJzdGVwIjtnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCI7CiAgICB2ZXJCLnRleHRDb250ZW50"
    "PSJFeHBpcmVkIjtwYXlCLmNsYXNzTmFtZT0iYnRuIjtwYXlCLmRpc2FibGVkPWZhbHNlO3BheUIu"
    "dGV4dENvbnRlbnQ9Ik5ldyBSZXF1ZXN0IjsKICAgIHBheUIub25jbGljaz0oKT0+ZGVwb3NpdFNo"
    "ZWV0KGEpOwogICAgdG9hc3QoQ01TLm1zZ0V4cGlyZWQpOwogIH0KICBkZXBUaWNrPXNldEludGVy"
    "dmFsKCgpPT57CiAgICBpZighYWxpdmUoKSl7Y2xlYXJJbnRlcnZhbChkZXBUaWNrKTtyZXR1cm47"
    "fQogICAgbGVmdC0tO2coImRwY2QiKS50ZXh0Q29udGVudD1mbXQoTWF0aC5tYXgoMCxsZWZ0KSk7"
    "CiAgICBnKCJkcGJhciIpLmZpcnN0Q2hpbGQuc3R5bGUud2lkdGg9KE1hdGgubWF4KDAsbGVmdCkv"
    "dG90YWwqMTAwKSsiJSI7CiAgICBnKCJkcGJhciIpLmNsYXNzTGlzdC50b2dnbGUoImxvdyIsbGVm"
    "dDw9NjApOwogICAgaWYobGVmdDw9MClleHBpcmUoKTsKICB9LDEwMDApOwogIGlmKHBheUIpcGF5"
    "Qi5vbmNsaWNrPSgpPT57CiAgICBpZihkb25lKXJldHVybjsKICAgIHBhaWQ9dHJ1ZTtnKCJzMiIp"
    "LmNsYXNzTmFtZT0ic3RlcCBvbiI7ZygiczMiKS5jbGFzc05hbWU9InN0ZXAgcnVuIjsKICAgIHBh"
    "eUIudGV4dENvbnRlbnQ9IlBheSBBZ2FpbiI7dmVyQi5jbGFzc0xpc3QucmVtb3ZlKCJnbGFzcyIp"
    "O3ZlckIuY2xhc3NMaXN0LmFkZCgiY3RhIik7CiAgICB0b2FzdCgiT3BlbmluZyB5b3VyIFVQSSBh"
    "cHAg4oCUIHBheSAiK21vbmV5KGEpKyIsIHRoZW4gdGFwIFZlcmlmeSIpOwogIH07CiAgdmVyQi5v"
    "bmNsaWNrPSgpPT57CiAgICBpZihkb25lfHxidXN5fHxsZWZ0PD0wKXJldHVybjsKICAgIGJ1c3k9"
    "dHJ1ZTt2ZXJCLmRpc2FibGVkPXRydWU7dmVyQi50ZXh0Q29udGVudD0iQ2hlY2tpbmfigKYiOwog"
    "ICAgZygiczMiKS5jbGFzc05hbWU9InN0ZXAgcnVuIjt0b2FzdChDTVMubXNnQ2hlY2tpbmcpOwog"
    "ICAgZGVwSm9iPXNldFRpbWVvdXQoKCk9PnsKICAgICAgYnVzeT1mYWxzZTsKICAgICAgaWYoIWFs"
    "aXZlKCkpcmV0dXJuOwogICAgICBpZihwYWlkKXsKICAgICAgICBkb25lPXRydWU7Y2xlYXJJbnRl"
    "cnZhbChkZXBUaWNrKTsKICAgICAgICBnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCBvbiI7ZygiczQi"
    "KS5jbGFzc05hbWU9InN0ZXAgb24iOwogICAgICAgIHZlckIudGV4dENvbnRlbnQ9IlZlcmlmaWVk"
    "IjsKICAgICAgICBCQUwrPU51bWJlcihhKTtzeW5jQmFsKCk7YnVyc3QoKTt0b2FzdChDTVMubXNn"
    "T2spOwogICAgICAgIHNldFRpbWVvdXQoY2xvc2VTaGVldCwxMjAwKTsKICAgICAgfWVsc2V7CiAg"
    "ICAgICAgZygiczMiKS5jbGFzc05hbWU9InN0ZXAiO3ZlckIuZGlzYWJsZWQ9ZmFsc2U7dmVyQi50"
    "ZXh0Q29udGVudD1DTVMudmVyaWZ5QnRuOwogICAgICAgIHRvYXN0KENNUy5tc2dOb25lKTsKICAg"
    "ICAgfQogICAgfSwxNzAwKTsKICB9OwogIHV0ckIub25jbGljaz0oKT0+e2lmKGxlZnQ+MCl0b2Fz"
    "dCgiVGhhbmtzIOKAlCB3ZSBhcmUgY2hlY2tpbmcgeW91ciBwYXltZW50Iik7fTsKfQpmdW5jdGlv"
    "biBzeW5jQmFsKCl7dGlja1RvKGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJiYWwiKSxCQUwpO3Rp"
    "Y2tUbyhkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYmFsMiIpLEJBTCk7fQpkb2N1bWVudC5hZGRF"
    "dmVudExpc3RlbmVyKCJjbGljayIsZT0+e2NvbnN0IGM9ZS50YXJnZXQuY2xvc2VzdCgiW2RhdGEt"
    "Y29uZmlybV0iKTtpZighYylyZXR1cm47CiAgY29uc3QgcD0rYy5kYXRhc2V0LmNvbmZpcm07aWYo"
    "QkFMPHApe3RvYXN0KCJOb3QgZW5vdWdoIGJhbGFuY2Ug4oCUIGFkZCBmdW5kcyIpO3JldHVybn0K"
    "ICBCQUwtPXA7c3luY0JhbCgpO2Nsb3NlU2hlZXQoKTtidXJzdCgpO3RvYXN0KCJQdXJjaGFzZWQg"
    "4oCUIE9UUCBpcyBpbiBPcmRlcnMiKTt9KTsKCi8qIOKUgOKUgCBBRE1JTiDilIDilIAgKi8KbGV0"
    "IEFUQUI9ImRhc2giOwpkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjYXN1YiBidXR0b24iKS5m"
    "b3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNh"
    "c3ViIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3QucmVtb3ZlKCJvbiIpKTtiLmNsYXNz"
    "TGlzdC5hZGQoIm9uIik7CiAgQVRBQj1iLmRhdGFzZXQuYTtyZW5kZXJBZG1pbigpO3dpbmRvdy5z"
    "Y3JvbGxUbygwLDApO30pOwpmdW5jdGlvbiByZW5kZXJBZG1pbigpewogIGNvbnN0IHN0PWRvY3Vt"
    "ZW50LmdldEVsZW1lbnRCeUlkKCJhc3RhZ2UiKTsKICBpZighYWRtaW5BbGxvd2VkKCkpe3N0Lmlu"
    "bmVySFRNTD0nPGRpdiBjbGFzcz0iY2FyZCIgc3R5bGU9Im1hcmdpbjoxNnB4O3RleHQtYWxpZ246"
    "Y2VudGVyIj4nCiAgICArJzxoMyBzdHlsZT0ibWFyZ2luOjAgMCA2cHgiPkFkbWlucyBvbmx5PC9o"
    "Mz4nCiAgICArJzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+VGhpcyBwYW5lbCBpcyBu"
    "b3QgYXZhaWxhYmxlIG9uIHlvdXIgYWNjb3VudC48L3A+PC9kaXY+JztyZXR1cm47fQogIHN0Lmlu"
    "bmVySFRNTD0oe2Rhc2g6YURhc2gsY2hhbm5lbHM6YUNoYW5uZWxzLGxvZ3M6YUxvZ3MscGF5OmFQ"
    "YXksY29udGVudDphQ29udGVudCxmZWF0dXJlczphRmVhdHVyZXMsbWFya2V0OmFNYXJrZXQsYW5h"
    "bHl0aWNzOmFBbmFseXRpY3Msc2VjdXJpdHk6YVNlY3VyaXR5LHN0b2NrOmFTdG9jayxvcmRlcnM6"
    "YU9yZGVycyx1c2VyczphVXNlcnMsc2V0dGluZ3M6YVNldHRpbmdzfSlbQVRBQl0oKTsKICBiaW5k"
    "QWRtaW4oKTtyZXZlYWxTdGFnZShzdCk7YmluZFRpbHQoKTtpZih3aW5kb3cuX192dGFiUGxhY2Up"
    "cmVxdWVzdEFuaW1hdGlvbkZyYW1lKHdpbmRvdy5fX3Z0YWJQbGFjZSk7fQoKZnVuY3Rpb24gYURh"
    "c2goKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImtwaXMiPiR7W1siUmV2ZW51ZSB0b2RheSIsbW9u"
    "ZXkoQURNLmtwaS5yZXYpXSxbIk9yZGVycyB0b2RheSIsYWRtTnVtKEFETS5rcGkub3JkZXJzKV0s"
    "CiAgICAgIFsiVXNlcnMiLGFkbU51bShBRE0ua3BpLnVzZXJzKV0sWyJBdXRvLXZlcmlmaWVkIixh"
    "ZG1OdW0oQURNLmtwaS5hdXRvKV1dCiAgICAubWFwKChbayx2XSk9PmA8ZGl2IGNsYXNzPSJrcGki"
    "PjxzbWFsbD4ke2t9PC9zbWFsbD48Yj4ke3Z9PC9iPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2Pgog"
    "ICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1z"
    "OmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJk"
    "b3QiPjwvaT5QYXltZW50IGNoZWNraW5nPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTtt"
    "aW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPiR7QURNLnBheS5n"
    "bWFpbF9zZXQ/IkdtYWlsIHdhdGNoZXIgb24gwrcgIitBRE0ucGF5LnBlbmRpbmcrIiBhd2FpdGlu"
    "ZyByZXZpZXciOiJHbWFpbCBub3Qgc2V0IOKAlCBkZXBvc2l0cyBnbyB0byBtYW51YWwgcmV2aWV3"
    "In08L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1hLWdvPSJwYXki"
    "Pk9wZW48L2J1dHRvbj48L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxhc3QgNyBkYXlz"
    "PC9oMz48cCBjbGFzcz0ic3ViIj5SZXZlbnVlIHRyZW5kPC9wPgogICAgIDxkaXYgc3R5bGU9ImRp"
    "c3BsYXk6ZmxleDthbGlnbi1pdGVtczpmbGV4LWVuZDtnYXA6N3B4O2hlaWdodDo5NnB4O21hcmdp"
    "bi10b3A6MTRweCI+CiAgICAgICAke0FETS5iYXJzLm1hcChoPT5gPGkgc3R5bGU9ImZsZXg6MTto"
    "ZWlnaHQ6JHtofSU7Ym9yZGVyLXJhZGl1czo3cHggN3B4IDNweCAzcHg7YmFja2dyb3VuZDp2YXIo"
    "LS1waWxsZ3JhZCk7ZGlzcGxheTpibG9jayI+PC9pPmApLmpvaW4oIiIpfTwvZGl2PjwvZGl2Pgog"
    "ICAke0coImRpZ2VzdCIsYDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZs"
    "ZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0icGlsbCBv"
    "biI+PGkgY2xhc3M9ImRvdCI+PC9pPkRpZ2VzdDwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4"
    "OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj5OZXh0IHN1"
    "bW1hcnkgYXQgMjM6MzA8L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0"
    "YS1hLWdvPSJhbmFseXRpY3MiPlZpZXc8L2J1dHRvbj48L2Rpdj5gKX0KICAgJHtHKCJzYW5kYm94"
    "IixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIj5TYW5kYm94PC9zcGFu"
    "PgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2Nv"
    "bG9yOnZhcigtLXR4MikiPiR7QURNLnNldHRpbmdzLnNhbmRib3g/IlRlc3QgbW9kZSBpcyBPTiI6"
    "IlRlc3QgbW9kZSBpcyBvZmYg4oCUIHJlYWwgbW9uZXkgaXMgbGl2ZSJ9PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5zYW5kYm94KT8iIG9uIjoiIn0iIGRhdGEtc2V0"
    "dGluZz0ic2FuZGJveCI+PC9kaXY+PC9kaXY+YCl9CiAgIDxkaXYgY2xhc3M9InNlYyI+PGgyPlF1"
    "aWNrIEFjdGlvbnM8L2gyPjwvZGl2PgogICA8ZGl2IGNsYXNzPSJncmlkIj4KICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4gc29mdCIgZGF0YS1xYT0iYnJvYWRjYXN0Ij5Ccm9hZGNhc3Q8L2J1dHRvbj4K"
    "ICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0YS1xYT0ic3RvY2siPkFkZCBTdG9jazwv"
    "YnV0dG9uPgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLXFhPSJmbGFzaCI+Rmxh"
    "c2ggU2FsZTwvYnV0dG9uPgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLXFhPSJi"
    "YWNrdXAiPkJhY2t1cCBEQjwvYnV0dG9uPjwvZGl2PmA7Cn0KCmZ1bmN0aW9uIGFDaGFubmVscygp"
    "ewogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkZvcmNlLWpvaW4gY2hhbm5lbHM8L2gz"
    "PgogICAgPHAgY2xhc3M9InN1YiI+QWRkIG9yIHJlbW92ZSBhbnkgbnVtYmVyIG9mIGNoYW5uZWxz"
    "LiBTYXZlZCB0byB0aGUgZGF0YWJhc2Ug4oCUIGluc3RhbnQsIG5vIHJlc3RhcnQuPC9wPgogICAg"
    "PGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEzcHgiPjxsYWJlbD5DaGFubmVs"
    "IGxpbmssIEB1c2VybmFtZSBvciAtMTAwIElEPC9sYWJlbD4KICAgICAgPGlucHV0IGNsYXNzPSJp"
    "bnAiIGlkPSJjaC1pbiIgcGxhY2Vob2xkZXI9IkBjaGFubmVsIMK3IHQubWUvK2ludml0ZSDCtyAt"
    "MTAw4oCmIj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+VHlwZTwvbGFiZWw+"
    "CiAgICAgIDxzZWxlY3QgY2xhc3M9ImlucCIgaWQ9ImNoLXR5cGUiPjxvcHRpb24gdmFsdWU9IjEi"
    "PlJlcXVpcmVkIOKAlCBtdXN0IGpvaW48L29wdGlvbj48b3B0aW9uIHZhbHVlPSIwIj5PcHRpb25h"
    "bCDigJQganVzdCBzdWdnZXN0ZWQ8L29wdGlvbj48L3NlbGVjdD48L2Rpdj4KICAgIDxkaXYgY2xh"
    "c3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0iY2gtYWRkIj4ke2ljKCJwbHVzIil9"
    "IEFkZCBDaGFubmVsPC9idXR0b24+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEt"
    "bWlzYz0iY2hhbm5lbHRlc3QiPlRlc3QgQm90PC9idXR0b24+PC9kaXY+PC9kaXY+CiAgPGRpdiBj"
    "bGFzcz0ic2VjIj48aDI+QWN0aXZlICgke0NIQU5ORUxTLmxlbmd0aH0pPC9oMj48L2Rpdj4KICAk"
    "e0NIQU5ORUxTLm1hcCgoYyxpKT0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiPgogICAgIDxkaXYg"
    "c3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAg"
    "PGRpdiBjbGFzcz0iZmxhZyI+JHtpYygic2VuZCIpfTwvZGl2PgogICAgICAgPGRpdiBzdHlsZT0i"
    "ZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44M3JlbTtkaXNwbGF5OmJs"
    "b2NrO3doaXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4dC1vdmVyZmxvdzplbGxp"
    "cHNpcyI+JHtlc2MoYy5uYW1lKX08L2I+CiAgICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFy"
    "KC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4ke2VzYyhjLmhhbmRsZSl9IMK3ICR7Yy5tZW1iZXJz"
    "fSBtZW1iZXJzPC9zbWFsbD48L2Rpdj4KICAgICAgIDxkaXYgY2xhc3M9InRnIHNtICR7Yy5vbj8i"
    "b24iOiIifSIgZGF0YS1jaG9uPSIke2MuaWR9Ij48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IHN0eWxl"
    "PSJkaXNwbGF5OmZsZXg7Z2FwOjZweDttYXJnaW4tdG9wOjEwcHg7ZmxleC13cmFwOndyYXAiPgog"
    "ICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzICR7Yy5yZXE/IiI6InNvZnQifSIgZGF0YS1jaHJl"
    "cT0iJHtjLmlkfSI+JHtjLnJlcT8iUmVxdWlyZWQiOiJPcHRpb25hbCJ9PC9idXR0b24+CiAgICAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1jb3B5PSJ0ZXh0IiBkYXRhLWNvcHkt"
    "dmFsdWU9IiR7ZXNjKGMuaGFuZGxlKX0iPkNvcHk8L2J1dHRvbj4KICAgICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biB4cyBiYWQiIHN0eWxlPSJtYXJnaW4tbGVmdDphdXRvIiBkYXRhLWNoZGVsPSIke2Mu"
    "aWR9Ij5SZW1vdmU8L2J1dHRvbj48L2Rpdj48L2Rpdj5gKS5qb2luKCIiKQogICB8fCc8ZGl2IGNs"
    "YXNzPSJjYXJkIj48cCBjbGFzcz0ic3ViIj5ObyBjaGFubmVscyDigJQgdXNlcnMgY2FuIGVudGVy"
    "IGZyZWVseS48L3A+PC9kaXY+J31gOwp9CgpmdW5jdGlvbiBhTG9ncygpewogIGNvbnN0IG9wdHM9"
    "aWQ9PkxPR0NILm1hcChsPT5gPG9wdGlvbiB2YWx1ZT0iJHtsLmlkfSIgJHtsLmlkPT09aWQ/InNl"
    "bGVjdGVkIjoiIn0+JHtlc2MobC5uYW1lKX08L29wdGlvbj5gKS5qb2luKCIiKTsKICByZXR1cm4g"
    "YDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDow"
    "Ij4KICAgICAgPGRpdiBjbGFzcz0idCI+TG9nZ2luZyBtYXN0ZXIgc3dpdGNoPHNtYWxsPk9mZiA9"
    "IHRoZSBib3QgcG9zdHMgbm8gbG9ncyBhbnl3aGVyZTwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYg"
    "Y2xhc3M9InRnICR7TE9HU19NQVNURVI/Im9uIjoiIn0iIGRhdGEtbWFzdGVyPjwvZGl2PjwvZGl2"
    "PjwvZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+TG9nIGNoYW5uZWxzPC9oMz48cCBjbGFz"
    "cz0ic3ViIj5BZGQgYXMgbWFueSBhcyB5b3UgbGlrZS4gVGhlIHN0YXJyZWQgb25lIGlzIHRoZSBw"
    "cmVmZXJyZWQvZGVmYXVsdCBjaGFubmVsLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPkNoYW5uZWwgbGluaywgQHVzZXJuYW1lIG9yIC0x"
    "MDAgSUQ8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGlkPSJsZy1pbiIgcGxhY2Vo"
    "b2xkZXI9IkBsb2dzX2NoYW5uZWwgwrcgLTEwMOKApiI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "YnRucm93Ij48YnV0dG9uIGNsYXNzPSJidG4iIGlkPSJsZy1hZGQiPiR7aWMoInBsdXMiKX0gQWRk"
    "IExvZyBDaGFubmVsPC9idXR0b24+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEt"
    "bWlzYz0ibG9ndGVzdCI+U2VuZCBUZXN0PC9idXR0b24+PC9kaXY+PC9kaXY+CiAgICR7TE9HQ0gu"
    "bWFwKGw9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgIDxkaXYgY2xhc3M9InN0YXIgJHtsLnByaW1h"
    "cnk/Im9uIjoiIn0iIGRhdGEtbGdzdGFyPSIke2wuaWR9Ij4ke2ljKCJzdGFyIil9PC9kaXY+CiAg"
    "ICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTou"
    "ODNyZW07ZGlzcGxheTpibG9jazt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3Rl"
    "eHQtb3ZlcmZsb3c6ZWxsaXBzaXMiPiR7ZXNjKGwubmFtZSl9PC9iPgogICAgICAgIDxzbWFsbCBz"
    "dHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4ke2VzYyhsLmhhbmRsZSl9"
    "JHtsLnByaW1hcnk/IiDCtyBwcmVmZXJyZWQiOiIifTwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYg"
    "Y2xhc3M9InRnIHNtICR7bC5vbj8ib24iOiIifSIgZGF0YS1sZ29uPSIke2wuaWR9Ij48L2Rpdj4K"
    "ICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIGJhZCIgZGF0YS1sZ2RlbD0iJHtsLmlkfSI+UmVt"
    "b3ZlPC9idXR0b24+PC9kaXY+YCkuam9pbigiIikKICAgIHx8JzxkaXYgY2xhc3M9ImNhcmQiPjxw"
    "IGNsYXNzPSJzdWIiPk5vIGxvZyBjaGFubmVscyB5ZXQuPC9wPjwvZGl2Pid9CiAgIDxkaXYgY2xh"
    "c3M9InNlYyI+PGgyPkV2ZW50cyAmYW1wOyByb3V0aW5nPC9oMj48YSBkYXRhLWV2LWFsbD5Ub2dn"
    "bGUgYWxsPC9hPjwvZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJkIj4ke0VWRU5UUy5tYXAoKGUsaSk9"
    "PmAKICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0IiBzdHlsZT0iZmxleDoxIj4k"
    "e2VzYyhlLmspfQogICAgICAgPHNtYWxsPjxzZWxlY3QgY2xhc3M9ImlucCIgc3R5bGU9ImhlaWdo"
    "dDozMHB4O2ZvbnQtc2l6ZTouN3JlbTttYXJnaW4tdG9wOjVweDttYXgtd2lkdGg6MTkwcHgiIGRh"
    "dGEtZXZjaD0iJHtpfSIgJHtlLm9uPyIiOiJkaXNhYmxlZCJ9PiR7b3B0cyhlLmNoKX08L3NlbGVj"
    "dD48L3NtYWxsPjwvZGl2PgogICAgICAgPGRpdiBjbGFzcz0idGcgJHtlLm9uPyJvbiI6IiJ9IiBk"
    "YXRhLWV2b249IiR7aX0iPjwvZGl2PjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PgogICA8ZGl2IGNs"
    "YXNzPSJjYXJkIj48aDM+UmVjZW50IGxvZyBvdXRwdXQ8L2gzPgogICAgICR7KChBRE0ubG9nZmVl"
    "ZCYmQURNLmxvZ2ZlZWQubGVuZ3RoKT9BRE0ubG9nZmVlZC5tYXAobD0+W2wud2hhdCxsLndoZXJl"
    "LGwud2hlbl0pOltdKQogICAgICAgLm1hcCgoW3QsYyx3XSk9PmA8ZGl2IGNsYXNzPSJrdiI+PHNw"
    "YW4+JHt0fTwvc3Bhbj48YiBzdHlsZT0iZm9udC1zaXplOi43cmVtIj4ke2N9IMK3ICR7d308L2I+"
    "PC9kaXY+YCkuam9pbigiIil9PC9kaXY+YDsKfQoKZnVuY3Rpb24gYVBheSgpewogIHJldHVybiBg"
    "PGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAi"
    "PgogICAgICA8ZGl2IGNsYXNzPSJ0Ij5HbWFpbCBhdXRvLXZlcmlmeTxzbWFsbD4ke0FETS5wYXku"
    "Z21haWxfc2V0PyJSZWFkaW5nICIrZXNjKEFETS5wYXkuZ21haWwpOiJOb3QgY29uZmlndXJlZCDi"
    "gJQgZGVwb3NpdHMgcXVldWUgZm9yIHJldmlldyJ9PC9zbWFsbD48L2Rpdj4KICAgICAgPGRpdiBj"
    "bGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5nbWFpbF9lbmFibGVkKT8iIG9uIjoiIn0iIGRhdGEt"
    "c2V0dGluZz0iZ21haWxfZW5hYmxlZCI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ic3dh"
    "cCI+PGRpdiBjbGFzcz0idCI+QXV0by1hcHByb3ZlIHNtYWxsIGRlcG9zaXRzPHNtYWxsPlNraXAg"
    "cmV2aWV3IGJlbG93IHRoZSB0aHJlc2hvbGQ8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "InRnJHsoQURNLnN3JiZBRE0uc3cuZ21haWxfZW5hYmxlZCk/IiBvbiI6IiJ9IiBkYXRhLXNldHRp"
    "bmc9ImdtYWlsX2VuYWJsZWQiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InN3YXAiPjxk"
    "aXYgY2xhc3M9InQiPk1hbnVhbCBVVFIgZmFsbGJhY2s8c21hbGw+TGV0IHVzZXJzIHN1Ym1pdCBh"
    "IFVUUiBpZiBhdXRvLXZlcmlmeSBtaXNzZXM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "InRnJHsoQURNLnN3JiZBRE0uc3cuYXV0b2RlcCk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImF1"
    "dG9kZXAiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQi"
    "PlZlcmlmeSBvbmx5IHdoZW4gdXNlciB0YXBzIFZlcmlmeTxzbWFsbD5ObyBiYWNrZ3JvdW5kIGNo"
    "ZWNraW5nIOKAlCBzYXZlcyBHbWFpbCBxdW90YTwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFz"
    "cz0idGckeyhBRE0uc3cmJkFETS5zdy51dHJfZmFsbGJhY2spPyIgb24iOiIifSIgZGF0YS1zZXR0"
    "aW5nPSJ1dHJfZmFsbGJhY2siPjwvZGl2PjwvZGl2PjwvZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJk"
    "Ij48aDM+VVBJPC9oMz48cCBjbGFzcz0ic3ViIj4ke0FETS5wYXkudXBpPyJJbiB1c2UgYnkgZXZl"
    "cnkgcGF5bWVudCByZXF1ZXN0LiI6Ik5vdCBzZXQgeWV0IOKAlCB0b3AtdXBzIGFyZSBkaXNhYmxl"
    "ZCB1bnRpbCB5b3UgYWRkIGl0LiJ9PC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+VVBJIElEPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFz"
    "cz0iaW5wIiBkYXRhLXNldD0idXBpX2lkIiB2YWx1ZT0iJHtlc2MoQURNLnBheS51cGl8fCIiKX0i"
    "IHBsYWNlaG9sZGVyPSJ5b3VyaGFuZGxlQGJhbmsiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZp"
    "ZWxkIj48bGFiZWw+UGF5ZWUgbmFtZTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIg"
    "ZGF0YS1zZXQ9InVwaV9uYW1lIiB2YWx1ZT0iJHtlc2MoQURNLnBheS5wYXllZXx8IiIpfSIgcGxh"
    "Y2Vob2xkZXI9IllvdXIgU3RvcmUiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFi"
    "ZWw+Tm90ZSBwcmVmaXg8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0"
    "PSJwYXlfcHJlZml4IiB2YWx1ZT0iJHtlc2MoQURNLnBheS5wcmVmaXh8fCJQQVkiKX0iPjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+UGF5bWVudCB3aW5kb3cgKG1pbnV0ZXMp"
    "PC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0icGF5X3dpbmRvdyIg"
    "dmFsdWU9IiR7QURNLnBheS53aW5kb3d9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+"
    "PGxhYmVsPk1pbmltdW0gZGVwb3NpdDwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIg"
    "ZGF0YS1zZXQ9Im1pbl9kZXBvc2l0IiB2YWx1ZT0iJHtBRE0ucGF5Lm1pbn0iPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+TWF4aW11bSBkZXBvc2l0PC9sYWJlbD4KICAgICAg"
    "IDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0ibWF4X2RlcG9zaXQiIHZhbHVlPSIke0FETS5w"
    "YXkubWF4fSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5BdXRvLWFwcHJv"
    "dmUgdW5kZXIgKDAgPSBvZmYpPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRh"
    "LXNldD0iYXV0b2RlcF9tYXgiIHZhbHVlPSIke0FETS5wYXkuYXV0b2RlcH0iPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+R21haWwgYWRkcmVzczwvbGFiZWw+CiAgICAgICA8"
    "aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9ImdtYWlsX2lkIiB2YWx1ZT0iJHtlc2MoQURNLnBh"
    "eS5nbWFpbHx8IiIpfSIgcGxhY2Vob2xkZXI9InN0b3JlQGdtYWlsLmNvbSI+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5HbWFpbCBhcHAgcGFzc3dvcmQ8L2xhYmVsPgogICAg"
    "ICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJnbWFpbF9wYXNzIiB0eXBlPSJwYXNzd29y"
    "ZCIgcGxhY2Vob2xkZXI9IiR7QURNLnBheS5nbWFpbF9zZXQ/InNhdmVkIOKAlCB0eXBlIHRvIHJl"
    "cGxhY2UiOiJub3Qgc2V0In0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5XYWl0"
    "aW5nIGZvciByZXZpZXc8L3NwYW4+PGI+JHtBRE0ucGF5LnBlbmRpbmd9PC9iPjwvZGl2PjwvZGl2"
    "PgogICAke0coImNyeXB0byIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5DcnlwdG8gd2FsbGV0PC9o"
    "Mz48cCBjbGFzcz0ic3ViIj5VU0RULVRSQzIwIGRlcG9zaXRzLCBhdXRvLWNvbmZpcm1lZCBvbi1j"
    "aGFpbi48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgi"
    "PjxsYWJlbD5XYWxsZXQgYWRkcmVzczwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHBsYWNlaG9s"
    "ZGVyPSJU4oCmIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkNvbmZpcm1h"
    "dGlvbnMgcmVxdWlyZWQ8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMSI+PC9kaXY+"
    "CiAgICAgPC9kaXY+YCl9CiAgICR7RygidXBpUm90YXRlIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgz"
    "PlVQSSByb3RhdGlvbjwvaDM+PHAgY2xhc3M9InN1YiI+U3ByZWFkIGxvYWQgYW5kIHN1cnZpdmUg"
    "YSBmcm96ZW4gaGFuZGxlLjwvcD4KICAgICAke1tbImhhbmRsZTFAZmFtIiwiQWN0aXZlIl0sWyJo"
    "YW5kbGUyQGZhbSIsIkFjdGl2ZSJdLFsiaGFuZGxlM0B1cGkiLCJQYXVzZWQiXV0KICAgICAgIC5t"
    "YXAoKFtoLHN0XSk9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHtofTwvc3Bhbj48Yj48c3BhbiBj"
    "bGFzcz0icGlsbCAke3N0PT09IkFjdGl2ZSI/Im9uIjoib2ZmIn0iPiR7c3R9PC9zcGFuPjwvYj48"
    "L2Rpdj5gKS5qb2luKCIiKX0KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10"
    "b3A6MTBweCI+PGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo3cHgiPgogICAgICAgPGlucHV0"
    "IGNsYXNzPSJpbnAiIHBsYWNlaG9sZGVyPSJhbm90aGVyQHVwaSI+PGJ1dHRvbiBjbGFzcz0iYnRu"
    "IHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9InVwaWFkZCI+QWRkPC9idXR0b24+PC9kaXY+"
    "PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiZGVwQm9udXMiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+"
    "RGVwb3NpdCBib251czwvaDM+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjhweCI+PGxhYmVsPuKCuTUwMCBhbmQgYWJvdmUgKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9"
    "ImlucCIgdmFsdWU9IjUiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+4oK5"
    "MTAwMCBhbmQgYWJvdmUgKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjgiPjwv"
    "ZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+4oK5MjAwMCBhbmQgYWJvdmUgKCUp"
    "PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEyIj48L2Rpdj48L2Rpdj5gKX0KICAg"
    "JHtHKCJkZXBMaW1pdHMiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+RGVwb3NpdCBsaW1pdHM8L2gz"
    "PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5N"
    "aW5pbXVtICjigrkpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwIj48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPk1heGltdW0gKOKCuSk8L2xhYmVsPjxpbnB1"
    "dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMjAwMDAiPjwvZGl2PjwvZGl2PmApfQogICAke0coIndyb25n"
    "QW10IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmct"
    "dG9wOjAiPjxkaXYgY2xhc3M9InQiPldyb25nLWFtb3VudCBoYW5kbGluZzxzbWFsbD5DcmVkaXQg"
    "d2hhdCB3YXMgYWN0dWFsbHkgcGFpZCBhbmQgZmxhZyBpdDwvc21hbGw+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy52ZXJpZnlfb25fdGFwKT8iIG9uIjoiIn0iIGRh"
    "dGEtc2V0dGluZz0idmVyaWZ5X29uX3RhcCI+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7Rygi"
    "c3BsaXRQYXkiLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFk"
    "ZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+U3BsaXQgcGF5bWVudDxzbWFsbD5XYWxsZXQgYmFs"
    "YW5jZSBwbHVzIFVQSSBpbiBvbmUgY2hlY2tvdXQ8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xh"
    "c3M9InRnJHtGRUFULndyb25nQW10PyIgb24iOiIifSIgZGF0YS1mZWF0PSJ3cm9uZ0FtdCI+PC9k"
    "aXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiYXV0b1JlZnVuZCIsYDxkaXYgY2xhc3M9ImNhcmQi"
    "PjxoMz5BdXRvLXJlZnVuZDwvaDM+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjhweCI+PGxhYmVsPldhcnJhbnR5IHdpbmRvdyAobWludXRlcyk8L2xhYmVsPjxpbnB1"
    "dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMTAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bh"
    "bj5SZWZ1bmRlZCB0b2RheTwvc3Bhbj48Yj7igrkxMDUuMDAgwrcgMyBvcmRlcnM8L2I+PC9kaXY+"
    "PC9kaXY+YCl9CiAgIDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5HbWFpbCBhY2NvdW50PC9oMz48cCBj"
    "bGFzcz0ic3ViIj5UaGUgR21haWwgdGhhdCByZWNlaXZlcyB5b3VyIEZhbVBheSBwYXltZW50IG1h"
    "aWxzLiBOb3RoaW5nIGlzIHN0b3JlZCBpbiBjb2RlLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPkdtYWlsIElEPC9sYWJlbD48aW5wdXQg"
    "Y2xhc3M9ImlucCIgcGxhY2Vob2xkZXI9InlvdXJzdG9yZUBnbWFpbC5jb20iPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+R21haWwgcGFzc3dvcmQ8L2xhYmVsPjxpbnB1dCBj"
    "bGFzcz0iaW5wIiB0eXBlPSJwYXNzd29yZCIgcGxhY2Vob2xkZXI9IuKAouKAouKAouKAouKAouKA"
    "ouKAouKAouKAouKAouKAouKAoiI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93Ij48YnV0"
    "dG9uIGNsYXNzPSJidG4iIGRhdGEtYWRtaW4tYWN0PSJnbWFpbHRlc3QiPlRlc3QgR21haWw8L2J1"
    "dHRvbj4KICAgICA8L2Rpdj4KICAgICA8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "MHB4Ij5UaXA6IGlmIDItc3RlcCB2ZXJpZmljYXRpb24gaXMgb24sIHBhc3RlIGEgR29vZ2xlIEFw"
    "cCBQYXNzd29yZCBoZXJlIGluc3RlYWQgb2YgeW91ciBub3JtYWwgb25lLjwvcD48L2Rpdj4KICAg"
    "PGRpdiBjbGFzcz0ic2VjIj48aDI+UmVjZW50IGF1dG8tdmVyaWZpY2F0aW9uczwvaDI+PC9kaXY+"
    "CiAgICR7KEFETS5kZXBvc2l0c3x8W10pLmxlbmd0aD9BRE0uZGVwb3NpdHMubWFwKGQ9PmA8ZGl2"
    "IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRl"
    "cjtnYXA6MTBweCI+CiAgICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJ3YWxsZXQiKX08L2Rp"
    "dj4KICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQt"
    "c2l6ZTouODJyZW0iPiR7bW9uZXkoZC5hbXQpfSDCtyAke2VzYyhkLnVzZXIpfTwvYj48YnI+CiAg"
    "ICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4k"
    "e2VzYyhkLnV0cnx8Im5vIHJlZmVyZW5jZSB5ZXQiKX08L3NtYWxsPjwvZGl2PgogICAgICAgPHNw"
    "YW4gY2xhc3M9InBpbGwgJHtkLnN0PT09IkNvbXBsZXRlZCI/Im9uIjoiaG90In0iPiR7ZXNjKGQu"
    "c3QpfTwvc3Bhbj4KICAgICAgICR7ZC5zdD09PSJDb21wbGV0ZWQiPyIiOmA8YnV0dG9uIGNsYXNz"
    "PSJidG4geHMiIGRhdGEtZGVwLW9rPSIke2QuaWR9Ij5BcHByb3ZlPC9idXR0b24+YH08L2Rpdj5g"
    "KS5qb2luKCIiKQogICAgIDonPGRpdiBjbGFzcz0iY2FyZCB0aWdodCI+PHAgY2xhc3M9InN1YiIg"
    "c3R5bGU9Im1hcmdpbjowIj5ObyBkZXBvc2l0cyB5ZXQuPC9wPjwvZGl2Pid9YDsKfQoKZnVuY3Rp"
    "b24gYUNvbnRlbnQoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5FZGl0IGFwcCB0"
    "ZXh0PC9oMz4KICAgICA8cCBjbGFzcz0ic3ViIj5FdmVyeSB3b3JkIHVzZXJzIHNlZSDigJQgY2hh"
    "bmdlIGl0IGhlcmUgYW5kIHRoZSBzdG9yZSB1cGRhdGVzIGluc3RhbnRseS48L3A+CiAgICAgPGRp"
    "diBjbGFzcz0ibXNlYXJjaCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGRpdiBjbGFzcz0iZmll"
    "bGR3cmFwIj4ke2ljKCJzZWFyY2giKX08aW5wdXQgaWQ9ImNxIiBwbGFjZWhvbGRlcj0iRmluZCB0"
    "ZXh0IHRvIGVkaXTigKYiPjwvZGl2PgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIHNvZnQi"
    "IGlkPSJjbXNyZXNldCI+UmVzZXQ8L2J1dHRvbj48L2Rpdj4KICAgICA8cCBjbGFzcz0ic3ViIiBz"
    "dHlsZT0ibWFyZ2luLXRvcDoxMHB4Ij5Vc2UgJmx0O2JyJmd0OyBmb3IgYSBsaW5lIGJyZWFrLCAm"
    "bHQ7YiZndDt3b3JkJmx0Oy9iJmd0OyBmb3IgYm9sZC4ge259ID0gbGl2ZSBjb3VudHJ5IGNvdW50"
    "LCB7bX0gPSBwYXltZW50IG1pbnV0ZXMuPC9wPjwvZGl2PgogICAke0NNU01FVEEubWFwKChbZ3Jw"
    "LHJvd3NdKT0+YDxkaXYgY2xhc3M9ImNhcmQgY21zZ3JwIj48aDM+JHtncnB9PC9oMz4KICAgICAk"
    "e3Jvd3MubWFwKChbayxsYmxdKT0+YDxkaXYgY2xhc3M9ImZpZWxkIGNtc3JvdyIgZGF0YS1mPSIk"
    "e2VzYygobGJsKyIgIitDTVNba10rIiAiK2dycCkudG9Mb3dlckNhc2UoKSl9Ij4KICAgICAgICA8"
    "bGFiZWw+JHtsYmx9PC9sYWJlbD4KICAgICAgICA8dGV4dGFyZWEgY2xhc3M9ImlucCIgcm93cz0i"
    "JHtDTVNba10ubGVuZ3RoPjQwPzI6MX0iIGRhdGEtY21zPSIke2t9Ij4ke2VzYyhDTVNba10pfTwv"
    "dGV4dGFyZWE+PC9kaXY+YCkuam9pbigiIil9CiAgIDwvZGl2PmApLmpvaW4oIiIpfQogICA8ZGl2"
    "IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRl"
    "cjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwv"
    "aT5MaXZlIGVkaXQ8L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2Zv"
    "bnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+Q2hhbmdlcyBhcHBseSB0byB0aGUgU3Rv"
    "cmUgdGFiIHN0cmFpZ2h0IGF3YXk8L2Rpdj4KICAgICA8L2Rpdj5gOwp9CgpmdW5jdGlvbiBhRmVh"
    "dHVyZXMoKXsKICBjb25zdCBvbj1PYmplY3QudmFsdWVzKEZFQVQpLmZpbHRlcihCb29sZWFuKS5s"
    "ZW5ndGgsdG90PU9iamVjdC5rZXlzKEZFQVQpLmxlbmd0aDsKICByZXR1cm4gYDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxoMz5GZWF0dXJlIHN3aXRjaGVzPC9oMz4KICAgICA8cCBjbGFzcz0ic3ViIj5UdXJu"
    "IGFueXRoaW5nIG9mZiBhbmQgaXQgZGlzYXBwZWFycyBmcm9tIHRoZSBhcHAgY29tcGxldGVseSDi"
    "gJQgbm8gYnV0dG9ucywgbm8gbWVudXMsIG5vIGxlZnRvdmVycy48L3A+CiAgICAgPGRpdiBjbGFz"
    "cz0ia3YiPjxzcGFuPkVuYWJsZWQ8L3NwYW4+PGIgaWQ9ImZjb3VudCI+JHtvbn0gb2YgJHt0b3R9"
    "PC9iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Im1zZWFyY2giIHN0eWxlPSJtYXJnaW4tdG9wOjEy"
    "cHgiPjxkaXYgY2xhc3M9ImZpZWxkd3JhcCI+JHtpYygic2VhcmNoIil9PGlucHV0IGlkPSJmcSIg"
    "cGxhY2Vob2xkZXI9IlNlYXJjaCBmZWF0dXJlc+KApiI+PC9kaXY+CiAgICAgICA8YnV0dG9uIGNs"
    "YXNzPSJidG4gc20gc29mdCIgaWQ9ImZhbGwiPkFsbCBvbjwvYnV0dG9uPjwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48YnV0dG9uIGNsYXNzPSJi"
    "dG4gc20gZ2hvc3QiIGlkPSJmbm9uZSI+QWxsIG9mZjwvYnV0dG9uPgogICAgICAgPGJ1dHRvbiBj"
    "bGFzcz0iYnRuIHNtIHNvZnQiIGlkPSJmcmVzZXQiPlJlc2V0PC9idXR0b24+PC9kaXY+PC9kaXY+"
    "CiAgICR7RkdST1VQUy5tYXAoKFtncnAscm93c10pPT5gPGRpdiBjbGFzcz0iY2FyZCBmZ3JwIj48"
    "aDM+JHtncnB9PC9oMz4KICAgICAke3Jvd3MubWFwKChbayxsLGRdKT0+YDxkaXYgY2xhc3M9ImZj"
    "YXJkIGZyb3ciIGRhdGEtZj0iJHtlc2MoKGwrIiAiK2QrIiAiK2dycCkudG9Mb3dlckNhc2UoKSl9"
    "Ij4KICAgICAgICA8ZGl2IGNsYXNzPSJ0Ij48Yj4ke2x9PC9iPjxzbWFsbD4ke2R9PC9zbWFsbD48"
    "L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJ0ZyAke0ZFQVRba10/Im9uIjoiIn0iIGRhdGEtZmVh"
    "dD0iJHtrfSI+PC9kaXY+PC9kaXY+YCkuam9pbigiIil9CiAgIDwvZGl2PmApLmpvaW4oIiIpfQog"
    "ICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1z"
    "OmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJk"
    "b3QiPjwvaT5MaXZlPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtm"
    "b250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlN3aXRjaGVzIGFwcGx5IHRvIHRoZSBT"
    "dG9yZSB0YWIgaW5zdGFudGx5PC9kaXY+CiAgICAgPC9kaXY+YDsKfQoKZnVuY3Rpb24gYU1hcmtl"
    "dCgpewogIHJldHVybiBgJHtHKCJjb3Vwb25zIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkNvdXBv"
    "bnM8L2gzPjxwIGNsYXNzPSJzdWIiPlBlcmNlbnRhZ2Ugb2ZmIGF0IGNoZWNrb3V0LjwvcD4KICAg"
    "ICAkeyhBRE0uY291cG9ucyYmQURNLmNvdXBvbnMubGVuZ3RoKT9BRE0uY291cG9ucy5tYXAoYz0+"
    "YDxkaXYgY2xhc3M9Imt2Ij4KICAgICAgIDxzcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCki"
    "PiR7ZXNjKGMuY29kZSl9PC9iPiDCtyAke2MucGVyY2VudH0lIMK3ICR7Yy51c2VkfS8ke2MubWF4"
    "fHwi4oieIn08L3NwYW4+CiAgICAgICA8Yj48c3BhbiBjbGFzcz0icGlsbCAke2MuYWN0aXZlPyJv"
    "biI6Im9mZiJ9Ij4ke2MuYWN0aXZlPyJBY3RpdmUiOiJPZmYifTwvc3Bhbj48L2I+PC9kaXY+YCku"
    "am9pbigiIikKICAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBjb3Vw"
    "b25zIHlldC48L3A+J30KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTJweCI+PGxhYmVsPk5ldyBjb2RlPC9sYWJlbD4KICAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6"
    "ZmxleDtnYXA6N3B4Ij48aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImNwY29kZSIgcGxhY2Vob2xkZXI9"
    "Ik5FV1lFQVIiPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGlkPSJjcHBjdCIgcGxhY2Vob2xk"
    "ZXI9IiUiIHN0eWxlPSJ3aWR0aDo2NnB4Ij4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbSIg"
    "c3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJjcGFkZCI+QWRkPC9idXR0b24+PC9kaXY+PC9kaXY+"
    "PC9kaXY+YCl9CiAgICR7RygiZmxhc2hTYWxlIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkZsYXNo"
    "IHNhbGUgc2NoZWR1bGVyPC9oMz48cCBjbGFzcz0ic3ViIj5TdGFydHMsIGFubm91bmNlcyBhbmQg"
    "ZW5kcyBieSBpdHNlbGYuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2lu"
    "LXRvcDoxMnB4Ij48bGFiZWw+RGlzY291bnQgKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIg"
    "dmFsdWU9IjIwIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlN0YXJ0czwv"
    "bGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSJUb2RheSAxODowMCI+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5FbmRzPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIg"
    "dmFsdWU9IlRvZGF5IDIzOjAwIj48L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2si"
    "IGlkPSJmbGFzaGdvIj5TdGFydCBGbGFzaCBTYWxlPC9idXR0b24+PC9kaXY+YCl9CiAgICR7Rygi"
    "c2NoZWRCcm9hZGNhc3QiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+U2NoZWR1bGVkIGJyb2FkY2Fz"
    "dDwvaDM+PHAgY2xhc3M9InN1YiI+UXVldWUgaXQgYW5kIHRhcmdldCBhIHNlZ21lbnQuPC9wPgog"
    "ICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+U2Vn"
    "bWVudDwvbGFiZWw+CiAgICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiPjxvcHRpb24+RXZlcnlvbmU8"
    "L29wdGlvbj48b3B0aW9uPkJ1eWVycyBvbmx5PC9vcHRpb24+PG9wdGlvbj5OZXZlciBib3VnaHQ8"
    "L29wdGlvbj48b3B0aW9uPkJpZyBzcGVuZGVycyAo4oK5MTAwMCspPC9vcHRpb24+PG9wdGlvbj5J"
    "bmFjdGl2ZSAzMCBkYXlzPC9vcHRpb24+PC9zZWxlY3Q+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "ZmllbGQiPjxsYWJlbD5TZW5kIGF0PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IlRv"
    "bW9ycm93IDEwOjAwIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPk1lc3Nh"
    "Z2U8L2xhYmVsPjx0ZXh0YXJlYSBjbGFzcz0iaW5wIiByb3dzPSIyIiBwbGFjZWhvbGRlcj0iV3Jp"
    "dGUgeW91ciBicm9hZGNhc3TigKYiPjwvdGV4dGFyZWE+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "YnRucm93Ij48YnV0dG9uIGNsYXNzPSJidG4iIGlkPSJiY2dvIj5TZW5kIEJyb2FkY2FzdDwvYnV0"
    "dG9uPgogICAgICAgPC9kaXY+PC9kaXY+YCl9CiAgICR7Rygid2luYmFjayIsYDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNz"
    "PSJ0Ij5XaW4tYmFjayBtZXNzYWdlczxzbWFsbD5BdXRvLURNIHVzZXJzIGluYWN0aXZlIGZvciA3"
    "IGFuZCAzMCBkYXlzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5zcGxp"
    "dFBheT8iIG9uIjoiIn0iIGRhdGEtZmVhdD0ic3BsaXRQYXkiPjwvZGl2PjwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RGlzY291bnQgb2ZmZXJlZCAoJSk8L2xhYmVsPjxpbnB1"
    "dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMTAiPjwvZGl2PjwvZGl2PmApfQogICAke0coImFiYW5kb25S"
    "ZW1pbmQiLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNs"
    "YXNzPSJkb3QiPjwvaT5SZW1pbmRlcnM8L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21p"
    "bi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+NCB1bmZpbmlzaGVk"
    "IHBheW1lbnRzIG51ZGdlZCB0b2RheTwvZGl2PgogICAgIDwvZGl2PmApfQogICAke0coImxveWFs"
    "dHkiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+TG95YWx0eSBwb2ludHM8L2gzPgogICAgIDxkaXYg"
    "Y2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5Qb2ludHMgcGVyIOKC"
    "uTEwIHNwZW50PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEiPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+UG9pbnRzIG5lZWRlZCBmb3Ig4oK5MTA8L2xhYmVs"
    "PjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMTAwIj48L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNz"
    "PSJidG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0id2luYmFja3NhdmUiPlNhdmU8L2J1"
    "dHRvbj48L2Rpdj5gKX0KICAgJHthbnlGKCJyZWZEYXNoIiwicmVmTDIiKT9gPGRpdiBjbGFzcz0i"
    "Y2FyZCI+PGgzPlJlZmVycmFsIHBheW91dHM8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBz"
    "dHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5MZXZlbCAxIGNvbW1pc3Npb24gKCUpPC9sYWJl"
    "bD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjUiPjwvZGl2PgogICAgICR7RygicmVmTDIiLGA8"
    "ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkxldmVsIDIgY29tbWlzc2lvbiAoJSk8L2xhYmVsPjxp"
    "bnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMSI+PC9kaXY+YCl9CiAgICAgPGRpdiBjbGFzcz0ia3Yi"
    "PjxzcGFuPlBhaWQgb3V0IHRoaXMgbW9udGg8L3NwYW4+PGI+4oK5MywyNDA8L2I+PC9kaXY+PC9k"
    "aXY+YDoiIn0KICAgJHtHKCJzYWxlc0ZlZWQiLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNz"
    "PSJzd2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+QXV0by1wb3N0IHNh"
    "bGVzIHRvIGNoYW5uZWw8c21hbGw+JHtGRUVELmxlbmd0aD9lc2MoRkVFRFswXVsxXSsiIHNvbGQg"
    "wrcgIitGRUVEWzBdWzNdKToibm8gc2FsZXMgeWV0In08L3NtYWxsPjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9InRnJHtGRUFULndpbmJhY2s/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9IndpbmJhY2siPjwv"
    "ZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImxlYWRlcmJvYXJkIixgPGRpdiBjbGFzcz0iY2Fy"
    "ZCI+PGgzPkxlYWRlcmJvYXJkIHByaXplPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5"
    "bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+V2Vla2x5IHByaXplICjigrkpPC9sYWJlbD48aW5w"
    "dXQgY2xhc3M9ImlucCIgdmFsdWU9IjUwMCI+PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9InByaXplc2F2ZSI+U2F2ZTwvYnV0dG9uPjwv"
    "ZGl2PmApfWA7Cn0KCmZ1bmN0aW9uIGFBbmFseXRpY3MoKXsKICBjb25zdCB3ZWVrPShBRE0uc2Vy"
    "aWVzfHxbXSkucmVkdWNlKChhLGIpPT5hK2IsMCk7CiAgY29uc3QgYXZnPUFETS5rcGkub3JkZXJz"
    "P0FETS5rcGkucmV2L0FETS5rcGkub3JkZXJzOjA7CiAgcmV0dXJuIGAke0coInJldkNoYXJ0Iixg"
    "PGRpdiBjbGFzcz0iY2FyZCI+PGgzPlJldmVudWU8L2gzPjxwIGNsYXNzPSJzdWIiPkxhc3QgNyBk"
    "YXlzIMK3ICR7bW9uZXkod2Vlayl9IHRvdGFsPC9wPgogICAgIDxkaXYgY2xhc3M9ImJhcnMiPiR7"
    "QURNLmJhcnMubWFwKGg9PmA8aSBzdHlsZT0iaGVpZ2h0OiR7aH0lIj48L2k+YCkuam9pbigiIil9"
    "PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3BpcyIgc3R5bGU9Im1hcmdpbi10b3A6MTRweCI+JHtb"
    "WyJUb2RheSIsbW9uZXkoQURNLmtwaS5yZXYpXSxbIlRoaXMgbW9udGgiLG1vbmV5KEFETS5tYXJr"
    "ZXQubW9udGgpXSxbIkF2ZyBvcmRlciIsbW9uZXkoYXZnKV1dCiAgICAgICAubWFwKChbayx2XSk9"
    "PmA8ZGl2IGNsYXNzPSJrcGkiPjxzbWFsbD4ke2t9PC9zbWFsbD48Yj4ke3Z9PC9iPjwvZGl2PmAp"
    "LmpvaW4oIiIpfTwvZGl2PjwvZGl2PmApfQogICAke0coImZ1bm5lbCIsYDxkaXYgY2xhc3M9ImNh"
    "cmQiPjxoMz5Db252ZXJzaW9uIGZ1bm5lbDwvaDM+PHAgY2xhc3M9InN1YiI+V2hlcmUgcGVvcGxl"
    "IGRyb3Agb2ZmPC9wPgogICAgIDxkaXYgY2xhc3M9ImZ1bm5lbCI+JHsoKCk9Pntjb25zdCBmPUFE"
    "TS5hbmFseXRpY3MuZnVubmVsLHQ9TWF0aC5tYXgoMSxmLnVzZXJzKTsKICAgICAgIHJldHVybiBb"
    "WyJTdGFydGVkIGJvdCIsZi51c2VycywxMDBdLFsiRGVwb3NpdGVkIixmLmRlcCxNYXRoLnJvdW5k"
    "KGYuZGVwL3QqMTAwKV0sWyJCb3VnaHQiLGYuYnV5LE1hdGgucm91bmQoZi5idXkvdCoxMDApXV0K"
    "ICAgICAgIC5tYXAoKFtrLHYsd10pPT5gPGRpdj48aSBzdHlsZT0id2lkdGg6JHtNYXRoLm1heCg0"
    "LHcpfSUiPjwvaT48c3BhbiBzdHlsZT0id2hpdGUtc3BhY2U6bm93cmFwIj4ke2t9IMK3IDxiPiR7"
    "dn08L2I+PC9zcGFuPjwvZGl2PmApLmpvaW4oIiIpO30pKCl9PC9kaXY+PC9kaXY+YCl9CiAgICR7"
    "RygicHJvZml0UmVwIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlBlci1jb3VudHJ5IHByb2ZpdDwv"
    "aDM+CiAgICAgJHtBRE0uYW5hbHl0aWNzLmNvdW50cnkubGVuZ3RoP0FETS5hbmFseXRpY3MuY291"
    "bnRyeS5tYXAoKFtjLG4sc3VtXSk9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHtlc2MoYyl9IMK3"
    "ICR7bn0gc29sZDwvc3Bhbj48YiBzdHlsZT0iY29sb3I6dmFyKC0tb2spIj4ke21vbmV5KHN1bSl9"
    "PC9iPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJn"
    "aW46MCI+Tm8gc2FsZXMgeWV0LjwvcD4nfQogICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayBz"
    "b2Z0IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4IiBkYXRhLWFkbWluLWFjdD0iZXhwb3J0Ij5FeHBv"
    "cnQgQ1NWPC9idXR0b24+PC9kaXY+YCl9CiAgICR7RygiY2x2IixgPGRpdiBjbGFzcz0iY2FyZCI+"
    "PGgzPkN1c3RvbWVyIHZhbHVlPC9oMz4KICAgICAke0FETS51c2Vycy5sZW5ndGg/QURNLnVzZXJz"
    "LnNsaWNlKDAsNSkubWFwKHU9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+PGIgc3R5bGU9ImNvbG9y"
    "OnZhcigtLXR4KSI+JHtlc2ModS5uYW1lKX08L2I+IMK3ICR7dS5vcmRlcnN9IG9yZGVyczwvc3Bh"
    "bj4KICAgICAgICAgPGI+JHttb25leSh1LnNwZW50KX0gPHNwYW4gY2xhc3M9InBpbGwgJHt1LnNw"
    "ZW50PjA/Im9uIjoiaG90In0iPiR7dS5zcGVudD4wPyJhY3RpdmUiOiJubyBzcGVuZCJ9PC9zcGFu"
    "PjwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgOic8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFy"
    "Z2luOjAiPk5vIGN1c3RvbWVycyB5ZXQuPC9wPid9PC9kaXY+YCl9CiAgICR7RygiZGlnZXN0Iixg"
    "PGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAi"
    "PjxkaXYgY2xhc3M9InQiPkRhaWx5IGRpZ2VzdDxzbWFsbD5FbmQtb2YtZGF5IHN1bW1hcnkgdG8g"
    "dGhlIGFkbWluIGNoYXQ8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULnNh"
    "bGVzRmVlZD8iIG9uIjoiIn0iIGRhdGEtZmVhdD0ic2FsZXNGZWVkIj48L2Rpdj48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlNlbmQgYXQ8L2xhYmVsPjxpbnB1dCBjbGFzcz0i"
    "aW5wIiB2YWx1ZT0iMjM6MzAiPjwvZGl2PjwvZGl2PmApfWA7Cn0KCmZ1bmN0aW9uIGFTZWN1cml0"
    "eSgpewogIHJldHVybiBgJHtHKCJyYXRlTGltaXQiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+UmF0"
    "ZSBsaW1pdGluZzwvaDM+PHAgY2xhc3M9InN1YiI+U3RvcHMgb25lIHVzZXIgaGFtbWVyaW5nIHRo"
    "ZSBib3QuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4"
    "Ij48bGFiZWw+TWF4IGFjdGlvbnMgcGVyIG1pbnV0ZTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAi"
    "IHZhbHVlPSIyMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5Db29sZG93"
    "biBhZnRlciBsaW1pdCAocyk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iNjAiPjwv"
    "ZGl2PjwvZGl2PmApfQogICAke0coIm11bHRpQWNjIixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBj"
    "bGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPk11bHRpLWFj"
    "Y291bnQgZGV0ZWN0aW9uPHNtYWxsPkZsYWdzIHJlZmVycmFsIGZhcm1pbmcgcGF0dGVybnM8L3Nt"
    "YWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULm11bHRpQWNjPyIgb24iOiIifSIg"
    "ZGF0YS1mZWF0PSJtdWx0aUFjYyI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPkZsYWdnZWQgdGhpcyB3ZWVrPC9zcGFuPjxiPjcgYWNjb3VudHM8L2I+PC9kaXY+CiAgICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIHNvZnQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiIGRh"
    "dGEtYS1nbz0ib3JkZXJzIj5SZXZpZXcgRmxhZ2dlZDwvYnV0dG9uPjwvZGl2PmApfQogICAke0co"
    "InZlbG9jaXR5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBh"
    "ZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPlB1cmNoYXNlIHZlbG9jaXR5IGFsZXJ0czxzbWFs"
    "bD5DYXRjaGVzIHN0b2xlbiB0b3AtdXBzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0"
    "ZyR7RkVBVC52ZWxvY2l0eT8iIG9uIjoiIn0iIGRhdGEtZmVhdD0idmVsb2NpdHkiPjwvZGl2Pjwv"
    "ZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+QWxlcnQgYWJvdmUgTiBidXlzIC8g"
    "MTAgbWluPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjUiPjwvZGl2PjwvZGl2PmAp"
    "fQogICAke0coImFkbWluMmZhIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkFkbWluIDJGQTwvaDM+"
    "PHAgY2xhc3M9InN1YiI+UmVxdWlyZWQgYmVmb3JlIGJhbGFuY2UgZWRpdHMsIGJyb2FkY2FzdHMg"
    "YW5kIHdpcGVzLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTBweCI+PGxhYmVsPk1ldGhvZDwvbGFiZWw+PHNlbGVjdCBjbGFzcz0iaW5wIj48b3B0aW9uPlBJ"
    "Tjwvb3B0aW9uPjxvcHRpb24+VE9UUCBhcHA8L29wdGlvbj48L3NlbGVjdD48L2Rpdj4KICAgICA8"
    "YnV0dG9uIGNsYXNzPSJidG4geHMiIGRhdGEtZmVhdD0iYWRtaW4yZmEiPlNldCBVcDwvYnV0dG9u"
    "PjwvZGl2PmApfQogICAke0coImF1dG9CYW4iLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QXV0by1i"
    "YW4gcnVsZXM8L2gzPgogICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPkNoYXJn"
    "ZWJhY2s8c21hbGw+QmFuIGluc3RhbnRseTwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "dGckeyhBRE0uc3cmJkFETS5zdy5iYW5fY2hhcmdlYmFjayk/IiBvbiI6IiJ9IiBkYXRhLXNldHRp"
    "bmc9ImJhbl9jaGFyZ2ViYWNrIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48"
    "ZGl2IGNsYXNzPSJ0Ij5GYWtlIFVUUiBzdWJtaXR0ZWQ8c21hbGw+QmFuIGFmdGVyIDIgYXR0ZW1w"
    "dHM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuYmFu"
    "X2Zha2VfdXRyKT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0iYmFuX2Zha2VfdXRyIj48L2Rpdj48"
    "L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5SZXBlYXRlZCBmYWls"
    "ZWQgdmVyaWZpY2F0aW9uPHNtYWxsPkJhbiBhZnRlciA1PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmJhbl9mYWlsZWRfdmVyaWZ5KT8iIG9uIjoiIn0i"
    "IGRhdGEtc2V0dGluZz0iYmFuX2ZhaWxlZF92ZXJpZnkiPjwvZGl2PjwvZGl2PjwvZGl2PmApfQog"
    "ICAke0coImJsYWNrbGlzdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5CbGFja2xpc3Q8L2gzPjxw"
    "IGNsYXNzPSJzdWIiPlVzZXIgSURzLCBwaG9uZSBudW1iZXJzIGFuZCBVUEkgcmVmZXJlbmNlcy48"
    "L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxkaXYg"
    "c3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4Ij4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBp"
    "ZD0iYmwtaW4iIHBsYWNlaG9sZGVyPSJJRCwgcGhvbmUgb3IgVVBJIHJlZiI+PGJ1dHRvbiBjbGFz"
    "cz0iYnRuIHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9ImJsYmxvY2siPkJsb2NrPC9idXR0"
    "b24+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkJsb2NrZWQgZW50cmll"
    "czwvc3Bhbj48Yj40MjwvYj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJlbmNyeXB0IixgPGRpdiBj"
    "bGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYg"
    "Y2xhc3M9InQiPkVuY3J5cHQgc2Vzc2lvbnMgYXQgcmVzdDxzbWFsbD5BIGxlYWtlZCBkYXRhYmFz"
    "ZSBmaWxlIHN0YXlzIHVzZWxlc3M8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtG"
    "RUFULmVuY3J5cHQ/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9ImVuY3J5cHQiPjwvZGl2PjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5LZXkgc3RhdHVzPC9zcGFuPjxiIHN0eWxlPSJjb2xv"
    "cjp2YXIoLS1vaykiPkxvYWRlZCBmcm9tIGVudjwvYj48L2Rpdj48L2Rpdj5gKX1gOwp9CgpmdW5j"
    "dGlvbiBhU3RvY2soKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5VcGxvYWQgc3Rv"
    "Y2s8L2gzPjxwIGNsYXNzPSJzdWIiPlBhc3RlIHNlc3Npb25zIG9yIHVwbG9hZCBhIGZpbGUg4oCU"
    "IG9uZSBwZXIgbGluZS48L3A+CjxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "MnB4Ij48bGFiZWw+UGFzdGUgYWNjb3VudHMg4oCUIG9uZSBwZXIgbGluZTwvbGFiZWw+CiAgICAg"
    "IDx0ZXh0YXJlYSBjbGFzcz0iaW5wIiBpZD0ic3RwYXN0ZSIgcm93cz0iNCIKICAgICAgICBwbGFj"
    "ZWhvbGRlcj0iSW5kaWEgfCArOTEgfCAzNSB8IHNlc3Npb24mIzEwO0tlbnlhIHwgKzI1NCB8IDc3"
    "IHwgc2Vzc2lvbiI+PC90ZXh0YXJlYT48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+PGJ1"
    "dHRvbiBjbGFzcz0iYnRuIGN0YSIgaWQ9InN0c2F2ZSI+SW1wb3J0PC9idXR0b24+CiAgICAgIDxs"
    "YWJlbCBjbGFzcz0iYnRuIHNvZnQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2Vu"
    "dGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Y3Vyc29yOnBvaW50ZXIiPgogICAgICAgIFVwbG9h"
    "ZCAudHh0PGlucHV0IHR5cGU9ImZpbGUiIGlkPSJzdGZpbGUiIGFjY2VwdD0iLnR4dCwuY3N2IiBo"
    "aWRkZW4+PC9sYWJlbD48L2Rpdj4KICAgIDxwIGNsYXNzPSJzdWIiIGlkPSJzdG1zZyIgc3R5bGU9"
    "Im1hcmdpbjo4cHggMCAwO2ZvbnQtc2l6ZTouNzJyZW0iPjwvcD4KICAgICR7RygiYnVsa1VwbG9h"
    "ZCIsYDxkaXYgY2xhc3M9InN0ZXBzIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij4KICAgICAgPGRp"
    "diBjbGFzcz0ic3RlcCBvbiI+PGk+MTwvaT4ke0FETS5hbmFseXRpY3Muc3RvY2tfdG90YWx9IGF2"
    "YWlsYWJsZSBub3c8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCBvbiI+PGk+MjwvaT4ke0FE"
    "TS5hbmFseXRpY3Muc29sZF90b3RhbH0gc29sZCBzbyBmYXI8L2Rpdj4KICAgICAgPGRpdiBjbGFz"
    "cz0ic3RlcCI+PGk+MzwvaT5BZGQgbW9yZSBmcm9tIHRoZSBib3Q6IEFkbWluIOKGkiBTdG9jazwv"
    "ZGl2PjwvZGl2PmApfQogICAgJHtHKCJkdXBEZXRlY3QiLGA8cCBjbGFzcz0ic3ViIiBzdHlsZT0i"
    "bWFyZ2luOjEwcHggMCAwO2ZvbnQtc2l6ZTouN3JlbSI+RHVwbGljYXRlIHBob25lcyBhbmQgc2Vz"
    "c2lvbnMgYXJlIHJlamVjdGVkIGF1dG9tYXRpY2FsbHkuPC9wPmApfQogICAgJHtHKCJoZWFsdGhD"
    "aGVjayIsYDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPlZhbGlkYXRlIGJlZm9yZSBz"
    "ZWxsaW5nPHNtYWxsPkNoZWNrIHRoZSBzZXNzaW9uIGlzIGFsaXZlIGF0IHB1cmNoYXNlIHRpbWU8"
    "L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULmhlYWx0aENoZWNrPyIgb24i"
    "OiIifSIgZGF0YS1mZWF0PSJoZWFsdGhDaGVjayI+PC9kaXY+PC9kaXY+YCl9PC9kaXY+CiAgICR7"
    "RygibG93U3RvY2siLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+TG93LXN0b2NrIGFsZXJ0czwvaDM+"
    "PHAgY2xhc3M9InN1YiI+UGVyLWNvdW50cnkgdGhyZXNob2xkIOKAlCBwaW5nIHdoZW4gaXQgZHJv"
    "cHMgYmVsb3cuPC9wPgogICAgICR7KEFETS5zdG9jay5sZW5ndGg/QURNLnN0b2NrOlNUT0NLKS5z"
    "bGljZSgwLDYpLm1hcChzPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7cy5mfSAke2VzYyhzLmMp"
    "fSDCtyAke3MucXR5fHwwfSBsZWZ0PC9zcGFuPgogICAgICAgPGI+PGlucHV0IGNsYXNzPSJpbnAi"
    "IHN0eWxlPSJoZWlnaHQ6MzBweDt3aWR0aDo2NHB4O3RleHQtYWxpZ246Y2VudGVyO2ZvbnQtc2l6"
    "ZTouNzRyZW0iIHZhbHVlPSIke3MubG93fHwxMH0iPjwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAg"
    "ICAgfHwnPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBzdG9jayB5ZXQuPC9wPid9"
    "PC9kaXY+YCl9CiAgICR7Rygic2VsbEhvdXJzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlNlbGxp"
    "bmcgaG91cnM8L2gzPjxwIGNsYXNzPSJzdWIiPkNvdW50cmllcyBoaWRlIHRoZW1zZWx2ZXMgb3V0"
    "c2lkZSB0aGUgd2luZG93LjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdp"
    "bi10b3A6OHB4Ij48bGFiZWw+T3BlbjwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIw"
    "OTowMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5DbG9zZTwvbGFiZWw+"
    "PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIwMjowMCI+PC9kaXY+PC9kaXY+YCl9CiAgICR7Rygi"
    "ZHluUHJpY2UiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+RHluYW1pYyBwcmljaW5nPC9oMz4KICAg"
    "ICA8ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+"
    "UmFpc2UgcHJpY2Ugd2hlbiBzdG9jayBpcyBsb3c8c21hbGw+KzEwJSB1bmRlciAyMCB1bml0czwv"
    "c21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGcke0ZFQVQuZHluUHJpY2U/IiBvbiI6IiJ9"
    "IiBkYXRhLWZlYXQ9ImR5blByaWNlIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJzd2Fw"
    "Ij48ZGl2IGNsYXNzPSJ0Ij5OaWdodCBkaXNjb3VudDxzbWFsbD7iiJI1JSBiZXR3ZWVuIDAwOjAw"
    "IGFuZCAwNjowMDwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFE"
    "TS5zdy5uaWdodF9kaXNjb3VudCk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9Im5pZ2h0X2Rpc2Nv"
    "dW50Ij48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJidWxrRGlzYyIsYDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxoMz5CdWxrIGRpc2NvdW50czwvaDM+CiAgICAgPHAgY2xhc3M9InN1YiI+UGVyY2Vu"
    "dCBvZmYgd2hlbiB0aGUgYnV5ZXIgdGFrZXMgc2V2ZXJhbCBhdCBvbmNlLjwvcD4KICAgICA8ZGl2"
    "IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+PGxhYmVsPjUgb3IgbW9yZSAo"
    "JSk8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtcHJpY2U9ImJ1bGtfNSIg"
    "dmFsdWU9IiR7QURNLnByaWNpbmc/QURNLnByaWNpbmcuYnVsa181OjV9Ij48L2Rpdj4KICAgICA8"
    "ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPjEwIG9yIG1vcmUgKCUpPC9sYWJlbD4KICAgICAgIDxp"
    "bnB1dCBjbGFzcz0iaW5wIiBkYXRhLXByaWNlPSJidWxrXzEwIiB2YWx1ZT0iJHtBRE0ucHJpY2lu"
    "Zz9BRE0ucHJpY2luZy5idWxrXzEwOjEwfSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "PjxsYWJlbD4yNSBvciBtb3JlICglKTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIg"
    "ZGF0YS1wcmljZT0iYnVsa18yNSIgdmFsdWU9IiR7QURNLnByaWNpbmc/QURNLnByaWNpbmcuYnVs"
    "a18yNToxNX0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RGVmYXVsdCBw"
    "cmljZSBmb3IgbmV3IHN0b2NrPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRh"
    "LXByaWNlPSJkZWZhdWx0X3ByaWNlIiB2YWx1ZT0iJHtBRE0ucHJpY2luZz9BRE0ucHJpY2luZy5k"
    "ZWZhdWx0X3ByaWNlOjM1fSI+PC9kaXY+CiAgICAgPC9kaXY+YCl9CiAgICR7RygidmFyaWFudHMi"
    "LGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+VmFyaWFudHM8L2gzPjxwIGNsYXNzPSJzdWIiPlByaWNl"
    "ZCBzZXBhcmF0ZWx5IG9uIGV2ZXJ5IGNvdW50cnkuPC9wPgogICAgIDxkaXYgY2xhc3M9Imt2Ij48"
    "c3Bhbj4yRkEgZW5hYmxlZDwvc3Bhbj48Yj4r4oK5NTwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNz"
    "PSJrdiI+PHNwYW4+V2l0aCBtYWlsPC9zcGFuPjxiPivigrk4PC9iPjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9Imt2Ij48c3Bhbj5TcGFtLWZyZWUgZ3VhcmFudGVlPC9zcGFuPjxiPivigrk0PC9iPjwv"
    "ZGl2PjwvZGl2PmApfQogICA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5JbnZlbnRvcnk8L2gyPjxhIGNs"
    "YXNzPSJzdWIiPnRhcCBhIHByaWNlIHRvIGNoYW5nZSBpdDwvYT48L2Rpdj4KICAgJHtTVE9DSy5t"
    "YXAocz0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24t"
    "aXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke3MuZn08L2Rp"
    "dj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNp"
    "emU6LjgzcmVtIj4ke3MuY308L2I+PGJyPgogICAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIo"
    "LS10eDIpO2ZvbnQtc2l6ZTouNjlyZW0iPiR7cy5xdHl9IGF2YWlsYWJsZTwvc21hbGw+PC9kaXY+"
    "CiAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtY3ByaWNlPSIke2VzYyhzLmMpfSIgdmFsdWU9"
    "IiR7cy5wcmljZX0iIHN0eWxlPSJoZWlnaHQ6MzJweDt3aWR0aDo3OHB4O3RleHQtYWxpZ246Y2Vu"
    "dGVyO2ZvbnQtc2l6ZTouNzZyZW0iPgogICAgIAogICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBi"
    "YWQiIGRhdGEtbWlzYz0iY2xlYXJzdG9jayIgZGF0YS1taXNjLXZhbHVlPSIke2VzYyhzLmMpfSI+"
    "Q2xlYXI8L2J1dHRvbj48L2Rpdj5gKS5qb2luKCIiKX1gOwp9CmZ1bmN0aW9uIGFPcmRlcnMoKXsK"
    "ICByZXR1cm4gYDxkaXYgY2xhc3M9InNlYyI+PGgyPlJlY2VudCBPcmRlcnM8L2gyPjxhIGRhdGEt"
    "YWRtaW4tYWN0PSJleHBvcnQiPkV4cG9ydDwvYT48L2Rpdj4KICAgJHtBRE0ub3JkZXJzLmxlbmd0"
    "aD9BRE0ub3JkZXJzLm1hcChvPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3Bs"
    "YXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxkaXYgY2xhc3M9ImZs"
    "YWciPiR7by5mfTwvZGl2PjxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9"
    "ImZvbnQtc2l6ZTouODJyZW0iPiR7ZXNjKG8uaWQpfTwvYj48YnI+CiAgICAgPHNtYWxsIHN0eWxl"
    "PSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjlyZW0iPiR7ZXNjKG8udXNlcil9IMK3ICR7"
    "bW9uZXkoby5wcmljZSl9IMK3ICR7ZXNjKG8ud2hlbil9PC9zbWFsbD48L2Rpdj4KICAgICA8c3Bh"
    "biBjbGFzcz0icGlsbCAke28uc3Q9PT0iRGVsaXZlcmVkIj8ib24iOiJvZmYifSI+JHtlc2Moby5z"
    "dCl9PC9zcGFuPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgOic8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0"
    "Ij48cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIG9yZGVycyB5ZXQuPC9wPjwvZGl2"
    "Pid9CiAgICR7RygiZGlzcHV0ZSIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPkRpc3B1dGVzPC9oMj48"
    "L2Rpdj4KICAgICAke0FETS5kaXNwdXRlcy5sZW5ndGg/QURNLmRpc3B1dGVzLm1hcChkPT5gPGRp"
    "diBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50"
    "ZXI7Z2FwOjEwcHgiPgogICAgICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJzaGllbGQiKX08"
    "L2Rpdj4KICAgICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0i"
    "Zm9udC1zaXplOi44cmVtIj4ke2VzYyhkLmlkKX0gwrcgJHtlc2MoZC51c2VyKX08L2I+PGJyPjxz"
    "bWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY4cmVtIj4ke2VzYyhkLnJl"
    "YXNvbil9PC9zbWFsbD48L2Rpdj4KICAgICAgICAgPHNwYW4gY2xhc3M9InBpbGwgJHtkLnN0YXR1"
    "cz09PSJPcGVuIj8iaG90Ijoib24ifSI+JHtlc2MoZC5zdGF0dXMpfTwvc3Bhbj48L2Rpdj5gKS5q"
    "b2luKCIiKQogICAgICAgOic8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0Ij48cCBjbGFzcz0ic3ViIiBz"
    "dHlsZT0ibWFyZ2luOjAiPk5vIGRpc3B1dGVzLjwvcD48L2Rpdj4nfWApfQogICAke0coIndhcnJh"
    "bnR5IixgPGRpdiBjbGFzcz0ic2VjIj48aDI+UmVwbGFjZW1lbnQgY2xhaW1zPC9oMj48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0"
    "ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJzaGll"
    "bGQiKX08L2Rpdj4KICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5"
    "bGU9ImZvbnQtc2l6ZTouOHJlbSI+MiBjbGFpbXMgaW5zaWRlIHdhcnJhbnR5PC9iPjxicj4KICAg"
    "ICAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjhyZW0iPkF1"
    "dG8tcmVwbGFjZWQgZnJvbSBzdG9jazwvc21hbGw+PC9kaXY+CiAgICAgICA8L2Rpdj5gKX0KICAg"
    "CiAgICR7RygicmV2aWV3cyIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPlJldmlld3M8L2gyPjwvZGl2"
    "PgogICAgICR7KEFETS5yZXZpZXdzLmxlbmd0aD9BRE0ucmV2aWV3cy5tYXAocj0+W3IudXNlcixy"
    "LmMsci5zdGFycyxyLnRleHRdKTpbXSkKICAgICAgIC5tYXAoKFt1LGMscix0XSk9PmA8ZGl2IGNs"
    "YXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtn"
    "YXA6MTBweCI+CiAgICAgICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoInN0YXIiKX08L2Rpdj4K"
    "ICAgICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1z"
    "aXplOi44cmVtIj4ke3V9IMK3ICR7Y30gwrcgJHtyfTwvYj48YnI+CiAgICAgICAgICAgPHNtYWxs"
    "IHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjhyZW0iPiR7dH08L3NtYWxsPjwv"
    "ZGl2PgogICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1yZXY9IiR7ci5p"
    "ZH0iPkhpZGU8L2J1dHRvbj48L2Rpdj5gKS5qb2luKCIiKX1gKX1gOwp9CmZ1bmN0aW9uIGFVc2Vy"
    "cygpewogIHJldHVybiBgPGRpdiBjbGFzcz0iZmllbGQiPjxpbnB1dCBjbGFzcz0iaW5wIiBwbGFj"
    "ZWhvbGRlcj0iU2VhcmNoIGJ5IElEIG9yIHVzZXJuYW1lIj48L2Rpdj4KICAgJHtBRE0udXNlcnMu"
    "bGVuZ3RoP0FETS51c2Vycy5tYXAodT0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJk"
    "aXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICAgPGRpdiBjbGFz"
    "cz0iZmxhZyI+JHtpYygidXNlciIpfTwvZGl2PgogICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWlu"
    "LXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjgycmVtIj4ke2VzYyh1Lm5hbWUpfTwvYj48"
    "YnI+CiAgICAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjly"
    "ZW0iPiR7bW9uZXkodS5zcGVudCl9IHNwZW50IMK3ICR7dS5vcmRlcnN9IG9yZGVycyDCtyAke21v"
    "bmV5KHUuYmFsYW5jZSl9IGJhbGFuY2Uke3UuYmFubmVkPyIgwrcgYmFubmVkIjoiIn08L3NtYWxs"
    "PjwvZGl2PgogICAgICA8c3BhbiBjbGFzcz0icGlsbCAke3UuYmFubmVkPyJvZmYiOiJvbiJ9Ij4k"
    "e2VzYyh1LnRpZXIpfTwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKQogICAgIDonPGRpdiBjbGFzcz0i"
    "Y2FyZCB0aWdodCI+PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyB1c2VycyB5ZXQu"
    "PC9wPjwvZGl2Pid9CiAgICR7RygiYWRtaW5Sb2xlcyIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPkFk"
    "bWluIHJvbGVzPC9oMj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJjYXJkIj4KICAgICAgICR7QURN"
    "LmFkbWlucy5sZW5ndGg/QURNLmFkbWlucy5tYXAoYT0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj48"
    "YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4ke2VzYyhhLmxhYmVsKX08L2I+IMK3ICR7ZXNjKGEu"
    "cGVybXMpfTwvc3Bhbj48Yj48c3BhbiBjbGFzcz0icGlsbCBvbiI+JHtlc2MoYS5yb2xlKX08L3Nw"
    "YW4+PC9iPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5bGU9"
    "Im1hcmdpbjowIj5Pbmx5IHlvdSBzbyBmYXIuPC9wPid9CiAgICAgICA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+PGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo3"
    "cHgiPgogICAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgcGxhY2Vob2xkZXI9IkB1c2VybmFtZSI+"
    "PHNlbGVjdCBjbGFzcz0iaW5wIiBzdHlsZT0id2lkdGg6YXV0byI+PG9wdGlvbj5NYW5hZ2VyPC9v"
    "cHRpb24+PG9wdGlvbj5TdXBwb3J0PC9vcHRpb24+PC9zZWxlY3Q+CiAgICAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJhZG1pbmFkZCI+QWRkPC9i"
    "dXR0b24+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiYnV5ZXJCYWRnZSIsYDxkaXYgY2xh"
    "c3M9ImNhcmQiPjxoMz5WZXJpZmllZCBidXllciBiYWRnZTwvaDM+CiAgICAgPGRpdiBjbGFzcz0i"
    "ZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPk9yZGVycyByZXF1aXJlZDwvbGFi"
    "ZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4+VmVyaWZpZWQgdXNlcnM8L3NwYW4+PGI+ODQ8L2I+PC9kaXY+PC9kaXY+YCl9CiAg"
    "ICR7RygibXVsdGlBY2MiLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpm"
    "bGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwg"
    "aG90Ij43IGZsYWdnZWQ8L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDow"
    "O2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+UG9zc2libGUgbXVsdGktYWNjb3Vu"
    "dHMgdGhpcyB3ZWVrPC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEt"
    "YS1nbz0ic2VjdXJpdHkiPlJldmlldzwvYnV0dG9uPjwvZGl2PmApfWA7Cn0KZnVuY3Rpb24gYVNl"
    "dHRpbmdzKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+U3RvcmU8L2gzPjxwIGNs"
    "YXNzPSJzdWIiPkV2ZXJ5dGhpbmcgbGl2ZXMgaW4gdGhlIGRhdGFiYXNlIOKAlCBubyBjb2RlIGVk"
    "aXRzLCBubyByZXN0YXJ0LjwvcD4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2lu"
    "LXRvcDoxMnB4Ij48bGFiZWw+U3RvcmUgbmFtZTwvbGFiZWw+CiAgICAgIDxpbnB1dCBjbGFzcz0i"
    "aW5wIiBkYXRhLXNldD0ic3RvcmVfbmFtZSIgdmFsdWU9IiR7ZXNjKENNUy5zdG9yZU5hbWV8fENG"
    "Ry5uYW1lKX0iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5UYWdsaW5lPC9s"
    "YWJlbD4KICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJzdG9yZV90YWdsaW5lIiB2"
    "YWx1ZT0iJHtlc2MoQ01TLnN0b3JlVGFnfHxDRkcudGFnKX0iPjwvZGl2PgogICAgPGRpdiBjbGFz"
    "cz0iZmllbGQiPjxsYWJlbD5UYWdsaW5lPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9"
    "IiR7Q0ZHLnRhZ30iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5TdXBwb3J0"
    "IGNvbnRhY3Q8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiBwbGFjZWhvbGRlcj0iQHlvdXJfc3Vw"
    "cG9ydCI+PC9kaXY+CiAgICA8L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlRvZ2dsZXM8"
    "L2gzPgogICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+Rm9yY2Ugam9pbjxzbWFs"
    "bD5SZXF1aXJlIGNoYW5uZWwgbWVtYmVyc2hpcDwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFz"
    "cz0idGckeyhBRE0uc3cmJkFETS5zdy5mb3JjZV9zdWIpPyIgb24iOiIifSIgZGF0YS1zZXR0aW5n"
    "PSJmb3JjZV9zdWIiPjwvZGl2PjwvZGl2PgogICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFz"
    "cz0idCI+TWFpbnRlbmFuY2UgbW9kZTxzbWFsbD5GcmVlemUgdGhlIHN0b3JlPC9zbWFsbD48L2Rp"
    "dj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3Lm1haW50ZW5hbmNlKT8iIG9u"
    "IjoiIn0iIGRhdGEtc2V0dGluZz0ibWFpbnRlbmFuY2UiPjwvZGl2PjwvZGl2PgogICAgPGRpdiBj"
    "bGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+QXV0byByZWZ1bmRzPHNtYWxsPlJlZnVuZCBmYWls"
    "ZWQgbG9naW5zPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5hdXRvUmVm"
    "dW5kPyIgb24iOiIifSIgZGF0YS1mZWF0PSJhdXRvUmVmdW5kIj48L2Rpdj48L2Rpdj4KICAgIDxk"
    "aXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPlJlZmVycmFsIHByb2dyYW08c21hbGw+MSUg"
    "b2YgaW52aXRlZCBkZXBvc2l0czwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGcke0ZF"
    "QVQucmVmRGFzaD8iIG9uIjoiIn0iIGRhdGEtZmVhdD0icmVmRGFzaCI+PC9kaXY+PC9kaXY+PC9k"
    "aXY+CiAgICR7RygiaTE4biIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5MYW5ndWFnZXM8L2gzPjxw"
    "IGNsYXNzPSJzdWIiPlVzZXJzIHBpY2sgdGhlaXIgb3duOyBldmVyeSBzdHJpbmcgaXMgdHJhbnNs"
    "YXRlZC48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5EZWZhdWx0IGxhbmd1YWdl"
    "PC9sYWJlbD4KICAgICAgPHNlbGVjdCBjbGFzcz0iaW5wIiBkYXRhLXNldHNlbD0ibGFuZ19kZWZh"
    "dWx0Ij4KICAgICAgICA8b3B0aW9uIHZhbHVlPSJlbiIgJHsoQURNLnNldHRpbmdzJiZBRE0uc2V0"
    "dGluZ3MubGFuZz09PSJlbiIpPyJzZWxlY3RlZCI6IiJ9PkVuZ2xpc2g8L29wdGlvbj4KICAgICAg"
    "ICA8b3B0aW9uIHZhbHVlPSJoaSIgJHsoQURNLnNldHRpbmdzJiZBRE0uc2V0dGluZ3MubGFuZz09"
    "PSJoaSIpPyJzZWxlY3RlZCI6IiJ9PuCkueCkv+CkqOCljeCkpuClgDwvb3B0aW9uPgogICAgICA8"
    "L3NlbGVjdD48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJzbGFzaENtZHMiLGA8ZGl2IGNsYXNzPSJj"
    "YXJkIj48aDM+U2xhc2ggY29tbWFuZHM8L2gzPjxwIGNsYXNzPSJzdWIiPlJlZ2lzdGVyZWQgaW4g"
    "VGVsZWdyYW0ncyBjb21tYW5kIG1lbnUuPC9wPgogICAgICR7W1siL3N0YXJ0IiwiT3BlbiB0aGUg"
    "c3RvcmUiXSxbIi9idXkiLCJKdW1wIHRvIHN0b2NrIl0sWyIvYmFsYW5jZSIsIlNob3cgd2FsbGV0"
    "Il0sWyIvb3JkZXJzIiwiTXkgb3JkZXJzIl0sWyIvc3VwcG9ydCIsIkNvbnRhY3Qgc3VwcG9ydCJd"
    "XQogICAgICAgLm1hcCgoW2MsZF0pPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPjxiIHN0eWxlPSJj"
    "b2xvcjp2YXIoLS10eCkiPiR7Y308L2I+PC9zcGFuPjxiIHN0eWxlPSJmb250LXdlaWdodDo1MDA7"
    "Zm9udC1zaXplOi43NHJlbSI+JHtkfTwvYj48L2Rpdj5gKS5qb2luKCIiKX0KICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4gYmxvY2sgc29mdCIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCIgZGF0YS1hZG1p"
    "bi1hY3Q9InN5bmMiPlN5bmMgQ29tbWFuZHM8L2J1dHRvbj48L2Rpdj5gKX0KICAgJHtHKCJhdXRv"
    "QmFja3VwIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkF1dG8tYmFja3VwPC9oMz48cCBjbGFzcz0i"
    "c3ViIj5EYXRhYmFzZSB0byBhIHByaXZhdGUgY2hhbm5lbCBvbiBhIHNjaGVkdWxlLjwvcD4KICAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+RXZlcnkg"
    "KGhvdXJzKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI2Ij48L2Rpdj4KICAgICA8"
    "ZGl2IGNsYXNzPSJrdiI+PHNwYW4+TGFzdCBiYWNrdXA8L3NwYW4+PGI+NDIgbWluIGFnbzwvYj48"
    "L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxi"
    "dXR0b24gY2xhc3M9ImJ0biIgZGF0YS1hZG1pbi1hY3Q9ImJhY2t1cCI+QmFjayBVcCBOb3c8L2J1"
    "dHRvbj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCIgZGF0YS1hZG1pbi1hY3Q9InJl"
    "c3RvcmUiPlJlc3RvcmU8L2J1dHRvbj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJhdWRpdExvZyIs"
    "YDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5BdWRpdCBsb2c8L2gzPjxwIGNsYXNzPSJzdWIiPkV2ZXJ5"
    "IGFkbWluIGFjdGlvbiwgd2l0aCB1bmRvLjwvcD4KICAgICAkeygoQURNLmF1ZGl0JiZBRE0uYXVk"
    "aXQubGVuZ3RoKT9BRE0uYXVkaXQubWFwKGE9PlthLndobywoYS53aGF0KyIgIithLmRldGFpbCku"
    "dHJpbSgpLGEud2hlbl0pOltdKQogICAgICAgLm1hcCgoW3UsYSx0XSk9PmA8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4+PGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHt1fTwvYj4gJHthfTwvc3Bhbj4K"
    "ICAgICAgICAgPGI+JHt0fSA8L2I+PC9kaXY+YCkuam9pbigiIil9PC9kaXY+YCl9CiAgICR7Rygi"
    "c2FuZGJveCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRk"
    "aW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5TYW5kYm94IC8gdGVzdCBtb2RlPHNtYWxsPkZha2Ug"
    "bW9uZXksIG5vIHJlYWwgZGVsaXZlcnk8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRn"
    "JHsoQURNLnN3JiZBRE0uc3cuc2FuZGJveCk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9InNhbmRi"
    "b3giPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImVuY3J5cHQiLGA8ZGl2IGNsYXNzPSJj"
    "YXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBw"
    "eCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5FbmNyeXB0"
    "ZWQ8L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTou"
    "NzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+U2Vzc2lvbnMgYXJlIGVuY3J5cHRlZCBhdCByZXN0PC9k"
    "aXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtYS1nbz0ic2VjdXJpdHki"
    "PktleXM8L2J1dHRvbj48L2Rpdj5gKX0KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkRhbmdlciB6"
    "b25lPC9oMz48cCBjbGFzcz0ic3ViIj5JcnJldmVyc2libGUuIEFzayB0d2ljZS48L3A+CiAgICA8"
    "ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxidXR0b24gY2xhc3M9"
    "ImJ0biB4cyBzb2Z0IiBkYXRhLWFkbWluLWFjdD0iYmFja3VwIj5CYWNrdXA8L2J1dHRvbj4KICAg"
    "IDxidXR0b24gY2xhc3M9ImJ0biBiYWQgaG9sZCIgZGF0YS1kb25lPSJEYXRhYmFzZSByZXNldCAo"
    "ZGVtbykiPjxzcGFuIGNsYXNzPSJmaWxsIj48L3NwYW4+SG9sZCB0byBSZXNldCBEQjwvYnV0dG9u"
    "PjwvZGl2PjwvZGl2PmA7Cn0KCmZ1bmN0aW9uIGJpbmRBZG1pbigpewogIGJpbmRDb21tb24oKTsK"
    "ICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1mZWF0XSIpLmZvckVhY2godD0+dC5v"
    "bmNsaWNrPSgpPT57CiAgICBjb25zdCBrPXQuZGF0YXNldC5mZWF0O0ZFQVRba109IUZFQVRba107"
    "dC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIsRkVBVFtrXSk7CiAgICBwdXNoKCIvYXBpL2FkbWluL2Zl"
    "YXR1cmUiLHtrZXk6ayxvbjpGRUFUW2tdfSk7CiAgICBjb25zdCBjPWRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJmY291bnQiKTsKICAgIGlmKGMpYy50ZXh0Q29udGVudD1PYmplY3QudmFsdWVzKEZF"
    "QVQpLmZpbHRlcihCb29sZWFuKS5sZW5ndGgrIiBvZiAiK09iamVjdC5rZXlzKEZFQVQpLmxlbmd0"
    "aDsKICAgIGFwcGx5RmVhdHVyZXMoKTsKICAgIGNvbnN0IGNhcmQ9dC5jbG9zZXN0KCIuZmNhcmQi"
    "KSwgbmFtZUVsPWNhcmQ/Y2FyZC5xdWVyeVNlbGVjdG9yKCJiIik6bnVsbDsKICAgIGNvbnN0IGxh"
    "YmVsPW5hbWVFbD9uYW1lRWwudGV4dENvbnRlbnQ6ay5yZXBsYWNlKC8oW0EtWl0pL2csIiAkMSIp"
    "LnRvTG93ZXJDYXNlKCk7CiAgICB0b2FzdChsYWJlbCsoRkVBVFtrXT8iIGVuYWJsZWQiOiIgaGlk"
    "ZGVuIikpO30pOwogIGNvbnN0IGZxPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJmcSIpOwogIGlm"
    "KGZxKWZxLm9uaW5wdXQ9KCk9Pntjb25zdCBxPWZxLnZhbHVlLnRyaW0oKS50b0xvd2VyQ2FzZSgp"
    "OwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiLmZyb3ciKS5mb3JFYWNoKHI9PnIuY2xh"
    "c3NMaXN0LnRvZ2dsZSgiaGlkZSIsISFxJiYhci5kYXRhc2V0LmYuaW5jbHVkZXMocSkpKTsKICAg"
    "IGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5mZ3JwIikuZm9yRWFjaChnPT5nLmNsYXNzTGlz"
    "dC50b2dnbGUoImhpZGUiLCFnLnF1ZXJ5U2VsZWN0b3IoIi5mcm93Om5vdCguaGlkZSkiKSkpO307"
    "CiAgY29uc3Qgc2V0QWxsPXY9PntPYmplY3Qua2V5cyhGRUFUKS5mb3JFYWNoKGs9PkZFQVRba109"
    "dik7YXBwbHlGZWF0dXJlcygpO3JlbmRlckFkbWluKCk7dG9hc3Qodj8iQWxsIGZlYXR1cmVzIGVu"
    "YWJsZWQiOiJBbGwgZmVhdHVyZXMgaGlkZGVuIik7fTsKICBjb25zdCBmYT1kb2N1bWVudC5nZXRF"
    "bGVtZW50QnlJZCgiZmFsbCIpLGZuPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJmbm9uZSIpLGZy"
    "PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJmcmVzZXQiKTsKICBpZihmYSlmYS5vbmNsaWNrPSgp"
    "PT5zZXRBbGwodHJ1ZSk7CiAgaWYoZm4pZm4ub25jbGljaz0oKT0+c2V0QWxsKGZhbHNlKTsKICBp"
    "Zihmcilmci5vbmNsaWNrPSgpPT57T2JqZWN0LmFzc2lnbihGRUFULEZFQVQwKTthcHBseUZlYXR1"
    "cmVzKCk7cmVuZGVyQWRtaW4oKTt0b2FzdCgiRmVhdHVyZXMgcmVzZXQiKTt9OwogIGNvbnN0IHN0"
    "bXNnPW09Pntjb25zdCBlPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdG1zZyIpO2lmKGUpZS50"
    "ZXh0Q29udGVudD1tO307CiAgYXN5bmMgZnVuY3Rpb24gaW1wb3J0U3RvY2sodGV4dCl7CiAgICBp"
    "ZighdGV4dC50cmltKCkpe3RvYXN0KCJQYXN0ZSBzb21lIGxpbmVzIGZpcnN0Iik7cmV0dXJuO30K"
    "ICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgIHN0bXNnKCJJ"
    "bXBvcnRpbmfigKYiKTsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3N0b2Nr"
    "Iix7dGV4dDp0ZXh0fSk7CiAgICBpZighcil7c3Rtc2coIiIpO3JldHVybjt9CiAgICBjb25zdCBu"
    "b3RlPXIuYWRkZWQrIiBpbXBvcnRlZCIrKHIuc2tpcHBlZCYmci5za2lwcGVkLmxlbmd0aD8oIiDC"
    "tyAiK3Iuc2tpcHBlZC5sZW5ndGgrIiBza2lwcGVkIik6IiIpOwogICAgdG9hc3Qoci5hZGRlZCsi"
    "IGFjY291bnRzIGFkZGVkIik7CiAgICBjb25zdCBib3g9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "InN0cGFzdGUiKTtpZihib3gpYm94LnZhbHVlPSIiOwogICAgYXdhaXQgcmVmcmVzaCgpOwogICAg"
    "c3Rtc2cobm90ZSk7CiAgfQogIGNvbnN0IHN0c2F2ZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgi"
    "c3RzYXZlIik7CiAgaWYoc3RzYXZlKXN0c2F2ZS5vbmNsaWNrPSgpPT5pbXBvcnRTdG9jaygoZG9j"
    "dW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0cGFzdGUiKXx8e30pLnZhbHVlfHwiIik7CiAgY29uc3Qg"
    "c3RmaWxlPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdGZpbGUiKTsKICBpZihzdGZpbGUpc3Rm"
    "aWxlLm9uY2hhbmdlPSgpPT57CiAgICBjb25zdCBmPXN0ZmlsZS5maWxlcyYmc3RmaWxlLmZpbGVz"
    "WzBdO2lmKCFmKXJldHVybjsKICAgIGNvbnN0IHJkPW5ldyBGaWxlUmVhZGVyKCk7CiAgICByZC5v"
    "bmxvYWQ9KCk9Pntjb25zdCBib3g9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0cGFzdGUiKTsK"
    "ICAgICAgaWYoYm94KWJveC52YWx1ZT1yZC5yZXN1bHQ7c3Rtc2coZi5uYW1lKyIgbG9hZGVkIOKA"
    "lCBwcmVzcyBJbXBvcnQiKTt9OwogICAgcmQucmVhZEFzVGV4dChmKTsKICB9OwogIGNvbnN0IGJs"
    "PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJibGJsb2NrIik7CiAgaWYoYmwpYmwub25jbGljaz1h"
    "c3luYygpPT57CiAgICBjb25zdCBpPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJibC1pbiIpOwog"
    "ICAgY29uc3Qgdj1pPyhpLnZhbHVlfHwiIikudHJpbSgpOiIiOwogICAgaWYoIXYpe3RvYXN0KCJU"
    "eXBlIGFuIGlkLCBwaG9uZSBvciBVUEkgcmVmZXJlbmNlIik7aWYoaSlpLmZvY3VzKCk7cmV0dXJu"
    "O30KICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgIGNvbnN0"
    "IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2JsYWNrbGlzdCIse2FjdGlvbjoiYWRkIix2YWx1"
    "ZTp2fSk7CiAgICBpZighcilyZXR1cm47CiAgICBpLnZhbHVlPSIiO3RvYXN0KCJCbG9ja2VkICIr"
    "dik7YXdhaXQgcmVmcmVzaCgpOwogIH07CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2Rh"
    "dGEtYmwtZGVsXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHI9"
    "YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2JsYWNrbGlzdCIse2FjdGlvbjoiZGVsIixpZDorYi5k"
    "YXRhc2V0LmJsRGVsfSk7CiAgICBpZihyKXt0b2FzdCgiVW5ibG9ja2VkIik7YXdhaXQgcmVmcmVz"
    "aCgpO30KICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1taXNjXSIpLmZv"
    "ckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmll"
    "dyBvbmx5Iik7cmV0dXJuO30KICAgIGNvbnN0IHdoYXQ9Yi5kYXRhc2V0Lm1pc2MsIHZhbD1iLmRh"
    "dGFzZXQubWlzY1ZhbHVlfHwiIjsKICAgIGNvbnN0IGxibD1iLnRleHRDb250ZW50O2IuZGlzYWJs"
    "ZWQ9dHJ1ZTtiLnRleHRDb250ZW50PSLigKYiOwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9h"
    "cGkvYWRtaW4vbWlzYyIse3doYXQ6d2hhdCx2YWx1ZTp2YWx9KTsKICAgIGIuZGlzYWJsZWQ9ZmFs"
    "c2U7Yi50ZXh0Q29udGVudD1sYmw7CiAgICBpZighcilyZXR1cm47CiAgICBpZihyLmxpbmVzKXNo"
    "ZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5DaGFubmVsIGNo"
    "ZWNrPC9oMz4KICAgICAgPHAgY2xhc3M9InN1YiIgc3R5bGU9IndoaXRlLXNwYWNlOnByZS1saW5l"
    "Ij4ke2VzYyhyLmxpbmVzLmpvaW4oIlxuIikpfTwvcD4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+"
    "YCk7CiAgICBlbHNlIGlmKHdoYXQ9PT0ibG9ndGVzdCIpdG9hc3QoIlRlc3QgbWVzc2FnZSBzZW50"
    "IHRvIHRoZSBsb2cgY2hhbm5lbCIpOwogICAgZWxzZSBpZih3aGF0PT09ImNsZWFyc3RvY2siKXRv"
    "YXN0KHIucmVtb3ZlZCsiIHJlbW92ZWQiKTsKICAgIGVsc2UgdG9hc3QoIkRvbmUiKTsKICAgIGF3"
    "YWl0IHJlZnJlc2goKTsKICB9KTsKICBjb25zdCB1cGlhZGQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5"
    "SWQoInVwaWFkZCIpOwogIGlmKHVwaWFkZCl1cGlhZGQub25jbGljaz1hc3luYygpPT57CiAgICBj"
    "b25zdCBpPXVwaWFkZC5jbG9zZXN0KCJkaXYiKS5xdWVyeVNlbGVjdG9yKCJpbnB1dCIpOwogICAg"
    "Y29uc3Qgdj1pPyhpLnZhbHVlfHwiIikudHJpbSgpOiIiOwogICAgaWYoIXYpe3RvYXN0KCJQYXN0"
    "ZSB0aGUgZXh0cmEgVVBJIGhhbmRsZSIpO3JldHVybjt9CiAgICBjb25zdCBjdXI9KEFETS5wYXkm"
    "JkFETS5wYXkuZXh0cmEpfHwiIjsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWlu"
    "L3NldHRpbmciLHtrZXk6InVwaV9leHRyYSIsdmFsdWU6Y3VyPyhjdXIrIiwiK3YpOnZ9KTsKICAg"
    "IGlmKHIpe2kudmFsdWU9IiI7dG9hc3QoIkhhbmRsZSBhZGRlZCIpO2F3YWl0IHJlZnJlc2goKTt9"
    "CiAgfTsKICBjb25zdCBhZG1pbmFkZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYWRtaW5hZGQi"
    "KTsKICBpZihhZG1pbmFkZClhZG1pbmFkZC5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGk9"
    "YWRtaW5hZGQuY2xvc2VzdCgiZGl2IikucXVlcnlTZWxlY3RvcigiaW5wdXQiKTsKICAgIGNvbnN0"
    "IHY9aT8oaS52YWx1ZXx8IiIpLnRyaW0oKS5yZXBsYWNlKC9eQC8sIiIpOiIiOwogICAgaWYoIXYp"
    "e3RvYXN0KCJTZW5kIHRoZSBudW1lcmljIHVzZXIgaWQiKTtyZXR1cm47fQogICAgY29uc3Qgcj1h"
    "d2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vbWlzYyIse3doYXQ6ImFkZGFkbWluIix2YWx1ZTp2fSk7"
    "CiAgICBpZihyKXtpLnZhbHVlPSIiO3RvYXN0KCJBZG1pbiBhZGRlZCB3aXRoIG5vIHBvd2VycyDi"
    "gJQgZ3JhbnQgdGhlbSBpbiB0aGUgYm90Iik7YXdhaXQgcmVmcmVzaCgpO30KICB9OwogIGNvbnN0"
    "IHByaXplPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJwcml6ZXNhdmUiKTsKICBpZihwcml6ZSlw"
    "cml6ZS5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGk9cHJpemUuY2xvc2VzdCgiZGl2Iiku"
    "cXVlcnlTZWxlY3RvcigiaW5wdXQiKTsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2Fk"
    "bWluL3NldHRpbmciLHtrZXk6ImxlYWRlcmJvYXJkX3ByaXplIix2YWx1ZTooaSYmaS52YWx1ZSl8"
    "fCIwIn0pOwogICAgaWYocil0b2FzdCgiUHJpemUgc2F2ZWQiKTsKICB9OwogIGNvbnN0IGNwYWRk"
    "PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjcGFkZCIpOwogIGlmKGNwYWRkKWNwYWRkLm9uY2xp"
    "Y2s9YXN5bmMoKT0+ewogICAgY29uc3QgY29kZT0oKGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJj"
    "cGNvZGUiKXx8e30pLnZhbHVlfHwiIikudHJpbSgpOwogICAgY29uc3QgcGN0PSgoZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoImNwcGN0Iil8fHt9KS52YWx1ZXx8IiIpLnRyaW0oKTsKICAgIGlmKCFj"
    "b2RlfHwhcGN0KXt0b2FzdCgiQ29kZSBhbmQgcGVyY2VudCwgcGxlYXNlIik7cmV0dXJuO30KICAg"
    "IGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgIGNvbnN0IHI9YXdh"
    "aXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NvdXBvbiIse2NvZGU6Y29kZSxwZXJjZW50OnBjdH0pOwog"
    "ICAgaWYoIXIpcmV0dXJuOwogICAgY29uc3QgYTE9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNw"
    "Y29kZSIpLGEyPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjcHBjdCIpOwogICAgaWYoYTEpYTEu"
    "dmFsdWU9IiI7aWYoYTIpYTIudmFsdWU9IiI7CiAgICB0b2FzdCgiQ291cG9uICIrY29kZS50b1Vw"
    "cGVyQ2FzZSgpKyIgaXMgbGl2ZSIpO2F3YWl0IHJlZnJlc2goKTsKICB9OwogIGNvbnN0IGZnPWRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJmbGFzaGdvIik7CiAgaWYoZmcpZmcub25jbGljaz1hc3lu"
    "YygpPT57CiAgICBjb25zdCBjYXJkPWZnLmNsb3Nlc3QoIi5jYXJkIil8fGRvY3VtZW50OwogICAg"
    "Y29uc3QgaW5zPWNhcmQucXVlcnlTZWxlY3RvckFsbCgiaW5wdXQiKTsKICAgIGNvbnN0IHBjdD0o"
    "KGluc1swXSYmaW5zWzBdLnZhbHVlKXx8IjIwIiksIGhycz0oKGluc1sxXSYmaW5zWzFdLnZhbHVl"
    "KXx8IjUiKTsKICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAg"
    "IGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2ZsYXNoIix7cGVyY2VudDpwY3QsaG91"
    "cnM6aHJzfSk7CiAgICBpZihyKXt0b2FzdCgiRmxhc2ggc2FsZSBsaXZlIOKAlCAiK3BjdCsiJSBm"
    "b3IgIitocnMrImgiKTthd2FpdCByZWZyZXNoKCk7fQogIH07CiAgY29uc3QgYmM9ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoImJjZ28iKTsKICBpZihiYyliYy5vbmNsaWNrPWFzeW5jKCk9PnsKICAg"
    "IGNvbnN0IGNhcmQ9YmMuY2xvc2VzdCgiLmNhcmQiKXx8ZG9jdW1lbnQ7CiAgICBjb25zdCB0YT1j"
    "YXJkLnF1ZXJ5U2VsZWN0b3IoInRleHRhcmVhIik7CiAgICBjb25zdCB0ZXh0PSgodGEmJnRhLnZh"
    "bHVlKXx8IiIpLnRyaW0oKTsKICAgIGlmKCF0ZXh0KXt0b2FzdCgiV3JpdGUgdGhlIG1lc3NhZ2Ug"
    "Zmlyc3QiKTtpZih0YSl0YS5mb2N1cygpO3JldHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlBy"
    "ZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBiYy5kaXNhYmxlZD10cnVlO2JjLnRleHRDb250ZW50"
    "PSJTZW5kaW5n4oCmIjsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2Jyb2Fk"
    "Y2FzdCIse3RleHQ6dGV4dH0pOwogICAgYmMuZGlzYWJsZWQ9ZmFsc2U7YmMudGV4dENvbnRlbnQ9"
    "IlNlbmQgQnJvYWRjYXN0IjsKICAgIGlmKHIpe3RvYXN0KCJTZW50IHRvICIrci5zZW50KyIgcGVv"
    "cGxlIik7aWYodGEpdGEudmFsdWU9IiI7fQogIH07CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiW2RhdGEtZGVwLW9rXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNv"
    "bnN0IGlkPWIuZGF0YXNldC5kZXBPazsKICAgIGlmKCFpZCl7dG9hc3QoIk5vdGhpbmcgdG8gYXBw"
    "cm92ZSIpO3JldHVybjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9kZXBv"
    "c2l0Iix7aWQ6K2lkLGFjdGlvbjoiYXBwcm92ZSJ9KTsKICAgIGlmKHIpe3RvYXN0KCJDcmVkaXRl"
    "ZCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgi"
    "W2RhdGEtcmV2XSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHI9"
    "YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3JldmlldyIse2lkOitiLmRhdGFzZXQucmV2fSk7CiAg"
    "ICBpZihyKXt0b2FzdCgiVXBkYXRlZCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgfSk7CiAgZG9jdW1l"
    "bnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtc2V0dGluZ10iKS5mb3JFYWNoKHQ9PnsKICAgIHQu"
    "b25jbGljaz1hc3luYygpPT57CiAgICAgIGNvbnN0IGtleT10LmRhdGFzZXQuc2V0dGluZywgb249"
    "IXQuY2xhc3NMaXN0LmNvbnRhaW5zKCJvbiIpOwogICAgICB0LmNsYXNzTGlzdC50b2dnbGUoIm9u"
    "Iixvbik7CiAgICAgIGlmKCFMSVZFKXt0b2FzdChvbj8iT24iOiJPZmYiKTtyZXR1cm47fQogICAg"
    "ICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OmtleSx2YWx1"
    "ZTpvbj8iMSI6IjAifSk7CiAgICAgIGlmKCFyKXt0LmNsYXNzTGlzdC50b2dnbGUoIm9uIiwhb24p"
    "O3JldHVybjt9CiAgICAgIEFETS5zdz1BRE0uc3d8fHt9O0FETS5zd1trZXldPW9uOwogICAgICB0"
    "b2FzdChrZXkucmVwbGFjZSgvXy9nLCIgIikrKG9uPyIgb24iOiIgb2ZmIikpOwogICAgfTsKICB9"
    "KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1hZG1pbi1hY3RdIikuZm9yRWFj"
    "aChiPT57CiAgICBiLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25zdCBhY3Q9Yi5kYXRhc2V0"
    "LmFkbWluQWN0OwogICAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9"
    "CiAgICAgIGNvbnN0IGxhYmVsPWIudGV4dENvbnRlbnQ7Yi5kaXNhYmxlZD10cnVlO2IudGV4dENv"
    "bnRlbnQ9IldvcmtpbmfigKYiOwogICAgICBsZXQgcj1udWxsOwogICAgICBpZihhY3Q9PT0iYmFj"
    "a3VwIikgICAgICByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9iYWNrdXAiLHt9KTsKICAgICAg"
    "ZWxzZSBpZihhY3Q9PT0iZGlnZXN0Iikgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vZGlnZXN0"
    "Iix7fSk7CiAgICAgIGVsc2UgaWYoYWN0PT09ImV4cG9ydCIpIHI9YXdhaXQgYXBpVHJ5KCIvYXBp"
    "L2FkbWluL2V4cG9ydCIse30pOwogICAgICBlbHNlIGlmKGFjdD09PSJzeW5jIikgICByPWF3YWl0"
    "IGFwaVRyeSgiL2FwaS9hZG1pbi9zeW5jIix7fSk7CiAgICAgIGIuZGlzYWJsZWQ9ZmFsc2U7Yi50"
    "ZXh0Q29udGVudD1sYWJlbDsKICAgICAgaWYoIXIpcmV0dXJuOwogICAgICBpZihhY3Q9PT0iYmFj"
    "a3VwIil0b2FzdCgiRGF0YWJhc2Ugc2VudCB0byB5b3UgaW4gY2hhdCIpOwogICAgICBpZihhY3Q9"
    "PT0iZGlnZXN0Iil0b2FzdCgiRGlnZXN0IHNlbnQgdG8gdGhlIGFkbWlucyIpOwogICAgICBpZihh"
    "Y3Q9PT0iZXhwb3J0Iil0b2FzdChyLnJvd3MrIiBvcmRlcnMgc2VudCBhcyBDU1YiKTsKICAgICAg"
    "aWYoYWN0PT09InN5bmMiKXRvYXN0KCJDb21tYW5kcyBzeW5jZWQgd2l0aCBUZWxlZ3JhbSIpOwog"
    "ICAgICBhd2FpdCByZWZyZXNoKCk7CiAgICB9OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIltkYXRhLXByaWNlXSIpLmZvckVhY2goaW5wPT57CiAgICBpbnAub25jaGFuZ2U9YXN5"
    "bmMoKT0+ewogICAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAg"
    "ICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3ByaWNpbmciLHtrZXk6aW5wLmRh"
    "dGFzZXQucHJpY2UsdmFsdWU6aW5wLnZhbHVlfSk7CiAgICAgIGlmKHIpe3RvYXN0KCJQcmljaW5n"
    "IHVwZGF0ZWQiKTthd2FpdCByZWZyZXNoKCk7fQogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVy"
    "eVNlbGVjdG9yQWxsKCJbZGF0YS1jcHJpY2VdIikuZm9yRWFjaChpbnA9PnsKICAgIGlucC5vbmNo"
    "YW5nZT1hc3luYygpPT57CiAgICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0"
    "dXJuO30KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vcHJpY2luZyIse2Nv"
    "dW50cnk6aW5wLmRhdGFzZXQuY3ByaWNlLHByaWNlOmlucC52YWx1ZX0pOwogICAgICBpZihyKXt0"
    "b2FzdChpbnAuZGF0YXNldC5jcHJpY2UrIiBub3cgIittb25leShpbnAudmFsdWUpKTthd2FpdCBy"
    "ZWZyZXNoKCk7fQogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0"
    "YS1xYV0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgY29uc3Qgd2hhdD1iLmRhdGFz"
    "ZXQucWE7CiAgICBjb25zdCBnbz0odGFiLG1zZyk9PnsKICAgICAgY29uc3QgdD1kb2N1bWVudC5x"
    "dWVyeVNlbGVjdG9yKGAjYXN1YiBidXR0b25bZGF0YS1hPSIke3RhYn0iXWApOwogICAgICBpZih0"
    "KXt0LmNsaWNrKCk7dG9hc3QobXNnKTt9ZWxzZSB0b2FzdChtc2cpOwogICAgfTsKICAgIGlmKHdo"
    "YXQ9PT0iYnJvYWRjYXN0IilyZXR1cm4gZ28oIm1hcmtldCIsIkNvbXBvc2UgdGhlIGJyb2FkY2Fz"
    "dCBmcm9tIHRoZSBib3Q6IC9hZG1pbiDihpIgTWFya2V0aW5nIik7CiAgICBpZih3aGF0PT09InN0"
    "b2NrIilyZXR1cm4gZ28oInN0b2NrIiwiQWRkIHN0b2NrIGZyb20gdGhlIGJvdDogL2FkbWluIOKG"
    "kiBTdG9jayDihpIgQWRkIHN0b2NrIik7CiAgICBpZih3aGF0PT09ImZsYXNoIilyZXR1cm4gZ28o"
    "Im1hcmtldCIsIkZsYXNoIHNhbGUgbGl2ZXMgaGVyZSIpOwogICAgaWYod2hhdD09PSJiYWNrdXAi"
    "KXJldHVybiB0b2FzdCgiQmFja3VwcyBhcmUgc2VudCB0byB5b3VyIGxvZyBjaGFubmVsIOKAlCAv"
    "YWRtaW4g4oaSIFNldHRpbmdzIik7CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgi"
    "W2RhdGEtc2V0XSIpLmZvckVhY2goaW5wPT57CiAgICBpbnAub25jaGFuZ2U9YXN5bmMoKT0+ewog"
    "ICAgICBjb25zdCBrZXk9aW5wLmRhdGFzZXQuc2V0LCB2YWw9aW5wLnZhbHVlOwogICAgICBpZigh"
    "TElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICAgIGNvbnN0IHI9YXdhaXQg"
    "YXBpVHJ5KCIvYXBpL2FkbWluL3NldHRpbmciLHtrZXk6a2V5LHZhbHVlOnZhbH0pOwogICAgICBp"
    "ZihyKXt0b2FzdChrZXkucmVwbGFjZSgvXy9nLCIgIikrIiBzYXZlZCIpO2F3YWl0IHJlZnJlc2go"
    "KTt9CiAgICB9OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNtc10i"
    "KS5mb3JFYWNoKHQ9PnsKICAgIHQub25pbnB1dD0oKT0+e0NNU1t0LmRhdGFzZXQuY21zXT10LnZh"
    "bHVlO2FwcGx5Q01TKCk7CiAgICAgIGNsZWFyVGltZW91dCh0Ll9zdik7dC5fc3Y9c2V0VGltZW91"
    "dCgoKT0+cHVzaCgiL2FwaS9hZG1pbi9jb250ZW50Iix7a2V5OnQuZGF0YXNldC5jbXMsdmFsdWU6"
    "dC52YWx1ZX0pLDUwMCk7CiAgICAgIHQuY2xvc2VzdCgiLmNtc3JvdyIpLmRhdGFzZXQuZj0odC5j"
    "bG9zZXN0KCIuY21zcm93IikucXVlcnlTZWxlY3RvcigibGFiZWwiKS50ZXh0Q29udGVudCsiICIr"
    "dC52YWx1ZSkudG9Mb3dlckNhc2UoKTt9O30pOwogIGNvbnN0IGNxPWRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJjcSIpOwogIGlmKGNxKWNxLm9uaW5wdXQ9KCk9Pntjb25zdCBxPWNxLnZhbHVlLnRy"
    "aW0oKS50b0xvd2VyQ2FzZSgpOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiLmNtc3Jv"
    "dyIpLmZvckVhY2gocj0+ci5jbGFzc0xpc3QudG9nZ2xlKCJoaWRlIiwhIXEmJiFyLmRhdGFzZXQu"
    "Zi5pbmNsdWRlcyhxKSkpOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiLmNtc2dycCIp"
    "LmZvckVhY2goZz0+Zy5jbGFzc0xpc3QudG9nZ2xlKCJoaWRlIiwhZy5xdWVyeVNlbGVjdG9yKCIu"
    "Y21zcm93Om5vdCguaGlkZSkiKSkpO307CiAgY29uc3QgY3I9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5"
    "SWQoImNtc3Jlc2V0Iik7CiAgaWYoY3IpY3Iub25jbGljaz0oKT0+e09iamVjdC5hc3NpZ24oQ01T"
    "LENNUzApO2FwcGx5Q01TKCk7cmVuZGVyQWRtaW4oKTt0b2FzdCgiVGV4dCByZXNldCB0byBkZWZh"
    "dWx0cyIpO307CiAgY29uc3QgYWRkPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjaC1hZGQiKTsK"
    "ICBpZihhZGQpYWRkLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgaT1kb2N1bWVudC5nZXRF"
    "bGVtZW50QnlJZCgiY2gtaW4iKSx2PShpLnZhbHVlfHwiIikudHJpbSgpOwogICAgaWYoIXYpe3Rv"
    "YXN0KCJQYXN0ZSBhIGNoYW5uZWwgbGluayBmaXJzdCIpO2kuZm9jdXMoKTtyZXR1cm59CiAgICBj"
    "b25zdCByZXE9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNoLXR5cGUiKS52YWx1ZT09PSIxIjsK"
    "ICAgIGlmKExJVkUpewogICAgICBhZGQuZGlzYWJsZWQ9dHJ1ZTsKICAgICAgY29uc3Qgcj1hd2Fp"
    "dCBhcGlUcnkoIi9hcGkvYWRtaW4vY2hhbm5lbCIse2FjdGlvbjoiYWRkIixoYW5kbGU6dixyZXF1"
    "aXJlZDpyZXF9KTsKICAgICAgYWRkLmRpc2FibGVkPWZhbHNlOwogICAgICBpZighcilyZXR1cm47"
    "CiAgICAgIGkudmFsdWU9IiI7YXdhaXQgcmVmcmVzaCgpO3RvYXN0KCJDaGFubmVsIHNhdmVkIOKA"
    "lCB0aGUgYm90IHVzZXMgaXQgbm93Iik7cmV0dXJuOwogICAgfQogICAgQ0hBTk5FTFMucHVzaCh7"
    "aWQ6RGF0ZS5ub3coKSxuYW1lOnYucmVwbGFjZSgvXmh0dHBzPzpcL1wvdFwubWVcLy8sIkAiKS5z"
    "bGljZSgwLDI2KSxoYW5kbGU6dixyZXE6cmVxLG9uOnRydWUsbWVtYmVyczoi4oCUIn0pOwogICAg"
    "aS52YWx1ZT0iIjtyZW5kZXJBZG1pbigpO3RvYXN0KCJDaGFubmVsIGFkZGVkIik7fTsKICBjb25z"
    "dCBsZ2FkZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgibGctYWRkIik7CiAgaWYobGdhZGQpbGdh"
    "ZGQub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBpPWRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJsZy1pbiIpLHY9KGkudmFsdWV8fCIiKS50cmltKCk7CiAgICBpZighdil7dG9hc3QoIlBhc3Rl"
    "IGEgbG9nIGNoYW5uZWwgZmlyc3QiKTtpLmZvY3VzKCk7cmV0dXJufQogICAgaWYoTElWRSl7CiAg"
    "ICAgIGxnYWRkLmRpc2FibGVkPXRydWU7CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBp"
    "L2FkbWluL2NoYW5uZWwiLHthY3Rpb246ImFkZCIsaGFuZGxlOnYsbG9nOnRydWV9KTsKICAgICAg"
    "bGdhZGQuZGlzYWJsZWQ9ZmFsc2U7CiAgICAgIGlmKCFyKXJldHVybjsKICAgICAgaS52YWx1ZT0i"
    "Ijthd2FpdCByZWZyZXNoKCk7dG9hc3QoIkxvZyBjaGFubmVsIHNhdmVkIik7cmV0dXJuOwogICAg"
    "fQogICAgTE9HQ0gucHVzaCh7aWQ6RGF0ZS5ub3coKSxuYW1lOnYucmVwbGFjZSgvXmh0dHBzPzpc"
    "L1wvdFwubWVcLy8sIkAiKS5zbGljZSgwLDI0KSxoYW5kbGU6dixwcmltYXJ5OkxPR0NILmxlbmd0"
    "aD09PTAsb246dHJ1ZX0pOwogICAgaS52YWx1ZT0iIjtyZW5kZXJBZG1pbigpO3RvYXN0KCJMb2cg"
    "Y2hhbm5lbCBhZGRlZCIpO307CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2hk"
    "ZWxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYoTElWRSl7Y29uc3Qg"
    "cj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vY2hhbm5lbCIse2FjdGlvbjoiZGVsIixpZDorYi5k"
    "YXRhc2V0LmNoZGVsfSk7CiAgICAgIGlmKHIpe2F3YWl0IHJlZnJlc2goKTt0b2FzdCgiQ2hhbm5l"
    "bCByZW1vdmVkIik7fXJldHVybjt9CiAgICBDSEFOTkVMUz1DSEFOTkVMUy5maWx0ZXIoYz0+Yy5p"
    "ZCE9Yi5kYXRhc2V0LmNoZGVsKTtyZW5kZXJBZG1pbigpO3RvYXN0KCJDaGFubmVsIHJlbW92ZWQi"
    "KX0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNocmVxXSIpLmZvckVhY2go"
    "Yj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKExJVkUpe2NvbnN0IHI9YXdhaXQgYXBpVHJ5"
    "KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rpb246InJlcXVpcmVkIixpZDorYi5kYXRhc2V0LmNo"
    "cmVxfSk7CiAgICAgIGlmKHIpe2F3YWl0IHJlZnJlc2goKTt0b2FzdCgiVXBkYXRlZCIpO31yZXR1"
    "cm47fQogICAgY29uc3QgYz1DSEFOTkVMUy5maW5kKHg9PnguaWQ9PWIuZGF0YXNldC5jaHJlcSk7"
    "Yy5yZXE9IWMucmVxO3JlbmRlckFkbWluKCk7dG9hc3QoYy5yZXE/Ik5vdyByZXF1aXJlZCI6Ik5v"
    "dyBvcHRpb25hbCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2hvbl0i"
    "KS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBpZihMSVZFKXtjb25zdCByPWF3"
    "YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJ0b2dnbGUiLGlkOitiLmRh"
    "dGFzZXQuY2hvbn0pOwogICAgICBpZihyKXthd2FpdCByZWZyZXNoKCk7dG9hc3QoIlVwZGF0ZWQi"
    "KTt9cmV0dXJuO30KICAgIGNvbnN0IGM9Q0hBTk5FTFMuZmluZCh4PT54LmlkPT1iLmRhdGFzZXQu"
    "Y2hvbik7Yy5vbj0hYy5vbjtyZW5kZXJBZG1pbigpO3RvYXN0KGMub24/IkNoYW5uZWwgZW5hYmxl"
    "ZCI6IkNoYW5uZWwgcGF1c2VkIil9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0"
    "YS1sZ2RlbF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgY29uc3Qgd2FzUD1MT0dD"
    "SC5maW5kKGw9PmwuaWQ9PWIuZGF0YXNldC5sZ2RlbCk/LnByaW1hcnk7CiAgICBMT0dDSD1MT0dD"
    "SC5maWx0ZXIobD0+bC5pZCE9Yi5kYXRhc2V0LmxnZGVsKTsKICAgIGlmKHdhc1AmJkxPR0NIWzBd"
    "KUxPR0NIWzBdLnByaW1hcnk9dHJ1ZTtyZW5kZXJBZG1pbigpO3RvYXN0KCJMb2cgY2hhbm5lbCBy"
    "ZW1vdmVkIil9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1sZ3N0YXJdIiku"
    "Zm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIExPR0NILmZvckVhY2gobD0+bC5wcmltYXJ5"
    "PShsLmlkPT1iLmRhdGFzZXQubGdzdGFyKSk7cmVuZGVyQWRtaW4oKTt0b2FzdCgiUHJlZmVycmVk"
    "IGxvZyBjaGFubmVsIHNldCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEt"
    "bGdvbl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+e2NvbnN0IGw9TE9HQ0guZmluZCh4PT54"
    "LmlkPT1iLmRhdGFzZXQubGdvbik7bC5vbj0hbC5vbjtyZW5kZXJBZG1pbigpO3RvYXN0KGwub24/"
    "IkNoYW5uZWwgb24iOiJDaGFubmVsIG11dGVkIil9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "QWxsKCJbZGF0YS1ldm9uXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57Y29uc3QgZT1FVkVO"
    "VFNbK2IuZGF0YXNldC5ldm9uXTtlLm9uPSFlLm9uO3JlbmRlckFkbWluKCl9KTsKICBkb2N1bWVu"
    "dC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1ldmNoXSIpLmZvckVhY2gocz0+cy5vbmNoYW5nZT0o"
    "KT0+e0VWRU5UU1srcy5kYXRhc2V0LmV2Y2hdLmNoPStzLnZhbHVlO3RvYXN0KCJSb3V0aW5nIHVw"
    "ZGF0ZWQiKX0pOwogIGNvbnN0IGFsbD1kb2N1bWVudC5xdWVyeVNlbGVjdG9yKCJbZGF0YS1ldi1h"
    "bGxdIik7CiAgaWYoYWxsKWFsbC5vbmNsaWNrPSgpPT57Y29uc3QgdD0hRVZFTlRTLmV2ZXJ5KGU9"
    "PmUub24pO0VWRU5UUy5mb3JFYWNoKGU9PmUub249dCk7cmVuZGVyQWRtaW4oKTt0b2FzdCh0PyJB"
    "bGwgZXZlbnRzIG9uIjoiQWxsIGV2ZW50cyBvZmYiKX07CiAgY29uc3QgbT1kb2N1bWVudC5xdWVy"
    "eVNlbGVjdG9yKCJbZGF0YS1tYXN0ZXJdIik7CiAgaWYobSltLm9uY2xpY2s9KCk9PntMT0dTX01B"
    "U1RFUj0hTE9HU19NQVNURVI7cmVuZGVyQWRtaW4oKTt0b2FzdChMT0dTX01BU1RFUj8iTG9nZ2lu"
    "ZyBlbmFibGVkIjoiQWxsIGxvZ2dpbmcgZGlzYWJsZWQiKX07CiAgZG9jdW1lbnQucXVlcnlTZWxl"
    "Y3RvckFsbCgiW2RhdGEtYS1nb10iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgZG9j"
    "dW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI2FzdWIgYnV0dG9uIikuZm9yRWFjaCh4PT54LmNsYXNz"
    "TGlzdC50b2dnbGUoIm9uIix4LmRhdGFzZXQuYT09PWIuZGF0YXNldC5hR28pKTsKICAgIEFUQUI9"
    "Yi5kYXRhc2V0LmFHbztyZW5kZXJBZG1pbigpO3dpbmRvdy5zY3JvbGxUbygwLDApO30pOwogIGRv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXRnXSIpLmZvckVhY2godD0+dC5vbmNsaWNr"
    "PSgpPT50LmNsYXNzTGlzdC50b2dnbGUoIm9uIikpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JB"
    "bGwoIltkYXRhLXNldHNlbF0iKS5mb3JFYWNoKHNlbD0+c2VsLm9uY2hhbmdlPWFzeW5jKCk9PnsK"
    "ICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgIGNvbnN0IHI9"
    "YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3NldHRpbmciLHtrZXk6c2VsLmRhdGFzZXQuc2V0c2Vs"
    "LHZhbHVlOnNlbC52YWx1ZX0pOwogICAgaWYocil0b2FzdCgiU2F2ZWQiKTsKICB9KTsKfQoKY29u"
    "c3QgX2NjPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjYXJ0Y2hpcCIpOwppZihfY2MpX2NjLm9u"
    "Y2xpY2s9Y2FydFNoZWV0OwpyZW5kZXIoKTtyZW5kZXJBZG1pbigpO2FwcGx5RmVhdHVyZXMoKTsK"
    "Ci8qIOKUgOKUgCBsaXZlIGRhdGE6IGV2ZXJ5dGhpbmcgYmVsb3cgY29tZXMgZnJvbSB0aGUgYm90"
    "IGRhdGFiYXNlIHdoZW4gc2VydmVkIGJ5IHRoZSBib3Qg4pSA4pSAICovCi8qIExJVkUgLyBNRSAv"
    "IFRHIC8gSU5JVCBhcmUgZGVjbGFyZWQgYXQgdGhlIHZlcnkgdG9wIG9mIHRoaXMgc2NyaXB0ICov"
    "CmFzeW5jIGZ1bmN0aW9uIGFwaShwYXRoLGJvZHkpewogIGNvbnN0IG89e2hlYWRlcnM6eyJDb250"
    "ZW50LVR5cGUiOiJhcHBsaWNhdGlvbi9qc29uIiwiWC1Jbml0LURhdGEiOklOSVQsCiAgICAiWC1U"
    "ZWxlZ3JhbS1Jbml0LURhdGEiOklOSVQsIlgtQXBwLUtleSI6QVBQS0VZfX07CiAgaWYoYm9keSE9"
    "PXVuZGVmaW5lZCl7by5tZXRob2Q9IlBPU1QiO28uYm9keT1KU09OLnN0cmluZ2lmeShib2R5KTt9"
    "CiAgY29uc3Qgcj1hd2FpdCBmZXRjaChwYXRoLG8pOwogIGxldCBkPW51bGw7dHJ5e2Q9YXdhaXQg"
    "ci5qc29uKCk7fWNhdGNoKGVycil7fQogIGlmKCFyLm9rKXtjb25zdCBlPW5ldyBFcnJvcigoZCYm"
    "ZC5lcnJvcil8fChwYXRoKyIgIityLnN0YXR1cykpO2Uuc3RhdHVzPXIuc3RhdHVzO2UuZGF0YT1k"
    "O3Rocm93IGU7fQogIHJldHVybiBkOwp9CmZ1bmN0aW9uIGFkbWluQWxsb3dlZCgpe3JldHVybiAh"
    "TElWRSB8fCAhIShNRSYmTUUuYWRtaW4pO30KZnVuY3Rpb24gYXBwbHlTdGF0ZShkKXsKICAvKiBl"
    "dmVyeSBsaXN0IGlzIHJlcGxhY2VkLCBldmVuIHdoZW4gZW1wdHkg4oCUIGEgbmV3IHNob3AgbXVz"
    "dCBuZXZlciBzaG93IGRlbW8gZGF0YSAqLwogIGlmKGQuZmVhdHVyZXMpT2JqZWN0LmtleXMoZC5m"
    "ZWF0dXJlcykuZm9yRWFjaChrPT57aWYoayBpbiBGRUFUKUZFQVRba109ISFkLmZlYXR1cmVzW2td"
    "O30pOwogIGlmKGQuY29udGVudClPYmplY3Qua2V5cyhkLmNvbnRlbnQpLmZvckVhY2goaz0+e2lm"
    "KGsgaW4gQ01TJiZkLmNvbnRlbnRba10hPW51bGwpQ01TW2tdPWQuY29udGVudFtrXTt9KTsKICBp"
    "ZihBcnJheS5pc0FycmF5KGQuc3RvY2spKXtTVE9DSy5sZW5ndGg9MDtkLnN0b2NrLmZvckVhY2go"
    "eD0+U1RPQ0sucHVzaCh4KSk7RklMVEVSRUQ9U1RPQ0suc2xpY2UoKTt9CiAgaWYoQXJyYXkuaXNB"
    "cnJheShkLm9yZGVycykpe09SREVSUy5sZW5ndGg9MDtkLm9yZGVycy5mb3JFYWNoKHg9Pk9SREVS"
    "Uy5wdXNoKHgpKTt9CiAgaWYoQXJyYXkuaXNBcnJheShkLnR4KSl7VFgubGVuZ3RoPTA7ZC50eC5m"
    "b3JFYWNoKHg9PlRYLnB1c2goeCkpO30KICBpZihBcnJheS5pc0FycmF5KGQuZmVlZCkpRkVFRD1k"
    "LmZlZWQ7CiAgaWYoZC5lYXJuKUVBUk49T2JqZWN0LmFzc2lnbih7fSxFQVJOLGQuZWFybik7CiAg"
    "aWYoZC5wcmVmcylQUkVGUz1PYmplY3QuYXNzaWduKHt9LFBSRUZTLGQucHJlZnMpOwogIGlmKEFy"
    "cmF5LmlzQXJyYXkoZC5hbGVydHMpKXdpbmRvdy5fX2FsZXJ0cz1kLmFsZXJ0czsKICBpZihkLnN0"
    "b3JlKXsKICAgIFNUT1JFPU9iamVjdC5hc3NpZ24oe30sU1RPUkUsZC5zdG9yZSk7CiAgICBpZihk"
    "LnN0b3JlLm5hbWUpQ0ZHLm5hbWU9ZC5zdG9yZS5uYW1lOwogICAgaWYoZC5zdG9yZS50YWcpQ0ZH"
    "LnRhZz1kLnN0b3JlLnRhZzsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXN0"
    "b3JlbmFtZV0iKS5mb3JFYWNoKGU9PmUudGV4dENvbnRlbnQ9Q0ZHLm5hbWUpOwogICAgZG9jdW1l"
    "bnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtc3RvcmV0YWddIikuZm9yRWFjaChlPT5lLnRleHRD"
    "b250ZW50PUNGRy50YWcpOwogICAgZG9jdW1lbnQudGl0bGU9Q0ZHLm5hbWU7CiAgfQogIGlmKGQu"
    "YWRtKXsKICAgIEFETT1PYmplY3QuYXNzaWduKHt9LEFETSxkLmFkbSk7CiAgICBpZihBcnJheS5p"
    "c0FycmF5KEFETS5jaGFubmVscykpCiAgICAgIENIQU5ORUxTPUFETS5jaGFubmVscy5tYXAoYz0+"
    "KHtpZDpjLmlkLG5hbWU6Yy5uYW1lLGhhbmRsZTpjLmhhbmRsZXx8IiIsCiAgICAgICAgcmVxOiEh"
    "Yy5yZXF1aXJlZCxvbjohIWMuYWN0aXZlLG1lbWJlcnM6IuKAlCJ9KSk7CiAgICBpZihBRE0ubG9n"
    "cyl7CiAgICAgIExPR1NfTUFTVEVSPSEhQURNLmxvZ3MubWFzdGVyOwogICAgICBpZihBcnJheS5p"
    "c0FycmF5KEFETS5sb2dzLmNoYW5uZWxzKSkKICAgICAgICBMT0dDSD1BRE0ubG9ncy5jaGFubmVs"
    "cy5tYXAoYz0+KHtpZDpjLmlkLG5hbWU6Yy5uYW1lLGhhbmRsZToiIiwKICAgICAgICAgIHByaW1h"
    "cnk6ISFjLnByaW1hcnksb246ISFjLmFjdGl2ZX0pKTsKICAgICAgaWYoQXJyYXkuaXNBcnJheShB"
    "RE0ubG9ncy5ldmVudHMpJiZBRE0ubG9ncy5ldmVudHMubGVuZ3RoKQogICAgICAgIEVWRU5UUz1B"
    "RE0ubG9ncy5ldmVudHMubWFwKGU9Pih7azplLmxhYmVsLG9uOiEhZS5vbixjaDooTE9HQ0hbMF18"
    "fHt9KS5pZHx8MX0pKTsKICAgIH0KICB9CiAgaWYoZC5yYXRpbmdzKVJFVklFV1M9ZC5yYXRpbmdz"
    "OwogIGlmKHR5cGVvZiBkLmJhbGFuY2U9PT0ibnVtYmVyIilCQUw9ZC5iYWxhbmNlOwogIGlmKGQu"
    "bWUpTUU9ZC5tZTsKICBpZihkLmNhcnQpQ0FSVD1kLmNhcnQ7CiAgaWYoZC53aXNobGlzdClXSVNI"
    "PWQud2lzaGxpc3Q7Cn0KZnVuY3Rpb24gc2hvd1N3aXRjaChvbil7CiAgY29uc3Qgc3c9ZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoInN3Iik7CiAgaWYoc3cpc3cuc3R5bGUuZGlzcGxheT1vbj8iZmxl"
    "eCI6Im5vbmUiOwp9CnZhciBMT0NLX1RJVExFPSIiLExPQ0tfTVNHPSIiOwpmdW5jdGlvbiBsb2Nr"
    "ZWRTY3JlZW4odGl0bGUsbXNnKXsKICBMT0NLRUQ9dHJ1ZTtMT0NLX1RJVExFPXRpdGxlO0xPQ0tf"
    "TVNHPW1zZzsKICBzaG93U3dpdGNoKGZhbHNlKTsKICBwYWludExvY2tlZCgpOwp9CmZ1bmN0aW9u"
    "IHBhaW50TG9ja2VkKCl7CiAgY29uc3Qgc3Q9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0YWdl"
    "Iik7CiAgaWYoc3Qpc3QuaW5uZXJIVE1MPSc8ZGl2IGNsYXNzPSJjYXJkIiBzdHlsZT0ibWFyZ2lu"
    "OjE4cHg7dGV4dC1hbGlnbjpjZW50ZXI7cGFkZGluZzoyMnB4Ij4nCiAgICArJzxoMyBzdHlsZT0i"
    "bWFyZ2luOjAgMCA4cHgiPicrTE9DS19USVRMRSsnPC9oMz4nCiAgICArJzxwIGNsYXNzPSJzdWIi"
    "IHN0eWxlPSJtYXJnaW46MCAwIDE0cHgiPicrTE9DS19NU0crJzwvcD4nCiAgICArJzxidXR0b24g"
    "Y2xhc3M9ImJ0biBibG9jayIgb25jbGljaz0ibG9jYXRpb24ucmVsb2FkKCkiPlRyeSBhZ2Fpbjwv"
    "YnV0dG9uPjwvZGl2Pic7CiAgWyJ0YWJzIiwiZG9jayJdLmZvckVhY2goaWQ9Pntjb25zdCBlPWRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKTtpZihlKWUuc3R5bGUuZGlzcGxheT0ibm9uZSI7fSk7"
    "Cn0KYXN5bmMgZnVuY3Rpb24gYXBpVHJ5KHBhdGgsYm9keSl7CiAgdHJ5e3JldHVybiBhd2FpdCBh"
    "cGkocGF0aCxib2R5KTt9CiAgY2F0Y2goZSl7dG9hc3QoU3RyaW5nKGUmJmUubWVzc2FnZXx8ZSku"
    "c2xpY2UoMCw5MCkpO3JldHVybiBudWxsO30KfQphc3luYyBmdW5jdGlvbiByZWZyZXNoKCl7CiAg"
    "aWYoIUxJVkUpcmV0dXJuOwogIHRyeXsKICAgIGFwcGx5U3RhdGUoYXdhaXQgYXBpKCIvYXBpL3N0"
    "YXRlIikpOwogICAgYXBwbHlDTVMoKTthcHBseUZlYXR1cmVzKCk7c3luY0JhbCgpO3JlbmRlcigp"
    "O3JlbmRlckFkbWluKCk7CiAgfWNhdGNoKGUpe30KfQphc3luYyBmdW5jdGlvbiBjb3B5VGV4dCh0"
    "LCBsYWJlbCl7CiAgY29uc3QgdHh0PVN0cmluZyh0fHwiIik7CiAgaWYoIXR4dCl7dG9hc3QoIk5v"
    "dGhpbmcgdG8gY29weSIpO3JldHVybiBmYWxzZTt9CiAgdHJ5ewogICAgaWYobmF2aWdhdG9yLmNs"
    "aXBib2FyZCYmd2luZG93LmlzU2VjdXJlQ29udGV4dCl7YXdhaXQgbmF2aWdhdG9yLmNsaXBib2Fy"
    "ZC53cml0ZVRleHQodHh0KTt9CiAgICBlbHNlewogICAgICBjb25zdCB0YT1kb2N1bWVudC5jcmVh"
    "dGVFbGVtZW50KCJ0ZXh0YXJlYSIpOwogICAgICB0YS52YWx1ZT10eHQ7dGEuc2V0QXR0cmlidXRl"
    "KCJyZWFkb25seSIsIiIpOwogICAgICB0YS5zdHlsZS5jc3NUZXh0PSJwb3NpdGlvbjpmaXhlZDts"
    "ZWZ0Oi05OTk5cHg7b3BhY2l0eTowIjsKICAgICAgZG9jdW1lbnQuYm9keS5hcHBlbmRDaGlsZCh0"
    "YSk7dGEuc2VsZWN0KCk7dGEuc2V0U2VsZWN0aW9uUmFuZ2UoMCx0eHQubGVuZ3RoKTsKICAgICAg"
    "Y29uc3Qgb2s9ZG9jdW1lbnQuZXhlY0NvbW1hbmQoImNvcHkiKTtkb2N1bWVudC5ib2R5LnJlbW92"
    "ZUNoaWxkKHRhKTsKICAgICAgaWYoIW9rKXRocm93IG5ldyBFcnJvcigiY29weSByZWZ1c2VkIik7"
    "CiAgICB9CiAgICB0b2FzdCgobGFiZWx8fCJDb3BpZWQiKSsiOiAiKyh0eHQubGVuZ3RoPjI2P3R4"
    "dC5zbGljZSgwLDI2KSsi4oCmIjp0eHQpKTsKICAgIHJldHVybiB0cnVlOwogIH1jYXRjaChlKXsK"
    "ICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5Db3B5"
    "IHRoaXM8L2gzPgogICAgICA8cCBjbGFzcz0ic3ViIj5Zb3VyIGJyb3dzZXIgYmxvY2tlZCB0aGUg"
    "Y2xpcGJvYXJkIOKAlCBzZWxlY3QgYW5kIGNvcHkuPC9wPgogICAgICA8dGV4dGFyZWEgY2xhc3M9"
    "ImlucCIgcm93cz0iMyIgcmVhZG9ubHkgb25jbGljaz0idGhpcy5zZWxlY3QoKSI+JHtlc2ModHh0"
    "KX08L3RleHRhcmVhPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEwcHgiIGRhdGEtY2xvc2U+RG9uZTwvYnV0dG9uPmApOwogICAgcmV0dXJuIGZhbHNl"
    "OwogIH0KfQpsZXQgU1RPUkU9e3VwaToiIixwYXllZToiIixwcmVmaXg6IlBBWSIsdGVybXM6IiIs"
    "c3VwcG9ydDoiIn07CmxldCBQUkVGUz17cmVzdG9jazp0cnVlLHJlY2VpcHRzOnRydWUscHJvbW9z"
    "OnRydWUscHVzaDp0cnVlfTsKbGV0IE9GRkVSPSIiOwpmdW5jdGlvbiBvcGVuUGF5KGxpbmspewog"
    "IGlmKCFsaW5rKXJldHVybjsKICB0cnl7CiAgICBpZihURyYmVEcub3BlbkxpbmsmJi9eaHR0cHM/"
    "Oi8udGVzdChsaW5rKSlURy5vcGVuTGluayhsaW5rKTsKICAgIGVsc2Ugd2luZG93LmxvY2F0aW9u"
    "LmhyZWY9bGluazsKICB9Y2F0Y2goZSl7d2luZG93LmxvY2F0aW9uLmhyZWY9bGluazt9Cn0KYXN5"
    "bmMgZnVuY3Rpb24gYm9vdCgpewogIGlmKFRHKXt0cnl7VEcucmVhZHkoKTtURy5leHBhbmQoKTt9"
    "Y2F0Y2goZSl7fX0KICBjb25zdCBpblRHPSEhKChJTklUJiZJTklULmxlbmd0aCl8fChBUFBLRVkm"
    "JkFQUEtFWS5sZW5ndGgpKTsKICBpZihpblRHKXNob3dTd2l0Y2goZmFsc2UpOyAgICAgICAgICAg"
    "ICAgLyogZmFpbCBjbG9zZWQ6IG5vIGFkbWluIHN0cmlwIHVudGlsIHByb3ZlbiAqLwogIGlmKGxv"
    "Y2F0aW9uLnByb3RvY29sPT09ImZpbGU6IilyZXR1cm47ICAvKiBvcGVuZWQgYXMgYSBwbGFpbiBm"
    "aWxlIOKAlCBkZW1vIGdhbGxlcnkgKi8KICBsZXQgZDsKICB0cnl7CiAgICBkPWF3YWl0IGFwaSgi"
    "L2FwaS9zdGF0ZSIpOwogIH1jYXRjaChlKXsKICAgIGlmKGUmJmUuc3RhdHVzPT09NDAxKXsKICAg"
    "ICAgbGV0IHdoeT0iIixmaXg9IiI7CiAgICAgIHRyeXsKICAgICAgICBjb25zdCB3PWF3YWl0IChh"
    "d2FpdCBmZXRjaCgiL2FwaS93aG9hbWkiLHtoZWFkZXJzOnsiWC1Jbml0LURhdGEiOklOSVQsCiAg"
    "ICAgICAgICAiWC1UZWxlZ3JhbS1Jbml0LURhdGEiOklOSVQsIlgtQXBwLUtleSI6QVBQS0VZfX0p"
    "KS5qc29uKCk7CiAgICAgICAgY29uc3QgUj17bWlzc2luZzpbIlRlbGVncmFtIGRpZCBub3QgcGFz"
    "cyB5b3VyIGFjY291bnQgdG8gdGhpcyBwYWdlLiIsCiAgICAgICAgICAgICAgICAgICAiT3BlbiB0"
    "aGUgYm90IGFuZCB0YXAgPGI+T3BlbiBTdG9yZTwvYj4sIG9yIHNlbmQgPGI+L2FwcDwvYj4uIl0s"
    "CiAgICAgICAgICAgICAgICAgImJhZC1mb3JtYXQiOlsiVGhlIHNpZ24taW4gZGF0YSBhcnJpdmVk"
    "IGRhbWFnZWQuIiwiQ2xvc2UgdGhlIGFwcCBhbmQgb3BlbiBpdCBhZ2FpbiBmcm9tIHRoZSBib3Qu"
    "Il0sCiAgICAgICAgICAgICAgICAgIm5vLWhhc2giOlsiVGhlIHNpZ24taW4gZGF0YSBhcnJpdmVk"
    "IGluY29tcGxldGUuIiwiQ2xvc2UgdGhlIGFwcCBhbmQgb3BlbiBpdCBhZ2FpbiBmcm9tIHRoZSBi"
    "b3QuIl0sCiAgICAgICAgICAgICAgICAgImJhZC1oYXNoIjpbIlRoZSBzaWduYXR1cmUgZG9lcyBu"
    "b3QgbWF0Y2ggdGhpcyBib3QncyB0b2tlbi4iLAogICAgICAgICAgICAgICAgICAgIlRoZSBwYWdl"
    "IGlzIHNlcnZlZCBieSBhIGRpZmZlcmVudCBib3QsIG9yIEJPVF9UT0tFTiBvbiB0aGUgc2VydmVy"
    "IGhhcyBxdW90ZXMgYXJvdW5kIGl0LiBTZW5kIDxiPi9kaWFnPC9iPiBpbiB0aGUgYm90IHRvIGNv"
    "bXBhcmUuIl0sCiAgICAgICAgICAgICAgICAgc3RhbGU6WyJUaGF0IHNpZ24taW4gaXMgb2xkZXIg"
    "dGhhbiB0aGUgYWxsb3dlZCB3aW5kb3cuIiwiUmVvcGVuIHRoZSBhcHAgZnJvbSB0aGUgYm90LiJd"
    "LAogICAgICAgICAgICAgICAgICJiYWQtdXNlciI6WyJZb3VyIGFjY291bnQgZGV0YWlscyBjb3Vs"
    "ZCBub3QgYmUgcmVhZC4iLCJSZW9wZW4gdGhlIGFwcCBmcm9tIHRoZSBib3QuIl0sCiAgICAgICAg"
    "ICAgICAgICAgIm5vLWlkIjpbIlRlbGVncmFtIHNlbnQgbm8gdXNlciBpZC4iLCJSZW9wZW4gdGhl"
    "IGFwcCBmcm9tIHRoZSBib3QuIl19OwogICAgICAgIGNvbnN0IHI9Ult3LndoeV18fFsiVGhlIHN0"
    "b3JlIGNvdWxkIG5vdCBjb25maXJtIHdobyB5b3UgYXJlLiIsIk9wZW4gaXQgYWdhaW4gZnJvbSB0"
    "aGUgYm90LiJdOwogICAgICAgIHdoeT1yWzBdKyIgPGNvZGU+KCIrKHcud2h5fHwidW5rbm93biIp"
    "KyIpPC9jb2RlPiI7CiAgICAgICAgZml4PXJbMV07CiAgICAgIH1jYXRjaChlcnIpewogICAgICAg"
    "IHdoeT0iVGhlIHN0b3JlIGNvdWxkIG5vdCBjb25maXJtIHdobyB5b3UgYXJlLiI7CiAgICAgICAg"
    "Zml4PSJPcGVuIGl0IGFnYWluIGZyb20gdGhlIGJvdCB3aXRoIHRoZSA8Yj5PcGVuIFN0b3JlPC9i"
    "PiBidXR0b24uIjsKICAgICAgfQogICAgICBsb2NrZWRTY3JlZW4oIkNhbm5vdCBvcGVuIHRoZSBz"
    "dG9yZSIsd2h5KyI8YnI+PGJyPiIrZml4KTsKICAgIH0KICAgIHJldHVybjsKICB9CiAgYXBwbHlT"
    "dGF0ZShkKTtMSVZFPXRydWU7CiAgc2hvd1N3aXRjaCghIShNRSYmTUUuYWRtaW4pKTsgICAgICAg"
    "ICAgIC8qIG9ubHkgYSB2ZXJpZmllZCBhZG1pbiBzZWVzIGl0ICovCiAgYXBwbHlDTVMoKTthcHBs"
    "eUZlYXR1cmVzKCk7c3luY0JhbCgpO3JlbmRlcigpO3JlbmRlckFkbWluKCk7Cn0KYXN5bmMgZnVu"
    "Y3Rpb24gcHVzaChwYXRoLGJvZHksb2tNc2cpewogIGlmKCFMSVZFKXJldHVybiB0cnVlOwogIHRy"
    "eXtjb25zdCByPWF3YWl0IGFwaShwYXRoLGJvZHkpO2lmKG9rTXNnKXRvYXN0KG9rTXNnKTtyZXR1"
    "cm4gcjt9CiAgY2F0Y2goZSl7dG9hc3QoIkNvdWxkIG5vdCBzYXZlIOKAlCBjaGVjayB5b3VyIGNv"
    "bm5lY3Rpb24iKTtyZXR1cm4gZmFsc2U7fQp9CmJvb3QoKTsKPC9zY3JpcHQ+CjwvYm9keT48L2h0"
    "bWw+Cg=="
)


def valid_init_data(init: str) -> int | None:


    if not init or not BOT_TOKEN:
        return None
    for candidate in _init_candidates(init):
        uid = _check_init(candidate)
        if uid:
            return uid
    return None


def _init_candidates(init: str) -> list:

    out = [init]
    if "%" in init:
        with contextlib.suppress(Exception):
            once = urllib.parse.unquote(init)
            if once != init:
                out.append(once)
    return out


def _check_init(init: str) -> int | None:
    for decoder in (urllib.parse.unquote, urllib.parse.unquote_plus):
        uid = _check_init_with(init, decoder)
        if uid:
            return uid
    return None


def _check_init_with(init: str, decode) -> int | None:
    try:
        data = {}
        for chunk in init.split("&"):
            key, _, val = chunk.partition("=")
            if key:
                data[key] = decode(val)
        given = data.pop("hash", "")
        data.pop("signature", None)
        if not given:
            log.warning("initData rejected: no hash")
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, given):
            log.warning("initData rejected: hash mismatch (fields: %s)",
                        ",".join(sorted(data)))
            return None
        max_age = int(sgetf("initdata_max_age", 86400))
        if max_age > 0:
            age = time.time() - float(data.get("auth_date", 0) or 0)
            if age > max_age:
                log.warning("initData rejected: %.0f s old", age)
                return None
        user = json.loads(data.get("user", "{}"))
        uid = int(user.get("id") or 0)
        if not uid:
            log.warning("initData rejected: no user id")
            return None
        with contextlib.suppress(Exception):
            get_user(uid, user.get("username") or "", user.get("first_name") or "")
        return uid
    except Exception as e:
        log.warning("initData rejected: %s", e)
        return None


PREVIEW_USER = int(os.getenv("PREVIEW_USER_ID", "0") or 0)


def app_key(uid: int) -> str:

    sig = hmac.new(BOT_TOKEN.encode(), f"appkey:{uid}".encode(),
                   hashlib.sha256).hexdigest()[:24]
    return f"{uid}.{sig}"


def key_user(key: str) -> int | None:
    try:
        uid_part, sig = (key or "").split(".", 1)
        uid = int(uid_part)
    except (ValueError, AttributeError):
        return None
    expected = app_key(uid).split(".", 1)[1]
    return uid if hmac.compare_digest(sig, expected) else None


def app_url_for(uid: int) -> str:

    base = public_url()
    if not base:
        return ""
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}k={app_key(uid)}"


def init_header(request: web.Request) -> str:
    return (request.headers.get("X-Init-Data")
            or request.headers.get("X-Telegram-Init-Data")
            or "")


def req_user(request: web.Request) -> int | None:
    uid = valid_init_data(init_header(request))
    if uid:
        return uid
    key = request.headers.get("X-App-Key") or request.query.get("k", "")
    if key:
        uid = key_user(key)
        if uid:
            get_user(uid)
            return uid
        log.warning("app key rejected")
    if PREVIEW_USER:
        get_user(PREVIEW_USER, "preview", "Preview")
        return PREVIEW_USER
    return None


def json_error(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": msg}, status=status)


async def h_index(request: web.Request) -> web.Response:
    return web.Response(text=miniapp_html(), content_type="text/html")


def content_live() -> dict:


    c = dict(content_all())
    c["storeName"] = sget("store_name")
    c["storeTag"] = sget("store_tagline")
    return c


def admin_state(uid: int) -> dict:

    today = datetime.now(timezone.utc).date()
    td = today.isoformat()
    rev = float(q1("SELECT COALESCE(SUM(price),0) s FROM orders WHERE substr(created_at,1,10)=?",
                   (td,))["s"])
    orders_today = q1("SELECT COUNT(*) c FROM orders WHERE substr(created_at,1,10)=?", (td,))["c"]
    users_n = q1("SELECT COUNT(*) c FROM users")["c"]
    auto = q1("SELECT COUNT(*) c FROM deposits WHERE status='completed' AND utr NOT IN ('','manual')")["c"]

    series = []
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        series.append(float(q1(
            "SELECT COALESCE(SUM(price),0) s FROM orders WHERE substr(created_at,1,10)=?",
            (d,))["s"]))
    peak = max(series) or 1
    bars = [max(3, int(v / peak * 100)) for v in series]

    stock_rows = []
    for c in countries():
        stock_rows.append({"c": c["c"], "f": c["f"], "qty": c["qty"], "price": c["price"],
                           "low": int(sgetf("low_stock_default", 10))})

    orders_rows = []
    for o in q("SELECT * FROM orders ORDER BY created_at DESC LIMIT 8"):
        orders_rows.append({
            "id": o["id"], "f": flag_for(o["country"]), "c": o["country"],
            "user": str(o["user_id"]), "price": float(o["price"]),
            "when": (o["created_at"] or "")[:16].replace("T", " "),
            "st": (o["status"] or "").title(),
        })

    disputes = [{"id": f"D-{d['id']}", "user": str(d["user_id"]), "reason": d["reason"],
                 "status": (d["status"] or "").title()}
                for d in q("SELECT * FROM disputes ORDER BY id DESC LIMIT 6")]

    users_rows = []
    for r in q("SELECT * FROM users ORDER BY total_spent DESC LIMIT 10"):
        spent = float(r["total_spent"])
        tier = "Gold" if spent >= 500 else "Silver" if spent >= 100 else "Bronze"
        users_rows.append({
            "id": str(r["user_id"]), "name": "@" + (r["username"] or str(r["user_id"])),
            "spent": spent, "orders": r["total_orders"], "balance": float(r["balance"]),
            "banned": bool(r["banned"]), "tier": tier,
        })

    admins_rows = []
    for a in q("SELECT * FROM admins ORDER BY role DESC"):
        pm = perms_of(a["user_id"])
        granted = [k for k, v in pm.items() if v]
        admins_rows.append({
            "id": str(a["user_id"]), "label": a["label"] or str(a["user_id"]),
            "role": (a["role"] or "").title(),
            "perms": "everything" if a["role"] == "owner" else (", ".join(granted[:3]) or "nothing yet"),
        })

    channels = [{"id": c["id"], "name": c["name"], "handle": c["handle"],
                 "required": bool(c["required"]), "active": bool(c["active"])}
                for c in q("SELECT * FROM channels ORDER BY id")]

    logs = {
        "master": sget("logs_master", "1") == "1",
        "channels": [{"id": c["id"], "name": c["name"], "primary": bool(c["is_primary"]),
                      "active": bool(c["active"])}
                     for c in q("SELECT * FROM logchannels ORDER BY id")],
        "events": [{"key": k, "label": lbl,
                    "on": bool((q1("SELECT on_ FROM logevents WHERE key=?", (k,)) or {"on_": 1})["on_"])}
                   for k, lbl in LOG_EVENTS],
    }

    coupons = [{"code": c["code"], "percent": float(c["percent"]), "used": c["used"],
                "max": c["max_uses"], "active": bool(c["active"])}
               for c in q("SELECT * FROM coupons ORDER BY rowid DESC LIMIT 6")]

    audit_rows = [{"who": str(a["actor"]), "what": a["action"], "detail": a["detail"],
                   "when": (a["ts"] or "")[11:16]}
                  for a in q("SELECT * FROM audit ORDER BY id DESC LIMIT 8")]

    reviews = [{"user": "@" + str((q1("SELECT username FROM users WHERE user_id=?",
                                       (r["user_id"],)) or {"username": ""})["username"]
                                  or r["user_id"]),
                "c": f"{flag_for(r['country'])} {r['country']}",
                "stars": f"{int(r['rating'])}\u2605", "text": r["text"] or ""}
               for r in q("SELECT * FROM reviews WHERE hidden=0 ORDER BY id DESC LIMIT 5")]

    deposits = [{"id": d["id"], "amt": float(d["amount"]), "user": str(d["user_id"]),
                 "utr": (d["utr"] or "no match")[:12],
                 "st": (d["status"] or "").title()}
                for d in q("SELECT * FROM deposits ORDER BY id DESC LIMIT 6")]

    per_country = [[r["country"], r["n"], float(r["s"])] for r in q(
        "SELECT country, COUNT(*) n, COALESCE(SUM(price),0) s FROM orders "
        "GROUP BY country ORDER BY s DESC LIMIT 6")]

    dep_users = q1("SELECT COUNT(DISTINCT user_id) c FROM deposits WHERE status='completed'")["c"]
    buy_users = q1("SELECT COUNT(DISTINCT user_id) c FROM orders")["c"]

    return {
        "kpi": {"rev": rev, "orders": orders_today, "users": users_n, "auto": auto},
        "bars": bars, "series": series,
        "stock": stock_rows, "orders": orders_rows, "disputes": disputes,
        "users": users_rows, "admins": admins_rows, "channels": channels, "logs": logs,
        "coupons": coupons, "audit": audit_rows,
        "reviews": reviews, "deposits": deposits,
        "logfeed": [
            {"what": f"{a['action']}: {a['detail']}"[:48],
             "where": (q1("SELECT name FROM logchannels WHERE is_primary=1")
                       or {"name": "no channel"})["name"],
             "when": (a["ts"] or "")[11:16]}
            for a in q("SELECT * FROM audit ORDER BY id DESC LIMIT 5")
        ],
        "pay": {
            "upi": sget("upi_id"), "payee": sget("upi_name"), "gmail": sget("gmail_id"),
            "gmail_set": bool(sget("gmail_pass")), "window": int(sgetf("pay_window", 5)),
            "prefix": sget("pay_prefix"), "extra": sget("upi_extra"),
            "min": sgetf("min_deposit", 10),
            "max": sgetf("max_deposit", 20000), "autodep": sgetf("autodep_max", 0),
            "pending": q1("SELECT COUNT(*) c FROM deposits WHERE status='review'")["c"],
        },
        "security": {
            "rate": int(sgetf("rate_limit", 20)), "velocity": int(sgetf("velocity_limit", 5)),
            "blacklist": q1("SELECT COUNT(*) c FROM blacklist")["c"],
            "banned": q1("SELECT COUNT(*) c FROM users WHERE banned=1")["c"],
        },
        "sw": {
            "gmail_enabled": sget("gmail_enabled", "1") == "1",
            "autodep": sgetf("autodep_max", 0) > 0,
            "utr_fallback": sget("utr_fallback", "1") == "1",
            "verify_on_tap": sget("verify_on_tap", "1") == "1",
            "night_discount": sget("night_discount", "0") == "1",
            "force_sub": sget("force_sub", "1") == "1",
            "maintenance": sget("maintenance") == "1",
            "sandbox": sget("sandbox") == "1",
            "unique_paise": sget("unique_paise", "0") == "1",
            "verify_need_note": sget("verify_need_note", "1") == "1",
            "ban_chargeback": sget("ban_chargeback", "0") == "1",
            "ban_fake_utr": sget("ban_fake_utr", "0") == "1",
            "ban_failed_verify": sget("ban_failed_verify", "0") == "1",
            "digest_to_admins": sget("digest_to_admins", "1") == "1",
            "logs_master": sget("logs_master", "1") == "1",
        },
        "blacklist": [
            {"id": r["rowid"], "kind": r["kind"], "value": r["value"],
             "when": (r["added_at"] or "")[:10]}
            for r in q("SELECT rowid, * FROM blacklist ORDER BY rowid DESC LIMIT 20")
        ],
        "flagged": [
            {"id": r["id"], "user": str(r["user_id"]), "c": r["country"],
             "stars": int(r["rating"]), "text": (r["text"] or "")[:60],
             "hidden": bool(r["hidden"])}
            for r in q("SELECT * FROM reviews WHERE rating <= 2 OR hidden=1 "
                       "ORDER BY id DESC LIMIT 10")
        ],
        "pricing": {
            "bulk_5": sgetf("bulk_5", 5), "bulk_10": sgetf("bulk_10", 10),
            "bulk_25": sgetf("bulk_25", 15), "default_price": sgetf("default_price", 35),
        },
        "market": {
            "flash": sgetf("flash_percent", 20), "flash_on": flash_active(),
            "ref1": sgetf("ref_l1", 5), "ref2": sgetf("ref_l2", 1),
            "goal": sgetf("rev_goal", 0),
            "month": float(q1("SELECT COALESCE(SUM(price),0) s FROM orders "
                              "WHERE substr(created_at,1,7)=?",
                              (datetime.now(timezone.utc).strftime("%Y-%m"),))["s"]),
        },
        "settings": {
            "store": sget("store_name"), "tagline": sget("store_tagline"),
            "support": sget("support"), "price": sgetf("default_price", 35),
            "daily": sgetf("daily_reward", 0.4), "app": public_url(),
            "maintenance": sget("maintenance") == "1", "sandbox": sget("sandbox") == "1",
        },
        "analytics": {
            "funnel": {"users": users_n, "dep": dep_users, "buy": buy_users},
            "country": per_country,
            "stock_total": q1("SELECT COUNT(*) c FROM stock WHERE sold=0")["c"],
            "sold_total": q1("SELECT COUNT(*) c FROM stock WHERE sold=1")["c"],
        },
    }


async def h_state(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    u = get_user(uid)
    stock = [
        {
            "c": c["c"], "f": c["f"], "code": c["code"], "price": c["price"],
            "was": c["was"], "qty": c["qty"], "hot": c["hot"],
        }
        for c in countries()
    ]
    orders = []
    for o in q("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 12", (uid,)):
        st_row = q1("SELECT payload FROM stock WHERE id=?", (o["stock_id"],))
        info = parse_session((st_row["payload"] if st_row else "") or "")
        orders.append({
            "id": o["id"], "c": o["country"], "f": flag_for(o["country"]),
            "num": o["id"], "st": (o["status"] or "").title(),
            "when": (o["created_at"] or "")[:16].replace("T", " "), "price": o["price"],
            "phone": info["phone"], "twofa": info["twofa"],
            "otp": o["otp"] or "",
            "otp_left": otp_quota_left(o["id"]),
            "window": otp_window_left(o["id"]),
            "login": (q1("SELECT state FROM logins WHERE order_id=?", (o["id"],))
                      or {"state": ""})["state"],
            "claim": (q1("SELECT status FROM claims WHERE order_id=?", (o["id"],))
                      or {"status": ""})["status"],
        })
    tx = []
    for d in q("SELECT * FROM deposits WHERE user_id=? AND status='completed' ORDER BY id DESC LIMIT 8", (uid,)):
        tx.append({"t": "Deposit", "a": f"+{CUR}{d['amount']:.2f}", "w": (d["done_at"] or "")[:16].replace("T", " ")})
    for o in q("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 8", (uid,)):
        tx.append({"t": f"Purchase · {o['country']}", "a": f"-{CUR}{o['price']:.2f}",
                   "w": (o["created_at"] or "")[:16].replace("T", " ")})
    cart = []
    for r in q("SELECT * FROM cart WHERE user_id=?", (uid,)):
        for _ in range(r["qty"]):
            cart.append({"c": r["country"], "f": flag_for(r["country"]), "code": "",
                         "price": price_of(r["country"])})
    feed = []
    for o in q("SELECT * FROM orders ORDER BY created_at DESC LIMIT 5"):
        feed.append([flag_for(o["country"]), o["country"], f"{CUR}{o['price']:.0f}",
                     (o["created_at"] or "")[11:16]])
    ratings = {}
    for c in countries():
        avg, n = country_rating(c["c"])
        if n:
            ratings[c["c"]] = [avg, n]
    today = datetime.now(timezone.utc).date().isoformat()
    inv = q1("SELECT COUNT(*) c FROM users WHERE referred_by=?", (uid,))["c"]
    l1 = q1("SELECT COALESCE(SUM(earned),0) s FROM referrals WHERE inviter=? AND level=1",
            (uid,))["s"]
    l2 = q1("SELECT COALESCE(SUM(earned),0) s FROM referrals WHERE inviter=? AND level=2",
            (uid,))["s"]
    recent = []
    for r in q("SELECT * FROM referrals WHERE inviter=? ORDER BY id DESC LIMIT 5", (uid,)):
        recent.append([f"user_{r['invitee']}", "deposited",
                       f"+{CUR}{float(r['earned']):.2f}"])
    leaders = []
    for i, r in enumerate(leaderboard(5), 1):
        leaders.append([str(i), "@" + str(r["username"] or r["user_id"]),
                        f"{CUR}{float(r['total_spent']):,.0f}"])
    per = sgetf("points_per_rupee", 100)
    bot_name = sget("bot_username") or "your_bot"
    earn = {
        "streak": u["streak"], "daily": sgetf("daily_reward", 0.40),
        "claimed": u["last_checkin"] == today,
        "points": u["points"], "pointsWorth": round(u["points"] / max(1, per) * 10, 2),
        "invites": inv, "l1": round(float(l1), 2), "l2": round(float(l2), 2),
        "earned": round(float(l1) + float(l2), 2),
        "link": f"t.me/{bot_name}?start=ref_{uid}",
        "recent": recent, "leaders": leaders,
        "tasks": [
            ["Join the main channel", f"{CUR}1.00",
             1 if q1("SELECT 1 FROM channels WHERE active=1") else 0],
            ["Place your first order", f"{CUR}2.00", 1 if u["total_orders"] else 0],
            ["Invite 3 friends", f"{CUR}5.00", 1 if inv >= 3 else 0],
        ],
    }
    wish = [r["country"] for r in q("SELECT country FROM wishlist WHERE user_id=?", (uid,))]
    wish_idx = [i for i, s in enumerate(stock) if s["c"] in wish]
    adm = admin_state(uid) if is_admin(uid) else None
    return web.json_response(
        {
            "ok": True,
            "adm": adm,
            "features": feat_all(),
            "content": content_live(),
            "stock": stock,
            "orders": orders,
            "tx": tx,
            "feed": feed,
            "earn": earn,
            "ratings": ratings,
            "cart": cart,
            "wishlist": wish_idx,
            "balance": round(float(u["balance"]), 2),
            "points": u["points"],
            "me": {
                "id": uid,
                "admin": is_admin(uid),
                "owner": is_owner(uid),
                "perms": perms_of(uid),
                "name": u["first_name"] or "",
                "username": ("@" + u["username"]) if u["username"] else "",
                "photo": f"/api/avatar?u={uid}&t={qr_token(uid)}",
                "joined": (u["joined_at"] or "")[:10],
                "orders": u["total_orders"],
                "spent": round(float(u["total_spent"]), 2),
                "balance": round(float(u["balance"]), 2),
                "points": u["points"],
                "streak": u["streak"],
                "banned": bool(u["banned"]),
                "tier": ("Platinum" if u["total_spent"] >= 2000 else
                         "Gold" if u["total_spent"] >= 500 else
                         "Silver" if u["total_spent"] >= 100 else "Bronze"),
                "next_tier": (0 if u["total_spent"] >= 2000 else
                              round((500 if u["total_spent"] < 500 else 2000)
                                    - float(u["total_spent"]), 2)),
                "debt": get_debt(uid) if feat("udhar") else 0,
            },
            "store": {"name": sget("store_name"), "tag": sget("store_tagline"),
                      "upi": sget("upi_id"), "payee": sget("upi_name"),
                      "prefix": sget("pay_prefix"),
                      "window": int(sgetf("pay_window", 5)),
                      "min": sgetf("min_deposit", 10), "max": sgetf("max_deposit", 20000),
                      "terms": cget("terms"), "support": sget("support"),
                      "autocheck": auto_check_on()},
            "prefs": prefs_of(uid),
            "alerts": [r["country"].title() for r in
                       q("SELECT country FROM stock_alerts WHERE user_id=?", (uid,))],
        }
    )


async def h_buy(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    if sget("maintenance") == "1":
        return json_error("maintenance")
    body = await request.json()
    country = str(body.get("country", ""))
    qty = max(1, min(25, int(body.get("qty", 1))))
    code = str(body.get("coupon", ""))
    avail = q1("SELECT COUNT(*) c FROM stock WHERE country=? AND sold=0", (country,))["c"]
    if not avail:
        return json_error("out of stock")
    if avail < qty:
        return json_error(f"only {avail} left")
    qt = quote(country, qty, code)
    if balance(uid) < qt["total"]:
        return json_error("insufficient balance")
    credit(uid, -qt["total"])
    out = []
    for _ in range(qty):
        oid, row = deliver(uid, country, round(qt["total"] / qty, 2))
        if oid:
            out.append({"id": oid, "payload": row["payload"]})
    pay_referral(uid, qt["total"])
    await send_log("purchase", f"🛒 <b>Mini app purchase</b> {country} × {qty} · {money(qt['total'])}")
    if feat("pushNotif"):
        await notify_user(uid, f"✅ Purchased {qty} × {country} — details are in Orders.")
    return web.json_response({"ok": True, "orders": out, "balance": balance(uid)})


def qr_token(dep_id: int) -> str:
    return hmac.new(BOT_TOKEN.encode(), f"qr:{dep_id}".encode(), hashlib.sha256).hexdigest()[:20]


async def h_dep_qr(request: web.Request) -> web.Response:

    dep_id = int(request.query.get("id", 0) or 0)
    token = request.query.get("t", "")
    dep = q1("SELECT * FROM deposits WHERE id=?", (dep_id,))
    if not dep:
        return json_error("not found", 404)
    if not (token and hmac.compare_digest(token, qr_token(dep_id))):
        uid = req_user(request)
        if not uid or uid != dep["user_id"]:
            return json_error("unauthorised", 401)
    buf = generate_qr(upi_link(dep["amount"], dep["code"]))
    if not buf:
        return json_error("qr unavailable", 503)
    return web.Response(body=buf.read(), content_type="image/png")


def qr_data_uri(dep) -> str:

    link = upi_link(dep["amount"], dep["code"])
    if not link:
        return ""
    try:
        return "data:image/png;base64," + base64.b64encode(
            qr_png(link, scale=6, border=3)).decode("ascii")
    except Exception as e:
        log.warning("inline qr failed: %s", e)
        return ""


async def h_dep_create(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    body = await request.json()
    amount = float(body.get("amount", 0))
    lo, hi = sgetf("min_deposit", 10), sgetf("max_deposit", 20000)
    if feat("depLimits") and not (lo <= amount <= hi):
        return json_error(f"allowed {lo:.0f}–{hi:.0f}")
    if not sget("upi_id"):
        return json_error("payments not configured")
    dep = create_deposit(uid, amount)
    return web.json_response(
        {
            "ok": True, "id": dep["id"], "code": dep["code"], "amount": dep["amount"],
            "upi": sget("upi_id"), "payee": sget("upi_name") or sget("store_name"),
            "link": upi_link(dep["amount"], dep["code"]),
            "qr": qr_data_uri(dep) if feat("payQR") else "",
            "qr_url": (f"/api/deposit/qr?id={dep['id']}&t={qr_token(dep['id'])}"
                       if feat("payQR") else ""),
            "expires_at": dep["expires_at"],
            "window": int(sgetf("pay_window", 5)),
            "bonus": deposit_bonus(dep["amount"]),
        }
    )


async def h_dep_verify(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    body = await request.json()
    dep = q1("SELECT * FROM deposits WHERE id=? AND user_id=?", (int(body.get("id", 0)), uid))
    if not dep:
        return json_error("not found", 404)
    if dep["status"] == "completed":
        return web.json_response({"ok": True, "status": "completed", "balance": balance(uid)})
    if deposit_expired(dep):
        ex("UPDATE deposits SET status='expired' WHERE id=?", (dep["id"],))
        return web.json_response({"ok": False, "status": "expired"})
    detail = await asyncio.to_thread(check_gmail_detailed, float(dep["amount"]),
                                     dep["code"], dep["created_at"] or "")
    utr = detail["utr"] if detail["found"] else None
    if not utr:
        reason_l = (detail.get("reason") or "").lower()
        cannot_check = (not sget("gmail_id")
                        or any(w in reason_l for w in ("switched off", "password",
                                                       "login", "not saved",
                                                       "could not reach")))
        if cannot_check:
            ex("UPDATE deposits SET status='review' WHERE id=?", (dep["id"],))
            await notify_admins(
                f"💳 <b>Deposit needs review</b>\n{money(dep['amount'])} · user <code>{uid}</code>", "payments")
            return web.json_response({"ok": False, "status": "review"})
        reason = (detail.get("reason") or "").lower()
        if any(w in reason for w in ("password", "login", "switched off",
                                     "not saved", "could not reach")):
            await notify_admins(
                f"⚠️ <b>Payment checking is not working</b>\n<i>{esc(detail['reason'])}</i>",
                "payments")
        return web.json_response({"ok": False, "status": "notfound",
                                  "reason": cget("msgNone")})
    total = settle_deposit(dep, utr)
    await send_log("deposit", f"✅ <b>Deposit verified</b> {money(dep['amount'])} · user <code>{uid}</code>")
    if feat("pushNotif"):
        await notify_user(uid, f"✅ {money(total)} credited to your wallet.")
    return web.json_response({"ok": True, "status": "completed", "credited": total, "balance": balance(uid)})


async def h_dep_claim(request: web.Request) -> web.Response:

    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    body = await request.json()
    dep = q1("SELECT * FROM deposits WHERE id=? AND user_id=?",
             (int(body.get("id", 0)), uid))
    if not dep:
        return json_error("not found", 404)
    if dep["status"] == "completed":
        return json_error("already credited")
    utr = re.sub(r"\D", "", str(body.get("utr", "")))[:32]
    if len(utr) < 6:
        return json_error("enter the 10-12 digit UTR from your payment app")
    shot = str(body.get("shot", ""))
    ex("UPDATE deposits SET utr=?, status='review' WHERE id=?", (utr, dep["id"]))
    dep = q1("SELECT * FROM deposits WHERE id=?", (dep["id"],))
    text = payment_review(dep, uid, utr)
    photo = None
    if shot.startswith("data:image"):
        with contextlib.suppress(Exception):
            raw = base64.b64decode(shot.split(",", 1)[1])
            if len(raw) <= 8 * 1024 * 1024:
                photo = raw
    for r in q("SELECT user_id FROM admins"):
        if not can(r["user_id"], "payments"):
            continue
        sent = False
        if photo:
            with contextlib.suppress(Exception):
                bio = io.BytesIO(photo)
                bio.name = f"proof-{dep['id']}.jpg"
                await _APP.bot.send_photo(chat_id=r["user_id"], photo=InputFile(bio),
                                          caption=text[:1024], parse_mode=ParseMode.HTML)
                sent = True
        if not sent:
            with contextlib.suppress(Exception):
                await _APP.bot.send_message(chat_id=r["user_id"], text=text,
                                            parse_mode=ParseMode.HTML)
    return web.json_response({"ok": True, "status": "review"})


async def h_dep_utr(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    body = await request.json()
    dep = q1("SELECT * FROM deposits WHERE id=? AND user_id=?",
             (int(body.get("id", 0)), uid))
    if not dep:
        return json_error("not found", 404)
    utr = re.sub(r"\D", "", str(body.get("utr", "")))[:32]
    if len(utr) < 6:
        return json_error("that does not look like a UTR")
    ex("UPDATE deposits SET utr=?, status='review' WHERE id=?", (utr, dep["id"]))
    await notify_admins(utr_report(dep, uid, utr), "payments")
    return web.json_response({"ok": True, "status": "review"})


async def h_checkin(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    if not feat("streak"):
        return json_error("not available")
    ok, amount, streak = claim_checkin(uid)
    if not ok:
        return json_error("already claimed today")
    return web.json_response(
        {"ok": True, "amount": amount, "streak": streak, "balance": balance(uid)})


async def h_cart_checkout(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    rows = q("SELECT * FROM cart WHERE user_id=?", (uid,))
    if not rows:
        return json_error("cart is empty")
    total = sum(quote(r["country"], r["qty"])["total"] for r in rows)
    if balance(uid) < total:
        return json_error("insufficient balance")
    for r in rows:
        have = q1("SELECT COUNT(*) c FROM stock WHERE country=? AND sold=0",
                  (r["country"],))["c"]
        if have < r["qty"]:
            return json_error(f"only {have} left of {r['country']}")
    credit(uid, -total)
    out = []
    for r in rows:
        for _ in range(r["qty"]):
            oid, st = deliver(uid, r["country"], quote(r["country"], 1)["total"])
            if oid:
                out.append({"id": oid, "payload": st["payload"]})
    ex("DELETE FROM cart WHERE user_id=?", (uid,))
    pay_referral(uid, total)
    await send_log("purchase", f"🧺 <b>Cart checkout</b> {money(total)} · user <code>{uid}</code>")
    return web.json_response({"ok": True, "orders": out, "balance": balance(uid)})


async def h_dispute(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    if not feat("dispute"):
        return json_error("not available")
    body = await request.json()
    order_id = str(body.get("order", ""))[:32]
    reason = str(body.get("reason", ""))[:80] or "Not stated"
    details = str(body.get("details", ""))[:500]
    evidence = str(body.get("evidence", ""))[:200]
    if order_id and not q1("SELECT 1 FROM orders WHERE id=? AND user_id=?", (order_id, uid)):
        return json_error("that order is not yours")
    shot = str(body.get("shot", ""))
    did = ex(
        "INSERT INTO disputes(order_id,user_id,reason,details,status,created_at)"
        " VALUES(?,?,?,?,'open',?)",
        (order_id, uid, reason, (details + ("\n" + evidence if evidence else "")), now()),
    )
    u = get_user(uid)
    caption = premiumize("\n".join([
        head(f"Dispute #{did}", reason),
        "",
        f"👤 <b>Name:</b> {esc(u['first_name'] or '—')}",
        f"🔗 <b>Username:</b> @{esc(u['username'] or '—')}",
        f"🆔 <b>User id:</b> <code>{uid}</code>",
        f"🧾 <b>Order:</b> <code>{esc(order_id or '—')}</code>",
        "",
        esc(details)[:400],
    ]))
    photo = None
    if shot.startswith("data:image"):
        with contextlib.suppress(Exception):
            raw = base64.b64decode(shot.split(",", 1)[1])
            if len(raw) <= 8 * 1024 * 1024:
                photo = raw
    for r in q("SELECT user_id FROM admins"):
        if not can(r["user_id"], "orders"):
            continue
        sent_photo = False
        if photo:
            with contextlib.suppress(Exception):
                bio = io.BytesIO(photo)
                bio.name = f"dispute-{did}.jpg"
                await _APP.bot.send_photo(chat_id=r["user_id"], photo=InputFile(bio),
                                          caption=caption[:1024], parse_mode=ParseMode.HTML)
                sent_photo = True
        if not sent_photo:
            with contextlib.suppress(Exception):
                await _APP.bot.send_message(chat_id=r["user_id"], text=caption,
                                            parse_mode=ParseMode.HTML)
    await send_log("admin", f"⚖️ <b>Dispute opened</b> #{did} by <code>{uid}</code>")
    await notify_user(uid, f"⚖️ Dispute #{did} opened. An admin will reply here.")
    return web.json_response({"ok": True, "id": did})


async def h_redeem(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    ok, amount, pts = redeem_points(uid)
    if not ok:
        return json_error("not enough points to redeem yet")
    await notify_user(uid, f"⭐ {pts} points redeemed for {money(amount)}.")
    return web.json_response({"ok": True, "amount": amount, "points": pts,
                              "balance": balance(uid)})


async def h_prefs(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    body = await request.json()
    key = str(body.get("key", ""))
    if key not in [k for k, _l, _d in NOTIF_KEYS]:
        return json_error("unknown preference")
    pref_set(uid, key, bool(body.get("on")))
    return web.json_response({"ok": True, "prefs": prefs_of(uid)})


async def h_admin_channel(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid or not can(uid, "channels"):
        return json_error("forbidden", 403)
    body = await request.json()
    action = str(body.get("action", ""))
    table = "logchannels" if body.get("log") else "channels"
    if action == "add":
        handle = str(body.get("handle", "")).strip()
        if not handle:
            return json_error("paste a channel link first")
        name = handle.replace("https://t.me/", "@").split("/")[-1][:26] or handle[:26]
        if table == "channels":
            ex("INSERT INTO channels(name,handle,chat_id,required,active) VALUES(?,?,?,?,1)",
               (name, handle, handle if handle.startswith(("@", "-100")) else "",
                1 if body.get("required") else 0))
        else:
            first = not q("SELECT 1 FROM logchannels")
            ex("INSERT INTO logchannels(name,chat_id,is_primary,active) VALUES(?,?,?,1)",
               (name, handle, 1 if first else 0))
        audit(uid, "channels", f"added {name}")
    elif action == "del":
        ex(f"DELETE FROM {table} WHERE id=?", (int(body.get("id", 0)),))
        audit(uid, "channels", f"removed {body.get('id')}")
    elif action == "toggle":
        cid = int(body.get("id", 0))
        row = q1(f"SELECT * FROM {table} WHERE id=?", (cid,))
        if row:
            ex(f"UPDATE {table} SET active=? WHERE id=?", (0 if row["active"] else 1, cid))
    elif action == "required" and table == "channels":
        cid = int(body.get("id", 0))
        row = q1("SELECT * FROM channels WHERE id=?", (cid,))
        if row:
            ex("UPDATE channels SET required=? WHERE id=?", (0 if row["required"] else 1, cid))
    elif action == "primary" and table == "logchannels":
        ex("UPDATE logchannels SET is_primary=0")
        ex("UPDATE logchannels SET is_primary=1 WHERE id=?", (int(body.get("id", 0)),))
    else:
        return json_error("unknown action")
    return web.json_response({"ok": True, "adm": admin_state(uid)})


async def h_admin_pricing(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid or not can(uid, "pricing"):
        return json_error("forbidden", 403)
    body = await request.json()
    country = str(body.get("country", "")).strip()
    if country:
        try:
            price = float(body.get("price", 0))
        except (TypeError, ValueError):
            return json_error("price must be a number")
        if price <= 0:
            return json_error("price must be above zero")
        ex("UPDATE stock SET price=? WHERE country=? AND sold=0", (price, country))
        audit(uid, "pricing", f"{country} = {price}")
        return web.json_response({"ok": True, "adm": admin_state(uid)})
    key = str(body.get("key", ""))
    if key not in ("bulk_5", "bulk_10", "bulk_25", "default_price", "flash_percent"):
        return json_error("unknown rule")
    sset(key, str(body.get("value", "")))
    audit(uid, "pricing", f"{key} = {body.get('value')}")
    return web.json_response({"ok": True, "adm": admin_state(uid)})


def admin_gate(request, perm):
    uid = req_user(request)
    if not uid or not can(uid, perm):
        return None
    return uid


async def h_admin_stock(request: web.Request) -> web.Response:
    uid = admin_gate(request, "stock")
    if not uid:
        return json_error("forbidden", 403)
    body = await request.json()
    text = str(body.get("text", ""))
    if not text.strip():
        return json_error("paste at least one line")
    good, bad = parse_stock_lines(text)
    if not good:
        return json_error("nothing usable — " + ("; ".join(bad[:2]) if bad else "check the format"))
    n = insert_stock(good)
    audit(uid, "stock", f"{n} added from the app")
    await send_log("stock", f"📦 <b>Stock added</b> — {n} by <code>{uid}</code>")
    for c in {r["country"] for r in good}:
        with contextlib.suppress(Exception):
            await announce_restock(c)
    return web.json_response({"ok": True, "added": n, "skipped": bad[:6],
                              "adm": admin_state(uid)})


async def h_admin_blacklist(request: web.Request) -> web.Response:
    uid = admin_gate(request, "security")
    if not uid:
        return json_error("forbidden", 403)
    body = await request.json()
    action = str(body.get("action", "add"))
    if action == "add":
        value = str(body.get("value", "")).strip()
        if not value:
            return json_error("type an id, phone or UPI reference")
        kind = "user" if value.isdigit() else ("upi" if "@" in value else "phone")
        ex("INSERT OR IGNORE INTO blacklist(kind,value,added_at) VALUES(?,?,?)",
           (kind, value, now()))
        if kind == "user" and value.isdigit():
            ex("UPDATE users SET banned=1 WHERE user_id=?", (int(value),))
        audit(uid, "security", f"blocked {value}")
    elif action == "del":
        row = q1("SELECT rowid, * FROM blacklist WHERE rowid=?", (int(body.get("id", 0)),))
        if row:
            ex("DELETE FROM blacklist WHERE rowid=?", (row["rowid"],))
            if row["kind"] == "user" and str(row["value"]).isdigit():
                ex("UPDATE users SET banned=0 WHERE user_id=?", (int(row["value"]),))
            audit(uid, "security", f"unblocked {row['value']}")
    return web.json_response({"ok": True, "adm": admin_state(uid)})


async def h_admin_flash(request: web.Request) -> web.Response:
    uid = admin_gate(request, "broadcast")
    if not uid:
        return json_error("forbidden", 403)
    body = await request.json()
    if body.get("stop"):
        sset("flash_until", "")
        audit(uid, "flash", "stopped")
        return web.json_response({"ok": True, "running": False, "adm": admin_state(uid)})
    try:
        pct = float(body.get("percent", 0))
        hours = float(body.get("hours", 0))
    except (TypeError, ValueError):
        return json_error("percent and hours must be numbers")
    if not (1 <= pct <= 90) or not (0 < hours <= 168):
        return json_error("use 1-90% and up to 168 hours")
    sset("flash_percent", pct)
    sset("flash_until", (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat())
    audit(uid, "flash", f"{pct}% for {hours}h")
    await send_log("admin", f"🔥 <b>Flash sale</b> −{pct:.0f}% for {hours:g}h")
    return web.json_response({"ok": True, "running": True, "adm": admin_state(uid)})


async def h_admin_coupon(request: web.Request) -> web.Response:
    uid = admin_gate(request, "broadcast")
    if not uid:
        return json_error("forbidden", 403)
    body = await request.json()
    if body.get("delete"):
        ex("DELETE FROM coupons WHERE code=?", (str(body.get("code", "")).upper(),))
        return web.json_response({"ok": True, "adm": admin_state(uid)})
    code = str(body.get("code", "")).strip().upper()
    if not code:
        return json_error("give the coupon a code")
    try:
        pct = float(body.get("percent", 0))
    except (TypeError, ValueError):
        return json_error("percent must be a number")
    if not (1 <= pct <= 90):
        return json_error("percent must be between 1 and 90")
    ex("INSERT OR REPLACE INTO coupons(code,percent,max_uses,used,active,created_at)"
       " VALUES(?,?,?,0,1,?)",
       (code, pct, int(body.get("max", 0) or 0), now()))
    audit(uid, "coupon", f"{code} {pct}%")
    return web.json_response({"ok": True, "adm": admin_state(uid)})


async def h_admin_broadcast(request: web.Request) -> web.Response:
    uid = admin_gate(request, "broadcast")
    if not uid:
        return json_error("forbidden", 403)
    body = await request.json()
    text = str(body.get("text", "")).strip()
    if len(text) < 3:
        return json_error("write the message first")
    sent = 0
    for r in q("SELECT user_id FROM users WHERE banned=0"):
        if not pref_on(r["user_id"], "promos"):
            continue
        with contextlib.suppress(Exception):
            await _APP.bot.send_message(chat_id=r["user_id"], text=text,
                                        parse_mode=ParseMode.HTML)
            sent += 1
        await asyncio.sleep(0.04)
    audit(uid, "broadcast", f"{sent} users")
    return web.json_response({"ok": True, "sent": sent})


async def h_admin_review(request: web.Request) -> web.Response:
    uid = admin_gate(request, "orders")
    if not uid:
        return json_error("forbidden", 403)
    body = await request.json()
    rid_ = int(body.get("id", 0))
    row = q1("SELECT * FROM reviews WHERE id=?", (rid_,))
    if not row:
        return json_error("not found", 404)
    ex("UPDATE reviews SET hidden=? WHERE id=?", (0 if row["hidden"] else 1, rid_))
    audit(uid, "review", f"{'shown' if row['hidden'] else 'hidden'} #{rid_}")
    return web.json_response({"ok": True, "adm": admin_state(uid)})


async def h_admin_deposit(request: web.Request) -> web.Response:
    uid = admin_gate(request, "payments")
    if not uid:
        return json_error("forbidden", 403)
    body = await request.json()
    dep = q1("SELECT * FROM deposits WHERE id=?", (int(body.get("id", 0)),))
    if not dep:
        return json_error("not found", 404)
    if str(body.get("action")) == "reject":
        ex("UPDATE deposits SET status='rejected' WHERE id=?", (dep["id"],))
        await notify_user(dep["user_id"],
                          f"❌ Your deposit of {money(dep['amount'])} was not approved.")
        audit(uid, "deposit", f"rejected #{dep['id']}")
        return web.json_response({"ok": True, "adm": admin_state(uid)})
    if dep["status"] == "completed":
        return json_error("already credited")
    total = settle_deposit(dep, dep["utr"] or "manual")
    await notify_user(dep["user_id"], f"✅ {money(total)} added to your wallet.")
    audit(uid, "deposit", f"approved #{dep['id']}")
    return web.json_response({"ok": True, "adm": admin_state(uid)})


async def h_admin_backup(request: web.Request) -> web.Response:

    uid = admin_gate(request, "settings")
    if not uid:
        return json_error("forbidden", 403)
    try:
        with open(DB_PATH, "rb") as fh:
            await _APP.bot.send_document(
                chat_id=uid, document=InputFile(fh, filename=f"store-{now()[:10]}.db"),
                caption="Database backup — keep this private.")
    except Exception as e:
        return json_error(f"could not send: {str(e)[:80]}")
    audit(uid, "backup", "sent to admin")
    return web.json_response({"ok": True})


async def h_admin_digest(request: web.Request) -> web.Response:
    uid = admin_gate(request, "analytics")
    if not uid:
        return json_error("forbidden", 403)
    await send_digest()
    return web.json_response({"ok": True})


async def h_admin_export(request: web.Request) -> web.Response:
    uid = admin_gate(request, "analytics")
    if not uid:
        return json_error("forbidden", 403)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["order", "user", "country", "price", "status", "created"])
    for o in q("SELECT * FROM orders ORDER BY created_at DESC"):
        w.writerow([o["id"], o["user_id"], o["country"], o["price"], o["status"],
                    o["created_at"]])
    data = buf.getvalue()
    with contextlib.suppress(Exception):
        bio = io.BytesIO(data.encode())
        bio.name = "orders.csv"
        await _APP.bot.send_document(chat_id=uid, document=InputFile(bio),
                                     caption=f"{len(data.splitlines()) - 1} orders")
    return web.json_response({"ok": True, "rows": len(data.splitlines()) - 1})


async def h_admin_sync(request: web.Request) -> web.Response:
    uid = admin_gate(request, "settings")
    if not uid:
        return json_error("forbidden", 403)
    await register_commands(_APP)
    return web.json_response({"ok": True})


async def h_admin_misc(request: web.Request) -> web.Response:


    uid = req_user(request)
    if not uid or not is_admin(uid):
        return json_error("forbidden", 403)
    body = await request.json()
    what = str(body.get("what", ""))

    if what == "channeltest":
        if not can(uid, "channels"):
            return json_error("forbidden", 403)
        results = []
        for c in q("SELECT * FROM channels WHERE active=1"):
            target = c["chat_id"] or c["handle"]
            try:
                me = await _APP.bot.get_chat_member(chat_id=target, user_id=
                                                    (await _APP.bot.get_me()).id)
                ok = me.status in ("administrator", "creator")
                results.append(f"{'✅' if ok else '⚠️'} {c['name']}")
            except Exception as e:
                results.append(f"❌ {c['name']}: {str(e)[:40]}")
        return web.json_response({"ok": True, "lines": results or ["No channels yet."]})

    if what == "logtest":
        if not can(uid, "logs"):
            return json_error("forbidden", 403)
        await send_log("admin", "🔔 <b>Test message</b> — logging is working.")
        return web.json_response({"ok": True})

    if what == "addadmin":
        if not is_owner(uid):
            return json_error("only the owner can add admins", 403)
        try:
            target = int(str(body.get("value", "")).strip())
        except ValueError:
            return json_error("send the numeric user id")
        ex("INSERT OR IGNORE INTO admins(user_id,role,perms,label,added_by,added_at)"
           " VALUES(?,'manager',?,?,?,?)",
           (target, json.dumps({k: False for k in PERM_KEYS}), f"admin {target}", uid, now()))
        audit(uid, "admin", f"added {target}")
        await notify_user(target, "👑 You are now an admin. Send /admin to open the panel.")
        return web.json_response({"ok": True, "adm": admin_state(uid)})

    if what == "clearstock":
        if not can(uid, "stock"):
            return json_error("forbidden", 403)
        country = str(body.get("value", ""))
        n = q1("SELECT COUNT(*) c FROM stock WHERE country=? AND sold=0", (country,))["c"]
        ex("DELETE FROM stock WHERE country=? AND sold=0", (country,))
        audit(uid, "stock", f"cleared {country} ({n})")
        return web.json_response({"ok": True, "removed": n, "adm": admin_state(uid)})

    return json_error("unknown action")


async def h_alert(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    if not feat("restockAlert"):
        return json_error("not available")
    body = await request.json()
    country = str(body.get("country", "")).strip()
    if not country:
        return json_error("which country?")
    on = alert_subscribe(uid, country)
    return web.json_response({"ok": True, "on": on,
                              "alerts": [r["country"].title() for r in
                                         q("SELECT country FROM stock_alerts WHERE user_id=?",
                                           (uid,))]})


async def h_order_action(request: web.Request) -> web.Response:

    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    body = await request.json()
    oid = str(body.get("id", ""))
    what = str(body.get("what", ""))
    o = q1("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, uid))
    if not o:
        return json_error("that order is not yours", 404)
    st = q1("SELECT payload FROM stock WHERE id=?", (o["stock_id"],))
    payload = (st["payload"] if st else "") or ""

    if what == "otp":
        left = otp_window_left(oid)
        if left <= 0:
            return json_error("the one-hour handover window for this order has ended")
        if otp_quota_left(oid) <= 0:
            return json_error(cget("otpLimit"))
        code = await fetch_live_otp(oid)
        source = "live"
        if not code:
            code = o["otp"]
            source = "stored"
        if not code:
            info = parse_session(payload)
            return json_error(
                "No code has arrived yet. Request one in Telegram on "
                + (info["phone"] or "the number") + ", then tap again.")
        ex("UPDATE orders SET otp=? WHERE id=?", (code, oid))
        ex("INSERT INTO otp_log(order_id,user_id,phone,code,sent_at) VALUES(?,?,?,?,?)",
           (oid, uid, parse_session(payload)["phone"], code, now()))
        return web.json_response({"ok": True, "code": code, "source": source,
                                  "left": otp_quota_left(oid), "window": left})

    if what == "details":
        if not feat("redownload"):
            return json_error("not available")
        info = parse_session(payload)
        lines = [head("Order " + oid, f"{flag_for(o['country'])} {o['country']}"), ""]
        if info["phone"]:
            lines.append(f"📱 <b>Number:</b> <code>{esc(info['phone'])}</code>")
        if info["twofa"]:
            lines.append(f"🔐 <b>2FA:</b> <code>{esc(info['twofa'])}</code>")
        lines.append(f"💵 <b>Paid:</b> {money(o['price'])}")
        await notify_user(uid, premiumize("\n".join(lines)))
        return web.json_response({"ok": True, "sent": True})

    if what == "file":
        if not feat("sessionFile"):
            return json_error("not available")
        if not payload.strip():
            return json_error("no session stored for this order")
        bio = io.BytesIO(payload.encode())
        bio.name = f"{oid}.session.txt"
        with contextlib.suppress(Exception):
            await _APP.bot.send_document(chat_id=uid, document=InputFile(bio),
                                         caption="Delete this after use.")
        return web.json_response({"ok": True, "sent": True})

    if what == "receipt":
        if not feat("receipts"):
            return json_error("not available")
        text = premiumize("\n".join([
            head("Receipt", oid), "",
            f"{flag_for(o['country'])} <b>{esc(o['country'])}</b>",
            f"💵 <b>Paid:</b> {money(o['price'])}",
            f"🕒 <b>When:</b> {esc((o['created_at'] or '')[:16].replace('T', ' '))}",
            f"🏪 <b>{esc(sget('store_name'))}</b>",
        ]))
        await notify_user(uid, text)
        return web.json_response({"ok": True, "sent": True})

    if what == "claim":
        if q1("SELECT 1 FROM claims WHERE order_id=?", (oid,)):
            return json_error("a claim for this order is already with an admin")
        reason = str(body.get("reason", ""))[:80] or "Not stated"
        details = str(body.get("details", ""))[:500]
        if len(details) < 5:
            return json_error("tell us what went wrong")
        cid = ex("INSERT INTO claims(order_id,user_id,reason,details,status,created_at)"
                 " VALUES(?,?,?,?,'open',?)", (oid, uid, reason, details, now()))
        u = get_user(uid)
        info = parse_session(payload)
        text = premiumize("\n".join([
            head(f"Claim #{cid}", reason),
            "",
            f"<b>User:</b> {esc(u['first_name'] or '—')} "
            f"({'@' + esc(u['username']) if u['username'] else 'no username'})",
            f"<b>ID:</b> <code>{uid}</code>",
            f"<b>Order:</b> <code>{esc(oid)}</code> · {esc(o['country'])}",
            f"<b>Paid:</b> {money(o['price'])}",
            f"<b>Number:</b> <code>{esc(info['phone'] or '—')}</code>",
            f"<b>Bought:</b> {esc((o['created_at'] or '')[:16].replace('T', ' '))}",
            f"<b>Past orders:</b> {u['total_orders']} · spent {money(u['total_spent'])}",
            "",
            esc(details)[:400],
            "",
            "<i>Nothing has been refunded. Decide with the buttons below.</i>",
        ]))
        for r in q("SELECT user_id FROM admins"):
            if not can(r["user_id"], "orders"):
                continue
            kb = InlineKeyboardMarkup([
                [B("🔁 Replace", f"clrep:{cid}", 10), B("💸 Refund", f"clref:{cid}", 10)],
                [B("✖ Decline", f"cldec:{cid}", 22)],
            ])
            with contextlib.suppress(Exception):
                await _APP.bot.send_message(chat_id=r["user_id"], text=text,
                                            reply_markup=kb, parse_mode=ParseMode.HTML)
        await notify_user(uid, premiumize(
            head("Claim sent", f"#{cid}")
            + "\n\nAn admin will look at your order and reply here."))
        return web.json_response({"ok": True, "id": cid})

    if what == "loggedin":
        ex("INSERT OR REPLACE INTO logins(order_id,state,seen_at) VALUES(?,?,?)",
           (oid, "in", now()))
        row = q1("SELECT payload FROM stock WHERE id=?", (o["stock_id"],))
        info = parse_session((row["payload"] if row else "") or "")
        keep = info["phone"] + (f" | 2fa:{info['twofa']}" if info["twofa"] else "")
        ex("UPDATE stock SET payload=? WHERE id=?", (keep, o["stock_id"]))
        ex("UPDATE orders SET status='closed', closed_at=? WHERE id=?", (now(), oid))
        await send_log("otp", f"🔒 <b>Buyer confirmed login</b> {esc(oid)} — session released")
        return web.json_response({"ok": True, "state": "in"})

    return json_error("unknown action")


async def h_review(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    if not feat("reviews"):
        return json_error("not available")
    body = await request.json()
    country = str(body.get("country", "")).strip()
    try:
        rating = int(body.get("rating", 0))
    except (TypeError, ValueError):
        return json_error("rating must be 1-5")
    if not country or not (1 <= rating <= 5):
        return json_error("pick a country and 1-5 stars")
    if not q1("SELECT 1 FROM orders WHERE user_id=? AND country=?", (uid, country)):
        return json_error("you can only rate something you bought")
    ex("INSERT INTO reviews(user_id,country,rating,text,created_at) VALUES(?,?,?,?,?)",
       (uid, country, rating, str(body.get("text", ""))[:200], now()))
    avg, n = country_rating(country)
    return web.json_response({"ok": True, "avg": avg, "count": n})


async def h_crypto_claim(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    body = await request.json()
    ref = str(body.get("ref", ""))[:60]
    await notify_admins(
        f"₿ <b>Crypto transfer claimed</b>\nuser <code>{uid}</code>"
        + (f"\nref <code>{esc(ref)}</code>" if ref else ""), "payments")
    await notify_user(uid, "₿ Thanks — an admin will confirm the transfer shortly.")
    return web.json_response({"ok": True})


async def h_cart(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    body = await request.json()
    action = body.get("action", "add")
    if action == "clear":
        ex("DELETE FROM cart WHERE user_id=?", (uid,))
    elif action == "remove":
        ex("DELETE FROM cart WHERE user_id=? AND country=?", (uid, body.get("country", "")))
    else:
        ex("INSERT INTO cart(user_id,country,qty) VALUES(?,?,?)",
           (uid, body.get("country", ""), int(body.get("qty", 1))))
    return web.json_response({"ok": True})


async def h_wishlist(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid:
        return json_error("unauthorised", 401)
    body = await request.json()
    c = body.get("country", "")
    if q1("SELECT 1 FROM wishlist WHERE user_id=? AND country=?", (uid, c)):
        ex("DELETE FROM wishlist WHERE user_id=? AND country=?", (uid, c))
        return web.json_response({"ok": True, "saved": False})
    ex("INSERT OR IGNORE INTO wishlist(user_id,country) VALUES(?,?)", (uid, c))
    return web.json_response({"ok": True, "saved": True})


async def h_admin_feature(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid or not can(uid, "features"):
        return json_error("forbidden", 403)
    body = await request.json()
    key = str(body.get("key", ""))
    if key not in FEATURE_KEYS:
        return json_error("unknown feature")
    feat_set(key, bool(body.get("on")))
    audit(uid, "feature", f"{key} → {'on' if body.get('on') else 'off'} (mini app)")
    return web.json_response({"ok": True, "features": feat_all()})


async def h_admin_content(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid or not can(uid, "content"):
        return json_error("forbidden", 403)
    body = await request.json()
    key = str(body.get("key", ""))
    if key not in DEFAULT_CONTENT:
        return json_error("unknown key")
    value = str(body.get("value", ""))
    cset(key, value)
    if key == "storeName":
        sset("store_name", value)
    elif key == "storeTag":
        sset("store_tagline", value)
    audit(uid, "content", f"{key} (mini app)")
    return web.json_response({"ok": True})


async def h_admin_setting(request: web.Request) -> web.Response:
    uid = req_user(request)
    if not uid or not can(uid, "settings"):
        return json_error("forbidden", 403)
    body = await request.json()
    key = str(body.get("key", ""))
    value = str(body.get("value", ""))
    if key == "autodep":
        sset("autodep_max", "500" if value == "1" else "0")
        audit(uid, "setting", f"auto-approve {'on' if value == '1' else 'off'}")
        return web.json_response({"ok": True})
    if key not in DEFAULT_SETTINGS:
        return json_error("unknown setting")
    if key == "gmail_pass" and not value.strip():
        return web.json_response({"ok": True, "skipped": True})
    sset(key, value)
    audit(uid, "setting", f"{key} (mini app)")
    return web.json_response({"ok": True, "value": "" if "pass" in key else value})


def auth_why(init: str) -> str:

    if not init:
        return "missing"
    data = {}
    for chunk in init.split("&"):
        k, _, v = chunk.partition("=")
        if k:
            data[k] = urllib.parse.unquote(v)
    if not data:
        return "bad-format"
    if not data.pop("hash", ""):
        return "no-hash"
    if valid_init_data(init) is None:
        max_age = int(sgetf("initdata_max_age", 0))
        if max_age > 0:
            with contextlib.suppress(Exception):
                if time.time() - float(data.get("auth_date", 0)) > max_age:
                    return "stale"
        try:
            user = json.loads(data.get("user", "{}"))
        except ValueError:
            return "bad-user"
        if "id" not in user:
            return "no-id"
        return "bad-hash"
    return "ok"


async def h_whoami(request: web.Request) -> web.Response:

    init = init_header(request)
    uid = valid_init_data(init)
    fields = sorted(dict(urllib.parse.parse_qsl(init, keep_blank_values=True))) if init else []
    return web.json_response(
        {
            "ok": bool(uid),
            "user_id": uid,
            "admin": is_admin(uid) if uid else False,
            "owner": is_owner(uid) if uid else False,
            "init_data_present": bool(init),
            "init_data_fields": fields,
            "reason": "" if uid else auth_why(init),
            "why": auth_why(init),
            "bot_id": BOT_TOKEN.split(":")[0] if BOT_TOKEN else "",
            "key_accepted": bool(key_user(request.query.get("k", "")
                                          or request.headers.get("X-App-Key", ""))),
        }
    )


_AVATARS: dict = {}
AVATAR_COLORS = ["#4f7cff", "#e0564a", "#1fae72", "#c084fc", "#f59e0b", "#06b6d4"]


def _initial_avatar(uid: int) -> web.Response:

    row = q1("SELECT first_name, username FROM users WHERE user_id=?", (uid,))
    name = ((row["first_name"] if row else "") or (row["username"] if row else "") or "?").strip()
    letter = esc(name[:1].upper() or "?")
    colour = AVATAR_COLORS[uid % len(AVATAR_COLORS)]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160">'
        f'<circle cx="80" cy="80" r="80" fill="{colour}"/>'
        f'<text x="80" y="80" font-family="system-ui,Segoe UI,Roboto,sans-serif" font-size="74"'
        f' font-weight="700" fill="#fff" text-anchor="middle" dominant-baseline="central">'
        f'{letter}</text></svg>'
    )
    return web.Response(text=svg, content_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=600"})


async def h_avatar(request: web.Request) -> web.Response:

    uid = int(request.query.get("u", 0) or 0)
    token = request.query.get("t", "")
    if not uid or not hmac.compare_digest(token, qr_token(uid)):
        return web.Response(status=404, text="no")
    hit = _AVATARS.get(uid)
    if hit is not None and time.time() - hit[0] < 3600:
        if not hit[1]:
            return _initial_avatar(uid)
        return web.Response(body=hit[1], content_type="image/jpeg")
    data = b""
    try:
        photos = await _APP.bot.get_user_profile_photos(uid, limit=1)
        if photos.total_count:
            size = photos.photos[0][-1]
            f = await _APP.bot.get_file(size.file_id)
            data = bytes(await f.download_as_bytearray())
    except Exception as e:
        log.info("avatar for %s unavailable: %s", uid, e)
    _AVATARS[uid] = (time.time(), data)
    if not data:
        return _initial_avatar(uid)
    return web.Response(body=data, content_type="image/jpeg")


async def h_pay(request: web.Request) -> web.Response:


    dep_id = int(request.query.get("id", 0) or 0)
    token = request.query.get("t", "")
    dep = q1("SELECT * FROM deposits WHERE id=?", (dep_id,))
    if not dep or not hmac.compare_digest(token, qr_token(dep_id)):
        return web.Response(text="Link expired.", status=404)
    link = upi_link(dep["amount"], dep["code"])
    if not link:
        return web.Response(text="Payments are not configured.", status=503)
    qr = qr_data_uri(dep)
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pay {esc(sget('store_name'))}</title>
<style>
body{{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1017;color:#eef1f8;
display:flex;min-height:100vh;align-items:center;justify-content:center;padding:22px}}
.c{{max-width:340px;width:100%;text-align:center}}
img{{width:200px;border-radius:14px;background:#fff;padding:8px}}
h1{{font-size:1.1rem;margin:16px 0 4px}} p{{color:#98a2b8;font-size:.86rem;margin:0 0 16px}}
a.b{{display:block;padding:14px;border-radius:12px;background:#3b82f6;color:#fff;
text-decoration:none;font-weight:700;margin-bottom:10px}}
code{{background:#171c28;padding:7px 10px;border-radius:8px;display:block;margin-top:10px;font-size:.8rem}}
</style></head><body><div class="c">
{'<img src="' + qr + '" alt="QR">' if qr else ''}
<h1>Pay {money(dep['amount'])}</h1>
<p>Opening your UPI app…</p>
<a class="b" id="go" href="{esc(link)}">Open UPI app</a>
<code>{esc(sget('upi_id'))}</code>
<code>Note: {esc(dep['code'])}</code>
</div>
<script>
setTimeout(function(){{ try{{ window.location.href = {json.dumps(link)}; }}catch(e){{}} }}, 350);
document.getElementById("go").addEventListener("click", function(){{
  setTimeout(function(){{ window.location.href = {json.dumps(link)}; }}, 60);
}});
</script></body></html>"""
    return web.Response(text=page, content_type="text/html")


async def h_health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "design": DESIGN,
            "store": sget("store_name"),
            "features_on": sum(1 for k in FEATURE_KEYS if feat(k)),
            "stock": q1("SELECT COUNT(*) c FROM stock WHERE sold=0")["c"],
            "users": q1("SELECT COUNT(*) c FROM users")["c"],
        }
    )


def build_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", h_index)
    app.router.add_get("/index.html", h_index)
    app.router.add_get("/pay", h_pay)
    app.router.add_get("/api/avatar", h_avatar)
    app.router.add_get("/api/health", h_health)
    app.router.add_get("/api/whoami", h_whoami)
    app.router.add_get("/api/state", h_state)
    app.router.add_post("/api/buy", h_buy)
    app.router.add_post("/api/deposit/create", h_dep_create)
    app.router.add_get("/api/deposit/qr", h_dep_qr)
    app.router.add_post("/api/deposit/verify", h_dep_verify)
    app.router.add_post("/api/cart", h_cart)
    app.router.add_post("/api/dispute", h_dispute)
    app.router.add_post("/api/redeem", h_redeem)
    app.router.add_post("/api/prefs", h_prefs)
    app.router.add_post("/api/admin/channel", h_admin_channel)
    app.router.add_post("/api/admin/pricing", h_admin_pricing)
    app.router.add_post("/api/admin/stock", h_admin_stock)
    app.router.add_post("/api/admin/blacklist", h_admin_blacklist)
    app.router.add_post("/api/admin/flash", h_admin_flash)
    app.router.add_post("/api/admin/coupon", h_admin_coupon)
    app.router.add_post("/api/admin/broadcast", h_admin_broadcast)
    app.router.add_post("/api/admin/review", h_admin_review)
    app.router.add_post("/api/admin/deposit", h_admin_deposit)
    app.router.add_post("/api/admin/backup", h_admin_backup)
    app.router.add_post("/api/admin/digest", h_admin_digest)
    app.router.add_post("/api/admin/export", h_admin_export)
    app.router.add_post("/api/admin/sync", h_admin_sync)
    app.router.add_post("/api/admin/misc", h_admin_misc)
    app.router.add_post("/api/alert", h_alert)
    app.router.add_post("/api/order", h_order_action)
    app.router.add_post("/api/review", h_review)
    app.router.add_post("/api/crypto", h_crypto_claim)
    app.router.add_post("/api/cart/checkout", h_cart_checkout)
    app.router.add_post("/api/deposit/utr", h_dep_utr)
    app.router.add_post("/api/deposit/claim", h_dep_claim)
    app.router.add_post("/api/checkin", h_checkin)
    app.router.add_post("/api/wishlist", h_wishlist)
    app.router.add_post("/api/admin/feature", h_admin_feature)
    app.router.add_post("/api/admin/content", h_admin_content)
    app.router.add_post("/api/admin/setting", h_admin_setting)

    async def cors(request, handler):
        if request.method == "OPTIONS":
            return web.Response(headers={"Access-Control-Allow-Origin": "*",
                                         "Access-Control-Allow-Headers": "Content-Type, X-Init-Data",
                                         "Access-Control-Allow-Methods": "GET, POST, OPTIONS"})
        resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Init-Data"
        return resp

    app.middlewares.append(web.middleware(cors))
    return app


async def loop_expire_deposits() -> None:
    for d in q("SELECT * FROM deposits WHERE status IN ('awaiting','verifying')"):
        if deposit_expired(d):
            ex("UPDATE deposits SET status='expired' WHERE id=?", (d["id"],))


async def loop_abandon_reminder() -> None:

    if not feat("abandonRemind"):
        return
    for d in q("SELECT * FROM deposits WHERE status='awaiting'"):
        with contextlib.suppress(Exception):
            if datetime.fromisoformat(d["expires_at"]) > datetime.now(timezone.utc):
                continue
            ex("UPDATE deposits SET status='expired' WHERE id=?", (d["id"],))
            await notify_user(
                d["user_id"],
                premiumize(f"⌛ Your {money(d['amount'])} payment request has expired. "
                           "Tap Add Funds to start a new one."))


async def send_digest() -> None:

    today = datetime.now(timezone.utc).date().isoformat()
    rev = q1("SELECT COALESCE(SUM(price),0) s FROM orders WHERE substr(created_at,1,10)=?",
             (today,))["s"]
    orders_n = q1("SELECT COUNT(*) c FROM orders WHERE substr(created_at,1,10)=?", (today,))["c"]
    new_users = q1("SELECT COUNT(*) c FROM users WHERE substr(joined_at,1,10)=?", (today,))["c"]
    deps = q1("SELECT COALESCE(SUM(amount),0) s FROM deposits WHERE status='completed' "
              "AND substr(done_at,1,10)=?", (today,))["s"]
    stock_left = q1("SELECT COUNT(*) c FROM stock WHERE sold=0")["c"]
    pend = q1("SELECT COUNT(*) c FROM deposits WHERE status='review'")["c"]
    top = q("SELECT country, COUNT(*) n FROM orders WHERE substr(created_at,1,10)=? "
            "GROUP BY country ORDER BY n DESC LIMIT 3", (today,))
    lines = [
        head("Daily digest", today), "",
        f"\U0001f4b0 <b>Revenue:</b> {money(rev)}",
        f"\U0001f9fe <b>Orders:</b> {orders_n}",
        f"\U0001f4b3 <b>Deposits:</b> {money(deps)}",
        f"\U0001f465 <b>New users:</b> {new_users}",
        f"\U0001f4e6 <b>Stock left:</b> {stock_left}",
    ]
    if pend:
        lines.append(f"\u23f3 <b>Waiting for review:</b> {pend}")
    if top:
        lines += ["", "<b>Best sellers today</b>"]
        lines += [f"{flag_for(t['country'])} {esc(t['country'])} \u2014 {t['n']}" for t in top]
    text = premiumize("\n".join(lines))
    for r in q("SELECT user_id FROM admins"):
        if sget("digest_to_admins", "1") == "1" and can(r["user_id"], "analytics"):
            with contextlib.suppress(Exception):
                await _APP.bot.send_message(chat_id=r["user_id"], text=text,
                                            parse_mode=ParseMode.HTML)
    await send_log("admin", text)


async def loop_digest() -> None:
    if not feat("digest"):
        return
    if datetime.now().strftime("%H:%M") != sget("digest_at", "23:30"):
        return
    await send_digest()


async def loop_backup() -> None:

    if not feat("autoBackup") or not _APP:
        return
    for r in q("SELECT user_id FROM admins"):
        if not can(r["user_id"], "settings"):
            continue
        with contextlib.suppress(Exception):
            with open(DB_PATH, "rb") as fh:
                await _APP.bot.send_document(
                    chat_id=r["user_id"],
                    document=InputFile(fh, filename=f"store-{now()[:10]}.db"),
                    caption="Automatic backup — keep this private.")


async def loop_winback() -> None:
    if not feat("winback"):
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    for u in q("SELECT user_id FROM users WHERE banned=0 AND last_seen < ? LIMIT 20", (cutoff,)):
        await notify_user(u["user_id"], "👋 We miss you — there is fresh stock waiting in the store.")
        ex("UPDATE users SET last_seen=? WHERE user_id=?", (now(), u["user_id"]))


async def register_commands(app) -> None:
    if app is None:
        return
    if not feat("slashCmds"):
        with contextlib.suppress(TelegramError):
            await app.bot.set_my_commands([])
        return
    cmds = [
        ("start", "Open the store"),
        ("shop", "Browse stock"),
        ("balance", "Wallet balance"),
        ("orders", "My orders"),
        ("support", "Contact support"),
        ("app", "Open the store page"),
    ]
    if feat("adminRoles"):
        cmds.append(("diag", "Check everything is working"))
    if feat("guide"):
        cmds.insert(4, ("guide", "How it works"))
    if feat("contentEditor"):
        cmds.append(("admin", "Admin panel"))
    with contextlib.suppress(TelegramError):
        await app.bot.set_my_commands(cmds)


async def start_web(app: Application) -> int:

    global WEB_PORT_USED
    runner = web.AppRunner(build_web_app())
    await runner.setup()
    last = None
    for port in range(WEB_PORT, WEB_PORT + 12):
        try:
            await web.TCPSite(runner, WEB_HOST, port).start()
        except OSError as e:
            last = e
            log.warning("port %s busy, trying %s", port, port + 1)
            continue
        WEB_PORT_USED = port
        app.bot_data["web_runner"] = runner
        log.info("mini app server on http://%s:%s  (design %s)", WEB_HOST, port, DESIGN)
        if PREVIEW_USER:
            log.warning("PREVIEW MODE: unauthenticated requests act as user %s", PREVIEW_USER)
        return port
    log.error("could not bind any port from %s upward: %s", WEB_PORT, last)
    await notify_admins(
        f"⚠️ <b>Mini app server could not start</b>\n\nPorts {WEB_PORT}-{WEB_PORT + 11} "
        f"are all in use.\n<i>{esc(str(last))}</i>\n\n"
        "Set a free one with <code>WEB_PORT=9000 python3 main.py</code>."
    )
    return 0


async def on_startup(app: Application) -> None:

    global _APP
    _APP = app

    try:
        port = await start_web(app)
    except Exception as e:
        port = 0
        log.exception("web server failed to start: %s", e)

    with contextlib.suppress(Exception):
        await register_commands(app)
    with contextlib.suppress(Exception):
        me = await app.bot.get_me()
        if me and me.username:
            sset("bot_username", me.username)
    if feat("colorBtn"):
        log.info("coloured buttons: %s", "active" if colored_buttons_active() else "sent anyway")

    if OWNER_ID:
        with contextlib.suppress(TelegramError):
            await app.bot.send_message(
                chat_id=OWNER_ID,
                text=premiumize(
                    head("Bot online", sget("store_name"))
                    + ("\n\n🖥 <b>Store page:</b> running on port %s" % port if port
                       else "\n\n⚠️ <b>Store page:</b> could not start")
                    + "\n🌐 <b>Public link:</b> opening…"
                    + "\n\nOpen /admin to finish the setup:\n"
                    "• Payments → UPI ID and Gmail\n• Channels → force-join list\n"
                    "• Logs → where events are posted\n• Features → what users can see"
                ),
                parse_mode=ParseMode.HTML,
            )

    try:
        start_background()
    except Exception as e:
        log.exception("background loops failed to start: %s", e)

    if port:
        async def bring_up_public() -> None:
            try:
                url = await start_tunnel()
            except Exception as e:
                log.exception("tunnel crashed: %s", e)
                return
            if not url:
                return
            with contextlib.suppress(TelegramError):
                await app.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(text="Store", web_app=WebAppInfo(url=url))
                )

        app.create_task(bring_up_public())


BG_TASKS: list = []


async def every(seconds: float, fn, first: float = 0) -> None:
    if first:
        await asyncio.sleep(first)
    while True:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("%s failed: %s", getattr(fn, "__name__", fn), e)
        await asyncio.sleep(seconds)


def start_background() -> None:
    schedule = [
        (60, loop_expire_deposits, 30),
        (60, loop_abandon_reminder, 45),
        (60, loop_digest, 60),
        (120, loop_auto_deposit, 90),
        (90, loop_tunnel_watch, 150),
        (120, loop_login_timeout, 120),
        (300, loop_session_handover, 240),
        (max(20, int(sgetf("otp_poll_seconds", 45))), loop_otp_forward, 120),
        (3600 * max(1, int(sgetf("backup_hours", 6))), loop_backup, 300),
        (3600 * 12, loop_winback, 600),
    ]
    for seconds, fn, first in schedule:
        BG_TASKS.append(asyncio.ensure_future(every(seconds, fn, first)))
    log.info("%d background loops running (no job queue)", len(BG_TASKS))


def stop_background() -> None:
    for t in BG_TASKS:
        t.cancel()
    BG_TASKS.clear()


async def on_shutdown(app: Application) -> None:
    stop_background()
    await stop_tunnel()
    runner = app.bot_data.get("web_runner")
    if runner:
        await runner.cleanup()


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    await update.effective_message.reply_text(
        admin_home_text(uid), reply_markup=admin_kb(uid), parse_mode=ParseMode.HTML
    )


async def cmd_shop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cs = countries()
    if not cs:
        await update.effective_message.reply_text(
            head("Out of stock", "Nothing listed yet."), parse_mode=ParseMode.HTML)
        return
    await update.effective_message.reply_text(
        head(cget("shopHead"), f"{sum(c['qty'] for c in cs)} accounts ready"),
        reply_markup=shop_kb(0), parse_mode=ParseMode.HTML)


async def cmd_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    await update.effective_message.reply_text(
        head("My Orders"), reply_markup=orders_kb(uid), parse_mode=ParseMode.HTML)


async def cmd_support(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data["await"] = ("support", None)
    await update.effective_message.reply_text(
        head("Support") + "\n\n" + cget("support_text"), parse_mode=ParseMode.HTML)


async def cmd_diag(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:

    uid = update.effective_user.id
    if not is_admin(uid):
        return
    msg = update.effective_message
    url = public_url()
    tok = BOT_TOKEN.split(":")[0] if BOT_TOKEN else "—"
    qr_ok = True
    qr_note = ""
    try:
        png = qr_png("upi://pay?pa=test@upi&am=1&cu=INR&tn=T")
        qr_note = f"{len(png)} byte PNG"
    except Exception as e:
        qr_ok = False
        qr_note = str(e)[:60]
    lines = [
        head("Diagnostics", sget("store_name")),
        "",
        f"🤖 <b>Bot id:</b> <code>{esc(tok)}</code>",
        f"🖥 <b>Store server:</b> port {tunnel_port()}",
        f"🌐 <b>Public URL:</b> {esc(url) if url else 'none — mini app cannot open'}",
        f"📶 <b>Tunnel:</b> {esc(TUNNEL['status'])}"
        + (f" ({esc(TUNNEL['how'])})" if TUNNEL["how"] else ""),
        "",
        f"🧾 <b>QR generator:</b> {'working, ' + qr_note if qr_ok else 'BROKEN: ' + qr_note}",
        f"🎛 <b>QR feature:</b> {'on' if feat('payQR') else 'OFF — switch it on in Features'}",
        f"🏦 <b>UPI ID:</b> {esc(sget('upi_id') or 'not set — top-ups disabled')}",
        f"📧 <b>Gmail:</b> {esc(sget('gmail_id') or 'not set — Verify can only queue for review')}",
        "",
        f"📦 <b>Stock:</b> {q1('SELECT COUNT(*) c FROM stock WHERE sold=0')['c']} available",
        f"👥 <b>Users:</b> {q1('SELECT COUNT(*) c FROM users')['c']}",
        f"🎛 <b>Features on:</b> {sum(1 for k in FEATURE_KEYS if feat(k))} of {len(FEATURE_KEYS)}",
        f"🔁 <b>Background loops:</b> {len(BG_TASKS)}",
    ]
    if not url:
        lines += ["", "<i>Fix the public URL first — without https Telegram will not open "
                      "the mini app at all.</i>"]
    if not sget("gmail_id"):
        lines += ["", "<i>Without Gmail, Verify sends the payment to manual review. "
                      "That is working as designed, not a failure.</i>"]
    rows = []
    mb = miniapp_button("🛍 Open Store", uid)
    if mb:
        rows.append([mb])
    rows.append([B("🧾 Send me a test QR", "qrtest", 22)])
    rows.append([B("🌐 Mini app status", "miniapp", 22)])
    await msg.reply_text(premiumize("\n".join(lines)),
                         reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)


async def cmd_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:

    uid = update.effective_user.id
    mb = miniapp_button("🛍 Open Store", uid)
    if mb:
        await update.effective_message.reply_text(
            premiumize(head("Store", sget("store_name"))),
            reply_markup=InlineKeyboardMarkup([[mb]]), parse_mode=ParseMode.HTML)
        return
    if is_admin(uid):
        await update.effective_message.reply_text(
            miniapp_status_text(), reply_markup=miniapp_kb(), parse_mode=ParseMode.HTML)
        return
    await update.effective_message.reply_text(
        premiumize(head("Almost there", "The store page is still starting — try again shortly.")),
        parse_mode=ParseMode.HTML)


async def cmd_guide(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not feat("guide"):
        return
    await update.effective_message.reply_text(
        premiumize(guide_text(0)), reply_markup=guide_kb(0), parse_mode=ParseMode.HTML)


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:

    uid = update.effective_user.id
    pending = ctx.user_data.get("await")
    if not pending or pending[0] != "dep_shot":
        return
    dep_id = pending[1]
    photo = update.effective_message.photo[-1]
    ex("INSERT OR REPLACE INTO depshots(dep_id,file_id,added_at) VALUES(?,?,?)",
       (dep_id, photo.file_id, now()))
    ex("UPDATE deposits SET status='waiting_utr' WHERE id=?", (dep_id,))
    ctx.user_data["await"] = ("dep_utr", dep_id)
    await update.effective_message.reply_text(
        premiumize(head("Now the UTR", "The 10-12 digit reference from your payment app")
                   + "\n\nSend it as a message."),
        reply_markup=InlineKeyboardMarkup([[B("✖ Cancel", f"depcancel:{dep_id}", 22)]]),
        parse_mode=ParseMode.HTML)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("handler error", exc_info=ctx.error)
    with contextlib.suppress(Exception):
        await send_log("error", f"⚠️ <b>Error</b>\n<code>{esc(str(ctx.error)[:300])}</code>")


def main() -> None:
    if not BOT_TOKEN:
        sys.exit("BOT_TOKEN is missing — run:  export BOT_TOKEN=\"123456:ABC...\"")
    if not OWNER_ID:
        sys.exit("OWNER_ID is still 0 — open main.py and set it to your Telegram id.")
    init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .job_queue(None)
        .concurrent_updates(True)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("shop", cmd_shop))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("support", cmd_support))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("guide", cmd_guide))
    app.add_handler(CommandHandler("app", cmd_app))
    app.add_handler(CommandHandler("diag", cmd_diag))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)

    log.info("starting %s (design %s)", sget("store_name"), DESIGN)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
