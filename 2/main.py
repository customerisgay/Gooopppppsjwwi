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
DESIGN = "2-brutal"


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
    "store_tagline": "No nonsense, fast",
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
    "storeTag": "No nonsense, fast",
    "tick1": "<b>Instant delivery</b> on every country",
    "tick2": "Wallet top-ups <b>auto-verified</b>",
    "tick3": "Failed login? <b>Auto refund</b>",
    "tick4": "Daily check-in <b>bonus</b>",
    "heroKick": "FRESH STOCK",
    "heroH1": "BUY.<br>GET OTP.<br>DONE.",
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
    "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIiBkYXRhLWRlc2lnbj0iYnJ1dGFsIj4KPGhl"
    "YWQ+CjxtZXRhIGNoYXJzZXQ9InV0Zi04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQiIGNvbnRlbnQ9"
    "IndpZHRoPWRldmljZS13aWR0aCxpbml0aWFsLXNjYWxlPTEsdmlld3BvcnQtZml0PWNvdmVyLG1h"
    "eGltdW0tc2NhbGU9MSI+CjxzY3JpcHQgc3JjPSJodHRwczovL3RlbGVncmFtLm9yZy9qcy90ZWxl"
    "Z3JhbS13ZWItYXBwLmpzIj48L3NjcmlwdD4KPHRpdGxlPlpvcm8gVEcgU3RvcmUg4oCUIFN0b3Jl"
    "IE1pbmkgQXBwIChEZXNpZ24gMik8L3RpdGxlPgo8c3R5bGU+Cjpyb290ewogIC0tYmc6I2ZmZjhl"
    "NjsgLS1iZzI6I2ZmZmRmNjsgLS1jYXJkOiNmZmZmZmY7IC0tY2FyZDI6I2ZmZjJjYzsKICAtLWxp"
    "bmU6IzExMTExMTsgLS10eDojMTExMTExOyAtLXR4MjojNWM1NjQ4OyAtLWFjYzojZmY1YzJiOyAt"
    "LWFjYzI6IzExMTExMTsKICAtLW9rOiMxNmEzNGE7IC0td2FybjojZDk3NzA2OyAtLWJhZDojZTEx"
    "ZDQ4OwogIC0tcjoxNHB4OyAtLXJzOjEwcHg7IC0tYnRuaDo0NHB4OyAtLWZvbnQ6J0FyY2hpdm8n"
    "LCdIZWx2ZXRpY2EgTmV1ZScsQXJpYWwsc2Fucy1zZXJpZjsgLS1maGVhZDonQXJjaGl2byBCbGFj"
    "aycsJ0FyaWFsIEJsYWNrJyxBcmlhbCxzYW5zLXNlcmlmOwogIC0tc2hhZG93OjRweCA0cHggMCAj"
    "MTExMTExOyAtLXBpbGxncmFkOiMxMTExMTE7IC0tYnRuYmc6IzExMTExMTsKfQoqe2JveC1zaXpp"
    "bmc6Ym9yZGVyLWJveDstd2Via2l0LXRhcC1oaWdobGlnaHQtY29sb3I6dHJhbnNwYXJlbnR9Cmh0"
    "bWwsYm9keXttYXJnaW46MDtwYWRkaW5nOjB9CmJvZHl7YmFja2dyb3VuZDp2YXIoLS1iZyk7Y29s"
    "b3I6dmFyKC0tdHgpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZToxNXB4O2xpbmUt"
    "aGVpZ2h0OjEuNDU7bWluLWhlaWdodDoxMDB2aDtvdmVyZmxvdy14OmhpZGRlbjtwYWRkaW5nLXRv"
    "cDozMnB4O30KYm9keTpiZWZvcmV7Y29udGVudDoiIjtwb3NpdGlvbjpmaXhlZDtpbnNldDowO3Bv"
    "aW50ZXItZXZlbnRzOm5vbmU7ei1pbmRleDowO2JhY2tncm91bmQtaW1hZ2U6bGluZWFyLWdyYWRp"
    "ZW50KCMxMTExMTEwOCAxcHgsdHJhbnNwYXJlbnQgMXB4KSxsaW5lYXItZ3JhZGllbnQoOTBkZWcs"
    "IzExMTExMTA4IDFweCx0cmFuc3BhcmVudCAxcHgpO2JhY2tncm91bmQtc2l6ZToyNnB4IDI2cHh9"
    "Cjo6LXdlYmtpdC1zY3JvbGxiYXJ7d2lkdGg6MDtoZWlnaHQ6MH0KI3NoZWxse3Bvc2l0aW9uOnJl"
    "bGF0aXZlO3otaW5kZXg6MTttYXgtd2lkdGg6NTIwcHg7bWFyZ2luOjAgYXV0bzttaW4taGVpZ2h0"
    "OjEwMHZoO3BhZGRpbmc6MCAxNnB4IDk2cHh9Ci5oaWRkZW57ZGlzcGxheTpub25lIWltcG9ydGFu"
    "dH0KCiN0b3BiYXJ7cG9zaXRpb246c3RpY2t5O3RvcDozMnB4O3otaW5kZXg6NDA7ZGlzcGxheTpm"
    "bGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjE0cHggMCAxMnB4O2JhY2tn"
    "cm91bmQ6bGluZWFyLWdyYWRpZW50KHZhcigtLWJnKSA3MiUsdHJhbnNwYXJlbnQpfQoubG9nb3t3"
    "aWR0aDozOHB4O2hlaWdodDozOHB4O2JvcmRlci1yYWRpdXM6MTBweDtkaXNwbGF5OmdyaWQ7cGxh"
    "Y2UtaXRlbXM6Y2VudGVyO2ZsZXg6MCAwIGF1dG87YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7"
    "Y29sb3I6I2ZmZmZmZjtmb250LXdlaWdodDo4MDA7Zm9udC1zaXplOi45cmVtO2xldHRlci1zcGFj"
    "aW5nOi0uMDNlbTt9Ci5icmFuZHttaW4td2lkdGg6MDtmbGV4OjF9Ci5icmFuZCBie2Rpc3BsYXk6"
    "YmxvY2s7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZTouOTVyZW07Zm9udC13ZWln"
    "aHQ6OTAwO2xldHRlci1zcGFjaW5nOi0uMDJlbTt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6"
    "aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXN9Ci5icmFuZCBzcGFue2Rpc3BsYXk6YmxvY2s7"
    "Zm9udC1zaXplOi42NnJlbTtjb2xvcjp2YXIoLS10eDIpO2xldHRlci1zcGFjaW5nOi4wNmVtO3Rl"
    "eHQtdHJhbnNmb3JtOnVwcGVyY2FzZX0KLmJhbGNoaXB7ZGlzcGxheTppbmxpbmUtZmxleDthbGln"
    "bi1pdGVtczpjZW50ZXI7Z2FwOjdweDtoZWlnaHQ6MzhweDtwYWRkaW5nOjAgMTNweDtib3JkZXIt"
    "cmFkaXVzOjEwcHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MnB4IHNvbGlkIHZhcigt"
    "LWxpbmUpO2NvbG9yOnZhcigtLXR4KTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXdlaWdo"
    "dDo3MDA7Zm9udC1zaXplOi44NXJlbTt3aGl0ZS1zcGFjZTpub3dyYXA7Y3Vyc29yOnBvaW50ZXI7"
    "Ym94LXNoYWRvdzozcHggM3B4IDAgIzExMTExMX0KLmJhbGNoaXAgLnBsdXN7ZGlzcGxheTpncmlk"
    "O3BsYWNlLWl0ZW1zOmNlbnRlcjt3aWR0aDoyMHB4O2hlaWdodDoyMHB4O2JvcmRlci1yYWRpdXM6"
    "N3B4O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiNmZmZmZmY7Zm9udC13ZWlnaHQ6"
    "ODAwO2ZvbnQtc2l6ZTouOHJlbTtsaW5lLWhlaWdodDoxfQoKLmNhcmR7YmFja2dyb3VuZDp2YXIo"
    "LS1jYXJkKTtib3JkZXI6MnB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6dmFyKC0t"
    "cik7cGFkZGluZzoxNnB4O21hcmdpbi1ib3R0b206MTJweDtib3gtc2hhZG93OnZhcigtLXNoYWRv"
    "dyk7fQouY2FyZC50aWdodHtwYWRkaW5nOjEzcHh9Ci5jYXJkIGgze21hcmdpbjowIDAgM3B4O2Zv"
    "bnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6LjkycmVtO2ZvbnQtd2VpZ2h0OjkwMDts"
    "ZXR0ZXItc3BhY2luZzotLjAyZW19Ci5jYXJkIC5zdWJ7bWFyZ2luOjA7Y29sb3I6dmFyKC0tdHgy"
    "KTtmb250LXNpemU6Ljc4cmVtfQouc2Vje2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7"
    "anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47Z2FwOjEwcHg7bWFyZ2luOjE4cHggMnB4IDEw"
    "cHh9Ci5zZWMgaDJ7bWFyZ2luOjA7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZTou"
    "ODJyZW07Zm9udC13ZWlnaHQ6OTAwO2xldHRlci1zcGFjaW5nOi4xNGVtO3RleHQtdHJhbnNmb3Jt"
    "OnVwcGVyY2FzZTtjb2xvcjp2YXIoLS10eDIpfQouc2VjIGF7Zm9udC1zaXplOi43NXJlbTtjb2xv"
    "cjp2YXIoLS1hY2MpO3RleHQtZGVjb3JhdGlvbjpub25lO2ZvbnQtd2VpZ2h0OjYwMDt3aGl0ZS1z"
    "cGFjZTpub3dyYXA7Y3Vyc29yOnBvaW50ZXJ9CgovKiBvbmUgYnV0dG9uIHN5c3RlbSDigJQgdGV4"
    "dCBjYW4gbmV2ZXIgb3ZlcmZsb3cgKi8KLmJ0bntkaXNwbGF5OmlubGluZS1mbGV4O2FsaWduLWl0"
    "ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2dhcDo3cHg7aGVpZ2h0OnZhcigtLWJ0"
    "bmgpO21pbi13aWR0aDowO21heC13aWR0aDoxMDAlO3BhZGRpbmc6MCAxNnB4O2JvcmRlcjowO2N1"
    "cnNvcjpwb2ludGVyO2JvcmRlci1yYWRpdXM6MTBweDtmb250LWZhbWlseTp2YXIoLS1mb250KTtm"
    "b250LXNpemU6LjgzcmVtO2ZvbnQtd2VpZ2h0OjcwMDtsZXR0ZXItc3BhY2luZzouMDFlbTt3aGl0"
    "ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXM7bGlu"
    "ZS1oZWlnaHQ6MTtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojZmZmZmZmO3RyYW5z"
    "aXRpb246dHJhbnNmb3JtIC4xNXMsZmlsdGVyIC4xNXM7Ym94LXNoYWRvdzozcHggM3B4IDAgIzEx"
    "MTExMX0KLmJ0bjphY3RpdmV7dHJhbnNmb3JtOnNjYWxlKC45Nyl9Ci5idG46aG92ZXJ7ZmlsdGVy"
    "OmJyaWdodG5lc3MoMS4wNil9Ci5idG4gc3Zne3dpZHRoOjE2cHg7aGVpZ2h0OjE2cHg7ZmxleDow"
    "IDAgYXV0b30KLmJ0bi5ibG9ja3tkaXNwbGF5OmZsZXg7d2lkdGg6MTAwJX0KLmJ0bi5zbXtoZWln"
    "aHQ6MzZweDtwYWRkaW5nOjAgMTJweDtmb250LXNpemU6Ljc1cmVtO2JvcmRlci1yYWRpdXM6OHB4"
    "fQouYnRuLnhze2hlaWdodDozMHB4O3BhZGRpbmc6MCAxMHB4O2ZvbnQtc2l6ZTouN3JlbTtib3Jk"
    "ZXItcmFkaXVzOjhweH0KLmJ0bi5naG9zdHtiYWNrZ3JvdW5kOnRyYW5zcGFyZW50O2NvbG9yOnZh"
    "cigtLXR4KTtib3JkZXI6MnB4IHNvbGlkIHZhcigtLWxpbmUpO2JveC1zaGFkb3c6M3B4IDNweCAw"
    "ICMxMTExMTF9Ci5idG4uc29mdHtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10"
    "eCk7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1saW5lKX0KLmJ0bi5iYWR7YmFja2dyb3VuZDp0cmFu"
    "c3BhcmVudDtjb2xvcjp2YXIoLS1iYWQpO2JvcmRlcjoycHggc29saWQgdmFyKC0tYmFkKX0KLmJ0"
    "bltkaXNhYmxlZF17b3BhY2l0eTouNDU7cG9pbnRlci1ldmVudHM6bm9uZX0KLmJ0bnJvd3tkaXNw"
    "bGF5OmZsZXg7Z2FwOjhweH0KLmJ0bnJvdz4uYnRue2ZsZXg6MSAxIDA7bWluLXdpZHRoOjB9Cgou"
    "cGlsbHtkaXNwbGF5OmlubGluZS1mbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6NXB4O2hlaWdo"
    "dDoyNHB4O3BhZGRpbmc6MCA5cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2ZvbnQtc2l6ZTouNjZyZW07"
    "Zm9udC13ZWlnaHQ6NzAwO2xldHRlci1zcGFjaW5nOi4wNGVtO2JhY2tncm91bmQ6dmFyKC0tY2Fy"
    "ZDIpO2NvbG9yOnZhcigtLXR4Mik7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1saW5lKTt3aGl0ZS1z"
    "cGFjZTpub3dyYXB9Ci5waWxsLm9ue2NvbG9yOnZhcigtLW9rKTtib3JkZXItY29sb3I6IzE2YTM0"
    "YX0KLnBpbGwuaG90e2NvbG9yOnZhcigtLXdhcm4pO2JvcmRlci1jb2xvcjojZDk3NzA2fQoucGls"
    "bC5vZmZ7Y29sb3I6dmFyKC0tYmFkKTtib3JkZXItY29sb3I6I2UxMWQ0OH0KLmRvdHt3aWR0aDo3"
    "cHg7aGVpZ2h0OjdweDtib3JkZXItcmFkaXVzOjUwJTtiYWNrZ3JvdW5kOnZhcigtLW9rKTtib3gt"
    "c2hhZG93OjAgMCA4cHggdmFyKC0tb2spfQoua3Z7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNl"
    "bnRlcjtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjtnYXA6MTJweDtwYWRkaW5nOjEwcHgg"
    "MDtib3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIoLS1saW5lKTtmb250LXNpemU6LjgycmVtO2Nv"
    "bG9yOnZhcigtLXR4Mil9Ci5rdjpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206MH0KLmt2IGJ7Y29s"
    "b3I6dmFyKC0tdHgpO2ZvbnQtd2VpZ2h0OjcwMDt0ZXh0LWFsaWduOnJpZ2h0O292ZXJmbG93LXdy"
    "YXA6YW55d2hlcmV9Ci5mbGFne3dpZHRoOjQycHg7aGVpZ2h0OjQycHg7Ym9yZGVyLXJhZGl1czox"
    "MHB4O2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9udC1zaXplOjEuMzVyZW07Zmxl"
    "eDowIDAgYXV0bztiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtib3JkZXI6MnB4IHNvbGlkIHZhcigt"
    "LWxpbmUpfQouZmxhZyBzdmd7d2lkdGg6MThweDtoZWlnaHQ6MThweH0KCi5oZXJve3Bvc2l0aW9u"
    "OnJlbGF0aXZlO292ZXJmbG93OmhpZGRlbjtib3JkZXItcmFkaXVzOjE2cHg7cGFkZGluZzoyMnB4"
    "IDE4cHg7bWFyZ2luOjRweCAwIDE0cHg7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNr"
    "Z3JvdW5kOiNmZjVjMmI7Y29sb3I6I2ZmZmZmZn0KLmhlcm8gLmtpY2t7Zm9udC1zaXplOi42cmVt"
    "O2xldHRlci1zcGFjaW5nOi4yZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2NvbG9yOnZhcigt"
    "LWFjYyk7Zm9udC13ZWlnaHQ6ODAwfQouaGVybyBoMXttYXJnaW46OHB4IDAgNnB4O2ZvbnQtZmFt"
    "aWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6MS43NXJlbTtsaW5lLWhlaWdodDoxLjA1O2xldHRl"
    "ci1zcGFjaW5nOi0uMDJlbTtmb250LXdlaWdodDo5MDB9Ci5oZXJvIHB7bWFyZ2luOjAgMCAxNHB4"
    "O2ZvbnQtc2l6ZTouOHJlbTtjb2xvcjp2YXIoLS10eDIpO21heC13aWR0aDoyODBweH0KLmhlcm8g"
    "LmJ0bnJvd3ttYXgtd2lkdGg6MzMwcHh9CgouZ3JpZHtkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0"
    "ZS1jb2x1bW5zOjFmciAxZnI7Z2FwOjEwcHh9Ci5wY2FyZHtiYWNrZ3JvdW5kOnZhcigtLWNhcmQp"
    "O2JvcmRlcjoycHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJhZGl1czp2YXIoLS1yKTtwYWRk"
    "aW5nOjE0cHg7Ym94LXNoYWRvdzp2YXIoLS1zaGFkb3cpO2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVj"
    "dGlvbjpjb2x1bW47Z2FwOjlweDtjdXJzb3I6cG9pbnRlcjt9Ci5wY2FyZCAudG9we2Rpc3BsYXk6"
    "ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47Z2Fw"
    "OjhweH0KLnBjYXJkIC5ubXtmb250LXdlaWdodDo3MDA7Zm9udC1zaXplOi44NXJlbTt3aGl0ZS1z"
    "cGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXN9Ci5wY2Fy"
    "ZCAubWV0YXtmb250LXNpemU6LjY4cmVtO2NvbG9yOnZhcigtLXR4Mil9Ci5wY2FyZCAucHJpY2V7"
    "Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZToxLjI1cmVtO2ZvbnQtd2VpZ2h0Ojkw"
    "MDtsZXR0ZXItc3BhY2luZzotLjAyZW19Ci5wY2FyZCAucHJpY2Ugc3tmb250LXNpemU6LjcycmVt"
    "O2NvbG9yOnZhcigtLXR4Mik7Zm9udC13ZWlnaHQ6NDAwO21hcmdpbi1yaWdodDo2cHh9Ci5yb3dz"
    "e2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47Z2FwOjlweH0KLnJvd3tkaXNwbGF5"
    "OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMnB4O2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7"
    "Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXIpO3BhZGRp"
    "bmc6MTJweCAxM3B4O2JveC1zaGFkb3c6dmFyKC0tc2hhZG93KTtjdXJzb3I6cG9pbnRlcjt0cmFu"
    "c2l0aW9uOnRyYW5zZm9ybSAuMTVzO30KLnJvdzphY3RpdmV7dHJhbnNmb3JtOnNjYWxlKC45OSl9"
    "Ci5yb3cgLm1pZHtmbGV4OjE7bWluLXdpZHRoOjB9Ci5yb3cgLm1pZCBie2Rpc3BsYXk6YmxvY2s7"
    "Zm9udC1zaXplOi44OHJlbTt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3RleHQt"
    "b3ZlcmZsb3c6ZWxsaXBzaXN9Ci5yb3cgLm1pZCBzbWFsbHtkaXNwbGF5OmJsb2NrO2ZvbnQtc2l6"
    "ZTouN3JlbTtjb2xvcjp2YXIoLS10eDIpO21hcmdpbi10b3A6MnB4fQoucm93IC5ydHt0ZXh0LWFs"
    "aWduOnJpZ2h0O2ZsZXg6MCAwIGF1dG99Ci5yb3cgLnJ0IC5we2ZvbnQtd2VpZ2h0OjgwMDtmb250"
    "LXNpemU6Ljk1cmVtO3doaXRlLXNwYWNlOm5vd3JhcH0KLnJvdyAucnQgLnAgc3tmb250LXdlaWdo"
    "dDo0MDA7Zm9udC1zaXplOi43MnJlbTtjb2xvcjp2YXIoLS10eDIpO21hcmdpbi1yaWdodDo1cHh9"
    "Ci5jYXJvdXtkaXNwbGF5OmZsZXg7Z2FwOjEwcHg7b3ZlcmZsb3cteDphdXRvO3BhZGRpbmc6MnB4"
    "IDAgNnB4O21hcmdpbjowIC0xNnB4IDZweDtwYWRkaW5nLWxlZnQ6MTZweDtwYWRkaW5nLXJpZ2h0"
    "OjE2cHg7c2Nyb2xsLXNuYXAtdHlwZTp4IG1hbmRhdG9yeX0KLmNhcm91Pip7ZmxleDowIDAgMTcy"
    "cHg7c2Nyb2xsLXNuYXAtYWxpZ246c3RhcnR9CgpuYXYjdGFic3twb3NpdGlvbjpmaXhlZDt6LWlu"
    "ZGV4OjYwO2xlZnQ6NTAlO2JvdHRvbTpjYWxjKDEycHggKyBlbnYoc2FmZS1hcmVhLWluc2V0LWJv"
    "dHRvbSkpO3RyYW5zZm9ybTp0cmFuc2xhdGVYKC01MCUpO21heC13aWR0aDpjYWxjKDEwMHZ3IC0g"
    "MjBweCl9Cm5hdiN0YWJzIC5kb2Nre2Rpc3BsYXk6ZmxleDtnYXA6MnB4O3BhZGRpbmc6N3B4IDhw"
    "eDtib3JkZXItcmFkaXVzOjBweDtiYWNrZ3JvdW5kOiNmZmZmZmY7Ym9yZGVyOjJweCBzb2xpZCB2"
    "YXIoLS1saW5lKTtib3gtc2hhZG93OjAgLTRweCAwICMxMTExMTF9Cm5hdiN0YWJzIGJ1dHRvbntw"
    "b3NpdGlvbjpyZWxhdGl2ZTtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1uO2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2dhcDo0cHg7bWluLXdpZHRoOjU4"
    "cHg7aGVpZ2h0OjQ4cHg7cGFkZGluZzowIDZweDtib3JkZXI6MDtiYWNrZ3JvdW5kOnRyYW5zcGFy"
    "ZW50O2N1cnNvcjpwb2ludGVyO2NvbG9yOnZhcigtLXR4Mik7Zm9udC1mYW1pbHk6dmFyKC0tZm9u"
    "dCk7Zm9udC1zaXplOi42cmVtO2ZvbnQtd2VpZ2h0OjcwMDtsZXR0ZXItc3BhY2luZzouMDNlbTti"
    "b3JkZXItcmFkaXVzOjEwcHg7dHJhbnNpdGlvbjpjb2xvciAuMnMsYmFja2dyb3VuZCAuMnM7d2hp"
    "dGUtc3BhY2U6bm93cmFwfQpuYXYjdGFicyBidXR0b24gc3Zne3dpZHRoOjE5cHg7aGVpZ2h0OjE5"
    "cHh9Cm5hdiN0YWJzIGJ1dHRvbi5vbntjb2xvcjojZmZmZmZmO2JhY2tncm91bmQ6dmFyKC0tcGls"
    "bGdyYWQpfQoKLnN1Ym5hdntkaXNwbGF5OmZsZXg7Z2FwOjdweDtvdmVyZmxvdy14OmF1dG87cGFk"
    "ZGluZzoycHggMCAxMnB4O21hcmdpbjowIC0ycHh9Ci5zdWJuYXYgYnV0dG9ue2ZsZXg6MCAwIGF1"
    "dG87aGVpZ2h0OjM0cHg7cGFkZGluZzowIDEzcHg7Ym9yZGVyLXJhZGl1czo4cHg7Ym9yZGVyOjJw"
    "eCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10"
    "eDIpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouNzRyZW07Zm9udC13ZWlnaHQ6"
    "NzAwO2N1cnNvcjpwb2ludGVyO3doaXRlLXNwYWNlOm5vd3JhcH0KLnN1Ym5hdiBidXR0b24ub257"
    "YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmZmZjtib3JkZXItY29sb3I6dHJh"
    "bnNwYXJlbnR9Ci5rcGlze2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyIDFm"
    "cjtnYXA6MTBweDttYXJnaW4tYm90dG9tOjEycHh9Ci5rcGl7YmFja2dyb3VuZDp2YXIoLS1jYXJk"
    "KTtib3JkZXI6MnB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO3Bh"
    "ZGRpbmc6MTNweDtib3gtc2hhZG93OnZhcigtLXNoYWRvdyl9Ci5rcGkgc21hbGx7ZGlzcGxheTpi"
    "bG9jaztmb250LXNpemU6LjY0cmVtO2NvbG9yOnZhcigtLXR4Mik7bGV0dGVyLXNwYWNpbmc6LjFl"
    "bTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2V9Ci5rcGkgYntkaXNwbGF5OmJsb2NrO21hcmdpbi10"
    "b3A6NXB4O2ZvbnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6MS4zNXJlbTtmb250LXdl"
    "aWdodDo5MDA7bGV0dGVyLXNwYWNpbmc6LS4wMmVtfQouaW5wLHNlbGVjdC5pbnB7d2lkdGg6MTAw"
    "JTtoZWlnaHQ6dmFyKC0tYnRuaCk7cGFkZGluZzowIDEzcHg7Ym9yZGVyLXJhZGl1czoxMHB4O2Jv"
    "cmRlcjoycHggc29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6"
    "dmFyKC0tdHgpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouODNyZW07b3V0bGlu"
    "ZTpub25lOy13ZWJraXQtYXBwZWFyYW5jZTpub25lO2FwcGVhcmFuY2U6bm9uZX0Kc2VsZWN0Lmlu"
    "cHtiYWNrZ3JvdW5kLWltYWdlOm5vbmV9Ci5pbnA6OnBsYWNlaG9sZGVye2NvbG9yOnZhcigtLXR4"
    "Mik7b3BhY2l0eTouNzV9Ci5pbnA6Zm9jdXN7Ym9yZGVyLWNvbG9yOnZhcigtLWFjYyl9CnRleHRh"
    "cmVhLmlucHtoZWlnaHQ6YXV0bzttaW4taGVpZ2h0OjQwcHg7cGFkZGluZzoxMHB4IDEzcHg7bGlu"
    "ZS1oZWlnaHQ6MS41O3Jlc2l6ZTp2ZXJ0aWNhbH0KLmNtc3Jvdy5oaWRle2Rpc3BsYXk6bm9uZX0K"
    "Lmljb25jaGlwe3Bvc2l0aW9uOnJlbGF0aXZlO2hlaWdodDozNHB4O21pbi13aWR0aDozNHB4O3Bh"
    "ZGRpbmc6MCA5cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2JvcmRlcjoycHggc29saWQgdmFyKC0tbGlu"
    "ZSk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgpO2Rpc3BsYXk6aW5saW5l"
    "LWdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2N1cnNvcjpwb2ludGVyfQouaWNvbmNoaXAgc3Zne3dp"
    "ZHRoOjE3cHg7aGVpZ2h0OjE3cHh9Ci5pY29uY2hpcCBpe3Bvc2l0aW9uOmFic29sdXRlO3RvcDot"
    "NXB4O3JpZ2h0Oi01cHg7bWluLXdpZHRoOjE3cHg7aGVpZ2h0OjE3cHg7cGFkZGluZzowIDRweDti"
    "b3JkZXItcmFkaXVzOjk5cHg7YmFja2dyb3VuZDp2YXIoLS1hY2MpO2NvbG9yOiNmZmZmZmY7Zm9u"
    "dC1zaXplOi42cmVtO2ZvbnQtc3R5bGU6bm9ybWFsO2ZvbnQtd2VpZ2h0OjgwMDtkaXNwbGF5Omdy"
    "aWQ7cGxhY2UtaXRlbXM6Y2VudGVyfQouc3RhcnN7ZGlzcGxheTppbmxpbmUtZmxleDtnYXA6MXB4"
    "O3ZlcnRpY2FsLWFsaWduOm1pZGRsZX0KLnN0YXJzIHN2Z3t3aWR0aDoxMXB4O2hlaWdodDoxMXB4"
    "O2NvbG9yOiNmNWE2MjN9Ci5zdGFycyBie2ZvbnQtc2l6ZTouNjZyZW07Y29sb3I6dmFyKC0tdHgy"
    "KTtmb250LXdlaWdodDo3MDA7bWFyZ2luLWxlZnQ6M3B4fQouaGVhcnR7cG9zaXRpb246YWJzb2x1"
    "dGU7dG9wOjhweDtyaWdodDo4cHg7d2lkdGg6MjZweDtoZWlnaHQ6MjZweDtib3JkZXItcmFkaXVz"
    "Ojk5cHg7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtib3JkZXI6MnB4IHNvbGlkIHZh"
    "cigtLWxpbmUpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2N1cnNvcjpwb2ludGVyO3otaW5kZXg6"
    "Mn0KLmhlYXJ0IHN2Z3t3aWR0aDoxM3B4O2hlaWdodDoxM3B4O2NvbG9yOnZhcigtLXR4Mil9Ci5o"
    "ZWFydC5vbntiYWNrZ3JvdW5kOnZhcigtLWFjYyk7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50fQou"
    "aGVhcnQub24gc3Zne2NvbG9yOiNmZmZmZmZ9Ci5xdHl7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1z"
    "OmNlbnRlcjtnYXA6MDtib3JkZXI6MnB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6"
    "MTBweDtvdmVyZmxvdzpoaWRkZW59Ci5xdHkgYnV0dG9ue3dpZHRoOjM4cHg7aGVpZ2h0OjM4cHg7"
    "Ym9yZGVyOjA7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgpO2ZvbnQtc2l6"
    "ZToxcmVtO2ZvbnQtd2VpZ2h0OjgwMDtjdXJzb3I6cG9pbnRlcn0KLnF0eSBie21pbi13aWR0aDoz"
    "OHB4O3RleHQtYWxpZ246Y2VudGVyO2ZvbnQtc2l6ZTouODVyZW19Ci50bHttYXJnaW46MTBweCAw"
    "IDA7cGFkZGluZy1sZWZ0OjE2cHg7Ym9yZGVyLWxlZnQ6MnB4IHNvbGlkIHZhcigtLWxpbmUpfQou"
    "dGwgZGl2e3Bvc2l0aW9uOnJlbGF0aXZlO3BhZGRpbmc6MCAwIDExcHggMTBweDtmb250LXNpemU6"
    "Ljc0cmVtO2NvbG9yOnZhcigtLXR4Mil9Ci50bCBkaXY6bGFzdC1jaGlsZHtwYWRkaW5nLWJvdHRv"
    "bTowfQoudGwgZGl2OjpiZWZvcmV7Y29udGVudDoiIjtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0Oi0y"
    "MXB4O3RvcDo0cHg7d2lkdGg6OHB4O2hlaWdodDo4cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2JhY2tn"
    "cm91bmQ6dmFyKC0tbGluZSl9Ci50bCBkaXYub2s6OmJlZm9yZXtiYWNrZ3JvdW5kOnZhcigtLW9r"
    "KX0KLnRsIGRpdi5vayBie2NvbG9yOnZhcigtLXR4KX0KLnRsIHNtYWxse2Rpc3BsYXk6YmxvY2s7"
    "Zm9udC1zaXplOi42NnJlbTtvcGFjaXR5Oi44fQouZmNhcmR7ZGlzcGxheTpmbGV4O2FsaWduLWl0"
    "ZW1zOmZsZXgtc3RhcnQ7Z2FwOjExcHg7cGFkZGluZzoxMXB4IDA7Ym9yZGVyLWJvdHRvbToycHgg"
    "c29saWQgdmFyKC0tbGluZSl9Ci5mY2FyZDpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206MH0KLmZj"
    "YXJkIC50e2ZsZXg6MTttaW4td2lkdGg6MH0KLmZjYXJkIC50IGJ7Zm9udC1zaXplOi44MXJlbTtk"
    "aXNwbGF5OmJsb2NrfQouZmNhcmQgLnQgc21hbGx7ZGlzcGxheTpibG9jaztmb250LXNpemU6LjY5"
    "cmVtO2NvbG9yOnZhcigtLXR4Mik7bGluZS1oZWlnaHQ6MS40NTttYXJnaW4tdG9wOjJweH0KLmZy"
    "b3cuaGlkZSwuZmdycC5oaWRle2Rpc3BsYXk6bm9uZX0KLmJhcnN7ZGlzcGxheTpmbGV4O2FsaWdu"
    "LWl0ZW1zOmZsZXgtZW5kO2dhcDo1cHg7aGVpZ2h0Ojg2cHg7bWFyZ2luLXRvcDoxMnB4fQouYmFy"
    "cyBpe2ZsZXg6MTtib3JkZXItcmFkaXVzOjZweCA2cHggM3B4IDNweDtiYWNrZ3JvdW5kOnZhcigt"
    "LXBpbGxncmFkKTtkaXNwbGF5OmJsb2NrfQouZnVubmVsIGRpdntkaXNwbGF5OmZsZXg7YWxpZ24t"
    "aXRlbXM6Y2VudGVyO2dhcDo5cHg7bWFyZ2luLXRvcDo5cHg7Zm9udC1zaXplOi43NHJlbX0KLmZ1"
    "bm5lbCBpe2hlaWdodDoyNnB4O2JvcmRlci1yYWRpdXM6N3B4O2JhY2tncm91bmQ6dmFyKC0tcGls"
    "bGdyYWQpO2Rpc3BsYXk6YmxvY2t9Ci5mZWVkbGluZXtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6"
    "Y2VudGVyO2dhcDo5cHg7Zm9udC1zaXplOi43M3JlbTtjb2xvcjp2YXIoLS10eDIpO3BhZGRpbmc6"
    "N3B4IDA7Ym9yZGVyLWJvdHRvbToycHggc29saWQgdmFyKC0tbGluZSl9Ci5mZWVkbGluZTpsYXN0"
    "LWNoaWxke2JvcmRlci1ib3R0b206MH0KLmZlZWRsaW5lIGJ7Y29sb3I6dmFyKC0tdHgpfQoubGJ7"
    "ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjlweCAwO2Jv"
    "cmRlci1ib3R0b206MnB4IHNvbGlkIHZhcigtLWxpbmUpfQoubGI6bGFzdC1jaGlsZHtib3JkZXIt"
    "Ym90dG9tOjB9Ci5sYiAucmt7d2lkdGg6MjRweDtoZWlnaHQ6MjRweDtib3JkZXItcmFkaXVzOjk5"
    "cHg7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtmb250LXNpemU6LjdyZW07Zm9udC13"
    "ZWlnaHQ6ODAwO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4Mil9Ci5sYiAu"
    "cmsuZ3tiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojZmZmZmZmfQouY21zZ3JwLmhp"
    "ZGV7ZGlzcGxheTpub25lfQouY21zcm93IGxhYmVsIGJ7Y29sb3I6dmFyKC0tYWNjKTtmb250LXdl"
    "aWdodDo3MDB9Ci50aW1lcmJhcntoZWlnaHQ6NXB4O2JvcmRlci1yYWRpdXM6OTlweDtiYWNrZ3Jv"
    "dW5kOnZhcigtLWNhcmQyKTtvdmVyZmxvdzpoaWRkZW47bWFyZ2luOjEwcHggMCAycHh9Ci50aW1l"
    "cmJhciBpe2Rpc3BsYXk6YmxvY2s7aGVpZ2h0OjEwMCU7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3Jh"
    "ZCk7dHJhbnNpdGlvbjp3aWR0aCAxcyBsaW5lYXJ9Ci50aW1lcmJhci5sb3cgaXtiYWNrZ3JvdW5k"
    "OnZhcigtLWJhZCwjZTU0ODRkKX0KLmZpZWxke21hcmdpbi1ib3R0b206OXB4fQouZmllbGQgbGFi"
    "ZWx7ZGlzcGxheTpibG9jaztmb250LXNpemU6LjY4cmVtO2NvbG9yOnZhcigtLXR4Mik7bGV0dGVy"
    "LXNwYWNpbmc6LjA5ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO21hcmdpbjowIDAgNXB4IDJw"
    "eDtmb250LXdlaWdodDo3MDB9Ci5zd2Fwe2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7"
    "anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47Z2FwOjEycHg7cGFkZGluZzoxMXB4IDA7Ym9y"
    "ZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0tbGluZSl9Ci5zd2FwOmxhc3QtY2hpbGR7Ym9yZGVy"
    "LWJvdHRvbTowfQouc3dhcCAudHtmb250LXNpemU6LjgycmVtO2ZvbnQtd2VpZ2h0OjYwMDttaW4t"
    "d2lkdGg6MH0KLnN3YXAgLnQgc21hbGx7ZGlzcGxheTpibG9jaztmb250LXdlaWdodDo0MDA7Zm9u"
    "dC1zaXplOi43cmVtO2NvbG9yOnZhcigtLXR4Mil9Ci50Z3t3aWR0aDo0NHB4O2hlaWdodDoyNnB4"
    "O2JvcmRlci1yYWRpdXM6OTlweDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtib3JkZXI6MnB4IHNv"
    "bGlkIHZhcigtLWxpbmUpO3Bvc2l0aW9uOnJlbGF0aXZlO2N1cnNvcjpwb2ludGVyO2ZsZXg6MCAw"
    "IGF1dG99Ci50ZzphZnRlcntjb250ZW50OiIiO3Bvc2l0aW9uOmFic29sdXRlO3RvcDozcHg7bGVm"
    "dDozcHg7d2lkdGg6MThweDtoZWlnaHQ6MThweDtib3JkZXItcmFkaXVzOjUwJTtiYWNrZ3JvdW5k"
    "OnZhcigtLXR4Mik7dHJhbnNpdGlvbjouMjJzfQoudGcub257YmFja2dyb3VuZDp2YXIoLS1waWxs"
    "Z3JhZCk7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50fQoudGcub246YWZ0ZXJ7bGVmdDoyMXB4O2Jh"
    "Y2tncm91bmQ6I2ZmZmZmZn0KLnRnLnNte3dpZHRoOjM2cHg7aGVpZ2h0OjIxcHh9Ci50Zy5zbTph"
    "ZnRlcnt3aWR0aDoxNHB4O2hlaWdodDoxNHB4O3RvcDoyLjVweH0KLnRnLnNtLm9uOmFmdGVye2xl"
    "ZnQ6MThweH0KLnN0YXJ7d2lkdGg6MzBweDtoZWlnaHQ6MzBweDtib3JkZXItcmFkaXVzOjlweDtk"
    "aXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2JvcmRlcjoycHggc29saWQgdmFyKC0tbGlu"
    "ZSk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgyKTtjdXJzb3I6cG9pbnRl"
    "cjtmbGV4OjAgMCBhdXRvfQouc3Rhci5vbntiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xv"
    "cjojZmZmZmZmO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVudH0KLnN0YXIgc3Zne3dpZHRoOjE0cHg7"
    "aGVpZ2h0OjE0cHh9Cgojc2hlZXR7cG9zaXRpb246Zml4ZWQ7aW5zZXQ6MDt6LWluZGV4OjkwO2Rp"
    "c3BsYXk6bm9uZX0KI3NoZWV0Lm9wZW57ZGlzcGxheTpibG9ja30KI3NoZWV0IC5iZ3twb3NpdGlv"
    "bjphYnNvbHV0ZTtpbnNldDowO2JhY2tncm91bmQ6cmdiYSgxNywxNywxNywuNSk7YmFja2Ryb3At"
    "ZmlsdGVyOmJsdXIoM3B4KX0KI3NoZWV0IC5wYW5lbHtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjUw"
    "JTtib3R0b206MDt0cmFuc2Zvcm06dHJhbnNsYXRlWCgtNTAlKTt3aWR0aDoxMDAlO21heC13aWR0"
    "aDo1MjBweDtiYWNrZ3JvdW5kOnZhcigtLWJnMik7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1saW5l"
    "KTtib3JkZXItYm90dG9tOjA7Ym9yZGVyLXJhZGl1czoxOHB4IDE4cHggMCAwO3BhZGRpbmc6MThw"
    "eCAxNnB4IGNhbGMoMjBweCArIGVudihzYWZlLWFyZWEtaW5zZXQtYm90dG9tKSk7YW5pbWF0aW9u"
    "OnVwIC4yOHMgY3ViaWMtYmV6aWVyKC4yLC45LC4zLDEpO21heC1oZWlnaHQ6OTJ2aDtvdmVyZmxv"
    "dzphdXRvfQpAa2V5ZnJhbWVzIHVwe2Zyb217dHJhbnNmb3JtOnRyYW5zbGF0ZSgtNTAlLDEwMCUp"
    "fXRve3RyYW5zZm9ybTp0cmFuc2xhdGUoLTUwJSwwKX19CiNzaGVldCAuZ3JhYnt3aWR0aDozOHB4"
    "O2hlaWdodDo0cHg7Ym9yZGVyLXJhZGl1czo5cHg7YmFja2dyb3VuZDp2YXIoLS1saW5lKTttYXJn"
    "aW46MCBhdXRvIDE0cHh9Ci5vdHBib3h7Zm9udC1mYW1pbHk6dWktbW9ub3NwYWNlLE1lbmxvLENv"
    "bnNvbGFzLG1vbm9zcGFjZTtmb250LXNpemU6MS42cmVtO2xldHRlci1zcGFjaW5nOi4zNWVtO3Rl"
    "eHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MTZweDtib3JkZXItcmFkaXVzOnZhcigtLXJzKTtiYWNr"
    "Z3JvdW5kOnZhcigtLWNhcmQyKTtib3JkZXI6MnB4IGRhc2hlZCB2YXIoLS1saW5lKTttYXJnaW46"
    "MTBweCAwfQoucXJ7d2lkdGg6MTUwcHg7aGVpZ2h0OjE1MHB4O21hcmdpbjo2cHggYXV0byAxMHB4"
    "O2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2JvcmRlcjoy"
    "cHggc29saWQgdmFyKC0tbGluZSk7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtjb2xv"
    "cjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjVyZW07bGV0dGVyLXNwYWNpbmc6LjFlbTt0ZXh0LWFs"
    "aWduOmNlbnRlcn0KLnN0ZXBze21hcmdpbjoxMnB4IDAgNHB4fQouc3RlcHtkaXNwbGF5OmZsZXg7"
    "YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4O3BhZGRpbmc6OHB4IDA7Zm9udC1zaXplOi43OXJl"
    "bTtjb2xvcjp2YXIoLS10eDIpfQouc3RlcCBpe3dpZHRoOjIycHg7aGVpZ2h0OjIycHg7Ym9yZGVy"
    "LXJhZGl1czo1MCU7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1saW5lKTtkaXNwbGF5OmdyaWQ7cGxh"
    "Y2UtaXRlbXM6Y2VudGVyO2ZvbnQtc3R5bGU6bm9ybWFsO2ZvbnQtc2l6ZTouNjVyZW07Zm9udC13"
    "ZWlnaHQ6ODAwO2ZsZXg6MCAwIGF1dG99Ci5zdGVwLm9ue2NvbG9yOnZhcigtLXR4KX0KLnN0ZXAu"
    "b24gaXtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojZmZmZmZmO2JvcmRlci1jb2xv"
    "cjp0cmFuc3BhcmVudH0KLnN0ZXAucnVuIGl7Ym9yZGVyLWNvbG9yOnZhcigtLWFjYyk7Y29sb3I6"
    "dmFyKC0tYWNjKTthbmltYXRpb246cHVsc2UgMXMgaW5maW5pdGV9CkBrZXlmcmFtZXMgcHVsc2V7"
    "NTAle29wYWNpdHk6LjM1fX0KLnRvYXN0e3Bvc2l0aW9uOmZpeGVkO2xlZnQ6NTAlO2JvdHRvbTpj"
    "YWxjKDg2cHggKyBlbnYoc2FmZS1hcmVhLWluc2V0LWJvdHRvbSkpO3RyYW5zZm9ybTp0cmFuc2xh"
    "dGVYKC01MCUpO3otaW5kZXg6MTIwO2JhY2tncm91bmQ6dmFyKC0tYmcyKTtib3JkZXI6MnB4IHNv"
    "bGlkIHZhcigtLWxpbmUpO2NvbG9yOnZhcigtLXR4KTtwYWRkaW5nOjExcHggMTZweDtib3JkZXIt"
    "cmFkaXVzOjk5cHg7Zm9udC1zaXplOi43OHJlbTtmb250LXdlaWdodDo2MDA7Ym94LXNoYWRvdzp2"
    "YXIoLS1zaGFkb3cpO29wYWNpdHk6MDtwb2ludGVyLWV2ZW50czpub25lO3RyYW5zaXRpb246b3Bh"
    "Y2l0eSAuMnMsdHJhbnNmb3JtIC4yczt3aGl0ZS1zcGFjZTpub3dyYXA7bWF4LXdpZHRoOjkwdnc7"
    "b3ZlcmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXN9Ci50b2FzdC5zaG93e29wYWNp"
    "dHk6MTt0cmFuc2Zvcm06dHJhbnNsYXRlWCgtNTAlKSB0cmFuc2xhdGVZKC02cHgpfQoKLyogcHJl"
    "dmlldyBjaHJvbWUg4oCUIG1vY2t1cCBvbmx5LCBvd24gc3RyaXAgc28gaXQgbmV2ZXIgb3Zlcmxh"
    "cHMgdGhlIFVJICovCiNzd3twb3NpdGlvbjpmaXhlZDt0b3A6MDtsZWZ0OjA7cmlnaHQ6MDtoZWln"
    "aHQ6MzJweDt6LWluZGV4OjIwMDtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3Rp"
    "ZnktY29udGVudDpjZW50ZXI7Z2FwOjVweDsKICBiYWNrZ3JvdW5kOiNmZmZmZmY7Ym9yZGVyLWJv"
    "dHRvbToxcHggc29saWQgdmFyKC0tbGluZSk7YmFja2Ryb3AtZmlsdGVyOmJsdXIoMTBweCl9CiNz"
    "dyBlbXtmb250LXN0eWxlOm5vcm1hbDtmb250LXNpemU6LjU2cmVtO2xldHRlci1zcGFjaW5nOi4x"
    "OGVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtjb2xvcjp2YXIoLS10eDIpO21hcmdpbi1yaWdo"
    "dDo0cHh9CiNzdyBidXR0b257aGVpZ2h0OjIycHg7cGFkZGluZzowIDEycHg7Ym9yZGVyLXJhZGl1"
    "czo5OXB4O2JvcmRlcjowO2N1cnNvcjpwb2ludGVyO2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7Y29s"
    "b3I6dmFyKC0tdHgyKTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXNpemU6LjZyZW07Zm9u"
    "dC13ZWlnaHQ6NzAwO2xldHRlci1zcGFjaW5nOi4wN2VtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2Fz"
    "ZX0KI3N3IGJ1dHRvbi5vbntiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojZmZmZmZm"
    "fQoKLyog4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQIEFVUk9S"
    "QSBVSSBLSVQg4oCUIHBvcnRlZCBjb21wb25lbnRzIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkCAqLwpAcHJvcGVydHkgLS1nYXtzeW50YXg6IjxhbmdsZT4iO2lu"
    "aXRpYWwtdmFsdWU6MGRlZztpbmhlcml0czpmYWxzZX0KQHByb3BlcnR5IC0tZ3B7c3ludGF4OiI8"
    "cGVyY2VudGFnZT4iO2luaXRpYWwtdmFsdWU6NSU7aW5oZXJpdHM6ZmFsc2V9CkBwcm9wZXJ0eSAt"
    "LWdve3N5bnRheDoiPGFuZ2xlPiI7aW5pdGlhbC12YWx1ZTowZGVnO2luaGVyaXRzOmZhbHNlfQoK"
    "LyogMjMgwrcgU2hpbnkgQ1RBIOKAlCBjb25pYyBib3JkZXIgKyBkb3QgbWFzayArIGlubmVyIHNo"
    "aW1tZXIgKyBicmVhdGhlICovCi5idG4uY3Rhey0tZHVyOjMuMnM7LS1obDojZmZiMTk5O3Bvc2l0"
    "aW9uOnJlbGF0aXZlO2lzb2xhdGlvbjppc29sYXRlO2JvcmRlcjoxcHggc29saWQgdHJhbnNwYXJl"
    "bnQ7CiAgYmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQodmFyKC0tYnRuYmcpLHZhcigtLWJ0bmJn"
    "KSkgcGFkZGluZy1ib3gsCiAgICBjb25pYy1ncmFkaWVudChmcm9tIGNhbGModmFyKC0tZ2EpIC0g"
    "dmFyKC0tZ28pKSx0cmFuc3BhcmVudCx2YXIoLS1obCkgdmFyKC0tZ3ApLAogICAgICAjZmZmIGNh"
    "bGModmFyKC0tZ3ApKjIpLHZhcigtLWhsKSBjYWxjKHZhcigtLWdwKSozKSx0cmFuc3BhcmVudCBj"
    "YWxjKHZhcigtLWdwKSo0KSkgYm9yZGVyLWJveDsKICBib3gtc2hhZG93Omluc2V0IDAgMCAwIDFw"
    "eCByZ2JhKDI1NSwyNTUsMjU1LC4wOCk7CiAgdHJhbnNpdGlvbjotLWdvIC44cyBjdWJpYy1iZXpp"
    "ZXIoLjI1LDEsLjUsMSksLS1ncCAuOHMgY3ViaWMtYmV6aWVyKC4yNSwxLC41LDEpLHRyYW5zZm9y"
    "bSAuMTVzOwogIGFuaW1hdGlvbjpnYSB2YXIoLS1kdXIpIGxpbmVhciBpbmZpbml0ZX0KLmJ0bi5j"
    "dGE6YmVmb3JlLC5idG4uY3RhOmFmdGVye2NvbnRlbnQ6IiI7cG9zaXRpb246YWJzb2x1dGU7bGVm"
    "dDo1MCU7dG9wOjUwJTt0cmFuc2xhdGU6LTUwJSAtNTAlO3otaW5kZXg6LTE7cG9pbnRlci1ldmVu"
    "dHM6bm9uZX0KLmJ0bi5jdGE6YmVmb3Jle3dpZHRoOmNhbGMoMTAwJSAtIDZweCk7aGVpZ2h0OmNh"
    "bGMoMTAwJSAtIDZweCk7Ym9yZGVyLXJhZGl1czppbmhlcml0O29wYWNpdHk6LjM1OwogIGJhY2tn"
    "cm91bmQ6cmFkaWFsLWdyYWRpZW50KGNpcmNsZSBhdCAycHggMnB4LCNmZmYgLjVweCx0cmFuc3Bh"
    "cmVudCAwKSBwYWRkaW5nLWJveDtiYWNrZ3JvdW5kLXNpemU6NHB4IDRweDsKICAtd2Via2l0LW1h"
    "c2staW1hZ2U6Y29uaWMtZ3JhZGllbnQoZnJvbSBjYWxjKHZhcigtLWdhKSArIDQ1ZGVnKSwjMDAw"
    "LHRyYW5zcGFyZW50IDEwJSA5MCUsIzAwMCk7CiAgbWFzay1pbWFnZTpjb25pYy1ncmFkaWVudChm"
    "cm9tIGNhbGModmFyKC0tZ2EpICsgNDVkZWcpLCMwMDAsdHJhbnNwYXJlbnQgMTAlIDkwJSwjMDAw"
    "KX0KLmJ0bi5jdGE6YWZ0ZXJ7d2lkdGg6MTAwJTthc3BlY3QtcmF0aW86MTtvcGFjaXR5Oi41NTsK"
    "ICBiYWNrZ3JvdW5kOmxpbmVhci1ncmFkaWVudCgtNTBkZWcsdHJhbnNwYXJlbnQsdmFyKC0taGwp"
    "LHRyYW5zcGFyZW50KTsKICAtd2Via2l0LW1hc2staW1hZ2U6cmFkaWFsLWdyYWRpZW50KGNpcmNs"
    "ZSBhdCBib3R0b20sdHJhbnNwYXJlbnQgNDAlLCMwMDApOwogIG1hc2staW1hZ2U6cmFkaWFsLWdy"
    "YWRpZW50KGNpcmNsZSBhdCBib3R0b20sdHJhbnNwYXJlbnQgNDAlLCMwMDApOwogIGFuaW1hdGlv"
    "bjpzaGltbWVyIHZhcigtLWR1cikgbGluZWFyIGluZmluaXRlfQouYnRuLmN0YTpob3ZlcnstLWdw"
    "OjIyJTstLWdvOjk1ZGVnfQouYnRuLmN0YTphY3RpdmV7dHJhbnNsYXRlOjAgMXB4fQpAa2V5ZnJh"
    "bWVzIGdhe3Rvey0tZ2E6MzYwZGVnfX0KQGtleWZyYW1lcyBzaGltbWVye3Rve3JvdGF0ZTozNjBk"
    "ZWd9fQoKLyogMjIgwrcgTGlxdWlkIGdsYXNzICovCi5idG4uZ2xhc3MsLmdsYXNzY2FyZHtwb3Np"
    "dGlvbjpyZWxhdGl2ZTtiYWNrZ3JvdW5kOmNvbG9yLW1peChpbiBzcmdiLHZhcigtLWNhcmQyKSA3"
    "MCUsdHJhbnNwYXJlbnQpOwogIGJvcmRlcjoxcHggc29saWQgY29sb3ItbWl4KGluIHNyZ2IsI2Zm"
    "ZiAxNiUsdmFyKC0tbGluZSkpO2NvbG9yOnZhcigtLXR4KTsKICBiYWNrZHJvcC1maWx0ZXI6Ymx1"
    "cigxNHB4KSBzYXR1cmF0ZSgxLjQpOy13ZWJraXQtYmFja2Ryb3AtZmlsdGVyOmJsdXIoMTRweCkg"
    "c2F0dXJhdGUoMS40KTsKICBib3gtc2hhZG93Omluc2V0IDFweCAxcHggMXB4IC0uNXB4IHJnYmEo"
    "MjU1LDI1NSwyNTUsLjU1KSxpbnNldCAtMXB4IC0xcHggMXB4IC0uNXB4IHJnYmEoMjU1LDI1NSwy"
    "NTUsLjMpLAogICAgaW5zZXQgMCAwIDhweCA2cHggcmdiYSgyNTUsMjU1LDI1NSwuMDcpLDAgMnB4"
    "IDEwcHggcmdiYSgwLDAsMCwuMTIpfQouYnRuLmdsYXNzOmFmdGVye2NvbnRlbnQ6IiI7cG9zaXRp"
    "b246YWJzb2x1dGU7aW5zZXQ6MXB4IDFweCBhdXRvO2hlaWdodDo0MiU7Ym9yZGVyLXJhZGl1czpp"
    "bmhlcml0OwogIGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KDE4MGRlZyxyZ2JhKDI1NSwyNTUs"
    "MjU1LC4zNCksdHJhbnNwYXJlbnQpO3BvaW50ZXItZXZlbnRzOm5vbmV9Ci5idG4uZ2xhc3M6aG92"
    "ZXJ7dHJhbnNmb3JtOnNjYWxlKDEuMDMpfQoKLyogcHJlc3MgcmlwcGxlIChraXQgZG9jay9idXR0"
    "b24gZmVlbCkgKi8KLnJpcHtwb3NpdGlvbjphYnNvbHV0ZTtib3JkZXItcmFkaXVzOjUwJTt0cmFu"
    "c2Zvcm06dHJhbnNsYXRlKC01MCUsLTUwJSkgc2NhbGUoMCk7cG9pbnRlci1ldmVudHM6bm9uZTsK"
    "ICBiYWNrZ3JvdW5kOnJhZGlhbC1ncmFkaWVudChjaXJjbGUscmdiYSgyNTUsMjU1LDI1NSwuNTUp"
    "LHRyYW5zcGFyZW50IDcwJSk7YW5pbWF0aW9uOnJpcCAuNTVzIGVhc2Utb3V0IGZvcndhcmRzfQpA"
    "a2V5ZnJhbWVzIHJpcHt0b3t0cmFuc2Zvcm06dHJhbnNsYXRlKC01MCUsLTUwJSkgc2NhbGUoMSk7"
    "b3BhY2l0eTowfX0KCi8qIDI0IMK3IEFuaW1hdGVkIGJhbm5lciArIDE3IMK3IGxvZ28gY2xvdWQg"
    "bWFycXVlZSAqLwouYmFubmVye3Bvc2l0aW9uOnJlbGF0aXZlO292ZXJmbG93OmhpZGRlbjtib3Jk"
    "ZXItcmFkaXVzOjEwcHg7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1saW5lKTsKICBiYWNrZ3JvdW5k"
    "OnZhcigtLWNhcmQpO21hcmdpbjoycHggMCAxMnB4O2hlaWdodDozOHB4O2Rpc3BsYXk6ZmxleDth"
    "bGlnbi1pdGVtczpjZW50ZXI7CiAgLXdlYmtpdC1tYXNrLWltYWdlOmxpbmVhci1ncmFkaWVudCg5"
    "MGRlZyx0cmFuc3BhcmVudCwjMDAwIDglLCMwMDAgOTIlLHRyYW5zcGFyZW50KTsKICBtYXNrLWlt"
    "YWdlOmxpbmVhci1ncmFkaWVudCg5MGRlZyx0cmFuc3BhcmVudCwjMDAwIDglLCMwMDAgOTIlLHRy"
    "YW5zcGFyZW50KX0KLmJhbm5lciAudHJhY2t7ZGlzcGxheTpmbGV4O2dhcDozMHB4O3doaXRlLXNw"
    "YWNlOm5vd3JhcDthbmltYXRpb246bWFycSAyNnMgbGluZWFyIGluZmluaXRlO3dpbGwtY2hhbmdl"
    "OnRyYW5zZm9ybX0KLmJhbm5lcjpob3ZlciAudHJhY2t7YW5pbWF0aW9uLXBsYXktc3RhdGU6cGF1"
    "c2VkfQouYmFubmVyIHNwYW57Zm9udC1zaXplOi43cmVtO2NvbG9yOnZhcigtLXR4Mik7Zm9udC13"
    "ZWlnaHQ6NjAwO2xldHRlci1zcGFjaW5nOi4wM2VtO2Rpc3BsYXk6aW5saW5lLWZsZXg7YWxpZ24t"
    "aXRlbXM6Y2VudGVyO2dhcDo2cHh9Ci5iYW5uZXIgc3BhbiBie2NvbG9yOnZhcigtLWFjYyl9Ci5i"
    "YW5uZXIgc3BhbiBzdmd7d2lkdGg6MTNweDtoZWlnaHQ6MTNweDtjb2xvcjp2YXIoLS1hY2MpfQpA"
    "a2V5ZnJhbWVzIG1hcnF7dG97dHJhbnNmb3JtOnRyYW5zbGF0ZVgoLTUwJSl9fQouY2xvdWR7ZGlz"
    "cGxheTpmbGV4O2dhcDoyNnB4O292ZXJmbG93OmhpZGRlbjtwYWRkaW5nOjRweCAwIDJweDsKICAt"
    "d2Via2l0LW1hc2staW1hZ2U6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRyYW5zcGFyZW50LCMwMDAg"
    "MTIlLCMwMDAgODglLHRyYW5zcGFyZW50KTsKICBtYXNrLWltYWdlOmxpbmVhci1ncmFkaWVudCg5"
    "MGRlZyx0cmFuc3BhcmVudCwjMDAwIDEyJSwjMDAwIDg4JSx0cmFuc3BhcmVudCl9Ci5jbG91ZCAu"
    "dHtkaXNwbGF5OmZsZXg7Z2FwOjI2cHg7YW5pbWF0aW9uOm1hcnEgMzBzIGxpbmVhciBpbmZpbml0"
    "ZX0KLmNsb3VkIGl7Zm9udC1zdHlsZTpub3JtYWw7Zm9udC1zaXplOi42OHJlbTtmb250LXdlaWdo"
    "dDo4MDA7bGV0dGVyLXNwYWNpbmc6LjE0ZW07Y29sb3I6dmFyKC0tdHgyKTtvcGFjaXR5Oi41NTt3"
    "aGl0ZS1zcGFjZTpub3dyYXB9CgovKiAwNyDCtyBHcmFkaWVudCB0cmFjaW5nICovCi50cmFjZXtk"
    "aXNwbGF5OmJsb2NrO3dpZHRoOjEwMCU7aGVpZ2h0OjEwcHg7bWFyZ2luOi0ycHggMCA4cHg7b3Zl"
    "cmZsb3c6dmlzaWJsZX0KLnRyYWNlIC5iYXNle3N0cm9rZTp2YXIoLS1saW5lKTtzdHJva2Utd2lk"
    "dGg6MS40fQoudHJhY2UgLmxlYWR7c3Ryb2tlLXdpZHRoOjEuODtzdHJva2UtbGluZWNhcDpyb3Vu"
    "ZH0KLnNlYyBoMntwb3NpdGlvbjpyZWxhdGl2ZX0KCi8qIDAxICsgMTMgwrcgRG90IGxvYWRlciAq"
    "LwouZG90Z3JpZHtkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOnJlcGVhdCg3LDFm"
    "cik7Z2FwOjNweDt3aWR0aDo3MHB4fQouZG90Z3JpZCBpe3dpZHRoOjEwMCU7YXNwZWN0LXJhdGlv"
    "OjE7Ym9yZGVyLXJhZGl1czoycHg7YmFja2dyb3VuZDpjb2xvci1taXgoaW4gc3JnYix2YXIoLS10"
    "eCkgMTQlLHRyYW5zcGFyZW50KTt0cmFuc2l0aW9uOmJhY2tncm91bmQgLjEycyx0cmFuc2Zvcm0g"
    "LjEyc30KLmRvdGdyaWQgaS5hY3R7YmFja2dyb3VuZDp2YXIoLS1hY2MpO3RyYW5zZm9ybTpzY2Fs"
    "ZSgxLjEyKX0KLmRvdHN7ZGlzcGxheTppbmxpbmUtZmxleDtnYXA6NHB4O2FsaWduLWl0ZW1zOmNl"
    "bnRlcn0KLmRvdHMgaXt3aWR0aDo1cHg7aGVpZ2h0OjVweDtib3JkZXItcmFkaXVzOjUwJTtiYWNr"
    "Z3JvdW5kOnZhcigtLWFjYyk7YW5pbWF0aW9uOmJvdW5jZSAxcyBpbmZpbml0ZSBlYXNlLWluLW91"
    "dH0KLmRvdHMgaTpudGgtY2hpbGQoMil7YW5pbWF0aW9uLWRlbGF5Oi4xNXN9LmRvdHMgaTpudGgt"
    "Y2hpbGQoMyl7YW5pbWF0aW9uLWRlbGF5Oi4zc30KQGtleWZyYW1lcyBib3VuY2V7MCUsODAlLDEw"
    "MCV7dHJhbnNmb3JtOnRyYW5zbGF0ZVkoMCk7b3BhY2l0eTouMzV9NDAle3RyYW5zZm9ybTp0cmFu"
    "c2xhdGVZKC01cHgpO29wYWNpdHk6MX19CgovKiBzcGxhc2ggKi8KI3NwbGFzaHtwb3NpdGlvbjpm"
    "aXhlZDtpbnNldDowO3otaW5kZXg6MzAwO2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1"
    "bW47YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Z2FwOjE2cHg7CiAg"
    "YmFja2dyb3VuZDp2YXIoLS1iZyk7dHJhbnNpdGlvbjpvcGFjaXR5IC41cyBlYXNlLHZpc2liaWxp"
    "dHkgLjVzfQojc3BsYXNoLmdvbmV7b3BhY2l0eTowO3Zpc2liaWxpdHk6aGlkZGVufQojc3BsYXNo"
    "IC5tYXJre3dpZHRoOjU4cHg7aGVpZ2h0OjU4cHg7Ym9yZGVyLXJhZGl1czoxMHB4O2Rpc3BsYXk6"
    "Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7CiAgY29s"
    "b3I6I2ZmZmZmZjtmb250LXdlaWdodDo4MDA7Zm9udC1zaXplOjEuMXJlbTthbmltYXRpb246bWFy"
    "a2luIC43cyBjdWJpYy1iZXppZXIoLjIsMS40LC40LDEpIGJvdGh9CiNzcGxhc2ggaDR7bWFyZ2lu"
    "OjA7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZToxcmVtO2ZvbnQtd2VpZ2h0Ojkw"
    "MDtsZXR0ZXItc3BhY2luZzotLjAyZW07CiAgYW5pbWF0aW9uOmZhZGV1cCAuNnMgLjE1cyBib3Ro"
    "fQojc3BsYXNoIHNtYWxse2NvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42NXJlbTtsZXR0ZXIt"
    "c3BhY2luZzouMjJlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7YW5pbWF0aW9uOmZhZGV1cCAu"
    "NnMgLjI1cyBib3RofQpAa2V5ZnJhbWVzIG1hcmtpbntmcm9te29wYWNpdHk6MDt0cmFuc2Zvcm06"
    "c2NhbGUoLjYpIHJvdGF0ZSgtMTJkZWcpfXRve29wYWNpdHk6MTt0cmFuc2Zvcm06bm9uZX19CkBr"
    "ZXlmcmFtZXMgZmFkZXVwe2Zyb217b3BhY2l0eTowO3RyYW5zZm9ybTp0cmFuc2xhdGVZKDhweCl9"
    "dG97b3BhY2l0eToxO3RyYW5zZm9ybTpub25lfX0KCi8qIDA4IMK3IEtpbmV0aWMgZ3JpZCBjYW52"
    "YXMgKi8KI2tne3Bvc2l0aW9uOmZpeGVkO2luc2V0OjA7ei1pbmRleDowO3BvaW50ZXItZXZlbnRz"
    "Om5vbmU7b3BhY2l0eTouMzB9CgovKiBzdGFnZ2VyZWQgcmV2ZWFsIGlzIGRyaXZlbiBieSB0aGUg"
    "V2ViIEFuaW1hdGlvbnMgQVBJIChjYW5ub3QgY2xhc2ggd2l0aCB0aGVtZSBDU1MpICovCgovKiAw"
    "MiArIDI1IMK3IERvY2sgd2l0aCBzcHJpbmcgcGlsbCAqLwpuYXYjdGFicyAuZG9ja3twb3NpdGlv"
    "bjpyZWxhdGl2ZX0KbmF2I3RhYnMgLmRwaWxse3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6MDt0b3A6"
    "MDt6LWluZGV4OjA7Ym9yZGVyLXJhZGl1czoxMHB4O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQp"
    "OwogIGJveC1zaGFkb3c6MCA2cHggMThweCBjb2xvci1taXgoaW4gc3JnYix2YXIoLS1hY2MpIDM1"
    "JSx0cmFuc3BhcmVudCk7cG9pbnRlci1ldmVudHM6bm9uZX0KbmF2I3RhYnMgYnV0dG9ue3Bvc2l0"
    "aW9uOnJlbGF0aXZlO3otaW5kZXg6MTt3aWxsLWNoYW5nZTp0cmFuc2Zvcm07CiAgdHJhbnNpdGlv"
    "bjpjb2xvciAuMjZzIGN1YmljLWJlemllciguMjIsMSwuMzYsMSksdHJhbnNmb3JtIC4xOHMgY3Vi"
    "aWMtYmV6aWVyKC4zLDEuNSwuNSwxKX0KbmF2I3RhYnMgYnV0dG9uLm9ue2JhY2tncm91bmQ6dHJh"
    "bnNwYXJlbnQ7Y29sb3I6I2ZmZmZmZn0KbmF2I3RhYnMgLmRwaWxse29wYWNpdHk6MH0KbmF2I3Rh"
    "YnMgLmRwaWxsLnJlYWR5e29wYWNpdHk6MX0KCi8qIDE0IMK3IFYtdGFicyBzbGlkaW5nIGluZGlj"
    "YXRvciAqLwouc3VibmF2e3Bvc2l0aW9uOnJlbGF0aXZlfQouc3VibmF2IC52cGlsbHtwb3NpdGlv"
    "bjphYnNvbHV0ZTtsZWZ0OjA7dG9wOjA7ei1pbmRleDowO2JvcmRlci1yYWRpdXM6OHB4O2JhY2tn"
    "cm91bmQ6dmFyKC0tcGlsbGdyYWQpO3RyYW5zaXRpb246bm9uZTtwb2ludGVyLWV2ZW50czpub25l"
    "fQouc3VibmF2IGJ1dHRvbntwb3NpdGlvbjpyZWxhdGl2ZTt6LWluZGV4OjF9Ci5zdWJuYXYgYnV0"
    "dG9uLm9ue2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7Y29sb3I6I2ZmZmZmZjtib3JkZXItY29sb3I6"
    "dHJhbnNwYXJlbnR9CgovKiAxNSDCtyBBbmltYXRlZCBwcm9maWxlIGNhcmQg4oCUIHRpbHQgKyBn"
    "bGFyZSAqLwoudGlsdHt0cmFuc2Zvcm0tc3R5bGU6cHJlc2VydmUtM2Q7dHJhbnNpdGlvbjp0cmFu"
    "c2Zvcm0gLjM1cyBjdWJpYy1iZXppZXIoLjIyLDEsLjM2LDEpO3dpbGwtY2hhbmdlOnRyYW5zZm9y"
    "bX0KLnRpbHQgLmdsYXJle3Bvc2l0aW9uOmFic29sdXRlO2luc2V0OjA7Ym9yZGVyLXJhZGl1czpp"
    "bmhlcml0O3BvaW50ZXItZXZlbnRzOm5vbmU7b3BhY2l0eTowO3RyYW5zaXRpb246b3BhY2l0eSAu"
    "M3M7CiAgYmFja2dyb3VuZDpyYWRpYWwtZ3JhZGllbnQoMjQwcHggY2lyY2xlIGF0IHZhcigtLW14"
    "LDUwJSkgdmFyKC0tbXksNTAlKSxyZ2JhKDI1NSwyNTUsMjU1LC4yMiksdHJhbnNwYXJlbnQgNjAl"
    "KX0KLnRpbHQ6aG92ZXIgLmdsYXJle29wYWNpdHk6MX0KLnNoaW5lY2FyZHtwb3NpdGlvbjpyZWxh"
    "dGl2ZTtvdmVyZmxvdzpoaWRkZW59Ci5zaGluZWNhcmQ6YmVmb3Jle2NvbnRlbnQ6IiI7cG9zaXRp"
    "b246YWJzb2x1dGU7dG9wOi02MCU7bGVmdDotMzAlO3dpZHRoOjU1JTtoZWlnaHQ6MjIwJTt0cmFu"
    "c2Zvcm06cm90YXRlKDE4ZGVnKTsKICBiYWNrZ3JvdW5kOmxpbmVhci1ncmFkaWVudCg5MGRlZyx0"
    "cmFuc3BhcmVudCxyZ2JhKDI1NSwyNTUsMjU1LC4xNiksdHJhbnNwYXJlbnQpO2FuaW1hdGlvbjpj"
    "YXJkc2hpbmUgNS41cyBlYXNlLWluLW91dCBpbmZpbml0ZTtwb2ludGVyLWV2ZW50czpub25lfQpA"
    "a2V5ZnJhbWVzIGNhcmRzaGluZXswJSw3MCV7bGVmdDotNDAlfTEwMCV7bGVmdDoxMjAlfX0KCi8q"
    "IDA1IMK3IEhvbGQgYWN0aW9uIGJ1dHRvbiAqLwouaG9sZHtwb3NpdGlvbjpyZWxhdGl2ZTtvdmVy"
    "ZmxvdzpoaWRkZW59Ci5ob2xkIC5maWxse3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6MDt0b3A6MDti"
    "b3R0b206MDt3aWR0aDowO2JhY2tncm91bmQ6cmdiYSgyNTUsMjU1LDI1NSwuMjUpO3RyYW5zaXRp"
    "b246d2lkdGggLjA1cyBsaW5lYXJ9Ci5ob2xkLmRvbmV7YW5pbWF0aW9uOnBvcCAuNHMgY3ViaWMt"
    "YmV6aWVyKC4yLDEuNiwuNCwxKX0KQGtleWZyYW1lcyBwb3B7NTAle3RyYW5zZm9ybTpzY2FsZSgx"
    "LjA0KX19CgovKiAxMSDCtyBNb3JwaGluZyBzZWFyY2ggKi8KLm1zZWFyY2h7ZGlzcGxheTpmbGV4"
    "O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6OHB4O21hcmdpbjowIDAgMTBweH0KLm1zZWFyY2ggLmZp"
    "ZWxkd3JhcHtmbGV4OjE7bWluLXdpZHRoOjA7cG9zaXRpb246cmVsYXRpdmU7aGVpZ2h0OjQwcHh9"
    "Ci5tc2VhcmNoIGlucHV0e3dpZHRoOjEwMCU7aGVpZ2h0OjQwcHg7cGFkZGluZzowIDEzcHggMCAz"
    "NnB4O2JvcmRlci1yYWRpdXM6OTlweDtib3JkZXI6MnB4IHNvbGlkIHZhcigtLWxpbmUpOwogIGJh"
    "Y2tncm91bmQ6dmFyKC0tY2FyZCk7Y29sb3I6dmFyKC0tdHgpO2ZvbnQtZmFtaWx5OnZhcigtLWZv"
    "bnQpO2ZvbnQtc2l6ZTouOHJlbTtvdXRsaW5lOm5vbmU7CiAgdHJhbnNpdGlvbjpib3gtc2hhZG93"
    "IC4zcyBjdWJpYy1iZXppZXIoLjIyLDEsLjM2LDEpLGJvcmRlci1jb2xvciAuM3N9Ci5tc2VhcmNo"
    "IGlucHV0OmZvY3Vze2JvcmRlci1jb2xvcjp2YXIoLS1hY2MpO2JveC1zaGFkb3c6MCAwIDAgNHB4"
    "IGNvbG9yLW1peChpbiBzcmdiLHZhcigtLWFjYykgMTYlLHRyYW5zcGFyZW50KX0KLm1zZWFyY2gg"
    "c3Zne3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6MTJweDt0b3A6MTFweDt3aWR0aDoxN3B4O2hlaWdo"
    "dDoxN3B4O2NvbG9yOnZhcigtLXR4Mik7dHJhbnNpdGlvbjpjb2xvciAuMjVzfQoubXNlYXJjaCBp"
    "bnB1dDpmb2N1cytzdmcsLm1zZWFyY2ggLmZpZWxkd3JhcDpmb2N1cy13aXRoaW4gc3Zne2NvbG9y"
    "OnZhcigtLWFjYyl9Ci5jaGlwcm93e2Rpc3BsYXk6ZmxleDtnYXA6NnB4O292ZXJmbG93LXg6YXV0"
    "bztwYWRkaW5nLWJvdHRvbToxMHB4fQouY2hpcHJvdyBidXR0b257ZmxleDowIDAgYXV0bztoZWln"
    "aHQ6MzBweDtwYWRkaW5nOjAgMTJweDtib3JkZXItcmFkaXVzOjk5cHg7Ym9yZGVyOjJweCBzb2xp"
    "ZCB2YXIoLS1saW5lKTsKICBiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIp"
    "O2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouN3JlbTtmb250LXdlaWdodDo3MDA7"
    "Y3Vyc29yOnBvaW50ZXI7d2hpdGUtc3BhY2U6bm93cmFwOwogIHRyYW5zaXRpb246dHJhbnNmb3Jt"
    "IC4ycyBjdWJpYy1iZXppZXIoLjMsMS41LC41LDEpLGJhY2tncm91bmQgLjJzLGNvbG9yIC4yc30K"
    "LmNoaXByb3cgYnV0dG9uLm9ue2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiNmZmZm"
    "ZmY7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50O3RyYW5zZm9ybTpzY2FsZSgxLjA0KX0KCi8qIHNr"
    "ZWxldG9ucyAoMTMgwrcgbG9hZGVyKSAqLwouc2t7cG9zaXRpb246cmVsYXRpdmU7b3ZlcmZsb3c6"
    "aGlkZGVuO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO2hl"
    "aWdodDo3NHB4O21hcmdpbi1ib3R0b206OXB4fQouc2s6YWZ0ZXJ7Y29udGVudDoiIjtwb3NpdGlv"
    "bjphYnNvbHV0ZTtpbnNldDowO2JhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRyYW5z"
    "cGFyZW50LGNvbG9yLW1peChpbiBzcmdiLHZhcigtLXR4KSAxMCUsdHJhbnNwYXJlbnQpLHRyYW5z"
    "cGFyZW50KTsKICB0cmFuc2Zvcm06dHJhbnNsYXRlWCgtMTAwJSk7YW5pbWF0aW9uOnNoaW0gMS4x"
    "NXMgaW5maW5pdGV9CkBrZXlmcmFtZXMgc2hpbXt0b3t0cmFuc2Zvcm06dHJhbnNsYXRlWCgxMDAl"
    "KX19CgovKiAxOSDCtyBGZWF0dXJlIHN0cmlwICovCi5mZWF0c3tkaXNwbGF5OmdyaWQ7Z3JpZC10"
    "ZW1wbGF0ZS1jb2x1bW5zOnJlcGVhdCgzLDFmcik7Z2FwOjhweDttYXJnaW4tYm90dG9tOjEycHh9"
    "Ci5mZWF0e2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1saW5l"
    "KTtib3JkZXItcmFkaXVzOnZhcigtLXJzKTtwYWRkaW5nOjExcHggOXB4O3RleHQtYWxpZ246Y2Vu"
    "dGVyOwogIHRyYW5zaXRpb246dHJhbnNmb3JtIC4yNXMgY3ViaWMtYmV6aWVyKC4yMiwxLC4zNiwx"
    "KX0KLmZlYXQ6aG92ZXJ7dHJhbnNmb3JtOnRyYW5zbGF0ZVkoLTNweCl9Ci5mZWF0IHN2Z3t3aWR0"
    "aDoxN3B4O2hlaWdodDoxN3B4O2NvbG9yOnZhcigtLWFjYyl9Ci5mZWF0IGJ7ZGlzcGxheTpibG9j"
    "aztmb250LXNpemU6LjY4cmVtO21hcmdpbi10b3A6NXB4fQouZmVhdCBzbWFsbHtkaXNwbGF5OmJs"
    "b2NrO2ZvbnQtc2l6ZTouNnJlbTtjb2xvcjp2YXIoLS10eDIpfQoKLyogMDMgwrcgV29ya3Mgd2hl"
    "ZWwg4oCUIHJvdGF0aW5nIGRlYWwgZGlhbCAqLwoud2hlZWx7cG9zaXRpb246cmVsYXRpdmU7aGVp"
    "Z2h0OjExOHB4O21hcmdpbjoycHggMCAxMnB4O292ZXJmbG93OmhpZGRlbjtib3JkZXItcmFkaXVz"
    "OnZhcigtLXIpOwogIGJvcmRlcjoycHggc29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIo"
    "LS1jYXJkKX0KLndoZWVsIC5yaW5ne3Bvc2l0aW9uOmFic29sdXRlO3JpZ2h0Oi01MnB4O3RvcDo1"
    "MCU7d2lkdGg6MTc2cHg7aGVpZ2h0OjE3NnB4O21hcmdpbi10b3A6LTg4cHg7Ym9yZGVyLXJhZGl1"
    "czo1MCU7CiAgYm9yZGVyOjFweCBkYXNoZWQgdmFyKC0tbGluZSk7YW5pbWF0aW9uOnNwaW4gMjZz"
    "IGxpbmVhciBpbmZpbml0ZX0KLndoZWVsIC5yaW5nIGJ7cG9zaXRpb246YWJzb2x1dGU7bGVmdDo1"
    "MCU7dG9wOjUwJTt3aWR0aDo1MHB4O2hlaWdodDoyNHB4O21hcmdpbjotMTJweCAwIDAgLTI1cHg7"
    "Ym9yZGVyLXJhZGl1czo5OXB4OwogIGRpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9u"
    "dC1zaXplOi42MnJlbTtmb250LXdlaWdodDo4MDA7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9y"
    "ZGVyOjJweCBzb2xpZCB2YXIoLS1saW5lKTtjb2xvcjp2YXIoLS10eDIpfQoud2hlZWwgLmNhcHtw"
    "b3NpdGlvbjphYnNvbHV0ZTtpbnNldDowO2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1"
    "bW47anVzdGlmeS1jb250ZW50OmNlbnRlcjtwYWRkaW5nOjAgMTZweDsKICBiYWNrZ3JvdW5kOmxp"
    "bmVhci1ncmFkaWVudCg5MGRlZyx2YXIoLS1jYXJkKSAwIDU4JSx0cmFuc3BhcmVudCl9Ci53aGVl"
    "bCAuY2FwPip7bWF4LXdpZHRoOjYyJX0KLndoZWVsIC5jYXAgZW17Zm9udC1zdHlsZTpub3JtYWw7"
    "Zm9udC1zaXplOi42cmVtO2xldHRlci1zcGFjaW5nOi4xOGVtO3RleHQtdHJhbnNmb3JtOnVwcGVy"
    "Y2FzZTtjb2xvcjp2YXIoLS1hY2MpO2ZvbnQtd2VpZ2h0OjgwMH0KLndoZWVsIC5jYXAgYntmb250"
    "LWZhbWlseTp2YXIoLS1maGVhZCk7Zm9udC1zaXplOjEuMDVyZW07Zm9udC13ZWlnaHQ6OTAwfQpA"
    "a2V5ZnJhbWVzIHNwaW57dG97dHJhbnNmb3JtOnJvdGF0ZSgzNjBkZWcpfX0KCi8qIHN1Y2Nlc3Mg"
    "YnVyc3QgKi8KI2Z4e3Bvc2l0aW9uOmZpeGVkO2luc2V0OjA7ei1pbmRleDoxNTA7ZGlzcGxheTpu"
    "b25lO3BsYWNlLWl0ZW1zOmNlbnRlcjtwb2ludGVyLWV2ZW50czpub25lfQojZngub257ZGlzcGxh"
    "eTpncmlkfQojZnggLnJpbmd7d2lkdGg6ODJweDtoZWlnaHQ6ODJweDtib3JkZXItcmFkaXVzOjUw"
    "JTtib3JkZXI6MnB4IHNvbGlkIHZhcigtLW9rKTtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2Vu"
    "dGVyOwogIGNvbG9yOnZhcigtLW9rKTthbmltYXRpb246YnVyc3QgLjc1cyBjdWJpYy1iZXppZXIo"
    "LjIsMS4zLC40LDEpfQojZnggLnJpbmcgc3Zne3dpZHRoOjM0cHg7aGVpZ2h0OjM0cHh9CiNmeCAu"
    "d2F2ZXtwb3NpdGlvbjphYnNvbHV0ZTt3aWR0aDo4MnB4O2hlaWdodDo4MnB4O2JvcmRlci1yYWRp"
    "dXM6NTAlO2JvcmRlcjoycHggc29saWQgdmFyKC0tb2spO2FuaW1hdGlvbjp3YXZlIC45cyBlYXNl"
    "LW91dCBmb3J3YXJkc30KQGtleWZyYW1lcyBidXJzdHtmcm9te3RyYW5zZm9ybTpzY2FsZSguNCk7"
    "b3BhY2l0eTowfXRve3RyYW5zZm9ybTpzY2FsZSgxKTtvcGFjaXR5OjF9fQpAa2V5ZnJhbWVzIHdh"
    "dmV7dG97dHJhbnNmb3JtOnNjYWxlKDMuNCk7b3BhY2l0eTowfX0KCi8qIG51bWJlciB0aWNrZXIg"
    "Ki8KLnRpY2t7Zm9udC12YXJpYW50LW51bWVyaWM6dGFidWxhci1udW1zfQoKQG1lZGlhKHByZWZl"
    "cnMtcmVkdWNlZC1tb3Rpb246cmVkdWNlKXsqe2FuaW1hdGlvbjpub25lIWltcG9ydGFudDt0cmFu"
    "c2l0aW9uOm5vbmUhaW1wb3J0YW50fX0KbmF2I3RhYnN7bGVmdDowO3JpZ2h0OjA7Ym90dG9tOjA7"
    "dHJhbnNmb3JtOm5vbmU7bWF4LXdpZHRoOjEwMCV9bmF2I3RhYnMgLmRvY2t7Ym9yZGVyLXJhZGl1"
    "czowO2JvcmRlci1sZWZ0OjA7Ym9yZGVyLXJpZ2h0OjA7Ym9yZGVyLWJvdHRvbTowO3BhZGRpbmc6"
    "NnB4IDZweCBjYWxjKDZweCArIGVudihzYWZlLWFyZWEtaW5zZXQtYm90dG9tKSk7anVzdGlmeS1j"
    "b250ZW50OnNwYWNlLWFyb3VuZH1uYXYjdGFicyBidXR0b257ZmxleDoxfQouaGVybzphZnRlcntj"
    "b250ZW50OidORVcnO3Bvc2l0aW9uOmFic29sdXRlO3JpZ2h0Oi0yNnB4O3RvcDoxNHB4O3RyYW5z"
    "Zm9ybTpyb3RhdGUoMzhkZWcpO2JhY2tncm91bmQ6IzExMTtjb2xvcjojZmZmO2ZvbnQtd2VpZ2h0"
    "OjkwMDtmb250LXNpemU6LjYycmVtO2xldHRlci1zcGFjaW5nOi4yZW07cGFkZGluZzo1cHggMzBw"
    "eH0uaGVybyAua2ljaywuaGVybyBwe2NvbG9yOiNmZmZmZmYhaW1wb3J0YW50O29wYWNpdHk6Ljky"
    "fS5oZXJvIC5idG4uZ2hvc3R7Y29sb3I6I2ZmZmZmZjtib3JkZXItY29sb3I6I2ZmZmZmZn0ucGls"
    "bHtib3JkZXItd2lkdGg6MnB4fS50Z3tib3JkZXItd2lkdGg6MnB4fS5idG4ueHN7Ym94LXNoYWRv"
    "dzoycHggMnB4IDAgIzExMTExMX0KPC9zdHlsZT4KPC9oZWFkPgo8Ym9keT4KCjxjYW52YXMgaWQ9"
    "ImtnIj48L2NhbnZhcz4KPGRpdiBpZD0ic3BsYXNoIj48ZGl2IGNsYXNzPSJtYXJrIiBpZD0ic3Bt"
    "YXJrIj5aVDwvZGl2PjxkaXYgY2xhc3M9ImRvdGdyaWQiIGlkPSJkZyI+PC9kaXY+CiAgPGg0IGlk"
    "PSJzcG5hbWUiPlpvcm8gVEcgU3RvcmU8L2g0PjxzbWFsbCBpZD0ic3B0YWciPk5vLW5vbnNlbnNl"
    "IGFjY291bnRzPC9zbWFsbD48L2Rpdj4KPHN2ZyB3aWR0aD0iMCIgaGVpZ2h0PSIwIiBzdHlsZT0i"
    "cG9zaXRpb246YWJzb2x1dGUiPjxkZWZzPgogIDxmaWx0ZXIgaWQ9ImxnIiB4PSIwJSIgeT0iMCUi"
    "IHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiIGNvbG9yLWludGVycG9sYXRpb24tZmlsdGVycz0i"
    "c1JHQiI+CiAgICA8ZmVUdXJidWxlbmNlIHR5cGU9ImZyYWN0YWxOb2lzZSIgYmFzZUZyZXF1ZW5j"
    "eT0iMC4wNSAwLjA1IiBudW1PY3RhdmVzPSIxIiBzZWVkPSIxIiByZXN1bHQ9InQiLz4KICAgIDxm"
    "ZUdhdXNzaWFuQmx1ciBpbj0idCIgc3RkRGV2aWF0aW9uPSIyIiByZXN1bHQ9ImIiLz4KICAgIDxm"
    "ZURpc3BsYWNlbWVudE1hcCBpbj0iU291cmNlR3JhcGhpYyIgaW4yPSJiIiBzY2FsZT0iNzAiIHhD"
    "aGFubmVsU2VsZWN0b3I9IlIiIHlDaGFubmVsU2VsZWN0b3I9IkIiLz4KICA8L2ZpbHRlcj48L2Rl"
    "ZnM+PC9zdmc+CjxkaXYgaWQ9ImZ4Ij48ZGl2IGNsYXNzPSJ3YXZlIj48L2Rpdj48ZGl2IGNsYXNz"
    "PSJyaW5nIj48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJy"
    "ZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMi40IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9r"
    "ZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yMCA2IDkgMTdsLTUtNSIvPjwvc3ZnPjwvZGl2"
    "PjwvZGl2Pgo8ZGl2IGlkPSJzdyI+PGVtPlByZXZpZXc8L2VtPgogIDxidXR0b24gZGF0YS1zdz0i"
    "YXBwIiBjbGFzcz0ib24iPlN0b3JlPC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXN3PSJhZG1pbiI+"
    "QWRtaW48L2J1dHRvbj4KPC9kaXY+Cgo8ZGl2IGlkPSJ2aWV3LWFwcCI+CjxkaXYgaWQ9InNoZWxs"
    "Ij4KICA8aGVhZGVyIGlkPSJ0b3BiYXIiPgogICAgPGRpdiBjbGFzcz0ibG9nbyI+WlQ8L2Rpdj4K"
    "ICAgIDxkaXYgY2xhc3M9ImJyYW5kIj48YiBpZD0iYm5hbWUiPlpvcm8gVEcgU3RvcmU8L2I+PHNw"
    "YW4gaWQ9ImJ0YWciPk5vLW5vbnNlbnNlIGFjY291bnRzPC9zcGFuPjwvZGl2PgogICAgPGJ1dHRv"
    "biBjbGFzcz0iaWNvbmNoaXAgaGlkZGVuIiBpZD0iY2FydGNoaXAiIHRpdGxlPSJDYXJ0Ij48c3Zn"
    "IHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0"
    "cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJv"
    "dW5kIj48cGF0aCBkPSJNNiAyIDMgNnYxNGEyIDIgMCAwIDAgMiAyaDE0YTIgMiAwIDAgMCAyLTJW"
    "NmwtMy00eiIvPjxwYXRoIGQ9Ik0zIDZoMTgiLz48cGF0aCBkPSJNMTYgMTBhNCA0IDAgMCAxLTgg"
    "MCIvPjwvc3ZnPjxpIGlkPSJjYXJ0biI+MDwvaT48L2J1dHRvbj4KICAgIDxidXR0b24gY2xhc3M9"
    "ImJhbGNoaXAiIGRhdGEtZ289IndhbGxldCI+PHNwYW4gY2xhc3M9InBsdXMiPis8L3NwYW4+4oK5"
    "PHNwYW4gaWQ9ImJhbCIgY2xhc3M9InRpY2siPjI0OC41MDwvc3Bhbj48L2J1dHRvbj4KICA8L2hl"
    "YWRlcj4KICA8bWFpbiBpZD0ic3RhZ2UiPjwvbWFpbj4KPC9kaXY+CjxuYXYgaWQ9InRhYnMiPjxk"
    "aXYgY2xhc3M9ImRvY2siPjxzcGFuIGNsYXNzPSJkcGlsbCIgaWQ9ImRwaWxsIj48L3NwYW4+CiAg"
    "PGJ1dHRvbiBkYXRhLXRhYj0ic2hvcCIgY2xhc3M9Im9uIj48c3ZnIHZpZXdCb3g9IjAgMCAyNCAy"
    "NCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ry"
    "b2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNNiAy"
    "IDMgNnYxNGEyIDIgMCAwIDAgMiAyaDE0YTIgMiAwIDAgMCAyLTJWNmwtMy00eiIvPjxwYXRoIGQ9"
    "Ik0zIDZoMTgiLz48cGF0aCBkPSJNMTYgMTBhNCA0IDAgMCAxLTggMCIvPjwvc3ZnPjxzcGFuIGRh"
    "dGEtbmF2PSJzaG9wIj5TaG9wPC9zcGFuPjwvYnV0dG9uPgogIDxidXR0b24gZGF0YS10YWI9Im9y"
    "ZGVycyI+PHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3VycmVu"
    "dENvbG9yIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxp"
    "bmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTIxIDhhMiAyIDAgMCAwLTEtMS43M2wtNy00YTIgMiAw"
    "IDAgMC0yIDBsLTcgNEEyIDIgMCAwIDAgMyA4djhhMiAyIDAgMCAwIDEgMS43M2w3IDRhMiAyIDAg"
    "MCAwIDIgMGw3LTRBMiAyIDAgMCAwIDIxIDE2WiIvPjxwYXRoIGQ9Im0zLjMgNyA4LjcgNSA4Ljct"
    "NSIvPjxwYXRoIGQ9Ik0xMiAyMlYxMiIvPjwvc3ZnPjxzcGFuIGRhdGEtbmF2PSJvcmRlcnMiPk9y"
    "ZGVyczwvc3Bhbj48L2J1dHRvbj4KICA8YnV0dG9uIGRhdGEtdGFiPSJlYXJuIj48c3ZnIHZpZXdC"
    "b3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13"
    "aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48"
    "cGF0aCBkPSJNMTMgMiAzIDE0aDlsLTEgOCAxMC0xMmgtOXoiLz48L3N2Zz48c3BhbiBkYXRhLW5h"
    "dj0iZWFybiI+RWFybjwvc3Bhbj48L2J1dHRvbj4KICA8YnV0dG9uIGRhdGEtdGFiPSJ3YWxsZXQi"
    "Pjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xv"
    "ciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9p"
    "bj0icm91bmQiPjxwYXRoIGQ9Ik0xOSA3VjVhMiAyIDAgMCAwLTItMkg1YTIgMiAwIDAgMCAwIDRo"
    "MTVhMiAyIDAgMCAxIDIgMnY5YTIgMiAwIDAgMS0yIDJINWEyIDIgMCAwIDEtMi0yVjUiLz48cGF0"
    "aCBkPSJNMTggMTJoLjAxIi8+PC9zdmc+PHNwYW4gZGF0YS1uYXY9IndhbGxldCI+V2FsbGV0PC9z"
    "cGFuPjwvYnV0dG9uPgogIDxidXR0b24gZGF0YS10YWI9InlvdSI+PHN2ZyB2aWV3Qm94PSIwIDAg"
    "MjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3VycmVudENvbG9yIiBzdHJva2Utd2lkdGg9IjIi"
    "IHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0i"
    "TTE5IDIxdi0yYTQgNCAwIDAgMC00LTRIOWE0IDQgMCAwIDAtNCA0djIiLz48Y2lyY2xlIGN4PSIx"
    "MiIgY3k9IjciIHI9IjQiLz48L3N2Zz48c3BhbiBkYXRhLW5hdj0ieW91Ij5Zb3U8L3NwYW4+PC9i"
    "dXR0b24+CjwvZGl2PjwvbmF2Pgo8L2Rpdj4KCjxkaXYgaWQ9InZpZXctYWRtaW4iIGNsYXNzPSJo"
    "aWRkZW4iPjxkaXYgaWQ9InNoZWxsIj4KICA8aGVhZGVyIGlkPSJ0b3BiYXIiPgogICAgPGRpdiBj"
    "bGFzcz0ibG9nbyI+PHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0i"
    "Y3VycmVudENvbG9yIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ry"
    "b2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTIwIDEzYzAgNS0zLjUgNy41LTcuNjYgOC45"
    "NWExIDEgMCAwIDEtLjY3LS4wMUM3LjUgMjAuNSA0IDE4IDQgMTNWNmExIDEgMCAwIDEgMS0xYzIg"
    "MCA0LjUtMS4yIDYuMjQtMi43MmExLjE3IDEuMTcgMCAwIDEgMS41MiAwQzE0LjUxIDMuODEgMTcg"
    "NSAxOSA1YTEgMSAwIDAgMSAxIDF6Ii8+PC9zdmc+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJicmFu"
    "ZCI+PGI+QWRtaW4gQ29uc29sZTwvYj48c3BhbiBpZD0iYWJuYW1lIj5ab3JvIFRHIFN0b3JlPC9z"
    "cGFuPjwvZGl2PgogICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5M"
    "aXZlPC9zcGFuPgogIDwvaGVhZGVyPgogIDxkaXYgY2xhc3M9InN1Ym5hdiIgaWQ9ImFzdWIiPjxz"
    "cGFuIGNsYXNzPSJ2cGlsbCIgaWQ9InZwaWxsIj48L3NwYW4+CiAgICA8YnV0dG9uIGRhdGEtYT0i"
    "ZGFzaCIgY2xhc3M9Im9uIj5EYXNoYm9hcmQ8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJj"
    "aGFubmVscyI+Q2hhbm5lbHM8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJsb2dzIj5Mb2dz"
    "PC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0icGF5Ij5BdXRvLVBheTwvYnV0dG9uPgogICAg"
    "PGJ1dHRvbiBkYXRhLWE9ImNvbnRlbnQiPkNvbnRlbnQ8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0"
    "YS1hPSJmZWF0dXJlcyI+RmVhdHVyZXM8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJtYXJr"
    "ZXQiPk1hcmtldGluZzwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9ImFuYWx5dGljcyI+QW5h"
    "bHl0aWNzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0ic2VjdXJpdHkiPlNlY3VyaXR5PC9i"
    "dXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0ic3RvY2siPlN0b2NrPC9idXR0b24+CiAgICA8YnV0"
    "dG9uIGRhdGEtYT0ib3JkZXJzIj5PcmRlcnM8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJ1"
    "c2VycyI+VXNlcnM8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJzZXR0aW5ncyI+U2V0dGlu"
    "Z3M8L2J1dHRvbj4KICA8L2Rpdj4KICA8bWFpbiBpZD0iYXN0YWdlIj48L21haW4+CjwvZGl2Pjwv"
    "ZGl2PgoKPGRpdiBpZD0ic2hlZXQiPjxkaXYgY2xhc3M9ImJnIiBkYXRhLWNsb3NlPjwvZGl2Pjxk"
    "aXYgY2xhc3M9InBhbmVsIiBpZD0icGFuZWwiPjwvZGl2PjwvZGl2Pgo8ZGl2IGNsYXNzPSJ0b2Fz"
    "dCIgaWQ9InRvYXN0Ij48L2Rpdj4KCjxzY3JpcHQ+Ci8qIOKUgOKUgCBsaXZlLW1vZGUgc3RhdGUs"
    "IGRlY2xhcmVkIGZpcnN0IHNvIG5vdGhpbmcgY2FuIHJlYWQgaXQgdG9vIGVhcmx5IOKUgOKUgCAq"
    "Lwp2YXIgTElWRT1mYWxzZSxMT0NLRUQ9ZmFsc2UsTUU9e2FkbWluOmZhbHNlLG93bmVyOmZhbHNl"
    "LHBlcm1zOnt9fTsKdmFyIFRHPXdpbmRvdy5UZWxlZ3JhbSYmd2luZG93LlRlbGVncmFtLldlYkFw"
    "cHx8bnVsbDsKZnVuY3Rpb24gcmVhZEluaXREYXRhKCl7CiAgdHJ5e2lmKFRHJiZURy5pbml0RGF0"
    "YSYmVEcuaW5pdERhdGEubGVuZ3RoKXJldHVybiBURy5pbml0RGF0YTt9Y2F0Y2goZSl7fQogIC8q"
    "IFRlbGVncmFtIGFsc28gcHV0cyBpdCBpbiB0aGUgVVJMLCBzbyB0aGUgYXBwIHN0aWxsIHdvcmtz"
    "IGlmIHRoZSBTREsgaXMgYmxvY2tlZCAqLwogIHRyeXsKICAgIGNvbnN0IG09L1sjJj9ddGdXZWJB"
    "cHBEYXRhPShbXiZdKikvLmV4ZWMobG9jYXRpb24uaGFzaHx8IiIpOwogICAgaWYobSYmbVsxXSly"
    "ZXR1cm4gZGVjb2RlVVJJQ29tcG9uZW50KG1bMV0pOwogIH1jYXRjaChlKXt9CiAgdHJ5ewogICAg"
    "Y29uc3QgcT1uZXcgVVJMU2VhcmNoUGFyYW1zKGxvY2F0aW9uLnNlYXJjaCk7CiAgICBjb25zdCB2"
    "PXEuZ2V0KCJ0Z1dlYkFwcERhdGEiKTsKICAgIGlmKHYpcmV0dXJuIHY7CiAgfWNhdGNoKGUpe30K"
    "ICB0cnl7CiAgICBjb25zdCBzYXZlZD1zZXNzaW9uU3RvcmFnZS5nZXRJdGVtKCJ0Z0luaXQiKTsK"
    "ICAgIGlmKHNhdmVkKXJldHVybiBzYXZlZDsKICB9Y2F0Y2goZSl7fQogIHJldHVybiAiIjsKfQpm"
    "dW5jdGlvbiBpbml0aWFscyhuYW1lKXsKICBjb25zdCB3PShuYW1lfHwiIikudHJpbSgpLnNwbGl0"
    "KC9ccysvKS5maWx0ZXIoQm9vbGVhbik7CiAgaWYoIXcubGVuZ3RoKXJldHVybiAiU1QiOwogIHJl"
    "dHVybiAoKHdbMF1bMF18fCIiKSsody5sZW5ndGg+MT8od1sxXVswXXx8IiIpOih3WzBdWzFdfHwi"
    "IikpKS50b1VwcGVyQ2FzZSgpOwp9CmZ1bmN0aW9uIHBhaW50QnJhbmQobmFtZSx0YWcpewogIGlm"
    "KG5hbWUpewogICAgY29uc3Qgbj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3BuYW1lIik7IGlm"
    "KG4pbi50ZXh0Q29udGVudD1uYW1lOwogICAgY29uc3QgbT1kb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgic3BtYXJrIik7IGlmKG0pbS50ZXh0Q29udGVudD1pbml0aWFscyhuYW1lKTsKICAgIGNvbnN0"
    "IGI9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJuYW1lIik7IGlmKGIpYi50ZXh0Q29udGVudD1u"
    "YW1lOwogICAgY29uc3QgYWI9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFibmFtZSIpOyBpZihh"
    "YilhYi50ZXh0Q29udGVudD1uYW1lOwogICAgZG9jdW1lbnQudGl0bGU9bmFtZTsKICB9CiAgaWYo"
    "dGFnKXsKICAgIGNvbnN0IHQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNwdGFnIik7IGlmKHQp"
    "dC50ZXh0Q29udGVudD10YWc7CiAgICBjb25zdCBidD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgi"
    "YnRhZyIpOyBpZihidClidC50ZXh0Q29udGVudD10YWc7CiAgfQp9CnRyeXsKICBjb25zdCBjbj1s"
    "b2NhbFN0b3JhZ2UuZ2V0SXRlbSgic3RvcmVOYW1lIiksIGN0PWxvY2FsU3RvcmFnZS5nZXRJdGVt"
    "KCJzdG9yZVRhZyIpOwogIGlmKGNufHxjdClwYWludEJyYW5kKGNuLGN0KTsKfWNhdGNoKGUpe30K"
    "dmFyIElOSVQ9cmVhZEluaXREYXRhKCk7CnRyeXtpZihJTklUKXNlc3Npb25TdG9yYWdlLnNldEl0"
    "ZW0oInRnSW5pdCIsSU5JVCk7fWNhdGNoKGUpe30KZnVuY3Rpb24gcmVhZEFwcEtleSgpewogIHRy"
    "eXsKICAgIGNvbnN0IHE9bmV3IFVSTFNlYXJjaFBhcmFtcyhsb2NhdGlvbi5zZWFyY2gpOwogICAg"
    "aWYocS5nZXQoImsiKSlyZXR1cm4gcS5nZXQoImsiKTsKICAgIGNvbnN0IG09L1sjJj9daz0oW14m"
    "XSopLy5leGVjKGxvY2F0aW9uLmhhc2h8fCIiKTsKICAgIGlmKG0mJm1bMV0pcmV0dXJuIGRlY29k"
    "ZVVSSUNvbXBvbmVudChtWzFdKTsKICAgIHJldHVybiBzZXNzaW9uU3RvcmFnZS5nZXRJdGVtKCJh"
    "cHBLZXkiKXx8IiI7CiAgfWNhdGNoKGUpe3JldHVybiAiIjt9Cn0KdmFyIEFQUEtFWT1yZWFkQXBw"
    "S2V5KCk7CnRyeXtpZihBUFBLRVkpc2Vzc2lvblN0b3JhZ2Uuc2V0SXRlbSgiYXBwS2V5IixBUFBL"
    "RVkpO31jYXRjaChlKXt9Ci8qIGRlbW8gZGF0YSBvbmx5IOKAlCBubyB0b2tlbnMsIG5vIFVQSSBJ"
    "RCwgbm8gR21haWwgY3JlZGVudGlhbHMgKi8KbGV0IENGRz17bmFtZToiWm9ybyBURyBTdG9yZSIs"
    "dGFnOiJOby1ub25zZW5zZSBhY2NvdW50cyIsY3VyOiLigrkifTsKLyog4pSA4pSAIEVkaXRhYmxl"
    "IGNvcHkg4oCUIGV2ZXJ5IHN0cmluZyBiZWxvdyBpcyBlZGl0YWJsZSBpbiBBZG1pbiDilrggQ29u"
    "dGVudCDilIDilIAgKi8KY29uc3QgQ01TPXsKIHN0b3JlTmFtZToiWm9ybyBURyBTdG9yZSIsIHN0"
    "b3JlVGFnOiJOby1ub25zZW5zZSBhY2NvdW50cyIsCiBlbXB0eVRpdGxlOiJSZXN0b2NraW5nIiwg"
    "ZW1wdHlCb2R5OiJFdmVyeXRoaW5nIGp1c3Qgc29sZCBvdXQuIEZyZXNoIGFjY291bnRzIGxhbmQg"
    "aGVyZSBzaG9ydGx5IOKAlCB0dXJuIG9uIGFuIGFsZXJ0IGFuZCB5b3Ugd2lsbCBoZWFyIGZpcnN0"
    "LiIsCiB0aWNrMToiPGI+SW5zdGFudCBkZWxpdmVyeTwvYj4gb24gZXZlcnkgY291bnRyeSIsIHRp"
    "Y2syOiJUb3AtdXBzIDxiPmNvbmZpcm1lZCBhdXRvbWF0aWNhbGx5PC9iPiIsCiB0aWNrMzoiRmFp"
    "bGVkIGxvZ2luPyA8Yj5BdXRvIHJlZnVuZDwvYj4iLCB0aWNrNDoiRGFpbHkgY2hlY2staW4gPGI+"
    "4oK5MC40MDwvYj4iLAogaGVyb0tpY2s6IkZSRVNIIFNUT0NLIiwgaGVyb0gxOiJCVVkuPGJyPkdF"
    "VCBPVFAuPGJyPkRPTkUuIiwKIGhlcm9TdWI6Ikluc3RhbnQgZGVsaXZlcnksIE9UUCBpbi1hcHAs"
    "IHdhbGxldCB0b3AtdXBzIHZlcmlmaWVkIGF1dG9tYXRpY2FsbHkuIHtufSBjb3VudHJpZXMgbGl2"
    "ZS4iLAogaGVyb0J0bjE6IkJyb3dzZSBTdG9jayIsIGhlcm9CdG4yOiJBZGQgRnVuZHMiLAogZmVh"
    "dDE6In44cyIsIGZlYXQxczoiZGVsaXZlcnkiLCBmZWF0MjoiQXV0byIsIGZlYXQyczoicmVmdW5k"
    "cyIsIGZlYXQzOiJVUEkiLCBmZWF0M3M6ImF1dG8tdmVyaWZ5IiwKIGZsYXNoUGlsbDoiRmxhc2gg"
    "4oiSMjAlIiwgZmxhc2hUZXh0OiJFbmRzIGluIiwgZmxhc2hCdG46IlZpZXciLAogd2hlZWxLaWNr"
    "OiJSb3RhdGluZyBkZWFscyIsIHdoZWVsVGl0bGU6IlVwIHRvIDI4JSBvZmYiLCB3aGVlbFdvcmRz"
    "OiJJTlNUQU5ULCBBVVRPLVZFUklGWSwgUkVGVU5EUywgMjTDlzciLAogc2hvcEhlYWQ6IkluIFN0"
    "b2NrIiwgc2hvcFNvcnQ6IlNvcnQiLCBzZWFyY2hQaDoiU2VhcmNoIGNvdW50cnkgb3IgY29kZeKA"
    "piIsIGFsZXJ0c0J0bjoiQWxlcnRzIiwKIHdhbEtpY2s6IkJhbGFuY2UiLCB3YWxTdWI6IlBheSBi"
    "eSBVUEkg4oCUIHRhcCBWZXJpZnkgYWZ0ZXIgcGF5aW5nIGFuZCB5b3VyIHdhbGxldCBpcyBjcmVk"
    "aXRlZCBpbiBzZWNvbmRzLiIsCiB3YWxBZGQ6IkFkZCBGdW5kcyIsIHBheVRpdGxlOiJQYXltZW50"
    "IERldGFpbHMiLCBwYXlTdWI6IkFkbWluIHNldHMgdGhlIFVQSSBoYW5kbGUg4oCUIG5vdGhpbmcg"
    "aXMgaGFyZC1jb2RlZCBpbiB0aGUgYXBwLiIsCiBwYXlQcmVmaXg6IlBBWSIsIHBheVdpbmRvdzoi"
    "NSIsCiBzaGVldFRpdGxlOiJBZGQiLCBzaGVldFN1YjoiUGF5IHRoZSBleGFjdCBhbW91bnQsIHRo"
    "ZW4gdGFwIFZlcmlmeSBQYXltZW50LiBUaGUgcmVxdWVzdCBleHBpcmVzIGluIHttfSBtaW51dGVz"
    "LiIsCiBwYXlCdG46IlBheSBOb3ciLCB2ZXJpZnlCdG46IlZlcmlmeSBQYXltZW50IiwgdXRyQnRu"
    "OiJOb3QgZGV0ZWN0ZWQ/IFN1Ym1pdCBVVFIiLAogc3RlcDE6IlBheW1lbnQgcmVxdWVzdCBjcmVh"
    "dGVkIiwgc3RlcDI6IlBheSB0aGUgZXhhY3QgYW1vdW50IGluIGFueSBVUEkgYXBwIiwKIHN0ZXAz"
    "OiJUYXAgVmVyaWZ5IOKAlCB3ZSBjb25maXJtIHlvdXIgcGF5bWVudCIsIHN0ZXA0OiJXYWxsZXQg"
    "Y3JlZGl0ZWQiLAogbXNnQ2hlY2tpbmc6IkNoZWNraW5nIHlvdXIgcGF5bWVudOKApiIsIG1zZ09r"
    "OiJWZXJpZmllZCDigJQgd2FsbGV0IGNyZWRpdGVkIiwKIG1zZ05vbmU6Ik5vIHBheW1lbnQgZm91"
    "bmQgeWV0LiBQYXkgZmlyc3QsIHRoZW4gdGFwIFZlcmlmeS4iLCBtc2dFeHBpcmVkOiJSZXF1ZXN0"
    "IGV4cGlyZWQg4oCUIGNyZWF0ZSBhIG5ldyBvbmUuIiwKIG5hdlNob3A6IlNob3AiLCBuYXZPcmRl"
    "cnM6Ik9yZGVycyIsIG5hdkVhcm46IkVhcm4iLCBuYXZXYWxsZXQ6IldhbGxldCIsIG5hdllvdToi"
    "WW91Igp9Owpjb25zdCBDTVMwPU9iamVjdC5hc3NpZ24oe30sQ01TKTsKY29uc3QgQ01TTUVUQT1b"
    "CiBbIlN0b3JlIGhlYWRlciIsW1sic3RvcmVOYW1lIiwiU3RvcmUgbmFtZSJdLFsic3RvcmVUYWci"
    "LCJUYWdsaW5lIl1dXSwKIFsiU2Nyb2xsaW5nIHRpY2tlciIsW1sidGljazEiLCJUaWNrZXIgMSJd"
    "LFsidGljazIiLCJUaWNrZXIgMiJdLFsidGljazMiLCJUaWNrZXIgMyJdLFsidGljazQiLCJUaWNr"
    "ZXIgNCJdXV0sCiBbIkhlcm8gc2VjdGlvbiIsW1siaGVyb0tpY2siLCJTbWFsbCBsYWJlbCBhYm92"
    "ZSB0aXRsZSJdLFsiaGVyb0gxIiwiQmlnIHRpdGxlIl0sWyJoZXJvU3ViIiwiUGFyYWdyYXBoIHVu"
    "ZGVyIHRoZSB0aXRsZSJdLFsiaGVyb0J0bjEiLCJMZWZ0IGJ1dHRvbiJdLFsiaGVyb0J0bjIiLCJS"
    "aWdodCBidXR0b24iXV1dLAogWyJGZWF0dXJlIGJhZGdlcyIsW1siZmVhdDEiLCJCYWRnZSAxIHZh"
    "bHVlIl0sWyJmZWF0MXMiLCJCYWRnZSAxIGNhcHRpb24iXSxbImZlYXQyIiwiQmFkZ2UgMiB2YWx1"
    "ZSJdLFsiZmVhdDJzIiwiQmFkZ2UgMiBjYXB0aW9uIl0sWyJmZWF0MyIsIkJhZGdlIDMgdmFsdWUi"
    "XSxbImZlYXQzcyIsIkJhZGdlIDMgY2FwdGlvbiJdXV0sCiBbIkZsYXNoIHNhbGUgc3RyaXAiLFtb"
    "ImZsYXNoUGlsbCIsIlBpbGwgdGV4dCJdLFsiZmxhc2hUZXh0IiwiQ291bnRkb3duIGxhYmVsIl0s"
    "WyJmbGFzaEJ0biIsIkJ1dHRvbiJdXV0sCiBbIlJvdGF0aW5nIGRlYWxzIGNhcmQiLFtbIndoZWVs"
    "S2ljayIsIlNtYWxsIGxhYmVsIl0sWyJ3aGVlbFRpdGxlIiwiSGVhZGxpbmUiXSxbIndoZWVsV29y"
    "ZHMiLCJNYXJxdWVlIHdvcmRzIChjb21tYSBzZXBhcmF0ZWQpIl1dXSwKIFsiU2hvcCBsaXN0Iixb"
    "WyJzaG9wSGVhZCIsIlNlY3Rpb24gaGVhZGluZyJdLFsic2hvcFNvcnQiLCJTb3J0IGxpbmsiXSxb"
    "InNlYXJjaFBoIiwiU2VhcmNoIHBsYWNlaG9sZGVyIl0sWyJhbGVydHNCdG4iLCJBbGVydHMgYnV0"
    "dG9uIl1dXSwKIFsiV2FsbGV0IHNjcmVlbiIsW1sid2FsS2ljayIsIkxhYmVsIGFib3ZlIGJhbGFu"
    "Y2UiXSxbIndhbFN1YiIsIkJhbGFuY2UgZGVzY3JpcHRpb24iXSxbIndhbEFkZCIsIkFkZCBmdW5k"
    "cyBidXR0b24iXSxbInBheVRpdGxlIiwiUGF5bWVudCBjYXJkIHRpdGxlIl0sWyJwYXlTdWIiLCJQ"
    "YXltZW50IGNhcmQgZGVzY3JpcHRpb24iXV1dLAogWyJQYXltZW50IHNoZWV0IixbWyJzaGVldFRp"
    "dGxlIiwiU2hlZXQgdGl0bGUgcHJlZml4Il0sWyJzaGVldFN1YiIsIlNoZWV0IGRlc2NyaXB0aW9u"
    "ICh7bX0gPSBtaW51dGVzKSJdLFsicGF5QnRuIiwiUGF5IGJ1dHRvbiJdLFsidmVyaWZ5QnRuIiwi"
    "VmVyaWZ5IGJ1dHRvbiJdLFsidXRyQnRuIiwiTWFudWFsIGZhbGxiYWNrIGJ1dHRvbiJdLFsicGF5"
    "UHJlZml4IiwiUmVmZXJlbmNlIHByZWZpeCJdLFsicGF5V2luZG93IiwiUGF5bWVudCB3aW5kb3cg"
    "KG1pbnV0ZXMpIl1dXSwKIFsiUGF5bWVudCBzdGVwcyIsW1sic3RlcDEiLCJTdGVwIDEiXSxbInN0"
    "ZXAyIiwiU3RlcCAyIl0sWyJzdGVwMyIsIlN0ZXAgMyJdLFsic3RlcDQiLCJTdGVwIDQiXV1dLAog"
    "WyJQYXltZW50IG1lc3NhZ2VzIixbWyJtc2dDaGVja2luZyIsIldoaWxlIGNoZWNraW5nIl0sWyJt"
    "c2dPayIsIlZlcmlmaWVkIl0sWyJtc2dOb25lIiwiTm90aGluZyBmb3VuZCJdLFsibXNnRXhwaXJl"
    "ZCIsIkV4cGlyZWQiXV1dLAogWyJCb3R0b20gbmF2aWdhdGlvbiIsW1sibmF2U2hvcCIsIlRhYiAx"
    "Il0sWyJuYXZPcmRlcnMiLCJUYWIgMiJdLFsibmF2RWFybiIsIlRhYiAzIl0sWyJuYXZXYWxsZXQi"
    "LCJUYWIgNCJdLFsibmF2WW91IiwiVGFiIDUiXV1dCl07Ci8qIOKVkOKVkCBGRUFUVVJFIEZMQUdT"
    "IOKAlCBldmVyeSBmZWF0dXJlIGJlbG93IGlzIG9mZi1hYmxlOyB3aGVuIG9mZiBub3RoaW5nIHJl"
    "bmRlcnMg4pWQ4pWQICovCmNvbnN0IEZHUk9VUFM9WwogWyJQYXltZW50cyAmIHdhbGxldCIsWwog"
    "IFsiY3J5cHRvIiwiQ3J5cHRvIC8gVVNEVCB0b3AtdXAiLCJBY2NlcHQgVVNEVC1UUkMyMCwgQlRD"
    "IGV0Yy4gd2l0aCBhdXRvLWNvbmZpcm0gZnJvbSB0aGUgZXhwbG9yZXIuIl0sCiAgWyJ1cGlSb3Rh"
    "dGUiLCJNdWx0aXBsZSBVUEkgSURzICsgcm90YXRpb24iLCJSb3RhdGUgaGFuZGxlcyBwZXIgcGF5"
    "bWVudCBvciBhdXRvLXN3aXRjaCB3aGVuIG9uZSBpcyBsaW1pdGVkLiJdLAogIFsiYXV0b1JlZnVu"
    "ZCIsIkF1dG8tcmVmdW5kIHRvIHdhbGxldCIsIkRlYWQgYWNjb3VudCBpbnNpZGUgdGhlIHdhcnJh"
    "bnR5IHdpbmRvdyBpcyByZWZ1bmRlZCB3aXRob3V0IHN1cHBvcnQuIl0sCiAgWyJzcGxpdFBheSIs"
    "IlNwbGl0IHBheW1lbnQiLCJQYXkgcGFydCBmcm9tIGJhbGFuY2UgYW5kIHRoZSByZXN0IGJ5IFVQ"
    "SSBpbiBvbmUgZmxvdy4iXSwKICBbImxlZGdlciIsIldhbGxldCBsZWRnZXIgc3RhdGVtZW50Iiwi"
    "RnVsbCBjcmVkaXQvZGViaXQgaGlzdG9yeSB3aXRoIHJ1bm5pbmcgYmFsYW5jZSBhbmQgQ1NWIGV4"
    "cG9ydC4iXSwKICBbImRlcExpbWl0cyIsIk1pbiAvIG1heCBkZXBvc2l0IHJ1bGVzIiwiR2xvYmFs"
    "IG9yIHBlci11c2VyIGNhcHMgb24gYSBzaW5nbGUgdG9wLXVwLiJdLAogIFsiZGVwQm9udXMiLCJE"
    "ZXBvc2l0IGJvbnVzIHRpZXJzIiwiQWRkIOKCuTUwMCBnZXQgNSUgZXh0cmEg4oCUIGNvbmZpZ3Vy"
    "YWJsZSBzbGFicywgYXV0by1hcHBsaWVkLiJdLAogIFsid3JvbmdBbXQiLCJXcm9uZy1hbW91bnQg"
    "aGFuZGxpbmciLCJDcmVkaXQgd2hhdGV2ZXIgd2FzIGFjdHVhbGx5IHBhaWQgYW5kIGZsYWcgdGhl"
    "IG1pc21hdGNoLiJdXV0sCiBbIkNhdGFsb2cgJiBzZWxsaW5nIixbCiAgWyJjYXJ0IiwiU2hvcHBp"
    "bmcgY2FydCAvIGJ1bGsgYnV5IiwiTWl4IGNvdW50cmllcyBpbiBvbmUgY2hlY2tvdXQsIG9uZSBj"
    "b25maXJtYXRpb24sIG9uZSBkZWJpdC4iXSwKICBbImJ1bGtEaXNjIiwiQnVsayBwdXJjaGFzZSBk"
    "aXNjb3VudCIsIkF1dG9tYXRpYyBwcmljZSBicmVhayBhdCA1IC8gMTAgLyAyNSB1bml0cy4iXSwK"
    "ICBbIndpc2hsaXN0IiwiV2lzaGxpc3QgLyBmYXZvdXJpdGVzIiwiU2F2ZSBjb3VudHJpZXMgYW5k"
    "IHJlYnV5IGluIG9uZSB0YXAuIl0sCiAgWyJkeW5QcmljZSIsIkR5bmFtaWMgcHJpY2luZyIsIlBy"
    "aWNlIG1vdmVzIHdpdGggc3RvY2sgbGV2ZWwsIGFnZSB0aWVyIG9yIHRpbWUgb2YgZGF5LiJdLAog"
    "IFsiZmxhc2hTYWxlIiwiRmxhc2ggc2FsZSIsIlRpbWVkIGRpc2NvdW50IHdpdGggYSBsaXZlIGNv"
    "dW50ZG93biBvbiB0aGUgc3RvcmVmcm9udC4iXSwKICBbImNvdXBvbnMiLCJDb3Vwb24gY29kZXMi"
    "LCJQZXJjZW50YWdlIG9mZiBhdCBjaGVja291dCB3aXRoIGxpbWl0cyBhbmQgZXhwaXJ5LiJdLAog"
    "IFsidmFyaWFudHMiLCJBY2NvdW50IHZhcmlhbnRzIiwiU3BhbS1ibG9jaywgMkZBLCB1c2VybmFt"
    "ZSwgbWFpbCDigJQgZmlsdGVyYWJsZSBhbmQgcHJpY2VkIGFwYXJ0LiJdLAogIFsiaGVhbHRoQ2hl"
    "Y2siLCJTdG9jayBoZWFsdGggY2hlY2siLCJTZXNzaW9uIGlzIHZhbGlkYXRlZCBsaXZlIGJlZm9y"
    "ZSBpdCBpcyBzb2xkLiJdXV0sCiBbIk9yZGVycyAmIGFmdGVyLXNhbGUiLFsKICBbInRpbWVsaW5l"
    "IiwiT3JkZXIgc3RhdHVzIHRpbWVsaW5lIiwiUGVuZGluZywgZGVsaXZlcmVkLCBPVFAgcmVjZWl2"
    "ZWQsIGNsb3NlZCDigJQgdmlzaWJsZSB0byBib3RoIHNpZGVzLiJdLAogIFsid2FycmFudHkiLCJS"
    "ZXBsYWNlbWVudCAvIHdhcnJhbnR5IiwiUmVwb3J0IGEgcHJvYmxlbSBpbnNpZGUgWCBob3VycyBm"
    "b3IgYW4gYXV0by1yZXBsYWNlbWVudC4iXSwKICBbInJlZG93bmxvYWQiLCJSZS1kb3dubG9hZCBk"
    "ZXRhaWxzIiwiRmV0Y2ggYSBwYXN0IG9yZGVyJ3MgZGV0YWlscyBhZ2FpbiwgcmF0ZS1saW1pdGVk"
    "LiJdLAogIFsiZGlzcHV0ZSIsIkRpc3B1dGUgZmxvdyIsIlJlYXNvbiBwaWNrZXIgcGx1cyBldmlk"
    "ZW5jZSBpbnN0ZWFkIG9mIGZyZWUtdGV4dCB0aWNrZXRzLiJdLAogIFsic2Vzc2lvbkZpbGUiLCJT"
    "ZXNzaW9uIGZpbGUgZGVsaXZlcnkiLCJTZW5kIC5zZXNzaW9uIC8gSlNPTiBhcyBhIGRvY3VtZW50"
    "IHdpdGggYSBzZWxmLWRlc3RydWN0IHRpbWVyLiJdLAogIFsicmVjZWlwdHMiLCJPcmRlciByZWNl"
    "aXB0cyIsIkF1dG8tZ2VuZXJhdGVkIGludm9pY2UgcGVyIHB1cmNoYXNlLiJdXV0sCiBbIlRydXN0"
    "ICYgc29jaWFsIHByb29mIixbCiAgWyJyZXZpZXdzIiwiUmV2aWV3cyAmIHN0YXIgcmF0aW5ncyIs"
    "IkJ1eWVyIHJhdGVzIGFmdGVyIGRlbGl2ZXJ5OyBhdmVyYWdlcyBzaG93IG9uIGV2ZXJ5IGNvdW50"
    "cnkuIl0sCiAgWyJzYWxlc0ZlZWQiLCJMaXZlIHNhbGVzIGZlZWQiLCJSZWNlbnQgc2FsZXMgc2hv"
    "d24gaW4tYXBwIGFuZCBhdXRvLXBvc3RlZCB0byB5b3VyIGNoYW5uZWwuIl0sCiAgWyJsZWFkZXJi"
    "b2FyZCIsIkxlYWRlcmJvYXJkIiwiVG9wIGJ1eWVycyBhbmQgcmVmZXJyZXJzIG9mIHRoZSB3ZWVr"
    "LCB3aXRoIGEgcHJpemUuIl0sCiAgWyJidXllckJhZGdlIiwiVmVyaWZpZWQgYnV5ZXIgYmFkZ2Ui"
    "LCJCYWRnZSBhbmQgcGVya3MgYWZ0ZXIgTiBjb21wbGV0ZWQgb3JkZXJzLiJdXV0sCiBbIkdyb3d0"
    "aCAmIHJldGVudGlvbiIsWwogIFsicmVmTDIiLCJNdWx0aS1sZXZlbCByZWZlcnJhbCIsIkxldmVs"
    "IDEgYW5kIGxldmVsIDIgY29tbWlzc2lvbiBpbnN0ZWFkIG9mIGEgZmxhdCByYXRlLiJdLAogIFsi"
    "cmVmRGFzaCIsIlJlZmVycmFsIGRhc2hib2FyZCIsIldobyBqb2luZWQsIHdobyBkZXBvc2l0ZWQg"
    "YW5kIHdoYXQgZWFjaCBpbnZpdGUgZWFybmVkLiJdLAogIFsic3RyZWFrIiwiQ2hlY2staW4gc3Ry"
    "ZWFrIHJld2FyZHMiLCJFc2NhbGF0aW5nIGRhaWx5IHJld2FyZCB0aGF0IHJlc2V0cyB3aGVuIGJy"
    "b2tlbi4iXSwKICBbImxveWFsdHkiLCJMb3lhbHR5IHBvaW50cyIsIkVhcm4gcGVyIOKCuSBzcGVu"
    "dCwgcmVkZWVtIGZvciBiYWxhbmNlIG9yIGEgZnJlZSBhY2NvdW50LiJdLAogIFsid2luYmFjayIs"
    "IlJlLWVuZ2FnZW1lbnQgRE1zIiwiQXV0byBtZXNzYWdlIHVzZXJzIGluYWN0aXZlIGZvciA3IC8g"
    "MzAgZGF5cy4iXSwKICBbImFiYW5kb25SZW1pbmQiLCJBYmFuZG9uZWQtZGVwb3NpdCByZW1pbmRl"
    "ciIsIk51ZGdlIGJlZm9yZSBhbiB1bmZpbmlzaGVkIHBheW1lbnQgZXhwaXJlcy4iXSwKICBbInNj"
    "aGVkQnJvYWRjYXN0IiwiU2NoZWR1bGVkICYgc2VnbWVudGVkIGJyb2FkY2FzdHMiLCJRdWV1ZSBm"
    "b3IgbGF0ZXIsIHRhcmdldCBidXllcnMsIG5vbi1idXllcnMgb3IgYmlnIHNwZW5kZXJzLiJdXV0s"
    "CiBbIkFkbWluICYgb3BlcmF0aW9ucyIsWwogIFsiYWRtaW5Sb2xlcyIsIkFkbWluIHJvbGVzIiwi"
    "T3duZXIgLyBtYW5hZ2VyIC8gc3VwcG9ydCB3aXRoIHNlcGFyYXRlIHBlcm1pc3Npb25zLiJdLAog"
    "IFsiYXVkaXRMb2ciLCJBdWRpdCBsb2ciLCJFdmVyeSBhZG1pbiBhY3Rpb24gbG9nZ2VkIHdpdGgg"
    "dGltZXN0YW1wIGFuZCB1bmRvLiJdLAogIFsiY29udGVudEVkaXRvciIsIkluLWJvdCBjb250ZW50"
    "IGVkaXRvciIsIkVkaXQgZXZlcnkgbWVzc2FnZSBhbmQgYnV0dG9uIHdpdGhvdXQgdG91Y2hpbmcg"
    "Y29kZS4iXSwKICBbImF1dG9CYWNrdXAiLCJTY2hlZHVsZWQgYXV0by1iYWNrdXAiLCJEYXRhYmFz"
    "ZSB0byBhIHByaXZhdGUgY2hhbm5lbCBldmVyeSBOIGhvdXJzLCBvbmUtdGFwIHJlc3RvcmUuIl0s"
    "CiAgWyJidWxrVXBsb2FkIiwiQnVsayB1cGxvYWQgd2l0aCB2YWxpZGF0aW9uIiwiVmFsaWRhdGUg"
    "ZWFjaCBzZXNzaW9uLCByZXBvcnQgZGVhZCBvciBkdXBsaWNhdGUsIGltcG9ydCB0aGUgcmVzdC4i"
    "XSwKICBbImR1cERldGVjdCIsIkR1cGxpY2F0ZSBkZXRlY3Rpb24iLCJSZWplY3QgYSBwaG9uZSBv"
    "ciBzZXNzaW9uIGFscmVhZHkgaW4gdGhlIGRhdGFiYXNlLiJdLAogIFsibG93U3RvY2siLCJMb3ct"
    "c3RvY2sgYWxlcnRzIiwiUGluZyB3aGVuIGEgY291bnRyeSBkcm9wcyBiZWxvdyBpdHMgb3duIHRo"
    "cmVzaG9sZC4iXSwKICBbInNlbGxIb3VycyIsIlNlbGxpbmctaG91cnMgc2NoZWR1bGUiLCJBdXRv"
    "LWhpZGUgY291bnRyaWVzIG91dHNpZGUgdGhlaXIgc2VsbGluZyB3aW5kb3cuIl0sCiAgWyJzYW5k"
    "Ym94IiwiVGVzdCBtb2RlIC8gc2FuZGJveCIsIlJ1biB0aGUgd2hvbGUgZmxvdyBvbiBmYWtlIG1v"
    "bmV5IGJlZm9yZSBnb2luZyBsaXZlLiJdLAogIFsiaTE4biIsIk11bHRpLWxhbmd1YWdlIiwiUGVy"
    "LXVzZXIgSGluZGkgLyBFbmdsaXNoIHN3aXRjaCBhY3Jvc3MgZXZlcnkgc3RyaW5nLiJdXV0sCiBb"
    "IlNlY3VyaXR5ICYgYW50aS1hYnVzZSIsWwogIFsicmF0ZUxpbWl0IiwiUmF0ZSBsaW1pdGluZyIs"
    "IlBlci11c2VyIGFjdGlvbiBsaW1pdHMgc28gbm9ib2R5IGNhbiBoYW1tZXIgdGhlIGJvdC4iXSwK"
    "ICBbIm11bHRpQWNjIiwiTXVsdGktYWNjb3VudCBkZXRlY3Rpb24iLCJDYXRjaCByZWZlcnJhbCBm"
    "YXJtaW5nIGFuZCBmbGFnIHN1c3BpY2lvdXMgc2lnbnVwcy4iXSwKICBbInZlbG9jaXR5IiwiUHVy"
    "Y2hhc2UgdmVsb2NpdHkgYWxlcnRzIiwiRmxhZyBhYm5vcm1hbGx5IGZhc3QgYnV5aW5nIOKAlCB0"
    "eXBpY2FsIG9mIHN0b2xlbiB0b3AtdXBzLiJdLAogIFsiYWRtaW4yZmEiLCJBZG1pbiAyRkEiLCJQ"
    "SU4gb3IgVE9UUCBiZWZvcmUgYmFsYW5jZSBlZGl0cywgYnJvYWRjYXN0cyBvciB3aXBlcy4iXSwK"
    "ICBbImF1dG9CYW4iLCJBdXRvLWJhbiBydWxlcyIsIkJhbiBvbiBjaGFyZ2ViYWNrLCBmYWtlIFVU"
    "UiBvciByZXBlYXRlZCBmYWlsZWQgdmVyaWZpY2F0aW9uLiJdLAogIFsiYmxhY2tsaXN0IiwiQmxh"
    "Y2tsaXN0IiwiUGVybWFuZW50bHkgYmxvY2sgdXNlciBJRHMsIHBob25lIG51bWJlcnMgYW5kIFVQ"
    "SSByZWZlcmVuY2VzLiJdLAogIFsiZW5jcnlwdCIsIlNlc3Npb24gZW5jcnlwdGlvbiBhdCByZXN0"
    "IiwiQSBsZWFrZWQgZGF0YWJhc2UgZmlsZSBpcyB1c2VsZXNzIHdpdGhvdXQgdGhlIGtleS4iXV1d"
    "LAogWyJBbmFseXRpY3MiLFsKICBbInJldkNoYXJ0IiwiUmV2ZW51ZSBkYXNoYm9hcmQiLCJEYWls"
    "eSwgd2Vla2x5IGFuZCBtb250aGx5IHJldmVudWUgYW5kIHByb2ZpdCBhcyBjaGFydHMuIl0sCiAg"
    "WyJmdW5uZWwiLCJDb252ZXJzaW9uIGZ1bm5lbCIsIlNlZSB3aGVyZSB1c2VycyBkcm9wIG9mZiBi"
    "ZXR3ZWVuIGpvaW5pbmcgYW5kIGJ1eWluZy4iXSwKICBbInByb2ZpdFJlcCIsIlBlci1jb3VudHJ5"
    "IHByb2ZpdCIsIkNvc3QgdnMgc2FsZSBwcmljZSB2cyB2b2x1bWUgZm9yIGV2ZXJ5IGNvdW50cnku"
    "Il0sCiAgWyJjbHYiLCJDdXN0b21lciBsaWZldGltZSB2YWx1ZSIsIlNwZW5kLCBmcmVxdWVuY3kg"
    "YW5kIGNodXJuIHJpc2sgcGVyIHVzZXIuIl0sCiAgWyJkaWdlc3QiLCJEYWlseSBkaWdlc3QiLCJF"
    "bmQtb2YtZGF5IHN1bW1hcnkgcHVzaGVkIHRvIHRoZSBhZG1pbiBjaGF0LiJdXV0sCiBbIlF1YWxp"
    "dHkgb2YgbGlmZSIsWwogIFsiaW5saW5lU2VhcmNoIiwiSW5saW5lIHNlYXJjaCIsIlR5cGUgQHlv"
    "dXJib3QgaW5kaWEgaW4gYW55IGNoYXQgdG8gZmluZCBhbmQgYnV5LiJdLAogIFsic2xhc2hDbWRz"
    "IiwiU2xhc2ggY29tbWFuZHMgbWVudSIsIi9zdGFydCAvYnV5IC9iYWxhbmNlIC9vcmRlcnMgcmVn"
    "aXN0ZXJlZCB3aXRoIFRlbGVncmFtLiJdLAogIFsic3RvY2tDb3VudGVyIiwiTGl2ZSBzdG9jayBj"
    "b3VudGVyIiwiQXV0by11cGRhdGluZyBwaW5uZWQgcG9zdCBhbmQgaW4tYXBwIGxpdmUgY291bnQu"
    "Il0sCiAgWyJvbmJvYXJkaW5nIiwiT25ib2FyZGluZyB0b3VyIiwiVGhyZWUtc3RlcCB3YWxrdGhy"
    "b3VnaCBmb3IgZmlyc3QtdGltZSB1c2Vycy4iXSwKICBbIm5vdGlmUHJlZnMiLCJOb3RpZmljYXRp"
    "b24gcHJlZmVyZW5jZXMiLCJVc2VycyBwaWNrIHdoaWNoIGFsZXJ0cyB0aGV5IHJlY2VpdmUuIl0s"
    "CiAgWyJwdXNoTm90aWYiLCJNaW5pIGFwcCBwdXNoIiwiTWluaSBhcHAgYWN0aW9ucyBub3RpZnkg"
    "dGhlIHVzZXIgdGhyb3VnaCB0aGUgYm90LiJdXV0KXTsKY29uc3QgRkVBVD17fTsKRkdST1VQUy5m"
    "b3JFYWNoKChbZyxyb3dzXSk9PnJvd3MuZm9yRWFjaChyPT5GRUFUW3JbMF1dPXRydWUpKTsKY29u"
    "c3QgRkVBVDA9T2JqZWN0LmFzc2lnbih7fSxGRUFUKTsKY29uc3QgRz0oayxoKT0+RkVBVFtrXT9o"
    "OiIiOwpjb25zdCBhbnlGPSguLi5rcyk9PmtzLnNvbWUoaz0+RkVBVFtrXSk7CmxldCBDQVJUPVtd"
    "LFdJU0g9W107CmxldCBSRVZJRVdTPXtJbmRpYTpbNC44LDQxMl0sSW5kb25lc2lhOls0LjYsMTgw"
    "XSxCcmF6aWw6WzQuNyw5Nl0sTmlnZXJpYTpbNC40LDU0XSxWaWV0bmFtOls0LjksMjMzXSxQaGls"
    "aXBwaW5lczpbNC41LDcxXX07CmxldCBGRUVEPVtbIvCfh67wn4ezIiwiSW5kaWEiLCLigrkzNSIs"
    "IjIgbWluIGFnbyJdLFsi8J+Hu/Cfh7MiLCJWaWV0bmFtIiwi4oK5MjYiLCI2IG1pbiBhZ28iXSxb"
    "IvCfh6fwn4e3IiwiQnJhemlsIiwi4oK5NDQiLCIxMSBtaW4gYWdvIl1dOwpsZXQgQURNPXsKICBr"
    "cGk6e3Jldjo0ODIwLG9yZGVyczo2MSx1c2VyczoxMjg0LGF1dG86Mzh9LAogIGJhcnM6WzQyLDYx"
    "LDM4LDc0LDU1LDg4LDY3XSwKICBzdG9jazpbXSxvcmRlcnM6W10sZGlzcHV0ZXM6W10sdXNlcnM6"
    "W10sYWRtaW5zOltdLGNoYW5uZWxzOltdLAogIGxvZ3M6e21hc3Rlcjp0cnVlLGNoYW5uZWxzOltd"
    "LGV2ZW50czpbXX0sY291cG9uczpbXSxhdWRpdDpbXSxyZXZpZXdzOltdLGRlcG9zaXRzOltdLGxv"
    "Z2ZlZWQ6W10sCiAgcGF5Ont1cGk6IiIscGF5ZWU6IiIsZ21haWw6IiIsZ21haWxfc2V0OmZhbHNl"
    "LHdpbmRvdzo1LHByZWZpeDoiUEFZIixtaW46MTAsbWF4OjIwMDAwLGF1dG9kZXA6MCxwZW5kaW5n"
    "OjB9LAogIHNlY3VyaXR5OntyYXRlOjIwLHZlbG9jaXR5OjUsYmxhY2tsaXN0OjAsYmFubmVkOjB9"
    "LAogIG1hcmtldDp7Zmxhc2g6MjAsZmxhc2hfb246ZmFsc2UscmVmMTo1LHJlZjI6MSxnb2FsOjAs"
    "bW9udGg6MH0sCiAgc2V0dGluZ3M6e3N0b3JlOiIiLHRhZ2xpbmU6IiIsc3VwcG9ydDoiIixwcmlj"
    "ZTozNSxkYWlseTowLjQsYXBwOiIiLG1haW50ZW5hbmNlOmZhbHNlLHNhbmRib3g6ZmFsc2V9LAog"
    "IGFuYWx5dGljczp7ZnVubmVsOnt1c2VyczowLGRlcDowLGJ1eTowfSxjb3VudHJ5OltdLHN0b2Nr"
    "X3RvdGFsOjAsc29sZF90b3RhbDowfQp9OwpmdW5jdGlvbiBhZG1OdW0obil7cmV0dXJuIChufHww"
    "KS50b0xvY2FsZVN0cmluZygiZW4tSU4iKTt9CmxldCBFQVJOPXtzdHJlYWs6NCxkYWlseTowLjQw"
    "LGNsYWltZWQ6ZmFsc2UscG9pbnRzOjM0MCxwb2ludHNXb3J0aDozNCxpbnZpdGVzOjEyLAogIGwx"
    "OjUyLjQwLGwyOjEyLjQwLGVhcm5lZDo2NC44MCxsaW5rOiJ0Lm1lL3lvdXJfYm90P3N0YXJ0PXJl"
    "Zl9kZW1vIiwKICByZWNlbnQ6W1sidXNlcl8xMDQxIiwiZGVwb3NpdGVkIOKCuTIwMCIsIivigrky"
    "LjAwIl0sWyJ1c2VyXzEwMzgiLCJqb2luZWQiLCLigJQiXSxbInVzZXJfMTAyMiIsImRlcG9zaXRl"
    "ZCDigrk1MDAiLCIr4oK5NS4wMCJdXSwKICBsZWFkZXJzOltbIjEiLCJAcmFodWwiLCLigrk0LDgy"
    "MCJdLFsiMiIsIkBhbWFuIiwi4oK5MywxNDAiXSxbIjMiLCJAeW91Iiwi4oK5MSw5ODAiXV0sCiAg"
    "dGFza3M6W1siSm9pbiB0aGUgbWFpbiBjaGFubmVsIiwi4oK5MS4wMCIsMV0sWyJQbGFjZSB5b3Vy"
    "IGZpcnN0IG9yZGVyIiwi4oK5Mi4wMCIsMV0sWyJJbnZpdGUgMyBmcmllbmRzIiwi4oK5NS4wMCIs"
    "MF1dfTsKZnVuY3Rpb24gc3RhcnMoYyl7Y29uc3Qgcj0oUkVWSUVXU1tjXXx8WzQuNywyMF0pWzBd"
    "LG49KFJFVklFV1NbY118fFs0LjcsMjBdKVsxXTsKICByZXR1cm4gYDxzcGFuIGNsYXNzPSJzdGFy"
    "cyI+JHtbMSwyLDMsNCw1XS5tYXAoaT0+aWMoaTw9TWF0aC5yb3VuZChyKT8ic3RhciI6InN0YXIi"
    "KSkuam9pbigiIil9PGI+JHtyfSAoJHtufSk8L2I+PC9zcGFuPmA7fQpmdW5jdGlvbiBhcHBseUZl"
    "YXR1cmVzKCl7CiAgY29uc3QgY2M9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNhcnRjaGlwIik7"
    "CiAgaWYoY2Mpe2NjLmNsYXNzTGlzdC50b2dnbGUoImhpZGRlbiIsIUZFQVQuY2FydCk7ZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoImNhcnRuIikudGV4dENvbnRlbnQ9Q0FSVC5sZW5ndGg7fQogIGNv"
    "bnN0IGVhcm49YW55Rigic3RyZWFrIiwicmVmRGFzaCIsInJlZkwyIiwibG95YWx0eSIsImxlYWRl"
    "cmJvYXJkIiwid2luYmFjayIpOwogIGNvbnN0IGViPWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoJyN0"
    "YWJzIGJ1dHRvbltkYXRhLXRhYj0iZWFybiJdJyk7CiAgaWYoZWIpe2ViLmNsYXNzTGlzdC50b2dn"
    "bGUoImhpZGRlbiIsIWVhcm4pOwogICAgaWYoIWVhcm4mJlRBQj09PSJlYXJuIil7VEFCPSJzaG9w"
    "Ijtkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjdGFicyBidXR0b24iKS5mb3JFYWNoKHg9Pngu"
    "Y2xhc3NMaXN0LnRvZ2dsZSgib24iLHguZGF0YXNldC50YWI9PT0ic2hvcCIpKTtyZW5kZXIoKTt9"
    "fQogIGNvbnN0IGFtYXA9e21hcmtldDphbnlGKCJjb3Vwb25zIiwiZmxhc2hTYWxlIiwic2NoZWRC"
    "cm9hZGNhc3QiLCJ3aW5iYWNrIiwic2FsZXNGZWVkIiwibGVhZGVyYm9hcmQiLCJsb3lhbHR5Iiwi"
    "cmVmRGFzaCIsInJlZkwyIiwiYWJhbmRvblJlbWluZCIpLAogICAgYW5hbHl0aWNzOmFueUYoInJl"
    "dkNoYXJ0IiwiZnVubmVsIiwicHJvZml0UmVwIiwiY2x2IiwiZGlnZXN0IiksCiAgICBzZWN1cml0"
    "eTphbnlGKCJyYXRlTGltaXQiLCJtdWx0aUFjYyIsInZlbG9jaXR5IiwiYWRtaW4yZmEiLCJhdXRv"
    "QmFuIiwiYmxhY2tsaXN0IiwiZW5jcnlwdCIpLAogICAgY29udGVudDpGRUFULmNvbnRlbnRFZGl0"
    "b3J9OwogIE9iamVjdC5lbnRyaWVzKGFtYXApLmZvckVhY2goKFtrLHZdKT0+e2NvbnN0IGI9ZG9j"
    "dW1lbnQucXVlcnlTZWxlY3RvcignI2FzdWIgYnV0dG9uW2RhdGEtYT0iJytrKyciXScpOwogICAg"
    "aWYoYil7Yi5jbGFzc0xpc3QudG9nZ2xlKCJoaWRkZW4iLCF2KTsKICAgICAgaWYoIXYmJkFUQUI9"
    "PT1rKXtBVEFCPSJkYXNoIjtkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjYXN1YiBidXR0b24i"
    "KS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnRvZ2dsZSgib24iLHguZGF0YXNldC5hPT09ImRhc2gi"
    "KSk7cmVuZGVyQWRtaW4oKTt9fX0pOwogIGlmKHdpbmRvdy5fX2RvY2tQbGFjZSlyZXF1ZXN0QW5p"
    "bWF0aW9uRnJhbWUoKCk9PndpbmRvdy5fX2RvY2tQbGFjZSh0cnVlKSk7CiAgaWYod2luZG93Ll9f"
    "dnRhYlBsYWNlKXJlcXVlc3RBbmltYXRpb25GcmFtZSgoKT0+d2luZG93Ll9fdnRhYlBsYWNlKHRy"
    "dWUpKTsKfQpmdW5jdGlvbiBhcHBseUNNUygpewogY29uc3QgZz1pZD0+ZG9jdW1lbnQuZ2V0RWxl"
    "bWVudEJ5SWQoaWQpOwogaWYoZygiYm5hbWUiKSlnKCJibmFtZSIpLnRleHRDb250ZW50PUNNUy5z"
    "dG9yZU5hbWU7CiBpZihnKCJhYm5hbWUiKSlnKCJhYm5hbWUiKS50ZXh0Q29udGVudD1DTVMuc3Rv"
    "cmVOYW1lOwogaWYoQ01TLnN0b3JlTmFtZSl7Q0ZHLm5hbWU9Q01TLnN0b3JlTmFtZTt9CiBpZihD"
    "TVMuc3RvcmVUYWcpQ0ZHLnRhZz1DTVMuc3RvcmVUYWc7CiBwYWludEJyYW5kKENNUy5zdG9yZU5h"
    "bWUsQ01TLnN0b3JlVGFnKTsKIHRyeXsKICAgaWYoQ01TLnN0b3JlTmFtZSlsb2NhbFN0b3JhZ2Uu"
    "c2V0SXRlbSgic3RvcmVOYW1lIixDTVMuc3RvcmVOYW1lKTsKICAgaWYoQ01TLnN0b3JlVGFnKWxv"
    "Y2FsU3RvcmFnZS5zZXRJdGVtKCJzdG9yZVRhZyIsQ01TLnN0b3JlVGFnKTsKIH1jYXRjaChlKXt9"
    "CiBpZihnKCJidGFnIikpZygiYnRhZyIpLnRleHRDb250ZW50PUNNUy5zdG9yZVRhZzsKIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLW5hdl0iKS5mb3JFYWNoKG49PnsKICAgY29uc3Qg"
    "az0ibmF2IituLmRhdGFzZXQubmF2WzBdLnRvVXBwZXJDYXNlKCkrbi5kYXRhc2V0Lm5hdi5zbGlj"
    "ZSgxKTsKICAgaWYoQ01TW2tdKW4udGV4dENvbnRlbnQ9Q01TW2tdO30pOwogaWYod2luZG93Ll9f"
    "ZG9ja1BsYWNlKXJlcXVlc3RBbmltYXRpb25GcmFtZSgoKT0+d2luZG93Ll9fZG9ja1BsYWNlKHRy"
    "dWUpKTsKfQpsZXQgQkFMPTI0OC41MDsKY29uc3QgU1RPQ0s9Wwoge2M6IkluZGlhIixmOiLwn4eu"
    "8J+HsyIsY29kZToiKzkxIixwcmljZTozNSx3YXM6NDkscXR5OjEyOCxob3Q6MX0sCiB7YzoiSW5k"
    "b25lc2lhIixmOiLwn4eu8J+HqSIsY29kZToiKzYyIixwcmljZToyOCx3YXM6MCxxdHk6NjR9LAog"
    "e2M6IkJyYXppbCIsZjoi8J+Hp/Cfh7ciLGNvZGU6Iis1NSIscHJpY2U6NDQsd2FzOjU5LHF0eToy"
    "Mn0sCiB7YzoiTmlnZXJpYSIsZjoi8J+Hs/Cfh6wiLGNvZGU6IisyMzQiLHByaWNlOjMxLHdhczow"
    "LHF0eTowfSwKIHtjOiJWaWV0bmFtIixmOiLwn4e78J+HsyIsY29kZToiKzg0IixwcmljZToyNix3"
    "YXM6MCxxdHk6OTF9LAoge2M6IlBoaWxpcHBpbmVzIixmOiLwn4e18J+HrSIsY29kZToiKzYzIixw"
    "cmljZTozOCx3YXM6MCxxdHk6N30KXTsKY29uc3QgT1JERVJTPVsKIHtpZDoiT1JELTc3NDEiLGM6"
    "IkluZGlhIixmOiLwn4eu8J+HsyIsbnVtOiIrOTEg4oCi4oCi4oCi4oCiIOKAouKAojMyIixzdDoi"
    "RGVsaXZlcmVkIix3aGVuOiIyIG1pbiBhZ28iLHByaWNlOjM1fSwKIHtpZDoiT1JELTc3MjgiLGM6"
    "IlZpZXRuYW0iLGY6IvCfh7vwn4ezIixudW06Iis4NCDigKLigKLigKLigKIg4oCi4oCiMDciLHN0"
    "OiJEZWxpdmVyZWQiLHdoZW46Illlc3RlcmRheSIscHJpY2U6MjZ9LAoge2lkOiJPUkQtNzcwMiIs"
    "YzoiQnJhemlsIixmOiLwn4en8J+HtyIsbnVtOiIrNTUg4oCi4oCi4oCi4oCiIOKAouKAojg4Iixz"
    "dDoiUmVmdW5kZWQiLHdoZW46IjMgZGF5cyBhZ28iLHByaWNlOjQ0fQpdOwpsZXQgQ0hBTk5FTFM9"
    "Wwoge2lkOjEsbmFtZToiTWFpbiBDaGFubmVsIixoYW5kbGU6IkB5b3VyX21haW5fY2hhbm5lbCIs"
    "cmVxOnRydWUsb246dHJ1ZSxtZW1iZXJzOiIxMi40SyJ9LAoge2lkOjIsbmFtZToiQmFja3VwIENo"
    "YW5uZWwiLGhhbmRsZToiQHlvdXJfYmFja3VwIixyZXE6dHJ1ZSxvbjp0cnVlLG1lbWJlcnM6IjMu"
    "MUsifSwKIHtpZDozLG5hbWU6IkRpc2N1c3Npb24gR3JvdXAiLGhhbmRsZToiQHlvdXJfZ3JvdXAi"
    "LHJlcTpmYWxzZSxvbjp0cnVlLG1lbWJlcnM6IjUuOEsifQpdOwpsZXQgTE9HQ0g9Wwoge2lkOjEs"
    "bmFtZToiTWFpbiBMb2dzIixoYW5kbGU6IkB5b3VyX2xvZ3MiLHByaW1hcnk6dHJ1ZSxvbjp0cnVl"
    "fSwKIHtpZDoyLG5hbWU6IlBheW1lbnQgTG9ncyIsaGFuZGxlOiJAeW91cl9wYXlfbG9ncyIscHJp"
    "bWFyeTpmYWxzZSxvbjp0cnVlfSwKIHtpZDozLG5hbWU6IkVycm9yIExvZ3MiLGhhbmRsZToiLTEw"
    "MHh4eHh4eHh4eHgiLHByaW1hcnk6ZmFsc2Usb246ZmFsc2V9Cl07CmxldCBMT0dTX01BU1RFUj10"
    "cnVlOwpsZXQgRVZFTlRTPVsKIHtrOiJOZXcgdXNlciBqb2luZWQiLG9uOmZhbHNlLGNoOjF9LHtr"
    "OiJEZXBvc2l0IC8gcGF5bWVudCIsb246dHJ1ZSxjaDoyfSwKIHtrOiJQdXJjaGFzZSIsb246dHJ1"
    "ZSxjaDoxfSx7azoiT1RQIHJlcXVlc3RlZCIsb246ZmFsc2UsY2g6MX0sCiB7azoiU3RvY2sgYWRk"
    "ZWQiLG9uOnRydWUsY2g6MX0se2s6IkFkbWluIGFjdGlvbiIsb246dHJ1ZSxjaDoxfSwKIHtrOiJF"
    "cnJvcnMgJiB3YXJuaW5ncyIsb246dHJ1ZSxjaDozfQpdOwpjb25zdCBUWD1be3Q6IkRlcG9zaXQi"
    "LGE6IivigrkyMDAuMDAiLHc6IlRvZGF5LCAxNDowMiJ9LHt0OiJQdXJjaGFzZSDCtyBJbmRpYSIs"
    "YToiLeKCuTM1LjAwIix3OiJUb2RheSwgMTQ6MDUifSwKIHt0OiJEYWlseSBjaGVjay1pbiIsYToi"
    "K+KCuTAuNDAiLHc6IlRvZGF5LCAwOToxMSJ9LHt0OiJSZWZlcnJhbCBib251cyIsYToiK+KCuTMu"
    "MjAiLHc6Illlc3RlcmRheSJ9XTsKCmxldCBGSUxURVJFRD1TVE9DSy5zbGljZSgpLCBRVUVSWT0i"
    "IiwgQ0hJUD0iYWxsIjsKY29uc3QgRU1QVFk9JzxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJ0ZXh0"
    "LWFsaWduOmNlbnRlciI+PHAgY2xhc3M9InN1YiI+Tm90aGluZyBtYXRjaGVzIHRoYXQgc2VhcmNo"
    "LjwvcD48L2Rpdj4nOwpjb25zdCBJQz17YmFnOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBm"
    "aWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBz"
    "dHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBk"
    "PVwiTTYgMiAzIDZ2MTRhMiAyIDAgMCAwIDIgMmgxNGEyIDIgMCAwIDAgMi0yVjZsLTMtNHpcIi8+"
    "PHBhdGggZD1cIk0zIDZoMThcIi8+PHBhdGggZD1cIk0xNiAxMGE0IDQgMCAwIDEtOCAwXCIvPjwv"
    "c3ZnPiIsYm94OiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9r"
    "ZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJv"
    "dW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwiTTIxIDhhMiAyIDAgMCAw"
    "LTEtMS43M2wtNy00YTIgMiAwIDAgMC0yIDBsLTcgNEEyIDIgMCAwIDAgMyA4djhhMiAyIDAgMCAw"
    "IDEgMS43M2w3IDRhMiAyIDAgMCAwIDIgMGw3LTRBMiAyIDAgMCAwIDIxIDE2WlwiLz48cGF0aCBk"
    "PVwibTMuMyA3IDguNyA1IDguNy01XCIvPjxwYXRoIGQ9XCJNMTIgMjJWMTJcIi8+PC9zdmc+Iix6"
    "YXA6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3Vy"
    "cmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBz"
    "dHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTMgMiAzIDE0aDlsLTEgOCAxMC0x"
    "MmgtOXpcIi8+PC9zdmc+Iix3YWxsZXQ6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9"
    "XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9r"
    "ZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJN"
    "MTkgN1Y1YTIgMiAwIDAgMC0yLTJINWEyIDIgMCAwIDAgMCA0aDE1YTIgMiAwIDAgMSAyIDJ2OWEy"
    "IDIgMCAwIDEtMiAySDVhMiAyIDAgMCAxLTItMlY1XCIvPjxwYXRoIGQ9XCJNMTggMTJoLjAxXCIv"
    "Pjwvc3ZnPiIsdXNlcjoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBz"
    "dHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9"
    "XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0xOSAyMXYtMmE0"
    "IDQgMCAwIDAtNC00SDlhNCA0IDAgMCAwLTQgNHYyXCIvPjxjaXJjbGUgY3g9XCIxMlwiIGN5PVwi"
    "N1wiIHI9XCI0XCIvPjwvc3ZnPiIsc2hpZWxkOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBm"
    "aWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBz"
    "dHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBk"
    "PVwiTTIwIDEzYzAgNS0zLjUgNy41LTcuNjYgOC45NWExIDEgMCAwIDEtLjY3LS4wMUM3LjUgMjAu"
    "NSA0IDE4IDQgMTNWNmExIDEgMCAwIDEgMS0xYzIgMCA0LjUtMS4yIDYuMjQtMi43MmExLjE3IDEu"
    "MTcgMCAwIDEgMS41MiAwQzE0LjUxIDMuODEgMTcgNSAxOSA1YTEgMSAwIDAgMSAxIDF6XCIvPjwv"
    "c3ZnPiIsY2hlY2s6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ry"
    "b2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMi40XCIgc3Ryb2tlLWxpbmVjYXA9"
    "XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0yMCA2IDkgMTds"
    "LTUtNVwiLz48L3N2Zz4iLHNlbmQ6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJu"
    "b25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1s"
    "aW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJtMjIg"
    "Mi03IDIwLTQtOS05LTRaXCIvPjxwYXRoIGQ9XCJNMjIgMiAxMSAxM1wiLz48L3N2Zz4iLHBsdXM6"
    "Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVu"
    "dENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMi40XCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiPjxw"
    "YXRoIGQ9XCJNMTIgNXYxNE01IDEyaDE0XCIvPjwvc3ZnPiIsY29weToiPHN2ZyB2aWV3Qm94PVwi"
    "MCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Ut"
    "d2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJv"
    "dW5kXCI+PHJlY3Qgd2lkdGg9XCIxNFwiIGhlaWdodD1cIjE0XCIgeD1cIjhcIiB5PVwiOFwiIHJ4"
    "PVwiMlwiLz48cGF0aCBkPVwiTTQgMTZjLTEuMSAwLTItLjktMi0yVjRjMC0xLjEuOS0yIDItMmgx"
    "MGMxLjEgMCAyIC45IDIgMlwiLz48L3N2Zz4iLGNoZXY6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAy"
    "NFwiIHdpZHRoPVwiMTZcIiBoZWlnaHQ9XCIxNlwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3Vy"
    "cmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBz"
    "dHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJtOSAxOCA2LTYtNi02XCIvPjwvc3Zn"
    "PiIsZmlyZToiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgd2lkdGg9XCIxMlwiIGhlaWdodD1c"
    "IjEyXCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9"
    "XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+"
    "PHBhdGggZD1cIk0xMiAyYzEgNCA1IDUgNSA5YTUgNSAwIDAgMS0xMCAwYzAtMiAxLTMgMi00IDAg"
    "MiAxIDMgMiAzIDAtMy0xLTUgMS04WlwiLz48L3N2Zz4iLGRvd246Ijxzdmcgdmlld0JveD1cIjAg"
    "MCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdp"
    "ZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3Vu"
    "ZFwiPjxwYXRoIGQ9XCJNMTIgNXYxNE0xOSAxMmwtNyA3LTctN1wiLz48L3N2Zz4iLHVwOiI8c3Zn"
    "IHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xv"
    "clwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxp"
    "bmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwiTTEyIDE5VjVNNSAxMmw3LTcgNyA3XCIvPjwvc3Zn"
    "PiIsc2VhcmNoOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9r"
    "ZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJv"
    "dW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48Y2lyY2xlIGN4PVwiMTFcIiBjeT1cIjEx"
    "XCIgcj1cIjdcIi8+PHBhdGggZD1cIm0yMSAyMS00LjMtNC4zXCIvPjwvc3ZnPiIsc3RhcjoiPHN2"
    "ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cImN1cnJlbnRDb2xvclwiIHN0cm9rZT1cImN1"
    "cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjEuNVwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5k"
    "XCI+PHBhdGggZD1cIm0xMiAzIDIuNiA1LjMgNS45LjktNC4zIDQuMSAxIDUuOC01LjItMi43LTUu"
    "MiAyLjcgMS01LjhMMy41IDkuMmw1LjktLjl6XCIvPjwvc3ZnPiJ9Owpjb25zdCBpYz1uPT5JQ1tu"
    "XXx8IiI7CmNvbnN0IGVzYz1zPT5TdHJpbmcocykucmVwbGFjZSgvWyY8PiJdL2csYz0+KHsiJiI6"
    "IiZhbXA7IiwiPCI6IiZsdDsiLCI+IjoiJmd0OyIsJyInOiImcXVvdDsifVtjXSkpOwpjb25zdCBt"
    "b25leT1uPT5DRkcuY3VyK051bWJlcihuKS50b0ZpeGVkKDIpOwoKbGV0IHR0O2NvbnN0IHRvYXN0"
    "PW09Pntjb25zdCBlPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJ0b2FzdCIpO2UudGV4dENvbnRl"
    "bnQ9bTtlLmNsYXNzTGlzdC5hZGQoInNob3ciKTtjbGVhclRpbWVvdXQodHQpO3R0PXNldFRpbWVv"
    "dXQoKCk9PmUuY2xhc3NMaXN0LnJlbW92ZSgic2hvdyIpLDE5MDApfTsKZG9jdW1lbnQuYWRkRXZl"
    "bnRMaXN0ZW5lcigiY2xpY2siLGU9Pntjb25zdCBiPWUudGFyZ2V0LmNsb3Nlc3QoIltkYXRhLXRv"
    "YXN0XSIpO2lmKGIpdG9hc3QoYi5kYXRhc2V0LnRvYXN0KX0pOwpjb25zdCBzaGVldD1oPT57CiAg"
    "ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInBhbmVsIikuaW5uZXJIVE1MPSc8ZGl2IGNsYXNzPSJn"
    "cmFiIj48L2Rpdj4nK2g7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNoZWV0IikuY2xhc3NM"
    "aXN0LmFkZCgib3BlbiIpOwogIC8qIGNvbnRyb2xzIGluc2lkZSBhIHNoZWV0IGFyZSBjcmVhdGVk"
    "IGFmdGVyIGJpbmRpbmcsIHNvIGJpbmQgdGhlbSBub3cgKi8KICB0cnl7IGlmKHR5cGVvZiBiaW5k"
    "Q29tbW9uPT09ImZ1bmN0aW9uIikgYmluZENvbW1vbigpOyB9Y2F0Y2goZSl7fQogIGRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3JBbGwoIiNzaGVldCBbZGF0YS1jbG9zZV0iKS5mb3JFYWNoKHg9Pngub25j"
    "bGljaz1jbG9zZVNoZWV0KTsKfTsKY29uc3QgY2xvc2VTaGVldD0oKT0+ZG9jdW1lbnQuZ2V0RWxl"
    "bWVudEJ5SWQoInNoZWV0IikuY2xhc3NMaXN0LnJlbW92ZSgib3BlbiIpOwpkb2N1bWVudC5hZGRF"
    "dmVudExpc3RlbmVyKCJjbGljayIsZT0+e2lmKGUudGFyZ2V0LmNsb3Nlc3QoIltkYXRhLWNsb3Nl"
    "XSIpKWNsb3NlU2hlZXQoKX0pOwoKZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3N3IGJ1dHRv"
    "biIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgaWYoYi5kYXRhc2V0LnN3PT09ImFkbWlu"
    "IiYmIWFkbWluQWxsb3dlZCgpKXt0b2FzdCgiQWRtaW5zIG9ubHkiKTtyZXR1cm47fQogIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNzdyBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0"
    "LnJlbW92ZSgib24iKSk7Yi5jbGFzc0xpc3QuYWRkKCJvbiIpOwogIFsiYXBwIiwiYWRtaW4iXS5m"
    "b3JFYWNoKHY9PmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJ2aWV3LSIrdikuY2xhc3NMaXN0LnRv"
    "Z2dsZSgiaGlkZGVuIix2IT09Yi5kYXRhc2V0LnN3KSk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5"
    "SWQoInRhYnMiKS5zdHlsZS5kaXNwbGF5PWIuZGF0YXNldC5zdz09PSJhcHAiPyIiOiJub25lIjt3"
    "aW5kb3cuc2Nyb2xsVG8oMCwwKTsKICByZXF1ZXN0QW5pbWF0aW9uRnJhbWUoKCk9PntpZih3aW5k"
    "b3cuX19kb2NrUGxhY2Upd2luZG93Ll9fZG9ja1BsYWNlKHRydWUpOwogICAgaWYod2luZG93Ll9f"
    "dnRhYlBsYWNlKXdpbmRvdy5fX3Z0YWJQbGFjZSh0cnVlKTt9KTt9KTsKCmxldCBUQUI9InNob3Ai"
    "Owpkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjdGFicyBidXR0b24iKS5mb3JFYWNoKGI9PmIu"
    "b25jbGljaz0oKT0+ewogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiN0YWJzIGJ1dHRvbiIp"
    "LmZvckVhY2goeD0+eC5jbGFzc0xpc3QucmVtb3ZlKCJvbiIpKTtiLmNsYXNzTGlzdC5hZGQoIm9u"
    "Iik7CiAgVEFCPWIuZGF0YXNldC50YWI7cmVuZGVyKCk7d2luZG93LnNjcm9sbFRvKDAsMCk7aWYo"
    "d2luZG93Ll9fZG9ja1BsYWNlKXJlcXVlc3RBbmltYXRpb25GcmFtZSh3aW5kb3cuX19kb2NrUGxh"
    "Y2UpO30pOwpkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCJjbGljayIsZT0+e2NvbnN0IGc9ZS50"
    "YXJnZXQuY2xvc2VzdCgiW2RhdGEtZ29dIik7aWYoIWcpcmV0dXJuOwogIGNvbnN0IHQ9Zy5kYXRh"
    "c2V0LmdvO2RvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiN0YWJzIGJ1dHRvbiIpLmZvckVhY2go"
    "eD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseC5kYXRhc2V0LnRhYj09PXQpKTsKICBUQUI9dDty"
    "ZW5kZXIoKTt3aW5kb3cuc2Nyb2xsVG8oMCwwKTt9KTsKCmZ1bmN0aW9uIHJlbmRlcigpe2NvbnN0"
    "IHN0PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdGFnZSIpOwogIGlmKExPQ0tFRCl7cGFpbnRM"
    "b2NrZWQoKTtyZXR1cm47fQogIHN0LmlubmVySFRNTD0nPGRpdiBjbGFzcz0ic2siPjwvZGl2Pjxk"
    "aXYgY2xhc3M9InNrIj48L2Rpdj48ZGl2IGNsYXNzPSJzayIgc3R5bGU9ImhlaWdodDoxNDBweCI+"
    "PC9kaXY+JzsKICBzZXRUaW1lb3V0KCgpPT57aWYoTE9DS0VEKXtwYWludExvY2tlZCgpO3JldHVy"
    "bjt9CiAgICBzdC5pbm5lckhUTUw9KHtzaG9wOnZTaG9wLG9yZGVyczp2T3JkZXJzLGVhcm46dkVh"
    "cm4sd2FsbGV0OnZXYWxsZXQseW91OnZZb3V9KVtUQUJdKCk7CiAgICBiaW5kU3RhZ2UoKTtyZXZl"
    "YWxTdGFnZShzdCk7YmluZFRpbHQoKTt9LFJNPzA6MTYwKTt9CgpmdW5jdGlvbiB2U2hvcCgpewog"
    "IGNvbnN0IGxpdmU9U1RPQ0suZmlsdGVyKHM9PnMucXR5PjApLmxlbmd0aCwgdW5pdHM9U1RPQ0su"
    "cmVkdWNlKChhLGIpPT5hK2IucXR5LDApOwogIGlmKExJVkUmJiFTVE9DSy5sZW5ndGgpewogICAg"
    "cmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIiBzdHlsZT0ibWFyZ2luOjE4cHggMDt0ZXh0LWFsaWdu"
    "OmNlbnRlcjtwYWRkaW5nOjI2cHgiPgogICAgICA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNnB4Ij4k"
    "e2VzYyhDTVMuZW1wdHlUaXRsZXx8IlJlc3RvY2tpbmciKX08L2gzPgogICAgICA8cCBjbGFzcz0i"
    "c3ViIiBzdHlsZT0ibWFyZ2luOjAiPiR7ZXNjKENNUy5lbXB0eUJvZHl8fCJFdmVyeXRoaW5nIGp1"
    "c3Qgc29sZCBvdXQuIEZyZXNoIGFjY291bnRzIGxhbmQgaGVyZSBzaG9ydGx5IFx1MjAxNCB0dXJu"
    "IG9uIGFuIGFsZXJ0IGFuZCB5b3Ugd2lsbCBoZWFyIGZpcnN0LiIpfTwvcD4KICAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4IiBkYXRhLWFjdD0iYWxl"
    "cnRzIj5Ob3RpZnkgbWU8L2J1dHRvbj48L2Rpdj5gOwogIH0KICByZXR1cm4gYAogIDxkaXYgY2xh"
    "c3M9ImJhbm5lciI+PGRpdiBjbGFzcz0idHJhY2siPgogICAgJHtbMSwyXS5tYXAoKCk9PmA8c3Bh"
    "bj4ke2ljKCJ6YXAiKX0ke0NNUy50aWNrMX08L3NwYW4+PHNwYW4+JHtpYygid2FsbGV0Iil9JHtD"
    "TVMudGljazJ9PC9zcGFuPgogICAgPHNwYW4+JHtpYygic2hpZWxkIil9JHtDTVMudGljazN9PC9z"
    "cGFuPjxzcGFuPiR7aWMoInN0YXIiKX0ke0NNUy50aWNrNH08L3NwYW4+YCkuam9pbigiIil9CiAg"
    "PC9kaXY+PC9kaXY+CiAgJHtHKCJvbmJvYXJkaW5nIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIg"
    "aWQ9InRvdXJjYXJkIiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6"
    "MTBweCI+CiAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJ6YXAiKX08L2Rpdj4KICAgIDxkaXYg"
    "c3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+TmV3"
    "IGhlcmU/IDMtc3RlcCB0b3VyPC9iPgogICAgICA8YnI+PHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIo"
    "LS10eDIpO2ZvbnQtc2l6ZTouNjlyZW0iPlRvcCB1cCwgcGljayBhIGNvdW50cnksIGdldCB0aGUg"
    "T1RQIGluLWFwcC48L3NtYWxsPjwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBpZD0i"
    "dG91cmdvIj5TdGFydDwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIGdob3N0IiBp"
    "ZD0idG91cnNraXAiPlNraXA8L2J1dHRvbj48L2Rpdj5gKX0KICA8c2VjdGlvbiBjbGFzcz0iaGVy"
    "byI+CiAgICA8ZGl2IGNsYXNzPSJraWNrIj4ke0NNUy5oZXJvS2lja308L2Rpdj4KICAgIDxoMT4k"
    "e0NNUy5oZXJvSDF9PC9oMT4KICAgIDxwPiR7Q01TLmhlcm9TdWIucmVwbGFjZSgie259IixsaXZl"
    "KX08L3A+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "Y3RhIiBkYXRhLXNjcm9sbD0ic2hvcGxpc3QiPiR7aWMoImJhZyIpfSAke0NNUy5oZXJvQnRuMX08"
    "L2J1dHRvbj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdsYXNzIiBkYXRhLWdvPSJ3YWxsZXQi"
    "PiR7Q01TLmhlcm9CdG4yfTwvYnV0dG9uPgogICAgPC9kaXY+CiAgPC9zZWN0aW9uPgogIDxzdmcg"
    "Y2xhc3M9InRyYWNlIiB2aWV3Qm94PSIwIDAgMzIwIDEwIiBwcmVzZXJ2ZUFzcGVjdFJhdGlvPSJu"
    "b25lIj48cGF0aCBjbGFzcz0iYmFzZSIgZD0iTTAsNSBMMzIwLDUiLz4KICAgIDxwYXRoIGNsYXNz"
    "PSJsZWFkIiBkPSJNMCw1IEwzMjAsNSIgc3Ryb2tlPSJ1cmwoI3RnMSkiLz4KICAgIDxkZWZzPjxs"
    "aW5lYXJHcmFkaWVudCBpZD0idGcxIiBncmFkaWVudFVuaXRzPSJ1c2VyU3BhY2VPblVzZSIgeDE9"
    "Ii0xNjAiIHkxPSIwIiB4Mj0iMCIgeTI9IjAiPgogICAgICA8c3RvcCBzdG9wLWNvbG9yPSJ2YXIo"
    "LS1hY2MpIiBzdG9wLW9wYWNpdHk9IjAiLz48c3RvcCBvZmZzZXQ9Ii41IiBzdG9wLWNvbG9yPSJ2"
    "YXIoLS1hY2MpIi8+CiAgICAgIDxzdG9wIG9mZnNldD0iMSIgc3RvcC1jb2xvcj0idmFyKC0tYWNj"
    "MikiIHN0b3Atb3BhY2l0eT0iMCIvPgogICAgICA8YW5pbWF0ZSBhdHRyaWJ1dGVOYW1lPSJ4MSIg"
    "dmFsdWVzPSItMTYwOzMyMCIgZHVyPSIyLjJzIiByZXBlYXRDb3VudD0iaW5kZWZpbml0ZSIvPgog"
    "ICAgICA8YW5pbWF0ZSBhdHRyaWJ1dGVOYW1lPSJ4MiIgdmFsdWVzPSIwOzQ4MCIgZHVyPSIyLjJz"
    "IiByZXBlYXRDb3VudD0iaW5kZWZpbml0ZSIvPgogICAgPC9saW5lYXJHcmFkaWVudD48L2RlZnM+"
    "PC9zdmc+CiAgPGRpdiBjbGFzcz0iZmVhdHMiPgogICAgPGRpdiBjbGFzcz0iZmVhdCI+JHtpYygi"
    "emFwIil9PGI+JHtDTVMuZmVhdDF9PC9iPjxzbWFsbD4ke0NNUy5mZWF0MXN9PC9zbWFsbD48L2Rp"
    "dj4KICAgIDxkaXYgY2xhc3M9ImZlYXQiPiR7aWMoInNoaWVsZCIpfTxiPiR7Q01TLmZlYXQyfTwv"
    "Yj48c21hbGw+JHtDTVMuZmVhdDJzfTwvc21hbGw+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmZWF0"
    "Ij4ke2ljKCJ3YWxsZXQiKX08Yj4ke0NNUy5mZWF0M308L2I+PHNtYWxsPiR7Q01TLmZlYXQzc308"
    "L3NtYWxsPjwvZGl2PgogIDwvZGl2PgogICR7Rygic3RvY2tDb3VudGVyIixgPGRpdiBjbGFzcz0i"
    "Y2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEw"
    "cHgiPgogICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5MaXZlPC9z"
    "cGFuPgogICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzZyZW07"
    "Y29sb3I6dmFyKC0tdHgyKSI+PGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHt1bml0c308L2I+"
    "IGFjY291bnRzIGluIHN0b2NrIGFjcm9zcyA8YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4ke2xp"
    "dmV9PC9iPiBjb3VudHJpZXM8L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBk"
    "YXRhLWFjdD0iY2hhbm5lbCI+Q2hhbm5lbDwvYnV0dG9uPjwvZGl2PmApfQogICR7RygiZmxhc2hT"
    "YWxlIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1p"
    "dGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgPHNwYW4gY2xhc3M9InBpbGwgaG90Ij4ke2ljKCJm"
    "aXJlIil9ICR7Q01TLmZsYXNoUGlsbH08L3NwYW4+CiAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWlu"
    "LXdpZHRoOjA7Zm9udC1zaXplOi43NnJlbTtjb2xvcjp2YXIoLS10eDIpIj4ke0NNUy5mbGFzaFRl"
    "eHR9IDxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiIGlkPSJjZCI+MTQ6NTI8L2I+PC9kaXY+CiAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4geHMiIGRhdGEtYWN0PSJmbGFzaCI+JHtDTVMuZmxhc2hCdG59"
    "PC9idXR0b24+PC9kaXY+YCl9CiAgPGRpdiBjbGFzcz0id2hlZWwiPjxkaXYgY2xhc3M9InJpbmci"
    "PiR7U1RPQ0subWFwKChzLGkpPT5gPGIgc3R5bGU9InRyYW5zZm9ybTpyb3RhdGUoJHtpKjYwfWRl"
    "ZykgdHJhbnNsYXRlKDcwcHgpIHJvdGF0ZSgtJHtpKjYwfWRlZykiPiR7cy5jb2RlfTwvYj5gKS5q"
    "b2luKCIiKX08L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImNhcCI+PGVtPiR7Q01TLndoZWVsS2lja308"
    "L2VtPjxiPiR7Q01TLndoZWVsVGl0bGV9PC9iPgogICAgICA8ZGl2IGNsYXNzPSJjbG91ZCIgc3R5"
    "bGU9Im1hcmdpbi10b3A6NnB4Ij48ZGl2IGNsYXNzPSJ0Ij4ke1sxLDJdLm1hcCgoKT0+Q01TLndo"
    "ZWVsV29yZHMuc3BsaXQoIiwiKS5tYXAodz0+YDxpPiR7dy50cmltKCl9PC9pPmApLmpvaW4oIiIp"
    "KS5qb2luKCIiKX08L2Rpdj48L2Rpdj48L2Rpdj48L2Rpdj4KICAke0coInNhbGVzRmVlZCIsYDxk"
    "aXYgY2xhc3M9ImNhcmQiPjxoMz5KdXN0IHNvbGQ8L2gzPjxwIGNsYXNzPSJzdWIiPkxpdmUgZmVl"
    "ZCDigJQgYWxzbyBhdXRvLXBvc3RlZCB0byB5b3VyIGNoYW5uZWwuPC9wPgogICAgJHtGRUVELmxl"
    "bmd0aD9GRUVELm1hcCgoW2YsYyxhLHddKT0+YDxkaXYgY2xhc3M9ImZlZWRsaW5lIj48c3Bhbj4k"
    "e2Z9PC9zcGFuPjxiPiR7Y308L2I+IHNvbGQgZm9yIDxiPiR7YX08L2I+PHNwYW4gc3R5bGU9Im1h"
    "cmdpbi1sZWZ0OmF1dG8iPiR7d308L3NwYW4+PC9kaXY+YCkuam9pbigiIikKICAgICAgOic8cCBj"
    "bGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIHNhbGVzIHlldCB0b2RheS48L3A+J308L2Rp"
    "dj5gKX0KICA8ZGl2IGNsYXNzPSJzZWMiIGlkPSJzaG9wbGlzdCI+PGgyPiR7Q01TLnNob3BIZWFk"
    "fTwvaDI+PGEgZGF0YS1hY3Q9InNvcnQiPiR7Q01TLnNob3BTb3J0fTwvYT48L2Rpdj4KICA8ZGl2"
    "IGNsYXNzPSJtc2VhcmNoIj48ZGl2IGNsYXNzPSJmaWVsZHdyYXAiPiR7aWMoInNlYXJjaCIpfTxp"
    "bnB1dCBpZD0icSIgcGxhY2Vob2xkZXI9IiR7Q01TLnNlYXJjaFBofSI+PC9kaXY+CiAgICA8YnV0"
    "dG9uIGNsYXNzPSJidG4gc20gc29mdCIgZGF0YS1hY3Q9ImFsZXJ0cyI+JHtDTVMuYWxlcnRzQnRu"
    "fTwvYnV0dG9uPjwvZGl2PgogIDxkaXYgY2xhc3M9ImNoaXByb3ciIGlkPSJjaGlwcyI+CiAgICA8"
    "YnV0dG9uIGNsYXNzPSJvbiIgZGF0YS1maWx0ZXI9ImFsbCI+QWxsPC9idXR0b24+PGJ1dHRvbiBk"
    "YXRhLWZpbHRlcj0ic3RvY2siPkluIHN0b2NrPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtZmls"
    "dGVyPSJkZWFsIj5PbiBkZWFsPC9idXR0b24+PGJ1dHRvbiBkYXRhLWZpbHRlcj0iY2hlYXAiPlVu"
    "ZGVyIOKCuTM1PC9idXR0b24+CiAgICAke0coInZhcmlhbnRzIixgPGJ1dHRvbiBkYXRhLWZpbHRl"
    "cj0idjJmYSI+Tm8gMkZBPC9idXR0b24+PGJ1dHRvbiBkYXRhLWZpbHRlcj0idm1haWwiPldpdGgg"
    "bWFpbDwvYnV0dG9uPjxidXR0b24gZGF0YS1maWx0ZXI9InZzcGFtIj5TcGFtLWZyZWU8L2J1dHRv"
    "bj5gKX08L2Rpdj4KICA8ZGl2IGlkPSJzaG9wYm9keSI+JHtzaG9wQm9keSgpfTwvZGl2PmA7Cn0K"
    "ZnVuY3Rpb24gc2hvcEJvZHkoKXtyZXR1cm4gYCR7RklMVEVSRUQubGVuZ3RoP2A8ZGl2IGNsYXNz"
    "PSJyb3dzIj4ke0ZJTFRFUkVELm1hcChzaG9wUm93KS5qb2luKCIiKX08L2Rpdj5gOkVNUFRZfWA7"
    "fQpmdW5jdGlvbiBwcmljZUJpdHMocyl7CiAgcmV0dXJuIGAke3Mud2FzP2A8cz4ke21vbmV5KHMu"
    "d2FzKX08L3M+YDoiIn0ke21vbmV5KHMucHJpY2UpfWA7Cn0KZnVuY3Rpb24gc2hvcFJvdyhzKXtj"
    "b25zdCBpPVNUT0NLLmluZGV4T2Yocyksb2ZmPXMucXR5PT09MDsKICByZXR1cm4gYDxkaXYgY2xh"
    "c3M9InJvdyIgZGF0YS1idXk9IiR7aX0iPjxkaXYgY2xhc3M9ImZsYWciPiR7cy5mfTwvZGl2Pgog"
    "ICAgPGRpdiBjbGFzcz0ibWlkIj48Yj4ke2VzYyhzLmMpfSAke3MuY29kZX08L2I+CiAgICAgIDxz"
    "bWFsbD4ke29mZj8iT3V0IG9mIHN0b2NrIOKAlCBnZXQgYW4gYWxlcnQiOnMucXR5KyIgaW4gc3Rv"
    "Y2sgwrcgaW5zdGFudCJ9PC9zbWFsbD4KICAgICAgJHtHKCJyZXZpZXdzIixgPGRpdiBzdHlsZT0i"
    "bWFyZ2luLXRvcDozcHgiPiR7c3RhcnMocy5jKX08L2Rpdj5gKX0KICAgICAgJHtHKCJ2YXJpYW50"
    "cyIsYDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6M3B4O2Rpc3BsYXk6ZmxleDtnYXA6NHB4O2ZsZXgt"
    "d3JhcDp3cmFwIj4KICAgICAgICA8c3BhbiBjbGFzcz0icGlsbCI+Tm8gMkZBPC9zcGFuPiR7cy5o"
    "b3Q/JzxzcGFuIGNsYXNzPSJwaWxsIj5XaXRoIG1haWw8L3NwYW4+JzoiIn08L2Rpdj5gKX08L2Rp"
    "dj4KICAgIDxkaXYgY2xhc3M9InJ0Ij48ZGl2IGNsYXNzPSJwIj4ke3ByaWNlQml0cyhzKX08L2Rp"
    "dj4KICAgICAgJHtHKCJkeW5QcmljZSIsYDxzbWFsbCBzdHlsZT0iZGlzcGxheTpibG9jaztmb250"
    "LXNpemU6LjZyZW07Y29sb3I6dmFyKC0tdHgyKSI+bGl2ZSBwcmljZTwvc21hbGw+YCl9CiAgICAg"
    "IDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6NXB4O2p1c3RpZnktY29udGVudDpmbGV4LWVu"
    "ZDttYXJnaW4tdG9wOjZweCI+CiAgICAgICAgJHtHKCJ3aXNobGlzdCIsYDxidXR0b24gY2xhc3M9"
    "ImJ0biB4cyBzb2Z0IiBkYXRhLXdpc2g9IiR7aX0iPiR7V0lTSC5pbmNsdWRlcyhpKT8i4pmlIjoi"
    "4pmhIn08L2J1dHRvbj5gKX0KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMiPiR7b2ZmPyJO"
    "b3RpZnkgTWUiOiJCdXkifTwvYnV0dG9uPjwvZGl2PjwvZGl2PjwvZGl2PmA7fQpmdW5jdGlvbiBz"
    "aG9wQ2FyZChzKXtjb25zdCBpPVNUT0NLLmluZGV4T2Yocyksb2ZmPXMucXR5PT09MDsKICByZXR1"
    "cm4gYDxkaXYgY2xhc3M9InBjYXJkIiBkYXRhLWJ1eT0iJHtpfSIgc3R5bGU9InBvc2l0aW9uOnJl"
    "bGF0aXZlIj4KICAgICR7Rygid2lzaGxpc3QiLGA8c3BhbiBjbGFzcz0iaGVhcnQgJHtXSVNILmlu"
    "Y2x1ZGVzKGkpPyJvbiI6IiJ9IiBkYXRhLXdpc2g9IiR7aX0iPiR7aWMoInN0YXIiKX08L3NwYW4+"
    "YCl9CiAgICA8ZGl2IGNsYXNzPSJ0b3AiPjxkaXYgY2xhc3M9ImZsYWciPiR7cy5mfTwvZGl2Pgog"
    "ICAgICA8c3BhbiBjbGFzcz0icGlsbCAke29mZj8ib2ZmIjoib24ifSI+JHtvZmY/IlNvbGQgb3V0"
    "IjpzLnF0eSsiIGxlZnQifTwvc3Bhbj48L2Rpdj4KICAgIDxkaXY+PGRpdiBjbGFzcz0ibm0iPiR7"
    "ZXNjKHMuYyl9PC9kaXY+PGRpdiBjbGFzcz0ibWV0YSI+JHtzLmNvZGV9IMK3IGluc3RhbnQ8L2Rp"
    "dj4KICAgICAgJHtHKCJyZXZpZXdzIixgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDo0cHgiPiR7c3Rh"
    "cnMocy5jKX08L2Rpdj5gKX08L2Rpdj4KICAgIDxkaXYgY2xhc3M9InByaWNlIj4ke3ByaWNlQml0"
    "cyhzKX08L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayBzbSI+JHtvZmY/Ik5vdGlm"
    "eSBNZSI6IkJ1eSBOb3cifTwvYnV0dG9uPjwvZGl2PmA7fQoKZnVuY3Rpb24gdk9yZGVycygpewog"
    "IHJldHVybiBgPGRpdiBjbGFzcz0ic2VjIj48aDI+WW91ciBPcmRlcnM8L2gyPjxhIGRhdGEtYWN0"
    "PSJjc3YiPkV4cG9ydDwvYT48L2Rpdj4KICAke09SREVSUy5tYXAoKG8sb2kpPT5gPGRpdiBjbGFz"
    "cz0iY2FyZCIgZGF0YS1vcmRlci1pZD0iJHtvLmlkfSI+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5"
    "OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMnB4Ij4KICAgICAgPGRpdiBjbGFzcz0iZmxh"
    "ZyI+JHtvLmZ9PC9kaXY+CiAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIg"
    "c3R5bGU9ImZvbnQtc2l6ZTouODVyZW07ZGlzcGxheTpibG9jayI+JHtvLm51bX08L2I+CiAgICAg"
    "ICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouN3JlbSI+JHtvLndo"
    "ZW59PC9zbWFsbD48L2Rpdj4KICAgICAgPGRpdiBzdHlsZT0idGV4dC1hbGlnbjpyaWdodCI+PHNw"
    "YW4gY2xhc3M9InBpbGwgJHtvLnN0PT09IkRlbGl2ZXJlZCI/Im9uIjoib2ZmIn0iPiR7by5zdH08"
    "L3NwYW4+PC9kaXY+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0ibWFy"
    "Z2luOjEwcHggMCAwO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpIj4KICAgICAgPGRpdiBjbGFzcz0i"
    "a3YiPjxzcGFuPlBob25lIG51bWJlcjwvc3Bhbj4KICAgICAgICA8Yj48Y29kZSBzdHlsZT0iZm9u"
    "dC1zaXplOi44cmVtO3VzZXItc2VsZWN0OmFsbCI+JHtlc2Moby5waG9uZXx8IuKAlCIpfTwvY29k"
    "ZT4KICAgICAgICAke28ucGhvbmU/YDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWNv"
    "cHk9InRleHQiIGRhdGEtY29weS12YWx1ZT0iJHtlc2Moby5waG9uZSl9Ij5Db3B5PC9idXR0b24+"
    "YDoiIn08L2I+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj4yRkEgcGFzc3dvcmQ8"
    "L3NwYW4+CiAgICAgICAgPGI+PGNvZGUgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbTt1c2VyLXNlbGVj"
    "dDphbGwiPiR7ZXNjKG8udHdvZmF8fCJub25lIil9PC9jb2RlPgogICAgICAgICR7by50d29mYT9g"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtY29weT0idGV4dCIgZGF0YS1jb3B5LXZh"
    "bHVlPSIke2VzYyhvLnR3b2ZhKX0iPkNvcHk8L2J1dHRvbj5gOiIifTwvYj48L2Rpdj4KICAgICAg"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxhc3QgbG9naW4gY29kZTwvc3Bhbj4KICAgICAgICA8Yj48"
    "Y29kZSBzdHlsZT0iZm9udC1zaXplOi44cmVtO3VzZXItc2VsZWN0OmFsbCI+JHtlc2Moby5vdHB8"
    "fCJub3QgeWV0Iil9PC9jb2RlPgogICAgICAgICR7by5vdHA/YDxidXR0b24gY2xhc3M9ImJ0biB4"
    "cyBzb2Z0IiBkYXRhLWNvcHk9InRleHQiIGRhdGEtY29weS12YWx1ZT0iJHtlc2Moby5vdHApfSI+"
    "Q29weTwvYnV0dG9uPmA6IiJ9PC9iPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+"
    "U3RhdHVzPC9zcGFuPjxiPiR7by5sb2dpbj09PSJpbiIKICAgICAgICA/ICc8c3BhbiBjbGFzcz0i"
    "cGlsbCBvbiI+TG9nZ2VkIGluIMK3IHlvdXJzIG9ubHk8L3NwYW4+JwogICAgICAgIDogKG8ud2lu"
    "ZG93PjAKICAgICAgICAgICAgPyAnPHNwYW4gY2xhc3M9InBpbGwiPkNvZGVzIGZvcndhcmRpbmcg"
    "wrcgJytvLndpbmRvdysnIG1pbiBsZWZ0PC9zcGFuPicKICAgICAgICAgICAgOiAnPHNwYW4gY2xh"
    "c3M9InBpbGwgb2ZmIj5IYW5kb3ZlciBjb21wbGV0ZTwvc3Bhbj4nKX08L2I+PC9kaXY+CiAgICA8"
    "L2Rpdj4KICAgICR7RygidGltZWxpbmUiLGA8ZGl2IGNsYXNzPSJ0bCI+CiAgICAgIDxkaXYgY2xh"
    "c3M9Im9rIj48Yj5QYWlkPC9iPjxzbWFsbD4ke28ud2hlbn08L3NtYWxsPjwvZGl2PgogICAgICA8"
    "ZGl2IGNsYXNzPSJvayI+PGI+RGVsaXZlcmVkPC9iPjxzbWFsbD5TZXNzaW9uIGhhbmRlZCBvdmVy"
    "IGluc3RhbnRseTwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9IiR7by5zdD09PSJEZWxp"
    "dmVyZWQiPyJvayI6IiJ9Ij48Yj5PVFAgcmVjZWl2ZWQ8L2I+PHNtYWxsPiR7by5zdD09PSJEZWxp"
    "dmVyZWQiPyJDb2RlIGZvcndhcmRlZCBpbi1hcHAiOiJXYWl0aW5nIn08L3NtYWxsPjwvZGl2Pgog"
    "ICAgICA8ZGl2PjxiPkNsb3NlZDwvYj48c21hbGw+QXV0by1jbG9zZXMgYWZ0ZXIgdGhlIHdhcnJh"
    "bnR5IHdpbmRvdzwvc21hbGw+PC9kaXY+PC9kaXY+YCl9CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ci"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjExcHgiPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20iIGRh"
    "dGEtb3RwPSIke28ubnVtfSI+R2V0IE9UUDwvYnV0dG9uPgogICAgICAke0coInJlZG93bmxvYWQi"
    "LGA8YnV0dG9uIGNsYXNzPSJidG4gc20gc29mdCIgZGF0YS1vcmQ9ImRldGFpbHMiPkRldGFpbHM8"
    "L2J1dHRvbj5gKX08L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10"
    "b3A6N3B4Ij4KICAgICAgJHtHKCJzZXNzaW9uRmlsZSIsYDxidXR0b24gY2xhc3M9ImJ0biBzbSBz"
    "b2Z0IiBkYXRhLW9yZD0iZmlsZSI+JHtpYygiY29weSIpfSBGaWxlPC9idXR0b24+YCl9CiAgICAg"
    "ICR7RygicmVjZWlwdHMiLGA8YnV0dG9uIGNsYXNzPSJidG4gc20gc29mdCIgZGF0YS1vcmQ9InJl"
    "Y2VpcHQiPlJlY2VpcHQ8L2J1dHRvbj5gKX0KICAgICAgJHsoby5sb2dpbj09PSJpbiIpPyIiOmA8"
    "YnV0dG9uIGNsYXNzPSJidG4gc20iIGRhdGEtbG9nZ2VkaW49IiR7by5pZH0iPkknbSBsb2dnZWQg"
    "aW48L2J1dHRvbj5gfQogICAgICAke0coIndhcnJhbnR5IixgPGJ1dHRvbiBjbGFzcz0iYnRuIHNt"
    "IGdob3N0IiBkYXRhLWNsYWltLW9yZGVyPSIke28uaWR9Ij5SZXBvcnQgaXNzdWU8L2J1dHRvbj5g"
    "KX08L2Rpdj4KICAgICR7RygicmV2aWV3cyIsYDxkaXYgY2xhc3M9Imt2IiBzdHlsZT0ibWFyZ2lu"
    "LXRvcDo5cHgiPjxzcGFuPlJhdGUgdGhpcyBvcmRlcjwvc3Bhbj4KICAgICAgPGI+PGJ1dHRvbiBj"
    "bGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtcmF0ZT0iJHtvLmN9Ij5SYXRlPC9idXR0b24+PC9iPjwv"
    "ZGl2PmApfQogIDwvZGl2PmApLmpvaW4oIiIpfQoKICA8ZGl2IGNsYXNzPSJjYXJkIiBzdHlsZT0i"
    "dGV4dC1hbGlnbjpjZW50ZXIiPjxoMz5OZWVkIGhlbHAgd2l0aCBhbiBvcmRlcj88L2gzPgogICAg"
    "PHAgY2xhc3M9InN1YiI+JHtGRUFULmF1dG9SZWZ1bmQ/IkZhaWxlZCBsb2dpbnMgYXJlIHJlZnVu"
    "ZGVkIGF1dG9tYXRpY2FsbHkgd2l0aGluIDEwIG1pbnV0ZXMuIjoiQ29udGFjdCBzdXBwb3J0IGFu"
    "ZCB3ZSB3aWxsIGxvb2sgaW50byBpdC4ifTwvcD4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTJweCI+PGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtYWN0PSJz"
    "dXBwb3J0Ij5TdXBwb3J0PC9idXR0b24+CiAgICAke0coImRpc3B1dGUiLGA8YnV0dG9uIGNsYXNz"
    "PSJidG4gZ2hvc3QiIGRhdGEtZGlzcHV0ZT5EaXNwdXRlPC9idXR0b24+YCl9PC9kaXY+PC9kaXY+"
    "YDsKfQoKZnVuY3Rpb24gdkVhcm4oKXsKICByZXR1cm4gYCR7Rygic3RyZWFrIixgPGRpdiBjbGFz"
    "cz0iY2FyZCI+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVy"
    "O2dhcDoxMnB4Ij4KICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygiemFwIil9PC9kaXY+PGRp"
    "diBzdHlsZT0iZmxleDoxIj48aDM+RGFpbHkgQ2hlY2staW48L2gzPjxwIGNsYXNzPSJzdWIiPkRh"
    "eSAke0VBUk4uc3RyZWFrfSBzdHJlYWsgwrcgJHttb25leShFQVJOLmRhaWx5KX0gd2FpdGluZzwv"
    "cD48L2Rpdj48L2Rpdj4KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6NnB4O21hcmdp"
    "bjoxNHB4IDAiPgogICAgICAke1sxLDIsMyw0LDUsNiw3XS5tYXAoZD0+YDxkaXYgc3R5bGU9ImZs"
    "ZXg6MTtoZWlnaHQ6MzRweDtib3JkZXItcmFkaXVzOjlweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRl"
    "bXM6Y2VudGVyO2ZvbnQtc2l6ZTouNjhyZW07Zm9udC13ZWlnaHQ6NzAwO2JvcmRlcjoycHggc29s"
    "aWQgdmFyKC0tbGluZSk7JHtkPD0oRUFSTi5zdHJlYWt8fDApPyJiYWNrZ3JvdW5kOnZhcigtLXBp"
    "bGxncmFkKTtjb2xvcjojZmZmZmZmO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVudCI6ImJhY2tncm91"
    "bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4MikifSI+JHtkfTwvZGl2PmApLmpvaW4oIiIp"
    "fTwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBibG9jayIgZGF0YS1jbGFpbSAke0VB"
    "Uk4uY2xhaW1lZD8iZGlzYWJsZWQiOiIifT4ke0VBUk4uY2xhaW1lZD8iQ2xhaW1lZCB0b2RheSI6"
    "IkNsYWltICIrbW9uZXkoRUFSTi5kYWlseSl9PC9idXR0b24+PC9kaXY+YCl9CiAgJHtHKCJsb3lh"
    "bHR5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxveWFsdHkgUG9pbnRzPC9oMz48cCBjbGFzcz0i"
    "c3ViIj4xIHBvaW50IHBlciDigrkxMCBzcGVudC4gMTAwIHBvaW50cyA9IOKCuTEwIGJhbGFuY2Uu"
    "PC9wPgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkJhbGFuY2U8L3NwYW4+PGI+JHtFQVJOLnBv"
    "aW50c30gcHRzPC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPldvcnRoPC9zcGFu"
    "PjxiPiR7bW9uZXkoRUFSTi5wb2ludHNXb3J0aCl9PC9iPjwvZGl2PgogICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIGJsb2NrIiBpZD0icmVkZWVtZ28iICR7RUFSTi5wb2ludHM+MD8iIjoiZGlzYWJsZWQi"
    "fT5SZWRlZW0gUG9pbnRzPC9idXR0b24+PC9kaXY+YCl9CiAgJHthbnlGKCJyZWZEYXNoIiwicmVm"
    "TDIiKT9gPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkludml0ZSAmYW1wOyBFYXJuPC9oMz4KICAgIDxw"
    "IGNsYXNzPSJzdWIiPiR7RkVBVC5yZWZMMj8iNSUgZnJvbSBwZW9wbGUgeW91IGludml0ZSwgcGx1"
    "cyAxJSBmcm9tIHRoZSBwZW9wbGUgdGhleSBpbnZpdGUuIjoiMSUgb2YgZXZlcnkgZGVwb3NpdCB5"
    "b3VyIGludml0ZXMgZXZlciBtYWtlLiJ9PC9wPgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPklu"
    "dml0ZXM8L3NwYW4+PGI+JHtFQVJOLmludml0ZXN9PC9iPjwvZGl2PgogICAgJHtHKCJyZWZMMiIs"
    "YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5MZXZlbCAxIGVhcm5pbmdzPC9zcGFuPjxiPiR7bW9uZXko"
    "RUFSTi5sMSl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxldmVsIDIgZWFy"
    "bmluZ3M8L3NwYW4+PGI+JHttb25leShFQVJOLmwyKX08L2I+PC9kaXY+YCl9CiAgICA8ZGl2IGNs"
    "YXNzPSJrdiI+PHNwYW4+VG90YWwgZWFybmVkPC9zcGFuPjxiPiR7bW9uZXkoRUFSTi5lYXJuZWQp"
    "fTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Zb3VyIGxpbms8L3NwYW4+PGIg"
    "c3R5bGU9ImZvbnQtc2l6ZTouNzJyZW0iPiR7ZXNjKEVBUk4ubGluayl9PC9iPjwvZGl2PgogICAg"
    "PGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48YnV0dG9uIGNsYXNz"
    "PSJidG4iIGRhdGEtY29weT0icmVmIj4ke2ljKCJjb3B5Iil9IENvcHkgTGluazwvYnV0dG9uPgog"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtY29weT0icmVmIj5TaGFyZTwvYnV0dG9u"
    "PjwvZGl2PgogICAgJHtHKCJyZWZEYXNoIixgPGRpdiBjbGFzcz0ic2VjIiBzdHlsZT0ibWFyZ2lu"
    "LXRvcDo2cHgiPjxoMiBzdHlsZT0iZm9udC1zaXplOi43OHJlbSI+UmVjZW50IGludml0ZXM8L2gy"
    "PjwvZGl2PgogICAgJHtFQVJOLnJlY2VudC5sZW5ndGg/RUFSTi5yZWNlbnQubWFwKChbdSxhLGVd"
    "KT0+YDxkaXYgY2xhc3M9ImZlZWRsaW5lIj48Yj4ke2VzYyh1KX08L2I+ICR7ZXNjKGEpfTxzcGFu"
    "IHN0eWxlPSJtYXJnaW4tbGVmdDphdXRvO2NvbG9yOnZhcigtLW9rKSI+JHtlc2MoZSl9PC9zcGFu"
    "PjwvZGl2PmApLmpvaW4oIiIpCiAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjow"
    "Ij5Ob2JvZHkgaGFzIGpvaW5lZCB0aHJvdWdoIHlvdXIgbGluayB5ZXQuPC9wPid9CiAgICA8cCBj"
    "bGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjExcHggMCAwIj5SZWZlcnJhbCBlYXJuaW5ncyBsYW5k"
    "IGluIHlvdXIgYmFsYW5jZSBhdXRvbWF0aWNhbGx5IOKAlCBub3RoaW5nIHRvIGNsYWltLjwvcD5g"
    "KX0KICA8L2Rpdj5gOiIifQogICR7RygibGVhZGVyYm9hcmQiLGA8ZGl2IGNsYXNzPSJjYXJkIj48"
    "aDM+VGhpcyB3ZWVrJ3MgdG9wIGJ1eWVyczwvaDM+PHAgY2xhc3M9InN1YiI+TnVtYmVyIG9uZSB3"
    "aW5zIOKCuTUwMCBvbiBTdW5kYXkuPC9wPgogICAgJHtFQVJOLmxlYWRlcnMubGVuZ3RoP0VBUk4u"
    "bGVhZGVycy5tYXAoKFtyLHUsdl0saSk9PmA8ZGl2IGNsYXNzPSJsYiI+PHNwYW4gY2xhc3M9InJr"
    "ICR7aT09PTA/ImciOiIifSI+JHtlc2Mocil9PC9zcGFuPgogICAgICAgIDxkaXYgc3R5bGU9ImZs"
    "ZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+JHtlc2ModSl9PC9i"
    "PjwvZGl2PjxiIHN0eWxlPSJmb250LXNpemU6LjhyZW0iPiR7ZXNjKHYpfTwvYj48L2Rpdj5gKS5q"
    "b2luKCIiKQogICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gcHVyY2hh"
    "c2VzIHlldCB0aGlzIHdlZWsuPC9wPid9PC9kaXY+YCl9CiAgJHtHKCJ3aW5iYWNrIixgPGRpdiBj"
    "bGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7"
    "Z2FwOjEwcHgiPgogICAgPHNwYW4gY2xhc3M9InBpbGwgaG90Ij4ke2ljKCJmaXJlIil9IENvbWVi"
    "YWNrPC9zcGFuPgogICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTou"
    "NzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+MTAlIG9mZiDigJQgZXhwaXJlcyBpbiA0OCBob3Vyczwv"
    "ZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBpZD0ib2ZmZXJ1c2UiPlVzZTwvYnV0dG9u"
    "PjwvZGl2PmApfQogIDxkaXYgY2xhc3M9InNlYyI+PGgyPlRhc2tzPC9oMj48L2Rpdj4KICAke0VB"
    "Uk4udGFza3MubWFwKChbdCxyLGRvbmVdKT0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxl"
    "PSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMXB4Ij4KICAgICAgPGRpdiBj"
    "bGFzcz0iZmxhZyI+JHtkb25lP2ljKCJjaGVjayIpOmljKCJ6YXAiKX08L2Rpdj4KICAgICAgPGRp"
    "diBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44MnJlbSI+"
    "JHt0fTwvYj48YnI+PHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouN3Jl"
    "bSI+UmV3YXJkICR7cn08L3NtYWxsPjwvZGl2PgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMg"
    "JHtkb25lPyJzb2Z0IjoiIn0iICR7ZG9uZT8iZGlzYWJsZWQiOiIifSBkYXRhLWFjdD0idGFzayI+"
    "JHtkb25lPyJEb25lIjoiU3RhcnQifTwvYnV0dG9uPjwvZGl2PmApLmpvaW4oIiIpfWA7Cn0KCmZ1"
    "bmN0aW9uIHZXYWxsZXQoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9Imhlcm8iIHN0eWxlPSJ0ZXh0"
    "LWFsaWduOmNlbnRlciI+CiAgICA8ZGl2IGNsYXNzPSJraWNrIj4ke0NNUy53YWxLaWNrfTwvZGl2"
    "PgogICAgPGgxIHN0eWxlPSJmb250LXNpemU6Mi4zcmVtIj7igrk8c3BhbiBpZD0iYmFsMiIgY2xh"
    "c3M9InRpY2siPiR7QkFMLnRvRml4ZWQoMil9PC9zcGFuPjwvaDE+CiAgICA8cCBzdHlsZT0ibWFy"
    "Z2luOjAgYXV0byAxNHB4Ij4ke0NNUy53YWxTdWJ9PC9wPgogICAgPGRpdiBzdHlsZT0ibWFyZ2lu"
    "OjAgYXV0bzttYXgtd2lkdGg6MzAwcHgiPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJs"
    "b2NrIiBkYXRhLWRlcD4ke2ljKCJwbHVzIil9ICR7Q01TLndhbEFkZH08L2J1dHRvbj4KICAgICAg"
    "JHtHKCJjcnlwdG8iLGA8YnV0dG9uIGNsYXNzPSJidG4gZ2xhc3MgYmxvY2siIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjhweCIgZGF0YS1jcnlwdG8+UGF5IHdpdGggQ3J5cHRvPC9idXR0b24+YCl9PC9kaXY+"
    "PC9kaXY+CiAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGln"
    "bi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNs"
    "YXNzPSJkb3QiPjwvaT5BdXRvLXZlcmlmeSBPTjwvc3Bhbj4KICAgIDxkaXYgc3R5bGU9ImZsZXg6"
    "MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlRvcC11cHMg"
    "YXJlIGNyZWRpdGVkIGFzIHNvb24gYXMgdGhleSBhcnJpdmU8L2Rpdj4KICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWFjdD0ic3RhdHVzIj5TdGF0dXM8L2J1dHRvbj48L2Rpdj4K"
    "ICAke0coImRlcEJvbnVzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlRvcC11cCBib251czwvaDM+"
    "PHAgY2xhc3M9InN1YiI+RXh0cmEgY3JlZGl0IGFkZGVkIGF1dG9tYXRpY2FsbHkuPC9wPgogICAg"
    "PGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo3cHg7bWFyZ2luLXRvcDoxMHB4Ij4KICAgICAg"
    "JHtbWyLigrk1MDAiLCIrNSUiXSxbIuKCuTEwMDAiLCIrOCUiXSxbIuKCuTIwMDAiLCIrMTIlIl1d"
    "Lm1hcCgoW2EsYl0pPT5gPGRpdiBzdHlsZT0iZmxleDoxO3RleHQtYWxpZ246Y2VudGVyO3BhZGRp"
    "bmc6OXB4IDRweDtib3JkZXItcmFkaXVzOjEycHg7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1saW5l"
    "KTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKSI+CiAgICAgICAgPGIgc3R5bGU9ImZvbnQtc2l6ZTou"
    "OHJlbTtkaXNwbGF5OmJsb2NrIj4ke2F9PC9iPjxzbWFsbCBzdHlsZT0iZm9udC1zaXplOi42OHJl"
    "bTtjb2xvcjp2YXIoLS1vaykiPiR7Yn08L3NtYWxsPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2Pjwv"
    "ZGl2PmApfQogIDxkaXYgY2xhc3M9InNlYyI+PGgyPlF1aWNrIFRvcC11cDwvaDI+PC9kaXY+CiAg"
    "PGRpdiBjbGFzcz0iZ3JpZCIgc3R5bGU9ImdyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoMywx"
    "ZnIpIj4KICAgICR7WzUwLDEwMCwyMDAsNTAwLDEwMDAsMjAwMF0ubWFwKGE9PmA8YnV0dG9uIGNs"
    "YXNzPSJidG4gc29mdCIgZGF0YS1hbXQ9IiR7YX0iPuKCuSR7YX08L2J1dHRvbj5gKS5qb2luKCIi"
    "KX08L2Rpdj4KICAke0coImRlcExpbWl0cyIsYDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46"
    "OXB4IDAgMDtmb250LXNpemU6LjdyZW0iPk1pbmltdW0g4oK5MTAgwrcgbWF4aW11bSDigrkyMCww"
    "MDAgcGVyIHRvcC11cC48L3A+YCl9CiAgPGRpdiBjbGFzcz0iY2FyZCIgc3R5bGU9Im1hcmdpbi10"
    "b3A6MTJweCI+PGgzPiR7Q01TLnBheVRpdGxlfTwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj4ke0NN"
    "Uy5wYXlTdWJ9PC9wPgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlVQSSBJRDwvc3Bhbj48Yj4k"
    "e2VzYyhTVE9SRS51cGl8fCJub3Qgc2V0IHlldCIpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9"
    "Imt2Ij48c3Bhbj5QYXllZTwvc3Bhbj48Yj4ke2VzYyhTVE9SRS5wYXllZXx8IuKAlCIpfTwvYj48"
    "L2Rpdj4KICAgICR7RygidXBpUm90YXRlIixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkhhbmRsZXMg"
    "aW4gcm90YXRpb248L3NwYW4+PGI+MyBhY3RpdmU8L2I+PC9kaXY+YCl9CiAgICA8ZGl2IGNsYXNz"
    "PSJrdiI+PHNwYW4+UGF5ZWU8L3NwYW4+PGI+WW91ciBTdG9yZSBOYW1lPC9iPjwvZGl2PgogICAg"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPkF1dG8tdmVyaWZ5PC9zcGFuPjxiIHN0eWxlPSJjb2xvcjp2"
    "YXIoLS1vaykiPkVuYWJsZWQ8L2I+PC9kaXY+CiAgICAke0coIndyb25nQW10IixgPGRpdiBjbGFz"
    "cz0ia3YiPjxzcGFuPldyb25nIGFtb3VudDwvc3Bhbj48Yj5DcmVkaXRlZCBhcyBwYWlkPC9iPjwv"
    "ZGl2PmApfQogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIHNvZnQiIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjEycHgiIGRhdGEtY29weT0idXBpIj4ke2ljKCJjb3B5Iil9IENvcHkgVVBJIElEPC9idXR0"
    "b24+PC9kaXY+CiAgJHtHKCJhYmFuZG9uUmVtaW5kIixgPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1h"
    "cmdpbjoxMHB4IDAgMDtmb250LXNpemU6LjdyZW0iPldlIHdpbGwgcmVtaW5kIHlvdSBiZWZvcmUg"
    "YW4gdW5maW5pc2hlZCBwYXltZW50IGV4cGlyZXMuPC9wPmApfQogIDxkaXYgY2xhc3M9InNlYyI+"
    "PGgyPiR7RkVBVC5sZWRnZXI/IlN0YXRlbWVudCI6Ikhpc3RvcnkifTwvaDI+JHtHKCJsZWRnZXIi"
    "LGA8YSBkYXRhLWFkbWluLWFjdD0iZXhwb3J0Ij5FeHBvcnQ8L2E+YCl9PC9kaXY+CiAgJHtUWC5t"
    "YXAoKHQsaSk9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2Fs"
    "aWduLWl0ZW1zOmNlbnRlcjtnYXA6MTFweCI+CiAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke3QuYVsw"
    "XT09PSIrIj9pYygiZG93biIpOmljKCJ1cCIpfTwvZGl2PgogICAgPGRpdiBzdHlsZT0iZmxleDox"
    "O21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44MnJlbSI+JHt0LnR9PC9iPjxicj48"
    "c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi43cmVtIj4ke3Qud308L3Nt"
    "YWxsPjwvZGl2PgogICAgPGRpdiBzdHlsZT0idGV4dC1hbGlnbjpyaWdodCI+PGIgc3R5bGU9ImZv"
    "bnQtc2l6ZTouODVyZW07Y29sb3I6JHt0LmFbMF09PT0iKyI/InZhcigtLW9rKSI6InZhcigtLXR4"
    "KSJ9Ij4ke3QuYX08L2I+CiAgICAke0coImxlZGdlciIsYDxicj48c21hbGwgc3R5bGU9ImZvbnQt"
    "c2l6ZTouNjVyZW07Y29sb3I6dmFyKC0tdHgyKSI+YmFsIOKCuSR7KDI0OC41K2kqNykudG9GaXhl"
    "ZCgyKX08L3NtYWxsPmApfTwvZGl2PjwvZGl2PmApLmpvaW4oIiIpfWA7Cn0KCmZ1bmN0aW9uIHZZ"
    "b3UoKXsKICBjb25zdCBtZT1NRXx8e307CiAgY29uc3Qgc3BlbnQ9bWUuc3BlbnR8fDAsIHRhcmdl"
    "dD1zcGVudD49NTAwPzIwMDA6NTAwOwogIGNvbnN0IHBjdD1NYXRoLm1heCg0LE1hdGgubWluKDEw"
    "MCxNYXRoLnJvdW5kKHNwZW50L3RhcmdldCoxMDApKSk7CiAgY29uc3QgYXY9bWUucGhvdG8/YDxp"
    "bWcgc3JjPSIke21lLnBob3RvfSIgYWx0PSIiIHN0eWxlPSJ3aWR0aDo2NHB4O2hlaWdodDo2NHB4"
    "O2JvcmRlci1yYWRpdXM6NTAlO29iamVjdC1maXQ6Y292ZXI7ZGlzcGxheTpibG9jazttYXJnaW46"
    "MCBhdXRvIDEwcHgiIG9uZXJyb3I9InRoaXMucmVwbGFjZVdpdGgoT2JqZWN0LmFzc2lnbihkb2N1"
    "bWVudC5jcmVhdGVFbGVtZW50KCdkaXYnKSx7Y2xhc3NOYW1lOidmbGFnJyxzdHlsZTond2lkdGg6"
    "NjRweDtoZWlnaHQ6NjRweDttYXJnaW46MCBhdXRvIDEwcHgnfSkpIj5gCiAgICA6YDxkaXYgY2xh"
    "c3M9ImZsYWciIHN0eWxlPSJ3aWR0aDo2NHB4O2hlaWdodDo2NHB4O21hcmdpbjowIGF1dG8gMTBw"
    "eCI+JHtpYygidXNlciIpfTwvZGl2PmA7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIHNoaW5l"
    "Y2FyZCB0aWx0IiBkYXRhLXRpbHQgc3R5bGU9InRleHQtYWxpZ246Y2VudGVyIj48c3BhbiBjbGFz"
    "cz0iZ2xhcmUiPjwvc3Bhbj4KICAgICR7YXZ9CiAgICA8aDMgc3R5bGU9ImZvbnQtc2l6ZToxLjA1"
    "cmVtIj4ke2VzYyhtZS5uYW1lfHwiWW91Iil9PC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPiR7ZXNj"
    "KG1lLnVzZXJuYW1lfHwoIklEICIrKG1lLmlkfHwi4oCUIikpKX0ke21lLnVzZXJuYW1lPyIgwrcg"
    "SUQgIisobWUuaWR8fCLigJQiKToiIn0ke21lLmpvaW5lZD8iIMK3IGpvaW5lZCAiK2VzYyhtZS5q"
    "b2luZWQpOiIifTwvcD4KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4O2p1c3Rp"
    "ZnktY29udGVudDpjZW50ZXI7bWFyZ2luLXRvcDoxMHB4O2ZsZXgtd3JhcDp3cmFwIj4KICAgICAg"
    "PHNwYW4gY2xhc3M9InBpbGwgb24iPiR7ZXNjKG1lLnRpZXJ8fCJCcm9uemUiKX08L3NwYW4+PHNw"
    "YW4gY2xhc3M9InBpbGwiPiR7bWUub3JkZXJzfHwwfSBvcmRlcnM8L3NwYW4+CiAgICAgIDxzcGFu"
    "IGNsYXNzPSJwaWxsIj4ke21vbmV5KG1lLmJhbGFuY2V8fDApfTwvc3Bhbj4KICAgICAgJHtHKCJs"
    "b3lhbHR5IixgPHNwYW4gY2xhc3M9InBpbGwiPiR7bWUucG9pbnRzfHwwfSBwdHM8L3NwYW4+YCl9"
    "CiAgICAgICR7Rygic3RyZWFrIixgPHNwYW4gY2xhc3M9InBpbGwiPiR7bWUuc3RyZWFrfHwwfS1k"
    "YXkgc3RyZWFrPC9zcGFuPmApfQogICAgICAkeyhGRUFULmJ1eWVyQmFkZ2UmJihtZS5vcmRlcnN8"
    "fDApPj01KT9gPHNwYW4gY2xhc3M9InBpbGwgb24iPiR7aWMoImNoZWNrIil9IFZlcmlmaWVkIGJ1"
    "eWVyPC9zcGFuPmA6IiJ9CiAgICAgICR7KG1lLmRlYnQ+MCk/YDxzcGFuIGNsYXNzPSJwaWxsIGhv"
    "dCI+JHttb25leShtZS5kZWJ0KX0gdWRoYXI8L3NwYW4+YDoiIn08L2Rpdj4KICAgIDxkaXYgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTRweDtoZWlnaHQ6N3B4O2JvcmRlci1yYWRpdXM6OXB4O2JhY2tncm91"
    "bmQ6dmFyKC0tY2FyZDIpO292ZXJmbG93OmhpZGRlbiI+CiAgICAgIDxpIHN0eWxlPSJkaXNwbGF5"
    "OmJsb2NrO2hlaWdodDoxMDAlO3dpZHRoOiR7cGN0fSU7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3Jh"
    "ZCkiPjwvaT48L2Rpdj4KICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW4tdG9wOjdweDtm"
    "b250LXNpemU6LjcycmVtIj4ke21lLm5leHRfdGllcj4wCiAgICAgID8gbW9uZXkobWUubmV4dF90"
    "aWVyKSsiIG1vcmUgc3BlbnQgdG8gcmVhY2ggIisoc3BlbnQ+PTUwMD8iUGxhdGludW0iOiJHb2xk"
    "IikKICAgICAgOiAiVG9wIHRpZXIgcmVhY2hlZCJ9PC9wPjwvZGl2PgogICR7Rygid2lzaGxpc3Qi"
    "LGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5TYXZlZCBjb3VudHJpZXM8L2gyPjwvZGl2PgogICAgPGRp"
    "diBjbGFzcz0iY2FyZCI+JHtXSVNILmxlbmd0aD9XSVNILm1hcChpPT5gPGRpdiBjbGFzcz0ia3Yi"
    "PjxzcGFuPiR7U1RPQ0tbaV0uZn0gJHtlc2MoU1RPQ0tbaV0uYyl9ICR7U1RPQ0tbaV0uY29kZX08"
    "L3NwYW4+CiAgICAgIDxiPjxidXR0b24gY2xhc3M9ImJ0biB4cyIgZGF0YS1idXk9IiR7aX0iPkJ1"
    "eTwvYnV0dG9uPjwvYj48L2Rpdj5gKS5qb2luKCIiKTonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1h"
    "cmdpbjowIj5UYXAgdGhlIGhlYXJ0IG9uIGFueSBjb3VudHJ5IHRvIHNhdmUgaXQgaGVyZS48L3A+"
    "J308L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5BY2NvdW50PC9oMj48L2Rpdj4KICA8"
    "ZGl2IGNsYXNzPSJjYXJkIj4ke1tbIk9yZGVyIGhpc3RvcnkiLCJvcmRlcnMiXSxbIldhbGxldCAm"
    "IHBheW1lbnRzIiwid2FsbGV0Il1dCiAgICAuY29uY2F0KGFueUYoInJlZkRhc2giLCJyZWZMMiIp"
    "P1tbIlJlZmVycmFsIHByb2dyYW0iLCJlYXJuIl1dOltdKQogICAgLm1hcCgoW3QsZ10pPT5gPGRp"
    "diBjbGFzcz0ia3YiIHN0eWxlPSJjdXJzb3I6cG9pbnRlciIgZGF0YS1nbz0iJHtnfSI+PHNwYW4g"
    "c3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHt0fTwvc3Bhbj48Yj4ke2ljKCJjaGV2Iil9PC9iPjwv"
    "ZGl2PmApLmpvaW4oIiIpfQogICAgJHtHKCJpMThuIixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuIHN0"
    "eWxlPSJjb2xvcjp2YXIoLS10eCkiPkxhbmd1YWdlPC9zcGFuPjxiPgogICAgICA8c2VsZWN0IGNs"
    "YXNzPSJpbnAiIHN0eWxlPSJoZWlnaHQ6MzJweDt3aWR0aDphdXRvO2ZvbnQtc2l6ZTouNzRyZW0i"
    "PjxvcHRpb24+RW5nbGlzaDwvb3B0aW9uPjxvcHRpb24+4KS54KS/4KSo4KWN4KSm4KWAPC9vcHRp"
    "b24+PC9zZWxlY3Q+PC9iPjwvZGl2PmApfTwvZGl2PgogICR7Rygibm90aWZQcmVmcyIsYDxkaXYg"
    "Y2xhc3M9InNlYyI+PGgyPk5vdGlmaWNhdGlvbnM8L2gyPjwvZGl2PgogIDxkaXYgY2xhc3M9ImNh"
    "cmQiPgogICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+UmVzdG9jayBhbGVydHM8"
    "c21hbGw+RE0gbWUgd2hlbiBteSBjb3VudHJ5IGlzIGJhY2s8L3NtYWxsPjwvZGl2PgogICAgICA8"
    "ZGl2IGNsYXNzPSJ0ZyAke1BSRUZTLnJlc3RvY2s/Im9uIjoiIn0iIGRhdGEtcHJlZj0icmVzdG9j"
    "ayI+PC9kaXY+PC9kaXY+CiAgICAke0coInJlY2VpcHRzIixgPGRpdiBjbGFzcz0ic3dhcCI+PGRp"
    "diBjbGFzcz0idCI+T3JkZXIgcmVjZWlwdHM8c21hbGw+U2VuZCBhIHJlY2VpcHQgYWZ0ZXIgZWFj"
    "aCBidXk8L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyAke1BSRUZTLnJlY2VpcHRz"
    "PyJvbiI6IiJ9IiBkYXRhLXByZWY9InJlY2VpcHRzIj48L2Rpdj48L2Rpdj5gKX0KICAgIDxkaXYg"
    "Y2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPlByb21vIG1lc3NhZ2VzPHNtYWxsPkZsYXNoIHNh"
    "bGVzIGFuZCBnaXZlYXdheXM8L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyAke1BS"
    "RUZTLnByb21vcz8ib24iOiIifSIgZGF0YS1wcmVmPSJwcm9tb3MiPjwvZGl2PjwvZGl2PgogICAg"
    "JHtHKCJwdXNoTm90aWYiLGA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5NaW5pIGFw"
    "cCBwdXNoPHNtYWxsPk1lc3NhZ2UgbWUgd2hlbiBzb21ldGhpbmcgaGFwcGVuczwvc21hbGw+PC9k"
    "aXY+CiAgICAgIDxkaXYgY2xhc3M9InRnICR7UFJFRlMucHVzaD8ib24iOiIifSIgZGF0YS1wcmVm"
    "PSJwdXNoIj48L2Rpdj48L2Rpdj5gKX0KICA8L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJidG5yb3ci"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBpZD0ic3Vw"
    "cG9ydGdvIj5TdXBwb3J0PC9idXR0b24+CiAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IiBpZD0i"
    "dGVybXNnbyI+VGVybXM8L2J1dHRvbj48L2Rpdj5gOwp9CgpmdW5jdGlvbiBiaW5kQnV5KCl7CiAg"
    "ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtd2lzaF0iKS5mb3JFYWNoKGVsPT5lbC5v"
    "bmNsaWNrPWU9PnsKICAgIGUuc3RvcFByb3BhZ2F0aW9uKCk7Y29uc3QgaT0rZWwuZGF0YXNldC53"
    "aXNoOwogICAgY29uc3Qgaz1XSVNILmluZGV4T2YoaSk7aWYoazwwKVdJU0gucHVzaChpKTtlbHNl"
    "IFdJU0guc3BsaWNlKGssMSk7CiAgICB0b2FzdChrPDA/IlNhdmVkIHRvIHdpc2hsaXN0IjoiUmVt"
    "b3ZlZCBmcm9tIHdpc2hsaXN0Iik7YXBwbHlGaWx0ZXIoKTt9KTsKICBkb2N1bWVudC5xdWVyeVNl"
    "bGVjdG9yQWxsKCJbZGF0YS1idXldIikuZm9yRWFjaChlbD0+ZWwub25jbGljaz1lPT57CiAgICBp"
    "ZihlLnRhcmdldC5jbG9zZXN0KCJbZGF0YS10b2FzdF0sW2RhdGEtd2lzaF0iKSlyZXR1cm47Y29u"
    "c3Qgcz1TVE9DS1srZWwuZGF0YXNldC5idXldOwogICAgaWYocy5xdHk9PT0wJiYhZmFsc2Upe3Rv"
    "YXN0KCJBbGVydCBzZXQg4oCUIHlvdSdsbCBnZXQgYSBETSBvbiByZXN0b2NrIik7cmV0dXJufQog"
    "ICAgYnV5U2hlZXQocyxzLnF0eT09PTApO30pOwp9CmxldCBRVFk9MSxDT1VQT049MDsKZnVuY3Rp"
    "b24gYnV5U2hlZXQocyxwcmUpewogIFFUWT0xO0NPVVBPTj0wOwogIGNvbnN0IG1heD1GRUFULmNh"
    "cnR8fEZFQVQuYnVsa0Rpc2M/TWF0aC5tYXgoMSxNYXRoLm1pbigyNSxzLnF0eXx8NSkpOjE7CiAg"
    "c2hlZXQoYDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEy"
    "cHg7bWFyZ2luLWJvdHRvbToxNHB4Ij4KICAgICAgPGRpdiBjbGFzcz0iZmxhZyIgc3R5bGU9Indp"
    "ZHRoOjUwcHg7aGVpZ2h0OjUwcHg7Zm9udC1zaXplOjEuNXJlbSI+JHtzLmZ9PC9kaXY+CiAgICAg"
    "IDxkaXYgc3R5bGU9ImZsZXg6MSI+PGgzIHN0eWxlPSJtYXJnaW46MDtmb250LXNpemU6MXJlbSI+"
    "JHtlc2Mocy5jKX0gQWNjb3VudDwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJn"
    "aW46MCI+JHtzLmNvZGV9IMK3ICR7cy5xdHkrIiBpbiBzdG9jayDCtyBpbnN0YW50In08L3A+CiAg"
    "ICAgICR7RygicmV2aWV3cyIsYDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6NHB4Ij4ke3N0YXJzKHMu"
    "Yyl9PC9kaXY+YCl9PC9kaXY+PC9kaXY+CiAgICAke0coImhlYWx0aENoZWNrIixgPGRpdiBjbGFz"
    "cz0ia3YiPjxzcGFuPlNlc3Npb24gaGVhbHRoPC9zcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS1v"
    "aykiPiR7aWMoImNoZWNrIil9IGNoZWNrZWQgbGl2ZTwvYj48L2Rpdj5gKX0KICAgICR7RygidmFy"
    "aWFudHMiLGA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VmFyaWFudDwvc3Bhbj48Yj4KICAgICAgPHNl"
    "bGVjdCBjbGFzcz0iaW5wIiBzdHlsZT0iaGVpZ2h0OjMycHg7d2lkdGg6YXV0bztmb250LXNpemU6"
    "Ljc0cmVtIj48b3B0aW9uPk5vIDJGQTwvb3B0aW9uPjxvcHRpb24+MkZBIG9uICgr4oK5NSk8L29w"
    "dGlvbj48b3B0aW9uPldpdGggbWFpbCAoK+KCuTgpPC9vcHRpb24+PC9zZWxlY3Q+PC9iPjwvZGl2"
    "PmApfQogICAgJHsoRkVBVC5jYXJ0fHxGRUFULmJ1bGtEaXNjKT9gPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPlF1YW50aXR5PC9zcGFuPjxiPgogICAgICA8c3BhbiBjbGFzcz0icXR5Ij48YnV0dG9uIGlk"
    "PSJxbSI+4oiSPC9idXR0b24+PGIgaWQ9InFuIj4xPC9iPjxidXR0b24gaWQ9InFwIj4rPC9idXR0"
    "b24+PC9zcGFuPjwvYj48L2Rpdj5gOiIifQogICAgJHtHKCJidWxrRGlzYyIsYDxwIGNsYXNzPSJz"
    "dWIiIGlkPSJiZG5vdGUiIHN0eWxlPSJtYXJnaW46NnB4IDAgMDtmb250LXNpemU6LjdyZW0iPkJ1"
    "eSA1KyBmb3IgNSUgb2ZmIMK3IDEwKyBmb3IgMTAlIG9mZiDCtyAyNSsgZm9yIDE1JSBvZmYuPC9w"
    "PmApfQogICAgJHtHKCJjb3Vwb25zIixgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjEwcHgiPjxsYWJlbD5Db3Vwb24gY29kZTwvbGFiZWw+CiAgICAgIDxkaXYgc3R5bGU9ImRp"
    "c3BsYXk6ZmxleDtnYXA6N3B4Ij48aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImNwbiIgcGxhY2Vob2xk"
    "ZXI9IldFTENPTUUxMCI+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbSIgaWQ9ImNwbmFwcGx5"
    "IiBzdHlsZT0iZmxleDowIDAgYXV0byI+QXBwbHk8L2J1dHRvbj48L2Rpdj48L2Rpdj5gKX0KICAg"
    "IDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5QcmljZTwvc3Bhbj48YiBpZD0iYnByaWNlIj4ke21vbmV5"
    "KHMucHJpY2UpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Zb3VyIGJhbGFu"
    "Y2U8L3NwYW4+PGI+JHttb25leShCQUwpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48"
    "c3Bhbj5BZnRlciBwdXJjaGFzZTwvc3Bhbj48YiBpZD0iYmFmdGVyIj4ke21vbmV5KE1hdGgubWF4"
    "KDAsQkFMLXMucHJpY2UpKX08L2I+PC9kaXY+CiAgICA8ZGl2IGlkPSJzcGxpdG5vdGUiPjwvZGl2"
    "PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4Ij48YnV0dG9u"
    "IGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtY2xvc2U+Q2FuY2VsPC9idXR0b24+CiAgICAgIDxidXR0"
    "b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJiY29uZmlybSI+Q29uZmlybSBCdXk8L2J1dHRvbj48L2Rp"
    "dj4KICAgICR7RygiY2FydCIsYDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IGJsb2NrIHNtIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDo4cHgiIGlkPSJiYWRkY2FydCI+QWRkIHRvIGNhcnQ8L2J1dHRvbj5gKX1g"
    "KTsKICBjb25zdCBnPWlkPT5kb2N1bWVudC5nZXRFbGVtZW50QnlJZChpZCk7CiAgZnVuY3Rpb24g"
    "dG90YWwoKXsKICAgIGxldCB1bml0PXMucHJpY2UsIHE9UVRZLCB0PXVuaXQqcTsKICAgIGlmKEZF"
    "QVQuYnVsa0Rpc2Mpe2NvbnN0IGQ9cT49MjU/LjE1OnE+PTEwPy4xMDpxPj01Py4wNTowO3Q9dCoo"
    "MS1kKTt9CiAgICBpZihGRUFULmNvdXBvbnMmJkNPVVBPTil0PXQqKDEtQ09VUE9OLzEwMCk7CiAg"
    "ICByZXR1cm4gTWF0aC5yb3VuZCh0KjEwMCkvMTAwOwogIH0KICBmdW5jdGlvbiBwYWludCgpewog"
    "ICAgY29uc3QgdD10b3RhbCgpOwogICAgZygiYnByaWNlIikudGV4dENvbnRlbnQ9bW9uZXkodCk7"
    "CiAgICBnKCJiYWZ0ZXIiKS50ZXh0Q29udGVudD1tb25leShNYXRoLm1heCgwLEJBTC10KSk7CiAg"
    "ICBjb25zdCBzbj1nKCJzcGxpdG5vdGUiKTsKICAgIGlmKEZFQVQuc3BsaXRQYXkmJnQ+QkFMKXtz"
    "bi5pbm5lckhUTUw9YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5TcGxpdCBwYXltZW50PC9zcGFuPjxi"
    "PiR7bW9uZXkoQkFMKX0gd2FsbGV0ICsgJHttb25leSh0LUJBTCl9IFVQSTwvYj48L2Rpdj5gO30K"
    "ICAgIGVsc2Ugc24uaW5uZXJIVE1MPSIiOwogICAgaWYoZygicW4iKSlnKCJxbiIpLnRleHRDb250"
    "ZW50PVFUWTsKICB9CiAgaWYoZygicW0iKSlnKCJxbSIpLm9uY2xpY2s9KCk9PntRVFk9TWF0aC5t"
    "YXgoMSxRVFktMSk7cGFpbnQoKTt9OwogIGlmKGcoInFwIikpZygicXAiKS5vbmNsaWNrPSgpPT57"
    "UVRZPU1hdGgubWluKG1heCxRVFkrMSk7cGFpbnQoKTt9OwogIGlmKGcoImNwbiIpJiZPRkZFUiYm"
    "IWcoImNwbiIpLnZhbHVlKWcoImNwbiIpLnZhbHVlPU9GRkVSOwogIGlmKGcoImNwbmFwcGx5Iikp"
    "ZygiY3BuYXBwbHkiKS5vbmNsaWNrPSgpPT57CiAgICBjb25zdCB2PShnKCJjcG4iKS52YWx1ZXx8"
    "IiIpLnRyaW0oKS50b1VwcGVyQ2FzZSgpOwogICAgaWYoIXYpe3RvYXN0KCJFbnRlciBhIGNvdXBv"
    "biBjb2RlIik7cmV0dXJufQogICAgQ09VUE9OPXY9PT0iV0VMQ09NRTEwIj8xMDp2PT09IlNBVkUy"
    "MCI/MjA6MDsKICAgIHRvYXN0KENPVVBPTj9DT1VQT04rIiUgY291cG9uIGFwcGxpZWQiOiJDb3Vw"
    "b24gbm90IHZhbGlkIik7cGFpbnQoKTt9OwogIGlmKGcoImJhZGRjYXJ0IikpZygiYmFkZGNhcnQi"
    "KS5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKExJVkUpewogICAgICBjb25zdCBkPWF3YWl0IGFw"
    "aVRyeSgiL2FwaS9jYXJ0Iix7YWN0aW9uOiJhZGQiLGNvdW50cnk6cy5jLHF0eTpRVFl9KTsKICAg"
    "ICAgaWYoIWQpcmV0dXJuOwogICAgICBjbG9zZVNoZWV0KCk7dG9hc3QoUVRZKyIgw5cgIitzLmMr"
    "IiBhZGRlZCB0byBjYXJ0Iik7YXdhaXQgcmVmcmVzaCgpO3JldHVybjsKICAgIH0KICAgIGZvcihs"
    "ZXQgaT0wO2k8UVRZO2krKylDQVJULnB1c2gocyk7YXBwbHlGZWF0dXJlcygpO2Nsb3NlU2hlZXQo"
    "KTt0b2FzdChRVFkrIiDDlyAiK3MuYysiIGFkZGVkIHRvIGNhcnQiKTt9OwogIGcoImJjb25maXJt"
    "Iikub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCB0PXRvdGFsKCk7CiAgICBpZihCQUw8dCYm"
    "IUZFQVQuc3BsaXRQYXkpe3RvYXN0KCJOb3QgZW5vdWdoIGJhbGFuY2Ug4oCUIGFkZCBmdW5kcyIp"
    "O3JldHVybn0KICAgIGlmKEJBTDx0JiZGRUFULnNwbGl0UGF5KXtjbG9zZVNoZWV0KCk7dG9hc3Qo"
    "IlBheWluZyAiK21vbmV5KHQtQkFMKSsiIGJ5IFVQSSIpO2RlcG9zaXRTaGVldChNYXRoLmNlaWwo"
    "dC1CQUwpKTtyZXR1cm59CiAgICBpZihMSVZFKXsKICAgICAgY29uc3QgYnRuPWcoImJjb25maXJt"
    "Iik7YnRuLmRpc2FibGVkPXRydWU7YnRuLnRleHRDb250ZW50PSJXb3JraW5n4oCmIjsKICAgICAg"
    "Y29uc3QgY29kZT1nKCJjcG4iKT8oZygiY3BuIikudmFsdWV8fCIiKS50cmltKCk6IiI7CiAgICAg"
    "IGNvbnN0IGQ9YXdhaXQgYXBpVHJ5KCIvYXBpL2J1eSIse2NvdW50cnk6cy5jLHF0eTpRVFksY291"
    "cG9uOmNvZGV9KTsKICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0iQ29u"
    "ZmlybSBCdXkiOwogICAgICBpZighZClyZXR1cm47CiAgICAgIGlmKHR5cGVvZiBkLmJhbGFuY2U9"
    "PT0ibnVtYmVyIilCQUw9ZC5iYWxhbmNlOwogICAgICBzeW5jQmFsKCk7Y2xvc2VTaGVldCgpO2J1"
    "cnN0KCk7CiAgICAgIGNvbnN0IGZpcnN0PShkLm9yZGVycyYmZC5vcmRlcnNbMF0pfHxudWxsOwog"
    "ICAgICB0b2FzdChmaXJzdD8oIkRlbGl2ZXJlZCDigJQgIitmaXJzdC5pZCk6IlB1cmNoYXNlZCDi"
    "gJQgZGV0YWlscyBpbiBPcmRlcnMiKTsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgICBpZihm"
    "aXJzdCYmZmlyc3QucGF5bG9hZClzZXRUaW1lb3V0KCgpPT5zaGVldChgPGgzIHN0eWxlPSJtYXJn"
    "aW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+JHtlc2MoZmlyc3QuaWQpfTwvaDM+CiAgICAgICAg"
    "PHAgY2xhc3M9InN1YiI+S2VlcCB0aGlzIHNhZmUuIEl0IGlzIGFsc28gaW4gT3JkZXJzLjwvcD4K"
    "ICAgICAgICA8ZGl2IGNsYXNzPSJrdiIgc3R5bGU9ImRpc3BsYXk6YmxvY2siPjxjb2RlIHN0eWxl"
    "PSJmb250LXNpemU6LjcycmVtO3dvcmQtYnJlYWs6YnJlYWstYWxsIj4ke2VzYyhmaXJzdC5wYXls"
    "b2FkKX08L2NvZGU+PC9kaXY+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMHB4IiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRvbj5gKSwzNTApOwogICAg"
    "ICByZXR1cm47CiAgICB9CiAgICBCQUwtPXQ7c3luY0JhbCgpO2Nsb3NlU2hlZXQoKTtidXJzdCgp"
    "OwogICAgdG9hc3QoIlB1cmNoYXNlZCDigJQgT1RQIGlzIGluIE9yZGVycyIpOwogICAgaWYoRkVB"
    "VC5wdXNoTm90aWYpc2V0VGltZW91dCgoKT0+dG9hc3QoIkJvdCBub3RpZmljYXRpb24gc2VudCB0"
    "byB5b3VyIGNoYXQiKSwxNTAwKTt9OwogIHBhaW50KCk7Cn0KZnVuY3Rpb24gY2FydFNoZWV0KCl7"
    "CiAgaWYoIUNBUlQubGVuZ3RoKXt0b2FzdCgiQ2FydCBpcyBlbXB0eSIpO3JldHVybn0KICBjb25z"
    "dCB0PUNBUlQucmVkdWNlKChhLGIpPT5hK2IucHJpY2UsMCk7CiAgc2hlZXQoYDxoMyBzdHlsZT0i"
    "bWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPllvdXIgY2FydDwvaDM+CiAgICA8cCBjbGFz"
    "cz0ic3ViIj4ke0NBUlQubGVuZ3RofSBpdGVtJHtDQVJULmxlbmd0aD4xPyJzIjoiIn0g4oCUIG9u"
    "ZSBjb25maXJtYXRpb24sIG9uZSBkZWJpdC48L3A+CiAgICAke0NBUlQubWFwKChzLGkpPT5gPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPiR7cy5mfSAke2VzYyhzLmMpfSAke3MuY29kZX08L3NwYW4+CiAg"
    "ICAgIDxiPiR7bW9uZXkocy5wcmljZSl9IDxidXR0b24gY2xhc3M9ImJ0biB4cyBnaG9zdCIgZGF0"
    "YS1jZGVsPSIke2l9Ij7DlzwvYnV0dG9uPjwvYj48L2Rpdj5gKS5qb2luKCIiKX0KICAgIDxkaXYg"
    "Y2xhc3M9Imt2Ij48c3Bhbj5Ub3RhbDwvc3Bhbj48Yj4ke21vbmV5KHQpfTwvYj48L2Rpdj4KICAg"
    "IDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTRweCI+PGJ1dHRvbiBjbGFz"
    "cz0iYnRuIGdob3N0IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+CiAgICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBjdGEiIGlkPSJjYXJ0YnV5Ij5DaGVja291dDwvYnV0dG9uPjwvZGl2PmApOwogIGRv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNkZWxdIikuZm9yRWFjaChiPT5iLm9uY2xp"
    "Y2s9KCk9PntDQVJULnNwbGljZSgrYi5kYXRhc2V0LmNkZWwsMSk7YXBwbHlGZWF0dXJlcygpO2Nh"
    "cnRTaGVldCgpO30pOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjYXJ0YnV5Iikub25jbGlj"
    "az0oKT0+ewogICAgaWYoQkFMPHQpe3RvYXN0KCJOb3QgZW5vdWdoIGJhbGFuY2Ug4oCUIGFkZCBm"
    "dW5kcyIpO3JldHVybn0KICAgIEJBTC09dDtDQVJUPVtdO3N5bmNCYWwoKTthcHBseUZlYXR1cmVz"
    "KCk7Y2xvc2VTaGVldCgpO2J1cnN0KCk7dG9hc3QoIk9yZGVyIHBsYWNlZCDigJQgT1RQcyBhcmUg"
    "aW4gT3JkZXJzIik7fTsKfQpmdW5jdGlvbiBnb1RhYih0KXsKICBjb25zdCBiPWRvY3VtZW50LnF1"
    "ZXJ5U2VsZWN0b3IoYCN0YWJzIGJ1dHRvbltkYXRhLXRhYj0iJHt0fSJdYCk7CiAgaWYoYiliLmNs"
    "aWNrKCk7Cn0KZnVuY3Rpb24gYmluZENvbW1vbigpewogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JB"
    "bGwoIltkYXRhLW9yZF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBjb25z"
    "dCB3aGF0PWIuZGF0YXNldC5vcmQ7CiAgICBjb25zdCBjYXJkPWIuY2xvc2VzdCgiW2RhdGEtb3Jk"
    "ZXItaWRdIik7CiAgICBjb25zdCBvaWQ9KGNhcmQmJmNhcmQuZGF0YXNldC5vcmRlcklkKXx8KE9S"
    "REVSU1swXSYmT1JERVJTWzBdLmlkKXx8IiI7CiAgICBpZighb2lkKXt0b2FzdCgiTm8gb3JkZXIg"
    "c2VsZWN0ZWQiKTtyZXR1cm47fQogICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTty"
    "ZXR1cm47fQogICAgY29uc3QgbGJsPWIudGV4dENvbnRlbnQ7Yi5kaXNhYmxlZD10cnVlO2IudGV4"
    "dENvbnRlbnQ9IuKApiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRlciIse2lk"
    "Om9pZCx3aGF0OndoYXR9KTsKICAgIGIuZGlzYWJsZWQ9ZmFsc2U7Yi50ZXh0Q29udGVudD1sYmw7"
    "CiAgICBpZighcilyZXR1cm47CiAgICBpZih3aGF0PT09ImRldGFpbHMiKXRvYXN0KCJMb2dpbiBk"
    "ZXRhaWxzIHNlbnQgdG8geW91ciBjaGF0Iik7CiAgICBpZih3aGF0PT09ImZpbGUiKXRvYXN0KCJT"
    "ZXNzaW9uIGZpbGUgc2VudCB0byB5b3VyIGNoYXQiKTsKICAgIGlmKHdoYXQ9PT0icmVjZWlwdCIp"
    "dG9hc3QoIlJlY2VpcHQgc2VudCB0byB5b3VyIGNoYXQiKTsKICB9KTsKICBkb2N1bWVudC5xdWVy"
    "eVNlbGVjdG9yQWxsKCJbZGF0YS1yYXRlLWdvXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5j"
    "KCk9PnsKICAgIGNvbnN0IHN0YXJzPStiLmRhdGFzZXQucmF0ZUdvOwogICAgY29uc3QgYz0od2lu"
    "ZG93Ll9fcmF0ZUNvdW50cnkpfHwoKE9SREVSU1swXXx8e30pLmMpfHwiIjsKICAgIGlmKCFMSVZF"
    "KXtjbG9zZVNoZWV0KCk7dG9hc3QoIlRoYW5rcyBmb3IgdGhlICIrc3RhcnMrIuKYhSIpO3JldHVy"
    "bjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9yZXZpZXciLHtjb3VudHJ5OmMscmF0"
    "aW5nOnN0YXJzfSk7CiAgICBpZighcilyZXR1cm47CiAgICBjbG9zZVNoZWV0KCk7dG9hc3QoIlRo"
    "YW5rcyDigJQgIitjKyIgaXMgbm93ICIrci5hdmcrIuKYhSIpOwogICAgYXdhaXQgcmVmcmVzaCgp"
    "OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWFjdF0iKS5mb3JFYWNo"
    "KGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCB3aGF0PWIuZGF0YXNldC5hY3Q7CiAg"
    "ICBpZih3aGF0PT09ImNoYW5uZWwiKXsKICAgICAgY29uc3QgYz0oQURNLmNoYW5uZWxzJiZBRE0u"
    "Y2hhbm5lbHNbMF0pfHxudWxsOwogICAgICBjb25zdCBoPWM/U3RyaW5nKGMuaGFuZGxlfHwiIiku"
    "cmVwbGFjZSgvXkAvLCIiKToiIjsKICAgICAgaWYoaClvcGVuUGF5KGguc3RhcnRzV2l0aCgiaHR0"
    "cCIpP2g6KCJodHRwczovL3QubWUvIitoKSk7CiAgICAgIGVsc2UgdG9hc3QoIk5vIGNoYW5uZWwg"
    "aGFzIGJlZW4gYWRkZWQgeWV0Iik7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9PT0i"
    "Zmxhc2giKXsKICAgICAgY29uc3Qgb249QURNLm1hcmtldCYmQURNLm1hcmtldC5mbGFzaF9vbjsK"
    "ICAgICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkZs"
    "YXNoIHNhbGU8L2gzPgogICAgICAgIDxwIGNsYXNzPSJzdWIiPiR7b24/KCJFdmVyeSBwcmljZSBp"
    "cyAiKyhBRE0ubWFya2V0LmZsYXNofHwwKSsiJSBvZmYgcmlnaHQgbm93LiIpOiJObyBzYWxlIGlz"
    "IHJ1bm5pbmcgYXQgdGhlIG1vbWVudC4ifTwvcD4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "YmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5g"
    "KTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJzb3J0Iil7CiAgICAgIEZJTFRF"
    "UkVEPShGSUxURVJFRCYmRklMVEVSRUQubGVuZ3RoP0ZJTFRFUkVEOlNUT0NLKS5zbGljZSgpLnNv"
    "cnQoKHgseSk9Pih5LnF0eXx8MCktKHgucXR5fHwwKSk7CiAgICAgIHJlbmRlcigpO3RvYXN0KCJT"
    "b3J0ZWQgYnkgd2hhdCBpcyBpbiBzdG9jayIpOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3"
    "aGF0PT09ImFsZXJ0cyIpewogICAgICBjb25zdCBtaW5lPSh3aW5kb3cuX19hbGVydHMpfHxbXTsK"
    "ICAgICAgY29uc3Qgb2ZmPVNUT0NLLmZpbHRlcih4PT4heC5xdHkpLm1hcCh4PT54LmMpOwogICAg"
    "ICBjb25zdCBsaXN0PShvZmYubGVuZ3RoP29mZjpTVE9DSy5tYXAoeD0+eC5jKSkuc2xpY2UoMCw4"
    "KTsKICAgICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0i"
    "PlJlc3RvY2sgYWxlcnRzPC9oMz4KICAgICAgICA8cCBjbGFzcz0ic3ViIj5XZSBtZXNzYWdlIHlv"
    "dSB0aGUgbW9tZW50IG9uZSBvZiB0aGVzZSBpcyBiYWNrLjwvcD4KICAgICAgICAke2xpc3QubWFw"
    "KGM9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHtlc2MoYyl9PC9zcGFuPgogICAgICAgICAgPGI+"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIHhzICR7bWluZS5pbmRleE9mKGMpPj0wPyIiOiJzb2Z0In0iIGRh"
    "dGEtYWxlcnQ9IiR7ZXNjKGMpfSI+CiAgICAgICAgICAgICR7bWluZS5pbmRleE9mKGMpPj0wPyJP"
    "biI6Ik5vdGlmeSBtZSJ9PC9idXR0b24+PC9iPjwvZGl2PmApLmpvaW4oIiIpfQogICAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9z"
    "ZT5Eb25lPC9idXR0b24+YCk7CiAgICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRh"
    "LWFsZXJ0XSIpLmZvckVhY2goYT0+YS5vbmNsaWNrPWFzeW5jKCk9PnsKICAgICAgICBpZighTElW"
    "RSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICAgICAgY29uc3Qgcj1hd2FpdCBh"
    "cGlUcnkoIi9hcGkvYWxlcnQiLHtjb3VudHJ5OmEuZGF0YXNldC5hbGVydH0pOwogICAgICAgIGlm"
    "KCFyKXJldHVybjsKICAgICAgICB3aW5kb3cuX19hbGVydHM9ci5hbGVydHN8fFtdOwogICAgICAg"
    "IGEudGV4dENvbnRlbnQ9ci5vbj8iT24iOiJOb3RpZnkgbWUiOwogICAgICAgIGEuY2xhc3NMaXN0"
    "LnRvZ2dsZSgic29mdCIsIXIub24pOwogICAgICAgIHRvYXN0KHIub24/KCJXZSB3aWxsIHRlbGwg"
    "eW91IHdoZW4gIithLmRhdGFzZXQuYWxlcnQrIiBpcyBiYWNrIik6IkFsZXJ0IHJlbW92ZWQiKTsK"
    "ICAgICAgfSk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9PT0iY3N2Iil7CiAgICAg"
    "IGNvbnN0IHJvd3M9W1sib3JkZXIiLCJjb3VudHJ5IiwicHJpY2UiLCJzdGF0dXMiLCJ3aGVuIl1d"
    "CiAgICAgICAgLmNvbmNhdChPUkRFUlMubWFwKG89PltvLmlkLG8uYyxvLnByaWNlLG8uc3Qsby53"
    "aGVuXSkpOwogICAgICBjb25zdCBjc3Y9cm93cy5tYXAocj0+ci5qb2luKCIsIikpLmpvaW4oIlxu"
    "Iik7CiAgICAgIGNvbnN0IGE9ZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgiYSIpOwogICAgICBhLmhy"
    "ZWY9ImRhdGE6dGV4dC9jc3Y7Y2hhcnNldD11dGYtOCwiK2VuY29kZVVSSUNvbXBvbmVudChjc3Yp"
    "OwogICAgICBhLmRvd25sb2FkPSJteS1vcmRlcnMuY3N2Ijtkb2N1bWVudC5ib2R5LmFwcGVuZENo"
    "aWxkKGEpO2EuY2xpY2soKTthLnJlbW92ZSgpOwogICAgICB0b2FzdChPUkRFUlMubGVuZ3RoKyIg"
    "b3JkZXJzIGRvd25sb2FkZWQiKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJz"
    "dXBwb3J0Iil7CiAgICAgIGNvbnN0IGg9KFNUT1JFLnN1cHBvcnR8fCIiKS5yZXBsYWNlKC9eQC8s"
    "IiIpOwogICAgICBpZihoKW9wZW5QYXkoImh0dHBzOi8vdC5tZS8iK2gpOwogICAgICBlbHNlIHRv"
    "YXN0KCJTZW5kIHlvdXIgcXVlc3Rpb24gaW4gdGhlIGNoYXQgYW5kIHdlIHdpbGwgcmVwbHkiKTsK"
    "ICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJ0YXNrIil7CiAgICAgIGNvbnN0IHJv"
    "dz1iLmNsb3Nlc3QoIi5jYXJkIik7CiAgICAgIGNvbnN0IGxhYmVsPXJvdz8ocm93LnF1ZXJ5U2Vs"
    "ZWN0b3IoImIiKXx8e30pLnRleHRDb250ZW50fHwiIjoiIjsKICAgICAgaWYoL2NoYW5uZWwvaS50"
    "ZXN0KGxhYmVsKSl7Yi5ibHVyKCk7ZG9jdW1lbnQucXVlcnlTZWxlY3RvcignW2RhdGEtYWN0PSJj"
    "aGFubmVsIl0nKT8uY2xpY2soKTtyZXR1cm47fQogICAgICBpZigvb3JkZXIvaS50ZXN0KGxhYmVs"
    "KSl7Z29UYWIoInNob3AiKTt0b2FzdCgiUGljayBhIGNvdW50cnkgdG8gcGxhY2UgeW91ciBmaXJz"
    "dCBvcmRlciIpO3JldHVybjt9CiAgICAgIGdvVGFiKCJlYXJuIik7dG9hc3QoIlNoYXJlIHlvdXIg"
    "aW52aXRlIGxpbmsgdG8gZmluaXNoIHRoaXMiKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYo"
    "d2hhdD09PSJzdGF0dXMiKXsKICAgICAgY29uc3QgZz0oQURNLnBheSYmQURNLnBheS5nbWFpbF9z"
    "ZXQpOwogICAgICBjb25zdCBwZW5kPShBRE0ucGF5JiZBRE0ucGF5LnBlbmRpbmcpfHwwOwogICAg"
    "ICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+UGF5bWVu"
    "dCBjaGVja2luZzwvaDM+CiAgICAgICAgPHAgY2xhc3M9InN1YiI+JHtnPyJBdXRvbWF0aWMgY2hl"
    "Y2tpbmcgaXMgb24uIFBheSwgdGhlbiB0YXAgVmVyaWZ5LiI6CiAgICAgICAgICAiQXV0b21hdGlj"
    "IGNoZWNraW5nIGlzIG5vdCBzZXQgdXAsIHNvIGFuIGFkbWluIGNvbmZpcm1zIHBheW1lbnRzIGJ5"
    "IGhhbmQuIn08L3A+CiAgICAgICAgJHtwZW5kP2A8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+V2FpdGlu"
    "ZyBmb3IgYW4gYWRtaW48L3NwYW4+PGI+JHtwZW5kfTwvYj48L2Rpdj5gOiIifQogICAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9z"
    "ZT5DbG9zZTwvYnV0dG9uPmApOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09ImNv"
    "cHljb2RlIil7CiAgICAgIGNvbnN0IGVsPWIuY2xvc2VzdCgiLmNhcmQsLnNoZWV0Ym9keSwjc2hl"
    "ZXQiKXx8ZG9jdW1lbnQ7CiAgICAgIGNvbnN0IGNvZGU9KGVsLnF1ZXJ5U2VsZWN0b3IoImNvZGUi"
    "KXx8e30pLnRleHRDb250ZW50fHwiIjsKICAgICAgY29weVRleHQoY29kZS50cmltKCksIkNvZGUi"
    "KTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJyZXBsYWNlIil7CiAgICAgIGNv"
    "bnN0IGNhcmQ9Yi5jbG9zZXN0KCJbZGF0YS1vcmRlci1pZF0iKTsKICAgICAgY29uc3Qgb2lkPShj"
    "YXJkJiZjYXJkLmRhdGFzZXQub3JkZXJJZCl8fChPUkRFUlNbMF0mJk9SREVSU1swXS5pZCl8fCIi"
    "OwogICAgICBpZighTElWRSl7Y2xvc2VTaGVldCgpO3RvYXN0KCJSZXBsYWNlbWVudCBzZW50Iik7"
    "cmV0dXJuO30KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvb3JkZXIiLHtpZDpvaWQs"
    "d2hhdDoicmVwbGFjZSJ9KTsKICAgICAgaWYoIXIpcmV0dXJuOwogICAgICBjbG9zZVNoZWV0KCk7"
    "CiAgICAgIHRvYXN0KHIucmVmdW5kZWQ/Ik5vIHN0b2NrIGxlZnQg4oCUIHJlZnVuZGVkIHRvIHlv"
    "dXIgd2FsbGV0IjooIlJlcGxhY2VkIOKAlCAiK3Iub3JkZXIpKTsKICAgICAgYXdhaXQgcmVmcmVz"
    "aCgpOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09ImNyeXB0byIpewogICAgICBp"
    "ZighTElWRSl7Y2xvc2VTaGVldCgpO3RvYXN0KCJXZSB3aWxsIGNvbmZpcm0geW91ciB0cmFuc2Zl"
    "ciBzaG9ydGx5Iik7cmV0dXJuO30KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvY3J5"
    "cHRvIix7cmVmOiIifSk7CiAgICAgIGlmKHIpe2Nsb3NlU2hlZXQoKTt0b2FzdCgiV2Ugd2lsbCBj"
    "b25maXJtIHlvdXIgdHJhbnNmZXIgc2hvcnRseSIpO30KICAgICAgcmV0dXJuOwogICAgfQogIH0p"
    "OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNvcHldIikuZm9yRWFjaChiPT5i"
    "Lm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IHdoYXQ9Yi5kYXRhc2V0LmNvcHk7CiAgICBpZih3aGF0"
    "PT09InVwaSIpcmV0dXJuIGNvcHlUZXh0KFNUT1JFLnVwaSwiVVBJIElEIik7CiAgICBpZih3aGF0"
    "PT09InJlZiIpewogICAgICBsZXQgbD1FQVJOLmxpbmt8fCIiOwogICAgICBpZihsJiYhL15odHRw"
    "cz86Ly50ZXN0KGwpKWw9Imh0dHBzOi8vIitsOwogICAgICByZXR1cm4gY29weVRleHQobCwiSW52"
    "aXRlIGxpbmsiKTsKICAgIH0KICAgIHJldHVybiBjb3B5VGV4dChiLmRhdGFzZXQuY29weVZhbHVl"
    "fHxiLnRleHRDb250ZW50LnRyaW0oKSwiQ29waWVkIik7CiAgfSk7CiAgY29uc3QgcmQ9ZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoInJlZGVlbWdvIik7CiAgaWYocmQpcmQub25jbGljaz1hc3luYygp"
    "PT57CiAgICBpZighTElWRSl7dG9hc3QoIlJlZGVlbWVkIik7cmV0dXJuO30KICAgIHJkLmRpc2Fi"
    "bGVkPXRydWU7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9yZWRlZW0iLHt9KTsKICAg"
    "IHJkLmRpc2FibGVkPWZhbHNlOwogICAgaWYoIXIpcmV0dXJuOwogICAgQkFMPXIuYmFsYW5jZTtz"
    "eW5jQmFsKCk7YnVyc3QoKTsKICAgIHRvYXN0KHIucG9pbnRzKyIgcG9pbnRzIOKGkiAiK21vbmV5"
    "KHIuYW1vdW50KSk7CiAgICBhd2FpdCByZWZyZXNoKCk7CiAgfTsKICBjb25zdCBvdT1kb2N1bWVu"
    "dC5nZXRFbGVtZW50QnlJZCgib2ZmZXJ1c2UiKTsKICBpZihvdSlvdS5vbmNsaWNrPSgpPT57CiAg"
    "ICBjb25zdCBjPShBRE0uY291cG9ucyYmQURNLmNvdXBvbnMubGVuZ3RoKT9BRE0uY291cG9uc1sw"
    "XS5jb2RlOiIiOwogICAgaWYoYyl7T0ZGRVI9Yztjb3B5VGV4dChjLCJDb3Vwb24iKTt9CiAgICBl"
    "bHNlIHRvYXN0KCJObyBjb3Vwb24gaXMgcnVubmluZyByaWdodCBub3ciKTsKICB9OwogIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXByZWZdIikuZm9yRWFjaCh0PT50Lm9uY2xpY2s9"
    "YXN5bmMoKT0+ewogICAgY29uc3Qga2V5PXQuZGF0YXNldC5wcmVmLCBvbj0hdC5jbGFzc0xpc3Qu"
    "Y29udGFpbnMoIm9uIik7CiAgICB0LmNsYXNzTGlzdC50b2dnbGUoIm9uIixvbik7UFJFRlNba2V5"
    "XT1vbjsKICAgIGlmKExJVkUpewogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9wcmVm"
    "cyIse2tleTprZXksb246b259KTsKICAgICAgaWYoIXIpe3QuY2xhc3NMaXN0LnRvZ2dsZSgib24i"
    "LCFvbik7UFJFRlNba2V5XT0hb247cmV0dXJuO30KICAgIH0KICAgIHRvYXN0KG9uPyJUdXJuZWQg"
    "b24iOiJUdXJuZWQgb2ZmIik7CiAgfSk7CiAgY29uc3QgdGdiPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJ0ZXJtc2dvIik7CiAgaWYodGdiKXRnYi5vbmNsaWNrPSgpPT5zaGVldChgPGgzIHN0eWxl"
    "PSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+VGVybXMgJiByZWZ1bmRzPC9oMz4KICAg"
    "IDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJ3aGl0ZS1zcGFjZTpwcmUtd3JhcCI+JHtlc2MoU1RPUkUu"
    "dGVybXN8fENNUy50ZXJtc3x8Ik5vIHRlcm1zIGhhdmUgYmVlbiB3cml0dGVuIHlldC4iKX08L3A+"
    "CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRh"
    "dGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTsKICBjb25zdCBzZz1kb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgic3VwcG9ydGdvIik7CiAgaWYoc2cpc2cub25jbGljaz0oKT0+ewogICAgY29uc3QgaD0o"
    "U1RPUkUuc3VwcG9ydHx8IiIpLnJlcGxhY2UoL15ALywiIik7CiAgICBpZihoKW9wZW5QYXkoImh0"
    "dHBzOi8vdC5tZS8iK2gpOwogICAgZWxzZSBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRw"
    "eDtmb250LXNpemU6MXJlbSI+U3VwcG9ydDwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiPlNlbmQg"
    "eW91ciBxdWVzdGlvbiBpbiB0aGUgY2hhdCBhbmQgd2Ugd2lsbCByZXBseS48L3A+CiAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9z"
    "ZT5DbG9zZTwvYnV0dG9uPmApOwogIH07Cn0KZnVuY3Rpb24gYmluZFN0YWdlKCl7CiAgYmluZENv"
    "bW1vbigpOwogIGJpbmRCdXkoKTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1z"
    "Y3JvbGxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PmRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KGIuZGF0YXNldC5zY3JvbGwpLnNjcm9sbEludG9WaWV3KHtiZWhhdmlvcjoic21vb3RoIn0pKTsK"
    "ICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1vdHBdIikuZm9yRWFjaChiPT5iLm9u"
    "Y2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgY2FyZD1iLmNsb3Nlc3QoIltkYXRhLW9yZGVyLWlk"
    "XSIpOwogICAgY29uc3Qgb2lkPShjYXJkJiZjYXJkLmRhdGFzZXQub3JkZXJJZCl8fGIuZGF0YXNl"
    "dC5vdHB8fCIiOwogICAgY29uc3Qgb3JkPU9SREVSUy5maW5kKHg9PnguaWQ9PT1vaWQpfHx7fTsK"
    "ICAgIGlmKCFMSVZFKXsKICAgICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9u"
    "dC1zaXplOjFyZW0iPkxvZ2luIGNvZGU8L2gzPgogICAgICAgIDxwIGNsYXNzPSJzdWIiPlNhbXBs"
    "ZSBjb2RlIOKAlCB0aGlzIGlzIHRoZSBwcmV2aWV3LjwvcD4KICAgICAgICA8ZGl2IGNsYXNzPSJv"
    "dHBib3giPjQgMiA5IDEgNzwvZGl2PgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTBweCIgZGF0YS1jbG9zZT5Eb25lPC9idXR0b24+YCk7CiAgICAg"
    "IHJldHVybjsKICAgIH0KICAgIGNvbnN0IGxibD1iLnRleHRDb250ZW50O2IuZGlzYWJsZWQ9dHJ1"
    "ZTtiLnRleHRDb250ZW50PSJGZXRjaGluZ+KApiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgi"
    "L2FwaS9vcmRlciIse2lkOm9pZCx3aGF0OiJvdHAifSk7CiAgICBiLmRpc2FibGVkPWZhbHNlO2Iu"
    "dGV4dENvbnRlbnQ9bGJsOwogICAgaWYoIXIpcmV0dXJuOwogICAgc2hlZXQoYDxoMyBzdHlsZT0i"
    "bWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkxvZ2luIGNvZGU8L2gzPgogICAgICA8cCBj"
    "bGFzcz0ic3ViIj4ke29yZC5waG9uZT8oIkZvciAiK2VzYyhvcmQucGhvbmUpKToiRm9yIHRoaXMg"
    "YWNjb3VudCJ9PC9wPgogICAgICA8ZGl2IGNsYXNzPSJvdHBib3giPiR7U3RyaW5nKHIuY29kZSku"
    "c3BsaXQoIiIpLmpvaW4oIiAiKX08L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkNv"
    "ZGVzIGxlZnQ8L3NwYW4+PGI+JHtyLmxlZnR9PC9iPjwvZGl2PgogICAgICAke3Iud2luZG93P2A8"
    "ZGl2IGNsYXNzPSJrdiI+PHNwYW4+Rm9yd2FyZGluZyBlbmRzIGluPC9zcGFuPjxiPiR7ci53aW5k"
    "b3d9IG1pbjwvYj48L2Rpdj5gOiIifQogICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJt"
    "YXJnaW4tdG9wOjEwcHgiPgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLWFj"
    "dD0iY29weWNvZGUiPkNvcHkgQ29kZTwvYnV0dG9uPgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biIgZGF0YS1jbG9zZT5Eb25lPC9idXR0b24+PC9kaXY+YCk7CiAgfSk7CiAgZG9jdW1lbnQucXVl"
    "cnlTZWxlY3RvckFsbCgiW2RhdGEtY2xhaW1dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMo"
    "KT0+ewogICAgaWYoYi5kaXNhYmxlZClyZXR1cm47CiAgICBiLmRpc2FibGVkPXRydWU7CiAgICBp"
    "ZihMSVZFKXsKICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvY2hlY2tpbiIse30pOwog"
    "ICAgICBpZighcil7Yi5kaXNhYmxlZD1mYWxzZTtyZXR1cm47fQogICAgICBCQUw9dHlwZW9mIHIu"
    "YmFsYW5jZT09PSJudW1iZXIiP3IuYmFsYW5jZTpCQUw7CiAgICAgIEVBUk4uY2xhaW1lZD10cnVl"
    "O0VBUk4uc3RyZWFrPXIuc3RyZWFrfHxFQVJOLnN0cmVhazsKICAgICAgc3luY0JhbCgpO2J1cnN0"
    "KCk7dG9hc3QobW9uZXkoci5hbW91bnQpKyIgYWRkZWQgwrcgZGF5ICIrci5zdHJlYWspOwogICAg"
    "ICBiLnRleHRDb250ZW50PSJDbGFpbWVkIHRvZGF5IjsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwog"
    "ICAgICByZXR1cm47CiAgICB9CiAgICBCQUwrPUVBUk4uZGFpbHk7c3luY0JhbCgpO2J1cnN0KCk7"
    "dG9hc3QobW9uZXkoRUFSTi5kYWlseSkrIiBhZGRlZCIpOwogICAgYi50ZXh0Q29udGVudD0iQ2xh"
    "aW1lZCB0b2RheSI7fSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtYW10XSIp"
    "LmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT5kZXBvc2l0U2hlZXQoYi5kYXRhc2V0LmFtdCkpOwog"
    "IGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWRlcF0iKS5mb3JFYWNoKGI9PmIub25j"
    "bGljaz0oKT0+YW1vdW50U2hlZXQoKSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2Rh"
    "dGEtdGddIikuZm9yRWFjaCh0PT50Lm9uY2xpY2s9KCk9PnQuY2xhc3NMaXN0LnRvZ2dsZSgib24i"
    "KSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtc2V0c2VsXSIpLmZvckVhY2go"
    "c2VsPT5zZWwub25jaGFuZ2U9YXN5bmMoKT0+ewogICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3"
    "IG9ubHkiKTtyZXR1cm47fQogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc2V0"
    "dGluZyIse2tleTpzZWwuZGF0YXNldC5zZXRzZWwsdmFsdWU6c2VsLnZhbHVlfSk7CiAgICBpZihy"
    "KXRvYXN0KCJTYXZlZCIpOwogIH0pOwogIGNvbnN0IHE9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "InEiKTsKICBpZihxKXtxLnZhbHVlPVFVRVJZO3Eub25pbnB1dD0oKT0+e1FVRVJZPXEudmFsdWUu"
    "dG9Mb3dlckNhc2UoKTthcHBseUZpbHRlcigpfTt9CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiI2NoaXBzIGJ1dHRvbiIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57Q0hJUD1iLmRhdGFz"
    "ZXQuZmlsdGVyOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI2NoaXBzIGJ1dHRvbiIp"
    "LmZvckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseD09PWIpKTthcHBseUZpbHRlcigp"
    "fSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2xhaW0tb3JkZXJdIikuZm9y"
    "RWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IG9pZD1iLmRhdGFzZXQuY2xhaW1PcmRl"
    "cjsKICAgIGNvbnN0IG9yZD1PUkRFUlMuZmluZCh4PT54LmlkPT09b2lkKXx8e307CiAgICBpZihv"
    "cmQuY2xhaW0pe3RvYXN0KCJBIGNsYWltIGZvciB0aGlzIG9yZGVyIGlzIGFscmVhZHkgd2l0aCBh"
    "biBhZG1pbiIpO3JldHVybjt9CiAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtm"
    "b250LXNpemU6MXJlbSI+UmVwb3J0IGEgcHJvYmxlbTwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIi"
    "PkFuIGFkbWluIHJlYWRzIGV2ZXJ5IHJlcG9ydCBhbmQgZGVjaWRlcy4gTm90aGluZyBpcyByZWZ1"
    "bmRlZCBhdXRvbWF0aWNhbGx5LjwvcD4KICAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJt"
    "YXJnaW4tdG9wOjEycHgiPjxsYWJlbD5XaGF0IGhhcHBlbmVkPzwvbGFiZWw+CiAgICAgICAgPHNl"
    "bGVjdCBjbGFzcz0iaW5wIiBpZD0iY2xyIj4KICAgICAgICAgIDxvcHRpb24+TG9naW4gbm90IHdv"
    "cmtpbmc8L29wdGlvbj48b3B0aW9uPkFjY291bnQgYmFubmVkPC9vcHRpb24+CiAgICAgICAgICA8"
    "b3B0aW9uPkNvZGUgbmV2ZXIgYXJyaXZlZDwvb3B0aW9uPjxvcHRpb24+V3JvbmcgY291bnRyeTwv"
    "b3B0aW9uPgogICAgICAgICAgPG9wdGlvbj5Tb21ldGhpbmcgZWxzZTwvb3B0aW9uPjwvc2VsZWN0"
    "PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkRldGFpbHM8L2xhYmVsPgog"
    "ICAgICAgIDx0ZXh0YXJlYSBjbGFzcz0iaW5wIiBpZD0iY2xkIiByb3dzPSIzIiBwbGFjZWhvbGRl"
    "cj0iVGVsbCB1cyBleGFjdGx5IHdoYXQgeW91IHNhdyI+PC90ZXh0YXJlYT48L2Rpdj4KICAgICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBibG9jayIgaWQ9ImNsZ28iPlNlbmQgdG8gYW4gYWRtaW48"
    "L2J1dHRvbj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJsb2NrIHNtIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+Q2FuY2VsPC9idXR0b24+YCk7CiAgICBkb2N1bWVu"
    "dC5nZXRFbGVtZW50QnlJZCgiY2xnbyIpLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25zdCBy"
    "ZWFzb249ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNsciIpLnZhbHVlOwogICAgICBjb25zdCBk"
    "ZXRhaWxzPShkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2xkIikudmFsdWV8fCIiKS50cmltKCk7"
    "CiAgICAgIGlmKGRldGFpbHMubGVuZ3RoPDUpe3RvYXN0KCJUZWxsIHVzIHdoYXQgd2VudCB3cm9u"
    "ZyIpO3JldHVybjt9CiAgICAgIGlmKCFMSVZFKXtjbG9zZVNoZWV0KCk7dG9hc3QoIlNlbnQiKTty"
    "ZXR1cm47fQogICAgICBjb25zdCBidG49ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNsZ28iKTti"
    "dG4uZGlzYWJsZWQ9dHJ1ZTtidG4udGV4dENvbnRlbnQ9IlNlbmRpbmfigKYiOwogICAgICBjb25z"
    "dCByPWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRlciIse2lkOm9pZCx3aGF0OiJjbGFpbSIscmVhc29u"
    "OnJlYXNvbixkZXRhaWxzOmRldGFpbHN9KTsKICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50"
    "ZXh0Q29udGVudD0iU2VuZCB0byBhbiBhZG1pbiI7CiAgICAgIGlmKCFyKXJldHVybjsKICAgICAg"
    "Y2xvc2VTaGVldCgpO3RvYXN0KCJDbGFpbSAjIityLmlkKyIgc2VudCDigJQgYW4gYWRtaW4gd2ls"
    "bCByZXBseSIpOwogICAgICBhd2FpdCByZWZyZXNoKCk7CiAgICB9OwogIH0pOwogIGRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWxvZ2dlZGluXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNr"
    "PWFzeW5jKCk9PnsKICAgIGNvbnN0IG9pZD1iLmRhdGFzZXQubG9nZ2VkaW47CiAgICBpZighTElW"
    "RSl7dG9hc3QoIk1hcmtlZCBhcyBsb2dnZWQgaW4iKTtyZXR1cm47fQogICAgc2hlZXQoYDxoMyBz"
    "dHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkNvbmZpcm0geW91IGFyZSBpbjwv"
    "aDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiPldlIHdpbGwgZGVsZXRlIG91ciBjb3B5IG9mIHRoZSBs"
    "b2dpbiBzZXNzaW9uIHN0cmFpZ2h0IGF3YXksIHNvIHRoZQogICAgICBhY2NvdW50IGJlY29tZXMg"
    "eW91cnMgYWxvbmUuIENvZGVzIHdpbGwgc3RvcCBiZWluZyBmb3J3YXJkZWQuPC9wPgogICAgICA8"
    "YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBpZD0ibGlvayIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTJweCI+WWVzLCBJIGFtIGxvZ2dlZCBpbjwvYnV0dG9uPgogICAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4gZ2hvc3QgYmxvY2sgc20iIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgZGF0YS1jbG9zZT5Ob3Qg"
    "eWV0PC9idXR0b24+YCk7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgibGlvayIpLm9uY2xp"
    "Y2s9YXN5bmMoKT0+ewogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRlciIse2lk"
    "Om9pZCx3aGF0OiJsb2dnZWRpbiJ9KTsKICAgICAgaWYoIXIpcmV0dXJuOwogICAgICBjbG9zZVNo"
    "ZWV0KCk7dG9hc3QoIkRvbmUg4oCUIHRoZSBhY2NvdW50IGlzIHlvdXJzIGFsb25lIG5vdyIpOwog"
    "ICAgICBhd2FpdCByZWZyZXNoKCk7CiAgICB9OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIltkYXRhLWRpc3B1dGVdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNv"
    "bnN0IG9yZD1iLmRhdGFzZXQuZGlzcHV0ZXx8IiI7CiAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJn"
    "aW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+T3BlbiBhIGRpc3B1dGU8L2gzPgogICAgIDxwIGNs"
    "YXNzPSJzdWIiPiR7b3JkPygiT3JkZXIgIitlc2Mob3JkKSk6IkFuIGFkbWluIHJldmlld3MgZXZl"
    "cnkgZGlzcHV0ZS4ifTwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10"
    "b3A6MTJweCI+PGxhYmVsPlJlYXNvbjwvbGFiZWw+CiAgICAgICA8c2VsZWN0IGNsYXNzPSJpbnAi"
    "IGlkPSJkc3IiPjxvcHRpb24+Tm90IGRlbGl2ZXJlZDwvb3B0aW9uPjxvcHRpb24+QWNjb3VudCBk"
    "ZWFkIG9uIGFycml2YWw8L29wdGlvbj48b3B0aW9uPkNoYXJnZWQgdHdpY2U8L29wdGlvbj48b3B0"
    "aW9uPk90aGVyPC9vcHRpb24+PC9zZWxlY3Q+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "PjxsYWJlbD5XaGF0IGhhcHBlbmVkPzwvbGFiZWw+CiAgICAgICA8dGV4dGFyZWEgY2xhc3M9Imlu"
    "cCIgaWQ9ImRzZCIgcm93cz0iMyIgcGxhY2Vob2xkZXI9IkRlc2NyaWJlIHRoZSBwcm9ibGVtIj48"
    "L3RleHRhcmVhPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RXZpZGVuY2Ug"
    "KG9wdGlvbmFsKTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImRzZiIgdHlw"
    "ZT0iZmlsZSIgYWNjZXB0PSJpbWFnZS8qIj4KICAgICAgIDxzbWFsbCBjbGFzcz0ic3ViIiBpZD0i"
    "ZHNmbiI+PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciPjxidXR0b24gY2xh"
    "c3M9ImJ0biBnaG9zdCIgZGF0YS1jbG9zZT5DYW5jZWw8L2J1dHRvbj4KICAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBjdGEiIGlkPSJkc2dvIj5TdWJtaXQ8L2J1dHRvbj48L2Rpdj5gKTsKICAgIGNv"
    "bnN0IGY9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRzZiIpOwogICAgaWYoZilmLm9uY2hhbmdl"
    "PSgpPT57Y29uc3Qgbj0oZi5maWxlcyYmZi5maWxlc1swXSk/Zi5maWxlc1swXS5uYW1lOiIiOwog"
    "ICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZHNmbiIpLnRleHRDb250ZW50PW4/KCJBdHRh"
    "Y2hlZDogIituKToiIjt9OwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRzZ28iKS5vbmNs"
    "aWNrPWFzeW5jKCk9PnsKICAgICAgY29uc3QgcmVhc29uPWRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJkc3IiKS52YWx1ZTsKICAgICAgY29uc3QgZGV0YWlscz0oZG9jdW1lbnQuZ2V0RWxlbWVudEJ5"
    "SWQoImRzZCIpLnZhbHVlfHwiIikudHJpbSgpOwogICAgICBpZighZGV0YWlscyl7dG9hc3QoIkRl"
    "c2NyaWJlIHdoYXQgaGFwcGVuZWQiKTtyZXR1cm47fQogICAgICBjb25zdCBmaWxlPShmJiZmLmZp"
    "bGVzJiZmLmZpbGVzWzBdKXx8bnVsbDsKICAgICAgY29uc3QgZm49ZmlsZT8oInNjcmVlbnNob3Q6"
    "ICIrZmlsZS5uYW1lKToiIjsKICAgICAgaWYoIUxJVkUpe2Nsb3NlU2hlZXQoKTt0b2FzdCgiRGlz"
    "cHV0ZSBvcGVuZWQiKTtyZXR1cm47fQogICAgICBjb25zdCBidG49ZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoImRzZ28iKTtidG4uZGlzYWJsZWQ9dHJ1ZTtidG4udGV4dENvbnRlbnQ9IlNlbmRpbmfi"
    "gKYiOwogICAgICBsZXQgc2hvdD0iIjsKICAgICAgaWYoZmlsZSl7CiAgICAgICAgaWYoZmlsZS5z"
    "aXplPjgqMTAyNCoxMDI0KXt0b2FzdCgiVGhhdCBpbWFnZSBpcyB0b28gbGFyZ2UgKDggTUIgbWF4"
    "KSIpOwogICAgICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0iU3VibWl0"
    "IjtyZXR1cm47fQogICAgICAgIHNob3Q9YXdhaXQgbmV3IFByb21pc2UocmVzPT57CiAgICAgICAg"
    "ICBjb25zdCByZD1uZXcgRmlsZVJlYWRlcigpOwogICAgICAgICAgcmQub25sb2FkPSgpPT5yZXMo"
    "U3RyaW5nKHJkLnJlc3VsdHx8IiIpKTsKICAgICAgICAgIHJkLm9uZXJyb3I9KCk9PnJlcygiIik7"
    "CiAgICAgICAgICByZC5yZWFkQXNEYXRhVVJMKGZpbGUpOwogICAgICAgIH0pOwogICAgICB9CiAg"
    "ICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2Rpc3B1dGUiLHtvcmRlcjpvcmQscmVhc29u"
    "OnJlYXNvbixkZXRhaWxzOmRldGFpbHMsCiAgICAgICAgZXZpZGVuY2U6Zm4sc2hvdDpzaG90fSk7"
    "CiAgICAgIGJ0bi5kaXNhYmxlZD1mYWxzZTtidG4udGV4dENvbnRlbnQ9IlN1Ym1pdCI7CiAgICAg"
    "IGlmKCFyKXJldHVybjsKICAgICAgY2xvc2VTaGVldCgpO3RvYXN0KCJEaXNwdXRlICMiK3IuaWQr"
    "IiBzZW50IHRvIHRoZSBhZG1pbnMiKTsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgfTsKICB9"
    "KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1yYXRlXSIpLmZvckVhY2goYj0+"
    "Yi5vbmNsaWNrPSgpPT57d2luZG93Ll9fcmF0ZUNvdW50cnk9Yi5kYXRhc2V0LnJhdGU7cmV0dXJu"
    "IHNoZWV0KAogICAgYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPlJh"
    "dGUgJHtiLmRhdGFzZXQucmF0ZX08L2gzPgogICAgIDxwIGNsYXNzPSJzdWIiPllvdXIgcmF0aW5n"
    "IHNob3dzIG9uIHRoZSBjb3VudHJ5IGluIHRoZSBzaG9wLjwvcD4KICAgICA8ZGl2IHN0eWxlPSJk"
    "aXNwbGF5OmZsZXg7Z2FwOjlweDtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO21hcmdpbjoxNnB4IDAi"
    "PgogICAgICAgJHtbMSwyLDMsNCw1XS5tYXAobj0+YDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0"
    "IiBkYXRhLXJhdGUtZ289IiR7bn0iPiR7bn3imIU8L2J1dHRvbj5gKS5qb2luKCIiKX08L2Rpdj4K"
    "ICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QgYmxvY2sgc20iIGRhdGEtY2xvc2U+Q2xvc2U8"
    "L2J1dHRvbj5gKTt9KTsgCiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY3J5cHRv"
    "XSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT5zaGVldCgKICAgIGA8aDMgc3R5bGU9Im1hcmdp"
    "bjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5DcnlwdG8gdG9wLXVwPC9oMz4KICAgICA8cCBjbGFz"
    "cz0ic3ViIj5TZW5kIFVTRFQtVFJDMjAg4oCUIGNyZWRpdGVkIGFmdGVyIDEgbmV0d29yayBjb25m"
    "aXJtYXRpb24uPC9wPgogICAgIDxkaXYgY2xhc3M9InFyIj5RUiBDT0RFPGJyPih3YWxsZXQgYWRk"
    "cmVzcyk8L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+TmV0d29yazwvc3Bhbj48Yj5U"
    "UkMtMjA8L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkFkZHJlc3M8L3NwYW4+"
    "PGIgc3R5bGU9ImZvbnQtc2l6ZTouN3JlbSI+bm90LWNvbmZpZ3VyZWQ8L2I+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPlJhdGU8L3NwYW4+PGI+MSBVU0RUID0g4oK5ODguNDA8L2I+"
    "PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48"
    "YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj4KICAgICAg"
    "IDxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGRhdGEtYWN0PSJjcnlwdG8iPkkndmUgU2VudCBJdDwv"
    "YnV0dG9uPjwvZGl2PmApKTsKICBjb25zdCB0Zz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgidG91"
    "cmdvIiksdHM9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInRvdXJza2lwIik7CiAgaWYodGcpdGcu"
    "b25jbGljaz0oKT0+c2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFy"
    "ZW0iPkhvdyBpdCB3b3JrczwvaDM+CiAgICAgPGRpdiBjbGFzcz0ic3RlcHMiIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEycHgiPgogICAgICAgPGRpdiBjbGFzcz0ic3RlcCBvbiI+PGk+MTwvaT5Ub3AgdXAg"
    "eW91ciB3YWxsZXQgYnkgVVBJPC9kaXY+CiAgICAgICA8ZGl2IGNsYXNzPSJzdGVwIG9uIj48aT4y"
    "PC9pPlBpY2sgYSBjb3VudHJ5IGFuZCBidXkgaW5zdGFudGx5PC9kaXY+CiAgICAgICA8ZGl2IGNs"
    "YXNzPSJzdGVwIG9uIj48aT4zPC9pPlJlYWQgdGhlIGxvZ2luIGNvZGUgcmlnaHQgaGVyZSBpbiBP"
    "cmRlcnM8L2Rpdj48L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMnB4IiBkYXRhLWNsb3NlPkdvdCBpdDwvYnV0dG9uPmApOwogIGlmKHRz"
    "KXRzLm9uY2xpY2s9KCk9PntGRUFULm9uYm9hcmRpbmc9ZmFsc2U7cmVuZGVyKCk7dG9hc3QoIlRv"
    "dXIgaGlkZGVuIik7fTsKICBzdGFydENvdW50ZG93bigpOwogIGJpbmRIb2xkKCk7Cn0KZnVuY3Rp"
    "b24gYXBwbHlGaWx0ZXIoKXsKICBGSUxURVJFRD1TVE9DSy5maWx0ZXIocz0+ewogICAgY29uc3Qg"
    "dD0ocy5jKyIgIitzLmNvZGUpLnRvTG93ZXJDYXNlKCk7CiAgICBpZihRVUVSWSYmIXQuaW5jbHVk"
    "ZXMoUVVFUlkpKXJldHVybiBmYWxzZTsKICAgIGlmKENISVA9PT0ic3RvY2siJiZzLnF0eT09PTAp"
    "cmV0dXJuIGZhbHNlOwogICAgaWYoQ0hJUD09PSJkZWFsIiYmIXMud2FzKXJldHVybiBmYWxzZTsK"
    "ICAgIGlmKENISVA9PT0iY2hlYXAiJiZzLnByaWNlPj0zNSlyZXR1cm4gZmFsc2U7CiAgICBpZihD"
    "SElQPT09InYyZmEiJiZzLnF0eT09PTApcmV0dXJuIGZhbHNlOwogICAgaWYoQ0hJUD09PSJ2bWFp"
    "bCImJiFzLmhvdClyZXR1cm4gZmFsc2U7CiAgICBpZihDSElQPT09InZzcGFtIiYmcy5wcmljZTwz"
    "MClyZXR1cm4gZmFsc2U7CiAgICByZXR1cm4gdHJ1ZTt9KTsKICBjb25zdCBiPWRvY3VtZW50Lmdl"
    "dEVsZW1lbnRCeUlkKCJzaG9wYm9keSIpOwogIGlmKGIpe2IuaW5uZXJIVE1MPXNob3BCb2R5KCk7"
    "YmluZEJ1eSgpO30KfQpsZXQgY2RUPW51bGw7CmZ1bmN0aW9uIHN0YXJ0Q291bnRkb3duKCl7CiAg"
    "Y29uc3QgZWw9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNkIik7Y2xlYXJJbnRlcnZhbChjZFQp"
    "O2lmKCFlbClyZXR1cm47CiAgbGV0IHQ9MTQqNjArNTI7CiAgY2RUPXNldEludGVydmFsKCgpPT57"
    "aWYoIWRvY3VtZW50LmJvZHkuY29udGFpbnMoZWwpKXtjbGVhckludGVydmFsKGNkVCk7cmV0dXJu"
    "fQogICAgdD10PjA/dC0xOjE0KjYwKzUyO2VsLnRleHRDb250ZW50PVN0cmluZyhNYXRoLmZsb29y"
    "KHQvNjApKS5wYWRTdGFydCgyLCIwIikrIjoiK1N0cmluZyh0JTYwKS5wYWRTdGFydCgyLCIwIil9"
    "LDEwMDApOwp9Ci8qIDA1IMK3IGhvbGQtdG8tY29uZmlybSAqLwpmdW5jdGlvbiBiaW5kSG9sZCgp"
    "ewogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5ob2xkIikuZm9yRWFjaChiPT57CiAgICBp"
    "ZihiLl9oKXJldHVybjtiLl9oPTE7Y29uc3QgZmlsbD1iLnF1ZXJ5U2VsZWN0b3IoIi5maWxsIik7"
    "bGV0IGl2PW51bGwscD0wOwogICAgY29uc3Qgc3RvcD0oKT0+e2NsZWFySW50ZXJ2YWwoaXYpO3A9"
    "MDtpZihmaWxsKWZpbGwuc3R5bGUud2lkdGg9IjAlIn07CiAgICBjb25zdCBnbz1lPT57ZS5wcmV2"
    "ZW50RGVmYXVsdCgpO2NsZWFySW50ZXJ2YWwoaXYpO2l2PXNldEludGVydmFsKCgpPT57cCs9NDtp"
    "ZihmaWxsKWZpbGwuc3R5bGUud2lkdGg9cCsiJSI7CiAgICAgIGlmKHA+PTEwMCl7c3RvcCgpO2Iu"
    "Y2xhc3NMaXN0LmFkZCgiZG9uZSIpO3NldFRpbWVvdXQoKCk9PmIuY2xhc3NMaXN0LnJlbW92ZSgi"
    "ZG9uZSIpLDQ1MCk7YnVyc3QoKTt0b2FzdChiLmRhdGFzZXQuZG9uZXx8IkNvbmZpcm1lZCIpfX0s"
    "MjgpfTsKICAgIGIuYWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcmRvd24iLGdvKTtbInBvaW50ZXJ1"
    "cCIsInBvaW50ZXJsZWF2ZSIsInBvaW50ZXJjYW5jZWwiXS5mb3JFYWNoKGV2PT5iLmFkZEV2ZW50"
    "TGlzdGVuZXIoZXYsc3RvcCkpO30pOwp9Ci8qIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkCBLSVQgUlVOVElNRSDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAgKi8KY29u"
    "c3QgUk09bWF0Y2hNZWRpYSgiKHByZWZlcnMtcmVkdWNlZC1tb3Rpb246cmVkdWNlKSIpLm1hdGNo"
    "ZXM7Ci8qIHNwcmluZyBpbnRlZ3JhdG9yIOKAlCBraXQgdXNlcyBtYXNzIC4xIC8gc3RpZmZuZXNz"
    "IDE1MCAvIGRhbXBpbmcgMTIgKi8KZnVuY3Rpb24gU3ByaW5nKHYse209LjEsaz0xNTAsZD0xMn09"
    "e30pe3RoaXMudj12O3RoaXMudD12O3RoaXMudmVsPTA7dGhpcy5tPW07dGhpcy5rPWs7dGhpcy5k"
    "PWQ7fQpTcHJpbmcucHJvdG90eXBlLnN0ZXA9ZnVuY3Rpb24oZHQpe2NvbnN0IEY9LXRoaXMuayoo"
    "dGhpcy52LXRoaXMudCktdGhpcy5kKnRoaXMudmVsOwogIHRoaXMudmVsKz0oRi90aGlzLm0pKmR0"
    "O3RoaXMudis9dGhpcy52ZWwqZHQ7cmV0dXJuIHRoaXMudjt9OwoKLyogMDIgKyAyNSDCtyBhbmlt"
    "YXRlZCBkb2NrOiBkaXN0YW5jZeKGknNjYWxlIHdpdGggc3ByaW5nLCBwbHVzIHNsaWRpbmcgcGls"
    "bCAqLwooZnVuY3Rpb24gZG9jaygpewogIGNvbnN0IGRrPWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3Io"
    "Im5hdiN0YWJzIC5kb2NrIik7aWYoIWRrKXJldHVybjsKICBjb25zdCBidG5zPVsuLi5kay5xdWVy"
    "eVNlbGVjdG9yQWxsKCJidXR0b24iKV0scGlsbD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZHBp"
    "bGwiKTsKICBjb25zdCBGSU5FPW1hdGNoTWVkaWEoIihob3Zlcjpob3ZlcikgYW5kIChwb2ludGVy"
    "OmZpbmUpIikubWF0Y2hlcyYmIVJNOwogIGNvbnN0IHNwPWJ0bnMubWFwKCgpPT5uZXcgU3ByaW5n"
    "KDEpKSxwcz17eDpuZXcgU3ByaW5nKDAse2s6MjYwLGQ6MTF9KSx3Om5ldyBTcHJpbmcoMCx7azoy"
    "NjAsZDoxMX0pfTsKICBsZXQgbXg9SW5maW5pdHksbGFzdD1wZXJmb3JtYW5jZS5ub3coKSxwbGFj"
    "ZWQ9ZmFsc2U7CiAgZnVuY3Rpb24gcmVzZXRTY2FsZSgpe214PUluZmluaXR5O2lmKCFGSU5FKWJ0"
    "bnMuZm9yRWFjaChiPT5iLnN0eWxlLnRyYW5zZm9ybT0iIik7fQogIGlmKEZJTkUpewogICAgZGsu"
    "YWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcm1vdmUiLGU9PntpZihlLnBvaW50ZXJUeXBlPT09Im1v"
    "dXNlIilteD1lLmNsaWVudFh9KTsKICAgIGRrLmFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJsZWF2"
    "ZSIsKCk9Pm14PUluZmluaXR5KTsKICAgIFsicG9pbnRlcnVwIiwicG9pbnRlcmNhbmNlbCIsInRv"
    "dWNoZW5kIiwidG91Y2hjYW5jZWwiLCJibHVyIl0uZm9yRWFjaChldj0+CiAgICAgIGFkZEV2ZW50"
    "TGlzdGVuZXIoZXYscmVzZXRTY2FsZSx7cGFzc2l2ZTp0cnVlfSkpOwogIH1lbHNle2J0bnMuZm9y"
    "RWFjaChiPT5iLnN0eWxlLnRyYW5zZm9ybT0iIik7fQogIGRvY3VtZW50LmFkZEV2ZW50TGlzdGVu"
    "ZXIoInZpc2liaWxpdHljaGFuZ2UiLHJlc2V0U2NhbGUpOwoKICBmdW5jdGlvbiBwbGFjZShzbmFw"
    "KXsKICAgIGNvbnN0IG9uPWRrLnF1ZXJ5U2VsZWN0b3IoImJ1dHRvbi5vbiIpOwogICAgaWYoIW9u"
    "fHwhcGlsbHx8IW9uLm9mZnNldFdpZHRofHwhZGsub2Zmc2V0V2lkdGgpcmV0dXJuOyAgIC8vIG5l"
    "dmVyIHBsYWNlIHdoaWxlIGhpZGRlbgogICAgcHMueC50PW9uLm9mZnNldExlZnQtZGsuY2xpZW50"
    "TGVmdDtwcy53LnQ9b24ub2Zmc2V0V2lkdGg7CiAgICBwaWxsLnN0eWxlLnRvcD0ob24ub2Zmc2V0"
    "VG9wLWRrLmNsaWVudFRvcCkrInB4IjtwaWxsLnN0eWxlLmhlaWdodD1vbi5vZmZzZXRIZWlnaHQr"
    "InB4IjsKICAgIGlmKHNuYXB8fCFwbGFjZWQpe3BsYWNlZD10cnVlO3BzLngudj1wcy54LnQ7cHMu"
    "eC52ZWw9MDtwcy53LnY9cHMudy50O3BzLncudmVsPTA7CiAgICAgIHBpbGwuc3R5bGUudHJhbnNm"
    "b3JtPSJ0cmFuc2xhdGVYKCIrcHMueC52KyJweCkiO3BpbGwuc3R5bGUud2lkdGg9cHMudy52KyJw"
    "eCI7CiAgICAgIHBpbGwuY2xhc3NMaXN0LmFkZCgicmVhZHkiKTt9CiAgfQogIG5ldyBSZXNpemVP"
    "YnNlcnZlcigoKT0+cGxhY2UoIXBsYWNlZCkpLm9ic2VydmUoZGspOwogIGFkZEV2ZW50TGlzdGVu"
    "ZXIoInJlc2l6ZSIsKCk9PnBsYWNlKHRydWUpKTsKICBhZGRFdmVudExpc3RlbmVyKCJsb2FkIiwo"
    "KT0+cGxhY2UoIXBsYWNlZCkpOwogIHNldFRpbWVvdXQoKCk9PnBsYWNlKCFwbGFjZWQpLDYwKTtz"
    "ZXRUaW1lb3V0KCgpPT5wbGFjZSghcGxhY2VkKSw0MDApOwoKICAoZnVuY3Rpb24gbG9vcCh0KXsK"
    "ICAgIGNvbnN0IGR0PU1hdGgubWluKC4wMzIsKHQtbGFzdCkvMTAwMCk7bGFzdD10OwogICAgaWYo"
    "RklORSl7YnRucy5mb3JFYWNoKChiLGkpPT57bGV0IGs9MDsKICAgICAgaWYobXghPT1JbmZpbml0"
    "eSl7Y29uc3Qgcj1iLmdldEJvdW5kaW5nQ2xpZW50UmVjdCgpOwogICAgICAgIGNvbnN0IGRpc3Q9"
    "TWF0aC5hYnMoci5sZWZ0K3Iud2lkdGgvMi1teCk7az1NYXRoLm1heCgwLDEtZGlzdC8xNTApO30K"
    "ICAgICAgc3BbaV0udD0xKy4zNCprKms7Y29uc3Qgdj1zcFtpXS5zdGVwKGR0KTsKICAgICAgYi5z"
    "dHlsZS50cmFuc2Zvcm09KHY+MS4wMDJ8fHNwW2ldLnZlbD4uMDF8fHNwW2ldLnZlbDwtLjAxKQog"
    "ICAgICAgID8gInRyYW5zbGF0ZVkoIisoLTgqKHYtMSkpLnRvRml4ZWQoMikrInB4KSBzY2FsZSgi"
    "K3YudG9GaXhlZCgzKSsiKSIgOiAiIjt9KTt9CiAgICBpZihwaWxsJiZwbGFjZWQpe3BpbGwuc3R5"
    "bGUudHJhbnNmb3JtPSJ0cmFuc2xhdGVYKCIrcHMueC5zdGVwKGR0KS50b0ZpeGVkKDIpKyJweCki"
    "OwogICAgICBwaWxsLnN0eWxlLndpZHRoPXBzLncuc3RlcChkdCkudG9GaXhlZCgyKSsicHgiO30K"
    "ICAgIHJlcXVlc3RBbmltYXRpb25GcmFtZShsb29wKTt9KShsYXN0KTsKICB3aW5kb3cuX19kb2Nr"
    "UGxhY2U9cGxhY2U7cGxhY2UodHJ1ZSk7Cn0pKCk7CgovKiAxNCDCtyB2LXRhYnMgc2xpZGluZyBp"
    "bmRpY2F0b3IgZm9yIHRoZSBhZG1pbiBzdWJuYXYgKi8KKGZ1bmN0aW9uIHZ0YWJzKCl7CiAgY29u"
    "c3QgbmF2PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJhc3ViIikscGlsbD1kb2N1bWVudC5nZXRF"
    "bGVtZW50QnlJZCgidnBpbGwiKTtpZighbmF2fHwhcGlsbClyZXR1cm47CiAgY29uc3QgcHg9bmV3"
    "IFNwcmluZygwLHtrOjIxMCxkOjE5fSkscHc9bmV3IFNwcmluZygwLHtrOjIxMCxkOjE5fSk7bGV0"
    "IGxhc3Q9cGVyZm9ybWFuY2Uubm93KCksaW5pdD1mYWxzZTsKICBmdW5jdGlvbiBwbGFjZShzbmFw"
    "KXtjb25zdCBvbj1uYXYucXVlcnlTZWxlY3RvcigiYnV0dG9uLm9uIik7CiAgICBpZighb258fCFv"
    "bi5vZmZzZXRXaWR0aHx8IW5hdi5vZmZzZXRXaWR0aClyZXR1cm47CiAgICBweC50PW9uLm9mZnNl"
    "dExlZnQtbmF2LmNsaWVudExlZnQ7cHcudD1vbi5vZmZzZXRXaWR0aDsKICAgIHBpbGwuc3R5bGUu"
    "dG9wPShvbi5vZmZzZXRUb3AtbmF2LmNsaWVudFRvcCkrInB4IjtwaWxsLnN0eWxlLmhlaWdodD1v"
    "bi5vZmZzZXRIZWlnaHQrInB4IjsKICAgIGlmKHNuYXB8fCFpbml0KXtpbml0PXRydWU7cHgudj1w"
    "eC50O3B4LnZlbD0wO3B3LnY9cHcudDtwdy52ZWw9MDsKICAgICAgcGlsbC5zdHlsZS50cmFuc2Zv"
    "cm09InRyYW5zbGF0ZVgoIitweC52KyJweCkiO3BpbGwuc3R5bGUud2lkdGg9cHcudisicHgiO319"
    "CiAgbmF2LmFkZEV2ZW50TGlzdGVuZXIoImNsaWNrIiwoKT0+cmVxdWVzdEFuaW1hdGlvbkZyYW1l"
    "KCgpPT5wbGFjZShmYWxzZSkpKTsKICBuYXYuYWRkRXZlbnRMaXN0ZW5lcigic2Nyb2xsIiwoKT0+"
    "cGxhY2UoZmFsc2UpKTsKICBuZXcgUmVzaXplT2JzZXJ2ZXIoKCk9PnBsYWNlKCFpbml0KSkub2Jz"
    "ZXJ2ZShuYXYpOwogIGFkZEV2ZW50TGlzdGVuZXIoImxvYWQiLCgpPT5wbGFjZSghaW5pdCkpO3Nl"
    "dFRpbWVvdXQoKCk9PnBsYWNlKCFpbml0KSw4MCk7cGxhY2UodHJ1ZSk7CiAgKGZ1bmN0aW9uIGxv"
    "b3AodCl7Y29uc3QgZHQ9TWF0aC5taW4oLjAzMiwodC1sYXN0KS8xMDAwKTtsYXN0PXQ7CiAgICBp"
    "Zihpbml0KXtwaWxsLnN0eWxlLnRyYW5zZm9ybT0idHJhbnNsYXRlWCgiK3B4LnN0ZXAoZHQpLnRv"
    "Rml4ZWQoMikrInB4KSI7cGlsbC5zdHlsZS53aWR0aD1wdy5zdGVwKGR0KS50b0ZpeGVkKDIpKyJw"
    "eCI7fQogICAgcmVxdWVzdEFuaW1hdGlvbkZyYW1lKGxvb3ApO30pKGxhc3QpOwogIHdpbmRvdy5f"
    "X3Z0YWJQbGFjZT1wbGFjZTsKfSkoKTsKCi8qIDA4IMK3IGtpbmV0aWMgZ3JpZCDigJQgcG9pbnRl"
    "ciB3YXJwICsgY2xpY2sgcmlwcGxlcyAoY2FudmFzIHBvcnQpICovCihmdW5jdGlvbiBraW5ldGlj"
    "KCl7CiAgY29uc3QgY3Y9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImtnIik7aWYoIWN2fHxSTSly"
    "ZXR1cm47Y29uc3QgY3R4PWN2LmdldENvbnRleHQoIjJkIik7CiAgY29uc3QgU1A9MzAsSU5GPTIx"
    "MCxXQVJQPTIyLExFUlA9LjA5LFJHQj0iMTcsMTcsMTciOwogIGxldCBXPTAsSD0wLG1vdXNlPXt4"
    "Oi05OTk5LHk6LTk5OTl9LHRndD17eDotOTk5OSx5Oi05OTk5fSxyaXA9W10scmFmPTA7CiAgZnVu"
    "Y3Rpb24gc2l6ZSgpe2NvbnN0IGQ9TWF0aC5taW4oMS42LGRldmljZVBpeGVsUmF0aW98fDEpO1c9"
    "aW5uZXJXaWR0aDtIPWlubmVySGVpZ2h0OwogICAgY3Yud2lkdGg9VypkO2N2LmhlaWdodD1IKmQ7"
    "Y3Yuc3R5bGUud2lkdGg9VysicHgiO2N2LnN0eWxlLmhlaWdodD1IKyJweCI7Y3R4LnNldFRyYW5z"
    "Zm9ybShkLDAsMCxkLDAsMCk7fQogIGFkZEV2ZW50TGlzdGVuZXIoInJlc2l6ZSIsc2l6ZSk7c2l6"
    "ZSgpOwogIGFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJtb3ZlIixlPT57dGd0PXt4OmUuY2xpZW50"
    "WCx5OmUuY2xpZW50WX19LHtwYXNzaXZlOnRydWV9KTsKICBhZGRFdmVudExpc3RlbmVyKCJwb2lu"
    "dGVyZG93biIsZT0+e2lmKHJpcC5sZW5ndGg8NClyaXAucHVzaCh7eDplLmNsaWVudFgseTplLmNs"
    "aWVudFkscjowLG86MX0pfSx7cGFzc2l2ZTp0cnVlfSk7CiAgYWRkRXZlbnRMaXN0ZW5lcigicG9p"
    "bnRlcmxlYXZlIiwoKT0+dGd0PXt4Oi05OTk5LHk6LTk5OTl9KTsKICBmdW5jdGlvbiB3YXJwKGd4"
    "LGd5LGMscixjb2xzLHJvd3MpewogICAgY29uc3QgY3A9TWF0aC5taW4oYy8xLjUsKGNvbHMtMS1j"
    "KS8xLjUsMSkscnA9TWF0aC5taW4oci8xLjUsKHJvd3MtMS1yKS8xLjUsMSkscGluPWNwKmNwKnJw"
    "KnJwOwogICAgY29uc3QgZHg9Z3gtbW91c2UueCxkeT1neS1tb3VzZS55LGRpc3Q9TWF0aC5oeXBv"
    "dChkeCxkeSk7CiAgICBjb25zdCBwcm94PU1hdGgubWF4KDAsMS1kaXN0L0lORikqcGluO2xldCBy"
    "eD0wLHJ5PTA7CiAgICBmb3IoY29uc3QgcCBvZiByaXApe2NvbnN0IGRkeD1neC1wLngsZGR5PWd5"
    "LXAueSxkZD1NYXRoLmh5cG90KGRkeCxkZHkpLGRpZmY9ZGQtcC5yOwogICAgICBpZihNYXRoLmFi"
    "cyhkaWZmKTw1NSl7Y29uc3Qgc3Q9KDEtTWF0aC5hYnMoZGlmZikvNTUpKnAubyoxNipwaW4sYW49"
    "TWF0aC5hdGFuMihkZHksZGR4KSxzZz1kaWZmPDA/LTE6MTsKICAgICAgICByeCs9TWF0aC5jb3Mo"
    "YW4pKnN0KnNnKi0xO3J5Kz1NYXRoLnNpbihhbikqc3Qqc2cqLTE7fX0KICAgIGlmKGRpc3Q8SU5G"
    "JiZkaXN0PjAmJnBpbj4wKXtjb25zdCB0PWRpc3QvSU5GLGU9KDEtdCkqKDEtdCkqTWF0aC5taW4o"
    "MSxkaXN0LzYwKSx3PWUqV0FSUCpwaW4sYW49TWF0aC5hdGFuMihkeSxkeCk7CiAgICAgIHJldHVy"
    "bltneC1NYXRoLmNvcyhhbikqdytyeCxneS1NYXRoLnNpbihhbikqdytyeSxwcm94XTt9CiAgICBy"
    "ZXR1cm5bZ3grcngsZ3krcnkscHJveF07fQogIGZ1bmN0aW9uIGZyYW1lKCl7CiAgICBtb3VzZS54"
    "Kz0odGd0LngtbW91c2UueCkqTEVSUDttb3VzZS55Kz0odGd0LnktbW91c2UueSkqTEVSUDsKICAg"
    "IHJpcD1yaXAuZmlsdGVyKHA9PntwLnIrPTc7cC5vLT0uMDE2O3JldHVybiBwLm8+MH0pOwogICAg"
    "Y3R4LmNsZWFyUmVjdCgwLDAsVyxIKTsKICAgIGNvbnN0IGNvbHM9TWF0aC5jZWlsKFcvU1ApKzEs"
    "cm93cz1NYXRoLmNlaWwoSC9TUCkrMSxwdHM9W107CiAgICBmb3IobGV0IHI9MDtyPHJvd3M7cisr"
    "KXtjb25zdCByb3c9W107Zm9yKGxldCBjPTA7Yzxjb2xzO2MrKylyb3cucHVzaCh3YXJwKGMqU1As"
    "cipTUCxjLHIsY29scyxyb3dzKSk7cHRzLnB1c2gocm93KTt9CiAgICBmb3IobGV0IHI9MDtyPHJv"
    "d3M7cisrKWZvcihsZXQgYz0wO2M8Y29scztjKyspe2NvbnN0IHA9cHRzW3JdW2NdOwogICAgICBp"
    "ZihjPGNvbHMtMSl7Y29uc3QgcT1wdHNbcl1bYysxXTtjdHguc3Ryb2tlU3R5bGU9InJnYmEoIitS"
    "R0IrIiwiKyguMDUrcFsyXSouMjIpLnRvRml4ZWQoMykrIikiOwogICAgICAgIGN0eC5saW5lV2lk"
    "dGg9MTtjdHguYmVnaW5QYXRoKCk7Y3R4Lm1vdmVUbyhwWzBdLHBbMV0pO2N0eC5saW5lVG8ocVsw"
    "XSxxWzFdKTtjdHguc3Ryb2tlKCk7fQogICAgICBpZihyPHJvd3MtMSl7Y29uc3QgcT1wdHNbcisx"
    "XVtjXTtjdHguc3Ryb2tlU3R5bGU9InJnYmEoIitSR0IrIiwiKyguMDUrcFsyXSouMjIpLnRvRml4"
    "ZWQoMykrIikiOwogICAgICAgIGN0eC5saW5lV2lkdGg9MTtjdHguYmVnaW5QYXRoKCk7Y3R4Lm1v"
    "dmVUbyhwWzBdLHBbMV0pO2N0eC5saW5lVG8ocVswXSxxWzFdKTtjdHguc3Ryb2tlKCk7fQogICAg"
    "ICBpZihwWzJdPi4wNil7Y3R4LmZpbGxTdHlsZT0icmdiYSgiK1JHQisiLCIrKHBbMl0qLjUpLnRv"
    "Rml4ZWQoMykrIikiO2N0eC5iZWdpblBhdGgoKTsKICAgICAgICBjdHguYXJjKHBbMF0scFsxXSwx"
    "LjYrcFsyXSoxLjYsMCw2LjI4Myk7Y3R4LmZpbGwoKTt9fQogICAgcmFmPXJlcXVlc3RBbmltYXRp"
    "b25GcmFtZShmcmFtZSk7fQogIGZyYW1lKCk7CiAgZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigi"
    "dmlzaWJpbGl0eWNoYW5nZSIsKCk9PntpZihkb2N1bWVudC5oaWRkZW4pY2FuY2VsQW5pbWF0aW9u"
    "RnJhbWUocmFmKTtlbHNlIGZyYW1lKCk7fSk7Cn0pKCk7CgovKiAwMSDCtyBkb3QgbG9hZGVyIHNw"
    "bGFzaCAoa2l0IGZyYW1lIGRhdGEpICovCihmdW5jdGlvbiBzcGxhc2goKXsKICBjb25zdCBnPWRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJkZyIpLHNwPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJz"
    "cGxhc2giKTtpZighZylyZXR1cm47CiAgZm9yKGxldCBpPTA7aTw0OTtpKyspZy5hcHBlbmRDaGls"
    "ZChkb2N1bWVudC5jcmVhdGVFbGVtZW50KCJpIikpOwogIGNvbnN0IGNlbGxzPVsuLi5nLmNoaWxk"
    "cmVuXTsKICBjb25zdCBGPVtbMTQsNywwLDgsNiwxMywyMF0sWzE0LDcsMTMsMjAsMTYsMjcsMjFd"
    "LFsxNCwyMCwyNywyMSwzNCwyNCwyOF0sWzI3LDIxLDM0LDI4LDQxLDMyLDM1XSwKICAgWzM0LDI4"
    "LDQxLDM1LDQ4LDQwLDQyXSxbMzQsMjgsNDEsMzUsNDgsNDIsNDZdLFszNCwyOCw0MSwzNSw0OCw0"
    "MiwzOF0sWzM0LDI4LDQxLDM1LDQ4LDMwLDIxXSwKICAgWzM0LDI4LDQxLDQ4LDIxLDIyLDE0XSxb"
    "MzQsMjgsNDEsMjEsMTQsMTYsMjddLFszNCwyOCwyMSwxNCwxMCwyMCwyN10sWzI4LDIxLDE0LDQs"
    "MTMsMjAsMjddLAogICBbMjgsMjEsMTQsMTIsNiwxMywyMF0sWzI4LDIxLDE0LDYsMTMsMjAsMTFd"
    "LFsyOCwyMSwxNCw2LDEzLDIwLDEwXSxbMTQsNiwxMywyMCw5LDcsMjFdXTsKICBsZXQgZj0wO2Nv"
    "bnN0IGl2PXNldEludGVydmFsKCgpPT57Y2VsbHMuZm9yRWFjaChjPT5jLmNsYXNzTGlzdC5yZW1v"
    "dmUoImFjdCIpKTsKICAgIEZbZiVGLmxlbmd0aF0uZm9yRWFjaChpPT5jZWxsc1tpXSYmY2VsbHNb"
    "aV0uY2xhc3NMaXN0LmFkZCgiYWN0IikpO2YrKzt9LDEwMCk7CiAgc2V0VGltZW91dCgoKT0+e2Ns"
    "ZWFySW50ZXJ2YWwoaXYpO3NwLmNsYXNzTGlzdC5hZGQoImdvbmUiKTsKICAgIHNldFRpbWVvdXQo"
    "KCk9PntzcC5yZW1vdmUoKTtpZih3aW5kb3cuX19kb2NrUGxhY2Upd2luZG93Ll9fZG9ja1BsYWNl"
    "KHRydWUpOwogICAgICBpZih3aW5kb3cuX192dGFiUGxhY2Upd2luZG93Ll9fdnRhYlBsYWNlKHRy"
    "dWUpO30sNjAwKTt9LFJNPzMwMDoxNzUwKTsKfSkoKTsKCi8qIHN0YWdnZXJlZCByZXZlYWwgb24g"
    "ZXZlcnkgdmlldyBjaGFuZ2UgKi8KZnVuY3Rpb24gcmV2ZWFsU3RhZ2UoZWwpewogIGlmKCFlbHx8"
    "Uk0pcmV0dXJuOwogIFsuLi5lbC5jaGlsZHJlbl0uc2xpY2UoMCwxNCkuZm9yRWFjaCgoYyxpKT0+"
    "ewogICAgaWYoIWMuYW5pbWF0ZSlyZXR1cm47CiAgICB0cnl7CiAgICAgIGMuZ2V0QW5pbWF0aW9u"
    "cygpLmZvckVhY2goYT0+e2lmKGEuaWQ9PT0icnYiKWEuY2FuY2VsKCk7fSk7CiAgICAgIGNvbnN0"
    "IGFuPWMuYW5pbWF0ZShbe29wYWNpdHk6MCx0cmFuc2Zvcm06InRyYW5zbGF0ZVkoMTRweCkgc2Nh"
    "bGUoLjk4NSkifSx7b3BhY2l0eToxLHRyYW5zZm9ybToibm9uZSJ9XSwKICAgICAgICB7ZHVyYXRp"
    "b246NTIwLGRlbGF5OmkqNDAsZWFzaW5nOiJjdWJpYy1iZXppZXIoLjIyLDEsLjM2LDEpIixmaWxs"
    "OiJiYWNrd2FyZHMifSk7CiAgICAgIGFuLmlkPSJydiI7CiAgICB9Y2F0Y2goZSl7fQogIH0pO30K"
    "Ci8qIG51bWJlciB0aWNrZXIgKi8KZnVuY3Rpb24gdGlja1RvKGVsLHRvLGR1cil7aWYoIWVsKXJl"
    "dHVybjtjb25zdCBmcm9tPXBhcnNlRmxvYXQoZWwudGV4dENvbnRlbnQucmVwbGFjZSgvW15cZC5d"
    "L2csIiIpKXx8MDsKICBpZihSTSl7ZWwudGV4dENvbnRlbnQ9dG8udG9GaXhlZCgyKTtyZXR1cm59"
    "CiAgY29uc3QgdDA9cGVyZm9ybWFuY2Uubm93KCk7KGZ1bmN0aW9uIHN0KHQpe2NvbnN0IHA9TWF0"
    "aC5taW4oMSwodC10MCkvKGR1cnx8NjAwKSk7CiAgICBjb25zdCBlPTEtTWF0aC5wb3coMS1wLDMp"
    "O2VsLnRleHRDb250ZW50PShmcm9tKyh0by1mcm9tKSplKS50b0ZpeGVkKDIpOwogICAgaWYocDwx"
    "KXJlcXVlc3RBbmltYXRpb25GcmFtZShzdCk7fSkodDApO30KCi8qIHByZXNzIHJpcHBsZSBvbiBl"
    "dmVyeSBidXR0b24gKi8KZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcmRvd24iLGU9"
    "Pntjb25zdCBiPWUudGFyZ2V0LmNsb3Nlc3QoIi5idG4sLmNoaXByb3cgYnV0dG9uLC5zdWJuYXYg"
    "YnV0dG9uIik7CiAgaWYoIWJ8fFJNKXJldHVybjtjb25zdCByPWIuZ2V0Qm91bmRpbmdDbGllbnRS"
    "ZWN0KCksZD1NYXRoLm1heChyLndpZHRoLHIuaGVpZ2h0KSoxLjQ7CiAgY29uc3Qgcz1kb2N1bWVu"
    "dC5jcmVhdGVFbGVtZW50KCJzcGFuIik7cy5jbGFzc05hbWU9InJpcCI7CiAgcy5zdHlsZS5jc3NU"
    "ZXh0PSJsZWZ0OiIrKGUuY2xpZW50WC1yLmxlZnQpKyJweDt0b3A6IisoZS5jbGllbnRZLXIudG9w"
    "KSsicHg7d2lkdGg6IitkKyJweDtoZWlnaHQ6IitkKyJweCI7CiAgaWYoZ2V0Q29tcHV0ZWRTdHls"
    "ZShiKS5wb3NpdGlvbj09PSJzdGF0aWMiKWIuc3R5bGUucG9zaXRpb249InJlbGF0aXZlIjsKICBi"
    "LmFwcGVuZENoaWxkKHMpO3NldFRpbWVvdXQoKCk9PnMucmVtb3ZlKCksNTYwKTt9KTsKCi8qIDE1"
    "IMK3IHRpbHQgKyBnbGFyZSAqLwpmdW5jdGlvbiBiaW5kVGlsdCgpe2RvY3VtZW50LnF1ZXJ5U2Vs"
    "ZWN0b3JBbGwoIltkYXRhLXRpbHRdIikuZm9yRWFjaChjPT57CiAgaWYoYy5fdHx8Uk0pcmV0dXJu"
    "O2MuX3Q9MTsKICBjLmFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJtb3ZlIixlPT57Y29uc3Qgcj1j"
    "LmdldEJvdW5kaW5nQ2xpZW50UmVjdCgpOwogICAgY29uc3QgeD0oZS5jbGllbnRYLXIubGVmdCkv"
    "ci53aWR0aCx5PShlLmNsaWVudFktci50b3ApL3IuaGVpZ2h0OwogICAgYy5zdHlsZS50cmFuc2Zv"
    "cm09InBlcnNwZWN0aXZlKDcwMHB4KSByb3RhdGVYKCIrKCguNS15KSo5KS50b0ZpeGVkKDIpKyJk"
    "ZWcpIHJvdGF0ZVkoIisoKHgtLjUpKjExKS50b0ZpeGVkKDIpKyJkZWcpIHRyYW5zbGF0ZVooNHB4"
    "KSI7CiAgICBjLnN0eWxlLnNldFByb3BlcnR5KCItLW14IiwoeCoxMDApKyIlIik7Yy5zdHlsZS5z"
    "ZXRQcm9wZXJ0eSgiLS1teSIsKHkqMTAwKSsiJSIpO30pOwogIGMuYWRkRXZlbnRMaXN0ZW5lcigi"
    "cG9pbnRlcmxlYXZlIiwoKT0+Yy5zdHlsZS50cmFuc2Zvcm09IiIpO30pO30KCi8qIHN1Y2Nlc3Mg"
    "YnVyc3QgKi8KZnVuY3Rpb24gYnVyc3QoKXtjb25zdCBmeD1kb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgiZngiKTtpZighZngpcmV0dXJuO2Z4LmNsYXNzTGlzdC5hZGQoIm9uIik7CiAgY29uc3Qgdz1m"
    "eC5xdWVyeVNlbGVjdG9yKCIud2F2ZSIpO3cuc3R5bGUuYW5pbWF0aW9uPSJub25lIjt2b2lkIHcu"
    "b2Zmc2V0V2lkdGg7dy5zdHlsZS5hbmltYXRpb249IiI7CiAgc2V0VGltZW91dCgoKT0+ZnguY2xh"
    "c3NMaXN0LnJlbW92ZSgib24iKSw5MDApO30KCi8qIOKUgOKUgCBQYXkgKyB2ZXJpZnkgZGVwb3Np"
    "dCBmbG93ICg1LW1pbnV0ZSB3aW5kb3csIHZlcmlmeSBvbmx5IG9uIHRhcCkg4pSA4pSAICovCmxl"
    "dCBkZXBUaWNrPW51bGwsZGVwSm9iPW51bGw7CmZ1bmN0aW9uIGFtb3VudFNoZWV0KCl7CiAgY29u"
    "c3QgcGlja3M9WzUwLDEwMCwyMDAsNTAwLDEwMDAsMjAwMF07CiAgc2hlZXQoYDxoMyBzdHlsZT0i"
    "bWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPiR7Q01TLndhbEtpY2t8fCJBZGQgZnVuZHMi"
    "fTwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj5QaWNrIGFuIGFtb3VudCBvciB0eXBlIHlvdXIgb3du"
    "LjwvcD4KICAgIDxkaXYgY2xhc3M9ImNoaXBzIiBzdHlsZT0ibWFyZ2luOjEycHggMCA0cHg7ZGlz"
    "cGxheTpmbGV4O2ZsZXgtd3JhcDp3cmFwO2dhcDo3cHgiPgogICAgICAke3BpY2tzLm1hcCh2PT5g"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBkYXRhLXBpY2s9IiR7dn0iIHN0eWxlPSJmbGV4OjAgMCBh"
    "dXRvIj4ke21vbmV5KHYpfTwvYnV0dG9uPmApLmpvaW4oIiIpfQogICAgPC9kaXY+CiAgICA8ZGl2"
    "IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPkN1c3RvbSBhbW91"
    "bnQ8L2xhYmVsPgogICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImFtdGluIiB0eXBlPSJudW1i"
    "ZXIiIGlucHV0bW9kZT0ibnVtZXJpYyIgbWluPSIxIiBwbGFjZWhvbGRlcj0iRW50ZXIgYW1vdW50"
    "Ij48L2Rpdj4KICAgIDxwIGNsYXNzPSJzdWIiIGlkPSJhbXRoaW50IiBzdHlsZT0ibWFyZ2luOjAg"
    "MCAxMHB4O2ZvbnQtc2l6ZTouNzJyZW0iPjwvcD4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEg"
    "YmxvY2siIGlkPSJhbXRnbyI+Q29udGludWU8L2J1dHRvbj4KICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNsb3NlPkNhbmNl"
    "bDwvYnV0dG9uPmApOwogIGNvbnN0IGlucD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYW10aW4i"
    "KSwgZ289ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFtdGdvIiksCiAgICAgICAgaGludD1kb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgiYW10aGludCIpOwogIGNvbnN0IGxvPShBRE0mJkFETS5wYXkm"
    "JkFETS5wYXkubWluKXx8MTAsIGhpPShBRE0mJkFETS5wYXkmJkFETS5wYXkubWF4KXx8MjAwMDA7"
    "CiAgaGludC50ZXh0Q29udGVudD0iQmV0d2VlbiAiK21vbmV5KGxvKSsiIGFuZCAiK21vbmV5KGhp"
    "KSsiLiI7CiAgY29uc3Qgc3RhcnQ9dj0+ewogICAgY29uc3Qgbj1OdW1iZXIodik7CiAgICBpZigh"
    "bnx8bjw9MCl7dG9hc3QoIkVudGVyIGFuIGFtb3VudCBmaXJzdCIpO2lucCYmaW5wLmZvY3VzKCk7"
    "cmV0dXJuO30KICAgIGlmKG48bG98fG4+aGkpe3RvYXN0KCJBbGxvd2VkICIrbW9uZXkobG8pKyIg"
    "dG8gIittb25leShoaSkpO3JldHVybjt9CiAgICBkZXBvc2l0U2hlZXQobik7CiAgfTsKICBkb2N1"
    "bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1waWNrXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNr"
    "PSgpPT5zdGFydChiLmRhdGFzZXQucGljaykpOwogIGdvLm9uY2xpY2s9KCk9PnN0YXJ0KGlucC52"
    "YWx1ZSk7CiAgaWYoaW5wKWlucC5vbmtleWRvd249ZT0+e2lmKGUua2V5PT09IkVudGVyIilzdGFy"
    "dChpbnAudmFsdWUpO307CiAgc2V0VGltZW91dCgoKT0+e2lmKGlucClpbnAuZm9jdXMoKTt9LDI1"
    "MCk7Cn0KYXN5bmMgZnVuY3Rpb24gbGl2ZURlcG9zaXRTaGVldChhKXsKICBjbGVhckludGVydmFs"
    "KGRlcFRpY2spO2NsZWFyVGltZW91dChkZXBKb2IpOwogIGNvbnN0IGQ9YXdhaXQgYXBpVHJ5KCIv"
    "YXBpL2RlcG9zaXQvY3JlYXRlIix7YW1vdW50Ok51bWJlcihhKX0pOwogIGlmKCFkKXJldHVybjsK"
    "ICBjb25zdCBtaW5zPWQud2luZG93fHw1OwogIGxldCBsZWZ0PU1hdGgubWF4KDAsTWF0aC5yb3Vu"
    "ZCgobmV3IERhdGUoZC5leHBpcmVzX2F0KS1uZXcgRGF0ZSgpKS8xMDAwKSl8fG1pbnMqNjA7CiAg"
    "Y29uc3QgdG90YWw9bWlucyo2MDsKICBsZXQgZG9uZT1mYWxzZSxidXN5PWZhbHNlOwogIHNoZWV0"
    "KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj4ke0NNUy5zaGVldFRp"
    "dGxlfSAke21vbmV5KGQuYW1vdW50KX08L2gzPgogICAgPHAgY2xhc3M9InN1YiI+JHtDTVMuc2hl"
    "ZXRTdWIucmVwbGFjZSgie219IixtaW5zKX08L3A+CiAgICAke2QucXI/YDxkaXYgY2xhc3M9InFy"
    "IiBzdHlsZT0icGFkZGluZzowO292ZXJmbG93OmhpZGRlbiI+PGltZyBzcmM9IiR7ZC5xcn0iIGFs"
    "dD0iVVBJIFFSIiBzdHlsZT0id2lkdGg6MTAwJTtkaXNwbGF5OmJsb2NrIj48L2Rpdj5gOiIifQog"
    "ICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlVQSSBJRDwvc3Bhbj48Yj4ke2VzYyhkLnVwaXx8IiIp"
    "fTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5QYXllZTwvc3Bhbj48Yj4ke2Vz"
    "YyhkLnBheWVlfHwiIil9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkFtb3Vu"
    "dDwvc3Bhbj48Yj4ke21vbmV5KGQuYW1vdW50KX08L2I+PC9kaXY+CiAgICAke2QuYm9udXM/YDxk"
    "aXYgY2xhc3M9Imt2Ij48c3Bhbj5Cb251czwvc3Bhbj48Yj4rJHttb25leShkLmJvbnVzKX08L2I+"
    "PC9kaXY+YDoiIn0KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Ob3RlIC8gcmVmZXJlbmNlPC9z"
    "cGFuPjxiPiR7ZXNjKGQuY29kZSl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFu"
    "PkV4cGlyZXMgaW48L3NwYW4+PGIgaWQ9ImRwY2QiPiR7TWF0aC5mbG9vcihsZWZ0LzYwKX06JHtT"
    "dHJpbmcobGVmdCU2MCkucGFkU3RhcnQoMiwiMCIpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9"
    "InRpbWVyYmFyIiBpZD0iZHBiYXIiPjxpIHN0eWxlPSJ3aWR0aDoxMDAlIj48L2k+PC9kaXY+CiAg"
    "ICA8ZGl2IGNsYXNzPSJzdGVwcyIgaWQ9ImRzdGVwcyI+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAg"
    "b24iPjxpPjE8L2k+JHtDTVMuc3RlcDF9PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAiIGlk"
    "PSJzMiI+PGk+MjwvaT4ke0NNUy5zdGVwMn08L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCIg"
    "aWQ9InMzIj48aT4zPC9pPiR7Q01TLnN0ZXAzfTwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGVw"
    "IiBpZD0iczQiPjxpPjQ8L2k+JHtDTVMuc3RlcDR9PC9kaXY+CiAgICA8L2Rpdj4KICAgIDxkaXYg"
    "Y2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij4KICAgICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIiBpZD0iZHBheSI+8J+PpiBDb3B5IFVQSSBJRDwvYnV0dG9uPgogICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4gY3RhIiBpZD0iZHZlciI+JHtTVE9SRS5hdXRvY2hlY2s9PT1mYWxzZT8i4pyF"
    "IEkgaGF2ZSBwYWlkIjoi4pyFICIrQ01TLnZlcmlmeUJ0bn08L2J1dHRvbj48L2Rpdj4KICAgICR7"
    "U1RPUkUuYXV0b2NoZWNrPT09ZmFsc2U/IiI6YDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IGJsb2Nr"
    "IHNtIiBpZD0iZHV0ciIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij7wn6e+IFNlbmQgdHJhbnNhY3Rp"
    "b24gbm8uPC9idXR0b24+YH0KICAgIDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIg"
    "c3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+YCk7CiAgY29u"
    "c3QgZz1pZD0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoaWQpOwogIGNvbnN0IHBheUI9ZygiZHBh"
    "eSIpLHZlckI9ZygiZHZlciIpLHV0ckI9ZygiZHV0ciIpOwogIGNvbnN0IGFsaXZlPSgpPT5nKCJk"
    "cGNkIikmJmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzaGVldCIpLmNsYXNzTGlzdC5jb250YWlu"
    "cygib3BlbiIpOwogIGZ1bmN0aW9uIGV4cGlyZSgpewogICAgY2xlYXJJbnRlcnZhbChkZXBUaWNr"
    "KTsKICAgIGlmKCFhbGl2ZSgpKXJldHVybjsKICAgIGcoImRwY2QiKS50ZXh0Q29udGVudD0iMDow"
    "MCI7ZygiZHBiYXIiKS5jbGFzc0xpc3QuYWRkKCJsb3ciKTtnKCJkcGJhciIpLmZpcnN0Q2hpbGQu"
    "c3R5bGUud2lkdGg9IjAlIjsKICAgIHZlckIuZGlzYWJsZWQ9dHJ1ZTt1dHJCLmRpc2FibGVkPXRy"
    "dWU7dmVyQi50ZXh0Q29udGVudD0iRXhwaXJlZCI7CiAgICBwYXlCLmNsYXNzTmFtZT0iYnRuIjtw"
    "YXlCLnRleHRDb250ZW50PSJOZXcgUmVxdWVzdCI7cGF5Qi5vbmNsaWNrPSgpPT5kZXBvc2l0U2hl"
    "ZXQoYSk7CiAgICB0b2FzdChDTVMubXNnRXhwaXJlZCk7CiAgfQogIGRlcFRpY2s9c2V0SW50ZXJ2"
    "YWwoKCk9PnsKICAgIGlmKCFhbGl2ZSgpKXtjbGVhckludGVydmFsKGRlcFRpY2spO3JldHVybjt9"
    "CiAgICBsZWZ0LS07CiAgICBnKCJkcGNkIikudGV4dENvbnRlbnQ9TWF0aC5tYXgoMCxNYXRoLmZs"
    "b29yKGxlZnQvNjApKSsiOiIrU3RyaW5nKE1hdGgubWF4KDAsbGVmdCU2MCkpLnBhZFN0YXJ0KDIs"
    "IjAiKTsKICAgIGcoImRwYmFyIikuZmlyc3RDaGlsZC5zdHlsZS53aWR0aD0oTWF0aC5tYXgoMCxs"
    "ZWZ0KS90b3RhbCoxMDApKyIlIjsKICAgIGcoImRwYmFyIikuY2xhc3NMaXN0LnRvZ2dsZSgibG93"
    "IixsZWZ0PD02MCk7CiAgICBpZihsZWZ0PD0wKWV4cGlyZSgpOwogIH0sMTAwMCk7CiAgaWYocGF5"
    "QilwYXlCLm9uY2xpY2s9KCk9PnsKICAgIGlmKGRvbmUpcmV0dXJuOwogICAgZygiczIiKS5jbGFz"
    "c05hbWU9InN0ZXAgb24iO2coInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1biI7CiAgICB2ZXJCLmNs"
    "YXNzTGlzdC5yZW1vdmUoImdsYXNzIik7dmVyQi5jbGFzc0xpc3QuYWRkKCJjdGEiKTsKICAgIHRy"
    "eXtuYXZpZ2F0b3IuY2xpcGJvYXJkJiZuYXZpZ2F0b3IuY2xpcGJvYXJkLndyaXRlVGV4dChkLnVw"
    "aXx8IiIpO31jYXRjaChlKXt9CiAgICB0b2FzdCgiVVBJIElEIGNvcGllZDogIisoZC51cGl8fCIi"
    "KSk7CiAgfTsKICBpZih2ZXJCKXZlckIub25jbGljaz1hc3luYygpPT57CiAgICBpZihkb25lfHxi"
    "dXN5fHxsZWZ0PD0wKXJldHVybjsKICAgIGlmKFNUT1JFLmF1dG9jaGVjaz09PWZhbHNlKXsgICAg"
    "ICAgICAgLyogbm8gYXV0b21hdGljIGNoZWNraW5nIOKAlCBjb2xsZWN0IHByb29mICovCiAgICAg"
    "IHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5TZW5kIHlv"
    "dXIgcGF5bWVudCBwcm9vZjwvaDM+CiAgICAgICAgPHAgY2xhc3M9InN1YiI+QSBzY3JlZW5zaG90"
    "IG9mIHRoZSBzdWNjZXNzZnVsIHBheW1lbnQgYW5kIHRoZSBVVFIuPC9wPgogICAgICAgIDxkaXYg"
    "Y2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+U2NyZWVuc2hvdDwv"
    "bGFiZWw+CiAgICAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9InBmc2hvdCIgdHlwZT0iZmls"
    "ZSIgYWNjZXB0PSJpbWFnZS8qIj48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxh"
    "YmVsPlVUUiAvIHRyYW5zYWN0aW9uIG51bWJlcjwvbGFiZWw+CiAgICAgICAgICA8aW5wdXQgY2xh"
    "c3M9ImlucCIgaWQ9InBmdXRyIiBpbnB1dG1vZGU9Im51bWVyaWMiIHBsYWNlaG9sZGVyPSIxMiBk"
    "aWdpdHMiPjwvZGl2PgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEgYmxvY2siIGlkPSJw"
    "ZmdvIj5TdWJtaXQgZm9yIHJldmlldzwvYnV0dG9uPgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNsb3NlPkNhbmNl"
    "bDwvYnV0dG9uPmApOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicGZnbyIpLm9uY2xp"
    "Y2s9YXN5bmMoKT0+ewogICAgICAgIGNvbnN0IGY9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInBm"
    "c2hvdCIpOwogICAgICAgIGNvbnN0IHU9KGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJwZnV0ciIp"
    "LnZhbHVlfHwiIikucmVwbGFjZSgvXEQvZywiIik7CiAgICAgICAgaWYodS5sZW5ndGg8Nil7dG9h"
    "c3QoIkVudGVyIHRoZSBVVFIgZnJvbSB5b3VyIHBheW1lbnQgYXBwIik7cmV0dXJuO30KICAgICAg"
    "ICBjb25zdCBmaWxlPShmJiZmLmZpbGVzJiZmLmZpbGVzWzBdKXx8bnVsbDsKICAgICAgICBsZXQg"
    "c2hvdD0iIjsKICAgICAgICBpZihmaWxlKXsKICAgICAgICAgIGlmKGZpbGUuc2l6ZT44KjEwMjQq"
    "MTAyNCl7dG9hc3QoIlRoYXQgaW1hZ2UgaXMgdG9vIGxhcmdlICg4IE1CIG1heCkiKTtyZXR1cm47"
    "fQogICAgICAgICAgc2hvdD1hd2FpdCBuZXcgUHJvbWlzZShyZXM9Pntjb25zdCByZD1uZXcgRmls"
    "ZVJlYWRlcigpOwogICAgICAgICAgICByZC5vbmxvYWQ9KCk9PnJlcyhTdHJpbmcocmQucmVzdWx0"
    "fHwiIikpO3JkLm9uZXJyb3I9KCk9PnJlcygiIik7CiAgICAgICAgICAgIHJkLnJlYWRBc0RhdGFV"
    "UkwoZmlsZSk7fSk7CiAgICAgICAgfQogICAgICAgIGNvbnN0IGJ0bj1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgicGZnbyIpO2J0bi5kaXNhYmxlZD10cnVlO2J0bi50ZXh0Q29udGVudD0iU2VuZGlu"
    "Z+KApiI7CiAgICAgICAgY29uc3QgcnI9YXdhaXQgYXBpVHJ5KCIvYXBpL2RlcG9zaXQvY2xhaW0i"
    "LHtpZDpkLmlkLHV0cjp1LHNob3Q6c2hvdH0pOwogICAgICAgIGJ0bi5kaXNhYmxlZD1mYWxzZTti"
    "dG4udGV4dENvbnRlbnQ9IlN1Ym1pdCBmb3IgcmV2aWV3IjsKICAgICAgICBpZighcnIpcmV0dXJu"
    "OwogICAgICAgIGNsb3NlU2hlZXQoKTt0b2FzdCgiU2VudCDigJQgd2Ugd2lsbCBjb25maXJtIGl0"
    "IHNob3J0bHkiKTsKICAgICAgICBhd2FpdCByZWZyZXNoKCk7CiAgICAgIH07CiAgICAgIHJldHVy"
    "bjsKICAgIH0KICAgIGJ1c3k9dHJ1ZTt2ZXJCLmRpc2FibGVkPXRydWU7dmVyQi50ZXh0Q29udGVu"
    "dD0iQ2hlY2tpbmfigKYiO2coInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1biI7CiAgICBjb25zdCBy"
    "PWF3YWl0IGFwaVRyeSgiL2FwaS9kZXBvc2l0L3ZlcmlmeSIse2lkOmQuaWR9KTsKICAgIGJ1c3k9"
    "ZmFsc2U7CiAgICBpZighYWxpdmUoKSlyZXR1cm47CiAgICB2ZXJCLmRpc2FibGVkPWZhbHNlO3Zl"
    "ckIudGV4dENvbnRlbnQ9Q01TLnZlcmlmeUJ0bjsKICAgIGlmKCFyKXJldHVybjsKICAgIGlmKHIu"
    "c3RhdHVzPT09ImNvbXBsZXRlZCIpewogICAgICBkb25lPXRydWU7Y2xlYXJJbnRlcnZhbChkZXBU"
    "aWNrKTsKICAgICAgZygiczMiKS5jbGFzc05hbWU9InN0ZXAgb24iO2coInM0IikuY2xhc3NOYW1l"
    "PSJzdGVwIG9uIjsKICAgICAgaWYodHlwZW9mIHIuYmFsYW5jZT09PSJudW1iZXIiKUJBTD1yLmJh"
    "bGFuY2U7CiAgICAgIHN5bmNCYWwoKTtidXJzdCgpO3RvYXN0KENNUy5tc2dPayk7CiAgICAgIHNl"
    "dFRpbWVvdXQoKCk9PntjbG9zZVNoZWV0KCk7cmVmcmVzaCgpO30sOTAwKTsKICAgIH1lbHNlIGlm"
    "KHIuc3RhdHVzPT09InJldmlldyIpewogICAgICBnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCBvbiI7"
    "dG9hc3QoIlBheW1lbnQgc2VudCBmb3IgY2hlY2tpbmcg4oCUIHdlIHdpbGwgY29uZmlybSBpdCBz"
    "aG9ydGx5Iik7CiAgICB9ZWxzZSBpZihyLnN0YXR1cz09PSJleHBpcmVkIil7CiAgICAgIGV4cGly"
    "ZSgpOwogICAgfWVsc2V7CiAgICAgIHRvYXN0KENNUy5tc2dOb25lKTsKICAgIH0KICB9OwogIGlm"
    "KHV0ckIpdXRyQi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHY9cHJvbXB0KCJFbnRlciB0"
    "aGUgMTItZGlnaXQgVVRSIGZyb20geW91ciBwYXltZW50IGFwcCIpOwogICAgaWYoIXYpcmV0dXJu"
    "OwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvZGVwb3NpdC91dHIiLHtpZDpkLmlkLHV0"
    "cjp2fSk7CiAgICBpZihyKXRvYXN0KCJUaGFua3Mg4oCUIHdlIGFyZSBjaGVja2luZyB5b3VyIHBh"
    "eW1lbnQiKTsKICB9Owp9CmZ1bmN0aW9uIGRlcG9zaXRTaGVldChhKXsKICBpZihMSVZFKXJldHVy"
    "biBsaXZlRGVwb3NpdFNoZWV0KGEpOwogIGNsZWFySW50ZXJ2YWwoZGVwVGljayk7Y2xlYXJUaW1l"
    "b3V0KGRlcEpvYik7CiAgY29uc3QgY29kZT0oQ01TLnBheVByZWZpeHx8IlBBWSIpK01hdGguZmxv"
    "b3IoMTAwMCtNYXRoLnJhbmRvbSgpKjg5OTkpOwogIGNvbnN0IG1pbnM9TWF0aC5tYXgoMSxwYXJz"
    "ZUludChDTVMucGF5V2luZG93LDEwKXx8NSksIHRvdGFsPW1pbnMqNjA7CiAgbGV0IGxlZnQ9dG90"
    "YWwscGFpZD1mYWxzZSxkb25lPWZhbHNlLGJ1c3k9ZmFsc2U7CiAgc2hlZXQoYDxoMyBzdHlsZT0i"
    "bWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPiR7Q01TLnNoZWV0VGl0bGV9ICR7bW9uZXko"
    "YSl9PC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPiR7Q01TLnNoZWV0U3ViLnJlcGxhY2UoInttfSIs"
    "bWlucyl9PC9wPgogICAgPGRpdiBjbGFzcz0icXIiPlFSIENPREU8YnI+KGdlbmVyYXRlZCBieSBi"
    "b3QpPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VVBJIElEPC9zcGFuPjxiPiR7ZXNj"
    "KFNUT1JFLnVwaXx8Im5vdCBzZXQgeWV0Iil9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3Yi"
    "PjxzcGFuPlBheWVlPC9zcGFuPjxiPiR7ZXNjKFNUT1JFLnBheWVlfHwi4oCUIil9PC9iPjwvZGl2"
    "PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkFtb3VudDwvc3Bhbj48Yj4ke21vbmV5KGEpfTwv"
    "Yj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Ob3RlIC8gcmVmZXJlbmNlPC9zcGFu"
    "PjxiPiR7Y29kZX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+RXhwaXJlcyBp"
    "bjwvc3Bhbj48YiBpZD0iZHBjZCI+JHttaW5zfTowMDwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9"
    "InRpbWVyYmFyIiBpZD0iZHBiYXIiPjxpIHN0eWxlPSJ3aWR0aDoxMDAlIj48L2k+PC9kaXY+CiAg"
    "ICA8ZGl2IGNsYXNzPSJzdGVwcyIgaWQ9ImRzdGVwcyI+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAg"
    "b24iPjxpPjE8L2k+JHtDTVMuc3RlcDF9PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAiIGlk"
    "PSJzMiI+PGk+MjwvaT4ke0NNUy5zdGVwMn08L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCIg"
    "aWQ9InMzIj48aT4zPC9pPiR7Q01TLnN0ZXAzfTwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGVw"
    "IiBpZD0iczQiPjxpPjQ8L2k+JHtDTVMuc3RlcDR9PC9kaXY+CiAgICA8L2Rpdj4KICAgIDxkaXYg"
    "Y2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij4KICAgICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIGN0YSIgaWQ9ImRwYXkiPiR7Q01TLnBheUJ0bn08L2J1dHRvbj4KICAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIGdsYXNzIiBpZD0iZHZlciI+JHtDTVMudmVyaWZ5QnRufTwvYnV0dG9uPjwv"
    "ZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQgYmxvY2sgc20iIGlkPSJkdXRyIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDo4cHgiPiR7Q01TLnV0ckJ0bn08L2J1dHRvbj4KICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNsb3Nl"
    "PkNsb3NlPC9idXR0b24+YCk7CiAgY29uc3QgZz1pZD0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "aWQpOwogIGNvbnN0IHBheUI9ZygiZHBheSIpLHZlckI9ZygiZHZlciIpLHV0ckI9ZygiZHV0ciIp"
    "OwogIGZ1bmN0aW9uIGZtdChuKXtyZXR1cm4gTWF0aC5mbG9vcihuLzYwKSsiOiIrU3RyaW5nKG4l"
    "NjApLnBhZFN0YXJ0KDIsIjAiKTt9CiAgZnVuY3Rpb24gYWxpdmUoKXtyZXR1cm4gZygiZHBjZCIp"
    "JiZkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic2hlZXQiKS5jbGFzc0xpc3QuY29udGFpbnMoIm9w"
    "ZW4iKTt9CiAgZnVuY3Rpb24gZXhwaXJlKCl7CiAgICBjbGVhckludGVydmFsKGRlcFRpY2spO2Ns"
    "ZWFyVGltZW91dChkZXBKb2IpOwogICAgaWYoIWFsaXZlKCkpcmV0dXJuOwogICAgZygiZHBjZCIp"
    "LnRleHRDb250ZW50PSIwOjAwIjtnKCJkcGJhciIpLmNsYXNzTGlzdC5hZGQoImxvdyIpO2coImRw"
    "YmFyIikuZmlyc3RDaGlsZC5zdHlsZS53aWR0aD0iMCUiOwogICAgcGF5Qi5kaXNhYmxlZD10cnVl"
    "O3ZlckIuZGlzYWJsZWQ9dHJ1ZTt1dHJCLmRpc2FibGVkPXRydWU7CiAgICBnKCJzMiIpLmNsYXNz"
    "TmFtZT0ic3RlcCI7ZygiczMiKS5jbGFzc05hbWU9InN0ZXAiOwogICAgdmVyQi50ZXh0Q29udGVu"
    "dD0iRXhwaXJlZCI7cGF5Qi5jbGFzc05hbWU9ImJ0biI7cGF5Qi5kaXNhYmxlZD1mYWxzZTtwYXlC"
    "LnRleHRDb250ZW50PSJOZXcgUmVxdWVzdCI7CiAgICBwYXlCLm9uY2xpY2s9KCk9PmRlcG9zaXRT"
    "aGVldChhKTsKICAgIHRvYXN0KENNUy5tc2dFeHBpcmVkKTsKICB9CiAgZGVwVGljaz1zZXRJbnRl"
    "cnZhbCgoKT0+ewogICAgaWYoIWFsaXZlKCkpe2NsZWFySW50ZXJ2YWwoZGVwVGljayk7cmV0dXJu"
    "O30KICAgIGxlZnQtLTtnKCJkcGNkIikudGV4dENvbnRlbnQ9Zm10KE1hdGgubWF4KDAsbGVmdCkp"
    "OwogICAgZygiZHBiYXIiKS5maXJzdENoaWxkLnN0eWxlLndpZHRoPShNYXRoLm1heCgwLGxlZnQp"
    "L3RvdGFsKjEwMCkrIiUiOwogICAgZygiZHBiYXIiKS5jbGFzc0xpc3QudG9nZ2xlKCJsb3ciLGxl"
    "ZnQ8PTYwKTsKICAgIGlmKGxlZnQ8PTApZXhwaXJlKCk7CiAgfSwxMDAwKTsKICBpZihwYXlCKXBh"
    "eUIub25jbGljaz0oKT0+ewogICAgaWYoZG9uZSlyZXR1cm47CiAgICBwYWlkPXRydWU7ZygiczIi"
    "KS5jbGFzc05hbWU9InN0ZXAgb24iO2coInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1biI7CiAgICBw"
    "YXlCLnRleHRDb250ZW50PSJQYXkgQWdhaW4iO3ZlckIuY2xhc3NMaXN0LnJlbW92ZSgiZ2xhc3Mi"
    "KTt2ZXJCLmNsYXNzTGlzdC5hZGQoImN0YSIpOwogICAgdG9hc3QoIk9wZW5pbmcgeW91ciBVUEkg"
    "YXBwIOKAlCBwYXkgIittb25leShhKSsiLCB0aGVuIHRhcCBWZXJpZnkiKTsKICB9OwogIHZlckIu"
    "b25jbGljaz0oKT0+ewogICAgaWYoZG9uZXx8YnVzeXx8bGVmdDw9MClyZXR1cm47CiAgICBidXN5"
    "PXRydWU7dmVyQi5kaXNhYmxlZD10cnVlO3ZlckIudGV4dENvbnRlbnQ9IkNoZWNraW5n4oCmIjsK"
    "ICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1biI7dG9hc3QoQ01TLm1zZ0NoZWNraW5nKTsK"
    "ICAgIGRlcEpvYj1zZXRUaW1lb3V0KCgpPT57CiAgICAgIGJ1c3k9ZmFsc2U7CiAgICAgIGlmKCFh"
    "bGl2ZSgpKXJldHVybjsKICAgICAgaWYocGFpZCl7CiAgICAgICAgZG9uZT10cnVlO2NsZWFySW50"
    "ZXJ2YWwoZGVwVGljayk7CiAgICAgICAgZygiczMiKS5jbGFzc05hbWU9InN0ZXAgb24iO2coInM0"
    "IikuY2xhc3NOYW1lPSJzdGVwIG9uIjsKICAgICAgICB2ZXJCLnRleHRDb250ZW50PSJWZXJpZmll"
    "ZCI7CiAgICAgICAgQkFMKz1OdW1iZXIoYSk7c3luY0JhbCgpO2J1cnN0KCk7dG9hc3QoQ01TLm1z"
    "Z09rKTsKICAgICAgICBzZXRUaW1lb3V0KGNsb3NlU2hlZXQsMTIwMCk7CiAgICAgIH1lbHNlewog"
    "ICAgICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIjt2ZXJCLmRpc2FibGVkPWZhbHNlO3ZlckIu"
    "dGV4dENvbnRlbnQ9Q01TLnZlcmlmeUJ0bjsKICAgICAgICB0b2FzdChDTVMubXNnTm9uZSk7CiAg"
    "ICAgIH0KICAgIH0sMTcwMCk7CiAgfTsKICB1dHJCLm9uY2xpY2s9KCk9PntpZihsZWZ0PjApdG9h"
    "c3QoIlRoYW5rcyDigJQgd2UgYXJlIGNoZWNraW5nIHlvdXIgcGF5bWVudCIpO307Cn0KZnVuY3Rp"
    "b24gc3luY0JhbCgpe3RpY2tUbyhkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYmFsIiksQkFMKTt0"
    "aWNrVG8oZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJhbDIiKSxCQUwpO30KZG9jdW1lbnQuYWRk"
    "RXZlbnRMaXN0ZW5lcigiY2xpY2siLGU9Pntjb25zdCBjPWUudGFyZ2V0LmNsb3Nlc3QoIltkYXRh"
    "LWNvbmZpcm1dIik7aWYoIWMpcmV0dXJuOwogIGNvbnN0IHA9K2MuZGF0YXNldC5jb25maXJtO2lm"
    "KEJBTDxwKXt0b2FzdCgiTm90IGVub3VnaCBiYWxhbmNlIOKAlCBhZGQgZnVuZHMiKTtyZXR1cm59"
    "CiAgQkFMLT1wO3N5bmNCYWwoKTtjbG9zZVNoZWV0KCk7YnVyc3QoKTt0b2FzdCgiUHVyY2hhc2Vk"
    "IOKAlCBPVFAgaXMgaW4gT3JkZXJzIik7fSk7CgovKiDilIDilIAgQURNSU4g4pSA4pSAICovCmxl"
    "dCBBVEFCPSJkYXNoIjsKZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI2FzdWIgYnV0dG9uIiku"
    "Zm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIj"
    "YXN1YiBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnJlbW92ZSgib24iKSk7Yi5jbGFz"
    "c0xpc3QuYWRkKCJvbiIpOwogIEFUQUI9Yi5kYXRhc2V0LmE7cmVuZGVyQWRtaW4oKTt3aW5kb3cu"
    "c2Nyb2xsVG8oMCwwKTt9KTsKZnVuY3Rpb24gcmVuZGVyQWRtaW4oKXsKICBjb25zdCBzdD1kb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgiYXN0YWdlIik7CiAgaWYoIWFkbWluQWxsb3dlZCgpKXtzdC5p"
    "bm5lckhUTUw9JzxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJtYXJnaW46MTZweDt0ZXh0LWFsaWdu"
    "OmNlbnRlciI+JwogICAgKyc8aDMgc3R5bGU9Im1hcmdpbjowIDAgNnB4Ij5BZG1pbnMgb25seTwv"
    "aDM+JwogICAgKyc8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPlRoaXMgcGFuZWwgaXMg"
    "bm90IGF2YWlsYWJsZSBvbiB5b3VyIGFjY291bnQuPC9wPjwvZGl2Pic7cmV0dXJuO30KICBzdC5p"
    "bm5lckhUTUw9KHtkYXNoOmFEYXNoLGNoYW5uZWxzOmFDaGFubmVscyxsb2dzOmFMb2dzLHBheTph"
    "UGF5LGNvbnRlbnQ6YUNvbnRlbnQsZmVhdHVyZXM6YUZlYXR1cmVzLG1hcmtldDphTWFya2V0LGFu"
    "YWx5dGljczphQW5hbHl0aWNzLHNlY3VyaXR5OmFTZWN1cml0eSxzdG9jazphU3RvY2ssb3JkZXJz"
    "OmFPcmRlcnMsdXNlcnM6YVVzZXJzLHNldHRpbmdzOmFTZXR0aW5nc30pW0FUQUJdKCk7CiAgYmlu"
    "ZEFkbWluKCk7cmV2ZWFsU3RhZ2Uoc3QpO2JpbmRUaWx0KCk7aWYod2luZG93Ll9fdnRhYlBsYWNl"
    "KXJlcXVlc3RBbmltYXRpb25GcmFtZSh3aW5kb3cuX192dGFiUGxhY2UpO30KCmZ1bmN0aW9uIGFE"
    "YXNoKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJrcGlzIj4ke1tbIlJldmVudWUgdG9kYXkiLG1v"
    "bmV5KEFETS5rcGkucmV2KV0sWyJPcmRlcnMgdG9kYXkiLGFkbU51bShBRE0ua3BpLm9yZGVycyld"
    "LAogICAgICBbIlVzZXJzIixhZG1OdW0oQURNLmtwaS51c2VycyldLFsiQXV0by12ZXJpZmllZCIs"
    "YWRtTnVtKEFETS5rcGkuYXV0byldXQogICAgLm1hcCgoW2ssdl0pPT5gPGRpdiBjbGFzcz0ia3Bp"
    "Ij48c21hbGw+JHtrfTwvc21hbGw+PGI+JHt2fTwvYj48L2Rpdj5gKS5qb2luKCIiKX08L2Rpdj4K"
    "ICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFzcz0i"
    "ZG90Ij48L2k+UGF5bWVudCBjaGVja2luZzwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7"
    "bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj4ke0FETS5wYXku"
    "Z21haWxfc2V0PyJHbWFpbCB3YXRjaGVyIG9uIMK3ICIrQURNLnBheS5wZW5kaW5nKyIgYXdhaXRp"
    "bmcgcmV2aWV3IjoiR21haWwgbm90IHNldCDigJQgZGVwb3NpdHMgZ28gdG8gbWFudWFsIHJldmll"
    "dyJ9PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtYS1nbz0icGF5"
    "Ij5PcGVuPC9idXR0b24+PC9kaXY+CiAgIDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5MYXN0IDcgZGF5"
    "czwvaDM+PHAgY2xhc3M9InN1YiI+UmV2ZW51ZSB0cmVuZDwvcD4KICAgICA8ZGl2IHN0eWxlPSJk"
    "aXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6ZmxleC1lbmQ7Z2FwOjdweDtoZWlnaHQ6OTZweDttYXJn"
    "aW4tdG9wOjE0cHgiPgogICAgICAgJHtBRE0uYmFycy5tYXAoaD0+YDxpIHN0eWxlPSJmbGV4OjE7"
    "aGVpZ2h0OiR7aH0lO2JvcmRlci1yYWRpdXM6N3B4IDdweCAzcHggM3B4O2JhY2tncm91bmQ6dmFy"
    "KC0tcGlsbGdyYWQpO2Rpc3BsYXk6YmxvY2siPjwvaT5gKS5qb2luKCIiKX08L2Rpdj48L2Rpdj4K"
    "ICAgJHtHKCJkaWdlc3QiLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpm"
    "bGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwg"
    "b24iPjxpIGNsYXNzPSJkb3QiPjwvaT5EaWdlc3Q8L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxl"
    "eDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+TmV4dCBz"
    "dW1tYXJ5IGF0IDIzOjMwPC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRh"
    "dGEtYS1nbz0iYW5hbHl0aWNzIj5WaWV3PC9idXR0b24+PC9kaXY+YCl9CiAgICR7Rygic2FuZGJv"
    "eCIsYDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRl"
    "bXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0icGlsbCI+U2FuZGJveDwvc3Bh"
    "bj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NHJlbTtj"
    "b2xvcjp2YXIoLS10eDIpIj4ke0FETS5zZXR0aW5ncy5zYW5kYm94PyJUZXN0IG1vZGUgaXMgT04i"
    "OiJUZXN0IG1vZGUgaXMgb2ZmIOKAlCByZWFsIG1vbmV5IGlzIGxpdmUifTwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuc2FuZGJveCk/IiBvbiI6IiJ9IiBkYXRhLXNl"
    "dHRpbmc9InNhbmRib3giPjwvZGl2PjwvZGl2PmApfQogICA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5R"
    "dWljayBBY3Rpb25zPC9oMj48L2Rpdj4KICAgPGRpdiBjbGFzcz0iZ3JpZCI+CiAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtcWE9ImJyb2FkY2FzdCI+QnJvYWRjYXN0PC9idXR0b24+"
    "CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtcWE9InN0b2NrIj5BZGQgU3RvY2s8"
    "L2J1dHRvbj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0YS1xYT0iZmxhc2giPkZs"
    "YXNoIFNhbGU8L2J1dHRvbj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0YS1xYT0i"
    "YmFja3VwIj5CYWNrdXAgREI8L2J1dHRvbj48L2Rpdj5gOwp9CgpmdW5jdGlvbiBhQ2hhbm5lbHMo"
    "KXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Gb3JjZS1qb2luIGNoYW5uZWxzPC9o"
    "Mz4KICAgIDxwIGNsYXNzPSJzdWIiPkFkZCBvciByZW1vdmUgYW55IG51bWJlciBvZiBjaGFubmVs"
    "cy4gU2F2ZWQgdG8gdGhlIGRhdGFiYXNlIOKAlCBpbnN0YW50LCBubyByZXN0YXJ0LjwvcD4KICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxM3B4Ij48bGFiZWw+Q2hhbm5l"
    "bCBsaW5rLCBAdXNlcm5hbWUgb3IgLTEwMCBJRDwvbGFiZWw+CiAgICAgIDxpbnB1dCBjbGFzcz0i"
    "aW5wIiBpZD0iY2gtaW4iIHBsYWNlaG9sZGVyPSJAY2hhbm5lbCDCtyB0Lm1lLytpbnZpdGUgwrcg"
    "LTEwMOKApiI+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlR5cGU8L2xhYmVs"
    "PgogICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIGlkPSJjaC10eXBlIj48b3B0aW9uIHZhbHVlPSIx"
    "Ij5SZXF1aXJlZCDigJQgbXVzdCBqb2luPC9vcHRpb24+PG9wdGlvbiB2YWx1ZT0iMCI+T3B0aW9u"
    "YWwg4oCUIGp1c3Qgc3VnZ2VzdGVkPC9vcHRpb24+PC9zZWxlY3Q+PC9kaXY+CiAgICA8ZGl2IGNs"
    "YXNzPSJidG5yb3ciPjxidXR0b24gY2xhc3M9ImJ0biIgaWQ9ImNoLWFkZCI+JHtpYygicGx1cyIp"
    "fSBBZGQgQ2hhbm5lbDwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IiBkYXRh"
    "LW1pc2M9ImNoYW5uZWx0ZXN0Ij5UZXN0IEJvdDwvYnV0dG9uPjwvZGl2PjwvZGl2PgogIDxkaXYg"
    "Y2xhc3M9InNlYyI+PGgyPkFjdGl2ZSAoJHtDSEFOTkVMUy5sZW5ndGh9KTwvaDI+PC9kaXY+CiAg"
    "JHtDSEFOTkVMUy5tYXAoKGMsaSk9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0Ij4KICAgICA8ZGl2"
    "IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICAg"
    "IDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoInNlbmQiKX08L2Rpdj4KICAgICAgIDxkaXYgc3R5bGU9"
    "ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODNyZW07ZGlzcGxheTpi"
    "bG9jazt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxs"
    "aXBzaXMiPiR7ZXNjKGMubmFtZSl9PC9iPgogICAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9yOnZh"
    "cigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+JHtlc2MoYy5oYW5kbGUpfSDCtyAke2MubWVtYmVy"
    "c30gbWVtYmVyczwvc21hbGw+PC9kaXY+CiAgICAgICA8ZGl2IGNsYXNzPSJ0ZyBzbSAke2Mub24/"
    "Im9uIjoiIn0iIGRhdGEtY2hvbj0iJHtjLmlkfSI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBzdHls"
    "ZT0iZGlzcGxheTpmbGV4O2dhcDo2cHg7bWFyZ2luLXRvcDoxMHB4O2ZsZXgtd3JhcDp3cmFwIj4K"
    "ICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyAke2MucmVxPyIiOiJzb2Z0In0iIGRhdGEtY2hy"
    "ZXE9IiR7Yy5pZH0iPiR7Yy5yZXE/IlJlcXVpcmVkIjoiT3B0aW9uYWwifTwvYnV0dG9uPgogICAg"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtY29weT0idGV4dCIgZGF0YS1jb3B5"
    "LXZhbHVlPSIke2VzYyhjLmhhbmRsZSl9Ij5Db3B5PC9idXR0b24+CiAgICAgICA8YnV0dG9uIGNs"
    "YXNzPSJidG4geHMgYmFkIiBzdHlsZT0ibWFyZ2luLWxlZnQ6YXV0byIgZGF0YS1jaGRlbD0iJHtj"
    "LmlkfSI+UmVtb3ZlPC9idXR0b24+PC9kaXY+PC9kaXY+YCkuam9pbigiIikKICAgfHwnPGRpdiBj"
    "bGFzcz0iY2FyZCI+PHAgY2xhc3M9InN1YiI+Tm8gY2hhbm5lbHMg4oCUIHVzZXJzIGNhbiBlbnRl"
    "ciBmcmVlbHkuPC9wPjwvZGl2Pid9YDsKfQoKZnVuY3Rpb24gYUxvZ3MoKXsKICBjb25zdCBvcHRz"
    "PWlkPT5MT0dDSC5tYXAobD0+YDxvcHRpb24gdmFsdWU9IiR7bC5pZH0iICR7bC5pZD09PWlkPyJz"
    "ZWxlY3RlZCI6IiJ9PiR7ZXNjKGwubmFtZSl9PC9vcHRpb24+YCkuam9pbigiIik7CiAgcmV0dXJu"
    "IGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10b3A6"
    "MCI+CiAgICAgIDxkaXYgY2xhc3M9InQiPkxvZ2dpbmcgbWFzdGVyIHN3aXRjaDxzbWFsbD5PZmYg"
    "PSB0aGUgYm90IHBvc3RzIG5vIGxvZ3MgYW55d2hlcmU8L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2"
    "IGNsYXNzPSJ0ZyAke0xPR1NfTUFTVEVSPyJvbiI6IiJ9IiBkYXRhLW1hc3Rlcj48L2Rpdj48L2Rp"
    "dj48L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxvZyBjaGFubmVsczwvaDM+PHAgY2xh"
    "c3M9InN1YiI+QWRkIGFzIG1hbnkgYXMgeW91IGxpa2UuIFRoZSBzdGFycmVkIG9uZSBpcyB0aGUg"
    "cHJlZmVycmVkL2RlZmF1bHQgY2hhbm5lbC48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0"
    "eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5DaGFubmVsIGxpbmssIEB1c2VybmFtZSBvciAt"
    "MTAwIElEPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0ibGctaW4iIHBsYWNl"
    "aG9sZGVyPSJAbG9nc19jaGFubmVsIMK3IC0xMDDigKYiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0ibGctYWRkIj4ke2ljKCJwbHVzIil9IEFk"
    "ZCBMb2cgQ2hhbm5lbDwvYnV0dG9uPgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRh"
    "LW1pc2M9ImxvZ3Rlc3QiPlNlbmQgVGVzdDwvYnV0dG9uPjwvZGl2PjwvZGl2PgogICAke0xPR0NI"
    "Lm1hcChsPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGln"
    "bi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICA8ZGl2IGNsYXNzPSJzdGFyICR7bC5wcmlt"
    "YXJ5PyJvbiI6IiJ9IiBkYXRhLWxnc3Rhcj0iJHtsLmlkfSI+JHtpYygic3RhciIpfTwvZGl2Pgog"
    "ICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6"
    "LjgzcmVtO2Rpc3BsYXk6YmxvY2s7d2hpdGUtc3BhY2U6bm93cmFwO292ZXJmbG93OmhpZGRlbjt0"
    "ZXh0LW92ZXJmbG93OmVsbGlwc2lzIj4ke2VzYyhsLm5hbWUpfTwvYj4KICAgICAgICA8c21hbGwg"
    "c3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+JHtlc2MobC5oYW5kbGUp"
    "fSR7bC5wcmltYXJ5PyIgwrcgcHJlZmVycmVkIjoiIn08L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2"
    "IGNsYXNzPSJ0ZyBzbSAke2wub24/Im9uIjoiIn0iIGRhdGEtbGdvbj0iJHtsLmlkfSI+PC9kaXY+"
    "CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBiYWQiIGRhdGEtbGdkZWw9IiR7bC5pZH0iPlJl"
    "bW92ZTwvYnV0dG9uPjwvZGl2PmApLmpvaW4oIiIpCiAgICB8fCc8ZGl2IGNsYXNzPSJjYXJkIj48"
    "cCBjbGFzcz0ic3ViIj5ObyBsb2cgY2hhbm5lbHMgeWV0LjwvcD48L2Rpdj4nfQogICA8ZGl2IGNs"
    "YXNzPSJzZWMiPjxoMj5FdmVudHMgJmFtcDsgcm91dGluZzwvaDI+PGEgZGF0YS1ldi1hbGw+VG9n"
    "Z2xlIGFsbDwvYT48L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+JHtFVkVOVFMubWFwKChlLGkp"
    "PT5gCiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCIgc3R5bGU9ImZsZXg6MSI+"
    "JHtlc2MoZS5rKX0KICAgICAgIDxzbWFsbD48c2VsZWN0IGNsYXNzPSJpbnAiIHN0eWxlPSJoZWln"
    "aHQ6MzBweDtmb250LXNpemU6LjdyZW07bWFyZ2luLXRvcDo1cHg7bWF4LXdpZHRoOjE5MHB4IiBk"
    "YXRhLWV2Y2g9IiR7aX0iICR7ZS5vbj8iIjoiZGlzYWJsZWQifT4ke29wdHMoZS5jaCl9PC9zZWxl"
    "Y3Q+PC9zbWFsbD48L2Rpdj4KICAgICAgIDxkaXYgY2xhc3M9InRnICR7ZS5vbj8ib24iOiIifSIg"
    "ZGF0YS1ldm9uPSIke2l9Ij48L2Rpdj48L2Rpdj5gKS5qb2luKCIiKX08L2Rpdj4KICAgPGRpdiBj"
    "bGFzcz0iY2FyZCI+PGgzPlJlY2VudCBsb2cgb3V0cHV0PC9oMz4KICAgICAkeygoQURNLmxvZ2Zl"
    "ZWQmJkFETS5sb2dmZWVkLmxlbmd0aCk/QURNLmxvZ2ZlZWQubWFwKGw9PltsLndoYXQsbC53aGVy"
    "ZSxsLndoZW5dKTpbXSkKICAgICAgIC5tYXAoKFt0LGMsd10pPT5gPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPiR7dH08L3NwYW4+PGIgc3R5bGU9ImZvbnQtc2l6ZTouN3JlbSI+JHtjfSDCtyAke3d9PC9i"
    "PjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PmA7Cn0KCmZ1bmN0aW9uIGFQYXkoKXsKICByZXR1cm4g"
    "YDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDow"
    "Ij4KICAgICAgPGRpdiBjbGFzcz0idCI+R21haWwgYXV0by12ZXJpZnk8c21hbGw+JHtBRE0ucGF5"
    "LmdtYWlsX3NldD8iUmVhZGluZyAiK2VzYyhBRE0ucGF5LmdtYWlsKToiTm90IGNvbmZpZ3VyZWQg"
    "4oCUIGRlcG9zaXRzIHF1ZXVlIGZvciByZXZpZXcifTwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYg"
    "Y2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuZ21haWxfZW5hYmxlZCk/IiBvbiI6IiJ9IiBkYXRh"
    "LXNldHRpbmc9ImdtYWlsX2VuYWJsZWQiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InN3"
    "YXAiPjxkaXYgY2xhc3M9InQiPkF1dG8tYXBwcm92ZSBzbWFsbCBkZXBvc2l0czxzbWFsbD5Ta2lw"
    "IHJldmlldyBiZWxvdyB0aGUgdGhyZXNob2xkPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNz"
    "PSJ0ZyR7KEFETS5zdyYmQURNLnN3LmdtYWlsX2VuYWJsZWQpPyIgb24iOiIifSIgZGF0YS1zZXR0"
    "aW5nPSJnbWFpbF9lbmFibGVkIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48"
    "ZGl2IGNsYXNzPSJ0Ij5NYW51YWwgVVRSIGZhbGxiYWNrPHNtYWxsPkxldCB1c2VycyBzdWJtaXQg"
    "YSBVVFIgaWYgYXV0by12ZXJpZnkgbWlzc2VzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNz"
    "PSJ0ZyR7KEFETS5zdyYmQURNLnN3LmF1dG9kZXApPyIgb24iOiIifSIgZGF0YS1zZXR0aW5nPSJh"
    "dXRvZGVwIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0"
    "Ij5WZXJpZnkgb25seSB3aGVuIHVzZXIgdGFwcyBWZXJpZnk8c21hbGw+Tm8gYmFja2dyb3VuZCBj"
    "aGVja2luZyDigJQgc2F2ZXMgR21haWwgcXVvdGE8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xh"
    "c3M9InRnJHsoQURNLnN3JiZBRE0uc3cudXRyX2ZhbGxiYWNrKT8iIG9uIjoiIn0iIGRhdGEtc2V0"
    "dGluZz0idXRyX2ZhbGxiYWNrIj48L2Rpdj48L2Rpdj48L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2Fy"
    "ZCI+PGgzPlVQSTwvaDM+PHAgY2xhc3M9InN1YiI+JHtBRE0ucGF5LnVwaT8iSW4gdXNlIGJ5IGV2"
    "ZXJ5IHBheW1lbnQgcmVxdWVzdC4iOiJOb3Qgc2V0IHlldCDigJQgdG9wLXVwcyBhcmUgZGlzYWJs"
    "ZWQgdW50aWwgeW91IGFkZCBpdC4ifTwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9"
    "Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlVQSSBJRDwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xh"
    "c3M9ImlucCIgZGF0YS1zZXQ9InVwaV9pZCIgdmFsdWU9IiR7ZXNjKEFETS5wYXkudXBpfHwiIil9"
    "IiBwbGFjZWhvbGRlcj0ieW91cmhhbmRsZUBiYW5rIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJm"
    "aWVsZCI+PGxhYmVsPlBheWVlIG5hbWU8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAi"
    "IGRhdGEtc2V0PSJ1cGlfbmFtZSIgdmFsdWU9IiR7ZXNjKEFETS5wYXkucGF5ZWV8fCIiKX0iIHBs"
    "YWNlaG9sZGVyPSJZb3VyIFN0b3JlIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxh"
    "YmVsPk5vdGUgcHJlZml4PC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNl"
    "dD0icGF5X3ByZWZpeCIgdmFsdWU9IiR7ZXNjKEFETS5wYXkucHJlZml4fHwiUEFZIil9Ij48L2Rp"
    "dj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlBheW1lbnQgd2luZG93IChtaW51dGVz"
    "KTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9InBheV93aW5kb3ci"
    "IHZhbHVlPSIke0FETS5wYXkud2luZG93fSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "PjxsYWJlbD5NaW5pbXVtIGRlcG9zaXQ8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAi"
    "IGRhdGEtc2V0PSJtaW5fZGVwb3NpdCIgdmFsdWU9IiR7QURNLnBheS5taW59Ij48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPk1heGltdW0gZGVwb3NpdDwvbGFiZWw+CiAgICAg"
    "ICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9Im1heF9kZXBvc2l0IiB2YWx1ZT0iJHtBRE0u"
    "cGF5Lm1heH0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+QXV0by1hcHBy"
    "b3ZlIHVuZGVyICgwID0gb2ZmKTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0"
    "YS1zZXQ9ImF1dG9kZXBfbWF4IiB2YWx1ZT0iJHtBRE0ucGF5LmF1dG9kZXB9Ij48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkdtYWlsIGFkZHJlc3M8L2xhYmVsPgogICAgICAg"
    "PGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJnbWFpbF9pZCIgdmFsdWU9IiR7ZXNjKEFETS5w"
    "YXkuZ21haWx8fCIiKX0iIHBsYWNlaG9sZGVyPSJzdG9yZUBnbWFpbC5jb20iPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+R21haWwgYXBwIHBhc3N3b3JkPC9sYWJlbD4KICAg"
    "ICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0iZ21haWxfcGFzcyIgdHlwZT0icGFzc3dv"
    "cmQiIHBsYWNlaG9sZGVyPSIke0FETS5wYXkuZ21haWxfc2V0PyJzYXZlZCDigJQgdHlwZSB0byBy"
    "ZXBsYWNlIjoibm90IHNldCJ9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+V2Fp"
    "dGluZyBmb3IgcmV2aWV3PC9zcGFuPjxiPiR7QURNLnBheS5wZW5kaW5nfTwvYj48L2Rpdj48L2Rp"
    "dj4KICAgJHtHKCJjcnlwdG8iLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+Q3J5cHRvIHdhbGxldDwv"
    "aDM+PHAgY2xhc3M9InN1YiI+VVNEVC1UUkMyMCBkZXBvc2l0cywgYXV0by1jb25maXJtZWQgb24t"
    "Y2hhaW4uPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4"
    "Ij48bGFiZWw+V2FsbGV0IGFkZHJlc3M8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiBwbGFjZWhv"
    "bGRlcj0iVOKApiI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5Db25maXJt"
    "YXRpb25zIHJlcXVpcmVkPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEiPjwvZGl2"
    "PgogICAgIDwvZGl2PmApfQogICAke0coInVwaVJvdGF0ZSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxo"
    "Mz5VUEkgcm90YXRpb248L2gzPjxwIGNsYXNzPSJzdWIiPlNwcmVhZCBsb2FkIGFuZCBzdXJ2aXZl"
    "IGEgZnJvemVuIGhhbmRsZS48L3A+CiAgICAgJHtbWyJoYW5kbGUxQGZhbSIsIkFjdGl2ZSJdLFsi"
    "aGFuZGxlMkBmYW0iLCJBY3RpdmUiXSxbImhhbmRsZTNAdXBpIiwiUGF1c2VkIl1dCiAgICAgICAu"
    "bWFwKChbaCxzdF0pPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7aH08L3NwYW4+PGI+PHNwYW4g"
    "Y2xhc3M9InBpbGwgJHtzdD09PSJBY3RpdmUiPyJvbiI6Im9mZiJ9Ij4ke3N0fTwvc3Bhbj48L2I+"
    "PC9kaXY+YCkuam9pbigiIil9CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjEwcHgiPjxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4Ij4KICAgICAgIDxpbnB1"
    "dCBjbGFzcz0iaW5wIiBwbGFjZWhvbGRlcj0iYW5vdGhlckB1cGkiPjxidXR0b24gY2xhc3M9ImJ0"
    "biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJ1cGlhZGQiPkFkZDwvYnV0dG9uPjwvZGl2"
    "PjwvZGl2PjwvZGl2PmApfQogICAke0coImRlcEJvbnVzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgz"
    "PkRlcG9zaXQgYm9udXM8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2lu"
    "LXRvcDo4cHgiPjxsYWJlbD7igrk1MDAgYW5kIGFib3ZlICglKTwvbGFiZWw+PGlucHV0IGNsYXNz"
    "PSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPuKC"
    "uTEwMDAgYW5kIGFib3ZlICglKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI4Ij48"
    "L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPuKCuTIwMDAgYW5kIGFib3ZlICgl"
    "KTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxMiI+PC9kaXY+PC9kaXY+YCl9CiAg"
    "ICR7RygiZGVwTGltaXRzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkRlcG9zaXQgbGltaXRzPC9o"
    "Mz4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+"
    "TWluaW11bSAo4oK5KTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxMCI+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5NYXhpbXVtICjigrkpPC9sYWJlbD48aW5w"
    "dXQgY2xhc3M9ImlucCIgdmFsdWU9IjIwMDAwIj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJ3cm9u"
    "Z0FtdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5n"
    "LXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5Xcm9uZy1hbW91bnQgaGFuZGxpbmc8c21hbGw+Q3JlZGl0"
    "IHdoYXQgd2FzIGFjdHVhbGx5IHBhaWQgYW5kIGZsYWcgaXQ8L3NtYWxsPjwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cudmVyaWZ5X29uX3RhcCk/IiBvbiI6IiJ9IiBk"
    "YXRhLXNldHRpbmc9InZlcmlmeV9vbl90YXAiPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0co"
    "InNwbGl0UGF5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBh"
    "ZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPlNwbGl0IHBheW1lbnQ8c21hbGw+V2FsbGV0IGJh"
    "bGFuY2UgcGx1cyBVUEkgaW4gb25lIGNoZWNrb3V0PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJ0ZyR7RkVBVC53cm9uZ0FtdD8iIG9uIjoiIn0iIGRhdGEtZmVhdD0id3JvbmdBbXQiPjwv"
    "ZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImF1dG9SZWZ1bmQiLGA8ZGl2IGNsYXNzPSJjYXJk"
    "Ij48aDM+QXV0by1yZWZ1bmQ8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFy"
    "Z2luLXRvcDo4cHgiPjxsYWJlbD5XYXJyYW50eSB3aW5kb3cgKG1pbnV0ZXMpPC9sYWJlbD48aW5w"
    "dXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNw"
    "YW4+UmVmdW5kZWQgdG9kYXk8L3NwYW4+PGI+4oK5MTA1LjAwIMK3IDMgb3JkZXJzPC9iPjwvZGl2"
    "PjwvZGl2PmApfQogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+R21haWwgYWNjb3VudDwvaDM+PHAg"
    "Y2xhc3M9InN1YiI+VGhlIEdtYWlsIHRoYXQgcmVjZWl2ZXMgeW91ciBGYW1QYXkgcGF5bWVudCBt"
    "YWlscy4gTm90aGluZyBpcyBzdG9yZWQgaW4gY29kZS48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmll"
    "bGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5HbWFpbCBJRDwvbGFiZWw+PGlucHV0"
    "IGNsYXNzPSJpbnAiIHBsYWNlaG9sZGVyPSJ5b3Vyc3RvcmVAZ21haWwuY29tIj48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkdtYWlsIHBhc3N3b3JkPC9sYWJlbD48aW5wdXQg"
    "Y2xhc3M9ImlucCIgdHlwZT0icGFzc3dvcmQiIHBsYWNlaG9sZGVyPSLigKLigKLigKLigKLigKLi"
    "gKLigKLigKLigKLigKLigKLigKIiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+PGJ1"
    "dHRvbiBjbGFzcz0iYnRuIiBkYXRhLWFkbWluLWFjdD0iZ21haWx0ZXN0Ij5UZXN0IEdtYWlsPC9i"
    "dXR0b24+CiAgICAgPC9kaXY+CiAgICAgPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTBweCI+VGlwOiBpZiAyLXN0ZXAgdmVyaWZpY2F0aW9uIGlzIG9uLCBwYXN0ZSBhIEdvb2dsZSBB"
    "cHAgUGFzc3dvcmQgaGVyZSBpbnN0ZWFkIG9mIHlvdXIgbm9ybWFsIG9uZS48L3A+PC9kaXY+CiAg"
    "IDxkaXYgY2xhc3M9InNlYyI+PGgyPlJlY2VudCBhdXRvLXZlcmlmaWNhdGlvbnM8L2gyPjwvZGl2"
    "PgogICAkeyhBRE0uZGVwb3NpdHN8fFtdKS5sZW5ndGg/QURNLmRlcG9zaXRzLm1hcChkPT5gPGRp"
    "diBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50"
    "ZXI7Z2FwOjEwcHgiPgogICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygid2FsbGV0Iil9PC9k"
    "aXY+CiAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250"
    "LXNpemU6LjgycmVtIj4ke21vbmV5KGQuYW10KX0gwrcgJHtlc2MoZC51c2VyKX08L2I+PGJyPgog"
    "ICAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+"
    "JHtlc2MoZC51dHJ8fCJubyByZWZlcmVuY2UgeWV0Iil9PC9zbWFsbD48L2Rpdj4KICAgICAgIDxz"
    "cGFuIGNsYXNzPSJwaWxsICR7ZC5zdD09PSJDb21wbGV0ZWQiPyJvbiI6ImhvdCJ9Ij4ke2VzYyhk"
    "LnN0KX08L3NwYW4+CiAgICAgICAke2Quc3Q9PT0iQ29tcGxldGVkIj8iIjpgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIHhzIiBkYXRhLWRlcC1vaz0iJHtkLmlkfSI+QXBwcm92ZTwvYnV0dG9uPmB9PC9kaXY+"
    "YCkuam9pbigiIikKICAgICA6JzxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiPjxwIGNsYXNzPSJzdWIi"
    "IHN0eWxlPSJtYXJnaW46MCI+Tm8gZGVwb3NpdHMgeWV0LjwvcD48L2Rpdj4nfWA7Cn0KCmZ1bmN0"
    "aW9uIGFDb250ZW50KCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+RWRpdCBhcHAg"
    "dGV4dDwvaDM+CiAgICAgPHAgY2xhc3M9InN1YiI+RXZlcnkgd29yZCB1c2VycyBzZWUg4oCUIGNo"
    "YW5nZSBpdCBoZXJlIGFuZCB0aGUgc3RvcmUgdXBkYXRlcyBpbnN0YW50bHkuPC9wPgogICAgIDxk"
    "aXYgY2xhc3M9Im1zZWFyY2giIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxkaXYgY2xhc3M9ImZp"
    "ZWxkd3JhcCI+JHtpYygic2VhcmNoIil9PGlucHV0IGlkPSJjcSIgcGxhY2Vob2xkZXI9IkZpbmQg"
    "dGV4dCB0byBlZGl04oCmIj48L2Rpdj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0"
    "IiBpZD0iY21zcmVzZXQiPlJlc2V0PC9idXR0b24+PC9kaXY+CiAgICAgPHAgY2xhc3M9InN1YiIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTBweCI+VXNlICZsdDticiZndDsgZm9yIGEgbGluZSBicmVhaywg"
    "Jmx0O2ImZ3Q7d29yZCZsdDsvYiZndDsgZm9yIGJvbGQuIHtufSA9IGxpdmUgY291bnRyeSBjb3Vu"
    "dCwge219ID0gcGF5bWVudCBtaW51dGVzLjwvcD48L2Rpdj4KICAgJHtDTVNNRVRBLm1hcCgoW2dy"
    "cCxyb3dzXSk9PmA8ZGl2IGNsYXNzPSJjYXJkIGNtc2dycCI+PGgzPiR7Z3JwfTwvaDM+CiAgICAg"
    "JHtyb3dzLm1hcCgoW2ssbGJsXSk9PmA8ZGl2IGNsYXNzPSJmaWVsZCBjbXNyb3ciIGRhdGEtZj0i"
    "JHtlc2MoKGxibCsiICIrQ01TW2tdKyIgIitncnApLnRvTG93ZXJDYXNlKCkpfSI+CiAgICAgICAg"
    "PGxhYmVsPiR7bGJsfTwvbGFiZWw+CiAgICAgICAgPHRleHRhcmVhIGNsYXNzPSJpbnAiIHJvd3M9"
    "IiR7Q01TW2tdLmxlbmd0aD40MD8yOjF9IiBkYXRhLWNtcz0iJHtrfSI+JHtlc2MoQ01TW2tdKX08"
    "L3RleHRhcmVhPjwvZGl2PmApLmpvaW4oIiIpfQogICA8L2Rpdj5gKS5qb2luKCIiKX0KICAgPGRp"
    "diBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50"
    "ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFzcz0iZG90Ij48"
    "L2k+TGl2ZSBlZGl0PC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtm"
    "b250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPkNoYW5nZXMgYXBwbHkgdG8gdGhlIFN0"
    "b3JlIHRhYiBzdHJhaWdodCBhd2F5PC9kaXY+CiAgICAgPC9kaXY+YDsKfQoKZnVuY3Rpb24gYUZl"
    "YXR1cmVzKCl7CiAgY29uc3Qgb249T2JqZWN0LnZhbHVlcyhGRUFUKS5maWx0ZXIoQm9vbGVhbiku"
    "bGVuZ3RoLHRvdD1PYmplY3Qua2V5cyhGRUFUKS5sZW5ndGg7CiAgcmV0dXJuIGA8ZGl2IGNsYXNz"
    "PSJjYXJkIj48aDM+RmVhdHVyZSBzd2l0Y2hlczwvaDM+CiAgICAgPHAgY2xhc3M9InN1YiI+VHVy"
    "biBhbnl0aGluZyBvZmYgYW5kIGl0IGRpc2FwcGVhcnMgZnJvbSB0aGUgYXBwIGNvbXBsZXRlbHkg"
    "4oCUIG5vIGJ1dHRvbnMsIG5vIG1lbnVzLCBubyBsZWZ0b3ZlcnMuPC9wPgogICAgIDxkaXYgY2xh"
    "c3M9Imt2Ij48c3Bhbj5FbmFibGVkPC9zcGFuPjxiIGlkPSJmY291bnQiPiR7b259IG9mICR7dG90"
    "fTwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJtc2VhcmNoIiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "MnB4Ij48ZGl2IGNsYXNzPSJmaWVsZHdyYXAiPiR7aWMoInNlYXJjaCIpfTxpbnB1dCBpZD0iZnEi"
    "IHBsYWNlaG9sZGVyPSJTZWFyY2ggZmVhdHVyZXPigKYiPjwvZGl2PgogICAgICAgPGJ1dHRvbiBj"
    "bGFzcz0iYnRuIHNtIHNvZnQiIGlkPSJmYWxsIj5BbGwgb248L2J1dHRvbj48L2Rpdj4KICAgICA8"
    "ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGJ1dHRvbiBjbGFzcz0i"
    "YnRuIHNtIGdob3N0IiBpZD0iZm5vbmUiPkFsbCBvZmY8L2J1dHRvbj4KICAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBzbSBzb2Z0IiBpZD0iZnJlc2V0Ij5SZXNldDwvYnV0dG9uPjwvZGl2PjwvZGl2"
    "PgogICAke0ZHUk9VUFMubWFwKChbZ3JwLHJvd3NdKT0+YDxkaXYgY2xhc3M9ImNhcmQgZmdycCI+"
    "PGgzPiR7Z3JwfTwvaDM+CiAgICAgJHtyb3dzLm1hcCgoW2ssbCxkXSk9PmA8ZGl2IGNsYXNzPSJm"
    "Y2FyZCBmcm93IiBkYXRhLWY9IiR7ZXNjKChsKyIgIitkKyIgIitncnApLnRvTG93ZXJDYXNlKCkp"
    "fSI+CiAgICAgICAgPGRpdiBjbGFzcz0idCI+PGI+JHtsfTwvYj48c21hbGw+JHtkfTwvc21hbGw+"
    "PC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0idGcgJHtGRUFUW2tdPyJvbiI6IiJ9IiBkYXRhLWZl"
    "YXQ9IiR7a30iPjwvZGl2PjwvZGl2PmApLmpvaW4oIiIpfQogICA8L2Rpdj5gKS5qb2luKCIiKX0K"
    "ICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFzcz0i"
    "ZG90Ij48L2k+TGl2ZTwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7"
    "Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj5Td2l0Y2hlcyBhcHBseSB0byB0aGUg"
    "U3RvcmUgdGFiIGluc3RhbnRseTwvZGl2PgogICAgIDwvZGl2PmA7Cn0KCmZ1bmN0aW9uIGFNYXJr"
    "ZXQoKXsKICByZXR1cm4gYCR7RygiY291cG9ucyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Db3Vw"
    "b25zPC9oMz48cCBjbGFzcz0ic3ViIj5QZXJjZW50YWdlIG9mZiBhdCBjaGVja291dC48L3A+CiAg"
    "ICAgJHsoQURNLmNvdXBvbnMmJkFETS5jb3Vwb25zLmxlbmd0aCk/QURNLmNvdXBvbnMubWFwKGM9"
    "PmA8ZGl2IGNsYXNzPSJrdiI+CiAgICAgICA8c3Bhbj48YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgp"
    "Ij4ke2VzYyhjLmNvZGUpfTwvYj4gwrcgJHtjLnBlcmNlbnR9JSDCtyAke2MudXNlZH0vJHtjLm1h"
    "eHx8IuKIniJ9PC9zcGFuPgogICAgICAgPGI+PHNwYW4gY2xhc3M9InBpbGwgJHtjLmFjdGl2ZT8i"
    "b24iOiJvZmYifSI+JHtjLmFjdGl2ZT8iQWN0aXZlIjoiT2ZmIn08L3NwYW4+PC9iPjwvZGl2PmAp"
    "LmpvaW4oIiIpCiAgICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gY291"
    "cG9ucyB5ZXQuPC9wPid9CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9w"
    "OjEycHgiPjxsYWJlbD5OZXcgY29kZTwvbGFiZWw+CiAgICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5"
    "OmZsZXg7Z2FwOjdweCI+PGlucHV0IGNsYXNzPSJpbnAiIGlkPSJjcGNvZGUiIHBsYWNlaG9sZGVy"
    "PSJORVdZRUFSIj4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0iY3BwY3QiIHBsYWNlaG9s"
    "ZGVyPSIlIiBzdHlsZT0id2lkdGg6NjZweCI+CiAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20i"
    "IHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0iY3BhZGQiPkFkZDwvYnV0dG9uPjwvZGl2PjwvZGl2"
    "PjwvZGl2PmApfQogICAke0coImZsYXNoU2FsZSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5GbGFz"
    "aCBzYWxlIHNjaGVkdWxlcjwvaDM+PHAgY2xhc3M9InN1YiI+U3RhcnRzLCBhbm5vdW5jZXMgYW5k"
    "IGVuZHMgYnkgaXRzZWxmLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdp"
    "bi10b3A6MTJweCI+PGxhYmVsPkRpc2NvdW50ICglKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAi"
    "IHZhbHVlPSIyMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5TdGFydHM8"
    "L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iVG9kYXkgMTg6MDAiPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RW5kczwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAi"
    "IHZhbHVlPSJUb2RheSAyMzowMCI+PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2Nr"
    "IiBpZD0iZmxhc2hnbyI+U3RhcnQgRmxhc2ggU2FsZTwvYnV0dG9uPjwvZGl2PmApfQogICAke0co"
    "InNjaGVkQnJvYWRjYXN0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlNjaGVkdWxlZCBicm9hZGNh"
    "c3Q8L2gzPjxwIGNsYXNzPSJzdWIiPlF1ZXVlIGl0IGFuZCB0YXJnZXQgYSBzZWdtZW50LjwvcD4K"
    "ICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlNl"
    "Z21lbnQ8L2xhYmVsPgogICAgICAgPHNlbGVjdCBjbGFzcz0iaW5wIj48b3B0aW9uPkV2ZXJ5b25l"
    "PC9vcHRpb24+PG9wdGlvbj5CdXllcnMgb25seTwvb3B0aW9uPjxvcHRpb24+TmV2ZXIgYm91Z2h0"
    "PC9vcHRpb24+PG9wdGlvbj5CaWcgc3BlbmRlcnMgKOKCuTEwMDArKTwvb3B0aW9uPjxvcHRpb24+"
    "SW5hY3RpdmUgMzAgZGF5czwvb3B0aW9uPjwvc2VsZWN0PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "ImZpZWxkIj48bGFiZWw+U2VuZCBhdDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSJU"
    "b21vcnJvdyAxMDowMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5NZXNz"
    "YWdlPC9sYWJlbD48dGV4dGFyZWEgY2xhc3M9ImlucCIgcm93cz0iMiIgcGxhY2Vob2xkZXI9Ildy"
    "aXRlIHlvdXIgYnJvYWRjYXN04oCmIj48L3RleHRhcmVhPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0iYmNnbyI+U2VuZCBCcm9hZGNhc3Q8L2J1"
    "dHRvbj4KICAgICAgIDwvZGl2PjwvZGl2PmApfQogICAke0coIndpbmJhY2siLGA8ZGl2IGNsYXNz"
    "PSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFz"
    "cz0idCI+V2luLWJhY2sgbWVzc2FnZXM8c21hbGw+QXV0by1ETSB1c2VycyBpbmFjdGl2ZSBmb3Ig"
    "NyBhbmQgMzAgZGF5czwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGcke0ZFQVQuc3Bs"
    "aXRQYXk/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InNwbGl0UGF5Ij48L2Rpdj48L2Rpdj4KICAgICA8"
    "ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkRpc2NvdW50IG9mZmVyZWQgKCUpPC9sYWJlbD48aW5w"
    "dXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwIj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJhYmFuZG9u"
    "UmVtaW5kIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGln"
    "bi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBj"
    "bGFzcz0iZG90Ij48L2k+UmVtaW5kZXJzPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTtt"
    "aW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPjQgdW5maW5pc2hl"
    "ZCBwYXltZW50cyBudWRnZWQgdG9kYXk8L2Rpdj4KICAgICA8L2Rpdj5gKX0KICAgJHtHKCJsb3lh"
    "bHR5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxveWFsdHkgcG9pbnRzPC9oMz4KICAgICA8ZGl2"
    "IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+UG9pbnRzIHBlciDi"
    "grkxMCBzcGVudDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxIj48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlBvaW50cyBuZWVkZWQgZm9yIOKCuTEwPC9sYWJl"
    "bD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwMCI+PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9IndpbmJhY2tzYXZlIj5TYXZlPC9i"
    "dXR0b24+PC9kaXY+YCl9CiAgICR7YW55RigicmVmRGFzaCIsInJlZkwyIik/YDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxoMz5SZWZlcnJhbCBwYXlvdXRzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIg"
    "c3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+TGV2ZWwgMSBjb21taXNzaW9uICglKTwvbGFi"
    "ZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj4KICAgICAke0coInJlZkwyIixg"
    "PGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5MZXZlbCAyIGNvbW1pc3Npb24gKCUpPC9sYWJlbD48"
    "aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEiPjwvZGl2PmApfQogICAgIDxkaXYgY2xhc3M9Imt2"
    "Ij48c3Bhbj5QYWlkIG91dCB0aGlzIG1vbnRoPC9zcGFuPjxiPuKCuTMsMjQwPC9iPjwvZGl2Pjwv"
    "ZGl2PmA6IiJ9CiAgICR7Rygic2FsZXNGZWVkIixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFz"
    "cz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPkF1dG8tcG9zdCBz"
    "YWxlcyB0byBjaGFubmVsPHNtYWxsPiR7RkVFRC5sZW5ndGg/ZXNjKEZFRURbMF1bMV0rIiBzb2xk"
    "IMK3ICIrRkVFRFswXVszXSk6Im5vIHNhbGVzIHlldCJ9PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJ0ZyR7RkVBVC53aW5iYWNrPyIgb24iOiIifSIgZGF0YS1mZWF0PSJ3aW5iYWNrIj48"
    "L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJsZWFkZXJib2FyZCIsYDxkaXYgY2xhc3M9ImNh"
    "cmQiPjxoMz5MZWFkZXJib2FyZCBwcml6ZTwvaDM+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0"
    "eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPldlZWtseSBwcml6ZSAo4oK5KTwvbGFiZWw+PGlu"
    "cHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1MDAiPjwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJwcml6ZXNhdmUiPlNhdmU8L2J1dHRvbj48"
    "L2Rpdj5gKX1gOwp9CgpmdW5jdGlvbiBhQW5hbHl0aWNzKCl7CiAgY29uc3Qgd2Vlaz0oQURNLnNl"
    "cmllc3x8W10pLnJlZHVjZSgoYSxiKT0+YStiLDApOwogIGNvbnN0IGF2Zz1BRE0ua3BpLm9yZGVy"
    "cz9BRE0ua3BpLnJldi9BRE0ua3BpLm9yZGVyczowOwogIHJldHVybiBgJHtHKCJyZXZDaGFydCIs"
    "YDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5SZXZlbnVlPC9oMz48cCBjbGFzcz0ic3ViIj5MYXN0IDcg"
    "ZGF5cyDCtyAke21vbmV5KHdlZWspfSB0b3RhbDwvcD4KICAgICA8ZGl2IGNsYXNzPSJiYXJzIj4k"
    "e0FETS5iYXJzLm1hcChoPT5gPGkgc3R5bGU9ImhlaWdodDoke2h9JSI+PC9pPmApLmpvaW4oIiIp"
    "fTwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImtwaXMiIHN0eWxlPSJtYXJnaW4tdG9wOjE0cHgiPiR7"
    "W1siVG9kYXkiLG1vbmV5KEFETS5rcGkucmV2KV0sWyJUaGlzIG1vbnRoIixtb25leShBRE0ubWFy"
    "a2V0Lm1vbnRoKV0sWyJBdmcgb3JkZXIiLG1vbmV5KGF2ZyldXQogICAgICAgLm1hcCgoW2ssdl0p"
    "PT5gPGRpdiBjbGFzcz0ia3BpIj48c21hbGw+JHtrfTwvc21hbGw+PGI+JHt2fTwvYj48L2Rpdj5g"
    "KS5qb2luKCIiKX08L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJmdW5uZWwiLGA8ZGl2IGNsYXNzPSJj"
    "YXJkIj48aDM+Q29udmVyc2lvbiBmdW5uZWw8L2gzPjxwIGNsYXNzPSJzdWIiPldoZXJlIHBlb3Bs"
    "ZSBkcm9wIG9mZjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmdW5uZWwiPiR7KCgpPT57Y29uc3QgZj1B"
    "RE0uYW5hbHl0aWNzLmZ1bm5lbCx0PU1hdGgubWF4KDEsZi51c2Vycyk7CiAgICAgICByZXR1cm4g"
    "W1siU3RhcnRlZCBib3QiLGYudXNlcnMsMTAwXSxbIkRlcG9zaXRlZCIsZi5kZXAsTWF0aC5yb3Vu"
    "ZChmLmRlcC90KjEwMCldLFsiQm91Z2h0IixmLmJ1eSxNYXRoLnJvdW5kKGYuYnV5L3QqMTAwKV1d"
    "CiAgICAgICAubWFwKChbayx2LHddKT0+YDxkaXY+PGkgc3R5bGU9IndpZHRoOiR7TWF0aC5tYXgo"
    "NCx3KX0lIj48L2k+PHNwYW4gc3R5bGU9IndoaXRlLXNwYWNlOm5vd3JhcCI+JHtrfSDCtyA8Yj4k"
    "e3Z9PC9iPjwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKTt9KSgpfTwvZGl2PjwvZGl2PmApfQogICAk"
    "e0coInByb2ZpdFJlcCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5QZXItY291bnRyeSBwcm9maXQ8"
    "L2gzPgogICAgICR7QURNLmFuYWx5dGljcy5jb3VudHJ5Lmxlbmd0aD9BRE0uYW5hbHl0aWNzLmNv"
    "dW50cnkubWFwKChbYyxuLHN1bV0pPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7ZXNjKGMpfSDC"
    "tyAke259IHNvbGQ8L3NwYW4+PGIgc3R5bGU9ImNvbG9yOnZhcigtLW9rKSI+JHttb25leShzdW0p"
    "fTwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgOic8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFy"
    "Z2luOjAiPk5vIHNhbGVzIHlldC48L3A+J30KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2sg"
    "c29mdCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1hZG1pbi1hY3Q9ImV4cG9ydCI+RXhw"
    "b3J0IENTVjwvYnV0dG9uPjwvZGl2PmApfQogICAke0coImNsdiIsYDxkaXYgY2xhc3M9ImNhcmQi"
    "PjxoMz5DdXN0b21lciB2YWx1ZTwvaDM+CiAgICAgJHtBRE0udXNlcnMubGVuZ3RoP0FETS51c2Vy"
    "cy5zbGljZSgwLDUpLm1hcCh1PT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPjxiIHN0eWxlPSJjb2xv"
    "cjp2YXIoLS10eCkiPiR7ZXNjKHUubmFtZSl9PC9iPiDCtyAke3Uub3JkZXJzfSBvcmRlcnM8L3Nw"
    "YW4+CiAgICAgICAgIDxiPiR7bW9uZXkodS5zcGVudCl9IDxzcGFuIGNsYXNzPSJwaWxsICR7dS5z"
    "cGVudD4wPyJvbiI6ImhvdCJ9Ij4ke3Uuc3BlbnQ+MD8iYWN0aXZlIjoibm8gc3BlbmQifTwvc3Bh"
    "bj48L2I+PC9kaXY+YCkuam9pbigiIikKICAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1h"
    "cmdpbjowIj5ObyBjdXN0b21lcnMgeWV0LjwvcD4nfTwvZGl2PmApfQogICAke0coImRpZ2VzdCIs"
    "YDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDow"
    "Ij48ZGl2IGNsYXNzPSJ0Ij5EYWlseSBkaWdlc3Q8c21hbGw+RW5kLW9mLWRheSBzdW1tYXJ5IHRv"
    "IHRoZSBhZG1pbiBjaGF0PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5z"
    "YWxlc0ZlZWQ/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InNhbGVzRmVlZCI+PC9kaXY+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5TZW5kIGF0PC9sYWJlbD48aW5wdXQgY2xhc3M9"
    "ImlucCIgdmFsdWU9IjIzOjMwIj48L2Rpdj48L2Rpdj5gKX1gOwp9CgpmdW5jdGlvbiBhU2VjdXJp"
    "dHkoKXsKICByZXR1cm4gYCR7RygicmF0ZUxpbWl0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlJh"
    "dGUgbGltaXRpbmc8L2gzPjxwIGNsYXNzPSJzdWIiPlN0b3BzIG9uZSB1c2VyIGhhbW1lcmluZyB0"
    "aGUgYm90LjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTBw"
    "eCI+PGxhYmVsPk1heCBhY3Rpb25zIHBlciBtaW51dGU8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5w"
    "IiB2YWx1ZT0iMjAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+Q29vbGRv"
    "d24gYWZ0ZXIgbGltaXQgKHMpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjYwIj48"
    "L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJtdWx0aUFjYyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYg"
    "Y2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5NdWx0aS1h"
    "Y2NvdW50IGRldGVjdGlvbjxzbWFsbD5GbGFncyByZWZlcnJhbCBmYXJtaW5nIHBhdHRlcm5zPC9z"
    "bWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5tdWx0aUFjYz8iIG9uIjoiIn0i"
    "IGRhdGEtZmVhdD0ibXVsdGlBY2MiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48"
    "c3Bhbj5GbGFnZ2VkIHRoaXMgd2Vlazwvc3Bhbj48Yj43IGFjY291bnRzPC9iPjwvZGl2PgogICAg"
    "IDxidXR0b24gY2xhc3M9ImJ0biBibG9jayBzb2Z0IiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4IiBk"
    "YXRhLWEtZ289Im9yZGVycyI+UmV2aWV3IEZsYWdnZWQ8L2J1dHRvbj48L2Rpdj5gKX0KICAgJHtH"
    "KCJ2ZWxvY2l0eSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJw"
    "YWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5QdXJjaGFzZSB2ZWxvY2l0eSBhbGVydHM8c21h"
    "bGw+Q2F0Y2hlcyBzdG9sZW4gdG9wLXVwczwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "dGcke0ZFQVQudmVsb2NpdHk/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InZlbG9jaXR5Ij48L2Rpdj48"
    "L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkFsZXJ0IGFib3ZlIE4gYnV5cyAv"
    "IDEwIG1pbjwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj48L2Rpdj5g"
    "KX0KICAgJHtHKCJhZG1pbjJmYSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5BZG1pbiAyRkE8L2gz"
    "PjxwIGNsYXNzPSJzdWIiPlJlcXVpcmVkIGJlZm9yZSBiYWxhbmNlIGVkaXRzLCBicm9hZGNhc3Rz"
    "IGFuZCB3aXBlcy48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9w"
    "OjEwcHgiPjxsYWJlbD5NZXRob2Q8L2xhYmVsPjxzZWxlY3QgY2xhc3M9ImlucCI+PG9wdGlvbj5Q"
    "SU48L29wdGlvbj48b3B0aW9uPlRPVFAgYXBwPC9vcHRpb24+PC9zZWxlY3Q+PC9kaXY+CiAgICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBkYXRhLWZlYXQ9ImFkbWluMmZhIj5TZXQgVXA8L2J1dHRv"
    "bj48L2Rpdj5gKX0KICAgJHtHKCJhdXRvQmFuIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkF1dG8t"
    "YmFuIHJ1bGVzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5DaGFy"
    "Z2ViYWNrPHNtYWxsPkJhbiBpbnN0YW50bHk8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "InRnJHsoQURNLnN3JiZBRE0uc3cuYmFuX2NoYXJnZWJhY2spPyIgb24iOiIifSIgZGF0YS1zZXR0"
    "aW5nPSJiYW5fY2hhcmdlYmFjayI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+"
    "PGRpdiBjbGFzcz0idCI+RmFrZSBVVFIgc3VibWl0dGVkPHNtYWxsPkJhbiBhZnRlciAyIGF0dGVt"
    "cHRzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmJh"
    "bl9mYWtlX3V0cik/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImJhbl9mYWtlX3V0ciI+PC9kaXY+"
    "PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+UmVwZWF0ZWQgZmFp"
    "bGVkIHZlcmlmaWNhdGlvbjxzbWFsbD5CYW4gYWZ0ZXIgNTwvc21hbGw+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5iYW5fZmFpbGVkX3ZlcmlmeSk/IiBvbiI6IiJ9"
    "IiBkYXRhLXNldHRpbmc9ImJhbl9mYWlsZWRfdmVyaWZ5Ij48L2Rpdj48L2Rpdj48L2Rpdj5gKX0K"
    "ICAgJHtHKCJibGFja2xpc3QiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QmxhY2tsaXN0PC9oMz48"
    "cCBjbGFzcz0ic3ViIj5Vc2VyIElEcywgcGhvbmUgbnVtYmVycyBhbmQgVVBJIHJlZmVyZW5jZXMu"
    "PC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4Ij48ZGl2"
    "IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjdweCI+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIg"
    "aWQ9ImJsLWluIiBwbGFjZWhvbGRlcj0iSUQsIHBob25lIG9yIFVQSSByZWYiPjxidXR0b24gY2xh"
    "c3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJibGJsb2NrIj5CbG9jazwvYnV0"
    "dG9uPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5CbG9ja2VkIGVudHJp"
    "ZXM8L3NwYW4+PGI+NDI8L2I+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiZW5jcnlwdCIsYDxkaXYg"
    "Y2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2"
    "IGNsYXNzPSJ0Ij5FbmNyeXB0IHNlc3Npb25zIGF0IHJlc3Q8c21hbGw+QSBsZWFrZWQgZGF0YWJh"
    "c2UgZmlsZSBzdGF5cyB1c2VsZXNzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7"
    "RkVBVC5lbmNyeXB0PyIgb24iOiIifSIgZGF0YS1mZWF0PSJlbmNyeXB0Ij48L2Rpdj48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+S2V5IHN0YXR1czwvc3Bhbj48YiBzdHlsZT0iY29s"
    "b3I6dmFyKC0tb2spIj5Mb2FkZWQgZnJvbSBlbnY8L2I+PC9kaXY+PC9kaXY+YCl9YDsKfQoKZnVu"
    "Y3Rpb24gYVN0b2NrKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+VXBsb2FkIHN0"
    "b2NrPC9oMz48cCBjbGFzcz0ic3ViIj5QYXN0ZSBzZXNzaW9ucyBvciB1cGxvYWQgYSBmaWxlIOKA"
    "lCBvbmUgcGVyIGxpbmUuPC9wPgo8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTJweCI+PGxhYmVsPlBhc3RlIGFjY291bnRzIOKAlCBvbmUgcGVyIGxpbmU8L2xhYmVsPgogICAg"
    "ICA8dGV4dGFyZWEgY2xhc3M9ImlucCIgaWQ9InN0cGFzdGUiIHJvd3M9IjQiCiAgICAgICAgcGxh"
    "Y2Vob2xkZXI9IkluZGlhIHwgKzkxIHwgMzUgfCBzZXNzaW9uJiMxMDtLZW55YSB8ICsyNTQgfCA3"
    "NyB8IHNlc3Npb24iPjwvdGV4dGFyZWE+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciPjxi"
    "dXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJzdHNhdmUiPkltcG9ydDwvYnV0dG9uPgogICAgICA8"
    "bGFiZWwgY2xhc3M9ImJ0biBzb2Z0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNl"
    "bnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2N1cnNvcjpwb2ludGVyIj4KICAgICAgICBVcGxv"
    "YWQgLnR4dDxpbnB1dCB0eXBlPSJmaWxlIiBpZD0ic3RmaWxlIiBhY2NlcHQ9Ii50eHQsLmNzdiIg"
    "aGlkZGVuPjwvbGFiZWw+PC9kaXY+CiAgICA8cCBjbGFzcz0ic3ViIiBpZD0ic3Rtc2ciIHN0eWxl"
    "PSJtYXJnaW46OHB4IDAgMDtmb250LXNpemU6LjcycmVtIj48L3A+CiAgICAke0coImJ1bGtVcGxv"
    "YWQiLGA8ZGl2IGNsYXNzPSJzdGVwcyIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+CiAgICAgIDxk"
    "aXYgY2xhc3M9InN0ZXAgb24iPjxpPjE8L2k+JHtBRE0uYW5hbHl0aWNzLnN0b2NrX3RvdGFsfSBh"
    "dmFpbGFibGUgbm93PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAgb24iPjxpPjI8L2k+JHtB"
    "RE0uYW5hbHl0aWNzLnNvbGRfdG90YWx9IHNvbGQgc28gZmFyPC9kaXY+CiAgICAgIDxkaXYgY2xh"
    "c3M9InN0ZXAiPjxpPjM8L2k+QWRkIG1vcmUgZnJvbSB0aGUgYm90OiBBZG1pbiDihpIgU3RvY2s8"
    "L2Rpdj48L2Rpdj5gKX0KICAgICR7RygiZHVwRGV0ZWN0IixgPHAgY2xhc3M9InN1YiIgc3R5bGU9"
    "Im1hcmdpbjoxMHB4IDAgMDtmb250LXNpemU6LjdyZW0iPkR1cGxpY2F0ZSBwaG9uZXMgYW5kIHNl"
    "c3Npb25zIGFyZSByZWplY3RlZCBhdXRvbWF0aWNhbGx5LjwvcD5gKX0KICAgICR7RygiaGVhbHRo"
    "Q2hlY2siLGA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5WYWxpZGF0ZSBiZWZvcmUg"
    "c2VsbGluZzxzbWFsbD5DaGVjayB0aGUgc2Vzc2lvbiBpcyBhbGl2ZSBhdCBwdXJjaGFzZSB0aW1l"
    "PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5oZWFsdGhDaGVjaz8iIG9u"
    "IjoiIn0iIGRhdGEtZmVhdD0iaGVhbHRoQ2hlY2siPjwvZGl2PjwvZGl2PmApfTwvZGl2PgogICAk"
    "e0coImxvd1N0b2NrIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxvdy1zdG9jayBhbGVydHM8L2gz"
    "PjxwIGNsYXNzPSJzdWIiPlBlci1jb3VudHJ5IHRocmVzaG9sZCDigJQgcGluZyB3aGVuIGl0IGRy"
    "b3BzIGJlbG93LjwvcD4KICAgICAkeyhBRE0uc3RvY2subGVuZ3RoP0FETS5zdG9jazpTVE9DSyku"
    "c2xpY2UoMCw2KS5tYXAocz0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj4ke3MuZn0gJHtlc2Mocy5j"
    "KX0gwrcgJHtzLnF0eXx8MH0gbGVmdDwvc3Bhbj4KICAgICAgIDxiPjxpbnB1dCBjbGFzcz0iaW5w"
    "IiBzdHlsZT0iaGVpZ2h0OjMwcHg7d2lkdGg6NjRweDt0ZXh0LWFsaWduOmNlbnRlcjtmb250LXNp"
    "emU6Ljc0cmVtIiB2YWx1ZT0iJHtzLmxvd3x8MTB9Ij48L2I+PC9kaXY+YCkuam9pbigiIikKICAg"
    "ICAgIHx8JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gc3RvY2sgeWV0LjwvcD4n"
    "fTwvZGl2PmApfQogICAke0coInNlbGxIb3VycyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5TZWxs"
    "aW5nIGhvdXJzPC9oMz48cCBjbGFzcz0ic3ViIj5Db3VudHJpZXMgaGlkZSB0aGVtc2VsdmVzIG91"
    "dHNpZGUgdGhlIHdpbmRvdy48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjhweCI+PGxhYmVsPk9wZW48L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0i"
    "MDk6MDAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+Q2xvc2U8L2xhYmVs"
    "PjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMDI6MDAiPjwvZGl2PjwvZGl2PmApfQogICAke0co"
    "ImR5blByaWNlIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkR5bmFtaWMgcHJpY2luZzwvaDM+CiAg"
    "ICAgPGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQi"
    "PlJhaXNlIHByaWNlIHdoZW4gc3RvY2sgaXMgbG93PHNtYWxsPisxMCUgdW5kZXIgMjAgdW5pdHM8"
    "L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULmR5blByaWNlPyIgb24iOiIi"
    "fSIgZGF0YS1mZWF0PSJkeW5QcmljZSI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ic3dh"
    "cCI+PGRpdiBjbGFzcz0idCI+TmlnaHQgZGlzY291bnQ8c21hbGw+4oiSNSUgYmV0d2VlbiAwMDow"
    "MCBhbmQgMDY6MDA8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZB"
    "RE0uc3cubmlnaHRfZGlzY291bnQpPyIgb24iOiIifSIgZGF0YS1zZXR0aW5nPSJuaWdodF9kaXNj"
    "b3VudCI+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiYnVsa0Rpc2MiLGA8ZGl2IGNsYXNz"
    "PSJjYXJkIj48aDM+QnVsayBkaXNjb3VudHM8L2gzPgogICAgIDxwIGNsYXNzPSJzdWIiPlBlcmNl"
    "bnQgb2ZmIHdoZW4gdGhlIGJ1eWVyIHRha2VzIHNldmVyYWwgYXQgb25jZS48L3A+CiAgICAgPGRp"
    "diBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxsYWJlbD41IG9yIG1vcmUg"
    "KCUpPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXByaWNlPSJidWxrXzUi"
    "IHZhbHVlPSIke0FETS5wcmljaW5nP0FETS5wcmljaW5nLmJ1bGtfNTo1fSI+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD4xMCBvciBtb3JlICglKTwvbGFiZWw+CiAgICAgICA8"
    "aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1wcmljZT0iYnVsa18xMCIgdmFsdWU9IiR7QURNLnByaWNp"
    "bmc/QURNLnByaWNpbmcuYnVsa18xMDoxMH0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxk"
    "Ij48bGFiZWw+MjUgb3IgbW9yZSAoJSk8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAi"
    "IGRhdGEtcHJpY2U9ImJ1bGtfMjUiIHZhbHVlPSIke0FETS5wcmljaW5nP0FETS5wcmljaW5nLmJ1"
    "bGtfMjU6MTV9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkRlZmF1bHQg"
    "cHJpY2UgZm9yIG5ldyBzdG9jazwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0"
    "YS1wcmljZT0iZGVmYXVsdF9wcmljZSIgdmFsdWU9IiR7QURNLnByaWNpbmc/QURNLnByaWNpbmcu"
    "ZGVmYXVsdF9wcmljZTozNX0iPjwvZGl2PgogICAgIDwvZGl2PmApfQogICAke0coInZhcmlhbnRz"
    "IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlZhcmlhbnRzPC9oMz48cCBjbGFzcz0ic3ViIj5Qcmlj"
    "ZWQgc2VwYXJhdGVseSBvbiBldmVyeSBjb3VudHJ5LjwvcD4KICAgICA8ZGl2IGNsYXNzPSJrdiI+"
    "PHNwYW4+MkZBIGVuYWJsZWQ8L3NwYW4+PGI+K+KCuTU8L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFz"
    "cz0ia3YiPjxzcGFuPldpdGggbWFpbDwvc3Bhbj48Yj4r4oK5ODwvYj48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJrdiI+PHNwYW4+U3BhbS1mcmVlIGd1YXJhbnRlZTwvc3Bhbj48Yj4r4oK5NDwvYj48"
    "L2Rpdj48L2Rpdj5gKX0KICAgPGRpdiBjbGFzcz0ic2VjIj48aDI+SW52ZW50b3J5PC9oMj48YSBj"
    "bGFzcz0ic3ViIj50YXAgYSBwcmljZSB0byBjaGFuZ2UgaXQ8L2E+PC9kaXY+CiAgICR7U1RPQ0su"
    "bWFwKHM9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtzLmZ9PC9k"
    "aXY+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1z"
    "aXplOi44M3JlbSI+JHtzLmN9PC9iPjxicj4KICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFy"
    "KC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4ke3MucXR5fSBhdmFpbGFibGU8L3NtYWxsPjwvZGl2"
    "PgogICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLWNwcmljZT0iJHtlc2Mocy5jKX0iIHZhbHVl"
    "PSIke3MucHJpY2V9IiBzdHlsZT0iaGVpZ2h0OjMycHg7d2lkdGg6NzhweDt0ZXh0LWFsaWduOmNl"
    "bnRlcjtmb250LXNpemU6Ljc2cmVtIj4KICAgICAKICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMg"
    "YmFkIiBkYXRhLW1pc2M9ImNsZWFyc3RvY2siIGRhdGEtbWlzYy12YWx1ZT0iJHtlc2Mocy5jKX0i"
    "PkNsZWFyPC9idXR0b24+PC9kaXY+YCkuam9pbigiIil9YDsKfQpmdW5jdGlvbiBhT3JkZXJzKCl7"
    "CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5SZWNlbnQgT3JkZXJzPC9oMj48YSBkYXRh"
    "LWFkbWluLWFjdD0iZXhwb3J0Ij5FeHBvcnQ8L2E+PC9kaXY+CiAgICR7QURNLm9yZGVycy5sZW5n"
    "dGg/QURNLm9yZGVycy5tYXAobz0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNw"
    "bGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8ZGl2IGNsYXNzPSJm"
    "bGFnIj4ke28uZn08L2Rpdj48ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxl"
    "PSJmb250LXNpemU6LjgycmVtIj4ke2VzYyhvLmlkKX08L2I+PGJyPgogICAgIDxzbWFsbCBzdHls"
    "ZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4ke2VzYyhvLnVzZXIpfSDCtyAk"
    "e21vbmV5KG8ucHJpY2UpfSDCtyAke2VzYyhvLndoZW4pfTwvc21hbGw+PC9kaXY+CiAgICAgPHNw"
    "YW4gY2xhc3M9InBpbGwgJHtvLnN0PT09IkRlbGl2ZXJlZCI/Im9uIjoib2ZmIn0iPiR7ZXNjKG8u"
    "c3QpfTwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKQogICAgIDonPGRpdiBjbGFzcz0iY2FyZCB0aWdo"
    "dCI+PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBvcmRlcnMgeWV0LjwvcD48L2Rp"
    "dj4nfQogICAke0coImRpc3B1dGUiLGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5EaXNwdXRlczwvaDI+"
    "PC9kaXY+CiAgICAgJHtBRE0uZGlzcHV0ZXMubGVuZ3RoP0FETS5kaXNwdXRlcy5tYXAoZD0+YDxk"
    "aXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2Vu"
    "dGVyO2dhcDoxMHB4Ij4KICAgICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygic2hpZWxkIil9"
    "PC9kaXY+CiAgICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9"
    "ImZvbnQtc2l6ZTouOHJlbSI+JHtlc2MoZC5pZCl9IMK3ICR7ZXNjKGQudXNlcil9PC9iPjxicj48"
    "c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OHJlbSI+JHtlc2MoZC5y"
    "ZWFzb24pfTwvc21hbGw+PC9kaXY+CiAgICAgICAgIDxzcGFuIGNsYXNzPSJwaWxsICR7ZC5zdGF0"
    "dXM9PT0iT3BlbiI/ImhvdCI6Im9uIn0iPiR7ZXNjKGQuc3RhdHVzKX08L3NwYW4+PC9kaXY+YCku"
    "am9pbigiIikKICAgICAgIDonPGRpdiBjbGFzcz0iY2FyZCB0aWdodCI+PHAgY2xhc3M9InN1YiIg"
    "c3R5bGU9Im1hcmdpbjowIj5ObyBkaXNwdXRlcy48L3A+PC9kaXY+J31gKX0KICAgJHtHKCJ3YXJy"
    "YW50eSIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPlJlcGxhY2VtZW50IGNsYWltczwvaDI+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1p"
    "dGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygic2hp"
    "ZWxkIil9PC9kaXY+CiAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0"
    "eWxlPSJmb250LXNpemU6LjhyZW0iPjIgY2xhaW1zIGluc2lkZSB3YXJyYW50eTwvYj48YnI+CiAg"
    "ICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY4cmVtIj5B"
    "dXRvLXJlcGxhY2VkIGZyb20gc3RvY2s8L3NtYWxsPjwvZGl2PgogICAgICAgPC9kaXY+YCl9CiAg"
    "IAogICAke0coInJldmlld3MiLGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5SZXZpZXdzPC9oMj48L2Rp"
    "dj4KICAgICAkeyhBRE0ucmV2aWV3cy5sZW5ndGg/QURNLnJldmlld3MubWFwKHI9PltyLnVzZXIs"
    "ci5jLHIuc3RhcnMsci50ZXh0XSk6W10pCiAgICAgICAubWFwKChbdSxjLHIsdF0pPT5gPGRpdiBj"
    "bGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7"
    "Z2FwOjEwcHgiPgogICAgICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJzdGFyIil9PC9kaXY+"
    "CiAgICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQt"
    "c2l6ZTouOHJlbSI+JHt1fSDCtyAke2N9IMK3ICR7cn08L2I+PGJyPgogICAgICAgICAgIDxzbWFs"
    "bCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY4cmVtIj4ke3R9PC9zbWFsbD48"
    "L2Rpdj4KICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtcmV2PSIke3Iu"
    "aWR9Ij5IaWRlPC9idXR0b24+PC9kaXY+YCkuam9pbigiIil9YCl9YDsKfQpmdW5jdGlvbiBhVXNl"
    "cnMoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImZpZWxkIj48aW5wdXQgY2xhc3M9ImlucCIgcGxh"
    "Y2Vob2xkZXI9IlNlYXJjaCBieSBJRCBvciB1c2VybmFtZSI+PC9kaXY+CiAgICR7QURNLnVzZXJz"
    "Lmxlbmd0aD9BRE0udXNlcnMubWFwKHU9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0i"
    "ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgIDxkaXYgY2xh"
    "c3M9ImZsYWciPiR7aWMoInVzZXIiKX08L2Rpdj4KICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21p"
    "bi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44MnJlbSI+JHtlc2ModS5uYW1lKX08L2I+"
    "PGJyPgogICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5"
    "cmVtIj4ke21vbmV5KHUuc3BlbnQpfSBzcGVudCDCtyAke3Uub3JkZXJzfSBvcmRlcnMgwrcgJHtt"
    "b25leSh1LmJhbGFuY2UpfSBiYWxhbmNlJHt1LmJhbm5lZD8iIMK3IGJhbm5lZCI6IiJ9PC9zbWFs"
    "bD48L2Rpdj4KICAgICAgPHNwYW4gY2xhc3M9InBpbGwgJHt1LmJhbm5lZD8ib2ZmIjoib24ifSI+"
    "JHtlc2ModS50aWVyKX08L3NwYW4+PC9kaXY+YCkuam9pbigiIikKICAgICA6JzxkaXYgY2xhc3M9"
    "ImNhcmQgdGlnaHQiPjxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gdXNlcnMgeWV0"
    "LjwvcD48L2Rpdj4nfQogICAke0coImFkbWluUm9sZXMiLGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5B"
    "ZG1pbiByb2xlczwvaDI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iY2FyZCI+CiAgICAgICAke0FE"
    "TS5hZG1pbnMubGVuZ3RoP0FETS5hZG1pbnMubWFwKGE9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+"
    "PGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHtlc2MoYS5sYWJlbCl9PC9iPiDCtyAke2VzYyhh"
    "LnBlcm1zKX08L3NwYW4+PGI+PHNwYW4gY2xhc3M9InBpbGwgb24iPiR7ZXNjKGEucm9sZSl9PC9z"
    "cGFuPjwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxl"
    "PSJtYXJnaW46MCI+T25seSB5b3Ugc28gZmFyLjwvcD4nfQogICAgICAgPGRpdiBjbGFzcz0iZmll"
    "bGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6"
    "N3B4Ij4KICAgICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIHBsYWNlaG9sZGVyPSJAdXNlcm5hbWUi"
    "PjxzZWxlY3QgY2xhc3M9ImlucCIgc3R5bGU9IndpZHRoOmF1dG8iPjxvcHRpb24+TWFuYWdlcjwv"
    "b3B0aW9uPjxvcHRpb24+U3VwcG9ydDwvb3B0aW9uPjwvc2VsZWN0PgogICAgICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0iYWRtaW5hZGQiPkFkZDwv"
    "YnV0dG9uPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImJ1eWVyQmFkZ2UiLGA8ZGl2IGNs"
    "YXNzPSJjYXJkIj48aDM+VmVyaWZpZWQgYnV5ZXIgYmFkZ2U8L2gzPgogICAgIDxkaXYgY2xhc3M9"
    "ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5PcmRlcnMgcmVxdWlyZWQ8L2xh"
    "YmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iNSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "a3YiPjxzcGFuPlZlcmlmaWVkIHVzZXJzPC9zcGFuPjxiPjg0PC9iPjwvZGl2PjwvZGl2PmApfQog"
    "ICAke0coIm11bHRpQWNjIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6"
    "ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxs"
    "IGhvdCI+NyBmbGFnZ2VkPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6"
    "MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlBvc3NpYmxlIG11bHRpLWFjY291"
    "bnRzIHRoaXMgd2VlazwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRh"
    "LWEtZ289InNlY3VyaXR5Ij5SZXZpZXc8L2J1dHRvbj48L2Rpdj5gKX1gOwp9CmZ1bmN0aW9uIGFT"
    "ZXR0aW5ncygpewogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlN0b3JlPC9oMz48cCBj"
    "bGFzcz0ic3ViIj5FdmVyeXRoaW5nIGxpdmVzIGluIHRoZSBkYXRhYmFzZSDigJQgbm8gY29kZSBl"
    "ZGl0cywgbm8gcmVzdGFydC48L3A+CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdp"
    "bi10b3A6MTJweCI+PGxhYmVsPlN0b3JlIG5hbWU8L2xhYmVsPgogICAgICA8aW5wdXQgY2xhc3M9"
    "ImlucCIgZGF0YS1zZXQ9InN0b3JlX25hbWUiIHZhbHVlPSIke2VzYyhDTVMuc3RvcmVOYW1lfHxD"
    "RkcubmFtZSl9Ij48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+VGFnbGluZTwv"
    "bGFiZWw+CiAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0ic3RvcmVfdGFnbGluZSIg"
    "dmFsdWU9IiR7ZXNjKENNUy5zdG9yZVRhZ3x8Q0ZHLnRhZyl9Ij48L2Rpdj4KICAgIDxkaXYgY2xh"
    "c3M9ImZpZWxkIj48bGFiZWw+VGFnbGluZTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVl"
    "PSIke0NGRy50YWd9Ij48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+U3VwcG9y"
    "dCBjb250YWN0PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgcGxhY2Vob2xkZXI9IkB5b3VyX3N1"
    "cHBvcnQiPjwvZGl2PgogICAgPC9kaXY+CiAgIDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Ub2dnbGVz"
    "PC9oMz4KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPkZvcmNlIGpvaW48c21h"
    "bGw+UmVxdWlyZSBjaGFubmVsIG1lbWJlcnNoaXA8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xh"
    "c3M9InRnJHsoQURNLnN3JiZBRE0uc3cuZm9yY2Vfc3ViKT8iIG9uIjoiIn0iIGRhdGEtc2V0dGlu"
    "Zz0iZm9yY2Vfc3ViIj48L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xh"
    "c3M9InQiPk1haW50ZW5hbmNlIG1vZGU8c21hbGw+RnJlZXplIHRoZSBzdG9yZTwvc21hbGw+PC9k"
    "aXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5tYWludGVuYW5jZSk/IiBv"
    "biI6IiJ9IiBkYXRhLXNldHRpbmc9Im1haW50ZW5hbmNlIj48L2Rpdj48L2Rpdj4KICAgIDxkaXYg"
    "Y2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPkF1dG8gcmVmdW5kczxzbWFsbD5SZWZ1bmQgZmFp"
    "bGVkIGxvZ2luczwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGcke0ZFQVQuYXV0b1Jl"
    "ZnVuZD8iIG9uIjoiIn0iIGRhdGEtZmVhdD0iYXV0b1JlZnVuZCI+PC9kaXY+PC9kaXY+CiAgICA8"
    "ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5SZWZlcnJhbCBwcm9ncmFtPHNtYWxsPjEl"
    "IG9mIGludml0ZWQgZGVwb3NpdHM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtG"
    "RUFULnJlZkRhc2g/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InJlZkRhc2giPjwvZGl2PjwvZGl2Pjwv"
    "ZGl2PgogICAke0coImkxOG4iLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+TGFuZ3VhZ2VzPC9oMz48"
    "cCBjbGFzcz0ic3ViIj5Vc2VycyBwaWNrIHRoZWlyIG93bjsgZXZlcnkgc3RyaW5nIGlzIHRyYW5z"
    "bGF0ZWQuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RGVmYXVsdCBsYW5ndWFn"
    "ZTwvbGFiZWw+CiAgICAgIDxzZWxlY3QgY2xhc3M9ImlucCIgZGF0YS1zZXRzZWw9ImxhbmdfZGVm"
    "YXVsdCI+CiAgICAgICAgPG9wdGlvbiB2YWx1ZT0iZW4iICR7KEFETS5zZXR0aW5ncyYmQURNLnNl"
    "dHRpbmdzLmxhbmc9PT0iZW4iKT8ic2VsZWN0ZWQiOiIifT5FbmdsaXNoPC9vcHRpb24+CiAgICAg"
    "ICAgPG9wdGlvbiB2YWx1ZT0iaGkiICR7KEFETS5zZXR0aW5ncyYmQURNLnNldHRpbmdzLmxhbmc9"
    "PT0iaGkiKT8ic2VsZWN0ZWQiOiIifT7gpLngpL/gpKjgpY3gpKbgpYA8L29wdGlvbj4KICAgICAg"
    "PC9zZWxlY3Q+PC9kaXY+PC9kaXY+YCl9CiAgICR7Rygic2xhc2hDbWRzIixgPGRpdiBjbGFzcz0i"
    "Y2FyZCI+PGgzPlNsYXNoIGNvbW1hbmRzPC9oMz48cCBjbGFzcz0ic3ViIj5SZWdpc3RlcmVkIGlu"
    "IFRlbGVncmFtJ3MgY29tbWFuZCBtZW51LjwvcD4KICAgICAke1tbIi9zdGFydCIsIk9wZW4gdGhl"
    "IHN0b3JlIl0sWyIvYnV5IiwiSnVtcCB0byBzdG9jayJdLFsiL2JhbGFuY2UiLCJTaG93IHdhbGxl"
    "dCJdLFsiL29yZGVycyIsIk15IG9yZGVycyJdLFsiL3N1cHBvcnQiLCJDb250YWN0IHN1cHBvcnQi"
    "XV0KICAgICAgIC5tYXAoKFtjLGRdKT0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj48YiBzdHlsZT0i"
    "Y29sb3I6dmFyKC0tdHgpIj4ke2N9PC9iPjwvc3Bhbj48YiBzdHlsZT0iZm9udC13ZWlnaHQ6NTAw"
    "O2ZvbnQtc2l6ZTouNzRyZW0iPiR7ZH08L2I+PC9kaXY+YCkuam9pbigiIil9CiAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIGJsb2NrIHNvZnQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiIGRhdGEtYWRt"
    "aW4tYWN0PSJzeW5jIj5TeW5jIENvbW1hbmRzPC9idXR0b24+PC9kaXY+YCl9CiAgICR7RygiYXV0"
    "b0JhY2t1cCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5BdXRvLWJhY2t1cDwvaDM+PHAgY2xhc3M9"
    "InN1YiI+RGF0YWJhc2UgdG8gYSBwcml2YXRlIGNoYW5uZWwgb24gYSBzY2hlZHVsZS48L3A+CiAg"
    "ICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPkV2ZXJ5"
    "IChob3Vycyk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iNiI+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxhc3QgYmFja3VwPC9zcGFuPjxiPjQyIG1pbiBhZ288L2I+"
    "PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4Ij48"
    "YnV0dG9uIGNsYXNzPSJidG4iIGRhdGEtYWRtaW4tYWN0PSJiYWNrdXAiPkJhY2sgVXAgTm93PC9i"
    "dXR0b24+CiAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtYWRtaW4tYWN0PSJy"
    "ZXN0b3JlIj5SZXN0b3JlPC9idXR0b24+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiYXVkaXRMb2ci"
    "LGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QXVkaXQgbG9nPC9oMz48cCBjbGFzcz0ic3ViIj5FdmVy"
    "eSBhZG1pbiBhY3Rpb24sIHdpdGggdW5kby48L3A+CiAgICAgJHsoKEFETS5hdWRpdCYmQURNLmF1"
    "ZGl0Lmxlbmd0aCk/QURNLmF1ZGl0Lm1hcChhPT5bYS53aG8sKGEud2hhdCsiICIrYS5kZXRhaWwp"
    "LnRyaW0oKSxhLndoZW5dKTpbXSkKICAgICAgIC5tYXAoKFt1LGEsdF0pPT5gPGRpdiBjbGFzcz0i"
    "a3YiPjxzcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7dX08L2I+ICR7YX08L3NwYW4+"
    "CiAgICAgICAgIDxiPiR7dH0gPC9iPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PmApfQogICAke0co"
    "InNhbmRib3giLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFk"
    "ZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+U2FuZGJveCAvIHRlc3QgbW9kZTxzbWFsbD5GYWtl"
    "IG1vbmV5LCBubyByZWFsIGRlbGl2ZXJ5PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0"
    "ZyR7KEFETS5zdyYmQURNLnN3LnNhbmRib3gpPyIgb24iOiIifSIgZGF0YS1zZXR0aW5nPSJzYW5k"
    "Ym94Ij48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJlbmNyeXB0IixgPGRpdiBjbGFzcz0i"
    "Y2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEw"
    "cHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFzcz0iZG90Ij48L2k+RW5jcnlw"
    "dGVkPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6"
    "Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlNlc3Npb25zIGFyZSBlbmNyeXB0ZWQgYXQgcmVzdDwv"
    "ZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWEtZ289InNlY3VyaXR5"
    "Ij5LZXlzPC9idXR0b24+PC9kaXY+YCl9CiAgIDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5EYW5nZXIg"
    "em9uZTwvaDM+PHAgY2xhc3M9InN1YiI+SXJyZXZlcnNpYmxlLiBBc2sgdHdpY2UuPC9wPgogICAg"
    "PGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48YnV0dG9uIGNsYXNz"
    "PSJidG4geHMgc29mdCIgZGF0YS1hZG1pbi1hY3Q9ImJhY2t1cCI+QmFja3VwPC9idXR0b24+CiAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4gYmFkIGhvbGQiIGRhdGEtZG9uZT0iRGF0YWJhc2UgcmVzZXQg"
    "KGRlbW8pIj48c3BhbiBjbGFzcz0iZmlsbCI+PC9zcGFuPkhvbGQgdG8gUmVzZXQgREI8L2J1dHRv"
    "bj48L2Rpdj48L2Rpdj5gOwp9CgpmdW5jdGlvbiBiaW5kQWRtaW4oKXsKICBiaW5kQ29tbW9uKCk7"
    "CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZmVhdF0iKS5mb3JFYWNoKHQ9PnQu"
    "b25jbGljaz0oKT0+ewogICAgY29uc3Qgaz10LmRhdGFzZXQuZmVhdDtGRUFUW2tdPSFGRUFUW2td"
    "O3QuY2xhc3NMaXN0LnRvZ2dsZSgib24iLEZFQVRba10pOwogICAgcHVzaCgiL2FwaS9hZG1pbi9m"
    "ZWF0dXJlIix7a2V5Omssb246RkVBVFtrXX0pOwogICAgY29uc3QgYz1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiZmNvdW50Iik7CiAgICBpZihjKWMudGV4dENvbnRlbnQ9T2JqZWN0LnZhbHVlcyhG"
    "RUFUKS5maWx0ZXIoQm9vbGVhbikubGVuZ3RoKyIgb2YgIitPYmplY3Qua2V5cyhGRUFUKS5sZW5n"
    "dGg7CiAgICBhcHBseUZlYXR1cmVzKCk7CiAgICBjb25zdCBjYXJkPXQuY2xvc2VzdCgiLmZjYXJk"
    "IiksIG5hbWVFbD1jYXJkP2NhcmQucXVlcnlTZWxlY3RvcigiYiIpOm51bGw7CiAgICBjb25zdCBs"
    "YWJlbD1uYW1lRWw/bmFtZUVsLnRleHRDb250ZW50OmsucmVwbGFjZSgvKFtBLVpdKS9nLCIgJDEi"
    "KS50b0xvd2VyQ2FzZSgpOwogICAgdG9hc3QobGFiZWwrKEZFQVRba10/IiBlbmFibGVkIjoiIGhp"
    "ZGRlbiIpKTt9KTsKICBjb25zdCBmcT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZnEiKTsKICBp"
    "ZihmcSlmcS5vbmlucHV0PSgpPT57Y29uc3QgcT1mcS52YWx1ZS50cmltKCkudG9Mb3dlckNhc2Uo"
    "KTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5mcm93IikuZm9yRWFjaChyPT5yLmNs"
    "YXNzTGlzdC50b2dnbGUoImhpZGUiLCEhcSYmIXIuZGF0YXNldC5mLmluY2x1ZGVzKHEpKSk7CiAg"
    "ICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIuZmdycCIpLmZvckVhY2goZz0+Zy5jbGFzc0xp"
    "c3QudG9nZ2xlKCJoaWRlIiwhZy5xdWVyeVNlbGVjdG9yKCIuZnJvdzpub3QoLmhpZGUpIikpKTt9"
    "OwogIGNvbnN0IHNldEFsbD12PT57T2JqZWN0LmtleXMoRkVBVCkuZm9yRWFjaChrPT5GRUFUW2td"
    "PXYpO2FwcGx5RmVhdHVyZXMoKTtyZW5kZXJBZG1pbigpO3RvYXN0KHY/IkFsbCBmZWF0dXJlcyBl"
    "bmFibGVkIjoiQWxsIGZlYXR1cmVzIGhpZGRlbiIpO307CiAgY29uc3QgZmE9ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoImZhbGwiKSxmbj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZm5vbmUiKSxm"
    "cj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZnJlc2V0Iik7CiAgaWYoZmEpZmEub25jbGljaz0o"
    "KT0+c2V0QWxsKHRydWUpOwogIGlmKGZuKWZuLm9uY2xpY2s9KCk9PnNldEFsbChmYWxzZSk7CiAg"
    "aWYoZnIpZnIub25jbGljaz0oKT0+e09iamVjdC5hc3NpZ24oRkVBVCxGRUFUMCk7YXBwbHlGZWF0"
    "dXJlcygpO3JlbmRlckFkbWluKCk7dG9hc3QoIkZlYXR1cmVzIHJlc2V0Iik7fTsKICBjb25zdCBz"
    "dG1zZz1tPT57Y29uc3QgZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3Rtc2ciKTtpZihlKWUu"
    "dGV4dENvbnRlbnQ9bTt9OwogIGFzeW5jIGZ1bmN0aW9uIGltcG9ydFN0b2NrKHRleHQpewogICAg"
    "aWYoIXRleHQudHJpbSgpKXt0b2FzdCgiUGFzdGUgc29tZSBsaW5lcyBmaXJzdCIpO3JldHVybjt9"
    "CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBzdG1zZygi"
    "SW1wb3J0aW5n4oCmIik7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zdG9j"
    "ayIse3RleHQ6dGV4dH0pOwogICAgaWYoIXIpe3N0bXNnKCIiKTtyZXR1cm47fQogICAgY29uc3Qg"
    "bm90ZT1yLmFkZGVkKyIgaW1wb3J0ZWQiKyhyLnNraXBwZWQmJnIuc2tpcHBlZC5sZW5ndGg/KCIg"
    "wrcgIityLnNraXBwZWQubGVuZ3RoKyIgc2tpcHBlZCIpOiIiKTsKICAgIHRvYXN0KHIuYWRkZWQr"
    "IiBhY2NvdW50cyBhZGRlZCIpOwogICAgY29uc3QgYm94PWRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJzdHBhc3RlIik7aWYoYm94KWJveC52YWx1ZT0iIjsKICAgIGF3YWl0IHJlZnJlc2goKTsKICAg"
    "IHN0bXNnKG5vdGUpOwogIH0KICBjb25zdCBzdHNhdmU9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "InN0c2F2ZSIpOwogIGlmKHN0c2F2ZSlzdHNhdmUub25jbGljaz0oKT0+aW1wb3J0U3RvY2soKGRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdHBhc3RlIil8fHt9KS52YWx1ZXx8IiIpOwogIGNvbnN0"
    "IHN0ZmlsZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RmaWxlIik7CiAgaWYoc3RmaWxlKXN0"
    "ZmlsZS5vbmNoYW5nZT0oKT0+ewogICAgY29uc3QgZj1zdGZpbGUuZmlsZXMmJnN0ZmlsZS5maWxl"
    "c1swXTtpZighZilyZXR1cm47CiAgICBjb25zdCByZD1uZXcgRmlsZVJlYWRlcigpOwogICAgcmQu"
    "b25sb2FkPSgpPT57Y29uc3QgYm94PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdHBhc3RlIik7"
    "CiAgICAgIGlmKGJveClib3gudmFsdWU9cmQucmVzdWx0O3N0bXNnKGYubmFtZSsiIGxvYWRlZCDi"
    "gJQgcHJlc3MgSW1wb3J0Iik7fTsKICAgIHJkLnJlYWRBc1RleHQoZik7CiAgfTsKICBjb25zdCBi"
    "bD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYmxibG9jayIpOwogIGlmKGJsKWJsLm9uY2xpY2s9"
    "YXN5bmMoKT0+ewogICAgY29uc3QgaT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYmwtaW4iKTsK"
    "ICAgIGNvbnN0IHY9aT8oaS52YWx1ZXx8IiIpLnRyaW0oKToiIjsKICAgIGlmKCF2KXt0b2FzdCgi"
    "VHlwZSBhbiBpZCwgcGhvbmUgb3IgVVBJIHJlZmVyZW5jZSIpO2lmKGkpaS5mb2N1cygpO3JldHVy"
    "bjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBjb25z"
    "dCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9ibGFja2xpc3QiLHthY3Rpb246ImFkZCIsdmFs"
    "dWU6dn0pOwogICAgaWYoIXIpcmV0dXJuOwogICAgaS52YWx1ZT0iIjt0b2FzdCgiQmxvY2tlZCAi"
    "K3YpO2F3YWl0IHJlZnJlc2goKTsKICB9OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltk"
    "YXRhLWJsLWRlbF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBy"
    "PWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9ibGFja2xpc3QiLHthY3Rpb246ImRlbCIsaWQ6K2Iu"
    "ZGF0YXNldC5ibERlbH0pOwogICAgaWYocil7dG9hc3QoIlVuYmxvY2tlZCIpO2F3YWl0IHJlZnJl"
    "c2goKTt9CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtbWlzY10iKS5m"
    "b3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZp"
    "ZXcgb25seSIpO3JldHVybjt9CiAgICBjb25zdCB3aGF0PWIuZGF0YXNldC5taXNjLCB2YWw9Yi5k"
    "YXRhc2V0Lm1pc2NWYWx1ZXx8IiI7CiAgICBjb25zdCBsYmw9Yi50ZXh0Q29udGVudDtiLmRpc2Fi"
    "bGVkPXRydWU7Yi50ZXh0Q29udGVudD0i4oCmIjsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIv"
    "YXBpL2FkbWluL21pc2MiLHt3aGF0OndoYXQsdmFsdWU6dmFsfSk7CiAgICBiLmRpc2FibGVkPWZh"
    "bHNlO2IudGV4dENvbnRlbnQ9bGJsOwogICAgaWYoIXIpcmV0dXJuOwogICAgaWYoci5saW5lcylz"
    "aGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+Q2hhbm5lbCBj"
    "aGVjazwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJ3aGl0ZS1zcGFjZTpwcmUtbGlu"
    "ZSI+JHtlc2Moci5saW5lcy5qb2luKCJcbiIpKX08L3A+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9zZT5DbG9zZTwvYnV0dG9u"
    "PmApOwogICAgZWxzZSBpZih3aGF0PT09ImxvZ3Rlc3QiKXRvYXN0KCJUZXN0IG1lc3NhZ2Ugc2Vu"
    "dCB0byB0aGUgbG9nIGNoYW5uZWwiKTsKICAgIGVsc2UgaWYod2hhdD09PSJjbGVhcnN0b2NrIil0"
    "b2FzdChyLnJlbW92ZWQrIiByZW1vdmVkIik7CiAgICBlbHNlIHRvYXN0KCJEb25lIik7CiAgICBh"
    "d2FpdCByZWZyZXNoKCk7CiAgfSk7CiAgY29uc3QgdXBpYWRkPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJ1cGlhZGQiKTsKICBpZih1cGlhZGQpdXBpYWRkLm9uY2xpY2s9YXN5bmMoKT0+ewogICAg"
    "Y29uc3QgaT11cGlhZGQuY2xvc2VzdCgiZGl2IikucXVlcnlTZWxlY3RvcigiaW5wdXQiKTsKICAg"
    "IGNvbnN0IHY9aT8oaS52YWx1ZXx8IiIpLnRyaW0oKToiIjsKICAgIGlmKCF2KXt0b2FzdCgiUGFz"
    "dGUgdGhlIGV4dHJhIFVQSSBoYW5kbGUiKTtyZXR1cm47fQogICAgY29uc3QgY3VyPShBRE0ucGF5"
    "JiZBRE0ucGF5LmV4dHJhKXx8IiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1p"
    "bi9zZXR0aW5nIix7a2V5OiJ1cGlfZXh0cmEiLHZhbHVlOmN1cj8oY3VyKyIsIit2KTp2fSk7CiAg"
    "ICBpZihyKXtpLnZhbHVlPSIiO3RvYXN0KCJIYW5kbGUgYWRkZWQiKTthd2FpdCByZWZyZXNoKCk7"
    "fQogIH07CiAgY29uc3QgYWRtaW5hZGQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFkbWluYWRk"
    "Iik7CiAgaWYoYWRtaW5hZGQpYWRtaW5hZGQub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBp"
    "PWFkbWluYWRkLmNsb3Nlc3QoImRpdiIpLnF1ZXJ5U2VsZWN0b3IoImlucHV0Iik7CiAgICBjb25z"
    "dCB2PWk/KGkudmFsdWV8fCIiKS50cmltKCkucmVwbGFjZSgvXkAvLCIiKToiIjsKICAgIGlmKCF2"
    "KXt0b2FzdCgiU2VuZCB0aGUgbnVtZXJpYyB1c2VyIGlkIik7cmV0dXJuO30KICAgIGNvbnN0IHI9"
    "YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL21pc2MiLHt3aGF0OiJhZGRhZG1pbiIsdmFsdWU6dn0p"
    "OwogICAgaWYocil7aS52YWx1ZT0iIjt0b2FzdCgiQWRtaW4gYWRkZWQgd2l0aCBubyBwb3dlcnMg"
    "4oCUIGdyYW50IHRoZW0gaW4gdGhlIGJvdCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgfTsKICBjb25z"
    "dCBwcml6ZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicHJpemVzYXZlIik7CiAgaWYocHJpemUp"
    "cHJpemUub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBpPXByaXplLmNsb3Nlc3QoImRpdiIp"
    "LnF1ZXJ5U2VsZWN0b3IoImlucHV0Iik7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9h"
    "ZG1pbi9zZXR0aW5nIix7a2V5OiJsZWFkZXJib2FyZF9wcml6ZSIsdmFsdWU6KGkmJmkudmFsdWUp"
    "fHwiMCJ9KTsKICAgIGlmKHIpdG9hc3QoIlByaXplIHNhdmVkIik7CiAgfTsKICBjb25zdCBjcGFk"
    "ZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY3BhZGQiKTsKICBpZihjcGFkZCljcGFkZC5vbmNs"
    "aWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGNvZGU9KChkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgi"
    "Y3Bjb2RlIil8fHt9KS52YWx1ZXx8IiIpLnRyaW0oKTsKICAgIGNvbnN0IHBjdD0oKGRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJjcHBjdCIpfHx7fSkudmFsdWV8fCIiKS50cmltKCk7CiAgICBpZigh"
    "Y29kZXx8IXBjdCl7dG9hc3QoIkNvZGUgYW5kIHBlcmNlbnQsIHBsZWFzZSIpO3JldHVybjt9CiAg"
    "ICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBjb25zdCByPWF3"
    "YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jb3Vwb24iLHtjb2RlOmNvZGUscGVyY2VudDpwY3R9KTsK"
    "ICAgIGlmKCFyKXJldHVybjsKICAgIGNvbnN0IGExPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJj"
    "cGNvZGUiKSxhMj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY3BwY3QiKTsKICAgIGlmKGExKWEx"
    "LnZhbHVlPSIiO2lmKGEyKWEyLnZhbHVlPSIiOwogICAgdG9hc3QoIkNvdXBvbiAiK2NvZGUudG9V"
    "cHBlckNhc2UoKSsiIGlzIGxpdmUiKTthd2FpdCByZWZyZXNoKCk7CiAgfTsKICBjb25zdCBmZz1k"
    "b2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZmxhc2hnbyIpOwogIGlmKGZnKWZnLm9uY2xpY2s9YXN5"
    "bmMoKT0+ewogICAgY29uc3QgY2FyZD1mZy5jbG9zZXN0KCIuY2FyZCIpfHxkb2N1bWVudDsKICAg"
    "IGNvbnN0IGlucz1jYXJkLnF1ZXJ5U2VsZWN0b3JBbGwoImlucHV0Iik7CiAgICBjb25zdCBwY3Q9"
    "KChpbnNbMF0mJmluc1swXS52YWx1ZSl8fCIyMCIpLCBocnM9KChpbnNbMV0mJmluc1sxXS52YWx1"
    "ZSl8fCI1Iik7CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAg"
    "ICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9mbGFzaCIse3BlcmNlbnQ6cGN0LGhv"
    "dXJzOmhyc30pOwogICAgaWYocil7dG9hc3QoIkZsYXNoIHNhbGUgbGl2ZSDigJQgIitwY3QrIiUg"
    "Zm9yICIraHJzKyJoIik7YXdhaXQgcmVmcmVzaCgpO30KICB9OwogIGNvbnN0IGJjPWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJiY2dvIik7CiAgaWYoYmMpYmMub25jbGljaz1hc3luYygpPT57CiAg"
    "ICBjb25zdCBjYXJkPWJjLmNsb3Nlc3QoIi5jYXJkIil8fGRvY3VtZW50OwogICAgY29uc3QgdGE9"
    "Y2FyZC5xdWVyeVNlbGVjdG9yKCJ0ZXh0YXJlYSIpOwogICAgY29uc3QgdGV4dD0oKHRhJiZ0YS52"
    "YWx1ZSl8fCIiKS50cmltKCk7CiAgICBpZighdGV4dCl7dG9hc3QoIldyaXRlIHRoZSBtZXNzYWdl"
    "IGZpcnN0Iik7aWYodGEpdGEuZm9jdXMoKTtyZXR1cm47fQogICAgaWYoIUxJVkUpe3RvYXN0KCJQ"
    "cmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgYmMuZGlzYWJsZWQ9dHJ1ZTtiYy50ZXh0Q29udGVu"
    "dD0iU2VuZGluZ+KApiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9icm9h"
    "ZGNhc3QiLHt0ZXh0OnRleHR9KTsKICAgIGJjLmRpc2FibGVkPWZhbHNlO2JjLnRleHRDb250ZW50"
    "PSJTZW5kIEJyb2FkY2FzdCI7CiAgICBpZihyKXt0b2FzdCgiU2VudCB0byAiK3Iuc2VudCsiIHBl"
    "b3BsZSIpO2lmKHRhKXRhLnZhbHVlPSIiO30KICB9OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JB"
    "bGwoIltkYXRhLWRlcC1va10iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBj"
    "b25zdCBpZD1iLmRhdGFzZXQuZGVwT2s7CiAgICBpZighaWQpe3RvYXN0KCJOb3RoaW5nIHRvIGFw"
    "cHJvdmUiKTtyZXR1cm47fQogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vZGVw"
    "b3NpdCIse2lkOitpZCxhY3Rpb246ImFwcHJvdmUifSk7CiAgICBpZihyKXt0b2FzdCgiQ3JlZGl0"
    "ZWQiKTthd2FpdCByZWZyZXNoKCk7fQogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IltkYXRhLXJldl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBy"
    "PWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9yZXZpZXciLHtpZDorYi5kYXRhc2V0LnJldn0pOwog"
    "ICAgaWYocil7dG9hc3QoIlVwZGF0ZWQiKTthd2FpdCByZWZyZXNoKCk7fQogIH0pOwogIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXNldHRpbmddIikuZm9yRWFjaCh0PT57CiAgICB0"
    "Lm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25zdCBrZXk9dC5kYXRhc2V0LnNldHRpbmcsIG9u"
    "PSF0LmNsYXNzTGlzdC5jb250YWlucygib24iKTsKICAgICAgdC5jbGFzc0xpc3QudG9nZ2xlKCJv"
    "biIsb24pOwogICAgICBpZighTElWRSl7dG9hc3Qob24/Ik9uIjoiT2ZmIik7cmV0dXJuO30KICAg"
    "ICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc2V0dGluZyIse2tleTprZXksdmFs"
    "dWU6b24/IjEiOiIwIn0pOwogICAgICBpZighcil7dC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIsIW9u"
    "KTtyZXR1cm47fQogICAgICBBRE0uc3c9QURNLnN3fHx7fTtBRE0uc3dba2V5XT1vbjsKICAgICAg"
    "dG9hc3Qoa2V5LnJlcGxhY2UoL18vZywiICIpKyhvbj8iIG9uIjoiIG9mZiIpKTsKICAgIH07CiAg"
    "fSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtYWRtaW4tYWN0XSIpLmZvckVh"
    "Y2goYj0+ewogICAgYi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgICAgY29uc3QgYWN0PWIuZGF0YXNl"
    "dC5hZG1pbkFjdDsKICAgICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47"
    "fQogICAgICBjb25zdCBsYWJlbD1iLnRleHRDb250ZW50O2IuZGlzYWJsZWQ9dHJ1ZTtiLnRleHRD"
    "b250ZW50PSJXb3JraW5n4oCmIjsKICAgICAgbGV0IHI9bnVsbDsKICAgICAgaWYoYWN0PT09ImJh"
    "Y2t1cCIpICAgICAgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vYmFja3VwIix7fSk7CiAgICAg"
    "IGVsc2UgaWYoYWN0PT09ImRpZ2VzdCIpIHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2RpZ2Vz"
    "dCIse30pOwogICAgICBlbHNlIGlmKGFjdD09PSJleHBvcnQiKSByPWF3YWl0IGFwaVRyeSgiL2Fw"
    "aS9hZG1pbi9leHBvcnQiLHt9KTsKICAgICAgZWxzZSBpZihhY3Q9PT0ic3luYyIpICAgcj1hd2Fp"
    "dCBhcGlUcnkoIi9hcGkvYWRtaW4vc3luYyIse30pOwogICAgICBiLmRpc2FibGVkPWZhbHNlO2Iu"
    "dGV4dENvbnRlbnQ9bGFiZWw7CiAgICAgIGlmKCFyKXJldHVybjsKICAgICAgaWYoYWN0PT09ImJh"
    "Y2t1cCIpdG9hc3QoIkRhdGFiYXNlIHNlbnQgdG8geW91IGluIGNoYXQiKTsKICAgICAgaWYoYWN0"
    "PT09ImRpZ2VzdCIpdG9hc3QoIkRpZ2VzdCBzZW50IHRvIHRoZSBhZG1pbnMiKTsKICAgICAgaWYo"
    "YWN0PT09ImV4cG9ydCIpdG9hc3Qoci5yb3dzKyIgb3JkZXJzIHNlbnQgYXMgQ1NWIik7CiAgICAg"
    "IGlmKGFjdD09PSJzeW5jIil0b2FzdCgiQ29tbWFuZHMgc3luY2VkIHdpdGggVGVsZWdyYW0iKTsK"
    "ICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCJbZGF0YS1wcmljZV0iKS5mb3JFYWNoKGlucD0+ewogICAgaW5wLm9uY2hhbmdlPWFz"
    "eW5jKCk9PnsKICAgICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQog"
    "ICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9wcmljaW5nIix7a2V5OmlucC5k"
    "YXRhc2V0LnByaWNlLHZhbHVlOmlucC52YWx1ZX0pOwogICAgICBpZihyKXt0b2FzdCgiUHJpY2lu"
    "ZyB1cGRhdGVkIik7YXdhaXQgcmVmcmVzaCgpO30KICAgIH07CiAgfSk7CiAgZG9jdW1lbnQucXVl"
    "cnlTZWxlY3RvckFsbCgiW2RhdGEtY3ByaWNlXSIpLmZvckVhY2goaW5wPT57CiAgICBpbnAub25j"
    "aGFuZ2U9YXN5bmMoKT0+ewogICAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3Jl"
    "dHVybjt9CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3ByaWNpbmciLHtj"
    "b3VudHJ5OmlucC5kYXRhc2V0LmNwcmljZSxwcmljZTppbnAudmFsdWV9KTsKICAgICAgaWYocil7"
    "dG9hc3QoaW5wLmRhdGFzZXQuY3ByaWNlKyIgbm93ICIrbW9uZXkoaW5wLnZhbHVlKSk7YXdhaXQg"
    "cmVmcmVzaCgpO30KICAgIH07CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2Rh"
    "dGEtcWFdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IHdoYXQ9Yi5kYXRh"
    "c2V0LnFhOwogICAgY29uc3QgZ289KHRhYixtc2cpPT57CiAgICAgIGNvbnN0IHQ9ZG9jdW1lbnQu"
    "cXVlcnlTZWxlY3RvcihgI2FzdWIgYnV0dG9uW2RhdGEtYT0iJHt0YWJ9Il1gKTsKICAgICAgaWYo"
    "dCl7dC5jbGljaygpO3RvYXN0KG1zZyk7fWVsc2UgdG9hc3QobXNnKTsKICAgIH07CiAgICBpZih3"
    "aGF0PT09ImJyb2FkY2FzdCIpcmV0dXJuIGdvKCJtYXJrZXQiLCJDb21wb3NlIHRoZSBicm9hZGNh"
    "c3QgZnJvbSB0aGUgYm90OiAvYWRtaW4g4oaSIE1hcmtldGluZyIpOwogICAgaWYod2hhdD09PSJz"
    "dG9jayIpcmV0dXJuIGdvKCJzdG9jayIsIkFkZCBzdG9jayBmcm9tIHRoZSBib3Q6IC9hZG1pbiDi"
    "hpIgU3RvY2sg4oaSIEFkZCBzdG9jayIpOwogICAgaWYod2hhdD09PSJmbGFzaCIpcmV0dXJuIGdv"
    "KCJtYXJrZXQiLCJGbGFzaCBzYWxlIGxpdmVzIGhlcmUiKTsKICAgIGlmKHdoYXQ9PT0iYmFja3Vw"
    "IilyZXR1cm4gdG9hc3QoIkJhY2t1cHMgYXJlIHNlbnQgdG8geW91ciBsb2cgY2hhbm5lbCDigJQg"
    "L2FkbWluIOKGkiBTZXR0aW5ncyIpOwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IltkYXRhLXNldF0iKS5mb3JFYWNoKGlucD0+ewogICAgaW5wLm9uY2hhbmdlPWFzeW5jKCk9PnsK"
    "ICAgICAgY29uc3Qga2V5PWlucC5kYXRhc2V0LnNldCwgdmFsPWlucC52YWx1ZTsKICAgICAgaWYo"
    "IUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgICBjb25zdCByPWF3YWl0"
    "IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OmtleSx2YWx1ZTp2YWx9KTsKICAgICAg"
    "aWYocil7dG9hc3Qoa2V5LnJlcGxhY2UoL18vZywiICIpKyIgc2F2ZWQiKTthd2FpdCByZWZyZXNo"
    "KCk7fQogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1jbXNd"
    "IikuZm9yRWFjaCh0PT57CiAgICB0Lm9uaW5wdXQ9KCk9PntDTVNbdC5kYXRhc2V0LmNtc109dC52"
    "YWx1ZTthcHBseUNNUygpOwogICAgICBjbGVhclRpbWVvdXQodC5fc3YpO3QuX3N2PXNldFRpbWVv"
    "dXQoKCk9PnB1c2goIi9hcGkvYWRtaW4vY29udGVudCIse2tleTp0LmRhdGFzZXQuY21zLHZhbHVl"
    "OnQudmFsdWV9KSw1MDApOwogICAgICB0LmNsb3Nlc3QoIi5jbXNyb3ciKS5kYXRhc2V0LmY9KHQu"
    "Y2xvc2VzdCgiLmNtc3JvdyIpLnF1ZXJ5U2VsZWN0b3IoImxhYmVsIikudGV4dENvbnRlbnQrIiAi"
    "K3QudmFsdWUpLnRvTG93ZXJDYXNlKCk7fTt9KTsKICBjb25zdCBjcT1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiY3EiKTsKICBpZihjcSljcS5vbmlucHV0PSgpPT57Y29uc3QgcT1jcS52YWx1ZS50"
    "cmltKCkudG9Mb3dlckNhc2UoKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5jbXNy"
    "b3ciKS5mb3JFYWNoKHI9PnIuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZSIsISFxJiYhci5kYXRhc2V0"
    "LmYuaW5jbHVkZXMocSkpKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5jbXNncnAi"
    "KS5mb3JFYWNoKGc9PmcuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZSIsIWcucXVlcnlTZWxlY3Rvcigi"
    "LmNtc3Jvdzpub3QoLmhpZGUpIikpKTt9OwogIGNvbnN0IGNyPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJjbXNyZXNldCIpOwogIGlmKGNyKWNyLm9uY2xpY2s9KCk9PntPYmplY3QuYXNzaWduKENN"
    "UyxDTVMwKTthcHBseUNNUygpO3JlbmRlckFkbWluKCk7dG9hc3QoIlRleHQgcmVzZXQgdG8gZGVm"
    "YXVsdHMiKTt9OwogIGNvbnN0IGFkZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2gtYWRkIik7"
    "CiAgaWYoYWRkKWFkZC5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGk9ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoImNoLWluIiksdj0oaS52YWx1ZXx8IiIpLnRyaW0oKTsKICAgIGlmKCF2KXt0"
    "b2FzdCgiUGFzdGUgYSBjaGFubmVsIGxpbmsgZmlyc3QiKTtpLmZvY3VzKCk7cmV0dXJufQogICAg"
    "Y29uc3QgcmVxPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjaC10eXBlIikudmFsdWU9PT0iMSI7"
    "CiAgICBpZihMSVZFKXsKICAgICAgYWRkLmRpc2FibGVkPXRydWU7CiAgICAgIGNvbnN0IHI9YXdh"
    "aXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rpb246ImFkZCIsaGFuZGxlOnYscmVx"
    "dWlyZWQ6cmVxfSk7CiAgICAgIGFkZC5kaXNhYmxlZD1mYWxzZTsKICAgICAgaWYoIXIpcmV0dXJu"
    "OwogICAgICBpLnZhbHVlPSIiO2F3YWl0IHJlZnJlc2goKTt0b2FzdCgiQ2hhbm5lbCBzYXZlZCDi"
    "gJQgdGhlIGJvdCB1c2VzIGl0IG5vdyIpO3JldHVybjsKICAgIH0KICAgIENIQU5ORUxTLnB1c2go"
    "e2lkOkRhdGUubm93KCksbmFtZTp2LnJlcGxhY2UoL15odHRwcz86XC9cL3RcLm1lXC8vLCJAIiku"
    "c2xpY2UoMCwyNiksaGFuZGxlOnYscmVxOnJlcSxvbjp0cnVlLG1lbWJlcnM6IuKAlCJ9KTsKICAg"
    "IGkudmFsdWU9IiI7cmVuZGVyQWRtaW4oKTt0b2FzdCgiQ2hhbm5lbCBhZGRlZCIpO307CiAgY29u"
    "c3QgbGdhZGQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImxnLWFkZCIpOwogIGlmKGxnYWRkKWxn"
    "YWRkLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgaT1kb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgibGctaW4iKSx2PShpLnZhbHVlfHwiIikudHJpbSgpOwogICAgaWYoIXYpe3RvYXN0KCJQYXN0"
    "ZSBhIGxvZyBjaGFubmVsIGZpcnN0Iik7aS5mb2N1cygpO3JldHVybn0KICAgIGlmKExJVkUpewog"
    "ICAgICBsZ2FkZC5kaXNhYmxlZD10cnVlOwogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2Fw"
    "aS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJhZGQiLGhhbmRsZTp2LGxvZzp0cnVlfSk7CiAgICAg"
    "IGxnYWRkLmRpc2FibGVkPWZhbHNlOwogICAgICBpZighcilyZXR1cm47CiAgICAgIGkudmFsdWU9"
    "IiI7YXdhaXQgcmVmcmVzaCgpO3RvYXN0KCJMb2cgY2hhbm5lbCBzYXZlZCIpO3JldHVybjsKICAg"
    "IH0KICAgIExPR0NILnB1c2goe2lkOkRhdGUubm93KCksbmFtZTp2LnJlcGxhY2UoL15odHRwcz86"
    "XC9cL3RcLm1lXC8vLCJAIikuc2xpY2UoMCwyNCksaGFuZGxlOnYscHJpbWFyeTpMT0dDSC5sZW5n"
    "dGg9PT0wLG9uOnRydWV9KTsKICAgIGkudmFsdWU9IiI7cmVuZGVyQWRtaW4oKTt0b2FzdCgiTG9n"
    "IGNoYW5uZWwgYWRkZWQiKTt9OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNo"
    "ZGVsXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKExJVkUpe2NvbnN0"
    "IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rpb246ImRlbCIsaWQ6K2Iu"
    "ZGF0YXNldC5jaGRlbH0pOwogICAgICBpZihyKXthd2FpdCByZWZyZXNoKCk7dG9hc3QoIkNoYW5u"
    "ZWwgcmVtb3ZlZCIpO31yZXR1cm47fQogICAgQ0hBTk5FTFM9Q0hBTk5FTFMuZmlsdGVyKGM9PmMu"
    "aWQhPWIuZGF0YXNldC5jaGRlbCk7cmVuZGVyQWRtaW4oKTt0b2FzdCgiQ2hhbm5lbCByZW1vdmVk"
    "Iil9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1jaHJlcV0iKS5mb3JFYWNo"
    "KGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBpZihMSVZFKXtjb25zdCByPWF3YWl0IGFwaVRy"
    "eSgiL2FwaS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJyZXF1aXJlZCIsaWQ6K2IuZGF0YXNldC5j"
    "aHJlcX0pOwogICAgICBpZihyKXthd2FpdCByZWZyZXNoKCk7dG9hc3QoIlVwZGF0ZWQiKTt9cmV0"
    "dXJuO30KICAgIGNvbnN0IGM9Q0hBTk5FTFMuZmluZCh4PT54LmlkPT1iLmRhdGFzZXQuY2hyZXEp"
    "O2MucmVxPSFjLnJlcTtyZW5kZXJBZG1pbigpO3RvYXN0KGMucmVxPyJOb3cgcmVxdWlyZWQiOiJO"
    "b3cgb3B0aW9uYWwiKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNob25d"
    "IikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYoTElWRSl7Y29uc3Qgcj1h"
    "d2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vY2hhbm5lbCIse2FjdGlvbjoidG9nZ2xlIixpZDorYi5k"
    "YXRhc2V0LmNob259KTsKICAgICAgaWYocil7YXdhaXQgcmVmcmVzaCgpO3RvYXN0KCJVcGRhdGVk"
    "Iik7fXJldHVybjt9CiAgICBjb25zdCBjPUNIQU5ORUxTLmZpbmQoeD0+eC5pZD09Yi5kYXRhc2V0"
    "LmNob24pO2Mub249IWMub247cmVuZGVyQWRtaW4oKTt0b2FzdChjLm9uPyJDaGFubmVsIGVuYWJs"
    "ZWQiOiJDaGFubmVsIHBhdXNlZCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2Rh"
    "dGEtbGdkZWxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IHdhc1A9TE9H"
    "Q0guZmluZChsPT5sLmlkPT1iLmRhdGFzZXQubGdkZWwpPy5wcmltYXJ5OwogICAgTE9HQ0g9TE9H"
    "Q0guZmlsdGVyKGw9PmwuaWQhPWIuZGF0YXNldC5sZ2RlbCk7CiAgICBpZih3YXNQJiZMT0dDSFsw"
    "XSlMT0dDSFswXS5wcmltYXJ5PXRydWU7cmVuZGVyQWRtaW4oKTt0b2FzdCgiTG9nIGNoYW5uZWwg"
    "cmVtb3ZlZCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtbGdzdGFyXSIp"
    "LmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgICBMT0dDSC5mb3JFYWNoKGw9PmwucHJpbWFy"
    "eT0obC5pZD09Yi5kYXRhc2V0Lmxnc3RhcikpO3JlbmRlckFkbWluKCk7dG9hc3QoIlByZWZlcnJl"
    "ZCBsb2cgY2hhbm5lbCBzZXQiKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRh"
    "LWxnb25dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9Pntjb25zdCBsPUxPR0NILmZpbmQoeD0+"
    "eC5pZD09Yi5kYXRhc2V0Lmxnb24pO2wub249IWwub247cmVuZGVyQWRtaW4oKTt0b2FzdChsLm9u"
    "PyJDaGFubmVsIG9uIjoiQ2hhbm5lbCBtdXRlZCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3Rv"
    "ckFsbCgiW2RhdGEtZXZvbl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+e2NvbnN0IGU9RVZF"
    "TlRTWytiLmRhdGFzZXQuZXZvbl07ZS5vbj0hZS5vbjtyZW5kZXJBZG1pbigpfSk7CiAgZG9jdW1l"
    "bnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZXZjaF0iKS5mb3JFYWNoKHM9PnMub25jaGFuZ2U9"
    "KCk9PntFVkVOVFNbK3MuZGF0YXNldC5ldmNoXS5jaD0rcy52YWx1ZTt0b2FzdCgiUm91dGluZyB1"
    "cGRhdGVkIil9KTsKICBjb25zdCBhbGw9ZG9jdW1lbnQucXVlcnlTZWxlY3RvcigiW2RhdGEtZXYt"
    "YWxsXSIpOwogIGlmKGFsbClhbGwub25jbGljaz0oKT0+e2NvbnN0IHQ9IUVWRU5UUy5ldmVyeShl"
    "PT5lLm9uKTtFVkVOVFMuZm9yRWFjaChlPT5lLm9uPXQpO3JlbmRlckFkbWluKCk7dG9hc3QodD8i"
    "QWxsIGV2ZW50cyBvbiI6IkFsbCBldmVudHMgb2ZmIil9OwogIGNvbnN0IG09ZG9jdW1lbnQucXVl"
    "cnlTZWxlY3RvcigiW2RhdGEtbWFzdGVyXSIpOwogIGlmKG0pbS5vbmNsaWNrPSgpPT57TE9HU19N"
    "QVNURVI9IUxPR1NfTUFTVEVSO3JlbmRlckFkbWluKCk7dG9hc3QoTE9HU19NQVNURVI/IkxvZ2dp"
    "bmcgZW5hYmxlZCI6IkFsbCBsb2dnaW5nIGRpc2FibGVkIil9OwogIGRvY3VtZW50LnF1ZXJ5U2Vs"
    "ZWN0b3JBbGwoIltkYXRhLWEtZ29dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGRv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNhc3ViIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFz"
    "c0xpc3QudG9nZ2xlKCJvbiIseC5kYXRhc2V0LmE9PT1iLmRhdGFzZXQuYUdvKSk7CiAgICBBVEFC"
    "PWIuZGF0YXNldC5hR287cmVuZGVyQWRtaW4oKTt3aW5kb3cuc2Nyb2xsVG8oMCwwKTt9KTsKICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS10Z10iKS5mb3JFYWNoKHQ9PnQub25jbGlj"
    "az0oKT0+dC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIpKTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "QWxsKCJbZGF0YS1zZXRzZWxdIikuZm9yRWFjaChzZWw9PnNlbC5vbmNoYW5nZT1hc3luYygpPT57"
    "CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBjb25zdCBy"
    "PWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OnNlbC5kYXRhc2V0LnNldHNl"
    "bCx2YWx1ZTpzZWwudmFsdWV9KTsKICAgIGlmKHIpdG9hc3QoIlNhdmVkIik7CiAgfSk7Cn0KCmNv"
    "bnN0IF9jYz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2FydGNoaXAiKTsKaWYoX2NjKV9jYy5v"
    "bmNsaWNrPWNhcnRTaGVldDsKcmVuZGVyKCk7cmVuZGVyQWRtaW4oKTthcHBseUZlYXR1cmVzKCk7"
    "CgovKiDilIDilIAgbGl2ZSBkYXRhOiBldmVyeXRoaW5nIGJlbG93IGNvbWVzIGZyb20gdGhlIGJv"
    "dCBkYXRhYmFzZSB3aGVuIHNlcnZlZCBieSB0aGUgYm90IOKUgOKUgCAqLwovKiBMSVZFIC8gTUUg"
    "LyBURyAvIElOSVQgYXJlIGRlY2xhcmVkIGF0IHRoZSB2ZXJ5IHRvcCBvZiB0aGlzIHNjcmlwdCAq"
    "Lwphc3luYyBmdW5jdGlvbiBhcGkocGF0aCxib2R5KXsKICBjb25zdCBvPXtoZWFkZXJzOnsiQ29u"
    "dGVudC1UeXBlIjoiYXBwbGljYXRpb24vanNvbiIsIlgtSW5pdC1EYXRhIjpJTklULAogICAgIlgt"
    "VGVsZWdyYW0tSW5pdC1EYXRhIjpJTklULCJYLUFwcC1LZXkiOkFQUEtFWX19OwogIGlmKGJvZHkh"
    "PT11bmRlZmluZWQpe28ubWV0aG9kPSJQT1NUIjtvLmJvZHk9SlNPTi5zdHJpbmdpZnkoYm9keSk7"
    "fQogIGNvbnN0IHI9YXdhaXQgZmV0Y2gocGF0aCxvKTsKICBsZXQgZD1udWxsO3RyeXtkPWF3YWl0"
    "IHIuanNvbigpO31jYXRjaChlcnIpe30KICBpZighci5vayl7Y29uc3QgZT1uZXcgRXJyb3IoKGQm"
    "JmQuZXJyb3IpfHwocGF0aCsiICIrci5zdGF0dXMpKTtlLnN0YXR1cz1yLnN0YXR1cztlLmRhdGE9"
    "ZDt0aHJvdyBlO30KICByZXR1cm4gZDsKfQpmdW5jdGlvbiBhZG1pbkFsbG93ZWQoKXtyZXR1cm4g"
    "IUxJVkUgfHwgISEoTUUmJk1FLmFkbWluKTt9CmZ1bmN0aW9uIGFwcGx5U3RhdGUoZCl7CiAgLyog"
    "ZXZlcnkgbGlzdCBpcyByZXBsYWNlZCwgZXZlbiB3aGVuIGVtcHR5IOKAlCBhIG5ldyBzaG9wIG11"
    "c3QgbmV2ZXIgc2hvdyBkZW1vIGRhdGEgKi8KICBpZihkLmZlYXR1cmVzKU9iamVjdC5rZXlzKGQu"
    "ZmVhdHVyZXMpLmZvckVhY2goaz0+e2lmKGsgaW4gRkVBVClGRUFUW2tdPSEhZC5mZWF0dXJlc1tr"
    "XTt9KTsKICBpZihkLmNvbnRlbnQpT2JqZWN0LmtleXMoZC5jb250ZW50KS5mb3JFYWNoKGs9Pntp"
    "ZihrIGluIENNUyYmZC5jb250ZW50W2tdIT1udWxsKUNNU1trXT1kLmNvbnRlbnRba107fSk7CiAg"
    "aWYoQXJyYXkuaXNBcnJheShkLnN0b2NrKSl7U1RPQ0subGVuZ3RoPTA7ZC5zdG9jay5mb3JFYWNo"
    "KHg9PlNUT0NLLnB1c2goeCkpO0ZJTFRFUkVEPVNUT0NLLnNsaWNlKCk7fQogIGlmKEFycmF5Lmlz"
    "QXJyYXkoZC5vcmRlcnMpKXtPUkRFUlMubGVuZ3RoPTA7ZC5vcmRlcnMuZm9yRWFjaCh4PT5PUkRF"
    "UlMucHVzaCh4KSk7fQogIGlmKEFycmF5LmlzQXJyYXkoZC50eCkpe1RYLmxlbmd0aD0wO2QudHgu"
    "Zm9yRWFjaCh4PT5UWC5wdXNoKHgpKTt9CiAgaWYoQXJyYXkuaXNBcnJheShkLmZlZWQpKUZFRUQ9"
    "ZC5mZWVkOwogIGlmKGQuZWFybilFQVJOPU9iamVjdC5hc3NpZ24oe30sRUFSTixkLmVhcm4pOwog"
    "IGlmKGQucHJlZnMpUFJFRlM9T2JqZWN0LmFzc2lnbih7fSxQUkVGUyxkLnByZWZzKTsKICBpZihB"
    "cnJheS5pc0FycmF5KGQuYWxlcnRzKSl3aW5kb3cuX19hbGVydHM9ZC5hbGVydHM7CiAgaWYoZC5z"
    "dG9yZSl7CiAgICBTVE9SRT1PYmplY3QuYXNzaWduKHt9LFNUT1JFLGQuc3RvcmUpOwogICAgaWYo"
    "ZC5zdG9yZS5uYW1lKUNGRy5uYW1lPWQuc3RvcmUubmFtZTsKICAgIGlmKGQuc3RvcmUudGFnKUNG"
    "Ry50YWc9ZC5zdG9yZS50YWc7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1z"
    "dG9yZW5hbWVdIikuZm9yRWFjaChlPT5lLnRleHRDb250ZW50PUNGRy5uYW1lKTsKICAgIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXN0b3JldGFnXSIpLmZvckVhY2goZT0+ZS50ZXh0"
    "Q29udGVudD1DRkcudGFnKTsKICAgIGRvY3VtZW50LnRpdGxlPUNGRy5uYW1lOwogIH0KICBpZihk"
    "LmFkbSl7CiAgICBBRE09T2JqZWN0LmFzc2lnbih7fSxBRE0sZC5hZG0pOwogICAgaWYoQXJyYXku"
    "aXNBcnJheShBRE0uY2hhbm5lbHMpKQogICAgICBDSEFOTkVMUz1BRE0uY2hhbm5lbHMubWFwKGM9"
    "Pih7aWQ6Yy5pZCxuYW1lOmMubmFtZSxoYW5kbGU6Yy5oYW5kbGV8fCIiLAogICAgICAgIHJlcToh"
    "IWMucmVxdWlyZWQsb246ISFjLmFjdGl2ZSxtZW1iZXJzOiLigJQifSkpOwogICAgaWYoQURNLmxv"
    "Z3MpewogICAgICBMT0dTX01BU1RFUj0hIUFETS5sb2dzLm1hc3RlcjsKICAgICAgaWYoQXJyYXku"
    "aXNBcnJheShBRE0ubG9ncy5jaGFubmVscykpCiAgICAgICAgTE9HQ0g9QURNLmxvZ3MuY2hhbm5l"
    "bHMubWFwKGM9Pih7aWQ6Yy5pZCxuYW1lOmMubmFtZSxoYW5kbGU6IiIsCiAgICAgICAgICBwcmlt"
    "YXJ5OiEhYy5wcmltYXJ5LG9uOiEhYy5hY3RpdmV9KSk7CiAgICAgIGlmKEFycmF5LmlzQXJyYXko"
    "QURNLmxvZ3MuZXZlbnRzKSYmQURNLmxvZ3MuZXZlbnRzLmxlbmd0aCkKICAgICAgICBFVkVOVFM9"
    "QURNLmxvZ3MuZXZlbnRzLm1hcChlPT4oe2s6ZS5sYWJlbCxvbjohIWUub24sY2g6KExPR0NIWzBd"
    "fHx7fSkuaWR8fDF9KSk7CiAgICB9CiAgfQogIGlmKGQucmF0aW5ncylSRVZJRVdTPWQucmF0aW5n"
    "czsKICBpZih0eXBlb2YgZC5iYWxhbmNlPT09Im51bWJlciIpQkFMPWQuYmFsYW5jZTsKICBpZihk"
    "Lm1lKU1FPWQubWU7CiAgaWYoZC5jYXJ0KUNBUlQ9ZC5jYXJ0OwogIGlmKGQud2lzaGxpc3QpV0lT"
    "SD1kLndpc2hsaXN0Owp9CmZ1bmN0aW9uIHNob3dTd2l0Y2gob24pewogIGNvbnN0IHN3PWRvY3Vt"
    "ZW50LmdldEVsZW1lbnRCeUlkKCJzdyIpOwogIGlmKHN3KXN3LnN0eWxlLmRpc3BsYXk9b24/ImZs"
    "ZXgiOiJub25lIjsKfQp2YXIgTE9DS19USVRMRT0iIixMT0NLX01TRz0iIjsKZnVuY3Rpb24gbG9j"
    "a2VkU2NyZWVuKHRpdGxlLG1zZyl7CiAgTE9DS0VEPXRydWU7TE9DS19USVRMRT10aXRsZTtMT0NL"
    "X01TRz1tc2c7CiAgc2hvd1N3aXRjaChmYWxzZSk7CiAgcGFpbnRMb2NrZWQoKTsKfQpmdW5jdGlv"
    "biBwYWludExvY2tlZCgpewogIGNvbnN0IHN0PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdGFn"
    "ZSIpOwogIGlmKHN0KXN0LmlubmVySFRNTD0nPGRpdiBjbGFzcz0iY2FyZCIgc3R5bGU9Im1hcmdp"
    "bjoxOHB4O3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MjJweCI+JwogICAgKyc8aDMgc3R5bGU9"
    "Im1hcmdpbjowIDAgOHB4Ij4nK0xPQ0tfVElUTEUrJzwvaDM+JwogICAgKyc8cCBjbGFzcz0ic3Vi"
    "IiBzdHlsZT0ibWFyZ2luOjAgMCAxNHB4Ij4nK0xPQ0tfTVNHKyc8L3A+JwogICAgKyc8YnV0dG9u"
    "IGNsYXNzPSJidG4gYmxvY2siIG9uY2xpY2s9ImxvY2F0aW9uLnJlbG9hZCgpIj5UcnkgYWdhaW48"
    "L2J1dHRvbj48L2Rpdj4nOwogIFsidGFicyIsImRvY2siXS5mb3JFYWNoKGlkPT57Y29uc3QgZT1k"
    "b2N1bWVudC5nZXRFbGVtZW50QnlJZChpZCk7aWYoZSllLnN0eWxlLmRpc3BsYXk9Im5vbmUiO30p"
    "Owp9CmFzeW5jIGZ1bmN0aW9uIGFwaVRyeShwYXRoLGJvZHkpewogIHRyeXtyZXR1cm4gYXdhaXQg"
    "YXBpKHBhdGgsYm9keSk7fQogIGNhdGNoKGUpe3RvYXN0KFN0cmluZyhlJiZlLm1lc3NhZ2V8fGUp"
    "LnNsaWNlKDAsOTApKTtyZXR1cm4gbnVsbDt9Cn0KYXN5bmMgZnVuY3Rpb24gcmVmcmVzaCgpewog"
    "IGlmKCFMSVZFKXJldHVybjsKICB0cnl7CiAgICBhcHBseVN0YXRlKGF3YWl0IGFwaSgiL2FwaS9z"
    "dGF0ZSIpKTsKICAgIGFwcGx5Q01TKCk7YXBwbHlGZWF0dXJlcygpO3N5bmNCYWwoKTtyZW5kZXIo"
    "KTtyZW5kZXJBZG1pbigpOwogIH1jYXRjaChlKXt9Cn0KYXN5bmMgZnVuY3Rpb24gY29weVRleHQo"
    "dCwgbGFiZWwpewogIGNvbnN0IHR4dD1TdHJpbmcodHx8IiIpOwogIGlmKCF0eHQpe3RvYXN0KCJO"
    "b3RoaW5nIHRvIGNvcHkiKTtyZXR1cm4gZmFsc2U7fQogIHRyeXsKICAgIGlmKG5hdmlnYXRvci5j"
    "bGlwYm9hcmQmJndpbmRvdy5pc1NlY3VyZUNvbnRleHQpe2F3YWl0IG5hdmlnYXRvci5jbGlwYm9h"
    "cmQud3JpdGVUZXh0KHR4dCk7fQogICAgZWxzZXsKICAgICAgY29uc3QgdGE9ZG9jdW1lbnQuY3Jl"
    "YXRlRWxlbWVudCgidGV4dGFyZWEiKTsKICAgICAgdGEudmFsdWU9dHh0O3RhLnNldEF0dHJpYnV0"
    "ZSgicmVhZG9ubHkiLCIiKTsKICAgICAgdGEuc3R5bGUuY3NzVGV4dD0icG9zaXRpb246Zml4ZWQ7"
    "bGVmdDotOTk5OXB4O29wYWNpdHk6MCI7CiAgICAgIGRvY3VtZW50LmJvZHkuYXBwZW5kQ2hpbGQo"
    "dGEpO3RhLnNlbGVjdCgpO3RhLnNldFNlbGVjdGlvblJhbmdlKDAsdHh0Lmxlbmd0aCk7CiAgICAg"
    "IGNvbnN0IG9rPWRvY3VtZW50LmV4ZWNDb21tYW5kKCJjb3B5Iik7ZG9jdW1lbnQuYm9keS5yZW1v"
    "dmVDaGlsZCh0YSk7CiAgICAgIGlmKCFvayl0aHJvdyBuZXcgRXJyb3IoImNvcHkgcmVmdXNlZCIp"
    "OwogICAgfQogICAgdG9hc3QoKGxhYmVsfHwiQ29waWVkIikrIjogIisodHh0Lmxlbmd0aD4yNj90"
    "eHQuc2xpY2UoMCwyNikrIuKApiI6dHh0KSk7CiAgICByZXR1cm4gdHJ1ZTsKICB9Y2F0Y2goZSl7"
    "CiAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+Q29w"
    "eSB0aGlzPC9oMz4KICAgICAgPHAgY2xhc3M9InN1YiI+WW91ciBicm93c2VyIGJsb2NrZWQgdGhl"
    "IGNsaXBib2FyZCDigJQgc2VsZWN0IGFuZCBjb3B5LjwvcD4KICAgICAgPHRleHRhcmVhIGNsYXNz"
    "PSJpbnAiIHJvd3M9IjMiIHJlYWRvbmx5IG9uY2xpY2s9InRoaXMuc2VsZWN0KCkiPiR7ZXNjKHR4"
    "dCl9PC90ZXh0YXJlYT4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFy"
    "Z2luLXRvcDoxMHB4IiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRvbj5gKTsKICAgIHJldHVybiBmYWxz"
    "ZTsKICB9Cn0KbGV0IFNUT1JFPXt1cGk6IiIscGF5ZWU6IiIscHJlZml4OiJQQVkiLHRlcm1zOiIi"
    "LHN1cHBvcnQ6IiJ9OwpsZXQgUFJFRlM9e3Jlc3RvY2s6dHJ1ZSxyZWNlaXB0czp0cnVlLHByb21v"
    "czp0cnVlLHB1c2g6dHJ1ZX07CmxldCBPRkZFUj0iIjsKZnVuY3Rpb24gb3BlblBheShsaW5rKXsK"
    "ICBpZighbGluaylyZXR1cm47CiAgdHJ5ewogICAgaWYoVEcmJlRHLm9wZW5MaW5rJiYvXmh0dHBz"
    "PzovLnRlc3QobGluaykpVEcub3BlbkxpbmsobGluayk7CiAgICBlbHNlIHdpbmRvdy5sb2NhdGlv"
    "bi5ocmVmPWxpbms7CiAgfWNhdGNoKGUpe3dpbmRvdy5sb2NhdGlvbi5ocmVmPWxpbms7fQp9CmFz"
    "eW5jIGZ1bmN0aW9uIGJvb3QoKXsKICBpZihURyl7dHJ5e1RHLnJlYWR5KCk7VEcuZXhwYW5kKCk7"
    "fWNhdGNoKGUpe319CiAgY29uc3QgaW5URz0hISgoSU5JVCYmSU5JVC5sZW5ndGgpfHwoQVBQS0VZ"
    "JiZBUFBLRVkubGVuZ3RoKSk7CiAgaWYoaW5URylzaG93U3dpdGNoKGZhbHNlKTsgICAgICAgICAg"
    "ICAgIC8qIGZhaWwgY2xvc2VkOiBubyBhZG1pbiBzdHJpcCB1bnRpbCBwcm92ZW4gKi8KICBpZihs"
    "b2NhdGlvbi5wcm90b2NvbD09PSJmaWxlOiIpcmV0dXJuOyAgLyogb3BlbmVkIGFzIGEgcGxhaW4g"
    "ZmlsZSDigJQgZGVtbyBnYWxsZXJ5ICovCiAgbGV0IGQ7CiAgdHJ5ewogICAgZD1hd2FpdCBhcGko"
    "Ii9hcGkvc3RhdGUiKTsKICB9Y2F0Y2goZSl7CiAgICBpZihlJiZlLnN0YXR1cz09PTQwMSl7CiAg"
    "ICAgIGxldCB3aHk9IiIsZml4PSIiOwogICAgICB0cnl7CiAgICAgICAgY29uc3Qgdz1hd2FpdCAo"
    "YXdhaXQgZmV0Y2goIi9hcGkvd2hvYW1pIix7aGVhZGVyczp7IlgtSW5pdC1EYXRhIjpJTklULAog"
    "ICAgICAgICAgIlgtVGVsZWdyYW0tSW5pdC1EYXRhIjpJTklULCJYLUFwcC1LZXkiOkFQUEtFWX19"
    "KSkuanNvbigpOwogICAgICAgIGNvbnN0IFI9e21pc3Npbmc6WyJUZWxlZ3JhbSBkaWQgbm90IHBh"
    "c3MgeW91ciBhY2NvdW50IHRvIHRoaXMgcGFnZS4iLAogICAgICAgICAgICAgICAgICAgIk9wZW4g"
    "dGhlIGJvdCBhbmQgdGFwIDxiPk9wZW4gU3RvcmU8L2I+LCBvciBzZW5kIDxiPi9hcHA8L2I+LiJd"
    "LAogICAgICAgICAgICAgICAgICJiYWQtZm9ybWF0IjpbIlRoZSBzaWduLWluIGRhdGEgYXJyaXZl"
    "ZCBkYW1hZ2VkLiIsIkNsb3NlIHRoZSBhcHAgYW5kIG9wZW4gaXQgYWdhaW4gZnJvbSB0aGUgYm90"
    "LiJdLAogICAgICAgICAgICAgICAgICJuby1oYXNoIjpbIlRoZSBzaWduLWluIGRhdGEgYXJyaXZl"
    "ZCBpbmNvbXBsZXRlLiIsIkNsb3NlIHRoZSBhcHAgYW5kIG9wZW4gaXQgYWdhaW4gZnJvbSB0aGUg"
    "Ym90LiJdLAogICAgICAgICAgICAgICAgICJiYWQtaGFzaCI6WyJUaGUgc2lnbmF0dXJlIGRvZXMg"
    "bm90IG1hdGNoIHRoaXMgYm90J3MgdG9rZW4uIiwKICAgICAgICAgICAgICAgICAgICJUaGUgcGFn"
    "ZSBpcyBzZXJ2ZWQgYnkgYSBkaWZmZXJlbnQgYm90LCBvciBCT1RfVE9LRU4gb24gdGhlIHNlcnZl"
    "ciBoYXMgcXVvdGVzIGFyb3VuZCBpdC4gU2VuZCA8Yj4vZGlhZzwvYj4gaW4gdGhlIGJvdCB0byBj"
    "b21wYXJlLiJdLAogICAgICAgICAgICAgICAgIHN0YWxlOlsiVGhhdCBzaWduLWluIGlzIG9sZGVy"
    "IHRoYW4gdGhlIGFsbG93ZWQgd2luZG93LiIsIlJlb3BlbiB0aGUgYXBwIGZyb20gdGhlIGJvdC4i"
    "XSwKICAgICAgICAgICAgICAgICAiYmFkLXVzZXIiOlsiWW91ciBhY2NvdW50IGRldGFpbHMgY291"
    "bGQgbm90IGJlIHJlYWQuIiwiUmVvcGVuIHRoZSBhcHAgZnJvbSB0aGUgYm90LiJdLAogICAgICAg"
    "ICAgICAgICAgICJuby1pZCI6WyJUZWxlZ3JhbSBzZW50IG5vIHVzZXIgaWQuIiwiUmVvcGVuIHRo"
    "ZSBhcHAgZnJvbSB0aGUgYm90LiJdfTsKICAgICAgICBjb25zdCByPVJbdy53aHldfHxbIlRoZSBz"
    "dG9yZSBjb3VsZCBub3QgY29uZmlybSB3aG8geW91IGFyZS4iLCJPcGVuIGl0IGFnYWluIGZyb20g"
    "dGhlIGJvdC4iXTsKICAgICAgICB3aHk9clswXSsiIDxjb2RlPigiKyh3LndoeXx8InVua25vd24i"
    "KSsiKTwvY29kZT4iOwogICAgICAgIGZpeD1yWzFdOwogICAgICB9Y2F0Y2goZXJyKXsKICAgICAg"
    "ICB3aHk9IlRoZSBzdG9yZSBjb3VsZCBub3QgY29uZmlybSB3aG8geW91IGFyZS4iOwogICAgICAg"
    "IGZpeD0iT3BlbiBpdCBhZ2FpbiBmcm9tIHRoZSBib3Qgd2l0aCB0aGUgPGI+T3BlbiBTdG9yZTwv"
    "Yj4gYnV0dG9uLiI7CiAgICAgIH0KICAgICAgbG9ja2VkU2NyZWVuKCJDYW5ub3Qgb3BlbiB0aGUg"
    "c3RvcmUiLHdoeSsiPGJyPjxicj4iK2ZpeCk7CiAgICB9CiAgICByZXR1cm47CiAgfQogIGFwcGx5"
    "U3RhdGUoZCk7TElWRT10cnVlOwogIHNob3dTd2l0Y2goISEoTUUmJk1FLmFkbWluKSk7ICAgICAg"
    "ICAgICAvKiBvbmx5IGEgdmVyaWZpZWQgYWRtaW4gc2VlcyBpdCAqLwogIGFwcGx5Q01TKCk7YXBw"
    "bHlGZWF0dXJlcygpO3N5bmNCYWwoKTtyZW5kZXIoKTtyZW5kZXJBZG1pbigpOwp9CmFzeW5jIGZ1"
    "bmN0aW9uIHB1c2gocGF0aCxib2R5LG9rTXNnKXsKICBpZighTElWRSlyZXR1cm4gdHJ1ZTsKICB0"
    "cnl7Y29uc3Qgcj1hd2FpdCBhcGkocGF0aCxib2R5KTtpZihva01zZyl0b2FzdChva01zZyk7cmV0"
    "dXJuIHI7fQogIGNhdGNoKGUpe3RvYXN0KCJDb3VsZCBub3Qgc2F2ZSDigJQgY2hlY2sgeW91ciBj"
    "b25uZWN0aW9uIik7cmV0dXJuIGZhbHNlO30KfQpib290KCk7Cjwvc2NyaXB0Pgo8L2JvZHk+PC9o"
    "dG1sPgo="
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
