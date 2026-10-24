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
DESIGN = "1-aurora"


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
    "store_tagline": "Premium accounts",
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
    "storeTag": "Premium accounts",
    "tick1": "<b>Instant delivery</b> on every country",
    "tick2": "Wallet top-ups <b>auto-verified</b>",
    "tick3": "Failed login? <b>Auto refund</b>",
    "tick4": "Daily check-in <b>bonus</b>",
    "heroKick": "Live inventory",
    "heroH1": "Accounts,<br>delivered instantly.",
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
    "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIiBkYXRhLWRlc2lnbj0iYXVyb3JhIj4KPGhl"
    "YWQ+CjxtZXRhIGNoYXJzZXQ9InV0Zi04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQiIGNvbnRlbnQ9"
    "IndpZHRoPWRldmljZS13aWR0aCxpbml0aWFsLXNjYWxlPTEsdmlld3BvcnQtZml0PWNvdmVyLG1h"
    "eGltdW0tc2NhbGU9MSI+CjxzY3JpcHQgc3JjPSJodHRwczovL3RlbGVncmFtLm9yZy9qcy90ZWxl"
    "Z3JhbS13ZWItYXBwLmpzIj48L3NjcmlwdD4KPHRpdGxlPlpvcm8gVEcgU3RvcmUg4oCUIFN0b3Jl"
    "IE1pbmkgQXBwIChEZXNpZ24gMSk8L3RpdGxlPgo8c3R5bGU+Cjpyb290ewogIC0tYmc6IzA4MGEx"
    "MjsgLS1iZzI6IzBlMTEyMDsgLS1jYXJkOnJnYmEoMjU1LDI1NSwyNTUsLjA0NSk7IC0tY2FyZDI6"
    "cmdiYSgyNTUsMjU1LDI1NSwuMDcpOwogIC0tbGluZTpyZ2JhKDI1NSwyNTUsMjU1LC4xMSk7IC0t"
    "dHg6I2VlZjBmNzsgLS10eDI6IzhlOTNhODsgLS1hY2M6IzhiN2JmZjsgLS1hY2MyOiMzZmQ4YzY7"
    "CiAgLS1vazojM2ZkOGM2OyAtLXdhcm46I2ZmYjY0ODsgLS1iYWQ6I2ZmNmI4MTsKICAtLXI6MThw"
    "eDsgLS1yczoxNHB4OyAtLWJ0bmg6NDRweDsgLS1mb250Oi1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNT"
    "eXN0ZW1Gb250LCdTZWdvZSBVSScsUm9ib3RvLHNhbnMtc2VyaWY7IC0tZmhlYWQ6LWFwcGxlLXN5"
    "c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsJ1NlZ29lIFVJJyxSb2JvdG8sc2Fucy1zZXJpZjsKICAt"
    "LXNoYWRvdzowIDEwcHggMzBweCByZ2JhKDAsMCwwLC4zNSk7IC0tcGlsbGdyYWQ6bGluZWFyLWdy"
    "YWRpZW50KDEzNWRlZywjOGI3YmZmLCMzZmQ4YzYpOyAtLWJ0bmJnOiM2ZjYzZTA7Cn0KKntib3gt"
    "c2l6aW5nOmJvcmRlci1ib3g7LXdlYmtpdC10YXAtaGlnaGxpZ2h0LWNvbG9yOnRyYW5zcGFyZW50"
    "fQpodG1sLGJvZHl7bWFyZ2luOjA7cGFkZGluZzowfQpib2R5e2JhY2tncm91bmQ6dmFyKC0tYmcp"
    "O2NvbG9yOnZhcigtLXR4KTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXNpemU6MTVweDts"
    "aW5lLWhlaWdodDoxLjQ1O21pbi1oZWlnaHQ6MTAwdmg7b3ZlcmZsb3cteDpoaWRkZW47cGFkZGlu"
    "Zy10b3A6MzJweDt9CmJvZHk6YmVmb3Jle2NvbnRlbnQ6IiI7cG9zaXRpb246Zml4ZWQ7aW5zZXQ6"
    "MDtwb2ludGVyLWV2ZW50czpub25lO3otaW5kZXg6MDtiYWNrZ3JvdW5kOnJhZGlhbC1ncmFkaWVu"
    "dCg2MCUgNDAlIGF0IDE1JSAwJSxyZ2JhKDEzOSwxMjMsMjU1LC4yOCksdHJhbnNwYXJlbnQgNzAl"
    "KSxyYWRpYWwtZ3JhZGllbnQoNTAlIDM1JSBhdCA5MCUgMTIlLHJnYmEoNjMsMjE2LDE5OCwuMiks"
    "dHJhbnNwYXJlbnQgNzAlKSxyYWRpYWwtZ3JhZGllbnQoNzAlIDUwJSBhdCA1MCUgMTAwJSxyZ2Jh"
    "KDEzOSwxMjMsMjU1LC4xMiksdHJhbnNwYXJlbnQgNzAlKX0KOjotd2Via2l0LXNjcm9sbGJhcnt3"
    "aWR0aDowO2hlaWdodDowfQojc2hlbGx7cG9zaXRpb246cmVsYXRpdmU7ei1pbmRleDoxO21heC13"
    "aWR0aDo1MjBweDttYXJnaW46MCBhdXRvO21pbi1oZWlnaHQ6MTAwdmg7cGFkZGluZzowIDE2cHgg"
    "MTEycHh9Ci5oaWRkZW57ZGlzcGxheTpub25lIWltcG9ydGFudH0KCiN0b3BiYXJ7cG9zaXRpb246"
    "c3RpY2t5O3RvcDozMnB4O3otaW5kZXg6NDA7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRl"
    "cjtnYXA6MTBweDtwYWRkaW5nOjE0cHggMCAxMnB4O2JhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50"
    "KHZhcigtLWJnKSA3MiUsdHJhbnNwYXJlbnQpfQoubG9nb3t3aWR0aDozOHB4O2hlaWdodDozOHB4"
    "O2JvcmRlci1yYWRpdXM6MTJweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2ZsZXg6"
    "MCAwIGF1dG87YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzBiMGQxODtmb250LXdl"
    "aWdodDo4MDA7Zm9udC1zaXplOi45cmVtO2xldHRlci1zcGFjaW5nOi0uMDNlbTt9Ci5icmFuZHtt"
    "aW4td2lkdGg6MDtmbGV4OjF9Ci5icmFuZCBie2Rpc3BsYXk6YmxvY2s7Zm9udC1mYW1pbHk6dmFy"
    "KC0tZmhlYWQpO2ZvbnQtc2l6ZTouOTVyZW07Zm9udC13ZWlnaHQ6ODAwO2xldHRlci1zcGFjaW5n"
    "Oi0uMDNlbTt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6"
    "ZWxsaXBzaXN9Ci5icmFuZCBzcGFue2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42NnJlbTtjb2xv"
    "cjp2YXIoLS10eDIpO2xldHRlci1zcGFjaW5nOi4wNmVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2Fz"
    "ZX0KLmJhbGNoaXB7ZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjdw"
    "eDtoZWlnaHQ6MzhweDtwYWRkaW5nOjAgMTNweDtib3JkZXItcmFkaXVzOjEycHg7YmFja2dyb3Vu"
    "ZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2NvbG9yOnZhcigtLXR4"
    "KTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXdlaWdodDo3MDA7Zm9udC1zaXplOi44NXJl"
    "bTt3aGl0ZS1zcGFjZTpub3dyYXA7Y3Vyc29yOnBvaW50ZXI7fQouYmFsY2hpcCAucGx1c3tkaXNw"
    "bGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO3dpZHRoOjIwcHg7aGVpZ2h0OjIwcHg7Ym9yZGVy"
    "LXJhZGl1czo3cHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzBiMGQxODtmb250"
    "LXdlaWdodDo4MDA7Zm9udC1zaXplOi44cmVtO2xpbmUtaGVpZ2h0OjF9CgouY2FyZHtiYWNrZ3Jv"
    "dW5kOnZhcigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJhZGl1"
    "czp2YXIoLS1yKTtwYWRkaW5nOjE2cHg7bWFyZ2luLWJvdHRvbToxMnB4O2JveC1zaGFkb3c6dmFy"
    "KC0tc2hhZG93KTtiYWNrZHJvcC1maWx0ZXI6Ymx1cigxNHB4KX0KLmNhcmQudGlnaHR7cGFkZGlu"
    "ZzoxM3B4fQouY2FyZCBoM3ttYXJnaW46MCAwIDNweDtmb250LWZhbWlseTp2YXIoLS1maGVhZCk7"
    "Zm9udC1zaXplOi45MnJlbTtmb250LXdlaWdodDo4MDA7bGV0dGVyLXNwYWNpbmc6LS4wM2VtfQou"
    "Y2FyZCAuc3Vie21hcmdpbjowO2NvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi43OHJlbX0KLnNl"
    "Y3tkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpzcGFjZS1i"
    "ZXR3ZWVuO2dhcDoxMHB4O21hcmdpbjoxOHB4IDJweCAxMHB4fQouc2VjIGgye21hcmdpbjowO2Zv"
    "bnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6LjgycmVtO2ZvbnQtd2VpZ2h0OjgwMDts"
    "ZXR0ZXItc3BhY2luZzouMTRlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7Y29sb3I6dmFyKC0t"
    "dHgyKX0KLnNlYyBhe2ZvbnQtc2l6ZTouNzVyZW07Y29sb3I6dmFyKC0tYWNjKTt0ZXh0LWRlY29y"
    "YXRpb246bm9uZTtmb250LXdlaWdodDo2MDA7d2hpdGUtc3BhY2U6bm93cmFwO2N1cnNvcjpwb2lu"
    "dGVyfQoKLyogb25lIGJ1dHRvbiBzeXN0ZW0g4oCUIHRleHQgY2FuIG5ldmVyIG92ZXJmbG93ICov"
    "Ci5idG57ZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250"
    "ZW50OmNlbnRlcjtnYXA6N3B4O2hlaWdodDp2YXIoLS1idG5oKTttaW4td2lkdGg6MDttYXgtd2lk"
    "dGg6MTAwJTtwYWRkaW5nOjAgMTZweDtib3JkZXI6MDtjdXJzb3I6cG9pbnRlcjtib3JkZXItcmFk"
    "aXVzOjEzcHg7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi44M3JlbTtmb250LXdl"
    "aWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LjAxZW07d2hpdGUtc3BhY2U6bm93cmFwO292ZXJmbG93"
    "OmhpZGRlbjt0ZXh0LW92ZXJmbG93OmVsbGlwc2lzO2xpbmUtaGVpZ2h0OjE7YmFja2dyb3VuZDp2"
    "YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzBiMGQxODt0cmFuc2l0aW9uOnRyYW5zZm9ybSAuMTVzLGZp"
    "bHRlciAuMTVzO30KLmJ0bjphY3RpdmV7dHJhbnNmb3JtOnNjYWxlKC45Nyl9Ci5idG46aG92ZXJ7"
    "ZmlsdGVyOmJyaWdodG5lc3MoMS4wNil9Ci5idG4gc3Zne3dpZHRoOjE2cHg7aGVpZ2h0OjE2cHg7"
    "ZmxleDowIDAgYXV0b30KLmJ0bi5ibG9ja3tkaXNwbGF5OmZsZXg7d2lkdGg6MTAwJX0KLmJ0bi5z"
    "bXtoZWlnaHQ6MzZweDtwYWRkaW5nOjAgMTJweDtmb250LXNpemU6Ljc1cmVtO2JvcmRlci1yYWRp"
    "dXM6MTBweH0KLmJ0bi54c3toZWlnaHQ6MzBweDtwYWRkaW5nOjAgMTBweDtmb250LXNpemU6Ljdy"
    "ZW07Ym9yZGVyLXJhZGl1czoxMHB4fQouYnRuLmdob3N0e2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7"
    "Y29sb3I6dmFyKC0tdHgpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSl9Ci5idG4uc29mdHti"
    "YWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eCk7Ym9yZGVyOjFweCBzb2xpZCB2"
    "YXIoLS1saW5lKX0KLmJ0bi5iYWR7YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjb2xvcjp2YXIoLS1i"
    "YWQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tYmFkKX0KLmJ0bltkaXNhYmxlZF17b3BhY2l0eTou"
    "NDU7cG9pbnRlci1ldmVudHM6bm9uZX0KLmJ0bnJvd3tkaXNwbGF5OmZsZXg7Z2FwOjhweH0KLmJ0"
    "bnJvdz4uYnRue2ZsZXg6MSAxIDA7bWluLXdpZHRoOjB9CgoucGlsbHtkaXNwbGF5OmlubGluZS1m"
    "bGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6NXB4O2hlaWdodDoyNHB4O3BhZGRpbmc6MCA5cHg7"
    "Ym9yZGVyLXJhZGl1czo5OXB4O2ZvbnQtc2l6ZTouNjZyZW07Zm9udC13ZWlnaHQ6NzAwO2xldHRl"
    "ci1zcGFjaW5nOi4wNGVtO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4Mik7"
    "Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTt3aGl0ZS1zcGFjZTpub3dyYXB9Ci5waWxsLm9u"
    "e2NvbG9yOnZhcigtLW9rKTtib3JkZXItY29sb3I6cmdiYSg2MywyMTYsMTk4LC40NSl9Ci5waWxs"
    "LmhvdHtjb2xvcjp2YXIoLS13YXJuKTtib3JkZXItY29sb3I6cmdiYSgyNTUsMTgyLDcyLC40NSl9"
    "Ci5waWxsLm9mZntjb2xvcjp2YXIoLS1iYWQpO2JvcmRlci1jb2xvcjpyZ2JhKDI1NSwxMDcsMTI5"
    "LC40NSl9Ci5kb3R7d2lkdGg6N3B4O2hlaWdodDo3cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dy"
    "b3VuZDp2YXIoLS1vayk7Ym94LXNoYWRvdzowIDAgOHB4IHZhcigtLW9rKX0KLmt2e2Rpc3BsYXk6"
    "ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47Z2Fw"
    "OjEycHg7cGFkZGluZzoxMHB4IDA7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0tbGluZSk7"
    "Zm9udC1zaXplOi44MnJlbTtjb2xvcjp2YXIoLS10eDIpfQoua3Y6bGFzdC1jaGlsZHtib3JkZXIt"
    "Ym90dG9tOjB9Ci5rdiBie2NvbG9yOnZhcigtLXR4KTtmb250LXdlaWdodDo3MDA7dGV4dC1hbGln"
    "bjpyaWdodDtvdmVyZmxvdy13cmFwOmFueXdoZXJlfQouZmxhZ3t3aWR0aDo0MnB4O2hlaWdodDo0"
    "MnB4O2JvcmRlci1yYWRpdXM6MTJweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2Zv"
    "bnQtc2l6ZToxLjM1cmVtO2ZsZXg6MCAwIGF1dG87YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9y"
    "ZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKX0KLmZsYWcgc3Zne3dpZHRoOjE4cHg7aGVpZ2h0OjE4"
    "cHh9CgouaGVyb3twb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRkZW47Ym9yZGVyLXJhZGl1"
    "czoyMnB4O3BhZGRpbmc6MjJweCAxOHB4O21hcmdpbjo0cHggMCAxNHB4O2JvcmRlcjoxcHggc29s"
    "aWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoMTM1ZGVnLHJnYmEoMTM5"
    "LDEyMywyNTUsLjI4KSxyZ2JhKDYzLDIxNiwxOTgsLjEyKSl9Ci5oZXJvIC5raWNre2ZvbnQtc2l6"
    "ZTouNnJlbTtsZXR0ZXItc3BhY2luZzouMmVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtjb2xv"
    "cjp2YXIoLS1hY2MpO2ZvbnQtd2VpZ2h0OjgwMH0KLmhlcm8gaDF7bWFyZ2luOjhweCAwIDZweDtm"
    "b250LWZhbWlseTp2YXIoLS1maGVhZCk7Zm9udC1zaXplOjEuNzVyZW07bGluZS1oZWlnaHQ6MS4w"
    "NTtsZXR0ZXItc3BhY2luZzotLjAzZW07Zm9udC13ZWlnaHQ6ODAwfQouaGVybyBwe21hcmdpbjow"
    "IDAgMTRweDtmb250LXNpemU6LjhyZW07Y29sb3I6dmFyKC0tdHgyKTttYXgtd2lkdGg6MjgwcHh9"
    "Ci5oZXJvIC5idG5yb3d7bWF4LXdpZHRoOjMzMHB4fQoKLmdyaWR7ZGlzcGxheTpncmlkO2dyaWQt"
    "dGVtcGxhdGUtY29sdW1uczoxZnIgMWZyO2dhcDoxMHB4fQoucGNhcmR7YmFja2dyb3VuZDp2YXIo"
    "LS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6dmFyKC0t"
    "cik7cGFkZGluZzoxNHB4O2JveC1zaGFkb3c6dmFyKC0tc2hhZG93KTtkaXNwbGF5OmZsZXg7Zmxl"
    "eC1kaXJlY3Rpb246Y29sdW1uO2dhcDo5cHg7Y3Vyc29yOnBvaW50ZXI7YmFja2Ryb3AtZmlsdGVy"
    "OmJsdXIoMTRweCl9Ci5wY2FyZCAudG9we2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7"
    "anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47Z2FwOjhweH0KLnBjYXJkIC5ubXtmb250LXdl"
    "aWdodDo3MDA7Zm9udC1zaXplOi44NXJlbTt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlk"
    "ZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXN9Ci5wY2FyZCAubWV0YXtmb250LXNpemU6LjY4cmVt"
    "O2NvbG9yOnZhcigtLXR4Mil9Ci5wY2FyZCAucHJpY2V7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQp"
    "O2ZvbnQtc2l6ZToxLjI1cmVtO2ZvbnQtd2VpZ2h0OjgwMDtsZXR0ZXItc3BhY2luZzotLjAyZW19"
    "Ci5wY2FyZCAucHJpY2Ugc3tmb250LXNpemU6LjcycmVtO2NvbG9yOnZhcigtLXR4Mik7Zm9udC13"
    "ZWlnaHQ6NDAwO21hcmdpbi1yaWdodDo2cHh9Ci5yb3dze2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVj"
    "dGlvbjpjb2x1bW47Z2FwOjlweH0KLnJvd3tkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVy"
    "O2dhcDoxMnB4O2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1s"
    "aW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXIpO3BhZGRpbmc6MTJweCAxM3B4O2JveC1zaGFkb3c6"
    "dmFyKC0tc2hhZG93KTtjdXJzb3I6cG9pbnRlcjt0cmFuc2l0aW9uOnRyYW5zZm9ybSAuMTVzO2Jh"
    "Y2tkcm9wLWZpbHRlcjpibHVyKDE0cHgpfQoucm93OmFjdGl2ZXt0cmFuc2Zvcm06c2NhbGUoLjk5"
    "KX0KLnJvdyAubWlke2ZsZXg6MTttaW4td2lkdGg6MH0KLnJvdyAubWlkIGJ7ZGlzcGxheTpibG9j"
    "aztmb250LXNpemU6Ljg4cmVtO3doaXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4"
    "dC1vdmVyZmxvdzplbGxpcHNpc30KLnJvdyAubWlkIHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1z"
    "aXplOi43cmVtO2NvbG9yOnZhcigtLXR4Mik7bWFyZ2luLXRvcDoycHh9Ci5yb3cgLnJ0e3RleHQt"
    "YWxpZ246cmlnaHQ7ZmxleDowIDAgYXV0b30KLnJvdyAucnQgLnB7Zm9udC13ZWlnaHQ6ODAwO2Zv"
    "bnQtc2l6ZTouOTVyZW07d2hpdGUtc3BhY2U6bm93cmFwfQoucm93IC5ydCAucCBze2ZvbnQtd2Vp"
    "Z2h0OjQwMDtmb250LXNpemU6LjcycmVtO2NvbG9yOnZhcigtLXR4Mik7bWFyZ2luLXJpZ2h0OjVw"
    "eH0KLmNhcm91e2Rpc3BsYXk6ZmxleDtnYXA6MTBweDtvdmVyZmxvdy14OmF1dG87cGFkZGluZzoy"
    "cHggMCA2cHg7bWFyZ2luOjAgLTE2cHggNnB4O3BhZGRpbmctbGVmdDoxNnB4O3BhZGRpbmctcmln"
    "aHQ6MTZweDtzY3JvbGwtc25hcC10eXBlOnggbWFuZGF0b3J5fQouY2Fyb3U+KntmbGV4OjAgMCAx"
    "NzJweDtzY3JvbGwtc25hcC1hbGlnbjpzdGFydH0KCm5hdiN0YWJze3Bvc2l0aW9uOmZpeGVkO3ot"
    "aW5kZXg6NjA7bGVmdDo1MCU7Ym90dG9tOmNhbGMoMTJweCArIGVudihzYWZlLWFyZWEtaW5zZXQt"
    "Ym90dG9tKSk7dHJhbnNmb3JtOnRyYW5zbGF0ZVgoLTUwJSk7bWF4LXdpZHRoOmNhbGMoMTAwdncg"
    "LSAyMHB4KX0KbmF2I3RhYnMgLmRvY2t7ZGlzcGxheTpmbGV4O2dhcDoycHg7cGFkZGluZzo3cHgg"
    "OHB4O2JvcmRlci1yYWRpdXM6MjJweDtiYWNrZ3JvdW5kOnJnYmEoMTYsMTgsMzAsLjcyKTtib3Jk"
    "ZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tkcm9wLWZpbHRlcjpibHVyKDE4cHgpO2JveC1z"
    "aGFkb3c6MCAxNHB4IDQwcHggcmdiYSgwLDAsMCwuNSl9Cm5hdiN0YWJzIGJ1dHRvbntwb3NpdGlv"
    "bjpyZWxhdGl2ZTtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1uO2FsaWduLWl0ZW1z"
    "OmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2dhcDo0cHg7bWluLXdpZHRoOjU4cHg7aGVp"
    "Z2h0OjQ4cHg7cGFkZGluZzowIDZweDtib3JkZXI6MDtiYWNrZ3JvdW5kOnRyYW5zcGFyZW50O2N1"
    "cnNvcjpwb2ludGVyO2NvbG9yOnZhcigtLXR4Mik7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9u"
    "dC1zaXplOi42cmVtO2ZvbnQtd2VpZ2h0OjcwMDtsZXR0ZXItc3BhY2luZzouMDNlbTtib3JkZXIt"
    "cmFkaXVzOjE2cHg7dHJhbnNpdGlvbjpjb2xvciAuMnMsYmFja2dyb3VuZCAuMnM7d2hpdGUtc3Bh"
    "Y2U6bm93cmFwfQpuYXYjdGFicyBidXR0b24gc3Zne3dpZHRoOjE5cHg7aGVpZ2h0OjE5cHh9Cm5h"
    "diN0YWJzIGJ1dHRvbi5vbntjb2xvcjojMGIwZDE4O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQp"
    "fQoKLnN1Ym5hdntkaXNwbGF5OmZsZXg7Z2FwOjdweDtvdmVyZmxvdy14OmF1dG87cGFkZGluZzoy"
    "cHggMCAxMnB4O21hcmdpbjowIC0ycHh9Ci5zdWJuYXYgYnV0dG9ue2ZsZXg6MCAwIGF1dG87aGVp"
    "Z2h0OjM0cHg7cGFkZGluZzowIDEzcHg7Ym9yZGVyLXJhZGl1czoxMHB4O2JvcmRlcjoxcHggc29s"
    "aWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgyKTtm"
    "b250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXNpemU6Ljc0cmVtO2ZvbnQtd2VpZ2h0OjcwMDtj"
    "dXJzb3I6cG9pbnRlcjt3aGl0ZS1zcGFjZTpub3dyYXB9Ci5zdWJuYXYgYnV0dG9uLm9ue2JhY2tn"
    "cm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiMwYjBkMTg7Ym9yZGVyLWNvbG9yOnRyYW5zcGFy"
    "ZW50fQoua3Bpc3tkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjFmciAxZnI7Z2Fw"
    "OjEwcHg7bWFyZ2luLWJvdHRvbToxMnB4fQoua3Bpe2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9y"
    "ZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXJzKTtwYWRkaW5n"
    "OjEzcHg7Ym94LXNoYWRvdzp2YXIoLS1zaGFkb3cpfQoua3BpIHNtYWxse2Rpc3BsYXk6YmxvY2s7"
    "Zm9udC1zaXplOi42NHJlbTtjb2xvcjp2YXIoLS10eDIpO2xldHRlci1zcGFjaW5nOi4xZW07dGV4"
    "dC10cmFuc2Zvcm06dXBwZXJjYXNlfQoua3BpIGJ7ZGlzcGxheTpibG9jazttYXJnaW4tdG9wOjVw"
    "eDtmb250LWZhbWlseTp2YXIoLS1maGVhZCk7Zm9udC1zaXplOjEuMzVyZW07Zm9udC13ZWlnaHQ6"
    "ODAwO2xldHRlci1zcGFjaW5nOi0uMDJlbX0KLmlucCxzZWxlY3QuaW5we3dpZHRoOjEwMCU7aGVp"
    "Z2h0OnZhcigtLWJ0bmgpO3BhZGRpbmc6MCAxM3B4O2JvcmRlci1yYWRpdXM6MTNweDtib3JkZXI6"
    "MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigt"
    "LXR4KTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXNpemU6LjgzcmVtO291dGxpbmU6bm9u"
    "ZTstd2Via2l0LWFwcGVhcmFuY2U6bm9uZTthcHBlYXJhbmNlOm5vbmV9CnNlbGVjdC5pbnB7YmFj"
    "a2dyb3VuZC1pbWFnZTpub25lfQouaW5wOjpwbGFjZWhvbGRlcntjb2xvcjp2YXIoLS10eDIpO29w"
    "YWNpdHk6Ljc1fQouaW5wOmZvY3Vze2JvcmRlci1jb2xvcjp2YXIoLS1hY2MpfQp0ZXh0YXJlYS5p"
    "bnB7aGVpZ2h0OmF1dG87bWluLWhlaWdodDo0MHB4O3BhZGRpbmc6MTBweCAxM3B4O2xpbmUtaGVp"
    "Z2h0OjEuNTtyZXNpemU6dmVydGljYWx9Ci5jbXNyb3cuaGlkZXtkaXNwbGF5Om5vbmV9Ci5pY29u"
    "Y2hpcHtwb3NpdGlvbjpyZWxhdGl2ZTtoZWlnaHQ6MzRweDttaW4td2lkdGg6MzRweDtwYWRkaW5n"
    "OjAgOXB4O2JvcmRlci1yYWRpdXM6OTlweDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2Jh"
    "Y2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4KTtkaXNwbGF5OmlubGluZS1ncmlk"
    "O3BsYWNlLWl0ZW1zOmNlbnRlcjtjdXJzb3I6cG9pbnRlcn0KLmljb25jaGlwIHN2Z3t3aWR0aDox"
    "N3B4O2hlaWdodDoxN3B4fQouaWNvbmNoaXAgaXtwb3NpdGlvbjphYnNvbHV0ZTt0b3A6LTVweDty"
    "aWdodDotNXB4O21pbi13aWR0aDoxN3B4O2hlaWdodDoxN3B4O3BhZGRpbmc6MCA0cHg7Ym9yZGVy"
    "LXJhZGl1czo5OXB4O2JhY2tncm91bmQ6dmFyKC0tYWNjKTtjb2xvcjojMGIwZDE4O2ZvbnQtc2l6"
    "ZTouNnJlbTtmb250LXN0eWxlOm5vcm1hbDtmb250LXdlaWdodDo4MDA7ZGlzcGxheTpncmlkO3Bs"
    "YWNlLWl0ZW1zOmNlbnRlcn0KLnN0YXJze2Rpc3BsYXk6aW5saW5lLWZsZXg7Z2FwOjFweDt2ZXJ0"
    "aWNhbC1hbGlnbjptaWRkbGV9Ci5zdGFycyBzdmd7d2lkdGg6MTFweDtoZWlnaHQ6MTFweDtjb2xv"
    "cjojZjVhNjIzfQouc3RhcnMgYntmb250LXNpemU6LjY2cmVtO2NvbG9yOnZhcigtLXR4Mik7Zm9u"
    "dC13ZWlnaHQ6NzAwO21hcmdpbi1sZWZ0OjNweH0KLmhlYXJ0e3Bvc2l0aW9uOmFic29sdXRlO3Rv"
    "cDo4cHg7cmlnaHQ6OHB4O3dpZHRoOjI2cHg7aGVpZ2h0OjI2cHg7Ym9yZGVyLXJhZGl1czo5OXB4"
    "O2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1s"
    "aW5lKTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjdXJzb3I6cG9pbnRlcjt6LWluZGV4OjJ9Ci5o"
    "ZWFydCBzdmd7d2lkdGg6MTNweDtoZWlnaHQ6MTNweDtjb2xvcjp2YXIoLS10eDIpfQouaGVhcnQu"
    "b257YmFja2dyb3VuZDp2YXIoLS1hY2MpO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVudH0KLmhlYXJ0"
    "Lm9uIHN2Z3tjb2xvcjojMGIwZDE4fQoucXR5e2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50"
    "ZXI7Z2FwOjA7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOjEzcHg7"
    "b3ZlcmZsb3c6aGlkZGVufQoucXR5IGJ1dHRvbnt3aWR0aDozOHB4O2hlaWdodDozOHB4O2JvcmRl"
    "cjowO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4KTtmb250LXNpemU6MXJl"
    "bTtmb250LXdlaWdodDo4MDA7Y3Vyc29yOnBvaW50ZXJ9Ci5xdHkgYnttaW4td2lkdGg6MzhweDt0"
    "ZXh0LWFsaWduOmNlbnRlcjtmb250LXNpemU6Ljg1cmVtfQoudGx7bWFyZ2luOjEwcHggMCAwO3Bh"
    "ZGRpbmctbGVmdDoxNnB4O2JvcmRlci1sZWZ0OjJweCBzb2xpZCB2YXIoLS1saW5lKX0KLnRsIGRp"
    "dntwb3NpdGlvbjpyZWxhdGl2ZTtwYWRkaW5nOjAgMCAxMXB4IDEwcHg7Zm9udC1zaXplOi43NHJl"
    "bTtjb2xvcjp2YXIoLS10eDIpfQoudGwgZGl2Omxhc3QtY2hpbGR7cGFkZGluZy1ib3R0b206MH0K"
    "LnRsIGRpdjo6YmVmb3Jle2NvbnRlbnQ6IiI7cG9zaXRpb246YWJzb2x1dGU7bGVmdDotMjFweDt0"
    "b3A6NHB4O3dpZHRoOjhweDtoZWlnaHQ6OHB4O2JvcmRlci1yYWRpdXM6OTlweDtiYWNrZ3JvdW5k"
    "OnZhcigtLWxpbmUpfQoudGwgZGl2Lm9rOjpiZWZvcmV7YmFja2dyb3VuZDp2YXIoLS1vayl9Ci50"
    "bCBkaXYub2sgYntjb2xvcjp2YXIoLS10eCl9Ci50bCBzbWFsbHtkaXNwbGF5OmJsb2NrO2ZvbnQt"
    "c2l6ZTouNjZyZW07b3BhY2l0eTouOH0KLmZjYXJke2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpm"
    "bGV4LXN0YXJ0O2dhcDoxMXB4O3BhZGRpbmc6MTFweCAwO2JvcmRlci1ib3R0b206MXB4IHNvbGlk"
    "IHZhcigtLWxpbmUpfQouZmNhcmQ6bGFzdC1jaGlsZHtib3JkZXItYm90dG9tOjB9Ci5mY2FyZCAu"
    "dHtmbGV4OjE7bWluLXdpZHRoOjB9Ci5mY2FyZCAudCBie2ZvbnQtc2l6ZTouODFyZW07ZGlzcGxh"
    "eTpibG9ja30KLmZjYXJkIC50IHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42OXJlbTtj"
    "b2xvcjp2YXIoLS10eDIpO2xpbmUtaGVpZ2h0OjEuNDU7bWFyZ2luLXRvcDoycHh9Ci5mcm93Lmhp"
    "ZGUsLmZncnAuaGlkZXtkaXNwbGF5Om5vbmV9Ci5iYXJze2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpmbGV4LWVuZDtnYXA6NXB4O2hlaWdodDo4NnB4O21hcmdpbi10b3A6MTJweH0KLmJhcnMgaXtm"
    "bGV4OjE7Ym9yZGVyLXJhZGl1czo2cHggNnB4IDNweCAzcHg7YmFja2dyb3VuZDp2YXIoLS1waWxs"
    "Z3JhZCk7ZGlzcGxheTpibG9ja30KLmZ1bm5lbCBkaXZ7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1z"
    "OmNlbnRlcjtnYXA6OXB4O21hcmdpbi10b3A6OXB4O2ZvbnQtc2l6ZTouNzRyZW19Ci5mdW5uZWwg"
    "aXtoZWlnaHQ6MjZweDtib3JkZXItcmFkaXVzOjdweDtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFk"
    "KTtkaXNwbGF5OmJsb2NrfQouZmVlZGxpbmV7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRl"
    "cjtnYXA6OXB4O2ZvbnQtc2l6ZTouNzNyZW07Y29sb3I6dmFyKC0tdHgyKTtwYWRkaW5nOjdweCAw"
    "O2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouZmVlZGxpbmU6bGFzdC1jaGls"
    "ZHtib3JkZXItYm90dG9tOjB9Ci5mZWVkbGluZSBie2NvbG9yOnZhcigtLXR4KX0KLmxie2Rpc3Bs"
    "YXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHg7cGFkZGluZzo5cHggMDtib3JkZXIt"
    "Ym90dG9tOjFweCBzb2xpZCB2YXIoLS1saW5lKX0KLmxiOmxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRv"
    "bTowfQoubGIgLnJre3dpZHRoOjI0cHg7aGVpZ2h0OjI0cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2Rp"
    "c3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9udC1zaXplOi43cmVtO2ZvbnQtd2VpZ2h0"
    "OjgwMDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIpfQoubGIgLnJrLmd7"
    "YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzBiMGQxOH0KLmNtc2dycC5oaWRle2Rp"
    "c3BsYXk6bm9uZX0KLmNtc3JvdyBsYWJlbCBie2NvbG9yOnZhcigtLWFjYyk7Zm9udC13ZWlnaHQ6"
    "NzAwfQoudGltZXJiYXJ7aGVpZ2h0OjVweDtib3JkZXItcmFkaXVzOjk5cHg7YmFja2dyb3VuZDp2"
    "YXIoLS1jYXJkMik7b3ZlcmZsb3c6aGlkZGVuO21hcmdpbjoxMHB4IDAgMnB4fQoudGltZXJiYXIg"
    "aXtkaXNwbGF5OmJsb2NrO2hlaWdodDoxMDAlO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO3Ry"
    "YW5zaXRpb246d2lkdGggMXMgbGluZWFyfQoudGltZXJiYXIubG93IGl7YmFja2dyb3VuZDp2YXIo"
    "LS1iYWQsI2U1NDg0ZCl9Ci5maWVsZHttYXJnaW4tYm90dG9tOjlweH0KLmZpZWxkIGxhYmVse2Rp"
    "c3BsYXk6YmxvY2s7Zm9udC1zaXplOi42OHJlbTtjb2xvcjp2YXIoLS10eDIpO2xldHRlci1zcGFj"
    "aW5nOi4wOWVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTttYXJnaW46MCAwIDVweCAycHg7Zm9u"
    "dC13ZWlnaHQ6NzAwfQouc3dhcHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3Rp"
    "ZnktY29udGVudDpzcGFjZS1iZXR3ZWVuO2dhcDoxMnB4O3BhZGRpbmc6MTFweCAwO2JvcmRlci1i"
    "b3R0b206MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouc3dhcDpsYXN0LWNoaWxke2JvcmRlci1ib3R0"
    "b206MH0KLnN3YXAgLnR7Zm9udC1zaXplOi44MnJlbTtmb250LXdlaWdodDo2MDA7bWluLXdpZHRo"
    "OjB9Ci5zd2FwIC50IHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC13ZWlnaHQ6NDAwO2ZvbnQtc2l6"
    "ZTouN3JlbTtjb2xvcjp2YXIoLS10eDIpfQoudGd7d2lkdGg6NDRweDtoZWlnaHQ6MjZweDtib3Jk"
    "ZXItcmFkaXVzOjk5cHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9yZGVyOjFweCBzb2xpZCB2"
    "YXIoLS1saW5lKTtwb3NpdGlvbjpyZWxhdGl2ZTtjdXJzb3I6cG9pbnRlcjtmbGV4OjAgMCBhdXRv"
    "fQoudGc6YWZ0ZXJ7Y29udGVudDoiIjtwb3NpdGlvbjphYnNvbHV0ZTt0b3A6M3B4O2xlZnQ6M3B4"
    "O3dpZHRoOjE4cHg7aGVpZ2h0OjE4cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3VuZDp2YXIo"
    "LS10eDIpO3RyYW5zaXRpb246LjIyc30KLnRnLm9ue2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQp"
    "O2JvcmRlci1jb2xvcjp0cmFuc3BhcmVudH0KLnRnLm9uOmFmdGVye2xlZnQ6MjFweDtiYWNrZ3Jv"
    "dW5kOiMwYjBkMTh9Ci50Zy5zbXt3aWR0aDozNnB4O2hlaWdodDoyMXB4fQoudGcuc206YWZ0ZXJ7"
    "d2lkdGg6MTRweDtoZWlnaHQ6MTRweDt0b3A6Mi41cHh9Ci50Zy5zbS5vbjphZnRlcntsZWZ0OjE4"
    "cHh9Ci5zdGFye3dpZHRoOjMwcHg7aGVpZ2h0OjMwcHg7Ym9yZGVyLXJhZGl1czo5cHg7ZGlzcGxh"
    "eTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2Jh"
    "Y2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4Mik7Y3Vyc29yOnBvaW50ZXI7Zmxl"
    "eDowIDAgYXV0b30KLnN0YXIub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzBi"
    "MGQxODtib3JkZXItY29sb3I6dHJhbnNwYXJlbnR9Ci5zdGFyIHN2Z3t3aWR0aDoxNHB4O2hlaWdo"
    "dDoxNHB4fQoKI3NoZWV0e3Bvc2l0aW9uOmZpeGVkO2luc2V0OjA7ei1pbmRleDo5MDtkaXNwbGF5"
    "Om5vbmV9CiNzaGVldC5vcGVue2Rpc3BsYXk6YmxvY2t9CiNzaGVldCAuYmd7cG9zaXRpb246YWJz"
    "b2x1dGU7aW5zZXQ6MDtiYWNrZ3JvdW5kOnJnYmEoMCwwLDAsLjU1KTtiYWNrZHJvcC1maWx0ZXI6"
    "Ymx1cigzcHgpfQojc2hlZXQgLnBhbmVse3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6NTAlO2JvdHRv"
    "bTowO3RyYW5zZm9ybTp0cmFuc2xhdGVYKC01MCUpO3dpZHRoOjEwMCU7bWF4LXdpZHRoOjUyMHB4"
    "O2JhY2tncm91bmQ6dmFyKC0tYmcyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRl"
    "ci1ib3R0b206MDtib3JkZXItcmFkaXVzOjI0cHggMjRweCAwIDA7cGFkZGluZzoxOHB4IDE2cHgg"
    "Y2FsYygyMHB4ICsgZW52KHNhZmUtYXJlYS1pbnNldC1ib3R0b20pKTthbmltYXRpb246dXAgLjI4"
    "cyBjdWJpYy1iZXppZXIoLjIsLjksLjMsMSk7bWF4LWhlaWdodDo5MnZoO292ZXJmbG93OmF1dG99"
    "CkBrZXlmcmFtZXMgdXB7ZnJvbXt0cmFuc2Zvcm06dHJhbnNsYXRlKC01MCUsMTAwJSl9dG97dHJh"
    "bnNmb3JtOnRyYW5zbGF0ZSgtNTAlLDApfX0KI3NoZWV0IC5ncmFie3dpZHRoOjM4cHg7aGVpZ2h0"
    "OjRweDtib3JkZXItcmFkaXVzOjlweDtiYWNrZ3JvdW5kOnZhcigtLWxpbmUpO21hcmdpbjowIGF1"
    "dG8gMTRweH0KLm90cGJveHtmb250LWZhbWlseTp1aS1tb25vc3BhY2UsTWVubG8sQ29uc29sYXMs"
    "bW9ub3NwYWNlO2ZvbnQtc2l6ZToxLjZyZW07bGV0dGVyLXNwYWNpbmc6LjM1ZW07dGV4dC1hbGln"
    "bjpjZW50ZXI7cGFkZGluZzoxNnB4O2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO2JhY2tncm91bmQ6"
    "dmFyKC0tY2FyZDIpO2JvcmRlcjoxcHggZGFzaGVkIHZhcigtLWxpbmUpO21hcmdpbjoxMHB4IDB9"
    "Ci5xcnt3aWR0aDoxNTBweDtoZWlnaHQ6MTUwcHg7bWFyZ2luOjZweCBhdXRvIDEwcHg7Ym9yZGVy"
    "LXJhZGl1czp2YXIoLS1ycyk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9yZGVyOjFweCBzb2xp"
    "ZCB2YXIoLS1saW5lKTtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2NvbG9yOnZhcigt"
    "LXR4Mik7Zm9udC1zaXplOi42NXJlbTtsZXR0ZXItc3BhY2luZzouMWVtO3RleHQtYWxpZ246Y2Vu"
    "dGVyfQouc3RlcHN7bWFyZ2luOjEycHggMCA0cHh9Ci5zdGVwe2Rpc3BsYXk6ZmxleDthbGlnbi1p"
    "dGVtczpjZW50ZXI7Z2FwOjEwcHg7cGFkZGluZzo4cHggMDtmb250LXNpemU6Ljc5cmVtO2NvbG9y"
    "OnZhcigtLXR4Mil9Ci5zdGVwIGl7d2lkdGg6MjJweDtoZWlnaHQ6MjJweDtib3JkZXItcmFkaXVz"
    "OjUwJTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVt"
    "czpjZW50ZXI7Zm9udC1zdHlsZTpub3JtYWw7Zm9udC1zaXplOi42NXJlbTtmb250LXdlaWdodDo4"
    "MDA7ZmxleDowIDAgYXV0b30KLnN0ZXAub257Y29sb3I6dmFyKC0tdHgpfQouc3RlcC5vbiBpe2Jh"
    "Y2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiMwYjBkMTg7Ym9yZGVyLWNvbG9yOnRyYW5z"
    "cGFyZW50fQouc3RlcC5ydW4gaXtib3JkZXItY29sb3I6dmFyKC0tYWNjKTtjb2xvcjp2YXIoLS1h"
    "Y2MpO2FuaW1hdGlvbjpwdWxzZSAxcyBpbmZpbml0ZX0KQGtleWZyYW1lcyBwdWxzZXs1MCV7b3Bh"
    "Y2l0eTouMzV9fQoudG9hc3R7cG9zaXRpb246Zml4ZWQ7bGVmdDo1MCU7Ym90dG9tOmNhbGMoOTZw"
    "eCArIGVudihzYWZlLWFyZWEtaW5zZXQtYm90dG9tKSk7dHJhbnNmb3JtOnRyYW5zbGF0ZVgoLTUw"
    "JSk7ei1pbmRleDoxMjA7YmFja2dyb3VuZDp2YXIoLS1iZzIpO2JvcmRlcjoxcHggc29saWQgdmFy"
    "KC0tbGluZSk7Y29sb3I6dmFyKC0tdHgpO3BhZGRpbmc6MTFweCAxNnB4O2JvcmRlci1yYWRpdXM6"
    "OTlweDtmb250LXNpemU6Ljc4cmVtO2ZvbnQtd2VpZ2h0OjYwMDtib3gtc2hhZG93OnZhcigtLXNo"
    "YWRvdyk7b3BhY2l0eTowO3BvaW50ZXItZXZlbnRzOm5vbmU7dHJhbnNpdGlvbjpvcGFjaXR5IC4y"
    "cyx0cmFuc2Zvcm0gLjJzO3doaXRlLXNwYWNlOm5vd3JhcDttYXgtd2lkdGg6OTB2dztvdmVyZmxv"
    "dzpoaWRkZW47dGV4dC1vdmVyZmxvdzplbGxpcHNpc30KLnRvYXN0LnNob3d7b3BhY2l0eToxO3Ry"
    "YW5zZm9ybTp0cmFuc2xhdGVYKC01MCUpIHRyYW5zbGF0ZVkoLTZweCl9CgovKiBwcmV2aWV3IGNo"
    "cm9tZSDigJQgbW9ja3VwIG9ubHksIG93biBzdHJpcCBzbyBpdCBuZXZlciBvdmVybGFwcyB0aGUg"
    "VUkgKi8KI3N3e3Bvc2l0aW9uOmZpeGVkO3RvcDowO2xlZnQ6MDtyaWdodDowO2hlaWdodDozMnB4"
    "O3otaW5kZXg6MjAwO2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250"
    "ZW50OmNlbnRlcjtnYXA6NXB4OwogIGJhY2tncm91bmQ6cmdiYSgyMCwyMCwyNiwuNzUpO2JvcmRl"
    "ci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tkcm9wLWZpbHRlcjpibHVyKDEwcHgp"
    "fQojc3cgZW17Zm9udC1zdHlsZTpub3JtYWw7Zm9udC1zaXplOi41NnJlbTtsZXR0ZXItc3BhY2lu"
    "ZzouMThlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7Y29sb3I6dmFyKC0tdHgyKTttYXJnaW4t"
    "cmlnaHQ6NHB4fQojc3cgYnV0dG9ue2hlaWdodDoyMnB4O3BhZGRpbmc6MCAxMnB4O2JvcmRlci1y"
    "YWRpdXM6OTlweDtib3JkZXI6MDtjdXJzb3I6cG9pbnRlcjtiYWNrZ3JvdW5kOnRyYW5zcGFyZW50"
    "O2NvbG9yOnZhcigtLXR4Mik7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi42cmVt"
    "O2ZvbnQtd2VpZ2h0OjcwMDtsZXR0ZXItc3BhY2luZzouMDdlbTt0ZXh0LXRyYW5zZm9ybTp1cHBl"
    "cmNhc2V9CiNzdyBidXR0b24ub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzBi"
    "MGQxOH0KCi8qIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCBB"
    "VVJPUkEgVUkgS0lUIOKAlCBwb3J0ZWQgY29tcG9uZW50cyDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZDilZDilZDilZDilZDilZAgKi8KQHByb3BlcnR5IC0tZ2F7c3ludGF4OiI8YW5nbGU+"
    "Ijtpbml0aWFsLXZhbHVlOjBkZWc7aW5oZXJpdHM6ZmFsc2V9CkBwcm9wZXJ0eSAtLWdwe3N5bnRh"
    "eDoiPHBlcmNlbnRhZ2U+Ijtpbml0aWFsLXZhbHVlOjUlO2luaGVyaXRzOmZhbHNlfQpAcHJvcGVy"
    "dHkgLS1nb3tzeW50YXg6IjxhbmdsZT4iO2luaXRpYWwtdmFsdWU6MGRlZztpbmhlcml0czpmYWxz"
    "ZX0KCi8qIDIzIMK3IFNoaW55IENUQSDigJQgY29uaWMgYm9yZGVyICsgZG90IG1hc2sgKyBpbm5l"
    "ciBzaGltbWVyICsgYnJlYXRoZSAqLwouYnRuLmN0YXstLWR1cjozLjJzOy0taGw6I2NmYzhmZjtw"
    "b3NpdGlvbjpyZWxhdGl2ZTtpc29sYXRpb246aXNvbGF0ZTtib3JkZXI6MXB4IHNvbGlkIHRyYW5z"
    "cGFyZW50OwogIGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KHZhcigtLWJ0bmJnKSx2YXIoLS1i"
    "dG5iZykpIHBhZGRpbmctYm94LAogICAgY29uaWMtZ3JhZGllbnQoZnJvbSBjYWxjKHZhcigtLWdh"
    "KSAtIHZhcigtLWdvKSksdHJhbnNwYXJlbnQsdmFyKC0taGwpIHZhcigtLWdwKSwKICAgICAgI2Zm"
    "ZiBjYWxjKHZhcigtLWdwKSoyKSx2YXIoLS1obCkgY2FsYyh2YXIoLS1ncCkqMyksdHJhbnNwYXJl"
    "bnQgY2FsYyh2YXIoLS1ncCkqNCkpIGJvcmRlci1ib3g7CiAgYm94LXNoYWRvdzppbnNldCAwIDAg"
    "MCAxcHggcmdiYSgyNTUsMjU1LDI1NSwuMDgpOwogIHRyYW5zaXRpb246LS1nbyAuOHMgY3ViaWMt"
    "YmV6aWVyKC4yNSwxLC41LDEpLC0tZ3AgLjhzIGN1YmljLWJlemllciguMjUsMSwuNSwxKSx0cmFu"
    "c2Zvcm0gLjE1czsKICBhbmltYXRpb246Z2EgdmFyKC0tZHVyKSBsaW5lYXIgaW5maW5pdGV9Ci5i"
    "dG4uY3RhOmJlZm9yZSwuYnRuLmN0YTphZnRlcntjb250ZW50OiIiO3Bvc2l0aW9uOmFic29sdXRl"
    "O2xlZnQ6NTAlO3RvcDo1MCU7dHJhbnNsYXRlOi01MCUgLTUwJTt6LWluZGV4Oi0xO3BvaW50ZXIt"
    "ZXZlbnRzOm5vbmV9Ci5idG4uY3RhOmJlZm9yZXt3aWR0aDpjYWxjKDEwMCUgLSA2cHgpO2hlaWdo"
    "dDpjYWxjKDEwMCUgLSA2cHgpO2JvcmRlci1yYWRpdXM6aW5oZXJpdDtvcGFjaXR5Oi4zNTsKICBi"
    "YWNrZ3JvdW5kOnJhZGlhbC1ncmFkaWVudChjaXJjbGUgYXQgMnB4IDJweCwjZmZmIC41cHgsdHJh"
    "bnNwYXJlbnQgMCkgcGFkZGluZy1ib3g7YmFja2dyb3VuZC1zaXplOjRweCA0cHg7CiAgLXdlYmtp"
    "dC1tYXNrLWltYWdlOmNvbmljLWdyYWRpZW50KGZyb20gY2FsYyh2YXIoLS1nYSkgKyA0NWRlZyks"
    "IzAwMCx0cmFuc3BhcmVudCAxMCUgOTAlLCMwMDApOwogIG1hc2staW1hZ2U6Y29uaWMtZ3JhZGll"
    "bnQoZnJvbSBjYWxjKHZhcigtLWdhKSArIDQ1ZGVnKSwjMDAwLHRyYW5zcGFyZW50IDEwJSA5MCUs"
    "IzAwMCl9Ci5idG4uY3RhOmFmdGVye3dpZHRoOjEwMCU7YXNwZWN0LXJhdGlvOjE7b3BhY2l0eTou"
    "NTU7CiAgYmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoLTUwZGVnLHRyYW5zcGFyZW50LHZhcigt"
    "LWhsKSx0cmFuc3BhcmVudCk7CiAgLXdlYmtpdC1tYXNrLWltYWdlOnJhZGlhbC1ncmFkaWVudChj"
    "aXJjbGUgYXQgYm90dG9tLHRyYW5zcGFyZW50IDQwJSwjMDAwKTsKICBtYXNrLWltYWdlOnJhZGlh"
    "bC1ncmFkaWVudChjaXJjbGUgYXQgYm90dG9tLHRyYW5zcGFyZW50IDQwJSwjMDAwKTsKICBhbmlt"
    "YXRpb246c2hpbW1lciB2YXIoLS1kdXIpIGxpbmVhciBpbmZpbml0ZX0KLmJ0bi5jdGE6aG92ZXJ7"
    "LS1ncDoyMiU7LS1nbzo5NWRlZ30KLmJ0bi5jdGE6YWN0aXZle3RyYW5zbGF0ZTowIDFweH0KQGtl"
    "eWZyYW1lcyBnYXt0b3stLWdhOjM2MGRlZ319CkBrZXlmcmFtZXMgc2hpbW1lcnt0b3tyb3RhdGU6"
    "MzYwZGVnfX0KCi8qIDIyIMK3IExpcXVpZCBnbGFzcyAqLwouYnRuLmdsYXNzLC5nbGFzc2NhcmR7"
    "cG9zaXRpb246cmVsYXRpdmU7YmFja2dyb3VuZDpjb2xvci1taXgoaW4gc3JnYix2YXIoLS1jYXJk"
    "MikgNzAlLHRyYW5zcGFyZW50KTsKICBib3JkZXI6MXB4IHNvbGlkIGNvbG9yLW1peChpbiBzcmdi"
    "LCNmZmYgMTYlLHZhcigtLWxpbmUpKTtjb2xvcjp2YXIoLS10eCk7CiAgYmFja2Ryb3AtZmlsdGVy"
    "OmJsdXIoMTRweCkgc2F0dXJhdGUoMS40KTstd2Via2l0LWJhY2tkcm9wLWZpbHRlcjpibHVyKDE0"
    "cHgpIHNhdHVyYXRlKDEuNCk7CiAgYm94LXNoYWRvdzppbnNldCAxcHggMXB4IDFweCAtLjVweCBy"
    "Z2JhKDI1NSwyNTUsMjU1LC41NSksaW5zZXQgLTFweCAtMXB4IDFweCAtLjVweCByZ2JhKDI1NSwy"
    "NTUsMjU1LC4zKSwKICAgIGluc2V0IDAgMCA4cHggNnB4IHJnYmEoMjU1LDI1NSwyNTUsLjA3KSww"
    "IDJweCAxMHB4IHJnYmEoMCwwLDAsLjEyKX0KLmJ0bi5nbGFzczphZnRlcntjb250ZW50OiIiO3Bv"
    "c2l0aW9uOmFic29sdXRlO2luc2V0OjFweCAxcHggYXV0bztoZWlnaHQ6NDIlO2JvcmRlci1yYWRp"
    "dXM6aW5oZXJpdDsKICBiYWNrZ3JvdW5kOmxpbmVhci1ncmFkaWVudCgxODBkZWcscmdiYSgyNTUs"
    "MjU1LDI1NSwuMzQpLHRyYW5zcGFyZW50KTtwb2ludGVyLWV2ZW50czpub25lfQouYnRuLmdsYXNz"
    "OmhvdmVye3RyYW5zZm9ybTpzY2FsZSgxLjAzKX0KCi8qIHByZXNzIHJpcHBsZSAoa2l0IGRvY2sv"
    "YnV0dG9uIGZlZWwpICovCi5yaXB7cG9zaXRpb246YWJzb2x1dGU7Ym9yZGVyLXJhZGl1czo1MCU7"
    "dHJhbnNmb3JtOnRyYW5zbGF0ZSgtNTAlLC01MCUpIHNjYWxlKDApO3BvaW50ZXItZXZlbnRzOm5v"
    "bmU7CiAgYmFja2dyb3VuZDpyYWRpYWwtZ3JhZGllbnQoY2lyY2xlLHJnYmEoMjU1LDI1NSwyNTUs"
    "LjU1KSx0cmFuc3BhcmVudCA3MCUpO2FuaW1hdGlvbjpyaXAgLjU1cyBlYXNlLW91dCBmb3J3YXJk"
    "c30KQGtleWZyYW1lcyByaXB7dG97dHJhbnNmb3JtOnRyYW5zbGF0ZSgtNTAlLC01MCUpIHNjYWxl"
    "KDEpO29wYWNpdHk6MH19CgovKiAyNCDCtyBBbmltYXRlZCBiYW5uZXIgKyAxNyDCtyBsb2dvIGNs"
    "b3VkIG1hcnF1ZWUgKi8KLmJhbm5lcntwb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRkZW47"
    "Ym9yZGVyLXJhZGl1czoxM3B4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7CiAgYmFja2dy"
    "b3VuZDp2YXIoLS1jYXJkKTttYXJnaW46MnB4IDAgMTJweDtoZWlnaHQ6MzhweDtkaXNwbGF5OmZs"
    "ZXg7YWxpZ24taXRlbXM6Y2VudGVyOwogIC13ZWJraXQtbWFzay1pbWFnZTpsaW5lYXItZ3JhZGll"
    "bnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCA4JSwjMDAwIDkyJSx0cmFuc3BhcmVudCk7CiAgbWFz"
    "ay1pbWFnZTpsaW5lYXItZ3JhZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCA4JSwjMDAwIDky"
    "JSx0cmFuc3BhcmVudCl9Ci5iYW5uZXIgLnRyYWNre2Rpc3BsYXk6ZmxleDtnYXA6MzBweDt3aGl0"
    "ZS1zcGFjZTpub3dyYXA7YW5pbWF0aW9uOm1hcnEgMjZzIGxpbmVhciBpbmZpbml0ZTt3aWxsLWNo"
    "YW5nZTp0cmFuc2Zvcm19Ci5iYW5uZXI6aG92ZXIgLnRyYWNre2FuaW1hdGlvbi1wbGF5LXN0YXRl"
    "OnBhdXNlZH0KLmJhbm5lciBzcGFue2ZvbnQtc2l6ZTouN3JlbTtjb2xvcjp2YXIoLS10eDIpO2Zv"
    "bnQtd2VpZ2h0OjYwMDtsZXR0ZXItc3BhY2luZzouMDNlbTtkaXNwbGF5OmlubGluZS1mbGV4O2Fs"
    "aWduLWl0ZW1zOmNlbnRlcjtnYXA6NnB4fQouYmFubmVyIHNwYW4gYntjb2xvcjp2YXIoLS1hY2Mp"
    "fQouYmFubmVyIHNwYW4gc3Zne3dpZHRoOjEzcHg7aGVpZ2h0OjEzcHg7Y29sb3I6dmFyKC0tYWNj"
    "KX0KQGtleWZyYW1lcyBtYXJxe3Rve3RyYW5zZm9ybTp0cmFuc2xhdGVYKC01MCUpfX0KLmNsb3Vk"
    "e2Rpc3BsYXk6ZmxleDtnYXA6MjZweDtvdmVyZmxvdzpoaWRkZW47cGFkZGluZzo0cHggMCAycHg7"
    "CiAgLXdlYmtpdC1tYXNrLWltYWdlOmxpbmVhci1ncmFkaWVudCg5MGRlZyx0cmFuc3BhcmVudCwj"
    "MDAwIDEyJSwjMDAwIDg4JSx0cmFuc3BhcmVudCk7CiAgbWFzay1pbWFnZTpsaW5lYXItZ3JhZGll"
    "bnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCAxMiUsIzAwMCA4OCUsdHJhbnNwYXJlbnQpfQouY2xv"
    "dWQgLnR7ZGlzcGxheTpmbGV4O2dhcDoyNnB4O2FuaW1hdGlvbjptYXJxIDMwcyBsaW5lYXIgaW5m"
    "aW5pdGV9Ci5jbG91ZCBpe2ZvbnQtc3R5bGU6bm9ybWFsO2ZvbnQtc2l6ZTouNjhyZW07Zm9udC13"
    "ZWlnaHQ6ODAwO2xldHRlci1zcGFjaW5nOi4xNGVtO2NvbG9yOnZhcigtLXR4Mik7b3BhY2l0eTou"
    "NTU7d2hpdGUtc3BhY2U6bm93cmFwfQoKLyogMDcgwrcgR3JhZGllbnQgdHJhY2luZyAqLwoudHJh"
    "Y2V7ZGlzcGxheTpibG9jazt3aWR0aDoxMDAlO2hlaWdodDoxMHB4O21hcmdpbjotMnB4IDAgOHB4"
    "O292ZXJmbG93OnZpc2libGV9Ci50cmFjZSAuYmFzZXtzdHJva2U6dmFyKC0tbGluZSk7c3Ryb2tl"
    "LXdpZHRoOjEuNH0KLnRyYWNlIC5sZWFke3N0cm9rZS13aWR0aDoxLjg7c3Ryb2tlLWxpbmVjYXA6"
    "cm91bmR9Ci5zZWMgaDJ7cG9zaXRpb246cmVsYXRpdmV9CgovKiAwMSArIDEzIMK3IERvdCBsb2Fk"
    "ZXIgKi8KLmRvdGdyaWR7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQo"
    "NywxZnIpO2dhcDozcHg7d2lkdGg6NzBweH0KLmRvdGdyaWQgaXt3aWR0aDoxMDAlO2FzcGVjdC1y"
    "YXRpbzoxO2JvcmRlci1yYWRpdXM6MnB4O2JhY2tncm91bmQ6Y29sb3ItbWl4KGluIHNyZ2IsdmFy"
    "KC0tdHgpIDE0JSx0cmFuc3BhcmVudCk7dHJhbnNpdGlvbjpiYWNrZ3JvdW5kIC4xMnMsdHJhbnNm"
    "b3JtIC4xMnN9Ci5kb3RncmlkIGkuYWN0e2JhY2tncm91bmQ6dmFyKC0tYWNjKTt0cmFuc2Zvcm06"
    "c2NhbGUoMS4xMil9Ci5kb3Rze2Rpc3BsYXk6aW5saW5lLWZsZXg7Z2FwOjRweDthbGlnbi1pdGVt"
    "czpjZW50ZXJ9Ci5kb3RzIGl7d2lkdGg6NXB4O2hlaWdodDo1cHg7Ym9yZGVyLXJhZGl1czo1MCU7"
    "YmFja2dyb3VuZDp2YXIoLS1hY2MpO2FuaW1hdGlvbjpib3VuY2UgMXMgaW5maW5pdGUgZWFzZS1p"
    "bi1vdXR9Ci5kb3RzIGk6bnRoLWNoaWxkKDIpe2FuaW1hdGlvbi1kZWxheTouMTVzfS5kb3RzIGk6"
    "bnRoLWNoaWxkKDMpe2FuaW1hdGlvbi1kZWxheTouM3N9CkBrZXlmcmFtZXMgYm91bmNlezAlLDgw"
    "JSwxMDAle3RyYW5zZm9ybTp0cmFuc2xhdGVZKDApO29wYWNpdHk6LjM1fTQwJXt0cmFuc2Zvcm06"
    "dHJhbnNsYXRlWSgtNXB4KTtvcGFjaXR5OjF9fQoKLyogc3BsYXNoICovCiNzcGxhc2h7cG9zaXRp"
    "b246Zml4ZWQ7aW5zZXQ6MDt6LWluZGV4OjMwMDtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246"
    "Y29sdW1uO2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2dhcDoxNnB4"
    "OwogIGJhY2tncm91bmQ6dmFyKC0tYmcpO3RyYW5zaXRpb246b3BhY2l0eSAuNXMgZWFzZSx2aXNp"
    "YmlsaXR5IC41c30KI3NwbGFzaC5nb25le29wYWNpdHk6MDt2aXNpYmlsaXR5OmhpZGRlbn0KI3Nw"
    "bGFzaCAubWFya3t3aWR0aDo1OHB4O2hlaWdodDo1OHB4O2JvcmRlci1yYWRpdXM6MTJweDtkaXNw"
    "bGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpOwog"
    "IGNvbG9yOiMwYjBkMTg7Zm9udC13ZWlnaHQ6ODAwO2ZvbnQtc2l6ZToxLjFyZW07YW5pbWF0aW9u"
    "Om1hcmtpbiAuN3MgY3ViaWMtYmV6aWVyKC4yLDEuNCwuNCwxKSBib3RofQojc3BsYXNoIGg0e21h"
    "cmdpbjowO2ZvbnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6MXJlbTtmb250LXdlaWdo"
    "dDo4MDA7bGV0dGVyLXNwYWNpbmc6LS4wM2VtOwogIGFuaW1hdGlvbjpmYWRldXAgLjZzIC4xNXMg"
    "Ym90aH0KI3NwbGFzaCBzbWFsbHtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjVyZW07bGV0"
    "dGVyLXNwYWNpbmc6LjIyZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2FuaW1hdGlvbjpmYWRl"
    "dXAgLjZzIC4yNXMgYm90aH0KQGtleWZyYW1lcyBtYXJraW57ZnJvbXtvcGFjaXR5OjA7dHJhbnNm"
    "b3JtOnNjYWxlKC42KSByb3RhdGUoLTEyZGVnKX10b3tvcGFjaXR5OjE7dHJhbnNmb3JtOm5vbmV9"
    "fQpAa2V5ZnJhbWVzIGZhZGV1cHtmcm9te29wYWNpdHk6MDt0cmFuc2Zvcm06dHJhbnNsYXRlWSg4"
    "cHgpfXRve29wYWNpdHk6MTt0cmFuc2Zvcm06bm9uZX19CgovKiAwOCDCtyBLaW5ldGljIGdyaWQg"
    "Y2FudmFzICovCiNrZ3twb3NpdGlvbjpmaXhlZDtpbnNldDowO3otaW5kZXg6MDtwb2ludGVyLWV2"
    "ZW50czpub25lO29wYWNpdHk6LjU1fQoKLyogc3RhZ2dlcmVkIHJldmVhbCBpcyBkcml2ZW4gYnkg"
    "dGhlIFdlYiBBbmltYXRpb25zIEFQSSAoY2Fubm90IGNsYXNoIHdpdGggdGhlbWUgQ1NTKSAqLwoK"
    "LyogMDIgKyAyNSDCtyBEb2NrIHdpdGggc3ByaW5nIHBpbGwgKi8KbmF2I3RhYnMgLmRvY2t7cG9z"
    "aXRpb246cmVsYXRpdmV9Cm5hdiN0YWJzIC5kcGlsbHtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjA7"
    "dG9wOjA7ei1pbmRleDowO2JvcmRlci1yYWRpdXM6MTZweDtiYWNrZ3JvdW5kOnZhcigtLXBpbGxn"
    "cmFkKTsKICBib3gtc2hhZG93OjAgNnB4IDE4cHggY29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tYWNj"
    "KSAzNSUsdHJhbnNwYXJlbnQpO3BvaW50ZXItZXZlbnRzOm5vbmV9Cm5hdiN0YWJzIGJ1dHRvbntw"
    "b3NpdGlvbjpyZWxhdGl2ZTt6LWluZGV4OjE7d2lsbC1jaGFuZ2U6dHJhbnNmb3JtOwogIHRyYW5z"
    "aXRpb246Y29sb3IgLjI2cyBjdWJpYy1iZXppZXIoLjIyLDEsLjM2LDEpLHRyYW5zZm9ybSAuMThz"
    "IGN1YmljLWJlemllciguMywxLjUsLjUsMSl9Cm5hdiN0YWJzIGJ1dHRvbi5vbntiYWNrZ3JvdW5k"
    "OnRyYW5zcGFyZW50O2NvbG9yOiMwYjBkMTh9Cm5hdiN0YWJzIC5kcGlsbHtvcGFjaXR5OjB9Cm5h"
    "diN0YWJzIC5kcGlsbC5yZWFkeXtvcGFjaXR5OjF9CgovKiAxNCDCtyBWLXRhYnMgc2xpZGluZyBp"
    "bmRpY2F0b3IgKi8KLnN1Ym5hdntwb3NpdGlvbjpyZWxhdGl2ZX0KLnN1Ym5hdiAudnBpbGx7cG9z"
    "aXRpb246YWJzb2x1dGU7bGVmdDowO3RvcDowO3otaW5kZXg6MDtib3JkZXItcmFkaXVzOjEwcHg7"
    "YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7dHJhbnNpdGlvbjpub25lO3BvaW50ZXItZXZlbnRz"
    "Om5vbmV9Ci5zdWJuYXYgYnV0dG9ue3Bvc2l0aW9uOnJlbGF0aXZlO3otaW5kZXg6MX0KLnN1Ym5h"
    "diBidXR0b24ub257YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjb2xvcjojMGIwZDE4O2JvcmRlci1j"
    "b2xvcjp0cmFuc3BhcmVudH0KCi8qIDE1IMK3IEFuaW1hdGVkIHByb2ZpbGUgY2FyZCDigJQgdGls"
    "dCArIGdsYXJlICovCi50aWx0e3RyYW5zZm9ybS1zdHlsZTpwcmVzZXJ2ZS0zZDt0cmFuc2l0aW9u"
    "OnRyYW5zZm9ybSAuMzVzIGN1YmljLWJlemllciguMjIsMSwuMzYsMSk7d2lsbC1jaGFuZ2U6dHJh"
    "bnNmb3JtfQoudGlsdCAuZ2xhcmV7cG9zaXRpb246YWJzb2x1dGU7aW5zZXQ6MDtib3JkZXItcmFk"
    "aXVzOmluaGVyaXQ7cG9pbnRlci1ldmVudHM6bm9uZTtvcGFjaXR5OjA7dHJhbnNpdGlvbjpvcGFj"
    "aXR5IC4zczsKICBiYWNrZ3JvdW5kOnJhZGlhbC1ncmFkaWVudCgyNDBweCBjaXJjbGUgYXQgdmFy"
    "KC0tbXgsNTAlKSB2YXIoLS1teSw1MCUpLHJnYmEoMjU1LDI1NSwyNTUsLjIyKSx0cmFuc3BhcmVu"
    "dCA2MCUpfQoudGlsdDpob3ZlciAuZ2xhcmV7b3BhY2l0eToxfQouc2hpbmVjYXJke3Bvc2l0aW9u"
    "OnJlbGF0aXZlO292ZXJmbG93OmhpZGRlbn0KLnNoaW5lY2FyZDpiZWZvcmV7Y29udGVudDoiIjtw"
    "b3NpdGlvbjphYnNvbHV0ZTt0b3A6LTYwJTtsZWZ0Oi0zMCU7d2lkdGg6NTUlO2hlaWdodDoyMjAl"
    "O3RyYW5zZm9ybTpyb3RhdGUoMThkZWcpOwogIGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KDkw"
    "ZGVnLHRyYW5zcGFyZW50LHJnYmEoMjU1LDI1NSwyNTUsLjE2KSx0cmFuc3BhcmVudCk7YW5pbWF0"
    "aW9uOmNhcmRzaGluZSA1LjVzIGVhc2UtaW4tb3V0IGluZmluaXRlO3BvaW50ZXItZXZlbnRzOm5v"
    "bmV9CkBrZXlmcmFtZXMgY2FyZHNoaW5lezAlLDcwJXtsZWZ0Oi00MCV9MTAwJXtsZWZ0OjEyMCV9"
    "fQoKLyogMDUgwrcgSG9sZCBhY3Rpb24gYnV0dG9uICovCi5ob2xke3Bvc2l0aW9uOnJlbGF0aXZl"
    "O292ZXJmbG93OmhpZGRlbn0KLmhvbGQgLmZpbGx7cG9zaXRpb246YWJzb2x1dGU7bGVmdDowO3Rv"
    "cDowO2JvdHRvbTowO3dpZHRoOjA7YmFja2dyb3VuZDpyZ2JhKDI1NSwyNTUsMjU1LC4yNSk7dHJh"
    "bnNpdGlvbjp3aWR0aCAuMDVzIGxpbmVhcn0KLmhvbGQuZG9uZXthbmltYXRpb246cG9wIC40cyBj"
    "dWJpYy1iZXppZXIoLjIsMS42LC40LDEpfQpAa2V5ZnJhbWVzIHBvcHs1MCV7dHJhbnNmb3JtOnNj"
    "YWxlKDEuMDQpfX0KCi8qIDExIMK3IE1vcnBoaW5nIHNlYXJjaCAqLwoubXNlYXJjaHtkaXNwbGF5"
    "OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo4cHg7bWFyZ2luOjAgMCAxMHB4fQoubXNlYXJj"
    "aCAuZmllbGR3cmFwe2ZsZXg6MTttaW4td2lkdGg6MDtwb3NpdGlvbjpyZWxhdGl2ZTtoZWlnaHQ6"
    "NDBweH0KLm1zZWFyY2ggaW5wdXR7d2lkdGg6MTAwJTtoZWlnaHQ6NDBweDtwYWRkaW5nOjAgMTNw"
    "eCAwIDM2cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7"
    "CiAgYmFja2dyb3VuZDp2YXIoLS1jYXJkKTtjb2xvcjp2YXIoLS10eCk7Zm9udC1mYW1pbHk6dmFy"
    "KC0tZm9udCk7Zm9udC1zaXplOi44cmVtO291dGxpbmU6bm9uZTsKICB0cmFuc2l0aW9uOmJveC1z"
    "aGFkb3cgLjNzIGN1YmljLWJlemllciguMjIsMSwuMzYsMSksYm9yZGVyLWNvbG9yIC4zc30KLm1z"
    "ZWFyY2ggaW5wdXQ6Zm9jdXN7Ym9yZGVyLWNvbG9yOnZhcigtLWFjYyk7Ym94LXNoYWRvdzowIDAg"
    "MCA0cHggY29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tYWNjKSAxNiUsdHJhbnNwYXJlbnQpfQoubXNl"
    "YXJjaCBzdmd7cG9zaXRpb246YWJzb2x1dGU7bGVmdDoxMnB4O3RvcDoxMXB4O3dpZHRoOjE3cHg7"
    "aGVpZ2h0OjE3cHg7Y29sb3I6dmFyKC0tdHgyKTt0cmFuc2l0aW9uOmNvbG9yIC4yNXN9Ci5tc2Vh"
    "cmNoIGlucHV0OmZvY3VzK3N2ZywubXNlYXJjaCAuZmllbGR3cmFwOmZvY3VzLXdpdGhpbiBzdmd7"
    "Y29sb3I6dmFyKC0tYWNjKX0KLmNoaXByb3d7ZGlzcGxheTpmbGV4O2dhcDo2cHg7b3ZlcmZsb3ct"
    "eDphdXRvO3BhZGRpbmctYm90dG9tOjEwcHh9Ci5jaGlwcm93IGJ1dHRvbntmbGV4OjAgMCBhdXRv"
    "O2hlaWdodDozMHB4O3BhZGRpbmc6MCAxMnB4O2JvcmRlci1yYWRpdXM6OTlweDtib3JkZXI6MXB4"
    "IHNvbGlkIHZhcigtLWxpbmUpOwogIGJhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigt"
    "LXR4Mik7Zm9udC1mYW1pbHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi43cmVtO2ZvbnQtd2VpZ2h0"
    "OjcwMDtjdXJzb3I6cG9pbnRlcjt3aGl0ZS1zcGFjZTpub3dyYXA7CiAgdHJhbnNpdGlvbjp0cmFu"
    "c2Zvcm0gLjJzIGN1YmljLWJlemllciguMywxLjUsLjUsMSksYmFja2dyb3VuZCAuMnMsY29sb3Ig"
    "LjJzfQouY2hpcHJvdyBidXR0b24ub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6"
    "IzBiMGQxODtib3JkZXItY29sb3I6dHJhbnNwYXJlbnQ7dHJhbnNmb3JtOnNjYWxlKDEuMDQpfQoK"
    "Lyogc2tlbGV0b25zICgxMyDCtyBsb2FkZXIpICovCi5za3twb3NpdGlvbjpyZWxhdGl2ZTtvdmVy"
    "ZmxvdzpoaWRkZW47YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9yZGVyLXJhZGl1czp2YXIoLS1y"
    "cyk7aGVpZ2h0Ojc0cHg7bWFyZ2luLWJvdHRvbTo5cHh9Ci5zazphZnRlcntjb250ZW50OiIiO3Bv"
    "c2l0aW9uOmFic29sdXRlO2luc2V0OjA7YmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoOTBkZWcs"
    "dHJhbnNwYXJlbnQsY29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tdHgpIDEwJSx0cmFuc3BhcmVudCks"
    "dHJhbnNwYXJlbnQpOwogIHRyYW5zZm9ybTp0cmFuc2xhdGVYKC0xMDAlKTthbmltYXRpb246c2hp"
    "bSAxLjE1cyBpbmZpbml0ZX0KQGtleWZyYW1lcyBzaGlte3Rve3RyYW5zZm9ybTp0cmFuc2xhdGVY"
    "KDEwMCUpfX0KCi8qIDE5IMK3IEZlYXR1cmUgc3RyaXAgKi8KLmZlYXRze2Rpc3BsYXk6Z3JpZDtn"
    "cmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KDMsMWZyKTtnYXA6OHB4O21hcmdpbi1ib3R0b206"
    "MTJweH0KLmZlYXR7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigt"
    "LWxpbmUpO2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO3BhZGRpbmc6MTFweCA5cHg7dGV4dC1hbGln"
    "bjpjZW50ZXI7CiAgdHJhbnNpdGlvbjp0cmFuc2Zvcm0gLjI1cyBjdWJpYy1iZXppZXIoLjIyLDEs"
    "LjM2LDEpfQouZmVhdDpob3Zlcnt0cmFuc2Zvcm06dHJhbnNsYXRlWSgtM3B4KX0KLmZlYXQgc3Zn"
    "e3dpZHRoOjE3cHg7aGVpZ2h0OjE3cHg7Y29sb3I6dmFyKC0tYWNjKX0KLmZlYXQgYntkaXNwbGF5"
    "OmJsb2NrO2ZvbnQtc2l6ZTouNjhyZW07bWFyZ2luLXRvcDo1cHh9Ci5mZWF0IHNtYWxse2Rpc3Bs"
    "YXk6YmxvY2s7Zm9udC1zaXplOi42cmVtO2NvbG9yOnZhcigtLXR4Mil9CgovKiAwMyDCtyBXb3Jr"
    "cyB3aGVlbCDigJQgcm90YXRpbmcgZGVhbCBkaWFsICovCi53aGVlbHtwb3NpdGlvbjpyZWxhdGl2"
    "ZTtoZWlnaHQ6MTE4cHg7bWFyZ2luOjJweCAwIDEycHg7b3ZlcmZsb3c6aGlkZGVuO2JvcmRlci1y"
    "YWRpdXM6dmFyKC0tcik7CiAgYm9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5k"
    "OnZhcigtLWNhcmQpfQoud2hlZWwgLnJpbmd7cG9zaXRpb246YWJzb2x1dGU7cmlnaHQ6LTUycHg7"
    "dG9wOjUwJTt3aWR0aDoxNzZweDtoZWlnaHQ6MTc2cHg7bWFyZ2luLXRvcDotODhweDtib3JkZXIt"
    "cmFkaXVzOjUwJTsKICBib3JkZXI6MXB4IGRhc2hlZCB2YXIoLS1saW5lKTthbmltYXRpb246c3Bp"
    "biAyNnMgbGluZWFyIGluZmluaXRlfQoud2hlZWwgLnJpbmcgYntwb3NpdGlvbjphYnNvbHV0ZTts"
    "ZWZ0OjUwJTt0b3A6NTAlO3dpZHRoOjUwcHg7aGVpZ2h0OjI0cHg7bWFyZ2luOi0xMnB4IDAgMCAt"
    "MjVweDtib3JkZXItcmFkaXVzOjk5cHg7CiAgZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRl"
    "cjtmb250LXNpemU6LjYycmVtO2ZvbnQtd2VpZ2h0OjgwMDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQy"
    "KTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2NvbG9yOnZhcigtLXR4Mil9Ci53aGVlbCAu"
    "Y2Fwe3Bvc2l0aW9uOmFic29sdXRlO2luc2V0OjA7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9u"
    "OmNvbHVtbjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO3BhZGRpbmc6MCAxNnB4OwogIGJhY2tncm91"
    "bmQ6bGluZWFyLWdyYWRpZW50KDkwZGVnLHZhcigtLWNhcmQpIDAgNTglLHRyYW5zcGFyZW50KX0K"
    "LndoZWVsIC5jYXA+KnttYXgtd2lkdGg6NjIlfQoud2hlZWwgLmNhcCBlbXtmb250LXN0eWxlOm5v"
    "cm1hbDtmb250LXNpemU6LjZyZW07bGV0dGVyLXNwYWNpbmc6LjE4ZW07dGV4dC10cmFuc2Zvcm06"
    "dXBwZXJjYXNlO2NvbG9yOnZhcigtLWFjYyk7Zm9udC13ZWlnaHQ6ODAwfQoud2hlZWwgLmNhcCBi"
    "e2ZvbnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6MS4wNXJlbTtmb250LXdlaWdodDo4"
    "MDB9CkBrZXlmcmFtZXMgc3Bpbnt0b3t0cmFuc2Zvcm06cm90YXRlKDM2MGRlZyl9fQoKLyogc3Vj"
    "Y2VzcyBidXJzdCAqLwojZnh7cG9zaXRpb246Zml4ZWQ7aW5zZXQ6MDt6LWluZGV4OjE1MDtkaXNw"
    "bGF5Om5vbmU7cGxhY2UtaXRlbXM6Y2VudGVyO3BvaW50ZXItZXZlbnRzOm5vbmV9CiNmeC5vbntk"
    "aXNwbGF5OmdyaWR9CiNmeCAucmluZ3t3aWR0aDo4MnB4O2hlaWdodDo4MnB4O2JvcmRlci1yYWRp"
    "dXM6NTAlO2JvcmRlcjoycHggc29saWQgdmFyKC0tb2spO2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVt"
    "czpjZW50ZXI7CiAgY29sb3I6dmFyKC0tb2spO2FuaW1hdGlvbjpidXJzdCAuNzVzIGN1YmljLWJl"
    "emllciguMiwxLjMsLjQsMSl9CiNmeCAucmluZyBzdmd7d2lkdGg6MzRweDtoZWlnaHQ6MzRweH0K"
    "I2Z4IC53YXZle3Bvc2l0aW9uOmFic29sdXRlO3dpZHRoOjgycHg7aGVpZ2h0OjgycHg7Ym9yZGVy"
    "LXJhZGl1czo1MCU7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1vayk7YW5pbWF0aW9uOndhdmUgLjlz"
    "IGVhc2Utb3V0IGZvcndhcmRzfQpAa2V5ZnJhbWVzIGJ1cnN0e2Zyb217dHJhbnNmb3JtOnNjYWxl"
    "KC40KTtvcGFjaXR5OjB9dG97dHJhbnNmb3JtOnNjYWxlKDEpO29wYWNpdHk6MX19CkBrZXlmcmFt"
    "ZXMgd2F2ZXt0b3t0cmFuc2Zvcm06c2NhbGUoMy40KTtvcGFjaXR5OjB9fQoKLyogbnVtYmVyIHRp"
    "Y2tlciAqLwoudGlja3tmb250LXZhcmlhbnQtbnVtZXJpYzp0YWJ1bGFyLW51bXN9CgpAbWVkaWEo"
    "cHJlZmVycy1yZWR1Y2VkLW1vdGlvbjpyZWR1Y2Upeyp7YW5pbWF0aW9uOm5vbmUhaW1wb3J0YW50"
    "O3RyYW5zaXRpb246bm9uZSFpbXBvcnRhbnR9fQoKLnBjYXJkOmhvdmVyLC5yb3c6aG92ZXJ7Ym9y"
    "ZGVyLWNvbG9yOnJnYmEoMTM5LDEyMywyNTUsLjQ1KX0uaGVybzphZnRlcntjb250ZW50OicnO3Bv"
    "c2l0aW9uOmFic29sdXRlO3JpZ2h0Oi0zMHB4O3RvcDotMzBweDt3aWR0aDoxNTBweDtoZWlnaHQ6"
    "MTUwcHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3VuZDpyYWRpYWwtZ3JhZGllbnQoY2lyY2xl"
    "LHJnYmEoNjMsMjE2LDE5OCwuMzUpLHRyYW5zcGFyZW50IDcwJSl9Cjwvc3R5bGU+CjwvaGVhZD4K"
    "PGJvZHk+Cgo8Y2FudmFzIGlkPSJrZyI+PC9jYW52YXM+CjxkaXYgaWQ9InNwbGFzaCI+PGRpdiBj"
    "bGFzcz0ibWFyayIgaWQ9InNwbWFyayI+WlQ8L2Rpdj48ZGl2IGNsYXNzPSJkb3RncmlkIiBpZD0i"
    "ZGciPjwvZGl2PgogIDxoNCBpZD0ic3BuYW1lIj5ab3JvIFRHIFN0b3JlPC9oND48c21hbGwgaWQ9"
    "InNwdGFnIj5QcmVtaXVtIGFjY291bnRzPC9zbWFsbD48L2Rpdj4KPHN2ZyB3aWR0aD0iMCIgaGVp"
    "Z2h0PSIwIiBzdHlsZT0icG9zaXRpb246YWJzb2x1dGUiPjxkZWZzPgogIDxmaWx0ZXIgaWQ9Imxn"
    "IiB4PSIwJSIgeT0iMCUiIHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiIGNvbG9yLWludGVycG9s"
    "YXRpb24tZmlsdGVycz0ic1JHQiI+CiAgICA8ZmVUdXJidWxlbmNlIHR5cGU9ImZyYWN0YWxOb2lz"
    "ZSIgYmFzZUZyZXF1ZW5jeT0iMC4wNSAwLjA1IiBudW1PY3RhdmVzPSIxIiBzZWVkPSIxIiByZXN1"
    "bHQ9InQiLz4KICAgIDxmZUdhdXNzaWFuQmx1ciBpbj0idCIgc3RkRGV2aWF0aW9uPSIyIiByZXN1"
    "bHQ9ImIiLz4KICAgIDxmZURpc3BsYWNlbWVudE1hcCBpbj0iU291cmNlR3JhcGhpYyIgaW4yPSJi"
    "IiBzY2FsZT0iNzAiIHhDaGFubmVsU2VsZWN0b3I9IlIiIHlDaGFubmVsU2VsZWN0b3I9IkIiLz4K"
    "ICA8L2ZpbHRlcj48L2RlZnM+PC9zdmc+CjxkaXYgaWQ9ImZ4Ij48ZGl2IGNsYXNzPSJ3YXZlIj48"
    "L2Rpdj48ZGl2IGNsYXNzPSJyaW5nIj48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9u"
    "ZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMi40IiBzdHJva2UtbGluZWNh"
    "cD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yMCA2IDkgMTdsLTUt"
    "NSIvPjwvc3ZnPjwvZGl2PjwvZGl2Pgo8ZGl2IGlkPSJzdyI+PGVtPlByZXZpZXc8L2VtPgogIDxi"
    "dXR0b24gZGF0YS1zdz0iYXBwIiBjbGFzcz0ib24iPlN0b3JlPC9idXR0b24+CiAgPGJ1dHRvbiBk"
    "YXRhLXN3PSJhZG1pbiI+QWRtaW48L2J1dHRvbj4KPC9kaXY+Cgo8ZGl2IGlkPSJ2aWV3LWFwcCI+"
    "CjxkaXYgaWQ9InNoZWxsIj4KICA8aGVhZGVyIGlkPSJ0b3BiYXIiPgogICAgPGRpdiBjbGFzcz0i"
    "bG9nbyI+WlQ8L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJyYW5kIj48YiBpZD0iYm5hbWUiPlpvcm8g"
    "VEcgU3RvcmU8L2I+PHNwYW4gaWQ9ImJ0YWciPlByZW1pdW0gYWNjb3VudHM8L3NwYW4+PC9kaXY+"
    "CiAgICA8YnV0dG9uIGNsYXNzPSJpY29uY2hpcCBoaWRkZW4iIGlkPSJjYXJ0Y2hpcCIgdGl0bGU9"
    "IkNhcnQiPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJl"
    "bnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1s"
    "aW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik02IDIgMyA2djE0YTIgMiAwIDAgMCAyIDJoMTRhMiAy"
    "IDAgMCAwIDItMlY2bC0zLTR6Ii8+PHBhdGggZD0iTTMgNmgxOCIvPjxwYXRoIGQ9Ik0xNiAxMGE0"
    "IDQgMCAwIDEtOCAwIi8+PC9zdmc+PGkgaWQ9ImNhcnRuIj4wPC9pPjwvYnV0dG9uPgogICAgPGJ1"
    "dHRvbiBjbGFzcz0iYmFsY2hpcCIgZGF0YS1nbz0id2FsbGV0Ij48c3BhbiBjbGFzcz0icGx1cyI+"
    "Kzwvc3Bhbj7igrk8c3BhbiBpZD0iYmFsIiBjbGFzcz0idGljayI+MjQ4LjUwPC9zcGFuPjwvYnV0"
    "dG9uPgogIDwvaGVhZGVyPgogIDxtYWluIGlkPSJzdGFnZSI+PC9tYWluPgo8L2Rpdj4KPG5hdiBp"
    "ZD0idGFicyI+PGRpdiBjbGFzcz0iZG9jayI+PHNwYW4gY2xhc3M9ImRwaWxsIiBpZD0iZHBpbGwi"
    "Pjwvc3Bhbj4KICA8YnV0dG9uIGRhdGEtdGFiPSJzaG9wIiBjbGFzcz0ib24iPjxzdmcgdmlld0Jv"
    "eD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdp"
    "ZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxw"
    "YXRoIGQ9Ik02IDIgMyA2djE0YTIgMiAwIDAgMCAyIDJoMTRhMiAyIDAgMCAwIDItMlY2bC0zLTR6"
    "Ii8+PHBhdGggZD0iTTMgNmgxOCIvPjxwYXRoIGQ9Ik0xNiAxMGE0IDQgMCAwIDEtOCAwIi8+PC9z"
    "dmc+PHNwYW4gZGF0YS1uYXY9InNob3AiPlNob3A8L3NwYW4+PC9idXR0b24+CiAgPGJ1dHRvbiBk"
    "YXRhLXRhYj0ib3JkZXJzIj48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ry"
    "b2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5k"
    "IiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMjEgOGEyIDIgMCAwIDAtMS0xLjcz"
    "bC03LTRhMiAyIDAgMCAwLTIgMGwtNyA0QTIgMiAwIDAgMCAzIDh2OGEyIDIgMCAwIDAgMSAxLjcz"
    "bDcgNGEyIDIgMCAwIDAgMiAwbDctNEEyIDIgMCAwIDAgMjEgMTZaIi8+PHBhdGggZD0ibTMuMyA3"
    "IDguNyA1IDguNy01Ii8+PHBhdGggZD0iTTEyIDIyVjEyIi8+PC9zdmc+PHNwYW4gZGF0YS1uYXY9"
    "Im9yZGVycyI+T3JkZXJzPC9zcGFuPjwvYnV0dG9uPgogIDxidXR0b24gZGF0YS10YWI9ImVhcm4i"
    "Pjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xv"
    "ciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9p"
    "bj0icm91bmQiPjxwYXRoIGQ9Ik0xMyAyIDMgMTRoOWwtMSA4IDEwLTEyaC05eiIvPjwvc3ZnPjxz"
    "cGFuIGRhdGEtbmF2PSJlYXJuIj5FYXJuPC9zcGFuPjwvYnV0dG9uPgogIDxidXR0b24gZGF0YS10"
    "YWI9IndhbGxldCI+PHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0i"
    "Y3VycmVudENvbG9yIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ry"
    "b2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTE5IDdWNWEyIDIgMCAwIDAtMi0ySDVhMiAy"
    "IDAgMCAwIDAgNGgxNWEyIDIgMCAwIDEgMiAydjlhMiAyIDAgMCAxLTIgMkg1YTIgMiAwIDAgMS0y"
    "LTJWNSIvPjxwYXRoIGQ9Ik0xOCAxMmguMDEiLz48L3N2Zz48c3BhbiBkYXRhLW5hdj0id2FsbGV0"
    "Ij5XYWxsZXQ8L3NwYW4+PC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXRhYj0ieW91Ij48c3ZnIHZp"
    "ZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9r"
    "ZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5k"
    "Ij48cGF0aCBkPSJNMTkgMjF2LTJhNCA0IDAgMCAwLTQtNEg5YTQgNCAwIDAgMC00IDR2MiIvPjxj"
    "aXJjbGUgY3g9IjEyIiBjeT0iNyIgcj0iNCIvPjwvc3ZnPjxzcGFuIGRhdGEtbmF2PSJ5b3UiPllv"
    "dTwvc3Bhbj48L2J1dHRvbj4KPC9kaXY+PC9uYXY+CjwvZGl2PgoKPGRpdiBpZD0idmlldy1hZG1p"
    "biIgY2xhc3M9ImhpZGRlbiI+PGRpdiBpZD0ic2hlbGwiPgogIDxoZWFkZXIgaWQ9InRvcGJhciI+"
    "CiAgICA8ZGl2IGNsYXNzPSJsb2dvIj48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9u"
    "ZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9"
    "InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMjAgMTNjMCA1LTMuNSA3"
    "LjUtNy42NiA4Ljk1YTEgMSAwIDAgMS0uNjctLjAxQzcuNSAyMC41IDQgMTggNCAxM1Y2YTEgMSAw"
    "IDAgMSAxLTFjMiAwIDQuNS0xLjIgNi4yNC0yLjcyYTEuMTcgMS4xNyAwIDAgMSAxLjUyIDBDMTQu"
    "NTEgMy44MSAxNyA1IDE5IDVhMSAxIDAgMCAxIDEgMXoiLz48L3N2Zz48L2Rpdj4KICAgIDxkaXYg"
    "Y2xhc3M9ImJyYW5kIj48Yj5BZG1pbiBDb25zb2xlPC9iPjxzcGFuIGlkPSJhYm5hbWUiPlpvcm8g"
    "VEcgU3RvcmU8L3NwYW4+PC9kaXY+CiAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+PGkgY2xhc3M9"
    "ImRvdCI+PC9pPkxpdmU8L3NwYW4+CiAgPC9oZWFkZXI+CiAgPGRpdiBjbGFzcz0ic3VibmF2IiBp"
    "ZD0iYXN1YiI+PHNwYW4gY2xhc3M9InZwaWxsIiBpZD0idnBpbGwiPjwvc3Bhbj4KICAgIDxidXR0"
    "b24gZGF0YS1hPSJkYXNoIiBjbGFzcz0ib24iPkRhc2hib2FyZDwvYnV0dG9uPgogICAgPGJ1dHRv"
    "biBkYXRhLWE9ImNoYW5uZWxzIj5DaGFubmVsczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9"
    "ImxvZ3MiPkxvZ3M8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJwYXkiPkF1dG8tUGF5PC9i"
    "dXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0iY29udGVudCI+Q29udGVudDwvYnV0dG9uPgogICAg"
    "PGJ1dHRvbiBkYXRhLWE9ImZlYXR1cmVzIj5GZWF0dXJlczwvYnV0dG9uPgogICAgPGJ1dHRvbiBk"
    "YXRhLWE9Im1hcmtldCI+TWFya2V0aW5nPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0iYW5h"
    "bHl0aWNzIj5BbmFseXRpY3M8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJzZWN1cml0eSI+"
    "U2VjdXJpdHk8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJzdG9jayI+U3RvY2s8L2J1dHRv"
    "bj4KICAgIDxidXR0b24gZGF0YS1hPSJvcmRlcnMiPk9yZGVyczwvYnV0dG9uPgogICAgPGJ1dHRv"
    "biBkYXRhLWE9InVzZXJzIj5Vc2VyczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9InNldHRp"
    "bmdzIj5TZXR0aW5nczwvYnV0dG9uPgogIDwvZGl2PgogIDxtYWluIGlkPSJhc3RhZ2UiPjwvbWFp"
    "bj4KPC9kaXY+PC9kaXY+Cgo8ZGl2IGlkPSJzaGVldCI+PGRpdiBjbGFzcz0iYmciIGRhdGEtY2xv"
    "c2U+PC9kaXY+PGRpdiBjbGFzcz0icGFuZWwiIGlkPSJwYW5lbCI+PC9kaXY+PC9kaXY+CjxkaXYg"
    "Y2xhc3M9InRvYXN0IiBpZD0idG9hc3QiPjwvZGl2PgoKPHNjcmlwdD4KLyog4pSA4pSAIGxpdmUt"
    "bW9kZSBzdGF0ZSwgZGVjbGFyZWQgZmlyc3Qgc28gbm90aGluZyBjYW4gcmVhZCBpdCB0b28gZWFy"
    "bHkg4pSA4pSAICovCnZhciBMSVZFPWZhbHNlLExPQ0tFRD1mYWxzZSxNRT17YWRtaW46ZmFsc2Us"
    "b3duZXI6ZmFsc2UscGVybXM6e319Owp2YXIgVEc9d2luZG93LlRlbGVncmFtJiZ3aW5kb3cuVGVs"
    "ZWdyYW0uV2ViQXBwfHxudWxsOwpmdW5jdGlvbiByZWFkSW5pdERhdGEoKXsKICB0cnl7aWYoVEcm"
    "JlRHLmluaXREYXRhJiZURy5pbml0RGF0YS5sZW5ndGgpcmV0dXJuIFRHLmluaXREYXRhO31jYXRj"
    "aChlKXt9CiAgLyogVGVsZWdyYW0gYWxzbyBwdXRzIGl0IGluIHRoZSBVUkwsIHNvIHRoZSBhcHAg"
    "c3RpbGwgd29ya3MgaWYgdGhlIFNESyBpcyBibG9ja2VkICovCiAgdHJ5ewogICAgY29uc3QgbT0v"
    "WyMmP110Z1dlYkFwcERhdGE9KFteJl0qKS8uZXhlYyhsb2NhdGlvbi5oYXNofHwiIik7CiAgICBp"
    "ZihtJiZtWzFdKXJldHVybiBkZWNvZGVVUklDb21wb25lbnQobVsxXSk7CiAgfWNhdGNoKGUpe30K"
    "ICB0cnl7CiAgICBjb25zdCBxPW5ldyBVUkxTZWFyY2hQYXJhbXMobG9jYXRpb24uc2VhcmNoKTsK"
    "ICAgIGNvbnN0IHY9cS5nZXQoInRnV2ViQXBwRGF0YSIpOwogICAgaWYodilyZXR1cm4gdjsKICB9"
    "Y2F0Y2goZSl7fQogIHRyeXsKICAgIGNvbnN0IHNhdmVkPXNlc3Npb25TdG9yYWdlLmdldEl0ZW0o"
    "InRnSW5pdCIpOwogICAgaWYoc2F2ZWQpcmV0dXJuIHNhdmVkOwogIH1jYXRjaChlKXt9CiAgcmV0"
    "dXJuICIiOwp9CmZ1bmN0aW9uIGluaXRpYWxzKG5hbWUpewogIGNvbnN0IHc9KG5hbWV8fCIiKS50"
    "cmltKCkuc3BsaXQoL1xzKy8pLmZpbHRlcihCb29sZWFuKTsKICBpZighdy5sZW5ndGgpcmV0dXJu"
    "ICJTVCI7CiAgcmV0dXJuICgod1swXVswXXx8IiIpKyh3Lmxlbmd0aD4xPyh3WzFdWzBdfHwiIik6"
    "KHdbMF1bMV18fCIiKSkpLnRvVXBwZXJDYXNlKCk7Cn0KZnVuY3Rpb24gcGFpbnRCcmFuZChuYW1l"
    "LHRhZyl7CiAgaWYobmFtZSl7CiAgICBjb25zdCBuPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJz"
    "cG5hbWUiKTsgaWYobiluLnRleHRDb250ZW50PW5hbWU7CiAgICBjb25zdCBtPWRvY3VtZW50Lmdl"
    "dEVsZW1lbnRCeUlkKCJzcG1hcmsiKTsgaWYobSltLnRleHRDb250ZW50PWluaXRpYWxzKG5hbWUp"
    "OwogICAgY29uc3QgYj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYm5hbWUiKTsgaWYoYiliLnRl"
    "eHRDb250ZW50PW5hbWU7CiAgICBjb25zdCBhYj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYWJu"
    "YW1lIik7IGlmKGFiKWFiLnRleHRDb250ZW50PW5hbWU7CiAgICBkb2N1bWVudC50aXRsZT1uYW1l"
    "OwogIH0KICBpZih0YWcpewogICAgY29uc3QgdD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3B0"
    "YWciKTsgaWYodCl0LnRleHRDb250ZW50PXRhZzsKICAgIGNvbnN0IGJ0PWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJidGFnIik7IGlmKGJ0KWJ0LnRleHRDb250ZW50PXRhZzsKICB9Cn0KdHJ5ewog"
    "IGNvbnN0IGNuPWxvY2FsU3RvcmFnZS5nZXRJdGVtKCJzdG9yZU5hbWUiKSwgY3Q9bG9jYWxTdG9y"
    "YWdlLmdldEl0ZW0oInN0b3JlVGFnIik7CiAgaWYoY258fGN0KXBhaW50QnJhbmQoY24sY3QpOwp9"
    "Y2F0Y2goZSl7fQp2YXIgSU5JVD1yZWFkSW5pdERhdGEoKTsKdHJ5e2lmKElOSVQpc2Vzc2lvblN0"
    "b3JhZ2Uuc2V0SXRlbSgidGdJbml0IixJTklUKTt9Y2F0Y2goZSl7fQpmdW5jdGlvbiByZWFkQXBw"
    "S2V5KCl7CiAgdHJ5ewogICAgY29uc3QgcT1uZXcgVVJMU2VhcmNoUGFyYW1zKGxvY2F0aW9uLnNl"
    "YXJjaCk7CiAgICBpZihxLmdldCgiayIpKXJldHVybiBxLmdldCgiayIpOwogICAgY29uc3QgbT0v"
    "WyMmP11rPShbXiZdKikvLmV4ZWMobG9jYXRpb24uaGFzaHx8IiIpOwogICAgaWYobSYmbVsxXSly"
    "ZXR1cm4gZGVjb2RlVVJJQ29tcG9uZW50KG1bMV0pOwogICAgcmV0dXJuIHNlc3Npb25TdG9yYWdl"
    "LmdldEl0ZW0oImFwcEtleSIpfHwiIjsKICB9Y2F0Y2goZSl7cmV0dXJuICIiO30KfQp2YXIgQVBQ"
    "S0VZPXJlYWRBcHBLZXkoKTsKdHJ5e2lmKEFQUEtFWSlzZXNzaW9uU3RvcmFnZS5zZXRJdGVtKCJh"
    "cHBLZXkiLEFQUEtFWSk7fWNhdGNoKGUpe30KLyogZGVtbyBkYXRhIG9ubHkg4oCUIG5vIHRva2Vu"
    "cywgbm8gVVBJIElELCBubyBHbWFpbCBjcmVkZW50aWFscyAqLwpsZXQgQ0ZHPXtuYW1lOiJab3Jv"
    "IFRHIFN0b3JlIix0YWc6IlByZW1pdW0gYWNjb3VudHMiLGN1cjoi4oK5In07Ci8qIOKUgOKUgCBF"
    "ZGl0YWJsZSBjb3B5IOKAlCBldmVyeSBzdHJpbmcgYmVsb3cgaXMgZWRpdGFibGUgaW4gQWRtaW4g"
    "4pa4IENvbnRlbnQg4pSA4pSAICovCmNvbnN0IENNUz17CiBzdG9yZU5hbWU6Ilpvcm8gVEcgU3Rv"
    "cmUiLCBzdG9yZVRhZzoiUHJlbWl1bSBhY2NvdW50cyIsCiBlbXB0eVRpdGxlOiJSZXN0b2NraW5n"
    "IiwgZW1wdHlCb2R5OiJFdmVyeXRoaW5nIGp1c3Qgc29sZCBvdXQuIEZyZXNoIGFjY291bnRzIGxh"
    "bmQgaGVyZSBzaG9ydGx5IOKAlCB0dXJuIG9uIGFuIGFsZXJ0IGFuZCB5b3Ugd2lsbCBoZWFyIGZp"
    "cnN0LiIsCiB0aWNrMToiPGI+SW5zdGFudCBkZWxpdmVyeTwvYj4gb24gZXZlcnkgY291bnRyeSIs"
    "IHRpY2syOiJUb3AtdXBzIDxiPmNvbmZpcm1lZCBhdXRvbWF0aWNhbGx5PC9iPiIsCiB0aWNrMzoi"
    "RmFpbGVkIGxvZ2luPyA8Yj5BdXRvIHJlZnVuZDwvYj4iLCB0aWNrNDoiRGFpbHkgY2hlY2staW4g"
    "PGI+4oK5MC40MDwvYj4iLAogaGVyb0tpY2s6IkxpdmUgaW52ZW50b3J5IiwgaGVyb0gxOiJBY2Nv"
    "dW50cyw8YnI+ZGVsaXZlcmVkIGluc3RhbnRseS4iLAogaGVyb1N1YjoiSW5zdGFudCBkZWxpdmVy"
    "eSwgT1RQIGluLWFwcCwgd2FsbGV0IHRvcC11cHMgdmVyaWZpZWQgYXV0b21hdGljYWxseS4ge259"
    "IGNvdW50cmllcyBsaXZlLiIsCiBoZXJvQnRuMToiQnJvd3NlIFN0b2NrIiwgaGVyb0J0bjI6IkFk"
    "ZCBGdW5kcyIsCiBmZWF0MToifjhzIiwgZmVhdDFzOiJkZWxpdmVyeSIsIGZlYXQyOiJBdXRvIiwg"
    "ZmVhdDJzOiJyZWZ1bmRzIiwgZmVhdDM6IlVQSSIsIGZlYXQzczoiYXV0by12ZXJpZnkiLAogZmxh"
    "c2hQaWxsOiJGbGFzaCDiiJIyMCUiLCBmbGFzaFRleHQ6IkVuZHMgaW4iLCBmbGFzaEJ0bjoiVmll"
    "dyIsCiB3aGVlbEtpY2s6IlJvdGF0aW5nIGRlYWxzIiwgd2hlZWxUaXRsZToiVXAgdG8gMjglIG9m"
    "ZiIsIHdoZWVsV29yZHM6IklOU1RBTlQsIEFVVE8tVkVSSUZZLCBSRUZVTkRTLCAyNMOXNyIsCiBz"
    "aG9wSGVhZDoiSW4gU3RvY2siLCBzaG9wU29ydDoiU29ydCIsIHNlYXJjaFBoOiJTZWFyY2ggY291"
    "bnRyeSBvciBjb2Rl4oCmIiwgYWxlcnRzQnRuOiJBbGVydHMiLAogd2FsS2ljazoiQmFsYW5jZSIs"
    "IHdhbFN1YjoiUGF5IGJ5IFVQSSDigJQgdGFwIFZlcmlmeSBhZnRlciBwYXlpbmcgYW5kIHlvdXIg"
    "d2FsbGV0IGlzIGNyZWRpdGVkIGluIHNlY29uZHMuIiwKIHdhbEFkZDoiQWRkIEZ1bmRzIiwgcGF5"
    "VGl0bGU6IlBheW1lbnQgRGV0YWlscyIsIHBheVN1YjoiQWRtaW4gc2V0cyB0aGUgVVBJIGhhbmRs"
    "ZSDigJQgbm90aGluZyBpcyBoYXJkLWNvZGVkIGluIHRoZSBhcHAuIiwKIHBheVByZWZpeDoiUEFZ"
    "IiwgcGF5V2luZG93OiI1IiwKIHNoZWV0VGl0bGU6IkFkZCIsIHNoZWV0U3ViOiJQYXkgdGhlIGV4"
    "YWN0IGFtb3VudCwgdGhlbiB0YXAgVmVyaWZ5IFBheW1lbnQuIFRoZSByZXF1ZXN0IGV4cGlyZXMg"
    "aW4ge219IG1pbnV0ZXMuIiwKIHBheUJ0bjoiUGF5IE5vdyIsIHZlcmlmeUJ0bjoiVmVyaWZ5IFBh"
    "eW1lbnQiLCB1dHJCdG46Ik5vdCBkZXRlY3RlZD8gU3VibWl0IFVUUiIsCiBzdGVwMToiUGF5bWVu"
    "dCByZXF1ZXN0IGNyZWF0ZWQiLCBzdGVwMjoiUGF5IHRoZSBleGFjdCBhbW91bnQgaW4gYW55IFVQ"
    "SSBhcHAiLAogc3RlcDM6IlRhcCBWZXJpZnkg4oCUIHdlIGNvbmZpcm0geW91ciBwYXltZW50Iiwg"
    "c3RlcDQ6IldhbGxldCBjcmVkaXRlZCIsCiBtc2dDaGVja2luZzoiQ2hlY2tpbmcgeW91ciBwYXlt"
    "ZW504oCmIiwgbXNnT2s6IlZlcmlmaWVkIOKAlCB3YWxsZXQgY3JlZGl0ZWQiLAogbXNnTm9uZToi"
    "Tm8gcGF5bWVudCBmb3VuZCB5ZXQuIFBheSBmaXJzdCwgdGhlbiB0YXAgVmVyaWZ5LiIsIG1zZ0V4"
    "cGlyZWQ6IlJlcXVlc3QgZXhwaXJlZCDigJQgY3JlYXRlIGEgbmV3IG9uZS4iLAogbmF2U2hvcDoi"
    "U2hvcCIsIG5hdk9yZGVyczoiT3JkZXJzIiwgbmF2RWFybjoiRWFybiIsIG5hdldhbGxldDoiV2Fs"
    "bGV0IiwgbmF2WW91OiJZb3UiCn07CmNvbnN0IENNUzA9T2JqZWN0LmFzc2lnbih7fSxDTVMpOwpj"
    "b25zdCBDTVNNRVRBPVsKIFsiU3RvcmUgaGVhZGVyIixbWyJzdG9yZU5hbWUiLCJTdG9yZSBuYW1l"
    "Il0sWyJzdG9yZVRhZyIsIlRhZ2xpbmUiXV1dLAogWyJTY3JvbGxpbmcgdGlja2VyIixbWyJ0aWNr"
    "MSIsIlRpY2tlciAxIl0sWyJ0aWNrMiIsIlRpY2tlciAyIl0sWyJ0aWNrMyIsIlRpY2tlciAzIl0s"
    "WyJ0aWNrNCIsIlRpY2tlciA0Il1dXSwKIFsiSGVybyBzZWN0aW9uIixbWyJoZXJvS2ljayIsIlNt"
    "YWxsIGxhYmVsIGFib3ZlIHRpdGxlIl0sWyJoZXJvSDEiLCJCaWcgdGl0bGUiXSxbImhlcm9TdWIi"
    "LCJQYXJhZ3JhcGggdW5kZXIgdGhlIHRpdGxlIl0sWyJoZXJvQnRuMSIsIkxlZnQgYnV0dG9uIl0s"
    "WyJoZXJvQnRuMiIsIlJpZ2h0IGJ1dHRvbiJdXV0sCiBbIkZlYXR1cmUgYmFkZ2VzIixbWyJmZWF0"
    "MSIsIkJhZGdlIDEgdmFsdWUiXSxbImZlYXQxcyIsIkJhZGdlIDEgY2FwdGlvbiJdLFsiZmVhdDIi"
    "LCJCYWRnZSAyIHZhbHVlIl0sWyJmZWF0MnMiLCJCYWRnZSAyIGNhcHRpb24iXSxbImZlYXQzIiwi"
    "QmFkZ2UgMyB2YWx1ZSJdLFsiZmVhdDNzIiwiQmFkZ2UgMyBjYXB0aW9uIl1dXSwKIFsiRmxhc2gg"
    "c2FsZSBzdHJpcCIsW1siZmxhc2hQaWxsIiwiUGlsbCB0ZXh0Il0sWyJmbGFzaFRleHQiLCJDb3Vu"
    "dGRvd24gbGFiZWwiXSxbImZsYXNoQnRuIiwiQnV0dG9uIl1dXSwKIFsiUm90YXRpbmcgZGVhbHMg"
    "Y2FyZCIsW1sid2hlZWxLaWNrIiwiU21hbGwgbGFiZWwiXSxbIndoZWVsVGl0bGUiLCJIZWFkbGlu"
    "ZSJdLFsid2hlZWxXb3JkcyIsIk1hcnF1ZWUgd29yZHMgKGNvbW1hIHNlcGFyYXRlZCkiXV1dLAog"
    "WyJTaG9wIGxpc3QiLFtbInNob3BIZWFkIiwiU2VjdGlvbiBoZWFkaW5nIl0sWyJzaG9wU29ydCIs"
    "IlNvcnQgbGluayJdLFsic2VhcmNoUGgiLCJTZWFyY2ggcGxhY2Vob2xkZXIiXSxbImFsZXJ0c0J0"
    "biIsIkFsZXJ0cyBidXR0b24iXV1dLAogWyJXYWxsZXQgc2NyZWVuIixbWyJ3YWxLaWNrIiwiTGFi"
    "ZWwgYWJvdmUgYmFsYW5jZSJdLFsid2FsU3ViIiwiQmFsYW5jZSBkZXNjcmlwdGlvbiJdLFsid2Fs"
    "QWRkIiwiQWRkIGZ1bmRzIGJ1dHRvbiJdLFsicGF5VGl0bGUiLCJQYXltZW50IGNhcmQgdGl0bGUi"
    "XSxbInBheVN1YiIsIlBheW1lbnQgY2FyZCBkZXNjcmlwdGlvbiJdXV0sCiBbIlBheW1lbnQgc2hl"
    "ZXQiLFtbInNoZWV0VGl0bGUiLCJTaGVldCB0aXRsZSBwcmVmaXgiXSxbInNoZWV0U3ViIiwiU2hl"
    "ZXQgZGVzY3JpcHRpb24gKHttfSA9IG1pbnV0ZXMpIl0sWyJwYXlCdG4iLCJQYXkgYnV0dG9uIl0s"
    "WyJ2ZXJpZnlCdG4iLCJWZXJpZnkgYnV0dG9uIl0sWyJ1dHJCdG4iLCJNYW51YWwgZmFsbGJhY2sg"
    "YnV0dG9uIl0sWyJwYXlQcmVmaXgiLCJSZWZlcmVuY2UgcHJlZml4Il0sWyJwYXlXaW5kb3ciLCJQ"
    "YXltZW50IHdpbmRvdyAobWludXRlcykiXV1dLAogWyJQYXltZW50IHN0ZXBzIixbWyJzdGVwMSIs"
    "IlN0ZXAgMSJdLFsic3RlcDIiLCJTdGVwIDIiXSxbInN0ZXAzIiwiU3RlcCAzIl0sWyJzdGVwNCIs"
    "IlN0ZXAgNCJdXV0sCiBbIlBheW1lbnQgbWVzc2FnZXMiLFtbIm1zZ0NoZWNraW5nIiwiV2hpbGUg"
    "Y2hlY2tpbmciXSxbIm1zZ09rIiwiVmVyaWZpZWQiXSxbIm1zZ05vbmUiLCJOb3RoaW5nIGZvdW5k"
    "Il0sWyJtc2dFeHBpcmVkIiwiRXhwaXJlZCJdXV0sCiBbIkJvdHRvbSBuYXZpZ2F0aW9uIixbWyJu"
    "YXZTaG9wIiwiVGFiIDEiXSxbIm5hdk9yZGVycyIsIlRhYiAyIl0sWyJuYXZFYXJuIiwiVGFiIDMi"
    "XSxbIm5hdldhbGxldCIsIlRhYiA0Il0sWyJuYXZZb3UiLCJUYWIgNSJdXV0KXTsKLyog4pWQ4pWQ"
    "IEZFQVRVUkUgRkxBR1Mg4oCUIGV2ZXJ5IGZlYXR1cmUgYmVsb3cgaXMgb2ZmLWFibGU7IHdoZW4g"
    "b2ZmIG5vdGhpbmcgcmVuZGVycyDilZDilZAgKi8KY29uc3QgRkdST1VQUz1bCiBbIlBheW1lbnRz"
    "ICYgd2FsbGV0IixbCiAgWyJjcnlwdG8iLCJDcnlwdG8gLyBVU0RUIHRvcC11cCIsIkFjY2VwdCBV"
    "U0RULVRSQzIwLCBCVEMgZXRjLiB3aXRoIGF1dG8tY29uZmlybSBmcm9tIHRoZSBleHBsb3Jlci4i"
    "XSwKICBbInVwaVJvdGF0ZSIsIk11bHRpcGxlIFVQSSBJRHMgKyByb3RhdGlvbiIsIlJvdGF0ZSBo"
    "YW5kbGVzIHBlciBwYXltZW50IG9yIGF1dG8tc3dpdGNoIHdoZW4gb25lIGlzIGxpbWl0ZWQuIl0s"
    "CiAgWyJhdXRvUmVmdW5kIiwiQXV0by1yZWZ1bmQgdG8gd2FsbGV0IiwiRGVhZCBhY2NvdW50IGlu"
    "c2lkZSB0aGUgd2FycmFudHkgd2luZG93IGlzIHJlZnVuZGVkIHdpdGhvdXQgc3VwcG9ydC4iXSwK"
    "ICBbInNwbGl0UGF5IiwiU3BsaXQgcGF5bWVudCIsIlBheSBwYXJ0IGZyb20gYmFsYW5jZSBhbmQg"
    "dGhlIHJlc3QgYnkgVVBJIGluIG9uZSBmbG93LiJdLAogIFsibGVkZ2VyIiwiV2FsbGV0IGxlZGdl"
    "ciBzdGF0ZW1lbnQiLCJGdWxsIGNyZWRpdC9kZWJpdCBoaXN0b3J5IHdpdGggcnVubmluZyBiYWxh"
    "bmNlIGFuZCBDU1YgZXhwb3J0LiJdLAogIFsiZGVwTGltaXRzIiwiTWluIC8gbWF4IGRlcG9zaXQg"
    "cnVsZXMiLCJHbG9iYWwgb3IgcGVyLXVzZXIgY2FwcyBvbiBhIHNpbmdsZSB0b3AtdXAuIl0sCiAg"
    "WyJkZXBCb251cyIsIkRlcG9zaXQgYm9udXMgdGllcnMiLCJBZGQg4oK5NTAwIGdldCA1JSBleHRy"
    "YSDigJQgY29uZmlndXJhYmxlIHNsYWJzLCBhdXRvLWFwcGxpZWQuIl0sCiAgWyJ3cm9uZ0FtdCIs"
    "Ildyb25nLWFtb3VudCBoYW5kbGluZyIsIkNyZWRpdCB3aGF0ZXZlciB3YXMgYWN0dWFsbHkgcGFp"
    "ZCBhbmQgZmxhZyB0aGUgbWlzbWF0Y2guIl1dXSwKIFsiQ2F0YWxvZyAmIHNlbGxpbmciLFsKICBb"
    "ImNhcnQiLCJTaG9wcGluZyBjYXJ0IC8gYnVsayBidXkiLCJNaXggY291bnRyaWVzIGluIG9uZSBj"
    "aGVja291dCwgb25lIGNvbmZpcm1hdGlvbiwgb25lIGRlYml0LiJdLAogIFsiYnVsa0Rpc2MiLCJC"
    "dWxrIHB1cmNoYXNlIGRpc2NvdW50IiwiQXV0b21hdGljIHByaWNlIGJyZWFrIGF0IDUgLyAxMCAv"
    "IDI1IHVuaXRzLiJdLAogIFsid2lzaGxpc3QiLCJXaXNobGlzdCAvIGZhdm91cml0ZXMiLCJTYXZl"
    "IGNvdW50cmllcyBhbmQgcmVidXkgaW4gb25lIHRhcC4iXSwKICBbImR5blByaWNlIiwiRHluYW1p"
    "YyBwcmljaW5nIiwiUHJpY2UgbW92ZXMgd2l0aCBzdG9jayBsZXZlbCwgYWdlIHRpZXIgb3IgdGlt"
    "ZSBvZiBkYXkuIl0sCiAgWyJmbGFzaFNhbGUiLCJGbGFzaCBzYWxlIiwiVGltZWQgZGlzY291bnQg"
    "d2l0aCBhIGxpdmUgY291bnRkb3duIG9uIHRoZSBzdG9yZWZyb250LiJdLAogIFsiY291cG9ucyIs"
    "IkNvdXBvbiBjb2RlcyIsIlBlcmNlbnRhZ2Ugb2ZmIGF0IGNoZWNrb3V0IHdpdGggbGltaXRzIGFu"
    "ZCBleHBpcnkuIl0sCiAgWyJ2YXJpYW50cyIsIkFjY291bnQgdmFyaWFudHMiLCJTcGFtLWJsb2Nr"
    "LCAyRkEsIHVzZXJuYW1lLCBtYWlsIOKAlCBmaWx0ZXJhYmxlIGFuZCBwcmljZWQgYXBhcnQuIl0s"
    "CiAgWyJoZWFsdGhDaGVjayIsIlN0b2NrIGhlYWx0aCBjaGVjayIsIlNlc3Npb24gaXMgdmFsaWRh"
    "dGVkIGxpdmUgYmVmb3JlIGl0IGlzIHNvbGQuIl1dXSwKIFsiT3JkZXJzICYgYWZ0ZXItc2FsZSIs"
    "WwogIFsidGltZWxpbmUiLCJPcmRlciBzdGF0dXMgdGltZWxpbmUiLCJQZW5kaW5nLCBkZWxpdmVy"
    "ZWQsIE9UUCByZWNlaXZlZCwgY2xvc2VkIOKAlCB2aXNpYmxlIHRvIGJvdGggc2lkZXMuIl0sCiAg"
    "WyJ3YXJyYW50eSIsIlJlcGxhY2VtZW50IC8gd2FycmFudHkiLCJSZXBvcnQgYSBwcm9ibGVtIGlu"
    "c2lkZSBYIGhvdXJzIGZvciBhbiBhdXRvLXJlcGxhY2VtZW50LiJdLAogIFsicmVkb3dubG9hZCIs"
    "IlJlLWRvd25sb2FkIGRldGFpbHMiLCJGZXRjaCBhIHBhc3Qgb3JkZXIncyBkZXRhaWxzIGFnYWlu"
    "LCByYXRlLWxpbWl0ZWQuIl0sCiAgWyJkaXNwdXRlIiwiRGlzcHV0ZSBmbG93IiwiUmVhc29uIHBp"
    "Y2tlciBwbHVzIGV2aWRlbmNlIGluc3RlYWQgb2YgZnJlZS10ZXh0IHRpY2tldHMuIl0sCiAgWyJz"
    "ZXNzaW9uRmlsZSIsIlNlc3Npb24gZmlsZSBkZWxpdmVyeSIsIlNlbmQgLnNlc3Npb24gLyBKU09O"
    "IGFzIGEgZG9jdW1lbnQgd2l0aCBhIHNlbGYtZGVzdHJ1Y3QgdGltZXIuIl0sCiAgWyJyZWNlaXB0"
    "cyIsIk9yZGVyIHJlY2VpcHRzIiwiQXV0by1nZW5lcmF0ZWQgaW52b2ljZSBwZXIgcHVyY2hhc2Uu"
    "Il1dXSwKIFsiVHJ1c3QgJiBzb2NpYWwgcHJvb2YiLFsKICBbInJldmlld3MiLCJSZXZpZXdzICYg"
    "c3RhciByYXRpbmdzIiwiQnV5ZXIgcmF0ZXMgYWZ0ZXIgZGVsaXZlcnk7IGF2ZXJhZ2VzIHNob3cg"
    "b24gZXZlcnkgY291bnRyeS4iXSwKICBbInNhbGVzRmVlZCIsIkxpdmUgc2FsZXMgZmVlZCIsIlJl"
    "Y2VudCBzYWxlcyBzaG93biBpbi1hcHAgYW5kIGF1dG8tcG9zdGVkIHRvIHlvdXIgY2hhbm5lbC4i"
    "XSwKICBbImxlYWRlcmJvYXJkIiwiTGVhZGVyYm9hcmQiLCJUb3AgYnV5ZXJzIGFuZCByZWZlcnJl"
    "cnMgb2YgdGhlIHdlZWssIHdpdGggYSBwcml6ZS4iXSwKICBbImJ1eWVyQmFkZ2UiLCJWZXJpZmll"
    "ZCBidXllciBiYWRnZSIsIkJhZGdlIGFuZCBwZXJrcyBhZnRlciBOIGNvbXBsZXRlZCBvcmRlcnMu"
    "Il1dXSwKIFsiR3Jvd3RoICYgcmV0ZW50aW9uIixbCiAgWyJyZWZMMiIsIk11bHRpLWxldmVsIHJl"
    "ZmVycmFsIiwiTGV2ZWwgMSBhbmQgbGV2ZWwgMiBjb21taXNzaW9uIGluc3RlYWQgb2YgYSBmbGF0"
    "IHJhdGUuIl0sCiAgWyJyZWZEYXNoIiwiUmVmZXJyYWwgZGFzaGJvYXJkIiwiV2hvIGpvaW5lZCwg"
    "d2hvIGRlcG9zaXRlZCBhbmQgd2hhdCBlYWNoIGludml0ZSBlYXJuZWQuIl0sCiAgWyJzdHJlYWsi"
    "LCJDaGVjay1pbiBzdHJlYWsgcmV3YXJkcyIsIkVzY2FsYXRpbmcgZGFpbHkgcmV3YXJkIHRoYXQg"
    "cmVzZXRzIHdoZW4gYnJva2VuLiJdLAogIFsibG95YWx0eSIsIkxveWFsdHkgcG9pbnRzIiwiRWFy"
    "biBwZXIg4oK5IHNwZW50LCByZWRlZW0gZm9yIGJhbGFuY2Ugb3IgYSBmcmVlIGFjY291bnQuIl0s"
    "CiAgWyJ3aW5iYWNrIiwiUmUtZW5nYWdlbWVudCBETXMiLCJBdXRvIG1lc3NhZ2UgdXNlcnMgaW5h"
    "Y3RpdmUgZm9yIDcgLyAzMCBkYXlzLiJdLAogIFsiYWJhbmRvblJlbWluZCIsIkFiYW5kb25lZC1k"
    "ZXBvc2l0IHJlbWluZGVyIiwiTnVkZ2UgYmVmb3JlIGFuIHVuZmluaXNoZWQgcGF5bWVudCBleHBp"
    "cmVzLiJdLAogIFsic2NoZWRCcm9hZGNhc3QiLCJTY2hlZHVsZWQgJiBzZWdtZW50ZWQgYnJvYWRj"
    "YXN0cyIsIlF1ZXVlIGZvciBsYXRlciwgdGFyZ2V0IGJ1eWVycywgbm9uLWJ1eWVycyBvciBiaWcg"
    "c3BlbmRlcnMuIl1dXSwKIFsiQWRtaW4gJiBvcGVyYXRpb25zIixbCiAgWyJhZG1pblJvbGVzIiwi"
    "QWRtaW4gcm9sZXMiLCJPd25lciAvIG1hbmFnZXIgLyBzdXBwb3J0IHdpdGggc2VwYXJhdGUgcGVy"
    "bWlzc2lvbnMuIl0sCiAgWyJhdWRpdExvZyIsIkF1ZGl0IGxvZyIsIkV2ZXJ5IGFkbWluIGFjdGlv"
    "biBsb2dnZWQgd2l0aCB0aW1lc3RhbXAgYW5kIHVuZG8uIl0sCiAgWyJjb250ZW50RWRpdG9yIiwi"
    "SW4tYm90IGNvbnRlbnQgZWRpdG9yIiwiRWRpdCBldmVyeSBtZXNzYWdlIGFuZCBidXR0b24gd2l0"
    "aG91dCB0b3VjaGluZyBjb2RlLiJdLAogIFsiYXV0b0JhY2t1cCIsIlNjaGVkdWxlZCBhdXRvLWJh"
    "Y2t1cCIsIkRhdGFiYXNlIHRvIGEgcHJpdmF0ZSBjaGFubmVsIGV2ZXJ5IE4gaG91cnMsIG9uZS10"
    "YXAgcmVzdG9yZS4iXSwKICBbImJ1bGtVcGxvYWQiLCJCdWxrIHVwbG9hZCB3aXRoIHZhbGlkYXRp"
    "b24iLCJWYWxpZGF0ZSBlYWNoIHNlc3Npb24sIHJlcG9ydCBkZWFkIG9yIGR1cGxpY2F0ZSwgaW1w"
    "b3J0IHRoZSByZXN0LiJdLAogIFsiZHVwRGV0ZWN0IiwiRHVwbGljYXRlIGRldGVjdGlvbiIsIlJl"
    "amVjdCBhIHBob25lIG9yIHNlc3Npb24gYWxyZWFkeSBpbiB0aGUgZGF0YWJhc2UuIl0sCiAgWyJs"
    "b3dTdG9jayIsIkxvdy1zdG9jayBhbGVydHMiLCJQaW5nIHdoZW4gYSBjb3VudHJ5IGRyb3BzIGJl"
    "bG93IGl0cyBvd24gdGhyZXNob2xkLiJdLAogIFsic2VsbEhvdXJzIiwiU2VsbGluZy1ob3VycyBz"
    "Y2hlZHVsZSIsIkF1dG8taGlkZSBjb3VudHJpZXMgb3V0c2lkZSB0aGVpciBzZWxsaW5nIHdpbmRv"
    "dy4iXSwKICBbInNhbmRib3giLCJUZXN0IG1vZGUgLyBzYW5kYm94IiwiUnVuIHRoZSB3aG9sZSBm"
    "bG93IG9uIGZha2UgbW9uZXkgYmVmb3JlIGdvaW5nIGxpdmUuIl0sCiAgWyJpMThuIiwiTXVsdGkt"
    "bGFuZ3VhZ2UiLCJQZXItdXNlciBIaW5kaSAvIEVuZ2xpc2ggc3dpdGNoIGFjcm9zcyBldmVyeSBz"
    "dHJpbmcuIl1dXSwKIFsiU2VjdXJpdHkgJiBhbnRpLWFidXNlIixbCiAgWyJyYXRlTGltaXQiLCJS"
    "YXRlIGxpbWl0aW5nIiwiUGVyLXVzZXIgYWN0aW9uIGxpbWl0cyBzbyBub2JvZHkgY2FuIGhhbW1l"
    "ciB0aGUgYm90LiJdLAogIFsibXVsdGlBY2MiLCJNdWx0aS1hY2NvdW50IGRldGVjdGlvbiIsIkNh"
    "dGNoIHJlZmVycmFsIGZhcm1pbmcgYW5kIGZsYWcgc3VzcGljaW91cyBzaWdudXBzLiJdLAogIFsi"
    "dmVsb2NpdHkiLCJQdXJjaGFzZSB2ZWxvY2l0eSBhbGVydHMiLCJGbGFnIGFibm9ybWFsbHkgZmFz"
    "dCBidXlpbmcg4oCUIHR5cGljYWwgb2Ygc3RvbGVuIHRvcC11cHMuIl0sCiAgWyJhZG1pbjJmYSIs"
    "IkFkbWluIDJGQSIsIlBJTiBvciBUT1RQIGJlZm9yZSBiYWxhbmNlIGVkaXRzLCBicm9hZGNhc3Rz"
    "IG9yIHdpcGVzLiJdLAogIFsiYXV0b0JhbiIsIkF1dG8tYmFuIHJ1bGVzIiwiQmFuIG9uIGNoYXJn"
    "ZWJhY2ssIGZha2UgVVRSIG9yIHJlcGVhdGVkIGZhaWxlZCB2ZXJpZmljYXRpb24uIl0sCiAgWyJi"
    "bGFja2xpc3QiLCJCbGFja2xpc3QiLCJQZXJtYW5lbnRseSBibG9jayB1c2VyIElEcywgcGhvbmUg"
    "bnVtYmVycyBhbmQgVVBJIHJlZmVyZW5jZXMuIl0sCiAgWyJlbmNyeXB0IiwiU2Vzc2lvbiBlbmNy"
    "eXB0aW9uIGF0IHJlc3QiLCJBIGxlYWtlZCBkYXRhYmFzZSBmaWxlIGlzIHVzZWxlc3Mgd2l0aG91"
    "dCB0aGUga2V5LiJdXV0sCiBbIkFuYWx5dGljcyIsWwogIFsicmV2Q2hhcnQiLCJSZXZlbnVlIGRh"
    "c2hib2FyZCIsIkRhaWx5LCB3ZWVrbHkgYW5kIG1vbnRobHkgcmV2ZW51ZSBhbmQgcHJvZml0IGFz"
    "IGNoYXJ0cy4iXSwKICBbImZ1bm5lbCIsIkNvbnZlcnNpb24gZnVubmVsIiwiU2VlIHdoZXJlIHVz"
    "ZXJzIGRyb3Agb2ZmIGJldHdlZW4gam9pbmluZyBhbmQgYnV5aW5nLiJdLAogIFsicHJvZml0UmVw"
    "IiwiUGVyLWNvdW50cnkgcHJvZml0IiwiQ29zdCB2cyBzYWxlIHByaWNlIHZzIHZvbHVtZSBmb3Ig"
    "ZXZlcnkgY291bnRyeS4iXSwKICBbImNsdiIsIkN1c3RvbWVyIGxpZmV0aW1lIHZhbHVlIiwiU3Bl"
    "bmQsIGZyZXF1ZW5jeSBhbmQgY2h1cm4gcmlzayBwZXIgdXNlci4iXSwKICBbImRpZ2VzdCIsIkRh"
    "aWx5IGRpZ2VzdCIsIkVuZC1vZi1kYXkgc3VtbWFyeSBwdXNoZWQgdG8gdGhlIGFkbWluIGNoYXQu"
    "Il1dXSwKIFsiUXVhbGl0eSBvZiBsaWZlIixbCiAgWyJpbmxpbmVTZWFyY2giLCJJbmxpbmUgc2Vh"
    "cmNoIiwiVHlwZSBAeW91cmJvdCBpbmRpYSBpbiBhbnkgY2hhdCB0byBmaW5kIGFuZCBidXkuIl0s"
    "CiAgWyJzbGFzaENtZHMiLCJTbGFzaCBjb21tYW5kcyBtZW51IiwiL3N0YXJ0IC9idXkgL2JhbGFu"
    "Y2UgL29yZGVycyByZWdpc3RlcmVkIHdpdGggVGVsZWdyYW0uIl0sCiAgWyJzdG9ja0NvdW50ZXIi"
    "LCJMaXZlIHN0b2NrIGNvdW50ZXIiLCJBdXRvLXVwZGF0aW5nIHBpbm5lZCBwb3N0IGFuZCBpbi1h"
    "cHAgbGl2ZSBjb3VudC4iXSwKICBbIm9uYm9hcmRpbmciLCJPbmJvYXJkaW5nIHRvdXIiLCJUaHJl"
    "ZS1zdGVwIHdhbGt0aHJvdWdoIGZvciBmaXJzdC10aW1lIHVzZXJzLiJdLAogIFsibm90aWZQcmVm"
    "cyIsIk5vdGlmaWNhdGlvbiBwcmVmZXJlbmNlcyIsIlVzZXJzIHBpY2sgd2hpY2ggYWxlcnRzIHRo"
    "ZXkgcmVjZWl2ZS4iXSwKICBbInB1c2hOb3RpZiIsIk1pbmkgYXBwIHB1c2giLCJNaW5pIGFwcCBh"
    "Y3Rpb25zIG5vdGlmeSB0aGUgdXNlciB0aHJvdWdoIHRoZSBib3QuIl1dXQpdOwpjb25zdCBGRUFU"
    "PXt9OwpGR1JPVVBTLmZvckVhY2goKFtnLHJvd3NdKT0+cm93cy5mb3JFYWNoKHI9PkZFQVRbclsw"
    "XV09dHJ1ZSkpOwpjb25zdCBGRUFUMD1PYmplY3QuYXNzaWduKHt9LEZFQVQpOwpjb25zdCBHPShr"
    "LGgpPT5GRUFUW2tdP2g6IiI7CmNvbnN0IGFueUY9KC4uLmtzKT0+a3Muc29tZShrPT5GRUFUW2td"
    "KTsKbGV0IENBUlQ9W10sV0lTSD1bXTsKbGV0IFJFVklFV1M9e0luZGlhOls0LjgsNDEyXSxJbmRv"
    "bmVzaWE6WzQuNiwxODBdLEJyYXppbDpbNC43LDk2XSxOaWdlcmlhOls0LjQsNTRdLFZpZXRuYW06"
    "WzQuOSwyMzNdLFBoaWxpcHBpbmVzOls0LjUsNzFdfTsKbGV0IEZFRUQ9W1si8J+HrvCfh7MiLCJJ"
    "bmRpYSIsIuKCuTM1IiwiMiBtaW4gYWdvIl0sWyLwn4e78J+HsyIsIlZpZXRuYW0iLCLigrkyNiIs"
    "IjYgbWluIGFnbyJdLFsi8J+Hp/Cfh7ciLCJCcmF6aWwiLCLigrk0NCIsIjExIG1pbiBhZ28iXV07"
    "CmxldCBBRE09ewogIGtwaTp7cmV2OjQ4MjAsb3JkZXJzOjYxLHVzZXJzOjEyODQsYXV0bzozOH0s"
    "CiAgYmFyczpbNDIsNjEsMzgsNzQsNTUsODgsNjddLAogIHN0b2NrOltdLG9yZGVyczpbXSxkaXNw"
    "dXRlczpbXSx1c2VyczpbXSxhZG1pbnM6W10sY2hhbm5lbHM6W10sCiAgbG9nczp7bWFzdGVyOnRy"
    "dWUsY2hhbm5lbHM6W10sZXZlbnRzOltdfSxjb3Vwb25zOltdLGF1ZGl0OltdLHJldmlld3M6W10s"
    "ZGVwb3NpdHM6W10sbG9nZmVlZDpbXSwKICBwYXk6e3VwaToiIixwYXllZToiIixnbWFpbDoiIixn"
    "bWFpbF9zZXQ6ZmFsc2Usd2luZG93OjUscHJlZml4OiJQQVkiLG1pbjoxMCxtYXg6MjAwMDAsYXV0"
    "b2RlcDowLHBlbmRpbmc6MH0sCiAgc2VjdXJpdHk6e3JhdGU6MjAsdmVsb2NpdHk6NSxibGFja2xp"
    "c3Q6MCxiYW5uZWQ6MH0sCiAgbWFya2V0OntmbGFzaDoyMCxmbGFzaF9vbjpmYWxzZSxyZWYxOjUs"
    "cmVmMjoxLGdvYWw6MCxtb250aDowfSwKICBzZXR0aW5nczp7c3RvcmU6IiIsdGFnbGluZToiIixz"
    "dXBwb3J0OiIiLHByaWNlOjM1LGRhaWx5OjAuNCxhcHA6IiIsbWFpbnRlbmFuY2U6ZmFsc2Usc2Fu"
    "ZGJveDpmYWxzZX0sCiAgYW5hbHl0aWNzOntmdW5uZWw6e3VzZXJzOjAsZGVwOjAsYnV5OjB9LGNv"
    "dW50cnk6W10sc3RvY2tfdG90YWw6MCxzb2xkX3RvdGFsOjB9Cn07CmZ1bmN0aW9uIGFkbU51bShu"
    "KXtyZXR1cm4gKG58fDApLnRvTG9jYWxlU3RyaW5nKCJlbi1JTiIpO30KbGV0IEVBUk49e3N0cmVh"
    "azo0LGRhaWx5OjAuNDAsY2xhaW1lZDpmYWxzZSxwb2ludHM6MzQwLHBvaW50c1dvcnRoOjM0LGlu"
    "dml0ZXM6MTIsCiAgbDE6NTIuNDAsbDI6MTIuNDAsZWFybmVkOjY0LjgwLGxpbms6InQubWUveW91"
    "cl9ib3Q/c3RhcnQ9cmVmX2RlbW8iLAogIHJlY2VudDpbWyJ1c2VyXzEwNDEiLCJkZXBvc2l0ZWQg"
    "4oK5MjAwIiwiK+KCuTIuMDAiXSxbInVzZXJfMTAzOCIsImpvaW5lZCIsIuKAlCJdLFsidXNlcl8x"
    "MDIyIiwiZGVwb3NpdGVkIOKCuTUwMCIsIivigrk1LjAwIl1dLAogIGxlYWRlcnM6W1siMSIsIkBy"
    "YWh1bCIsIuKCuTQsODIwIl0sWyIyIiwiQGFtYW4iLCLigrkzLDE0MCJdLFsiMyIsIkB5b3UiLCLi"
    "grkxLDk4MCJdXSwKICB0YXNrczpbWyJKb2luIHRoZSBtYWluIGNoYW5uZWwiLCLigrkxLjAwIiwx"
    "XSxbIlBsYWNlIHlvdXIgZmlyc3Qgb3JkZXIiLCLigrkyLjAwIiwxXSxbIkludml0ZSAzIGZyaWVu"
    "ZHMiLCLigrk1LjAwIiwwXV19OwpmdW5jdGlvbiBzdGFycyhjKXtjb25zdCByPShSRVZJRVdTW2Nd"
    "fHxbNC43LDIwXSlbMF0sbj0oUkVWSUVXU1tjXXx8WzQuNywyMF0pWzFdOwogIHJldHVybiBgPHNw"
    "YW4gY2xhc3M9InN0YXJzIj4ke1sxLDIsMyw0LDVdLm1hcChpPT5pYyhpPD1NYXRoLnJvdW5kKHIp"
    "PyJzdGFyIjoic3RhciIpKS5qb2luKCIiKX08Yj4ke3J9ICgke259KTwvYj48L3NwYW4+YDt9CmZ1"
    "bmN0aW9uIGFwcGx5RmVhdHVyZXMoKXsKICBjb25zdCBjYz1kb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgiY2FydGNoaXAiKTsKICBpZihjYyl7Y2MuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZGVuIiwhRkVB"
    "VC5jYXJ0KTtkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2FydG4iKS50ZXh0Q29udGVudD1DQVJU"
    "Lmxlbmd0aDt9CiAgY29uc3QgZWFybj1hbnlGKCJzdHJlYWsiLCJyZWZEYXNoIiwicmVmTDIiLCJs"
    "b3lhbHR5IiwibGVhZGVyYm9hcmQiLCJ3aW5iYWNrIik7CiAgY29uc3QgZWI9ZG9jdW1lbnQucXVl"
    "cnlTZWxlY3RvcignI3RhYnMgYnV0dG9uW2RhdGEtdGFiPSJlYXJuIl0nKTsKICBpZihlYil7ZWIu"
    "Y2xhc3NMaXN0LnRvZ2dsZSgiaGlkZGVuIiwhZWFybik7CiAgICBpZighZWFybiYmVEFCPT09ImVh"
    "cm4iKXtUQUI9InNob3AiO2RvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiN0YWJzIGJ1dHRvbiIp"
    "LmZvckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseC5kYXRhc2V0LnRhYj09PSJzaG9w"
    "IikpO3JlbmRlcigpO319CiAgY29uc3QgYW1hcD17bWFya2V0OmFueUYoImNvdXBvbnMiLCJmbGFz"
    "aFNhbGUiLCJzY2hlZEJyb2FkY2FzdCIsIndpbmJhY2siLCJzYWxlc0ZlZWQiLCJsZWFkZXJib2Fy"
    "ZCIsImxveWFsdHkiLCJyZWZEYXNoIiwicmVmTDIiLCJhYmFuZG9uUmVtaW5kIiksCiAgICBhbmFs"
    "eXRpY3M6YW55RigicmV2Q2hhcnQiLCJmdW5uZWwiLCJwcm9maXRSZXAiLCJjbHYiLCJkaWdlc3Qi"
    "KSwKICAgIHNlY3VyaXR5OmFueUYoInJhdGVMaW1pdCIsIm11bHRpQWNjIiwidmVsb2NpdHkiLCJh"
    "ZG1pbjJmYSIsImF1dG9CYW4iLCJibGFja2xpc3QiLCJlbmNyeXB0IiksCiAgICBjb250ZW50OkZF"
    "QVQuY29udGVudEVkaXRvcn07CiAgT2JqZWN0LmVudHJpZXMoYW1hcCkuZm9yRWFjaCgoW2ssdl0p"
    "PT57Y29uc3QgYj1kb2N1bWVudC5xdWVyeVNlbGVjdG9yKCcjYXN1YiBidXR0b25bZGF0YS1hPSIn"
    "K2srJyJdJyk7CiAgICBpZihiKXtiLmNsYXNzTGlzdC50b2dnbGUoImhpZGRlbiIsIXYpOwogICAg"
    "ICBpZighdiYmQVRBQj09PWspe0FUQUI9ImRhc2giO2RvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IiNhc3ViIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseC5kYXRh"
    "c2V0LmE9PT0iZGFzaCIpKTtyZW5kZXJBZG1pbigpO319fSk7CiAgaWYod2luZG93Ll9fZG9ja1Bs"
    "YWNlKXJlcXVlc3RBbmltYXRpb25GcmFtZSgoKT0+d2luZG93Ll9fZG9ja1BsYWNlKHRydWUpKTsK"
    "ICBpZih3aW5kb3cuX192dGFiUGxhY2UpcmVxdWVzdEFuaW1hdGlvbkZyYW1lKCgpPT53aW5kb3cu"
    "X192dGFiUGxhY2UodHJ1ZSkpOwp9CmZ1bmN0aW9uIGFwcGx5Q01TKCl7CiBjb25zdCBnPWlkPT5k"
    "b2N1bWVudC5nZXRFbGVtZW50QnlJZChpZCk7CiBpZihnKCJibmFtZSIpKWcoImJuYW1lIikudGV4"
    "dENvbnRlbnQ9Q01TLnN0b3JlTmFtZTsKIGlmKGcoImFibmFtZSIpKWcoImFibmFtZSIpLnRleHRD"
    "b250ZW50PUNNUy5zdG9yZU5hbWU7CiBpZihDTVMuc3RvcmVOYW1lKXtDRkcubmFtZT1DTVMuc3Rv"
    "cmVOYW1lO30KIGlmKENNUy5zdG9yZVRhZylDRkcudGFnPUNNUy5zdG9yZVRhZzsKIHBhaW50QnJh"
    "bmQoQ01TLnN0b3JlTmFtZSxDTVMuc3RvcmVUYWcpOwogdHJ5ewogICBpZihDTVMuc3RvcmVOYW1l"
    "KWxvY2FsU3RvcmFnZS5zZXRJdGVtKCJzdG9yZU5hbWUiLENNUy5zdG9yZU5hbWUpOwogICBpZihD"
    "TVMuc3RvcmVUYWcpbG9jYWxTdG9yYWdlLnNldEl0ZW0oInN0b3JlVGFnIixDTVMuc3RvcmVUYWcp"
    "OwogfWNhdGNoKGUpe30KIGlmKGcoImJ0YWciKSlnKCJidGFnIikudGV4dENvbnRlbnQ9Q01TLnN0"
    "b3JlVGFnOwogZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtbmF2XSIpLmZvckVhY2go"
    "bj0+ewogICBjb25zdCBrPSJuYXYiK24uZGF0YXNldC5uYXZbMF0udG9VcHBlckNhc2UoKStuLmRh"
    "dGFzZXQubmF2LnNsaWNlKDEpOwogICBpZihDTVNba10pbi50ZXh0Q29udGVudD1DTVNba107fSk7"
    "CiBpZih3aW5kb3cuX19kb2NrUGxhY2UpcmVxdWVzdEFuaW1hdGlvbkZyYW1lKCgpPT53aW5kb3cu"
    "X19kb2NrUGxhY2UodHJ1ZSkpOwp9CmxldCBCQUw9MjQ4LjUwOwpjb25zdCBTVE9DSz1bCiB7Yzoi"
    "SW5kaWEiLGY6IvCfh67wn4ezIixjb2RlOiIrOTEiLHByaWNlOjM1LHdhczo0OSxxdHk6MTI4LGhv"
    "dDoxfSwKIHtjOiJJbmRvbmVzaWEiLGY6IvCfh67wn4epIixjb2RlOiIrNjIiLHByaWNlOjI4LHdh"
    "czowLHF0eTo2NH0sCiB7YzoiQnJhemlsIixmOiLwn4en8J+HtyIsY29kZToiKzU1IixwcmljZTo0"
    "NCx3YXM6NTkscXR5OjIyfSwKIHtjOiJOaWdlcmlhIixmOiLwn4ez8J+HrCIsY29kZToiKzIzNCIs"
    "cHJpY2U6MzEsd2FzOjAscXR5OjB9LAoge2M6IlZpZXRuYW0iLGY6IvCfh7vwn4ezIixjb2RlOiIr"
    "ODQiLHByaWNlOjI2LHdhczowLHF0eTo5MX0sCiB7YzoiUGhpbGlwcGluZXMiLGY6IvCfh7Xwn4et"
    "Iixjb2RlOiIrNjMiLHByaWNlOjM4LHdhczowLHF0eTo3fQpdOwpjb25zdCBPUkRFUlM9Wwoge2lk"
    "OiJPUkQtNzc0MSIsYzoiSW5kaWEiLGY6IvCfh67wn4ezIixudW06Iis5MSDigKLigKLigKLigKIg"
    "4oCi4oCiMzIiLHN0OiJEZWxpdmVyZWQiLHdoZW46IjIgbWluIGFnbyIscHJpY2U6MzV9LAoge2lk"
    "OiJPUkQtNzcyOCIsYzoiVmlldG5hbSIsZjoi8J+Hu/Cfh7MiLG51bToiKzg0IOKAouKAouKAouKA"
    "oiDigKLigKIwNyIsc3Q6IkRlbGl2ZXJlZCIsd2hlbjoiWWVzdGVyZGF5IixwcmljZToyNn0sCiB7"
    "aWQ6Ik9SRC03NzAyIixjOiJCcmF6aWwiLGY6IvCfh6fwn4e3IixudW06Iis1NSDigKLigKLigKLi"
    "gKIg4oCi4oCiODgiLHN0OiJSZWZ1bmRlZCIsd2hlbjoiMyBkYXlzIGFnbyIscHJpY2U6NDR9Cl07"
    "CmxldCBDSEFOTkVMUz1bCiB7aWQ6MSxuYW1lOiJNYWluIENoYW5uZWwiLGhhbmRsZToiQHlvdXJf"
    "bWFpbl9jaGFubmVsIixyZXE6dHJ1ZSxvbjp0cnVlLG1lbWJlcnM6IjEyLjRLIn0sCiB7aWQ6Mixu"
    "YW1lOiJCYWNrdXAgQ2hhbm5lbCIsaGFuZGxlOiJAeW91cl9iYWNrdXAiLHJlcTp0cnVlLG9uOnRy"
    "dWUsbWVtYmVyczoiMy4xSyJ9LAoge2lkOjMsbmFtZToiRGlzY3Vzc2lvbiBHcm91cCIsaGFuZGxl"
    "OiJAeW91cl9ncm91cCIscmVxOmZhbHNlLG9uOnRydWUsbWVtYmVyczoiNS44SyJ9Cl07CmxldCBM"
    "T0dDSD1bCiB7aWQ6MSxuYW1lOiJNYWluIExvZ3MiLGhhbmRsZToiQHlvdXJfbG9ncyIscHJpbWFy"
    "eTp0cnVlLG9uOnRydWV9LAoge2lkOjIsbmFtZToiUGF5bWVudCBMb2dzIixoYW5kbGU6IkB5b3Vy"
    "X3BheV9sb2dzIixwcmltYXJ5OmZhbHNlLG9uOnRydWV9LAoge2lkOjMsbmFtZToiRXJyb3IgTG9n"
    "cyIsaGFuZGxlOiItMTAweHh4eHh4eHh4eCIscHJpbWFyeTpmYWxzZSxvbjpmYWxzZX0KXTsKbGV0"
    "IExPR1NfTUFTVEVSPXRydWU7CmxldCBFVkVOVFM9Wwoge2s6Ik5ldyB1c2VyIGpvaW5lZCIsb246"
    "ZmFsc2UsY2g6MX0se2s6IkRlcG9zaXQgLyBwYXltZW50Iixvbjp0cnVlLGNoOjJ9LAoge2s6IlB1"
    "cmNoYXNlIixvbjp0cnVlLGNoOjF9LHtrOiJPVFAgcmVxdWVzdGVkIixvbjpmYWxzZSxjaDoxfSwK"
    "IHtrOiJTdG9jayBhZGRlZCIsb246dHJ1ZSxjaDoxfSx7azoiQWRtaW4gYWN0aW9uIixvbjp0cnVl"
    "LGNoOjF9LAoge2s6IkVycm9ycyAmIHdhcm5pbmdzIixvbjp0cnVlLGNoOjN9Cl07CmNvbnN0IFRY"
    "PVt7dDoiRGVwb3NpdCIsYToiK+KCuTIwMC4wMCIsdzoiVG9kYXksIDE0OjAyIn0se3Q6IlB1cmNo"
    "YXNlIMK3IEluZGlhIixhOiIt4oK5MzUuMDAiLHc6IlRvZGF5LCAxNDowNSJ9LAoge3Q6IkRhaWx5"
    "IGNoZWNrLWluIixhOiIr4oK5MC40MCIsdzoiVG9kYXksIDA5OjExIn0se3Q6IlJlZmVycmFsIGJv"
    "bnVzIixhOiIr4oK5My4yMCIsdzoiWWVzdGVyZGF5In1dOwoKbGV0IEZJTFRFUkVEPVNUT0NLLnNs"
    "aWNlKCksIFFVRVJZPSIiLCBDSElQPSJhbGwiOwpjb25zdCBFTVBUWT0nPGRpdiBjbGFzcz0iY2Fy"
    "ZCIgc3R5bGU9InRleHQtYWxpZ246Y2VudGVyIj48cCBjbGFzcz0ic3ViIj5Ob3RoaW5nIG1hdGNo"
    "ZXMgdGhhdCBzZWFyY2guPC9wPjwvZGl2Pic7CmNvbnN0IElDPXtiYWc6Ijxzdmcgdmlld0JveD1c"
    "IjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tl"
    "LXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJy"
    "b3VuZFwiPjxwYXRoIGQ9XCJNNiAyIDMgNnYxNGEyIDIgMCAwIDAgMiAyaDE0YTIgMiAwIDAgMCAy"
    "LTJWNmwtMy00elwiLz48cGF0aCBkPVwiTTMgNmgxOFwiLz48cGF0aCBkPVwiTTE2IDEwYTQgNCAw"
    "IDAgMS04IDBcIi8+PC9zdmc+Iixib3g6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9"
    "XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9r"
    "ZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJN"
    "MjEgOGEyIDIgMCAwIDAtMS0xLjczbC03LTRhMiAyIDAgMCAwLTIgMGwtNyA0QTIgMiAwIDAgMCAz"
    "IDh2OGEyIDIgMCAwIDAgMSAxLjczbDcgNGEyIDIgMCAwIDAgMiAwbDctNEEyIDIgMCAwIDAgMjEg"
    "MTZaXCIvPjxwYXRoIGQ9XCJtMy4zIDcgOC43IDUgOC43LTVcIi8+PHBhdGggZD1cIk0xMiAyMlYx"
    "MlwiLz48L3N2Zz4iLHphcDoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVc"
    "IiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVj"
    "YXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0xMyAyIDMg"
    "MTRoOWwtMSA4IDEwLTEyaC05elwiLz48L3N2Zz4iLHdhbGxldDoiPHN2ZyB2aWV3Qm94PVwiMCAw"
    "IDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lk"
    "dGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5k"
    "XCI+PHBhdGggZD1cIk0xOSA3VjVhMiAyIDAgMCAwLTItMkg1YTIgMiAwIDAgMCAwIDRoMTVhMiAy"
    "IDAgMCAxIDIgMnY5YTIgMiAwIDAgMS0yIDJINWEyIDIgMCAwIDEtMi0yVjVcIi8+PHBhdGggZD1c"
    "Ik0xOCAxMmguMDFcIi8+PC9zdmc+Iix1c2VyOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBm"
    "aWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBz"
    "dHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBk"
    "PVwiTTE5IDIxdi0yYTQgNCAwIDAgMC00LTRIOWE0IDQgMCAwIDAtNCA0djJcIi8+PGNpcmNsZSBj"
    "eD1cIjEyXCIgY3k9XCI3XCIgcj1cIjRcIi8+PC9zdmc+IixzaGllbGQ6Ijxzdmcgdmlld0JveD1c"
    "IjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tl"
    "LXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJy"
    "b3VuZFwiPjxwYXRoIGQ9XCJNMjAgMTNjMCA1LTMuNSA3LjUtNy42NiA4Ljk1YTEgMSAwIDAgMS0u"
    "NjctLjAxQzcuNSAyMC41IDQgMTggNCAxM1Y2YTEgMSAwIDAgMSAxLTFjMiAwIDQuNS0xLjIgNi4y"
    "NC0yLjcyYTEuMTcgMS4xNyAwIDAgMSAxLjUyIDBDMTQuNTEgMy44MSAxNyA1IDE5IDVhMSAxIDAg"
    "MCAxIDEgMXpcIi8+PC9zdmc+IixjaGVjazoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmls"
    "bD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyLjRcIiBz"
    "dHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBk"
    "PVwiTTIwIDYgOSAxN2wtNS01XCIvPjwvc3ZnPiIsc2VuZDoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0"
    "IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9"
    "XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+"
    "PHBhdGggZD1cIm0yMiAyLTcgMjAtNC05LTktNFpcIi8+PHBhdGggZD1cIk0yMiAyIDExIDEzXCIv"
    "Pjwvc3ZnPiIscGx1czoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBz"
    "dHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyLjRcIiBzdHJva2UtbGluZWNh"
    "cD1cInJvdW5kXCI+PHBhdGggZD1cIk0xMiA1djE0TTUgMTJoMTRcIi8+PC9zdmc+Iixjb3B5OiI8"
    "c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRD"
    "b2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tl"
    "LWxpbmVqb2luPVwicm91bmRcIj48cmVjdCB3aWR0aD1cIjE0XCIgaGVpZ2h0PVwiMTRcIiB4PVwi"
    "OFwiIHk9XCI4XCIgcng9XCIyXCIvPjxwYXRoIGQ9XCJNNCAxNmMtMS4xIDAtMi0uOS0yLTJWNGMw"
    "LTEuMS45LTIgMi0yaDEwYzEuMSAwIDIgLjkgMiAyXCIvPjwvc3ZnPiIsY2hldjoiPHN2ZyB2aWV3"
    "Qm94PVwiMCAwIDI0IDI0XCIgd2lkdGg9XCIxNlwiIGhlaWdodD1cIjE2XCIgZmlsbD1cIm5vbmVc"
    "IiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVj"
    "YXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIm05IDE4IDYt"
    "Ni02LTZcIi8+PC9zdmc+IixmaXJlOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiB3aWR0aD1c"
    "IjEyXCIgaGVpZ2h0PVwiMTJcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwi"
    "IHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVq"
    "b2luPVwicm91bmRcIj48cGF0aCBkPVwiTTEyIDJjMSA0IDUgNSA1IDlhNSA1IDAgMCAxLTEwIDBj"
    "MC0yIDEtMyAyLTQgMCAyIDEgMyAyIDMgMC0zLTEtNSAxLThaXCIvPjwvc3ZnPiIsZG93bjoiPHN2"
    "ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29s"
    "b3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1s"
    "aW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0xMiA1djE0TTE5IDEybC03IDctNy03XCIvPjwv"
    "c3ZnPiIsdXA6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tl"
    "PVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91"
    "bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTIgMTlWNU01IDEybDct"
    "NyA3IDdcIi8+PC9zdmc+IixzZWFyY2g6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9"
    "XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9r"
    "ZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxjaXJjbGUgY3g9"
    "XCIxMVwiIGN5PVwiMTFcIiByPVwiN1wiLz48cGF0aCBkPVwibTIxIDIxLTQuMy00LjNcIi8+PC9z"
    "dmc+IixzdGFyOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwiY3VycmVudENvbG9y"
    "XCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMS41XCIgc3Ryb2tlLWxp"
    "bmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwibTEyIDMgMi42IDUuMyA1LjkuOS00LjMgNC4xIDEg"
    "NS44LTUuMi0yLjctNS4yIDIuNyAxLTUuOEwzLjUgOS4ybDUuOS0uOXpcIi8+PC9zdmc+In07CmNv"
    "bnN0IGljPW49PklDW25dfHwiIjsKY29uc3QgZXNjPXM9PlN0cmluZyhzKS5yZXBsYWNlKC9bJjw+"
    "Il0vZyxjPT4oeyImIjoiJmFtcDsiLCI8IjoiJmx0OyIsIj4iOiImZ3Q7IiwnIic6IiZxdW90OyJ9"
    "W2NdKSk7CmNvbnN0IG1vbmV5PW49PkNGRy5jdXIrTnVtYmVyKG4pLnRvRml4ZWQoMik7CgpsZXQg"
    "dHQ7Y29uc3QgdG9hc3Q9bT0+e2NvbnN0IGU9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInRvYXN0"
    "Iik7ZS50ZXh0Q29udGVudD1tO2UuY2xhc3NMaXN0LmFkZCgic2hvdyIpO2NsZWFyVGltZW91dCh0"
    "dCk7dHQ9c2V0VGltZW91dCgoKT0+ZS5jbGFzc0xpc3QucmVtb3ZlKCJzaG93IiksMTkwMCl9Owpk"
    "b2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCJjbGljayIsZT0+e2NvbnN0IGI9ZS50YXJnZXQuY2xv"
    "c2VzdCgiW2RhdGEtdG9hc3RdIik7aWYoYil0b2FzdChiLmRhdGFzZXQudG9hc3QpfSk7CmNvbnN0"
    "IHNoZWV0PWg9PnsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicGFuZWwiKS5pbm5lckhUTUw9"
    "JzxkaXYgY2xhc3M9ImdyYWIiPjwvZGl2PicraDsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgi"
    "c2hlZXQiKS5jbGFzc0xpc3QuYWRkKCJvcGVuIik7CiAgLyogY29udHJvbHMgaW5zaWRlIGEgc2hl"
    "ZXQgYXJlIGNyZWF0ZWQgYWZ0ZXIgYmluZGluZywgc28gYmluZCB0aGVtIG5vdyAqLwogIHRyeXsg"
    "aWYodHlwZW9mIGJpbmRDb21tb249PT0iZnVuY3Rpb24iKSBiaW5kQ29tbW9uKCk7IH1jYXRjaChl"
    "KXt9CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3NoZWV0IFtkYXRhLWNsb3NlXSIpLmZv"
    "ckVhY2goeD0+eC5vbmNsaWNrPWNsb3NlU2hlZXQpOwp9Owpjb25zdCBjbG9zZVNoZWV0PSgpPT5k"
    "b2N1bWVudC5nZXRFbGVtZW50QnlJZCgic2hlZXQiKS5jbGFzc0xpc3QucmVtb3ZlKCJvcGVuIik7"
    "CmRvY3VtZW50LmFkZEV2ZW50TGlzdGVuZXIoImNsaWNrIixlPT57aWYoZS50YXJnZXQuY2xvc2Vz"
    "dCgiW2RhdGEtY2xvc2VdIikpY2xvc2VTaGVldCgpfSk7Cgpkb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "QWxsKCIjc3cgYnV0dG9uIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICBpZihiLmRhdGFz"
    "ZXQuc3c9PT0iYWRtaW4iJiYhYWRtaW5BbGxvd2VkKCkpe3RvYXN0KCJBZG1pbnMgb25seSIpO3Jl"
    "dHVybjt9CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3N3IGJ1dHRvbiIpLmZvckVhY2go"
    "eD0+eC5jbGFzc0xpc3QucmVtb3ZlKCJvbiIpKTtiLmNsYXNzTGlzdC5hZGQoIm9uIik7CiAgWyJh"
    "cHAiLCJhZG1pbiJdLmZvckVhY2godj0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInZpZXctIit2"
    "KS5jbGFzc0xpc3QudG9nZ2xlKCJoaWRkZW4iLHYhPT1iLmRhdGFzZXQuc3cpKTsKICBkb2N1bWVu"
    "dC5nZXRFbGVtZW50QnlJZCgidGFicyIpLnN0eWxlLmRpc3BsYXk9Yi5kYXRhc2V0LnN3PT09ImFw"
    "cCI/IiI6Im5vbmUiO3dpbmRvdy5zY3JvbGxUbygwLDApOwogIHJlcXVlc3RBbmltYXRpb25GcmFt"
    "ZSgoKT0+e2lmKHdpbmRvdy5fX2RvY2tQbGFjZSl3aW5kb3cuX19kb2NrUGxhY2UodHJ1ZSk7CiAg"
    "ICBpZih3aW5kb3cuX192dGFiUGxhY2Upd2luZG93Ll9fdnRhYlBsYWNlKHRydWUpO30pO30pOwoK"
    "bGV0IFRBQj0ic2hvcCI7CmRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiN0YWJzIGJ1dHRvbiIp"
    "LmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgi"
    "I3RhYnMgYnV0dG9uIikuZm9yRWFjaCh4PT54LmNsYXNzTGlzdC5yZW1vdmUoIm9uIikpO2IuY2xh"
    "c3NMaXN0LmFkZCgib24iKTsKICBUQUI9Yi5kYXRhc2V0LnRhYjtyZW5kZXIoKTt3aW5kb3cuc2Ny"
    "b2xsVG8oMCwwKTtpZih3aW5kb3cuX19kb2NrUGxhY2UpcmVxdWVzdEFuaW1hdGlvbkZyYW1lKHdp"
    "bmRvdy5fX2RvY2tQbGFjZSk7fSk7CmRvY3VtZW50LmFkZEV2ZW50TGlzdGVuZXIoImNsaWNrIixl"
    "PT57Y29uc3QgZz1lLnRhcmdldC5jbG9zZXN0KCJbZGF0YS1nb10iKTtpZighZylyZXR1cm47CiAg"
    "Y29uc3QgdD1nLmRhdGFzZXQuZ287ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3RhYnMgYnV0"
    "dG9uIikuZm9yRWFjaCh4PT54LmNsYXNzTGlzdC50b2dnbGUoIm9uIix4LmRhdGFzZXQudGFiPT09"
    "dCkpOwogIFRBQj10O3JlbmRlcigpO3dpbmRvdy5zY3JvbGxUbygwLDApO30pOwoKZnVuY3Rpb24g"
    "cmVuZGVyKCl7Y29uc3Qgc3Q9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0YWdlIik7CiAgaWYo"
    "TE9DS0VEKXtwYWludExvY2tlZCgpO3JldHVybjt9CiAgc3QuaW5uZXJIVE1MPSc8ZGl2IGNsYXNz"
    "PSJzayI+PC9kaXY+PGRpdiBjbGFzcz0ic2siPjwvZGl2PjxkaXYgY2xhc3M9InNrIiBzdHlsZT0i"
    "aGVpZ2h0OjE0MHB4Ij48L2Rpdj4nOwogIHNldFRpbWVvdXQoKCk9PntpZihMT0NLRUQpe3BhaW50"
    "TG9ja2VkKCk7cmV0dXJuO30KICAgIHN0LmlubmVySFRNTD0oe3Nob3A6dlNob3Asb3JkZXJzOnZP"
    "cmRlcnMsZWFybjp2RWFybix3YWxsZXQ6dldhbGxldCx5b3U6dllvdX0pW1RBQl0oKTsKICAgIGJp"
    "bmRTdGFnZSgpO3JldmVhbFN0YWdlKHN0KTtiaW5kVGlsdCgpO30sUk0/MDoxNjApO30KCmZ1bmN0"
    "aW9uIHZTaG9wKCl7CiAgY29uc3QgbGl2ZT1TVE9DSy5maWx0ZXIocz0+cy5xdHk+MCkubGVuZ3Ro"
    "LCB1bml0cz1TVE9DSy5yZWR1Y2UoKGEsYik9PmErYi5xdHksMCk7CiAgaWYoTElWRSYmIVNUT0NL"
    "Lmxlbmd0aCl7CiAgICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJtYXJnaW46MThw"
    "eCAwO3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MjZweCI+CiAgICAgIDxoMyBzdHlsZT0ibWFy"
    "Z2luOjAgMCA2cHgiPiR7ZXNjKENNUy5lbXB0eVRpdGxlfHwiUmVzdG9ja2luZyIpfTwvaDM+CiAg"
    "ICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+JHtlc2MoQ01TLmVtcHR5Qm9keXx8"
    "IkV2ZXJ5dGhpbmcganVzdCBzb2xkIG91dC4gRnJlc2ggYWNjb3VudHMgbGFuZCBoZXJlIHNob3J0"
    "bHkgXHUyMDE0IHR1cm4gb24gYW4gYWxlcnQgYW5kIHlvdSB3aWxsIGhlYXIgZmlyc3QuIil9PC9w"
    "PgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjE0cHgi"
    "IGRhdGEtYWN0PSJhbGVydHMiPk5vdGlmeSBtZTwvYnV0dG9uPjwvZGl2PmA7CiAgfQogIHJldHVy"
    "biBgCiAgPGRpdiBjbGFzcz0iYmFubmVyIj48ZGl2IGNsYXNzPSJ0cmFjayI+CiAgICAke1sxLDJd"
    "Lm1hcCgoKT0+YDxzcGFuPiR7aWMoInphcCIpfSR7Q01TLnRpY2sxfTwvc3Bhbj48c3Bhbj4ke2lj"
    "KCJ3YWxsZXQiKX0ke0NNUy50aWNrMn08L3NwYW4+CiAgICA8c3Bhbj4ke2ljKCJzaGllbGQiKX0k"
    "e0NNUy50aWNrM308L3NwYW4+PHNwYW4+JHtpYygic3RhciIpfSR7Q01TLnRpY2s0fTwvc3Bhbj5g"
    "KS5qb2luKCIiKX0KICA8L2Rpdj48L2Rpdj4KICAke0coIm9uYm9hcmRpbmciLGA8ZGl2IGNsYXNz"
    "PSJjYXJkIHRpZ2h0IiBpZD0idG91cmNhcmQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRl"
    "bXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoInphcCIpfTwv"
    "ZGl2PgogICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1z"
    "aXplOi44cmVtIj5OZXcgaGVyZT8gMy1zdGVwIHRvdXI8L2I+CiAgICAgIDxicj48c21hbGwgc3R5"
    "bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+VG9wIHVwLCBwaWNrIGEgY291"
    "bnRyeSwgZ2V0IHRoZSBPVFAgaW4tYXBwLjwvc21hbGw+PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNz"
    "PSJidG4geHMiIGlkPSJ0b3VyZ28iPlN0YXJ0PC9idXR0b24+CiAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4geHMgZ2hvc3QiIGlkPSJ0b3Vyc2tpcCI+U2tpcDwvYnV0dG9uPjwvZGl2PmApfQogIDxzZWN0"
    "aW9uIGNsYXNzPSJoZXJvIj4KICAgIDxkaXYgY2xhc3M9ImtpY2siPiR7Q01TLmhlcm9LaWNrfTwv"
    "ZGl2PgogICAgPGgxPiR7Q01TLmhlcm9IMX08L2gxPgogICAgPHA+JHtDTVMuaGVyb1N1Yi5yZXBs"
    "YWNlKCJ7bn0iLGxpdmUpfTwvcD4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+CiAgICAgIDxidXR0"
    "b24gY2xhc3M9ImJ0biBjdGEiIGRhdGEtc2Nyb2xsPSJzaG9wbGlzdCI+JHtpYygiYmFnIil9ICR7"
    "Q01TLmhlcm9CdG4xfTwvYnV0dG9uPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2xhc3MiIGRh"
    "dGEtZ289IndhbGxldCI+JHtDTVMuaGVyb0J0bjJ9PC9idXR0b24+CiAgICA8L2Rpdj4KICA8L3Nl"
    "Y3Rpb24+CiAgPHN2ZyBjbGFzcz0idHJhY2UiIHZpZXdCb3g9IjAgMCAzMjAgMTAiIHByZXNlcnZl"
    "QXNwZWN0UmF0aW89Im5vbmUiPjxwYXRoIGNsYXNzPSJiYXNlIiBkPSJNMCw1IEwzMjAsNSIvPgog"
    "ICAgPHBhdGggY2xhc3M9ImxlYWQiIGQ9Ik0wLDUgTDMyMCw1IiBzdHJva2U9InVybCgjdGcxKSIv"
    "PgogICAgPGRlZnM+PGxpbmVhckdyYWRpZW50IGlkPSJ0ZzEiIGdyYWRpZW50VW5pdHM9InVzZXJT"
    "cGFjZU9uVXNlIiB4MT0iLTE2MCIgeTE9IjAiIHgyPSIwIiB5Mj0iMCI+CiAgICAgIDxzdG9wIHN0"
    "b3AtY29sb3I9InZhcigtLWFjYykiIHN0b3Atb3BhY2l0eT0iMCIvPjxzdG9wIG9mZnNldD0iLjUi"
    "IHN0b3AtY29sb3I9InZhcigtLWFjYykiLz4KICAgICAgPHN0b3Agb2Zmc2V0PSIxIiBzdG9wLWNv"
    "bG9yPSJ2YXIoLS1hY2MyKSIgc3RvcC1vcGFjaXR5PSIwIi8+CiAgICAgIDxhbmltYXRlIGF0dHJp"
    "YnV0ZU5hbWU9IngxIiB2YWx1ZXM9Ii0xNjA7MzIwIiBkdXI9IjIuMnMiIHJlcGVhdENvdW50PSJp"
    "bmRlZmluaXRlIi8+CiAgICAgIDxhbmltYXRlIGF0dHJpYnV0ZU5hbWU9IngyIiB2YWx1ZXM9IjA7"
    "NDgwIiBkdXI9IjIuMnMiIHJlcGVhdENvdW50PSJpbmRlZmluaXRlIi8+CiAgICA8L2xpbmVhckdy"
    "YWRpZW50PjwvZGVmcz48L3N2Zz4KICA8ZGl2IGNsYXNzPSJmZWF0cyI+CiAgICA8ZGl2IGNsYXNz"
    "PSJmZWF0Ij4ke2ljKCJ6YXAiKX08Yj4ke0NNUy5mZWF0MX08L2I+PHNtYWxsPiR7Q01TLmZlYXQx"
    "c308L3NtYWxsPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iZmVhdCI+JHtpYygic2hpZWxkIil9PGI+"
    "JHtDTVMuZmVhdDJ9PC9iPjxzbWFsbD4ke0NNUy5mZWF0MnN9PC9zbWFsbD48L2Rpdj4KICAgIDxk"
    "aXYgY2xhc3M9ImZlYXQiPiR7aWMoIndhbGxldCIpfTxiPiR7Q01TLmZlYXQzfTwvYj48c21hbGw+"
    "JHtDTVMuZmVhdDNzfTwvc21hbGw+PC9kaXY+CiAgPC9kaXY+CiAgJHtHKCJzdG9ja0NvdW50ZXIi"
    "LGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1z"
    "OmNlbnRlcjtnYXA6MTBweCI+CiAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+PGkgY2xhc3M9ImRv"
    "dCI+PC9pPkxpdmU8L3NwYW4+CiAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9u"
    "dC1zaXplOi43NnJlbTtjb2xvcjp2YXIoLS10eDIpIj48YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgp"
    "Ij4ke3VuaXRzfTwvYj4gYWNjb3VudHMgaW4gc3RvY2sgYWNyb3NzIDxiIHN0eWxlPSJjb2xvcjp2"
    "YXIoLS10eCkiPiR7bGl2ZX08L2I+IGNvdW50cmllczwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIHhzIHNvZnQiIGRhdGEtYWN0PSJjaGFubmVsIj5DaGFubmVsPC9idXR0b24+PC9kaXY+YCl9"
    "CiAgJHtHKCJmbGFzaFNhbGUiLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxh"
    "eTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICA8c3BhbiBjbGFzcz0icGls"
    "bCBob3QiPiR7aWMoImZpcmUiKX0gJHtDTVMuZmxhc2hQaWxsfTwvc3Bhbj4KICAgIDxkaXYgc3R5"
    "bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc2cmVtO2NvbG9yOnZhcigtLXR4Miki"
    "PiR7Q01TLmZsYXNoVGV4dH0gPGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSIgaWQ9ImNkIj4xNDo1"
    "MjwvYj48L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyIgZGF0YS1hY3Q9ImZsYXNoIj4k"
    "e0NNUy5mbGFzaEJ0bn08L2J1dHRvbj48L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJ3aGVlbCI+PGRp"
    "diBjbGFzcz0icmluZyI+JHtTVE9DSy5tYXAoKHMsaSk9PmA8YiBzdHlsZT0idHJhbnNmb3JtOnJv"
    "dGF0ZSgke2kqNjB9ZGVnKSB0cmFuc2xhdGUoNzBweCkgcm90YXRlKC0ke2kqNjB9ZGVnKSI+JHtz"
    "LmNvZGV9PC9iPmApLmpvaW4oIiIpfTwvZGl2PgogICAgPGRpdiBjbGFzcz0iY2FwIj48ZW0+JHtD"
    "TVMud2hlZWxLaWNrfTwvZW0+PGI+JHtDTVMud2hlZWxUaXRsZX08L2I+CiAgICAgIDxkaXYgY2xh"
    "c3M9ImNsb3VkIiBzdHlsZT0ibWFyZ2luLXRvcDo2cHgiPjxkaXYgY2xhc3M9InQiPiR7WzEsMl0u"
    "bWFwKCgpPT5DTVMud2hlZWxXb3Jkcy5zcGxpdCgiLCIpLm1hcCh3PT5gPGk+JHt3LnRyaW0oKX08"
    "L2k+YCkuam9pbigiIikpLmpvaW4oIiIpfTwvZGl2PjwvZGl2PjwvZGl2PjwvZGl2PgogICR7Rygi"
    "c2FsZXNGZWVkIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkp1c3Qgc29sZDwvaDM+PHAgY2xhc3M9"
    "InN1YiI+TGl2ZSBmZWVkIOKAlCBhbHNvIGF1dG8tcG9zdGVkIHRvIHlvdXIgY2hhbm5lbC48L3A+"
    "CiAgICAke0ZFRUQubGVuZ3RoP0ZFRUQubWFwKChbZixjLGEsd10pPT5gPGRpdiBjbGFzcz0iZmVl"
    "ZGxpbmUiPjxzcGFuPiR7Zn08L3NwYW4+PGI+JHtjfTwvYj4gc29sZCBmb3IgPGI+JHthfTwvYj48"
    "c3BhbiBzdHlsZT0ibWFyZ2luLWxlZnQ6YXV0byI+JHt3fTwvc3Bhbj48L2Rpdj5gKS5qb2luKCIi"
    "KQogICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gc2FsZXMgeWV0IHRv"
    "ZGF5LjwvcD4nfTwvZGl2PmApfQogIDxkaXYgY2xhc3M9InNlYyIgaWQ9InNob3BsaXN0Ij48aDI+"
    "JHtDTVMuc2hvcEhlYWR9PC9oMj48YSBkYXRhLWFjdD0ic29ydCI+JHtDTVMuc2hvcFNvcnR9PC9h"
    "PjwvZGl2PgogIDxkaXYgY2xhc3M9Im1zZWFyY2giPjxkaXYgY2xhc3M9ImZpZWxkd3JhcCI+JHtp"
    "Yygic2VhcmNoIil9PGlucHV0IGlkPSJxIiBwbGFjZWhvbGRlcj0iJHtDTVMuc2VhcmNoUGh9Ij48"
    "L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBkYXRhLWFjdD0iYWxlcnRzIj4k"
    "e0NNUy5hbGVydHNCdG59PC9idXR0b24+PC9kaXY+CiAgPGRpdiBjbGFzcz0iY2hpcHJvdyIgaWQ9"
    "ImNoaXBzIj4KICAgIDxidXR0b24gY2xhc3M9Im9uIiBkYXRhLWZpbHRlcj0iYWxsIj5BbGw8L2J1"
    "dHRvbj48YnV0dG9uIGRhdGEtZmlsdGVyPSJzdG9jayI+SW4gc3RvY2s8L2J1dHRvbj4KICAgIDxi"
    "dXR0b24gZGF0YS1maWx0ZXI9ImRlYWwiPk9uIGRlYWw8L2J1dHRvbj48YnV0dG9uIGRhdGEtZmls"
    "dGVyPSJjaGVhcCI+VW5kZXIg4oK5MzU8L2J1dHRvbj4KICAgICR7RygidmFyaWFudHMiLGA8YnV0"
    "dG9uIGRhdGEtZmlsdGVyPSJ2MmZhIj5ObyAyRkE8L2J1dHRvbj48YnV0dG9uIGRhdGEtZmlsdGVy"
    "PSJ2bWFpbCI+V2l0aCBtYWlsPC9idXR0b24+PGJ1dHRvbiBkYXRhLWZpbHRlcj0idnNwYW0iPlNw"
    "YW0tZnJlZTwvYnV0dG9uPmApfTwvZGl2PgogIDxkaXYgaWQ9InNob3Bib2R5Ij4ke3Nob3BCb2R5"
    "KCl9PC9kaXY+YDsKfQpmdW5jdGlvbiBzaG9wQm9keSgpe3JldHVybiBgJHtGSUxURVJFRC5sZW5n"
    "dGg/YDxkaXYgY2xhc3M9ImdyaWQiPiR7RklMVEVSRUQubWFwKHNob3BDYXJkKS5qb2luKCIiKX08"
    "L2Rpdj5gOkVNUFRZfWA7fQpmdW5jdGlvbiBwcmljZUJpdHMocyl7CiAgcmV0dXJuIGAke3Mud2Fz"
    "P2A8cz4ke21vbmV5KHMud2FzKX08L3M+YDoiIn0ke21vbmV5KHMucHJpY2UpfWA7Cn0KZnVuY3Rp"
    "b24gc2hvcFJvdyhzKXtjb25zdCBpPVNUT0NLLmluZGV4T2Yocyksb2ZmPXMucXR5PT09MDsKICBy"
    "ZXR1cm4gYDxkaXYgY2xhc3M9InJvdyIgZGF0YS1idXk9IiR7aX0iPjxkaXYgY2xhc3M9ImZsYWci"
    "PiR7cy5mfTwvZGl2PgogICAgPGRpdiBjbGFzcz0ibWlkIj48Yj4ke2VzYyhzLmMpfSAke3MuY29k"
    "ZX08L2I+CiAgICAgIDxzbWFsbD4ke29mZj8iT3V0IG9mIHN0b2NrIOKAlCBnZXQgYW4gYWxlcnQi"
    "OnMucXR5KyIgaW4gc3RvY2sgwrcgaW5zdGFudCJ9PC9zbWFsbD4KICAgICAgJHtHKCJyZXZpZXdz"
    "IixgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDozcHgiPiR7c3RhcnMocy5jKX08L2Rpdj5gKX0KICAg"
    "ICAgJHtHKCJ2YXJpYW50cyIsYDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6M3B4O2Rpc3BsYXk6Zmxl"
    "eDtnYXA6NHB4O2ZsZXgtd3JhcDp3cmFwIj4KICAgICAgICA8c3BhbiBjbGFzcz0icGlsbCI+Tm8g"
    "MkZBPC9zcGFuPiR7cy5ob3Q/JzxzcGFuIGNsYXNzPSJwaWxsIj5XaXRoIG1haWw8L3NwYW4+Jzoi"
    "In08L2Rpdj5gKX08L2Rpdj4KICAgIDxkaXYgY2xhc3M9InJ0Ij48ZGl2IGNsYXNzPSJwIj4ke3By"
    "aWNlQml0cyhzKX08L2Rpdj4KICAgICAgJHtHKCJkeW5QcmljZSIsYDxzbWFsbCBzdHlsZT0iZGlz"
    "cGxheTpibG9jaztmb250LXNpemU6LjZyZW07Y29sb3I6dmFyKC0tdHgyKSI+bGl2ZSBwcmljZTwv"
    "c21hbGw+YCl9CiAgICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6NXB4O2p1c3RpZnkt"
    "Y29udGVudDpmbGV4LWVuZDttYXJnaW4tdG9wOjZweCI+CiAgICAgICAgJHtHKCJ3aXNobGlzdCIs"
    "YDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLXdpc2g9IiR7aX0iPiR7V0lTSC5pbmNs"
    "dWRlcyhpKT8i4pmlIjoi4pmhIn08L2J1dHRvbj5gKX0KICAgICAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4geHMiPiR7b2ZmPyJOb3RpZnkgTWUiOiJCdXkifTwvYnV0dG9uPjwvZGl2PjwvZGl2PjwvZGl2"
    "PmA7fQpmdW5jdGlvbiBzaG9wQ2FyZChzKXtjb25zdCBpPVNUT0NLLmluZGV4T2Yocyksb2ZmPXMu"
    "cXR5PT09MDsKICByZXR1cm4gYDxkaXYgY2xhc3M9InBjYXJkIiBkYXRhLWJ1eT0iJHtpfSIgc3R5"
    "bGU9InBvc2l0aW9uOnJlbGF0aXZlIj4KICAgICR7Rygid2lzaGxpc3QiLGA8c3BhbiBjbGFzcz0i"
    "aGVhcnQgJHtXSVNILmluY2x1ZGVzKGkpPyJvbiI6IiJ9IiBkYXRhLXdpc2g9IiR7aX0iPiR7aWMo"
    "InN0YXIiKX08L3NwYW4+YCl9CiAgICA8ZGl2IGNsYXNzPSJ0b3AiPjxkaXYgY2xhc3M9ImZsYWci"
    "PiR7cy5mfTwvZGl2PgogICAgICA8c3BhbiBjbGFzcz0icGlsbCAke29mZj8ib2ZmIjoib24ifSI+"
    "JHtvZmY/IlNvbGQgb3V0IjpzLnF0eSsiIGxlZnQifTwvc3Bhbj48L2Rpdj4KICAgIDxkaXY+PGRp"
    "diBjbGFzcz0ibm0iPiR7ZXNjKHMuYyl9PC9kaXY+PGRpdiBjbGFzcz0ibWV0YSI+JHtzLmNvZGV9"
    "IMK3IGluc3RhbnQ8L2Rpdj4KICAgICAgJHtHKCJyZXZpZXdzIixgPGRpdiBzdHlsZT0ibWFyZ2lu"
    "LXRvcDo0cHgiPiR7c3RhcnMocy5jKX08L2Rpdj5gKX08L2Rpdj4KICAgIDxkaXYgY2xhc3M9InBy"
    "aWNlIj4ke3ByaWNlQml0cyhzKX08L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayBz"
    "bSI+JHtvZmY/Ik5vdGlmeSBNZSI6IkJ1eSBOb3cifTwvYnV0dG9uPjwvZGl2PmA7fQoKZnVuY3Rp"
    "b24gdk9yZGVycygpewogIHJldHVybiBgPGRpdiBjbGFzcz0ic2VjIj48aDI+WW91ciBPcmRlcnM8"
    "L2gyPjxhIGRhdGEtYWN0PSJjc3YiPkV4cG9ydDwvYT48L2Rpdj4KICAke09SREVSUy5tYXAoKG8s"
    "b2kpPT5gPGRpdiBjbGFzcz0iY2FyZCIgZGF0YS1vcmRlci1pZD0iJHtvLmlkfSI+CiAgICA8ZGl2"
    "IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMnB4Ij4KICAgICAg"
    "PGRpdiBjbGFzcz0iZmxhZyI+JHtvLmZ9PC9kaXY+CiAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTtt"
    "aW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODVyZW07ZGlzcGxheTpibG9jayI+JHtv"
    "Lm51bX08L2I+CiAgICAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6"
    "ZTouN3JlbSI+JHtvLndoZW59PC9zbWFsbD48L2Rpdj4KICAgICAgPGRpdiBzdHlsZT0idGV4dC1h"
    "bGlnbjpyaWdodCI+PHNwYW4gY2xhc3M9InBpbGwgJHtvLnN0PT09IkRlbGl2ZXJlZCI/Im9uIjoi"
    "b2ZmIn0iPiR7by5zdH08L3NwYW4+PC9kaXY+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJjYXJkIHRp"
    "Z2h0IiBzdHlsZT0ibWFyZ2luOjEwcHggMCAwO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpIj4KICAg"
    "ICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlBob25lIG51bWJlcjwvc3Bhbj4KICAgICAgICA8Yj48"
    "Y29kZSBzdHlsZT0iZm9udC1zaXplOi44cmVtO3VzZXItc2VsZWN0OmFsbCI+JHtlc2Moby5waG9u"
    "ZXx8IuKAlCIpfTwvY29kZT4KICAgICAgICAke28ucGhvbmU/YDxidXR0b24gY2xhc3M9ImJ0biB4"
    "cyBzb2Z0IiBkYXRhLWNvcHk9InRleHQiIGRhdGEtY29weS12YWx1ZT0iJHtlc2Moby5waG9uZSl9"
    "Ij5Db3B5PC9idXR0b24+YDoiIn08L2I+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bh"
    "bj4yRkEgcGFzc3dvcmQ8L3NwYW4+CiAgICAgICAgPGI+PGNvZGUgc3R5bGU9ImZvbnQtc2l6ZTou"
    "OHJlbTt1c2VyLXNlbGVjdDphbGwiPiR7ZXNjKG8udHdvZmF8fCJub25lIil9PC9jb2RlPgogICAg"
    "ICAgICR7by50d29mYT9gPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtY29weT0idGV4"
    "dCIgZGF0YS1jb3B5LXZhbHVlPSIke2VzYyhvLnR3b2ZhKX0iPkNvcHk8L2J1dHRvbj5gOiIifTwv"
    "Yj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxhc3QgbG9naW4gY29kZTwvc3Bh"
    "bj4KICAgICAgICA8Yj48Y29kZSBzdHlsZT0iZm9udC1zaXplOi44cmVtO3VzZXItc2VsZWN0OmFs"
    "bCI+JHtlc2Moby5vdHB8fCJub3QgeWV0Iil9PC9jb2RlPgogICAgICAgICR7by5vdHA/YDxidXR0"
    "b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWNvcHk9InRleHQiIGRhdGEtY29weS12YWx1ZT0i"
    "JHtlc2Moby5vdHApfSI+Q29weTwvYnV0dG9uPmA6IiJ9PC9iPjwvZGl2PgogICAgICA8ZGl2IGNs"
    "YXNzPSJrdiI+PHNwYW4+U3RhdHVzPC9zcGFuPjxiPiR7by5sb2dpbj09PSJpbiIKICAgICAgICA/"
    "ICc8c3BhbiBjbGFzcz0icGlsbCBvbiI+TG9nZ2VkIGluIMK3IHlvdXJzIG9ubHk8L3NwYW4+Jwog"
    "ICAgICAgIDogKG8ud2luZG93PjAKICAgICAgICAgICAgPyAnPHNwYW4gY2xhc3M9InBpbGwiPkNv"
    "ZGVzIGZvcndhcmRpbmcgwrcgJytvLndpbmRvdysnIG1pbiBsZWZ0PC9zcGFuPicKICAgICAgICAg"
    "ICAgOiAnPHNwYW4gY2xhc3M9InBpbGwgb2ZmIj5IYW5kb3ZlciBjb21wbGV0ZTwvc3Bhbj4nKX08"
    "L2I+PC9kaXY+CiAgICA8L2Rpdj4KICAgICR7RygidGltZWxpbmUiLGA8ZGl2IGNsYXNzPSJ0bCI+"
    "CiAgICAgIDxkaXYgY2xhc3M9Im9rIj48Yj5QYWlkPC9iPjxzbWFsbD4ke28ud2hlbn08L3NtYWxs"
    "PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJvayI+PGI+RGVsaXZlcmVkPC9iPjxzbWFsbD5TZXNz"
    "aW9uIGhhbmRlZCBvdmVyIGluc3RhbnRseTwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9"
    "IiR7by5zdD09PSJEZWxpdmVyZWQiPyJvayI6IiJ9Ij48Yj5PVFAgcmVjZWl2ZWQ8L2I+PHNtYWxs"
    "PiR7by5zdD09PSJEZWxpdmVyZWQiPyJDb2RlIGZvcndhcmRlZCBpbi1hcHAiOiJXYWl0aW5nIn08"
    "L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2PjxiPkNsb3NlZDwvYj48c21hbGw+QXV0by1jbG9zZXMg"
    "YWZ0ZXIgdGhlIHdhcnJhbnR5IHdpbmRvdzwvc21hbGw+PC9kaXY+PC9kaXY+YCl9CiAgICA8ZGl2"
    "IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjExcHgiPgogICAgICA8YnV0dG9uIGNs"
    "YXNzPSJidG4gc20iIGRhdGEtb3RwPSIke28ubnVtfSI+R2V0IE9UUDwvYnV0dG9uPgogICAgICAk"
    "e0coInJlZG93bmxvYWQiLGA8YnV0dG9uIGNsYXNzPSJidG4gc20gc29mdCIgZGF0YS1vcmQ9ImRl"
    "dGFpbHMiPkRldGFpbHM8L2J1dHRvbj5gKX08L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIg"
    "c3R5bGU9Im1hcmdpbi10b3A6N3B4Ij4KICAgICAgJHtHKCJzZXNzaW9uRmlsZSIsYDxidXR0b24g"
    "Y2xhc3M9ImJ0biBzbSBzb2Z0IiBkYXRhLW9yZD0iZmlsZSI+JHtpYygiY29weSIpfSBGaWxlPC9i"
    "dXR0b24+YCl9CiAgICAgICR7RygicmVjZWlwdHMiLGA8YnV0dG9uIGNsYXNzPSJidG4gc20gc29m"
    "dCIgZGF0YS1vcmQ9InJlY2VpcHQiPlJlY2VpcHQ8L2J1dHRvbj5gKX0KICAgICAgJHsoby5sb2dp"
    "bj09PSJpbiIpPyIiOmA8YnV0dG9uIGNsYXNzPSJidG4gc20iIGRhdGEtbG9nZ2VkaW49IiR7by5p"
    "ZH0iPkknbSBsb2dnZWQgaW48L2J1dHRvbj5gfQogICAgICAke0coIndhcnJhbnR5IixgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIHNtIGdob3N0IiBkYXRhLWNsYWltLW9yZGVyPSIke28uaWR9Ij5SZXBvcnQg"
    "aXNzdWU8L2J1dHRvbj5gKX08L2Rpdj4KICAgICR7RygicmV2aWV3cyIsYDxkaXYgY2xhc3M9Imt2"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDo5cHgiPjxzcGFuPlJhdGUgdGhpcyBvcmRlcjwvc3Bhbj4KICAg"
    "ICAgPGI+PGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtcmF0ZT0iJHtvLmN9Ij5SYXRl"
    "PC9idXR0b24+PC9iPjwvZGl2PmApfQogIDwvZGl2PmApLmpvaW4oIiIpfQoKICA8ZGl2IGNsYXNz"
    "PSJjYXJkIiBzdHlsZT0idGV4dC1hbGlnbjpjZW50ZXIiPjxoMz5OZWVkIGhlbHAgd2l0aCBhbiBv"
    "cmRlcj88L2gzPgogICAgPHAgY2xhc3M9InN1YiI+JHtGRUFULmF1dG9SZWZ1bmQ/IkZhaWxlZCBs"
    "b2dpbnMgYXJlIHJlZnVuZGVkIGF1dG9tYXRpY2FsbHkgd2l0aGluIDEwIG1pbnV0ZXMuIjoiQ29u"
    "dGFjdCBzdXBwb3J0IGFuZCB3ZSB3aWxsIGxvb2sgaW50byBpdC4ifTwvcD4KICAgIDxkaXYgY2xh"
    "c3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGJ1dHRvbiBjbGFzcz0iYnRuIHNv"
    "ZnQiIGRhdGEtYWN0PSJzdXBwb3J0Ij5TdXBwb3J0PC9idXR0b24+CiAgICAke0coImRpc3B1dGUi"
    "LGA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtZGlzcHV0ZT5EaXNwdXRlPC9idXR0b24+"
    "YCl9PC9kaXY+PC9kaXY+YDsKfQoKZnVuY3Rpb24gdkVhcm4oKXsKICByZXR1cm4gYCR7Rygic3Ry"
    "ZWFrIixgPGRpdiBjbGFzcz0iY2FyZCI+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxp"
    "Z24taXRlbXM6Y2VudGVyO2dhcDoxMnB4Ij4KICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygi"
    "emFwIil9PC9kaXY+PGRpdiBzdHlsZT0iZmxleDoxIj48aDM+RGFpbHkgQ2hlY2staW48L2gzPjxw"
    "IGNsYXNzPSJzdWIiPkRheSAke0VBUk4uc3RyZWFrfSBzdHJlYWsgwrcgJHttb25leShFQVJOLmRh"
    "aWx5KX0gd2FpdGluZzwvcD48L2Rpdj48L2Rpdj4KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6Zmxl"
    "eDtnYXA6NnB4O21hcmdpbjoxNHB4IDAiPgogICAgICAke1sxLDIsMyw0LDUsNiw3XS5tYXAoZD0+"
    "YDxkaXYgc3R5bGU9ImZsZXg6MTtoZWlnaHQ6MzRweDtib3JkZXItcmFkaXVzOjlweDtkaXNwbGF5"
    "OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2ZvbnQtc2l6ZTouNjhyZW07Zm9udC13ZWlnaHQ6NzAw"
    "O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7JHtkPD0oRUFSTi5zdHJlYWt8fDApPyJiYWNr"
    "Z3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojMGIwZDE4O2JvcmRlci1jb2xvcjp0cmFuc3Bh"
    "cmVudCI6ImJhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigtLXR4MikifSI+JHtkfTwv"
    "ZGl2PmApLmpvaW4oIiIpfTwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBibG9jayIg"
    "ZGF0YS1jbGFpbSAke0VBUk4uY2xhaW1lZD8iZGlzYWJsZWQiOiIifT4ke0VBUk4uY2xhaW1lZD8i"
    "Q2xhaW1lZCB0b2RheSI6IkNsYWltICIrbW9uZXkoRUFSTi5kYWlseSl9PC9idXR0b24+PC9kaXY+"
    "YCl9CiAgJHtHKCJsb3lhbHR5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxveWFsdHkgUG9pbnRz"
    "PC9oMz48cCBjbGFzcz0ic3ViIj4xIHBvaW50IHBlciDigrkxMCBzcGVudC4gMTAwIHBvaW50cyA9"
    "IOKCuTEwIGJhbGFuY2UuPC9wPgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkJhbGFuY2U8L3Nw"
    "YW4+PGI+JHtFQVJOLnBvaW50c30gcHRzPC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPldvcnRoPC9zcGFuPjxiPiR7bW9uZXkoRUFSTi5wb2ludHNXb3J0aCl9PC9iPjwvZGl2Pgog"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBpZD0icmVkZWVtZ28iICR7RUFSTi5wb2ludHM+"
    "MD8iIjoiZGlzYWJsZWQifT5SZWRlZW0gUG9pbnRzPC9idXR0b24+PC9kaXY+YCl9CiAgJHthbnlG"
    "KCJyZWZEYXNoIiwicmVmTDIiKT9gPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkludml0ZSAmYW1wOyBF"
    "YXJuPC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPiR7RkVBVC5yZWZMMj8iNSUgZnJvbSBwZW9wbGUg"
    "eW91IGludml0ZSwgcGx1cyAxJSBmcm9tIHRoZSBwZW9wbGUgdGhleSBpbnZpdGUuIjoiMSUgb2Yg"
    "ZXZlcnkgZGVwb3NpdCB5b3VyIGludml0ZXMgZXZlciBtYWtlLiJ9PC9wPgogICAgPGRpdiBjbGFz"
    "cz0ia3YiPjxzcGFuPkludml0ZXM8L3NwYW4+PGI+JHtFQVJOLmludml0ZXN9PC9iPjwvZGl2Pgog"
    "ICAgJHtHKCJyZWZMMiIsYDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5MZXZlbCAxIGVhcm5pbmdzPC9z"
    "cGFuPjxiPiR7bW9uZXkoRUFSTi5sMSl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPkxldmVsIDIgZWFybmluZ3M8L3NwYW4+PGI+JHttb25leShFQVJOLmwyKX08L2I+PC9kaXY+"
    "YCl9CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VG90YWwgZWFybmVkPC9zcGFuPjxiPiR7bW9u"
    "ZXkoRUFSTi5lYXJuZWQpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Zb3Vy"
    "IGxpbms8L3NwYW4+PGIgc3R5bGU9ImZvbnQtc2l6ZTouNzJyZW0iPiR7ZXNjKEVBUk4ubGluayl9"
    "PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4"
    "Ij48YnV0dG9uIGNsYXNzPSJidG4iIGRhdGEtY29weT0icmVmIj4ke2ljKCJjb3B5Iil9IENvcHkg"
    "TGluazwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtY29weT0icmVm"
    "Ij5TaGFyZTwvYnV0dG9uPjwvZGl2PgogICAgJHtHKCJyZWZEYXNoIixgPGRpdiBjbGFzcz0ic2Vj"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDo2cHgiPjxoMiBzdHlsZT0iZm9udC1zaXplOi43OHJlbSI+UmVj"
    "ZW50IGludml0ZXM8L2gyPjwvZGl2PgogICAgJHtFQVJOLnJlY2VudC5sZW5ndGg/RUFSTi5yZWNl"
    "bnQubWFwKChbdSxhLGVdKT0+YDxkaXYgY2xhc3M9ImZlZWRsaW5lIj48Yj4ke2VzYyh1KX08L2I+"
    "ICR7ZXNjKGEpfTxzcGFuIHN0eWxlPSJtYXJnaW4tbGVmdDphdXRvO2NvbG9yOnZhcigtLW9rKSI+"
    "JHtlc2MoZSl9PC9zcGFuPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgIDonPHAgY2xhc3M9InN1YiIg"
    "c3R5bGU9Im1hcmdpbjowIj5Ob2JvZHkgaGFzIGpvaW5lZCB0aHJvdWdoIHlvdXIgbGluayB5ZXQu"
    "PC9wPid9CiAgICA8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjExcHggMCAwIj5SZWZlcnJh"
    "bCBlYXJuaW5ncyBsYW5kIGluIHlvdXIgYmFsYW5jZSBhdXRvbWF0aWNhbGx5IOKAlCBub3RoaW5n"
    "IHRvIGNsYWltLjwvcD5gKX0KICA8L2Rpdj5gOiIifQogICR7RygibGVhZGVyYm9hcmQiLGA8ZGl2"
    "IGNsYXNzPSJjYXJkIj48aDM+VGhpcyB3ZWVrJ3MgdG9wIGJ1eWVyczwvaDM+PHAgY2xhc3M9InN1"
    "YiI+TnVtYmVyIG9uZSB3aW5zIOKCuTUwMCBvbiBTdW5kYXkuPC9wPgogICAgJHtFQVJOLmxlYWRl"
    "cnMubGVuZ3RoP0VBUk4ubGVhZGVycy5tYXAoKFtyLHUsdl0saSk9PmA8ZGl2IGNsYXNzPSJsYiI+"
    "PHNwYW4gY2xhc3M9InJrICR7aT09PTA/ImciOiIifSI+JHtlc2Mocil9PC9zcGFuPgogICAgICAg"
    "IDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJl"
    "bSI+JHtlc2ModSl9PC9iPjwvZGl2PjxiIHN0eWxlPSJmb250LXNpemU6LjhyZW0iPiR7ZXNjKHYp"
    "fTwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJn"
    "aW46MCI+Tm8gcHVyY2hhc2VzIHlldCB0aGlzIHdlZWsuPC9wPid9PC9kaXY+YCl9CiAgJHtHKCJ3"
    "aW5iYWNrIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGln"
    "bi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgPHNwYW4gY2xhc3M9InBpbGwgaG90Ij4ke2lj"
    "KCJmaXJlIil9IENvbWViYWNrPC9zcGFuPgogICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0"
    "aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+MTAlIG9mZiDigJQgZXhwaXJl"
    "cyBpbiA0OCBob3VyczwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBpZD0ib2ZmZXJ1"
    "c2UiPlVzZTwvYnV0dG9uPjwvZGl2PmApfQogIDxkaXYgY2xhc3M9InNlYyI+PGgyPlRhc2tzPC9o"
    "Mj48L2Rpdj4KICAke0VBUk4udGFza3MubWFwKChbdCxyLGRvbmVdKT0+YDxkaXYgY2xhc3M9ImNh"
    "cmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMXB4"
    "Ij4KICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtkb25lP2ljKCJjaGVjayIpOmljKCJ6YXAiKX08"
    "L2Rpdj4KICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9u"
    "dC1zaXplOi44MnJlbSI+JHt0fTwvYj48YnI+PHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIp"
    "O2ZvbnQtc2l6ZTouN3JlbSI+UmV3YXJkICR7cn08L3NtYWxsPjwvZGl2PgogICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4geHMgJHtkb25lPyJzb2Z0IjoiIn0iICR7ZG9uZT8iZGlzYWJsZWQiOiIifSBk"
    "YXRhLWFjdD0idGFzayI+JHtkb25lPyJEb25lIjoiU3RhcnQifTwvYnV0dG9uPjwvZGl2PmApLmpv"
    "aW4oIiIpfWA7Cn0KCmZ1bmN0aW9uIHZXYWxsZXQoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9Imhl"
    "cm8iIHN0eWxlPSJ0ZXh0LWFsaWduOmNlbnRlciI+CiAgICA8ZGl2IGNsYXNzPSJraWNrIj4ke0NN"
    "Uy53YWxLaWNrfTwvZGl2PgogICAgPGgxIHN0eWxlPSJmb250LXNpemU6Mi4zcmVtIj7igrk8c3Bh"
    "biBpZD0iYmFsMiIgY2xhc3M9InRpY2siPiR7QkFMLnRvRml4ZWQoMil9PC9zcGFuPjwvaDE+CiAg"
    "ICA8cCBzdHlsZT0ibWFyZ2luOjAgYXV0byAxNHB4Ij4ke0NNUy53YWxTdWJ9PC9wPgogICAgPGRp"
    "diBzdHlsZT0ibWFyZ2luOjAgYXV0bzttYXgtd2lkdGg6MzAwcHgiPgogICAgICA8YnV0dG9uIGNs"
    "YXNzPSJidG4gY3RhIGJsb2NrIiBkYXRhLWRlcD4ke2ljKCJwbHVzIil9ICR7Q01TLndhbEFkZH08"
    "L2J1dHRvbj4KICAgICAgJHtHKCJjcnlwdG8iLGA8YnV0dG9uIGNsYXNzPSJidG4gZ2xhc3MgYmxv"
    "Y2siIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgZGF0YS1jcnlwdG8+UGF5IHdpdGggQ3J5cHRvPC9i"
    "dXR0b24+YCl9PC9kaXY+PC9kaXY+CiAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRp"
    "c3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgPHNwYW4gY2xhc3M9"
    "InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5BdXRvLXZlcmlmeSBPTjwvc3Bhbj4KICAgIDxk"
    "aXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigt"
    "LXR4MikiPlRvcC11cHMgYXJlIGNyZWRpdGVkIGFzIHNvb24gYXMgdGhleSBhcnJpdmU8L2Rpdj4K"
    "ICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWFjdD0ic3RhdHVzIj5TdGF0dXM8"
    "L2J1dHRvbj48L2Rpdj4KICAke0coImRlcEJvbnVzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlRv"
    "cC11cCBib251czwvaDM+PHAgY2xhc3M9InN1YiI+RXh0cmEgY3JlZGl0IGFkZGVkIGF1dG9tYXRp"
    "Y2FsbHkuPC9wPgogICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo3cHg7bWFyZ2luLXRv"
    "cDoxMHB4Ij4KICAgICAgJHtbWyLigrk1MDAiLCIrNSUiXSxbIuKCuTEwMDAiLCIrOCUiXSxbIuKC"
    "uTIwMDAiLCIrMTIlIl1dLm1hcCgoW2EsYl0pPT5gPGRpdiBzdHlsZT0iZmxleDoxO3RleHQtYWxp"
    "Z246Y2VudGVyO3BhZGRpbmc6OXB4IDRweDtib3JkZXItcmFkaXVzOjEycHg7Ym9yZGVyOjFweCBz"
    "b2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKSI+CiAgICAgICAgPGIgc3R5"
    "bGU9ImZvbnQtc2l6ZTouOHJlbTtkaXNwbGF5OmJsb2NrIj4ke2F9PC9iPjxzbWFsbCBzdHlsZT0i"
    "Zm9udC1zaXplOi42OHJlbTtjb2xvcjp2YXIoLS1vaykiPiR7Yn08L3NtYWxsPjwvZGl2PmApLmpv"
    "aW4oIiIpfTwvZGl2PjwvZGl2PmApfQogIDxkaXYgY2xhc3M9InNlYyI+PGgyPlF1aWNrIFRvcC11"
    "cDwvaDI+PC9kaXY+CiAgPGRpdiBjbGFzcz0iZ3JpZCIgc3R5bGU9ImdyaWQtdGVtcGxhdGUtY29s"
    "dW1uczpyZXBlYXQoMywxZnIpIj4KICAgICR7WzUwLDEwMCwyMDAsNTAwLDEwMDAsMjAwMF0ubWFw"
    "KGE9PmA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0YS1hbXQ9IiR7YX0iPuKCuSR7YX08L2J1"
    "dHRvbj5gKS5qb2luKCIiKX08L2Rpdj4KICAke0coImRlcExpbWl0cyIsYDxwIGNsYXNzPSJzdWIi"
    "IHN0eWxlPSJtYXJnaW46OXB4IDAgMDtmb250LXNpemU6LjdyZW0iPk1pbmltdW0g4oK5MTAgwrcg"
    "bWF4aW11bSDigrkyMCwwMDAgcGVyIHRvcC11cC48L3A+YCl9CiAgPGRpdiBjbGFzcz0iY2FyZCIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGgzPiR7Q01TLnBheVRpdGxlfTwvaDM+CiAgICA8cCBj"
    "bGFzcz0ic3ViIj4ke0NNUy5wYXlTdWJ9PC9wPgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlVQ"
    "SSBJRDwvc3Bhbj48Yj4ke2VzYyhTVE9SRS51cGl8fCJub3Qgc2V0IHlldCIpfTwvYj48L2Rpdj4K"
    "ICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5QYXllZTwvc3Bhbj48Yj4ke2VzYyhTVE9SRS5wYXll"
    "ZXx8IuKAlCIpfTwvYj48L2Rpdj4KICAgICR7RygidXBpUm90YXRlIixgPGRpdiBjbGFzcz0ia3Yi"
    "PjxzcGFuPkhhbmRsZXMgaW4gcm90YXRpb248L3NwYW4+PGI+MyBhY3RpdmU8L2I+PC9kaXY+YCl9"
    "CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+UGF5ZWU8L3NwYW4+PGI+WW91ciBTdG9yZSBOYW1l"
    "PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkF1dG8tdmVyaWZ5PC9zcGFuPjxi"
    "IHN0eWxlPSJjb2xvcjp2YXIoLS1vaykiPkVuYWJsZWQ8L2I+PC9kaXY+CiAgICAke0coIndyb25n"
    "QW10IixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPldyb25nIGFtb3VudDwvc3Bhbj48Yj5DcmVkaXRl"
    "ZCBhcyBwYWlkPC9iPjwvZGl2PmApfQogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIHNvZnQi"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEtY29weT0idXBpIj4ke2ljKCJjb3B5Iil9IENv"
    "cHkgVVBJIElEPC9idXR0b24+PC9kaXY+CiAgJHtHKCJhYmFuZG9uUmVtaW5kIixgPHAgY2xhc3M9"
    "InN1YiIgc3R5bGU9Im1hcmdpbjoxMHB4IDAgMDtmb250LXNpemU6LjdyZW0iPldlIHdpbGwgcmVt"
    "aW5kIHlvdSBiZWZvcmUgYW4gdW5maW5pc2hlZCBwYXltZW50IGV4cGlyZXMuPC9wPmApfQogIDxk"
    "aXYgY2xhc3M9InNlYyI+PGgyPiR7RkVBVC5sZWRnZXI/IlN0YXRlbWVudCI6Ikhpc3RvcnkifTwv"
    "aDI+JHtHKCJsZWRnZXIiLGA8YSBkYXRhLWFkbWluLWFjdD0iZXhwb3J0Ij5FeHBvcnQ8L2E+YCl9"
    "PC9kaXY+CiAgJHtUWC5tYXAoKHQsaSk9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0i"
    "ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTFweCI+CiAgICA8ZGl2IGNsYXNz"
    "PSJmbGFnIj4ke3QuYVswXT09PSIrIj9pYygiZG93biIpOmljKCJ1cCIpfTwvZGl2PgogICAgPGRp"
    "diBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44MnJlbSI+"
    "JHt0LnR9PC9iPjxicj48c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi43"
    "cmVtIj4ke3Qud308L3NtYWxsPjwvZGl2PgogICAgPGRpdiBzdHlsZT0idGV4dC1hbGlnbjpyaWdo"
    "dCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODVyZW07Y29sb3I6JHt0LmFbMF09PT0iKyI/InZhcigt"
    "LW9rKSI6InZhcigtLXR4KSJ9Ij4ke3QuYX08L2I+CiAgICAke0coImxlZGdlciIsYDxicj48c21h"
    "bGwgc3R5bGU9ImZvbnQtc2l6ZTouNjVyZW07Y29sb3I6dmFyKC0tdHgyKSI+YmFsIOKCuSR7KDI0"
    "OC41K2kqNykudG9GaXhlZCgyKX08L3NtYWxsPmApfTwvZGl2PjwvZGl2PmApLmpvaW4oIiIpfWA7"
    "Cn0KCmZ1bmN0aW9uIHZZb3UoKXsKICBjb25zdCBtZT1NRXx8e307CiAgY29uc3Qgc3BlbnQ9bWUu"
    "c3BlbnR8fDAsIHRhcmdldD1zcGVudD49NTAwPzIwMDA6NTAwOwogIGNvbnN0IHBjdD1NYXRoLm1h"
    "eCg0LE1hdGgubWluKDEwMCxNYXRoLnJvdW5kKHNwZW50L3RhcmdldCoxMDApKSk7CiAgY29uc3Qg"
    "YXY9bWUucGhvdG8/YDxpbWcgc3JjPSIke21lLnBob3RvfSIgYWx0PSIiIHN0eWxlPSJ3aWR0aDo2"
    "NHB4O2hlaWdodDo2NHB4O2JvcmRlci1yYWRpdXM6NTAlO29iamVjdC1maXQ6Y292ZXI7ZGlzcGxh"
    "eTpibG9jazttYXJnaW46MCBhdXRvIDEwcHgiIG9uZXJyb3I9InRoaXMucmVwbGFjZVdpdGgoT2Jq"
    "ZWN0LmFzc2lnbihkb2N1bWVudC5jcmVhdGVFbGVtZW50KCdkaXYnKSx7Y2xhc3NOYW1lOidmbGFn"
    "JyxzdHlsZTond2lkdGg6NjRweDtoZWlnaHQ6NjRweDttYXJnaW46MCBhdXRvIDEwcHgnfSkpIj5g"
    "CiAgICA6YDxkaXYgY2xhc3M9ImZsYWciIHN0eWxlPSJ3aWR0aDo2NHB4O2hlaWdodDo2NHB4O21h"
    "cmdpbjowIGF1dG8gMTBweCI+JHtpYygidXNlciIpfTwvZGl2PmA7CiAgcmV0dXJuIGA8ZGl2IGNs"
    "YXNzPSJjYXJkIHNoaW5lY2FyZCB0aWx0IiBkYXRhLXRpbHQgc3R5bGU9InRleHQtYWxpZ246Y2Vu"
    "dGVyIj48c3BhbiBjbGFzcz0iZ2xhcmUiPjwvc3Bhbj4KICAgICR7YXZ9CiAgICA8aDMgc3R5bGU9"
    "ImZvbnQtc2l6ZToxLjA1cmVtIj4ke2VzYyhtZS5uYW1lfHwiWW91Iil9PC9oMz4KICAgIDxwIGNs"
    "YXNzPSJzdWIiPiR7ZXNjKG1lLnVzZXJuYW1lfHwoIklEICIrKG1lLmlkfHwi4oCUIikpKX0ke21l"
    "LnVzZXJuYW1lPyIgwrcgSUQgIisobWUuaWR8fCLigJQiKToiIn0ke21lLmpvaW5lZD8iIMK3IGpv"
    "aW5lZCAiK2VzYyhtZS5qb2luZWQpOiIifTwvcD4KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6Zmxl"
    "eDtnYXA6N3B4O2p1c3RpZnktY29udGVudDpjZW50ZXI7bWFyZ2luLXRvcDoxMHB4O2ZsZXgtd3Jh"
    "cDp3cmFwIj4KICAgICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPiR7ZXNjKG1lLnRpZXJ8fCJCcm9u"
    "emUiKX08L3NwYW4+PHNwYW4gY2xhc3M9InBpbGwiPiR7bWUub3JkZXJzfHwwfSBvcmRlcnM8L3Nw"
    "YW4+CiAgICAgIDxzcGFuIGNsYXNzPSJwaWxsIj4ke21vbmV5KG1lLmJhbGFuY2V8fDApfTwvc3Bh"
    "bj4KICAgICAgJHtHKCJsb3lhbHR5IixgPHNwYW4gY2xhc3M9InBpbGwiPiR7bWUucG9pbnRzfHww"
    "fSBwdHM8L3NwYW4+YCl9CiAgICAgICR7Rygic3RyZWFrIixgPHNwYW4gY2xhc3M9InBpbGwiPiR7"
    "bWUuc3RyZWFrfHwwfS1kYXkgc3RyZWFrPC9zcGFuPmApfQogICAgICAkeyhGRUFULmJ1eWVyQmFk"
    "Z2UmJihtZS5vcmRlcnN8fDApPj01KT9gPHNwYW4gY2xhc3M9InBpbGwgb24iPiR7aWMoImNoZWNr"
    "Iil9IFZlcmlmaWVkIGJ1eWVyPC9zcGFuPmA6IiJ9CiAgICAgICR7KG1lLmRlYnQ+MCk/YDxzcGFu"
    "IGNsYXNzPSJwaWxsIGhvdCI+JHttb25leShtZS5kZWJ0KX0gdWRoYXI8L3NwYW4+YDoiIn08L2Rp"
    "dj4KICAgIDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6MTRweDtoZWlnaHQ6N3B4O2JvcmRlci1yYWRp"
    "dXM6OXB4O2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO292ZXJmbG93OmhpZGRlbiI+CiAgICAgIDxp"
    "IHN0eWxlPSJkaXNwbGF5OmJsb2NrO2hlaWdodDoxMDAlO3dpZHRoOiR7cGN0fSU7YmFja2dyb3Vu"
    "ZDp2YXIoLS1waWxsZ3JhZCkiPjwvaT48L2Rpdj4KICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJt"
    "YXJnaW4tdG9wOjdweDtmb250LXNpemU6LjcycmVtIj4ke21lLm5leHRfdGllcj4wCiAgICAgID8g"
    "bW9uZXkobWUubmV4dF90aWVyKSsiIG1vcmUgc3BlbnQgdG8gcmVhY2ggIisoc3BlbnQ+PTUwMD8i"
    "UGxhdGludW0iOiJHb2xkIikKICAgICAgOiAiVG9wIHRpZXIgcmVhY2hlZCJ9PC9wPjwvZGl2Pgog"
    "ICR7Rygid2lzaGxpc3QiLGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5TYXZlZCBjb3VudHJpZXM8L2gy"
    "PjwvZGl2PgogICAgPGRpdiBjbGFzcz0iY2FyZCI+JHtXSVNILmxlbmd0aD9XSVNILm1hcChpPT5g"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7U1RPQ0tbaV0uZn0gJHtlc2MoU1RPQ0tbaV0uYyl9ICR7"
    "U1RPQ0tbaV0uY29kZX08L3NwYW4+CiAgICAgIDxiPjxidXR0b24gY2xhc3M9ImJ0biB4cyIgZGF0"
    "YS1idXk9IiR7aX0iPkJ1eTwvYnV0dG9uPjwvYj48L2Rpdj5gKS5qb2luKCIiKTonPHAgY2xhc3M9"
    "InN1YiIgc3R5bGU9Im1hcmdpbjowIj5UYXAgdGhlIGhlYXJ0IG9uIGFueSBjb3VudHJ5IHRvIHNh"
    "dmUgaXQgaGVyZS48L3A+J308L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5BY2NvdW50"
    "PC9oMj48L2Rpdj4KICA8ZGl2IGNsYXNzPSJjYXJkIj4ke1tbIk9yZGVyIGhpc3RvcnkiLCJvcmRl"
    "cnMiXSxbIldhbGxldCAmIHBheW1lbnRzIiwid2FsbGV0Il1dCiAgICAuY29uY2F0KGFueUYoInJl"
    "ZkRhc2giLCJyZWZMMiIpP1tbIlJlZmVycmFsIHByb2dyYW0iLCJlYXJuIl1dOltdKQogICAgLm1h"
    "cCgoW3QsZ10pPT5gPGRpdiBjbGFzcz0ia3YiIHN0eWxlPSJjdXJzb3I6cG9pbnRlciIgZGF0YS1n"
    "bz0iJHtnfSI+PHNwYW4gc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHt0fTwvc3Bhbj48Yj4ke2lj"
    "KCJjaGV2Iil9PC9iPjwvZGl2PmApLmpvaW4oIiIpfQogICAgJHtHKCJpMThuIixgPGRpdiBjbGFz"
    "cz0ia3YiPjxzcGFuIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPkxhbmd1YWdlPC9zcGFuPjxiPgog"
    "ICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIHN0eWxlPSJoZWlnaHQ6MzJweDt3aWR0aDphdXRvO2Zv"
    "bnQtc2l6ZTouNzRyZW0iPjxvcHRpb24+RW5nbGlzaDwvb3B0aW9uPjxvcHRpb24+4KS54KS/4KSo"
    "4KWN4KSm4KWAPC9vcHRpb24+PC9zZWxlY3Q+PC9iPjwvZGl2PmApfTwvZGl2PgogICR7Rygibm90"
    "aWZQcmVmcyIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPk5vdGlmaWNhdGlvbnM8L2gyPjwvZGl2Pgog"
    "IDxkaXYgY2xhc3M9ImNhcmQiPgogICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+"
    "UmVzdG9jayBhbGVydHM8c21hbGw+RE0gbWUgd2hlbiBteSBjb3VudHJ5IGlzIGJhY2s8L3NtYWxs"
    "PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyAke1BSRUZTLnJlc3RvY2s/Im9uIjoiIn0iIGRh"
    "dGEtcHJlZj0icmVzdG9jayI+PC9kaXY+PC9kaXY+CiAgICAke0coInJlY2VpcHRzIixgPGRpdiBj"
    "bGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+T3JkZXIgcmVjZWlwdHM8c21hbGw+U2VuZCBhIHJl"
    "Y2VpcHQgYWZ0ZXIgZWFjaCBidXk8L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyAk"
    "e1BSRUZTLnJlY2VpcHRzPyJvbiI6IiJ9IiBkYXRhLXByZWY9InJlY2VpcHRzIj48L2Rpdj48L2Rp"
    "dj5gKX0KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPlByb21vIG1lc3NhZ2Vz"
    "PHNtYWxsPkZsYXNoIHNhbGVzIGFuZCBnaXZlYXdheXM8L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2"
    "IGNsYXNzPSJ0ZyAke1BSRUZTLnByb21vcz8ib24iOiIifSIgZGF0YS1wcmVmPSJwcm9tb3MiPjwv"
    "ZGl2PjwvZGl2PgogICAgJHtHKCJwdXNoTm90aWYiLGA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNs"
    "YXNzPSJ0Ij5NaW5pIGFwcCBwdXNoPHNtYWxsPk1lc3NhZ2UgbWUgd2hlbiBzb21ldGhpbmcgaGFw"
    "cGVuczwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InRnICR7UFJFRlMucHVzaD8ib24i"
    "OiIifSIgZGF0YS1wcmVmPSJwdXNoIj48L2Rpdj48L2Rpdj5gKX0KICA8L2Rpdj5gKX0KICA8ZGl2"
    "IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxidXR0b24gY2xhc3M9ImJ0"
    "biBzb2Z0IiBpZD0ic3VwcG9ydGdvIj5TdXBwb3J0PC9idXR0b24+CiAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIGdob3N0IiBpZD0idGVybXNnbyI+VGVybXM8L2J1dHRvbj48L2Rpdj5gOwp9CgpmdW5jdGlv"
    "biBiaW5kQnV5KCl7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtd2lzaF0iKS5m"
    "b3JFYWNoKGVsPT5lbC5vbmNsaWNrPWU9PnsKICAgIGUuc3RvcFByb3BhZ2F0aW9uKCk7Y29uc3Qg"
    "aT0rZWwuZGF0YXNldC53aXNoOwogICAgY29uc3Qgaz1XSVNILmluZGV4T2YoaSk7aWYoazwwKVdJ"
    "U0gucHVzaChpKTtlbHNlIFdJU0guc3BsaWNlKGssMSk7CiAgICB0b2FzdChrPDA/IlNhdmVkIHRv"
    "IHdpc2hsaXN0IjoiUmVtb3ZlZCBmcm9tIHdpc2hsaXN0Iik7YXBwbHlGaWx0ZXIoKTt9KTsKICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1idXldIikuZm9yRWFjaChlbD0+ZWwub25j"
    "bGljaz1lPT57CiAgICBpZihlLnRhcmdldC5jbG9zZXN0KCJbZGF0YS10b2FzdF0sW2RhdGEtd2lz"
    "aF0iKSlyZXR1cm47Y29uc3Qgcz1TVE9DS1srZWwuZGF0YXNldC5idXldOwogICAgaWYocy5xdHk9"
    "PT0wJiYhZmFsc2Upe3RvYXN0KCJBbGVydCBzZXQg4oCUIHlvdSdsbCBnZXQgYSBETSBvbiByZXN0"
    "b2NrIik7cmV0dXJufQogICAgYnV5U2hlZXQocyxzLnF0eT09PTApO30pOwp9CmxldCBRVFk9MSxD"
    "T1VQT049MDsKZnVuY3Rpb24gYnV5U2hlZXQocyxwcmUpewogIFFUWT0xO0NPVVBPTj0wOwogIGNv"
    "bnN0IG1heD1GRUFULmNhcnR8fEZFQVQuYnVsa0Rpc2M/TWF0aC5tYXgoMSxNYXRoLm1pbigyNSxz"
    "LnF0eXx8NSkpOjE7CiAgc2hlZXQoYDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7Z2FwOjEycHg7bWFyZ2luLWJvdHRvbToxNHB4Ij4KICAgICAgPGRpdiBjbGFzcz0i"
    "ZmxhZyIgc3R5bGU9IndpZHRoOjUwcHg7aGVpZ2h0OjUwcHg7Zm9udC1zaXplOjEuNXJlbSI+JHtz"
    "LmZ9PC9kaXY+CiAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MSI+PGgzIHN0eWxlPSJtYXJnaW46MDtm"
    "b250LXNpemU6MXJlbSI+JHtlc2Mocy5jKX0gQWNjb3VudDwvaDM+CiAgICAgIDxwIGNsYXNzPSJz"
    "dWIiIHN0eWxlPSJtYXJnaW46MCI+JHtzLmNvZGV9IMK3ICR7cy5xdHkrIiBpbiBzdG9jayDCtyBp"
    "bnN0YW50In08L3A+CiAgICAgICR7RygicmV2aWV3cyIsYDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6"
    "NHB4Ij4ke3N0YXJzKHMuYyl9PC9kaXY+YCl9PC9kaXY+PC9kaXY+CiAgICAke0coImhlYWx0aENo"
    "ZWNrIixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlNlc3Npb24gaGVhbHRoPC9zcGFuPjxiIHN0eWxl"
    "PSJjb2xvcjp2YXIoLS1vaykiPiR7aWMoImNoZWNrIil9IGNoZWNrZWQgbGl2ZTwvYj48L2Rpdj5g"
    "KX0KICAgICR7RygidmFyaWFudHMiLGA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VmFyaWFudDwvc3Bh"
    "bj48Yj4KICAgICAgPHNlbGVjdCBjbGFzcz0iaW5wIiBzdHlsZT0iaGVpZ2h0OjMycHg7d2lkdGg6"
    "YXV0bztmb250LXNpemU6Ljc0cmVtIj48b3B0aW9uPk5vIDJGQTwvb3B0aW9uPjxvcHRpb24+MkZB"
    "IG9uICgr4oK5NSk8L29wdGlvbj48b3B0aW9uPldpdGggbWFpbCAoK+KCuTgpPC9vcHRpb24+PC9z"
    "ZWxlY3Q+PC9iPjwvZGl2PmApfQogICAgJHsoRkVBVC5jYXJ0fHxGRUFULmJ1bGtEaXNjKT9gPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPlF1YW50aXR5PC9zcGFuPjxiPgogICAgICA8c3BhbiBjbGFzcz0i"
    "cXR5Ij48YnV0dG9uIGlkPSJxbSI+4oiSPC9idXR0b24+PGIgaWQ9InFuIj4xPC9iPjxidXR0b24g"
    "aWQ9InFwIj4rPC9idXR0b24+PC9zcGFuPjwvYj48L2Rpdj5gOiIifQogICAgJHtHKCJidWxrRGlz"
    "YyIsYDxwIGNsYXNzPSJzdWIiIGlkPSJiZG5vdGUiIHN0eWxlPSJtYXJnaW46NnB4IDAgMDtmb250"
    "LXNpemU6LjdyZW0iPkJ1eSA1KyBmb3IgNSUgb2ZmIMK3IDEwKyBmb3IgMTAlIG9mZiDCtyAyNSsg"
    "Zm9yIDE1JSBvZmYuPC9wPmApfQogICAgJHtHKCJjb3Vwb25zIixgPGRpdiBjbGFzcz0iZmllbGQi"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxsYWJlbD5Db3Vwb24gY29kZTwvbGFiZWw+CiAgICAg"
    "IDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4Ij48aW5wdXQgY2xhc3M9ImlucCIgaWQ9"
    "ImNwbiIgcGxhY2Vob2xkZXI9IldFTENPTUUxMCI+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBz"
    "bSIgaWQ9ImNwbmFwcGx5IiBzdHlsZT0iZmxleDowIDAgYXV0byI+QXBwbHk8L2J1dHRvbj48L2Rp"
    "dj48L2Rpdj5gKX0KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5QcmljZTwvc3Bhbj48YiBpZD0i"
    "YnByaWNlIj4ke21vbmV5KHMucHJpY2UpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48"
    "c3Bhbj5Zb3VyIGJhbGFuY2U8L3NwYW4+PGI+JHttb25leShCQUwpfTwvYj48L2Rpdj4KICAgIDxk"
    "aXYgY2xhc3M9Imt2Ij48c3Bhbj5BZnRlciBwdXJjaGFzZTwvc3Bhbj48YiBpZD0iYmFmdGVyIj4k"
    "e21vbmV5KE1hdGgubWF4KDAsQkFMLXMucHJpY2UpKX08L2I+PC9kaXY+CiAgICA8ZGl2IGlkPSJz"
    "cGxpdG5vdGUiPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRv"
    "cDoxNHB4Ij48YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtY2xvc2U+Q2FuY2VsPC9idXR0"
    "b24+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJiY29uZmlybSI+Q29uZmlybSBC"
    "dXk8L2J1dHRvbj48L2Rpdj4KICAgICR7RygiY2FydCIsYDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0"
    "IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGlkPSJiYWRkY2FydCI+QWRkIHRvIGNh"
    "cnQ8L2J1dHRvbj5gKX1gKTsKICBjb25zdCBnPWlkPT5kb2N1bWVudC5nZXRFbGVtZW50QnlJZChp"
    "ZCk7CiAgZnVuY3Rpb24gdG90YWwoKXsKICAgIGxldCB1bml0PXMucHJpY2UsIHE9UVRZLCB0PXVu"
    "aXQqcTsKICAgIGlmKEZFQVQuYnVsa0Rpc2Mpe2NvbnN0IGQ9cT49MjU/LjE1OnE+PTEwPy4xMDpx"
    "Pj01Py4wNTowO3Q9dCooMS1kKTt9CiAgICBpZihGRUFULmNvdXBvbnMmJkNPVVBPTil0PXQqKDEt"
    "Q09VUE9OLzEwMCk7CiAgICByZXR1cm4gTWF0aC5yb3VuZCh0KjEwMCkvMTAwOwogIH0KICBmdW5j"
    "dGlvbiBwYWludCgpewogICAgY29uc3QgdD10b3RhbCgpOwogICAgZygiYnByaWNlIikudGV4dENv"
    "bnRlbnQ9bW9uZXkodCk7CiAgICBnKCJiYWZ0ZXIiKS50ZXh0Q29udGVudD1tb25leShNYXRoLm1h"
    "eCgwLEJBTC10KSk7CiAgICBjb25zdCBzbj1nKCJzcGxpdG5vdGUiKTsKICAgIGlmKEZFQVQuc3Bs"
    "aXRQYXkmJnQ+QkFMKXtzbi5pbm5lckhUTUw9YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5TcGxpdCBw"
    "YXltZW50PC9zcGFuPjxiPiR7bW9uZXkoQkFMKX0gd2FsbGV0ICsgJHttb25leSh0LUJBTCl9IFVQ"
    "STwvYj48L2Rpdj5gO30KICAgIGVsc2Ugc24uaW5uZXJIVE1MPSIiOwogICAgaWYoZygicW4iKSln"
    "KCJxbiIpLnRleHRDb250ZW50PVFUWTsKICB9CiAgaWYoZygicW0iKSlnKCJxbSIpLm9uY2xpY2s9"
    "KCk9PntRVFk9TWF0aC5tYXgoMSxRVFktMSk7cGFpbnQoKTt9OwogIGlmKGcoInFwIikpZygicXAi"
    "KS5vbmNsaWNrPSgpPT57UVRZPU1hdGgubWluKG1heCxRVFkrMSk7cGFpbnQoKTt9OwogIGlmKGco"
    "ImNwbiIpJiZPRkZFUiYmIWcoImNwbiIpLnZhbHVlKWcoImNwbiIpLnZhbHVlPU9GRkVSOwogIGlm"
    "KGcoImNwbmFwcGx5IikpZygiY3BuYXBwbHkiKS5vbmNsaWNrPSgpPT57CiAgICBjb25zdCB2PShn"
    "KCJjcG4iKS52YWx1ZXx8IiIpLnRyaW0oKS50b1VwcGVyQ2FzZSgpOwogICAgaWYoIXYpe3RvYXN0"
    "KCJFbnRlciBhIGNvdXBvbiBjb2RlIik7cmV0dXJufQogICAgQ09VUE9OPXY9PT0iV0VMQ09NRTEw"
    "Ij8xMDp2PT09IlNBVkUyMCI/MjA6MDsKICAgIHRvYXN0KENPVVBPTj9DT1VQT04rIiUgY291cG9u"
    "IGFwcGxpZWQiOiJDb3Vwb24gbm90IHZhbGlkIik7cGFpbnQoKTt9OwogIGlmKGcoImJhZGRjYXJ0"
    "IikpZygiYmFkZGNhcnQiKS5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKExJVkUpewogICAgICBj"
    "b25zdCBkPWF3YWl0IGFwaVRyeSgiL2FwaS9jYXJ0Iix7YWN0aW9uOiJhZGQiLGNvdW50cnk6cy5j"
    "LHF0eTpRVFl9KTsKICAgICAgaWYoIWQpcmV0dXJuOwogICAgICBjbG9zZVNoZWV0KCk7dG9hc3Qo"
    "UVRZKyIgw5cgIitzLmMrIiBhZGRlZCB0byBjYXJ0Iik7YXdhaXQgcmVmcmVzaCgpO3JldHVybjsK"
    "ICAgIH0KICAgIGZvcihsZXQgaT0wO2k8UVRZO2krKylDQVJULnB1c2gocyk7YXBwbHlGZWF0dXJl"
    "cygpO2Nsb3NlU2hlZXQoKTt0b2FzdChRVFkrIiDDlyAiK3MuYysiIGFkZGVkIHRvIGNhcnQiKTt9"
    "OwogIGcoImJjb25maXJtIikub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCB0PXRvdGFsKCk7"
    "CiAgICBpZihCQUw8dCYmIUZFQVQuc3BsaXRQYXkpe3RvYXN0KCJOb3QgZW5vdWdoIGJhbGFuY2Ug"
    "4oCUIGFkZCBmdW5kcyIpO3JldHVybn0KICAgIGlmKEJBTDx0JiZGRUFULnNwbGl0UGF5KXtjbG9z"
    "ZVNoZWV0KCk7dG9hc3QoIlBheWluZyAiK21vbmV5KHQtQkFMKSsiIGJ5IFVQSSIpO2RlcG9zaXRT"
    "aGVldChNYXRoLmNlaWwodC1CQUwpKTtyZXR1cm59CiAgICBpZihMSVZFKXsKICAgICAgY29uc3Qg"
    "YnRuPWcoImJjb25maXJtIik7YnRuLmRpc2FibGVkPXRydWU7YnRuLnRleHRDb250ZW50PSJXb3Jr"
    "aW5n4oCmIjsKICAgICAgY29uc3QgY29kZT1nKCJjcG4iKT8oZygiY3BuIikudmFsdWV8fCIiKS50"
    "cmltKCk6IiI7CiAgICAgIGNvbnN0IGQ9YXdhaXQgYXBpVHJ5KCIvYXBpL2J1eSIse2NvdW50cnk6"
    "cy5jLHF0eTpRVFksY291cG9uOmNvZGV9KTsKICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50"
    "ZXh0Q29udGVudD0iQ29uZmlybSBCdXkiOwogICAgICBpZighZClyZXR1cm47CiAgICAgIGlmKHR5"
    "cGVvZiBkLmJhbGFuY2U9PT0ibnVtYmVyIilCQUw9ZC5iYWxhbmNlOwogICAgICBzeW5jQmFsKCk7"
    "Y2xvc2VTaGVldCgpO2J1cnN0KCk7CiAgICAgIGNvbnN0IGZpcnN0PShkLm9yZGVycyYmZC5vcmRl"
    "cnNbMF0pfHxudWxsOwogICAgICB0b2FzdChmaXJzdD8oIkRlbGl2ZXJlZCDigJQgIitmaXJzdC5p"
    "ZCk6IlB1cmNoYXNlZCDigJQgZGV0YWlscyBpbiBPcmRlcnMiKTsKICAgICAgYXdhaXQgcmVmcmVz"
    "aCgpOwogICAgICBpZihmaXJzdCYmZmlyc3QucGF5bG9hZClzZXRUaW1lb3V0KCgpPT5zaGVldChg"
    "PGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+JHtlc2MoZmlyc3QuaWQp"
    "fTwvaDM+CiAgICAgICAgPHAgY2xhc3M9InN1YiI+S2VlcCB0aGlzIHNhZmUuIEl0IGlzIGFsc28g"
    "aW4gT3JkZXJzLjwvcD4KICAgICAgICA8ZGl2IGNsYXNzPSJrdiIgc3R5bGU9ImRpc3BsYXk6Ymxv"
    "Y2siPjxjb2RlIHN0eWxlPSJmb250LXNpemU6LjcycmVtO3dvcmQtYnJlYWs6YnJlYWstYWxsIj4k"
    "e2VzYyhmaXJzdC5wYXlsb2FkKX08L2NvZGU+PC9kaXY+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4IiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRv"
    "bj5gKSwzNTApOwogICAgICByZXR1cm47CiAgICB9CiAgICBCQUwtPXQ7c3luY0JhbCgpO2Nsb3Nl"
    "U2hlZXQoKTtidXJzdCgpOwogICAgdG9hc3QoIlB1cmNoYXNlZCDigJQgT1RQIGlzIGluIE9yZGVy"
    "cyIpOwogICAgaWYoRkVBVC5wdXNoTm90aWYpc2V0VGltZW91dCgoKT0+dG9hc3QoIkJvdCBub3Rp"
    "ZmljYXRpb24gc2VudCB0byB5b3VyIGNoYXQiKSwxNTAwKTt9OwogIHBhaW50KCk7Cn0KZnVuY3Rp"
    "b24gY2FydFNoZWV0KCl7CiAgaWYoIUNBUlQubGVuZ3RoKXt0b2FzdCgiQ2FydCBpcyBlbXB0eSIp"
    "O3JldHVybn0KICBjb25zdCB0PUNBUlQucmVkdWNlKChhLGIpPT5hK2IucHJpY2UsMCk7CiAgc2hl"
    "ZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPllvdXIgY2FydDwv"
    "aDM+CiAgICA8cCBjbGFzcz0ic3ViIj4ke0NBUlQubGVuZ3RofSBpdGVtJHtDQVJULmxlbmd0aD4x"
    "PyJzIjoiIn0g4oCUIG9uZSBjb25maXJtYXRpb24sIG9uZSBkZWJpdC48L3A+CiAgICAke0NBUlQu"
    "bWFwKChzLGkpPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7cy5mfSAke2VzYyhzLmMpfSAke3Mu"
    "Y29kZX08L3NwYW4+CiAgICAgIDxiPiR7bW9uZXkocy5wcmljZSl9IDxidXR0b24gY2xhc3M9ImJ0"
    "biB4cyBnaG9zdCIgZGF0YS1jZGVsPSIke2l9Ij7DlzwvYnV0dG9uPjwvYj48L2Rpdj5gKS5qb2lu"
    "KCIiKX0KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Ub3RhbDwvc3Bhbj48Yj4ke21vbmV5KHQp"
    "fTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTRw"
    "eCI+PGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+CiAg"
    "ICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJjYXJ0YnV5Ij5DaGVja291dDwvYnV0dG9u"
    "PjwvZGl2PmApOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNkZWxdIikuZm9y"
    "RWFjaChiPT5iLm9uY2xpY2s9KCk9PntDQVJULnNwbGljZSgrYi5kYXRhc2V0LmNkZWwsMSk7YXBw"
    "bHlGZWF0dXJlcygpO2NhcnRTaGVldCgpO30pOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJj"
    "YXJ0YnV5Iikub25jbGljaz0oKT0+ewogICAgaWYoQkFMPHQpe3RvYXN0KCJOb3QgZW5vdWdoIGJh"
    "bGFuY2Ug4oCUIGFkZCBmdW5kcyIpO3JldHVybn0KICAgIEJBTC09dDtDQVJUPVtdO3N5bmNCYWwo"
    "KTthcHBseUZlYXR1cmVzKCk7Y2xvc2VTaGVldCgpO2J1cnN0KCk7dG9hc3QoIk9yZGVyIHBsYWNl"
    "ZCDigJQgT1RQcyBhcmUgaW4gT3JkZXJzIik7fTsKfQpmdW5jdGlvbiBnb1RhYih0KXsKICBjb25z"
    "dCBiPWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoYCN0YWJzIGJ1dHRvbltkYXRhLXRhYj0iJHt0fSJd"
    "YCk7CiAgaWYoYiliLmNsaWNrKCk7Cn0KZnVuY3Rpb24gYmluZENvbW1vbigpewogIGRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLW9yZF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3lu"
    "YygpPT57CiAgICBjb25zdCB3aGF0PWIuZGF0YXNldC5vcmQ7CiAgICBjb25zdCBjYXJkPWIuY2xv"
    "c2VzdCgiW2RhdGEtb3JkZXItaWRdIik7CiAgICBjb25zdCBvaWQ9KGNhcmQmJmNhcmQuZGF0YXNl"
    "dC5vcmRlcklkKXx8KE9SREVSU1swXSYmT1JERVJTWzBdLmlkKXx8IiI7CiAgICBpZighb2lkKXt0"
    "b2FzdCgiTm8gb3JkZXIgc2VsZWN0ZWQiKTtyZXR1cm47fQogICAgaWYoIUxJVkUpe3RvYXN0KCJQ"
    "cmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgY29uc3QgbGJsPWIudGV4dENvbnRlbnQ7Yi5kaXNh"
    "YmxlZD10cnVlO2IudGV4dENvbnRlbnQ9IuKApiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgi"
    "L2FwaS9vcmRlciIse2lkOm9pZCx3aGF0OndoYXR9KTsKICAgIGIuZGlzYWJsZWQ9ZmFsc2U7Yi50"
    "ZXh0Q29udGVudD1sYmw7CiAgICBpZighcilyZXR1cm47CiAgICBpZih3aGF0PT09ImRldGFpbHMi"
    "KXRvYXN0KCJMb2dpbiBkZXRhaWxzIHNlbnQgdG8geW91ciBjaGF0Iik7CiAgICBpZih3aGF0PT09"
    "ImZpbGUiKXRvYXN0KCJTZXNzaW9uIGZpbGUgc2VudCB0byB5b3VyIGNoYXQiKTsKICAgIGlmKHdo"
    "YXQ9PT0icmVjZWlwdCIpdG9hc3QoIlJlY2VpcHQgc2VudCB0byB5b3VyIGNoYXQiKTsKICB9KTsK"
    "ICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1yYXRlLWdvXSIpLmZvckVhY2goYj0+"
    "Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHN0YXJzPStiLmRhdGFzZXQucmF0ZUdvOwog"
    "ICAgY29uc3QgYz0od2luZG93Ll9fcmF0ZUNvdW50cnkpfHwoKE9SREVSU1swXXx8e30pLmMpfHwi"
    "IjsKICAgIGlmKCFMSVZFKXtjbG9zZVNoZWV0KCk7dG9hc3QoIlRoYW5rcyBmb3IgdGhlICIrc3Rh"
    "cnMrIuKYhSIpO3JldHVybjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9yZXZpZXci"
    "LHtjb3VudHJ5OmMscmF0aW5nOnN0YXJzfSk7CiAgICBpZighcilyZXR1cm47CiAgICBjbG9zZVNo"
    "ZWV0KCk7dG9hc3QoIlRoYW5rcyDigJQgIitjKyIgaXMgbm93ICIrci5hdmcrIuKYhSIpOwogICAg"
    "YXdhaXQgcmVmcmVzaCgpOwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRh"
    "LWFjdF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCB3aGF0PWIu"
    "ZGF0YXNldC5hY3Q7CiAgICBpZih3aGF0PT09ImNoYW5uZWwiKXsKICAgICAgY29uc3QgYz0oQURN"
    "LmNoYW5uZWxzJiZBRE0uY2hhbm5lbHNbMF0pfHxudWxsOwogICAgICBjb25zdCBoPWM/U3RyaW5n"
    "KGMuaGFuZGxlfHwiIikucmVwbGFjZSgvXkAvLCIiKToiIjsKICAgICAgaWYoaClvcGVuUGF5KGgu"
    "c3RhcnRzV2l0aCgiaHR0cCIpP2g6KCJodHRwczovL3QubWUvIitoKSk7CiAgICAgIGVsc2UgdG9h"
    "c3QoIk5vIGNoYW5uZWwgaGFzIGJlZW4gYWRkZWQgeWV0Iik7CiAgICAgIHJldHVybjsKICAgIH0K"
    "ICAgIGlmKHdoYXQ9PT0iZmxhc2giKXsKICAgICAgY29uc3Qgb249QURNLm1hcmtldCYmQURNLm1h"
    "cmtldC5mbGFzaF9vbjsKICAgICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9u"
    "dC1zaXplOjFyZW0iPkZsYXNoIHNhbGU8L2gzPgogICAgICAgIDxwIGNsYXNzPSJzdWIiPiR7b24/"
    "KCJFdmVyeSBwcmljZSBpcyAiKyhBRE0ubWFya2V0LmZsYXNofHwwKSsiJSBvZmYgcmlnaHQgbm93"
    "LiIpOiJObyBzYWxlIGlzIHJ1bm5pbmcgYXQgdGhlIG1vbWVudC4ifTwvcD4KICAgICAgICA8YnV0"
    "dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEtY2xvc2U+"
    "Q2xvc2U8L2J1dHRvbj5gKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJzb3J0"
    "Iil7CiAgICAgIEZJTFRFUkVEPShGSUxURVJFRCYmRklMVEVSRUQubGVuZ3RoP0ZJTFRFUkVEOlNU"
    "T0NLKS5zbGljZSgpLnNvcnQoKHgseSk9Pih5LnF0eXx8MCktKHgucXR5fHwwKSk7CiAgICAgIHJl"
    "bmRlcigpO3RvYXN0KCJTb3J0ZWQgYnkgd2hhdCBpcyBpbiBzdG9jayIpOwogICAgICByZXR1cm47"
    "CiAgICB9CiAgICBpZih3aGF0PT09ImFsZXJ0cyIpewogICAgICBjb25zdCBtaW5lPSh3aW5kb3cu"
    "X19hbGVydHMpfHxbXTsKICAgICAgY29uc3Qgb2ZmPVNUT0NLLmZpbHRlcih4PT4heC5xdHkpLm1h"
    "cCh4PT54LmMpOwogICAgICBjb25zdCBsaXN0PShvZmYubGVuZ3RoP29mZjpTVE9DSy5tYXAoeD0+"
    "eC5jKSkuc2xpY2UoMCw4KTsKICAgICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7"
    "Zm9udC1zaXplOjFyZW0iPlJlc3RvY2sgYWxlcnRzPC9oMz4KICAgICAgICA8cCBjbGFzcz0ic3Vi"
    "Ij5XZSBtZXNzYWdlIHlvdSB0aGUgbW9tZW50IG9uZSBvZiB0aGVzZSBpcyBiYWNrLjwvcD4KICAg"
    "ICAgICAke2xpc3QubWFwKGM9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHtlc2MoYyl9PC9zcGFu"
    "PgogICAgICAgICAgPGI+PGJ1dHRvbiBjbGFzcz0iYnRuIHhzICR7bWluZS5pbmRleE9mKGMpPj0w"
    "PyIiOiJzb2Z0In0iIGRhdGEtYWxlcnQ9IiR7ZXNjKGMpfSI+CiAgICAgICAgICAgICR7bWluZS5p"
    "bmRleE9mKGMpPj0wPyJPbiI6Ik5vdGlmeSBtZSJ9PC9idXR0b24+PC9iPjwvZGl2PmApLmpvaW4o"
    "IiIpfQogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTJweCIgZGF0YS1jbG9zZT5Eb25lPC9idXR0b24+YCk7CiAgICAgIGRvY3VtZW50LnF1ZXJ5U2Vs"
    "ZWN0b3JBbGwoIltkYXRhLWFsZXJ0XSIpLmZvckVhY2goYT0+YS5vbmNsaWNrPWFzeW5jKCk9PnsK"
    "ICAgICAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICAgICAg"
    "Y29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWxlcnQiLHtjb3VudHJ5OmEuZGF0YXNldC5hbGVy"
    "dH0pOwogICAgICAgIGlmKCFyKXJldHVybjsKICAgICAgICB3aW5kb3cuX19hbGVydHM9ci5hbGVy"
    "dHN8fFtdOwogICAgICAgIGEudGV4dENvbnRlbnQ9ci5vbj8iT24iOiJOb3RpZnkgbWUiOwogICAg"
    "ICAgIGEuY2xhc3NMaXN0LnRvZ2dsZSgic29mdCIsIXIub24pOwogICAgICAgIHRvYXN0KHIub24/"
    "KCJXZSB3aWxsIHRlbGwgeW91IHdoZW4gIithLmRhdGFzZXQuYWxlcnQrIiBpcyBiYWNrIik6IkFs"
    "ZXJ0IHJlbW92ZWQiKTsKICAgICAgfSk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9"
    "PT0iY3N2Iil7CiAgICAgIGNvbnN0IHJvd3M9W1sib3JkZXIiLCJjb3VudHJ5IiwicHJpY2UiLCJz"
    "dGF0dXMiLCJ3aGVuIl1dCiAgICAgICAgLmNvbmNhdChPUkRFUlMubWFwKG89PltvLmlkLG8uYyxv"
    "LnByaWNlLG8uc3Qsby53aGVuXSkpOwogICAgICBjb25zdCBjc3Y9cm93cy5tYXAocj0+ci5qb2lu"
    "KCIsIikpLmpvaW4oIlxuIik7CiAgICAgIGNvbnN0IGE9ZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgi"
    "YSIpOwogICAgICBhLmhyZWY9ImRhdGE6dGV4dC9jc3Y7Y2hhcnNldD11dGYtOCwiK2VuY29kZVVS"
    "SUNvbXBvbmVudChjc3YpOwogICAgICBhLmRvd25sb2FkPSJteS1vcmRlcnMuY3N2Ijtkb2N1bWVu"
    "dC5ib2R5LmFwcGVuZENoaWxkKGEpO2EuY2xpY2soKTthLnJlbW92ZSgpOwogICAgICB0b2FzdChP"
    "UkRFUlMubGVuZ3RoKyIgb3JkZXJzIGRvd25sb2FkZWQiKTsKICAgICAgcmV0dXJuOwogICAgfQog"
    "ICAgaWYod2hhdD09PSJzdXBwb3J0Iil7CiAgICAgIGNvbnN0IGg9KFNUT1JFLnN1cHBvcnR8fCIi"
    "KS5yZXBsYWNlKC9eQC8sIiIpOwogICAgICBpZihoKW9wZW5QYXkoImh0dHBzOi8vdC5tZS8iK2gp"
    "OwogICAgICBlbHNlIHRvYXN0KCJTZW5kIHlvdXIgcXVlc3Rpb24gaW4gdGhlIGNoYXQgYW5kIHdl"
    "IHdpbGwgcmVwbHkiKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJ0YXNrIil7"
    "CiAgICAgIGNvbnN0IHJvdz1iLmNsb3Nlc3QoIi5jYXJkIik7CiAgICAgIGNvbnN0IGxhYmVsPXJv"
    "dz8ocm93LnF1ZXJ5U2VsZWN0b3IoImIiKXx8e30pLnRleHRDb250ZW50fHwiIjoiIjsKICAgICAg"
    "aWYoL2NoYW5uZWwvaS50ZXN0KGxhYmVsKSl7Yi5ibHVyKCk7ZG9jdW1lbnQucXVlcnlTZWxlY3Rv"
    "cignW2RhdGEtYWN0PSJjaGFubmVsIl0nKT8uY2xpY2soKTtyZXR1cm47fQogICAgICBpZigvb3Jk"
    "ZXIvaS50ZXN0KGxhYmVsKSl7Z29UYWIoInNob3AiKTt0b2FzdCgiUGljayBhIGNvdW50cnkgdG8g"
    "cGxhY2UgeW91ciBmaXJzdCBvcmRlciIpO3JldHVybjt9CiAgICAgIGdvVGFiKCJlYXJuIik7dG9h"
    "c3QoIlNoYXJlIHlvdXIgaW52aXRlIGxpbmsgdG8gZmluaXNoIHRoaXMiKTsKICAgICAgcmV0dXJu"
    "OwogICAgfQogICAgaWYod2hhdD09PSJzdGF0dXMiKXsKICAgICAgY29uc3QgZz0oQURNLnBheSYm"
    "QURNLnBheS5nbWFpbF9zZXQpOwogICAgICBjb25zdCBwZW5kPShBRE0ucGF5JiZBRE0ucGF5LnBl"
    "bmRpbmcpfHwwOwogICAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNp"
    "emU6MXJlbSI+UGF5bWVudCBjaGVja2luZzwvaDM+CiAgICAgICAgPHAgY2xhc3M9InN1YiI+JHtn"
    "PyJBdXRvbWF0aWMgY2hlY2tpbmcgaXMgb24uIFBheSwgdGhlbiB0YXAgVmVyaWZ5LiI6CiAgICAg"
    "ICAgICAiQXV0b21hdGljIGNoZWNraW5nIGlzIG5vdCBzZXQgdXAsIHNvIGFuIGFkbWluIGNvbmZp"
    "cm1zIHBheW1lbnRzIGJ5IGhhbmQuIn08L3A+CiAgICAgICAgJHtwZW5kP2A8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4+V2FpdGluZyBmb3IgYW4gYWRtaW48L3NwYW4+PGI+JHtwZW5kfTwvYj48L2Rpdj5g"
    "OiIifQogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTJweCIgZGF0YS1jbG9zZT5DbG9zZTwvYnV0dG9uPmApOwogICAgICByZXR1cm47CiAgICB9CiAg"
    "ICBpZih3aGF0PT09ImNvcHljb2RlIil7CiAgICAgIGNvbnN0IGVsPWIuY2xvc2VzdCgiLmNhcmQs"
    "LnNoZWV0Ym9keSwjc2hlZXQiKXx8ZG9jdW1lbnQ7CiAgICAgIGNvbnN0IGNvZGU9KGVsLnF1ZXJ5"
    "U2VsZWN0b3IoImNvZGUiKXx8e30pLnRleHRDb250ZW50fHwiIjsKICAgICAgY29weVRleHQoY29k"
    "ZS50cmltKCksIkNvZGUiKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJyZXBs"
    "YWNlIil7CiAgICAgIGNvbnN0IGNhcmQ9Yi5jbG9zZXN0KCJbZGF0YS1vcmRlci1pZF0iKTsKICAg"
    "ICAgY29uc3Qgb2lkPShjYXJkJiZjYXJkLmRhdGFzZXQub3JkZXJJZCl8fChPUkRFUlNbMF0mJk9S"
    "REVSU1swXS5pZCl8fCIiOwogICAgICBpZighTElWRSl7Y2xvc2VTaGVldCgpO3RvYXN0KCJSZXBs"
    "YWNlbWVudCBzZW50Iik7cmV0dXJuO30KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkv"
    "b3JkZXIiLHtpZDpvaWQsd2hhdDoicmVwbGFjZSJ9KTsKICAgICAgaWYoIXIpcmV0dXJuOwogICAg"
    "ICBjbG9zZVNoZWV0KCk7CiAgICAgIHRvYXN0KHIucmVmdW5kZWQ/Ik5vIHN0b2NrIGxlZnQg4oCU"
    "IHJlZnVuZGVkIHRvIHlvdXIgd2FsbGV0IjooIlJlcGxhY2VkIOKAlCAiK3Iub3JkZXIpKTsKICAg"
    "ICAgYXdhaXQgcmVmcmVzaCgpOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09ImNy"
    "eXB0byIpewogICAgICBpZighTElWRSl7Y2xvc2VTaGVldCgpO3RvYXN0KCJXZSB3aWxsIGNvbmZp"
    "cm0geW91ciB0cmFuc2ZlciBzaG9ydGx5Iik7cmV0dXJuO30KICAgICAgY29uc3Qgcj1hd2FpdCBh"
    "cGlUcnkoIi9hcGkvY3J5cHRvIix7cmVmOiIifSk7CiAgICAgIGlmKHIpe2Nsb3NlU2hlZXQoKTt0"
    "b2FzdCgiV2Ugd2lsbCBjb25maXJtIHlvdXIgdHJhbnNmZXIgc2hvcnRseSIpO30KICAgICAgcmV0"
    "dXJuOwogICAgfQogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNvcHld"
    "IikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IHdoYXQ9Yi5kYXRhc2V0LmNv"
    "cHk7CiAgICBpZih3aGF0PT09InVwaSIpcmV0dXJuIGNvcHlUZXh0KFNUT1JFLnVwaSwiVVBJIElE"
    "Iik7CiAgICBpZih3aGF0PT09InJlZiIpewogICAgICBsZXQgbD1FQVJOLmxpbmt8fCIiOwogICAg"
    "ICBpZihsJiYhL15odHRwcz86Ly50ZXN0KGwpKWw9Imh0dHBzOi8vIitsOwogICAgICByZXR1cm4g"
    "Y29weVRleHQobCwiSW52aXRlIGxpbmsiKTsKICAgIH0KICAgIHJldHVybiBjb3B5VGV4dChiLmRh"
    "dGFzZXQuY29weVZhbHVlfHxiLnRleHRDb250ZW50LnRyaW0oKSwiQ29waWVkIik7CiAgfSk7CiAg"
    "Y29uc3QgcmQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInJlZGVlbWdvIik7CiAgaWYocmQpcmQu"
    "b25jbGljaz1hc3luYygpPT57CiAgICBpZighTElWRSl7dG9hc3QoIlJlZGVlbWVkIik7cmV0dXJu"
    "O30KICAgIHJkLmRpc2FibGVkPXRydWU7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9y"
    "ZWRlZW0iLHt9KTsKICAgIHJkLmRpc2FibGVkPWZhbHNlOwogICAgaWYoIXIpcmV0dXJuOwogICAg"
    "QkFMPXIuYmFsYW5jZTtzeW5jQmFsKCk7YnVyc3QoKTsKICAgIHRvYXN0KHIucG9pbnRzKyIgcG9p"
    "bnRzIOKGkiAiK21vbmV5KHIuYW1vdW50KSk7CiAgICBhd2FpdCByZWZyZXNoKCk7CiAgfTsKICBj"
    "b25zdCBvdT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgib2ZmZXJ1c2UiKTsKICBpZihvdSlvdS5v"
    "bmNsaWNrPSgpPT57CiAgICBjb25zdCBjPShBRE0uY291cG9ucyYmQURNLmNvdXBvbnMubGVuZ3Ro"
    "KT9BRE0uY291cG9uc1swXS5jb2RlOiIiOwogICAgaWYoYyl7T0ZGRVI9Yztjb3B5VGV4dChjLCJD"
    "b3Vwb24iKTt9CiAgICBlbHNlIHRvYXN0KCJObyBjb3Vwb24gaXMgcnVubmluZyByaWdodCBub3ci"
    "KTsKICB9OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXByZWZdIikuZm9yRWFj"
    "aCh0PT50Lm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3Qga2V5PXQuZGF0YXNldC5wcmVmLCBv"
    "bj0hdC5jbGFzc0xpc3QuY29udGFpbnMoIm9uIik7CiAgICB0LmNsYXNzTGlzdC50b2dnbGUoIm9u"
    "Iixvbik7UFJFRlNba2V5XT1vbjsKICAgIGlmKExJVkUpewogICAgICBjb25zdCByPWF3YWl0IGFw"
    "aVRyeSgiL2FwaS9wcmVmcyIse2tleTprZXksb246b259KTsKICAgICAgaWYoIXIpe3QuY2xhc3NM"
    "aXN0LnRvZ2dsZSgib24iLCFvbik7UFJFRlNba2V5XT0hb247cmV0dXJuO30KICAgIH0KICAgIHRv"
    "YXN0KG9uPyJUdXJuZWQgb24iOiJUdXJuZWQgb2ZmIik7CiAgfSk7CiAgY29uc3QgdGdiPWRvY3Vt"
    "ZW50LmdldEVsZW1lbnRCeUlkKCJ0ZXJtc2dvIik7CiAgaWYodGdiKXRnYi5vbmNsaWNrPSgpPT5z"
    "aGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+VGVybXMgJiBy"
    "ZWZ1bmRzPC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJ3aGl0ZS1zcGFjZTpwcmUtd3Jh"
    "cCI+JHtlc2MoU1RPUkUudGVybXN8fENNUy50ZXJtc3x8Ik5vIHRlcm1zIGhhdmUgYmVlbiB3cml0"
    "dGVuIHlldC4iKX08L3A+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEycHgiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTsKICBjb25zdCBzZz1kb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgic3VwcG9ydGdvIik7CiAgaWYoc2cpc2cub25jbGljaz0oKT0+"
    "ewogICAgY29uc3QgaD0oU1RPUkUuc3VwcG9ydHx8IiIpLnJlcGxhY2UoL15ALywiIik7CiAgICBp"
    "ZihoKW9wZW5QYXkoImh0dHBzOi8vdC5tZS8iK2gpOwogICAgZWxzZSBzaGVldChgPGgzIHN0eWxl"
    "PSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+U3VwcG9ydDwvaDM+CiAgICAgIDxwIGNs"
    "YXNzPSJzdWIiPlNlbmQgeW91ciBxdWVzdGlvbiBpbiB0aGUgY2hhdCBhbmQgd2Ugd2lsbCByZXBs"
    "eS48L3A+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTJweCIgZGF0YS1jbG9zZT5DbG9zZTwvYnV0dG9uPmApOwogIH07Cn0KZnVuY3Rpb24gYmluZFN0"
    "YWdlKCl7CiAgYmluZENvbW1vbigpOwogIGJpbmRCdXkoKTsKICBkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCJbZGF0YS1zY3JvbGxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PmRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKGIuZGF0YXNldC5zY3JvbGwpLnNjcm9sbEludG9WaWV3KHtiZWhhdmlv"
    "cjoic21vb3RoIn0pKTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1vdHBdIiku"
    "Zm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgY2FyZD1iLmNsb3Nlc3Qo"
    "IltkYXRhLW9yZGVyLWlkXSIpOwogICAgY29uc3Qgb2lkPShjYXJkJiZjYXJkLmRhdGFzZXQub3Jk"
    "ZXJJZCl8fGIuZGF0YXNldC5vdHB8fCIiOwogICAgY29uc3Qgb3JkPU9SREVSUy5maW5kKHg9Pngu"
    "aWQ9PT1vaWQpfHx7fTsKICAgIGlmKCFMSVZFKXsKICAgICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFy"
    "Z2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkxvZ2luIGNvZGU8L2gzPgogICAgICAgIDxwIGNs"
    "YXNzPSJzdWIiPlNhbXBsZSBjb2RlIOKAlCB0aGlzIGlzIHRoZSBwcmV2aWV3LjwvcD4KICAgICAg"
    "ICA8ZGl2IGNsYXNzPSJvdHBib3giPjQgMiA5IDEgNzwvZGl2PgogICAgICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCIgZGF0YS1jbG9zZT5Eb25lPC9i"
    "dXR0b24+YCk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGNvbnN0IGxibD1iLnRleHRDb250ZW50"
    "O2IuZGlzYWJsZWQ9dHJ1ZTtiLnRleHRDb250ZW50PSJGZXRjaGluZ+KApiI7CiAgICBjb25zdCBy"
    "PWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRlciIse2lkOm9pZCx3aGF0OiJvdHAifSk7CiAgICBiLmRp"
    "c2FibGVkPWZhbHNlO2IudGV4dENvbnRlbnQ9bGJsOwogICAgaWYoIXIpcmV0dXJuOwogICAgc2hl"
    "ZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkxvZ2luIGNvZGU8"
    "L2gzPgogICAgICA8cCBjbGFzcz0ic3ViIj4ke29yZC5waG9uZT8oIkZvciAiK2VzYyhvcmQucGhv"
    "bmUpKToiRm9yIHRoaXMgYWNjb3VudCJ9PC9wPgogICAgICA8ZGl2IGNsYXNzPSJvdHBib3giPiR7"
    "U3RyaW5nKHIuY29kZSkuc3BsaXQoIiIpLmpvaW4oIiAiKX08L2Rpdj4KICAgICAgPGRpdiBjbGFz"
    "cz0ia3YiPjxzcGFuPkNvZGVzIGxlZnQ8L3NwYW4+PGI+JHtyLmxlZnR9PC9iPjwvZGl2PgogICAg"
    "ICAke3Iud2luZG93P2A8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+Rm9yd2FyZGluZyBlbmRzIGluPC9z"
    "cGFuPjxiPiR7ci53aW5kb3d9IG1pbjwvYj48L2Rpdj5gOiIifQogICAgICA8ZGl2IGNsYXNzPSJi"
    "dG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biBzb2Z0IiBkYXRhLWFjdD0iY29weWNvZGUiPkNvcHkgQ29kZTwvYnV0dG9uPgogICAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biIgZGF0YS1jbG9zZT5Eb25lPC9idXR0b24+PC9kaXY+YCk7CiAgfSk7"
    "CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2xhaW1dIikuZm9yRWFjaChiPT5i"
    "Lm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYoYi5kaXNhYmxlZClyZXR1cm47CiAgICBiLmRpc2Fi"
    "bGVkPXRydWU7CiAgICBpZihMSVZFKXsKICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkv"
    "Y2hlY2tpbiIse30pOwogICAgICBpZighcil7Yi5kaXNhYmxlZD1mYWxzZTtyZXR1cm47fQogICAg"
    "ICBCQUw9dHlwZW9mIHIuYmFsYW5jZT09PSJudW1iZXIiP3IuYmFsYW5jZTpCQUw7CiAgICAgIEVB"
    "Uk4uY2xhaW1lZD10cnVlO0VBUk4uc3RyZWFrPXIuc3RyZWFrfHxFQVJOLnN0cmVhazsKICAgICAg"
    "c3luY0JhbCgpO2J1cnN0KCk7dG9hc3QobW9uZXkoci5hbW91bnQpKyIgYWRkZWQgwrcgZGF5ICIr"
    "ci5zdHJlYWspOwogICAgICBiLnRleHRDb250ZW50PSJDbGFpbWVkIHRvZGF5IjsKICAgICAgYXdh"
    "aXQgcmVmcmVzaCgpOwogICAgICByZXR1cm47CiAgICB9CiAgICBCQUwrPUVBUk4uZGFpbHk7c3lu"
    "Y0JhbCgpO2J1cnN0KCk7dG9hc3QobW9uZXkoRUFSTi5kYWlseSkrIiBhZGRlZCIpOwogICAgYi50"
    "ZXh0Q29udGVudD0iQ2xhaW1lZCB0b2RheSI7fSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiW2RhdGEtYW10XSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT5kZXBvc2l0U2hlZXQoYi5k"
    "YXRhc2V0LmFtdCkpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWRlcF0iKS5m"
    "b3JFYWNoKGI9PmIub25jbGljaz0oKT0+YW1vdW50U2hlZXQoKSk7CiAgZG9jdW1lbnQucXVlcnlT"
    "ZWxlY3RvckFsbCgiW2RhdGEtdGddIikuZm9yRWFjaCh0PT50Lm9uY2xpY2s9KCk9PnQuY2xhc3NM"
    "aXN0LnRvZ2dsZSgib24iKSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtc2V0"
    "c2VsXSIpLmZvckVhY2goc2VsPT5zZWwub25jaGFuZ2U9YXN5bmMoKT0+ewogICAgaWYoIUxJVkUp"
    "e3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnko"
    "Ii9hcGkvYWRtaW4vc2V0dGluZyIse2tleTpzZWwuZGF0YXNldC5zZXRzZWwsdmFsdWU6c2VsLnZh"
    "bHVlfSk7CiAgICBpZihyKXRvYXN0KCJTYXZlZCIpOwogIH0pOwogIGNvbnN0IHE9ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoInEiKTsKICBpZihxKXtxLnZhbHVlPVFVRVJZO3Eub25pbnB1dD0oKT0+"
    "e1FVRVJZPXEudmFsdWUudG9Mb3dlckNhc2UoKTthcHBseUZpbHRlcigpfTt9CiAgZG9jdW1lbnQu"
    "cXVlcnlTZWxlY3RvckFsbCgiI2NoaXBzIGJ1dHRvbiIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgp"
    "PT57Q0hJUD1iLmRhdGFzZXQuZmlsdGVyOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgi"
    "I2NoaXBzIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseD09PWIp"
    "KTthcHBseUZpbHRlcigpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2xh"
    "aW0tb3JkZXJdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IG9pZD1iLmRh"
    "dGFzZXQuY2xhaW1PcmRlcjsKICAgIGNvbnN0IG9yZD1PUkRFUlMuZmluZCh4PT54LmlkPT09b2lk"
    "KXx8e307CiAgICBpZihvcmQuY2xhaW0pe3RvYXN0KCJBIGNsYWltIGZvciB0aGlzIG9yZGVyIGlz"
    "IGFscmVhZHkgd2l0aCBhbiBhZG1pbiIpO3JldHVybjt9CiAgICBzaGVldChgPGgzIHN0eWxlPSJt"
    "YXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+UmVwb3J0IGEgcHJvYmxlbTwvaDM+CiAgICAg"
    "IDxwIGNsYXNzPSJzdWIiPkFuIGFkbWluIHJlYWRzIGV2ZXJ5IHJlcG9ydCBhbmQgZGVjaWRlcy4g"
    "Tm90aGluZyBpcyByZWZ1bmRlZCBhdXRvbWF0aWNhbGx5LjwvcD4KICAgICAgPGRpdiBjbGFzcz0i"
    "ZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5XaGF0IGhhcHBlbmVkPzwvbGFi"
    "ZWw+CiAgICAgICAgPHNlbGVjdCBjbGFzcz0iaW5wIiBpZD0iY2xyIj4KICAgICAgICAgIDxvcHRp"
    "b24+TG9naW4gbm90IHdvcmtpbmc8L29wdGlvbj48b3B0aW9uPkFjY291bnQgYmFubmVkPC9vcHRp"
    "b24+CiAgICAgICAgICA8b3B0aW9uPkNvZGUgbmV2ZXIgYXJyaXZlZDwvb3B0aW9uPjxvcHRpb24+"
    "V3JvbmcgY291bnRyeTwvb3B0aW9uPgogICAgICAgICAgPG9wdGlvbj5Tb21ldGhpbmcgZWxzZTwv"
    "b3B0aW9uPjwvc2VsZWN0PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkRl"
    "dGFpbHM8L2xhYmVsPgogICAgICAgIDx0ZXh0YXJlYSBjbGFzcz0iaW5wIiBpZD0iY2xkIiByb3dz"
    "PSIzIiBwbGFjZWhvbGRlcj0iVGVsbCB1cyBleGFjdGx5IHdoYXQgeW91IHNhdyI+PC90ZXh0YXJl"
    "YT48L2Rpdj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSBibG9jayIgaWQ9ImNsZ28iPlNl"
    "bmQgdG8gYW4gYWRtaW48L2J1dHRvbj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJs"
    "b2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+Q2FuY2VsPC9idXR0b24+"
    "YCk7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2xnbyIpLm9uY2xpY2s9YXN5bmMoKT0+"
    "ewogICAgICBjb25zdCByZWFzb249ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNsciIpLnZhbHVl"
    "OwogICAgICBjb25zdCBkZXRhaWxzPShkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2xkIikudmFs"
    "dWV8fCIiKS50cmltKCk7CiAgICAgIGlmKGRldGFpbHMubGVuZ3RoPDUpe3RvYXN0KCJUZWxsIHVz"
    "IHdoYXQgd2VudCB3cm9uZyIpO3JldHVybjt9CiAgICAgIGlmKCFMSVZFKXtjbG9zZVNoZWV0KCk7"
    "dG9hc3QoIlNlbnQiKTtyZXR1cm47fQogICAgICBjb25zdCBidG49ZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoImNsZ28iKTtidG4uZGlzYWJsZWQ9dHJ1ZTtidG4udGV4dENvbnRlbnQ9IlNlbmRpbmfi"
    "gKYiOwogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRlciIse2lkOm9pZCx3aGF0"
    "OiJjbGFpbSIscmVhc29uOnJlYXNvbixkZXRhaWxzOmRldGFpbHN9KTsKICAgICAgYnRuLmRpc2Fi"
    "bGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0iU2VuZCB0byBhbiBhZG1pbiI7CiAgICAgIGlmKCFy"
    "KXJldHVybjsKICAgICAgY2xvc2VTaGVldCgpO3RvYXN0KCJDbGFpbSAjIityLmlkKyIgc2VudCDi"
    "gJQgYW4gYWRtaW4gd2lsbCByZXBseSIpOwogICAgICBhd2FpdCByZWZyZXNoKCk7CiAgICB9Owog"
    "IH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWxvZ2dlZGluXSIpLmZvckVh"
    "Y2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IG9pZD1iLmRhdGFzZXQubG9nZ2Vk"
    "aW47CiAgICBpZighTElWRSl7dG9hc3QoIk1hcmtlZCBhcyBsb2dnZWQgaW4iKTtyZXR1cm47fQog"
    "ICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkNvbmZp"
    "cm0geW91IGFyZSBpbjwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiPldlIHdpbGwgZGVsZXRlIG91"
    "ciBjb3B5IG9mIHRoZSBsb2dpbiBzZXNzaW9uIHN0cmFpZ2h0IGF3YXksIHNvIHRoZQogICAgICBh"
    "Y2NvdW50IGJlY29tZXMgeW91cnMgYWxvbmUuIENvZGVzIHdpbGwgc3RvcCBiZWluZyBmb3J3YXJk"
    "ZWQuPC9wPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBpZD0ibGlvayIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTJweCI+WWVzLCBJIGFtIGxvZ2dlZCBpbjwvYnV0dG9uPgogICAgICA8"
    "YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QgYmxvY2sgc20iIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIg"
    "ZGF0YS1jbG9zZT5Ob3QgeWV0PC9idXR0b24+YCk7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgibGlvayIpLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgi"
    "L2FwaS9vcmRlciIse2lkOm9pZCx3aGF0OiJsb2dnZWRpbiJ9KTsKICAgICAgaWYoIXIpcmV0dXJu"
    "OwogICAgICBjbG9zZVNoZWV0KCk7dG9hc3QoIkRvbmUg4oCUIHRoZSBhY2NvdW50IGlzIHlvdXJz"
    "IGFsb25lIG5vdyIpOwogICAgICBhd2FpdCByZWZyZXNoKCk7CiAgICB9OwogIH0pOwogIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWRpc3B1dGVdIikuZm9yRWFjaChiPT5iLm9uY2xp"
    "Y2s9KCk9PnsKICAgIGNvbnN0IG9yZD1iLmRhdGFzZXQuZGlzcHV0ZXx8IiI7CiAgICBzaGVldChg"
    "PGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+T3BlbiBhIGRpc3B1dGU8"
    "L2gzPgogICAgIDxwIGNsYXNzPSJzdWIiPiR7b3JkPygiT3JkZXIgIitlc2Mob3JkKSk6IkFuIGFk"
    "bWluIHJldmlld3MgZXZlcnkgZGlzcHV0ZS4ifTwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlJlYXNvbjwvbGFiZWw+CiAgICAgICA8c2Vs"
    "ZWN0IGNsYXNzPSJpbnAiIGlkPSJkc3IiPjxvcHRpb24+Tm90IGRlbGl2ZXJlZDwvb3B0aW9uPjxv"
    "cHRpb24+QWNjb3VudCBkZWFkIG9uIGFycml2YWw8L29wdGlvbj48b3B0aW9uPkNoYXJnZWQgdHdp"
    "Y2U8L29wdGlvbj48b3B0aW9uPk90aGVyPC9vcHRpb24+PC9zZWxlY3Q+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0iZmllbGQiPjxsYWJlbD5XaGF0IGhhcHBlbmVkPzwvbGFiZWw+CiAgICAgICA8dGV4"
    "dGFyZWEgY2xhc3M9ImlucCIgaWQ9ImRzZCIgcm93cz0iMyIgcGxhY2Vob2xkZXI9IkRlc2NyaWJl"
    "IHRoZSBwcm9ibGVtIj48L3RleHRhcmVhPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48"
    "bGFiZWw+RXZpZGVuY2UgKG9wdGlvbmFsKTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9Imlu"
    "cCIgaWQ9ImRzZiIgdHlwZT0iZmlsZSIgYWNjZXB0PSJpbWFnZS8qIj4KICAgICAgIDxzbWFsbCBj"
    "bGFzcz0ic3ViIiBpZD0iZHNmbiI+PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJidG5y"
    "b3ciPjxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCIgZGF0YS1jbG9zZT5DYW5jZWw8L2J1dHRvbj4K"
    "ICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJkc2dvIj5TdWJtaXQ8L2J1dHRvbj48"
    "L2Rpdj5gKTsKICAgIGNvbnN0IGY9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRzZiIpOwogICAg"
    "aWYoZilmLm9uY2hhbmdlPSgpPT57Y29uc3Qgbj0oZi5maWxlcyYmZi5maWxlc1swXSk/Zi5maWxl"
    "c1swXS5uYW1lOiIiOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZHNmbiIpLnRleHRD"
    "b250ZW50PW4/KCJBdHRhY2hlZDogIituKToiIjt9OwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5"
    "SWQoImRzZ28iKS5vbmNsaWNrPWFzeW5jKCk9PnsKICAgICAgY29uc3QgcmVhc29uPWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJkc3IiKS52YWx1ZTsKICAgICAgY29uc3QgZGV0YWlscz0oZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoImRzZCIpLnZhbHVlfHwiIikudHJpbSgpOwogICAgICBpZighZGV0"
    "YWlscyl7dG9hc3QoIkRlc2NyaWJlIHdoYXQgaGFwcGVuZWQiKTtyZXR1cm47fQogICAgICBjb25z"
    "dCBmaWxlPShmJiZmLmZpbGVzJiZmLmZpbGVzWzBdKXx8bnVsbDsKICAgICAgY29uc3QgZm49Zmls"
    "ZT8oInNjcmVlbnNob3Q6ICIrZmlsZS5uYW1lKToiIjsKICAgICAgaWYoIUxJVkUpe2Nsb3NlU2hl"
    "ZXQoKTt0b2FzdCgiRGlzcHV0ZSBvcGVuZWQiKTtyZXR1cm47fQogICAgICBjb25zdCBidG49ZG9j"
    "dW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRzZ28iKTtidG4uZGlzYWJsZWQ9dHJ1ZTtidG4udGV4dENv"
    "bnRlbnQ9IlNlbmRpbmfigKYiOwogICAgICBsZXQgc2hvdD0iIjsKICAgICAgaWYoZmlsZSl7CiAg"
    "ICAgICAgaWYoZmlsZS5zaXplPjgqMTAyNCoxMDI0KXt0b2FzdCgiVGhhdCBpbWFnZSBpcyB0b28g"
    "bGFyZ2UgKDggTUIgbWF4KSIpOwogICAgICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50ZXh0"
    "Q29udGVudD0iU3VibWl0IjtyZXR1cm47fQogICAgICAgIHNob3Q9YXdhaXQgbmV3IFByb21pc2Uo"
    "cmVzPT57CiAgICAgICAgICBjb25zdCByZD1uZXcgRmlsZVJlYWRlcigpOwogICAgICAgICAgcmQu"
    "b25sb2FkPSgpPT5yZXMoU3RyaW5nKHJkLnJlc3VsdHx8IiIpKTsKICAgICAgICAgIHJkLm9uZXJy"
    "b3I9KCk9PnJlcygiIik7CiAgICAgICAgICByZC5yZWFkQXNEYXRhVVJMKGZpbGUpOwogICAgICAg"
    "IH0pOwogICAgICB9CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2Rpc3B1dGUiLHtv"
    "cmRlcjpvcmQscmVhc29uOnJlYXNvbixkZXRhaWxzOmRldGFpbHMsCiAgICAgICAgZXZpZGVuY2U6"
    "Zm4sc2hvdDpzaG90fSk7CiAgICAgIGJ0bi5kaXNhYmxlZD1mYWxzZTtidG4udGV4dENvbnRlbnQ9"
    "IlN1Ym1pdCI7CiAgICAgIGlmKCFyKXJldHVybjsKICAgICAgY2xvc2VTaGVldCgpO3RvYXN0KCJE"
    "aXNwdXRlICMiK3IuaWQrIiBzZW50IHRvIHRoZSBhZG1pbnMiKTsKICAgICAgYXdhaXQgcmVmcmVz"
    "aCgpOwogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1yYXRl"
    "XSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57d2luZG93Ll9fcmF0ZUNvdW50cnk9Yi5kYXRh"
    "c2V0LnJhdGU7cmV0dXJuIHNoZWV0KAogICAgYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9u"
    "dC1zaXplOjFyZW0iPlJhdGUgJHtiLmRhdGFzZXQucmF0ZX08L2gzPgogICAgIDxwIGNsYXNzPSJz"
    "dWIiPllvdXIgcmF0aW5nIHNob3dzIG9uIHRoZSBjb3VudHJ5IGluIHRoZSBzaG9wLjwvcD4KICAg"
    "ICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjlweDtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVy"
    "O21hcmdpbjoxNnB4IDAiPgogICAgICAgJHtbMSwyLDMsNCw1XS5tYXAobj0+YDxidXR0b24gY2xh"
    "c3M9ImJ0biBzbSBzb2Z0IiBkYXRhLXJhdGUtZ289IiR7bn0iPiR7bn3imIU8L2J1dHRvbj5gKS5q"
    "b2luKCIiKX08L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QgYmxvY2sgc20iIGRh"
    "dGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTt9KTsgCiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiW2RhdGEtY3J5cHRvXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT5zaGVldCgKICAgIGA8"
    "aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5DcnlwdG8gdG9wLXVwPC9o"
    "Mz4KICAgICA8cCBjbGFzcz0ic3ViIj5TZW5kIFVTRFQtVFJDMjAg4oCUIGNyZWRpdGVkIGFmdGVy"
    "IDEgbmV0d29yayBjb25maXJtYXRpb24uPC9wPgogICAgIDxkaXYgY2xhc3M9InFyIj5RUiBDT0RF"
    "PGJyPih3YWxsZXQgYWRkcmVzcyk8L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+TmV0"
    "d29yazwvc3Bhbj48Yj5UUkMtMjA8L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFu"
    "PkFkZHJlc3M8L3NwYW4+PGIgc3R5bGU9ImZvbnQtc2l6ZTouN3JlbSI+bm90LWNvbmZpZ3VyZWQ8"
    "L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlJhdGU8L3NwYW4+PGI+MSBVU0RU"
    "ID0g4oK5ODguNDA8L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFy"
    "Z2luLXRvcDoxMnB4Ij48YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtY2xvc2U+Q2xvc2U8"
    "L2J1dHRvbj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGRhdGEtYWN0PSJjcnlwdG8i"
    "PkkndmUgU2VudCBJdDwvYnV0dG9uPjwvZGl2PmApKTsKICBjb25zdCB0Zz1kb2N1bWVudC5nZXRF"
    "bGVtZW50QnlJZCgidG91cmdvIiksdHM9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInRvdXJza2lw"
    "Iik7CiAgaWYodGcpdGcub25jbGljaz0oKT0+c2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0"
    "cHg7Zm9udC1zaXplOjFyZW0iPkhvdyBpdCB3b3JrczwvaDM+CiAgICAgPGRpdiBjbGFzcz0ic3Rl"
    "cHMiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPgogICAgICAgPGRpdiBjbGFzcz0ic3RlcCBvbiI+"
    "PGk+MTwvaT5Ub3AgdXAgeW91ciB3YWxsZXQgYnkgVVBJPC9kaXY+CiAgICAgICA8ZGl2IGNsYXNz"
    "PSJzdGVwIG9uIj48aT4yPC9pPlBpY2sgYSBjb3VudHJ5IGFuZCBidXkgaW5zdGFudGx5PC9kaXY+"
    "CiAgICAgICA8ZGl2IGNsYXNzPSJzdGVwIG9uIj48aT4zPC9pPlJlYWQgdGhlIGxvZ2luIGNvZGUg"
    "cmlnaHQgaGVyZSBpbiBPcmRlcnM8L2Rpdj48L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "Y3RhIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4IiBkYXRhLWNsb3NlPkdvdCBpdDwvYnV0"
    "dG9uPmApOwogIGlmKHRzKXRzLm9uY2xpY2s9KCk9PntGRUFULm9uYm9hcmRpbmc9ZmFsc2U7cmVu"
    "ZGVyKCk7dG9hc3QoIlRvdXIgaGlkZGVuIik7fTsKICBzdGFydENvdW50ZG93bigpOwogIGJpbmRI"
    "b2xkKCk7Cn0KZnVuY3Rpb24gYXBwbHlGaWx0ZXIoKXsKICBGSUxURVJFRD1TVE9DSy5maWx0ZXIo"
    "cz0+ewogICAgY29uc3QgdD0ocy5jKyIgIitzLmNvZGUpLnRvTG93ZXJDYXNlKCk7CiAgICBpZihR"
    "VUVSWSYmIXQuaW5jbHVkZXMoUVVFUlkpKXJldHVybiBmYWxzZTsKICAgIGlmKENISVA9PT0ic3Rv"
    "Y2siJiZzLnF0eT09PTApcmV0dXJuIGZhbHNlOwogICAgaWYoQ0hJUD09PSJkZWFsIiYmIXMud2Fz"
    "KXJldHVybiBmYWxzZTsKICAgIGlmKENISVA9PT0iY2hlYXAiJiZzLnByaWNlPj0zNSlyZXR1cm4g"
    "ZmFsc2U7CiAgICBpZihDSElQPT09InYyZmEiJiZzLnF0eT09PTApcmV0dXJuIGZhbHNlOwogICAg"
    "aWYoQ0hJUD09PSJ2bWFpbCImJiFzLmhvdClyZXR1cm4gZmFsc2U7CiAgICBpZihDSElQPT09InZz"
    "cGFtIiYmcy5wcmljZTwzMClyZXR1cm4gZmFsc2U7CiAgICByZXR1cm4gdHJ1ZTt9KTsKICBjb25z"
    "dCBiPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzaG9wYm9keSIpOwogIGlmKGIpe2IuaW5uZXJI"
    "VE1MPXNob3BCb2R5KCk7YmluZEJ1eSgpO30KfQpsZXQgY2RUPW51bGw7CmZ1bmN0aW9uIHN0YXJ0"
    "Q291bnRkb3duKCl7CiAgY29uc3QgZWw9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNkIik7Y2xl"
    "YXJJbnRlcnZhbChjZFQpO2lmKCFlbClyZXR1cm47CiAgbGV0IHQ9MTQqNjArNTI7CiAgY2RUPXNl"
    "dEludGVydmFsKCgpPT57aWYoIWRvY3VtZW50LmJvZHkuY29udGFpbnMoZWwpKXtjbGVhckludGVy"
    "dmFsKGNkVCk7cmV0dXJufQogICAgdD10PjA/dC0xOjE0KjYwKzUyO2VsLnRleHRDb250ZW50PVN0"
    "cmluZyhNYXRoLmZsb29yKHQvNjApKS5wYWRTdGFydCgyLCIwIikrIjoiK1N0cmluZyh0JTYwKS5w"
    "YWRTdGFydCgyLCIwIil9LDEwMDApOwp9Ci8qIDA1IMK3IGhvbGQtdG8tY29uZmlybSAqLwpmdW5j"
    "dGlvbiBiaW5kSG9sZCgpewogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5ob2xkIikuZm9y"
    "RWFjaChiPT57CiAgICBpZihiLl9oKXJldHVybjtiLl9oPTE7Y29uc3QgZmlsbD1iLnF1ZXJ5U2Vs"
    "ZWN0b3IoIi5maWxsIik7bGV0IGl2PW51bGwscD0wOwogICAgY29uc3Qgc3RvcD0oKT0+e2NsZWFy"
    "SW50ZXJ2YWwoaXYpO3A9MDtpZihmaWxsKWZpbGwuc3R5bGUud2lkdGg9IjAlIn07CiAgICBjb25z"
    "dCBnbz1lPT57ZS5wcmV2ZW50RGVmYXVsdCgpO2NsZWFySW50ZXJ2YWwoaXYpO2l2PXNldEludGVy"
    "dmFsKCgpPT57cCs9NDtpZihmaWxsKWZpbGwuc3R5bGUud2lkdGg9cCsiJSI7CiAgICAgIGlmKHA+"
    "PTEwMCl7c3RvcCgpO2IuY2xhc3NMaXN0LmFkZCgiZG9uZSIpO3NldFRpbWVvdXQoKCk9PmIuY2xh"
    "c3NMaXN0LnJlbW92ZSgiZG9uZSIpLDQ1MCk7YnVyc3QoKTt0b2FzdChiLmRhdGFzZXQuZG9uZXx8"
    "IkNvbmZpcm1lZCIpfX0sMjgpfTsKICAgIGIuYWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcmRvd24i"
    "LGdvKTtbInBvaW50ZXJ1cCIsInBvaW50ZXJsZWF2ZSIsInBvaW50ZXJjYW5jZWwiXS5mb3JFYWNo"
    "KGV2PT5iLmFkZEV2ZW50TGlzdGVuZXIoZXYsc3RvcCkpO30pOwp9Ci8qIOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkCBLSVQgUlVOVElNRSDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZDilZDilZAgKi8KY29uc3QgUk09bWF0Y2hNZWRpYSgiKHByZWZlcnMtcmVkdWNlZC1tb3Rpb246"
    "cmVkdWNlKSIpLm1hdGNoZXM7Ci8qIHNwcmluZyBpbnRlZ3JhdG9yIOKAlCBraXQgdXNlcyBtYXNz"
    "IC4xIC8gc3RpZmZuZXNzIDE1MCAvIGRhbXBpbmcgMTIgKi8KZnVuY3Rpb24gU3ByaW5nKHYse209"
    "LjEsaz0xNTAsZD0xMn09e30pe3RoaXMudj12O3RoaXMudD12O3RoaXMudmVsPTA7dGhpcy5tPW07"
    "dGhpcy5rPWs7dGhpcy5kPWQ7fQpTcHJpbmcucHJvdG90eXBlLnN0ZXA9ZnVuY3Rpb24oZHQpe2Nv"
    "bnN0IEY9LXRoaXMuayoodGhpcy52LXRoaXMudCktdGhpcy5kKnRoaXMudmVsOwogIHRoaXMudmVs"
    "Kz0oRi90aGlzLm0pKmR0O3RoaXMudis9dGhpcy52ZWwqZHQ7cmV0dXJuIHRoaXMudjt9OwoKLyog"
    "MDIgKyAyNSDCtyBhbmltYXRlZCBkb2NrOiBkaXN0YW5jZeKGknNjYWxlIHdpdGggc3ByaW5nLCBw"
    "bHVzIHNsaWRpbmcgcGlsbCAqLwooZnVuY3Rpb24gZG9jaygpewogIGNvbnN0IGRrPWRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3IoIm5hdiN0YWJzIC5kb2NrIik7aWYoIWRrKXJldHVybjsKICBjb25zdCBi"
    "dG5zPVsuLi5kay5xdWVyeVNlbGVjdG9yQWxsKCJidXR0b24iKV0scGlsbD1kb2N1bWVudC5nZXRF"
    "bGVtZW50QnlJZCgiZHBpbGwiKTsKICBjb25zdCBGSU5FPW1hdGNoTWVkaWEoIihob3Zlcjpob3Zl"
    "cikgYW5kIChwb2ludGVyOmZpbmUpIikubWF0Y2hlcyYmIVJNOwogIGNvbnN0IHNwPWJ0bnMubWFw"
    "KCgpPT5uZXcgU3ByaW5nKDEpKSxwcz17eDpuZXcgU3ByaW5nKDAse2s6MjYwLGQ6MTF9KSx3Om5l"
    "dyBTcHJpbmcoMCx7azoyNjAsZDoxMX0pfTsKICBsZXQgbXg9SW5maW5pdHksbGFzdD1wZXJmb3Jt"
    "YW5jZS5ub3coKSxwbGFjZWQ9ZmFsc2U7CiAgZnVuY3Rpb24gcmVzZXRTY2FsZSgpe214PUluZmlu"
    "aXR5O2lmKCFGSU5FKWJ0bnMuZm9yRWFjaChiPT5iLnN0eWxlLnRyYW5zZm9ybT0iIik7fQogIGlm"
    "KEZJTkUpewogICAgZGsuYWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcm1vdmUiLGU9PntpZihlLnBv"
    "aW50ZXJUeXBlPT09Im1vdXNlIilteD1lLmNsaWVudFh9KTsKICAgIGRrLmFkZEV2ZW50TGlzdGVu"
    "ZXIoInBvaW50ZXJsZWF2ZSIsKCk9Pm14PUluZmluaXR5KTsKICAgIFsicG9pbnRlcnVwIiwicG9p"
    "bnRlcmNhbmNlbCIsInRvdWNoZW5kIiwidG91Y2hjYW5jZWwiLCJibHVyIl0uZm9yRWFjaChldj0+"
    "CiAgICAgIGFkZEV2ZW50TGlzdGVuZXIoZXYscmVzZXRTY2FsZSx7cGFzc2l2ZTp0cnVlfSkpOwog"
    "IH1lbHNle2J0bnMuZm9yRWFjaChiPT5iLnN0eWxlLnRyYW5zZm9ybT0iIik7fQogIGRvY3VtZW50"
    "LmFkZEV2ZW50TGlzdGVuZXIoInZpc2liaWxpdHljaGFuZ2UiLHJlc2V0U2NhbGUpOwoKICBmdW5j"
    "dGlvbiBwbGFjZShzbmFwKXsKICAgIGNvbnN0IG9uPWRrLnF1ZXJ5U2VsZWN0b3IoImJ1dHRvbi5v"
    "biIpOwogICAgaWYoIW9ufHwhcGlsbHx8IW9uLm9mZnNldFdpZHRofHwhZGsub2Zmc2V0V2lkdGgp"
    "cmV0dXJuOyAgIC8vIG5ldmVyIHBsYWNlIHdoaWxlIGhpZGRlbgogICAgcHMueC50PW9uLm9mZnNl"
    "dExlZnQtZGsuY2xpZW50TGVmdDtwcy53LnQ9b24ub2Zmc2V0V2lkdGg7CiAgICBwaWxsLnN0eWxl"
    "LnRvcD0ob24ub2Zmc2V0VG9wLWRrLmNsaWVudFRvcCkrInB4IjtwaWxsLnN0eWxlLmhlaWdodD1v"
    "bi5vZmZzZXRIZWlnaHQrInB4IjsKICAgIGlmKHNuYXB8fCFwbGFjZWQpe3BsYWNlZD10cnVlO3Bz"
    "Lngudj1wcy54LnQ7cHMueC52ZWw9MDtwcy53LnY9cHMudy50O3BzLncudmVsPTA7CiAgICAgIHBp"
    "bGwuc3R5bGUudHJhbnNmb3JtPSJ0cmFuc2xhdGVYKCIrcHMueC52KyJweCkiO3BpbGwuc3R5bGUu"
    "d2lkdGg9cHMudy52KyJweCI7CiAgICAgIHBpbGwuY2xhc3NMaXN0LmFkZCgicmVhZHkiKTt9CiAg"
    "fQogIG5ldyBSZXNpemVPYnNlcnZlcigoKT0+cGxhY2UoIXBsYWNlZCkpLm9ic2VydmUoZGspOwog"
    "IGFkZEV2ZW50TGlzdGVuZXIoInJlc2l6ZSIsKCk9PnBsYWNlKHRydWUpKTsKICBhZGRFdmVudExp"
    "c3RlbmVyKCJsb2FkIiwoKT0+cGxhY2UoIXBsYWNlZCkpOwogIHNldFRpbWVvdXQoKCk9PnBsYWNl"
    "KCFwbGFjZWQpLDYwKTtzZXRUaW1lb3V0KCgpPT5wbGFjZSghcGxhY2VkKSw0MDApOwoKICAoZnVu"
    "Y3Rpb24gbG9vcCh0KXsKICAgIGNvbnN0IGR0PU1hdGgubWluKC4wMzIsKHQtbGFzdCkvMTAwMCk7"
    "bGFzdD10OwogICAgaWYoRklORSl7YnRucy5mb3JFYWNoKChiLGkpPT57bGV0IGs9MDsKICAgICAg"
    "aWYobXghPT1JbmZpbml0eSl7Y29uc3Qgcj1iLmdldEJvdW5kaW5nQ2xpZW50UmVjdCgpOwogICAg"
    "ICAgIGNvbnN0IGRpc3Q9TWF0aC5hYnMoci5sZWZ0K3Iud2lkdGgvMi1teCk7az1NYXRoLm1heCgw"
    "LDEtZGlzdC8xNTApO30KICAgICAgc3BbaV0udD0xKy4zNCprKms7Y29uc3Qgdj1zcFtpXS5zdGVw"
    "KGR0KTsKICAgICAgYi5zdHlsZS50cmFuc2Zvcm09KHY+MS4wMDJ8fHNwW2ldLnZlbD4uMDF8fHNw"
    "W2ldLnZlbDwtLjAxKQogICAgICAgID8gInRyYW5zbGF0ZVkoIisoLTgqKHYtMSkpLnRvRml4ZWQo"
    "MikrInB4KSBzY2FsZSgiK3YudG9GaXhlZCgzKSsiKSIgOiAiIjt9KTt9CiAgICBpZihwaWxsJiZw"
    "bGFjZWQpe3BpbGwuc3R5bGUudHJhbnNmb3JtPSJ0cmFuc2xhdGVYKCIrcHMueC5zdGVwKGR0KS50"
    "b0ZpeGVkKDIpKyJweCkiOwogICAgICBwaWxsLnN0eWxlLndpZHRoPXBzLncuc3RlcChkdCkudG9G"
    "aXhlZCgyKSsicHgiO30KICAgIHJlcXVlc3RBbmltYXRpb25GcmFtZShsb29wKTt9KShsYXN0KTsK"
    "ICB3aW5kb3cuX19kb2NrUGxhY2U9cGxhY2U7cGxhY2UodHJ1ZSk7Cn0pKCk7CgovKiAxNCDCtyB2"
    "LXRhYnMgc2xpZGluZyBpbmRpY2F0b3IgZm9yIHRoZSBhZG1pbiBzdWJuYXYgKi8KKGZ1bmN0aW9u"
    "IHZ0YWJzKCl7CiAgY29uc3QgbmF2PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJhc3ViIikscGls"
    "bD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgidnBpbGwiKTtpZighbmF2fHwhcGlsbClyZXR1cm47"
    "CiAgY29uc3QgcHg9bmV3IFNwcmluZygwLHtrOjIxMCxkOjE5fSkscHc9bmV3IFNwcmluZygwLHtr"
    "OjIxMCxkOjE5fSk7bGV0IGxhc3Q9cGVyZm9ybWFuY2Uubm93KCksaW5pdD1mYWxzZTsKICBmdW5j"
    "dGlvbiBwbGFjZShzbmFwKXtjb25zdCBvbj1uYXYucXVlcnlTZWxlY3RvcigiYnV0dG9uLm9uIik7"
    "CiAgICBpZighb258fCFvbi5vZmZzZXRXaWR0aHx8IW5hdi5vZmZzZXRXaWR0aClyZXR1cm47CiAg"
    "ICBweC50PW9uLm9mZnNldExlZnQtbmF2LmNsaWVudExlZnQ7cHcudD1vbi5vZmZzZXRXaWR0aDsK"
    "ICAgIHBpbGwuc3R5bGUudG9wPShvbi5vZmZzZXRUb3AtbmF2LmNsaWVudFRvcCkrInB4IjtwaWxs"
    "LnN0eWxlLmhlaWdodD1vbi5vZmZzZXRIZWlnaHQrInB4IjsKICAgIGlmKHNuYXB8fCFpbml0KXtp"
    "bml0PXRydWU7cHgudj1weC50O3B4LnZlbD0wO3B3LnY9cHcudDtwdy52ZWw9MDsKICAgICAgcGls"
    "bC5zdHlsZS50cmFuc2Zvcm09InRyYW5zbGF0ZVgoIitweC52KyJweCkiO3BpbGwuc3R5bGUud2lk"
    "dGg9cHcudisicHgiO319CiAgbmF2LmFkZEV2ZW50TGlzdGVuZXIoImNsaWNrIiwoKT0+cmVxdWVz"
    "dEFuaW1hdGlvbkZyYW1lKCgpPT5wbGFjZShmYWxzZSkpKTsKICBuYXYuYWRkRXZlbnRMaXN0ZW5l"
    "cigic2Nyb2xsIiwoKT0+cGxhY2UoZmFsc2UpKTsKICBuZXcgUmVzaXplT2JzZXJ2ZXIoKCk9PnBs"
    "YWNlKCFpbml0KSkub2JzZXJ2ZShuYXYpOwogIGFkZEV2ZW50TGlzdGVuZXIoImxvYWQiLCgpPT5w"
    "bGFjZSghaW5pdCkpO3NldFRpbWVvdXQoKCk9PnBsYWNlKCFpbml0KSw4MCk7cGxhY2UodHJ1ZSk7"
    "CiAgKGZ1bmN0aW9uIGxvb3AodCl7Y29uc3QgZHQ9TWF0aC5taW4oLjAzMiwodC1sYXN0KS8xMDAw"
    "KTtsYXN0PXQ7CiAgICBpZihpbml0KXtwaWxsLnN0eWxlLnRyYW5zZm9ybT0idHJhbnNsYXRlWCgi"
    "K3B4LnN0ZXAoZHQpLnRvRml4ZWQoMikrInB4KSI7cGlsbC5zdHlsZS53aWR0aD1wdy5zdGVwKGR0"
    "KS50b0ZpeGVkKDIpKyJweCI7fQogICAgcmVxdWVzdEFuaW1hdGlvbkZyYW1lKGxvb3ApO30pKGxh"
    "c3QpOwogIHdpbmRvdy5fX3Z0YWJQbGFjZT1wbGFjZTsKfSkoKTsKCi8qIDA4IMK3IGtpbmV0aWMg"
    "Z3JpZCDigJQgcG9pbnRlciB3YXJwICsgY2xpY2sgcmlwcGxlcyAoY2FudmFzIHBvcnQpICovCihm"
    "dW5jdGlvbiBraW5ldGljKCl7CiAgY29uc3QgY3Y9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImtn"
    "Iik7aWYoIWN2fHxSTSlyZXR1cm47Y29uc3QgY3R4PWN2LmdldENvbnRleHQoIjJkIik7CiAgY29u"
    "c3QgU1A9MzAsSU5GPTIxMCxXQVJQPTIyLExFUlA9LjA5LFJHQj0iMTM5LDEyMywyNTUiOwogIGxl"
    "dCBXPTAsSD0wLG1vdXNlPXt4Oi05OTk5LHk6LTk5OTl9LHRndD17eDotOTk5OSx5Oi05OTk5fSxy"
    "aXA9W10scmFmPTA7CiAgZnVuY3Rpb24gc2l6ZSgpe2NvbnN0IGQ9TWF0aC5taW4oMS42LGRldmlj"
    "ZVBpeGVsUmF0aW98fDEpO1c9aW5uZXJXaWR0aDtIPWlubmVySGVpZ2h0OwogICAgY3Yud2lkdGg9"
    "VypkO2N2LmhlaWdodD1IKmQ7Y3Yuc3R5bGUud2lkdGg9VysicHgiO2N2LnN0eWxlLmhlaWdodD1I"
    "KyJweCI7Y3R4LnNldFRyYW5zZm9ybShkLDAsMCxkLDAsMCk7fQogIGFkZEV2ZW50TGlzdGVuZXIo"
    "InJlc2l6ZSIsc2l6ZSk7c2l6ZSgpOwogIGFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJtb3ZlIixl"
    "PT57dGd0PXt4OmUuY2xpZW50WCx5OmUuY2xpZW50WX19LHtwYXNzaXZlOnRydWV9KTsKICBhZGRF"
    "dmVudExpc3RlbmVyKCJwb2ludGVyZG93biIsZT0+e2lmKHJpcC5sZW5ndGg8NClyaXAucHVzaCh7"
    "eDplLmNsaWVudFgseTplLmNsaWVudFkscjowLG86MX0pfSx7cGFzc2l2ZTp0cnVlfSk7CiAgYWRk"
    "RXZlbnRMaXN0ZW5lcigicG9pbnRlcmxlYXZlIiwoKT0+dGd0PXt4Oi05OTk5LHk6LTk5OTl9KTsK"
    "ICBmdW5jdGlvbiB3YXJwKGd4LGd5LGMscixjb2xzLHJvd3MpewogICAgY29uc3QgY3A9TWF0aC5t"
    "aW4oYy8xLjUsKGNvbHMtMS1jKS8xLjUsMSkscnA9TWF0aC5taW4oci8xLjUsKHJvd3MtMS1yKS8x"
    "LjUsMSkscGluPWNwKmNwKnJwKnJwOwogICAgY29uc3QgZHg9Z3gtbW91c2UueCxkeT1neS1tb3Vz"
    "ZS55LGRpc3Q9TWF0aC5oeXBvdChkeCxkeSk7CiAgICBjb25zdCBwcm94PU1hdGgubWF4KDAsMS1k"
    "aXN0L0lORikqcGluO2xldCByeD0wLHJ5PTA7CiAgICBmb3IoY29uc3QgcCBvZiByaXApe2NvbnN0"
    "IGRkeD1neC1wLngsZGR5PWd5LXAueSxkZD1NYXRoLmh5cG90KGRkeCxkZHkpLGRpZmY9ZGQtcC5y"
    "OwogICAgICBpZihNYXRoLmFicyhkaWZmKTw1NSl7Y29uc3Qgc3Q9KDEtTWF0aC5hYnMoZGlmZikv"
    "NTUpKnAubyoxNipwaW4sYW49TWF0aC5hdGFuMihkZHksZGR4KSxzZz1kaWZmPDA/LTE6MTsKICAg"
    "ICAgICByeCs9TWF0aC5jb3MoYW4pKnN0KnNnKi0xO3J5Kz1NYXRoLnNpbihhbikqc3Qqc2cqLTE7"
    "fX0KICAgIGlmKGRpc3Q8SU5GJiZkaXN0PjAmJnBpbj4wKXtjb25zdCB0PWRpc3QvSU5GLGU9KDEt"
    "dCkqKDEtdCkqTWF0aC5taW4oMSxkaXN0LzYwKSx3PWUqV0FSUCpwaW4sYW49TWF0aC5hdGFuMihk"
    "eSxkeCk7CiAgICAgIHJldHVybltneC1NYXRoLmNvcyhhbikqdytyeCxneS1NYXRoLnNpbihhbikq"
    "dytyeSxwcm94XTt9CiAgICByZXR1cm5bZ3grcngsZ3krcnkscHJveF07fQogIGZ1bmN0aW9uIGZy"
    "YW1lKCl7CiAgICBtb3VzZS54Kz0odGd0LngtbW91c2UueCkqTEVSUDttb3VzZS55Kz0odGd0Lnkt"
    "bW91c2UueSkqTEVSUDsKICAgIHJpcD1yaXAuZmlsdGVyKHA9PntwLnIrPTc7cC5vLT0uMDE2O3Jl"
    "dHVybiBwLm8+MH0pOwogICAgY3R4LmNsZWFyUmVjdCgwLDAsVyxIKTsKICAgIGNvbnN0IGNvbHM9"
    "TWF0aC5jZWlsKFcvU1ApKzEscm93cz1NYXRoLmNlaWwoSC9TUCkrMSxwdHM9W107CiAgICBmb3Io"
    "bGV0IHI9MDtyPHJvd3M7cisrKXtjb25zdCByb3c9W107Zm9yKGxldCBjPTA7Yzxjb2xzO2MrKyly"
    "b3cucHVzaCh3YXJwKGMqU1AscipTUCxjLHIsY29scyxyb3dzKSk7cHRzLnB1c2gocm93KTt9CiAg"
    "ICBmb3IobGV0IHI9MDtyPHJvd3M7cisrKWZvcihsZXQgYz0wO2M8Y29scztjKyspe2NvbnN0IHA9"
    "cHRzW3JdW2NdOwogICAgICBpZihjPGNvbHMtMSl7Y29uc3QgcT1wdHNbcl1bYysxXTtjdHguc3Ry"
    "b2tlU3R5bGU9InJnYmEoIitSR0IrIiwiKyguMDUrcFsyXSouMjIpLnRvRml4ZWQoMykrIikiOwog"
    "ICAgICAgIGN0eC5saW5lV2lkdGg9MTtjdHguYmVnaW5QYXRoKCk7Y3R4Lm1vdmVUbyhwWzBdLHBb"
    "MV0pO2N0eC5saW5lVG8ocVswXSxxWzFdKTtjdHguc3Ryb2tlKCk7fQogICAgICBpZihyPHJvd3Mt"
    "MSl7Y29uc3QgcT1wdHNbcisxXVtjXTtjdHguc3Ryb2tlU3R5bGU9InJnYmEoIitSR0IrIiwiKygu"
    "MDUrcFsyXSouMjIpLnRvRml4ZWQoMykrIikiOwogICAgICAgIGN0eC5saW5lV2lkdGg9MTtjdHgu"
    "YmVnaW5QYXRoKCk7Y3R4Lm1vdmVUbyhwWzBdLHBbMV0pO2N0eC5saW5lVG8ocVswXSxxWzFdKTtj"
    "dHguc3Ryb2tlKCk7fQogICAgICBpZihwWzJdPi4wNil7Y3R4LmZpbGxTdHlsZT0icmdiYSgiK1JH"
    "QisiLCIrKHBbMl0qLjUpLnRvRml4ZWQoMykrIikiO2N0eC5iZWdpblBhdGgoKTsKICAgICAgICBj"
    "dHguYXJjKHBbMF0scFsxXSwxLjYrcFsyXSoxLjYsMCw2LjI4Myk7Y3R4LmZpbGwoKTt9fQogICAg"
    "cmFmPXJlcXVlc3RBbmltYXRpb25GcmFtZShmcmFtZSk7fQogIGZyYW1lKCk7CiAgZG9jdW1lbnQu"
    "YWRkRXZlbnRMaXN0ZW5lcigidmlzaWJpbGl0eWNoYW5nZSIsKCk9PntpZihkb2N1bWVudC5oaWRk"
    "ZW4pY2FuY2VsQW5pbWF0aW9uRnJhbWUocmFmKTtlbHNlIGZyYW1lKCk7fSk7Cn0pKCk7CgovKiAw"
    "MSDCtyBkb3QgbG9hZGVyIHNwbGFzaCAoa2l0IGZyYW1lIGRhdGEpICovCihmdW5jdGlvbiBzcGxh"
    "c2goKXsKICBjb25zdCBnPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJkZyIpLHNwPWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJzcGxhc2giKTtpZighZylyZXR1cm47CiAgZm9yKGxldCBpPTA7aTw0"
    "OTtpKyspZy5hcHBlbmRDaGlsZChkb2N1bWVudC5jcmVhdGVFbGVtZW50KCJpIikpOwogIGNvbnN0"
    "IGNlbGxzPVsuLi5nLmNoaWxkcmVuXTsKICBjb25zdCBGPVtbMTQsNywwLDgsNiwxMywyMF0sWzE0"
    "LDcsMTMsMjAsMTYsMjcsMjFdLFsxNCwyMCwyNywyMSwzNCwyNCwyOF0sWzI3LDIxLDM0LDI4LDQx"
    "LDMyLDM1XSwKICAgWzM0LDI4LDQxLDM1LDQ4LDQwLDQyXSxbMzQsMjgsNDEsMzUsNDgsNDIsNDZd"
    "LFszNCwyOCw0MSwzNSw0OCw0MiwzOF0sWzM0LDI4LDQxLDM1LDQ4LDMwLDIxXSwKICAgWzM0LDI4"
    "LDQxLDQ4LDIxLDIyLDE0XSxbMzQsMjgsNDEsMjEsMTQsMTYsMjddLFszNCwyOCwyMSwxNCwxMCwy"
    "MCwyN10sWzI4LDIxLDE0LDQsMTMsMjAsMjddLAogICBbMjgsMjEsMTQsMTIsNiwxMywyMF0sWzI4"
    "LDIxLDE0LDYsMTMsMjAsMTFdLFsyOCwyMSwxNCw2LDEzLDIwLDEwXSxbMTQsNiwxMywyMCw5LDcs"
    "MjFdXTsKICBsZXQgZj0wO2NvbnN0IGl2PXNldEludGVydmFsKCgpPT57Y2VsbHMuZm9yRWFjaChj"
    "PT5jLmNsYXNzTGlzdC5yZW1vdmUoImFjdCIpKTsKICAgIEZbZiVGLmxlbmd0aF0uZm9yRWFjaChp"
    "PT5jZWxsc1tpXSYmY2VsbHNbaV0uY2xhc3NMaXN0LmFkZCgiYWN0IikpO2YrKzt9LDEwMCk7CiAg"
    "c2V0VGltZW91dCgoKT0+e2NsZWFySW50ZXJ2YWwoaXYpO3NwLmNsYXNzTGlzdC5hZGQoImdvbmUi"
    "KTsKICAgIHNldFRpbWVvdXQoKCk9PntzcC5yZW1vdmUoKTtpZih3aW5kb3cuX19kb2NrUGxhY2Up"
    "d2luZG93Ll9fZG9ja1BsYWNlKHRydWUpOwogICAgICBpZih3aW5kb3cuX192dGFiUGxhY2Upd2lu"
    "ZG93Ll9fdnRhYlBsYWNlKHRydWUpO30sNjAwKTt9LFJNPzMwMDoxNzUwKTsKfSkoKTsKCi8qIHN0"
    "YWdnZXJlZCByZXZlYWwgb24gZXZlcnkgdmlldyBjaGFuZ2UgKi8KZnVuY3Rpb24gcmV2ZWFsU3Rh"
    "Z2UoZWwpewogIGlmKCFlbHx8Uk0pcmV0dXJuOwogIFsuLi5lbC5jaGlsZHJlbl0uc2xpY2UoMCwx"
    "NCkuZm9yRWFjaCgoYyxpKT0+ewogICAgaWYoIWMuYW5pbWF0ZSlyZXR1cm47CiAgICB0cnl7CiAg"
    "ICAgIGMuZ2V0QW5pbWF0aW9ucygpLmZvckVhY2goYT0+e2lmKGEuaWQ9PT0icnYiKWEuY2FuY2Vs"
    "KCk7fSk7CiAgICAgIGNvbnN0IGFuPWMuYW5pbWF0ZShbe29wYWNpdHk6MCx0cmFuc2Zvcm06InRy"
    "YW5zbGF0ZVkoMTRweCkgc2NhbGUoLjk4NSkifSx7b3BhY2l0eToxLHRyYW5zZm9ybToibm9uZSJ9"
    "XSwKICAgICAgICB7ZHVyYXRpb246NTIwLGRlbGF5OmkqNDAsZWFzaW5nOiJjdWJpYy1iZXppZXIo"
    "LjIyLDEsLjM2LDEpIixmaWxsOiJiYWNrd2FyZHMifSk7CiAgICAgIGFuLmlkPSJydiI7CiAgICB9"
    "Y2F0Y2goZSl7fQogIH0pO30KCi8qIG51bWJlciB0aWNrZXIgKi8KZnVuY3Rpb24gdGlja1RvKGVs"
    "LHRvLGR1cil7aWYoIWVsKXJldHVybjtjb25zdCBmcm9tPXBhcnNlRmxvYXQoZWwudGV4dENvbnRl"
    "bnQucmVwbGFjZSgvW15cZC5dL2csIiIpKXx8MDsKICBpZihSTSl7ZWwudGV4dENvbnRlbnQ9dG8u"
    "dG9GaXhlZCgyKTtyZXR1cm59CiAgY29uc3QgdDA9cGVyZm9ybWFuY2Uubm93KCk7KGZ1bmN0aW9u"
    "IHN0KHQpe2NvbnN0IHA9TWF0aC5taW4oMSwodC10MCkvKGR1cnx8NjAwKSk7CiAgICBjb25zdCBl"
    "PTEtTWF0aC5wb3coMS1wLDMpO2VsLnRleHRDb250ZW50PShmcm9tKyh0by1mcm9tKSplKS50b0Zp"
    "eGVkKDIpOwogICAgaWYocDwxKXJlcXVlc3RBbmltYXRpb25GcmFtZShzdCk7fSkodDApO30KCi8q"
    "IHByZXNzIHJpcHBsZSBvbiBldmVyeSBidXR0b24gKi8KZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5l"
    "cigicG9pbnRlcmRvd24iLGU9Pntjb25zdCBiPWUudGFyZ2V0LmNsb3Nlc3QoIi5idG4sLmNoaXBy"
    "b3cgYnV0dG9uLC5zdWJuYXYgYnV0dG9uIik7CiAgaWYoIWJ8fFJNKXJldHVybjtjb25zdCByPWIu"
    "Z2V0Qm91bmRpbmdDbGllbnRSZWN0KCksZD1NYXRoLm1heChyLndpZHRoLHIuaGVpZ2h0KSoxLjQ7"
    "CiAgY29uc3Qgcz1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCJzcGFuIik7cy5jbGFzc05hbWU9InJp"
    "cCI7CiAgcy5zdHlsZS5jc3NUZXh0PSJsZWZ0OiIrKGUuY2xpZW50WC1yLmxlZnQpKyJweDt0b3A6"
    "IisoZS5jbGllbnRZLXIudG9wKSsicHg7d2lkdGg6IitkKyJweDtoZWlnaHQ6IitkKyJweCI7CiAg"
    "aWYoZ2V0Q29tcHV0ZWRTdHlsZShiKS5wb3NpdGlvbj09PSJzdGF0aWMiKWIuc3R5bGUucG9zaXRp"
    "b249InJlbGF0aXZlIjsKICBiLmFwcGVuZENoaWxkKHMpO3NldFRpbWVvdXQoKCk9PnMucmVtb3Zl"
    "KCksNTYwKTt9KTsKCi8qIDE1IMK3IHRpbHQgKyBnbGFyZSAqLwpmdW5jdGlvbiBiaW5kVGlsdCgp"
    "e2RvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXRpbHRdIikuZm9yRWFjaChjPT57CiAg"
    "aWYoYy5fdHx8Uk0pcmV0dXJuO2MuX3Q9MTsKICBjLmFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJt"
    "b3ZlIixlPT57Y29uc3Qgcj1jLmdldEJvdW5kaW5nQ2xpZW50UmVjdCgpOwogICAgY29uc3QgeD0o"
    "ZS5jbGllbnRYLXIubGVmdCkvci53aWR0aCx5PShlLmNsaWVudFktci50b3ApL3IuaGVpZ2h0Owog"
    "ICAgYy5zdHlsZS50cmFuc2Zvcm09InBlcnNwZWN0aXZlKDcwMHB4KSByb3RhdGVYKCIrKCguNS15"
    "KSo5KS50b0ZpeGVkKDIpKyJkZWcpIHJvdGF0ZVkoIisoKHgtLjUpKjExKS50b0ZpeGVkKDIpKyJk"
    "ZWcpIHRyYW5zbGF0ZVooNHB4KSI7CiAgICBjLnN0eWxlLnNldFByb3BlcnR5KCItLW14IiwoeCox"
    "MDApKyIlIik7Yy5zdHlsZS5zZXRQcm9wZXJ0eSgiLS1teSIsKHkqMTAwKSsiJSIpO30pOwogIGMu"
    "YWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcmxlYXZlIiwoKT0+Yy5zdHlsZS50cmFuc2Zvcm09IiIp"
    "O30pO30KCi8qIHN1Y2Nlc3MgYnVyc3QgKi8KZnVuY3Rpb24gYnVyc3QoKXtjb25zdCBmeD1kb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgiZngiKTtpZighZngpcmV0dXJuO2Z4LmNsYXNzTGlzdC5hZGQo"
    "Im9uIik7CiAgY29uc3Qgdz1meC5xdWVyeVNlbGVjdG9yKCIud2F2ZSIpO3cuc3R5bGUuYW5pbWF0"
    "aW9uPSJub25lIjt2b2lkIHcub2Zmc2V0V2lkdGg7dy5zdHlsZS5hbmltYXRpb249IiI7CiAgc2V0"
    "VGltZW91dCgoKT0+ZnguY2xhc3NMaXN0LnJlbW92ZSgib24iKSw5MDApO30KCi8qIOKUgOKUgCBQ"
    "YXkgKyB2ZXJpZnkgZGVwb3NpdCBmbG93ICg1LW1pbnV0ZSB3aW5kb3csIHZlcmlmeSBvbmx5IG9u"
    "IHRhcCkg4pSA4pSAICovCmxldCBkZXBUaWNrPW51bGwsZGVwSm9iPW51bGw7CmZ1bmN0aW9uIGFt"
    "b3VudFNoZWV0KCl7CiAgY29uc3QgcGlja3M9WzUwLDEwMCwyMDAsNTAwLDEwMDAsMjAwMF07CiAg"
    "c2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPiR7Q01TLndh"
    "bEtpY2t8fCJBZGQgZnVuZHMifTwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj5QaWNrIGFuIGFtb3Vu"
    "dCBvciB0eXBlIHlvdXIgb3duLjwvcD4KICAgIDxkaXYgY2xhc3M9ImNoaXBzIiBzdHlsZT0ibWFy"
    "Z2luOjEycHggMCA0cHg7ZGlzcGxheTpmbGV4O2ZsZXgtd3JhcDp3cmFwO2dhcDo3cHgiPgogICAg"
    "ICAke3BpY2tzLm1hcCh2PT5gPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBkYXRhLXBpY2s9IiR7dn0i"
    "IHN0eWxlPSJmbGV4OjAgMCBhdXRvIj4ke21vbmV5KHYpfTwvYnV0dG9uPmApLmpvaW4oIiIpfQog"
    "ICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+"
    "PGxhYmVsPkN1c3RvbSBhbW91bnQ8L2xhYmVsPgogICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9"
    "ImFtdGluIiB0eXBlPSJudW1iZXIiIGlucHV0bW9kZT0ibnVtZXJpYyIgbWluPSIxIiBwbGFjZWhv"
    "bGRlcj0iRW50ZXIgYW1vdW50Ij48L2Rpdj4KICAgIDxwIGNsYXNzPSJzdWIiIGlkPSJhbXRoaW50"
    "IiBzdHlsZT0ibWFyZ2luOjAgMCAxMHB4O2ZvbnQtc2l6ZTouNzJyZW0iPjwvcD4KICAgIDxidXR0"
    "b24gY2xhc3M9ImJ0biBjdGEgYmxvY2siIGlkPSJhbXRnbyI+Q29udGludWU8L2J1dHRvbj4KICAg"
    "IDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4"
    "IiBkYXRhLWNsb3NlPkNhbmNlbDwvYnV0dG9uPmApOwogIGNvbnN0IGlucD1kb2N1bWVudC5nZXRF"
    "bGVtZW50QnlJZCgiYW10aW4iKSwgZ289ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFtdGdvIiks"
    "CiAgICAgICAgaGludD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYW10aGludCIpOwogIGNvbnN0"
    "IGxvPShBRE0mJkFETS5wYXkmJkFETS5wYXkubWluKXx8MTAsIGhpPShBRE0mJkFETS5wYXkmJkFE"
    "TS5wYXkubWF4KXx8MjAwMDA7CiAgaGludC50ZXh0Q29udGVudD0iQmV0d2VlbiAiK21vbmV5KGxv"
    "KSsiIGFuZCAiK21vbmV5KGhpKSsiLiI7CiAgY29uc3Qgc3RhcnQ9dj0+ewogICAgY29uc3Qgbj1O"
    "dW1iZXIodik7CiAgICBpZighbnx8bjw9MCl7dG9hc3QoIkVudGVyIGFuIGFtb3VudCBmaXJzdCIp"
    "O2lucCYmaW5wLmZvY3VzKCk7cmV0dXJuO30KICAgIGlmKG48bG98fG4+aGkpe3RvYXN0KCJBbGxv"
    "d2VkICIrbW9uZXkobG8pKyIgdG8gIittb25leShoaSkpO3JldHVybjt9CiAgICBkZXBvc2l0U2hl"
    "ZXQobik7CiAgfTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1waWNrXSIpLmZv"
    "ckVhY2goYj0+Yi5vbmNsaWNrPSgpPT5zdGFydChiLmRhdGFzZXQucGljaykpOwogIGdvLm9uY2xp"
    "Y2s9KCk9PnN0YXJ0KGlucC52YWx1ZSk7CiAgaWYoaW5wKWlucC5vbmtleWRvd249ZT0+e2lmKGUu"
    "a2V5PT09IkVudGVyIilzdGFydChpbnAudmFsdWUpO307CiAgc2V0VGltZW91dCgoKT0+e2lmKGlu"
    "cClpbnAuZm9jdXMoKTt9LDI1MCk7Cn0KYXN5bmMgZnVuY3Rpb24gbGl2ZURlcG9zaXRTaGVldChh"
    "KXsKICBjbGVhckludGVydmFsKGRlcFRpY2spO2NsZWFyVGltZW91dChkZXBKb2IpOwogIGNvbnN0"
    "IGQ9YXdhaXQgYXBpVHJ5KCIvYXBpL2RlcG9zaXQvY3JlYXRlIix7YW1vdW50Ok51bWJlcihhKX0p"
    "OwogIGlmKCFkKXJldHVybjsKICBjb25zdCBtaW5zPWQud2luZG93fHw1OwogIGxldCBsZWZ0PU1h"
    "dGgubWF4KDAsTWF0aC5yb3VuZCgobmV3IERhdGUoZC5leHBpcmVzX2F0KS1uZXcgRGF0ZSgpKS8x"
    "MDAwKSl8fG1pbnMqNjA7CiAgY29uc3QgdG90YWw9bWlucyo2MDsKICBsZXQgZG9uZT1mYWxzZSxi"
    "dXN5PWZhbHNlOwogIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZTox"
    "cmVtIj4ke0NNUy5zaGVldFRpdGxlfSAke21vbmV5KGQuYW1vdW50KX08L2gzPgogICAgPHAgY2xh"
    "c3M9InN1YiI+JHtDTVMuc2hlZXRTdWIucmVwbGFjZSgie219IixtaW5zKX08L3A+CiAgICAke2Qu"
    "cXI/YDxkaXYgY2xhc3M9InFyIiBzdHlsZT0icGFkZGluZzowO292ZXJmbG93OmhpZGRlbiI+PGlt"
    "ZyBzcmM9IiR7ZC5xcn0iIGFsdD0iVVBJIFFSIiBzdHlsZT0id2lkdGg6MTAwJTtkaXNwbGF5OmJs"
    "b2NrIj48L2Rpdj5gOiIifQogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlVQSSBJRDwvc3Bhbj48"
    "Yj4ke2VzYyhkLnVwaXx8IiIpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Q"
    "YXllZTwvc3Bhbj48Yj4ke2VzYyhkLnBheWVlfHwiIil9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFz"
    "cz0ia3YiPjxzcGFuPkFtb3VudDwvc3Bhbj48Yj4ke21vbmV5KGQuYW1vdW50KX08L2I+PC9kaXY+"
    "CiAgICAke2QuYm9udXM/YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Cb251czwvc3Bhbj48Yj4rJHtt"
    "b25leShkLmJvbnVzKX08L2I+PC9kaXY+YDoiIn0KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5O"
    "b3RlIC8gcmVmZXJlbmNlPC9zcGFuPjxiPiR7ZXNjKGQuY29kZSl9PC9iPjwvZGl2PgogICAgPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPkV4cGlyZXMgaW48L3NwYW4+PGIgaWQ9ImRwY2QiPiR7TWF0aC5m"
    "bG9vcihsZWZ0LzYwKX06JHtTdHJpbmcobGVmdCU2MCkucGFkU3RhcnQoMiwiMCIpfTwvYj48L2Rp"
    "dj4KICAgIDxkaXYgY2xhc3M9InRpbWVyYmFyIiBpZD0iZHBiYXIiPjxpIHN0eWxlPSJ3aWR0aDox"
    "MDAlIj48L2k+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJzdGVwcyIgaWQ9ImRzdGVwcyI+CiAgICAg"
    "IDxkaXYgY2xhc3M9InN0ZXAgb24iPjxpPjE8L2k+JHtDTVMuc3RlcDF9PC9kaXY+CiAgICAgIDxk"
    "aXYgY2xhc3M9InN0ZXAiIGlkPSJzMiI+PGk+MjwvaT4ke0NNUy5zdGVwMn08L2Rpdj4KICAgICAg"
    "PGRpdiBjbGFzcz0ic3RlcCIgaWQ9InMzIj48aT4zPC9pPiR7Q01TLnN0ZXAzfTwvZGl2PgogICAg"
    "ICA8ZGl2IGNsYXNzPSJzdGVwIiBpZD0iczQiPjxpPjQ8L2k+JHtDTVMuc3RlcDR9PC9kaXY+CiAg"
    "ICA8L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij4K"
    "ICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0iZHBheSI+8J+PpiBDb3B5IFVQSSBJRDwvYnV0"
    "dG9uPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIiBpZD0iZHZlciI+JHtTVE9SRS5hdXRv"
    "Y2hlY2s9PT1mYWxzZT8i4pyFIEkgaGF2ZSBwYWlkIjoi4pyFICIrQ01TLnZlcmlmeUJ0bn08L2J1"
    "dHRvbj48L2Rpdj4KICAgICR7U1RPUkUuYXV0b2NoZWNrPT09ZmFsc2U/IiI6YDxidXR0b24gY2xh"
    "c3M9ImJ0biBzb2Z0IGJsb2NrIHNtIiBpZD0iZHV0ciIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij7w"
    "n6e+IFNlbmQgdHJhbnNhY3Rpb24gbm8uPC9idXR0b24+YH0KICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNsb3NlPkNsb3Nl"
    "PC9idXR0b24+YCk7CiAgY29uc3QgZz1pZD0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoaWQpOwog"
    "IGNvbnN0IHBheUI9ZygiZHBheSIpLHZlckI9ZygiZHZlciIpLHV0ckI9ZygiZHV0ciIpOwogIGNv"
    "bnN0IGFsaXZlPSgpPT5nKCJkcGNkIikmJmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzaGVldCIp"
    "LmNsYXNzTGlzdC5jb250YWlucygib3BlbiIpOwogIGZ1bmN0aW9uIGV4cGlyZSgpewogICAgY2xl"
    "YXJJbnRlcnZhbChkZXBUaWNrKTsKICAgIGlmKCFhbGl2ZSgpKXJldHVybjsKICAgIGcoImRwY2Qi"
    "KS50ZXh0Q29udGVudD0iMDowMCI7ZygiZHBiYXIiKS5jbGFzc0xpc3QuYWRkKCJsb3ciKTtnKCJk"
    "cGJhciIpLmZpcnN0Q2hpbGQuc3R5bGUud2lkdGg9IjAlIjsKICAgIHZlckIuZGlzYWJsZWQ9dHJ1"
    "ZTt1dHJCLmRpc2FibGVkPXRydWU7dmVyQi50ZXh0Q29udGVudD0iRXhwaXJlZCI7CiAgICBwYXlC"
    "LmNsYXNzTmFtZT0iYnRuIjtwYXlCLnRleHRDb250ZW50PSJOZXcgUmVxdWVzdCI7cGF5Qi5vbmNs"
    "aWNrPSgpPT5kZXBvc2l0U2hlZXQoYSk7CiAgICB0b2FzdChDTVMubXNnRXhwaXJlZCk7CiAgfQog"
    "IGRlcFRpY2s9c2V0SW50ZXJ2YWwoKCk9PnsKICAgIGlmKCFhbGl2ZSgpKXtjbGVhckludGVydmFs"
    "KGRlcFRpY2spO3JldHVybjt9CiAgICBsZWZ0LS07CiAgICBnKCJkcGNkIikudGV4dENvbnRlbnQ9"
    "TWF0aC5tYXgoMCxNYXRoLmZsb29yKGxlZnQvNjApKSsiOiIrU3RyaW5nKE1hdGgubWF4KDAsbGVm"
    "dCU2MCkpLnBhZFN0YXJ0KDIsIjAiKTsKICAgIGcoImRwYmFyIikuZmlyc3RDaGlsZC5zdHlsZS53"
    "aWR0aD0oTWF0aC5tYXgoMCxsZWZ0KS90b3RhbCoxMDApKyIlIjsKICAgIGcoImRwYmFyIikuY2xh"
    "c3NMaXN0LnRvZ2dsZSgibG93IixsZWZ0PD02MCk7CiAgICBpZihsZWZ0PD0wKWV4cGlyZSgpOwog"
    "IH0sMTAwMCk7CiAgaWYocGF5QilwYXlCLm9uY2xpY2s9KCk9PnsKICAgIGlmKGRvbmUpcmV0dXJu"
    "OwogICAgZygiczIiKS5jbGFzc05hbWU9InN0ZXAgb24iO2coInMzIikuY2xhc3NOYW1lPSJzdGVw"
    "IHJ1biI7CiAgICB2ZXJCLmNsYXNzTGlzdC5yZW1vdmUoImdsYXNzIik7dmVyQi5jbGFzc0xpc3Qu"
    "YWRkKCJjdGEiKTsKICAgIHRyeXtuYXZpZ2F0b3IuY2xpcGJvYXJkJiZuYXZpZ2F0b3IuY2xpcGJv"
    "YXJkLndyaXRlVGV4dChkLnVwaXx8IiIpO31jYXRjaChlKXt9CiAgICB0b2FzdCgiVVBJIElEIGNv"
    "cGllZDogIisoZC51cGl8fCIiKSk7CiAgfTsKICBpZih2ZXJCKXZlckIub25jbGljaz1hc3luYygp"
    "PT57CiAgICBpZihkb25lfHxidXN5fHxsZWZ0PD0wKXJldHVybjsKICAgIGlmKFNUT1JFLmF1dG9j"
    "aGVjaz09PWZhbHNlKXsgICAgICAgICAgLyogbm8gYXV0b21hdGljIGNoZWNraW5nIOKAlCBjb2xs"
    "ZWN0IHByb29mICovCiAgICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQt"
    "c2l6ZToxcmVtIj5TZW5kIHlvdXIgcGF5bWVudCBwcm9vZjwvaDM+CiAgICAgICAgPHAgY2xhc3M9"
    "InN1YiI+QSBzY3JlZW5zaG90IG9mIHRoZSBzdWNjZXNzZnVsIHBheW1lbnQgYW5kIHRoZSBVVFIu"
    "PC9wPgogICAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48"
    "bGFiZWw+U2NyZWVuc2hvdDwvbGFiZWw+CiAgICAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9"
    "InBmc2hvdCIgdHlwZT0iZmlsZSIgYWNjZXB0PSJpbWFnZS8qIj48L2Rpdj4KICAgICAgICA8ZGl2"
    "IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlVUUiAvIHRyYW5zYWN0aW9uIG51bWJlcjwvbGFiZWw+CiAg"
    "ICAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9InBmdXRyIiBpbnB1dG1vZGU9Im51bWVyaWMi"
    "IHBsYWNlaG9sZGVyPSIxMiBkaWdpdHMiPjwvZGl2PgogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0"
    "biBjdGEgYmxvY2siIGlkPSJwZmdvIj5TdWJtaXQgZm9yIHJldmlldzwvYnV0dG9uPgogICAgICAg"
    "IDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4"
    "IiBkYXRhLWNsb3NlPkNhbmNlbDwvYnV0dG9uPmApOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgicGZnbyIpLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICAgIGNvbnN0IGY9ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoInBmc2hvdCIpOwogICAgICAgIGNvbnN0IHU9KGRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJwZnV0ciIpLnZhbHVlfHwiIikucmVwbGFjZSgvXEQvZywiIik7CiAgICAgICAg"
    "aWYodS5sZW5ndGg8Nil7dG9hc3QoIkVudGVyIHRoZSBVVFIgZnJvbSB5b3VyIHBheW1lbnQgYXBw"
    "Iik7cmV0dXJuO30KICAgICAgICBjb25zdCBmaWxlPShmJiZmLmZpbGVzJiZmLmZpbGVzWzBdKXx8"
    "bnVsbDsKICAgICAgICBsZXQgc2hvdD0iIjsKICAgICAgICBpZihmaWxlKXsKICAgICAgICAgIGlm"
    "KGZpbGUuc2l6ZT44KjEwMjQqMTAyNCl7dG9hc3QoIlRoYXQgaW1hZ2UgaXMgdG9vIGxhcmdlICg4"
    "IE1CIG1heCkiKTtyZXR1cm47fQogICAgICAgICAgc2hvdD1hd2FpdCBuZXcgUHJvbWlzZShyZXM9"
    "Pntjb25zdCByZD1uZXcgRmlsZVJlYWRlcigpOwogICAgICAgICAgICByZC5vbmxvYWQ9KCk9PnJl"
    "cyhTdHJpbmcocmQucmVzdWx0fHwiIikpO3JkLm9uZXJyb3I9KCk9PnJlcygiIik7CiAgICAgICAg"
    "ICAgIHJkLnJlYWRBc0RhdGFVUkwoZmlsZSk7fSk7CiAgICAgICAgfQogICAgICAgIGNvbnN0IGJ0"
    "bj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicGZnbyIpO2J0bi5kaXNhYmxlZD10cnVlO2J0bi50"
    "ZXh0Q29udGVudD0iU2VuZGluZ+KApiI7CiAgICAgICAgY29uc3QgcnI9YXdhaXQgYXBpVHJ5KCIv"
    "YXBpL2RlcG9zaXQvY2xhaW0iLHtpZDpkLmlkLHV0cjp1LHNob3Q6c2hvdH0pOwogICAgICAgIGJ0"
    "bi5kaXNhYmxlZD1mYWxzZTtidG4udGV4dENvbnRlbnQ9IlN1Ym1pdCBmb3IgcmV2aWV3IjsKICAg"
    "ICAgICBpZighcnIpcmV0dXJuOwogICAgICAgIGNsb3NlU2hlZXQoKTt0b2FzdCgiU2VudCDigJQg"
    "d2Ugd2lsbCBjb25maXJtIGl0IHNob3J0bHkiKTsKICAgICAgICBhd2FpdCByZWZyZXNoKCk7CiAg"
    "ICAgIH07CiAgICAgIHJldHVybjsKICAgIH0KICAgIGJ1c3k9dHJ1ZTt2ZXJCLmRpc2FibGVkPXRy"
    "dWU7dmVyQi50ZXh0Q29udGVudD0iQ2hlY2tpbmfigKYiO2coInMzIikuY2xhc3NOYW1lPSJzdGVw"
    "IHJ1biI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9kZXBvc2l0L3ZlcmlmeSIse2lk"
    "OmQuaWR9KTsKICAgIGJ1c3k9ZmFsc2U7CiAgICBpZighYWxpdmUoKSlyZXR1cm47CiAgICB2ZXJC"
    "LmRpc2FibGVkPWZhbHNlO3ZlckIudGV4dENvbnRlbnQ9Q01TLnZlcmlmeUJ0bjsKICAgIGlmKCFy"
    "KXJldHVybjsKICAgIGlmKHIuc3RhdHVzPT09ImNvbXBsZXRlZCIpewogICAgICBkb25lPXRydWU7"
    "Y2xlYXJJbnRlcnZhbChkZXBUaWNrKTsKICAgICAgZygiczMiKS5jbGFzc05hbWU9InN0ZXAgb24i"
    "O2coInM0IikuY2xhc3NOYW1lPSJzdGVwIG9uIjsKICAgICAgaWYodHlwZW9mIHIuYmFsYW5jZT09"
    "PSJudW1iZXIiKUJBTD1yLmJhbGFuY2U7CiAgICAgIHN5bmNCYWwoKTtidXJzdCgpO3RvYXN0KENN"
    "Uy5tc2dPayk7CiAgICAgIHNldFRpbWVvdXQoKCk9PntjbG9zZVNoZWV0KCk7cmVmcmVzaCgpO30s"
    "OTAwKTsKICAgIH1lbHNlIGlmKHIuc3RhdHVzPT09InJldmlldyIpewogICAgICBnKCJzMyIpLmNs"
    "YXNzTmFtZT0ic3RlcCBvbiI7dG9hc3QoIlBheW1lbnQgc2VudCBmb3IgY2hlY2tpbmcg4oCUIHdl"
    "IHdpbGwgY29uZmlybSBpdCBzaG9ydGx5Iik7CiAgICB9ZWxzZSBpZihyLnN0YXR1cz09PSJleHBp"
    "cmVkIil7CiAgICAgIGV4cGlyZSgpOwogICAgfWVsc2V7CiAgICAgIHRvYXN0KENNUy5tc2dOb25l"
    "KTsKICAgIH0KICB9OwogIGlmKHV0ckIpdXRyQi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0"
    "IHY9cHJvbXB0KCJFbnRlciB0aGUgMTItZGlnaXQgVVRSIGZyb20geW91ciBwYXltZW50IGFwcCIp"
    "OwogICAgaWYoIXYpcmV0dXJuOwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvZGVwb3Np"
    "dC91dHIiLHtpZDpkLmlkLHV0cjp2fSk7CiAgICBpZihyKXRvYXN0KCJUaGFua3Mg4oCUIHdlIGFy"
    "ZSBjaGVja2luZyB5b3VyIHBheW1lbnQiKTsKICB9Owp9CmZ1bmN0aW9uIGRlcG9zaXRTaGVldChh"
    "KXsKICBpZihMSVZFKXJldHVybiBsaXZlRGVwb3NpdFNoZWV0KGEpOwogIGNsZWFySW50ZXJ2YWwo"
    "ZGVwVGljayk7Y2xlYXJUaW1lb3V0KGRlcEpvYik7CiAgY29uc3QgY29kZT0oQ01TLnBheVByZWZp"
    "eHx8IlBBWSIpK01hdGguZmxvb3IoMTAwMCtNYXRoLnJhbmRvbSgpKjg5OTkpOwogIGNvbnN0IG1p"
    "bnM9TWF0aC5tYXgoMSxwYXJzZUludChDTVMucGF5V2luZG93LDEwKXx8NSksIHRvdGFsPW1pbnMq"
    "NjA7CiAgbGV0IGxlZnQ9dG90YWwscGFpZD1mYWxzZSxkb25lPWZhbHNlLGJ1c3k9ZmFsc2U7CiAg"
    "c2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPiR7Q01TLnNo"
    "ZWV0VGl0bGV9ICR7bW9uZXkoYSl9PC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPiR7Q01TLnNoZWV0"
    "U3ViLnJlcGxhY2UoInttfSIsbWlucyl9PC9wPgogICAgPGRpdiBjbGFzcz0icXIiPlFSIENPREU8"
    "YnI+KGdlbmVyYXRlZCBieSBib3QpPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VVBJ"
    "IElEPC9zcGFuPjxiPiR7ZXNjKFNUT1JFLnVwaXx8Im5vdCBzZXQgeWV0Iil9PC9iPjwvZGl2Pgog"
    "ICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlBheWVlPC9zcGFuPjxiPiR7ZXNjKFNUT1JFLnBheWVl"
    "fHwi4oCUIil9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkFtb3VudDwvc3Bh"
    "bj48Yj4ke21vbmV5KGEpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Ob3Rl"
    "IC8gcmVmZXJlbmNlPC9zcGFuPjxiPiR7Y29kZX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4+RXhwaXJlcyBpbjwvc3Bhbj48YiBpZD0iZHBjZCI+JHttaW5zfTowMDwvYj48L2Rp"
    "dj4KICAgIDxkaXYgY2xhc3M9InRpbWVyYmFyIiBpZD0iZHBiYXIiPjxpIHN0eWxlPSJ3aWR0aDox"
    "MDAlIj48L2k+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJzdGVwcyIgaWQ9ImRzdGVwcyI+CiAgICAg"
    "IDxkaXYgY2xhc3M9InN0ZXAgb24iPjxpPjE8L2k+JHtDTVMuc3RlcDF9PC9kaXY+CiAgICAgIDxk"
    "aXYgY2xhc3M9InN0ZXAiIGlkPSJzMiI+PGk+MjwvaT4ke0NNUy5zdGVwMn08L2Rpdj4KICAgICAg"
    "PGRpdiBjbGFzcz0ic3RlcCIgaWQ9InMzIj48aT4zPC9pPiR7Q01TLnN0ZXAzfTwvZGl2PgogICAg"
    "ICA8ZGl2IGNsYXNzPSJzdGVwIiBpZD0iczQiPjxpPjQ8L2k+JHtDTVMuc3RlcDR9PC9kaXY+CiAg"
    "ICA8L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij4K"
    "ICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIgaWQ9ImRwYXkiPiR7Q01TLnBheUJ0bn08L2J1"
    "dHRvbj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdsYXNzIiBpZD0iZHZlciI+JHtDTVMudmVy"
    "aWZ5QnRufTwvYnV0dG9uPjwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQgYmxvY2sg"
    "c20iIGlkPSJkdXRyIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPiR7Q01TLnV0ckJ0bn08L2J1dHRv"
    "bj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10"
    "b3A6OHB4IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+YCk7CiAgY29uc3QgZz1pZD0+ZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoaWQpOwogIGNvbnN0IHBheUI9ZygiZHBheSIpLHZlckI9ZygiZHZl"
    "ciIpLHV0ckI9ZygiZHV0ciIpOwogIGZ1bmN0aW9uIGZtdChuKXtyZXR1cm4gTWF0aC5mbG9vcihu"
    "LzYwKSsiOiIrU3RyaW5nKG4lNjApLnBhZFN0YXJ0KDIsIjAiKTt9CiAgZnVuY3Rpb24gYWxpdmUo"
    "KXtyZXR1cm4gZygiZHBjZCIpJiZkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic2hlZXQiKS5jbGFz"
    "c0xpc3QuY29udGFpbnMoIm9wZW4iKTt9CiAgZnVuY3Rpb24gZXhwaXJlKCl7CiAgICBjbGVhcklu"
    "dGVydmFsKGRlcFRpY2spO2NsZWFyVGltZW91dChkZXBKb2IpOwogICAgaWYoIWFsaXZlKCkpcmV0"
    "dXJuOwogICAgZygiZHBjZCIpLnRleHRDb250ZW50PSIwOjAwIjtnKCJkcGJhciIpLmNsYXNzTGlz"
    "dC5hZGQoImxvdyIpO2coImRwYmFyIikuZmlyc3RDaGlsZC5zdHlsZS53aWR0aD0iMCUiOwogICAg"
    "cGF5Qi5kaXNhYmxlZD10cnVlO3ZlckIuZGlzYWJsZWQ9dHJ1ZTt1dHJCLmRpc2FibGVkPXRydWU7"
    "CiAgICBnKCJzMiIpLmNsYXNzTmFtZT0ic3RlcCI7ZygiczMiKS5jbGFzc05hbWU9InN0ZXAiOwog"
    "ICAgdmVyQi50ZXh0Q29udGVudD0iRXhwaXJlZCI7cGF5Qi5jbGFzc05hbWU9ImJ0biI7cGF5Qi5k"
    "aXNhYmxlZD1mYWxzZTtwYXlCLnRleHRDb250ZW50PSJOZXcgUmVxdWVzdCI7CiAgICBwYXlCLm9u"
    "Y2xpY2s9KCk9PmRlcG9zaXRTaGVldChhKTsKICAgIHRvYXN0KENNUy5tc2dFeHBpcmVkKTsKICB9"
    "CiAgZGVwVGljaz1zZXRJbnRlcnZhbCgoKT0+ewogICAgaWYoIWFsaXZlKCkpe2NsZWFySW50ZXJ2"
    "YWwoZGVwVGljayk7cmV0dXJuO30KICAgIGxlZnQtLTtnKCJkcGNkIikudGV4dENvbnRlbnQ9Zm10"
    "KE1hdGgubWF4KDAsbGVmdCkpOwogICAgZygiZHBiYXIiKS5maXJzdENoaWxkLnN0eWxlLndpZHRo"
    "PShNYXRoLm1heCgwLGxlZnQpL3RvdGFsKjEwMCkrIiUiOwogICAgZygiZHBiYXIiKS5jbGFzc0xp"
    "c3QudG9nZ2xlKCJsb3ciLGxlZnQ8PTYwKTsKICAgIGlmKGxlZnQ8PTApZXhwaXJlKCk7CiAgfSwx"
    "MDAwKTsKICBpZihwYXlCKXBheUIub25jbGljaz0oKT0+ewogICAgaWYoZG9uZSlyZXR1cm47CiAg"
    "ICBwYWlkPXRydWU7ZygiczIiKS5jbGFzc05hbWU9InN0ZXAgb24iO2coInMzIikuY2xhc3NOYW1l"
    "PSJzdGVwIHJ1biI7CiAgICBwYXlCLnRleHRDb250ZW50PSJQYXkgQWdhaW4iO3ZlckIuY2xhc3NM"
    "aXN0LnJlbW92ZSgiZ2xhc3MiKTt2ZXJCLmNsYXNzTGlzdC5hZGQoImN0YSIpOwogICAgdG9hc3Qo"
    "Ik9wZW5pbmcgeW91ciBVUEkgYXBwIOKAlCBwYXkgIittb25leShhKSsiLCB0aGVuIHRhcCBWZXJp"
    "ZnkiKTsKICB9OwogIHZlckIub25jbGljaz0oKT0+ewogICAgaWYoZG9uZXx8YnVzeXx8bGVmdDw9"
    "MClyZXR1cm47CiAgICBidXN5PXRydWU7dmVyQi5kaXNhYmxlZD10cnVlO3ZlckIudGV4dENvbnRl"
    "bnQ9IkNoZWNraW5n4oCmIjsKICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIHJ1biI7dG9hc3Qo"
    "Q01TLm1zZ0NoZWNraW5nKTsKICAgIGRlcEpvYj1zZXRUaW1lb3V0KCgpPT57CiAgICAgIGJ1c3k9"
    "ZmFsc2U7CiAgICAgIGlmKCFhbGl2ZSgpKXJldHVybjsKICAgICAgaWYocGFpZCl7CiAgICAgICAg"
    "ZG9uZT10cnVlO2NsZWFySW50ZXJ2YWwoZGVwVGljayk7CiAgICAgICAgZygiczMiKS5jbGFzc05h"
    "bWU9InN0ZXAgb24iO2coInM0IikuY2xhc3NOYW1lPSJzdGVwIG9uIjsKICAgICAgICB2ZXJCLnRl"
    "eHRDb250ZW50PSJWZXJpZmllZCI7CiAgICAgICAgQkFMKz1OdW1iZXIoYSk7c3luY0JhbCgpO2J1"
    "cnN0KCk7dG9hc3QoQ01TLm1zZ09rKTsKICAgICAgICBzZXRUaW1lb3V0KGNsb3NlU2hlZXQsMTIw"
    "MCk7CiAgICAgIH1lbHNlewogICAgICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIjt2ZXJCLmRp"
    "c2FibGVkPWZhbHNlO3ZlckIudGV4dENvbnRlbnQ9Q01TLnZlcmlmeUJ0bjsKICAgICAgICB0b2Fz"
    "dChDTVMubXNnTm9uZSk7CiAgICAgIH0KICAgIH0sMTcwMCk7CiAgfTsKICB1dHJCLm9uY2xpY2s9"
    "KCk9PntpZihsZWZ0PjApdG9hc3QoIlRoYW5rcyDigJQgd2UgYXJlIGNoZWNraW5nIHlvdXIgcGF5"
    "bWVudCIpO307Cn0KZnVuY3Rpb24gc3luY0JhbCgpe3RpY2tUbyhkb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgiYmFsIiksQkFMKTt0aWNrVG8oZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJhbDIiKSxC"
    "QUwpO30KZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigiY2xpY2siLGU9Pntjb25zdCBjPWUudGFy"
    "Z2V0LmNsb3Nlc3QoIltkYXRhLWNvbmZpcm1dIik7aWYoIWMpcmV0dXJuOwogIGNvbnN0IHA9K2Mu"
    "ZGF0YXNldC5jb25maXJtO2lmKEJBTDxwKXt0b2FzdCgiTm90IGVub3VnaCBiYWxhbmNlIOKAlCBh"
    "ZGQgZnVuZHMiKTtyZXR1cm59CiAgQkFMLT1wO3N5bmNCYWwoKTtjbG9zZVNoZWV0KCk7YnVyc3Qo"
    "KTt0b2FzdCgiUHVyY2hhc2VkIOKAlCBPVFAgaXMgaW4gT3JkZXJzIik7fSk7CgovKiDilIDilIAg"
    "QURNSU4g4pSA4pSAICovCmxldCBBVEFCPSJkYXNoIjsKZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiI2FzdWIgYnV0dG9uIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICBkb2N1bWVudC5x"
    "dWVyeVNlbGVjdG9yQWxsKCIjYXN1YiBidXR0b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnJl"
    "bW92ZSgib24iKSk7Yi5jbGFzc0xpc3QuYWRkKCJvbiIpOwogIEFUQUI9Yi5kYXRhc2V0LmE7cmVu"
    "ZGVyQWRtaW4oKTt3aW5kb3cuc2Nyb2xsVG8oMCwwKTt9KTsKZnVuY3Rpb24gcmVuZGVyQWRtaW4o"
    "KXsKICBjb25zdCBzdD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYXN0YWdlIik7CiAgaWYoIWFk"
    "bWluQWxsb3dlZCgpKXtzdC5pbm5lckhUTUw9JzxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJtYXJn"
    "aW46MTZweDt0ZXh0LWFsaWduOmNlbnRlciI+JwogICAgKyc8aDMgc3R5bGU9Im1hcmdpbjowIDAg"
    "NnB4Ij5BZG1pbnMgb25seTwvaDM+JwogICAgKyc8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2lu"
    "OjAiPlRoaXMgcGFuZWwgaXMgbm90IGF2YWlsYWJsZSBvbiB5b3VyIGFjY291bnQuPC9wPjwvZGl2"
    "Pic7cmV0dXJuO30KICBzdC5pbm5lckhUTUw9KHtkYXNoOmFEYXNoLGNoYW5uZWxzOmFDaGFubmVs"
    "cyxsb2dzOmFMb2dzLHBheTphUGF5LGNvbnRlbnQ6YUNvbnRlbnQsZmVhdHVyZXM6YUZlYXR1cmVz"
    "LG1hcmtldDphTWFya2V0LGFuYWx5dGljczphQW5hbHl0aWNzLHNlY3VyaXR5OmFTZWN1cml0eSxz"
    "dG9jazphU3RvY2ssb3JkZXJzOmFPcmRlcnMsdXNlcnM6YVVzZXJzLHNldHRpbmdzOmFTZXR0aW5n"
    "c30pW0FUQUJdKCk7CiAgYmluZEFkbWluKCk7cmV2ZWFsU3RhZ2Uoc3QpO2JpbmRUaWx0KCk7aWYo"
    "d2luZG93Ll9fdnRhYlBsYWNlKXJlcXVlc3RBbmltYXRpb25GcmFtZSh3aW5kb3cuX192dGFiUGxh"
    "Y2UpO30KCmZ1bmN0aW9uIGFEYXNoKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJrcGlzIj4ke1tb"
    "IlJldmVudWUgdG9kYXkiLG1vbmV5KEFETS5rcGkucmV2KV0sWyJPcmRlcnMgdG9kYXkiLGFkbU51"
    "bShBRE0ua3BpLm9yZGVycyldLAogICAgICBbIlVzZXJzIixhZG1OdW0oQURNLmtwaS51c2Vycyld"
    "LFsiQXV0by12ZXJpZmllZCIsYWRtTnVtKEFETS5rcGkuYXV0byldXQogICAgLm1hcCgoW2ssdl0p"
    "PT5gPGRpdiBjbGFzcz0ia3BpIj48c21hbGw+JHtrfTwvc21hbGw+PGI+JHt2fTwvYj48L2Rpdj5g"
    "KS5qb2luKCIiKX08L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3Bs"
    "YXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJw"
    "aWxsIG9uIj48aSBjbGFzcz0iZG90Ij48L2k+UGF5bWVudCBjaGVja2luZzwvc3Bhbj4KICAgICA8"
    "ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIo"
    "LS10eDIpIj4ke0FETS5wYXkuZ21haWxfc2V0PyJHbWFpbCB3YXRjaGVyIG9uIMK3ICIrQURNLnBh"
    "eS5wZW5kaW5nKyIgYXdhaXRpbmcgcmV2aWV3IjoiR21haWwgbm90IHNldCDigJQgZGVwb3NpdHMg"
    "Z28gdG8gbWFudWFsIHJldmlldyJ9PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNv"
    "ZnQiIGRhdGEtYS1nbz0icGF5Ij5PcGVuPC9idXR0b24+PC9kaXY+CiAgIDxkaXYgY2xhc3M9ImNh"
    "cmQiPjxoMz5MYXN0IDcgZGF5czwvaDM+PHAgY2xhc3M9InN1YiI+UmV2ZW51ZSB0cmVuZDwvcD4K"
    "ICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6ZmxleC1lbmQ7Z2FwOjdw"
    "eDtoZWlnaHQ6OTZweDttYXJnaW4tdG9wOjE0cHgiPgogICAgICAgJHtBRE0uYmFycy5tYXAoaD0+"
    "YDxpIHN0eWxlPSJmbGV4OjE7aGVpZ2h0OiR7aH0lO2JvcmRlci1yYWRpdXM6N3B4IDdweCAzcHgg"
    "M3B4O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2Rpc3BsYXk6YmxvY2siPjwvaT5gKS5qb2lu"
    "KCIiKX08L2Rpdj48L2Rpdj4KICAgJHtHKCJkaWdlc3QiLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0"
    "IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAg"
    "PHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5EaWdlc3Q8L3NwYW4+CiAg"
    "ICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6"
    "dmFyKC0tdHgyKSI+TmV4dCBzdW1tYXJ5IGF0IDIzOjMwPC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIHhzIHNvZnQiIGRhdGEtYS1nbz0iYW5hbHl0aWNzIj5WaWV3PC9idXR0b24+PC9kaXY+"
    "YCl9CiAgICR7Rygic2FuZGJveCIsYDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNw"
    "bGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0i"
    "cGlsbCI+U2FuZGJveDwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7"
    "Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj4ke0FETS5zZXR0aW5ncy5zYW5kYm94"
    "PyJUZXN0IG1vZGUgaXMgT04iOiJUZXN0IG1vZGUgaXMgb2ZmIOKAlCByZWFsIG1vbmV5IGlzIGxp"
    "dmUifTwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuc2FuZGJveCk/"
    "IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9InNhbmRib3giPjwvZGl2PjwvZGl2PmApfQogICA8ZGl2"
    "IGNsYXNzPSJzZWMiPjxoMj5RdWljayBBY3Rpb25zPC9oMj48L2Rpdj4KICAgPGRpdiBjbGFzcz0i"
    "Z3JpZCI+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtcWE9ImJyb2FkY2FzdCI+"
    "QnJvYWRjYXN0PC9idXR0b24+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtcWE9"
    "InN0b2NrIj5BZGQgU3RvY2s8L2J1dHRvbj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIg"
    "ZGF0YS1xYT0iZmxhc2giPkZsYXNoIFNhbGU8L2J1dHRvbj4KICAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4gc29mdCIgZGF0YS1xYT0iYmFja3VwIj5CYWNrdXAgREI8L2J1dHRvbj48L2Rpdj5gOwp9Cgpm"
    "dW5jdGlvbiBhQ2hhbm5lbHMoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Gb3Jj"
    "ZS1qb2luIGNoYW5uZWxzPC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPkFkZCBvciByZW1vdmUgYW55"
    "IG51bWJlciBvZiBjaGFubmVscy4gU2F2ZWQgdG8gdGhlIGRhdGFiYXNlIOKAlCBpbnN0YW50LCBu"
    "byByZXN0YXJ0LjwvcD4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "M3B4Ij48bGFiZWw+Q2hhbm5lbCBsaW5rLCBAdXNlcm5hbWUgb3IgLTEwMCBJRDwvbGFiZWw+CiAg"
    "ICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0iY2gtaW4iIHBsYWNlaG9sZGVyPSJAY2hhbm5lbCDC"
    "tyB0Lm1lLytpbnZpdGUgwrcgLTEwMOKApiI+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+"
    "PGxhYmVsPlR5cGU8L2xhYmVsPgogICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIGlkPSJjaC10eXBl"
    "Ij48b3B0aW9uIHZhbHVlPSIxIj5SZXF1aXJlZCDigJQgbXVzdCBqb2luPC9vcHRpb24+PG9wdGlv"
    "biB2YWx1ZT0iMCI+T3B0aW9uYWwg4oCUIGp1c3Qgc3VnZ2VzdGVkPC9vcHRpb24+PC9zZWxlY3Q+"
    "PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciPjxidXR0b24gY2xhc3M9ImJ0biIgaWQ9ImNo"
    "LWFkZCI+JHtpYygicGx1cyIpfSBBZGQgQ2hhbm5lbDwvYnV0dG9uPgogICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIGdob3N0IiBkYXRhLW1pc2M9ImNoYW5uZWx0ZXN0Ij5UZXN0IEJvdDwvYnV0dG9uPjwv"
    "ZGl2PjwvZGl2PgogIDxkaXYgY2xhc3M9InNlYyI+PGgyPkFjdGl2ZSAoJHtDSEFOTkVMUy5sZW5n"
    "dGh9KTwvaDI+PC9kaXY+CiAgJHtDSEFOTkVMUy5tYXAoKGMsaSk9PmA8ZGl2IGNsYXNzPSJjYXJk"
    "IHRpZ2h0Ij4KICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVy"
    "O2dhcDoxMHB4Ij4KICAgICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoInNlbmQiKX08L2Rpdj4K"
    "ICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6"
    "ZTouODNyZW07ZGlzcGxheTpibG9jazt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVu"
    "O3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXMiPiR7ZXNjKGMubmFtZSl9PC9iPgogICAgICAgICA8c21h"
    "bGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+JHtlc2MoYy5oYW5k"
    "bGUpfSDCtyAke2MubWVtYmVyc30gbWVtYmVyczwvc21hbGw+PC9kaXY+CiAgICAgICA8ZGl2IGNs"
    "YXNzPSJ0ZyBzbSAke2Mub24/Im9uIjoiIn0iIGRhdGEtY2hvbj0iJHtjLmlkfSI+PC9kaXY+PC9k"
    "aXY+CiAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo2cHg7bWFyZ2luLXRvcDoxMHB4"
    "O2ZsZXgtd3JhcDp3cmFwIj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyAke2MucmVxPyIi"
    "OiJzb2Z0In0iIGRhdGEtY2hyZXE9IiR7Yy5pZH0iPiR7Yy5yZXE/IlJlcXVpcmVkIjoiT3B0aW9u"
    "YWwifTwvYnV0dG9uPgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtY29w"
    "eT0idGV4dCIgZGF0YS1jb3B5LXZhbHVlPSIke2VzYyhjLmhhbmRsZSl9Ij5Db3B5PC9idXR0b24+"
    "CiAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgYmFkIiBzdHlsZT0ibWFyZ2luLWxlZnQ6YXV0"
    "byIgZGF0YS1jaGRlbD0iJHtjLmlkfSI+UmVtb3ZlPC9idXR0b24+PC9kaXY+PC9kaXY+YCkuam9p"
    "bigiIikKICAgfHwnPGRpdiBjbGFzcz0iY2FyZCI+PHAgY2xhc3M9InN1YiI+Tm8gY2hhbm5lbHMg"
    "4oCUIHVzZXJzIGNhbiBlbnRlciBmcmVlbHkuPC9wPjwvZGl2Pid9YDsKfQoKZnVuY3Rpb24gYUxv"
    "Z3MoKXsKICBjb25zdCBvcHRzPWlkPT5MT0dDSC5tYXAobD0+YDxvcHRpb24gdmFsdWU9IiR7bC5p"
    "ZH0iICR7bC5pZD09PWlkPyJzZWxlY3RlZCI6IiJ9PiR7ZXNjKGwubmFtZSl9PC9vcHRpb24+YCku"
    "am9pbigiIik7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBz"
    "dHlsZT0icGFkZGluZy10b3A6MCI+CiAgICAgIDxkaXYgY2xhc3M9InQiPkxvZ2dpbmcgbWFzdGVy"
    "IHN3aXRjaDxzbWFsbD5PZmYgPSB0aGUgYm90IHBvc3RzIG5vIGxvZ3MgYW55d2hlcmU8L3NtYWxs"
    "PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyAke0xPR1NfTUFTVEVSPyJvbiI6IiJ9IiBkYXRh"
    "LW1hc3Rlcj48L2Rpdj48L2Rpdj48L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxvZyBj"
    "aGFubmVsczwvaDM+PHAgY2xhc3M9InN1YiI+QWRkIGFzIG1hbnkgYXMgeW91IGxpa2UuIFRoZSBz"
    "dGFycmVkIG9uZSBpcyB0aGUgcHJlZmVycmVkL2RlZmF1bHQgY2hhbm5lbC48L3A+CiAgICAgPGRp"
    "diBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5DaGFubmVsIGxp"
    "bmssIEB1c2VybmFtZSBvciAtMTAwIElEPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5w"
    "IiBpZD0ibGctaW4iIHBsYWNlaG9sZGVyPSJAbG9nc19jaGFubmVsIMK3IC0xMDDigKYiPjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0ibGctYWRk"
    "Ij4ke2ljKCJwbHVzIil9IEFkZCBMb2cgQ2hhbm5lbDwvYnV0dG9uPgogICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBzb2Z0IiBkYXRhLW1pc2M9ImxvZ3Rlc3QiPlNlbmQgVGVzdDwvYnV0dG9uPjwvZGl2"
    "PjwvZGl2PgogICAke0xPR0NILm1hcChsPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9"
    "ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICA8ZGl2IGNs"
    "YXNzPSJzdGFyICR7bC5wcmltYXJ5PyJvbiI6IiJ9IiBkYXRhLWxnc3Rhcj0iJHtsLmlkfSI+JHtp"
    "Yygic3RhciIpfTwvZGl2PgogICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxi"
    "IHN0eWxlPSJmb250LXNpemU6LjgzcmVtO2Rpc3BsYXk6YmxvY2s7d2hpdGUtc3BhY2U6bm93cmFw"
    "O292ZXJmbG93OmhpZGRlbjt0ZXh0LW92ZXJmbG93OmVsbGlwc2lzIj4ke2VzYyhsLm5hbWUpfTwv"
    "Yj4KICAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJl"
    "bSI+JHtlc2MobC5oYW5kbGUpfSR7bC5wcmltYXJ5PyIgwrcgcHJlZmVycmVkIjoiIn08L3NtYWxs"
    "PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0ZyBzbSAke2wub24/Im9uIjoiIn0iIGRhdGEtbGdv"
    "bj0iJHtsLmlkfSI+PC9kaXY+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBiYWQiIGRhdGEt"
    "bGdkZWw9IiR7bC5pZH0iPlJlbW92ZTwvYnV0dG9uPjwvZGl2PmApLmpvaW4oIiIpCiAgICB8fCc8"
    "ZGl2IGNsYXNzPSJjYXJkIj48cCBjbGFzcz0ic3ViIj5ObyBsb2cgY2hhbm5lbHMgeWV0LjwvcD48"
    "L2Rpdj4nfQogICA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5FdmVudHMgJmFtcDsgcm91dGluZzwvaDI+"
    "PGEgZGF0YS1ldi1hbGw+VG9nZ2xlIGFsbDwvYT48L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+"
    "JHtFVkVOVFMubWFwKChlLGkpPT5gCiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0i"
    "dCIgc3R5bGU9ImZsZXg6MSI+JHtlc2MoZS5rKX0KICAgICAgIDxzbWFsbD48c2VsZWN0IGNsYXNz"
    "PSJpbnAiIHN0eWxlPSJoZWlnaHQ6MzBweDtmb250LXNpemU6LjdyZW07bWFyZ2luLXRvcDo1cHg7"
    "bWF4LXdpZHRoOjE5MHB4IiBkYXRhLWV2Y2g9IiR7aX0iICR7ZS5vbj8iIjoiZGlzYWJsZWQifT4k"
    "e29wdHMoZS5jaCl9PC9zZWxlY3Q+PC9zbWFsbD48L2Rpdj4KICAgICAgIDxkaXYgY2xhc3M9InRn"
    "ICR7ZS5vbj8ib24iOiIifSIgZGF0YS1ldm9uPSIke2l9Ij48L2Rpdj48L2Rpdj5gKS5qb2luKCIi"
    "KX08L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlJlY2VudCBsb2cgb3V0cHV0PC9oMz4K"
    "ICAgICAkeygoQURNLmxvZ2ZlZWQmJkFETS5sb2dmZWVkLmxlbmd0aCk/QURNLmxvZ2ZlZWQubWFw"
    "KGw9PltsLndoYXQsbC53aGVyZSxsLndoZW5dKTpbXSkKICAgICAgIC5tYXAoKFt0LGMsd10pPT5g"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7dH08L3NwYW4+PGIgc3R5bGU9ImZvbnQtc2l6ZTouN3Jl"
    "bSI+JHtjfSDCtyAke3d9PC9iPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PmA7Cn0KCmZ1bmN0aW9u"
    "IGFQYXkoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0"
    "eWxlPSJwYWRkaW5nLXRvcDowIj4KICAgICAgPGRpdiBjbGFzcz0idCI+R21haWwgYXV0by12ZXJp"
    "Znk8c21hbGw+JHtBRE0ucGF5LmdtYWlsX3NldD8iUmVhZGluZyAiK2VzYyhBRE0ucGF5LmdtYWls"
    "KToiTm90IGNvbmZpZ3VyZWQg4oCUIGRlcG9zaXRzIHF1ZXVlIGZvciByZXZpZXcifTwvc21hbGw+"
    "PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuZ21haWxfZW5hYmxl"
    "ZCk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImdtYWlsX2VuYWJsZWQiPjwvZGl2PjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPkF1dG8tYXBwcm92ZSBzbWFsbCBk"
    "ZXBvc2l0czxzbWFsbD5Ta2lwIHJldmlldyBiZWxvdyB0aGUgdGhyZXNob2xkPC9zbWFsbD48L2Rp"
    "dj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmdtYWlsX2VuYWJsZWQpPyIg"
    "b24iOiIifSIgZGF0YS1zZXR0aW5nPSJnbWFpbF9lbmFibGVkIj48L2Rpdj48L2Rpdj4KICAgICA8"
    "ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5NYW51YWwgVVRSIGZhbGxiYWNrPHNtYWxs"
    "PkxldCB1c2VycyBzdWJtaXQgYSBVVFIgaWYgYXV0by12ZXJpZnkgbWlzc2VzPC9zbWFsbD48L2Rp"
    "dj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmF1dG9kZXApPyIgb24iOiIi"
    "fSIgZGF0YS1zZXR0aW5nPSJhdXRvZGVwIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJz"
    "d2FwIj48ZGl2IGNsYXNzPSJ0Ij5WZXJpZnkgb25seSB3aGVuIHVzZXIgdGFwcyBWZXJpZnk8c21h"
    "bGw+Tm8gYmFja2dyb3VuZCBjaGVja2luZyDigJQgc2F2ZXMgR21haWwgcXVvdGE8L3NtYWxsPjwv"
    "ZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cudXRyX2ZhbGxiYWNrKT8i"
    "IG9uIjoiIn0iIGRhdGEtc2V0dGluZz0idXRyX2ZhbGxiYWNrIj48L2Rpdj48L2Rpdj48L2Rpdj4K"
    "ICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlVQSTwvaDM+PHAgY2xhc3M9InN1YiI+JHtBRE0ucGF5"
    "LnVwaT8iSW4gdXNlIGJ5IGV2ZXJ5IHBheW1lbnQgcmVxdWVzdC4iOiJOb3Qgc2V0IHlldCDigJQg"
    "dG9wLXVwcyBhcmUgZGlzYWJsZWQgdW50aWwgeW91IGFkZCBpdC4ifTwvcD4KICAgICA8ZGl2IGNs"
    "YXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlVQSSBJRDwvbGFiZWw+"
    "CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9InVwaV9pZCIgdmFsdWU9IiR7ZXNj"
    "KEFETS5wYXkudXBpfHwiIil9IiBwbGFjZWhvbGRlcj0ieW91cmhhbmRsZUBiYW5rIj48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlBheWVlIG5hbWU8L2xhYmVsPgogICAgICAg"
    "PGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJ1cGlfbmFtZSIgdmFsdWU9IiR7ZXNjKEFETS5w"
    "YXkucGF5ZWV8fCIiKX0iIHBsYWNlaG9sZGVyPSJZb3VyIFN0b3JlIj48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJmaWVsZCI+PGxhYmVsPk5vdGUgcHJlZml4PC9sYWJlbD4KICAgICAgIDxpbnB1dCBj"
    "bGFzcz0iaW5wIiBkYXRhLXNldD0icGF5X3ByZWZpeCIgdmFsdWU9IiR7ZXNjKEFETS5wYXkucHJl"
    "Zml4fHwiUEFZIil9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlBheW1l"
    "bnQgd2luZG93IChtaW51dGVzKTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0"
    "YS1zZXQ9InBheV93aW5kb3ciIHZhbHVlPSIke0FETS5wYXkud2luZG93fSI+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5NaW5pbXVtIGRlcG9zaXQ8L2xhYmVsPgogICAgICAg"
    "PGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJtaW5fZGVwb3NpdCIgdmFsdWU9IiR7QURNLnBh"
    "eS5taW59Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPk1heGltdW0gZGVw"
    "b3NpdDwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9Im1heF9kZXBv"
    "c2l0IiB2YWx1ZT0iJHtBRE0ucGF5Lm1heH0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxk"
    "Ij48bGFiZWw+QXV0by1hcHByb3ZlIHVuZGVyICgwID0gb2ZmKTwvbGFiZWw+CiAgICAgICA8aW5w"
    "dXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9ImF1dG9kZXBfbWF4IiB2YWx1ZT0iJHtBRE0ucGF5LmF1"
    "dG9kZXB9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkdtYWlsIGFkZHJl"
    "c3M8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJnbWFpbF9pZCIg"
    "dmFsdWU9IiR7ZXNjKEFETS5wYXkuZ21haWx8fCIiKX0iIHBsYWNlaG9sZGVyPSJzdG9yZUBnbWFp"
    "bC5jb20iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+R21haWwgYXBwIHBh"
    "c3N3b3JkPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0iZ21haWxf"
    "cGFzcyIgdHlwZT0icGFzc3dvcmQiIHBsYWNlaG9sZGVyPSIke0FETS5wYXkuZ21haWxfc2V0PyJz"
    "YXZlZCDigJQgdHlwZSB0byByZXBsYWNlIjoibm90IHNldCJ9Ij48L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJrdiI+PHNwYW4+V2FpdGluZyBmb3IgcmV2aWV3PC9zcGFuPjxiPiR7QURNLnBheS5wZW5k"
    "aW5nfTwvYj48L2Rpdj48L2Rpdj4KICAgJHtHKCJjcnlwdG8iLGA8ZGl2IGNsYXNzPSJjYXJkIj48"
    "aDM+Q3J5cHRvIHdhbGxldDwvaDM+PHAgY2xhc3M9InN1YiI+VVNEVC1UUkMyMCBkZXBvc2l0cywg"
    "YXV0by1jb25maXJtZWQgb24tY2hhaW4uPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMHB4Ij48bGFiZWw+V2FsbGV0IGFkZHJlc3M8L2xhYmVsPjxpbnB1dCBj"
    "bGFzcz0iaW5wIiBwbGFjZWhvbGRlcj0iVOKApiI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmll"
    "bGQiPjxsYWJlbD5Db25maXJtYXRpb25zIHJlcXVpcmVkPC9sYWJlbD48aW5wdXQgY2xhc3M9Imlu"
    "cCIgdmFsdWU9IjEiPjwvZGl2PgogICAgIDwvZGl2PmApfQogICAke0coInVwaVJvdGF0ZSIsYDxk"
    "aXYgY2xhc3M9ImNhcmQiPjxoMz5VUEkgcm90YXRpb248L2gzPjxwIGNsYXNzPSJzdWIiPlNwcmVh"
    "ZCBsb2FkIGFuZCBzdXJ2aXZlIGEgZnJvemVuIGhhbmRsZS48L3A+CiAgICAgJHtbWyJoYW5kbGUx"
    "QGZhbSIsIkFjdGl2ZSJdLFsiaGFuZGxlMkBmYW0iLCJBY3RpdmUiXSxbImhhbmRsZTNAdXBpIiwi"
    "UGF1c2VkIl1dCiAgICAgICAubWFwKChbaCxzdF0pPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7"
    "aH08L3NwYW4+PGI+PHNwYW4gY2xhc3M9InBpbGwgJHtzdD09PSJBY3RpdmUiPyJvbiI6Im9mZiJ9"
    "Ij4ke3N0fTwvc3Bhbj48L2I+PC9kaXY+YCkuam9pbigiIil9CiAgICAgPGRpdiBjbGFzcz0iZmll"
    "bGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6"
    "N3B4Ij4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBwbGFjZWhvbGRlcj0iYW5vdGhlckB1cGki"
    "PjxidXR0b24gY2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJ1cGlhZGQi"
    "PkFkZDwvYnV0dG9uPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImRlcEJvbnVzIixgPGRp"
    "diBjbGFzcz0iY2FyZCI+PGgzPkRlcG9zaXQgYm9udXM8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZp"
    "ZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD7igrk1MDAgYW5kIGFib3ZlICglKTwv"
    "bGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNz"
    "PSJmaWVsZCI+PGxhYmVsPuKCuTEwMDAgYW5kIGFib3ZlICglKTwvbGFiZWw+PGlucHV0IGNsYXNz"
    "PSJpbnAiIHZhbHVlPSI4Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPuKC"
    "uTIwMDAgYW5kIGFib3ZlICglKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxMiI+"
    "PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiZGVwTGltaXRzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgz"
    "PkRlcG9zaXQgbGltaXRzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdp"
    "bi10b3A6OHB4Ij48bGFiZWw+TWluaW11bSAo4oK5KTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAi"
    "IHZhbHVlPSIxMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5NYXhpbXVt"
    "ICjigrkpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjIwMDAwIj48L2Rpdj48L2Rp"
    "dj5gKX0KICAgJHtHKCJ3cm9uZ0FtdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3"
    "YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5Xcm9uZy1hbW91bnQgaGFu"
    "ZGxpbmc8c21hbGw+Q3JlZGl0IHdoYXQgd2FzIGFjdHVhbGx5IHBhaWQgYW5kIGZsYWcgaXQ8L3Nt"
    "YWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cudmVyaWZ5X29u"
    "X3RhcCk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9InZlcmlmeV9vbl90YXAiPjwvZGl2PjwvZGl2"
    "PjwvZGl2PmApfQogICAke0coInNwbGl0UGF5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFz"
    "cz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPlNwbGl0IHBheW1l"
    "bnQ8c21hbGw+V2FsbGV0IGJhbGFuY2UgcGx1cyBVUEkgaW4gb25lIGNoZWNrb3V0PC9zbWFsbD48"
    "L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC53cm9uZ0FtdD8iIG9uIjoiIn0iIGRhdGEt"
    "ZmVhdD0id3JvbmdBbXQiPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImF1dG9SZWZ1bmQi"
    "LGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QXV0by1yZWZ1bmQ8L2gzPgogICAgIDxkaXYgY2xhc3M9"
    "ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5XYXJyYW50eSB3aW5kb3cgKG1p"
    "bnV0ZXMpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwIj48L2Rpdj4KICAgICA8"
    "ZGl2IGNsYXNzPSJrdiI+PHNwYW4+UmVmdW5kZWQgdG9kYXk8L3NwYW4+PGI+4oK5MTA1LjAwIMK3"
    "IDMgb3JkZXJzPC9iPjwvZGl2PjwvZGl2PmApfQogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+R21h"
    "aWwgYWNjb3VudDwvaDM+PHAgY2xhc3M9InN1YiI+VGhlIEdtYWlsIHRoYXQgcmVjZWl2ZXMgeW91"
    "ciBGYW1QYXkgcGF5bWVudCBtYWlscy4gTm90aGluZyBpcyBzdG9yZWQgaW4gY29kZS48L3A+CiAg"
    "ICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5HbWFp"
    "bCBJRDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHBsYWNlaG9sZGVyPSJ5b3Vyc3RvcmVAZ21h"
    "aWwuY29tIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkdtYWlsIHBhc3N3"
    "b3JkPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdHlwZT0icGFzc3dvcmQiIHBsYWNlaG9sZGVy"
    "PSLigKLigKLigKLigKLigKLigKLigKLigKLigKLigKLigKLigKIiPjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBkYXRhLWFkbWluLWFjdD0iZ21haWx0"
    "ZXN0Ij5UZXN0IEdtYWlsPC9idXR0b24+CiAgICAgPC9kaXY+CiAgICAgPHAgY2xhc3M9InN1YiIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTBweCI+VGlwOiBpZiAyLXN0ZXAgdmVyaWZpY2F0aW9uIGlzIG9u"
    "LCBwYXN0ZSBhIEdvb2dsZSBBcHAgUGFzc3dvcmQgaGVyZSBpbnN0ZWFkIG9mIHlvdXIgbm9ybWFs"
    "IG9uZS48L3A+PC9kaXY+CiAgIDxkaXYgY2xhc3M9InNlYyI+PGgyPlJlY2VudCBhdXRvLXZlcmlm"
    "aWNhdGlvbnM8L2gyPjwvZGl2PgogICAkeyhBRE0uZGVwb3NpdHN8fFtdKS5sZW5ndGg/QURNLmRl"
    "cG9zaXRzLm1hcChkPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6Zmxl"
    "eDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+"
    "JHtpYygid2FsbGV0Iil9PC9kaXY+CiAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRo"
    "OjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjgycmVtIj4ke21vbmV5KGQuYW10KX0gwrcgJHtlc2Mo"
    "ZC51c2VyKX08L2I+PGJyPgogICAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7"
    "Zm9udC1zaXplOi42OXJlbSI+JHtlc2MoZC51dHJ8fCJubyByZWZlcmVuY2UgeWV0Iil9PC9zbWFs"
    "bD48L2Rpdj4KICAgICAgIDxzcGFuIGNsYXNzPSJwaWxsICR7ZC5zdD09PSJDb21wbGV0ZWQiPyJv"
    "biI6ImhvdCJ9Ij4ke2VzYyhkLnN0KX08L3NwYW4+CiAgICAgICAke2Quc3Q9PT0iQ29tcGxldGVk"
    "Ij8iIjpgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBkYXRhLWRlcC1vaz0iJHtkLmlkfSI+QXBwcm92"
    "ZTwvYnV0dG9uPmB9PC9kaXY+YCkuam9pbigiIikKICAgICA6JzxkaXYgY2xhc3M9ImNhcmQgdGln"
    "aHQiPjxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gZGVwb3NpdHMgeWV0LjwvcD48"
    "L2Rpdj4nfWA7Cn0KCmZ1bmN0aW9uIGFDb250ZW50KCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJj"
    "YXJkIj48aDM+RWRpdCBhcHAgdGV4dDwvaDM+CiAgICAgPHAgY2xhc3M9InN1YiI+RXZlcnkgd29y"
    "ZCB1c2VycyBzZWUg4oCUIGNoYW5nZSBpdCBoZXJlIGFuZCB0aGUgc3RvcmUgdXBkYXRlcyBpbnN0"
    "YW50bHkuPC9wPgogICAgIDxkaXYgY2xhc3M9Im1zZWFyY2giIHN0eWxlPSJtYXJnaW4tdG9wOjEy"
    "cHgiPjxkaXYgY2xhc3M9ImZpZWxkd3JhcCI+JHtpYygic2VhcmNoIil9PGlucHV0IGlkPSJjcSIg"
    "cGxhY2Vob2xkZXI9IkZpbmQgdGV4dCB0byBlZGl04oCmIj48L2Rpdj4KICAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBzbSBzb2Z0IiBpZD0iY21zcmVzZXQiPlJlc2V0PC9idXR0b24+PC9kaXY+CiAg"
    "ICAgPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+VXNlICZsdDticiZndDsg"
    "Zm9yIGEgbGluZSBicmVhaywgJmx0O2ImZ3Q7d29yZCZsdDsvYiZndDsgZm9yIGJvbGQuIHtufSA9"
    "IGxpdmUgY291bnRyeSBjb3VudCwge219ID0gcGF5bWVudCBtaW51dGVzLjwvcD48L2Rpdj4KICAg"
    "JHtDTVNNRVRBLm1hcCgoW2dycCxyb3dzXSk9PmA8ZGl2IGNsYXNzPSJjYXJkIGNtc2dycCI+PGgz"
    "PiR7Z3JwfTwvaDM+CiAgICAgJHtyb3dzLm1hcCgoW2ssbGJsXSk9PmA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCBjbXNyb3ciIGRhdGEtZj0iJHtlc2MoKGxibCsiICIrQ01TW2tdKyIgIitncnApLnRvTG93ZXJD"
    "YXNlKCkpfSI+CiAgICAgICAgPGxhYmVsPiR7bGJsfTwvbGFiZWw+CiAgICAgICAgPHRleHRhcmVh"
    "IGNsYXNzPSJpbnAiIHJvd3M9IiR7Q01TW2tdLmxlbmd0aD40MD8yOjF9IiBkYXRhLWNtcz0iJHtr"
    "fSI+JHtlc2MoQ01TW2tdKX08L3RleHRhcmVhPjwvZGl2PmApLmpvaW4oIiIpfQogICA8L2Rpdj5g"
    "KS5qb2luKCIiKX0KICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6Zmxl"
    "eDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9u"
    "Ij48aSBjbGFzcz0iZG90Ij48L2k+TGl2ZSBlZGl0PC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZs"
    "ZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPkNoYW5n"
    "ZXMgYXBwbHkgdG8gdGhlIFN0b3JlIHRhYiBzdHJhaWdodCBhd2F5PC9kaXY+CiAgICAgPC9kaXY+"
    "YDsKfQoKZnVuY3Rpb24gYUZlYXR1cmVzKCl7CiAgY29uc3Qgb249T2JqZWN0LnZhbHVlcyhGRUFU"
    "KS5maWx0ZXIoQm9vbGVhbikubGVuZ3RoLHRvdD1PYmplY3Qua2V5cyhGRUFUKS5sZW5ndGg7CiAg"
    "cmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+RmVhdHVyZSBzd2l0Y2hlczwvaDM+CiAgICAg"
    "PHAgY2xhc3M9InN1YiI+VHVybiBhbnl0aGluZyBvZmYgYW5kIGl0IGRpc2FwcGVhcnMgZnJvbSB0"
    "aGUgYXBwIGNvbXBsZXRlbHkg4oCUIG5vIGJ1dHRvbnMsIG5vIG1lbnVzLCBubyBsZWZ0b3ZlcnMu"
    "PC9wPgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5FbmFibGVkPC9zcGFuPjxiIGlkPSJmY291"
    "bnQiPiR7b259IG9mICR7dG90fTwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJtc2VhcmNoIiBz"
    "dHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48ZGl2IGNsYXNzPSJmaWVsZHdyYXAiPiR7aWMoInNlYXJj"
    "aCIpfTxpbnB1dCBpZD0iZnEiIHBsYWNlaG9sZGVyPSJTZWFyY2ggZmVhdHVyZXPigKYiPjwvZGl2"
    "PgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIHNvZnQiIGlkPSJmYWxsIj5BbGwgb248L2J1"
    "dHRvbj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjhw"
    "eCI+PGJ1dHRvbiBjbGFzcz0iYnRuIHNtIGdob3N0IiBpZD0iZm5vbmUiPkFsbCBvZmY8L2J1dHRv"
    "bj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBpZD0iZnJlc2V0Ij5SZXNldDwv"
    "YnV0dG9uPjwvZGl2PjwvZGl2PgogICAke0ZHUk9VUFMubWFwKChbZ3JwLHJvd3NdKT0+YDxkaXYg"
    "Y2xhc3M9ImNhcmQgZmdycCI+PGgzPiR7Z3JwfTwvaDM+CiAgICAgJHtyb3dzLm1hcCgoW2ssbCxk"
    "XSk9PmA8ZGl2IGNsYXNzPSJmY2FyZCBmcm93IiBkYXRhLWY9IiR7ZXNjKChsKyIgIitkKyIgIitn"
    "cnApLnRvTG93ZXJDYXNlKCkpfSI+CiAgICAgICAgPGRpdiBjbGFzcz0idCI+PGI+JHtsfTwvYj48"
    "c21hbGw+JHtkfTwvc21hbGw+PC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0idGcgJHtGRUFUW2td"
    "PyJvbiI6IiJ9IiBkYXRhLWZlYXQ9IiR7a30iPjwvZGl2PjwvZGl2PmApLmpvaW4oIiIpfQogICA8"
    "L2Rpdj5gKS5qb2luKCIiKX0KICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3Bs"
    "YXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJw"
    "aWxsIG9uIj48aSBjbGFzcz0iZG90Ij48L2k+TGl2ZTwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJm"
    "bGV4OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj5Td2l0"
    "Y2hlcyBhcHBseSB0byB0aGUgU3RvcmUgdGFiIGluc3RhbnRseTwvZGl2PgogICAgIDwvZGl2PmA7"
    "Cn0KCmZ1bmN0aW9uIGFNYXJrZXQoKXsKICByZXR1cm4gYCR7RygiY291cG9ucyIsYDxkaXYgY2xh"
    "c3M9ImNhcmQiPjxoMz5Db3Vwb25zPC9oMz48cCBjbGFzcz0ic3ViIj5QZXJjZW50YWdlIG9mZiBh"
    "dCBjaGVja291dC48L3A+CiAgICAgJHsoQURNLmNvdXBvbnMmJkFETS5jb3Vwb25zLmxlbmd0aCk/"
    "QURNLmNvdXBvbnMubWFwKGM9PmA8ZGl2IGNsYXNzPSJrdiI+CiAgICAgICA8c3Bhbj48YiBzdHls"
    "ZT0iY29sb3I6dmFyKC0tdHgpIj4ke2VzYyhjLmNvZGUpfTwvYj4gwrcgJHtjLnBlcmNlbnR9JSDC"
    "tyAke2MudXNlZH0vJHtjLm1heHx8IuKIniJ9PC9zcGFuPgogICAgICAgPGI+PHNwYW4gY2xhc3M9"
    "InBpbGwgJHtjLmFjdGl2ZT8ib24iOiJvZmYifSI+JHtjLmFjdGl2ZT8iQWN0aXZlIjoiT2ZmIn08"
    "L3NwYW4+PC9iPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxl"
    "PSJtYXJnaW46MCI+Tm8gY291cG9ucyB5ZXQuPC9wPid9CiAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5OZXcgY29kZTwvbGFiZWw+CiAgICAgICA8"
    "ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjdweCI+PGlucHV0IGNsYXNzPSJpbnAiIGlkPSJj"
    "cGNvZGUiIHBsYWNlaG9sZGVyPSJORVdZRUFSIj4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBp"
    "ZD0iY3BwY3QiIHBsYWNlaG9sZGVyPSIlIiBzdHlsZT0id2lkdGg6NjZweCI+CiAgICAgICA8YnV0"
    "dG9uIGNsYXNzPSJidG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0iY3BhZGQiPkFkZDwv"
    "YnV0dG9uPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImZsYXNoU2FsZSIsYDxkaXYgY2xh"
    "c3M9ImNhcmQiPjxoMz5GbGFzaCBzYWxlIHNjaGVkdWxlcjwvaDM+PHAgY2xhc3M9InN1YiI+U3Rh"
    "cnRzLCBhbm5vdW5jZXMgYW5kIGVuZHMgYnkgaXRzZWxmLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJm"
    "aWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPkRpc2NvdW50ICglKTwvbGFiZWw+"
    "PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIyMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmll"
    "bGQiPjxsYWJlbD5TdGFydHM8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iVG9kYXkg"
    "MTg6MDAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RW5kczwvbGFiZWw+"
    "PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSJUb2RheSAyMzowMCI+PC9kaXY+CiAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIGJsb2NrIiBpZD0iZmxhc2hnbyI+U3RhcnQgRmxhc2ggU2FsZTwvYnV0dG9u"
    "PjwvZGl2PmApfQogICAke0coInNjaGVkQnJvYWRjYXN0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgz"
    "PlNjaGVkdWxlZCBicm9hZGNhc3Q8L2gzPjxwIGNsYXNzPSJzdWIiPlF1ZXVlIGl0IGFuZCB0YXJn"
    "ZXQgYSBzZWdtZW50LjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10"
    "b3A6MTJweCI+PGxhYmVsPlNlZ21lbnQ8L2xhYmVsPgogICAgICAgPHNlbGVjdCBjbGFzcz0iaW5w"
    "Ij48b3B0aW9uPkV2ZXJ5b25lPC9vcHRpb24+PG9wdGlvbj5CdXllcnMgb25seTwvb3B0aW9uPjxv"
    "cHRpb24+TmV2ZXIgYm91Z2h0PC9vcHRpb24+PG9wdGlvbj5CaWcgc3BlbmRlcnMgKOKCuTEwMDAr"
    "KTwvb3B0aW9uPjxvcHRpb24+SW5hY3RpdmUgMzAgZGF5czwvb3B0aW9uPjwvc2VsZWN0PjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+U2VuZCBhdDwvbGFiZWw+PGlucHV0IGNs"
    "YXNzPSJpbnAiIHZhbHVlPSJUb21vcnJvdyAxMDowMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "ZmllbGQiPjxsYWJlbD5NZXNzYWdlPC9sYWJlbD48dGV4dGFyZWEgY2xhc3M9ImlucCIgcm93cz0i"
    "MiIgcGxhY2Vob2xkZXI9IldyaXRlIHlvdXIgYnJvYWRjYXN04oCmIj48L3RleHRhcmVhPjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0iYmNnbyI+"
    "U2VuZCBCcm9hZGNhc3Q8L2J1dHRvbj4KICAgICAgIDwvZGl2PjwvZGl2PmApfQogICAke0coIndp"
    "bmJhY2siLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGlu"
    "Zy10b3A6MCI+PGRpdiBjbGFzcz0idCI+V2luLWJhY2sgbWVzc2FnZXM8c21hbGw+QXV0by1ETSB1"
    "c2VycyBpbmFjdGl2ZSBmb3IgNyBhbmQgMzAgZGF5czwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBj"
    "bGFzcz0idGcke0ZFQVQuc3BsaXRQYXk/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InNwbGl0UGF5Ij48"
    "L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkRpc2NvdW50IG9mZmVy"
    "ZWQgKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwIj48L2Rpdj48L2Rpdj5g"
    "KX0KICAgJHtHKCJhYmFuZG9uUmVtaW5kIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9"
    "ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNs"
    "YXNzPSJwaWxsIG9uIj48aSBjbGFzcz0iZG90Ij48L2k+UmVtaW5kZXJzPC9zcGFuPgogICAgIDxk"
    "aXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigt"
    "LXR4MikiPjQgdW5maW5pc2hlZCBwYXltZW50cyBudWRnZWQgdG9kYXk8L2Rpdj4KICAgICA8L2Rp"
    "dj5gKX0KICAgJHtHKCJsb3lhbHR5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxveWFsdHkgcG9p"
    "bnRzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48"
    "bGFiZWw+UG9pbnRzIHBlciDigrkxMCBzcGVudDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZh"
    "bHVlPSIxIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlBvaW50cyBuZWVk"
    "ZWQgZm9yIOKCuTEwPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEwMCI+PC9kaXY+"
    "CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9Indp"
    "bmJhY2tzYXZlIj5TYXZlPC9idXR0b24+PC9kaXY+YCl9CiAgICR7YW55RigicmVmRGFzaCIsInJl"
    "ZkwyIik/YDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5SZWZlcnJhbCBwYXlvdXRzPC9oMz4KICAgICA8"
    "ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+TGV2ZWwgMSBj"
    "b21taXNzaW9uICglKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj4K"
    "ICAgICAke0coInJlZkwyIixgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5MZXZlbCAyIGNvbW1p"
    "c3Npb24gKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEiPjwvZGl2PmApfQog"
    "ICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5QYWlkIG91dCB0aGlzIG1vbnRoPC9zcGFuPjxiPuKC"
    "uTMsMjQwPC9iPjwvZGl2PjwvZGl2PmA6IiJ9CiAgICR7Rygic2FsZXNGZWVkIixgPGRpdiBjbGFz"
    "cz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xh"
    "c3M9InQiPkF1dG8tcG9zdCBzYWxlcyB0byBjaGFubmVsPHNtYWxsPiR7RkVFRC5sZW5ndGg/ZXNj"
    "KEZFRURbMF1bMV0rIiBzb2xkIMK3ICIrRkVFRFswXVszXSk6Im5vIHNhbGVzIHlldCJ9PC9zbWFs"
    "bD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC53aW5iYWNrPyIgb24iOiIifSIgZGF0"
    "YS1mZWF0PSJ3aW5iYWNrIj48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJsZWFkZXJib2Fy"
    "ZCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5MZWFkZXJib2FyZCBwcml6ZTwvaDM+CiAgICAgPGRp"
    "diBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPldlZWtseSBwcml6"
    "ZSAo4oK5KTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1MDAiPjwvZGl2PgogICAg"
    "IDxidXR0b24gY2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJwcml6ZXNh"
    "dmUiPlNhdmU8L2J1dHRvbj48L2Rpdj5gKX1gOwp9CgpmdW5jdGlvbiBhQW5hbHl0aWNzKCl7CiAg"
    "Y29uc3Qgd2Vlaz0oQURNLnNlcmllc3x8W10pLnJlZHVjZSgoYSxiKT0+YStiLDApOwogIGNvbnN0"
    "IGF2Zz1BRE0ua3BpLm9yZGVycz9BRE0ua3BpLnJldi9BRE0ua3BpLm9yZGVyczowOwogIHJldHVy"
    "biBgJHtHKCJyZXZDaGFydCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5SZXZlbnVlPC9oMz48cCBj"
    "bGFzcz0ic3ViIj5MYXN0IDcgZGF5cyDCtyAke21vbmV5KHdlZWspfSB0b3RhbDwvcD4KICAgICA8"
    "ZGl2IGNsYXNzPSJiYXJzIj4ke0FETS5iYXJzLm1hcChoPT5gPGkgc3R5bGU9ImhlaWdodDoke2h9"
    "JSI+PC9pPmApLmpvaW4oIiIpfTwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImtwaXMiIHN0eWxlPSJt"
    "YXJnaW4tdG9wOjE0cHgiPiR7W1siVG9kYXkiLG1vbmV5KEFETS5rcGkucmV2KV0sWyJUaGlzIG1v"
    "bnRoIixtb25leShBRE0ubWFya2V0Lm1vbnRoKV0sWyJBdmcgb3JkZXIiLG1vbmV5KGF2ZyldXQog"
    "ICAgICAgLm1hcCgoW2ssdl0pPT5gPGRpdiBjbGFzcz0ia3BpIj48c21hbGw+JHtrfTwvc21hbGw+"
    "PGI+JHt2fTwvYj48L2Rpdj5gKS5qb2luKCIiKX08L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJmdW5u"
    "ZWwiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+Q29udmVyc2lvbiBmdW5uZWw8L2gzPjxwIGNsYXNz"
    "PSJzdWIiPldoZXJlIHBlb3BsZSBkcm9wIG9mZjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmdW5uZWwi"
    "PiR7KCgpPT57Y29uc3QgZj1BRE0uYW5hbHl0aWNzLmZ1bm5lbCx0PU1hdGgubWF4KDEsZi51c2Vy"
    "cyk7CiAgICAgICByZXR1cm4gW1siU3RhcnRlZCBib3QiLGYudXNlcnMsMTAwXSxbIkRlcG9zaXRl"
    "ZCIsZi5kZXAsTWF0aC5yb3VuZChmLmRlcC90KjEwMCldLFsiQm91Z2h0IixmLmJ1eSxNYXRoLnJv"
    "dW5kKGYuYnV5L3QqMTAwKV1dCiAgICAgICAubWFwKChbayx2LHddKT0+YDxkaXY+PGkgc3R5bGU9"
    "IndpZHRoOiR7TWF0aC5tYXgoNCx3KX0lIj48L2k+PHNwYW4gc3R5bGU9IndoaXRlLXNwYWNlOm5v"
    "d3JhcCI+JHtrfSDCtyA8Yj4ke3Z9PC9iPjwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKTt9KSgpfTwv"
    "ZGl2PjwvZGl2PmApfQogICAke0coInByb2ZpdFJlcCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Q"
    "ZXItY291bnRyeSBwcm9maXQ8L2gzPgogICAgICR7QURNLmFuYWx5dGljcy5jb3VudHJ5Lmxlbmd0"
    "aD9BRE0uYW5hbHl0aWNzLmNvdW50cnkubWFwKChbYyxuLHN1bV0pPT5gPGRpdiBjbGFzcz0ia3Yi"
    "PjxzcGFuPiR7ZXNjKGMpfSDCtyAke259IHNvbGQ8L3NwYW4+PGIgc3R5bGU9ImNvbG9yOnZhcigt"
    "LW9rKSI+JHttb25leShzdW0pfTwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgOic8cCBjbGFz"
    "cz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIHNhbGVzIHlldC48L3A+J30KICAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4gYmxvY2sgc29mdCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1hZG1p"
    "bi1hY3Q9ImV4cG9ydCI+RXhwb3J0IENTVjwvYnV0dG9uPjwvZGl2PmApfQogICAke0coImNsdiIs"
    "YDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5DdXN0b21lciB2YWx1ZTwvaDM+CiAgICAgJHtBRE0udXNl"
    "cnMubGVuZ3RoP0FETS51c2Vycy5zbGljZSgwLDUpLm1hcCh1PT5gPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7ZXNjKHUubmFtZSl9PC9iPiDCtyAke3Uu"
    "b3JkZXJzfSBvcmRlcnM8L3NwYW4+CiAgICAgICAgIDxiPiR7bW9uZXkodS5zcGVudCl9IDxzcGFu"
    "IGNsYXNzPSJwaWxsICR7dS5zcGVudD4wPyJvbiI6ImhvdCJ9Ij4ke3Uuc3BlbnQ+MD8iYWN0aXZl"
    "Ijoibm8gc3BlbmQifTwvc3Bhbj48L2I+PC9kaXY+YCkuam9pbigiIikKICAgICAgIDonPHAgY2xh"
    "c3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBjdXN0b21lcnMgeWV0LjwvcD4nfTwvZGl2PmAp"
    "fQogICAke0coImRpZ2VzdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0"
    "eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5EYWlseSBkaWdlc3Q8c21hbGw+RW5k"
    "LW9mLWRheSBzdW1tYXJ5IHRvIHRoZSBhZG1pbiBjaGF0PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJ0ZyR7RkVBVC5zYWxlc0ZlZWQ/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InNhbGVzRmVl"
    "ZCI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5TZW5kIGF0PC9s"
    "YWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjIzOjMwIj48L2Rpdj48L2Rpdj5gKX1gOwp9"
    "CgpmdW5jdGlvbiBhU2VjdXJpdHkoKXsKICByZXR1cm4gYCR7RygicmF0ZUxpbWl0IixgPGRpdiBj"
    "bGFzcz0iY2FyZCI+PGgzPlJhdGUgbGltaXRpbmc8L2gzPjxwIGNsYXNzPSJzdWIiPlN0b3BzIG9u"
    "ZSB1c2VyIGhhbW1lcmluZyB0aGUgYm90LjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTBweCI+PGxhYmVsPk1heCBhY3Rpb25zIHBlciBtaW51dGU8L2xhYmVs"
    "PjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMjAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZp"
    "ZWxkIj48bGFiZWw+Q29vbGRvd24gYWZ0ZXIgbGltaXQgKHMpPC9sYWJlbD48aW5wdXQgY2xhc3M9"
    "ImlucCIgdmFsdWU9IjYwIj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJtdWx0aUFjYyIsYDxkaXYg"
    "Y2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2"
    "IGNsYXNzPSJ0Ij5NdWx0aS1hY2NvdW50IGRldGVjdGlvbjxzbWFsbD5GbGFncyByZWZlcnJhbCBm"
    "YXJtaW5nIHBhdHRlcm5zPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5t"
    "dWx0aUFjYz8iIG9uIjoiIn0iIGRhdGEtZmVhdD0ibXVsdGlBY2MiPjwvZGl2PjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5GbGFnZ2VkIHRoaXMgd2Vlazwvc3Bhbj48Yj43IGFjY291"
    "bnRzPC9iPjwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayBzb2Z0IiBzdHlsZT0i"
    "bWFyZ2luLXRvcDoxMHB4IiBkYXRhLWEtZ289Im9yZGVycyI+UmV2aWV3IEZsYWdnZWQ8L2J1dHRv"
    "bj48L2Rpdj5gKX0KICAgJHtHKCJ2ZWxvY2l0eSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xh"
    "c3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5QdXJjaGFzZSB2"
    "ZWxvY2l0eSBhbGVydHM8c21hbGw+Q2F0Y2hlcyBzdG9sZW4gdG9wLXVwczwvc21hbGw+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0idGcke0ZFQVQudmVsb2NpdHk/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9"
    "InZlbG9jaXR5Ij48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkFs"
    "ZXJ0IGFib3ZlIE4gYnV5cyAvIDEwIG1pbjwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVl"
    "PSI1Ij48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJhZG1pbjJmYSIsYDxkaXYgY2xhc3M9ImNhcmQi"
    "PjxoMz5BZG1pbiAyRkE8L2gzPjxwIGNsYXNzPSJzdWIiPlJlcXVpcmVkIGJlZm9yZSBiYWxhbmNl"
    "IGVkaXRzLCBicm9hZGNhc3RzIGFuZCB3aXBlcy48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxsYWJlbD5NZXRob2Q8L2xhYmVsPjxzZWxlY3QgY2xh"
    "c3M9ImlucCI+PG9wdGlvbj5QSU48L29wdGlvbj48b3B0aW9uPlRPVFAgYXBwPC9vcHRpb24+PC9z"
    "ZWxlY3Q+PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBkYXRhLWZlYXQ9ImFkbWlu"
    "MmZhIj5TZXQgVXA8L2J1dHRvbj48L2Rpdj5gKX0KICAgJHtHKCJhdXRvQmFuIixgPGRpdiBjbGFz"
    "cz0iY2FyZCI+PGgzPkF1dG8tYmFuIHJ1bGVzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48"
    "ZGl2IGNsYXNzPSJ0Ij5DaGFyZ2ViYWNrPHNtYWxsPkJhbiBpbnN0YW50bHk8L3NtYWxsPjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuYmFuX2NoYXJnZWJhY2spPyIg"
    "b24iOiIifSIgZGF0YS1zZXR0aW5nPSJiYW5fY2hhcmdlYmFjayI+PC9kaXY+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+RmFrZSBVVFIgc3VibWl0dGVkPHNtYWxs"
    "PkJhbiBhZnRlciAyIGF0dGVtcHRzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7"
    "KEFETS5zdyYmQURNLnN3LmJhbl9mYWtlX3V0cik/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImJh"
    "bl9mYWtlX3V0ciI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFz"
    "cz0idCI+UmVwZWF0ZWQgZmFpbGVkIHZlcmlmaWNhdGlvbjxzbWFsbD5CYW4gYWZ0ZXIgNTwvc21h"
    "bGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5iYW5fZmFpbGVk"
    "X3ZlcmlmeSk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9ImJhbl9mYWlsZWRfdmVyaWZ5Ij48L2Rp"
    "dj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJibGFja2xpc3QiLGA8ZGl2IGNsYXNzPSJjYXJkIj48"
    "aDM+QmxhY2tsaXN0PC9oMz48cCBjbGFzcz0ic3ViIj5Vc2VyIElEcywgcGhvbmUgbnVtYmVycyBh"
    "bmQgVVBJIHJlZmVyZW5jZXMuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFy"
    "Z2luLXRvcDoxMHB4Ij48ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjdweCI+CiAgICAgICA8"
    "aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImJsLWluIiBwbGFjZWhvbGRlcj0iSUQsIHBob25lIG9yIFVQ"
    "SSByZWYiPjxidXR0b24gY2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJi"
    "bGJsb2NrIj5CbG9jazwvYnV0dG9uPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48"
    "c3Bhbj5CbG9ja2VkIGVudHJpZXM8L3NwYW4+PGI+NDI8L2I+PC9kaXY+PC9kaXY+YCl9CiAgICR7"
    "RygiZW5jcnlwdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJw"
    "YWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5FbmNyeXB0IHNlc3Npb25zIGF0IHJlc3Q8c21h"
    "bGw+QSBsZWFrZWQgZGF0YWJhc2UgZmlsZSBzdGF5cyB1c2VsZXNzPC9zbWFsbD48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5lbmNyeXB0PyIgb24iOiIifSIgZGF0YS1mZWF0PSJlbmNy"
    "eXB0Ij48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+S2V5IHN0YXR1czwv"
    "c3Bhbj48YiBzdHlsZT0iY29sb3I6dmFyKC0tb2spIj5Mb2FkZWQgZnJvbSBlbnY8L2I+PC9kaXY+"
    "PC9kaXY+YCl9YDsKfQoKZnVuY3Rpb24gYVN0b2NrKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJj"
    "YXJkIj48aDM+VXBsb2FkIHN0b2NrPC9oMz48cCBjbGFzcz0ic3ViIj5QYXN0ZSBzZXNzaW9ucyBv"
    "ciB1cGxvYWQgYSBmaWxlIOKAlCBvbmUgcGVyIGxpbmUuPC9wPgo8ZGl2IGNsYXNzPSJmaWVsZCIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlBhc3RlIGFjY291bnRzIOKAlCBvbmUgcGVy"
    "IGxpbmU8L2xhYmVsPgogICAgICA8dGV4dGFyZWEgY2xhc3M9ImlucCIgaWQ9InN0cGFzdGUiIHJv"
    "d3M9IjQiCiAgICAgICAgcGxhY2Vob2xkZXI9IkluZGlhIHwgKzkxIHwgMzUgfCBzZXNzaW9uJiMx"
    "MDtLZW55YSB8ICsyNTQgfCA3NyB8IHNlc3Npb24iPjwvdGV4dGFyZWE+PC9kaXY+CiAgICA8ZGl2"
    "IGNsYXNzPSJidG5yb3ciPjxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJzdHNhdmUiPkltcG9y"
    "dDwvYnV0dG9uPgogICAgICA8bGFiZWwgY2xhc3M9ImJ0biBzb2Z0IiBzdHlsZT0iZGlzcGxheTpm"
    "bGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2N1cnNvcjpwb2lu"
    "dGVyIj4KICAgICAgICBVcGxvYWQgLnR4dDxpbnB1dCB0eXBlPSJmaWxlIiBpZD0ic3RmaWxlIiBh"
    "Y2NlcHQ9Ii50eHQsLmNzdiIgaGlkZGVuPjwvbGFiZWw+PC9kaXY+CiAgICA8cCBjbGFzcz0ic3Vi"
    "IiBpZD0ic3Rtc2ciIHN0eWxlPSJtYXJnaW46OHB4IDAgMDtmb250LXNpemU6LjcycmVtIj48L3A+"
    "CiAgICAke0coImJ1bGtVcGxvYWQiLGA8ZGl2IGNsYXNzPSJzdGVwcyIgc3R5bGU9Im1hcmdpbi10"
    "b3A6MTJweCI+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAgb24iPjxpPjE8L2k+JHtBRE0uYW5hbHl0"
    "aWNzLnN0b2NrX3RvdGFsfSBhdmFpbGFibGUgbm93PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0"
    "ZXAgb24iPjxpPjI8L2k+JHtBRE0uYW5hbHl0aWNzLnNvbGRfdG90YWx9IHNvbGQgc28gZmFyPC9k"
    "aXY+CiAgICAgIDxkaXYgY2xhc3M9InN0ZXAiPjxpPjM8L2k+QWRkIG1vcmUgZnJvbSB0aGUgYm90"
    "OiBBZG1pbiDihpIgU3RvY2s8L2Rpdj48L2Rpdj5gKX0KICAgICR7RygiZHVwRGV0ZWN0IixgPHAg"
    "Y2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjoxMHB4IDAgMDtmb250LXNpemU6LjdyZW0iPkR1cGxp"
    "Y2F0ZSBwaG9uZXMgYW5kIHNlc3Npb25zIGFyZSByZWplY3RlZCBhdXRvbWF0aWNhbGx5LjwvcD5g"
    "KX0KICAgICR7RygiaGVhbHRoQ2hlY2siLGA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0"
    "Ij5WYWxpZGF0ZSBiZWZvcmUgc2VsbGluZzxzbWFsbD5DaGVjayB0aGUgc2Vzc2lvbiBpcyBhbGl2"
    "ZSBhdCBwdXJjaGFzZSB0aW1lPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVB"
    "VC5oZWFsdGhDaGVjaz8iIG9uIjoiIn0iIGRhdGEtZmVhdD0iaGVhbHRoQ2hlY2siPjwvZGl2Pjwv"
    "ZGl2PmApfTwvZGl2PgogICAke0coImxvd1N0b2NrIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxv"
    "dy1zdG9jayBhbGVydHM8L2gzPjxwIGNsYXNzPSJzdWIiPlBlci1jb3VudHJ5IHRocmVzaG9sZCDi"
    "gJQgcGluZyB3aGVuIGl0IGRyb3BzIGJlbG93LjwvcD4KICAgICAkeyhBRE0uc3RvY2subGVuZ3Ro"
    "P0FETS5zdG9jazpTVE9DSykuc2xpY2UoMCw2KS5tYXAocz0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bh"
    "bj4ke3MuZn0gJHtlc2Mocy5jKX0gwrcgJHtzLnF0eXx8MH0gbGVmdDwvc3Bhbj4KICAgICAgIDxi"
    "PjxpbnB1dCBjbGFzcz0iaW5wIiBzdHlsZT0iaGVpZ2h0OjMwcHg7d2lkdGg6NjRweDt0ZXh0LWFs"
    "aWduOmNlbnRlcjtmb250LXNpemU6Ljc0cmVtIiB2YWx1ZT0iJHtzLmxvd3x8MTB9Ij48L2I+PC9k"
    "aXY+YCkuam9pbigiIikKICAgICAgIHx8JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+"
    "Tm8gc3RvY2sgeWV0LjwvcD4nfTwvZGl2PmApfQogICAke0coInNlbGxIb3VycyIsYDxkaXYgY2xh"
    "c3M9ImNhcmQiPjxoMz5TZWxsaW5nIGhvdXJzPC9oMz48cCBjbGFzcz0ic3ViIj5Db3VudHJpZXMg"
    "aGlkZSB0aGVtc2VsdmVzIG91dHNpZGUgdGhlIHdpbmRvdy48L3A+CiAgICAgPGRpdiBjbGFzcz0i"
    "ZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPk9wZW48L2xhYmVsPjxpbnB1dCBj"
    "bGFzcz0iaW5wIiB2YWx1ZT0iMDk6MDAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48"
    "bGFiZWw+Q2xvc2U8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMDI6MDAiPjwvZGl2"
    "PjwvZGl2PmApfQogICAke0coImR5blByaWNlIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkR5bmFt"
    "aWMgcHJpY2luZzwvaDM+CiAgICAgPGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9w"
    "OjAiPjxkaXYgY2xhc3M9InQiPlJhaXNlIHByaWNlIHdoZW4gc3RvY2sgaXMgbG93PHNtYWxsPisx"
    "MCUgdW5kZXIgMjAgdW5pdHM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFU"
    "LmR5blByaWNlPyIgb24iOiIifSIgZGF0YS1mZWF0PSJkeW5QcmljZSI+PC9kaXY+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+TmlnaHQgZGlzY291bnQ8c21hbGw+"
    "4oiSNSUgYmV0d2VlbiAwMDowMCBhbmQgMDY6MDA8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xh"
    "c3M9InRnJHsoQURNLnN3JiZBRE0uc3cubmlnaHRfZGlzY291bnQpPyIgb24iOiIifSIgZGF0YS1z"
    "ZXR0aW5nPSJuaWdodF9kaXNjb3VudCI+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiYnVs"
    "a0Rpc2MiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QnVsayBkaXNjb3VudHM8L2gzPgogICAgIDxw"
    "IGNsYXNzPSJzdWIiPlBlcmNlbnQgb2ZmIHdoZW4gdGhlIGJ1eWVyIHRha2VzIHNldmVyYWwgYXQg"
    "b25jZS48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgi"
    "PjxsYWJlbD41IG9yIG1vcmUgKCUpPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBk"
    "YXRhLXByaWNlPSJidWxrXzUiIHZhbHVlPSIke0FETS5wcmljaW5nP0FETS5wcmljaW5nLmJ1bGtf"
    "NTo1fSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD4xMCBvciBtb3JlICgl"
    "KTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1wcmljZT0iYnVsa18xMCIg"
    "dmFsdWU9IiR7QURNLnByaWNpbmc/QURNLnByaWNpbmcuYnVsa18xMDoxMH0iPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+MjUgb3IgbW9yZSAoJSk8L2xhYmVsPgogICAgICAg"
    "PGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtcHJpY2U9ImJ1bGtfMjUiIHZhbHVlPSIke0FETS5wcmlj"
    "aW5nP0FETS5wcmljaW5nLmJ1bGtfMjU6MTV9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCI+PGxhYmVsPkRlZmF1bHQgcHJpY2UgZm9yIG5ldyBzdG9jazwvbGFiZWw+CiAgICAgICA8aW5w"
    "dXQgY2xhc3M9ImlucCIgZGF0YS1wcmljZT0iZGVmYXVsdF9wcmljZSIgdmFsdWU9IiR7QURNLnBy"
    "aWNpbmc/QURNLnByaWNpbmcuZGVmYXVsdF9wcmljZTozNX0iPjwvZGl2PgogICAgIDwvZGl2PmAp"
    "fQogICAke0coInZhcmlhbnRzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlZhcmlhbnRzPC9oMz48"
    "cCBjbGFzcz0ic3ViIj5QcmljZWQgc2VwYXJhdGVseSBvbiBldmVyeSBjb3VudHJ5LjwvcD4KICAg"
    "ICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+MkZBIGVuYWJsZWQ8L3NwYW4+PGI+K+KCuTU8L2I+PC9k"
    "aXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPldpdGggbWFpbDwvc3Bhbj48Yj4r4oK5ODwv"
    "Yj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+U3BhbS1mcmVlIGd1YXJhbnRlZTwv"
    "c3Bhbj48Yj4r4oK5NDwvYj48L2Rpdj48L2Rpdj5gKX0KICAgPGRpdiBjbGFzcz0ic2VjIj48aDI+"
    "SW52ZW50b3J5PC9oMj48YSBjbGFzcz0ic3ViIj50YXAgYSBwcmljZSB0byBjaGFuZ2UgaXQ8L2E+"
    "PC9kaXY+CiAgICR7U1RPQ0subWFwKHM9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0i"
    "ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPGRpdiBjbGFz"
    "cz0iZmxhZyI+JHtzLmZ9PC9kaXY+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDow"
    "Ij48YiBzdHlsZT0iZm9udC1zaXplOi44M3JlbSI+JHtzLmN9PC9iPjxicj4KICAgICAgIDxzbWFs"
    "bCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4ke3MucXR5fSBhdmFp"
    "bGFibGU8L3NtYWxsPjwvZGl2PgogICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLWNwcmljZT0i"
    "JHtlc2Mocy5jKX0iIHZhbHVlPSIke3MucHJpY2V9IiBzdHlsZT0iaGVpZ2h0OjMycHg7d2lkdGg6"
    "NzhweDt0ZXh0LWFsaWduOmNlbnRlcjtmb250LXNpemU6Ljc2cmVtIj4KICAgICAKICAgICA8YnV0"
    "dG9uIGNsYXNzPSJidG4geHMgYmFkIiBkYXRhLW1pc2M9ImNsZWFyc3RvY2siIGRhdGEtbWlzYy12"
    "YWx1ZT0iJHtlc2Mocy5jKX0iPkNsZWFyPC9idXR0b24+PC9kaXY+YCkuam9pbigiIil9YDsKfQpm"
    "dW5jdGlvbiBhT3JkZXJzKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5SZWNlbnQg"
    "T3JkZXJzPC9oMj48YSBkYXRhLWFkbWluLWFjdD0iZXhwb3J0Ij5FeHBvcnQ8L2E+PC9kaXY+CiAg"
    "ICR7QURNLm9yZGVycy5sZW5ndGg/QURNLm9yZGVycy5tYXAobz0+YDxkaXYgY2xhc3M9ImNhcmQg"
    "dGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4K"
    "ICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke28uZn08L2Rpdj48ZGl2IHN0eWxlPSJmbGV4OjE7bWlu"
    "LXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjgycmVtIj4ke2VzYyhvLmlkKX08L2I+PGJy"
    "PgogICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4k"
    "e2VzYyhvLnVzZXIpfSDCtyAke21vbmV5KG8ucHJpY2UpfSDCtyAke2VzYyhvLndoZW4pfTwvc21h"
    "bGw+PC9kaXY+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwgJHtvLnN0PT09IkRlbGl2ZXJlZCI/Im9u"
    "Ijoib2ZmIn0iPiR7ZXNjKG8uc3QpfTwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKQogICAgIDonPGRp"
    "diBjbGFzcz0iY2FyZCB0aWdodCI+PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBv"
    "cmRlcnMgeWV0LjwvcD48L2Rpdj4nfQogICAke0coImRpc3B1dGUiLGA8ZGl2IGNsYXNzPSJzZWMi"
    "PjxoMj5EaXNwdXRlczwvaDI+PC9kaXY+CiAgICAgJHtBRE0uZGlzcHV0ZXMubGVuZ3RoP0FETS5k"
    "aXNwdXRlcy5tYXAoZD0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZs"
    "ZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICAgICAgPGRpdiBjbGFzcz0iZmxh"
    "ZyI+JHtpYygic2hpZWxkIil9PC9kaXY+CiAgICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4t"
    "d2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+JHtlc2MoZC5pZCl9IMK3ICR7ZXNj"
    "KGQudXNlcil9PC9iPjxicj48c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXpl"
    "Oi42OHJlbSI+JHtlc2MoZC5yZWFzb24pfTwvc21hbGw+PC9kaXY+CiAgICAgICAgIDxzcGFuIGNs"
    "YXNzPSJwaWxsICR7ZC5zdGF0dXM9PT0iT3BlbiI/ImhvdCI6Im9uIn0iPiR7ZXNjKGQuc3RhdHVz"
    "KX08L3NwYW4+PC9kaXY+YCkuam9pbigiIikKICAgICAgIDonPGRpdiBjbGFzcz0iY2FyZCB0aWdo"
    "dCI+PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBkaXNwdXRlcy48L3A+PC9kaXY+"
    "J31gKX0KICAgJHtHKCJ3YXJyYW50eSIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPlJlcGxhY2VtZW50"
    "IGNsYWltczwvaDI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRp"
    "c3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgPGRpdiBjbGFz"
    "cz0iZmxhZyI+JHtpYygic2hpZWxkIil9PC9kaXY+CiAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7"
    "bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjhyZW0iPjIgY2xhaW1zIGluc2lkZSB3"
    "YXJyYW50eTwvYj48YnI+CiAgICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtm"
    "b250LXNpemU6LjY4cmVtIj5BdXRvLXJlcGxhY2VkIGZyb20gc3RvY2s8L3NtYWxsPjwvZGl2Pgog"
    "ICAgICAgPC9kaXY+YCl9CiAgIAogICAke0coInJldmlld3MiLGA8ZGl2IGNsYXNzPSJzZWMiPjxo"
    "Mj5SZXZpZXdzPC9oMj48L2Rpdj4KICAgICAkeyhBRE0ucmV2aWV3cy5sZW5ndGg/QURNLnJldmll"
    "d3MubWFwKHI9PltyLnVzZXIsci5jLHIuc3RhcnMsci50ZXh0XSk6W10pCiAgICAgICAubWFwKChb"
    "dSxjLHIsdF0pPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDth"
    "bGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4k"
    "e2ljKCJzdGFyIil9PC9kaXY+CiAgICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6"
    "MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+JHt1fSDCtyAke2N9IMK3ICR7cn08L2I+PGJy"
    "PgogICAgICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY4"
    "cmVtIj4ke3R9PC9zbWFsbD48L2Rpdj4KICAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNv"
    "ZnQiIGRhdGEtcmV2PSIke3IuaWR9Ij5IaWRlPC9idXR0b24+PC9kaXY+YCkuam9pbigiIil9YCl9"
    "YDsKfQpmdW5jdGlvbiBhVXNlcnMoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImZpZWxkIj48aW5w"
    "dXQgY2xhc3M9ImlucCIgcGxhY2Vob2xkZXI9IlNlYXJjaCBieSBJRCBvciB1c2VybmFtZSI+PC9k"
    "aXY+CiAgICR7QURNLnVzZXJzLmxlbmd0aD9BRE0udXNlcnMubWFwKHU9PmA8ZGl2IGNsYXNzPSJj"
    "YXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBw"
    "eCI+CiAgICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoInVzZXIiKX08L2Rpdj4KICAgICAgPGRp"
    "diBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44MnJlbSI+"
    "JHtlc2ModS5uYW1lKX08L2I+PGJyPgogICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0t"
    "dHgyKTtmb250LXNpemU6LjY5cmVtIj4ke21vbmV5KHUuc3BlbnQpfSBzcGVudCDCtyAke3Uub3Jk"
    "ZXJzfSBvcmRlcnMgwrcgJHttb25leSh1LmJhbGFuY2UpfSBiYWxhbmNlJHt1LmJhbm5lZD8iIMK3"
    "IGJhbm5lZCI6IiJ9PC9zbWFsbD48L2Rpdj4KICAgICAgPHNwYW4gY2xhc3M9InBpbGwgJHt1LmJh"
    "bm5lZD8ib2ZmIjoib24ifSI+JHtlc2ModS50aWVyKX08L3NwYW4+PC9kaXY+YCkuam9pbigiIikK"
    "ICAgICA6JzxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiPjxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJn"
    "aW46MCI+Tm8gdXNlcnMgeWV0LjwvcD48L2Rpdj4nfQogICAke0coImFkbWluUm9sZXMiLGA8ZGl2"
    "IGNsYXNzPSJzZWMiPjxoMj5BZG1pbiByb2xlczwvaDI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "Y2FyZCI+CiAgICAgICAke0FETS5hZG1pbnMubGVuZ3RoP0FETS5hZG1pbnMubWFwKGE9PmA8ZGl2"
    "IGNsYXNzPSJrdiI+PHNwYW4+PGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHtlc2MoYS5sYWJl"
    "bCl9PC9iPiDCtyAke2VzYyhhLnBlcm1zKX08L3NwYW4+PGI+PHNwYW4gY2xhc3M9InBpbGwgb24i"
    "PiR7ZXNjKGEucm9sZSl9PC9zcGFuPjwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgICA6Jzxw"
    "IGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+T25seSB5b3Ugc28gZmFyLjwvcD4nfQogICAg"
    "ICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPjxkaXYgc3R5bGU9"
    "ImRpc3BsYXk6ZmxleDtnYXA6N3B4Ij4KICAgICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIHBsYWNl"
    "aG9sZGVyPSJAdXNlcm5hbWUiPjxzZWxlY3QgY2xhc3M9ImlucCIgc3R5bGU9IndpZHRoOmF1dG8i"
    "PjxvcHRpb24+TWFuYWdlcjwvb3B0aW9uPjxvcHRpb24+U3VwcG9ydDwvb3B0aW9uPjwvc2VsZWN0"
    "PgogICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBp"
    "ZD0iYWRtaW5hZGQiPkFkZDwvYnV0dG9uPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImJ1"
    "eWVyQmFkZ2UiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+VmVyaWZpZWQgYnV5ZXIgYmFkZ2U8L2gz"
    "PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5P"
    "cmRlcnMgcmVxdWlyZWQ8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iNSI+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlZlcmlmaWVkIHVzZXJzPC9zcGFuPjxiPjg0PC9i"
    "PjwvZGl2PjwvZGl2PmApfQogICAke0coIm11bHRpQWNjIixgPGRpdiBjbGFzcz0iY2FyZCB0aWdo"
    "dCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAg"
    "IDxzcGFuIGNsYXNzPSJwaWxsIGhvdCI+NyBmbGFnZ2VkPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9"
    "ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlBv"
    "c3NpYmxlIG11bHRpLWFjY291bnRzIHRoaXMgd2VlazwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9"
    "ImJ0biB4cyBzb2Z0IiBkYXRhLWEtZ289InNlY3VyaXR5Ij5SZXZpZXc8L2J1dHRvbj48L2Rpdj5g"
    "KX1gOwp9CmZ1bmN0aW9uIGFTZXR0aW5ncygpewogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCI+"
    "PGgzPlN0b3JlPC9oMz48cCBjbGFzcz0ic3ViIj5FdmVyeXRoaW5nIGxpdmVzIGluIHRoZSBkYXRh"
    "YmFzZSDigJQgbm8gY29kZSBlZGl0cywgbm8gcmVzdGFydC48L3A+CiAgICA8ZGl2IGNsYXNzPSJm"
    "aWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlN0b3JlIG5hbWU8L2xhYmVsPgog"
    "ICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9InN0b3JlX25hbWUiIHZhbHVlPSIke2Vz"
    "YyhDTVMuc3RvcmVOYW1lfHxDRkcubmFtZSl9Ij48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxk"
    "Ij48bGFiZWw+VGFnbGluZTwvbGFiZWw+CiAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNl"
    "dD0ic3RvcmVfdGFnbGluZSIgdmFsdWU9IiR7ZXNjKENNUy5zdG9yZVRhZ3x8Q0ZHLnRhZyl9Ij48"
    "L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+VGFnbGluZTwvbGFiZWw+PGlucHV0"
    "IGNsYXNzPSJpbnAiIHZhbHVlPSIke0NGRy50YWd9Ij48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZp"
    "ZWxkIj48bGFiZWw+U3VwcG9ydCBjb250YWN0PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgcGxh"
    "Y2Vob2xkZXI9IkB5b3VyX3N1cHBvcnQiPjwvZGl2PgogICAgPC9kaXY+CiAgIDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxoMz5Ub2dnbGVzPC9oMz4KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9"
    "InQiPkZvcmNlIGpvaW48c21hbGw+UmVxdWlyZSBjaGFubmVsIG1lbWJlcnNoaXA8L3NtYWxsPjwv"
    "ZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuZm9yY2Vfc3ViKT8iIG9u"
    "IjoiIn0iIGRhdGEtc2V0dGluZz0iZm9yY2Vfc3ViIj48L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xh"
    "c3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPk1haW50ZW5hbmNlIG1vZGU8c21hbGw+RnJlZXplIHRo"
    "ZSBzdG9yZTwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5z"
    "dy5tYWludGVuYW5jZSk/IiBvbiI6IiJ9IiBkYXRhLXNldHRpbmc9Im1haW50ZW5hbmNlIj48L2Rp"
    "dj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPkF1dG8gcmVmdW5k"
    "czxzbWFsbD5SZWZ1bmQgZmFpbGVkIGxvZ2luczwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFz"
    "cz0idGcke0ZFQVQuYXV0b1JlZnVuZD8iIG9uIjoiIn0iIGRhdGEtZmVhdD0iYXV0b1JlZnVuZCI+"
    "PC9kaXY+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5SZWZlcnJh"
    "bCBwcm9ncmFtPHNtYWxsPjElIG9mIGludml0ZWQgZGVwb3NpdHM8L3NtYWxsPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9InRnJHtGRUFULnJlZkRhc2g/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9InJlZkRh"
    "c2giPjwvZGl2PjwvZGl2PjwvZGl2PgogICAke0coImkxOG4iLGA8ZGl2IGNsYXNzPSJjYXJkIj48"
    "aDM+TGFuZ3VhZ2VzPC9oMz48cCBjbGFzcz0ic3ViIj5Vc2VycyBwaWNrIHRoZWlyIG93bjsgZXZl"
    "cnkgc3RyaW5nIGlzIHRyYW5zbGF0ZWQuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFi"
    "ZWw+RGVmYXVsdCBsYW5ndWFnZTwvbGFiZWw+CiAgICAgIDxzZWxlY3QgY2xhc3M9ImlucCIgZGF0"
    "YS1zZXRzZWw9ImxhbmdfZGVmYXVsdCI+CiAgICAgICAgPG9wdGlvbiB2YWx1ZT0iZW4iICR7KEFE"
    "TS5zZXR0aW5ncyYmQURNLnNldHRpbmdzLmxhbmc9PT0iZW4iKT8ic2VsZWN0ZWQiOiIifT5Fbmds"
    "aXNoPC9vcHRpb24+CiAgICAgICAgPG9wdGlvbiB2YWx1ZT0iaGkiICR7KEFETS5zZXR0aW5ncyYm"
    "QURNLnNldHRpbmdzLmxhbmc9PT0iaGkiKT8ic2VsZWN0ZWQiOiIifT7gpLngpL/gpKjgpY3gpKbg"
    "pYA8L29wdGlvbj4KICAgICAgPC9zZWxlY3Q+PC9kaXY+PC9kaXY+YCl9CiAgICR7Rygic2xhc2hD"
    "bWRzIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlNsYXNoIGNvbW1hbmRzPC9oMz48cCBjbGFzcz0i"
    "c3ViIj5SZWdpc3RlcmVkIGluIFRlbGVncmFtJ3MgY29tbWFuZCBtZW51LjwvcD4KICAgICAke1tb"
    "Ii9zdGFydCIsIk9wZW4gdGhlIHN0b3JlIl0sWyIvYnV5IiwiSnVtcCB0byBzdG9jayJdLFsiL2Jh"
    "bGFuY2UiLCJTaG93IHdhbGxldCJdLFsiL29yZGVycyIsIk15IG9yZGVycyJdLFsiL3N1cHBvcnQi"
    "LCJDb250YWN0IHN1cHBvcnQiXV0KICAgICAgIC5tYXAoKFtjLGRdKT0+YDxkaXYgY2xhc3M9Imt2"
    "Ij48c3Bhbj48YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4ke2N9PC9iPjwvc3Bhbj48YiBzdHls"
    "ZT0iZm9udC13ZWlnaHQ6NTAwO2ZvbnQtc2l6ZTouNzRyZW0iPiR7ZH08L2I+PC9kaXY+YCkuam9p"
    "bigiIil9CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIHNvZnQiIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjEwcHgiIGRhdGEtYWRtaW4tYWN0PSJzeW5jIj5TeW5jIENvbW1hbmRzPC9idXR0b24+PC9k"
    "aXY+YCl9CiAgICR7RygiYXV0b0JhY2t1cCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5BdXRvLWJh"
    "Y2t1cDwvaDM+PHAgY2xhc3M9InN1YiI+RGF0YWJhc2UgdG8gYSBwcml2YXRlIGNoYW5uZWwgb24g"
    "YSBzY2hlZHVsZS48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9w"
    "OjhweCI+PGxhYmVsPkV2ZXJ5IChob3Vycyk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1"
    "ZT0iNiI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxhc3QgYmFja3VwPC9zcGFu"
    "PjxiPjQyIG1pbiBhZ288L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0i"
    "bWFyZ2luLXRvcDoxMHB4Ij48YnV0dG9uIGNsYXNzPSJidG4iIGRhdGEtYWRtaW4tYWN0PSJiYWNr"
    "dXAiPkJhY2sgVXAgTm93PC9idXR0b24+CiAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3Qi"
    "IGRhdGEtYWRtaW4tYWN0PSJyZXN0b3JlIj5SZXN0b3JlPC9idXR0b24+PC9kaXY+PC9kaXY+YCl9"
    "CiAgICR7RygiYXVkaXRMb2ciLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QXVkaXQgbG9nPC9oMz48"
    "cCBjbGFzcz0ic3ViIj5FdmVyeSBhZG1pbiBhY3Rpb24sIHdpdGggdW5kby48L3A+CiAgICAgJHso"
    "KEFETS5hdWRpdCYmQURNLmF1ZGl0Lmxlbmd0aCk/QURNLmF1ZGl0Lm1hcChhPT5bYS53aG8sKGEu"
    "d2hhdCsiICIrYS5kZXRhaWwpLnRyaW0oKSxhLndoZW5dKTpbXSkKICAgICAgIC5tYXAoKFt1LGEs"
    "dF0pPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7"
    "dX08L2I+ICR7YX08L3NwYW4+CiAgICAgICAgIDxiPiR7dH0gPC9iPjwvZGl2PmApLmpvaW4oIiIp"
    "fTwvZGl2PmApfQogICAke0coInNhbmRib3giLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNz"
    "PSJzd2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+U2FuZGJveCAvIHRl"
    "c3QgbW9kZTxzbWFsbD5GYWtlIG1vbmV5LCBubyByZWFsIGRlbGl2ZXJ5PC9zbWFsbD48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LnNhbmRib3gpPyIgb24iOiIifSIg"
    "ZGF0YS1zZXR0aW5nPSJzYW5kYm94Ij48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJlbmNy"
    "eXB0IixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1p"
    "dGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFz"
    "cz0iZG90Ij48L2k+RW5jcnlwdGVkPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4t"
    "d2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlNlc3Npb25zIGFyZSBl"
    "bmNyeXB0ZWQgYXQgcmVzdDwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBk"
    "YXRhLWEtZ289InNlY3VyaXR5Ij5LZXlzPC9idXR0b24+PC9kaXY+YCl9CiAgIDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxoMz5EYW5nZXIgem9uZTwvaDM+PHAgY2xhc3M9InN1YiI+SXJyZXZlcnNpYmxlLiBB"
    "c2sgdHdpY2UuPC9wPgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "MnB4Ij48YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1hZG1pbi1hY3Q9ImJhY2t1cCI+"
    "QmFja3VwPC9idXR0b24+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmFkIGhvbGQiIGRhdGEtZG9u"
    "ZT0iRGF0YWJhc2UgcmVzZXQgKGRlbW8pIj48c3BhbiBjbGFzcz0iZmlsbCI+PC9zcGFuPkhvbGQg"
    "dG8gUmVzZXQgREI8L2J1dHRvbj48L2Rpdj48L2Rpdj5gOwp9CgpmdW5jdGlvbiBiaW5kQWRtaW4o"
    "KXsKICBiaW5kQ29tbW9uKCk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZmVh"
    "dF0iKS5mb3JFYWNoKHQ9PnQub25jbGljaz0oKT0+ewogICAgY29uc3Qgaz10LmRhdGFzZXQuZmVh"
    "dDtGRUFUW2tdPSFGRUFUW2tdO3QuY2xhc3NMaXN0LnRvZ2dsZSgib24iLEZFQVRba10pOwogICAg"
    "cHVzaCgiL2FwaS9hZG1pbi9mZWF0dXJlIix7a2V5Omssb246RkVBVFtrXX0pOwogICAgY29uc3Qg"
    "Yz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZmNvdW50Iik7CiAgICBpZihjKWMudGV4dENvbnRl"
    "bnQ9T2JqZWN0LnZhbHVlcyhGRUFUKS5maWx0ZXIoQm9vbGVhbikubGVuZ3RoKyIgb2YgIitPYmpl"
    "Y3Qua2V5cyhGRUFUKS5sZW5ndGg7CiAgICBhcHBseUZlYXR1cmVzKCk7CiAgICBjb25zdCBjYXJk"
    "PXQuY2xvc2VzdCgiLmZjYXJkIiksIG5hbWVFbD1jYXJkP2NhcmQucXVlcnlTZWxlY3RvcigiYiIp"
    "Om51bGw7CiAgICBjb25zdCBsYWJlbD1uYW1lRWw/bmFtZUVsLnRleHRDb250ZW50OmsucmVwbGFj"
    "ZSgvKFtBLVpdKS9nLCIgJDEiKS50b0xvd2VyQ2FzZSgpOwogICAgdG9hc3QobGFiZWwrKEZFQVRb"
    "a10/IiBlbmFibGVkIjoiIGhpZGRlbiIpKTt9KTsKICBjb25zdCBmcT1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiZnEiKTsKICBpZihmcSlmcS5vbmlucHV0PSgpPT57Y29uc3QgcT1mcS52YWx1ZS50"
    "cmltKCkudG9Mb3dlckNhc2UoKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5mcm93"
    "IikuZm9yRWFjaChyPT5yLmNsYXNzTGlzdC50b2dnbGUoImhpZGUiLCEhcSYmIXIuZGF0YXNldC5m"
    "LmluY2x1ZGVzKHEpKSk7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIuZmdycCIpLmZv"
    "ckVhY2goZz0+Zy5jbGFzc0xpc3QudG9nZ2xlKCJoaWRlIiwhZy5xdWVyeVNlbGVjdG9yKCIuZnJv"
    "dzpub3QoLmhpZGUpIikpKTt9OwogIGNvbnN0IHNldEFsbD12PT57T2JqZWN0LmtleXMoRkVBVCku"
    "Zm9yRWFjaChrPT5GRUFUW2tdPXYpO2FwcGx5RmVhdHVyZXMoKTtyZW5kZXJBZG1pbigpO3RvYXN0"
    "KHY/IkFsbCBmZWF0dXJlcyBlbmFibGVkIjoiQWxsIGZlYXR1cmVzIGhpZGRlbiIpO307CiAgY29u"
    "c3QgZmE9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImZhbGwiKSxmbj1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiZm5vbmUiKSxmcj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZnJlc2V0Iik7CiAg"
    "aWYoZmEpZmEub25jbGljaz0oKT0+c2V0QWxsKHRydWUpOwogIGlmKGZuKWZuLm9uY2xpY2s9KCk9"
    "PnNldEFsbChmYWxzZSk7CiAgaWYoZnIpZnIub25jbGljaz0oKT0+e09iamVjdC5hc3NpZ24oRkVB"
    "VCxGRUFUMCk7YXBwbHlGZWF0dXJlcygpO3JlbmRlckFkbWluKCk7dG9hc3QoIkZlYXR1cmVzIHJl"
    "c2V0Iik7fTsKICBjb25zdCBzdG1zZz1tPT57Y29uc3QgZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgic3Rtc2ciKTtpZihlKWUudGV4dENvbnRlbnQ9bTt9OwogIGFzeW5jIGZ1bmN0aW9uIGltcG9y"
    "dFN0b2NrKHRleHQpewogICAgaWYoIXRleHQudHJpbSgpKXt0b2FzdCgiUGFzdGUgc29tZSBsaW5l"
    "cyBmaXJzdCIpO3JldHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3Jl"
    "dHVybjt9CiAgICBzdG1zZygiSW1wb3J0aW5n4oCmIik7CiAgICBjb25zdCByPWF3YWl0IGFwaVRy"
    "eSgiL2FwaS9hZG1pbi9zdG9jayIse3RleHQ6dGV4dH0pOwogICAgaWYoIXIpe3N0bXNnKCIiKTty"
    "ZXR1cm47fQogICAgY29uc3Qgbm90ZT1yLmFkZGVkKyIgaW1wb3J0ZWQiKyhyLnNraXBwZWQmJnIu"
    "c2tpcHBlZC5sZW5ndGg/KCIgwrcgIityLnNraXBwZWQubGVuZ3RoKyIgc2tpcHBlZCIpOiIiKTsK"
    "ICAgIHRvYXN0KHIuYWRkZWQrIiBhY2NvdW50cyBhZGRlZCIpOwogICAgY29uc3QgYm94PWRvY3Vt"
    "ZW50LmdldEVsZW1lbnRCeUlkKCJzdHBhc3RlIik7aWYoYm94KWJveC52YWx1ZT0iIjsKICAgIGF3"
    "YWl0IHJlZnJlc2goKTsKICAgIHN0bXNnKG5vdGUpOwogIH0KICBjb25zdCBzdHNhdmU9ZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoInN0c2F2ZSIpOwogIGlmKHN0c2F2ZSlzdHNhdmUub25jbGljaz0o"
    "KT0+aW1wb3J0U3RvY2soKGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdHBhc3RlIil8fHt9KS52"
    "YWx1ZXx8IiIpOwogIGNvbnN0IHN0ZmlsZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RmaWxl"
    "Iik7CiAgaWYoc3RmaWxlKXN0ZmlsZS5vbmNoYW5nZT0oKT0+ewogICAgY29uc3QgZj1zdGZpbGUu"
    "ZmlsZXMmJnN0ZmlsZS5maWxlc1swXTtpZighZilyZXR1cm47CiAgICBjb25zdCByZD1uZXcgRmls"
    "ZVJlYWRlcigpOwogICAgcmQub25sb2FkPSgpPT57Y29uc3QgYm94PWRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJzdHBhc3RlIik7CiAgICAgIGlmKGJveClib3gudmFsdWU9cmQucmVzdWx0O3N0bXNn"
    "KGYubmFtZSsiIGxvYWRlZCDigJQgcHJlc3MgSW1wb3J0Iik7fTsKICAgIHJkLnJlYWRBc1RleHQo"
    "Zik7CiAgfTsKICBjb25zdCBibD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYmxibG9jayIpOwog"
    "IGlmKGJsKWJsLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgaT1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiYmwtaW4iKTsKICAgIGNvbnN0IHY9aT8oaS52YWx1ZXx8IiIpLnRyaW0oKToiIjsK"
    "ICAgIGlmKCF2KXt0b2FzdCgiVHlwZSBhbiBpZCwgcGhvbmUgb3IgVVBJIHJlZmVyZW5jZSIpO2lm"
    "KGkpaS5mb2N1cygpO3JldHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIp"
    "O3JldHVybjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9ibGFja2xpc3Qi"
    "LHthY3Rpb246ImFkZCIsdmFsdWU6dn0pOwogICAgaWYoIXIpcmV0dXJuOwogICAgaS52YWx1ZT0i"
    "Ijt0b2FzdCgiQmxvY2tlZCAiK3YpO2F3YWl0IHJlZnJlc2goKTsKICB9OwogIGRvY3VtZW50LnF1"
    "ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWJsLWRlbF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3lu"
    "YygpPT57CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9ibGFja2xpc3QiLHth"
    "Y3Rpb246ImRlbCIsaWQ6K2IuZGF0YXNldC5ibERlbH0pOwogICAgaWYocil7dG9hc3QoIlVuYmxv"
    "Y2tlZCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiW2RhdGEtbWlzY10iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBpZigh"
    "TElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBjb25zdCB3aGF0PWIuZGF0"
    "YXNldC5taXNjLCB2YWw9Yi5kYXRhc2V0Lm1pc2NWYWx1ZXx8IiI7CiAgICBjb25zdCBsYmw9Yi50"
    "ZXh0Q29udGVudDtiLmRpc2FibGVkPXRydWU7Yi50ZXh0Q29udGVudD0i4oCmIjsKICAgIGNvbnN0"
    "IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL21pc2MiLHt3aGF0OndoYXQsdmFsdWU6dmFsfSk7"
    "CiAgICBiLmRpc2FibGVkPWZhbHNlO2IudGV4dENvbnRlbnQ9bGJsOwogICAgaWYoIXIpcmV0dXJu"
    "OwogICAgaWYoci5saW5lcylzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNp"
    "emU6MXJlbSI+Q2hhbm5lbCBjaGVjazwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJ3"
    "aGl0ZS1zcGFjZTpwcmUtbGluZSI+JHtlc2Moci5saW5lcy5qb2luKCJcbiIpKX08L3A+CiAgICAg"
    "IDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1j"
    "bG9zZT5DbG9zZTwvYnV0dG9uPmApOwogICAgZWxzZSBpZih3aGF0PT09ImxvZ3Rlc3QiKXRvYXN0"
    "KCJUZXN0IG1lc3NhZ2Ugc2VudCB0byB0aGUgbG9nIGNoYW5uZWwiKTsKICAgIGVsc2UgaWYod2hh"
    "dD09PSJjbGVhcnN0b2NrIil0b2FzdChyLnJlbW92ZWQrIiByZW1vdmVkIik7CiAgICBlbHNlIHRv"
    "YXN0KCJEb25lIik7CiAgICBhd2FpdCByZWZyZXNoKCk7CiAgfSk7CiAgY29uc3QgdXBpYWRkPWRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJ1cGlhZGQiKTsKICBpZih1cGlhZGQpdXBpYWRkLm9uY2xp"
    "Y2s9YXN5bmMoKT0+ewogICAgY29uc3QgaT11cGlhZGQuY2xvc2VzdCgiZGl2IikucXVlcnlTZWxl"
    "Y3RvcigiaW5wdXQiKTsKICAgIGNvbnN0IHY9aT8oaS52YWx1ZXx8IiIpLnRyaW0oKToiIjsKICAg"
    "IGlmKCF2KXt0b2FzdCgiUGFzdGUgdGhlIGV4dHJhIFVQSSBoYW5kbGUiKTtyZXR1cm47fQogICAg"
    "Y29uc3QgY3VyPShBRE0ucGF5JiZBRE0ucGF5LmV4dHJhKXx8IiI7CiAgICBjb25zdCByPWF3YWl0"
    "IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OiJ1cGlfZXh0cmEiLHZhbHVlOmN1cj8o"
    "Y3VyKyIsIit2KTp2fSk7CiAgICBpZihyKXtpLnZhbHVlPSIiO3RvYXN0KCJIYW5kbGUgYWRkZWQi"
    "KTthd2FpdCByZWZyZXNoKCk7fQogIH07CiAgY29uc3QgYWRtaW5hZGQ9ZG9jdW1lbnQuZ2V0RWxl"
    "bWVudEJ5SWQoImFkbWluYWRkIik7CiAgaWYoYWRtaW5hZGQpYWRtaW5hZGQub25jbGljaz1hc3lu"
    "YygpPT57CiAgICBjb25zdCBpPWFkbWluYWRkLmNsb3Nlc3QoImRpdiIpLnF1ZXJ5U2VsZWN0b3Io"
    "ImlucHV0Iik7CiAgICBjb25zdCB2PWk/KGkudmFsdWV8fCIiKS50cmltKCkucmVwbGFjZSgvXkAv"
    "LCIiKToiIjsKICAgIGlmKCF2KXt0b2FzdCgiU2VuZCB0aGUgbnVtZXJpYyB1c2VyIGlkIik7cmV0"
    "dXJuO30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL21pc2MiLHt3aGF0OiJh"
    "ZGRhZG1pbiIsdmFsdWU6dn0pOwogICAgaWYocil7aS52YWx1ZT0iIjt0b2FzdCgiQWRtaW4gYWRk"
    "ZWQgd2l0aCBubyBwb3dlcnMg4oCUIGdyYW50IHRoZW0gaW4gdGhlIGJvdCIpO2F3YWl0IHJlZnJl"
    "c2goKTt9CiAgfTsKICBjb25zdCBwcml6ZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicHJpemVz"
    "YXZlIik7CiAgaWYocHJpemUpcHJpemUub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBpPXBy"
    "aXplLmNsb3Nlc3QoImRpdiIpLnF1ZXJ5U2VsZWN0b3IoImlucHV0Iik7CiAgICBjb25zdCByPWF3"
    "YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OiJsZWFkZXJib2FyZF9wcml6ZSIs"
    "dmFsdWU6KGkmJmkudmFsdWUpfHwiMCJ9KTsKICAgIGlmKHIpdG9hc3QoIlByaXplIHNhdmVkIik7"
    "CiAgfTsKICBjb25zdCBjcGFkZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY3BhZGQiKTsKICBp"
    "ZihjcGFkZCljcGFkZC5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGNvZGU9KChkb2N1bWVu"
    "dC5nZXRFbGVtZW50QnlJZCgiY3Bjb2RlIil8fHt9KS52YWx1ZXx8IiIpLnRyaW0oKTsKICAgIGNv"
    "bnN0IHBjdD0oKGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjcHBjdCIpfHx7fSkudmFsdWV8fCIi"
    "KS50cmltKCk7CiAgICBpZighY29kZXx8IXBjdCl7dG9hc3QoIkNvZGUgYW5kIHBlcmNlbnQsIHBs"
    "ZWFzZSIpO3JldHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVy"
    "bjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jb3Vwb24iLHtjb2RlOmNv"
    "ZGUscGVyY2VudDpwY3R9KTsKICAgIGlmKCFyKXJldHVybjsKICAgIGNvbnN0IGExPWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJjcGNvZGUiKSxhMj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY3Bw"
    "Y3QiKTsKICAgIGlmKGExKWExLnZhbHVlPSIiO2lmKGEyKWEyLnZhbHVlPSIiOwogICAgdG9hc3Qo"
    "IkNvdXBvbiAiK2NvZGUudG9VcHBlckNhc2UoKSsiIGlzIGxpdmUiKTthd2FpdCByZWZyZXNoKCk7"
    "CiAgfTsKICBjb25zdCBmZz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZmxhc2hnbyIpOwogIGlm"
    "KGZnKWZnLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgY2FyZD1mZy5jbG9zZXN0KCIuY2Fy"
    "ZCIpfHxkb2N1bWVudDsKICAgIGNvbnN0IGlucz1jYXJkLnF1ZXJ5U2VsZWN0b3JBbGwoImlucHV0"
    "Iik7CiAgICBjb25zdCBwY3Q9KChpbnNbMF0mJmluc1swXS52YWx1ZSl8fCIyMCIpLCBocnM9KChp"
    "bnNbMV0mJmluc1sxXS52YWx1ZSl8fCI1Iik7CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcg"
    "b25seSIpO3JldHVybjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9mbGFz"
    "aCIse3BlcmNlbnQ6cGN0LGhvdXJzOmhyc30pOwogICAgaWYocil7dG9hc3QoIkZsYXNoIHNhbGUg"
    "bGl2ZSDigJQgIitwY3QrIiUgZm9yICIraHJzKyJoIik7YXdhaXQgcmVmcmVzaCgpO30KICB9Owog"
    "IGNvbnN0IGJjPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJiY2dvIik7CiAgaWYoYmMpYmMub25j"
    "bGljaz1hc3luYygpPT57CiAgICBjb25zdCBjYXJkPWJjLmNsb3Nlc3QoIi5jYXJkIil8fGRvY3Vt"
    "ZW50OwogICAgY29uc3QgdGE9Y2FyZC5xdWVyeVNlbGVjdG9yKCJ0ZXh0YXJlYSIpOwogICAgY29u"
    "c3QgdGV4dD0oKHRhJiZ0YS52YWx1ZSl8fCIiKS50cmltKCk7CiAgICBpZighdGV4dCl7dG9hc3Qo"
    "IldyaXRlIHRoZSBtZXNzYWdlIGZpcnN0Iik7aWYodGEpdGEuZm9jdXMoKTtyZXR1cm47fQogICAg"
    "aWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgYmMuZGlzYWJsZWQ9"
    "dHJ1ZTtiYy50ZXh0Q29udGVudD0iU2VuZGluZ+KApiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRy"
    "eSgiL2FwaS9hZG1pbi9icm9hZGNhc3QiLHt0ZXh0OnRleHR9KTsKICAgIGJjLmRpc2FibGVkPWZh"
    "bHNlO2JjLnRleHRDb250ZW50PSJTZW5kIEJyb2FkY2FzdCI7CiAgICBpZihyKXt0b2FzdCgiU2Vu"
    "dCB0byAiK3Iuc2VudCsiIHBlb3BsZSIpO2lmKHRhKXRhLnZhbHVlPSIiO30KICB9OwogIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWRlcC1va10iKS5mb3JFYWNoKGI9PmIub25jbGlj"
    "az1hc3luYygpPT57CiAgICBjb25zdCBpZD1iLmRhdGFzZXQuZGVwT2s7CiAgICBpZighaWQpe3Rv"
    "YXN0KCJOb3RoaW5nIHRvIGFwcHJvdmUiKTtyZXR1cm47fQogICAgY29uc3Qgcj1hd2FpdCBhcGlU"
    "cnkoIi9hcGkvYWRtaW4vZGVwb3NpdCIse2lkOitpZCxhY3Rpb246ImFwcHJvdmUifSk7CiAgICBp"
    "ZihyKXt0b2FzdCgiQ3JlZGl0ZWQiKTthd2FpdCByZWZyZXNoKCk7fQogIH0pOwogIGRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXJldl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3lu"
    "YygpPT57CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9yZXZpZXciLHtpZDor"
    "Yi5kYXRhc2V0LnJldn0pOwogICAgaWYocil7dG9hc3QoIlVwZGF0ZWQiKTthd2FpdCByZWZyZXNo"
    "KCk7fQogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXNldHRpbmddIiku"
    "Zm9yRWFjaCh0PT57CiAgICB0Lm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25zdCBrZXk9dC5k"
    "YXRhc2V0LnNldHRpbmcsIG9uPSF0LmNsYXNzTGlzdC5jb250YWlucygib24iKTsKICAgICAgdC5j"
    "bGFzc0xpc3QudG9nZ2xlKCJvbiIsb24pOwogICAgICBpZighTElWRSl7dG9hc3Qob24/Ik9uIjoi"
    "T2ZmIik7cmV0dXJuO30KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc2V0"
    "dGluZyIse2tleTprZXksdmFsdWU6b24/IjEiOiIwIn0pOwogICAgICBpZighcil7dC5jbGFzc0xp"
    "c3QudG9nZ2xlKCJvbiIsIW9uKTtyZXR1cm47fQogICAgICBBRE0uc3c9QURNLnN3fHx7fTtBRE0u"
    "c3dba2V5XT1vbjsKICAgICAgdG9hc3Qoa2V5LnJlcGxhY2UoL18vZywiICIpKyhvbj8iIG9uIjoi"
    "IG9mZiIpKTsKICAgIH07CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEt"
    "YWRtaW4tYWN0XSIpLmZvckVhY2goYj0+ewogICAgYi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgICAg"
    "Y29uc3QgYWN0PWIuZGF0YXNldC5hZG1pbkFjdDsKICAgICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2"
    "aWV3IG9ubHkiKTtyZXR1cm47fQogICAgICBjb25zdCBsYWJlbD1iLnRleHRDb250ZW50O2IuZGlz"
    "YWJsZWQ9dHJ1ZTtiLnRleHRDb250ZW50PSJXb3JraW5n4oCmIjsKICAgICAgbGV0IHI9bnVsbDsK"
    "ICAgICAgaWYoYWN0PT09ImJhY2t1cCIpICAgICAgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4v"
    "YmFja3VwIix7fSk7CiAgICAgIGVsc2UgaWYoYWN0PT09ImRpZ2VzdCIpIHI9YXdhaXQgYXBpVHJ5"
    "KCIvYXBpL2FkbWluL2RpZ2VzdCIse30pOwogICAgICBlbHNlIGlmKGFjdD09PSJleHBvcnQiKSBy"
    "PWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9leHBvcnQiLHt9KTsKICAgICAgZWxzZSBpZihhY3Q9"
    "PT0ic3luYyIpICAgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc3luYyIse30pOwogICAgICBi"
    "LmRpc2FibGVkPWZhbHNlO2IudGV4dENvbnRlbnQ9bGFiZWw7CiAgICAgIGlmKCFyKXJldHVybjsK"
    "ICAgICAgaWYoYWN0PT09ImJhY2t1cCIpdG9hc3QoIkRhdGFiYXNlIHNlbnQgdG8geW91IGluIGNo"
    "YXQiKTsKICAgICAgaWYoYWN0PT09ImRpZ2VzdCIpdG9hc3QoIkRpZ2VzdCBzZW50IHRvIHRoZSBh"
    "ZG1pbnMiKTsKICAgICAgaWYoYWN0PT09ImV4cG9ydCIpdG9hc3Qoci5yb3dzKyIgb3JkZXJzIHNl"
    "bnQgYXMgQ1NWIik7CiAgICAgIGlmKGFjdD09PSJzeW5jIil0b2FzdCgiQ29tbWFuZHMgc3luY2Vk"
    "IHdpdGggVGVsZWdyYW0iKTsKICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgfTsKICB9KTsKICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1wcmljZV0iKS5mb3JFYWNoKGlucD0+ewog"
    "ICAgaW5wLm9uY2hhbmdlPWFzeW5jKCk9PnsKICAgICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3"
    "IG9ubHkiKTtyZXR1cm47fQogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9w"
    "cmljaW5nIix7a2V5OmlucC5kYXRhc2V0LnByaWNlLHZhbHVlOmlucC52YWx1ZX0pOwogICAgICBp"
    "ZihyKXt0b2FzdCgiUHJpY2luZyB1cGRhdGVkIik7YXdhaXQgcmVmcmVzaCgpO30KICAgIH07CiAg"
    "fSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY3ByaWNlXSIpLmZvckVhY2go"
    "aW5wPT57CiAgICBpbnAub25jaGFuZ2U9YXN5bmMoKT0+ewogICAgICBpZighTElWRSl7dG9hc3Qo"
    "IlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBp"
    "L2FkbWluL3ByaWNpbmciLHtjb3VudHJ5OmlucC5kYXRhc2V0LmNwcmljZSxwcmljZTppbnAudmFs"
    "dWV9KTsKICAgICAgaWYocil7dG9hc3QoaW5wLmRhdGFzZXQuY3ByaWNlKyIgbm93ICIrbW9uZXko"
    "aW5wLnZhbHVlKSk7YXdhaXQgcmVmcmVzaCgpO30KICAgIH07CiAgfSk7CiAgZG9jdW1lbnQucXVl"
    "cnlTZWxlY3RvckFsbCgiW2RhdGEtcWFdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAg"
    "IGNvbnN0IHdoYXQ9Yi5kYXRhc2V0LnFhOwogICAgY29uc3QgZ289KHRhYixtc2cpPT57CiAgICAg"
    "IGNvbnN0IHQ9ZG9jdW1lbnQucXVlcnlTZWxlY3RvcihgI2FzdWIgYnV0dG9uW2RhdGEtYT0iJHt0"
    "YWJ9Il1gKTsKICAgICAgaWYodCl7dC5jbGljaygpO3RvYXN0KG1zZyk7fWVsc2UgdG9hc3QobXNn"
    "KTsKICAgIH07CiAgICBpZih3aGF0PT09ImJyb2FkY2FzdCIpcmV0dXJuIGdvKCJtYXJrZXQiLCJD"
    "b21wb3NlIHRoZSBicm9hZGNhc3QgZnJvbSB0aGUgYm90OiAvYWRtaW4g4oaSIE1hcmtldGluZyIp"
    "OwogICAgaWYod2hhdD09PSJzdG9jayIpcmV0dXJuIGdvKCJzdG9jayIsIkFkZCBzdG9jayBmcm9t"
    "IHRoZSBib3Q6IC9hZG1pbiDihpIgU3RvY2sg4oaSIEFkZCBzdG9jayIpOwogICAgaWYod2hhdD09"
    "PSJmbGFzaCIpcmV0dXJuIGdvKCJtYXJrZXQiLCJGbGFzaCBzYWxlIGxpdmVzIGhlcmUiKTsKICAg"
    "IGlmKHdoYXQ9PT0iYmFja3VwIilyZXR1cm4gdG9hc3QoIkJhY2t1cHMgYXJlIHNlbnQgdG8geW91"
    "ciBsb2cgY2hhbm5lbCDigJQgL2FkbWluIOKGkiBTZXR0aW5ncyIpOwogIH0pOwogIGRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXNldF0iKS5mb3JFYWNoKGlucD0+ewogICAgaW5wLm9u"
    "Y2hhbmdlPWFzeW5jKCk9PnsKICAgICAgY29uc3Qga2V5PWlucC5kYXRhc2V0LnNldCwgdmFsPWlu"
    "cC52YWx1ZTsKICAgICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQog"
    "ICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5OmtleSx2"
    "YWx1ZTp2YWx9KTsKICAgICAgaWYocil7dG9hc3Qoa2V5LnJlcGxhY2UoL18vZywiICIpKyIgc2F2"
    "ZWQiKTthd2FpdCByZWZyZXNoKCk7fQogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCJbZGF0YS1jbXNdIikuZm9yRWFjaCh0PT57CiAgICB0Lm9uaW5wdXQ9KCk9PntDTVNb"
    "dC5kYXRhc2V0LmNtc109dC52YWx1ZTthcHBseUNNUygpOwogICAgICBjbGVhclRpbWVvdXQodC5f"
    "c3YpO3QuX3N2PXNldFRpbWVvdXQoKCk9PnB1c2goIi9hcGkvYWRtaW4vY29udGVudCIse2tleTp0"
    "LmRhdGFzZXQuY21zLHZhbHVlOnQudmFsdWV9KSw1MDApOwogICAgICB0LmNsb3Nlc3QoIi5jbXNy"
    "b3ciKS5kYXRhc2V0LmY9KHQuY2xvc2VzdCgiLmNtc3JvdyIpLnF1ZXJ5U2VsZWN0b3IoImxhYmVs"
    "IikudGV4dENvbnRlbnQrIiAiK3QudmFsdWUpLnRvTG93ZXJDYXNlKCk7fTt9KTsKICBjb25zdCBj"
    "cT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY3EiKTsKICBpZihjcSljcS5vbmlucHV0PSgpPT57"
    "Y29uc3QgcT1jcS52YWx1ZS50cmltKCkudG9Mb3dlckNhc2UoKTsKICAgIGRvY3VtZW50LnF1ZXJ5"
    "U2VsZWN0b3JBbGwoIi5jbXNyb3ciKS5mb3JFYWNoKHI9PnIuY2xhc3NMaXN0LnRvZ2dsZSgiaGlk"
    "ZSIsISFxJiYhci5kYXRhc2V0LmYuaW5jbHVkZXMocSkpKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2Vs"
    "ZWN0b3JBbGwoIi5jbXNncnAiKS5mb3JFYWNoKGc9PmcuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZSIs"
    "IWcucXVlcnlTZWxlY3RvcigiLmNtc3Jvdzpub3QoLmhpZGUpIikpKTt9OwogIGNvbnN0IGNyPWRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJjbXNyZXNldCIpOwogIGlmKGNyKWNyLm9uY2xpY2s9KCk9"
    "PntPYmplY3QuYXNzaWduKENNUyxDTVMwKTthcHBseUNNUygpO3JlbmRlckFkbWluKCk7dG9hc3Qo"
    "IlRleHQgcmVzZXQgdG8gZGVmYXVsdHMiKTt9OwogIGNvbnN0IGFkZD1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiY2gtYWRkIik7CiAgaWYoYWRkKWFkZC5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNv"
    "bnN0IGk9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNoLWluIiksdj0oaS52YWx1ZXx8IiIpLnRy"
    "aW0oKTsKICAgIGlmKCF2KXt0b2FzdCgiUGFzdGUgYSBjaGFubmVsIGxpbmsgZmlyc3QiKTtpLmZv"
    "Y3VzKCk7cmV0dXJufQogICAgY29uc3QgcmVxPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjaC10"
    "eXBlIikudmFsdWU9PT0iMSI7CiAgICBpZihMSVZFKXsKICAgICAgYWRkLmRpc2FibGVkPXRydWU7"
    "CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rpb246"
    "ImFkZCIsaGFuZGxlOnYscmVxdWlyZWQ6cmVxfSk7CiAgICAgIGFkZC5kaXNhYmxlZD1mYWxzZTsK"
    "ICAgICAgaWYoIXIpcmV0dXJuOwogICAgICBpLnZhbHVlPSIiO2F3YWl0IHJlZnJlc2goKTt0b2Fz"
    "dCgiQ2hhbm5lbCBzYXZlZCDigJQgdGhlIGJvdCB1c2VzIGl0IG5vdyIpO3JldHVybjsKICAgIH0K"
    "ICAgIENIQU5ORUxTLnB1c2goe2lkOkRhdGUubm93KCksbmFtZTp2LnJlcGxhY2UoL15odHRwcz86"
    "XC9cL3RcLm1lXC8vLCJAIikuc2xpY2UoMCwyNiksaGFuZGxlOnYscmVxOnJlcSxvbjp0cnVlLG1l"
    "bWJlcnM6IuKAlCJ9KTsKICAgIGkudmFsdWU9IiI7cmVuZGVyQWRtaW4oKTt0b2FzdCgiQ2hhbm5l"
    "bCBhZGRlZCIpO307CiAgY29uc3QgbGdhZGQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImxnLWFk"
    "ZCIpOwogIGlmKGxnYWRkKWxnYWRkLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgaT1kb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgibGctaW4iKSx2PShpLnZhbHVlfHwiIikudHJpbSgpOwogICAg"
    "aWYoIXYpe3RvYXN0KCJQYXN0ZSBhIGxvZyBjaGFubmVsIGZpcnN0Iik7aS5mb2N1cygpO3JldHVy"
    "bn0KICAgIGlmKExJVkUpewogICAgICBsZ2FkZC5kaXNhYmxlZD10cnVlOwogICAgICBjb25zdCBy"
    "PWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJhZGQiLGhhbmRsZTp2"
    "LGxvZzp0cnVlfSk7CiAgICAgIGxnYWRkLmRpc2FibGVkPWZhbHNlOwogICAgICBpZighcilyZXR1"
    "cm47CiAgICAgIGkudmFsdWU9IiI7YXdhaXQgcmVmcmVzaCgpO3RvYXN0KCJMb2cgY2hhbm5lbCBz"
    "YXZlZCIpO3JldHVybjsKICAgIH0KICAgIExPR0NILnB1c2goe2lkOkRhdGUubm93KCksbmFtZTp2"
    "LnJlcGxhY2UoL15odHRwcz86XC9cL3RcLm1lXC8vLCJAIikuc2xpY2UoMCwyNCksaGFuZGxlOnYs"
    "cHJpbWFyeTpMT0dDSC5sZW5ndGg9PT0wLG9uOnRydWV9KTsKICAgIGkudmFsdWU9IiI7cmVuZGVy"
    "QWRtaW4oKTt0b2FzdCgiTG9nIGNoYW5uZWwgYWRkZWQiKTt9OwogIGRvY3VtZW50LnF1ZXJ5U2Vs"
    "ZWN0b3JBbGwoIltkYXRhLWNoZGVsXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsK"
    "ICAgIGlmKExJVkUpe2NvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHth"
    "Y3Rpb246ImRlbCIsaWQ6K2IuZGF0YXNldC5jaGRlbH0pOwogICAgICBpZihyKXthd2FpdCByZWZy"
    "ZXNoKCk7dG9hc3QoIkNoYW5uZWwgcmVtb3ZlZCIpO31yZXR1cm47fQogICAgQ0hBTk5FTFM9Q0hB"
    "Tk5FTFMuZmlsdGVyKGM9PmMuaWQhPWIuZGF0YXNldC5jaGRlbCk7cmVuZGVyQWRtaW4oKTt0b2Fz"
    "dCgiQ2hhbm5lbCByZW1vdmVkIil9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0"
    "YS1jaHJlcV0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBpZihMSVZFKXtj"
    "b25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJyZXF1aXJl"
    "ZCIsaWQ6K2IuZGF0YXNldC5jaHJlcX0pOwogICAgICBpZihyKXthd2FpdCByZWZyZXNoKCk7dG9h"
    "c3QoIlVwZGF0ZWQiKTt9cmV0dXJuO30KICAgIGNvbnN0IGM9Q0hBTk5FTFMuZmluZCh4PT54Lmlk"
    "PT1iLmRhdGFzZXQuY2hyZXEpO2MucmVxPSFjLnJlcTtyZW5kZXJBZG1pbigpO3RvYXN0KGMucmVx"
    "PyJOb3cgcmVxdWlyZWQiOiJOb3cgb3B0aW9uYWwiKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIltkYXRhLWNob25dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAg"
    "aWYoTElWRSl7Y29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vY2hhbm5lbCIse2FjdGlv"
    "bjoidG9nZ2xlIixpZDorYi5kYXRhc2V0LmNob259KTsKICAgICAgaWYocil7YXdhaXQgcmVmcmVz"
    "aCgpO3RvYXN0KCJVcGRhdGVkIik7fXJldHVybjt9CiAgICBjb25zdCBjPUNIQU5ORUxTLmZpbmQo"
    "eD0+eC5pZD09Yi5kYXRhc2V0LmNob24pO2Mub249IWMub247cmVuZGVyQWRtaW4oKTt0b2FzdChj"
    "Lm9uPyJDaGFubmVsIGVuYWJsZWQiOiJDaGFubmVsIHBhdXNlZCIpfSk7CiAgZG9jdW1lbnQucXVl"
    "cnlTZWxlY3RvckFsbCgiW2RhdGEtbGdkZWxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsK"
    "ICAgIGNvbnN0IHdhc1A9TE9HQ0guZmluZChsPT5sLmlkPT1iLmRhdGFzZXQubGdkZWwpPy5wcmlt"
    "YXJ5OwogICAgTE9HQ0g9TE9HQ0guZmlsdGVyKGw9PmwuaWQhPWIuZGF0YXNldC5sZ2RlbCk7CiAg"
    "ICBpZih3YXNQJiZMT0dDSFswXSlMT0dDSFswXS5wcmltYXJ5PXRydWU7cmVuZGVyQWRtaW4oKTt0"
    "b2FzdCgiTG9nIGNoYW5uZWwgcmVtb3ZlZCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiW2RhdGEtbGdzdGFyXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgICBMT0dDSC5m"
    "b3JFYWNoKGw9PmwucHJpbWFyeT0obC5pZD09Yi5kYXRhc2V0Lmxnc3RhcikpO3JlbmRlckFkbWlu"
    "KCk7dG9hc3QoIlByZWZlcnJlZCBsb2cgY2hhbm5lbCBzZXQiKX0pOwogIGRvY3VtZW50LnF1ZXJ5"
    "U2VsZWN0b3JBbGwoIltkYXRhLWxnb25dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9Pntjb25z"
    "dCBsPUxPR0NILmZpbmQoeD0+eC5pZD09Yi5kYXRhc2V0Lmxnb24pO2wub249IWwub247cmVuZGVy"
    "QWRtaW4oKTt0b2FzdChsLm9uPyJDaGFubmVsIG9uIjoiQ2hhbm5lbCBtdXRlZCIpfSk7CiAgZG9j"
    "dW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZXZvbl0iKS5mb3JFYWNoKGI9PmIub25jbGlj"
    "az0oKT0+e2NvbnN0IGU9RVZFTlRTWytiLmRhdGFzZXQuZXZvbl07ZS5vbj0hZS5vbjtyZW5kZXJB"
    "ZG1pbigpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZXZjaF0iKS5mb3JF"
    "YWNoKHM9PnMub25jaGFuZ2U9KCk9PntFVkVOVFNbK3MuZGF0YXNldC5ldmNoXS5jaD0rcy52YWx1"
    "ZTt0b2FzdCgiUm91dGluZyB1cGRhdGVkIil9KTsKICBjb25zdCBhbGw9ZG9jdW1lbnQucXVlcnlT"
    "ZWxlY3RvcigiW2RhdGEtZXYtYWxsXSIpOwogIGlmKGFsbClhbGwub25jbGljaz0oKT0+e2NvbnN0"
    "IHQ9IUVWRU5UUy5ldmVyeShlPT5lLm9uKTtFVkVOVFMuZm9yRWFjaChlPT5lLm9uPXQpO3JlbmRl"
    "ckFkbWluKCk7dG9hc3QodD8iQWxsIGV2ZW50cyBvbiI6IkFsbCBldmVudHMgb2ZmIil9OwogIGNv"
    "bnN0IG09ZG9jdW1lbnQucXVlcnlTZWxlY3RvcigiW2RhdGEtbWFzdGVyXSIpOwogIGlmKG0pbS5v"
    "bmNsaWNrPSgpPT57TE9HU19NQVNURVI9IUxPR1NfTUFTVEVSO3JlbmRlckFkbWluKCk7dG9hc3Qo"
    "TE9HU19NQVNURVI/IkxvZ2dpbmcgZW5hYmxlZCI6IkFsbCBsb2dnaW5nIGRpc2FibGVkIil9Owog"
    "IGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWEtZ29dIikuZm9yRWFjaChiPT5iLm9u"
    "Y2xpY2s9KCk9PnsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNhc3ViIGJ1dHRvbiIp"
    "LmZvckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseC5kYXRhc2V0LmE9PT1iLmRhdGFz"
    "ZXQuYUdvKSk7CiAgICBBVEFCPWIuZGF0YXNldC5hR287cmVuZGVyQWRtaW4oKTt3aW5kb3cuc2Ny"
    "b2xsVG8oMCwwKTt9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS10Z10iKS5m"
    "b3JFYWNoKHQ9PnQub25jbGljaz0oKT0+dC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIpKTsKICBkb2N1"
    "bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1zZXRzZWxdIikuZm9yRWFjaChzZWw9PnNlbC5v"
    "bmNoYW5nZT1hc3luYygpPT57CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3Jl"
    "dHVybjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5nIix7a2V5"
    "OnNlbC5kYXRhc2V0LnNldHNlbCx2YWx1ZTpzZWwudmFsdWV9KTsKICAgIGlmKHIpdG9hc3QoIlNh"
    "dmVkIik7CiAgfSk7Cn0KCmNvbnN0IF9jYz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2FydGNo"
    "aXAiKTsKaWYoX2NjKV9jYy5vbmNsaWNrPWNhcnRTaGVldDsKcmVuZGVyKCk7cmVuZGVyQWRtaW4o"
    "KTthcHBseUZlYXR1cmVzKCk7CgovKiDilIDilIAgbGl2ZSBkYXRhOiBldmVyeXRoaW5nIGJlbG93"
    "IGNvbWVzIGZyb20gdGhlIGJvdCBkYXRhYmFzZSB3aGVuIHNlcnZlZCBieSB0aGUgYm90IOKUgOKU"
    "gCAqLwovKiBMSVZFIC8gTUUgLyBURyAvIElOSVQgYXJlIGRlY2xhcmVkIGF0IHRoZSB2ZXJ5IHRv"
    "cCBvZiB0aGlzIHNjcmlwdCAqLwphc3luYyBmdW5jdGlvbiBhcGkocGF0aCxib2R5KXsKICBjb25z"
    "dCBvPXtoZWFkZXJzOnsiQ29udGVudC1UeXBlIjoiYXBwbGljYXRpb24vanNvbiIsIlgtSW5pdC1E"
    "YXRhIjpJTklULAogICAgIlgtVGVsZWdyYW0tSW5pdC1EYXRhIjpJTklULCJYLUFwcC1LZXkiOkFQ"
    "UEtFWX19OwogIGlmKGJvZHkhPT11bmRlZmluZWQpe28ubWV0aG9kPSJQT1NUIjtvLmJvZHk9SlNP"
    "Ti5zdHJpbmdpZnkoYm9keSk7fQogIGNvbnN0IHI9YXdhaXQgZmV0Y2gocGF0aCxvKTsKICBsZXQg"
    "ZD1udWxsO3RyeXtkPWF3YWl0IHIuanNvbigpO31jYXRjaChlcnIpe30KICBpZighci5vayl7Y29u"
    "c3QgZT1uZXcgRXJyb3IoKGQmJmQuZXJyb3IpfHwocGF0aCsiICIrci5zdGF0dXMpKTtlLnN0YXR1"
    "cz1yLnN0YXR1cztlLmRhdGE9ZDt0aHJvdyBlO30KICByZXR1cm4gZDsKfQpmdW5jdGlvbiBhZG1p"
    "bkFsbG93ZWQoKXtyZXR1cm4gIUxJVkUgfHwgISEoTUUmJk1FLmFkbWluKTt9CmZ1bmN0aW9uIGFw"
    "cGx5U3RhdGUoZCl7CiAgLyogZXZlcnkgbGlzdCBpcyByZXBsYWNlZCwgZXZlbiB3aGVuIGVtcHR5"
    "IOKAlCBhIG5ldyBzaG9wIG11c3QgbmV2ZXIgc2hvdyBkZW1vIGRhdGEgKi8KICBpZihkLmZlYXR1"
    "cmVzKU9iamVjdC5rZXlzKGQuZmVhdHVyZXMpLmZvckVhY2goaz0+e2lmKGsgaW4gRkVBVClGRUFU"
    "W2tdPSEhZC5mZWF0dXJlc1trXTt9KTsKICBpZihkLmNvbnRlbnQpT2JqZWN0LmtleXMoZC5jb250"
    "ZW50KS5mb3JFYWNoKGs9PntpZihrIGluIENNUyYmZC5jb250ZW50W2tdIT1udWxsKUNNU1trXT1k"
    "LmNvbnRlbnRba107fSk7CiAgaWYoQXJyYXkuaXNBcnJheShkLnN0b2NrKSl7U1RPQ0subGVuZ3Ro"
    "PTA7ZC5zdG9jay5mb3JFYWNoKHg9PlNUT0NLLnB1c2goeCkpO0ZJTFRFUkVEPVNUT0NLLnNsaWNl"
    "KCk7fQogIGlmKEFycmF5LmlzQXJyYXkoZC5vcmRlcnMpKXtPUkRFUlMubGVuZ3RoPTA7ZC5vcmRl"
    "cnMuZm9yRWFjaCh4PT5PUkRFUlMucHVzaCh4KSk7fQogIGlmKEFycmF5LmlzQXJyYXkoZC50eCkp"
    "e1RYLmxlbmd0aD0wO2QudHguZm9yRWFjaCh4PT5UWC5wdXNoKHgpKTt9CiAgaWYoQXJyYXkuaXNB"
    "cnJheShkLmZlZWQpKUZFRUQ9ZC5mZWVkOwogIGlmKGQuZWFybilFQVJOPU9iamVjdC5hc3NpZ24o"
    "e30sRUFSTixkLmVhcm4pOwogIGlmKGQucHJlZnMpUFJFRlM9T2JqZWN0LmFzc2lnbih7fSxQUkVG"
    "UyxkLnByZWZzKTsKICBpZihBcnJheS5pc0FycmF5KGQuYWxlcnRzKSl3aW5kb3cuX19hbGVydHM9"
    "ZC5hbGVydHM7CiAgaWYoZC5zdG9yZSl7CiAgICBTVE9SRT1PYmplY3QuYXNzaWduKHt9LFNUT1JF"
    "LGQuc3RvcmUpOwogICAgaWYoZC5zdG9yZS5uYW1lKUNGRy5uYW1lPWQuc3RvcmUubmFtZTsKICAg"
    "IGlmKGQuc3RvcmUudGFnKUNGRy50YWc9ZC5zdG9yZS50YWc7CiAgICBkb2N1bWVudC5xdWVyeVNl"
    "bGVjdG9yQWxsKCJbZGF0YS1zdG9yZW5hbWVdIikuZm9yRWFjaChlPT5lLnRleHRDb250ZW50PUNG"
    "Ry5uYW1lKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXN0b3JldGFnXSIp"
    "LmZvckVhY2goZT0+ZS50ZXh0Q29udGVudD1DRkcudGFnKTsKICAgIGRvY3VtZW50LnRpdGxlPUNG"
    "Ry5uYW1lOwogIH0KICBpZihkLmFkbSl7CiAgICBBRE09T2JqZWN0LmFzc2lnbih7fSxBRE0sZC5h"
    "ZG0pOwogICAgaWYoQXJyYXkuaXNBcnJheShBRE0uY2hhbm5lbHMpKQogICAgICBDSEFOTkVMUz1B"
    "RE0uY2hhbm5lbHMubWFwKGM9Pih7aWQ6Yy5pZCxuYW1lOmMubmFtZSxoYW5kbGU6Yy5oYW5kbGV8"
    "fCIiLAogICAgICAgIHJlcTohIWMucmVxdWlyZWQsb246ISFjLmFjdGl2ZSxtZW1iZXJzOiLigJQi"
    "fSkpOwogICAgaWYoQURNLmxvZ3MpewogICAgICBMT0dTX01BU1RFUj0hIUFETS5sb2dzLm1hc3Rl"
    "cjsKICAgICAgaWYoQXJyYXkuaXNBcnJheShBRE0ubG9ncy5jaGFubmVscykpCiAgICAgICAgTE9H"
    "Q0g9QURNLmxvZ3MuY2hhbm5lbHMubWFwKGM9Pih7aWQ6Yy5pZCxuYW1lOmMubmFtZSxoYW5kbGU6"
    "IiIsCiAgICAgICAgICBwcmltYXJ5OiEhYy5wcmltYXJ5LG9uOiEhYy5hY3RpdmV9KSk7CiAgICAg"
    "IGlmKEFycmF5LmlzQXJyYXkoQURNLmxvZ3MuZXZlbnRzKSYmQURNLmxvZ3MuZXZlbnRzLmxlbmd0"
    "aCkKICAgICAgICBFVkVOVFM9QURNLmxvZ3MuZXZlbnRzLm1hcChlPT4oe2s6ZS5sYWJlbCxvbjoh"
    "IWUub24sY2g6KExPR0NIWzBdfHx7fSkuaWR8fDF9KSk7CiAgICB9CiAgfQogIGlmKGQucmF0aW5n"
    "cylSRVZJRVdTPWQucmF0aW5nczsKICBpZih0eXBlb2YgZC5iYWxhbmNlPT09Im51bWJlciIpQkFM"
    "PWQuYmFsYW5jZTsKICBpZihkLm1lKU1FPWQubWU7CiAgaWYoZC5jYXJ0KUNBUlQ9ZC5jYXJ0Owog"
    "IGlmKGQud2lzaGxpc3QpV0lTSD1kLndpc2hsaXN0Owp9CmZ1bmN0aW9uIHNob3dTd2l0Y2gob24p"
    "ewogIGNvbnN0IHN3PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdyIpOwogIGlmKHN3KXN3LnN0"
    "eWxlLmRpc3BsYXk9b24/ImZsZXgiOiJub25lIjsKfQp2YXIgTE9DS19USVRMRT0iIixMT0NLX01T"
    "Rz0iIjsKZnVuY3Rpb24gbG9ja2VkU2NyZWVuKHRpdGxlLG1zZyl7CiAgTE9DS0VEPXRydWU7TE9D"
    "S19USVRMRT10aXRsZTtMT0NLX01TRz1tc2c7CiAgc2hvd1N3aXRjaChmYWxzZSk7CiAgcGFpbnRM"
    "b2NrZWQoKTsKfQpmdW5jdGlvbiBwYWludExvY2tlZCgpewogIGNvbnN0IHN0PWRvY3VtZW50Lmdl"
    "dEVsZW1lbnRCeUlkKCJzdGFnZSIpOwogIGlmKHN0KXN0LmlubmVySFRNTD0nPGRpdiBjbGFzcz0i"
    "Y2FyZCIgc3R5bGU9Im1hcmdpbjoxOHB4O3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MjJweCI+"
    "JwogICAgKyc8aDMgc3R5bGU9Im1hcmdpbjowIDAgOHB4Ij4nK0xPQ0tfVElUTEUrJzwvaDM+Jwog"
    "ICAgKyc8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAgMCAxNHB4Ij4nK0xPQ0tfTVNHKyc8"
    "L3A+JwogICAgKyc8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIG9uY2xpY2s9ImxvY2F0aW9uLnJl"
    "bG9hZCgpIj5UcnkgYWdhaW48L2J1dHRvbj48L2Rpdj4nOwogIFsidGFicyIsImRvY2siXS5mb3JF"
    "YWNoKGlkPT57Y29uc3QgZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZChpZCk7aWYoZSllLnN0eWxl"
    "LmRpc3BsYXk9Im5vbmUiO30pOwp9CmFzeW5jIGZ1bmN0aW9uIGFwaVRyeShwYXRoLGJvZHkpewog"
    "IHRyeXtyZXR1cm4gYXdhaXQgYXBpKHBhdGgsYm9keSk7fQogIGNhdGNoKGUpe3RvYXN0KFN0cmlu"
    "ZyhlJiZlLm1lc3NhZ2V8fGUpLnNsaWNlKDAsOTApKTtyZXR1cm4gbnVsbDt9Cn0KYXN5bmMgZnVu"
    "Y3Rpb24gcmVmcmVzaCgpewogIGlmKCFMSVZFKXJldHVybjsKICB0cnl7CiAgICBhcHBseVN0YXRl"
    "KGF3YWl0IGFwaSgiL2FwaS9zdGF0ZSIpKTsKICAgIGFwcGx5Q01TKCk7YXBwbHlGZWF0dXJlcygp"
    "O3N5bmNCYWwoKTtyZW5kZXIoKTtyZW5kZXJBZG1pbigpOwogIH1jYXRjaChlKXt9Cn0KYXN5bmMg"
    "ZnVuY3Rpb24gY29weVRleHQodCwgbGFiZWwpewogIGNvbnN0IHR4dD1TdHJpbmcodHx8IiIpOwog"
    "IGlmKCF0eHQpe3RvYXN0KCJOb3RoaW5nIHRvIGNvcHkiKTtyZXR1cm4gZmFsc2U7fQogIHRyeXsK"
    "ICAgIGlmKG5hdmlnYXRvci5jbGlwYm9hcmQmJndpbmRvdy5pc1NlY3VyZUNvbnRleHQpe2F3YWl0"
    "IG5hdmlnYXRvci5jbGlwYm9hcmQud3JpdGVUZXh0KHR4dCk7fQogICAgZWxzZXsKICAgICAgY29u"
    "c3QgdGE9ZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgidGV4dGFyZWEiKTsKICAgICAgdGEudmFsdWU9"
    "dHh0O3RhLnNldEF0dHJpYnV0ZSgicmVhZG9ubHkiLCIiKTsKICAgICAgdGEuc3R5bGUuY3NzVGV4"
    "dD0icG9zaXRpb246Zml4ZWQ7bGVmdDotOTk5OXB4O29wYWNpdHk6MCI7CiAgICAgIGRvY3VtZW50"
    "LmJvZHkuYXBwZW5kQ2hpbGQodGEpO3RhLnNlbGVjdCgpO3RhLnNldFNlbGVjdGlvblJhbmdlKDAs"
    "dHh0Lmxlbmd0aCk7CiAgICAgIGNvbnN0IG9rPWRvY3VtZW50LmV4ZWNDb21tYW5kKCJjb3B5Iik7"
    "ZG9jdW1lbnQuYm9keS5yZW1vdmVDaGlsZCh0YSk7CiAgICAgIGlmKCFvayl0aHJvdyBuZXcgRXJy"
    "b3IoImNvcHkgcmVmdXNlZCIpOwogICAgfQogICAgdG9hc3QoKGxhYmVsfHwiQ29waWVkIikrIjog"
    "IisodHh0Lmxlbmd0aD4yNj90eHQuc2xpY2UoMCwyNikrIuKApiI6dHh0KSk7CiAgICByZXR1cm4g"
    "dHJ1ZTsKICB9Y2F0Y2goZSl7CiAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtm"
    "b250LXNpemU6MXJlbSI+Q29weSB0aGlzPC9oMz4KICAgICAgPHAgY2xhc3M9InN1YiI+WW91ciBi"
    "cm93c2VyIGJsb2NrZWQgdGhlIGNsaXBib2FyZCDigJQgc2VsZWN0IGFuZCBjb3B5LjwvcD4KICAg"
    "ICAgPHRleHRhcmVhIGNsYXNzPSJpbnAiIHJvd3M9IjMiIHJlYWRvbmx5IG9uY2xpY2s9InRoaXMu"
    "c2VsZWN0KCkiPiR7ZXNjKHR4dCl9PC90ZXh0YXJlYT4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4IiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRvbj5g"
    "KTsKICAgIHJldHVybiBmYWxzZTsKICB9Cn0KbGV0IFNUT1JFPXt1cGk6IiIscGF5ZWU6IiIscHJl"
    "Zml4OiJQQVkiLHRlcm1zOiIiLHN1cHBvcnQ6IiJ9OwpsZXQgUFJFRlM9e3Jlc3RvY2s6dHJ1ZSxy"
    "ZWNlaXB0czp0cnVlLHByb21vczp0cnVlLHB1c2g6dHJ1ZX07CmxldCBPRkZFUj0iIjsKZnVuY3Rp"
    "b24gb3BlblBheShsaW5rKXsKICBpZighbGluaylyZXR1cm47CiAgdHJ5ewogICAgaWYoVEcmJlRH"
    "Lm9wZW5MaW5rJiYvXmh0dHBzPzovLnRlc3QobGluaykpVEcub3BlbkxpbmsobGluayk7CiAgICBl"
    "bHNlIHdpbmRvdy5sb2NhdGlvbi5ocmVmPWxpbms7CiAgfWNhdGNoKGUpe3dpbmRvdy5sb2NhdGlv"
    "bi5ocmVmPWxpbms7fQp9CmFzeW5jIGZ1bmN0aW9uIGJvb3QoKXsKICBpZihURyl7dHJ5e1RHLnJl"
    "YWR5KCk7VEcuZXhwYW5kKCk7fWNhdGNoKGUpe319CiAgY29uc3QgaW5URz0hISgoSU5JVCYmSU5J"
    "VC5sZW5ndGgpfHwoQVBQS0VZJiZBUFBLRVkubGVuZ3RoKSk7CiAgaWYoaW5URylzaG93U3dpdGNo"
    "KGZhbHNlKTsgICAgICAgICAgICAgIC8qIGZhaWwgY2xvc2VkOiBubyBhZG1pbiBzdHJpcCB1bnRp"
    "bCBwcm92ZW4gKi8KICBpZihsb2NhdGlvbi5wcm90b2NvbD09PSJmaWxlOiIpcmV0dXJuOyAgLyog"
    "b3BlbmVkIGFzIGEgcGxhaW4gZmlsZSDigJQgZGVtbyBnYWxsZXJ5ICovCiAgbGV0IGQ7CiAgdHJ5"
    "ewogICAgZD1hd2FpdCBhcGkoIi9hcGkvc3RhdGUiKTsKICB9Y2F0Y2goZSl7CiAgICBpZihlJiZl"
    "LnN0YXR1cz09PTQwMSl7CiAgICAgIGxldCB3aHk9IiIsZml4PSIiOwogICAgICB0cnl7CiAgICAg"
    "ICAgY29uc3Qgdz1hd2FpdCAoYXdhaXQgZmV0Y2goIi9hcGkvd2hvYW1pIix7aGVhZGVyczp7Ilgt"
    "SW5pdC1EYXRhIjpJTklULAogICAgICAgICAgIlgtVGVsZWdyYW0tSW5pdC1EYXRhIjpJTklULCJY"
    "LUFwcC1LZXkiOkFQUEtFWX19KSkuanNvbigpOwogICAgICAgIGNvbnN0IFI9e21pc3Npbmc6WyJU"
    "ZWxlZ3JhbSBkaWQgbm90IHBhc3MgeW91ciBhY2NvdW50IHRvIHRoaXMgcGFnZS4iLAogICAgICAg"
    "ICAgICAgICAgICAgIk9wZW4gdGhlIGJvdCBhbmQgdGFwIDxiPk9wZW4gU3RvcmU8L2I+LCBvciBz"
    "ZW5kIDxiPi9hcHA8L2I+LiJdLAogICAgICAgICAgICAgICAgICJiYWQtZm9ybWF0IjpbIlRoZSBz"
    "aWduLWluIGRhdGEgYXJyaXZlZCBkYW1hZ2VkLiIsIkNsb3NlIHRoZSBhcHAgYW5kIG9wZW4gaXQg"
    "YWdhaW4gZnJvbSB0aGUgYm90LiJdLAogICAgICAgICAgICAgICAgICJuby1oYXNoIjpbIlRoZSBz"
    "aWduLWluIGRhdGEgYXJyaXZlZCBpbmNvbXBsZXRlLiIsIkNsb3NlIHRoZSBhcHAgYW5kIG9wZW4g"
    "aXQgYWdhaW4gZnJvbSB0aGUgYm90LiJdLAogICAgICAgICAgICAgICAgICJiYWQtaGFzaCI6WyJU"
    "aGUgc2lnbmF0dXJlIGRvZXMgbm90IG1hdGNoIHRoaXMgYm90J3MgdG9rZW4uIiwKICAgICAgICAg"
    "ICAgICAgICAgICJUaGUgcGFnZSBpcyBzZXJ2ZWQgYnkgYSBkaWZmZXJlbnQgYm90LCBvciBCT1Rf"
    "VE9LRU4gb24gdGhlIHNlcnZlciBoYXMgcXVvdGVzIGFyb3VuZCBpdC4gU2VuZCA8Yj4vZGlhZzwv"
    "Yj4gaW4gdGhlIGJvdCB0byBjb21wYXJlLiJdLAogICAgICAgICAgICAgICAgIHN0YWxlOlsiVGhh"
    "dCBzaWduLWluIGlzIG9sZGVyIHRoYW4gdGhlIGFsbG93ZWQgd2luZG93LiIsIlJlb3BlbiB0aGUg"
    "YXBwIGZyb20gdGhlIGJvdC4iXSwKICAgICAgICAgICAgICAgICAiYmFkLXVzZXIiOlsiWW91ciBh"
    "Y2NvdW50IGRldGFpbHMgY291bGQgbm90IGJlIHJlYWQuIiwiUmVvcGVuIHRoZSBhcHAgZnJvbSB0"
    "aGUgYm90LiJdLAogICAgICAgICAgICAgICAgICJuby1pZCI6WyJUZWxlZ3JhbSBzZW50IG5vIHVz"
    "ZXIgaWQuIiwiUmVvcGVuIHRoZSBhcHAgZnJvbSB0aGUgYm90LiJdfTsKICAgICAgICBjb25zdCBy"
    "PVJbdy53aHldfHxbIlRoZSBzdG9yZSBjb3VsZCBub3QgY29uZmlybSB3aG8geW91IGFyZS4iLCJP"
    "cGVuIGl0IGFnYWluIGZyb20gdGhlIGJvdC4iXTsKICAgICAgICB3aHk9clswXSsiIDxjb2RlPigi"
    "Kyh3LndoeXx8InVua25vd24iKSsiKTwvY29kZT4iOwogICAgICAgIGZpeD1yWzFdOwogICAgICB9"
    "Y2F0Y2goZXJyKXsKICAgICAgICB3aHk9IlRoZSBzdG9yZSBjb3VsZCBub3QgY29uZmlybSB3aG8g"
    "eW91IGFyZS4iOwogICAgICAgIGZpeD0iT3BlbiBpdCBhZ2FpbiBmcm9tIHRoZSBib3Qgd2l0aCB0"
    "aGUgPGI+T3BlbiBTdG9yZTwvYj4gYnV0dG9uLiI7CiAgICAgIH0KICAgICAgbG9ja2VkU2NyZWVu"
    "KCJDYW5ub3Qgb3BlbiB0aGUgc3RvcmUiLHdoeSsiPGJyPjxicj4iK2ZpeCk7CiAgICB9CiAgICBy"
    "ZXR1cm47CiAgfQogIGFwcGx5U3RhdGUoZCk7TElWRT10cnVlOwogIHNob3dTd2l0Y2goISEoTUUm"
    "Jk1FLmFkbWluKSk7ICAgICAgICAgICAvKiBvbmx5IGEgdmVyaWZpZWQgYWRtaW4gc2VlcyBpdCAq"
    "LwogIGFwcGx5Q01TKCk7YXBwbHlGZWF0dXJlcygpO3N5bmNCYWwoKTtyZW5kZXIoKTtyZW5kZXJB"
    "ZG1pbigpOwp9CmFzeW5jIGZ1bmN0aW9uIHB1c2gocGF0aCxib2R5LG9rTXNnKXsKICBpZighTElW"
    "RSlyZXR1cm4gdHJ1ZTsKICB0cnl7Y29uc3Qgcj1hd2FpdCBhcGkocGF0aCxib2R5KTtpZihva01z"
    "Zyl0b2FzdChva01zZyk7cmV0dXJuIHI7fQogIGNhdGNoKGUpe3RvYXN0KCJDb3VsZCBub3Qgc2F2"
    "ZSDigJQgY2hlY2sgeW91ciBjb25uZWN0aW9uIik7cmV0dXJuIGZhbHNlO30KfQpib290KCk7Cjwv"
    "c2NyaXB0Pgo8L2JvZHk+PC9odG1sPgo="
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
