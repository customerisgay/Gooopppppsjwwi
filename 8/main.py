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
DESIGN = "8-emerald"


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
    "store_tagline": "Fintech clean",
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
    "storeTag": "Fintech clean",
    "tick1": "<b>Instant delivery</b> on every country",
    "tick2": "Wallet top-ups <b>auto-verified</b>",
    "tick3": "Failed login? <b>Auto refund</b>",
    "tick4": "Daily check-in <b>bonus</b>",
    "heroKick": "Auto-verified payments",
    "heroH1": "Pay once.<br>Credited instantly.",
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
    "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIiBkYXRhLWRlc2lnbj0iZW1lcmFsZCI+Cjxo"
    "ZWFkPgo8bWV0YSBjaGFyc2V0PSJ1dGYtOCI+CjxtZXRhIG5hbWU9InZpZXdwb3J0IiBjb250ZW50"
    "PSJ3aWR0aD1kZXZpY2Utd2lkdGgsaW5pdGlhbC1zY2FsZT0xLHZpZXdwb3J0LWZpdD1jb3Zlcixt"
    "YXhpbXVtLXNjYWxlPTEiPgo8c2NyaXB0IHNyYz0iaHR0cHM6Ly90ZWxlZ3JhbS5vcmcvanMvdGVs"
    "ZWdyYW0td2ViLWFwcC5qcyI+PC9zY3JpcHQ+Cjx0aXRsZT5ab3JvIFRHIFN0b3JlIOKAlCBTdG9y"
    "ZSBNaW5pIEFwcCAoRGVzaWduIDgpPC90aXRsZT4KPHN0eWxlPgo6cm9vdHsKICAtLWJnOiMwNjEy"
    "MGY7IC0tYmcyOiMwYjFhMTY7IC0tY2FyZDojMGQyMDFiOyAtLWNhcmQyOiMxMjMwMjk7CiAgLS1s"
    "aW5lOnJnYmEoNTIsMjExLDE1MywuMTgpOyAtLXR4OiNlNmZmZjY7IC0tdHgyOiM3OWEzOTY7IC0t"
    "YWNjOiMzNGQzOTk7IC0tYWNjMjojYTdmM2QwOwogIC0tb2s6IzM0ZDM5OTsgLS13YXJuOiNmY2Qz"
    "NGQ7IC0tYmFkOiNmYjcxODU7CiAgLS1yOjE2cHg7IC0tcnM6MTNweDsgLS1idG5oOjQ0cHg7IC0t"
    "Zm9udDotYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCwnU2Vnb2UgVUknLFJvYm90byxz"
    "YW5zLXNlcmlmOyAtLWZoZWFkOi1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LCdTZWdv"
    "ZSBVSScsUm9ib3RvLHNhbnMtc2VyaWY7CiAgLS1zaGFkb3c6MCA4cHggMjJweCByZ2JhKDAsMCww"
    "LC40KTsgLS1waWxsZ3JhZDpsaW5lYXItZ3JhZGllbnQoMTM1ZGVnLCMzNGQzOTksIzBlYTVhMyk7"
    "IC0tYnRuYmc6IzFmYmY4ZjsKfQoqe2JveC1zaXppbmc6Ym9yZGVyLWJveDstd2Via2l0LXRhcC1o"
    "aWdobGlnaHQtY29sb3I6dHJhbnNwYXJlbnR9Cmh0bWwsYm9keXttYXJnaW46MDtwYWRkaW5nOjB9"
    "CmJvZHl7YmFja2dyb3VuZDp2YXIoLS1iZyk7Y29sb3I6dmFyKC0tdHgpO2ZvbnQtZmFtaWx5OnZh"
    "cigtLWZvbnQpO2ZvbnQtc2l6ZToxNXB4O2xpbmUtaGVpZ2h0OjEuNDU7bWluLWhlaWdodDoxMDB2"
    "aDtvdmVyZmxvdy14OmhpZGRlbjtwYWRkaW5nLXRvcDozMnB4O30KYm9keTpiZWZvcmV7Y29udGVu"
    "dDoiIjtwb3NpdGlvbjpmaXhlZDtpbnNldDowO3BvaW50ZXItZXZlbnRzOm5vbmU7ei1pbmRleDow"
    "O2JhY2tncm91bmQ6cmFkaWFsLWdyYWRpZW50KDYwJSAzNSUgYXQgNTAlIDAlLHJnYmEoNTIsMjEx"
    "LDE1MywuMTQpLHRyYW5zcGFyZW50IDcwJSl9Cjo6LXdlYmtpdC1zY3JvbGxiYXJ7d2lkdGg6MDto"
    "ZWlnaHQ6MH0KI3NoZWxse3Bvc2l0aW9uOnJlbGF0aXZlO3otaW5kZXg6MTttYXgtd2lkdGg6NTIw"
    "cHg7bWFyZ2luOjAgYXV0bzttaW4taGVpZ2h0OjEwMHZoO3BhZGRpbmc6MCAxNnB4IDk4cHh9Ci5o"
    "aWRkZW57ZGlzcGxheTpub25lIWltcG9ydGFudH0KCiN0b3BiYXJ7cG9zaXRpb246c3RpY2t5O3Rv"
    "cDozMnB4O3otaW5kZXg6NDA7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBw"
    "eDtwYWRkaW5nOjE0cHggMCAxMnB4O2JhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KHZhcigtLWJn"
    "KSA3MiUsdHJhbnNwYXJlbnQpfQoubG9nb3t3aWR0aDozOHB4O2hlaWdodDozOHB4O2JvcmRlci1y"
    "YWRpdXM6MTJweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2ZsZXg6MCAwIGF1dG87"
    "YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzA0MjQxYztmb250LXdlaWdodDo4MDA7"
    "Zm9udC1zaXplOi45cmVtO2xldHRlci1zcGFjaW5nOi0uMDNlbTt9Ci5icmFuZHttaW4td2lkdGg6"
    "MDtmbGV4OjF9Ci5icmFuZCBie2Rpc3BsYXk6YmxvY2s7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQp"
    "O2ZvbnQtc2l6ZTouOTVyZW07Zm9udC13ZWlnaHQ6ODAwO2xldHRlci1zcGFjaW5nOi0uMDNlbTt3"
    "aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXN9"
    "Ci5icmFuZCBzcGFue2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42NnJlbTtjb2xvcjp2YXIoLS10"
    "eDIpO2xldHRlci1zcGFjaW5nOi4wNmVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZX0KLmJhbGNo"
    "aXB7ZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjdweDtoZWlnaHQ6"
    "MzhweDtwYWRkaW5nOjAgMTNweDtib3JkZXItcmFkaXVzOjEycHg7YmFja2dyb3VuZDp2YXIoLS1j"
    "YXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2NvbG9yOnZhcigtLXR4KTtmb250LWZh"
    "bWlseTp2YXIoLS1mb250KTtmb250LXdlaWdodDo3MDA7Zm9udC1zaXplOi44NXJlbTt3aGl0ZS1z"
    "cGFjZTpub3dyYXA7Y3Vyc29yOnBvaW50ZXI7fQouYmFsY2hpcCAucGx1c3tkaXNwbGF5OmdyaWQ7"
    "cGxhY2UtaXRlbXM6Y2VudGVyO3dpZHRoOjIwcHg7aGVpZ2h0OjIwcHg7Ym9yZGVyLXJhZGl1czo3"
    "cHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzA0MjQxYztmb250LXdlaWdodDo4"
    "MDA7Zm9udC1zaXplOi44cmVtO2xpbmUtaGVpZ2h0OjF9CgouY2FyZHtiYWNrZ3JvdW5kOnZhcigt"
    "LWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJhZGl1czp2YXIoLS1y"
    "KTtwYWRkaW5nOjE2cHg7bWFyZ2luLWJvdHRvbToxMnB4O2JveC1zaGFkb3c6dmFyKC0tc2hhZG93"
    "KTt9Ci5jYXJkLnRpZ2h0e3BhZGRpbmc6MTNweH0KLmNhcmQgaDN7bWFyZ2luOjAgMCAzcHg7Zm9u"
    "dC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZTouOTJyZW07Zm9udC13ZWlnaHQ6ODAwO2xl"
    "dHRlci1zcGFjaW5nOi0uMDNlbX0KLmNhcmQgLnN1YnttYXJnaW46MDtjb2xvcjp2YXIoLS10eDIp"
    "O2ZvbnQtc2l6ZTouNzhyZW19Ci5zZWN7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtq"
    "dXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjtnYXA6MTBweDttYXJnaW46MThweCAycHggMTBw"
    "eH0KLnNlYyBoMnttYXJnaW46MDtmb250LWZhbWlseTp2YXIoLS1maGVhZCk7Zm9udC1zaXplOi44"
    "MnJlbTtmb250LXdlaWdodDo4MDA7bGV0dGVyLXNwYWNpbmc6LjE0ZW07dGV4dC10cmFuc2Zvcm06"
    "dXBwZXJjYXNlO2NvbG9yOnZhcigtLXR4Mil9Ci5zZWMgYXtmb250LXNpemU6Ljc1cmVtO2NvbG9y"
    "OnZhcigtLWFjYyk7dGV4dC1kZWNvcmF0aW9uOm5vbmU7Zm9udC13ZWlnaHQ6NjAwO3doaXRlLXNw"
    "YWNlOm5vd3JhcDtjdXJzb3I6cG9pbnRlcn0KCi8qIG9uZSBidXR0b24gc3lzdGVtIOKAlCB0ZXh0"
    "IGNhbiBuZXZlciBvdmVyZmxvdyAqLwouYnRue2Rpc3BsYXk6aW5saW5lLWZsZXg7YWxpZ24taXRl"
    "bXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Z2FwOjdweDtoZWlnaHQ6dmFyKC0tYnRu"
    "aCk7bWluLXdpZHRoOjA7bWF4LXdpZHRoOjEwMCU7cGFkZGluZzowIDE2cHg7Ym9yZGVyOjA7Y3Vy"
    "c29yOnBvaW50ZXI7Ym9yZGVyLXJhZGl1czoxMnB4O2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2Zv"
    "bnQtc2l6ZTouODNyZW07Zm9udC13ZWlnaHQ6NzAwO2xldHRlci1zcGFjaW5nOi4wMWVtO3doaXRl"
    "LXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4dC1vdmVyZmxvdzplbGxpcHNpcztsaW5l"
    "LWhlaWdodDoxO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiMwNDI0MWM7dHJhbnNp"
    "dGlvbjp0cmFuc2Zvcm0gLjE1cyxmaWx0ZXIgLjE1czt9Ci5idG46YWN0aXZle3RyYW5zZm9ybTpz"
    "Y2FsZSguOTcpfQouYnRuOmhvdmVye2ZpbHRlcjpicmlnaHRuZXNzKDEuMDYpfQouYnRuIHN2Z3t3"
    "aWR0aDoxNnB4O2hlaWdodDoxNnB4O2ZsZXg6MCAwIGF1dG99Ci5idG4uYmxvY2t7ZGlzcGxheTpm"
    "bGV4O3dpZHRoOjEwMCV9Ci5idG4uc217aGVpZ2h0OjM2cHg7cGFkZGluZzowIDEycHg7Zm9udC1z"
    "aXplOi43NXJlbTtib3JkZXItcmFkaXVzOjlweH0KLmJ0bi54c3toZWlnaHQ6MzBweDtwYWRkaW5n"
    "OjAgMTBweDtmb250LXNpemU6LjdyZW07Ym9yZGVyLXJhZGl1czo5cHh9Ci5idG4uZ2hvc3R7YmFj"
    "a2dyb3VuZDp0cmFuc3BhcmVudDtjb2xvcjp2YXIoLS10eCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIo"
    "LS1saW5lKX0KLmJ0bi5zb2Z0e2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4"
    "KTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouYnRuLmJhZHtiYWNrZ3JvdW5kOnRyYW5z"
    "cGFyZW50O2NvbG9yOnZhcigtLWJhZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1iYWQpfQouYnRu"
    "W2Rpc2FibGVkXXtvcGFjaXR5Oi40NTtwb2ludGVyLWV2ZW50czpub25lfQouYnRucm93e2Rpc3Bs"
    "YXk6ZmxleDtnYXA6OHB4fQouYnRucm93Pi5idG57ZmxleDoxIDEgMDttaW4td2lkdGg6MH0KCi5w"
    "aWxse2Rpc3BsYXk6aW5saW5lLWZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo1cHg7aGVpZ2h0"
    "OjI0cHg7cGFkZGluZzowIDlweDtib3JkZXItcmFkaXVzOjk5cHg7Zm9udC1zaXplOi42NnJlbTtm"
    "b250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LjA0ZW07YmFja2dyb3VuZDp2YXIoLS1jYXJk"
    "Mik7Y29sb3I6dmFyKC0tdHgyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO3doaXRlLXNw"
    "YWNlOm5vd3JhcH0KLnBpbGwub257Y29sb3I6dmFyKC0tb2spO2JvcmRlci1jb2xvcjpyZ2JhKDUy"
    "LDIxMSwxNTMsLjQ1KX0KLnBpbGwuaG90e2NvbG9yOnZhcigtLXdhcm4pO2JvcmRlci1jb2xvcjpy"
    "Z2JhKDI1MiwyMTEsNzcsLjQ1KX0KLnBpbGwub2Zme2NvbG9yOnZhcigtLWJhZCk7Ym9yZGVyLWNv"
    "bG9yOnJnYmEoMjUxLDExMywxMzMsLjQ1KX0KLmRvdHt3aWR0aDo3cHg7aGVpZ2h0OjdweDtib3Jk"
    "ZXItcmFkaXVzOjUwJTtiYWNrZ3JvdW5kOnZhcigtLW9rKTtib3gtc2hhZG93OjAgMCA4cHggdmFy"
    "KC0tb2spfQoua3Z7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRl"
    "bnQ6c3BhY2UtYmV0d2VlbjtnYXA6MTJweDtwYWRkaW5nOjEwcHggMDtib3JkZXItYm90dG9tOjFw"
    "eCBzb2xpZCB2YXIoLS1saW5lKTtmb250LXNpemU6LjgycmVtO2NvbG9yOnZhcigtLXR4Mil9Ci5r"
    "djpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206MH0KLmt2IGJ7Y29sb3I6dmFyKC0tdHgpO2ZvbnQt"
    "d2VpZ2h0OjcwMDt0ZXh0LWFsaWduOnJpZ2h0O292ZXJmbG93LXdyYXA6YW55d2hlcmV9Ci5mbGFn"
    "e3dpZHRoOjQycHg7aGVpZ2h0OjQycHg7Ym9yZGVyLXJhZGl1czoxMnB4O2Rpc3BsYXk6Z3JpZDtw"
    "bGFjZS1pdGVtczpjZW50ZXI7Zm9udC1zaXplOjEuMzVyZW07ZmxleDowIDAgYXV0bztiYWNrZ3Jv"
    "dW5kOnZhcigtLWNhcmQyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouZmxhZyBzdmd7"
    "d2lkdGg6MThweDtoZWlnaHQ6MThweH0KCi5oZXJve3Bvc2l0aW9uOnJlbGF0aXZlO292ZXJmbG93"
    "OmhpZGRlbjtib3JkZXItcmFkaXVzOjE4cHg7cGFkZGluZzoyMnB4IDE4cHg7bWFyZ2luOjRweCAw"
    "IDE0cHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5kOmxpbmVhci1ncmFk"
    "aWVudCgxNDVkZWcscmdiYSg1MiwyMTEsMTUzLC4xOCkscmdiYSg2LDE4LDE1LC45KSk7Ym9yZGVy"
    "LWNvbG9yOnJnYmEoNTIsMjExLDE1MywuMjgpfQouaGVybyAua2lja3tmb250LXNpemU6LjZyZW07"
    "bGV0dGVyLXNwYWNpbmc6LjJlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7Y29sb3I6dmFyKC0t"
    "YWNjKTtmb250LXdlaWdodDo4MDB9Ci5oZXJvIGgxe21hcmdpbjo4cHggMCA2cHg7Zm9udC1mYW1p"
    "bHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZToxLjc1cmVtO2xpbmUtaGVpZ2h0OjEuMDU7bGV0dGVy"
    "LXNwYWNpbmc6LS4wM2VtO2ZvbnQtd2VpZ2h0OjgwMH0KLmhlcm8gcHttYXJnaW46MCAwIDE0cHg7"
    "Zm9udC1zaXplOi44cmVtO2NvbG9yOnZhcigtLXR4Mik7bWF4LXdpZHRoOjI4MHB4fQouaGVybyAu"
    "YnRucm93e21heC13aWR0aDozMzBweH0KCi5ncmlke2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRl"
    "LWNvbHVtbnM6MWZyIDFmcjtnYXA6MTBweH0KLnBjYXJke2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7"
    "Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXIpO3BhZGRp"
    "bmc6MTRweDtib3gtc2hhZG93OnZhcigtLXNoYWRvdyk7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0"
    "aW9uOmNvbHVtbjtnYXA6OXB4O2N1cnNvcjpwb2ludGVyO30KLnBjYXJkIC50b3B7ZGlzcGxheTpm"
    "bGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjtnYXA6"
    "OHB4fQoucGNhcmQgLm5te2ZvbnQtd2VpZ2h0OjcwMDtmb250LXNpemU6Ljg1cmVtO3doaXRlLXNw"
    "YWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4dC1vdmVyZmxvdzplbGxpcHNpc30KLnBjYXJk"
    "IC5tZXRhe2ZvbnQtc2l6ZTouNjhyZW07Y29sb3I6dmFyKC0tdHgyKX0KLnBjYXJkIC5wcmljZXtm"
    "b250LWZhbWlseTp2YXIoLS1maGVhZCk7Zm9udC1zaXplOjEuMjVyZW07Zm9udC13ZWlnaHQ6ODAw"
    "O2xldHRlci1zcGFjaW5nOi0uMDJlbX0KLnBjYXJkIC5wcmljZSBze2ZvbnQtc2l6ZTouNzJyZW07"
    "Y29sb3I6dmFyKC0tdHgyKTtmb250LXdlaWdodDo0MDA7bWFyZ2luLXJpZ2h0OjZweH0KLnJvd3N7"
    "ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjtnYXA6OXB4fQoucm93e2Rpc3BsYXk6"
    "ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEycHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTti"
    "b3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6dmFyKC0tcik7cGFkZGlu"
    "ZzoxMnB4IDEzcHg7Ym94LXNoYWRvdzp2YXIoLS1zaGFkb3cpO2N1cnNvcjpwb2ludGVyO3RyYW5z"
    "aXRpb246dHJhbnNmb3JtIC4xNXM7fQoucm93OmFjdGl2ZXt0cmFuc2Zvcm06c2NhbGUoLjk5KX0K"
    "LnJvdyAubWlke2ZsZXg6MTttaW4td2lkdGg6MH0KLnJvdyAubWlkIGJ7ZGlzcGxheTpibG9jaztm"
    "b250LXNpemU6Ljg4cmVtO3doaXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4dC1v"
    "dmVyZmxvdzplbGxpcHNpc30KLnJvdyAubWlkIHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXpl"
    "Oi43cmVtO2NvbG9yOnZhcigtLXR4Mik7bWFyZ2luLXRvcDoycHh9Ci5yb3cgLnJ0e3RleHQtYWxp"
    "Z246cmlnaHQ7ZmxleDowIDAgYXV0b30KLnJvdyAucnQgLnB7Zm9udC13ZWlnaHQ6ODAwO2ZvbnQt"
    "c2l6ZTouOTVyZW07d2hpdGUtc3BhY2U6bm93cmFwfQoucm93IC5ydCAucCBze2ZvbnQtd2VpZ2h0"
    "OjQwMDtmb250LXNpemU6LjcycmVtO2NvbG9yOnZhcigtLXR4Mik7bWFyZ2luLXJpZ2h0OjVweH0K"
    "LmNhcm91e2Rpc3BsYXk6ZmxleDtnYXA6MTBweDtvdmVyZmxvdy14OmF1dG87cGFkZGluZzoycHgg"
    "MCA2cHg7bWFyZ2luOjAgLTE2cHggNnB4O3BhZGRpbmctbGVmdDoxNnB4O3BhZGRpbmctcmlnaHQ6"
    "MTZweDtzY3JvbGwtc25hcC10eXBlOnggbWFuZGF0b3J5fQouY2Fyb3U+KntmbGV4OjAgMCAxNzJw"
    "eDtzY3JvbGwtc25hcC1hbGlnbjpzdGFydH0KCm5hdiN0YWJze3Bvc2l0aW9uOmZpeGVkO3otaW5k"
    "ZXg6NjA7bGVmdDo1MCU7Ym90dG9tOmNhbGMoMTJweCArIGVudihzYWZlLWFyZWEtaW5zZXQtYm90"
    "dG9tKSk7dHJhbnNmb3JtOnRyYW5zbGF0ZVgoLTUwJSk7bWF4LXdpZHRoOmNhbGMoMTAwdncgLSAy"
    "MHB4KX0KbmF2I3RhYnMgLmRvY2t7ZGlzcGxheTpmbGV4O2dhcDoycHg7cGFkZGluZzo3cHggOHB4"
    "O2JvcmRlci1yYWRpdXM6MHB4O2JhY2tncm91bmQ6cmdiYSg4LDIyLDE4LC45Nik7Ym9yZGVyOjFw"
    "eCBzb2xpZCB2YXIoLS1saW5lKTtib3gtc2hhZG93OjAgLTZweCAyNHB4IHJnYmEoMCwwLDAsLjUp"
    "fQpuYXYjdGFicyBidXR0b257cG9zaXRpb246cmVsYXRpdmU7ZGlzcGxheTpmbGV4O2ZsZXgtZGly"
    "ZWN0aW9uOmNvbHVtbjthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjtn"
    "YXA6NHB4O21pbi13aWR0aDo1OHB4O2hlaWdodDo0OHB4O3BhZGRpbmc6MCA2cHg7Ym9yZGVyOjA7"
    "YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjdXJzb3I6cG9pbnRlcjtjb2xvcjp2YXIoLS10eDIpO2Zv"
    "bnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouNnJlbTtmb250LXdlaWdodDo3MDA7bGV0"
    "dGVyLXNwYWNpbmc6LjAzZW07Ym9yZGVyLXJhZGl1czoxMnB4O3RyYW5zaXRpb246Y29sb3IgLjJz"
    "LGJhY2tncm91bmQgLjJzO3doaXRlLXNwYWNlOm5vd3JhcH0KbmF2I3RhYnMgYnV0dG9uIHN2Z3t3"
    "aWR0aDoxOXB4O2hlaWdodDoxOXB4fQpuYXYjdGFicyBidXR0b24ub257Y29sb3I6IzA0MjQxYzti"
    "YWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKX0KCi5zdWJuYXZ7ZGlzcGxheTpmbGV4O2dhcDo3cHg7"
    "b3ZlcmZsb3cteDphdXRvO3BhZGRpbmc6MnB4IDAgMTJweDttYXJnaW46MCAtMnB4fQouc3VibmF2"
    "IGJ1dHRvbntmbGV4OjAgMCBhdXRvO2hlaWdodDozNHB4O3BhZGRpbmc6MCAxM3B4O2JvcmRlci1y"
    "YWRpdXM6OXB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIoLS1j"
    "YXJkMik7Y29sb3I6dmFyKC0tdHgyKTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXNpemU6"
    "Ljc0cmVtO2ZvbnQtd2VpZ2h0OjcwMDtjdXJzb3I6cG9pbnRlcjt3aGl0ZS1zcGFjZTpub3dyYXB9"
    "Ci5zdWJuYXYgYnV0dG9uLm9ue2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiMwNDI0"
    "MWM7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50fQoua3Bpc3tkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1w"
    "bGF0ZS1jb2x1bW5zOjFmciAxZnI7Z2FwOjEwcHg7bWFyZ2luLWJvdHRvbToxMnB4fQoua3Bpe2Jh"
    "Y2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXIt"
    "cmFkaXVzOnZhcigtLXJzKTtwYWRkaW5nOjEzcHg7Ym94LXNoYWRvdzp2YXIoLS1zaGFkb3cpfQou"
    "a3BpIHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42NHJlbTtjb2xvcjp2YXIoLS10eDIp"
    "O2xldHRlci1zcGFjaW5nOi4xZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlfQoua3BpIGJ7ZGlz"
    "cGxheTpibG9jazttYXJnaW4tdG9wOjVweDtmb250LWZhbWlseTp2YXIoLS1maGVhZCk7Zm9udC1z"
    "aXplOjEuMzVyZW07Zm9udC13ZWlnaHQ6ODAwO2xldHRlci1zcGFjaW5nOi0uMDJlbX0KLmlucCxz"
    "ZWxlY3QuaW5we3dpZHRoOjEwMCU7aGVpZ2h0OnZhcigtLWJ0bmgpO3BhZGRpbmc6MCAxM3B4O2Jv"
    "cmRlci1yYWRpdXM6MTJweDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6"
    "dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4KTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250"
    "LXNpemU6LjgzcmVtO291dGxpbmU6bm9uZTstd2Via2l0LWFwcGVhcmFuY2U6bm9uZTthcHBlYXJh"
    "bmNlOm5vbmV9CnNlbGVjdC5pbnB7YmFja2dyb3VuZC1pbWFnZTpub25lfQouaW5wOjpwbGFjZWhv"
    "bGRlcntjb2xvcjp2YXIoLS10eDIpO29wYWNpdHk6Ljc1fQouaW5wOmZvY3Vze2JvcmRlci1jb2xv"
    "cjp2YXIoLS1hY2MpfQp0ZXh0YXJlYS5pbnB7aGVpZ2h0OmF1dG87bWluLWhlaWdodDo0MHB4O3Bh"
    "ZGRpbmc6MTBweCAxM3B4O2xpbmUtaGVpZ2h0OjEuNTtyZXNpemU6dmVydGljYWx9Ci5jbXNyb3cu"
    "aGlkZXtkaXNwbGF5Om5vbmV9Ci5pY29uY2hpcHtwb3NpdGlvbjpyZWxhdGl2ZTtoZWlnaHQ6MzRw"
    "eDttaW4td2lkdGg6MzRweDtwYWRkaW5nOjAgOXB4O2JvcmRlci1yYWRpdXM6OTlweDtib3JkZXI6"
    "MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigt"
    "LXR4KTtkaXNwbGF5OmlubGluZS1ncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtjdXJzb3I6cG9pbnRl"
    "cn0KLmljb25jaGlwIHN2Z3t3aWR0aDoxN3B4O2hlaWdodDoxN3B4fQouaWNvbmNoaXAgaXtwb3Np"
    "dGlvbjphYnNvbHV0ZTt0b3A6LTVweDtyaWdodDotNXB4O21pbi13aWR0aDoxN3B4O2hlaWdodDox"
    "N3B4O3BhZGRpbmc6MCA0cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2JhY2tncm91bmQ6dmFyKC0tYWNj"
    "KTtjb2xvcjojMDQyNDFjO2ZvbnQtc2l6ZTouNnJlbTtmb250LXN0eWxlOm5vcm1hbDtmb250LXdl"
    "aWdodDo4MDA7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcn0KLnN0YXJze2Rpc3BsYXk6"
    "aW5saW5lLWZsZXg7Z2FwOjFweDt2ZXJ0aWNhbC1hbGlnbjptaWRkbGV9Ci5zdGFycyBzdmd7d2lk"
    "dGg6MTFweDtoZWlnaHQ6MTFweDtjb2xvcjojZjVhNjIzfQouc3RhcnMgYntmb250LXNpemU6LjY2"
    "cmVtO2NvbG9yOnZhcigtLXR4Mik7Zm9udC13ZWlnaHQ6NzAwO21hcmdpbi1sZWZ0OjNweH0KLmhl"
    "YXJ0e3Bvc2l0aW9uOmFic29sdXRlO3RvcDo4cHg7cmlnaHQ6OHB4O3dpZHRoOjI2cHg7aGVpZ2h0"
    "OjI2cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7"
    "Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjdXJz"
    "b3I6cG9pbnRlcjt6LWluZGV4OjJ9Ci5oZWFydCBzdmd7d2lkdGg6MTNweDtoZWlnaHQ6MTNweDtj"
    "b2xvcjp2YXIoLS10eDIpfQouaGVhcnQub257YmFja2dyb3VuZDp2YXIoLS1hY2MpO2JvcmRlci1j"
    "b2xvcjp0cmFuc3BhcmVudH0KLmhlYXJ0Lm9uIHN2Z3tjb2xvcjojMDQyNDFjfQoucXR5e2Rpc3Bs"
    "YXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjA7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1s"
    "aW5lKTtib3JkZXItcmFkaXVzOjEycHg7b3ZlcmZsb3c6aGlkZGVufQoucXR5IGJ1dHRvbnt3aWR0"
    "aDozOHB4O2hlaWdodDozOHB4O2JvcmRlcjowO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9y"
    "OnZhcigtLXR4KTtmb250LXNpemU6MXJlbTtmb250LXdlaWdodDo4MDA7Y3Vyc29yOnBvaW50ZXJ9"
    "Ci5xdHkgYnttaW4td2lkdGg6MzhweDt0ZXh0LWFsaWduOmNlbnRlcjtmb250LXNpemU6Ljg1cmVt"
    "fQoudGx7bWFyZ2luOjEwcHggMCAwO3BhZGRpbmctbGVmdDoxNnB4O2JvcmRlci1sZWZ0OjJweCBz"
    "b2xpZCB2YXIoLS1saW5lKX0KLnRsIGRpdntwb3NpdGlvbjpyZWxhdGl2ZTtwYWRkaW5nOjAgMCAx"
    "MXB4IDEwcHg7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpfQoudGwgZGl2Omxhc3Qt"
    "Y2hpbGR7cGFkZGluZy1ib3R0b206MH0KLnRsIGRpdjo6YmVmb3Jle2NvbnRlbnQ6IiI7cG9zaXRp"
    "b246YWJzb2x1dGU7bGVmdDotMjFweDt0b3A6NHB4O3dpZHRoOjhweDtoZWlnaHQ6OHB4O2JvcmRl"
    "ci1yYWRpdXM6OTlweDtiYWNrZ3JvdW5kOnZhcigtLWxpbmUpfQoudGwgZGl2Lm9rOjpiZWZvcmV7"
    "YmFja2dyb3VuZDp2YXIoLS1vayl9Ci50bCBkaXYub2sgYntjb2xvcjp2YXIoLS10eCl9Ci50bCBz"
    "bWFsbHtkaXNwbGF5OmJsb2NrO2ZvbnQtc2l6ZTouNjZyZW07b3BhY2l0eTouOH0KLmZjYXJke2Rp"
    "c3BsYXk6ZmxleDthbGlnbi1pdGVtczpmbGV4LXN0YXJ0O2dhcDoxMXB4O3BhZGRpbmc6MTFweCAw"
    "O2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouZmNhcmQ6bGFzdC1jaGlsZHti"
    "b3JkZXItYm90dG9tOjB9Ci5mY2FyZCAudHtmbGV4OjE7bWluLXdpZHRoOjB9Ci5mY2FyZCAudCBi"
    "e2ZvbnQtc2l6ZTouODFyZW07ZGlzcGxheTpibG9ja30KLmZjYXJkIC50IHNtYWxse2Rpc3BsYXk6"
    "YmxvY2s7Zm9udC1zaXplOi42OXJlbTtjb2xvcjp2YXIoLS10eDIpO2xpbmUtaGVpZ2h0OjEuNDU7"
    "bWFyZ2luLXRvcDoycHh9Ci5mcm93LmhpZGUsLmZncnAuaGlkZXtkaXNwbGF5Om5vbmV9Ci5iYXJz"
    "e2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpmbGV4LWVuZDtnYXA6NXB4O2hlaWdodDo4NnB4O21h"
    "cmdpbi10b3A6MTJweH0KLmJhcnMgaXtmbGV4OjE7Ym9yZGVyLXJhZGl1czo2cHggNnB4IDNweCAz"
    "cHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7ZGlzcGxheTpibG9ja30KLmZ1bm5lbCBkaXZ7"
    "ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6OXB4O21hcmdpbi10b3A6OXB4O2Zv"
    "bnQtc2l6ZTouNzRyZW19Ci5mdW5uZWwgaXtoZWlnaHQ6MjZweDtib3JkZXItcmFkaXVzOjdweDti"
    "YWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtkaXNwbGF5OmJsb2NrfQouZmVlZGxpbmV7ZGlzcGxh"
    "eTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6OXB4O2ZvbnQtc2l6ZTouNzNyZW07Y29sb3I6"
    "dmFyKC0tdHgyKTtwYWRkaW5nOjdweCAwO2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxp"
    "bmUpfQouZmVlZGxpbmU6bGFzdC1jaGlsZHtib3JkZXItYm90dG9tOjB9Ci5mZWVkbGluZSBie2Nv"
    "bG9yOnZhcigtLXR4KX0KLmxie2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEw"
    "cHg7cGFkZGluZzo5cHggMDtib3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIoLS1saW5lKX0KLmxi"
    "Omxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRvbTowfQoubGIgLnJre3dpZHRoOjI0cHg7aGVpZ2h0OjI0"
    "cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9u"
    "dC1zaXplOi43cmVtO2ZvbnQtd2VpZ2h0OjgwMDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xv"
    "cjp2YXIoLS10eDIpfQoubGIgLnJrLmd7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6"
    "IzA0MjQxY30KLmNtc2dycC5oaWRle2Rpc3BsYXk6bm9uZX0KLmNtc3JvdyBsYWJlbCBie2NvbG9y"
    "OnZhcigtLWFjYyk7Zm9udC13ZWlnaHQ6NzAwfQoudGltZXJiYXJ7aGVpZ2h0OjVweDtib3JkZXIt"
    "cmFkaXVzOjk5cHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7b3ZlcmZsb3c6aGlkZGVuO21hcmdp"
    "bjoxMHB4IDAgMnB4fQoudGltZXJiYXIgaXtkaXNwbGF5OmJsb2NrO2hlaWdodDoxMDAlO2JhY2tn"
    "cm91bmQ6dmFyKC0tcGlsbGdyYWQpO3RyYW5zaXRpb246d2lkdGggMXMgbGluZWFyfQoudGltZXJi"
    "YXIubG93IGl7YmFja2dyb3VuZDp2YXIoLS1iYWQsI2U1NDg0ZCl9Ci5maWVsZHttYXJnaW4tYm90"
    "dG9tOjlweH0KLmZpZWxkIGxhYmVse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42OHJlbTtjb2xv"
    "cjp2YXIoLS10eDIpO2xldHRlci1zcGFjaW5nOi4wOWVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2Fz"
    "ZTttYXJnaW46MCAwIDVweCAycHg7Zm9udC13ZWlnaHQ6NzAwfQouc3dhcHtkaXNwbGF5OmZsZXg7"
    "YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpzcGFjZS1iZXR3ZWVuO2dhcDoxMnB4"
    "O3BhZGRpbmc6MTFweCAwO2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouc3dh"
    "cDpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206MH0KLnN3YXAgLnR7Zm9udC1zaXplOi44MnJlbTtm"
    "b250LXdlaWdodDo2MDA7bWluLXdpZHRoOjB9Ci5zd2FwIC50IHNtYWxse2Rpc3BsYXk6YmxvY2s7"
    "Zm9udC13ZWlnaHQ6NDAwO2ZvbnQtc2l6ZTouN3JlbTtjb2xvcjp2YXIoLS10eDIpfQoudGd7d2lk"
    "dGg6NDRweDtoZWlnaHQ6MjZweDtib3JkZXItcmFkaXVzOjk5cHg7YmFja2dyb3VuZDp2YXIoLS1j"
    "YXJkMik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtwb3NpdGlvbjpyZWxhdGl2ZTtjdXJz"
    "b3I6cG9pbnRlcjtmbGV4OjAgMCBhdXRvfQoudGc6YWZ0ZXJ7Y29udGVudDoiIjtwb3NpdGlvbjph"
    "YnNvbHV0ZTt0b3A6M3B4O2xlZnQ6M3B4O3dpZHRoOjE4cHg7aGVpZ2h0OjE4cHg7Ym9yZGVyLXJh"
    "ZGl1czo1MCU7YmFja2dyb3VuZDp2YXIoLS10eDIpO3RyYW5zaXRpb246LjIyc30KLnRnLm9ue2Jh"
    "Y2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVudH0KLnRnLm9u"
    "OmFmdGVye2xlZnQ6MjFweDtiYWNrZ3JvdW5kOiMwNDI0MWN9Ci50Zy5zbXt3aWR0aDozNnB4O2hl"
    "aWdodDoyMXB4fQoudGcuc206YWZ0ZXJ7d2lkdGg6MTRweDtoZWlnaHQ6MTRweDt0b3A6Mi41cHh9"
    "Ci50Zy5zbS5vbjphZnRlcntsZWZ0OjE4cHh9Ci5zdGFye3dpZHRoOjMwcHg7aGVpZ2h0OjMwcHg7"
    "Ym9yZGVyLXJhZGl1czo5cHg7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtib3JkZXI6"
    "MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigt"
    "LXR4Mik7Y3Vyc29yOnBvaW50ZXI7ZmxleDowIDAgYXV0b30KLnN0YXIub257YmFja2dyb3VuZDp2"
    "YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzA0MjQxYztib3JkZXItY29sb3I6dHJhbnNwYXJlbnR9Ci5z"
    "dGFyIHN2Z3t3aWR0aDoxNHB4O2hlaWdodDoxNHB4fQoKI3NoZWV0e3Bvc2l0aW9uOmZpeGVkO2lu"
    "c2V0OjA7ei1pbmRleDo5MDtkaXNwbGF5Om5vbmV9CiNzaGVldC5vcGVue2Rpc3BsYXk6YmxvY2t9"
    "CiNzaGVldCAuYmd7cG9zaXRpb246YWJzb2x1dGU7aW5zZXQ6MDtiYWNrZ3JvdW5kOnJnYmEoMiwx"
    "NiwxMiwuNjUpO2JhY2tkcm9wLWZpbHRlcjpibHVyKDNweCl9CiNzaGVldCAucGFuZWx7cG9zaXRp"
    "b246YWJzb2x1dGU7bGVmdDo1MCU7Ym90dG9tOjA7dHJhbnNmb3JtOnRyYW5zbGF0ZVgoLTUwJSk7"
    "d2lkdGg6MTAwJTttYXgtd2lkdGg6NTIwcHg7YmFja2dyb3VuZDp2YXIoLS1iZzIpO2JvcmRlcjox"
    "cHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLWJvdHRvbTowO2JvcmRlci1yYWRpdXM6MjRweCAy"
    "NHB4IDAgMDtwYWRkaW5nOjE4cHggMTZweCBjYWxjKDIwcHggKyBlbnYoc2FmZS1hcmVhLWluc2V0"
    "LWJvdHRvbSkpO2FuaW1hdGlvbjp1cCAuMjhzIGN1YmljLWJlemllciguMiwuOSwuMywxKTttYXgt"
    "aGVpZ2h0Ojkydmg7b3ZlcmZsb3c6YXV0b30KQGtleWZyYW1lcyB1cHtmcm9te3RyYW5zZm9ybTp0"
    "cmFuc2xhdGUoLTUwJSwxMDAlKX10b3t0cmFuc2Zvcm06dHJhbnNsYXRlKC01MCUsMCl9fQojc2hl"
    "ZXQgLmdyYWJ7d2lkdGg6MzhweDtoZWlnaHQ6NHB4O2JvcmRlci1yYWRpdXM6OXB4O2JhY2tncm91"
    "bmQ6dmFyKC0tbGluZSk7bWFyZ2luOjAgYXV0byAxNHB4fQoub3RwYm94e2ZvbnQtZmFtaWx5OnVp"
    "LW1vbm9zcGFjZSxNZW5sbyxDb25zb2xhcyxtb25vc3BhY2U7Zm9udC1zaXplOjEuNnJlbTtsZXR0"
    "ZXItc3BhY2luZzouMzVlbTt0ZXh0LWFsaWduOmNlbnRlcjtwYWRkaW5nOjE2cHg7Ym9yZGVyLXJh"
    "ZGl1czp2YXIoLS1ycyk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9yZGVyOjFweCBkYXNoZWQg"
    "dmFyKC0tbGluZSk7bWFyZ2luOjEwcHggMH0KLnFye3dpZHRoOjE1MHB4O2hlaWdodDoxNTBweDtt"
    "YXJnaW46NnB4IGF1dG8gMTBweDtib3JkZXItcmFkaXVzOnZhcigtLXJzKTtiYWNrZ3JvdW5kOnZh"
    "cigtLWNhcmQyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2Rpc3BsYXk6Z3JpZDtwbGFj"
    "ZS1pdGVtczpjZW50ZXI7Y29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY1cmVtO2xldHRlci1z"
    "cGFjaW5nOi4xZW07dGV4dC1hbGlnbjpjZW50ZXJ9Ci5zdGVwc3ttYXJnaW46MTJweCAwIDRweH0K"
    "LnN0ZXB7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjhw"
    "eCAwO2ZvbnQtc2l6ZTouNzlyZW07Y29sb3I6dmFyKC0tdHgyKX0KLnN0ZXAgaXt3aWR0aDoyMnB4"
    "O2hlaWdodDoyMnB4O2JvcmRlci1yYWRpdXM6NTAlO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGlu"
    "ZSk7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtmb250LXN0eWxlOm5vcm1hbDtmb250"
    "LXNpemU6LjY1cmVtO2ZvbnQtd2VpZ2h0OjgwMDtmbGV4OjAgMCBhdXRvfQouc3RlcC5vbntjb2xv"
    "cjp2YXIoLS10eCl9Ci5zdGVwLm9uIGl7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6"
    "IzA0MjQxYztib3JkZXItY29sb3I6dHJhbnNwYXJlbnR9Ci5zdGVwLnJ1biBpe2JvcmRlci1jb2xv"
    "cjp2YXIoLS1hY2MpO2NvbG9yOnZhcigtLWFjYyk7YW5pbWF0aW9uOnB1bHNlIDFzIGluZmluaXRl"
    "fQpAa2V5ZnJhbWVzIHB1bHNlezUwJXtvcGFjaXR5Oi4zNX19Ci50b2FzdHtwb3NpdGlvbjpmaXhl"
    "ZDtsZWZ0OjUwJTtib3R0b206Y2FsYyg4OHB4ICsgZW52KHNhZmUtYXJlYS1pbnNldC1ib3R0b20p"
    "KTt0cmFuc2Zvcm06dHJhbnNsYXRlWCgtNTAlKTt6LWluZGV4OjEyMDtiYWNrZ3JvdW5kOnZhcigt"
    "LWJnMik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtjb2xvcjp2YXIoLS10eCk7cGFkZGlu"
    "ZzoxMXB4IDE2cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2ZvbnQtc2l6ZTouNzhyZW07Zm9udC13ZWln"
    "aHQ6NjAwO2JveC1zaGFkb3c6dmFyKC0tc2hhZG93KTtvcGFjaXR5OjA7cG9pbnRlci1ldmVudHM6"
    "bm9uZTt0cmFuc2l0aW9uOm9wYWNpdHkgLjJzLHRyYW5zZm9ybSAuMnM7d2hpdGUtc3BhY2U6bm93"
    "cmFwO21heC13aWR0aDo5MHZ3O292ZXJmbG93OmhpZGRlbjt0ZXh0LW92ZXJmbG93OmVsbGlwc2lz"
    "fQoudG9hc3Quc2hvd3tvcGFjaXR5OjE7dHJhbnNmb3JtOnRyYW5zbGF0ZVgoLTUwJSkgdHJhbnNs"
    "YXRlWSgtNnB4KX0KCi8qIHByZXZpZXcgY2hyb21lIOKAlCBtb2NrdXAgb25seSwgb3duIHN0cmlw"
    "IHNvIGl0IG5ldmVyIG92ZXJsYXBzIHRoZSBVSSAqLwojc3d7cG9zaXRpb246Zml4ZWQ7dG9wOjA7"
    "bGVmdDowO3JpZ2h0OjA7aGVpZ2h0OjMycHg7ei1pbmRleDoyMDA7ZGlzcGxheTpmbGV4O2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2dhcDo1cHg7CiAgYmFja2dyb3Vu"
    "ZDpyZ2JhKDExLDI2LDIyLC45KTtib3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIoLS1saW5lKTti"
    "YWNrZHJvcC1maWx0ZXI6Ymx1cigxMHB4KX0KI3N3IGVte2ZvbnQtc3R5bGU6bm9ybWFsO2ZvbnQt"
    "c2l6ZTouNTZyZW07bGV0dGVyLXNwYWNpbmc6LjE4ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNl"
    "O2NvbG9yOnZhcigtLXR4Mik7bWFyZ2luLXJpZ2h0OjRweH0KI3N3IGJ1dHRvbntoZWlnaHQ6MjJw"
    "eDtwYWRkaW5nOjAgMTJweDtib3JkZXItcmFkaXVzOjk5cHg7Ym9yZGVyOjA7Y3Vyc29yOnBvaW50"
    "ZXI7YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtZmFtaWx5OnZh"
    "cigtLWZvbnQpO2ZvbnQtc2l6ZTouNnJlbTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6"
    "LjA3ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlfQojc3cgYnV0dG9uLm9ue2JhY2tncm91bmQ6"
    "dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiMwNDI0MWN9CgovKiDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZAgQVVST1JBIFVJIEtJVCDigJQgcG9ydGVkIGNvbXBvbmVu"
    "dHMg4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQICovCkBwcm9w"
    "ZXJ0eSAtLWdhe3N5bnRheDoiPGFuZ2xlPiI7aW5pdGlhbC12YWx1ZTowZGVnO2luaGVyaXRzOmZh"
    "bHNlfQpAcHJvcGVydHkgLS1ncHtzeW50YXg6IjxwZXJjZW50YWdlPiI7aW5pdGlhbC12YWx1ZTo1"
    "JTtpbmhlcml0czpmYWxzZX0KQHByb3BlcnR5IC0tZ297c3ludGF4OiI8YW5nbGU+Ijtpbml0aWFs"
    "LXZhbHVlOjBkZWc7aW5oZXJpdHM6ZmFsc2V9CgovKiAyMyDCtyBTaGlueSBDVEEg4oCUIGNvbmlj"
    "IGJvcmRlciArIGRvdCBtYXNrICsgaW5uZXIgc2hpbW1lciArIGJyZWF0aGUgKi8KLmJ0bi5jdGF7"
    "LS1kdXI6My4yczstLWhsOiNiOGZmZTY7cG9zaXRpb246cmVsYXRpdmU7aXNvbGF0aW9uOmlzb2xh"
    "dGU7Ym9yZGVyOjFweCBzb2xpZCB0cmFuc3BhcmVudDsKICBiYWNrZ3JvdW5kOmxpbmVhci1ncmFk"
    "aWVudCh2YXIoLS1idG5iZyksdmFyKC0tYnRuYmcpKSBwYWRkaW5nLWJveCwKICAgIGNvbmljLWdy"
    "YWRpZW50KGZyb20gY2FsYyh2YXIoLS1nYSkgLSB2YXIoLS1nbykpLHRyYW5zcGFyZW50LHZhcigt"
    "LWhsKSB2YXIoLS1ncCksCiAgICAgICNmZmYgY2FsYyh2YXIoLS1ncCkqMiksdmFyKC0taGwpIGNh"
    "bGModmFyKC0tZ3ApKjMpLHRyYW5zcGFyZW50IGNhbGModmFyKC0tZ3ApKjQpKSBib3JkZXItYm94"
    "OwogIGJveC1zaGFkb3c6aW5zZXQgMCAwIDAgMXB4IHJnYmEoMjU1LDI1NSwyNTUsLjA4KTsKICB0"
    "cmFuc2l0aW9uOi0tZ28gLjhzIGN1YmljLWJlemllciguMjUsMSwuNSwxKSwtLWdwIC44cyBjdWJp"
    "Yy1iZXppZXIoLjI1LDEsLjUsMSksdHJhbnNmb3JtIC4xNXM7CiAgYW5pbWF0aW9uOmdhIHZhcigt"
    "LWR1cikgbGluZWFyIGluZmluaXRlfQouYnRuLmN0YTpiZWZvcmUsLmJ0bi5jdGE6YWZ0ZXJ7Y29u"
    "dGVudDoiIjtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjUwJTt0b3A6NTAlO3RyYW5zbGF0ZTotNTAl"
    "IC01MCU7ei1pbmRleDotMTtwb2ludGVyLWV2ZW50czpub25lfQouYnRuLmN0YTpiZWZvcmV7d2lk"
    "dGg6Y2FsYygxMDAlIC0gNnB4KTtoZWlnaHQ6Y2FsYygxMDAlIC0gNnB4KTtib3JkZXItcmFkaXVz"
    "OmluaGVyaXQ7b3BhY2l0eTouMzU7CiAgYmFja2dyb3VuZDpyYWRpYWwtZ3JhZGllbnQoY2lyY2xl"
    "IGF0IDJweCAycHgsI2ZmZiAuNXB4LHRyYW5zcGFyZW50IDApIHBhZGRpbmctYm94O2JhY2tncm91"
    "bmQtc2l6ZTo0cHggNHB4OwogIC13ZWJraXQtbWFzay1pbWFnZTpjb25pYy1ncmFkaWVudChmcm9t"
    "IGNhbGModmFyKC0tZ2EpICsgNDVkZWcpLCMwMDAsdHJhbnNwYXJlbnQgMTAlIDkwJSwjMDAwKTsK"
    "ICBtYXNrLWltYWdlOmNvbmljLWdyYWRpZW50KGZyb20gY2FsYyh2YXIoLS1nYSkgKyA0NWRlZyks"
    "IzAwMCx0cmFuc3BhcmVudCAxMCUgOTAlLCMwMDApfQouYnRuLmN0YTphZnRlcnt3aWR0aDoxMDAl"
    "O2FzcGVjdC1yYXRpbzoxO29wYWNpdHk6LjU1OwogIGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50"
    "KC01MGRlZyx0cmFuc3BhcmVudCx2YXIoLS1obCksdHJhbnNwYXJlbnQpOwogIC13ZWJraXQtbWFz"
    "ay1pbWFnZTpyYWRpYWwtZ3JhZGllbnQoY2lyY2xlIGF0IGJvdHRvbSx0cmFuc3BhcmVudCA0MCUs"
    "IzAwMCk7CiAgbWFzay1pbWFnZTpyYWRpYWwtZ3JhZGllbnQoY2lyY2xlIGF0IGJvdHRvbSx0cmFu"
    "c3BhcmVudCA0MCUsIzAwMCk7CiAgYW5pbWF0aW9uOnNoaW1tZXIgdmFyKC0tZHVyKSBsaW5lYXIg"
    "aW5maW5pdGV9Ci5idG4uY3RhOmhvdmVyey0tZ3A6MjIlOy0tZ286OTVkZWd9Ci5idG4uY3RhOmFj"
    "dGl2ZXt0cmFuc2xhdGU6MCAxcHh9CkBrZXlmcmFtZXMgZ2F7dG97LS1nYTozNjBkZWd9fQpAa2V5"
    "ZnJhbWVzIHNoaW1tZXJ7dG97cm90YXRlOjM2MGRlZ319CgovKiAyMiDCtyBMaXF1aWQgZ2xhc3Mg"
    "Ki8KLmJ0bi5nbGFzcywuZ2xhc3NjYXJke3Bvc2l0aW9uOnJlbGF0aXZlO2JhY2tncm91bmQ6Y29s"
    "b3ItbWl4KGluIHNyZ2IsdmFyKC0tY2FyZDIpIDcwJSx0cmFuc3BhcmVudCk7CiAgYm9yZGVyOjFw"
    "eCBzb2xpZCBjb2xvci1taXgoaW4gc3JnYiwjZmZmIDE2JSx2YXIoLS1saW5lKSk7Y29sb3I6dmFy"
    "KC0tdHgpOwogIGJhY2tkcm9wLWZpbHRlcjpibHVyKDE0cHgpIHNhdHVyYXRlKDEuNCk7LXdlYmtp"
    "dC1iYWNrZHJvcC1maWx0ZXI6Ymx1cigxNHB4KSBzYXR1cmF0ZSgxLjQpOwogIGJveC1zaGFkb3c6"
    "aW5zZXQgMXB4IDFweCAxcHggLS41cHggcmdiYSgyNTUsMjU1LDI1NSwuNTUpLGluc2V0IC0xcHgg"
    "LTFweCAxcHggLS41cHggcmdiYSgyNTUsMjU1LDI1NSwuMyksCiAgICBpbnNldCAwIDAgOHB4IDZw"
    "eCByZ2JhKDI1NSwyNTUsMjU1LC4wNyksMCAycHggMTBweCByZ2JhKDAsMCwwLC4xMil9Ci5idG4u"
    "Z2xhc3M6YWZ0ZXJ7Y29udGVudDoiIjtwb3NpdGlvbjphYnNvbHV0ZTtpbnNldDoxcHggMXB4IGF1"
    "dG87aGVpZ2h0OjQyJTtib3JkZXItcmFkaXVzOmluaGVyaXQ7CiAgYmFja2dyb3VuZDpsaW5lYXIt"
    "Z3JhZGllbnQoMTgwZGVnLHJnYmEoMjU1LDI1NSwyNTUsLjM0KSx0cmFuc3BhcmVudCk7cG9pbnRl"
    "ci1ldmVudHM6bm9uZX0KLmJ0bi5nbGFzczpob3Zlcnt0cmFuc2Zvcm06c2NhbGUoMS4wMyl9Cgov"
    "KiBwcmVzcyByaXBwbGUgKGtpdCBkb2NrL2J1dHRvbiBmZWVsKSAqLwoucmlwe3Bvc2l0aW9uOmFi"
    "c29sdXRlO2JvcmRlci1yYWRpdXM6NTAlO3RyYW5zZm9ybTp0cmFuc2xhdGUoLTUwJSwtNTAlKSBz"
    "Y2FsZSgwKTtwb2ludGVyLWV2ZW50czpub25lOwogIGJhY2tncm91bmQ6cmFkaWFsLWdyYWRpZW50"
    "KGNpcmNsZSxyZ2JhKDI1NSwyNTUsMjU1LC41NSksdHJhbnNwYXJlbnQgNzAlKTthbmltYXRpb246"
    "cmlwIC41NXMgZWFzZS1vdXQgZm9yd2FyZHN9CkBrZXlmcmFtZXMgcmlwe3Rve3RyYW5zZm9ybTp0"
    "cmFuc2xhdGUoLTUwJSwtNTAlKSBzY2FsZSgxKTtvcGFjaXR5OjB9fQoKLyogMjQgwrcgQW5pbWF0"
    "ZWQgYmFubmVyICsgMTcgwrcgbG9nbyBjbG91ZCBtYXJxdWVlICovCi5iYW5uZXJ7cG9zaXRpb246"
    "cmVsYXRpdmU7b3ZlcmZsb3c6aGlkZGVuO2JvcmRlci1yYWRpdXM6MTJweDtib3JkZXI6MXB4IHNv"
    "bGlkIHZhcigtLWxpbmUpOwogIGJhY2tncm91bmQ6dmFyKC0tY2FyZCk7bWFyZ2luOjJweCAwIDEy"
    "cHg7aGVpZ2h0OjM4cHg7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjsKICAtd2Via2l0"
    "LW1hc2staW1hZ2U6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRyYW5zcGFyZW50LCMwMDAgOCUsIzAw"
    "MCA5MiUsdHJhbnNwYXJlbnQpOwogIG1hc2staW1hZ2U6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRy"
    "YW5zcGFyZW50LCMwMDAgOCUsIzAwMCA5MiUsdHJhbnNwYXJlbnQpfQouYmFubmVyIC50cmFja3tk"
    "aXNwbGF5OmZsZXg7Z2FwOjMwcHg7d2hpdGUtc3BhY2U6bm93cmFwO2FuaW1hdGlvbjptYXJxIDI2"
    "cyBsaW5lYXIgaW5maW5pdGU7d2lsbC1jaGFuZ2U6dHJhbnNmb3JtfQouYmFubmVyOmhvdmVyIC50"
    "cmFja3thbmltYXRpb24tcGxheS1zdGF0ZTpwYXVzZWR9Ci5iYW5uZXIgc3Bhbntmb250LXNpemU6"
    "LjdyZW07Y29sb3I6dmFyKC0tdHgyKTtmb250LXdlaWdodDo2MDA7bGV0dGVyLXNwYWNpbmc6LjAz"
    "ZW07ZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjZweH0KLmJhbm5l"
    "ciBzcGFuIGJ7Y29sb3I6dmFyKC0tYWNjKX0KLmJhbm5lciBzcGFuIHN2Z3t3aWR0aDoxM3B4O2hl"
    "aWdodDoxM3B4O2NvbG9yOnZhcigtLWFjYyl9CkBrZXlmcmFtZXMgbWFycXt0b3t0cmFuc2Zvcm06"
    "dHJhbnNsYXRlWCgtNTAlKX19Ci5jbG91ZHtkaXNwbGF5OmZsZXg7Z2FwOjI2cHg7b3ZlcmZsb3c6"
    "aGlkZGVuO3BhZGRpbmc6NHB4IDAgMnB4OwogIC13ZWJraXQtbWFzay1pbWFnZTpsaW5lYXItZ3Jh"
    "ZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCAxMiUsIzAwMCA4OCUsdHJhbnNwYXJlbnQpOwog"
    "IG1hc2staW1hZ2U6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRyYW5zcGFyZW50LCMwMDAgMTIlLCMw"
    "MDAgODglLHRyYW5zcGFyZW50KX0KLmNsb3VkIC50e2Rpc3BsYXk6ZmxleDtnYXA6MjZweDthbmlt"
    "YXRpb246bWFycSAzMHMgbGluZWFyIGluZmluaXRlfQouY2xvdWQgaXtmb250LXN0eWxlOm5vcm1h"
    "bDtmb250LXNpemU6LjY4cmVtO2ZvbnQtd2VpZ2h0OjgwMDtsZXR0ZXItc3BhY2luZzouMTRlbTtj"
    "b2xvcjp2YXIoLS10eDIpO29wYWNpdHk6LjU1O3doaXRlLXNwYWNlOm5vd3JhcH0KCi8qIDA3IMK3"
    "IEdyYWRpZW50IHRyYWNpbmcgKi8KLnRyYWNle2Rpc3BsYXk6YmxvY2s7d2lkdGg6MTAwJTtoZWln"
    "aHQ6MTBweDttYXJnaW46LTJweCAwIDhweDtvdmVyZmxvdzp2aXNpYmxlfQoudHJhY2UgLmJhc2V7"
    "c3Ryb2tlOnZhcigtLWxpbmUpO3N0cm9rZS13aWR0aDoxLjR9Ci50cmFjZSAubGVhZHtzdHJva2Ut"
    "d2lkdGg6MS44O3N0cm9rZS1saW5lY2FwOnJvdW5kfQouc2VjIGgye3Bvc2l0aW9uOnJlbGF0aXZl"
    "fQoKLyogMDEgKyAxMyDCtyBEb3QgbG9hZGVyICovCi5kb3Rncmlke2Rpc3BsYXk6Z3JpZDtncmlk"
    "LXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KDcsMWZyKTtnYXA6M3B4O3dpZHRoOjcwcHh9Ci5kb3Rn"
    "cmlkIGl7d2lkdGg6MTAwJTthc3BlY3QtcmF0aW86MTtib3JkZXItcmFkaXVzOjJweDtiYWNrZ3Jv"
    "dW5kOmNvbG9yLW1peChpbiBzcmdiLHZhcigtLXR4KSAxNCUsdHJhbnNwYXJlbnQpO3RyYW5zaXRp"
    "b246YmFja2dyb3VuZCAuMTJzLHRyYW5zZm9ybSAuMTJzfQouZG90Z3JpZCBpLmFjdHtiYWNrZ3Jv"
    "dW5kOnZhcigtLWFjYyk7dHJhbnNmb3JtOnNjYWxlKDEuMTIpfQouZG90c3tkaXNwbGF5OmlubGlu"
    "ZS1mbGV4O2dhcDo0cHg7YWxpZ24taXRlbXM6Y2VudGVyfQouZG90cyBpe3dpZHRoOjVweDtoZWln"
    "aHQ6NXB4O2JvcmRlci1yYWRpdXM6NTAlO2JhY2tncm91bmQ6dmFyKC0tYWNjKTthbmltYXRpb246"
    "Ym91bmNlIDFzIGluZmluaXRlIGVhc2UtaW4tb3V0fQouZG90cyBpOm50aC1jaGlsZCgyKXthbmlt"
    "YXRpb24tZGVsYXk6LjE1c30uZG90cyBpOm50aC1jaGlsZCgzKXthbmltYXRpb24tZGVsYXk6LjNz"
    "fQpAa2V5ZnJhbWVzIGJvdW5jZXswJSw4MCUsMTAwJXt0cmFuc2Zvcm06dHJhbnNsYXRlWSgwKTtv"
    "cGFjaXR5Oi4zNX00MCV7dHJhbnNmb3JtOnRyYW5zbGF0ZVkoLTVweCk7b3BhY2l0eToxfX0KCi8q"
    "IHNwbGFzaCAqLwojc3BsYXNoe3Bvc2l0aW9uOmZpeGVkO2luc2V0OjA7ei1pbmRleDozMDA7ZGlz"
    "cGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlm"
    "eS1jb250ZW50OmNlbnRlcjtnYXA6MTZweDsKICBiYWNrZ3JvdW5kOnZhcigtLWJnKTt0cmFuc2l0"
    "aW9uOm9wYWNpdHkgLjVzIGVhc2UsdmlzaWJpbGl0eSAuNXN9CiNzcGxhc2guZ29uZXtvcGFjaXR5"
    "OjA7dmlzaWJpbGl0eTpoaWRkZW59CiNzcGxhc2ggLm1hcmt7d2lkdGg6NThweDtoZWlnaHQ6NThw"
    "eDtib3JkZXItcmFkaXVzOjEycHg7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtiYWNr"
    "Z3JvdW5kOnZhcigtLXBpbGxncmFkKTsKICBjb2xvcjojMDQyNDFjO2ZvbnQtd2VpZ2h0OjgwMDtm"
    "b250LXNpemU6MS4xcmVtO2FuaW1hdGlvbjptYXJraW4gLjdzIGN1YmljLWJlemllciguMiwxLjQs"
    "LjQsMSkgYm90aH0KI3NwbGFzaCBoNHttYXJnaW46MDtmb250LWZhbWlseTp2YXIoLS1maGVhZCk7"
    "Zm9udC1zaXplOjFyZW07Zm9udC13ZWlnaHQ6ODAwO2xldHRlci1zcGFjaW5nOi0uMDNlbTsKICBh"
    "bmltYXRpb246ZmFkZXVwIC42cyAuMTVzIGJvdGh9CiNzcGxhc2ggc21hbGx7Y29sb3I6dmFyKC0t"
    "dHgyKTtmb250LXNpemU6LjY1cmVtO2xldHRlci1zcGFjaW5nOi4yMmVtO3RleHQtdHJhbnNmb3Jt"
    "OnVwcGVyY2FzZTthbmltYXRpb246ZmFkZXVwIC42cyAuMjVzIGJvdGh9CkBrZXlmcmFtZXMgbWFy"
    "a2lue2Zyb217b3BhY2l0eTowO3RyYW5zZm9ybTpzY2FsZSguNikgcm90YXRlKC0xMmRlZyl9dG97"
    "b3BhY2l0eToxO3RyYW5zZm9ybTpub25lfX0KQGtleWZyYW1lcyBmYWRldXB7ZnJvbXtvcGFjaXR5"
    "OjA7dHJhbnNmb3JtOnRyYW5zbGF0ZVkoOHB4KX10b3tvcGFjaXR5OjE7dHJhbnNmb3JtOm5vbmV9"
    "fQoKLyogMDggwrcgS2luZXRpYyBncmlkIGNhbnZhcyAqLwoja2d7cG9zaXRpb246Zml4ZWQ7aW5z"
    "ZXQ6MDt6LWluZGV4OjA7cG9pbnRlci1ldmVudHM6bm9uZTtvcGFjaXR5Oi40OH0KCi8qIHN0YWdn"
    "ZXJlZCByZXZlYWwgaXMgZHJpdmVuIGJ5IHRoZSBXZWIgQW5pbWF0aW9ucyBBUEkgKGNhbm5vdCBj"
    "bGFzaCB3aXRoIHRoZW1lIENTUykgKi8KCi8qIDAyICsgMjUgwrcgRG9jayB3aXRoIHNwcmluZyBw"
    "aWxsICovCm5hdiN0YWJzIC5kb2Nre3Bvc2l0aW9uOnJlbGF0aXZlfQpuYXYjdGFicyAuZHBpbGx7"
    "cG9zaXRpb246YWJzb2x1dGU7bGVmdDowO3RvcDowO3otaW5kZXg6MDtib3JkZXItcmFkaXVzOjEy"
    "cHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7CiAgYm94LXNoYWRvdzowIDZweCAxOHB4IGNv"
    "bG9yLW1peChpbiBzcmdiLHZhcigtLWFjYykgMzUlLHRyYW5zcGFyZW50KTtwb2ludGVyLWV2ZW50"
    "czpub25lfQpuYXYjdGFicyBidXR0b257cG9zaXRpb246cmVsYXRpdmU7ei1pbmRleDoxO3dpbGwt"
    "Y2hhbmdlOnRyYW5zZm9ybTsKICB0cmFuc2l0aW9uOmNvbG9yIC4yNnMgY3ViaWMtYmV6aWVyKC4y"
    "MiwxLC4zNiwxKSx0cmFuc2Zvcm0gLjE4cyBjdWJpYy1iZXppZXIoLjMsMS41LC41LDEpfQpuYXYj"
    "dGFicyBidXR0b24ub257YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjb2xvcjojMDQyNDFjfQpuYXYj"
    "dGFicyAuZHBpbGx7b3BhY2l0eTowfQpuYXYjdGFicyAuZHBpbGwucmVhZHl7b3BhY2l0eToxfQoK"
    "LyogMTQgwrcgVi10YWJzIHNsaWRpbmcgaW5kaWNhdG9yICovCi5zdWJuYXZ7cG9zaXRpb246cmVs"
    "YXRpdmV9Ci5zdWJuYXYgLnZwaWxse3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6MDt0b3A6MDt6LWlu"
    "ZGV4OjA7Ym9yZGVyLXJhZGl1czo5cHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7dHJhbnNp"
    "dGlvbjpub25lO3BvaW50ZXItZXZlbnRzOm5vbmV9Ci5zdWJuYXYgYnV0dG9ue3Bvc2l0aW9uOnJl"
    "bGF0aXZlO3otaW5kZXg6MX0KLnN1Ym5hdiBidXR0b24ub257YmFja2dyb3VuZDp0cmFuc3BhcmVu"
    "dDtjb2xvcjojMDQyNDFjO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVudH0KCi8qIDE1IMK3IEFuaW1h"
    "dGVkIHByb2ZpbGUgY2FyZCDigJQgdGlsdCArIGdsYXJlICovCi50aWx0e3RyYW5zZm9ybS1zdHls"
    "ZTpwcmVzZXJ2ZS0zZDt0cmFuc2l0aW9uOnRyYW5zZm9ybSAuMzVzIGN1YmljLWJlemllciguMjIs"
    "MSwuMzYsMSk7d2lsbC1jaGFuZ2U6dHJhbnNmb3JtfQoudGlsdCAuZ2xhcmV7cG9zaXRpb246YWJz"
    "b2x1dGU7aW5zZXQ6MDtib3JkZXItcmFkaXVzOmluaGVyaXQ7cG9pbnRlci1ldmVudHM6bm9uZTtv"
    "cGFjaXR5OjA7dHJhbnNpdGlvbjpvcGFjaXR5IC4zczsKICBiYWNrZ3JvdW5kOnJhZGlhbC1ncmFk"
    "aWVudCgyNDBweCBjaXJjbGUgYXQgdmFyKC0tbXgsNTAlKSB2YXIoLS1teSw1MCUpLHJnYmEoMjU1"
    "LDI1NSwyNTUsLjIyKSx0cmFuc3BhcmVudCA2MCUpfQoudGlsdDpob3ZlciAuZ2xhcmV7b3BhY2l0"
    "eToxfQouc2hpbmVjYXJke3Bvc2l0aW9uOnJlbGF0aXZlO292ZXJmbG93OmhpZGRlbn0KLnNoaW5l"
    "Y2FyZDpiZWZvcmV7Y29udGVudDoiIjtwb3NpdGlvbjphYnNvbHV0ZTt0b3A6LTYwJTtsZWZ0Oi0z"
    "MCU7d2lkdGg6NTUlO2hlaWdodDoyMjAlO3RyYW5zZm9ybTpyb3RhdGUoMThkZWcpOwogIGJhY2tn"
    "cm91bmQ6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRyYW5zcGFyZW50LHJnYmEoMjU1LDI1NSwyNTUs"
    "LjE2KSx0cmFuc3BhcmVudCk7YW5pbWF0aW9uOmNhcmRzaGluZSA1LjVzIGVhc2UtaW4tb3V0IGlu"
    "ZmluaXRlO3BvaW50ZXItZXZlbnRzOm5vbmV9CkBrZXlmcmFtZXMgY2FyZHNoaW5lezAlLDcwJXts"
    "ZWZ0Oi00MCV9MTAwJXtsZWZ0OjEyMCV9fQoKLyogMDUgwrcgSG9sZCBhY3Rpb24gYnV0dG9uICov"
    "Ci5ob2xke3Bvc2l0aW9uOnJlbGF0aXZlO292ZXJmbG93OmhpZGRlbn0KLmhvbGQgLmZpbGx7cG9z"
    "aXRpb246YWJzb2x1dGU7bGVmdDowO3RvcDowO2JvdHRvbTowO3dpZHRoOjA7YmFja2dyb3VuZDpy"
    "Z2JhKDI1NSwyNTUsMjU1LC4yNSk7dHJhbnNpdGlvbjp3aWR0aCAuMDVzIGxpbmVhcn0KLmhvbGQu"
    "ZG9uZXthbmltYXRpb246cG9wIC40cyBjdWJpYy1iZXppZXIoLjIsMS42LC40LDEpfQpAa2V5ZnJh"
    "bWVzIHBvcHs1MCV7dHJhbnNmb3JtOnNjYWxlKDEuMDQpfX0KCi8qIDExIMK3IE1vcnBoaW5nIHNl"
    "YXJjaCAqLwoubXNlYXJjaHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo4cHg7"
    "bWFyZ2luOjAgMCAxMHB4fQoubXNlYXJjaCAuZmllbGR3cmFwe2ZsZXg6MTttaW4td2lkdGg6MDtw"
    "b3NpdGlvbjpyZWxhdGl2ZTtoZWlnaHQ6NDBweH0KLm1zZWFyY2ggaW5wdXR7d2lkdGg6MTAwJTto"
    "ZWlnaHQ6NDBweDtwYWRkaW5nOjAgMTNweCAwIDM2cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2JvcmRl"
    "cjoxcHggc29saWQgdmFyKC0tbGluZSk7CiAgYmFja2dyb3VuZDp2YXIoLS1jYXJkKTtjb2xvcjp2"
    "YXIoLS10eCk7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi44cmVtO291dGxpbmU6"
    "bm9uZTsKICB0cmFuc2l0aW9uOmJveC1zaGFkb3cgLjNzIGN1YmljLWJlemllciguMjIsMSwuMzYs"
    "MSksYm9yZGVyLWNvbG9yIC4zc30KLm1zZWFyY2ggaW5wdXQ6Zm9jdXN7Ym9yZGVyLWNvbG9yOnZh"
    "cigtLWFjYyk7Ym94LXNoYWRvdzowIDAgMCA0cHggY29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tYWNj"
    "KSAxNiUsdHJhbnNwYXJlbnQpfQoubXNlYXJjaCBzdmd7cG9zaXRpb246YWJzb2x1dGU7bGVmdDox"
    "MnB4O3RvcDoxMXB4O3dpZHRoOjE3cHg7aGVpZ2h0OjE3cHg7Y29sb3I6dmFyKC0tdHgyKTt0cmFu"
    "c2l0aW9uOmNvbG9yIC4yNXN9Ci5tc2VhcmNoIGlucHV0OmZvY3VzK3N2ZywubXNlYXJjaCAuZmll"
    "bGR3cmFwOmZvY3VzLXdpdGhpbiBzdmd7Y29sb3I6dmFyKC0tYWNjKX0KLmNoaXByb3d7ZGlzcGxh"
    "eTpmbGV4O2dhcDo2cHg7b3ZlcmZsb3cteDphdXRvO3BhZGRpbmctYm90dG9tOjEwcHh9Ci5jaGlw"
    "cm93IGJ1dHRvbntmbGV4OjAgMCBhdXRvO2hlaWdodDozMHB4O3BhZGRpbmc6MCAxMnB4O2JvcmRl"
    "ci1yYWRpdXM6OTlweDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpOwogIGJhY2tncm91bmQ6"
    "dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4Mik7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9u"
    "dC1zaXplOi43cmVtO2ZvbnQtd2VpZ2h0OjcwMDtjdXJzb3I6cG9pbnRlcjt3aGl0ZS1zcGFjZTpu"
    "b3dyYXA7CiAgdHJhbnNpdGlvbjp0cmFuc2Zvcm0gLjJzIGN1YmljLWJlemllciguMywxLjUsLjUs"
    "MSksYmFja2dyb3VuZCAuMnMsY29sb3IgLjJzfQouY2hpcHJvdyBidXR0b24ub257YmFja2dyb3Vu"
    "ZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzA0MjQxYztib3JkZXItY29sb3I6dHJhbnNwYXJlbnQ7"
    "dHJhbnNmb3JtOnNjYWxlKDEuMDQpfQoKLyogc2tlbGV0b25zICgxMyDCtyBsb2FkZXIpICovCi5z"
    "a3twb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRkZW47YmFja2dyb3VuZDp2YXIoLS1jYXJk"
    "Mik7Ym9yZGVyLXJhZGl1czp2YXIoLS1ycyk7aGVpZ2h0Ojc0cHg7bWFyZ2luLWJvdHRvbTo5cHh9"
    "Ci5zazphZnRlcntjb250ZW50OiIiO3Bvc2l0aW9uOmFic29sdXRlO2luc2V0OjA7YmFja2dyb3Vu"
    "ZDpsaW5lYXItZ3JhZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsY29sb3ItbWl4KGluIHNyZ2IsdmFy"
    "KC0tdHgpIDEwJSx0cmFuc3BhcmVudCksdHJhbnNwYXJlbnQpOwogIHRyYW5zZm9ybTp0cmFuc2xh"
    "dGVYKC0xMDAlKTthbmltYXRpb246c2hpbSAxLjE1cyBpbmZpbml0ZX0KQGtleWZyYW1lcyBzaGlt"
    "e3Rve3RyYW5zZm9ybTp0cmFuc2xhdGVYKDEwMCUpfX0KCi8qIDE5IMK3IEZlYXR1cmUgc3RyaXAg"
    "Ki8KLmZlYXRze2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KDMsMWZy"
    "KTtnYXA6OHB4O21hcmdpbi1ib3R0b206MTJweH0KLmZlYXR7YmFja2dyb3VuZDp2YXIoLS1jYXJk"
    "KTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO3Bh"
    "ZGRpbmc6MTFweCA5cHg7dGV4dC1hbGlnbjpjZW50ZXI7CiAgdHJhbnNpdGlvbjp0cmFuc2Zvcm0g"
    "LjI1cyBjdWJpYy1iZXppZXIoLjIyLDEsLjM2LDEpfQouZmVhdDpob3Zlcnt0cmFuc2Zvcm06dHJh"
    "bnNsYXRlWSgtM3B4KX0KLmZlYXQgc3Zne3dpZHRoOjE3cHg7aGVpZ2h0OjE3cHg7Y29sb3I6dmFy"
    "KC0tYWNjKX0KLmZlYXQgYntkaXNwbGF5OmJsb2NrO2ZvbnQtc2l6ZTouNjhyZW07bWFyZ2luLXRv"
    "cDo1cHh9Ci5mZWF0IHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42cmVtO2NvbG9yOnZh"
    "cigtLXR4Mil9CgovKiAwMyDCtyBXb3JrcyB3aGVlbCDigJQgcm90YXRpbmcgZGVhbCBkaWFsICov"
    "Ci53aGVlbHtwb3NpdGlvbjpyZWxhdGl2ZTtoZWlnaHQ6MTE4cHg7bWFyZ2luOjJweCAwIDEycHg7"
    "b3ZlcmZsb3c6aGlkZGVuO2JvcmRlci1yYWRpdXM6dmFyKC0tcik7CiAgYm9yZGVyOjFweCBzb2xp"
    "ZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQpfQoud2hlZWwgLnJpbmd7cG9zaXRp"
    "b246YWJzb2x1dGU7cmlnaHQ6LTUycHg7dG9wOjUwJTt3aWR0aDoxNzZweDtoZWlnaHQ6MTc2cHg7"
    "bWFyZ2luLXRvcDotODhweDtib3JkZXItcmFkaXVzOjUwJTsKICBib3JkZXI6MXB4IGRhc2hlZCB2"
    "YXIoLS1saW5lKTthbmltYXRpb246c3BpbiAyNnMgbGluZWFyIGluZmluaXRlfQoud2hlZWwgLnJp"
    "bmcgYntwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjUwJTt0b3A6NTAlO3dpZHRoOjUwcHg7aGVpZ2h0"
    "OjI0cHg7bWFyZ2luOi0xMnB4IDAgMCAtMjVweDtib3JkZXItcmFkaXVzOjk5cHg7CiAgZGlzcGxh"
    "eTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtmb250LXNpemU6LjYycmVtO2ZvbnQtd2VpZ2h0Ojgw"
    "MDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2Nv"
    "bG9yOnZhcigtLXR4Mil9Ci53aGVlbCAuY2Fwe3Bvc2l0aW9uOmFic29sdXRlO2luc2V0OjA7ZGlz"
    "cGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO3Bh"
    "ZGRpbmc6MCAxNnB4OwogIGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KDkwZGVnLHZhcigtLWNh"
    "cmQpIDAgNTglLHRyYW5zcGFyZW50KX0KLndoZWVsIC5jYXA+KnttYXgtd2lkdGg6NjIlfQoud2hl"
    "ZWwgLmNhcCBlbXtmb250LXN0eWxlOm5vcm1hbDtmb250LXNpemU6LjZyZW07bGV0dGVyLXNwYWNp"
    "bmc6LjE4ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2NvbG9yOnZhcigtLWFjYyk7Zm9udC13"
    "ZWlnaHQ6ODAwfQoud2hlZWwgLmNhcCBie2ZvbnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNp"
    "emU6MS4wNXJlbTtmb250LXdlaWdodDo4MDB9CkBrZXlmcmFtZXMgc3Bpbnt0b3t0cmFuc2Zvcm06"
    "cm90YXRlKDM2MGRlZyl9fQoKLyogc3VjY2VzcyBidXJzdCAqLwojZnh7cG9zaXRpb246Zml4ZWQ7"
    "aW5zZXQ6MDt6LWluZGV4OjE1MDtkaXNwbGF5Om5vbmU7cGxhY2UtaXRlbXM6Y2VudGVyO3BvaW50"
    "ZXItZXZlbnRzOm5vbmV9CiNmeC5vbntkaXNwbGF5OmdyaWR9CiNmeCAucmluZ3t3aWR0aDo4MnB4"
    "O2hlaWdodDo4MnB4O2JvcmRlci1yYWRpdXM6NTAlO2JvcmRlcjoycHggc29saWQgdmFyKC0tb2sp"
    "O2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7CiAgY29sb3I6dmFyKC0tb2spO2FuaW1h"
    "dGlvbjpidXJzdCAuNzVzIGN1YmljLWJlemllciguMiwxLjMsLjQsMSl9CiNmeCAucmluZyBzdmd7"
    "d2lkdGg6MzRweDtoZWlnaHQ6MzRweH0KI2Z4IC53YXZle3Bvc2l0aW9uOmFic29sdXRlO3dpZHRo"
    "OjgycHg7aGVpZ2h0OjgycHg7Ym9yZGVyLXJhZGl1czo1MCU7Ym9yZGVyOjJweCBzb2xpZCB2YXIo"
    "LS1vayk7YW5pbWF0aW9uOndhdmUgLjlzIGVhc2Utb3V0IGZvcndhcmRzfQpAa2V5ZnJhbWVzIGJ1"
    "cnN0e2Zyb217dHJhbnNmb3JtOnNjYWxlKC40KTtvcGFjaXR5OjB9dG97dHJhbnNmb3JtOnNjYWxl"
    "KDEpO29wYWNpdHk6MX19CkBrZXlmcmFtZXMgd2F2ZXt0b3t0cmFuc2Zvcm06c2NhbGUoMy40KTtv"
    "cGFjaXR5OjB9fQoKLyogbnVtYmVyIHRpY2tlciAqLwoudGlja3tmb250LXZhcmlhbnQtbnVtZXJp"
    "Yzp0YWJ1bGFyLW51bXN9CgpAbWVkaWEocHJlZmVycy1yZWR1Y2VkLW1vdGlvbjpyZWR1Y2Upeyp7"
    "YW5pbWF0aW9uOm5vbmUhaW1wb3J0YW50O3RyYW5zaXRpb246bm9uZSFpbXBvcnRhbnR9fQpuYXYj"
    "dGFic3tsZWZ0OjA7cmlnaHQ6MDtib3R0b206MDt0cmFuc2Zvcm06bm9uZTttYXgtd2lkdGg6MTAw"
    "JX1uYXYjdGFicyAuZG9ja3tib3JkZXItcmFkaXVzOjA7Ym9yZGVyLWxlZnQ6MDtib3JkZXItcmln"
    "aHQ6MDtib3JkZXItYm90dG9tOjA7cGFkZGluZzo2cHggNnB4IGNhbGMoNnB4ICsgZW52KHNhZmUt"
    "YXJlYS1pbnNldC1ib3R0b20pKTtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYXJvdW5kfW5hdiN0YWJz"
    "IGJ1dHRvbntmbGV4OjF9Ci5waWxsLm9uIC5kb3R7YW5pbWF0aW9uOmJlYXQgMS42cyBpbmZpbml0"
    "ZX1Aa2V5ZnJhbWVzIGJlYXR7NTAle3RyYW5zZm9ybTpzY2FsZSgxLjUpO29wYWNpdHk6LjZ9fS5r"
    "cGl7cG9zaXRpb246cmVsYXRpdmU7b3ZlcmZsb3c6aGlkZGVufS5rcGk6YWZ0ZXJ7Y29udGVudDon"
    "Jztwb3NpdGlvbjphYnNvbHV0ZTtyaWdodDotMTBweDtib3R0b206LTE0cHg7d2lkdGg6NzBweDto"
    "ZWlnaHQ6NDBweDtib3JkZXItcmFkaXVzOjUwJTtiYWNrZ3JvdW5kOnJhZGlhbC1ncmFkaWVudChj"
    "aXJjbGUscmdiYSg1MiwyMTEsMTUzLC4yMiksdHJhbnNwYXJlbnQgNzAlKX0uaGVybyBoMXtmb250"
    "LXNpemU6MS42cmVtfQo8L3N0eWxlPgo8L2hlYWQ+Cjxib2R5PgoKPGNhbnZhcyBpZD0ia2ciPjwv"
    "Y2FudmFzPgo8ZGl2IGlkPSJzcGxhc2giPjxkaXYgY2xhc3M9Im1hcmsiIGlkPSJzcG1hcmsiPlpU"
    "PC9kaXY+PGRpdiBjbGFzcz0iZG90Z3JpZCIgaWQ9ImRnIj48L2Rpdj4KICA8aDQgaWQ9InNwbmFt"
    "ZSI+Wm9ybyBURyBTdG9yZTwvaDQ+PHNtYWxsIGlkPSJzcHRhZyI+V2FsbGV0LWZpcnN0IHN0b3Jl"
    "PC9zbWFsbD48L2Rpdj4KPHN2ZyB3aWR0aD0iMCIgaGVpZ2h0PSIwIiBzdHlsZT0icG9zaXRpb246"
    "YWJzb2x1dGUiPjxkZWZzPgogIDxmaWx0ZXIgaWQ9ImxnIiB4PSIwJSIgeT0iMCUiIHdpZHRoPSIx"
    "MDAlIiBoZWlnaHQ9IjEwMCUiIGNvbG9yLWludGVycG9sYXRpb24tZmlsdGVycz0ic1JHQiI+CiAg"
    "ICA8ZmVUdXJidWxlbmNlIHR5cGU9ImZyYWN0YWxOb2lzZSIgYmFzZUZyZXF1ZW5jeT0iMC4wNSAw"
    "LjA1IiBudW1PY3RhdmVzPSIxIiBzZWVkPSIxIiByZXN1bHQ9InQiLz4KICAgIDxmZUdhdXNzaWFu"
    "Qmx1ciBpbj0idCIgc3RkRGV2aWF0aW9uPSIyIiByZXN1bHQ9ImIiLz4KICAgIDxmZURpc3BsYWNl"
    "bWVudE1hcCBpbj0iU291cmNlR3JhcGhpYyIgaW4yPSJiIiBzY2FsZT0iNzAiIHhDaGFubmVsU2Vs"
    "ZWN0b3I9IlIiIHlDaGFubmVsU2VsZWN0b3I9IkIiLz4KICA8L2ZpbHRlcj48L2RlZnM+PC9zdmc+"
    "CjxkaXYgaWQ9ImZ4Ij48ZGl2IGNsYXNzPSJ3YXZlIj48L2Rpdj48ZGl2IGNsYXNzPSJyaW5nIj48"
    "c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3Ii"
    "IHN0cm9rZS13aWR0aD0iMi40IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9p"
    "bj0icm91bmQiPjxwYXRoIGQ9Ik0yMCA2IDkgMTdsLTUtNSIvPjwvc3ZnPjwvZGl2PjwvZGl2Pgo8"
    "ZGl2IGlkPSJzdyI+PGVtPlByZXZpZXc8L2VtPgogIDxidXR0b24gZGF0YS1zdz0iYXBwIiBjbGFz"
    "cz0ib24iPlN0b3JlPC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXN3PSJhZG1pbiI+QWRtaW48L2J1"
    "dHRvbj4KPC9kaXY+Cgo8ZGl2IGlkPSJ2aWV3LWFwcCI+CjxkaXYgaWQ9InNoZWxsIj4KICA8aGVh"
    "ZGVyIGlkPSJ0b3BiYXIiPgogICAgPGRpdiBjbGFzcz0ibG9nbyI+WlQ8L2Rpdj4KICAgIDxkaXYg"
    "Y2xhc3M9ImJyYW5kIj48YiBpZD0iYm5hbWUiPlpvcm8gVEcgU3RvcmU8L2I+PHNwYW4gaWQ9ImJ0"
    "YWciPldhbGxldC1maXJzdCBzdG9yZTwvc3Bhbj48L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9Imlj"
    "b25jaGlwIGhpZGRlbiIgaWQ9ImNhcnRjaGlwIiB0aXRsZT0iQ2FydCI+PHN2ZyB2aWV3Qm94PSIw"
    "IDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3VycmVudENvbG9yIiBzdHJva2Utd2lkdGg9"
    "IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGgg"
    "ZD0iTTYgMiAzIDZ2MTRhMiAyIDAgMCAwIDIgMmgxNGEyIDIgMCAwIDAgMi0yVjZsLTMtNHoiLz48"
    "cGF0aCBkPSJNMyA2aDE4Ii8+PHBhdGggZD0iTTE2IDEwYTQgNCAwIDAgMS04IDAiLz48L3N2Zz48"
    "aSBpZD0iY2FydG4iPjA8L2k+PC9idXR0b24+CiAgICA8YnV0dG9uIGNsYXNzPSJiYWxjaGlwIiBk"
    "YXRhLWdvPSJ3YWxsZXQiPjxzcGFuIGNsYXNzPSJwbHVzIj4rPC9zcGFuPuKCuTxzcGFuIGlkPSJi"
    "YWwiIGNsYXNzPSJ0aWNrIj4yNDguNTA8L3NwYW4+PC9idXR0b24+CiAgPC9oZWFkZXI+CiAgPG1h"
    "aW4gaWQ9InN0YWdlIj48L21haW4+CjwvZGl2Pgo8bmF2IGlkPSJ0YWJzIj48ZGl2IGNsYXNzPSJk"
    "b2NrIj48c3BhbiBjbGFzcz0iZHBpbGwiIGlkPSJkcGlsbCI+PC9zcGFuPgogIDxidXR0b24gZGF0"
    "YS10YWI9InNob3AiIGNsYXNzPSJvbiI+PHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5v"
    "bmUiIHN0cm9rZT0iY3VycmVudENvbG9yIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2Fw"
    "PSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTYgMiAzIDZ2MTRhMiAy"
    "IDAgMCAwIDIgMmgxNGEyIDIgMCAwIDAgMi0yVjZsLTMtNHoiLz48cGF0aCBkPSJNMyA2aDE4Ii8+"
    "PHBhdGggZD0iTTE2IDEwYTQgNCAwIDAgMS04IDAiLz48L3N2Zz48c3BhbiBkYXRhLW5hdj0ic2hv"
    "cCI+U2hvcDwvc3Bhbj48L2J1dHRvbj4KICA8YnV0dG9uIGRhdGEtdGFiPSJvcmRlcnMiPjxzdmcg"
    "dmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ry"
    "b2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91"
    "bmQiPjxwYXRoIGQ9Ik0yMSA4YTIgMiAwIDAgMC0xLTEuNzNsLTctNGEyIDIgMCAwIDAtMiAwbC03"
    "IDRBMiAyIDAgMCAwIDMgOHY4YTIgMiAwIDAgMCAxIDEuNzNsNyA0YTIgMiAwIDAgMCAyIDBsNy00"
    "QTIgMiAwIDAgMCAyMSAxNloiLz48cGF0aCBkPSJtMy4zIDcgOC43IDUgOC43LTUiLz48cGF0aCBk"
    "PSJNMTIgMjJWMTIiLz48L3N2Zz48c3BhbiBkYXRhLW5hdj0ib3JkZXJzIj5PcmRlcnM8L3NwYW4+"
    "PC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXRhYj0iZWFybiI+PHN2ZyB2aWV3Qm94PSIwIDAgMjQg"
    "MjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3VycmVudENvbG9yIiBzdHJva2Utd2lkdGg9IjIiIHN0"
    "cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTEz"
    "IDIgMyAxNGg5bC0xIDggMTAtMTJoLTl6Ii8+PC9zdmc+PHNwYW4gZGF0YS1uYXY9ImVhcm4iPkVh"
    "cm48L3NwYW4+PC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXRhYj0id2FsbGV0Ij48c3ZnIHZpZXdC"
    "b3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13"
    "aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48"
    "cGF0aCBkPSJNMTkgN1Y1YTIgMiAwIDAgMC0yLTJINWEyIDIgMCAwIDAgMCA0aDE1YTIgMiAwIDAg"
    "MSAyIDJ2OWEyIDIgMCAwIDEtMiAySDVhMiAyIDAgMCAxLTItMlY1Ii8+PHBhdGggZD0iTTE4IDEy"
    "aC4wMSIvPjwvc3ZnPjxzcGFuIGRhdGEtbmF2PSJ3YWxsZXQiPldhbGxldDwvc3Bhbj48L2J1dHRv"
    "bj4KICA8YnV0dG9uIGRhdGEtdGFiPSJ5b3UiPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxs"
    "PSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGlu"
    "ZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0xOSAyMXYtMmE0"
    "IDQgMCAwIDAtNC00SDlhNCA0IDAgMCAwLTQgNHYyIi8+PGNpcmNsZSBjeD0iMTIiIGN5PSI3IiBy"
    "PSI0Ii8+PC9zdmc+PHNwYW4gZGF0YS1uYXY9InlvdSI+WW91PC9zcGFuPjwvYnV0dG9uPgo8L2Rp"
    "dj48L25hdj4KPC9kaXY+Cgo8ZGl2IGlkPSJ2aWV3LWFkbWluIiBjbGFzcz0iaGlkZGVuIj48ZGl2"
    "IGlkPSJzaGVsbCI+CiAgPGhlYWRlciBpZD0idG9wYmFyIj4KICAgIDxkaXYgY2xhc3M9ImxvZ28i"
    "Pjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xv"
    "ciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9p"
    "bj0icm91bmQiPjxwYXRoIGQ9Ik0yMCAxM2MwIDUtMy41IDcuNS03LjY2IDguOTVhMSAxIDAgMCAx"
    "LS42Ny0uMDFDNy41IDIwLjUgNCAxOCA0IDEzVjZhMSAxIDAgMCAxIDEtMWMyIDAgNC41LTEuMiA2"
    "LjI0LTIuNzJhMS4xNyAxLjE3IDAgMCAxIDEuNTIgMEMxNC41MSAzLjgxIDE3IDUgMTkgNWExIDEg"
    "MCAwIDEgMSAxeiIvPjwvc3ZnPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnJhbmQiPjxiPkFkbWlu"
    "IENvbnNvbGU8L2I+PHNwYW4gaWQ9ImFibmFtZSI+Wm9ybyBURyBTdG9yZTwvc3Bhbj48L2Rpdj4K"
    "ICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFzcz0iZG90Ij48L2k+TGl2ZTwvc3Bhbj4K"
    "ICA8L2hlYWRlcj4KICA8ZGl2IGNsYXNzPSJzdWJuYXYiIGlkPSJhc3ViIj48c3BhbiBjbGFzcz0i"
    "dnBpbGwiIGlkPSJ2cGlsbCI+PC9zcGFuPgogICAgPGJ1dHRvbiBkYXRhLWE9ImRhc2giIGNsYXNz"
    "PSJvbiI+RGFzaGJvYXJkPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0iY2hhbm5lbHMiPkNo"
    "YW5uZWxzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0ibG9ncyI+TG9nczwvYnV0dG9uPgog"
    "ICAgPGJ1dHRvbiBkYXRhLWE9InBheSI+QXV0by1QYXk8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0"
    "YS1hPSJjb250ZW50Ij5Db250ZW50PC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0iZmVhdHVy"
    "ZXMiPkZlYXR1cmVzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0ibWFya2V0Ij5NYXJrZXRp"
    "bmc8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJhbmFseXRpY3MiPkFuYWx5dGljczwvYnV0"
    "dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9InNlY3VyaXR5Ij5TZWN1cml0eTwvYnV0dG9uPgogICAg"
    "PGJ1dHRvbiBkYXRhLWE9InN0b2NrIj5TdG9jazwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9"
    "Im9yZGVycyI+T3JkZXJzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0idXNlcnMiPlVzZXJz"
    "PC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0ic2V0dGluZ3MiPlNldHRpbmdzPC9idXR0b24+"
    "CiAgPC9kaXY+CiAgPG1haW4gaWQ9ImFzdGFnZSI+PC9tYWluPgo8L2Rpdj48L2Rpdj4KCjxkaXYg"
    "aWQ9InNoZWV0Ij48ZGl2IGNsYXNzPSJiZyIgZGF0YS1jbG9zZT48L2Rpdj48ZGl2IGNsYXNzPSJw"
    "YW5lbCIgaWQ9InBhbmVsIj48L2Rpdj48L2Rpdj4KPGRpdiBjbGFzcz0idG9hc3QiIGlkPSJ0b2Fz"
    "dCI+PC9kaXY+Cgo8c2NyaXB0PgovKiDilIDilIAgbGl2ZS1tb2RlIHN0YXRlLCBkZWNsYXJlZCBm"
    "aXJzdCBzbyBub3RoaW5nIGNhbiByZWFkIGl0IHRvbyBlYXJseSDilIDilIAgKi8KdmFyIExJVkU9"
    "ZmFsc2UsTE9DS0VEPWZhbHNlLE1FPXthZG1pbjpmYWxzZSxvd25lcjpmYWxzZSxwZXJtczp7fX07"
    "CnZhciBURz13aW5kb3cuVGVsZWdyYW0mJndpbmRvdy5UZWxlZ3JhbS5XZWJBcHB8fG51bGw7CmZ1"
    "bmN0aW9uIHJlYWRJbml0RGF0YSgpewogIHRyeXtpZihURyYmVEcuaW5pdERhdGEmJlRHLmluaXRE"
    "YXRhLmxlbmd0aClyZXR1cm4gVEcuaW5pdERhdGE7fWNhdGNoKGUpe30KICAvKiBUZWxlZ3JhbSBh"
    "bHNvIHB1dHMgaXQgaW4gdGhlIFVSTCwgc28gdGhlIGFwcCBzdGlsbCB3b3JrcyBpZiB0aGUgU0RL"
    "IGlzIGJsb2NrZWQgKi8KICB0cnl7CiAgICBjb25zdCBtPS9bIyY/XXRnV2ViQXBwRGF0YT0oW14m"
    "XSopLy5leGVjKGxvY2F0aW9uLmhhc2h8fCIiKTsKICAgIGlmKG0mJm1bMV0pcmV0dXJuIGRlY29k"
    "ZVVSSUNvbXBvbmVudChtWzFdKTsKICB9Y2F0Y2goZSl7fQogIHRyeXsKICAgIGNvbnN0IHE9bmV3"
    "IFVSTFNlYXJjaFBhcmFtcyhsb2NhdGlvbi5zZWFyY2gpOwogICAgY29uc3Qgdj1xLmdldCgidGdX"
    "ZWJBcHBEYXRhIik7CiAgICBpZih2KXJldHVybiB2OwogIH1jYXRjaChlKXt9CiAgdHJ5ewogICAg"
    "Y29uc3Qgc2F2ZWQ9c2Vzc2lvblN0b3JhZ2UuZ2V0SXRlbSgidGdJbml0Iik7CiAgICBpZihzYXZl"
    "ZClyZXR1cm4gc2F2ZWQ7CiAgfWNhdGNoKGUpe30KICByZXR1cm4gIiI7Cn0KZnVuY3Rpb24gaW5p"
    "dGlhbHMobmFtZSl7CiAgY29uc3Qgdz0obmFtZXx8IiIpLnRyaW0oKS5zcGxpdCgvXHMrLykuZmls"
    "dGVyKEJvb2xlYW4pOwogIGlmKCF3Lmxlbmd0aClyZXR1cm4gIlNUIjsKICByZXR1cm4gKCh3WzBd"
    "WzBdfHwiIikrKHcubGVuZ3RoPjE/KHdbMV1bMF18fCIiKTood1swXVsxXXx8IiIpKSkudG9VcHBl"
    "ckNhc2UoKTsKfQpmdW5jdGlvbiBwYWludEJyYW5kKG5hbWUsdGFnKXsKICBpZihuYW1lKXsKICAg"
    "IGNvbnN0IG49ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNwbmFtZSIpOyBpZihuKW4udGV4dENv"
    "bnRlbnQ9bmFtZTsKICAgIGNvbnN0IG09ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNwbWFyayIp"
    "OyBpZihtKW0udGV4dENvbnRlbnQ9aW5pdGlhbHMobmFtZSk7CiAgICBjb25zdCBiPWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJibmFtZSIpOyBpZihiKWIudGV4dENvbnRlbnQ9bmFtZTsKICAgIGNv"
    "bnN0IGFiPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJhYm5hbWUiKTsgaWYoYWIpYWIudGV4dENv"
    "bnRlbnQ9bmFtZTsKICAgIGRvY3VtZW50LnRpdGxlPW5hbWU7CiAgfQogIGlmKHRhZyl7CiAgICBj"
    "b25zdCB0PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzcHRhZyIpOyBpZih0KXQudGV4dENvbnRl"
    "bnQ9dGFnOwogICAgY29uc3QgYnQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJ0YWciKTsgaWYo"
    "YnQpYnQudGV4dENvbnRlbnQ9dGFnOwogIH0KfQp0cnl7CiAgY29uc3QgY249bG9jYWxTdG9yYWdl"
    "LmdldEl0ZW0oInN0b3JlTmFtZSIpLCBjdD1sb2NhbFN0b3JhZ2UuZ2V0SXRlbSgic3RvcmVUYWci"
    "KTsKICBpZihjbnx8Y3QpcGFpbnRCcmFuZChjbixjdCk7Cn1jYXRjaChlKXt9CnZhciBJTklUPXJl"
    "YWRJbml0RGF0YSgpOwp0cnl7aWYoSU5JVClzZXNzaW9uU3RvcmFnZS5zZXRJdGVtKCJ0Z0luaXQi"
    "LElOSVQpO31jYXRjaChlKXt9CmZ1bmN0aW9uIHJlYWRBcHBLZXkoKXsKICB0cnl7CiAgICBjb25z"
    "dCBxPW5ldyBVUkxTZWFyY2hQYXJhbXMobG9jYXRpb24uc2VhcmNoKTsKICAgIGlmKHEuZ2V0KCJr"
    "IikpcmV0dXJuIHEuZ2V0KCJrIik7CiAgICBjb25zdCBtPS9bIyY/XWs9KFteJl0qKS8uZXhlYyhs"
    "b2NhdGlvbi5oYXNofHwiIik7CiAgICBpZihtJiZtWzFdKXJldHVybiBkZWNvZGVVUklDb21wb25l"
    "bnQobVsxXSk7CiAgICByZXR1cm4gc2Vzc2lvblN0b3JhZ2UuZ2V0SXRlbSgiYXBwS2V5Iil8fCIi"
    "OwogIH1jYXRjaChlKXtyZXR1cm4gIiI7fQp9CnZhciBBUFBLRVk9cmVhZEFwcEtleSgpOwp0cnl7"
    "aWYoQVBQS0VZKXNlc3Npb25TdG9yYWdlLnNldEl0ZW0oImFwcEtleSIsQVBQS0VZKTt9Y2F0Y2go"
    "ZSl7fQovKiBkZW1vIGRhdGEgb25seSDigJQgbm8gdG9rZW5zLCBubyBVUEkgSUQsIG5vIEdtYWls"
    "IGNyZWRlbnRpYWxzICovCmxldCBDRkc9e25hbWU6Ilpvcm8gVEcgU3RvcmUiLHRhZzoiV2FsbGV0"
    "LWZpcnN0IHN0b3JlIixjdXI6IuKCuSJ9OwovKiDilIDilIAgRWRpdGFibGUgY29weSDigJQgZXZl"
    "cnkgc3RyaW5nIGJlbG93IGlzIGVkaXRhYmxlIGluIEFkbWluIOKWuCBDb250ZW50IOKUgOKUgCAq"
    "Lwpjb25zdCBDTVM9ewogc3RvcmVOYW1lOiJab3JvIFRHIFN0b3JlIiwgc3RvcmVUYWc6IldhbGxl"
    "dC1maXJzdCBzdG9yZSIsCiBlbXB0eVRpdGxlOiJSZXN0b2NraW5nIiwgZW1wdHlCb2R5OiJFdmVy"
    "eXRoaW5nIGp1c3Qgc29sZCBvdXQuIEZyZXNoIGFjY291bnRzIGxhbmQgaGVyZSBzaG9ydGx5IOKA"
    "lCB0dXJuIG9uIGFuIGFsZXJ0IGFuZCB5b3Ugd2lsbCBoZWFyIGZpcnN0LiIsCiB0aWNrMToiPGI+"
    "SW5zdGFudCBkZWxpdmVyeTwvYj4gb24gZXZlcnkgY291bnRyeSIsIHRpY2syOiJUb3AtdXBzIDxi"
    "PmNvbmZpcm1lZCBhdXRvbWF0aWNhbGx5PC9iPiIsCiB0aWNrMzoiRmFpbGVkIGxvZ2luPyA8Yj5B"
    "dXRvIHJlZnVuZDwvYj4iLCB0aWNrNDoiRGFpbHkgY2hlY2staW4gPGI+4oK5MC40MDwvYj4iLAog"
    "aGVyb0tpY2s6IkF1dG8tdmVyaWZpZWQgcGF5bWVudHMiLCBoZXJvSDE6IlBheSBvbmNlLjxicj5D"
    "cmVkaXRlZCBpbnN0YW50bHkuIiwKIGhlcm9TdWI6Ikluc3RhbnQgZGVsaXZlcnksIE9UUCBpbi1h"
    "cHAsIHdhbGxldCB0b3AtdXBzIHZlcmlmaWVkIGF1dG9tYXRpY2FsbHkuIHtufSBjb3VudHJpZXMg"
    "bGl2ZS4iLAogaGVyb0J0bjE6IkJyb3dzZSBTdG9jayIsIGhlcm9CdG4yOiJBZGQgRnVuZHMiLAog"
    "ZmVhdDE6In44cyIsIGZlYXQxczoiZGVsaXZlcnkiLCBmZWF0MjoiQXV0byIsIGZlYXQyczoicmVm"
    "dW5kcyIsIGZlYXQzOiJVUEkiLCBmZWF0M3M6ImF1dG8tdmVyaWZ5IiwKIGZsYXNoUGlsbDoiRmxh"
    "c2gg4oiSMjAlIiwgZmxhc2hUZXh0OiJFbmRzIGluIiwgZmxhc2hCdG46IlZpZXciLAogd2hlZWxL"
    "aWNrOiJSb3RhdGluZyBkZWFscyIsIHdoZWVsVGl0bGU6IlVwIHRvIDI4JSBvZmYiLCB3aGVlbFdv"
    "cmRzOiJJTlNUQU5ULCBBVVRPLVZFUklGWSwgUkVGVU5EUywgMjTDlzciLAogc2hvcEhlYWQ6Iklu"
    "IFN0b2NrIiwgc2hvcFNvcnQ6IlNvcnQiLCBzZWFyY2hQaDoiU2VhcmNoIGNvdW50cnkgb3IgY29k"
    "ZeKApiIsIGFsZXJ0c0J0bjoiQWxlcnRzIiwKIHdhbEtpY2s6IkJhbGFuY2UiLCB3YWxTdWI6IlBh"
    "eSBieSBVUEkg4oCUIHRhcCBWZXJpZnkgYWZ0ZXIgcGF5aW5nIGFuZCB5b3VyIHdhbGxldCBpcyBj"
    "cmVkaXRlZCBpbiBzZWNvbmRzLiIsCiB3YWxBZGQ6IkFkZCBGdW5kcyIsIHBheVRpdGxlOiJQYXlt"
    "ZW50IERldGFpbHMiLCBwYXlTdWI6IkFkbWluIHNldHMgdGhlIFVQSSBoYW5kbGUg4oCUIG5vdGhp"
    "bmcgaXMgaGFyZC1jb2RlZCBpbiB0aGUgYXBwLiIsCiBwYXlQcmVmaXg6IlBBWSIsIHBheVdpbmRv"
    "dzoiNSIsCiBzaGVldFRpdGxlOiJBZGQiLCBzaGVldFN1YjoiUGF5IHRoZSBleGFjdCBhbW91bnQs"
    "IHRoZW4gdGFwIFZlcmlmeSBQYXltZW50LiBUaGUgcmVxdWVzdCBleHBpcmVzIGluIHttfSBtaW51"
    "dGVzLiIsCiBwYXlCdG46IlBheSBOb3ciLCB2ZXJpZnlCdG46IlZlcmlmeSBQYXltZW50IiwgdXRy"
    "QnRuOiJOb3QgZGV0ZWN0ZWQ/IFN1Ym1pdCBVVFIiLAogc3RlcDE6IlBheW1lbnQgcmVxdWVzdCBj"
    "cmVhdGVkIiwgc3RlcDI6IlBheSB0aGUgZXhhY3QgYW1vdW50IGluIGFueSBVUEkgYXBwIiwKIHN0"
    "ZXAzOiJUYXAgVmVyaWZ5IOKAlCB3ZSBjb25maXJtIHlvdXIgcGF5bWVudCIsIHN0ZXA0OiJXYWxs"
    "ZXQgY3JlZGl0ZWQiLAogbXNnQ2hlY2tpbmc6IkNoZWNraW5nIHlvdXIgcGF5bWVudOKApiIsIG1z"
    "Z09rOiJWZXJpZmllZCDigJQgd2FsbGV0IGNyZWRpdGVkIiwKIG1zZ05vbmU6Ik5vIHBheW1lbnQg"
    "Zm91bmQgeWV0LiBQYXkgZmlyc3QsIHRoZW4gdGFwIFZlcmlmeS4iLCBtc2dFeHBpcmVkOiJSZXF1"
    "ZXN0IGV4cGlyZWQg4oCUIGNyZWF0ZSBhIG5ldyBvbmUuIiwKIG5hdlNob3A6IlNob3AiLCBuYXZP"
    "cmRlcnM6Ik9yZGVycyIsIG5hdkVhcm46IkVhcm4iLCBuYXZXYWxsZXQ6IldhbGxldCIsIG5hdllv"
    "dToiWW91Igp9Owpjb25zdCBDTVMwPU9iamVjdC5hc3NpZ24oe30sQ01TKTsKY29uc3QgQ01TTUVU"
    "QT1bCiBbIlN0b3JlIGhlYWRlciIsW1sic3RvcmVOYW1lIiwiU3RvcmUgbmFtZSJdLFsic3RvcmVU"
    "YWciLCJUYWdsaW5lIl1dXSwKIFsiU2Nyb2xsaW5nIHRpY2tlciIsW1sidGljazEiLCJUaWNrZXIg"
    "MSJdLFsidGljazIiLCJUaWNrZXIgMiJdLFsidGljazMiLCJUaWNrZXIgMyJdLFsidGljazQiLCJU"
    "aWNrZXIgNCJdXV0sCiBbIkhlcm8gc2VjdGlvbiIsW1siaGVyb0tpY2siLCJTbWFsbCBsYWJlbCBh"
    "Ym92ZSB0aXRsZSJdLFsiaGVyb0gxIiwiQmlnIHRpdGxlIl0sWyJoZXJvU3ViIiwiUGFyYWdyYXBo"
    "IHVuZGVyIHRoZSB0aXRsZSJdLFsiaGVyb0J0bjEiLCJMZWZ0IGJ1dHRvbiJdLFsiaGVyb0J0bjIi"
    "LCJSaWdodCBidXR0b24iXV1dLAogWyJGZWF0dXJlIGJhZGdlcyIsW1siZmVhdDEiLCJCYWRnZSAx"
    "IHZhbHVlIl0sWyJmZWF0MXMiLCJCYWRnZSAxIGNhcHRpb24iXSxbImZlYXQyIiwiQmFkZ2UgMiB2"
    "YWx1ZSJdLFsiZmVhdDJzIiwiQmFkZ2UgMiBjYXB0aW9uIl0sWyJmZWF0MyIsIkJhZGdlIDMgdmFs"
    "dWUiXSxbImZlYXQzcyIsIkJhZGdlIDMgY2FwdGlvbiJdXV0sCiBbIkZsYXNoIHNhbGUgc3RyaXAi"
    "LFtbImZsYXNoUGlsbCIsIlBpbGwgdGV4dCJdLFsiZmxhc2hUZXh0IiwiQ291bnRkb3duIGxhYmVs"
    "Il0sWyJmbGFzaEJ0biIsIkJ1dHRvbiJdXV0sCiBbIlJvdGF0aW5nIGRlYWxzIGNhcmQiLFtbIndo"
    "ZWVsS2ljayIsIlNtYWxsIGxhYmVsIl0sWyJ3aGVlbFRpdGxlIiwiSGVhZGxpbmUiXSxbIndoZWVs"
    "V29yZHMiLCJNYXJxdWVlIHdvcmRzIChjb21tYSBzZXBhcmF0ZWQpIl1dXSwKIFsiU2hvcCBsaXN0"
    "IixbWyJzaG9wSGVhZCIsIlNlY3Rpb24gaGVhZGluZyJdLFsic2hvcFNvcnQiLCJTb3J0IGxpbmsi"
    "XSxbInNlYXJjaFBoIiwiU2VhcmNoIHBsYWNlaG9sZGVyIl0sWyJhbGVydHNCdG4iLCJBbGVydHMg"
    "YnV0dG9uIl1dXSwKIFsiV2FsbGV0IHNjcmVlbiIsW1sid2FsS2ljayIsIkxhYmVsIGFib3ZlIGJh"
    "bGFuY2UiXSxbIndhbFN1YiIsIkJhbGFuY2UgZGVzY3JpcHRpb24iXSxbIndhbEFkZCIsIkFkZCBm"
    "dW5kcyBidXR0b24iXSxbInBheVRpdGxlIiwiUGF5bWVudCBjYXJkIHRpdGxlIl0sWyJwYXlTdWIi"
    "LCJQYXltZW50IGNhcmQgZGVzY3JpcHRpb24iXV1dLAogWyJQYXltZW50IHNoZWV0IixbWyJzaGVl"
    "dFRpdGxlIiwiU2hlZXQgdGl0bGUgcHJlZml4Il0sWyJzaGVldFN1YiIsIlNoZWV0IGRlc2NyaXB0"
    "aW9uICh7bX0gPSBtaW51dGVzKSJdLFsicGF5QnRuIiwiUGF5IGJ1dHRvbiJdLFsidmVyaWZ5QnRu"
    "IiwiVmVyaWZ5IGJ1dHRvbiJdLFsidXRyQnRuIiwiTWFudWFsIGZhbGxiYWNrIGJ1dHRvbiJdLFsi"
    "cGF5UHJlZml4IiwiUmVmZXJlbmNlIHByZWZpeCJdLFsicGF5V2luZG93IiwiUGF5bWVudCB3aW5k"
    "b3cgKG1pbnV0ZXMpIl1dXSwKIFsiUGF5bWVudCBzdGVwcyIsW1sic3RlcDEiLCJTdGVwIDEiXSxb"
    "InN0ZXAyIiwiU3RlcCAyIl0sWyJzdGVwMyIsIlN0ZXAgMyJdLFsic3RlcDQiLCJTdGVwIDQiXV1d"
    "LAogWyJQYXltZW50IG1lc3NhZ2VzIixbWyJtc2dDaGVja2luZyIsIldoaWxlIGNoZWNraW5nIl0s"
    "WyJtc2dPayIsIlZlcmlmaWVkIl0sWyJtc2dOb25lIiwiTm90aGluZyBmb3VuZCJdLFsibXNnRXhw"
    "aXJlZCIsIkV4cGlyZWQiXV1dLAogWyJCb3R0b20gbmF2aWdhdGlvbiIsW1sibmF2U2hvcCIsIlRh"
    "YiAxIl0sWyJuYXZPcmRlcnMiLCJUYWIgMiJdLFsibmF2RWFybiIsIlRhYiAzIl0sWyJuYXZXYWxs"
    "ZXQiLCJUYWIgNCJdLFsibmF2WW91IiwiVGFiIDUiXV1dCl07Ci8qIOKVkOKVkCBGRUFUVVJFIEZM"
    "QUdTIOKAlCBldmVyeSBmZWF0dXJlIGJlbG93IGlzIG9mZi1hYmxlOyB3aGVuIG9mZiBub3RoaW5n"
    "IHJlbmRlcnMg4pWQ4pWQICovCmNvbnN0IEZHUk9VUFM9WwogWyJQYXltZW50cyAmIHdhbGxldCIs"
    "WwogIFsiY3J5cHRvIiwiQ3J5cHRvIC8gVVNEVCB0b3AtdXAiLCJBY2NlcHQgVVNEVC1UUkMyMCwg"
    "QlRDIGV0Yy4gd2l0aCBhdXRvLWNvbmZpcm0gZnJvbSB0aGUgZXhwbG9yZXIuIl0sCiAgWyJ1cGlS"
    "b3RhdGUiLCJNdWx0aXBsZSBVUEkgSURzICsgcm90YXRpb24iLCJSb3RhdGUgaGFuZGxlcyBwZXIg"
    "cGF5bWVudCBvciBhdXRvLXN3aXRjaCB3aGVuIG9uZSBpcyBsaW1pdGVkLiJdLAogIFsiYXV0b1Jl"
    "ZnVuZCIsIkF1dG8tcmVmdW5kIHRvIHdhbGxldCIsIkRlYWQgYWNjb3VudCBpbnNpZGUgdGhlIHdh"
    "cnJhbnR5IHdpbmRvdyBpcyByZWZ1bmRlZCB3aXRob3V0IHN1cHBvcnQuIl0sCiAgWyJzcGxpdFBh"
    "eSIsIlNwbGl0IHBheW1lbnQiLCJQYXkgcGFydCBmcm9tIGJhbGFuY2UgYW5kIHRoZSByZXN0IGJ5"
    "IFVQSSBpbiBvbmUgZmxvdy4iXSwKICBbImxlZGdlciIsIldhbGxldCBsZWRnZXIgc3RhdGVtZW50"
    "IiwiRnVsbCBjcmVkaXQvZGViaXQgaGlzdG9yeSB3aXRoIHJ1bm5pbmcgYmFsYW5jZSBhbmQgQ1NW"
    "IGV4cG9ydC4iXSwKICBbImRlcExpbWl0cyIsIk1pbiAvIG1heCBkZXBvc2l0IHJ1bGVzIiwiR2xv"
    "YmFsIG9yIHBlci11c2VyIGNhcHMgb24gYSBzaW5nbGUgdG9wLXVwLiJdLAogIFsiZGVwQm9udXMi"
    "LCJEZXBvc2l0IGJvbnVzIHRpZXJzIiwiQWRkIOKCuTUwMCBnZXQgNSUgZXh0cmEg4oCUIGNvbmZp"
    "Z3VyYWJsZSBzbGFicywgYXV0by1hcHBsaWVkLiJdLAogIFsid3JvbmdBbXQiLCJXcm9uZy1hbW91"
    "bnQgaGFuZGxpbmciLCJDcmVkaXQgd2hhdGV2ZXIgd2FzIGFjdHVhbGx5IHBhaWQgYW5kIGZsYWcg"
    "dGhlIG1pc21hdGNoLiJdXV0sCiBbIkNhdGFsb2cgJiBzZWxsaW5nIixbCiAgWyJjYXJ0IiwiU2hv"
    "cHBpbmcgY2FydCAvIGJ1bGsgYnV5IiwiTWl4IGNvdW50cmllcyBpbiBvbmUgY2hlY2tvdXQsIG9u"
    "ZSBjb25maXJtYXRpb24sIG9uZSBkZWJpdC4iXSwKICBbImJ1bGtEaXNjIiwiQnVsayBwdXJjaGFz"
    "ZSBkaXNjb3VudCIsIkF1dG9tYXRpYyBwcmljZSBicmVhayBhdCA1IC8gMTAgLyAyNSB1bml0cy4i"
    "XSwKICBbIndpc2hsaXN0IiwiV2lzaGxpc3QgLyBmYXZvdXJpdGVzIiwiU2F2ZSBjb3VudHJpZXMg"
    "YW5kIHJlYnV5IGluIG9uZSB0YXAuIl0sCiAgWyJkeW5QcmljZSIsIkR5bmFtaWMgcHJpY2luZyIs"
    "IlByaWNlIG1vdmVzIHdpdGggc3RvY2sgbGV2ZWwsIGFnZSB0aWVyIG9yIHRpbWUgb2YgZGF5LiJd"
    "LAogIFsiZmxhc2hTYWxlIiwiRmxhc2ggc2FsZSIsIlRpbWVkIGRpc2NvdW50IHdpdGggYSBsaXZl"
    "IGNvdW50ZG93biBvbiB0aGUgc3RvcmVmcm9udC4iXSwKICBbImNvdXBvbnMiLCJDb3Vwb24gY29k"
    "ZXMiLCJQZXJjZW50YWdlIG9mZiBhdCBjaGVja291dCB3aXRoIGxpbWl0cyBhbmQgZXhwaXJ5LiJd"
    "LAogIFsidmFyaWFudHMiLCJBY2NvdW50IHZhcmlhbnRzIiwiU3BhbS1ibG9jaywgMkZBLCB1c2Vy"
    "bmFtZSwgbWFpbCDigJQgZmlsdGVyYWJsZSBhbmQgcHJpY2VkIGFwYXJ0LiJdLAogIFsiaGVhbHRo"
    "Q2hlY2siLCJTdG9jayBoZWFsdGggY2hlY2siLCJTZXNzaW9uIGlzIHZhbGlkYXRlZCBsaXZlIGJl"
    "Zm9yZSBpdCBpcyBzb2xkLiJdXV0sCiBbIk9yZGVycyAmIGFmdGVyLXNhbGUiLFsKICBbInRpbWVs"
    "aW5lIiwiT3JkZXIgc3RhdHVzIHRpbWVsaW5lIiwiUGVuZGluZywgZGVsaXZlcmVkLCBPVFAgcmVj"
    "ZWl2ZWQsIGNsb3NlZCDigJQgdmlzaWJsZSB0byBib3RoIHNpZGVzLiJdLAogIFsid2FycmFudHki"
    "LCJSZXBsYWNlbWVudCAvIHdhcnJhbnR5IiwiUmVwb3J0IGEgcHJvYmxlbSBpbnNpZGUgWCBob3Vy"
    "cyBmb3IgYW4gYXV0by1yZXBsYWNlbWVudC4iXSwKICBbInJlZG93bmxvYWQiLCJSZS1kb3dubG9h"
    "ZCBkZXRhaWxzIiwiRmV0Y2ggYSBwYXN0IG9yZGVyJ3MgZGV0YWlscyBhZ2FpbiwgcmF0ZS1saW1p"
    "dGVkLiJdLAogIFsiZGlzcHV0ZSIsIkRpc3B1dGUgZmxvdyIsIlJlYXNvbiBwaWNrZXIgcGx1cyBl"
    "dmlkZW5jZSBpbnN0ZWFkIG9mIGZyZWUtdGV4dCB0aWNrZXRzLiJdLAogIFsic2Vzc2lvbkZpbGUi"
    "LCJTZXNzaW9uIGZpbGUgZGVsaXZlcnkiLCJTZW5kIC5zZXNzaW9uIC8gSlNPTiBhcyBhIGRvY3Vt"
    "ZW50IHdpdGggYSBzZWxmLWRlc3RydWN0IHRpbWVyLiJdLAogIFsicmVjZWlwdHMiLCJPcmRlciBy"
    "ZWNlaXB0cyIsIkF1dG8tZ2VuZXJhdGVkIGludm9pY2UgcGVyIHB1cmNoYXNlLiJdXV0sCiBbIlRy"
    "dXN0ICYgc29jaWFsIHByb29mIixbCiAgWyJyZXZpZXdzIiwiUmV2aWV3cyAmIHN0YXIgcmF0aW5n"
    "cyIsIkJ1eWVyIHJhdGVzIGFmdGVyIGRlbGl2ZXJ5OyBhdmVyYWdlcyBzaG93IG9uIGV2ZXJ5IGNv"
    "dW50cnkuIl0sCiAgWyJzYWxlc0ZlZWQiLCJMaXZlIHNhbGVzIGZlZWQiLCJSZWNlbnQgc2FsZXMg"
    "c2hvd24gaW4tYXBwIGFuZCBhdXRvLXBvc3RlZCB0byB5b3VyIGNoYW5uZWwuIl0sCiAgWyJsZWFk"
    "ZXJib2FyZCIsIkxlYWRlcmJvYXJkIiwiVG9wIGJ1eWVycyBhbmQgcmVmZXJyZXJzIG9mIHRoZSB3"
    "ZWVrLCB3aXRoIGEgcHJpemUuIl0sCiAgWyJidXllckJhZGdlIiwiVmVyaWZpZWQgYnV5ZXIgYmFk"
    "Z2UiLCJCYWRnZSBhbmQgcGVya3MgYWZ0ZXIgTiBjb21wbGV0ZWQgb3JkZXJzLiJdXV0sCiBbIkdy"
    "b3d0aCAmIHJldGVudGlvbiIsWwogIFsicmVmTDIiLCJNdWx0aS1sZXZlbCByZWZlcnJhbCIsIkxl"
    "dmVsIDEgYW5kIGxldmVsIDIgY29tbWlzc2lvbiBpbnN0ZWFkIG9mIGEgZmxhdCByYXRlLiJdLAog"
    "IFsicmVmRGFzaCIsIlJlZmVycmFsIGRhc2hib2FyZCIsIldobyBqb2luZWQsIHdobyBkZXBvc2l0"
    "ZWQgYW5kIHdoYXQgZWFjaCBpbnZpdGUgZWFybmVkLiJdLAogIFsic3RyZWFrIiwiQ2hlY2staW4g"
    "c3RyZWFrIHJld2FyZHMiLCJFc2NhbGF0aW5nIGRhaWx5IHJld2FyZCB0aGF0IHJlc2V0cyB3aGVu"
    "IGJyb2tlbi4iXSwKICBbImxveWFsdHkiLCJMb3lhbHR5IHBvaW50cyIsIkVhcm4gcGVyIOKCuSBz"
    "cGVudCwgcmVkZWVtIGZvciBiYWxhbmNlIG9yIGEgZnJlZSBhY2NvdW50LiJdLAogIFsid2luYmFj"
    "ayIsIlJlLWVuZ2FnZW1lbnQgRE1zIiwiQXV0byBtZXNzYWdlIHVzZXJzIGluYWN0aXZlIGZvciA3"
    "IC8gMzAgZGF5cy4iXSwKICBbImFiYW5kb25SZW1pbmQiLCJBYmFuZG9uZWQtZGVwb3NpdCByZW1p"
    "bmRlciIsIk51ZGdlIGJlZm9yZSBhbiB1bmZpbmlzaGVkIHBheW1lbnQgZXhwaXJlcy4iXSwKICBb"
    "InNjaGVkQnJvYWRjYXN0IiwiU2NoZWR1bGVkICYgc2VnbWVudGVkIGJyb2FkY2FzdHMiLCJRdWV1"
    "ZSBmb3IgbGF0ZXIsIHRhcmdldCBidXllcnMsIG5vbi1idXllcnMgb3IgYmlnIHNwZW5kZXJzLiJd"
    "XV0sCiBbIkFkbWluICYgb3BlcmF0aW9ucyIsWwogIFsiYWRtaW5Sb2xlcyIsIkFkbWluIHJvbGVz"
    "IiwiT3duZXIgLyBtYW5hZ2VyIC8gc3VwcG9ydCB3aXRoIHNlcGFyYXRlIHBlcm1pc3Npb25zLiJd"
    "LAogIFsiYXVkaXRMb2ciLCJBdWRpdCBsb2ciLCJFdmVyeSBhZG1pbiBhY3Rpb24gbG9nZ2VkIHdp"
    "dGggdGltZXN0YW1wIGFuZCB1bmRvLiJdLAogIFsiY29udGVudEVkaXRvciIsIkluLWJvdCBjb250"
    "ZW50IGVkaXRvciIsIkVkaXQgZXZlcnkgbWVzc2FnZSBhbmQgYnV0dG9uIHdpdGhvdXQgdG91Y2hp"
    "bmcgY29kZS4iXSwKICBbImF1dG9CYWNrdXAiLCJTY2hlZHVsZWQgYXV0by1iYWNrdXAiLCJEYXRh"
    "YmFzZSB0byBhIHByaXZhdGUgY2hhbm5lbCBldmVyeSBOIGhvdXJzLCBvbmUtdGFwIHJlc3RvcmUu"
    "Il0sCiAgWyJidWxrVXBsb2FkIiwiQnVsayB1cGxvYWQgd2l0aCB2YWxpZGF0aW9uIiwiVmFsaWRh"
    "dGUgZWFjaCBzZXNzaW9uLCByZXBvcnQgZGVhZCBvciBkdXBsaWNhdGUsIGltcG9ydCB0aGUgcmVz"
    "dC4iXSwKICBbImR1cERldGVjdCIsIkR1cGxpY2F0ZSBkZXRlY3Rpb24iLCJSZWplY3QgYSBwaG9u"
    "ZSBvciBzZXNzaW9uIGFscmVhZHkgaW4gdGhlIGRhdGFiYXNlLiJdLAogIFsibG93U3RvY2siLCJM"
    "b3ctc3RvY2sgYWxlcnRzIiwiUGluZyB3aGVuIGEgY291bnRyeSBkcm9wcyBiZWxvdyBpdHMgb3du"
    "IHRocmVzaG9sZC4iXSwKICBbInNlbGxIb3VycyIsIlNlbGxpbmctaG91cnMgc2NoZWR1bGUiLCJB"
    "dXRvLWhpZGUgY291bnRyaWVzIG91dHNpZGUgdGhlaXIgc2VsbGluZyB3aW5kb3cuIl0sCiAgWyJz"
    "YW5kYm94IiwiVGVzdCBtb2RlIC8gc2FuZGJveCIsIlJ1biB0aGUgd2hvbGUgZmxvdyBvbiBmYWtl"
    "IG1vbmV5IGJlZm9yZSBnb2luZyBsaXZlLiJdLAogIFsiaTE4biIsIk11bHRpLWxhbmd1YWdlIiwi"
    "UGVyLXVzZXIgSGluZGkgLyBFbmdsaXNoIHN3aXRjaCBhY3Jvc3MgZXZlcnkgc3RyaW5nLiJdXV0s"
    "CiBbIlNlY3VyaXR5ICYgYW50aS1hYnVzZSIsWwogIFsicmF0ZUxpbWl0IiwiUmF0ZSBsaW1pdGlu"
    "ZyIsIlBlci11c2VyIGFjdGlvbiBsaW1pdHMgc28gbm9ib2R5IGNhbiBoYW1tZXIgdGhlIGJvdC4i"
    "XSwKICBbIm11bHRpQWNjIiwiTXVsdGktYWNjb3VudCBkZXRlY3Rpb24iLCJDYXRjaCByZWZlcnJh"
    "bCBmYXJtaW5nIGFuZCBmbGFnIHN1c3BpY2lvdXMgc2lnbnVwcy4iXSwKICBbInZlbG9jaXR5Iiwi"
    "UHVyY2hhc2UgdmVsb2NpdHkgYWxlcnRzIiwiRmxhZyBhYm5vcm1hbGx5IGZhc3QgYnV5aW5nIOKA"
    "lCB0eXBpY2FsIG9mIHN0b2xlbiB0b3AtdXBzLiJdLAogIFsiYWRtaW4yZmEiLCJBZG1pbiAyRkEi"
    "LCJQSU4gb3IgVE9UUCBiZWZvcmUgYmFsYW5jZSBlZGl0cywgYnJvYWRjYXN0cyBvciB3aXBlcy4i"
    "XSwKICBbImF1dG9CYW4iLCJBdXRvLWJhbiBydWxlcyIsIkJhbiBvbiBjaGFyZ2ViYWNrLCBmYWtl"
    "IFVUUiBvciByZXBlYXRlZCBmYWlsZWQgdmVyaWZpY2F0aW9uLiJdLAogIFsiYmxhY2tsaXN0Iiwi"
    "QmxhY2tsaXN0IiwiUGVybWFuZW50bHkgYmxvY2sgdXNlciBJRHMsIHBob25lIG51bWJlcnMgYW5k"
    "IFVQSSByZWZlcmVuY2VzLiJdLAogIFsiZW5jcnlwdCIsIlNlc3Npb24gZW5jcnlwdGlvbiBhdCBy"
    "ZXN0IiwiQSBsZWFrZWQgZGF0YWJhc2UgZmlsZSBpcyB1c2VsZXNzIHdpdGhvdXQgdGhlIGtleS4i"
    "XV1dLAogWyJBbmFseXRpY3MiLFsKICBbInJldkNoYXJ0IiwiUmV2ZW51ZSBkYXNoYm9hcmQiLCJE"
    "YWlseSwgd2Vla2x5IGFuZCBtb250aGx5IHJldmVudWUgYW5kIHByb2ZpdCBhcyBjaGFydHMuIl0s"
    "CiAgWyJmdW5uZWwiLCJDb252ZXJzaW9uIGZ1bm5lbCIsIlNlZSB3aGVyZSB1c2VycyBkcm9wIG9m"
    "ZiBiZXR3ZWVuIGpvaW5pbmcgYW5kIGJ1eWluZy4iXSwKICBbInByb2ZpdFJlcCIsIlBlci1jb3Vu"
    "dHJ5IHByb2ZpdCIsIkNvc3QgdnMgc2FsZSBwcmljZSB2cyB2b2x1bWUgZm9yIGV2ZXJ5IGNvdW50"
    "cnkuIl0sCiAgWyJjbHYiLCJDdXN0b21lciBsaWZldGltZSB2YWx1ZSIsIlNwZW5kLCBmcmVxdWVu"
    "Y3kgYW5kIGNodXJuIHJpc2sgcGVyIHVzZXIuIl0sCiAgWyJkaWdlc3QiLCJEYWlseSBkaWdlc3Qi"
    "LCJFbmQtb2YtZGF5IHN1bW1hcnkgcHVzaGVkIHRvIHRoZSBhZG1pbiBjaGF0LiJdXV0sCiBbIlF1"
    "YWxpdHkgb2YgbGlmZSIsWwogIFsiaW5saW5lU2VhcmNoIiwiSW5saW5lIHNlYXJjaCIsIlR5cGUg"
    "QHlvdXJib3QgaW5kaWEgaW4gYW55IGNoYXQgdG8gZmluZCBhbmQgYnV5LiJdLAogIFsic2xhc2hD"
    "bWRzIiwiU2xhc2ggY29tbWFuZHMgbWVudSIsIi9zdGFydCAvYnV5IC9iYWxhbmNlIC9vcmRlcnMg"
    "cmVnaXN0ZXJlZCB3aXRoIFRlbGVncmFtLiJdLAogIFsic3RvY2tDb3VudGVyIiwiTGl2ZSBzdG9j"
    "ayBjb3VudGVyIiwiQXV0by11cGRhdGluZyBwaW5uZWQgcG9zdCBhbmQgaW4tYXBwIGxpdmUgY291"
    "bnQuIl0sCiAgWyJvbmJvYXJkaW5nIiwiT25ib2FyZGluZyB0b3VyIiwiVGhyZWUtc3RlcCB3YWxr"
    "dGhyb3VnaCBmb3IgZmlyc3QtdGltZSB1c2Vycy4iXSwKICBbIm5vdGlmUHJlZnMiLCJOb3RpZmlj"
    "YXRpb24gcHJlZmVyZW5jZXMiLCJVc2VycyBwaWNrIHdoaWNoIGFsZXJ0cyB0aGV5IHJlY2VpdmUu"
    "Il0sCiAgWyJwdXNoTm90aWYiLCJNaW5pIGFwcCBwdXNoIiwiTWluaSBhcHAgYWN0aW9ucyBub3Rp"
    "ZnkgdGhlIHVzZXIgdGhyb3VnaCB0aGUgYm90LiJdXV0KXTsKY29uc3QgRkVBVD17fTsKRkdST1VQ"
    "Uy5mb3JFYWNoKChbZyxyb3dzXSk9PnJvd3MuZm9yRWFjaChyPT5GRUFUW3JbMF1dPXRydWUpKTsK"
    "Y29uc3QgRkVBVDA9T2JqZWN0LmFzc2lnbih7fSxGRUFUKTsKY29uc3QgRz0oayxoKT0+RkVBVFtr"
    "XT9oOiIiOwpjb25zdCBhbnlGPSguLi5rcyk9PmtzLnNvbWUoaz0+RkVBVFtrXSk7CmxldCBDQVJU"
    "PVtdLFdJU0g9W107CmxldCBSRVZJRVdTPXtJbmRpYTpbNC44LDQxMl0sSW5kb25lc2lhOls0LjYs"
    "MTgwXSxCcmF6aWw6WzQuNyw5Nl0sTmlnZXJpYTpbNC40LDU0XSxWaWV0bmFtOls0LjksMjMzXSxQ"
    "aGlsaXBwaW5lczpbNC41LDcxXX07CmxldCBGRUVEPVtbIvCfh67wn4ezIiwiSW5kaWEiLCLigrkz"
    "NSIsIjIgbWluIGFnbyJdLFsi8J+Hu/Cfh7MiLCJWaWV0bmFtIiwi4oK5MjYiLCI2IG1pbiBhZ28i"
    "XSxbIvCfh6fwn4e3IiwiQnJhemlsIiwi4oK5NDQiLCIxMSBtaW4gYWdvIl1dOwpsZXQgQURNPXsK"
    "ICBrcGk6e3Jldjo0ODIwLG9yZGVyczo2MSx1c2VyczoxMjg0LGF1dG86Mzh9LAogIGJhcnM6WzQy"
    "LDYxLDM4LDc0LDU1LDg4LDY3XSwKICBzdG9jazpbXSxvcmRlcnM6W10sZGlzcHV0ZXM6W10sdXNl"
    "cnM6W10sYWRtaW5zOltdLGNoYW5uZWxzOltdLAogIGxvZ3M6e21hc3Rlcjp0cnVlLGNoYW5uZWxz"
    "OltdLGV2ZW50czpbXX0sY291cG9uczpbXSxhdWRpdDpbXSxyZXZpZXdzOltdLGRlcG9zaXRzOltd"
    "LGxvZ2ZlZWQ6W10sCiAgcGF5Ont1cGk6IiIscGF5ZWU6IiIsZ21haWw6IiIsZ21haWxfc2V0OmZh"
    "bHNlLHdpbmRvdzo1LHByZWZpeDoiUEFZIixtaW46MTAsbWF4OjIwMDAwLGF1dG9kZXA6MCxwZW5k"
    "aW5nOjB9LAogIHNlY3VyaXR5OntyYXRlOjIwLHZlbG9jaXR5OjUsYmxhY2tsaXN0OjAsYmFubmVk"
    "OjB9LAogIG1hcmtldDp7Zmxhc2g6MjAsZmxhc2hfb246ZmFsc2UscmVmMTo1LHJlZjI6MSxnb2Fs"
    "OjAsbW9udGg6MH0sCiAgc2V0dGluZ3M6e3N0b3JlOiIiLHRhZ2xpbmU6IiIsc3VwcG9ydDoiIixw"
    "cmljZTozNSxkYWlseTowLjQsYXBwOiIiLG1haW50ZW5hbmNlOmZhbHNlLHNhbmRib3g6ZmFsc2V9"
    "LAogIGFuYWx5dGljczp7ZnVubmVsOnt1c2VyczowLGRlcDowLGJ1eTowfSxjb3VudHJ5OltdLHN0"
    "b2NrX3RvdGFsOjAsc29sZF90b3RhbDowfQp9OwpmdW5jdGlvbiBhZG1OdW0obil7cmV0dXJuIChu"
    "fHwwKS50b0xvY2FsZVN0cmluZygiZW4tSU4iKTt9CmxldCBFQVJOPXtzdHJlYWs6NCxkYWlseTow"
    "LjQwLGNsYWltZWQ6ZmFsc2UscG9pbnRzOjM0MCxwb2ludHNXb3J0aDozNCxpbnZpdGVzOjEyLAog"
    "IGwxOjUyLjQwLGwyOjEyLjQwLGVhcm5lZDo2NC44MCxsaW5rOiJ0Lm1lL3lvdXJfYm90P3N0YXJ0"
    "PXJlZl9kZW1vIiwKICByZWNlbnQ6W1sidXNlcl8xMDQxIiwiZGVwb3NpdGVkIOKCuTIwMCIsIivi"
    "grkyLjAwIl0sWyJ1c2VyXzEwMzgiLCJqb2luZWQiLCLigJQiXSxbInVzZXJfMTAyMiIsImRlcG9z"
    "aXRlZCDigrk1MDAiLCIr4oK5NS4wMCJdXSwKICBsZWFkZXJzOltbIjEiLCJAcmFodWwiLCLigrk0"
    "LDgyMCJdLFsiMiIsIkBhbWFuIiwi4oK5MywxNDAiXSxbIjMiLCJAeW91Iiwi4oK5MSw5ODAiXV0s"
    "CiAgdGFza3M6W1siSm9pbiB0aGUgbWFpbiBjaGFubmVsIiwi4oK5MS4wMCIsMV0sWyJQbGFjZSB5"
    "b3VyIGZpcnN0IG9yZGVyIiwi4oK5Mi4wMCIsMV0sWyJJbnZpdGUgMyBmcmllbmRzIiwi4oK5NS4w"
    "MCIsMF1dfTsKZnVuY3Rpb24gc3RhcnMoYyl7Y29uc3Qgcj0oUkVWSUVXU1tjXXx8WzQuNywyMF0p"
    "WzBdLG49KFJFVklFV1NbY118fFs0LjcsMjBdKVsxXTsKICByZXR1cm4gYDxzcGFuIGNsYXNzPSJz"
    "dGFycyI+JHtbMSwyLDMsNCw1XS5tYXAoaT0+aWMoaTw9TWF0aC5yb3VuZChyKT8ic3RhciI6InN0"
    "YXIiKSkuam9pbigiIil9PGI+JHtyfSAoJHtufSk8L2I+PC9zcGFuPmA7fQpmdW5jdGlvbiBhcHBs"
    "eUZlYXR1cmVzKCl7CiAgY29uc3QgY2M9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNhcnRjaGlw"
    "Iik7CiAgaWYoY2Mpe2NjLmNsYXNzTGlzdC50b2dnbGUoImhpZGRlbiIsIUZFQVQuY2FydCk7ZG9j"
    "dW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNhcnRuIikudGV4dENvbnRlbnQ9Q0FSVC5sZW5ndGg7fQog"
    "IGNvbnN0IGVhcm49YW55Rigic3RyZWFrIiwicmVmRGFzaCIsInJlZkwyIiwibG95YWx0eSIsImxl"
    "YWRlcmJvYXJkIiwid2luYmFjayIpOwogIGNvbnN0IGViPWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3Io"
    "JyN0YWJzIGJ1dHRvbltkYXRhLXRhYj0iZWFybiJdJyk7CiAgaWYoZWIpe2ViLmNsYXNzTGlzdC50"
    "b2dnbGUoImhpZGRlbiIsIWVhcm4pOwogICAgaWYoIWVhcm4mJlRBQj09PSJlYXJuIil7VEFCPSJz"
    "aG9wIjtkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjdGFicyBidXR0b24iKS5mb3JFYWNoKHg9"
    "PnguY2xhc3NMaXN0LnRvZ2dsZSgib24iLHguZGF0YXNldC50YWI9PT0ic2hvcCIpKTtyZW5kZXIo"
    "KTt9fQogIGNvbnN0IGFtYXA9e21hcmtldDphbnlGKCJjb3Vwb25zIiwiZmxhc2hTYWxlIiwic2No"
    "ZWRCcm9hZGNhc3QiLCJ3aW5iYWNrIiwic2FsZXNGZWVkIiwibGVhZGVyYm9hcmQiLCJsb3lhbHR5"
    "IiwicmVmRGFzaCIsInJlZkwyIiwiYWJhbmRvblJlbWluZCIpLAogICAgYW5hbHl0aWNzOmFueUYo"
    "InJldkNoYXJ0IiwiZnVubmVsIiwicHJvZml0UmVwIiwiY2x2IiwiZGlnZXN0IiksCiAgICBzZWN1"
    "cml0eTphbnlGKCJyYXRlTGltaXQiLCJtdWx0aUFjYyIsInZlbG9jaXR5IiwiYWRtaW4yZmEiLCJh"
    "dXRvQmFuIiwiYmxhY2tsaXN0IiwiZW5jcnlwdCIpLAogICAgY29udGVudDpGRUFULmNvbnRlbnRF"
    "ZGl0b3J9OwogIE9iamVjdC5lbnRyaWVzKGFtYXApLmZvckVhY2goKFtrLHZdKT0+e2NvbnN0IGI9"
    "ZG9jdW1lbnQucXVlcnlTZWxlY3RvcignI2FzdWIgYnV0dG9uW2RhdGEtYT0iJytrKyciXScpOwog"
    "ICAgaWYoYil7Yi5jbGFzc0xpc3QudG9nZ2xlKCJoaWRkZW4iLCF2KTsKICAgICAgaWYoIXYmJkFU"
    "QUI9PT1rKXtBVEFCPSJkYXNoIjtkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjYXN1YiBidXR0"
    "b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnRvZ2dsZSgib24iLHguZGF0YXNldC5hPT09ImRh"
    "c2giKSk7cmVuZGVyQWRtaW4oKTt9fX0pOwogIGlmKHdpbmRvdy5fX2RvY2tQbGFjZSlyZXF1ZXN0"
    "QW5pbWF0aW9uRnJhbWUoKCk9PndpbmRvdy5fX2RvY2tQbGFjZSh0cnVlKSk7CiAgaWYod2luZG93"
    "Ll9fdnRhYlBsYWNlKXJlcXVlc3RBbmltYXRpb25GcmFtZSgoKT0+d2luZG93Ll9fdnRhYlBsYWNl"
    "KHRydWUpKTsKfQpmdW5jdGlvbiBhcHBseUNNUygpewogY29uc3QgZz1pZD0+ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoaWQpOwogaWYoZygiYm5hbWUiKSlnKCJibmFtZSIpLnRleHRDb250ZW50PUNN"
    "Uy5zdG9yZU5hbWU7CiBpZihnKCJhYm5hbWUiKSlnKCJhYm5hbWUiKS50ZXh0Q29udGVudD1DTVMu"
    "c3RvcmVOYW1lOwogaWYoQ01TLnN0b3JlTmFtZSl7Q0ZHLm5hbWU9Q01TLnN0b3JlTmFtZTt9CiBp"
    "ZihDTVMuc3RvcmVUYWcpQ0ZHLnRhZz1DTVMuc3RvcmVUYWc7CiBwYWludEJyYW5kKENNUy5zdG9y"
    "ZU5hbWUsQ01TLnN0b3JlVGFnKTsKIHRyeXsKICAgaWYoQ01TLnN0b3JlTmFtZSlsb2NhbFN0b3Jh"
    "Z2Uuc2V0SXRlbSgic3RvcmVOYW1lIixDTVMuc3RvcmVOYW1lKTsKICAgaWYoQ01TLnN0b3JlVGFn"
    "KWxvY2FsU3RvcmFnZS5zZXRJdGVtKCJzdG9yZVRhZyIsQ01TLnN0b3JlVGFnKTsKIH1jYXRjaChl"
    "KXt9CiBpZihnKCJidGFnIikpZygiYnRhZyIpLnRleHRDb250ZW50PUNNUy5zdG9yZVRhZzsKIGRv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLW5hdl0iKS5mb3JFYWNoKG49PnsKICAgY29u"
    "c3Qgaz0ibmF2IituLmRhdGFzZXQubmF2WzBdLnRvVXBwZXJDYXNlKCkrbi5kYXRhc2V0Lm5hdi5z"
    "bGljZSgxKTsKICAgaWYoQ01TW2tdKW4udGV4dENvbnRlbnQ9Q01TW2tdO30pOwogaWYod2luZG93"
    "Ll9fZG9ja1BsYWNlKXJlcXVlc3RBbmltYXRpb25GcmFtZSgoKT0+d2luZG93Ll9fZG9ja1BsYWNl"
    "KHRydWUpKTsKfQpsZXQgQkFMPTI0OC41MDsKY29uc3QgU1RPQ0s9Wwoge2M6IkluZGlhIixmOiLw"
    "n4eu8J+HsyIsY29kZToiKzkxIixwcmljZTozNSx3YXM6NDkscXR5OjEyOCxob3Q6MX0sCiB7Yzoi"
    "SW5kb25lc2lhIixmOiLwn4eu8J+HqSIsY29kZToiKzYyIixwcmljZToyOCx3YXM6MCxxdHk6NjR9"
    "LAoge2M6IkJyYXppbCIsZjoi8J+Hp/Cfh7ciLGNvZGU6Iis1NSIscHJpY2U6NDQsd2FzOjU5LHF0"
    "eToyMn0sCiB7YzoiTmlnZXJpYSIsZjoi8J+Hs/Cfh6wiLGNvZGU6IisyMzQiLHByaWNlOjMxLHdh"
    "czowLHF0eTowfSwKIHtjOiJWaWV0bmFtIixmOiLwn4e78J+HsyIsY29kZToiKzg0IixwcmljZToy"
    "Nix3YXM6MCxxdHk6OTF9LAoge2M6IlBoaWxpcHBpbmVzIixmOiLwn4e18J+HrSIsY29kZToiKzYz"
    "IixwcmljZTozOCx3YXM6MCxxdHk6N30KXTsKY29uc3QgT1JERVJTPVsKIHtpZDoiT1JELTc3NDEi"
    "LGM6IkluZGlhIixmOiLwn4eu8J+HsyIsbnVtOiIrOTEg4oCi4oCi4oCi4oCiIOKAouKAojMyIixz"
    "dDoiRGVsaXZlcmVkIix3aGVuOiIyIG1pbiBhZ28iLHByaWNlOjM1fSwKIHtpZDoiT1JELTc3Mjgi"
    "LGM6IlZpZXRuYW0iLGY6IvCfh7vwn4ezIixudW06Iis4NCDigKLigKLigKLigKIg4oCi4oCiMDci"
    "LHN0OiJEZWxpdmVyZWQiLHdoZW46Illlc3RlcmRheSIscHJpY2U6MjZ9LAoge2lkOiJPUkQtNzcw"
    "MiIsYzoiQnJhemlsIixmOiLwn4en8J+HtyIsbnVtOiIrNTUg4oCi4oCi4oCi4oCiIOKAouKAojg4"
    "IixzdDoiUmVmdW5kZWQiLHdoZW46IjMgZGF5cyBhZ28iLHByaWNlOjQ0fQpdOwpsZXQgQ0hBTk5F"
    "TFM9Wwoge2lkOjEsbmFtZToiTWFpbiBDaGFubmVsIixoYW5kbGU6IkB5b3VyX21haW5fY2hhbm5l"
    "bCIscmVxOnRydWUsb246dHJ1ZSxtZW1iZXJzOiIxMi40SyJ9LAoge2lkOjIsbmFtZToiQmFja3Vw"
    "IENoYW5uZWwiLGhhbmRsZToiQHlvdXJfYmFja3VwIixyZXE6dHJ1ZSxvbjp0cnVlLG1lbWJlcnM6"
    "IjMuMUsifSwKIHtpZDozLG5hbWU6IkRpc2N1c3Npb24gR3JvdXAiLGhhbmRsZToiQHlvdXJfZ3Jv"
    "dXAiLHJlcTpmYWxzZSxvbjp0cnVlLG1lbWJlcnM6IjUuOEsifQpdOwpsZXQgTE9HQ0g9Wwoge2lk"
    "OjEsbmFtZToiTWFpbiBMb2dzIixoYW5kbGU6IkB5b3VyX2xvZ3MiLHByaW1hcnk6dHJ1ZSxvbjp0"
    "cnVlfSwKIHtpZDoyLG5hbWU6IlBheW1lbnQgTG9ncyIsaGFuZGxlOiJAeW91cl9wYXlfbG9ncyIs"
    "cHJpbWFyeTpmYWxzZSxvbjp0cnVlfSwKIHtpZDozLG5hbWU6IkVycm9yIExvZ3MiLGhhbmRsZToi"
    "LTEwMHh4eHh4eHh4eHgiLHByaW1hcnk6ZmFsc2Usb246ZmFsc2V9Cl07CmxldCBMT0dTX01BU1RF"
    "Uj10cnVlOwpsZXQgRVZFTlRTPVsKIHtrOiJOZXcgdXNlciBqb2luZWQiLG9uOmZhbHNlLGNoOjF9"
    "LHtrOiJEZXBvc2l0IC8gcGF5bWVudCIsb246dHJ1ZSxjaDoyfSwKIHtrOiJQdXJjaGFzZSIsb246"
    "dHJ1ZSxjaDoxfSx7azoiT1RQIHJlcXVlc3RlZCIsb246ZmFsc2UsY2g6MX0sCiB7azoiU3RvY2sg"
    "YWRkZWQiLG9uOnRydWUsY2g6MX0se2s6IkFkbWluIGFjdGlvbiIsb246dHJ1ZSxjaDoxfSwKIHtr"
    "OiJFcnJvcnMgJiB3YXJuaW5ncyIsb246dHJ1ZSxjaDozfQpdOwpjb25zdCBUWD1be3Q6IkRlcG9z"
    "aXQiLGE6IivigrkyMDAuMDAiLHc6IlRvZGF5LCAxNDowMiJ9LHt0OiJQdXJjaGFzZSDCtyBJbmRp"
    "YSIsYToiLeKCuTM1LjAwIix3OiJUb2RheSwgMTQ6MDUifSwKIHt0OiJEYWlseSBjaGVjay1pbiIs"
    "YToiK+KCuTAuNDAiLHc6IlRvZGF5LCAwOToxMSJ9LHt0OiJSZWZlcnJhbCBib251cyIsYToiK+KC"
    "uTMuMjAiLHc6Illlc3RlcmRheSJ9XTsKCmxldCBGSUxURVJFRD1TVE9DSy5zbGljZSgpLCBRVUVS"
    "WT0iIiwgQ0hJUD0iYWxsIjsKY29uc3QgRU1QVFk9JzxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJ0"
    "ZXh0LWFsaWduOmNlbnRlciI+PHAgY2xhc3M9InN1YiI+Tm90aGluZyBtYXRjaGVzIHRoYXQgc2Vh"
    "cmNoLjwvcD48L2Rpdj4nOwpjb25zdCBJQz17YmFnOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRc"
    "IiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJc"
    "IiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0"
    "aCBkPVwiTTYgMiAzIDZ2MTRhMiAyIDAgMCAwIDIgMmgxNGEyIDIgMCAwIDAgMi0yVjZsLTMtNHpc"
    "Ii8+PHBhdGggZD1cIk0zIDZoMThcIi8+PHBhdGggZD1cIk0xNiAxMGE0IDQgMCAwIDEtOCAwXCIv"
    "Pjwvc3ZnPiIsYm94OiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0"
    "cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1c"
    "InJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwiTTIxIDhhMiAyIDAg"
    "MCAwLTEtMS43M2wtNy00YTIgMiAwIDAgMC0yIDBsLTcgNEEyIDIgMCAwIDAgMyA4djhhMiAyIDAg"
    "MCAwIDEgMS43M2w3IDRhMiAyIDAgMCAwIDIgMGw3LTRBMiAyIDAgMCAwIDIxIDE2WlwiLz48cGF0"
    "aCBkPVwibTMuMyA3IDguNyA1IDguNy01XCIvPjxwYXRoIGQ9XCJNMTIgMjJWMTJcIi8+PC9zdmc+"
    "Iix6YXA6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwi"
    "Y3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRc"
    "IiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTMgMiAzIDE0aDlsLTEgOCAx"
    "MC0xMmgtOXpcIi8+PC9zdmc+Iix3YWxsZXQ6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZp"
    "bGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0"
    "cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9"
    "XCJNMTkgN1Y1YTIgMiAwIDAgMC0yLTJINWEyIDIgMCAwIDAgMCA0aDE1YTIgMiAwIDAgMSAyIDJ2"
    "OWEyIDIgMCAwIDEtMiAySDVhMiAyIDAgMCAxLTItMlY1XCIvPjxwYXRoIGQ9XCJNMTggMTJoLjAx"
    "XCIvPjwvc3ZnPiIsdXNlcjoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVc"
    "IiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVj"
    "YXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0xOSAyMXYt"
    "MmE0IDQgMCAwIDAtNC00SDlhNCA0IDAgMCAwLTQgNHYyXCIvPjxjaXJjbGUgY3g9XCIxMlwiIGN5"
    "PVwiN1wiIHI9XCI0XCIvPjwvc3ZnPiIsc2hpZWxkOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRc"
    "IiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJc"
    "IiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0"
    "aCBkPVwiTTIwIDEzYzAgNS0zLjUgNy41LTcuNjYgOC45NWExIDEgMCAwIDEtLjY3LS4wMUM3LjUg"
    "MjAuNSA0IDE4IDQgMTNWNmExIDEgMCAwIDEgMS0xYzIgMCA0LjUtMS4yIDYuMjQtMi43MmExLjE3"
    "IDEuMTcgMCAwIDEgMS41MiAwQzE0LjUxIDMuODEgMTcgNSAxOSA1YTEgMSAwIDAgMSAxIDF6XCIv"
    "Pjwvc3ZnPiIsY2hlY2s6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIg"
    "c3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMi40XCIgc3Ryb2tlLWxpbmVj"
    "YXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0yMCA2IDkg"
    "MTdsLTUtNVwiLz48L3N2Zz4iLHNlbmQ6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9"
    "XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9r"
    "ZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJt"
    "MjIgMi03IDIwLTQtOS05LTRaXCIvPjxwYXRoIGQ9XCJNMjIgMiAxMSAxM1wiLz48L3N2Zz4iLHBs"
    "dXM6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3Vy"
    "cmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMi40XCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwi"
    "PjxwYXRoIGQ9XCJNMTIgNXYxNE01IDEyaDE0XCIvPjwvc3ZnPiIsY29weToiPHN2ZyB2aWV3Qm94"
    "PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJv"
    "a2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1c"
    "InJvdW5kXCI+PHJlY3Qgd2lkdGg9XCIxNFwiIGhlaWdodD1cIjE0XCIgeD1cIjhcIiB5PVwiOFwi"
    "IHJ4PVwiMlwiLz48cGF0aCBkPVwiTTQgMTZjLTEuMSAwLTItLjktMi0yVjRjMC0xLjEuOS0yIDIt"
    "MmgxMGMxLjEgMCAyIC45IDIgMlwiLz48L3N2Zz4iLGNoZXY6Ijxzdmcgdmlld0JveD1cIjAgMCAy"
    "NCAyNFwiIHdpZHRoPVwiMTZcIiBoZWlnaHQ9XCIxNlwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwi"
    "Y3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRc"
    "IiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJtOSAxOCA2LTYtNi02XCIvPjwv"
    "c3ZnPiIsZmlyZToiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgd2lkdGg9XCIxMlwiIGhlaWdo"
    "dD1cIjEyXCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lk"
    "dGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5k"
    "XCI+PHBhdGggZD1cIk0xMiAyYzEgNCA1IDUgNSA5YTUgNSAwIDAgMS0xMCAwYzAtMiAxLTMgMi00"
    "IDAgMiAxIDMgMiAzIDAtMy0xLTUgMS04WlwiLz48L3N2Zz4iLGRvd246Ijxzdmcgdmlld0JveD1c"
    "IjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tl"
    "LXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJy"
    "b3VuZFwiPjxwYXRoIGQ9XCJNMTIgNXYxNE0xOSAxMmwtNyA3LTctN1wiLz48L3N2Zz4iLHVwOiI8"
    "c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRD"
    "b2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tl"
    "LWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwiTTEyIDE5VjVNNSAxMmw3LTcgNyA3XCIvPjwv"
    "c3ZnPiIsc2VhcmNoOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0"
    "cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1c"
    "InJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48Y2lyY2xlIGN4PVwiMTFcIiBjeT1c"
    "IjExXCIgcj1cIjdcIi8+PHBhdGggZD1cIm0yMSAyMS00LjMtNC4zXCIvPjwvc3ZnPiIsc3Rhcjoi"
    "PHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cImN1cnJlbnRDb2xvclwiIHN0cm9rZT1c"
    "ImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjEuNVwiIHN0cm9rZS1saW5lam9pbj1cInJv"
    "dW5kXCI+PHBhdGggZD1cIm0xMiAzIDIuNiA1LjMgNS45LjktNC4zIDQuMSAxIDUuOC01LjItMi43"
    "LTUuMiAyLjcgMS01LjhMMy41IDkuMmw1LjktLjl6XCIvPjwvc3ZnPiJ9Owpjb25zdCBpYz1uPT5J"
    "Q1tuXXx8IiI7CmNvbnN0IGVzYz1zPT5TdHJpbmcocykucmVwbGFjZSgvWyY8PiJdL2csYz0+KHsi"
    "JiI6IiZhbXA7IiwiPCI6IiZsdDsiLCI+IjoiJmd0OyIsJyInOiImcXVvdDsifVtjXSkpOwpjb25z"
    "dCBtb25leT1uPT5DRkcuY3VyK051bWJlcihuKS50b0ZpeGVkKDIpOwoKbGV0IHR0O2NvbnN0IHRv"
    "YXN0PW09Pntjb25zdCBlPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJ0b2FzdCIpO2UudGV4dENv"
    "bnRlbnQ9bTtlLmNsYXNzTGlzdC5hZGQoInNob3ciKTtjbGVhclRpbWVvdXQodHQpO3R0PXNldFRp"
    "bWVvdXQoKCk9PmUuY2xhc3NMaXN0LnJlbW92ZSgic2hvdyIpLDE5MDApfTsKZG9jdW1lbnQuYWRk"
    "RXZlbnRMaXN0ZW5lcigiY2xpY2siLGU9Pntjb25zdCBiPWUudGFyZ2V0LmNsb3Nlc3QoIltkYXRh"
    "LXRvYXN0XSIpO2lmKGIpdG9hc3QoYi5kYXRhc2V0LnRvYXN0KX0pOwpjb25zdCBzaGVldD1oPT57"
    "CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInBhbmVsIikuaW5uZXJIVE1MPSc8ZGl2IGNsYXNz"
    "PSJncmFiIj48L2Rpdj4nK2g7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNoZWV0IikuY2xh"
    "c3NMaXN0LmFkZCgib3BlbiIpOwogIC8qIGNvbnRyb2xzIGluc2lkZSBhIHNoZWV0IGFyZSBjcmVh"
    "dGVkIGFmdGVyIGJpbmRpbmcsIHNvIGJpbmQgdGhlbSBub3cgKi8KICB0cnl7IGlmKHR5cGVvZiBi"
    "aW5kQ29tbW9uPT09ImZ1bmN0aW9uIikgYmluZENvbW1vbigpOyB9Y2F0Y2goZSl7fQogIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNzaGVldCBbZGF0YS1jbG9zZV0iKS5mb3JFYWNoKHg9Pngu"
    "b25jbGljaz1jbG9zZVNoZWV0KTsKfTsKY29uc3QgY2xvc2VTaGVldD0oKT0+ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoInNoZWV0IikuY2xhc3NMaXN0LnJlbW92ZSgib3BlbiIpOwpkb2N1bWVudC5h"
    "ZGRFdmVudExpc3RlbmVyKCJjbGljayIsZT0+e2lmKGUudGFyZ2V0LmNsb3Nlc3QoIltkYXRhLWNs"
    "b3NlXSIpKWNsb3NlU2hlZXQoKX0pOwoKZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3N3IGJ1"
    "dHRvbiIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgaWYoYi5kYXRhc2V0LnN3PT09ImFk"
    "bWluIiYmIWFkbWluQWxsb3dlZCgpKXt0b2FzdCgiQWRtaW5zIG9ubHkiKTtyZXR1cm47fQogIGRv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNzdyBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NM"
    "aXN0LnJlbW92ZSgib24iKSk7Yi5jbGFzc0xpc3QuYWRkKCJvbiIpOwogIFsiYXBwIiwiYWRtaW4i"
    "XS5mb3JFYWNoKHY9PmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJ2aWV3LSIrdikuY2xhc3NMaXN0"
    "LnRvZ2dsZSgiaGlkZGVuIix2IT09Yi5kYXRhc2V0LnN3KSk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoInRhYnMiKS5zdHlsZS5kaXNwbGF5PWIuZGF0YXNldC5zdz09PSJhcHAiPyIiOiJub25l"
    "Ijt3aW5kb3cuc2Nyb2xsVG8oMCwwKTsKICByZXF1ZXN0QW5pbWF0aW9uRnJhbWUoKCk9PntpZih3"
    "aW5kb3cuX19kb2NrUGxhY2Upd2luZG93Ll9fZG9ja1BsYWNlKHRydWUpOwogICAgaWYod2luZG93"
    "Ll9fdnRhYlBsYWNlKXdpbmRvdy5fX3Z0YWJQbGFjZSh0cnVlKTt9KTt9KTsKCmxldCBUQUI9InNo"
    "b3AiOwpkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjdGFicyBidXR0b24iKS5mb3JFYWNoKGI9"
    "PmIub25jbGljaz0oKT0+ewogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiN0YWJzIGJ1dHRv"
    "biIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3QucmVtb3ZlKCJvbiIpKTtiLmNsYXNzTGlzdC5hZGQo"
    "Im9uIik7CiAgVEFCPWIuZGF0YXNldC50YWI7cmVuZGVyKCk7d2luZG93LnNjcm9sbFRvKDAsMCk7"
    "aWYod2luZG93Ll9fZG9ja1BsYWNlKXJlcXVlc3RBbmltYXRpb25GcmFtZSh3aW5kb3cuX19kb2Nr"
    "UGxhY2UpO30pOwpkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCJjbGljayIsZT0+e2NvbnN0IGc9"
    "ZS50YXJnZXQuY2xvc2VzdCgiW2RhdGEtZ29dIik7aWYoIWcpcmV0dXJuOwogIGNvbnN0IHQ9Zy5k"
    "YXRhc2V0LmdvO2RvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiN0YWJzIGJ1dHRvbiIpLmZvckVh"
    "Y2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseC5kYXRhc2V0LnRhYj09PXQpKTsKICBUQUI9"
    "dDtyZW5kZXIoKTt3aW5kb3cuc2Nyb2xsVG8oMCwwKTt9KTsKCmZ1bmN0aW9uIHJlbmRlcigpe2Nv"
    "bnN0IHN0PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdGFnZSIpOwogIGlmKExPQ0tFRCl7cGFp"
    "bnRMb2NrZWQoKTtyZXR1cm47fQogIHN0LmlubmVySFRNTD0nPGRpdiBjbGFzcz0ic2siPjwvZGl2"
    "PjxkaXYgY2xhc3M9InNrIj48L2Rpdj48ZGl2IGNsYXNzPSJzayIgc3R5bGU9ImhlaWdodDoxNDBw"
    "eCI+PC9kaXY+JzsKICBzZXRUaW1lb3V0KCgpPT57aWYoTE9DS0VEKXtwYWludExvY2tlZCgpO3Jl"
    "dHVybjt9CiAgICBzdC5pbm5lckhUTUw9KHtzaG9wOnZTaG9wLG9yZGVyczp2T3JkZXJzLGVhcm46"
    "dkVhcm4sd2FsbGV0OnZXYWxsZXQseW91OnZZb3V9KVtUQUJdKCk7CiAgICBiaW5kU3RhZ2UoKTty"
    "ZXZlYWxTdGFnZShzdCk7YmluZFRpbHQoKTt9LFJNPzA6MTYwKTt9CgpmdW5jdGlvbiB2U2hvcCgp"
    "ewogIGNvbnN0IGxpdmU9U1RPQ0suZmlsdGVyKHM9PnMucXR5PjApLmxlbmd0aCwgdW5pdHM9U1RP"
    "Q0sucmVkdWNlKChhLGIpPT5hK2IucXR5LDApOwogIGlmKExJVkUmJiFTVE9DSy5sZW5ndGgpewog"
    "ICAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIiBzdHlsZT0ibWFyZ2luOjE4cHggMDt0ZXh0LWFs"
    "aWduOmNlbnRlcjtwYWRkaW5nOjI2cHgiPgogICAgICA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNnB4"
    "Ij4ke2VzYyhDTVMuZW1wdHlUaXRsZXx8IlJlc3RvY2tpbmciKX08L2gzPgogICAgICA8cCBjbGFz"
    "cz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPiR7ZXNjKENNUy5lbXB0eUJvZHl8fCJFdmVyeXRoaW5n"
    "IGp1c3Qgc29sZCBvdXQuIEZyZXNoIGFjY291bnRzIGxhbmQgaGVyZSBzaG9ydGx5IFx1MjAxNCB0"
    "dXJuIG9uIGFuIGFsZXJ0IGFuZCB5b3Ugd2lsbCBoZWFyIGZpcnN0LiIpfTwvcD4KICAgICAgPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4IiBkYXRhLWFjdD0i"
    "YWxlcnRzIj5Ob3RpZnkgbWU8L2J1dHRvbj48L2Rpdj5gOwogIH0KICByZXR1cm4gYAogIDxkaXYg"
    "Y2xhc3M9ImJhbm5lciI+PGRpdiBjbGFzcz0idHJhY2siPgogICAgJHtbMSwyXS5tYXAoKCk9PmA8"
    "c3Bhbj4ke2ljKCJ6YXAiKX0ke0NNUy50aWNrMX08L3NwYW4+PHNwYW4+JHtpYygid2FsbGV0Iil9"
    "JHtDTVMudGljazJ9PC9zcGFuPgogICAgPHNwYW4+JHtpYygic2hpZWxkIil9JHtDTVMudGljazN9"
    "PC9zcGFuPjxzcGFuPiR7aWMoInN0YXIiKX0ke0NNUy50aWNrNH08L3NwYW4+YCkuam9pbigiIil9"
    "CiAgPC9kaXY+PC9kaXY+CiAgJHtHKCJvbmJvYXJkaW5nIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdo"
    "dCIgaWQ9InRvdXJjYXJkIiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtn"
    "YXA6MTBweCI+CiAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJ6YXAiKX08L2Rpdj4KICAgIDxk"
    "aXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+"
    "TmV3IGhlcmU/IDMtc3RlcCB0b3VyPC9iPgogICAgICA8YnI+PHNtYWxsIHN0eWxlPSJjb2xvcjp2"
    "YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjlyZW0iPlRvcCB1cCwgcGljayBhIGNvdW50cnksIGdldCB0"
    "aGUgT1RQIGluLWFwcC48L3NtYWxsPjwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBp"
    "ZD0idG91cmdvIj5TdGFydDwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIGdob3N0"
    "IiBpZD0idG91cnNraXAiPlNraXA8L2J1dHRvbj48L2Rpdj5gKX0KICA8c2VjdGlvbiBjbGFzcz0i"
    "aGVybyI+CiAgICA8ZGl2IGNsYXNzPSJraWNrIj4ke0NNUy5oZXJvS2lja308L2Rpdj4KICAgIDxo"
    "MT4ke0NNUy5oZXJvSDF9PC9oMT4KICAgIDxwPiR7Q01TLmhlcm9TdWIucmVwbGFjZSgie259Iixs"
    "aXZlKX08L3A+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciPgogICAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4gY3RhIiBkYXRhLXNjcm9sbD0ic2hvcGxpc3QiPiR7aWMoImJhZyIpfSAke0NNUy5oZXJvQnRu"
    "MX08L2J1dHRvbj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdsYXNzIiBkYXRhLWdvPSJ3YWxs"
    "ZXQiPiR7Q01TLmhlcm9CdG4yfTwvYnV0dG9uPgogICAgPC9kaXY+CiAgPC9zZWN0aW9uPgogIDxz"
    "dmcgY2xhc3M9InRyYWNlIiB2aWV3Qm94PSIwIDAgMzIwIDEwIiBwcmVzZXJ2ZUFzcGVjdFJhdGlv"
    "PSJub25lIj48cGF0aCBjbGFzcz0iYmFzZSIgZD0iTTAsNSBMMzIwLDUiLz4KICAgIDxwYXRoIGNs"
    "YXNzPSJsZWFkIiBkPSJNMCw1IEwzMjAsNSIgc3Ryb2tlPSJ1cmwoI3RnMSkiLz4KICAgIDxkZWZz"
    "PjxsaW5lYXJHcmFkaWVudCBpZD0idGcxIiBncmFkaWVudFVuaXRzPSJ1c2VyU3BhY2VPblVzZSIg"
    "eDE9Ii0xNjAiIHkxPSIwIiB4Mj0iMCIgeTI9IjAiPgogICAgICA8c3RvcCBzdG9wLWNvbG9yPSJ2"
    "YXIoLS1hY2MpIiBzdG9wLW9wYWNpdHk9IjAiLz48c3RvcCBvZmZzZXQ9Ii41IiBzdG9wLWNvbG9y"
    "PSJ2YXIoLS1hY2MpIi8+CiAgICAgIDxzdG9wIG9mZnNldD0iMSIgc3RvcC1jb2xvcj0idmFyKC0t"
    "YWNjMikiIHN0b3Atb3BhY2l0eT0iMCIvPgogICAgICA8YW5pbWF0ZSBhdHRyaWJ1dGVOYW1lPSJ4"
    "MSIgdmFsdWVzPSItMTYwOzMyMCIgZHVyPSIyLjJzIiByZXBlYXRDb3VudD0iaW5kZWZpbml0ZSIv"
    "PgogICAgICA8YW5pbWF0ZSBhdHRyaWJ1dGVOYW1lPSJ4MiIgdmFsdWVzPSIwOzQ4MCIgZHVyPSIy"
    "LjJzIiByZXBlYXRDb3VudD0iaW5kZWZpbml0ZSIvPgogICAgPC9saW5lYXJHcmFkaWVudD48L2Rl"
    "ZnM+PC9zdmc+CiAgPGRpdiBjbGFzcz0iZmVhdHMiPgogICAgPGRpdiBjbGFzcz0iZmVhdCI+JHtp"
    "YygiemFwIil9PGI+JHtDTVMuZmVhdDF9PC9iPjxzbWFsbD4ke0NNUy5mZWF0MXN9PC9zbWFsbD48"
    "L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZlYXQiPiR7aWMoInNoaWVsZCIpfTxiPiR7Q01TLmZlYXQy"
    "fTwvYj48c21hbGw+JHtDTVMuZmVhdDJzfTwvc21hbGw+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJm"
    "ZWF0Ij4ke2ljKCJ3YWxsZXQiKX08Yj4ke0NNUy5mZWF0M308L2I+PHNtYWxsPiR7Q01TLmZlYXQz"
    "c308L3NtYWxsPjwvZGl2PgogIDwvZGl2PgogICR7Rygic3RvY2tDb3VudGVyIixgPGRpdiBjbGFz"
    "cz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2Fw"
    "OjEwcHgiPgogICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5MaXZl"
    "PC9zcGFuPgogICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzZy"
    "ZW07Y29sb3I6dmFyKC0tdHgyKSI+PGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHt1bml0c308"
    "L2I+IGFjY291bnRzIGluIHN0b2NrIGFjcm9zcyA8YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4k"
    "e2xpdmV9PC9iPiBjb3VudHJpZXM8L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0"
    "IiBkYXRhLWFjdD0iY2hhbm5lbCI+Q2hhbm5lbDwvYnV0dG9uPjwvZGl2PmApfQogICR7RygiZmxh"
    "c2hTYWxlIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGln"
    "bi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgPHNwYW4gY2xhc3M9InBpbGwgaG90Ij4ke2lj"
    "KCJmaXJlIil9ICR7Q01TLmZsYXNoUGlsbH08L3NwYW4+CiAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7"
    "bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NnJlbTtjb2xvcjp2YXIoLS10eDIpIj4ke0NNUy5mbGFz"
    "aFRleHR9IDxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiIGlkPSJjZCI+MTQ6NTI8L2I+PC9kaXY+"
    "CiAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMiIGRhdGEtYWN0PSJmbGFzaCI+JHtDTVMuZmxhc2hC"
    "dG59PC9idXR0b24+PC9kaXY+YCl9CiAgPGRpdiBjbGFzcz0id2hlZWwiPjxkaXYgY2xhc3M9InJp"
    "bmciPiR7U1RPQ0subWFwKChzLGkpPT5gPGIgc3R5bGU9InRyYW5zZm9ybTpyb3RhdGUoJHtpKjYw"
    "fWRlZykgdHJhbnNsYXRlKDcwcHgpIHJvdGF0ZSgtJHtpKjYwfWRlZykiPiR7cy5jb2RlfTwvYj5g"
    "KS5qb2luKCIiKX08L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImNhcCI+PGVtPiR7Q01TLndoZWVsS2lj"
    "a308L2VtPjxiPiR7Q01TLndoZWVsVGl0bGV9PC9iPgogICAgICA8ZGl2IGNsYXNzPSJjbG91ZCIg"
    "c3R5bGU9Im1hcmdpbi10b3A6NnB4Ij48ZGl2IGNsYXNzPSJ0Ij4ke1sxLDJdLm1hcCgoKT0+Q01T"
    "LndoZWVsV29yZHMuc3BsaXQoIiwiKS5tYXAodz0+YDxpPiR7dy50cmltKCl9PC9pPmApLmpvaW4o"
    "IiIpKS5qb2luKCIiKX08L2Rpdj48L2Rpdj48L2Rpdj48L2Rpdj4KICAke0coInNhbGVzRmVlZCIs"
    "YDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5KdXN0IHNvbGQ8L2gzPjxwIGNsYXNzPSJzdWIiPkxpdmUg"
    "ZmVlZCDigJQgYWxzbyBhdXRvLXBvc3RlZCB0byB5b3VyIGNoYW5uZWwuPC9wPgogICAgJHtGRUVE"
    "Lmxlbmd0aD9GRUVELm1hcCgoW2YsYyxhLHddKT0+YDxkaXYgY2xhc3M9ImZlZWRsaW5lIj48c3Bh"
    "bj4ke2Z9PC9zcGFuPjxiPiR7Y308L2I+IHNvbGQgZm9yIDxiPiR7YX08L2I+PHNwYW4gc3R5bGU9"
    "Im1hcmdpbi1sZWZ0OmF1dG8iPiR7d308L3NwYW4+PC9kaXY+YCkuam9pbigiIikKICAgICAgOic8"
    "cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIHNhbGVzIHlldCB0b2RheS48L3A+J308"
    "L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJzZWMiIGlkPSJzaG9wbGlzdCI+PGgyPiR7Q01TLnNob3BI"
    "ZWFkfTwvaDI+PGEgZGF0YS1hY3Q9InNvcnQiPiR7Q01TLnNob3BTb3J0fTwvYT48L2Rpdj4KICA8"
    "ZGl2IGNsYXNzPSJtc2VhcmNoIj48ZGl2IGNsYXNzPSJmaWVsZHdyYXAiPiR7aWMoInNlYXJjaCIp"
    "fTxpbnB1dCBpZD0icSIgcGxhY2Vob2xkZXI9IiR7Q01TLnNlYXJjaFBofSI+PC9kaXY+CiAgICA8"
    "YnV0dG9uIGNsYXNzPSJidG4gc20gc29mdCIgZGF0YS1hY3Q9ImFsZXJ0cyI+JHtDTVMuYWxlcnRz"
    "QnRufTwvYnV0dG9uPjwvZGl2PgogIDxkaXYgY2xhc3M9ImNoaXByb3ciIGlkPSJjaGlwcyI+CiAg"
    "ICA8YnV0dG9uIGNsYXNzPSJvbiIgZGF0YS1maWx0ZXI9ImFsbCI+QWxsPC9idXR0b24+PGJ1dHRv"
    "biBkYXRhLWZpbHRlcj0ic3RvY2siPkluIHN0b2NrPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEt"
    "ZmlsdGVyPSJkZWFsIj5PbiBkZWFsPC9idXR0b24+PGJ1dHRvbiBkYXRhLWZpbHRlcj0iY2hlYXAi"
    "PlVuZGVyIOKCuTM1PC9idXR0b24+CiAgICAke0coInZhcmlhbnRzIixgPGJ1dHRvbiBkYXRhLWZp"
    "bHRlcj0idjJmYSI+Tm8gMkZBPC9idXR0b24+PGJ1dHRvbiBkYXRhLWZpbHRlcj0idm1haWwiPldp"
    "dGggbWFpbDwvYnV0dG9uPjxidXR0b24gZGF0YS1maWx0ZXI9InZzcGFtIj5TcGFtLWZyZWU8L2J1"
    "dHRvbj5gKX08L2Rpdj4KICA8ZGl2IGlkPSJzaG9wYm9keSI+JHtzaG9wQm9keSgpfTwvZGl2PmA7"
    "Cn0KZnVuY3Rpb24gc2hvcEJvZHkoKXtyZXR1cm4gYCR7RklMVEVSRUQubGVuZ3RoP2A8ZGl2IGNs"
    "YXNzPSJncmlkIj4ke0ZJTFRFUkVELm1hcChzaG9wQ2FyZCkuam9pbigiIil9PC9kaXY+YDpFTVBU"
    "WX1gO30KZnVuY3Rpb24gcHJpY2VCaXRzKHMpewogIHJldHVybiBgJHtzLndhcz9gPHM+JHttb25l"
    "eShzLndhcyl9PC9zPmA6IiJ9JHttb25leShzLnByaWNlKX1gOwp9CmZ1bmN0aW9uIHNob3BSb3co"
    "cyl7Y29uc3QgaT1TVE9DSy5pbmRleE9mKHMpLG9mZj1zLnF0eT09PTA7CiAgcmV0dXJuIGA8ZGl2"
    "IGNsYXNzPSJyb3ciIGRhdGEtYnV5PSIke2l9Ij48ZGl2IGNsYXNzPSJmbGFnIj4ke3MuZn08L2Rp"
    "dj4KICAgIDxkaXYgY2xhc3M9Im1pZCI+PGI+JHtlc2Mocy5jKX0gJHtzLmNvZGV9PC9iPgogICAg"
    "ICA8c21hbGw+JHtvZmY/Ik91dCBvZiBzdG9jayDigJQgZ2V0IGFuIGFsZXJ0IjpzLnF0eSsiIGlu"
    "IHN0b2NrIMK3IGluc3RhbnQifTwvc21hbGw+CiAgICAgICR7RygicmV2aWV3cyIsYDxkaXYgc3R5"
    "bGU9Im1hcmdpbi10b3A6M3B4Ij4ke3N0YXJzKHMuYyl9PC9kaXY+YCl9CiAgICAgICR7RygidmFy"
    "aWFudHMiLGA8ZGl2IHN0eWxlPSJtYXJnaW4tdG9wOjNweDtkaXNwbGF5OmZsZXg7Z2FwOjRweDtm"
    "bGV4LXdyYXA6d3JhcCI+CiAgICAgICAgPHNwYW4gY2xhc3M9InBpbGwiPk5vIDJGQTwvc3Bhbj4k"
    "e3MuaG90Pyc8c3BhbiBjbGFzcz0icGlsbCI+V2l0aCBtYWlsPC9zcGFuPic6IiJ9PC9kaXY+YCl9"
    "PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJydCI+PGRpdiBjbGFzcz0icCI+JHtwcmljZUJpdHMocyl9"
    "PC9kaXY+CiAgICAgICR7RygiZHluUHJpY2UiLGA8c21hbGwgc3R5bGU9ImRpc3BsYXk6YmxvY2s7"
    "Zm9udC1zaXplOi42cmVtO2NvbG9yOnZhcigtLXR4MikiPmxpdmUgcHJpY2U8L3NtYWxsPmApfQog"
    "ICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjVweDtqdXN0aWZ5LWNvbnRlbnQ6Zmxl"
    "eC1lbmQ7bWFyZ2luLXRvcDo2cHgiPgogICAgICAgICR7Rygid2lzaGxpc3QiLGA8YnV0dG9uIGNs"
    "YXNzPSJidG4geHMgc29mdCIgZGF0YS13aXNoPSIke2l9Ij4ke1dJU0guaW5jbHVkZXMoaSk/IuKZ"
    "pSI6IuKZoSJ9PC9idXR0b24+YCl9CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIj4ke29m"
    "Zj8iTm90aWZ5IE1lIjoiQnV5In08L2J1dHRvbj48L2Rpdj48L2Rpdj48L2Rpdj5gO30KZnVuY3Rp"
    "b24gc2hvcENhcmQocyl7Y29uc3QgaT1TVE9DSy5pbmRleE9mKHMpLG9mZj1zLnF0eT09PTA7CiAg"
    "cmV0dXJuIGA8ZGl2IGNsYXNzPSJwY2FyZCIgZGF0YS1idXk9IiR7aX0iIHN0eWxlPSJwb3NpdGlv"
    "bjpyZWxhdGl2ZSI+CiAgICAke0coIndpc2hsaXN0IixgPHNwYW4gY2xhc3M9ImhlYXJ0ICR7V0lT"
    "SC5pbmNsdWRlcyhpKT8ib24iOiIifSIgZGF0YS13aXNoPSIke2l9Ij4ke2ljKCJzdGFyIil9PC9z"
    "cGFuPmApfQogICAgPGRpdiBjbGFzcz0idG9wIj48ZGl2IGNsYXNzPSJmbGFnIj4ke3MuZn08L2Rp"
    "dj4KICAgICAgPHNwYW4gY2xhc3M9InBpbGwgJHtvZmY/Im9mZiI6Im9uIn0iPiR7b2ZmPyJTb2xk"
    "IG91dCI6cy5xdHkrIiBsZWZ0In08L3NwYW4+PC9kaXY+CiAgICA8ZGl2PjxkaXYgY2xhc3M9Im5t"
    "Ij4ke2VzYyhzLmMpfTwvZGl2PjxkaXYgY2xhc3M9Im1ldGEiPiR7cy5jb2RlfSDCtyBpbnN0YW50"
    "PC9kaXY+CiAgICAgICR7RygicmV2aWV3cyIsYDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6NHB4Ij4k"
    "e3N0YXJzKHMuYyl9PC9kaXY+YCl9PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJwcmljZSI+JHtwcmlj"
    "ZUJpdHMocyl9PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2sgc20iPiR7b2ZmPyJO"
    "b3RpZnkgTWUiOiJCdXkgTm93In08L2J1dHRvbj48L2Rpdj5gO30KCmZ1bmN0aW9uIHZPcmRlcnMo"
    "KXsKICByZXR1cm4gYDxkaXYgY2xhc3M9InNlYyI+PGgyPllvdXIgT3JkZXJzPC9oMj48YSBkYXRh"
    "LWFjdD0iY3N2Ij5FeHBvcnQ8L2E+PC9kaXY+CiAgJHtPUkRFUlMubWFwKChvLG9pKT0+YDxkaXYg"
    "Y2xhc3M9ImNhcmQiIGRhdGEtb3JkZXItaWQ9IiR7by5pZH0iPgogICAgPGRpdiBzdHlsZT0iZGlz"
    "cGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTJweCI+CiAgICAgIDxkaXYgY2xhc3M9"
    "ImZsYWciPiR7by5mfTwvZGl2PgogICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAi"
    "PjxiIHN0eWxlPSJmb250LXNpemU6Ljg1cmVtO2Rpc3BsYXk6YmxvY2siPiR7by5udW19PC9iPgog"
    "ICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjdyZW0iPiR7"
    "by53aGVufTwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYgc3R5bGU9InRleHQtYWxpZ246cmlnaHQi"
    "PjxzcGFuIGNsYXNzPSJwaWxsICR7by5zdD09PSJEZWxpdmVyZWQiPyJvbiI6Im9mZiJ9Ij4ke28u"
    "c3R9PC9zcGFuPjwvZGl2PjwvZGl2PgogICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9"
    "Im1hcmdpbjoxMHB4IDAgMDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKSI+CiAgICAgIDxkaXYgY2xh"
    "c3M9Imt2Ij48c3Bhbj5QaG9uZSBudW1iZXI8L3NwYW4+CiAgICAgICAgPGI+PGNvZGUgc3R5bGU9"
    "ImZvbnQtc2l6ZTouOHJlbTt1c2VyLXNlbGVjdDphbGwiPiR7ZXNjKG8ucGhvbmV8fCLigJQiKX08"
    "L2NvZGU+CiAgICAgICAgJHtvLnBob25lP2A8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0"
    "YS1jb3B5PSJ0ZXh0IiBkYXRhLWNvcHktdmFsdWU9IiR7ZXNjKG8ucGhvbmUpfSI+Q29weTwvYnV0"
    "dG9uPmA6IiJ9PC9iPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+MkZBIHBhc3N3"
    "b3JkPC9zcGFuPgogICAgICAgIDxiPjxjb2RlIHN0eWxlPSJmb250LXNpemU6LjhyZW07dXNlci1z"
    "ZWxlY3Q6YWxsIj4ke2VzYyhvLnR3b2ZhfHwibm9uZSIpfTwvY29kZT4KICAgICAgICAke28udHdv"
    "ZmE/YDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWNvcHk9InRleHQiIGRhdGEtY29w"
    "eS12YWx1ZT0iJHtlc2Moby50d29mYSl9Ij5Db3B5PC9idXR0b24+YDoiIn08L2I+PC9kaXY+CiAg"
    "ICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5MYXN0IGxvZ2luIGNvZGU8L3NwYW4+CiAgICAgICAg"
    "PGI+PGNvZGUgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbTt1c2VyLXNlbGVjdDphbGwiPiR7ZXNjKG8u"
    "b3RwfHwibm90IHlldCIpfTwvY29kZT4KICAgICAgICAke28ub3RwP2A8YnV0dG9uIGNsYXNzPSJi"
    "dG4geHMgc29mdCIgZGF0YS1jb3B5PSJ0ZXh0IiBkYXRhLWNvcHktdmFsdWU9IiR7ZXNjKG8ub3Rw"
    "KX0iPkNvcHk8L2J1dHRvbj5gOiIifTwvYj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPlN0YXR1czwvc3Bhbj48Yj4ke28ubG9naW49PT0iaW4iCiAgICAgICAgPyAnPHNwYW4gY2xh"
    "c3M9InBpbGwgb24iPkxvZ2dlZCBpbiDCtyB5b3VycyBvbmx5PC9zcGFuPicKICAgICAgICA6IChv"
    "LndpbmRvdz4wCiAgICAgICAgICAgID8gJzxzcGFuIGNsYXNzPSJwaWxsIj5Db2RlcyBmb3J3YXJk"
    "aW5nIMK3ICcrby53aW5kb3crJyBtaW4gbGVmdDwvc3Bhbj4nCiAgICAgICAgICAgIDogJzxzcGFu"
    "IGNsYXNzPSJwaWxsIG9mZiI+SGFuZG92ZXIgY29tcGxldGU8L3NwYW4+Jyl9PC9iPjwvZGl2Pgog"
    "ICAgPC9kaXY+CiAgICAke0coInRpbWVsaW5lIixgPGRpdiBjbGFzcz0idGwiPgogICAgICA8ZGl2"
    "IGNsYXNzPSJvayI+PGI+UGFpZDwvYj48c21hbGw+JHtvLndoZW59PC9zbWFsbD48L2Rpdj4KICAg"
    "ICAgPGRpdiBjbGFzcz0ib2siPjxiPkRlbGl2ZXJlZDwvYj48c21hbGw+U2Vzc2lvbiBoYW5kZWQg"
    "b3ZlciBpbnN0YW50bHk8L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSIke28uc3Q9PT0i"
    "RGVsaXZlcmVkIj8ib2siOiIifSI+PGI+T1RQIHJlY2VpdmVkPC9iPjxzbWFsbD4ke28uc3Q9PT0i"
    "RGVsaXZlcmVkIj8iQ29kZSBmb3J3YXJkZWQgaW4tYXBwIjoiV2FpdGluZyJ9PC9zbWFsbD48L2Rp"
    "dj4KICAgICAgPGRpdj48Yj5DbG9zZWQ8L2I+PHNtYWxsPkF1dG8tY2xvc2VzIGFmdGVyIHRoZSB3"
    "YXJyYW50eSB3aW5kb3c8L3NtYWxsPjwvZGl2PjwvZGl2PmApfQogICAgPGRpdiBjbGFzcz0iYnRu"
    "cm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMXB4Ij4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNt"
    "IiBkYXRhLW90cD0iJHtvLm51bX0iPkdldCBPVFA8L2J1dHRvbj4KICAgICAgJHtHKCJyZWRvd25s"
    "b2FkIixgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIHNvZnQiIGRhdGEtb3JkPSJkZXRhaWxzIj5EZXRh"
    "aWxzPC9idXR0b24+YCl9PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjdweCI+CiAgICAgICR7Rygic2Vzc2lvbkZpbGUiLGA8YnV0dG9uIGNsYXNzPSJidG4g"
    "c20gc29mdCIgZGF0YS1vcmQ9ImZpbGUiPiR7aWMoImNvcHkiKX0gRmlsZTwvYnV0dG9uPmApfQog"
    "ICAgICAke0coInJlY2VpcHRzIixgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIHNvZnQiIGRhdGEtb3Jk"
    "PSJyZWNlaXB0Ij5SZWNlaXB0PC9idXR0b24+YCl9CiAgICAgICR7KG8ubG9naW49PT0iaW4iKT8i"
    "IjpgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBkYXRhLWxvZ2dlZGluPSIke28uaWR9Ij5JJ20gbG9n"
    "Z2VkIGluPC9idXR0b24+YH0KICAgICAgJHtHKCJ3YXJyYW50eSIsYDxidXR0b24gY2xhc3M9ImJ0"
    "biBzbSBnaG9zdCIgZGF0YS1jbGFpbS1vcmRlcj0iJHtvLmlkfSI+UmVwb3J0IGlzc3VlPC9idXR0"
    "b24+YCl9PC9kaXY+CiAgICAke0coInJldmlld3MiLGA8ZGl2IGNsYXNzPSJrdiIgc3R5bGU9Im1h"
    "cmdpbi10b3A6OXB4Ij48c3Bhbj5SYXRlIHRoaXMgb3JkZXI8L3NwYW4+CiAgICAgIDxiPjxidXR0"
    "b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLXJhdGU9IiR7by5jfSI+UmF0ZTwvYnV0dG9uPjwv"
    "Yj48L2Rpdj5gKX0KICA8L2Rpdj5gKS5qb2luKCIiKX0KCiAgPGRpdiBjbGFzcz0iY2FyZCIgc3R5"
    "bGU9InRleHQtYWxpZ246Y2VudGVyIj48aDM+TmVlZCBoZWxwIHdpdGggYW4gb3JkZXI/PC9oMz4K"
    "ICAgIDxwIGNsYXNzPSJzdWIiPiR7RkVBVC5hdXRvUmVmdW5kPyJGYWlsZWQgbG9naW5zIGFyZSBy"
    "ZWZ1bmRlZCBhdXRvbWF0aWNhbGx5IHdpdGhpbiAxMCBtaW51dGVzLiI6IkNvbnRhY3Qgc3VwcG9y"
    "dCBhbmQgd2Ugd2lsbCBsb29rIGludG8gaXQuIn08L3A+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ci"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLWFj"
    "dD0ic3VwcG9ydCI+U3VwcG9ydDwvYnV0dG9uPgogICAgJHtHKCJkaXNwdXRlIixgPGJ1dHRvbiBj"
    "bGFzcz0iYnRuIGdob3N0IiBkYXRhLWRpc3B1dGU+RGlzcHV0ZTwvYnV0dG9uPmApfTwvZGl2Pjwv"
    "ZGl2PmA7Cn0KCmZ1bmN0aW9uIHZFYXJuKCl7CiAgcmV0dXJuIGAke0coInN0cmVhayIsYDxkaXYg"
    "Y2xhc3M9ImNhcmQiPgogICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNl"
    "bnRlcjtnYXA6MTJweCI+CiAgICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoInphcCIpfTwvZGl2"
    "PjxkaXYgc3R5bGU9ImZsZXg6MSI+PGgzPkRhaWx5IENoZWNrLWluPC9oMz48cCBjbGFzcz0ic3Vi"
    "Ij5EYXkgJHtFQVJOLnN0cmVha30gc3RyZWFrIMK3ICR7bW9uZXkoRUFSTi5kYWlseSl9IHdhaXRp"
    "bmc8L3A+PC9kaXY+PC9kaXY+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjZweDtt"
    "YXJnaW46MTRweCAwIj4KICAgICAgJHtbMSwyLDMsNCw1LDYsN10ubWFwKGQ9PmA8ZGl2IHN0eWxl"
    "PSJmbGV4OjE7aGVpZ2h0OjM0cHg7Ym9yZGVyLXJhZGl1czo5cHg7ZGlzcGxheTpncmlkO3BsYWNl"
    "LWl0ZW1zOmNlbnRlcjtmb250LXNpemU6LjY4cmVtO2ZvbnQtd2VpZ2h0OjcwMDtib3JkZXI6MXB4"
    "IHNvbGlkIHZhcigtLWxpbmUpOyR7ZDw9KEVBUk4uc3RyZWFrfHwwKT8iYmFja2dyb3VuZDp2YXIo"
    "LS1waWxsZ3JhZCk7Y29sb3I6IzA0MjQxYztib3JkZXItY29sb3I6dHJhbnNwYXJlbnQiOiJiYWNr"
    "Z3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIpIn0iPiR7ZH08L2Rpdj5gKS5qb2lu"
    "KCIiKX08L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEgYmxvY2siIGRhdGEtY2xhaW0g"
    "JHtFQVJOLmNsYWltZWQ/ImRpc2FibGVkIjoiIn0+JHtFQVJOLmNsYWltZWQ/IkNsYWltZWQgdG9k"
    "YXkiOiJDbGFpbSAiK21vbmV5KEVBUk4uZGFpbHkpfTwvYnV0dG9uPjwvZGl2PmApfQogICR7Rygi"
    "bG95YWx0eSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Mb3lhbHR5IFBvaW50czwvaDM+PHAgY2xh"
    "c3M9InN1YiI+MSBwb2ludCBwZXIg4oK5MTAgc3BlbnQuIDEwMCBwb2ludHMgPSDigrkxMCBiYWxh"
    "bmNlLjwvcD4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5CYWxhbmNlPC9zcGFuPjxiPiR7RUFS"
    "Ti5wb2ludHN9IHB0czwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Xb3J0aDwv"
    "c3Bhbj48Yj4ke21vbmV5KEVBUk4ucG9pbnRzV29ydGgpfTwvYj48L2Rpdj4KICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBibG9jayIgaWQ9InJlZGVlbWdvIiAke0VBUk4ucG9pbnRzPjA/IiI6ImRpc2Fi"
    "bGVkIn0+UmVkZWVtIFBvaW50czwvYnV0dG9uPjwvZGl2PmApfQogICR7YW55RigicmVmRGFzaCIs"
    "InJlZkwyIik/YDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5JbnZpdGUgJmFtcDsgRWFybjwvaDM+CiAg"
    "ICA8cCBjbGFzcz0ic3ViIj4ke0ZFQVQucmVmTDI/IjUlIGZyb20gcGVvcGxlIHlvdSBpbnZpdGUs"
    "IHBsdXMgMSUgZnJvbSB0aGUgcGVvcGxlIHRoZXkgaW52aXRlLiI6IjElIG9mIGV2ZXJ5IGRlcG9z"
    "aXQgeW91ciBpbnZpdGVzIGV2ZXIgbWFrZS4ifTwvcD4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bh"
    "bj5JbnZpdGVzPC9zcGFuPjxiPiR7RUFSTi5pbnZpdGVzfTwvYj48L2Rpdj4KICAgICR7RygicmVm"
    "TDIiLGA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+TGV2ZWwgMSBlYXJuaW5nczwvc3Bhbj48Yj4ke21v"
    "bmV5KEVBUk4ubDEpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5MZXZlbCAy"
    "IGVhcm5pbmdzPC9zcGFuPjxiPiR7bW9uZXkoRUFSTi5sMil9PC9iPjwvZGl2PmApfQogICAgPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPlRvdGFsIGVhcm5lZDwvc3Bhbj48Yj4ke21vbmV5KEVBUk4uZWFy"
    "bmVkKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+WW91ciBsaW5rPC9zcGFu"
    "PjxiIHN0eWxlPSJmb250LXNpemU6LjcycmVtIj4ke2VzYyhFQVJOLmxpbmspfTwvYj48L2Rpdj4K"
    "ICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGJ1dHRvbiBj"
    "bGFzcz0iYnRuIiBkYXRhLWNvcHk9InJlZiI+JHtpYygiY29weSIpfSBDb3B5IExpbms8L2J1dHRv"
    "bj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLWNvcHk9InJlZiI+U2hhcmU8L2J1"
    "dHRvbj48L2Rpdj4KICAgICR7RygicmVmRGFzaCIsYDxkaXYgY2xhc3M9InNlYyIgc3R5bGU9Im1h"
    "cmdpbi10b3A6NnB4Ij48aDIgc3R5bGU9ImZvbnQtc2l6ZTouNzhyZW0iPlJlY2VudCBpbnZpdGVz"
    "PC9oMj48L2Rpdj4KICAgICR7RUFSTi5yZWNlbnQubGVuZ3RoP0VBUk4ucmVjZW50Lm1hcCgoW3Us"
    "YSxlXSk9PmA8ZGl2IGNsYXNzPSJmZWVkbGluZSI+PGI+JHtlc2ModSl9PC9iPiAke2VzYyhhKX08"
    "c3BhbiBzdHlsZT0ibWFyZ2luLWxlZnQ6YXV0bztjb2xvcjp2YXIoLS1vaykiPiR7ZXNjKGUpfTwv"
    "c3Bhbj48L2Rpdj5gKS5qb2luKCIiKQogICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJn"
    "aW46MCI+Tm9ib2R5IGhhcyBqb2luZWQgdGhyb3VnaCB5b3VyIGxpbmsgeWV0LjwvcD4nfQogICAg"
    "PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjoxMXB4IDAgMCI+UmVmZXJyYWwgZWFybmluZ3Mg"
    "bGFuZCBpbiB5b3VyIGJhbGFuY2UgYXV0b21hdGljYWxseSDigJQgbm90aGluZyB0byBjbGFpbS48"
    "L3A+YCl9CiAgPC9kaXY+YDoiIn0KICAke0coImxlYWRlcmJvYXJkIixgPGRpdiBjbGFzcz0iY2Fy"
    "ZCI+PGgzPlRoaXMgd2VlaydzIHRvcCBidXllcnM8L2gzPjxwIGNsYXNzPSJzdWIiPk51bWJlciBv"
    "bmUgd2lucyDigrk1MDAgb24gU3VuZGF5LjwvcD4KICAgICR7RUFSTi5sZWFkZXJzLmxlbmd0aD9F"
    "QVJOLmxlYWRlcnMubWFwKChbcix1LHZdLGkpPT5gPGRpdiBjbGFzcz0ibGIiPjxzcGFuIGNsYXNz"
    "PSJyayAke2k9PT0wPyJnIjoiIn0iPiR7ZXNjKHIpfTwvc3Bhbj4KICAgICAgICA8ZGl2IHN0eWxl"
    "PSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjhyZW0iPiR7ZXNjKHUp"
    "fTwvYj48L2Rpdj48YiBzdHlsZT0iZm9udC1zaXplOi44cmVtIj4ke2VzYyh2KX08L2I+PC9kaXY+"
    "YCkuam9pbigiIikKICAgICAgOic8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIHB1"
    "cmNoYXNlcyB5ZXQgdGhpcyB3ZWVrLjwvcD4nfTwvZGl2PmApfQogICR7Rygid2luYmFjayIsYDxk"
    "aXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2Vu"
    "dGVyO2dhcDoxMHB4Ij4KICAgIDxzcGFuIGNsYXNzPSJwaWxsIGhvdCI+JHtpYygiZmlyZSIpfSBD"
    "b21lYmFjazwvc3Bhbj4KICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNp"
    "emU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPjEwJSBvZmYg4oCUIGV4cGlyZXMgaW4gNDggaG91"
    "cnM8L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyIgaWQ9Im9mZmVydXNlIj5Vc2U8L2J1"
    "dHRvbj48L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5UYXNrczwvaDI+PC9kaXY+CiAg"
    "JHtFQVJOLnRhc2tzLm1hcCgoW3Qscixkb25lXSk9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBz"
    "dHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTFweCI+CiAgICAgIDxk"
    "aXYgY2xhc3M9ImZsYWciPiR7ZG9uZT9pYygiY2hlY2siKTppYygiemFwIil9PC9kaXY+CiAgICAg"
    "IDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODJy"
    "ZW0iPiR7dH08L2I+PGJyPjxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6"
    "LjdyZW0iPlJld2FyZCAke3J9PC9zbWFsbD48L2Rpdj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IHhzICR7ZG9uZT8ic29mdCI6IiJ9IiAke2RvbmU/ImRpc2FibGVkIjoiIn0gZGF0YS1hY3Q9InRh"
    "c2siPiR7ZG9uZT8iRG9uZSI6IlN0YXJ0In08L2J1dHRvbj48L2Rpdj5gKS5qb2luKCIiKX1gOwp9"
    "CgpmdW5jdGlvbiB2V2FsbGV0KCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJoZXJvIiBzdHlsZT0i"
    "dGV4dC1hbGlnbjpjZW50ZXIiPgogICAgPGRpdiBjbGFzcz0ia2ljayI+JHtDTVMud2FsS2lja308"
    "L2Rpdj4KICAgIDxoMSBzdHlsZT0iZm9udC1zaXplOjIuM3JlbSI+4oK5PHNwYW4gaWQ9ImJhbDIi"
    "IGNsYXNzPSJ0aWNrIj4ke0JBTC50b0ZpeGVkKDIpfTwvc3Bhbj48L2gxPgogICAgPHAgc3R5bGU9"
    "Im1hcmdpbjowIGF1dG8gMTRweCI+JHtDTVMud2FsU3VifTwvcD4KICAgIDxkaXYgc3R5bGU9Im1h"
    "cmdpbjowIGF1dG87bWF4LXdpZHRoOjMwMHB4Ij4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0"
    "YSBibG9jayIgZGF0YS1kZXA+JHtpYygicGx1cyIpfSAke0NNUy53YWxBZGR9PC9idXR0b24+CiAg"
    "ICAgICR7RygiY3J5cHRvIixgPGJ1dHRvbiBjbGFzcz0iYnRuIGdsYXNzIGJsb2NrIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDo4cHgiIGRhdGEtY3J5cHRvPlBheSB3aXRoIENyeXB0bzwvYnV0dG9uPmApfTwv"
    "ZGl2PjwvZGl2PgogIDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7"
    "YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48"
    "aSBjbGFzcz0iZG90Ij48L2k+QXV0by12ZXJpZnkgT048L3NwYW4+CiAgICA8ZGl2IHN0eWxlPSJm"
    "bGV4OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj5Ub3At"
    "dXBzIGFyZSBjcmVkaXRlZCBhcyBzb29uIGFzIHRoZXkgYXJyaXZlPC9kaXY+CiAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1hY3Q9InN0YXR1cyI+U3RhdHVzPC9idXR0b24+PC9k"
    "aXY+CiAgJHtHKCJkZXBCb251cyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Ub3AtdXAgYm9udXM8"
    "L2gzPjxwIGNsYXNzPSJzdWIiPkV4dHJhIGNyZWRpdCBhZGRlZCBhdXRvbWF0aWNhbGx5LjwvcD4K"
    "ICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4O21hcmdpbi10b3A6MTBweCI+CiAg"
    "ICAgICR7W1si4oK5NTAwIiwiKzUlIl0sWyLigrkxMDAwIiwiKzglIl0sWyLigrkyMDAwIiwiKzEy"
    "JSJdXS5tYXAoKFthLGJdKT0+YDxkaXYgc3R5bGU9ImZsZXg6MTt0ZXh0LWFsaWduOmNlbnRlcjtw"
    "YWRkaW5nOjlweCA0cHg7Ym9yZGVyLXJhZGl1czoxMnB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0t"
    "bGluZSk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMikiPgogICAgICAgIDxiIHN0eWxlPSJmb250LXNp"
    "emU6LjhyZW07ZGlzcGxheTpibG9jayI+JHthfTwvYj48c21hbGwgc3R5bGU9ImZvbnQtc2l6ZTou"
    "NjhyZW07Y29sb3I6dmFyKC0tb2spIj4ke2J9PC9zbWFsbD48L2Rpdj5gKS5qb2luKCIiKX08L2Rp"
    "dj48L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5RdWljayBUb3AtdXA8L2gyPjwvZGl2"
    "PgogIDxkaXYgY2xhc3M9ImdyaWQiIHN0eWxlPSJncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0"
    "KDMsMWZyKSI+CiAgICAke1s1MCwxMDAsMjAwLDUwMCwxMDAwLDIwMDBdLm1hcChhPT5gPGJ1dHRv"
    "biBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtYW10PSIke2F9Ij7igrkke2F9PC9idXR0b24+YCkuam9p"
    "bigiIil9PC9kaXY+CiAgJHtHKCJkZXBMaW1pdHMiLGA8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFy"
    "Z2luOjlweCAwIDA7Zm9udC1zaXplOi43cmVtIj5NaW5pbXVtIOKCuTEwIMK3IG1heGltdW0g4oK5"
    "MjAsMDAwIHBlciB0b3AtdXAuPC9wPmApfQogIDxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEycHgiPjxoMz4ke0NNUy5wYXlUaXRsZX08L2gzPgogICAgPHAgY2xhc3M9InN1YiI+"
    "JHtDTVMucGF5U3VifTwvcD4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5VUEkgSUQ8L3NwYW4+"
    "PGI+JHtlc2MoU1RPUkUudXBpfHwibm90IHNldCB5ZXQiKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNs"
    "YXNzPSJrdiI+PHNwYW4+UGF5ZWU8L3NwYW4+PGI+JHtlc2MoU1RPUkUucGF5ZWV8fCLigJQiKX08"
    "L2I+PC9kaXY+CiAgICAke0coInVwaVJvdGF0ZSIsYDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5IYW5k"
    "bGVzIGluIHJvdGF0aW9uPC9zcGFuPjxiPjMgYWN0aXZlPC9iPjwvZGl2PmApfQogICAgPGRpdiBj"
    "bGFzcz0ia3YiPjxzcGFuPlBheWVlPC9zcGFuPjxiPllvdXIgU3RvcmUgTmFtZTwvYj48L2Rpdj4K"
    "ICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5BdXRvLXZlcmlmeTwvc3Bhbj48YiBzdHlsZT0iY29s"
    "b3I6dmFyKC0tb2spIj5FbmFibGVkPC9iPjwvZGl2PgogICAgJHtHKCJ3cm9uZ0FtdCIsYDxkaXYg"
    "Y2xhc3M9Imt2Ij48c3Bhbj5Xcm9uZyBhbW91bnQ8L3NwYW4+PGI+Q3JlZGl0ZWQgYXMgcGFpZDwv"
    "Yj48L2Rpdj5gKX0KICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayBzb2Z0IiBzdHlsZT0ibWFy"
    "Z2luLXRvcDoxMnB4IiBkYXRhLWNvcHk9InVwaSI+JHtpYygiY29weSIpfSBDb3B5IFVQSSBJRDwv"
    "YnV0dG9uPjwvZGl2PgogICR7RygiYWJhbmRvblJlbWluZCIsYDxwIGNsYXNzPSJzdWIiIHN0eWxl"
    "PSJtYXJnaW46MTBweCAwIDA7Zm9udC1zaXplOi43cmVtIj5XZSB3aWxsIHJlbWluZCB5b3UgYmVm"
    "b3JlIGFuIHVuZmluaXNoZWQgcGF5bWVudCBleHBpcmVzLjwvcD5gKX0KICA8ZGl2IGNsYXNzPSJz"
    "ZWMiPjxoMj4ke0ZFQVQubGVkZ2VyPyJTdGF0ZW1lbnQiOiJIaXN0b3J5In08L2gyPiR7RygibGVk"
    "Z2VyIixgPGEgZGF0YS1hZG1pbi1hY3Q9ImV4cG9ydCI+RXhwb3J0PC9hPmApfTwvZGl2PgogICR7"
    "VFgubWFwKCh0LGkpPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6Zmxl"
    "eDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjExcHgiPgogICAgPGRpdiBjbGFzcz0iZmxhZyI+JHt0"
    "LmFbMF09PT0iKyI/aWMoImRvd24iKTppYygidXAiKX08L2Rpdj4KICAgIDxkaXYgc3R5bGU9ImZs"
    "ZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODJyZW0iPiR7dC50fTwvYj48"
    "YnI+PHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouN3JlbSI+JHt0Lnd9"
    "PC9zbWFsbD48L2Rpdj4KICAgIDxkaXYgc3R5bGU9InRleHQtYWxpZ246cmlnaHQiPjxiIHN0eWxl"
    "PSJmb250LXNpemU6Ljg1cmVtO2NvbG9yOiR7dC5hWzBdPT09IisiPyJ2YXIoLS1vaykiOiJ2YXIo"
    "LS10eCkifSI+JHt0LmF9PC9iPgogICAgJHtHKCJsZWRnZXIiLGA8YnI+PHNtYWxsIHN0eWxlPSJm"
    "b250LXNpemU6LjY1cmVtO2NvbG9yOnZhcigtLXR4MikiPmJhbCDigrkkeygyNDguNStpKjcpLnRv"
    "Rml4ZWQoMil9PC9zbWFsbD5gKX08L2Rpdj48L2Rpdj5gKS5qb2luKCIiKX1gOwp9CgpmdW5jdGlv"
    "biB2WW91KCl7CiAgY29uc3QgbWU9TUV8fHt9OwogIGNvbnN0IHNwZW50PW1lLnNwZW50fHwwLCB0"
    "YXJnZXQ9c3BlbnQ+PTUwMD8yMDAwOjUwMDsKICBjb25zdCBwY3Q9TWF0aC5tYXgoNCxNYXRoLm1p"
    "bigxMDAsTWF0aC5yb3VuZChzcGVudC90YXJnZXQqMTAwKSkpOwogIGNvbnN0IGF2PW1lLnBob3Rv"
    "P2A8aW1nIHNyYz0iJHttZS5waG90b30iIGFsdD0iIiBzdHlsZT0id2lkdGg6NjRweDtoZWlnaHQ6"
    "NjRweDtib3JkZXItcmFkaXVzOjUwJTtvYmplY3QtZml0OmNvdmVyO2Rpc3BsYXk6YmxvY2s7bWFy"
    "Z2luOjAgYXV0byAxMHB4IiBvbmVycm9yPSJ0aGlzLnJlcGxhY2VXaXRoKE9iamVjdC5hc3NpZ24o"
    "ZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgnZGl2Jykse2NsYXNzTmFtZTonZmxhZycsc3R5bGU6J3dp"
    "ZHRoOjY0cHg7aGVpZ2h0OjY0cHg7bWFyZ2luOjAgYXV0byAxMHB4J30pKSI+YAogICAgOmA8ZGl2"
    "IGNsYXNzPSJmbGFnIiBzdHlsZT0id2lkdGg6NjRweDtoZWlnaHQ6NjRweDttYXJnaW46MCBhdXRv"
    "IDEwcHgiPiR7aWMoInVzZXIiKX08L2Rpdj5gOwogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCBz"
    "aGluZWNhcmQgdGlsdCIgZGF0YS10aWx0IHN0eWxlPSJ0ZXh0LWFsaWduOmNlbnRlciI+PHNwYW4g"
    "Y2xhc3M9ImdsYXJlIj48L3NwYW4+CiAgICAke2F2fQogICAgPGgzIHN0eWxlPSJmb250LXNpemU6"
    "MS4wNXJlbSI+JHtlc2MobWUubmFtZXx8IllvdSIpfTwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj4k"
    "e2VzYyhtZS51c2VybmFtZXx8KCJJRCAiKyhtZS5pZHx8IuKAlCIpKSl9JHttZS51c2VybmFtZT8i"
    "IMK3IElEICIrKG1lLmlkfHwi4oCUIik6IiJ9JHttZS5qb2luZWQ/IiDCtyBqb2luZWQgIitlc2Mo"
    "bWUuam9pbmVkKToiIn08L3A+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjdweDtq"
    "dXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO21hcmdpbi10b3A6MTBweDtmbGV4LXdyYXA6d3JhcCI+CiAg"
    "ICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj4ke2VzYyhtZS50aWVyfHwiQnJvbnplIil9PC9zcGFu"
    "PjxzcGFuIGNsYXNzPSJwaWxsIj4ke21lLm9yZGVyc3x8MH0gb3JkZXJzPC9zcGFuPgogICAgICA8"
    "c3BhbiBjbGFzcz0icGlsbCI+JHttb25leShtZS5iYWxhbmNlfHwwKX08L3NwYW4+CiAgICAgICR7"
    "RygibG95YWx0eSIsYDxzcGFuIGNsYXNzPSJwaWxsIj4ke21lLnBvaW50c3x8MH0gcHRzPC9zcGFu"
    "PmApfQogICAgICAke0coInN0cmVhayIsYDxzcGFuIGNsYXNzPSJwaWxsIj4ke21lLnN0cmVha3x8"
    "MH0tZGF5IHN0cmVhazwvc3Bhbj5gKX0KICAgICAgJHsoRkVBVC5idXllckJhZGdlJiYobWUub3Jk"
    "ZXJzfHwwKT49NSk/YDxzcGFuIGNsYXNzPSJwaWxsIG9uIj4ke2ljKCJjaGVjayIpfSBWZXJpZmll"
    "ZCBidXllcjwvc3Bhbj5gOiIifQogICAgICAkeyhtZS5kZWJ0PjApP2A8c3BhbiBjbGFzcz0icGls"
    "bCBob3QiPiR7bW9uZXkobWUuZGVidCl9IHVkaGFyPC9zcGFuPmA6IiJ9PC9kaXY+CiAgICA8ZGl2"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjE0cHg7aGVpZ2h0OjdweDtib3JkZXItcmFkaXVzOjlweDtiYWNr"
    "Z3JvdW5kOnZhcigtLWNhcmQyKTtvdmVyZmxvdzpoaWRkZW4iPgogICAgICA8aSBzdHlsZT0iZGlz"
    "cGxheTpibG9jaztoZWlnaHQ6MTAwJTt3aWR0aDoke3BjdH0lO2JhY2tncm91bmQ6dmFyKC0tcGls"
    "bGdyYWQpIj48L2k+PC9kaXY+CiAgICA8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luLXRvcDo3"
    "cHg7Zm9udC1zaXplOi43MnJlbSI+JHttZS5uZXh0X3RpZXI+MAogICAgICA/IG1vbmV5KG1lLm5l"
    "eHRfdGllcikrIiBtb3JlIHNwZW50IHRvIHJlYWNoICIrKHNwZW50Pj01MDA/IlBsYXRpbnVtIjoi"
    "R29sZCIpCiAgICAgIDogIlRvcCB0aWVyIHJlYWNoZWQifTwvcD48L2Rpdj4KICAke0coIndpc2hs"
    "aXN0IixgPGRpdiBjbGFzcz0ic2VjIj48aDI+U2F2ZWQgY291bnRyaWVzPC9oMj48L2Rpdj4KICAg"
    "IDxkaXYgY2xhc3M9ImNhcmQiPiR7V0lTSC5sZW5ndGg/V0lTSC5tYXAoaT0+YDxkaXYgY2xhc3M9"
    "Imt2Ij48c3Bhbj4ke1NUT0NLW2ldLmZ9ICR7ZXNjKFNUT0NLW2ldLmMpfSAke1NUT0NLW2ldLmNv"
    "ZGV9PC9zcGFuPgogICAgICA8Yj48YnV0dG9uIGNsYXNzPSJidG4geHMiIGRhdGEtYnV5PSIke2l9"
    "Ij5CdXk8L2J1dHRvbj48L2I+PC9kaXY+YCkuam9pbigiIik6JzxwIGNsYXNzPSJzdWIiIHN0eWxl"
    "PSJtYXJnaW46MCI+VGFwIHRoZSBoZWFydCBvbiBhbnkgY291bnRyeSB0byBzYXZlIGl0IGhlcmUu"
    "PC9wPid9PC9kaXY+YCl9CiAgPGRpdiBjbGFzcz0ic2VjIj48aDI+QWNjb3VudDwvaDI+PC9kaXY+"
    "CiAgPGRpdiBjbGFzcz0iY2FyZCI+JHtbWyJPcmRlciBoaXN0b3J5Iiwib3JkZXJzIl0sWyJXYWxs"
    "ZXQgJiBwYXltZW50cyIsIndhbGxldCJdXQogICAgLmNvbmNhdChhbnlGKCJyZWZEYXNoIiwicmVm"
    "TDIiKT9bWyJSZWZlcnJhbCBwcm9ncmFtIiwiZWFybiJdXTpbXSkKICAgIC5tYXAoKFt0LGddKT0+"
    "YDxkaXYgY2xhc3M9Imt2IiBzdHlsZT0iY3Vyc29yOnBvaW50ZXIiIGRhdGEtZ289IiR7Z30iPjxz"
    "cGFuIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7dH08L3NwYW4+PGI+JHtpYygiY2hldiIpfTwv"
    "Yj48L2Rpdj5gKS5qb2luKCIiKX0KICAgICR7RygiaTE4biIsYDxkaXYgY2xhc3M9Imt2Ij48c3Bh"
    "biBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj5MYW5ndWFnZTwvc3Bhbj48Yj4KICAgICAgPHNlbGVj"
    "dCBjbGFzcz0iaW5wIiBzdHlsZT0iaGVpZ2h0OjMycHg7d2lkdGg6YXV0bztmb250LXNpemU6Ljc0"
    "cmVtIj48b3B0aW9uPkVuZ2xpc2g8L29wdGlvbj48b3B0aW9uPuCkueCkv+CkqOCljeCkpuClgDwv"
    "b3B0aW9uPjwvc2VsZWN0PjwvYj48L2Rpdj5gKX08L2Rpdj4KICAke0coIm5vdGlmUHJlZnMiLGA8"
    "ZGl2IGNsYXNzPSJzZWMiPjxoMj5Ob3RpZmljYXRpb25zPC9oMj48L2Rpdj4KICA8ZGl2IGNsYXNz"
    "PSJjYXJkIj4KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPlJlc3RvY2sgYWxl"
    "cnRzPHNtYWxsPkRNIG1lIHdoZW4gbXkgY291bnRyeSBpcyBiYWNrPC9zbWFsbD48L2Rpdj4KICAg"
    "ICAgPGRpdiBjbGFzcz0idGcgJHtQUkVGUy5yZXN0b2NrPyJvbiI6IiJ9IiBkYXRhLXByZWY9InJl"
    "c3RvY2siPjwvZGl2PjwvZGl2PgogICAgJHtHKCJyZWNlaXB0cyIsYDxkaXYgY2xhc3M9InN3YXAi"
    "PjxkaXYgY2xhc3M9InQiPk9yZGVyIHJlY2VpcHRzPHNtYWxsPlNlbmQgYSByZWNlaXB0IGFmdGVy"
    "IGVhY2ggYnV5PC9zbWFsbD48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0idGcgJHtQUkVGUy5yZWNl"
    "aXB0cz8ib24iOiIifSIgZGF0YS1wcmVmPSJyZWNlaXB0cyI+PC9kaXY+PC9kaXY+YCl9CiAgICA8"
    "ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5Qcm9tbyBtZXNzYWdlczxzbWFsbD5GbGFz"
    "aCBzYWxlcyBhbmQgZ2l2ZWF3YXlzPC9zbWFsbD48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0idGcg"
    "JHtQUkVGUy5wcm9tb3M/Im9uIjoiIn0iIGRhdGEtcHJlZj0icHJvbW9zIj48L2Rpdj48L2Rpdj4K"
    "ICAgICR7RygicHVzaE5vdGlmIixgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+TWlu"
    "aSBhcHAgcHVzaDxzbWFsbD5NZXNzYWdlIG1lIHdoZW4gc29tZXRoaW5nIGhhcHBlbnM8L3NtYWxs"
    "PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyAke1BSRUZTLnB1c2g/Im9uIjoiIn0iIGRhdGEt"
    "cHJlZj0icHVzaCI+PC9kaXY+PC9kaXY+YCl9CiAgPC9kaXY+YCl9CiAgPGRpdiBjbGFzcz0iYnRu"
    "cm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgaWQ9"
    "InN1cHBvcnRnbyI+U3VwcG9ydDwvYnV0dG9uPgogIDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCIg"
    "aWQ9InRlcm1zZ28iPlRlcm1zPC9idXR0b24+PC9kaXY+YDsKfQoKZnVuY3Rpb24gYmluZEJ1eSgp"
    "ewogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXdpc2hdIikuZm9yRWFjaChlbD0+"
    "ZWwub25jbGljaz1lPT57CiAgICBlLnN0b3BQcm9wYWdhdGlvbigpO2NvbnN0IGk9K2VsLmRhdGFz"
    "ZXQud2lzaDsKICAgIGNvbnN0IGs9V0lTSC5pbmRleE9mKGkpO2lmKGs8MClXSVNILnB1c2goaSk7"
    "ZWxzZSBXSVNILnNwbGljZShrLDEpOwogICAgdG9hc3QoazwwPyJTYXZlZCB0byB3aXNobGlzdCI6"
    "IlJlbW92ZWQgZnJvbSB3aXNobGlzdCIpO2FwcGx5RmlsdGVyKCk7fSk7CiAgZG9jdW1lbnQucXVl"
    "cnlTZWxlY3RvckFsbCgiW2RhdGEtYnV5XSIpLmZvckVhY2goZWw9PmVsLm9uY2xpY2s9ZT0+ewog"
    "ICAgaWYoZS50YXJnZXQuY2xvc2VzdCgiW2RhdGEtdG9hc3RdLFtkYXRhLXdpc2hdIikpcmV0dXJu"
    "O2NvbnN0IHM9U1RPQ0tbK2VsLmRhdGFzZXQuYnV5XTsKICAgIGlmKHMucXR5PT09MCYmIWZhbHNl"
    "KXt0b2FzdCgiQWxlcnQgc2V0IOKAlCB5b3UnbGwgZ2V0IGEgRE0gb24gcmVzdG9jayIpO3JldHVy"
    "bn0KICAgIGJ1eVNoZWV0KHMscy5xdHk9PT0wKTt9KTsKfQpsZXQgUVRZPTEsQ09VUE9OPTA7CmZ1"
    "bmN0aW9uIGJ1eVNoZWV0KHMscHJlKXsKICBRVFk9MTtDT1VQT049MDsKICBjb25zdCBtYXg9RkVB"
    "VC5jYXJ0fHxGRUFULmJ1bGtEaXNjP01hdGgubWF4KDEsTWF0aC5taW4oMjUscy5xdHl8fDUpKTox"
    "OwogIHNoZWV0KGA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dh"
    "cDoxMnB4O21hcmdpbi1ib3R0b206MTRweCI+CiAgICAgIDxkaXYgY2xhc3M9ImZsYWciIHN0eWxl"
    "PSJ3aWR0aDo1MHB4O2hlaWdodDo1MHB4O2ZvbnQtc2l6ZToxLjVyZW0iPiR7cy5mfTwvZGl2Pgog"
    "ICAgICA8ZGl2IHN0eWxlPSJmbGV4OjEiPjxoMyBzdHlsZT0ibWFyZ2luOjA7Zm9udC1zaXplOjFy"
    "ZW0iPiR7ZXNjKHMuYyl9IEFjY291bnQ8L2gzPgogICAgICA8cCBjbGFzcz0ic3ViIiBzdHlsZT0i"
    "bWFyZ2luOjAiPiR7cy5jb2RlfSDCtyAke3MucXR5KyIgaW4gc3RvY2sgwrcgaW5zdGFudCJ9PC9w"
    "PgogICAgICAke0coInJldmlld3MiLGA8ZGl2IHN0eWxlPSJtYXJnaW4tdG9wOjRweCI+JHtzdGFy"
    "cyhzLmMpfTwvZGl2PmApfTwvZGl2PjwvZGl2PgogICAgJHtHKCJoZWFsdGhDaGVjayIsYDxkaXYg"
    "Y2xhc3M9Imt2Ij48c3Bhbj5TZXNzaW9uIGhlYWx0aDwvc3Bhbj48YiBzdHlsZT0iY29sb3I6dmFy"
    "KC0tb2spIj4ke2ljKCJjaGVjayIpfSBjaGVja2VkIGxpdmU8L2I+PC9kaXY+YCl9CiAgICAke0co"
    "InZhcmlhbnRzIixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlZhcmlhbnQ8L3NwYW4+PGI+CiAgICAg"
    "IDxzZWxlY3QgY2xhc3M9ImlucCIgc3R5bGU9ImhlaWdodDozMnB4O3dpZHRoOmF1dG87Zm9udC1z"
    "aXplOi43NHJlbSI+PG9wdGlvbj5ObyAyRkE8L29wdGlvbj48b3B0aW9uPjJGQSBvbiAoK+KCuTUp"
    "PC9vcHRpb24+PG9wdGlvbj5XaXRoIG1haWwgKCvigrk4KTwvb3B0aW9uPjwvc2VsZWN0PjwvYj48"
    "L2Rpdj5gKX0KICAgICR7KEZFQVQuY2FydHx8RkVBVC5idWxrRGlzYyk/YDxkaXYgY2xhc3M9Imt2"
    "Ij48c3Bhbj5RdWFudGl0eTwvc3Bhbj48Yj4KICAgICAgPHNwYW4gY2xhc3M9InF0eSI+PGJ1dHRv"
    "biBpZD0icW0iPuKIkjwvYnV0dG9uPjxiIGlkPSJxbiI+MTwvYj48YnV0dG9uIGlkPSJxcCI+Kzwv"
    "YnV0dG9uPjwvc3Bhbj48L2I+PC9kaXY+YDoiIn0KICAgICR7RygiYnVsa0Rpc2MiLGA8cCBjbGFz"
    "cz0ic3ViIiBpZD0iYmRub3RlIiBzdHlsZT0ibWFyZ2luOjZweCAwIDA7Zm9udC1zaXplOi43cmVt"
    "Ij5CdXkgNSsgZm9yIDUlIG9mZiDCtyAxMCsgZm9yIDEwJSBvZmYgwrcgMjUrIGZvciAxNSUgb2Zm"
    "LjwvcD5gKX0KICAgICR7RygiY291cG9ucyIsYDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFy"
    "Z2luLXRvcDoxMHB4Ij48bGFiZWw+Q291cG9uIGNvZGU8L2xhYmVsPgogICAgICA8ZGl2IHN0eWxl"
    "PSJkaXNwbGF5OmZsZXg7Z2FwOjdweCI+PGlucHV0IGNsYXNzPSJpbnAiIGlkPSJjcG4iIHBsYWNl"
    "aG9sZGVyPSJXRUxDT01FMTAiPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20iIGlkPSJjcG5h"
    "cHBseSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iPkFwcGx5PC9idXR0b24+PC9kaXY+PC9kaXY+YCl9"
    "CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+UHJpY2U8L3NwYW4+PGIgaWQ9ImJwcmljZSI+JHtt"
    "b25leShzLnByaWNlKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+WW91ciBi"
    "YWxhbmNlPC9zcGFuPjxiPiR7bW9uZXkoQkFMKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4+QWZ0ZXIgcHVyY2hhc2U8L3NwYW4+PGIgaWQ9ImJhZnRlciI+JHttb25leShNYXRo"
    "Lm1heCgwLEJBTC1zLnByaWNlKSl9PC9iPjwvZGl2PgogICAgPGRpdiBpZD0ic3BsaXRub3RlIj48"
    "L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTRweCI+PGJ1"
    "dHRvbiBjbGFzcz0iYnRuIGdob3N0IiBkYXRhLWNsb3NlPkNhbmNlbDwvYnV0dG9uPgogICAgICA8"
    "YnV0dG9uIGNsYXNzPSJidG4gY3RhIiBpZD0iYmNvbmZpcm0iPkNvbmZpcm0gQnV5PC9idXR0b24+"
    "PC9kaXY+CiAgICAke0coImNhcnQiLGA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCBibG9jayBzbSIg"
    "c3R5bGU9Im1hcmdpbi10b3A6OHB4IiBpZD0iYmFkZGNhcnQiPkFkZCB0byBjYXJ0PC9idXR0b24+"
    "YCl9YCk7CiAgY29uc3QgZz1pZD0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoaWQpOwogIGZ1bmN0"
    "aW9uIHRvdGFsKCl7CiAgICBsZXQgdW5pdD1zLnByaWNlLCBxPVFUWSwgdD11bml0KnE7CiAgICBp"
    "ZihGRUFULmJ1bGtEaXNjKXtjb25zdCBkPXE+PTI1Py4xNTpxPj0xMD8uMTA6cT49NT8uMDU6MDt0"
    "PXQqKDEtZCk7fQogICAgaWYoRkVBVC5jb3Vwb25zJiZDT1VQT04pdD10KigxLUNPVVBPTi8xMDAp"
    "OwogICAgcmV0dXJuIE1hdGgucm91bmQodCoxMDApLzEwMDsKICB9CiAgZnVuY3Rpb24gcGFpbnQo"
    "KXsKICAgIGNvbnN0IHQ9dG90YWwoKTsKICAgIGcoImJwcmljZSIpLnRleHRDb250ZW50PW1vbmV5"
    "KHQpOwogICAgZygiYmFmdGVyIikudGV4dENvbnRlbnQ9bW9uZXkoTWF0aC5tYXgoMCxCQUwtdCkp"
    "OwogICAgY29uc3Qgc249Zygic3BsaXRub3RlIik7CiAgICBpZihGRUFULnNwbGl0UGF5JiZ0PkJB"
    "TCl7c24uaW5uZXJIVE1MPWA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+U3BsaXQgcGF5bWVudDwvc3Bh"
    "bj48Yj4ke21vbmV5KEJBTCl9IHdhbGxldCArICR7bW9uZXkodC1CQUwpfSBVUEk8L2I+PC9kaXY+"
    "YDt9CiAgICBlbHNlIHNuLmlubmVySFRNTD0iIjsKICAgIGlmKGcoInFuIikpZygicW4iKS50ZXh0"
    "Q29udGVudD1RVFk7CiAgfQogIGlmKGcoInFtIikpZygicW0iKS5vbmNsaWNrPSgpPT57UVRZPU1h"
    "dGgubWF4KDEsUVRZLTEpO3BhaW50KCk7fTsKICBpZihnKCJxcCIpKWcoInFwIikub25jbGljaz0o"
    "KT0+e1FUWT1NYXRoLm1pbihtYXgsUVRZKzEpO3BhaW50KCk7fTsKICBpZihnKCJjcG4iKSYmT0ZG"
    "RVImJiFnKCJjcG4iKS52YWx1ZSlnKCJjcG4iKS52YWx1ZT1PRkZFUjsKICBpZihnKCJjcG5hcHBs"
    "eSIpKWcoImNwbmFwcGx5Iikub25jbGljaz0oKT0+ewogICAgY29uc3Qgdj0oZygiY3BuIikudmFs"
    "dWV8fCIiKS50cmltKCkudG9VcHBlckNhc2UoKTsKICAgIGlmKCF2KXt0b2FzdCgiRW50ZXIgYSBj"
    "b3Vwb24gY29kZSIpO3JldHVybn0KICAgIENPVVBPTj12PT09IldFTENPTUUxMCI/MTA6dj09PSJT"
    "QVZFMjAiPzIwOjA7CiAgICB0b2FzdChDT1VQT04/Q09VUE9OKyIlIGNvdXBvbiBhcHBsaWVkIjoi"
    "Q291cG9uIG5vdCB2YWxpZCIpO3BhaW50KCk7fTsKICBpZihnKCJiYWRkY2FydCIpKWcoImJhZGRj"
    "YXJ0Iikub25jbGljaz1hc3luYygpPT57CiAgICBpZihMSVZFKXsKICAgICAgY29uc3QgZD1hd2Fp"
    "dCBhcGlUcnkoIi9hcGkvY2FydCIse2FjdGlvbjoiYWRkIixjb3VudHJ5OnMuYyxxdHk6UVRZfSk7"
    "CiAgICAgIGlmKCFkKXJldHVybjsKICAgICAgY2xvc2VTaGVldCgpO3RvYXN0KFFUWSsiIMOXICIr"
    "cy5jKyIgYWRkZWQgdG8gY2FydCIpO2F3YWl0IHJlZnJlc2goKTtyZXR1cm47CiAgICB9CiAgICBm"
    "b3IobGV0IGk9MDtpPFFUWTtpKyspQ0FSVC5wdXNoKHMpO2FwcGx5RmVhdHVyZXMoKTtjbG9zZVNo"
    "ZWV0KCk7dG9hc3QoUVRZKyIgw5cgIitzLmMrIiBhZGRlZCB0byBjYXJ0Iik7fTsKICBnKCJiY29u"
    "ZmlybSIpLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgdD10b3RhbCgpOwogICAgaWYoQkFM"
    "PHQmJiFGRUFULnNwbGl0UGF5KXt0b2FzdCgiTm90IGVub3VnaCBiYWxhbmNlIOKAlCBhZGQgZnVu"
    "ZHMiKTtyZXR1cm59CiAgICBpZihCQUw8dCYmRkVBVC5zcGxpdFBheSl7Y2xvc2VTaGVldCgpO3Rv"
    "YXN0KCJQYXlpbmcgIittb25leSh0LUJBTCkrIiBieSBVUEkiKTtkZXBvc2l0U2hlZXQoTWF0aC5j"
    "ZWlsKHQtQkFMKSk7cmV0dXJufQogICAgaWYoTElWRSl7CiAgICAgIGNvbnN0IGJ0bj1nKCJiY29u"
    "ZmlybSIpO2J0bi5kaXNhYmxlZD10cnVlO2J0bi50ZXh0Q29udGVudD0iV29ya2luZ+KApiI7CiAg"
    "ICAgIGNvbnN0IGNvZGU9ZygiY3BuIik/KGcoImNwbiIpLnZhbHVlfHwiIikudHJpbSgpOiIiOwog"
    "ICAgICBjb25zdCBkPWF3YWl0IGFwaVRyeSgiL2FwaS9idXkiLHtjb3VudHJ5OnMuYyxxdHk6UVRZ"
    "LGNvdXBvbjpjb2RlfSk7CiAgICAgIGJ0bi5kaXNhYmxlZD1mYWxzZTtidG4udGV4dENvbnRlbnQ9"
    "IkNvbmZpcm0gQnV5IjsKICAgICAgaWYoIWQpcmV0dXJuOwogICAgICBpZih0eXBlb2YgZC5iYWxh"
    "bmNlPT09Im51bWJlciIpQkFMPWQuYmFsYW5jZTsKICAgICAgc3luY0JhbCgpO2Nsb3NlU2hlZXQo"
    "KTtidXJzdCgpOwogICAgICBjb25zdCBmaXJzdD0oZC5vcmRlcnMmJmQub3JkZXJzWzBdKXx8bnVs"
    "bDsKICAgICAgdG9hc3QoZmlyc3Q/KCJEZWxpdmVyZWQg4oCUICIrZmlyc3QuaWQpOiJQdXJjaGFz"
    "ZWQg4oCUIGRldGFpbHMgaW4gT3JkZXJzIik7CiAgICAgIGF3YWl0IHJlZnJlc2goKTsKICAgICAg"
    "aWYoZmlyc3QmJmZpcnN0LnBheWxvYWQpc2V0VGltZW91dCgoKT0+c2hlZXQoYDxoMyBzdHlsZT0i"
    "bWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPiR7ZXNjKGZpcnN0LmlkKX08L2gzPgogICAg"
    "ICAgIDxwIGNsYXNzPSJzdWIiPktlZXAgdGhpcyBzYWZlLiBJdCBpcyBhbHNvIGluIE9yZGVycy48"
    "L3A+CiAgICAgICAgPGRpdiBjbGFzcz0ia3YiIHN0eWxlPSJkaXNwbGF5OmJsb2NrIj48Y29kZSBz"
    "dHlsZT0iZm9udC1zaXplOi43MnJlbTt3b3JkLWJyZWFrOmJyZWFrLWFsbCI+JHtlc2MoZmlyc3Qu"
    "cGF5bG9hZCl9PC9jb2RlPjwvZGl2PgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTBweCIgZGF0YS1jbG9zZT5Eb25lPC9idXR0b24+YCksMzUwKTsK"
    "ICAgICAgcmV0dXJuOwogICAgfQogICAgQkFMLT10O3N5bmNCYWwoKTtjbG9zZVNoZWV0KCk7YnVy"
    "c3QoKTsKICAgIHRvYXN0KCJQdXJjaGFzZWQg4oCUIE9UUCBpcyBpbiBPcmRlcnMiKTsKICAgIGlm"
    "KEZFQVQucHVzaE5vdGlmKXNldFRpbWVvdXQoKCk9PnRvYXN0KCJCb3Qgbm90aWZpY2F0aW9uIHNl"
    "bnQgdG8geW91ciBjaGF0IiksMTUwMCk7fTsKICBwYWludCgpOwp9CmZ1bmN0aW9uIGNhcnRTaGVl"
    "dCgpewogIGlmKCFDQVJULmxlbmd0aCl7dG9hc3QoIkNhcnQgaXMgZW1wdHkiKTtyZXR1cm59CiAg"
    "Y29uc3QgdD1DQVJULnJlZHVjZSgoYSxiKT0+YStiLnByaWNlLDApOwogIHNoZWV0KGA8aDMgc3R5"
    "bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5Zb3VyIGNhcnQ8L2gzPgogICAgPHAg"
    "Y2xhc3M9InN1YiI+JHtDQVJULmxlbmd0aH0gaXRlbSR7Q0FSVC5sZW5ndGg+MT8icyI6IiJ9IOKA"
    "lCBvbmUgY29uZmlybWF0aW9uLCBvbmUgZGViaXQuPC9wPgogICAgJHtDQVJULm1hcCgocyxpKT0+"
    "YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj4ke3MuZn0gJHtlc2Mocy5jKX0gJHtzLmNvZGV9PC9zcGFu"
    "PgogICAgICA8Yj4ke21vbmV5KHMucHJpY2UpfSA8YnV0dG9uIGNsYXNzPSJidG4geHMgZ2hvc3Qi"
    "IGRhdGEtY2RlbD0iJHtpfSI+w5c8L2J1dHRvbj48L2I+PC9kaXY+YCkuam9pbigiIil9CiAgICA8"
    "ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VG90YWw8L3NwYW4+PGI+JHttb25leSh0KX08L2I+PC9kaXY+"
    "CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjE0cHgiPjxidXR0b24g"
    "Y2xhc3M9ImJ0biBnaG9zdCIgZGF0YS1jbG9zZT5DbG9zZTwvYnV0dG9uPgogICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4gY3RhIiBpZD0iY2FydGJ1eSI+Q2hlY2tvdXQ8L2J1dHRvbj48L2Rpdj5gKTsK"
    "ICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1jZGVsXSIpLmZvckVhY2goYj0+Yi5v"
    "bmNsaWNrPSgpPT57Q0FSVC5zcGxpY2UoK2IuZGF0YXNldC5jZGVsLDEpO2FwcGx5RmVhdHVyZXMo"
    "KTtjYXJ0U2hlZXQoKTt9KTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2FydGJ1eSIpLm9u"
    "Y2xpY2s9KCk9PnsKICAgIGlmKEJBTDx0KXt0b2FzdCgiTm90IGVub3VnaCBiYWxhbmNlIOKAlCBh"
    "ZGQgZnVuZHMiKTtyZXR1cm59CiAgICBCQUwtPXQ7Q0FSVD1bXTtzeW5jQmFsKCk7YXBwbHlGZWF0"
    "dXJlcygpO2Nsb3NlU2hlZXQoKTtidXJzdCgpO3RvYXN0KCJPcmRlciBwbGFjZWQg4oCUIE9UUHMg"
    "YXJlIGluIE9yZGVycyIpO307Cn0KZnVuY3Rpb24gZ29UYWIodCl7CiAgY29uc3QgYj1kb2N1bWVu"
    "dC5xdWVyeVNlbGVjdG9yKGAjdGFicyBidXR0b25bZGF0YS10YWI9IiR7dH0iXWApOwogIGlmKGIp"
    "Yi5jbGljaygpOwp9CmZ1bmN0aW9uIGJpbmRDb21tb24oKXsKICBkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCJbZGF0YS1vcmRdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAg"
    "Y29uc3Qgd2hhdD1iLmRhdGFzZXQub3JkOwogICAgY29uc3QgY2FyZD1iLmNsb3Nlc3QoIltkYXRh"
    "LW9yZGVyLWlkXSIpOwogICAgY29uc3Qgb2lkPShjYXJkJiZjYXJkLmRhdGFzZXQub3JkZXJJZCl8"
    "fChPUkRFUlNbMF0mJk9SREVSU1swXS5pZCl8fCIiOwogICAgaWYoIW9pZCl7dG9hc3QoIk5vIG9y"
    "ZGVyIHNlbGVjdGVkIik7cmV0dXJuO30KICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5"
    "Iik7cmV0dXJuO30KICAgIGNvbnN0IGxibD1iLnRleHRDb250ZW50O2IuZGlzYWJsZWQ9dHJ1ZTti"
    "LnRleHRDb250ZW50PSLigKYiOwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvb3JkZXIi"
    "LHtpZDpvaWQsd2hhdDp3aGF0fSk7CiAgICBiLmRpc2FibGVkPWZhbHNlO2IudGV4dENvbnRlbnQ9"
    "bGJsOwogICAgaWYoIXIpcmV0dXJuOwogICAgaWYod2hhdD09PSJkZXRhaWxzIil0b2FzdCgiTG9n"
    "aW4gZGV0YWlscyBzZW50IHRvIHlvdXIgY2hhdCIpOwogICAgaWYod2hhdD09PSJmaWxlIil0b2Fz"
    "dCgiU2Vzc2lvbiBmaWxlIHNlbnQgdG8geW91ciBjaGF0Iik7CiAgICBpZih3aGF0PT09InJlY2Vp"
    "cHQiKXRvYXN0KCJSZWNlaXB0IHNlbnQgdG8geW91ciBjaGF0Iik7CiAgfSk7CiAgZG9jdW1lbnQu"
    "cXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtcmF0ZS1nb10iKS5mb3JFYWNoKGI9PmIub25jbGljaz1h"
    "c3luYygpPT57CiAgICBjb25zdCBzdGFycz0rYi5kYXRhc2V0LnJhdGVHbzsKICAgIGNvbnN0IGM9"
    "KHdpbmRvdy5fX3JhdGVDb3VudHJ5KXx8KChPUkRFUlNbMF18fHt9KS5jKXx8IiI7CiAgICBpZigh"
    "TElWRSl7Y2xvc2VTaGVldCgpO3RvYXN0KCJUaGFua3MgZm9yIHRoZSAiK3N0YXJzKyLimIUiKTty"
    "ZXR1cm47fQogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvcmV2aWV3Iix7Y291bnRyeTpj"
    "LHJhdGluZzpzdGFyc30pOwogICAgaWYoIXIpcmV0dXJuOwogICAgY2xvc2VTaGVldCgpO3RvYXN0"
    "KCJUaGFua3Mg4oCUICIrYysiIGlzIG5vdyAiK3IuYXZnKyLimIUiKTsKICAgIGF3YWl0IHJlZnJl"
    "c2goKTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1hY3RdIikuZm9y"
    "RWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3Qgd2hhdD1iLmRhdGFzZXQuYWN0"
    "OwogICAgaWYod2hhdD09PSJjaGFubmVsIil7CiAgICAgIGNvbnN0IGM9KEFETS5jaGFubmVscyYm"
    "QURNLmNoYW5uZWxzWzBdKXx8bnVsbDsKICAgICAgY29uc3QgaD1jP1N0cmluZyhjLmhhbmRsZXx8"
    "IiIpLnJlcGxhY2UoL15ALywiIik6IiI7CiAgICAgIGlmKGgpb3BlblBheShoLnN0YXJ0c1dpdGgo"
    "Imh0dHAiKT9oOigiaHR0cHM6Ly90Lm1lLyIraCkpOwogICAgICBlbHNlIHRvYXN0KCJObyBjaGFu"
    "bmVsIGhhcyBiZWVuIGFkZGVkIHlldCIpOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0"
    "PT09ImZsYXNoIil7CiAgICAgIGNvbnN0IG9uPUFETS5tYXJrZXQmJkFETS5tYXJrZXQuZmxhc2hf"
    "b247CiAgICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVt"
    "Ij5GbGFzaCBzYWxlPC9oMz4KICAgICAgICA8cCBjbGFzcz0ic3ViIj4ke29uPygiRXZlcnkgcHJp"
    "Y2UgaXMgIisoQURNLm1hcmtldC5mbGFzaHx8MCkrIiUgb2ZmIHJpZ2h0IG5vdy4iKToiTm8gc2Fs"
    "ZSBpcyBydW5uaW5nIGF0IHRoZSBtb21lbnQuIn08L3A+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0"
    "b24+YCk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9PT0ic29ydCIpewogICAgICBG"
    "SUxURVJFRD0oRklMVEVSRUQmJkZJTFRFUkVELmxlbmd0aD9GSUxURVJFRDpTVE9DSykuc2xpY2Uo"
    "KS5zb3J0KCh4LHkpPT4oeS5xdHl8fDApLSh4LnF0eXx8MCkpOwogICAgICByZW5kZXIoKTt0b2Fz"
    "dCgiU29ydGVkIGJ5IHdoYXQgaXMgaW4gc3RvY2siKTsKICAgICAgcmV0dXJuOwogICAgfQogICAg"
    "aWYod2hhdD09PSJhbGVydHMiKXsKICAgICAgY29uc3QgbWluZT0od2luZG93Ll9fYWxlcnRzKXx8"
    "W107CiAgICAgIGNvbnN0IG9mZj1TVE9DSy5maWx0ZXIoeD0+IXgucXR5KS5tYXAoeD0+eC5jKTsK"
    "ICAgICAgY29uc3QgbGlzdD0ob2ZmLmxlbmd0aD9vZmY6U1RPQ0subWFwKHg9PnguYykpLnNsaWNl"
    "KDAsOCk7CiAgICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZTox"
    "cmVtIj5SZXN0b2NrIGFsZXJ0czwvaDM+CiAgICAgICAgPHAgY2xhc3M9InN1YiI+V2UgbWVzc2Fn"
    "ZSB5b3UgdGhlIG1vbWVudCBvbmUgb2YgdGhlc2UgaXMgYmFjay48L3A+CiAgICAgICAgJHtsaXN0"
    "Lm1hcChjPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7ZXNjKGMpfTwvc3Bhbj4KICAgICAgICAg"
    "IDxiPjxidXR0b24gY2xhc3M9ImJ0biB4cyAke21pbmUuaW5kZXhPZihjKT49MD8iIjoic29mdCJ9"
    "IiBkYXRhLWFsZXJ0PSIke2VzYyhjKX0iPgogICAgICAgICAgICAke21pbmUuaW5kZXhPZihjKT49"
    "MD8iT24iOiJOb3RpZnkgbWUifTwvYnV0dG9uPjwvYj48L2Rpdj5gKS5qb2luKCIiKX0KICAgICAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEt"
    "Y2xvc2U+RG9uZTwvYnV0dG9uPmApOwogICAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJb"
    "ZGF0YS1hbGVydF0iKS5mb3JFYWNoKGE9PmEub25jbGljaz1hc3luYygpPT57CiAgICAgICAgaWYo"
    "IUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgICAgIGNvbnN0IHI9YXdh"
    "aXQgYXBpVHJ5KCIvYXBpL2FsZXJ0Iix7Y291bnRyeTphLmRhdGFzZXQuYWxlcnR9KTsKICAgICAg"
    "ICBpZighcilyZXR1cm47CiAgICAgICAgd2luZG93Ll9fYWxlcnRzPXIuYWxlcnRzfHxbXTsKICAg"
    "ICAgICBhLnRleHRDb250ZW50PXIub24/Ik9uIjoiTm90aWZ5IG1lIjsKICAgICAgICBhLmNsYXNz"
    "TGlzdC50b2dnbGUoInNvZnQiLCFyLm9uKTsKICAgICAgICB0b2FzdChyLm9uPygiV2Ugd2lsbCB0"
    "ZWxsIHlvdSB3aGVuICIrYS5kYXRhc2V0LmFsZXJ0KyIgaXMgYmFjayIpOiJBbGVydCByZW1vdmVk"
    "Iik7CiAgICAgIH0pOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09ImNzdiIpewog"
    "ICAgICBjb25zdCByb3dzPVtbIm9yZGVyIiwiY291bnRyeSIsInByaWNlIiwic3RhdHVzIiwid2hl"
    "biJdXQogICAgICAgIC5jb25jYXQoT1JERVJTLm1hcChvPT5bby5pZCxvLmMsby5wcmljZSxvLnN0"
    "LG8ud2hlbl0pKTsKICAgICAgY29uc3QgY3N2PXJvd3MubWFwKHI9PnIuam9pbigiLCIpKS5qb2lu"
    "KCJcbiIpOwogICAgICBjb25zdCBhPWRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoImEiKTsKICAgICAg"
    "YS5ocmVmPSJkYXRhOnRleHQvY3N2O2NoYXJzZXQ9dXRmLTgsIitlbmNvZGVVUklDb21wb25lbnQo"
    "Y3N2KTsKICAgICAgYS5kb3dubG9hZD0ibXktb3JkZXJzLmNzdiI7ZG9jdW1lbnQuYm9keS5hcHBl"
    "bmRDaGlsZChhKTthLmNsaWNrKCk7YS5yZW1vdmUoKTsKICAgICAgdG9hc3QoT1JERVJTLmxlbmd0"
    "aCsiIG9yZGVycyBkb3dubG9hZGVkIik7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9"
    "PT0ic3VwcG9ydCIpewogICAgICBjb25zdCBoPShTVE9SRS5zdXBwb3J0fHwiIikucmVwbGFjZSgv"
    "XkAvLCIiKTsKICAgICAgaWYoaClvcGVuUGF5KCJodHRwczovL3QubWUvIitoKTsKICAgICAgZWxz"
    "ZSB0b2FzdCgiU2VuZCB5b3VyIHF1ZXN0aW9uIGluIHRoZSBjaGF0IGFuZCB3ZSB3aWxsIHJlcGx5"
    "Iik7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9PT0idGFzayIpewogICAgICBjb25z"
    "dCByb3c9Yi5jbG9zZXN0KCIuY2FyZCIpOwogICAgICBjb25zdCBsYWJlbD1yb3c/KHJvdy5xdWVy"
    "eVNlbGVjdG9yKCJiIil8fHt9KS50ZXh0Q29udGVudHx8IiI6IiI7CiAgICAgIGlmKC9jaGFubmVs"
    "L2kudGVzdChsYWJlbCkpe2IuYmx1cigpO2RvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoJ1tkYXRhLWFj"
    "dD0iY2hhbm5lbCJdJyk/LmNsaWNrKCk7cmV0dXJuO30KICAgICAgaWYoL29yZGVyL2kudGVzdChs"
    "YWJlbCkpe2dvVGFiKCJzaG9wIik7dG9hc3QoIlBpY2sgYSBjb3VudHJ5IHRvIHBsYWNlIHlvdXIg"
    "Zmlyc3Qgb3JkZXIiKTtyZXR1cm47fQogICAgICBnb1RhYigiZWFybiIpO3RvYXN0KCJTaGFyZSB5"
    "b3VyIGludml0ZSBsaW5rIHRvIGZpbmlzaCB0aGlzIik7CiAgICAgIHJldHVybjsKICAgIH0KICAg"
    "IGlmKHdoYXQ9PT0ic3RhdHVzIil7CiAgICAgIGNvbnN0IGc9KEFETS5wYXkmJkFETS5wYXkuZ21h"
    "aWxfc2V0KTsKICAgICAgY29uc3QgcGVuZD0oQURNLnBheSYmQURNLnBheS5wZW5kaW5nKXx8MDsK"
    "ICAgICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPlBh"
    "eW1lbnQgY2hlY2tpbmc8L2gzPgogICAgICAgIDxwIGNsYXNzPSJzdWIiPiR7Zz8iQXV0b21hdGlj"
    "IGNoZWNraW5nIGlzIG9uLiBQYXksIHRoZW4gdGFwIFZlcmlmeS4iOgogICAgICAgICAgIkF1dG9t"
    "YXRpYyBjaGVja2luZyBpcyBub3Qgc2V0IHVwLCBzbyBhbiBhZG1pbiBjb25maXJtcyBwYXltZW50"
    "cyBieSBoYW5kLiJ9PC9wPgogICAgICAgICR7cGVuZD9gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPldh"
    "aXRpbmcgZm9yIGFuIGFkbWluPC9zcGFuPjxiPiR7cGVuZH08L2I+PC9kaXY+YDoiIn0KICAgICAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEt"
    "Y2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09"
    "PSJjb3B5Y29kZSIpewogICAgICBjb25zdCBlbD1iLmNsb3Nlc3QoIi5jYXJkLC5zaGVldGJvZHks"
    "I3NoZWV0Iil8fGRvY3VtZW50OwogICAgICBjb25zdCBjb2RlPShlbC5xdWVyeVNlbGVjdG9yKCJj"
    "b2RlIil8fHt9KS50ZXh0Q29udGVudHx8IiI7CiAgICAgIGNvcHlUZXh0KGNvZGUudHJpbSgpLCJD"
    "b2RlIik7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9PT0icmVwbGFjZSIpewogICAg"
    "ICBjb25zdCBjYXJkPWIuY2xvc2VzdCgiW2RhdGEtb3JkZXItaWRdIik7CiAgICAgIGNvbnN0IG9p"
    "ZD0oY2FyZCYmY2FyZC5kYXRhc2V0Lm9yZGVySWQpfHwoT1JERVJTWzBdJiZPUkRFUlNbMF0uaWQp"
    "fHwiIjsKICAgICAgaWYoIUxJVkUpe2Nsb3NlU2hlZXQoKTt0b2FzdCgiUmVwbGFjZW1lbnQgc2Vu"
    "dCIpO3JldHVybjt9CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL29yZGVyIix7aWQ6"
    "b2lkLHdoYXQ6InJlcGxhY2UifSk7CiAgICAgIGlmKCFyKXJldHVybjsKICAgICAgY2xvc2VTaGVl"
    "dCgpOwogICAgICB0b2FzdChyLnJlZnVuZGVkPyJObyBzdG9jayBsZWZ0IOKAlCByZWZ1bmRlZCB0"
    "byB5b3VyIHdhbGxldCI6KCJSZXBsYWNlZCDigJQgIityLm9yZGVyKSk7CiAgICAgIGF3YWl0IHJl"
    "ZnJlc2goKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJjcnlwdG8iKXsKICAg"
    "ICAgaWYoIUxJVkUpe2Nsb3NlU2hlZXQoKTt0b2FzdCgiV2Ugd2lsbCBjb25maXJtIHlvdXIgdHJh"
    "bnNmZXIgc2hvcnRseSIpO3JldHVybjt9CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBp"
    "L2NyeXB0byIse3JlZjoiIn0pOwogICAgICBpZihyKXtjbG9zZVNoZWV0KCk7dG9hc3QoIldlIHdp"
    "bGwgY29uZmlybSB5b3VyIHRyYW5zZmVyIHNob3J0bHkiKTt9CiAgICAgIHJldHVybjsKICAgIH0K"
    "ICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1jb3B5XSIpLmZvckVhY2go"
    "Yj0+Yi5vbmNsaWNrPSgpPT57CiAgICBjb25zdCB3aGF0PWIuZGF0YXNldC5jb3B5OwogICAgaWYo"
    "d2hhdD09PSJ1cGkiKXJldHVybiBjb3B5VGV4dChTVE9SRS51cGksIlVQSSBJRCIpOwogICAgaWYo"
    "d2hhdD09PSJyZWYiKXsKICAgICAgbGV0IGw9RUFSTi5saW5rfHwiIjsKICAgICAgaWYobCYmIS9e"
    "aHR0cHM/Oi8udGVzdChsKSlsPSJodHRwczovLyIrbDsKICAgICAgcmV0dXJuIGNvcHlUZXh0KGws"
    "Ikludml0ZSBsaW5rIik7CiAgICB9CiAgICByZXR1cm4gY29weVRleHQoYi5kYXRhc2V0LmNvcHlW"
    "YWx1ZXx8Yi50ZXh0Q29udGVudC50cmltKCksIkNvcGllZCIpOwogIH0pOwogIGNvbnN0IHJkPWRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJyZWRlZW1nbyIpOwogIGlmKHJkKXJkLm9uY2xpY2s9YXN5"
    "bmMoKT0+ewogICAgaWYoIUxJVkUpe3RvYXN0KCJSZWRlZW1lZCIpO3JldHVybjt9CiAgICByZC5k"
    "aXNhYmxlZD10cnVlOwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvcmVkZWVtIix7fSk7"
    "CiAgICByZC5kaXNhYmxlZD1mYWxzZTsKICAgIGlmKCFyKXJldHVybjsKICAgIEJBTD1yLmJhbGFu"
    "Y2U7c3luY0JhbCgpO2J1cnN0KCk7CiAgICB0b2FzdChyLnBvaW50cysiIHBvaW50cyDihpIgIitt"
    "b25leShyLmFtb3VudCkpOwogICAgYXdhaXQgcmVmcmVzaCgpOwogIH07CiAgY29uc3Qgb3U9ZG9j"
    "dW1lbnQuZ2V0RWxlbWVudEJ5SWQoIm9mZmVydXNlIik7CiAgaWYob3Upb3Uub25jbGljaz0oKT0+"
    "ewogICAgY29uc3QgYz0oQURNLmNvdXBvbnMmJkFETS5jb3Vwb25zLmxlbmd0aCk/QURNLmNvdXBv"
    "bnNbMF0uY29kZToiIjsKICAgIGlmKGMpe09GRkVSPWM7Y29weVRleHQoYywiQ291cG9uIik7fQog"
    "ICAgZWxzZSB0b2FzdCgiTm8gY291cG9uIGlzIHJ1bm5pbmcgcmlnaHQgbm93Iik7CiAgfTsKICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1wcmVmXSIpLmZvckVhY2godD0+dC5vbmNs"
    "aWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGtleT10LmRhdGFzZXQucHJlZiwgb249IXQuY2xhc3NM"
    "aXN0LmNvbnRhaW5zKCJvbiIpOwogICAgdC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIsb24pO1BSRUZT"
    "W2tleV09b247CiAgICBpZihMSVZFKXsKICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkv"
    "cHJlZnMiLHtrZXk6a2V5LG9uOm9ufSk7CiAgICAgIGlmKCFyKXt0LmNsYXNzTGlzdC50b2dnbGUo"
    "Im9uIiwhb24pO1BSRUZTW2tleV09IW9uO3JldHVybjt9CiAgICB9CiAgICB0b2FzdChvbj8iVHVy"
    "bmVkIG9uIjoiVHVybmVkIG9mZiIpOwogIH0pOwogIGNvbnN0IHRnYj1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgidGVybXNnbyIpOwogIGlmKHRnYil0Z2Iub25jbGljaz0oKT0+c2hlZXQoYDxoMyBz"
    "dHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPlRlcm1zICYgcmVmdW5kczwvaDM+"
    "CiAgICA8cCBjbGFzcz0ic3ViIiBzdHlsZT0id2hpdGUtc3BhY2U6cHJlLXdyYXAiPiR7ZXNjKFNU"
    "T1JFLnRlcm1zfHxDTVMudGVybXN8fCJObyB0ZXJtcyBoYXZlIGJlZW4gd3JpdHRlbiB5ZXQuIil9"
    "PC9wPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4"
    "IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+YCk7CiAgY29uc3Qgc2c9ZG9jdW1lbnQuZ2V0RWxl"
    "bWVudEJ5SWQoInN1cHBvcnRnbyIpOwogIGlmKHNnKXNnLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0"
    "IGg9KFNUT1JFLnN1cHBvcnR8fCIiKS5yZXBsYWNlKC9eQC8sIiIpOwogICAgaWYoaClvcGVuUGF5"
    "KCJodHRwczovL3QubWUvIitoKTsKICAgIGVsc2Ugc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAg"
    "MCA0cHg7Zm9udC1zaXplOjFyZW0iPlN1cHBvcnQ8L2gzPgogICAgICA8cCBjbGFzcz0ic3ViIj5T"
    "ZW5kIHlvdXIgcXVlc3Rpb24gaW4gdGhlIGNoYXQgYW5kIHdlIHdpbGwgcmVwbHkuPC9wPgogICAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEt"
    "Y2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTsKICB9Owp9CmZ1bmN0aW9uIGJpbmRTdGFnZSgpewogIGJp"
    "bmRDb21tb24oKTsKICBiaW5kQnV5KCk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2Rh"
    "dGEtc2Nyb2xsXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT5kb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZChiLmRhdGFzZXQuc2Nyb2xsKS5zY3JvbGxJbnRvVmlldyh7YmVoYXZpb3I6InNtb290aCJ9"
    "KSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtb3RwXSIpLmZvckVhY2goYj0+"
    "Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGNhcmQ9Yi5jbG9zZXN0KCJbZGF0YS1vcmRl"
    "ci1pZF0iKTsKICAgIGNvbnN0IG9pZD0oY2FyZCYmY2FyZC5kYXRhc2V0Lm9yZGVySWQpfHxiLmRh"
    "dGFzZXQub3RwfHwiIjsKICAgIGNvbnN0IG9yZD1PUkRFUlMuZmluZCh4PT54LmlkPT09b2lkKXx8"
    "e307CiAgICBpZighTElWRSl7CiAgICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4"
    "O2ZvbnQtc2l6ZToxcmVtIj5Mb2dpbiBjb2RlPC9oMz4KICAgICAgICA8cCBjbGFzcz0ic3ViIj5T"
    "YW1wbGUgY29kZSDigJQgdGhpcyBpcyB0aGUgcHJldmlldy48L3A+CiAgICAgICAgPGRpdiBjbGFz"
    "cz0ib3RwYm94Ij40IDIgOSAxIDc8L2Rpdj4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxv"
    "Y2siIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiIGRhdGEtY2xvc2U+RG9uZTwvYnV0dG9uPmApOwog"
    "ICAgICByZXR1cm47CiAgICB9CiAgICBjb25zdCBsYmw9Yi50ZXh0Q29udGVudDtiLmRpc2FibGVk"
    "PXRydWU7Yi50ZXh0Q29udGVudD0iRmV0Y2hpbmfigKYiOwogICAgY29uc3Qgcj1hd2FpdCBhcGlU"
    "cnkoIi9hcGkvb3JkZXIiLHtpZDpvaWQsd2hhdDoib3RwIn0pOwogICAgYi5kaXNhYmxlZD1mYWxz"
    "ZTtiLnRleHRDb250ZW50PWxibDsKICAgIGlmKCFyKXJldHVybjsKICAgIHNoZWV0KGA8aDMgc3R5"
    "bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5Mb2dpbiBjb2RlPC9oMz4KICAgICAg"
    "PHAgY2xhc3M9InN1YiI+JHtvcmQucGhvbmU/KCJGb3IgIitlc2Mob3JkLnBob25lKSk6IkZvciB0"
    "aGlzIGFjY291bnQifTwvcD4KICAgICAgPGRpdiBjbGFzcz0ib3RwYm94Ij4ke1N0cmluZyhyLmNv"
    "ZGUpLnNwbGl0KCIiKS5qb2luKCIgIil9PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bh"
    "bj5Db2RlcyBsZWZ0PC9zcGFuPjxiPiR7ci5sZWZ0fTwvYj48L2Rpdj4KICAgICAgJHtyLndpbmRv"
    "dz9gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkZvcndhcmRpbmcgZW5kcyBpbjwvc3Bhbj48Yj4ke3Iu"
    "d2luZG93fSBtaW48L2I+PC9kaXY+YDoiIn0KICAgICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMHB4Ij4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0"
    "YS1hY3Q9ImNvcHljb2RlIj5Db3B5IENvZGU8L2J1dHRvbj4KICAgICAgICA8YnV0dG9uIGNsYXNz"
    "PSJidG4iIGRhdGEtY2xvc2U+RG9uZTwvYnV0dG9uPjwvZGl2PmApOwogIH0pOwogIGRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNsYWltXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFz"
    "eW5jKCk9PnsKICAgIGlmKGIuZGlzYWJsZWQpcmV0dXJuOwogICAgYi5kaXNhYmxlZD10cnVlOwog"
    "ICAgaWYoTElWRSl7CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2NoZWNraW4iLHt9"
    "KTsKICAgICAgaWYoIXIpe2IuZGlzYWJsZWQ9ZmFsc2U7cmV0dXJuO30KICAgICAgQkFMPXR5cGVv"
    "ZiByLmJhbGFuY2U9PT0ibnVtYmVyIj9yLmJhbGFuY2U6QkFMOwogICAgICBFQVJOLmNsYWltZWQ9"
    "dHJ1ZTtFQVJOLnN0cmVhaz1yLnN0cmVha3x8RUFSTi5zdHJlYWs7CiAgICAgIHN5bmNCYWwoKTti"
    "dXJzdCgpO3RvYXN0KG1vbmV5KHIuYW1vdW50KSsiIGFkZGVkIMK3IGRheSAiK3Iuc3RyZWFrKTsK"
    "ICAgICAgYi50ZXh0Q29udGVudD0iQ2xhaW1lZCB0b2RheSI7CiAgICAgIGF3YWl0IHJlZnJlc2go"
    "KTsKICAgICAgcmV0dXJuOwogICAgfQogICAgQkFMKz1FQVJOLmRhaWx5O3N5bmNCYWwoKTtidXJz"
    "dCgpO3RvYXN0KG1vbmV5KEVBUk4uZGFpbHkpKyIgYWRkZWQiKTsKICAgIGIudGV4dENvbnRlbnQ9"
    "IkNsYWltZWQgdG9kYXkiO30pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWFt"
    "dF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ZGVwb3NpdFNoZWV0KGIuZGF0YXNldC5hbXQp"
    "KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1kZXBdIikuZm9yRWFjaChiPT5i"
    "Lm9uY2xpY2s9KCk9PmFtb3VudFNoZWV0KCkpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IltkYXRhLXRnXSIpLmZvckVhY2godD0+dC5vbmNsaWNrPSgpPT50LmNsYXNzTGlzdC50b2dnbGUo"
    "Im9uIikpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXNldHNlbF0iKS5mb3JF"
    "YWNoKHNlbD0+c2VsLm9uY2hhbmdlPWFzeW5jKCk9PnsKICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJl"
    "dmlldyBvbmx5Iik7cmV0dXJuO30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWlu"
    "L3NldHRpbmciLHtrZXk6c2VsLmRhdGFzZXQuc2V0c2VsLHZhbHVlOnNlbC52YWx1ZX0pOwogICAg"
    "aWYocil0b2FzdCgiU2F2ZWQiKTsKICB9KTsKICBjb25zdCBxPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJxIik7CiAgaWYocSl7cS52YWx1ZT1RVUVSWTtxLm9uaW5wdXQ9KCk9PntRVUVSWT1xLnZh"
    "bHVlLnRvTG93ZXJDYXNlKCk7YXBwbHlGaWx0ZXIoKX07fQogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIiNjaGlwcyBidXR0b24iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+e0NISVA9Yi5k"
    "YXRhc2V0LmZpbHRlcjsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNjaGlwcyBidXR0"
    "b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnRvZ2dsZSgib24iLHg9PT1iKSk7YXBwbHlGaWx0"
    "ZXIoKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNsYWltLW9yZGVyXSIp"
    "LmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgICBjb25zdCBvaWQ9Yi5kYXRhc2V0LmNsYWlt"
    "T3JkZXI7CiAgICBjb25zdCBvcmQ9T1JERVJTLmZpbmQoeD0+eC5pZD09PW9pZCl8fHt9OwogICAg"
    "aWYob3JkLmNsYWltKXt0b2FzdCgiQSBjbGFpbSBmb3IgdGhpcyBvcmRlciBpcyBhbHJlYWR5IHdp"
    "dGggYW4gYWRtaW4iKTtyZXR1cm47fQogICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0"
    "cHg7Zm9udC1zaXplOjFyZW0iPlJlcG9ydCBhIHByb2JsZW08L2gzPgogICAgICA8cCBjbGFzcz0i"
    "c3ViIj5BbiBhZG1pbiByZWFkcyBldmVyeSByZXBvcnQgYW5kIGRlY2lkZXMuIE5vdGhpbmcgaXMg"
    "cmVmdW5kZWQgYXV0b21hdGljYWxseS48L3A+CiAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+V2hhdCBoYXBwZW5lZD88L2xhYmVsPgogICAgICAg"
    "IDxzZWxlY3QgY2xhc3M9ImlucCIgaWQ9ImNsciI+CiAgICAgICAgICA8b3B0aW9uPkxvZ2luIG5v"
    "dCB3b3JraW5nPC9vcHRpb24+PG9wdGlvbj5BY2NvdW50IGJhbm5lZDwvb3B0aW9uPgogICAgICAg"
    "ICAgPG9wdGlvbj5Db2RlIG5ldmVyIGFycml2ZWQ8L29wdGlvbj48b3B0aW9uPldyb25nIGNvdW50"
    "cnk8L29wdGlvbj4KICAgICAgICAgIDxvcHRpb24+U29tZXRoaW5nIGVsc2U8L29wdGlvbj48L3Nl"
    "bGVjdD48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5EZXRhaWxzPC9sYWJl"
    "bD4KICAgICAgICA8dGV4dGFyZWEgY2xhc3M9ImlucCIgaWQ9ImNsZCIgcm93cz0iMyIgcGxhY2Vo"
    "b2xkZXI9IlRlbGwgdXMgZXhhY3RseSB3aGF0IHlvdSBzYXciPjwvdGV4dGFyZWE+PC9kaXY+CiAg"
    "ICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEgYmxvY2siIGlkPSJjbGdvIj5TZW5kIHRvIGFuIGFk"
    "bWluPC9idXR0b24+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5"
    "bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNsb3NlPkNhbmNlbDwvYnV0dG9uPmApOwogICAgZG9j"
    "dW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNsZ28iKS5vbmNsaWNrPWFzeW5jKCk9PnsKICAgICAgY29u"
    "c3QgcmVhc29uPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjbHIiKS52YWx1ZTsKICAgICAgY29u"
    "c3QgZGV0YWlscz0oZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNsZCIpLnZhbHVlfHwiIikudHJp"
    "bSgpOwogICAgICBpZihkZXRhaWxzLmxlbmd0aDw1KXt0b2FzdCgiVGVsbCB1cyB3aGF0IHdlbnQg"
    "d3JvbmciKTtyZXR1cm47fQogICAgICBpZighTElWRSl7Y2xvc2VTaGVldCgpO3RvYXN0KCJTZW50"
    "Iik7cmV0dXJuO30KICAgICAgY29uc3QgYnRuPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjbGdv"
    "Iik7YnRuLmRpc2FibGVkPXRydWU7YnRuLnRleHRDb250ZW50PSJTZW5kaW5n4oCmIjsKICAgICAg"
    "Y29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvb3JkZXIiLHtpZDpvaWQsd2hhdDoiY2xhaW0iLHJl"
    "YXNvbjpyZWFzb24sZGV0YWlsczpkZXRhaWxzfSk7CiAgICAgIGJ0bi5kaXNhYmxlZD1mYWxzZTti"
    "dG4udGV4dENvbnRlbnQ9IlNlbmQgdG8gYW4gYWRtaW4iOwogICAgICBpZighcilyZXR1cm47CiAg"
    "ICAgIGNsb3NlU2hlZXQoKTt0b2FzdCgiQ2xhaW0gIyIrci5pZCsiIHNlbnQg4oCUIGFuIGFkbWlu"
    "IHdpbGwgcmVwbHkiKTsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgfTsKICB9KTsKICBkb2N1"
    "bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1sb2dnZWRpbl0iKS5mb3JFYWNoKGI9PmIub25j"
    "bGljaz1hc3luYygpPT57CiAgICBjb25zdCBvaWQ9Yi5kYXRhc2V0LmxvZ2dlZGluOwogICAgaWYo"
    "IUxJVkUpe3RvYXN0KCJNYXJrZWQgYXMgbG9nZ2VkIGluIik7cmV0dXJuO30KICAgIHNoZWV0KGA8"
    "aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5Db25maXJtIHlvdSBhcmUg"
    "aW48L2gzPgogICAgICA8cCBjbGFzcz0ic3ViIj5XZSB3aWxsIGRlbGV0ZSBvdXIgY29weSBvZiB0"
    "aGUgbG9naW4gc2Vzc2lvbiBzdHJhaWdodCBhd2F5LCBzbyB0aGUKICAgICAgYWNjb3VudCBiZWNv"
    "bWVzIHlvdXJzIGFsb25lLiBDb2RlcyB3aWxsIHN0b3AgYmVpbmcgZm9yd2FyZGVkLjwvcD4KICAg"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBibG9jayIgaWQ9Imxpb2siIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjEycHgiPlllcywgSSBhbSBsb2dnZWQgaW48L2J1dHRvbj4KICAgICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIGdob3N0IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+"
    "Tm90IHlldDwvYnV0dG9uPmApOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImxpb2siKS5v"
    "bmNsaWNrPWFzeW5jKCk9PnsKICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvb3JkZXIi"
    "LHtpZDpvaWQsd2hhdDoibG9nZ2VkaW4ifSk7CiAgICAgIGlmKCFyKXJldHVybjsKICAgICAgY2xv"
    "c2VTaGVldCgpO3RvYXN0KCJEb25lIOKAlCB0aGUgYWNjb3VudCBpcyB5b3VycyBhbG9uZSBub3ci"
    "KTsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNl"
    "bGVjdG9yQWxsKCJbZGF0YS1kaXNwdXRlXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAg"
    "ICBjb25zdCBvcmQ9Yi5kYXRhc2V0LmRpc3B1dGV8fCIiOwogICAgc2hlZXQoYDxoMyBzdHlsZT0i"
    "bWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPk9wZW4gYSBkaXNwdXRlPC9oMz4KICAgICA8"
    "cCBjbGFzcz0ic3ViIj4ke29yZD8oIk9yZGVyICIrZXNjKG9yZCkpOiJBbiBhZG1pbiByZXZpZXdz"
    "IGV2ZXJ5IGRpc3B1dGUuIn08L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEycHgiPjxsYWJlbD5SZWFzb248L2xhYmVsPgogICAgICAgPHNlbGVjdCBjbGFzcz0i"
    "aW5wIiBpZD0iZHNyIj48b3B0aW9uPk5vdCBkZWxpdmVyZWQ8L29wdGlvbj48b3B0aW9uPkFjY291"
    "bnQgZGVhZCBvbiBhcnJpdmFsPC9vcHRpb24+PG9wdGlvbj5DaGFyZ2VkIHR3aWNlPC9vcHRpb24+"
    "PG9wdGlvbj5PdGhlcjwvb3B0aW9uPjwvc2VsZWN0PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZp"
    "ZWxkIj48bGFiZWw+V2hhdCBoYXBwZW5lZD88L2xhYmVsPgogICAgICAgPHRleHRhcmVhIGNsYXNz"
    "PSJpbnAiIGlkPSJkc2QiIHJvd3M9IjMiIHBsYWNlaG9sZGVyPSJEZXNjcmliZSB0aGUgcHJvYmxl"
    "bSI+PC90ZXh0YXJlYT48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkV2aWRl"
    "bmNlIChvcHRpb25hbCk8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGlkPSJkc2Yi"
    "IHR5cGU9ImZpbGUiIGFjY2VwdD0iaW1hZ2UvKiI+CiAgICAgICA8c21hbGwgY2xhc3M9InN1YiIg"
    "aWQ9ImRzZm4iPjwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93Ij48YnV0dG9u"
    "IGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtY2xvc2U+Q2FuY2VsPC9idXR0b24+CiAgICAgICA8YnV0"
    "dG9uIGNsYXNzPSJidG4gY3RhIiBpZD0iZHNnbyI+U3VibWl0PC9idXR0b24+PC9kaXY+YCk7CiAg"
    "ICBjb25zdCBmPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJkc2YiKTsKICAgIGlmKGYpZi5vbmNo"
    "YW5nZT0oKT0+e2NvbnN0IG49KGYuZmlsZXMmJmYuZmlsZXNbMF0pP2YuZmlsZXNbMF0ubmFtZToi"
    "IjsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRzZm4iKS50ZXh0Q29udGVudD1uPygi"
    "QXR0YWNoZWQ6ICIrbik6IiI7fTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJkc2dvIiku"
    "b25jbGljaz1hc3luYygpPT57CiAgICAgIGNvbnN0IHJlYXNvbj1kb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgiZHNyIikudmFsdWU7CiAgICAgIGNvbnN0IGRldGFpbHM9KGRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJkc2QiKS52YWx1ZXx8IiIpLnRyaW0oKTsKICAgICAgaWYoIWRldGFpbHMpe3RvYXN0"
    "KCJEZXNjcmliZSB3aGF0IGhhcHBlbmVkIik7cmV0dXJuO30KICAgICAgY29uc3QgZmlsZT0oZiYm"
    "Zi5maWxlcyYmZi5maWxlc1swXSl8fG51bGw7CiAgICAgIGNvbnN0IGZuPWZpbGU/KCJzY3JlZW5z"
    "aG90OiAiK2ZpbGUubmFtZSk6IiI7CiAgICAgIGlmKCFMSVZFKXtjbG9zZVNoZWV0KCk7dG9hc3Qo"
    "IkRpc3B1dGUgb3BlbmVkIik7cmV0dXJuO30KICAgICAgY29uc3QgYnRuPWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJkc2dvIik7YnRuLmRpc2FibGVkPXRydWU7YnRuLnRleHRDb250ZW50PSJTZW5k"
    "aW5n4oCmIjsKICAgICAgbGV0IHNob3Q9IiI7CiAgICAgIGlmKGZpbGUpewogICAgICAgIGlmKGZp"
    "bGUuc2l6ZT44KjEwMjQqMTAyNCl7dG9hc3QoIlRoYXQgaW1hZ2UgaXMgdG9vIGxhcmdlICg4IE1C"
    "IG1heCkiKTsKICAgICAgICAgIGJ0bi5kaXNhYmxlZD1mYWxzZTtidG4udGV4dENvbnRlbnQ9IlN1"
    "Ym1pdCI7cmV0dXJuO30KICAgICAgICBzaG90PWF3YWl0IG5ldyBQcm9taXNlKHJlcz0+ewogICAg"
    "ICAgICAgY29uc3QgcmQ9bmV3IEZpbGVSZWFkZXIoKTsKICAgICAgICAgIHJkLm9ubG9hZD0oKT0+"
    "cmVzKFN0cmluZyhyZC5yZXN1bHR8fCIiKSk7CiAgICAgICAgICByZC5vbmVycm9yPSgpPT5yZXMo"
    "IiIpOwogICAgICAgICAgcmQucmVhZEFzRGF0YVVSTChmaWxlKTsKICAgICAgICB9KTsKICAgICAg"
    "fQogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9kaXNwdXRlIix7b3JkZXI6b3JkLHJl"
    "YXNvbjpyZWFzb24sZGV0YWlsczpkZXRhaWxzLAogICAgICAgIGV2aWRlbmNlOmZuLHNob3Q6c2hv"
    "dH0pOwogICAgICBidG4uZGlzYWJsZWQ9ZmFsc2U7YnRuLnRleHRDb250ZW50PSJTdWJtaXQiOwog"
    "ICAgICBpZighcilyZXR1cm47CiAgICAgIGNsb3NlU2hlZXQoKTt0b2FzdCgiRGlzcHV0ZSAjIity"
    "LmlkKyIgc2VudCB0byB0aGUgYWRtaW5zIik7CiAgICAgIGF3YWl0IHJlZnJlc2goKTsKICAgIH07"
    "CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtcmF0ZV0iKS5mb3JFYWNo"
    "KGI9PmIub25jbGljaz0oKT0+e3dpbmRvdy5fX3JhdGVDb3VudHJ5PWIuZGF0YXNldC5yYXRlO3Jl"
    "dHVybiBzaGVldCgKICAgIGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVt"
    "Ij5SYXRlICR7Yi5kYXRhc2V0LnJhdGV9PC9oMz4KICAgICA8cCBjbGFzcz0ic3ViIj5Zb3VyIHJh"
    "dGluZyBzaG93cyBvbiB0aGUgY291bnRyeSBpbiB0aGUgc2hvcC48L3A+CiAgICAgPGRpdiBzdHls"
    "ZT0iZGlzcGxheTpmbGV4O2dhcDo5cHg7anVzdGlmeS1jb250ZW50OmNlbnRlcjttYXJnaW46MTZw"
    "eCAwIj4KICAgICAgICR7WzEsMiwzLDQsNV0ubWFwKG49PmA8YnV0dG9uIGNsYXNzPSJidG4gc20g"
    "c29mdCIgZGF0YS1yYXRlLWdvPSIke259Ij4ke2594piFPC9idXR0b24+YCkuam9pbigiIil9PC9k"
    "aXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJsb2NrIHNtIiBkYXRhLWNsb3NlPkNs"
    "b3NlPC9idXR0b24+YCk7fSk7IAogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNy"
    "eXB0b10iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+c2hlZXQoCiAgICBgPGgzIHN0eWxlPSJt"
    "YXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+Q3J5cHRvIHRvcC11cDwvaDM+CiAgICAgPHAg"
    "Y2xhc3M9InN1YiI+U2VuZCBVU0RULVRSQzIwIOKAlCBjcmVkaXRlZCBhZnRlciAxIG5ldHdvcmsg"
    "Y29uZmlybWF0aW9uLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJxciI+UVIgQ09ERTxicj4od2FsbGV0"
    "IGFkZHJlc3MpPC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPk5ldHdvcms8L3NwYW4+"
    "PGI+VFJDLTIwPC9iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5BZGRyZXNzPC9z"
    "cGFuPjxiIHN0eWxlPSJmb250LXNpemU6LjdyZW0iPm5vdC1jb25maWd1cmVkPC9iPjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5SYXRlPC9zcGFuPjxiPjEgVVNEVCA9IOKCuTg4LjQw"
    "PC9iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTJw"
    "eCI+PGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+CiAg"
    "ICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIiBkYXRhLWFjdD0iY3J5cHRvIj5JJ3ZlIFNlbnQg"
    "SXQ8L2J1dHRvbj48L2Rpdj5gKSk7CiAgY29uc3QgdGc9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "InRvdXJnbyIpLHRzPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJ0b3Vyc2tpcCIpOwogIGlmKHRn"
    "KXRnLm9uY2xpY2s9KCk9PnNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6"
    "ZToxcmVtIj5Ib3cgaXQgd29ya3M8L2gzPgogICAgIDxkaXYgY2xhc3M9InN0ZXBzIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDoxMnB4Ij4KICAgICAgIDxkaXYgY2xhc3M9InN0ZXAgb24iPjxpPjE8L2k+VG9w"
    "IHVwIHlvdXIgd2FsbGV0IGJ5IFVQSTwvZGl2PgogICAgICAgPGRpdiBjbGFzcz0ic3RlcCBvbiI+"
    "PGk+MjwvaT5QaWNrIGEgY291bnRyeSBhbmQgYnV5IGluc3RhbnRseTwvZGl2PgogICAgICAgPGRp"
    "diBjbGFzcz0ic3RlcCBvbiI+PGk+MzwvaT5SZWFkIHRoZSBsb2dpbiBjb2RlIHJpZ2h0IGhlcmUg"
    "aW4gT3JkZXJzPC9kaXY+PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBibG9jayIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9zZT5Hb3QgaXQ8L2J1dHRvbj5gKTsKICBp"
    "Zih0cyl0cy5vbmNsaWNrPSgpPT57RkVBVC5vbmJvYXJkaW5nPWZhbHNlO3JlbmRlcigpO3RvYXN0"
    "KCJUb3VyIGhpZGRlbiIpO307CiAgc3RhcnRDb3VudGRvd24oKTsKICBiaW5kSG9sZCgpOwp9CmZ1"
    "bmN0aW9uIGFwcGx5RmlsdGVyKCl7CiAgRklMVEVSRUQ9U1RPQ0suZmlsdGVyKHM9PnsKICAgIGNv"
    "bnN0IHQ9KHMuYysiICIrcy5jb2RlKS50b0xvd2VyQ2FzZSgpOwogICAgaWYoUVVFUlkmJiF0Lmlu"
    "Y2x1ZGVzKFFVRVJZKSlyZXR1cm4gZmFsc2U7CiAgICBpZihDSElQPT09InN0b2NrIiYmcy5xdHk9"
    "PT0wKXJldHVybiBmYWxzZTsKICAgIGlmKENISVA9PT0iZGVhbCImJiFzLndhcylyZXR1cm4gZmFs"
    "c2U7CiAgICBpZihDSElQPT09ImNoZWFwIiYmcy5wcmljZT49MzUpcmV0dXJuIGZhbHNlOwogICAg"
    "aWYoQ0hJUD09PSJ2MmZhIiYmcy5xdHk9PT0wKXJldHVybiBmYWxzZTsKICAgIGlmKENISVA9PT0i"
    "dm1haWwiJiYhcy5ob3QpcmV0dXJuIGZhbHNlOwogICAgaWYoQ0hJUD09PSJ2c3BhbSImJnMucHJp"
    "Y2U8MzApcmV0dXJuIGZhbHNlOwogICAgcmV0dXJuIHRydWU7fSk7CiAgY29uc3QgYj1kb2N1bWVu"
    "dC5nZXRFbGVtZW50QnlJZCgic2hvcGJvZHkiKTsKICBpZihiKXtiLmlubmVySFRNTD1zaG9wQm9k"
    "eSgpO2JpbmRCdXkoKTt9Cn0KbGV0IGNkVD1udWxsOwpmdW5jdGlvbiBzdGFydENvdW50ZG93bigp"
    "ewogIGNvbnN0IGVsPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjZCIpO2NsZWFySW50ZXJ2YWwo"
    "Y2RUKTtpZighZWwpcmV0dXJuOwogIGxldCB0PTE0KjYwKzUyOwogIGNkVD1zZXRJbnRlcnZhbCgo"
    "KT0+e2lmKCFkb2N1bWVudC5ib2R5LmNvbnRhaW5zKGVsKSl7Y2xlYXJJbnRlcnZhbChjZFQpO3Jl"
    "dHVybn0KICAgIHQ9dD4wP3QtMToxNCo2MCs1MjtlbC50ZXh0Q29udGVudD1TdHJpbmcoTWF0aC5m"
    "bG9vcih0LzYwKSkucGFkU3RhcnQoMiwiMCIpKyI6IitTdHJpbmcodCU2MCkucGFkU3RhcnQoMiwi"
    "MCIpfSwxMDAwKTsKfQovKiAwNSDCtyBob2xkLXRvLWNvbmZpcm0gKi8KZnVuY3Rpb24gYmluZEhv"
    "bGQoKXsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIuaG9sZCIpLmZvckVhY2goYj0+ewog"
    "ICAgaWYoYi5faClyZXR1cm47Yi5faD0xO2NvbnN0IGZpbGw9Yi5xdWVyeVNlbGVjdG9yKCIuZmls"
    "bCIpO2xldCBpdj1udWxsLHA9MDsKICAgIGNvbnN0IHN0b3A9KCk9PntjbGVhckludGVydmFsKGl2"
    "KTtwPTA7aWYoZmlsbClmaWxsLnN0eWxlLndpZHRoPSIwJSJ9OwogICAgY29uc3QgZ289ZT0+e2Uu"
    "cHJldmVudERlZmF1bHQoKTtjbGVhckludGVydmFsKGl2KTtpdj1zZXRJbnRlcnZhbCgoKT0+e3Ar"
    "PTQ7aWYoZmlsbClmaWxsLnN0eWxlLndpZHRoPXArIiUiOwogICAgICBpZihwPj0xMDApe3N0b3Ao"
    "KTtiLmNsYXNzTGlzdC5hZGQoImRvbmUiKTtzZXRUaW1lb3V0KCgpPT5iLmNsYXNzTGlzdC5yZW1v"
    "dmUoImRvbmUiKSw0NTApO2J1cnN0KCk7dG9hc3QoYi5kYXRhc2V0LmRvbmV8fCJDb25maXJtZWQi"
    "KX19LDI4KX07CiAgICBiLmFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJkb3duIixnbyk7WyJwb2lu"
    "dGVydXAiLCJwb2ludGVybGVhdmUiLCJwb2ludGVyY2FuY2VsIl0uZm9yRWFjaChldj0+Yi5hZGRF"
    "dmVudExpc3RlbmVyKGV2LHN0b3ApKTt9KTsKfQovKiDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZAgS0lUIFJVTlRJTUUg4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQICov"
    "CmNvbnN0IFJNPW1hdGNoTWVkaWEoIihwcmVmZXJzLXJlZHVjZWQtbW90aW9uOnJlZHVjZSkiKS5t"
    "YXRjaGVzOwovKiBzcHJpbmcgaW50ZWdyYXRvciDigJQga2l0IHVzZXMgbWFzcyAuMSAvIHN0aWZm"
    "bmVzcyAxNTAgLyBkYW1waW5nIDEyICovCmZ1bmN0aW9uIFNwcmluZyh2LHttPS4xLGs9MTUwLGQ9"
    "MTJ9PXt9KXt0aGlzLnY9djt0aGlzLnQ9djt0aGlzLnZlbD0wO3RoaXMubT1tO3RoaXMuaz1rO3Ro"
    "aXMuZD1kO30KU3ByaW5nLnByb3RvdHlwZS5zdGVwPWZ1bmN0aW9uKGR0KXtjb25zdCBGPS10aGlz"
    "LmsqKHRoaXMudi10aGlzLnQpLXRoaXMuZCp0aGlzLnZlbDsKICB0aGlzLnZlbCs9KEYvdGhpcy5t"
    "KSpkdDt0aGlzLnYrPXRoaXMudmVsKmR0O3JldHVybiB0aGlzLnY7fTsKCi8qIDAyICsgMjUgwrcg"
    "YW5pbWF0ZWQgZG9jazogZGlzdGFuY2XihpJzY2FsZSB3aXRoIHNwcmluZywgcGx1cyBzbGlkaW5n"
    "IHBpbGwgKi8KKGZ1bmN0aW9uIGRvY2soKXsKICBjb25zdCBkaz1kb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yKCJuYXYjdGFicyAuZG9jayIpO2lmKCFkaylyZXR1cm47CiAgY29uc3QgYnRucz1bLi4uZGsu"
    "cXVlcnlTZWxlY3RvckFsbCgiYnV0dG9uIildLHBpbGw9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "ImRwaWxsIik7CiAgY29uc3QgRklORT1tYXRjaE1lZGlhKCIoaG92ZXI6aG92ZXIpIGFuZCAocG9p"
    "bnRlcjpmaW5lKSIpLm1hdGNoZXMmJiFSTTsKICBjb25zdCBzcD1idG5zLm1hcCgoKT0+bmV3IFNw"
    "cmluZygxKSkscHM9e3g6bmV3IFNwcmluZygwLHtrOjI2MCxkOjExfSksdzpuZXcgU3ByaW5nKDAs"
    "e2s6MjYwLGQ6MTF9KX07CiAgbGV0IG14PUluZmluaXR5LGxhc3Q9cGVyZm9ybWFuY2Uubm93KCks"
    "cGxhY2VkPWZhbHNlOwogIGZ1bmN0aW9uIHJlc2V0U2NhbGUoKXtteD1JbmZpbml0eTtpZighRklO"
    "RSlidG5zLmZvckVhY2goYj0+Yi5zdHlsZS50cmFuc2Zvcm09IiIpO30KICBpZihGSU5FKXsKICAg"
    "IGRrLmFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJtb3ZlIixlPT57aWYoZS5wb2ludGVyVHlwZT09"
    "PSJtb3VzZSIpbXg9ZS5jbGllbnRYfSk7CiAgICBkay5hZGRFdmVudExpc3RlbmVyKCJwb2ludGVy"
    "bGVhdmUiLCgpPT5teD1JbmZpbml0eSk7CiAgICBbInBvaW50ZXJ1cCIsInBvaW50ZXJjYW5jZWwi"
    "LCJ0b3VjaGVuZCIsInRvdWNoY2FuY2VsIiwiYmx1ciJdLmZvckVhY2goZXY9PgogICAgICBhZGRF"
    "dmVudExpc3RlbmVyKGV2LHJlc2V0U2NhbGUse3Bhc3NpdmU6dHJ1ZX0pKTsKICB9ZWxzZXtidG5z"
    "LmZvckVhY2goYj0+Yi5zdHlsZS50cmFuc2Zvcm09IiIpO30KICBkb2N1bWVudC5hZGRFdmVudExp"
    "c3RlbmVyKCJ2aXNpYmlsaXR5Y2hhbmdlIixyZXNldFNjYWxlKTsKCiAgZnVuY3Rpb24gcGxhY2Uo"
    "c25hcCl7CiAgICBjb25zdCBvbj1kay5xdWVyeVNlbGVjdG9yKCJidXR0b24ub24iKTsKICAgIGlm"
    "KCFvbnx8IXBpbGx8fCFvbi5vZmZzZXRXaWR0aHx8IWRrLm9mZnNldFdpZHRoKXJldHVybjsgICAv"
    "LyBuZXZlciBwbGFjZSB3aGlsZSBoaWRkZW4KICAgIHBzLngudD1vbi5vZmZzZXRMZWZ0LWRrLmNs"
    "aWVudExlZnQ7cHMudy50PW9uLm9mZnNldFdpZHRoOwogICAgcGlsbC5zdHlsZS50b3A9KG9uLm9m"
    "ZnNldFRvcC1kay5jbGllbnRUb3ApKyJweCI7cGlsbC5zdHlsZS5oZWlnaHQ9b24ub2Zmc2V0SGVp"
    "Z2h0KyJweCI7CiAgICBpZihzbmFwfHwhcGxhY2VkKXtwbGFjZWQ9dHJ1ZTtwcy54LnY9cHMueC50"
    "O3BzLngudmVsPTA7cHMudy52PXBzLncudDtwcy53LnZlbD0wOwogICAgICBwaWxsLnN0eWxlLnRy"
    "YW5zZm9ybT0idHJhbnNsYXRlWCgiK3BzLngudisicHgpIjtwaWxsLnN0eWxlLndpZHRoPXBzLncu"
    "disicHgiOwogICAgICBwaWxsLmNsYXNzTGlzdC5hZGQoInJlYWR5Iik7fQogIH0KICBuZXcgUmVz"
    "aXplT2JzZXJ2ZXIoKCk9PnBsYWNlKCFwbGFjZWQpKS5vYnNlcnZlKGRrKTsKICBhZGRFdmVudExp"
    "c3RlbmVyKCJyZXNpemUiLCgpPT5wbGFjZSh0cnVlKSk7CiAgYWRkRXZlbnRMaXN0ZW5lcigibG9h"
    "ZCIsKCk9PnBsYWNlKCFwbGFjZWQpKTsKICBzZXRUaW1lb3V0KCgpPT5wbGFjZSghcGxhY2VkKSw2"
    "MCk7c2V0VGltZW91dCgoKT0+cGxhY2UoIXBsYWNlZCksNDAwKTsKCiAgKGZ1bmN0aW9uIGxvb3Ao"
    "dCl7CiAgICBjb25zdCBkdD1NYXRoLm1pbiguMDMyLCh0LWxhc3QpLzEwMDApO2xhc3Q9dDsKICAg"
    "IGlmKEZJTkUpe2J0bnMuZm9yRWFjaCgoYixpKT0+e2xldCBrPTA7CiAgICAgIGlmKG14IT09SW5m"
    "aW5pdHkpe2NvbnN0IHI9Yi5nZXRCb3VuZGluZ0NsaWVudFJlY3QoKTsKICAgICAgICBjb25zdCBk"
    "aXN0PU1hdGguYWJzKHIubGVmdCtyLndpZHRoLzItbXgpO2s9TWF0aC5tYXgoMCwxLWRpc3QvMTUw"
    "KTt9CiAgICAgIHNwW2ldLnQ9MSsuMzQqayprO2NvbnN0IHY9c3BbaV0uc3RlcChkdCk7CiAgICAg"
    "IGIuc3R5bGUudHJhbnNmb3JtPSh2PjEuMDAyfHxzcFtpXS52ZWw+LjAxfHxzcFtpXS52ZWw8LS4w"
    "MSkKICAgICAgICA/ICJ0cmFuc2xhdGVZKCIrKC04Kih2LTEpKS50b0ZpeGVkKDIpKyJweCkgc2Nh"
    "bGUoIit2LnRvRml4ZWQoMykrIikiIDogIiI7fSk7fQogICAgaWYocGlsbCYmcGxhY2VkKXtwaWxs"
    "LnN0eWxlLnRyYW5zZm9ybT0idHJhbnNsYXRlWCgiK3BzLnguc3RlcChkdCkudG9GaXhlZCgyKSsi"
    "cHgpIjsKICAgICAgcGlsbC5zdHlsZS53aWR0aD1wcy53LnN0ZXAoZHQpLnRvRml4ZWQoMikrInB4"
    "Ijt9CiAgICByZXF1ZXN0QW5pbWF0aW9uRnJhbWUobG9vcCk7fSkobGFzdCk7CiAgd2luZG93Ll9f"
    "ZG9ja1BsYWNlPXBsYWNlO3BsYWNlKHRydWUpOwp9KSgpOwoKLyogMTQgwrcgdi10YWJzIHNsaWRp"
    "bmcgaW5kaWNhdG9yIGZvciB0aGUgYWRtaW4gc3VibmF2ICovCihmdW5jdGlvbiB2dGFicygpewog"
    "IGNvbnN0IG5hdj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYXN1YiIpLHBpbGw9ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoInZwaWxsIik7aWYoIW5hdnx8IXBpbGwpcmV0dXJuOwogIGNvbnN0IHB4"
    "PW5ldyBTcHJpbmcoMCx7azoyMTAsZDoxOX0pLHB3PW5ldyBTcHJpbmcoMCx7azoyMTAsZDoxOX0p"
    "O2xldCBsYXN0PXBlcmZvcm1hbmNlLm5vdygpLGluaXQ9ZmFsc2U7CiAgZnVuY3Rpb24gcGxhY2Uo"
    "c25hcCl7Y29uc3Qgb249bmF2LnF1ZXJ5U2VsZWN0b3IoImJ1dHRvbi5vbiIpOwogICAgaWYoIW9u"
    "fHwhb24ub2Zmc2V0V2lkdGh8fCFuYXYub2Zmc2V0V2lkdGgpcmV0dXJuOwogICAgcHgudD1vbi5v"
    "ZmZzZXRMZWZ0LW5hdi5jbGllbnRMZWZ0O3B3LnQ9b24ub2Zmc2V0V2lkdGg7CiAgICBwaWxsLnN0"
    "eWxlLnRvcD0ob24ub2Zmc2V0VG9wLW5hdi5jbGllbnRUb3ApKyJweCI7cGlsbC5zdHlsZS5oZWln"
    "aHQ9b24ub2Zmc2V0SGVpZ2h0KyJweCI7CiAgICBpZihzbmFwfHwhaW5pdCl7aW5pdD10cnVlO3B4"
    "LnY9cHgudDtweC52ZWw9MDtwdy52PXB3LnQ7cHcudmVsPTA7CiAgICAgIHBpbGwuc3R5bGUudHJh"
    "bnNmb3JtPSJ0cmFuc2xhdGVYKCIrcHgudisicHgpIjtwaWxsLnN0eWxlLndpZHRoPXB3LnYrInB4"
    "Ijt9fQogIG5hdi5hZGRFdmVudExpc3RlbmVyKCJjbGljayIsKCk9PnJlcXVlc3RBbmltYXRpb25G"
    "cmFtZSgoKT0+cGxhY2UoZmFsc2UpKSk7CiAgbmF2LmFkZEV2ZW50TGlzdGVuZXIoInNjcm9sbCIs"
    "KCk9PnBsYWNlKGZhbHNlKSk7CiAgbmV3IFJlc2l6ZU9ic2VydmVyKCgpPT5wbGFjZSghaW5pdCkp"
    "Lm9ic2VydmUobmF2KTsKICBhZGRFdmVudExpc3RlbmVyKCJsb2FkIiwoKT0+cGxhY2UoIWluaXQp"
    "KTtzZXRUaW1lb3V0KCgpPT5wbGFjZSghaW5pdCksODApO3BsYWNlKHRydWUpOwogIChmdW5jdGlv"
    "biBsb29wKHQpe2NvbnN0IGR0PU1hdGgubWluKC4wMzIsKHQtbGFzdCkvMTAwMCk7bGFzdD10Owog"
    "ICAgaWYoaW5pdCl7cGlsbC5zdHlsZS50cmFuc2Zvcm09InRyYW5zbGF0ZVgoIitweC5zdGVwKGR0"
    "KS50b0ZpeGVkKDIpKyJweCkiO3BpbGwuc3R5bGUud2lkdGg9cHcuc3RlcChkdCkudG9GaXhlZCgy"
    "KSsicHgiO30KICAgIHJlcXVlc3RBbmltYXRpb25GcmFtZShsb29wKTt9KShsYXN0KTsKICB3aW5k"
    "b3cuX192dGFiUGxhY2U9cGxhY2U7Cn0pKCk7CgovKiAwOCDCtyBraW5ldGljIGdyaWQg4oCUIHBv"
    "aW50ZXIgd2FycCArIGNsaWNrIHJpcHBsZXMgKGNhbnZhcyBwb3J0KSAqLwooZnVuY3Rpb24ga2lu"
    "ZXRpYygpewogIGNvbnN0IGN2PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJrZyIpO2lmKCFjdnx8"
    "Uk0pcmV0dXJuO2NvbnN0IGN0eD1jdi5nZXRDb250ZXh0KCIyZCIpOwogIGNvbnN0IFNQPTMwLElO"
    "Rj0yMTAsV0FSUD0yMixMRVJQPS4wOSxSR0I9IjUyLDIxMSwxNTMiOwogIGxldCBXPTAsSD0wLG1v"
    "dXNlPXt4Oi05OTk5LHk6LTk5OTl9LHRndD17eDotOTk5OSx5Oi05OTk5fSxyaXA9W10scmFmPTA7"
    "CiAgZnVuY3Rpb24gc2l6ZSgpe2NvbnN0IGQ9TWF0aC5taW4oMS42LGRldmljZVBpeGVsUmF0aW98"
    "fDEpO1c9aW5uZXJXaWR0aDtIPWlubmVySGVpZ2h0OwogICAgY3Yud2lkdGg9VypkO2N2LmhlaWdo"
    "dD1IKmQ7Y3Yuc3R5bGUud2lkdGg9VysicHgiO2N2LnN0eWxlLmhlaWdodD1IKyJweCI7Y3R4LnNl"
    "dFRyYW5zZm9ybShkLDAsMCxkLDAsMCk7fQogIGFkZEV2ZW50TGlzdGVuZXIoInJlc2l6ZSIsc2l6"
    "ZSk7c2l6ZSgpOwogIGFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJtb3ZlIixlPT57dGd0PXt4OmUu"
    "Y2xpZW50WCx5OmUuY2xpZW50WX19LHtwYXNzaXZlOnRydWV9KTsKICBhZGRFdmVudExpc3RlbmVy"
    "KCJwb2ludGVyZG93biIsZT0+e2lmKHJpcC5sZW5ndGg8NClyaXAucHVzaCh7eDplLmNsaWVudFgs"
    "eTplLmNsaWVudFkscjowLG86MX0pfSx7cGFzc2l2ZTp0cnVlfSk7CiAgYWRkRXZlbnRMaXN0ZW5l"
    "cigicG9pbnRlcmxlYXZlIiwoKT0+dGd0PXt4Oi05OTk5LHk6LTk5OTl9KTsKICBmdW5jdGlvbiB3"
    "YXJwKGd4LGd5LGMscixjb2xzLHJvd3MpewogICAgY29uc3QgY3A9TWF0aC5taW4oYy8xLjUsKGNv"
    "bHMtMS1jKS8xLjUsMSkscnA9TWF0aC5taW4oci8xLjUsKHJvd3MtMS1yKS8xLjUsMSkscGluPWNw"
    "KmNwKnJwKnJwOwogICAgY29uc3QgZHg9Z3gtbW91c2UueCxkeT1neS1tb3VzZS55LGRpc3Q9TWF0"
    "aC5oeXBvdChkeCxkeSk7CiAgICBjb25zdCBwcm94PU1hdGgubWF4KDAsMS1kaXN0L0lORikqcGlu"
    "O2xldCByeD0wLHJ5PTA7CiAgICBmb3IoY29uc3QgcCBvZiByaXApe2NvbnN0IGRkeD1neC1wLngs"
    "ZGR5PWd5LXAueSxkZD1NYXRoLmh5cG90KGRkeCxkZHkpLGRpZmY9ZGQtcC5yOwogICAgICBpZihN"
    "YXRoLmFicyhkaWZmKTw1NSl7Y29uc3Qgc3Q9KDEtTWF0aC5hYnMoZGlmZikvNTUpKnAubyoxNipw"
    "aW4sYW49TWF0aC5hdGFuMihkZHksZGR4KSxzZz1kaWZmPDA/LTE6MTsKICAgICAgICByeCs9TWF0"
    "aC5jb3MoYW4pKnN0KnNnKi0xO3J5Kz1NYXRoLnNpbihhbikqc3Qqc2cqLTE7fX0KICAgIGlmKGRp"
    "c3Q8SU5GJiZkaXN0PjAmJnBpbj4wKXtjb25zdCB0PWRpc3QvSU5GLGU9KDEtdCkqKDEtdCkqTWF0"
    "aC5taW4oMSxkaXN0LzYwKSx3PWUqV0FSUCpwaW4sYW49TWF0aC5hdGFuMihkeSxkeCk7CiAgICAg"
    "IHJldHVybltneC1NYXRoLmNvcyhhbikqdytyeCxneS1NYXRoLnNpbihhbikqdytyeSxwcm94XTt9"
    "CiAgICByZXR1cm5bZ3grcngsZ3krcnkscHJveF07fQogIGZ1bmN0aW9uIGZyYW1lKCl7CiAgICBt"
    "b3VzZS54Kz0odGd0LngtbW91c2UueCkqTEVSUDttb3VzZS55Kz0odGd0LnktbW91c2UueSkqTEVS"
    "UDsKICAgIHJpcD1yaXAuZmlsdGVyKHA9PntwLnIrPTc7cC5vLT0uMDE2O3JldHVybiBwLm8+MH0p"
    "OwogICAgY3R4LmNsZWFyUmVjdCgwLDAsVyxIKTsKICAgIGNvbnN0IGNvbHM9TWF0aC5jZWlsKFcv"
    "U1ApKzEscm93cz1NYXRoLmNlaWwoSC9TUCkrMSxwdHM9W107CiAgICBmb3IobGV0IHI9MDtyPHJv"
    "d3M7cisrKXtjb25zdCByb3c9W107Zm9yKGxldCBjPTA7Yzxjb2xzO2MrKylyb3cucHVzaCh3YXJw"
    "KGMqU1AscipTUCxjLHIsY29scyxyb3dzKSk7cHRzLnB1c2gocm93KTt9CiAgICBmb3IobGV0IHI9"
    "MDtyPHJvd3M7cisrKWZvcihsZXQgYz0wO2M8Y29scztjKyspe2NvbnN0IHA9cHRzW3JdW2NdOwog"
    "ICAgICBpZihjPGNvbHMtMSl7Y29uc3QgcT1wdHNbcl1bYysxXTtjdHguc3Ryb2tlU3R5bGU9InJn"
    "YmEoIitSR0IrIiwiKyguMDUrcFsyXSouMjIpLnRvRml4ZWQoMykrIikiOwogICAgICAgIGN0eC5s"
    "aW5lV2lkdGg9MTtjdHguYmVnaW5QYXRoKCk7Y3R4Lm1vdmVUbyhwWzBdLHBbMV0pO2N0eC5saW5l"
    "VG8ocVswXSxxWzFdKTtjdHguc3Ryb2tlKCk7fQogICAgICBpZihyPHJvd3MtMSl7Y29uc3QgcT1w"
    "dHNbcisxXVtjXTtjdHguc3Ryb2tlU3R5bGU9InJnYmEoIitSR0IrIiwiKyguMDUrcFsyXSouMjIp"
    "LnRvRml4ZWQoMykrIikiOwogICAgICAgIGN0eC5saW5lV2lkdGg9MTtjdHguYmVnaW5QYXRoKCk7"
    "Y3R4Lm1vdmVUbyhwWzBdLHBbMV0pO2N0eC5saW5lVG8ocVswXSxxWzFdKTtjdHguc3Ryb2tlKCk7"
    "fQogICAgICBpZihwWzJdPi4wNil7Y3R4LmZpbGxTdHlsZT0icmdiYSgiK1JHQisiLCIrKHBbMl0q"
    "LjUpLnRvRml4ZWQoMykrIikiO2N0eC5iZWdpblBhdGgoKTsKICAgICAgICBjdHguYXJjKHBbMF0s"
    "cFsxXSwxLjYrcFsyXSoxLjYsMCw2LjI4Myk7Y3R4LmZpbGwoKTt9fQogICAgcmFmPXJlcXVlc3RB"
    "bmltYXRpb25GcmFtZShmcmFtZSk7fQogIGZyYW1lKCk7CiAgZG9jdW1lbnQuYWRkRXZlbnRMaXN0"
    "ZW5lcigidmlzaWJpbGl0eWNoYW5nZSIsKCk9PntpZihkb2N1bWVudC5oaWRkZW4pY2FuY2VsQW5p"
    "bWF0aW9uRnJhbWUocmFmKTtlbHNlIGZyYW1lKCk7fSk7Cn0pKCk7CgovKiAwMSDCtyBkb3QgbG9h"
    "ZGVyIHNwbGFzaCAoa2l0IGZyYW1lIGRhdGEpICovCihmdW5jdGlvbiBzcGxhc2goKXsKICBjb25z"
    "dCBnPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJkZyIpLHNwPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJzcGxhc2giKTtpZighZylyZXR1cm47CiAgZm9yKGxldCBpPTA7aTw0OTtpKyspZy5hcHBl"
    "bmRDaGlsZChkb2N1bWVudC5jcmVhdGVFbGVtZW50KCJpIikpOwogIGNvbnN0IGNlbGxzPVsuLi5n"
    "LmNoaWxkcmVuXTsKICBjb25zdCBGPVtbMTQsNywwLDgsNiwxMywyMF0sWzE0LDcsMTMsMjAsMTYs"
    "MjcsMjFdLFsxNCwyMCwyNywyMSwzNCwyNCwyOF0sWzI3LDIxLDM0LDI4LDQxLDMyLDM1XSwKICAg"
    "WzM0LDI4LDQxLDM1LDQ4LDQwLDQyXSxbMzQsMjgsNDEsMzUsNDgsNDIsNDZdLFszNCwyOCw0MSwz"
    "NSw0OCw0MiwzOF0sWzM0LDI4LDQxLDM1LDQ4LDMwLDIxXSwKICAgWzM0LDI4LDQxLDQ4LDIxLDIy"
    "LDE0XSxbMzQsMjgsNDEsMjEsMTQsMTYsMjddLFszNCwyOCwyMSwxNCwxMCwyMCwyN10sWzI4LDIx"
    "LDE0LDQsMTMsMjAsMjddLAogICBbMjgsMjEsMTQsMTIsNiwxMywyMF0sWzI4LDIxLDE0LDYsMTMs"
    "MjAsMTFdLFsyOCwyMSwxNCw2LDEzLDIwLDEwXSxbMTQsNiwxMywyMCw5LDcsMjFdXTsKICBsZXQg"
    "Zj0wO2NvbnN0IGl2PXNldEludGVydmFsKCgpPT57Y2VsbHMuZm9yRWFjaChjPT5jLmNsYXNzTGlz"
    "dC5yZW1vdmUoImFjdCIpKTsKICAgIEZbZiVGLmxlbmd0aF0uZm9yRWFjaChpPT5jZWxsc1tpXSYm"
    "Y2VsbHNbaV0uY2xhc3NMaXN0LmFkZCgiYWN0IikpO2YrKzt9LDEwMCk7CiAgc2V0VGltZW91dCgo"
    "KT0+e2NsZWFySW50ZXJ2YWwoaXYpO3NwLmNsYXNzTGlzdC5hZGQoImdvbmUiKTsKICAgIHNldFRp"
    "bWVvdXQoKCk9PntzcC5yZW1vdmUoKTtpZih3aW5kb3cuX19kb2NrUGxhY2Upd2luZG93Ll9fZG9j"
    "a1BsYWNlKHRydWUpOwogICAgICBpZih3aW5kb3cuX192dGFiUGxhY2Upd2luZG93Ll9fdnRhYlBs"
    "YWNlKHRydWUpO30sNjAwKTt9LFJNPzMwMDoxNzUwKTsKfSkoKTsKCi8qIHN0YWdnZXJlZCByZXZl"
    "YWwgb24gZXZlcnkgdmlldyBjaGFuZ2UgKi8KZnVuY3Rpb24gcmV2ZWFsU3RhZ2UoZWwpewogIGlm"
    "KCFlbHx8Uk0pcmV0dXJuOwogIFsuLi5lbC5jaGlsZHJlbl0uc2xpY2UoMCwxNCkuZm9yRWFjaCgo"
    "YyxpKT0+ewogICAgaWYoIWMuYW5pbWF0ZSlyZXR1cm47CiAgICB0cnl7CiAgICAgIGMuZ2V0QW5p"
    "bWF0aW9ucygpLmZvckVhY2goYT0+e2lmKGEuaWQ9PT0icnYiKWEuY2FuY2VsKCk7fSk7CiAgICAg"
    "IGNvbnN0IGFuPWMuYW5pbWF0ZShbe29wYWNpdHk6MCx0cmFuc2Zvcm06InRyYW5zbGF0ZVkoMTRw"
    "eCkgc2NhbGUoLjk4NSkifSx7b3BhY2l0eToxLHRyYW5zZm9ybToibm9uZSJ9XSwKICAgICAgICB7"
    "ZHVyYXRpb246NTIwLGRlbGF5OmkqNDAsZWFzaW5nOiJjdWJpYy1iZXppZXIoLjIyLDEsLjM2LDEp"
    "IixmaWxsOiJiYWNrd2FyZHMifSk7CiAgICAgIGFuLmlkPSJydiI7CiAgICB9Y2F0Y2goZSl7fQog"
    "IH0pO30KCi8qIG51bWJlciB0aWNrZXIgKi8KZnVuY3Rpb24gdGlja1RvKGVsLHRvLGR1cil7aWYo"
    "IWVsKXJldHVybjtjb25zdCBmcm9tPXBhcnNlRmxvYXQoZWwudGV4dENvbnRlbnQucmVwbGFjZSgv"
    "W15cZC5dL2csIiIpKXx8MDsKICBpZihSTSl7ZWwudGV4dENvbnRlbnQ9dG8udG9GaXhlZCgyKTty"
    "ZXR1cm59CiAgY29uc3QgdDA9cGVyZm9ybWFuY2Uubm93KCk7KGZ1bmN0aW9uIHN0KHQpe2NvbnN0"
    "IHA9TWF0aC5taW4oMSwodC10MCkvKGR1cnx8NjAwKSk7CiAgICBjb25zdCBlPTEtTWF0aC5wb3co"
    "MS1wLDMpO2VsLnRleHRDb250ZW50PShmcm9tKyh0by1mcm9tKSplKS50b0ZpeGVkKDIpOwogICAg"
    "aWYocDwxKXJlcXVlc3RBbmltYXRpb25GcmFtZShzdCk7fSkodDApO30KCi8qIHByZXNzIHJpcHBs"
    "ZSBvbiBldmVyeSBidXR0b24gKi8KZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcmRv"
    "d24iLGU9Pntjb25zdCBiPWUudGFyZ2V0LmNsb3Nlc3QoIi5idG4sLmNoaXByb3cgYnV0dG9uLC5z"
    "dWJuYXYgYnV0dG9uIik7CiAgaWYoIWJ8fFJNKXJldHVybjtjb25zdCByPWIuZ2V0Qm91bmRpbmdD"
    "bGllbnRSZWN0KCksZD1NYXRoLm1heChyLndpZHRoLHIuaGVpZ2h0KSoxLjQ7CiAgY29uc3Qgcz1k"
    "b2N1bWVudC5jcmVhdGVFbGVtZW50KCJzcGFuIik7cy5jbGFzc05hbWU9InJpcCI7CiAgcy5zdHls"
    "ZS5jc3NUZXh0PSJsZWZ0OiIrKGUuY2xpZW50WC1yLmxlZnQpKyJweDt0b3A6IisoZS5jbGllbnRZ"
    "LXIudG9wKSsicHg7d2lkdGg6IitkKyJweDtoZWlnaHQ6IitkKyJweCI7CiAgaWYoZ2V0Q29tcHV0"
    "ZWRTdHlsZShiKS5wb3NpdGlvbj09PSJzdGF0aWMiKWIuc3R5bGUucG9zaXRpb249InJlbGF0aXZl"
    "IjsKICBiLmFwcGVuZENoaWxkKHMpO3NldFRpbWVvdXQoKCk9PnMucmVtb3ZlKCksNTYwKTt9KTsK"
    "Ci8qIDE1IMK3IHRpbHQgKyBnbGFyZSAqLwpmdW5jdGlvbiBiaW5kVGlsdCgpe2RvY3VtZW50LnF1"
    "ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXRpbHRdIikuZm9yRWFjaChjPT57CiAgaWYoYy5fdHx8Uk0p"
    "cmV0dXJuO2MuX3Q9MTsKICBjLmFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJtb3ZlIixlPT57Y29u"
    "c3Qgcj1jLmdldEJvdW5kaW5nQ2xpZW50UmVjdCgpOwogICAgY29uc3QgeD0oZS5jbGllbnRYLXIu"
    "bGVmdCkvci53aWR0aCx5PShlLmNsaWVudFktci50b3ApL3IuaGVpZ2h0OwogICAgYy5zdHlsZS50"
    "cmFuc2Zvcm09InBlcnNwZWN0aXZlKDcwMHB4KSByb3RhdGVYKCIrKCguNS15KSo5KS50b0ZpeGVk"
    "KDIpKyJkZWcpIHJvdGF0ZVkoIisoKHgtLjUpKjExKS50b0ZpeGVkKDIpKyJkZWcpIHRyYW5zbGF0"
    "ZVooNHB4KSI7CiAgICBjLnN0eWxlLnNldFByb3BlcnR5KCItLW14IiwoeCoxMDApKyIlIik7Yy5z"
    "dHlsZS5zZXRQcm9wZXJ0eSgiLS1teSIsKHkqMTAwKSsiJSIpO30pOwogIGMuYWRkRXZlbnRMaXN0"
    "ZW5lcigicG9pbnRlcmxlYXZlIiwoKT0+Yy5zdHlsZS50cmFuc2Zvcm09IiIpO30pO30KCi8qIHN1"
    "Y2Nlc3MgYnVyc3QgKi8KZnVuY3Rpb24gYnVyc3QoKXtjb25zdCBmeD1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiZngiKTtpZighZngpcmV0dXJuO2Z4LmNsYXNzTGlzdC5hZGQoIm9uIik7CiAgY29u"
    "c3Qgdz1meC5xdWVyeVNlbGVjdG9yKCIud2F2ZSIpO3cuc3R5bGUuYW5pbWF0aW9uPSJub25lIjt2"
    "b2lkIHcub2Zmc2V0V2lkdGg7dy5zdHlsZS5hbmltYXRpb249IiI7CiAgc2V0VGltZW91dCgoKT0+"
    "ZnguY2xhc3NMaXN0LnJlbW92ZSgib24iKSw5MDApO30KCi8qIOKUgOKUgCBQYXkgKyB2ZXJpZnkg"
    "ZGVwb3NpdCBmbG93ICg1LW1pbnV0ZSB3aW5kb3csIHZlcmlmeSBvbmx5IG9uIHRhcCkg4pSA4pSA"
    "ICovCmxldCBkZXBUaWNrPW51bGwsZGVwSm9iPW51bGw7CmZ1bmN0aW9uIGFtb3VudFNoZWV0KCl7"
    "CiAgY29uc3QgcGlja3M9WzUwLDEwMCwyMDAsNTAwLDEwMDAsMjAwMF07CiAgc2hlZXQoYDxoMyBz"
    "dHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPiR7Q01TLndhbEtpY2t8fCJBZGQg"
    "ZnVuZHMifTwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj5QaWNrIGFuIGFtb3VudCBvciB0eXBlIHlv"
    "dXIgb3duLjwvcD4KICAgIDxkaXYgY2xhc3M9ImNoaXBzIiBzdHlsZT0ibWFyZ2luOjEycHggMCA0"
    "cHg7ZGlzcGxheTpmbGV4O2ZsZXgtd3JhcDp3cmFwO2dhcDo3cHgiPgogICAgICAke3BpY2tzLm1h"
    "cCh2PT5gPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBkYXRhLXBpY2s9IiR7dn0iIHN0eWxlPSJmbGV4"
    "OjAgMCBhdXRvIj4ke21vbmV5KHYpfTwvYnV0dG9uPmApLmpvaW4oIiIpfQogICAgPC9kaXY+CiAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPkN1c3Rv"
    "bSBhbW91bnQ8L2xhYmVsPgogICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImFtdGluIiB0eXBl"
    "PSJudW1iZXIiIGlucHV0bW9kZT0ibnVtZXJpYyIgbWluPSIxIiBwbGFjZWhvbGRlcj0iRW50ZXIg"
    "YW1vdW50Ij48L2Rpdj4KICAgIDxwIGNsYXNzPSJzdWIiIGlkPSJhbXRoaW50IiBzdHlsZT0ibWFy"
    "Z2luOjAgMCAxMHB4O2ZvbnQtc2l6ZTouNzJyZW0iPjwvcD4KICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biBjdGEgYmxvY2siIGlkPSJhbXRnbyI+Q29udGludWU8L2J1dHRvbj4KICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNsb3Nl"
    "PkNhbmNlbDwvYnV0dG9uPmApOwogIGNvbnN0IGlucD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgi"
    "YW10aW4iKSwgZ289ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFtdGdvIiksCiAgICAgICAgaGlu"
    "dD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYW10aGludCIpOwogIGNvbnN0IGxvPShBRE0mJkFE"
    "TS5wYXkmJkFETS5wYXkubWluKXx8MTAsIGhpPShBRE0mJkFETS5wYXkmJkFETS5wYXkubWF4KXx8"
    "MjAwMDA7CiAgaGludC50ZXh0Q29udGVudD0iQmV0d2VlbiAiK21vbmV5KGxvKSsiIGFuZCAiK21v"
    "bmV5KGhpKSsiLiI7CiAgY29uc3Qgc3RhcnQ9dj0+ewogICAgY29uc3Qgbj1OdW1iZXIodik7CiAg"
    "ICBpZighbnx8bjw9MCl7dG9hc3QoIkVudGVyIGFuIGFtb3VudCBmaXJzdCIpO2lucCYmaW5wLmZv"
    "Y3VzKCk7cmV0dXJuO30KICAgIGlmKG48bG98fG4+aGkpe3RvYXN0KCJBbGxvd2VkICIrbW9uZXko"
    "bG8pKyIgdG8gIittb25leShoaSkpO3JldHVybjt9CiAgICBkZXBvc2l0U2hlZXQobik7CiAgfTsK"
    "ICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1waWNrXSIpLmZvckVhY2goYj0+Yi5v"
    "bmNsaWNrPSgpPT5zdGFydChiLmRhdGFzZXQucGljaykpOwogIGdvLm9uY2xpY2s9KCk9PnN0YXJ0"
    "KGlucC52YWx1ZSk7CiAgaWYoaW5wKWlucC5vbmtleWRvd249ZT0+e2lmKGUua2V5PT09IkVudGVy"
    "IilzdGFydChpbnAudmFsdWUpO307CiAgc2V0VGltZW91dCgoKT0+e2lmKGlucClpbnAuZm9jdXMo"
    "KTt9LDI1MCk7Cn0KYXN5bmMgZnVuY3Rpb24gbGl2ZURlcG9zaXRTaGVldChhKXsKICBjbGVhcklu"
    "dGVydmFsKGRlcFRpY2spO2NsZWFyVGltZW91dChkZXBKb2IpOwogIGNvbnN0IGQ9YXdhaXQgYXBp"
    "VHJ5KCIvYXBpL2RlcG9zaXQvY3JlYXRlIix7YW1vdW50Ok51bWJlcihhKX0pOwogIGlmKCFkKXJl"
    "dHVybjsKICBjb25zdCBtaW5zPWQud2luZG93fHw1OwogIGxldCBsZWZ0PU1hdGgubWF4KDAsTWF0"
    "aC5yb3VuZCgobmV3IERhdGUoZC5leHBpcmVzX2F0KS1uZXcgRGF0ZSgpKS8xMDAwKSl8fG1pbnMq"
    "NjA7CiAgY29uc3QgdG90YWw9bWlucyo2MDsKICBsZXQgZG9uZT1mYWxzZSxidXN5PWZhbHNlOwog"
    "IHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj4ke0NNUy5z"
    "aGVldFRpdGxlfSAke21vbmV5KGQuYW1vdW50KX08L2gzPgogICAgPHAgY2xhc3M9InN1YiI+JHtD"
    "TVMuc2hlZXRTdWIucmVwbGFjZSgie219IixtaW5zKX08L3A+CiAgICAke2QucXI/YDxkaXYgY2xh"
    "c3M9InFyIiBzdHlsZT0icGFkZGluZzowO292ZXJmbG93OmhpZGRlbiI+PGltZyBzcmM9IiR7ZC5x"
    "cn0iIGFsdD0iVVBJIFFSIiBzdHlsZT0id2lkdGg6MTAwJTtkaXNwbGF5OmJsb2NrIj48L2Rpdj5g"
    "OiIifQogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlVQSSBJRDwvc3Bhbj48Yj4ke2VzYyhkLnVw"
    "aXx8IiIpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5QYXllZTwvc3Bhbj48"
    "Yj4ke2VzYyhkLnBheWVlfHwiIil9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFu"
    "PkFtb3VudDwvc3Bhbj48Yj4ke21vbmV5KGQuYW1vdW50KX08L2I+PC9kaXY+CiAgICAke2QuYm9u"
    "dXM/YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Cb251czwvc3Bhbj48Yj4rJHttb25leShkLmJvbnVz"
    "KX08L2I+PC9kaXY+YDoiIn0KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Ob3RlIC8gcmVmZXJl"
    "bmNlPC9zcGFuPjxiPiR7ZXNjKGQuY29kZSl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3Yi"
    "PjxzcGFuPkV4cGlyZXMgaW48L3NwYW4+PGIgaWQ9ImRwY2QiPiR7TWF0aC5mbG9vcihsZWZ0LzYw"
    "KX06JHtTdHJpbmcobGVmdCU2MCkucGFkU3RhcnQoMiwiMCIpfTwvYj48L2Rpdj4KICAgIDxkaXYg"
    "Y2xhc3M9InRpbWVyYmFyIiBpZD0iZHBiYXIiPjxpIHN0eWxlPSJ3aWR0aDoxMDAlIj48L2k+PC9k"
    "aXY+CiAgICA8ZGl2IGNsYXNzPSJzdGVwcyIgaWQ9ImRzdGVwcyI+CiAgICAgIDxkaXYgY2xhc3M9"
    "InN0ZXAgb24iPjxpPjE8L2k+JHtDTVMuc3RlcDF9PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0"
    "ZXAiIGlkPSJzMiI+PGk+MjwvaT4ke0NNUy5zdGVwMn08L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0i"
    "c3RlcCIgaWQ9InMzIj48aT4zPC9pPiR7Q01TLnN0ZXAzfTwvZGl2PgogICAgICA8ZGl2IGNsYXNz"
    "PSJzdGVwIiBpZD0iczQiPjxpPjQ8L2k+JHtDTVMuc3RlcDR9PC9kaXY+CiAgICA8L2Rpdj4KICAg"
    "IDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij4KICAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIiBpZD0iZHBheSI+8J+PpiBDb3B5IFVQSSBJRDwvYnV0dG9uPgogICAgICA8"
    "YnV0dG9uIGNsYXNzPSJidG4gY3RhIiBpZD0iZHZlciI+JHtTVE9SRS5hdXRvY2hlY2s9PT1mYWxz"
    "ZT8i4pyFIEkgaGF2ZSBwYWlkIjoi4pyFICIrQ01TLnZlcmlmeUJ0bn08L2J1dHRvbj48L2Rpdj4K"
    "ICAgICR7U1RPUkUuYXV0b2NoZWNrPT09ZmFsc2U/IiI6YDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0"
    "IGJsb2NrIHNtIiBpZD0iZHV0ciIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij7wn6e+IFNlbmQgdHJh"
    "bnNhY3Rpb24gbm8uPC9idXR0b24+YH0KICAgIDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9j"
    "ayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+YCk7"
    "CiAgY29uc3QgZz1pZD0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoaWQpOwogIGNvbnN0IHBheUI9"
    "ZygiZHBheSIpLHZlckI9ZygiZHZlciIpLHV0ckI9ZygiZHV0ciIpOwogIGNvbnN0IGFsaXZlPSgp"
    "PT5nKCJkcGNkIikmJmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzaGVldCIpLmNsYXNzTGlzdC5j"
    "b250YWlucygib3BlbiIpOwogIGZ1bmN0aW9uIGV4cGlyZSgpewogICAgY2xlYXJJbnRlcnZhbChk"
    "ZXBUaWNrKTsKICAgIGlmKCFhbGl2ZSgpKXJldHVybjsKICAgIGcoImRwY2QiKS50ZXh0Q29udGVu"
    "dD0iMDowMCI7ZygiZHBiYXIiKS5jbGFzc0xpc3QuYWRkKCJsb3ciKTtnKCJkcGJhciIpLmZpcnN0"
    "Q2hpbGQuc3R5bGUud2lkdGg9IjAlIjsKICAgIHZlckIuZGlzYWJsZWQ9dHJ1ZTt1dHJCLmRpc2Fi"
    "bGVkPXRydWU7dmVyQi50ZXh0Q29udGVudD0iRXhwaXJlZCI7CiAgICBwYXlCLmNsYXNzTmFtZT0i"
    "YnRuIjtwYXlCLnRleHRDb250ZW50PSJOZXcgUmVxdWVzdCI7cGF5Qi5vbmNsaWNrPSgpPT5kZXBv"
    "c2l0U2hlZXQoYSk7CiAgICB0b2FzdChDTVMubXNnRXhwaXJlZCk7CiAgfQogIGRlcFRpY2s9c2V0"
    "SW50ZXJ2YWwoKCk9PnsKICAgIGlmKCFhbGl2ZSgpKXtjbGVhckludGVydmFsKGRlcFRpY2spO3Jl"
    "dHVybjt9CiAgICBsZWZ0LS07CiAgICBnKCJkcGNkIikudGV4dENvbnRlbnQ9TWF0aC5tYXgoMCxN"
    "YXRoLmZsb29yKGxlZnQvNjApKSsiOiIrU3RyaW5nKE1hdGgubWF4KDAsbGVmdCU2MCkpLnBhZFN0"
    "YXJ0KDIsIjAiKTsKICAgIGcoImRwYmFyIikuZmlyc3RDaGlsZC5zdHlsZS53aWR0aD0oTWF0aC5t"
    "YXgoMCxsZWZ0KS90b3RhbCoxMDApKyIlIjsKICAgIGcoImRwYmFyIikuY2xhc3NMaXN0LnRvZ2ds"
    "ZSgibG93IixsZWZ0PD02MCk7CiAgICBpZihsZWZ0PD0wKWV4cGlyZSgpOwogIH0sMTAwMCk7CiAg"
    "aWYocGF5QilwYXlCLm9uY2xpY2s9KCk9PnsKICAgIGlmKGRvbmUpcmV0dXJuOwogICAgZygiczIi"
    "KS5jbGFzc05hbWU9InN0ZXAgb24iO2coInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1biI7CiAgICB2"
    "ZXJCLmNsYXNzTGlzdC5yZW1vdmUoImdsYXNzIik7dmVyQi5jbGFzc0xpc3QuYWRkKCJjdGEiKTsK"
    "ICAgIHRyeXtuYXZpZ2F0b3IuY2xpcGJvYXJkJiZuYXZpZ2F0b3IuY2xpcGJvYXJkLndyaXRlVGV4"
    "dChkLnVwaXx8IiIpO31jYXRjaChlKXt9CiAgICB0b2FzdCgiVVBJIElEIGNvcGllZDogIisoZC51"
    "cGl8fCIiKSk7CiAgfTsKICBpZih2ZXJCKXZlckIub25jbGljaz1hc3luYygpPT57CiAgICBpZihk"
    "b25lfHxidXN5fHxsZWZ0PD0wKXJldHVybjsKICAgIGlmKFNUT1JFLmF1dG9jaGVjaz09PWZhbHNl"
    "KXsgICAgICAgICAgLyogbm8gYXV0b21hdGljIGNoZWNraW5nIOKAlCBjb2xsZWN0IHByb29mICov"
    "CiAgICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5T"
    "ZW5kIHlvdXIgcGF5bWVudCBwcm9vZjwvaDM+CiAgICAgICAgPHAgY2xhc3M9InN1YiI+QSBzY3Jl"
    "ZW5zaG90IG9mIHRoZSBzdWNjZXNzZnVsIHBheW1lbnQgYW5kIHRoZSBVVFIuPC9wPgogICAgICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+U2NyZWVu"
    "c2hvdDwvbGFiZWw+CiAgICAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9InBmc2hvdCIgdHlw"
    "ZT0iZmlsZSIgYWNjZXB0PSJpbWFnZS8qIj48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCI+PGxhYmVsPlVUUiAvIHRyYW5zYWN0aW9uIG51bWJlcjwvbGFiZWw+CiAgICAgICAgICA8aW5w"
    "dXQgY2xhc3M9ImlucCIgaWQ9InBmdXRyIiBpbnB1dG1vZGU9Im51bWVyaWMiIHBsYWNlaG9sZGVy"
    "PSIxMiBkaWdpdHMiPjwvZGl2PgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEgYmxvY2si"
    "IGlkPSJwZmdvIj5TdWJtaXQgZm9yIHJldmlldzwvYnV0dG9uPgogICAgICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNsb3Nl"
    "PkNhbmNlbDwvYnV0dG9uPmApOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicGZnbyIp"
    "Lm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICAgIGNvbnN0IGY9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5"
    "SWQoInBmc2hvdCIpOwogICAgICAgIGNvbnN0IHU9KGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJw"
    "ZnV0ciIpLnZhbHVlfHwiIikucmVwbGFjZSgvXEQvZywiIik7CiAgICAgICAgaWYodS5sZW5ndGg8"
    "Nil7dG9hc3QoIkVudGVyIHRoZSBVVFIgZnJvbSB5b3VyIHBheW1lbnQgYXBwIik7cmV0dXJuO30K"
    "ICAgICAgICBjb25zdCBmaWxlPShmJiZmLmZpbGVzJiZmLmZpbGVzWzBdKXx8bnVsbDsKICAgICAg"
    "ICBsZXQgc2hvdD0iIjsKICAgICAgICBpZihmaWxlKXsKICAgICAgICAgIGlmKGZpbGUuc2l6ZT44"
    "KjEwMjQqMTAyNCl7dG9hc3QoIlRoYXQgaW1hZ2UgaXMgdG9vIGxhcmdlICg4IE1CIG1heCkiKTty"
    "ZXR1cm47fQogICAgICAgICAgc2hvdD1hd2FpdCBuZXcgUHJvbWlzZShyZXM9Pntjb25zdCByZD1u"
    "ZXcgRmlsZVJlYWRlcigpOwogICAgICAgICAgICByZC5vbmxvYWQ9KCk9PnJlcyhTdHJpbmcocmQu"
    "cmVzdWx0fHwiIikpO3JkLm9uZXJyb3I9KCk9PnJlcygiIik7CiAgICAgICAgICAgIHJkLnJlYWRB"
    "c0RhdGFVUkwoZmlsZSk7fSk7CiAgICAgICAgfQogICAgICAgIGNvbnN0IGJ0bj1kb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgicGZnbyIpO2J0bi5kaXNhYmxlZD10cnVlO2J0bi50ZXh0Q29udGVudD0i"
    "U2VuZGluZ+KApiI7CiAgICAgICAgY29uc3QgcnI9YXdhaXQgYXBpVHJ5KCIvYXBpL2RlcG9zaXQv"
    "Y2xhaW0iLHtpZDpkLmlkLHV0cjp1LHNob3Q6c2hvdH0pOwogICAgICAgIGJ0bi5kaXNhYmxlZD1m"
    "YWxzZTtidG4udGV4dENvbnRlbnQ9IlN1Ym1pdCBmb3IgcmV2aWV3IjsKICAgICAgICBpZighcnIp"
    "cmV0dXJuOwogICAgICAgIGNsb3NlU2hlZXQoKTt0b2FzdCgiU2VudCDigJQgd2Ugd2lsbCBjb25m"
    "aXJtIGl0IHNob3J0bHkiKTsKICAgICAgICBhd2FpdCByZWZyZXNoKCk7CiAgICAgIH07CiAgICAg"
    "IHJldHVybjsKICAgIH0KICAgIGJ1c3k9dHJ1ZTt2ZXJCLmRpc2FibGVkPXRydWU7dmVyQi50ZXh0"
    "Q29udGVudD0iQ2hlY2tpbmfigKYiO2coInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1biI7CiAgICBj"
    "b25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9kZXBvc2l0L3ZlcmlmeSIse2lkOmQuaWR9KTsKICAg"
    "IGJ1c3k9ZmFsc2U7CiAgICBpZighYWxpdmUoKSlyZXR1cm47CiAgICB2ZXJCLmRpc2FibGVkPWZh"
    "bHNlO3ZlckIudGV4dENvbnRlbnQ9Q01TLnZlcmlmeUJ0bjsKICAgIGlmKCFyKXJldHVybjsKICAg"
    "IGlmKHIuc3RhdHVzPT09ImNvbXBsZXRlZCIpewogICAgICBkb25lPXRydWU7Y2xlYXJJbnRlcnZh"
    "bChkZXBUaWNrKTsKICAgICAgZygiczMiKS5jbGFzc05hbWU9InN0ZXAgb24iO2coInM0IikuY2xh"
    "c3NOYW1lPSJzdGVwIG9uIjsKICAgICAgaWYodHlwZW9mIHIuYmFsYW5jZT09PSJudW1iZXIiKUJB"
    "TD1yLmJhbGFuY2U7CiAgICAgIHN5bmNCYWwoKTtidXJzdCgpO3RvYXN0KENNUy5tc2dPayk7CiAg"
    "ICAgIHNldFRpbWVvdXQoKCk9PntjbG9zZVNoZWV0KCk7cmVmcmVzaCgpO30sOTAwKTsKICAgIH1l"
    "bHNlIGlmKHIuc3RhdHVzPT09InJldmlldyIpewogICAgICBnKCJzMyIpLmNsYXNzTmFtZT0ic3Rl"
    "cCBvbiI7dG9hc3QoIlBheW1lbnQgc2VudCBmb3IgY2hlY2tpbmcg4oCUIHdlIHdpbGwgY29uZmly"
    "bSBpdCBzaG9ydGx5Iik7CiAgICB9ZWxzZSBpZihyLnN0YXR1cz09PSJleHBpcmVkIil7CiAgICAg"
    "IGV4cGlyZSgpOwogICAgfWVsc2V7CiAgICAgIHRvYXN0KENNUy5tc2dOb25lKTsKICAgIH0KICB9"
    "OwogIGlmKHV0ckIpdXRyQi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHY9cHJvbXB0KCJF"
    "bnRlciB0aGUgMTItZGlnaXQgVVRSIGZyb20geW91ciBwYXltZW50IGFwcCIpOwogICAgaWYoIXYp"
    "cmV0dXJuOwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvZGVwb3NpdC91dHIiLHtpZDpk"
    "LmlkLHV0cjp2fSk7CiAgICBpZihyKXRvYXN0KCJUaGFua3Mg4oCUIHdlIGFyZSBjaGVja2luZyB5"
    "b3VyIHBheW1lbnQiKTsKICB9Owp9CmZ1bmN0aW9uIGRlcG9zaXRTaGVldChhKXsKICBpZihMSVZF"
    "KXJldHVybiBsaXZlRGVwb3NpdFNoZWV0KGEpOwogIGNsZWFySW50ZXJ2YWwoZGVwVGljayk7Y2xl"
    "YXJUaW1lb3V0KGRlcEpvYik7CiAgY29uc3QgY29kZT0oQ01TLnBheVByZWZpeHx8IlBBWSIpK01h"
    "dGguZmxvb3IoMTAwMCtNYXRoLnJhbmRvbSgpKjg5OTkpOwogIGNvbnN0IG1pbnM9TWF0aC5tYXgo"
    "MSxwYXJzZUludChDTVMucGF5V2luZG93LDEwKXx8NSksIHRvdGFsPW1pbnMqNjA7CiAgbGV0IGxl"
    "ZnQ9dG90YWwscGFpZD1mYWxzZSxkb25lPWZhbHNlLGJ1c3k9ZmFsc2U7CiAgc2hlZXQoYDxoMyBz"
    "dHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPiR7Q01TLnNoZWV0VGl0bGV9ICR7"
    "bW9uZXkoYSl9PC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPiR7Q01TLnNoZWV0U3ViLnJlcGxhY2Uo"
    "InttfSIsbWlucyl9PC9wPgogICAgPGRpdiBjbGFzcz0icXIiPlFSIENPREU8YnI+KGdlbmVyYXRl"
    "ZCBieSBib3QpPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VVBJIElEPC9zcGFuPjxi"
    "PiR7ZXNjKFNUT1JFLnVwaXx8Im5vdCBzZXQgeWV0Iil9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFz"
    "cz0ia3YiPjxzcGFuPlBheWVlPC9zcGFuPjxiPiR7ZXNjKFNUT1JFLnBheWVlfHwi4oCUIil9PC9i"
    "PjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkFtb3VudDwvc3Bhbj48Yj4ke21vbmV5"
    "KGEpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Ob3RlIC8gcmVmZXJlbmNl"
    "PC9zcGFuPjxiPiR7Y29kZX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+RXhw"
    "aXJlcyBpbjwvc3Bhbj48YiBpZD0iZHBjZCI+JHttaW5zfTowMDwvYj48L2Rpdj4KICAgIDxkaXYg"
    "Y2xhc3M9InRpbWVyYmFyIiBpZD0iZHBiYXIiPjxpIHN0eWxlPSJ3aWR0aDoxMDAlIj48L2k+PC9k"
    "aXY+CiAgICA8ZGl2IGNsYXNzPSJzdGVwcyIgaWQ9ImRzdGVwcyI+CiAgICAgIDxkaXYgY2xhc3M9"
    "InN0ZXAgb24iPjxpPjE8L2k+JHtDTVMuc3RlcDF9PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0"
    "ZXAiIGlkPSJzMiI+PGk+MjwvaT4ke0NNUy5zdGVwMn08L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0i"
    "c3RlcCIgaWQ9InMzIj48aT4zPC9pPiR7Q01TLnN0ZXAzfTwvZGl2PgogICAgICA8ZGl2IGNsYXNz"
    "PSJzdGVwIiBpZD0iczQiPjxpPjQ8L2k+JHtDTVMuc3RlcDR9PC9kaXY+CiAgICA8L2Rpdj4KICAg"
    "IDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij4KICAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIGN0YSIgaWQ9ImRwYXkiPiR7Q01TLnBheUJ0bn08L2J1dHRvbj4KICAgICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIGdsYXNzIiBpZD0iZHZlciI+JHtDTVMudmVyaWZ5QnRufTwvYnV0"
    "dG9uPjwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQgYmxvY2sgc20iIGlkPSJkdXRy"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPiR7Q01TLnV0ckJ0bn08L2J1dHRvbj4KICAgIDxidXR0"
    "b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRh"
    "LWNsb3NlPkNsb3NlPC9idXR0b24+YCk7CiAgY29uc3QgZz1pZD0+ZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoaWQpOwogIGNvbnN0IHBheUI9ZygiZHBheSIpLHZlckI9ZygiZHZlciIpLHV0ckI9Zygi"
    "ZHV0ciIpOwogIGZ1bmN0aW9uIGZtdChuKXtyZXR1cm4gTWF0aC5mbG9vcihuLzYwKSsiOiIrU3Ry"
    "aW5nKG4lNjApLnBhZFN0YXJ0KDIsIjAiKTt9CiAgZnVuY3Rpb24gYWxpdmUoKXtyZXR1cm4gZygi"
    "ZHBjZCIpJiZkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic2hlZXQiKS5jbGFzc0xpc3QuY29udGFp"
    "bnMoIm9wZW4iKTt9CiAgZnVuY3Rpb24gZXhwaXJlKCl7CiAgICBjbGVhckludGVydmFsKGRlcFRp"
    "Y2spO2NsZWFyVGltZW91dChkZXBKb2IpOwogICAgaWYoIWFsaXZlKCkpcmV0dXJuOwogICAgZygi"
    "ZHBjZCIpLnRleHRDb250ZW50PSIwOjAwIjtnKCJkcGJhciIpLmNsYXNzTGlzdC5hZGQoImxvdyIp"
    "O2coImRwYmFyIikuZmlyc3RDaGlsZC5zdHlsZS53aWR0aD0iMCUiOwogICAgcGF5Qi5kaXNhYmxl"
    "ZD10cnVlO3ZlckIuZGlzYWJsZWQ9dHJ1ZTt1dHJCLmRpc2FibGVkPXRydWU7CiAgICBnKCJzMiIp"
    "LmNsYXNzTmFtZT0ic3RlcCI7ZygiczMiKS5jbGFzc05hbWU9InN0ZXAiOwogICAgdmVyQi50ZXh0"
    "Q29udGVudD0iRXhwaXJlZCI7cGF5Qi5jbGFzc05hbWU9ImJ0biI7cGF5Qi5kaXNhYmxlZD1mYWxz"
    "ZTtwYXlCLnRleHRDb250ZW50PSJOZXcgUmVxdWVzdCI7CiAgICBwYXlCLm9uY2xpY2s9KCk9PmRl"
    "cG9zaXRTaGVldChhKTsKICAgIHRvYXN0KENNUy5tc2dFeHBpcmVkKTsKICB9CiAgZGVwVGljaz1z"
    "ZXRJbnRlcnZhbCgoKT0+ewogICAgaWYoIWFsaXZlKCkpe2NsZWFySW50ZXJ2YWwoZGVwVGljayk7"
    "cmV0dXJuO30KICAgIGxlZnQtLTtnKCJkcGNkIikudGV4dENvbnRlbnQ9Zm10KE1hdGgubWF4KDAs"
    "bGVmdCkpOwogICAgZygiZHBiYXIiKS5maXJzdENoaWxkLnN0eWxlLndpZHRoPShNYXRoLm1heCgw"
    "LGxlZnQpL3RvdGFsKjEwMCkrIiUiOwogICAgZygiZHBiYXIiKS5jbGFzc0xpc3QudG9nZ2xlKCJs"
    "b3ciLGxlZnQ8PTYwKTsKICAgIGlmKGxlZnQ8PTApZXhwaXJlKCk7CiAgfSwxMDAwKTsKICBpZihw"
    "YXlCKXBheUIub25jbGljaz0oKT0+ewogICAgaWYoZG9uZSlyZXR1cm47CiAgICBwYWlkPXRydWU7"
    "ZygiczIiKS5jbGFzc05hbWU9InN0ZXAgb24iO2coInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1biI7"
    "CiAgICBwYXlCLnRleHRDb250ZW50PSJQYXkgQWdhaW4iO3ZlckIuY2xhc3NMaXN0LnJlbW92ZSgi"
    "Z2xhc3MiKTt2ZXJCLmNsYXNzTGlzdC5hZGQoImN0YSIpOwogICAgdG9hc3QoIk9wZW5pbmcgeW91"
    "ciBVUEkgYXBwIOKAlCBwYXkgIittb25leShhKSsiLCB0aGVuIHRhcCBWZXJpZnkiKTsKICB9Owog"
    "IHZlckIub25jbGljaz0oKT0+ewogICAgaWYoZG9uZXx8YnVzeXx8bGVmdDw9MClyZXR1cm47CiAg"
    "ICBidXN5PXRydWU7dmVyQi5kaXNhYmxlZD10cnVlO3ZlckIudGV4dENvbnRlbnQ9IkNoZWNraW5n"
    "4oCmIjsKICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1biI7dG9hc3QoQ01TLm1zZ0NoZWNr"
    "aW5nKTsKICAgIGRlcEpvYj1zZXRUaW1lb3V0KCgpPT57CiAgICAgIGJ1c3k9ZmFsc2U7CiAgICAg"
    "IGlmKCFhbGl2ZSgpKXJldHVybjsKICAgICAgaWYocGFpZCl7CiAgICAgICAgZG9uZT10cnVlO2Ns"
    "ZWFySW50ZXJ2YWwoZGVwVGljayk7CiAgICAgICAgZygiczMiKS5jbGFzc05hbWU9InN0ZXAgb24i"
    "O2coInM0IikuY2xhc3NOYW1lPSJzdGVwIG9uIjsKICAgICAgICB2ZXJCLnRleHRDb250ZW50PSJW"
    "ZXJpZmllZCI7CiAgICAgICAgQkFMKz1OdW1iZXIoYSk7c3luY0JhbCgpO2J1cnN0KCk7dG9hc3Qo"
    "Q01TLm1zZ09rKTsKICAgICAgICBzZXRUaW1lb3V0KGNsb3NlU2hlZXQsMTIwMCk7CiAgICAgIH1l"
    "bHNlewogICAgICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIjt2ZXJCLmRpc2FibGVkPWZhbHNl"
    "O3ZlckIudGV4dENvbnRlbnQ9Q01TLnZlcmlmeUJ0bjsKICAgICAgICB0b2FzdChDTVMubXNnTm9u"
    "ZSk7CiAgICAgIH0KICAgIH0sMTcwMCk7CiAgfTsKICB1dHJCLm9uY2xpY2s9KCk9PntpZihsZWZ0"
    "PjApdG9hc3QoIlRoYW5rcyDigJQgd2UgYXJlIGNoZWNraW5nIHlvdXIgcGF5bWVudCIpO307Cn0K"
    "ZnVuY3Rpb24gc3luY0JhbCgpe3RpY2tUbyhkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYmFsIiks"
    "QkFMKTt0aWNrVG8oZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJhbDIiKSxCQUwpO30KZG9jdW1l"
    "bnQuYWRkRXZlbnRMaXN0ZW5lcigiY2xpY2siLGU9Pntjb25zdCBjPWUudGFyZ2V0LmNsb3Nlc3Qo"
    "IltkYXRhLWNvbmZpcm1dIik7aWYoIWMpcmV0dXJuOwogIGNvbnN0IHA9K2MuZGF0YXNldC5jb25m"
    "aXJtO2lmKEJBTDxwKXt0b2FzdCgiTm90IGVub3VnaCBiYWxhbmNlIOKAlCBhZGQgZnVuZHMiKTty"
    "ZXR1cm59CiAgQkFMLT1wO3N5bmNCYWwoKTtjbG9zZVNoZWV0KCk7YnVyc3QoKTt0b2FzdCgiUHVy"
    "Y2hhc2VkIOKAlCBPVFAgaXMgaW4gT3JkZXJzIik7fSk7CgovKiDilIDilIAgQURNSU4g4pSA4pSA"
    "ICovCmxldCBBVEFCPSJkYXNoIjsKZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI2FzdWIgYnV0"
    "dG9uIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "QWxsKCIjYXN1YiBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnJlbW92ZSgib24iKSk7"
    "Yi5jbGFzc0xpc3QuYWRkKCJvbiIpOwogIEFUQUI9Yi5kYXRhc2V0LmE7cmVuZGVyQWRtaW4oKTt3"
    "aW5kb3cuc2Nyb2xsVG8oMCwwKTt9KTsKZnVuY3Rpb24gcmVuZGVyQWRtaW4oKXsKICBjb25zdCBz"
    "dD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYXN0YWdlIik7CiAgaWYoIWFkbWluQWxsb3dlZCgp"
    "KXtzdC5pbm5lckhUTUw9JzxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJtYXJnaW46MTZweDt0ZXh0"
    "LWFsaWduOmNlbnRlciI+JwogICAgKyc8aDMgc3R5bGU9Im1hcmdpbjowIDAgNnB4Ij5BZG1pbnMg"
    "b25seTwvaDM+JwogICAgKyc8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPlRoaXMgcGFu"
    "ZWwgaXMgbm90IGF2YWlsYWJsZSBvbiB5b3VyIGFjY291bnQuPC9wPjwvZGl2Pic7cmV0dXJuO30K"
    "ICBzdC5pbm5lckhUTUw9KHtkYXNoOmFEYXNoLGNoYW5uZWxzOmFDaGFubmVscyxsb2dzOmFMb2dz"
    "LHBheTphUGF5LGNvbnRlbnQ6YUNvbnRlbnQsZmVhdHVyZXM6YUZlYXR1cmVzLG1hcmtldDphTWFy"
    "a2V0LGFuYWx5dGljczphQW5hbHl0aWNzLHNlY3VyaXR5OmFTZWN1cml0eSxzdG9jazphU3RvY2ss"
    "b3JkZXJzOmFPcmRlcnMsdXNlcnM6YVVzZXJzLHNldHRpbmdzOmFTZXR0aW5nc30pW0FUQUJdKCk7"
    "CiAgYmluZEFkbWluKCk7cmV2ZWFsU3RhZ2Uoc3QpO2JpbmRUaWx0KCk7aWYod2luZG93Ll9fdnRh"
    "YlBsYWNlKXJlcXVlc3RBbmltYXRpb25GcmFtZSh3aW5kb3cuX192dGFiUGxhY2UpO30KCmZ1bmN0"
    "aW9uIGFEYXNoKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJrcGlzIj4ke1tbIlJldmVudWUgdG9k"
    "YXkiLG1vbmV5KEFETS5rcGkucmV2KV0sWyJPcmRlcnMgdG9kYXkiLGFkbU51bShBRE0ua3BpLm9y"
    "ZGVycyldLAogICAgICBbIlVzZXJzIixhZG1OdW0oQURNLmtwaS51c2VycyldLFsiQXV0by12ZXJp"
    "ZmllZCIsYWRtTnVtKEFETS5rcGkuYXV0byldXQogICAgLm1hcCgoW2ssdl0pPT5gPGRpdiBjbGFz"
    "cz0ia3BpIj48c21hbGw+JHtrfTwvc21hbGw+PGI+JHt2fTwvYj48L2Rpdj5gKS5qb2luKCIiKX08"
    "L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGln"
    "bi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBj"
    "bGFzcz0iZG90Ij48L2k+UGF5bWVudCBjaGVja2luZzwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJm"
    "bGV4OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj4ke0FE"
    "TS5wYXkuZ21haWxfc2V0PyJHbWFpbCB3YXRjaGVyIG9uIMK3ICIrQURNLnBheS5wZW5kaW5nKyIg"
    "YXdhaXRpbmcgcmV2aWV3IjoiR21haWwgbm90IHNldCDigJQgZGVwb3NpdHMgZ28gdG8gbWFudWFs"
    "IHJldmlldyJ9PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtYS1n"
    "bz0icGF5Ij5PcGVuPC9idXR0b24+PC9kaXY+CiAgIDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5MYXN0"
    "IDcgZGF5czwvaDM+PHAgY2xhc3M9InN1YiI+UmV2ZW51ZSB0cmVuZDwvcD4KICAgICA8ZGl2IHN0"
    "eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6ZmxleC1lbmQ7Z2FwOjdweDtoZWlnaHQ6OTZw"
    "eDttYXJnaW4tdG9wOjE0cHgiPgogICAgICAgJHtBRE0uYmFycy5tYXAoaD0+YDxpIHN0eWxlPSJm"
    "bGV4OjE7aGVpZ2h0OiR7aH0lO2JvcmRlci1yYWRpdXM6N3B4IDdweCAzcHggM3B4O2JhY2tncm91"
    "bmQ6dmFyKC0tcGlsbGdyYWQpO2Rpc3BsYXk6YmxvY2siPjwvaT5gKS5qb2luKCIiKX08L2Rpdj48"
    "L2Rpdj4KICAgJHtHKCJkaWdlc3QiLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlz"
    "cGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9"
    "InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5EaWdlc3Q8L3NwYW4+CiAgICAgPGRpdiBzdHls"
    "ZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+"
    "TmV4dCBzdW1tYXJ5IGF0IDIzOjMwPC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNv"
    "ZnQiIGRhdGEtYS1nbz0iYW5hbHl0aWNzIj5WaWV3PC9idXR0b24+PC9kaXY+YCl9CiAgICR7Rygi"
    "c2FuZGJveCIsYDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxp"
    "Z24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0icGlsbCI+U2FuZGJv"
    "eDwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43"
    "NHJlbTtjb2xvcjp2YXIoLS10eDIpIj4ke0FETS5zZXR0aW5ncy5zYW5kYm94PyJUZXN0IG1vZGUg"
    "aXMgT04iOiJUZXN0IG1vZGUgaXMgb2ZmIOKAlCByZWFsIG1vbmV5IGlzIGxpdmUifTwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuc2FuZGJveCk/IiBvbiI6IiJ9IiBk"
    "YXRhLXNldHRpbmc9InNhbmRib3giPjwvZGl2PjwvZGl2PmApfQogICA8ZGl2IGNsYXNzPSJzZWMi"
    "PjxoMj5RdWljayBBY3Rpb25zPC9oMj48L2Rpdj4KICAgPGRpdiBjbGFzcz0iZ3JpZCI+CiAgICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtcWE9ImJyb2FkY2FzdCI+QnJvYWRjYXN0PC9i"
    "dXR0b24+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtcWE9InN0b2NrIj5BZGQg"
    "U3RvY2s8L2J1dHRvbj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0YS1xYT0iZmxh"
    "c2giPkZsYXNoIFNhbGU8L2J1dHRvbj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0"
    "YS1xYT0iYmFja3VwIj5CYWNrdXAgREI8L2J1dHRvbj48L2Rpdj5gOwp9CgpmdW5jdGlvbiBhQ2hh"
    "bm5lbHMoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Gb3JjZS1qb2luIGNoYW5u"
    "ZWxzPC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPkFkZCBvciByZW1vdmUgYW55IG51bWJlciBvZiBj"
    "aGFubmVscy4gU2F2ZWQgdG8gdGhlIGRhdGFiYXNlIOKAlCBpbnN0YW50LCBubyByZXN0YXJ0Ljwv"
    "cD4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxM3B4Ij48bGFiZWw+"
    "Q2hhbm5lbCBsaW5rLCBAdXNlcm5hbWUgb3IgLTEwMCBJRDwvbGFiZWw+CiAgICAgIDxpbnB1dCBj"
    "bGFzcz0iaW5wIiBpZD0iY2gtaW4iIHBsYWNlaG9sZGVyPSJAY2hhbm5lbCDCtyB0Lm1lLytpbnZp"
    "dGUgwrcgLTEwMOKApiI+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlR5cGU8"
    "L2xhYmVsPgogICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIGlkPSJjaC10eXBlIj48b3B0aW9uIHZh"
    "bHVlPSIxIj5SZXF1aXJlZCDigJQgbXVzdCBqb2luPC9vcHRpb24+PG9wdGlvbiB2YWx1ZT0iMCI+"
    "T3B0aW9uYWwg4oCUIGp1c3Qgc3VnZ2VzdGVkPC9vcHRpb24+PC9zZWxlY3Q+PC9kaXY+CiAgICA8"
    "ZGl2IGNsYXNzPSJidG5yb3ciPjxidXR0b24gY2xhc3M9ImJ0biIgaWQ9ImNoLWFkZCI+JHtpYygi"
    "cGx1cyIpfSBBZGQgQ2hhbm5lbDwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0"
    "IiBkYXRhLW1pc2M9ImNoYW5uZWx0ZXN0Ij5UZXN0IEJvdDwvYnV0dG9uPjwvZGl2PjwvZGl2Pgog"
    "IDxkaXYgY2xhc3M9InNlYyI+PGgyPkFjdGl2ZSAoJHtDSEFOTkVMUy5sZW5ndGh9KTwvaDI+PC9k"
    "aXY+CiAgJHtDSEFOTkVMUy5tYXAoKGMsaSk9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0Ij4KICAg"
    "ICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4K"
    "ICAgICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoInNlbmQiKX08L2Rpdj4KICAgICAgIDxkaXYg"
    "c3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODNyZW07ZGlz"
    "cGxheTpibG9jazt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZs"
    "b3c6ZWxsaXBzaXMiPiR7ZXNjKGMubmFtZSl9PC9iPgogICAgICAgICA8c21hbGwgc3R5bGU9ImNv"
    "bG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+JHtlc2MoYy5oYW5kbGUpfSDCtyAke2Mu"
    "bWVtYmVyc30gbWVtYmVyczwvc21hbGw+PC9kaXY+CiAgICAgICA8ZGl2IGNsYXNzPSJ0ZyBzbSAk"
    "e2Mub24/Im9uIjoiIn0iIGRhdGEtY2hvbj0iJHtjLmlkfSI+PC9kaXY+PC9kaXY+CiAgICAgPGRp"
    "diBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo2cHg7bWFyZ2luLXRvcDoxMHB4O2ZsZXgtd3JhcDp3"
    "cmFwIj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyAke2MucmVxPyIiOiJzb2Z0In0iIGRh"
    "dGEtY2hyZXE9IiR7Yy5pZH0iPiR7Yy5yZXE/IlJlcXVpcmVkIjoiT3B0aW9uYWwifTwvYnV0dG9u"
    "PgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtY29weT0idGV4dCIgZGF0"
    "YS1jb3B5LXZhbHVlPSIke2VzYyhjLmhhbmRsZSl9Ij5Db3B5PC9idXR0b24+CiAgICAgICA8YnV0"
    "dG9uIGNsYXNzPSJidG4geHMgYmFkIiBzdHlsZT0ibWFyZ2luLWxlZnQ6YXV0byIgZGF0YS1jaGRl"
    "bD0iJHtjLmlkfSI+UmVtb3ZlPC9idXR0b24+PC9kaXY+PC9kaXY+YCkuam9pbigiIikKICAgfHwn"
    "PGRpdiBjbGFzcz0iY2FyZCI+PHAgY2xhc3M9InN1YiI+Tm8gY2hhbm5lbHMg4oCUIHVzZXJzIGNh"
    "biBlbnRlciBmcmVlbHkuPC9wPjwvZGl2Pid9YDsKfQoKZnVuY3Rpb24gYUxvZ3MoKXsKICBjb25z"
    "dCBvcHRzPWlkPT5MT0dDSC5tYXAobD0+YDxvcHRpb24gdmFsdWU9IiR7bC5pZH0iICR7bC5pZD09"
    "PWlkPyJzZWxlY3RlZCI6IiJ9PiR7ZXNjKGwubmFtZSl9PC9vcHRpb24+YCkuam9pbigiIik7CiAg"
    "cmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGlu"
    "Zy10b3A6MCI+CiAgICAgIDxkaXYgY2xhc3M9InQiPkxvZ2dpbmcgbWFzdGVyIHN3aXRjaDxzbWFs"
    "bD5PZmYgPSB0aGUgYm90IHBvc3RzIG5vIGxvZ3MgYW55d2hlcmU8L3NtYWxsPjwvZGl2PgogICAg"
    "ICA8ZGl2IGNsYXNzPSJ0ZyAke0xPR1NfTUFTVEVSPyJvbiI6IiJ9IiBkYXRhLW1hc3Rlcj48L2Rp"
    "dj48L2Rpdj48L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxvZyBjaGFubmVsczwvaDM+"
    "PHAgY2xhc3M9InN1YiI+QWRkIGFzIG1hbnkgYXMgeW91IGxpa2UuIFRoZSBzdGFycmVkIG9uZSBp"
    "cyB0aGUgcHJlZmVycmVkL2RlZmF1bHQgY2hhbm5lbC48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmll"
    "bGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5DaGFubmVsIGxpbmssIEB1c2VybmFt"
    "ZSBvciAtMTAwIElEPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0ibGctaW4i"
    "IHBsYWNlaG9sZGVyPSJAbG9nc19jaGFubmVsIMK3IC0xMDDigKYiPjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0ibGctYWRkIj4ke2ljKCJwbHVz"
    "Iil9IEFkZCBMb2cgQ2hhbm5lbDwvYnV0dG9uPgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0"
    "IiBkYXRhLW1pc2M9ImxvZ3Rlc3QiPlNlbmQgVGVzdDwvYnV0dG9uPjwvZGl2PjwvZGl2PgogICAk"
    "e0xPR0NILm1hcChsPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6Zmxl"
    "eDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICA8ZGl2IGNsYXNzPSJzdGFyICR7"
    "bC5wcmltYXJ5PyJvbiI6IiJ9IiBkYXRhLWxnc3Rhcj0iJHtsLmlkfSI+JHtpYygic3RhciIpfTwv"
    "ZGl2PgogICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250"
    "LXNpemU6LjgzcmVtO2Rpc3BsYXk6YmxvY2s7d2hpdGUtc3BhY2U6bm93cmFwO292ZXJmbG93Omhp"
    "ZGRlbjt0ZXh0LW92ZXJmbG93OmVsbGlwc2lzIj4ke2VzYyhsLm5hbWUpfTwvYj4KICAgICAgICA8"
    "c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+JHtlc2MobC5o"
    "YW5kbGUpfSR7bC5wcmltYXJ5PyIgwrcgcHJlZmVycmVkIjoiIn08L3NtYWxsPjwvZGl2PgogICAg"
    "ICA8ZGl2IGNsYXNzPSJ0ZyBzbSAke2wub24/Im9uIjoiIn0iIGRhdGEtbGdvbj0iJHtsLmlkfSI+"
    "PC9kaXY+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBiYWQiIGRhdGEtbGdkZWw9IiR7bC5p"
    "ZH0iPlJlbW92ZTwvYnV0dG9uPjwvZGl2PmApLmpvaW4oIiIpCiAgICB8fCc8ZGl2IGNsYXNzPSJj"
    "YXJkIj48cCBjbGFzcz0ic3ViIj5ObyBsb2cgY2hhbm5lbHMgeWV0LjwvcD48L2Rpdj4nfQogICA8"
    "ZGl2IGNsYXNzPSJzZWMiPjxoMj5FdmVudHMgJmFtcDsgcm91dGluZzwvaDI+PGEgZGF0YS1ldi1h"
    "bGw+VG9nZ2xlIGFsbDwvYT48L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+JHtFVkVOVFMubWFw"
    "KChlLGkpPT5gCiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCIgc3R5bGU9ImZs"
    "ZXg6MSI+JHtlc2MoZS5rKX0KICAgICAgIDxzbWFsbD48c2VsZWN0IGNsYXNzPSJpbnAiIHN0eWxl"
    "PSJoZWlnaHQ6MzBweDtmb250LXNpemU6LjdyZW07bWFyZ2luLXRvcDo1cHg7bWF4LXdpZHRoOjE5"
    "MHB4IiBkYXRhLWV2Y2g9IiR7aX0iICR7ZS5vbj8iIjoiZGlzYWJsZWQifT4ke29wdHMoZS5jaCl9"
    "PC9zZWxlY3Q+PC9zbWFsbD48L2Rpdj4KICAgICAgIDxkaXYgY2xhc3M9InRnICR7ZS5vbj8ib24i"
    "OiIifSIgZGF0YS1ldm9uPSIke2l9Ij48L2Rpdj48L2Rpdj5gKS5qb2luKCIiKX08L2Rpdj4KICAg"
    "PGRpdiBjbGFzcz0iY2FyZCI+PGgzPlJlY2VudCBsb2cgb3V0cHV0PC9oMz4KICAgICAkeygoQURN"
    "LmxvZ2ZlZWQmJkFETS5sb2dmZWVkLmxlbmd0aCk/QURNLmxvZ2ZlZWQubWFwKGw9PltsLndoYXQs"
    "bC53aGVyZSxsLndoZW5dKTpbXSkKICAgICAgIC5tYXAoKFt0LGMsd10pPT5gPGRpdiBjbGFzcz0i"
    "a3YiPjxzcGFuPiR7dH08L3NwYW4+PGIgc3R5bGU9ImZvbnQtc2l6ZTouN3JlbSI+JHtjfSDCtyAk"
    "e3d9PC9iPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PmA7Cn0KCmZ1bmN0aW9uIGFQYXkoKXsKICBy"
    "ZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5n"
    "LXRvcDowIj4KICAgICAgPGRpdiBjbGFzcz0idCI+R21haWwgYXV0by12ZXJpZnk8c21hbGw+JHtB"
    "RE0ucGF5LmdtYWlsX3NldD8iUmVhZGluZyAiK2VzYyhBRE0ucGF5LmdtYWlsKToiTm90IGNvbmZp"
    "Z3VyZWQg4oCUIGRlcG9zaXRzIHF1ZXVlIGZvciByZXZpZXcifTwvc21hbGw+PC9kaXY+CiAgICAg"
    "IDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuZ21haWxfZW5hYmxlZCk/IiBvbiI6IiJ9"
    "IiBkYXRhLXNldHRpbmc9ImdtYWlsX2VuYWJsZWQiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xh"
    "c3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPkF1dG8tYXBwcm92ZSBzbWFsbCBkZXBvc2l0czxzbWFs"
    "bD5Ta2lwIHJldmlldyBiZWxvdyB0aGUgdGhyZXNob2xkPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmdtYWlsX2VuYWJsZWQpPyIgb24iOiIifSIgZGF0"
    "YS1zZXR0aW5nPSJnbWFpbF9lbmFibGVkIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJz"
    "d2FwIj48ZGl2IGNsYXNzPSJ0Ij5NYW51YWwgVVRSIGZhbGxiYWNrPHNtYWxsPkxldCB1c2VycyBz"
    "dWJtaXQgYSBVVFIgaWYgYXV0by12ZXJpZnkgbWlzc2VzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmF1dG9kZXApPyIgb24iOiIifSIgZGF0YS1zZXR0"
    "aW5nPSJhdXRvZGVwIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNs"
    "YXNzPSJ0Ij5WZXJpZnkgb25seSB3aGVuIHVzZXIgdGFwcyBWZXJpZnk8c21hbGw+Tm8gYmFja2dy"
    "b3VuZCBjaGVja2luZyDigJQgc2F2ZXMgR21haWwgcXVvdGE8L3NtYWxsPjwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cudXRyX2ZhbGxiYWNrKT8iIG9uIjoiIn0iIGRh"
    "dGEtc2V0dGluZz0idXRyX2ZhbGxiYWNrIj48L2Rpdj48L2Rpdj48L2Rpdj4KICAgPGRpdiBjbGFz"
    "cz0iY2FyZCI+PGgzPlVQSTwvaDM+PHAgY2xhc3M9InN1YiI+JHtBRE0ucGF5LnVwaT8iSW4gdXNl"
    "IGJ5IGV2ZXJ5IHBheW1lbnQgcmVxdWVzdC4iOiJOb3Qgc2V0IHlldCDigJQgdG9wLXVwcyBhcmUg"
    "ZGlzYWJsZWQgdW50aWwgeW91IGFkZCBpdC4ifTwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlVQSSBJRDwvbGFiZWw+CiAgICAgICA8aW5w"
    "dXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9InVwaV9pZCIgdmFsdWU9IiR7ZXNjKEFETS5wYXkudXBp"
    "fHwiIil9IiBwbGFjZWhvbGRlcj0ieW91cmhhbmRsZUBiYW5rIj48L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJmaWVsZCI+PGxhYmVsPlBheWVlIG5hbWU8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNz"
    "PSJpbnAiIGRhdGEtc2V0PSJ1cGlfbmFtZSIgdmFsdWU9IiR7ZXNjKEFETS5wYXkucGF5ZWV8fCIi"
    "KX0iIHBsYWNlaG9sZGVyPSJZb3VyIFN0b3JlIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCI+PGxhYmVsPk5vdGUgcHJlZml4PC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBk"
    "YXRhLXNldD0icGF5X3ByZWZpeCIgdmFsdWU9IiR7ZXNjKEFETS5wYXkucHJlZml4fHwiUEFZIil9"
    "Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlBheW1lbnQgd2luZG93ICht"
    "aW51dGVzKTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9InBheV93"
    "aW5kb3ciIHZhbHVlPSIke0FETS5wYXkud2luZG93fSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "ZmllbGQiPjxsYWJlbD5NaW5pbXVtIGRlcG9zaXQ8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNz"
    "PSJpbnAiIGRhdGEtc2V0PSJtaW5fZGVwb3NpdCIgdmFsdWU9IiR7QURNLnBheS5taW59Ij48L2Rp"
    "dj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPk1heGltdW0gZGVwb3NpdDwvbGFiZWw+"
    "CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9Im1heF9kZXBvc2l0IiB2YWx1ZT0i"
    "JHtBRE0ucGF5Lm1heH0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+QXV0"
    "by1hcHByb3ZlIHVuZGVyICgwID0gb2ZmKTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9Imlu"
    "cCIgZGF0YS1zZXQ9ImF1dG9kZXBfbWF4IiB2YWx1ZT0iJHtBRE0ucGF5LmF1dG9kZXB9Ij48L2Rp"
    "dj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkdtYWlsIGFkZHJlc3M8L2xhYmVsPgog"
    "ICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJnbWFpbF9pZCIgdmFsdWU9IiR7ZXNj"
    "KEFETS5wYXkuZ21haWx8fCIiKX0iIHBsYWNlaG9sZGVyPSJzdG9yZUBnbWFpbC5jb20iPjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+R21haWwgYXBwIHBhc3N3b3JkPC9sYWJl"
    "bD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0iZ21haWxfcGFzcyIgdHlwZT0i"
    "cGFzc3dvcmQiIHBsYWNlaG9sZGVyPSIke0FETS5wYXkuZ21haWxfc2V0PyJzYXZlZCDigJQgdHlw"
    "ZSB0byByZXBsYWNlIjoibm90IHNldCJ9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNw"
    "YW4+V2FpdGluZyBmb3IgcmV2aWV3PC9zcGFuPjxiPiR7QURNLnBheS5wZW5kaW5nfTwvYj48L2Rp"
    "dj48L2Rpdj4KICAgJHtHKCJjcnlwdG8iLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+Q3J5cHRvIHdh"
    "bGxldDwvaDM+PHAgY2xhc3M9InN1YiI+VVNEVC1UUkMyMCBkZXBvc2l0cywgYXV0by1jb25maXJt"
    "ZWQgb24tY2hhaW4uPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRv"
    "cDoxMHB4Ij48bGFiZWw+V2FsbGV0IGFkZHJlc3M8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiBw"
    "bGFjZWhvbGRlcj0iVOKApiI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5D"
    "b25maXJtYXRpb25zIHJlcXVpcmVkPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEi"
    "PjwvZGl2PgogICAgIDwvZGl2PmApfQogICAke0coInVwaVJvdGF0ZSIsYDxkaXYgY2xhc3M9ImNh"
    "cmQiPjxoMz5VUEkgcm90YXRpb248L2gzPjxwIGNsYXNzPSJzdWIiPlNwcmVhZCBsb2FkIGFuZCBz"
    "dXJ2aXZlIGEgZnJvemVuIGhhbmRsZS48L3A+CiAgICAgJHtbWyJoYW5kbGUxQGZhbSIsIkFjdGl2"
    "ZSJdLFsiaGFuZGxlMkBmYW0iLCJBY3RpdmUiXSxbImhhbmRsZTNAdXBpIiwiUGF1c2VkIl1dCiAg"
    "ICAgICAubWFwKChbaCxzdF0pPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7aH08L3NwYW4+PGI+"
    "PHNwYW4gY2xhc3M9InBpbGwgJHtzdD09PSJBY3RpdmUiPyJvbiI6Im9mZiJ9Ij4ke3N0fTwvc3Bh"
    "bj48L2I+PC9kaXY+YCkuam9pbigiIil9CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJt"
    "YXJnaW4tdG9wOjEwcHgiPjxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4Ij4KICAgICAg"
    "IDxpbnB1dCBjbGFzcz0iaW5wIiBwbGFjZWhvbGRlcj0iYW5vdGhlckB1cGkiPjxidXR0b24gY2xh"
    "c3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJ1cGlhZGQiPkFkZDwvYnV0dG9u"
    "PjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImRlcEJvbnVzIixgPGRpdiBjbGFzcz0iY2Fy"
    "ZCI+PGgzPkRlcG9zaXQgYm9udXM8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDo4cHgiPjxsYWJlbD7igrk1MDAgYW5kIGFib3ZlICglKTwvbGFiZWw+PGlucHV0"
    "IGNsYXNzPSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxh"
    "YmVsPuKCuTEwMDAgYW5kIGFib3ZlICglKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVl"
    "PSI4Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPuKCuTIwMDAgYW5kIGFi"
    "b3ZlICglKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxMiI+PC9kaXY+PC9kaXY+"
    "YCl9CiAgICR7RygiZGVwTGltaXRzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkRlcG9zaXQgbGlt"
    "aXRzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48"
    "bGFiZWw+TWluaW11bSAo4oK5KTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxMCI+"
    "PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5NYXhpbXVtICjigrkpPC9sYWJl"
    "bD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjIwMDAwIj48L2Rpdj48L2Rpdj5gKX0KICAgJHtH"
    "KCJ3cm9uZ0FtdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJw"
    "YWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5Xcm9uZy1hbW91bnQgaGFuZGxpbmc8c21hbGw+"
    "Q3JlZGl0IHdoYXQgd2FzIGFjdHVhbGx5IHBhaWQgYW5kIGZsYWcgaXQ8L3NtYWxsPjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cudmVyaWZ5X29uX3RhcCk/IiBvbiI6"
    "IiJ9IiBkYXRhLXNldHRpbmc9InZlcmlmeV9vbl90YXAiPjwvZGl2PjwvZGl2PjwvZGl2PmApfQog"
    "ICAke0coInNwbGl0UGF5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5"
    "bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPlNwbGl0IHBheW1lbnQ8c21hbGw+V2Fs"
    "bGV0IGJhbGFuY2UgcGx1cyBVUEkgaW4gb25lIGNoZWNrb3V0PC9zbWFsbD48L2Rpdj4KICAgICA8"
    "ZGl2IGNsYXNzPSJ0ZyR7RkVBVC53cm9uZ0FtdD8iIG9uIjoiIn0iIGRhdGEtZmVhdD0id3JvbmdB"
    "bXQiPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImF1dG9SZWZ1bmQiLGA8ZGl2IGNsYXNz"
    "PSJjYXJkIj48aDM+QXV0by1yZWZ1bmQ8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5XYXJyYW50eSB3aW5kb3cgKG1pbnV0ZXMpPC9sYWJl"
    "bD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4+UmVmdW5kZWQgdG9kYXk8L3NwYW4+PGI+4oK5MTA1LjAwIMK3IDMgb3JkZXJzPC9i"
    "PjwvZGl2PjwvZGl2PmApfQogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+R21haWwgYWNjb3VudDwv"
    "aDM+PHAgY2xhc3M9InN1YiI+VGhlIEdtYWlsIHRoYXQgcmVjZWl2ZXMgeW91ciBGYW1QYXkgcGF5"
    "bWVudCBtYWlscy4gTm90aGluZyBpcyBzdG9yZWQgaW4gY29kZS48L3A+CiAgICAgPGRpdiBjbGFz"
    "cz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5HbWFpbCBJRDwvbGFiZWw+"
    "PGlucHV0IGNsYXNzPSJpbnAiIHBsYWNlaG9sZGVyPSJ5b3Vyc3RvcmVAZ21haWwuY29tIj48L2Rp"
    "dj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkdtYWlsIHBhc3N3b3JkPC9sYWJlbD48"
    "aW5wdXQgY2xhc3M9ImlucCIgdHlwZT0icGFzc3dvcmQiIHBsYWNlaG9sZGVyPSLigKLigKLigKLi"
    "gKLigKLigKLigKLigKLigKLigKLigKLigKIiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImJ0bnJv"
    "dyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBkYXRhLWFkbWluLWFjdD0iZ21haWx0ZXN0Ij5UZXN0IEdt"
    "YWlsPC9idXR0b24+CiAgICAgPC9kaXY+CiAgICAgPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdp"
    "bi10b3A6MTBweCI+VGlwOiBpZiAyLXN0ZXAgdmVyaWZpY2F0aW9uIGlzIG9uLCBwYXN0ZSBhIEdv"
    "b2dsZSBBcHAgUGFzc3dvcmQgaGVyZSBpbnN0ZWFkIG9mIHlvdXIgbm9ybWFsIG9uZS48L3A+PC9k"
    "aXY+CiAgIDxkaXYgY2xhc3M9InNlYyI+PGgyPlJlY2VudCBhdXRvLXZlcmlmaWNhdGlvbnM8L2gy"
    "PjwvZGl2PgogICAkeyhBRE0uZGVwb3NpdHN8fFtdKS5sZW5ndGg/QURNLmRlcG9zaXRzLm1hcChk"
    "PT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygid2FsbGV0"
    "Iil9PC9kaXY+CiAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxl"
    "PSJmb250LXNpemU6LjgycmVtIj4ke21vbmV5KGQuYW10KX0gwrcgJHtlc2MoZC51c2VyKX08L2I+"
    "PGJyPgogICAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42"
    "OXJlbSI+JHtlc2MoZC51dHJ8fCJubyByZWZlcmVuY2UgeWV0Iil9PC9zbWFsbD48L2Rpdj4KICAg"
    "ICAgIDxzcGFuIGNsYXNzPSJwaWxsICR7ZC5zdD09PSJDb21wbGV0ZWQiPyJvbiI6ImhvdCJ9Ij4k"
    "e2VzYyhkLnN0KX08L3NwYW4+CiAgICAgICAke2Quc3Q9PT0iQ29tcGxldGVkIj8iIjpgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIHhzIiBkYXRhLWRlcC1vaz0iJHtkLmlkfSI+QXBwcm92ZTwvYnV0dG9uPmB9"
    "PC9kaXY+YCkuam9pbigiIikKICAgICA6JzxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiPjxwIGNsYXNz"
    "PSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gZGVwb3NpdHMgeWV0LjwvcD48L2Rpdj4nfWA7Cn0K"
    "CmZ1bmN0aW9uIGFDb250ZW50KCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+RWRp"
    "dCBhcHAgdGV4dDwvaDM+CiAgICAgPHAgY2xhc3M9InN1YiI+RXZlcnkgd29yZCB1c2VycyBzZWUg"
    "4oCUIGNoYW5nZSBpdCBoZXJlIGFuZCB0aGUgc3RvcmUgdXBkYXRlcyBpbnN0YW50bHkuPC9wPgog"
    "ICAgIDxkaXYgY2xhc3M9Im1zZWFyY2giIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxkaXYgY2xh"
    "c3M9ImZpZWxkd3JhcCI+JHtpYygic2VhcmNoIil9PGlucHV0IGlkPSJjcSIgcGxhY2Vob2xkZXI9"
    "IkZpbmQgdGV4dCB0byBlZGl04oCmIj48L2Rpdj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBz"
    "bSBzb2Z0IiBpZD0iY21zcmVzZXQiPlJlc2V0PC9idXR0b24+PC9kaXY+CiAgICAgPHAgY2xhc3M9"
    "InN1YiIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+VXNlICZsdDticiZndDsgZm9yIGEgbGluZSBi"
    "cmVhaywgJmx0O2ImZ3Q7d29yZCZsdDsvYiZndDsgZm9yIGJvbGQuIHtufSA9IGxpdmUgY291bnRy"
    "eSBjb3VudCwge219ID0gcGF5bWVudCBtaW51dGVzLjwvcD48L2Rpdj4KICAgJHtDTVNNRVRBLm1h"
    "cCgoW2dycCxyb3dzXSk9PmA8ZGl2IGNsYXNzPSJjYXJkIGNtc2dycCI+PGgzPiR7Z3JwfTwvaDM+"
    "CiAgICAgJHtyb3dzLm1hcCgoW2ssbGJsXSk9PmA8ZGl2IGNsYXNzPSJmaWVsZCBjbXNyb3ciIGRh"
    "dGEtZj0iJHtlc2MoKGxibCsiICIrQ01TW2tdKyIgIitncnApLnRvTG93ZXJDYXNlKCkpfSI+CiAg"
    "ICAgICAgPGxhYmVsPiR7bGJsfTwvbGFiZWw+CiAgICAgICAgPHRleHRhcmVhIGNsYXNzPSJpbnAi"
    "IHJvd3M9IiR7Q01TW2tdLmxlbmd0aD40MD8yOjF9IiBkYXRhLWNtcz0iJHtrfSI+JHtlc2MoQ01T"
    "W2tdKX08L3RleHRhcmVhPjwvZGl2PmApLmpvaW4oIiIpfQogICA8L2Rpdj5gKS5qb2luKCIiKX0K"
    "ICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFzcz0i"
    "ZG90Ij48L2k+TGl2ZSBlZGl0PC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lk"
    "dGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPkNoYW5nZXMgYXBwbHkgdG8g"
    "dGhlIFN0b3JlIHRhYiBzdHJhaWdodCBhd2F5PC9kaXY+CiAgICAgPC9kaXY+YDsKfQoKZnVuY3Rp"
    "b24gYUZlYXR1cmVzKCl7CiAgY29uc3Qgb249T2JqZWN0LnZhbHVlcyhGRUFUKS5maWx0ZXIoQm9v"
    "bGVhbikubGVuZ3RoLHRvdD1PYmplY3Qua2V5cyhGRUFUKS5sZW5ndGg7CiAgcmV0dXJuIGA8ZGl2"
    "IGNsYXNzPSJjYXJkIj48aDM+RmVhdHVyZSBzd2l0Y2hlczwvaDM+CiAgICAgPHAgY2xhc3M9InN1"
    "YiI+VHVybiBhbnl0aGluZyBvZmYgYW5kIGl0IGRpc2FwcGVhcnMgZnJvbSB0aGUgYXBwIGNvbXBs"
    "ZXRlbHkg4oCUIG5vIGJ1dHRvbnMsIG5vIG1lbnVzLCBubyBsZWZ0b3ZlcnMuPC9wPgogICAgIDxk"
    "aXYgY2xhc3M9Imt2Ij48c3Bhbj5FbmFibGVkPC9zcGFuPjxiIGlkPSJmY291bnQiPiR7b259IG9m"
    "ICR7dG90fTwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJtc2VhcmNoIiBzdHlsZT0ibWFyZ2lu"
    "LXRvcDoxMnB4Ij48ZGl2IGNsYXNzPSJmaWVsZHdyYXAiPiR7aWMoInNlYXJjaCIpfTxpbnB1dCBp"
    "ZD0iZnEiIHBsYWNlaG9sZGVyPSJTZWFyY2ggZmVhdHVyZXPigKYiPjwvZGl2PgogICAgICAgPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIHNtIHNvZnQiIGlkPSJmYWxsIj5BbGwgb248L2J1dHRvbj48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGJ1dHRvbiBj"
    "bGFzcz0iYnRuIHNtIGdob3N0IiBpZD0iZm5vbmUiPkFsbCBvZmY8L2J1dHRvbj4KICAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBpZD0iZnJlc2V0Ij5SZXNldDwvYnV0dG9uPjwvZGl2"
    "PjwvZGl2PgogICAke0ZHUk9VUFMubWFwKChbZ3JwLHJvd3NdKT0+YDxkaXYgY2xhc3M9ImNhcmQg"
    "ZmdycCI+PGgzPiR7Z3JwfTwvaDM+CiAgICAgJHtyb3dzLm1hcCgoW2ssbCxkXSk9PmA8ZGl2IGNs"
    "YXNzPSJmY2FyZCBmcm93IiBkYXRhLWY9IiR7ZXNjKChsKyIgIitkKyIgIitncnApLnRvTG93ZXJD"
    "YXNlKCkpfSI+CiAgICAgICAgPGRpdiBjbGFzcz0idCI+PGI+JHtsfTwvYj48c21hbGw+JHtkfTwv"
    "c21hbGw+PC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0idGcgJHtGRUFUW2tdPyJvbiI6IiJ9IiBk"
    "YXRhLWZlYXQ9IiR7a30iPjwvZGl2PjwvZGl2PmApLmpvaW4oIiIpfQogICA8L2Rpdj5gKS5qb2lu"
    "KCIiKX0KICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGln"
    "bi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBj"
    "bGFzcz0iZG90Ij48L2k+TGl2ZTwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdp"
    "ZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj5Td2l0Y2hlcyBhcHBseSB0"
    "byB0aGUgU3RvcmUgdGFiIGluc3RhbnRseTwvZGl2PgogICAgIDwvZGl2PmA7Cn0KCmZ1bmN0aW9u"
    "IGFNYXJrZXQoKXsKICByZXR1cm4gYCR7RygiY291cG9ucyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxo"
    "Mz5Db3Vwb25zPC9oMz48cCBjbGFzcz0ic3ViIj5QZXJjZW50YWdlIG9mZiBhdCBjaGVja291dC48"
    "L3A+CiAgICAgJHsoQURNLmNvdXBvbnMmJkFETS5jb3Vwb25zLmxlbmd0aCk/QURNLmNvdXBvbnMu"
    "bWFwKGM9PmA8ZGl2IGNsYXNzPSJrdiI+CiAgICAgICA8c3Bhbj48YiBzdHlsZT0iY29sb3I6dmFy"
    "KC0tdHgpIj4ke2VzYyhjLmNvZGUpfTwvYj4gwrcgJHtjLnBlcmNlbnR9JSDCtyAke2MudXNlZH0v"
    "JHtjLm1heHx8IuKIniJ9PC9zcGFuPgogICAgICAgPGI+PHNwYW4gY2xhc3M9InBpbGwgJHtjLmFj"
    "dGl2ZT8ib24iOiJvZmYifSI+JHtjLmFjdGl2ZT8iQWN0aXZlIjoiT2ZmIn08L3NwYW4+PC9iPjwv"
    "ZGl2PmApLmpvaW4oIiIpCiAgICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+"
    "Tm8gY291cG9ucyB5ZXQuPC9wPid9CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEycHgiPjxsYWJlbD5OZXcgY29kZTwvbGFiZWw+CiAgICAgICA8ZGl2IHN0eWxlPSJk"
    "aXNwbGF5OmZsZXg7Z2FwOjdweCI+PGlucHV0IGNsYXNzPSJpbnAiIGlkPSJjcGNvZGUiIHBsYWNl"
    "aG9sZGVyPSJORVdZRUFSIj4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0iY3BwY3QiIHBs"
    "YWNlaG9sZGVyPSIlIiBzdHlsZT0id2lkdGg6NjZweCI+CiAgICAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0iY3BhZGQiPkFkZDwvYnV0dG9uPjwvZGl2"
    "PjwvZGl2PjwvZGl2PmApfQogICAke0coImZsYXNoU2FsZSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxo"
    "Mz5GbGFzaCBzYWxlIHNjaGVkdWxlcjwvaDM+PHAgY2xhc3M9InN1YiI+U3RhcnRzLCBhbm5vdW5j"
    "ZXMgYW5kIGVuZHMgYnkgaXRzZWxmLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9"
    "Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPkRpc2NvdW50ICglKTwvbGFiZWw+PGlucHV0IGNsYXNz"
    "PSJpbnAiIHZhbHVlPSIyMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5T"
    "dGFydHM8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iVG9kYXkgMTg6MDAiPjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RW5kczwvbGFiZWw+PGlucHV0IGNsYXNz"
    "PSJpbnAiIHZhbHVlPSJUb2RheSAyMzowMCI+PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IGJsb2NrIiBpZD0iZmxhc2hnbyI+U3RhcnQgRmxhc2ggU2FsZTwvYnV0dG9uPjwvZGl2PmApfQog"
    "ICAke0coInNjaGVkQnJvYWRjYXN0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlNjaGVkdWxlZCBi"
    "cm9hZGNhc3Q8L2gzPjxwIGNsYXNzPSJzdWIiPlF1ZXVlIGl0IGFuZCB0YXJnZXQgYSBzZWdtZW50"
    "LjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxh"
    "YmVsPlNlZ21lbnQ8L2xhYmVsPgogICAgICAgPHNlbGVjdCBjbGFzcz0iaW5wIj48b3B0aW9uPkV2"
    "ZXJ5b25lPC9vcHRpb24+PG9wdGlvbj5CdXllcnMgb25seTwvb3B0aW9uPjxvcHRpb24+TmV2ZXIg"
    "Ym91Z2h0PC9vcHRpb24+PG9wdGlvbj5CaWcgc3BlbmRlcnMgKOKCuTEwMDArKTwvb3B0aW9uPjxv"
    "cHRpb24+SW5hY3RpdmUgMzAgZGF5czwvb3B0aW9uPjwvc2VsZWN0PjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9ImZpZWxkIj48bGFiZWw+U2VuZCBhdDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZh"
    "bHVlPSJUb21vcnJvdyAxMDowMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJl"
    "bD5NZXNzYWdlPC9sYWJlbD48dGV4dGFyZWEgY2xhc3M9ImlucCIgcm93cz0iMiIgcGxhY2Vob2xk"
    "ZXI9IldyaXRlIHlvdXIgYnJvYWRjYXN04oCmIj48L3RleHRhcmVhPjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0iYmNnbyI+U2VuZCBCcm9hZGNh"
    "c3Q8L2J1dHRvbj4KICAgICAgIDwvZGl2PjwvZGl2PmApfQogICAke0coIndpbmJhY2siLGA8ZGl2"
    "IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+PGRp"
    "diBjbGFzcz0idCI+V2luLWJhY2sgbWVzc2FnZXM8c21hbGw+QXV0by1ETSB1c2VycyBpbmFjdGl2"
    "ZSBmb3IgNyBhbmQgMzAgZGF5czwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGcke0ZF"
    "QVQuc3BsaXRQYXk/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InNwbGl0UGF5Ij48L2Rpdj48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkRpc2NvdW50IG9mZmVyZWQgKCUpPC9sYWJl"
    "bD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwIj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJh"
    "YmFuZG9uUmVtaW5kIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6Zmxl"
    "eDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9u"
    "Ij48aSBjbGFzcz0iZG90Ij48L2k+UmVtaW5kZXJzPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZs"
    "ZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPjQgdW5m"
    "aW5pc2hlZCBwYXltZW50cyBudWRnZWQgdG9kYXk8L2Rpdj4KICAgICA8L2Rpdj5gKX0KICAgJHtH"
    "KCJsb3lhbHR5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxveWFsdHkgcG9pbnRzPC9oMz4KICAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+UG9pbnRz"
    "IHBlciDigrkxMCBzcGVudDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxIj48L2Rp"
    "dj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlBvaW50cyBuZWVkZWQgZm9yIOKCuTEw"
    "PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwMCI+PC9kaXY+CiAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9IndpbmJhY2tzYXZlIj5T"
    "YXZlPC9idXR0b24+PC9kaXY+YCl9CiAgICR7YW55RigicmVmRGFzaCIsInJlZkwyIik/YDxkaXYg"
    "Y2xhc3M9ImNhcmQiPjxoMz5SZWZlcnJhbCBwYXlvdXRzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJm"
    "aWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+TGV2ZWwgMSBjb21taXNzaW9uICgl"
    "KTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj4KICAgICAke0coInJl"
    "ZkwyIixgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5MZXZlbCAyIGNvbW1pc3Npb24gKCUpPC9s"
    "YWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEiPjwvZGl2PmApfQogICAgIDxkaXYgY2xh"
    "c3M9Imt2Ij48c3Bhbj5QYWlkIG91dCB0aGlzIG1vbnRoPC9zcGFuPjxiPuKCuTMsMjQwPC9iPjwv"
    "ZGl2PjwvZGl2PmA6IiJ9CiAgICR7Rygic2FsZXNGZWVkIixgPGRpdiBjbGFzcz0iY2FyZCI+PGRp"
    "diBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPkF1dG8t"
    "cG9zdCBzYWxlcyB0byBjaGFubmVsPHNtYWxsPiR7RkVFRC5sZW5ndGg/ZXNjKEZFRURbMF1bMV0r"
    "IiBzb2xkIMK3ICIrRkVFRFswXVszXSk6Im5vIHNhbGVzIHlldCJ9PC9zbWFsbD48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC53aW5iYWNrPyIgb24iOiIifSIgZGF0YS1mZWF0PSJ3aW5i"
    "YWNrIj48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJsZWFkZXJib2FyZCIsYDxkaXYgY2xh"
    "c3M9ImNhcmQiPjxoMz5MZWFkZXJib2FyZCBwcml6ZTwvaDM+CiAgICAgPGRpdiBjbGFzcz0iZmll"
    "bGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPldlZWtseSBwcml6ZSAo4oK5KTwvbGFi"
    "ZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1MDAiPjwvZGl2PgogICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJwcml6ZXNhdmUiPlNhdmU8L2J1"
    "dHRvbj48L2Rpdj5gKX1gOwp9CgpmdW5jdGlvbiBhQW5hbHl0aWNzKCl7CiAgY29uc3Qgd2Vlaz0o"
    "QURNLnNlcmllc3x8W10pLnJlZHVjZSgoYSxiKT0+YStiLDApOwogIGNvbnN0IGF2Zz1BRE0ua3Bp"
    "Lm9yZGVycz9BRE0ua3BpLnJldi9BRE0ua3BpLm9yZGVyczowOwogIHJldHVybiBgJHtHKCJyZXZD"
    "aGFydCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5SZXZlbnVlPC9oMz48cCBjbGFzcz0ic3ViIj5M"
    "YXN0IDcgZGF5cyDCtyAke21vbmV5KHdlZWspfSB0b3RhbDwvcD4KICAgICA8ZGl2IGNsYXNzPSJi"
    "YXJzIj4ke0FETS5iYXJzLm1hcChoPT5gPGkgc3R5bGU9ImhlaWdodDoke2h9JSI+PC9pPmApLmpv"
    "aW4oIiIpfTwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImtwaXMiIHN0eWxlPSJtYXJnaW4tdG9wOjE0"
    "cHgiPiR7W1siVG9kYXkiLG1vbmV5KEFETS5rcGkucmV2KV0sWyJUaGlzIG1vbnRoIixtb25leShB"
    "RE0ubWFya2V0Lm1vbnRoKV0sWyJBdmcgb3JkZXIiLG1vbmV5KGF2ZyldXQogICAgICAgLm1hcCgo"
    "W2ssdl0pPT5gPGRpdiBjbGFzcz0ia3BpIj48c21hbGw+JHtrfTwvc21hbGw+PGI+JHt2fTwvYj48"
    "L2Rpdj5gKS5qb2luKCIiKX08L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJmdW5uZWwiLGA8ZGl2IGNs"
    "YXNzPSJjYXJkIj48aDM+Q29udmVyc2lvbiBmdW5uZWw8L2gzPjxwIGNsYXNzPSJzdWIiPldoZXJl"
    "IHBlb3BsZSBkcm9wIG9mZjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmdW5uZWwiPiR7KCgpPT57Y29u"
    "c3QgZj1BRE0uYW5hbHl0aWNzLmZ1bm5lbCx0PU1hdGgubWF4KDEsZi51c2Vycyk7CiAgICAgICBy"
    "ZXR1cm4gW1siU3RhcnRlZCBib3QiLGYudXNlcnMsMTAwXSxbIkRlcG9zaXRlZCIsZi5kZXAsTWF0"
    "aC5yb3VuZChmLmRlcC90KjEwMCldLFsiQm91Z2h0IixmLmJ1eSxNYXRoLnJvdW5kKGYuYnV5L3Qq"
    "MTAwKV1dCiAgICAgICAubWFwKChbayx2LHddKT0+YDxkaXY+PGkgc3R5bGU9IndpZHRoOiR7TWF0"
    "aC5tYXgoNCx3KX0lIj48L2k+PHNwYW4gc3R5bGU9IndoaXRlLXNwYWNlOm5vd3JhcCI+JHtrfSDC"
    "tyA8Yj4ke3Z9PC9iPjwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKTt9KSgpfTwvZGl2PjwvZGl2PmAp"
    "fQogICAke0coInByb2ZpdFJlcCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5QZXItY291bnRyeSBw"
    "cm9maXQ8L2gzPgogICAgICR7QURNLmFuYWx5dGljcy5jb3VudHJ5Lmxlbmd0aD9BRE0uYW5hbHl0"
    "aWNzLmNvdW50cnkubWFwKChbYyxuLHN1bV0pPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7ZXNj"
    "KGMpfSDCtyAke259IHNvbGQ8L3NwYW4+PGIgc3R5bGU9ImNvbG9yOnZhcigtLW9rKSI+JHttb25l"
    "eShzdW0pfTwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgOic8cCBjbGFzcz0ic3ViIiBzdHls"
    "ZT0ibWFyZ2luOjAiPk5vIHNhbGVzIHlldC48L3A+J30KICAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "YmxvY2sgc29mdCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1hZG1pbi1hY3Q9ImV4cG9y"
    "dCI+RXhwb3J0IENTVjwvYnV0dG9uPjwvZGl2PmApfQogICAke0coImNsdiIsYDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxoMz5DdXN0b21lciB2YWx1ZTwvaDM+CiAgICAgJHtBRE0udXNlcnMubGVuZ3RoP0FE"
    "TS51c2Vycy5zbGljZSgwLDUpLm1hcCh1PT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPjxiIHN0eWxl"
    "PSJjb2xvcjp2YXIoLS10eCkiPiR7ZXNjKHUubmFtZSl9PC9iPiDCtyAke3Uub3JkZXJzfSBvcmRl"
    "cnM8L3NwYW4+CiAgICAgICAgIDxiPiR7bW9uZXkodS5zcGVudCl9IDxzcGFuIGNsYXNzPSJwaWxs"
    "ICR7dS5zcGVudD4wPyJvbiI6ImhvdCJ9Ij4ke3Uuc3BlbnQ+MD8iYWN0aXZlIjoibm8gc3BlbmQi"
    "fTwvc3Bhbj48L2I+PC9kaXY+YCkuam9pbigiIikKICAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5"
    "bGU9Im1hcmdpbjowIj5ObyBjdXN0b21lcnMgeWV0LjwvcD4nfTwvZGl2PmApfQogICAke0coImRp"
    "Z2VzdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5n"
    "LXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5EYWlseSBkaWdlc3Q8c21hbGw+RW5kLW9mLWRheSBzdW1t"
    "YXJ5IHRvIHRoZSBhZG1pbiBjaGF0PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7"
    "RkVBVC5zYWxlc0ZlZWQ/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InNhbGVzRmVlZCI+PC9kaXY+PC9k"
    "aXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5TZW5kIGF0PC9sYWJlbD48aW5wdXQg"
    "Y2xhc3M9ImlucCIgdmFsdWU9IjIzOjMwIj48L2Rpdj48L2Rpdj5gKX1gOwp9CgpmdW5jdGlvbiBh"
    "U2VjdXJpdHkoKXsKICByZXR1cm4gYCR7RygicmF0ZUxpbWl0IixgPGRpdiBjbGFzcz0iY2FyZCI+"
    "PGgzPlJhdGUgbGltaXRpbmc8L2gzPjxwIGNsYXNzPSJzdWIiPlN0b3BzIG9uZSB1c2VyIGhhbW1l"
    "cmluZyB0aGUgYm90LjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10"
    "b3A6MTBweCI+PGxhYmVsPk1heCBhY3Rpb25zIHBlciBtaW51dGU8L2xhYmVsPjxpbnB1dCBjbGFz"
    "cz0iaW5wIiB2YWx1ZT0iMjAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+"
    "Q29vbGRvd24gYWZ0ZXIgbGltaXQgKHMpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9"
    "IjYwIj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJtdWx0aUFjYyIsYDxkaXYgY2xhc3M9ImNhcmQi"
    "PjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5N"
    "dWx0aS1hY2NvdW50IGRldGVjdGlvbjxzbWFsbD5GbGFncyByZWZlcnJhbCBmYXJtaW5nIHBhdHRl"
    "cm5zPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5tdWx0aUFjYz8iIG9u"
    "IjoiIn0iIGRhdGEtZmVhdD0ibXVsdGlBY2MiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "Imt2Ij48c3Bhbj5GbGFnZ2VkIHRoaXMgd2Vlazwvc3Bhbj48Yj43IGFjY291bnRzPC9iPjwvZGl2"
    "PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayBzb2Z0IiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "MHB4IiBkYXRhLWEtZ289Im9yZGVycyI+UmV2aWV3IEZsYWdnZWQ8L2J1dHRvbj48L2Rpdj5gKX0K"
    "ICAgJHtHKCJ2ZWxvY2l0eSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0"
    "eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5QdXJjaGFzZSB2ZWxvY2l0eSBhbGVy"
    "dHM8c21hbGw+Q2F0Y2hlcyBzdG9sZW4gdG9wLXVwczwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBj"
    "bGFzcz0idGcke0ZFQVQudmVsb2NpdHk/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InZlbG9jaXR5Ij48"
    "L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkFsZXJ0IGFib3ZlIE4g"
    "YnV5cyAvIDEwIG1pbjwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj48"
    "L2Rpdj5gKX0KICAgJHtHKCJhZG1pbjJmYSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5BZG1pbiAy"
    "RkE8L2gzPjxwIGNsYXNzPSJzdWIiPlJlcXVpcmVkIGJlZm9yZSBiYWxhbmNlIGVkaXRzLCBicm9h"
    "ZGNhc3RzIGFuZCB3aXBlcy48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEwcHgiPjxsYWJlbD5NZXRob2Q8L2xhYmVsPjxzZWxlY3QgY2xhc3M9ImlucCI+PG9w"
    "dGlvbj5QSU48L29wdGlvbj48b3B0aW9uPlRPVFAgYXBwPC9vcHRpb24+PC9zZWxlY3Q+PC9kaXY+"
    "CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBkYXRhLWZlYXQ9ImFkbWluMmZhIj5TZXQgVXA8"
    "L2J1dHRvbj48L2Rpdj5gKX0KICAgJHtHKCJhdXRvQmFuIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgz"
    "PkF1dG8tYmFuIHJ1bGVzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0"
    "Ij5DaGFyZ2ViYWNrPHNtYWxsPkJhbiBpbnN0YW50bHk8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuYmFuX2NoYXJnZWJhY2spPyIgb24iOiIifSIgZGF0"
    "YS1zZXR0aW5nPSJiYW5fY2hhcmdlYmFjayI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "c3dhcCI+PGRpdiBjbGFzcz0idCI+RmFrZSBVVFIgc3VibWl0dGVkPHNtYWxsPkJhbiBhZnRlciAy"
    "IGF0dGVtcHRzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURN"
    "LnN3LmJhbl9mYWtlX3V0cik/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImJhbl9mYWtlX3V0ciI+"
    "PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+UmVwZWF0"
    "ZWQgZmFpbGVkIHZlcmlmaWNhdGlvbjxzbWFsbD5CYW4gYWZ0ZXIgNTwvc21hbGw+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5iYW5fZmFpbGVkX3ZlcmlmeSk/IiBv"
    "biI6IiJ9IiBkYXRhLXNldHRpbmc9ImJhbl9mYWlsZWRfdmVyaWZ5Ij48L2Rpdj48L2Rpdj48L2Rp"
    "dj5gKX0KICAgJHtHKCJibGFja2xpc3QiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QmxhY2tsaXN0"
    "PC9oMz48cCBjbGFzcz0ic3ViIj5Vc2VyIElEcywgcGhvbmUgbnVtYmVycyBhbmQgVVBJIHJlZmVy"
    "ZW5jZXMuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4"
    "Ij48ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjdweCI+CiAgICAgICA8aW5wdXQgY2xhc3M9"
    "ImlucCIgaWQ9ImJsLWluIiBwbGFjZWhvbGRlcj0iSUQsIHBob25lIG9yIFVQSSByZWYiPjxidXR0"
    "b24gY2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJibGJsb2NrIj5CbG9j"
    "azwvYnV0dG9uPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5CbG9ja2Vk"
    "IGVudHJpZXM8L3NwYW4+PGI+NDI8L2I+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiZW5jcnlwdCIs"
    "YDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDow"
    "Ij48ZGl2IGNsYXNzPSJ0Ij5FbmNyeXB0IHNlc3Npb25zIGF0IHJlc3Q8c21hbGw+QSBsZWFrZWQg"
    "ZGF0YWJhc2UgZmlsZSBzdGF5cyB1c2VsZXNzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNz"
    "PSJ0ZyR7RkVBVC5lbmNyeXB0PyIgb24iOiIifSIgZGF0YS1mZWF0PSJlbmNyeXB0Ij48L2Rpdj48"
    "L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+S2V5IHN0YXR1czwvc3Bhbj48YiBzdHls"
    "ZT0iY29sb3I6dmFyKC0tb2spIj5Mb2FkZWQgZnJvbSBlbnY8L2I+PC9kaXY+PC9kaXY+YCl9YDsK"
    "fQoKZnVuY3Rpb24gYVN0b2NrKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+VXBs"
    "b2FkIHN0b2NrPC9oMz48cCBjbGFzcz0ic3ViIj5QYXN0ZSBzZXNzaW9ucyBvciB1cGxvYWQgYSBm"
    "aWxlIOKAlCBvbmUgcGVyIGxpbmUuPC9wPgo8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdp"
    "bi10b3A6MTJweCI+PGxhYmVsPlBhc3RlIGFjY291bnRzIOKAlCBvbmUgcGVyIGxpbmU8L2xhYmVs"
    "PgogICAgICA8dGV4dGFyZWEgY2xhc3M9ImlucCIgaWQ9InN0cGFzdGUiIHJvd3M9IjQiCiAgICAg"
    "ICAgcGxhY2Vob2xkZXI9IkluZGlhIHwgKzkxIHwgMzUgfCBzZXNzaW9uJiMxMDtLZW55YSB8ICsy"
    "NTQgfCA3NyB8IHNlc3Npb24iPjwvdGV4dGFyZWE+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJidG5y"
    "b3ciPjxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJzdHNhdmUiPkltcG9ydDwvYnV0dG9uPgog"
    "ICAgICA8bGFiZWwgY2xhc3M9ImJ0biBzb2Z0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0"
    "ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2N1cnNvcjpwb2ludGVyIj4KICAgICAg"
    "ICBVcGxvYWQgLnR4dDxpbnB1dCB0eXBlPSJmaWxlIiBpZD0ic3RmaWxlIiBhY2NlcHQ9Ii50eHQs"
    "LmNzdiIgaGlkZGVuPjwvbGFiZWw+PC9kaXY+CiAgICA8cCBjbGFzcz0ic3ViIiBpZD0ic3Rtc2ci"
    "IHN0eWxlPSJtYXJnaW46OHB4IDAgMDtmb250LXNpemU6LjcycmVtIj48L3A+CiAgICAke0coImJ1"
    "bGtVcGxvYWQiLGA8ZGl2IGNsYXNzPSJzdGVwcyIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+CiAg"
    "ICAgIDxkaXYgY2xhc3M9InN0ZXAgb24iPjxpPjE8L2k+JHtBRE0uYW5hbHl0aWNzLnN0b2NrX3Rv"
    "dGFsfSBhdmFpbGFibGUgbm93PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAgb24iPjxpPjI8"
    "L2k+JHtBRE0uYW5hbHl0aWNzLnNvbGRfdG90YWx9IHNvbGQgc28gZmFyPC9kaXY+CiAgICAgIDxk"
    "aXYgY2xhc3M9InN0ZXAiPjxpPjM8L2k+QWRkIG1vcmUgZnJvbSB0aGUgYm90OiBBZG1pbiDihpIg"
    "U3RvY2s8L2Rpdj48L2Rpdj5gKX0KICAgICR7RygiZHVwRGV0ZWN0IixgPHAgY2xhc3M9InN1YiIg"
    "c3R5bGU9Im1hcmdpbjoxMHB4IDAgMDtmb250LXNpemU6LjdyZW0iPkR1cGxpY2F0ZSBwaG9uZXMg"
    "YW5kIHNlc3Npb25zIGFyZSByZWplY3RlZCBhdXRvbWF0aWNhbGx5LjwvcD5gKX0KICAgICR7Rygi"
    "aGVhbHRoQ2hlY2siLGA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5WYWxpZGF0ZSBi"
    "ZWZvcmUgc2VsbGluZzxzbWFsbD5DaGVjayB0aGUgc2Vzc2lvbiBpcyBhbGl2ZSBhdCBwdXJjaGFz"
    "ZSB0aW1lPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5oZWFsdGhDaGVj"
    "az8iIG9uIjoiIn0iIGRhdGEtZmVhdD0iaGVhbHRoQ2hlY2siPjwvZGl2PjwvZGl2PmApfTwvZGl2"
    "PgogICAke0coImxvd1N0b2NrIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxvdy1zdG9jayBhbGVy"
    "dHM8L2gzPjxwIGNsYXNzPSJzdWIiPlBlci1jb3VudHJ5IHRocmVzaG9sZCDigJQgcGluZyB3aGVu"
    "IGl0IGRyb3BzIGJlbG93LjwvcD4KICAgICAkeyhBRE0uc3RvY2subGVuZ3RoP0FETS5zdG9jazpT"
    "VE9DSykuc2xpY2UoMCw2KS5tYXAocz0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj4ke3MuZn0gJHtl"
    "c2Mocy5jKX0gwrcgJHtzLnF0eXx8MH0gbGVmdDwvc3Bhbj4KICAgICAgIDxiPjxpbnB1dCBjbGFz"
    "cz0iaW5wIiBzdHlsZT0iaGVpZ2h0OjMwcHg7d2lkdGg6NjRweDt0ZXh0LWFsaWduOmNlbnRlcjtm"
    "b250LXNpemU6Ljc0cmVtIiB2YWx1ZT0iJHtzLmxvd3x8MTB9Ij48L2I+PC9kaXY+YCkuam9pbigi"
    "IikKICAgICAgIHx8JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gc3RvY2sgeWV0"
    "LjwvcD4nfTwvZGl2PmApfQogICAke0coInNlbGxIb3VycyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxo"
    "Mz5TZWxsaW5nIGhvdXJzPC9oMz48cCBjbGFzcz0ic3ViIj5Db3VudHJpZXMgaGlkZSB0aGVtc2Vs"
    "dmVzIG91dHNpZGUgdGhlIHdpbmRvdy48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxl"
    "PSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPk9wZW48L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2"
    "YWx1ZT0iMDk6MDAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+Q2xvc2U8"
    "L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMDI6MDAiPjwvZGl2PjwvZGl2PmApfQog"
    "ICAke0coImR5blByaWNlIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkR5bmFtaWMgcHJpY2luZzwv"
    "aDM+CiAgICAgPGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xh"
    "c3M9InQiPlJhaXNlIHByaWNlIHdoZW4gc3RvY2sgaXMgbG93PHNtYWxsPisxMCUgdW5kZXIgMjAg"
    "dW5pdHM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULmR5blByaWNlPyIg"
    "b24iOiIifSIgZGF0YS1mZWF0PSJkeW5QcmljZSI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFz"
    "cz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+TmlnaHQgZGlzY291bnQ8c21hbGw+4oiSNSUgYmV0d2Vl"
    "biAwMDowMCBhbmQgMDY6MDA8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURN"
    "LnN3JiZBRE0uc3cubmlnaHRfZGlzY291bnQpPyIgb24iOiIifSIgZGF0YS1zZXR0aW5nPSJuaWdo"
    "dF9kaXNjb3VudCI+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiYnVsa0Rpc2MiLGA8ZGl2"
    "IGNsYXNzPSJjYXJkIj48aDM+QnVsayBkaXNjb3VudHM8L2gzPgogICAgIDxwIGNsYXNzPSJzdWIi"
    "PlBlcmNlbnQgb2ZmIHdoZW4gdGhlIGJ1eWVyIHRha2VzIHNldmVyYWwgYXQgb25jZS48L3A+CiAg"
    "ICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxsYWJlbD41IG9y"
    "IG1vcmUgKCUpPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXByaWNlPSJi"
    "dWxrXzUiIHZhbHVlPSIke0FETS5wcmljaW5nP0FETS5wcmljaW5nLmJ1bGtfNTo1fSI+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD4xMCBvciBtb3JlICglKTwvbGFiZWw+CiAg"
    "ICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1wcmljZT0iYnVsa18xMCIgdmFsdWU9IiR7QURN"
    "LnByaWNpbmc/QURNLnByaWNpbmcuYnVsa18xMDoxMH0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "ImZpZWxkIj48bGFiZWw+MjUgb3IgbW9yZSAoJSk8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNz"
    "PSJpbnAiIGRhdGEtcHJpY2U9ImJ1bGtfMjUiIHZhbHVlPSIke0FETS5wcmljaW5nP0FETS5wcmlj"
    "aW5nLmJ1bGtfMjU6MTV9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkRl"
    "ZmF1bHQgcHJpY2UgZm9yIG5ldyBzdG9jazwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9Imlu"
    "cCIgZGF0YS1wcmljZT0iZGVmYXVsdF9wcmljZSIgdmFsdWU9IiR7QURNLnByaWNpbmc/QURNLnBy"
    "aWNpbmcuZGVmYXVsdF9wcmljZTozNX0iPjwvZGl2PgogICAgIDwvZGl2PmApfQogICAke0coInZh"
    "cmlhbnRzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlZhcmlhbnRzPC9oMz48cCBjbGFzcz0ic3Vi"
    "Ij5QcmljZWQgc2VwYXJhdGVseSBvbiBldmVyeSBjb3VudHJ5LjwvcD4KICAgICA8ZGl2IGNsYXNz"
    "PSJrdiI+PHNwYW4+MkZBIGVuYWJsZWQ8L3NwYW4+PGI+K+KCuTU8L2I+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPldpdGggbWFpbDwvc3Bhbj48Yj4r4oK5ODwvYj48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+U3BhbS1mcmVlIGd1YXJhbnRlZTwvc3Bhbj48Yj4r4oK5"
    "NDwvYj48L2Rpdj48L2Rpdj5gKX0KICAgPGRpdiBjbGFzcz0ic2VjIj48aDI+SW52ZW50b3J5PC9o"
    "Mj48YSBjbGFzcz0ic3ViIj50YXAgYSBwcmljZSB0byBjaGFuZ2UgaXQ8L2E+PC9kaXY+CiAgICR7"
    "U1RPQ0subWFwKHM9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4"
    "O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtz"
    "LmZ9PC9kaXY+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0i"
    "Zm9udC1zaXplOi44M3JlbSI+JHtzLmN9PC9iPjxicj4KICAgICAgIDxzbWFsbCBzdHlsZT0iY29s"
    "b3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4ke3MucXR5fSBhdmFpbGFibGU8L3NtYWxs"
    "PjwvZGl2PgogICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLWNwcmljZT0iJHtlc2Mocy5jKX0i"
    "IHZhbHVlPSIke3MucHJpY2V9IiBzdHlsZT0iaGVpZ2h0OjMycHg7d2lkdGg6NzhweDt0ZXh0LWFs"
    "aWduOmNlbnRlcjtmb250LXNpemU6Ljc2cmVtIj4KICAgICAKICAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4geHMgYmFkIiBkYXRhLW1pc2M9ImNsZWFyc3RvY2siIGRhdGEtbWlzYy12YWx1ZT0iJHtlc2Mo"
    "cy5jKX0iPkNsZWFyPC9idXR0b24+PC9kaXY+YCkuam9pbigiIil9YDsKfQpmdW5jdGlvbiBhT3Jk"
    "ZXJzKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5SZWNlbnQgT3JkZXJzPC9oMj48"
    "YSBkYXRhLWFkbWluLWFjdD0iZXhwb3J0Ij5FeHBvcnQ8L2E+PC9kaXY+CiAgICR7QURNLm9yZGVy"
    "cy5sZW5ndGg/QURNLm9yZGVycy5tYXAobz0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxl"
    "PSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8ZGl2IGNs"
    "YXNzPSJmbGFnIj4ke28uZn08L2Rpdj48ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxi"
    "IHN0eWxlPSJmb250LXNpemU6LjgycmVtIj4ke2VzYyhvLmlkKX08L2I+PGJyPgogICAgIDxzbWFs"
    "bCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4ke2VzYyhvLnVzZXIp"
    "fSDCtyAke21vbmV5KG8ucHJpY2UpfSDCtyAke2VzYyhvLndoZW4pfTwvc21hbGw+PC9kaXY+CiAg"
    "ICAgPHNwYW4gY2xhc3M9InBpbGwgJHtvLnN0PT09IkRlbGl2ZXJlZCI/Im9uIjoib2ZmIn0iPiR7"
    "ZXNjKG8uc3QpfTwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKQogICAgIDonPGRpdiBjbGFzcz0iY2Fy"
    "ZCB0aWdodCI+PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBvcmRlcnMgeWV0Ljwv"
    "cD48L2Rpdj4nfQogICAke0coImRpc3B1dGUiLGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5EaXNwdXRl"
    "czwvaDI+PC9kaXY+CiAgICAgJHtBRE0uZGlzcHV0ZXMubGVuZ3RoP0FETS5kaXNwdXRlcy5tYXAo"
    "ZD0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRl"
    "bXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygic2hp"
    "ZWxkIil9PC9kaXY+CiAgICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIg"
    "c3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+JHtlc2MoZC5pZCl9IMK3ICR7ZXNjKGQudXNlcil9PC9i"
    "Pjxicj48c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OHJlbSI+JHtl"
    "c2MoZC5yZWFzb24pfTwvc21hbGw+PC9kaXY+CiAgICAgICAgIDxzcGFuIGNsYXNzPSJwaWxsICR7"
    "ZC5zdGF0dXM9PT0iT3BlbiI/ImhvdCI6Im9uIn0iPiR7ZXNjKGQuc3RhdHVzKX08L3NwYW4+PC9k"
    "aXY+YCkuam9pbigiIikKICAgICAgIDonPGRpdiBjbGFzcz0iY2FyZCB0aWdodCI+PHAgY2xhc3M9"
    "InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBkaXNwdXRlcy48L3A+PC9kaXY+J31gKX0KICAgJHtH"
    "KCJ3YXJyYW50eSIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPlJlcGxhY2VtZW50IGNsYWltczwvaDI+"
    "PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDth"
    "bGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtp"
    "Yygic2hpZWxkIil9PC9kaXY+CiAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAi"
    "PjxiIHN0eWxlPSJmb250LXNpemU6LjhyZW0iPjIgY2xhaW1zIGluc2lkZSB3YXJyYW50eTwvYj48"
    "YnI+CiAgICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY4"
    "cmVtIj5BdXRvLXJlcGxhY2VkIGZyb20gc3RvY2s8L3NtYWxsPjwvZGl2PgogICAgICAgPC9kaXY+"
    "YCl9CiAgIAogICAke0coInJldmlld3MiLGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5SZXZpZXdzPC9o"
    "Mj48L2Rpdj4KICAgICAkeyhBRE0ucmV2aWV3cy5sZW5ndGg/QURNLnJldmlld3MubWFwKHI9Plty"
    "LnVzZXIsci5jLHIuc3RhcnMsci50ZXh0XSk6W10pCiAgICAgICAubWFwKChbdSxjLHIsdF0pPT5g"
    "PGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpj"
    "ZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJzdGFyIil9"
    "PC9kaXY+CiAgICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9"
    "ImZvbnQtc2l6ZTouOHJlbSI+JHt1fSDCtyAke2N9IMK3ICR7cn08L2I+PGJyPgogICAgICAgICAg"
    "IDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY4cmVtIj4ke3R9PC9z"
    "bWFsbD48L2Rpdj4KICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtcmV2"
    "PSIke3IuaWR9Ij5IaWRlPC9idXR0b24+PC9kaXY+YCkuam9pbigiIil9YCl9YDsKfQpmdW5jdGlv"
    "biBhVXNlcnMoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImZpZWxkIj48aW5wdXQgY2xhc3M9Imlu"
    "cCIgcGxhY2Vob2xkZXI9IlNlYXJjaCBieSBJRCBvciB1c2VybmFtZSI+PC9kaXY+CiAgICR7QURN"
    "LnVzZXJzLmxlbmd0aD9BRE0udXNlcnMubWFwKHU9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBz"
    "dHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgIDxk"
    "aXYgY2xhc3M9ImZsYWciPiR7aWMoInVzZXIiKX08L2Rpdj4KICAgICAgPGRpdiBzdHlsZT0iZmxl"
    "eDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44MnJlbSI+JHtlc2ModS5uYW1l"
    "KX08L2I+PGJyPgogICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNp"
    "emU6LjY5cmVtIj4ke21vbmV5KHUuc3BlbnQpfSBzcGVudCDCtyAke3Uub3JkZXJzfSBvcmRlcnMg"
    "wrcgJHttb25leSh1LmJhbGFuY2UpfSBiYWxhbmNlJHt1LmJhbm5lZD8iIMK3IGJhbm5lZCI6IiJ9"
    "PC9zbWFsbD48L2Rpdj4KICAgICAgPHNwYW4gY2xhc3M9InBpbGwgJHt1LmJhbm5lZD8ib2ZmIjoi"
    "b24ifSI+JHtlc2ModS50aWVyKX08L3NwYW4+PC9kaXY+YCkuam9pbigiIikKICAgICA6JzxkaXYg"
    "Y2xhc3M9ImNhcmQgdGlnaHQiPjxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gdXNl"
    "cnMgeWV0LjwvcD48L2Rpdj4nfQogICAke0coImFkbWluUm9sZXMiLGA8ZGl2IGNsYXNzPSJzZWMi"
    "PjxoMj5BZG1pbiByb2xlczwvaDI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iY2FyZCI+CiAgICAg"
    "ICAke0FETS5hZG1pbnMubGVuZ3RoP0FETS5hZG1pbnMubWFwKGE9PmA8ZGl2IGNsYXNzPSJrdiI+"
    "PHNwYW4+PGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHtlc2MoYS5sYWJlbCl9PC9iPiDCtyAk"
    "e2VzYyhhLnBlcm1zKX08L3NwYW4+PGI+PHNwYW4gY2xhc3M9InBpbGwgb24iPiR7ZXNjKGEucm9s"
    "ZSl9PC9zcGFuPjwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgICA6JzxwIGNsYXNzPSJzdWIi"
    "IHN0eWxlPSJtYXJnaW46MCI+T25seSB5b3Ugc28gZmFyLjwvcD4nfQogICAgICAgPGRpdiBjbGFz"
    "cz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxkaXYgc3R5bGU9ImRpc3BsYXk6Zmxl"
    "eDtnYXA6N3B4Ij4KICAgICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIHBsYWNlaG9sZGVyPSJAdXNl"
    "cm5hbWUiPjxzZWxlY3QgY2xhc3M9ImlucCIgc3R5bGU9IndpZHRoOmF1dG8iPjxvcHRpb24+TWFu"
    "YWdlcjwvb3B0aW9uPjxvcHRpb24+U3VwcG9ydDwvb3B0aW9uPjwvc2VsZWN0PgogICAgICAgICA8"
    "YnV0dG9uIGNsYXNzPSJidG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0iYWRtaW5hZGQi"
    "PkFkZDwvYnV0dG9uPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImJ1eWVyQmFkZ2UiLGA8"
    "ZGl2IGNsYXNzPSJjYXJkIj48aDM+VmVyaWZpZWQgYnV5ZXIgYmFkZ2U8L2gzPgogICAgIDxkaXYg"
    "Y2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5PcmRlcnMgcmVxdWly"
    "ZWQ8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iNSI+PC9kaXY+CiAgICAgPGRpdiBj"
    "bGFzcz0ia3YiPjxzcGFuPlZlcmlmaWVkIHVzZXJzPC9zcGFuPjxiPjg0PC9iPjwvZGl2PjwvZGl2"
    "PmApfQogICAke0coIm11bHRpQWNjIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRp"
    "c3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNz"
    "PSJwaWxsIGhvdCI+NyBmbGFnZ2VkPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4t"
    "d2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlBvc3NpYmxlIG11bHRp"
    "LWFjY291bnRzIHRoaXMgd2VlazwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0"
    "IiBkYXRhLWEtZ289InNlY3VyaXR5Ij5SZXZpZXc8L2J1dHRvbj48L2Rpdj5gKX1gOwp9CmZ1bmN0"
    "aW9uIGFTZXR0aW5ncygpewogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlN0b3JlPC9o"
    "Mz48cCBjbGFzcz0ic3ViIj5FdmVyeXRoaW5nIGxpdmVzIGluIHRoZSBkYXRhYmFzZSDigJQgbm8g"
    "Y29kZSBlZGl0cywgbm8gcmVzdGFydC48L3A+CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9"
    "Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlN0b3JlIG5hbWU8L2xhYmVsPgogICAgICA8aW5wdXQg"
    "Y2xhc3M9ImlucCIgZGF0YS1zZXQ9InN0b3JlX25hbWUiIHZhbHVlPSIke2VzYyhDTVMuc3RvcmVO"
    "YW1lfHxDRkcubmFtZSl9Ij48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+VGFn"
    "bGluZTwvbGFiZWw+CiAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0ic3RvcmVfdGFn"
    "bGluZSIgdmFsdWU9IiR7ZXNjKENNUy5zdG9yZVRhZ3x8Q0ZHLnRhZyl9Ij48L2Rpdj4KICAgIDxk"
    "aXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+VGFnbGluZTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAi"
    "IHZhbHVlPSIke0NGRy50YWd9Ij48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+"
    "U3VwcG9ydCBjb250YWN0PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgcGxhY2Vob2xkZXI9IkB5"
    "b3VyX3N1cHBvcnQiPjwvZGl2PgogICAgPC9kaXY+CiAgIDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5U"
    "b2dnbGVzPC9oMz4KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPkZvcmNlIGpv"
    "aW48c21hbGw+UmVxdWlyZSBjaGFubmVsIG1lbWJlcnNoaXA8L3NtYWxsPjwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuZm9yY2Vfc3ViKT8iIG9uIjoiIn0iIGRhdGEt"
    "c2V0dGluZz0iZm9yY2Vfc3ViIj48L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxk"
    "aXYgY2xhc3M9InQiPk1haW50ZW5hbmNlIG1vZGU8c21hbGw+RnJlZXplIHRoZSBzdG9yZTwvc21h"
    "bGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5tYWludGVuYW5j"
    "ZSk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9Im1haW50ZW5hbmNlIj48L2Rpdj48L2Rpdj4KICAg"
    "IDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPkF1dG8gcmVmdW5kczxzbWFsbD5SZWZ1"
    "bmQgZmFpbGVkIGxvZ2luczwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGcke0ZFQVQu"
    "YXV0b1JlZnVuZD8iIG9uIjoiIn0iIGRhdGEtZmVhdD0iYXV0b1JlZnVuZCI+PC9kaXY+PC9kaXY+"
    "CiAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5SZWZlcnJhbCBwcm9ncmFtPHNt"
    "YWxsPjElIG9mIGludml0ZWQgZGVwb3NpdHM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "InRnJHtGRUFULnJlZkRhc2g/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InJlZkRhc2giPjwvZGl2Pjwv"
    "ZGl2PjwvZGl2PgogICAke0coImkxOG4iLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+TGFuZ3VhZ2Vz"
    "PC9oMz48cCBjbGFzcz0ic3ViIj5Vc2VycyBwaWNrIHRoZWlyIG93bjsgZXZlcnkgc3RyaW5nIGlz"
    "IHRyYW5zbGF0ZWQuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RGVmYXVsdCBs"
    "YW5ndWFnZTwvbGFiZWw+CiAgICAgIDxzZWxlY3QgY2xhc3M9ImlucCIgZGF0YS1zZXRzZWw9Imxh"
    "bmdfZGVmYXVsdCI+CiAgICAgICAgPG9wdGlvbiB2YWx1ZT0iZW4iICR7KEFETS5zZXR0aW5ncyYm"
    "QURNLnNldHRpbmdzLmxhbmc9PT0iZW4iKT8ic2VsZWN0ZWQiOiIifT5FbmdsaXNoPC9vcHRpb24+"
    "CiAgICAgICAgPG9wdGlvbiB2YWx1ZT0iaGkiICR7KEFETS5zZXR0aW5ncyYmQURNLnNldHRpbmdz"
    "Lmxhbmc9PT0iaGkiKT8ic2VsZWN0ZWQiOiIifT7gpLngpL/gpKjgpY3gpKbgpYA8L29wdGlvbj4K"
    "ICAgICAgPC9zZWxlY3Q+PC9kaXY+PC9kaXY+YCl9CiAgICR7Rygic2xhc2hDbWRzIixgPGRpdiBj"
    "bGFzcz0iY2FyZCI+PGgzPlNsYXNoIGNvbW1hbmRzPC9oMz48cCBjbGFzcz0ic3ViIj5SZWdpc3Rl"
    "cmVkIGluIFRlbGVncmFtJ3MgY29tbWFuZCBtZW51LjwvcD4KICAgICAke1tbIi9zdGFydCIsIk9w"
    "ZW4gdGhlIHN0b3JlIl0sWyIvYnV5IiwiSnVtcCB0byBzdG9jayJdLFsiL2JhbGFuY2UiLCJTaG93"
    "IHdhbGxldCJdLFsiL29yZGVycyIsIk15IG9yZGVycyJdLFsiL3N1cHBvcnQiLCJDb250YWN0IHN1"
    "cHBvcnQiXV0KICAgICAgIC5tYXAoKFtjLGRdKT0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj48YiBz"
    "dHlsZT0iY29sb3I6dmFyKC0tdHgpIj4ke2N9PC9iPjwvc3Bhbj48YiBzdHlsZT0iZm9udC13ZWln"
    "aHQ6NTAwO2ZvbnQtc2l6ZTouNzRyZW0iPiR7ZH08L2I+PC9kaXY+YCkuam9pbigiIil9CiAgICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIHNvZnQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiIGRh"
    "dGEtYWRtaW4tYWN0PSJzeW5jIj5TeW5jIENvbW1hbmRzPC9idXR0b24+PC9kaXY+YCl9CiAgICR7"
    "RygiYXV0b0JhY2t1cCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5BdXRvLWJhY2t1cDwvaDM+PHAg"
    "Y2xhc3M9InN1YiI+RGF0YWJhc2UgdG8gYSBwcml2YXRlIGNoYW5uZWwgb24gYSBzY2hlZHVsZS48"
    "L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVs"
    "PkV2ZXJ5IChob3Vycyk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iNiI+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxhc3QgYmFja3VwPC9zcGFuPjxiPjQyIG1pbiBh"
    "Z288L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "MHB4Ij48YnV0dG9uIGNsYXNzPSJidG4iIGRhdGEtYWRtaW4tYWN0PSJiYWNrdXAiPkJhY2sgVXAg"
    "Tm93PC9idXR0b24+CiAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtYWRtaW4t"
    "YWN0PSJyZXN0b3JlIj5SZXN0b3JlPC9idXR0b24+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiYXVk"
    "aXRMb2ciLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QXVkaXQgbG9nPC9oMz48cCBjbGFzcz0ic3Vi"
    "Ij5FdmVyeSBhZG1pbiBhY3Rpb24sIHdpdGggdW5kby48L3A+CiAgICAgJHsoKEFETS5hdWRpdCYm"
    "QURNLmF1ZGl0Lmxlbmd0aCk/QURNLmF1ZGl0Lm1hcChhPT5bYS53aG8sKGEud2hhdCsiICIrYS5k"
    "ZXRhaWwpLnRyaW0oKSxhLndoZW5dKTpbXSkKICAgICAgIC5tYXAoKFt1LGEsdF0pPT5gPGRpdiBj"
    "bGFzcz0ia3YiPjxzcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7dX08L2I+ICR7YX08"
    "L3NwYW4+CiAgICAgICAgIDxiPiR7dH0gPC9iPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PmApfQog"
    "ICAke0coInNhbmRib3giLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHls"
    "ZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+U2FuZGJveCAvIHRlc3QgbW9kZTxzbWFs"
    "bD5GYWtlIG1vbmV5LCBubyByZWFsIGRlbGl2ZXJ5PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LnNhbmRib3gpPyIgb24iOiIifSIgZGF0YS1zZXR0aW5n"
    "PSJzYW5kYm94Ij48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJlbmNyeXB0IixgPGRpdiBj"
    "bGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7"
    "Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFzcz0iZG90Ij48L2k+"
    "RW5jcnlwdGVkPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250"
    "LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlNlc3Npb25zIGFyZSBlbmNyeXB0ZWQgYXQg"
    "cmVzdDwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWEtZ289InNl"
    "Y3VyaXR5Ij5LZXlzPC9idXR0b24+PC9kaXY+YCl9CiAgIDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5E"
    "YW5nZXIgem9uZTwvaDM+PHAgY2xhc3M9InN1YiI+SXJyZXZlcnNpYmxlLiBBc2sgdHdpY2UuPC9w"
    "PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48YnV0dG9u"
    "IGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1hZG1pbi1hY3Q9ImJhY2t1cCI+QmFja3VwPC9idXR0"
    "b24+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmFkIGhvbGQiIGRhdGEtZG9uZT0iRGF0YWJhc2Ug"
    "cmVzZXQgKGRlbW8pIj48c3BhbiBjbGFzcz0iZmlsbCI+PC9zcGFuPkhvbGQgdG8gUmVzZXQgREI8"
    "L2J1dHRvbj48L2Rpdj48L2Rpdj5gOwp9CgpmdW5jdGlvbiBiaW5kQWRtaW4oKXsKICBiaW5kQ29t"
    "bW9uKCk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZmVhdF0iKS5mb3JFYWNo"
    "KHQ9PnQub25jbGljaz0oKT0+ewogICAgY29uc3Qgaz10LmRhdGFzZXQuZmVhdDtGRUFUW2tdPSFG"
    "RUFUW2tdO3QuY2xhc3NMaXN0LnRvZ2dsZSgib24iLEZFQVRba10pOwogICAgcHVzaCgiL2FwaS9h"
    "ZG1pbi9mZWF0dXJlIix7a2V5Omssb246RkVBVFtrXX0pOwogICAgY29uc3QgYz1kb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgiZmNvdW50Iik7CiAgICBpZihjKWMudGV4dENvbnRlbnQ9T2JqZWN0LnZh"
    "bHVlcyhGRUFUKS5maWx0ZXIoQm9vbGVhbikubGVuZ3RoKyIgb2YgIitPYmplY3Qua2V5cyhGRUFU"
    "KS5sZW5ndGg7CiAgICBhcHBseUZlYXR1cmVzKCk7CiAgICBjb25zdCBjYXJkPXQuY2xvc2VzdCgi"
    "LmZjYXJkIiksIG5hbWVFbD1jYXJkP2NhcmQucXVlcnlTZWxlY3RvcigiYiIpOm51bGw7CiAgICBj"
    "b25zdCBsYWJlbD1uYW1lRWw/bmFtZUVsLnRleHRDb250ZW50OmsucmVwbGFjZSgvKFtBLVpdKS9n"
    "LCIgJDEiKS50b0xvd2VyQ2FzZSgpOwogICAgdG9hc3QobGFiZWwrKEZFQVRba10/IiBlbmFibGVk"
    "IjoiIGhpZGRlbiIpKTt9KTsKICBjb25zdCBmcT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZnEi"
    "KTsKICBpZihmcSlmcS5vbmlucHV0PSgpPT57Y29uc3QgcT1mcS52YWx1ZS50cmltKCkudG9Mb3dl"
    "ckNhc2UoKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5mcm93IikuZm9yRWFjaChy"
    "PT5yLmNsYXNzTGlzdC50b2dnbGUoImhpZGUiLCEhcSYmIXIuZGF0YXNldC5mLmluY2x1ZGVzKHEp"
    "KSk7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIuZmdycCIpLmZvckVhY2goZz0+Zy5j"
    "bGFzc0xpc3QudG9nZ2xlKCJoaWRlIiwhZy5xdWVyeVNlbGVjdG9yKCIuZnJvdzpub3QoLmhpZGUp"
    "IikpKTt9OwogIGNvbnN0IHNldEFsbD12PT57T2JqZWN0LmtleXMoRkVBVCkuZm9yRWFjaChrPT5G"
    "RUFUW2tdPXYpO2FwcGx5RmVhdHVyZXMoKTtyZW5kZXJBZG1pbigpO3RvYXN0KHY/IkFsbCBmZWF0"
    "dXJlcyBlbmFibGVkIjoiQWxsIGZlYXR1cmVzIGhpZGRlbiIpO307CiAgY29uc3QgZmE9ZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoImZhbGwiKSxmbj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZm5v"
    "bmUiKSxmcj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZnJlc2V0Iik7CiAgaWYoZmEpZmEub25j"
    "bGljaz0oKT0+c2V0QWxsKHRydWUpOwogIGlmKGZuKWZuLm9uY2xpY2s9KCk9PnNldEFsbChmYWxz"
    "ZSk7CiAgaWYoZnIpZnIub25jbGljaz0oKT0+e09iamVjdC5hc3NpZ24oRkVBVCxGRUFUMCk7YXBw"
    "bHlGZWF0dXJlcygpO3JlbmRlckFkbWluKCk7dG9hc3QoIkZlYXR1cmVzIHJlc2V0Iik7fTsKICBj"
    "b25zdCBzdG1zZz1tPT57Y29uc3QgZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3Rtc2ciKTtp"
    "ZihlKWUudGV4dENvbnRlbnQ9bTt9OwogIGFzeW5jIGZ1bmN0aW9uIGltcG9ydFN0b2NrKHRleHQp"
    "ewogICAgaWYoIXRleHQudHJpbSgpKXt0b2FzdCgiUGFzdGUgc29tZSBsaW5lcyBmaXJzdCIpO3Jl"
    "dHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBz"
    "dG1zZygiSW1wb3J0aW5n4oCmIik7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1p"
    "bi9zdG9jayIse3RleHQ6dGV4dH0pOwogICAgaWYoIXIpe3N0bXNnKCIiKTtyZXR1cm47fQogICAg"
    "Y29uc3Qgbm90ZT1yLmFkZGVkKyIgaW1wb3J0ZWQiKyhyLnNraXBwZWQmJnIuc2tpcHBlZC5sZW5n"
    "dGg/KCIgwrcgIityLnNraXBwZWQubGVuZ3RoKyIgc2tpcHBlZCIpOiIiKTsKICAgIHRvYXN0KHIu"
    "YWRkZWQrIiBhY2NvdW50cyBhZGRlZCIpOwogICAgY29uc3QgYm94PWRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJzdHBhc3RlIik7aWYoYm94KWJveC52YWx1ZT0iIjsKICAgIGF3YWl0IHJlZnJlc2go"
    "KTsKICAgIHN0bXNnKG5vdGUpOwogIH0KICBjb25zdCBzdHNhdmU9ZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoInN0c2F2ZSIpOwogIGlmKHN0c2F2ZSlzdHNhdmUub25jbGljaz0oKT0+aW1wb3J0U3Rv"
    "Y2soKGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdHBhc3RlIil8fHt9KS52YWx1ZXx8IiIpOwog"
    "IGNvbnN0IHN0ZmlsZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RmaWxlIik7CiAgaWYoc3Rm"
    "aWxlKXN0ZmlsZS5vbmNoYW5nZT0oKT0+ewogICAgY29uc3QgZj1zdGZpbGUuZmlsZXMmJnN0Zmls"
    "ZS5maWxlc1swXTtpZighZilyZXR1cm47CiAgICBjb25zdCByZD1uZXcgRmlsZVJlYWRlcigpOwog"
    "ICAgcmQub25sb2FkPSgpPT57Y29uc3QgYm94PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdHBh"
    "c3RlIik7CiAgICAgIGlmKGJveClib3gudmFsdWU9cmQucmVzdWx0O3N0bXNnKGYubmFtZSsiIGxv"
    "YWRlZCDigJQgcHJlc3MgSW1wb3J0Iik7fTsKICAgIHJkLnJlYWRBc1RleHQoZik7CiAgfTsKICBj"
    "b25zdCBibD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYmxibG9jayIpOwogIGlmKGJsKWJsLm9u"
    "Y2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgaT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYmwt"
    "aW4iKTsKICAgIGNvbnN0IHY9aT8oaS52YWx1ZXx8IiIpLnRyaW0oKToiIjsKICAgIGlmKCF2KXt0"
    "b2FzdCgiVHlwZSBhbiBpZCwgcGhvbmUgb3IgVVBJIHJlZmVyZW5jZSIpO2lmKGkpaS5mb2N1cygp"
    "O3JldHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAg"
    "ICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9ibGFja2xpc3QiLHthY3Rpb246ImFk"
    "ZCIsdmFsdWU6dn0pOwogICAgaWYoIXIpcmV0dXJuOwogICAgaS52YWx1ZT0iIjt0b2FzdCgiQmxv"
    "Y2tlZCAiK3YpO2F3YWl0IHJlZnJlc2goKTsKICB9OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JB"
    "bGwoIltkYXRhLWJsLWRlbF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBj"
    "b25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9ibGFja2xpc3QiLHthY3Rpb246ImRlbCIs"
    "aWQ6K2IuZGF0YXNldC5ibERlbH0pOwogICAgaWYocil7dG9hc3QoIlVuYmxvY2tlZCIpO2F3YWl0"
    "IHJlZnJlc2goKTt9CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtbWlz"
    "Y10iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBpZighTElWRSl7dG9hc3Qo"
    "IlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBjb25zdCB3aGF0PWIuZGF0YXNldC5taXNjLCB2"
    "YWw9Yi5kYXRhc2V0Lm1pc2NWYWx1ZXx8IiI7CiAgICBjb25zdCBsYmw9Yi50ZXh0Q29udGVudDti"
    "LmRpc2FibGVkPXRydWU7Yi50ZXh0Q29udGVudD0i4oCmIjsKICAgIGNvbnN0IHI9YXdhaXQgYXBp"
    "VHJ5KCIvYXBpL2FkbWluL21pc2MiLHt3aGF0OndoYXQsdmFsdWU6dmFsfSk7CiAgICBiLmRpc2Fi"
    "bGVkPWZhbHNlO2IudGV4dENvbnRlbnQ9bGJsOwogICAgaWYoIXIpcmV0dXJuOwogICAgaWYoci5s"
    "aW5lcylzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+Q2hh"
    "bm5lbCBjaGVjazwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJ3aGl0ZS1zcGFjZTpw"
    "cmUtbGluZSI+JHtlc2Moci5saW5lcy5qb2luKCJcbiIpKX08L3A+CiAgICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9zZT5DbG9zZTwv"
    "YnV0dG9uPmApOwogICAgZWxzZSBpZih3aGF0PT09ImxvZ3Rlc3QiKXRvYXN0KCJUZXN0IG1lc3Nh"
    "Z2Ugc2VudCB0byB0aGUgbG9nIGNoYW5uZWwiKTsKICAgIGVsc2UgaWYod2hhdD09PSJjbGVhcnN0"
    "b2NrIil0b2FzdChyLnJlbW92ZWQrIiByZW1vdmVkIik7CiAgICBlbHNlIHRvYXN0KCJEb25lIik7"
    "CiAgICBhd2FpdCByZWZyZXNoKCk7CiAgfSk7CiAgY29uc3QgdXBpYWRkPWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJ1cGlhZGQiKTsKICBpZih1cGlhZGQpdXBpYWRkLm9uY2xpY2s9YXN5bmMoKT0+"
    "ewogICAgY29uc3QgaT11cGlhZGQuY2xvc2VzdCgiZGl2IikucXVlcnlTZWxlY3RvcigiaW5wdXQi"
    "KTsKICAgIGNvbnN0IHY9aT8oaS52YWx1ZXx8IiIpLnRyaW0oKToiIjsKICAgIGlmKCF2KXt0b2Fz"
    "dCgiUGFzdGUgdGhlIGV4dHJhIFVQSSBoYW5kbGUiKTtyZXR1cm47fQogICAgY29uc3QgY3VyPShB"
    "RE0ucGF5JiZBRE0ucGF5LmV4dHJhKXx8IiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2Fw"
    "aS9hZG1pbi9zZXR0aW5nIix7a2V5OiJ1cGlfZXh0cmEiLHZhbHVlOmN1cj8oY3VyKyIsIit2KTp2"
    "fSk7CiAgICBpZihyKXtpLnZhbHVlPSIiO3RvYXN0KCJIYW5kbGUgYWRkZWQiKTthd2FpdCByZWZy"
    "ZXNoKCk7fQogIH07CiAgY29uc3QgYWRtaW5hZGQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFk"
    "bWluYWRkIik7CiAgaWYoYWRtaW5hZGQpYWRtaW5hZGQub25jbGljaz1hc3luYygpPT57CiAgICBj"
    "b25zdCBpPWFkbWluYWRkLmNsb3Nlc3QoImRpdiIpLnF1ZXJ5U2VsZWN0b3IoImlucHV0Iik7CiAg"
    "ICBjb25zdCB2PWk/KGkudmFsdWV8fCIiKS50cmltKCkucmVwbGFjZSgvXkAvLCIiKToiIjsKICAg"
    "IGlmKCF2KXt0b2FzdCgiU2VuZCB0aGUgbnVtZXJpYyB1c2VyIGlkIik7cmV0dXJuO30KICAgIGNv"
    "bnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL21pc2MiLHt3aGF0OiJhZGRhZG1pbiIsdmFs"
    "dWU6dn0pOwogICAgaWYocil7aS52YWx1ZT0iIjt0b2FzdCgiQWRtaW4gYWRkZWQgd2l0aCBubyBw"
    "b3dlcnMg4oCUIGdyYW50IHRoZW0gaW4gdGhlIGJvdCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgfTsK"
    "ICBjb25zdCBwcml6ZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicHJpemVzYXZlIik7CiAgaWYo"
    "cHJpemUpcHJpemUub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBpPXByaXplLmNsb3Nlc3Qo"
    "ImRpdiIpLnF1ZXJ5U2VsZWN0b3IoImlucHV0Iik7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgi"
    "L2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OiJsZWFkZXJib2FyZF9wcml6ZSIsdmFsdWU6KGkmJmku"
    "dmFsdWUpfHwiMCJ9KTsKICAgIGlmKHIpdG9hc3QoIlByaXplIHNhdmVkIik7CiAgfTsKICBjb25z"
    "dCBjcGFkZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY3BhZGQiKTsKICBpZihjcGFkZCljcGFk"
    "ZC5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGNvZGU9KChkb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgiY3Bjb2RlIil8fHt9KS52YWx1ZXx8IiIpLnRyaW0oKTsKICAgIGNvbnN0IHBjdD0oKGRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJjcHBjdCIpfHx7fSkudmFsdWV8fCIiKS50cmltKCk7CiAg"
    "ICBpZighY29kZXx8IXBjdCl7dG9hc3QoIkNvZGUgYW5kIHBlcmNlbnQsIHBsZWFzZSIpO3JldHVy"
    "bjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBjb25z"
    "dCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jb3Vwb24iLHtjb2RlOmNvZGUscGVyY2VudDpw"
    "Y3R9KTsKICAgIGlmKCFyKXJldHVybjsKICAgIGNvbnN0IGExPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJjcGNvZGUiKSxhMj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY3BwY3QiKTsKICAgIGlm"
    "KGExKWExLnZhbHVlPSIiO2lmKGEyKWEyLnZhbHVlPSIiOwogICAgdG9hc3QoIkNvdXBvbiAiK2Nv"
    "ZGUudG9VcHBlckNhc2UoKSsiIGlzIGxpdmUiKTthd2FpdCByZWZyZXNoKCk7CiAgfTsKICBjb25z"
    "dCBmZz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZmxhc2hnbyIpOwogIGlmKGZnKWZnLm9uY2xp"
    "Y2s9YXN5bmMoKT0+ewogICAgY29uc3QgY2FyZD1mZy5jbG9zZXN0KCIuY2FyZCIpfHxkb2N1bWVu"
    "dDsKICAgIGNvbnN0IGlucz1jYXJkLnF1ZXJ5U2VsZWN0b3JBbGwoImlucHV0Iik7CiAgICBjb25z"
    "dCBwY3Q9KChpbnNbMF0mJmluc1swXS52YWx1ZSl8fCIyMCIpLCBocnM9KChpbnNbMV0mJmluc1sx"
    "XS52YWx1ZSl8fCI1Iik7CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVy"
    "bjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9mbGFzaCIse3BlcmNlbnQ6"
    "cGN0LGhvdXJzOmhyc30pOwogICAgaWYocil7dG9hc3QoIkZsYXNoIHNhbGUgbGl2ZSDigJQgIitw"
    "Y3QrIiUgZm9yICIraHJzKyJoIik7YXdhaXQgcmVmcmVzaCgpO30KICB9OwogIGNvbnN0IGJjPWRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJiY2dvIik7CiAgaWYoYmMpYmMub25jbGljaz1hc3luYygp"
    "PT57CiAgICBjb25zdCBjYXJkPWJjLmNsb3Nlc3QoIi5jYXJkIil8fGRvY3VtZW50OwogICAgY29u"
    "c3QgdGE9Y2FyZC5xdWVyeVNlbGVjdG9yKCJ0ZXh0YXJlYSIpOwogICAgY29uc3QgdGV4dD0oKHRh"
    "JiZ0YS52YWx1ZSl8fCIiKS50cmltKCk7CiAgICBpZighdGV4dCl7dG9hc3QoIldyaXRlIHRoZSBt"
    "ZXNzYWdlIGZpcnN0Iik7aWYodGEpdGEuZm9jdXMoKTtyZXR1cm47fQogICAgaWYoIUxJVkUpe3Rv"
    "YXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgYmMuZGlzYWJsZWQ9dHJ1ZTtiYy50ZXh0"
    "Q29udGVudD0iU2VuZGluZ+KApiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1p"
    "bi9icm9hZGNhc3QiLHt0ZXh0OnRleHR9KTsKICAgIGJjLmRpc2FibGVkPWZhbHNlO2JjLnRleHRD"
    "b250ZW50PSJTZW5kIEJyb2FkY2FzdCI7CiAgICBpZihyKXt0b2FzdCgiU2VudCB0byAiK3Iuc2Vu"
    "dCsiIHBlb3BsZSIpO2lmKHRhKXRhLnZhbHVlPSIiO30KICB9OwogIGRvY3VtZW50LnF1ZXJ5U2Vs"
    "ZWN0b3JBbGwoIltkYXRhLWRlcC1va10iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57"
    "CiAgICBjb25zdCBpZD1iLmRhdGFzZXQuZGVwT2s7CiAgICBpZighaWQpe3RvYXN0KCJOb3RoaW5n"
    "IHRvIGFwcHJvdmUiKTtyZXR1cm47fQogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRt"
    "aW4vZGVwb3NpdCIse2lkOitpZCxhY3Rpb246ImFwcHJvdmUifSk7CiAgICBpZihyKXt0b2FzdCgi"
    "Q3JlZGl0ZWQiKTthd2FpdCByZWZyZXNoKCk7fQogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIltkYXRhLXJldl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBj"
    "b25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9yZXZpZXciLHtpZDorYi5kYXRhc2V0LnJl"
    "dn0pOwogICAgaWYocil7dG9hc3QoIlVwZGF0ZWQiKTthd2FpdCByZWZyZXNoKCk7fQogIH0pOwog"
    "IGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXNldHRpbmddIikuZm9yRWFjaCh0PT57"
    "CiAgICB0Lm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25zdCBrZXk9dC5kYXRhc2V0LnNldHRp"
    "bmcsIG9uPSF0LmNsYXNzTGlzdC5jb250YWlucygib24iKTsKICAgICAgdC5jbGFzc0xpc3QudG9n"
    "Z2xlKCJvbiIsb24pOwogICAgICBpZighTElWRSl7dG9hc3Qob24/Ik9uIjoiT2ZmIik7cmV0dXJu"
    "O30KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc2V0dGluZyIse2tleTpr"
    "ZXksdmFsdWU6b24/IjEiOiIwIn0pOwogICAgICBpZighcil7dC5jbGFzc0xpc3QudG9nZ2xlKCJv"
    "biIsIW9uKTtyZXR1cm47fQogICAgICBBRE0uc3c9QURNLnN3fHx7fTtBRE0uc3dba2V5XT1vbjsK"
    "ICAgICAgdG9hc3Qoa2V5LnJlcGxhY2UoL18vZywiICIpKyhvbj8iIG9uIjoiIG9mZiIpKTsKICAg"
    "IH07CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtYWRtaW4tYWN0XSIp"
    "LmZvckVhY2goYj0+ewogICAgYi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgICAgY29uc3QgYWN0PWIu"
    "ZGF0YXNldC5hZG1pbkFjdDsKICAgICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTty"
    "ZXR1cm47fQogICAgICBjb25zdCBsYWJlbD1iLnRleHRDb250ZW50O2IuZGlzYWJsZWQ9dHJ1ZTti"
    "LnRleHRDb250ZW50PSJXb3JraW5n4oCmIjsKICAgICAgbGV0IHI9bnVsbDsKICAgICAgaWYoYWN0"
    "PT09ImJhY2t1cCIpICAgICAgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vYmFja3VwIix7fSk7"
    "CiAgICAgIGVsc2UgaWYoYWN0PT09ImRpZ2VzdCIpIHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWlu"
    "L2RpZ2VzdCIse30pOwogICAgICBlbHNlIGlmKGFjdD09PSJleHBvcnQiKSByPWF3YWl0IGFwaVRy"
    "eSgiL2FwaS9hZG1pbi9leHBvcnQiLHt9KTsKICAgICAgZWxzZSBpZihhY3Q9PT0ic3luYyIpICAg"
    "cj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc3luYyIse30pOwogICAgICBiLmRpc2FibGVkPWZh"
    "bHNlO2IudGV4dENvbnRlbnQ9bGFiZWw7CiAgICAgIGlmKCFyKXJldHVybjsKICAgICAgaWYoYWN0"
    "PT09ImJhY2t1cCIpdG9hc3QoIkRhdGFiYXNlIHNlbnQgdG8geW91IGluIGNoYXQiKTsKICAgICAg"
    "aWYoYWN0PT09ImRpZ2VzdCIpdG9hc3QoIkRpZ2VzdCBzZW50IHRvIHRoZSBhZG1pbnMiKTsKICAg"
    "ICAgaWYoYWN0PT09ImV4cG9ydCIpdG9hc3Qoci5yb3dzKyIgb3JkZXJzIHNlbnQgYXMgQ1NWIik7"
    "CiAgICAgIGlmKGFjdD09PSJzeW5jIil0b2FzdCgiQ29tbWFuZHMgc3luY2VkIHdpdGggVGVsZWdy"
    "YW0iKTsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVy"
    "eVNlbGVjdG9yQWxsKCJbZGF0YS1wcmljZV0iKS5mb3JFYWNoKGlucD0+ewogICAgaW5wLm9uY2hh"
    "bmdlPWFzeW5jKCk9PnsKICAgICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1"
    "cm47fQogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9wcmljaW5nIix7a2V5"
    "OmlucC5kYXRhc2V0LnByaWNlLHZhbHVlOmlucC52YWx1ZX0pOwogICAgICBpZihyKXt0b2FzdCgi"
    "UHJpY2luZyB1cGRhdGVkIik7YXdhaXQgcmVmcmVzaCgpO30KICAgIH07CiAgfSk7CiAgZG9jdW1l"
    "bnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY3ByaWNlXSIpLmZvckVhY2goaW5wPT57CiAgICBp"
    "bnAub25jaGFuZ2U9YXN5bmMoKT0+ewogICAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25s"
    "eSIpO3JldHVybjt9CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3ByaWNp"
    "bmciLHtjb3VudHJ5OmlucC5kYXRhc2V0LmNwcmljZSxwcmljZTppbnAudmFsdWV9KTsKICAgICAg"
    "aWYocil7dG9hc3QoaW5wLmRhdGFzZXQuY3ByaWNlKyIgbm93ICIrbW9uZXkoaW5wLnZhbHVlKSk7"
    "YXdhaXQgcmVmcmVzaCgpO30KICAgIH07CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiW2RhdGEtcWFdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IHdoYXQ9"
    "Yi5kYXRhc2V0LnFhOwogICAgY29uc3QgZ289KHRhYixtc2cpPT57CiAgICAgIGNvbnN0IHQ9ZG9j"
    "dW1lbnQucXVlcnlTZWxlY3RvcihgI2FzdWIgYnV0dG9uW2RhdGEtYT0iJHt0YWJ9Il1gKTsKICAg"
    "ICAgaWYodCl7dC5jbGljaygpO3RvYXN0KG1zZyk7fWVsc2UgdG9hc3QobXNnKTsKICAgIH07CiAg"
    "ICBpZih3aGF0PT09ImJyb2FkY2FzdCIpcmV0dXJuIGdvKCJtYXJrZXQiLCJDb21wb3NlIHRoZSBi"
    "cm9hZGNhc3QgZnJvbSB0aGUgYm90OiAvYWRtaW4g4oaSIE1hcmtldGluZyIpOwogICAgaWYod2hh"
    "dD09PSJzdG9jayIpcmV0dXJuIGdvKCJzdG9jayIsIkFkZCBzdG9jayBmcm9tIHRoZSBib3Q6IC9h"
    "ZG1pbiDihpIgU3RvY2sg4oaSIEFkZCBzdG9jayIpOwogICAgaWYod2hhdD09PSJmbGFzaCIpcmV0"
    "dXJuIGdvKCJtYXJrZXQiLCJGbGFzaCBzYWxlIGxpdmVzIGhlcmUiKTsKICAgIGlmKHdoYXQ9PT0i"
    "YmFja3VwIilyZXR1cm4gdG9hc3QoIkJhY2t1cHMgYXJlIHNlbnQgdG8geW91ciBsb2cgY2hhbm5l"
    "bCDigJQgL2FkbWluIOKGkiBTZXR0aW5ncyIpOwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIltkYXRhLXNldF0iKS5mb3JFYWNoKGlucD0+ewogICAgaW5wLm9uY2hhbmdlPWFzeW5j"
    "KCk9PnsKICAgICAgY29uc3Qga2V5PWlucC5kYXRhc2V0LnNldCwgdmFsPWlucC52YWx1ZTsKICAg"
    "ICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgICBjb25zdCBy"
    "PWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OmtleSx2YWx1ZTp2YWx9KTsK"
    "ICAgICAgaWYocil7dG9hc3Qoa2V5LnJlcGxhY2UoL18vZywiICIpKyIgc2F2ZWQiKTthd2FpdCBy"
    "ZWZyZXNoKCk7fQogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0"
    "YS1jbXNdIikuZm9yRWFjaCh0PT57CiAgICB0Lm9uaW5wdXQ9KCk9PntDTVNbdC5kYXRhc2V0LmNt"
    "c109dC52YWx1ZTthcHBseUNNUygpOwogICAgICBjbGVhclRpbWVvdXQodC5fc3YpO3QuX3N2PXNl"
    "dFRpbWVvdXQoKCk9PnB1c2goIi9hcGkvYWRtaW4vY29udGVudCIse2tleTp0LmRhdGFzZXQuY21z"
    "LHZhbHVlOnQudmFsdWV9KSw1MDApOwogICAgICB0LmNsb3Nlc3QoIi5jbXNyb3ciKS5kYXRhc2V0"
    "LmY9KHQuY2xvc2VzdCgiLmNtc3JvdyIpLnF1ZXJ5U2VsZWN0b3IoImxhYmVsIikudGV4dENvbnRl"
    "bnQrIiAiK3QudmFsdWUpLnRvTG93ZXJDYXNlKCk7fTt9KTsKICBjb25zdCBjcT1kb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgiY3EiKTsKICBpZihjcSljcS5vbmlucHV0PSgpPT57Y29uc3QgcT1jcS52"
    "YWx1ZS50cmltKCkudG9Mb3dlckNhc2UoKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "Ii5jbXNyb3ciKS5mb3JFYWNoKHI9PnIuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZSIsISFxJiYhci5k"
    "YXRhc2V0LmYuaW5jbHVkZXMocSkpKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5j"
    "bXNncnAiKS5mb3JFYWNoKGc9PmcuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZSIsIWcucXVlcnlTZWxl"
    "Y3RvcigiLmNtc3Jvdzpub3QoLmhpZGUpIikpKTt9OwogIGNvbnN0IGNyPWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJjbXNyZXNldCIpOwogIGlmKGNyKWNyLm9uY2xpY2s9KCk9PntPYmplY3QuYXNz"
    "aWduKENNUyxDTVMwKTthcHBseUNNUygpO3JlbmRlckFkbWluKCk7dG9hc3QoIlRleHQgcmVzZXQg"
    "dG8gZGVmYXVsdHMiKTt9OwogIGNvbnN0IGFkZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2gt"
    "YWRkIik7CiAgaWYoYWRkKWFkZC5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGk9ZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoImNoLWluIiksdj0oaS52YWx1ZXx8IiIpLnRyaW0oKTsKICAgIGlm"
    "KCF2KXt0b2FzdCgiUGFzdGUgYSBjaGFubmVsIGxpbmsgZmlyc3QiKTtpLmZvY3VzKCk7cmV0dXJu"
    "fQogICAgY29uc3QgcmVxPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjaC10eXBlIikudmFsdWU9"
    "PT0iMSI7CiAgICBpZihMSVZFKXsKICAgICAgYWRkLmRpc2FibGVkPXRydWU7CiAgICAgIGNvbnN0"
    "IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rpb246ImFkZCIsaGFuZGxl"
    "OnYscmVxdWlyZWQ6cmVxfSk7CiAgICAgIGFkZC5kaXNhYmxlZD1mYWxzZTsKICAgICAgaWYoIXIp"
    "cmV0dXJuOwogICAgICBpLnZhbHVlPSIiO2F3YWl0IHJlZnJlc2goKTt0b2FzdCgiQ2hhbm5lbCBz"
    "YXZlZCDigJQgdGhlIGJvdCB1c2VzIGl0IG5vdyIpO3JldHVybjsKICAgIH0KICAgIENIQU5ORUxT"
    "LnB1c2goe2lkOkRhdGUubm93KCksbmFtZTp2LnJlcGxhY2UoL15odHRwcz86XC9cL3RcLm1lXC8v"
    "LCJAIikuc2xpY2UoMCwyNiksaGFuZGxlOnYscmVxOnJlcSxvbjp0cnVlLG1lbWJlcnM6IuKAlCJ9"
    "KTsKICAgIGkudmFsdWU9IiI7cmVuZGVyQWRtaW4oKTt0b2FzdCgiQ2hhbm5lbCBhZGRlZCIpO307"
    "CiAgY29uc3QgbGdhZGQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImxnLWFkZCIpOwogIGlmKGxn"
    "YWRkKWxnYWRkLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgaT1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgibGctaW4iKSx2PShpLnZhbHVlfHwiIikudHJpbSgpOwogICAgaWYoIXYpe3RvYXN0"
    "KCJQYXN0ZSBhIGxvZyBjaGFubmVsIGZpcnN0Iik7aS5mb2N1cygpO3JldHVybn0KICAgIGlmKExJ"
    "VkUpewogICAgICBsZ2FkZC5kaXNhYmxlZD10cnVlOwogICAgICBjb25zdCByPWF3YWl0IGFwaVRy"
    "eSgiL2FwaS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJhZGQiLGhhbmRsZTp2LGxvZzp0cnVlfSk7"
    "CiAgICAgIGxnYWRkLmRpc2FibGVkPWZhbHNlOwogICAgICBpZighcilyZXR1cm47CiAgICAgIGku"
    "dmFsdWU9IiI7YXdhaXQgcmVmcmVzaCgpO3RvYXN0KCJMb2cgY2hhbm5lbCBzYXZlZCIpO3JldHVy"
    "bjsKICAgIH0KICAgIExPR0NILnB1c2goe2lkOkRhdGUubm93KCksbmFtZTp2LnJlcGxhY2UoL15o"
    "dHRwcz86XC9cL3RcLm1lXC8vLCJAIikuc2xpY2UoMCwyNCksaGFuZGxlOnYscHJpbWFyeTpMT0dD"
    "SC5sZW5ndGg9PT0wLG9uOnRydWV9KTsKICAgIGkudmFsdWU9IiI7cmVuZGVyQWRtaW4oKTt0b2Fz"
    "dCgiTG9nIGNoYW5uZWwgYWRkZWQiKTt9OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltk"
    "YXRhLWNoZGVsXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKExJVkUp"
    "e2NvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rpb246ImRlbCIs"
    "aWQ6K2IuZGF0YXNldC5jaGRlbH0pOwogICAgICBpZihyKXthd2FpdCByZWZyZXNoKCk7dG9hc3Qo"
    "IkNoYW5uZWwgcmVtb3ZlZCIpO31yZXR1cm47fQogICAgQ0hBTk5FTFM9Q0hBTk5FTFMuZmlsdGVy"
    "KGM9PmMuaWQhPWIuZGF0YXNldC5jaGRlbCk7cmVuZGVyQWRtaW4oKTt0b2FzdCgiQ2hhbm5lbCBy"
    "ZW1vdmVkIil9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1jaHJlcV0iKS5m"
    "b3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBpZihMSVZFKXtjb25zdCByPWF3YWl0"
    "IGFwaVRyeSgiL2FwaS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJyZXF1aXJlZCIsaWQ6K2IuZGF0"
    "YXNldC5jaHJlcX0pOwogICAgICBpZihyKXthd2FpdCByZWZyZXNoKCk7dG9hc3QoIlVwZGF0ZWQi"
    "KTt9cmV0dXJuO30KICAgIGNvbnN0IGM9Q0hBTk5FTFMuZmluZCh4PT54LmlkPT1iLmRhdGFzZXQu"
    "Y2hyZXEpO2MucmVxPSFjLnJlcTtyZW5kZXJBZG1pbigpO3RvYXN0KGMucmVxPyJOb3cgcmVxdWly"
    "ZWQiOiJOb3cgb3B0aW9uYWwiKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRh"
    "LWNob25dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYoTElWRSl7Y29u"
    "c3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vY2hhbm5lbCIse2FjdGlvbjoidG9nZ2xlIixp"
    "ZDorYi5kYXRhc2V0LmNob259KTsKICAgICAgaWYocil7YXdhaXQgcmVmcmVzaCgpO3RvYXN0KCJV"
    "cGRhdGVkIik7fXJldHVybjt9CiAgICBjb25zdCBjPUNIQU5ORUxTLmZpbmQoeD0+eC5pZD09Yi5k"
    "YXRhc2V0LmNob24pO2Mub249IWMub247cmVuZGVyQWRtaW4oKTt0b2FzdChjLm9uPyJDaGFubmVs"
    "IGVuYWJsZWQiOiJDaGFubmVsIHBhdXNlZCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiW2RhdGEtbGdkZWxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IHdh"
    "c1A9TE9HQ0guZmluZChsPT5sLmlkPT1iLmRhdGFzZXQubGdkZWwpPy5wcmltYXJ5OwogICAgTE9H"
    "Q0g9TE9HQ0guZmlsdGVyKGw9PmwuaWQhPWIuZGF0YXNldC5sZ2RlbCk7CiAgICBpZih3YXNQJiZM"
    "T0dDSFswXSlMT0dDSFswXS5wcmltYXJ5PXRydWU7cmVuZGVyQWRtaW4oKTt0b2FzdCgiTG9nIGNo"
    "YW5uZWwgcmVtb3ZlZCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtbGdz"
    "dGFyXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgICBMT0dDSC5mb3JFYWNoKGw9Pmwu"
    "cHJpbWFyeT0obC5pZD09Yi5kYXRhc2V0Lmxnc3RhcikpO3JlbmRlckFkbWluKCk7dG9hc3QoIlBy"
    "ZWZlcnJlZCBsb2cgY2hhbm5lbCBzZXQiKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IltkYXRhLWxnb25dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9Pntjb25zdCBsPUxPR0NILmZp"
    "bmQoeD0+eC5pZD09Yi5kYXRhc2V0Lmxnb24pO2wub249IWwub247cmVuZGVyQWRtaW4oKTt0b2Fz"
    "dChsLm9uPyJDaGFubmVsIG9uIjoiQ2hhbm5lbCBtdXRlZCIpfSk7CiAgZG9jdW1lbnQucXVlcnlT"
    "ZWxlY3RvckFsbCgiW2RhdGEtZXZvbl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+e2NvbnN0"
    "IGU9RVZFTlRTWytiLmRhdGFzZXQuZXZvbl07ZS5vbj0hZS5vbjtyZW5kZXJBZG1pbigpfSk7CiAg"
    "ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZXZjaF0iKS5mb3JFYWNoKHM9PnMub25j"
    "aGFuZ2U9KCk9PntFVkVOVFNbK3MuZGF0YXNldC5ldmNoXS5jaD0rcy52YWx1ZTt0b2FzdCgiUm91"
    "dGluZyB1cGRhdGVkIil9KTsKICBjb25zdCBhbGw9ZG9jdW1lbnQucXVlcnlTZWxlY3RvcigiW2Rh"
    "dGEtZXYtYWxsXSIpOwogIGlmKGFsbClhbGwub25jbGljaz0oKT0+e2NvbnN0IHQ9IUVWRU5UUy5l"
    "dmVyeShlPT5lLm9uKTtFVkVOVFMuZm9yRWFjaChlPT5lLm9uPXQpO3JlbmRlckFkbWluKCk7dG9h"
    "c3QodD8iQWxsIGV2ZW50cyBvbiI6IkFsbCBldmVudHMgb2ZmIil9OwogIGNvbnN0IG09ZG9jdW1l"
    "bnQucXVlcnlTZWxlY3RvcigiW2RhdGEtbWFzdGVyXSIpOwogIGlmKG0pbS5vbmNsaWNrPSgpPT57"
    "TE9HU19NQVNURVI9IUxPR1NfTUFTVEVSO3JlbmRlckFkbWluKCk7dG9hc3QoTE9HU19NQVNURVI/"
    "IkxvZ2dpbmcgZW5hYmxlZCI6IkFsbCBsb2dnaW5nIGRpc2FibGVkIil9OwogIGRvY3VtZW50LnF1"
    "ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWEtZ29dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsK"
    "ICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNhc3ViIGJ1dHRvbiIpLmZvckVhY2goeD0+"
    "eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseC5kYXRhc2V0LmE9PT1iLmRhdGFzZXQuYUdvKSk7CiAg"
    "ICBBVEFCPWIuZGF0YXNldC5hR287cmVuZGVyQWRtaW4oKTt3aW5kb3cuc2Nyb2xsVG8oMCwwKTt9"
    "KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS10Z10iKS5mb3JFYWNoKHQ9PnQu"
    "b25jbGljaz0oKT0+dC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIpKTsKICBkb2N1bWVudC5xdWVyeVNl"
    "bGVjdG9yQWxsKCJbZGF0YS1zZXRzZWxdIikuZm9yRWFjaChzZWw9PnNlbC5vbmNoYW5nZT1hc3lu"
    "YygpPT57CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBj"
    "b25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OnNlbC5kYXRhc2V0"
    "LnNldHNlbCx2YWx1ZTpzZWwudmFsdWV9KTsKICAgIGlmKHIpdG9hc3QoIlNhdmVkIik7CiAgfSk7"
    "Cn0KCmNvbnN0IF9jYz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2FydGNoaXAiKTsKaWYoX2Nj"
    "KV9jYy5vbmNsaWNrPWNhcnRTaGVldDsKcmVuZGVyKCk7cmVuZGVyQWRtaW4oKTthcHBseUZlYXR1"
    "cmVzKCk7CgovKiDilIDilIAgbGl2ZSBkYXRhOiBldmVyeXRoaW5nIGJlbG93IGNvbWVzIGZyb20g"
    "dGhlIGJvdCBkYXRhYmFzZSB3aGVuIHNlcnZlZCBieSB0aGUgYm90IOKUgOKUgCAqLwovKiBMSVZF"
    "IC8gTUUgLyBURyAvIElOSVQgYXJlIGRlY2xhcmVkIGF0IHRoZSB2ZXJ5IHRvcCBvZiB0aGlzIHNj"
    "cmlwdCAqLwphc3luYyBmdW5jdGlvbiBhcGkocGF0aCxib2R5KXsKICBjb25zdCBvPXtoZWFkZXJz"
    "OnsiQ29udGVudC1UeXBlIjoiYXBwbGljYXRpb24vanNvbiIsIlgtSW5pdC1EYXRhIjpJTklULAog"
    "ICAgIlgtVGVsZWdyYW0tSW5pdC1EYXRhIjpJTklULCJYLUFwcC1LZXkiOkFQUEtFWX19OwogIGlm"
    "KGJvZHkhPT11bmRlZmluZWQpe28ubWV0aG9kPSJQT1NUIjtvLmJvZHk9SlNPTi5zdHJpbmdpZnko"
    "Ym9keSk7fQogIGNvbnN0IHI9YXdhaXQgZmV0Y2gocGF0aCxvKTsKICBsZXQgZD1udWxsO3RyeXtk"
    "PWF3YWl0IHIuanNvbigpO31jYXRjaChlcnIpe30KICBpZighci5vayl7Y29uc3QgZT1uZXcgRXJy"
    "b3IoKGQmJmQuZXJyb3IpfHwocGF0aCsiICIrci5zdGF0dXMpKTtlLnN0YXR1cz1yLnN0YXR1cztl"
    "LmRhdGE9ZDt0aHJvdyBlO30KICByZXR1cm4gZDsKfQpmdW5jdGlvbiBhZG1pbkFsbG93ZWQoKXty"
    "ZXR1cm4gIUxJVkUgfHwgISEoTUUmJk1FLmFkbWluKTt9CmZ1bmN0aW9uIGFwcGx5U3RhdGUoZCl7"
    "CiAgLyogZXZlcnkgbGlzdCBpcyByZXBsYWNlZCwgZXZlbiB3aGVuIGVtcHR5IOKAlCBhIG5ldyBz"
    "aG9wIG11c3QgbmV2ZXIgc2hvdyBkZW1vIGRhdGEgKi8KICBpZihkLmZlYXR1cmVzKU9iamVjdC5r"
    "ZXlzKGQuZmVhdHVyZXMpLmZvckVhY2goaz0+e2lmKGsgaW4gRkVBVClGRUFUW2tdPSEhZC5mZWF0"
    "dXJlc1trXTt9KTsKICBpZihkLmNvbnRlbnQpT2JqZWN0LmtleXMoZC5jb250ZW50KS5mb3JFYWNo"
    "KGs9PntpZihrIGluIENNUyYmZC5jb250ZW50W2tdIT1udWxsKUNNU1trXT1kLmNvbnRlbnRba107"
    "fSk7CiAgaWYoQXJyYXkuaXNBcnJheShkLnN0b2NrKSl7U1RPQ0subGVuZ3RoPTA7ZC5zdG9jay5m"
    "b3JFYWNoKHg9PlNUT0NLLnB1c2goeCkpO0ZJTFRFUkVEPVNUT0NLLnNsaWNlKCk7fQogIGlmKEFy"
    "cmF5LmlzQXJyYXkoZC5vcmRlcnMpKXtPUkRFUlMubGVuZ3RoPTA7ZC5vcmRlcnMuZm9yRWFjaCh4"
    "PT5PUkRFUlMucHVzaCh4KSk7fQogIGlmKEFycmF5LmlzQXJyYXkoZC50eCkpe1RYLmxlbmd0aD0w"
    "O2QudHguZm9yRWFjaCh4PT5UWC5wdXNoKHgpKTt9CiAgaWYoQXJyYXkuaXNBcnJheShkLmZlZWQp"
    "KUZFRUQ9ZC5mZWVkOwogIGlmKGQuZWFybilFQVJOPU9iamVjdC5hc3NpZ24oe30sRUFSTixkLmVh"
    "cm4pOwogIGlmKGQucHJlZnMpUFJFRlM9T2JqZWN0LmFzc2lnbih7fSxQUkVGUyxkLnByZWZzKTsK"
    "ICBpZihBcnJheS5pc0FycmF5KGQuYWxlcnRzKSl3aW5kb3cuX19hbGVydHM9ZC5hbGVydHM7CiAg"
    "aWYoZC5zdG9yZSl7CiAgICBTVE9SRT1PYmplY3QuYXNzaWduKHt9LFNUT1JFLGQuc3RvcmUpOwog"
    "ICAgaWYoZC5zdG9yZS5uYW1lKUNGRy5uYW1lPWQuc3RvcmUubmFtZTsKICAgIGlmKGQuc3RvcmUu"
    "dGFnKUNGRy50YWc9ZC5zdG9yZS50YWc7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJb"
    "ZGF0YS1zdG9yZW5hbWVdIikuZm9yRWFjaChlPT5lLnRleHRDb250ZW50PUNGRy5uYW1lKTsKICAg"
    "IGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXN0b3JldGFnXSIpLmZvckVhY2goZT0+"
    "ZS50ZXh0Q29udGVudD1DRkcudGFnKTsKICAgIGRvY3VtZW50LnRpdGxlPUNGRy5uYW1lOwogIH0K"
    "ICBpZihkLmFkbSl7CiAgICBBRE09T2JqZWN0LmFzc2lnbih7fSxBRE0sZC5hZG0pOwogICAgaWYo"
    "QXJyYXkuaXNBcnJheShBRE0uY2hhbm5lbHMpKQogICAgICBDSEFOTkVMUz1BRE0uY2hhbm5lbHMu"
    "bWFwKGM9Pih7aWQ6Yy5pZCxuYW1lOmMubmFtZSxoYW5kbGU6Yy5oYW5kbGV8fCIiLAogICAgICAg"
    "IHJlcTohIWMucmVxdWlyZWQsb246ISFjLmFjdGl2ZSxtZW1iZXJzOiLigJQifSkpOwogICAgaWYo"
    "QURNLmxvZ3MpewogICAgICBMT0dTX01BU1RFUj0hIUFETS5sb2dzLm1hc3RlcjsKICAgICAgaWYo"
    "QXJyYXkuaXNBcnJheShBRE0ubG9ncy5jaGFubmVscykpCiAgICAgICAgTE9HQ0g9QURNLmxvZ3Mu"
    "Y2hhbm5lbHMubWFwKGM9Pih7aWQ6Yy5pZCxuYW1lOmMubmFtZSxoYW5kbGU6IiIsCiAgICAgICAg"
    "ICBwcmltYXJ5OiEhYy5wcmltYXJ5LG9uOiEhYy5hY3RpdmV9KSk7CiAgICAgIGlmKEFycmF5Lmlz"
    "QXJyYXkoQURNLmxvZ3MuZXZlbnRzKSYmQURNLmxvZ3MuZXZlbnRzLmxlbmd0aCkKICAgICAgICBF"
    "VkVOVFM9QURNLmxvZ3MuZXZlbnRzLm1hcChlPT4oe2s6ZS5sYWJlbCxvbjohIWUub24sY2g6KExP"
    "R0NIWzBdfHx7fSkuaWR8fDF9KSk7CiAgICB9CiAgfQogIGlmKGQucmF0aW5ncylSRVZJRVdTPWQu"
    "cmF0aW5nczsKICBpZih0eXBlb2YgZC5iYWxhbmNlPT09Im51bWJlciIpQkFMPWQuYmFsYW5jZTsK"
    "ICBpZihkLm1lKU1FPWQubWU7CiAgaWYoZC5jYXJ0KUNBUlQ9ZC5jYXJ0OwogIGlmKGQud2lzaGxp"
    "c3QpV0lTSD1kLndpc2hsaXN0Owp9CmZ1bmN0aW9uIHNob3dTd2l0Y2gob24pewogIGNvbnN0IHN3"
    "PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdyIpOwogIGlmKHN3KXN3LnN0eWxlLmRpc3BsYXk9"
    "b24/ImZsZXgiOiJub25lIjsKfQp2YXIgTE9DS19USVRMRT0iIixMT0NLX01TRz0iIjsKZnVuY3Rp"
    "b24gbG9ja2VkU2NyZWVuKHRpdGxlLG1zZyl7CiAgTE9DS0VEPXRydWU7TE9DS19USVRMRT10aXRs"
    "ZTtMT0NLX01TRz1tc2c7CiAgc2hvd1N3aXRjaChmYWxzZSk7CiAgcGFpbnRMb2NrZWQoKTsKfQpm"
    "dW5jdGlvbiBwYWludExvY2tlZCgpewogIGNvbnN0IHN0PWRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJzdGFnZSIpOwogIGlmKHN0KXN0LmlubmVySFRNTD0nPGRpdiBjbGFzcz0iY2FyZCIgc3R5bGU9"
    "Im1hcmdpbjoxOHB4O3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MjJweCI+JwogICAgKyc8aDMg"
    "c3R5bGU9Im1hcmdpbjowIDAgOHB4Ij4nK0xPQ0tfVElUTEUrJzwvaDM+JwogICAgKyc8cCBjbGFz"
    "cz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAgMCAxNHB4Ij4nK0xPQ0tfTVNHKyc8L3A+JwogICAgKyc8"
    "YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIG9uY2xpY2s9ImxvY2F0aW9uLnJlbG9hZCgpIj5Ucnkg"
    "YWdhaW48L2J1dHRvbj48L2Rpdj4nOwogIFsidGFicyIsImRvY2siXS5mb3JFYWNoKGlkPT57Y29u"
    "c3QgZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZChpZCk7aWYoZSllLnN0eWxlLmRpc3BsYXk9Im5v"
    "bmUiO30pOwp9CmFzeW5jIGZ1bmN0aW9uIGFwaVRyeShwYXRoLGJvZHkpewogIHRyeXtyZXR1cm4g"
    "YXdhaXQgYXBpKHBhdGgsYm9keSk7fQogIGNhdGNoKGUpe3RvYXN0KFN0cmluZyhlJiZlLm1lc3Nh"
    "Z2V8fGUpLnNsaWNlKDAsOTApKTtyZXR1cm4gbnVsbDt9Cn0KYXN5bmMgZnVuY3Rpb24gcmVmcmVz"
    "aCgpewogIGlmKCFMSVZFKXJldHVybjsKICB0cnl7CiAgICBhcHBseVN0YXRlKGF3YWl0IGFwaSgi"
    "L2FwaS9zdGF0ZSIpKTsKICAgIGFwcGx5Q01TKCk7YXBwbHlGZWF0dXJlcygpO3N5bmNCYWwoKTty"
    "ZW5kZXIoKTtyZW5kZXJBZG1pbigpOwogIH1jYXRjaChlKXt9Cn0KYXN5bmMgZnVuY3Rpb24gY29w"
    "eVRleHQodCwgbGFiZWwpewogIGNvbnN0IHR4dD1TdHJpbmcodHx8IiIpOwogIGlmKCF0eHQpe3Rv"
    "YXN0KCJOb3RoaW5nIHRvIGNvcHkiKTtyZXR1cm4gZmFsc2U7fQogIHRyeXsKICAgIGlmKG5hdmln"
    "YXRvci5jbGlwYm9hcmQmJndpbmRvdy5pc1NlY3VyZUNvbnRleHQpe2F3YWl0IG5hdmlnYXRvci5j"
    "bGlwYm9hcmQud3JpdGVUZXh0KHR4dCk7fQogICAgZWxzZXsKICAgICAgY29uc3QgdGE9ZG9jdW1l"
    "bnQuY3JlYXRlRWxlbWVudCgidGV4dGFyZWEiKTsKICAgICAgdGEudmFsdWU9dHh0O3RhLnNldEF0"
    "dHJpYnV0ZSgicmVhZG9ubHkiLCIiKTsKICAgICAgdGEuc3R5bGUuY3NzVGV4dD0icG9zaXRpb246"
    "Zml4ZWQ7bGVmdDotOTk5OXB4O29wYWNpdHk6MCI7CiAgICAgIGRvY3VtZW50LmJvZHkuYXBwZW5k"
    "Q2hpbGQodGEpO3RhLnNlbGVjdCgpO3RhLnNldFNlbGVjdGlvblJhbmdlKDAsdHh0Lmxlbmd0aCk7"
    "CiAgICAgIGNvbnN0IG9rPWRvY3VtZW50LmV4ZWNDb21tYW5kKCJjb3B5Iik7ZG9jdW1lbnQuYm9k"
    "eS5yZW1vdmVDaGlsZCh0YSk7CiAgICAgIGlmKCFvayl0aHJvdyBuZXcgRXJyb3IoImNvcHkgcmVm"
    "dXNlZCIpOwogICAgfQogICAgdG9hc3QoKGxhYmVsfHwiQ29waWVkIikrIjogIisodHh0Lmxlbmd0"
    "aD4yNj90eHQuc2xpY2UoMCwyNikrIuKApiI6dHh0KSk7CiAgICByZXR1cm4gdHJ1ZTsKICB9Y2F0"
    "Y2goZSl7CiAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJl"
    "bSI+Q29weSB0aGlzPC9oMz4KICAgICAgPHAgY2xhc3M9InN1YiI+WW91ciBicm93c2VyIGJsb2Nr"
    "ZWQgdGhlIGNsaXBib2FyZCDigJQgc2VsZWN0IGFuZCBjb3B5LjwvcD4KICAgICAgPHRleHRhcmVh"
    "IGNsYXNzPSJpbnAiIHJvd3M9IjMiIHJlYWRvbmx5IG9uY2xpY2s9InRoaXMuc2VsZWN0KCkiPiR7"
    "ZXNjKHR4dCl9PC90ZXh0YXJlYT4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMHB4IiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRvbj5gKTsKICAgIHJldHVy"
    "biBmYWxzZTsKICB9Cn0KbGV0IFNUT1JFPXt1cGk6IiIscGF5ZWU6IiIscHJlZml4OiJQQVkiLHRl"
    "cm1zOiIiLHN1cHBvcnQ6IiJ9OwpsZXQgUFJFRlM9e3Jlc3RvY2s6dHJ1ZSxyZWNlaXB0czp0cnVl"
    "LHByb21vczp0cnVlLHB1c2g6dHJ1ZX07CmxldCBPRkZFUj0iIjsKZnVuY3Rpb24gb3BlblBheShs"
    "aW5rKXsKICBpZighbGluaylyZXR1cm47CiAgdHJ5ewogICAgaWYoVEcmJlRHLm9wZW5MaW5rJiYv"
    "Xmh0dHBzPzovLnRlc3QobGluaykpVEcub3BlbkxpbmsobGluayk7CiAgICBlbHNlIHdpbmRvdy5s"
    "b2NhdGlvbi5ocmVmPWxpbms7CiAgfWNhdGNoKGUpe3dpbmRvdy5sb2NhdGlvbi5ocmVmPWxpbms7"
    "fQp9CmFzeW5jIGZ1bmN0aW9uIGJvb3QoKXsKICBpZihURyl7dHJ5e1RHLnJlYWR5KCk7VEcuZXhw"
    "YW5kKCk7fWNhdGNoKGUpe319CiAgY29uc3QgaW5URz0hISgoSU5JVCYmSU5JVC5sZW5ndGgpfHwo"
    "QVBQS0VZJiZBUFBLRVkubGVuZ3RoKSk7CiAgaWYoaW5URylzaG93U3dpdGNoKGZhbHNlKTsgICAg"
    "ICAgICAgICAgIC8qIGZhaWwgY2xvc2VkOiBubyBhZG1pbiBzdHJpcCB1bnRpbCBwcm92ZW4gKi8K"
    "ICBpZihsb2NhdGlvbi5wcm90b2NvbD09PSJmaWxlOiIpcmV0dXJuOyAgLyogb3BlbmVkIGFzIGEg"
    "cGxhaW4gZmlsZSDigJQgZGVtbyBnYWxsZXJ5ICovCiAgbGV0IGQ7CiAgdHJ5ewogICAgZD1hd2Fp"
    "dCBhcGkoIi9hcGkvc3RhdGUiKTsKICB9Y2F0Y2goZSl7CiAgICBpZihlJiZlLnN0YXR1cz09PTQw"
    "MSl7CiAgICAgIGxldCB3aHk9IiIsZml4PSIiOwogICAgICB0cnl7CiAgICAgICAgY29uc3Qgdz1h"
    "d2FpdCAoYXdhaXQgZmV0Y2goIi9hcGkvd2hvYW1pIix7aGVhZGVyczp7IlgtSW5pdC1EYXRhIjpJ"
    "TklULAogICAgICAgICAgIlgtVGVsZWdyYW0tSW5pdC1EYXRhIjpJTklULCJYLUFwcC1LZXkiOkFQ"
    "UEtFWX19KSkuanNvbigpOwogICAgICAgIGNvbnN0IFI9e21pc3Npbmc6WyJUZWxlZ3JhbSBkaWQg"
    "bm90IHBhc3MgeW91ciBhY2NvdW50IHRvIHRoaXMgcGFnZS4iLAogICAgICAgICAgICAgICAgICAg"
    "Ik9wZW4gdGhlIGJvdCBhbmQgdGFwIDxiPk9wZW4gU3RvcmU8L2I+LCBvciBzZW5kIDxiPi9hcHA8"
    "L2I+LiJdLAogICAgICAgICAgICAgICAgICJiYWQtZm9ybWF0IjpbIlRoZSBzaWduLWluIGRhdGEg"
    "YXJyaXZlZCBkYW1hZ2VkLiIsIkNsb3NlIHRoZSBhcHAgYW5kIG9wZW4gaXQgYWdhaW4gZnJvbSB0"
    "aGUgYm90LiJdLAogICAgICAgICAgICAgICAgICJuby1oYXNoIjpbIlRoZSBzaWduLWluIGRhdGEg"
    "YXJyaXZlZCBpbmNvbXBsZXRlLiIsIkNsb3NlIHRoZSBhcHAgYW5kIG9wZW4gaXQgYWdhaW4gZnJv"
    "bSB0aGUgYm90LiJdLAogICAgICAgICAgICAgICAgICJiYWQtaGFzaCI6WyJUaGUgc2lnbmF0dXJl"
    "IGRvZXMgbm90IG1hdGNoIHRoaXMgYm90J3MgdG9rZW4uIiwKICAgICAgICAgICAgICAgICAgICJU"
    "aGUgcGFnZSBpcyBzZXJ2ZWQgYnkgYSBkaWZmZXJlbnQgYm90LCBvciBCT1RfVE9LRU4gb24gdGhl"
    "IHNlcnZlciBoYXMgcXVvdGVzIGFyb3VuZCBpdC4gU2VuZCA8Yj4vZGlhZzwvYj4gaW4gdGhlIGJv"
    "dCB0byBjb21wYXJlLiJdLAogICAgICAgICAgICAgICAgIHN0YWxlOlsiVGhhdCBzaWduLWluIGlz"
    "IG9sZGVyIHRoYW4gdGhlIGFsbG93ZWQgd2luZG93LiIsIlJlb3BlbiB0aGUgYXBwIGZyb20gdGhl"
    "IGJvdC4iXSwKICAgICAgICAgICAgICAgICAiYmFkLXVzZXIiOlsiWW91ciBhY2NvdW50IGRldGFp"
    "bHMgY291bGQgbm90IGJlIHJlYWQuIiwiUmVvcGVuIHRoZSBhcHAgZnJvbSB0aGUgYm90LiJdLAog"
    "ICAgICAgICAgICAgICAgICJuby1pZCI6WyJUZWxlZ3JhbSBzZW50IG5vIHVzZXIgaWQuIiwiUmVv"
    "cGVuIHRoZSBhcHAgZnJvbSB0aGUgYm90LiJdfTsKICAgICAgICBjb25zdCByPVJbdy53aHldfHxb"
    "IlRoZSBzdG9yZSBjb3VsZCBub3QgY29uZmlybSB3aG8geW91IGFyZS4iLCJPcGVuIGl0IGFnYWlu"
    "IGZyb20gdGhlIGJvdC4iXTsKICAgICAgICB3aHk9clswXSsiIDxjb2RlPigiKyh3LndoeXx8InVu"
    "a25vd24iKSsiKTwvY29kZT4iOwogICAgICAgIGZpeD1yWzFdOwogICAgICB9Y2F0Y2goZXJyKXsK"
    "ICAgICAgICB3aHk9IlRoZSBzdG9yZSBjb3VsZCBub3QgY29uZmlybSB3aG8geW91IGFyZS4iOwog"
    "ICAgICAgIGZpeD0iT3BlbiBpdCBhZ2FpbiBmcm9tIHRoZSBib3Qgd2l0aCB0aGUgPGI+T3BlbiBT"
    "dG9yZTwvYj4gYnV0dG9uLiI7CiAgICAgIH0KICAgICAgbG9ja2VkU2NyZWVuKCJDYW5ub3Qgb3Bl"
    "biB0aGUgc3RvcmUiLHdoeSsiPGJyPjxicj4iK2ZpeCk7CiAgICB9CiAgICByZXR1cm47CiAgfQog"
    "IGFwcGx5U3RhdGUoZCk7TElWRT10cnVlOwogIHNob3dTd2l0Y2goISEoTUUmJk1FLmFkbWluKSk7"
    "ICAgICAgICAgICAvKiBvbmx5IGEgdmVyaWZpZWQgYWRtaW4gc2VlcyBpdCAqLwogIGFwcGx5Q01T"
    "KCk7YXBwbHlGZWF0dXJlcygpO3N5bmNCYWwoKTtyZW5kZXIoKTtyZW5kZXJBZG1pbigpOwp9CmFz"
    "eW5jIGZ1bmN0aW9uIHB1c2gocGF0aCxib2R5LG9rTXNnKXsKICBpZighTElWRSlyZXR1cm4gdHJ1"
    "ZTsKICB0cnl7Y29uc3Qgcj1hd2FpdCBhcGkocGF0aCxib2R5KTtpZihva01zZyl0b2FzdChva01z"
    "Zyk7cmV0dXJuIHI7fQogIGNhdGNoKGUpe3RvYXN0KCJDb3VsZCBub3Qgc2F2ZSDigJQgY2hlY2sg"
    "eW91ciBjb25uZWN0aW9uIik7cmV0dXJuIGZhbHNlO30KfQpib290KCk7Cjwvc2NyaXB0Pgo8L2Jv"
    "ZHk+PC9odG1sPgo="
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
