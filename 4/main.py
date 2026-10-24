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
DESIGN = "4-cyber"


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
    "store_tagline": "Terminal grade",
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
    "storeTag": "Terminal grade",
    "tick1": "<b>Instant delivery</b> on every country",
    "tick2": "Wallet top-ups <b>auto-verified</b>",
    "tick3": "Failed login? <b>Auto refund</b>",
    "tick4": "Daily check-in <b>bonus</b>",
    "heroKick": "&gt; inventory --live",
    "heroH1": "SECURE_ACCOUNTS<br>0MS DELIVERY",
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
    "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIiBkYXRhLWRlc2lnbj0iY3liZXIiPgo8aGVh"
    "ZD4KPG1ldGEgY2hhcnNldD0idXRmLTgiPgo8bWV0YSBuYW1lPSJ2aWV3cG9ydCIgY29udGVudD0i"
    "d2lkdGg9ZGV2aWNlLXdpZHRoLGluaXRpYWwtc2NhbGU9MSx2aWV3cG9ydC1maXQ9Y292ZXIsbWF4"
    "aW11bS1zY2FsZT0xIj4KPHNjcmlwdCBzcmM9Imh0dHBzOi8vdGVsZWdyYW0ub3JnL2pzL3RlbGVn"
    "cmFtLXdlYi1hcHAuanMiPjwvc2NyaXB0Pgo8dGl0bGU+Wm9ybyBURyBTdG9yZSDigJQgU3RvcmUg"
    "TWluaSBBcHAgKERlc2lnbiA0KTwvdGl0bGU+CjxzdHlsZT4KOnJvb3R7CiAgLS1iZzojMDQwNjBh"
    "OyAtLWJnMjojMDcwYzE0OyAtLWNhcmQ6cmdiYSgwLDI1NSwyMTQsLjAzNSk7IC0tY2FyZDI6cmdi"
    "YSgwLDI1NSwyMTQsLjA3KTsKICAtLWxpbmU6cmdiYSgwLDI1NSwyMTQsLjIyKTsgLS10eDojZDZm"
    "ZmY2OyAtLXR4MjojNWY4Zjg4OyAtLWFjYzojMDBmZmQ2OyAtLWFjYzI6I2ZmMmJkMTsKICAtLW9r"
    "OiMwMGZmYTM7IC0td2FybjojZmZkMTY2OyAtLWJhZDojZmYyYmQxOwogIC0tcjo2cHg7IC0tcnM6"
    "NnB4OyAtLWJ0bmg6NDRweDsgLS1mb250OnVpLW1vbm9zcGFjZSwnU0YgTW9ubycsTWVubG8sQ29u"
    "c29sYXMsbW9ub3NwYWNlOyAtLWZoZWFkOnVpLW1vbm9zcGFjZSwnU0YgTW9ubycsTWVubG8sQ29u"
    "c29sYXMsbW9ub3NwYWNlOwogIC0tc2hhZG93OjAgOHB4IDI2cHggcmdiYSgwLDAsMCwuNik7IC0t"
    "cGlsbGdyYWQ6bGluZWFyLWdyYWRpZW50KDEzNWRlZywjMDBmZmQ2LCMwMGIzZmYpOyAtLWJ0bmJn"
    "OiMwN2I5YTA7Cn0KKntib3gtc2l6aW5nOmJvcmRlci1ib3g7LXdlYmtpdC10YXAtaGlnaGxpZ2h0"
    "LWNvbG9yOnRyYW5zcGFyZW50fQpodG1sLGJvZHl7bWFyZ2luOjA7cGFkZGluZzowfQpib2R5e2Jh"
    "Y2tncm91bmQ6dmFyKC0tYmcpO2NvbG9yOnZhcigtLXR4KTtmb250LWZhbWlseTp2YXIoLS1mb250"
    "KTtmb250LXNpemU6MTVweDtsaW5lLWhlaWdodDoxLjQ1O21pbi1oZWlnaHQ6MTAwdmg7b3ZlcmZs"
    "b3cteDpoaWRkZW47cGFkZGluZy10b3A6MzJweDt9CmJvZHk6YmVmb3Jle2NvbnRlbnQ6IiI7cG9z"
    "aXRpb246Zml4ZWQ7aW5zZXQ6MDtwb2ludGVyLWV2ZW50czpub25lO3otaW5kZXg6MDtiYWNrZ3Jv"
    "dW5kLWltYWdlOmxpbmVhci1ncmFkaWVudChyZ2JhKDAsMjU1LDIxNCwuMDUpIDFweCx0cmFuc3Bh"
    "cmVudCAxcHgpLGxpbmVhci1ncmFkaWVudCg5MGRlZyxyZ2JhKDAsMjU1LDIxNCwuMDUpIDFweCx0"
    "cmFuc3BhcmVudCAxcHgpO2JhY2tncm91bmQtc2l6ZTozNHB4IDM0cHg7bWFzay1pbWFnZTpyYWRp"
    "YWwtZ3JhZGllbnQoNzAlIDYwJSBhdCA1MCUgMCUsIzAwMCx0cmFuc3BhcmVudCl9Cjo6LXdlYmtp"
    "dC1zY3JvbGxiYXJ7d2lkdGg6MDtoZWlnaHQ6MH0KI3NoZWxse3Bvc2l0aW9uOnJlbGF0aXZlO3ot"
    "aW5kZXg6MTttYXgtd2lkdGg6NTIwcHg7bWFyZ2luOjAgYXV0bzttaW4taGVpZ2h0OjEwMHZoO3Bh"
    "ZGRpbmc6MCAxNnB4IDExMnB4fQouaGlkZGVue2Rpc3BsYXk6bm9uZSFpbXBvcnRhbnR9CgojdG9w"
    "YmFye3Bvc2l0aW9uOnN0aWNreTt0b3A6MzJweDt6LWluZGV4OjQwO2Rpc3BsYXk6ZmxleDthbGln"
    "bi1pdGVtczpjZW50ZXI7Z2FwOjEwcHg7cGFkZGluZzoxNHB4IDAgMTJweDtiYWNrZ3JvdW5kOmxp"
    "bmVhci1ncmFkaWVudCh2YXIoLS1iZykgNzIlLHRyYW5zcGFyZW50KX0KLmxvZ297d2lkdGg6Mzhw"
    "eDtoZWlnaHQ6MzhweDtib3JkZXItcmFkaXVzOjZweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6"
    "Y2VudGVyO2ZsZXg6MCAwIGF1dG87YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzAy"
    "MTIwZjtmb250LXdlaWdodDo4MDA7Zm9udC1zaXplOi45cmVtO2xldHRlci1zcGFjaW5nOi0uMDNl"
    "bTt9Ci5icmFuZHttaW4td2lkdGg6MDtmbGV4OjF9Ci5icmFuZCBie2Rpc3BsYXk6YmxvY2s7Zm9u"
    "dC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZTouOTVyZW07Zm9udC13ZWlnaHQ6NzAwO2xl"
    "dHRlci1zcGFjaW5nOi4wMmVtO3doaXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4"
    "dC1vdmVyZmxvdzplbGxpcHNpc30KLmJyYW5kIHNwYW57ZGlzcGxheTpibG9jaztmb250LXNpemU6"
    "LjY2cmVtO2NvbG9yOnZhcigtLXR4Mik7bGV0dGVyLXNwYWNpbmc6LjA2ZW07dGV4dC10cmFuc2Zv"
    "cm06dXBwZXJjYXNlfQouYmFsY2hpcHtkaXNwbGF5OmlubGluZS1mbGV4O2FsaWduLWl0ZW1zOmNl"
    "bnRlcjtnYXA6N3B4O2hlaWdodDozOHB4O3BhZGRpbmc6MCAxM3B4O2JvcmRlci1yYWRpdXM6NnB4"
    "O2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtjb2xv"
    "cjp2YXIoLS10eCk7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9udC13ZWlnaHQ6NzAwO2ZvbnQt"
    "c2l6ZTouODVyZW07d2hpdGUtc3BhY2U6bm93cmFwO2N1cnNvcjpwb2ludGVyO30KLmJhbGNoaXAg"
    "LnBsdXN7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjt3aWR0aDoyMHB4O2hlaWdodDoy"
    "MHB4O2JvcmRlci1yYWRpdXM6N3B4O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiMw"
    "MjEyMGY7Zm9udC13ZWlnaHQ6ODAwO2ZvbnQtc2l6ZTouOHJlbTtsaW5lLWhlaWdodDoxfQoKLmNh"
    "cmR7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2Jv"
    "cmRlci1yYWRpdXM6dmFyKC0tcik7cGFkZGluZzoxNnB4O21hcmdpbi1ib3R0b206MTJweDtib3gt"
    "c2hhZG93OnZhcigtLXNoYWRvdyk7fQouY2FyZC50aWdodHtwYWRkaW5nOjEzcHh9Ci5jYXJkIGgz"
    "e21hcmdpbjowIDAgM3B4O2ZvbnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6LjkycmVt"
    "O2ZvbnQtd2VpZ2h0OjcwMDtsZXR0ZXItc3BhY2luZzouMDJlbX0KLmNhcmQgLnN1YnttYXJnaW46"
    "MDtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNzhyZW19Ci5zZWN7ZGlzcGxheTpmbGV4O2Fs"
    "aWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjtnYXA6MTBweDtt"
    "YXJnaW46MThweCAycHggMTBweH0KLnNlYyBoMnttYXJnaW46MDtmb250LWZhbWlseTp2YXIoLS1m"
    "aGVhZCk7Zm9udC1zaXplOi44MnJlbTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LjE0"
    "ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2NvbG9yOnZhcigtLXR4Mil9Ci5zZWMgYXtmb250"
    "LXNpemU6Ljc1cmVtO2NvbG9yOnZhcigtLWFjYyk7dGV4dC1kZWNvcmF0aW9uOm5vbmU7Zm9udC13"
    "ZWlnaHQ6NjAwO3doaXRlLXNwYWNlOm5vd3JhcDtjdXJzb3I6cG9pbnRlcn0KCi8qIG9uZSBidXR0"
    "b24gc3lzdGVtIOKAlCB0ZXh0IGNhbiBuZXZlciBvdmVyZmxvdyAqLwouYnRue2Rpc3BsYXk6aW5s"
    "aW5lLWZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Z2FwOjdw"
    "eDtoZWlnaHQ6dmFyKC0tYnRuaCk7bWluLXdpZHRoOjA7bWF4LXdpZHRoOjEwMCU7cGFkZGluZzow"
    "IDE2cHg7Ym9yZGVyOjA7Y3Vyc29yOnBvaW50ZXI7Ym9yZGVyLXJhZGl1czo2cHg7Zm9udC1mYW1p"
    "bHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi44M3JlbTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNw"
    "YWNpbmc6LjAxZW07d2hpdGUtc3BhY2U6bm93cmFwO292ZXJmbG93OmhpZGRlbjt0ZXh0LW92ZXJm"
    "bG93OmVsbGlwc2lzO2xpbmUtaGVpZ2h0OjE7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29s"
    "b3I6IzAyMTIwZjt0cmFuc2l0aW9uOnRyYW5zZm9ybSAuMTVzLGZpbHRlciAuMTVzO30KLmJ0bjph"
    "Y3RpdmV7dHJhbnNmb3JtOnNjYWxlKC45Nyl9Ci5idG46aG92ZXJ7ZmlsdGVyOmJyaWdodG5lc3Mo"
    "MS4wNil9Ci5idG4gc3Zne3dpZHRoOjE2cHg7aGVpZ2h0OjE2cHg7ZmxleDowIDAgYXV0b30KLmJ0"
    "bi5ibG9ja3tkaXNwbGF5OmZsZXg7d2lkdGg6MTAwJX0KLmJ0bi5zbXtoZWlnaHQ6MzZweDtwYWRk"
    "aW5nOjAgMTJweDtmb250LXNpemU6Ljc1cmVtO2JvcmRlci1yYWRpdXM6NXB4fQouYnRuLnhze2hl"
    "aWdodDozMHB4O3BhZGRpbmc6MCAxMHB4O2ZvbnQtc2l6ZTouN3JlbTtib3JkZXItcmFkaXVzOjVw"
    "eH0KLmJ0bi5naG9zdHtiYWNrZ3JvdW5kOnRyYW5zcGFyZW50O2NvbG9yOnZhcigtLXR4KTtib3Jk"
    "ZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouYnRuLnNvZnR7YmFja2dyb3VuZDp2YXIoLS1jYXJk"
    "Mik7Y29sb3I6dmFyKC0tdHgpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSl9Ci5idG4uYmFk"
    "e2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7Y29sb3I6dmFyKC0tYmFkKTtib3JkZXI6MXB4IHNvbGlk"
    "IHZhcigtLWJhZCl9Ci5idG5bZGlzYWJsZWRde29wYWNpdHk6LjQ1O3BvaW50ZXItZXZlbnRzOm5v"
    "bmV9Ci5idG5yb3d7ZGlzcGxheTpmbGV4O2dhcDo4cHh9Ci5idG5yb3c+LmJ0bntmbGV4OjEgMSAw"
    "O21pbi13aWR0aDowfQoKLnBpbGx7ZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpjZW50"
    "ZXI7Z2FwOjVweDtoZWlnaHQ6MjRweDtwYWRkaW5nOjAgOXB4O2JvcmRlci1yYWRpdXM6OTlweDtm"
    "b250LXNpemU6LjY2cmVtO2ZvbnQtd2VpZ2h0OjcwMDtsZXR0ZXItc3BhY2luZzouMDRlbTtiYWNr"
    "Z3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIpO2JvcmRlcjoxcHggc29saWQgdmFy"
    "KC0tbGluZSk7d2hpdGUtc3BhY2U6bm93cmFwfQoucGlsbC5vbntjb2xvcjp2YXIoLS1vayk7Ym9y"
    "ZGVyLWNvbG9yOnJnYmEoMCwyNTUsMTYzLC41KX0KLnBpbGwuaG90e2NvbG9yOnZhcigtLXdhcm4p"
    "O2JvcmRlci1jb2xvcjpyZ2JhKDI1NSwyMDksMTAyLC41KX0KLnBpbGwub2Zme2NvbG9yOnZhcigt"
    "LWJhZCk7Ym9yZGVyLWNvbG9yOnJnYmEoMjU1LDQzLDIwOSwuNSl9Ci5kb3R7d2lkdGg6N3B4O2hl"
    "aWdodDo3cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3VuZDp2YXIoLS1vayk7Ym94LXNoYWRv"
    "dzowIDAgOHB4IHZhcigtLW9rKX0KLmt2e2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7"
    "anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47Z2FwOjEycHg7cGFkZGluZzoxMHB4IDA7Ym9y"
    "ZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0tbGluZSk7Zm9udC1zaXplOi44MnJlbTtjb2xvcjp2"
    "YXIoLS10eDIpfQoua3Y6bGFzdC1jaGlsZHtib3JkZXItYm90dG9tOjB9Ci5rdiBie2NvbG9yOnZh"
    "cigtLXR4KTtmb250LXdlaWdodDo3MDA7dGV4dC1hbGlnbjpyaWdodDtvdmVyZmxvdy13cmFwOmFu"
    "eXdoZXJlfQouZmxhZ3t3aWR0aDo0MnB4O2hlaWdodDo0MnB4O2JvcmRlci1yYWRpdXM6NnB4O2Rp"
    "c3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9udC1zaXplOjEuMzVyZW07ZmxleDowIDAg"
    "YXV0bztiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUp"
    "fQouZmxhZyBzdmd7d2lkdGg6MThweDtoZWlnaHQ6MThweH0KCi5oZXJve3Bvc2l0aW9uOnJlbGF0"
    "aXZlO292ZXJmbG93OmhpZGRlbjtib3JkZXItcmFkaXVzOjhweDtwYWRkaW5nOjIycHggMThweDtt"
    "YXJnaW46NHB4IDAgMTRweDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6"
    "bGluZWFyLWdyYWRpZW50KDEzNWRlZyxyZ2JhKDAsMjU1LDIxNCwuMTIpLHJnYmEoMjU1LDQzLDIw"
    "OSwuMDcpKTtib3JkZXItY29sb3I6cmdiYSgwLDI1NSwyMTQsLjMpfQouaGVybyAua2lja3tmb250"
    "LXNpemU6LjZyZW07bGV0dGVyLXNwYWNpbmc6LjJlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7"
    "Y29sb3I6dmFyKC0tYWNjKTtmb250LXdlaWdodDo4MDB9Ci5oZXJvIGgxe21hcmdpbjo4cHggMCA2"
    "cHg7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZToxLjc1cmVtO2xpbmUtaGVpZ2h0"
    "OjEuMDU7bGV0dGVyLXNwYWNpbmc6LjAyZW07Zm9udC13ZWlnaHQ6NzAwfQouaGVybyBwe21hcmdp"
    "bjowIDAgMTRweDtmb250LXNpemU6LjhyZW07Y29sb3I6dmFyKC0tdHgyKTttYXgtd2lkdGg6Mjgw"
    "cHh9Ci5oZXJvIC5idG5yb3d7bWF4LXdpZHRoOjMzMHB4fQoKLmdyaWR7ZGlzcGxheTpncmlkO2dy"
    "aWQtdGVtcGxhdGUtY29sdW1uczoxZnIgMWZyO2dhcDoxMHB4fQoucGNhcmR7YmFja2dyb3VuZDp2"
    "YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6dmFy"
    "KC0tcik7cGFkZGluZzoxNHB4O2JveC1zaGFkb3c6dmFyKC0tc2hhZG93KTtkaXNwbGF5OmZsZXg7"
    "ZmxleC1kaXJlY3Rpb246Y29sdW1uO2dhcDo5cHg7Y3Vyc29yOnBvaW50ZXI7fQoucGNhcmQgLnRv"
    "cHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpzcGFjZS1i"
    "ZXR3ZWVuO2dhcDo4cHh9Ci5wY2FyZCAubm17Zm9udC13ZWlnaHQ6NzAwO2ZvbnQtc2l6ZTouODVy"
    "ZW07d2hpdGUtc3BhY2U6bm93cmFwO292ZXJmbG93OmhpZGRlbjt0ZXh0LW92ZXJmbG93OmVsbGlw"
    "c2lzfQoucGNhcmQgLm1ldGF7Zm9udC1zaXplOi42OHJlbTtjb2xvcjp2YXIoLS10eDIpfQoucGNh"
    "cmQgLnByaWNle2ZvbnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6MS4yNXJlbTtmb250"
    "LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LS4wMmVtfQoucGNhcmQgLnByaWNlIHN7Zm9udC1z"
    "aXplOi43MnJlbTtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtd2VpZ2h0OjQwMDttYXJnaW4tcmlnaHQ6"
    "NnB4fQoucm93c3tkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1uO2dhcDo5cHh9Ci5y"
    "b3d7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTJweDtiYWNrZ3JvdW5kOnZh"
    "cigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJhZGl1czp2YXIo"
    "LS1yKTtwYWRkaW5nOjEycHggMTNweDtib3gtc2hhZG93OnZhcigtLXNoYWRvdyk7Y3Vyc29yOnBv"
    "aW50ZXI7dHJhbnNpdGlvbjp0cmFuc2Zvcm0gLjE1czt9Ci5yb3c6YWN0aXZle3RyYW5zZm9ybTpz"
    "Y2FsZSguOTkpfQoucm93IC5taWR7ZmxleDoxO21pbi13aWR0aDowfQoucm93IC5taWQgYntkaXNw"
    "bGF5OmJsb2NrO2ZvbnQtc2l6ZTouODhyZW07d2hpdGUtc3BhY2U6bm93cmFwO292ZXJmbG93Omhp"
    "ZGRlbjt0ZXh0LW92ZXJmbG93OmVsbGlwc2lzfQoucm93IC5taWQgc21hbGx7ZGlzcGxheTpibG9j"
    "aztmb250LXNpemU6LjdyZW07Y29sb3I6dmFyKC0tdHgyKTttYXJnaW4tdG9wOjJweH0KLnJvdyAu"
    "cnR7dGV4dC1hbGlnbjpyaWdodDtmbGV4OjAgMCBhdXRvfQoucm93IC5ydCAucHtmb250LXdlaWdo"
    "dDo4MDA7Zm9udC1zaXplOi45NXJlbTt3aGl0ZS1zcGFjZTpub3dyYXB9Ci5yb3cgLnJ0IC5wIHN7"
    "Zm9udC13ZWlnaHQ6NDAwO2ZvbnQtc2l6ZTouNzJyZW07Y29sb3I6dmFyKC0tdHgyKTttYXJnaW4t"
    "cmlnaHQ6NXB4fQouY2Fyb3V7ZGlzcGxheTpmbGV4O2dhcDoxMHB4O292ZXJmbG93LXg6YXV0bztw"
    "YWRkaW5nOjJweCAwIDZweDttYXJnaW46MCAtMTZweCA2cHg7cGFkZGluZy1sZWZ0OjE2cHg7cGFk"
    "ZGluZy1yaWdodDoxNnB4O3Njcm9sbC1zbmFwLXR5cGU6eCBtYW5kYXRvcnl9Ci5jYXJvdT4qe2Zs"
    "ZXg6MCAwIDE3MnB4O3Njcm9sbC1zbmFwLWFsaWduOnN0YXJ0fQoKbmF2I3RhYnN7cG9zaXRpb246"
    "Zml4ZWQ7ei1pbmRleDo2MDtsZWZ0OjUwJTtib3R0b206Y2FsYygxMnB4ICsgZW52KHNhZmUtYXJl"
    "YS1pbnNldC1ib3R0b20pKTt0cmFuc2Zvcm06dHJhbnNsYXRlWCgtNTAlKTttYXgtd2lkdGg6Y2Fs"
    "YygxMDB2dyAtIDIwcHgpfQpuYXYjdGFicyAuZG9ja3tkaXNwbGF5OmZsZXg7Z2FwOjJweDtwYWRk"
    "aW5nOjdweCA4cHg7Ym9yZGVyLXJhZGl1czo4cHg7YmFja2dyb3VuZDpyZ2JhKDQsMTAsMTQsLjkp"
    "O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym94LXNoYWRvdzowIDAgMjRweCByZ2JhKDAs"
    "MjU1LDIxNCwuMTgpfQpuYXYjdGFicyBidXR0b257cG9zaXRpb246cmVsYXRpdmU7ZGlzcGxheTpm"
    "bGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250"
    "ZW50OmNlbnRlcjtnYXA6NHB4O21pbi13aWR0aDo1OHB4O2hlaWdodDo0OHB4O3BhZGRpbmc6MCA2"
    "cHg7Ym9yZGVyOjA7YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjdXJzb3I6cG9pbnRlcjtjb2xvcjp2"
    "YXIoLS10eDIpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouNnJlbTtmb250LXdl"
    "aWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LjAzZW07Ym9yZGVyLXJhZGl1czo2cHg7dHJhbnNpdGlv"
    "bjpjb2xvciAuMnMsYmFja2dyb3VuZCAuMnM7d2hpdGUtc3BhY2U6bm93cmFwfQpuYXYjdGFicyBi"
    "dXR0b24gc3Zne3dpZHRoOjE5cHg7aGVpZ2h0OjE5cHh9Cm5hdiN0YWJzIGJ1dHRvbi5vbntjb2xv"
    "cjojMDIxMjBmO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpfQoKLnN1Ym5hdntkaXNwbGF5OmZs"
    "ZXg7Z2FwOjdweDtvdmVyZmxvdy14OmF1dG87cGFkZGluZzoycHggMCAxMnB4O21hcmdpbjowIC0y"
    "cHh9Ci5zdWJuYXYgYnV0dG9ue2ZsZXg6MCAwIGF1dG87aGVpZ2h0OjM0cHg7cGFkZGluZzowIDEz"
    "cHg7Ym9yZGVyLXJhZGl1czo1cHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3Jv"
    "dW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQp"
    "O2ZvbnQtc2l6ZTouNzRyZW07Zm9udC13ZWlnaHQ6NzAwO2N1cnNvcjpwb2ludGVyO3doaXRlLXNw"
    "YWNlOm5vd3JhcH0KLnN1Ym5hdiBidXR0b24ub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7"
    "Y29sb3I6IzAyMTIwZjtib3JkZXItY29sb3I6dHJhbnNwYXJlbnR9Ci5rcGlze2Rpc3BsYXk6Z3Jp"
    "ZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyIDFmcjtnYXA6MTBweDttYXJnaW4tYm90dG9tOjEy"
    "cHh9Ci5rcGl7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxp"
    "bmUpO2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO3BhZGRpbmc6MTNweDtib3gtc2hhZG93OnZhcigt"
    "LXNoYWRvdyl9Ci5rcGkgc21hbGx7ZGlzcGxheTpibG9jaztmb250LXNpemU6LjY0cmVtO2NvbG9y"
    "OnZhcigtLXR4Mik7bGV0dGVyLXNwYWNpbmc6LjFlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2V9"
    "Ci5rcGkgYntkaXNwbGF5OmJsb2NrO21hcmdpbi10b3A6NXB4O2ZvbnQtZmFtaWx5OnZhcigtLWZo"
    "ZWFkKTtmb250LXNpemU6MS4zNXJlbTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LS4w"
    "MmVtfQouaW5wLHNlbGVjdC5pbnB7d2lkdGg6MTAwJTtoZWlnaHQ6dmFyKC0tYnRuaCk7cGFkZGlu"
    "ZzowIDEzcHg7Ym9yZGVyLXJhZGl1czo2cHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTti"
    "YWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eCk7Zm9udC1mYW1pbHk6dmFyKC0t"
    "Zm9udCk7Zm9udC1zaXplOi44M3JlbTtvdXRsaW5lOm5vbmU7LXdlYmtpdC1hcHBlYXJhbmNlOm5v"
    "bmU7YXBwZWFyYW5jZTpub25lfQpzZWxlY3QuaW5we2JhY2tncm91bmQtaW1hZ2U6bm9uZX0KLmlu"
    "cDo6cGxhY2Vob2xkZXJ7Y29sb3I6dmFyKC0tdHgyKTtvcGFjaXR5Oi43NX0KLmlucDpmb2N1c3ti"
    "b3JkZXItY29sb3I6dmFyKC0tYWNjKX0KdGV4dGFyZWEuaW5we2hlaWdodDphdXRvO21pbi1oZWln"
    "aHQ6NDBweDtwYWRkaW5nOjEwcHggMTNweDtsaW5lLWhlaWdodDoxLjU7cmVzaXplOnZlcnRpY2Fs"
    "fQouY21zcm93LmhpZGV7ZGlzcGxheTpub25lfQouaWNvbmNoaXB7cG9zaXRpb246cmVsYXRpdmU7"
    "aGVpZ2h0OjM0cHg7bWluLXdpZHRoOjM0cHg7cGFkZGluZzowIDlweDtib3JkZXItcmFkaXVzOjk5"
    "cHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtj"
    "b2xvcjp2YXIoLS10eCk7ZGlzcGxheTppbmxpbmUtZ3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Y3Vy"
    "c29yOnBvaW50ZXJ9Ci5pY29uY2hpcCBzdmd7d2lkdGg6MTdweDtoZWlnaHQ6MTdweH0KLmljb25j"
    "aGlwIGl7cG9zaXRpb246YWJzb2x1dGU7dG9wOi01cHg7cmlnaHQ6LTVweDttaW4td2lkdGg6MTdw"
    "eDtoZWlnaHQ6MTdweDtwYWRkaW5nOjAgNHB4O2JvcmRlci1yYWRpdXM6OTlweDtiYWNrZ3JvdW5k"
    "OnZhcigtLWFjYyk7Y29sb3I6IzAyMTIwZjtmb250LXNpemU6LjZyZW07Zm9udC1zdHlsZTpub3Jt"
    "YWw7Zm9udC13ZWlnaHQ6ODAwO2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXJ9Ci5zdGFy"
    "c3tkaXNwbGF5OmlubGluZS1mbGV4O2dhcDoxcHg7dmVydGljYWwtYWxpZ246bWlkZGxlfQouc3Rh"
    "cnMgc3Zne3dpZHRoOjExcHg7aGVpZ2h0OjExcHg7Y29sb3I6I2Y1YTYyM30KLnN0YXJzIGJ7Zm9u"
    "dC1zaXplOi42NnJlbTtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtd2VpZ2h0OjcwMDttYXJnaW4tbGVm"
    "dDozcHh9Ci5oZWFydHtwb3NpdGlvbjphYnNvbHV0ZTt0b3A6OHB4O3JpZ2h0OjhweDt3aWR0aDoy"
    "NnB4O2hlaWdodDoyNnB4O2JvcmRlci1yYWRpdXM6OTlweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRl"
    "bXM6Y2VudGVyO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIoLS1j"
    "YXJkMik7Y3Vyc29yOnBvaW50ZXI7ei1pbmRleDoyfQouaGVhcnQgc3Zne3dpZHRoOjEzcHg7aGVp"
    "Z2h0OjEzcHg7Y29sb3I6dmFyKC0tdHgyKX0KLmhlYXJ0Lm9ue2JhY2tncm91bmQ6dmFyKC0tYWNj"
    "KTtib3JkZXItY29sb3I6dHJhbnNwYXJlbnR9Ci5oZWFydC5vbiBzdmd7Y29sb3I6IzAyMTIwZn0K"
    "LnF0eXtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDowO2JvcmRlcjoxcHggc29s"
    "aWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJhZGl1czo2cHg7b3ZlcmZsb3c6aGlkZGVufQoucXR5IGJ1"
    "dHRvbnt3aWR0aDozOHB4O2hlaWdodDozOHB4O2JvcmRlcjowO2JhY2tncm91bmQ6dmFyKC0tY2Fy"
    "ZDIpO2NvbG9yOnZhcigtLXR4KTtmb250LXNpemU6MXJlbTtmb250LXdlaWdodDo4MDA7Y3Vyc29y"
    "OnBvaW50ZXJ9Ci5xdHkgYnttaW4td2lkdGg6MzhweDt0ZXh0LWFsaWduOmNlbnRlcjtmb250LXNp"
    "emU6Ljg1cmVtfQoudGx7bWFyZ2luOjEwcHggMCAwO3BhZGRpbmctbGVmdDoxNnB4O2JvcmRlci1s"
    "ZWZ0OjJweCBzb2xpZCB2YXIoLS1saW5lKX0KLnRsIGRpdntwb3NpdGlvbjpyZWxhdGl2ZTtwYWRk"
    "aW5nOjAgMCAxMXB4IDEwcHg7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpfQoudGwg"
    "ZGl2Omxhc3QtY2hpbGR7cGFkZGluZy1ib3R0b206MH0KLnRsIGRpdjo6YmVmb3Jle2NvbnRlbnQ6"
    "IiI7cG9zaXRpb246YWJzb2x1dGU7bGVmdDotMjFweDt0b3A6NHB4O3dpZHRoOjhweDtoZWlnaHQ6"
    "OHB4O2JvcmRlci1yYWRpdXM6OTlweDtiYWNrZ3JvdW5kOnZhcigtLWxpbmUpfQoudGwgZGl2Lm9r"
    "OjpiZWZvcmV7YmFja2dyb3VuZDp2YXIoLS1vayl9Ci50bCBkaXYub2sgYntjb2xvcjp2YXIoLS10"
    "eCl9Ci50bCBzbWFsbHtkaXNwbGF5OmJsb2NrO2ZvbnQtc2l6ZTouNjZyZW07b3BhY2l0eTouOH0K"
    "LmZjYXJke2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpmbGV4LXN0YXJ0O2dhcDoxMXB4O3BhZGRp"
    "bmc6MTFweCAwO2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouZmNhcmQ6bGFz"
    "dC1jaGlsZHtib3JkZXItYm90dG9tOjB9Ci5mY2FyZCAudHtmbGV4OjE7bWluLXdpZHRoOjB9Ci5m"
    "Y2FyZCAudCBie2ZvbnQtc2l6ZTouODFyZW07ZGlzcGxheTpibG9ja30KLmZjYXJkIC50IHNtYWxs"
    "e2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42OXJlbTtjb2xvcjp2YXIoLS10eDIpO2xpbmUtaGVp"
    "Z2h0OjEuNDU7bWFyZ2luLXRvcDoycHh9Ci5mcm93LmhpZGUsLmZncnAuaGlkZXtkaXNwbGF5Om5v"
    "bmV9Ci5iYXJze2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpmbGV4LWVuZDtnYXA6NXB4O2hlaWdo"
    "dDo4NnB4O21hcmdpbi10b3A6MTJweH0KLmJhcnMgaXtmbGV4OjE7Ym9yZGVyLXJhZGl1czo2cHgg"
    "NnB4IDNweCAzcHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7ZGlzcGxheTpibG9ja30KLmZ1"
    "bm5lbCBkaXZ7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6OXB4O21hcmdpbi10"
    "b3A6OXB4O2ZvbnQtc2l6ZTouNzRyZW19Ci5mdW5uZWwgaXtoZWlnaHQ6MjZweDtib3JkZXItcmFk"
    "aXVzOjdweDtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtkaXNwbGF5OmJsb2NrfQouZmVlZGxp"
    "bmV7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6OXB4O2ZvbnQtc2l6ZTouNzNy"
    "ZW07Y29sb3I6dmFyKC0tdHgyKTtwYWRkaW5nOjdweCAwO2JvcmRlci1ib3R0b206MXB4IHNvbGlk"
    "IHZhcigtLWxpbmUpfQouZmVlZGxpbmU6bGFzdC1jaGlsZHtib3JkZXItYm90dG9tOjB9Ci5mZWVk"
    "bGluZSBie2NvbG9yOnZhcigtLXR4KX0KLmxie2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50"
    "ZXI7Z2FwOjEwcHg7cGFkZGluZzo5cHggMDtib3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIoLS1s"
    "aW5lKX0KLmxiOmxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRvbTowfQoubGIgLnJre3dpZHRoOjI0cHg7"
    "aGVpZ2h0OjI0cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpj"
    "ZW50ZXI7Zm9udC1zaXplOi43cmVtO2ZvbnQtd2VpZ2h0OjgwMDtiYWNrZ3JvdW5kOnZhcigtLWNh"
    "cmQyKTtjb2xvcjp2YXIoLS10eDIpfQoubGIgLnJrLmd7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3Jh"
    "ZCk7Y29sb3I6IzAyMTIwZn0KLmNtc2dycC5oaWRle2Rpc3BsYXk6bm9uZX0KLmNtc3JvdyBsYWJl"
    "bCBie2NvbG9yOnZhcigtLWFjYyk7Zm9udC13ZWlnaHQ6NzAwfQoudGltZXJiYXJ7aGVpZ2h0OjVw"
    "eDtib3JkZXItcmFkaXVzOjk5cHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7b3ZlcmZsb3c6aGlk"
    "ZGVuO21hcmdpbjoxMHB4IDAgMnB4fQoudGltZXJiYXIgaXtkaXNwbGF5OmJsb2NrO2hlaWdodDox"
    "MDAlO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO3RyYW5zaXRpb246d2lkdGggMXMgbGluZWFy"
    "fQoudGltZXJiYXIubG93IGl7YmFja2dyb3VuZDp2YXIoLS1iYWQsI2U1NDg0ZCl9Ci5maWVsZHtt"
    "YXJnaW4tYm90dG9tOjlweH0KLmZpZWxkIGxhYmVse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42"
    "OHJlbTtjb2xvcjp2YXIoLS10eDIpO2xldHRlci1zcGFjaW5nOi4wOWVtO3RleHQtdHJhbnNmb3Jt"
    "OnVwcGVyY2FzZTttYXJnaW46MCAwIDVweCAycHg7Zm9udC13ZWlnaHQ6NzAwfQouc3dhcHtkaXNw"
    "bGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpzcGFjZS1iZXR3ZWVu"
    "O2dhcDoxMnB4O3BhZGRpbmc6MTFweCAwO2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxp"
    "bmUpfQouc3dhcDpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206MH0KLnN3YXAgLnR7Zm9udC1zaXpl"
    "Oi44MnJlbTtmb250LXdlaWdodDo2MDA7bWluLXdpZHRoOjB9Ci5zd2FwIC50IHNtYWxse2Rpc3Bs"
    "YXk6YmxvY2s7Zm9udC13ZWlnaHQ6NDAwO2ZvbnQtc2l6ZTouN3JlbTtjb2xvcjp2YXIoLS10eDIp"
    "fQoudGd7d2lkdGg6NDRweDtoZWlnaHQ6MjZweDtib3JkZXItcmFkaXVzOjk5cHg7YmFja2dyb3Vu"
    "ZDp2YXIoLS1jYXJkMik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtwb3NpdGlvbjpyZWxh"
    "dGl2ZTtjdXJzb3I6cG9pbnRlcjtmbGV4OjAgMCBhdXRvfQoudGc6YWZ0ZXJ7Y29udGVudDoiIjtw"
    "b3NpdGlvbjphYnNvbHV0ZTt0b3A6M3B4O2xlZnQ6M3B4O3dpZHRoOjE4cHg7aGVpZ2h0OjE4cHg7"
    "Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3VuZDp2YXIoLS10eDIpO3RyYW5zaXRpb246LjIyc30K"
    "LnRnLm9ue2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVu"
    "dH0KLnRnLm9uOmFmdGVye2xlZnQ6MjFweDtiYWNrZ3JvdW5kOiMwMjEyMGZ9Ci50Zy5zbXt3aWR0"
    "aDozNnB4O2hlaWdodDoyMXB4fQoudGcuc206YWZ0ZXJ7d2lkdGg6MTRweDtoZWlnaHQ6MTRweDt0"
    "b3A6Mi41cHh9Ci50Zy5zbS5vbjphZnRlcntsZWZ0OjE4cHh9Ci5zdGFye3dpZHRoOjMwcHg7aGVp"
    "Z2h0OjMwcHg7Ym9yZGVyLXJhZGl1czo5cHg7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRl"
    "cjtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2Nv"
    "bG9yOnZhcigtLXR4Mik7Y3Vyc29yOnBvaW50ZXI7ZmxleDowIDAgYXV0b30KLnN0YXIub257YmFj"
    "a2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzAyMTIwZjtib3JkZXItY29sb3I6dHJhbnNw"
    "YXJlbnR9Ci5zdGFyIHN2Z3t3aWR0aDoxNHB4O2hlaWdodDoxNHB4fQoKI3NoZWV0e3Bvc2l0aW9u"
    "OmZpeGVkO2luc2V0OjA7ei1pbmRleDo5MDtkaXNwbGF5Om5vbmV9CiNzaGVldC5vcGVue2Rpc3Bs"
    "YXk6YmxvY2t9CiNzaGVldCAuYmd7cG9zaXRpb246YWJzb2x1dGU7aW5zZXQ6MDtiYWNrZ3JvdW5k"
    "OnJnYmEoMCwxMCw4LC43KTtiYWNrZHJvcC1maWx0ZXI6Ymx1cigzcHgpfQojc2hlZXQgLnBhbmVs"
    "e3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6NTAlO2JvdHRvbTowO3RyYW5zZm9ybTp0cmFuc2xhdGVY"
    "KC01MCUpO3dpZHRoOjEwMCU7bWF4LXdpZHRoOjUyMHB4O2JhY2tncm91bmQ6dmFyKC0tYmcyKTti"
    "b3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1ib3R0b206MDtib3JkZXItcmFkaXVz"
    "OjEwcHggMTBweCAwIDA7cGFkZGluZzoxOHB4IDE2cHggY2FsYygyMHB4ICsgZW52KHNhZmUtYXJl"
    "YS1pbnNldC1ib3R0b20pKTthbmltYXRpb246dXAgLjI4cyBjdWJpYy1iZXppZXIoLjIsLjksLjMs"
    "MSk7bWF4LWhlaWdodDo5MnZoO292ZXJmbG93OmF1dG99CkBrZXlmcmFtZXMgdXB7ZnJvbXt0cmFu"
    "c2Zvcm06dHJhbnNsYXRlKC01MCUsMTAwJSl9dG97dHJhbnNmb3JtOnRyYW5zbGF0ZSgtNTAlLDAp"
    "fX0KI3NoZWV0IC5ncmFie3dpZHRoOjM4cHg7aGVpZ2h0OjRweDtib3JkZXItcmFkaXVzOjlweDti"
    "YWNrZ3JvdW5kOnZhcigtLWxpbmUpO21hcmdpbjowIGF1dG8gMTRweH0KLm90cGJveHtmb250LWZh"
    "bWlseTp1aS1tb25vc3BhY2UsTWVubG8sQ29uc29sYXMsbW9ub3NwYWNlO2ZvbnQtc2l6ZToxLjZy"
    "ZW07bGV0dGVyLXNwYWNpbmc6LjM1ZW07dGV4dC1hbGlnbjpjZW50ZXI7cGFkZGluZzoxNnB4O2Jv"
    "cmRlci1yYWRpdXM6dmFyKC0tcnMpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2JvcmRlcjoxcHgg"
    "ZGFzaGVkIHZhcigtLWxpbmUpO21hcmdpbjoxMHB4IDB9Ci5xcnt3aWR0aDoxNTBweDtoZWlnaHQ6"
    "MTUwcHg7bWFyZ2luOjZweCBhdXRvIDEwcHg7Ym9yZGVyLXJhZGl1czp2YXIoLS1ycyk7YmFja2dy"
    "b3VuZDp2YXIoLS1jYXJkMik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtkaXNwbGF5Omdy"
    "aWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2NvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42NXJlbTts"
    "ZXR0ZXItc3BhY2luZzouMWVtO3RleHQtYWxpZ246Y2VudGVyfQouc3RlcHN7bWFyZ2luOjEycHgg"
    "MCA0cHh9Ci5zdGVwe2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHg7cGFk"
    "ZGluZzo4cHggMDtmb250LXNpemU6Ljc5cmVtO2NvbG9yOnZhcigtLXR4Mil9Ci5zdGVwIGl7d2lk"
    "dGg6MjJweDtoZWlnaHQ6MjJweDtib3JkZXItcmFkaXVzOjUwJTtib3JkZXI6MXB4IHNvbGlkIHZh"
    "cigtLWxpbmUpO2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9udC1zdHlsZTpub3Jt"
    "YWw7Zm9udC1zaXplOi42NXJlbTtmb250LXdlaWdodDo4MDA7ZmxleDowIDAgYXV0b30KLnN0ZXAu"
    "b257Y29sb3I6dmFyKC0tdHgpfQouc3RlcC5vbiBpe2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQp"
    "O2NvbG9yOiMwMjEyMGY7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50fQouc3RlcC5ydW4gaXtib3Jk"
    "ZXItY29sb3I6dmFyKC0tYWNjKTtjb2xvcjp2YXIoLS1hY2MpO2FuaW1hdGlvbjpwdWxzZSAxcyBp"
    "bmZpbml0ZX0KQGtleWZyYW1lcyBwdWxzZXs1MCV7b3BhY2l0eTouMzV9fQoudG9hc3R7cG9zaXRp"
    "b246Zml4ZWQ7bGVmdDo1MCU7Ym90dG9tOmNhbGMoOTZweCArIGVudihzYWZlLWFyZWEtaW5zZXQt"
    "Ym90dG9tKSk7dHJhbnNmb3JtOnRyYW5zbGF0ZVgoLTUwJSk7ei1pbmRleDoxMjA7YmFja2dyb3Vu"
    "ZDp2YXIoLS1iZzIpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Y29sb3I6dmFyKC0tdHgp"
    "O3BhZGRpbmc6MTFweCAxNnB4O2JvcmRlci1yYWRpdXM6OTlweDtmb250LXNpemU6Ljc4cmVtO2Zv"
    "bnQtd2VpZ2h0OjYwMDtib3gtc2hhZG93OnZhcigtLXNoYWRvdyk7b3BhY2l0eTowO3BvaW50ZXIt"
    "ZXZlbnRzOm5vbmU7dHJhbnNpdGlvbjpvcGFjaXR5IC4ycyx0cmFuc2Zvcm0gLjJzO3doaXRlLXNw"
    "YWNlOm5vd3JhcDttYXgtd2lkdGg6OTB2dztvdmVyZmxvdzpoaWRkZW47dGV4dC1vdmVyZmxvdzpl"
    "bGxpcHNpc30KLnRvYXN0LnNob3d7b3BhY2l0eToxO3RyYW5zZm9ybTp0cmFuc2xhdGVYKC01MCUp"
    "IHRyYW5zbGF0ZVkoLTZweCl9CgovKiBwcmV2aWV3IGNocm9tZSDigJQgbW9ja3VwIG9ubHksIG93"
    "biBzdHJpcCBzbyBpdCBuZXZlciBvdmVybGFwcyB0aGUgVUkgKi8KI3N3e3Bvc2l0aW9uOmZpeGVk"
    "O3RvcDowO2xlZnQ6MDtyaWdodDowO2hlaWdodDozMnB4O3otaW5kZXg6MjAwO2Rpc3BsYXk6Zmxl"
    "eDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjtnYXA6NXB4OwogIGJh"
    "Y2tncm91bmQ6cmdiYSg0LDEyLDE2LC44NSk7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0t"
    "bGluZSk7YmFja2Ryb3AtZmlsdGVyOmJsdXIoMTBweCl9CiNzdyBlbXtmb250LXN0eWxlOm5vcm1h"
    "bDtmb250LXNpemU6LjU2cmVtO2xldHRlci1zcGFjaW5nOi4xOGVtO3RleHQtdHJhbnNmb3JtOnVw"
    "cGVyY2FzZTtjb2xvcjp2YXIoLS10eDIpO21hcmdpbi1yaWdodDo0cHh9CiNzdyBidXR0b257aGVp"
    "Z2h0OjIycHg7cGFkZGluZzowIDEycHg7Ym9yZGVyLXJhZGl1czo5OXB4O2JvcmRlcjowO2N1cnNv"
    "cjpwb2ludGVyO2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7Y29sb3I6dmFyKC0tdHgyKTtmb250LWZh"
    "bWlseTp2YXIoLS1mb250KTtmb250LXNpemU6LjZyZW07Zm9udC13ZWlnaHQ6NzAwO2xldHRlci1z"
    "cGFjaW5nOi4wN2VtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZX0KI3N3IGJ1dHRvbi5vbntiYWNr"
    "Z3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojMDIxMjBmfQoKLyog4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQIEFVUk9SQSBVSSBLSVQg4oCUIHBvcnRlZCBj"
    "b21wb25lbnRzIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCAq"
    "LwpAcHJvcGVydHkgLS1nYXtzeW50YXg6IjxhbmdsZT4iO2luaXRpYWwtdmFsdWU6MGRlZztpbmhl"
    "cml0czpmYWxzZX0KQHByb3BlcnR5IC0tZ3B7c3ludGF4OiI8cGVyY2VudGFnZT4iO2luaXRpYWwt"
    "dmFsdWU6NSU7aW5oZXJpdHM6ZmFsc2V9CkBwcm9wZXJ0eSAtLWdve3N5bnRheDoiPGFuZ2xlPiI7"
    "aW5pdGlhbC12YWx1ZTowZGVnO2luaGVyaXRzOmZhbHNlfQoKLyogMjMgwrcgU2hpbnkgQ1RBIOKA"
    "lCBjb25pYyBib3JkZXIgKyBkb3QgbWFzayArIGlubmVyIHNoaW1tZXIgKyBicmVhdGhlICovCi5i"
    "dG4uY3Rhey0tZHVyOjMuMnM7LS1obDojYjhmZmYyO3Bvc2l0aW9uOnJlbGF0aXZlO2lzb2xhdGlv"
    "bjppc29sYXRlO2JvcmRlcjoxcHggc29saWQgdHJhbnNwYXJlbnQ7CiAgYmFja2dyb3VuZDpsaW5l"
    "YXItZ3JhZGllbnQodmFyKC0tYnRuYmcpLHZhcigtLWJ0bmJnKSkgcGFkZGluZy1ib3gsCiAgICBj"
    "b25pYy1ncmFkaWVudChmcm9tIGNhbGModmFyKC0tZ2EpIC0gdmFyKC0tZ28pKSx0cmFuc3BhcmVu"
    "dCx2YXIoLS1obCkgdmFyKC0tZ3ApLAogICAgICAjZmZmIGNhbGModmFyKC0tZ3ApKjIpLHZhcigt"
    "LWhsKSBjYWxjKHZhcigtLWdwKSozKSx0cmFuc3BhcmVudCBjYWxjKHZhcigtLWdwKSo0KSkgYm9y"
    "ZGVyLWJveDsKICBib3gtc2hhZG93Omluc2V0IDAgMCAwIDFweCByZ2JhKDI1NSwyNTUsMjU1LC4w"
    "OCk7CiAgdHJhbnNpdGlvbjotLWdvIC44cyBjdWJpYy1iZXppZXIoLjI1LDEsLjUsMSksLS1ncCAu"
    "OHMgY3ViaWMtYmV6aWVyKC4yNSwxLC41LDEpLHRyYW5zZm9ybSAuMTVzOwogIGFuaW1hdGlvbjpn"
    "YSB2YXIoLS1kdXIpIGxpbmVhciBpbmZpbml0ZX0KLmJ0bi5jdGE6YmVmb3JlLC5idG4uY3RhOmFm"
    "dGVye2NvbnRlbnQ6IiI7cG9zaXRpb246YWJzb2x1dGU7bGVmdDo1MCU7dG9wOjUwJTt0cmFuc2xh"
    "dGU6LTUwJSAtNTAlO3otaW5kZXg6LTE7cG9pbnRlci1ldmVudHM6bm9uZX0KLmJ0bi5jdGE6YmVm"
    "b3Jle3dpZHRoOmNhbGMoMTAwJSAtIDZweCk7aGVpZ2h0OmNhbGMoMTAwJSAtIDZweCk7Ym9yZGVy"
    "LXJhZGl1czppbmhlcml0O29wYWNpdHk6LjM1OwogIGJhY2tncm91bmQ6cmFkaWFsLWdyYWRpZW50"
    "KGNpcmNsZSBhdCAycHggMnB4LCNmZmYgLjVweCx0cmFuc3BhcmVudCAwKSBwYWRkaW5nLWJveDti"
    "YWNrZ3JvdW5kLXNpemU6NHB4IDRweDsKICAtd2Via2l0LW1hc2staW1hZ2U6Y29uaWMtZ3JhZGll"
    "bnQoZnJvbSBjYWxjKHZhcigtLWdhKSArIDQ1ZGVnKSwjMDAwLHRyYW5zcGFyZW50IDEwJSA5MCUs"
    "IzAwMCk7CiAgbWFzay1pbWFnZTpjb25pYy1ncmFkaWVudChmcm9tIGNhbGModmFyKC0tZ2EpICsg"
    "NDVkZWcpLCMwMDAsdHJhbnNwYXJlbnQgMTAlIDkwJSwjMDAwKX0KLmJ0bi5jdGE6YWZ0ZXJ7d2lk"
    "dGg6MTAwJTthc3BlY3QtcmF0aW86MTtvcGFjaXR5Oi41NTsKICBiYWNrZ3JvdW5kOmxpbmVhci1n"
    "cmFkaWVudCgtNTBkZWcsdHJhbnNwYXJlbnQsdmFyKC0taGwpLHRyYW5zcGFyZW50KTsKICAtd2Vi"
    "a2l0LW1hc2staW1hZ2U6cmFkaWFsLWdyYWRpZW50KGNpcmNsZSBhdCBib3R0b20sdHJhbnNwYXJl"
    "bnQgNDAlLCMwMDApOwogIG1hc2staW1hZ2U6cmFkaWFsLWdyYWRpZW50KGNpcmNsZSBhdCBib3R0"
    "b20sdHJhbnNwYXJlbnQgNDAlLCMwMDApOwogIGFuaW1hdGlvbjpzaGltbWVyIHZhcigtLWR1cikg"
    "bGluZWFyIGluZmluaXRlfQouYnRuLmN0YTpob3ZlcnstLWdwOjIyJTstLWdvOjk1ZGVnfQouYnRu"
    "LmN0YTphY3RpdmV7dHJhbnNsYXRlOjAgMXB4fQpAa2V5ZnJhbWVzIGdhe3Rvey0tZ2E6MzYwZGVn"
    "fX0KQGtleWZyYW1lcyBzaGltbWVye3Rve3JvdGF0ZTozNjBkZWd9fQoKLyogMjIgwrcgTGlxdWlk"
    "IGdsYXNzICovCi5idG4uZ2xhc3MsLmdsYXNzY2FyZHtwb3NpdGlvbjpyZWxhdGl2ZTtiYWNrZ3Jv"
    "dW5kOmNvbG9yLW1peChpbiBzcmdiLHZhcigtLWNhcmQyKSA3MCUsdHJhbnNwYXJlbnQpOwogIGJv"
    "cmRlcjoxcHggc29saWQgY29sb3ItbWl4KGluIHNyZ2IsI2ZmZiAxNiUsdmFyKC0tbGluZSkpO2Nv"
    "bG9yOnZhcigtLXR4KTsKICBiYWNrZHJvcC1maWx0ZXI6Ymx1cigxNHB4KSBzYXR1cmF0ZSgxLjQp"
    "Oy13ZWJraXQtYmFja2Ryb3AtZmlsdGVyOmJsdXIoMTRweCkgc2F0dXJhdGUoMS40KTsKICBib3gt"
    "c2hhZG93Omluc2V0IDFweCAxcHggMXB4IC0uNXB4IHJnYmEoMjU1LDI1NSwyNTUsLjU1KSxpbnNl"
    "dCAtMXB4IC0xcHggMXB4IC0uNXB4IHJnYmEoMjU1LDI1NSwyNTUsLjMpLAogICAgaW5zZXQgMCAw"
    "IDhweCA2cHggcmdiYSgyNTUsMjU1LDI1NSwuMDcpLDAgMnB4IDEwcHggcmdiYSgwLDAsMCwuMTIp"
    "fQouYnRuLmdsYXNzOmFmdGVye2NvbnRlbnQ6IiI7cG9zaXRpb246YWJzb2x1dGU7aW5zZXQ6MXB4"
    "IDFweCBhdXRvO2hlaWdodDo0MiU7Ym9yZGVyLXJhZGl1czppbmhlcml0OwogIGJhY2tncm91bmQ6"
    "bGluZWFyLWdyYWRpZW50KDE4MGRlZyxyZ2JhKDI1NSwyNTUsMjU1LC4zNCksdHJhbnNwYXJlbnQp"
    "O3BvaW50ZXItZXZlbnRzOm5vbmV9Ci5idG4uZ2xhc3M6aG92ZXJ7dHJhbnNmb3JtOnNjYWxlKDEu"
    "MDMpfQoKLyogcHJlc3MgcmlwcGxlIChraXQgZG9jay9idXR0b24gZmVlbCkgKi8KLnJpcHtwb3Np"
    "dGlvbjphYnNvbHV0ZTtib3JkZXItcmFkaXVzOjUwJTt0cmFuc2Zvcm06dHJhbnNsYXRlKC01MCUs"
    "LTUwJSkgc2NhbGUoMCk7cG9pbnRlci1ldmVudHM6bm9uZTsKICBiYWNrZ3JvdW5kOnJhZGlhbC1n"
    "cmFkaWVudChjaXJjbGUscmdiYSgyNTUsMjU1LDI1NSwuNTUpLHRyYW5zcGFyZW50IDcwJSk7YW5p"
    "bWF0aW9uOnJpcCAuNTVzIGVhc2Utb3V0IGZvcndhcmRzfQpAa2V5ZnJhbWVzIHJpcHt0b3t0cmFu"
    "c2Zvcm06dHJhbnNsYXRlKC01MCUsLTUwJSkgc2NhbGUoMSk7b3BhY2l0eTowfX0KCi8qIDI0IMK3"
    "IEFuaW1hdGVkIGJhbm5lciArIDE3IMK3IGxvZ28gY2xvdWQgbWFycXVlZSAqLwouYmFubmVye3Bv"
    "c2l0aW9uOnJlbGF0aXZlO292ZXJmbG93OmhpZGRlbjtib3JkZXItcmFkaXVzOjZweDtib3JkZXI6"
    "MXB4IHNvbGlkIHZhcigtLWxpbmUpOwogIGJhY2tncm91bmQ6dmFyKC0tY2FyZCk7bWFyZ2luOjJw"
    "eCAwIDEycHg7aGVpZ2h0OjM4cHg7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjsKICAt"
    "d2Via2l0LW1hc2staW1hZ2U6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRyYW5zcGFyZW50LCMwMDAg"
    "OCUsIzAwMCA5MiUsdHJhbnNwYXJlbnQpOwogIG1hc2staW1hZ2U6bGluZWFyLWdyYWRpZW50KDkw"
    "ZGVnLHRyYW5zcGFyZW50LCMwMDAgOCUsIzAwMCA5MiUsdHJhbnNwYXJlbnQpfQouYmFubmVyIC50"
    "cmFja3tkaXNwbGF5OmZsZXg7Z2FwOjMwcHg7d2hpdGUtc3BhY2U6bm93cmFwO2FuaW1hdGlvbjpt"
    "YXJxIDI2cyBsaW5lYXIgaW5maW5pdGU7d2lsbC1jaGFuZ2U6dHJhbnNmb3JtfQouYmFubmVyOmhv"
    "dmVyIC50cmFja3thbmltYXRpb24tcGxheS1zdGF0ZTpwYXVzZWR9Ci5iYW5uZXIgc3Bhbntmb250"
    "LXNpemU6LjdyZW07Y29sb3I6dmFyKC0tdHgyKTtmb250LXdlaWdodDo2MDA7bGV0dGVyLXNwYWNp"
    "bmc6LjAzZW07ZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjZweH0K"
    "LmJhbm5lciBzcGFuIGJ7Y29sb3I6dmFyKC0tYWNjKX0KLmJhbm5lciBzcGFuIHN2Z3t3aWR0aDox"
    "M3B4O2hlaWdodDoxM3B4O2NvbG9yOnZhcigtLWFjYyl9CkBrZXlmcmFtZXMgbWFycXt0b3t0cmFu"
    "c2Zvcm06dHJhbnNsYXRlWCgtNTAlKX19Ci5jbG91ZHtkaXNwbGF5OmZsZXg7Z2FwOjI2cHg7b3Zl"
    "cmZsb3c6aGlkZGVuO3BhZGRpbmc6NHB4IDAgMnB4OwogIC13ZWJraXQtbWFzay1pbWFnZTpsaW5l"
    "YXItZ3JhZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCAxMiUsIzAwMCA4OCUsdHJhbnNwYXJl"
    "bnQpOwogIG1hc2staW1hZ2U6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRyYW5zcGFyZW50LCMwMDAg"
    "MTIlLCMwMDAgODglLHRyYW5zcGFyZW50KX0KLmNsb3VkIC50e2Rpc3BsYXk6ZmxleDtnYXA6MjZw"
    "eDthbmltYXRpb246bWFycSAzMHMgbGluZWFyIGluZmluaXRlfQouY2xvdWQgaXtmb250LXN0eWxl"
    "Om5vcm1hbDtmb250LXNpemU6LjY4cmVtO2ZvbnQtd2VpZ2h0OjgwMDtsZXR0ZXItc3BhY2luZzou"
    "MTRlbTtjb2xvcjp2YXIoLS10eDIpO29wYWNpdHk6LjU1O3doaXRlLXNwYWNlOm5vd3JhcH0KCi8q"
    "IDA3IMK3IEdyYWRpZW50IHRyYWNpbmcgKi8KLnRyYWNle2Rpc3BsYXk6YmxvY2s7d2lkdGg6MTAw"
    "JTtoZWlnaHQ6MTBweDttYXJnaW46LTJweCAwIDhweDtvdmVyZmxvdzp2aXNpYmxlfQoudHJhY2Ug"
    "LmJhc2V7c3Ryb2tlOnZhcigtLWxpbmUpO3N0cm9rZS13aWR0aDoxLjR9Ci50cmFjZSAubGVhZHtz"
    "dHJva2Utd2lkdGg6MS44O3N0cm9rZS1saW5lY2FwOnJvdW5kfQouc2VjIGgye3Bvc2l0aW9uOnJl"
    "bGF0aXZlfQoKLyogMDEgKyAxMyDCtyBEb3QgbG9hZGVyICovCi5kb3Rncmlke2Rpc3BsYXk6Z3Jp"
    "ZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KDcsMWZyKTtnYXA6M3B4O3dpZHRoOjcwcHh9"
    "Ci5kb3RncmlkIGl7d2lkdGg6MTAwJTthc3BlY3QtcmF0aW86MTtib3JkZXItcmFkaXVzOjJweDti"
    "YWNrZ3JvdW5kOmNvbG9yLW1peChpbiBzcmdiLHZhcigtLXR4KSAxNCUsdHJhbnNwYXJlbnQpO3Ry"
    "YW5zaXRpb246YmFja2dyb3VuZCAuMTJzLHRyYW5zZm9ybSAuMTJzfQouZG90Z3JpZCBpLmFjdHti"
    "YWNrZ3JvdW5kOnZhcigtLWFjYyk7dHJhbnNmb3JtOnNjYWxlKDEuMTIpfQouZG90c3tkaXNwbGF5"
    "OmlubGluZS1mbGV4O2dhcDo0cHg7YWxpZ24taXRlbXM6Y2VudGVyfQouZG90cyBpe3dpZHRoOjVw"
    "eDtoZWlnaHQ6NXB4O2JvcmRlci1yYWRpdXM6NTAlO2JhY2tncm91bmQ6dmFyKC0tYWNjKTthbmlt"
    "YXRpb246Ym91bmNlIDFzIGluZmluaXRlIGVhc2UtaW4tb3V0fQouZG90cyBpOm50aC1jaGlsZCgy"
    "KXthbmltYXRpb24tZGVsYXk6LjE1c30uZG90cyBpOm50aC1jaGlsZCgzKXthbmltYXRpb24tZGVs"
    "YXk6LjNzfQpAa2V5ZnJhbWVzIGJvdW5jZXswJSw4MCUsMTAwJXt0cmFuc2Zvcm06dHJhbnNsYXRl"
    "WSgwKTtvcGFjaXR5Oi4zNX00MCV7dHJhbnNmb3JtOnRyYW5zbGF0ZVkoLTVweCk7b3BhY2l0eTox"
    "fX0KCi8qIHNwbGFzaCAqLwojc3BsYXNoe3Bvc2l0aW9uOmZpeGVkO2luc2V0OjA7ei1pbmRleDoz"
    "MDA7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjthbGlnbi1pdGVtczpjZW50ZXI7"
    "anVzdGlmeS1jb250ZW50OmNlbnRlcjtnYXA6MTZweDsKICBiYWNrZ3JvdW5kOnZhcigtLWJnKTt0"
    "cmFuc2l0aW9uOm9wYWNpdHkgLjVzIGVhc2UsdmlzaWJpbGl0eSAuNXN9CiNzcGxhc2guZ29uZXtv"
    "cGFjaXR5OjA7dmlzaWJpbGl0eTpoaWRkZW59CiNzcGxhc2ggLm1hcmt7d2lkdGg6NThweDtoZWln"
    "aHQ6NThweDtib3JkZXItcmFkaXVzOjZweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVy"
    "O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpOwogIGNvbG9yOiMwMjEyMGY7Zm9udC13ZWlnaHQ6"
    "ODAwO2ZvbnQtc2l6ZToxLjFyZW07YW5pbWF0aW9uOm1hcmtpbiAuN3MgY3ViaWMtYmV6aWVyKC4y"
    "LDEuNCwuNCwxKSBib3RofQojc3BsYXNoIGg0e21hcmdpbjowO2ZvbnQtZmFtaWx5OnZhcigtLWZo"
    "ZWFkKTtmb250LXNpemU6MXJlbTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LjAyZW07"
    "CiAgYW5pbWF0aW9uOmZhZGV1cCAuNnMgLjE1cyBib3RofQojc3BsYXNoIHNtYWxse2NvbG9yOnZh"
    "cigtLXR4Mik7Zm9udC1zaXplOi42NXJlbTtsZXR0ZXItc3BhY2luZzouMjJlbTt0ZXh0LXRyYW5z"
    "Zm9ybTp1cHBlcmNhc2U7YW5pbWF0aW9uOmZhZGV1cCAuNnMgLjI1cyBib3RofQpAa2V5ZnJhbWVz"
    "IG1hcmtpbntmcm9te29wYWNpdHk6MDt0cmFuc2Zvcm06c2NhbGUoLjYpIHJvdGF0ZSgtMTJkZWcp"
    "fXRve29wYWNpdHk6MTt0cmFuc2Zvcm06bm9uZX19CkBrZXlmcmFtZXMgZmFkZXVwe2Zyb217b3Bh"
    "Y2l0eTowO3RyYW5zZm9ybTp0cmFuc2xhdGVZKDhweCl9dG97b3BhY2l0eToxO3RyYW5zZm9ybTpu"
    "b25lfX0KCi8qIDA4IMK3IEtpbmV0aWMgZ3JpZCBjYW52YXMgKi8KI2tne3Bvc2l0aW9uOmZpeGVk"
    "O2luc2V0OjA7ei1pbmRleDowO3BvaW50ZXItZXZlbnRzOm5vbmU7b3BhY2l0eTouNjB9CgovKiBz"
    "dGFnZ2VyZWQgcmV2ZWFsIGlzIGRyaXZlbiBieSB0aGUgV2ViIEFuaW1hdGlvbnMgQVBJIChjYW5u"
    "b3QgY2xhc2ggd2l0aCB0aGVtZSBDU1MpICovCgovKiAwMiArIDI1IMK3IERvY2sgd2l0aCBzcHJp"
    "bmcgcGlsbCAqLwpuYXYjdGFicyAuZG9ja3twb3NpdGlvbjpyZWxhdGl2ZX0KbmF2I3RhYnMgLmRw"
    "aWxse3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6MDt0b3A6MDt6LWluZGV4OjA7Ym9yZGVyLXJhZGl1"
    "czo2cHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7CiAgYm94LXNoYWRvdzowIDZweCAxOHB4"
    "IGNvbG9yLW1peChpbiBzcmdiLHZhcigtLWFjYykgMzUlLHRyYW5zcGFyZW50KTtwb2ludGVyLWV2"
    "ZW50czpub25lfQpuYXYjdGFicyBidXR0b257cG9zaXRpb246cmVsYXRpdmU7ei1pbmRleDoxO3dp"
    "bGwtY2hhbmdlOnRyYW5zZm9ybTsKICB0cmFuc2l0aW9uOmNvbG9yIC4yNnMgY3ViaWMtYmV6aWVy"
    "KC4yMiwxLC4zNiwxKSx0cmFuc2Zvcm0gLjE4cyBjdWJpYy1iZXppZXIoLjMsMS41LC41LDEpfQpu"
    "YXYjdGFicyBidXR0b24ub257YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjb2xvcjojMDIxMjBmfQpu"
    "YXYjdGFicyAuZHBpbGx7b3BhY2l0eTowfQpuYXYjdGFicyAuZHBpbGwucmVhZHl7b3BhY2l0eTox"
    "fQoKLyogMTQgwrcgVi10YWJzIHNsaWRpbmcgaW5kaWNhdG9yICovCi5zdWJuYXZ7cG9zaXRpb246"
    "cmVsYXRpdmV9Ci5zdWJuYXYgLnZwaWxse3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6MDt0b3A6MDt6"
    "LWluZGV4OjA7Ym9yZGVyLXJhZGl1czo1cHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7dHJh"
    "bnNpdGlvbjpub25lO3BvaW50ZXItZXZlbnRzOm5vbmV9Ci5zdWJuYXYgYnV0dG9ue3Bvc2l0aW9u"
    "OnJlbGF0aXZlO3otaW5kZXg6MX0KLnN1Ym5hdiBidXR0b24ub257YmFja2dyb3VuZDp0cmFuc3Bh"
    "cmVudDtjb2xvcjojMDIxMjBmO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVudH0KCi8qIDE1IMK3IEFu"
    "aW1hdGVkIHByb2ZpbGUgY2FyZCDigJQgdGlsdCArIGdsYXJlICovCi50aWx0e3RyYW5zZm9ybS1z"
    "dHlsZTpwcmVzZXJ2ZS0zZDt0cmFuc2l0aW9uOnRyYW5zZm9ybSAuMzVzIGN1YmljLWJlemllcigu"
    "MjIsMSwuMzYsMSk7d2lsbC1jaGFuZ2U6dHJhbnNmb3JtfQoudGlsdCAuZ2xhcmV7cG9zaXRpb246"
    "YWJzb2x1dGU7aW5zZXQ6MDtib3JkZXItcmFkaXVzOmluaGVyaXQ7cG9pbnRlci1ldmVudHM6bm9u"
    "ZTtvcGFjaXR5OjA7dHJhbnNpdGlvbjpvcGFjaXR5IC4zczsKICBiYWNrZ3JvdW5kOnJhZGlhbC1n"
    "cmFkaWVudCgyNDBweCBjaXJjbGUgYXQgdmFyKC0tbXgsNTAlKSB2YXIoLS1teSw1MCUpLHJnYmEo"
    "MjU1LDI1NSwyNTUsLjIyKSx0cmFuc3BhcmVudCA2MCUpfQoudGlsdDpob3ZlciAuZ2xhcmV7b3Bh"
    "Y2l0eToxfQouc2hpbmVjYXJke3Bvc2l0aW9uOnJlbGF0aXZlO292ZXJmbG93OmhpZGRlbn0KLnNo"
    "aW5lY2FyZDpiZWZvcmV7Y29udGVudDoiIjtwb3NpdGlvbjphYnNvbHV0ZTt0b3A6LTYwJTtsZWZ0"
    "Oi0zMCU7d2lkdGg6NTUlO2hlaWdodDoyMjAlO3RyYW5zZm9ybTpyb3RhdGUoMThkZWcpOwogIGJh"
    "Y2tncm91bmQ6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRyYW5zcGFyZW50LHJnYmEoMjU1LDI1NSwy"
    "NTUsLjE2KSx0cmFuc3BhcmVudCk7YW5pbWF0aW9uOmNhcmRzaGluZSA1LjVzIGVhc2UtaW4tb3V0"
    "IGluZmluaXRlO3BvaW50ZXItZXZlbnRzOm5vbmV9CkBrZXlmcmFtZXMgY2FyZHNoaW5lezAlLDcw"
    "JXtsZWZ0Oi00MCV9MTAwJXtsZWZ0OjEyMCV9fQoKLyogMDUgwrcgSG9sZCBhY3Rpb24gYnV0dG9u"
    "ICovCi5ob2xke3Bvc2l0aW9uOnJlbGF0aXZlO292ZXJmbG93OmhpZGRlbn0KLmhvbGQgLmZpbGx7"
    "cG9zaXRpb246YWJzb2x1dGU7bGVmdDowO3RvcDowO2JvdHRvbTowO3dpZHRoOjA7YmFja2dyb3Vu"
    "ZDpyZ2JhKDI1NSwyNTUsMjU1LC4yNSk7dHJhbnNpdGlvbjp3aWR0aCAuMDVzIGxpbmVhcn0KLmhv"
    "bGQuZG9uZXthbmltYXRpb246cG9wIC40cyBjdWJpYy1iZXppZXIoLjIsMS42LC40LDEpfQpAa2V5"
    "ZnJhbWVzIHBvcHs1MCV7dHJhbnNmb3JtOnNjYWxlKDEuMDQpfX0KCi8qIDExIMK3IE1vcnBoaW5n"
    "IHNlYXJjaCAqLwoubXNlYXJjaHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo4"
    "cHg7bWFyZ2luOjAgMCAxMHB4fQoubXNlYXJjaCAuZmllbGR3cmFwe2ZsZXg6MTttaW4td2lkdGg6"
    "MDtwb3NpdGlvbjpyZWxhdGl2ZTtoZWlnaHQ6NDBweH0KLm1zZWFyY2ggaW5wdXR7d2lkdGg6MTAw"
    "JTtoZWlnaHQ6NDBweDtwYWRkaW5nOjAgMTNweCAwIDM2cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2Jv"
    "cmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7CiAgYmFja2dyb3VuZDp2YXIoLS1jYXJkKTtjb2xv"
    "cjp2YXIoLS10eCk7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi44cmVtO291dGxp"
    "bmU6bm9uZTsKICB0cmFuc2l0aW9uOmJveC1zaGFkb3cgLjNzIGN1YmljLWJlemllciguMjIsMSwu"
    "MzYsMSksYm9yZGVyLWNvbG9yIC4zc30KLm1zZWFyY2ggaW5wdXQ6Zm9jdXN7Ym9yZGVyLWNvbG9y"
    "OnZhcigtLWFjYyk7Ym94LXNoYWRvdzowIDAgMCA0cHggY29sb3ItbWl4KGluIHNyZ2IsdmFyKC0t"
    "YWNjKSAxNiUsdHJhbnNwYXJlbnQpfQoubXNlYXJjaCBzdmd7cG9zaXRpb246YWJzb2x1dGU7bGVm"
    "dDoxMnB4O3RvcDoxMXB4O3dpZHRoOjE3cHg7aGVpZ2h0OjE3cHg7Y29sb3I6dmFyKC0tdHgyKTt0"
    "cmFuc2l0aW9uOmNvbG9yIC4yNXN9Ci5tc2VhcmNoIGlucHV0OmZvY3VzK3N2ZywubXNlYXJjaCAu"
    "ZmllbGR3cmFwOmZvY3VzLXdpdGhpbiBzdmd7Y29sb3I6dmFyKC0tYWNjKX0KLmNoaXByb3d7ZGlz"
    "cGxheTpmbGV4O2dhcDo2cHg7b3ZlcmZsb3cteDphdXRvO3BhZGRpbmctYm90dG9tOjEwcHh9Ci5j"
    "aGlwcm93IGJ1dHRvbntmbGV4OjAgMCBhdXRvO2hlaWdodDozMHB4O3BhZGRpbmc6MCAxMnB4O2Jv"
    "cmRlci1yYWRpdXM6OTlweDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpOwogIGJhY2tncm91"
    "bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4Mik7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7"
    "Zm9udC1zaXplOi43cmVtO2ZvbnQtd2VpZ2h0OjcwMDtjdXJzb3I6cG9pbnRlcjt3aGl0ZS1zcGFj"
    "ZTpub3dyYXA7CiAgdHJhbnNpdGlvbjp0cmFuc2Zvcm0gLjJzIGN1YmljLWJlemllciguMywxLjUs"
    "LjUsMSksYmFja2dyb3VuZCAuMnMsY29sb3IgLjJzfQouY2hpcHJvdyBidXR0b24ub257YmFja2dy"
    "b3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzAyMTIwZjtib3JkZXItY29sb3I6dHJhbnNwYXJl"
    "bnQ7dHJhbnNmb3JtOnNjYWxlKDEuMDQpfQoKLyogc2tlbGV0b25zICgxMyDCtyBsb2FkZXIpICov"
    "Ci5za3twb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRkZW47YmFja2dyb3VuZDp2YXIoLS1j"
    "YXJkMik7Ym9yZGVyLXJhZGl1czp2YXIoLS1ycyk7aGVpZ2h0Ojc0cHg7bWFyZ2luLWJvdHRvbTo5"
    "cHh9Ci5zazphZnRlcntjb250ZW50OiIiO3Bvc2l0aW9uOmFic29sdXRlO2luc2V0OjA7YmFja2dy"
    "b3VuZDpsaW5lYXItZ3JhZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsY29sb3ItbWl4KGluIHNyZ2Is"
    "dmFyKC0tdHgpIDEwJSx0cmFuc3BhcmVudCksdHJhbnNwYXJlbnQpOwogIHRyYW5zZm9ybTp0cmFu"
    "c2xhdGVYKC0xMDAlKTthbmltYXRpb246c2hpbSAxLjE1cyBpbmZpbml0ZX0KQGtleWZyYW1lcyBz"
    "aGlte3Rve3RyYW5zZm9ybTp0cmFuc2xhdGVYKDEwMCUpfX0KCi8qIDE5IMK3IEZlYXR1cmUgc3Ry"
    "aXAgKi8KLmZlYXRze2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KDMs"
    "MWZyKTtnYXA6OHB4O21hcmdpbi1ib3R0b206MTJweH0KLmZlYXR7YmFja2dyb3VuZDp2YXIoLS1j"
    "YXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6dmFyKC0tcnMp"
    "O3BhZGRpbmc6MTFweCA5cHg7dGV4dC1hbGlnbjpjZW50ZXI7CiAgdHJhbnNpdGlvbjp0cmFuc2Zv"
    "cm0gLjI1cyBjdWJpYy1iZXppZXIoLjIyLDEsLjM2LDEpfQouZmVhdDpob3Zlcnt0cmFuc2Zvcm06"
    "dHJhbnNsYXRlWSgtM3B4KX0KLmZlYXQgc3Zne3dpZHRoOjE3cHg7aGVpZ2h0OjE3cHg7Y29sb3I6"
    "dmFyKC0tYWNjKX0KLmZlYXQgYntkaXNwbGF5OmJsb2NrO2ZvbnQtc2l6ZTouNjhyZW07bWFyZ2lu"
    "LXRvcDo1cHh9Ci5mZWF0IHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42cmVtO2NvbG9y"
    "OnZhcigtLXR4Mil9CgovKiAwMyDCtyBXb3JrcyB3aGVlbCDigJQgcm90YXRpbmcgZGVhbCBkaWFs"
    "ICovCi53aGVlbHtwb3NpdGlvbjpyZWxhdGl2ZTtoZWlnaHQ6MTE4cHg7bWFyZ2luOjJweCAwIDEy"
    "cHg7b3ZlcmZsb3c6aGlkZGVuO2JvcmRlci1yYWRpdXM6dmFyKC0tcik7CiAgYm9yZGVyOjFweCBz"
    "b2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQpfQoud2hlZWwgLnJpbmd7cG9z"
    "aXRpb246YWJzb2x1dGU7cmlnaHQ6LTUycHg7dG9wOjUwJTt3aWR0aDoxNzZweDtoZWlnaHQ6MTc2"
    "cHg7bWFyZ2luLXRvcDotODhweDtib3JkZXItcmFkaXVzOjUwJTsKICBib3JkZXI6MXB4IGRhc2hl"
    "ZCB2YXIoLS1saW5lKTthbmltYXRpb246c3BpbiAyNnMgbGluZWFyIGluZmluaXRlfQoud2hlZWwg"
    "LnJpbmcgYntwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjUwJTt0b3A6NTAlO3dpZHRoOjUwcHg7aGVp"
    "Z2h0OjI0cHg7bWFyZ2luOi0xMnB4IDAgMCAtMjVweDtib3JkZXItcmFkaXVzOjk5cHg7CiAgZGlz"
    "cGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtmb250LXNpemU6LjYycmVtO2ZvbnQtd2VpZ2h0"
    "OjgwMDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUp"
    "O2NvbG9yOnZhcigtLXR4Mil9Ci53aGVlbCAuY2Fwe3Bvc2l0aW9uOmFic29sdXRlO2luc2V0OjA7"
    "ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVy"
    "O3BhZGRpbmc6MCAxNnB4OwogIGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KDkwZGVnLHZhcigt"
    "LWNhcmQpIDAgNTglLHRyYW5zcGFyZW50KX0KLndoZWVsIC5jYXA+KnttYXgtd2lkdGg6NjIlfQou"
    "d2hlZWwgLmNhcCBlbXtmb250LXN0eWxlOm5vcm1hbDtmb250LXNpemU6LjZyZW07bGV0dGVyLXNw"
    "YWNpbmc6LjE4ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2NvbG9yOnZhcigtLWFjYyk7Zm9u"
    "dC13ZWlnaHQ6ODAwfQoud2hlZWwgLmNhcCBie2ZvbnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250"
    "LXNpemU6MS4wNXJlbTtmb250LXdlaWdodDo3MDB9CkBrZXlmcmFtZXMgc3Bpbnt0b3t0cmFuc2Zv"
    "cm06cm90YXRlKDM2MGRlZyl9fQoKLyogc3VjY2VzcyBidXJzdCAqLwojZnh7cG9zaXRpb246Zml4"
    "ZWQ7aW5zZXQ6MDt6LWluZGV4OjE1MDtkaXNwbGF5Om5vbmU7cGxhY2UtaXRlbXM6Y2VudGVyO3Bv"
    "aW50ZXItZXZlbnRzOm5vbmV9CiNmeC5vbntkaXNwbGF5OmdyaWR9CiNmeCAucmluZ3t3aWR0aDo4"
    "MnB4O2hlaWdodDo4MnB4O2JvcmRlci1yYWRpdXM6NTAlO2JvcmRlcjoycHggc29saWQgdmFyKC0t"
    "b2spO2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7CiAgY29sb3I6dmFyKC0tb2spO2Fu"
    "aW1hdGlvbjpidXJzdCAuNzVzIGN1YmljLWJlemllciguMiwxLjMsLjQsMSl9CiNmeCAucmluZyBz"
    "dmd7d2lkdGg6MzRweDtoZWlnaHQ6MzRweH0KI2Z4IC53YXZle3Bvc2l0aW9uOmFic29sdXRlO3dp"
    "ZHRoOjgycHg7aGVpZ2h0OjgycHg7Ym9yZGVyLXJhZGl1czo1MCU7Ym9yZGVyOjJweCBzb2xpZCB2"
    "YXIoLS1vayk7YW5pbWF0aW9uOndhdmUgLjlzIGVhc2Utb3V0IGZvcndhcmRzfQpAa2V5ZnJhbWVz"
    "IGJ1cnN0e2Zyb217dHJhbnNmb3JtOnNjYWxlKC40KTtvcGFjaXR5OjB9dG97dHJhbnNmb3JtOnNj"
    "YWxlKDEpO29wYWNpdHk6MX19CkBrZXlmcmFtZXMgd2F2ZXt0b3t0cmFuc2Zvcm06c2NhbGUoMy40"
    "KTtvcGFjaXR5OjB9fQoKLyogbnVtYmVyIHRpY2tlciAqLwoudGlja3tmb250LXZhcmlhbnQtbnVt"
    "ZXJpYzp0YWJ1bGFyLW51bXN9CgpAbWVkaWEocHJlZmVycy1yZWR1Y2VkLW1vdGlvbjpyZWR1Y2Up"
    "eyp7YW5pbWF0aW9uOm5vbmUhaW1wb3J0YW50O3RyYW5zaXRpb246bm9uZSFpbXBvcnRhbnR9fQoK"
    "Ym9keTpiZWZvcmV7YW5pbWF0aW9uOmdyaWRtb3ZlIDE0cyBsaW5lYXIgaW5maW5pdGV9QGtleWZy"
    "YW1lcyBncmlkbW92ZXt0b3tiYWNrZ3JvdW5kLXBvc2l0aW9uOjM0cHggMzRweH19Lmhlcm8gaDF7"
    "Zm9udC1zaXplOjEuMzVyZW07dGV4dC1zaGFkb3c6MCAwIDE4cHggcmdiYSgwLDI1NSwyMTQsLjQ1"
    "KX0uYnRue3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtsZXR0ZXItc3BhY2luZzouMDZlbX1uYXYj"
    "dGFicyBidXR0b257dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlfS5rcGkgYnt0ZXh0LXNoYWRvdzow"
    "IDAgMTRweCByZ2JhKDAsMjU1LDIxNCwuMzUpfQo8L3N0eWxlPgo8L2hlYWQ+Cjxib2R5PgoKPGNh"
    "bnZhcyBpZD0ia2ciPjwvY2FudmFzPgo8ZGl2IGlkPSJzcGxhc2giPjxkaXYgY2xhc3M9Im1hcmsi"
    "IGlkPSJzcG1hcmsiPlpUPC9kaXY+PGRpdiBjbGFzcz0iZG90Z3JpZCIgaWQ9ImRnIj48L2Rpdj4K"
    "ICA8aDQgaWQ9InNwbmFtZSI+Wm9ybyBURyBTdG9yZTwvaDQ+PHNtYWxsIGlkPSJzcHRhZyI+ZW5j"
    "cnlwdGVkIGRlbGl2ZXJ5PC9zbWFsbD48L2Rpdj4KPHN2ZyB3aWR0aD0iMCIgaGVpZ2h0PSIwIiBz"
    "dHlsZT0icG9zaXRpb246YWJzb2x1dGUiPjxkZWZzPgogIDxmaWx0ZXIgaWQ9ImxnIiB4PSIwJSIg"
    "eT0iMCUiIHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiIGNvbG9yLWludGVycG9sYXRpb24tZmls"
    "dGVycz0ic1JHQiI+CiAgICA8ZmVUdXJidWxlbmNlIHR5cGU9ImZyYWN0YWxOb2lzZSIgYmFzZUZy"
    "ZXF1ZW5jeT0iMC4wNSAwLjA1IiBudW1PY3RhdmVzPSIxIiBzZWVkPSIxIiByZXN1bHQ9InQiLz4K"
    "ICAgIDxmZUdhdXNzaWFuQmx1ciBpbj0idCIgc3RkRGV2aWF0aW9uPSIyIiByZXN1bHQ9ImIiLz4K"
    "ICAgIDxmZURpc3BsYWNlbWVudE1hcCBpbj0iU291cmNlR3JhcGhpYyIgaW4yPSJiIiBzY2FsZT0i"
    "NzAiIHhDaGFubmVsU2VsZWN0b3I9IlIiIHlDaGFubmVsU2VsZWN0b3I9IkIiLz4KICA8L2ZpbHRl"
    "cj48L2RlZnM+PC9zdmc+CjxkaXYgaWQ9ImZ4Ij48ZGl2IGNsYXNzPSJ3YXZlIj48L2Rpdj48ZGl2"
    "IGNsYXNzPSJyaW5nIj48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tl"
    "PSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMi40IiBzdHJva2UtbGluZWNhcD0icm91bmQi"
    "IHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yMCA2IDkgMTdsLTUtNSIvPjwvc3Zn"
    "PjwvZGl2PjwvZGl2Pgo8ZGl2IGlkPSJzdyI+PGVtPlByZXZpZXc8L2VtPgogIDxidXR0b24gZGF0"
    "YS1zdz0iYXBwIiBjbGFzcz0ib24iPlN0b3JlPC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXN3PSJh"
    "ZG1pbiI+QWRtaW48L2J1dHRvbj4KPC9kaXY+Cgo8ZGl2IGlkPSJ2aWV3LWFwcCI+CjxkaXYgaWQ9"
    "InNoZWxsIj4KICA8aGVhZGVyIGlkPSJ0b3BiYXIiPgogICAgPGRpdiBjbGFzcz0ibG9nbyI+WlQ8"
    "L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJyYW5kIj48YiBpZD0iYm5hbWUiPlpvcm8gVEcgU3RvcmU8"
    "L2I+PHNwYW4gaWQ9ImJ0YWciPmVuY3J5cHRlZCBkZWxpdmVyeTwvc3Bhbj48L2Rpdj4KICAgIDxi"
    "dXR0b24gY2xhc3M9Imljb25jaGlwIGhpZGRlbiIgaWQ9ImNhcnRjaGlwIiB0aXRsZT0iQ2FydCI+"
    "PHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3VycmVudENvbG9y"
    "IiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2lu"
    "PSJyb3VuZCI+PHBhdGggZD0iTTYgMiAzIDZ2MTRhMiAyIDAgMCAwIDIgMmgxNGEyIDIgMCAwIDAg"
    "Mi0yVjZsLTMtNHoiLz48cGF0aCBkPSJNMyA2aDE4Ii8+PHBhdGggZD0iTTE2IDEwYTQgNCAwIDAg"
    "MS04IDAiLz48L3N2Zz48aSBpZD0iY2FydG4iPjA8L2k+PC9idXR0b24+CiAgICA8YnV0dG9uIGNs"
    "YXNzPSJiYWxjaGlwIiBkYXRhLWdvPSJ3YWxsZXQiPjxzcGFuIGNsYXNzPSJwbHVzIj4rPC9zcGFu"
    "PuKCuTxzcGFuIGlkPSJiYWwiIGNsYXNzPSJ0aWNrIj4yNDguNTA8L3NwYW4+PC9idXR0b24+CiAg"
    "PC9oZWFkZXI+CiAgPG1haW4gaWQ9InN0YWdlIj48L21haW4+CjwvZGl2Pgo8bmF2IGlkPSJ0YWJz"
    "Ij48ZGl2IGNsYXNzPSJkb2NrIj48c3BhbiBjbGFzcz0iZHBpbGwiIGlkPSJkcGlsbCI+PC9zcGFu"
    "PgogIDxidXR0b24gZGF0YS10YWI9InNob3AiIGNsYXNzPSJvbiI+PHN2ZyB2aWV3Qm94PSIwIDAg"
    "MjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3VycmVudENvbG9yIiBzdHJva2Utd2lkdGg9IjIi"
    "IHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0i"
    "TTYgMiAzIDZ2MTRhMiAyIDAgMCAwIDIgMmgxNGEyIDIgMCAwIDAgMi0yVjZsLTMtNHoiLz48cGF0"
    "aCBkPSJNMyA2aDE4Ii8+PHBhdGggZD0iTTE2IDEwYTQgNCAwIDAgMS04IDAiLz48L3N2Zz48c3Bh"
    "biBkYXRhLW5hdj0ic2hvcCI+U2hvcDwvc3Bhbj48L2J1dHRvbj4KICA8YnV0dG9uIGRhdGEtdGFi"
    "PSJvcmRlcnMiPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1"
    "cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9r"
    "ZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yMSA4YTIgMiAwIDAgMC0xLTEuNzNsLTctNGEy"
    "IDIgMCAwIDAtMiAwbC03IDRBMiAyIDAgMCAwIDMgOHY4YTIgMiAwIDAgMCAxIDEuNzNsNyA0YTIg"
    "MiAwIDAgMCAyIDBsNy00QTIgMiAwIDAgMCAyMSAxNloiLz48cGF0aCBkPSJtMy4zIDcgOC43IDUg"
    "OC43LTUiLz48cGF0aCBkPSJNMTIgMjJWMTIiLz48L3N2Zz48c3BhbiBkYXRhLW5hdj0ib3JkZXJz"
    "Ij5PcmRlcnM8L3NwYW4+PC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXRhYj0iZWFybiI+PHN2ZyB2"
    "aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3VycmVudENvbG9yIiBzdHJv"
    "a2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3Vu"
    "ZCI+PHBhdGggZD0iTTEzIDIgMyAxNGg5bC0xIDggMTAtMTJoLTl6Ii8+PC9zdmc+PHNwYW4gZGF0"
    "YS1uYXY9ImVhcm4iPkVhcm48L3NwYW4+PC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXRhYj0id2Fs"
    "bGV0Ij48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50"
    "Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGlu"
    "ZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTkgN1Y1YTIgMiAwIDAgMC0yLTJINWEyIDIgMCAwIDAg"
    "MCA0aDE1YTIgMiAwIDAgMSAyIDJ2OWEyIDIgMCAwIDEtMiAySDVhMiAyIDAgMCAxLTItMlY1Ii8+"
    "PHBhdGggZD0iTTE4IDEyaC4wMSIvPjwvc3ZnPjxzcGFuIGRhdGEtbmF2PSJ3YWxsZXQiPldhbGxl"
    "dDwvc3Bhbj48L2J1dHRvbj4KICA8YnV0dG9uIGRhdGEtdGFiPSJ5b3UiPjxzdmcgdmlld0JveD0i"
    "MCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRo"
    "PSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRo"
    "IGQ9Ik0xOSAyMXYtMmE0IDQgMCAwIDAtNC00SDlhNCA0IDAgMCAwLTQgNHYyIi8+PGNpcmNsZSBj"
    "eD0iMTIiIGN5PSI3IiByPSI0Ii8+PC9zdmc+PHNwYW4gZGF0YS1uYXY9InlvdSI+WW91PC9zcGFu"
    "PjwvYnV0dG9uPgo8L2Rpdj48L25hdj4KPC9kaXY+Cgo8ZGl2IGlkPSJ2aWV3LWFkbWluIiBjbGFz"
    "cz0iaGlkZGVuIj48ZGl2IGlkPSJzaGVsbCI+CiAgPGhlYWRlciBpZD0idG9wYmFyIj4KICAgIDxk"
    "aXYgY2xhc3M9ImxvZ28iPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJv"
    "a2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQi"
    "IHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yMCAxM2MwIDUtMy41IDcuNS03LjY2"
    "IDguOTVhMSAxIDAgMCAxLS42Ny0uMDFDNy41IDIwLjUgNCAxOCA0IDEzVjZhMSAxIDAgMCAxIDEt"
    "MWMyIDAgNC41LTEuMiA2LjI0LTIuNzJhMS4xNyAxLjE3IDAgMCAxIDEuNTIgMEMxNC41MSAzLjgx"
    "IDE3IDUgMTkgNWExIDEgMCAwIDEgMSAxeiIvPjwvc3ZnPjwvZGl2PgogICAgPGRpdiBjbGFzcz0i"
    "YnJhbmQiPjxiPkFkbWluIENvbnNvbGU8L2I+PHNwYW4gaWQ9ImFibmFtZSI+Wm9ybyBURyBTdG9y"
    "ZTwvc3Bhbj48L2Rpdj4KICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFzcz0iZG90Ij48"
    "L2k+TGl2ZTwvc3Bhbj4KICA8L2hlYWRlcj4KICA8ZGl2IGNsYXNzPSJzdWJuYXYiIGlkPSJhc3Vi"
    "Ij48c3BhbiBjbGFzcz0idnBpbGwiIGlkPSJ2cGlsbCI+PC9zcGFuPgogICAgPGJ1dHRvbiBkYXRh"
    "LWE9ImRhc2giIGNsYXNzPSJvbiI+RGFzaGJvYXJkPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEt"
    "YT0iY2hhbm5lbHMiPkNoYW5uZWxzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0ibG9ncyI+"
    "TG9nczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9InBheSI+QXV0by1QYXk8L2J1dHRvbj4K"
    "ICAgIDxidXR0b24gZGF0YS1hPSJjb250ZW50Ij5Db250ZW50PC9idXR0b24+CiAgICA8YnV0dG9u"
    "IGRhdGEtYT0iZmVhdHVyZXMiPkZlYXR1cmVzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0i"
    "bWFya2V0Ij5NYXJrZXRpbmc8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJhbmFseXRpY3Mi"
    "PkFuYWx5dGljczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9InNlY3VyaXR5Ij5TZWN1cml0"
    "eTwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9InN0b2NrIj5TdG9jazwvYnV0dG9uPgogICAg"
    "PGJ1dHRvbiBkYXRhLWE9Im9yZGVycyI+T3JkZXJzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEt"
    "YT0idXNlcnMiPlVzZXJzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0ic2V0dGluZ3MiPlNl"
    "dHRpbmdzPC9idXR0b24+CiAgPC9kaXY+CiAgPG1haW4gaWQ9ImFzdGFnZSI+PC9tYWluPgo8L2Rp"
    "dj48L2Rpdj4KCjxkaXYgaWQ9InNoZWV0Ij48ZGl2IGNsYXNzPSJiZyIgZGF0YS1jbG9zZT48L2Rp"
    "dj48ZGl2IGNsYXNzPSJwYW5lbCIgaWQ9InBhbmVsIj48L2Rpdj48L2Rpdj4KPGRpdiBjbGFzcz0i"
    "dG9hc3QiIGlkPSJ0b2FzdCI+PC9kaXY+Cgo8c2NyaXB0PgovKiDilIDilIAgbGl2ZS1tb2RlIHN0"
    "YXRlLCBkZWNsYXJlZCBmaXJzdCBzbyBub3RoaW5nIGNhbiByZWFkIGl0IHRvbyBlYXJseSDilIDi"
    "lIAgKi8KdmFyIExJVkU9ZmFsc2UsTE9DS0VEPWZhbHNlLE1FPXthZG1pbjpmYWxzZSxvd25lcjpm"
    "YWxzZSxwZXJtczp7fX07CnZhciBURz13aW5kb3cuVGVsZWdyYW0mJndpbmRvdy5UZWxlZ3JhbS5X"
    "ZWJBcHB8fG51bGw7CmZ1bmN0aW9uIHJlYWRJbml0RGF0YSgpewogIHRyeXtpZihURyYmVEcuaW5p"
    "dERhdGEmJlRHLmluaXREYXRhLmxlbmd0aClyZXR1cm4gVEcuaW5pdERhdGE7fWNhdGNoKGUpe30K"
    "ICAvKiBUZWxlZ3JhbSBhbHNvIHB1dHMgaXQgaW4gdGhlIFVSTCwgc28gdGhlIGFwcCBzdGlsbCB3"
    "b3JrcyBpZiB0aGUgU0RLIGlzIGJsb2NrZWQgKi8KICB0cnl7CiAgICBjb25zdCBtPS9bIyY/XXRn"
    "V2ViQXBwRGF0YT0oW14mXSopLy5leGVjKGxvY2F0aW9uLmhhc2h8fCIiKTsKICAgIGlmKG0mJm1b"
    "MV0pcmV0dXJuIGRlY29kZVVSSUNvbXBvbmVudChtWzFdKTsKICB9Y2F0Y2goZSl7fQogIHRyeXsK"
    "ICAgIGNvbnN0IHE9bmV3IFVSTFNlYXJjaFBhcmFtcyhsb2NhdGlvbi5zZWFyY2gpOwogICAgY29u"
    "c3Qgdj1xLmdldCgidGdXZWJBcHBEYXRhIik7CiAgICBpZih2KXJldHVybiB2OwogIH1jYXRjaChl"
    "KXt9CiAgdHJ5ewogICAgY29uc3Qgc2F2ZWQ9c2Vzc2lvblN0b3JhZ2UuZ2V0SXRlbSgidGdJbml0"
    "Iik7CiAgICBpZihzYXZlZClyZXR1cm4gc2F2ZWQ7CiAgfWNhdGNoKGUpe30KICByZXR1cm4gIiI7"
    "Cn0KZnVuY3Rpb24gaW5pdGlhbHMobmFtZSl7CiAgY29uc3Qgdz0obmFtZXx8IiIpLnRyaW0oKS5z"
    "cGxpdCgvXHMrLykuZmlsdGVyKEJvb2xlYW4pOwogIGlmKCF3Lmxlbmd0aClyZXR1cm4gIlNUIjsK"
    "ICByZXR1cm4gKCh3WzBdWzBdfHwiIikrKHcubGVuZ3RoPjE/KHdbMV1bMF18fCIiKTood1swXVsx"
    "XXx8IiIpKSkudG9VcHBlckNhc2UoKTsKfQpmdW5jdGlvbiBwYWludEJyYW5kKG5hbWUsdGFnKXsK"
    "ICBpZihuYW1lKXsKICAgIGNvbnN0IG49ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNwbmFtZSIp"
    "OyBpZihuKW4udGV4dENvbnRlbnQ9bmFtZTsKICAgIGNvbnN0IG09ZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoInNwbWFyayIpOyBpZihtKW0udGV4dENvbnRlbnQ9aW5pdGlhbHMobmFtZSk7CiAgICBj"
    "b25zdCBiPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJibmFtZSIpOyBpZihiKWIudGV4dENvbnRl"
    "bnQ9bmFtZTsKICAgIGNvbnN0IGFiPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJhYm5hbWUiKTsg"
    "aWYoYWIpYWIudGV4dENvbnRlbnQ9bmFtZTsKICAgIGRvY3VtZW50LnRpdGxlPW5hbWU7CiAgfQog"
    "IGlmKHRhZyl7CiAgICBjb25zdCB0PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzcHRhZyIpOyBp"
    "Zih0KXQudGV4dENvbnRlbnQ9dGFnOwogICAgY29uc3QgYnQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5"
    "SWQoImJ0YWciKTsgaWYoYnQpYnQudGV4dENvbnRlbnQ9dGFnOwogIH0KfQp0cnl7CiAgY29uc3Qg"
    "Y249bG9jYWxTdG9yYWdlLmdldEl0ZW0oInN0b3JlTmFtZSIpLCBjdD1sb2NhbFN0b3JhZ2UuZ2V0"
    "SXRlbSgic3RvcmVUYWciKTsKICBpZihjbnx8Y3QpcGFpbnRCcmFuZChjbixjdCk7Cn1jYXRjaChl"
    "KXt9CnZhciBJTklUPXJlYWRJbml0RGF0YSgpOwp0cnl7aWYoSU5JVClzZXNzaW9uU3RvcmFnZS5z"
    "ZXRJdGVtKCJ0Z0luaXQiLElOSVQpO31jYXRjaChlKXt9CmZ1bmN0aW9uIHJlYWRBcHBLZXkoKXsK"
    "ICB0cnl7CiAgICBjb25zdCBxPW5ldyBVUkxTZWFyY2hQYXJhbXMobG9jYXRpb24uc2VhcmNoKTsK"
    "ICAgIGlmKHEuZ2V0KCJrIikpcmV0dXJuIHEuZ2V0KCJrIik7CiAgICBjb25zdCBtPS9bIyY/XWs9"
    "KFteJl0qKS8uZXhlYyhsb2NhdGlvbi5oYXNofHwiIik7CiAgICBpZihtJiZtWzFdKXJldHVybiBk"
    "ZWNvZGVVUklDb21wb25lbnQobVsxXSk7CiAgICByZXR1cm4gc2Vzc2lvblN0b3JhZ2UuZ2V0SXRl"
    "bSgiYXBwS2V5Iil8fCIiOwogIH1jYXRjaChlKXtyZXR1cm4gIiI7fQp9CnZhciBBUFBLRVk9cmVh"
    "ZEFwcEtleSgpOwp0cnl7aWYoQVBQS0VZKXNlc3Npb25TdG9yYWdlLnNldEl0ZW0oImFwcEtleSIs"
    "QVBQS0VZKTt9Y2F0Y2goZSl7fQovKiBkZW1vIGRhdGEgb25seSDigJQgbm8gdG9rZW5zLCBubyBV"
    "UEkgSUQsIG5vIEdtYWlsIGNyZWRlbnRpYWxzICovCmxldCBDRkc9e25hbWU6Ilpvcm8gVEcgU3Rv"
    "cmUiLHRhZzoiZW5jcnlwdGVkIGRlbGl2ZXJ5IixjdXI6IuKCuSJ9OwovKiDilIDilIAgRWRpdGFi"
    "bGUgY29weSDigJQgZXZlcnkgc3RyaW5nIGJlbG93IGlzIGVkaXRhYmxlIGluIEFkbWluIOKWuCBD"
    "b250ZW50IOKUgOKUgCAqLwpjb25zdCBDTVM9ewogc3RvcmVOYW1lOiJab3JvIFRHIFN0b3JlIiwg"
    "c3RvcmVUYWc6ImVuY3J5cHRlZCBkZWxpdmVyeSIsCiBlbXB0eVRpdGxlOiJSZXN0b2NraW5nIiwg"
    "ZW1wdHlCb2R5OiJFdmVyeXRoaW5nIGp1c3Qgc29sZCBvdXQuIEZyZXNoIGFjY291bnRzIGxhbmQg"
    "aGVyZSBzaG9ydGx5IOKAlCB0dXJuIG9uIGFuIGFsZXJ0IGFuZCB5b3Ugd2lsbCBoZWFyIGZpcnN0"
    "LiIsCiB0aWNrMToiPGI+SW5zdGFudCBkZWxpdmVyeTwvYj4gb24gZXZlcnkgY291bnRyeSIsIHRp"
    "Y2syOiJUb3AtdXBzIDxiPmNvbmZpcm1lZCBhdXRvbWF0aWNhbGx5PC9iPiIsCiB0aWNrMzoiRmFp"
    "bGVkIGxvZ2luPyA8Yj5BdXRvIHJlZnVuZDwvYj4iLCB0aWNrNDoiRGFpbHkgY2hlY2staW4gPGI+"
    "4oK5MC40MDwvYj4iLAogaGVyb0tpY2s6Ij4gaW52ZW50b3J5IC0tbGl2ZSIsIGhlcm9IMToiU0VD"
    "VVJFX0FDQ09VTlRTPGJyPjBNUyBERUxJVkVSWSIsCiBoZXJvU3ViOiJJbnN0YW50IGRlbGl2ZXJ5"
    "LCBPVFAgaW4tYXBwLCB3YWxsZXQgdG9wLXVwcyB2ZXJpZmllZCBhdXRvbWF0aWNhbGx5LiB7bn0g"
    "Y291bnRyaWVzIGxpdmUuIiwKIGhlcm9CdG4xOiJCcm93c2UgU3RvY2siLCBoZXJvQnRuMjoiQWRk"
    "IEZ1bmRzIiwKIGZlYXQxOiJ+OHMiLCBmZWF0MXM6ImRlbGl2ZXJ5IiwgZmVhdDI6IkF1dG8iLCBm"
    "ZWF0MnM6InJlZnVuZHMiLCBmZWF0MzoiVVBJIiwgZmVhdDNzOiJhdXRvLXZlcmlmeSIsCiBmbGFz"
    "aFBpbGw6IkZsYXNoIOKIkjIwJSIsIGZsYXNoVGV4dDoiRW5kcyBpbiIsIGZsYXNoQnRuOiJWaWV3"
    "IiwKIHdoZWVsS2ljazoiUm90YXRpbmcgZGVhbHMiLCB3aGVlbFRpdGxlOiJVcCB0byAyOCUgb2Zm"
    "Iiwgd2hlZWxXb3JkczoiSU5TVEFOVCwgQVVUTy1WRVJJRlksIFJFRlVORFMsIDI0w5c3IiwKIHNo"
    "b3BIZWFkOiJJbiBTdG9jayIsIHNob3BTb3J0OiJTb3J0Iiwgc2VhcmNoUGg6IlNlYXJjaCBjb3Vu"
    "dHJ5IG9yIGNvZGXigKYiLCBhbGVydHNCdG46IkFsZXJ0cyIsCiB3YWxLaWNrOiJCYWxhbmNlIiwg"
    "d2FsU3ViOiJQYXkgYnkgVVBJIOKAlCB0YXAgVmVyaWZ5IGFmdGVyIHBheWluZyBhbmQgeW91ciB3"
    "YWxsZXQgaXMgY3JlZGl0ZWQgaW4gc2Vjb25kcy4iLAogd2FsQWRkOiJBZGQgRnVuZHMiLCBwYXlU"
    "aXRsZToiUGF5bWVudCBEZXRhaWxzIiwgcGF5U3ViOiJBZG1pbiBzZXRzIHRoZSBVUEkgaGFuZGxl"
    "IOKAlCBub3RoaW5nIGlzIGhhcmQtY29kZWQgaW4gdGhlIGFwcC4iLAogcGF5UHJlZml4OiJQQVki"
    "LCBwYXlXaW5kb3c6IjUiLAogc2hlZXRUaXRsZToiQWRkIiwgc2hlZXRTdWI6IlBheSB0aGUgZXhh"
    "Y3QgYW1vdW50LCB0aGVuIHRhcCBWZXJpZnkgUGF5bWVudC4gVGhlIHJlcXVlc3QgZXhwaXJlcyBp"
    "biB7bX0gbWludXRlcy4iLAogcGF5QnRuOiJQYXkgTm93IiwgdmVyaWZ5QnRuOiJWZXJpZnkgUGF5"
    "bWVudCIsIHV0ckJ0bjoiTm90IGRldGVjdGVkPyBTdWJtaXQgVVRSIiwKIHN0ZXAxOiJQYXltZW50"
    "IHJlcXVlc3QgY3JlYXRlZCIsIHN0ZXAyOiJQYXkgdGhlIGV4YWN0IGFtb3VudCBpbiBhbnkgVVBJ"
    "IGFwcCIsCiBzdGVwMzoiVGFwIFZlcmlmeSDigJQgd2UgY29uZmlybSB5b3VyIHBheW1lbnQiLCBz"
    "dGVwNDoiV2FsbGV0IGNyZWRpdGVkIiwKIG1zZ0NoZWNraW5nOiJDaGVja2luZyB5b3VyIHBheW1l"
    "bnTigKYiLCBtc2dPazoiVmVyaWZpZWQg4oCUIHdhbGxldCBjcmVkaXRlZCIsCiBtc2dOb25lOiJO"
    "byBwYXltZW50IGZvdW5kIHlldC4gUGF5IGZpcnN0LCB0aGVuIHRhcCBWZXJpZnkuIiwgbXNnRXhw"
    "aXJlZDoiUmVxdWVzdCBleHBpcmVkIOKAlCBjcmVhdGUgYSBuZXcgb25lLiIsCiBuYXZTaG9wOiJT"
    "aG9wIiwgbmF2T3JkZXJzOiJPcmRlcnMiLCBuYXZFYXJuOiJFYXJuIiwgbmF2V2FsbGV0OiJXYWxs"
    "ZXQiLCBuYXZZb3U6IllvdSIKfTsKY29uc3QgQ01TMD1PYmplY3QuYXNzaWduKHt9LENNUyk7CmNv"
    "bnN0IENNU01FVEE9WwogWyJTdG9yZSBoZWFkZXIiLFtbInN0b3JlTmFtZSIsIlN0b3JlIG5hbWUi"
    "XSxbInN0b3JlVGFnIiwiVGFnbGluZSJdXV0sCiBbIlNjcm9sbGluZyB0aWNrZXIiLFtbInRpY2sx"
    "IiwiVGlja2VyIDEiXSxbInRpY2syIiwiVGlja2VyIDIiXSxbInRpY2szIiwiVGlja2VyIDMiXSxb"
    "InRpY2s0IiwiVGlja2VyIDQiXV1dLAogWyJIZXJvIHNlY3Rpb24iLFtbImhlcm9LaWNrIiwiU21h"
    "bGwgbGFiZWwgYWJvdmUgdGl0bGUiXSxbImhlcm9IMSIsIkJpZyB0aXRsZSJdLFsiaGVyb1N1YiIs"
    "IlBhcmFncmFwaCB1bmRlciB0aGUgdGl0bGUiXSxbImhlcm9CdG4xIiwiTGVmdCBidXR0b24iXSxb"
    "Imhlcm9CdG4yIiwiUmlnaHQgYnV0dG9uIl1dXSwKIFsiRmVhdHVyZSBiYWRnZXMiLFtbImZlYXQx"
    "IiwiQmFkZ2UgMSB2YWx1ZSJdLFsiZmVhdDFzIiwiQmFkZ2UgMSBjYXB0aW9uIl0sWyJmZWF0MiIs"
    "IkJhZGdlIDIgdmFsdWUiXSxbImZlYXQycyIsIkJhZGdlIDIgY2FwdGlvbiJdLFsiZmVhdDMiLCJC"
    "YWRnZSAzIHZhbHVlIl0sWyJmZWF0M3MiLCJCYWRnZSAzIGNhcHRpb24iXV1dLAogWyJGbGFzaCBz"
    "YWxlIHN0cmlwIixbWyJmbGFzaFBpbGwiLCJQaWxsIHRleHQiXSxbImZsYXNoVGV4dCIsIkNvdW50"
    "ZG93biBsYWJlbCJdLFsiZmxhc2hCdG4iLCJCdXR0b24iXV1dLAogWyJSb3RhdGluZyBkZWFscyBj"
    "YXJkIixbWyJ3aGVlbEtpY2siLCJTbWFsbCBsYWJlbCJdLFsid2hlZWxUaXRsZSIsIkhlYWRsaW5l"
    "Il0sWyJ3aGVlbFdvcmRzIiwiTWFycXVlZSB3b3JkcyAoY29tbWEgc2VwYXJhdGVkKSJdXV0sCiBb"
    "IlNob3AgbGlzdCIsW1sic2hvcEhlYWQiLCJTZWN0aW9uIGhlYWRpbmciXSxbInNob3BTb3J0Iiwi"
    "U29ydCBsaW5rIl0sWyJzZWFyY2hQaCIsIlNlYXJjaCBwbGFjZWhvbGRlciJdLFsiYWxlcnRzQnRu"
    "IiwiQWxlcnRzIGJ1dHRvbiJdXV0sCiBbIldhbGxldCBzY3JlZW4iLFtbIndhbEtpY2siLCJMYWJl"
    "bCBhYm92ZSBiYWxhbmNlIl0sWyJ3YWxTdWIiLCJCYWxhbmNlIGRlc2NyaXB0aW9uIl0sWyJ3YWxB"
    "ZGQiLCJBZGQgZnVuZHMgYnV0dG9uIl0sWyJwYXlUaXRsZSIsIlBheW1lbnQgY2FyZCB0aXRsZSJd"
    "LFsicGF5U3ViIiwiUGF5bWVudCBjYXJkIGRlc2NyaXB0aW9uIl1dXSwKIFsiUGF5bWVudCBzaGVl"
    "dCIsW1sic2hlZXRUaXRsZSIsIlNoZWV0IHRpdGxlIHByZWZpeCJdLFsic2hlZXRTdWIiLCJTaGVl"
    "dCBkZXNjcmlwdGlvbiAoe219ID0gbWludXRlcykiXSxbInBheUJ0biIsIlBheSBidXR0b24iXSxb"
    "InZlcmlmeUJ0biIsIlZlcmlmeSBidXR0b24iXSxbInV0ckJ0biIsIk1hbnVhbCBmYWxsYmFjayBi"
    "dXR0b24iXSxbInBheVByZWZpeCIsIlJlZmVyZW5jZSBwcmVmaXgiXSxbInBheVdpbmRvdyIsIlBh"
    "eW1lbnQgd2luZG93IChtaW51dGVzKSJdXV0sCiBbIlBheW1lbnQgc3RlcHMiLFtbInN0ZXAxIiwi"
    "U3RlcCAxIl0sWyJzdGVwMiIsIlN0ZXAgMiJdLFsic3RlcDMiLCJTdGVwIDMiXSxbInN0ZXA0Iiwi"
    "U3RlcCA0Il1dXSwKIFsiUGF5bWVudCBtZXNzYWdlcyIsW1sibXNnQ2hlY2tpbmciLCJXaGlsZSBj"
    "aGVja2luZyJdLFsibXNnT2siLCJWZXJpZmllZCJdLFsibXNnTm9uZSIsIk5vdGhpbmcgZm91bmQi"
    "XSxbIm1zZ0V4cGlyZWQiLCJFeHBpcmVkIl1dXSwKIFsiQm90dG9tIG5hdmlnYXRpb24iLFtbIm5h"
    "dlNob3AiLCJUYWIgMSJdLFsibmF2T3JkZXJzIiwiVGFiIDIiXSxbIm5hdkVhcm4iLCJUYWIgMyJd"
    "LFsibmF2V2FsbGV0IiwiVGFiIDQiXSxbIm5hdllvdSIsIlRhYiA1Il1dXQpdOwovKiDilZDilZAg"
    "RkVBVFVSRSBGTEFHUyDigJQgZXZlcnkgZmVhdHVyZSBiZWxvdyBpcyBvZmYtYWJsZTsgd2hlbiBv"
    "ZmYgbm90aGluZyByZW5kZXJzIOKVkOKVkCAqLwpjb25zdCBGR1JPVVBTPVsKIFsiUGF5bWVudHMg"
    "JiB3YWxsZXQiLFsKICBbImNyeXB0byIsIkNyeXB0byAvIFVTRFQgdG9wLXVwIiwiQWNjZXB0IFVT"
    "RFQtVFJDMjAsIEJUQyBldGMuIHdpdGggYXV0by1jb25maXJtIGZyb20gdGhlIGV4cGxvcmVyLiJd"
    "LAogIFsidXBpUm90YXRlIiwiTXVsdGlwbGUgVVBJIElEcyArIHJvdGF0aW9uIiwiUm90YXRlIGhh"
    "bmRsZXMgcGVyIHBheW1lbnQgb3IgYXV0by1zd2l0Y2ggd2hlbiBvbmUgaXMgbGltaXRlZC4iXSwK"
    "ICBbImF1dG9SZWZ1bmQiLCJBdXRvLXJlZnVuZCB0byB3YWxsZXQiLCJEZWFkIGFjY291bnQgaW5z"
    "aWRlIHRoZSB3YXJyYW50eSB3aW5kb3cgaXMgcmVmdW5kZWQgd2l0aG91dCBzdXBwb3J0LiJdLAog"
    "IFsic3BsaXRQYXkiLCJTcGxpdCBwYXltZW50IiwiUGF5IHBhcnQgZnJvbSBiYWxhbmNlIGFuZCB0"
    "aGUgcmVzdCBieSBVUEkgaW4gb25lIGZsb3cuIl0sCiAgWyJsZWRnZXIiLCJXYWxsZXQgbGVkZ2Vy"
    "IHN0YXRlbWVudCIsIkZ1bGwgY3JlZGl0L2RlYml0IGhpc3Rvcnkgd2l0aCBydW5uaW5nIGJhbGFu"
    "Y2UgYW5kIENTViBleHBvcnQuIl0sCiAgWyJkZXBMaW1pdHMiLCJNaW4gLyBtYXggZGVwb3NpdCBy"
    "dWxlcyIsIkdsb2JhbCBvciBwZXItdXNlciBjYXBzIG9uIGEgc2luZ2xlIHRvcC11cC4iXSwKICBb"
    "ImRlcEJvbnVzIiwiRGVwb3NpdCBib251cyB0aWVycyIsIkFkZCDigrk1MDAgZ2V0IDUlIGV4dHJh"
    "IOKAlCBjb25maWd1cmFibGUgc2xhYnMsIGF1dG8tYXBwbGllZC4iXSwKICBbIndyb25nQW10Iiwi"
    "V3JvbmctYW1vdW50IGhhbmRsaW5nIiwiQ3JlZGl0IHdoYXRldmVyIHdhcyBhY3R1YWxseSBwYWlk"
    "IGFuZCBmbGFnIHRoZSBtaXNtYXRjaC4iXV1dLAogWyJDYXRhbG9nICYgc2VsbGluZyIsWwogIFsi"
    "Y2FydCIsIlNob3BwaW5nIGNhcnQgLyBidWxrIGJ1eSIsIk1peCBjb3VudHJpZXMgaW4gb25lIGNo"
    "ZWNrb3V0LCBvbmUgY29uZmlybWF0aW9uLCBvbmUgZGViaXQuIl0sCiAgWyJidWxrRGlzYyIsIkJ1"
    "bGsgcHVyY2hhc2UgZGlzY291bnQiLCJBdXRvbWF0aWMgcHJpY2UgYnJlYWsgYXQgNSAvIDEwIC8g"
    "MjUgdW5pdHMuIl0sCiAgWyJ3aXNobGlzdCIsIldpc2hsaXN0IC8gZmF2b3VyaXRlcyIsIlNhdmUg"
    "Y291bnRyaWVzIGFuZCByZWJ1eSBpbiBvbmUgdGFwLiJdLAogIFsiZHluUHJpY2UiLCJEeW5hbWlj"
    "IHByaWNpbmciLCJQcmljZSBtb3ZlcyB3aXRoIHN0b2NrIGxldmVsLCBhZ2UgdGllciBvciB0aW1l"
    "IG9mIGRheS4iXSwKICBbImZsYXNoU2FsZSIsIkZsYXNoIHNhbGUiLCJUaW1lZCBkaXNjb3VudCB3"
    "aXRoIGEgbGl2ZSBjb3VudGRvd24gb24gdGhlIHN0b3JlZnJvbnQuIl0sCiAgWyJjb3Vwb25zIiwi"
    "Q291cG9uIGNvZGVzIiwiUGVyY2VudGFnZSBvZmYgYXQgY2hlY2tvdXQgd2l0aCBsaW1pdHMgYW5k"
    "IGV4cGlyeS4iXSwKICBbInZhcmlhbnRzIiwiQWNjb3VudCB2YXJpYW50cyIsIlNwYW0tYmxvY2ss"
    "IDJGQSwgdXNlcm5hbWUsIG1haWwg4oCUIGZpbHRlcmFibGUgYW5kIHByaWNlZCBhcGFydC4iXSwK"
    "ICBbImhlYWx0aENoZWNrIiwiU3RvY2sgaGVhbHRoIGNoZWNrIiwiU2Vzc2lvbiBpcyB2YWxpZGF0"
    "ZWQgbGl2ZSBiZWZvcmUgaXQgaXMgc29sZC4iXV1dLAogWyJPcmRlcnMgJiBhZnRlci1zYWxlIixb"
    "CiAgWyJ0aW1lbGluZSIsIk9yZGVyIHN0YXR1cyB0aW1lbGluZSIsIlBlbmRpbmcsIGRlbGl2ZXJl"
    "ZCwgT1RQIHJlY2VpdmVkLCBjbG9zZWQg4oCUIHZpc2libGUgdG8gYm90aCBzaWRlcy4iXSwKICBb"
    "IndhcnJhbnR5IiwiUmVwbGFjZW1lbnQgLyB3YXJyYW50eSIsIlJlcG9ydCBhIHByb2JsZW0gaW5z"
    "aWRlIFggaG91cnMgZm9yIGFuIGF1dG8tcmVwbGFjZW1lbnQuIl0sCiAgWyJyZWRvd25sb2FkIiwi"
    "UmUtZG93bmxvYWQgZGV0YWlscyIsIkZldGNoIGEgcGFzdCBvcmRlcidzIGRldGFpbHMgYWdhaW4s"
    "IHJhdGUtbGltaXRlZC4iXSwKICBbImRpc3B1dGUiLCJEaXNwdXRlIGZsb3ciLCJSZWFzb24gcGlj"
    "a2VyIHBsdXMgZXZpZGVuY2UgaW5zdGVhZCBvZiBmcmVlLXRleHQgdGlja2V0cy4iXSwKICBbInNl"
    "c3Npb25GaWxlIiwiU2Vzc2lvbiBmaWxlIGRlbGl2ZXJ5IiwiU2VuZCAuc2Vzc2lvbiAvIEpTT04g"
    "YXMgYSBkb2N1bWVudCB3aXRoIGEgc2VsZi1kZXN0cnVjdCB0aW1lci4iXSwKICBbInJlY2VpcHRz"
    "IiwiT3JkZXIgcmVjZWlwdHMiLCJBdXRvLWdlbmVyYXRlZCBpbnZvaWNlIHBlciBwdXJjaGFzZS4i"
    "XV1dLAogWyJUcnVzdCAmIHNvY2lhbCBwcm9vZiIsWwogIFsicmV2aWV3cyIsIlJldmlld3MgJiBz"
    "dGFyIHJhdGluZ3MiLCJCdXllciByYXRlcyBhZnRlciBkZWxpdmVyeTsgYXZlcmFnZXMgc2hvdyBv"
    "biBldmVyeSBjb3VudHJ5LiJdLAogIFsic2FsZXNGZWVkIiwiTGl2ZSBzYWxlcyBmZWVkIiwiUmVj"
    "ZW50IHNhbGVzIHNob3duIGluLWFwcCBhbmQgYXV0by1wb3N0ZWQgdG8geW91ciBjaGFubmVsLiJd"
    "LAogIFsibGVhZGVyYm9hcmQiLCJMZWFkZXJib2FyZCIsIlRvcCBidXllcnMgYW5kIHJlZmVycmVy"
    "cyBvZiB0aGUgd2Vlaywgd2l0aCBhIHByaXplLiJdLAogIFsiYnV5ZXJCYWRnZSIsIlZlcmlmaWVk"
    "IGJ1eWVyIGJhZGdlIiwiQmFkZ2UgYW5kIHBlcmtzIGFmdGVyIE4gY29tcGxldGVkIG9yZGVycy4i"
    "XV1dLAogWyJHcm93dGggJiByZXRlbnRpb24iLFsKICBbInJlZkwyIiwiTXVsdGktbGV2ZWwgcmVm"
    "ZXJyYWwiLCJMZXZlbCAxIGFuZCBsZXZlbCAyIGNvbW1pc3Npb24gaW5zdGVhZCBvZiBhIGZsYXQg"
    "cmF0ZS4iXSwKICBbInJlZkRhc2giLCJSZWZlcnJhbCBkYXNoYm9hcmQiLCJXaG8gam9pbmVkLCB3"
    "aG8gZGVwb3NpdGVkIGFuZCB3aGF0IGVhY2ggaW52aXRlIGVhcm5lZC4iXSwKICBbInN0cmVhayIs"
    "IkNoZWNrLWluIHN0cmVhayByZXdhcmRzIiwiRXNjYWxhdGluZyBkYWlseSByZXdhcmQgdGhhdCBy"
    "ZXNldHMgd2hlbiBicm9rZW4uIl0sCiAgWyJsb3lhbHR5IiwiTG95YWx0eSBwb2ludHMiLCJFYXJu"
    "IHBlciDigrkgc3BlbnQsIHJlZGVlbSBmb3IgYmFsYW5jZSBvciBhIGZyZWUgYWNjb3VudC4iXSwK"
    "ICBbIndpbmJhY2siLCJSZS1lbmdhZ2VtZW50IERNcyIsIkF1dG8gbWVzc2FnZSB1c2VycyBpbmFj"
    "dGl2ZSBmb3IgNyAvIDMwIGRheXMuIl0sCiAgWyJhYmFuZG9uUmVtaW5kIiwiQWJhbmRvbmVkLWRl"
    "cG9zaXQgcmVtaW5kZXIiLCJOdWRnZSBiZWZvcmUgYW4gdW5maW5pc2hlZCBwYXltZW50IGV4cGly"
    "ZXMuIl0sCiAgWyJzY2hlZEJyb2FkY2FzdCIsIlNjaGVkdWxlZCAmIHNlZ21lbnRlZCBicm9hZGNh"
    "c3RzIiwiUXVldWUgZm9yIGxhdGVyLCB0YXJnZXQgYnV5ZXJzLCBub24tYnV5ZXJzIG9yIGJpZyBz"
    "cGVuZGVycy4iXV1dLAogWyJBZG1pbiAmIG9wZXJhdGlvbnMiLFsKICBbImFkbWluUm9sZXMiLCJB"
    "ZG1pbiByb2xlcyIsIk93bmVyIC8gbWFuYWdlciAvIHN1cHBvcnQgd2l0aCBzZXBhcmF0ZSBwZXJt"
    "aXNzaW9ucy4iXSwKICBbImF1ZGl0TG9nIiwiQXVkaXQgbG9nIiwiRXZlcnkgYWRtaW4gYWN0aW9u"
    "IGxvZ2dlZCB3aXRoIHRpbWVzdGFtcCBhbmQgdW5kby4iXSwKICBbImNvbnRlbnRFZGl0b3IiLCJJ"
    "bi1ib3QgY29udGVudCBlZGl0b3IiLCJFZGl0IGV2ZXJ5IG1lc3NhZ2UgYW5kIGJ1dHRvbiB3aXRo"
    "b3V0IHRvdWNoaW5nIGNvZGUuIl0sCiAgWyJhdXRvQmFja3VwIiwiU2NoZWR1bGVkIGF1dG8tYmFj"
    "a3VwIiwiRGF0YWJhc2UgdG8gYSBwcml2YXRlIGNoYW5uZWwgZXZlcnkgTiBob3Vycywgb25lLXRh"
    "cCByZXN0b3JlLiJdLAogIFsiYnVsa1VwbG9hZCIsIkJ1bGsgdXBsb2FkIHdpdGggdmFsaWRhdGlv"
    "biIsIlZhbGlkYXRlIGVhY2ggc2Vzc2lvbiwgcmVwb3J0IGRlYWQgb3IgZHVwbGljYXRlLCBpbXBv"
    "cnQgdGhlIHJlc3QuIl0sCiAgWyJkdXBEZXRlY3QiLCJEdXBsaWNhdGUgZGV0ZWN0aW9uIiwiUmVq"
    "ZWN0IGEgcGhvbmUgb3Igc2Vzc2lvbiBhbHJlYWR5IGluIHRoZSBkYXRhYmFzZS4iXSwKICBbImxv"
    "d1N0b2NrIiwiTG93LXN0b2NrIGFsZXJ0cyIsIlBpbmcgd2hlbiBhIGNvdW50cnkgZHJvcHMgYmVs"
    "b3cgaXRzIG93biB0aHJlc2hvbGQuIl0sCiAgWyJzZWxsSG91cnMiLCJTZWxsaW5nLWhvdXJzIHNj"
    "aGVkdWxlIiwiQXV0by1oaWRlIGNvdW50cmllcyBvdXRzaWRlIHRoZWlyIHNlbGxpbmcgd2luZG93"
    "LiJdLAogIFsic2FuZGJveCIsIlRlc3QgbW9kZSAvIHNhbmRib3giLCJSdW4gdGhlIHdob2xlIGZs"
    "b3cgb24gZmFrZSBtb25leSBiZWZvcmUgZ29pbmcgbGl2ZS4iXSwKICBbImkxOG4iLCJNdWx0aS1s"
    "YW5ndWFnZSIsIlBlci11c2VyIEhpbmRpIC8gRW5nbGlzaCBzd2l0Y2ggYWNyb3NzIGV2ZXJ5IHN0"
    "cmluZy4iXV1dLAogWyJTZWN1cml0eSAmIGFudGktYWJ1c2UiLFsKICBbInJhdGVMaW1pdCIsIlJh"
    "dGUgbGltaXRpbmciLCJQZXItdXNlciBhY3Rpb24gbGltaXRzIHNvIG5vYm9keSBjYW4gaGFtbWVy"
    "IHRoZSBib3QuIl0sCiAgWyJtdWx0aUFjYyIsIk11bHRpLWFjY291bnQgZGV0ZWN0aW9uIiwiQ2F0"
    "Y2ggcmVmZXJyYWwgZmFybWluZyBhbmQgZmxhZyBzdXNwaWNpb3VzIHNpZ251cHMuIl0sCiAgWyJ2"
    "ZWxvY2l0eSIsIlB1cmNoYXNlIHZlbG9jaXR5IGFsZXJ0cyIsIkZsYWcgYWJub3JtYWxseSBmYXN0"
    "IGJ1eWluZyDigJQgdHlwaWNhbCBvZiBzdG9sZW4gdG9wLXVwcy4iXSwKICBbImFkbWluMmZhIiwi"
    "QWRtaW4gMkZBIiwiUElOIG9yIFRPVFAgYmVmb3JlIGJhbGFuY2UgZWRpdHMsIGJyb2FkY2FzdHMg"
    "b3Igd2lwZXMuIl0sCiAgWyJhdXRvQmFuIiwiQXV0by1iYW4gcnVsZXMiLCJCYW4gb24gY2hhcmdl"
    "YmFjaywgZmFrZSBVVFIgb3IgcmVwZWF0ZWQgZmFpbGVkIHZlcmlmaWNhdGlvbi4iXSwKICBbImJs"
    "YWNrbGlzdCIsIkJsYWNrbGlzdCIsIlBlcm1hbmVudGx5IGJsb2NrIHVzZXIgSURzLCBwaG9uZSBu"
    "dW1iZXJzIGFuZCBVUEkgcmVmZXJlbmNlcy4iXSwKICBbImVuY3J5cHQiLCJTZXNzaW9uIGVuY3J5"
    "cHRpb24gYXQgcmVzdCIsIkEgbGVha2VkIGRhdGFiYXNlIGZpbGUgaXMgdXNlbGVzcyB3aXRob3V0"
    "IHRoZSBrZXkuIl1dXSwKIFsiQW5hbHl0aWNzIixbCiAgWyJyZXZDaGFydCIsIlJldmVudWUgZGFz"
    "aGJvYXJkIiwiRGFpbHksIHdlZWtseSBhbmQgbW9udGhseSByZXZlbnVlIGFuZCBwcm9maXQgYXMg"
    "Y2hhcnRzLiJdLAogIFsiZnVubmVsIiwiQ29udmVyc2lvbiBmdW5uZWwiLCJTZWUgd2hlcmUgdXNl"
    "cnMgZHJvcCBvZmYgYmV0d2VlbiBqb2luaW5nIGFuZCBidXlpbmcuIl0sCiAgWyJwcm9maXRSZXAi"
    "LCJQZXItY291bnRyeSBwcm9maXQiLCJDb3N0IHZzIHNhbGUgcHJpY2UgdnMgdm9sdW1lIGZvciBl"
    "dmVyeSBjb3VudHJ5LiJdLAogIFsiY2x2IiwiQ3VzdG9tZXIgbGlmZXRpbWUgdmFsdWUiLCJTcGVu"
    "ZCwgZnJlcXVlbmN5IGFuZCBjaHVybiByaXNrIHBlciB1c2VyLiJdLAogIFsiZGlnZXN0IiwiRGFp"
    "bHkgZGlnZXN0IiwiRW5kLW9mLWRheSBzdW1tYXJ5IHB1c2hlZCB0byB0aGUgYWRtaW4gY2hhdC4i"
    "XV1dLAogWyJRdWFsaXR5IG9mIGxpZmUiLFsKICBbImlubGluZVNlYXJjaCIsIklubGluZSBzZWFy"
    "Y2giLCJUeXBlIEB5b3VyYm90IGluZGlhIGluIGFueSBjaGF0IHRvIGZpbmQgYW5kIGJ1eS4iXSwK"
    "ICBbInNsYXNoQ21kcyIsIlNsYXNoIGNvbW1hbmRzIG1lbnUiLCIvc3RhcnQgL2J1eSAvYmFsYW5j"
    "ZSAvb3JkZXJzIHJlZ2lzdGVyZWQgd2l0aCBUZWxlZ3JhbS4iXSwKICBbInN0b2NrQ291bnRlciIs"
    "IkxpdmUgc3RvY2sgY291bnRlciIsIkF1dG8tdXBkYXRpbmcgcGlubmVkIHBvc3QgYW5kIGluLWFw"
    "cCBsaXZlIGNvdW50LiJdLAogIFsib25ib2FyZGluZyIsIk9uYm9hcmRpbmcgdG91ciIsIlRocmVl"
    "LXN0ZXAgd2Fsa3Rocm91Z2ggZm9yIGZpcnN0LXRpbWUgdXNlcnMuIl0sCiAgWyJub3RpZlByZWZz"
    "IiwiTm90aWZpY2F0aW9uIHByZWZlcmVuY2VzIiwiVXNlcnMgcGljayB3aGljaCBhbGVydHMgdGhl"
    "eSByZWNlaXZlLiJdLAogIFsicHVzaE5vdGlmIiwiTWluaSBhcHAgcHVzaCIsIk1pbmkgYXBwIGFj"
    "dGlvbnMgbm90aWZ5IHRoZSB1c2VyIHRocm91Z2ggdGhlIGJvdC4iXV1dCl07CmNvbnN0IEZFQVQ9"
    "e307CkZHUk9VUFMuZm9yRWFjaCgoW2cscm93c10pPT5yb3dzLmZvckVhY2gocj0+RkVBVFtyWzBd"
    "XT10cnVlKSk7CmNvbnN0IEZFQVQwPU9iamVjdC5hc3NpZ24oe30sRkVBVCk7CmNvbnN0IEc9KGss"
    "aCk9PkZFQVRba10/aDoiIjsKY29uc3QgYW55Rj0oLi4ua3MpPT5rcy5zb21lKGs9PkZFQVRba10p"
    "OwpsZXQgQ0FSVD1bXSxXSVNIPVtdOwpsZXQgUkVWSUVXUz17SW5kaWE6WzQuOCw0MTJdLEluZG9u"
    "ZXNpYTpbNC42LDE4MF0sQnJhemlsOls0LjcsOTZdLE5pZ2VyaWE6WzQuNCw1NF0sVmlldG5hbTpb"
    "NC45LDIzM10sUGhpbGlwcGluZXM6WzQuNSw3MV19OwpsZXQgRkVFRD1bWyLwn4eu8J+HsyIsIklu"
    "ZGlhIiwi4oK5MzUiLCIyIG1pbiBhZ28iXSxbIvCfh7vwn4ezIiwiVmlldG5hbSIsIuKCuTI2Iiwi"
    "NiBtaW4gYWdvIl0sWyLwn4en8J+HtyIsIkJyYXppbCIsIuKCuTQ0IiwiMTEgbWluIGFnbyJdXTsK"
    "bGV0IEFETT17CiAga3BpOntyZXY6NDgyMCxvcmRlcnM6NjEsdXNlcnM6MTI4NCxhdXRvOjM4fSwK"
    "ICBiYXJzOls0Miw2MSwzOCw3NCw1NSw4OCw2N10sCiAgc3RvY2s6W10sb3JkZXJzOltdLGRpc3B1"
    "dGVzOltdLHVzZXJzOltdLGFkbWluczpbXSxjaGFubmVsczpbXSwKICBsb2dzOnttYXN0ZXI6dHJ1"
    "ZSxjaGFubmVsczpbXSxldmVudHM6W119LGNvdXBvbnM6W10sYXVkaXQ6W10scmV2aWV3czpbXSxk"
    "ZXBvc2l0czpbXSxsb2dmZWVkOltdLAogIHBheTp7dXBpOiIiLHBheWVlOiIiLGdtYWlsOiIiLGdt"
    "YWlsX3NldDpmYWxzZSx3aW5kb3c6NSxwcmVmaXg6IlBBWSIsbWluOjEwLG1heDoyMDAwMCxhdXRv"
    "ZGVwOjAscGVuZGluZzowfSwKICBzZWN1cml0eTp7cmF0ZToyMCx2ZWxvY2l0eTo1LGJsYWNrbGlz"
    "dDowLGJhbm5lZDowfSwKICBtYXJrZXQ6e2ZsYXNoOjIwLGZsYXNoX29uOmZhbHNlLHJlZjE6NSxy"
    "ZWYyOjEsZ29hbDowLG1vbnRoOjB9LAogIHNldHRpbmdzOntzdG9yZToiIix0YWdsaW5lOiIiLHN1"
    "cHBvcnQ6IiIscHJpY2U6MzUsZGFpbHk6MC40LGFwcDoiIixtYWludGVuYW5jZTpmYWxzZSxzYW5k"
    "Ym94OmZhbHNlfSwKICBhbmFseXRpY3M6e2Z1bm5lbDp7dXNlcnM6MCxkZXA6MCxidXk6MH0sY291"
    "bnRyeTpbXSxzdG9ja190b3RhbDowLHNvbGRfdG90YWw6MH0KfTsKZnVuY3Rpb24gYWRtTnVtKG4p"
    "e3JldHVybiAobnx8MCkudG9Mb2NhbGVTdHJpbmcoImVuLUlOIik7fQpsZXQgRUFSTj17c3RyZWFr"
    "OjQsZGFpbHk6MC40MCxjbGFpbWVkOmZhbHNlLHBvaW50czozNDAscG9pbnRzV29ydGg6MzQsaW52"
    "aXRlczoxMiwKICBsMTo1Mi40MCxsMjoxMi40MCxlYXJuZWQ6NjQuODAsbGluazoidC5tZS95b3Vy"
    "X2JvdD9zdGFydD1yZWZfZGVtbyIsCiAgcmVjZW50OltbInVzZXJfMTA0MSIsImRlcG9zaXRlZCDi"
    "grkyMDAiLCIr4oK5Mi4wMCJdLFsidXNlcl8xMDM4Iiwiam9pbmVkIiwi4oCUIl0sWyJ1c2VyXzEw"
    "MjIiLCJkZXBvc2l0ZWQg4oK5NTAwIiwiK+KCuTUuMDAiXV0sCiAgbGVhZGVyczpbWyIxIiwiQHJh"
    "aHVsIiwi4oK5NCw4MjAiXSxbIjIiLCJAYW1hbiIsIuKCuTMsMTQwIl0sWyIzIiwiQHlvdSIsIuKC"
    "uTEsOTgwIl1dLAogIHRhc2tzOltbIkpvaW4gdGhlIG1haW4gY2hhbm5lbCIsIuKCuTEuMDAiLDFd"
    "LFsiUGxhY2UgeW91ciBmaXJzdCBvcmRlciIsIuKCuTIuMDAiLDFdLFsiSW52aXRlIDMgZnJpZW5k"
    "cyIsIuKCuTUuMDAiLDBdXX07CmZ1bmN0aW9uIHN0YXJzKGMpe2NvbnN0IHI9KFJFVklFV1NbY118"
    "fFs0LjcsMjBdKVswXSxuPShSRVZJRVdTW2NdfHxbNC43LDIwXSlbMV07CiAgcmV0dXJuIGA8c3Bh"
    "biBjbGFzcz0ic3RhcnMiPiR7WzEsMiwzLDQsNV0ubWFwKGk9PmljKGk8PU1hdGgucm91bmQocik/"
    "InN0YXIiOiJzdGFyIikpLmpvaW4oIiIpfTxiPiR7cn0gKCR7bn0pPC9iPjwvc3Bhbj5gO30KZnVu"
    "Y3Rpb24gYXBwbHlGZWF0dXJlcygpewogIGNvbnN0IGNjPWRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJjYXJ0Y2hpcCIpOwogIGlmKGNjKXtjYy5jbGFzc0xpc3QudG9nZ2xlKCJoaWRkZW4iLCFGRUFU"
    "LmNhcnQpO2RvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjYXJ0biIpLnRleHRDb250ZW50PUNBUlQu"
    "bGVuZ3RoO30KICBjb25zdCBlYXJuPWFueUYoInN0cmVhayIsInJlZkRhc2giLCJyZWZMMiIsImxv"
    "eWFsdHkiLCJsZWFkZXJib2FyZCIsIndpbmJhY2siKTsKICBjb25zdCBlYj1kb2N1bWVudC5xdWVy"
    "eVNlbGVjdG9yKCcjdGFicyBidXR0b25bZGF0YS10YWI9ImVhcm4iXScpOwogIGlmKGViKXtlYi5j"
    "bGFzc0xpc3QudG9nZ2xlKCJoaWRkZW4iLCFlYXJuKTsKICAgIGlmKCFlYXJuJiZUQUI9PT0iZWFy"
    "biIpe1RBQj0ic2hvcCI7ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3RhYnMgYnV0dG9uIiku"
    "Zm9yRWFjaCh4PT54LmNsYXNzTGlzdC50b2dnbGUoIm9uIix4LmRhdGFzZXQudGFiPT09InNob3Ai"
    "KSk7cmVuZGVyKCk7fX0KICBjb25zdCBhbWFwPXttYXJrZXQ6YW55RigiY291cG9ucyIsImZsYXNo"
    "U2FsZSIsInNjaGVkQnJvYWRjYXN0Iiwid2luYmFjayIsInNhbGVzRmVlZCIsImxlYWRlcmJvYXJk"
    "IiwibG95YWx0eSIsInJlZkRhc2giLCJyZWZMMiIsImFiYW5kb25SZW1pbmQiKSwKICAgIGFuYWx5"
    "dGljczphbnlGKCJyZXZDaGFydCIsImZ1bm5lbCIsInByb2ZpdFJlcCIsImNsdiIsImRpZ2VzdCIp"
    "LAogICAgc2VjdXJpdHk6YW55RigicmF0ZUxpbWl0IiwibXVsdGlBY2MiLCJ2ZWxvY2l0eSIsImFk"
    "bWluMmZhIiwiYXV0b0JhbiIsImJsYWNrbGlzdCIsImVuY3J5cHQiKSwKICAgIGNvbnRlbnQ6RkVB"
    "VC5jb250ZW50RWRpdG9yfTsKICBPYmplY3QuZW50cmllcyhhbWFwKS5mb3JFYWNoKChbayx2XSk9"
    "Pntjb25zdCBiPWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoJyNhc3ViIGJ1dHRvbltkYXRhLWE9Iicr"
    "aysnIl0nKTsKICAgIGlmKGIpe2IuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZGVuIiwhdik7CiAgICAg"
    "IGlmKCF2JiZBVEFCPT09ayl7QVRBQj0iZGFzaCI7ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgi"
    "I2FzdWIgYnV0dG9uIikuZm9yRWFjaCh4PT54LmNsYXNzTGlzdC50b2dnbGUoIm9uIix4LmRhdGFz"
    "ZXQuYT09PSJkYXNoIikpO3JlbmRlckFkbWluKCk7fX19KTsKICBpZih3aW5kb3cuX19kb2NrUGxh"
    "Y2UpcmVxdWVzdEFuaW1hdGlvbkZyYW1lKCgpPT53aW5kb3cuX19kb2NrUGxhY2UodHJ1ZSkpOwog"
    "IGlmKHdpbmRvdy5fX3Z0YWJQbGFjZSlyZXF1ZXN0QW5pbWF0aW9uRnJhbWUoKCk9PndpbmRvdy5f"
    "X3Z0YWJQbGFjZSh0cnVlKSk7Cn0KZnVuY3Rpb24gYXBwbHlDTVMoKXsKIGNvbnN0IGc9aWQ9PmRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKTsKIGlmKGcoImJuYW1lIikpZygiYm5hbWUiKS50ZXh0"
    "Q29udGVudD1DTVMuc3RvcmVOYW1lOwogaWYoZygiYWJuYW1lIikpZygiYWJuYW1lIikudGV4dENv"
    "bnRlbnQ9Q01TLnN0b3JlTmFtZTsKIGlmKENNUy5zdG9yZU5hbWUpe0NGRy5uYW1lPUNNUy5zdG9y"
    "ZU5hbWU7fQogaWYoQ01TLnN0b3JlVGFnKUNGRy50YWc9Q01TLnN0b3JlVGFnOwogcGFpbnRCcmFu"
    "ZChDTVMuc3RvcmVOYW1lLENNUy5zdG9yZVRhZyk7CiB0cnl7CiAgIGlmKENNUy5zdG9yZU5hbWUp"
    "bG9jYWxTdG9yYWdlLnNldEl0ZW0oInN0b3JlTmFtZSIsQ01TLnN0b3JlTmFtZSk7CiAgIGlmKENN"
    "Uy5zdG9yZVRhZylsb2NhbFN0b3JhZ2Uuc2V0SXRlbSgic3RvcmVUYWciLENNUy5zdG9yZVRhZyk7"
    "CiB9Y2F0Y2goZSl7fQogaWYoZygiYnRhZyIpKWcoImJ0YWciKS50ZXh0Q29udGVudD1DTVMuc3Rv"
    "cmVUYWc7CiBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1uYXZdIikuZm9yRWFjaChu"
    "PT57CiAgIGNvbnN0IGs9Im5hdiIrbi5kYXRhc2V0Lm5hdlswXS50b1VwcGVyQ2FzZSgpK24uZGF0"
    "YXNldC5uYXYuc2xpY2UoMSk7CiAgIGlmKENNU1trXSluLnRleHRDb250ZW50PUNNU1trXTt9KTsK"
    "IGlmKHdpbmRvdy5fX2RvY2tQbGFjZSlyZXF1ZXN0QW5pbWF0aW9uRnJhbWUoKCk9PndpbmRvdy5f"
    "X2RvY2tQbGFjZSh0cnVlKSk7Cn0KbGV0IEJBTD0yNDguNTA7CmNvbnN0IFNUT0NLPVsKIHtjOiJJ"
    "bmRpYSIsZjoi8J+HrvCfh7MiLGNvZGU6Iis5MSIscHJpY2U6MzUsd2FzOjQ5LHF0eToxMjgsaG90"
    "OjF9LAoge2M6IkluZG9uZXNpYSIsZjoi8J+HrvCfh6kiLGNvZGU6Iis2MiIscHJpY2U6Mjgsd2Fz"
    "OjAscXR5OjY0fSwKIHtjOiJCcmF6aWwiLGY6IvCfh6fwn4e3Iixjb2RlOiIrNTUiLHByaWNlOjQ0"
    "LHdhczo1OSxxdHk6MjJ9LAoge2M6Ik5pZ2VyaWEiLGY6IvCfh7Pwn4esIixjb2RlOiIrMjM0Iixw"
    "cmljZTozMSx3YXM6MCxxdHk6MH0sCiB7YzoiVmlldG5hbSIsZjoi8J+Hu/Cfh7MiLGNvZGU6Iis4"
    "NCIscHJpY2U6MjYsd2FzOjAscXR5OjkxfSwKIHtjOiJQaGlsaXBwaW5lcyIsZjoi8J+HtfCfh60i"
    "LGNvZGU6Iis2MyIscHJpY2U6Mzgsd2FzOjAscXR5Ojd9Cl07CmNvbnN0IE9SREVSUz1bCiB7aWQ6"
    "Ik9SRC03NzQxIixjOiJJbmRpYSIsZjoi8J+HrvCfh7MiLG51bToiKzkxIOKAouKAouKAouKAoiDi"
    "gKLigKIzMiIsc3Q6IkRlbGl2ZXJlZCIsd2hlbjoiMiBtaW4gYWdvIixwcmljZTozNX0sCiB7aWQ6"
    "Ik9SRC03NzI4IixjOiJWaWV0bmFtIixmOiLwn4e78J+HsyIsbnVtOiIrODQg4oCi4oCi4oCi4oCi"
    "IOKAouKAojA3IixzdDoiRGVsaXZlcmVkIix3aGVuOiJZZXN0ZXJkYXkiLHByaWNlOjI2fSwKIHtp"
    "ZDoiT1JELTc3MDIiLGM6IkJyYXppbCIsZjoi8J+Hp/Cfh7ciLG51bToiKzU1IOKAouKAouKAouKA"
    "oiDigKLigKI4OCIsc3Q6IlJlZnVuZGVkIix3aGVuOiIzIGRheXMgYWdvIixwcmljZTo0NH0KXTsK"
    "bGV0IENIQU5ORUxTPVsKIHtpZDoxLG5hbWU6Ik1haW4gQ2hhbm5lbCIsaGFuZGxlOiJAeW91cl9t"
    "YWluX2NoYW5uZWwiLHJlcTp0cnVlLG9uOnRydWUsbWVtYmVyczoiMTIuNEsifSwKIHtpZDoyLG5h"
    "bWU6IkJhY2t1cCBDaGFubmVsIixoYW5kbGU6IkB5b3VyX2JhY2t1cCIscmVxOnRydWUsb246dHJ1"
    "ZSxtZW1iZXJzOiIzLjFLIn0sCiB7aWQ6MyxuYW1lOiJEaXNjdXNzaW9uIEdyb3VwIixoYW5kbGU6"
    "IkB5b3VyX2dyb3VwIixyZXE6ZmFsc2Usb246dHJ1ZSxtZW1iZXJzOiI1LjhLIn0KXTsKbGV0IExP"
    "R0NIPVsKIHtpZDoxLG5hbWU6Ik1haW4gTG9ncyIsaGFuZGxlOiJAeW91cl9sb2dzIixwcmltYXJ5"
    "OnRydWUsb246dHJ1ZX0sCiB7aWQ6MixuYW1lOiJQYXltZW50IExvZ3MiLGhhbmRsZToiQHlvdXJf"
    "cGF5X2xvZ3MiLHByaW1hcnk6ZmFsc2Usb246dHJ1ZX0sCiB7aWQ6MyxuYW1lOiJFcnJvciBMb2dz"
    "IixoYW5kbGU6Ii0xMDB4eHh4eHh4eHh4IixwcmltYXJ5OmZhbHNlLG9uOmZhbHNlfQpdOwpsZXQg"
    "TE9HU19NQVNURVI9dHJ1ZTsKbGV0IEVWRU5UUz1bCiB7azoiTmV3IHVzZXIgam9pbmVkIixvbjpm"
    "YWxzZSxjaDoxfSx7azoiRGVwb3NpdCAvIHBheW1lbnQiLG9uOnRydWUsY2g6Mn0sCiB7azoiUHVy"
    "Y2hhc2UiLG9uOnRydWUsY2g6MX0se2s6Ik9UUCByZXF1ZXN0ZWQiLG9uOmZhbHNlLGNoOjF9LAog"
    "e2s6IlN0b2NrIGFkZGVkIixvbjp0cnVlLGNoOjF9LHtrOiJBZG1pbiBhY3Rpb24iLG9uOnRydWUs"
    "Y2g6MX0sCiB7azoiRXJyb3JzICYgd2FybmluZ3MiLG9uOnRydWUsY2g6M30KXTsKY29uc3QgVFg9"
    "W3t0OiJEZXBvc2l0IixhOiIr4oK5MjAwLjAwIix3OiJUb2RheSwgMTQ6MDIifSx7dDoiUHVyY2hh"
    "c2UgwrcgSW5kaWEiLGE6Ii3igrkzNS4wMCIsdzoiVG9kYXksIDE0OjA1In0sCiB7dDoiRGFpbHkg"
    "Y2hlY2staW4iLGE6IivigrkwLjQwIix3OiJUb2RheSwgMDk6MTEifSx7dDoiUmVmZXJyYWwgYm9u"
    "dXMiLGE6IivigrkzLjIwIix3OiJZZXN0ZXJkYXkifV07CgpsZXQgRklMVEVSRUQ9U1RPQ0suc2xp"
    "Y2UoKSwgUVVFUlk9IiIsIENISVA9ImFsbCI7CmNvbnN0IEVNUFRZPSc8ZGl2IGNsYXNzPSJjYXJk"
    "IiBzdHlsZT0idGV4dC1hbGlnbjpjZW50ZXIiPjxwIGNsYXNzPSJzdWIiPk5vdGhpbmcgbWF0Y2hl"
    "cyB0aGF0IHNlYXJjaC48L3A+PC9kaXY+JzsKY29uc3QgSUM9e2JhZzoiPHN2ZyB2aWV3Qm94PVwi"
    "MCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Ut"
    "d2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJv"
    "dW5kXCI+PHBhdGggZD1cIk02IDIgMyA2djE0YTIgMiAwIDAgMCAyIDJoMTRhMiAyIDAgMCAwIDIt"
    "MlY2bC0zLTR6XCIvPjxwYXRoIGQ9XCJNMyA2aDE4XCIvPjxwYXRoIGQ9XCJNMTYgMTBhNCA0IDAg"
    "MCAxLTggMFwiLz48L3N2Zz4iLGJveDoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1c"
    "Im5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tl"
    "LWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0y"
    "MSA4YTIgMiAwIDAgMC0xLTEuNzNsLTctNGEyIDIgMCAwIDAtMiAwbC03IDRBMiAyIDAgMCAwIDMg"
    "OHY4YTIgMiAwIDAgMCAxIDEuNzNsNyA0YTIgMiAwIDAgMCAyIDBsNy00QTIgMiAwIDAgMCAyMSAx"
    "NlpcIi8+PHBhdGggZD1cIm0zLjMgNyA4LjcgNSA4LjctNVwiLz48cGF0aCBkPVwiTTEyIDIyVjEy"
    "XCIvPjwvc3ZnPiIsemFwOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwi"
    "IHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNh"
    "cD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwiTTEzIDIgMyAx"
    "NGg5bC0xIDggMTAtMTJoLTl6XCIvPjwvc3ZnPiIsd2FsbGV0OiI8c3ZnIHZpZXdCb3g9XCIwIDAg"
    "MjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0"
    "aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRc"
    "Ij48cGF0aCBkPVwiTTE5IDdWNWEyIDIgMCAwIDAtMi0ySDVhMiAyIDAgMCAwIDAgNGgxNWEyIDIg"
    "MCAwIDEgMiAydjlhMiAyIDAgMCAxLTIgMkg1YTIgMiAwIDAgMS0yLTJWNVwiLz48cGF0aCBkPVwi"
    "TTE4IDEyaC4wMVwiLz48L3N2Zz4iLHVzZXI6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZp"
    "bGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0"
    "cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9"
    "XCJNMTkgMjF2LTJhNCA0IDAgMCAwLTQtNEg5YTQgNCAwIDAgMC00IDR2MlwiLz48Y2lyY2xlIGN4"
    "PVwiMTJcIiBjeT1cIjdcIiByPVwiNFwiLz48L3N2Zz4iLHNoaWVsZDoiPHN2ZyB2aWV3Qm94PVwi"
    "MCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Ut"
    "d2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJv"
    "dW5kXCI+PHBhdGggZD1cIk0yMCAxM2MwIDUtMy41IDcuNS03LjY2IDguOTVhMSAxIDAgMCAxLS42"
    "Ny0uMDFDNy41IDIwLjUgNCAxOCA0IDEzVjZhMSAxIDAgMCAxIDEtMWMyIDAgNC41LTEuMiA2LjI0"
    "LTIuNzJhMS4xNyAxLjE3IDAgMCAxIDEuNTIgMEMxNC41MSAzLjgxIDE3IDUgMTkgNWExIDEgMCAw"
    "IDEgMSAxelwiLz48L3N2Zz4iLGNoZWNrOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxs"
    "PVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjIuNFwiIHN0"
    "cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9"
    "XCJNMjAgNiA5IDE3bC01LTVcIi8+PC9zdmc+IixzZW5kOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQg"
    "MjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1c"
    "IjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48"
    "cGF0aCBkPVwibTIyIDItNyAyMC00LTktOS00WlwiLz48cGF0aCBkPVwiTTIyIDIgMTEgMTNcIi8+"
    "PC9zdmc+IixwbHVzOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0"
    "cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjIuNFwiIHN0cm9rZS1saW5lY2Fw"
    "PVwicm91bmRcIj48cGF0aCBkPVwiTTEyIDV2MTRNNSAxMmgxNFwiLz48L3N2Zz4iLGNvcHk6Ijxz"
    "dmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENv"
    "bG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2Ut"
    "bGluZWpvaW49XCJyb3VuZFwiPjxyZWN0IHdpZHRoPVwiMTRcIiBoZWlnaHQ9XCIxNFwiIHg9XCI4"
    "XCIgeT1cIjhcIiByeD1cIjJcIi8+PHBhdGggZD1cIk00IDE2Yy0xLjEgMC0yLS45LTItMlY0YzAt"
    "MS4xLjktMiAyLTJoMTBjMS4xIDAgMiAuOSAyIDJcIi8+PC9zdmc+IixjaGV2OiI8c3ZnIHZpZXdC"
    "b3g9XCIwIDAgMjQgMjRcIiB3aWR0aD1cIjE2XCIgaGVpZ2h0PVwiMTZcIiBmaWxsPVwibm9uZVwi"
    "IHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNh"
    "cD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwibTkgMTggNi02"
    "LTYtNlwiLz48L3N2Zz4iLGZpcmU6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIHdpZHRoPVwi"
    "MTJcIiBoZWlnaHQ9XCIxMlwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIg"
    "c3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpv"
    "aW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTIgMmMxIDQgNSA1IDUgOWE1IDUgMCAwIDEtMTAgMGMw"
    "LTIgMS0zIDItNCAwIDIgMSAzIDIgMyAwLTMtMS01IDEtOFpcIi8+PC9zdmc+Iixkb3duOiI8c3Zn"
    "IHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xv"
    "clwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxp"
    "bmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwiTTEyIDV2MTRNMTkgMTJsLTcgNy03LTdcIi8+PC9z"
    "dmc+Iix1cDoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9"
    "XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3Vu"
    "ZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0xMiAxOVY1TTUgMTJsNy03"
    "IDcgN1wiLz48L3N2Zz4iLHNlYXJjaDoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1c"
    "Im5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tl"
    "LWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PGNpcmNsZSBjeD1c"
    "IjExXCIgY3k9XCIxMVwiIHI9XCI3XCIvPjxwYXRoIGQ9XCJtMjEgMjEtNC4zLTQuM1wiLz48L3N2"
    "Zz4iLHN0YXI6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJjdXJyZW50Q29sb3Jc"
    "IiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIxLjVcIiBzdHJva2UtbGlu"
    "ZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJtMTIgMyAyLjYgNS4zIDUuOS45LTQuMyA0LjEgMSA1"
    "LjgtNS4yLTIuNy01LjIgMi43IDEtNS44TDMuNSA5LjJsNS45LS45elwiLz48L3N2Zz4ifTsKY29u"
    "c3QgaWM9bj0+SUNbbl18fCIiOwpjb25zdCBlc2M9cz0+U3RyaW5nKHMpLnJlcGxhY2UoL1smPD4i"
    "XS9nLGM9Pih7IiYiOiImYW1wOyIsIjwiOiImbHQ7IiwiPiI6IiZndDsiLCciJzoiJnF1b3Q7In1b"
    "Y10pKTsKY29uc3QgbW9uZXk9bj0+Q0ZHLmN1citOdW1iZXIobikudG9GaXhlZCgyKTsKCmxldCB0"
    "dDtjb25zdCB0b2FzdD1tPT57Y29uc3QgZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgidG9hc3Qi"
    "KTtlLnRleHRDb250ZW50PW07ZS5jbGFzc0xpc3QuYWRkKCJzaG93Iik7Y2xlYXJUaW1lb3V0KHR0"
    "KTt0dD1zZXRUaW1lb3V0KCgpPT5lLmNsYXNzTGlzdC5yZW1vdmUoInNob3ciKSwxOTAwKX07CmRv"
    "Y3VtZW50LmFkZEV2ZW50TGlzdGVuZXIoImNsaWNrIixlPT57Y29uc3QgYj1lLnRhcmdldC5jbG9z"
    "ZXN0KCJbZGF0YS10b2FzdF0iKTtpZihiKXRvYXN0KGIuZGF0YXNldC50b2FzdCl9KTsKY29uc3Qg"
    "c2hlZXQ9aD0+ewogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJwYW5lbCIpLmlubmVySFRNTD0n"
    "PGRpdiBjbGFzcz0iZ3JhYiI+PC9kaXY+JytoOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJz"
    "aGVldCIpLmNsYXNzTGlzdC5hZGQoIm9wZW4iKTsKICAvKiBjb250cm9scyBpbnNpZGUgYSBzaGVl"
    "dCBhcmUgY3JlYXRlZCBhZnRlciBiaW5kaW5nLCBzbyBiaW5kIHRoZW0gbm93ICovCiAgdHJ5eyBp"
    "Zih0eXBlb2YgYmluZENvbW1vbj09PSJmdW5jdGlvbiIpIGJpbmRDb21tb24oKTsgfWNhdGNoKGUp"
    "e30KICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjc2hlZXQgW2RhdGEtY2xvc2VdIikuZm9y"
    "RWFjaCh4PT54Lm9uY2xpY2s9Y2xvc2VTaGVldCk7Cn07CmNvbnN0IGNsb3NlU2hlZXQ9KCk9PmRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJzaGVldCIpLmNsYXNzTGlzdC5yZW1vdmUoIm9wZW4iKTsK"
    "ZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigiY2xpY2siLGU9PntpZihlLnRhcmdldC5jbG9zZXN0"
    "KCJbZGF0YS1jbG9zZV0iKSljbG9zZVNoZWV0KCl9KTsKCmRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JB"
    "bGwoIiNzdyBidXR0b24iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogIGlmKGIuZGF0YXNl"
    "dC5zdz09PSJhZG1pbiImJiFhZG1pbkFsbG93ZWQoKSl7dG9hc3QoIkFkbWlucyBvbmx5Iik7cmV0"
    "dXJuO30KICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjc3cgYnV0dG9uIikuZm9yRWFjaCh4"
    "PT54LmNsYXNzTGlzdC5yZW1vdmUoIm9uIikpO2IuY2xhc3NMaXN0LmFkZCgib24iKTsKICBbImFw"
    "cCIsImFkbWluIl0uZm9yRWFjaCh2PT5kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgidmlldy0iK3Yp"
    "LmNsYXNzTGlzdC50b2dnbGUoImhpZGRlbiIsdiE9PWIuZGF0YXNldC5zdykpOwogIGRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJ0YWJzIikuc3R5bGUuZGlzcGxheT1iLmRhdGFzZXQuc3c9PT0iYXBw"
    "Ij8iIjoibm9uZSI7d2luZG93LnNjcm9sbFRvKDAsMCk7CiAgcmVxdWVzdEFuaW1hdGlvbkZyYW1l"
    "KCgpPT57aWYod2luZG93Ll9fZG9ja1BsYWNlKXdpbmRvdy5fX2RvY2tQbGFjZSh0cnVlKTsKICAg"
    "IGlmKHdpbmRvdy5fX3Z0YWJQbGFjZSl3aW5kb3cuX192dGFiUGxhY2UodHJ1ZSk7fSk7fSk7Cgps"
    "ZXQgVEFCPSJzaG9wIjsKZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3RhYnMgYnV0dG9uIiku"
    "Zm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIj"
    "dGFicyBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnJlbW92ZSgib24iKSk7Yi5jbGFz"
    "c0xpc3QuYWRkKCJvbiIpOwogIFRBQj1iLmRhdGFzZXQudGFiO3JlbmRlcigpO3dpbmRvdy5zY3Jv"
    "bGxUbygwLDApO2lmKHdpbmRvdy5fX2RvY2tQbGFjZSlyZXF1ZXN0QW5pbWF0aW9uRnJhbWUod2lu"
    "ZG93Ll9fZG9ja1BsYWNlKTt9KTsKZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigiY2xpY2siLGU9"
    "Pntjb25zdCBnPWUudGFyZ2V0LmNsb3Nlc3QoIltkYXRhLWdvXSIpO2lmKCFnKXJldHVybjsKICBj"
    "b25zdCB0PWcuZGF0YXNldC5nbztkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjdGFicyBidXR0"
    "b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnRvZ2dsZSgib24iLHguZGF0YXNldC50YWI9PT10"
    "KSk7CiAgVEFCPXQ7cmVuZGVyKCk7d2luZG93LnNjcm9sbFRvKDAsMCk7fSk7CgpmdW5jdGlvbiBy"
    "ZW5kZXIoKXtjb25zdCBzdD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RhZ2UiKTsKICBpZihM"
    "T0NLRUQpe3BhaW50TG9ja2VkKCk7cmV0dXJuO30KICBzdC5pbm5lckhUTUw9JzxkaXYgY2xhc3M9"
    "InNrIj48L2Rpdj48ZGl2IGNsYXNzPSJzayI+PC9kaXY+PGRpdiBjbGFzcz0ic2siIHN0eWxlPSJo"
    "ZWlnaHQ6MTQwcHgiPjwvZGl2Pic7CiAgc2V0VGltZW91dCgoKT0+e2lmKExPQ0tFRCl7cGFpbnRM"
    "b2NrZWQoKTtyZXR1cm47fQogICAgc3QuaW5uZXJIVE1MPSh7c2hvcDp2U2hvcCxvcmRlcnM6dk9y"
    "ZGVycyxlYXJuOnZFYXJuLHdhbGxldDp2V2FsbGV0LHlvdTp2WW91fSlbVEFCXSgpOwogICAgYmlu"
    "ZFN0YWdlKCk7cmV2ZWFsU3RhZ2Uoc3QpO2JpbmRUaWx0KCk7fSxSTT8wOjE2MCk7fQoKZnVuY3Rp"
    "b24gdlNob3AoKXsKICBjb25zdCBsaXZlPVNUT0NLLmZpbHRlcihzPT5zLnF0eT4wKS5sZW5ndGgs"
    "IHVuaXRzPVNUT0NLLnJlZHVjZSgoYSxiKT0+YStiLnF0eSwwKTsKICBpZihMSVZFJiYhU1RPQ0su"
    "bGVuZ3RoKXsKICAgIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCIgc3R5bGU9Im1hcmdpbjoxOHB4"
    "IDA7dGV4dC1hbGlnbjpjZW50ZXI7cGFkZGluZzoyNnB4Ij4KICAgICAgPGgzIHN0eWxlPSJtYXJn"
    "aW46MCAwIDZweCI+JHtlc2MoQ01TLmVtcHR5VGl0bGV8fCJSZXN0b2NraW5nIil9PC9oMz4KICAg"
    "ICAgPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj4ke2VzYyhDTVMuZW1wdHlCb2R5fHwi"
    "RXZlcnl0aGluZyBqdXN0IHNvbGQgb3V0LiBGcmVzaCBhY2NvdW50cyBsYW5kIGhlcmUgc2hvcnRs"
    "eSBcdTIwMTQgdHVybiBvbiBhbiBhbGVydCBhbmQgeW91IHdpbGwgaGVhciBmaXJzdC4iKX08L3A+"
    "CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTRweCIg"
    "ZGF0YS1hY3Q9ImFsZXJ0cyI+Tm90aWZ5IG1lPC9idXR0b24+PC9kaXY+YDsKICB9CiAgcmV0dXJu"
    "IGAKICA8ZGl2IGNsYXNzPSJiYW5uZXIiPjxkaXYgY2xhc3M9InRyYWNrIj4KICAgICR7WzEsMl0u"
    "bWFwKCgpPT5gPHNwYW4+JHtpYygiemFwIil9JHtDTVMudGljazF9PC9zcGFuPjxzcGFuPiR7aWMo"
    "IndhbGxldCIpfSR7Q01TLnRpY2syfTwvc3Bhbj4KICAgIDxzcGFuPiR7aWMoInNoaWVsZCIpfSR7"
    "Q01TLnRpY2szfTwvc3Bhbj48c3Bhbj4ke2ljKCJzdGFyIil9JHtDTVMudGljazR9PC9zcGFuPmAp"
    "LmpvaW4oIiIpfQogIDwvZGl2PjwvZGl2PgogICR7Rygib25ib2FyZGluZyIsYDxkaXYgY2xhc3M9"
    "ImNhcmQgdGlnaHQiIGlkPSJ0b3VyY2FyZCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7Z2FwOjEwcHgiPgogICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygiemFwIil9PC9k"
    "aXY+CiAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNp"
    "emU6LjhyZW0iPk5ldyBoZXJlPyAzLXN0ZXAgdG91cjwvYj4KICAgICAgPGJyPjxzbWFsbCBzdHls"
    "ZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj5Ub3AgdXAsIHBpY2sgYSBjb3Vu"
    "dHJ5LCBnZXQgdGhlIE9UUCBpbi1hcHAuPC9zbWFsbD48L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9"
    "ImJ0biB4cyIgaWQ9InRvdXJnbyI+U3RhcnQ8L2J1dHRvbj4KICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biB4cyBnaG9zdCIgaWQ9InRvdXJza2lwIj5Ta2lwPC9idXR0b24+PC9kaXY+YCl9CiAgPHNlY3Rp"
    "b24gY2xhc3M9Imhlcm8iPgogICAgPGRpdiBjbGFzcz0ia2ljayI+JHtDTVMuaGVyb0tpY2t9PC9k"
    "aXY+CiAgICA8aDE+JHtDTVMuaGVyb0gxfTwvaDE+CiAgICA8cD4ke0NNUy5oZXJvU3ViLnJlcGxh"
    "Y2UoIntufSIsbGl2ZSl9PC9wPgogICAgPGRpdiBjbGFzcz0iYnRucm93Ij4KICAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIGN0YSIgZGF0YS1zY3JvbGw9InNob3BsaXN0Ij4ke2ljKCJiYWciKX0gJHtD"
    "TVMuaGVyb0J0bjF9PC9idXR0b24+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBnbGFzcyIgZGF0"
    "YS1nbz0id2FsbGV0Ij4ke0NNUy5oZXJvQnRuMn08L2J1dHRvbj4KICAgIDwvZGl2PgogIDwvc2Vj"
    "dGlvbj4KICA8c3ZnIGNsYXNzPSJ0cmFjZSIgdmlld0JveD0iMCAwIDMyMCAxMCIgcHJlc2VydmVB"
    "c3BlY3RSYXRpbz0ibm9uZSI+PHBhdGggY2xhc3M9ImJhc2UiIGQ9Ik0wLDUgTDMyMCw1Ii8+CiAg"
    "ICA8cGF0aCBjbGFzcz0ibGVhZCIgZD0iTTAsNSBMMzIwLDUiIHN0cm9rZT0idXJsKCN0ZzEpIi8+"
    "CiAgICA8ZGVmcz48bGluZWFyR3JhZGllbnQgaWQ9InRnMSIgZ3JhZGllbnRVbml0cz0idXNlclNw"
    "YWNlT25Vc2UiIHgxPSItMTYwIiB5MT0iMCIgeDI9IjAiIHkyPSIwIj4KICAgICAgPHN0b3Agc3Rv"
    "cC1jb2xvcj0idmFyKC0tYWNjKSIgc3RvcC1vcGFjaXR5PSIwIi8+PHN0b3Agb2Zmc2V0PSIuNSIg"
    "c3RvcC1jb2xvcj0idmFyKC0tYWNjKSIvPgogICAgICA8c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29s"
    "b3I9InZhcigtLWFjYzIpIiBzdG9wLW9wYWNpdHk9IjAiLz4KICAgICAgPGFuaW1hdGUgYXR0cmli"
    "dXRlTmFtZT0ieDEiIHZhbHVlcz0iLTE2MDszMjAiIGR1cj0iMi4ycyIgcmVwZWF0Q291bnQ9Imlu"
    "ZGVmaW5pdGUiLz4KICAgICAgPGFuaW1hdGUgYXR0cmlidXRlTmFtZT0ieDIiIHZhbHVlcz0iMDs0"
    "ODAiIGR1cj0iMi4ycyIgcmVwZWF0Q291bnQ9ImluZGVmaW5pdGUiLz4KICAgIDwvbGluZWFyR3Jh"
    "ZGllbnQ+PC9kZWZzPjwvc3ZnPgogIDxkaXYgY2xhc3M9ImZlYXRzIj4KICAgIDxkaXYgY2xhc3M9"
    "ImZlYXQiPiR7aWMoInphcCIpfTxiPiR7Q01TLmZlYXQxfTwvYj48c21hbGw+JHtDTVMuZmVhdDFz"
    "fTwvc21hbGw+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmZWF0Ij4ke2ljKCJzaGllbGQiKX08Yj4k"
    "e0NNUy5mZWF0Mn08L2I+PHNtYWxsPiR7Q01TLmZlYXQyc308L3NtYWxsPjwvZGl2PgogICAgPGRp"
    "diBjbGFzcz0iZmVhdCI+JHtpYygid2FsbGV0Iil9PGI+JHtDTVMuZmVhdDN9PC9iPjxzbWFsbD4k"
    "e0NNUy5mZWF0M3N9PC9zbWFsbD48L2Rpdj4KICA8L2Rpdj4KICAke0coInN0b2NrQ291bnRlciIs"
    "YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6"
    "Y2VudGVyO2dhcDoxMHB4Ij4KICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFzcz0iZG90"
    "Ij48L2k+TGl2ZTwvc3Bhbj4KICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250"
    "LXNpemU6Ljc2cmVtO2NvbG9yOnZhcigtLXR4MikiPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCki"
    "PiR7dW5pdHN9PC9iPiBhY2NvdW50cyBpbiBzdG9jayBhY3Jvc3MgPGIgc3R5bGU9ImNvbG9yOnZh"
    "cigtLXR4KSI+JHtsaXZlfTwvYj4gY291bnRyaWVzPC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4geHMgc29mdCIgZGF0YS1hY3Q9ImNoYW5uZWwiPkNoYW5uZWw8L2J1dHRvbj48L2Rpdj5gKX0K"
    "ICAke0coImZsYXNoU2FsZSIsYDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5"
    "OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgIDxzcGFuIGNsYXNzPSJwaWxs"
    "IGhvdCI+JHtpYygiZmlyZSIpfSAke0NNUy5mbGFzaFBpbGx9PC9zcGFuPgogICAgPGRpdiBzdHls"
    "ZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzZyZW07Y29sb3I6dmFyKC0tdHgyKSI+"
    "JHtDTVMuZmxhc2hUZXh0fSA8YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIiBpZD0iY2QiPjE0OjUy"
    "PC9iPjwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBkYXRhLWFjdD0iZmxhc2giPiR7"
    "Q01TLmZsYXNoQnRufTwvYnV0dG9uPjwvZGl2PmApfQogIDxkaXYgY2xhc3M9IndoZWVsIj48ZGl2"
    "IGNsYXNzPSJyaW5nIj4ke1NUT0NLLm1hcCgocyxpKT0+YDxiIHN0eWxlPSJ0cmFuc2Zvcm06cm90"
    "YXRlKCR7aSo2MH1kZWcpIHRyYW5zbGF0ZSg3MHB4KSByb3RhdGUoLSR7aSo2MH1kZWcpIj4ke3Mu"
    "Y29kZX08L2I+YCkuam9pbigiIil9PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJjYXAiPjxlbT4ke0NN"
    "Uy53aGVlbEtpY2t9PC9lbT48Yj4ke0NNUy53aGVlbFRpdGxlfTwvYj4KICAgICAgPGRpdiBjbGFz"
    "cz0iY2xvdWQiIHN0eWxlPSJtYXJnaW4tdG9wOjZweCI+PGRpdiBjbGFzcz0idCI+JHtbMSwyXS5t"
    "YXAoKCk9PkNNUy53aGVlbFdvcmRzLnNwbGl0KCIsIikubWFwKHc9PmA8aT4ke3cudHJpbSgpfTwv"
    "aT5gKS5qb2luKCIiKSkuam9pbigiIil9PC9kaXY+PC9kaXY+PC9kaXY+PC9kaXY+CiAgJHtHKCJz"
    "YWxlc0ZlZWQiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+SnVzdCBzb2xkPC9oMz48cCBjbGFzcz0i"
    "c3ViIj5MaXZlIGZlZWQg4oCUIGFsc28gYXV0by1wb3N0ZWQgdG8geW91ciBjaGFubmVsLjwvcD4K"
    "ICAgICR7RkVFRC5sZW5ndGg/RkVFRC5tYXAoKFtmLGMsYSx3XSk9PmA8ZGl2IGNsYXNzPSJmZWVk"
    "bGluZSI+PHNwYW4+JHtmfTwvc3Bhbj48Yj4ke2N9PC9iPiBzb2xkIGZvciA8Yj4ke2F9PC9iPjxz"
    "cGFuIHN0eWxlPSJtYXJnaW4tbGVmdDphdXRvIj4ke3d9PC9zcGFuPjwvZGl2PmApLmpvaW4oIiIp"
    "CiAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBzYWxlcyB5ZXQgdG9k"
    "YXkuPC9wPid9PC9kaXY+YCl9CiAgPGRpdiBjbGFzcz0ic2VjIiBpZD0ic2hvcGxpc3QiPjxoMj4k"
    "e0NNUy5zaG9wSGVhZH08L2gyPjxhIGRhdGEtYWN0PSJzb3J0Ij4ke0NNUy5zaG9wU29ydH08L2E+"
    "PC9kaXY+CiAgPGRpdiBjbGFzcz0ibXNlYXJjaCI+PGRpdiBjbGFzcz0iZmllbGR3cmFwIj4ke2lj"
    "KCJzZWFyY2giKX08aW5wdXQgaWQ9InEiIHBsYWNlaG9sZGVyPSIke0NNUy5zZWFyY2hQaH0iPjwv"
    "ZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIHNvZnQiIGRhdGEtYWN0PSJhbGVydHMiPiR7"
    "Q01TLmFsZXJ0c0J0bn08L2J1dHRvbj48L2Rpdj4KICA8ZGl2IGNsYXNzPSJjaGlwcm93IiBpZD0i"
    "Y2hpcHMiPgogICAgPGJ1dHRvbiBjbGFzcz0ib24iIGRhdGEtZmlsdGVyPSJhbGwiPkFsbDwvYnV0"
    "dG9uPjxidXR0b24gZGF0YS1maWx0ZXI9InN0b2NrIj5JbiBzdG9jazwvYnV0dG9uPgogICAgPGJ1"
    "dHRvbiBkYXRhLWZpbHRlcj0iZGVhbCI+T24gZGVhbDwvYnV0dG9uPjxidXR0b24gZGF0YS1maWx0"
    "ZXI9ImNoZWFwIj5VbmRlciDigrkzNTwvYnV0dG9uPgogICAgJHtHKCJ2YXJpYW50cyIsYDxidXR0"
    "b24gZGF0YS1maWx0ZXI9InYyZmEiPk5vIDJGQTwvYnV0dG9uPjxidXR0b24gZGF0YS1maWx0ZXI9"
    "InZtYWlsIj5XaXRoIG1haWw8L2J1dHRvbj48YnV0dG9uIGRhdGEtZmlsdGVyPSJ2c3BhbSI+U3Bh"
    "bS1mcmVlPC9idXR0b24+YCl9PC9kaXY+CiAgPGRpdiBpZD0ic2hvcGJvZHkiPiR7c2hvcEJvZHko"
    "KX08L2Rpdj5gOwp9CmZ1bmN0aW9uIHNob3BCb2R5KCl7cmV0dXJuIGAke0ZJTFRFUkVELmxlbmd0"
    "aD9gPGRpdiBjbGFzcz0iZ3JpZCI+JHtGSUxURVJFRC5tYXAoc2hvcENhcmQpLmpvaW4oIiIpfTwv"
    "ZGl2PmA6RU1QVFl9YDt9CmZ1bmN0aW9uIHByaWNlQml0cyhzKXsKICByZXR1cm4gYCR7cy53YXM/"
    "YDxzPiR7bW9uZXkocy53YXMpfTwvcz5gOiIifSR7bW9uZXkocy5wcmljZSl9YDsKfQpmdW5jdGlv"
    "biBzaG9wUm93KHMpe2NvbnN0IGk9U1RPQ0suaW5kZXhPZihzKSxvZmY9cy5xdHk9PT0wOwogIHJl"
    "dHVybiBgPGRpdiBjbGFzcz0icm93IiBkYXRhLWJ1eT0iJHtpfSI+PGRpdiBjbGFzcz0iZmxhZyI+"
    "JHtzLmZ9PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJtaWQiPjxiPiR7ZXNjKHMuYyl9ICR7cy5jb2Rl"
    "fTwvYj4KICAgICAgPHNtYWxsPiR7b2ZmPyJPdXQgb2Ygc3RvY2sg4oCUIGdldCBhbiBhbGVydCI6"
    "cy5xdHkrIiBpbiBzdG9jayDCtyBpbnN0YW50In08L3NtYWxsPgogICAgICAke0coInJldmlld3Mi"
    "LGA8ZGl2IHN0eWxlPSJtYXJnaW4tdG9wOjNweCI+JHtzdGFycyhzLmMpfTwvZGl2PmApfQogICAg"
    "ICAke0coInZhcmlhbnRzIixgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDozcHg7ZGlzcGxheTpmbGV4"
    "O2dhcDo0cHg7ZmxleC13cmFwOndyYXAiPgogICAgICAgIDxzcGFuIGNsYXNzPSJwaWxsIj5ObyAy"
    "RkE8L3NwYW4+JHtzLmhvdD8nPHNwYW4gY2xhc3M9InBpbGwiPldpdGggbWFpbDwvc3Bhbj4nOiIi"
    "fTwvZGl2PmApfTwvZGl2PgogICAgPGRpdiBjbGFzcz0icnQiPjxkaXYgY2xhc3M9InAiPiR7cHJp"
    "Y2VCaXRzKHMpfTwvZGl2PgogICAgICAke0coImR5blByaWNlIixgPHNtYWxsIHN0eWxlPSJkaXNw"
    "bGF5OmJsb2NrO2ZvbnQtc2l6ZTouNnJlbTtjb2xvcjp2YXIoLS10eDIpIj5saXZlIHByaWNlPC9z"
    "bWFsbD5gKX0KICAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo1cHg7anVzdGlmeS1j"
    "b250ZW50OmZsZXgtZW5kO21hcmdpbi10b3A6NnB4Ij4KICAgICAgICAke0coIndpc2hsaXN0Iixg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtd2lzaD0iJHtpfSI+JHtXSVNILmluY2x1"
    "ZGVzKGkpPyLimaUiOiLimaEifTwvYnV0dG9uPmApfQogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biB4cyI+JHtvZmY/Ik5vdGlmeSBNZSI6IkJ1eSJ9PC9idXR0b24+PC9kaXY+PC9kaXY+PC9kaXY+"
    "YDt9CmZ1bmN0aW9uIHNob3BDYXJkKHMpe2NvbnN0IGk9U1RPQ0suaW5kZXhPZihzKSxvZmY9cy5x"
    "dHk9PT0wOwogIHJldHVybiBgPGRpdiBjbGFzcz0icGNhcmQiIGRhdGEtYnV5PSIke2l9IiBzdHls"
    "ZT0icG9zaXRpb246cmVsYXRpdmUiPgogICAgJHtHKCJ3aXNobGlzdCIsYDxzcGFuIGNsYXNzPSJo"
    "ZWFydCAke1dJU0guaW5jbHVkZXMoaSk/Im9uIjoiIn0iIGRhdGEtd2lzaD0iJHtpfSI+JHtpYygi"
    "c3RhciIpfTwvc3Bhbj5gKX0KICAgIDxkaXYgY2xhc3M9InRvcCI+PGRpdiBjbGFzcz0iZmxhZyI+"
    "JHtzLmZ9PC9kaXY+CiAgICAgIDxzcGFuIGNsYXNzPSJwaWxsICR7b2ZmPyJvZmYiOiJvbiJ9Ij4k"
    "e29mZj8iU29sZCBvdXQiOnMucXR5KyIgbGVmdCJ9PC9zcGFuPjwvZGl2PgogICAgPGRpdj48ZGl2"
    "IGNsYXNzPSJubSI+JHtlc2Mocy5jKX08L2Rpdj48ZGl2IGNsYXNzPSJtZXRhIj4ke3MuY29kZX0g"
    "wrcgaW5zdGFudDwvZGl2PgogICAgICAke0coInJldmlld3MiLGA8ZGl2IHN0eWxlPSJtYXJnaW4t"
    "dG9wOjRweCI+JHtzdGFycyhzLmMpfTwvZGl2PmApfTwvZGl2PgogICAgPGRpdiBjbGFzcz0icHJp"
    "Y2UiPiR7cHJpY2VCaXRzKHMpfTwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIHNt"
    "Ij4ke29mZj8iTm90aWZ5IE1lIjoiQnV5IE5vdyJ9PC9idXR0b24+PC9kaXY+YDt9CgpmdW5jdGlv"
    "biB2T3JkZXJzKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5Zb3VyIE9yZGVyczwv"
    "aDI+PGEgZGF0YS1hY3Q9ImNzdiI+RXhwb3J0PC9hPjwvZGl2PgogICR7T1JERVJTLm1hcCgobyxv"
    "aSk9PmA8ZGl2IGNsYXNzPSJjYXJkIiBkYXRhLW9yZGVyLWlkPSIke28uaWR9Ij4KICAgIDxkaXYg"
    "c3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEycHgiPgogICAgICA8"
    "ZGl2IGNsYXNzPSJmbGFnIj4ke28uZn08L2Rpdj4KICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21p"
    "bi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44NXJlbTtkaXNwbGF5OmJsb2NrIj4ke28u"
    "bnVtfTwvYj4KICAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXpl"
    "Oi43cmVtIj4ke28ud2hlbn08L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2IHN0eWxlPSJ0ZXh0LWFs"
    "aWduOnJpZ2h0Ij48c3BhbiBjbGFzcz0icGlsbCAke28uc3Q9PT0iRGVsaXZlcmVkIj8ib24iOiJv"
    "ZmYifSI+JHtvLnN0fTwvc3Bhbj48L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImNhcmQgdGln"
    "aHQiIHN0eWxlPSJtYXJnaW46MTBweCAwIDA7YmFja2dyb3VuZDp2YXIoLS1jYXJkMikiPgogICAg"
    "ICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+UGhvbmUgbnVtYmVyPC9zcGFuPgogICAgICAgIDxiPjxj"
    "b2RlIHN0eWxlPSJmb250LXNpemU6LjhyZW07dXNlci1zZWxlY3Q6YWxsIj4ke2VzYyhvLnBob25l"
    "fHwi4oCUIil9PC9jb2RlPgogICAgICAgICR7by5waG9uZT9gPGJ1dHRvbiBjbGFzcz0iYnRuIHhz"
    "IHNvZnQiIGRhdGEtY29weT0idGV4dCIgZGF0YS1jb3B5LXZhbHVlPSIke2VzYyhvLnBob25lKX0i"
    "PkNvcHk8L2J1dHRvbj5gOiIifTwvYj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFu"
    "PjJGQSBwYXNzd29yZDwvc3Bhbj4KICAgICAgICA8Yj48Y29kZSBzdHlsZT0iZm9udC1zaXplOi44"
    "cmVtO3VzZXItc2VsZWN0OmFsbCI+JHtlc2Moby50d29mYXx8Im5vbmUiKX08L2NvZGU+CiAgICAg"
    "ICAgJHtvLnR3b2ZhP2A8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1jb3B5PSJ0ZXh0"
    "IiBkYXRhLWNvcHktdmFsdWU9IiR7ZXNjKG8udHdvZmEpfSI+Q29weTwvYnV0dG9uPmA6IiJ9PC9i"
    "PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+TGFzdCBsb2dpbiBjb2RlPC9zcGFu"
    "PgogICAgICAgIDxiPjxjb2RlIHN0eWxlPSJmb250LXNpemU6LjhyZW07dXNlci1zZWxlY3Q6YWxs"
    "Ij4ke2VzYyhvLm90cHx8Im5vdCB5ZXQiKX08L2NvZGU+CiAgICAgICAgJHtvLm90cD9gPGJ1dHRv"
    "biBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtY29weT0idGV4dCIgZGF0YS1jb3B5LXZhbHVlPSIk"
    "e2VzYyhvLm90cCl9Ij5Db3B5PC9idXR0b24+YDoiIn08L2I+PC9kaXY+CiAgICAgIDxkaXYgY2xh"
    "c3M9Imt2Ij48c3Bhbj5TdGF0dXM8L3NwYW4+PGI+JHtvLmxvZ2luPT09ImluIgogICAgICAgID8g"
    "JzxzcGFuIGNsYXNzPSJwaWxsIG9uIj5Mb2dnZWQgaW4gwrcgeW91cnMgb25seTwvc3Bhbj4nCiAg"
    "ICAgICAgOiAoby53aW5kb3c+MAogICAgICAgICAgICA/ICc8c3BhbiBjbGFzcz0icGlsbCI+Q29k"
    "ZXMgZm9yd2FyZGluZyDCtyAnK28ud2luZG93KycgbWluIGxlZnQ8L3NwYW4+JwogICAgICAgICAg"
    "ICA6ICc8c3BhbiBjbGFzcz0icGlsbCBvZmYiPkhhbmRvdmVyIGNvbXBsZXRlPC9zcGFuPicpfTwv"
    "Yj48L2Rpdj4KICAgIDwvZGl2PgogICAgJHtHKCJ0aW1lbGluZSIsYDxkaXYgY2xhc3M9InRsIj4K"
    "ICAgICAgPGRpdiBjbGFzcz0ib2siPjxiPlBhaWQ8L2I+PHNtYWxsPiR7by53aGVufTwvc21hbGw+"
    "PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9Im9rIj48Yj5EZWxpdmVyZWQ8L2I+PHNtYWxsPlNlc3Np"
    "b24gaGFuZGVkIG92ZXIgaW5zdGFudGx5PC9zbWFsbD48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0i"
    "JHtvLnN0PT09IkRlbGl2ZXJlZCI/Im9rIjoiIn0iPjxiPk9UUCByZWNlaXZlZDwvYj48c21hbGw+"
    "JHtvLnN0PT09IkRlbGl2ZXJlZCI/IkNvZGUgZm9yd2FyZGVkIGluLWFwcCI6IldhaXRpbmcifTwv"
    "c21hbGw+PC9kaXY+CiAgICAgIDxkaXY+PGI+Q2xvc2VkPC9iPjxzbWFsbD5BdXRvLWNsb3NlcyBh"
    "ZnRlciB0aGUgd2FycmFudHkgd2luZG93PC9zbWFsbD48L2Rpdj48L2Rpdj5gKX0KICAgIDxkaXYg"
    "Y2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTFweCI+CiAgICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBzbSIgZGF0YS1vdHA9IiR7by5udW19Ij5HZXQgT1RQPC9idXR0b24+CiAgICAgICR7"
    "RygicmVkb3dubG9hZCIsYDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBkYXRhLW9yZD0iZGV0"
    "YWlscyI+RGV0YWlsczwvYnV0dG9uPmApfTwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBz"
    "dHlsZT0ibWFyZ2luLXRvcDo3cHgiPgogICAgICAke0coInNlc3Npb25GaWxlIixgPGJ1dHRvbiBj"
    "bGFzcz0iYnRuIHNtIHNvZnQiIGRhdGEtb3JkPSJmaWxlIj4ke2ljKCJjb3B5Iil9IEZpbGU8L2J1"
    "dHRvbj5gKX0KICAgICAgJHtHKCJyZWNlaXB0cyIsYDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0"
    "IiBkYXRhLW9yZD0icmVjZWlwdCI+UmVjZWlwdDwvYnV0dG9uPmApfQogICAgICAkeyhvLmxvZ2lu"
    "PT09ImluIik/IiI6YDxidXR0b24gY2xhc3M9ImJ0biBzbSIgZGF0YS1sb2dnZWRpbj0iJHtvLmlk"
    "fSI+SSdtIGxvZ2dlZCBpbjwvYnV0dG9uPmB9CiAgICAgICR7Rygid2FycmFudHkiLGA8YnV0dG9u"
    "IGNsYXNzPSJidG4gc20gZ2hvc3QiIGRhdGEtY2xhaW0tb3JkZXI9IiR7by5pZH0iPlJlcG9ydCBp"
    "c3N1ZTwvYnV0dG9uPmApfTwvZGl2PgogICAgJHtHKCJyZXZpZXdzIixgPGRpdiBjbGFzcz0ia3Yi"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjlweCI+PHNwYW4+UmF0ZSB0aGlzIG9yZGVyPC9zcGFuPgogICAg"
    "ICA8Yj48YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1yYXRlPSIke28uY30iPlJhdGU8"
    "L2J1dHRvbj48L2I+PC9kaXY+YCl9CiAgPC9kaXY+YCkuam9pbigiIil9CgogIDxkaXYgY2xhc3M9"
    "ImNhcmQiIHN0eWxlPSJ0ZXh0LWFsaWduOmNlbnRlciI+PGgzPk5lZWQgaGVscCB3aXRoIGFuIG9y"
    "ZGVyPzwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj4ke0ZFQVQuYXV0b1JlZnVuZD8iRmFpbGVkIGxv"
    "Z2lucyBhcmUgcmVmdW5kZWQgYXV0b21hdGljYWxseSB3aXRoaW4gMTAgbWludXRlcy4iOiJDb250"
    "YWN0IHN1cHBvcnQgYW5kIHdlIHdpbGwgbG9vayBpbnRvIGl0LiJ9PC9wPgogICAgPGRpdiBjbGFz"
    "cz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48YnV0dG9uIGNsYXNzPSJidG4gc29m"
    "dCIgZGF0YS1hY3Q9InN1cHBvcnQiPlN1cHBvcnQ8L2J1dHRvbj4KICAgICR7RygiZGlzcHV0ZSIs"
    "YDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCIgZGF0YS1kaXNwdXRlPkRpc3B1dGU8L2J1dHRvbj5g"
    "KX08L2Rpdj48L2Rpdj5gOwp9CgpmdW5jdGlvbiB2RWFybigpewogIHJldHVybiBgJHtHKCJzdHJl"
    "YWsiLGA8ZGl2IGNsYXNzPSJjYXJkIj4KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGln"
    "bi1pdGVtczpjZW50ZXI7Z2FwOjEycHgiPgogICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJ6"
    "YXAiKX08L2Rpdj48ZGl2IHN0eWxlPSJmbGV4OjEiPjxoMz5EYWlseSBDaGVjay1pbjwvaDM+PHAg"
    "Y2xhc3M9InN1YiI+RGF5ICR7RUFSTi5zdHJlYWt9IHN0cmVhayDCtyAke21vbmV5KEVBUk4uZGFp"
    "bHkpfSB3YWl0aW5nPC9wPjwvZGl2PjwvZGl2PgogICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4"
    "O2dhcDo2cHg7bWFyZ2luOjE0cHggMCI+CiAgICAgICR7WzEsMiwzLDQsNSw2LDddLm1hcChkPT5g"
    "PGRpdiBzdHlsZT0iZmxleDoxO2hlaWdodDozNHB4O2JvcmRlci1yYWRpdXM6OXB4O2Rpc3BsYXk6"
    "Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9udC1zaXplOi42OHJlbTtmb250LXdlaWdodDo3MDA7"
    "Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTske2Q8PShFQVJOLnN0cmVha3x8MCk/ImJhY2tn"
    "cm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiMwMjEyMGY7Ym9yZGVyLWNvbG9yOnRyYW5zcGFy"
    "ZW50IjoiYmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgyKSJ9Ij4ke2R9PC9k"
    "aXY+YCkuam9pbigiIil9PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBk"
    "YXRhLWNsYWltICR7RUFSTi5jbGFpbWVkPyJkaXNhYmxlZCI6IiJ9PiR7RUFSTi5jbGFpbWVkPyJD"
    "bGFpbWVkIHRvZGF5IjoiQ2xhaW0gIittb25leShFQVJOLmRhaWx5KX08L2J1dHRvbj48L2Rpdj5g"
    "KX0KICAke0coImxveWFsdHkiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+TG95YWx0eSBQb2ludHM8"
    "L2gzPjxwIGNsYXNzPSJzdWIiPjEgcG9pbnQgcGVyIOKCuTEwIHNwZW50LiAxMDAgcG9pbnRzID0g"
    "4oK5MTAgYmFsYW5jZS48L3A+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+QmFsYW5jZTwvc3Bh"
    "bj48Yj4ke0VBUk4ucG9pbnRzfSBwdHM8L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNw"
    "YW4+V29ydGg8L3NwYW4+PGI+JHttb25leShFQVJOLnBvaW50c1dvcnRoKX08L2I+PC9kaXY+CiAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIGlkPSJyZWRlZW1nbyIgJHtFQVJOLnBvaW50cz4w"
    "PyIiOiJkaXNhYmxlZCJ9PlJlZGVlbSBQb2ludHM8L2J1dHRvbj48L2Rpdj5gKX0KICAke2FueUYo"
    "InJlZkRhc2giLCJyZWZMMiIpP2A8ZGl2IGNsYXNzPSJjYXJkIj48aDM+SW52aXRlICZhbXA7IEVh"
    "cm48L2gzPgogICAgPHAgY2xhc3M9InN1YiI+JHtGRUFULnJlZkwyPyI1JSBmcm9tIHBlb3BsZSB5"
    "b3UgaW52aXRlLCBwbHVzIDElIGZyb20gdGhlIHBlb3BsZSB0aGV5IGludml0ZS4iOiIxJSBvZiBl"
    "dmVyeSBkZXBvc2l0IHlvdXIgaW52aXRlcyBldmVyIG1ha2UuIn08L3A+CiAgICA8ZGl2IGNsYXNz"
    "PSJrdiI+PHNwYW4+SW52aXRlczwvc3Bhbj48Yj4ke0VBUk4uaW52aXRlc308L2I+PC9kaXY+CiAg"
    "ICAke0coInJlZkwyIixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxldmVsIDEgZWFybmluZ3M8L3Nw"
    "YW4+PGI+JHttb25leShFQVJOLmwxKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNw"
    "YW4+TGV2ZWwgMiBlYXJuaW5nczwvc3Bhbj48Yj4ke21vbmV5KEVBUk4ubDIpfTwvYj48L2Rpdj5g"
    "KX0KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Ub3RhbCBlYXJuZWQ8L3NwYW4+PGI+JHttb25l"
    "eShFQVJOLmVhcm5lZCl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPllvdXIg"
    "bGluazwvc3Bhbj48YiBzdHlsZT0iZm9udC1zaXplOi43MnJlbSI+JHtlc2MoRUFSTi5saW5rKX08"
    "L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgi"
    "PjxidXR0b24gY2xhc3M9ImJ0biIgZGF0YS1jb3B5PSJyZWYiPiR7aWMoImNvcHkiKX0gQ29weSBM"
    "aW5rPC9idXR0b24+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0YS1jb3B5PSJyZWYi"
    "PlNoYXJlPC9idXR0b24+PC9kaXY+CiAgICAke0coInJlZkRhc2giLGA8ZGl2IGNsYXNzPSJzZWMi"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjZweCI+PGgyIHN0eWxlPSJmb250LXNpemU6Ljc4cmVtIj5SZWNl"
    "bnQgaW52aXRlczwvaDI+PC9kaXY+CiAgICAke0VBUk4ucmVjZW50Lmxlbmd0aD9FQVJOLnJlY2Vu"
    "dC5tYXAoKFt1LGEsZV0pPT5gPGRpdiBjbGFzcz0iZmVlZGxpbmUiPjxiPiR7ZXNjKHUpfTwvYj4g"
    "JHtlc2MoYSl9PHNwYW4gc3R5bGU9Im1hcmdpbi1sZWZ0OmF1dG87Y29sb3I6dmFyKC0tb2spIj4k"
    "e2VzYyhlKX08L3NwYW4+PC9kaXY+YCkuam9pbigiIikKICAgICAgOic8cCBjbGFzcz0ic3ViIiBz"
    "dHlsZT0ibWFyZ2luOjAiPk5vYm9keSBoYXMgam9pbmVkIHRocm91Z2ggeW91ciBsaW5rIHlldC48"
    "L3A+J30KICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MTFweCAwIDAiPlJlZmVycmFs"
    "IGVhcm5pbmdzIGxhbmQgaW4geW91ciBiYWxhbmNlIGF1dG9tYXRpY2FsbHkg4oCUIG5vdGhpbmcg"
    "dG8gY2xhaW0uPC9wPmApfQogIDwvZGl2PmA6IiJ9CiAgJHtHKCJsZWFkZXJib2FyZCIsYDxkaXYg"
    "Y2xhc3M9ImNhcmQiPjxoMz5UaGlzIHdlZWsncyB0b3AgYnV5ZXJzPC9oMz48cCBjbGFzcz0ic3Vi"
    "Ij5OdW1iZXIgb25lIHdpbnMg4oK5NTAwIG9uIFN1bmRheS48L3A+CiAgICAke0VBUk4ubGVhZGVy"
    "cy5sZW5ndGg/RUFSTi5sZWFkZXJzLm1hcCgoW3IsdSx2XSxpKT0+YDxkaXYgY2xhc3M9ImxiIj48"
    "c3BhbiBjbGFzcz0icmsgJHtpPT09MD8iZyI6IiJ9Ij4ke2VzYyhyKX08L3NwYW4+CiAgICAgICAg"
    "PGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44cmVt"
    "Ij4ke2VzYyh1KX08L2I+PC9kaXY+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+JHtlc2Modil9"
    "PC9iPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdp"
    "bjowIj5ObyBwdXJjaGFzZXMgeWV0IHRoaXMgd2Vlay48L3A+J308L2Rpdj5gKX0KICAke0coIndp"
    "bmJhY2siLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICA8c3BhbiBjbGFzcz0icGlsbCBob3QiPiR7aWMo"
    "ImZpcmUiKX0gQ29tZWJhY2s8L3NwYW4+CiAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRo"
    "OjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj4xMCUgb2ZmIOKAlCBleHBpcmVz"
    "IGluIDQ4IGhvdXJzPC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMiIGlkPSJvZmZlcnVz"
    "ZSI+VXNlPC9idXR0b24+PC9kaXY+YCl9CiAgPGRpdiBjbGFzcz0ic2VjIj48aDI+VGFza3M8L2gy"
    "PjwvZGl2PgogICR7RUFSTi50YXNrcy5tYXAoKFt0LHIsZG9uZV0pPT5gPGRpdiBjbGFzcz0iY2Fy"
    "ZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjExcHgi"
    "PgogICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2RvbmU/aWMoImNoZWNrIik6aWMoInphcCIpfTwv"
    "ZGl2PgogICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250"
    "LXNpemU6LjgycmVtIj4ke3R9PC9iPjxicj48c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7"
    "Zm9udC1zaXplOi43cmVtIj5SZXdhcmQgJHtyfTwvc21hbGw+PC9kaXY+CiAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biB4cyAke2RvbmU/InNvZnQiOiIifSIgJHtkb25lPyJkaXNhYmxlZCI6IiJ9IGRh"
    "dGEtYWN0PSJ0YXNrIj4ke2RvbmU/IkRvbmUiOiJTdGFydCJ9PC9idXR0b24+PC9kaXY+YCkuam9p"
    "bigiIil9YDsKfQoKZnVuY3Rpb24gdldhbGxldCgpewogIHJldHVybiBgPGRpdiBjbGFzcz0iaGVy"
    "byIgc3R5bGU9InRleHQtYWxpZ246Y2VudGVyIj4KICAgIDxkaXYgY2xhc3M9ImtpY2siPiR7Q01T"
    "LndhbEtpY2t9PC9kaXY+CiAgICA8aDEgc3R5bGU9ImZvbnQtc2l6ZToyLjNyZW0iPuKCuTxzcGFu"
    "IGlkPSJiYWwyIiBjbGFzcz0idGljayI+JHtCQUwudG9GaXhlZCgyKX08L3NwYW4+PC9oMT4KICAg"
    "IDxwIHN0eWxlPSJtYXJnaW46MCBhdXRvIDE0cHgiPiR7Q01TLndhbFN1Yn08L3A+CiAgICA8ZGl2"
    "IHN0eWxlPSJtYXJnaW46MCBhdXRvO21heC13aWR0aDozMDBweCI+CiAgICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBjdGEgYmxvY2siIGRhdGEtZGVwPiR7aWMoInBsdXMiKX0gJHtDTVMud2FsQWRkfTwv"
    "YnV0dG9uPgogICAgICAke0coImNyeXB0byIsYDxidXR0b24gY2xhc3M9ImJ0biBnbGFzcyBibG9j"
    "ayIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNyeXB0bz5QYXkgd2l0aCBDcnlwdG88L2J1"
    "dHRvbj5gKX08L2Rpdj48L2Rpdj4KICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlz"
    "cGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICA8c3BhbiBjbGFzcz0i"
    "cGlsbCBvbiI+PGkgY2xhc3M9ImRvdCI+PC9pPkF1dG8tdmVyaWZ5IE9OPC9zcGFuPgogICAgPGRp"
    "diBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0t"
    "dHgyKSI+VG9wLXVwcyBhcmUgY3JlZGl0ZWQgYXMgc29vbiBhcyB0aGV5IGFycml2ZTwvZGl2Pgog"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtYWN0PSJzdGF0dXMiPlN0YXR1czwv"
    "YnV0dG9uPjwvZGl2PgogICR7RygiZGVwQm9udXMiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+VG9w"
    "LXVwIGJvbnVzPC9oMz48cCBjbGFzcz0ic3ViIj5FeHRyYSBjcmVkaXQgYWRkZWQgYXV0b21hdGlj"
    "YWxseS48L3A+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjdweDttYXJnaW4tdG9w"
    "OjEwcHgiPgogICAgICAke1tbIuKCuTUwMCIsIis1JSJdLFsi4oK5MTAwMCIsIis4JSJdLFsi4oK5"
    "MjAwMCIsIisxMiUiXV0ubWFwKChbYSxiXSk9PmA8ZGl2IHN0eWxlPSJmbGV4OjE7dGV4dC1hbGln"
    "bjpjZW50ZXI7cGFkZGluZzo5cHggNHB4O2JvcmRlci1yYWRpdXM6MTJweDtib3JkZXI6MXB4IHNv"
    "bGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpIj4KICAgICAgICA8YiBzdHls"
    "ZT0iZm9udC1zaXplOi44cmVtO2Rpc3BsYXk6YmxvY2siPiR7YX08L2I+PHNtYWxsIHN0eWxlPSJm"
    "b250LXNpemU6LjY4cmVtO2NvbG9yOnZhcigtLW9rKSI+JHtifTwvc21hbGw+PC9kaXY+YCkuam9p"
    "bigiIil9PC9kaXY+PC9kaXY+YCl9CiAgPGRpdiBjbGFzcz0ic2VjIj48aDI+UXVpY2sgVG9wLXVw"
    "PC9oMj48L2Rpdj4KICA8ZGl2IGNsYXNzPSJncmlkIiBzdHlsZT0iZ3JpZC10ZW1wbGF0ZS1jb2x1"
    "bW5zOnJlcGVhdCgzLDFmcikiPgogICAgJHtbNTAsMTAwLDIwMCw1MDAsMTAwMCwyMDAwXS5tYXAo"
    "YT0+YDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLWFtdD0iJHthfSI+4oK5JHthfTwvYnV0"
    "dG9uPmApLmpvaW4oIiIpfTwvZGl2PgogICR7RygiZGVwTGltaXRzIixgPHAgY2xhc3M9InN1YiIg"
    "c3R5bGU9Im1hcmdpbjo5cHggMCAwO2ZvbnQtc2l6ZTouN3JlbSI+TWluaW11bSDigrkxMCDCtyBt"
    "YXhpbXVtIOKCuTIwLDAwMCBwZXIgdG9wLXVwLjwvcD5gKX0KICA8ZGl2IGNsYXNzPSJjYXJkIiBz"
    "dHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48aDM+JHtDTVMucGF5VGl0bGV9PC9oMz4KICAgIDxwIGNs"
    "YXNzPSJzdWIiPiR7Q01TLnBheVN1Yn08L3A+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VVBJ"
    "IElEPC9zcGFuPjxiPiR7ZXNjKFNUT1JFLnVwaXx8Im5vdCBzZXQgeWV0Iil9PC9iPjwvZGl2Pgog"
    "ICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlBheWVlPC9zcGFuPjxiPiR7ZXNjKFNUT1JFLnBheWVl"
    "fHwi4oCUIil9PC9iPjwvZGl2PgogICAgJHtHKCJ1cGlSb3RhdGUiLGA8ZGl2IGNsYXNzPSJrdiI+"
    "PHNwYW4+SGFuZGxlcyBpbiByb3RhdGlvbjwvc3Bhbj48Yj4zIGFjdGl2ZTwvYj48L2Rpdj5gKX0K"
    "ICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5QYXllZTwvc3Bhbj48Yj5Zb3VyIFN0b3JlIE5hbWU8"
    "L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+QXV0by12ZXJpZnk8L3NwYW4+PGIg"
    "c3R5bGU9ImNvbG9yOnZhcigtLW9rKSI+RW5hYmxlZDwvYj48L2Rpdj4KICAgICR7Rygid3JvbmdB"
    "bXQiLGA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+V3JvbmcgYW1vdW50PC9zcGFuPjxiPkNyZWRpdGVk"
    "IGFzIHBhaWQ8L2I+PC9kaXY+YCl9CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2sgc29mdCIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jb3B5PSJ1cGkiPiR7aWMoImNvcHkiKX0gQ29w"
    "eSBVUEkgSUQ8L2J1dHRvbj48L2Rpdj4KICAke0coImFiYW5kb25SZW1pbmQiLGA8cCBjbGFzcz0i"
    "c3ViIiBzdHlsZT0ibWFyZ2luOjEwcHggMCAwO2ZvbnQtc2l6ZTouN3JlbSI+V2Ugd2lsbCByZW1p"
    "bmQgeW91IGJlZm9yZSBhbiB1bmZpbmlzaGVkIHBheW1lbnQgZXhwaXJlcy48L3A+YCl9CiAgPGRp"
    "diBjbGFzcz0ic2VjIj48aDI+JHtGRUFULmxlZGdlcj8iU3RhdGVtZW50IjoiSGlzdG9yeSJ9PC9o"
    "Mj4ke0coImxlZGdlciIsYDxhIGRhdGEtYWRtaW4tYWN0PSJleHBvcnQiPkV4cG9ydDwvYT5gKX08"
    "L2Rpdj4KICAke1RYLm1hcCgodCxpKT0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJk"
    "aXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMXB4Ij4KICAgIDxkaXYgY2xhc3M9"
    "ImZsYWciPiR7dC5hWzBdPT09IisiP2ljKCJkb3duIik6aWMoInVwIil9PC9kaXY+CiAgICA8ZGl2"
    "IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjgycmVtIj4k"
    "e3QudH08L2I+PGJyPjxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6Ljdy"
    "ZW0iPiR7dC53fTwvc21hbGw+PC9kaXY+CiAgICA8ZGl2IHN0eWxlPSJ0ZXh0LWFsaWduOnJpZ2h0"
    "Ij48YiBzdHlsZT0iZm9udC1zaXplOi44NXJlbTtjb2xvcjoke3QuYVswXT09PSIrIj8idmFyKC0t"
    "b2spIjoidmFyKC0tdHgpIn0iPiR7dC5hfTwvYj4KICAgICR7RygibGVkZ2VyIixgPGJyPjxzbWFs"
    "bCBzdHlsZT0iZm9udC1zaXplOi42NXJlbTtjb2xvcjp2YXIoLS10eDIpIj5iYWwg4oK5JHsoMjQ4"
    "LjUraSo3KS50b0ZpeGVkKDIpfTwvc21hbGw+YCl9PC9kaXY+PC9kaXY+YCkuam9pbigiIil9YDsK"
    "fQoKZnVuY3Rpb24gdllvdSgpewogIGNvbnN0IG1lPU1FfHx7fTsKICBjb25zdCBzcGVudD1tZS5z"
    "cGVudHx8MCwgdGFyZ2V0PXNwZW50Pj01MDA/MjAwMDo1MDA7CiAgY29uc3QgcGN0PU1hdGgubWF4"
    "KDQsTWF0aC5taW4oMTAwLE1hdGgucm91bmQoc3BlbnQvdGFyZ2V0KjEwMCkpKTsKICBjb25zdCBh"
    "dj1tZS5waG90bz9gPGltZyBzcmM9IiR7bWUucGhvdG99IiBhbHQ9IiIgc3R5bGU9IndpZHRoOjY0"
    "cHg7aGVpZ2h0OjY0cHg7Ym9yZGVyLXJhZGl1czo1MCU7b2JqZWN0LWZpdDpjb3ZlcjtkaXNwbGF5"
    "OmJsb2NrO21hcmdpbjowIGF1dG8gMTBweCIgb25lcnJvcj0idGhpcy5yZXBsYWNlV2l0aChPYmpl"
    "Y3QuYXNzaWduKGRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoJ2RpdicpLHtjbGFzc05hbWU6J2ZsYWcn"
    "LHN0eWxlOid3aWR0aDo2NHB4O2hlaWdodDo2NHB4O21hcmdpbjowIGF1dG8gMTBweCd9KSkiPmAK"
    "ICAgIDpgPGRpdiBjbGFzcz0iZmxhZyIgc3R5bGU9IndpZHRoOjY0cHg7aGVpZ2h0OjY0cHg7bWFy"
    "Z2luOjAgYXV0byAxMHB4Ij4ke2ljKCJ1c2VyIil9PC9kaXY+YDsKICByZXR1cm4gYDxkaXYgY2xh"
    "c3M9ImNhcmQgc2hpbmVjYXJkIHRpbHQiIGRhdGEtdGlsdCBzdHlsZT0idGV4dC1hbGlnbjpjZW50"
    "ZXIiPjxzcGFuIGNsYXNzPSJnbGFyZSI+PC9zcGFuPgogICAgJHthdn0KICAgIDxoMyBzdHlsZT0i"
    "Zm9udC1zaXplOjEuMDVyZW0iPiR7ZXNjKG1lLm5hbWV8fCJZb3UiKX08L2gzPgogICAgPHAgY2xh"
    "c3M9InN1YiI+JHtlc2MobWUudXNlcm5hbWV8fCgiSUQgIisobWUuaWR8fCLigJQiKSkpfSR7bWUu"
    "dXNlcm5hbWU/IiDCtyBJRCAiKyhtZS5pZHx8IuKAlCIpOiIifSR7bWUuam9pbmVkPyIgwrcgam9p"
    "bmVkICIrZXNjKG1lLmpvaW5lZCk6IiJ9PC9wPgogICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4"
    "O2dhcDo3cHg7anVzdGlmeS1jb250ZW50OmNlbnRlcjttYXJnaW4tdG9wOjEwcHg7ZmxleC13cmFw"
    "OndyYXAiPgogICAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+JHtlc2MobWUudGllcnx8IkJyb256"
    "ZSIpfTwvc3Bhbj48c3BhbiBjbGFzcz0icGlsbCI+JHttZS5vcmRlcnN8fDB9IG9yZGVyczwvc3Bh"
    "bj4KICAgICAgPHNwYW4gY2xhc3M9InBpbGwiPiR7bW9uZXkobWUuYmFsYW5jZXx8MCl9PC9zcGFu"
    "PgogICAgICAke0coImxveWFsdHkiLGA8c3BhbiBjbGFzcz0icGlsbCI+JHttZS5wb2ludHN8fDB9"
    "IHB0czwvc3Bhbj5gKX0KICAgICAgJHtHKCJzdHJlYWsiLGA8c3BhbiBjbGFzcz0icGlsbCI+JHtt"
    "ZS5zdHJlYWt8fDB9LWRheSBzdHJlYWs8L3NwYW4+YCl9CiAgICAgICR7KEZFQVQuYnV5ZXJCYWRn"
    "ZSYmKG1lLm9yZGVyc3x8MCk+PTUpP2A8c3BhbiBjbGFzcz0icGlsbCBvbiI+JHtpYygiY2hlY2si"
    "KX0gVmVyaWZpZWQgYnV5ZXI8L3NwYW4+YDoiIn0KICAgICAgJHsobWUuZGVidD4wKT9gPHNwYW4g"
    "Y2xhc3M9InBpbGwgaG90Ij4ke21vbmV5KG1lLmRlYnQpfSB1ZGhhcjwvc3Bhbj5gOiIifTwvZGl2"
    "PgogICAgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4O2hlaWdodDo3cHg7Ym9yZGVyLXJhZGl1"
    "czo5cHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7b3ZlcmZsb3c6aGlkZGVuIj4KICAgICAgPGkg"
    "c3R5bGU9ImRpc3BsYXk6YmxvY2s7aGVpZ2h0OjEwMCU7d2lkdGg6JHtwY3R9JTtiYWNrZ3JvdW5k"
    "OnZhcigtLXBpbGxncmFkKSI+PC9pPjwvZGl2PgogICAgPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1h"
    "cmdpbi10b3A6N3B4O2ZvbnQtc2l6ZTouNzJyZW0iPiR7bWUubmV4dF90aWVyPjAKICAgICAgPyBt"
    "b25leShtZS5uZXh0X3RpZXIpKyIgbW9yZSBzcGVudCB0byByZWFjaCAiKyhzcGVudD49NTAwPyJQ"
    "bGF0aW51bSI6IkdvbGQiKQogICAgICA6ICJUb3AgdGllciByZWFjaGVkIn08L3A+PC9kaXY+CiAg"
    "JHtHKCJ3aXNobGlzdCIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPlNhdmVkIGNvdW50cmllczwvaDI+"
    "PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJjYXJkIj4ke1dJU0gubGVuZ3RoP1dJU0gubWFwKGk9PmA8"
    "ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHtTVE9DS1tpXS5mfSAke2VzYyhTVE9DS1tpXS5jKX0gJHtT"
    "VE9DS1tpXS5jb2RlfTwvc3Bhbj4KICAgICAgPGI+PGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBkYXRh"
    "LWJ1eT0iJHtpfSI+QnV5PC9idXR0b24+PC9iPjwvZGl2PmApLmpvaW4oIiIpOic8cCBjbGFzcz0i"
    "c3ViIiBzdHlsZT0ibWFyZ2luOjAiPlRhcCB0aGUgaGVhcnQgb24gYW55IGNvdW50cnkgdG8gc2F2"
    "ZSBpdCBoZXJlLjwvcD4nfTwvZGl2PmApfQogIDxkaXYgY2xhc3M9InNlYyI+PGgyPkFjY291bnQ8"
    "L2gyPjwvZGl2PgogIDxkaXYgY2xhc3M9ImNhcmQiPiR7W1siT3JkZXIgaGlzdG9yeSIsIm9yZGVy"
    "cyJdLFsiV2FsbGV0ICYgcGF5bWVudHMiLCJ3YWxsZXQiXV0KICAgIC5jb25jYXQoYW55RigicmVm"
    "RGFzaCIsInJlZkwyIik/W1siUmVmZXJyYWwgcHJvZ3JhbSIsImVhcm4iXV06W10pCiAgICAubWFw"
    "KChbdCxnXSk9PmA8ZGl2IGNsYXNzPSJrdiIgc3R5bGU9ImN1cnNvcjpwb2ludGVyIiBkYXRhLWdv"
    "PSIke2d9Ij48c3BhbiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4ke3R9PC9zcGFuPjxiPiR7aWMo"
    "ImNoZXYiKX08L2I+PC9kaXY+YCkuam9pbigiIil9CiAgICAke0coImkxOG4iLGA8ZGl2IGNsYXNz"
    "PSJrdiI+PHNwYW4gc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+TGFuZ3VhZ2U8L3NwYW4+PGI+CiAg"
    "ICAgIDxzZWxlY3QgY2xhc3M9ImlucCIgc3R5bGU9ImhlaWdodDozMnB4O3dpZHRoOmF1dG87Zm9u"
    "dC1zaXplOi43NHJlbSI+PG9wdGlvbj5FbmdsaXNoPC9vcHRpb24+PG9wdGlvbj7gpLngpL/gpKjg"
    "pY3gpKbgpYA8L29wdGlvbj48L3NlbGVjdD48L2I+PC9kaXY+YCl9PC9kaXY+CiAgJHtHKCJub3Rp"
    "ZlByZWZzIixgPGRpdiBjbGFzcz0ic2VjIj48aDI+Tm90aWZpY2F0aW9uczwvaDI+PC9kaXY+CiAg"
    "PGRpdiBjbGFzcz0iY2FyZCI+CiAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5S"
    "ZXN0b2NrIGFsZXJ0czxzbWFsbD5ETSBtZSB3aGVuIG15IGNvdW50cnkgaXMgYmFjazwvc21hbGw+"
    "PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InRnICR7UFJFRlMucmVzdG9jaz8ib24iOiIifSIgZGF0"
    "YS1wcmVmPSJyZXN0b2NrIj48L2Rpdj48L2Rpdj4KICAgICR7RygicmVjZWlwdHMiLGA8ZGl2IGNs"
    "YXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5PcmRlciByZWNlaXB0czxzbWFsbD5TZW5kIGEgcmVj"
    "ZWlwdCBhZnRlciBlYWNoIGJ1eTwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InRnICR7"
    "UFJFRlMucmVjZWlwdHM/Im9uIjoiIn0iIGRhdGEtcHJlZj0icmVjZWlwdHMiPjwvZGl2PjwvZGl2"
    "PmApfQogICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+UHJvbW8gbWVzc2FnZXM8"
    "c21hbGw+Rmxhc2ggc2FsZXMgYW5kIGdpdmVhd2F5czwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYg"
    "Y2xhc3M9InRnICR7UFJFRlMucHJvbW9zPyJvbiI6IiJ9IiBkYXRhLXByZWY9InByb21vcyI+PC9k"
    "aXY+PC9kaXY+CiAgICAke0coInB1c2hOb3RpZiIsYDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xh"
    "c3M9InQiPk1pbmkgYXBwIHB1c2g8c21hbGw+TWVzc2FnZSBtZSB3aGVuIHNvbWV0aGluZyBoYXBw"
    "ZW5zPC9zbWFsbD48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0idGcgJHtQUkVGUy5wdXNoPyJvbiI6"
    "IiJ9IiBkYXRhLXByZWY9InB1c2giPjwvZGl2PjwvZGl2PmApfQogIDwvZGl2PmApfQogIDxkaXYg"
    "Y2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGJ1dHRvbiBjbGFzcz0iYnRu"
    "IHNvZnQiIGlkPSJzdXBwb3J0Z28iPlN1cHBvcnQ8L2J1dHRvbj4KICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4gZ2hvc3QiIGlkPSJ0ZXJtc2dvIj5UZXJtczwvYnV0dG9uPjwvZGl2PmA7Cn0KCmZ1bmN0aW9u"
    "IGJpbmRCdXkoKXsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS13aXNoXSIpLmZv"
    "ckVhY2goZWw9PmVsLm9uY2xpY2s9ZT0+ewogICAgZS5zdG9wUHJvcGFnYXRpb24oKTtjb25zdCBp"
    "PStlbC5kYXRhc2V0Lndpc2g7CiAgICBjb25zdCBrPVdJU0guaW5kZXhPZihpKTtpZihrPDApV0lT"
    "SC5wdXNoKGkpO2Vsc2UgV0lTSC5zcGxpY2UoaywxKTsKICAgIHRvYXN0KGs8MD8iU2F2ZWQgdG8g"
    "d2lzaGxpc3QiOiJSZW1vdmVkIGZyb20gd2lzaGxpc3QiKTthcHBseUZpbHRlcigpO30pOwogIGRv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWJ1eV0iKS5mb3JFYWNoKGVsPT5lbC5vbmNs"
    "aWNrPWU9PnsKICAgIGlmKGUudGFyZ2V0LmNsb3Nlc3QoIltkYXRhLXRvYXN0XSxbZGF0YS13aXNo"
    "XSIpKXJldHVybjtjb25zdCBzPVNUT0NLWytlbC5kYXRhc2V0LmJ1eV07CiAgICBpZihzLnF0eT09"
    "PTAmJiFmYWxzZSl7dG9hc3QoIkFsZXJ0IHNldCDigJQgeW91J2xsIGdldCBhIERNIG9uIHJlc3Rv"
    "Y2siKTtyZXR1cm59CiAgICBidXlTaGVldChzLHMucXR5PT09MCk7fSk7Cn0KbGV0IFFUWT0xLENP"
    "VVBPTj0wOwpmdW5jdGlvbiBidXlTaGVldChzLHByZSl7CiAgUVRZPTE7Q09VUE9OPTA7CiAgY29u"
    "c3QgbWF4PUZFQVQuY2FydHx8RkVBVC5idWxrRGlzYz9NYXRoLm1heCgxLE1hdGgubWluKDI1LHMu"
    "cXR5fHw1KSk6MTsKICBzaGVldChgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1z"
    "OmNlbnRlcjtnYXA6MTJweDttYXJnaW4tYm90dG9tOjE0cHgiPgogICAgICA8ZGl2IGNsYXNzPSJm"
    "bGFnIiBzdHlsZT0id2lkdGg6NTBweDtoZWlnaHQ6NTBweDtmb250LXNpemU6MS41cmVtIj4ke3Mu"
    "Zn08L2Rpdj4KICAgICAgPGRpdiBzdHlsZT0iZmxleDoxIj48aDMgc3R5bGU9Im1hcmdpbjowO2Zv"
    "bnQtc2l6ZToxcmVtIj4ke2VzYyhzLmMpfSBBY2NvdW50PC9oMz4KICAgICAgPHAgY2xhc3M9InN1"
    "YiIgc3R5bGU9Im1hcmdpbjowIj4ke3MuY29kZX0gwrcgJHtzLnF0eSsiIGluIHN0b2NrIMK3IGlu"
    "c3RhbnQifTwvcD4KICAgICAgJHtHKCJyZXZpZXdzIixgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDo0"
    "cHgiPiR7c3RhcnMocy5jKX08L2Rpdj5gKX08L2Rpdj48L2Rpdj4KICAgICR7RygiaGVhbHRoQ2hl"
    "Y2siLGA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+U2Vzc2lvbiBoZWFsdGg8L3NwYW4+PGIgc3R5bGU9"
    "ImNvbG9yOnZhcigtLW9rKSI+JHtpYygiY2hlY2siKX0gY2hlY2tlZCBsaXZlPC9iPjwvZGl2PmAp"
    "fQogICAgJHtHKCJ2YXJpYW50cyIsYDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5WYXJpYW50PC9zcGFu"
    "PjxiPgogICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIHN0eWxlPSJoZWlnaHQ6MzJweDt3aWR0aDph"
    "dXRvO2ZvbnQtc2l6ZTouNzRyZW0iPjxvcHRpb24+Tm8gMkZBPC9vcHRpb24+PG9wdGlvbj4yRkEg"
    "b24gKCvigrk1KTwvb3B0aW9uPjxvcHRpb24+V2l0aCBtYWlsICgr4oK5OCk8L29wdGlvbj48L3Nl"
    "bGVjdD48L2I+PC9kaXY+YCl9CiAgICAkeyhGRUFULmNhcnR8fEZFQVQuYnVsa0Rpc2MpP2A8ZGl2"
    "IGNsYXNzPSJrdiI+PHNwYW4+UXVhbnRpdHk8L3NwYW4+PGI+CiAgICAgIDxzcGFuIGNsYXNzPSJx"
    "dHkiPjxidXR0b24gaWQ9InFtIj7iiJI8L2J1dHRvbj48YiBpZD0icW4iPjE8L2I+PGJ1dHRvbiBp"
    "ZD0icXAiPis8L2J1dHRvbj48L3NwYW4+PC9iPjwvZGl2PmA6IiJ9CiAgICAke0coImJ1bGtEaXNj"
    "IixgPHAgY2xhc3M9InN1YiIgaWQ9ImJkbm90ZSIgc3R5bGU9Im1hcmdpbjo2cHggMCAwO2ZvbnQt"
    "c2l6ZTouN3JlbSI+QnV5IDUrIGZvciA1JSBvZmYgwrcgMTArIGZvciAxMCUgb2ZmIMK3IDI1KyBm"
    "b3IgMTUlIG9mZi48L3A+YCl9CiAgICAke0coImNvdXBvbnMiLGA8ZGl2IGNsYXNzPSJmaWVsZCIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTBweCI+PGxhYmVsPkNvdXBvbiBjb2RlPC9sYWJlbD4KICAgICAg"
    "PGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo3cHgiPjxpbnB1dCBjbGFzcz0iaW5wIiBpZD0i"
    "Y3BuIiBwbGFjZWhvbGRlcj0iV0VMQ09NRTEwIj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNt"
    "IiBpZD0iY3BuYXBwbHkiIHN0eWxlPSJmbGV4OjAgMCBhdXRvIj5BcHBseTwvYnV0dG9uPjwvZGl2"
    "PjwvZGl2PmApfQogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlByaWNlPC9zcGFuPjxiIGlkPSJi"
    "cHJpY2UiPiR7bW9uZXkocy5wcmljZSl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPllvdXIgYmFsYW5jZTwvc3Bhbj48Yj4ke21vbmV5KEJBTCl9PC9iPjwvZGl2PgogICAgPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPkFmdGVyIHB1cmNoYXNlPC9zcGFuPjxiIGlkPSJiYWZ0ZXIiPiR7"
    "bW9uZXkoTWF0aC5tYXgoMCxCQUwtcy5wcmljZSkpfTwvYj48L2Rpdj4KICAgIDxkaXYgaWQ9InNw"
    "bGl0bm90ZSI+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9w"
    "OjE0cHgiPjxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCIgZGF0YS1jbG9zZT5DYW5jZWw8L2J1dHRv"
    "bj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIgaWQ9ImJjb25maXJtIj5Db25maXJtIEJ1"
    "eTwvYnV0dG9uPjwvZGl2PgogICAgJHtHKCJjYXJ0IixgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQg"
    "YmxvY2sgc20iIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgaWQ9ImJhZGRjYXJ0Ij5BZGQgdG8gY2Fy"
    "dDwvYnV0dG9uPmApfWApOwogIGNvbnN0IGc9aWQ9PmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGlk"
    "KTsKICBmdW5jdGlvbiB0b3RhbCgpewogICAgbGV0IHVuaXQ9cy5wcmljZSwgcT1RVFksIHQ9dW5p"
    "dCpxOwogICAgaWYoRkVBVC5idWxrRGlzYyl7Y29uc3QgZD1xPj0yNT8uMTU6cT49MTA/LjEwOnE+"
    "PTU/LjA1OjA7dD10KigxLWQpO30KICAgIGlmKEZFQVQuY291cG9ucyYmQ09VUE9OKXQ9dCooMS1D"
    "T1VQT04vMTAwKTsKICAgIHJldHVybiBNYXRoLnJvdW5kKHQqMTAwKS8xMDA7CiAgfQogIGZ1bmN0"
    "aW9uIHBhaW50KCl7CiAgICBjb25zdCB0PXRvdGFsKCk7CiAgICBnKCJicHJpY2UiKS50ZXh0Q29u"
    "dGVudD1tb25leSh0KTsKICAgIGcoImJhZnRlciIpLnRleHRDb250ZW50PW1vbmV5KE1hdGgubWF4"
    "KDAsQkFMLXQpKTsKICAgIGNvbnN0IHNuPWcoInNwbGl0bm90ZSIpOwogICAgaWYoRkVBVC5zcGxp"
    "dFBheSYmdD5CQUwpe3NuLmlubmVySFRNTD1gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlNwbGl0IHBh"
    "eW1lbnQ8L3NwYW4+PGI+JHttb25leShCQUwpfSB3YWxsZXQgKyAke21vbmV5KHQtQkFMKX0gVVBJ"
    "PC9iPjwvZGl2PmA7fQogICAgZWxzZSBzbi5pbm5lckhUTUw9IiI7CiAgICBpZihnKCJxbiIpKWco"
    "InFuIikudGV4dENvbnRlbnQ9UVRZOwogIH0KICBpZihnKCJxbSIpKWcoInFtIikub25jbGljaz0o"
    "KT0+e1FUWT1NYXRoLm1heCgxLFFUWS0xKTtwYWludCgpO307CiAgaWYoZygicXAiKSlnKCJxcCIp"
    "Lm9uY2xpY2s9KCk9PntRVFk9TWF0aC5taW4obWF4LFFUWSsxKTtwYWludCgpO307CiAgaWYoZygi"
    "Y3BuIikmJk9GRkVSJiYhZygiY3BuIikudmFsdWUpZygiY3BuIikudmFsdWU9T0ZGRVI7CiAgaWYo"
    "ZygiY3BuYXBwbHkiKSlnKCJjcG5hcHBseSIpLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IHY9KGco"
    "ImNwbiIpLnZhbHVlfHwiIikudHJpbSgpLnRvVXBwZXJDYXNlKCk7CiAgICBpZighdil7dG9hc3Qo"
    "IkVudGVyIGEgY291cG9uIGNvZGUiKTtyZXR1cm59CiAgICBDT1VQT049dj09PSJXRUxDT01FMTAi"
    "PzEwOnY9PT0iU0FWRTIwIj8yMDowOwogICAgdG9hc3QoQ09VUE9OP0NPVVBPTisiJSBjb3Vwb24g"
    "YXBwbGllZCI6IkNvdXBvbiBub3QgdmFsaWQiKTtwYWludCgpO307CiAgaWYoZygiYmFkZGNhcnQi"
    "KSlnKCJiYWRkY2FydCIpLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYoTElWRSl7CiAgICAgIGNv"
    "bnN0IGQ9YXdhaXQgYXBpVHJ5KCIvYXBpL2NhcnQiLHthY3Rpb246ImFkZCIsY291bnRyeTpzLmMs"
    "cXR5OlFUWX0pOwogICAgICBpZighZClyZXR1cm47CiAgICAgIGNsb3NlU2hlZXQoKTt0b2FzdChR"
    "VFkrIiDDlyAiK3MuYysiIGFkZGVkIHRvIGNhcnQiKTthd2FpdCByZWZyZXNoKCk7cmV0dXJuOwog"
    "ICAgfQogICAgZm9yKGxldCBpPTA7aTxRVFk7aSsrKUNBUlQucHVzaChzKTthcHBseUZlYXR1cmVz"
    "KCk7Y2xvc2VTaGVldCgpO3RvYXN0KFFUWSsiIMOXICIrcy5jKyIgYWRkZWQgdG8gY2FydCIpO307"
    "CiAgZygiYmNvbmZpcm0iKS5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHQ9dG90YWwoKTsK"
    "ICAgIGlmKEJBTDx0JiYhRkVBVC5zcGxpdFBheSl7dG9hc3QoIk5vdCBlbm91Z2ggYmFsYW5jZSDi"
    "gJQgYWRkIGZ1bmRzIik7cmV0dXJufQogICAgaWYoQkFMPHQmJkZFQVQuc3BsaXRQYXkpe2Nsb3Nl"
    "U2hlZXQoKTt0b2FzdCgiUGF5aW5nICIrbW9uZXkodC1CQUwpKyIgYnkgVVBJIik7ZGVwb3NpdFNo"
    "ZWV0KE1hdGguY2VpbCh0LUJBTCkpO3JldHVybn0KICAgIGlmKExJVkUpewogICAgICBjb25zdCBi"
    "dG49ZygiYmNvbmZpcm0iKTtidG4uZGlzYWJsZWQ9dHJ1ZTtidG4udGV4dENvbnRlbnQ9Ildvcmtp"
    "bmfigKYiOwogICAgICBjb25zdCBjb2RlPWcoImNwbiIpPyhnKCJjcG4iKS52YWx1ZXx8IiIpLnRy"
    "aW0oKToiIjsKICAgICAgY29uc3QgZD1hd2FpdCBhcGlUcnkoIi9hcGkvYnV5Iix7Y291bnRyeTpz"
    "LmMscXR5OlFUWSxjb3Vwb246Y29kZX0pOwogICAgICBidG4uZGlzYWJsZWQ9ZmFsc2U7YnRuLnRl"
    "eHRDb250ZW50PSJDb25maXJtIEJ1eSI7CiAgICAgIGlmKCFkKXJldHVybjsKICAgICAgaWYodHlw"
    "ZW9mIGQuYmFsYW5jZT09PSJudW1iZXIiKUJBTD1kLmJhbGFuY2U7CiAgICAgIHN5bmNCYWwoKTtj"
    "bG9zZVNoZWV0KCk7YnVyc3QoKTsKICAgICAgY29uc3QgZmlyc3Q9KGQub3JkZXJzJiZkLm9yZGVy"
    "c1swXSl8fG51bGw7CiAgICAgIHRvYXN0KGZpcnN0PygiRGVsaXZlcmVkIOKAlCAiK2ZpcnN0Lmlk"
    "KToiUHVyY2hhc2VkIOKAlCBkZXRhaWxzIGluIE9yZGVycyIpOwogICAgICBhd2FpdCByZWZyZXNo"
    "KCk7CiAgICAgIGlmKGZpcnN0JiZmaXJzdC5wYXlsb2FkKXNldFRpbWVvdXQoKCk9PnNoZWV0KGA8"
    "aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj4ke2VzYyhmaXJzdC5pZCl9"
    "PC9oMz4KICAgICAgICA8cCBjbGFzcz0ic3ViIj5LZWVwIHRoaXMgc2FmZS4gSXQgaXMgYWxzbyBp"
    "biBPcmRlcnMuPC9wPgogICAgICAgIDxkaXYgY2xhc3M9Imt2IiBzdHlsZT0iZGlzcGxheTpibG9j"
    "ayI+PGNvZGUgc3R5bGU9ImZvbnQtc2l6ZTouNzJyZW07d29yZC1icmVhazpicmVhay1hbGwiPiR7"
    "ZXNjKGZpcnN0LnBheWxvYWQpfTwvY29kZT48L2Rpdj4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiIGRhdGEtY2xvc2U+RG9uZTwvYnV0dG9u"
    "PmApLDM1MCk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIEJBTC09dDtzeW5jQmFsKCk7Y2xvc2VT"
    "aGVldCgpO2J1cnN0KCk7CiAgICB0b2FzdCgiUHVyY2hhc2VkIOKAlCBPVFAgaXMgaW4gT3JkZXJz"
    "Iik7CiAgICBpZihGRUFULnB1c2hOb3RpZilzZXRUaW1lb3V0KCgpPT50b2FzdCgiQm90IG5vdGlm"
    "aWNhdGlvbiBzZW50IHRvIHlvdXIgY2hhdCIpLDE1MDApO307CiAgcGFpbnQoKTsKfQpmdW5jdGlv"
    "biBjYXJ0U2hlZXQoKXsKICBpZighQ0FSVC5sZW5ndGgpe3RvYXN0KCJDYXJ0IGlzIGVtcHR5Iik7"
    "cmV0dXJufQogIGNvbnN0IHQ9Q0FSVC5yZWR1Y2UoKGEsYik9PmErYi5wcmljZSwwKTsKICBzaGVl"
    "dChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+WW91ciBjYXJ0PC9o"
    "Mz4KICAgIDxwIGNsYXNzPSJzdWIiPiR7Q0FSVC5sZW5ndGh9IGl0ZW0ke0NBUlQubGVuZ3RoPjE/"
    "InMiOiIifSDigJQgb25lIGNvbmZpcm1hdGlvbiwgb25lIGRlYml0LjwvcD4KICAgICR7Q0FSVC5t"
    "YXAoKHMsaSk9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHtzLmZ9ICR7ZXNjKHMuYyl9ICR7cy5j"
    "b2RlfTwvc3Bhbj4KICAgICAgPGI+JHttb25leShzLnByaWNlKX0gPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IHhzIGdob3N0IiBkYXRhLWNkZWw9IiR7aX0iPsOXPC9idXR0b24+PC9iPjwvZGl2PmApLmpvaW4o"
    "IiIpfQogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlRvdGFsPC9zcGFuPjxiPiR7bW9uZXkodCl9"
    "PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4"
    "Ij48YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj4KICAg"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIgaWQ9ImNhcnRidXkiPkNoZWNrb3V0PC9idXR0b24+"
    "PC9kaXY+YCk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2RlbF0iKS5mb3JF"
    "YWNoKGI9PmIub25jbGljaz0oKT0+e0NBUlQuc3BsaWNlKCtiLmRhdGFzZXQuY2RlbCwxKTthcHBs"
    "eUZlYXR1cmVzKCk7Y2FydFNoZWV0KCk7fSk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNh"
    "cnRidXkiKS5vbmNsaWNrPSgpPT57CiAgICBpZihCQUw8dCl7dG9hc3QoIk5vdCBlbm91Z2ggYmFs"
    "YW5jZSDigJQgYWRkIGZ1bmRzIik7cmV0dXJufQogICAgQkFMLT10O0NBUlQ9W107c3luY0JhbCgp"
    "O2FwcGx5RmVhdHVyZXMoKTtjbG9zZVNoZWV0KCk7YnVyc3QoKTt0b2FzdCgiT3JkZXIgcGxhY2Vk"
    "IOKAlCBPVFBzIGFyZSBpbiBPcmRlcnMiKTt9Owp9CmZ1bmN0aW9uIGdvVGFiKHQpewogIGNvbnN0"
    "IGI9ZG9jdW1lbnQucXVlcnlTZWxlY3RvcihgI3RhYnMgYnV0dG9uW2RhdGEtdGFiPSIke3R9Il1g"
    "KTsKICBpZihiKWIuY2xpY2soKTsKfQpmdW5jdGlvbiBiaW5kQ29tbW9uKCl7CiAgZG9jdW1lbnQu"
    "cXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtb3JkXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5j"
    "KCk9PnsKICAgIGNvbnN0IHdoYXQ9Yi5kYXRhc2V0Lm9yZDsKICAgIGNvbnN0IGNhcmQ9Yi5jbG9z"
    "ZXN0KCJbZGF0YS1vcmRlci1pZF0iKTsKICAgIGNvbnN0IG9pZD0oY2FyZCYmY2FyZC5kYXRhc2V0"
    "Lm9yZGVySWQpfHwoT1JERVJTWzBdJiZPUkRFUlNbMF0uaWQpfHwiIjsKICAgIGlmKCFvaWQpe3Rv"
    "YXN0KCJObyBvcmRlciBzZWxlY3RlZCIpO3JldHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlBy"
    "ZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBjb25zdCBsYmw9Yi50ZXh0Q29udGVudDtiLmRpc2Fi"
    "bGVkPXRydWU7Yi50ZXh0Q29udGVudD0i4oCmIjsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIv"
    "YXBpL29yZGVyIix7aWQ6b2lkLHdoYXQ6d2hhdH0pOwogICAgYi5kaXNhYmxlZD1mYWxzZTtiLnRl"
    "eHRDb250ZW50PWxibDsKICAgIGlmKCFyKXJldHVybjsKICAgIGlmKHdoYXQ9PT0iZGV0YWlscyIp"
    "dG9hc3QoIkxvZ2luIGRldGFpbHMgc2VudCB0byB5b3VyIGNoYXQiKTsKICAgIGlmKHdoYXQ9PT0i"
    "ZmlsZSIpdG9hc3QoIlNlc3Npb24gZmlsZSBzZW50IHRvIHlvdXIgY2hhdCIpOwogICAgaWYod2hh"
    "dD09PSJyZWNlaXB0Iil0b2FzdCgiUmVjZWlwdCBzZW50IHRvIHlvdXIgY2hhdCIpOwogIH0pOwog"
    "IGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXJhdGUtZ29dIikuZm9yRWFjaChiPT5i"
    "Lm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3Qgc3RhcnM9K2IuZGF0YXNldC5yYXRlR287CiAg"
    "ICBjb25zdCBjPSh3aW5kb3cuX19yYXRlQ291bnRyeSl8fCgoT1JERVJTWzBdfHx7fSkuYyl8fCIi"
    "OwogICAgaWYoIUxJVkUpe2Nsb3NlU2hlZXQoKTt0b2FzdCgiVGhhbmtzIGZvciB0aGUgIitzdGFy"
    "cysi4piFIik7cmV0dXJuO30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL3JldmlldyIs"
    "e2NvdW50cnk6YyxyYXRpbmc6c3RhcnN9KTsKICAgIGlmKCFyKXJldHVybjsKICAgIGNsb3NlU2hl"
    "ZXQoKTt0b2FzdCgiVGhhbmtzIOKAlCAiK2MrIiBpcyBub3cgIityLmF2Zysi4piFIik7CiAgICBh"
    "d2FpdCByZWZyZXNoKCk7CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEt"
    "YWN0XSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHdoYXQ9Yi5k"
    "YXRhc2V0LmFjdDsKICAgIGlmKHdoYXQ9PT0iY2hhbm5lbCIpewogICAgICBjb25zdCBjPShBRE0u"
    "Y2hhbm5lbHMmJkFETS5jaGFubmVsc1swXSl8fG51bGw7CiAgICAgIGNvbnN0IGg9Yz9TdHJpbmco"
    "Yy5oYW5kbGV8fCIiKS5yZXBsYWNlKC9eQC8sIiIpOiIiOwogICAgICBpZihoKW9wZW5QYXkoaC5z"
    "dGFydHNXaXRoKCJodHRwIik/aDooImh0dHBzOi8vdC5tZS8iK2gpKTsKICAgICAgZWxzZSB0b2Fz"
    "dCgiTm8gY2hhbm5lbCBoYXMgYmVlbiBhZGRlZCB5ZXQiKTsKICAgICAgcmV0dXJuOwogICAgfQog"
    "ICAgaWYod2hhdD09PSJmbGFzaCIpewogICAgICBjb25zdCBvbj1BRE0ubWFya2V0JiZBRE0ubWFy"
    "a2V0LmZsYXNoX29uOwogICAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250"
    "LXNpemU6MXJlbSI+Rmxhc2ggc2FsZTwvaDM+CiAgICAgICAgPHAgY2xhc3M9InN1YiI+JHtvbj8o"
    "IkV2ZXJ5IHByaWNlIGlzICIrKEFETS5tYXJrZXQuZmxhc2h8fDApKyIlIG9mZiByaWdodCBub3cu"
    "Iik6Ik5vIHNhbGUgaXMgcnVubmluZyBhdCB0aGUgbW9tZW50LiJ9PC9wPgogICAgICAgIDxidXR0"
    "b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9zZT5D"
    "bG9zZTwvYnV0dG9uPmApOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09InNvcnQi"
    "KXsKICAgICAgRklMVEVSRUQ9KEZJTFRFUkVEJiZGSUxURVJFRC5sZW5ndGg/RklMVEVSRUQ6U1RP"
    "Q0spLnNsaWNlKCkuc29ydCgoeCx5KT0+KHkucXR5fHwwKS0oeC5xdHl8fDApKTsKICAgICAgcmVu"
    "ZGVyKCk7dG9hc3QoIlNvcnRlZCBieSB3aGF0IGlzIGluIHN0b2NrIik7CiAgICAgIHJldHVybjsK"
    "ICAgIH0KICAgIGlmKHdoYXQ9PT0iYWxlcnRzIil7CiAgICAgIGNvbnN0IG1pbmU9KHdpbmRvdy5f"
    "X2FsZXJ0cyl8fFtdOwogICAgICBjb25zdCBvZmY9U1RPQ0suZmlsdGVyKHg9PiF4LnF0eSkubWFw"
    "KHg9PnguYyk7CiAgICAgIGNvbnN0IGxpc3Q9KG9mZi5sZW5ndGg/b2ZmOlNUT0NLLm1hcCh4PT54"
    "LmMpKS5zbGljZSgwLDgpOwogICAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtm"
    "b250LXNpemU6MXJlbSI+UmVzdG9jayBhbGVydHM8L2gzPgogICAgICAgIDxwIGNsYXNzPSJzdWIi"
    "PldlIG1lc3NhZ2UgeW91IHRoZSBtb21lbnQgb25lIG9mIHRoZXNlIGlzIGJhY2suPC9wPgogICAg"
    "ICAgICR7bGlzdC5tYXAoYz0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj4ke2VzYyhjKX08L3NwYW4+"
    "CiAgICAgICAgICA8Yj48YnV0dG9uIGNsYXNzPSJidG4geHMgJHttaW5lLmluZGV4T2YoYyk+PTA/"
    "IiI6InNvZnQifSIgZGF0YS1hbGVydD0iJHtlc2MoYyl9Ij4KICAgICAgICAgICAgJHttaW5lLmlu"
    "ZGV4T2YoYyk+PTA/Ik9uIjoiTm90aWZ5IG1lIn08L2J1dHRvbj48L2I+PC9kaXY+YCkuam9pbigi"
    "Iil9CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "MnB4IiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRvbj5gKTsKICAgICAgZG9jdW1lbnQucXVlcnlTZWxl"
    "Y3RvckFsbCgiW2RhdGEtYWxlcnRdIikuZm9yRWFjaChhPT5hLm9uY2xpY2s9YXN5bmMoKT0+ewog"
    "ICAgICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgICAgICBj"
    "b25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hbGVydCIse2NvdW50cnk6YS5kYXRhc2V0LmFsZXJ0"
    "fSk7CiAgICAgICAgaWYoIXIpcmV0dXJuOwogICAgICAgIHdpbmRvdy5fX2FsZXJ0cz1yLmFsZXJ0"
    "c3x8W107CiAgICAgICAgYS50ZXh0Q29udGVudD1yLm9uPyJPbiI6Ik5vdGlmeSBtZSI7CiAgICAg"
    "ICAgYS5jbGFzc0xpc3QudG9nZ2xlKCJzb2Z0Iiwhci5vbik7CiAgICAgICAgdG9hc3Qoci5vbj8o"
    "IldlIHdpbGwgdGVsbCB5b3Ugd2hlbiAiK2EuZGF0YXNldC5hbGVydCsiIGlzIGJhY2siKToiQWxl"
    "cnQgcmVtb3ZlZCIpOwogICAgICB9KTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09"
    "PSJjc3YiKXsKICAgICAgY29uc3Qgcm93cz1bWyJvcmRlciIsImNvdW50cnkiLCJwcmljZSIsInN0"
    "YXR1cyIsIndoZW4iXV0KICAgICAgICAuY29uY2F0KE9SREVSUy5tYXAobz0+W28uaWQsby5jLG8u"
    "cHJpY2Usby5zdCxvLndoZW5dKSk7CiAgICAgIGNvbnN0IGNzdj1yb3dzLm1hcChyPT5yLmpvaW4o"
    "IiwiKSkuam9pbigiXG4iKTsKICAgICAgY29uc3QgYT1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCJh"
    "Iik7CiAgICAgIGEuaHJlZj0iZGF0YTp0ZXh0L2NzdjtjaGFyc2V0PXV0Zi04LCIrZW5jb2RlVVJJ"
    "Q29tcG9uZW50KGNzdik7CiAgICAgIGEuZG93bmxvYWQ9Im15LW9yZGVycy5jc3YiO2RvY3VtZW50"
    "LmJvZHkuYXBwZW5kQ2hpbGQoYSk7YS5jbGljaygpO2EucmVtb3ZlKCk7CiAgICAgIHRvYXN0KE9S"
    "REVSUy5sZW5ndGgrIiBvcmRlcnMgZG93bmxvYWRlZCIpOwogICAgICByZXR1cm47CiAgICB9CiAg"
    "ICBpZih3aGF0PT09InN1cHBvcnQiKXsKICAgICAgY29uc3QgaD0oU1RPUkUuc3VwcG9ydHx8IiIp"
    "LnJlcGxhY2UoL15ALywiIik7CiAgICAgIGlmKGgpb3BlblBheSgiaHR0cHM6Ly90Lm1lLyIraCk7"
    "CiAgICAgIGVsc2UgdG9hc3QoIlNlbmQgeW91ciBxdWVzdGlvbiBpbiB0aGUgY2hhdCBhbmQgd2Ug"
    "d2lsbCByZXBseSIpOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09InRhc2siKXsK"
    "ICAgICAgY29uc3Qgcm93PWIuY2xvc2VzdCgiLmNhcmQiKTsKICAgICAgY29uc3QgbGFiZWw9cm93"
    "Pyhyb3cucXVlcnlTZWxlY3RvcigiYiIpfHx7fSkudGV4dENvbnRlbnR8fCIiOiIiOwogICAgICBp"
    "ZigvY2hhbm5lbC9pLnRlc3QobGFiZWwpKXtiLmJsdXIoKTtkb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "KCdbZGF0YS1hY3Q9ImNoYW5uZWwiXScpPy5jbGljaygpO3JldHVybjt9CiAgICAgIGlmKC9vcmRl"
    "ci9pLnRlc3QobGFiZWwpKXtnb1RhYigic2hvcCIpO3RvYXN0KCJQaWNrIGEgY291bnRyeSB0byBw"
    "bGFjZSB5b3VyIGZpcnN0IG9yZGVyIik7cmV0dXJuO30KICAgICAgZ29UYWIoImVhcm4iKTt0b2Fz"
    "dCgiU2hhcmUgeW91ciBpbnZpdGUgbGluayB0byBmaW5pc2ggdGhpcyIpOwogICAgICByZXR1cm47"
    "CiAgICB9CiAgICBpZih3aGF0PT09InN0YXR1cyIpewogICAgICBjb25zdCBnPShBRE0ucGF5JiZB"
    "RE0ucGF5LmdtYWlsX3NldCk7CiAgICAgIGNvbnN0IHBlbmQ9KEFETS5wYXkmJkFETS5wYXkucGVu"
    "ZGluZyl8fDA7CiAgICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6"
    "ZToxcmVtIj5QYXltZW50IGNoZWNraW5nPC9oMz4KICAgICAgICA8cCBjbGFzcz0ic3ViIj4ke2c/"
    "IkF1dG9tYXRpYyBjaGVja2luZyBpcyBvbi4gUGF5LCB0aGVuIHRhcCBWZXJpZnkuIjoKICAgICAg"
    "ICAgICJBdXRvbWF0aWMgY2hlY2tpbmcgaXMgbm90IHNldCB1cCwgc28gYW4gYWRtaW4gY29uZmly"
    "bXMgcGF5bWVudHMgYnkgaGFuZC4ifTwvcD4KICAgICAgICAke3BlbmQ/YDxkaXYgY2xhc3M9Imt2"
    "Ij48c3Bhbj5XYWl0aW5nIGZvciBhbiBhZG1pbjwvc3Bhbj48Yj4ke3BlbmR9PC9iPjwvZGl2PmA6"
    "IiJ9CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "MnB4IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+YCk7CiAgICAgIHJldHVybjsKICAgIH0KICAg"
    "IGlmKHdoYXQ9PT0iY29weWNvZGUiKXsKICAgICAgY29uc3QgZWw9Yi5jbG9zZXN0KCIuY2FyZCwu"
    "c2hlZXRib2R5LCNzaGVldCIpfHxkb2N1bWVudDsKICAgICAgY29uc3QgY29kZT0oZWwucXVlcnlT"
    "ZWxlY3RvcigiY29kZSIpfHx7fSkudGV4dENvbnRlbnR8fCIiOwogICAgICBjb3B5VGV4dChjb2Rl"
    "LnRyaW0oKSwiQ29kZSIpOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09InJlcGxh"
    "Y2UiKXsKICAgICAgY29uc3QgY2FyZD1iLmNsb3Nlc3QoIltkYXRhLW9yZGVyLWlkXSIpOwogICAg"
    "ICBjb25zdCBvaWQ9KGNhcmQmJmNhcmQuZGF0YXNldC5vcmRlcklkKXx8KE9SREVSU1swXSYmT1JE"
    "RVJTWzBdLmlkKXx8IiI7CiAgICAgIGlmKCFMSVZFKXtjbG9zZVNoZWV0KCk7dG9hc3QoIlJlcGxh"
    "Y2VtZW50IHNlbnQiKTtyZXR1cm47fQogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9v"
    "cmRlciIse2lkOm9pZCx3aGF0OiJyZXBsYWNlIn0pOwogICAgICBpZighcilyZXR1cm47CiAgICAg"
    "IGNsb3NlU2hlZXQoKTsKICAgICAgdG9hc3Qoci5yZWZ1bmRlZD8iTm8gc3RvY2sgbGVmdCDigJQg"
    "cmVmdW5kZWQgdG8geW91ciB3YWxsZXQiOigiUmVwbGFjZWQg4oCUICIrci5vcmRlcikpOwogICAg"
    "ICBhd2FpdCByZWZyZXNoKCk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9PT0iY3J5"
    "cHRvIil7CiAgICAgIGlmKCFMSVZFKXtjbG9zZVNoZWV0KCk7dG9hc3QoIldlIHdpbGwgY29uZmly"
    "bSB5b3VyIHRyYW5zZmVyIHNob3J0bHkiKTtyZXR1cm47fQogICAgICBjb25zdCByPWF3YWl0IGFw"
    "aVRyeSgiL2FwaS9jcnlwdG8iLHtyZWY6IiJ9KTsKICAgICAgaWYocil7Y2xvc2VTaGVldCgpO3Rv"
    "YXN0KCJXZSB3aWxsIGNvbmZpcm0geW91ciB0cmFuc2ZlciBzaG9ydGx5Iik7fQogICAgICByZXR1"
    "cm47CiAgICB9CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY29weV0i"
    "KS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgY29uc3Qgd2hhdD1iLmRhdGFzZXQuY29w"
    "eTsKICAgIGlmKHdoYXQ9PT0idXBpIilyZXR1cm4gY29weVRleHQoU1RPUkUudXBpLCJVUEkgSUQi"
    "KTsKICAgIGlmKHdoYXQ9PT0icmVmIil7CiAgICAgIGxldCBsPUVBUk4ubGlua3x8IiI7CiAgICAg"
    "IGlmKGwmJiEvXmh0dHBzPzovLnRlc3QobCkpbD0iaHR0cHM6Ly8iK2w7CiAgICAgIHJldHVybiBj"
    "b3B5VGV4dChsLCJJbnZpdGUgbGluayIpOwogICAgfQogICAgcmV0dXJuIGNvcHlUZXh0KGIuZGF0"
    "YXNldC5jb3B5VmFsdWV8fGIudGV4dENvbnRlbnQudHJpbSgpLCJDb3BpZWQiKTsKICB9KTsKICBj"
    "b25zdCByZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicmVkZWVtZ28iKTsKICBpZihyZClyZC5v"
    "bmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKCFMSVZFKXt0b2FzdCgiUmVkZWVtZWQiKTtyZXR1cm47"
    "fQogICAgcmQuZGlzYWJsZWQ9dHJ1ZTsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL3Jl"
    "ZGVlbSIse30pOwogICAgcmQuZGlzYWJsZWQ9ZmFsc2U7CiAgICBpZighcilyZXR1cm47CiAgICBC"
    "QUw9ci5iYWxhbmNlO3N5bmNCYWwoKTtidXJzdCgpOwogICAgdG9hc3Qoci5wb2ludHMrIiBwb2lu"
    "dHMg4oaSICIrbW9uZXkoci5hbW91bnQpKTsKICAgIGF3YWl0IHJlZnJlc2goKTsKICB9OwogIGNv"
    "bnN0IG91PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJvZmZlcnVzZSIpOwogIGlmKG91KW91Lm9u"
    "Y2xpY2s9KCk9PnsKICAgIGNvbnN0IGM9KEFETS5jb3Vwb25zJiZBRE0uY291cG9ucy5sZW5ndGgp"
    "P0FETS5jb3Vwb25zWzBdLmNvZGU6IiI7CiAgICBpZihjKXtPRkZFUj1jO2NvcHlUZXh0KGMsIkNv"
    "dXBvbiIpO30KICAgIGVsc2UgdG9hc3QoIk5vIGNvdXBvbiBpcyBydW5uaW5nIHJpZ2h0IG5vdyIp"
    "OwogIH07CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtcHJlZl0iKS5mb3JFYWNo"
    "KHQ9PnQub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBrZXk9dC5kYXRhc2V0LnByZWYsIG9u"
    "PSF0LmNsYXNzTGlzdC5jb250YWlucygib24iKTsKICAgIHQuY2xhc3NMaXN0LnRvZ2dsZSgib24i"
    "LG9uKTtQUkVGU1trZXldPW9uOwogICAgaWYoTElWRSl7CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBp"
    "VHJ5KCIvYXBpL3ByZWZzIix7a2V5OmtleSxvbjpvbn0pOwogICAgICBpZighcil7dC5jbGFzc0xp"
    "c3QudG9nZ2xlKCJvbiIsIW9uKTtQUkVGU1trZXldPSFvbjtyZXR1cm47fQogICAgfQogICAgdG9h"
    "c3Qob24/IlR1cm5lZCBvbiI6IlR1cm5lZCBvZmYiKTsKICB9KTsKICBjb25zdCB0Z2I9ZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoInRlcm1zZ28iKTsKICBpZih0Z2IpdGdiLm9uY2xpY2s9KCk9PnNo"
    "ZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5UZXJtcyAmIHJl"
    "ZnVuZHM8L2gzPgogICAgPHAgY2xhc3M9InN1YiIgc3R5bGU9IndoaXRlLXNwYWNlOnByZS13cmFw"
    "Ij4ke2VzYyhTVE9SRS50ZXJtc3x8Q01TLnRlcm1zfHwiTm8gdGVybXMgaGF2ZSBiZWVuIHdyaXR0"
    "ZW4geWV0LiIpfTwvcD4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdp"
    "bi10b3A6MTJweCIgZGF0YS1jbG9zZT5DbG9zZTwvYnV0dG9uPmApOwogIGNvbnN0IHNnPWRvY3Vt"
    "ZW50LmdldEVsZW1lbnRCeUlkKCJzdXBwb3J0Z28iKTsKICBpZihzZylzZy5vbmNsaWNrPSgpPT57"
    "CiAgICBjb25zdCBoPShTVE9SRS5zdXBwb3J0fHwiIikucmVwbGFjZSgvXkAvLCIiKTsKICAgIGlm"
    "KGgpb3BlblBheSgiaHR0cHM6Ly90Lm1lLyIraCk7CiAgICBlbHNlIHNoZWV0KGA8aDMgc3R5bGU9"
    "Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5TdXBwb3J0PC9oMz4KICAgICAgPHAgY2xh"
    "c3M9InN1YiI+U2VuZCB5b3VyIHF1ZXN0aW9uIGluIHRoZSBjaGF0IGFuZCB3ZSB3aWxsIHJlcGx5"
    "LjwvcD4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "MnB4IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+YCk7CiAgfTsKfQpmdW5jdGlvbiBiaW5kU3Rh"
    "Z2UoKXsKICBiaW5kQ29tbW9uKCk7CiAgYmluZEJ1eSgpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIltkYXRhLXNjcm9sbF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoYi5kYXRhc2V0LnNjcm9sbCkuc2Nyb2xsSW50b1ZpZXcoe2JlaGF2aW9y"
    "OiJzbW9vdGgifSkpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLW90cF0iKS5m"
    "b3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBjYXJkPWIuY2xvc2VzdCgi"
    "W2RhdGEtb3JkZXItaWRdIik7CiAgICBjb25zdCBvaWQ9KGNhcmQmJmNhcmQuZGF0YXNldC5vcmRl"
    "cklkKXx8Yi5kYXRhc2V0Lm90cHx8IiI7CiAgICBjb25zdCBvcmQ9T1JERVJTLmZpbmQoeD0+eC5p"
    "ZD09PW9pZCl8fHt9OwogICAgaWYoIUxJVkUpewogICAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJn"
    "aW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+TG9naW4gY29kZTwvaDM+CiAgICAgICAgPHAgY2xh"
    "c3M9InN1YiI+U2FtcGxlIGNvZGUg4oCUIHRoaXMgaXMgdGhlIHByZXZpZXcuPC9wPgogICAgICAg"
    "IDxkaXYgY2xhc3M9Im90cGJveCI+NCAyIDkgMSA3PC9kaXY+CiAgICAgICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4IiBkYXRhLWNsb3NlPkRvbmU8L2J1"
    "dHRvbj5gKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgY29uc3QgbGJsPWIudGV4dENvbnRlbnQ7"
    "Yi5kaXNhYmxlZD10cnVlO2IudGV4dENvbnRlbnQ9IkZldGNoaW5n4oCmIjsKICAgIGNvbnN0IHI9"
    "YXdhaXQgYXBpVHJ5KCIvYXBpL29yZGVyIix7aWQ6b2lkLHdoYXQ6Im90cCJ9KTsKICAgIGIuZGlz"
    "YWJsZWQ9ZmFsc2U7Yi50ZXh0Q29udGVudD1sYmw7CiAgICBpZighcilyZXR1cm47CiAgICBzaGVl"
    "dChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+TG9naW4gY29kZTwv"
    "aDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiPiR7b3JkLnBob25lPygiRm9yICIrZXNjKG9yZC5waG9u"
    "ZSkpOiJGb3IgdGhpcyBhY2NvdW50In08L3A+CiAgICAgIDxkaXYgY2xhc3M9Im90cGJveCI+JHtT"
    "dHJpbmcoci5jb2RlKS5zcGxpdCgiIikuam9pbigiICIpfTwvZGl2PgogICAgICA8ZGl2IGNsYXNz"
    "PSJrdiI+PHNwYW4+Q29kZXMgbGVmdDwvc3Bhbj48Yj4ke3IubGVmdH08L2I+PC9kaXY+CiAgICAg"
    "ICR7ci53aW5kb3c/YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Gb3J3YXJkaW5nIGVuZHMgaW48L3Nw"
    "YW4+PGI+JHtyLndpbmRvd30gbWluPC9iPjwvZGl2PmA6IiJ9CiAgICAgIDxkaXYgY2xhc3M9ImJ0"
    "bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IHNvZnQiIGRhdGEtYWN0PSJjb3B5Y29kZSI+Q29weSBDb2RlPC9idXR0b24+CiAgICAgICAgPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRvbj48L2Rpdj5gKTsKICB9KTsK"
    "ICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1jbGFpbV0iKS5mb3JFYWNoKGI9PmIu"
    "b25jbGljaz1hc3luYygpPT57CiAgICBpZihiLmRpc2FibGVkKXJldHVybjsKICAgIGIuZGlzYWJs"
    "ZWQ9dHJ1ZTsKICAgIGlmKExJVkUpewogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9j"
    "aGVja2luIix7fSk7CiAgICAgIGlmKCFyKXtiLmRpc2FibGVkPWZhbHNlO3JldHVybjt9CiAgICAg"
    "IEJBTD10eXBlb2Ygci5iYWxhbmNlPT09Im51bWJlciI/ci5iYWxhbmNlOkJBTDsKICAgICAgRUFS"
    "Ti5jbGFpbWVkPXRydWU7RUFSTi5zdHJlYWs9ci5zdHJlYWt8fEVBUk4uc3RyZWFrOwogICAgICBz"
    "eW5jQmFsKCk7YnVyc3QoKTt0b2FzdChtb25leShyLmFtb3VudCkrIiBhZGRlZCDCtyBkYXkgIity"
    "LnN0cmVhayk7CiAgICAgIGIudGV4dENvbnRlbnQ9IkNsYWltZWQgdG9kYXkiOwogICAgICBhd2Fp"
    "dCByZWZyZXNoKCk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIEJBTCs9RUFSTi5kYWlseTtzeW5j"
    "QmFsKCk7YnVyc3QoKTt0b2FzdChtb25leShFQVJOLmRhaWx5KSsiIGFkZGVkIik7CiAgICBiLnRl"
    "eHRDb250ZW50PSJDbGFpbWVkIHRvZGF5Ijt9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxs"
    "KCJbZGF0YS1hbXRdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PmRlcG9zaXRTaGVldChiLmRh"
    "dGFzZXQuYW10KSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZGVwXSIpLmZv"
    "ckVhY2goYj0+Yi5vbmNsaWNrPSgpPT5hbW91bnRTaGVldCgpKTsKICBkb2N1bWVudC5xdWVyeVNl"
    "bGVjdG9yQWxsKCJbZGF0YS10Z10iKS5mb3JFYWNoKHQ9PnQub25jbGljaz0oKT0+dC5jbGFzc0xp"
    "c3QudG9nZ2xlKCJvbiIpKTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1zZXRz"
    "ZWxdIikuZm9yRWFjaChzZWw9PnNlbC5vbmNoYW5nZT1hc3luYygpPT57CiAgICBpZighTElWRSl7"
    "dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgi"
    "L2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OnNlbC5kYXRhc2V0LnNldHNlbCx2YWx1ZTpzZWwudmFs"
    "dWV9KTsKICAgIGlmKHIpdG9hc3QoIlNhdmVkIik7CiAgfSk7CiAgY29uc3QgcT1kb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgicSIpOwogIGlmKHEpe3EudmFsdWU9UVVFUlk7cS5vbmlucHV0PSgpPT57"
    "UVVFUlk9cS52YWx1ZS50b0xvd2VyQ2FzZSgpO2FwcGx5RmlsdGVyKCl9O30KICBkb2N1bWVudC5x"
    "dWVyeVNlbGVjdG9yQWxsKCIjY2hpcHMgYnV0dG9uIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9"
    "PntDSElQPWIuZGF0YXNldC5maWx0ZXI7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIj"
    "Y2hpcHMgYnV0dG9uIikuZm9yRWFjaCh4PT54LmNsYXNzTGlzdC50b2dnbGUoIm9uIix4PT09Yikp"
    "O2FwcGx5RmlsdGVyKCl9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1jbGFp"
    "bS1vcmRlcl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgY29uc3Qgb2lkPWIuZGF0"
    "YXNldC5jbGFpbU9yZGVyOwogICAgY29uc3Qgb3JkPU9SREVSUy5maW5kKHg9PnguaWQ9PT1vaWQp"
    "fHx7fTsKICAgIGlmKG9yZC5jbGFpbSl7dG9hc3QoIkEgY2xhaW0gZm9yIHRoaXMgb3JkZXIgaXMg"
    "YWxyZWFkeSB3aXRoIGFuIGFkbWluIik7cmV0dXJuO30KICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1h"
    "cmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5SZXBvcnQgYSBwcm9ibGVtPC9oMz4KICAgICAg"
    "PHAgY2xhc3M9InN1YiI+QW4gYWRtaW4gcmVhZHMgZXZlcnkgcmVwb3J0IGFuZCBkZWNpZGVzLiBO"
    "b3RoaW5nIGlzIHJlZnVuZGVkIGF1dG9tYXRpY2FsbHkuPC9wPgogICAgICA8ZGl2IGNsYXNzPSJm"
    "aWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPldoYXQgaGFwcGVuZWQ/PC9sYWJl"
    "bD4KICAgICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIGlkPSJjbHIiPgogICAgICAgICAgPG9wdGlv"
    "bj5Mb2dpbiBub3Qgd29ya2luZzwvb3B0aW9uPjxvcHRpb24+QWNjb3VudCBiYW5uZWQ8L29wdGlv"
    "bj4KICAgICAgICAgIDxvcHRpb24+Q29kZSBuZXZlciBhcnJpdmVkPC9vcHRpb24+PG9wdGlvbj5X"
    "cm9uZyBjb3VudHJ5PC9vcHRpb24+CiAgICAgICAgICA8b3B0aW9uPlNvbWV0aGluZyBlbHNlPC9v"
    "cHRpb24+PC9zZWxlY3Q+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RGV0"
    "YWlsczwvbGFiZWw+CiAgICAgICAgPHRleHRhcmVhIGNsYXNzPSJpbnAiIGlkPSJjbGQiIHJvd3M9"
    "IjMiIHBsYWNlaG9sZGVyPSJUZWxsIHVzIGV4YWN0bHkgd2hhdCB5b3Ugc2F3Ij48L3RleHRhcmVh"
    "PjwvZGl2PgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBpZD0iY2xnbyI+U2Vu"
    "ZCB0byBhbiBhZG1pbjwvYnV0dG9uPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QgYmxv"
    "Y2sgc20iIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgZGF0YS1jbG9zZT5DYW5jZWw8L2J1dHRvbj5g"
    "KTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjbGdvIikub25jbGljaz1hc3luYygpPT57"
    "CiAgICAgIGNvbnN0IHJlYXNvbj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2xyIikudmFsdWU7"
    "CiAgICAgIGNvbnN0IGRldGFpbHM9KGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjbGQiKS52YWx1"
    "ZXx8IiIpLnRyaW0oKTsKICAgICAgaWYoZGV0YWlscy5sZW5ndGg8NSl7dG9hc3QoIlRlbGwgdXMg"
    "d2hhdCB3ZW50IHdyb25nIik7cmV0dXJuO30KICAgICAgaWYoIUxJVkUpe2Nsb3NlU2hlZXQoKTt0"
    "b2FzdCgiU2VudCIpO3JldHVybjt9CiAgICAgIGNvbnN0IGJ0bj1kb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgiY2xnbyIpO2J0bi5kaXNhYmxlZD10cnVlO2J0bi50ZXh0Q29udGVudD0iU2VuZGluZ+KA"
    "piI7CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL29yZGVyIix7aWQ6b2lkLHdoYXQ6"
    "ImNsYWltIixyZWFzb246cmVhc29uLGRldGFpbHM6ZGV0YWlsc30pOwogICAgICBidG4uZGlzYWJs"
    "ZWQ9ZmFsc2U7YnRuLnRleHRDb250ZW50PSJTZW5kIHRvIGFuIGFkbWluIjsKICAgICAgaWYoIXIp"
    "cmV0dXJuOwogICAgICBjbG9zZVNoZWV0KCk7dG9hc3QoIkNsYWltICMiK3IuaWQrIiBzZW50IOKA"
    "lCBhbiBhZG1pbiB3aWxsIHJlcGx5Iik7CiAgICAgIGF3YWl0IHJlZnJlc2goKTsKICAgIH07CiAg"
    "fSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtbG9nZ2VkaW5dIikuZm9yRWFj"
    "aChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3Qgb2lkPWIuZGF0YXNldC5sb2dnZWRp"
    "bjsKICAgIGlmKCFMSVZFKXt0b2FzdCgiTWFya2VkIGFzIGxvZ2dlZCBpbiIpO3JldHVybjt9CiAg"
    "ICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+Q29uZmly"
    "bSB5b3UgYXJlIGluPC9oMz4KICAgICAgPHAgY2xhc3M9InN1YiI+V2Ugd2lsbCBkZWxldGUgb3Vy"
    "IGNvcHkgb2YgdGhlIGxvZ2luIHNlc3Npb24gc3RyYWlnaHQgYXdheSwgc28gdGhlCiAgICAgIGFj"
    "Y291bnQgYmVjb21lcyB5b3VycyBhbG9uZS4gQ29kZXMgd2lsbCBzdG9wIGJlaW5nIGZvcndhcmRl"
    "ZC48L3A+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEgYmxvY2siIGlkPSJsaW9rIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMnB4Ij5ZZXMsIEkgYW0gbG9nZ2VkIGluPC9idXR0b24+CiAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBk"
    "YXRhLWNsb3NlPk5vdCB5ZXQ8L2J1dHRvbj5gKTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJsaW9rIikub25jbGljaz1hc3luYygpPT57CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIv"
    "YXBpL29yZGVyIix7aWQ6b2lkLHdoYXQ6ImxvZ2dlZGluIn0pOwogICAgICBpZighcilyZXR1cm47"
    "CiAgICAgIGNsb3NlU2hlZXQoKTt0b2FzdCgiRG9uZSDigJQgdGhlIGFjY291bnQgaXMgeW91cnMg"
    "YWxvbmUgbm93Iik7CiAgICAgIGF3YWl0IHJlZnJlc2goKTsKICAgIH07CiAgfSk7CiAgZG9jdW1l"
    "bnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZGlzcHV0ZV0iKS5mb3JFYWNoKGI9PmIub25jbGlj"
    "az0oKT0+ewogICAgY29uc3Qgb3JkPWIuZGF0YXNldC5kaXNwdXRlfHwiIjsKICAgIHNoZWV0KGA8"
    "aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5PcGVuIGEgZGlzcHV0ZTwv"
    "aDM+CiAgICAgPHAgY2xhc3M9InN1YiI+JHtvcmQ/KCJPcmRlciAiK2VzYyhvcmQpKToiQW4gYWRt"
    "aW4gcmV2aWV3cyBldmVyeSBkaXNwdXRlLiJ9PC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBz"
    "dHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+UmVhc29uPC9sYWJlbD4KICAgICAgIDxzZWxl"
    "Y3QgY2xhc3M9ImlucCIgaWQ9ImRzciI+PG9wdGlvbj5Ob3QgZGVsaXZlcmVkPC9vcHRpb24+PG9w"
    "dGlvbj5BY2NvdW50IGRlYWQgb24gYXJyaXZhbDwvb3B0aW9uPjxvcHRpb24+Q2hhcmdlZCB0d2lj"
    "ZTwvb3B0aW9uPjxvcHRpb24+T3RoZXI8L29wdGlvbj48L3NlbGVjdD48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJmaWVsZCI+PGxhYmVsPldoYXQgaGFwcGVuZWQ/PC9sYWJlbD4KICAgICAgIDx0ZXh0"
    "YXJlYSBjbGFzcz0iaW5wIiBpZD0iZHNkIiByb3dzPSIzIiBwbGFjZWhvbGRlcj0iRGVzY3JpYmUg"
    "dGhlIHByb2JsZW0iPjwvdGV4dGFyZWE+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxs"
    "YWJlbD5FdmlkZW5jZSAob3B0aW9uYWwpPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5w"
    "IiBpZD0iZHNmIiB0eXBlPSJmaWxlIiBhY2NlcHQ9ImltYWdlLyoiPgogICAgICAgPHNtYWxsIGNs"
    "YXNzPSJzdWIiIGlkPSJkc2ZuIj48L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImJ0bnJv"
    "dyI+PGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IiBkYXRhLWNsb3NlPkNhbmNlbDwvYnV0dG9uPgog"
    "ICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIgaWQ9ImRzZ28iPlN1Ym1pdDwvYnV0dG9uPjwv"
    "ZGl2PmApOwogICAgY29uc3QgZj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZHNmIik7CiAgICBp"
    "ZihmKWYub25jaGFuZ2U9KCk9Pntjb25zdCBuPShmLmZpbGVzJiZmLmZpbGVzWzBdKT9mLmZpbGVz"
    "WzBdLm5hbWU6IiI7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJkc2ZuIikudGV4dENv"
    "bnRlbnQ9bj8oIkF0dGFjaGVkOiAiK24pOiIiO307CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgiZHNnbyIpLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25zdCByZWFzb249ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoImRzciIpLnZhbHVlOwogICAgICBjb25zdCBkZXRhaWxzPShkb2N1bWVu"
    "dC5nZXRFbGVtZW50QnlJZCgiZHNkIikudmFsdWV8fCIiKS50cmltKCk7CiAgICAgIGlmKCFkZXRh"
    "aWxzKXt0b2FzdCgiRGVzY3JpYmUgd2hhdCBoYXBwZW5lZCIpO3JldHVybjt9CiAgICAgIGNvbnN0"
    "IGZpbGU9KGYmJmYuZmlsZXMmJmYuZmlsZXNbMF0pfHxudWxsOwogICAgICBjb25zdCBmbj1maWxl"
    "Pygic2NyZWVuc2hvdDogIitmaWxlLm5hbWUpOiIiOwogICAgICBpZighTElWRSl7Y2xvc2VTaGVl"
    "dCgpO3RvYXN0KCJEaXNwdXRlIG9wZW5lZCIpO3JldHVybjt9CiAgICAgIGNvbnN0IGJ0bj1kb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgiZHNnbyIpO2J0bi5kaXNhYmxlZD10cnVlO2J0bi50ZXh0Q29u"
    "dGVudD0iU2VuZGluZ+KApiI7CiAgICAgIGxldCBzaG90PSIiOwogICAgICBpZihmaWxlKXsKICAg"
    "ICAgICBpZihmaWxlLnNpemU+OCoxMDI0KjEwMjQpe3RvYXN0KCJUaGF0IGltYWdlIGlzIHRvbyBs"
    "YXJnZSAoOCBNQiBtYXgpIik7CiAgICAgICAgICBidG4uZGlzYWJsZWQ9ZmFsc2U7YnRuLnRleHRD"
    "b250ZW50PSJTdWJtaXQiO3JldHVybjt9CiAgICAgICAgc2hvdD1hd2FpdCBuZXcgUHJvbWlzZShy"
    "ZXM9PnsKICAgICAgICAgIGNvbnN0IHJkPW5ldyBGaWxlUmVhZGVyKCk7CiAgICAgICAgICByZC5v"
    "bmxvYWQ9KCk9PnJlcyhTdHJpbmcocmQucmVzdWx0fHwiIikpOwogICAgICAgICAgcmQub25lcnJv"
    "cj0oKT0+cmVzKCIiKTsKICAgICAgICAgIHJkLnJlYWRBc0RhdGFVUkwoZmlsZSk7CiAgICAgICAg"
    "fSk7CiAgICAgIH0KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvZGlzcHV0ZSIse29y"
    "ZGVyOm9yZCxyZWFzb246cmVhc29uLGRldGFpbHM6ZGV0YWlscywKICAgICAgICBldmlkZW5jZTpm"
    "bixzaG90OnNob3R9KTsKICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0i"
    "U3VibWl0IjsKICAgICAgaWYoIXIpcmV0dXJuOwogICAgICBjbG9zZVNoZWV0KCk7dG9hc3QoIkRp"
    "c3B1dGUgIyIrci5pZCsiIHNlbnQgdG8gdGhlIGFkbWlucyIpOwogICAgICBhd2FpdCByZWZyZXNo"
    "KCk7CiAgICB9OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXJhdGVd"
    "IikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9Pnt3aW5kb3cuX19yYXRlQ291bnRyeT1iLmRhdGFz"
    "ZXQucmF0ZTtyZXR1cm4gc2hlZXQoCiAgICBgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250"
    "LXNpemU6MXJlbSI+UmF0ZSAke2IuZGF0YXNldC5yYXRlfTwvaDM+CiAgICAgPHAgY2xhc3M9InN1"
    "YiI+WW91ciByYXRpbmcgc2hvd3Mgb24gdGhlIGNvdW50cnkgaW4gdGhlIHNob3AuPC9wPgogICAg"
    "IDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6OXB4O2p1c3RpZnktY29udGVudDpjZW50ZXI7"
    "bWFyZ2luOjE2cHggMCI+CiAgICAgICAke1sxLDIsMyw0LDVdLm1hcChuPT5gPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIHNtIHNvZnQiIGRhdGEtcmF0ZS1nbz0iJHtufSI+JHtufeKYhTwvYnV0dG9uPmApLmpv"
    "aW4oIiIpfTwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgZGF0"
    "YS1jbG9zZT5DbG9zZTwvYnV0dG9uPmApO30pOyAKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxs"
    "KCJbZGF0YS1jcnlwdG9dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnNoZWV0KAogICAgYDxo"
    "MyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkNyeXB0byB0b3AtdXA8L2gz"
    "PgogICAgIDxwIGNsYXNzPSJzdWIiPlNlbmQgVVNEVC1UUkMyMCDigJQgY3JlZGl0ZWQgYWZ0ZXIg"
    "MSBuZXR3b3JrIGNvbmZpcm1hdGlvbi48L3A+CiAgICAgPGRpdiBjbGFzcz0icXIiPlFSIENPREU8"
    "YnI+KHdhbGxldCBhZGRyZXNzKTwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5OZXR3"
    "b3JrPC9zcGFuPjxiPlRSQy0yMDwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+"
    "QWRkcmVzczwvc3Bhbj48YiBzdHlsZT0iZm9udC1zaXplOi43cmVtIj5ub3QtY29uZmlndXJlZDwv"
    "Yj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+UmF0ZTwvc3Bhbj48Yj4xIFVTRFQg"
    "PSDigrk4OC40MDwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEycHgiPjxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCIgZGF0YS1jbG9zZT5DbG9zZTwv"
    "YnV0dG9uPgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIgZGF0YS1hY3Q9ImNyeXB0byI+"
    "SSd2ZSBTZW50IEl0PC9idXR0b24+PC9kaXY+YCkpOwogIGNvbnN0IHRnPWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJ0b3VyZ28iKSx0cz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgidG91cnNraXAi"
    "KTsKICBpZih0Zyl0Zy5vbmNsaWNrPSgpPT5zaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRw"
    "eDtmb250LXNpemU6MXJlbSI+SG93IGl0IHdvcmtzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJzdGVw"
    "cyIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+CiAgICAgICA8ZGl2IGNsYXNzPSJzdGVwIG9uIj48"
    "aT4xPC9pPlRvcCB1cCB5b3VyIHdhbGxldCBieSBVUEk8L2Rpdj4KICAgICAgIDxkaXYgY2xhc3M9"
    "InN0ZXAgb24iPjxpPjI8L2k+UGljayBhIGNvdW50cnkgYW5kIGJ1eSBpbnN0YW50bHk8L2Rpdj4K"
    "ICAgICAgIDxkaXYgY2xhc3M9InN0ZXAgb24iPjxpPjM8L2k+UmVhZCB0aGUgbG9naW4gY29kZSBy"
    "aWdodCBoZXJlIGluIE9yZGVyczwvZGl2PjwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBj"
    "dGEgYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEtY2xvc2U+R290IGl0PC9idXR0"
    "b24+YCk7CiAgaWYodHMpdHMub25jbGljaz0oKT0+e0ZFQVQub25ib2FyZGluZz1mYWxzZTtyZW5k"
    "ZXIoKTt0b2FzdCgiVG91ciBoaWRkZW4iKTt9OwogIHN0YXJ0Q291bnRkb3duKCk7CiAgYmluZEhv"
    "bGQoKTsKfQpmdW5jdGlvbiBhcHBseUZpbHRlcigpewogIEZJTFRFUkVEPVNUT0NLLmZpbHRlcihz"
    "PT57CiAgICBjb25zdCB0PShzLmMrIiAiK3MuY29kZSkudG9Mb3dlckNhc2UoKTsKICAgIGlmKFFV"
    "RVJZJiYhdC5pbmNsdWRlcyhRVUVSWSkpcmV0dXJuIGZhbHNlOwogICAgaWYoQ0hJUD09PSJzdG9j"
    "ayImJnMucXR5PT09MClyZXR1cm4gZmFsc2U7CiAgICBpZihDSElQPT09ImRlYWwiJiYhcy53YXMp"
    "cmV0dXJuIGZhbHNlOwogICAgaWYoQ0hJUD09PSJjaGVhcCImJnMucHJpY2U+PTM1KXJldHVybiBm"
    "YWxzZTsKICAgIGlmKENISVA9PT0idjJmYSImJnMucXR5PT09MClyZXR1cm4gZmFsc2U7CiAgICBp"
    "ZihDSElQPT09InZtYWlsIiYmIXMuaG90KXJldHVybiBmYWxzZTsKICAgIGlmKENISVA9PT0idnNw"
    "YW0iJiZzLnByaWNlPDMwKXJldHVybiBmYWxzZTsKICAgIHJldHVybiB0cnVlO30pOwogIGNvbnN0"
    "IGI9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNob3Bib2R5Iik7CiAgaWYoYil7Yi5pbm5lckhU"
    "TUw9c2hvcEJvZHkoKTtiaW5kQnV5KCk7fQp9CmxldCBjZFQ9bnVsbDsKZnVuY3Rpb24gc3RhcnRD"
    "b3VudGRvd24oKXsKICBjb25zdCBlbD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2QiKTtjbGVh"
    "ckludGVydmFsKGNkVCk7aWYoIWVsKXJldHVybjsKICBsZXQgdD0xNCo2MCs1MjsKICBjZFQ9c2V0"
    "SW50ZXJ2YWwoKCk9PntpZighZG9jdW1lbnQuYm9keS5jb250YWlucyhlbCkpe2NsZWFySW50ZXJ2"
    "YWwoY2RUKTtyZXR1cm59CiAgICB0PXQ+MD90LTE6MTQqNjArNTI7ZWwudGV4dENvbnRlbnQ9U3Ry"
    "aW5nKE1hdGguZmxvb3IodC82MCkpLnBhZFN0YXJ0KDIsIjAiKSsiOiIrU3RyaW5nKHQlNjApLnBh"
    "ZFN0YXJ0KDIsIjAiKX0sMTAwMCk7Cn0KLyogMDUgwrcgaG9sZC10by1jb25maXJtICovCmZ1bmN0"
    "aW9uIGJpbmRIb2xkKCl7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiLmhvbGQiKS5mb3JF"
    "YWNoKGI9PnsKICAgIGlmKGIuX2gpcmV0dXJuO2IuX2g9MTtjb25zdCBmaWxsPWIucXVlcnlTZWxl"
    "Y3RvcigiLmZpbGwiKTtsZXQgaXY9bnVsbCxwPTA7CiAgICBjb25zdCBzdG9wPSgpPT57Y2xlYXJJ"
    "bnRlcnZhbChpdik7cD0wO2lmKGZpbGwpZmlsbC5zdHlsZS53aWR0aD0iMCUifTsKICAgIGNvbnN0"
    "IGdvPWU9PntlLnByZXZlbnREZWZhdWx0KCk7Y2xlYXJJbnRlcnZhbChpdik7aXY9c2V0SW50ZXJ2"
    "YWwoKCk9PntwKz00O2lmKGZpbGwpZmlsbC5zdHlsZS53aWR0aD1wKyIlIjsKICAgICAgaWYocD49"
    "MTAwKXtzdG9wKCk7Yi5jbGFzc0xpc3QuYWRkKCJkb25lIik7c2V0VGltZW91dCgoKT0+Yi5jbGFz"
    "c0xpc3QucmVtb3ZlKCJkb25lIiksNDUwKTtidXJzdCgpO3RvYXN0KGIuZGF0YXNldC5kb25lfHwi"
    "Q29uZmlybWVkIil9fSwyOCl9OwogICAgYi5hZGRFdmVudExpc3RlbmVyKCJwb2ludGVyZG93biIs"
    "Z28pO1sicG9pbnRlcnVwIiwicG9pbnRlcmxlYXZlIiwicG9pbnRlcmNhbmNlbCJdLmZvckVhY2go"
    "ZXY9PmIuYWRkRXZlbnRMaXN0ZW5lcihldixzdG9wKSk7fSk7Cn0KLyog4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQIEtJVCBSVU5USU1FIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkCAqLwpjb25zdCBSTT1tYXRjaE1lZGlhKCIocHJlZmVycy1yZWR1Y2VkLW1vdGlvbjpy"
    "ZWR1Y2UpIikubWF0Y2hlczsKLyogc3ByaW5nIGludGVncmF0b3Ig4oCUIGtpdCB1c2VzIG1hc3Mg"
    "LjEgLyBzdGlmZm5lc3MgMTUwIC8gZGFtcGluZyAxMiAqLwpmdW5jdGlvbiBTcHJpbmcodix7bT0u"
    "MSxrPTE1MCxkPTEyfT17fSl7dGhpcy52PXY7dGhpcy50PXY7dGhpcy52ZWw9MDt0aGlzLm09bTt0"
    "aGlzLms9azt0aGlzLmQ9ZDt9ClNwcmluZy5wcm90b3R5cGUuc3RlcD1mdW5jdGlvbihkdCl7Y29u"
    "c3QgRj0tdGhpcy5rKih0aGlzLnYtdGhpcy50KS10aGlzLmQqdGhpcy52ZWw7CiAgdGhpcy52ZWwr"
    "PShGL3RoaXMubSkqZHQ7dGhpcy52Kz10aGlzLnZlbCpkdDtyZXR1cm4gdGhpcy52O307CgovKiAw"
    "MiArIDI1IMK3IGFuaW1hdGVkIGRvY2s6IGRpc3RhbmNl4oaSc2NhbGUgd2l0aCBzcHJpbmcsIHBs"
    "dXMgc2xpZGluZyBwaWxsICovCihmdW5jdGlvbiBkb2NrKCl7CiAgY29uc3QgZGs9ZG9jdW1lbnQu"
    "cXVlcnlTZWxlY3RvcigibmF2I3RhYnMgLmRvY2siKTtpZighZGspcmV0dXJuOwogIGNvbnN0IGJ0"
    "bnM9Wy4uLmRrLnF1ZXJ5U2VsZWN0b3JBbGwoImJ1dHRvbiIpXSxwaWxsPWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJkcGlsbCIpOwogIGNvbnN0IEZJTkU9bWF0Y2hNZWRpYSgiKGhvdmVyOmhvdmVy"
    "KSBhbmQgKHBvaW50ZXI6ZmluZSkiKS5tYXRjaGVzJiYhUk07CiAgY29uc3Qgc3A9YnRucy5tYXAo"
    "KCk9Pm5ldyBTcHJpbmcoMSkpLHBzPXt4Om5ldyBTcHJpbmcoMCx7azoyNjAsZDoxMX0pLHc6bmV3"
    "IFNwcmluZygwLHtrOjI2MCxkOjExfSl9OwogIGxldCBteD1JbmZpbml0eSxsYXN0PXBlcmZvcm1h"
    "bmNlLm5vdygpLHBsYWNlZD1mYWxzZTsKICBmdW5jdGlvbiByZXNldFNjYWxlKCl7bXg9SW5maW5p"
    "dHk7aWYoIUZJTkUpYnRucy5mb3JFYWNoKGI9PmIuc3R5bGUudHJhbnNmb3JtPSIiKTt9CiAgaWYo"
    "RklORSl7CiAgICBkay5hZGRFdmVudExpc3RlbmVyKCJwb2ludGVybW92ZSIsZT0+e2lmKGUucG9p"
    "bnRlclR5cGU9PT0ibW91c2UiKW14PWUuY2xpZW50WH0pOwogICAgZGsuYWRkRXZlbnRMaXN0ZW5l"
    "cigicG9pbnRlcmxlYXZlIiwoKT0+bXg9SW5maW5pdHkpOwogICAgWyJwb2ludGVydXAiLCJwb2lu"
    "dGVyY2FuY2VsIiwidG91Y2hlbmQiLCJ0b3VjaGNhbmNlbCIsImJsdXIiXS5mb3JFYWNoKGV2PT4K"
    "ICAgICAgYWRkRXZlbnRMaXN0ZW5lcihldixyZXNldFNjYWxlLHtwYXNzaXZlOnRydWV9KSk7CiAg"
    "fWVsc2V7YnRucy5mb3JFYWNoKGI9PmIuc3R5bGUudHJhbnNmb3JtPSIiKTt9CiAgZG9jdW1lbnQu"
    "YWRkRXZlbnRMaXN0ZW5lcigidmlzaWJpbGl0eWNoYW5nZSIscmVzZXRTY2FsZSk7CgogIGZ1bmN0"
    "aW9uIHBsYWNlKHNuYXApewogICAgY29uc3Qgb249ZGsucXVlcnlTZWxlY3RvcigiYnV0dG9uLm9u"
    "Iik7CiAgICBpZighb258fCFwaWxsfHwhb24ub2Zmc2V0V2lkdGh8fCFkay5vZmZzZXRXaWR0aCly"
    "ZXR1cm47ICAgLy8gbmV2ZXIgcGxhY2Ugd2hpbGUgaGlkZGVuCiAgICBwcy54LnQ9b24ub2Zmc2V0"
    "TGVmdC1kay5jbGllbnRMZWZ0O3BzLncudD1vbi5vZmZzZXRXaWR0aDsKICAgIHBpbGwuc3R5bGUu"
    "dG9wPShvbi5vZmZzZXRUb3AtZGsuY2xpZW50VG9wKSsicHgiO3BpbGwuc3R5bGUuaGVpZ2h0PW9u"
    "Lm9mZnNldEhlaWdodCsicHgiOwogICAgaWYoc25hcHx8IXBsYWNlZCl7cGxhY2VkPXRydWU7cHMu"
    "eC52PXBzLngudDtwcy54LnZlbD0wO3BzLncudj1wcy53LnQ7cHMudy52ZWw9MDsKICAgICAgcGls"
    "bC5zdHlsZS50cmFuc2Zvcm09InRyYW5zbGF0ZVgoIitwcy54LnYrInB4KSI7cGlsbC5zdHlsZS53"
    "aWR0aD1wcy53LnYrInB4IjsKICAgICAgcGlsbC5jbGFzc0xpc3QuYWRkKCJyZWFkeSIpO30KICB9"
    "CiAgbmV3IFJlc2l6ZU9ic2VydmVyKCgpPT5wbGFjZSghcGxhY2VkKSkub2JzZXJ2ZShkayk7CiAg"
    "YWRkRXZlbnRMaXN0ZW5lcigicmVzaXplIiwoKT0+cGxhY2UodHJ1ZSkpOwogIGFkZEV2ZW50TGlz"
    "dGVuZXIoImxvYWQiLCgpPT5wbGFjZSghcGxhY2VkKSk7CiAgc2V0VGltZW91dCgoKT0+cGxhY2Uo"
    "IXBsYWNlZCksNjApO3NldFRpbWVvdXQoKCk9PnBsYWNlKCFwbGFjZWQpLDQwMCk7CgogIChmdW5j"
    "dGlvbiBsb29wKHQpewogICAgY29uc3QgZHQ9TWF0aC5taW4oLjAzMiwodC1sYXN0KS8xMDAwKTts"
    "YXN0PXQ7CiAgICBpZihGSU5FKXtidG5zLmZvckVhY2goKGIsaSk9PntsZXQgaz0wOwogICAgICBp"
    "ZihteCE9PUluZmluaXR5KXtjb25zdCByPWIuZ2V0Qm91bmRpbmdDbGllbnRSZWN0KCk7CiAgICAg"
    "ICAgY29uc3QgZGlzdD1NYXRoLmFicyhyLmxlZnQrci53aWR0aC8yLW14KTtrPU1hdGgubWF4KDAs"
    "MS1kaXN0LzE1MCk7fQogICAgICBzcFtpXS50PTErLjM0Kmsqaztjb25zdCB2PXNwW2ldLnN0ZXAo"
    "ZHQpOwogICAgICBiLnN0eWxlLnRyYW5zZm9ybT0odj4xLjAwMnx8c3BbaV0udmVsPi4wMXx8c3Bb"
    "aV0udmVsPC0uMDEpCiAgICAgICAgPyAidHJhbnNsYXRlWSgiKygtOCoodi0xKSkudG9GaXhlZCgy"
    "KSsicHgpIHNjYWxlKCIrdi50b0ZpeGVkKDMpKyIpIiA6ICIiO30pO30KICAgIGlmKHBpbGwmJnBs"
    "YWNlZCl7cGlsbC5zdHlsZS50cmFuc2Zvcm09InRyYW5zbGF0ZVgoIitwcy54LnN0ZXAoZHQpLnRv"
    "Rml4ZWQoMikrInB4KSI7CiAgICAgIHBpbGwuc3R5bGUud2lkdGg9cHMudy5zdGVwKGR0KS50b0Zp"
    "eGVkKDIpKyJweCI7fQogICAgcmVxdWVzdEFuaW1hdGlvbkZyYW1lKGxvb3ApO30pKGxhc3QpOwog"
    "IHdpbmRvdy5fX2RvY2tQbGFjZT1wbGFjZTtwbGFjZSh0cnVlKTsKfSkoKTsKCi8qIDE0IMK3IHYt"
    "dGFicyBzbGlkaW5nIGluZGljYXRvciBmb3IgdGhlIGFkbWluIHN1Ym5hdiAqLwooZnVuY3Rpb24g"
    "dnRhYnMoKXsKICBjb25zdCBuYXY9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFzdWIiKSxwaWxs"
    "PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJ2cGlsbCIpO2lmKCFuYXZ8fCFwaWxsKXJldHVybjsK"
    "ICBjb25zdCBweD1uZXcgU3ByaW5nKDAse2s6MjEwLGQ6MTl9KSxwdz1uZXcgU3ByaW5nKDAse2s6"
    "MjEwLGQ6MTl9KTtsZXQgbGFzdD1wZXJmb3JtYW5jZS5ub3coKSxpbml0PWZhbHNlOwogIGZ1bmN0"
    "aW9uIHBsYWNlKHNuYXApe2NvbnN0IG9uPW5hdi5xdWVyeVNlbGVjdG9yKCJidXR0b24ub24iKTsK"
    "ICAgIGlmKCFvbnx8IW9uLm9mZnNldFdpZHRofHwhbmF2Lm9mZnNldFdpZHRoKXJldHVybjsKICAg"
    "IHB4LnQ9b24ub2Zmc2V0TGVmdC1uYXYuY2xpZW50TGVmdDtwdy50PW9uLm9mZnNldFdpZHRoOwog"
    "ICAgcGlsbC5zdHlsZS50b3A9KG9uLm9mZnNldFRvcC1uYXYuY2xpZW50VG9wKSsicHgiO3BpbGwu"
    "c3R5bGUuaGVpZ2h0PW9uLm9mZnNldEhlaWdodCsicHgiOwogICAgaWYoc25hcHx8IWluaXQpe2lu"
    "aXQ9dHJ1ZTtweC52PXB4LnQ7cHgudmVsPTA7cHcudj1wdy50O3B3LnZlbD0wOwogICAgICBwaWxs"
    "LnN0eWxlLnRyYW5zZm9ybT0idHJhbnNsYXRlWCgiK3B4LnYrInB4KSI7cGlsbC5zdHlsZS53aWR0"
    "aD1wdy52KyJweCI7fX0KICBuYXYuYWRkRXZlbnRMaXN0ZW5lcigiY2xpY2siLCgpPT5yZXF1ZXN0"
    "QW5pbWF0aW9uRnJhbWUoKCk9PnBsYWNlKGZhbHNlKSkpOwogIG5hdi5hZGRFdmVudExpc3RlbmVy"
    "KCJzY3JvbGwiLCgpPT5wbGFjZShmYWxzZSkpOwogIG5ldyBSZXNpemVPYnNlcnZlcigoKT0+cGxh"
    "Y2UoIWluaXQpKS5vYnNlcnZlKG5hdik7CiAgYWRkRXZlbnRMaXN0ZW5lcigibG9hZCIsKCk9PnBs"
    "YWNlKCFpbml0KSk7c2V0VGltZW91dCgoKT0+cGxhY2UoIWluaXQpLDgwKTtwbGFjZSh0cnVlKTsK"
    "ICAoZnVuY3Rpb24gbG9vcCh0KXtjb25zdCBkdD1NYXRoLm1pbiguMDMyLCh0LWxhc3QpLzEwMDAp"
    "O2xhc3Q9dDsKICAgIGlmKGluaXQpe3BpbGwuc3R5bGUudHJhbnNmb3JtPSJ0cmFuc2xhdGVYKCIr"
    "cHguc3RlcChkdCkudG9GaXhlZCgyKSsicHgpIjtwaWxsLnN0eWxlLndpZHRoPXB3LnN0ZXAoZHQp"
    "LnRvRml4ZWQoMikrInB4Ijt9CiAgICByZXF1ZXN0QW5pbWF0aW9uRnJhbWUobG9vcCk7fSkobGFz"
    "dCk7CiAgd2luZG93Ll9fdnRhYlBsYWNlPXBsYWNlOwp9KSgpOwoKLyogMDggwrcga2luZXRpYyBn"
    "cmlkIOKAlCBwb2ludGVyIHdhcnAgKyBjbGljayByaXBwbGVzIChjYW52YXMgcG9ydCkgKi8KKGZ1"
    "bmN0aW9uIGtpbmV0aWMoKXsKICBjb25zdCBjdj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgia2ci"
    "KTtpZighY3Z8fFJNKXJldHVybjtjb25zdCBjdHg9Y3YuZ2V0Q29udGV4dCgiMmQiKTsKICBjb25z"
    "dCBTUD0zMCxJTkY9MjEwLFdBUlA9MjIsTEVSUD0uMDksUkdCPSIwLDI1NSwyMTQiOwogIGxldCBX"
    "PTAsSD0wLG1vdXNlPXt4Oi05OTk5LHk6LTk5OTl9LHRndD17eDotOTk5OSx5Oi05OTk5fSxyaXA9"
    "W10scmFmPTA7CiAgZnVuY3Rpb24gc2l6ZSgpe2NvbnN0IGQ9TWF0aC5taW4oMS42LGRldmljZVBp"
    "eGVsUmF0aW98fDEpO1c9aW5uZXJXaWR0aDtIPWlubmVySGVpZ2h0OwogICAgY3Yud2lkdGg9Vypk"
    "O2N2LmhlaWdodD1IKmQ7Y3Yuc3R5bGUud2lkdGg9VysicHgiO2N2LnN0eWxlLmhlaWdodD1IKyJw"
    "eCI7Y3R4LnNldFRyYW5zZm9ybShkLDAsMCxkLDAsMCk7fQogIGFkZEV2ZW50TGlzdGVuZXIoInJl"
    "c2l6ZSIsc2l6ZSk7c2l6ZSgpOwogIGFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJtb3ZlIixlPT57"
    "dGd0PXt4OmUuY2xpZW50WCx5OmUuY2xpZW50WX19LHtwYXNzaXZlOnRydWV9KTsKICBhZGRFdmVu"
    "dExpc3RlbmVyKCJwb2ludGVyZG93biIsZT0+e2lmKHJpcC5sZW5ndGg8NClyaXAucHVzaCh7eDpl"
    "LmNsaWVudFgseTplLmNsaWVudFkscjowLG86MX0pfSx7cGFzc2l2ZTp0cnVlfSk7CiAgYWRkRXZl"
    "bnRMaXN0ZW5lcigicG9pbnRlcmxlYXZlIiwoKT0+dGd0PXt4Oi05OTk5LHk6LTk5OTl9KTsKICBm"
    "dW5jdGlvbiB3YXJwKGd4LGd5LGMscixjb2xzLHJvd3MpewogICAgY29uc3QgY3A9TWF0aC5taW4o"
    "Yy8xLjUsKGNvbHMtMS1jKS8xLjUsMSkscnA9TWF0aC5taW4oci8xLjUsKHJvd3MtMS1yKS8xLjUs"
    "MSkscGluPWNwKmNwKnJwKnJwOwogICAgY29uc3QgZHg9Z3gtbW91c2UueCxkeT1neS1tb3VzZS55"
    "LGRpc3Q9TWF0aC5oeXBvdChkeCxkeSk7CiAgICBjb25zdCBwcm94PU1hdGgubWF4KDAsMS1kaXN0"
    "L0lORikqcGluO2xldCByeD0wLHJ5PTA7CiAgICBmb3IoY29uc3QgcCBvZiByaXApe2NvbnN0IGRk"
    "eD1neC1wLngsZGR5PWd5LXAueSxkZD1NYXRoLmh5cG90KGRkeCxkZHkpLGRpZmY9ZGQtcC5yOwog"
    "ICAgICBpZihNYXRoLmFicyhkaWZmKTw1NSl7Y29uc3Qgc3Q9KDEtTWF0aC5hYnMoZGlmZikvNTUp"
    "KnAubyoxNipwaW4sYW49TWF0aC5hdGFuMihkZHksZGR4KSxzZz1kaWZmPDA/LTE6MTsKICAgICAg"
    "ICByeCs9TWF0aC5jb3MoYW4pKnN0KnNnKi0xO3J5Kz1NYXRoLnNpbihhbikqc3Qqc2cqLTE7fX0K"
    "ICAgIGlmKGRpc3Q8SU5GJiZkaXN0PjAmJnBpbj4wKXtjb25zdCB0PWRpc3QvSU5GLGU9KDEtdCkq"
    "KDEtdCkqTWF0aC5taW4oMSxkaXN0LzYwKSx3PWUqV0FSUCpwaW4sYW49TWF0aC5hdGFuMihkeSxk"
    "eCk7CiAgICAgIHJldHVybltneC1NYXRoLmNvcyhhbikqdytyeCxneS1NYXRoLnNpbihhbikqdyty"
    "eSxwcm94XTt9CiAgICByZXR1cm5bZ3grcngsZ3krcnkscHJveF07fQogIGZ1bmN0aW9uIGZyYW1l"
    "KCl7CiAgICBtb3VzZS54Kz0odGd0LngtbW91c2UueCkqTEVSUDttb3VzZS55Kz0odGd0LnktbW91"
    "c2UueSkqTEVSUDsKICAgIHJpcD1yaXAuZmlsdGVyKHA9PntwLnIrPTc7cC5vLT0uMDE2O3JldHVy"
    "biBwLm8+MH0pOwogICAgY3R4LmNsZWFyUmVjdCgwLDAsVyxIKTsKICAgIGNvbnN0IGNvbHM9TWF0"
    "aC5jZWlsKFcvU1ApKzEscm93cz1NYXRoLmNlaWwoSC9TUCkrMSxwdHM9W107CiAgICBmb3IobGV0"
    "IHI9MDtyPHJvd3M7cisrKXtjb25zdCByb3c9W107Zm9yKGxldCBjPTA7Yzxjb2xzO2MrKylyb3cu"
    "cHVzaCh3YXJwKGMqU1AscipTUCxjLHIsY29scyxyb3dzKSk7cHRzLnB1c2gocm93KTt9CiAgICBm"
    "b3IobGV0IHI9MDtyPHJvd3M7cisrKWZvcihsZXQgYz0wO2M8Y29scztjKyspe2NvbnN0IHA9cHRz"
    "W3JdW2NdOwogICAgICBpZihjPGNvbHMtMSl7Y29uc3QgcT1wdHNbcl1bYysxXTtjdHguc3Ryb2tl"
    "U3R5bGU9InJnYmEoIitSR0IrIiwiKyguMDUrcFsyXSouMjIpLnRvRml4ZWQoMykrIikiOwogICAg"
    "ICAgIGN0eC5saW5lV2lkdGg9MTtjdHguYmVnaW5QYXRoKCk7Y3R4Lm1vdmVUbyhwWzBdLHBbMV0p"
    "O2N0eC5saW5lVG8ocVswXSxxWzFdKTtjdHguc3Ryb2tlKCk7fQogICAgICBpZihyPHJvd3MtMSl7"
    "Y29uc3QgcT1wdHNbcisxXVtjXTtjdHguc3Ryb2tlU3R5bGU9InJnYmEoIitSR0IrIiwiKyguMDUr"
    "cFsyXSouMjIpLnRvRml4ZWQoMykrIikiOwogICAgICAgIGN0eC5saW5lV2lkdGg9MTtjdHguYmVn"
    "aW5QYXRoKCk7Y3R4Lm1vdmVUbyhwWzBdLHBbMV0pO2N0eC5saW5lVG8ocVswXSxxWzFdKTtjdHgu"
    "c3Ryb2tlKCk7fQogICAgICBpZihwWzJdPi4wNil7Y3R4LmZpbGxTdHlsZT0icmdiYSgiK1JHQisi"
    "LCIrKHBbMl0qLjUpLnRvRml4ZWQoMykrIikiO2N0eC5iZWdpblBhdGgoKTsKICAgICAgICBjdHgu"
    "YXJjKHBbMF0scFsxXSwxLjYrcFsyXSoxLjYsMCw2LjI4Myk7Y3R4LmZpbGwoKTt9fQogICAgcmFm"
    "PXJlcXVlc3RBbmltYXRpb25GcmFtZShmcmFtZSk7fQogIGZyYW1lKCk7CiAgZG9jdW1lbnQuYWRk"
    "RXZlbnRMaXN0ZW5lcigidmlzaWJpbGl0eWNoYW5nZSIsKCk9PntpZihkb2N1bWVudC5oaWRkZW4p"
    "Y2FuY2VsQW5pbWF0aW9uRnJhbWUocmFmKTtlbHNlIGZyYW1lKCk7fSk7Cn0pKCk7CgovKiAwMSDC"
    "tyBkb3QgbG9hZGVyIHNwbGFzaCAoa2l0IGZyYW1lIGRhdGEpICovCihmdW5jdGlvbiBzcGxhc2go"
    "KXsKICBjb25zdCBnPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJkZyIpLHNwPWRvY3VtZW50Lmdl"
    "dEVsZW1lbnRCeUlkKCJzcGxhc2giKTtpZighZylyZXR1cm47CiAgZm9yKGxldCBpPTA7aTw0OTtp"
    "KyspZy5hcHBlbmRDaGlsZChkb2N1bWVudC5jcmVhdGVFbGVtZW50KCJpIikpOwogIGNvbnN0IGNl"
    "bGxzPVsuLi5nLmNoaWxkcmVuXTsKICBjb25zdCBGPVtbMTQsNywwLDgsNiwxMywyMF0sWzE0LDcs"
    "MTMsMjAsMTYsMjcsMjFdLFsxNCwyMCwyNywyMSwzNCwyNCwyOF0sWzI3LDIxLDM0LDI4LDQxLDMy"
    "LDM1XSwKICAgWzM0LDI4LDQxLDM1LDQ4LDQwLDQyXSxbMzQsMjgsNDEsMzUsNDgsNDIsNDZdLFsz"
    "NCwyOCw0MSwzNSw0OCw0MiwzOF0sWzM0LDI4LDQxLDM1LDQ4LDMwLDIxXSwKICAgWzM0LDI4LDQx"
    "LDQ4LDIxLDIyLDE0XSxbMzQsMjgsNDEsMjEsMTQsMTYsMjddLFszNCwyOCwyMSwxNCwxMCwyMCwy"
    "N10sWzI4LDIxLDE0LDQsMTMsMjAsMjddLAogICBbMjgsMjEsMTQsMTIsNiwxMywyMF0sWzI4LDIx"
    "LDE0LDYsMTMsMjAsMTFdLFsyOCwyMSwxNCw2LDEzLDIwLDEwXSxbMTQsNiwxMywyMCw5LDcsMjFd"
    "XTsKICBsZXQgZj0wO2NvbnN0IGl2PXNldEludGVydmFsKCgpPT57Y2VsbHMuZm9yRWFjaChjPT5j"
    "LmNsYXNzTGlzdC5yZW1vdmUoImFjdCIpKTsKICAgIEZbZiVGLmxlbmd0aF0uZm9yRWFjaChpPT5j"
    "ZWxsc1tpXSYmY2VsbHNbaV0uY2xhc3NMaXN0LmFkZCgiYWN0IikpO2YrKzt9LDEwMCk7CiAgc2V0"
    "VGltZW91dCgoKT0+e2NsZWFySW50ZXJ2YWwoaXYpO3NwLmNsYXNzTGlzdC5hZGQoImdvbmUiKTsK"
    "ICAgIHNldFRpbWVvdXQoKCk9PntzcC5yZW1vdmUoKTtpZih3aW5kb3cuX19kb2NrUGxhY2Upd2lu"
    "ZG93Ll9fZG9ja1BsYWNlKHRydWUpOwogICAgICBpZih3aW5kb3cuX192dGFiUGxhY2Upd2luZG93"
    "Ll9fdnRhYlBsYWNlKHRydWUpO30sNjAwKTt9LFJNPzMwMDoxNzUwKTsKfSkoKTsKCi8qIHN0YWdn"
    "ZXJlZCByZXZlYWwgb24gZXZlcnkgdmlldyBjaGFuZ2UgKi8KZnVuY3Rpb24gcmV2ZWFsU3RhZ2Uo"
    "ZWwpewogIGlmKCFlbHx8Uk0pcmV0dXJuOwogIFsuLi5lbC5jaGlsZHJlbl0uc2xpY2UoMCwxNCku"
    "Zm9yRWFjaCgoYyxpKT0+ewogICAgaWYoIWMuYW5pbWF0ZSlyZXR1cm47CiAgICB0cnl7CiAgICAg"
    "IGMuZ2V0QW5pbWF0aW9ucygpLmZvckVhY2goYT0+e2lmKGEuaWQ9PT0icnYiKWEuY2FuY2VsKCk7"
    "fSk7CiAgICAgIGNvbnN0IGFuPWMuYW5pbWF0ZShbe29wYWNpdHk6MCx0cmFuc2Zvcm06InRyYW5z"
    "bGF0ZVkoMTRweCkgc2NhbGUoLjk4NSkifSx7b3BhY2l0eToxLHRyYW5zZm9ybToibm9uZSJ9XSwK"
    "ICAgICAgICB7ZHVyYXRpb246NTIwLGRlbGF5OmkqNDAsZWFzaW5nOiJjdWJpYy1iZXppZXIoLjIy"
    "LDEsLjM2LDEpIixmaWxsOiJiYWNrd2FyZHMifSk7CiAgICAgIGFuLmlkPSJydiI7CiAgICB9Y2F0"
    "Y2goZSl7fQogIH0pO30KCi8qIG51bWJlciB0aWNrZXIgKi8KZnVuY3Rpb24gdGlja1RvKGVsLHRv"
    "LGR1cil7aWYoIWVsKXJldHVybjtjb25zdCBmcm9tPXBhcnNlRmxvYXQoZWwudGV4dENvbnRlbnQu"
    "cmVwbGFjZSgvW15cZC5dL2csIiIpKXx8MDsKICBpZihSTSl7ZWwudGV4dENvbnRlbnQ9dG8udG9G"
    "aXhlZCgyKTtyZXR1cm59CiAgY29uc3QgdDA9cGVyZm9ybWFuY2Uubm93KCk7KGZ1bmN0aW9uIHN0"
    "KHQpe2NvbnN0IHA9TWF0aC5taW4oMSwodC10MCkvKGR1cnx8NjAwKSk7CiAgICBjb25zdCBlPTEt"
    "TWF0aC5wb3coMS1wLDMpO2VsLnRleHRDb250ZW50PShmcm9tKyh0by1mcm9tKSplKS50b0ZpeGVk"
    "KDIpOwogICAgaWYocDwxKXJlcXVlc3RBbmltYXRpb25GcmFtZShzdCk7fSkodDApO30KCi8qIHBy"
    "ZXNzIHJpcHBsZSBvbiBldmVyeSBidXR0b24gKi8KZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigi"
    "cG9pbnRlcmRvd24iLGU9Pntjb25zdCBiPWUudGFyZ2V0LmNsb3Nlc3QoIi5idG4sLmNoaXByb3cg"
    "YnV0dG9uLC5zdWJuYXYgYnV0dG9uIik7CiAgaWYoIWJ8fFJNKXJldHVybjtjb25zdCByPWIuZ2V0"
    "Qm91bmRpbmdDbGllbnRSZWN0KCksZD1NYXRoLm1heChyLndpZHRoLHIuaGVpZ2h0KSoxLjQ7CiAg"
    "Y29uc3Qgcz1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCJzcGFuIik7cy5jbGFzc05hbWU9InJpcCI7"
    "CiAgcy5zdHlsZS5jc3NUZXh0PSJsZWZ0OiIrKGUuY2xpZW50WC1yLmxlZnQpKyJweDt0b3A6Iiso"
    "ZS5jbGllbnRZLXIudG9wKSsicHg7d2lkdGg6IitkKyJweDtoZWlnaHQ6IitkKyJweCI7CiAgaWYo"
    "Z2V0Q29tcHV0ZWRTdHlsZShiKS5wb3NpdGlvbj09PSJzdGF0aWMiKWIuc3R5bGUucG9zaXRpb249"
    "InJlbGF0aXZlIjsKICBiLmFwcGVuZENoaWxkKHMpO3NldFRpbWVvdXQoKCk9PnMucmVtb3ZlKCks"
    "NTYwKTt9KTsKCi8qIDE1IMK3IHRpbHQgKyBnbGFyZSAqLwpmdW5jdGlvbiBiaW5kVGlsdCgpe2Rv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXRpbHRdIikuZm9yRWFjaChjPT57CiAgaWYo"
    "Yy5fdHx8Uk0pcmV0dXJuO2MuX3Q9MTsKICBjLmFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJtb3Zl"
    "IixlPT57Y29uc3Qgcj1jLmdldEJvdW5kaW5nQ2xpZW50UmVjdCgpOwogICAgY29uc3QgeD0oZS5j"
    "bGllbnRYLXIubGVmdCkvci53aWR0aCx5PShlLmNsaWVudFktci50b3ApL3IuaGVpZ2h0OwogICAg"
    "Yy5zdHlsZS50cmFuc2Zvcm09InBlcnNwZWN0aXZlKDcwMHB4KSByb3RhdGVYKCIrKCguNS15KSo5"
    "KS50b0ZpeGVkKDIpKyJkZWcpIHJvdGF0ZVkoIisoKHgtLjUpKjExKS50b0ZpeGVkKDIpKyJkZWcp"
    "IHRyYW5zbGF0ZVooNHB4KSI7CiAgICBjLnN0eWxlLnNldFByb3BlcnR5KCItLW14IiwoeCoxMDAp"
    "KyIlIik7Yy5zdHlsZS5zZXRQcm9wZXJ0eSgiLS1teSIsKHkqMTAwKSsiJSIpO30pOwogIGMuYWRk"
    "RXZlbnRMaXN0ZW5lcigicG9pbnRlcmxlYXZlIiwoKT0+Yy5zdHlsZS50cmFuc2Zvcm09IiIpO30p"
    "O30KCi8qIHN1Y2Nlc3MgYnVyc3QgKi8KZnVuY3Rpb24gYnVyc3QoKXtjb25zdCBmeD1kb2N1bWVu"
    "dC5nZXRFbGVtZW50QnlJZCgiZngiKTtpZighZngpcmV0dXJuO2Z4LmNsYXNzTGlzdC5hZGQoIm9u"
    "Iik7CiAgY29uc3Qgdz1meC5xdWVyeVNlbGVjdG9yKCIud2F2ZSIpO3cuc3R5bGUuYW5pbWF0aW9u"
    "PSJub25lIjt2b2lkIHcub2Zmc2V0V2lkdGg7dy5zdHlsZS5hbmltYXRpb249IiI7CiAgc2V0VGlt"
    "ZW91dCgoKT0+ZnguY2xhc3NMaXN0LnJlbW92ZSgib24iKSw5MDApO30KCi8qIOKUgOKUgCBQYXkg"
    "KyB2ZXJpZnkgZGVwb3NpdCBmbG93ICg1LW1pbnV0ZSB3aW5kb3csIHZlcmlmeSBvbmx5IG9uIHRh"
    "cCkg4pSA4pSAICovCmxldCBkZXBUaWNrPW51bGwsZGVwSm9iPW51bGw7CmZ1bmN0aW9uIGFtb3Vu"
    "dFNoZWV0KCl7CiAgY29uc3QgcGlja3M9WzUwLDEwMCwyMDAsNTAwLDEwMDAsMjAwMF07CiAgc2hl"
    "ZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPiR7Q01TLndhbEtp"
    "Y2t8fCJBZGQgZnVuZHMifTwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj5QaWNrIGFuIGFtb3VudCBv"
    "ciB0eXBlIHlvdXIgb3duLjwvcD4KICAgIDxkaXYgY2xhc3M9ImNoaXBzIiBzdHlsZT0ibWFyZ2lu"
    "OjEycHggMCA0cHg7ZGlzcGxheTpmbGV4O2ZsZXgtd3JhcDp3cmFwO2dhcDo3cHgiPgogICAgICAk"
    "e3BpY2tzLm1hcCh2PT5gPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBkYXRhLXBpY2s9IiR7dn0iIHN0"
    "eWxlPSJmbGV4OjAgMCBhdXRvIj4ke21vbmV5KHYpfTwvYnV0dG9uPmApLmpvaW4oIiIpfQogICAg"
    "PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxh"
    "YmVsPkN1c3RvbSBhbW91bnQ8L2xhYmVsPgogICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImFt"
    "dGluIiB0eXBlPSJudW1iZXIiIGlucHV0bW9kZT0ibnVtZXJpYyIgbWluPSIxIiBwbGFjZWhvbGRl"
    "cj0iRW50ZXIgYW1vdW50Ij48L2Rpdj4KICAgIDxwIGNsYXNzPSJzdWIiIGlkPSJhbXRoaW50IiBz"
    "dHlsZT0ibWFyZ2luOjAgMCAxMHB4O2ZvbnQtc2l6ZTouNzJyZW0iPjwvcD4KICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBjdGEgYmxvY2siIGlkPSJhbXRnbyI+Q29udGludWU8L2J1dHRvbj4KICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBk"
    "YXRhLWNsb3NlPkNhbmNlbDwvYnV0dG9uPmApOwogIGNvbnN0IGlucD1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiYW10aW4iKSwgZ289ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFtdGdvIiksCiAg"
    "ICAgICAgaGludD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYW10aGludCIpOwogIGNvbnN0IGxv"
    "PShBRE0mJkFETS5wYXkmJkFETS5wYXkubWluKXx8MTAsIGhpPShBRE0mJkFETS5wYXkmJkFETS5w"
    "YXkubWF4KXx8MjAwMDA7CiAgaGludC50ZXh0Q29udGVudD0iQmV0d2VlbiAiK21vbmV5KGxvKSsi"
    "IGFuZCAiK21vbmV5KGhpKSsiLiI7CiAgY29uc3Qgc3RhcnQ9dj0+ewogICAgY29uc3Qgbj1OdW1i"
    "ZXIodik7CiAgICBpZighbnx8bjw9MCl7dG9hc3QoIkVudGVyIGFuIGFtb3VudCBmaXJzdCIpO2lu"
    "cCYmaW5wLmZvY3VzKCk7cmV0dXJuO30KICAgIGlmKG48bG98fG4+aGkpe3RvYXN0KCJBbGxvd2Vk"
    "ICIrbW9uZXkobG8pKyIgdG8gIittb25leShoaSkpO3JldHVybjt9CiAgICBkZXBvc2l0U2hlZXQo"
    "bik7CiAgfTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1waWNrXSIpLmZvckVh"
    "Y2goYj0+Yi5vbmNsaWNrPSgpPT5zdGFydChiLmRhdGFzZXQucGljaykpOwogIGdvLm9uY2xpY2s9"
    "KCk9PnN0YXJ0KGlucC52YWx1ZSk7CiAgaWYoaW5wKWlucC5vbmtleWRvd249ZT0+e2lmKGUua2V5"
    "PT09IkVudGVyIilzdGFydChpbnAudmFsdWUpO307CiAgc2V0VGltZW91dCgoKT0+e2lmKGlucClp"
    "bnAuZm9jdXMoKTt9LDI1MCk7Cn0KYXN5bmMgZnVuY3Rpb24gbGl2ZURlcG9zaXRTaGVldChhKXsK"
    "ICBjbGVhckludGVydmFsKGRlcFRpY2spO2NsZWFyVGltZW91dChkZXBKb2IpOwogIGNvbnN0IGQ9"
    "YXdhaXQgYXBpVHJ5KCIvYXBpL2RlcG9zaXQvY3JlYXRlIix7YW1vdW50Ok51bWJlcihhKX0pOwog"
    "IGlmKCFkKXJldHVybjsKICBjb25zdCBtaW5zPWQud2luZG93fHw1OwogIGxldCBsZWZ0PU1hdGgu"
    "bWF4KDAsTWF0aC5yb3VuZCgobmV3IERhdGUoZC5leHBpcmVzX2F0KS1uZXcgRGF0ZSgpKS8xMDAw"
    "KSl8fG1pbnMqNjA7CiAgY29uc3QgdG90YWw9bWlucyo2MDsKICBsZXQgZG9uZT1mYWxzZSxidXN5"
    "PWZhbHNlOwogIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVt"
    "Ij4ke0NNUy5zaGVldFRpdGxlfSAke21vbmV5KGQuYW1vdW50KX08L2gzPgogICAgPHAgY2xhc3M9"
    "InN1YiI+JHtDTVMuc2hlZXRTdWIucmVwbGFjZSgie219IixtaW5zKX08L3A+CiAgICAke2QucXI/"
    "YDxkaXYgY2xhc3M9InFyIiBzdHlsZT0icGFkZGluZzowO292ZXJmbG93OmhpZGRlbiI+PGltZyBz"
    "cmM9IiR7ZC5xcn0iIGFsdD0iVVBJIFFSIiBzdHlsZT0id2lkdGg6MTAwJTtkaXNwbGF5OmJsb2Nr"
    "Ij48L2Rpdj5gOiIifQogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlVQSSBJRDwvc3Bhbj48Yj4k"
    "e2VzYyhkLnVwaXx8IiIpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5QYXll"
    "ZTwvc3Bhbj48Yj4ke2VzYyhkLnBheWVlfHwiIil9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0i"
    "a3YiPjxzcGFuPkFtb3VudDwvc3Bhbj48Yj4ke21vbmV5KGQuYW1vdW50KX08L2I+PC9kaXY+CiAg"
    "ICAke2QuYm9udXM/YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Cb251czwvc3Bhbj48Yj4rJHttb25l"
    "eShkLmJvbnVzKX08L2I+PC9kaXY+YDoiIn0KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Ob3Rl"
    "IC8gcmVmZXJlbmNlPC9zcGFuPjxiPiR7ZXNjKGQuY29kZSl9PC9iPjwvZGl2PgogICAgPGRpdiBj"
    "bGFzcz0ia3YiPjxzcGFuPkV4cGlyZXMgaW48L3NwYW4+PGIgaWQ9ImRwY2QiPiR7TWF0aC5mbG9v"
    "cihsZWZ0LzYwKX06JHtTdHJpbmcobGVmdCU2MCkucGFkU3RhcnQoMiwiMCIpfTwvYj48L2Rpdj4K"
    "ICAgIDxkaXYgY2xhc3M9InRpbWVyYmFyIiBpZD0iZHBiYXIiPjxpIHN0eWxlPSJ3aWR0aDoxMDAl"
    "Ij48L2k+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJzdGVwcyIgaWQ9ImRzdGVwcyI+CiAgICAgIDxk"
    "aXYgY2xhc3M9InN0ZXAgb24iPjxpPjE8L2k+JHtDTVMuc3RlcDF9PC9kaXY+CiAgICAgIDxkaXYg"
    "Y2xhc3M9InN0ZXAiIGlkPSJzMiI+PGk+MjwvaT4ke0NNUy5zdGVwMn08L2Rpdj4KICAgICAgPGRp"
    "diBjbGFzcz0ic3RlcCIgaWQ9InMzIj48aT4zPC9pPiR7Q01TLnN0ZXAzfTwvZGl2PgogICAgICA8"
    "ZGl2IGNsYXNzPSJzdGVwIiBpZD0iczQiPjxpPjQ8L2k+JHtDTVMuc3RlcDR9PC9kaXY+CiAgICA8"
    "L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij4KICAg"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0iZHBheSI+8J+PpiBDb3B5IFVQSSBJRDwvYnV0dG9u"
    "PgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIiBpZD0iZHZlciI+JHtTVE9SRS5hdXRvY2hl"
    "Y2s9PT1mYWxzZT8i4pyFIEkgaGF2ZSBwYWlkIjoi4pyFICIrQ01TLnZlcmlmeUJ0bn08L2J1dHRv"
    "bj48L2Rpdj4KICAgICR7U1RPUkUuYXV0b2NoZWNrPT09ZmFsc2U/IiI6YDxidXR0b24gY2xhc3M9"
    "ImJ0biBzb2Z0IGJsb2NrIHNtIiBpZD0iZHV0ciIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij7wn6e+"
    "IFNlbmQgdHJhbnNhY3Rpb24gbm8uPC9idXR0b24+YH0KICAgIDxidXR0b24gY2xhc3M9ImJ0biBn"
    "aG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNsb3NlPkNsb3NlPC9i"
    "dXR0b24+YCk7CiAgY29uc3QgZz1pZD0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoaWQpOwogIGNv"
    "bnN0IHBheUI9ZygiZHBheSIpLHZlckI9ZygiZHZlciIpLHV0ckI9ZygiZHV0ciIpOwogIGNvbnN0"
    "IGFsaXZlPSgpPT5nKCJkcGNkIikmJmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzaGVldCIpLmNs"
    "YXNzTGlzdC5jb250YWlucygib3BlbiIpOwogIGZ1bmN0aW9uIGV4cGlyZSgpewogICAgY2xlYXJJ"
    "bnRlcnZhbChkZXBUaWNrKTsKICAgIGlmKCFhbGl2ZSgpKXJldHVybjsKICAgIGcoImRwY2QiKS50"
    "ZXh0Q29udGVudD0iMDowMCI7ZygiZHBiYXIiKS5jbGFzc0xpc3QuYWRkKCJsb3ciKTtnKCJkcGJh"
    "ciIpLmZpcnN0Q2hpbGQuc3R5bGUud2lkdGg9IjAlIjsKICAgIHZlckIuZGlzYWJsZWQ9dHJ1ZTt1"
    "dHJCLmRpc2FibGVkPXRydWU7dmVyQi50ZXh0Q29udGVudD0iRXhwaXJlZCI7CiAgICBwYXlCLmNs"
    "YXNzTmFtZT0iYnRuIjtwYXlCLnRleHRDb250ZW50PSJOZXcgUmVxdWVzdCI7cGF5Qi5vbmNsaWNr"
    "PSgpPT5kZXBvc2l0U2hlZXQoYSk7CiAgICB0b2FzdChDTVMubXNnRXhwaXJlZCk7CiAgfQogIGRl"
    "cFRpY2s9c2V0SW50ZXJ2YWwoKCk9PnsKICAgIGlmKCFhbGl2ZSgpKXtjbGVhckludGVydmFsKGRl"
    "cFRpY2spO3JldHVybjt9CiAgICBsZWZ0LS07CiAgICBnKCJkcGNkIikudGV4dENvbnRlbnQ9TWF0"
    "aC5tYXgoMCxNYXRoLmZsb29yKGxlZnQvNjApKSsiOiIrU3RyaW5nKE1hdGgubWF4KDAsbGVmdCU2"
    "MCkpLnBhZFN0YXJ0KDIsIjAiKTsKICAgIGcoImRwYmFyIikuZmlyc3RDaGlsZC5zdHlsZS53aWR0"
    "aD0oTWF0aC5tYXgoMCxsZWZ0KS90b3RhbCoxMDApKyIlIjsKICAgIGcoImRwYmFyIikuY2xhc3NM"
    "aXN0LnRvZ2dsZSgibG93IixsZWZ0PD02MCk7CiAgICBpZihsZWZ0PD0wKWV4cGlyZSgpOwogIH0s"
    "MTAwMCk7CiAgaWYocGF5QilwYXlCLm9uY2xpY2s9KCk9PnsKICAgIGlmKGRvbmUpcmV0dXJuOwog"
    "ICAgZygiczIiKS5jbGFzc05hbWU9InN0ZXAgb24iO2coInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1"
    "biI7CiAgICB2ZXJCLmNsYXNzTGlzdC5yZW1vdmUoImdsYXNzIik7dmVyQi5jbGFzc0xpc3QuYWRk"
    "KCJjdGEiKTsKICAgIHRyeXtuYXZpZ2F0b3IuY2xpcGJvYXJkJiZuYXZpZ2F0b3IuY2xpcGJvYXJk"
    "LndyaXRlVGV4dChkLnVwaXx8IiIpO31jYXRjaChlKXt9CiAgICB0b2FzdCgiVVBJIElEIGNvcGll"
    "ZDogIisoZC51cGl8fCIiKSk7CiAgfTsKICBpZih2ZXJCKXZlckIub25jbGljaz1hc3luYygpPT57"
    "CiAgICBpZihkb25lfHxidXN5fHxsZWZ0PD0wKXJldHVybjsKICAgIGlmKFNUT1JFLmF1dG9jaGVj"
    "az09PWZhbHNlKXsgICAgICAgICAgLyogbm8gYXV0b21hdGljIGNoZWNraW5nIOKAlCBjb2xsZWN0"
    "IHByb29mICovCiAgICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6"
    "ZToxcmVtIj5TZW5kIHlvdXIgcGF5bWVudCBwcm9vZjwvaDM+CiAgICAgICAgPHAgY2xhc3M9InN1"
    "YiI+QSBzY3JlZW5zaG90IG9mIHRoZSBzdWNjZXNzZnVsIHBheW1lbnQgYW5kIHRoZSBVVFIuPC9w"
    "PgogICAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFi"
    "ZWw+U2NyZWVuc2hvdDwvbGFiZWw+CiAgICAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9InBm"
    "c2hvdCIgdHlwZT0iZmlsZSIgYWNjZXB0PSJpbWFnZS8qIj48L2Rpdj4KICAgICAgICA8ZGl2IGNs"
    "YXNzPSJmaWVsZCI+PGxhYmVsPlVUUiAvIHRyYW5zYWN0aW9uIG51bWJlcjwvbGFiZWw+CiAgICAg"
    "ICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9InBmdXRyIiBpbnB1dG1vZGU9Im51bWVyaWMiIHBs"
    "YWNlaG9sZGVyPSIxMiBkaWdpdHMiPjwvZGl2PgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBj"
    "dGEgYmxvY2siIGlkPSJwZmdvIj5TdWJtaXQgZm9yIHJldmlldzwvYnV0dG9uPgogICAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBk"
    "YXRhLWNsb3NlPkNhbmNlbDwvYnV0dG9uPmApOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgicGZnbyIpLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICAgIGNvbnN0IGY9ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoInBmc2hvdCIpOwogICAgICAgIGNvbnN0IHU9KGRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJwZnV0ciIpLnZhbHVlfHwiIikucmVwbGFjZSgvXEQvZywiIik7CiAgICAgICAgaWYo"
    "dS5sZW5ndGg8Nil7dG9hc3QoIkVudGVyIHRoZSBVVFIgZnJvbSB5b3VyIHBheW1lbnQgYXBwIik7"
    "cmV0dXJuO30KICAgICAgICBjb25zdCBmaWxlPShmJiZmLmZpbGVzJiZmLmZpbGVzWzBdKXx8bnVs"
    "bDsKICAgICAgICBsZXQgc2hvdD0iIjsKICAgICAgICBpZihmaWxlKXsKICAgICAgICAgIGlmKGZp"
    "bGUuc2l6ZT44KjEwMjQqMTAyNCl7dG9hc3QoIlRoYXQgaW1hZ2UgaXMgdG9vIGxhcmdlICg4IE1C"
    "IG1heCkiKTtyZXR1cm47fQogICAgICAgICAgc2hvdD1hd2FpdCBuZXcgUHJvbWlzZShyZXM9Pntj"
    "b25zdCByZD1uZXcgRmlsZVJlYWRlcigpOwogICAgICAgICAgICByZC5vbmxvYWQ9KCk9PnJlcyhT"
    "dHJpbmcocmQucmVzdWx0fHwiIikpO3JkLm9uZXJyb3I9KCk9PnJlcygiIik7CiAgICAgICAgICAg"
    "IHJkLnJlYWRBc0RhdGFVUkwoZmlsZSk7fSk7CiAgICAgICAgfQogICAgICAgIGNvbnN0IGJ0bj1k"
    "b2N1bWVudC5nZXRFbGVtZW50QnlJZCgicGZnbyIpO2J0bi5kaXNhYmxlZD10cnVlO2J0bi50ZXh0"
    "Q29udGVudD0iU2VuZGluZ+KApiI7CiAgICAgICAgY29uc3QgcnI9YXdhaXQgYXBpVHJ5KCIvYXBp"
    "L2RlcG9zaXQvY2xhaW0iLHtpZDpkLmlkLHV0cjp1LHNob3Q6c2hvdH0pOwogICAgICAgIGJ0bi5k"
    "aXNhYmxlZD1mYWxzZTtidG4udGV4dENvbnRlbnQ9IlN1Ym1pdCBmb3IgcmV2aWV3IjsKICAgICAg"
    "ICBpZighcnIpcmV0dXJuOwogICAgICAgIGNsb3NlU2hlZXQoKTt0b2FzdCgiU2VudCDigJQgd2Ug"
    "d2lsbCBjb25maXJtIGl0IHNob3J0bHkiKTsKICAgICAgICBhd2FpdCByZWZyZXNoKCk7CiAgICAg"
    "IH07CiAgICAgIHJldHVybjsKICAgIH0KICAgIGJ1c3k9dHJ1ZTt2ZXJCLmRpc2FibGVkPXRydWU7"
    "dmVyQi50ZXh0Q29udGVudD0iQ2hlY2tpbmfigKYiO2coInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1"
    "biI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9kZXBvc2l0L3ZlcmlmeSIse2lkOmQu"
    "aWR9KTsKICAgIGJ1c3k9ZmFsc2U7CiAgICBpZighYWxpdmUoKSlyZXR1cm47CiAgICB2ZXJCLmRp"
    "c2FibGVkPWZhbHNlO3ZlckIudGV4dENvbnRlbnQ9Q01TLnZlcmlmeUJ0bjsKICAgIGlmKCFyKXJl"
    "dHVybjsKICAgIGlmKHIuc3RhdHVzPT09ImNvbXBsZXRlZCIpewogICAgICBkb25lPXRydWU7Y2xl"
    "YXJJbnRlcnZhbChkZXBUaWNrKTsKICAgICAgZygiczMiKS5jbGFzc05hbWU9InN0ZXAgb24iO2co"
    "InM0IikuY2xhc3NOYW1lPSJzdGVwIG9uIjsKICAgICAgaWYodHlwZW9mIHIuYmFsYW5jZT09PSJu"
    "dW1iZXIiKUJBTD1yLmJhbGFuY2U7CiAgICAgIHN5bmNCYWwoKTtidXJzdCgpO3RvYXN0KENNUy5t"
    "c2dPayk7CiAgICAgIHNldFRpbWVvdXQoKCk9PntjbG9zZVNoZWV0KCk7cmVmcmVzaCgpO30sOTAw"
    "KTsKICAgIH1lbHNlIGlmKHIuc3RhdHVzPT09InJldmlldyIpewogICAgICBnKCJzMyIpLmNsYXNz"
    "TmFtZT0ic3RlcCBvbiI7dG9hc3QoIlBheW1lbnQgc2VudCBmb3IgY2hlY2tpbmcg4oCUIHdlIHdp"
    "bGwgY29uZmlybSBpdCBzaG9ydGx5Iik7CiAgICB9ZWxzZSBpZihyLnN0YXR1cz09PSJleHBpcmVk"
    "Iil7CiAgICAgIGV4cGlyZSgpOwogICAgfWVsc2V7CiAgICAgIHRvYXN0KENNUy5tc2dOb25lKTsK"
    "ICAgIH0KICB9OwogIGlmKHV0ckIpdXRyQi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHY9"
    "cHJvbXB0KCJFbnRlciB0aGUgMTItZGlnaXQgVVRSIGZyb20geW91ciBwYXltZW50IGFwcCIpOwog"
    "ICAgaWYoIXYpcmV0dXJuOwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvZGVwb3NpdC91"
    "dHIiLHtpZDpkLmlkLHV0cjp2fSk7CiAgICBpZihyKXRvYXN0KCJUaGFua3Mg4oCUIHdlIGFyZSBj"
    "aGVja2luZyB5b3VyIHBheW1lbnQiKTsKICB9Owp9CmZ1bmN0aW9uIGRlcG9zaXRTaGVldChhKXsK"
    "ICBpZihMSVZFKXJldHVybiBsaXZlRGVwb3NpdFNoZWV0KGEpOwogIGNsZWFySW50ZXJ2YWwoZGVw"
    "VGljayk7Y2xlYXJUaW1lb3V0KGRlcEpvYik7CiAgY29uc3QgY29kZT0oQ01TLnBheVByZWZpeHx8"
    "IlBBWSIpK01hdGguZmxvb3IoMTAwMCtNYXRoLnJhbmRvbSgpKjg5OTkpOwogIGNvbnN0IG1pbnM9"
    "TWF0aC5tYXgoMSxwYXJzZUludChDTVMucGF5V2luZG93LDEwKXx8NSksIHRvdGFsPW1pbnMqNjA7"
    "CiAgbGV0IGxlZnQ9dG90YWwscGFpZD1mYWxzZSxkb25lPWZhbHNlLGJ1c3k9ZmFsc2U7CiAgc2hl"
    "ZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPiR7Q01TLnNoZWV0"
    "VGl0bGV9ICR7bW9uZXkoYSl9PC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPiR7Q01TLnNoZWV0U3Vi"
    "LnJlcGxhY2UoInttfSIsbWlucyl9PC9wPgogICAgPGRpdiBjbGFzcz0icXIiPlFSIENPREU8YnI+"
    "KGdlbmVyYXRlZCBieSBib3QpPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VVBJIElE"
    "PC9zcGFuPjxiPiR7ZXNjKFNUT1JFLnVwaXx8Im5vdCBzZXQgeWV0Iil9PC9iPjwvZGl2PgogICAg"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPlBheWVlPC9zcGFuPjxiPiR7ZXNjKFNUT1JFLnBheWVlfHwi"
    "4oCUIil9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkFtb3VudDwvc3Bhbj48"
    "Yj4ke21vbmV5KGEpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Ob3RlIC8g"
    "cmVmZXJlbmNlPC9zcGFuPjxiPiR7Y29kZX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+"
    "PHNwYW4+RXhwaXJlcyBpbjwvc3Bhbj48YiBpZD0iZHBjZCI+JHttaW5zfTowMDwvYj48L2Rpdj4K"
    "ICAgIDxkaXYgY2xhc3M9InRpbWVyYmFyIiBpZD0iZHBiYXIiPjxpIHN0eWxlPSJ3aWR0aDoxMDAl"
    "Ij48L2k+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJzdGVwcyIgaWQ9ImRzdGVwcyI+CiAgICAgIDxk"
    "aXYgY2xhc3M9InN0ZXAgb24iPjxpPjE8L2k+JHtDTVMuc3RlcDF9PC9kaXY+CiAgICAgIDxkaXYg"
    "Y2xhc3M9InN0ZXAiIGlkPSJzMiI+PGk+MjwvaT4ke0NNUy5zdGVwMn08L2Rpdj4KICAgICAgPGRp"
    "diBjbGFzcz0ic3RlcCIgaWQ9InMzIj48aT4zPC9pPiR7Q01TLnN0ZXAzfTwvZGl2PgogICAgICA8"
    "ZGl2IGNsYXNzPSJzdGVwIiBpZD0iczQiPjxpPjQ8L2k+JHtDTVMuc3RlcDR9PC9kaXY+CiAgICA8"
    "L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij4KICAg"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIgaWQ9ImRwYXkiPiR7Q01TLnBheUJ0bn08L2J1dHRv"
    "bj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdsYXNzIiBpZD0iZHZlciI+JHtDTVMudmVyaWZ5"
    "QnRufTwvYnV0dG9uPjwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQgYmxvY2sgc20i"
    "IGlkPSJkdXRyIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPiR7Q01TLnV0ckJ0bn08L2J1dHRvbj4K"
    "ICAgIDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6"
    "OHB4IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+YCk7CiAgY29uc3QgZz1pZD0+ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoaWQpOwogIGNvbnN0IHBheUI9ZygiZHBheSIpLHZlckI9ZygiZHZlciIp"
    "LHV0ckI9ZygiZHV0ciIpOwogIGZ1bmN0aW9uIGZtdChuKXtyZXR1cm4gTWF0aC5mbG9vcihuLzYw"
    "KSsiOiIrU3RyaW5nKG4lNjApLnBhZFN0YXJ0KDIsIjAiKTt9CiAgZnVuY3Rpb24gYWxpdmUoKXty"
    "ZXR1cm4gZygiZHBjZCIpJiZkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic2hlZXQiKS5jbGFzc0xp"
    "c3QuY29udGFpbnMoIm9wZW4iKTt9CiAgZnVuY3Rpb24gZXhwaXJlKCl7CiAgICBjbGVhckludGVy"
    "dmFsKGRlcFRpY2spO2NsZWFyVGltZW91dChkZXBKb2IpOwogICAgaWYoIWFsaXZlKCkpcmV0dXJu"
    "OwogICAgZygiZHBjZCIpLnRleHRDb250ZW50PSIwOjAwIjtnKCJkcGJhciIpLmNsYXNzTGlzdC5h"
    "ZGQoImxvdyIpO2coImRwYmFyIikuZmlyc3RDaGlsZC5zdHlsZS53aWR0aD0iMCUiOwogICAgcGF5"
    "Qi5kaXNhYmxlZD10cnVlO3ZlckIuZGlzYWJsZWQ9dHJ1ZTt1dHJCLmRpc2FibGVkPXRydWU7CiAg"
    "ICBnKCJzMiIpLmNsYXNzTmFtZT0ic3RlcCI7ZygiczMiKS5jbGFzc05hbWU9InN0ZXAiOwogICAg"
    "dmVyQi50ZXh0Q29udGVudD0iRXhwaXJlZCI7cGF5Qi5jbGFzc05hbWU9ImJ0biI7cGF5Qi5kaXNh"
    "YmxlZD1mYWxzZTtwYXlCLnRleHRDb250ZW50PSJOZXcgUmVxdWVzdCI7CiAgICBwYXlCLm9uY2xp"
    "Y2s9KCk9PmRlcG9zaXRTaGVldChhKTsKICAgIHRvYXN0KENNUy5tc2dFeHBpcmVkKTsKICB9CiAg"
    "ZGVwVGljaz1zZXRJbnRlcnZhbCgoKT0+ewogICAgaWYoIWFsaXZlKCkpe2NsZWFySW50ZXJ2YWwo"
    "ZGVwVGljayk7cmV0dXJuO30KICAgIGxlZnQtLTtnKCJkcGNkIikudGV4dENvbnRlbnQ9Zm10KE1h"
    "dGgubWF4KDAsbGVmdCkpOwogICAgZygiZHBiYXIiKS5maXJzdENoaWxkLnN0eWxlLndpZHRoPShN"
    "YXRoLm1heCgwLGxlZnQpL3RvdGFsKjEwMCkrIiUiOwogICAgZygiZHBiYXIiKS5jbGFzc0xpc3Qu"
    "dG9nZ2xlKCJsb3ciLGxlZnQ8PTYwKTsKICAgIGlmKGxlZnQ8PTApZXhwaXJlKCk7CiAgfSwxMDAw"
    "KTsKICBpZihwYXlCKXBheUIub25jbGljaz0oKT0+ewogICAgaWYoZG9uZSlyZXR1cm47CiAgICBw"
    "YWlkPXRydWU7ZygiczIiKS5jbGFzc05hbWU9InN0ZXAgb24iO2coInMzIikuY2xhc3NOYW1lPSJz"
    "dGVwIHJ1biI7CiAgICBwYXlCLnRleHRDb250ZW50PSJQYXkgQWdhaW4iO3ZlckIuY2xhc3NMaXN0"
    "LnJlbW92ZSgiZ2xhc3MiKTt2ZXJCLmNsYXNzTGlzdC5hZGQoImN0YSIpOwogICAgdG9hc3QoIk9w"
    "ZW5pbmcgeW91ciBVUEkgYXBwIOKAlCBwYXkgIittb25leShhKSsiLCB0aGVuIHRhcCBWZXJpZnki"
    "KTsKICB9OwogIHZlckIub25jbGljaz0oKT0+ewogICAgaWYoZG9uZXx8YnVzeXx8bGVmdDw9MCly"
    "ZXR1cm47CiAgICBidXN5PXRydWU7dmVyQi5kaXNhYmxlZD10cnVlO3ZlckIudGV4dENvbnRlbnQ9"
    "IkNoZWNraW5n4oCmIjsKICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1biI7dG9hc3QoQ01T"
    "Lm1zZ0NoZWNraW5nKTsKICAgIGRlcEpvYj1zZXRUaW1lb3V0KCgpPT57CiAgICAgIGJ1c3k9ZmFs"
    "c2U7CiAgICAgIGlmKCFhbGl2ZSgpKXJldHVybjsKICAgICAgaWYocGFpZCl7CiAgICAgICAgZG9u"
    "ZT10cnVlO2NsZWFySW50ZXJ2YWwoZGVwVGljayk7CiAgICAgICAgZygiczMiKS5jbGFzc05hbWU9"
    "InN0ZXAgb24iO2coInM0IikuY2xhc3NOYW1lPSJzdGVwIG9uIjsKICAgICAgICB2ZXJCLnRleHRD"
    "b250ZW50PSJWZXJpZmllZCI7CiAgICAgICAgQkFMKz1OdW1iZXIoYSk7c3luY0JhbCgpO2J1cnN0"
    "KCk7dG9hc3QoQ01TLm1zZ09rKTsKICAgICAgICBzZXRUaW1lb3V0KGNsb3NlU2hlZXQsMTIwMCk7"
    "CiAgICAgIH1lbHNlewogICAgICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIjt2ZXJCLmRpc2Fi"
    "bGVkPWZhbHNlO3ZlckIudGV4dENvbnRlbnQ9Q01TLnZlcmlmeUJ0bjsKICAgICAgICB0b2FzdChD"
    "TVMubXNnTm9uZSk7CiAgICAgIH0KICAgIH0sMTcwMCk7CiAgfTsKICB1dHJCLm9uY2xpY2s9KCk9"
    "PntpZihsZWZ0PjApdG9hc3QoIlRoYW5rcyDigJQgd2UgYXJlIGNoZWNraW5nIHlvdXIgcGF5bWVu"
    "dCIpO307Cn0KZnVuY3Rpb24gc3luY0JhbCgpe3RpY2tUbyhkb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgiYmFsIiksQkFMKTt0aWNrVG8oZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJhbDIiKSxCQUwp"
    "O30KZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigiY2xpY2siLGU9Pntjb25zdCBjPWUudGFyZ2V0"
    "LmNsb3Nlc3QoIltkYXRhLWNvbmZpcm1dIik7aWYoIWMpcmV0dXJuOwogIGNvbnN0IHA9K2MuZGF0"
    "YXNldC5jb25maXJtO2lmKEJBTDxwKXt0b2FzdCgiTm90IGVub3VnaCBiYWxhbmNlIOKAlCBhZGQg"
    "ZnVuZHMiKTtyZXR1cm59CiAgQkFMLT1wO3N5bmNCYWwoKTtjbG9zZVNoZWV0KCk7YnVyc3QoKTt0"
    "b2FzdCgiUHVyY2hhc2VkIOKAlCBPVFAgaXMgaW4gT3JkZXJzIik7fSk7CgovKiDilIDilIAgQURN"
    "SU4g4pSA4pSAICovCmxldCBBVEFCPSJkYXNoIjsKZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgi"
    "I2FzdWIgYnV0dG9uIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICBkb2N1bWVudC5xdWVy"
    "eVNlbGVjdG9yQWxsKCIjYXN1YiBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnJlbW92"
    "ZSgib24iKSk7Yi5jbGFzc0xpc3QuYWRkKCJvbiIpOwogIEFUQUI9Yi5kYXRhc2V0LmE7cmVuZGVy"
    "QWRtaW4oKTt3aW5kb3cuc2Nyb2xsVG8oMCwwKTt9KTsKZnVuY3Rpb24gcmVuZGVyQWRtaW4oKXsK"
    "ICBjb25zdCBzdD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYXN0YWdlIik7CiAgaWYoIWFkbWlu"
    "QWxsb3dlZCgpKXtzdC5pbm5lckhUTUw9JzxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJtYXJnaW46"
    "MTZweDt0ZXh0LWFsaWduOmNlbnRlciI+JwogICAgKyc8aDMgc3R5bGU9Im1hcmdpbjowIDAgNnB4"
    "Ij5BZG1pbnMgb25seTwvaDM+JwogICAgKyc8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAi"
    "PlRoaXMgcGFuZWwgaXMgbm90IGF2YWlsYWJsZSBvbiB5b3VyIGFjY291bnQuPC9wPjwvZGl2Pic7"
    "cmV0dXJuO30KICBzdC5pbm5lckhUTUw9KHtkYXNoOmFEYXNoLGNoYW5uZWxzOmFDaGFubmVscyxs"
    "b2dzOmFMb2dzLHBheTphUGF5LGNvbnRlbnQ6YUNvbnRlbnQsZmVhdHVyZXM6YUZlYXR1cmVzLG1h"
    "cmtldDphTWFya2V0LGFuYWx5dGljczphQW5hbHl0aWNzLHNlY3VyaXR5OmFTZWN1cml0eSxzdG9j"
    "azphU3RvY2ssb3JkZXJzOmFPcmRlcnMsdXNlcnM6YVVzZXJzLHNldHRpbmdzOmFTZXR0aW5nc30p"
    "W0FUQUJdKCk7CiAgYmluZEFkbWluKCk7cmV2ZWFsU3RhZ2Uoc3QpO2JpbmRUaWx0KCk7aWYod2lu"
    "ZG93Ll9fdnRhYlBsYWNlKXJlcXVlc3RBbmltYXRpb25GcmFtZSh3aW5kb3cuX192dGFiUGxhY2Up"
    "O30KCmZ1bmN0aW9uIGFEYXNoKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJrcGlzIj4ke1tbIlJl"
    "dmVudWUgdG9kYXkiLG1vbmV5KEFETS5rcGkucmV2KV0sWyJPcmRlcnMgdG9kYXkiLGFkbU51bShB"
    "RE0ua3BpLm9yZGVycyldLAogICAgICBbIlVzZXJzIixhZG1OdW0oQURNLmtwaS51c2VycyldLFsi"
    "QXV0by12ZXJpZmllZCIsYWRtTnVtKEFETS5rcGkuYXV0byldXQogICAgLm1hcCgoW2ssdl0pPT5g"
    "PGRpdiBjbGFzcz0ia3BpIj48c21hbGw+JHtrfTwvc21hbGw+PGI+JHt2fTwvYj48L2Rpdj5gKS5q"
    "b2luKCIiKX08L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6"
    "ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxs"
    "IG9uIj48aSBjbGFzcz0iZG90Ij48L2k+UGF5bWVudCBjaGVja2luZzwvc3Bhbj4KICAgICA8ZGl2"
    "IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10"
    "eDIpIj4ke0FETS5wYXkuZ21haWxfc2V0PyJHbWFpbCB3YXRjaGVyIG9uIMK3ICIrQURNLnBheS5w"
    "ZW5kaW5nKyIgYXdhaXRpbmcgcmV2aWV3IjoiR21haWwgbm90IHNldCDigJQgZGVwb3NpdHMgZ28g"
    "dG8gbWFudWFsIHJldmlldyJ9PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQi"
    "IGRhdGEtYS1nbz0icGF5Ij5PcGVuPC9idXR0b24+PC9kaXY+CiAgIDxkaXYgY2xhc3M9ImNhcmQi"
    "PjxoMz5MYXN0IDcgZGF5czwvaDM+PHAgY2xhc3M9InN1YiI+UmV2ZW51ZSB0cmVuZDwvcD4KICAg"
    "ICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6ZmxleC1lbmQ7Z2FwOjdweDto"
    "ZWlnaHQ6OTZweDttYXJnaW4tdG9wOjE0cHgiPgogICAgICAgJHtBRE0uYmFycy5tYXAoaD0+YDxp"
    "IHN0eWxlPSJmbGV4OjE7aGVpZ2h0OiR7aH0lO2JvcmRlci1yYWRpdXM6N3B4IDdweCAzcHggM3B4"
    "O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2Rpc3BsYXk6YmxvY2siPjwvaT5gKS5qb2luKCIi"
    "KX08L2Rpdj48L2Rpdj4KICAgJHtHKCJkaWdlc3QiLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBz"
    "dHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNw"
    "YW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5EaWdlc3Q8L3NwYW4+CiAgICAg"
    "PGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFy"
    "KC0tdHgyKSI+TmV4dCBzdW1tYXJ5IGF0IDIzOjMwPC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIHhzIHNvZnQiIGRhdGEtYS1nbz0iYW5hbHl0aWNzIj5WaWV3PC9idXR0b24+PC9kaXY+YCl9"
    "CiAgICR7Rygic2FuZGJveCIsYDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5"
    "OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0icGls"
    "bCI+U2FuZGJveDwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9u"
    "dC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj4ke0FETS5zZXR0aW5ncy5zYW5kYm94PyJU"
    "ZXN0IG1vZGUgaXMgT04iOiJUZXN0IG1vZGUgaXMgb2ZmIOKAlCByZWFsIG1vbmV5IGlzIGxpdmUi"
    "fTwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuc2FuZGJveCk/IiBv"
    "biI6IiJ9IiBkYXRhLXNldHRpbmc9InNhbmRib3giPjwvZGl2PjwvZGl2PmApfQogICA8ZGl2IGNs"
    "YXNzPSJzZWMiPjxoMj5RdWljayBBY3Rpb25zPC9oMj48L2Rpdj4KICAgPGRpdiBjbGFzcz0iZ3Jp"
    "ZCI+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtcWE9ImJyb2FkY2FzdCI+QnJv"
    "YWRjYXN0PC9idXR0b24+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtcWE9InN0"
    "b2NrIj5BZGQgU3RvY2s8L2J1dHRvbj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0"
    "YS1xYT0iZmxhc2giPkZsYXNoIFNhbGU8L2J1dHRvbj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "c29mdCIgZGF0YS1xYT0iYmFja3VwIj5CYWNrdXAgREI8L2J1dHRvbj48L2Rpdj5gOwp9CgpmdW5j"
    "dGlvbiBhQ2hhbm5lbHMoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Gb3JjZS1q"
    "b2luIGNoYW5uZWxzPC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPkFkZCBvciByZW1vdmUgYW55IG51"
    "bWJlciBvZiBjaGFubmVscy4gU2F2ZWQgdG8gdGhlIGRhdGFiYXNlIOKAlCBpbnN0YW50LCBubyBy"
    "ZXN0YXJ0LjwvcD4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxM3B4"
    "Ij48bGFiZWw+Q2hhbm5lbCBsaW5rLCBAdXNlcm5hbWUgb3IgLTEwMCBJRDwvbGFiZWw+CiAgICAg"
    "IDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0iY2gtaW4iIHBsYWNlaG9sZGVyPSJAY2hhbm5lbCDCtyB0"
    "Lm1lLytpbnZpdGUgwrcgLTEwMOKApiI+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxh"
    "YmVsPlR5cGU8L2xhYmVsPgogICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIGlkPSJjaC10eXBlIj48"
    "b3B0aW9uIHZhbHVlPSIxIj5SZXF1aXJlZCDigJQgbXVzdCBqb2luPC9vcHRpb24+PG9wdGlvbiB2"
    "YWx1ZT0iMCI+T3B0aW9uYWwg4oCUIGp1c3Qgc3VnZ2VzdGVkPC9vcHRpb24+PC9zZWxlY3Q+PC9k"
    "aXY+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciPjxidXR0b24gY2xhc3M9ImJ0biIgaWQ9ImNoLWFk"
    "ZCI+JHtpYygicGx1cyIpfSBBZGQgQ2hhbm5lbDwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIGdob3N0IiBkYXRhLW1pc2M9ImNoYW5uZWx0ZXN0Ij5UZXN0IEJvdDwvYnV0dG9uPjwvZGl2"
    "PjwvZGl2PgogIDxkaXYgY2xhc3M9InNlYyI+PGgyPkFjdGl2ZSAoJHtDSEFOTkVMUy5sZW5ndGh9"
    "KTwvaDI+PC9kaXY+CiAgJHtDSEFOTkVMUy5tYXAoKGMsaSk9PmA8ZGl2IGNsYXNzPSJjYXJkIHRp"
    "Z2h0Ij4KICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dh"
    "cDoxMHB4Ij4KICAgICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoInNlbmQiKX08L2Rpdj4KICAg"
    "ICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTou"
    "ODNyZW07ZGlzcGxheTpibG9jazt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3Rl"
    "eHQtb3ZlcmZsb3c6ZWxsaXBzaXMiPiR7ZXNjKGMubmFtZSl9PC9iPgogICAgICAgICA8c21hbGwg"
    "c3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+JHtlc2MoYy5oYW5kbGUp"
    "fSDCtyAke2MubWVtYmVyc30gbWVtYmVyczwvc21hbGw+PC9kaXY+CiAgICAgICA8ZGl2IGNsYXNz"
    "PSJ0ZyBzbSAke2Mub24/Im9uIjoiIn0iIGRhdGEtY2hvbj0iJHtjLmlkfSI+PC9kaXY+PC9kaXY+"
    "CiAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo2cHg7bWFyZ2luLXRvcDoxMHB4O2Zs"
    "ZXgtd3JhcDp3cmFwIj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyAke2MucmVxPyIiOiJz"
    "b2Z0In0iIGRhdGEtY2hyZXE9IiR7Yy5pZH0iPiR7Yy5yZXE/IlJlcXVpcmVkIjoiT3B0aW9uYWwi"
    "fTwvYnV0dG9uPgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtY29weT0i"
    "dGV4dCIgZGF0YS1jb3B5LXZhbHVlPSIke2VzYyhjLmhhbmRsZSl9Ij5Db3B5PC9idXR0b24+CiAg"
    "ICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgYmFkIiBzdHlsZT0ibWFyZ2luLWxlZnQ6YXV0byIg"
    "ZGF0YS1jaGRlbD0iJHtjLmlkfSI+UmVtb3ZlPC9idXR0b24+PC9kaXY+PC9kaXY+YCkuam9pbigi"
    "IikKICAgfHwnPGRpdiBjbGFzcz0iY2FyZCI+PHAgY2xhc3M9InN1YiI+Tm8gY2hhbm5lbHMg4oCU"
    "IHVzZXJzIGNhbiBlbnRlciBmcmVlbHkuPC9wPjwvZGl2Pid9YDsKfQoKZnVuY3Rpb24gYUxvZ3Mo"
    "KXsKICBjb25zdCBvcHRzPWlkPT5MT0dDSC5tYXAobD0+YDxvcHRpb24gdmFsdWU9IiR7bC5pZH0i"
    "ICR7bC5pZD09PWlkPyJzZWxlY3RlZCI6IiJ9PiR7ZXNjKGwubmFtZSl9PC9vcHRpb24+YCkuam9p"
    "bigiIik7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHls"
    "ZT0icGFkZGluZy10b3A6MCI+CiAgICAgIDxkaXYgY2xhc3M9InQiPkxvZ2dpbmcgbWFzdGVyIHN3"
    "aXRjaDxzbWFsbD5PZmYgPSB0aGUgYm90IHBvc3RzIG5vIGxvZ3MgYW55d2hlcmU8L3NtYWxsPjwv"
    "ZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyAke0xPR1NfTUFTVEVSPyJvbiI6IiJ9IiBkYXRhLW1h"
    "c3Rlcj48L2Rpdj48L2Rpdj48L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxvZyBjaGFu"
    "bmVsczwvaDM+PHAgY2xhc3M9InN1YiI+QWRkIGFzIG1hbnkgYXMgeW91IGxpa2UuIFRoZSBzdGFy"
    "cmVkIG9uZSBpcyB0aGUgcHJlZmVycmVkL2RlZmF1bHQgY2hhbm5lbC48L3A+CiAgICAgPGRpdiBj"
    "bGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5DaGFubmVsIGxpbmss"
    "IEB1c2VybmFtZSBvciAtMTAwIElEPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBp"
    "ZD0ibGctaW4iIHBsYWNlaG9sZGVyPSJAbG9nc19jaGFubmVsIMK3IC0xMDDigKYiPjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0ibGctYWRkIj4k"
    "e2ljKCJwbHVzIil9IEFkZCBMb2cgQ2hhbm5lbDwvYnV0dG9uPgogICAgIDxidXR0b24gY2xhc3M9"
    "ImJ0biBzb2Z0IiBkYXRhLW1pc2M9ImxvZ3Rlc3QiPlNlbmQgVGVzdDwvYnV0dG9uPjwvZGl2Pjwv"
    "ZGl2PgogICAke0xPR0NILm1hcChsPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRp"
    "c3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICA8ZGl2IGNsYXNz"
    "PSJzdGFyICR7bC5wcmltYXJ5PyJvbiI6IiJ9IiBkYXRhLWxnc3Rhcj0iJHtsLmlkfSI+JHtpYygi"
    "c3RhciIpfTwvZGl2PgogICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0"
    "eWxlPSJmb250LXNpemU6LjgzcmVtO2Rpc3BsYXk6YmxvY2s7d2hpdGUtc3BhY2U6bm93cmFwO292"
    "ZXJmbG93OmhpZGRlbjt0ZXh0LW92ZXJmbG93OmVsbGlwc2lzIj4ke2VzYyhsLm5hbWUpfTwvYj4K"
    "ICAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+"
    "JHtlc2MobC5oYW5kbGUpfSR7bC5wcmltYXJ5PyIgwrcgcHJlZmVycmVkIjoiIn08L3NtYWxsPjwv"
    "ZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyBzbSAke2wub24/Im9uIjoiIn0iIGRhdGEtbGdvbj0i"
    "JHtsLmlkfSI+PC9kaXY+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBiYWQiIGRhdGEtbGdk"
    "ZWw9IiR7bC5pZH0iPlJlbW92ZTwvYnV0dG9uPjwvZGl2PmApLmpvaW4oIiIpCiAgICB8fCc8ZGl2"
    "IGNsYXNzPSJjYXJkIj48cCBjbGFzcz0ic3ViIj5ObyBsb2cgY2hhbm5lbHMgeWV0LjwvcD48L2Rp"
    "dj4nfQogICA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5FdmVudHMgJmFtcDsgcm91dGluZzwvaDI+PGEg"
    "ZGF0YS1ldi1hbGw+VG9nZ2xlIGFsbDwvYT48L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+JHtF"
    "VkVOVFMubWFwKChlLGkpPT5gCiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCIg"
    "c3R5bGU9ImZsZXg6MSI+JHtlc2MoZS5rKX0KICAgICAgIDxzbWFsbD48c2VsZWN0IGNsYXNzPSJp"
    "bnAiIHN0eWxlPSJoZWlnaHQ6MzBweDtmb250LXNpemU6LjdyZW07bWFyZ2luLXRvcDo1cHg7bWF4"
    "LXdpZHRoOjE5MHB4IiBkYXRhLWV2Y2g9IiR7aX0iICR7ZS5vbj8iIjoiZGlzYWJsZWQifT4ke29w"
    "dHMoZS5jaCl9PC9zZWxlY3Q+PC9zbWFsbD48L2Rpdj4KICAgICAgIDxkaXYgY2xhc3M9InRnICR7"
    "ZS5vbj8ib24iOiIifSIgZGF0YS1ldm9uPSIke2l9Ij48L2Rpdj48L2Rpdj5gKS5qb2luKCIiKX08"
    "L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlJlY2VudCBsb2cgb3V0cHV0PC9oMz4KICAg"
    "ICAkeygoQURNLmxvZ2ZlZWQmJkFETS5sb2dmZWVkLmxlbmd0aCk/QURNLmxvZ2ZlZWQubWFwKGw9"
    "PltsLndoYXQsbC53aGVyZSxsLndoZW5dKTpbXSkKICAgICAgIC5tYXAoKFt0LGMsd10pPT5gPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPiR7dH08L3NwYW4+PGIgc3R5bGU9ImZvbnQtc2l6ZTouN3JlbSI+"
    "JHtjfSDCtyAke3d9PC9iPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PmA7Cn0KCmZ1bmN0aW9uIGFQ"
    "YXkoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxl"
    "PSJwYWRkaW5nLXRvcDowIj4KICAgICAgPGRpdiBjbGFzcz0idCI+R21haWwgYXV0by12ZXJpZnk8"
    "c21hbGw+JHtBRE0ucGF5LmdtYWlsX3NldD8iUmVhZGluZyAiK2VzYyhBRE0ucGF5LmdtYWlsKToi"
    "Tm90IGNvbmZpZ3VyZWQg4oCUIGRlcG9zaXRzIHF1ZXVlIGZvciByZXZpZXcifTwvc21hbGw+PC9k"
    "aXY+CiAgICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuZ21haWxfZW5hYmxlZCk/"
    "IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImdtYWlsX2VuYWJsZWQiPjwvZGl2PjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPkF1dG8tYXBwcm92ZSBzbWFsbCBkZXBv"
    "c2l0czxzbWFsbD5Ta2lwIHJldmlldyBiZWxvdyB0aGUgdGhyZXNob2xkPC9zbWFsbD48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmdtYWlsX2VuYWJsZWQpPyIgb24i"
    "OiIifSIgZGF0YS1zZXR0aW5nPSJnbWFpbF9lbmFibGVkIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5NYW51YWwgVVRSIGZhbGxiYWNrPHNtYWxsPkxl"
    "dCB1c2VycyBzdWJtaXQgYSBVVFIgaWYgYXV0by12ZXJpZnkgbWlzc2VzPC9zbWFsbD48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmF1dG9kZXApPyIgb24iOiIifSIg"
    "ZGF0YS1zZXR0aW5nPSJhdXRvZGVwIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJzd2Fw"
    "Ij48ZGl2IGNsYXNzPSJ0Ij5WZXJpZnkgb25seSB3aGVuIHVzZXIgdGFwcyBWZXJpZnk8c21hbGw+"
    "Tm8gYmFja2dyb3VuZCBjaGVja2luZyDigJQgc2F2ZXMgR21haWwgcXVvdGE8L3NtYWxsPjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cudXRyX2ZhbGxiYWNrKT8iIG9u"
    "IjoiIn0iIGRhdGEtc2V0dGluZz0idXRyX2ZhbGxiYWNrIj48L2Rpdj48L2Rpdj48L2Rpdj4KICAg"
    "PGRpdiBjbGFzcz0iY2FyZCI+PGgzPlVQSTwvaDM+PHAgY2xhc3M9InN1YiI+JHtBRE0ucGF5LnVw"
    "aT8iSW4gdXNlIGJ5IGV2ZXJ5IHBheW1lbnQgcmVxdWVzdC4iOiJOb3Qgc2V0IHlldCDigJQgdG9w"
    "LXVwcyBhcmUgZGlzYWJsZWQgdW50aWwgeW91IGFkZCBpdC4ifTwvcD4KICAgICA8ZGl2IGNsYXNz"
    "PSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlVQSSBJRDwvbGFiZWw+CiAg"
    "ICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9InVwaV9pZCIgdmFsdWU9IiR7ZXNjKEFE"
    "TS5wYXkudXBpfHwiIil9IiBwbGFjZWhvbGRlcj0ieW91cmhhbmRsZUBiYW5rIj48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlBheWVlIG5hbWU8L2xhYmVsPgogICAgICAgPGlu"
    "cHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJ1cGlfbmFtZSIgdmFsdWU9IiR7ZXNjKEFETS5wYXku"
    "cGF5ZWV8fCIiKX0iIHBsYWNlaG9sZGVyPSJZb3VyIFN0b3JlIj48L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJmaWVsZCI+PGxhYmVsPk5vdGUgcHJlZml4PC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFz"
    "cz0iaW5wIiBkYXRhLXNldD0icGF5X3ByZWZpeCIgdmFsdWU9IiR7ZXNjKEFETS5wYXkucHJlZml4"
    "fHwiUEFZIil9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlBheW1lbnQg"
    "d2luZG93IChtaW51dGVzKTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1z"
    "ZXQ9InBheV93aW5kb3ciIHZhbHVlPSIke0FETS5wYXkud2luZG93fSI+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0iZmllbGQiPjxsYWJlbD5NaW5pbXVtIGRlcG9zaXQ8L2xhYmVsPgogICAgICAgPGlu"
    "cHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJtaW5fZGVwb3NpdCIgdmFsdWU9IiR7QURNLnBheS5t"
    "aW59Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPk1heGltdW0gZGVwb3Np"
    "dDwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9Im1heF9kZXBvc2l0"
    "IiB2YWx1ZT0iJHtBRE0ucGF5Lm1heH0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48"
    "bGFiZWw+QXV0by1hcHByb3ZlIHVuZGVyICgwID0gb2ZmKTwvbGFiZWw+CiAgICAgICA8aW5wdXQg"
    "Y2xhc3M9ImlucCIgZGF0YS1zZXQ9ImF1dG9kZXBfbWF4IiB2YWx1ZT0iJHtBRE0ucGF5LmF1dG9k"
    "ZXB9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkdtYWlsIGFkZHJlc3M8"
    "L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJnbWFpbF9pZCIgdmFs"
    "dWU9IiR7ZXNjKEFETS5wYXkuZ21haWx8fCIiKX0iIHBsYWNlaG9sZGVyPSJzdG9yZUBnbWFpbC5j"
    "b20iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+R21haWwgYXBwIHBhc3N3"
    "b3JkPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0iZ21haWxfcGFz"
    "cyIgdHlwZT0icGFzc3dvcmQiIHBsYWNlaG9sZGVyPSIke0FETS5wYXkuZ21haWxfc2V0PyJzYXZl"
    "ZCDigJQgdHlwZSB0byByZXBsYWNlIjoibm90IHNldCJ9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNz"
    "PSJrdiI+PHNwYW4+V2FpdGluZyBmb3IgcmV2aWV3PC9zcGFuPjxiPiR7QURNLnBheS5wZW5kaW5n"
    "fTwvYj48L2Rpdj48L2Rpdj4KICAgJHtHKCJjcnlwdG8iLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+"
    "Q3J5cHRvIHdhbGxldDwvaDM+PHAgY2xhc3M9InN1YiI+VVNEVC1UUkMyMCBkZXBvc2l0cywgYXV0"
    "by1jb25maXJtZWQgb24tY2hhaW4uPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDoxMHB4Ij48bGFiZWw+V2FsbGV0IGFkZHJlc3M8L2xhYmVsPjxpbnB1dCBjbGFz"
    "cz0iaW5wIiBwbGFjZWhvbGRlcj0iVOKApiI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "PjxsYWJlbD5Db25maXJtYXRpb25zIHJlcXVpcmVkPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIg"
    "dmFsdWU9IjEiPjwvZGl2PgogICAgIDwvZGl2PmApfQogICAke0coInVwaVJvdGF0ZSIsYDxkaXYg"
    "Y2xhc3M9ImNhcmQiPjxoMz5VUEkgcm90YXRpb248L2gzPjxwIGNsYXNzPSJzdWIiPlNwcmVhZCBs"
    "b2FkIGFuZCBzdXJ2aXZlIGEgZnJvemVuIGhhbmRsZS48L3A+CiAgICAgJHtbWyJoYW5kbGUxQGZh"
    "bSIsIkFjdGl2ZSJdLFsiaGFuZGxlMkBmYW0iLCJBY3RpdmUiXSxbImhhbmRsZTNAdXBpIiwiUGF1"
    "c2VkIl1dCiAgICAgICAubWFwKChbaCxzdF0pPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7aH08"
    "L3NwYW4+PGI+PHNwYW4gY2xhc3M9InBpbGwgJHtzdD09PSJBY3RpdmUiPyJvbiI6Im9mZiJ9Ij4k"
    "e3N0fTwvc3Bhbj48L2I+PC9kaXY+YCkuam9pbigiIil9CiAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4"
    "Ij4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBwbGFjZWhvbGRlcj0iYW5vdGhlckB1cGkiPjxi"
    "dXR0b24gY2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJ1cGlhZGQiPkFk"
    "ZDwvYnV0dG9uPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImRlcEJvbnVzIixgPGRpdiBj"
    "bGFzcz0iY2FyZCI+PGgzPkRlcG9zaXQgYm9udXM8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZpZWxk"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD7igrk1MDAgYW5kIGFib3ZlICglKTwvbGFi"
    "ZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJm"
    "aWVsZCI+PGxhYmVsPuKCuTEwMDAgYW5kIGFib3ZlICglKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJp"
    "bnAiIHZhbHVlPSI4Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPuKCuTIw"
    "MDAgYW5kIGFib3ZlICglKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxMiI+PC9k"
    "aXY+PC9kaXY+YCl9CiAgICR7RygiZGVwTGltaXRzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkRl"
    "cG9zaXQgbGltaXRzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10"
    "b3A6OHB4Ij48bGFiZWw+TWluaW11bSAo4oK5KTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZh"
    "bHVlPSIxMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5NYXhpbXVtICji"
    "grkpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjIwMDAwIj48L2Rpdj48L2Rpdj5g"
    "KX0KICAgJHtHKCJ3cm9uZ0FtdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAi"
    "IHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5Xcm9uZy1hbW91bnQgaGFuZGxp"
    "bmc8c21hbGw+Q3JlZGl0IHdoYXQgd2FzIGFjdHVhbGx5IHBhaWQgYW5kIGZsYWcgaXQ8L3NtYWxs"
    "PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cudmVyaWZ5X29uX3Rh"
    "cCk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9InZlcmlmeV9vbl90YXAiPjwvZGl2PjwvZGl2Pjwv"
    "ZGl2PmApfQogICAke0coInNwbGl0UGF5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0i"
    "c3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPlNwbGl0IHBheW1lbnQ8"
    "c21hbGw+V2FsbGV0IGJhbGFuY2UgcGx1cyBVUEkgaW4gb25lIGNoZWNrb3V0PC9zbWFsbD48L2Rp"
    "dj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC53cm9uZ0FtdD8iIG9uIjoiIn0iIGRhdGEtZmVh"
    "dD0id3JvbmdBbXQiPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImF1dG9SZWZ1bmQiLGA8"
    "ZGl2IGNsYXNzPSJjYXJkIj48aDM+QXV0by1yZWZ1bmQ8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZp"
    "ZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5XYXJyYW50eSB3aW5kb3cgKG1pbnV0"
    "ZXMpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwIj48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJrdiI+PHNwYW4+UmVmdW5kZWQgdG9kYXk8L3NwYW4+PGI+4oK5MTA1LjAwIMK3IDMg"
    "b3JkZXJzPC9iPjwvZGl2PjwvZGl2PmApfQogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+R21haWwg"
    "YWNjb3VudDwvaDM+PHAgY2xhc3M9InN1YiI+VGhlIEdtYWlsIHRoYXQgcmVjZWl2ZXMgeW91ciBG"
    "YW1QYXkgcGF5bWVudCBtYWlscy4gTm90aGluZyBpcyBzdG9yZWQgaW4gY29kZS48L3A+CiAgICAg"
    "PGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5HbWFpbCBJ"
    "RDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHBsYWNlaG9sZGVyPSJ5b3Vyc3RvcmVAZ21haWwu"
    "Y29tIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkdtYWlsIHBhc3N3b3Jk"
    "PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdHlwZT0icGFzc3dvcmQiIHBsYWNlaG9sZGVyPSLi"
    "gKLigKLigKLigKLigKLigKLigKLigKLigKLigKLigKLigKIiPjwvZGl2PgogICAgIDxkaXYgY2xh"
    "c3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBkYXRhLWFkbWluLWFjdD0iZ21haWx0ZXN0"
    "Ij5UZXN0IEdtYWlsPC9idXR0b24+CiAgICAgPC9kaXY+CiAgICAgPHAgY2xhc3M9InN1YiIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTBweCI+VGlwOiBpZiAyLXN0ZXAgdmVyaWZpY2F0aW9uIGlzIG9uLCBw"
    "YXN0ZSBhIEdvb2dsZSBBcHAgUGFzc3dvcmQgaGVyZSBpbnN0ZWFkIG9mIHlvdXIgbm9ybWFsIG9u"
    "ZS48L3A+PC9kaXY+CiAgIDxkaXYgY2xhc3M9InNlYyI+PGgyPlJlY2VudCBhdXRvLXZlcmlmaWNh"
    "dGlvbnM8L2gyPjwvZGl2PgogICAkeyhBRE0uZGVwb3NpdHN8fFtdKS5sZW5ndGg/QURNLmRlcG9z"
    "aXRzLm1hcChkPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDth"
    "bGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtp"
    "Yygid2FsbGV0Iil9PC9kaXY+CiAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAi"
    "PjxiIHN0eWxlPSJmb250LXNpemU6LjgycmVtIj4ke21vbmV5KGQuYW10KX0gwrcgJHtlc2MoZC51"
    "c2VyKX08L2I+PGJyPgogICAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9u"
    "dC1zaXplOi42OXJlbSI+JHtlc2MoZC51dHJ8fCJubyByZWZlcmVuY2UgeWV0Iil9PC9zbWFsbD48"
    "L2Rpdj4KICAgICAgIDxzcGFuIGNsYXNzPSJwaWxsICR7ZC5zdD09PSJDb21wbGV0ZWQiPyJvbiI6"
    "ImhvdCJ9Ij4ke2VzYyhkLnN0KX08L3NwYW4+CiAgICAgICAke2Quc3Q9PT0iQ29tcGxldGVkIj8i"
    "IjpgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBkYXRhLWRlcC1vaz0iJHtkLmlkfSI+QXBwcm92ZTwv"
    "YnV0dG9uPmB9PC9kaXY+YCkuam9pbigiIikKICAgICA6JzxkaXYgY2xhc3M9ImNhcmQgdGlnaHQi"
    "PjxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gZGVwb3NpdHMgeWV0LjwvcD48L2Rp"
    "dj4nfWA7Cn0KCmZ1bmN0aW9uIGFDb250ZW50KCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJk"
    "Ij48aDM+RWRpdCBhcHAgdGV4dDwvaDM+CiAgICAgPHAgY2xhc3M9InN1YiI+RXZlcnkgd29yZCB1"
    "c2VycyBzZWUg4oCUIGNoYW5nZSBpdCBoZXJlIGFuZCB0aGUgc3RvcmUgdXBkYXRlcyBpbnN0YW50"
    "bHkuPC9wPgogICAgIDxkaXYgY2xhc3M9Im1zZWFyY2giIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgi"
    "PjxkaXYgY2xhc3M9ImZpZWxkd3JhcCI+JHtpYygic2VhcmNoIil9PGlucHV0IGlkPSJjcSIgcGxh"
    "Y2Vob2xkZXI9IkZpbmQgdGV4dCB0byBlZGl04oCmIj48L2Rpdj4KICAgICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBzbSBzb2Z0IiBpZD0iY21zcmVzZXQiPlJlc2V0PC9idXR0b24+PC9kaXY+CiAgICAg"
    "PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+VXNlICZsdDticiZndDsgZm9y"
    "IGEgbGluZSBicmVhaywgJmx0O2ImZ3Q7d29yZCZsdDsvYiZndDsgZm9yIGJvbGQuIHtufSA9IGxp"
    "dmUgY291bnRyeSBjb3VudCwge219ID0gcGF5bWVudCBtaW51dGVzLjwvcD48L2Rpdj4KICAgJHtD"
    "TVNNRVRBLm1hcCgoW2dycCxyb3dzXSk9PmA8ZGl2IGNsYXNzPSJjYXJkIGNtc2dycCI+PGgzPiR7"
    "Z3JwfTwvaDM+CiAgICAgJHtyb3dzLm1hcCgoW2ssbGJsXSk9PmA8ZGl2IGNsYXNzPSJmaWVsZCBj"
    "bXNyb3ciIGRhdGEtZj0iJHtlc2MoKGxibCsiICIrQ01TW2tdKyIgIitncnApLnRvTG93ZXJDYXNl"
    "KCkpfSI+CiAgICAgICAgPGxhYmVsPiR7bGJsfTwvbGFiZWw+CiAgICAgICAgPHRleHRhcmVhIGNs"
    "YXNzPSJpbnAiIHJvd3M9IiR7Q01TW2tdLmxlbmd0aD40MD8yOjF9IiBkYXRhLWNtcz0iJHtrfSI+"
    "JHtlc2MoQ01TW2tdKX08L3RleHRhcmVhPjwvZGl2PmApLmpvaW4oIiIpfQogICA8L2Rpdj5gKS5q"
    "b2luKCIiKX0KICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDth"
    "bGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48"
    "aSBjbGFzcz0iZG90Ij48L2k+TGl2ZSBlZGl0PC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6"
    "MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPkNoYW5nZXMg"
    "YXBwbHkgdG8gdGhlIFN0b3JlIHRhYiBzdHJhaWdodCBhd2F5PC9kaXY+CiAgICAgPC9kaXY+YDsK"
    "fQoKZnVuY3Rpb24gYUZlYXR1cmVzKCl7CiAgY29uc3Qgb249T2JqZWN0LnZhbHVlcyhGRUFUKS5m"
    "aWx0ZXIoQm9vbGVhbikubGVuZ3RoLHRvdD1PYmplY3Qua2V5cyhGRUFUKS5sZW5ndGg7CiAgcmV0"
    "dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+RmVhdHVyZSBzd2l0Y2hlczwvaDM+CiAgICAgPHAg"
    "Y2xhc3M9InN1YiI+VHVybiBhbnl0aGluZyBvZmYgYW5kIGl0IGRpc2FwcGVhcnMgZnJvbSB0aGUg"
    "YXBwIGNvbXBsZXRlbHkg4oCUIG5vIGJ1dHRvbnMsIG5vIG1lbnVzLCBubyBsZWZ0b3ZlcnMuPC9w"
    "PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5FbmFibGVkPC9zcGFuPjxiIGlkPSJmY291bnQi"
    "PiR7b259IG9mICR7dG90fTwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJtc2VhcmNoIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMnB4Ij48ZGl2IGNsYXNzPSJmaWVsZHdyYXAiPiR7aWMoInNlYXJjaCIp"
    "fTxpbnB1dCBpZD0iZnEiIHBsYWNlaG9sZGVyPSJTZWFyY2ggZmVhdHVyZXPigKYiPjwvZGl2Pgog"
    "ICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIHNvZnQiIGlkPSJmYWxsIj5BbGwgb248L2J1dHRv"
    "bj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIHNtIGdob3N0IiBpZD0iZm5vbmUiPkFsbCBvZmY8L2J1dHRvbj4K"
    "ICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBpZD0iZnJlc2V0Ij5SZXNldDwvYnV0"
    "dG9uPjwvZGl2PjwvZGl2PgogICAke0ZHUk9VUFMubWFwKChbZ3JwLHJvd3NdKT0+YDxkaXYgY2xh"
    "c3M9ImNhcmQgZmdycCI+PGgzPiR7Z3JwfTwvaDM+CiAgICAgJHtyb3dzLm1hcCgoW2ssbCxkXSk9"
    "PmA8ZGl2IGNsYXNzPSJmY2FyZCBmcm93IiBkYXRhLWY9IiR7ZXNjKChsKyIgIitkKyIgIitncnAp"
    "LnRvTG93ZXJDYXNlKCkpfSI+CiAgICAgICAgPGRpdiBjbGFzcz0idCI+PGI+JHtsfTwvYj48c21h"
    "bGw+JHtkfTwvc21hbGw+PC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0idGcgJHtGRUFUW2tdPyJv"
    "biI6IiJ9IiBkYXRhLWZlYXQ9IiR7a30iPjwvZGl2PjwvZGl2PmApLmpvaW4oIiIpfQogICA8L2Rp"
    "dj5gKS5qb2luKCIiKX0KICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6"
    "ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxs"
    "IG9uIj48aSBjbGFzcz0iZG90Ij48L2k+TGl2ZTwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4"
    "OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj5Td2l0Y2hl"
    "cyBhcHBseSB0byB0aGUgU3RvcmUgdGFiIGluc3RhbnRseTwvZGl2PgogICAgIDwvZGl2PmA7Cn0K"
    "CmZ1bmN0aW9uIGFNYXJrZXQoKXsKICByZXR1cm4gYCR7RygiY291cG9ucyIsYDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxoMz5Db3Vwb25zPC9oMz48cCBjbGFzcz0ic3ViIj5QZXJjZW50YWdlIG9mZiBhdCBj"
    "aGVja291dC48L3A+CiAgICAgJHsoQURNLmNvdXBvbnMmJkFETS5jb3Vwb25zLmxlbmd0aCk/QURN"
    "LmNvdXBvbnMubWFwKGM9PmA8ZGl2IGNsYXNzPSJrdiI+CiAgICAgICA8c3Bhbj48YiBzdHlsZT0i"
    "Y29sb3I6dmFyKC0tdHgpIj4ke2VzYyhjLmNvZGUpfTwvYj4gwrcgJHtjLnBlcmNlbnR9JSDCtyAk"
    "e2MudXNlZH0vJHtjLm1heHx8IuKIniJ9PC9zcGFuPgogICAgICAgPGI+PHNwYW4gY2xhc3M9InBp"
    "bGwgJHtjLmFjdGl2ZT8ib24iOiJvZmYifSI+JHtjLmFjdGl2ZT8iQWN0aXZlIjoiT2ZmIn08L3Nw"
    "YW4+PC9iPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJt"
    "YXJnaW46MCI+Tm8gY291cG9ucyB5ZXQuPC9wPid9CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0"
    "eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5OZXcgY29kZTwvbGFiZWw+CiAgICAgICA8ZGl2"
    "IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjdweCI+PGlucHV0IGNsYXNzPSJpbnAiIGlkPSJjcGNv"
    "ZGUiIHBsYWNlaG9sZGVyPSJORVdZRUFSIj4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0i"
    "Y3BwY3QiIHBsYWNlaG9sZGVyPSIlIiBzdHlsZT0id2lkdGg6NjZweCI+CiAgICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0iY3BhZGQiPkFkZDwvYnV0"
    "dG9uPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImZsYXNoU2FsZSIsYDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxoMz5GbGFzaCBzYWxlIHNjaGVkdWxlcjwvaDM+PHAgY2xhc3M9InN1YiI+U3RhcnRz"
    "LCBhbm5vdW5jZXMgYW5kIGVuZHMgYnkgaXRzZWxmLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPkRpc2NvdW50ICglKTwvbGFiZWw+PGlu"
    "cHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIyMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "PjxsYWJlbD5TdGFydHM8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iVG9kYXkgMTg6"
    "MDAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RW5kczwvbGFiZWw+PGlu"
    "cHV0IGNsYXNzPSJpbnAiIHZhbHVlPSJUb2RheSAyMzowMCI+PC9kaXY+CiAgICAgPGJ1dHRvbiBj"
    "bGFzcz0iYnRuIGJsb2NrIiBpZD0iZmxhc2hnbyI+U3RhcnQgRmxhc2ggU2FsZTwvYnV0dG9uPjwv"
    "ZGl2PmApfQogICAke0coInNjaGVkQnJvYWRjYXN0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlNj"
    "aGVkdWxlZCBicm9hZGNhc3Q8L2gzPjxwIGNsYXNzPSJzdWIiPlF1ZXVlIGl0IGFuZCB0YXJnZXQg"
    "YSBzZWdtZW50LjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTJweCI+PGxhYmVsPlNlZ21lbnQ8L2xhYmVsPgogICAgICAgPHNlbGVjdCBjbGFzcz0iaW5wIj48"
    "b3B0aW9uPkV2ZXJ5b25lPC9vcHRpb24+PG9wdGlvbj5CdXllcnMgb25seTwvb3B0aW9uPjxvcHRp"
    "b24+TmV2ZXIgYm91Z2h0PC9vcHRpb24+PG9wdGlvbj5CaWcgc3BlbmRlcnMgKOKCuTEwMDArKTwv"
    "b3B0aW9uPjxvcHRpb24+SW5hY3RpdmUgMzAgZGF5czwvb3B0aW9uPjwvc2VsZWN0PjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+U2VuZCBhdDwvbGFiZWw+PGlucHV0IGNsYXNz"
    "PSJpbnAiIHZhbHVlPSJUb21vcnJvdyAxMDowMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmll"
    "bGQiPjxsYWJlbD5NZXNzYWdlPC9sYWJlbD48dGV4dGFyZWEgY2xhc3M9ImlucCIgcm93cz0iMiIg"
    "cGxhY2Vob2xkZXI9IldyaXRlIHlvdXIgYnJvYWRjYXN04oCmIj48L3RleHRhcmVhPjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0iYmNnbyI+U2Vu"
    "ZCBCcm9hZGNhc3Q8L2J1dHRvbj4KICAgICAgIDwvZGl2PjwvZGl2PmApfQogICAke0coIndpbmJh"
    "Y2siLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10"
    "b3A6MCI+PGRpdiBjbGFzcz0idCI+V2luLWJhY2sgbWVzc2FnZXM8c21hbGw+QXV0by1ETSB1c2Vy"
    "cyBpbmFjdGl2ZSBmb3IgNyBhbmQgMzAgZGF5czwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFz"
    "cz0idGcke0ZFQVQuc3BsaXRQYXk/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InNwbGl0UGF5Ij48L2Rp"
    "dj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkRpc2NvdW50IG9mZmVyZWQg"
    "KCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwIj48L2Rpdj48L2Rpdj5gKX0K"
    "ICAgJHtHKCJhYmFuZG9uUmVtaW5kIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRp"
    "c3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNz"
    "PSJwaWxsIG9uIj48aSBjbGFzcz0iZG90Ij48L2k+UmVtaW5kZXJzPC9zcGFuPgogICAgIDxkaXYg"
    "c3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4"
    "MikiPjQgdW5maW5pc2hlZCBwYXltZW50cyBudWRnZWQgdG9kYXk8L2Rpdj4KICAgICA8L2Rpdj5g"
    "KX0KICAgJHtHKCJsb3lhbHR5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxveWFsdHkgcG9pbnRz"
    "PC9oMz4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFi"
    "ZWw+UG9pbnRzIHBlciDigrkxMCBzcGVudDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVl"
    "PSIxIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlBvaW50cyBuZWVkZWQg"
    "Zm9yIOKCuTEwPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwMCI+PC9kaXY+CiAg"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9IndpbmJh"
    "Y2tzYXZlIj5TYXZlPC9idXR0b24+PC9kaXY+YCl9CiAgICR7YW55RigicmVmRGFzaCIsInJlZkwy"
    "Iik/YDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5SZWZlcnJhbCBwYXlvdXRzPC9oMz4KICAgICA8ZGl2"
    "IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+TGV2ZWwgMSBjb21t"
    "aXNzaW9uICglKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj4KICAg"
    "ICAke0coInJlZkwyIixgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5MZXZlbCAyIGNvbW1pc3Np"
    "b24gKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEiPjwvZGl2PmApfQogICAg"
    "IDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5QYWlkIG91dCB0aGlzIG1vbnRoPC9zcGFuPjxiPuKCuTMs"
    "MjQwPC9iPjwvZGl2PjwvZGl2PmA6IiJ9CiAgICR7Rygic2FsZXNGZWVkIixgPGRpdiBjbGFzcz0i"
    "Y2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9"
    "InQiPkF1dG8tcG9zdCBzYWxlcyB0byBjaGFubmVsPHNtYWxsPiR7RkVFRC5sZW5ndGg/ZXNjKEZF"
    "RURbMF1bMV0rIiBzb2xkIMK3ICIrRkVFRFswXVszXSk6Im5vIHNhbGVzIHlldCJ9PC9zbWFsbD48"
    "L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC53aW5iYWNrPyIgb24iOiIifSIgZGF0YS1m"
    "ZWF0PSJ3aW5iYWNrIj48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJsZWFkZXJib2FyZCIs"
    "YDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5MZWFkZXJib2FyZCBwcml6ZTwvaDM+CiAgICAgPGRpdiBj"
    "bGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPldlZWtseSBwcml6ZSAo"
    "4oK5KTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1MDAiPjwvZGl2PgogICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJwcml6ZXNhdmUi"
    "PlNhdmU8L2J1dHRvbj48L2Rpdj5gKX1gOwp9CgpmdW5jdGlvbiBhQW5hbHl0aWNzKCl7CiAgY29u"
    "c3Qgd2Vlaz0oQURNLnNlcmllc3x8W10pLnJlZHVjZSgoYSxiKT0+YStiLDApOwogIGNvbnN0IGF2"
    "Zz1BRE0ua3BpLm9yZGVycz9BRE0ua3BpLnJldi9BRE0ua3BpLm9yZGVyczowOwogIHJldHVybiBg"
    "JHtHKCJyZXZDaGFydCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5SZXZlbnVlPC9oMz48cCBjbGFz"
    "cz0ic3ViIj5MYXN0IDcgZGF5cyDCtyAke21vbmV5KHdlZWspfSB0b3RhbDwvcD4KICAgICA8ZGl2"
    "IGNsYXNzPSJiYXJzIj4ke0FETS5iYXJzLm1hcChoPT5gPGkgc3R5bGU9ImhlaWdodDoke2h9JSI+"
    "PC9pPmApLmpvaW4oIiIpfTwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImtwaXMiIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjE0cHgiPiR7W1siVG9kYXkiLG1vbmV5KEFETS5rcGkucmV2KV0sWyJUaGlzIG1vbnRo"
    "Iixtb25leShBRE0ubWFya2V0Lm1vbnRoKV0sWyJBdmcgb3JkZXIiLG1vbmV5KGF2ZyldXQogICAg"
    "ICAgLm1hcCgoW2ssdl0pPT5gPGRpdiBjbGFzcz0ia3BpIj48c21hbGw+JHtrfTwvc21hbGw+PGI+"
    "JHt2fTwvYj48L2Rpdj5gKS5qb2luKCIiKX08L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJmdW5uZWwi"
    "LGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+Q29udmVyc2lvbiBmdW5uZWw8L2gzPjxwIGNsYXNzPSJz"
    "dWIiPldoZXJlIHBlb3BsZSBkcm9wIG9mZjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmdW5uZWwiPiR7"
    "KCgpPT57Y29uc3QgZj1BRE0uYW5hbHl0aWNzLmZ1bm5lbCx0PU1hdGgubWF4KDEsZi51c2Vycyk7"
    "CiAgICAgICByZXR1cm4gW1siU3RhcnRlZCBib3QiLGYudXNlcnMsMTAwXSxbIkRlcG9zaXRlZCIs"
    "Zi5kZXAsTWF0aC5yb3VuZChmLmRlcC90KjEwMCldLFsiQm91Z2h0IixmLmJ1eSxNYXRoLnJvdW5k"
    "KGYuYnV5L3QqMTAwKV1dCiAgICAgICAubWFwKChbayx2LHddKT0+YDxkaXY+PGkgc3R5bGU9Indp"
    "ZHRoOiR7TWF0aC5tYXgoNCx3KX0lIj48L2k+PHNwYW4gc3R5bGU9IndoaXRlLXNwYWNlOm5vd3Jh"
    "cCI+JHtrfSDCtyA8Yj4ke3Z9PC9iPjwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKTt9KSgpfTwvZGl2"
    "PjwvZGl2PmApfQogICAke0coInByb2ZpdFJlcCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5QZXIt"
    "Y291bnRyeSBwcm9maXQ8L2gzPgogICAgICR7QURNLmFuYWx5dGljcy5jb3VudHJ5Lmxlbmd0aD9B"
    "RE0uYW5hbHl0aWNzLmNvdW50cnkubWFwKChbYyxuLHN1bV0pPT5gPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPiR7ZXNjKGMpfSDCtyAke259IHNvbGQ8L3NwYW4+PGIgc3R5bGU9ImNvbG9yOnZhcigtLW9r"
    "KSI+JHttb25leShzdW0pfTwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgOic8cCBjbGFzcz0i"
    "c3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIHNhbGVzIHlldC48L3A+J30KICAgICA8YnV0dG9uIGNs"
    "YXNzPSJidG4gYmxvY2sgc29mdCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1hZG1pbi1h"
    "Y3Q9ImV4cG9ydCI+RXhwb3J0IENTVjwvYnV0dG9uPjwvZGl2PmApfQogICAke0coImNsdiIsYDxk"
    "aXYgY2xhc3M9ImNhcmQiPjxoMz5DdXN0b21lciB2YWx1ZTwvaDM+CiAgICAgJHtBRE0udXNlcnMu"
    "bGVuZ3RoP0FETS51c2Vycy5zbGljZSgwLDUpLm1hcCh1PT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFu"
    "PjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7ZXNjKHUubmFtZSl9PC9iPiDCtyAke3Uub3Jk"
    "ZXJzfSBvcmRlcnM8L3NwYW4+CiAgICAgICAgIDxiPiR7bW9uZXkodS5zcGVudCl9IDxzcGFuIGNs"
    "YXNzPSJwaWxsICR7dS5zcGVudD4wPyJvbiI6ImhvdCJ9Ij4ke3Uuc3BlbnQ+MD8iYWN0aXZlIjoi"
    "bm8gc3BlbmQifTwvc3Bhbj48L2I+PC9kaXY+YCkuam9pbigiIikKICAgICAgIDonPHAgY2xhc3M9"
    "InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBjdXN0b21lcnMgeWV0LjwvcD4nfTwvZGl2PmApfQog"
    "ICAke0coImRpZ2VzdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxl"
    "PSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5EYWlseSBkaWdlc3Q8c21hbGw+RW5kLW9m"
    "LWRheSBzdW1tYXJ5IHRvIHRoZSBhZG1pbiBjaGF0PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJ0ZyR7RkVBVC5zYWxlc0ZlZWQ/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InNhbGVzRmVlZCI+"
    "PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5TZW5kIGF0PC9sYWJl"
    "bD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjIzOjMwIj48L2Rpdj48L2Rpdj5gKX1gOwp9Cgpm"
    "dW5jdGlvbiBhU2VjdXJpdHkoKXsKICByZXR1cm4gYCR7RygicmF0ZUxpbWl0IixgPGRpdiBjbGFz"
    "cz0iY2FyZCI+PGgzPlJhdGUgbGltaXRpbmc8L2gzPjxwIGNsYXNzPSJzdWIiPlN0b3BzIG9uZSB1"
    "c2VyIGhhbW1lcmluZyB0aGUgYm90LjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9"
    "Im1hcmdpbi10b3A6MTBweCI+PGxhYmVsPk1heCBhY3Rpb25zIHBlciBtaW51dGU8L2xhYmVsPjxp"
    "bnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMjAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxk"
    "Ij48bGFiZWw+Q29vbGRvd24gYWZ0ZXIgbGltaXQgKHMpPC9sYWJlbD48aW5wdXQgY2xhc3M9Imlu"
    "cCIgdmFsdWU9IjYwIj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJtdWx0aUFjYyIsYDxkaXYgY2xh"
    "c3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNs"
    "YXNzPSJ0Ij5NdWx0aS1hY2NvdW50IGRldGVjdGlvbjxzbWFsbD5GbGFncyByZWZlcnJhbCBmYXJt"
    "aW5nIHBhdHRlcm5zPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5tdWx0"
    "aUFjYz8iIG9uIjoiIn0iIGRhdGEtZmVhdD0ibXVsdGlBY2MiPjwvZGl2PjwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9Imt2Ij48c3Bhbj5GbGFnZ2VkIHRoaXMgd2Vlazwvc3Bhbj48Yj43IGFjY291bnRz"
    "PC9iPjwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayBzb2Z0IiBzdHlsZT0ibWFy"
    "Z2luLXRvcDoxMHB4IiBkYXRhLWEtZ289Im9yZGVycyI+UmV2aWV3IEZsYWdnZWQ8L2J1dHRvbj48"
    "L2Rpdj5gKX0KICAgJHtHKCJ2ZWxvY2l0eSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9"
    "InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5QdXJjaGFzZSB2ZWxv"
    "Y2l0eSBhbGVydHM8c21hbGw+Q2F0Y2hlcyBzdG9sZW4gdG9wLXVwczwvc21hbGw+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0idGcke0ZFQVQudmVsb2NpdHk/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InZl"
    "bG9jaXR5Ij48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkFsZXJ0"
    "IGFib3ZlIE4gYnV5cyAvIDEwIG1pbjwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1"
    "Ij48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJhZG1pbjJmYSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxo"
    "Mz5BZG1pbiAyRkE8L2gzPjxwIGNsYXNzPSJzdWIiPlJlcXVpcmVkIGJlZm9yZSBiYWxhbmNlIGVk"
    "aXRzLCBicm9hZGNhc3RzIGFuZCB3aXBlcy48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0"
    "eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxsYWJlbD5NZXRob2Q8L2xhYmVsPjxzZWxlY3QgY2xhc3M9"
    "ImlucCI+PG9wdGlvbj5QSU48L29wdGlvbj48b3B0aW9uPlRPVFAgYXBwPC9vcHRpb24+PC9zZWxl"
    "Y3Q+PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBkYXRhLWZlYXQ9ImFkbWluMmZh"
    "Ij5TZXQgVXA8L2J1dHRvbj48L2Rpdj5gKX0KICAgJHtHKCJhdXRvQmFuIixgPGRpdiBjbGFzcz0i"
    "Y2FyZCI+PGgzPkF1dG8tYmFuIHJ1bGVzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2"
    "IGNsYXNzPSJ0Ij5DaGFyZ2ViYWNrPHNtYWxsPkJhbiBpbnN0YW50bHk8L3NtYWxsPjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuYmFuX2NoYXJnZWJhY2spPyIgb24i"
    "OiIifSIgZGF0YS1zZXR0aW5nPSJiYW5fY2hhcmdlYmFjayI+PC9kaXY+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+RmFrZSBVVFIgc3VibWl0dGVkPHNtYWxsPkJh"
    "biBhZnRlciAyIGF0dGVtcHRzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFE"
    "TS5zdyYmQURNLnN3LmJhbl9mYWtlX3V0cik/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImJhbl9m"
    "YWtlX3V0ciI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0i"
    "dCI+UmVwZWF0ZWQgZmFpbGVkIHZlcmlmaWNhdGlvbjxzbWFsbD5CYW4gYWZ0ZXIgNTwvc21hbGw+"
    "PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5iYW5fZmFpbGVkX3Zl"
    "cmlmeSk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImJhbl9mYWlsZWRfdmVyaWZ5Ij48L2Rpdj48"
    "L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJibGFja2xpc3QiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+"
    "QmxhY2tsaXN0PC9oMz48cCBjbGFzcz0ic3ViIj5Vc2VyIElEcywgcGhvbmUgbnVtYmVycyBhbmQg"
    "VVBJIHJlZmVyZW5jZXMuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2lu"
    "LXRvcDoxMHB4Ij48ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjdweCI+CiAgICAgICA8aW5w"
    "dXQgY2xhc3M9ImlucCIgaWQ9ImJsLWluIiBwbGFjZWhvbGRlcj0iSUQsIHBob25lIG9yIFVQSSBy"
    "ZWYiPjxidXR0b24gY2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJibGJs"
    "b2NrIj5CbG9jazwvYnV0dG9uPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bh"
    "bj5CbG9ja2VkIGVudHJpZXM8L3NwYW4+PGI+NDI8L2I+PC9kaXY+PC9kaXY+YCl9CiAgICR7Rygi"
    "ZW5jcnlwdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRk"
    "aW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5FbmNyeXB0IHNlc3Npb25zIGF0IHJlc3Q8c21hbGw+"
    "QSBsZWFrZWQgZGF0YWJhc2UgZmlsZSBzdGF5cyB1c2VsZXNzPC9zbWFsbD48L2Rpdj4KICAgICA8"
    "ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5lbmNyeXB0PyIgb24iOiIifSIgZGF0YS1mZWF0PSJlbmNyeXB0"
    "Ij48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+S2V5IHN0YXR1czwvc3Bh"
    "bj48YiBzdHlsZT0iY29sb3I6dmFyKC0tb2spIj5Mb2FkZWQgZnJvbSBlbnY8L2I+PC9kaXY+PC9k"
    "aXY+YCl9YDsKfQoKZnVuY3Rpb24gYVN0b2NrKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJk"
    "Ij48aDM+VXBsb2FkIHN0b2NrPC9oMz48cCBjbGFzcz0ic3ViIj5QYXN0ZSBzZXNzaW9ucyBvciB1"
    "cGxvYWQgYSBmaWxlIOKAlCBvbmUgcGVyIGxpbmUuPC9wPgo8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlBhc3RlIGFjY291bnRzIOKAlCBvbmUgcGVyIGxp"
    "bmU8L2xhYmVsPgogICAgICA8dGV4dGFyZWEgY2xhc3M9ImlucCIgaWQ9InN0cGFzdGUiIHJvd3M9"
    "IjQiCiAgICAgICAgcGxhY2Vob2xkZXI9IkluZGlhIHwgKzkxIHwgMzUgfCBzZXNzaW9uJiMxMDtL"
    "ZW55YSB8ICsyNTQgfCA3NyB8IHNlc3Npb24iPjwvdGV4dGFyZWE+PC9kaXY+CiAgICA8ZGl2IGNs"
    "YXNzPSJidG5yb3ciPjxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJzdHNhdmUiPkltcG9ydDwv"
    "YnV0dG9uPgogICAgICA8bGFiZWwgY2xhc3M9ImJ0biBzb2Z0IiBzdHlsZT0iZGlzcGxheTpmbGV4"
    "O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2N1cnNvcjpwb2ludGVy"
    "Ij4KICAgICAgICBVcGxvYWQgLnR4dDxpbnB1dCB0eXBlPSJmaWxlIiBpZD0ic3RmaWxlIiBhY2Nl"
    "cHQ9Ii50eHQsLmNzdiIgaGlkZGVuPjwvbGFiZWw+PC9kaXY+CiAgICA8cCBjbGFzcz0ic3ViIiBp"
    "ZD0ic3Rtc2ciIHN0eWxlPSJtYXJnaW46OHB4IDAgMDtmb250LXNpemU6LjcycmVtIj48L3A+CiAg"
    "ICAke0coImJ1bGtVcGxvYWQiLGA8ZGl2IGNsYXNzPSJzdGVwcyIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTJweCI+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAgb24iPjxpPjE8L2k+JHtBRE0uYW5hbHl0aWNz"
    "LnN0b2NrX3RvdGFsfSBhdmFpbGFibGUgbm93PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAg"
    "b24iPjxpPjI8L2k+JHtBRE0uYW5hbHl0aWNzLnNvbGRfdG90YWx9IHNvbGQgc28gZmFyPC9kaXY+"
    "CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAiPjxpPjM8L2k+QWRkIG1vcmUgZnJvbSB0aGUgYm90OiBB"
    "ZG1pbiDihpIgU3RvY2s8L2Rpdj48L2Rpdj5gKX0KICAgICR7RygiZHVwRGV0ZWN0IixgPHAgY2xh"
    "c3M9InN1YiIgc3R5bGU9Im1hcmdpbjoxMHB4IDAgMDtmb250LXNpemU6LjdyZW0iPkR1cGxpY2F0"
    "ZSBwaG9uZXMgYW5kIHNlc3Npb25zIGFyZSByZWplY3RlZCBhdXRvbWF0aWNhbGx5LjwvcD5gKX0K"
    "ICAgICR7RygiaGVhbHRoQ2hlY2siLGA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5W"
    "YWxpZGF0ZSBiZWZvcmUgc2VsbGluZzxzbWFsbD5DaGVjayB0aGUgc2Vzc2lvbiBpcyBhbGl2ZSBh"
    "dCBwdXJjaGFzZSB0aW1lPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5o"
    "ZWFsdGhDaGVjaz8iIG9uIjoiIn0iIGRhdGEtZmVhdD0iaGVhbHRoQ2hlY2siPjwvZGl2PjwvZGl2"
    "PmApfTwvZGl2PgogICAke0coImxvd1N0b2NrIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxvdy1z"
    "dG9jayBhbGVydHM8L2gzPjxwIGNsYXNzPSJzdWIiPlBlci1jb3VudHJ5IHRocmVzaG9sZCDigJQg"
    "cGluZyB3aGVuIGl0IGRyb3BzIGJlbG93LjwvcD4KICAgICAkeyhBRE0uc3RvY2subGVuZ3RoP0FE"
    "TS5zdG9jazpTVE9DSykuc2xpY2UoMCw2KS5tYXAocz0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj4k"
    "e3MuZn0gJHtlc2Mocy5jKX0gwrcgJHtzLnF0eXx8MH0gbGVmdDwvc3Bhbj4KICAgICAgIDxiPjxp"
    "bnB1dCBjbGFzcz0iaW5wIiBzdHlsZT0iaGVpZ2h0OjMwcHg7d2lkdGg6NjRweDt0ZXh0LWFsaWdu"
    "OmNlbnRlcjtmb250LXNpemU6Ljc0cmVtIiB2YWx1ZT0iJHtzLmxvd3x8MTB9Ij48L2I+PC9kaXY+"
    "YCkuam9pbigiIikKICAgICAgIHx8JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8g"
    "c3RvY2sgeWV0LjwvcD4nfTwvZGl2PmApfQogICAke0coInNlbGxIb3VycyIsYDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxoMz5TZWxsaW5nIGhvdXJzPC9oMz48cCBjbGFzcz0ic3ViIj5Db3VudHJpZXMgaGlk"
    "ZSB0aGVtc2VsdmVzIG91dHNpZGUgdGhlIHdpbmRvdy48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmll"
    "bGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPk9wZW48L2xhYmVsPjxpbnB1dCBjbGFz"
    "cz0iaW5wIiB2YWx1ZT0iMDk6MDAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFi"
    "ZWw+Q2xvc2U8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMDI6MDAiPjwvZGl2Pjwv"
    "ZGl2PmApfQogICAke0coImR5blByaWNlIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkR5bmFtaWMg"
    "cHJpY2luZzwvaDM+CiAgICAgPGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAi"
    "PjxkaXYgY2xhc3M9InQiPlJhaXNlIHByaWNlIHdoZW4gc3RvY2sgaXMgbG93PHNtYWxsPisxMCUg"
    "dW5kZXIgMjAgdW5pdHM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULmR5"
    "blByaWNlPyIgb24iOiIifSIgZGF0YS1mZWF0PSJkeW5QcmljZSI+PC9kaXY+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+TmlnaHQgZGlzY291bnQ8c21hbGw+4oiS"
    "NSUgYmV0d2VlbiAwMDowMCBhbmQgMDY6MDA8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "InRnJHsoQURNLnN3JiZBRE0uc3cubmlnaHRfZGlzY291bnQpPyIgb24iOiIifSIgZGF0YS1zZXR0"
    "aW5nPSJuaWdodF9kaXNjb3VudCI+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiYnVsa0Rp"
    "c2MiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QnVsayBkaXNjb3VudHM8L2gzPgogICAgIDxwIGNs"
    "YXNzPSJzdWIiPlBlcmNlbnQgb2ZmIHdoZW4gdGhlIGJ1eWVyIHRha2VzIHNldmVyYWwgYXQgb25j"
    "ZS48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxs"
    "YWJlbD41IG9yIG1vcmUgKCUpPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRh"
    "LXByaWNlPSJidWxrXzUiIHZhbHVlPSIke0FETS5wcmljaW5nP0FETS5wcmljaW5nLmJ1bGtfNTo1"
    "fSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD4xMCBvciBtb3JlICglKTwv"
    "bGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1wcmljZT0iYnVsa18xMCIgdmFs"
    "dWU9IiR7QURNLnByaWNpbmc/QURNLnByaWNpbmcuYnVsa18xMDoxMH0iPjwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+MjUgb3IgbW9yZSAoJSk8L2xhYmVsPgogICAgICAgPGlu"
    "cHV0IGNsYXNzPSJpbnAiIGRhdGEtcHJpY2U9ImJ1bGtfMjUiIHZhbHVlPSIke0FETS5wcmljaW5n"
    "P0FETS5wcmljaW5nLmJ1bGtfMjU6MTV9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+"
    "PGxhYmVsPkRlZmF1bHQgcHJpY2UgZm9yIG5ldyBzdG9jazwvbGFiZWw+CiAgICAgICA8aW5wdXQg"
    "Y2xhc3M9ImlucCIgZGF0YS1wcmljZT0iZGVmYXVsdF9wcmljZSIgdmFsdWU9IiR7QURNLnByaWNp"
    "bmc/QURNLnByaWNpbmcuZGVmYXVsdF9wcmljZTozNX0iPjwvZGl2PgogICAgIDwvZGl2PmApfQog"
    "ICAke0coInZhcmlhbnRzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlZhcmlhbnRzPC9oMz48cCBj"
    "bGFzcz0ic3ViIj5QcmljZWQgc2VwYXJhdGVseSBvbiBldmVyeSBjb3VudHJ5LjwvcD4KICAgICA8"
    "ZGl2IGNsYXNzPSJrdiI+PHNwYW4+MkZBIGVuYWJsZWQ8L3NwYW4+PGI+K+KCuTU8L2I+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPldpdGggbWFpbDwvc3Bhbj48Yj4r4oK5ODwvYj48"
    "L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+U3BhbS1mcmVlIGd1YXJhbnRlZTwvc3Bh"
    "bj48Yj4r4oK5NDwvYj48L2Rpdj48L2Rpdj5gKX0KICAgPGRpdiBjbGFzcz0ic2VjIj48aDI+SW52"
    "ZW50b3J5PC9oMj48YSBjbGFzcz0ic3ViIj50YXAgYSBwcmljZSB0byBjaGFuZ2UgaXQ8L2E+PC9k"
    "aXY+CiAgICR7U1RPQ0subWFwKHM9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlz"
    "cGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPGRpdiBjbGFzcz0i"
    "ZmxhZyI+JHtzLmZ9PC9kaXY+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48"
    "YiBzdHlsZT0iZm9udC1zaXplOi44M3JlbSI+JHtzLmN9PC9iPjxicj4KICAgICAgIDxzbWFsbCBz"
    "dHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4ke3MucXR5fSBhdmFpbGFi"
    "bGU8L3NtYWxsPjwvZGl2PgogICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLWNwcmljZT0iJHtl"
    "c2Mocy5jKX0iIHZhbHVlPSIke3MucHJpY2V9IiBzdHlsZT0iaGVpZ2h0OjMycHg7d2lkdGg6Nzhw"
    "eDt0ZXh0LWFsaWduOmNlbnRlcjtmb250LXNpemU6Ljc2cmVtIj4KICAgICAKICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4geHMgYmFkIiBkYXRhLW1pc2M9ImNsZWFyc3RvY2siIGRhdGEtbWlzYy12YWx1"
    "ZT0iJHtlc2Mocy5jKX0iPkNsZWFyPC9idXR0b24+PC9kaXY+YCkuam9pbigiIil9YDsKfQpmdW5j"
    "dGlvbiBhT3JkZXJzKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5SZWNlbnQgT3Jk"
    "ZXJzPC9oMj48YSBkYXRhLWFkbWluLWFjdD0iZXhwb3J0Ij5FeHBvcnQ8L2E+PC9kaXY+CiAgICR7"
    "QURNLm9yZGVycy5sZW5ndGg/QURNLm9yZGVycy5tYXAobz0+YDxkaXYgY2xhc3M9ImNhcmQgdGln"
    "aHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAg"
    "ICA8ZGl2IGNsYXNzPSJmbGFnIj4ke28uZn08L2Rpdj48ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdp"
    "ZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjgycmVtIj4ke2VzYyhvLmlkKX08L2I+PGJyPgog"
    "ICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4ke2Vz"
    "YyhvLnVzZXIpfSDCtyAke21vbmV5KG8ucHJpY2UpfSDCtyAke2VzYyhvLndoZW4pfTwvc21hbGw+"
    "PC9kaXY+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwgJHtvLnN0PT09IkRlbGl2ZXJlZCI/Im9uIjoi"
    "b2ZmIn0iPiR7ZXNjKG8uc3QpfTwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKQogICAgIDonPGRpdiBj"
    "bGFzcz0iY2FyZCB0aWdodCI+PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBvcmRl"
    "cnMgeWV0LjwvcD48L2Rpdj4nfQogICAke0coImRpc3B1dGUiLGA8ZGl2IGNsYXNzPSJzZWMiPjxo"
    "Mj5EaXNwdXRlczwvaDI+PC9kaXY+CiAgICAgJHtBRE0uZGlzcHV0ZXMubGVuZ3RoP0FETS5kaXNw"
    "dXRlcy5tYXAoZD0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7"
    "YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+"
    "JHtpYygic2hpZWxkIil9PC9kaXY+CiAgICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lk"
    "dGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+JHtlc2MoZC5pZCl9IMK3ICR7ZXNjKGQu"
    "dXNlcil9PC9iPjxicj48c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42"
    "OHJlbSI+JHtlc2MoZC5yZWFzb24pfTwvc21hbGw+PC9kaXY+CiAgICAgICAgIDxzcGFuIGNsYXNz"
    "PSJwaWxsICR7ZC5zdGF0dXM9PT0iT3BlbiI/ImhvdCI6Im9uIn0iPiR7ZXNjKGQuc3RhdHVzKX08"
    "L3NwYW4+PC9kaXY+YCkuam9pbigiIikKICAgICAgIDonPGRpdiBjbGFzcz0iY2FyZCB0aWdodCI+"
    "PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBkaXNwdXRlcy48L3A+PC9kaXY+J31g"
    "KX0KICAgJHtHKCJ3YXJyYW50eSIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPlJlcGxhY2VtZW50IGNs"
    "YWltczwvaDI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3Bs"
    "YXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgPGRpdiBjbGFzcz0i"
    "ZmxhZyI+JHtpYygic2hpZWxkIil9PC9kaXY+CiAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWlu"
    "LXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjhyZW0iPjIgY2xhaW1zIGluc2lkZSB3YXJy"
    "YW50eTwvYj48YnI+CiAgICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250"
    "LXNpemU6LjY4cmVtIj5BdXRvLXJlcGxhY2VkIGZyb20gc3RvY2s8L3NtYWxsPjwvZGl2PgogICAg"
    "ICAgPC9kaXY+YCl9CiAgIAogICAke0coInJldmlld3MiLGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5S"
    "ZXZpZXdzPC9oMj48L2Rpdj4KICAgICAkeyhBRE0ucmV2aWV3cy5sZW5ndGg/QURNLnJldmlld3Mu"
    "bWFwKHI9PltyLnVzZXIsci5jLHIuc3RhcnMsci50ZXh0XSk6W10pCiAgICAgICAubWFwKChbdSxj"
    "LHIsdF0pPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGln"
    "bi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2lj"
    "KCJzdGFyIil9PC9kaXY+CiAgICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+"
    "PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+JHt1fSDCtyAke2N9IMK3ICR7cn08L2I+PGJyPgog"
    "ICAgICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY4cmVt"
    "Ij4ke3R9PC9zbWFsbD48L2Rpdj4KICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQi"
    "IGRhdGEtcmV2PSIke3IuaWR9Ij5IaWRlPC9idXR0b24+PC9kaXY+YCkuam9pbigiIil9YCl9YDsK"
    "fQpmdW5jdGlvbiBhVXNlcnMoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImZpZWxkIj48aW5wdXQg"
    "Y2xhc3M9ImlucCIgcGxhY2Vob2xkZXI9IlNlYXJjaCBieSBJRCBvciB1c2VybmFtZSI+PC9kaXY+"
    "CiAgICR7QURNLnVzZXJzLmxlbmd0aD9BRE0udXNlcnMubWFwKHU9PmA8ZGl2IGNsYXNzPSJjYXJk"
    "IHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+"
    "CiAgICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoInVzZXIiKX08L2Rpdj4KICAgICAgPGRpdiBz"
    "dHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44MnJlbSI+JHtl"
    "c2ModS5uYW1lKX08L2I+PGJyPgogICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgy"
    "KTtmb250LXNpemU6LjY5cmVtIj4ke21vbmV5KHUuc3BlbnQpfSBzcGVudCDCtyAke3Uub3JkZXJz"
    "fSBvcmRlcnMgwrcgJHttb25leSh1LmJhbGFuY2UpfSBiYWxhbmNlJHt1LmJhbm5lZD8iIMK3IGJh"
    "bm5lZCI6IiJ9PC9zbWFsbD48L2Rpdj4KICAgICAgPHNwYW4gY2xhc3M9InBpbGwgJHt1LmJhbm5l"
    "ZD8ib2ZmIjoib24ifSI+JHtlc2ModS50aWVyKX08L3NwYW4+PC9kaXY+YCkuam9pbigiIikKICAg"
    "ICA6JzxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiPjxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46"
    "MCI+Tm8gdXNlcnMgeWV0LjwvcD48L2Rpdj4nfQogICAke0coImFkbWluUm9sZXMiLGA8ZGl2IGNs"
    "YXNzPSJzZWMiPjxoMj5BZG1pbiByb2xlczwvaDI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iY2Fy"
    "ZCI+CiAgICAgICAke0FETS5hZG1pbnMubGVuZ3RoP0FETS5hZG1pbnMubWFwKGE9PmA8ZGl2IGNs"
    "YXNzPSJrdiI+PHNwYW4+PGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHtlc2MoYS5sYWJlbCl9"
    "PC9iPiDCtyAke2VzYyhhLnBlcm1zKX08L3NwYW4+PGI+PHNwYW4gY2xhc3M9InBpbGwgb24iPiR7"
    "ZXNjKGEucm9sZSl9PC9zcGFuPjwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgICA6JzxwIGNs"
    "YXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+T25seSB5b3Ugc28gZmFyLjwvcD4nfQogICAgICAg"
    "PGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxkaXYgc3R5bGU9ImRp"
    "c3BsYXk6ZmxleDtnYXA6N3B4Ij4KICAgICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIHBsYWNlaG9s"
    "ZGVyPSJAdXNlcm5hbWUiPjxzZWxlY3QgY2xhc3M9ImlucCIgc3R5bGU9IndpZHRoOmF1dG8iPjxv"
    "cHRpb24+TWFuYWdlcjwvb3B0aW9uPjxvcHRpb24+U3VwcG9ydDwvb3B0aW9uPjwvc2VsZWN0Pgog"
    "ICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0i"
    "YWRtaW5hZGQiPkFkZDwvYnV0dG9uPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImJ1eWVy"
    "QmFkZ2UiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+VmVyaWZpZWQgYnV5ZXIgYmFkZ2U8L2gzPgog"
    "ICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5PcmRl"
    "cnMgcmVxdWlyZWQ8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iNSI+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlZlcmlmaWVkIHVzZXJzPC9zcGFuPjxiPjg0PC9iPjwv"
    "ZGl2PjwvZGl2PmApfQogICAke0coIm11bHRpQWNjIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIg"
    "c3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxz"
    "cGFuIGNsYXNzPSJwaWxsIGhvdCI+NyBmbGFnZ2VkPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZs"
    "ZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlBvc3Np"
    "YmxlIG11bHRpLWFjY291bnRzIHRoaXMgd2VlazwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biB4cyBzb2Z0IiBkYXRhLWEtZ289InNlY3VyaXR5Ij5SZXZpZXc8L2J1dHRvbj48L2Rpdj5gKX1g"
    "Owp9CmZ1bmN0aW9uIGFTZXR0aW5ncygpewogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCI+PGgz"
    "PlN0b3JlPC9oMz48cCBjbGFzcz0ic3ViIj5FdmVyeXRoaW5nIGxpdmVzIGluIHRoZSBkYXRhYmFz"
    "ZSDigJQgbm8gY29kZSBlZGl0cywgbm8gcmVzdGFydC48L3A+CiAgICA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlN0b3JlIG5hbWU8L2xhYmVsPgogICAg"
    "ICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9InN0b3JlX25hbWUiIHZhbHVlPSIke2VzYyhD"
    "TVMuc3RvcmVOYW1lfHxDRkcubmFtZSl9Ij48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48"
    "bGFiZWw+VGFnbGluZTwvbGFiZWw+CiAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0i"
    "c3RvcmVfdGFnbGluZSIgdmFsdWU9IiR7ZXNjKENNUy5zdG9yZVRhZ3x8Q0ZHLnRhZyl9Ij48L2Rp"
    "dj4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+VGFnbGluZTwvbGFiZWw+PGlucHV0IGNs"
    "YXNzPSJpbnAiIHZhbHVlPSIke0NGRy50YWd9Ij48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxk"
    "Ij48bGFiZWw+U3VwcG9ydCBjb250YWN0PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgcGxhY2Vo"
    "b2xkZXI9IkB5b3VyX3N1cHBvcnQiPjwvZGl2PgogICAgPC9kaXY+CiAgIDxkaXYgY2xhc3M9ImNh"
    "cmQiPjxoMz5Ub2dnbGVzPC9oMz4KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQi"
    "PkZvcmNlIGpvaW48c21hbGw+UmVxdWlyZSBjaGFubmVsIG1lbWJlcnNoaXA8L3NtYWxsPjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuZm9yY2Vfc3ViKT8iIG9uIjoi"
    "In0iIGRhdGEtc2V0dGluZz0iZm9yY2Vfc3ViIj48L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9"
    "InN3YXAiPjxkaXYgY2xhc3M9InQiPk1haW50ZW5hbmNlIG1vZGU8c21hbGw+RnJlZXplIHRoZSBz"
    "dG9yZTwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5t"
    "YWludGVuYW5jZSk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9Im1haW50ZW5hbmNlIj48L2Rpdj48"
    "L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPkF1dG8gcmVmdW5kczxz"
    "bWFsbD5SZWZ1bmQgZmFpbGVkIGxvZ2luczwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "dGcke0ZFQVQuYXV0b1JlZnVuZD8iIG9uIjoiIn0iIGRhdGEtZmVhdD0iYXV0b1JlZnVuZCI+PC9k"
    "aXY+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5SZWZlcnJhbCBw"
    "cm9ncmFtPHNtYWxsPjElIG9mIGludml0ZWQgZGVwb3NpdHM8L3NtYWxsPjwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9InRnJHtGRUFULnJlZkRhc2g/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InJlZkRhc2gi"
    "PjwvZGl2PjwvZGl2PjwvZGl2PgogICAke0coImkxOG4iLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+"
    "TGFuZ3VhZ2VzPC9oMz48cCBjbGFzcz0ic3ViIj5Vc2VycyBwaWNrIHRoZWlyIG93bjsgZXZlcnkg"
    "c3RyaW5nIGlzIHRyYW5zbGF0ZWQuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+"
    "RGVmYXVsdCBsYW5ndWFnZTwvbGFiZWw+CiAgICAgIDxzZWxlY3QgY2xhc3M9ImlucCIgZGF0YS1z"
    "ZXRzZWw9ImxhbmdfZGVmYXVsdCI+CiAgICAgICAgPG9wdGlvbiB2YWx1ZT0iZW4iICR7KEFETS5z"
    "ZXR0aW5ncyYmQURNLnNldHRpbmdzLmxhbmc9PT0iZW4iKT8ic2VsZWN0ZWQiOiIifT5FbmdsaXNo"
    "PC9vcHRpb24+CiAgICAgICAgPG9wdGlvbiB2YWx1ZT0iaGkiICR7KEFETS5zZXR0aW5ncyYmQURN"
    "LnNldHRpbmdzLmxhbmc9PT0iaGkiKT8ic2VsZWN0ZWQiOiIifT7gpLngpL/gpKjgpY3gpKbgpYA8"
    "L29wdGlvbj4KICAgICAgPC9zZWxlY3Q+PC9kaXY+PC9kaXY+YCl9CiAgICR7Rygic2xhc2hDbWRz"
    "IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlNsYXNoIGNvbW1hbmRzPC9oMz48cCBjbGFzcz0ic3Vi"
    "Ij5SZWdpc3RlcmVkIGluIFRlbGVncmFtJ3MgY29tbWFuZCBtZW51LjwvcD4KICAgICAke1tbIi9z"
    "dGFydCIsIk9wZW4gdGhlIHN0b3JlIl0sWyIvYnV5IiwiSnVtcCB0byBzdG9jayJdLFsiL2JhbGFu"
    "Y2UiLCJTaG93IHdhbGxldCJdLFsiL29yZGVycyIsIk15IG9yZGVycyJdLFsiL3N1cHBvcnQiLCJD"
    "b250YWN0IHN1cHBvcnQiXV0KICAgICAgIC5tYXAoKFtjLGRdKT0+YDxkaXYgY2xhc3M9Imt2Ij48"
    "c3Bhbj48YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4ke2N9PC9iPjwvc3Bhbj48YiBzdHlsZT0i"
    "Zm9udC13ZWlnaHQ6NTAwO2ZvbnQtc2l6ZTouNzRyZW0iPiR7ZH08L2I+PC9kaXY+YCkuam9pbigi"
    "Iil9CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIHNvZnQiIHN0eWxlPSJtYXJnaW4tdG9w"
    "OjEwcHgiIGRhdGEtYWRtaW4tYWN0PSJzeW5jIj5TeW5jIENvbW1hbmRzPC9idXR0b24+PC9kaXY+"
    "YCl9CiAgICR7RygiYXV0b0JhY2t1cCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5BdXRvLWJhY2t1"
    "cDwvaDM+PHAgY2xhc3M9InN1YiI+RGF0YWJhc2UgdG8gYSBwcml2YXRlIGNoYW5uZWwgb24gYSBz"
    "Y2hlZHVsZS48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhw"
    "eCI+PGxhYmVsPkV2ZXJ5IChob3Vycyk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0i"
    "NiI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxhc3QgYmFja3VwPC9zcGFuPjxi"
    "PjQyIG1pbiBhZ288L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFy"
    "Z2luLXRvcDoxMHB4Ij48YnV0dG9uIGNsYXNzPSJidG4iIGRhdGEtYWRtaW4tYWN0PSJiYWNrdXAi"
    "PkJhY2sgVXAgTm93PC9idXR0b24+CiAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRh"
    "dGEtYWRtaW4tYWN0PSJyZXN0b3JlIj5SZXN0b3JlPC9idXR0b24+PC9kaXY+PC9kaXY+YCl9CiAg"
    "ICR7RygiYXVkaXRMb2ciLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QXVkaXQgbG9nPC9oMz48cCBj"
    "bGFzcz0ic3ViIj5FdmVyeSBhZG1pbiBhY3Rpb24sIHdpdGggdW5kby48L3A+CiAgICAgJHsoKEFE"
    "TS5hdWRpdCYmQURNLmF1ZGl0Lmxlbmd0aCk/QURNLmF1ZGl0Lm1hcChhPT5bYS53aG8sKGEud2hh"
    "dCsiICIrYS5kZXRhaWwpLnRyaW0oKSxhLndoZW5dKTpbXSkKICAgICAgIC5tYXAoKFt1LGEsdF0p"
    "PT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7dX08"
    "L2I+ICR7YX08L3NwYW4+CiAgICAgICAgIDxiPiR7dH0gPC9iPjwvZGl2PmApLmpvaW4oIiIpfTwv"
    "ZGl2PmApfQogICAke0coInNhbmRib3giLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJz"
    "d2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+U2FuZGJveCAvIHRlc3Qg"
    "bW9kZTxzbWFsbD5GYWtlIG1vbmV5LCBubyByZWFsIGRlbGl2ZXJ5PC9zbWFsbD48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LnNhbmRib3gpPyIgb24iOiIifSIgZGF0"
    "YS1zZXR0aW5nPSJzYW5kYm94Ij48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJlbmNyeXB0"
    "IixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFzcz0i"
    "ZG90Ij48L2k+RW5jcnlwdGVkPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lk"
    "dGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlNlc3Npb25zIGFyZSBlbmNy"
    "eXB0ZWQgYXQgcmVzdDwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRh"
    "LWEtZ289InNlY3VyaXR5Ij5LZXlzPC9idXR0b24+PC9kaXY+YCl9CiAgIDxkaXYgY2xhc3M9ImNh"
    "cmQiPjxoMz5EYW5nZXIgem9uZTwvaDM+PHAgY2xhc3M9InN1YiI+SXJyZXZlcnNpYmxlLiBBc2sg"
    "dHdpY2UuPC9wPgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4"
    "Ij48YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1hZG1pbi1hY3Q9ImJhY2t1cCI+QmFj"
    "a3VwPC9idXR0b24+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmFkIGhvbGQiIGRhdGEtZG9uZT0i"
    "RGF0YWJhc2UgcmVzZXQgKGRlbW8pIj48c3BhbiBjbGFzcz0iZmlsbCI+PC9zcGFuPkhvbGQgdG8g"
    "UmVzZXQgREI8L2J1dHRvbj48L2Rpdj48L2Rpdj5gOwp9CgpmdW5jdGlvbiBiaW5kQWRtaW4oKXsK"
    "ICBiaW5kQ29tbW9uKCk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZmVhdF0i"
    "KS5mb3JFYWNoKHQ9PnQub25jbGljaz0oKT0+ewogICAgY29uc3Qgaz10LmRhdGFzZXQuZmVhdDtG"
    "RUFUW2tdPSFGRUFUW2tdO3QuY2xhc3NMaXN0LnRvZ2dsZSgib24iLEZFQVRba10pOwogICAgcHVz"
    "aCgiL2FwaS9hZG1pbi9mZWF0dXJlIix7a2V5Omssb246RkVBVFtrXX0pOwogICAgY29uc3QgYz1k"
    "b2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZmNvdW50Iik7CiAgICBpZihjKWMudGV4dENvbnRlbnQ9"
    "T2JqZWN0LnZhbHVlcyhGRUFUKS5maWx0ZXIoQm9vbGVhbikubGVuZ3RoKyIgb2YgIitPYmplY3Qu"
    "a2V5cyhGRUFUKS5sZW5ndGg7CiAgICBhcHBseUZlYXR1cmVzKCk7CiAgICBjb25zdCBjYXJkPXQu"
    "Y2xvc2VzdCgiLmZjYXJkIiksIG5hbWVFbD1jYXJkP2NhcmQucXVlcnlTZWxlY3RvcigiYiIpOm51"
    "bGw7CiAgICBjb25zdCBsYWJlbD1uYW1lRWw/bmFtZUVsLnRleHRDb250ZW50OmsucmVwbGFjZSgv"
    "KFtBLVpdKS9nLCIgJDEiKS50b0xvd2VyQ2FzZSgpOwogICAgdG9hc3QobGFiZWwrKEZFQVRba10/"
    "IiBlbmFibGVkIjoiIGhpZGRlbiIpKTt9KTsKICBjb25zdCBmcT1kb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgiZnEiKTsKICBpZihmcSlmcS5vbmlucHV0PSgpPT57Y29uc3QgcT1mcS52YWx1ZS50cmlt"
    "KCkudG9Mb3dlckNhc2UoKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5mcm93Iiku"
    "Zm9yRWFjaChyPT5yLmNsYXNzTGlzdC50b2dnbGUoImhpZGUiLCEhcSYmIXIuZGF0YXNldC5mLmlu"
    "Y2x1ZGVzKHEpKSk7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIuZmdycCIpLmZvckVh"
    "Y2goZz0+Zy5jbGFzc0xpc3QudG9nZ2xlKCJoaWRlIiwhZy5xdWVyeVNlbGVjdG9yKCIuZnJvdzpu"
    "b3QoLmhpZGUpIikpKTt9OwogIGNvbnN0IHNldEFsbD12PT57T2JqZWN0LmtleXMoRkVBVCkuZm9y"
    "RWFjaChrPT5GRUFUW2tdPXYpO2FwcGx5RmVhdHVyZXMoKTtyZW5kZXJBZG1pbigpO3RvYXN0KHY/"
    "IkFsbCBmZWF0dXJlcyBlbmFibGVkIjoiQWxsIGZlYXR1cmVzIGhpZGRlbiIpO307CiAgY29uc3Qg"
    "ZmE9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImZhbGwiKSxmbj1kb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgiZm5vbmUiKSxmcj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZnJlc2V0Iik7CiAgaWYo"
    "ZmEpZmEub25jbGljaz0oKT0+c2V0QWxsKHRydWUpOwogIGlmKGZuKWZuLm9uY2xpY2s9KCk9PnNl"
    "dEFsbChmYWxzZSk7CiAgaWYoZnIpZnIub25jbGljaz0oKT0+e09iamVjdC5hc3NpZ24oRkVBVCxG"
    "RUFUMCk7YXBwbHlGZWF0dXJlcygpO3JlbmRlckFkbWluKCk7dG9hc3QoIkZlYXR1cmVzIHJlc2V0"
    "Iik7fTsKICBjb25zdCBzdG1zZz1tPT57Y29uc3QgZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgi"
    "c3Rtc2ciKTtpZihlKWUudGV4dENvbnRlbnQ9bTt9OwogIGFzeW5jIGZ1bmN0aW9uIGltcG9ydFN0"
    "b2NrKHRleHQpewogICAgaWYoIXRleHQudHJpbSgpKXt0b2FzdCgiUGFzdGUgc29tZSBsaW5lcyBm"
    "aXJzdCIpO3JldHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVy"
    "bjt9CiAgICBzdG1zZygiSW1wb3J0aW5n4oCmIik7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgi"
    "L2FwaS9hZG1pbi9zdG9jayIse3RleHQ6dGV4dH0pOwogICAgaWYoIXIpe3N0bXNnKCIiKTtyZXR1"
    "cm47fQogICAgY29uc3Qgbm90ZT1yLmFkZGVkKyIgaW1wb3J0ZWQiKyhyLnNraXBwZWQmJnIuc2tp"
    "cHBlZC5sZW5ndGg/KCIgwrcgIityLnNraXBwZWQubGVuZ3RoKyIgc2tpcHBlZCIpOiIiKTsKICAg"
    "IHRvYXN0KHIuYWRkZWQrIiBhY2NvdW50cyBhZGRlZCIpOwogICAgY29uc3QgYm94PWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJzdHBhc3RlIik7aWYoYm94KWJveC52YWx1ZT0iIjsKICAgIGF3YWl0"
    "IHJlZnJlc2goKTsKICAgIHN0bXNnKG5vdGUpOwogIH0KICBjb25zdCBzdHNhdmU9ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoInN0c2F2ZSIpOwogIGlmKHN0c2F2ZSlzdHNhdmUub25jbGljaz0oKT0+"
    "aW1wb3J0U3RvY2soKGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdHBhc3RlIil8fHt9KS52YWx1"
    "ZXx8IiIpOwogIGNvbnN0IHN0ZmlsZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RmaWxlIik7"
    "CiAgaWYoc3RmaWxlKXN0ZmlsZS5vbmNoYW5nZT0oKT0+ewogICAgY29uc3QgZj1zdGZpbGUuZmls"
    "ZXMmJnN0ZmlsZS5maWxlc1swXTtpZighZilyZXR1cm47CiAgICBjb25zdCByZD1uZXcgRmlsZVJl"
    "YWRlcigpOwogICAgcmQub25sb2FkPSgpPT57Y29uc3QgYm94PWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJzdHBhc3RlIik7CiAgICAgIGlmKGJveClib3gudmFsdWU9cmQucmVzdWx0O3N0bXNnKGYu"
    "bmFtZSsiIGxvYWRlZCDigJQgcHJlc3MgSW1wb3J0Iik7fTsKICAgIHJkLnJlYWRBc1RleHQoZik7"
    "CiAgfTsKICBjb25zdCBibD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYmxibG9jayIpOwogIGlm"
    "KGJsKWJsLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgaT1kb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgiYmwtaW4iKTsKICAgIGNvbnN0IHY9aT8oaS52YWx1ZXx8IiIpLnRyaW0oKToiIjsKICAg"
    "IGlmKCF2KXt0b2FzdCgiVHlwZSBhbiBpZCwgcGhvbmUgb3IgVVBJIHJlZmVyZW5jZSIpO2lmKGkp"
    "aS5mb2N1cygpO3JldHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3Jl"
    "dHVybjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9ibGFja2xpc3QiLHth"
    "Y3Rpb246ImFkZCIsdmFsdWU6dn0pOwogICAgaWYoIXIpcmV0dXJuOwogICAgaS52YWx1ZT0iIjt0"
    "b2FzdCgiQmxvY2tlZCAiK3YpO2F3YWl0IHJlZnJlc2goKTsKICB9OwogIGRvY3VtZW50LnF1ZXJ5"
    "U2VsZWN0b3JBbGwoIltkYXRhLWJsLWRlbF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygp"
    "PT57CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9ibGFja2xpc3QiLHthY3Rp"
    "b246ImRlbCIsaWQ6K2IuZGF0YXNldC5ibERlbH0pOwogICAgaWYocil7dG9hc3QoIlVuYmxvY2tl"
    "ZCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgi"
    "W2RhdGEtbWlzY10iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBpZighTElW"
    "RSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBjb25zdCB3aGF0PWIuZGF0YXNl"
    "dC5taXNjLCB2YWw9Yi5kYXRhc2V0Lm1pc2NWYWx1ZXx8IiI7CiAgICBjb25zdCBsYmw9Yi50ZXh0"
    "Q29udGVudDtiLmRpc2FibGVkPXRydWU7Yi50ZXh0Q29udGVudD0i4oCmIjsKICAgIGNvbnN0IHI9"
    "YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL21pc2MiLHt3aGF0OndoYXQsdmFsdWU6dmFsfSk7CiAg"
    "ICBiLmRpc2FibGVkPWZhbHNlO2IudGV4dENvbnRlbnQ9bGJsOwogICAgaWYoIXIpcmV0dXJuOwog"
    "ICAgaWYoci5saW5lcylzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6"
    "MXJlbSI+Q2hhbm5lbCBjaGVjazwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJ3aGl0"
    "ZS1zcGFjZTpwcmUtbGluZSI+JHtlc2Moci5saW5lcy5qb2luKCJcbiIpKX08L3A+CiAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9z"
    "ZT5DbG9zZTwvYnV0dG9uPmApOwogICAgZWxzZSBpZih3aGF0PT09ImxvZ3Rlc3QiKXRvYXN0KCJU"
    "ZXN0IG1lc3NhZ2Ugc2VudCB0byB0aGUgbG9nIGNoYW5uZWwiKTsKICAgIGVsc2UgaWYod2hhdD09"
    "PSJjbGVhcnN0b2NrIil0b2FzdChyLnJlbW92ZWQrIiByZW1vdmVkIik7CiAgICBlbHNlIHRvYXN0"
    "KCJEb25lIik7CiAgICBhd2FpdCByZWZyZXNoKCk7CiAgfSk7CiAgY29uc3QgdXBpYWRkPWRvY3Vt"
    "ZW50LmdldEVsZW1lbnRCeUlkKCJ1cGlhZGQiKTsKICBpZih1cGlhZGQpdXBpYWRkLm9uY2xpY2s9"
    "YXN5bmMoKT0+ewogICAgY29uc3QgaT11cGlhZGQuY2xvc2VzdCgiZGl2IikucXVlcnlTZWxlY3Rv"
    "cigiaW5wdXQiKTsKICAgIGNvbnN0IHY9aT8oaS52YWx1ZXx8IiIpLnRyaW0oKToiIjsKICAgIGlm"
    "KCF2KXt0b2FzdCgiUGFzdGUgdGhlIGV4dHJhIFVQSSBoYW5kbGUiKTtyZXR1cm47fQogICAgY29u"
    "c3QgY3VyPShBRE0ucGF5JiZBRE0ucGF5LmV4dHJhKXx8IiI7CiAgICBjb25zdCByPWF3YWl0IGFw"
    "aVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OiJ1cGlfZXh0cmEiLHZhbHVlOmN1cj8oY3Vy"
    "KyIsIit2KTp2fSk7CiAgICBpZihyKXtpLnZhbHVlPSIiO3RvYXN0KCJIYW5kbGUgYWRkZWQiKTth"
    "d2FpdCByZWZyZXNoKCk7fQogIH07CiAgY29uc3QgYWRtaW5hZGQ9ZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoImFkbWluYWRkIik7CiAgaWYoYWRtaW5hZGQpYWRtaW5hZGQub25jbGljaz1hc3luYygp"
    "PT57CiAgICBjb25zdCBpPWFkbWluYWRkLmNsb3Nlc3QoImRpdiIpLnF1ZXJ5U2VsZWN0b3IoImlu"
    "cHV0Iik7CiAgICBjb25zdCB2PWk/KGkudmFsdWV8fCIiKS50cmltKCkucmVwbGFjZSgvXkAvLCIi"
    "KToiIjsKICAgIGlmKCF2KXt0b2FzdCgiU2VuZCB0aGUgbnVtZXJpYyB1c2VyIGlkIik7cmV0dXJu"
    "O30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL21pc2MiLHt3aGF0OiJhZGRh"
    "ZG1pbiIsdmFsdWU6dn0pOwogICAgaWYocil7aS52YWx1ZT0iIjt0b2FzdCgiQWRtaW4gYWRkZWQg"
    "d2l0aCBubyBwb3dlcnMg4oCUIGdyYW50IHRoZW0gaW4gdGhlIGJvdCIpO2F3YWl0IHJlZnJlc2go"
    "KTt9CiAgfTsKICBjb25zdCBwcml6ZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicHJpemVzYXZl"
    "Iik7CiAgaWYocHJpemUpcHJpemUub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBpPXByaXpl"
    "LmNsb3Nlc3QoImRpdiIpLnF1ZXJ5U2VsZWN0b3IoImlucHV0Iik7CiAgICBjb25zdCByPWF3YWl0"
    "IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OiJsZWFkZXJib2FyZF9wcml6ZSIsdmFs"
    "dWU6KGkmJmkudmFsdWUpfHwiMCJ9KTsKICAgIGlmKHIpdG9hc3QoIlByaXplIHNhdmVkIik7CiAg"
    "fTsKICBjb25zdCBjcGFkZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY3BhZGQiKTsKICBpZihj"
    "cGFkZCljcGFkZC5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGNvZGU9KChkb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgiY3Bjb2RlIil8fHt9KS52YWx1ZXx8IiIpLnRyaW0oKTsKICAgIGNvbnN0"
    "IHBjdD0oKGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjcHBjdCIpfHx7fSkudmFsdWV8fCIiKS50"
    "cmltKCk7CiAgICBpZighY29kZXx8IXBjdCl7dG9hc3QoIkNvZGUgYW5kIHBlcmNlbnQsIHBsZWFz"
    "ZSIpO3JldHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9"
    "CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jb3Vwb24iLHtjb2RlOmNvZGUs"
    "cGVyY2VudDpwY3R9KTsKICAgIGlmKCFyKXJldHVybjsKICAgIGNvbnN0IGExPWRvY3VtZW50Lmdl"
    "dEVsZW1lbnRCeUlkKCJjcGNvZGUiKSxhMj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY3BwY3Qi"
    "KTsKICAgIGlmKGExKWExLnZhbHVlPSIiO2lmKGEyKWEyLnZhbHVlPSIiOwogICAgdG9hc3QoIkNv"
    "dXBvbiAiK2NvZGUudG9VcHBlckNhc2UoKSsiIGlzIGxpdmUiKTthd2FpdCByZWZyZXNoKCk7CiAg"
    "fTsKICBjb25zdCBmZz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZmxhc2hnbyIpOwogIGlmKGZn"
    "KWZnLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgY2FyZD1mZy5jbG9zZXN0KCIuY2FyZCIp"
    "fHxkb2N1bWVudDsKICAgIGNvbnN0IGlucz1jYXJkLnF1ZXJ5U2VsZWN0b3JBbGwoImlucHV0Iik7"
    "CiAgICBjb25zdCBwY3Q9KChpbnNbMF0mJmluc1swXS52YWx1ZSl8fCIyMCIpLCBocnM9KChpbnNb"
    "MV0mJmluc1sxXS52YWx1ZSl8fCI1Iik7CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25s"
    "eSIpO3JldHVybjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9mbGFzaCIs"
    "e3BlcmNlbnQ6cGN0LGhvdXJzOmhyc30pOwogICAgaWYocil7dG9hc3QoIkZsYXNoIHNhbGUgbGl2"
    "ZSDigJQgIitwY3QrIiUgZm9yICIraHJzKyJoIik7YXdhaXQgcmVmcmVzaCgpO30KICB9OwogIGNv"
    "bnN0IGJjPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJiY2dvIik7CiAgaWYoYmMpYmMub25jbGlj"
    "az1hc3luYygpPT57CiAgICBjb25zdCBjYXJkPWJjLmNsb3Nlc3QoIi5jYXJkIil8fGRvY3VtZW50"
    "OwogICAgY29uc3QgdGE9Y2FyZC5xdWVyeVNlbGVjdG9yKCJ0ZXh0YXJlYSIpOwogICAgY29uc3Qg"
    "dGV4dD0oKHRhJiZ0YS52YWx1ZSl8fCIiKS50cmltKCk7CiAgICBpZighdGV4dCl7dG9hc3QoIldy"
    "aXRlIHRoZSBtZXNzYWdlIGZpcnN0Iik7aWYodGEpdGEuZm9jdXMoKTtyZXR1cm47fQogICAgaWYo"
    "IUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgYmMuZGlzYWJsZWQ9dHJ1"
    "ZTtiYy50ZXh0Q29udGVudD0iU2VuZGluZ+KApiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgi"
    "L2FwaS9hZG1pbi9icm9hZGNhc3QiLHt0ZXh0OnRleHR9KTsKICAgIGJjLmRpc2FibGVkPWZhbHNl"
    "O2JjLnRleHRDb250ZW50PSJTZW5kIEJyb2FkY2FzdCI7CiAgICBpZihyKXt0b2FzdCgiU2VudCB0"
    "byAiK3Iuc2VudCsiIHBlb3BsZSIpO2lmKHRhKXRhLnZhbHVlPSIiO30KICB9OwogIGRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWRlcC1va10iKS5mb3JFYWNoKGI9PmIub25jbGljaz1h"
    "c3luYygpPT57CiAgICBjb25zdCBpZD1iLmRhdGFzZXQuZGVwT2s7CiAgICBpZighaWQpe3RvYXN0"
    "KCJOb3RoaW5nIHRvIGFwcHJvdmUiKTtyZXR1cm47fQogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnko"
    "Ii9hcGkvYWRtaW4vZGVwb3NpdCIse2lkOitpZCxhY3Rpb246ImFwcHJvdmUifSk7CiAgICBpZihy"
    "KXt0b2FzdCgiQ3JlZGl0ZWQiKTthd2FpdCByZWZyZXNoKCk7fQogIH0pOwogIGRvY3VtZW50LnF1"
    "ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXJldl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygp"
    "PT57CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9yZXZpZXciLHtpZDorYi5k"
    "YXRhc2V0LnJldn0pOwogICAgaWYocil7dG9hc3QoIlVwZGF0ZWQiKTthd2FpdCByZWZyZXNoKCk7"
    "fQogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXNldHRpbmddIikuZm9y"
    "RWFjaCh0PT57CiAgICB0Lm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25zdCBrZXk9dC5kYXRh"
    "c2V0LnNldHRpbmcsIG9uPSF0LmNsYXNzTGlzdC5jb250YWlucygib24iKTsKICAgICAgdC5jbGFz"
    "c0xpc3QudG9nZ2xlKCJvbiIsb24pOwogICAgICBpZighTElWRSl7dG9hc3Qob24/Ik9uIjoiT2Zm"
    "Iik7cmV0dXJuO30KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc2V0dGlu"
    "ZyIse2tleTprZXksdmFsdWU6b24/IjEiOiIwIn0pOwogICAgICBpZighcil7dC5jbGFzc0xpc3Qu"
    "dG9nZ2xlKCJvbiIsIW9uKTtyZXR1cm47fQogICAgICBBRE0uc3c9QURNLnN3fHx7fTtBRE0uc3db"
    "a2V5XT1vbjsKICAgICAgdG9hc3Qoa2V5LnJlcGxhY2UoL18vZywiICIpKyhvbj8iIG9uIjoiIG9m"
    "ZiIpKTsKICAgIH07CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtYWRt"
    "aW4tYWN0XSIpLmZvckVhY2goYj0+ewogICAgYi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgICAgY29u"
    "c3QgYWN0PWIuZGF0YXNldC5hZG1pbkFjdDsKICAgICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3"
    "IG9ubHkiKTtyZXR1cm47fQogICAgICBjb25zdCBsYWJlbD1iLnRleHRDb250ZW50O2IuZGlzYWJs"
    "ZWQ9dHJ1ZTtiLnRleHRDb250ZW50PSJXb3JraW5n4oCmIjsKICAgICAgbGV0IHI9bnVsbDsKICAg"
    "ICAgaWYoYWN0PT09ImJhY2t1cCIpICAgICAgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vYmFj"
    "a3VwIix7fSk7CiAgICAgIGVsc2UgaWYoYWN0PT09ImRpZ2VzdCIpIHI9YXdhaXQgYXBpVHJ5KCIv"
    "YXBpL2FkbWluL2RpZ2VzdCIse30pOwogICAgICBlbHNlIGlmKGFjdD09PSJleHBvcnQiKSByPWF3"
    "YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9leHBvcnQiLHt9KTsKICAgICAgZWxzZSBpZihhY3Q9PT0i"
    "c3luYyIpICAgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc3luYyIse30pOwogICAgICBiLmRp"
    "c2FibGVkPWZhbHNlO2IudGV4dENvbnRlbnQ9bGFiZWw7CiAgICAgIGlmKCFyKXJldHVybjsKICAg"
    "ICAgaWYoYWN0PT09ImJhY2t1cCIpdG9hc3QoIkRhdGFiYXNlIHNlbnQgdG8geW91IGluIGNoYXQi"
    "KTsKICAgICAgaWYoYWN0PT09ImRpZ2VzdCIpdG9hc3QoIkRpZ2VzdCBzZW50IHRvIHRoZSBhZG1p"
    "bnMiKTsKICAgICAgaWYoYWN0PT09ImV4cG9ydCIpdG9hc3Qoci5yb3dzKyIgb3JkZXJzIHNlbnQg"
    "YXMgQ1NWIik7CiAgICAgIGlmKGFjdD09PSJzeW5jIil0b2FzdCgiQ29tbWFuZHMgc3luY2VkIHdp"
    "dGggVGVsZWdyYW0iKTsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgfTsKICB9KTsKICBkb2N1"
    "bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1wcmljZV0iKS5mb3JFYWNoKGlucD0+ewogICAg"
    "aW5wLm9uY2hhbmdlPWFzeW5jKCk9PnsKICAgICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9u"
    "bHkiKTtyZXR1cm47fQogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9wcmlj"
    "aW5nIix7a2V5OmlucC5kYXRhc2V0LnByaWNlLHZhbHVlOmlucC52YWx1ZX0pOwogICAgICBpZihy"
    "KXt0b2FzdCgiUHJpY2luZyB1cGRhdGVkIik7YXdhaXQgcmVmcmVzaCgpO30KICAgIH07CiAgfSk7"
    "CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY3ByaWNlXSIpLmZvckVhY2goaW5w"
    "PT57CiAgICBpbnAub25jaGFuZ2U9YXN5bmMoKT0+ewogICAgICBpZighTElWRSl7dG9hc3QoIlBy"
    "ZXZpZXcgb25seSIpO3JldHVybjt9CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2Fk"
    "bWluL3ByaWNpbmciLHtjb3VudHJ5OmlucC5kYXRhc2V0LmNwcmljZSxwcmljZTppbnAudmFsdWV9"
    "KTsKICAgICAgaWYocil7dG9hc3QoaW5wLmRhdGFzZXQuY3ByaWNlKyIgbm93ICIrbW9uZXkoaW5w"
    "LnZhbHVlKSk7YXdhaXQgcmVmcmVzaCgpO30KICAgIH07CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlT"
    "ZWxlY3RvckFsbCgiW2RhdGEtcWFdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNv"
    "bnN0IHdoYXQ9Yi5kYXRhc2V0LnFhOwogICAgY29uc3QgZ289KHRhYixtc2cpPT57CiAgICAgIGNv"
    "bnN0IHQ9ZG9jdW1lbnQucXVlcnlTZWxlY3RvcihgI2FzdWIgYnV0dG9uW2RhdGEtYT0iJHt0YWJ9"
    "Il1gKTsKICAgICAgaWYodCl7dC5jbGljaygpO3RvYXN0KG1zZyk7fWVsc2UgdG9hc3QobXNnKTsK"
    "ICAgIH07CiAgICBpZih3aGF0PT09ImJyb2FkY2FzdCIpcmV0dXJuIGdvKCJtYXJrZXQiLCJDb21w"
    "b3NlIHRoZSBicm9hZGNhc3QgZnJvbSB0aGUgYm90OiAvYWRtaW4g4oaSIE1hcmtldGluZyIpOwog"
    "ICAgaWYod2hhdD09PSJzdG9jayIpcmV0dXJuIGdvKCJzdG9jayIsIkFkZCBzdG9jayBmcm9tIHRo"
    "ZSBib3Q6IC9hZG1pbiDihpIgU3RvY2sg4oaSIEFkZCBzdG9jayIpOwogICAgaWYod2hhdD09PSJm"
    "bGFzaCIpcmV0dXJuIGdvKCJtYXJrZXQiLCJGbGFzaCBzYWxlIGxpdmVzIGhlcmUiKTsKICAgIGlm"
    "KHdoYXQ9PT0iYmFja3VwIilyZXR1cm4gdG9hc3QoIkJhY2t1cHMgYXJlIHNlbnQgdG8geW91ciBs"
    "b2cgY2hhbm5lbCDigJQgL2FkbWluIOKGkiBTZXR0aW5ncyIpOwogIH0pOwogIGRvY3VtZW50LnF1"
    "ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXNldF0iKS5mb3JFYWNoKGlucD0+ewogICAgaW5wLm9uY2hh"
    "bmdlPWFzeW5jKCk9PnsKICAgICAgY29uc3Qga2V5PWlucC5kYXRhc2V0LnNldCwgdmFsPWlucC52"
    "YWx1ZTsKICAgICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAg"
    "ICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OmtleSx2YWx1"
    "ZTp2YWx9KTsKICAgICAgaWYocil7dG9hc3Qoa2V5LnJlcGxhY2UoL18vZywiICIpKyIgc2F2ZWQi"
    "KTthd2FpdCByZWZyZXNoKCk7fQogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "QWxsKCJbZGF0YS1jbXNdIikuZm9yRWFjaCh0PT57CiAgICB0Lm9uaW5wdXQ9KCk9PntDTVNbdC5k"
    "YXRhc2V0LmNtc109dC52YWx1ZTthcHBseUNNUygpOwogICAgICBjbGVhclRpbWVvdXQodC5fc3Yp"
    "O3QuX3N2PXNldFRpbWVvdXQoKCk9PnB1c2goIi9hcGkvYWRtaW4vY29udGVudCIse2tleTp0LmRh"
    "dGFzZXQuY21zLHZhbHVlOnQudmFsdWV9KSw1MDApOwogICAgICB0LmNsb3Nlc3QoIi5jbXNyb3ci"
    "KS5kYXRhc2V0LmY9KHQuY2xvc2VzdCgiLmNtc3JvdyIpLnF1ZXJ5U2VsZWN0b3IoImxhYmVsIiku"
    "dGV4dENvbnRlbnQrIiAiK3QudmFsdWUpLnRvTG93ZXJDYXNlKCk7fTt9KTsKICBjb25zdCBjcT1k"
    "b2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY3EiKTsKICBpZihjcSljcS5vbmlucHV0PSgpPT57Y29u"
    "c3QgcT1jcS52YWx1ZS50cmltKCkudG9Mb3dlckNhc2UoKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2Vs"
    "ZWN0b3JBbGwoIi5jbXNyb3ciKS5mb3JFYWNoKHI9PnIuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZSIs"
    "ISFxJiYhci5kYXRhc2V0LmYuaW5jbHVkZXMocSkpKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIi5jbXNncnAiKS5mb3JFYWNoKGc9PmcuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZSIsIWcu"
    "cXVlcnlTZWxlY3RvcigiLmNtc3Jvdzpub3QoLmhpZGUpIikpKTt9OwogIGNvbnN0IGNyPWRvY3Vt"
    "ZW50LmdldEVsZW1lbnRCeUlkKCJjbXNyZXNldCIpOwogIGlmKGNyKWNyLm9uY2xpY2s9KCk9PntP"
    "YmplY3QuYXNzaWduKENNUyxDTVMwKTthcHBseUNNUygpO3JlbmRlckFkbWluKCk7dG9hc3QoIlRl"
    "eHQgcmVzZXQgdG8gZGVmYXVsdHMiKTt9OwogIGNvbnN0IGFkZD1kb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgiY2gtYWRkIik7CiAgaWYoYWRkKWFkZC5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0"
    "IGk9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNoLWluIiksdj0oaS52YWx1ZXx8IiIpLnRyaW0o"
    "KTsKICAgIGlmKCF2KXt0b2FzdCgiUGFzdGUgYSBjaGFubmVsIGxpbmsgZmlyc3QiKTtpLmZvY3Vz"
    "KCk7cmV0dXJufQogICAgY29uc3QgcmVxPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjaC10eXBl"
    "IikudmFsdWU9PT0iMSI7CiAgICBpZihMSVZFKXsKICAgICAgYWRkLmRpc2FibGVkPXRydWU7CiAg"
    "ICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rpb246ImFk"
    "ZCIsaGFuZGxlOnYscmVxdWlyZWQ6cmVxfSk7CiAgICAgIGFkZC5kaXNhYmxlZD1mYWxzZTsKICAg"
    "ICAgaWYoIXIpcmV0dXJuOwogICAgICBpLnZhbHVlPSIiO2F3YWl0IHJlZnJlc2goKTt0b2FzdCgi"
    "Q2hhbm5lbCBzYXZlZCDigJQgdGhlIGJvdCB1c2VzIGl0IG5vdyIpO3JldHVybjsKICAgIH0KICAg"
    "IENIQU5ORUxTLnB1c2goe2lkOkRhdGUubm93KCksbmFtZTp2LnJlcGxhY2UoL15odHRwcz86XC9c"
    "L3RcLm1lXC8vLCJAIikuc2xpY2UoMCwyNiksaGFuZGxlOnYscmVxOnJlcSxvbjp0cnVlLG1lbWJl"
    "cnM6IuKAlCJ9KTsKICAgIGkudmFsdWU9IiI7cmVuZGVyQWRtaW4oKTt0b2FzdCgiQ2hhbm5lbCBh"
    "ZGRlZCIpO307CiAgY29uc3QgbGdhZGQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImxnLWFkZCIp"
    "OwogIGlmKGxnYWRkKWxnYWRkLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgaT1kb2N1bWVu"
    "dC5nZXRFbGVtZW50QnlJZCgibGctaW4iKSx2PShpLnZhbHVlfHwiIikudHJpbSgpOwogICAgaWYo"
    "IXYpe3RvYXN0KCJQYXN0ZSBhIGxvZyBjaGFubmVsIGZpcnN0Iik7aS5mb2N1cygpO3JldHVybn0K"
    "ICAgIGlmKExJVkUpewogICAgICBsZ2FkZC5kaXNhYmxlZD10cnVlOwogICAgICBjb25zdCByPWF3"
    "YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJhZGQiLGhhbmRsZTp2LGxv"
    "Zzp0cnVlfSk7CiAgICAgIGxnYWRkLmRpc2FibGVkPWZhbHNlOwogICAgICBpZighcilyZXR1cm47"
    "CiAgICAgIGkudmFsdWU9IiI7YXdhaXQgcmVmcmVzaCgpO3RvYXN0KCJMb2cgY2hhbm5lbCBzYXZl"
    "ZCIpO3JldHVybjsKICAgIH0KICAgIExPR0NILnB1c2goe2lkOkRhdGUubm93KCksbmFtZTp2LnJl"
    "cGxhY2UoL15odHRwcz86XC9cL3RcLm1lXC8vLCJAIikuc2xpY2UoMCwyNCksaGFuZGxlOnYscHJp"
    "bWFyeTpMT0dDSC5sZW5ndGg9PT0wLG9uOnRydWV9KTsKICAgIGkudmFsdWU9IiI7cmVuZGVyQWRt"
    "aW4oKTt0b2FzdCgiTG9nIGNoYW5uZWwgYWRkZWQiKTt9OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIltkYXRhLWNoZGVsXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAg"
    "IGlmKExJVkUpe2NvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rp"
    "b246ImRlbCIsaWQ6K2IuZGF0YXNldC5jaGRlbH0pOwogICAgICBpZihyKXthd2FpdCByZWZyZXNo"
    "KCk7dG9hc3QoIkNoYW5uZWwgcmVtb3ZlZCIpO31yZXR1cm47fQogICAgQ0hBTk5FTFM9Q0hBTk5F"
    "TFMuZmlsdGVyKGM9PmMuaWQhPWIuZGF0YXNldC5jaGRlbCk7cmVuZGVyQWRtaW4oKTt0b2FzdCgi"
    "Q2hhbm5lbCByZW1vdmVkIil9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1j"
    "aHJlcV0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBpZihMSVZFKXtjb25z"
    "dCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJyZXF1aXJlZCIs"
    "aWQ6K2IuZGF0YXNldC5jaHJlcX0pOwogICAgICBpZihyKXthd2FpdCByZWZyZXNoKCk7dG9hc3Qo"
    "IlVwZGF0ZWQiKTt9cmV0dXJuO30KICAgIGNvbnN0IGM9Q0hBTk5FTFMuZmluZCh4PT54LmlkPT1i"
    "LmRhdGFzZXQuY2hyZXEpO2MucmVxPSFjLnJlcTtyZW5kZXJBZG1pbigpO3RvYXN0KGMucmVxPyJO"
    "b3cgcmVxdWlyZWQiOiJOb3cgb3B0aW9uYWwiKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JB"
    "bGwoIltkYXRhLWNob25dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYo"
    "TElWRSl7Y29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vY2hhbm5lbCIse2FjdGlvbjoi"
    "dG9nZ2xlIixpZDorYi5kYXRhc2V0LmNob259KTsKICAgICAgaWYocil7YXdhaXQgcmVmcmVzaCgp"
    "O3RvYXN0KCJVcGRhdGVkIik7fXJldHVybjt9CiAgICBjb25zdCBjPUNIQU5ORUxTLmZpbmQoeD0+"
    "eC5pZD09Yi5kYXRhc2V0LmNob24pO2Mub249IWMub247cmVuZGVyQWRtaW4oKTt0b2FzdChjLm9u"
    "PyJDaGFubmVsIGVuYWJsZWQiOiJDaGFubmVsIHBhdXNlZCIpfSk7CiAgZG9jdW1lbnQucXVlcnlT"
    "ZWxlY3RvckFsbCgiW2RhdGEtbGdkZWxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAg"
    "IGNvbnN0IHdhc1A9TE9HQ0guZmluZChsPT5sLmlkPT1iLmRhdGFzZXQubGdkZWwpPy5wcmltYXJ5"
    "OwogICAgTE9HQ0g9TE9HQ0guZmlsdGVyKGw9PmwuaWQhPWIuZGF0YXNldC5sZ2RlbCk7CiAgICBp"
    "Zih3YXNQJiZMT0dDSFswXSlMT0dDSFswXS5wcmltYXJ5PXRydWU7cmVuZGVyQWRtaW4oKTt0b2Fz"
    "dCgiTG9nIGNoYW5uZWwgcmVtb3ZlZCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgi"
    "W2RhdGEtbGdzdGFyXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgICBMT0dDSC5mb3JF"
    "YWNoKGw9PmwucHJpbWFyeT0obC5pZD09Yi5kYXRhc2V0Lmxnc3RhcikpO3JlbmRlckFkbWluKCk7"
    "dG9hc3QoIlByZWZlcnJlZCBsb2cgY2hhbm5lbCBzZXQiKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2Vs"
    "ZWN0b3JBbGwoIltkYXRhLWxnb25dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9Pntjb25zdCBs"
    "PUxPR0NILmZpbmQoeD0+eC5pZD09Yi5kYXRhc2V0Lmxnb24pO2wub249IWwub247cmVuZGVyQWRt"
    "aW4oKTt0b2FzdChsLm9uPyJDaGFubmVsIG9uIjoiQ2hhbm5lbCBtdXRlZCIpfSk7CiAgZG9jdW1l"
    "bnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZXZvbl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0o"
    "KT0+e2NvbnN0IGU9RVZFTlRTWytiLmRhdGFzZXQuZXZvbl07ZS5vbj0hZS5vbjtyZW5kZXJBZG1p"
    "bigpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZXZjaF0iKS5mb3JFYWNo"
    "KHM9PnMub25jaGFuZ2U9KCk9PntFVkVOVFNbK3MuZGF0YXNldC5ldmNoXS5jaD0rcy52YWx1ZTt0"
    "b2FzdCgiUm91dGluZyB1cGRhdGVkIil9KTsKICBjb25zdCBhbGw9ZG9jdW1lbnQucXVlcnlTZWxl"
    "Y3RvcigiW2RhdGEtZXYtYWxsXSIpOwogIGlmKGFsbClhbGwub25jbGljaz0oKT0+e2NvbnN0IHQ9"
    "IUVWRU5UUy5ldmVyeShlPT5lLm9uKTtFVkVOVFMuZm9yRWFjaChlPT5lLm9uPXQpO3JlbmRlckFk"
    "bWluKCk7dG9hc3QodD8iQWxsIGV2ZW50cyBvbiI6IkFsbCBldmVudHMgb2ZmIil9OwogIGNvbnN0"
    "IG09ZG9jdW1lbnQucXVlcnlTZWxlY3RvcigiW2RhdGEtbWFzdGVyXSIpOwogIGlmKG0pbS5vbmNs"
    "aWNrPSgpPT57TE9HU19NQVNURVI9IUxPR1NfTUFTVEVSO3JlbmRlckFkbWluKCk7dG9hc3QoTE9H"
    "U19NQVNURVI/IkxvZ2dpbmcgZW5hYmxlZCI6IkFsbCBsb2dnaW5nIGRpc2FibGVkIil9OwogIGRv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWEtZ29dIikuZm9yRWFjaChiPT5iLm9uY2xp"
    "Y2s9KCk9PnsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNhc3ViIGJ1dHRvbiIpLmZv"
    "ckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseC5kYXRhc2V0LmE9PT1iLmRhdGFzZXQu"
    "YUdvKSk7CiAgICBBVEFCPWIuZGF0YXNldC5hR287cmVuZGVyQWRtaW4oKTt3aW5kb3cuc2Nyb2xs"
    "VG8oMCwwKTt9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS10Z10iKS5mb3JF"
    "YWNoKHQ9PnQub25jbGljaz0oKT0+dC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIpKTsKICBkb2N1bWVu"
    "dC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1zZXRzZWxdIikuZm9yRWFjaChzZWw9PnNlbC5vbmNo"
    "YW5nZT1hc3luYygpPT57CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVy"
    "bjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OnNl"
    "bC5kYXRhc2V0LnNldHNlbCx2YWx1ZTpzZWwudmFsdWV9KTsKICAgIGlmKHIpdG9hc3QoIlNhdmVk"
    "Iik7CiAgfSk7Cn0KCmNvbnN0IF9jYz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2FydGNoaXAi"
    "KTsKaWYoX2NjKV9jYy5vbmNsaWNrPWNhcnRTaGVldDsKcmVuZGVyKCk7cmVuZGVyQWRtaW4oKTth"
    "cHBseUZlYXR1cmVzKCk7CgovKiDilIDilIAgbGl2ZSBkYXRhOiBldmVyeXRoaW5nIGJlbG93IGNv"
    "bWVzIGZyb20gdGhlIGJvdCBkYXRhYmFzZSB3aGVuIHNlcnZlZCBieSB0aGUgYm90IOKUgOKUgCAq"
    "LwovKiBMSVZFIC8gTUUgLyBURyAvIElOSVQgYXJlIGRlY2xhcmVkIGF0IHRoZSB2ZXJ5IHRvcCBv"
    "ZiB0aGlzIHNjcmlwdCAqLwphc3luYyBmdW5jdGlvbiBhcGkocGF0aCxib2R5KXsKICBjb25zdCBv"
    "PXtoZWFkZXJzOnsiQ29udGVudC1UeXBlIjoiYXBwbGljYXRpb24vanNvbiIsIlgtSW5pdC1EYXRh"
    "IjpJTklULAogICAgIlgtVGVsZWdyYW0tSW5pdC1EYXRhIjpJTklULCJYLUFwcC1LZXkiOkFQUEtF"
    "WX19OwogIGlmKGJvZHkhPT11bmRlZmluZWQpe28ubWV0aG9kPSJQT1NUIjtvLmJvZHk9SlNPTi5z"
    "dHJpbmdpZnkoYm9keSk7fQogIGNvbnN0IHI9YXdhaXQgZmV0Y2gocGF0aCxvKTsKICBsZXQgZD1u"
    "dWxsO3RyeXtkPWF3YWl0IHIuanNvbigpO31jYXRjaChlcnIpe30KICBpZighci5vayl7Y29uc3Qg"
    "ZT1uZXcgRXJyb3IoKGQmJmQuZXJyb3IpfHwocGF0aCsiICIrci5zdGF0dXMpKTtlLnN0YXR1cz1y"
    "LnN0YXR1cztlLmRhdGE9ZDt0aHJvdyBlO30KICByZXR1cm4gZDsKfQpmdW5jdGlvbiBhZG1pbkFs"
    "bG93ZWQoKXtyZXR1cm4gIUxJVkUgfHwgISEoTUUmJk1FLmFkbWluKTt9CmZ1bmN0aW9uIGFwcGx5"
    "U3RhdGUoZCl7CiAgLyogZXZlcnkgbGlzdCBpcyByZXBsYWNlZCwgZXZlbiB3aGVuIGVtcHR5IOKA"
    "lCBhIG5ldyBzaG9wIG11c3QgbmV2ZXIgc2hvdyBkZW1vIGRhdGEgKi8KICBpZihkLmZlYXR1cmVz"
    "KU9iamVjdC5rZXlzKGQuZmVhdHVyZXMpLmZvckVhY2goaz0+e2lmKGsgaW4gRkVBVClGRUFUW2td"
    "PSEhZC5mZWF0dXJlc1trXTt9KTsKICBpZihkLmNvbnRlbnQpT2JqZWN0LmtleXMoZC5jb250ZW50"
    "KS5mb3JFYWNoKGs9PntpZihrIGluIENNUyYmZC5jb250ZW50W2tdIT1udWxsKUNNU1trXT1kLmNv"
    "bnRlbnRba107fSk7CiAgaWYoQXJyYXkuaXNBcnJheShkLnN0b2NrKSl7U1RPQ0subGVuZ3RoPTA7"
    "ZC5zdG9jay5mb3JFYWNoKHg9PlNUT0NLLnB1c2goeCkpO0ZJTFRFUkVEPVNUT0NLLnNsaWNlKCk7"
    "fQogIGlmKEFycmF5LmlzQXJyYXkoZC5vcmRlcnMpKXtPUkRFUlMubGVuZ3RoPTA7ZC5vcmRlcnMu"
    "Zm9yRWFjaCh4PT5PUkRFUlMucHVzaCh4KSk7fQogIGlmKEFycmF5LmlzQXJyYXkoZC50eCkpe1RY"
    "Lmxlbmd0aD0wO2QudHguZm9yRWFjaCh4PT5UWC5wdXNoKHgpKTt9CiAgaWYoQXJyYXkuaXNBcnJh"
    "eShkLmZlZWQpKUZFRUQ9ZC5mZWVkOwogIGlmKGQuZWFybilFQVJOPU9iamVjdC5hc3NpZ24oe30s"
    "RUFSTixkLmVhcm4pOwogIGlmKGQucHJlZnMpUFJFRlM9T2JqZWN0LmFzc2lnbih7fSxQUkVGUyxk"
    "LnByZWZzKTsKICBpZihBcnJheS5pc0FycmF5KGQuYWxlcnRzKSl3aW5kb3cuX19hbGVydHM9ZC5h"
    "bGVydHM7CiAgaWYoZC5zdG9yZSl7CiAgICBTVE9SRT1PYmplY3QuYXNzaWduKHt9LFNUT1JFLGQu"
    "c3RvcmUpOwogICAgaWYoZC5zdG9yZS5uYW1lKUNGRy5uYW1lPWQuc3RvcmUubmFtZTsKICAgIGlm"
    "KGQuc3RvcmUudGFnKUNGRy50YWc9ZC5zdG9yZS50YWc7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCJbZGF0YS1zdG9yZW5hbWVdIikuZm9yRWFjaChlPT5lLnRleHRDb250ZW50PUNGRy5u"
    "YW1lKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXN0b3JldGFnXSIpLmZv"
    "ckVhY2goZT0+ZS50ZXh0Q29udGVudD1DRkcudGFnKTsKICAgIGRvY3VtZW50LnRpdGxlPUNGRy5u"
    "YW1lOwogIH0KICBpZihkLmFkbSl7CiAgICBBRE09T2JqZWN0LmFzc2lnbih7fSxBRE0sZC5hZG0p"
    "OwogICAgaWYoQXJyYXkuaXNBcnJheShBRE0uY2hhbm5lbHMpKQogICAgICBDSEFOTkVMUz1BRE0u"
    "Y2hhbm5lbHMubWFwKGM9Pih7aWQ6Yy5pZCxuYW1lOmMubmFtZSxoYW5kbGU6Yy5oYW5kbGV8fCIi"
    "LAogICAgICAgIHJlcTohIWMucmVxdWlyZWQsb246ISFjLmFjdGl2ZSxtZW1iZXJzOiLigJQifSkp"
    "OwogICAgaWYoQURNLmxvZ3MpewogICAgICBMT0dTX01BU1RFUj0hIUFETS5sb2dzLm1hc3RlcjsK"
    "ICAgICAgaWYoQXJyYXkuaXNBcnJheShBRE0ubG9ncy5jaGFubmVscykpCiAgICAgICAgTE9HQ0g9"
    "QURNLmxvZ3MuY2hhbm5lbHMubWFwKGM9Pih7aWQ6Yy5pZCxuYW1lOmMubmFtZSxoYW5kbGU6IiIs"
    "CiAgICAgICAgICBwcmltYXJ5OiEhYy5wcmltYXJ5LG9uOiEhYy5hY3RpdmV9KSk7CiAgICAgIGlm"
    "KEFycmF5LmlzQXJyYXkoQURNLmxvZ3MuZXZlbnRzKSYmQURNLmxvZ3MuZXZlbnRzLmxlbmd0aCkK"
    "ICAgICAgICBFVkVOVFM9QURNLmxvZ3MuZXZlbnRzLm1hcChlPT4oe2s6ZS5sYWJlbCxvbjohIWUu"
    "b24sY2g6KExPR0NIWzBdfHx7fSkuaWR8fDF9KSk7CiAgICB9CiAgfQogIGlmKGQucmF0aW5ncylS"
    "RVZJRVdTPWQucmF0aW5nczsKICBpZih0eXBlb2YgZC5iYWxhbmNlPT09Im51bWJlciIpQkFMPWQu"
    "YmFsYW5jZTsKICBpZihkLm1lKU1FPWQubWU7CiAgaWYoZC5jYXJ0KUNBUlQ9ZC5jYXJ0OwogIGlm"
    "KGQud2lzaGxpc3QpV0lTSD1kLndpc2hsaXN0Owp9CmZ1bmN0aW9uIHNob3dTd2l0Y2gob24pewog"
    "IGNvbnN0IHN3PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdyIpOwogIGlmKHN3KXN3LnN0eWxl"
    "LmRpc3BsYXk9b24/ImZsZXgiOiJub25lIjsKfQp2YXIgTE9DS19USVRMRT0iIixMT0NLX01TRz0i"
    "IjsKZnVuY3Rpb24gbG9ja2VkU2NyZWVuKHRpdGxlLG1zZyl7CiAgTE9DS0VEPXRydWU7TE9DS19U"
    "SVRMRT10aXRsZTtMT0NLX01TRz1tc2c7CiAgc2hvd1N3aXRjaChmYWxzZSk7CiAgcGFpbnRMb2Nr"
    "ZWQoKTsKfQpmdW5jdGlvbiBwYWludExvY2tlZCgpewogIGNvbnN0IHN0PWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJzdGFnZSIpOwogIGlmKHN0KXN0LmlubmVySFRNTD0nPGRpdiBjbGFzcz0iY2Fy"
    "ZCIgc3R5bGU9Im1hcmdpbjoxOHB4O3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MjJweCI+Jwog"
    "ICAgKyc8aDMgc3R5bGU9Im1hcmdpbjowIDAgOHB4Ij4nK0xPQ0tfVElUTEUrJzwvaDM+JwogICAg"
    "Kyc8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAgMCAxNHB4Ij4nK0xPQ0tfTVNHKyc8L3A+"
    "JwogICAgKyc8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIG9uY2xpY2s9ImxvY2F0aW9uLnJlbG9h"
    "ZCgpIj5UcnkgYWdhaW48L2J1dHRvbj48L2Rpdj4nOwogIFsidGFicyIsImRvY2siXS5mb3JFYWNo"
    "KGlkPT57Y29uc3QgZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZChpZCk7aWYoZSllLnN0eWxlLmRp"
    "c3BsYXk9Im5vbmUiO30pOwp9CmFzeW5jIGZ1bmN0aW9uIGFwaVRyeShwYXRoLGJvZHkpewogIHRy"
    "eXtyZXR1cm4gYXdhaXQgYXBpKHBhdGgsYm9keSk7fQogIGNhdGNoKGUpe3RvYXN0KFN0cmluZyhl"
    "JiZlLm1lc3NhZ2V8fGUpLnNsaWNlKDAsOTApKTtyZXR1cm4gbnVsbDt9Cn0KYXN5bmMgZnVuY3Rp"
    "b24gcmVmcmVzaCgpewogIGlmKCFMSVZFKXJldHVybjsKICB0cnl7CiAgICBhcHBseVN0YXRlKGF3"
    "YWl0IGFwaSgiL2FwaS9zdGF0ZSIpKTsKICAgIGFwcGx5Q01TKCk7YXBwbHlGZWF0dXJlcygpO3N5"
    "bmNCYWwoKTtyZW5kZXIoKTtyZW5kZXJBZG1pbigpOwogIH1jYXRjaChlKXt9Cn0KYXN5bmMgZnVu"
    "Y3Rpb24gY29weVRleHQodCwgbGFiZWwpewogIGNvbnN0IHR4dD1TdHJpbmcodHx8IiIpOwogIGlm"
    "KCF0eHQpe3RvYXN0KCJOb3RoaW5nIHRvIGNvcHkiKTtyZXR1cm4gZmFsc2U7fQogIHRyeXsKICAg"
    "IGlmKG5hdmlnYXRvci5jbGlwYm9hcmQmJndpbmRvdy5pc1NlY3VyZUNvbnRleHQpe2F3YWl0IG5h"
    "dmlnYXRvci5jbGlwYm9hcmQud3JpdGVUZXh0KHR4dCk7fQogICAgZWxzZXsKICAgICAgY29uc3Qg"
    "dGE9ZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgidGV4dGFyZWEiKTsKICAgICAgdGEudmFsdWU9dHh0"
    "O3RhLnNldEF0dHJpYnV0ZSgicmVhZG9ubHkiLCIiKTsKICAgICAgdGEuc3R5bGUuY3NzVGV4dD0i"
    "cG9zaXRpb246Zml4ZWQ7bGVmdDotOTk5OXB4O29wYWNpdHk6MCI7CiAgICAgIGRvY3VtZW50LmJv"
    "ZHkuYXBwZW5kQ2hpbGQodGEpO3RhLnNlbGVjdCgpO3RhLnNldFNlbGVjdGlvblJhbmdlKDAsdHh0"
    "Lmxlbmd0aCk7CiAgICAgIGNvbnN0IG9rPWRvY3VtZW50LmV4ZWNDb21tYW5kKCJjb3B5Iik7ZG9j"
    "dW1lbnQuYm9keS5yZW1vdmVDaGlsZCh0YSk7CiAgICAgIGlmKCFvayl0aHJvdyBuZXcgRXJyb3Io"
    "ImNvcHkgcmVmdXNlZCIpOwogICAgfQogICAgdG9hc3QoKGxhYmVsfHwiQ29waWVkIikrIjogIiso"
    "dHh0Lmxlbmd0aD4yNj90eHQuc2xpY2UoMCwyNikrIuKApiI6dHh0KSk7CiAgICByZXR1cm4gdHJ1"
    "ZTsKICB9Y2F0Y2goZSl7CiAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250"
    "LXNpemU6MXJlbSI+Q29weSB0aGlzPC9oMz4KICAgICAgPHAgY2xhc3M9InN1YiI+WW91ciBicm93"
    "c2VyIGJsb2NrZWQgdGhlIGNsaXBib2FyZCDigJQgc2VsZWN0IGFuZCBjb3B5LjwvcD4KICAgICAg"
    "PHRleHRhcmVhIGNsYXNzPSJpbnAiIHJvd3M9IjMiIHJlYWRvbmx5IG9uY2xpY2s9InRoaXMuc2Vs"
    "ZWN0KCkiPiR7ZXNjKHR4dCl9PC90ZXh0YXJlYT4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJs"
    "b2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4IiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRvbj5gKTsK"
    "ICAgIHJldHVybiBmYWxzZTsKICB9Cn0KbGV0IFNUT1JFPXt1cGk6IiIscGF5ZWU6IiIscHJlZml4"
    "OiJQQVkiLHRlcm1zOiIiLHN1cHBvcnQ6IiJ9OwpsZXQgUFJFRlM9e3Jlc3RvY2s6dHJ1ZSxyZWNl"
    "aXB0czp0cnVlLHByb21vczp0cnVlLHB1c2g6dHJ1ZX07CmxldCBPRkZFUj0iIjsKZnVuY3Rpb24g"
    "b3BlblBheShsaW5rKXsKICBpZighbGluaylyZXR1cm47CiAgdHJ5ewogICAgaWYoVEcmJlRHLm9w"
    "ZW5MaW5rJiYvXmh0dHBzPzovLnRlc3QobGluaykpVEcub3BlbkxpbmsobGluayk7CiAgICBlbHNl"
    "IHdpbmRvdy5sb2NhdGlvbi5ocmVmPWxpbms7CiAgfWNhdGNoKGUpe3dpbmRvdy5sb2NhdGlvbi5o"
    "cmVmPWxpbms7fQp9CmFzeW5jIGZ1bmN0aW9uIGJvb3QoKXsKICBpZihURyl7dHJ5e1RHLnJlYWR5"
    "KCk7VEcuZXhwYW5kKCk7fWNhdGNoKGUpe319CiAgY29uc3QgaW5URz0hISgoSU5JVCYmSU5JVC5s"
    "ZW5ndGgpfHwoQVBQS0VZJiZBUFBLRVkubGVuZ3RoKSk7CiAgaWYoaW5URylzaG93U3dpdGNoKGZh"
    "bHNlKTsgICAgICAgICAgICAgIC8qIGZhaWwgY2xvc2VkOiBubyBhZG1pbiBzdHJpcCB1bnRpbCBw"
    "cm92ZW4gKi8KICBpZihsb2NhdGlvbi5wcm90b2NvbD09PSJmaWxlOiIpcmV0dXJuOyAgLyogb3Bl"
    "bmVkIGFzIGEgcGxhaW4gZmlsZSDigJQgZGVtbyBnYWxsZXJ5ICovCiAgbGV0IGQ7CiAgdHJ5ewog"
    "ICAgZD1hd2FpdCBhcGkoIi9hcGkvc3RhdGUiKTsKICB9Y2F0Y2goZSl7CiAgICBpZihlJiZlLnN0"
    "YXR1cz09PTQwMSl7CiAgICAgIGxldCB3aHk9IiIsZml4PSIiOwogICAgICB0cnl7CiAgICAgICAg"
    "Y29uc3Qgdz1hd2FpdCAoYXdhaXQgZmV0Y2goIi9hcGkvd2hvYW1pIix7aGVhZGVyczp7IlgtSW5p"
    "dC1EYXRhIjpJTklULAogICAgICAgICAgIlgtVGVsZWdyYW0tSW5pdC1EYXRhIjpJTklULCJYLUFw"
    "cC1LZXkiOkFQUEtFWX19KSkuanNvbigpOwogICAgICAgIGNvbnN0IFI9e21pc3Npbmc6WyJUZWxl"
    "Z3JhbSBkaWQgbm90IHBhc3MgeW91ciBhY2NvdW50IHRvIHRoaXMgcGFnZS4iLAogICAgICAgICAg"
    "ICAgICAgICAgIk9wZW4gdGhlIGJvdCBhbmQgdGFwIDxiPk9wZW4gU3RvcmU8L2I+LCBvciBzZW5k"
    "IDxiPi9hcHA8L2I+LiJdLAogICAgICAgICAgICAgICAgICJiYWQtZm9ybWF0IjpbIlRoZSBzaWdu"
    "LWluIGRhdGEgYXJyaXZlZCBkYW1hZ2VkLiIsIkNsb3NlIHRoZSBhcHAgYW5kIG9wZW4gaXQgYWdh"
    "aW4gZnJvbSB0aGUgYm90LiJdLAogICAgICAgICAgICAgICAgICJuby1oYXNoIjpbIlRoZSBzaWdu"
    "LWluIGRhdGEgYXJyaXZlZCBpbmNvbXBsZXRlLiIsIkNsb3NlIHRoZSBhcHAgYW5kIG9wZW4gaXQg"
    "YWdhaW4gZnJvbSB0aGUgYm90LiJdLAogICAgICAgICAgICAgICAgICJiYWQtaGFzaCI6WyJUaGUg"
    "c2lnbmF0dXJlIGRvZXMgbm90IG1hdGNoIHRoaXMgYm90J3MgdG9rZW4uIiwKICAgICAgICAgICAg"
    "ICAgICAgICJUaGUgcGFnZSBpcyBzZXJ2ZWQgYnkgYSBkaWZmZXJlbnQgYm90LCBvciBCT1RfVE9L"
    "RU4gb24gdGhlIHNlcnZlciBoYXMgcXVvdGVzIGFyb3VuZCBpdC4gU2VuZCA8Yj4vZGlhZzwvYj4g"
    "aW4gdGhlIGJvdCB0byBjb21wYXJlLiJdLAogICAgICAgICAgICAgICAgIHN0YWxlOlsiVGhhdCBz"
    "aWduLWluIGlzIG9sZGVyIHRoYW4gdGhlIGFsbG93ZWQgd2luZG93LiIsIlJlb3BlbiB0aGUgYXBw"
    "IGZyb20gdGhlIGJvdC4iXSwKICAgICAgICAgICAgICAgICAiYmFkLXVzZXIiOlsiWW91ciBhY2Nv"
    "dW50IGRldGFpbHMgY291bGQgbm90IGJlIHJlYWQuIiwiUmVvcGVuIHRoZSBhcHAgZnJvbSB0aGUg"
    "Ym90LiJdLAogICAgICAgICAgICAgICAgICJuby1pZCI6WyJUZWxlZ3JhbSBzZW50IG5vIHVzZXIg"
    "aWQuIiwiUmVvcGVuIHRoZSBhcHAgZnJvbSB0aGUgYm90LiJdfTsKICAgICAgICBjb25zdCByPVJb"
    "dy53aHldfHxbIlRoZSBzdG9yZSBjb3VsZCBub3QgY29uZmlybSB3aG8geW91IGFyZS4iLCJPcGVu"
    "IGl0IGFnYWluIGZyb20gdGhlIGJvdC4iXTsKICAgICAgICB3aHk9clswXSsiIDxjb2RlPigiKyh3"
    "LndoeXx8InVua25vd24iKSsiKTwvY29kZT4iOwogICAgICAgIGZpeD1yWzFdOwogICAgICB9Y2F0"
    "Y2goZXJyKXsKICAgICAgICB3aHk9IlRoZSBzdG9yZSBjb3VsZCBub3QgY29uZmlybSB3aG8geW91"
    "IGFyZS4iOwogICAgICAgIGZpeD0iT3BlbiBpdCBhZ2FpbiBmcm9tIHRoZSBib3Qgd2l0aCB0aGUg"
    "PGI+T3BlbiBTdG9yZTwvYj4gYnV0dG9uLiI7CiAgICAgIH0KICAgICAgbG9ja2VkU2NyZWVuKCJD"
    "YW5ub3Qgb3BlbiB0aGUgc3RvcmUiLHdoeSsiPGJyPjxicj4iK2ZpeCk7CiAgICB9CiAgICByZXR1"
    "cm47CiAgfQogIGFwcGx5U3RhdGUoZCk7TElWRT10cnVlOwogIHNob3dTd2l0Y2goISEoTUUmJk1F"
    "LmFkbWluKSk7ICAgICAgICAgICAvKiBvbmx5IGEgdmVyaWZpZWQgYWRtaW4gc2VlcyBpdCAqLwog"
    "IGFwcGx5Q01TKCk7YXBwbHlGZWF0dXJlcygpO3N5bmNCYWwoKTtyZW5kZXIoKTtyZW5kZXJBZG1p"
    "bigpOwp9CmFzeW5jIGZ1bmN0aW9uIHB1c2gocGF0aCxib2R5LG9rTXNnKXsKICBpZighTElWRSly"
    "ZXR1cm4gdHJ1ZTsKICB0cnl7Y29uc3Qgcj1hd2FpdCBhcGkocGF0aCxib2R5KTtpZihva01zZyl0"
    "b2FzdChva01zZyk7cmV0dXJuIHI7fQogIGNhdGNoKGUpe3RvYXN0KCJDb3VsZCBub3Qgc2F2ZSDi"
    "gJQgY2hlY2sgeW91ciBjb25uZWN0aW9uIik7cmV0dXJuIGZhbHNlO30KfQpib290KCk7Cjwvc2Ny"
    "aXB0Pgo8L2JvZHk+PC9odG1sPgo="
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
