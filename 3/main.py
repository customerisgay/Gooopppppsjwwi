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
DESIGN = "3-soft"


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
    "store_tagline": "Calm and simple",
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
    "storeTag": "Calm and simple",
    "tick1": "<b>Instant delivery</b> on every country",
    "tick2": "Wallet top-ups <b>auto-verified</b>",
    "tick3": "Failed login? <b>Auto refund</b>",
    "tick4": "Daily check-in <b>bonus</b>",
    "heroKick": "In stock now",
    "heroH1": "Everything you need,<br>in two taps.",
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
    "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIiBkYXRhLWRlc2lnbj0ic29mdCI+CjxoZWFk"
    "Pgo8bWV0YSBjaGFyc2V0PSJ1dGYtOCI+CjxtZXRhIG5hbWU9InZpZXdwb3J0IiBjb250ZW50PSJ3"
    "aWR0aD1kZXZpY2Utd2lkdGgsaW5pdGlhbC1zY2FsZT0xLHZpZXdwb3J0LWZpdD1jb3ZlcixtYXhp"
    "bXVtLXNjYWxlPTEiPgo8c2NyaXB0IHNyYz0iaHR0cHM6Ly90ZWxlZ3JhbS5vcmcvanMvdGVsZWdy"
    "YW0td2ViLWFwcC5qcyI+PC9zY3JpcHQ+Cjx0aXRsZT5ab3JvIFRHIFN0b3JlIOKAlCBTdG9yZSBN"
    "aW5pIEFwcCAoRGVzaWduIDMpPC90aXRsZT4KPHN0eWxlPgo6cm9vdHsKICAtLWJnOiNmNWY2Zjg7"
    "IC0tYmcyOiNmZmZmZmY7IC0tY2FyZDojZmZmZmZmOyAtLWNhcmQyOiNmMWYzZjc7CiAgLS1saW5l"
    "OiNlNmU4ZWU7IC0tdHg6IzE2MTgyMTsgLS10eDI6Izc2N2I4YTsgLS1hY2M6IzJmNmJmZjsgLS1h"
    "Y2MyOiMyZjZiZmY7CiAgLS1vazojMTJhNTZhOyAtLXdhcm46I2UwOGEwMDsgLS1iYWQ6I2UyNDQ1"
    "YzsKICAtLXI6MjBweDsgLS1yczoxNnB4OyAtLWJ0bmg6NDRweDsgLS1mb250Oi1hcHBsZS1zeXN0"
    "ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LCdTZWdvZSBVSScsUm9ib3RvLHNhbnMtc2VyaWY7IC0tZmhl"
    "YWQ6LWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsJ1NlZ29lIFVJJyxSb2JvdG8sc2Fu"
    "cy1zZXJpZjsKICAtLXNoYWRvdzowIDRweCAxOHB4IHJnYmEoMjAsMjUsNDUsLjA2KTsgLS1waWxs"
    "Z3JhZDpsaW5lYXItZ3JhZGllbnQoMTM1ZGVnLCMyZjZiZmYsIzViOGNmZik7IC0tYnRuYmc6IzJm"
    "NmJmZjsKfQoqe2JveC1zaXppbmc6Ym9yZGVyLWJveDstd2Via2l0LXRhcC1oaWdobGlnaHQtY29s"
    "b3I6dHJhbnNwYXJlbnR9Cmh0bWwsYm9keXttYXJnaW46MDtwYWRkaW5nOjB9CmJvZHl7YmFja2dy"
    "b3VuZDp2YXIoLS1iZyk7Y29sb3I6dmFyKC0tdHgpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2Zv"
    "bnQtc2l6ZToxNXB4O2xpbmUtaGVpZ2h0OjEuNDU7bWluLWhlaWdodDoxMDB2aDtvdmVyZmxvdy14"
    "OmhpZGRlbjtwYWRkaW5nLXRvcDozMnB4O30KYm9keTpiZWZvcmV7Y29udGVudDoiIjtwb3NpdGlv"
    "bjpmaXhlZDtpbnNldDowO3BvaW50ZXItZXZlbnRzOm5vbmU7ei1pbmRleDowO30KOjotd2Via2l0"
    "LXNjcm9sbGJhcnt3aWR0aDowO2hlaWdodDowfQojc2hlbGx7cG9zaXRpb246cmVsYXRpdmU7ei1p"
    "bmRleDoxO21heC13aWR0aDo1MjBweDttYXJnaW46MCBhdXRvO21pbi1oZWlnaHQ6MTAwdmg7cGFk"
    "ZGluZzowIDE2cHggMTEycHh9Ci5oaWRkZW57ZGlzcGxheTpub25lIWltcG9ydGFudH0KCiN0b3Bi"
    "YXJ7cG9zaXRpb246c3RpY2t5O3RvcDozMnB4O3otaW5kZXg6NDA7ZGlzcGxheTpmbGV4O2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjE0cHggMCAxMnB4O2JhY2tncm91bmQ6bGlu"
    "ZWFyLWdyYWRpZW50KHZhcigtLWJnKSA3MiUsdHJhbnNwYXJlbnQpfQoubG9nb3t3aWR0aDozOHB4"
    "O2hlaWdodDozOHB4O2JvcmRlci1yYWRpdXM6MTJweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6"
    "Y2VudGVyO2ZsZXg6MCAwIGF1dG87YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6I2Zm"
    "ZmZmZjtmb250LXdlaWdodDo4MDA7Zm9udC1zaXplOi45cmVtO2xldHRlci1zcGFjaW5nOi0uMDNl"
    "bTt9Ci5icmFuZHttaW4td2lkdGg6MDtmbGV4OjF9Ci5icmFuZCBie2Rpc3BsYXk6YmxvY2s7Zm9u"
    "dC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZTouOTVyZW07Zm9udC13ZWlnaHQ6ODAwO2xl"
    "dHRlci1zcGFjaW5nOi0uMDNlbTt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3Rl"
    "eHQtb3ZlcmZsb3c6ZWxsaXBzaXN9Ci5icmFuZCBzcGFue2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXpl"
    "Oi42NnJlbTtjb2xvcjp2YXIoLS10eDIpO2xldHRlci1zcGFjaW5nOi4wNmVtO3RleHQtdHJhbnNm"
    "b3JtOnVwcGVyY2FzZX0KLmJhbGNoaXB7ZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpj"
    "ZW50ZXI7Z2FwOjdweDtoZWlnaHQ6MzhweDtwYWRkaW5nOjAgMTNweDtib3JkZXItcmFkaXVzOjEy"
    "cHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2Nv"
    "bG9yOnZhcigtLXR4KTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXdlaWdodDo3MDA7Zm9u"
    "dC1zaXplOi44NXJlbTt3aGl0ZS1zcGFjZTpub3dyYXA7Y3Vyc29yOnBvaW50ZXI7fQouYmFsY2hp"
    "cCAucGx1c3tkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO3dpZHRoOjIwcHg7aGVpZ2h0"
    "OjIwcHg7Ym9yZGVyLXJhZGl1czo3cHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6"
    "I2ZmZmZmZjtmb250LXdlaWdodDo4MDA7Zm9udC1zaXplOi44cmVtO2xpbmUtaGVpZ2h0OjF9Cgou"
    "Y2FyZHtiYWNrZ3JvdW5kOnZhcigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7"
    "Ym9yZGVyLXJhZGl1czp2YXIoLS1yKTtwYWRkaW5nOjE2cHg7bWFyZ2luLWJvdHRvbToxMnB4O2Jv"
    "eC1zaGFkb3c6dmFyKC0tc2hhZG93KTt9Ci5jYXJkLnRpZ2h0e3BhZGRpbmc6MTNweH0KLmNhcmQg"
    "aDN7bWFyZ2luOjAgMCAzcHg7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZTouOTJy"
    "ZW07Zm9udC13ZWlnaHQ6ODAwO2xldHRlci1zcGFjaW5nOi0uMDNlbX0KLmNhcmQgLnN1YnttYXJn"
    "aW46MDtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNzhyZW19Ci5zZWN7ZGlzcGxheTpmbGV4"
    "O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjtnYXA6MTBw"
    "eDttYXJnaW46MThweCAycHggMTBweH0KLnNlYyBoMnttYXJnaW46MDtmb250LWZhbWlseTp2YXIo"
    "LS1maGVhZCk7Zm9udC1zaXplOi44MnJlbTtmb250LXdlaWdodDo4MDA7bGV0dGVyLXNwYWNpbmc6"
    "LjE0ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2NvbG9yOnZhcigtLXR4Mil9Ci5zZWMgYXtm"
    "b250LXNpemU6Ljc1cmVtO2NvbG9yOnZhcigtLWFjYyk7dGV4dC1kZWNvcmF0aW9uOm5vbmU7Zm9u"
    "dC13ZWlnaHQ6NjAwO3doaXRlLXNwYWNlOm5vd3JhcDtjdXJzb3I6cG9pbnRlcn0KCi8qIG9uZSBi"
    "dXR0b24gc3lzdGVtIOKAlCB0ZXh0IGNhbiBuZXZlciBvdmVyZmxvdyAqLwouYnRue2Rpc3BsYXk6"
    "aW5saW5lLWZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Z2Fw"
    "OjdweDtoZWlnaHQ6dmFyKC0tYnRuaCk7bWluLXdpZHRoOjA7bWF4LXdpZHRoOjEwMCU7cGFkZGlu"
    "ZzowIDE2cHg7Ym9yZGVyOjA7Y3Vyc29yOnBvaW50ZXI7Ym9yZGVyLXJhZGl1czoxNHB4O2ZvbnQt"
    "ZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouODNyZW07Zm9udC13ZWlnaHQ6NzAwO2xldHRl"
    "ci1zcGFjaW5nOi4wMWVtO3doaXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4dC1v"
    "dmVyZmxvdzplbGxpcHNpcztsaW5lLWhlaWdodDoxO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQp"
    "O2NvbG9yOiNmZmZmZmY7dHJhbnNpdGlvbjp0cmFuc2Zvcm0gLjE1cyxmaWx0ZXIgLjE1czt9Ci5i"
    "dG46YWN0aXZle3RyYW5zZm9ybTpzY2FsZSguOTcpfQouYnRuOmhvdmVye2ZpbHRlcjpicmlnaHRu"
    "ZXNzKDEuMDYpfQouYnRuIHN2Z3t3aWR0aDoxNnB4O2hlaWdodDoxNnB4O2ZsZXg6MCAwIGF1dG99"
    "Ci5idG4uYmxvY2t7ZGlzcGxheTpmbGV4O3dpZHRoOjEwMCV9Ci5idG4uc217aGVpZ2h0OjM2cHg7"
    "cGFkZGluZzowIDEycHg7Zm9udC1zaXplOi43NXJlbTtib3JkZXItcmFkaXVzOjExcHh9Ci5idG4u"
    "eHN7aGVpZ2h0OjMwcHg7cGFkZGluZzowIDEwcHg7Zm9udC1zaXplOi43cmVtO2JvcmRlci1yYWRp"
    "dXM6MTFweH0KLmJ0bi5naG9zdHtiYWNrZ3JvdW5kOnRyYW5zcGFyZW50O2NvbG9yOnZhcigtLXR4"
    "KTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouYnRuLnNvZnR7YmFja2dyb3VuZDp2YXIo"
    "LS1jYXJkMik7Y29sb3I6dmFyKC0tdHgpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSl9Ci5i"
    "dG4uYmFke2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7Y29sb3I6dmFyKC0tYmFkKTtib3JkZXI6MXB4"
    "IHNvbGlkIHZhcigtLWJhZCl9Ci5idG5bZGlzYWJsZWRde29wYWNpdHk6LjQ1O3BvaW50ZXItZXZl"
    "bnRzOm5vbmV9Ci5idG5yb3d7ZGlzcGxheTpmbGV4O2dhcDo4cHh9Ci5idG5yb3c+LmJ0bntmbGV4"
    "OjEgMSAwO21pbi13aWR0aDowfQoKLnBpbGx7ZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7Z2FwOjVweDtoZWlnaHQ6MjRweDtwYWRkaW5nOjAgOXB4O2JvcmRlci1yYWRpdXM6"
    "OTlweDtmb250LXNpemU6LjY2cmVtO2ZvbnQtd2VpZ2h0OjcwMDtsZXR0ZXItc3BhY2luZzouMDRl"
    "bTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIpO2JvcmRlcjoxcHggc29s"
    "aWQgdmFyKC0tbGluZSk7d2hpdGUtc3BhY2U6bm93cmFwfQoucGlsbC5vbntjb2xvcjp2YXIoLS1v"
    "ayk7Ym9yZGVyLWNvbG9yOnJnYmEoMTgsMTY1LDEwNiwuNCl9Ci5waWxsLmhvdHtjb2xvcjp2YXIo"
    "LS13YXJuKTtib3JkZXItY29sb3I6cmdiYSgyMjQsMTM4LDAsLjQpfQoucGlsbC5vZmZ7Y29sb3I6"
    "dmFyKC0tYmFkKTtib3JkZXItY29sb3I6cmdiYSgyMjYsNjgsOTIsLjQpfQouZG90e3dpZHRoOjdw"
    "eDtoZWlnaHQ6N3B4O2JvcmRlci1yYWRpdXM6NTAlO2JhY2tncm91bmQ6dmFyKC0tb2spO2JveC1z"
    "aGFkb3c6MCAwIDhweCB2YXIoLS1vayl9Ci5rdntkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2Vu"
    "dGVyO2p1c3RpZnktY29udGVudDpzcGFjZS1iZXR3ZWVuO2dhcDoxMnB4O3BhZGRpbmc6MTBweCAw"
    "O2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxpbmUpO2ZvbnQtc2l6ZTouODJyZW07Y29s"
    "b3I6dmFyKC0tdHgyKX0KLmt2Omxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRvbTowfQoua3YgYntjb2xv"
    "cjp2YXIoLS10eCk7Zm9udC13ZWlnaHQ6NzAwO3RleHQtYWxpZ246cmlnaHQ7b3ZlcmZsb3ctd3Jh"
    "cDphbnl3aGVyZX0KLmZsYWd7d2lkdGg6NDJweDtoZWlnaHQ6NDJweDtib3JkZXItcmFkaXVzOjEy"
    "cHg7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtmb250LXNpemU6MS4zNXJlbTtmbGV4"
    "OjAgMCBhdXRvO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2JvcmRlcjoxcHggc29saWQgdmFyKC0t"
    "bGluZSl9Ci5mbGFnIHN2Z3t3aWR0aDoxOHB4O2hlaWdodDoxOHB4fQoKLmhlcm97cG9zaXRpb246"
    "cmVsYXRpdmU7b3ZlcmZsb3c6aGlkZGVuO2JvcmRlci1yYWRpdXM6MjRweDtwYWRkaW5nOjIycHgg"
    "MThweDttYXJnaW46NHB4IDAgMTRweDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tn"
    "cm91bmQ6bGluZWFyLWdyYWRpZW50KDE0MGRlZywjZWVmM2ZmLCNmZmZmZmYpO2JvcmRlci1jb2xv"
    "cjojZTNlYWZmfQouaGVybyAua2lja3tmb250LXNpemU6LjZyZW07bGV0dGVyLXNwYWNpbmc6LjJl"
    "bTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7Y29sb3I6dmFyKC0tYWNjKTtmb250LXdlaWdodDo4"
    "MDB9Ci5oZXJvIGgxe21hcmdpbjo4cHggMCA2cHg7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2Zv"
    "bnQtc2l6ZToxLjc1cmVtO2xpbmUtaGVpZ2h0OjEuMDU7bGV0dGVyLXNwYWNpbmc6LS4wM2VtO2Zv"
    "bnQtd2VpZ2h0OjgwMH0KLmhlcm8gcHttYXJnaW46MCAwIDE0cHg7Zm9udC1zaXplOi44cmVtO2Nv"
    "bG9yOnZhcigtLXR4Mik7bWF4LXdpZHRoOjI4MHB4fQouaGVybyAuYnRucm93e21heC13aWR0aDoz"
    "MzBweH0KCi5ncmlke2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyIDFmcjtn"
    "YXA6MTBweH0KLnBjYXJke2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2"
    "YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXIpO3BhZGRpbmc6MTRweDtib3gtc2hhZG93"
    "OnZhcigtLXNoYWRvdyk7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjtnYXA6OXB4"
    "O2N1cnNvcjpwb2ludGVyO30KLnBjYXJkIC50b3B7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNl"
    "bnRlcjtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjtnYXA6OHB4fQoucGNhcmQgLm5te2Zv"
    "bnQtd2VpZ2h0OjcwMDtmb250LXNpemU6Ljg1cmVtO3doaXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxv"
    "dzpoaWRkZW47dGV4dC1vdmVyZmxvdzplbGxpcHNpc30KLnBjYXJkIC5tZXRhe2ZvbnQtc2l6ZTou"
    "NjhyZW07Y29sb3I6dmFyKC0tdHgyKX0KLnBjYXJkIC5wcmljZXtmb250LWZhbWlseTp2YXIoLS1m"
    "aGVhZCk7Zm9udC1zaXplOjEuMjVyZW07Zm9udC13ZWlnaHQ6ODAwO2xldHRlci1zcGFjaW5nOi0u"
    "MDJlbX0KLnBjYXJkIC5wcmljZSBze2ZvbnQtc2l6ZTouNzJyZW07Y29sb3I6dmFyKC0tdHgyKTtm"
    "b250LXdlaWdodDo0MDA7bWFyZ2luLXJpZ2h0OjZweH0KLnJvd3N7ZGlzcGxheTpmbGV4O2ZsZXgt"
    "ZGlyZWN0aW9uOmNvbHVtbjtnYXA6OXB4fQoucm93e2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpj"
    "ZW50ZXI7Z2FwOjEycHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZh"
    "cigtLWxpbmUpO2JvcmRlci1yYWRpdXM6dmFyKC0tcik7cGFkZGluZzoxMnB4IDEzcHg7Ym94LXNo"
    "YWRvdzp2YXIoLS1zaGFkb3cpO2N1cnNvcjpwb2ludGVyO3RyYW5zaXRpb246dHJhbnNmb3JtIC4x"
    "NXM7fQoucm93OmFjdGl2ZXt0cmFuc2Zvcm06c2NhbGUoLjk5KX0KLnJvdyAubWlke2ZsZXg6MTtt"
    "aW4td2lkdGg6MH0KLnJvdyAubWlkIGJ7ZGlzcGxheTpibG9jaztmb250LXNpemU6Ljg4cmVtO3do"
    "aXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4dC1vdmVyZmxvdzplbGxpcHNpc30K"
    "LnJvdyAubWlkIHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi43cmVtO2NvbG9yOnZhcigt"
    "LXR4Mik7bWFyZ2luLXRvcDoycHh9Ci5yb3cgLnJ0e3RleHQtYWxpZ246cmlnaHQ7ZmxleDowIDAg"
    "YXV0b30KLnJvdyAucnQgLnB7Zm9udC13ZWlnaHQ6ODAwO2ZvbnQtc2l6ZTouOTVyZW07d2hpdGUt"
    "c3BhY2U6bm93cmFwfQoucm93IC5ydCAucCBze2ZvbnQtd2VpZ2h0OjQwMDtmb250LXNpemU6Ljcy"
    "cmVtO2NvbG9yOnZhcigtLXR4Mik7bWFyZ2luLXJpZ2h0OjVweH0KLmNhcm91e2Rpc3BsYXk6Zmxl"
    "eDtnYXA6MTBweDtvdmVyZmxvdy14OmF1dG87cGFkZGluZzoycHggMCA2cHg7bWFyZ2luOjAgLTE2"
    "cHggNnB4O3BhZGRpbmctbGVmdDoxNnB4O3BhZGRpbmctcmlnaHQ6MTZweDtzY3JvbGwtc25hcC10"
    "eXBlOnggbWFuZGF0b3J5fQouY2Fyb3U+KntmbGV4OjAgMCAxNzJweDtzY3JvbGwtc25hcC1hbGln"
    "bjpzdGFydH0KCm5hdiN0YWJze3Bvc2l0aW9uOmZpeGVkO3otaW5kZXg6NjA7bGVmdDo1MCU7Ym90"
    "dG9tOmNhbGMoMTJweCArIGVudihzYWZlLWFyZWEtaW5zZXQtYm90dG9tKSk7dHJhbnNmb3JtOnRy"
    "YW5zbGF0ZVgoLTUwJSk7bWF4LXdpZHRoOmNhbGMoMTAwdncgLSAyMHB4KX0KbmF2I3RhYnMgLmRv"
    "Y2t7ZGlzcGxheTpmbGV4O2dhcDoycHg7cGFkZGluZzo3cHggOHB4O2JvcmRlci1yYWRpdXM6MjRw"
    "eDtiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsLjkyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigt"
    "LWxpbmUpO2JhY2tkcm9wLWZpbHRlcjpibHVyKDE2cHgpO2JveC1zaGFkb3c6MCA4cHggMzBweCBy"
    "Z2JhKDIwLDI1LDQ1LC4xMil9Cm5hdiN0YWJzIGJ1dHRvbntwb3NpdGlvbjpyZWxhdGl2ZTtkaXNw"
    "bGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1uO2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5"
    "LWNvbnRlbnQ6Y2VudGVyO2dhcDo0cHg7bWluLXdpZHRoOjU4cHg7aGVpZ2h0OjQ4cHg7cGFkZGlu"
    "ZzowIDZweDtib3JkZXI6MDtiYWNrZ3JvdW5kOnRyYW5zcGFyZW50O2N1cnNvcjpwb2ludGVyO2Nv"
    "bG9yOnZhcigtLXR4Mik7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi42cmVtO2Zv"
    "bnQtd2VpZ2h0OjcwMDtsZXR0ZXItc3BhY2luZzouMDNlbTtib3JkZXItcmFkaXVzOjE2cHg7dHJh"
    "bnNpdGlvbjpjb2xvciAuMnMsYmFja2dyb3VuZCAuMnM7d2hpdGUtc3BhY2U6bm93cmFwfQpuYXYj"
    "dGFicyBidXR0b24gc3Zne3dpZHRoOjE5cHg7aGVpZ2h0OjE5cHh9Cm5hdiN0YWJzIGJ1dHRvbi5v"
    "bntjb2xvcjojZmZmZmZmO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpfQoKLnN1Ym5hdntkaXNw"
    "bGF5OmZsZXg7Z2FwOjdweDtvdmVyZmxvdy14OmF1dG87cGFkZGluZzoycHggMCAxMnB4O21hcmdp"
    "bjowIC0ycHh9Ci5zdWJuYXYgYnV0dG9ue2ZsZXg6MCAwIGF1dG87aGVpZ2h0OjM0cHg7cGFkZGlu"
    "ZzowIDEzcHg7Ym9yZGVyLXJhZGl1czoxMXB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7"
    "YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgyKTtmb250LWZhbWlseTp2YXIo"
    "LS1mb250KTtmb250LXNpemU6Ljc0cmVtO2ZvbnQtd2VpZ2h0OjcwMDtjdXJzb3I6cG9pbnRlcjt3"
    "aGl0ZS1zcGFjZTpub3dyYXB9Ci5zdWJuYXYgYnV0dG9uLm9ue2JhY2tncm91bmQ6dmFyKC0tcGls"
    "bGdyYWQpO2NvbG9yOiNmZmZmZmY7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50fQoua3Bpc3tkaXNw"
    "bGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjFmciAxZnI7Z2FwOjEwcHg7bWFyZ2luLWJv"
    "dHRvbToxMnB4fQoua3Bpe2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2"
    "YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXJzKTtwYWRkaW5nOjEzcHg7Ym94LXNoYWRv"
    "dzp2YXIoLS1zaGFkb3cpfQoua3BpIHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42NHJl"
    "bTtjb2xvcjp2YXIoLS10eDIpO2xldHRlci1zcGFjaW5nOi4xZW07dGV4dC10cmFuc2Zvcm06dXBw"
    "ZXJjYXNlfQoua3BpIGJ7ZGlzcGxheTpibG9jazttYXJnaW4tdG9wOjVweDtmb250LWZhbWlseTp2"
    "YXIoLS1maGVhZCk7Zm9udC1zaXplOjEuMzVyZW07Zm9udC13ZWlnaHQ6ODAwO2xldHRlci1zcGFj"
    "aW5nOi0uMDJlbX0KLmlucCxzZWxlY3QuaW5we3dpZHRoOjEwMCU7aGVpZ2h0OnZhcigtLWJ0bmgp"
    "O3BhZGRpbmc6MCAxM3B4O2JvcmRlci1yYWRpdXM6MTRweDtib3JkZXI6MXB4IHNvbGlkIHZhcigt"
    "LWxpbmUpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4KTtmb250LWZhbWls"
    "eTp2YXIoLS1mb250KTtmb250LXNpemU6LjgzcmVtO291dGxpbmU6bm9uZTstd2Via2l0LWFwcGVh"
    "cmFuY2U6bm9uZTthcHBlYXJhbmNlOm5vbmV9CnNlbGVjdC5pbnB7YmFja2dyb3VuZC1pbWFnZTpu"
    "b25lfQouaW5wOjpwbGFjZWhvbGRlcntjb2xvcjp2YXIoLS10eDIpO29wYWNpdHk6Ljc1fQouaW5w"
    "OmZvY3Vze2JvcmRlci1jb2xvcjp2YXIoLS1hY2MpfQp0ZXh0YXJlYS5pbnB7aGVpZ2h0OmF1dG87"
    "bWluLWhlaWdodDo0MHB4O3BhZGRpbmc6MTBweCAxM3B4O2xpbmUtaGVpZ2h0OjEuNTtyZXNpemU6"
    "dmVydGljYWx9Ci5jbXNyb3cuaGlkZXtkaXNwbGF5Om5vbmV9Ci5pY29uY2hpcHtwb3NpdGlvbjpy"
    "ZWxhdGl2ZTtoZWlnaHQ6MzRweDttaW4td2lkdGg6MzRweDtwYWRkaW5nOjAgOXB4O2JvcmRlci1y"
    "YWRpdXM6OTlweDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6dmFyKC0t"
    "Y2FyZDIpO2NvbG9yOnZhcigtLXR4KTtkaXNwbGF5OmlubGluZS1ncmlkO3BsYWNlLWl0ZW1zOmNl"
    "bnRlcjtjdXJzb3I6cG9pbnRlcn0KLmljb25jaGlwIHN2Z3t3aWR0aDoxN3B4O2hlaWdodDoxN3B4"
    "fQouaWNvbmNoaXAgaXtwb3NpdGlvbjphYnNvbHV0ZTt0b3A6LTVweDtyaWdodDotNXB4O21pbi13"
    "aWR0aDoxN3B4O2hlaWdodDoxN3B4O3BhZGRpbmc6MCA0cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2Jh"
    "Y2tncm91bmQ6dmFyKC0tYWNjKTtjb2xvcjojZmZmZmZmO2ZvbnQtc2l6ZTouNnJlbTtmb250LXN0"
    "eWxlOm5vcm1hbDtmb250LXdlaWdodDo4MDA7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRl"
    "cn0KLnN0YXJze2Rpc3BsYXk6aW5saW5lLWZsZXg7Z2FwOjFweDt2ZXJ0aWNhbC1hbGlnbjptaWRk"
    "bGV9Ci5zdGFycyBzdmd7d2lkdGg6MTFweDtoZWlnaHQ6MTFweDtjb2xvcjojZjVhNjIzfQouc3Rh"
    "cnMgYntmb250LXNpemU6LjY2cmVtO2NvbG9yOnZhcigtLXR4Mik7Zm9udC13ZWlnaHQ6NzAwO21h"
    "cmdpbi1sZWZ0OjNweH0KLmhlYXJ0e3Bvc2l0aW9uOmFic29sdXRlO3RvcDo4cHg7cmlnaHQ6OHB4"
    "O3dpZHRoOjI2cHg7aGVpZ2h0OjI2cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2Rpc3BsYXk6Z3JpZDtw"
    "bGFjZS1pdGVtczpjZW50ZXI7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5k"
    "OnZhcigtLWNhcmQyKTtjdXJzb3I6cG9pbnRlcjt6LWluZGV4OjJ9Ci5oZWFydCBzdmd7d2lkdGg6"
    "MTNweDtoZWlnaHQ6MTNweDtjb2xvcjp2YXIoLS10eDIpfQouaGVhcnQub257YmFja2dyb3VuZDp2"
    "YXIoLS1hY2MpO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVudH0KLmhlYXJ0Lm9uIHN2Z3tjb2xvcjoj"
    "ZmZmZmZmfQoucXR5e2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjA7Ym9yZGVy"
    "OjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOjE0cHg7b3ZlcmZsb3c6aGlkZGVu"
    "fQoucXR5IGJ1dHRvbnt3aWR0aDozOHB4O2hlaWdodDozOHB4O2JvcmRlcjowO2JhY2tncm91bmQ6"
    "dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4KTtmb250LXNpemU6MXJlbTtmb250LXdlaWdodDo4"
    "MDA7Y3Vyc29yOnBvaW50ZXJ9Ci5xdHkgYnttaW4td2lkdGg6MzhweDt0ZXh0LWFsaWduOmNlbnRl"
    "cjtmb250LXNpemU6Ljg1cmVtfQoudGx7bWFyZ2luOjEwcHggMCAwO3BhZGRpbmctbGVmdDoxNnB4"
    "O2JvcmRlci1sZWZ0OjJweCBzb2xpZCB2YXIoLS1saW5lKX0KLnRsIGRpdntwb3NpdGlvbjpyZWxh"
    "dGl2ZTtwYWRkaW5nOjAgMCAxMXB4IDEwcHg7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10"
    "eDIpfQoudGwgZGl2Omxhc3QtY2hpbGR7cGFkZGluZy1ib3R0b206MH0KLnRsIGRpdjo6YmVmb3Jl"
    "e2NvbnRlbnQ6IiI7cG9zaXRpb246YWJzb2x1dGU7bGVmdDotMjFweDt0b3A6NHB4O3dpZHRoOjhw"
    "eDtoZWlnaHQ6OHB4O2JvcmRlci1yYWRpdXM6OTlweDtiYWNrZ3JvdW5kOnZhcigtLWxpbmUpfQou"
    "dGwgZGl2Lm9rOjpiZWZvcmV7YmFja2dyb3VuZDp2YXIoLS1vayl9Ci50bCBkaXYub2sgYntjb2xv"
    "cjp2YXIoLS10eCl9Ci50bCBzbWFsbHtkaXNwbGF5OmJsb2NrO2ZvbnQtc2l6ZTouNjZyZW07b3Bh"
    "Y2l0eTouOH0KLmZjYXJke2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpmbGV4LXN0YXJ0O2dhcDox"
    "MXB4O3BhZGRpbmc6MTFweCAwO2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxpbmUpfQou"
    "ZmNhcmQ6bGFzdC1jaGlsZHtib3JkZXItYm90dG9tOjB9Ci5mY2FyZCAudHtmbGV4OjE7bWluLXdp"
    "ZHRoOjB9Ci5mY2FyZCAudCBie2ZvbnQtc2l6ZTouODFyZW07ZGlzcGxheTpibG9ja30KLmZjYXJk"
    "IC50IHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42OXJlbTtjb2xvcjp2YXIoLS10eDIp"
    "O2xpbmUtaGVpZ2h0OjEuNDU7bWFyZ2luLXRvcDoycHh9Ci5mcm93LmhpZGUsLmZncnAuaGlkZXtk"
    "aXNwbGF5Om5vbmV9Ci5iYXJze2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpmbGV4LWVuZDtnYXA6"
    "NXB4O2hlaWdodDo4NnB4O21hcmdpbi10b3A6MTJweH0KLmJhcnMgaXtmbGV4OjE7Ym9yZGVyLXJh"
    "ZGl1czo2cHggNnB4IDNweCAzcHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7ZGlzcGxheTpi"
    "bG9ja30KLmZ1bm5lbCBkaXZ7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6OXB4"
    "O21hcmdpbi10b3A6OXB4O2ZvbnQtc2l6ZTouNzRyZW19Ci5mdW5uZWwgaXtoZWlnaHQ6MjZweDti"
    "b3JkZXItcmFkaXVzOjdweDtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtkaXNwbGF5OmJsb2Nr"
    "fQouZmVlZGxpbmV7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6OXB4O2ZvbnQt"
    "c2l6ZTouNzNyZW07Y29sb3I6dmFyKC0tdHgyKTtwYWRkaW5nOjdweCAwO2JvcmRlci1ib3R0b206"
    "MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouZmVlZGxpbmU6bGFzdC1jaGlsZHtib3JkZXItYm90dG9t"
    "OjB9Ci5mZWVkbGluZSBie2NvbG9yOnZhcigtLXR4KX0KLmxie2Rpc3BsYXk6ZmxleDthbGlnbi1p"
    "dGVtczpjZW50ZXI7Z2FwOjEwcHg7cGFkZGluZzo5cHggMDtib3JkZXItYm90dG9tOjFweCBzb2xp"
    "ZCB2YXIoLS1saW5lKX0KLmxiOmxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRvbTowfQoubGIgLnJre3dp"
    "ZHRoOjI0cHg7aGVpZ2h0OjI0cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2Rpc3BsYXk6Z3JpZDtwbGFj"
    "ZS1pdGVtczpjZW50ZXI7Zm9udC1zaXplOi43cmVtO2ZvbnQtd2VpZ2h0OjgwMDtiYWNrZ3JvdW5k"
    "OnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIpfQoubGIgLnJrLmd7YmFja2dyb3VuZDp2YXIo"
    "LS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmZmZn0KLmNtc2dycC5oaWRle2Rpc3BsYXk6bm9uZX0KLmNt"
    "c3JvdyBsYWJlbCBie2NvbG9yOnZhcigtLWFjYyk7Zm9udC13ZWlnaHQ6NzAwfQoudGltZXJiYXJ7"
    "aGVpZ2h0OjVweDtib3JkZXItcmFkaXVzOjk5cHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7b3Zl"
    "cmZsb3c6aGlkZGVuO21hcmdpbjoxMHB4IDAgMnB4fQoudGltZXJiYXIgaXtkaXNwbGF5OmJsb2Nr"
    "O2hlaWdodDoxMDAlO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO3RyYW5zaXRpb246d2lkdGgg"
    "MXMgbGluZWFyfQoudGltZXJiYXIubG93IGl7YmFja2dyb3VuZDp2YXIoLS1iYWQsI2U1NDg0ZCl9"
    "Ci5maWVsZHttYXJnaW4tYm90dG9tOjlweH0KLmZpZWxkIGxhYmVse2Rpc3BsYXk6YmxvY2s7Zm9u"
    "dC1zaXplOi42OHJlbTtjb2xvcjp2YXIoLS10eDIpO2xldHRlci1zcGFjaW5nOi4wOWVtO3RleHQt"
    "dHJhbnNmb3JtOnVwcGVyY2FzZTttYXJnaW46MCAwIDVweCAycHg7Zm9udC13ZWlnaHQ6NzAwfQou"
    "c3dhcHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpzcGFj"
    "ZS1iZXR3ZWVuO2dhcDoxMnB4O3BhZGRpbmc6MTFweCAwO2JvcmRlci1ib3R0b206MXB4IHNvbGlk"
    "IHZhcigtLWxpbmUpfQouc3dhcDpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206MH0KLnN3YXAgLnR7"
    "Zm9udC1zaXplOi44MnJlbTtmb250LXdlaWdodDo2MDA7bWluLXdpZHRoOjB9Ci5zd2FwIC50IHNt"
    "YWxse2Rpc3BsYXk6YmxvY2s7Zm9udC13ZWlnaHQ6NDAwO2ZvbnQtc2l6ZTouN3JlbTtjb2xvcjp2"
    "YXIoLS10eDIpfQoudGd7d2lkdGg6NDRweDtoZWlnaHQ6MjZweDtib3JkZXItcmFkaXVzOjk5cHg7"
    "YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtwb3Np"
    "dGlvbjpyZWxhdGl2ZTtjdXJzb3I6cG9pbnRlcjtmbGV4OjAgMCBhdXRvfQoudGc6YWZ0ZXJ7Y29u"
    "dGVudDoiIjtwb3NpdGlvbjphYnNvbHV0ZTt0b3A6M3B4O2xlZnQ6M3B4O3dpZHRoOjE4cHg7aGVp"
    "Z2h0OjE4cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3VuZDp2YXIoLS10eDIpO3RyYW5zaXRp"
    "b246LjIyc30KLnRnLm9ue2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2JvcmRlci1jb2xvcjp0"
    "cmFuc3BhcmVudH0KLnRnLm9uOmFmdGVye2xlZnQ6MjFweDtiYWNrZ3JvdW5kOiNmZmZmZmZ9Ci50"
    "Zy5zbXt3aWR0aDozNnB4O2hlaWdodDoyMXB4fQoudGcuc206YWZ0ZXJ7d2lkdGg6MTRweDtoZWln"
    "aHQ6MTRweDt0b3A6Mi41cHh9Ci50Zy5zbS5vbjphZnRlcntsZWZ0OjE4cHh9Ci5zdGFye3dpZHRo"
    "OjMwcHg7aGVpZ2h0OjMwcHg7Ym9yZGVyLXJhZGl1czo5cHg7ZGlzcGxheTpncmlkO3BsYWNlLWl0"
    "ZW1zOmNlbnRlcjtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6dmFyKC0t"
    "Y2FyZDIpO2NvbG9yOnZhcigtLXR4Mik7Y3Vyc29yOnBvaW50ZXI7ZmxleDowIDAgYXV0b30KLnN0"
    "YXIub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmZmZjtib3JkZXItY29s"
    "b3I6dHJhbnNwYXJlbnR9Ci5zdGFyIHN2Z3t3aWR0aDoxNHB4O2hlaWdodDoxNHB4fQoKI3NoZWV0"
    "e3Bvc2l0aW9uOmZpeGVkO2luc2V0OjA7ei1pbmRleDo5MDtkaXNwbGF5Om5vbmV9CiNzaGVldC5v"
    "cGVue2Rpc3BsYXk6YmxvY2t9CiNzaGVldCAuYmd7cG9zaXRpb246YWJzb2x1dGU7aW5zZXQ6MDti"
    "YWNrZ3JvdW5kOnJnYmEoMjAsMjQsNDAsLjM1KTtiYWNrZHJvcC1maWx0ZXI6Ymx1cigzcHgpfQoj"
    "c2hlZXQgLnBhbmVse3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6NTAlO2JvdHRvbTowO3RyYW5zZm9y"
    "bTp0cmFuc2xhdGVYKC01MCUpO3dpZHRoOjEwMCU7bWF4LXdpZHRoOjUyMHB4O2JhY2tncm91bmQ6"
    "dmFyKC0tYmcyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1ib3R0b206MDti"
    "b3JkZXItcmFkaXVzOjI0cHggMjRweCAwIDA7cGFkZGluZzoxOHB4IDE2cHggY2FsYygyMHB4ICsg"
    "ZW52KHNhZmUtYXJlYS1pbnNldC1ib3R0b20pKTthbmltYXRpb246dXAgLjI4cyBjdWJpYy1iZXpp"
    "ZXIoLjIsLjksLjMsMSk7bWF4LWhlaWdodDo5MnZoO292ZXJmbG93OmF1dG99CkBrZXlmcmFtZXMg"
    "dXB7ZnJvbXt0cmFuc2Zvcm06dHJhbnNsYXRlKC01MCUsMTAwJSl9dG97dHJhbnNmb3JtOnRyYW5z"
    "bGF0ZSgtNTAlLDApfX0KI3NoZWV0IC5ncmFie3dpZHRoOjM4cHg7aGVpZ2h0OjRweDtib3JkZXIt"
    "cmFkaXVzOjlweDtiYWNrZ3JvdW5kOnZhcigtLWxpbmUpO21hcmdpbjowIGF1dG8gMTRweH0KLm90"
    "cGJveHtmb250LWZhbWlseTp1aS1tb25vc3BhY2UsTWVubG8sQ29uc29sYXMsbW9ub3NwYWNlO2Zv"
    "bnQtc2l6ZToxLjZyZW07bGV0dGVyLXNwYWNpbmc6LjM1ZW07dGV4dC1hbGlnbjpjZW50ZXI7cGFk"
    "ZGluZzoxNnB4O2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIp"
    "O2JvcmRlcjoxcHggZGFzaGVkIHZhcigtLWxpbmUpO21hcmdpbjoxMHB4IDB9Ci5xcnt3aWR0aDox"
    "NTBweDtoZWlnaHQ6MTUwcHg7bWFyZ2luOjZweCBhdXRvIDEwcHg7Ym9yZGVyLXJhZGl1czp2YXIo"
    "LS1ycyk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5l"
    "KTtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2NvbG9yOnZhcigtLXR4Mik7Zm9udC1z"
    "aXplOi42NXJlbTtsZXR0ZXItc3BhY2luZzouMWVtO3RleHQtYWxpZ246Y2VudGVyfQouc3RlcHN7"
    "bWFyZ2luOjEycHggMCA0cHh9Ci5zdGVwe2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7"
    "Z2FwOjEwcHg7cGFkZGluZzo4cHggMDtmb250LXNpemU6Ljc5cmVtO2NvbG9yOnZhcigtLXR4Mil9"
    "Ci5zdGVwIGl7d2lkdGg6MjJweDtoZWlnaHQ6MjJweDtib3JkZXItcmFkaXVzOjUwJTtib3JkZXI6"
    "MXB4IHNvbGlkIHZhcigtLWxpbmUpO2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9u"
    "dC1zdHlsZTpub3JtYWw7Zm9udC1zaXplOi42NXJlbTtmb250LXdlaWdodDo4MDA7ZmxleDowIDAg"
    "YXV0b30KLnN0ZXAub257Y29sb3I6dmFyKC0tdHgpfQouc3RlcC5vbiBpe2JhY2tncm91bmQ6dmFy"
    "KC0tcGlsbGdyYWQpO2NvbG9yOiNmZmZmZmY7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50fQouc3Rl"
    "cC5ydW4gaXtib3JkZXItY29sb3I6dmFyKC0tYWNjKTtjb2xvcjp2YXIoLS1hY2MpO2FuaW1hdGlv"
    "bjpwdWxzZSAxcyBpbmZpbml0ZX0KQGtleWZyYW1lcyBwdWxzZXs1MCV7b3BhY2l0eTouMzV9fQou"
    "dG9hc3R7cG9zaXRpb246Zml4ZWQ7bGVmdDo1MCU7Ym90dG9tOmNhbGMoOTZweCArIGVudihzYWZl"
    "LWFyZWEtaW5zZXQtYm90dG9tKSk7dHJhbnNmb3JtOnRyYW5zbGF0ZVgoLTUwJSk7ei1pbmRleDox"
    "MjA7YmFja2dyb3VuZDp2YXIoLS1iZzIpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Y29s"
    "b3I6dmFyKC0tdHgpO3BhZGRpbmc6MTFweCAxNnB4O2JvcmRlci1yYWRpdXM6OTlweDtmb250LXNp"
    "emU6Ljc4cmVtO2ZvbnQtd2VpZ2h0OjYwMDtib3gtc2hhZG93OnZhcigtLXNoYWRvdyk7b3BhY2l0"
    "eTowO3BvaW50ZXItZXZlbnRzOm5vbmU7dHJhbnNpdGlvbjpvcGFjaXR5IC4ycyx0cmFuc2Zvcm0g"
    "LjJzO3doaXRlLXNwYWNlOm5vd3JhcDttYXgtd2lkdGg6OTB2dztvdmVyZmxvdzpoaWRkZW47dGV4"
    "dC1vdmVyZmxvdzplbGxpcHNpc30KLnRvYXN0LnNob3d7b3BhY2l0eToxO3RyYW5zZm9ybTp0cmFu"
    "c2xhdGVYKC01MCUpIHRyYW5zbGF0ZVkoLTZweCl9CgovKiBwcmV2aWV3IGNocm9tZSDigJQgbW9j"
    "a3VwIG9ubHksIG93biBzdHJpcCBzbyBpdCBuZXZlciBvdmVybGFwcyB0aGUgVUkgKi8KI3N3e3Bv"
    "c2l0aW9uOmZpeGVkO3RvcDowO2xlZnQ6MDtyaWdodDowO2hlaWdodDozMnB4O3otaW5kZXg6MjAw"
    "O2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjtn"
    "YXA6NXB4OwogIGJhY2tncm91bmQ6cmdiYSgyNTUsMjU1LDI1NSwuODUpO2JvcmRlci1ib3R0b206"
    "MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tkcm9wLWZpbHRlcjpibHVyKDEwcHgpfQojc3cgZW17"
    "Zm9udC1zdHlsZTpub3JtYWw7Zm9udC1zaXplOi41NnJlbTtsZXR0ZXItc3BhY2luZzouMThlbTt0"
    "ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7Y29sb3I6dmFyKC0tdHgyKTttYXJnaW4tcmlnaHQ6NHB4"
    "fQojc3cgYnV0dG9ue2hlaWdodDoyMnB4O3BhZGRpbmc6MCAxMnB4O2JvcmRlci1yYWRpdXM6OTlw"
    "eDtib3JkZXI6MDtjdXJzb3I6cG9pbnRlcjtiYWNrZ3JvdW5kOnRyYW5zcGFyZW50O2NvbG9yOnZh"
    "cigtLXR4Mik7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi42cmVtO2ZvbnQtd2Vp"
    "Z2h0OjcwMDtsZXR0ZXItc3BhY2luZzouMDdlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2V9CiNz"
    "dyBidXR0b24ub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmZmZn0KCi8q"
    "IOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCBBVVJPUkEgVUkg"
    "S0lUIOKAlCBwb3J0ZWQgY29tcG9uZW50cyDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZAgKi8KQHByb3BlcnR5IC0tZ2F7c3ludGF4OiI8YW5nbGU+Ijtpbml0aWFs"
    "LXZhbHVlOjBkZWc7aW5oZXJpdHM6ZmFsc2V9CkBwcm9wZXJ0eSAtLWdwe3N5bnRheDoiPHBlcmNl"
    "bnRhZ2U+Ijtpbml0aWFsLXZhbHVlOjUlO2luaGVyaXRzOmZhbHNlfQpAcHJvcGVydHkgLS1nb3tz"
    "eW50YXg6IjxhbmdsZT4iO2luaXRpYWwtdmFsdWU6MGRlZztpbmhlcml0czpmYWxzZX0KCi8qIDIz"
    "IMK3IFNoaW55IENUQSDigJQgY29uaWMgYm9yZGVyICsgZG90IG1hc2sgKyBpbm5lciBzaGltbWVy"
    "ICsgYnJlYXRoZSAqLwouYnRuLmN0YXstLWR1cjozLjJzOy0taGw6I2NmZTBmZjtwb3NpdGlvbjpy"
    "ZWxhdGl2ZTtpc29sYXRpb246aXNvbGF0ZTtib3JkZXI6MXB4IHNvbGlkIHRyYW5zcGFyZW50Owog"
    "IGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KHZhcigtLWJ0bmJnKSx2YXIoLS1idG5iZykpIHBh"
    "ZGRpbmctYm94LAogICAgY29uaWMtZ3JhZGllbnQoZnJvbSBjYWxjKHZhcigtLWdhKSAtIHZhcigt"
    "LWdvKSksdHJhbnNwYXJlbnQsdmFyKC0taGwpIHZhcigtLWdwKSwKICAgICAgI2ZmZiBjYWxjKHZh"
    "cigtLWdwKSoyKSx2YXIoLS1obCkgY2FsYyh2YXIoLS1ncCkqMyksdHJhbnNwYXJlbnQgY2FsYyh2"
    "YXIoLS1ncCkqNCkpIGJvcmRlci1ib3g7CiAgYm94LXNoYWRvdzppbnNldCAwIDAgMCAxcHggcmdi"
    "YSgyNTUsMjU1LDI1NSwuMDgpOwogIHRyYW5zaXRpb246LS1nbyAuOHMgY3ViaWMtYmV6aWVyKC4y"
    "NSwxLC41LDEpLC0tZ3AgLjhzIGN1YmljLWJlemllciguMjUsMSwuNSwxKSx0cmFuc2Zvcm0gLjE1"
    "czsKICBhbmltYXRpb246Z2EgdmFyKC0tZHVyKSBsaW5lYXIgaW5maW5pdGV9Ci5idG4uY3RhOmJl"
    "Zm9yZSwuYnRuLmN0YTphZnRlcntjb250ZW50OiIiO3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6NTAl"
    "O3RvcDo1MCU7dHJhbnNsYXRlOi01MCUgLTUwJTt6LWluZGV4Oi0xO3BvaW50ZXItZXZlbnRzOm5v"
    "bmV9Ci5idG4uY3RhOmJlZm9yZXt3aWR0aDpjYWxjKDEwMCUgLSA2cHgpO2hlaWdodDpjYWxjKDEw"
    "MCUgLSA2cHgpO2JvcmRlci1yYWRpdXM6aW5oZXJpdDtvcGFjaXR5Oi4zNTsKICBiYWNrZ3JvdW5k"
    "OnJhZGlhbC1ncmFkaWVudChjaXJjbGUgYXQgMnB4IDJweCwjZmZmIC41cHgsdHJhbnNwYXJlbnQg"
    "MCkgcGFkZGluZy1ib3g7YmFja2dyb3VuZC1zaXplOjRweCA0cHg7CiAgLXdlYmtpdC1tYXNrLWlt"
    "YWdlOmNvbmljLWdyYWRpZW50KGZyb20gY2FsYyh2YXIoLS1nYSkgKyA0NWRlZyksIzAwMCx0cmFu"
    "c3BhcmVudCAxMCUgOTAlLCMwMDApOwogIG1hc2staW1hZ2U6Y29uaWMtZ3JhZGllbnQoZnJvbSBj"
    "YWxjKHZhcigtLWdhKSArIDQ1ZGVnKSwjMDAwLHRyYW5zcGFyZW50IDEwJSA5MCUsIzAwMCl9Ci5i"
    "dG4uY3RhOmFmdGVye3dpZHRoOjEwMCU7YXNwZWN0LXJhdGlvOjE7b3BhY2l0eTouNTU7CiAgYmFj"
    "a2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoLTUwZGVnLHRyYW5zcGFyZW50LHZhcigtLWhsKSx0cmFu"
    "c3BhcmVudCk7CiAgLXdlYmtpdC1tYXNrLWltYWdlOnJhZGlhbC1ncmFkaWVudChjaXJjbGUgYXQg"
    "Ym90dG9tLHRyYW5zcGFyZW50IDQwJSwjMDAwKTsKICBtYXNrLWltYWdlOnJhZGlhbC1ncmFkaWVu"
    "dChjaXJjbGUgYXQgYm90dG9tLHRyYW5zcGFyZW50IDQwJSwjMDAwKTsKICBhbmltYXRpb246c2hp"
    "bW1lciB2YXIoLS1kdXIpIGxpbmVhciBpbmZpbml0ZX0KLmJ0bi5jdGE6aG92ZXJ7LS1ncDoyMiU7"
    "LS1nbzo5NWRlZ30KLmJ0bi5jdGE6YWN0aXZle3RyYW5zbGF0ZTowIDFweH0KQGtleWZyYW1lcyBn"
    "YXt0b3stLWdhOjM2MGRlZ319CkBrZXlmcmFtZXMgc2hpbW1lcnt0b3tyb3RhdGU6MzYwZGVnfX0K"
    "Ci8qIDIyIMK3IExpcXVpZCBnbGFzcyAqLwouYnRuLmdsYXNzLC5nbGFzc2NhcmR7cG9zaXRpb246"
    "cmVsYXRpdmU7YmFja2dyb3VuZDpjb2xvci1taXgoaW4gc3JnYix2YXIoLS1jYXJkMikgNzAlLHRy"
    "YW5zcGFyZW50KTsKICBib3JkZXI6MXB4IHNvbGlkIGNvbG9yLW1peChpbiBzcmdiLCNmZmYgMTYl"
    "LHZhcigtLWxpbmUpKTtjb2xvcjp2YXIoLS10eCk7CiAgYmFja2Ryb3AtZmlsdGVyOmJsdXIoMTRw"
    "eCkgc2F0dXJhdGUoMS40KTstd2Via2l0LWJhY2tkcm9wLWZpbHRlcjpibHVyKDE0cHgpIHNhdHVy"
    "YXRlKDEuNCk7CiAgYm94LXNoYWRvdzppbnNldCAxcHggMXB4IDFweCAtLjVweCByZ2JhKDI1NSwy"
    "NTUsMjU1LC41NSksaW5zZXQgLTFweCAtMXB4IDFweCAtLjVweCByZ2JhKDI1NSwyNTUsMjU1LC4z"
    "KSwKICAgIGluc2V0IDAgMCA4cHggNnB4IHJnYmEoMjU1LDI1NSwyNTUsLjA3KSwwIDJweCAxMHB4"
    "IHJnYmEoMCwwLDAsLjEyKX0KLmJ0bi5nbGFzczphZnRlcntjb250ZW50OiIiO3Bvc2l0aW9uOmFi"
    "c29sdXRlO2luc2V0OjFweCAxcHggYXV0bztoZWlnaHQ6NDIlO2JvcmRlci1yYWRpdXM6aW5oZXJp"
    "dDsKICBiYWNrZ3JvdW5kOmxpbmVhci1ncmFkaWVudCgxODBkZWcscmdiYSgyNTUsMjU1LDI1NSwu"
    "MzQpLHRyYW5zcGFyZW50KTtwb2ludGVyLWV2ZW50czpub25lfQouYnRuLmdsYXNzOmhvdmVye3Ry"
    "YW5zZm9ybTpzY2FsZSgxLjAzKX0KCi8qIHByZXNzIHJpcHBsZSAoa2l0IGRvY2svYnV0dG9uIGZl"
    "ZWwpICovCi5yaXB7cG9zaXRpb246YWJzb2x1dGU7Ym9yZGVyLXJhZGl1czo1MCU7dHJhbnNmb3Jt"
    "OnRyYW5zbGF0ZSgtNTAlLC01MCUpIHNjYWxlKDApO3BvaW50ZXItZXZlbnRzOm5vbmU7CiAgYmFj"
    "a2dyb3VuZDpyYWRpYWwtZ3JhZGllbnQoY2lyY2xlLHJnYmEoMjU1LDI1NSwyNTUsLjU1KSx0cmFu"
    "c3BhcmVudCA3MCUpO2FuaW1hdGlvbjpyaXAgLjU1cyBlYXNlLW91dCBmb3J3YXJkc30KQGtleWZy"
    "YW1lcyByaXB7dG97dHJhbnNmb3JtOnRyYW5zbGF0ZSgtNTAlLC01MCUpIHNjYWxlKDEpO29wYWNp"
    "dHk6MH19CgovKiAyNCDCtyBBbmltYXRlZCBiYW5uZXIgKyAxNyDCtyBsb2dvIGNsb3VkIG1hcnF1"
    "ZWUgKi8KLmJhbm5lcntwb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRkZW47Ym9yZGVyLXJh"
    "ZGl1czoxNHB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7CiAgYmFja2dyb3VuZDp2YXIo"
    "LS1jYXJkKTttYXJnaW46MnB4IDAgMTJweDtoZWlnaHQ6MzhweDtkaXNwbGF5OmZsZXg7YWxpZ24t"
    "aXRlbXM6Y2VudGVyOwogIC13ZWJraXQtbWFzay1pbWFnZTpsaW5lYXItZ3JhZGllbnQoOTBkZWcs"
    "dHJhbnNwYXJlbnQsIzAwMCA4JSwjMDAwIDkyJSx0cmFuc3BhcmVudCk7CiAgbWFzay1pbWFnZTps"
    "aW5lYXItZ3JhZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCA4JSwjMDAwIDkyJSx0cmFuc3Bh"
    "cmVudCl9Ci5iYW5uZXIgLnRyYWNre2Rpc3BsYXk6ZmxleDtnYXA6MzBweDt3aGl0ZS1zcGFjZTpu"
    "b3dyYXA7YW5pbWF0aW9uOm1hcnEgMjZzIGxpbmVhciBpbmZpbml0ZTt3aWxsLWNoYW5nZTp0cmFu"
    "c2Zvcm19Ci5iYW5uZXI6aG92ZXIgLnRyYWNre2FuaW1hdGlvbi1wbGF5LXN0YXRlOnBhdXNlZH0K"
    "LmJhbm5lciBzcGFue2ZvbnQtc2l6ZTouN3JlbTtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtd2VpZ2h0"
    "OjYwMDtsZXR0ZXItc3BhY2luZzouMDNlbTtkaXNwbGF5OmlubGluZS1mbGV4O2FsaWduLWl0ZW1z"
    "OmNlbnRlcjtnYXA6NnB4fQouYmFubmVyIHNwYW4gYntjb2xvcjp2YXIoLS1hY2MpfQouYmFubmVy"
    "IHNwYW4gc3Zne3dpZHRoOjEzcHg7aGVpZ2h0OjEzcHg7Y29sb3I6dmFyKC0tYWNjKX0KQGtleWZy"
    "YW1lcyBtYXJxe3Rve3RyYW5zZm9ybTp0cmFuc2xhdGVYKC01MCUpfX0KLmNsb3Vke2Rpc3BsYXk6"
    "ZmxleDtnYXA6MjZweDtvdmVyZmxvdzpoaWRkZW47cGFkZGluZzo0cHggMCAycHg7CiAgLXdlYmtp"
    "dC1tYXNrLWltYWdlOmxpbmVhci1ncmFkaWVudCg5MGRlZyx0cmFuc3BhcmVudCwjMDAwIDEyJSwj"
    "MDAwIDg4JSx0cmFuc3BhcmVudCk7CiAgbWFzay1pbWFnZTpsaW5lYXItZ3JhZGllbnQoOTBkZWcs"
    "dHJhbnNwYXJlbnQsIzAwMCAxMiUsIzAwMCA4OCUsdHJhbnNwYXJlbnQpfQouY2xvdWQgLnR7ZGlz"
    "cGxheTpmbGV4O2dhcDoyNnB4O2FuaW1hdGlvbjptYXJxIDMwcyBsaW5lYXIgaW5maW5pdGV9Ci5j"
    "bG91ZCBpe2ZvbnQtc3R5bGU6bm9ybWFsO2ZvbnQtc2l6ZTouNjhyZW07Zm9udC13ZWlnaHQ6ODAw"
    "O2xldHRlci1zcGFjaW5nOi4xNGVtO2NvbG9yOnZhcigtLXR4Mik7b3BhY2l0eTouNTU7d2hpdGUt"
    "c3BhY2U6bm93cmFwfQoKLyogMDcgwrcgR3JhZGllbnQgdHJhY2luZyAqLwoudHJhY2V7ZGlzcGxh"
    "eTpibG9jazt3aWR0aDoxMDAlO2hlaWdodDoxMHB4O21hcmdpbjotMnB4IDAgOHB4O292ZXJmbG93"
    "OnZpc2libGV9Ci50cmFjZSAuYmFzZXtzdHJva2U6dmFyKC0tbGluZSk7c3Ryb2tlLXdpZHRoOjEu"
    "NH0KLnRyYWNlIC5sZWFke3N0cm9rZS13aWR0aDoxLjg7c3Ryb2tlLWxpbmVjYXA6cm91bmR9Ci5z"
    "ZWMgaDJ7cG9zaXRpb246cmVsYXRpdmV9CgovKiAwMSArIDEzIMK3IERvdCBsb2FkZXIgKi8KLmRv"
    "dGdyaWR7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoNywxZnIpO2dh"
    "cDozcHg7d2lkdGg6NzBweH0KLmRvdGdyaWQgaXt3aWR0aDoxMDAlO2FzcGVjdC1yYXRpbzoxO2Jv"
    "cmRlci1yYWRpdXM6MnB4O2JhY2tncm91bmQ6Y29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tdHgpIDE0"
    "JSx0cmFuc3BhcmVudCk7dHJhbnNpdGlvbjpiYWNrZ3JvdW5kIC4xMnMsdHJhbnNmb3JtIC4xMnN9"
    "Ci5kb3RncmlkIGkuYWN0e2JhY2tncm91bmQ6dmFyKC0tYWNjKTt0cmFuc2Zvcm06c2NhbGUoMS4x"
    "Mil9Ci5kb3Rze2Rpc3BsYXk6aW5saW5lLWZsZXg7Z2FwOjRweDthbGlnbi1pdGVtczpjZW50ZXJ9"
    "Ci5kb3RzIGl7d2lkdGg6NXB4O2hlaWdodDo1cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3Vu"
    "ZDp2YXIoLS1hY2MpO2FuaW1hdGlvbjpib3VuY2UgMXMgaW5maW5pdGUgZWFzZS1pbi1vdXR9Ci5k"
    "b3RzIGk6bnRoLWNoaWxkKDIpe2FuaW1hdGlvbi1kZWxheTouMTVzfS5kb3RzIGk6bnRoLWNoaWxk"
    "KDMpe2FuaW1hdGlvbi1kZWxheTouM3N9CkBrZXlmcmFtZXMgYm91bmNlezAlLDgwJSwxMDAle3Ry"
    "YW5zZm9ybTp0cmFuc2xhdGVZKDApO29wYWNpdHk6LjM1fTQwJXt0cmFuc2Zvcm06dHJhbnNsYXRl"
    "WSgtNXB4KTtvcGFjaXR5OjF9fQoKLyogc3BsYXNoICovCiNzcGxhc2h7cG9zaXRpb246Zml4ZWQ7"
    "aW5zZXQ6MDt6LWluZGV4OjMwMDtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1uO2Fs"
    "aWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2dhcDoxNnB4OwogIGJhY2tn"
    "cm91bmQ6dmFyKC0tYmcpO3RyYW5zaXRpb246b3BhY2l0eSAuNXMgZWFzZSx2aXNpYmlsaXR5IC41"
    "c30KI3NwbGFzaC5nb25le29wYWNpdHk6MDt2aXNpYmlsaXR5OmhpZGRlbn0KI3NwbGFzaCAubWFy"
    "a3t3aWR0aDo1OHB4O2hlaWdodDo1OHB4O2JvcmRlci1yYWRpdXM6MTJweDtkaXNwbGF5OmdyaWQ7"
    "cGxhY2UtaXRlbXM6Y2VudGVyO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpOwogIGNvbG9yOiNm"
    "ZmZmZmY7Zm9udC13ZWlnaHQ6ODAwO2ZvbnQtc2l6ZToxLjFyZW07YW5pbWF0aW9uOm1hcmtpbiAu"
    "N3MgY3ViaWMtYmV6aWVyKC4yLDEuNCwuNCwxKSBib3RofQojc3BsYXNoIGg0e21hcmdpbjowO2Zv"
    "bnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6MXJlbTtmb250LXdlaWdodDo4MDA7bGV0"
    "dGVyLXNwYWNpbmc6LS4wM2VtOwogIGFuaW1hdGlvbjpmYWRldXAgLjZzIC4xNXMgYm90aH0KI3Nw"
    "bGFzaCBzbWFsbHtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjVyZW07bGV0dGVyLXNwYWNp"
    "bmc6LjIyZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2FuaW1hdGlvbjpmYWRldXAgLjZzIC4y"
    "NXMgYm90aH0KQGtleWZyYW1lcyBtYXJraW57ZnJvbXtvcGFjaXR5OjA7dHJhbnNmb3JtOnNjYWxl"
    "KC42KSByb3RhdGUoLTEyZGVnKX10b3tvcGFjaXR5OjE7dHJhbnNmb3JtOm5vbmV9fQpAa2V5ZnJh"
    "bWVzIGZhZGV1cHtmcm9te29wYWNpdHk6MDt0cmFuc2Zvcm06dHJhbnNsYXRlWSg4cHgpfXRve29w"
    "YWNpdHk6MTt0cmFuc2Zvcm06bm9uZX19CgovKiAwOCDCtyBLaW5ldGljIGdyaWQgY2FudmFzICov"
    "CiNrZ3twb3NpdGlvbjpmaXhlZDtpbnNldDowO3otaW5kZXg6MDtwb2ludGVyLWV2ZW50czpub25l"
    "O29wYWNpdHk6LjI4fQoKLyogc3RhZ2dlcmVkIHJldmVhbCBpcyBkcml2ZW4gYnkgdGhlIFdlYiBB"
    "bmltYXRpb25zIEFQSSAoY2Fubm90IGNsYXNoIHdpdGggdGhlbWUgQ1NTKSAqLwoKLyogMDIgKyAy"
    "NSDCtyBEb2NrIHdpdGggc3ByaW5nIHBpbGwgKi8KbmF2I3RhYnMgLmRvY2t7cG9zaXRpb246cmVs"
    "YXRpdmV9Cm5hdiN0YWJzIC5kcGlsbHtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjA7dG9wOjA7ei1p"
    "bmRleDowO2JvcmRlci1yYWRpdXM6MTZweDtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTsKICBi"
    "b3gtc2hhZG93OjAgNnB4IDE4cHggY29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tYWNjKSAzNSUsdHJh"
    "bnNwYXJlbnQpO3BvaW50ZXItZXZlbnRzOm5vbmV9Cm5hdiN0YWJzIGJ1dHRvbntwb3NpdGlvbjpy"
    "ZWxhdGl2ZTt6LWluZGV4OjE7d2lsbC1jaGFuZ2U6dHJhbnNmb3JtOwogIHRyYW5zaXRpb246Y29s"
    "b3IgLjI2cyBjdWJpYy1iZXppZXIoLjIyLDEsLjM2LDEpLHRyYW5zZm9ybSAuMThzIGN1YmljLWJl"
    "emllciguMywxLjUsLjUsMSl9Cm5hdiN0YWJzIGJ1dHRvbi5vbntiYWNrZ3JvdW5kOnRyYW5zcGFy"
    "ZW50O2NvbG9yOiNmZmZmZmZ9Cm5hdiN0YWJzIC5kcGlsbHtvcGFjaXR5OjB9Cm5hdiN0YWJzIC5k"
    "cGlsbC5yZWFkeXtvcGFjaXR5OjF9CgovKiAxNCDCtyBWLXRhYnMgc2xpZGluZyBpbmRpY2F0b3Ig"
    "Ki8KLnN1Ym5hdntwb3NpdGlvbjpyZWxhdGl2ZX0KLnN1Ym5hdiAudnBpbGx7cG9zaXRpb246YWJz"
    "b2x1dGU7bGVmdDowO3RvcDowO3otaW5kZXg6MDtib3JkZXItcmFkaXVzOjExcHg7YmFja2dyb3Vu"
    "ZDp2YXIoLS1waWxsZ3JhZCk7dHJhbnNpdGlvbjpub25lO3BvaW50ZXItZXZlbnRzOm5vbmV9Ci5z"
    "dWJuYXYgYnV0dG9ue3Bvc2l0aW9uOnJlbGF0aXZlO3otaW5kZXg6MX0KLnN1Ym5hdiBidXR0b24u"
    "b257YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjb2xvcjojZmZmZmZmO2JvcmRlci1jb2xvcjp0cmFu"
    "c3BhcmVudH0KCi8qIDE1IMK3IEFuaW1hdGVkIHByb2ZpbGUgY2FyZCDigJQgdGlsdCArIGdsYXJl"
    "ICovCi50aWx0e3RyYW5zZm9ybS1zdHlsZTpwcmVzZXJ2ZS0zZDt0cmFuc2l0aW9uOnRyYW5zZm9y"
    "bSAuMzVzIGN1YmljLWJlemllciguMjIsMSwuMzYsMSk7d2lsbC1jaGFuZ2U6dHJhbnNmb3JtfQou"
    "dGlsdCAuZ2xhcmV7cG9zaXRpb246YWJzb2x1dGU7aW5zZXQ6MDtib3JkZXItcmFkaXVzOmluaGVy"
    "aXQ7cG9pbnRlci1ldmVudHM6bm9uZTtvcGFjaXR5OjA7dHJhbnNpdGlvbjpvcGFjaXR5IC4zczsK"
    "ICBiYWNrZ3JvdW5kOnJhZGlhbC1ncmFkaWVudCgyNDBweCBjaXJjbGUgYXQgdmFyKC0tbXgsNTAl"
    "KSB2YXIoLS1teSw1MCUpLHJnYmEoMjU1LDI1NSwyNTUsLjIyKSx0cmFuc3BhcmVudCA2MCUpfQou"
    "dGlsdDpob3ZlciAuZ2xhcmV7b3BhY2l0eToxfQouc2hpbmVjYXJke3Bvc2l0aW9uOnJlbGF0aXZl"
    "O292ZXJmbG93OmhpZGRlbn0KLnNoaW5lY2FyZDpiZWZvcmV7Y29udGVudDoiIjtwb3NpdGlvbjph"
    "YnNvbHV0ZTt0b3A6LTYwJTtsZWZ0Oi0zMCU7d2lkdGg6NTUlO2hlaWdodDoyMjAlO3RyYW5zZm9y"
    "bTpyb3RhdGUoMThkZWcpOwogIGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRyYW5z"
    "cGFyZW50LHJnYmEoMjU1LDI1NSwyNTUsLjE2KSx0cmFuc3BhcmVudCk7YW5pbWF0aW9uOmNhcmRz"
    "aGluZSA1LjVzIGVhc2UtaW4tb3V0IGluZmluaXRlO3BvaW50ZXItZXZlbnRzOm5vbmV9CkBrZXlm"
    "cmFtZXMgY2FyZHNoaW5lezAlLDcwJXtsZWZ0Oi00MCV9MTAwJXtsZWZ0OjEyMCV9fQoKLyogMDUg"
    "wrcgSG9sZCBhY3Rpb24gYnV0dG9uICovCi5ob2xke3Bvc2l0aW9uOnJlbGF0aXZlO292ZXJmbG93"
    "OmhpZGRlbn0KLmhvbGQgLmZpbGx7cG9zaXRpb246YWJzb2x1dGU7bGVmdDowO3RvcDowO2JvdHRv"
    "bTowO3dpZHRoOjA7YmFja2dyb3VuZDpyZ2JhKDI1NSwyNTUsMjU1LC4yNSk7dHJhbnNpdGlvbjp3"
    "aWR0aCAuMDVzIGxpbmVhcn0KLmhvbGQuZG9uZXthbmltYXRpb246cG9wIC40cyBjdWJpYy1iZXpp"
    "ZXIoLjIsMS42LC40LDEpfQpAa2V5ZnJhbWVzIHBvcHs1MCV7dHJhbnNmb3JtOnNjYWxlKDEuMDQp"
    "fX0KCi8qIDExIMK3IE1vcnBoaW5nIHNlYXJjaCAqLwoubXNlYXJjaHtkaXNwbGF5OmZsZXg7YWxp"
    "Z24taXRlbXM6Y2VudGVyO2dhcDo4cHg7bWFyZ2luOjAgMCAxMHB4fQoubXNlYXJjaCAuZmllbGR3"
    "cmFwe2ZsZXg6MTttaW4td2lkdGg6MDtwb3NpdGlvbjpyZWxhdGl2ZTtoZWlnaHQ6NDBweH0KLm1z"
    "ZWFyY2ggaW5wdXR7d2lkdGg6MTAwJTtoZWlnaHQ6NDBweDtwYWRkaW5nOjAgMTNweCAwIDM2cHg7"
    "Ym9yZGVyLXJhZGl1czo5OXB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7CiAgYmFja2dy"
    "b3VuZDp2YXIoLS1jYXJkKTtjb2xvcjp2YXIoLS10eCk7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7"
    "Zm9udC1zaXplOi44cmVtO291dGxpbmU6bm9uZTsKICB0cmFuc2l0aW9uOmJveC1zaGFkb3cgLjNz"
    "IGN1YmljLWJlemllciguMjIsMSwuMzYsMSksYm9yZGVyLWNvbG9yIC4zc30KLm1zZWFyY2ggaW5w"
    "dXQ6Zm9jdXN7Ym9yZGVyLWNvbG9yOnZhcigtLWFjYyk7Ym94LXNoYWRvdzowIDAgMCA0cHggY29s"
    "b3ItbWl4KGluIHNyZ2IsdmFyKC0tYWNjKSAxNiUsdHJhbnNwYXJlbnQpfQoubXNlYXJjaCBzdmd7"
    "cG9zaXRpb246YWJzb2x1dGU7bGVmdDoxMnB4O3RvcDoxMXB4O3dpZHRoOjE3cHg7aGVpZ2h0OjE3"
    "cHg7Y29sb3I6dmFyKC0tdHgyKTt0cmFuc2l0aW9uOmNvbG9yIC4yNXN9Ci5tc2VhcmNoIGlucHV0"
    "OmZvY3VzK3N2ZywubXNlYXJjaCAuZmllbGR3cmFwOmZvY3VzLXdpdGhpbiBzdmd7Y29sb3I6dmFy"
    "KC0tYWNjKX0KLmNoaXByb3d7ZGlzcGxheTpmbGV4O2dhcDo2cHg7b3ZlcmZsb3cteDphdXRvO3Bh"
    "ZGRpbmctYm90dG9tOjEwcHh9Ci5jaGlwcm93IGJ1dHRvbntmbGV4OjAgMCBhdXRvO2hlaWdodDoz"
    "MHB4O3BhZGRpbmc6MCAxMnB4O2JvcmRlci1yYWRpdXM6OTlweDtib3JkZXI6MXB4IHNvbGlkIHZh"
    "cigtLWxpbmUpOwogIGJhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4Mik7Zm9u"
    "dC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi43cmVtO2ZvbnQtd2VpZ2h0OjcwMDtjdXJz"
    "b3I6cG9pbnRlcjt3aGl0ZS1zcGFjZTpub3dyYXA7CiAgdHJhbnNpdGlvbjp0cmFuc2Zvcm0gLjJz"
    "IGN1YmljLWJlemllciguMywxLjUsLjUsMSksYmFja2dyb3VuZCAuMnMsY29sb3IgLjJzfQouY2hp"
    "cHJvdyBidXR0b24ub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmZmZjti"
    "b3JkZXItY29sb3I6dHJhbnNwYXJlbnQ7dHJhbnNmb3JtOnNjYWxlKDEuMDQpfQoKLyogc2tlbGV0"
    "b25zICgxMyDCtyBsb2FkZXIpICovCi5za3twb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRk"
    "ZW47YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9yZGVyLXJhZGl1czp2YXIoLS1ycyk7aGVpZ2h0"
    "Ojc0cHg7bWFyZ2luLWJvdHRvbTo5cHh9Ci5zazphZnRlcntjb250ZW50OiIiO3Bvc2l0aW9uOmFi"
    "c29sdXRlO2luc2V0OjA7YmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoOTBkZWcsdHJhbnNwYXJl"
    "bnQsY29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tdHgpIDEwJSx0cmFuc3BhcmVudCksdHJhbnNwYXJl"
    "bnQpOwogIHRyYW5zZm9ybTp0cmFuc2xhdGVYKC0xMDAlKTthbmltYXRpb246c2hpbSAxLjE1cyBp"
    "bmZpbml0ZX0KQGtleWZyYW1lcyBzaGlte3Rve3RyYW5zZm9ybTp0cmFuc2xhdGVYKDEwMCUpfX0K"
    "Ci8qIDE5IMK3IEZlYXR1cmUgc3RyaXAgKi8KLmZlYXRze2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBs"
    "YXRlLWNvbHVtbnM6cmVwZWF0KDMsMWZyKTtnYXA6OHB4O21hcmdpbi1ib3R0b206MTJweH0KLmZl"
    "YXR7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2Jv"
    "cmRlci1yYWRpdXM6dmFyKC0tcnMpO3BhZGRpbmc6MTFweCA5cHg7dGV4dC1hbGlnbjpjZW50ZXI7"
    "CiAgdHJhbnNpdGlvbjp0cmFuc2Zvcm0gLjI1cyBjdWJpYy1iZXppZXIoLjIyLDEsLjM2LDEpfQou"
    "ZmVhdDpob3Zlcnt0cmFuc2Zvcm06dHJhbnNsYXRlWSgtM3B4KX0KLmZlYXQgc3Zne3dpZHRoOjE3"
    "cHg7aGVpZ2h0OjE3cHg7Y29sb3I6dmFyKC0tYWNjKX0KLmZlYXQgYntkaXNwbGF5OmJsb2NrO2Zv"
    "bnQtc2l6ZTouNjhyZW07bWFyZ2luLXRvcDo1cHh9Ci5mZWF0IHNtYWxse2Rpc3BsYXk6YmxvY2s7"
    "Zm9udC1zaXplOi42cmVtO2NvbG9yOnZhcigtLXR4Mil9CgovKiAwMyDCtyBXb3JrcyB3aGVlbCDi"
    "gJQgcm90YXRpbmcgZGVhbCBkaWFsICovCi53aGVlbHtwb3NpdGlvbjpyZWxhdGl2ZTtoZWlnaHQ6"
    "MTE4cHg7bWFyZ2luOjJweCAwIDEycHg7b3ZlcmZsb3c6aGlkZGVuO2JvcmRlci1yYWRpdXM6dmFy"
    "KC0tcik7CiAgYm9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5kOnZhcigtLWNh"
    "cmQpfQoud2hlZWwgLnJpbmd7cG9zaXRpb246YWJzb2x1dGU7cmlnaHQ6LTUycHg7dG9wOjUwJTt3"
    "aWR0aDoxNzZweDtoZWlnaHQ6MTc2cHg7bWFyZ2luLXRvcDotODhweDtib3JkZXItcmFkaXVzOjUw"
    "JTsKICBib3JkZXI6MXB4IGRhc2hlZCB2YXIoLS1saW5lKTthbmltYXRpb246c3BpbiAyNnMgbGlu"
    "ZWFyIGluZmluaXRlfQoud2hlZWwgLnJpbmcgYntwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjUwJTt0"
    "b3A6NTAlO3dpZHRoOjUwcHg7aGVpZ2h0OjI0cHg7bWFyZ2luOi0xMnB4IDAgMCAtMjVweDtib3Jk"
    "ZXItcmFkaXVzOjk5cHg7CiAgZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtmb250LXNp"
    "emU6LjYycmVtO2ZvbnQtd2VpZ2h0OjgwMDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtib3JkZXI6"
    "MXB4IHNvbGlkIHZhcigtLWxpbmUpO2NvbG9yOnZhcigtLXR4Mil9Ci53aGVlbCAuY2Fwe3Bvc2l0"
    "aW9uOmFic29sdXRlO2luc2V0OjA7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjtq"
    "dXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO3BhZGRpbmc6MCAxNnB4OwogIGJhY2tncm91bmQ6bGluZWFy"
    "LWdyYWRpZW50KDkwZGVnLHZhcigtLWNhcmQpIDAgNTglLHRyYW5zcGFyZW50KX0KLndoZWVsIC5j"
    "YXA+KnttYXgtd2lkdGg6NjIlfQoud2hlZWwgLmNhcCBlbXtmb250LXN0eWxlOm5vcm1hbDtmb250"
    "LXNpemU6LjZyZW07bGV0dGVyLXNwYWNpbmc6LjE4ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNl"
    "O2NvbG9yOnZhcigtLWFjYyk7Zm9udC13ZWlnaHQ6ODAwfQoud2hlZWwgLmNhcCBie2ZvbnQtZmFt"
    "aWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6MS4wNXJlbTtmb250LXdlaWdodDo4MDB9CkBrZXlm"
    "cmFtZXMgc3Bpbnt0b3t0cmFuc2Zvcm06cm90YXRlKDM2MGRlZyl9fQoKLyogc3VjY2VzcyBidXJz"
    "dCAqLwojZnh7cG9zaXRpb246Zml4ZWQ7aW5zZXQ6MDt6LWluZGV4OjE1MDtkaXNwbGF5Om5vbmU7"
    "cGxhY2UtaXRlbXM6Y2VudGVyO3BvaW50ZXItZXZlbnRzOm5vbmV9CiNmeC5vbntkaXNwbGF5Omdy"
    "aWR9CiNmeCAucmluZ3t3aWR0aDo4MnB4O2hlaWdodDo4MnB4O2JvcmRlci1yYWRpdXM6NTAlO2Jv"
    "cmRlcjoycHggc29saWQgdmFyKC0tb2spO2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7"
    "CiAgY29sb3I6dmFyKC0tb2spO2FuaW1hdGlvbjpidXJzdCAuNzVzIGN1YmljLWJlemllciguMiwx"
    "LjMsLjQsMSl9CiNmeCAucmluZyBzdmd7d2lkdGg6MzRweDtoZWlnaHQ6MzRweH0KI2Z4IC53YXZl"
    "e3Bvc2l0aW9uOmFic29sdXRlO3dpZHRoOjgycHg7aGVpZ2h0OjgycHg7Ym9yZGVyLXJhZGl1czo1"
    "MCU7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1vayk7YW5pbWF0aW9uOndhdmUgLjlzIGVhc2Utb3V0"
    "IGZvcndhcmRzfQpAa2V5ZnJhbWVzIGJ1cnN0e2Zyb217dHJhbnNmb3JtOnNjYWxlKC40KTtvcGFj"
    "aXR5OjB9dG97dHJhbnNmb3JtOnNjYWxlKDEpO29wYWNpdHk6MX19CkBrZXlmcmFtZXMgd2F2ZXt0"
    "b3t0cmFuc2Zvcm06c2NhbGUoMy40KTtvcGFjaXR5OjB9fQoKLyogbnVtYmVyIHRpY2tlciAqLwou"
    "dGlja3tmb250LXZhcmlhbnQtbnVtZXJpYzp0YWJ1bGFyLW51bXN9CgpAbWVkaWEocHJlZmVycy1y"
    "ZWR1Y2VkLW1vdGlvbjpyZWR1Y2Upeyp7YW5pbWF0aW9uOm5vbmUhaW1wb3J0YW50O3RyYW5zaXRp"
    "b246bm9uZSFpbXBvcnRhbnR9fQoKLmNhcmQsLnJvdywucGNhcmR7dHJhbnNpdGlvbjp0cmFuc2Zv"
    "cm0gLjE4cyxib3gtc2hhZG93IC4xOHN9LnJvdzpob3ZlciwucGNhcmQ6aG92ZXJ7dHJhbnNmb3Jt"
    "OnRyYW5zbGF0ZVkoLTJweCk7Ym94LXNoYWRvdzowIDEwcHggMjZweCByZ2JhKDIwLDI1LDQ1LC4x"
    "MCl9Lmhlcm8gaDF7Zm9udC1zaXplOjEuNnJlbX0KPC9zdHlsZT4KPC9oZWFkPgo8Ym9keT4KCjxj"
    "YW52YXMgaWQ9ImtnIj48L2NhbnZhcz4KPGRpdiBpZD0ic3BsYXNoIj48ZGl2IGNsYXNzPSJtYXJr"
    "IiBpZD0ic3BtYXJrIj5aVDwvZGl2PjxkaXYgY2xhc3M9ImRvdGdyaWQiIGlkPSJkZyI+PC9kaXY+"
    "CiAgPGg0IGlkPSJzcG5hbWUiPlpvcm8gVEcgU3RvcmU8L2g0PjxzbWFsbCBpZD0ic3B0YWciPlNp"
    "bXBsZS4gRmFzdC4gUmVsaWFibGUuPC9zbWFsbD48L2Rpdj4KPHN2ZyB3aWR0aD0iMCIgaGVpZ2h0"
    "PSIwIiBzdHlsZT0icG9zaXRpb246YWJzb2x1dGUiPjxkZWZzPgogIDxmaWx0ZXIgaWQ9ImxnIiB4"
    "PSIwJSIgeT0iMCUiIHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiIGNvbG9yLWludGVycG9sYXRp"
    "b24tZmlsdGVycz0ic1JHQiI+CiAgICA8ZmVUdXJidWxlbmNlIHR5cGU9ImZyYWN0YWxOb2lzZSIg"
    "YmFzZUZyZXF1ZW5jeT0iMC4wNSAwLjA1IiBudW1PY3RhdmVzPSIxIiBzZWVkPSIxIiByZXN1bHQ9"
    "InQiLz4KICAgIDxmZUdhdXNzaWFuQmx1ciBpbj0idCIgc3RkRGV2aWF0aW9uPSIyIiByZXN1bHQ9"
    "ImIiLz4KICAgIDxmZURpc3BsYWNlbWVudE1hcCBpbj0iU291cmNlR3JhcGhpYyIgaW4yPSJiIiBz"
    "Y2FsZT0iNzAiIHhDaGFubmVsU2VsZWN0b3I9IlIiIHlDaGFubmVsU2VsZWN0b3I9IkIiLz4KICA8"
    "L2ZpbHRlcj48L2RlZnM+PC9zdmc+CjxkaXYgaWQ9ImZ4Ij48ZGl2IGNsYXNzPSJ3YXZlIj48L2Rp"
    "dj48ZGl2IGNsYXNzPSJyaW5nIj48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIg"
    "c3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMi40IiBzdHJva2UtbGluZWNhcD0i"
    "cm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yMCA2IDkgMTdsLTUtNSIv"
    "Pjwvc3ZnPjwvZGl2PjwvZGl2Pgo8ZGl2IGlkPSJzdyI+PGVtPlByZXZpZXc8L2VtPgogIDxidXR0"
    "b24gZGF0YS1zdz0iYXBwIiBjbGFzcz0ib24iPlN0b3JlPC9idXR0b24+CiAgPGJ1dHRvbiBkYXRh"
    "LXN3PSJhZG1pbiI+QWRtaW48L2J1dHRvbj4KPC9kaXY+Cgo8ZGl2IGlkPSJ2aWV3LWFwcCI+Cjxk"
    "aXYgaWQ9InNoZWxsIj4KICA8aGVhZGVyIGlkPSJ0b3BiYXIiPgogICAgPGRpdiBjbGFzcz0ibG9n"
    "byI+WlQ8L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJyYW5kIj48YiBpZD0iYm5hbWUiPlpvcm8gVEcg"
    "U3RvcmU8L2I+PHNwYW4gaWQ9ImJ0YWciPlNpbXBsZS4gRmFzdC4gUmVsaWFibGUuPC9zcGFuPjwv"
    "ZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iaWNvbmNoaXAgaGlkZGVuIiBpZD0iY2FydGNoaXAiIHRp"
    "dGxlPSJDYXJ0Ij48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJj"
    "dXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJv"
    "a2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNNiAyIDMgNnYxNGEyIDIgMCAwIDAgMiAyaDE0"
    "YTIgMiAwIDAgMCAyLTJWNmwtMy00eiIvPjxwYXRoIGQ9Ik0zIDZoMTgiLz48cGF0aCBkPSJNMTYg"
    "MTBhNCA0IDAgMCAxLTggMCIvPjwvc3ZnPjxpIGlkPSJjYXJ0biI+MDwvaT48L2J1dHRvbj4KICAg"
    "IDxidXR0b24gY2xhc3M9ImJhbGNoaXAiIGRhdGEtZ289IndhbGxldCI+PHNwYW4gY2xhc3M9InBs"
    "dXMiPis8L3NwYW4+4oK5PHNwYW4gaWQ9ImJhbCIgY2xhc3M9InRpY2siPjI0OC41MDwvc3Bhbj48"
    "L2J1dHRvbj4KICA8L2hlYWRlcj4KICA8bWFpbiBpZD0ic3RhZ2UiPjwvbWFpbj4KPC9kaXY+Cjxu"
    "YXYgaWQ9InRhYnMiPjxkaXYgY2xhc3M9ImRvY2siPjxzcGFuIGNsYXNzPSJkcGlsbCIgaWQ9ImRw"
    "aWxsIj48L3NwYW4+CiAgPGJ1dHRvbiBkYXRhLXRhYj0ic2hvcCIgY2xhc3M9Im9uIj48c3ZnIHZp"
    "ZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9r"
    "ZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5k"
    "Ij48cGF0aCBkPSJNNiAyIDMgNnYxNGEyIDIgMCAwIDAgMiAyaDE0YTIgMiAwIDAgMCAyLTJWNmwt"
    "My00eiIvPjxwYXRoIGQ9Ik0zIDZoMTgiLz48cGF0aCBkPSJNMTYgMTBhNCA0IDAgMCAxLTggMCIv"
    "Pjwvc3ZnPjxzcGFuIGRhdGEtbmF2PSJzaG9wIj5TaG9wPC9zcGFuPjwvYnV0dG9uPgogIDxidXR0"
    "b24gZGF0YS10YWI9Im9yZGVycyI+PHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUi"
    "IHN0cm9rZT0iY3VycmVudENvbG9yIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJy"
    "b3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTIxIDhhMiAyIDAgMCAwLTEt"
    "MS43M2wtNy00YTIgMiAwIDAgMC0yIDBsLTcgNEEyIDIgMCAwIDAgMyA4djhhMiAyIDAgMCAwIDEg"
    "MS43M2w3IDRhMiAyIDAgMCAwIDIgMGw3LTRBMiAyIDAgMCAwIDIxIDE2WiIvPjxwYXRoIGQ9Im0z"
    "LjMgNyA4LjcgNSA4LjctNSIvPjxwYXRoIGQ9Ik0xMiAyMlYxMiIvPjwvc3ZnPjxzcGFuIGRhdGEt"
    "bmF2PSJvcmRlcnMiPk9yZGVyczwvc3Bhbj48L2J1dHRvbj4KICA8YnV0dG9uIGRhdGEtdGFiPSJl"
    "YXJuIj48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50"
    "Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGlu"
    "ZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTMgMiAzIDE0aDlsLTEgOCAxMC0xMmgtOXoiLz48L3N2"
    "Zz48c3BhbiBkYXRhLW5hdj0iZWFybiI+RWFybjwvc3Bhbj48L2J1dHRvbj4KICA8YnV0dG9uIGRh"
    "dGEtdGFiPSJ3YWxsZXQiPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJv"
    "a2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQi"
    "IHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0xOSA3VjVhMiAyIDAgMCAwLTItMkg1"
    "YTIgMiAwIDAgMCAwIDRoMTVhMiAyIDAgMCAxIDIgMnY5YTIgMiAwIDAgMS0yIDJINWEyIDIgMCAw"
    "IDEtMi0yVjUiLz48cGF0aCBkPSJNMTggMTJoLjAxIi8+PC9zdmc+PHNwYW4gZGF0YS1uYXY9Indh"
    "bGxldCI+V2FsbGV0PC9zcGFuPjwvYnV0dG9uPgogIDxidXR0b24gZGF0YS10YWI9InlvdSI+PHN2"
    "ZyB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3VycmVudENvbG9yIiBz"
    "dHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJy"
    "b3VuZCI+PHBhdGggZD0iTTE5IDIxdi0yYTQgNCAwIDAgMC00LTRIOWE0IDQgMCAwIDAtNCA0djIi"
    "Lz48Y2lyY2xlIGN4PSIxMiIgY3k9IjciIHI9IjQiLz48L3N2Zz48c3BhbiBkYXRhLW5hdj0ieW91"
    "Ij5Zb3U8L3NwYW4+PC9idXR0b24+CjwvZGl2PjwvbmF2Pgo8L2Rpdj4KCjxkaXYgaWQ9InZpZXct"
    "YWRtaW4iIGNsYXNzPSJoaWRkZW4iPjxkaXYgaWQ9InNoZWxsIj4KICA8aGVhZGVyIGlkPSJ0b3Bi"
    "YXIiPgogICAgPGRpdiBjbGFzcz0ibG9nbyI+PHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9"
    "Im5vbmUiIHN0cm9rZT0iY3VycmVudENvbG9yIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5l"
    "Y2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTIwIDEzYzAgNS0z"
    "LjUgNy41LTcuNjYgOC45NWExIDEgMCAwIDEtLjY3LS4wMUM3LjUgMjAuNSA0IDE4IDQgMTNWNmEx"
    "IDEgMCAwIDEgMS0xYzIgMCA0LjUtMS4yIDYuMjQtMi43MmExLjE3IDEuMTcgMCAwIDEgMS41MiAw"
    "QzE0LjUxIDMuODEgMTcgNSAxOSA1YTEgMSAwIDAgMSAxIDF6Ii8+PC9zdmc+PC9kaXY+CiAgICA8"
    "ZGl2IGNsYXNzPSJicmFuZCI+PGI+QWRtaW4gQ29uc29sZTwvYj48c3BhbiBpZD0iYWJuYW1lIj5a"
    "b3JvIFRHIFN0b3JlPC9zcGFuPjwvZGl2PgogICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNs"
    "YXNzPSJkb3QiPjwvaT5MaXZlPC9zcGFuPgogIDwvaGVhZGVyPgogIDxkaXYgY2xhc3M9InN1Ym5h"
    "diIgaWQ9ImFzdWIiPjxzcGFuIGNsYXNzPSJ2cGlsbCIgaWQ9InZwaWxsIj48L3NwYW4+CiAgICA8"
    "YnV0dG9uIGRhdGEtYT0iZGFzaCIgY2xhc3M9Im9uIj5EYXNoYm9hcmQ8L2J1dHRvbj4KICAgIDxi"
    "dXR0b24gZGF0YS1hPSJjaGFubmVscyI+Q2hhbm5lbHM8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0"
    "YS1hPSJsb2dzIj5Mb2dzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0icGF5Ij5BdXRvLVBh"
    "eTwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9ImNvbnRlbnQiPkNvbnRlbnQ8L2J1dHRvbj4K"
    "ICAgIDxidXR0b24gZGF0YS1hPSJmZWF0dXJlcyI+RmVhdHVyZXM8L2J1dHRvbj4KICAgIDxidXR0"
    "b24gZGF0YS1hPSJtYXJrZXQiPk1hcmtldGluZzwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9"
    "ImFuYWx5dGljcyI+QW5hbHl0aWNzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0ic2VjdXJp"
    "dHkiPlNlY3VyaXR5PC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0ic3RvY2siPlN0b2NrPC9i"
    "dXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0ib3JkZXJzIj5PcmRlcnM8L2J1dHRvbj4KICAgIDxi"
    "dXR0b24gZGF0YS1hPSJ1c2VycyI+VXNlcnM8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJz"
    "ZXR0aW5ncyI+U2V0dGluZ3M8L2J1dHRvbj4KICA8L2Rpdj4KICA8bWFpbiBpZD0iYXN0YWdlIj48"
    "L21haW4+CjwvZGl2PjwvZGl2PgoKPGRpdiBpZD0ic2hlZXQiPjxkaXYgY2xhc3M9ImJnIiBkYXRh"
    "LWNsb3NlPjwvZGl2PjxkaXYgY2xhc3M9InBhbmVsIiBpZD0icGFuZWwiPjwvZGl2PjwvZGl2Pgo8"
    "ZGl2IGNsYXNzPSJ0b2FzdCIgaWQ9InRvYXN0Ij48L2Rpdj4KCjxzY3JpcHQ+Ci8qIOKUgOKUgCBs"
    "aXZlLW1vZGUgc3RhdGUsIGRlY2xhcmVkIGZpcnN0IHNvIG5vdGhpbmcgY2FuIHJlYWQgaXQgdG9v"
    "IGVhcmx5IOKUgOKUgCAqLwp2YXIgTElWRT1mYWxzZSxMT0NLRUQ9ZmFsc2UsTUU9e2FkbWluOmZh"
    "bHNlLG93bmVyOmZhbHNlLHBlcm1zOnt9fTsKdmFyIFRHPXdpbmRvdy5UZWxlZ3JhbSYmd2luZG93"
    "LlRlbGVncmFtLldlYkFwcHx8bnVsbDsKZnVuY3Rpb24gcmVhZEluaXREYXRhKCl7CiAgdHJ5e2lm"
    "KFRHJiZURy5pbml0RGF0YSYmVEcuaW5pdERhdGEubGVuZ3RoKXJldHVybiBURy5pbml0RGF0YTt9"
    "Y2F0Y2goZSl7fQogIC8qIFRlbGVncmFtIGFsc28gcHV0cyBpdCBpbiB0aGUgVVJMLCBzbyB0aGUg"
    "YXBwIHN0aWxsIHdvcmtzIGlmIHRoZSBTREsgaXMgYmxvY2tlZCAqLwogIHRyeXsKICAgIGNvbnN0"
    "IG09L1sjJj9ddGdXZWJBcHBEYXRhPShbXiZdKikvLmV4ZWMobG9jYXRpb24uaGFzaHx8IiIpOwog"
    "ICAgaWYobSYmbVsxXSlyZXR1cm4gZGVjb2RlVVJJQ29tcG9uZW50KG1bMV0pOwogIH1jYXRjaChl"
    "KXt9CiAgdHJ5ewogICAgY29uc3QgcT1uZXcgVVJMU2VhcmNoUGFyYW1zKGxvY2F0aW9uLnNlYXJj"
    "aCk7CiAgICBjb25zdCB2PXEuZ2V0KCJ0Z1dlYkFwcERhdGEiKTsKICAgIGlmKHYpcmV0dXJuIHY7"
    "CiAgfWNhdGNoKGUpe30KICB0cnl7CiAgICBjb25zdCBzYXZlZD1zZXNzaW9uU3RvcmFnZS5nZXRJ"
    "dGVtKCJ0Z0luaXQiKTsKICAgIGlmKHNhdmVkKXJldHVybiBzYXZlZDsKICB9Y2F0Y2goZSl7fQog"
    "IHJldHVybiAiIjsKfQpmdW5jdGlvbiBpbml0aWFscyhuYW1lKXsKICBjb25zdCB3PShuYW1lfHwi"
    "IikudHJpbSgpLnNwbGl0KC9ccysvKS5maWx0ZXIoQm9vbGVhbik7CiAgaWYoIXcubGVuZ3RoKXJl"
    "dHVybiAiU1QiOwogIHJldHVybiAoKHdbMF1bMF18fCIiKSsody5sZW5ndGg+MT8od1sxXVswXXx8"
    "IiIpOih3WzBdWzFdfHwiIikpKS50b1VwcGVyQ2FzZSgpOwp9CmZ1bmN0aW9uIHBhaW50QnJhbmQo"
    "bmFtZSx0YWcpewogIGlmKG5hbWUpewogICAgY29uc3Qgbj1kb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgic3BuYW1lIik7IGlmKG4pbi50ZXh0Q29udGVudD1uYW1lOwogICAgY29uc3QgbT1kb2N1bWVu"
    "dC5nZXRFbGVtZW50QnlJZCgic3BtYXJrIik7IGlmKG0pbS50ZXh0Q29udGVudD1pbml0aWFscyhu"
    "YW1lKTsKICAgIGNvbnN0IGI9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJuYW1lIik7IGlmKGIp"
    "Yi50ZXh0Q29udGVudD1uYW1lOwogICAgY29uc3QgYWI9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "ImFibmFtZSIpOyBpZihhYilhYi50ZXh0Q29udGVudD1uYW1lOwogICAgZG9jdW1lbnQudGl0bGU9"
    "bmFtZTsKICB9CiAgaWYodGFnKXsKICAgIGNvbnN0IHQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "InNwdGFnIik7IGlmKHQpdC50ZXh0Q29udGVudD10YWc7CiAgICBjb25zdCBidD1kb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgiYnRhZyIpOyBpZihidClidC50ZXh0Q29udGVudD10YWc7CiAgfQp9CnRy"
    "eXsKICBjb25zdCBjbj1sb2NhbFN0b3JhZ2UuZ2V0SXRlbSgic3RvcmVOYW1lIiksIGN0PWxvY2Fs"
    "U3RvcmFnZS5nZXRJdGVtKCJzdG9yZVRhZyIpOwogIGlmKGNufHxjdClwYWludEJyYW5kKGNuLGN0"
    "KTsKfWNhdGNoKGUpe30KdmFyIElOSVQ9cmVhZEluaXREYXRhKCk7CnRyeXtpZihJTklUKXNlc3Np"
    "b25TdG9yYWdlLnNldEl0ZW0oInRnSW5pdCIsSU5JVCk7fWNhdGNoKGUpe30KZnVuY3Rpb24gcmVh"
    "ZEFwcEtleSgpewogIHRyeXsKICAgIGNvbnN0IHE9bmV3IFVSTFNlYXJjaFBhcmFtcyhsb2NhdGlv"
    "bi5zZWFyY2gpOwogICAgaWYocS5nZXQoImsiKSlyZXR1cm4gcS5nZXQoImsiKTsKICAgIGNvbnN0"
    "IG09L1sjJj9daz0oW14mXSopLy5leGVjKGxvY2F0aW9uLmhhc2h8fCIiKTsKICAgIGlmKG0mJm1b"
    "MV0pcmV0dXJuIGRlY29kZVVSSUNvbXBvbmVudChtWzFdKTsKICAgIHJldHVybiBzZXNzaW9uU3Rv"
    "cmFnZS5nZXRJdGVtKCJhcHBLZXkiKXx8IiI7CiAgfWNhdGNoKGUpe3JldHVybiAiIjt9Cn0KdmFy"
    "IEFQUEtFWT1yZWFkQXBwS2V5KCk7CnRyeXtpZihBUFBLRVkpc2Vzc2lvblN0b3JhZ2Uuc2V0SXRl"
    "bSgiYXBwS2V5IixBUFBLRVkpO31jYXRjaChlKXt9Ci8qIGRlbW8gZGF0YSBvbmx5IOKAlCBubyB0"
    "b2tlbnMsIG5vIFVQSSBJRCwgbm8gR21haWwgY3JlZGVudGlhbHMgKi8KbGV0IENGRz17bmFtZToi"
    "Wm9ybyBURyBTdG9yZSIsdGFnOiJTaW1wbGUuIEZhc3QuIFJlbGlhYmxlLiIsY3VyOiLigrkifTsK"
    "Lyog4pSA4pSAIEVkaXRhYmxlIGNvcHkg4oCUIGV2ZXJ5IHN0cmluZyBiZWxvdyBpcyBlZGl0YWJs"
    "ZSBpbiBBZG1pbiDilrggQ29udGVudCDilIDilIAgKi8KY29uc3QgQ01TPXsKIHN0b3JlTmFtZToi"
    "Wm9ybyBURyBTdG9yZSIsIHN0b3JlVGFnOiJTaW1wbGUuIEZhc3QuIFJlbGlhYmxlLiIsCiBlbXB0"
    "eVRpdGxlOiJSZXN0b2NraW5nIiwgZW1wdHlCb2R5OiJFdmVyeXRoaW5nIGp1c3Qgc29sZCBvdXQu"
    "IEZyZXNoIGFjY291bnRzIGxhbmQgaGVyZSBzaG9ydGx5IOKAlCB0dXJuIG9uIGFuIGFsZXJ0IGFu"
    "ZCB5b3Ugd2lsbCBoZWFyIGZpcnN0LiIsCiB0aWNrMToiPGI+SW5zdGFudCBkZWxpdmVyeTwvYj4g"
    "b24gZXZlcnkgY291bnRyeSIsIHRpY2syOiJUb3AtdXBzIDxiPmNvbmZpcm1lZCBhdXRvbWF0aWNh"
    "bGx5PC9iPiIsCiB0aWNrMzoiRmFpbGVkIGxvZ2luPyA8Yj5BdXRvIHJlZnVuZDwvYj4iLCB0aWNr"
    "NDoiRGFpbHkgY2hlY2staW4gPGI+4oK5MC40MDwvYj4iLAogaGVyb0tpY2s6IkluIHN0b2NrIG5v"
    "dyIsIGhlcm9IMToiRXZlcnl0aGluZyB5b3UgbmVlZCw8YnI+aW4gdHdvIHRhcHMuIiwKIGhlcm9T"
    "dWI6Ikluc3RhbnQgZGVsaXZlcnksIE9UUCBpbi1hcHAsIHdhbGxldCB0b3AtdXBzIHZlcmlmaWVk"
    "IGF1dG9tYXRpY2FsbHkuIHtufSBjb3VudHJpZXMgbGl2ZS4iLAogaGVyb0J0bjE6IkJyb3dzZSBT"
    "dG9jayIsIGhlcm9CdG4yOiJBZGQgRnVuZHMiLAogZmVhdDE6In44cyIsIGZlYXQxczoiZGVsaXZl"
    "cnkiLCBmZWF0MjoiQXV0byIsIGZlYXQyczoicmVmdW5kcyIsIGZlYXQzOiJVUEkiLCBmZWF0M3M6"
    "ImF1dG8tdmVyaWZ5IiwKIGZsYXNoUGlsbDoiRmxhc2gg4oiSMjAlIiwgZmxhc2hUZXh0OiJFbmRz"
    "IGluIiwgZmxhc2hCdG46IlZpZXciLAogd2hlZWxLaWNrOiJSb3RhdGluZyBkZWFscyIsIHdoZWVs"
    "VGl0bGU6IlVwIHRvIDI4JSBvZmYiLCB3aGVlbFdvcmRzOiJJTlNUQU5ULCBBVVRPLVZFUklGWSwg"
    "UkVGVU5EUywgMjTDlzciLAogc2hvcEhlYWQ6IkluIFN0b2NrIiwgc2hvcFNvcnQ6IlNvcnQiLCBz"
    "ZWFyY2hQaDoiU2VhcmNoIGNvdW50cnkgb3IgY29kZeKApiIsIGFsZXJ0c0J0bjoiQWxlcnRzIiwK"
    "IHdhbEtpY2s6IkJhbGFuY2UiLCB3YWxTdWI6IlBheSBieSBVUEkg4oCUIHRhcCBWZXJpZnkgYWZ0"
    "ZXIgcGF5aW5nIGFuZCB5b3VyIHdhbGxldCBpcyBjcmVkaXRlZCBpbiBzZWNvbmRzLiIsCiB3YWxB"
    "ZGQ6IkFkZCBGdW5kcyIsIHBheVRpdGxlOiJQYXltZW50IERldGFpbHMiLCBwYXlTdWI6IkFkbWlu"
    "IHNldHMgdGhlIFVQSSBoYW5kbGUg4oCUIG5vdGhpbmcgaXMgaGFyZC1jb2RlZCBpbiB0aGUgYXBw"
    "LiIsCiBwYXlQcmVmaXg6IlBBWSIsIHBheVdpbmRvdzoiNSIsCiBzaGVldFRpdGxlOiJBZGQiLCBz"
    "aGVldFN1YjoiUGF5IHRoZSBleGFjdCBhbW91bnQsIHRoZW4gdGFwIFZlcmlmeSBQYXltZW50LiBU"
    "aGUgcmVxdWVzdCBleHBpcmVzIGluIHttfSBtaW51dGVzLiIsCiBwYXlCdG46IlBheSBOb3ciLCB2"
    "ZXJpZnlCdG46IlZlcmlmeSBQYXltZW50IiwgdXRyQnRuOiJOb3QgZGV0ZWN0ZWQ/IFN1Ym1pdCBV"
    "VFIiLAogc3RlcDE6IlBheW1lbnQgcmVxdWVzdCBjcmVhdGVkIiwgc3RlcDI6IlBheSB0aGUgZXhh"
    "Y3QgYW1vdW50IGluIGFueSBVUEkgYXBwIiwKIHN0ZXAzOiJUYXAgVmVyaWZ5IOKAlCB3ZSBjb25m"
    "aXJtIHlvdXIgcGF5bWVudCIsIHN0ZXA0OiJXYWxsZXQgY3JlZGl0ZWQiLAogbXNnQ2hlY2tpbmc6"
    "IkNoZWNraW5nIHlvdXIgcGF5bWVudOKApiIsIG1zZ09rOiJWZXJpZmllZCDigJQgd2FsbGV0IGNy"
    "ZWRpdGVkIiwKIG1zZ05vbmU6Ik5vIHBheW1lbnQgZm91bmQgeWV0LiBQYXkgZmlyc3QsIHRoZW4g"
    "dGFwIFZlcmlmeS4iLCBtc2dFeHBpcmVkOiJSZXF1ZXN0IGV4cGlyZWQg4oCUIGNyZWF0ZSBhIG5l"
    "dyBvbmUuIiwKIG5hdlNob3A6IlNob3AiLCBuYXZPcmRlcnM6Ik9yZGVycyIsIG5hdkVhcm46IkVh"
    "cm4iLCBuYXZXYWxsZXQ6IldhbGxldCIsIG5hdllvdToiWW91Igp9Owpjb25zdCBDTVMwPU9iamVj"
    "dC5hc3NpZ24oe30sQ01TKTsKY29uc3QgQ01TTUVUQT1bCiBbIlN0b3JlIGhlYWRlciIsW1sic3Rv"
    "cmVOYW1lIiwiU3RvcmUgbmFtZSJdLFsic3RvcmVUYWciLCJUYWdsaW5lIl1dXSwKIFsiU2Nyb2xs"
    "aW5nIHRpY2tlciIsW1sidGljazEiLCJUaWNrZXIgMSJdLFsidGljazIiLCJUaWNrZXIgMiJdLFsi"
    "dGljazMiLCJUaWNrZXIgMyJdLFsidGljazQiLCJUaWNrZXIgNCJdXV0sCiBbIkhlcm8gc2VjdGlv"
    "biIsW1siaGVyb0tpY2siLCJTbWFsbCBsYWJlbCBhYm92ZSB0aXRsZSJdLFsiaGVyb0gxIiwiQmln"
    "IHRpdGxlIl0sWyJoZXJvU3ViIiwiUGFyYWdyYXBoIHVuZGVyIHRoZSB0aXRsZSJdLFsiaGVyb0J0"
    "bjEiLCJMZWZ0IGJ1dHRvbiJdLFsiaGVyb0J0bjIiLCJSaWdodCBidXR0b24iXV1dLAogWyJGZWF0"
    "dXJlIGJhZGdlcyIsW1siZmVhdDEiLCJCYWRnZSAxIHZhbHVlIl0sWyJmZWF0MXMiLCJCYWRnZSAx"
    "IGNhcHRpb24iXSxbImZlYXQyIiwiQmFkZ2UgMiB2YWx1ZSJdLFsiZmVhdDJzIiwiQmFkZ2UgMiBj"
    "YXB0aW9uIl0sWyJmZWF0MyIsIkJhZGdlIDMgdmFsdWUiXSxbImZlYXQzcyIsIkJhZGdlIDMgY2Fw"
    "dGlvbiJdXV0sCiBbIkZsYXNoIHNhbGUgc3RyaXAiLFtbImZsYXNoUGlsbCIsIlBpbGwgdGV4dCJd"
    "LFsiZmxhc2hUZXh0IiwiQ291bnRkb3duIGxhYmVsIl0sWyJmbGFzaEJ0biIsIkJ1dHRvbiJdXV0s"
    "CiBbIlJvdGF0aW5nIGRlYWxzIGNhcmQiLFtbIndoZWVsS2ljayIsIlNtYWxsIGxhYmVsIl0sWyJ3"
    "aGVlbFRpdGxlIiwiSGVhZGxpbmUiXSxbIndoZWVsV29yZHMiLCJNYXJxdWVlIHdvcmRzIChjb21t"
    "YSBzZXBhcmF0ZWQpIl1dXSwKIFsiU2hvcCBsaXN0IixbWyJzaG9wSGVhZCIsIlNlY3Rpb24gaGVh"
    "ZGluZyJdLFsic2hvcFNvcnQiLCJTb3J0IGxpbmsiXSxbInNlYXJjaFBoIiwiU2VhcmNoIHBsYWNl"
    "aG9sZGVyIl0sWyJhbGVydHNCdG4iLCJBbGVydHMgYnV0dG9uIl1dXSwKIFsiV2FsbGV0IHNjcmVl"
    "biIsW1sid2FsS2ljayIsIkxhYmVsIGFib3ZlIGJhbGFuY2UiXSxbIndhbFN1YiIsIkJhbGFuY2Ug"
    "ZGVzY3JpcHRpb24iXSxbIndhbEFkZCIsIkFkZCBmdW5kcyBidXR0b24iXSxbInBheVRpdGxlIiwi"
    "UGF5bWVudCBjYXJkIHRpdGxlIl0sWyJwYXlTdWIiLCJQYXltZW50IGNhcmQgZGVzY3JpcHRpb24i"
    "XV1dLAogWyJQYXltZW50IHNoZWV0IixbWyJzaGVldFRpdGxlIiwiU2hlZXQgdGl0bGUgcHJlZml4"
    "Il0sWyJzaGVldFN1YiIsIlNoZWV0IGRlc2NyaXB0aW9uICh7bX0gPSBtaW51dGVzKSJdLFsicGF5"
    "QnRuIiwiUGF5IGJ1dHRvbiJdLFsidmVyaWZ5QnRuIiwiVmVyaWZ5IGJ1dHRvbiJdLFsidXRyQnRu"
    "IiwiTWFudWFsIGZhbGxiYWNrIGJ1dHRvbiJdLFsicGF5UHJlZml4IiwiUmVmZXJlbmNlIHByZWZp"
    "eCJdLFsicGF5V2luZG93IiwiUGF5bWVudCB3aW5kb3cgKG1pbnV0ZXMpIl1dXSwKIFsiUGF5bWVu"
    "dCBzdGVwcyIsW1sic3RlcDEiLCJTdGVwIDEiXSxbInN0ZXAyIiwiU3RlcCAyIl0sWyJzdGVwMyIs"
    "IlN0ZXAgMyJdLFsic3RlcDQiLCJTdGVwIDQiXV1dLAogWyJQYXltZW50IG1lc3NhZ2VzIixbWyJt"
    "c2dDaGVja2luZyIsIldoaWxlIGNoZWNraW5nIl0sWyJtc2dPayIsIlZlcmlmaWVkIl0sWyJtc2dO"
    "b25lIiwiTm90aGluZyBmb3VuZCJdLFsibXNnRXhwaXJlZCIsIkV4cGlyZWQiXV1dLAogWyJCb3R0"
    "b20gbmF2aWdhdGlvbiIsW1sibmF2U2hvcCIsIlRhYiAxIl0sWyJuYXZPcmRlcnMiLCJUYWIgMiJd"
    "LFsibmF2RWFybiIsIlRhYiAzIl0sWyJuYXZXYWxsZXQiLCJUYWIgNCJdLFsibmF2WW91IiwiVGFi"
    "IDUiXV1dCl07Ci8qIOKVkOKVkCBGRUFUVVJFIEZMQUdTIOKAlCBldmVyeSBmZWF0dXJlIGJlbG93"
    "IGlzIG9mZi1hYmxlOyB3aGVuIG9mZiBub3RoaW5nIHJlbmRlcnMg4pWQ4pWQICovCmNvbnN0IEZH"
    "Uk9VUFM9WwogWyJQYXltZW50cyAmIHdhbGxldCIsWwogIFsiY3J5cHRvIiwiQ3J5cHRvIC8gVVNE"
    "VCB0b3AtdXAiLCJBY2NlcHQgVVNEVC1UUkMyMCwgQlRDIGV0Yy4gd2l0aCBhdXRvLWNvbmZpcm0g"
    "ZnJvbSB0aGUgZXhwbG9yZXIuIl0sCiAgWyJ1cGlSb3RhdGUiLCJNdWx0aXBsZSBVUEkgSURzICsg"
    "cm90YXRpb24iLCJSb3RhdGUgaGFuZGxlcyBwZXIgcGF5bWVudCBvciBhdXRvLXN3aXRjaCB3aGVu"
    "IG9uZSBpcyBsaW1pdGVkLiJdLAogIFsiYXV0b1JlZnVuZCIsIkF1dG8tcmVmdW5kIHRvIHdhbGxl"
    "dCIsIkRlYWQgYWNjb3VudCBpbnNpZGUgdGhlIHdhcnJhbnR5IHdpbmRvdyBpcyByZWZ1bmRlZCB3"
    "aXRob3V0IHN1cHBvcnQuIl0sCiAgWyJzcGxpdFBheSIsIlNwbGl0IHBheW1lbnQiLCJQYXkgcGFy"
    "dCBmcm9tIGJhbGFuY2UgYW5kIHRoZSByZXN0IGJ5IFVQSSBpbiBvbmUgZmxvdy4iXSwKICBbImxl"
    "ZGdlciIsIldhbGxldCBsZWRnZXIgc3RhdGVtZW50IiwiRnVsbCBjcmVkaXQvZGViaXQgaGlzdG9y"
    "eSB3aXRoIHJ1bm5pbmcgYmFsYW5jZSBhbmQgQ1NWIGV4cG9ydC4iXSwKICBbImRlcExpbWl0cyIs"
    "Ik1pbiAvIG1heCBkZXBvc2l0IHJ1bGVzIiwiR2xvYmFsIG9yIHBlci11c2VyIGNhcHMgb24gYSBz"
    "aW5nbGUgdG9wLXVwLiJdLAogIFsiZGVwQm9udXMiLCJEZXBvc2l0IGJvbnVzIHRpZXJzIiwiQWRk"
    "IOKCuTUwMCBnZXQgNSUgZXh0cmEg4oCUIGNvbmZpZ3VyYWJsZSBzbGFicywgYXV0by1hcHBsaWVk"
    "LiJdLAogIFsid3JvbmdBbXQiLCJXcm9uZy1hbW91bnQgaGFuZGxpbmciLCJDcmVkaXQgd2hhdGV2"
    "ZXIgd2FzIGFjdHVhbGx5IHBhaWQgYW5kIGZsYWcgdGhlIG1pc21hdGNoLiJdXV0sCiBbIkNhdGFs"
    "b2cgJiBzZWxsaW5nIixbCiAgWyJjYXJ0IiwiU2hvcHBpbmcgY2FydCAvIGJ1bGsgYnV5IiwiTWl4"
    "IGNvdW50cmllcyBpbiBvbmUgY2hlY2tvdXQsIG9uZSBjb25maXJtYXRpb24sIG9uZSBkZWJpdC4i"
    "XSwKICBbImJ1bGtEaXNjIiwiQnVsayBwdXJjaGFzZSBkaXNjb3VudCIsIkF1dG9tYXRpYyBwcmlj"
    "ZSBicmVhayBhdCA1IC8gMTAgLyAyNSB1bml0cy4iXSwKICBbIndpc2hsaXN0IiwiV2lzaGxpc3Qg"
    "LyBmYXZvdXJpdGVzIiwiU2F2ZSBjb3VudHJpZXMgYW5kIHJlYnV5IGluIG9uZSB0YXAuIl0sCiAg"
    "WyJkeW5QcmljZSIsIkR5bmFtaWMgcHJpY2luZyIsIlByaWNlIG1vdmVzIHdpdGggc3RvY2sgbGV2"
    "ZWwsIGFnZSB0aWVyIG9yIHRpbWUgb2YgZGF5LiJdLAogIFsiZmxhc2hTYWxlIiwiRmxhc2ggc2Fs"
    "ZSIsIlRpbWVkIGRpc2NvdW50IHdpdGggYSBsaXZlIGNvdW50ZG93biBvbiB0aGUgc3RvcmVmcm9u"
    "dC4iXSwKICBbImNvdXBvbnMiLCJDb3Vwb24gY29kZXMiLCJQZXJjZW50YWdlIG9mZiBhdCBjaGVj"
    "a291dCB3aXRoIGxpbWl0cyBhbmQgZXhwaXJ5LiJdLAogIFsidmFyaWFudHMiLCJBY2NvdW50IHZh"
    "cmlhbnRzIiwiU3BhbS1ibG9jaywgMkZBLCB1c2VybmFtZSwgbWFpbCDigJQgZmlsdGVyYWJsZSBh"
    "bmQgcHJpY2VkIGFwYXJ0LiJdLAogIFsiaGVhbHRoQ2hlY2siLCJTdG9jayBoZWFsdGggY2hlY2si"
    "LCJTZXNzaW9uIGlzIHZhbGlkYXRlZCBsaXZlIGJlZm9yZSBpdCBpcyBzb2xkLiJdXV0sCiBbIk9y"
    "ZGVycyAmIGFmdGVyLXNhbGUiLFsKICBbInRpbWVsaW5lIiwiT3JkZXIgc3RhdHVzIHRpbWVsaW5l"
    "IiwiUGVuZGluZywgZGVsaXZlcmVkLCBPVFAgcmVjZWl2ZWQsIGNsb3NlZCDigJQgdmlzaWJsZSB0"
    "byBib3RoIHNpZGVzLiJdLAogIFsid2FycmFudHkiLCJSZXBsYWNlbWVudCAvIHdhcnJhbnR5Iiwi"
    "UmVwb3J0IGEgcHJvYmxlbSBpbnNpZGUgWCBob3VycyBmb3IgYW4gYXV0by1yZXBsYWNlbWVudC4i"
    "XSwKICBbInJlZG93bmxvYWQiLCJSZS1kb3dubG9hZCBkZXRhaWxzIiwiRmV0Y2ggYSBwYXN0IG9y"
    "ZGVyJ3MgZGV0YWlscyBhZ2FpbiwgcmF0ZS1saW1pdGVkLiJdLAogIFsiZGlzcHV0ZSIsIkRpc3B1"
    "dGUgZmxvdyIsIlJlYXNvbiBwaWNrZXIgcGx1cyBldmlkZW5jZSBpbnN0ZWFkIG9mIGZyZWUtdGV4"
    "dCB0aWNrZXRzLiJdLAogIFsic2Vzc2lvbkZpbGUiLCJTZXNzaW9uIGZpbGUgZGVsaXZlcnkiLCJT"
    "ZW5kIC5zZXNzaW9uIC8gSlNPTiBhcyBhIGRvY3VtZW50IHdpdGggYSBzZWxmLWRlc3RydWN0IHRp"
    "bWVyLiJdLAogIFsicmVjZWlwdHMiLCJPcmRlciByZWNlaXB0cyIsIkF1dG8tZ2VuZXJhdGVkIGlu"
    "dm9pY2UgcGVyIHB1cmNoYXNlLiJdXV0sCiBbIlRydXN0ICYgc29jaWFsIHByb29mIixbCiAgWyJy"
    "ZXZpZXdzIiwiUmV2aWV3cyAmIHN0YXIgcmF0aW5ncyIsIkJ1eWVyIHJhdGVzIGFmdGVyIGRlbGl2"
    "ZXJ5OyBhdmVyYWdlcyBzaG93IG9uIGV2ZXJ5IGNvdW50cnkuIl0sCiAgWyJzYWxlc0ZlZWQiLCJM"
    "aXZlIHNhbGVzIGZlZWQiLCJSZWNlbnQgc2FsZXMgc2hvd24gaW4tYXBwIGFuZCBhdXRvLXBvc3Rl"
    "ZCB0byB5b3VyIGNoYW5uZWwuIl0sCiAgWyJsZWFkZXJib2FyZCIsIkxlYWRlcmJvYXJkIiwiVG9w"
    "IGJ1eWVycyBhbmQgcmVmZXJyZXJzIG9mIHRoZSB3ZWVrLCB3aXRoIGEgcHJpemUuIl0sCiAgWyJi"
    "dXllckJhZGdlIiwiVmVyaWZpZWQgYnV5ZXIgYmFkZ2UiLCJCYWRnZSBhbmQgcGVya3MgYWZ0ZXIg"
    "TiBjb21wbGV0ZWQgb3JkZXJzLiJdXV0sCiBbIkdyb3d0aCAmIHJldGVudGlvbiIsWwogIFsicmVm"
    "TDIiLCJNdWx0aS1sZXZlbCByZWZlcnJhbCIsIkxldmVsIDEgYW5kIGxldmVsIDIgY29tbWlzc2lv"
    "biBpbnN0ZWFkIG9mIGEgZmxhdCByYXRlLiJdLAogIFsicmVmRGFzaCIsIlJlZmVycmFsIGRhc2hi"
    "b2FyZCIsIldobyBqb2luZWQsIHdobyBkZXBvc2l0ZWQgYW5kIHdoYXQgZWFjaCBpbnZpdGUgZWFy"
    "bmVkLiJdLAogIFsic3RyZWFrIiwiQ2hlY2staW4gc3RyZWFrIHJld2FyZHMiLCJFc2NhbGF0aW5n"
    "IGRhaWx5IHJld2FyZCB0aGF0IHJlc2V0cyB3aGVuIGJyb2tlbi4iXSwKICBbImxveWFsdHkiLCJM"
    "b3lhbHR5IHBvaW50cyIsIkVhcm4gcGVyIOKCuSBzcGVudCwgcmVkZWVtIGZvciBiYWxhbmNlIG9y"
    "IGEgZnJlZSBhY2NvdW50LiJdLAogIFsid2luYmFjayIsIlJlLWVuZ2FnZW1lbnQgRE1zIiwiQXV0"
    "byBtZXNzYWdlIHVzZXJzIGluYWN0aXZlIGZvciA3IC8gMzAgZGF5cy4iXSwKICBbImFiYW5kb25S"
    "ZW1pbmQiLCJBYmFuZG9uZWQtZGVwb3NpdCByZW1pbmRlciIsIk51ZGdlIGJlZm9yZSBhbiB1bmZp"
    "bmlzaGVkIHBheW1lbnQgZXhwaXJlcy4iXSwKICBbInNjaGVkQnJvYWRjYXN0IiwiU2NoZWR1bGVk"
    "ICYgc2VnbWVudGVkIGJyb2FkY2FzdHMiLCJRdWV1ZSBmb3IgbGF0ZXIsIHRhcmdldCBidXllcnMs"
    "IG5vbi1idXllcnMgb3IgYmlnIHNwZW5kZXJzLiJdXV0sCiBbIkFkbWluICYgb3BlcmF0aW9ucyIs"
    "WwogIFsiYWRtaW5Sb2xlcyIsIkFkbWluIHJvbGVzIiwiT3duZXIgLyBtYW5hZ2VyIC8gc3VwcG9y"
    "dCB3aXRoIHNlcGFyYXRlIHBlcm1pc3Npb25zLiJdLAogIFsiYXVkaXRMb2ciLCJBdWRpdCBsb2ci"
    "LCJFdmVyeSBhZG1pbiBhY3Rpb24gbG9nZ2VkIHdpdGggdGltZXN0YW1wIGFuZCB1bmRvLiJdLAog"
    "IFsiY29udGVudEVkaXRvciIsIkluLWJvdCBjb250ZW50IGVkaXRvciIsIkVkaXQgZXZlcnkgbWVz"
    "c2FnZSBhbmQgYnV0dG9uIHdpdGhvdXQgdG91Y2hpbmcgY29kZS4iXSwKICBbImF1dG9CYWNrdXAi"
    "LCJTY2hlZHVsZWQgYXV0by1iYWNrdXAiLCJEYXRhYmFzZSB0byBhIHByaXZhdGUgY2hhbm5lbCBl"
    "dmVyeSBOIGhvdXJzLCBvbmUtdGFwIHJlc3RvcmUuIl0sCiAgWyJidWxrVXBsb2FkIiwiQnVsayB1"
    "cGxvYWQgd2l0aCB2YWxpZGF0aW9uIiwiVmFsaWRhdGUgZWFjaCBzZXNzaW9uLCByZXBvcnQgZGVh"
    "ZCBvciBkdXBsaWNhdGUsIGltcG9ydCB0aGUgcmVzdC4iXSwKICBbImR1cERldGVjdCIsIkR1cGxp"
    "Y2F0ZSBkZXRlY3Rpb24iLCJSZWplY3QgYSBwaG9uZSBvciBzZXNzaW9uIGFscmVhZHkgaW4gdGhl"
    "IGRhdGFiYXNlLiJdLAogIFsibG93U3RvY2siLCJMb3ctc3RvY2sgYWxlcnRzIiwiUGluZyB3aGVu"
    "IGEgY291bnRyeSBkcm9wcyBiZWxvdyBpdHMgb3duIHRocmVzaG9sZC4iXSwKICBbInNlbGxIb3Vy"
    "cyIsIlNlbGxpbmctaG91cnMgc2NoZWR1bGUiLCJBdXRvLWhpZGUgY291bnRyaWVzIG91dHNpZGUg"
    "dGhlaXIgc2VsbGluZyB3aW5kb3cuIl0sCiAgWyJzYW5kYm94IiwiVGVzdCBtb2RlIC8gc2FuZGJv"
    "eCIsIlJ1biB0aGUgd2hvbGUgZmxvdyBvbiBmYWtlIG1vbmV5IGJlZm9yZSBnb2luZyBsaXZlLiJd"
    "LAogIFsiaTE4biIsIk11bHRpLWxhbmd1YWdlIiwiUGVyLXVzZXIgSGluZGkgLyBFbmdsaXNoIHN3"
    "aXRjaCBhY3Jvc3MgZXZlcnkgc3RyaW5nLiJdXV0sCiBbIlNlY3VyaXR5ICYgYW50aS1hYnVzZSIs"
    "WwogIFsicmF0ZUxpbWl0IiwiUmF0ZSBsaW1pdGluZyIsIlBlci11c2VyIGFjdGlvbiBsaW1pdHMg"
    "c28gbm9ib2R5IGNhbiBoYW1tZXIgdGhlIGJvdC4iXSwKICBbIm11bHRpQWNjIiwiTXVsdGktYWNj"
    "b3VudCBkZXRlY3Rpb24iLCJDYXRjaCByZWZlcnJhbCBmYXJtaW5nIGFuZCBmbGFnIHN1c3BpY2lv"
    "dXMgc2lnbnVwcy4iXSwKICBbInZlbG9jaXR5IiwiUHVyY2hhc2UgdmVsb2NpdHkgYWxlcnRzIiwi"
    "RmxhZyBhYm5vcm1hbGx5IGZhc3QgYnV5aW5nIOKAlCB0eXBpY2FsIG9mIHN0b2xlbiB0b3AtdXBz"
    "LiJdLAogIFsiYWRtaW4yZmEiLCJBZG1pbiAyRkEiLCJQSU4gb3IgVE9UUCBiZWZvcmUgYmFsYW5j"
    "ZSBlZGl0cywgYnJvYWRjYXN0cyBvciB3aXBlcy4iXSwKICBbImF1dG9CYW4iLCJBdXRvLWJhbiBy"
    "dWxlcyIsIkJhbiBvbiBjaGFyZ2ViYWNrLCBmYWtlIFVUUiBvciByZXBlYXRlZCBmYWlsZWQgdmVy"
    "aWZpY2F0aW9uLiJdLAogIFsiYmxhY2tsaXN0IiwiQmxhY2tsaXN0IiwiUGVybWFuZW50bHkgYmxv"
    "Y2sgdXNlciBJRHMsIHBob25lIG51bWJlcnMgYW5kIFVQSSByZWZlcmVuY2VzLiJdLAogIFsiZW5j"
    "cnlwdCIsIlNlc3Npb24gZW5jcnlwdGlvbiBhdCByZXN0IiwiQSBsZWFrZWQgZGF0YWJhc2UgZmls"
    "ZSBpcyB1c2VsZXNzIHdpdGhvdXQgdGhlIGtleS4iXV1dLAogWyJBbmFseXRpY3MiLFsKICBbInJl"
    "dkNoYXJ0IiwiUmV2ZW51ZSBkYXNoYm9hcmQiLCJEYWlseSwgd2Vla2x5IGFuZCBtb250aGx5IHJl"
    "dmVudWUgYW5kIHByb2ZpdCBhcyBjaGFydHMuIl0sCiAgWyJmdW5uZWwiLCJDb252ZXJzaW9uIGZ1"
    "bm5lbCIsIlNlZSB3aGVyZSB1c2VycyBkcm9wIG9mZiBiZXR3ZWVuIGpvaW5pbmcgYW5kIGJ1eWlu"
    "Zy4iXSwKICBbInByb2ZpdFJlcCIsIlBlci1jb3VudHJ5IHByb2ZpdCIsIkNvc3QgdnMgc2FsZSBw"
    "cmljZSB2cyB2b2x1bWUgZm9yIGV2ZXJ5IGNvdW50cnkuIl0sCiAgWyJjbHYiLCJDdXN0b21lciBs"
    "aWZldGltZSB2YWx1ZSIsIlNwZW5kLCBmcmVxdWVuY3kgYW5kIGNodXJuIHJpc2sgcGVyIHVzZXIu"
    "Il0sCiAgWyJkaWdlc3QiLCJEYWlseSBkaWdlc3QiLCJFbmQtb2YtZGF5IHN1bW1hcnkgcHVzaGVk"
    "IHRvIHRoZSBhZG1pbiBjaGF0LiJdXV0sCiBbIlF1YWxpdHkgb2YgbGlmZSIsWwogIFsiaW5saW5l"
    "U2VhcmNoIiwiSW5saW5lIHNlYXJjaCIsIlR5cGUgQHlvdXJib3QgaW5kaWEgaW4gYW55IGNoYXQg"
    "dG8gZmluZCBhbmQgYnV5LiJdLAogIFsic2xhc2hDbWRzIiwiU2xhc2ggY29tbWFuZHMgbWVudSIs"
    "Ii9zdGFydCAvYnV5IC9iYWxhbmNlIC9vcmRlcnMgcmVnaXN0ZXJlZCB3aXRoIFRlbGVncmFtLiJd"
    "LAogIFsic3RvY2tDb3VudGVyIiwiTGl2ZSBzdG9jayBjb3VudGVyIiwiQXV0by11cGRhdGluZyBw"
    "aW5uZWQgcG9zdCBhbmQgaW4tYXBwIGxpdmUgY291bnQuIl0sCiAgWyJvbmJvYXJkaW5nIiwiT25i"
    "b2FyZGluZyB0b3VyIiwiVGhyZWUtc3RlcCB3YWxrdGhyb3VnaCBmb3IgZmlyc3QtdGltZSB1c2Vy"
    "cy4iXSwKICBbIm5vdGlmUHJlZnMiLCJOb3RpZmljYXRpb24gcHJlZmVyZW5jZXMiLCJVc2VycyBw"
    "aWNrIHdoaWNoIGFsZXJ0cyB0aGV5IHJlY2VpdmUuIl0sCiAgWyJwdXNoTm90aWYiLCJNaW5pIGFw"
    "cCBwdXNoIiwiTWluaSBhcHAgYWN0aW9ucyBub3RpZnkgdGhlIHVzZXIgdGhyb3VnaCB0aGUgYm90"
    "LiJdXV0KXTsKY29uc3QgRkVBVD17fTsKRkdST1VQUy5mb3JFYWNoKChbZyxyb3dzXSk9PnJvd3Mu"
    "Zm9yRWFjaChyPT5GRUFUW3JbMF1dPXRydWUpKTsKY29uc3QgRkVBVDA9T2JqZWN0LmFzc2lnbih7"
    "fSxGRUFUKTsKY29uc3QgRz0oayxoKT0+RkVBVFtrXT9oOiIiOwpjb25zdCBhbnlGPSguLi5rcyk9"
    "PmtzLnNvbWUoaz0+RkVBVFtrXSk7CmxldCBDQVJUPVtdLFdJU0g9W107CmxldCBSRVZJRVdTPXtJ"
    "bmRpYTpbNC44LDQxMl0sSW5kb25lc2lhOls0LjYsMTgwXSxCcmF6aWw6WzQuNyw5Nl0sTmlnZXJp"
    "YTpbNC40LDU0XSxWaWV0bmFtOls0LjksMjMzXSxQaGlsaXBwaW5lczpbNC41LDcxXX07CmxldCBG"
    "RUVEPVtbIvCfh67wn4ezIiwiSW5kaWEiLCLigrkzNSIsIjIgbWluIGFnbyJdLFsi8J+Hu/Cfh7Mi"
    "LCJWaWV0bmFtIiwi4oK5MjYiLCI2IG1pbiBhZ28iXSxbIvCfh6fwn4e3IiwiQnJhemlsIiwi4oK5"
    "NDQiLCIxMSBtaW4gYWdvIl1dOwpsZXQgQURNPXsKICBrcGk6e3Jldjo0ODIwLG9yZGVyczo2MSx1"
    "c2VyczoxMjg0LGF1dG86Mzh9LAogIGJhcnM6WzQyLDYxLDM4LDc0LDU1LDg4LDY3XSwKICBzdG9j"
    "azpbXSxvcmRlcnM6W10sZGlzcHV0ZXM6W10sdXNlcnM6W10sYWRtaW5zOltdLGNoYW5uZWxzOltd"
    "LAogIGxvZ3M6e21hc3Rlcjp0cnVlLGNoYW5uZWxzOltdLGV2ZW50czpbXX0sY291cG9uczpbXSxh"
    "dWRpdDpbXSxyZXZpZXdzOltdLGRlcG9zaXRzOltdLGxvZ2ZlZWQ6W10sCiAgcGF5Ont1cGk6IiIs"
    "cGF5ZWU6IiIsZ21haWw6IiIsZ21haWxfc2V0OmZhbHNlLHdpbmRvdzo1LHByZWZpeDoiUEFZIixt"
    "aW46MTAsbWF4OjIwMDAwLGF1dG9kZXA6MCxwZW5kaW5nOjB9LAogIHNlY3VyaXR5OntyYXRlOjIw"
    "LHZlbG9jaXR5OjUsYmxhY2tsaXN0OjAsYmFubmVkOjB9LAogIG1hcmtldDp7Zmxhc2g6MjAsZmxh"
    "c2hfb246ZmFsc2UscmVmMTo1LHJlZjI6MSxnb2FsOjAsbW9udGg6MH0sCiAgc2V0dGluZ3M6e3N0"
    "b3JlOiIiLHRhZ2xpbmU6IiIsc3VwcG9ydDoiIixwcmljZTozNSxkYWlseTowLjQsYXBwOiIiLG1h"
    "aW50ZW5hbmNlOmZhbHNlLHNhbmRib3g6ZmFsc2V9LAogIGFuYWx5dGljczp7ZnVubmVsOnt1c2Vy"
    "czowLGRlcDowLGJ1eTowfSxjb3VudHJ5OltdLHN0b2NrX3RvdGFsOjAsc29sZF90b3RhbDowfQp9"
    "OwpmdW5jdGlvbiBhZG1OdW0obil7cmV0dXJuIChufHwwKS50b0xvY2FsZVN0cmluZygiZW4tSU4i"
    "KTt9CmxldCBFQVJOPXtzdHJlYWs6NCxkYWlseTowLjQwLGNsYWltZWQ6ZmFsc2UscG9pbnRzOjM0"
    "MCxwb2ludHNXb3J0aDozNCxpbnZpdGVzOjEyLAogIGwxOjUyLjQwLGwyOjEyLjQwLGVhcm5lZDo2"
    "NC44MCxsaW5rOiJ0Lm1lL3lvdXJfYm90P3N0YXJ0PXJlZl9kZW1vIiwKICByZWNlbnQ6W1sidXNl"
    "cl8xMDQxIiwiZGVwb3NpdGVkIOKCuTIwMCIsIivigrkyLjAwIl0sWyJ1c2VyXzEwMzgiLCJqb2lu"
    "ZWQiLCLigJQiXSxbInVzZXJfMTAyMiIsImRlcG9zaXRlZCDigrk1MDAiLCIr4oK5NS4wMCJdXSwK"
    "ICBsZWFkZXJzOltbIjEiLCJAcmFodWwiLCLigrk0LDgyMCJdLFsiMiIsIkBhbWFuIiwi4oK5Mywx"
    "NDAiXSxbIjMiLCJAeW91Iiwi4oK5MSw5ODAiXV0sCiAgdGFza3M6W1siSm9pbiB0aGUgbWFpbiBj"
    "aGFubmVsIiwi4oK5MS4wMCIsMV0sWyJQbGFjZSB5b3VyIGZpcnN0IG9yZGVyIiwi4oK5Mi4wMCIs"
    "MV0sWyJJbnZpdGUgMyBmcmllbmRzIiwi4oK5NS4wMCIsMF1dfTsKZnVuY3Rpb24gc3RhcnMoYyl7"
    "Y29uc3Qgcj0oUkVWSUVXU1tjXXx8WzQuNywyMF0pWzBdLG49KFJFVklFV1NbY118fFs0LjcsMjBd"
    "KVsxXTsKICByZXR1cm4gYDxzcGFuIGNsYXNzPSJzdGFycyI+JHtbMSwyLDMsNCw1XS5tYXAoaT0+"
    "aWMoaTw9TWF0aC5yb3VuZChyKT8ic3RhciI6InN0YXIiKSkuam9pbigiIil9PGI+JHtyfSAoJHtu"
    "fSk8L2I+PC9zcGFuPmA7fQpmdW5jdGlvbiBhcHBseUZlYXR1cmVzKCl7CiAgY29uc3QgY2M9ZG9j"
    "dW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNhcnRjaGlwIik7CiAgaWYoY2Mpe2NjLmNsYXNzTGlzdC50"
    "b2dnbGUoImhpZGRlbiIsIUZFQVQuY2FydCk7ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNhcnRu"
    "IikudGV4dENvbnRlbnQ9Q0FSVC5sZW5ndGg7fQogIGNvbnN0IGVhcm49YW55Rigic3RyZWFrIiwi"
    "cmVmRGFzaCIsInJlZkwyIiwibG95YWx0eSIsImxlYWRlcmJvYXJkIiwid2luYmFjayIpOwogIGNv"
    "bnN0IGViPWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoJyN0YWJzIGJ1dHRvbltkYXRhLXRhYj0iZWFy"
    "biJdJyk7CiAgaWYoZWIpe2ViLmNsYXNzTGlzdC50b2dnbGUoImhpZGRlbiIsIWVhcm4pOwogICAg"
    "aWYoIWVhcm4mJlRBQj09PSJlYXJuIil7VEFCPSJzaG9wIjtkb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "QWxsKCIjdGFicyBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnRvZ2dsZSgib24iLHgu"
    "ZGF0YXNldC50YWI9PT0ic2hvcCIpKTtyZW5kZXIoKTt9fQogIGNvbnN0IGFtYXA9e21hcmtldDph"
    "bnlGKCJjb3Vwb25zIiwiZmxhc2hTYWxlIiwic2NoZWRCcm9hZGNhc3QiLCJ3aW5iYWNrIiwic2Fs"
    "ZXNGZWVkIiwibGVhZGVyYm9hcmQiLCJsb3lhbHR5IiwicmVmRGFzaCIsInJlZkwyIiwiYWJhbmRv"
    "blJlbWluZCIpLAogICAgYW5hbHl0aWNzOmFueUYoInJldkNoYXJ0IiwiZnVubmVsIiwicHJvZml0"
    "UmVwIiwiY2x2IiwiZGlnZXN0IiksCiAgICBzZWN1cml0eTphbnlGKCJyYXRlTGltaXQiLCJtdWx0"
    "aUFjYyIsInZlbG9jaXR5IiwiYWRtaW4yZmEiLCJhdXRvQmFuIiwiYmxhY2tsaXN0IiwiZW5jcnlw"
    "dCIpLAogICAgY29udGVudDpGRUFULmNvbnRlbnRFZGl0b3J9OwogIE9iamVjdC5lbnRyaWVzKGFt"
    "YXApLmZvckVhY2goKFtrLHZdKT0+e2NvbnN0IGI9ZG9jdW1lbnQucXVlcnlTZWxlY3RvcignI2Fz"
    "dWIgYnV0dG9uW2RhdGEtYT0iJytrKyciXScpOwogICAgaWYoYil7Yi5jbGFzc0xpc3QudG9nZ2xl"
    "KCJoaWRkZW4iLCF2KTsKICAgICAgaWYoIXYmJkFUQUI9PT1rKXtBVEFCPSJkYXNoIjtkb2N1bWVu"
    "dC5xdWVyeVNlbGVjdG9yQWxsKCIjYXN1YiBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0"
    "LnRvZ2dsZSgib24iLHguZGF0YXNldC5hPT09ImRhc2giKSk7cmVuZGVyQWRtaW4oKTt9fX0pOwog"
    "IGlmKHdpbmRvdy5fX2RvY2tQbGFjZSlyZXF1ZXN0QW5pbWF0aW9uRnJhbWUoKCk9PndpbmRvdy5f"
    "X2RvY2tQbGFjZSh0cnVlKSk7CiAgaWYod2luZG93Ll9fdnRhYlBsYWNlKXJlcXVlc3RBbmltYXRp"
    "b25GcmFtZSgoKT0+d2luZG93Ll9fdnRhYlBsYWNlKHRydWUpKTsKfQpmdW5jdGlvbiBhcHBseUNN"
    "UygpewogY29uc3QgZz1pZD0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoaWQpOwogaWYoZygiYm5h"
    "bWUiKSlnKCJibmFtZSIpLnRleHRDb250ZW50PUNNUy5zdG9yZU5hbWU7CiBpZihnKCJhYm5hbWUi"
    "KSlnKCJhYm5hbWUiKS50ZXh0Q29udGVudD1DTVMuc3RvcmVOYW1lOwogaWYoQ01TLnN0b3JlTmFt"
    "ZSl7Q0ZHLm5hbWU9Q01TLnN0b3JlTmFtZTt9CiBpZihDTVMuc3RvcmVUYWcpQ0ZHLnRhZz1DTVMu"
    "c3RvcmVUYWc7CiBwYWludEJyYW5kKENNUy5zdG9yZU5hbWUsQ01TLnN0b3JlVGFnKTsKIHRyeXsK"
    "ICAgaWYoQ01TLnN0b3JlTmFtZSlsb2NhbFN0b3JhZ2Uuc2V0SXRlbSgic3RvcmVOYW1lIixDTVMu"
    "c3RvcmVOYW1lKTsKICAgaWYoQ01TLnN0b3JlVGFnKWxvY2FsU3RvcmFnZS5zZXRJdGVtKCJzdG9y"
    "ZVRhZyIsQ01TLnN0b3JlVGFnKTsKIH1jYXRjaChlKXt9CiBpZihnKCJidGFnIikpZygiYnRhZyIp"
    "LnRleHRDb250ZW50PUNNUy5zdG9yZVRhZzsKIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltk"
    "YXRhLW5hdl0iKS5mb3JFYWNoKG49PnsKICAgY29uc3Qgaz0ibmF2IituLmRhdGFzZXQubmF2WzBd"
    "LnRvVXBwZXJDYXNlKCkrbi5kYXRhc2V0Lm5hdi5zbGljZSgxKTsKICAgaWYoQ01TW2tdKW4udGV4"
    "dENvbnRlbnQ9Q01TW2tdO30pOwogaWYod2luZG93Ll9fZG9ja1BsYWNlKXJlcXVlc3RBbmltYXRp"
    "b25GcmFtZSgoKT0+d2luZG93Ll9fZG9ja1BsYWNlKHRydWUpKTsKfQpsZXQgQkFMPTI0OC41MDsK"
    "Y29uc3QgU1RPQ0s9Wwoge2M6IkluZGlhIixmOiLwn4eu8J+HsyIsY29kZToiKzkxIixwcmljZToz"
    "NSx3YXM6NDkscXR5OjEyOCxob3Q6MX0sCiB7YzoiSW5kb25lc2lhIixmOiLwn4eu8J+HqSIsY29k"
    "ZToiKzYyIixwcmljZToyOCx3YXM6MCxxdHk6NjR9LAoge2M6IkJyYXppbCIsZjoi8J+Hp/Cfh7ci"
    "LGNvZGU6Iis1NSIscHJpY2U6NDQsd2FzOjU5LHF0eToyMn0sCiB7YzoiTmlnZXJpYSIsZjoi8J+H"
    "s/Cfh6wiLGNvZGU6IisyMzQiLHByaWNlOjMxLHdhczowLHF0eTowfSwKIHtjOiJWaWV0bmFtIixm"
    "OiLwn4e78J+HsyIsY29kZToiKzg0IixwcmljZToyNix3YXM6MCxxdHk6OTF9LAoge2M6IlBoaWxp"
    "cHBpbmVzIixmOiLwn4e18J+HrSIsY29kZToiKzYzIixwcmljZTozOCx3YXM6MCxxdHk6N30KXTsK"
    "Y29uc3QgT1JERVJTPVsKIHtpZDoiT1JELTc3NDEiLGM6IkluZGlhIixmOiLwn4eu8J+HsyIsbnVt"
    "OiIrOTEg4oCi4oCi4oCi4oCiIOKAouKAojMyIixzdDoiRGVsaXZlcmVkIix3aGVuOiIyIG1pbiBh"
    "Z28iLHByaWNlOjM1fSwKIHtpZDoiT1JELTc3MjgiLGM6IlZpZXRuYW0iLGY6IvCfh7vwn4ezIixu"
    "dW06Iis4NCDigKLigKLigKLigKIg4oCi4oCiMDciLHN0OiJEZWxpdmVyZWQiLHdoZW46Illlc3Rl"
    "cmRheSIscHJpY2U6MjZ9LAoge2lkOiJPUkQtNzcwMiIsYzoiQnJhemlsIixmOiLwn4en8J+HtyIs"
    "bnVtOiIrNTUg4oCi4oCi4oCi4oCiIOKAouKAojg4IixzdDoiUmVmdW5kZWQiLHdoZW46IjMgZGF5"
    "cyBhZ28iLHByaWNlOjQ0fQpdOwpsZXQgQ0hBTk5FTFM9Wwoge2lkOjEsbmFtZToiTWFpbiBDaGFu"
    "bmVsIixoYW5kbGU6IkB5b3VyX21haW5fY2hhbm5lbCIscmVxOnRydWUsb246dHJ1ZSxtZW1iZXJz"
    "OiIxMi40SyJ9LAoge2lkOjIsbmFtZToiQmFja3VwIENoYW5uZWwiLGhhbmRsZToiQHlvdXJfYmFj"
    "a3VwIixyZXE6dHJ1ZSxvbjp0cnVlLG1lbWJlcnM6IjMuMUsifSwKIHtpZDozLG5hbWU6IkRpc2N1"
    "c3Npb24gR3JvdXAiLGhhbmRsZToiQHlvdXJfZ3JvdXAiLHJlcTpmYWxzZSxvbjp0cnVlLG1lbWJl"
    "cnM6IjUuOEsifQpdOwpsZXQgTE9HQ0g9Wwoge2lkOjEsbmFtZToiTWFpbiBMb2dzIixoYW5kbGU6"
    "IkB5b3VyX2xvZ3MiLHByaW1hcnk6dHJ1ZSxvbjp0cnVlfSwKIHtpZDoyLG5hbWU6IlBheW1lbnQg"
    "TG9ncyIsaGFuZGxlOiJAeW91cl9wYXlfbG9ncyIscHJpbWFyeTpmYWxzZSxvbjp0cnVlfSwKIHtp"
    "ZDozLG5hbWU6IkVycm9yIExvZ3MiLGhhbmRsZToiLTEwMHh4eHh4eHh4eHgiLHByaW1hcnk6ZmFs"
    "c2Usb246ZmFsc2V9Cl07CmxldCBMT0dTX01BU1RFUj10cnVlOwpsZXQgRVZFTlRTPVsKIHtrOiJO"
    "ZXcgdXNlciBqb2luZWQiLG9uOmZhbHNlLGNoOjF9LHtrOiJEZXBvc2l0IC8gcGF5bWVudCIsb246"
    "dHJ1ZSxjaDoyfSwKIHtrOiJQdXJjaGFzZSIsb246dHJ1ZSxjaDoxfSx7azoiT1RQIHJlcXVlc3Rl"
    "ZCIsb246ZmFsc2UsY2g6MX0sCiB7azoiU3RvY2sgYWRkZWQiLG9uOnRydWUsY2g6MX0se2s6IkFk"
    "bWluIGFjdGlvbiIsb246dHJ1ZSxjaDoxfSwKIHtrOiJFcnJvcnMgJiB3YXJuaW5ncyIsb246dHJ1"
    "ZSxjaDozfQpdOwpjb25zdCBUWD1be3Q6IkRlcG9zaXQiLGE6IivigrkyMDAuMDAiLHc6IlRvZGF5"
    "LCAxNDowMiJ9LHt0OiJQdXJjaGFzZSDCtyBJbmRpYSIsYToiLeKCuTM1LjAwIix3OiJUb2RheSwg"
    "MTQ6MDUifSwKIHt0OiJEYWlseSBjaGVjay1pbiIsYToiK+KCuTAuNDAiLHc6IlRvZGF5LCAwOTox"
    "MSJ9LHt0OiJSZWZlcnJhbCBib251cyIsYToiK+KCuTMuMjAiLHc6Illlc3RlcmRheSJ9XTsKCmxl"
    "dCBGSUxURVJFRD1TVE9DSy5zbGljZSgpLCBRVUVSWT0iIiwgQ0hJUD0iYWxsIjsKY29uc3QgRU1Q"
    "VFk9JzxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJ0ZXh0LWFsaWduOmNlbnRlciI+PHAgY2xhc3M9"
    "InN1YiI+Tm90aGluZyBtYXRjaGVzIHRoYXQgc2VhcmNoLjwvcD48L2Rpdj4nOwpjb25zdCBJQz17"
    "YmFnOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1"
    "cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIg"
    "c3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwiTTYgMiAzIDZ2MTRhMiAyIDAgMCAw"
    "IDIgMmgxNGEyIDIgMCAwIDAgMi0yVjZsLTMtNHpcIi8+PHBhdGggZD1cIk0zIDZoMThcIi8+PHBh"
    "dGggZD1cIk0xNiAxMGE0IDQgMCAwIDEtOCAwXCIvPjwvc3ZnPiIsYm94OiI8c3ZnIHZpZXdCb3g9"
    "XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9r"
    "ZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwi"
    "cm91bmRcIj48cGF0aCBkPVwiTTIxIDhhMiAyIDAgMCAwLTEtMS43M2wtNy00YTIgMiAwIDAgMC0y"
    "IDBsLTcgNEEyIDIgMCAwIDAgMyA4djhhMiAyIDAgMCAwIDEgMS43M2w3IDRhMiAyIDAgMCAwIDIg"
    "MGw3LTRBMiAyIDAgMCAwIDIxIDE2WlwiLz48cGF0aCBkPVwibTMuMyA3IDguNyA1IDguNy01XCIv"
    "PjxwYXRoIGQ9XCJNMTIgMjJWMTJcIi8+PC9zdmc+Iix6YXA6Ijxzdmcgdmlld0JveD1cIjAgMCAy"
    "NCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRo"
    "PVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwi"
    "PjxwYXRoIGQ9XCJNMTMgMiAzIDE0aDlsLTEgOCAxMC0xMmgtOXpcIi8+PC9zdmc+Iix3YWxsZXQ6"
    "Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVu"
    "dENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJv"
    "a2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTkgN1Y1YTIgMiAwIDAgMC0yLTJINWEy"
    "IDIgMCAwIDAgMCA0aDE1YTIgMiAwIDAgMSAyIDJ2OWEyIDIgMCAwIDEtMiAySDVhMiAyIDAgMCAx"
    "LTItMlY1XCIvPjxwYXRoIGQ9XCJNMTggMTJoLjAxXCIvPjwvc3ZnPiIsdXNlcjoiPHN2ZyB2aWV3"
    "Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBz"
    "dHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9p"
    "bj1cInJvdW5kXCI+PHBhdGggZD1cIk0xOSAyMXYtMmE0IDQgMCAwIDAtNC00SDlhNCA0IDAgMCAw"
    "LTQgNHYyXCIvPjxjaXJjbGUgY3g9XCIxMlwiIGN5PVwiN1wiIHI9XCI0XCIvPjwvc3ZnPiIsc2hp"
    "ZWxkOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1"
    "cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIg"
    "c3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwiTTIwIDEzYzAgNS0zLjUgNy41LTcu"
    "NjYgOC45NWExIDEgMCAwIDEtLjY3LS4wMUM3LjUgMjAuNSA0IDE4IDQgMTNWNmExIDEgMCAwIDEg"
    "MS0xYzIgMCA0LjUtMS4yIDYuMjQtMi43MmExLjE3IDEuMTcgMCAwIDEgMS41MiAwQzE0LjUxIDMu"
    "ODEgMTcgNSAxOSA1YTEgMSAwIDAgMSAxIDF6XCIvPjwvc3ZnPiIsY2hlY2s6Ijxzdmcgdmlld0Jv"
    "eD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ry"
    "b2tlLXdpZHRoPVwiMi40XCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9p"
    "bj1cInJvdW5kXCI+PHBhdGggZD1cIk0yMCA2IDkgMTdsLTUtNVwiLz48L3N2Zz4iLHNlbmQ6Ijxz"
    "dmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENv"
    "bG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2Ut"
    "bGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJtMjIgMi03IDIwLTQtOS05LTRaXCIvPjxwYXRo"
    "IGQ9XCJNMjIgMiAxMSAxM1wiLz48L3N2Zz4iLHBsdXM6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAy"
    "NFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwi"
    "Mi40XCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTIgNXYxNE01IDEyaDE0"
    "XCIvPjwvc3ZnPiIsY29weToiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVc"
    "IiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVj"
    "YXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHJlY3Qgd2lkdGg9XCIxNFwi"
    "IGhlaWdodD1cIjE0XCIgeD1cIjhcIiB5PVwiOFwiIHJ4PVwiMlwiLz48cGF0aCBkPVwiTTQgMTZj"
    "LTEuMSAwLTItLjktMi0yVjRjMC0xLjEuOS0yIDItMmgxMGMxLjEgMCAyIC45IDIgMlwiLz48L3N2"
    "Zz4iLGNoZXY6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIHdpZHRoPVwiMTZcIiBoZWlnaHQ9"
    "XCIxNlwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRo"
    "PVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwi"
    "PjxwYXRoIGQ9XCJtOSAxOCA2LTYtNi02XCIvPjwvc3ZnPiIsZmlyZToiPHN2ZyB2aWV3Qm94PVwi"
    "MCAwIDI0IDI0XCIgd2lkdGg9XCIxMlwiIGhlaWdodD1cIjEyXCIgZmlsbD1cIm5vbmVcIiBzdHJv"
    "a2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJy"
    "b3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0xMiAyYzEgNCA1IDUg"
    "NSA5YTUgNSAwIDAgMS0xMCAwYzAtMiAxLTMgMi00IDAgMiAxIDMgMiAzIDAtMy0xLTUgMS04Wlwi"
    "Lz48L3N2Zz4iLGRvd246Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIg"
    "c3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2Fw"
    "PVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTIgNXYxNE0x"
    "OSAxMmwtNyA3LTctN1wiLz48L3N2Zz4iLHVwOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBm"
    "aWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBz"
    "dHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBk"
    "PVwiTTEyIDE5VjVNNSAxMmw3LTcgNyA3XCIvPjwvc3ZnPiIsc2VhcmNoOiI8c3ZnIHZpZXdCb3g9"
    "XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9r"
    "ZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwi"
    "cm91bmRcIj48Y2lyY2xlIGN4PVwiMTFcIiBjeT1cIjExXCIgcj1cIjdcIi8+PHBhdGggZD1cIm0y"
    "MSAyMS00LjMtNC4zXCIvPjwvc3ZnPiIsc3RhcjoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIg"
    "ZmlsbD1cImN1cnJlbnRDb2xvclwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0"
    "aD1cIjEuNVwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIm0xMiAzIDIuNiA1"
    "LjMgNS45LjktNC4zIDQuMSAxIDUuOC01LjItMi43LTUuMiAyLjcgMS01LjhMMy41IDkuMmw1Ljkt"
    "Ljl6XCIvPjwvc3ZnPiJ9Owpjb25zdCBpYz1uPT5JQ1tuXXx8IiI7CmNvbnN0IGVzYz1zPT5TdHJp"
    "bmcocykucmVwbGFjZSgvWyY8PiJdL2csYz0+KHsiJiI6IiZhbXA7IiwiPCI6IiZsdDsiLCI+Ijoi"
    "Jmd0OyIsJyInOiImcXVvdDsifVtjXSkpOwpjb25zdCBtb25leT1uPT5DRkcuY3VyK051bWJlcihu"
    "KS50b0ZpeGVkKDIpOwoKbGV0IHR0O2NvbnN0IHRvYXN0PW09Pntjb25zdCBlPWRvY3VtZW50Lmdl"
    "dEVsZW1lbnRCeUlkKCJ0b2FzdCIpO2UudGV4dENvbnRlbnQ9bTtlLmNsYXNzTGlzdC5hZGQoInNo"
    "b3ciKTtjbGVhclRpbWVvdXQodHQpO3R0PXNldFRpbWVvdXQoKCk9PmUuY2xhc3NMaXN0LnJlbW92"
    "ZSgic2hvdyIpLDE5MDApfTsKZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigiY2xpY2siLGU9Pntj"
    "b25zdCBiPWUudGFyZ2V0LmNsb3Nlc3QoIltkYXRhLXRvYXN0XSIpO2lmKGIpdG9hc3QoYi5kYXRh"
    "c2V0LnRvYXN0KX0pOwpjb25zdCBzaGVldD1oPT57CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "InBhbmVsIikuaW5uZXJIVE1MPSc8ZGl2IGNsYXNzPSJncmFiIj48L2Rpdj4nK2g7CiAgZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoInNoZWV0IikuY2xhc3NMaXN0LmFkZCgib3BlbiIpOwogIC8qIGNv"
    "bnRyb2xzIGluc2lkZSBhIHNoZWV0IGFyZSBjcmVhdGVkIGFmdGVyIGJpbmRpbmcsIHNvIGJpbmQg"
    "dGhlbSBub3cgKi8KICB0cnl7IGlmKHR5cGVvZiBiaW5kQ29tbW9uPT09ImZ1bmN0aW9uIikgYmlu"
    "ZENvbW1vbigpOyB9Y2F0Y2goZSl7fQogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNzaGVl"
    "dCBbZGF0YS1jbG9zZV0iKS5mb3JFYWNoKHg9Pngub25jbGljaz1jbG9zZVNoZWV0KTsKfTsKY29u"
    "c3QgY2xvc2VTaGVldD0oKT0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNoZWV0IikuY2xhc3NM"
    "aXN0LnJlbW92ZSgib3BlbiIpOwpkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCJjbGljayIsZT0+"
    "e2lmKGUudGFyZ2V0LmNsb3Nlc3QoIltkYXRhLWNsb3NlXSIpKWNsb3NlU2hlZXQoKX0pOwoKZG9j"
    "dW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3N3IGJ1dHRvbiIpLmZvckVhY2goYj0+Yi5vbmNsaWNr"
    "PSgpPT57CiAgaWYoYi5kYXRhc2V0LnN3PT09ImFkbWluIiYmIWFkbWluQWxsb3dlZCgpKXt0b2Fz"
    "dCgiQWRtaW5zIG9ubHkiKTtyZXR1cm47fQogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNz"
    "dyBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnJlbW92ZSgib24iKSk7Yi5jbGFzc0xp"
    "c3QuYWRkKCJvbiIpOwogIFsiYXBwIiwiYWRtaW4iXS5mb3JFYWNoKHY9PmRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJ2aWV3LSIrdikuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZGVuIix2IT09Yi5kYXRh"
    "c2V0LnN3KSk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInRhYnMiKS5zdHlsZS5kaXNwbGF5"
    "PWIuZGF0YXNldC5zdz09PSJhcHAiPyIiOiJub25lIjt3aW5kb3cuc2Nyb2xsVG8oMCwwKTsKICBy"
    "ZXF1ZXN0QW5pbWF0aW9uRnJhbWUoKCk9PntpZih3aW5kb3cuX19kb2NrUGxhY2Upd2luZG93Ll9f"
    "ZG9ja1BsYWNlKHRydWUpOwogICAgaWYod2luZG93Ll9fdnRhYlBsYWNlKXdpbmRvdy5fX3Z0YWJQ"
    "bGFjZSh0cnVlKTt9KTt9KTsKCmxldCBUQUI9InNob3AiOwpkb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "QWxsKCIjdGFicyBidXR0b24iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogIGRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3JBbGwoIiN0YWJzIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3Qu"
    "cmVtb3ZlKCJvbiIpKTtiLmNsYXNzTGlzdC5hZGQoIm9uIik7CiAgVEFCPWIuZGF0YXNldC50YWI7"
    "cmVuZGVyKCk7d2luZG93LnNjcm9sbFRvKDAsMCk7aWYod2luZG93Ll9fZG9ja1BsYWNlKXJlcXVl"
    "c3RBbmltYXRpb25GcmFtZSh3aW5kb3cuX19kb2NrUGxhY2UpO30pOwpkb2N1bWVudC5hZGRFdmVu"
    "dExpc3RlbmVyKCJjbGljayIsZT0+e2NvbnN0IGc9ZS50YXJnZXQuY2xvc2VzdCgiW2RhdGEtZ29d"
    "Iik7aWYoIWcpcmV0dXJuOwogIGNvbnN0IHQ9Zy5kYXRhc2V0LmdvO2RvY3VtZW50LnF1ZXJ5U2Vs"
    "ZWN0b3JBbGwoIiN0YWJzIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJv"
    "biIseC5kYXRhc2V0LnRhYj09PXQpKTsKICBUQUI9dDtyZW5kZXIoKTt3aW5kb3cuc2Nyb2xsVG8o"
    "MCwwKTt9KTsKCmZ1bmN0aW9uIHJlbmRlcigpe2NvbnN0IHN0PWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJzdGFnZSIpOwogIGlmKExPQ0tFRCl7cGFpbnRMb2NrZWQoKTtyZXR1cm47fQogIHN0Lmlu"
    "bmVySFRNTD0nPGRpdiBjbGFzcz0ic2siPjwvZGl2PjxkaXYgY2xhc3M9InNrIj48L2Rpdj48ZGl2"
    "IGNsYXNzPSJzayIgc3R5bGU9ImhlaWdodDoxNDBweCI+PC9kaXY+JzsKICBzZXRUaW1lb3V0KCgp"
    "PT57aWYoTE9DS0VEKXtwYWludExvY2tlZCgpO3JldHVybjt9CiAgICBzdC5pbm5lckhUTUw9KHtz"
    "aG9wOnZTaG9wLG9yZGVyczp2T3JkZXJzLGVhcm46dkVhcm4sd2FsbGV0OnZXYWxsZXQseW91OnZZ"
    "b3V9KVtUQUJdKCk7CiAgICBiaW5kU3RhZ2UoKTtyZXZlYWxTdGFnZShzdCk7YmluZFRpbHQoKTt9"
    "LFJNPzA6MTYwKTt9CgpmdW5jdGlvbiB2U2hvcCgpewogIGNvbnN0IGxpdmU9U1RPQ0suZmlsdGVy"
    "KHM9PnMucXR5PjApLmxlbmd0aCwgdW5pdHM9U1RPQ0sucmVkdWNlKChhLGIpPT5hK2IucXR5LDAp"
    "OwogIGlmKExJVkUmJiFTVE9DSy5sZW5ndGgpewogICAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJk"
    "IiBzdHlsZT0ibWFyZ2luOjE4cHggMDt0ZXh0LWFsaWduOmNlbnRlcjtwYWRkaW5nOjI2cHgiPgog"
    "ICAgICA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNnB4Ij4ke2VzYyhDTVMuZW1wdHlUaXRsZXx8IlJl"
    "c3RvY2tpbmciKX08L2gzPgogICAgICA8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPiR7"
    "ZXNjKENNUy5lbXB0eUJvZHl8fCJFdmVyeXRoaW5nIGp1c3Qgc29sZCBvdXQuIEZyZXNoIGFjY291"
    "bnRzIGxhbmQgaGVyZSBzaG9ydGx5IFx1MjAxNCB0dXJuIG9uIGFuIGFsZXJ0IGFuZCB5b3Ugd2ls"
    "bCBoZWFyIGZpcnN0LiIpfTwvcD4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxNHB4IiBkYXRhLWFjdD0iYWxlcnRzIj5Ob3RpZnkgbWU8L2J1dHRvbj48"
    "L2Rpdj5gOwogIH0KICByZXR1cm4gYAogIDxkaXYgY2xhc3M9ImJhbm5lciI+PGRpdiBjbGFzcz0i"
    "dHJhY2siPgogICAgJHtbMSwyXS5tYXAoKCk9PmA8c3Bhbj4ke2ljKCJ6YXAiKX0ke0NNUy50aWNr"
    "MX08L3NwYW4+PHNwYW4+JHtpYygid2FsbGV0Iil9JHtDTVMudGljazJ9PC9zcGFuPgogICAgPHNw"
    "YW4+JHtpYygic2hpZWxkIil9JHtDTVMudGljazN9PC9zcGFuPjxzcGFuPiR7aWMoInN0YXIiKX0k"
    "e0NNUy50aWNrNH08L3NwYW4+YCkuam9pbigiIil9CiAgPC9kaXY+PC9kaXY+CiAgJHtHKCJvbmJv"
    "YXJkaW5nIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgaWQ9InRvdXJjYXJkIiBzdHlsZT0iZGlz"
    "cGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICA8ZGl2IGNsYXNzPSJm"
    "bGFnIj4ke2ljKCJ6YXAiKX08L2Rpdj4KICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6"
    "MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+TmV3IGhlcmU/IDMtc3RlcCB0b3VyPC9iPgog"
    "ICAgICA8YnI+PHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjlyZW0i"
    "PlRvcCB1cCwgcGljayBhIGNvdW50cnksIGdldCB0aGUgT1RQIGluLWFwcC48L3NtYWxsPjwvZGl2"
    "PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBpZD0idG91cmdvIj5TdGFydDwvYnV0dG9uPgog"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIGdob3N0IiBpZD0idG91cnNraXAiPlNraXA8L2J1dHRv"
    "bj48L2Rpdj5gKX0KICA8c2VjdGlvbiBjbGFzcz0iaGVybyI+CiAgICA8ZGl2IGNsYXNzPSJraWNr"
    "Ij4ke0NNUy5oZXJvS2lja308L2Rpdj4KICAgIDxoMT4ke0NNUy5oZXJvSDF9PC9oMT4KICAgIDxw"
    "PiR7Q01TLmhlcm9TdWIucmVwbGFjZSgie259IixsaXZlKX08L3A+CiAgICA8ZGl2IGNsYXNzPSJi"
    "dG5yb3ciPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIiBkYXRhLXNjcm9sbD0ic2hvcGxp"
    "c3QiPiR7aWMoImJhZyIpfSAke0NNUy5oZXJvQnRuMX08L2J1dHRvbj4KICAgICAgPGJ1dHRvbiBj"
    "bGFzcz0iYnRuIGdsYXNzIiBkYXRhLWdvPSJ3YWxsZXQiPiR7Q01TLmhlcm9CdG4yfTwvYnV0dG9u"
    "PgogICAgPC9kaXY+CiAgPC9zZWN0aW9uPgogIDxzdmcgY2xhc3M9InRyYWNlIiB2aWV3Qm94PSIw"
    "IDAgMzIwIDEwIiBwcmVzZXJ2ZUFzcGVjdFJhdGlvPSJub25lIj48cGF0aCBjbGFzcz0iYmFzZSIg"
    "ZD0iTTAsNSBMMzIwLDUiLz4KICAgIDxwYXRoIGNsYXNzPSJsZWFkIiBkPSJNMCw1IEwzMjAsNSIg"
    "c3Ryb2tlPSJ1cmwoI3RnMSkiLz4KICAgIDxkZWZzPjxsaW5lYXJHcmFkaWVudCBpZD0idGcxIiBn"
    "cmFkaWVudFVuaXRzPSJ1c2VyU3BhY2VPblVzZSIgeDE9Ii0xNjAiIHkxPSIwIiB4Mj0iMCIgeTI9"
    "IjAiPgogICAgICA8c3RvcCBzdG9wLWNvbG9yPSJ2YXIoLS1hY2MpIiBzdG9wLW9wYWNpdHk9IjAi"
    "Lz48c3RvcCBvZmZzZXQ9Ii41IiBzdG9wLWNvbG9yPSJ2YXIoLS1hY2MpIi8+CiAgICAgIDxzdG9w"
    "IG9mZnNldD0iMSIgc3RvcC1jb2xvcj0idmFyKC0tYWNjMikiIHN0b3Atb3BhY2l0eT0iMCIvPgog"
    "ICAgICA8YW5pbWF0ZSBhdHRyaWJ1dGVOYW1lPSJ4MSIgdmFsdWVzPSItMTYwOzMyMCIgZHVyPSIy"
    "LjJzIiByZXBlYXRDb3VudD0iaW5kZWZpbml0ZSIvPgogICAgICA8YW5pbWF0ZSBhdHRyaWJ1dGVO"
    "YW1lPSJ4MiIgdmFsdWVzPSIwOzQ4MCIgZHVyPSIyLjJzIiByZXBlYXRDb3VudD0iaW5kZWZpbml0"
    "ZSIvPgogICAgPC9saW5lYXJHcmFkaWVudD48L2RlZnM+PC9zdmc+CiAgPGRpdiBjbGFzcz0iZmVh"
    "dHMiPgogICAgPGRpdiBjbGFzcz0iZmVhdCI+JHtpYygiemFwIil9PGI+JHtDTVMuZmVhdDF9PC9i"
    "PjxzbWFsbD4ke0NNUy5mZWF0MXN9PC9zbWFsbD48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZlYXQi"
    "PiR7aWMoInNoaWVsZCIpfTxiPiR7Q01TLmZlYXQyfTwvYj48c21hbGw+JHtDTVMuZmVhdDJzfTwv"
    "c21hbGw+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmZWF0Ij4ke2ljKCJ3YWxsZXQiKX08Yj4ke0NN"
    "Uy5mZWF0M308L2I+PHNtYWxsPiR7Q01TLmZlYXQzc308L3NtYWxsPjwvZGl2PgogIDwvZGl2Pgog"
    "ICR7Rygic3RvY2tDb3VudGVyIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3Bs"
    "YXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgPHNwYW4gY2xhc3M9InBp"
    "bGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5MaXZlPC9zcGFuPgogICAgPGRpdiBzdHlsZT0iZmxl"
    "eDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzZyZW07Y29sb3I6dmFyKC0tdHgyKSI+PGIgc3R5"
    "bGU9ImNvbG9yOnZhcigtLXR4KSI+JHt1bml0c308L2I+IGFjY291bnRzIGluIHN0b2NrIGFjcm9z"
    "cyA8YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4ke2xpdmV9PC9iPiBjb3VudHJpZXM8L2Rpdj4K"
    "ICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWFjdD0iY2hhbm5lbCI+Q2hhbm5l"
    "bDwvYnV0dG9uPjwvZGl2PmApfQogICR7RygiZmxhc2hTYWxlIixgPGRpdiBjbGFzcz0iY2FyZCB0"
    "aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgog"
    "ICAgPHNwYW4gY2xhc3M9InBpbGwgaG90Ij4ke2ljKCJmaXJlIil9ICR7Q01TLmZsYXNoUGlsbH08"
    "L3NwYW4+CiAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NnJl"
    "bTtjb2xvcjp2YXIoLS10eDIpIj4ke0NNUy5mbGFzaFRleHR9IDxiIHN0eWxlPSJjb2xvcjp2YXIo"
    "LS10eCkiIGlkPSJjZCI+MTQ6NTI8L2I+PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMi"
    "IGRhdGEtYWN0PSJmbGFzaCI+JHtDTVMuZmxhc2hCdG59PC9idXR0b24+PC9kaXY+YCl9CiAgPGRp"
    "diBjbGFzcz0id2hlZWwiPjxkaXYgY2xhc3M9InJpbmciPiR7U1RPQ0subWFwKChzLGkpPT5gPGIg"
    "c3R5bGU9InRyYW5zZm9ybTpyb3RhdGUoJHtpKjYwfWRlZykgdHJhbnNsYXRlKDcwcHgpIHJvdGF0"
    "ZSgtJHtpKjYwfWRlZykiPiR7cy5jb2RlfTwvYj5gKS5qb2luKCIiKX08L2Rpdj4KICAgIDxkaXYg"
    "Y2xhc3M9ImNhcCI+PGVtPiR7Q01TLndoZWVsS2lja308L2VtPjxiPiR7Q01TLndoZWVsVGl0bGV9"
    "PC9iPgogICAgICA8ZGl2IGNsYXNzPSJjbG91ZCIgc3R5bGU9Im1hcmdpbi10b3A6NnB4Ij48ZGl2"
    "IGNsYXNzPSJ0Ij4ke1sxLDJdLm1hcCgoKT0+Q01TLndoZWVsV29yZHMuc3BsaXQoIiwiKS5tYXAo"
    "dz0+YDxpPiR7dy50cmltKCl9PC9pPmApLmpvaW4oIiIpKS5qb2luKCIiKX08L2Rpdj48L2Rpdj48"
    "L2Rpdj48L2Rpdj4KICAke0coInNhbGVzRmVlZCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5KdXN0"
    "IHNvbGQ8L2gzPjxwIGNsYXNzPSJzdWIiPkxpdmUgZmVlZCDigJQgYWxzbyBhdXRvLXBvc3RlZCB0"
    "byB5b3VyIGNoYW5uZWwuPC9wPgogICAgJHtGRUVELmxlbmd0aD9GRUVELm1hcCgoW2YsYyxhLHdd"
    "KT0+YDxkaXYgY2xhc3M9ImZlZWRsaW5lIj48c3Bhbj4ke2Z9PC9zcGFuPjxiPiR7Y308L2I+IHNv"
    "bGQgZm9yIDxiPiR7YX08L2I+PHNwYW4gc3R5bGU9Im1hcmdpbi1sZWZ0OmF1dG8iPiR7d308L3Nw"
    "YW4+PC9kaXY+YCkuam9pbigiIikKICAgICAgOic8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2lu"
    "OjAiPk5vIHNhbGVzIHlldCB0b2RheS48L3A+J308L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJzZWMi"
    "IGlkPSJzaG9wbGlzdCI+PGgyPiR7Q01TLnNob3BIZWFkfTwvaDI+PGEgZGF0YS1hY3Q9InNvcnQi"
    "PiR7Q01TLnNob3BTb3J0fTwvYT48L2Rpdj4KICA8ZGl2IGNsYXNzPSJtc2VhcmNoIj48ZGl2IGNs"
    "YXNzPSJmaWVsZHdyYXAiPiR7aWMoInNlYXJjaCIpfTxpbnB1dCBpZD0icSIgcGxhY2Vob2xkZXI9"
    "IiR7Q01TLnNlYXJjaFBofSI+PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20gc29mdCIg"
    "ZGF0YS1hY3Q9ImFsZXJ0cyI+JHtDTVMuYWxlcnRzQnRufTwvYnV0dG9uPjwvZGl2PgogIDxkaXYg"
    "Y2xhc3M9ImNoaXByb3ciIGlkPSJjaGlwcyI+CiAgICA8YnV0dG9uIGNsYXNzPSJvbiIgZGF0YS1m"
    "aWx0ZXI9ImFsbCI+QWxsPC9idXR0b24+PGJ1dHRvbiBkYXRhLWZpbHRlcj0ic3RvY2siPkluIHN0"
    "b2NrPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtZmlsdGVyPSJkZWFsIj5PbiBkZWFsPC9idXR0"
    "b24+PGJ1dHRvbiBkYXRhLWZpbHRlcj0iY2hlYXAiPlVuZGVyIOKCuTM1PC9idXR0b24+CiAgICAk"
    "e0coInZhcmlhbnRzIixgPGJ1dHRvbiBkYXRhLWZpbHRlcj0idjJmYSI+Tm8gMkZBPC9idXR0b24+"
    "PGJ1dHRvbiBkYXRhLWZpbHRlcj0idm1haWwiPldpdGggbWFpbDwvYnV0dG9uPjxidXR0b24gZGF0"
    "YS1maWx0ZXI9InZzcGFtIj5TcGFtLWZyZWU8L2J1dHRvbj5gKX08L2Rpdj4KICA8ZGl2IGlkPSJz"
    "aG9wYm9keSI+JHtzaG9wQm9keSgpfTwvZGl2PmA7Cn0KZnVuY3Rpb24gc2hvcEJvZHkoKXtyZXR1"
    "cm4gYCR7RklMVEVSRUQubGVuZ3RoP2A8ZGl2IGNsYXNzPSJyb3dzIj4ke0ZJTFRFUkVELm1hcChz"
    "aG9wUm93KS5qb2luKCIiKX08L2Rpdj5gOkVNUFRZfWA7fQpmdW5jdGlvbiBwcmljZUJpdHMocyl7"
    "CiAgcmV0dXJuIGAke3Mud2FzP2A8cz4ke21vbmV5KHMud2FzKX08L3M+YDoiIn0ke21vbmV5KHMu"
    "cHJpY2UpfWA7Cn0KZnVuY3Rpb24gc2hvcFJvdyhzKXtjb25zdCBpPVNUT0NLLmluZGV4T2Yocyks"
    "b2ZmPXMucXR5PT09MDsKICByZXR1cm4gYDxkaXYgY2xhc3M9InJvdyIgZGF0YS1idXk9IiR7aX0i"
    "PjxkaXYgY2xhc3M9ImZsYWciPiR7cy5mfTwvZGl2PgogICAgPGRpdiBjbGFzcz0ibWlkIj48Yj4k"
    "e2VzYyhzLmMpfSAke3MuY29kZX08L2I+CiAgICAgIDxzbWFsbD4ke29mZj8iT3V0IG9mIHN0b2Nr"
    "IOKAlCBnZXQgYW4gYWxlcnQiOnMucXR5KyIgaW4gc3RvY2sgwrcgaW5zdGFudCJ9PC9zbWFsbD4K"
    "ICAgICAgJHtHKCJyZXZpZXdzIixgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDozcHgiPiR7c3RhcnMo"
    "cy5jKX08L2Rpdj5gKX0KICAgICAgJHtHKCJ2YXJpYW50cyIsYDxkaXYgc3R5bGU9Im1hcmdpbi10"
    "b3A6M3B4O2Rpc3BsYXk6ZmxleDtnYXA6NHB4O2ZsZXgtd3JhcDp3cmFwIj4KICAgICAgICA8c3Bh"
    "biBjbGFzcz0icGlsbCI+Tm8gMkZBPC9zcGFuPiR7cy5ob3Q/JzxzcGFuIGNsYXNzPSJwaWxsIj5X"
    "aXRoIG1haWw8L3NwYW4+JzoiIn08L2Rpdj5gKX08L2Rpdj4KICAgIDxkaXYgY2xhc3M9InJ0Ij48"
    "ZGl2IGNsYXNzPSJwIj4ke3ByaWNlQml0cyhzKX08L2Rpdj4KICAgICAgJHtHKCJkeW5QcmljZSIs"
    "YDxzbWFsbCBzdHlsZT0iZGlzcGxheTpibG9jaztmb250LXNpemU6LjZyZW07Y29sb3I6dmFyKC0t"
    "dHgyKSI+bGl2ZSBwcmljZTwvc21hbGw+YCl9CiAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6Zmxl"
    "eDtnYXA6NXB4O2p1c3RpZnktY29udGVudDpmbGV4LWVuZDttYXJnaW4tdG9wOjZweCI+CiAgICAg"
    "ICAgJHtHKCJ3aXNobGlzdCIsYDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLXdpc2g9"
    "IiR7aX0iPiR7V0lTSC5pbmNsdWRlcyhpKT8i4pmlIjoi4pmhIn08L2J1dHRvbj5gKX0KICAgICAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4geHMiPiR7b2ZmPyJOb3RpZnkgTWUiOiJCdXkifTwvYnV0dG9u"
    "PjwvZGl2PjwvZGl2PjwvZGl2PmA7fQpmdW5jdGlvbiBzaG9wQ2FyZChzKXtjb25zdCBpPVNUT0NL"
    "LmluZGV4T2Yocyksb2ZmPXMucXR5PT09MDsKICByZXR1cm4gYDxkaXYgY2xhc3M9InBjYXJkIiBk"
    "YXRhLWJ1eT0iJHtpfSIgc3R5bGU9InBvc2l0aW9uOnJlbGF0aXZlIj4KICAgICR7Rygid2lzaGxp"
    "c3QiLGA8c3BhbiBjbGFzcz0iaGVhcnQgJHtXSVNILmluY2x1ZGVzKGkpPyJvbiI6IiJ9IiBkYXRh"
    "LXdpc2g9IiR7aX0iPiR7aWMoInN0YXIiKX08L3NwYW4+YCl9CiAgICA8ZGl2IGNsYXNzPSJ0b3Ai"
    "PjxkaXYgY2xhc3M9ImZsYWciPiR7cy5mfTwvZGl2PgogICAgICA8c3BhbiBjbGFzcz0icGlsbCAk"
    "e29mZj8ib2ZmIjoib24ifSI+JHtvZmY/IlNvbGQgb3V0IjpzLnF0eSsiIGxlZnQifTwvc3Bhbj48"
    "L2Rpdj4KICAgIDxkaXY+PGRpdiBjbGFzcz0ibm0iPiR7ZXNjKHMuYyl9PC9kaXY+PGRpdiBjbGFz"
    "cz0ibWV0YSI+JHtzLmNvZGV9IMK3IGluc3RhbnQ8L2Rpdj4KICAgICAgJHtHKCJyZXZpZXdzIixg"
    "PGRpdiBzdHlsZT0ibWFyZ2luLXRvcDo0cHgiPiR7c3RhcnMocy5jKX08L2Rpdj5gKX08L2Rpdj4K"
    "ICAgIDxkaXYgY2xhc3M9InByaWNlIj4ke3ByaWNlQml0cyhzKX08L2Rpdj4KICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBibG9jayBzbSI+JHtvZmY/Ik5vdGlmeSBNZSI6IkJ1eSBOb3cifTwvYnV0dG9u"
    "PjwvZGl2PmA7fQoKZnVuY3Rpb24gdk9yZGVycygpewogIHJldHVybiBgPGRpdiBjbGFzcz0ic2Vj"
    "Ij48aDI+WW91ciBPcmRlcnM8L2gyPjxhIGRhdGEtYWN0PSJjc3YiPkV4cG9ydDwvYT48L2Rpdj4K"
    "ICAke09SREVSUy5tYXAoKG8sb2kpPT5gPGRpdiBjbGFzcz0iY2FyZCIgZGF0YS1vcmRlci1pZD0i"
    "JHtvLmlkfSI+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVy"
    "O2dhcDoxMnB4Ij4KICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtvLmZ9PC9kaXY+CiAgICAgIDxk"
    "aXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODVyZW07"
    "ZGlzcGxheTpibG9jayI+JHtvLm51bX08L2I+CiAgICAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2"
    "YXIoLS10eDIpO2ZvbnQtc2l6ZTouN3JlbSI+JHtvLndoZW59PC9zbWFsbD48L2Rpdj4KICAgICAg"
    "PGRpdiBzdHlsZT0idGV4dC1hbGlnbjpyaWdodCI+PHNwYW4gY2xhc3M9InBpbGwgJHtvLnN0PT09"
    "IkRlbGl2ZXJlZCI/Im9uIjoib2ZmIn0iPiR7by5zdH08L3NwYW4+PC9kaXY+PC9kaXY+CiAgICA8"
    "ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0ibWFyZ2luOjEwcHggMCAwO2JhY2tncm91bmQ6"
    "dmFyKC0tY2FyZDIpIj4KICAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlBob25lIG51bWJlcjwv"
    "c3Bhbj4KICAgICAgICA8Yj48Y29kZSBzdHlsZT0iZm9udC1zaXplOi44cmVtO3VzZXItc2VsZWN0"
    "OmFsbCI+JHtlc2Moby5waG9uZXx8IuKAlCIpfTwvY29kZT4KICAgICAgICAke28ucGhvbmU/YDxi"
    "dXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWNvcHk9InRleHQiIGRhdGEtY29weS12YWx1"
    "ZT0iJHtlc2Moby5waG9uZSl9Ij5Db3B5PC9idXR0b24+YDoiIn08L2I+PC9kaXY+CiAgICAgIDxk"
    "aXYgY2xhc3M9Imt2Ij48c3Bhbj4yRkEgcGFzc3dvcmQ8L3NwYW4+CiAgICAgICAgPGI+PGNvZGUg"
    "c3R5bGU9ImZvbnQtc2l6ZTouOHJlbTt1c2VyLXNlbGVjdDphbGwiPiR7ZXNjKG8udHdvZmF8fCJu"
    "b25lIil9PC9jb2RlPgogICAgICAgICR7by50d29mYT9gPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNv"
    "ZnQiIGRhdGEtY29weT0idGV4dCIgZGF0YS1jb3B5LXZhbHVlPSIke2VzYyhvLnR3b2ZhKX0iPkNv"
    "cHk8L2J1dHRvbj5gOiIifTwvYj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxh"
    "c3QgbG9naW4gY29kZTwvc3Bhbj4KICAgICAgICA8Yj48Y29kZSBzdHlsZT0iZm9udC1zaXplOi44"
    "cmVtO3VzZXItc2VsZWN0OmFsbCI+JHtlc2Moby5vdHB8fCJub3QgeWV0Iil9PC9jb2RlPgogICAg"
    "ICAgICR7by5vdHA/YDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWNvcHk9InRleHQi"
    "IGRhdGEtY29weS12YWx1ZT0iJHtlc2Moby5vdHApfSI+Q29weTwvYnV0dG9uPmA6IiJ9PC9iPjwv"
    "ZGl2PgogICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+U3RhdHVzPC9zcGFuPjxiPiR7by5sb2dp"
    "bj09PSJpbiIKICAgICAgICA/ICc8c3BhbiBjbGFzcz0icGlsbCBvbiI+TG9nZ2VkIGluIMK3IHlv"
    "dXJzIG9ubHk8L3NwYW4+JwogICAgICAgIDogKG8ud2luZG93PjAKICAgICAgICAgICAgPyAnPHNw"
    "YW4gY2xhc3M9InBpbGwiPkNvZGVzIGZvcndhcmRpbmcgwrcgJytvLndpbmRvdysnIG1pbiBsZWZ0"
    "PC9zcGFuPicKICAgICAgICAgICAgOiAnPHNwYW4gY2xhc3M9InBpbGwgb2ZmIj5IYW5kb3ZlciBj"
    "b21wbGV0ZTwvc3Bhbj4nKX08L2I+PC9kaXY+CiAgICA8L2Rpdj4KICAgICR7RygidGltZWxpbmUi"
    "LGA8ZGl2IGNsYXNzPSJ0bCI+CiAgICAgIDxkaXYgY2xhc3M9Im9rIj48Yj5QYWlkPC9iPjxzbWFs"
    "bD4ke28ud2hlbn08L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJvayI+PGI+RGVsaXZl"
    "cmVkPC9iPjxzbWFsbD5TZXNzaW9uIGhhbmRlZCBvdmVyIGluc3RhbnRseTwvc21hbGw+PC9kaXY+"
    "CiAgICAgIDxkaXYgY2xhc3M9IiR7by5zdD09PSJEZWxpdmVyZWQiPyJvayI6IiJ9Ij48Yj5PVFAg"
    "cmVjZWl2ZWQ8L2I+PHNtYWxsPiR7by5zdD09PSJEZWxpdmVyZWQiPyJDb2RlIGZvcndhcmRlZCBp"
    "bi1hcHAiOiJXYWl0aW5nIn08L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2PjxiPkNsb3NlZDwvYj48"
    "c21hbGw+QXV0by1jbG9zZXMgYWZ0ZXIgdGhlIHdhcnJhbnR5IHdpbmRvdzwvc21hbGw+PC9kaXY+"
    "PC9kaXY+YCl9CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjExcHgi"
    "PgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20iIGRhdGEtb3RwPSIke28ubnVtfSI+R2V0IE9U"
    "UDwvYnV0dG9uPgogICAgICAke0coInJlZG93bmxvYWQiLGA8YnV0dG9uIGNsYXNzPSJidG4gc20g"
    "c29mdCIgZGF0YS1vcmQ9ImRldGFpbHMiPkRldGFpbHM8L2J1dHRvbj5gKX08L2Rpdj4KICAgIDxk"
    "aXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6N3B4Ij4KICAgICAgJHtHKCJzZXNz"
    "aW9uRmlsZSIsYDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBkYXRhLW9yZD0iZmlsZSI+JHtp"
    "YygiY29weSIpfSBGaWxlPC9idXR0b24+YCl9CiAgICAgICR7RygicmVjZWlwdHMiLGA8YnV0dG9u"
    "IGNsYXNzPSJidG4gc20gc29mdCIgZGF0YS1vcmQ9InJlY2VpcHQiPlJlY2VpcHQ8L2J1dHRvbj5g"
    "KX0KICAgICAgJHsoby5sb2dpbj09PSJpbiIpPyIiOmA8YnV0dG9uIGNsYXNzPSJidG4gc20iIGRh"
    "dGEtbG9nZ2VkaW49IiR7by5pZH0iPkknbSBsb2dnZWQgaW48L2J1dHRvbj5gfQogICAgICAke0co"
    "IndhcnJhbnR5IixgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIGdob3N0IiBkYXRhLWNsYWltLW9yZGVy"
    "PSIke28uaWR9Ij5SZXBvcnQgaXNzdWU8L2J1dHRvbj5gKX08L2Rpdj4KICAgICR7RygicmV2aWV3"
    "cyIsYDxkaXYgY2xhc3M9Imt2IiBzdHlsZT0ibWFyZ2luLXRvcDo5cHgiPjxzcGFuPlJhdGUgdGhp"
    "cyBvcmRlcjwvc3Bhbj4KICAgICAgPGI+PGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEt"
    "cmF0ZT0iJHtvLmN9Ij5SYXRlPC9idXR0b24+PC9iPjwvZGl2PmApfQogIDwvZGl2PmApLmpvaW4o"
    "IiIpfQoKICA8ZGl2IGNsYXNzPSJjYXJkIiBzdHlsZT0idGV4dC1hbGlnbjpjZW50ZXIiPjxoMz5O"
    "ZWVkIGhlbHAgd2l0aCBhbiBvcmRlcj88L2gzPgogICAgPHAgY2xhc3M9InN1YiI+JHtGRUFULmF1"
    "dG9SZWZ1bmQ/IkZhaWxlZCBsb2dpbnMgYXJlIHJlZnVuZGVkIGF1dG9tYXRpY2FsbHkgd2l0aGlu"
    "IDEwIG1pbnV0ZXMuIjoiQ29udGFjdCBzdXBwb3J0IGFuZCB3ZSB3aWxsIGxvb2sgaW50byBpdC4i"
    "fTwvcD4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGJ1"
    "dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtYWN0PSJzdXBwb3J0Ij5TdXBwb3J0PC9idXR0b24+"
    "CiAgICAke0coImRpc3B1dGUiLGA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtZGlzcHV0"
    "ZT5EaXNwdXRlPC9idXR0b24+YCl9PC9kaXY+PC9kaXY+YDsKfQoKZnVuY3Rpb24gdkVhcm4oKXsK"
    "ICByZXR1cm4gYCR7Rygic3RyZWFrIixgPGRpdiBjbGFzcz0iY2FyZCI+CiAgICA8ZGl2IHN0eWxl"
    "PSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMnB4Ij4KICAgICAgPGRpdiBj"
    "bGFzcz0iZmxhZyI+JHtpYygiemFwIil9PC9kaXY+PGRpdiBzdHlsZT0iZmxleDoxIj48aDM+RGFp"
    "bHkgQ2hlY2staW48L2gzPjxwIGNsYXNzPSJzdWIiPkRheSAke0VBUk4uc3RyZWFrfSBzdHJlYWsg"
    "wrcgJHttb25leShFQVJOLmRhaWx5KX0gd2FpdGluZzwvcD48L2Rpdj48L2Rpdj4KICAgIDxkaXYg"
    "c3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6NnB4O21hcmdpbjoxNHB4IDAiPgogICAgICAke1sxLDIs"
    "Myw0LDUsNiw3XS5tYXAoZD0+YDxkaXYgc3R5bGU9ImZsZXg6MTtoZWlnaHQ6MzRweDtib3JkZXIt"
    "cmFkaXVzOjlweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2ZvbnQtc2l6ZTouNjhy"
    "ZW07Zm9udC13ZWlnaHQ6NzAwO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7JHtkPD0oRUFS"
    "Ti5zdHJlYWt8fDApPyJiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojZmZmZmZmO2Jv"
    "cmRlci1jb2xvcjp0cmFuc3BhcmVudCI6ImJhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZh"
    "cigtLXR4MikifSI+JHtkfTwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PgogICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIGN0YSBibG9jayIgZGF0YS1jbGFpbSAke0VBUk4uY2xhaW1lZD8iZGlzYWJsZWQiOiIi"
    "fT4ke0VBUk4uY2xhaW1lZD8iQ2xhaW1lZCB0b2RheSI6IkNsYWltICIrbW9uZXkoRUFSTi5kYWls"
    "eSl9PC9idXR0b24+PC9kaXY+YCl9CiAgJHtHKCJsb3lhbHR5IixgPGRpdiBjbGFzcz0iY2FyZCI+"
    "PGgzPkxveWFsdHkgUG9pbnRzPC9oMz48cCBjbGFzcz0ic3ViIj4xIHBvaW50IHBlciDigrkxMCBz"
    "cGVudC4gMTAwIHBvaW50cyA9IOKCuTEwIGJhbGFuY2UuPC9wPgogICAgPGRpdiBjbGFzcz0ia3Yi"
    "PjxzcGFuPkJhbGFuY2U8L3NwYW4+PGI+JHtFQVJOLnBvaW50c30gcHRzPC9iPjwvZGl2PgogICAg"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPldvcnRoPC9zcGFuPjxiPiR7bW9uZXkoRUFSTi5wb2ludHNX"
    "b3J0aCl9PC9iPjwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBpZD0icmVkZWVt"
    "Z28iICR7RUFSTi5wb2ludHM+MD8iIjoiZGlzYWJsZWQifT5SZWRlZW0gUG9pbnRzPC9idXR0b24+"
    "PC9kaXY+YCl9CiAgJHthbnlGKCJyZWZEYXNoIiwicmVmTDIiKT9gPGRpdiBjbGFzcz0iY2FyZCI+"
    "PGgzPkludml0ZSAmYW1wOyBFYXJuPC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPiR7RkVBVC5yZWZM"
    "Mj8iNSUgZnJvbSBwZW9wbGUgeW91IGludml0ZSwgcGx1cyAxJSBmcm9tIHRoZSBwZW9wbGUgdGhl"
    "eSBpbnZpdGUuIjoiMSUgb2YgZXZlcnkgZGVwb3NpdCB5b3VyIGludml0ZXMgZXZlciBtYWtlLiJ9"
    "PC9wPgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkludml0ZXM8L3NwYW4+PGI+JHtFQVJOLmlu"
    "dml0ZXN9PC9iPjwvZGl2PgogICAgJHtHKCJyZWZMMiIsYDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5M"
    "ZXZlbCAxIGVhcm5pbmdzPC9zcGFuPjxiPiR7bW9uZXkoRUFSTi5sMSl9PC9iPjwvZGl2PgogICAg"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxldmVsIDIgZWFybmluZ3M8L3NwYW4+PGI+JHttb25leShF"
    "QVJOLmwyKX08L2I+PC9kaXY+YCl9CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VG90YWwgZWFy"
    "bmVkPC9zcGFuPjxiPiR7bW9uZXkoRUFSTi5lYXJuZWQpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xh"
    "c3M9Imt2Ij48c3Bhbj5Zb3VyIGxpbms8L3NwYW4+PGIgc3R5bGU9ImZvbnQtc2l6ZTouNzJyZW0i"
    "PiR7ZXNjKEVBUk4ubGluayl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMnB4Ij48YnV0dG9uIGNsYXNzPSJidG4iIGRhdGEtY29weT0icmVmIj4k"
    "e2ljKCJjb3B5Iil9IENvcHkgTGluazwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNv"
    "ZnQiIGRhdGEtY29weT0icmVmIj5TaGFyZTwvYnV0dG9uPjwvZGl2PgogICAgJHtHKCJyZWZEYXNo"
    "IixgPGRpdiBjbGFzcz0ic2VjIiBzdHlsZT0ibWFyZ2luLXRvcDo2cHgiPjxoMiBzdHlsZT0iZm9u"
    "dC1zaXplOi43OHJlbSI+UmVjZW50IGludml0ZXM8L2gyPjwvZGl2PgogICAgJHtFQVJOLnJlY2Vu"
    "dC5sZW5ndGg/RUFSTi5yZWNlbnQubWFwKChbdSxhLGVdKT0+YDxkaXYgY2xhc3M9ImZlZWRsaW5l"
    "Ij48Yj4ke2VzYyh1KX08L2I+ICR7ZXNjKGEpfTxzcGFuIHN0eWxlPSJtYXJnaW4tbGVmdDphdXRv"
    "O2NvbG9yOnZhcigtLW9rKSI+JHtlc2MoZSl9PC9zcGFuPjwvZGl2PmApLmpvaW4oIiIpCiAgICAg"
    "IDonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5Ob2JvZHkgaGFzIGpvaW5lZCB0aHJv"
    "dWdoIHlvdXIgbGluayB5ZXQuPC9wPid9CiAgICA8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2lu"
    "OjExcHggMCAwIj5SZWZlcnJhbCBlYXJuaW5ncyBsYW5kIGluIHlvdXIgYmFsYW5jZSBhdXRvbWF0"
    "aWNhbGx5IOKAlCBub3RoaW5nIHRvIGNsYWltLjwvcD5gKX0KICA8L2Rpdj5gOiIifQogICR7Rygi"
    "bGVhZGVyYm9hcmQiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+VGhpcyB3ZWVrJ3MgdG9wIGJ1eWVy"
    "czwvaDM+PHAgY2xhc3M9InN1YiI+TnVtYmVyIG9uZSB3aW5zIOKCuTUwMCBvbiBTdW5kYXkuPC9w"
    "PgogICAgJHtFQVJOLmxlYWRlcnMubGVuZ3RoP0VBUk4ubGVhZGVycy5tYXAoKFtyLHUsdl0saSk9"
    "PmA8ZGl2IGNsYXNzPSJsYiI+PHNwYW4gY2xhc3M9InJrICR7aT09PTA/ImciOiIifSI+JHtlc2Mo"
    "cil9PC9zcGFuPgogICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5"
    "bGU9ImZvbnQtc2l6ZTouOHJlbSI+JHtlc2ModSl9PC9iPjwvZGl2PjxiIHN0eWxlPSJmb250LXNp"
    "emU6LjhyZW0iPiR7ZXNjKHYpfTwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICA6JzxwIGNsYXNz"
    "PSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gcHVyY2hhc2VzIHlldCB0aGlzIHdlZWsuPC9wPid9"
    "PC9kaXY+YCl9CiAgJHtHKCJ3aW5iYWNrIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9"
    "ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgPHNwYW4gY2xh"
    "c3M9InBpbGwgaG90Ij4ke2ljKCJmaXJlIil9IENvbWViYWNrPC9zcGFuPgogICAgPGRpdiBzdHls"
    "ZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+"
    "MTAlIG9mZiDigJQgZXhwaXJlcyBpbiA0OCBob3VyczwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIHhzIiBpZD0ib2ZmZXJ1c2UiPlVzZTwvYnV0dG9uPjwvZGl2PmApfQogIDxkaXYgY2xhc3M9"
    "InNlYyI+PGgyPlRhc2tzPC9oMj48L2Rpdj4KICAke0VBUk4udGFza3MubWFwKChbdCxyLGRvbmVd"
    "KT0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRl"
    "bXM6Y2VudGVyO2dhcDoxMXB4Ij4KICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtkb25lP2ljKCJj"
    "aGVjayIpOmljKCJ6YXAiKX08L2Rpdj4KICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0"
    "aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44MnJlbSI+JHt0fTwvYj48YnI+PHNtYWxsIHN0eWxl"
    "PSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouN3JlbSI+UmV3YXJkICR7cn08L3NtYWxsPjwv"
    "ZGl2PgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgJHtkb25lPyJzb2Z0IjoiIn0iICR7ZG9u"
    "ZT8iZGlzYWJsZWQiOiIifSBkYXRhLWFjdD0idGFzayI+JHtkb25lPyJEb25lIjoiU3RhcnQifTwv"
    "YnV0dG9uPjwvZGl2PmApLmpvaW4oIiIpfWA7Cn0KCmZ1bmN0aW9uIHZXYWxsZXQoKXsKICByZXR1"
    "cm4gYDxkaXYgY2xhc3M9Imhlcm8iIHN0eWxlPSJ0ZXh0LWFsaWduOmNlbnRlciI+CiAgICA8ZGl2"
    "IGNsYXNzPSJraWNrIj4ke0NNUy53YWxLaWNrfTwvZGl2PgogICAgPGgxIHN0eWxlPSJmb250LXNp"
    "emU6Mi4zcmVtIj7igrk8c3BhbiBpZD0iYmFsMiIgY2xhc3M9InRpY2siPiR7QkFMLnRvRml4ZWQo"
    "Mil9PC9zcGFuPjwvaDE+CiAgICA8cCBzdHlsZT0ibWFyZ2luOjAgYXV0byAxNHB4Ij4ke0NNUy53"
    "YWxTdWJ9PC9wPgogICAgPGRpdiBzdHlsZT0ibWFyZ2luOjAgYXV0bzttYXgtd2lkdGg6MzAwcHgi"
    "PgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBkYXRhLWRlcD4ke2ljKCJwbHVz"
    "Iil9ICR7Q01TLndhbEFkZH08L2J1dHRvbj4KICAgICAgJHtHKCJjcnlwdG8iLGA8YnV0dG9uIGNs"
    "YXNzPSJidG4gZ2xhc3MgYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgZGF0YS1jcnlwdG8+"
    "UGF5IHdpdGggQ3J5cHRvPC9idXR0b24+YCl9PC9kaXY+PC9kaXY+CiAgPGRpdiBjbGFzcz0iY2Fy"
    "ZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgi"
    "PgogICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5BdXRvLXZlcmlm"
    "eSBPTjwvc3Bhbj4KICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6"
    "Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlRvcC11cHMgYXJlIGNyZWRpdGVkIGFzIHNvb24gYXMg"
    "dGhleSBhcnJpdmU8L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWFj"
    "dD0ic3RhdHVzIj5TdGF0dXM8L2J1dHRvbj48L2Rpdj4KICAke0coImRlcEJvbnVzIixgPGRpdiBj"
    "bGFzcz0iY2FyZCI+PGgzPlRvcC11cCBib251czwvaDM+PHAgY2xhc3M9InN1YiI+RXh0cmEgY3Jl"
    "ZGl0IGFkZGVkIGF1dG9tYXRpY2FsbHkuPC9wPgogICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4"
    "O2dhcDo3cHg7bWFyZ2luLXRvcDoxMHB4Ij4KICAgICAgJHtbWyLigrk1MDAiLCIrNSUiXSxbIuKC"
    "uTEwMDAiLCIrOCUiXSxbIuKCuTIwMDAiLCIrMTIlIl1dLm1hcCgoW2EsYl0pPT5gPGRpdiBzdHls"
    "ZT0iZmxleDoxO3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6OXB4IDRweDtib3JkZXItcmFkaXVz"
    "OjEycHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQy"
    "KSI+CiAgICAgICAgPGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbTtkaXNwbGF5OmJsb2NrIj4ke2F9"
    "PC9iPjxzbWFsbCBzdHlsZT0iZm9udC1zaXplOi42OHJlbTtjb2xvcjp2YXIoLS1vaykiPiR7Yn08"
    "L3NtYWxsPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PjwvZGl2PmApfQogIDxkaXYgY2xhc3M9InNl"
    "YyI+PGgyPlF1aWNrIFRvcC11cDwvaDI+PC9kaXY+CiAgPGRpdiBjbGFzcz0iZ3JpZCIgc3R5bGU9"
    "ImdyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoMywxZnIpIj4KICAgICR7WzUwLDEwMCwyMDAs"
    "NTAwLDEwMDAsMjAwMF0ubWFwKGE9PmA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0YS1hbXQ9"
    "IiR7YX0iPuKCuSR7YX08L2J1dHRvbj5gKS5qb2luKCIiKX08L2Rpdj4KICAke0coImRlcExpbWl0"
    "cyIsYDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46OXB4IDAgMDtmb250LXNpemU6LjdyZW0i"
    "Pk1pbmltdW0g4oK5MTAgwrcgbWF4aW11bSDigrkyMCwwMDAgcGVyIHRvcC11cC48L3A+YCl9CiAg"
    "PGRpdiBjbGFzcz0iY2FyZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGgzPiR7Q01TLnBheVRp"
    "dGxlfTwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj4ke0NNUy5wYXlTdWJ9PC9wPgogICAgPGRpdiBj"
    "bGFzcz0ia3YiPjxzcGFuPlVQSSBJRDwvc3Bhbj48Yj4ke2VzYyhTVE9SRS51cGl8fCJub3Qgc2V0"
    "IHlldCIpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5QYXllZTwvc3Bhbj48"
    "Yj4ke2VzYyhTVE9SRS5wYXllZXx8IuKAlCIpfTwvYj48L2Rpdj4KICAgICR7RygidXBpUm90YXRl"
    "IixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkhhbmRsZXMgaW4gcm90YXRpb248L3NwYW4+PGI+MyBh"
    "Y3RpdmU8L2I+PC9kaXY+YCl9CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+UGF5ZWU8L3NwYW4+"
    "PGI+WW91ciBTdG9yZSBOYW1lPC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkF1"
    "dG8tdmVyaWZ5PC9zcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS1vaykiPkVuYWJsZWQ8L2I+PC9k"
    "aXY+CiAgICAke0coIndyb25nQW10IixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPldyb25nIGFtb3Vu"
    "dDwvc3Bhbj48Yj5DcmVkaXRlZCBhcyBwYWlkPC9iPjwvZGl2PmApfQogICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIGJsb2NrIHNvZnQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEtY29weT0idXBp"
    "Ij4ke2ljKCJjb3B5Iil9IENvcHkgVVBJIElEPC9idXR0b24+PC9kaXY+CiAgJHtHKCJhYmFuZG9u"
    "UmVtaW5kIixgPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjoxMHB4IDAgMDtmb250LXNpemU6"
    "LjdyZW0iPldlIHdpbGwgcmVtaW5kIHlvdSBiZWZvcmUgYW4gdW5maW5pc2hlZCBwYXltZW50IGV4"
    "cGlyZXMuPC9wPmApfQogIDxkaXYgY2xhc3M9InNlYyI+PGgyPiR7RkVBVC5sZWRnZXI/IlN0YXRl"
    "bWVudCI6Ikhpc3RvcnkifTwvaDI+JHtHKCJsZWRnZXIiLGA8YSBkYXRhLWFkbWluLWFjdD0iZXhw"
    "b3J0Ij5FeHBvcnQ8L2E+YCl9PC9kaXY+CiAgJHtUWC5tYXAoKHQsaSk9PmA8ZGl2IGNsYXNzPSJj"
    "YXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTFw"
    "eCI+CiAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke3QuYVswXT09PSIrIj9pYygiZG93biIpOmljKCJ1"
    "cCIpfTwvZGl2PgogICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0i"
    "Zm9udC1zaXplOi44MnJlbSI+JHt0LnR9PC9iPjxicj48c21hbGwgc3R5bGU9ImNvbG9yOnZhcigt"
    "LXR4Mik7Zm9udC1zaXplOi43cmVtIj4ke3Qud308L3NtYWxsPjwvZGl2PgogICAgPGRpdiBzdHls"
    "ZT0idGV4dC1hbGlnbjpyaWdodCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODVyZW07Y29sb3I6JHt0"
    "LmFbMF09PT0iKyI/InZhcigtLW9rKSI6InZhcigtLXR4KSJ9Ij4ke3QuYX08L2I+CiAgICAke0co"
    "ImxlZGdlciIsYDxicj48c21hbGwgc3R5bGU9ImZvbnQtc2l6ZTouNjVyZW07Y29sb3I6dmFyKC0t"
    "dHgyKSI+YmFsIOKCuSR7KDI0OC41K2kqNykudG9GaXhlZCgyKX08L3NtYWxsPmApfTwvZGl2Pjwv"
    "ZGl2PmApLmpvaW4oIiIpfWA7Cn0KCmZ1bmN0aW9uIHZZb3UoKXsKICBjb25zdCBtZT1NRXx8e307"
    "CiAgY29uc3Qgc3BlbnQ9bWUuc3BlbnR8fDAsIHRhcmdldD1zcGVudD49NTAwPzIwMDA6NTAwOwog"
    "IGNvbnN0IHBjdD1NYXRoLm1heCg0LE1hdGgubWluKDEwMCxNYXRoLnJvdW5kKHNwZW50L3Rhcmdl"
    "dCoxMDApKSk7CiAgY29uc3QgYXY9bWUucGhvdG8/YDxpbWcgc3JjPSIke21lLnBob3RvfSIgYWx0"
    "PSIiIHN0eWxlPSJ3aWR0aDo2NHB4O2hlaWdodDo2NHB4O2JvcmRlci1yYWRpdXM6NTAlO29iamVj"
    "dC1maXQ6Y292ZXI7ZGlzcGxheTpibG9jazttYXJnaW46MCBhdXRvIDEwcHgiIG9uZXJyb3I9InRo"
    "aXMucmVwbGFjZVdpdGgoT2JqZWN0LmFzc2lnbihkb2N1bWVudC5jcmVhdGVFbGVtZW50KCdkaXYn"
    "KSx7Y2xhc3NOYW1lOidmbGFnJyxzdHlsZTond2lkdGg6NjRweDtoZWlnaHQ6NjRweDttYXJnaW46"
    "MCBhdXRvIDEwcHgnfSkpIj5gCiAgICA6YDxkaXYgY2xhc3M9ImZsYWciIHN0eWxlPSJ3aWR0aDo2"
    "NHB4O2hlaWdodDo2NHB4O21hcmdpbjowIGF1dG8gMTBweCI+JHtpYygidXNlciIpfTwvZGl2PmA7"
    "CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIHNoaW5lY2FyZCB0aWx0IiBkYXRhLXRpbHQgc3R5"
    "bGU9InRleHQtYWxpZ246Y2VudGVyIj48c3BhbiBjbGFzcz0iZ2xhcmUiPjwvc3Bhbj4KICAgICR7"
    "YXZ9CiAgICA8aDMgc3R5bGU9ImZvbnQtc2l6ZToxLjA1cmVtIj4ke2VzYyhtZS5uYW1lfHwiWW91"
    "Iil9PC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPiR7ZXNjKG1lLnVzZXJuYW1lfHwoIklEICIrKG1l"
    "LmlkfHwi4oCUIikpKX0ke21lLnVzZXJuYW1lPyIgwrcgSUQgIisobWUuaWR8fCLigJQiKToiIn0k"
    "e21lLmpvaW5lZD8iIMK3IGpvaW5lZCAiK2VzYyhtZS5qb2luZWQpOiIifTwvcD4KICAgIDxkaXYg"
    "c3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4O2p1c3RpZnktY29udGVudDpjZW50ZXI7bWFyZ2lu"
    "LXRvcDoxMHB4O2ZsZXgtd3JhcDp3cmFwIj4KICAgICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPiR7"
    "ZXNjKG1lLnRpZXJ8fCJCcm9uemUiKX08L3NwYW4+PHNwYW4gY2xhc3M9InBpbGwiPiR7bWUub3Jk"
    "ZXJzfHwwfSBvcmRlcnM8L3NwYW4+CiAgICAgIDxzcGFuIGNsYXNzPSJwaWxsIj4ke21vbmV5KG1l"
    "LmJhbGFuY2V8fDApfTwvc3Bhbj4KICAgICAgJHtHKCJsb3lhbHR5IixgPHNwYW4gY2xhc3M9InBp"
    "bGwiPiR7bWUucG9pbnRzfHwwfSBwdHM8L3NwYW4+YCl9CiAgICAgICR7Rygic3RyZWFrIixgPHNw"
    "YW4gY2xhc3M9InBpbGwiPiR7bWUuc3RyZWFrfHwwfS1kYXkgc3RyZWFrPC9zcGFuPmApfQogICAg"
    "ICAkeyhGRUFULmJ1eWVyQmFkZ2UmJihtZS5vcmRlcnN8fDApPj01KT9gPHNwYW4gY2xhc3M9InBp"
    "bGwgb24iPiR7aWMoImNoZWNrIil9IFZlcmlmaWVkIGJ1eWVyPC9zcGFuPmA6IiJ9CiAgICAgICR7"
    "KG1lLmRlYnQ+MCk/YDxzcGFuIGNsYXNzPSJwaWxsIGhvdCI+JHttb25leShtZS5kZWJ0KX0gdWRo"
    "YXI8L3NwYW4+YDoiIn08L2Rpdj4KICAgIDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6MTRweDtoZWln"
    "aHQ6N3B4O2JvcmRlci1yYWRpdXM6OXB4O2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO292ZXJmbG93"
    "OmhpZGRlbiI+CiAgICAgIDxpIHN0eWxlPSJkaXNwbGF5OmJsb2NrO2hlaWdodDoxMDAlO3dpZHRo"
    "OiR7cGN0fSU7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCkiPjwvaT48L2Rpdj4KICAgIDxwIGNs"
    "YXNzPSJzdWIiIHN0eWxlPSJtYXJnaW4tdG9wOjdweDtmb250LXNpemU6LjcycmVtIj4ke21lLm5l"
    "eHRfdGllcj4wCiAgICAgID8gbW9uZXkobWUubmV4dF90aWVyKSsiIG1vcmUgc3BlbnQgdG8gcmVh"
    "Y2ggIisoc3BlbnQ+PTUwMD8iUGxhdGludW0iOiJHb2xkIikKICAgICAgOiAiVG9wIHRpZXIgcmVh"
    "Y2hlZCJ9PC9wPjwvZGl2PgogICR7Rygid2lzaGxpc3QiLGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5T"
    "YXZlZCBjb3VudHJpZXM8L2gyPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iY2FyZCI+JHtXSVNILmxl"
    "bmd0aD9XSVNILm1hcChpPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7U1RPQ0tbaV0uZn0gJHtl"
    "c2MoU1RPQ0tbaV0uYyl9ICR7U1RPQ0tbaV0uY29kZX08L3NwYW4+CiAgICAgIDxiPjxidXR0b24g"
    "Y2xhc3M9ImJ0biB4cyIgZGF0YS1idXk9IiR7aX0iPkJ1eTwvYnV0dG9uPjwvYj48L2Rpdj5gKS5q"
    "b2luKCIiKTonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5UYXAgdGhlIGhlYXJ0IG9u"
    "IGFueSBjb3VudHJ5IHRvIHNhdmUgaXQgaGVyZS48L3A+J308L2Rpdj5gKX0KICA8ZGl2IGNsYXNz"
    "PSJzZWMiPjxoMj5BY2NvdW50PC9oMj48L2Rpdj4KICA8ZGl2IGNsYXNzPSJjYXJkIj4ke1tbIk9y"
    "ZGVyIGhpc3RvcnkiLCJvcmRlcnMiXSxbIldhbGxldCAmIHBheW1lbnRzIiwid2FsbGV0Il1dCiAg"
    "ICAuY29uY2F0KGFueUYoInJlZkRhc2giLCJyZWZMMiIpP1tbIlJlZmVycmFsIHByb2dyYW0iLCJl"
    "YXJuIl1dOltdKQogICAgLm1hcCgoW3QsZ10pPT5gPGRpdiBjbGFzcz0ia3YiIHN0eWxlPSJjdXJz"
    "b3I6cG9pbnRlciIgZGF0YS1nbz0iJHtnfSI+PHNwYW4gc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+"
    "JHt0fTwvc3Bhbj48Yj4ke2ljKCJjaGV2Iil9PC9iPjwvZGl2PmApLmpvaW4oIiIpfQogICAgJHtH"
    "KCJpMThuIixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPkxh"
    "bmd1YWdlPC9zcGFuPjxiPgogICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIHN0eWxlPSJoZWlnaHQ6"
    "MzJweDt3aWR0aDphdXRvO2ZvbnQtc2l6ZTouNzRyZW0iPjxvcHRpb24+RW5nbGlzaDwvb3B0aW9u"
    "PjxvcHRpb24+4KS54KS/4KSo4KWN4KSm4KWAPC9vcHRpb24+PC9zZWxlY3Q+PC9iPjwvZGl2PmAp"
    "fTwvZGl2PgogICR7Rygibm90aWZQcmVmcyIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPk5vdGlmaWNh"
    "dGlvbnM8L2gyPjwvZGl2PgogIDxkaXYgY2xhc3M9ImNhcmQiPgogICAgPGRpdiBjbGFzcz0ic3dh"
    "cCI+PGRpdiBjbGFzcz0idCI+UmVzdG9jayBhbGVydHM8c21hbGw+RE0gbWUgd2hlbiBteSBjb3Vu"
    "dHJ5IGlzIGJhY2s8L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyAke1BSRUZTLnJl"
    "c3RvY2s/Im9uIjoiIn0iIGRhdGEtcHJlZj0icmVzdG9jayI+PC9kaXY+PC9kaXY+CiAgICAke0co"
    "InJlY2VpcHRzIixgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+T3JkZXIgcmVjZWlw"
    "dHM8c21hbGw+U2VuZCBhIHJlY2VpcHQgYWZ0ZXIgZWFjaCBidXk8L3NtYWxsPjwvZGl2PgogICAg"
    "ICA8ZGl2IGNsYXNzPSJ0ZyAke1BSRUZTLnJlY2VpcHRzPyJvbiI6IiJ9IiBkYXRhLXByZWY9InJl"
    "Y2VpcHRzIj48L2Rpdj48L2Rpdj5gKX0KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9"
    "InQiPlByb21vIG1lc3NhZ2VzPHNtYWxsPkZsYXNoIHNhbGVzIGFuZCBnaXZlYXdheXM8L3NtYWxs"
    "PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyAke1BSRUZTLnByb21vcz8ib24iOiIifSIgZGF0"
    "YS1wcmVmPSJwcm9tb3MiPjwvZGl2PjwvZGl2PgogICAgJHtHKCJwdXNoTm90aWYiLGA8ZGl2IGNs"
    "YXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5NaW5pIGFwcCBwdXNoPHNtYWxsPk1lc3NhZ2UgbWUg"
    "d2hlbiBzb21ldGhpbmcgaGFwcGVuczwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InRn"
    "ICR7UFJFRlMucHVzaD8ib24iOiIifSIgZGF0YS1wcmVmPSJwdXNoIj48L2Rpdj48L2Rpdj5gKX0K"
    "ICA8L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgi"
    "PjxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBpZD0ic3VwcG9ydGdvIj5TdXBwb3J0PC9idXR0b24+"
    "CiAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IiBpZD0idGVybXNnbyI+VGVybXM8L2J1dHRvbj48"
    "L2Rpdj5gOwp9CgpmdW5jdGlvbiBiaW5kQnV5KCl7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiW2RhdGEtd2lzaF0iKS5mb3JFYWNoKGVsPT5lbC5vbmNsaWNrPWU9PnsKICAgIGUuc3RvcFBy"
    "b3BhZ2F0aW9uKCk7Y29uc3QgaT0rZWwuZGF0YXNldC53aXNoOwogICAgY29uc3Qgaz1XSVNILmlu"
    "ZGV4T2YoaSk7aWYoazwwKVdJU0gucHVzaChpKTtlbHNlIFdJU0guc3BsaWNlKGssMSk7CiAgICB0"
    "b2FzdChrPDA/IlNhdmVkIHRvIHdpc2hsaXN0IjoiUmVtb3ZlZCBmcm9tIHdpc2hsaXN0Iik7YXBw"
    "bHlGaWx0ZXIoKTt9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1idXldIiku"
    "Zm9yRWFjaChlbD0+ZWwub25jbGljaz1lPT57CiAgICBpZihlLnRhcmdldC5jbG9zZXN0KCJbZGF0"
    "YS10b2FzdF0sW2RhdGEtd2lzaF0iKSlyZXR1cm47Y29uc3Qgcz1TVE9DS1srZWwuZGF0YXNldC5i"
    "dXldOwogICAgaWYocy5xdHk9PT0wJiYhZmFsc2Upe3RvYXN0KCJBbGVydCBzZXQg4oCUIHlvdSds"
    "bCBnZXQgYSBETSBvbiByZXN0b2NrIik7cmV0dXJufQogICAgYnV5U2hlZXQocyxzLnF0eT09PTAp"
    "O30pOwp9CmxldCBRVFk9MSxDT1VQT049MDsKZnVuY3Rpb24gYnV5U2hlZXQocyxwcmUpewogIFFU"
    "WT0xO0NPVVBPTj0wOwogIGNvbnN0IG1heD1GRUFULmNhcnR8fEZFQVQuYnVsa0Rpc2M/TWF0aC5t"
    "YXgoMSxNYXRoLm1pbigyNSxzLnF0eXx8NSkpOjE7CiAgc2hlZXQoYDxkaXYgc3R5bGU9ImRpc3Bs"
    "YXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEycHg7bWFyZ2luLWJvdHRvbToxNHB4Ij4K"
    "ICAgICAgPGRpdiBjbGFzcz0iZmxhZyIgc3R5bGU9IndpZHRoOjUwcHg7aGVpZ2h0OjUwcHg7Zm9u"
    "dC1zaXplOjEuNXJlbSI+JHtzLmZ9PC9kaXY+CiAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MSI+PGgz"
    "IHN0eWxlPSJtYXJnaW46MDtmb250LXNpemU6MXJlbSI+JHtlc2Mocy5jKX0gQWNjb3VudDwvaDM+"
    "CiAgICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+JHtzLmNvZGV9IMK3ICR7cy5x"
    "dHkrIiBpbiBzdG9jayDCtyBpbnN0YW50In08L3A+CiAgICAgICR7RygicmV2aWV3cyIsYDxkaXYg"
    "c3R5bGU9Im1hcmdpbi10b3A6NHB4Ij4ke3N0YXJzKHMuYyl9PC9kaXY+YCl9PC9kaXY+PC9kaXY+"
    "CiAgICAke0coImhlYWx0aENoZWNrIixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlNlc3Npb24gaGVh"
    "bHRoPC9zcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS1vaykiPiR7aWMoImNoZWNrIil9IGNoZWNr"
    "ZWQgbGl2ZTwvYj48L2Rpdj5gKX0KICAgICR7RygidmFyaWFudHMiLGA8ZGl2IGNsYXNzPSJrdiI+"
    "PHNwYW4+VmFyaWFudDwvc3Bhbj48Yj4KICAgICAgPHNlbGVjdCBjbGFzcz0iaW5wIiBzdHlsZT0i"
    "aGVpZ2h0OjMycHg7d2lkdGg6YXV0bztmb250LXNpemU6Ljc0cmVtIj48b3B0aW9uPk5vIDJGQTwv"
    "b3B0aW9uPjxvcHRpb24+MkZBIG9uICgr4oK5NSk8L29wdGlvbj48b3B0aW9uPldpdGggbWFpbCAo"
    "K+KCuTgpPC9vcHRpb24+PC9zZWxlY3Q+PC9iPjwvZGl2PmApfQogICAgJHsoRkVBVC5jYXJ0fHxG"
    "RUFULmJ1bGtEaXNjKT9gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlF1YW50aXR5PC9zcGFuPjxiPgog"
    "ICAgICA8c3BhbiBjbGFzcz0icXR5Ij48YnV0dG9uIGlkPSJxbSI+4oiSPC9idXR0b24+PGIgaWQ9"
    "InFuIj4xPC9iPjxidXR0b24gaWQ9InFwIj4rPC9idXR0b24+PC9zcGFuPjwvYj48L2Rpdj5gOiIi"
    "fQogICAgJHtHKCJidWxrRGlzYyIsYDxwIGNsYXNzPSJzdWIiIGlkPSJiZG5vdGUiIHN0eWxlPSJt"
    "YXJnaW46NnB4IDAgMDtmb250LXNpemU6LjdyZW0iPkJ1eSA1KyBmb3IgNSUgb2ZmIMK3IDEwKyBm"
    "b3IgMTAlIG9mZiDCtyAyNSsgZm9yIDE1JSBvZmYuPC9wPmApfQogICAgJHtHKCJjb3Vwb25zIixg"
    "PGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxsYWJlbD5Db3Vwb24g"
    "Y29kZTwvbGFiZWw+CiAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4Ij48aW5w"
    "dXQgY2xhc3M9ImlucCIgaWQ9ImNwbiIgcGxhY2Vob2xkZXI9IldFTENPTUUxMCI+CiAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBzbSIgaWQ9ImNwbmFwcGx5IiBzdHlsZT0iZmxleDowIDAgYXV0byI+"
    "QXBwbHk8L2J1dHRvbj48L2Rpdj48L2Rpdj5gKX0KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Q"
    "cmljZTwvc3Bhbj48YiBpZD0iYnByaWNlIj4ke21vbmV5KHMucHJpY2UpfTwvYj48L2Rpdj4KICAg"
    "IDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Zb3VyIGJhbGFuY2U8L3NwYW4+PGI+JHttb25leShCQUwp"
    "fTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5BZnRlciBwdXJjaGFzZTwvc3Bh"
    "bj48YiBpZD0iYmFmdGVyIj4ke21vbmV5KE1hdGgubWF4KDAsQkFMLXMucHJpY2UpKX08L2I+PC9k"
    "aXY+CiAgICA8ZGl2IGlkPSJzcGxpdG5vdGUiPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4Ij48YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEt"
    "Y2xvc2U+Q2FuY2VsPC9idXR0b24+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJi"
    "Y29uZmlybSI+Q29uZmlybSBCdXk8L2J1dHRvbj48L2Rpdj4KICAgICR7RygiY2FydCIsYDxidXR0"
    "b24gY2xhc3M9ImJ0biBzb2Z0IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGlkPSJi"
    "YWRkY2FydCI+QWRkIHRvIGNhcnQ8L2J1dHRvbj5gKX1gKTsKICBjb25zdCBnPWlkPT5kb2N1bWVu"
    "dC5nZXRFbGVtZW50QnlJZChpZCk7CiAgZnVuY3Rpb24gdG90YWwoKXsKICAgIGxldCB1bml0PXMu"
    "cHJpY2UsIHE9UVRZLCB0PXVuaXQqcTsKICAgIGlmKEZFQVQuYnVsa0Rpc2Mpe2NvbnN0IGQ9cT49"
    "MjU/LjE1OnE+PTEwPy4xMDpxPj01Py4wNTowO3Q9dCooMS1kKTt9CiAgICBpZihGRUFULmNvdXBv"
    "bnMmJkNPVVBPTil0PXQqKDEtQ09VUE9OLzEwMCk7CiAgICByZXR1cm4gTWF0aC5yb3VuZCh0KjEw"
    "MCkvMTAwOwogIH0KICBmdW5jdGlvbiBwYWludCgpewogICAgY29uc3QgdD10b3RhbCgpOwogICAg"
    "ZygiYnByaWNlIikudGV4dENvbnRlbnQ9bW9uZXkodCk7CiAgICBnKCJiYWZ0ZXIiKS50ZXh0Q29u"
    "dGVudD1tb25leShNYXRoLm1heCgwLEJBTC10KSk7CiAgICBjb25zdCBzbj1nKCJzcGxpdG5vdGUi"
    "KTsKICAgIGlmKEZFQVQuc3BsaXRQYXkmJnQ+QkFMKXtzbi5pbm5lckhUTUw9YDxkaXYgY2xhc3M9"
    "Imt2Ij48c3Bhbj5TcGxpdCBwYXltZW50PC9zcGFuPjxiPiR7bW9uZXkoQkFMKX0gd2FsbGV0ICsg"
    "JHttb25leSh0LUJBTCl9IFVQSTwvYj48L2Rpdj5gO30KICAgIGVsc2Ugc24uaW5uZXJIVE1MPSIi"
    "OwogICAgaWYoZygicW4iKSlnKCJxbiIpLnRleHRDb250ZW50PVFUWTsKICB9CiAgaWYoZygicW0i"
    "KSlnKCJxbSIpLm9uY2xpY2s9KCk9PntRVFk9TWF0aC5tYXgoMSxRVFktMSk7cGFpbnQoKTt9Owog"
    "IGlmKGcoInFwIikpZygicXAiKS5vbmNsaWNrPSgpPT57UVRZPU1hdGgubWluKG1heCxRVFkrMSk7"
    "cGFpbnQoKTt9OwogIGlmKGcoImNwbiIpJiZPRkZFUiYmIWcoImNwbiIpLnZhbHVlKWcoImNwbiIp"
    "LnZhbHVlPU9GRkVSOwogIGlmKGcoImNwbmFwcGx5IikpZygiY3BuYXBwbHkiKS5vbmNsaWNrPSgp"
    "PT57CiAgICBjb25zdCB2PShnKCJjcG4iKS52YWx1ZXx8IiIpLnRyaW0oKS50b1VwcGVyQ2FzZSgp"
    "OwogICAgaWYoIXYpe3RvYXN0KCJFbnRlciBhIGNvdXBvbiBjb2RlIik7cmV0dXJufQogICAgQ09V"
    "UE9OPXY9PT0iV0VMQ09NRTEwIj8xMDp2PT09IlNBVkUyMCI/MjA6MDsKICAgIHRvYXN0KENPVVBP"
    "Tj9DT1VQT04rIiUgY291cG9uIGFwcGxpZWQiOiJDb3Vwb24gbm90IHZhbGlkIik7cGFpbnQoKTt9"
    "OwogIGlmKGcoImJhZGRjYXJ0IikpZygiYmFkZGNhcnQiKS5vbmNsaWNrPWFzeW5jKCk9PnsKICAg"
    "IGlmKExJVkUpewogICAgICBjb25zdCBkPWF3YWl0IGFwaVRyeSgiL2FwaS9jYXJ0Iix7YWN0aW9u"
    "OiJhZGQiLGNvdW50cnk6cy5jLHF0eTpRVFl9KTsKICAgICAgaWYoIWQpcmV0dXJuOwogICAgICBj"
    "bG9zZVNoZWV0KCk7dG9hc3QoUVRZKyIgw5cgIitzLmMrIiBhZGRlZCB0byBjYXJ0Iik7YXdhaXQg"
    "cmVmcmVzaCgpO3JldHVybjsKICAgIH0KICAgIGZvcihsZXQgaT0wO2k8UVRZO2krKylDQVJULnB1"
    "c2gocyk7YXBwbHlGZWF0dXJlcygpO2Nsb3NlU2hlZXQoKTt0b2FzdChRVFkrIiDDlyAiK3MuYysi"
    "IGFkZGVkIHRvIGNhcnQiKTt9OwogIGcoImJjb25maXJtIikub25jbGljaz1hc3luYygpPT57CiAg"
    "ICBjb25zdCB0PXRvdGFsKCk7CiAgICBpZihCQUw8dCYmIUZFQVQuc3BsaXRQYXkpe3RvYXN0KCJO"
    "b3QgZW5vdWdoIGJhbGFuY2Ug4oCUIGFkZCBmdW5kcyIpO3JldHVybn0KICAgIGlmKEJBTDx0JiZG"
    "RUFULnNwbGl0UGF5KXtjbG9zZVNoZWV0KCk7dG9hc3QoIlBheWluZyAiK21vbmV5KHQtQkFMKSsi"
    "IGJ5IFVQSSIpO2RlcG9zaXRTaGVldChNYXRoLmNlaWwodC1CQUwpKTtyZXR1cm59CiAgICBpZihM"
    "SVZFKXsKICAgICAgY29uc3QgYnRuPWcoImJjb25maXJtIik7YnRuLmRpc2FibGVkPXRydWU7YnRu"
    "LnRleHRDb250ZW50PSJXb3JraW5n4oCmIjsKICAgICAgY29uc3QgY29kZT1nKCJjcG4iKT8oZygi"
    "Y3BuIikudmFsdWV8fCIiKS50cmltKCk6IiI7CiAgICAgIGNvbnN0IGQ9YXdhaXQgYXBpVHJ5KCIv"
    "YXBpL2J1eSIse2NvdW50cnk6cy5jLHF0eTpRVFksY291cG9uOmNvZGV9KTsKICAgICAgYnRuLmRp"
    "c2FibGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0iQ29uZmlybSBCdXkiOwogICAgICBpZighZCly"
    "ZXR1cm47CiAgICAgIGlmKHR5cGVvZiBkLmJhbGFuY2U9PT0ibnVtYmVyIilCQUw9ZC5iYWxhbmNl"
    "OwogICAgICBzeW5jQmFsKCk7Y2xvc2VTaGVldCgpO2J1cnN0KCk7CiAgICAgIGNvbnN0IGZpcnN0"
    "PShkLm9yZGVycyYmZC5vcmRlcnNbMF0pfHxudWxsOwogICAgICB0b2FzdChmaXJzdD8oIkRlbGl2"
    "ZXJlZCDigJQgIitmaXJzdC5pZCk6IlB1cmNoYXNlZCDigJQgZGV0YWlscyBpbiBPcmRlcnMiKTsK"
    "ICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgICBpZihmaXJzdCYmZmlyc3QucGF5bG9hZClzZXRU"
    "aW1lb3V0KCgpPT5zaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJl"
    "bSI+JHtlc2MoZmlyc3QuaWQpfTwvaDM+CiAgICAgICAgPHAgY2xhc3M9InN1YiI+S2VlcCB0aGlz"
    "IHNhZmUuIEl0IGlzIGFsc28gaW4gT3JkZXJzLjwvcD4KICAgICAgICA8ZGl2IGNsYXNzPSJrdiIg"
    "c3R5bGU9ImRpc3BsYXk6YmxvY2siPjxjb2RlIHN0eWxlPSJmb250LXNpemU6LjcycmVtO3dvcmQt"
    "YnJlYWs6YnJlYWstYWxsIj4ke2VzYyhmaXJzdC5wYXlsb2FkKX08L2NvZGU+PC9kaXY+CiAgICAg"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4IiBkYXRh"
    "LWNsb3NlPkRvbmU8L2J1dHRvbj5gKSwzNTApOwogICAgICByZXR1cm47CiAgICB9CiAgICBCQUwt"
    "PXQ7c3luY0JhbCgpO2Nsb3NlU2hlZXQoKTtidXJzdCgpOwogICAgdG9hc3QoIlB1cmNoYXNlZCDi"
    "gJQgT1RQIGlzIGluIE9yZGVycyIpOwogICAgaWYoRkVBVC5wdXNoTm90aWYpc2V0VGltZW91dCgo"
    "KT0+dG9hc3QoIkJvdCBub3RpZmljYXRpb24gc2VudCB0byB5b3VyIGNoYXQiKSwxNTAwKTt9Owog"
    "IHBhaW50KCk7Cn0KZnVuY3Rpb24gY2FydFNoZWV0KCl7CiAgaWYoIUNBUlQubGVuZ3RoKXt0b2Fz"
    "dCgiQ2FydCBpcyBlbXB0eSIpO3JldHVybn0KICBjb25zdCB0PUNBUlQucmVkdWNlKChhLGIpPT5h"
    "K2IucHJpY2UsMCk7CiAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXpl"
    "OjFyZW0iPllvdXIgY2FydDwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj4ke0NBUlQubGVuZ3RofSBp"
    "dGVtJHtDQVJULmxlbmd0aD4xPyJzIjoiIn0g4oCUIG9uZSBjb25maXJtYXRpb24sIG9uZSBkZWJp"
    "dC48L3A+CiAgICAke0NBUlQubWFwKChzLGkpPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7cy5m"
    "fSAke2VzYyhzLmMpfSAke3MuY29kZX08L3NwYW4+CiAgICAgIDxiPiR7bW9uZXkocy5wcmljZSl9"
    "IDxidXR0b24gY2xhc3M9ImJ0biB4cyBnaG9zdCIgZGF0YS1jZGVsPSIke2l9Ij7DlzwvYnV0dG9u"
    "PjwvYj48L2Rpdj5gKS5qb2luKCIiKX0KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Ub3RhbDwv"
    "c3Bhbj48Yj4ke21vbmV5KHQpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTRweCI+PGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IiBkYXRhLWNsb3Nl"
    "PkNsb3NlPC9idXR0b24+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJjYXJ0YnV5"
    "Ij5DaGVja291dDwvYnV0dG9uPjwvZGl2PmApOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IltkYXRhLWNkZWxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PntDQVJULnNwbGljZSgrYi5k"
    "YXRhc2V0LmNkZWwsMSk7YXBwbHlGZWF0dXJlcygpO2NhcnRTaGVldCgpO30pOwogIGRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJjYXJ0YnV5Iikub25jbGljaz0oKT0+ewogICAgaWYoQkFMPHQpe3Rv"
    "YXN0KCJOb3QgZW5vdWdoIGJhbGFuY2Ug4oCUIGFkZCBmdW5kcyIpO3JldHVybn0KICAgIEJBTC09"
    "dDtDQVJUPVtdO3N5bmNCYWwoKTthcHBseUZlYXR1cmVzKCk7Y2xvc2VTaGVldCgpO2J1cnN0KCk7"
    "dG9hc3QoIk9yZGVyIHBsYWNlZCDigJQgT1RQcyBhcmUgaW4gT3JkZXJzIik7fTsKfQpmdW5jdGlv"
    "biBnb1RhYih0KXsKICBjb25zdCBiPWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoYCN0YWJzIGJ1dHRv"
    "bltkYXRhLXRhYj0iJHt0fSJdYCk7CiAgaWYoYiliLmNsaWNrKCk7Cn0KZnVuY3Rpb24gYmluZENv"
    "bW1vbigpewogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLW9yZF0iKS5mb3JFYWNo"
    "KGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCB3aGF0PWIuZGF0YXNldC5vcmQ7CiAg"
    "ICBjb25zdCBjYXJkPWIuY2xvc2VzdCgiW2RhdGEtb3JkZXItaWRdIik7CiAgICBjb25zdCBvaWQ9"
    "KGNhcmQmJmNhcmQuZGF0YXNldC5vcmRlcklkKXx8KE9SREVSU1swXSYmT1JERVJTWzBdLmlkKXx8"
    "IiI7CiAgICBpZighb2lkKXt0b2FzdCgiTm8gb3JkZXIgc2VsZWN0ZWQiKTtyZXR1cm47fQogICAg"
    "aWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgY29uc3QgbGJsPWIu"
    "dGV4dENvbnRlbnQ7Yi5kaXNhYmxlZD10cnVlO2IudGV4dENvbnRlbnQ9IuKApiI7CiAgICBjb25z"
    "dCByPWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRlciIse2lkOm9pZCx3aGF0OndoYXR9KTsKICAgIGIu"
    "ZGlzYWJsZWQ9ZmFsc2U7Yi50ZXh0Q29udGVudD1sYmw7CiAgICBpZighcilyZXR1cm47CiAgICBp"
    "Zih3aGF0PT09ImRldGFpbHMiKXRvYXN0KCJMb2dpbiBkZXRhaWxzIHNlbnQgdG8geW91ciBjaGF0"
    "Iik7CiAgICBpZih3aGF0PT09ImZpbGUiKXRvYXN0KCJTZXNzaW9uIGZpbGUgc2VudCB0byB5b3Vy"
    "IGNoYXQiKTsKICAgIGlmKHdoYXQ9PT0icmVjZWlwdCIpdG9hc3QoIlJlY2VpcHQgc2VudCB0byB5"
    "b3VyIGNoYXQiKTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1yYXRl"
    "LWdvXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHN0YXJzPSti"
    "LmRhdGFzZXQucmF0ZUdvOwogICAgY29uc3QgYz0od2luZG93Ll9fcmF0ZUNvdW50cnkpfHwoKE9S"
    "REVSU1swXXx8e30pLmMpfHwiIjsKICAgIGlmKCFMSVZFKXtjbG9zZVNoZWV0KCk7dG9hc3QoIlRo"
    "YW5rcyBmb3IgdGhlICIrc3RhcnMrIuKYhSIpO3JldHVybjt9CiAgICBjb25zdCByPWF3YWl0IGFw"
    "aVRyeSgiL2FwaS9yZXZpZXciLHtjb3VudHJ5OmMscmF0aW5nOnN0YXJzfSk7CiAgICBpZighcily"
    "ZXR1cm47CiAgICBjbG9zZVNoZWV0KCk7dG9hc3QoIlRoYW5rcyDigJQgIitjKyIgaXMgbm93ICIr"
    "ci5hdmcrIuKYhSIpOwogICAgYXdhaXQgcmVmcmVzaCgpOwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5"
    "U2VsZWN0b3JBbGwoIltkYXRhLWFjdF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57"
    "CiAgICBjb25zdCB3aGF0PWIuZGF0YXNldC5hY3Q7CiAgICBpZih3aGF0PT09ImNoYW5uZWwiKXsK"
    "ICAgICAgY29uc3QgYz0oQURNLmNoYW5uZWxzJiZBRE0uY2hhbm5lbHNbMF0pfHxudWxsOwogICAg"
    "ICBjb25zdCBoPWM/U3RyaW5nKGMuaGFuZGxlfHwiIikucmVwbGFjZSgvXkAvLCIiKToiIjsKICAg"
    "ICAgaWYoaClvcGVuUGF5KGguc3RhcnRzV2l0aCgiaHR0cCIpP2g6KCJodHRwczovL3QubWUvIito"
    "KSk7CiAgICAgIGVsc2UgdG9hc3QoIk5vIGNoYW5uZWwgaGFzIGJlZW4gYWRkZWQgeWV0Iik7CiAg"
    "ICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9PT0iZmxhc2giKXsKICAgICAgY29uc3Qgb249"
    "QURNLm1hcmtldCYmQURNLm1hcmtldC5mbGFzaF9vbjsKICAgICAgc2hlZXQoYDxoMyBzdHlsZT0i"
    "bWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkZsYXNoIHNhbGU8L2gzPgogICAgICAgIDxw"
    "IGNsYXNzPSJzdWIiPiR7b24/KCJFdmVyeSBwcmljZSBpcyAiKyhBRE0ubWFya2V0LmZsYXNofHww"
    "KSsiJSBvZmYgcmlnaHQgbm93LiIpOiJObyBzYWxlIGlzIHJ1bm5pbmcgYXQgdGhlIG1vbWVudC4i"
    "fTwvcD4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9w"
    "OjEycHgiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTsKICAgICAgcmV0dXJuOwogICAgfQog"
    "ICAgaWYod2hhdD09PSJzb3J0Iil7CiAgICAgIEZJTFRFUkVEPShGSUxURVJFRCYmRklMVEVSRUQu"
    "bGVuZ3RoP0ZJTFRFUkVEOlNUT0NLKS5zbGljZSgpLnNvcnQoKHgseSk9Pih5LnF0eXx8MCktKHgu"
    "cXR5fHwwKSk7CiAgICAgIHJlbmRlcigpO3RvYXN0KCJTb3J0ZWQgYnkgd2hhdCBpcyBpbiBzdG9j"
    "ayIpOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09ImFsZXJ0cyIpewogICAgICBj"
    "b25zdCBtaW5lPSh3aW5kb3cuX19hbGVydHMpfHxbXTsKICAgICAgY29uc3Qgb2ZmPVNUT0NLLmZp"
    "bHRlcih4PT4heC5xdHkpLm1hcCh4PT54LmMpOwogICAgICBjb25zdCBsaXN0PShvZmYubGVuZ3Ro"
    "P29mZjpTVE9DSy5tYXAoeD0+eC5jKSkuc2xpY2UoMCw4KTsKICAgICAgc2hlZXQoYDxoMyBzdHls"
    "ZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPlJlc3RvY2sgYWxlcnRzPC9oMz4KICAg"
    "ICAgICA8cCBjbGFzcz0ic3ViIj5XZSBtZXNzYWdlIHlvdSB0aGUgbW9tZW50IG9uZSBvZiB0aGVz"
    "ZSBpcyBiYWNrLjwvcD4KICAgICAgICAke2xpc3QubWFwKGM9PmA8ZGl2IGNsYXNzPSJrdiI+PHNw"
    "YW4+JHtlc2MoYyl9PC9zcGFuPgogICAgICAgICAgPGI+PGJ1dHRvbiBjbGFzcz0iYnRuIHhzICR7"
    "bWluZS5pbmRleE9mKGMpPj0wPyIiOiJzb2Z0In0iIGRhdGEtYWxlcnQ9IiR7ZXNjKGMpfSI+CiAg"
    "ICAgICAgICAgICR7bWluZS5pbmRleE9mKGMpPj0wPyJPbiI6Ik5vdGlmeSBtZSJ9PC9idXR0b24+"
    "PC9iPjwvZGl2PmApLmpvaW4oIiIpfQogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9zZT5Eb25lPC9idXR0b24+YCk7CiAgICAg"
    "IGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWFsZXJ0XSIpLmZvckVhY2goYT0+YS5v"
    "bmNsaWNrPWFzeW5jKCk9PnsKICAgICAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIp"
    "O3JldHVybjt9CiAgICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWxlcnQiLHtjb3Vu"
    "dHJ5OmEuZGF0YXNldC5hbGVydH0pOwogICAgICAgIGlmKCFyKXJldHVybjsKICAgICAgICB3aW5k"
    "b3cuX19hbGVydHM9ci5hbGVydHN8fFtdOwogICAgICAgIGEudGV4dENvbnRlbnQ9ci5vbj8iT24i"
    "OiJOb3RpZnkgbWUiOwogICAgICAgIGEuY2xhc3NMaXN0LnRvZ2dsZSgic29mdCIsIXIub24pOwog"
    "ICAgICAgIHRvYXN0KHIub24/KCJXZSB3aWxsIHRlbGwgeW91IHdoZW4gIithLmRhdGFzZXQuYWxl"
    "cnQrIiBpcyBiYWNrIik6IkFsZXJ0IHJlbW92ZWQiKTsKICAgICAgfSk7CiAgICAgIHJldHVybjsK"
    "ICAgIH0KICAgIGlmKHdoYXQ9PT0iY3N2Iil7CiAgICAgIGNvbnN0IHJvd3M9W1sib3JkZXIiLCJj"
    "b3VudHJ5IiwicHJpY2UiLCJzdGF0dXMiLCJ3aGVuIl1dCiAgICAgICAgLmNvbmNhdChPUkRFUlMu"
    "bWFwKG89PltvLmlkLG8uYyxvLnByaWNlLG8uc3Qsby53aGVuXSkpOwogICAgICBjb25zdCBjc3Y9"
    "cm93cy5tYXAocj0+ci5qb2luKCIsIikpLmpvaW4oIlxuIik7CiAgICAgIGNvbnN0IGE9ZG9jdW1l"
    "bnQuY3JlYXRlRWxlbWVudCgiYSIpOwogICAgICBhLmhyZWY9ImRhdGE6dGV4dC9jc3Y7Y2hhcnNl"
    "dD11dGYtOCwiK2VuY29kZVVSSUNvbXBvbmVudChjc3YpOwogICAgICBhLmRvd25sb2FkPSJteS1v"
    "cmRlcnMuY3N2Ijtkb2N1bWVudC5ib2R5LmFwcGVuZENoaWxkKGEpO2EuY2xpY2soKTthLnJlbW92"
    "ZSgpOwogICAgICB0b2FzdChPUkRFUlMubGVuZ3RoKyIgb3JkZXJzIGRvd25sb2FkZWQiKTsKICAg"
    "ICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJzdXBwb3J0Iil7CiAgICAgIGNvbnN0IGg9"
    "KFNUT1JFLnN1cHBvcnR8fCIiKS5yZXBsYWNlKC9eQC8sIiIpOwogICAgICBpZihoKW9wZW5QYXko"
    "Imh0dHBzOi8vdC5tZS8iK2gpOwogICAgICBlbHNlIHRvYXN0KCJTZW5kIHlvdXIgcXVlc3Rpb24g"
    "aW4gdGhlIGNoYXQgYW5kIHdlIHdpbGwgcmVwbHkiKTsKICAgICAgcmV0dXJuOwogICAgfQogICAg"
    "aWYod2hhdD09PSJ0YXNrIil7CiAgICAgIGNvbnN0IHJvdz1iLmNsb3Nlc3QoIi5jYXJkIik7CiAg"
    "ICAgIGNvbnN0IGxhYmVsPXJvdz8ocm93LnF1ZXJ5U2VsZWN0b3IoImIiKXx8e30pLnRleHRDb250"
    "ZW50fHwiIjoiIjsKICAgICAgaWYoL2NoYW5uZWwvaS50ZXN0KGxhYmVsKSl7Yi5ibHVyKCk7ZG9j"
    "dW1lbnQucXVlcnlTZWxlY3RvcignW2RhdGEtYWN0PSJjaGFubmVsIl0nKT8uY2xpY2soKTtyZXR1"
    "cm47fQogICAgICBpZigvb3JkZXIvaS50ZXN0KGxhYmVsKSl7Z29UYWIoInNob3AiKTt0b2FzdCgi"
    "UGljayBhIGNvdW50cnkgdG8gcGxhY2UgeW91ciBmaXJzdCBvcmRlciIpO3JldHVybjt9CiAgICAg"
    "IGdvVGFiKCJlYXJuIik7dG9hc3QoIlNoYXJlIHlvdXIgaW52aXRlIGxpbmsgdG8gZmluaXNoIHRo"
    "aXMiKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJzdGF0dXMiKXsKICAgICAg"
    "Y29uc3QgZz0oQURNLnBheSYmQURNLnBheS5nbWFpbF9zZXQpOwogICAgICBjb25zdCBwZW5kPShB"
    "RE0ucGF5JiZBRE0ucGF5LnBlbmRpbmcpfHwwOwogICAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJn"
    "aW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+UGF5bWVudCBjaGVja2luZzwvaDM+CiAgICAgICAg"
    "PHAgY2xhc3M9InN1YiI+JHtnPyJBdXRvbWF0aWMgY2hlY2tpbmcgaXMgb24uIFBheSwgdGhlbiB0"
    "YXAgVmVyaWZ5LiI6CiAgICAgICAgICAiQXV0b21hdGljIGNoZWNraW5nIGlzIG5vdCBzZXQgdXAs"
    "IHNvIGFuIGFkbWluIGNvbmZpcm1zIHBheW1lbnRzIGJ5IGhhbmQuIn08L3A+CiAgICAgICAgJHtw"
    "ZW5kP2A8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+V2FpdGluZyBmb3IgYW4gYWRtaW48L3NwYW4+PGI+"
    "JHtwZW5kfTwvYj48L2Rpdj5gOiIifQogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9zZT5DbG9zZTwvYnV0dG9uPmApOwogICAg"
    "ICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09ImNvcHljb2RlIil7CiAgICAgIGNvbnN0IGVs"
    "PWIuY2xvc2VzdCgiLmNhcmQsLnNoZWV0Ym9keSwjc2hlZXQiKXx8ZG9jdW1lbnQ7CiAgICAgIGNv"
    "bnN0IGNvZGU9KGVsLnF1ZXJ5U2VsZWN0b3IoImNvZGUiKXx8e30pLnRleHRDb250ZW50fHwiIjsK"
    "ICAgICAgY29weVRleHQoY29kZS50cmltKCksIkNvZGUiKTsKICAgICAgcmV0dXJuOwogICAgfQog"
    "ICAgaWYod2hhdD09PSJyZXBsYWNlIil7CiAgICAgIGNvbnN0IGNhcmQ9Yi5jbG9zZXN0KCJbZGF0"
    "YS1vcmRlci1pZF0iKTsKICAgICAgY29uc3Qgb2lkPShjYXJkJiZjYXJkLmRhdGFzZXQub3JkZXJJ"
    "ZCl8fChPUkRFUlNbMF0mJk9SREVSU1swXS5pZCl8fCIiOwogICAgICBpZighTElWRSl7Y2xvc2VT"
    "aGVldCgpO3RvYXN0KCJSZXBsYWNlbWVudCBzZW50Iik7cmV0dXJuO30KICAgICAgY29uc3Qgcj1h"
    "d2FpdCBhcGlUcnkoIi9hcGkvb3JkZXIiLHtpZDpvaWQsd2hhdDoicmVwbGFjZSJ9KTsKICAgICAg"
    "aWYoIXIpcmV0dXJuOwogICAgICBjbG9zZVNoZWV0KCk7CiAgICAgIHRvYXN0KHIucmVmdW5kZWQ/"
    "Ik5vIHN0b2NrIGxlZnQg4oCUIHJlZnVuZGVkIHRvIHlvdXIgd2FsbGV0IjooIlJlcGxhY2VkIOKA"
    "lCAiK3Iub3JkZXIpKTsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgICByZXR1cm47CiAgICB9"
    "CiAgICBpZih3aGF0PT09ImNyeXB0byIpewogICAgICBpZighTElWRSl7Y2xvc2VTaGVldCgpO3Rv"
    "YXN0KCJXZSB3aWxsIGNvbmZpcm0geW91ciB0cmFuc2ZlciBzaG9ydGx5Iik7cmV0dXJuO30KICAg"
    "ICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvY3J5cHRvIix7cmVmOiIifSk7CiAgICAgIGlm"
    "KHIpe2Nsb3NlU2hlZXQoKTt0b2FzdCgiV2Ugd2lsbCBjb25maXJtIHlvdXIgdHJhbnNmZXIgc2hv"
    "cnRseSIpO30KICAgICAgcmV0dXJuOwogICAgfQogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIltkYXRhLWNvcHldIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0"
    "IHdoYXQ9Yi5kYXRhc2V0LmNvcHk7CiAgICBpZih3aGF0PT09InVwaSIpcmV0dXJuIGNvcHlUZXh0"
    "KFNUT1JFLnVwaSwiVVBJIElEIik7CiAgICBpZih3aGF0PT09InJlZiIpewogICAgICBsZXQgbD1F"
    "QVJOLmxpbmt8fCIiOwogICAgICBpZihsJiYhL15odHRwcz86Ly50ZXN0KGwpKWw9Imh0dHBzOi8v"
    "IitsOwogICAgICByZXR1cm4gY29weVRleHQobCwiSW52aXRlIGxpbmsiKTsKICAgIH0KICAgIHJl"
    "dHVybiBjb3B5VGV4dChiLmRhdGFzZXQuY29weVZhbHVlfHxiLnRleHRDb250ZW50LnRyaW0oKSwi"
    "Q29waWVkIik7CiAgfSk7CiAgY29uc3QgcmQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInJlZGVl"
    "bWdvIik7CiAgaWYocmQpcmQub25jbGljaz1hc3luYygpPT57CiAgICBpZighTElWRSl7dG9hc3Qo"
    "IlJlZGVlbWVkIik7cmV0dXJuO30KICAgIHJkLmRpc2FibGVkPXRydWU7CiAgICBjb25zdCByPWF3"
    "YWl0IGFwaVRyeSgiL2FwaS9yZWRlZW0iLHt9KTsKICAgIHJkLmRpc2FibGVkPWZhbHNlOwogICAg"
    "aWYoIXIpcmV0dXJuOwogICAgQkFMPXIuYmFsYW5jZTtzeW5jQmFsKCk7YnVyc3QoKTsKICAgIHRv"
    "YXN0KHIucG9pbnRzKyIgcG9pbnRzIOKGkiAiK21vbmV5KHIuYW1vdW50KSk7CiAgICBhd2FpdCBy"
    "ZWZyZXNoKCk7CiAgfTsKICBjb25zdCBvdT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgib2ZmZXJ1"
    "c2UiKTsKICBpZihvdSlvdS5vbmNsaWNrPSgpPT57CiAgICBjb25zdCBjPShBRE0uY291cG9ucyYm"
    "QURNLmNvdXBvbnMubGVuZ3RoKT9BRE0uY291cG9uc1swXS5jb2RlOiIiOwogICAgaWYoYyl7T0ZG"
    "RVI9Yztjb3B5VGV4dChjLCJDb3Vwb24iKTt9CiAgICBlbHNlIHRvYXN0KCJObyBjb3Vwb24gaXMg"
    "cnVubmluZyByaWdodCBub3ciKTsKICB9OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltk"
    "YXRhLXByZWZdIikuZm9yRWFjaCh0PT50Lm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3Qga2V5"
    "PXQuZGF0YXNldC5wcmVmLCBvbj0hdC5jbGFzc0xpc3QuY29udGFpbnMoIm9uIik7CiAgICB0LmNs"
    "YXNzTGlzdC50b2dnbGUoIm9uIixvbik7UFJFRlNba2V5XT1vbjsKICAgIGlmKExJVkUpewogICAg"
    "ICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9wcmVmcyIse2tleTprZXksb246b259KTsKICAg"
    "ICAgaWYoIXIpe3QuY2xhc3NMaXN0LnRvZ2dsZSgib24iLCFvbik7UFJFRlNba2V5XT0hb247cmV0"
    "dXJuO30KICAgIH0KICAgIHRvYXN0KG9uPyJUdXJuZWQgb24iOiJUdXJuZWQgb2ZmIik7CiAgfSk7"
    "CiAgY29uc3QgdGdiPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJ0ZXJtc2dvIik7CiAgaWYodGdi"
    "KXRnYi5vbmNsaWNrPSgpPT5zaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNp"
    "emU6MXJlbSI+VGVybXMgJiByZWZ1bmRzPC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJ3"
    "aGl0ZS1zcGFjZTpwcmUtd3JhcCI+JHtlc2MoU1RPUkUudGVybXN8fENNUy50ZXJtc3x8Ik5vIHRl"
    "cm1zIGhhdmUgYmVlbiB3cml0dGVuIHlldC4iKX08L3A+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "YmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5g"
    "KTsKICBjb25zdCBzZz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3VwcG9ydGdvIik7CiAgaWYo"
    "c2cpc2cub25jbGljaz0oKT0+ewogICAgY29uc3QgaD0oU1RPUkUuc3VwcG9ydHx8IiIpLnJlcGxh"
    "Y2UoL15ALywiIik7CiAgICBpZihoKW9wZW5QYXkoImh0dHBzOi8vdC5tZS8iK2gpOwogICAgZWxz"
    "ZSBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+U3VwcG9y"
    "dDwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiPlNlbmQgeW91ciBxdWVzdGlvbiBpbiB0aGUgY2hh"
    "dCBhbmQgd2Ugd2lsbCByZXBseS48L3A+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9zZT5DbG9zZTwvYnV0dG9uPmApOwogIH07"
    "Cn0KZnVuY3Rpb24gYmluZFN0YWdlKCl7CiAgYmluZENvbW1vbigpOwogIGJpbmRCdXkoKTsKICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1zY3JvbGxdIikuZm9yRWFjaChiPT5iLm9u"
    "Y2xpY2s9KCk9PmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGIuZGF0YXNldC5zY3JvbGwpLnNjcm9s"
    "bEludG9WaWV3KHtiZWhhdmlvcjoic21vb3RoIn0pKTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "QWxsKCJbZGF0YS1vdHBdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29u"
    "c3QgY2FyZD1iLmNsb3Nlc3QoIltkYXRhLW9yZGVyLWlkXSIpOwogICAgY29uc3Qgb2lkPShjYXJk"
    "JiZjYXJkLmRhdGFzZXQub3JkZXJJZCl8fGIuZGF0YXNldC5vdHB8fCIiOwogICAgY29uc3Qgb3Jk"
    "PU9SREVSUy5maW5kKHg9PnguaWQ9PT1vaWQpfHx7fTsKICAgIGlmKCFMSVZFKXsKICAgICAgc2hl"
    "ZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkxvZ2luIGNvZGU8"
    "L2gzPgogICAgICAgIDxwIGNsYXNzPSJzdWIiPlNhbXBsZSBjb2RlIOKAlCB0aGlzIGlzIHRoZSBw"
    "cmV2aWV3LjwvcD4KICAgICAgICA8ZGl2IGNsYXNzPSJvdHBib3giPjQgMiA5IDEgNzwvZGl2Pgog"
    "ICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCIg"
    "ZGF0YS1jbG9zZT5Eb25lPC9idXR0b24+YCk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGNvbnN0"
    "IGxibD1iLnRleHRDb250ZW50O2IuZGlzYWJsZWQ9dHJ1ZTtiLnRleHRDb250ZW50PSJGZXRjaGlu"
    "Z+KApiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRlciIse2lkOm9pZCx3aGF0"
    "OiJvdHAifSk7CiAgICBiLmRpc2FibGVkPWZhbHNlO2IudGV4dENvbnRlbnQ9bGJsOwogICAgaWYo"
    "IXIpcmV0dXJuOwogICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXpl"
    "OjFyZW0iPkxvZ2luIGNvZGU8L2gzPgogICAgICA8cCBjbGFzcz0ic3ViIj4ke29yZC5waG9uZT8o"
    "IkZvciAiK2VzYyhvcmQucGhvbmUpKToiRm9yIHRoaXMgYWNjb3VudCJ9PC9wPgogICAgICA8ZGl2"
    "IGNsYXNzPSJvdHBib3giPiR7U3RyaW5nKHIuY29kZSkuc3BsaXQoIiIpLmpvaW4oIiAiKX08L2Rp"
    "dj4KICAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkNvZGVzIGxlZnQ8L3NwYW4+PGI+JHtyLmxl"
    "ZnR9PC9iPjwvZGl2PgogICAgICAke3Iud2luZG93P2A8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+Rm9y"
    "d2FyZGluZyBlbmRzIGluPC9zcGFuPjxiPiR7ci53aW5kb3d9IG1pbjwvYj48L2Rpdj5gOiIifQog"
    "ICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPgogICAgICAg"
    "IDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLWFjdD0iY29weWNvZGUiPkNvcHkgQ29kZTwv"
    "YnV0dG9uPgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biIgZGF0YS1jbG9zZT5Eb25lPC9idXR0"
    "b24+PC9kaXY+YCk7CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2xh"
    "aW1dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYoYi5kaXNhYmxlZCly"
    "ZXR1cm47CiAgICBiLmRpc2FibGVkPXRydWU7CiAgICBpZihMSVZFKXsKICAgICAgY29uc3Qgcj1h"
    "d2FpdCBhcGlUcnkoIi9hcGkvY2hlY2tpbiIse30pOwogICAgICBpZighcil7Yi5kaXNhYmxlZD1m"
    "YWxzZTtyZXR1cm47fQogICAgICBCQUw9dHlwZW9mIHIuYmFsYW5jZT09PSJudW1iZXIiP3IuYmFs"
    "YW5jZTpCQUw7CiAgICAgIEVBUk4uY2xhaW1lZD10cnVlO0VBUk4uc3RyZWFrPXIuc3RyZWFrfHxF"
    "QVJOLnN0cmVhazsKICAgICAgc3luY0JhbCgpO2J1cnN0KCk7dG9hc3QobW9uZXkoci5hbW91bnQp"
    "KyIgYWRkZWQgwrcgZGF5ICIrci5zdHJlYWspOwogICAgICBiLnRleHRDb250ZW50PSJDbGFpbWVk"
    "IHRvZGF5IjsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgICByZXR1cm47CiAgICB9CiAgICBC"
    "QUwrPUVBUk4uZGFpbHk7c3luY0JhbCgpO2J1cnN0KCk7dG9hc3QobW9uZXkoRUFSTi5kYWlseSkr"
    "IiBhZGRlZCIpOwogICAgYi50ZXh0Q29udGVudD0iQ2xhaW1lZCB0b2RheSI7fSk7CiAgZG9jdW1l"
    "bnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtYW10XSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgp"
    "PT5kZXBvc2l0U2hlZXQoYi5kYXRhc2V0LmFtdCkpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JB"
    "bGwoIltkYXRhLWRlcF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+YW1vdW50U2hlZXQoKSk7"
    "CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtdGddIikuZm9yRWFjaCh0PT50Lm9u"
    "Y2xpY2s9KCk9PnQuY2xhc3NMaXN0LnRvZ2dsZSgib24iKSk7CiAgZG9jdW1lbnQucXVlcnlTZWxl"
    "Y3RvckFsbCgiW2RhdGEtc2V0c2VsXSIpLmZvckVhY2goc2VsPT5zZWwub25jaGFuZ2U9YXN5bmMo"
    "KT0+ewogICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgY29u"
    "c3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc2V0dGluZyIse2tleTpzZWwuZGF0YXNldC5z"
    "ZXRzZWwsdmFsdWU6c2VsLnZhbHVlfSk7CiAgICBpZihyKXRvYXN0KCJTYXZlZCIpOwogIH0pOwog"
    "IGNvbnN0IHE9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInEiKTsKICBpZihxKXtxLnZhbHVlPVFV"
    "RVJZO3Eub25pbnB1dD0oKT0+e1FVRVJZPXEudmFsdWUudG9Mb3dlckNhc2UoKTthcHBseUZpbHRl"
    "cigpfTt9CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI2NoaXBzIGJ1dHRvbiIpLmZvckVh"
    "Y2goYj0+Yi5vbmNsaWNrPSgpPT57Q0hJUD1iLmRhdGFzZXQuZmlsdGVyOwogICAgZG9jdW1lbnQu"
    "cXVlcnlTZWxlY3RvckFsbCgiI2NoaXBzIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3Qu"
    "dG9nZ2xlKCJvbiIseD09PWIpKTthcHBseUZpbHRlcigpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxl"
    "Y3RvckFsbCgiW2RhdGEtY2xhaW0tb3JkZXJdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsK"
    "ICAgIGNvbnN0IG9pZD1iLmRhdGFzZXQuY2xhaW1PcmRlcjsKICAgIGNvbnN0IG9yZD1PUkRFUlMu"
    "ZmluZCh4PT54LmlkPT09b2lkKXx8e307CiAgICBpZihvcmQuY2xhaW0pe3RvYXN0KCJBIGNsYWlt"
    "IGZvciB0aGlzIG9yZGVyIGlzIGFscmVhZHkgd2l0aCBhbiBhZG1pbiIpO3JldHVybjt9CiAgICBz"
    "aGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+UmVwb3J0IGEg"
    "cHJvYmxlbTwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiPkFuIGFkbWluIHJlYWRzIGV2ZXJ5IHJl"
    "cG9ydCBhbmQgZGVjaWRlcy4gTm90aGluZyBpcyByZWZ1bmRlZCBhdXRvbWF0aWNhbGx5LjwvcD4K"
    "ICAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5X"
    "aGF0IGhhcHBlbmVkPzwvbGFiZWw+CiAgICAgICAgPHNlbGVjdCBjbGFzcz0iaW5wIiBpZD0iY2xy"
    "Ij4KICAgICAgICAgIDxvcHRpb24+TG9naW4gbm90IHdvcmtpbmc8L29wdGlvbj48b3B0aW9uPkFj"
    "Y291bnQgYmFubmVkPC9vcHRpb24+CiAgICAgICAgICA8b3B0aW9uPkNvZGUgbmV2ZXIgYXJyaXZl"
    "ZDwvb3B0aW9uPjxvcHRpb24+V3JvbmcgY291bnRyeTwvb3B0aW9uPgogICAgICAgICAgPG9wdGlv"
    "bj5Tb21ldGhpbmcgZWxzZTwvb3B0aW9uPjwvc2VsZWN0PjwvZGl2PgogICAgICA8ZGl2IGNsYXNz"
    "PSJmaWVsZCI+PGxhYmVsPkRldGFpbHM8L2xhYmVsPgogICAgICAgIDx0ZXh0YXJlYSBjbGFzcz0i"
    "aW5wIiBpZD0iY2xkIiByb3dzPSIzIiBwbGFjZWhvbGRlcj0iVGVsbCB1cyBleGFjdGx5IHdoYXQg"
    "eW91IHNhdyI+PC90ZXh0YXJlYT48L2Rpdj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBi"
    "bG9jayIgaWQ9ImNsZ28iPlNlbmQgdG8gYW4gYWRtaW48L2J1dHRvbj4KICAgICAgPGJ1dHRvbiBj"
    "bGFzcz0iYnRuIGdob3N0IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xv"
    "c2U+Q2FuY2VsPC9idXR0b24+YCk7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2xnbyIp"
    "Lm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25zdCByZWFzb249ZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoImNsciIpLnZhbHVlOwogICAgICBjb25zdCBkZXRhaWxzPShkb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiY2xkIikudmFsdWV8fCIiKS50cmltKCk7CiAgICAgIGlmKGRldGFpbHMubGVuZ3Ro"
    "PDUpe3RvYXN0KCJUZWxsIHVzIHdoYXQgd2VudCB3cm9uZyIpO3JldHVybjt9CiAgICAgIGlmKCFM"
    "SVZFKXtjbG9zZVNoZWV0KCk7dG9hc3QoIlNlbnQiKTtyZXR1cm47fQogICAgICBjb25zdCBidG49"
    "ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNsZ28iKTtidG4uZGlzYWJsZWQ9dHJ1ZTtidG4udGV4"
    "dENvbnRlbnQ9IlNlbmRpbmfigKYiOwogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9v"
    "cmRlciIse2lkOm9pZCx3aGF0OiJjbGFpbSIscmVhc29uOnJlYXNvbixkZXRhaWxzOmRldGFpbHN9"
    "KTsKICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0iU2VuZCB0byBhbiBh"
    "ZG1pbiI7CiAgICAgIGlmKCFyKXJldHVybjsKICAgICAgY2xvc2VTaGVldCgpO3RvYXN0KCJDbGFp"
    "bSAjIityLmlkKyIgc2VudCDigJQgYW4gYWRtaW4gd2lsbCByZXBseSIpOwogICAgICBhd2FpdCBy"
    "ZWZyZXNoKCk7CiAgICB9OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRh"
    "LWxvZ2dlZGluXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IG9p"
    "ZD1iLmRhdGFzZXQubG9nZ2VkaW47CiAgICBpZighTElWRSl7dG9hc3QoIk1hcmtlZCBhcyBsb2dn"
    "ZWQgaW4iKTtyZXR1cm47fQogICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9u"
    "dC1zaXplOjFyZW0iPkNvbmZpcm0geW91IGFyZSBpbjwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIi"
    "PldlIHdpbGwgZGVsZXRlIG91ciBjb3B5IG9mIHRoZSBsb2dpbiBzZXNzaW9uIHN0cmFpZ2h0IGF3"
    "YXksIHNvIHRoZQogICAgICBhY2NvdW50IGJlY29tZXMgeW91cnMgYWxvbmUuIENvZGVzIHdpbGwg"
    "c3RvcCBiZWluZyBmb3J3YXJkZWQuPC9wPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJs"
    "b2NrIiBpZD0ibGlvayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+WWVzLCBJIGFtIGxvZ2dlZCBp"
    "bjwvYnV0dG9uPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QgYmxvY2sgc20iIHN0eWxl"
    "PSJtYXJnaW4tdG9wOjhweCIgZGF0YS1jbG9zZT5Ob3QgeWV0PC9idXR0b24+YCk7CiAgICBkb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgibGlvayIpLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25z"
    "dCByPWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRlciIse2lkOm9pZCx3aGF0OiJsb2dnZWRpbiJ9KTsK"
    "ICAgICAgaWYoIXIpcmV0dXJuOwogICAgICBjbG9zZVNoZWV0KCk7dG9hc3QoIkRvbmUg4oCUIHRo"
    "ZSBhY2NvdW50IGlzIHlvdXJzIGFsb25lIG5vdyIpOwogICAgICBhd2FpdCByZWZyZXNoKCk7CiAg"
    "ICB9OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWRpc3B1dGVdIiku"
    "Zm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IG9yZD1iLmRhdGFzZXQuZGlzcHV0"
    "ZXx8IiI7CiAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJl"
    "bSI+T3BlbiBhIGRpc3B1dGU8L2gzPgogICAgIDxwIGNsYXNzPSJzdWIiPiR7b3JkPygiT3JkZXIg"
    "Iitlc2Mob3JkKSk6IkFuIGFkbWluIHJldmlld3MgZXZlcnkgZGlzcHV0ZS4ifTwvcD4KICAgICA8"
    "ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlJlYXNvbjwv"
    "bGFiZWw+CiAgICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIGlkPSJkc3IiPjxvcHRpb24+Tm90IGRl"
    "bGl2ZXJlZDwvb3B0aW9uPjxvcHRpb24+QWNjb3VudCBkZWFkIG9uIGFycml2YWw8L29wdGlvbj48"
    "b3B0aW9uPkNoYXJnZWQgdHdpY2U8L29wdGlvbj48b3B0aW9uPk90aGVyPC9vcHRpb24+PC9zZWxl"
    "Y3Q+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5XaGF0IGhhcHBlbmVkPzwv"
    "bGFiZWw+CiAgICAgICA8dGV4dGFyZWEgY2xhc3M9ImlucCIgaWQ9ImRzZCIgcm93cz0iMyIgcGxh"
    "Y2Vob2xkZXI9IkRlc2NyaWJlIHRoZSBwcm9ibGVtIj48L3RleHRhcmVhPjwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RXZpZGVuY2UgKG9wdGlvbmFsKTwvbGFiZWw+CiAgICAg"
    "ICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImRzZiIgdHlwZT0iZmlsZSIgYWNjZXB0PSJpbWFnZS8q"
    "Ij4KICAgICAgIDxzbWFsbCBjbGFzcz0ic3ViIiBpZD0iZHNmbiI+PC9zbWFsbD48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJidG5yb3ciPjxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCIgZGF0YS1jbG9z"
    "ZT5DYW5jZWw8L2J1dHRvbj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJkc2dv"
    "Ij5TdWJtaXQ8L2J1dHRvbj48L2Rpdj5gKTsKICAgIGNvbnN0IGY9ZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoImRzZiIpOwogICAgaWYoZilmLm9uY2hhbmdlPSgpPT57Y29uc3Qgbj0oZi5maWxlcyYm"
    "Zi5maWxlc1swXSk/Zi5maWxlc1swXS5uYW1lOiIiOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgiZHNmbiIpLnRleHRDb250ZW50PW4/KCJBdHRhY2hlZDogIituKToiIjt9OwogICAgZG9j"
    "dW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRzZ28iKS5vbmNsaWNrPWFzeW5jKCk9PnsKICAgICAgY29u"
    "c3QgcmVhc29uPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJkc3IiKS52YWx1ZTsKICAgICAgY29u"
    "c3QgZGV0YWlscz0oZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRzZCIpLnZhbHVlfHwiIikudHJp"
    "bSgpOwogICAgICBpZighZGV0YWlscyl7dG9hc3QoIkRlc2NyaWJlIHdoYXQgaGFwcGVuZWQiKTty"
    "ZXR1cm47fQogICAgICBjb25zdCBmaWxlPShmJiZmLmZpbGVzJiZmLmZpbGVzWzBdKXx8bnVsbDsK"
    "ICAgICAgY29uc3QgZm49ZmlsZT8oInNjcmVlbnNob3Q6ICIrZmlsZS5uYW1lKToiIjsKICAgICAg"
    "aWYoIUxJVkUpe2Nsb3NlU2hlZXQoKTt0b2FzdCgiRGlzcHV0ZSBvcGVuZWQiKTtyZXR1cm47fQog"
    "ICAgICBjb25zdCBidG49ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRzZ28iKTtidG4uZGlzYWJs"
    "ZWQ9dHJ1ZTtidG4udGV4dENvbnRlbnQ9IlNlbmRpbmfigKYiOwogICAgICBsZXQgc2hvdD0iIjsK"
    "ICAgICAgaWYoZmlsZSl7CiAgICAgICAgaWYoZmlsZS5zaXplPjgqMTAyNCoxMDI0KXt0b2FzdCgi"
    "VGhhdCBpbWFnZSBpcyB0b28gbGFyZ2UgKDggTUIgbWF4KSIpOwogICAgICAgICAgYnRuLmRpc2Fi"
    "bGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0iU3VibWl0IjtyZXR1cm47fQogICAgICAgIHNob3Q9"
    "YXdhaXQgbmV3IFByb21pc2UocmVzPT57CiAgICAgICAgICBjb25zdCByZD1uZXcgRmlsZVJlYWRl"
    "cigpOwogICAgICAgICAgcmQub25sb2FkPSgpPT5yZXMoU3RyaW5nKHJkLnJlc3VsdHx8IiIpKTsK"
    "ICAgICAgICAgIHJkLm9uZXJyb3I9KCk9PnJlcygiIik7CiAgICAgICAgICByZC5yZWFkQXNEYXRh"
    "VVJMKGZpbGUpOwogICAgICAgIH0pOwogICAgICB9CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5"
    "KCIvYXBpL2Rpc3B1dGUiLHtvcmRlcjpvcmQscmVhc29uOnJlYXNvbixkZXRhaWxzOmRldGFpbHMs"
    "CiAgICAgICAgZXZpZGVuY2U6Zm4sc2hvdDpzaG90fSk7CiAgICAgIGJ0bi5kaXNhYmxlZD1mYWxz"
    "ZTtidG4udGV4dENvbnRlbnQ9IlN1Ym1pdCI7CiAgICAgIGlmKCFyKXJldHVybjsKICAgICAgY2xv"
    "c2VTaGVldCgpO3RvYXN0KCJEaXNwdXRlICMiK3IuaWQrIiBzZW50IHRvIHRoZSBhZG1pbnMiKTsK"
    "ICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCJbZGF0YS1yYXRlXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57d2luZG93Ll9f"
    "cmF0ZUNvdW50cnk9Yi5kYXRhc2V0LnJhdGU7cmV0dXJuIHNoZWV0KAogICAgYDxoMyBzdHlsZT0i"
    "bWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPlJhdGUgJHtiLmRhdGFzZXQucmF0ZX08L2gz"
    "PgogICAgIDxwIGNsYXNzPSJzdWIiPllvdXIgcmF0aW5nIHNob3dzIG9uIHRoZSBjb3VudHJ5IGlu"
    "IHRoZSBzaG9wLjwvcD4KICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjlweDtqdXN0"
    "aWZ5LWNvbnRlbnQ6Y2VudGVyO21hcmdpbjoxNnB4IDAiPgogICAgICAgJHtbMSwyLDMsNCw1XS5t"
    "YXAobj0+YDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBkYXRhLXJhdGUtZ289IiR7bn0iPiR7"
    "bn3imIU8L2J1dHRvbj5gKS5qb2luKCIiKX08L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "Z2hvc3QgYmxvY2sgc20iIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTt9KTsgCiAgZG9jdW1l"
    "bnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY3J5cHRvXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNr"
    "PSgpPT5zaGVldCgKICAgIGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVt"
    "Ij5DcnlwdG8gdG9wLXVwPC9oMz4KICAgICA8cCBjbGFzcz0ic3ViIj5TZW5kIFVTRFQtVFJDMjAg"
    "4oCUIGNyZWRpdGVkIGFmdGVyIDEgbmV0d29yayBjb25maXJtYXRpb24uPC9wPgogICAgIDxkaXYg"
    "Y2xhc3M9InFyIj5RUiBDT0RFPGJyPih3YWxsZXQgYWRkcmVzcyk8L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJrdiI+PHNwYW4+TmV0d29yazwvc3Bhbj48Yj5UUkMtMjA8L2I+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPkFkZHJlc3M8L3NwYW4+PGIgc3R5bGU9ImZvbnQtc2l6ZTouN3Jl"
    "bSI+bm90LWNvbmZpZ3VyZWQ8L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlJh"
    "dGU8L3NwYW4+PGI+MSBVU0RUID0g4oK5ODguNDA8L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "YnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3Qi"
    "IGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEi"
    "IGRhdGEtYWN0PSJjcnlwdG8iPkkndmUgU2VudCBJdDwvYnV0dG9uPjwvZGl2PmApKTsKICBjb25z"
    "dCB0Zz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgidG91cmdvIiksdHM9ZG9jdW1lbnQuZ2V0RWxl"
    "bWVudEJ5SWQoInRvdXJza2lwIik7CiAgaWYodGcpdGcub25jbGljaz0oKT0+c2hlZXQoYDxoMyBz"
    "dHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkhvdyBpdCB3b3JrczwvaDM+CiAg"
    "ICAgPGRpdiBjbGFzcz0ic3RlcHMiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPgogICAgICAgPGRp"
    "diBjbGFzcz0ic3RlcCBvbiI+PGk+MTwvaT5Ub3AgdXAgeW91ciB3YWxsZXQgYnkgVVBJPC9kaXY+"
    "CiAgICAgICA8ZGl2IGNsYXNzPSJzdGVwIG9uIj48aT4yPC9pPlBpY2sgYSBjb3VudHJ5IGFuZCBi"
    "dXkgaW5zdGFudGx5PC9kaXY+CiAgICAgICA8ZGl2IGNsYXNzPSJzdGVwIG9uIj48aT4zPC9pPlJl"
    "YWQgdGhlIGxvZ2luIGNvZGUgcmlnaHQgaGVyZSBpbiBPcmRlcnM8L2Rpdj48L2Rpdj4KICAgICA8"
    "YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4IiBkYXRh"
    "LWNsb3NlPkdvdCBpdDwvYnV0dG9uPmApOwogIGlmKHRzKXRzLm9uY2xpY2s9KCk9PntGRUFULm9u"
    "Ym9hcmRpbmc9ZmFsc2U7cmVuZGVyKCk7dG9hc3QoIlRvdXIgaGlkZGVuIik7fTsKICBzdGFydENv"
    "dW50ZG93bigpOwogIGJpbmRIb2xkKCk7Cn0KZnVuY3Rpb24gYXBwbHlGaWx0ZXIoKXsKICBGSUxU"
    "RVJFRD1TVE9DSy5maWx0ZXIocz0+ewogICAgY29uc3QgdD0ocy5jKyIgIitzLmNvZGUpLnRvTG93"
    "ZXJDYXNlKCk7CiAgICBpZihRVUVSWSYmIXQuaW5jbHVkZXMoUVVFUlkpKXJldHVybiBmYWxzZTsK"
    "ICAgIGlmKENISVA9PT0ic3RvY2siJiZzLnF0eT09PTApcmV0dXJuIGZhbHNlOwogICAgaWYoQ0hJ"
    "UD09PSJkZWFsIiYmIXMud2FzKXJldHVybiBmYWxzZTsKICAgIGlmKENISVA9PT0iY2hlYXAiJiZz"
    "LnByaWNlPj0zNSlyZXR1cm4gZmFsc2U7CiAgICBpZihDSElQPT09InYyZmEiJiZzLnF0eT09PTAp"
    "cmV0dXJuIGZhbHNlOwogICAgaWYoQ0hJUD09PSJ2bWFpbCImJiFzLmhvdClyZXR1cm4gZmFsc2U7"
    "CiAgICBpZihDSElQPT09InZzcGFtIiYmcy5wcmljZTwzMClyZXR1cm4gZmFsc2U7CiAgICByZXR1"
    "cm4gdHJ1ZTt9KTsKICBjb25zdCBiPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzaG9wYm9keSIp"
    "OwogIGlmKGIpe2IuaW5uZXJIVE1MPXNob3BCb2R5KCk7YmluZEJ1eSgpO30KfQpsZXQgY2RUPW51"
    "bGw7CmZ1bmN0aW9uIHN0YXJ0Q291bnRkb3duKCl7CiAgY29uc3QgZWw9ZG9jdW1lbnQuZ2V0RWxl"
    "bWVudEJ5SWQoImNkIik7Y2xlYXJJbnRlcnZhbChjZFQpO2lmKCFlbClyZXR1cm47CiAgbGV0IHQ9"
    "MTQqNjArNTI7CiAgY2RUPXNldEludGVydmFsKCgpPT57aWYoIWRvY3VtZW50LmJvZHkuY29udGFp"
    "bnMoZWwpKXtjbGVhckludGVydmFsKGNkVCk7cmV0dXJufQogICAgdD10PjA/dC0xOjE0KjYwKzUy"
    "O2VsLnRleHRDb250ZW50PVN0cmluZyhNYXRoLmZsb29yKHQvNjApKS5wYWRTdGFydCgyLCIwIikr"
    "IjoiK1N0cmluZyh0JTYwKS5wYWRTdGFydCgyLCIwIil9LDEwMDApOwp9Ci8qIDA1IMK3IGhvbGQt"
    "dG8tY29uZmlybSAqLwpmdW5jdGlvbiBiaW5kSG9sZCgpewogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIi5ob2xkIikuZm9yRWFjaChiPT57CiAgICBpZihiLl9oKXJldHVybjtiLl9oPTE7Y29u"
    "c3QgZmlsbD1iLnF1ZXJ5U2VsZWN0b3IoIi5maWxsIik7bGV0IGl2PW51bGwscD0wOwogICAgY29u"
    "c3Qgc3RvcD0oKT0+e2NsZWFySW50ZXJ2YWwoaXYpO3A9MDtpZihmaWxsKWZpbGwuc3R5bGUud2lk"
    "dGg9IjAlIn07CiAgICBjb25zdCBnbz1lPT57ZS5wcmV2ZW50RGVmYXVsdCgpO2NsZWFySW50ZXJ2"
    "YWwoaXYpO2l2PXNldEludGVydmFsKCgpPT57cCs9NDtpZihmaWxsKWZpbGwuc3R5bGUud2lkdGg9"
    "cCsiJSI7CiAgICAgIGlmKHA+PTEwMCl7c3RvcCgpO2IuY2xhc3NMaXN0LmFkZCgiZG9uZSIpO3Nl"
    "dFRpbWVvdXQoKCk9PmIuY2xhc3NMaXN0LnJlbW92ZSgiZG9uZSIpLDQ1MCk7YnVyc3QoKTt0b2Fz"
    "dChiLmRhdGFzZXQuZG9uZXx8IkNvbmZpcm1lZCIpfX0sMjgpfTsKICAgIGIuYWRkRXZlbnRMaXN0"
    "ZW5lcigicG9pbnRlcmRvd24iLGdvKTtbInBvaW50ZXJ1cCIsInBvaW50ZXJsZWF2ZSIsInBvaW50"
    "ZXJjYW5jZWwiXS5mb3JFYWNoKGV2PT5iLmFkZEV2ZW50TGlzdGVuZXIoZXYsc3RvcCkpO30pOwp9"
    "Ci8qIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCBLSVQgUlVOVElNRSDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZDilZAgKi8KY29uc3QgUk09bWF0Y2hNZWRpYSgiKHByZWZl"
    "cnMtcmVkdWNlZC1tb3Rpb246cmVkdWNlKSIpLm1hdGNoZXM7Ci8qIHNwcmluZyBpbnRlZ3JhdG9y"
    "IOKAlCBraXQgdXNlcyBtYXNzIC4xIC8gc3RpZmZuZXNzIDE1MCAvIGRhbXBpbmcgMTIgKi8KZnVu"
    "Y3Rpb24gU3ByaW5nKHYse209LjEsaz0xNTAsZD0xMn09e30pe3RoaXMudj12O3RoaXMudD12O3Ro"
    "aXMudmVsPTA7dGhpcy5tPW07dGhpcy5rPWs7dGhpcy5kPWQ7fQpTcHJpbmcucHJvdG90eXBlLnN0"
    "ZXA9ZnVuY3Rpb24oZHQpe2NvbnN0IEY9LXRoaXMuayoodGhpcy52LXRoaXMudCktdGhpcy5kKnRo"
    "aXMudmVsOwogIHRoaXMudmVsKz0oRi90aGlzLm0pKmR0O3RoaXMudis9dGhpcy52ZWwqZHQ7cmV0"
    "dXJuIHRoaXMudjt9OwoKLyogMDIgKyAyNSDCtyBhbmltYXRlZCBkb2NrOiBkaXN0YW5jZeKGknNj"
    "YWxlIHdpdGggc3ByaW5nLCBwbHVzIHNsaWRpbmcgcGlsbCAqLwooZnVuY3Rpb24gZG9jaygpewog"
    "IGNvbnN0IGRrPWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoIm5hdiN0YWJzIC5kb2NrIik7aWYoIWRr"
    "KXJldHVybjsKICBjb25zdCBidG5zPVsuLi5kay5xdWVyeVNlbGVjdG9yQWxsKCJidXR0b24iKV0s"
    "cGlsbD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZHBpbGwiKTsKICBjb25zdCBGSU5FPW1hdGNo"
    "TWVkaWEoIihob3Zlcjpob3ZlcikgYW5kIChwb2ludGVyOmZpbmUpIikubWF0Y2hlcyYmIVJNOwog"
    "IGNvbnN0IHNwPWJ0bnMubWFwKCgpPT5uZXcgU3ByaW5nKDEpKSxwcz17eDpuZXcgU3ByaW5nKDAs"
    "e2s6MjYwLGQ6MTF9KSx3Om5ldyBTcHJpbmcoMCx7azoyNjAsZDoxMX0pfTsKICBsZXQgbXg9SW5m"
    "aW5pdHksbGFzdD1wZXJmb3JtYW5jZS5ub3coKSxwbGFjZWQ9ZmFsc2U7CiAgZnVuY3Rpb24gcmVz"
    "ZXRTY2FsZSgpe214PUluZmluaXR5O2lmKCFGSU5FKWJ0bnMuZm9yRWFjaChiPT5iLnN0eWxlLnRy"
    "YW5zZm9ybT0iIik7fQogIGlmKEZJTkUpewogICAgZGsuYWRkRXZlbnRMaXN0ZW5lcigicG9pbnRl"
    "cm1vdmUiLGU9PntpZihlLnBvaW50ZXJUeXBlPT09Im1vdXNlIilteD1lLmNsaWVudFh9KTsKICAg"
    "IGRrLmFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJsZWF2ZSIsKCk9Pm14PUluZmluaXR5KTsKICAg"
    "IFsicG9pbnRlcnVwIiwicG9pbnRlcmNhbmNlbCIsInRvdWNoZW5kIiwidG91Y2hjYW5jZWwiLCJi"
    "bHVyIl0uZm9yRWFjaChldj0+CiAgICAgIGFkZEV2ZW50TGlzdGVuZXIoZXYscmVzZXRTY2FsZSx7"
    "cGFzc2l2ZTp0cnVlfSkpOwogIH1lbHNle2J0bnMuZm9yRWFjaChiPT5iLnN0eWxlLnRyYW5zZm9y"
    "bT0iIik7fQogIGRvY3VtZW50LmFkZEV2ZW50TGlzdGVuZXIoInZpc2liaWxpdHljaGFuZ2UiLHJl"
    "c2V0U2NhbGUpOwoKICBmdW5jdGlvbiBwbGFjZShzbmFwKXsKICAgIGNvbnN0IG9uPWRrLnF1ZXJ5"
    "U2VsZWN0b3IoImJ1dHRvbi5vbiIpOwogICAgaWYoIW9ufHwhcGlsbHx8IW9uLm9mZnNldFdpZHRo"
    "fHwhZGsub2Zmc2V0V2lkdGgpcmV0dXJuOyAgIC8vIG5ldmVyIHBsYWNlIHdoaWxlIGhpZGRlbgog"
    "ICAgcHMueC50PW9uLm9mZnNldExlZnQtZGsuY2xpZW50TGVmdDtwcy53LnQ9b24ub2Zmc2V0V2lk"
    "dGg7CiAgICBwaWxsLnN0eWxlLnRvcD0ob24ub2Zmc2V0VG9wLWRrLmNsaWVudFRvcCkrInB4Ijtw"
    "aWxsLnN0eWxlLmhlaWdodD1vbi5vZmZzZXRIZWlnaHQrInB4IjsKICAgIGlmKHNuYXB8fCFwbGFj"
    "ZWQpe3BsYWNlZD10cnVlO3BzLngudj1wcy54LnQ7cHMueC52ZWw9MDtwcy53LnY9cHMudy50O3Bz"
    "LncudmVsPTA7CiAgICAgIHBpbGwuc3R5bGUudHJhbnNmb3JtPSJ0cmFuc2xhdGVYKCIrcHMueC52"
    "KyJweCkiO3BpbGwuc3R5bGUud2lkdGg9cHMudy52KyJweCI7CiAgICAgIHBpbGwuY2xhc3NMaXN0"
    "LmFkZCgicmVhZHkiKTt9CiAgfQogIG5ldyBSZXNpemVPYnNlcnZlcigoKT0+cGxhY2UoIXBsYWNl"
    "ZCkpLm9ic2VydmUoZGspOwogIGFkZEV2ZW50TGlzdGVuZXIoInJlc2l6ZSIsKCk9PnBsYWNlKHRy"
    "dWUpKTsKICBhZGRFdmVudExpc3RlbmVyKCJsb2FkIiwoKT0+cGxhY2UoIXBsYWNlZCkpOwogIHNl"
    "dFRpbWVvdXQoKCk9PnBsYWNlKCFwbGFjZWQpLDYwKTtzZXRUaW1lb3V0KCgpPT5wbGFjZSghcGxh"
    "Y2VkKSw0MDApOwoKICAoZnVuY3Rpb24gbG9vcCh0KXsKICAgIGNvbnN0IGR0PU1hdGgubWluKC4w"
    "MzIsKHQtbGFzdCkvMTAwMCk7bGFzdD10OwogICAgaWYoRklORSl7YnRucy5mb3JFYWNoKChiLGkp"
    "PT57bGV0IGs9MDsKICAgICAgaWYobXghPT1JbmZpbml0eSl7Y29uc3Qgcj1iLmdldEJvdW5kaW5n"
    "Q2xpZW50UmVjdCgpOwogICAgICAgIGNvbnN0IGRpc3Q9TWF0aC5hYnMoci5sZWZ0K3Iud2lkdGgv"
    "Mi1teCk7az1NYXRoLm1heCgwLDEtZGlzdC8xNTApO30KICAgICAgc3BbaV0udD0xKy4zNCprKms7"
    "Y29uc3Qgdj1zcFtpXS5zdGVwKGR0KTsKICAgICAgYi5zdHlsZS50cmFuc2Zvcm09KHY+MS4wMDJ8"
    "fHNwW2ldLnZlbD4uMDF8fHNwW2ldLnZlbDwtLjAxKQogICAgICAgID8gInRyYW5zbGF0ZVkoIiso"
    "LTgqKHYtMSkpLnRvRml4ZWQoMikrInB4KSBzY2FsZSgiK3YudG9GaXhlZCgzKSsiKSIgOiAiIjt9"
    "KTt9CiAgICBpZihwaWxsJiZwbGFjZWQpe3BpbGwuc3R5bGUudHJhbnNmb3JtPSJ0cmFuc2xhdGVY"
    "KCIrcHMueC5zdGVwKGR0KS50b0ZpeGVkKDIpKyJweCkiOwogICAgICBwaWxsLnN0eWxlLndpZHRo"
    "PXBzLncuc3RlcChkdCkudG9GaXhlZCgyKSsicHgiO30KICAgIHJlcXVlc3RBbmltYXRpb25GcmFt"
    "ZShsb29wKTt9KShsYXN0KTsKICB3aW5kb3cuX19kb2NrUGxhY2U9cGxhY2U7cGxhY2UodHJ1ZSk7"
    "Cn0pKCk7CgovKiAxNCDCtyB2LXRhYnMgc2xpZGluZyBpbmRpY2F0b3IgZm9yIHRoZSBhZG1pbiBz"
    "dWJuYXYgKi8KKGZ1bmN0aW9uIHZ0YWJzKCl7CiAgY29uc3QgbmF2PWRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJhc3ViIikscGlsbD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgidnBpbGwiKTtpZigh"
    "bmF2fHwhcGlsbClyZXR1cm47CiAgY29uc3QgcHg9bmV3IFNwcmluZygwLHtrOjIxMCxkOjE5fSks"
    "cHc9bmV3IFNwcmluZygwLHtrOjIxMCxkOjE5fSk7bGV0IGxhc3Q9cGVyZm9ybWFuY2Uubm93KCks"
    "aW5pdD1mYWxzZTsKICBmdW5jdGlvbiBwbGFjZShzbmFwKXtjb25zdCBvbj1uYXYucXVlcnlTZWxl"
    "Y3RvcigiYnV0dG9uLm9uIik7CiAgICBpZighb258fCFvbi5vZmZzZXRXaWR0aHx8IW5hdi5vZmZz"
    "ZXRXaWR0aClyZXR1cm47CiAgICBweC50PW9uLm9mZnNldExlZnQtbmF2LmNsaWVudExlZnQ7cHcu"
    "dD1vbi5vZmZzZXRXaWR0aDsKICAgIHBpbGwuc3R5bGUudG9wPShvbi5vZmZzZXRUb3AtbmF2LmNs"
    "aWVudFRvcCkrInB4IjtwaWxsLnN0eWxlLmhlaWdodD1vbi5vZmZzZXRIZWlnaHQrInB4IjsKICAg"
    "IGlmKHNuYXB8fCFpbml0KXtpbml0PXRydWU7cHgudj1weC50O3B4LnZlbD0wO3B3LnY9cHcudDtw"
    "dy52ZWw9MDsKICAgICAgcGlsbC5zdHlsZS50cmFuc2Zvcm09InRyYW5zbGF0ZVgoIitweC52KyJw"
    "eCkiO3BpbGwuc3R5bGUud2lkdGg9cHcudisicHgiO319CiAgbmF2LmFkZEV2ZW50TGlzdGVuZXIo"
    "ImNsaWNrIiwoKT0+cmVxdWVzdEFuaW1hdGlvbkZyYW1lKCgpPT5wbGFjZShmYWxzZSkpKTsKICBu"
    "YXYuYWRkRXZlbnRMaXN0ZW5lcigic2Nyb2xsIiwoKT0+cGxhY2UoZmFsc2UpKTsKICBuZXcgUmVz"
    "aXplT2JzZXJ2ZXIoKCk9PnBsYWNlKCFpbml0KSkub2JzZXJ2ZShuYXYpOwogIGFkZEV2ZW50TGlz"
    "dGVuZXIoImxvYWQiLCgpPT5wbGFjZSghaW5pdCkpO3NldFRpbWVvdXQoKCk9PnBsYWNlKCFpbml0"
    "KSw4MCk7cGxhY2UodHJ1ZSk7CiAgKGZ1bmN0aW9uIGxvb3AodCl7Y29uc3QgZHQ9TWF0aC5taW4o"
    "LjAzMiwodC1sYXN0KS8xMDAwKTtsYXN0PXQ7CiAgICBpZihpbml0KXtwaWxsLnN0eWxlLnRyYW5z"
    "Zm9ybT0idHJhbnNsYXRlWCgiK3B4LnN0ZXAoZHQpLnRvRml4ZWQoMikrInB4KSI7cGlsbC5zdHls"
    "ZS53aWR0aD1wdy5zdGVwKGR0KS50b0ZpeGVkKDIpKyJweCI7fQogICAgcmVxdWVzdEFuaW1hdGlv"
    "bkZyYW1lKGxvb3ApO30pKGxhc3QpOwogIHdpbmRvdy5fX3Z0YWJQbGFjZT1wbGFjZTsKfSkoKTsK"
    "Ci8qIDA4IMK3IGtpbmV0aWMgZ3JpZCDigJQgcG9pbnRlciB3YXJwICsgY2xpY2sgcmlwcGxlcyAo"
    "Y2FudmFzIHBvcnQpICovCihmdW5jdGlvbiBraW5ldGljKCl7CiAgY29uc3QgY3Y9ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoImtnIik7aWYoIWN2fHxSTSlyZXR1cm47Y29uc3QgY3R4PWN2LmdldENv"
    "bnRleHQoIjJkIik7CiAgY29uc3QgU1A9MzAsSU5GPTIxMCxXQVJQPTIyLExFUlA9LjA5LFJHQj0i"
    "NDcsMTA3LDI1NSI7CiAgbGV0IFc9MCxIPTAsbW91c2U9e3g6LTk5OTkseTotOTk5OX0sdGd0PXt4"
    "Oi05OTk5LHk6LTk5OTl9LHJpcD1bXSxyYWY9MDsKICBmdW5jdGlvbiBzaXplKCl7Y29uc3QgZD1N"
    "YXRoLm1pbigxLjYsZGV2aWNlUGl4ZWxSYXRpb3x8MSk7Vz1pbm5lcldpZHRoO0g9aW5uZXJIZWln"
    "aHQ7CiAgICBjdi53aWR0aD1XKmQ7Y3YuaGVpZ2h0PUgqZDtjdi5zdHlsZS53aWR0aD1XKyJweCI7"
    "Y3Yuc3R5bGUuaGVpZ2h0PUgrInB4IjtjdHguc2V0VHJhbnNmb3JtKGQsMCwwLGQsMCwwKTt9CiAg"
    "YWRkRXZlbnRMaXN0ZW5lcigicmVzaXplIixzaXplKTtzaXplKCk7CiAgYWRkRXZlbnRMaXN0ZW5l"
    "cigicG9pbnRlcm1vdmUiLGU9Pnt0Z3Q9e3g6ZS5jbGllbnRYLHk6ZS5jbGllbnRZfX0se3Bhc3Np"
    "dmU6dHJ1ZX0pOwogIGFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJkb3duIixlPT57aWYocmlwLmxl"
    "bmd0aDw0KXJpcC5wdXNoKHt4OmUuY2xpZW50WCx5OmUuY2xpZW50WSxyOjAsbzoxfSl9LHtwYXNz"
    "aXZlOnRydWV9KTsKICBhZGRFdmVudExpc3RlbmVyKCJwb2ludGVybGVhdmUiLCgpPT50Z3Q9e3g6"
    "LTk5OTkseTotOTk5OX0pOwogIGZ1bmN0aW9uIHdhcnAoZ3gsZ3ksYyxyLGNvbHMscm93cyl7CiAg"
    "ICBjb25zdCBjcD1NYXRoLm1pbihjLzEuNSwoY29scy0xLWMpLzEuNSwxKSxycD1NYXRoLm1pbihy"
    "LzEuNSwocm93cy0xLXIpLzEuNSwxKSxwaW49Y3AqY3AqcnAqcnA7CiAgICBjb25zdCBkeD1neC1t"
    "b3VzZS54LGR5PWd5LW1vdXNlLnksZGlzdD1NYXRoLmh5cG90KGR4LGR5KTsKICAgIGNvbnN0IHBy"
    "b3g9TWF0aC5tYXgoMCwxLWRpc3QvSU5GKSpwaW47bGV0IHJ4PTAscnk9MDsKICAgIGZvcihjb25z"
    "dCBwIG9mIHJpcCl7Y29uc3QgZGR4PWd4LXAueCxkZHk9Z3ktcC55LGRkPU1hdGguaHlwb3QoZGR4"
    "LGRkeSksZGlmZj1kZC1wLnI7CiAgICAgIGlmKE1hdGguYWJzKGRpZmYpPDU1KXtjb25zdCBzdD0o"
    "MS1NYXRoLmFicyhkaWZmKS81NSkqcC5vKjE2KnBpbixhbj1NYXRoLmF0YW4yKGRkeSxkZHgpLHNn"
    "PWRpZmY8MD8tMToxOwogICAgICAgIHJ4Kz1NYXRoLmNvcyhhbikqc3Qqc2cqLTE7cnkrPU1hdGgu"
    "c2luKGFuKSpzdCpzZyotMTt9fQogICAgaWYoZGlzdDxJTkYmJmRpc3Q+MCYmcGluPjApe2NvbnN0"
    "IHQ9ZGlzdC9JTkYsZT0oMS10KSooMS10KSpNYXRoLm1pbigxLGRpc3QvNjApLHc9ZSpXQVJQKnBp"
    "bixhbj1NYXRoLmF0YW4yKGR5LGR4KTsKICAgICAgcmV0dXJuW2d4LU1hdGguY29zKGFuKSp3K3J4"
    "LGd5LU1hdGguc2luKGFuKSp3K3J5LHByb3hdO30KICAgIHJldHVybltneCtyeCxneStyeSxwcm94"
    "XTt9CiAgZnVuY3Rpb24gZnJhbWUoKXsKICAgIG1vdXNlLngrPSh0Z3QueC1tb3VzZS54KSpMRVJQ"
    "O21vdXNlLnkrPSh0Z3QueS1tb3VzZS55KSpMRVJQOwogICAgcmlwPXJpcC5maWx0ZXIocD0+e3Au"
    "cis9NztwLm8tPS4wMTY7cmV0dXJuIHAubz4wfSk7CiAgICBjdHguY2xlYXJSZWN0KDAsMCxXLEgp"
    "OwogICAgY29uc3QgY29scz1NYXRoLmNlaWwoVy9TUCkrMSxyb3dzPU1hdGguY2VpbChIL1NQKSsx"
    "LHB0cz1bXTsKICAgIGZvcihsZXQgcj0wO3I8cm93cztyKyspe2NvbnN0IHJvdz1bXTtmb3IobGV0"
    "IGM9MDtjPGNvbHM7YysrKXJvdy5wdXNoKHdhcnAoYypTUCxyKlNQLGMscixjb2xzLHJvd3MpKTtw"
    "dHMucHVzaChyb3cpO30KICAgIGZvcihsZXQgcj0wO3I8cm93cztyKyspZm9yKGxldCBjPTA7Yzxj"
    "b2xzO2MrKyl7Y29uc3QgcD1wdHNbcl1bY107CiAgICAgIGlmKGM8Y29scy0xKXtjb25zdCBxPXB0"
    "c1tyXVtjKzFdO2N0eC5zdHJva2VTdHlsZT0icmdiYSgiK1JHQisiLCIrKC4wNStwWzJdKi4yMiku"
    "dG9GaXhlZCgzKSsiKSI7CiAgICAgICAgY3R4LmxpbmVXaWR0aD0xO2N0eC5iZWdpblBhdGgoKTtj"
    "dHgubW92ZVRvKHBbMF0scFsxXSk7Y3R4LmxpbmVUbyhxWzBdLHFbMV0pO2N0eC5zdHJva2UoKTt9"
    "CiAgICAgIGlmKHI8cm93cy0xKXtjb25zdCBxPXB0c1tyKzFdW2NdO2N0eC5zdHJva2VTdHlsZT0i"
    "cmdiYSgiK1JHQisiLCIrKC4wNStwWzJdKi4yMikudG9GaXhlZCgzKSsiKSI7CiAgICAgICAgY3R4"
    "LmxpbmVXaWR0aD0xO2N0eC5iZWdpblBhdGgoKTtjdHgubW92ZVRvKHBbMF0scFsxXSk7Y3R4Lmxp"
    "bmVUbyhxWzBdLHFbMV0pO2N0eC5zdHJva2UoKTt9CiAgICAgIGlmKHBbMl0+LjA2KXtjdHguZmls"
    "bFN0eWxlPSJyZ2JhKCIrUkdCKyIsIisocFsyXSouNSkudG9GaXhlZCgzKSsiKSI7Y3R4LmJlZ2lu"
    "UGF0aCgpOwogICAgICAgIGN0eC5hcmMocFswXSxwWzFdLDEuNitwWzJdKjEuNiwwLDYuMjgzKTtj"
    "dHguZmlsbCgpO319CiAgICByYWY9cmVxdWVzdEFuaW1hdGlvbkZyYW1lKGZyYW1lKTt9CiAgZnJh"
    "bWUoKTsKICBkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCJ2aXNpYmlsaXR5Y2hhbmdlIiwoKT0+"
    "e2lmKGRvY3VtZW50LmhpZGRlbiljYW5jZWxBbmltYXRpb25GcmFtZShyYWYpO2Vsc2UgZnJhbWUo"
    "KTt9KTsKfSkoKTsKCi8qIDAxIMK3IGRvdCBsb2FkZXIgc3BsYXNoIChraXQgZnJhbWUgZGF0YSkg"
    "Ki8KKGZ1bmN0aW9uIHNwbGFzaCgpewogIGNvbnN0IGc9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "ImRnIiksc3A9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNwbGFzaCIpO2lmKCFnKXJldHVybjsK"
    "ICBmb3IobGV0IGk9MDtpPDQ5O2krKylnLmFwcGVuZENoaWxkKGRvY3VtZW50LmNyZWF0ZUVsZW1l"
    "bnQoImkiKSk7CiAgY29uc3QgY2VsbHM9Wy4uLmcuY2hpbGRyZW5dOwogIGNvbnN0IEY9W1sxNCw3"
    "LDAsOCw2LDEzLDIwXSxbMTQsNywxMywyMCwxNiwyNywyMV0sWzE0LDIwLDI3LDIxLDM0LDI0LDI4"
    "XSxbMjcsMjEsMzQsMjgsNDEsMzIsMzVdLAogICBbMzQsMjgsNDEsMzUsNDgsNDAsNDJdLFszNCwy"
    "OCw0MSwzNSw0OCw0Miw0Nl0sWzM0LDI4LDQxLDM1LDQ4LDQyLDM4XSxbMzQsMjgsNDEsMzUsNDgs"
    "MzAsMjFdLAogICBbMzQsMjgsNDEsNDgsMjEsMjIsMTRdLFszNCwyOCw0MSwyMSwxNCwxNiwyN10s"
    "WzM0LDI4LDIxLDE0LDEwLDIwLDI3XSxbMjgsMjEsMTQsNCwxMywyMCwyN10sCiAgIFsyOCwyMSwx"
    "NCwxMiw2LDEzLDIwXSxbMjgsMjEsMTQsNiwxMywyMCwxMV0sWzI4LDIxLDE0LDYsMTMsMjAsMTBd"
    "LFsxNCw2LDEzLDIwLDksNywyMV1dOwogIGxldCBmPTA7Y29uc3QgaXY9c2V0SW50ZXJ2YWwoKCk9"
    "PntjZWxscy5mb3JFYWNoKGM9PmMuY2xhc3NMaXN0LnJlbW92ZSgiYWN0IikpOwogICAgRltmJUYu"
    "bGVuZ3RoXS5mb3JFYWNoKGk9PmNlbGxzW2ldJiZjZWxsc1tpXS5jbGFzc0xpc3QuYWRkKCJhY3Qi"
    "KSk7ZisrO30sMTAwKTsKICBzZXRUaW1lb3V0KCgpPT57Y2xlYXJJbnRlcnZhbChpdik7c3AuY2xh"
    "c3NMaXN0LmFkZCgiZ29uZSIpOwogICAgc2V0VGltZW91dCgoKT0+e3NwLnJlbW92ZSgpO2lmKHdp"
    "bmRvdy5fX2RvY2tQbGFjZSl3aW5kb3cuX19kb2NrUGxhY2UodHJ1ZSk7CiAgICAgIGlmKHdpbmRv"
    "dy5fX3Z0YWJQbGFjZSl3aW5kb3cuX192dGFiUGxhY2UodHJ1ZSk7fSw2MDApO30sUk0/MzAwOjE3"
    "NTApOwp9KSgpOwoKLyogc3RhZ2dlcmVkIHJldmVhbCBvbiBldmVyeSB2aWV3IGNoYW5nZSAqLwpm"
    "dW5jdGlvbiByZXZlYWxTdGFnZShlbCl7CiAgaWYoIWVsfHxSTSlyZXR1cm47CiAgWy4uLmVsLmNo"
    "aWxkcmVuXS5zbGljZSgwLDE0KS5mb3JFYWNoKChjLGkpPT57CiAgICBpZighYy5hbmltYXRlKXJl"
    "dHVybjsKICAgIHRyeXsKICAgICAgYy5nZXRBbmltYXRpb25zKCkuZm9yRWFjaChhPT57aWYoYS5p"
    "ZD09PSJydiIpYS5jYW5jZWwoKTt9KTsKICAgICAgY29uc3QgYW49Yy5hbmltYXRlKFt7b3BhY2l0"
    "eTowLHRyYW5zZm9ybToidHJhbnNsYXRlWSgxNHB4KSBzY2FsZSguOTg1KSJ9LHtvcGFjaXR5OjEs"
    "dHJhbnNmb3JtOiJub25lIn1dLAogICAgICAgIHtkdXJhdGlvbjo1MjAsZGVsYXk6aSo0MCxlYXNp"
    "bmc6ImN1YmljLWJlemllciguMjIsMSwuMzYsMSkiLGZpbGw6ImJhY2t3YXJkcyJ9KTsKICAgICAg"
    "YW4uaWQ9InJ2IjsKICAgIH1jYXRjaChlKXt9CiAgfSk7fQoKLyogbnVtYmVyIHRpY2tlciAqLwpm"
    "dW5jdGlvbiB0aWNrVG8oZWwsdG8sZHVyKXtpZighZWwpcmV0dXJuO2NvbnN0IGZyb209cGFyc2VG"
    "bG9hdChlbC50ZXh0Q29udGVudC5yZXBsYWNlKC9bXlxkLl0vZywiIikpfHwwOwogIGlmKFJNKXtl"
    "bC50ZXh0Q29udGVudD10by50b0ZpeGVkKDIpO3JldHVybn0KICBjb25zdCB0MD1wZXJmb3JtYW5j"
    "ZS5ub3coKTsoZnVuY3Rpb24gc3QodCl7Y29uc3QgcD1NYXRoLm1pbigxLCh0LXQwKS8oZHVyfHw2"
    "MDApKTsKICAgIGNvbnN0IGU9MS1NYXRoLnBvdygxLXAsMyk7ZWwudGV4dENvbnRlbnQ9KGZyb20r"
    "KHRvLWZyb20pKmUpLnRvRml4ZWQoMik7CiAgICBpZihwPDEpcmVxdWVzdEFuaW1hdGlvbkZyYW1l"
    "KHN0KTt9KSh0MCk7fQoKLyogcHJlc3MgcmlwcGxlIG9uIGV2ZXJ5IGJ1dHRvbiAqLwpkb2N1bWVu"
    "dC5hZGRFdmVudExpc3RlbmVyKCJwb2ludGVyZG93biIsZT0+e2NvbnN0IGI9ZS50YXJnZXQuY2xv"
    "c2VzdCgiLmJ0biwuY2hpcHJvdyBidXR0b24sLnN1Ym5hdiBidXR0b24iKTsKICBpZighYnx8Uk0p"
    "cmV0dXJuO2NvbnN0IHI9Yi5nZXRCb3VuZGluZ0NsaWVudFJlY3QoKSxkPU1hdGgubWF4KHIud2lk"
    "dGgsci5oZWlnaHQpKjEuNDsKICBjb25zdCBzPWRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoInNwYW4i"
    "KTtzLmNsYXNzTmFtZT0icmlwIjsKICBzLnN0eWxlLmNzc1RleHQ9ImxlZnQ6IisoZS5jbGllbnRY"
    "LXIubGVmdCkrInB4O3RvcDoiKyhlLmNsaWVudFktci50b3ApKyJweDt3aWR0aDoiK2QrInB4O2hl"
    "aWdodDoiK2QrInB4IjsKICBpZihnZXRDb21wdXRlZFN0eWxlKGIpLnBvc2l0aW9uPT09InN0YXRp"
    "YyIpYi5zdHlsZS5wb3NpdGlvbj0icmVsYXRpdmUiOwogIGIuYXBwZW5kQ2hpbGQocyk7c2V0VGlt"
    "ZW91dCgoKT0+cy5yZW1vdmUoKSw1NjApO30pOwoKLyogMTUgwrcgdGlsdCArIGdsYXJlICovCmZ1"
    "bmN0aW9uIGJpbmRUaWx0KCl7ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtdGlsdF0i"
    "KS5mb3JFYWNoKGM9PnsKICBpZihjLl90fHxSTSlyZXR1cm47Yy5fdD0xOwogIGMuYWRkRXZlbnRM"
    "aXN0ZW5lcigicG9pbnRlcm1vdmUiLGU9Pntjb25zdCByPWMuZ2V0Qm91bmRpbmdDbGllbnRSZWN0"
    "KCk7CiAgICBjb25zdCB4PShlLmNsaWVudFgtci5sZWZ0KS9yLndpZHRoLHk9KGUuY2xpZW50WS1y"
    "LnRvcCkvci5oZWlnaHQ7CiAgICBjLnN0eWxlLnRyYW5zZm9ybT0icGVyc3BlY3RpdmUoNzAwcHgp"
    "IHJvdGF0ZVgoIisoKC41LXkpKjkpLnRvRml4ZWQoMikrImRlZykgcm90YXRlWSgiKygoeC0uNSkq"
    "MTEpLnRvRml4ZWQoMikrImRlZykgdHJhbnNsYXRlWig0cHgpIjsKICAgIGMuc3R5bGUuc2V0UHJv"
    "cGVydHkoIi0tbXgiLCh4KjEwMCkrIiUiKTtjLnN0eWxlLnNldFByb3BlcnR5KCItLW15IiwoeSox"
    "MDApKyIlIik7fSk7CiAgYy5hZGRFdmVudExpc3RlbmVyKCJwb2ludGVybGVhdmUiLCgpPT5jLnN0"
    "eWxlLnRyYW5zZm9ybT0iIik7fSk7fQoKLyogc3VjY2VzcyBidXJzdCAqLwpmdW5jdGlvbiBidXJz"
    "dCgpe2NvbnN0IGZ4PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJmeCIpO2lmKCFmeClyZXR1cm47"
    "ZnguY2xhc3NMaXN0LmFkZCgib24iKTsKICBjb25zdCB3PWZ4LnF1ZXJ5U2VsZWN0b3IoIi53YXZl"
    "Iik7dy5zdHlsZS5hbmltYXRpb249Im5vbmUiO3ZvaWQgdy5vZmZzZXRXaWR0aDt3LnN0eWxlLmFu"
    "aW1hdGlvbj0iIjsKICBzZXRUaW1lb3V0KCgpPT5meC5jbGFzc0xpc3QucmVtb3ZlKCJvbiIpLDkw"
    "MCk7fQoKLyog4pSA4pSAIFBheSArIHZlcmlmeSBkZXBvc2l0IGZsb3cgKDUtbWludXRlIHdpbmRv"
    "dywgdmVyaWZ5IG9ubHkgb24gdGFwKSDilIDilIAgKi8KbGV0IGRlcFRpY2s9bnVsbCxkZXBKb2I9"
    "bnVsbDsKZnVuY3Rpb24gYW1vdW50U2hlZXQoKXsKICBjb25zdCBwaWNrcz1bNTAsMTAwLDIwMCw1"
    "MDAsMTAwMCwyMDAwXTsKICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNp"
    "emU6MXJlbSI+JHtDTVMud2FsS2lja3x8IkFkZCBmdW5kcyJ9PC9oMz4KICAgIDxwIGNsYXNzPSJz"
    "dWIiPlBpY2sgYW4gYW1vdW50IG9yIHR5cGUgeW91ciBvd24uPC9wPgogICAgPGRpdiBjbGFzcz0i"
    "Y2hpcHMiIHN0eWxlPSJtYXJnaW46MTJweCAwIDRweDtkaXNwbGF5OmZsZXg7ZmxleC13cmFwOndy"
    "YXA7Z2FwOjdweCI+CiAgICAgICR7cGlja3MubWFwKHY9PmA8YnV0dG9uIGNsYXNzPSJidG4gc20i"
    "IGRhdGEtcGljaz0iJHt2fSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iPiR7bW9uZXkodil9PC9idXR0"
    "b24+YCkuam9pbigiIil9CiAgICA8L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+Q3VzdG9tIGFtb3VudDwvbGFiZWw+CiAgICAgIDxpbnB1"
    "dCBjbGFzcz0iaW5wIiBpZD0iYW10aW4iIHR5cGU9Im51bWJlciIgaW5wdXRtb2RlPSJudW1lcmlj"
    "IiBtaW49IjEiIHBsYWNlaG9sZGVyPSJFbnRlciBhbW91bnQiPjwvZGl2PgogICAgPHAgY2xhc3M9"
    "InN1YiIgaWQ9ImFtdGhpbnQiIHN0eWxlPSJtYXJnaW46MCAwIDEwcHg7Zm9udC1zaXplOi43MnJl"
    "bSI+PC9wPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBibG9jayIgaWQ9ImFtdGdvIj5Db250"
    "aW51ZTwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJsb2NrIHNtIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+Q2FuY2VsPC9idXR0b24+YCk7CiAgY29uc3Qg"
    "aW5wPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJhbXRpbiIpLCBnbz1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiYW10Z28iKSwKICAgICAgICBoaW50PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJh"
    "bXRoaW50Iik7CiAgY29uc3QgbG89KEFETSYmQURNLnBheSYmQURNLnBheS5taW4pfHwxMCwgaGk9"
    "KEFETSYmQURNLnBheSYmQURNLnBheS5tYXgpfHwyMDAwMDsKICBoaW50LnRleHRDb250ZW50PSJC"
    "ZXR3ZWVuICIrbW9uZXkobG8pKyIgYW5kICIrbW9uZXkoaGkpKyIuIjsKICBjb25zdCBzdGFydD12"
    "PT57CiAgICBjb25zdCBuPU51bWJlcih2KTsKICAgIGlmKCFufHxuPD0wKXt0b2FzdCgiRW50ZXIg"
    "YW4gYW1vdW50IGZpcnN0Iik7aW5wJiZpbnAuZm9jdXMoKTtyZXR1cm47fQogICAgaWYobjxsb3x8"
    "bj5oaSl7dG9hc3QoIkFsbG93ZWQgIittb25leShsbykrIiB0byAiK21vbmV5KGhpKSk7cmV0dXJu"
    "O30KICAgIGRlcG9zaXRTaGVldChuKTsKICB9OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IltkYXRhLXBpY2tdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnN0YXJ0KGIuZGF0YXNldC5w"
    "aWNrKSk7CiAgZ28ub25jbGljaz0oKT0+c3RhcnQoaW5wLnZhbHVlKTsKICBpZihpbnApaW5wLm9u"
    "a2V5ZG93bj1lPT57aWYoZS5rZXk9PT0iRW50ZXIiKXN0YXJ0KGlucC52YWx1ZSk7fTsKICBzZXRU"
    "aW1lb3V0KCgpPT57aWYoaW5wKWlucC5mb2N1cygpO30sMjUwKTsKfQphc3luYyBmdW5jdGlvbiBs"
    "aXZlRGVwb3NpdFNoZWV0KGEpewogIGNsZWFySW50ZXJ2YWwoZGVwVGljayk7Y2xlYXJUaW1lb3V0"
    "KGRlcEpvYik7CiAgY29uc3QgZD1hd2FpdCBhcGlUcnkoIi9hcGkvZGVwb3NpdC9jcmVhdGUiLHth"
    "bW91bnQ6TnVtYmVyKGEpfSk7CiAgaWYoIWQpcmV0dXJuOwogIGNvbnN0IG1pbnM9ZC53aW5kb3d8"
    "fDU7CiAgbGV0IGxlZnQ9TWF0aC5tYXgoMCxNYXRoLnJvdW5kKChuZXcgRGF0ZShkLmV4cGlyZXNf"
    "YXQpLW5ldyBEYXRlKCkpLzEwMDApKXx8bWlucyo2MDsKICBjb25zdCB0b3RhbD1taW5zKjYwOwog"
    "IGxldCBkb25lPWZhbHNlLGJ1c3k9ZmFsc2U7CiAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAg"
    "MCA0cHg7Zm9udC1zaXplOjFyZW0iPiR7Q01TLnNoZWV0VGl0bGV9ICR7bW9uZXkoZC5hbW91bnQp"
    "fTwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj4ke0NNUy5zaGVldFN1Yi5yZXBsYWNlKCJ7bX0iLG1p"
    "bnMpfTwvcD4KICAgICR7ZC5xcj9gPGRpdiBjbGFzcz0icXIiIHN0eWxlPSJwYWRkaW5nOjA7b3Zl"
    "cmZsb3c6aGlkZGVuIj48aW1nIHNyYz0iJHtkLnFyfSIgYWx0PSJVUEkgUVIiIHN0eWxlPSJ3aWR0"
    "aDoxMDAlO2Rpc3BsYXk6YmxvY2siPjwvZGl2PmA6IiJ9CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNw"
    "YW4+VVBJIElEPC9zcGFuPjxiPiR7ZXNjKGQudXBpfHwiIil9PC9iPjwvZGl2PgogICAgPGRpdiBj"
    "bGFzcz0ia3YiPjxzcGFuPlBheWVlPC9zcGFuPjxiPiR7ZXNjKGQucGF5ZWV8fCIiKX08L2I+PC9k"
    "aXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+QW1vdW50PC9zcGFuPjxiPiR7bW9uZXkoZC5h"
    "bW91bnQpfTwvYj48L2Rpdj4KICAgICR7ZC5ib251cz9gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkJv"
    "bnVzPC9zcGFuPjxiPiske21vbmV5KGQuYm9udXMpfTwvYj48L2Rpdj5gOiIifQogICAgPGRpdiBj"
    "bGFzcz0ia3YiPjxzcGFuPk5vdGUgLyByZWZlcmVuY2U8L3NwYW4+PGI+JHtlc2MoZC5jb2RlKX08"
    "L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+RXhwaXJlcyBpbjwvc3Bhbj48YiBp"
    "ZD0iZHBjZCI+JHtNYXRoLmZsb29yKGxlZnQvNjApfToke1N0cmluZyhsZWZ0JTYwKS5wYWRTdGFy"
    "dCgyLCIwIil9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0idGltZXJiYXIiIGlkPSJkcGJhciI+"
    "PGkgc3R5bGU9IndpZHRoOjEwMCUiPjwvaT48L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN0ZXBzIiBp"
    "ZD0iZHN0ZXBzIj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCBvbiI+PGk+MTwvaT4ke0NNUy5zdGVw"
    "MX08L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCIgaWQ9InMyIj48aT4yPC9pPiR7Q01TLnN0"
    "ZXAyfTwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGVwIiBpZD0iczMiPjxpPjM8L2k+JHtDTVMu"
    "c3RlcDN9PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAiIGlkPSJzNCI+PGk+NDwvaT4ke0NN"
    "Uy5zdGVwNH08L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0i"
    "bWFyZ2luLXRvcDo4cHgiPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4iIGlkPSJkcGF5Ij7wn4+m"
    "IENvcHkgVVBJIElEPC9idXR0b24+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJk"
    "dmVyIj4ke1NUT1JFLmF1dG9jaGVjaz09PWZhbHNlPyLinIUgSSBoYXZlIHBhaWQiOiLinIUgIitD"
    "TVMudmVyaWZ5QnRufTwvYnV0dG9uPjwvZGl2PgogICAgJHtTVE9SRS5hdXRvY2hlY2s9PT1mYWxz"
    "ZT8iIjpgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQgYmxvY2sgc20iIGlkPSJkdXRyIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDo4cHgiPvCfp74gU2VuZCB0cmFuc2FjdGlvbiBuby48L2J1dHRvbj5gfQogICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgi"
    "IGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTsKICBjb25zdCBnPWlkPT5kb2N1bWVudC5nZXRF"
    "bGVtZW50QnlJZChpZCk7CiAgY29uc3QgcGF5Qj1nKCJkcGF5IiksdmVyQj1nKCJkdmVyIiksdXRy"
    "Qj1nKCJkdXRyIik7CiAgY29uc3QgYWxpdmU9KCk9PmcoImRwY2QiKSYmZG9jdW1lbnQuZ2V0RWxl"
    "bWVudEJ5SWQoInNoZWV0IikuY2xhc3NMaXN0LmNvbnRhaW5zKCJvcGVuIik7CiAgZnVuY3Rpb24g"
    "ZXhwaXJlKCl7CiAgICBjbGVhckludGVydmFsKGRlcFRpY2spOwogICAgaWYoIWFsaXZlKCkpcmV0"
    "dXJuOwogICAgZygiZHBjZCIpLnRleHRDb250ZW50PSIwOjAwIjtnKCJkcGJhciIpLmNsYXNzTGlz"
    "dC5hZGQoImxvdyIpO2coImRwYmFyIikuZmlyc3RDaGlsZC5zdHlsZS53aWR0aD0iMCUiOwogICAg"
    "dmVyQi5kaXNhYmxlZD10cnVlO3V0ckIuZGlzYWJsZWQ9dHJ1ZTt2ZXJCLnRleHRDb250ZW50PSJF"
    "eHBpcmVkIjsKICAgIHBheUIuY2xhc3NOYW1lPSJidG4iO3BheUIudGV4dENvbnRlbnQ9Ik5ldyBS"
    "ZXF1ZXN0IjtwYXlCLm9uY2xpY2s9KCk9PmRlcG9zaXRTaGVldChhKTsKICAgIHRvYXN0KENNUy5t"
    "c2dFeHBpcmVkKTsKICB9CiAgZGVwVGljaz1zZXRJbnRlcnZhbCgoKT0+ewogICAgaWYoIWFsaXZl"
    "KCkpe2NsZWFySW50ZXJ2YWwoZGVwVGljayk7cmV0dXJuO30KICAgIGxlZnQtLTsKICAgIGcoImRw"
    "Y2QiKS50ZXh0Q29udGVudD1NYXRoLm1heCgwLE1hdGguZmxvb3IobGVmdC82MCkpKyI6IitTdHJp"
    "bmcoTWF0aC5tYXgoMCxsZWZ0JTYwKSkucGFkU3RhcnQoMiwiMCIpOwogICAgZygiZHBiYXIiKS5m"
    "aXJzdENoaWxkLnN0eWxlLndpZHRoPShNYXRoLm1heCgwLGxlZnQpL3RvdGFsKjEwMCkrIiUiOwog"
    "ICAgZygiZHBiYXIiKS5jbGFzc0xpc3QudG9nZ2xlKCJsb3ciLGxlZnQ8PTYwKTsKICAgIGlmKGxl"
    "ZnQ8PTApZXhwaXJlKCk7CiAgfSwxMDAwKTsKICBpZihwYXlCKXBheUIub25jbGljaz0oKT0+ewog"
    "ICAgaWYoZG9uZSlyZXR1cm47CiAgICBnKCJzMiIpLmNsYXNzTmFtZT0ic3RlcCBvbiI7ZygiczMi"
    "KS5jbGFzc05hbWU9InN0ZXAgcnVuIjsKICAgIHZlckIuY2xhc3NMaXN0LnJlbW92ZSgiZ2xhc3Mi"
    "KTt2ZXJCLmNsYXNzTGlzdC5hZGQoImN0YSIpOwogICAgdHJ5e25hdmlnYXRvci5jbGlwYm9hcmQm"
    "Jm5hdmlnYXRvci5jbGlwYm9hcmQud3JpdGVUZXh0KGQudXBpfHwiIik7fWNhdGNoKGUpe30KICAg"
    "IHRvYXN0KCJVUEkgSUQgY29waWVkOiAiKyhkLnVwaXx8IiIpKTsKICB9OwogIGlmKHZlckIpdmVy"
    "Qi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKGRvbmV8fGJ1c3l8fGxlZnQ8PTApcmV0dXJuOwog"
    "ICAgaWYoU1RPUkUuYXV0b2NoZWNrPT09ZmFsc2UpeyAgICAgICAgICAvKiBubyBhdXRvbWF0aWMg"
    "Y2hlY2tpbmcg4oCUIGNvbGxlY3QgcHJvb2YgKi8KICAgICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFy"
    "Z2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPlNlbmQgeW91ciBwYXltZW50IHByb29mPC9oMz4K"
    "ICAgICAgICA8cCBjbGFzcz0ic3ViIj5BIHNjcmVlbnNob3Qgb2YgdGhlIHN1Y2Nlc3NmdWwgcGF5"
    "bWVudCBhbmQgdGhlIFVUUi48L3A+CiAgICAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJt"
    "YXJnaW4tdG9wOjEycHgiPjxsYWJlbD5TY3JlZW5zaG90PC9sYWJlbD4KICAgICAgICAgIDxpbnB1"
    "dCBjbGFzcz0iaW5wIiBpZD0icGZzaG90IiB0eXBlPSJmaWxlIiBhY2NlcHQ9ImltYWdlLyoiPjwv"
    "ZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+VVRSIC8gdHJhbnNhY3Rpb24g"
    "bnVtYmVyPC9sYWJlbD4KICAgICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0icGZ1dHIiIGlu"
    "cHV0bW9kZT0ibnVtZXJpYyIgcGxhY2Vob2xkZXI9IjEyIGRpZ2l0cyI+PC9kaXY+CiAgICAgICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBibG9jayIgaWQ9InBmZ28iPlN1Ym1pdCBmb3IgcmV2aWV3"
    "PC9idXR0b24+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJsb2NrIHNtIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+Q2FuY2VsPC9idXR0b24+YCk7CiAgICAgIGRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJwZmdvIikub25jbGljaz1hc3luYygpPT57CiAgICAgICAg"
    "Y29uc3QgZj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicGZzaG90Iik7CiAgICAgICAgY29uc3Qg"
    "dT0oZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInBmdXRyIikudmFsdWV8fCIiKS5yZXBsYWNlKC9c"
    "RC9nLCIiKTsKICAgICAgICBpZih1Lmxlbmd0aDw2KXt0b2FzdCgiRW50ZXIgdGhlIFVUUiBmcm9t"
    "IHlvdXIgcGF5bWVudCBhcHAiKTtyZXR1cm47fQogICAgICAgIGNvbnN0IGZpbGU9KGYmJmYuZmls"
    "ZXMmJmYuZmlsZXNbMF0pfHxudWxsOwogICAgICAgIGxldCBzaG90PSIiOwogICAgICAgIGlmKGZp"
    "bGUpewogICAgICAgICAgaWYoZmlsZS5zaXplPjgqMTAyNCoxMDI0KXt0b2FzdCgiVGhhdCBpbWFn"
    "ZSBpcyB0b28gbGFyZ2UgKDggTUIgbWF4KSIpO3JldHVybjt9CiAgICAgICAgICBzaG90PWF3YWl0"
    "IG5ldyBQcm9taXNlKHJlcz0+e2NvbnN0IHJkPW5ldyBGaWxlUmVhZGVyKCk7CiAgICAgICAgICAg"
    "IHJkLm9ubG9hZD0oKT0+cmVzKFN0cmluZyhyZC5yZXN1bHR8fCIiKSk7cmQub25lcnJvcj0oKT0+"
    "cmVzKCIiKTsKICAgICAgICAgICAgcmQucmVhZEFzRGF0YVVSTChmaWxlKTt9KTsKICAgICAgICB9"
    "CiAgICAgICAgY29uc3QgYnRuPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJwZmdvIik7YnRuLmRp"
    "c2FibGVkPXRydWU7YnRuLnRleHRDb250ZW50PSJTZW5kaW5n4oCmIjsKICAgICAgICBjb25zdCBy"
    "cj1hd2FpdCBhcGlUcnkoIi9hcGkvZGVwb3NpdC9jbGFpbSIse2lkOmQuaWQsdXRyOnUsc2hvdDpz"
    "aG90fSk7CiAgICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0iU3VibWl0"
    "IGZvciByZXZpZXciOwogICAgICAgIGlmKCFycilyZXR1cm47CiAgICAgICAgY2xvc2VTaGVldCgp"
    "O3RvYXN0KCJTZW50IOKAlCB3ZSB3aWxsIGNvbmZpcm0gaXQgc2hvcnRseSIpOwogICAgICAgIGF3"
    "YWl0IHJlZnJlc2goKTsKICAgICAgfTsKICAgICAgcmV0dXJuOwogICAgfQogICAgYnVzeT10cnVl"
    "O3ZlckIuZGlzYWJsZWQ9dHJ1ZTt2ZXJCLnRleHRDb250ZW50PSJDaGVja2luZ+KApiI7ZygiczMi"
    "KS5jbGFzc05hbWU9InN0ZXAgcnVuIjsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2Rl"
    "cG9zaXQvdmVyaWZ5Iix7aWQ6ZC5pZH0pOwogICAgYnVzeT1mYWxzZTsKICAgIGlmKCFhbGl2ZSgp"
    "KXJldHVybjsKICAgIHZlckIuZGlzYWJsZWQ9ZmFsc2U7dmVyQi50ZXh0Q29udGVudD1DTVMudmVy"
    "aWZ5QnRuOwogICAgaWYoIXIpcmV0dXJuOwogICAgaWYoci5zdGF0dXM9PT0iY29tcGxldGVkIil7"
    "CiAgICAgIGRvbmU9dHJ1ZTtjbGVhckludGVydmFsKGRlcFRpY2spOwogICAgICBnKCJzMyIpLmNs"
    "YXNzTmFtZT0ic3RlcCBvbiI7ZygiczQiKS5jbGFzc05hbWU9InN0ZXAgb24iOwogICAgICBpZih0"
    "eXBlb2Ygci5iYWxhbmNlPT09Im51bWJlciIpQkFMPXIuYmFsYW5jZTsKICAgICAgc3luY0JhbCgp"
    "O2J1cnN0KCk7dG9hc3QoQ01TLm1zZ09rKTsKICAgICAgc2V0VGltZW91dCgoKT0+e2Nsb3NlU2hl"
    "ZXQoKTtyZWZyZXNoKCk7fSw5MDApOwogICAgfWVsc2UgaWYoci5zdGF0dXM9PT0icmV2aWV3Iil7"
    "CiAgICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIG9uIjt0b2FzdCgiUGF5bWVudCBzZW50IGZv"
    "ciBjaGVja2luZyDigJQgd2Ugd2lsbCBjb25maXJtIGl0IHNob3J0bHkiKTsKICAgIH1lbHNlIGlm"
    "KHIuc3RhdHVzPT09ImV4cGlyZWQiKXsKICAgICAgZXhwaXJlKCk7CiAgICB9ZWxzZXsKICAgICAg"
    "dG9hc3QoQ01TLm1zZ05vbmUpOwogICAgfQogIH07CiAgaWYodXRyQil1dHJCLm9uY2xpY2s9YXN5"
    "bmMoKT0+ewogICAgY29uc3Qgdj1wcm9tcHQoIkVudGVyIHRoZSAxMi1kaWdpdCBVVFIgZnJvbSB5"
    "b3VyIHBheW1lbnQgYXBwIik7CiAgICBpZighdilyZXR1cm47CiAgICBjb25zdCByPWF3YWl0IGFw"
    "aVRyeSgiL2FwaS9kZXBvc2l0L3V0ciIse2lkOmQuaWQsdXRyOnZ9KTsKICAgIGlmKHIpdG9hc3Qo"
    "IlRoYW5rcyDigJQgd2UgYXJlIGNoZWNraW5nIHlvdXIgcGF5bWVudCIpOwogIH07Cn0KZnVuY3Rp"
    "b24gZGVwb3NpdFNoZWV0KGEpewogIGlmKExJVkUpcmV0dXJuIGxpdmVEZXBvc2l0U2hlZXQoYSk7"
    "CiAgY2xlYXJJbnRlcnZhbChkZXBUaWNrKTtjbGVhclRpbWVvdXQoZGVwSm9iKTsKICBjb25zdCBj"
    "b2RlPShDTVMucGF5UHJlZml4fHwiUEFZIikrTWF0aC5mbG9vcigxMDAwK01hdGgucmFuZG9tKCkq"
    "ODk5OSk7CiAgY29uc3QgbWlucz1NYXRoLm1heCgxLHBhcnNlSW50KENNUy5wYXlXaW5kb3csMTAp"
    "fHw1KSwgdG90YWw9bWlucyo2MDsKICBsZXQgbGVmdD10b3RhbCxwYWlkPWZhbHNlLGRvbmU9ZmFs"
    "c2UsYnVzeT1mYWxzZTsKICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNp"
    "emU6MXJlbSI+JHtDTVMuc2hlZXRUaXRsZX0gJHttb25leShhKX08L2gzPgogICAgPHAgY2xhc3M9"
    "InN1YiI+JHtDTVMuc2hlZXRTdWIucmVwbGFjZSgie219IixtaW5zKX08L3A+CiAgICA8ZGl2IGNs"
    "YXNzPSJxciI+UVIgQ09ERTxicj4oZ2VuZXJhdGVkIGJ5IGJvdCk8L2Rpdj4KICAgIDxkaXYgY2xh"
    "c3M9Imt2Ij48c3Bhbj5VUEkgSUQ8L3NwYW4+PGI+JHtlc2MoU1RPUkUudXBpfHwibm90IHNldCB5"
    "ZXQiKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+UGF5ZWU8L3NwYW4+PGI+"
    "JHtlc2MoU1RPUkUucGF5ZWV8fCLigJQiKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+"
    "PHNwYW4+QW1vdW50PC9zcGFuPjxiPiR7bW9uZXkoYSl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFz"
    "cz0ia3YiPjxzcGFuPk5vdGUgLyByZWZlcmVuY2U8L3NwYW4+PGI+JHtjb2RlfTwvYj48L2Rpdj4K"
    "ICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5FeHBpcmVzIGluPC9zcGFuPjxiIGlkPSJkcGNkIj4k"
    "e21pbnN9OjAwPC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0idGltZXJiYXIiIGlkPSJkcGJhciI+"
    "PGkgc3R5bGU9IndpZHRoOjEwMCUiPjwvaT48L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN0ZXBzIiBp"
    "ZD0iZHN0ZXBzIj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCBvbiI+PGk+MTwvaT4ke0NNUy5zdGVw"
    "MX08L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCIgaWQ9InMyIj48aT4yPC9pPiR7Q01TLnN0"
    "ZXAyfTwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGVwIiBpZD0iczMiPjxpPjM8L2k+JHtDTVMu"
    "c3RlcDN9PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAiIGlkPSJzNCI+PGk+NDwvaT4ke0NN"
    "Uy5zdGVwNH08L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0i"
    "bWFyZ2luLXRvcDo4cHgiPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIiBpZD0iZHBheSI+"
    "JHtDTVMucGF5QnRufTwvYnV0dG9uPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2xhc3MiIGlk"
    "PSJkdmVyIj4ke0NNUy52ZXJpZnlCdG59PC9idXR0b24+PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNz"
    "PSJidG4gc29mdCBibG9jayBzbSIgaWQ9ImR1dHIiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+JHtD"
    "TVMudXRyQnRufTwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJsb2NrIHNt"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTsKICBj"
    "b25zdCBnPWlkPT5kb2N1bWVudC5nZXRFbGVtZW50QnlJZChpZCk7CiAgY29uc3QgcGF5Qj1nKCJk"
    "cGF5IiksdmVyQj1nKCJkdmVyIiksdXRyQj1nKCJkdXRyIik7CiAgZnVuY3Rpb24gZm10KG4pe3Jl"
    "dHVybiBNYXRoLmZsb29yKG4vNjApKyI6IitTdHJpbmcobiU2MCkucGFkU3RhcnQoMiwiMCIpO30K"
    "ICBmdW5jdGlvbiBhbGl2ZSgpe3JldHVybiBnKCJkcGNkIikmJmRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJzaGVldCIpLmNsYXNzTGlzdC5jb250YWlucygib3BlbiIpO30KICBmdW5jdGlvbiBleHBp"
    "cmUoKXsKICAgIGNsZWFySW50ZXJ2YWwoZGVwVGljayk7Y2xlYXJUaW1lb3V0KGRlcEpvYik7CiAg"
    "ICBpZighYWxpdmUoKSlyZXR1cm47CiAgICBnKCJkcGNkIikudGV4dENvbnRlbnQ9IjA6MDAiO2co"
    "ImRwYmFyIikuY2xhc3NMaXN0LmFkZCgibG93Iik7ZygiZHBiYXIiKS5maXJzdENoaWxkLnN0eWxl"
    "LndpZHRoPSIwJSI7CiAgICBwYXlCLmRpc2FibGVkPXRydWU7dmVyQi5kaXNhYmxlZD10cnVlO3V0"
    "ckIuZGlzYWJsZWQ9dHJ1ZTsKICAgIGcoInMyIikuY2xhc3NOYW1lPSJzdGVwIjtnKCJzMyIpLmNs"
    "YXNzTmFtZT0ic3RlcCI7CiAgICB2ZXJCLnRleHRDb250ZW50PSJFeHBpcmVkIjtwYXlCLmNsYXNz"
    "TmFtZT0iYnRuIjtwYXlCLmRpc2FibGVkPWZhbHNlO3BheUIudGV4dENvbnRlbnQ9Ik5ldyBSZXF1"
    "ZXN0IjsKICAgIHBheUIub25jbGljaz0oKT0+ZGVwb3NpdFNoZWV0KGEpOwogICAgdG9hc3QoQ01T"
    "Lm1zZ0V4cGlyZWQpOwogIH0KICBkZXBUaWNrPXNldEludGVydmFsKCgpPT57CiAgICBpZighYWxp"
    "dmUoKSl7Y2xlYXJJbnRlcnZhbChkZXBUaWNrKTtyZXR1cm47fQogICAgbGVmdC0tO2coImRwY2Qi"
    "KS50ZXh0Q29udGVudD1mbXQoTWF0aC5tYXgoMCxsZWZ0KSk7CiAgICBnKCJkcGJhciIpLmZpcnN0"
    "Q2hpbGQuc3R5bGUud2lkdGg9KE1hdGgubWF4KDAsbGVmdCkvdG90YWwqMTAwKSsiJSI7CiAgICBn"
    "KCJkcGJhciIpLmNsYXNzTGlzdC50b2dnbGUoImxvdyIsbGVmdDw9NjApOwogICAgaWYobGVmdDw9"
    "MClleHBpcmUoKTsKICB9LDEwMDApOwogIGlmKHBheUIpcGF5Qi5vbmNsaWNrPSgpPT57CiAgICBp"
    "Zihkb25lKXJldHVybjsKICAgIHBhaWQ9dHJ1ZTtnKCJzMiIpLmNsYXNzTmFtZT0ic3RlcCBvbiI7"
    "ZygiczMiKS5jbGFzc05hbWU9InN0ZXAgcnVuIjsKICAgIHBheUIudGV4dENvbnRlbnQ9IlBheSBB"
    "Z2FpbiI7dmVyQi5jbGFzc0xpc3QucmVtb3ZlKCJnbGFzcyIpO3ZlckIuY2xhc3NMaXN0LmFkZCgi"
    "Y3RhIik7CiAgICB0b2FzdCgiT3BlbmluZyB5b3VyIFVQSSBhcHAg4oCUIHBheSAiK21vbmV5KGEp"
    "KyIsIHRoZW4gdGFwIFZlcmlmeSIpOwogIH07CiAgdmVyQi5vbmNsaWNrPSgpPT57CiAgICBpZihk"
    "b25lfHxidXN5fHxsZWZ0PD0wKXJldHVybjsKICAgIGJ1c3k9dHJ1ZTt2ZXJCLmRpc2FibGVkPXRy"
    "dWU7dmVyQi50ZXh0Q29udGVudD0iQ2hlY2tpbmfigKYiOwogICAgZygiczMiKS5jbGFzc05hbWU9"
    "InN0ZXAgcnVuIjt0b2FzdChDTVMubXNnQ2hlY2tpbmcpOwogICAgZGVwSm9iPXNldFRpbWVvdXQo"
    "KCk9PnsKICAgICAgYnVzeT1mYWxzZTsKICAgICAgaWYoIWFsaXZlKCkpcmV0dXJuOwogICAgICBp"
    "ZihwYWlkKXsKICAgICAgICBkb25lPXRydWU7Y2xlYXJJbnRlcnZhbChkZXBUaWNrKTsKICAgICAg"
    "ICBnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCBvbiI7ZygiczQiKS5jbGFzc05hbWU9InN0ZXAgb24i"
    "OwogICAgICAgIHZlckIudGV4dENvbnRlbnQ9IlZlcmlmaWVkIjsKICAgICAgICBCQUwrPU51bWJl"
    "cihhKTtzeW5jQmFsKCk7YnVyc3QoKTt0b2FzdChDTVMubXNnT2spOwogICAgICAgIHNldFRpbWVv"
    "dXQoY2xvc2VTaGVldCwxMjAwKTsKICAgICAgfWVsc2V7CiAgICAgICAgZygiczMiKS5jbGFzc05h"
    "bWU9InN0ZXAiO3ZlckIuZGlzYWJsZWQ9ZmFsc2U7dmVyQi50ZXh0Q29udGVudD1DTVMudmVyaWZ5"
    "QnRuOwogICAgICAgIHRvYXN0KENNUy5tc2dOb25lKTsKICAgICAgfQogICAgfSwxNzAwKTsKICB9"
    "OwogIHV0ckIub25jbGljaz0oKT0+e2lmKGxlZnQ+MCl0b2FzdCgiVGhhbmtzIOKAlCB3ZSBhcmUg"
    "Y2hlY2tpbmcgeW91ciBwYXltZW50Iik7fTsKfQpmdW5jdGlvbiBzeW5jQmFsKCl7dGlja1RvKGRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJiYWwiKSxCQUwpO3RpY2tUbyhkb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiYmFsMiIpLEJBTCk7fQpkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCJjbGljayIs"
    "ZT0+e2NvbnN0IGM9ZS50YXJnZXQuY2xvc2VzdCgiW2RhdGEtY29uZmlybV0iKTtpZighYylyZXR1"
    "cm47CiAgY29uc3QgcD0rYy5kYXRhc2V0LmNvbmZpcm07aWYoQkFMPHApe3RvYXN0KCJOb3QgZW5v"
    "dWdoIGJhbGFuY2Ug4oCUIGFkZCBmdW5kcyIpO3JldHVybn0KICBCQUwtPXA7c3luY0JhbCgpO2Ns"
    "b3NlU2hlZXQoKTtidXJzdCgpO3RvYXN0KCJQdXJjaGFzZWQg4oCUIE9UUCBpcyBpbiBPcmRlcnMi"
    "KTt9KTsKCi8qIOKUgOKUgCBBRE1JTiDilIDilIAgKi8KbGV0IEFUQUI9ImRhc2giOwpkb2N1bWVu"
    "dC5xdWVyeVNlbGVjdG9yQWxsKCIjYXN1YiBidXR0b24iKS5mb3JFYWNoKGI9PmIub25jbGljaz0o"
    "KT0+ewogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNhc3ViIGJ1dHRvbiIpLmZvckVhY2go"
    "eD0+eC5jbGFzc0xpc3QucmVtb3ZlKCJvbiIpKTtiLmNsYXNzTGlzdC5hZGQoIm9uIik7CiAgQVRB"
    "Qj1iLmRhdGFzZXQuYTtyZW5kZXJBZG1pbigpO3dpbmRvdy5zY3JvbGxUbygwLDApO30pOwpmdW5j"
    "dGlvbiByZW5kZXJBZG1pbigpewogIGNvbnN0IHN0PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJh"
    "c3RhZ2UiKTsKICBpZighYWRtaW5BbGxvd2VkKCkpe3N0LmlubmVySFRNTD0nPGRpdiBjbGFzcz0i"
    "Y2FyZCIgc3R5bGU9Im1hcmdpbjoxNnB4O3RleHQtYWxpZ246Y2VudGVyIj4nCiAgICArJzxoMyBz"
    "dHlsZT0ibWFyZ2luOjAgMCA2cHgiPkFkbWlucyBvbmx5PC9oMz4nCiAgICArJzxwIGNsYXNzPSJz"
    "dWIiIHN0eWxlPSJtYXJnaW46MCI+VGhpcyBwYW5lbCBpcyBub3QgYXZhaWxhYmxlIG9uIHlvdXIg"
    "YWNjb3VudC48L3A+PC9kaXY+JztyZXR1cm47fQogIHN0LmlubmVySFRNTD0oe2Rhc2g6YURhc2gs"
    "Y2hhbm5lbHM6YUNoYW5uZWxzLGxvZ3M6YUxvZ3MscGF5OmFQYXksY29udGVudDphQ29udGVudCxm"
    "ZWF0dXJlczphRmVhdHVyZXMsbWFya2V0OmFNYXJrZXQsYW5hbHl0aWNzOmFBbmFseXRpY3Msc2Vj"
    "dXJpdHk6YVNlY3VyaXR5LHN0b2NrOmFTdG9jayxvcmRlcnM6YU9yZGVycyx1c2VyczphVXNlcnMs"
    "c2V0dGluZ3M6YVNldHRpbmdzfSlbQVRBQl0oKTsKICBiaW5kQWRtaW4oKTtyZXZlYWxTdGFnZShz"
    "dCk7YmluZFRpbHQoKTtpZih3aW5kb3cuX192dGFiUGxhY2UpcmVxdWVzdEFuaW1hdGlvbkZyYW1l"
    "KHdpbmRvdy5fX3Z0YWJQbGFjZSk7fQoKZnVuY3Rpb24gYURhc2goKXsKICByZXR1cm4gYDxkaXYg"
    "Y2xhc3M9ImtwaXMiPiR7W1siUmV2ZW51ZSB0b2RheSIsbW9uZXkoQURNLmtwaS5yZXYpXSxbIk9y"
    "ZGVycyB0b2RheSIsYWRtTnVtKEFETS5rcGkub3JkZXJzKV0sCiAgICAgIFsiVXNlcnMiLGFkbU51"
    "bShBRE0ua3BpLnVzZXJzKV0sWyJBdXRvLXZlcmlmaWVkIixhZG1OdW0oQURNLmtwaS5hdXRvKV1d"
    "CiAgICAubWFwKChbayx2XSk9PmA8ZGl2IGNsYXNzPSJrcGkiPjxzbWFsbD4ke2t9PC9zbWFsbD48"
    "Yj4ke3Z9PC9iPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJkIHRp"
    "Z2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAg"
    "ICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5QYXltZW50IGNoZWNr"
    "aW5nPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6"
    "Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPiR7QURNLnBheS5nbWFpbF9zZXQ/IkdtYWlsIHdhdGNo"
    "ZXIgb24gwrcgIitBRE0ucGF5LnBlbmRpbmcrIiBhd2FpdGluZyByZXZpZXciOiJHbWFpbCBub3Qg"
    "c2V0IOKAlCBkZXBvc2l0cyBnbyB0byBtYW51YWwgcmV2aWV3In08L2Rpdj4KICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1hLWdvPSJwYXkiPk9wZW48L2J1dHRvbj48L2Rpdj4K"
    "ICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxhc3QgNyBkYXlzPC9oMz48cCBjbGFzcz0ic3ViIj5S"
    "ZXZlbnVlIHRyZW5kPC9wPgogICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpmbGV4LWVuZDtnYXA6N3B4O2hlaWdodDo5NnB4O21hcmdpbi10b3A6MTRweCI+CiAgICAgICAk"
    "e0FETS5iYXJzLm1hcChoPT5gPGkgc3R5bGU9ImZsZXg6MTtoZWlnaHQ6JHtofSU7Ym9yZGVyLXJh"
    "ZGl1czo3cHggN3B4IDNweCAzcHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7ZGlzcGxheTpi"
    "bG9jayI+PC9pPmApLmpvaW4oIiIpfTwvZGl2PjwvZGl2PgogICAke0coImRpZ2VzdCIsYDxkaXYg"
    "Y2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVy"
    "O2dhcDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+PGkgY2xhc3M9ImRvdCI+PC9p"
    "PkRpZ2VzdDwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9udC1z"
    "aXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj5OZXh0IHN1bW1hcnkgYXQgMjM6MzA8L2Rpdj4K"
    "ICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1hLWdvPSJhbmFseXRpY3MiPlZp"
    "ZXc8L2J1dHRvbj48L2Rpdj5gKX0KICAgJHtHKCJzYW5kYm94IixgPGRpdiBjbGFzcz0iY2FyZCB0"
    "aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgog"
    "ICAgIDxzcGFuIGNsYXNzPSJwaWxsIj5TYW5kYm94PC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZs"
    "ZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPiR7QURN"
    "LnNldHRpbmdzLnNhbmRib3g/IlRlc3QgbW9kZSBpcyBPTiI6IlRlc3QgbW9kZSBpcyBvZmYg4oCU"
    "IHJlYWwgbW9uZXkgaXMgbGl2ZSJ9PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cm"
    "JkFETS5zdy5zYW5kYm94KT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0ic2FuZGJveCI+PC9kaXY+"
    "PC9kaXY+YCl9CiAgIDxkaXYgY2xhc3M9InNlYyI+PGgyPlF1aWNrIEFjdGlvbnM8L2gyPjwvZGl2"
    "PgogICA8ZGl2IGNsYXNzPSJncmlkIj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0"
    "YS1xYT0iYnJvYWRjYXN0Ij5Ccm9hZGNhc3Q8L2J1dHRvbj4KICAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4gc29mdCIgZGF0YS1xYT0ic3RvY2siPkFkZCBTdG9jazwvYnV0dG9uPgogICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBzb2Z0IiBkYXRhLXFhPSJmbGFzaCI+Rmxhc2ggU2FsZTwvYnV0dG9uPgogICAg"
    "IDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLXFhPSJiYWNrdXAiPkJhY2t1cCBEQjwvYnV0"
    "dG9uPjwvZGl2PmA7Cn0KCmZ1bmN0aW9uIGFDaGFubmVscygpewogIHJldHVybiBgPGRpdiBjbGFz"
    "cz0iY2FyZCI+PGgzPkZvcmNlLWpvaW4gY2hhbm5lbHM8L2gzPgogICAgPHAgY2xhc3M9InN1YiI+"
    "QWRkIG9yIHJlbW92ZSBhbnkgbnVtYmVyIG9mIGNoYW5uZWxzLiBTYXZlZCB0byB0aGUgZGF0YWJh"
    "c2Ug4oCUIGluc3RhbnQsIG5vIHJlc3RhcnQuPC9wPgogICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0"
    "eWxlPSJtYXJnaW4tdG9wOjEzcHgiPjxsYWJlbD5DaGFubmVsIGxpbmssIEB1c2VybmFtZSBvciAt"
    "MTAwIElEPC9sYWJlbD4KICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGlkPSJjaC1pbiIgcGxhY2Vo"
    "b2xkZXI9IkBjaGFubmVsIMK3IHQubWUvK2ludml0ZSDCtyAtMTAw4oCmIj48L2Rpdj4KICAgIDxk"
    "aXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+VHlwZTwvbGFiZWw+CiAgICAgIDxzZWxlY3QgY2xhc3M9"
    "ImlucCIgaWQ9ImNoLXR5cGUiPjxvcHRpb24gdmFsdWU9IjEiPlJlcXVpcmVkIOKAlCBtdXN0IGpv"
    "aW48L29wdGlvbj48b3B0aW9uIHZhbHVlPSIwIj5PcHRpb25hbCDigJQganVzdCBzdWdnZXN0ZWQ8"
    "L29wdGlvbj48L3NlbGVjdD48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+PGJ1dHRvbiBj"
    "bGFzcz0iYnRuIiBpZD0iY2gtYWRkIj4ke2ljKCJwbHVzIil9IEFkZCBDaGFubmVsPC9idXR0b24+"
    "CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtbWlzYz0iY2hhbm5lbHRlc3QiPlRl"
    "c3QgQm90PC9idXR0b24+PC9kaXY+PC9kaXY+CiAgPGRpdiBjbGFzcz0ic2VjIj48aDI+QWN0aXZl"
    "ICgke0NIQU5ORUxTLmxlbmd0aH0pPC9oMj48L2Rpdj4KICAke0NIQU5ORUxTLm1hcCgoYyxpKT0+"
    "YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiPgogICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDth"
    "bGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtp"
    "Yygic2VuZCIpfTwvZGl2PgogICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48"
    "YiBzdHlsZT0iZm9udC1zaXplOi44M3JlbTtkaXNwbGF5OmJsb2NrO3doaXRlLXNwYWNlOm5vd3Jh"
    "cDtvdmVyZmxvdzpoaWRkZW47dGV4dC1vdmVyZmxvdzplbGxpcHNpcyI+JHtlc2MoYy5uYW1lKX08"
    "L2I+CiAgICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5"
    "cmVtIj4ke2VzYyhjLmhhbmRsZSl9IMK3ICR7Yy5tZW1iZXJzfSBtZW1iZXJzPC9zbWFsbD48L2Rp"
    "dj4KICAgICAgIDxkaXYgY2xhc3M9InRnIHNtICR7Yy5vbj8ib24iOiIifSIgZGF0YS1jaG9uPSIk"
    "e2MuaWR9Ij48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjZw"
    "eDttYXJnaW4tdG9wOjEwcHg7ZmxleC13cmFwOndyYXAiPgogICAgICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIHhzICR7Yy5yZXE/IiI6InNvZnQifSIgZGF0YS1jaHJlcT0iJHtjLmlkfSI+JHtjLnJlcT8i"
    "UmVxdWlyZWQiOiJPcHRpb25hbCJ9PC9idXR0b24+CiAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "eHMgc29mdCIgZGF0YS1jb3B5PSJ0ZXh0IiBkYXRhLWNvcHktdmFsdWU9IiR7ZXNjKGMuaGFuZGxl"
    "KX0iPkNvcHk8L2J1dHRvbj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBiYWQiIHN0eWxl"
    "PSJtYXJnaW4tbGVmdDphdXRvIiBkYXRhLWNoZGVsPSIke2MuaWR9Ij5SZW1vdmU8L2J1dHRvbj48"
    "L2Rpdj48L2Rpdj5gKS5qb2luKCIiKQogICB8fCc8ZGl2IGNsYXNzPSJjYXJkIj48cCBjbGFzcz0i"
    "c3ViIj5ObyBjaGFubmVscyDigJQgdXNlcnMgY2FuIGVudGVyIGZyZWVseS48L3A+PC9kaXY+J31g"
    "Owp9CgpmdW5jdGlvbiBhTG9ncygpewogIGNvbnN0IG9wdHM9aWQ9PkxPR0NILm1hcChsPT5gPG9w"
    "dGlvbiB2YWx1ZT0iJHtsLmlkfSIgJHtsLmlkPT09aWQ/InNlbGVjdGVkIjoiIn0+JHtlc2MobC5u"
    "YW1lKX08L29wdGlvbj5gKS5qb2luKCIiKTsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxk"
    "aXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj4KICAgICAgPGRpdiBjbGFzcz0i"
    "dCI+TG9nZ2luZyBtYXN0ZXIgc3dpdGNoPHNtYWxsPk9mZiA9IHRoZSBib3QgcG9zdHMgbm8gbG9n"
    "cyBhbnl3aGVyZTwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InRnICR7TE9HU19NQVNU"
    "RVI/Im9uIjoiIn0iIGRhdGEtbWFzdGVyPjwvZGl2PjwvZGl2PjwvZGl2PgogICA8ZGl2IGNsYXNz"
    "PSJjYXJkIj48aDM+TG9nIGNoYW5uZWxzPC9oMz48cCBjbGFzcz0ic3ViIj5BZGQgYXMgbWFueSBh"
    "cyB5b3UgbGlrZS4gVGhlIHN0YXJyZWQgb25lIGlzIHRoZSBwcmVmZXJyZWQvZGVmYXVsdCBjaGFu"
    "bmVsLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+"
    "PGxhYmVsPkNoYW5uZWwgbGluaywgQHVzZXJuYW1lIG9yIC0xMDAgSUQ8L2xhYmVsPgogICAgICAg"
    "PGlucHV0IGNsYXNzPSJpbnAiIGlkPSJsZy1pbiIgcGxhY2Vob2xkZXI9IkBsb2dzX2NoYW5uZWwg"
    "wrcgLTEwMOKApiI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93Ij48YnV0dG9uIGNsYXNz"
    "PSJidG4iIGlkPSJsZy1hZGQiPiR7aWMoInBsdXMiKX0gQWRkIExvZyBDaGFubmVsPC9idXR0b24+"
    "CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtbWlzYz0ibG9ndGVzdCI+U2VuZCBU"
    "ZXN0PC9idXR0b24+PC9kaXY+PC9kaXY+CiAgICR7TE9HQ0gubWFwKGw9PmA8ZGl2IGNsYXNzPSJj"
    "YXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBw"
    "eCI+CiAgICAgIDxkaXYgY2xhc3M9InN0YXIgJHtsLnByaW1hcnk/Im9uIjoiIn0iIGRhdGEtbGdz"
    "dGFyPSIke2wuaWR9Ij4ke2ljKCJzdGFyIil9PC9kaXY+CiAgICAgIDxkaXYgc3R5bGU9ImZsZXg6"
    "MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODNyZW07ZGlzcGxheTpibG9jazt3"
    "aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXMi"
    "PiR7ZXNjKGwubmFtZSl9PC9iPgogICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgy"
    "KTtmb250LXNpemU6LjY5cmVtIj4ke2VzYyhsLmhhbmRsZSl9JHtsLnByaW1hcnk/IiDCtyBwcmVm"
    "ZXJyZWQiOiIifTwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InRnIHNtICR7bC5vbj8i"
    "b24iOiIifSIgZGF0YS1sZ29uPSIke2wuaWR9Ij48L2Rpdj4KICAgICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIHhzIGJhZCIgZGF0YS1sZ2RlbD0iJHtsLmlkfSI+UmVtb3ZlPC9idXR0b24+PC9kaXY+YCku"
    "am9pbigiIikKICAgIHx8JzxkaXYgY2xhc3M9ImNhcmQiPjxwIGNsYXNzPSJzdWIiPk5vIGxvZyBj"
    "aGFubmVscyB5ZXQuPC9wPjwvZGl2Pid9CiAgIDxkaXYgY2xhc3M9InNlYyI+PGgyPkV2ZW50cyAm"
    "YW1wOyByb3V0aW5nPC9oMj48YSBkYXRhLWV2LWFsbD5Ub2dnbGUgYWxsPC9hPjwvZGl2PgogICA8"
    "ZGl2IGNsYXNzPSJjYXJkIj4ke0VWRU5UUy5tYXAoKGUsaSk9PmAKICAgICA8ZGl2IGNsYXNzPSJz"
    "d2FwIj48ZGl2IGNsYXNzPSJ0IiBzdHlsZT0iZmxleDoxIj4ke2VzYyhlLmspfQogICAgICAgPHNt"
    "YWxsPjxzZWxlY3QgY2xhc3M9ImlucCIgc3R5bGU9ImhlaWdodDozMHB4O2ZvbnQtc2l6ZTouN3Jl"
    "bTttYXJnaW4tdG9wOjVweDttYXgtd2lkdGg6MTkwcHgiIGRhdGEtZXZjaD0iJHtpfSIgJHtlLm9u"
    "PyIiOiJkaXNhYmxlZCJ9PiR7b3B0cyhlLmNoKX08L3NlbGVjdD48L3NtYWxsPjwvZGl2PgogICAg"
    "ICAgPGRpdiBjbGFzcz0idGcgJHtlLm9uPyJvbiI6IiJ9IiBkYXRhLWV2b249IiR7aX0iPjwvZGl2"
    "PjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+UmVjZW50"
    "IGxvZyBvdXRwdXQ8L2gzPgogICAgICR7KChBRE0ubG9nZmVlZCYmQURNLmxvZ2ZlZWQubGVuZ3Ro"
    "KT9BRE0ubG9nZmVlZC5tYXAobD0+W2wud2hhdCxsLndoZXJlLGwud2hlbl0pOltdKQogICAgICAg"
    "Lm1hcCgoW3QsYyx3XSk9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHt0fTwvc3Bhbj48YiBzdHls"
    "ZT0iZm9udC1zaXplOi43cmVtIj4ke2N9IMK3ICR7d308L2I+PC9kaXY+YCkuam9pbigiIil9PC9k"
    "aXY+YDsKfQoKZnVuY3Rpb24gYVBheSgpewogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCI+PGRp"
    "diBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPgogICAgICA8ZGl2IGNsYXNzPSJ0"
    "Ij5HbWFpbCBhdXRvLXZlcmlmeTxzbWFsbD4ke0FETS5wYXkuZ21haWxfc2V0PyJSZWFkaW5nICIr"
    "ZXNjKEFETS5wYXkuZ21haWwpOiJOb3QgY29uZmlndXJlZCDigJQgZGVwb3NpdHMgcXVldWUgZm9y"
    "IHJldmlldyJ9PC9zbWFsbD48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFE"
    "TS5zdy5nbWFpbF9lbmFibGVkKT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0iZ21haWxfZW5hYmxl"
    "ZCI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+QXV0"
    "by1hcHByb3ZlIHNtYWxsIGRlcG9zaXRzPHNtYWxsPlNraXAgcmV2aWV3IGJlbG93IHRoZSB0aHJl"
    "c2hvbGQ8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cu"
    "Z21haWxfZW5hYmxlZCk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImdtYWlsX2VuYWJsZWQiPjwv"
    "ZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPk1hbnVhbCBV"
    "VFIgZmFsbGJhY2s8c21hbGw+TGV0IHVzZXJzIHN1Ym1pdCBhIFVUUiBpZiBhdXRvLXZlcmlmeSBt"
    "aXNzZXM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cu"
    "YXV0b2RlcCk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImF1dG9kZXAiPjwvZGl2PjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPlZlcmlmeSBvbmx5IHdoZW4gdXNl"
    "ciB0YXBzIFZlcmlmeTxzbWFsbD5ObyBiYWNrZ3JvdW5kIGNoZWNraW5nIOKAlCBzYXZlcyBHbWFp"
    "bCBxdW90YTwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5z"
    "dy51dHJfZmFsbGJhY2spPyIgb24iOiIifSIgZGF0YS1zZXR0aW5nPSJ1dHJfZmFsbGJhY2siPjwv"
    "ZGl2PjwvZGl2PjwvZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+VVBJPC9oMz48cCBjbGFz"
    "cz0ic3ViIj4ke0FETS5wYXkudXBpPyJJbiB1c2UgYnkgZXZlcnkgcGF5bWVudCByZXF1ZXN0LiI6"
    "Ik5vdCBzZXQgeWV0IOKAlCB0b3AtdXBzIGFyZSBkaXNhYmxlZCB1bnRpbCB5b3UgYWRkIGl0LiJ9"
    "PC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFi"
    "ZWw+VVBJIElEPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0idXBp"
    "X2lkIiB2YWx1ZT0iJHtlc2MoQURNLnBheS51cGl8fCIiKX0iIHBsYWNlaG9sZGVyPSJ5b3VyaGFu"
    "ZGxlQGJhbmsiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+UGF5ZWUgbmFt"
    "ZTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9InVwaV9uYW1lIiB2"
    "YWx1ZT0iJHtlc2MoQURNLnBheS5wYXllZXx8IiIpfSIgcGxhY2Vob2xkZXI9IllvdXIgU3RvcmUi"
    "PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+Tm90ZSBwcmVmaXg8L2xhYmVs"
    "PgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJwYXlfcHJlZml4IiB2YWx1ZT0i"
    "JHtlc2MoQURNLnBheS5wcmVmaXh8fCJQQVkiKX0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZp"
    "ZWxkIj48bGFiZWw+UGF5bWVudCB3aW5kb3cgKG1pbnV0ZXMpPC9sYWJlbD4KICAgICAgIDxpbnB1"
    "dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0icGF5X3dpbmRvdyIgdmFsdWU9IiR7QURNLnBheS53aW5k"
    "b3d9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPk1pbmltdW0gZGVwb3Np"
    "dDwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9Im1pbl9kZXBvc2l0"
    "IiB2YWx1ZT0iJHtBRE0ucGF5Lm1pbn0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48"
    "bGFiZWw+TWF4aW11bSBkZXBvc2l0PC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBk"
    "YXRhLXNldD0ibWF4X2RlcG9zaXQiIHZhbHVlPSIke0FETS5wYXkubWF4fSI+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5BdXRvLWFwcHJvdmUgdW5kZXIgKDAgPSBvZmYpPC9s"
    "YWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0iYXV0b2RlcF9tYXgiIHZh"
    "bHVlPSIke0FETS5wYXkuYXV0b2RlcH0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48"
    "bGFiZWw+R21haWwgYWRkcmVzczwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0"
    "YS1zZXQ9ImdtYWlsX2lkIiB2YWx1ZT0iJHtlc2MoQURNLnBheS5nbWFpbHx8IiIpfSIgcGxhY2Vo"
    "b2xkZXI9InN0b3JlQGdtYWlsLmNvbSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxs"
    "YWJlbD5HbWFpbCBhcHAgcGFzc3dvcmQ8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAi"
    "IGRhdGEtc2V0PSJnbWFpbF9wYXNzIiB0eXBlPSJwYXNzd29yZCIgcGxhY2Vob2xkZXI9IiR7QURN"
    "LnBheS5nbWFpbF9zZXQ/InNhdmVkIOKAlCB0eXBlIHRvIHJlcGxhY2UiOiJub3Qgc2V0In0iPjwv"
    "ZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5XYWl0aW5nIGZvciByZXZpZXc8L3NwYW4+"
    "PGI+JHtBRE0ucGF5LnBlbmRpbmd9PC9iPjwvZGl2PjwvZGl2PgogICAke0coImNyeXB0byIsYDxk"
    "aXYgY2xhc3M9ImNhcmQiPjxoMz5DcnlwdG8gd2FsbGV0PC9oMz48cCBjbGFzcz0ic3ViIj5VU0RU"
    "LVRSQzIwIGRlcG9zaXRzLCBhdXRvLWNvbmZpcm1lZCBvbi1jaGFpbi48L3A+CiAgICAgPGRpdiBj"
    "bGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxsYWJlbD5XYWxsZXQgYWRkcmVz"
    "czwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHBsYWNlaG9sZGVyPSJU4oCmIj48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkNvbmZpcm1hdGlvbnMgcmVxdWlyZWQ8L2xhYmVs"
    "PjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMSI+PC9kaXY+CiAgICAgPC9kaXY+YCl9CiAgICR7"
    "RygidXBpUm90YXRlIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlVQSSByb3RhdGlvbjwvaDM+PHAg"
    "Y2xhc3M9InN1YiI+U3ByZWFkIGxvYWQgYW5kIHN1cnZpdmUgYSBmcm96ZW4gaGFuZGxlLjwvcD4K"
    "ICAgICAke1tbImhhbmRsZTFAZmFtIiwiQWN0aXZlIl0sWyJoYW5kbGUyQGZhbSIsIkFjdGl2ZSJd"
    "LFsiaGFuZGxlM0B1cGkiLCJQYXVzZWQiXV0KICAgICAgIC5tYXAoKFtoLHN0XSk9PmA8ZGl2IGNs"
    "YXNzPSJrdiI+PHNwYW4+JHtofTwvc3Bhbj48Yj48c3BhbiBjbGFzcz0icGlsbCAke3N0PT09IkFj"
    "dGl2ZSI/Im9uIjoib2ZmIn0iPiR7c3R9PC9zcGFuPjwvYj48L2Rpdj5gKS5qb2luKCIiKX0KICAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+PGRpdiBzdHlsZT0i"
    "ZGlzcGxheTpmbGV4O2dhcDo3cHgiPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIHBsYWNlaG9s"
    "ZGVyPSJhbm90aGVyQHVwaSI+PGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBzdHlsZT0iZmxleDowIDAg"
    "YXV0byIgaWQ9InVwaWFkZCI+QWRkPC9idXR0b24+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7"
    "RygiZGVwQm9udXMiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+RGVwb3NpdCBib251czwvaDM+CiAg"
    "ICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPuKCuTUw"
    "MCBhbmQgYWJvdmUgKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjUiPjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+4oK5MTAwMCBhbmQgYWJvdmUgKCUpPC9s"
    "YWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjgiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "ImZpZWxkIj48bGFiZWw+4oK5MjAwMCBhbmQgYWJvdmUgKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9"
    "ImlucCIgdmFsdWU9IjEyIj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJkZXBMaW1pdHMiLGA8ZGl2"
    "IGNsYXNzPSJjYXJkIj48aDM+RGVwb3NpdCBsaW1pdHM8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZp"
    "ZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5NaW5pbXVtICjigrkpPC9sYWJlbD48"
    "aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCI+PGxhYmVsPk1heGltdW0gKOKCuSk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0i"
    "MjAwMDAiPjwvZGl2PjwvZGl2PmApfQogICAke0coIndyb25nQW10IixgPGRpdiBjbGFzcz0iY2Fy"
    "ZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQi"
    "Pldyb25nLWFtb3VudCBoYW5kbGluZzxzbWFsbD5DcmVkaXQgd2hhdCB3YXMgYWN0dWFsbHkgcGFp"
    "ZCBhbmQgZmxhZyBpdDwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cm"
    "JkFETS5zdy52ZXJpZnlfb25fdGFwKT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0idmVyaWZ5X29u"
    "X3RhcCI+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7Rygic3BsaXRQYXkiLGA8ZGl2IGNsYXNz"
    "PSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFz"
    "cz0idCI+U3BsaXQgcGF5bWVudDxzbWFsbD5XYWxsZXQgYmFsYW5jZSBwbHVzIFVQSSBpbiBvbmUg"
    "Y2hlY2tvdXQ8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULndyb25nQW10"
    "PyIgb24iOiIifSIgZGF0YS1mZWF0PSJ3cm9uZ0FtdCI+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAg"
    "ICR7RygiYXV0b1JlZnVuZCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5BdXRvLXJlZnVuZDwvaDM+"
    "CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPldh"
    "cnJhbnR5IHdpbmRvdyAobWludXRlcyk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0i"
    "MTAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5SZWZ1bmRlZCB0b2RheTwvc3Bh"
    "bj48Yj7igrkxMDUuMDAgwrcgMyBvcmRlcnM8L2I+PC9kaXY+PC9kaXY+YCl9CiAgIDxkaXYgY2xh"
    "c3M9ImNhcmQiPjxoMz5HbWFpbCBhY2NvdW50PC9oMz48cCBjbGFzcz0ic3ViIj5UaGUgR21haWwg"
    "dGhhdCByZWNlaXZlcyB5b3VyIEZhbVBheSBwYXltZW50IG1haWxzLiBOb3RoaW5nIGlzIHN0b3Jl"
    "ZCBpbiBjb2RlLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTJweCI+PGxhYmVsPkdtYWlsIElEPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgcGxhY2Vob2xk"
    "ZXI9InlvdXJzdG9yZUBnbWFpbC5jb20iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48"
    "bGFiZWw+R21haWwgcGFzc3dvcmQ8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB0eXBlPSJwYXNz"
    "d29yZCIgcGxhY2Vob2xkZXI9IuKAouKAouKAouKAouKAouKAouKAouKAouKAouKAouKAouKAoiI+"
    "PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93Ij48YnV0dG9uIGNsYXNzPSJidG4iIGRhdGEt"
    "YWRtaW4tYWN0PSJnbWFpbHRlc3QiPlRlc3QgR21haWw8L2J1dHRvbj4KICAgICA8L2Rpdj4KICAg"
    "ICA8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4Ij5UaXA6IGlmIDItc3RlcCB2"
    "ZXJpZmljYXRpb24gaXMgb24sIHBhc3RlIGEgR29vZ2xlIEFwcCBQYXNzd29yZCBoZXJlIGluc3Rl"
    "YWQgb2YgeW91ciBub3JtYWwgb25lLjwvcD48L2Rpdj4KICAgPGRpdiBjbGFzcz0ic2VjIj48aDI+"
    "UmVjZW50IGF1dG8tdmVyaWZpY2F0aW9uczwvaDI+PC9kaXY+CiAgICR7KEFETS5kZXBvc2l0c3x8"
    "W10pLmxlbmd0aD9BRE0uZGVwb3NpdHMubWFwKGQ9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBz"
    "dHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgICA8"
    "ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJ3YWxsZXQiKX08L2Rpdj4KICAgICAgIDxkaXYgc3R5bGU9"
    "ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODJyZW0iPiR7bW9uZXko"
    "ZC5hbXQpfSDCtyAke2VzYyhkLnVzZXIpfTwvYj48YnI+CiAgICAgICAgIDxzbWFsbCBzdHlsZT0i"
    "Y29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4ke2VzYyhkLnV0cnx8Im5vIHJlZmVy"
    "ZW5jZSB5ZXQiKX08L3NtYWxsPjwvZGl2PgogICAgICAgPHNwYW4gY2xhc3M9InBpbGwgJHtkLnN0"
    "PT09IkNvbXBsZXRlZCI/Im9uIjoiaG90In0iPiR7ZXNjKGQuc3QpfTwvc3Bhbj4KICAgICAgICR7"
    "ZC5zdD09PSJDb21wbGV0ZWQiPyIiOmA8YnV0dG9uIGNsYXNzPSJidG4geHMiIGRhdGEtZGVwLW9r"
    "PSIke2QuaWR9Ij5BcHByb3ZlPC9idXR0b24+YH08L2Rpdj5gKS5qb2luKCIiKQogICAgIDonPGRp"
    "diBjbGFzcz0iY2FyZCB0aWdodCI+PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBk"
    "ZXBvc2l0cyB5ZXQuPC9wPjwvZGl2Pid9YDsKfQoKZnVuY3Rpb24gYUNvbnRlbnQoKXsKICByZXR1"
    "cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5FZGl0IGFwcCB0ZXh0PC9oMz4KICAgICA8cCBjbGFz"
    "cz0ic3ViIj5FdmVyeSB3b3JkIHVzZXJzIHNlZSDigJQgY2hhbmdlIGl0IGhlcmUgYW5kIHRoZSBz"
    "dG9yZSB1cGRhdGVzIGluc3RhbnRseS48L3A+CiAgICAgPGRpdiBjbGFzcz0ibXNlYXJjaCIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTJweCI+PGRpdiBjbGFzcz0iZmllbGR3cmFwIj4ke2ljKCJzZWFyY2gi"
    "KX08aW5wdXQgaWQ9ImNxIiBwbGFjZWhvbGRlcj0iRmluZCB0ZXh0IHRvIGVkaXTigKYiPjwvZGl2"
    "PgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIHNvZnQiIGlkPSJjbXNyZXNldCI+UmVzZXQ8"
    "L2J1dHRvbj48L2Rpdj4KICAgICA8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4"
    "Ij5Vc2UgJmx0O2JyJmd0OyBmb3IgYSBsaW5lIGJyZWFrLCAmbHQ7YiZndDt3b3JkJmx0Oy9iJmd0"
    "OyBmb3IgYm9sZC4ge259ID0gbGl2ZSBjb3VudHJ5IGNvdW50LCB7bX0gPSBwYXltZW50IG1pbnV0"
    "ZXMuPC9wPjwvZGl2PgogICAke0NNU01FVEEubWFwKChbZ3JwLHJvd3NdKT0+YDxkaXYgY2xhc3M9"
    "ImNhcmQgY21zZ3JwIj48aDM+JHtncnB9PC9oMz4KICAgICAke3Jvd3MubWFwKChbayxsYmxdKT0+"
    "YDxkaXYgY2xhc3M9ImZpZWxkIGNtc3JvdyIgZGF0YS1mPSIke2VzYygobGJsKyIgIitDTVNba10r"
    "IiAiK2dycCkudG9Mb3dlckNhc2UoKSl9Ij4KICAgICAgICA8bGFiZWw+JHtsYmx9PC9sYWJlbD4K"
    "ICAgICAgICA8dGV4dGFyZWEgY2xhc3M9ImlucCIgcm93cz0iJHtDTVNba10ubGVuZ3RoPjQwPzI6"
    "MX0iIGRhdGEtY21zPSIke2t9Ij4ke2VzYyhDTVNba10pfTwvdGV4dGFyZWE+PC9kaXY+YCkuam9p"
    "bigiIil9CiAgIDwvZGl2PmApLmpvaW4oIiIpfQogICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBz"
    "dHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNw"
    "YW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5MaXZlIGVkaXQ8L3NwYW4+CiAg"
    "ICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6"
    "dmFyKC0tdHgyKSI+Q2hhbmdlcyBhcHBseSB0byB0aGUgU3RvcmUgdGFiIHN0cmFpZ2h0IGF3YXk8"
    "L2Rpdj4KICAgICA8L2Rpdj5gOwp9CgpmdW5jdGlvbiBhRmVhdHVyZXMoKXsKICBjb25zdCBvbj1P"
    "YmplY3QudmFsdWVzKEZFQVQpLmZpbHRlcihCb29sZWFuKS5sZW5ndGgsdG90PU9iamVjdC5rZXlz"
    "KEZFQVQpLmxlbmd0aDsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5GZWF0dXJlIHN3"
    "aXRjaGVzPC9oMz4KICAgICA8cCBjbGFzcz0ic3ViIj5UdXJuIGFueXRoaW5nIG9mZiBhbmQgaXQg"
    "ZGlzYXBwZWFycyBmcm9tIHRoZSBhcHAgY29tcGxldGVseSDigJQgbm8gYnV0dG9ucywgbm8gbWVu"
    "dXMsIG5vIGxlZnRvdmVycy48L3A+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkVuYWJsZWQ8"
    "L3NwYW4+PGIgaWQ9ImZjb3VudCI+JHtvbn0gb2YgJHt0b3R9PC9iPjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9Im1zZWFyY2giIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxkaXYgY2xhc3M9ImZpZWxk"
    "d3JhcCI+JHtpYygic2VhcmNoIil9PGlucHV0IGlkPSJmcSIgcGxhY2Vob2xkZXI9IlNlYXJjaCBm"
    "ZWF0dXJlc+KApiI+PC9kaXY+CiAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20gc29mdCIgaWQ9"
    "ImZhbGwiPkFsbCBvbjwvYnV0dG9uPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5"
    "bGU9Im1hcmdpbi10b3A6OHB4Ij48YnV0dG9uIGNsYXNzPSJidG4gc20gZ2hvc3QiIGlkPSJmbm9u"
    "ZSI+QWxsIG9mZjwvYnV0dG9uPgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIHNvZnQiIGlk"
    "PSJmcmVzZXQiPlJlc2V0PC9idXR0b24+PC9kaXY+PC9kaXY+CiAgICR7RkdST1VQUy5tYXAoKFtn"
    "cnAscm93c10pPT5gPGRpdiBjbGFzcz0iY2FyZCBmZ3JwIj48aDM+JHtncnB9PC9oMz4KICAgICAk"
    "e3Jvd3MubWFwKChbayxsLGRdKT0+YDxkaXYgY2xhc3M9ImZjYXJkIGZyb3ciIGRhdGEtZj0iJHtl"
    "c2MoKGwrIiAiK2QrIiAiK2dycCkudG9Mb3dlckNhc2UoKSl9Ij4KICAgICAgICA8ZGl2IGNsYXNz"
    "PSJ0Ij48Yj4ke2x9PC9iPjxzbWFsbD4ke2R9PC9zbWFsbD48L2Rpdj4KICAgICAgICA8ZGl2IGNs"
    "YXNzPSJ0ZyAke0ZFQVRba10/Im9uIjoiIn0iIGRhdGEtZmVhdD0iJHtrfSI+PC9kaXY+PC9kaXY+"
    "YCkuam9pbigiIil9CiAgIDwvZGl2PmApLmpvaW4oIiIpfQogICA8ZGl2IGNsYXNzPSJjYXJkIHRp"
    "Z2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAg"
    "ICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5MaXZlPC9zcGFuPgog"
    "ICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9y"
    "OnZhcigtLXR4MikiPlN3aXRjaGVzIGFwcGx5IHRvIHRoZSBTdG9yZSB0YWIgaW5zdGFudGx5PC9k"
    "aXY+CiAgICAgPC9kaXY+YDsKfQoKZnVuY3Rpb24gYU1hcmtldCgpewogIHJldHVybiBgJHtHKCJj"
    "b3Vwb25zIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkNvdXBvbnM8L2gzPjxwIGNsYXNzPSJzdWIi"
    "PlBlcmNlbnRhZ2Ugb2ZmIGF0IGNoZWNrb3V0LjwvcD4KICAgICAkeyhBRE0uY291cG9ucyYmQURN"
    "LmNvdXBvbnMubGVuZ3RoKT9BRE0uY291cG9ucy5tYXAoYz0+YDxkaXYgY2xhc3M9Imt2Ij4KICAg"
    "ICAgIDxzcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7ZXNjKGMuY29kZSl9PC9iPiDC"
    "tyAke2MucGVyY2VudH0lIMK3ICR7Yy51c2VkfS8ke2MubWF4fHwi4oieIn08L3NwYW4+CiAgICAg"
    "ICA8Yj48c3BhbiBjbGFzcz0icGlsbCAke2MuYWN0aXZlPyJvbiI6Im9mZiJ9Ij4ke2MuYWN0aXZl"
    "PyJBY3RpdmUiOiJPZmYifTwvc3Bhbj48L2I+PC9kaXY+YCkuam9pbigiIikKICAgICAgIDonPHAg"
    "Y2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBjb3Vwb25zIHlldC48L3A+J30KICAgICA8"
    "ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPk5ldyBjb2Rl"
    "PC9sYWJlbD4KICAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4Ij48aW5wdXQg"
    "Y2xhc3M9ImlucCIgaWQ9ImNwY29kZSIgcGxhY2Vob2xkZXI9Ik5FV1lFQVIiPgogICAgICAgPGlu"
    "cHV0IGNsYXNzPSJpbnAiIGlkPSJjcHBjdCIgcGxhY2Vob2xkZXI9IiUiIHN0eWxlPSJ3aWR0aDo2"
    "NnB4Ij4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8i"
    "IGlkPSJjcGFkZCI+QWRkPC9idXR0b24+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiZmxh"
    "c2hTYWxlIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkZsYXNoIHNhbGUgc2NoZWR1bGVyPC9oMz48"
    "cCBjbGFzcz0ic3ViIj5TdGFydHMsIGFubm91bmNlcyBhbmQgZW5kcyBieSBpdHNlbGYuPC9wPgog"
    "ICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+RGlz"
    "Y291bnQgKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjIwIj48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlN0YXJ0czwvbGFiZWw+PGlucHV0IGNsYXNzPSJp"
    "bnAiIHZhbHVlPSJUb2RheSAxODowMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxs"
    "YWJlbD5FbmRzPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IlRvZGF5IDIzOjAwIj48"
    "L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIGlkPSJmbGFzaGdvIj5TdGFydCBG"
    "bGFzaCBTYWxlPC9idXR0b24+PC9kaXY+YCl9CiAgICR7Rygic2NoZWRCcm9hZGNhc3QiLGA8ZGl2"
    "IGNsYXNzPSJjYXJkIj48aDM+U2NoZWR1bGVkIGJyb2FkY2FzdDwvaDM+PHAgY2xhc3M9InN1YiI+"
    "UXVldWUgaXQgYW5kIHRhcmdldCBhIHNlZ21lbnQuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxk"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+U2VnbWVudDwvbGFiZWw+CiAgICAgICA8"
    "c2VsZWN0IGNsYXNzPSJpbnAiPjxvcHRpb24+RXZlcnlvbmU8L29wdGlvbj48b3B0aW9uPkJ1eWVy"
    "cyBvbmx5PC9vcHRpb24+PG9wdGlvbj5OZXZlciBib3VnaHQ8L29wdGlvbj48b3B0aW9uPkJpZyBz"
    "cGVuZGVycyAo4oK5MTAwMCspPC9vcHRpb24+PG9wdGlvbj5JbmFjdGl2ZSAzMCBkYXlzPC9vcHRp"
    "b24+PC9zZWxlY3Q+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5TZW5kIGF0"
    "PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IlRvbW9ycm93IDEwOjAwIj48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPk1lc3NhZ2U8L2xhYmVsPjx0ZXh0YXJlYSBj"
    "bGFzcz0iaW5wIiByb3dzPSIyIiBwbGFjZWhvbGRlcj0iV3JpdGUgeW91ciBicm9hZGNhc3TigKYi"
    "PjwvdGV4dGFyZWE+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93Ij48YnV0dG9uIGNsYXNz"
    "PSJidG4iIGlkPSJiY2dvIj5TZW5kIEJyb2FkY2FzdDwvYnV0dG9uPgogICAgICAgPC9kaXY+PC9k"
    "aXY+YCl9CiAgICR7Rygid2luYmFjayIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3"
    "YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5XaW4tYmFjayBtZXNzYWdl"
    "czxzbWFsbD5BdXRvLURNIHVzZXJzIGluYWN0aXZlIGZvciA3IGFuZCAzMCBkYXlzPC9zbWFsbD48"
    "L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5zcGxpdFBheT8iIG9uIjoiIn0iIGRhdGEt"
    "ZmVhdD0ic3BsaXRQYXkiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFi"
    "ZWw+RGlzY291bnQgb2ZmZXJlZCAoJSk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0i"
    "MTAiPjwvZGl2PjwvZGl2PmApfQogICAke0coImFiYW5kb25SZW1pbmQiLGA8ZGl2IGNsYXNzPSJj"
    "YXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBw"
    "eCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5SZW1pbmRl"
    "cnM8L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTou"
    "NzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+NCB1bmZpbmlzaGVkIHBheW1lbnRzIG51ZGdlZCB0b2Rh"
    "eTwvZGl2PgogICAgIDwvZGl2PmApfQogICAke0coImxveWFsdHkiLGA8ZGl2IGNsYXNzPSJjYXJk"
    "Ij48aDM+TG95YWx0eSBwb2ludHM8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5Qb2ludHMgcGVyIOKCuTEwIHNwZW50PC9sYWJlbD48aW5w"
    "dXQgY2xhc3M9ImlucCIgdmFsdWU9IjEiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48"
    "bGFiZWw+UG9pbnRzIG5lZWRlZCBmb3Ig4oK5MTA8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2"
    "YWx1ZT0iMTAwIj48L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20iIHN0eWxlPSJmbGV4"
    "OjAgMCBhdXRvIiBpZD0id2luYmFja3NhdmUiPlNhdmU8L2J1dHRvbj48L2Rpdj5gKX0KICAgJHth"
    "bnlGKCJyZWZEYXNoIiwicmVmTDIiKT9gPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlJlZmVycmFsIHBh"
    "eW91dHM8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgi"
    "PjxsYWJlbD5MZXZlbCAxIGNvbW1pc3Npb24gKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIg"
    "dmFsdWU9IjUiPjwvZGl2PgogICAgICR7RygicmVmTDIiLGA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxh"
    "YmVsPkxldmVsIDIgY29tbWlzc2lvbiAoJSk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1"
    "ZT0iMSI+PC9kaXY+YCl9CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlBhaWQgb3V0IHRoaXMg"
    "bW9udGg8L3NwYW4+PGI+4oK5MywyNDA8L2I+PC9kaXY+PC9kaXY+YDoiIn0KICAgJHtHKCJzYWxl"
    "c0ZlZWQiLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGlu"
    "Zy10b3A6MCI+PGRpdiBjbGFzcz0idCI+QXV0by1wb3N0IHNhbGVzIHRvIGNoYW5uZWw8c21hbGw+"
    "JHtGRUVELmxlbmd0aD9lc2MoRkVFRFswXVsxXSsiIHNvbGQgwrcgIitGRUVEWzBdWzNdKToibm8g"
    "c2FsZXMgeWV0In08L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULndpbmJh"
    "Y2s/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9IndpbmJhY2siPjwvZGl2PjwvZGl2PjwvZGl2PmApfQog"
    "ICAke0coImxlYWRlcmJvYXJkIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxlYWRlcmJvYXJkIHBy"
    "aXplPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48"
    "bGFiZWw+V2Vla2x5IHByaXplICjigrkpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9"
    "IjUwMCI+PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBzdHlsZT0iZmxleDowIDAg"
    "YXV0byIgaWQ9InByaXplc2F2ZSI+U2F2ZTwvYnV0dG9uPjwvZGl2PmApfWA7Cn0KCmZ1bmN0aW9u"
    "IGFBbmFseXRpY3MoKXsKICBjb25zdCB3ZWVrPShBRE0uc2VyaWVzfHxbXSkucmVkdWNlKChhLGIp"
    "PT5hK2IsMCk7CiAgY29uc3QgYXZnPUFETS5rcGkub3JkZXJzP0FETS5rcGkucmV2L0FETS5rcGku"
    "b3JkZXJzOjA7CiAgcmV0dXJuIGAke0coInJldkNoYXJ0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgz"
    "PlJldmVudWU8L2gzPjxwIGNsYXNzPSJzdWIiPkxhc3QgNyBkYXlzIMK3ICR7bW9uZXkod2Vlayl9"
    "IHRvdGFsPC9wPgogICAgIDxkaXYgY2xhc3M9ImJhcnMiPiR7QURNLmJhcnMubWFwKGg9PmA8aSBz"
    "dHlsZT0iaGVpZ2h0OiR7aH0lIj48L2k+YCkuam9pbigiIil9PC9kaXY+CiAgICAgPGRpdiBjbGFz"
    "cz0ia3BpcyIgc3R5bGU9Im1hcmdpbi10b3A6MTRweCI+JHtbWyJUb2RheSIsbW9uZXkoQURNLmtw"
    "aS5yZXYpXSxbIlRoaXMgbW9udGgiLG1vbmV5KEFETS5tYXJrZXQubW9udGgpXSxbIkF2ZyBvcmRl"
    "ciIsbW9uZXkoYXZnKV1dCiAgICAgICAubWFwKChbayx2XSk9PmA8ZGl2IGNsYXNzPSJrcGkiPjxz"
    "bWFsbD4ke2t9PC9zbWFsbD48Yj4ke3Z9PC9iPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PjwvZGl2"
    "PmApfQogICAke0coImZ1bm5lbCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Db252ZXJzaW9uIGZ1"
    "bm5lbDwvaDM+PHAgY2xhc3M9InN1YiI+V2hlcmUgcGVvcGxlIGRyb3Agb2ZmPC9wPgogICAgIDxk"
    "aXYgY2xhc3M9ImZ1bm5lbCI+JHsoKCk9Pntjb25zdCBmPUFETS5hbmFseXRpY3MuZnVubmVsLHQ9"
    "TWF0aC5tYXgoMSxmLnVzZXJzKTsKICAgICAgIHJldHVybiBbWyJTdGFydGVkIGJvdCIsZi51c2Vy"
    "cywxMDBdLFsiRGVwb3NpdGVkIixmLmRlcCxNYXRoLnJvdW5kKGYuZGVwL3QqMTAwKV0sWyJCb3Vn"
    "aHQiLGYuYnV5LE1hdGgucm91bmQoZi5idXkvdCoxMDApXV0KICAgICAgIC5tYXAoKFtrLHYsd10p"
    "PT5gPGRpdj48aSBzdHlsZT0id2lkdGg6JHtNYXRoLm1heCg0LHcpfSUiPjwvaT48c3BhbiBzdHls"
    "ZT0id2hpdGUtc3BhY2U6bm93cmFwIj4ke2t9IMK3IDxiPiR7dn08L2I+PC9zcGFuPjwvZGl2PmAp"
    "LmpvaW4oIiIpO30pKCl9PC9kaXY+PC9kaXY+YCl9CiAgICR7RygicHJvZml0UmVwIixgPGRpdiBj"
    "bGFzcz0iY2FyZCI+PGgzPlBlci1jb3VudHJ5IHByb2ZpdDwvaDM+CiAgICAgJHtBRE0uYW5hbHl0"
    "aWNzLmNvdW50cnkubGVuZ3RoP0FETS5hbmFseXRpY3MuY291bnRyeS5tYXAoKFtjLG4sc3VtXSk9"
    "PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHtlc2MoYyl9IMK3ICR7bn0gc29sZDwvc3Bhbj48YiBz"
    "dHlsZT0iY29sb3I6dmFyKC0tb2spIj4ke21vbmV5KHN1bSl9PC9iPjwvZGl2PmApLmpvaW4oIiIp"
    "CiAgICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gc2FsZXMgeWV0Ljwv"
    "cD4nfQogICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayBzb2Z0IiBzdHlsZT0ibWFyZ2luLXRv"
    "cDoxMnB4IiBkYXRhLWFkbWluLWFjdD0iZXhwb3J0Ij5FeHBvcnQgQ1NWPC9idXR0b24+PC9kaXY+"
    "YCl9CiAgICR7RygiY2x2IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkN1c3RvbWVyIHZhbHVlPC9o"
    "Mz4KICAgICAke0FETS51c2Vycy5sZW5ndGg/QURNLnVzZXJzLnNsaWNlKDAsNSkubWFwKHU9PmA8"
    "ZGl2IGNsYXNzPSJrdiI+PHNwYW4+PGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHtlc2ModS5u"
    "YW1lKX08L2I+IMK3ICR7dS5vcmRlcnN9IG9yZGVyczwvc3Bhbj4KICAgICAgICAgPGI+JHttb25l"
    "eSh1LnNwZW50KX0gPHNwYW4gY2xhc3M9InBpbGwgJHt1LnNwZW50PjA/Im9uIjoiaG90In0iPiR7"
    "dS5zcGVudD4wPyJhY3RpdmUiOiJubyBzcGVuZCJ9PC9zcGFuPjwvYj48L2Rpdj5gKS5qb2luKCIi"
    "KQogICAgICAgOic8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIGN1c3RvbWVycyB5"
    "ZXQuPC9wPid9PC9kaXY+YCl9CiAgICR7RygiZGlnZXN0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRp"
    "diBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPkRhaWx5"
    "IGRpZ2VzdDxzbWFsbD5FbmQtb2YtZGF5IHN1bW1hcnkgdG8gdGhlIGFkbWluIGNoYXQ8L3NtYWxs"
    "PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULnNhbGVzRmVlZD8iIG9uIjoiIn0iIGRh"
    "dGEtZmVhdD0ic2FsZXNGZWVkIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+"
    "PGxhYmVsPlNlbmQgYXQ8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMjM6MzAiPjwv"
    "ZGl2PjwvZGl2PmApfWA7Cn0KCmZ1bmN0aW9uIGFTZWN1cml0eSgpewogIHJldHVybiBgJHtHKCJy"
    "YXRlTGltaXQiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+UmF0ZSBsaW1pdGluZzwvaDM+PHAgY2xh"
    "c3M9InN1YiI+U3RvcHMgb25lIHVzZXIgaGFtbWVyaW5nIHRoZSBib3QuPC9wPgogICAgIDxkaXYg"
    "Y2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4Ij48bGFiZWw+TWF4IGFjdGlvbnMg"
    "cGVyIG1pbnV0ZTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIyMCI+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5Db29sZG93biBhZnRlciBsaW1pdCAocyk8L2xh"
    "YmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iNjAiPjwvZGl2PjwvZGl2PmApfQogICAke0co"
    "Im11bHRpQWNjIixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBh"
    "ZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPk11bHRpLWFjY291bnQgZGV0ZWN0aW9uPHNtYWxs"
    "PkZsYWdzIHJlZmVycmFsIGZhcm1pbmcgcGF0dGVybnM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9InRnJHtGRUFULm11bHRpQWNjPyIgb24iOiIifSIgZGF0YS1mZWF0PSJtdWx0aUFjYyI+"
    "PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkZsYWdnZWQgdGhpcyB3ZWVr"
    "PC9zcGFuPjxiPjcgYWNjb3VudHM8L2I+PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJs"
    "b2NrIHNvZnQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiIGRhdGEtYS1nbz0ib3JkZXJzIj5SZXZp"
    "ZXcgRmxhZ2dlZDwvYnV0dG9uPjwvZGl2PmApfQogICAke0coInZlbG9jaXR5IixgPGRpdiBjbGFz"
    "cz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xh"
    "c3M9InQiPlB1cmNoYXNlIHZlbG9jaXR5IGFsZXJ0czxzbWFsbD5DYXRjaGVzIHN0b2xlbiB0b3At"
    "dXBzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC52ZWxvY2l0eT8iIG9u"
    "IjoiIn0iIGRhdGEtZmVhdD0idmVsb2NpdHkiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "ImZpZWxkIj48bGFiZWw+QWxlcnQgYWJvdmUgTiBidXlzIC8gMTAgbWluPC9sYWJlbD48aW5wdXQg"
    "Y2xhc3M9ImlucCIgdmFsdWU9IjUiPjwvZGl2PjwvZGl2PmApfQogICAke0coImFkbWluMmZhIixg"
    "PGRpdiBjbGFzcz0iY2FyZCI+PGgzPkFkbWluIDJGQTwvaDM+PHAgY2xhc3M9InN1YiI+UmVxdWly"
    "ZWQgYmVmb3JlIGJhbGFuY2UgZWRpdHMsIGJyb2FkY2FzdHMgYW5kIHdpcGVzLjwvcD4KICAgICA8"
    "ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+PGxhYmVsPk1ldGhvZDwv"
    "bGFiZWw+PHNlbGVjdCBjbGFzcz0iaW5wIj48b3B0aW9uPlBJTjwvb3B0aW9uPjxvcHRpb24+VE9U"
    "UCBhcHA8L29wdGlvbj48L3NlbGVjdD48L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMi"
    "IGRhdGEtZmVhdD0iYWRtaW4yZmEiPlNldCBVcDwvYnV0dG9uPjwvZGl2PmApfQogICAke0coImF1"
    "dG9CYW4iLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QXV0by1iYW4gcnVsZXM8L2gzPgogICAgIDxk"
    "aXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPkNoYXJnZWJhY2s8c21hbGw+QmFuIGluc3Rh"
    "bnRseTwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5i"
    "YW5fY2hhcmdlYmFjayk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImJhbl9jaGFyZ2ViYWNrIj48"
    "L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5GYWtlIFVU"
    "UiBzdWJtaXR0ZWQ8c21hbGw+QmFuIGFmdGVyIDIgYXR0ZW1wdHM8L3NtYWxsPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuYmFuX2Zha2VfdXRyKT8iIG9uIjoiIn0i"
    "IGRhdGEtc2V0dGluZz0iYmFuX2Zha2VfdXRyIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNz"
    "PSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5SZXBlYXRlZCBmYWlsZWQgdmVyaWZpY2F0aW9uPHNtYWxs"
    "PkJhbiBhZnRlciA1PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYm"
    "QURNLnN3LmJhbl9mYWlsZWRfdmVyaWZ5KT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0iYmFuX2Zh"
    "aWxlZF92ZXJpZnkiPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImJsYWNrbGlzdCIsYDxk"
    "aXYgY2xhc3M9ImNhcmQiPjxoMz5CbGFja2xpc3Q8L2gzPjxwIGNsYXNzPSJzdWIiPlVzZXIgSURz"
    "LCBwaG9uZSBudW1iZXJzIGFuZCBVUEkgcmVmZXJlbmNlcy48L3A+CiAgICAgPGRpdiBjbGFzcz0i"
    "ZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtn"
    "YXA6N3B4Ij4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0iYmwtaW4iIHBsYWNlaG9sZGVy"
    "PSJJRCwgcGhvbmUgb3IgVVBJIHJlZiI+PGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBzdHlsZT0iZmxl"
    "eDowIDAgYXV0byIgaWQ9ImJsYmxvY2siPkJsb2NrPC9idXR0b24+PC9kaXY+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPkJsb2NrZWQgZW50cmllczwvc3Bhbj48Yj40MjwvYj48L2Rp"
    "dj48L2Rpdj5gKX0KICAgJHtHKCJlbmNyeXB0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFz"
    "cz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPkVuY3J5cHQgc2Vz"
    "c2lvbnMgYXQgcmVzdDxzbWFsbD5BIGxlYWtlZCBkYXRhYmFzZSBmaWxlIHN0YXlzIHVzZWxlc3M8"
    "L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULmVuY3J5cHQ/IiBvbiI6IiJ9"
    "IiBkYXRhLWZlYXQ9ImVuY3J5cHQiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48"
    "c3Bhbj5LZXkgc3RhdHVzPC9zcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS1vaykiPkxvYWRlZCBm"
    "cm9tIGVudjwvYj48L2Rpdj48L2Rpdj5gKX1gOwp9CgpmdW5jdGlvbiBhU3RvY2soKXsKICByZXR1"
    "cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5VcGxvYWQgc3RvY2s8L2gzPjxwIGNsYXNzPSJzdWIi"
    "PlBhc3RlIHNlc3Npb25zIG9yIHVwbG9hZCBhIGZpbGUg4oCUIG9uZSBwZXIgbGluZS48L3A+Cjxk"
    "aXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+UGFzdGUgYWNj"
    "b3VudHMg4oCUIG9uZSBwZXIgbGluZTwvbGFiZWw+CiAgICAgIDx0ZXh0YXJlYSBjbGFzcz0iaW5w"
    "IiBpZD0ic3RwYXN0ZSIgcm93cz0iNCIKICAgICAgICBwbGFjZWhvbGRlcj0iSW5kaWEgfCArOTEg"
    "fCAzNSB8IHNlc3Npb24mIzEwO0tlbnlhIHwgKzI1NCB8IDc3IHwgc2Vzc2lvbiI+PC90ZXh0YXJl"
    "YT48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIg"
    "aWQ9InN0c2F2ZSI+SW1wb3J0PC9idXR0b24+CiAgICAgIDxsYWJlbCBjbGFzcz0iYnRuIHNvZnQi"
    "IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpj"
    "ZW50ZXI7Y3Vyc29yOnBvaW50ZXIiPgogICAgICAgIFVwbG9hZCAudHh0PGlucHV0IHR5cGU9ImZp"
    "bGUiIGlkPSJzdGZpbGUiIGFjY2VwdD0iLnR4dCwuY3N2IiBoaWRkZW4+PC9sYWJlbD48L2Rpdj4K"
    "ICAgIDxwIGNsYXNzPSJzdWIiIGlkPSJzdG1zZyIgc3R5bGU9Im1hcmdpbjo4cHggMCAwO2ZvbnQt"
    "c2l6ZTouNzJyZW0iPjwvcD4KICAgICR7RygiYnVsa1VwbG9hZCIsYDxkaXYgY2xhc3M9InN0ZXBz"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCBvbiI+PGk+"
    "MTwvaT4ke0FETS5hbmFseXRpY3Muc3RvY2tfdG90YWx9IGF2YWlsYWJsZSBub3c8L2Rpdj4KICAg"
    "ICAgPGRpdiBjbGFzcz0ic3RlcCBvbiI+PGk+MjwvaT4ke0FETS5hbmFseXRpY3Muc29sZF90b3Rh"
    "bH0gc29sZCBzbyBmYXI8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCI+PGk+MzwvaT5BZGQg"
    "bW9yZSBmcm9tIHRoZSBib3Q6IEFkbWluIOKGkiBTdG9jazwvZGl2PjwvZGl2PmApfQogICAgJHtH"
    "KCJkdXBEZXRlY3QiLGA8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjEwcHggMCAwO2ZvbnQt"
    "c2l6ZTouN3JlbSI+RHVwbGljYXRlIHBob25lcyBhbmQgc2Vzc2lvbnMgYXJlIHJlamVjdGVkIGF1"
    "dG9tYXRpY2FsbHkuPC9wPmApfQogICAgJHtHKCJoZWFsdGhDaGVjayIsYDxkaXYgY2xhc3M9InN3"
    "YXAiPjxkaXYgY2xhc3M9InQiPlZhbGlkYXRlIGJlZm9yZSBzZWxsaW5nPHNtYWxsPkNoZWNrIHRo"
    "ZSBzZXNzaW9uIGlzIGFsaXZlIGF0IHB1cmNoYXNlIHRpbWU8L3NtYWxsPjwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9InRnJHtGRUFULmhlYWx0aENoZWNrPyIgb24iOiIifSIgZGF0YS1mZWF0PSJoZWFs"
    "dGhDaGVjayI+PC9kaXY+PC9kaXY+YCl9PC9kaXY+CiAgICR7RygibG93U3RvY2siLGA8ZGl2IGNs"
    "YXNzPSJjYXJkIj48aDM+TG93LXN0b2NrIGFsZXJ0czwvaDM+PHAgY2xhc3M9InN1YiI+UGVyLWNv"
    "dW50cnkgdGhyZXNob2xkIOKAlCBwaW5nIHdoZW4gaXQgZHJvcHMgYmVsb3cuPC9wPgogICAgICR7"
    "KEFETS5zdG9jay5sZW5ndGg/QURNLnN0b2NrOlNUT0NLKS5zbGljZSgwLDYpLm1hcChzPT5gPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPiR7cy5mfSAke2VzYyhzLmMpfSDCtyAke3MucXR5fHwwfSBsZWZ0"
    "PC9zcGFuPgogICAgICAgPGI+PGlucHV0IGNsYXNzPSJpbnAiIHN0eWxlPSJoZWlnaHQ6MzBweDt3"
    "aWR0aDo2NHB4O3RleHQtYWxpZ246Y2VudGVyO2ZvbnQtc2l6ZTouNzRyZW0iIHZhbHVlPSIke3Mu"
    "bG93fHwxMH0iPjwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgfHwnPHAgY2xhc3M9InN1YiIg"
    "c3R5bGU9Im1hcmdpbjowIj5ObyBzdG9jayB5ZXQuPC9wPid9PC9kaXY+YCl9CiAgICR7Rygic2Vs"
    "bEhvdXJzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlNlbGxpbmcgaG91cnM8L2gzPjxwIGNsYXNz"
    "PSJzdWIiPkNvdW50cmllcyBoaWRlIHRoZW1zZWx2ZXMgb3V0c2lkZSB0aGUgd2luZG93LjwvcD4K"
    "ICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+T3Bl"
    "bjwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIwOTowMCI+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0iZmllbGQiPjxsYWJlbD5DbG9zZTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZh"
    "bHVlPSIwMjowMCI+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiZHluUHJpY2UiLGA8ZGl2IGNsYXNz"
    "PSJjYXJkIj48aDM+RHluYW1pYyBwcmljaW5nPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIiBz"
    "dHlsZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+UmFpc2UgcHJpY2Ugd2hlbiBzdG9j"
    "ayBpcyBsb3c8c21hbGw+KzEwJSB1bmRlciAyMCB1bml0czwvc21hbGw+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0idGcke0ZFQVQuZHluUHJpY2U/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9ImR5blByaWNl"
    "Ij48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5OaWdo"
    "dCBkaXNjb3VudDxzbWFsbD7iiJI1JSBiZXR3ZWVuIDAwOjAwIGFuZCAwNjowMDwvc21hbGw+PC9k"
    "aXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5uaWdodF9kaXNjb3VudCk/"
    "IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9Im5pZ2h0X2Rpc2NvdW50Ij48L2Rpdj48L2Rpdj48L2Rp"
    "dj5gKX0KICAgJHtHKCJidWxrRGlzYyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5CdWxrIGRpc2Nv"
    "dW50czwvaDM+CiAgICAgPHAgY2xhc3M9InN1YiI+UGVyY2VudCBvZmYgd2hlbiB0aGUgYnV5ZXIg"
    "dGFrZXMgc2V2ZXJhbCBhdCBvbmNlLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9"
    "Im1hcmdpbi10b3A6MTBweCI+PGxhYmVsPjUgb3IgbW9yZSAoJSk8L2xhYmVsPgogICAgICAgPGlu"
    "cHV0IGNsYXNzPSJpbnAiIGRhdGEtcHJpY2U9ImJ1bGtfNSIgdmFsdWU9IiR7QURNLnByaWNpbmc/"
    "QURNLnByaWNpbmcuYnVsa181OjV9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxh"
    "YmVsPjEwIG9yIG1vcmUgKCUpPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRh"
    "LXByaWNlPSJidWxrXzEwIiB2YWx1ZT0iJHtBRE0ucHJpY2luZz9BRE0ucHJpY2luZy5idWxrXzEw"
    "OjEwfSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD4yNSBvciBtb3JlICgl"
    "KTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1wcmljZT0iYnVsa18yNSIg"
    "dmFsdWU9IiR7QURNLnByaWNpbmc/QURNLnByaWNpbmcuYnVsa18yNToxNX0iPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RGVmYXVsdCBwcmljZSBmb3IgbmV3IHN0b2NrPC9s"
    "YWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXByaWNlPSJkZWZhdWx0X3ByaWNl"
    "IiB2YWx1ZT0iJHtBRE0ucHJpY2luZz9BRE0ucHJpY2luZy5kZWZhdWx0X3ByaWNlOjM1fSI+PC9k"
    "aXY+CiAgICAgPC9kaXY+YCl9CiAgICR7RygidmFyaWFudHMiLGA8ZGl2IGNsYXNzPSJjYXJkIj48"
    "aDM+VmFyaWFudHM8L2gzPjxwIGNsYXNzPSJzdWIiPlByaWNlZCBzZXBhcmF0ZWx5IG9uIGV2ZXJ5"
    "IGNvdW50cnkuPC9wPgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj4yRkEgZW5hYmxlZDwvc3Bh"
    "bj48Yj4r4oK5NTwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+V2l0aCBtYWls"
    "PC9zcGFuPjxiPivigrk4PC9iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5TcGFt"
    "LWZyZWUgZ3VhcmFudGVlPC9zcGFuPjxiPivigrk0PC9iPjwvZGl2PjwvZGl2PmApfQogICA8ZGl2"
    "IGNsYXNzPSJzZWMiPjxoMj5JbnZlbnRvcnk8L2gyPjxhIGNsYXNzPSJzdWIiPnRhcCBhIHByaWNl"
    "IHRvIGNoYW5nZSBpdDwvYT48L2Rpdj4KICAgJHtTVE9DSy5tYXAocz0+YDxkaXYgY2xhc3M9ImNh"
    "cmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4"
    "Ij4KICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke3MuZn08L2Rpdj4KICAgICA8ZGl2IHN0eWxlPSJm"
    "bGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjgzcmVtIj4ke3MuY308L2I+"
    "PGJyPgogICAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjly"
    "ZW0iPiR7cy5xdHl9IGF2YWlsYWJsZTwvc21hbGw+PC9kaXY+CiAgICAgPGlucHV0IGNsYXNzPSJp"
    "bnAiIGRhdGEtY3ByaWNlPSIke2VzYyhzLmMpfSIgdmFsdWU9IiR7cy5wcmljZX0iIHN0eWxlPSJo"
    "ZWlnaHQ6MzJweDt3aWR0aDo3OHB4O3RleHQtYWxpZ246Y2VudGVyO2ZvbnQtc2l6ZTouNzZyZW0i"
    "PgogICAgIAogICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBiYWQiIGRhdGEtbWlzYz0iY2xlYXJz"
    "dG9jayIgZGF0YS1taXNjLXZhbHVlPSIke2VzYyhzLmMpfSI+Q2xlYXI8L2J1dHRvbj48L2Rpdj5g"
    "KS5qb2luKCIiKX1gOwp9CmZ1bmN0aW9uIGFPcmRlcnMoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9"
    "InNlYyI+PGgyPlJlY2VudCBPcmRlcnM8L2gyPjxhIGRhdGEtYWRtaW4tYWN0PSJleHBvcnQiPkV4"
    "cG9ydDwvYT48L2Rpdj4KICAgJHtBRE0ub3JkZXJzLmxlbmd0aD9BRE0ub3JkZXJzLm1hcChvPT5g"
    "PGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpj"
    "ZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7by5mfTwvZGl2PjxkaXYg"
    "c3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODJyZW0iPiR7"
    "ZXNjKG8uaWQpfTwvYj48YnI+CiAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2Zv"
    "bnQtc2l6ZTouNjlyZW0iPiR7ZXNjKG8udXNlcil9IMK3ICR7bW9uZXkoby5wcmljZSl9IMK3ICR7"
    "ZXNjKG8ud2hlbil9PC9zbWFsbD48L2Rpdj4KICAgICA8c3BhbiBjbGFzcz0icGlsbCAke28uc3Q9"
    "PT0iRGVsaXZlcmVkIj8ib24iOiJvZmYifSI+JHtlc2Moby5zdCl9PC9zcGFuPjwvZGl2PmApLmpv"
    "aW4oIiIpCiAgICAgOic8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0Ij48cCBjbGFzcz0ic3ViIiBzdHls"
    "ZT0ibWFyZ2luOjAiPk5vIG9yZGVycyB5ZXQuPC9wPjwvZGl2Pid9CiAgICR7RygiZGlzcHV0ZSIs"
    "YDxkaXYgY2xhc3M9InNlYyI+PGgyPkRpc3B1dGVzPC9oMj48L2Rpdj4KICAgICAke0FETS5kaXNw"
    "dXRlcy5sZW5ndGg/QURNLmRpc3B1dGVzLm1hcChkPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIg"
    "c3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAg"
    "ICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJzaGllbGQiKX08L2Rpdj4KICAgICAgICAgPGRpdiBz"
    "dHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44cmVtIj4ke2Vz"
    "YyhkLmlkKX0gwrcgJHtlc2MoZC51c2VyKX08L2I+PGJyPjxzbWFsbCBzdHlsZT0iY29sb3I6dmFy"
    "KC0tdHgyKTtmb250LXNpemU6LjY4cmVtIj4ke2VzYyhkLnJlYXNvbil9PC9zbWFsbD48L2Rpdj4K"
    "ICAgICAgICAgPHNwYW4gY2xhc3M9InBpbGwgJHtkLnN0YXR1cz09PSJPcGVuIj8iaG90Ijoib24i"
    "fSI+JHtlc2MoZC5zdGF0dXMpfTwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgOic8ZGl2"
    "IGNsYXNzPSJjYXJkIHRpZ2h0Ij48cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIGRp"
    "c3B1dGVzLjwvcD48L2Rpdj4nfWApfQogICAke0coIndhcnJhbnR5IixgPGRpdiBjbGFzcz0ic2Vj"
    "Ij48aDI+UmVwbGFjZW1lbnQgY2xhaW1zPC9oMj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJjYXJk"
    "IHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+"
    "CiAgICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJzaGllbGQiKX08L2Rpdj4KICAgICAgIDxk"
    "aXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+"
    "MiBjbGFpbXMgaW5zaWRlIHdhcnJhbnR5PC9iPjxicj4KICAgICAgICAgPHNtYWxsIHN0eWxlPSJj"
    "b2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjhyZW0iPkF1dG8tcmVwbGFjZWQgZnJvbSBzdG9j"
    "azwvc21hbGw+PC9kaXY+CiAgICAgICA8L2Rpdj5gKX0KICAgCiAgICR7RygicmV2aWV3cyIsYDxk"
    "aXYgY2xhc3M9InNlYyI+PGgyPlJldmlld3M8L2gyPjwvZGl2PgogICAgICR7KEFETS5yZXZpZXdz"
    "Lmxlbmd0aD9BRE0ucmV2aWV3cy5tYXAocj0+W3IudXNlcixyLmMsci5zdGFycyxyLnRleHRdKTpb"
    "XSkKICAgICAgIC5tYXAoKFt1LGMscix0XSk9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHls"
    "ZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgICAgIDxk"
    "aXYgY2xhc3M9ImZsYWciPiR7aWMoInN0YXIiKX08L2Rpdj4KICAgICAgICAgPGRpdiBzdHlsZT0i"
    "ZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44cmVtIj4ke3V9IMK3ICR7"
    "Y30gwrcgJHtyfTwvYj48YnI+CiAgICAgICAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10"
    "eDIpO2ZvbnQtc2l6ZTouNjhyZW0iPiR7dH08L3NtYWxsPjwvZGl2PgogICAgICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1yZXY9IiR7ci5pZH0iPkhpZGU8L2J1dHRvbj48L2Rp"
    "dj5gKS5qb2luKCIiKX1gKX1gOwp9CmZ1bmN0aW9uIGFVc2VycygpewogIHJldHVybiBgPGRpdiBj"
    "bGFzcz0iZmllbGQiPjxpbnB1dCBjbGFzcz0iaW5wIiBwbGFjZWhvbGRlcj0iU2VhcmNoIGJ5IElE"
    "IG9yIHVzZXJuYW1lIj48L2Rpdj4KICAgJHtBRE0udXNlcnMubGVuZ3RoP0FETS51c2Vycy5tYXAo"
    "dT0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRl"
    "bXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygidXNlciIp"
    "fTwvZGl2PgogICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJm"
    "b250LXNpemU6LjgycmVtIj4ke2VzYyh1Lm5hbWUpfTwvYj48YnI+CiAgICAgICAgPHNtYWxsIHN0"
    "eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjlyZW0iPiR7bW9uZXkodS5zcGVudCl9"
    "IHNwZW50IMK3ICR7dS5vcmRlcnN9IG9yZGVycyDCtyAke21vbmV5KHUuYmFsYW5jZSl9IGJhbGFu"
    "Y2Uke3UuYmFubmVkPyIgwrcgYmFubmVkIjoiIn08L3NtYWxsPjwvZGl2PgogICAgICA8c3BhbiBj"
    "bGFzcz0icGlsbCAke3UuYmFubmVkPyJvZmYiOiJvbiJ9Ij4ke2VzYyh1LnRpZXIpfTwvc3Bhbj48"
    "L2Rpdj5gKS5qb2luKCIiKQogICAgIDonPGRpdiBjbGFzcz0iY2FyZCB0aWdodCI+PHAgY2xhc3M9"
    "InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyB1c2VycyB5ZXQuPC9wPjwvZGl2Pid9CiAgICR7Rygi"
    "YWRtaW5Sb2xlcyIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPkFkbWluIHJvbGVzPC9oMj48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJjYXJkIj4KICAgICAgICR7QURNLmFkbWlucy5sZW5ndGg/QURNLmFk"
    "bWlucy5tYXAoYT0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj48YiBzdHlsZT0iY29sb3I6dmFyKC0t"
    "dHgpIj4ke2VzYyhhLmxhYmVsKX08L2I+IMK3ICR7ZXNjKGEucGVybXMpfTwvc3Bhbj48Yj48c3Bh"
    "biBjbGFzcz0icGlsbCBvbiI+JHtlc2MoYS5yb2xlKX08L3NwYW4+PC9iPjwvZGl2PmApLmpvaW4o"
    "IiIpCiAgICAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5Pbmx5IHlvdSBz"
    "byBmYXIuPC9wPid9CiAgICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTBweCI+PGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo3cHgiPgogICAgICAgICA8aW5wdXQg"
    "Y2xhc3M9ImlucCIgcGxhY2Vob2xkZXI9IkB1c2VybmFtZSI+PHNlbGVjdCBjbGFzcz0iaW5wIiBz"
    "dHlsZT0id2lkdGg6YXV0byI+PG9wdGlvbj5NYW5hZ2VyPC9vcHRpb24+PG9wdGlvbj5TdXBwb3J0"
    "PC9vcHRpb24+PC9zZWxlY3Q+CiAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbSIgc3R5bGU9"
    "ImZsZXg6MCAwIGF1dG8iIGlkPSJhZG1pbmFkZCI+QWRkPC9idXR0b24+PC9kaXY+PC9kaXY+PC9k"
    "aXY+YCl9CiAgICR7RygiYnV5ZXJCYWRnZSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5WZXJpZmll"
    "ZCBidXllciBiYWRnZTwvaDM+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjhweCI+PGxhYmVsPk9yZGVycyByZXF1aXJlZDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAi"
    "IHZhbHVlPSI1Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VmVyaWZpZWQgdXNl"
    "cnM8L3NwYW4+PGI+ODQ8L2I+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygibXVsdGlBY2MiLGA8ZGl2"
    "IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRl"
    "cjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwgaG90Ij43IGZsYWdnZWQ8L3NwYW4+"
    "CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29s"
    "b3I6dmFyKC0tdHgyKSI+UG9zc2libGUgbXVsdGktYWNjb3VudHMgdGhpcyB3ZWVrPC9kaXY+CiAg"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtYS1nbz0ic2VjdXJpdHkiPlJldmll"
    "dzwvYnV0dG9uPjwvZGl2PmApfWA7Cn0KZnVuY3Rpb24gYVNldHRpbmdzKCl7CiAgcmV0dXJuIGA8"
    "ZGl2IGNsYXNzPSJjYXJkIj48aDM+U3RvcmU8L2gzPjxwIGNsYXNzPSJzdWIiPkV2ZXJ5dGhpbmcg"
    "bGl2ZXMgaW4gdGhlIGRhdGFiYXNlIOKAlCBubyBjb2RlIGVkaXRzLCBubyByZXN0YXJ0LjwvcD4K"
    "ICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+U3Rv"
    "cmUgbmFtZTwvbGFiZWw+CiAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0ic3RvcmVf"
    "bmFtZSIgdmFsdWU9IiR7ZXNjKENNUy5zdG9yZU5hbWV8fENGRy5uYW1lKX0iPjwvZGl2PgogICAg"
    "PGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5UYWdsaW5lPC9sYWJlbD4KICAgICAgPGlucHV0IGNs"
    "YXNzPSJpbnAiIGRhdGEtc2V0PSJzdG9yZV90YWdsaW5lIiB2YWx1ZT0iJHtlc2MoQ01TLnN0b3Jl"
    "VGFnfHxDRkcudGFnKX0iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5UYWds"
    "aW5lPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IiR7Q0ZHLnRhZ30iPjwvZGl2Pgog"
    "ICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5TdXBwb3J0IGNvbnRhY3Q8L2xhYmVsPjxpbnB1"
    "dCBjbGFzcz0iaW5wIiBwbGFjZWhvbGRlcj0iQHlvdXJfc3VwcG9ydCI+PC9kaXY+CiAgICA8L2Rp"
    "dj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlRvZ2dsZXM8L2gzPgogICAgPGRpdiBjbGFzcz0i"
    "c3dhcCI+PGRpdiBjbGFzcz0idCI+Rm9yY2Ugam9pbjxzbWFsbD5SZXF1aXJlIGNoYW5uZWwgbWVt"
    "YmVyc2hpcDwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5z"
    "dy5mb3JjZV9zdWIpPyIgb24iOiIifSIgZGF0YS1zZXR0aW5nPSJmb3JjZV9zdWIiPjwvZGl2Pjwv"
    "ZGl2PgogICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+TWFpbnRlbmFuY2UgbW9k"
    "ZTxzbWFsbD5GcmVlemUgdGhlIHN0b3JlPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0"
    "ZyR7KEFETS5zdyYmQURNLnN3Lm1haW50ZW5hbmNlKT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0i"
    "bWFpbnRlbmFuY2UiPjwvZGl2PjwvZGl2PgogICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFz"
    "cz0idCI+QXV0byByZWZ1bmRzPHNtYWxsPlJlZnVuZCBmYWlsZWQgbG9naW5zPC9zbWFsbD48L2Rp"
    "dj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5hdXRvUmVmdW5kPyIgb24iOiIifSIgZGF0YS1m"
    "ZWF0PSJhdXRvUmVmdW5kIj48L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYg"
    "Y2xhc3M9InQiPlJlZmVycmFsIHByb2dyYW08c21hbGw+MSUgb2YgaW52aXRlZCBkZXBvc2l0czwv"
    "c21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGcke0ZFQVQucmVmRGFzaD8iIG9uIjoiIn0i"
    "IGRhdGEtZmVhdD0icmVmRGFzaCI+PC9kaXY+PC9kaXY+PC9kaXY+CiAgICR7RygiaTE4biIsYDxk"
    "aXYgY2xhc3M9ImNhcmQiPjxoMz5MYW5ndWFnZXM8L2gzPjxwIGNsYXNzPSJzdWIiPlVzZXJzIHBp"
    "Y2sgdGhlaXIgb3duOyBldmVyeSBzdHJpbmcgaXMgdHJhbnNsYXRlZC48L3A+CiAgICAgPGRpdiBj"
    "bGFzcz0iZmllbGQiPjxsYWJlbD5EZWZhdWx0IGxhbmd1YWdlPC9sYWJlbD4KICAgICAgPHNlbGVj"
    "dCBjbGFzcz0iaW5wIiBkYXRhLXNldHNlbD0ibGFuZ19kZWZhdWx0Ij4KICAgICAgICA8b3B0aW9u"
    "IHZhbHVlPSJlbiIgJHsoQURNLnNldHRpbmdzJiZBRE0uc2V0dGluZ3MubGFuZz09PSJlbiIpPyJz"
    "ZWxlY3RlZCI6IiJ9PkVuZ2xpc2g8L29wdGlvbj4KICAgICAgICA8b3B0aW9uIHZhbHVlPSJoaSIg"
    "JHsoQURNLnNldHRpbmdzJiZBRE0uc2V0dGluZ3MubGFuZz09PSJoaSIpPyJzZWxlY3RlZCI6IiJ9"
    "PuCkueCkv+CkqOCljeCkpuClgDwvb3B0aW9uPgogICAgICA8L3NlbGVjdD48L2Rpdj48L2Rpdj5g"
    "KX0KICAgJHtHKCJzbGFzaENtZHMiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+U2xhc2ggY29tbWFu"
    "ZHM8L2gzPjxwIGNsYXNzPSJzdWIiPlJlZ2lzdGVyZWQgaW4gVGVsZWdyYW0ncyBjb21tYW5kIG1l"
    "bnUuPC9wPgogICAgICR7W1siL3N0YXJ0IiwiT3BlbiB0aGUgc3RvcmUiXSxbIi9idXkiLCJKdW1w"
    "IHRvIHN0b2NrIl0sWyIvYmFsYW5jZSIsIlNob3cgd2FsbGV0Il0sWyIvb3JkZXJzIiwiTXkgb3Jk"
    "ZXJzIl0sWyIvc3VwcG9ydCIsIkNvbnRhY3Qgc3VwcG9ydCJdXQogICAgICAgLm1hcCgoW2MsZF0p"
    "PT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7Y308"
    "L2I+PC9zcGFuPjxiIHN0eWxlPSJmb250LXdlaWdodDo1MDA7Zm9udC1zaXplOi43NHJlbSI+JHtk"
    "fTwvYj48L2Rpdj5gKS5qb2luKCIiKX0KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2sgc29m"
    "dCIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCIgZGF0YS1hZG1pbi1hY3Q9InN5bmMiPlN5bmMgQ29t"
    "bWFuZHM8L2J1dHRvbj48L2Rpdj5gKX0KICAgJHtHKCJhdXRvQmFja3VwIixgPGRpdiBjbGFzcz0i"
    "Y2FyZCI+PGgzPkF1dG8tYmFja3VwPC9oMz48cCBjbGFzcz0ic3ViIj5EYXRhYmFzZSB0byBhIHBy"
    "aXZhdGUgY2hhbm5lbCBvbiBhIHNjaGVkdWxlLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIg"
    "c3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+RXZlcnkgKGhvdXJzKTwvbGFiZWw+PGlucHV0"
    "IGNsYXNzPSJpbnAiIHZhbHVlPSI2Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+"
    "TGFzdCBiYWNrdXA8L3NwYW4+PGI+NDIgbWluIGFnbzwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNz"
    "PSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxidXR0b24gY2xhc3M9ImJ0biIgZGF0"
    "YS1hZG1pbi1hY3Q9ImJhY2t1cCI+QmFjayBVcCBOb3c8L2J1dHRvbj4KICAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBnaG9zdCIgZGF0YS1hZG1pbi1hY3Q9InJlc3RvcmUiPlJlc3RvcmU8L2J1dHRv"
    "bj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJhdWRpdExvZyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxo"
    "Mz5BdWRpdCBsb2c8L2gzPjxwIGNsYXNzPSJzdWIiPkV2ZXJ5IGFkbWluIGFjdGlvbiwgd2l0aCB1"
    "bmRvLjwvcD4KICAgICAkeygoQURNLmF1ZGl0JiZBRE0uYXVkaXQubGVuZ3RoKT9BRE0uYXVkaXQu"
    "bWFwKGE9PlthLndobywoYS53aGF0KyIgIithLmRldGFpbCkudHJpbSgpLGEud2hlbl0pOltdKQog"
    "ICAgICAgLm1hcCgoW3UsYSx0XSk9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+PGIgc3R5bGU9ImNv"
    "bG9yOnZhcigtLXR4KSI+JHt1fTwvYj4gJHthfTwvc3Bhbj4KICAgICAgICAgPGI+JHt0fSA8L2I+"
    "PC9kaXY+YCkuam9pbigiIil9PC9kaXY+YCl9CiAgICR7Rygic2FuZGJveCIsYDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNz"
    "PSJ0Ij5TYW5kYm94IC8gdGVzdCBtb2RlPHNtYWxsPkZha2UgbW9uZXksIG5vIHJlYWwgZGVsaXZl"
    "cnk8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuc2Fu"
    "ZGJveCk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9InNhbmRib3giPjwvZGl2PjwvZGl2PjwvZGl2"
    "PmApfQogICAke0coImVuY3J5cHQiLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlz"
    "cGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9"
    "InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5FbmNyeXB0ZWQ8L3NwYW4+CiAgICAgPGRpdiBz"
    "dHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgy"
    "KSI+U2Vzc2lvbnMgYXJlIGVuY3J5cHRlZCBhdCByZXN0PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIHhzIHNvZnQiIGRhdGEtYS1nbz0ic2VjdXJpdHkiPktleXM8L2J1dHRvbj48L2Rpdj5g"
    "KX0KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkRhbmdlciB6b25lPC9oMz48cCBjbGFzcz0ic3Vi"
    "Ij5JcnJldmVyc2libGUuIEFzayB0d2ljZS48L3A+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0"
    "eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWFk"
    "bWluLWFjdD0iYmFja3VwIj5CYWNrdXA8L2J1dHRvbj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBi"
    "YWQgaG9sZCIgZGF0YS1kb25lPSJEYXRhYmFzZSByZXNldCAoZGVtbykiPjxzcGFuIGNsYXNzPSJm"
    "aWxsIj48L3NwYW4+SG9sZCB0byBSZXNldCBEQjwvYnV0dG9uPjwvZGl2PjwvZGl2PmA7Cn0KCmZ1"
    "bmN0aW9uIGJpbmRBZG1pbigpewogIGJpbmRDb21tb24oKTsKICBkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCJbZGF0YS1mZWF0XSIpLmZvckVhY2godD0+dC5vbmNsaWNrPSgpPT57CiAgICBjb25z"
    "dCBrPXQuZGF0YXNldC5mZWF0O0ZFQVRba109IUZFQVRba107dC5jbGFzc0xpc3QudG9nZ2xlKCJv"
    "biIsRkVBVFtrXSk7CiAgICBwdXNoKCIvYXBpL2FkbWluL2ZlYXR1cmUiLHtrZXk6ayxvbjpGRUFU"
    "W2tdfSk7CiAgICBjb25zdCBjPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJmY291bnQiKTsKICAg"
    "IGlmKGMpYy50ZXh0Q29udGVudD1PYmplY3QudmFsdWVzKEZFQVQpLmZpbHRlcihCb29sZWFuKS5s"
    "ZW5ndGgrIiBvZiAiK09iamVjdC5rZXlzKEZFQVQpLmxlbmd0aDsKICAgIGFwcGx5RmVhdHVyZXMo"
    "KTsKICAgIGNvbnN0IGNhcmQ9dC5jbG9zZXN0KCIuZmNhcmQiKSwgbmFtZUVsPWNhcmQ/Y2FyZC5x"
    "dWVyeVNlbGVjdG9yKCJiIik6bnVsbDsKICAgIGNvbnN0IGxhYmVsPW5hbWVFbD9uYW1lRWwudGV4"
    "dENvbnRlbnQ6ay5yZXBsYWNlKC8oW0EtWl0pL2csIiAkMSIpLnRvTG93ZXJDYXNlKCk7CiAgICB0"
    "b2FzdChsYWJlbCsoRkVBVFtrXT8iIGVuYWJsZWQiOiIgaGlkZGVuIikpO30pOwogIGNvbnN0IGZx"
    "PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJmcSIpOwogIGlmKGZxKWZxLm9uaW5wdXQ9KCk9Pntj"
    "b25zdCBxPWZxLnZhbHVlLnRyaW0oKS50b0xvd2VyQ2FzZSgpOwogICAgZG9jdW1lbnQucXVlcnlT"
    "ZWxlY3RvckFsbCgiLmZyb3ciKS5mb3JFYWNoKHI9PnIuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZSIs"
    "ISFxJiYhci5kYXRhc2V0LmYuaW5jbHVkZXMocSkpKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIi5mZ3JwIikuZm9yRWFjaChnPT5nLmNsYXNzTGlzdC50b2dnbGUoImhpZGUiLCFnLnF1"
    "ZXJ5U2VsZWN0b3IoIi5mcm93Om5vdCguaGlkZSkiKSkpO307CiAgY29uc3Qgc2V0QWxsPXY9PntP"
    "YmplY3Qua2V5cyhGRUFUKS5mb3JFYWNoKGs9PkZFQVRba109dik7YXBwbHlGZWF0dXJlcygpO3Jl"
    "bmRlckFkbWluKCk7dG9hc3Qodj8iQWxsIGZlYXR1cmVzIGVuYWJsZWQiOiJBbGwgZmVhdHVyZXMg"
    "aGlkZGVuIik7fTsKICBjb25zdCBmYT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZmFsbCIpLGZu"
    "PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJmbm9uZSIpLGZyPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJmcmVzZXQiKTsKICBpZihmYSlmYS5vbmNsaWNrPSgpPT5zZXRBbGwodHJ1ZSk7CiAgaWYo"
    "Zm4pZm4ub25jbGljaz0oKT0+c2V0QWxsKGZhbHNlKTsKICBpZihmcilmci5vbmNsaWNrPSgpPT57"
    "T2JqZWN0LmFzc2lnbihGRUFULEZFQVQwKTthcHBseUZlYXR1cmVzKCk7cmVuZGVyQWRtaW4oKTt0"
    "b2FzdCgiRmVhdHVyZXMgcmVzZXQiKTt9OwogIGNvbnN0IHN0bXNnPW09Pntjb25zdCBlPWRvY3Vt"
    "ZW50LmdldEVsZW1lbnRCeUlkKCJzdG1zZyIpO2lmKGUpZS50ZXh0Q29udGVudD1tO307CiAgYXN5"
    "bmMgZnVuY3Rpb24gaW1wb3J0U3RvY2sodGV4dCl7CiAgICBpZighdGV4dC50cmltKCkpe3RvYXN0"
    "KCJQYXN0ZSBzb21lIGxpbmVzIGZpcnN0Iik7cmV0dXJuO30KICAgIGlmKCFMSVZFKXt0b2FzdCgi"
    "UHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgIHN0bXNnKCJJbXBvcnRpbmfigKYiKTsKICAgIGNv"
    "bnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3N0b2NrIix7dGV4dDp0ZXh0fSk7CiAgICBp"
    "Zighcil7c3Rtc2coIiIpO3JldHVybjt9CiAgICBjb25zdCBub3RlPXIuYWRkZWQrIiBpbXBvcnRl"
    "ZCIrKHIuc2tpcHBlZCYmci5za2lwcGVkLmxlbmd0aD8oIiDCtyAiK3Iuc2tpcHBlZC5sZW5ndGgr"
    "IiBza2lwcGVkIik6IiIpOwogICAgdG9hc3Qoci5hZGRlZCsiIGFjY291bnRzIGFkZGVkIik7CiAg"
    "ICBjb25zdCBib3g9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0cGFzdGUiKTtpZihib3gpYm94"
    "LnZhbHVlPSIiOwogICAgYXdhaXQgcmVmcmVzaCgpOwogICAgc3Rtc2cobm90ZSk7CiAgfQogIGNv"
    "bnN0IHN0c2F2ZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RzYXZlIik7CiAgaWYoc3RzYXZl"
    "KXN0c2F2ZS5vbmNsaWNrPSgpPT5pbXBvcnRTdG9jaygoZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "InN0cGFzdGUiKXx8e30pLnZhbHVlfHwiIik7CiAgY29uc3Qgc3RmaWxlPWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJzdGZpbGUiKTsKICBpZihzdGZpbGUpc3RmaWxlLm9uY2hhbmdlPSgpPT57CiAg"
    "ICBjb25zdCBmPXN0ZmlsZS5maWxlcyYmc3RmaWxlLmZpbGVzWzBdO2lmKCFmKXJldHVybjsKICAg"
    "IGNvbnN0IHJkPW5ldyBGaWxlUmVhZGVyKCk7CiAgICByZC5vbmxvYWQ9KCk9Pntjb25zdCBib3g9"
    "ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0cGFzdGUiKTsKICAgICAgaWYoYm94KWJveC52YWx1"
    "ZT1yZC5yZXN1bHQ7c3Rtc2coZi5uYW1lKyIgbG9hZGVkIOKAlCBwcmVzcyBJbXBvcnQiKTt9Owog"
    "ICAgcmQucmVhZEFzVGV4dChmKTsKICB9OwogIGNvbnN0IGJsPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJibGJsb2NrIik7CiAgaWYoYmwpYmwub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBp"
    "PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJibC1pbiIpOwogICAgY29uc3Qgdj1pPyhpLnZhbHVl"
    "fHwiIikudHJpbSgpOiIiOwogICAgaWYoIXYpe3RvYXN0KCJUeXBlIGFuIGlkLCBwaG9uZSBvciBV"
    "UEkgcmVmZXJlbmNlIik7aWYoaSlpLmZvY3VzKCk7cmV0dXJuO30KICAgIGlmKCFMSVZFKXt0b2Fz"
    "dCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBp"
    "L2FkbWluL2JsYWNrbGlzdCIse2FjdGlvbjoiYWRkIix2YWx1ZTp2fSk7CiAgICBpZighcilyZXR1"
    "cm47CiAgICBpLnZhbHVlPSIiO3RvYXN0KCJCbG9ja2VkICIrdik7YXdhaXQgcmVmcmVzaCgpOwog"
    "IH07CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtYmwtZGVsXSIpLmZvckVhY2go"
    "Yj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2Fk"
    "bWluL2JsYWNrbGlzdCIse2FjdGlvbjoiZGVsIixpZDorYi5kYXRhc2V0LmJsRGVsfSk7CiAgICBp"
    "ZihyKXt0b2FzdCgiVW5ibG9ja2VkIik7YXdhaXQgcmVmcmVzaCgpO30KICB9KTsKICBkb2N1bWVu"
    "dC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1taXNjXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFz"
    "eW5jKCk9PnsKICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAg"
    "IGNvbnN0IHdoYXQ9Yi5kYXRhc2V0Lm1pc2MsIHZhbD1iLmRhdGFzZXQubWlzY1ZhbHVlfHwiIjsK"
    "ICAgIGNvbnN0IGxibD1iLnRleHRDb250ZW50O2IuZGlzYWJsZWQ9dHJ1ZTtiLnRleHRDb250ZW50"
    "PSLigKYiOwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vbWlzYyIse3doYXQ6"
    "d2hhdCx2YWx1ZTp2YWx9KTsKICAgIGIuZGlzYWJsZWQ9ZmFsc2U7Yi50ZXh0Q29udGVudD1sYmw7"
    "CiAgICBpZighcilyZXR1cm47CiAgICBpZihyLmxpbmVzKXNoZWV0KGA8aDMgc3R5bGU9Im1hcmdp"
    "bjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5DaGFubmVsIGNoZWNrPC9oMz4KICAgICAgPHAgY2xh"
    "c3M9InN1YiIgc3R5bGU9IndoaXRlLXNwYWNlOnByZS1saW5lIj4ke2VzYyhyLmxpbmVzLmpvaW4o"
    "IlxuIikpfTwvcD4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2lu"
    "LXRvcDoxMnB4IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+YCk7CiAgICBlbHNlIGlmKHdoYXQ9"
    "PT0ibG9ndGVzdCIpdG9hc3QoIlRlc3QgbWVzc2FnZSBzZW50IHRvIHRoZSBsb2cgY2hhbm5lbCIp"
    "OwogICAgZWxzZSBpZih3aGF0PT09ImNsZWFyc3RvY2siKXRvYXN0KHIucmVtb3ZlZCsiIHJlbW92"
    "ZWQiKTsKICAgIGVsc2UgdG9hc3QoIkRvbmUiKTsKICAgIGF3YWl0IHJlZnJlc2goKTsKICB9KTsK"
    "ICBjb25zdCB1cGlhZGQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInVwaWFkZCIpOwogIGlmKHVw"
    "aWFkZCl1cGlhZGQub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBpPXVwaWFkZC5jbG9zZXN0"
    "KCJkaXYiKS5xdWVyeVNlbGVjdG9yKCJpbnB1dCIpOwogICAgY29uc3Qgdj1pPyhpLnZhbHVlfHwi"
    "IikudHJpbSgpOiIiOwogICAgaWYoIXYpe3RvYXN0KCJQYXN0ZSB0aGUgZXh0cmEgVVBJIGhhbmRs"
    "ZSIpO3JldHVybjt9CiAgICBjb25zdCBjdXI9KEFETS5wYXkmJkFETS5wYXkuZXh0cmEpfHwiIjsK"
    "ICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3NldHRpbmciLHtrZXk6InVwaV9l"
    "eHRyYSIsdmFsdWU6Y3VyPyhjdXIrIiwiK3YpOnZ9KTsKICAgIGlmKHIpe2kudmFsdWU9IiI7dG9h"
    "c3QoIkhhbmRsZSBhZGRlZCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgfTsKICBjb25zdCBhZG1pbmFk"
    "ZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYWRtaW5hZGQiKTsKICBpZihhZG1pbmFkZClhZG1p"
    "bmFkZC5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGk9YWRtaW5hZGQuY2xvc2VzdCgiZGl2"
    "IikucXVlcnlTZWxlY3RvcigiaW5wdXQiKTsKICAgIGNvbnN0IHY9aT8oaS52YWx1ZXx8IiIpLnRy"
    "aW0oKS5yZXBsYWNlKC9eQC8sIiIpOiIiOwogICAgaWYoIXYpe3RvYXN0KCJTZW5kIHRoZSBudW1l"
    "cmljIHVzZXIgaWQiKTtyZXR1cm47fQogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRt"
    "aW4vbWlzYyIse3doYXQ6ImFkZGFkbWluIix2YWx1ZTp2fSk7CiAgICBpZihyKXtpLnZhbHVlPSIi"
    "O3RvYXN0KCJBZG1pbiBhZGRlZCB3aXRoIG5vIHBvd2VycyDigJQgZ3JhbnQgdGhlbSBpbiB0aGUg"
    "Ym90Iik7YXdhaXQgcmVmcmVzaCgpO30KICB9OwogIGNvbnN0IHByaXplPWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJwcml6ZXNhdmUiKTsKICBpZihwcml6ZSlwcml6ZS5vbmNsaWNrPWFzeW5jKCk9"
    "PnsKICAgIGNvbnN0IGk9cHJpemUuY2xvc2VzdCgiZGl2IikucXVlcnlTZWxlY3RvcigiaW5wdXQi"
    "KTsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3NldHRpbmciLHtrZXk6Imxl"
    "YWRlcmJvYXJkX3ByaXplIix2YWx1ZTooaSYmaS52YWx1ZSl8fCIwIn0pOwogICAgaWYocil0b2Fz"
    "dCgiUHJpemUgc2F2ZWQiKTsKICB9OwogIGNvbnN0IGNwYWRkPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJjcGFkZCIpOwogIGlmKGNwYWRkKWNwYWRkLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29u"
    "c3QgY29kZT0oKGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjcGNvZGUiKXx8e30pLnZhbHVlfHwi"
    "IikudHJpbSgpOwogICAgY29uc3QgcGN0PSgoZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNwcGN0"
    "Iil8fHt9KS52YWx1ZXx8IiIpLnRyaW0oKTsKICAgIGlmKCFjb2RlfHwhcGN0KXt0b2FzdCgiQ29k"
    "ZSBhbmQgcGVyY2VudCwgcGxlYXNlIik7cmV0dXJuO30KICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJl"
    "dmlldyBvbmx5Iik7cmV0dXJuO30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWlu"
    "L2NvdXBvbiIse2NvZGU6Y29kZSxwZXJjZW50OnBjdH0pOwogICAgaWYoIXIpcmV0dXJuOwogICAg"
    "Y29uc3QgYTE9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNwY29kZSIpLGEyPWRvY3VtZW50Lmdl"
    "dEVsZW1lbnRCeUlkKCJjcHBjdCIpOwogICAgaWYoYTEpYTEudmFsdWU9IiI7aWYoYTIpYTIudmFs"
    "dWU9IiI7CiAgICB0b2FzdCgiQ291cG9uICIrY29kZS50b1VwcGVyQ2FzZSgpKyIgaXMgbGl2ZSIp"
    "O2F3YWl0IHJlZnJlc2goKTsKICB9OwogIGNvbnN0IGZnPWRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJmbGFzaGdvIik7CiAgaWYoZmcpZmcub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBjYXJk"
    "PWZnLmNsb3Nlc3QoIi5jYXJkIil8fGRvY3VtZW50OwogICAgY29uc3QgaW5zPWNhcmQucXVlcnlT"
    "ZWxlY3RvckFsbCgiaW5wdXQiKTsKICAgIGNvbnN0IHBjdD0oKGluc1swXSYmaW5zWzBdLnZhbHVl"
    "KXx8IjIwIiksIGhycz0oKGluc1sxXSYmaW5zWzFdLnZhbHVlKXx8IjUiKTsKICAgIGlmKCFMSVZF"
    "KXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5"
    "KCIvYXBpL2FkbWluL2ZsYXNoIix7cGVyY2VudDpwY3QsaG91cnM6aHJzfSk7CiAgICBpZihyKXt0"
    "b2FzdCgiRmxhc2ggc2FsZSBsaXZlIOKAlCAiK3BjdCsiJSBmb3IgIitocnMrImgiKTthd2FpdCBy"
    "ZWZyZXNoKCk7fQogIH07CiAgY29uc3QgYmM9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJjZ28i"
    "KTsKICBpZihiYyliYy5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGNhcmQ9YmMuY2xvc2Vz"
    "dCgiLmNhcmQiKXx8ZG9jdW1lbnQ7CiAgICBjb25zdCB0YT1jYXJkLnF1ZXJ5U2VsZWN0b3IoInRl"
    "eHRhcmVhIik7CiAgICBjb25zdCB0ZXh0PSgodGEmJnRhLnZhbHVlKXx8IiIpLnRyaW0oKTsKICAg"
    "IGlmKCF0ZXh0KXt0b2FzdCgiV3JpdGUgdGhlIG1lc3NhZ2UgZmlyc3QiKTtpZih0YSl0YS5mb2N1"
    "cygpO3JldHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9"
    "CiAgICBiYy5kaXNhYmxlZD10cnVlO2JjLnRleHRDb250ZW50PSJTZW5kaW5n4oCmIjsKICAgIGNv"
    "bnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2Jyb2FkY2FzdCIse3RleHQ6dGV4dH0pOwog"
    "ICAgYmMuZGlzYWJsZWQ9ZmFsc2U7YmMudGV4dENvbnRlbnQ9IlNlbmQgQnJvYWRjYXN0IjsKICAg"
    "IGlmKHIpe3RvYXN0KCJTZW50IHRvICIrci5zZW50KyIgcGVvcGxlIik7aWYodGEpdGEudmFsdWU9"
    "IiI7fQogIH07CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZGVwLW9rXSIpLmZv"
    "ckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGlkPWIuZGF0YXNldC5kZXBP"
    "azsKICAgIGlmKCFpZCl7dG9hc3QoIk5vdGhpbmcgdG8gYXBwcm92ZSIpO3JldHVybjt9CiAgICBj"
    "b25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9kZXBvc2l0Iix7aWQ6K2lkLGFjdGlvbjoi"
    "YXBwcm92ZSJ9KTsKICAgIGlmKHIpe3RvYXN0KCJDcmVkaXRlZCIpO2F3YWl0IHJlZnJlc2goKTt9"
    "CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtcmV2XSIpLmZvckVhY2go"
    "Yj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2Fk"
    "bWluL3JldmlldyIse2lkOitiLmRhdGFzZXQucmV2fSk7CiAgICBpZihyKXt0b2FzdCgiVXBkYXRl"
    "ZCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgi"
    "W2RhdGEtc2V0dGluZ10iKS5mb3JFYWNoKHQ9PnsKICAgIHQub25jbGljaz1hc3luYygpPT57CiAg"
    "ICAgIGNvbnN0IGtleT10LmRhdGFzZXQuc2V0dGluZywgb249IXQuY2xhc3NMaXN0LmNvbnRhaW5z"
    "KCJvbiIpOwogICAgICB0LmNsYXNzTGlzdC50b2dnbGUoIm9uIixvbik7CiAgICAgIGlmKCFMSVZF"
    "KXt0b2FzdChvbj8iT24iOiJPZmYiKTtyZXR1cm47fQogICAgICBjb25zdCByPWF3YWl0IGFwaVRy"
    "eSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OmtleSx2YWx1ZTpvbj8iMSI6IjAifSk7CiAgICAg"
    "IGlmKCFyKXt0LmNsYXNzTGlzdC50b2dnbGUoIm9uIiwhb24pO3JldHVybjt9CiAgICAgIEFETS5z"
    "dz1BRE0uc3d8fHt9O0FETS5zd1trZXldPW9uOwogICAgICB0b2FzdChrZXkucmVwbGFjZSgvXy9n"
    "LCIgIikrKG9uPyIgb24iOiIgb2ZmIikpOwogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNl"
    "bGVjdG9yQWxsKCJbZGF0YS1hZG1pbi1hY3RdIikuZm9yRWFjaChiPT57CiAgICBiLm9uY2xpY2s9"
    "YXN5bmMoKT0+ewogICAgICBjb25zdCBhY3Q9Yi5kYXRhc2V0LmFkbWluQWN0OwogICAgICBpZigh"
    "TElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICAgIGNvbnN0IGxhYmVsPWIu"
    "dGV4dENvbnRlbnQ7Yi5kaXNhYmxlZD10cnVlO2IudGV4dENvbnRlbnQ9IldvcmtpbmfigKYiOwog"
    "ICAgICBsZXQgcj1udWxsOwogICAgICBpZihhY3Q9PT0iYmFja3VwIikgICAgICByPWF3YWl0IGFw"
    "aVRyeSgiL2FwaS9hZG1pbi9iYWNrdXAiLHt9KTsKICAgICAgZWxzZSBpZihhY3Q9PT0iZGlnZXN0"
    "Iikgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vZGlnZXN0Iix7fSk7CiAgICAgIGVsc2UgaWYo"
    "YWN0PT09ImV4cG9ydCIpIHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2V4cG9ydCIse30pOwog"
    "ICAgICBlbHNlIGlmKGFjdD09PSJzeW5jIikgICByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9z"
    "eW5jIix7fSk7CiAgICAgIGIuZGlzYWJsZWQ9ZmFsc2U7Yi50ZXh0Q29udGVudD1sYWJlbDsKICAg"
    "ICAgaWYoIXIpcmV0dXJuOwogICAgICBpZihhY3Q9PT0iYmFja3VwIil0b2FzdCgiRGF0YWJhc2Ug"
    "c2VudCB0byB5b3UgaW4gY2hhdCIpOwogICAgICBpZihhY3Q9PT0iZGlnZXN0Iil0b2FzdCgiRGln"
    "ZXN0IHNlbnQgdG8gdGhlIGFkbWlucyIpOwogICAgICBpZihhY3Q9PT0iZXhwb3J0Iil0b2FzdChy"
    "LnJvd3MrIiBvcmRlcnMgc2VudCBhcyBDU1YiKTsKICAgICAgaWYoYWN0PT09InN5bmMiKXRvYXN0"
    "KCJDb21tYW5kcyBzeW5jZWQgd2l0aCBUZWxlZ3JhbSIpOwogICAgICBhd2FpdCByZWZyZXNoKCk7"
    "CiAgICB9OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXByaWNlXSIp"
    "LmZvckVhY2goaW5wPT57CiAgICBpbnAub25jaGFuZ2U9YXN5bmMoKT0+ewogICAgICBpZighTElW"
    "RSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBp"
    "VHJ5KCIvYXBpL2FkbWluL3ByaWNpbmciLHtrZXk6aW5wLmRhdGFzZXQucHJpY2UsdmFsdWU6aW5w"
    "LnZhbHVlfSk7CiAgICAgIGlmKHIpe3RvYXN0KCJQcmljaW5nIHVwZGF0ZWQiKTthd2FpdCByZWZy"
    "ZXNoKCk7fQogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1j"
    "cHJpY2VdIikuZm9yRWFjaChpbnA9PnsKICAgIGlucC5vbmNoYW5nZT1hc3luYygpPT57CiAgICAg"
    "IGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgICAgY29uc3Qgcj1h"
    "d2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vcHJpY2luZyIse2NvdW50cnk6aW5wLmRhdGFzZXQuY3By"
    "aWNlLHByaWNlOmlucC52YWx1ZX0pOwogICAgICBpZihyKXt0b2FzdChpbnAuZGF0YXNldC5jcHJp"
    "Y2UrIiBub3cgIittb25leShpbnAudmFsdWUpKTthd2FpdCByZWZyZXNoKCk7fQogICAgfTsKICB9"
    "KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1xYV0iKS5mb3JFYWNoKGI9PmIu"
    "b25jbGljaz0oKT0+ewogICAgY29uc3Qgd2hhdD1iLmRhdGFzZXQucWE7CiAgICBjb25zdCBnbz0o"
    "dGFiLG1zZyk9PnsKICAgICAgY29uc3QgdD1kb2N1bWVudC5xdWVyeVNlbGVjdG9yKGAjYXN1YiBi"
    "dXR0b25bZGF0YS1hPSIke3RhYn0iXWApOwogICAgICBpZih0KXt0LmNsaWNrKCk7dG9hc3QobXNn"
    "KTt9ZWxzZSB0b2FzdChtc2cpOwogICAgfTsKICAgIGlmKHdoYXQ9PT0iYnJvYWRjYXN0IilyZXR1"
    "cm4gZ28oIm1hcmtldCIsIkNvbXBvc2UgdGhlIGJyb2FkY2FzdCBmcm9tIHRoZSBib3Q6IC9hZG1p"
    "biDihpIgTWFya2V0aW5nIik7CiAgICBpZih3aGF0PT09InN0b2NrIilyZXR1cm4gZ28oInN0b2Nr"
    "IiwiQWRkIHN0b2NrIGZyb20gdGhlIGJvdDogL2FkbWluIOKGkiBTdG9jayDihpIgQWRkIHN0b2Nr"
    "Iik7CiAgICBpZih3aGF0PT09ImZsYXNoIilyZXR1cm4gZ28oIm1hcmtldCIsIkZsYXNoIHNhbGUg"
    "bGl2ZXMgaGVyZSIpOwogICAgaWYod2hhdD09PSJiYWNrdXAiKXJldHVybiB0b2FzdCgiQmFja3Vw"
    "cyBhcmUgc2VudCB0byB5b3VyIGxvZyBjaGFubmVsIOKAlCAvYWRtaW4g4oaSIFNldHRpbmdzIik7"
    "CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtc2V0XSIpLmZvckVhY2go"
    "aW5wPT57CiAgICBpbnAub25jaGFuZ2U9YXN5bmMoKT0+ewogICAgICBjb25zdCBrZXk9aW5wLmRh"
    "dGFzZXQuc2V0LCB2YWw9aW5wLnZhbHVlOwogICAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcg"
    "b25seSIpO3JldHVybjt9CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3Nl"
    "dHRpbmciLHtrZXk6a2V5LHZhbHVlOnZhbH0pOwogICAgICBpZihyKXt0b2FzdChrZXkucmVwbGFj"
    "ZSgvXy9nLCIgIikrIiBzYXZlZCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgICB9OwogIH0pOwogIGRv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNtc10iKS5mb3JFYWNoKHQ9PnsKICAgIHQu"
    "b25pbnB1dD0oKT0+e0NNU1t0LmRhdGFzZXQuY21zXT10LnZhbHVlO2FwcGx5Q01TKCk7CiAgICAg"
    "IGNsZWFyVGltZW91dCh0Ll9zdik7dC5fc3Y9c2V0VGltZW91dCgoKT0+cHVzaCgiL2FwaS9hZG1p"
    "bi9jb250ZW50Iix7a2V5OnQuZGF0YXNldC5jbXMsdmFsdWU6dC52YWx1ZX0pLDUwMCk7CiAgICAg"
    "IHQuY2xvc2VzdCgiLmNtc3JvdyIpLmRhdGFzZXQuZj0odC5jbG9zZXN0KCIuY21zcm93IikucXVl"
    "cnlTZWxlY3RvcigibGFiZWwiKS50ZXh0Q29udGVudCsiICIrdC52YWx1ZSkudG9Mb3dlckNhc2Uo"
    "KTt9O30pOwogIGNvbnN0IGNxPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjcSIpOwogIGlmKGNx"
    "KWNxLm9uaW5wdXQ9KCk9Pntjb25zdCBxPWNxLnZhbHVlLnRyaW0oKS50b0xvd2VyQ2FzZSgpOwog"
    "ICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiLmNtc3JvdyIpLmZvckVhY2gocj0+ci5jbGFz"
    "c0xpc3QudG9nZ2xlKCJoaWRlIiwhIXEmJiFyLmRhdGFzZXQuZi5pbmNsdWRlcyhxKSkpOwogICAg"
    "ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiLmNtc2dycCIpLmZvckVhY2goZz0+Zy5jbGFzc0xp"
    "c3QudG9nZ2xlKCJoaWRlIiwhZy5xdWVyeVNlbGVjdG9yKCIuY21zcm93Om5vdCguaGlkZSkiKSkp"
    "O307CiAgY29uc3QgY3I9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNtc3Jlc2V0Iik7CiAgaWYo"
    "Y3IpY3Iub25jbGljaz0oKT0+e09iamVjdC5hc3NpZ24oQ01TLENNUzApO2FwcGx5Q01TKCk7cmVu"
    "ZGVyQWRtaW4oKTt0b2FzdCgiVGV4dCByZXNldCB0byBkZWZhdWx0cyIpO307CiAgY29uc3QgYWRk"
    "PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjaC1hZGQiKTsKICBpZihhZGQpYWRkLm9uY2xpY2s9"
    "YXN5bmMoKT0+ewogICAgY29uc3QgaT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2gtaW4iKSx2"
    "PShpLnZhbHVlfHwiIikudHJpbSgpOwogICAgaWYoIXYpe3RvYXN0KCJQYXN0ZSBhIGNoYW5uZWwg"
    "bGluayBmaXJzdCIpO2kuZm9jdXMoKTtyZXR1cm59CiAgICBjb25zdCByZXE9ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoImNoLXR5cGUiKS52YWx1ZT09PSIxIjsKICAgIGlmKExJVkUpewogICAgICBh"
    "ZGQuZGlzYWJsZWQ9dHJ1ZTsKICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4v"
    "Y2hhbm5lbCIse2FjdGlvbjoiYWRkIixoYW5kbGU6dixyZXF1aXJlZDpyZXF9KTsKICAgICAgYWRk"
    "LmRpc2FibGVkPWZhbHNlOwogICAgICBpZighcilyZXR1cm47CiAgICAgIGkudmFsdWU9IiI7YXdh"
    "aXQgcmVmcmVzaCgpO3RvYXN0KCJDaGFubmVsIHNhdmVkIOKAlCB0aGUgYm90IHVzZXMgaXQgbm93"
    "Iik7cmV0dXJuOwogICAgfQogICAgQ0hBTk5FTFMucHVzaCh7aWQ6RGF0ZS5ub3coKSxuYW1lOnYu"
    "cmVwbGFjZSgvXmh0dHBzPzpcL1wvdFwubWVcLy8sIkAiKS5zbGljZSgwLDI2KSxoYW5kbGU6dixy"
    "ZXE6cmVxLG9uOnRydWUsbWVtYmVyczoi4oCUIn0pOwogICAgaS52YWx1ZT0iIjtyZW5kZXJBZG1p"
    "bigpO3RvYXN0KCJDaGFubmVsIGFkZGVkIik7fTsKICBjb25zdCBsZ2FkZD1kb2N1bWVudC5nZXRF"
    "bGVtZW50QnlJZCgibGctYWRkIik7CiAgaWYobGdhZGQpbGdhZGQub25jbGljaz1hc3luYygpPT57"
    "CiAgICBjb25zdCBpPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJsZy1pbiIpLHY9KGkudmFsdWV8"
    "fCIiKS50cmltKCk7CiAgICBpZighdil7dG9hc3QoIlBhc3RlIGEgbG9nIGNoYW5uZWwgZmlyc3Qi"
    "KTtpLmZvY3VzKCk7cmV0dXJufQogICAgaWYoTElWRSl7CiAgICAgIGxnYWRkLmRpc2FibGVkPXRy"
    "dWU7CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rp"
    "b246ImFkZCIsaGFuZGxlOnYsbG9nOnRydWV9KTsKICAgICAgbGdhZGQuZGlzYWJsZWQ9ZmFsc2U7"
    "CiAgICAgIGlmKCFyKXJldHVybjsKICAgICAgaS52YWx1ZT0iIjthd2FpdCByZWZyZXNoKCk7dG9h"
    "c3QoIkxvZyBjaGFubmVsIHNhdmVkIik7cmV0dXJuOwogICAgfQogICAgTE9HQ0gucHVzaCh7aWQ6"
    "RGF0ZS5ub3coKSxuYW1lOnYucmVwbGFjZSgvXmh0dHBzPzpcL1wvdFwubWVcLy8sIkAiKS5zbGlj"
    "ZSgwLDI0KSxoYW5kbGU6dixwcmltYXJ5OkxPR0NILmxlbmd0aD09PTAsb246dHJ1ZX0pOwogICAg"
    "aS52YWx1ZT0iIjtyZW5kZXJBZG1pbigpO3RvYXN0KCJMb2cgY2hhbm5lbCBhZGRlZCIpO307CiAg"
    "ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2hkZWxdIikuZm9yRWFjaChiPT5iLm9u"
    "Y2xpY2s9YXN5bmMoKT0+ewogICAgaWYoTElWRSl7Y29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkv"
    "YWRtaW4vY2hhbm5lbCIse2FjdGlvbjoiZGVsIixpZDorYi5kYXRhc2V0LmNoZGVsfSk7CiAgICAg"
    "IGlmKHIpe2F3YWl0IHJlZnJlc2goKTt0b2FzdCgiQ2hhbm5lbCByZW1vdmVkIik7fXJldHVybjt9"
    "CiAgICBDSEFOTkVMUz1DSEFOTkVMUy5maWx0ZXIoYz0+Yy5pZCE9Yi5kYXRhc2V0LmNoZGVsKTty"
    "ZW5kZXJBZG1pbigpO3RvYXN0KCJDaGFubmVsIHJlbW92ZWQiKX0pOwogIGRvY3VtZW50LnF1ZXJ5"
    "U2VsZWN0b3JBbGwoIltkYXRhLWNocmVxXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9"
    "PnsKICAgIGlmKExJVkUpe2NvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwi"
    "LHthY3Rpb246InJlcXVpcmVkIixpZDorYi5kYXRhc2V0LmNocmVxfSk7CiAgICAgIGlmKHIpe2F3"
    "YWl0IHJlZnJlc2goKTt0b2FzdCgiVXBkYXRlZCIpO31yZXR1cm47fQogICAgY29uc3QgYz1DSEFO"
    "TkVMUy5maW5kKHg9PnguaWQ9PWIuZGF0YXNldC5jaHJlcSk7Yy5yZXE9IWMucmVxO3JlbmRlckFk"
    "bWluKCk7dG9hc3QoYy5yZXE/Ik5vdyByZXF1aXJlZCI6Ik5vdyBvcHRpb25hbCIpfSk7CiAgZG9j"
    "dW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2hvbl0iKS5mb3JFYWNoKGI9PmIub25jbGlj"
    "az1hc3luYygpPT57CiAgICBpZihMSVZFKXtjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1p"
    "bi9jaGFubmVsIix7YWN0aW9uOiJ0b2dnbGUiLGlkOitiLmRhdGFzZXQuY2hvbn0pOwogICAgICBp"
    "ZihyKXthd2FpdCByZWZyZXNoKCk7dG9hc3QoIlVwZGF0ZWQiKTt9cmV0dXJuO30KICAgIGNvbnN0"
    "IGM9Q0hBTk5FTFMuZmluZCh4PT54LmlkPT1iLmRhdGFzZXQuY2hvbik7Yy5vbj0hYy5vbjtyZW5k"
    "ZXJBZG1pbigpO3RvYXN0KGMub24/IkNoYW5uZWwgZW5hYmxlZCI6IkNoYW5uZWwgcGF1c2VkIil9"
    "KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1sZ2RlbF0iKS5mb3JFYWNoKGI9"
    "PmIub25jbGljaz0oKT0+ewogICAgY29uc3Qgd2FzUD1MT0dDSC5maW5kKGw9PmwuaWQ9PWIuZGF0"
    "YXNldC5sZ2RlbCk/LnByaW1hcnk7CiAgICBMT0dDSD1MT0dDSC5maWx0ZXIobD0+bC5pZCE9Yi5k"
    "YXRhc2V0LmxnZGVsKTsKICAgIGlmKHdhc1AmJkxPR0NIWzBdKUxPR0NIWzBdLnByaW1hcnk9dHJ1"
    "ZTtyZW5kZXJBZG1pbigpO3RvYXN0KCJMb2cgY2hhbm5lbCByZW1vdmVkIil9KTsKICBkb2N1bWVu"
    "dC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1sZ3N0YXJdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9"
    "KCk9PnsKICAgIExPR0NILmZvckVhY2gobD0+bC5wcmltYXJ5PShsLmlkPT1iLmRhdGFzZXQubGdz"
    "dGFyKSk7cmVuZGVyQWRtaW4oKTt0b2FzdCgiUHJlZmVycmVkIGxvZyBjaGFubmVsIHNldCIpfSk7"
    "CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtbGdvbl0iKS5mb3JFYWNoKGI9PmIu"
    "b25jbGljaz0oKT0+e2NvbnN0IGw9TE9HQ0guZmluZCh4PT54LmlkPT1iLmRhdGFzZXQubGdvbik7"
    "bC5vbj0hbC5vbjtyZW5kZXJBZG1pbigpO3RvYXN0KGwub24/IkNoYW5uZWwgb24iOiJDaGFubmVs"
    "IG11dGVkIil9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1ldm9uXSIpLmZv"
    "ckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57Y29uc3QgZT1FVkVOVFNbK2IuZGF0YXNldC5ldm9uXTtl"
    "Lm9uPSFlLm9uO3JlbmRlckFkbWluKCl9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJb"
    "ZGF0YS1ldmNoXSIpLmZvckVhY2gocz0+cy5vbmNoYW5nZT0oKT0+e0VWRU5UU1srcy5kYXRhc2V0"
    "LmV2Y2hdLmNoPStzLnZhbHVlO3RvYXN0KCJSb3V0aW5nIHVwZGF0ZWQiKX0pOwogIGNvbnN0IGFs"
    "bD1kb2N1bWVudC5xdWVyeVNlbGVjdG9yKCJbZGF0YS1ldi1hbGxdIik7CiAgaWYoYWxsKWFsbC5v"
    "bmNsaWNrPSgpPT57Y29uc3QgdD0hRVZFTlRTLmV2ZXJ5KGU9PmUub24pO0VWRU5UUy5mb3JFYWNo"
    "KGU9PmUub249dCk7cmVuZGVyQWRtaW4oKTt0b2FzdCh0PyJBbGwgZXZlbnRzIG9uIjoiQWxsIGV2"
    "ZW50cyBvZmYiKX07CiAgY29uc3QgbT1kb2N1bWVudC5xdWVyeVNlbGVjdG9yKCJbZGF0YS1tYXN0"
    "ZXJdIik7CiAgaWYobSltLm9uY2xpY2s9KCk9PntMT0dTX01BU1RFUj0hTE9HU19NQVNURVI7cmVu"
    "ZGVyQWRtaW4oKTt0b2FzdChMT0dTX01BU1RFUj8iTG9nZ2luZyBlbmFibGVkIjoiQWxsIGxvZ2dp"
    "bmcgZGlzYWJsZWQiKX07CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtYS1nb10i"
    "KS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiI2FzdWIgYnV0dG9uIikuZm9yRWFjaCh4PT54LmNsYXNzTGlzdC50b2dnbGUoIm9uIix4LmRh"
    "dGFzZXQuYT09PWIuZGF0YXNldC5hR28pKTsKICAgIEFUQUI9Yi5kYXRhc2V0LmFHbztyZW5kZXJB"
    "ZG1pbigpO3dpbmRvdy5zY3JvbGxUbygwLDApO30pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JB"
    "bGwoIltkYXRhLXRnXSIpLmZvckVhY2godD0+dC5vbmNsaWNrPSgpPT50LmNsYXNzTGlzdC50b2dn"
    "bGUoIm9uIikpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXNldHNlbF0iKS5m"
    "b3JFYWNoKHNlbD0+c2VsLm9uY2hhbmdlPWFzeW5jKCk9PnsKICAgIGlmKCFMSVZFKXt0b2FzdCgi"
    "UHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2Fk"
    "bWluL3NldHRpbmciLHtrZXk6c2VsLmRhdGFzZXQuc2V0c2VsLHZhbHVlOnNlbC52YWx1ZX0pOwog"
    "ICAgaWYocil0b2FzdCgiU2F2ZWQiKTsKICB9KTsKfQoKY29uc3QgX2NjPWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJjYXJ0Y2hpcCIpOwppZihfY2MpX2NjLm9uY2xpY2s9Y2FydFNoZWV0OwpyZW5k"
    "ZXIoKTtyZW5kZXJBZG1pbigpO2FwcGx5RmVhdHVyZXMoKTsKCi8qIOKUgOKUgCBsaXZlIGRhdGE6"
    "IGV2ZXJ5dGhpbmcgYmVsb3cgY29tZXMgZnJvbSB0aGUgYm90IGRhdGFiYXNlIHdoZW4gc2VydmVk"
    "IGJ5IHRoZSBib3Qg4pSA4pSAICovCi8qIExJVkUgLyBNRSAvIFRHIC8gSU5JVCBhcmUgZGVjbGFy"
    "ZWQgYXQgdGhlIHZlcnkgdG9wIG9mIHRoaXMgc2NyaXB0ICovCmFzeW5jIGZ1bmN0aW9uIGFwaShw"
    "YXRoLGJvZHkpewogIGNvbnN0IG89e2hlYWRlcnM6eyJDb250ZW50LVR5cGUiOiJhcHBsaWNhdGlv"
    "bi9qc29uIiwiWC1Jbml0LURhdGEiOklOSVQsCiAgICAiWC1UZWxlZ3JhbS1Jbml0LURhdGEiOklO"
    "SVQsIlgtQXBwLUtleSI6QVBQS0VZfX07CiAgaWYoYm9keSE9PXVuZGVmaW5lZCl7by5tZXRob2Q9"
    "IlBPU1QiO28uYm9keT1KU09OLnN0cmluZ2lmeShib2R5KTt9CiAgY29uc3Qgcj1hd2FpdCBmZXRj"
    "aChwYXRoLG8pOwogIGxldCBkPW51bGw7dHJ5e2Q9YXdhaXQgci5qc29uKCk7fWNhdGNoKGVycil7"
    "fQogIGlmKCFyLm9rKXtjb25zdCBlPW5ldyBFcnJvcigoZCYmZC5lcnJvcil8fChwYXRoKyIgIity"
    "LnN0YXR1cykpO2Uuc3RhdHVzPXIuc3RhdHVzO2UuZGF0YT1kO3Rocm93IGU7fQogIHJldHVybiBk"
    "Owp9CmZ1bmN0aW9uIGFkbWluQWxsb3dlZCgpe3JldHVybiAhTElWRSB8fCAhIShNRSYmTUUuYWRt"
    "aW4pO30KZnVuY3Rpb24gYXBwbHlTdGF0ZShkKXsKICAvKiBldmVyeSBsaXN0IGlzIHJlcGxhY2Vk"
    "LCBldmVuIHdoZW4gZW1wdHkg4oCUIGEgbmV3IHNob3AgbXVzdCBuZXZlciBzaG93IGRlbW8gZGF0"
    "YSAqLwogIGlmKGQuZmVhdHVyZXMpT2JqZWN0LmtleXMoZC5mZWF0dXJlcykuZm9yRWFjaChrPT57"
    "aWYoayBpbiBGRUFUKUZFQVRba109ISFkLmZlYXR1cmVzW2tdO30pOwogIGlmKGQuY29udGVudClP"
    "YmplY3Qua2V5cyhkLmNvbnRlbnQpLmZvckVhY2goaz0+e2lmKGsgaW4gQ01TJiZkLmNvbnRlbnRb"
    "a10hPW51bGwpQ01TW2tdPWQuY29udGVudFtrXTt9KTsKICBpZihBcnJheS5pc0FycmF5KGQuc3Rv"
    "Y2spKXtTVE9DSy5sZW5ndGg9MDtkLnN0b2NrLmZvckVhY2goeD0+U1RPQ0sucHVzaCh4KSk7RklM"
    "VEVSRUQ9U1RPQ0suc2xpY2UoKTt9CiAgaWYoQXJyYXkuaXNBcnJheShkLm9yZGVycykpe09SREVS"
    "Uy5sZW5ndGg9MDtkLm9yZGVycy5mb3JFYWNoKHg9Pk9SREVSUy5wdXNoKHgpKTt9CiAgaWYoQXJy"
    "YXkuaXNBcnJheShkLnR4KSl7VFgubGVuZ3RoPTA7ZC50eC5mb3JFYWNoKHg9PlRYLnB1c2goeCkp"
    "O30KICBpZihBcnJheS5pc0FycmF5KGQuZmVlZCkpRkVFRD1kLmZlZWQ7CiAgaWYoZC5lYXJuKUVB"
    "Uk49T2JqZWN0LmFzc2lnbih7fSxFQVJOLGQuZWFybik7CiAgaWYoZC5wcmVmcylQUkVGUz1PYmpl"
    "Y3QuYXNzaWduKHt9LFBSRUZTLGQucHJlZnMpOwogIGlmKEFycmF5LmlzQXJyYXkoZC5hbGVydHMp"
    "KXdpbmRvdy5fX2FsZXJ0cz1kLmFsZXJ0czsKICBpZihkLnN0b3JlKXsKICAgIFNUT1JFPU9iamVj"
    "dC5hc3NpZ24oe30sU1RPUkUsZC5zdG9yZSk7CiAgICBpZihkLnN0b3JlLm5hbWUpQ0ZHLm5hbWU9"
    "ZC5zdG9yZS5uYW1lOwogICAgaWYoZC5zdG9yZS50YWcpQ0ZHLnRhZz1kLnN0b3JlLnRhZzsKICAg"
    "IGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXN0b3JlbmFtZV0iKS5mb3JFYWNoKGU9"
    "PmUudGV4dENvbnRlbnQ9Q0ZHLm5hbWUpOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgi"
    "W2RhdGEtc3RvcmV0YWddIikuZm9yRWFjaChlPT5lLnRleHRDb250ZW50PUNGRy50YWcpOwogICAg"
    "ZG9jdW1lbnQudGl0bGU9Q0ZHLm5hbWU7CiAgfQogIGlmKGQuYWRtKXsKICAgIEFETT1PYmplY3Qu"
    "YXNzaWduKHt9LEFETSxkLmFkbSk7CiAgICBpZihBcnJheS5pc0FycmF5KEFETS5jaGFubmVscykp"
    "CiAgICAgIENIQU5ORUxTPUFETS5jaGFubmVscy5tYXAoYz0+KHtpZDpjLmlkLG5hbWU6Yy5uYW1l"
    "LGhhbmRsZTpjLmhhbmRsZXx8IiIsCiAgICAgICAgcmVxOiEhYy5yZXF1aXJlZCxvbjohIWMuYWN0"
    "aXZlLG1lbWJlcnM6IuKAlCJ9KSk7CiAgICBpZihBRE0ubG9ncyl7CiAgICAgIExPR1NfTUFTVEVS"
    "PSEhQURNLmxvZ3MubWFzdGVyOwogICAgICBpZihBcnJheS5pc0FycmF5KEFETS5sb2dzLmNoYW5u"
    "ZWxzKSkKICAgICAgICBMT0dDSD1BRE0ubG9ncy5jaGFubmVscy5tYXAoYz0+KHtpZDpjLmlkLG5h"
    "bWU6Yy5uYW1lLGhhbmRsZToiIiwKICAgICAgICAgIHByaW1hcnk6ISFjLnByaW1hcnksb246ISFj"
    "LmFjdGl2ZX0pKTsKICAgICAgaWYoQXJyYXkuaXNBcnJheShBRE0ubG9ncy5ldmVudHMpJiZBRE0u"
    "bG9ncy5ldmVudHMubGVuZ3RoKQogICAgICAgIEVWRU5UUz1BRE0ubG9ncy5ldmVudHMubWFwKGU9"
    "Pih7azplLmxhYmVsLG9uOiEhZS5vbixjaDooTE9HQ0hbMF18fHt9KS5pZHx8MX0pKTsKICAgIH0K"
    "ICB9CiAgaWYoZC5yYXRpbmdzKVJFVklFV1M9ZC5yYXRpbmdzOwogIGlmKHR5cGVvZiBkLmJhbGFu"
    "Y2U9PT0ibnVtYmVyIilCQUw9ZC5iYWxhbmNlOwogIGlmKGQubWUpTUU9ZC5tZTsKICBpZihkLmNh"
    "cnQpQ0FSVD1kLmNhcnQ7CiAgaWYoZC53aXNobGlzdClXSVNIPWQud2lzaGxpc3Q7Cn0KZnVuY3Rp"
    "b24gc2hvd1N3aXRjaChvbil7CiAgY29uc3Qgc3c9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN3"
    "Iik7CiAgaWYoc3cpc3cuc3R5bGUuZGlzcGxheT1vbj8iZmxleCI6Im5vbmUiOwp9CnZhciBMT0NL"
    "X1RJVExFPSIiLExPQ0tfTVNHPSIiOwpmdW5jdGlvbiBsb2NrZWRTY3JlZW4odGl0bGUsbXNnKXsK"
    "ICBMT0NLRUQ9dHJ1ZTtMT0NLX1RJVExFPXRpdGxlO0xPQ0tfTVNHPW1zZzsKICBzaG93U3dpdGNo"
    "KGZhbHNlKTsKICBwYWludExvY2tlZCgpOwp9CmZ1bmN0aW9uIHBhaW50TG9ja2VkKCl7CiAgY29u"
    "c3Qgc3Q9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0YWdlIik7CiAgaWYoc3Qpc3QuaW5uZXJI"
    "VE1MPSc8ZGl2IGNsYXNzPSJjYXJkIiBzdHlsZT0ibWFyZ2luOjE4cHg7dGV4dC1hbGlnbjpjZW50"
    "ZXI7cGFkZGluZzoyMnB4Ij4nCiAgICArJzxoMyBzdHlsZT0ibWFyZ2luOjAgMCA4cHgiPicrTE9D"
    "S19USVRMRSsnPC9oMz4nCiAgICArJzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCAwIDE0"
    "cHgiPicrTE9DS19NU0crJzwvcD4nCiAgICArJzxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgb25j"
    "bGljaz0ibG9jYXRpb24ucmVsb2FkKCkiPlRyeSBhZ2FpbjwvYnV0dG9uPjwvZGl2Pic7CiAgWyJ0"
    "YWJzIiwiZG9jayJdLmZvckVhY2goaWQ9Pntjb25zdCBlPWRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KGlkKTtpZihlKWUuc3R5bGUuZGlzcGxheT0ibm9uZSI7fSk7Cn0KYXN5bmMgZnVuY3Rpb24gYXBp"
    "VHJ5KHBhdGgsYm9keSl7CiAgdHJ5e3JldHVybiBhd2FpdCBhcGkocGF0aCxib2R5KTt9CiAgY2F0"
    "Y2goZSl7dG9hc3QoU3RyaW5nKGUmJmUubWVzc2FnZXx8ZSkuc2xpY2UoMCw5MCkpO3JldHVybiBu"
    "dWxsO30KfQphc3luYyBmdW5jdGlvbiByZWZyZXNoKCl7CiAgaWYoIUxJVkUpcmV0dXJuOwogIHRy"
    "eXsKICAgIGFwcGx5U3RhdGUoYXdhaXQgYXBpKCIvYXBpL3N0YXRlIikpOwogICAgYXBwbHlDTVMo"
    "KTthcHBseUZlYXR1cmVzKCk7c3luY0JhbCgpO3JlbmRlcigpO3JlbmRlckFkbWluKCk7CiAgfWNh"
    "dGNoKGUpe30KfQphc3luYyBmdW5jdGlvbiBjb3B5VGV4dCh0LCBsYWJlbCl7CiAgY29uc3QgdHh0"
    "PVN0cmluZyh0fHwiIik7CiAgaWYoIXR4dCl7dG9hc3QoIk5vdGhpbmcgdG8gY29weSIpO3JldHVy"
    "biBmYWxzZTt9CiAgdHJ5ewogICAgaWYobmF2aWdhdG9yLmNsaXBib2FyZCYmd2luZG93LmlzU2Vj"
    "dXJlQ29udGV4dCl7YXdhaXQgbmF2aWdhdG9yLmNsaXBib2FyZC53cml0ZVRleHQodHh0KTt9CiAg"
    "ICBlbHNlewogICAgICBjb25zdCB0YT1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCJ0ZXh0YXJlYSIp"
    "OwogICAgICB0YS52YWx1ZT10eHQ7dGEuc2V0QXR0cmlidXRlKCJyZWFkb25seSIsIiIpOwogICAg"
    "ICB0YS5zdHlsZS5jc3NUZXh0PSJwb3NpdGlvbjpmaXhlZDtsZWZ0Oi05OTk5cHg7b3BhY2l0eTow"
    "IjsKICAgICAgZG9jdW1lbnQuYm9keS5hcHBlbmRDaGlsZCh0YSk7dGEuc2VsZWN0KCk7dGEuc2V0"
    "U2VsZWN0aW9uUmFuZ2UoMCx0eHQubGVuZ3RoKTsKICAgICAgY29uc3Qgb2s9ZG9jdW1lbnQuZXhl"
    "Y0NvbW1hbmQoImNvcHkiKTtkb2N1bWVudC5ib2R5LnJlbW92ZUNoaWxkKHRhKTsKICAgICAgaWYo"
    "IW9rKXRocm93IG5ldyBFcnJvcigiY29weSByZWZ1c2VkIik7CiAgICB9CiAgICB0b2FzdCgobGFi"
    "ZWx8fCJDb3BpZWQiKSsiOiAiKyh0eHQubGVuZ3RoPjI2P3R4dC5zbGljZSgwLDI2KSsi4oCmIjp0"
    "eHQpKTsKICAgIHJldHVybiB0cnVlOwogIH1jYXRjaChlKXsKICAgIHNoZWV0KGA8aDMgc3R5bGU9"
    "Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5Db3B5IHRoaXM8L2gzPgogICAgICA8cCBj"
    "bGFzcz0ic3ViIj5Zb3VyIGJyb3dzZXIgYmxvY2tlZCB0aGUgY2xpcGJvYXJkIOKAlCBzZWxlY3Qg"
    "YW5kIGNvcHkuPC9wPgogICAgICA8dGV4dGFyZWEgY2xhc3M9ImlucCIgcm93cz0iMyIgcmVhZG9u"
    "bHkgb25jbGljaz0idGhpcy5zZWxlY3QoKSI+JHtlc2ModHh0KX08L3RleHRhcmVhPgogICAgICA8"
    "YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiIGRhdGEtY2xv"
    "c2U+RG9uZTwvYnV0dG9uPmApOwogICAgcmV0dXJuIGZhbHNlOwogIH0KfQpsZXQgU1RPUkU9e3Vw"
    "aToiIixwYXllZToiIixwcmVmaXg6IlBBWSIsdGVybXM6IiIsc3VwcG9ydDoiIn07CmxldCBQUkVG"
    "Uz17cmVzdG9jazp0cnVlLHJlY2VpcHRzOnRydWUscHJvbW9zOnRydWUscHVzaDp0cnVlfTsKbGV0"
    "IE9GRkVSPSIiOwpmdW5jdGlvbiBvcGVuUGF5KGxpbmspewogIGlmKCFsaW5rKXJldHVybjsKICB0"
    "cnl7CiAgICBpZihURyYmVEcub3BlbkxpbmsmJi9eaHR0cHM/Oi8udGVzdChsaW5rKSlURy5vcGVu"
    "TGluayhsaW5rKTsKICAgIGVsc2Ugd2luZG93LmxvY2F0aW9uLmhyZWY9bGluazsKICB9Y2F0Y2go"
    "ZSl7d2luZG93LmxvY2F0aW9uLmhyZWY9bGluazt9Cn0KYXN5bmMgZnVuY3Rpb24gYm9vdCgpewog"
    "IGlmKFRHKXt0cnl7VEcucmVhZHkoKTtURy5leHBhbmQoKTt9Y2F0Y2goZSl7fX0KICBjb25zdCBp"
    "blRHPSEhKChJTklUJiZJTklULmxlbmd0aCl8fChBUFBLRVkmJkFQUEtFWS5sZW5ndGgpKTsKICBp"
    "ZihpblRHKXNob3dTd2l0Y2goZmFsc2UpOyAgICAgICAgICAgICAgLyogZmFpbCBjbG9zZWQ6IG5v"
    "IGFkbWluIHN0cmlwIHVudGlsIHByb3ZlbiAqLwogIGlmKGxvY2F0aW9uLnByb3RvY29sPT09ImZp"
    "bGU6IilyZXR1cm47ICAvKiBvcGVuZWQgYXMgYSBwbGFpbiBmaWxlIOKAlCBkZW1vIGdhbGxlcnkg"
    "Ki8KICBsZXQgZDsKICB0cnl7CiAgICBkPWF3YWl0IGFwaSgiL2FwaS9zdGF0ZSIpOwogIH1jYXRj"
    "aChlKXsKICAgIGlmKGUmJmUuc3RhdHVzPT09NDAxKXsKICAgICAgbGV0IHdoeT0iIixmaXg9IiI7"
    "CiAgICAgIHRyeXsKICAgICAgICBjb25zdCB3PWF3YWl0IChhd2FpdCBmZXRjaCgiL2FwaS93aG9h"
    "bWkiLHtoZWFkZXJzOnsiWC1Jbml0LURhdGEiOklOSVQsCiAgICAgICAgICAiWC1UZWxlZ3JhbS1J"
    "bml0LURhdGEiOklOSVQsIlgtQXBwLUtleSI6QVBQS0VZfX0pKS5qc29uKCk7CiAgICAgICAgY29u"
    "c3QgUj17bWlzc2luZzpbIlRlbGVncmFtIGRpZCBub3QgcGFzcyB5b3VyIGFjY291bnQgdG8gdGhp"
    "cyBwYWdlLiIsCiAgICAgICAgICAgICAgICAgICAiT3BlbiB0aGUgYm90IGFuZCB0YXAgPGI+T3Bl"
    "biBTdG9yZTwvYj4sIG9yIHNlbmQgPGI+L2FwcDwvYj4uIl0sCiAgICAgICAgICAgICAgICAgImJh"
    "ZC1mb3JtYXQiOlsiVGhlIHNpZ24taW4gZGF0YSBhcnJpdmVkIGRhbWFnZWQuIiwiQ2xvc2UgdGhl"
    "IGFwcCBhbmQgb3BlbiBpdCBhZ2FpbiBmcm9tIHRoZSBib3QuIl0sCiAgICAgICAgICAgICAgICAg"
    "Im5vLWhhc2giOlsiVGhlIHNpZ24taW4gZGF0YSBhcnJpdmVkIGluY29tcGxldGUuIiwiQ2xvc2Ug"
    "dGhlIGFwcCBhbmQgb3BlbiBpdCBhZ2FpbiBmcm9tIHRoZSBib3QuIl0sCiAgICAgICAgICAgICAg"
    "ICAgImJhZC1oYXNoIjpbIlRoZSBzaWduYXR1cmUgZG9lcyBub3QgbWF0Y2ggdGhpcyBib3QncyB0"
    "b2tlbi4iLAogICAgICAgICAgICAgICAgICAgIlRoZSBwYWdlIGlzIHNlcnZlZCBieSBhIGRpZmZl"
    "cmVudCBib3QsIG9yIEJPVF9UT0tFTiBvbiB0aGUgc2VydmVyIGhhcyBxdW90ZXMgYXJvdW5kIGl0"
    "LiBTZW5kIDxiPi9kaWFnPC9iPiBpbiB0aGUgYm90IHRvIGNvbXBhcmUuIl0sCiAgICAgICAgICAg"
    "ICAgICAgc3RhbGU6WyJUaGF0IHNpZ24taW4gaXMgb2xkZXIgdGhhbiB0aGUgYWxsb3dlZCB3aW5k"
    "b3cuIiwiUmVvcGVuIHRoZSBhcHAgZnJvbSB0aGUgYm90LiJdLAogICAgICAgICAgICAgICAgICJi"
    "YWQtdXNlciI6WyJZb3VyIGFjY291bnQgZGV0YWlscyBjb3VsZCBub3QgYmUgcmVhZC4iLCJSZW9w"
    "ZW4gdGhlIGFwcCBmcm9tIHRoZSBib3QuIl0sCiAgICAgICAgICAgICAgICAgIm5vLWlkIjpbIlRl"
    "bGVncmFtIHNlbnQgbm8gdXNlciBpZC4iLCJSZW9wZW4gdGhlIGFwcCBmcm9tIHRoZSBib3QuIl19"
    "OwogICAgICAgIGNvbnN0IHI9Ult3LndoeV18fFsiVGhlIHN0b3JlIGNvdWxkIG5vdCBjb25maXJt"
    "IHdobyB5b3UgYXJlLiIsIk9wZW4gaXQgYWdhaW4gZnJvbSB0aGUgYm90LiJdOwogICAgICAgIHdo"
    "eT1yWzBdKyIgPGNvZGU+KCIrKHcud2h5fHwidW5rbm93biIpKyIpPC9jb2RlPiI7CiAgICAgICAg"
    "Zml4PXJbMV07CiAgICAgIH1jYXRjaChlcnIpewogICAgICAgIHdoeT0iVGhlIHN0b3JlIGNvdWxk"
    "IG5vdCBjb25maXJtIHdobyB5b3UgYXJlLiI7CiAgICAgICAgZml4PSJPcGVuIGl0IGFnYWluIGZy"
    "b20gdGhlIGJvdCB3aXRoIHRoZSA8Yj5PcGVuIFN0b3JlPC9iPiBidXR0b24uIjsKICAgICAgfQog"
    "ICAgICBsb2NrZWRTY3JlZW4oIkNhbm5vdCBvcGVuIHRoZSBzdG9yZSIsd2h5KyI8YnI+PGJyPiIr"
    "Zml4KTsKICAgIH0KICAgIHJldHVybjsKICB9CiAgYXBwbHlTdGF0ZShkKTtMSVZFPXRydWU7CiAg"
    "c2hvd1N3aXRjaCghIShNRSYmTUUuYWRtaW4pKTsgICAgICAgICAgIC8qIG9ubHkgYSB2ZXJpZmll"
    "ZCBhZG1pbiBzZWVzIGl0ICovCiAgYXBwbHlDTVMoKTthcHBseUZlYXR1cmVzKCk7c3luY0JhbCgp"
    "O3JlbmRlcigpO3JlbmRlckFkbWluKCk7Cn0KYXN5bmMgZnVuY3Rpb24gcHVzaChwYXRoLGJvZHks"
    "b2tNc2cpewogIGlmKCFMSVZFKXJldHVybiB0cnVlOwogIHRyeXtjb25zdCByPWF3YWl0IGFwaShw"
    "YXRoLGJvZHkpO2lmKG9rTXNnKXRvYXN0KG9rTXNnKTtyZXR1cm4gcjt9CiAgY2F0Y2goZSl7dG9h"
    "c3QoIkNvdWxkIG5vdCBzYXZlIOKAlCBjaGVjayB5b3VyIGNvbm5lY3Rpb24iKTtyZXR1cm4gZmFs"
    "c2U7fQp9CmJvb3QoKTsKPC9zY3JpcHQ+CjwvYm9keT48L2h0bWw+Cg=="
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
