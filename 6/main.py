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
DESIGN = "6-midnight"


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
    "store_tagline": "Quiet and precise",
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
    "storeTag": "Quiet and precise",
    "tick1": "<b>Instant delivery</b> on every country",
    "tick2": "Wallet top-ups <b>auto-verified</b>",
    "tick3": "Failed login? <b>Auto refund</b>",
    "tick4": "Daily check-in <b>bonus</b>",
    "heroKick": "Stock feed",
    "heroH1": "Less noise.<br>More delivery.",
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
    "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIiBkYXRhLWRlc2lnbj0ibWlkbmlnaHQiPgo8"
    "aGVhZD4KPG1ldGEgY2hhcnNldD0idXRmLTgiPgo8bWV0YSBuYW1lPSJ2aWV3cG9ydCIgY29udGVu"
    "dD0id2lkdGg9ZGV2aWNlLXdpZHRoLGluaXRpYWwtc2NhbGU9MSx2aWV3cG9ydC1maXQ9Y292ZXIs"
    "bWF4aW11bS1zY2FsZT0xIj4KPHNjcmlwdCBzcmM9Imh0dHBzOi8vdGVsZWdyYW0ub3JnL2pzL3Rl"
    "bGVncmFtLXdlYi1hcHAuanMiPjwvc2NyaXB0Pgo8dGl0bGU+Wm9ybyBURyBTdG9yZSDigJQgU3Rv"
    "cmUgTWluaSBBcHAgKERlc2lnbiA2KTwvdGl0bGU+CjxzdHlsZT4KOnJvb3R7CiAgLS1iZzojMGYx"
    "MTEzOyAtLWJnMjojMTYxOTFjOyAtLWNhcmQ6IzE2MTkxYzsgLS1jYXJkMjojMWQyMTI1OwogIC0t"
    "bGluZTojMjYyYjMwOyAtLXR4OiNlOWVkZjE7IC0tdHgyOiM4MjhiOTU7IC0tYWNjOiM3ZGQzZmM7"
    "IC0tYWNjMjojN2RkM2ZjOwogIC0tb2s6IzRhZGU4MDsgLS13YXJuOiNmYmJmMjQ7IC0tYmFkOiNm"
    "ODcxNzE7CiAgLS1yOjEycHg7IC0tcnM6MTBweDsgLS1idG5oOjQ0cHg7IC0tZm9udDotYXBwbGUt"
    "c3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCwnU2Vnb2UgVUknLFJvYm90byxzYW5zLXNlcmlmOyAt"
    "LWZoZWFkOi1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LCdTZWdvZSBVSScsUm9ib3Rv"
    "LHNhbnMtc2VyaWY7CiAgLS1zaGFkb3c6MCA2cHggMThweCByZ2JhKDAsMCwwLC4zNSk7IC0tcGls"
    "bGdyYWQ6bGluZWFyLWdyYWRpZW50KDEzNWRlZywjZTllZGYxLCM5ZmIzYzgpOyAtLWJ0bmJnOiNj"
    "ZmQ4ZTA7Cn0KKntib3gtc2l6aW5nOmJvcmRlci1ib3g7LXdlYmtpdC10YXAtaGlnaGxpZ2h0LWNv"
    "bG9yOnRyYW5zcGFyZW50fQpodG1sLGJvZHl7bWFyZ2luOjA7cGFkZGluZzowfQpib2R5e2JhY2tn"
    "cm91bmQ6dmFyKC0tYmcpO2NvbG9yOnZhcigtLXR4KTtmb250LWZhbWlseTp2YXIoLS1mb250KTtm"
    "b250LXNpemU6MTVweDtsaW5lLWhlaWdodDoxLjQ1O21pbi1oZWlnaHQ6MTAwdmg7b3ZlcmZsb3ct"
    "eDpoaWRkZW47cGFkZGluZy10b3A6MzJweDt9CmJvZHk6YmVmb3Jle2NvbnRlbnQ6IiI7cG9zaXRp"
    "b246Zml4ZWQ7aW5zZXQ6MDtwb2ludGVyLWV2ZW50czpub25lO3otaW5kZXg6MDt9Cjo6LXdlYmtp"
    "dC1zY3JvbGxiYXJ7d2lkdGg6MDtoZWlnaHQ6MH0KI3NoZWxse3Bvc2l0aW9uOnJlbGF0aXZlO3ot"
    "aW5kZXg6MTttYXgtd2lkdGg6NTIwcHg7bWFyZ2luOjAgYXV0bzttaW4taGVpZ2h0OjEwMHZoO3Bh"
    "ZGRpbmc6MCAxNnB4IDk4cHh9Ci5oaWRkZW57ZGlzcGxheTpub25lIWltcG9ydGFudH0KCiN0b3Bi"
    "YXJ7cG9zaXRpb246c3RpY2t5O3RvcDozMnB4O3otaW5kZXg6NDA7ZGlzcGxheTpmbGV4O2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjE0cHggMCAxMnB4O2JhY2tncm91bmQ6bGlu"
    "ZWFyLWdyYWRpZW50KHZhcigtLWJnKSA3MiUsdHJhbnNwYXJlbnQpfQoubG9nb3t3aWR0aDozOHB4"
    "O2hlaWdodDozOHB4O2JvcmRlci1yYWRpdXM6MTJweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6"
    "Y2VudGVyO2ZsZXg6MCAwIGF1dG87YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzBm"
    "MTExMztmb250LXdlaWdodDo4MDA7Zm9udC1zaXplOi45cmVtO2xldHRlci1zcGFjaW5nOi0uMDNl"
    "bTt9Ci5icmFuZHttaW4td2lkdGg6MDtmbGV4OjF9Ci5icmFuZCBie2Rpc3BsYXk6YmxvY2s7Zm9u"
    "dC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZTouOTVyZW07Zm9udC13ZWlnaHQ6NzAwO2xl"
    "dHRlci1zcGFjaW5nOi0uMDJlbTt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3Rl"
    "eHQtb3ZlcmZsb3c6ZWxsaXBzaXN9Ci5icmFuZCBzcGFue2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXpl"
    "Oi42NnJlbTtjb2xvcjp2YXIoLS10eDIpO2xldHRlci1zcGFjaW5nOi4wNmVtO3RleHQtdHJhbnNm"
    "b3JtOnVwcGVyY2FzZX0KLmJhbGNoaXB7ZGlzcGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpj"
    "ZW50ZXI7Z2FwOjdweDtoZWlnaHQ6MzhweDtwYWRkaW5nOjAgMTNweDtib3JkZXItcmFkaXVzOjEy"
    "cHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2Nv"
    "bG9yOnZhcigtLXR4KTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXdlaWdodDo3MDA7Zm9u"
    "dC1zaXplOi44NXJlbTt3aGl0ZS1zcGFjZTpub3dyYXA7Y3Vyc29yOnBvaW50ZXI7fQouYmFsY2hp"
    "cCAucGx1c3tkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO3dpZHRoOjIwcHg7aGVpZ2h0"
    "OjIwcHg7Ym9yZGVyLXJhZGl1czo3cHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6"
    "IzBmMTExMztmb250LXdlaWdodDo4MDA7Zm9udC1zaXplOi44cmVtO2xpbmUtaGVpZ2h0OjF9Cgou"
    "Y2FyZHtiYWNrZ3JvdW5kOnZhcigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7"
    "Ym9yZGVyLXJhZGl1czp2YXIoLS1yKTtwYWRkaW5nOjE2cHg7bWFyZ2luLWJvdHRvbToxMnB4O2Jv"
    "eC1zaGFkb3c6dmFyKC0tc2hhZG93KTt9Ci5jYXJkLnRpZ2h0e3BhZGRpbmc6MTNweH0KLmNhcmQg"
    "aDN7bWFyZ2luOjAgMCAzcHg7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZTouOTJy"
    "ZW07Zm9udC13ZWlnaHQ6NzAwO2xldHRlci1zcGFjaW5nOi0uMDJlbX0KLmNhcmQgLnN1YnttYXJn"
    "aW46MDtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNzhyZW19Ci5zZWN7ZGlzcGxheTpmbGV4"
    "O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjtnYXA6MTBw"
    "eDttYXJnaW46MThweCAycHggMTBweH0KLnNlYyBoMnttYXJnaW46MDtmb250LWZhbWlseTp2YXIo"
    "LS1maGVhZCk7Zm9udC1zaXplOi44MnJlbTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6"
    "LjE0ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2NvbG9yOnZhcigtLXR4Mil9Ci5zZWMgYXtm"
    "b250LXNpemU6Ljc1cmVtO2NvbG9yOnZhcigtLWFjYyk7dGV4dC1kZWNvcmF0aW9uOm5vbmU7Zm9u"
    "dC13ZWlnaHQ6NjAwO3doaXRlLXNwYWNlOm5vd3JhcDtjdXJzb3I6cG9pbnRlcn0KCi8qIG9uZSBi"
    "dXR0b24gc3lzdGVtIOKAlCB0ZXh0IGNhbiBuZXZlciBvdmVyZmxvdyAqLwouYnRue2Rpc3BsYXk6"
    "aW5saW5lLWZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Z2Fw"
    "OjdweDtoZWlnaHQ6dmFyKC0tYnRuaCk7bWluLXdpZHRoOjA7bWF4LXdpZHRoOjEwMCU7cGFkZGlu"
    "ZzowIDE2cHg7Ym9yZGVyOjA7Y3Vyc29yOnBvaW50ZXI7Ym9yZGVyLXJhZGl1czoxMHB4O2ZvbnQt"
    "ZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouODNyZW07Zm9udC13ZWlnaHQ6NzAwO2xldHRl"
    "ci1zcGFjaW5nOi4wMWVtO3doaXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4dC1v"
    "dmVyZmxvdzplbGxpcHNpcztsaW5lLWhlaWdodDoxO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQp"
    "O2NvbG9yOiMwZjExMTM7dHJhbnNpdGlvbjp0cmFuc2Zvcm0gLjE1cyxmaWx0ZXIgLjE1czt9Ci5i"
    "dG46YWN0aXZle3RyYW5zZm9ybTpzY2FsZSguOTcpfQouYnRuOmhvdmVye2ZpbHRlcjpicmlnaHRu"
    "ZXNzKDEuMDYpfQouYnRuIHN2Z3t3aWR0aDoxNnB4O2hlaWdodDoxNnB4O2ZsZXg6MCAwIGF1dG99"
    "Ci5idG4uYmxvY2t7ZGlzcGxheTpmbGV4O3dpZHRoOjEwMCV9Ci5idG4uc217aGVpZ2h0OjM2cHg7"
    "cGFkZGluZzowIDEycHg7Zm9udC1zaXplOi43NXJlbTtib3JkZXItcmFkaXVzOjhweH0KLmJ0bi54"
    "c3toZWlnaHQ6MzBweDtwYWRkaW5nOjAgMTBweDtmb250LXNpemU6LjdyZW07Ym9yZGVyLXJhZGl1"
    "czo4cHh9Ci5idG4uZ2hvc3R7YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjb2xvcjp2YXIoLS10eCk7"
    "Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKX0KLmJ0bi5zb2Z0e2JhY2tncm91bmQ6dmFyKC0t"
    "Y2FyZDIpO2NvbG9yOnZhcigtLXR4KTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouYnRu"
    "LmJhZHtiYWNrZ3JvdW5kOnRyYW5zcGFyZW50O2NvbG9yOnZhcigtLWJhZCk7Ym9yZGVyOjFweCBz"
    "b2xpZCB2YXIoLS1iYWQpfQouYnRuW2Rpc2FibGVkXXtvcGFjaXR5Oi40NTtwb2ludGVyLWV2ZW50"
    "czpub25lfQouYnRucm93e2Rpc3BsYXk6ZmxleDtnYXA6OHB4fQouYnRucm93Pi5idG57ZmxleDox"
    "IDEgMDttaW4td2lkdGg6MH0KCi5waWxse2Rpc3BsYXk6aW5saW5lLWZsZXg7YWxpZ24taXRlbXM6"
    "Y2VudGVyO2dhcDo1cHg7aGVpZ2h0OjI0cHg7cGFkZGluZzowIDlweDtib3JkZXItcmFkaXVzOjk5"
    "cHg7Zm9udC1zaXplOi42NnJlbTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LjA0ZW07"
    "YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgyKTtib3JkZXI6MXB4IHNvbGlk"
    "IHZhcigtLWxpbmUpO3doaXRlLXNwYWNlOm5vd3JhcH0KLnBpbGwub257Y29sb3I6dmFyKC0tb2sp"
    "O2JvcmRlci1jb2xvcjpyZ2JhKDc0LDIyMiwxMjgsLjQpfQoucGlsbC5ob3R7Y29sb3I6dmFyKC0t"
    "d2Fybik7Ym9yZGVyLWNvbG9yOnJnYmEoMjUxLDE5MSwzNiwuNCl9Ci5waWxsLm9mZntjb2xvcjp2"
    "YXIoLS1iYWQpO2JvcmRlci1jb2xvcjpyZ2JhKDI0OCwxMTMsMTEzLC40KX0KLmRvdHt3aWR0aDo3"
    "cHg7aGVpZ2h0OjdweDtib3JkZXItcmFkaXVzOjUwJTtiYWNrZ3JvdW5kOnZhcigtLW9rKTtib3gt"
    "c2hhZG93OjAgMCA4cHggdmFyKC0tb2spfQoua3Z7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNl"
    "bnRlcjtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjtnYXA6MTJweDtwYWRkaW5nOjEwcHgg"
    "MDtib3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIoLS1saW5lKTtmb250LXNpemU6LjgycmVtO2Nv"
    "bG9yOnZhcigtLXR4Mil9Ci5rdjpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206MH0KLmt2IGJ7Y29s"
    "b3I6dmFyKC0tdHgpO2ZvbnQtd2VpZ2h0OjcwMDt0ZXh0LWFsaWduOnJpZ2h0O292ZXJmbG93LXdy"
    "YXA6YW55d2hlcmV9Ci5mbGFne3dpZHRoOjQycHg7aGVpZ2h0OjQycHg7Ym9yZGVyLXJhZGl1czox"
    "MnB4O2Rpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9udC1zaXplOjEuMzVyZW07Zmxl"
    "eDowIDAgYXV0bztiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigt"
    "LWxpbmUpfQouZmxhZyBzdmd7d2lkdGg6MThweDtoZWlnaHQ6MThweH0KCi5oZXJve3Bvc2l0aW9u"
    "OnJlbGF0aXZlO292ZXJmbG93OmhpZGRlbjtib3JkZXItcmFkaXVzOjE0cHg7cGFkZGluZzoyMnB4"
    "IDE4cHg7bWFyZ2luOjRweCAwIDE0cHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNr"
    "Z3JvdW5kOmxpbmVhci1ncmFkaWVudCgxNTBkZWcsIzFiMjAyNSwjMTIxNTE4KX0KLmhlcm8gLmtp"
    "Y2t7Zm9udC1zaXplOi42cmVtO2xldHRlci1zcGFjaW5nOi4yZW07dGV4dC10cmFuc2Zvcm06dXBw"
    "ZXJjYXNlO2NvbG9yOnZhcigtLWFjYyk7Zm9udC13ZWlnaHQ6ODAwfQouaGVybyBoMXttYXJnaW46"
    "OHB4IDAgNnB4O2ZvbnQtZmFtaWx5OnZhcigtLWZoZWFkKTtmb250LXNpemU6MS43NXJlbTtsaW5l"
    "LWhlaWdodDoxLjA1O2xldHRlci1zcGFjaW5nOi0uMDJlbTtmb250LXdlaWdodDo3MDB9Ci5oZXJv"
    "IHB7bWFyZ2luOjAgMCAxNHB4O2ZvbnQtc2l6ZTouOHJlbTtjb2xvcjp2YXIoLS10eDIpO21heC13"
    "aWR0aDoyODBweH0KLmhlcm8gLmJ0bnJvd3ttYXgtd2lkdGg6MzMwcHh9CgouZ3JpZHtkaXNwbGF5"
    "OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjFmciAxZnI7Z2FwOjEwcHh9Ci5wY2FyZHtiYWNr"
    "Z3JvdW5kOnZhcigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJh"
    "ZGl1czp2YXIoLS1yKTtwYWRkaW5nOjE0cHg7Ym94LXNoYWRvdzp2YXIoLS1zaGFkb3cpO2Rpc3Bs"
    "YXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47Z2FwOjlweDtjdXJzb3I6cG9pbnRlcjt9Ci5w"
    "Y2FyZCAudG9we2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50"
    "OnNwYWNlLWJldHdlZW47Z2FwOjhweH0KLnBjYXJkIC5ubXtmb250LXdlaWdodDo3MDA7Zm9udC1z"
    "aXplOi44NXJlbTt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZs"
    "b3c6ZWxsaXBzaXN9Ci5wY2FyZCAubWV0YXtmb250LXNpemU6LjY4cmVtO2NvbG9yOnZhcigtLXR4"
    "Mil9Ci5wY2FyZCAucHJpY2V7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZToxLjI1"
    "cmVtO2ZvbnQtd2VpZ2h0OjcwMDtsZXR0ZXItc3BhY2luZzotLjAyZW19Ci5wY2FyZCAucHJpY2Ug"
    "c3tmb250LXNpemU6LjcycmVtO2NvbG9yOnZhcigtLXR4Mik7Zm9udC13ZWlnaHQ6NDAwO21hcmdp"
    "bi1yaWdodDo2cHh9Ci5yb3dze2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47Z2Fw"
    "OjlweH0KLnJvd3tkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMnB4O2JhY2tn"
    "cm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFk"
    "aXVzOnZhcigtLXIpO3BhZGRpbmc6MTJweCAxM3B4O2JveC1zaGFkb3c6dmFyKC0tc2hhZG93KTtj"
    "dXJzb3I6cG9pbnRlcjt0cmFuc2l0aW9uOnRyYW5zZm9ybSAuMTVzO30KLnJvdzphY3RpdmV7dHJh"
    "bnNmb3JtOnNjYWxlKC45OSl9Ci5yb3cgLm1pZHtmbGV4OjE7bWluLXdpZHRoOjB9Ci5yb3cgLm1p"
    "ZCBie2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi44OHJlbTt3aGl0ZS1zcGFjZTpub3dyYXA7b3Zl"
    "cmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXN9Ci5yb3cgLm1pZCBzbWFsbHtkaXNw"
    "bGF5OmJsb2NrO2ZvbnQtc2l6ZTouN3JlbTtjb2xvcjp2YXIoLS10eDIpO21hcmdpbi10b3A6MnB4"
    "fQoucm93IC5ydHt0ZXh0LWFsaWduOnJpZ2h0O2ZsZXg6MCAwIGF1dG99Ci5yb3cgLnJ0IC5we2Zv"
    "bnQtd2VpZ2h0OjgwMDtmb250LXNpemU6Ljk1cmVtO3doaXRlLXNwYWNlOm5vd3JhcH0KLnJvdyAu"
    "cnQgLnAgc3tmb250LXdlaWdodDo0MDA7Zm9udC1zaXplOi43MnJlbTtjb2xvcjp2YXIoLS10eDIp"
    "O21hcmdpbi1yaWdodDo1cHh9Ci5jYXJvdXtkaXNwbGF5OmZsZXg7Z2FwOjEwcHg7b3ZlcmZsb3ct"
    "eDphdXRvO3BhZGRpbmc6MnB4IDAgNnB4O21hcmdpbjowIC0xNnB4IDZweDtwYWRkaW5nLWxlZnQ6"
    "MTZweDtwYWRkaW5nLXJpZ2h0OjE2cHg7c2Nyb2xsLXNuYXAtdHlwZTp4IG1hbmRhdG9yeX0KLmNh"
    "cm91Pip7ZmxleDowIDAgMTcycHg7c2Nyb2xsLXNuYXAtYWxpZ246c3RhcnR9CgpuYXYjdGFic3tw"
    "b3NpdGlvbjpmaXhlZDt6LWluZGV4OjYwO2xlZnQ6NTAlO2JvdHRvbTpjYWxjKDEycHggKyBlbnYo"
    "c2FmZS1hcmVhLWluc2V0LWJvdHRvbSkpO3RyYW5zZm9ybTp0cmFuc2xhdGVYKC01MCUpO21heC13"
    "aWR0aDpjYWxjKDEwMHZ3IC0gMjBweCl9Cm5hdiN0YWJzIC5kb2Nre2Rpc3BsYXk6ZmxleDtnYXA6"
    "MnB4O3BhZGRpbmc6N3B4IDhweDtib3JkZXItcmFkaXVzOjBweDtiYWNrZ3JvdW5kOiMxNjE5MWM7"
    "Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3gtc2hhZG93OjAgNnB4IDIycHggcmdiYSgw"
    "LDAsMCwuNDUpfQpuYXYjdGFicyBidXR0b257cG9zaXRpb246cmVsYXRpdmU7ZGlzcGxheTpmbGV4"
    "O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50"
    "OmNlbnRlcjtnYXA6NHB4O21pbi13aWR0aDo1OHB4O2hlaWdodDo0OHB4O3BhZGRpbmc6MCA2cHg7"
    "Ym9yZGVyOjA7YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjdXJzb3I6cG9pbnRlcjtjb2xvcjp2YXIo"
    "LS10eDIpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouNnJlbTtmb250LXdlaWdo"
    "dDo3MDA7bGV0dGVyLXNwYWNpbmc6LjAzZW07Ym9yZGVyLXJhZGl1czo5cHg7dHJhbnNpdGlvbjpj"
    "b2xvciAuMnMsYmFja2dyb3VuZCAuMnM7d2hpdGUtc3BhY2U6bm93cmFwfQpuYXYjdGFicyBidXR0"
    "b24gc3Zne3dpZHRoOjE5cHg7aGVpZ2h0OjE5cHh9Cm5hdiN0YWJzIGJ1dHRvbi5vbntjb2xvcjoj"
    "MGYxMTEzO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpfQoKLnN1Ym5hdntkaXNwbGF5OmZsZXg7"
    "Z2FwOjdweDtvdmVyZmxvdy14OmF1dG87cGFkZGluZzoycHggMCAxMnB4O21hcmdpbjowIC0ycHh9"
    "Ci5zdWJuYXYgYnV0dG9ue2ZsZXg6MCAwIGF1dG87aGVpZ2h0OjM0cHg7cGFkZGluZzowIDEzcHg7"
    "Ym9yZGVyLXJhZGl1czo4cHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZ3JvdW5k"
    "OnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2Zv"
    "bnQtc2l6ZTouNzRyZW07Zm9udC13ZWlnaHQ6NzAwO2N1cnNvcjpwb2ludGVyO3doaXRlLXNwYWNl"
    "Om5vd3JhcH0KLnN1Ym5hdiBidXR0b24ub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29s"
    "b3I6IzBmMTExMztib3JkZXItY29sb3I6dHJhbnNwYXJlbnR9Ci5rcGlze2Rpc3BsYXk6Z3JpZDtn"
    "cmlkLXRlbXBsYXRlLWNvbHVtbnM6MWZyIDFmcjtnYXA6MTBweDttYXJnaW4tYm90dG9tOjEycHh9"
    "Ci5rcGl7YmFja2dyb3VuZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUp"
    "O2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO3BhZGRpbmc6MTNweDtib3gtc2hhZG93OnZhcigtLXNo"
    "YWRvdyl9Ci5rcGkgc21hbGx7ZGlzcGxheTpibG9jaztmb250LXNpemU6LjY0cmVtO2NvbG9yOnZh"
    "cigtLXR4Mik7bGV0dGVyLXNwYWNpbmc6LjFlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2V9Ci5r"
    "cGkgYntkaXNwbGF5OmJsb2NrO21hcmdpbi10b3A6NXB4O2ZvbnQtZmFtaWx5OnZhcigtLWZoZWFk"
    "KTtmb250LXNpemU6MS4zNXJlbTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LS4wMmVt"
    "fQouaW5wLHNlbGVjdC5pbnB7d2lkdGg6MTAwJTtoZWlnaHQ6dmFyKC0tYnRuaCk7cGFkZGluZzow"
    "IDEzcHg7Ym9yZGVyLXJhZGl1czoxMHB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7YmFj"
    "a2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgpO2ZvbnQtZmFtaWx5OnZhcigtLWZv"
    "bnQpO2ZvbnQtc2l6ZTouODNyZW07b3V0bGluZTpub25lOy13ZWJraXQtYXBwZWFyYW5jZTpub25l"
    "O2FwcGVhcmFuY2U6bm9uZX0Kc2VsZWN0LmlucHtiYWNrZ3JvdW5kLWltYWdlOm5vbmV9Ci5pbnA6"
    "OnBsYWNlaG9sZGVye2NvbG9yOnZhcigtLXR4Mik7b3BhY2l0eTouNzV9Ci5pbnA6Zm9jdXN7Ym9y"
    "ZGVyLWNvbG9yOnZhcigtLWFjYyl9CnRleHRhcmVhLmlucHtoZWlnaHQ6YXV0bzttaW4taGVpZ2h0"
    "OjQwcHg7cGFkZGluZzoxMHB4IDEzcHg7bGluZS1oZWlnaHQ6MS41O3Jlc2l6ZTp2ZXJ0aWNhbH0K"
    "LmNtc3Jvdy5oaWRle2Rpc3BsYXk6bm9uZX0KLmljb25jaGlwe3Bvc2l0aW9uOnJlbGF0aXZlO2hl"
    "aWdodDozNHB4O21pbi13aWR0aDozNHB4O3BhZGRpbmc6MCA5cHg7Ym9yZGVyLXJhZGl1czo5OXB4"
    "O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29s"
    "b3I6dmFyKC0tdHgpO2Rpc3BsYXk6aW5saW5lLWdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2N1cnNv"
    "cjpwb2ludGVyfQouaWNvbmNoaXAgc3Zne3dpZHRoOjE3cHg7aGVpZ2h0OjE3cHh9Ci5pY29uY2hp"
    "cCBpe3Bvc2l0aW9uOmFic29sdXRlO3RvcDotNXB4O3JpZ2h0Oi01cHg7bWluLXdpZHRoOjE3cHg7"
    "aGVpZ2h0OjE3cHg7cGFkZGluZzowIDRweDtib3JkZXItcmFkaXVzOjk5cHg7YmFja2dyb3VuZDp2"
    "YXIoLS1hY2MpO2NvbG9yOiMwZjExMTM7Zm9udC1zaXplOi42cmVtO2ZvbnQtc3R5bGU6bm9ybWFs"
    "O2ZvbnQtd2VpZ2h0OjgwMDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyfQouc3RhcnN7"
    "ZGlzcGxheTppbmxpbmUtZmxleDtnYXA6MXB4O3ZlcnRpY2FsLWFsaWduOm1pZGRsZX0KLnN0YXJz"
    "IHN2Z3t3aWR0aDoxMXB4O2hlaWdodDoxMXB4O2NvbG9yOiNmNWE2MjN9Ci5zdGFycyBie2ZvbnQt"
    "c2l6ZTouNjZyZW07Y29sb3I6dmFyKC0tdHgyKTtmb250LXdlaWdodDo3MDA7bWFyZ2luLWxlZnQ6"
    "M3B4fQouaGVhcnR7cG9zaXRpb246YWJzb2x1dGU7dG9wOjhweDtyaWdodDo4cHg7d2lkdGg6MjZw"
    "eDtoZWlnaHQ6MjZweDtib3JkZXItcmFkaXVzOjk5cHg7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1z"
    "OmNlbnRlcjtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6dmFyKC0tY2Fy"
    "ZDIpO2N1cnNvcjpwb2ludGVyO3otaW5kZXg6Mn0KLmhlYXJ0IHN2Z3t3aWR0aDoxM3B4O2hlaWdo"
    "dDoxM3B4O2NvbG9yOnZhcigtLXR4Mil9Ci5oZWFydC5vbntiYWNrZ3JvdW5kOnZhcigtLWFjYyk7"
    "Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50fQouaGVhcnQub24gc3Zne2NvbG9yOiMwZjExMTN9Ci5x"
    "dHl7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MDtib3JkZXI6MXB4IHNvbGlk"
    "IHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6MTBweDtvdmVyZmxvdzpoaWRkZW59Ci5xdHkgYnV0"
    "dG9ue3dpZHRoOjM4cHg7aGVpZ2h0OjM4cHg7Ym9yZGVyOjA7YmFja2dyb3VuZDp2YXIoLS1jYXJk"
    "Mik7Y29sb3I6dmFyKC0tdHgpO2ZvbnQtc2l6ZToxcmVtO2ZvbnQtd2VpZ2h0OjgwMDtjdXJzb3I6"
    "cG9pbnRlcn0KLnF0eSBie21pbi13aWR0aDozOHB4O3RleHQtYWxpZ246Y2VudGVyO2ZvbnQtc2l6"
    "ZTouODVyZW19Ci50bHttYXJnaW46MTBweCAwIDA7cGFkZGluZy1sZWZ0OjE2cHg7Ym9yZGVyLWxl"
    "ZnQ6MnB4IHNvbGlkIHZhcigtLWxpbmUpfQoudGwgZGl2e3Bvc2l0aW9uOnJlbGF0aXZlO3BhZGRp"
    "bmc6MCAwIDExcHggMTBweDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4Mil9Ci50bCBk"
    "aXY6bGFzdC1jaGlsZHtwYWRkaW5nLWJvdHRvbTowfQoudGwgZGl2OjpiZWZvcmV7Y29udGVudDoi"
    "Ijtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0Oi0yMXB4O3RvcDo0cHg7d2lkdGg6OHB4O2hlaWdodDo4"
    "cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2JhY2tncm91bmQ6dmFyKC0tbGluZSl9Ci50bCBkaXYub2s6"
    "OmJlZm9yZXtiYWNrZ3JvdW5kOnZhcigtLW9rKX0KLnRsIGRpdi5vayBie2NvbG9yOnZhcigtLXR4"
    "KX0KLnRsIHNtYWxse2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi42NnJlbTtvcGFjaXR5Oi44fQou"
    "ZmNhcmR7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmZsZXgtc3RhcnQ7Z2FwOjExcHg7cGFkZGlu"
    "ZzoxMXB4IDA7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0tbGluZSl9Ci5mY2FyZDpsYXN0"
    "LWNoaWxke2JvcmRlci1ib3R0b206MH0KLmZjYXJkIC50e2ZsZXg6MTttaW4td2lkdGg6MH0KLmZj"
    "YXJkIC50IGJ7Zm9udC1zaXplOi44MXJlbTtkaXNwbGF5OmJsb2NrfQouZmNhcmQgLnQgc21hbGx7"
    "ZGlzcGxheTpibG9jaztmb250LXNpemU6LjY5cmVtO2NvbG9yOnZhcigtLXR4Mik7bGluZS1oZWln"
    "aHQ6MS40NTttYXJnaW4tdG9wOjJweH0KLmZyb3cuaGlkZSwuZmdycC5oaWRle2Rpc3BsYXk6bm9u"
    "ZX0KLmJhcnN7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmZsZXgtZW5kO2dhcDo1cHg7aGVpZ2h0"
    "Ojg2cHg7bWFyZ2luLXRvcDoxMnB4fQouYmFycyBpe2ZsZXg6MTtib3JkZXItcmFkaXVzOjZweCA2"
    "cHggM3B4IDNweDtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtkaXNwbGF5OmJsb2NrfQouZnVu"
    "bmVsIGRpdntkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo5cHg7bWFyZ2luLXRv"
    "cDo5cHg7Zm9udC1zaXplOi43NHJlbX0KLmZ1bm5lbCBpe2hlaWdodDoyNnB4O2JvcmRlci1yYWRp"
    "dXM6N3B4O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2Rpc3BsYXk6YmxvY2t9Ci5mZWVkbGlu"
    "ZXtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo5cHg7Zm9udC1zaXplOi43M3Jl"
    "bTtjb2xvcjp2YXIoLS10eDIpO3BhZGRpbmc6N3B4IDA7Ym9yZGVyLWJvdHRvbToxcHggc29saWQg"
    "dmFyKC0tbGluZSl9Ci5mZWVkbGluZTpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206MH0KLmZlZWRs"
    "aW5lIGJ7Y29sb3I6dmFyKC0tdHgpfQoubGJ7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRl"
    "cjtnYXA6MTBweDtwYWRkaW5nOjlweCAwO2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxp"
    "bmUpfQoubGI6bGFzdC1jaGlsZHtib3JkZXItYm90dG9tOjB9Ci5sYiAucmt7d2lkdGg6MjRweDto"
    "ZWlnaHQ6MjRweDtib3JkZXItcmFkaXVzOjk5cHg7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNl"
    "bnRlcjtmb250LXNpemU6LjdyZW07Zm9udC13ZWlnaHQ6ODAwO2JhY2tncm91bmQ6dmFyKC0tY2Fy"
    "ZDIpO2NvbG9yOnZhcigtLXR4Mil9Ci5sYiAucmsuZ3tiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFk"
    "KTtjb2xvcjojMGYxMTEzfQouY21zZ3JwLmhpZGV7ZGlzcGxheTpub25lfQouY21zcm93IGxhYmVs"
    "IGJ7Y29sb3I6dmFyKC0tYWNjKTtmb250LXdlaWdodDo3MDB9Ci50aW1lcmJhcntoZWlnaHQ6NXB4"
    "O2JvcmRlci1yYWRpdXM6OTlweDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtvdmVyZmxvdzpoaWRk"
    "ZW47bWFyZ2luOjEwcHggMCAycHh9Ci50aW1lcmJhciBpe2Rpc3BsYXk6YmxvY2s7aGVpZ2h0OjEw"
    "MCU7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7dHJhbnNpdGlvbjp3aWR0aCAxcyBsaW5lYXJ9"
    "Ci50aW1lcmJhci5sb3cgaXtiYWNrZ3JvdW5kOnZhcigtLWJhZCwjZTU0ODRkKX0KLmZpZWxke21h"
    "cmdpbi1ib3R0b206OXB4fQouZmllbGQgbGFiZWx7ZGlzcGxheTpibG9jaztmb250LXNpemU6LjY4"
    "cmVtO2NvbG9yOnZhcigtLXR4Mik7bGV0dGVyLXNwYWNpbmc6LjA5ZW07dGV4dC10cmFuc2Zvcm06"
    "dXBwZXJjYXNlO21hcmdpbjowIDAgNXB4IDJweDtmb250LXdlaWdodDo3MDB9Ci5zd2Fwe2Rpc3Bs"
    "YXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47"
    "Z2FwOjEycHg7cGFkZGluZzoxMXB4IDA7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0tbGlu"
    "ZSl9Ci5zd2FwOmxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRvbTowfQouc3dhcCAudHtmb250LXNpemU6"
    "LjgycmVtO2ZvbnQtd2VpZ2h0OjYwMDttaW4td2lkdGg6MH0KLnN3YXAgLnQgc21hbGx7ZGlzcGxh"
    "eTpibG9jaztmb250LXdlaWdodDo0MDA7Zm9udC1zaXplOi43cmVtO2NvbG9yOnZhcigtLXR4Mil9"
    "Ci50Z3t3aWR0aDo0NHB4O2hlaWdodDoyNnB4O2JvcmRlci1yYWRpdXM6OTlweDtiYWNrZ3JvdW5k"
    "OnZhcigtLWNhcmQyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO3Bvc2l0aW9uOnJlbGF0"
    "aXZlO2N1cnNvcjpwb2ludGVyO2ZsZXg6MCAwIGF1dG99Ci50ZzphZnRlcntjb250ZW50OiIiO3Bv"
    "c2l0aW9uOmFic29sdXRlO3RvcDozcHg7bGVmdDozcHg7d2lkdGg6MThweDtoZWlnaHQ6MThweDti"
    "b3JkZXItcmFkaXVzOjUwJTtiYWNrZ3JvdW5kOnZhcigtLXR4Mik7dHJhbnNpdGlvbjouMjJzfQou"
    "dGcub257YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50"
    "fQoudGcub246YWZ0ZXJ7bGVmdDoyMXB4O2JhY2tncm91bmQ6IzBmMTExM30KLnRnLnNte3dpZHRo"
    "OjM2cHg7aGVpZ2h0OjIxcHh9Ci50Zy5zbTphZnRlcnt3aWR0aDoxNHB4O2hlaWdodDoxNHB4O3Rv"
    "cDoyLjVweH0KLnRnLnNtLm9uOmFmdGVye2xlZnQ6MThweH0KLnN0YXJ7d2lkdGg6MzBweDtoZWln"
    "aHQ6MzBweDtib3JkZXItcmFkaXVzOjlweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVy"
    "O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29s"
    "b3I6dmFyKC0tdHgyKTtjdXJzb3I6cG9pbnRlcjtmbGV4OjAgMCBhdXRvfQouc3Rhci5vbntiYWNr"
    "Z3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojMGYxMTEzO2JvcmRlci1jb2xvcjp0cmFuc3Bh"
    "cmVudH0KLnN0YXIgc3Zne3dpZHRoOjE0cHg7aGVpZ2h0OjE0cHh9Cgojc2hlZXR7cG9zaXRpb246"
    "Zml4ZWQ7aW5zZXQ6MDt6LWluZGV4OjkwO2Rpc3BsYXk6bm9uZX0KI3NoZWV0Lm9wZW57ZGlzcGxh"
    "eTpibG9ja30KI3NoZWV0IC5iZ3twb3NpdGlvbjphYnNvbHV0ZTtpbnNldDowO2JhY2tncm91bmQ6"
    "cmdiYSgwLDAsMCwuNik7YmFja2Ryb3AtZmlsdGVyOmJsdXIoM3B4KX0KI3NoZWV0IC5wYW5lbHtw"
    "b3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjUwJTtib3R0b206MDt0cmFuc2Zvcm06dHJhbnNsYXRlWCgt"
    "NTAlKTt3aWR0aDoxMDAlO21heC13aWR0aDo1MjBweDtiYWNrZ3JvdW5kOnZhcigtLWJnMik7Ym9y"
    "ZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItYm90dG9tOjA7Ym9yZGVyLXJhZGl1czoy"
    "NHB4IDI0cHggMCAwO3BhZGRpbmc6MThweCAxNnB4IGNhbGMoMjBweCArIGVudihzYWZlLWFyZWEt"
    "aW5zZXQtYm90dG9tKSk7YW5pbWF0aW9uOnVwIC4yOHMgY3ViaWMtYmV6aWVyKC4yLC45LC4zLDEp"
    "O21heC1oZWlnaHQ6OTJ2aDtvdmVyZmxvdzphdXRvfQpAa2V5ZnJhbWVzIHVwe2Zyb217dHJhbnNm"
    "b3JtOnRyYW5zbGF0ZSgtNTAlLDEwMCUpfXRve3RyYW5zZm9ybTp0cmFuc2xhdGUoLTUwJSwwKX19"
    "CiNzaGVldCAuZ3JhYnt3aWR0aDozOHB4O2hlaWdodDo0cHg7Ym9yZGVyLXJhZGl1czo5cHg7YmFj"
    "a2dyb3VuZDp2YXIoLS1saW5lKTttYXJnaW46MCBhdXRvIDE0cHh9Ci5vdHBib3h7Zm9udC1mYW1p"
    "bHk6dWktbW9ub3NwYWNlLE1lbmxvLENvbnNvbGFzLG1vbm9zcGFjZTtmb250LXNpemU6MS42cmVt"
    "O2xldHRlci1zcGFjaW5nOi4zNWVtO3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MTZweDtib3Jk"
    "ZXItcmFkaXVzOnZhcigtLXJzKTtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtib3JkZXI6MXB4IGRh"
    "c2hlZCB2YXIoLS1saW5lKTttYXJnaW46MTBweCAwfQoucXJ7d2lkdGg6MTUwcHg7aGVpZ2h0OjE1"
    "MHB4O21hcmdpbjo2cHggYXV0byAxMHB4O2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO2JhY2tncm91"
    "bmQ6dmFyKC0tY2FyZDIpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7ZGlzcGxheTpncmlk"
    "O3BsYWNlLWl0ZW1zOmNlbnRlcjtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjVyZW07bGV0"
    "dGVyLXNwYWNpbmc6LjFlbTt0ZXh0LWFsaWduOmNlbnRlcn0KLnN0ZXBze21hcmdpbjoxMnB4IDAg"
    "NHB4fQouc3RlcHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4O3BhZGRp"
    "bmc6OHB4IDA7Zm9udC1zaXplOi43OXJlbTtjb2xvcjp2YXIoLS10eDIpfQouc3RlcCBpe3dpZHRo"
    "OjIycHg7aGVpZ2h0OjIycHg7Ym9yZGVyLXJhZGl1czo1MCU7Ym9yZGVyOjFweCBzb2xpZCB2YXIo"
    "LS1saW5lKTtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2ZvbnQtc3R5bGU6bm9ybWFs"
    "O2ZvbnQtc2l6ZTouNjVyZW07Zm9udC13ZWlnaHQ6ODAwO2ZsZXg6MCAwIGF1dG99Ci5zdGVwLm9u"
    "e2NvbG9yOnZhcigtLXR4KX0KLnN0ZXAub24gaXtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtj"
    "b2xvcjojMGYxMTEzO2JvcmRlci1jb2xvcjp0cmFuc3BhcmVudH0KLnN0ZXAucnVuIGl7Ym9yZGVy"
    "LWNvbG9yOnZhcigtLWFjYyk7Y29sb3I6dmFyKC0tYWNjKTthbmltYXRpb246cHVsc2UgMXMgaW5m"
    "aW5pdGV9CkBrZXlmcmFtZXMgcHVsc2V7NTAle29wYWNpdHk6LjM1fX0KLnRvYXN0e3Bvc2l0aW9u"
    "OmZpeGVkO2xlZnQ6NTAlO2JvdHRvbTpjYWxjKDg4cHggKyBlbnYoc2FmZS1hcmVhLWluc2V0LWJv"
    "dHRvbSkpO3RyYW5zZm9ybTp0cmFuc2xhdGVYKC01MCUpO3otaW5kZXg6MTIwO2JhY2tncm91bmQ6"
    "dmFyKC0tYmcyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2NvbG9yOnZhcigtLXR4KTtw"
    "YWRkaW5nOjExcHggMTZweDtib3JkZXItcmFkaXVzOjk5cHg7Zm9udC1zaXplOi43OHJlbTtmb250"
    "LXdlaWdodDo2MDA7Ym94LXNoYWRvdzp2YXIoLS1zaGFkb3cpO29wYWNpdHk6MDtwb2ludGVyLWV2"
    "ZW50czpub25lO3RyYW5zaXRpb246b3BhY2l0eSAuMnMsdHJhbnNmb3JtIC4yczt3aGl0ZS1zcGFj"
    "ZTpub3dyYXA7bWF4LXdpZHRoOjkwdnc7b3ZlcmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxs"
    "aXBzaXN9Ci50b2FzdC5zaG93e29wYWNpdHk6MTt0cmFuc2Zvcm06dHJhbnNsYXRlWCgtNTAlKSB0"
    "cmFuc2xhdGVZKC02cHgpfQoKLyogcHJldmlldyBjaHJvbWUg4oCUIG1vY2t1cCBvbmx5LCBvd24g"
    "c3RyaXAgc28gaXQgbmV2ZXIgb3ZlcmxhcHMgdGhlIFVJICovCiNzd3twb3NpdGlvbjpmaXhlZDt0"
    "b3A6MDtsZWZ0OjA7cmlnaHQ6MDtoZWlnaHQ6MzJweDt6LWluZGV4OjIwMDtkaXNwbGF5OmZsZXg7"
    "YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Z2FwOjVweDsKICBiYWNr"
    "Z3JvdW5kOnJnYmEoMjIsMjUsMjgsLjkpO2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxp"
    "bmUpO2JhY2tkcm9wLWZpbHRlcjpibHVyKDEwcHgpfQojc3cgZW17Zm9udC1zdHlsZTpub3JtYWw7"
    "Zm9udC1zaXplOi41NnJlbTtsZXR0ZXItc3BhY2luZzouMThlbTt0ZXh0LXRyYW5zZm9ybTp1cHBl"
    "cmNhc2U7Y29sb3I6dmFyKC0tdHgyKTttYXJnaW4tcmlnaHQ6NHB4fQojc3cgYnV0dG9ue2hlaWdo"
    "dDoyMnB4O3BhZGRpbmc6MCAxMnB4O2JvcmRlci1yYWRpdXM6OTlweDtib3JkZXI6MDtjdXJzb3I6"
    "cG9pbnRlcjtiYWNrZ3JvdW5kOnRyYW5zcGFyZW50O2NvbG9yOnZhcigtLXR4Mik7Zm9udC1mYW1p"
    "bHk6dmFyKC0tZm9udCk7Zm9udC1zaXplOi42cmVtO2ZvbnQtd2VpZ2h0OjcwMDtsZXR0ZXItc3Bh"
    "Y2luZzouMDdlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2V9CiNzdyBidXR0b24ub257YmFja2dy"
    "b3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6IzBmMTExM30KCi8qIOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCBBVVJPUkEgVUkgS0lUIOKAlCBwb3J0ZWQgY29t"
    "cG9uZW50cyDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZAgKi8K"
    "QHByb3BlcnR5IC0tZ2F7c3ludGF4OiI8YW5nbGU+Ijtpbml0aWFsLXZhbHVlOjBkZWc7aW5oZXJp"
    "dHM6ZmFsc2V9CkBwcm9wZXJ0eSAtLWdwe3N5bnRheDoiPHBlcmNlbnRhZ2U+Ijtpbml0aWFsLXZh"
    "bHVlOjUlO2luaGVyaXRzOmZhbHNlfQpAcHJvcGVydHkgLS1nb3tzeW50YXg6IjxhbmdsZT4iO2lu"
    "aXRpYWwtdmFsdWU6MGRlZztpbmhlcml0czpmYWxzZX0KCi8qIDIzIMK3IFNoaW55IENUQSDigJQg"
    "Y29uaWMgYm9yZGVyICsgZG90IG1hc2sgKyBpbm5lciBzaGltbWVyICsgYnJlYXRoZSAqLwouYnRu"
    "LmN0YXstLWR1cjozLjJzOy0taGw6I2ZmZmZmZjtwb3NpdGlvbjpyZWxhdGl2ZTtpc29sYXRpb246"
    "aXNvbGF0ZTtib3JkZXI6MXB4IHNvbGlkIHRyYW5zcGFyZW50OwogIGJhY2tncm91bmQ6bGluZWFy"
    "LWdyYWRpZW50KHZhcigtLWJ0bmJnKSx2YXIoLS1idG5iZykpIHBhZGRpbmctYm94LAogICAgY29u"
    "aWMtZ3JhZGllbnQoZnJvbSBjYWxjKHZhcigtLWdhKSAtIHZhcigtLWdvKSksdHJhbnNwYXJlbnQs"
    "dmFyKC0taGwpIHZhcigtLWdwKSwKICAgICAgI2ZmZiBjYWxjKHZhcigtLWdwKSoyKSx2YXIoLS1o"
    "bCkgY2FsYyh2YXIoLS1ncCkqMyksdHJhbnNwYXJlbnQgY2FsYyh2YXIoLS1ncCkqNCkpIGJvcmRl"
    "ci1ib3g7CiAgYm94LXNoYWRvdzppbnNldCAwIDAgMCAxcHggcmdiYSgyNTUsMjU1LDI1NSwuMDgp"
    "OwogIHRyYW5zaXRpb246LS1nbyAuOHMgY3ViaWMtYmV6aWVyKC4yNSwxLC41LDEpLC0tZ3AgLjhz"
    "IGN1YmljLWJlemllciguMjUsMSwuNSwxKSx0cmFuc2Zvcm0gLjE1czsKICBhbmltYXRpb246Z2Eg"
    "dmFyKC0tZHVyKSBsaW5lYXIgaW5maW5pdGV9Ci5idG4uY3RhOmJlZm9yZSwuYnRuLmN0YTphZnRl"
    "cntjb250ZW50OiIiO3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6NTAlO3RvcDo1MCU7dHJhbnNsYXRl"
    "Oi01MCUgLTUwJTt6LWluZGV4Oi0xO3BvaW50ZXItZXZlbnRzOm5vbmV9Ci5idG4uY3RhOmJlZm9y"
    "ZXt3aWR0aDpjYWxjKDEwMCUgLSA2cHgpO2hlaWdodDpjYWxjKDEwMCUgLSA2cHgpO2JvcmRlci1y"
    "YWRpdXM6aW5oZXJpdDtvcGFjaXR5Oi4zNTsKICBiYWNrZ3JvdW5kOnJhZGlhbC1ncmFkaWVudChj"
    "aXJjbGUgYXQgMnB4IDJweCwjZmZmIC41cHgsdHJhbnNwYXJlbnQgMCkgcGFkZGluZy1ib3g7YmFj"
    "a2dyb3VuZC1zaXplOjRweCA0cHg7CiAgLXdlYmtpdC1tYXNrLWltYWdlOmNvbmljLWdyYWRpZW50"
    "KGZyb20gY2FsYyh2YXIoLS1nYSkgKyA0NWRlZyksIzAwMCx0cmFuc3BhcmVudCAxMCUgOTAlLCMw"
    "MDApOwogIG1hc2staW1hZ2U6Y29uaWMtZ3JhZGllbnQoZnJvbSBjYWxjKHZhcigtLWdhKSArIDQ1"
    "ZGVnKSwjMDAwLHRyYW5zcGFyZW50IDEwJSA5MCUsIzAwMCl9Ci5idG4uY3RhOmFmdGVye3dpZHRo"
    "OjEwMCU7YXNwZWN0LXJhdGlvOjE7b3BhY2l0eTouNTU7CiAgYmFja2dyb3VuZDpsaW5lYXItZ3Jh"
    "ZGllbnQoLTUwZGVnLHRyYW5zcGFyZW50LHZhcigtLWhsKSx0cmFuc3BhcmVudCk7CiAgLXdlYmtp"
    "dC1tYXNrLWltYWdlOnJhZGlhbC1ncmFkaWVudChjaXJjbGUgYXQgYm90dG9tLHRyYW5zcGFyZW50"
    "IDQwJSwjMDAwKTsKICBtYXNrLWltYWdlOnJhZGlhbC1ncmFkaWVudChjaXJjbGUgYXQgYm90dG9t"
    "LHRyYW5zcGFyZW50IDQwJSwjMDAwKTsKICBhbmltYXRpb246c2hpbW1lciB2YXIoLS1kdXIpIGxp"
    "bmVhciBpbmZpbml0ZX0KLmJ0bi5jdGE6aG92ZXJ7LS1ncDoyMiU7LS1nbzo5NWRlZ30KLmJ0bi5j"
    "dGE6YWN0aXZle3RyYW5zbGF0ZTowIDFweH0KQGtleWZyYW1lcyBnYXt0b3stLWdhOjM2MGRlZ319"
    "CkBrZXlmcmFtZXMgc2hpbW1lcnt0b3tyb3RhdGU6MzYwZGVnfX0KCi8qIDIyIMK3IExpcXVpZCBn"
    "bGFzcyAqLwouYnRuLmdsYXNzLC5nbGFzc2NhcmR7cG9zaXRpb246cmVsYXRpdmU7YmFja2dyb3Vu"
    "ZDpjb2xvci1taXgoaW4gc3JnYix2YXIoLS1jYXJkMikgNzAlLHRyYW5zcGFyZW50KTsKICBib3Jk"
    "ZXI6MXB4IHNvbGlkIGNvbG9yLW1peChpbiBzcmdiLCNmZmYgMTYlLHZhcigtLWxpbmUpKTtjb2xv"
    "cjp2YXIoLS10eCk7CiAgYmFja2Ryb3AtZmlsdGVyOmJsdXIoMTRweCkgc2F0dXJhdGUoMS40KTst"
    "d2Via2l0LWJhY2tkcm9wLWZpbHRlcjpibHVyKDE0cHgpIHNhdHVyYXRlKDEuNCk7CiAgYm94LXNo"
    "YWRvdzppbnNldCAxcHggMXB4IDFweCAtLjVweCByZ2JhKDI1NSwyNTUsMjU1LC41NSksaW5zZXQg"
    "LTFweCAtMXB4IDFweCAtLjVweCByZ2JhKDI1NSwyNTUsMjU1LC4zKSwKICAgIGluc2V0IDAgMCA4"
    "cHggNnB4IHJnYmEoMjU1LDI1NSwyNTUsLjA3KSwwIDJweCAxMHB4IHJnYmEoMCwwLDAsLjEyKX0K"
    "LmJ0bi5nbGFzczphZnRlcntjb250ZW50OiIiO3Bvc2l0aW9uOmFic29sdXRlO2luc2V0OjFweCAx"
    "cHggYXV0bztoZWlnaHQ6NDIlO2JvcmRlci1yYWRpdXM6aW5oZXJpdDsKICBiYWNrZ3JvdW5kOmxp"
    "bmVhci1ncmFkaWVudCgxODBkZWcscmdiYSgyNTUsMjU1LDI1NSwuMzQpLHRyYW5zcGFyZW50KTtw"
    "b2ludGVyLWV2ZW50czpub25lfQouYnRuLmdsYXNzOmhvdmVye3RyYW5zZm9ybTpzY2FsZSgxLjAz"
    "KX0KCi8qIHByZXNzIHJpcHBsZSAoa2l0IGRvY2svYnV0dG9uIGZlZWwpICovCi5yaXB7cG9zaXRp"
    "b246YWJzb2x1dGU7Ym9yZGVyLXJhZGl1czo1MCU7dHJhbnNmb3JtOnRyYW5zbGF0ZSgtNTAlLC01"
    "MCUpIHNjYWxlKDApO3BvaW50ZXItZXZlbnRzOm5vbmU7CiAgYmFja2dyb3VuZDpyYWRpYWwtZ3Jh"
    "ZGllbnQoY2lyY2xlLHJnYmEoMjU1LDI1NSwyNTUsLjU1KSx0cmFuc3BhcmVudCA3MCUpO2FuaW1h"
    "dGlvbjpyaXAgLjU1cyBlYXNlLW91dCBmb3J3YXJkc30KQGtleWZyYW1lcyByaXB7dG97dHJhbnNm"
    "b3JtOnRyYW5zbGF0ZSgtNTAlLC01MCUpIHNjYWxlKDEpO29wYWNpdHk6MH19CgovKiAyNCDCtyBB"
    "bmltYXRlZCBiYW5uZXIgKyAxNyDCtyBsb2dvIGNsb3VkIG1hcnF1ZWUgKi8KLmJhbm5lcntwb3Np"
    "dGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRkZW47Ym9yZGVyLXJhZGl1czoxMHB4O2JvcmRlcjox"
    "cHggc29saWQgdmFyKC0tbGluZSk7CiAgYmFja2dyb3VuZDp2YXIoLS1jYXJkKTttYXJnaW46MnB4"
    "IDAgMTJweDtoZWlnaHQ6MzhweDtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyOwogIC13"
    "ZWJraXQtbWFzay1pbWFnZTpsaW5lYXItZ3JhZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCA4"
    "JSwjMDAwIDkyJSx0cmFuc3BhcmVudCk7CiAgbWFzay1pbWFnZTpsaW5lYXItZ3JhZGllbnQoOTBk"
    "ZWcsdHJhbnNwYXJlbnQsIzAwMCA4JSwjMDAwIDkyJSx0cmFuc3BhcmVudCl9Ci5iYW5uZXIgLnRy"
    "YWNre2Rpc3BsYXk6ZmxleDtnYXA6MzBweDt3aGl0ZS1zcGFjZTpub3dyYXA7YW5pbWF0aW9uOm1h"
    "cnEgMjZzIGxpbmVhciBpbmZpbml0ZTt3aWxsLWNoYW5nZTp0cmFuc2Zvcm19Ci5iYW5uZXI6aG92"
    "ZXIgLnRyYWNre2FuaW1hdGlvbi1wbGF5LXN0YXRlOnBhdXNlZH0KLmJhbm5lciBzcGFue2ZvbnQt"
    "c2l6ZTouN3JlbTtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtd2VpZ2h0OjYwMDtsZXR0ZXItc3BhY2lu"
    "ZzouMDNlbTtkaXNwbGF5OmlubGluZS1mbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6NnB4fQou"
    "YmFubmVyIHNwYW4gYntjb2xvcjp2YXIoLS1hY2MpfQouYmFubmVyIHNwYW4gc3Zne3dpZHRoOjEz"
    "cHg7aGVpZ2h0OjEzcHg7Y29sb3I6dmFyKC0tYWNjKX0KQGtleWZyYW1lcyBtYXJxe3Rve3RyYW5z"
    "Zm9ybTp0cmFuc2xhdGVYKC01MCUpfX0KLmNsb3Vke2Rpc3BsYXk6ZmxleDtnYXA6MjZweDtvdmVy"
    "ZmxvdzpoaWRkZW47cGFkZGluZzo0cHggMCAycHg7CiAgLXdlYmtpdC1tYXNrLWltYWdlOmxpbmVh"
    "ci1ncmFkaWVudCg5MGRlZyx0cmFuc3BhcmVudCwjMDAwIDEyJSwjMDAwIDg4JSx0cmFuc3BhcmVu"
    "dCk7CiAgbWFzay1pbWFnZTpsaW5lYXItZ3JhZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCAx"
    "MiUsIzAwMCA4OCUsdHJhbnNwYXJlbnQpfQouY2xvdWQgLnR7ZGlzcGxheTpmbGV4O2dhcDoyNnB4"
    "O2FuaW1hdGlvbjptYXJxIDMwcyBsaW5lYXIgaW5maW5pdGV9Ci5jbG91ZCBpe2ZvbnQtc3R5bGU6"
    "bm9ybWFsO2ZvbnQtc2l6ZTouNjhyZW07Zm9udC13ZWlnaHQ6ODAwO2xldHRlci1zcGFjaW5nOi4x"
    "NGVtO2NvbG9yOnZhcigtLXR4Mik7b3BhY2l0eTouNTU7d2hpdGUtc3BhY2U6bm93cmFwfQoKLyog"
    "MDcgwrcgR3JhZGllbnQgdHJhY2luZyAqLwoudHJhY2V7ZGlzcGxheTpibG9jazt3aWR0aDoxMDAl"
    "O2hlaWdodDoxMHB4O21hcmdpbjotMnB4IDAgOHB4O292ZXJmbG93OnZpc2libGV9Ci50cmFjZSAu"
    "YmFzZXtzdHJva2U6dmFyKC0tbGluZSk7c3Ryb2tlLXdpZHRoOjEuNH0KLnRyYWNlIC5sZWFke3N0"
    "cm9rZS13aWR0aDoxLjg7c3Ryb2tlLWxpbmVjYXA6cm91bmR9Ci5zZWMgaDJ7cG9zaXRpb246cmVs"
    "YXRpdmV9CgovKiAwMSArIDEzIMK3IERvdCBsb2FkZXIgKi8KLmRvdGdyaWR7ZGlzcGxheTpncmlk"
    "O2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoNywxZnIpO2dhcDozcHg7d2lkdGg6NzBweH0K"
    "LmRvdGdyaWQgaXt3aWR0aDoxMDAlO2FzcGVjdC1yYXRpbzoxO2JvcmRlci1yYWRpdXM6MnB4O2Jh"
    "Y2tncm91bmQ6Y29sb3ItbWl4KGluIHNyZ2IsdmFyKC0tdHgpIDE0JSx0cmFuc3BhcmVudCk7dHJh"
    "bnNpdGlvbjpiYWNrZ3JvdW5kIC4xMnMsdHJhbnNmb3JtIC4xMnN9Ci5kb3RncmlkIGkuYWN0e2Jh"
    "Y2tncm91bmQ6dmFyKC0tYWNjKTt0cmFuc2Zvcm06c2NhbGUoMS4xMil9Ci5kb3Rze2Rpc3BsYXk6"
    "aW5saW5lLWZsZXg7Z2FwOjRweDthbGlnbi1pdGVtczpjZW50ZXJ9Ci5kb3RzIGl7d2lkdGg6NXB4"
    "O2hlaWdodDo1cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3VuZDp2YXIoLS1hY2MpO2FuaW1h"
    "dGlvbjpib3VuY2UgMXMgaW5maW5pdGUgZWFzZS1pbi1vdXR9Ci5kb3RzIGk6bnRoLWNoaWxkKDIp"
    "e2FuaW1hdGlvbi1kZWxheTouMTVzfS5kb3RzIGk6bnRoLWNoaWxkKDMpe2FuaW1hdGlvbi1kZWxh"
    "eTouM3N9CkBrZXlmcmFtZXMgYm91bmNlezAlLDgwJSwxMDAle3RyYW5zZm9ybTp0cmFuc2xhdGVZ"
    "KDApO29wYWNpdHk6LjM1fTQwJXt0cmFuc2Zvcm06dHJhbnNsYXRlWSgtNXB4KTtvcGFjaXR5OjF9"
    "fQoKLyogc3BsYXNoICovCiNzcGxhc2h7cG9zaXRpb246Zml4ZWQ7aW5zZXQ6MDt6LWluZGV4OjMw"
    "MDtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1uO2FsaWduLWl0ZW1zOmNlbnRlcjtq"
    "dXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2dhcDoxNnB4OwogIGJhY2tncm91bmQ6dmFyKC0tYmcpO3Ry"
    "YW5zaXRpb246b3BhY2l0eSAuNXMgZWFzZSx2aXNpYmlsaXR5IC41c30KI3NwbGFzaC5nb25le29w"
    "YWNpdHk6MDt2aXNpYmlsaXR5OmhpZGRlbn0KI3NwbGFzaCAubWFya3t3aWR0aDo1OHB4O2hlaWdo"
    "dDo1OHB4O2JvcmRlci1yYWRpdXM6MTJweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVy"
    "O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpOwogIGNvbG9yOiMwZjExMTM7Zm9udC13ZWlnaHQ6"
    "ODAwO2ZvbnQtc2l6ZToxLjFyZW07YW5pbWF0aW9uOm1hcmtpbiAuN3MgY3ViaWMtYmV6aWVyKC4y"
    "LDEuNCwuNCwxKSBib3RofQojc3BsYXNoIGg0e21hcmdpbjowO2ZvbnQtZmFtaWx5OnZhcigtLWZo"
    "ZWFkKTtmb250LXNpemU6MXJlbTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LS4wMmVt"
    "OwogIGFuaW1hdGlvbjpmYWRldXAgLjZzIC4xNXMgYm90aH0KI3NwbGFzaCBzbWFsbHtjb2xvcjp2"
    "YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjVyZW07bGV0dGVyLXNwYWNpbmc6LjIyZW07dGV4dC10cmFu"
    "c2Zvcm06dXBwZXJjYXNlO2FuaW1hdGlvbjpmYWRldXAgLjZzIC4yNXMgYm90aH0KQGtleWZyYW1l"
    "cyBtYXJraW57ZnJvbXtvcGFjaXR5OjA7dHJhbnNmb3JtOnNjYWxlKC42KSByb3RhdGUoLTEyZGVn"
    "KX10b3tvcGFjaXR5OjE7dHJhbnNmb3JtOm5vbmV9fQpAa2V5ZnJhbWVzIGZhZGV1cHtmcm9te29w"
    "YWNpdHk6MDt0cmFuc2Zvcm06dHJhbnNsYXRlWSg4cHgpfXRve29wYWNpdHk6MTt0cmFuc2Zvcm06"
    "bm9uZX19CgovKiAwOCDCtyBLaW5ldGljIGdyaWQgY2FudmFzICovCiNrZ3twb3NpdGlvbjpmaXhl"
    "ZDtpbnNldDowO3otaW5kZXg6MDtwb2ludGVyLWV2ZW50czpub25lO29wYWNpdHk6LjMyfQoKLyog"
    "c3RhZ2dlcmVkIHJldmVhbCBpcyBkcml2ZW4gYnkgdGhlIFdlYiBBbmltYXRpb25zIEFQSSAoY2Fu"
    "bm90IGNsYXNoIHdpdGggdGhlbWUgQ1NTKSAqLwoKLyogMDIgKyAyNSDCtyBEb2NrIHdpdGggc3By"
    "aW5nIHBpbGwgKi8KbmF2I3RhYnMgLmRvY2t7cG9zaXRpb246cmVsYXRpdmV9Cm5hdiN0YWJzIC5k"
    "cGlsbHtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjA7dG9wOjA7ei1pbmRleDowO2JvcmRlci1yYWRp"
    "dXM6OXB4O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpOwogIGJveC1zaGFkb3c6MCA2cHggMThw"
    "eCBjb2xvci1taXgoaW4gc3JnYix2YXIoLS1hY2MpIDM1JSx0cmFuc3BhcmVudCk7cG9pbnRlci1l"
    "dmVudHM6bm9uZX0KbmF2I3RhYnMgYnV0dG9ue3Bvc2l0aW9uOnJlbGF0aXZlO3otaW5kZXg6MTt3"
    "aWxsLWNoYW5nZTp0cmFuc2Zvcm07CiAgdHJhbnNpdGlvbjpjb2xvciAuMjZzIGN1YmljLWJlemll"
    "ciguMjIsMSwuMzYsMSksdHJhbnNmb3JtIC4xOHMgY3ViaWMtYmV6aWVyKC4zLDEuNSwuNSwxKX0K"
    "bmF2I3RhYnMgYnV0dG9uLm9ue2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7Y29sb3I6IzBmMTExM30K"
    "bmF2I3RhYnMgLmRwaWxse29wYWNpdHk6MH0KbmF2I3RhYnMgLmRwaWxsLnJlYWR5e29wYWNpdHk6"
    "MX0KCi8qIDE0IMK3IFYtdGFicyBzbGlkaW5nIGluZGljYXRvciAqLwouc3VibmF2e3Bvc2l0aW9u"
    "OnJlbGF0aXZlfQouc3VibmF2IC52cGlsbHtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjA7dG9wOjA7"
    "ei1pbmRleDowO2JvcmRlci1yYWRpdXM6OHB4O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO3Ry"
    "YW5zaXRpb246bm9uZTtwb2ludGVyLWV2ZW50czpub25lfQouc3VibmF2IGJ1dHRvbntwb3NpdGlv"
    "bjpyZWxhdGl2ZTt6LWluZGV4OjF9Ci5zdWJuYXYgYnV0dG9uLm9ue2JhY2tncm91bmQ6dHJhbnNw"
    "YXJlbnQ7Y29sb3I6IzBmMTExMztib3JkZXItY29sb3I6dHJhbnNwYXJlbnR9CgovKiAxNSDCtyBB"
    "bmltYXRlZCBwcm9maWxlIGNhcmQg4oCUIHRpbHQgKyBnbGFyZSAqLwoudGlsdHt0cmFuc2Zvcm0t"
    "c3R5bGU6cHJlc2VydmUtM2Q7dHJhbnNpdGlvbjp0cmFuc2Zvcm0gLjM1cyBjdWJpYy1iZXppZXIo"
    "LjIyLDEsLjM2LDEpO3dpbGwtY2hhbmdlOnRyYW5zZm9ybX0KLnRpbHQgLmdsYXJle3Bvc2l0aW9u"
    "OmFic29sdXRlO2luc2V0OjA7Ym9yZGVyLXJhZGl1czppbmhlcml0O3BvaW50ZXItZXZlbnRzOm5v"
    "bmU7b3BhY2l0eTowO3RyYW5zaXRpb246b3BhY2l0eSAuM3M7CiAgYmFja2dyb3VuZDpyYWRpYWwt"
    "Z3JhZGllbnQoMjQwcHggY2lyY2xlIGF0IHZhcigtLW14LDUwJSkgdmFyKC0tbXksNTAlKSxyZ2Jh"
    "KDI1NSwyNTUsMjU1LC4yMiksdHJhbnNwYXJlbnQgNjAlKX0KLnRpbHQ6aG92ZXIgLmdsYXJle29w"
    "YWNpdHk6MX0KLnNoaW5lY2FyZHtwb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRkZW59Ci5z"
    "aGluZWNhcmQ6YmVmb3Jle2NvbnRlbnQ6IiI7cG9zaXRpb246YWJzb2x1dGU7dG9wOi02MCU7bGVm"
    "dDotMzAlO3dpZHRoOjU1JTtoZWlnaHQ6MjIwJTt0cmFuc2Zvcm06cm90YXRlKDE4ZGVnKTsKICBi"
    "YWNrZ3JvdW5kOmxpbmVhci1ncmFkaWVudCg5MGRlZyx0cmFuc3BhcmVudCxyZ2JhKDI1NSwyNTUs"
    "MjU1LC4xNiksdHJhbnNwYXJlbnQpO2FuaW1hdGlvbjpjYXJkc2hpbmUgNS41cyBlYXNlLWluLW91"
    "dCBpbmZpbml0ZTtwb2ludGVyLWV2ZW50czpub25lfQpAa2V5ZnJhbWVzIGNhcmRzaGluZXswJSw3"
    "MCV7bGVmdDotNDAlfTEwMCV7bGVmdDoxMjAlfX0KCi8qIDA1IMK3IEhvbGQgYWN0aW9uIGJ1dHRv"
    "biAqLwouaG9sZHtwb3NpdGlvbjpyZWxhdGl2ZTtvdmVyZmxvdzpoaWRkZW59Ci5ob2xkIC5maWxs"
    "e3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6MDt0b3A6MDtib3R0b206MDt3aWR0aDowO2JhY2tncm91"
    "bmQ6cmdiYSgyNTUsMjU1LDI1NSwuMjUpO3RyYW5zaXRpb246d2lkdGggLjA1cyBsaW5lYXJ9Ci5o"
    "b2xkLmRvbmV7YW5pbWF0aW9uOnBvcCAuNHMgY3ViaWMtYmV6aWVyKC4yLDEuNiwuNCwxKX0KQGtl"
    "eWZyYW1lcyBwb3B7NTAle3RyYW5zZm9ybTpzY2FsZSgxLjA0KX19CgovKiAxMSDCtyBNb3JwaGlu"
    "ZyBzZWFyY2ggKi8KLm1zZWFyY2h7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6"
    "OHB4O21hcmdpbjowIDAgMTBweH0KLm1zZWFyY2ggLmZpZWxkd3JhcHtmbGV4OjE7bWluLXdpZHRo"
    "OjA7cG9zaXRpb246cmVsYXRpdmU7aGVpZ2h0OjQwcHh9Ci5tc2VhcmNoIGlucHV0e3dpZHRoOjEw"
    "MCU7aGVpZ2h0OjQwcHg7cGFkZGluZzowIDEzcHggMCAzNnB4O2JvcmRlci1yYWRpdXM6OTlweDti"
    "b3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpOwogIGJhY2tncm91bmQ6dmFyKC0tY2FyZCk7Y29s"
    "b3I6dmFyKC0tdHgpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouOHJlbTtvdXRs"
    "aW5lOm5vbmU7CiAgdHJhbnNpdGlvbjpib3gtc2hhZG93IC4zcyBjdWJpYy1iZXppZXIoLjIyLDEs"
    "LjM2LDEpLGJvcmRlci1jb2xvciAuM3N9Ci5tc2VhcmNoIGlucHV0OmZvY3Vze2JvcmRlci1jb2xv"
    "cjp2YXIoLS1hY2MpO2JveC1zaGFkb3c6MCAwIDAgNHB4IGNvbG9yLW1peChpbiBzcmdiLHZhcigt"
    "LWFjYykgMTYlLHRyYW5zcGFyZW50KX0KLm1zZWFyY2ggc3Zne3Bvc2l0aW9uOmFic29sdXRlO2xl"
    "ZnQ6MTJweDt0b3A6MTFweDt3aWR0aDoxN3B4O2hlaWdodDoxN3B4O2NvbG9yOnZhcigtLXR4Mik7"
    "dHJhbnNpdGlvbjpjb2xvciAuMjVzfQoubXNlYXJjaCBpbnB1dDpmb2N1cytzdmcsLm1zZWFyY2gg"
    "LmZpZWxkd3JhcDpmb2N1cy13aXRoaW4gc3Zne2NvbG9yOnZhcigtLWFjYyl9Ci5jaGlwcm93e2Rp"
    "c3BsYXk6ZmxleDtnYXA6NnB4O292ZXJmbG93LXg6YXV0bztwYWRkaW5nLWJvdHRvbToxMHB4fQou"
    "Y2hpcHJvdyBidXR0b257ZmxleDowIDAgYXV0bztoZWlnaHQ6MzBweDtwYWRkaW5nOjAgMTJweDti"
    "b3JkZXItcmFkaXVzOjk5cHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTsKICBiYWNrZ3Jv"
    "dW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQp"
    "O2ZvbnQtc2l6ZTouN3JlbTtmb250LXdlaWdodDo3MDA7Y3Vyc29yOnBvaW50ZXI7d2hpdGUtc3Bh"
    "Y2U6bm93cmFwOwogIHRyYW5zaXRpb246dHJhbnNmb3JtIC4ycyBjdWJpYy1iZXppZXIoLjMsMS41"
    "LC41LDEpLGJhY2tncm91bmQgLjJzLGNvbG9yIC4yc30KLmNoaXByb3cgYnV0dG9uLm9ue2JhY2tn"
    "cm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiMwZjExMTM7Ym9yZGVyLWNvbG9yOnRyYW5zcGFy"
    "ZW50O3RyYW5zZm9ybTpzY2FsZSgxLjA0KX0KCi8qIHNrZWxldG9ucyAoMTMgwrcgbG9hZGVyKSAq"
    "Lwouc2t7cG9zaXRpb246cmVsYXRpdmU7b3ZlcmZsb3c6aGlkZGVuO2JhY2tncm91bmQ6dmFyKC0t"
    "Y2FyZDIpO2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO2hlaWdodDo3NHB4O21hcmdpbi1ib3R0b206"
    "OXB4fQouc2s6YWZ0ZXJ7Y29udGVudDoiIjtwb3NpdGlvbjphYnNvbHV0ZTtpbnNldDowO2JhY2tn"
    "cm91bmQ6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRyYW5zcGFyZW50LGNvbG9yLW1peChpbiBzcmdi"
    "LHZhcigtLXR4KSAxMCUsdHJhbnNwYXJlbnQpLHRyYW5zcGFyZW50KTsKICB0cmFuc2Zvcm06dHJh"
    "bnNsYXRlWCgtMTAwJSk7YW5pbWF0aW9uOnNoaW0gMS4xNXMgaW5maW5pdGV9CkBrZXlmcmFtZXMg"
    "c2hpbXt0b3t0cmFuc2Zvcm06dHJhbnNsYXRlWCgxMDAlKX19CgovKiAxOSDCtyBGZWF0dXJlIHN0"
    "cmlwICovCi5mZWF0c3tkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOnJlcGVhdCgz"
    "LDFmcik7Z2FwOjhweDttYXJnaW4tYm90dG9tOjEycHh9Ci5mZWF0e2JhY2tncm91bmQ6dmFyKC0t"
    "Y2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXJz"
    "KTtwYWRkaW5nOjExcHggOXB4O3RleHQtYWxpZ246Y2VudGVyOwogIHRyYW5zaXRpb246dHJhbnNm"
    "b3JtIC4yNXMgY3ViaWMtYmV6aWVyKC4yMiwxLC4zNiwxKX0KLmZlYXQ6aG92ZXJ7dHJhbnNmb3Jt"
    "OnRyYW5zbGF0ZVkoLTNweCl9Ci5mZWF0IHN2Z3t3aWR0aDoxN3B4O2hlaWdodDoxN3B4O2NvbG9y"
    "OnZhcigtLWFjYyl9Ci5mZWF0IGJ7ZGlzcGxheTpibG9jaztmb250LXNpemU6LjY4cmVtO21hcmdp"
    "bi10b3A6NXB4fQouZmVhdCBzbWFsbHtkaXNwbGF5OmJsb2NrO2ZvbnQtc2l6ZTouNnJlbTtjb2xv"
    "cjp2YXIoLS10eDIpfQoKLyogMDMgwrcgV29ya3Mgd2hlZWwg4oCUIHJvdGF0aW5nIGRlYWwgZGlh"
    "bCAqLwoud2hlZWx7cG9zaXRpb246cmVsYXRpdmU7aGVpZ2h0OjExOHB4O21hcmdpbjoycHggMCAx"
    "MnB4O292ZXJmbG93OmhpZGRlbjtib3JkZXItcmFkaXVzOnZhcigtLXIpOwogIGJvcmRlcjoxcHgg"
    "c29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIoLS1jYXJkKX0KLndoZWVsIC5yaW5ne3Bv"
    "c2l0aW9uOmFic29sdXRlO3JpZ2h0Oi01MnB4O3RvcDo1MCU7d2lkdGg6MTc2cHg7aGVpZ2h0OjE3"
    "NnB4O21hcmdpbi10b3A6LTg4cHg7Ym9yZGVyLXJhZGl1czo1MCU7CiAgYm9yZGVyOjFweCBkYXNo"
    "ZWQgdmFyKC0tbGluZSk7YW5pbWF0aW9uOnNwaW4gMjZzIGxpbmVhciBpbmZpbml0ZX0KLndoZWVs"
    "IC5yaW5nIGJ7cG9zaXRpb246YWJzb2x1dGU7bGVmdDo1MCU7dG9wOjUwJTt3aWR0aDo1MHB4O2hl"
    "aWdodDoyNHB4O21hcmdpbjotMTJweCAwIDAgLTI1cHg7Ym9yZGVyLXJhZGl1czo5OXB4OwogIGRp"
    "c3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9udC1zaXplOi42MnJlbTtmb250LXdlaWdo"
    "dDo4MDA7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5l"
    "KTtjb2xvcjp2YXIoLS10eDIpfQoud2hlZWwgLmNhcHtwb3NpdGlvbjphYnNvbHV0ZTtpbnNldDow"
    "O2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47anVzdGlmeS1jb250ZW50OmNlbnRl"
    "cjtwYWRkaW5nOjAgMTZweDsKICBiYWNrZ3JvdW5kOmxpbmVhci1ncmFkaWVudCg5MGRlZyx2YXIo"
    "LS1jYXJkKSAwIDU4JSx0cmFuc3BhcmVudCl9Ci53aGVlbCAuY2FwPip7bWF4LXdpZHRoOjYyJX0K"
    "LndoZWVsIC5jYXAgZW17Zm9udC1zdHlsZTpub3JtYWw7Zm9udC1zaXplOi42cmVtO2xldHRlci1z"
    "cGFjaW5nOi4xOGVtO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtjb2xvcjp2YXIoLS1hY2MpO2Zv"
    "bnQtd2VpZ2h0OjgwMH0KLndoZWVsIC5jYXAgYntmb250LWZhbWlseTp2YXIoLS1maGVhZCk7Zm9u"
    "dC1zaXplOjEuMDVyZW07Zm9udC13ZWlnaHQ6NzAwfQpAa2V5ZnJhbWVzIHNwaW57dG97dHJhbnNm"
    "b3JtOnJvdGF0ZSgzNjBkZWcpfX0KCi8qIHN1Y2Nlc3MgYnVyc3QgKi8KI2Z4e3Bvc2l0aW9uOmZp"
    "eGVkO2luc2V0OjA7ei1pbmRleDoxNTA7ZGlzcGxheTpub25lO3BsYWNlLWl0ZW1zOmNlbnRlcjtw"
    "b2ludGVyLWV2ZW50czpub25lfQojZngub257ZGlzcGxheTpncmlkfQojZnggLnJpbmd7d2lkdGg6"
    "ODJweDtoZWlnaHQ6ODJweDtib3JkZXItcmFkaXVzOjUwJTtib3JkZXI6MnB4IHNvbGlkIHZhcigt"
    "LW9rKTtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyOwogIGNvbG9yOnZhcigtLW9rKTth"
    "bmltYXRpb246YnVyc3QgLjc1cyBjdWJpYy1iZXppZXIoLjIsMS4zLC40LDEpfQojZnggLnJpbmcg"
    "c3Zne3dpZHRoOjM0cHg7aGVpZ2h0OjM0cHh9CiNmeCAud2F2ZXtwb3NpdGlvbjphYnNvbHV0ZTt3"
    "aWR0aDo4MnB4O2hlaWdodDo4MnB4O2JvcmRlci1yYWRpdXM6NTAlO2JvcmRlcjoycHggc29saWQg"
    "dmFyKC0tb2spO2FuaW1hdGlvbjp3YXZlIC45cyBlYXNlLW91dCBmb3J3YXJkc30KQGtleWZyYW1l"
    "cyBidXJzdHtmcm9te3RyYW5zZm9ybTpzY2FsZSguNCk7b3BhY2l0eTowfXRve3RyYW5zZm9ybTpz"
    "Y2FsZSgxKTtvcGFjaXR5OjF9fQpAa2V5ZnJhbWVzIHdhdmV7dG97dHJhbnNmb3JtOnNjYWxlKDMu"
    "NCk7b3BhY2l0eTowfX0KCi8qIG51bWJlciB0aWNrZXIgKi8KLnRpY2t7Zm9udC12YXJpYW50LW51"
    "bWVyaWM6dGFidWxhci1udW1zfQoKQG1lZGlhKHByZWZlcnMtcmVkdWNlZC1tb3Rpb246cmVkdWNl"
    "KXsqe2FuaW1hdGlvbjpub25lIWltcG9ydGFudDt0cmFuc2l0aW9uOm5vbmUhaW1wb3J0YW50fX0K"
    "bmF2I3RhYnN7bGVmdDowO3JpZ2h0OjA7Ym90dG9tOjA7dHJhbnNmb3JtOm5vbmU7bWF4LXdpZHRo"
    "OjEwMCV9bmF2I3RhYnMgLmRvY2t7Ym9yZGVyLXJhZGl1czowO2JvcmRlci1sZWZ0OjA7Ym9yZGVy"
    "LXJpZ2h0OjA7Ym9yZGVyLWJvdHRvbTowO3BhZGRpbmc6NnB4IDZweCBjYWxjKDZweCArIGVudihz"
    "YWZlLWFyZWEtaW5zZXQtYm90dG9tKSk7anVzdGlmeS1jb250ZW50OnNwYWNlLWFyb3VuZH1uYXYj"
    "dGFicyBidXR0b257ZmxleDoxfQoua3Bpe3Bvc2l0aW9uOnJlbGF0aXZlfS5rcGk6YWZ0ZXJ7Y29u"
    "dGVudDonJztwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjEzcHg7cmlnaHQ6MTNweDtib3R0b206MTBw"
    "eDtoZWlnaHQ6MnB4O2JvcmRlci1yYWRpdXM6MnB4O2JhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50"
    "KDkwZGVnLHZhcigtLWFjYyksdHJhbnNwYXJlbnQpfS5oZXJvIGgxe2ZvbnQtc2l6ZToxLjU1cmVt"
    "fS5jYXJke2JvcmRlci1jb2xvcjojMjMyODJkfQo8L3N0eWxlPgo8L2hlYWQ+Cjxib2R5PgoKPGNh"
    "bnZhcyBpZD0ia2ciPjwvY2FudmFzPgo8ZGl2IGlkPSJzcGxhc2giPjxkaXYgY2xhc3M9Im1hcmsi"
    "IGlkPSJzcG1hcmsiPlpUPC9kaXY+PGRpdiBjbGFzcz0iZG90Z3JpZCIgaWQ9ImRnIj48L2Rpdj4K"
    "ICA8aDQgaWQ9InNwbmFtZSI+Wm9ybyBURyBTdG9yZTwvaDQ+PHNtYWxsIGlkPSJzcHRhZyI+UXVp"
    "ZXQsIGZhc3QsIHByZWNpc2U8L3NtYWxsPjwvZGl2Pgo8c3ZnIHdpZHRoPSIwIiBoZWlnaHQ9IjAi"
    "IHN0eWxlPSJwb3NpdGlvbjphYnNvbHV0ZSI+PGRlZnM+CiAgPGZpbHRlciBpZD0ibGciIHg9IjAl"
    "IiB5PSIwJSIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgY29sb3ItaW50ZXJwb2xhdGlvbi1m"
    "aWx0ZXJzPSJzUkdCIj4KICAgIDxmZVR1cmJ1bGVuY2UgdHlwZT0iZnJhY3RhbE5vaXNlIiBiYXNl"
    "RnJlcXVlbmN5PSIwLjA1IDAuMDUiIG51bU9jdGF2ZXM9IjEiIHNlZWQ9IjEiIHJlc3VsdD0idCIv"
    "PgogICAgPGZlR2F1c3NpYW5CbHVyIGluPSJ0IiBzdGREZXZpYXRpb249IjIiIHJlc3VsdD0iYiIv"
    "PgogICAgPGZlRGlzcGxhY2VtZW50TWFwIGluPSJTb3VyY2VHcmFwaGljIiBpbjI9ImIiIHNjYWxl"
    "PSI3MCIgeENoYW5uZWxTZWxlY3Rvcj0iUiIgeUNoYW5uZWxTZWxlY3Rvcj0iQiIvPgogIDwvZmls"
    "dGVyPjwvZGVmcz48L3N2Zz4KPGRpdiBpZD0iZngiPjxkaXYgY2xhc3M9IndhdmUiPjwvZGl2Pjxk"
    "aXYgY2xhc3M9InJpbmciPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJv"
    "a2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyLjQiIHN0cm9rZS1saW5lY2FwPSJyb3Vu"
    "ZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTIwIDYgOSAxN2wtNS01Ii8+PC9z"
    "dmc+PC9kaXY+PC9kaXY+CjxkaXYgaWQ9InN3Ij48ZW0+UHJldmlldzwvZW0+CiAgPGJ1dHRvbiBk"
    "YXRhLXN3PSJhcHAiIGNsYXNzPSJvbiI+U3RvcmU8L2J1dHRvbj4KICA8YnV0dG9uIGRhdGEtc3c9"
    "ImFkbWluIj5BZG1pbjwvYnV0dG9uPgo8L2Rpdj4KCjxkaXYgaWQ9InZpZXctYXBwIj4KPGRpdiBp"
    "ZD0ic2hlbGwiPgogIDxoZWFkZXIgaWQ9InRvcGJhciI+CiAgICA8ZGl2IGNsYXNzPSJsb2dvIj5a"
    "VDwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnJhbmQiPjxiIGlkPSJibmFtZSI+Wm9ybyBURyBTdG9y"
    "ZTwvYj48c3BhbiBpZD0iYnRhZyI+UXVpZXQsIGZhc3QsIHByZWNpc2U8L3NwYW4+PC9kaXY+CiAg"
    "ICA8YnV0dG9uIGNsYXNzPSJpY29uY2hpcCBoaWRkZW4iIGlkPSJjYXJ0Y2hpcCIgdGl0bGU9IkNh"
    "cnQiPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRD"
    "b2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5l"
    "am9pbj0icm91bmQiPjxwYXRoIGQ9Ik02IDIgMyA2djE0YTIgMiAwIDAgMCAyIDJoMTRhMiAyIDAg"
    "MCAwIDItMlY2bC0zLTR6Ii8+PHBhdGggZD0iTTMgNmgxOCIvPjxwYXRoIGQ9Ik0xNiAxMGE0IDQg"
    "MCAwIDEtOCAwIi8+PC9zdmc+PGkgaWQ9ImNhcnRuIj4wPC9pPjwvYnV0dG9uPgogICAgPGJ1dHRv"
    "biBjbGFzcz0iYmFsY2hpcCIgZGF0YS1nbz0id2FsbGV0Ij48c3BhbiBjbGFzcz0icGx1cyI+Kzwv"
    "c3Bhbj7igrk8c3BhbiBpZD0iYmFsIiBjbGFzcz0idGljayI+MjQ4LjUwPC9zcGFuPjwvYnV0dG9u"
    "PgogIDwvaGVhZGVyPgogIDxtYWluIGlkPSJzdGFnZSI+PC9tYWluPgo8L2Rpdj4KPG5hdiBpZD0i"
    "dGFicyI+PGRpdiBjbGFzcz0iZG9jayI+PHNwYW4gY2xhc3M9ImRwaWxsIiBpZD0iZHBpbGwiPjwv"
    "c3Bhbj4KICA8YnV0dG9uIGRhdGEtdGFiPSJzaG9wIiBjbGFzcz0ib24iPjxzdmcgdmlld0JveD0i"
    "MCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRo"
    "PSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRo"
    "IGQ9Ik02IDIgMyA2djE0YTIgMiAwIDAgMCAyIDJoMTRhMiAyIDAgMCAwIDItMlY2bC0zLTR6Ii8+"
    "PHBhdGggZD0iTTMgNmgxOCIvPjxwYXRoIGQ9Ik0xNiAxMGE0IDQgMCAwIDEtOCAwIi8+PC9zdmc+"
    "PHNwYW4gZGF0YS1uYXY9InNob3AiPlNob3A8L3NwYW4+PC9idXR0b24+CiAgPGJ1dHRvbiBkYXRh"
    "LXRhYj0ib3JkZXJzIj48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tl"
    "PSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBz"
    "dHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMjEgOGEyIDIgMCAwIDAtMS0xLjczbC03"
    "LTRhMiAyIDAgMCAwLTIgMGwtNyA0QTIgMiAwIDAgMCAzIDh2OGEyIDIgMCAwIDAgMSAxLjczbDcg"
    "NGEyIDIgMCAwIDAgMiAwbDctNEEyIDIgMCAwIDAgMjEgMTZaIi8+PHBhdGggZD0ibTMuMyA3IDgu"
    "NyA1IDguNy01Ii8+PHBhdGggZD0iTTEyIDIyVjEyIi8+PC9zdmc+PHNwYW4gZGF0YS1uYXY9Im9y"
    "ZGVycyI+T3JkZXJzPC9zcGFuPjwvYnV0dG9uPgogIDxidXR0b24gZGF0YS10YWI9ImVhcm4iPjxz"
    "dmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIg"
    "c3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0i"
    "cm91bmQiPjxwYXRoIGQ9Ik0xMyAyIDMgMTRoOWwtMSA4IDEwLTEyaC05eiIvPjwvc3ZnPjxzcGFu"
    "IGRhdGEtbmF2PSJlYXJuIj5FYXJuPC9zcGFuPjwvYnV0dG9uPgogIDxidXR0b24gZGF0YS10YWI9"
    "IndhbGxldCI+PHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3Vy"
    "cmVudENvbG9yIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tl"
    "LWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTE5IDdWNWEyIDIgMCAwIDAtMi0ySDVhMiAyIDAg"
    "MCAwIDAgNGgxNWEyIDIgMCAwIDEgMiAydjlhMiAyIDAgMCAxLTIgMkg1YTIgMiAwIDAgMS0yLTJW"
    "NSIvPjxwYXRoIGQ9Ik0xOCAxMmguMDEiLz48L3N2Zz48c3BhbiBkYXRhLW5hdj0id2FsbGV0Ij5X"
    "YWxsZXQ8L3NwYW4+PC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXRhYj0ieW91Ij48c3ZnIHZpZXdC"
    "b3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13"
    "aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48"
    "cGF0aCBkPSJNMTkgMjF2LTJhNCA0IDAgMCAwLTQtNEg5YTQgNCAwIDAgMC00IDR2MiIvPjxjaXJj"
    "bGUgY3g9IjEyIiBjeT0iNyIgcj0iNCIvPjwvc3ZnPjxzcGFuIGRhdGEtbmF2PSJ5b3UiPllvdTwv"
    "c3Bhbj48L2J1dHRvbj4KPC9kaXY+PC9uYXY+CjwvZGl2PgoKPGRpdiBpZD0idmlldy1hZG1pbiIg"
    "Y2xhc3M9ImhpZGRlbiI+PGRpdiBpZD0ic2hlbGwiPgogIDxoZWFkZXIgaWQ9InRvcGJhciI+CiAg"
    "ICA8ZGl2IGNsYXNzPSJsb2dvIj48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIg"
    "c3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJv"
    "dW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMjAgMTNjMCA1LTMuNSA3LjUt"
    "Ny42NiA4Ljk1YTEgMSAwIDAgMS0uNjctLjAxQzcuNSAyMC41IDQgMTggNCAxM1Y2YTEgMSAwIDAg"
    "MSAxLTFjMiAwIDQuNS0xLjIgNi4yNC0yLjcyYTEuMTcgMS4xNyAwIDAgMSAxLjUyIDBDMTQuNTEg"
    "My44MSAxNyA1IDE5IDVhMSAxIDAgMCAxIDEgMXoiLz48L3N2Zz48L2Rpdj4KICAgIDxkaXYgY2xh"
    "c3M9ImJyYW5kIj48Yj5BZG1pbiBDb25zb2xlPC9iPjxzcGFuIGlkPSJhYm5hbWUiPlpvcm8gVEcg"
    "U3RvcmU8L3NwYW4+PC9kaXY+CiAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+PGkgY2xhc3M9ImRv"
    "dCI+PC9pPkxpdmU8L3NwYW4+CiAgPC9oZWFkZXI+CiAgPGRpdiBjbGFzcz0ic3VibmF2IiBpZD0i"
    "YXN1YiI+PHNwYW4gY2xhc3M9InZwaWxsIiBpZD0idnBpbGwiPjwvc3Bhbj4KICAgIDxidXR0b24g"
    "ZGF0YS1hPSJkYXNoIiBjbGFzcz0ib24iPkRhc2hib2FyZDwvYnV0dG9uPgogICAgPGJ1dHRvbiBk"
    "YXRhLWE9ImNoYW5uZWxzIj5DaGFubmVsczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9Imxv"
    "Z3MiPkxvZ3M8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJwYXkiPkF1dG8tUGF5PC9idXR0"
    "b24+CiAgICA8YnV0dG9uIGRhdGEtYT0iY29udGVudCI+Q29udGVudDwvYnV0dG9uPgogICAgPGJ1"
    "dHRvbiBkYXRhLWE9ImZlYXR1cmVzIj5GZWF0dXJlczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRh"
    "LWE9Im1hcmtldCI+TWFya2V0aW5nPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0iYW5hbHl0"
    "aWNzIj5BbmFseXRpY3M8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJzZWN1cml0eSI+U2Vj"
    "dXJpdHk8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJzdG9jayI+U3RvY2s8L2J1dHRvbj4K"
    "ICAgIDxidXR0b24gZGF0YS1hPSJvcmRlcnMiPk9yZGVyczwvYnV0dG9uPgogICAgPGJ1dHRvbiBk"
    "YXRhLWE9InVzZXJzIj5Vc2VyczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9InNldHRpbmdz"
    "Ij5TZXR0aW5nczwvYnV0dG9uPgogIDwvZGl2PgogIDxtYWluIGlkPSJhc3RhZ2UiPjwvbWFpbj4K"
    "PC9kaXY+PC9kaXY+Cgo8ZGl2IGlkPSJzaGVldCI+PGRpdiBjbGFzcz0iYmciIGRhdGEtY2xvc2U+"
    "PC9kaXY+PGRpdiBjbGFzcz0icGFuZWwiIGlkPSJwYW5lbCI+PC9kaXY+PC9kaXY+CjxkaXYgY2xh"
    "c3M9InRvYXN0IiBpZD0idG9hc3QiPjwvZGl2PgoKPHNjcmlwdD4KLyog4pSA4pSAIGxpdmUtbW9k"
    "ZSBzdGF0ZSwgZGVjbGFyZWQgZmlyc3Qgc28gbm90aGluZyBjYW4gcmVhZCBpdCB0b28gZWFybHkg"
    "4pSA4pSAICovCnZhciBMSVZFPWZhbHNlLExPQ0tFRD1mYWxzZSxNRT17YWRtaW46ZmFsc2Usb3du"
    "ZXI6ZmFsc2UscGVybXM6e319Owp2YXIgVEc9d2luZG93LlRlbGVncmFtJiZ3aW5kb3cuVGVsZWdy"
    "YW0uV2ViQXBwfHxudWxsOwpmdW5jdGlvbiByZWFkSW5pdERhdGEoKXsKICB0cnl7aWYoVEcmJlRH"
    "LmluaXREYXRhJiZURy5pbml0RGF0YS5sZW5ndGgpcmV0dXJuIFRHLmluaXREYXRhO31jYXRjaChl"
    "KXt9CiAgLyogVGVsZWdyYW0gYWxzbyBwdXRzIGl0IGluIHRoZSBVUkwsIHNvIHRoZSBhcHAgc3Rp"
    "bGwgd29ya3MgaWYgdGhlIFNESyBpcyBibG9ja2VkICovCiAgdHJ5ewogICAgY29uc3QgbT0vWyMm"
    "P110Z1dlYkFwcERhdGE9KFteJl0qKS8uZXhlYyhsb2NhdGlvbi5oYXNofHwiIik7CiAgICBpZiht"
    "JiZtWzFdKXJldHVybiBkZWNvZGVVUklDb21wb25lbnQobVsxXSk7CiAgfWNhdGNoKGUpe30KICB0"
    "cnl7CiAgICBjb25zdCBxPW5ldyBVUkxTZWFyY2hQYXJhbXMobG9jYXRpb24uc2VhcmNoKTsKICAg"
    "IGNvbnN0IHY9cS5nZXQoInRnV2ViQXBwRGF0YSIpOwogICAgaWYodilyZXR1cm4gdjsKICB9Y2F0"
    "Y2goZSl7fQogIHRyeXsKICAgIGNvbnN0IHNhdmVkPXNlc3Npb25TdG9yYWdlLmdldEl0ZW0oInRn"
    "SW5pdCIpOwogICAgaWYoc2F2ZWQpcmV0dXJuIHNhdmVkOwogIH1jYXRjaChlKXt9CiAgcmV0dXJu"
    "ICIiOwp9CmZ1bmN0aW9uIGluaXRpYWxzKG5hbWUpewogIGNvbnN0IHc9KG5hbWV8fCIiKS50cmlt"
    "KCkuc3BsaXQoL1xzKy8pLmZpbHRlcihCb29sZWFuKTsKICBpZighdy5sZW5ndGgpcmV0dXJuICJT"
    "VCI7CiAgcmV0dXJuICgod1swXVswXXx8IiIpKyh3Lmxlbmd0aD4xPyh3WzFdWzBdfHwiIik6KHdb"
    "MF1bMV18fCIiKSkpLnRvVXBwZXJDYXNlKCk7Cn0KZnVuY3Rpb24gcGFpbnRCcmFuZChuYW1lLHRh"
    "Zyl7CiAgaWYobmFtZSl7CiAgICBjb25zdCBuPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzcG5h"
    "bWUiKTsgaWYobiluLnRleHRDb250ZW50PW5hbWU7CiAgICBjb25zdCBtPWRvY3VtZW50LmdldEVs"
    "ZW1lbnRCeUlkKCJzcG1hcmsiKTsgaWYobSltLnRleHRDb250ZW50PWluaXRpYWxzKG5hbWUpOwog"
    "ICAgY29uc3QgYj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYm5hbWUiKTsgaWYoYiliLnRleHRD"
    "b250ZW50PW5hbWU7CiAgICBjb25zdCBhYj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYWJuYW1l"
    "Iik7IGlmKGFiKWFiLnRleHRDb250ZW50PW5hbWU7CiAgICBkb2N1bWVudC50aXRsZT1uYW1lOwog"
    "IH0KICBpZih0YWcpewogICAgY29uc3QgdD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3B0YWci"
    "KTsgaWYodCl0LnRleHRDb250ZW50PXRhZzsKICAgIGNvbnN0IGJ0PWRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJidGFnIik7IGlmKGJ0KWJ0LnRleHRDb250ZW50PXRhZzsKICB9Cn0KdHJ5ewogIGNv"
    "bnN0IGNuPWxvY2FsU3RvcmFnZS5nZXRJdGVtKCJzdG9yZU5hbWUiKSwgY3Q9bG9jYWxTdG9yYWdl"
    "LmdldEl0ZW0oInN0b3JlVGFnIik7CiAgaWYoY258fGN0KXBhaW50QnJhbmQoY24sY3QpOwp9Y2F0"
    "Y2goZSl7fQp2YXIgSU5JVD1yZWFkSW5pdERhdGEoKTsKdHJ5e2lmKElOSVQpc2Vzc2lvblN0b3Jh"
    "Z2Uuc2V0SXRlbSgidGdJbml0IixJTklUKTt9Y2F0Y2goZSl7fQpmdW5jdGlvbiByZWFkQXBwS2V5"
    "KCl7CiAgdHJ5ewogICAgY29uc3QgcT1uZXcgVVJMU2VhcmNoUGFyYW1zKGxvY2F0aW9uLnNlYXJj"
    "aCk7CiAgICBpZihxLmdldCgiayIpKXJldHVybiBxLmdldCgiayIpOwogICAgY29uc3QgbT0vWyMm"
    "P11rPShbXiZdKikvLmV4ZWMobG9jYXRpb24uaGFzaHx8IiIpOwogICAgaWYobSYmbVsxXSlyZXR1"
    "cm4gZGVjb2RlVVJJQ29tcG9uZW50KG1bMV0pOwogICAgcmV0dXJuIHNlc3Npb25TdG9yYWdlLmdl"
    "dEl0ZW0oImFwcEtleSIpfHwiIjsKICB9Y2F0Y2goZSl7cmV0dXJuICIiO30KfQp2YXIgQVBQS0VZ"
    "PXJlYWRBcHBLZXkoKTsKdHJ5e2lmKEFQUEtFWSlzZXNzaW9uU3RvcmFnZS5zZXRJdGVtKCJhcHBL"
    "ZXkiLEFQUEtFWSk7fWNhdGNoKGUpe30KLyogZGVtbyBkYXRhIG9ubHkg4oCUIG5vIHRva2Vucywg"
    "bm8gVVBJIElELCBubyBHbWFpbCBjcmVkZW50aWFscyAqLwpsZXQgQ0ZHPXtuYW1lOiJab3JvIFRH"
    "IFN0b3JlIix0YWc6IlF1aWV0LCBmYXN0LCBwcmVjaXNlIixjdXI6IuKCuSJ9OwovKiDilIDilIAg"
    "RWRpdGFibGUgY29weSDigJQgZXZlcnkgc3RyaW5nIGJlbG93IGlzIGVkaXRhYmxlIGluIEFkbWlu"
    "IOKWuCBDb250ZW50IOKUgOKUgCAqLwpjb25zdCBDTVM9ewogc3RvcmVOYW1lOiJab3JvIFRHIFN0"
    "b3JlIiwgc3RvcmVUYWc6IlF1aWV0LCBmYXN0LCBwcmVjaXNlIiwKIGVtcHR5VGl0bGU6IlJlc3Rv"
    "Y2tpbmciLCBlbXB0eUJvZHk6IkV2ZXJ5dGhpbmcganVzdCBzb2xkIG91dC4gRnJlc2ggYWNjb3Vu"
    "dHMgbGFuZCBoZXJlIHNob3J0bHkg4oCUIHR1cm4gb24gYW4gYWxlcnQgYW5kIHlvdSB3aWxsIGhl"
    "YXIgZmlyc3QuIiwKIHRpY2sxOiI8Yj5JbnN0YW50IGRlbGl2ZXJ5PC9iPiBvbiBldmVyeSBjb3Vu"
    "dHJ5IiwgdGljazI6IlRvcC11cHMgPGI+Y29uZmlybWVkIGF1dG9tYXRpY2FsbHk8L2I+IiwKIHRp"
    "Y2szOiJGYWlsZWQgbG9naW4/IDxiPkF1dG8gcmVmdW5kPC9iPiIsIHRpY2s0OiJEYWlseSBjaGVj"
    "ay1pbiA8Yj7igrkwLjQwPC9iPiIsCiBoZXJvS2ljazoiU3RvY2sgZmVlZCIsIGhlcm9IMToiTGVz"
    "cyBub2lzZS48YnI+TW9yZSBkZWxpdmVyeS4iLAogaGVyb1N1YjoiSW5zdGFudCBkZWxpdmVyeSwg"
    "T1RQIGluLWFwcCwgd2FsbGV0IHRvcC11cHMgdmVyaWZpZWQgYXV0b21hdGljYWxseS4ge259IGNv"
    "dW50cmllcyBsaXZlLiIsCiBoZXJvQnRuMToiQnJvd3NlIFN0b2NrIiwgaGVyb0J0bjI6IkFkZCBG"
    "dW5kcyIsCiBmZWF0MToifjhzIiwgZmVhdDFzOiJkZWxpdmVyeSIsIGZlYXQyOiJBdXRvIiwgZmVh"
    "dDJzOiJyZWZ1bmRzIiwgZmVhdDM6IlVQSSIsIGZlYXQzczoiYXV0by12ZXJpZnkiLAogZmxhc2hQ"
    "aWxsOiJGbGFzaCDiiJIyMCUiLCBmbGFzaFRleHQ6IkVuZHMgaW4iLCBmbGFzaEJ0bjoiVmlldyIs"
    "CiB3aGVlbEtpY2s6IlJvdGF0aW5nIGRlYWxzIiwgd2hlZWxUaXRsZToiVXAgdG8gMjglIG9mZiIs"
    "IHdoZWVsV29yZHM6IklOU1RBTlQsIEFVVE8tVkVSSUZZLCBSRUZVTkRTLCAyNMOXNyIsCiBzaG9w"
    "SGVhZDoiSW4gU3RvY2siLCBzaG9wU29ydDoiU29ydCIsIHNlYXJjaFBoOiJTZWFyY2ggY291bnRy"
    "eSBvciBjb2Rl4oCmIiwgYWxlcnRzQnRuOiJBbGVydHMiLAogd2FsS2ljazoiQmFsYW5jZSIsIHdh"
    "bFN1YjoiUGF5IGJ5IFVQSSDigJQgdGFwIFZlcmlmeSBhZnRlciBwYXlpbmcgYW5kIHlvdXIgd2Fs"
    "bGV0IGlzIGNyZWRpdGVkIGluIHNlY29uZHMuIiwKIHdhbEFkZDoiQWRkIEZ1bmRzIiwgcGF5VGl0"
    "bGU6IlBheW1lbnQgRGV0YWlscyIsIHBheVN1YjoiQWRtaW4gc2V0cyB0aGUgVVBJIGhhbmRsZSDi"
    "gJQgbm90aGluZyBpcyBoYXJkLWNvZGVkIGluIHRoZSBhcHAuIiwKIHBheVByZWZpeDoiUEFZIiwg"
    "cGF5V2luZG93OiI1IiwKIHNoZWV0VGl0bGU6IkFkZCIsIHNoZWV0U3ViOiJQYXkgdGhlIGV4YWN0"
    "IGFtb3VudCwgdGhlbiB0YXAgVmVyaWZ5IFBheW1lbnQuIFRoZSByZXF1ZXN0IGV4cGlyZXMgaW4g"
    "e219IG1pbnV0ZXMuIiwKIHBheUJ0bjoiUGF5IE5vdyIsIHZlcmlmeUJ0bjoiVmVyaWZ5IFBheW1l"
    "bnQiLCB1dHJCdG46Ik5vdCBkZXRlY3RlZD8gU3VibWl0IFVUUiIsCiBzdGVwMToiUGF5bWVudCBy"
    "ZXF1ZXN0IGNyZWF0ZWQiLCBzdGVwMjoiUGF5IHRoZSBleGFjdCBhbW91bnQgaW4gYW55IFVQSSBh"
    "cHAiLAogc3RlcDM6IlRhcCBWZXJpZnkg4oCUIHdlIGNvbmZpcm0geW91ciBwYXltZW50Iiwgc3Rl"
    "cDQ6IldhbGxldCBjcmVkaXRlZCIsCiBtc2dDaGVja2luZzoiQ2hlY2tpbmcgeW91ciBwYXltZW50"
    "4oCmIiwgbXNnT2s6IlZlcmlmaWVkIOKAlCB3YWxsZXQgY3JlZGl0ZWQiLAogbXNnTm9uZToiTm8g"
    "cGF5bWVudCBmb3VuZCB5ZXQuIFBheSBmaXJzdCwgdGhlbiB0YXAgVmVyaWZ5LiIsIG1zZ0V4cGly"
    "ZWQ6IlJlcXVlc3QgZXhwaXJlZCDigJQgY3JlYXRlIGEgbmV3IG9uZS4iLAogbmF2U2hvcDoiU2hv"
    "cCIsIG5hdk9yZGVyczoiT3JkZXJzIiwgbmF2RWFybjoiRWFybiIsIG5hdldhbGxldDoiV2FsbGV0"
    "IiwgbmF2WW91OiJZb3UiCn07CmNvbnN0IENNUzA9T2JqZWN0LmFzc2lnbih7fSxDTVMpOwpjb25z"
    "dCBDTVNNRVRBPVsKIFsiU3RvcmUgaGVhZGVyIixbWyJzdG9yZU5hbWUiLCJTdG9yZSBuYW1lIl0s"
    "WyJzdG9yZVRhZyIsIlRhZ2xpbmUiXV1dLAogWyJTY3JvbGxpbmcgdGlja2VyIixbWyJ0aWNrMSIs"
    "IlRpY2tlciAxIl0sWyJ0aWNrMiIsIlRpY2tlciAyIl0sWyJ0aWNrMyIsIlRpY2tlciAzIl0sWyJ0"
    "aWNrNCIsIlRpY2tlciA0Il1dXSwKIFsiSGVybyBzZWN0aW9uIixbWyJoZXJvS2ljayIsIlNtYWxs"
    "IGxhYmVsIGFib3ZlIHRpdGxlIl0sWyJoZXJvSDEiLCJCaWcgdGl0bGUiXSxbImhlcm9TdWIiLCJQ"
    "YXJhZ3JhcGggdW5kZXIgdGhlIHRpdGxlIl0sWyJoZXJvQnRuMSIsIkxlZnQgYnV0dG9uIl0sWyJo"
    "ZXJvQnRuMiIsIlJpZ2h0IGJ1dHRvbiJdXV0sCiBbIkZlYXR1cmUgYmFkZ2VzIixbWyJmZWF0MSIs"
    "IkJhZGdlIDEgdmFsdWUiXSxbImZlYXQxcyIsIkJhZGdlIDEgY2FwdGlvbiJdLFsiZmVhdDIiLCJC"
    "YWRnZSAyIHZhbHVlIl0sWyJmZWF0MnMiLCJCYWRnZSAyIGNhcHRpb24iXSxbImZlYXQzIiwiQmFk"
    "Z2UgMyB2YWx1ZSJdLFsiZmVhdDNzIiwiQmFkZ2UgMyBjYXB0aW9uIl1dXSwKIFsiRmxhc2ggc2Fs"
    "ZSBzdHJpcCIsW1siZmxhc2hQaWxsIiwiUGlsbCB0ZXh0Il0sWyJmbGFzaFRleHQiLCJDb3VudGRv"
    "d24gbGFiZWwiXSxbImZsYXNoQnRuIiwiQnV0dG9uIl1dXSwKIFsiUm90YXRpbmcgZGVhbHMgY2Fy"
    "ZCIsW1sid2hlZWxLaWNrIiwiU21hbGwgbGFiZWwiXSxbIndoZWVsVGl0bGUiLCJIZWFkbGluZSJd"
    "LFsid2hlZWxXb3JkcyIsIk1hcnF1ZWUgd29yZHMgKGNvbW1hIHNlcGFyYXRlZCkiXV1dLAogWyJT"
    "aG9wIGxpc3QiLFtbInNob3BIZWFkIiwiU2VjdGlvbiBoZWFkaW5nIl0sWyJzaG9wU29ydCIsIlNv"
    "cnQgbGluayJdLFsic2VhcmNoUGgiLCJTZWFyY2ggcGxhY2Vob2xkZXIiXSxbImFsZXJ0c0J0biIs"
    "IkFsZXJ0cyBidXR0b24iXV1dLAogWyJXYWxsZXQgc2NyZWVuIixbWyJ3YWxLaWNrIiwiTGFiZWwg"
    "YWJvdmUgYmFsYW5jZSJdLFsid2FsU3ViIiwiQmFsYW5jZSBkZXNjcmlwdGlvbiJdLFsid2FsQWRk"
    "IiwiQWRkIGZ1bmRzIGJ1dHRvbiJdLFsicGF5VGl0bGUiLCJQYXltZW50IGNhcmQgdGl0bGUiXSxb"
    "InBheVN1YiIsIlBheW1lbnQgY2FyZCBkZXNjcmlwdGlvbiJdXV0sCiBbIlBheW1lbnQgc2hlZXQi"
    "LFtbInNoZWV0VGl0bGUiLCJTaGVldCB0aXRsZSBwcmVmaXgiXSxbInNoZWV0U3ViIiwiU2hlZXQg"
    "ZGVzY3JpcHRpb24gKHttfSA9IG1pbnV0ZXMpIl0sWyJwYXlCdG4iLCJQYXkgYnV0dG9uIl0sWyJ2"
    "ZXJpZnlCdG4iLCJWZXJpZnkgYnV0dG9uIl0sWyJ1dHJCdG4iLCJNYW51YWwgZmFsbGJhY2sgYnV0"
    "dG9uIl0sWyJwYXlQcmVmaXgiLCJSZWZlcmVuY2UgcHJlZml4Il0sWyJwYXlXaW5kb3ciLCJQYXlt"
    "ZW50IHdpbmRvdyAobWludXRlcykiXV1dLAogWyJQYXltZW50IHN0ZXBzIixbWyJzdGVwMSIsIlN0"
    "ZXAgMSJdLFsic3RlcDIiLCJTdGVwIDIiXSxbInN0ZXAzIiwiU3RlcCAzIl0sWyJzdGVwNCIsIlN0"
    "ZXAgNCJdXV0sCiBbIlBheW1lbnQgbWVzc2FnZXMiLFtbIm1zZ0NoZWNraW5nIiwiV2hpbGUgY2hl"
    "Y2tpbmciXSxbIm1zZ09rIiwiVmVyaWZpZWQiXSxbIm1zZ05vbmUiLCJOb3RoaW5nIGZvdW5kIl0s"
    "WyJtc2dFeHBpcmVkIiwiRXhwaXJlZCJdXV0sCiBbIkJvdHRvbSBuYXZpZ2F0aW9uIixbWyJuYXZT"
    "aG9wIiwiVGFiIDEiXSxbIm5hdk9yZGVycyIsIlRhYiAyIl0sWyJuYXZFYXJuIiwiVGFiIDMiXSxb"
    "Im5hdldhbGxldCIsIlRhYiA0Il0sWyJuYXZZb3UiLCJUYWIgNSJdXV0KXTsKLyog4pWQ4pWQIEZF"
    "QVRVUkUgRkxBR1Mg4oCUIGV2ZXJ5IGZlYXR1cmUgYmVsb3cgaXMgb2ZmLWFibGU7IHdoZW4gb2Zm"
    "IG5vdGhpbmcgcmVuZGVycyDilZDilZAgKi8KY29uc3QgRkdST1VQUz1bCiBbIlBheW1lbnRzICYg"
    "d2FsbGV0IixbCiAgWyJjcnlwdG8iLCJDcnlwdG8gLyBVU0RUIHRvcC11cCIsIkFjY2VwdCBVU0RU"
    "LVRSQzIwLCBCVEMgZXRjLiB3aXRoIGF1dG8tY29uZmlybSBmcm9tIHRoZSBleHBsb3Jlci4iXSwK"
    "ICBbInVwaVJvdGF0ZSIsIk11bHRpcGxlIFVQSSBJRHMgKyByb3RhdGlvbiIsIlJvdGF0ZSBoYW5k"
    "bGVzIHBlciBwYXltZW50IG9yIGF1dG8tc3dpdGNoIHdoZW4gb25lIGlzIGxpbWl0ZWQuIl0sCiAg"
    "WyJhdXRvUmVmdW5kIiwiQXV0by1yZWZ1bmQgdG8gd2FsbGV0IiwiRGVhZCBhY2NvdW50IGluc2lk"
    "ZSB0aGUgd2FycmFudHkgd2luZG93IGlzIHJlZnVuZGVkIHdpdGhvdXQgc3VwcG9ydC4iXSwKICBb"
    "InNwbGl0UGF5IiwiU3BsaXQgcGF5bWVudCIsIlBheSBwYXJ0IGZyb20gYmFsYW5jZSBhbmQgdGhl"
    "IHJlc3QgYnkgVVBJIGluIG9uZSBmbG93LiJdLAogIFsibGVkZ2VyIiwiV2FsbGV0IGxlZGdlciBz"
    "dGF0ZW1lbnQiLCJGdWxsIGNyZWRpdC9kZWJpdCBoaXN0b3J5IHdpdGggcnVubmluZyBiYWxhbmNl"
    "IGFuZCBDU1YgZXhwb3J0LiJdLAogIFsiZGVwTGltaXRzIiwiTWluIC8gbWF4IGRlcG9zaXQgcnVs"
    "ZXMiLCJHbG9iYWwgb3IgcGVyLXVzZXIgY2FwcyBvbiBhIHNpbmdsZSB0b3AtdXAuIl0sCiAgWyJk"
    "ZXBCb251cyIsIkRlcG9zaXQgYm9udXMgdGllcnMiLCJBZGQg4oK5NTAwIGdldCA1JSBleHRyYSDi"
    "gJQgY29uZmlndXJhYmxlIHNsYWJzLCBhdXRvLWFwcGxpZWQuIl0sCiAgWyJ3cm9uZ0FtdCIsIldy"
    "b25nLWFtb3VudCBoYW5kbGluZyIsIkNyZWRpdCB3aGF0ZXZlciB3YXMgYWN0dWFsbHkgcGFpZCBh"
    "bmQgZmxhZyB0aGUgbWlzbWF0Y2guIl1dXSwKIFsiQ2F0YWxvZyAmIHNlbGxpbmciLFsKICBbImNh"
    "cnQiLCJTaG9wcGluZyBjYXJ0IC8gYnVsayBidXkiLCJNaXggY291bnRyaWVzIGluIG9uZSBjaGVj"
    "a291dCwgb25lIGNvbmZpcm1hdGlvbiwgb25lIGRlYml0LiJdLAogIFsiYnVsa0Rpc2MiLCJCdWxr"
    "IHB1cmNoYXNlIGRpc2NvdW50IiwiQXV0b21hdGljIHByaWNlIGJyZWFrIGF0IDUgLyAxMCAvIDI1"
    "IHVuaXRzLiJdLAogIFsid2lzaGxpc3QiLCJXaXNobGlzdCAvIGZhdm91cml0ZXMiLCJTYXZlIGNv"
    "dW50cmllcyBhbmQgcmVidXkgaW4gb25lIHRhcC4iXSwKICBbImR5blByaWNlIiwiRHluYW1pYyBw"
    "cmljaW5nIiwiUHJpY2UgbW92ZXMgd2l0aCBzdG9jayBsZXZlbCwgYWdlIHRpZXIgb3IgdGltZSBv"
    "ZiBkYXkuIl0sCiAgWyJmbGFzaFNhbGUiLCJGbGFzaCBzYWxlIiwiVGltZWQgZGlzY291bnQgd2l0"
    "aCBhIGxpdmUgY291bnRkb3duIG9uIHRoZSBzdG9yZWZyb250LiJdLAogIFsiY291cG9ucyIsIkNv"
    "dXBvbiBjb2RlcyIsIlBlcmNlbnRhZ2Ugb2ZmIGF0IGNoZWNrb3V0IHdpdGggbGltaXRzIGFuZCBl"
    "eHBpcnkuIl0sCiAgWyJ2YXJpYW50cyIsIkFjY291bnQgdmFyaWFudHMiLCJTcGFtLWJsb2NrLCAy"
    "RkEsIHVzZXJuYW1lLCBtYWlsIOKAlCBmaWx0ZXJhYmxlIGFuZCBwcmljZWQgYXBhcnQuIl0sCiAg"
    "WyJoZWFsdGhDaGVjayIsIlN0b2NrIGhlYWx0aCBjaGVjayIsIlNlc3Npb24gaXMgdmFsaWRhdGVk"
    "IGxpdmUgYmVmb3JlIGl0IGlzIHNvbGQuIl1dXSwKIFsiT3JkZXJzICYgYWZ0ZXItc2FsZSIsWwog"
    "IFsidGltZWxpbmUiLCJPcmRlciBzdGF0dXMgdGltZWxpbmUiLCJQZW5kaW5nLCBkZWxpdmVyZWQs"
    "IE9UUCByZWNlaXZlZCwgY2xvc2VkIOKAlCB2aXNpYmxlIHRvIGJvdGggc2lkZXMuIl0sCiAgWyJ3"
    "YXJyYW50eSIsIlJlcGxhY2VtZW50IC8gd2FycmFudHkiLCJSZXBvcnQgYSBwcm9ibGVtIGluc2lk"
    "ZSBYIGhvdXJzIGZvciBhbiBhdXRvLXJlcGxhY2VtZW50LiJdLAogIFsicmVkb3dubG9hZCIsIlJl"
    "LWRvd25sb2FkIGRldGFpbHMiLCJGZXRjaCBhIHBhc3Qgb3JkZXIncyBkZXRhaWxzIGFnYWluLCBy"
    "YXRlLWxpbWl0ZWQuIl0sCiAgWyJkaXNwdXRlIiwiRGlzcHV0ZSBmbG93IiwiUmVhc29uIHBpY2tl"
    "ciBwbHVzIGV2aWRlbmNlIGluc3RlYWQgb2YgZnJlZS10ZXh0IHRpY2tldHMuIl0sCiAgWyJzZXNz"
    "aW9uRmlsZSIsIlNlc3Npb24gZmlsZSBkZWxpdmVyeSIsIlNlbmQgLnNlc3Npb24gLyBKU09OIGFz"
    "IGEgZG9jdW1lbnQgd2l0aCBhIHNlbGYtZGVzdHJ1Y3QgdGltZXIuIl0sCiAgWyJyZWNlaXB0cyIs"
    "Ik9yZGVyIHJlY2VpcHRzIiwiQXV0by1nZW5lcmF0ZWQgaW52b2ljZSBwZXIgcHVyY2hhc2UuIl1d"
    "XSwKIFsiVHJ1c3QgJiBzb2NpYWwgcHJvb2YiLFsKICBbInJldmlld3MiLCJSZXZpZXdzICYgc3Rh"
    "ciByYXRpbmdzIiwiQnV5ZXIgcmF0ZXMgYWZ0ZXIgZGVsaXZlcnk7IGF2ZXJhZ2VzIHNob3cgb24g"
    "ZXZlcnkgY291bnRyeS4iXSwKICBbInNhbGVzRmVlZCIsIkxpdmUgc2FsZXMgZmVlZCIsIlJlY2Vu"
    "dCBzYWxlcyBzaG93biBpbi1hcHAgYW5kIGF1dG8tcG9zdGVkIHRvIHlvdXIgY2hhbm5lbC4iXSwK"
    "ICBbImxlYWRlcmJvYXJkIiwiTGVhZGVyYm9hcmQiLCJUb3AgYnV5ZXJzIGFuZCByZWZlcnJlcnMg"
    "b2YgdGhlIHdlZWssIHdpdGggYSBwcml6ZS4iXSwKICBbImJ1eWVyQmFkZ2UiLCJWZXJpZmllZCBi"
    "dXllciBiYWRnZSIsIkJhZGdlIGFuZCBwZXJrcyBhZnRlciBOIGNvbXBsZXRlZCBvcmRlcnMuIl1d"
    "XSwKIFsiR3Jvd3RoICYgcmV0ZW50aW9uIixbCiAgWyJyZWZMMiIsIk11bHRpLWxldmVsIHJlZmVy"
    "cmFsIiwiTGV2ZWwgMSBhbmQgbGV2ZWwgMiBjb21taXNzaW9uIGluc3RlYWQgb2YgYSBmbGF0IHJh"
    "dGUuIl0sCiAgWyJyZWZEYXNoIiwiUmVmZXJyYWwgZGFzaGJvYXJkIiwiV2hvIGpvaW5lZCwgd2hv"
    "IGRlcG9zaXRlZCBhbmQgd2hhdCBlYWNoIGludml0ZSBlYXJuZWQuIl0sCiAgWyJzdHJlYWsiLCJD"
    "aGVjay1pbiBzdHJlYWsgcmV3YXJkcyIsIkVzY2FsYXRpbmcgZGFpbHkgcmV3YXJkIHRoYXQgcmVz"
    "ZXRzIHdoZW4gYnJva2VuLiJdLAogIFsibG95YWx0eSIsIkxveWFsdHkgcG9pbnRzIiwiRWFybiBw"
    "ZXIg4oK5IHNwZW50LCByZWRlZW0gZm9yIGJhbGFuY2Ugb3IgYSBmcmVlIGFjY291bnQuIl0sCiAg"
    "WyJ3aW5iYWNrIiwiUmUtZW5nYWdlbWVudCBETXMiLCJBdXRvIG1lc3NhZ2UgdXNlcnMgaW5hY3Rp"
    "dmUgZm9yIDcgLyAzMCBkYXlzLiJdLAogIFsiYWJhbmRvblJlbWluZCIsIkFiYW5kb25lZC1kZXBv"
    "c2l0IHJlbWluZGVyIiwiTnVkZ2UgYmVmb3JlIGFuIHVuZmluaXNoZWQgcGF5bWVudCBleHBpcmVz"
    "LiJdLAogIFsic2NoZWRCcm9hZGNhc3QiLCJTY2hlZHVsZWQgJiBzZWdtZW50ZWQgYnJvYWRjYXN0"
    "cyIsIlF1ZXVlIGZvciBsYXRlciwgdGFyZ2V0IGJ1eWVycywgbm9uLWJ1eWVycyBvciBiaWcgc3Bl"
    "bmRlcnMuIl1dXSwKIFsiQWRtaW4gJiBvcGVyYXRpb25zIixbCiAgWyJhZG1pblJvbGVzIiwiQWRt"
    "aW4gcm9sZXMiLCJPd25lciAvIG1hbmFnZXIgLyBzdXBwb3J0IHdpdGggc2VwYXJhdGUgcGVybWlz"
    "c2lvbnMuIl0sCiAgWyJhdWRpdExvZyIsIkF1ZGl0IGxvZyIsIkV2ZXJ5IGFkbWluIGFjdGlvbiBs"
    "b2dnZWQgd2l0aCB0aW1lc3RhbXAgYW5kIHVuZG8uIl0sCiAgWyJjb250ZW50RWRpdG9yIiwiSW4t"
    "Ym90IGNvbnRlbnQgZWRpdG9yIiwiRWRpdCBldmVyeSBtZXNzYWdlIGFuZCBidXR0b24gd2l0aG91"
    "dCB0b3VjaGluZyBjb2RlLiJdLAogIFsiYXV0b0JhY2t1cCIsIlNjaGVkdWxlZCBhdXRvLWJhY2t1"
    "cCIsIkRhdGFiYXNlIHRvIGEgcHJpdmF0ZSBjaGFubmVsIGV2ZXJ5IE4gaG91cnMsIG9uZS10YXAg"
    "cmVzdG9yZS4iXSwKICBbImJ1bGtVcGxvYWQiLCJCdWxrIHVwbG9hZCB3aXRoIHZhbGlkYXRpb24i"
    "LCJWYWxpZGF0ZSBlYWNoIHNlc3Npb24sIHJlcG9ydCBkZWFkIG9yIGR1cGxpY2F0ZSwgaW1wb3J0"
    "IHRoZSByZXN0LiJdLAogIFsiZHVwRGV0ZWN0IiwiRHVwbGljYXRlIGRldGVjdGlvbiIsIlJlamVj"
    "dCBhIHBob25lIG9yIHNlc3Npb24gYWxyZWFkeSBpbiB0aGUgZGF0YWJhc2UuIl0sCiAgWyJsb3dT"
    "dG9jayIsIkxvdy1zdG9jayBhbGVydHMiLCJQaW5nIHdoZW4gYSBjb3VudHJ5IGRyb3BzIGJlbG93"
    "IGl0cyBvd24gdGhyZXNob2xkLiJdLAogIFsic2VsbEhvdXJzIiwiU2VsbGluZy1ob3VycyBzY2hl"
    "ZHVsZSIsIkF1dG8taGlkZSBjb3VudHJpZXMgb3V0c2lkZSB0aGVpciBzZWxsaW5nIHdpbmRvdy4i"
    "XSwKICBbInNhbmRib3giLCJUZXN0IG1vZGUgLyBzYW5kYm94IiwiUnVuIHRoZSB3aG9sZSBmbG93"
    "IG9uIGZha2UgbW9uZXkgYmVmb3JlIGdvaW5nIGxpdmUuIl0sCiAgWyJpMThuIiwiTXVsdGktbGFu"
    "Z3VhZ2UiLCJQZXItdXNlciBIaW5kaSAvIEVuZ2xpc2ggc3dpdGNoIGFjcm9zcyBldmVyeSBzdHJp"
    "bmcuIl1dXSwKIFsiU2VjdXJpdHkgJiBhbnRpLWFidXNlIixbCiAgWyJyYXRlTGltaXQiLCJSYXRl"
    "IGxpbWl0aW5nIiwiUGVyLXVzZXIgYWN0aW9uIGxpbWl0cyBzbyBub2JvZHkgY2FuIGhhbW1lciB0"
    "aGUgYm90LiJdLAogIFsibXVsdGlBY2MiLCJNdWx0aS1hY2NvdW50IGRldGVjdGlvbiIsIkNhdGNo"
    "IHJlZmVycmFsIGZhcm1pbmcgYW5kIGZsYWcgc3VzcGljaW91cyBzaWdudXBzLiJdLAogIFsidmVs"
    "b2NpdHkiLCJQdXJjaGFzZSB2ZWxvY2l0eSBhbGVydHMiLCJGbGFnIGFibm9ybWFsbHkgZmFzdCBi"
    "dXlpbmcg4oCUIHR5cGljYWwgb2Ygc3RvbGVuIHRvcC11cHMuIl0sCiAgWyJhZG1pbjJmYSIsIkFk"
    "bWluIDJGQSIsIlBJTiBvciBUT1RQIGJlZm9yZSBiYWxhbmNlIGVkaXRzLCBicm9hZGNhc3RzIG9y"
    "IHdpcGVzLiJdLAogIFsiYXV0b0JhbiIsIkF1dG8tYmFuIHJ1bGVzIiwiQmFuIG9uIGNoYXJnZWJh"
    "Y2ssIGZha2UgVVRSIG9yIHJlcGVhdGVkIGZhaWxlZCB2ZXJpZmljYXRpb24uIl0sCiAgWyJibGFj"
    "a2xpc3QiLCJCbGFja2xpc3QiLCJQZXJtYW5lbnRseSBibG9jayB1c2VyIElEcywgcGhvbmUgbnVt"
    "YmVycyBhbmQgVVBJIHJlZmVyZW5jZXMuIl0sCiAgWyJlbmNyeXB0IiwiU2Vzc2lvbiBlbmNyeXB0"
    "aW9uIGF0IHJlc3QiLCJBIGxlYWtlZCBkYXRhYmFzZSBmaWxlIGlzIHVzZWxlc3Mgd2l0aG91dCB0"
    "aGUga2V5LiJdXV0sCiBbIkFuYWx5dGljcyIsWwogIFsicmV2Q2hhcnQiLCJSZXZlbnVlIGRhc2hi"
    "b2FyZCIsIkRhaWx5LCB3ZWVrbHkgYW5kIG1vbnRobHkgcmV2ZW51ZSBhbmQgcHJvZml0IGFzIGNo"
    "YXJ0cy4iXSwKICBbImZ1bm5lbCIsIkNvbnZlcnNpb24gZnVubmVsIiwiU2VlIHdoZXJlIHVzZXJz"
    "IGRyb3Agb2ZmIGJldHdlZW4gam9pbmluZyBhbmQgYnV5aW5nLiJdLAogIFsicHJvZml0UmVwIiwi"
    "UGVyLWNvdW50cnkgcHJvZml0IiwiQ29zdCB2cyBzYWxlIHByaWNlIHZzIHZvbHVtZSBmb3IgZXZl"
    "cnkgY291bnRyeS4iXSwKICBbImNsdiIsIkN1c3RvbWVyIGxpZmV0aW1lIHZhbHVlIiwiU3BlbmQs"
    "IGZyZXF1ZW5jeSBhbmQgY2h1cm4gcmlzayBwZXIgdXNlci4iXSwKICBbImRpZ2VzdCIsIkRhaWx5"
    "IGRpZ2VzdCIsIkVuZC1vZi1kYXkgc3VtbWFyeSBwdXNoZWQgdG8gdGhlIGFkbWluIGNoYXQuIl1d"
    "XSwKIFsiUXVhbGl0eSBvZiBsaWZlIixbCiAgWyJpbmxpbmVTZWFyY2giLCJJbmxpbmUgc2VhcmNo"
    "IiwiVHlwZSBAeW91cmJvdCBpbmRpYSBpbiBhbnkgY2hhdCB0byBmaW5kIGFuZCBidXkuIl0sCiAg"
    "WyJzbGFzaENtZHMiLCJTbGFzaCBjb21tYW5kcyBtZW51IiwiL3N0YXJ0IC9idXkgL2JhbGFuY2Ug"
    "L29yZGVycyByZWdpc3RlcmVkIHdpdGggVGVsZWdyYW0uIl0sCiAgWyJzdG9ja0NvdW50ZXIiLCJM"
    "aXZlIHN0b2NrIGNvdW50ZXIiLCJBdXRvLXVwZGF0aW5nIHBpbm5lZCBwb3N0IGFuZCBpbi1hcHAg"
    "bGl2ZSBjb3VudC4iXSwKICBbIm9uYm9hcmRpbmciLCJPbmJvYXJkaW5nIHRvdXIiLCJUaHJlZS1z"
    "dGVwIHdhbGt0aHJvdWdoIGZvciBmaXJzdC10aW1lIHVzZXJzLiJdLAogIFsibm90aWZQcmVmcyIs"
    "Ik5vdGlmaWNhdGlvbiBwcmVmZXJlbmNlcyIsIlVzZXJzIHBpY2sgd2hpY2ggYWxlcnRzIHRoZXkg"
    "cmVjZWl2ZS4iXSwKICBbInB1c2hOb3RpZiIsIk1pbmkgYXBwIHB1c2giLCJNaW5pIGFwcCBhY3Rp"
    "b25zIG5vdGlmeSB0aGUgdXNlciB0aHJvdWdoIHRoZSBib3QuIl1dXQpdOwpjb25zdCBGRUFUPXt9"
    "OwpGR1JPVVBTLmZvckVhY2goKFtnLHJvd3NdKT0+cm93cy5mb3JFYWNoKHI9PkZFQVRbclswXV09"
    "dHJ1ZSkpOwpjb25zdCBGRUFUMD1PYmplY3QuYXNzaWduKHt9LEZFQVQpOwpjb25zdCBHPShrLGgp"
    "PT5GRUFUW2tdP2g6IiI7CmNvbnN0IGFueUY9KC4uLmtzKT0+a3Muc29tZShrPT5GRUFUW2tdKTsK"
    "bGV0IENBUlQ9W10sV0lTSD1bXTsKbGV0IFJFVklFV1M9e0luZGlhOls0LjgsNDEyXSxJbmRvbmVz"
    "aWE6WzQuNiwxODBdLEJyYXppbDpbNC43LDk2XSxOaWdlcmlhOls0LjQsNTRdLFZpZXRuYW06WzQu"
    "OSwyMzNdLFBoaWxpcHBpbmVzOls0LjUsNzFdfTsKbGV0IEZFRUQ9W1si8J+HrvCfh7MiLCJJbmRp"
    "YSIsIuKCuTM1IiwiMiBtaW4gYWdvIl0sWyLwn4e78J+HsyIsIlZpZXRuYW0iLCLigrkyNiIsIjYg"
    "bWluIGFnbyJdLFsi8J+Hp/Cfh7ciLCJCcmF6aWwiLCLigrk0NCIsIjExIG1pbiBhZ28iXV07Cmxl"
    "dCBBRE09ewogIGtwaTp7cmV2OjQ4MjAsb3JkZXJzOjYxLHVzZXJzOjEyODQsYXV0bzozOH0sCiAg"
    "YmFyczpbNDIsNjEsMzgsNzQsNTUsODgsNjddLAogIHN0b2NrOltdLG9yZGVyczpbXSxkaXNwdXRl"
    "czpbXSx1c2VyczpbXSxhZG1pbnM6W10sY2hhbm5lbHM6W10sCiAgbG9nczp7bWFzdGVyOnRydWUs"
    "Y2hhbm5lbHM6W10sZXZlbnRzOltdfSxjb3Vwb25zOltdLGF1ZGl0OltdLHJldmlld3M6W10sZGVw"
    "b3NpdHM6W10sbG9nZmVlZDpbXSwKICBwYXk6e3VwaToiIixwYXllZToiIixnbWFpbDoiIixnbWFp"
    "bF9zZXQ6ZmFsc2Usd2luZG93OjUscHJlZml4OiJQQVkiLG1pbjoxMCxtYXg6MjAwMDAsYXV0b2Rl"
    "cDowLHBlbmRpbmc6MH0sCiAgc2VjdXJpdHk6e3JhdGU6MjAsdmVsb2NpdHk6NSxibGFja2xpc3Q6"
    "MCxiYW5uZWQ6MH0sCiAgbWFya2V0OntmbGFzaDoyMCxmbGFzaF9vbjpmYWxzZSxyZWYxOjUscmVm"
    "MjoxLGdvYWw6MCxtb250aDowfSwKICBzZXR0aW5nczp7c3RvcmU6IiIsdGFnbGluZToiIixzdXBw"
    "b3J0OiIiLHByaWNlOjM1LGRhaWx5OjAuNCxhcHA6IiIsbWFpbnRlbmFuY2U6ZmFsc2Usc2FuZGJv"
    "eDpmYWxzZX0sCiAgYW5hbHl0aWNzOntmdW5uZWw6e3VzZXJzOjAsZGVwOjAsYnV5OjB9LGNvdW50"
    "cnk6W10sc3RvY2tfdG90YWw6MCxzb2xkX3RvdGFsOjB9Cn07CmZ1bmN0aW9uIGFkbU51bShuKXty"
    "ZXR1cm4gKG58fDApLnRvTG9jYWxlU3RyaW5nKCJlbi1JTiIpO30KbGV0IEVBUk49e3N0cmVhazo0"
    "LGRhaWx5OjAuNDAsY2xhaW1lZDpmYWxzZSxwb2ludHM6MzQwLHBvaW50c1dvcnRoOjM0LGludml0"
    "ZXM6MTIsCiAgbDE6NTIuNDAsbDI6MTIuNDAsZWFybmVkOjY0LjgwLGxpbms6InQubWUveW91cl9i"
    "b3Q/c3RhcnQ9cmVmX2RlbW8iLAogIHJlY2VudDpbWyJ1c2VyXzEwNDEiLCJkZXBvc2l0ZWQg4oK5"
    "MjAwIiwiK+KCuTIuMDAiXSxbInVzZXJfMTAzOCIsImpvaW5lZCIsIuKAlCJdLFsidXNlcl8xMDIy"
    "IiwiZGVwb3NpdGVkIOKCuTUwMCIsIivigrk1LjAwIl1dLAogIGxlYWRlcnM6W1siMSIsIkByYWh1"
    "bCIsIuKCuTQsODIwIl0sWyIyIiwiQGFtYW4iLCLigrkzLDE0MCJdLFsiMyIsIkB5b3UiLCLigrkx"
    "LDk4MCJdXSwKICB0YXNrczpbWyJKb2luIHRoZSBtYWluIGNoYW5uZWwiLCLigrkxLjAwIiwxXSxb"
    "IlBsYWNlIHlvdXIgZmlyc3Qgb3JkZXIiLCLigrkyLjAwIiwxXSxbIkludml0ZSAzIGZyaWVuZHMi"
    "LCLigrk1LjAwIiwwXV19OwpmdW5jdGlvbiBzdGFycyhjKXtjb25zdCByPShSRVZJRVdTW2NdfHxb"
    "NC43LDIwXSlbMF0sbj0oUkVWSUVXU1tjXXx8WzQuNywyMF0pWzFdOwogIHJldHVybiBgPHNwYW4g"
    "Y2xhc3M9InN0YXJzIj4ke1sxLDIsMyw0LDVdLm1hcChpPT5pYyhpPD1NYXRoLnJvdW5kKHIpPyJz"
    "dGFyIjoic3RhciIpKS5qb2luKCIiKX08Yj4ke3J9ICgke259KTwvYj48L3NwYW4+YDt9CmZ1bmN0"
    "aW9uIGFwcGx5RmVhdHVyZXMoKXsKICBjb25zdCBjYz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgi"
    "Y2FydGNoaXAiKTsKICBpZihjYyl7Y2MuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZGVuIiwhRkVBVC5j"
    "YXJ0KTtkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2FydG4iKS50ZXh0Q29udGVudD1DQVJULmxl"
    "bmd0aDt9CiAgY29uc3QgZWFybj1hbnlGKCJzdHJlYWsiLCJyZWZEYXNoIiwicmVmTDIiLCJsb3lh"
    "bHR5IiwibGVhZGVyYm9hcmQiLCJ3aW5iYWNrIik7CiAgY29uc3QgZWI9ZG9jdW1lbnQucXVlcnlT"
    "ZWxlY3RvcignI3RhYnMgYnV0dG9uW2RhdGEtdGFiPSJlYXJuIl0nKTsKICBpZihlYil7ZWIuY2xh"
    "c3NMaXN0LnRvZ2dsZSgiaGlkZGVuIiwhZWFybik7CiAgICBpZighZWFybiYmVEFCPT09ImVhcm4i"
    "KXtUQUI9InNob3AiO2RvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiN0YWJzIGJ1dHRvbiIpLmZv"
    "ckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseC5kYXRhc2V0LnRhYj09PSJzaG9wIikp"
    "O3JlbmRlcigpO319CiAgY29uc3QgYW1hcD17bWFya2V0OmFueUYoImNvdXBvbnMiLCJmbGFzaFNh"
    "bGUiLCJzY2hlZEJyb2FkY2FzdCIsIndpbmJhY2siLCJzYWxlc0ZlZWQiLCJsZWFkZXJib2FyZCIs"
    "ImxveWFsdHkiLCJyZWZEYXNoIiwicmVmTDIiLCJhYmFuZG9uUmVtaW5kIiksCiAgICBhbmFseXRp"
    "Y3M6YW55RigicmV2Q2hhcnQiLCJmdW5uZWwiLCJwcm9maXRSZXAiLCJjbHYiLCJkaWdlc3QiKSwK"
    "ICAgIHNlY3VyaXR5OmFueUYoInJhdGVMaW1pdCIsIm11bHRpQWNjIiwidmVsb2NpdHkiLCJhZG1p"
    "bjJmYSIsImF1dG9CYW4iLCJibGFja2xpc3QiLCJlbmNyeXB0IiksCiAgICBjb250ZW50OkZFQVQu"
    "Y29udGVudEVkaXRvcn07CiAgT2JqZWN0LmVudHJpZXMoYW1hcCkuZm9yRWFjaCgoW2ssdl0pPT57"
    "Y29uc3QgYj1kb2N1bWVudC5xdWVyeVNlbGVjdG9yKCcjYXN1YiBidXR0b25bZGF0YS1hPSInK2sr"
    "JyJdJyk7CiAgICBpZihiKXtiLmNsYXNzTGlzdC50b2dnbGUoImhpZGRlbiIsIXYpOwogICAgICBp"
    "ZighdiYmQVRBQj09PWspe0FUQUI9ImRhc2giO2RvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNh"
    "c3ViIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIseC5kYXRhc2V0"
    "LmE9PT0iZGFzaCIpKTtyZW5kZXJBZG1pbigpO319fSk7CiAgaWYod2luZG93Ll9fZG9ja1BsYWNl"
    "KXJlcXVlc3RBbmltYXRpb25GcmFtZSgoKT0+d2luZG93Ll9fZG9ja1BsYWNlKHRydWUpKTsKICBp"
    "Zih3aW5kb3cuX192dGFiUGxhY2UpcmVxdWVzdEFuaW1hdGlvbkZyYW1lKCgpPT53aW5kb3cuX192"
    "dGFiUGxhY2UodHJ1ZSkpOwp9CmZ1bmN0aW9uIGFwcGx5Q01TKCl7CiBjb25zdCBnPWlkPT5kb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZChpZCk7CiBpZihnKCJibmFtZSIpKWcoImJuYW1lIikudGV4dENv"
    "bnRlbnQ9Q01TLnN0b3JlTmFtZTsKIGlmKGcoImFibmFtZSIpKWcoImFibmFtZSIpLnRleHRDb250"
    "ZW50PUNNUy5zdG9yZU5hbWU7CiBpZihDTVMuc3RvcmVOYW1lKXtDRkcubmFtZT1DTVMuc3RvcmVO"
    "YW1lO30KIGlmKENNUy5zdG9yZVRhZylDRkcudGFnPUNNUy5zdG9yZVRhZzsKIHBhaW50QnJhbmQo"
    "Q01TLnN0b3JlTmFtZSxDTVMuc3RvcmVUYWcpOwogdHJ5ewogICBpZihDTVMuc3RvcmVOYW1lKWxv"
    "Y2FsU3RvcmFnZS5zZXRJdGVtKCJzdG9yZU5hbWUiLENNUy5zdG9yZU5hbWUpOwogICBpZihDTVMu"
    "c3RvcmVUYWcpbG9jYWxTdG9yYWdlLnNldEl0ZW0oInN0b3JlVGFnIixDTVMuc3RvcmVUYWcpOwog"
    "fWNhdGNoKGUpe30KIGlmKGcoImJ0YWciKSlnKCJidGFnIikudGV4dENvbnRlbnQ9Q01TLnN0b3Jl"
    "VGFnOwogZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtbmF2XSIpLmZvckVhY2gobj0+"
    "ewogICBjb25zdCBrPSJuYXYiK24uZGF0YXNldC5uYXZbMF0udG9VcHBlckNhc2UoKStuLmRhdGFz"
    "ZXQubmF2LnNsaWNlKDEpOwogICBpZihDTVNba10pbi50ZXh0Q29udGVudD1DTVNba107fSk7CiBp"
    "Zih3aW5kb3cuX19kb2NrUGxhY2UpcmVxdWVzdEFuaW1hdGlvbkZyYW1lKCgpPT53aW5kb3cuX19k"
    "b2NrUGxhY2UodHJ1ZSkpOwp9CmxldCBCQUw9MjQ4LjUwOwpjb25zdCBTVE9DSz1bCiB7YzoiSW5k"
    "aWEiLGY6IvCfh67wn4ezIixjb2RlOiIrOTEiLHByaWNlOjM1LHdhczo0OSxxdHk6MTI4LGhvdDox"
    "fSwKIHtjOiJJbmRvbmVzaWEiLGY6IvCfh67wn4epIixjb2RlOiIrNjIiLHByaWNlOjI4LHdhczow"
    "LHF0eTo2NH0sCiB7YzoiQnJhemlsIixmOiLwn4en8J+HtyIsY29kZToiKzU1IixwcmljZTo0NCx3"
    "YXM6NTkscXR5OjIyfSwKIHtjOiJOaWdlcmlhIixmOiLwn4ez8J+HrCIsY29kZToiKzIzNCIscHJp"
    "Y2U6MzEsd2FzOjAscXR5OjB9LAoge2M6IlZpZXRuYW0iLGY6IvCfh7vwn4ezIixjb2RlOiIrODQi"
    "LHByaWNlOjI2LHdhczowLHF0eTo5MX0sCiB7YzoiUGhpbGlwcGluZXMiLGY6IvCfh7Xwn4etIixj"
    "b2RlOiIrNjMiLHByaWNlOjM4LHdhczowLHF0eTo3fQpdOwpjb25zdCBPUkRFUlM9Wwoge2lkOiJP"
    "UkQtNzc0MSIsYzoiSW5kaWEiLGY6IvCfh67wn4ezIixudW06Iis5MSDigKLigKLigKLigKIg4oCi"
    "4oCiMzIiLHN0OiJEZWxpdmVyZWQiLHdoZW46IjIgbWluIGFnbyIscHJpY2U6MzV9LAoge2lkOiJP"
    "UkQtNzcyOCIsYzoiVmlldG5hbSIsZjoi8J+Hu/Cfh7MiLG51bToiKzg0IOKAouKAouKAouKAoiDi"
    "gKLigKIwNyIsc3Q6IkRlbGl2ZXJlZCIsd2hlbjoiWWVzdGVyZGF5IixwcmljZToyNn0sCiB7aWQ6"
    "Ik9SRC03NzAyIixjOiJCcmF6aWwiLGY6IvCfh6fwn4e3IixudW06Iis1NSDigKLigKLigKLigKIg"
    "4oCi4oCiODgiLHN0OiJSZWZ1bmRlZCIsd2hlbjoiMyBkYXlzIGFnbyIscHJpY2U6NDR9Cl07Cmxl"
    "dCBDSEFOTkVMUz1bCiB7aWQ6MSxuYW1lOiJNYWluIENoYW5uZWwiLGhhbmRsZToiQHlvdXJfbWFp"
    "bl9jaGFubmVsIixyZXE6dHJ1ZSxvbjp0cnVlLG1lbWJlcnM6IjEyLjRLIn0sCiB7aWQ6MixuYW1l"
    "OiJCYWNrdXAgQ2hhbm5lbCIsaGFuZGxlOiJAeW91cl9iYWNrdXAiLHJlcTp0cnVlLG9uOnRydWUs"
    "bWVtYmVyczoiMy4xSyJ9LAoge2lkOjMsbmFtZToiRGlzY3Vzc2lvbiBHcm91cCIsaGFuZGxlOiJA"
    "eW91cl9ncm91cCIscmVxOmZhbHNlLG9uOnRydWUsbWVtYmVyczoiNS44SyJ9Cl07CmxldCBMT0dD"
    "SD1bCiB7aWQ6MSxuYW1lOiJNYWluIExvZ3MiLGhhbmRsZToiQHlvdXJfbG9ncyIscHJpbWFyeTp0"
    "cnVlLG9uOnRydWV9LAoge2lkOjIsbmFtZToiUGF5bWVudCBMb2dzIixoYW5kbGU6IkB5b3VyX3Bh"
    "eV9sb2dzIixwcmltYXJ5OmZhbHNlLG9uOnRydWV9LAoge2lkOjMsbmFtZToiRXJyb3IgTG9ncyIs"
    "aGFuZGxlOiItMTAweHh4eHh4eHh4eCIscHJpbWFyeTpmYWxzZSxvbjpmYWxzZX0KXTsKbGV0IExP"
    "R1NfTUFTVEVSPXRydWU7CmxldCBFVkVOVFM9Wwoge2s6Ik5ldyB1c2VyIGpvaW5lZCIsb246ZmFs"
    "c2UsY2g6MX0se2s6IkRlcG9zaXQgLyBwYXltZW50Iixvbjp0cnVlLGNoOjJ9LAoge2s6IlB1cmNo"
    "YXNlIixvbjp0cnVlLGNoOjF9LHtrOiJPVFAgcmVxdWVzdGVkIixvbjpmYWxzZSxjaDoxfSwKIHtr"
    "OiJTdG9jayBhZGRlZCIsb246dHJ1ZSxjaDoxfSx7azoiQWRtaW4gYWN0aW9uIixvbjp0cnVlLGNo"
    "OjF9LAoge2s6IkVycm9ycyAmIHdhcm5pbmdzIixvbjp0cnVlLGNoOjN9Cl07CmNvbnN0IFRYPVt7"
    "dDoiRGVwb3NpdCIsYToiK+KCuTIwMC4wMCIsdzoiVG9kYXksIDE0OjAyIn0se3Q6IlB1cmNoYXNl"
    "IMK3IEluZGlhIixhOiIt4oK5MzUuMDAiLHc6IlRvZGF5LCAxNDowNSJ9LAoge3Q6IkRhaWx5IGNo"
    "ZWNrLWluIixhOiIr4oK5MC40MCIsdzoiVG9kYXksIDA5OjExIn0se3Q6IlJlZmVycmFsIGJvbnVz"
    "IixhOiIr4oK5My4yMCIsdzoiWWVzdGVyZGF5In1dOwoKbGV0IEZJTFRFUkVEPVNUT0NLLnNsaWNl"
    "KCksIFFVRVJZPSIiLCBDSElQPSJhbGwiOwpjb25zdCBFTVBUWT0nPGRpdiBjbGFzcz0iY2FyZCIg"
    "c3R5bGU9InRleHQtYWxpZ246Y2VudGVyIj48cCBjbGFzcz0ic3ViIj5Ob3RoaW5nIG1hdGNoZXMg"
    "dGhhdCBzZWFyY2guPC9wPjwvZGl2Pic7CmNvbnN0IElDPXtiYWc6Ijxzdmcgdmlld0JveD1cIjAg"
    "MCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdp"
    "ZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3Vu"
    "ZFwiPjxwYXRoIGQ9XCJNNiAyIDMgNnYxNGEyIDIgMCAwIDAgMiAyaDE0YTIgMiAwIDAgMCAyLTJW"
    "NmwtMy00elwiLz48cGF0aCBkPVwiTTMgNmgxOFwiLz48cGF0aCBkPVwiTTE2IDEwYTQgNCAwIDAg"
    "MS04IDBcIi8+PC9zdmc+Iixib3g6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJu"
    "b25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1s"
    "aW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMjEg"
    "OGEyIDIgMCAwIDAtMS0xLjczbC03LTRhMiAyIDAgMCAwLTIgMGwtNyA0QTIgMiAwIDAgMCAzIDh2"
    "OGEyIDIgMCAwIDAgMSAxLjczbDcgNGEyIDIgMCAwIDAgMiAwbDctNEEyIDIgMCAwIDAgMjEgMTZa"
    "XCIvPjxwYXRoIGQ9XCJtMy4zIDcgOC43IDUgOC43LTVcIi8+PHBhdGggZD1cIk0xMiAyMlYxMlwi"
    "Lz48L3N2Zz4iLHphcDoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBz"
    "dHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9"
    "XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0xMyAyIDMgMTRo"
    "OWwtMSA4IDEwLTEyaC05elwiLz48L3N2Zz4iLHdhbGxldDoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0"
    "IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9"
    "XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+"
    "PHBhdGggZD1cIk0xOSA3VjVhMiAyIDAgMCAwLTItMkg1YTIgMiAwIDAgMCAwIDRoMTVhMiAyIDAg"
    "MCAxIDIgMnY5YTIgMiAwIDAgMS0yIDJINWEyIDIgMCAwIDEtMi0yVjVcIi8+PHBhdGggZD1cIk0x"
    "OCAxMmguMDFcIi8+PC9zdmc+Iix1c2VyOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxs"
    "PVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJv"
    "a2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwi"
    "TTE5IDIxdi0yYTQgNCAwIDAgMC00LTRIOWE0IDQgMCAwIDAtNCA0djJcIi8+PGNpcmNsZSBjeD1c"
    "IjEyXCIgY3k9XCI3XCIgcj1cIjRcIi8+PC9zdmc+IixzaGllbGQ6Ijxzdmcgdmlld0JveD1cIjAg"
    "MCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdp"
    "ZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3Vu"
    "ZFwiPjxwYXRoIGQ9XCJNMjAgMTNjMCA1LTMuNSA3LjUtNy42NiA4Ljk1YTEgMSAwIDAgMS0uNjct"
    "LjAxQzcuNSAyMC41IDQgMTggNCAxM1Y2YTEgMSAwIDAgMSAxLTFjMiAwIDQuNS0xLjIgNi4yNC0y"
    "LjcyYTEuMTcgMS4xNyAwIDAgMSAxLjUyIDBDMTQuNTEgMy44MSAxNyA1IDE5IDVhMSAxIDAgMCAx"
    "IDEgMXpcIi8+PC9zdmc+IixjaGVjazoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1c"
    "Im5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyLjRcIiBzdHJv"
    "a2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwi"
    "TTIwIDYgOSAxN2wtNS01XCIvPjwvc3ZnPiIsc2VuZDoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0"
    "XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIy"
    "XCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBh"
    "dGggZD1cIm0yMiAyLTcgMjAtNC05LTktNFpcIi8+PHBhdGggZD1cIk0yMiAyIDExIDEzXCIvPjwv"
    "c3ZnPiIscGx1czoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJv"
    "a2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyLjRcIiBzdHJva2UtbGluZWNhcD1c"
    "InJvdW5kXCI+PHBhdGggZD1cIk0xMiA1djE0TTUgMTJoMTRcIi8+PC9zdmc+Iixjb3B5OiI8c3Zn"
    "IHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xv"
    "clwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxp"
    "bmVqb2luPVwicm91bmRcIj48cmVjdCB3aWR0aD1cIjE0XCIgaGVpZ2h0PVwiMTRcIiB4PVwiOFwi"
    "IHk9XCI4XCIgcng9XCIyXCIvPjxwYXRoIGQ9XCJNNCAxNmMtMS4xIDAtMi0uOS0yLTJWNGMwLTEu"
    "MS45LTIgMi0yaDEwYzEuMSAwIDIgLjkgMiAyXCIvPjwvc3ZnPiIsY2hldjoiPHN2ZyB2aWV3Qm94"
    "PVwiMCAwIDI0IDI0XCIgd2lkdGg9XCIxNlwiIGhlaWdodD1cIjE2XCIgZmlsbD1cIm5vbmVcIiBz"
    "dHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9"
    "XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIm05IDE4IDYtNi02"
    "LTZcIi8+PC9zdmc+IixmaXJlOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiB3aWR0aD1cIjEy"
    "XCIgaGVpZ2h0PVwiMTJcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0"
    "cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2lu"
    "PVwicm91bmRcIj48cGF0aCBkPVwiTTEyIDJjMSA0IDUgNSA1IDlhNSA1IDAgMCAxLTEwIDBjMC0y"
    "IDEtMyAyLTQgMCAyIDEgMyAyIDMgMC0zLTEtNSAxLThaXCIvPjwvc3ZnPiIsZG93bjoiPHN2ZyB2"
    "aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3Jc"
    "IiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5l"
    "am9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0xMiA1djE0TTE5IDEybC03IDctNy03XCIvPjwvc3Zn"
    "PiIsdXA6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwi"
    "Y3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRc"
    "IiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTIgMTlWNU01IDEybDctNyA3"
    "IDdcIi8+PC9zdmc+IixzZWFyY2g6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJu"
    "b25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1s"
    "aW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxjaXJjbGUgY3g9XCIx"
    "MVwiIGN5PVwiMTFcIiByPVwiN1wiLz48cGF0aCBkPVwibTIxIDIxLTQuMy00LjNcIi8+PC9zdmc+"
    "IixzdGFyOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwiY3VycmVudENvbG9yXCIg"
    "c3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMS41XCIgc3Ryb2tlLWxpbmVq"
    "b2luPVwicm91bmRcIj48cGF0aCBkPVwibTEyIDMgMi42IDUuMyA1LjkuOS00LjMgNC4xIDEgNS44"
    "LTUuMi0yLjctNS4yIDIuNyAxLTUuOEwzLjUgOS4ybDUuOS0uOXpcIi8+PC9zdmc+In07CmNvbnN0"
    "IGljPW49PklDW25dfHwiIjsKY29uc3QgZXNjPXM9PlN0cmluZyhzKS5yZXBsYWNlKC9bJjw+Il0v"
    "ZyxjPT4oeyImIjoiJmFtcDsiLCI8IjoiJmx0OyIsIj4iOiImZ3Q7IiwnIic6IiZxdW90OyJ9W2Nd"
    "KSk7CmNvbnN0IG1vbmV5PW49PkNGRy5jdXIrTnVtYmVyKG4pLnRvRml4ZWQoMik7CgpsZXQgdHQ7"
    "Y29uc3QgdG9hc3Q9bT0+e2NvbnN0IGU9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInRvYXN0Iik7"
    "ZS50ZXh0Q29udGVudD1tO2UuY2xhc3NMaXN0LmFkZCgic2hvdyIpO2NsZWFyVGltZW91dCh0dCk7"
    "dHQ9c2V0VGltZW91dCgoKT0+ZS5jbGFzc0xpc3QucmVtb3ZlKCJzaG93IiksMTkwMCl9Owpkb2N1"
    "bWVudC5hZGRFdmVudExpc3RlbmVyKCJjbGljayIsZT0+e2NvbnN0IGI9ZS50YXJnZXQuY2xvc2Vz"
    "dCgiW2RhdGEtdG9hc3RdIik7aWYoYil0b2FzdChiLmRhdGFzZXQudG9hc3QpfSk7CmNvbnN0IHNo"
    "ZWV0PWg9PnsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicGFuZWwiKS5pbm5lckhUTUw9Jzxk"
    "aXYgY2xhc3M9ImdyYWIiPjwvZGl2PicraDsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic2hl"
    "ZXQiKS5jbGFzc0xpc3QuYWRkKCJvcGVuIik7CiAgLyogY29udHJvbHMgaW5zaWRlIGEgc2hlZXQg"
    "YXJlIGNyZWF0ZWQgYWZ0ZXIgYmluZGluZywgc28gYmluZCB0aGVtIG5vdyAqLwogIHRyeXsgaWYo"
    "dHlwZW9mIGJpbmRDb21tb249PT0iZnVuY3Rpb24iKSBiaW5kQ29tbW9uKCk7IH1jYXRjaChlKXt9"
    "CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3NoZWV0IFtkYXRhLWNsb3NlXSIpLmZvckVh"
    "Y2goeD0+eC5vbmNsaWNrPWNsb3NlU2hlZXQpOwp9Owpjb25zdCBjbG9zZVNoZWV0PSgpPT5kb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgic2hlZXQiKS5jbGFzc0xpc3QucmVtb3ZlKCJvcGVuIik7CmRv"
    "Y3VtZW50LmFkZEV2ZW50TGlzdGVuZXIoImNsaWNrIixlPT57aWYoZS50YXJnZXQuY2xvc2VzdCgi"
    "W2RhdGEtY2xvc2VdIikpY2xvc2VTaGVldCgpfSk7Cgpkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxs"
    "KCIjc3cgYnV0dG9uIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICBpZihiLmRhdGFzZXQu"
    "c3c9PT0iYWRtaW4iJiYhYWRtaW5BbGxvd2VkKCkpe3RvYXN0KCJBZG1pbnMgb25seSIpO3JldHVy"
    "bjt9CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3N3IGJ1dHRvbiIpLmZvckVhY2goeD0+"
    "eC5jbGFzc0xpc3QucmVtb3ZlKCJvbiIpKTtiLmNsYXNzTGlzdC5hZGQoIm9uIik7CiAgWyJhcHAi"
    "LCJhZG1pbiJdLmZvckVhY2godj0+ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInZpZXctIit2KS5j"
    "bGFzc0xpc3QudG9nZ2xlKCJoaWRkZW4iLHYhPT1iLmRhdGFzZXQuc3cpKTsKICBkb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgidGFicyIpLnN0eWxlLmRpc3BsYXk9Yi5kYXRhc2V0LnN3PT09ImFwcCI/"
    "IiI6Im5vbmUiO3dpbmRvdy5zY3JvbGxUbygwLDApOwogIHJlcXVlc3RBbmltYXRpb25GcmFtZSgo"
    "KT0+e2lmKHdpbmRvdy5fX2RvY2tQbGFjZSl3aW5kb3cuX19kb2NrUGxhY2UodHJ1ZSk7CiAgICBp"
    "Zih3aW5kb3cuX192dGFiUGxhY2Upd2luZG93Ll9fdnRhYlBsYWNlKHRydWUpO30pO30pOwoKbGV0"
    "IFRBQj0ic2hvcCI7CmRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiN0YWJzIGJ1dHRvbiIpLmZv"
    "ckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3Rh"
    "YnMgYnV0dG9uIikuZm9yRWFjaCh4PT54LmNsYXNzTGlzdC5yZW1vdmUoIm9uIikpO2IuY2xhc3NM"
    "aXN0LmFkZCgib24iKTsKICBUQUI9Yi5kYXRhc2V0LnRhYjtyZW5kZXIoKTt3aW5kb3cuc2Nyb2xs"
    "VG8oMCwwKTtpZih3aW5kb3cuX19kb2NrUGxhY2UpcmVxdWVzdEFuaW1hdGlvbkZyYW1lKHdpbmRv"
    "dy5fX2RvY2tQbGFjZSk7fSk7CmRvY3VtZW50LmFkZEV2ZW50TGlzdGVuZXIoImNsaWNrIixlPT57"
    "Y29uc3QgZz1lLnRhcmdldC5jbG9zZXN0KCJbZGF0YS1nb10iKTtpZighZylyZXR1cm47CiAgY29u"
    "c3QgdD1nLmRhdGFzZXQuZ287ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3RhYnMgYnV0dG9u"
    "IikuZm9yRWFjaCh4PT54LmNsYXNzTGlzdC50b2dnbGUoIm9uIix4LmRhdGFzZXQudGFiPT09dCkp"
    "OwogIFRBQj10O3JlbmRlcigpO3dpbmRvdy5zY3JvbGxUbygwLDApO30pOwoKZnVuY3Rpb24gcmVu"
    "ZGVyKCl7Y29uc3Qgc3Q9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0YWdlIik7CiAgaWYoTE9D"
    "S0VEKXtwYWludExvY2tlZCgpO3JldHVybjt9CiAgc3QuaW5uZXJIVE1MPSc8ZGl2IGNsYXNzPSJz"
    "ayI+PC9kaXY+PGRpdiBjbGFzcz0ic2siPjwvZGl2PjxkaXYgY2xhc3M9InNrIiBzdHlsZT0iaGVp"
    "Z2h0OjE0MHB4Ij48L2Rpdj4nOwogIHNldFRpbWVvdXQoKCk9PntpZihMT0NLRUQpe3BhaW50TG9j"
    "a2VkKCk7cmV0dXJuO30KICAgIHN0LmlubmVySFRNTD0oe3Nob3A6dlNob3Asb3JkZXJzOnZPcmRl"
    "cnMsZWFybjp2RWFybix3YWxsZXQ6dldhbGxldCx5b3U6dllvdX0pW1RBQl0oKTsKICAgIGJpbmRT"
    "dGFnZSgpO3JldmVhbFN0YWdlKHN0KTtiaW5kVGlsdCgpO30sUk0/MDoxNjApO30KCmZ1bmN0aW9u"
    "IHZTaG9wKCl7CiAgY29uc3QgbGl2ZT1TVE9DSy5maWx0ZXIocz0+cy5xdHk+MCkubGVuZ3RoLCB1"
    "bml0cz1TVE9DSy5yZWR1Y2UoKGEsYik9PmErYi5xdHksMCk7CiAgaWYoTElWRSYmIVNUT0NLLmxl"
    "bmd0aCl7CiAgICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJtYXJnaW46MThweCAw"
    "O3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MjZweCI+CiAgICAgIDxoMyBzdHlsZT0ibWFyZ2lu"
    "OjAgMCA2cHgiPiR7ZXNjKENNUy5lbXB0eVRpdGxlfHwiUmVzdG9ja2luZyIpfTwvaDM+CiAgICAg"
    "IDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+JHtlc2MoQ01TLmVtcHR5Qm9keXx8IkV2"
    "ZXJ5dGhpbmcganVzdCBzb2xkIG91dC4gRnJlc2ggYWNjb3VudHMgbGFuZCBoZXJlIHNob3J0bHkg"
    "XHUyMDE0IHR1cm4gb24gYW4gYWxlcnQgYW5kIHlvdSB3aWxsIGhlYXIgZmlyc3QuIil9PC9wPgog"
    "ICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjE0cHgiIGRh"
    "dGEtYWN0PSJhbGVydHMiPk5vdGlmeSBtZTwvYnV0dG9uPjwvZGl2PmA7CiAgfQogIHJldHVybiBg"
    "CiAgPGRpdiBjbGFzcz0iYmFubmVyIj48ZGl2IGNsYXNzPSJ0cmFjayI+CiAgICAke1sxLDJdLm1h"
    "cCgoKT0+YDxzcGFuPiR7aWMoInphcCIpfSR7Q01TLnRpY2sxfTwvc3Bhbj48c3Bhbj4ke2ljKCJ3"
    "YWxsZXQiKX0ke0NNUy50aWNrMn08L3NwYW4+CiAgICA8c3Bhbj4ke2ljKCJzaGllbGQiKX0ke0NN"
    "Uy50aWNrM308L3NwYW4+PHNwYW4+JHtpYygic3RhciIpfSR7Q01TLnRpY2s0fTwvc3Bhbj5gKS5q"
    "b2luKCIiKX0KICA8L2Rpdj48L2Rpdj4KICAke0coIm9uYm9hcmRpbmciLGA8ZGl2IGNsYXNzPSJj"
    "YXJkIHRpZ2h0IiBpZD0idG91cmNhcmQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6"
    "Y2VudGVyO2dhcDoxMHB4Ij4KICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoInphcCIpfTwvZGl2"
    "PgogICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXpl"
    "Oi44cmVtIj5OZXcgaGVyZT8gMy1zdGVwIHRvdXI8L2I+CiAgICAgIDxicj48c21hbGwgc3R5bGU9"
    "ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+VG9wIHVwLCBwaWNrIGEgY291bnRy"
    "eSwgZ2V0IHRoZSBPVFAgaW4tYXBwLjwvc21hbGw+PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4geHMiIGlkPSJ0b3VyZ28iPlN0YXJ0PC9idXR0b24+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "eHMgZ2hvc3QiIGlkPSJ0b3Vyc2tpcCI+U2tpcDwvYnV0dG9uPjwvZGl2PmApfQogIDxzZWN0aW9u"
    "IGNsYXNzPSJoZXJvIj4KICAgIDxkaXYgY2xhc3M9ImtpY2siPiR7Q01TLmhlcm9LaWNrfTwvZGl2"
    "PgogICAgPGgxPiR7Q01TLmhlcm9IMX08L2gxPgogICAgPHA+JHtDTVMuaGVyb1N1Yi5yZXBsYWNl"
    "KCJ7bn0iLGxpdmUpfTwvcD4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+CiAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBjdGEiIGRhdGEtc2Nyb2xsPSJzaG9wbGlzdCI+JHtpYygiYmFnIil9ICR7Q01T"
    "Lmhlcm9CdG4xfTwvYnV0dG9uPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2xhc3MiIGRhdGEt"
    "Z289IndhbGxldCI+JHtDTVMuaGVyb0J0bjJ9PC9idXR0b24+CiAgICA8L2Rpdj4KICA8L3NlY3Rp"
    "b24+CiAgPHN2ZyBjbGFzcz0idHJhY2UiIHZpZXdCb3g9IjAgMCAzMjAgMTAiIHByZXNlcnZlQXNw"
    "ZWN0UmF0aW89Im5vbmUiPjxwYXRoIGNsYXNzPSJiYXNlIiBkPSJNMCw1IEwzMjAsNSIvPgogICAg"
    "PHBhdGggY2xhc3M9ImxlYWQiIGQ9Ik0wLDUgTDMyMCw1IiBzdHJva2U9InVybCgjdGcxKSIvPgog"
    "ICAgPGRlZnM+PGxpbmVhckdyYWRpZW50IGlkPSJ0ZzEiIGdyYWRpZW50VW5pdHM9InVzZXJTcGFj"
    "ZU9uVXNlIiB4MT0iLTE2MCIgeTE9IjAiIHgyPSIwIiB5Mj0iMCI+CiAgICAgIDxzdG9wIHN0b3At"
    "Y29sb3I9InZhcigtLWFjYykiIHN0b3Atb3BhY2l0eT0iMCIvPjxzdG9wIG9mZnNldD0iLjUiIHN0"
    "b3AtY29sb3I9InZhcigtLWFjYykiLz4KICAgICAgPHN0b3Agb2Zmc2V0PSIxIiBzdG9wLWNvbG9y"
    "PSJ2YXIoLS1hY2MyKSIgc3RvcC1vcGFjaXR5PSIwIi8+CiAgICAgIDxhbmltYXRlIGF0dHJpYnV0"
    "ZU5hbWU9IngxIiB2YWx1ZXM9Ii0xNjA7MzIwIiBkdXI9IjIuMnMiIHJlcGVhdENvdW50PSJpbmRl"
    "ZmluaXRlIi8+CiAgICAgIDxhbmltYXRlIGF0dHJpYnV0ZU5hbWU9IngyIiB2YWx1ZXM9IjA7NDgw"
    "IiBkdXI9IjIuMnMiIHJlcGVhdENvdW50PSJpbmRlZmluaXRlIi8+CiAgICA8L2xpbmVhckdyYWRp"
    "ZW50PjwvZGVmcz48L3N2Zz4KICA8ZGl2IGNsYXNzPSJmZWF0cyI+CiAgICA8ZGl2IGNsYXNzPSJm"
    "ZWF0Ij4ke2ljKCJ6YXAiKX08Yj4ke0NNUy5mZWF0MX08L2I+PHNtYWxsPiR7Q01TLmZlYXQxc308"
    "L3NtYWxsPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iZmVhdCI+JHtpYygic2hpZWxkIil9PGI+JHtD"
    "TVMuZmVhdDJ9PC9iPjxzbWFsbD4ke0NNUy5mZWF0MnN9PC9zbWFsbD48L2Rpdj4KICAgIDxkaXYg"
    "Y2xhc3M9ImZlYXQiPiR7aWMoIndhbGxldCIpfTxiPiR7Q01TLmZlYXQzfTwvYj48c21hbGw+JHtD"
    "TVMuZmVhdDNzfTwvc21hbGw+PC9kaXY+CiAgPC9kaXY+CiAgJHtHKCJzdG9ja0NvdW50ZXIiLGA8"
    "ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNl"
    "bnRlcjtnYXA6MTBweCI+CiAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+PGkgY2xhc3M9ImRvdCI+"
    "PC9pPkxpdmU8L3NwYW4+CiAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9udC1z"
    "aXplOi43NnJlbTtjb2xvcjp2YXIoLS10eDIpIj48YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4k"
    "e3VuaXRzfTwvYj4gYWNjb3VudHMgaW4gc3RvY2sgYWNyb3NzIDxiIHN0eWxlPSJjb2xvcjp2YXIo"
    "LS10eCkiPiR7bGl2ZX08L2I+IGNvdW50cmllczwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IHhzIHNvZnQiIGRhdGEtYWN0PSJjaGFubmVsIj5DaGFubmVsPC9idXR0b24+PC9kaXY+YCl9CiAg"
    "JHtHKCJmbGFzaFNhbGUiLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpm"
    "bGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICA8c3BhbiBjbGFzcz0icGlsbCBo"
    "b3QiPiR7aWMoImZpcmUiKX0gJHtDTVMuZmxhc2hQaWxsfTwvc3Bhbj4KICAgIDxkaXYgc3R5bGU9"
    "ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc2cmVtO2NvbG9yOnZhcigtLXR4MikiPiR7"
    "Q01TLmZsYXNoVGV4dH0gPGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSIgaWQ9ImNkIj4xNDo1Mjwv"
    "Yj48L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyIgZGF0YS1hY3Q9ImZsYXNoIj4ke0NN"
    "Uy5mbGFzaEJ0bn08L2J1dHRvbj48L2Rpdj5gKX0KICA8ZGl2IGNsYXNzPSJ3aGVlbCI+PGRpdiBj"
    "bGFzcz0icmluZyI+JHtTVE9DSy5tYXAoKHMsaSk9PmA8YiBzdHlsZT0idHJhbnNmb3JtOnJvdGF0"
    "ZSgke2kqNjB9ZGVnKSB0cmFuc2xhdGUoNzBweCkgcm90YXRlKC0ke2kqNjB9ZGVnKSI+JHtzLmNv"
    "ZGV9PC9iPmApLmpvaW4oIiIpfTwvZGl2PgogICAgPGRpdiBjbGFzcz0iY2FwIj48ZW0+JHtDTVMu"
    "d2hlZWxLaWNrfTwvZW0+PGI+JHtDTVMud2hlZWxUaXRsZX08L2I+CiAgICAgIDxkaXYgY2xhc3M9"
    "ImNsb3VkIiBzdHlsZT0ibWFyZ2luLXRvcDo2cHgiPjxkaXYgY2xhc3M9InQiPiR7WzEsMl0ubWFw"
    "KCgpPT5DTVMud2hlZWxXb3Jkcy5zcGxpdCgiLCIpLm1hcCh3PT5gPGk+JHt3LnRyaW0oKX08L2k+"
    "YCkuam9pbigiIikpLmpvaW4oIiIpfTwvZGl2PjwvZGl2PjwvZGl2PjwvZGl2PgogICR7Rygic2Fs"
    "ZXNGZWVkIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkp1c3Qgc29sZDwvaDM+PHAgY2xhc3M9InN1"
    "YiI+TGl2ZSBmZWVkIOKAlCBhbHNvIGF1dG8tcG9zdGVkIHRvIHlvdXIgY2hhbm5lbC48L3A+CiAg"
    "ICAke0ZFRUQubGVuZ3RoP0ZFRUQubWFwKChbZixjLGEsd10pPT5gPGRpdiBjbGFzcz0iZmVlZGxp"
    "bmUiPjxzcGFuPiR7Zn08L3NwYW4+PGI+JHtjfTwvYj4gc29sZCBmb3IgPGI+JHthfTwvYj48c3Bh"
    "biBzdHlsZT0ibWFyZ2luLWxlZnQ6YXV0byI+JHt3fTwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKQog"
    "ICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gc2FsZXMgeWV0IHRvZGF5"
    "LjwvcD4nfTwvZGl2PmApfQogIDxkaXYgY2xhc3M9InNlYyIgaWQ9InNob3BsaXN0Ij48aDI+JHtD"
    "TVMuc2hvcEhlYWR9PC9oMj48YSBkYXRhLWFjdD0ic29ydCI+JHtDTVMuc2hvcFNvcnR9PC9hPjwv"
    "ZGl2PgogIDxkaXYgY2xhc3M9Im1zZWFyY2giPjxkaXYgY2xhc3M9ImZpZWxkd3JhcCI+JHtpYygi"
    "c2VhcmNoIil9PGlucHV0IGlkPSJxIiBwbGFjZWhvbGRlcj0iJHtDTVMuc2VhcmNoUGh9Ij48L2Rp"
    "dj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBkYXRhLWFjdD0iYWxlcnRzIj4ke0NN"
    "Uy5hbGVydHNCdG59PC9idXR0b24+PC9kaXY+CiAgPGRpdiBjbGFzcz0iY2hpcHJvdyIgaWQ9ImNo"
    "aXBzIj4KICAgIDxidXR0b24gY2xhc3M9Im9uIiBkYXRhLWZpbHRlcj0iYWxsIj5BbGw8L2J1dHRv"
    "bj48YnV0dG9uIGRhdGEtZmlsdGVyPSJzdG9jayI+SW4gc3RvY2s8L2J1dHRvbj4KICAgIDxidXR0"
    "b24gZGF0YS1maWx0ZXI9ImRlYWwiPk9uIGRlYWw8L2J1dHRvbj48YnV0dG9uIGRhdGEtZmlsdGVy"
    "PSJjaGVhcCI+VW5kZXIg4oK5MzU8L2J1dHRvbj4KICAgICR7RygidmFyaWFudHMiLGA8YnV0dG9u"
    "IGRhdGEtZmlsdGVyPSJ2MmZhIj5ObyAyRkE8L2J1dHRvbj48YnV0dG9uIGRhdGEtZmlsdGVyPSJ2"
    "bWFpbCI+V2l0aCBtYWlsPC9idXR0b24+PGJ1dHRvbiBkYXRhLWZpbHRlcj0idnNwYW0iPlNwYW0t"
    "ZnJlZTwvYnV0dG9uPmApfTwvZGl2PgogIDxkaXYgaWQ9InNob3Bib2R5Ij4ke3Nob3BCb2R5KCl9"
    "PC9kaXY+YDsKfQpmdW5jdGlvbiBzaG9wQm9keSgpe3JldHVybiBgJHtGSUxURVJFRC5sZW5ndGg/"
    "YDxkaXYgY2xhc3M9InJvd3MiPiR7RklMVEVSRUQubWFwKHNob3BSb3cpLmpvaW4oIiIpfTwvZGl2"
    "PmA6RU1QVFl9YDt9CmZ1bmN0aW9uIHByaWNlQml0cyhzKXsKICByZXR1cm4gYCR7cy53YXM/YDxz"
    "PiR7bW9uZXkocy53YXMpfTwvcz5gOiIifSR7bW9uZXkocy5wcmljZSl9YDsKfQpmdW5jdGlvbiBz"
    "aG9wUm93KHMpe2NvbnN0IGk9U1RPQ0suaW5kZXhPZihzKSxvZmY9cy5xdHk9PT0wOwogIHJldHVy"
    "biBgPGRpdiBjbGFzcz0icm93IiBkYXRhLWJ1eT0iJHtpfSI+PGRpdiBjbGFzcz0iZmxhZyI+JHtz"
    "LmZ9PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJtaWQiPjxiPiR7ZXNjKHMuYyl9ICR7cy5jb2RlfTwv"
    "Yj4KICAgICAgPHNtYWxsPiR7b2ZmPyJPdXQgb2Ygc3RvY2sg4oCUIGdldCBhbiBhbGVydCI6cy5x"
    "dHkrIiBpbiBzdG9jayDCtyBpbnN0YW50In08L3NtYWxsPgogICAgICAke0coInJldmlld3MiLGA8"
    "ZGl2IHN0eWxlPSJtYXJnaW4tdG9wOjNweCI+JHtzdGFycyhzLmMpfTwvZGl2PmApfQogICAgICAk"
    "e0coInZhcmlhbnRzIixgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDozcHg7ZGlzcGxheTpmbGV4O2dh"
    "cDo0cHg7ZmxleC13cmFwOndyYXAiPgogICAgICAgIDxzcGFuIGNsYXNzPSJwaWxsIj5ObyAyRkE8"
    "L3NwYW4+JHtzLmhvdD8nPHNwYW4gY2xhc3M9InBpbGwiPldpdGggbWFpbDwvc3Bhbj4nOiIifTwv"
    "ZGl2PmApfTwvZGl2PgogICAgPGRpdiBjbGFzcz0icnQiPjxkaXYgY2xhc3M9InAiPiR7cHJpY2VC"
    "aXRzKHMpfTwvZGl2PgogICAgICAke0coImR5blByaWNlIixgPHNtYWxsIHN0eWxlPSJkaXNwbGF5"
    "OmJsb2NrO2ZvbnQtc2l6ZTouNnJlbTtjb2xvcjp2YXIoLS10eDIpIj5saXZlIHByaWNlPC9zbWFs"
    "bD5gKX0KICAgICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo1cHg7anVzdGlmeS1jb250"
    "ZW50OmZsZXgtZW5kO21hcmdpbi10b3A6NnB4Ij4KICAgICAgICAke0coIndpc2hsaXN0IixgPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtd2lzaD0iJHtpfSI+JHtXSVNILmluY2x1ZGVz"
    "KGkpPyLimaUiOiLimaEifTwvYnV0dG9uPmApfQogICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4"
    "cyI+JHtvZmY/Ik5vdGlmeSBNZSI6IkJ1eSJ9PC9idXR0b24+PC9kaXY+PC9kaXY+PC9kaXY+YDt9"
    "CmZ1bmN0aW9uIHNob3BDYXJkKHMpe2NvbnN0IGk9U1RPQ0suaW5kZXhPZihzKSxvZmY9cy5xdHk9"
    "PT0wOwogIHJldHVybiBgPGRpdiBjbGFzcz0icGNhcmQiIGRhdGEtYnV5PSIke2l9IiBzdHlsZT0i"
    "cG9zaXRpb246cmVsYXRpdmUiPgogICAgJHtHKCJ3aXNobGlzdCIsYDxzcGFuIGNsYXNzPSJoZWFy"
    "dCAke1dJU0guaW5jbHVkZXMoaSk/Im9uIjoiIn0iIGRhdGEtd2lzaD0iJHtpfSI+JHtpYygic3Rh"
    "ciIpfTwvc3Bhbj5gKX0KICAgIDxkaXYgY2xhc3M9InRvcCI+PGRpdiBjbGFzcz0iZmxhZyI+JHtz"
    "LmZ9PC9kaXY+CiAgICAgIDxzcGFuIGNsYXNzPSJwaWxsICR7b2ZmPyJvZmYiOiJvbiJ9Ij4ke29m"
    "Zj8iU29sZCBvdXQiOnMucXR5KyIgbGVmdCJ9PC9zcGFuPjwvZGl2PgogICAgPGRpdj48ZGl2IGNs"
    "YXNzPSJubSI+JHtlc2Mocy5jKX08L2Rpdj48ZGl2IGNsYXNzPSJtZXRhIj4ke3MuY29kZX0gwrcg"
    "aW5zdGFudDwvZGl2PgogICAgICAke0coInJldmlld3MiLGA8ZGl2IHN0eWxlPSJtYXJnaW4tdG9w"
    "OjRweCI+JHtzdGFycyhzLmMpfTwvZGl2PmApfTwvZGl2PgogICAgPGRpdiBjbGFzcz0icHJpY2Ui"
    "PiR7cHJpY2VCaXRzKHMpfTwvZGl2PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIHNtIj4k"
    "e29mZj8iTm90aWZ5IE1lIjoiQnV5IE5vdyJ9PC9idXR0b24+PC9kaXY+YDt9CgpmdW5jdGlvbiB2"
    "T3JkZXJzKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5Zb3VyIE9yZGVyczwvaDI+"
    "PGEgZGF0YS1hY3Q9ImNzdiI+RXhwb3J0PC9hPjwvZGl2PgogICR7T1JERVJTLm1hcCgobyxvaSk9"
    "PmA8ZGl2IGNsYXNzPSJjYXJkIiBkYXRhLW9yZGVyLWlkPSIke28uaWR9Ij4KICAgIDxkaXYgc3R5"
    "bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEycHgiPgogICAgICA8ZGl2"
    "IGNsYXNzPSJmbGFnIj4ke28uZn08L2Rpdj4KICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13"
    "aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44NXJlbTtkaXNwbGF5OmJsb2NrIj4ke28ubnVt"
    "fTwvYj4KICAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi43"
    "cmVtIj4ke28ud2hlbn08L3NtYWxsPjwvZGl2PgogICAgICA8ZGl2IHN0eWxlPSJ0ZXh0LWFsaWdu"
    "OnJpZ2h0Ij48c3BhbiBjbGFzcz0icGlsbCAke28uc3Q9PT0iRGVsaXZlcmVkIj8ib24iOiJvZmYi"
    "fSI+JHtvLnN0fTwvc3Bhbj48L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQi"
    "IHN0eWxlPSJtYXJnaW46MTBweCAwIDA7YmFja2dyb3VuZDp2YXIoLS1jYXJkMikiPgogICAgICA8"
    "ZGl2IGNsYXNzPSJrdiI+PHNwYW4+UGhvbmUgbnVtYmVyPC9zcGFuPgogICAgICAgIDxiPjxjb2Rl"
    "IHN0eWxlPSJmb250LXNpemU6LjhyZW07dXNlci1zZWxlY3Q6YWxsIj4ke2VzYyhvLnBob25lfHwi"
    "4oCUIil9PC9jb2RlPgogICAgICAgICR7by5waG9uZT9gPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNv"
    "ZnQiIGRhdGEtY29weT0idGV4dCIgZGF0YS1jb3B5LXZhbHVlPSIke2VzYyhvLnBob25lKX0iPkNv"
    "cHk8L2J1dHRvbj5gOiIifTwvYj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPjJG"
    "QSBwYXNzd29yZDwvc3Bhbj4KICAgICAgICA8Yj48Y29kZSBzdHlsZT0iZm9udC1zaXplOi44cmVt"
    "O3VzZXItc2VsZWN0OmFsbCI+JHtlc2Moby50d29mYXx8Im5vbmUiKX08L2NvZGU+CiAgICAgICAg"
    "JHtvLnR3b2ZhP2A8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1jb3B5PSJ0ZXh0IiBk"
    "YXRhLWNvcHktdmFsdWU9IiR7ZXNjKG8udHdvZmEpfSI+Q29weTwvYnV0dG9uPmA6IiJ9PC9iPjwv"
    "ZGl2PgogICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+TGFzdCBsb2dpbiBjb2RlPC9zcGFuPgog"
    "ICAgICAgIDxiPjxjb2RlIHN0eWxlPSJmb250LXNpemU6LjhyZW07dXNlci1zZWxlY3Q6YWxsIj4k"
    "e2VzYyhvLm90cHx8Im5vdCB5ZXQiKX08L2NvZGU+CiAgICAgICAgJHtvLm90cD9gPGJ1dHRvbiBj"
    "bGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtY29weT0idGV4dCIgZGF0YS1jb3B5LXZhbHVlPSIke2Vz"
    "YyhvLm90cCl9Ij5Db3B5PC9idXR0b24+YDoiIn08L2I+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9"
    "Imt2Ij48c3Bhbj5TdGF0dXM8L3NwYW4+PGI+JHtvLmxvZ2luPT09ImluIgogICAgICAgID8gJzxz"
    "cGFuIGNsYXNzPSJwaWxsIG9uIj5Mb2dnZWQgaW4gwrcgeW91cnMgb25seTwvc3Bhbj4nCiAgICAg"
    "ICAgOiAoby53aW5kb3c+MAogICAgICAgICAgICA/ICc8c3BhbiBjbGFzcz0icGlsbCI+Q29kZXMg"
    "Zm9yd2FyZGluZyDCtyAnK28ud2luZG93KycgbWluIGxlZnQ8L3NwYW4+JwogICAgICAgICAgICA6"
    "ICc8c3BhbiBjbGFzcz0icGlsbCBvZmYiPkhhbmRvdmVyIGNvbXBsZXRlPC9zcGFuPicpfTwvYj48"
    "L2Rpdj4KICAgIDwvZGl2PgogICAgJHtHKCJ0aW1lbGluZSIsYDxkaXYgY2xhc3M9InRsIj4KICAg"
    "ICAgPGRpdiBjbGFzcz0ib2siPjxiPlBhaWQ8L2I+PHNtYWxsPiR7by53aGVufTwvc21hbGw+PC9k"
    "aXY+CiAgICAgIDxkaXYgY2xhc3M9Im9rIj48Yj5EZWxpdmVyZWQ8L2I+PHNtYWxsPlNlc3Npb24g"
    "aGFuZGVkIG92ZXIgaW5zdGFudGx5PC9zbWFsbD48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iJHtv"
    "LnN0PT09IkRlbGl2ZXJlZCI/Im9rIjoiIn0iPjxiPk9UUCByZWNlaXZlZDwvYj48c21hbGw+JHtv"
    "LnN0PT09IkRlbGl2ZXJlZCI/IkNvZGUgZm9yd2FyZGVkIGluLWFwcCI6IldhaXRpbmcifTwvc21h"
    "bGw+PC9kaXY+CiAgICAgIDxkaXY+PGI+Q2xvc2VkPC9iPjxzbWFsbD5BdXRvLWNsb3NlcyBhZnRl"
    "ciB0aGUgd2FycmFudHkgd2luZG93PC9zbWFsbD48L2Rpdj48L2Rpdj5gKX0KICAgIDxkaXYgY2xh"
    "c3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTFweCI+CiAgICAgIDxidXR0b24gY2xhc3M9"
    "ImJ0biBzbSIgZGF0YS1vdHA9IiR7by5udW19Ij5HZXQgT1RQPC9idXR0b24+CiAgICAgICR7Rygi"
    "cmVkb3dubG9hZCIsYDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBkYXRhLW9yZD0iZGV0YWls"
    "cyI+RGV0YWlsczwvYnV0dG9uPmApfTwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHls"
    "ZT0ibWFyZ2luLXRvcDo3cHgiPgogICAgICAke0coInNlc3Npb25GaWxlIixgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIHNtIHNvZnQiIGRhdGEtb3JkPSJmaWxlIj4ke2ljKCJjb3B5Iil9IEZpbGU8L2J1dHRv"
    "bj5gKX0KICAgICAgJHtHKCJyZWNlaXB0cyIsYDxidXR0b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBk"
    "YXRhLW9yZD0icmVjZWlwdCI+UmVjZWlwdDwvYnV0dG9uPmApfQogICAgICAkeyhvLmxvZ2luPT09"
    "ImluIik/IiI6YDxidXR0b24gY2xhc3M9ImJ0biBzbSIgZGF0YS1sb2dnZWRpbj0iJHtvLmlkfSI+"
    "SSdtIGxvZ2dlZCBpbjwvYnV0dG9uPmB9CiAgICAgICR7Rygid2FycmFudHkiLGA8YnV0dG9uIGNs"
    "YXNzPSJidG4gc20gZ2hvc3QiIGRhdGEtY2xhaW0tb3JkZXI9IiR7by5pZH0iPlJlcG9ydCBpc3N1"
    "ZTwvYnV0dG9uPmApfTwvZGl2PgogICAgJHtHKCJyZXZpZXdzIixgPGRpdiBjbGFzcz0ia3YiIHN0"
    "eWxlPSJtYXJnaW4tdG9wOjlweCI+PHNwYW4+UmF0ZSB0aGlzIG9yZGVyPC9zcGFuPgogICAgICA8"
    "Yj48YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1yYXRlPSIke28uY30iPlJhdGU8L2J1"
    "dHRvbj48L2I+PC9kaXY+YCl9CiAgPC9kaXY+YCkuam9pbigiIil9CgogIDxkaXYgY2xhc3M9ImNh"
    "cmQiIHN0eWxlPSJ0ZXh0LWFsaWduOmNlbnRlciI+PGgzPk5lZWQgaGVscCB3aXRoIGFuIG9yZGVy"
    "PzwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj4ke0ZFQVQuYXV0b1JlZnVuZD8iRmFpbGVkIGxvZ2lu"
    "cyBhcmUgcmVmdW5kZWQgYXV0b21hdGljYWxseSB3aXRoaW4gMTAgbWludXRlcy4iOiJDb250YWN0"
    "IHN1cHBvcnQgYW5kIHdlIHdpbGwgbG9vayBpbnRvIGl0LiJ9PC9wPgogICAgPGRpdiBjbGFzcz0i"
    "YnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48YnV0dG9uIGNsYXNzPSJidG4gc29mdCIg"
    "ZGF0YS1hY3Q9InN1cHBvcnQiPlN1cHBvcnQ8L2J1dHRvbj4KICAgICR7RygiZGlzcHV0ZSIsYDxi"
    "dXR0b24gY2xhc3M9ImJ0biBnaG9zdCIgZGF0YS1kaXNwdXRlPkRpc3B1dGU8L2J1dHRvbj5gKX08"
    "L2Rpdj48L2Rpdj5gOwp9CgpmdW5jdGlvbiB2RWFybigpewogIHJldHVybiBgJHtHKCJzdHJlYWsi"
    "LGA8ZGl2IGNsYXNzPSJjYXJkIj4KICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1p"
    "dGVtczpjZW50ZXI7Z2FwOjEycHgiPgogICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJ6YXAi"
    "KX08L2Rpdj48ZGl2IHN0eWxlPSJmbGV4OjEiPjxoMz5EYWlseSBDaGVjay1pbjwvaDM+PHAgY2xh"
    "c3M9InN1YiI+RGF5ICR7RUFSTi5zdHJlYWt9IHN0cmVhayDCtyAke21vbmV5KEVBUk4uZGFpbHkp"
    "fSB3YWl0aW5nPC9wPjwvZGl2PjwvZGl2PgogICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dh"
    "cDo2cHg7bWFyZ2luOjE0cHggMCI+CiAgICAgICR7WzEsMiwzLDQsNSw2LDddLm1hcChkPT5gPGRp"
    "diBzdHlsZT0iZmxleDoxO2hlaWdodDozNHB4O2JvcmRlci1yYWRpdXM6OXB4O2Rpc3BsYXk6Z3Jp"
    "ZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9udC1zaXplOi42OHJlbTtmb250LXdlaWdodDo3MDA7Ym9y"
    "ZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTske2Q8PShFQVJOLnN0cmVha3x8MCk/ImJhY2tncm91"
    "bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiMwZjExMTM7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50"
    "IjoiYmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgyKSJ9Ij4ke2R9PC9kaXY+"
    "YCkuam9pbigiIil9PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBkYXRh"
    "LWNsYWltICR7RUFSTi5jbGFpbWVkPyJkaXNhYmxlZCI6IiJ9PiR7RUFSTi5jbGFpbWVkPyJDbGFp"
    "bWVkIHRvZGF5IjoiQ2xhaW0gIittb25leShFQVJOLmRhaWx5KX08L2J1dHRvbj48L2Rpdj5gKX0K"
    "ICAke0coImxveWFsdHkiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+TG95YWx0eSBQb2ludHM8L2gz"
    "PjxwIGNsYXNzPSJzdWIiPjEgcG9pbnQgcGVyIOKCuTEwIHNwZW50LiAxMDAgcG9pbnRzID0g4oK5"
    "MTAgYmFsYW5jZS48L3A+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+QmFsYW5jZTwvc3Bhbj48"
    "Yj4ke0VBUk4ucG9pbnRzfSBwdHM8L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+"
    "V29ydGg8L3NwYW4+PGI+JHttb25leShFQVJOLnBvaW50c1dvcnRoKX08L2I+PC9kaXY+CiAgICA8"
    "YnV0dG9uIGNsYXNzPSJidG4gYmxvY2siIGlkPSJyZWRlZW1nbyIgJHtFQVJOLnBvaW50cz4wPyIi"
    "OiJkaXNhYmxlZCJ9PlJlZGVlbSBQb2ludHM8L2J1dHRvbj48L2Rpdj5gKX0KICAke2FueUYoInJl"
    "ZkRhc2giLCJyZWZMMiIpP2A8ZGl2IGNsYXNzPSJjYXJkIj48aDM+SW52aXRlICZhbXA7IEVhcm48"
    "L2gzPgogICAgPHAgY2xhc3M9InN1YiI+JHtGRUFULnJlZkwyPyI1JSBmcm9tIHBlb3BsZSB5b3Ug"
    "aW52aXRlLCBwbHVzIDElIGZyb20gdGhlIHBlb3BsZSB0aGV5IGludml0ZS4iOiIxJSBvZiBldmVy"
    "eSBkZXBvc2l0IHlvdXIgaW52aXRlcyBldmVyIG1ha2UuIn08L3A+CiAgICA8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4+SW52aXRlczwvc3Bhbj48Yj4ke0VBUk4uaW52aXRlc308L2I+PC9kaXY+CiAgICAk"
    "e0coInJlZkwyIixgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkxldmVsIDEgZWFybmluZ3M8L3NwYW4+"
    "PGI+JHttb25leShFQVJOLmwxKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+"
    "TGV2ZWwgMiBlYXJuaW5nczwvc3Bhbj48Yj4ke21vbmV5KEVBUk4ubDIpfTwvYj48L2Rpdj5gKX0K"
    "ICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Ub3RhbCBlYXJuZWQ8L3NwYW4+PGI+JHttb25leShF"
    "QVJOLmVhcm5lZCl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPllvdXIgbGlu"
    "azwvc3Bhbj48YiBzdHlsZT0iZm9udC1zaXplOi43MnJlbSI+JHtlc2MoRUFSTi5saW5rKX08L2I+"
    "PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxi"
    "dXR0b24gY2xhc3M9ImJ0biIgZGF0YS1jb3B5PSJyZWYiPiR7aWMoImNvcHkiKX0gQ29weSBMaW5r"
    "PC9idXR0b24+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0YS1jb3B5PSJyZWYiPlNo"
    "YXJlPC9idXR0b24+PC9kaXY+CiAgICAke0coInJlZkRhc2giLGA8ZGl2IGNsYXNzPSJzZWMiIHN0"
    "eWxlPSJtYXJnaW4tdG9wOjZweCI+PGgyIHN0eWxlPSJmb250LXNpemU6Ljc4cmVtIj5SZWNlbnQg"
    "aW52aXRlczwvaDI+PC9kaXY+CiAgICAke0VBUk4ucmVjZW50Lmxlbmd0aD9FQVJOLnJlY2VudC5t"
    "YXAoKFt1LGEsZV0pPT5gPGRpdiBjbGFzcz0iZmVlZGxpbmUiPjxiPiR7ZXNjKHUpfTwvYj4gJHtl"
    "c2MoYSl9PHNwYW4gc3R5bGU9Im1hcmdpbi1sZWZ0OmF1dG87Y29sb3I6dmFyKC0tb2spIj4ke2Vz"
    "YyhlKX08L3NwYW4+PC9kaXY+YCkuam9pbigiIikKICAgICAgOic8cCBjbGFzcz0ic3ViIiBzdHls"
    "ZT0ibWFyZ2luOjAiPk5vYm9keSBoYXMgam9pbmVkIHRocm91Z2ggeW91ciBsaW5rIHlldC48L3A+"
    "J30KICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MTFweCAwIDAiPlJlZmVycmFsIGVh"
    "cm5pbmdzIGxhbmQgaW4geW91ciBiYWxhbmNlIGF1dG9tYXRpY2FsbHkg4oCUIG5vdGhpbmcgdG8g"
    "Y2xhaW0uPC9wPmApfQogIDwvZGl2PmA6IiJ9CiAgJHtHKCJsZWFkZXJib2FyZCIsYDxkaXYgY2xh"
    "c3M9ImNhcmQiPjxoMz5UaGlzIHdlZWsncyB0b3AgYnV5ZXJzPC9oMz48cCBjbGFzcz0ic3ViIj5O"
    "dW1iZXIgb25lIHdpbnMg4oK5NTAwIG9uIFN1bmRheS48L3A+CiAgICAke0VBUk4ubGVhZGVycy5s"
    "ZW5ndGg/RUFSTi5sZWFkZXJzLm1hcCgoW3IsdSx2XSxpKT0+YDxkaXYgY2xhc3M9ImxiIj48c3Bh"
    "biBjbGFzcz0icmsgJHtpPT09MD8iZyI6IiJ9Ij4ke2VzYyhyKX08L3NwYW4+CiAgICAgICAgPGRp"
    "diBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44cmVtIj4k"
    "e2VzYyh1KX08L2I+PC9kaXY+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+JHtlc2Modil9PC9i"
    "PjwvZGl2PmApLmpvaW4oIiIpCiAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjow"
    "Ij5ObyBwdXJjaGFzZXMgeWV0IHRoaXMgd2Vlay48L3A+J308L2Rpdj5gKX0KICAke0coIndpbmJh"
    "Y2siLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0"
    "ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICA8c3BhbiBjbGFzcz0icGlsbCBob3QiPiR7aWMoImZp"
    "cmUiKX0gQ29tZWJhY2s8L3NwYW4+CiAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7"
    "Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj4xMCUgb2ZmIOKAlCBleHBpcmVzIGlu"
    "IDQ4IGhvdXJzPC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMiIGlkPSJvZmZlcnVzZSI+"
    "VXNlPC9idXR0b24+PC9kaXY+YCl9CiAgPGRpdiBjbGFzcz0ic2VjIj48aDI+VGFza3M8L2gyPjwv"
    "ZGl2PgogICR7RUFSTi50YXNrcy5tYXAoKFt0LHIsZG9uZV0pPT5gPGRpdiBjbGFzcz0iY2FyZCB0"
    "aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjExcHgiPgog"
    "ICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2RvbmU/aWMoImNoZWNrIik6aWMoInphcCIpfTwvZGl2"
    "PgogICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNp"
    "emU6LjgycmVtIj4ke3R9PC9iPjxicj48c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9u"
    "dC1zaXplOi43cmVtIj5SZXdhcmQgJHtyfTwvc21hbGw+PC9kaXY+CiAgICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biB4cyAke2RvbmU/InNvZnQiOiIifSIgJHtkb25lPyJkaXNhYmxlZCI6IiJ9IGRhdGEt"
    "YWN0PSJ0YXNrIj4ke2RvbmU/IkRvbmUiOiJTdGFydCJ9PC9idXR0b24+PC9kaXY+YCkuam9pbigi"
    "Iil9YDsKfQoKZnVuY3Rpb24gdldhbGxldCgpewogIHJldHVybiBgPGRpdiBjbGFzcz0iaGVybyIg"
    "c3R5bGU9InRleHQtYWxpZ246Y2VudGVyIj4KICAgIDxkaXYgY2xhc3M9ImtpY2siPiR7Q01TLndh"
    "bEtpY2t9PC9kaXY+CiAgICA8aDEgc3R5bGU9ImZvbnQtc2l6ZToyLjNyZW0iPuKCuTxzcGFuIGlk"
    "PSJiYWwyIiBjbGFzcz0idGljayI+JHtCQUwudG9GaXhlZCgyKX08L3NwYW4+PC9oMT4KICAgIDxw"
    "IHN0eWxlPSJtYXJnaW46MCBhdXRvIDE0cHgiPiR7Q01TLndhbFN1Yn08L3A+CiAgICA8ZGl2IHN0"
    "eWxlPSJtYXJnaW46MCBhdXRvO21heC13aWR0aDozMDBweCI+CiAgICAgIDxidXR0b24gY2xhc3M9"
    "ImJ0biBjdGEgYmxvY2siIGRhdGEtZGVwPiR7aWMoInBsdXMiKX0gJHtDTVMud2FsQWRkfTwvYnV0"
    "dG9uPgogICAgICAke0coImNyeXB0byIsYDxidXR0b24gY2xhc3M9ImJ0biBnbGFzcyBibG9jayIg"
    "c3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRhLWNyeXB0bz5QYXkgd2l0aCBDcnlwdG88L2J1dHRv"
    "bj5gKX08L2Rpdj48L2Rpdj4KICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxh"
    "eTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICA8c3BhbiBjbGFzcz0icGls"
    "bCBvbiI+PGkgY2xhc3M9ImRvdCI+PC9pPkF1dG8tdmVyaWZ5IE9OPC9zcGFuPgogICAgPGRpdiBz"
    "dHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgy"
    "KSI+VG9wLXVwcyBhcmUgY3JlZGl0ZWQgYXMgc29vbiBhcyB0aGV5IGFycml2ZTwvZGl2PgogICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtYWN0PSJzdGF0dXMiPlN0YXR1czwvYnV0"
    "dG9uPjwvZGl2PgogICR7RygiZGVwQm9udXMiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+VG9wLXVw"
    "IGJvbnVzPC9oMz48cCBjbGFzcz0ic3ViIj5FeHRyYSBjcmVkaXQgYWRkZWQgYXV0b21hdGljYWxs"
    "eS48L3A+CiAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjdweDttYXJnaW4tdG9wOjEw"
    "cHgiPgogICAgICAke1tbIuKCuTUwMCIsIis1JSJdLFsi4oK5MTAwMCIsIis4JSJdLFsi4oK5MjAw"
    "MCIsIisxMiUiXV0ubWFwKChbYSxiXSk9PmA8ZGl2IHN0eWxlPSJmbGV4OjE7dGV4dC1hbGlnbjpj"
    "ZW50ZXI7cGFkZGluZzo5cHggNHB4O2JvcmRlci1yYWRpdXM6MTJweDtib3JkZXI6MXB4IHNvbGlk"
    "IHZhcigtLWxpbmUpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpIj4KICAgICAgICA8YiBzdHlsZT0i"
    "Zm9udC1zaXplOi44cmVtO2Rpc3BsYXk6YmxvY2siPiR7YX08L2I+PHNtYWxsIHN0eWxlPSJmb250"
    "LXNpemU6LjY4cmVtO2NvbG9yOnZhcigtLW9rKSI+JHtifTwvc21hbGw+PC9kaXY+YCkuam9pbigi"
    "Iil9PC9kaXY+PC9kaXY+YCl9CiAgPGRpdiBjbGFzcz0ic2VjIj48aDI+UXVpY2sgVG9wLXVwPC9o"
    "Mj48L2Rpdj4KICA8ZGl2IGNsYXNzPSJncmlkIiBzdHlsZT0iZ3JpZC10ZW1wbGF0ZS1jb2x1bW5z"
    "OnJlcGVhdCgzLDFmcikiPgogICAgJHtbNTAsMTAwLDIwMCw1MDAsMTAwMCwyMDAwXS5tYXAoYT0+"
    "YDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLWFtdD0iJHthfSI+4oK5JHthfTwvYnV0dG9u"
    "PmApLmpvaW4oIiIpfTwvZGl2PgogICR7RygiZGVwTGltaXRzIixgPHAgY2xhc3M9InN1YiIgc3R5"
    "bGU9Im1hcmdpbjo5cHggMCAwO2ZvbnQtc2l6ZTouN3JlbSI+TWluaW11bSDigrkxMCDCtyBtYXhp"
    "bXVtIOKCuTIwLDAwMCBwZXIgdG9wLXVwLjwvcD5gKX0KICA8ZGl2IGNsYXNzPSJjYXJkIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMnB4Ij48aDM+JHtDTVMucGF5VGl0bGV9PC9oMz4KICAgIDxwIGNsYXNz"
    "PSJzdWIiPiR7Q01TLnBheVN1Yn08L3A+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VVBJIElE"
    "PC9zcGFuPjxiPiR7ZXNjKFNUT1JFLnVwaXx8Im5vdCBzZXQgeWV0Iil9PC9iPjwvZGl2PgogICAg"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPlBheWVlPC9zcGFuPjxiPiR7ZXNjKFNUT1JFLnBheWVlfHwi"
    "4oCUIil9PC9iPjwvZGl2PgogICAgJHtHKCJ1cGlSb3RhdGUiLGA8ZGl2IGNsYXNzPSJrdiI+PHNw"
    "YW4+SGFuZGxlcyBpbiByb3RhdGlvbjwvc3Bhbj48Yj4zIGFjdGl2ZTwvYj48L2Rpdj5gKX0KICAg"
    "IDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5QYXllZTwvc3Bhbj48Yj5Zb3VyIFN0b3JlIE5hbWU8L2I+"
    "PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+QXV0by12ZXJpZnk8L3NwYW4+PGIgc3R5"
    "bGU9ImNvbG9yOnZhcigtLW9rKSI+RW5hYmxlZDwvYj48L2Rpdj4KICAgICR7Rygid3JvbmdBbXQi"
    "LGA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+V3JvbmcgYW1vdW50PC9zcGFuPjxiPkNyZWRpdGVkIGFz"
    "IHBhaWQ8L2I+PC9kaXY+YCl9CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2sgc29mdCIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jb3B5PSJ1cGkiPiR7aWMoImNvcHkiKX0gQ29weSBV"
    "UEkgSUQ8L2J1dHRvbj48L2Rpdj4KICAke0coImFiYW5kb25SZW1pbmQiLGA8cCBjbGFzcz0ic3Vi"
    "IiBzdHlsZT0ibWFyZ2luOjEwcHggMCAwO2ZvbnQtc2l6ZTouN3JlbSI+V2Ugd2lsbCByZW1pbmQg"
    "eW91IGJlZm9yZSBhbiB1bmZpbmlzaGVkIHBheW1lbnQgZXhwaXJlcy48L3A+YCl9CiAgPGRpdiBj"
    "bGFzcz0ic2VjIj48aDI+JHtGRUFULmxlZGdlcj8iU3RhdGVtZW50IjoiSGlzdG9yeSJ9PC9oMj4k"
    "e0coImxlZGdlciIsYDxhIGRhdGEtYWRtaW4tYWN0PSJleHBvcnQiPkV4cG9ydDwvYT5gKX08L2Rp"
    "dj4KICAke1RYLm1hcCgodCxpKT0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNw"
    "bGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMXB4Ij4KICAgIDxkaXYgY2xhc3M9ImZs"
    "YWciPiR7dC5hWzBdPT09IisiP2ljKCJkb3duIik6aWMoInVwIil9PC9kaXY+CiAgICA8ZGl2IHN0"
    "eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjgycmVtIj4ke3Qu"
    "dH08L2I+PGJyPjxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjdyZW0i"
    "PiR7dC53fTwvc21hbGw+PC9kaXY+CiAgICA8ZGl2IHN0eWxlPSJ0ZXh0LWFsaWduOnJpZ2h0Ij48"
    "YiBzdHlsZT0iZm9udC1zaXplOi44NXJlbTtjb2xvcjoke3QuYVswXT09PSIrIj8idmFyKC0tb2sp"
    "IjoidmFyKC0tdHgpIn0iPiR7dC5hfTwvYj4KICAgICR7RygibGVkZ2VyIixgPGJyPjxzbWFsbCBz"
    "dHlsZT0iZm9udC1zaXplOi42NXJlbTtjb2xvcjp2YXIoLS10eDIpIj5iYWwg4oK5JHsoMjQ4LjUr"
    "aSo3KS50b0ZpeGVkKDIpfTwvc21hbGw+YCl9PC9kaXY+PC9kaXY+YCkuam9pbigiIil9YDsKfQoK"
    "ZnVuY3Rpb24gdllvdSgpewogIGNvbnN0IG1lPU1FfHx7fTsKICBjb25zdCBzcGVudD1tZS5zcGVu"
    "dHx8MCwgdGFyZ2V0PXNwZW50Pj01MDA/MjAwMDo1MDA7CiAgY29uc3QgcGN0PU1hdGgubWF4KDQs"
    "TWF0aC5taW4oMTAwLE1hdGgucm91bmQoc3BlbnQvdGFyZ2V0KjEwMCkpKTsKICBjb25zdCBhdj1t"
    "ZS5waG90bz9gPGltZyBzcmM9IiR7bWUucGhvdG99IiBhbHQ9IiIgc3R5bGU9IndpZHRoOjY0cHg7"
    "aGVpZ2h0OjY0cHg7Ym9yZGVyLXJhZGl1czo1MCU7b2JqZWN0LWZpdDpjb3ZlcjtkaXNwbGF5OmJs"
    "b2NrO21hcmdpbjowIGF1dG8gMTBweCIgb25lcnJvcj0idGhpcy5yZXBsYWNlV2l0aChPYmplY3Qu"
    "YXNzaWduKGRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoJ2RpdicpLHtjbGFzc05hbWU6J2ZsYWcnLHN0"
    "eWxlOid3aWR0aDo2NHB4O2hlaWdodDo2NHB4O21hcmdpbjowIGF1dG8gMTBweCd9KSkiPmAKICAg"
    "IDpgPGRpdiBjbGFzcz0iZmxhZyIgc3R5bGU9IndpZHRoOjY0cHg7aGVpZ2h0OjY0cHg7bWFyZ2lu"
    "OjAgYXV0byAxMHB4Ij4ke2ljKCJ1c2VyIil9PC9kaXY+YDsKICByZXR1cm4gYDxkaXYgY2xhc3M9"
    "ImNhcmQgc2hpbmVjYXJkIHRpbHQiIGRhdGEtdGlsdCBzdHlsZT0idGV4dC1hbGlnbjpjZW50ZXIi"
    "PjxzcGFuIGNsYXNzPSJnbGFyZSI+PC9zcGFuPgogICAgJHthdn0KICAgIDxoMyBzdHlsZT0iZm9u"
    "dC1zaXplOjEuMDVyZW0iPiR7ZXNjKG1lLm5hbWV8fCJZb3UiKX08L2gzPgogICAgPHAgY2xhc3M9"
    "InN1YiI+JHtlc2MobWUudXNlcm5hbWV8fCgiSUQgIisobWUuaWR8fCLigJQiKSkpfSR7bWUudXNl"
    "cm5hbWU/IiDCtyBJRCAiKyhtZS5pZHx8IuKAlCIpOiIifSR7bWUuam9pbmVkPyIgwrcgam9pbmVk"
    "ICIrZXNjKG1lLmpvaW5lZCk6IiJ9PC9wPgogICAgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dh"
    "cDo3cHg7anVzdGlmeS1jb250ZW50OmNlbnRlcjttYXJnaW4tdG9wOjEwcHg7ZmxleC13cmFwOndy"
    "YXAiPgogICAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+JHtlc2MobWUudGllcnx8IkJyb256ZSIp"
    "fTwvc3Bhbj48c3BhbiBjbGFzcz0icGlsbCI+JHttZS5vcmRlcnN8fDB9IG9yZGVyczwvc3Bhbj4K"
    "ICAgICAgPHNwYW4gY2xhc3M9InBpbGwiPiR7bW9uZXkobWUuYmFsYW5jZXx8MCl9PC9zcGFuPgog"
    "ICAgICAke0coImxveWFsdHkiLGA8c3BhbiBjbGFzcz0icGlsbCI+JHttZS5wb2ludHN8fDB9IHB0"
    "czwvc3Bhbj5gKX0KICAgICAgJHtHKCJzdHJlYWsiLGA8c3BhbiBjbGFzcz0icGlsbCI+JHttZS5z"
    "dHJlYWt8fDB9LWRheSBzdHJlYWs8L3NwYW4+YCl9CiAgICAgICR7KEZFQVQuYnV5ZXJCYWRnZSYm"
    "KG1lLm9yZGVyc3x8MCk+PTUpP2A8c3BhbiBjbGFzcz0icGlsbCBvbiI+JHtpYygiY2hlY2siKX0g"
    "VmVyaWZpZWQgYnV5ZXI8L3NwYW4+YDoiIn0KICAgICAgJHsobWUuZGVidD4wKT9gPHNwYW4gY2xh"
    "c3M9InBpbGwgaG90Ij4ke21vbmV5KG1lLmRlYnQpfSB1ZGhhcjwvc3Bhbj5gOiIifTwvZGl2Pgog"
    "ICAgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4O2hlaWdodDo3cHg7Ym9yZGVyLXJhZGl1czo5"
    "cHg7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7b3ZlcmZsb3c6aGlkZGVuIj4KICAgICAgPGkgc3R5"
    "bGU9ImRpc3BsYXk6YmxvY2s7aGVpZ2h0OjEwMCU7d2lkdGg6JHtwY3R9JTtiYWNrZ3JvdW5kOnZh"
    "cigtLXBpbGxncmFkKSI+PC9pPjwvZGl2PgogICAgPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdp"
    "bi10b3A6N3B4O2ZvbnQtc2l6ZTouNzJyZW0iPiR7bWUubmV4dF90aWVyPjAKICAgICAgPyBtb25l"
    "eShtZS5uZXh0X3RpZXIpKyIgbW9yZSBzcGVudCB0byByZWFjaCAiKyhzcGVudD49NTAwPyJQbGF0"
    "aW51bSI6IkdvbGQiKQogICAgICA6ICJUb3AgdGllciByZWFjaGVkIn08L3A+PC9kaXY+CiAgJHtH"
    "KCJ3aXNobGlzdCIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPlNhdmVkIGNvdW50cmllczwvaDI+PC9k"
    "aXY+CiAgICA8ZGl2IGNsYXNzPSJjYXJkIj4ke1dJU0gubGVuZ3RoP1dJU0gubWFwKGk9PmA8ZGl2"
    "IGNsYXNzPSJrdiI+PHNwYW4+JHtTVE9DS1tpXS5mfSAke2VzYyhTVE9DS1tpXS5jKX0gJHtTVE9D"
    "S1tpXS5jb2RlfTwvc3Bhbj4KICAgICAgPGI+PGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBkYXRhLWJ1"
    "eT0iJHtpfSI+QnV5PC9idXR0b24+PC9iPjwvZGl2PmApLmpvaW4oIiIpOic8cCBjbGFzcz0ic3Vi"
    "IiBzdHlsZT0ibWFyZ2luOjAiPlRhcCB0aGUgaGVhcnQgb24gYW55IGNvdW50cnkgdG8gc2F2ZSBp"
    "dCBoZXJlLjwvcD4nfTwvZGl2PmApfQogIDxkaXYgY2xhc3M9InNlYyI+PGgyPkFjY291bnQ8L2gy"
    "PjwvZGl2PgogIDxkaXYgY2xhc3M9ImNhcmQiPiR7W1siT3JkZXIgaGlzdG9yeSIsIm9yZGVycyJd"
    "LFsiV2FsbGV0ICYgcGF5bWVudHMiLCJ3YWxsZXQiXV0KICAgIC5jb25jYXQoYW55RigicmVmRGFz"
    "aCIsInJlZkwyIik/W1siUmVmZXJyYWwgcHJvZ3JhbSIsImVhcm4iXV06W10pCiAgICAubWFwKChb"
    "dCxnXSk9PmA8ZGl2IGNsYXNzPSJrdiIgc3R5bGU9ImN1cnNvcjpwb2ludGVyIiBkYXRhLWdvPSIk"
    "e2d9Ij48c3BhbiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4ke3R9PC9zcGFuPjxiPiR7aWMoImNo"
    "ZXYiKX08L2I+PC9kaXY+YCkuam9pbigiIil9CiAgICAke0coImkxOG4iLGA8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4gc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+TGFuZ3VhZ2U8L3NwYW4+PGI+CiAgICAg"
    "IDxzZWxlY3QgY2xhc3M9ImlucCIgc3R5bGU9ImhlaWdodDozMnB4O3dpZHRoOmF1dG87Zm9udC1z"
    "aXplOi43NHJlbSI+PG9wdGlvbj5FbmdsaXNoPC9vcHRpb24+PG9wdGlvbj7gpLngpL/gpKjgpY3g"
    "pKbgpYA8L29wdGlvbj48L3NlbGVjdD48L2I+PC9kaXY+YCl9PC9kaXY+CiAgJHtHKCJub3RpZlBy"
    "ZWZzIixgPGRpdiBjbGFzcz0ic2VjIj48aDI+Tm90aWZpY2F0aW9uczwvaDI+PC9kaXY+CiAgPGRp"
    "diBjbGFzcz0iY2FyZCI+CiAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5SZXN0"
    "b2NrIGFsZXJ0czxzbWFsbD5ETSBtZSB3aGVuIG15IGNvdW50cnkgaXMgYmFjazwvc21hbGw+PC9k"
    "aXY+CiAgICAgIDxkaXYgY2xhc3M9InRnICR7UFJFRlMucmVzdG9jaz8ib24iOiIifSIgZGF0YS1w"
    "cmVmPSJyZXN0b2NrIj48L2Rpdj48L2Rpdj4KICAgICR7RygicmVjZWlwdHMiLGA8ZGl2IGNsYXNz"
    "PSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5PcmRlciByZWNlaXB0czxzbWFsbD5TZW5kIGEgcmVjZWlw"
    "dCBhZnRlciBlYWNoIGJ1eTwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InRnICR7UFJF"
    "RlMucmVjZWlwdHM/Im9uIjoiIn0iIGRhdGEtcHJlZj0icmVjZWlwdHMiPjwvZGl2PjwvZGl2PmAp"
    "fQogICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+UHJvbW8gbWVzc2FnZXM8c21h"
    "bGw+Rmxhc2ggc2FsZXMgYW5kIGdpdmVhd2F5czwvc21hbGw+PC9kaXY+CiAgICAgIDxkaXYgY2xh"
    "c3M9InRnICR7UFJFRlMucHJvbW9zPyJvbiI6IiJ9IiBkYXRhLXByZWY9InByb21vcyI+PC9kaXY+"
    "PC9kaXY+CiAgICAke0coInB1c2hOb3RpZiIsYDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9"
    "InQiPk1pbmkgYXBwIHB1c2g8c21hbGw+TWVzc2FnZSBtZSB3aGVuIHNvbWV0aGluZyBoYXBwZW5z"
    "PC9zbWFsbD48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0idGcgJHtQUkVGUy5wdXNoPyJvbiI6IiJ9"
    "IiBkYXRhLXByZWY9InB1c2giPjwvZGl2PjwvZGl2PmApfQogIDwvZGl2PmApfQogIDxkaXYgY2xh"
    "c3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGJ1dHRvbiBjbGFzcz0iYnRuIHNv"
    "ZnQiIGlkPSJzdXBwb3J0Z28iPlN1cHBvcnQ8L2J1dHRvbj4KICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "Z2hvc3QiIGlkPSJ0ZXJtc2dvIj5UZXJtczwvYnV0dG9uPjwvZGl2PmA7Cn0KCmZ1bmN0aW9uIGJp"
    "bmRCdXkoKXsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS13aXNoXSIpLmZvckVh"
    "Y2goZWw9PmVsLm9uY2xpY2s9ZT0+ewogICAgZS5zdG9wUHJvcGFnYXRpb24oKTtjb25zdCBpPStl"
    "bC5kYXRhc2V0Lndpc2g7CiAgICBjb25zdCBrPVdJU0guaW5kZXhPZihpKTtpZihrPDApV0lTSC5w"
    "dXNoKGkpO2Vsc2UgV0lTSC5zcGxpY2UoaywxKTsKICAgIHRvYXN0KGs8MD8iU2F2ZWQgdG8gd2lz"
    "aGxpc3QiOiJSZW1vdmVkIGZyb20gd2lzaGxpc3QiKTthcHBseUZpbHRlcigpO30pOwogIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWJ1eV0iKS5mb3JFYWNoKGVsPT5lbC5vbmNsaWNr"
    "PWU9PnsKICAgIGlmKGUudGFyZ2V0LmNsb3Nlc3QoIltkYXRhLXRvYXN0XSxbZGF0YS13aXNoXSIp"
    "KXJldHVybjtjb25zdCBzPVNUT0NLWytlbC5kYXRhc2V0LmJ1eV07CiAgICBpZihzLnF0eT09PTAm"
    "JiFmYWxzZSl7dG9hc3QoIkFsZXJ0IHNldCDigJQgeW91J2xsIGdldCBhIERNIG9uIHJlc3RvY2si"
    "KTtyZXR1cm59CiAgICBidXlTaGVldChzLHMucXR5PT09MCk7fSk7Cn0KbGV0IFFUWT0xLENPVVBP"
    "Tj0wOwpmdW5jdGlvbiBidXlTaGVldChzLHByZSl7CiAgUVRZPTE7Q09VUE9OPTA7CiAgY29uc3Qg"
    "bWF4PUZFQVQuY2FydHx8RkVBVC5idWxrRGlzYz9NYXRoLm1heCgxLE1hdGgubWluKDI1LHMucXR5"
    "fHw1KSk6MTsKICBzaGVldChgPGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNl"
    "bnRlcjtnYXA6MTJweDttYXJnaW4tYm90dG9tOjE0cHgiPgogICAgICA8ZGl2IGNsYXNzPSJmbGFn"
    "IiBzdHlsZT0id2lkdGg6NTBweDtoZWlnaHQ6NTBweDtmb250LXNpemU6MS41cmVtIj4ke3MuZn08"
    "L2Rpdj4KICAgICAgPGRpdiBzdHlsZT0iZmxleDoxIj48aDMgc3R5bGU9Im1hcmdpbjowO2ZvbnQt"
    "c2l6ZToxcmVtIj4ke2VzYyhzLmMpfSBBY2NvdW50PC9oMz4KICAgICAgPHAgY2xhc3M9InN1YiIg"
    "c3R5bGU9Im1hcmdpbjowIj4ke3MuY29kZX0gwrcgJHtzLnF0eSsiIGluIHN0b2NrIMK3IGluc3Rh"
    "bnQifTwvcD4KICAgICAgJHtHKCJyZXZpZXdzIixgPGRpdiBzdHlsZT0ibWFyZ2luLXRvcDo0cHgi"
    "PiR7c3RhcnMocy5jKX08L2Rpdj5gKX08L2Rpdj48L2Rpdj4KICAgICR7RygiaGVhbHRoQ2hlY2si"
    "LGA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+U2Vzc2lvbiBoZWFsdGg8L3NwYW4+PGIgc3R5bGU9ImNv"
    "bG9yOnZhcigtLW9rKSI+JHtpYygiY2hlY2siKX0gY2hlY2tlZCBsaXZlPC9iPjwvZGl2PmApfQog"
    "ICAgJHtHKCJ2YXJpYW50cyIsYDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5WYXJpYW50PC9zcGFuPjxi"
    "PgogICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIHN0eWxlPSJoZWlnaHQ6MzJweDt3aWR0aDphdXRv"
    "O2ZvbnQtc2l6ZTouNzRyZW0iPjxvcHRpb24+Tm8gMkZBPC9vcHRpb24+PG9wdGlvbj4yRkEgb24g"
    "KCvigrk1KTwvb3B0aW9uPjxvcHRpb24+V2l0aCBtYWlsICgr4oK5OCk8L29wdGlvbj48L3NlbGVj"
    "dD48L2I+PC9kaXY+YCl9CiAgICAkeyhGRUFULmNhcnR8fEZFQVQuYnVsa0Rpc2MpP2A8ZGl2IGNs"
    "YXNzPSJrdiI+PHNwYW4+UXVhbnRpdHk8L3NwYW4+PGI+CiAgICAgIDxzcGFuIGNsYXNzPSJxdHki"
    "PjxidXR0b24gaWQ9InFtIj7iiJI8L2J1dHRvbj48YiBpZD0icW4iPjE8L2I+PGJ1dHRvbiBpZD0i"
    "cXAiPis8L2J1dHRvbj48L3NwYW4+PC9iPjwvZGl2PmA6IiJ9CiAgICAke0coImJ1bGtEaXNjIixg"
    "PHAgY2xhc3M9InN1YiIgaWQ9ImJkbm90ZSIgc3R5bGU9Im1hcmdpbjo2cHggMCAwO2ZvbnQtc2l6"
    "ZTouN3JlbSI+QnV5IDUrIGZvciA1JSBvZmYgwrcgMTArIGZvciAxMCUgb2ZmIMK3IDI1KyBmb3Ig"
    "MTUlIG9mZi48L3A+YCl9CiAgICAke0coImNvdXBvbnMiLGA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTBweCI+PGxhYmVsPkNvdXBvbiBjb2RlPC9sYWJlbD4KICAgICAgPGRp"
    "diBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo3cHgiPjxpbnB1dCBjbGFzcz0iaW5wIiBpZD0iY3Bu"
    "IiBwbGFjZWhvbGRlcj0iV0VMQ09NRTEwIj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBp"
    "ZD0iY3BuYXBwbHkiIHN0eWxlPSJmbGV4OjAgMCBhdXRvIj5BcHBseTwvYnV0dG9uPjwvZGl2Pjwv"
    "ZGl2PmApfQogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlByaWNlPC9zcGFuPjxiIGlkPSJicHJp"
    "Y2UiPiR7bW9uZXkocy5wcmljZSl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFu"
    "PllvdXIgYmFsYW5jZTwvc3Bhbj48Yj4ke21vbmV5KEJBTCl9PC9iPjwvZGl2PgogICAgPGRpdiBj"
    "bGFzcz0ia3YiPjxzcGFuPkFmdGVyIHB1cmNoYXNlPC9zcGFuPjxiIGlkPSJiYWZ0ZXIiPiR7bW9u"
    "ZXkoTWF0aC5tYXgoMCxCQUwtcy5wcmljZSkpfTwvYj48L2Rpdj4KICAgIDxkaXYgaWQ9InNwbGl0"
    "bm90ZSI+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjE0"
    "cHgiPjxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCIgZGF0YS1jbG9zZT5DYW5jZWw8L2J1dHRvbj4K"
    "ICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIgaWQ9ImJjb25maXJtIj5Db25maXJtIEJ1eTwv"
    "YnV0dG9uPjwvZGl2PgogICAgJHtHKCJjYXJ0IixgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQgYmxv"
    "Y2sgc20iIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgaWQ9ImJhZGRjYXJ0Ij5BZGQgdG8gY2FydDwv"
    "YnV0dG9uPmApfWApOwogIGNvbnN0IGc9aWQ9PmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKTsK"
    "ICBmdW5jdGlvbiB0b3RhbCgpewogICAgbGV0IHVuaXQ9cy5wcmljZSwgcT1RVFksIHQ9dW5pdCpx"
    "OwogICAgaWYoRkVBVC5idWxrRGlzYyl7Y29uc3QgZD1xPj0yNT8uMTU6cT49MTA/LjEwOnE+PTU/"
    "LjA1OjA7dD10KigxLWQpO30KICAgIGlmKEZFQVQuY291cG9ucyYmQ09VUE9OKXQ9dCooMS1DT1VQ"
    "T04vMTAwKTsKICAgIHJldHVybiBNYXRoLnJvdW5kKHQqMTAwKS8xMDA7CiAgfQogIGZ1bmN0aW9u"
    "IHBhaW50KCl7CiAgICBjb25zdCB0PXRvdGFsKCk7CiAgICBnKCJicHJpY2UiKS50ZXh0Q29udGVu"
    "dD1tb25leSh0KTsKICAgIGcoImJhZnRlciIpLnRleHRDb250ZW50PW1vbmV5KE1hdGgubWF4KDAs"
    "QkFMLXQpKTsKICAgIGNvbnN0IHNuPWcoInNwbGl0bm90ZSIpOwogICAgaWYoRkVBVC5zcGxpdFBh"
    "eSYmdD5CQUwpe3NuLmlubmVySFRNTD1gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlNwbGl0IHBheW1l"
    "bnQ8L3NwYW4+PGI+JHttb25leShCQUwpfSB3YWxsZXQgKyAke21vbmV5KHQtQkFMKX0gVVBJPC9i"
    "PjwvZGl2PmA7fQogICAgZWxzZSBzbi5pbm5lckhUTUw9IiI7CiAgICBpZihnKCJxbiIpKWcoInFu"
    "IikudGV4dENvbnRlbnQ9UVRZOwogIH0KICBpZihnKCJxbSIpKWcoInFtIikub25jbGljaz0oKT0+"
    "e1FUWT1NYXRoLm1heCgxLFFUWS0xKTtwYWludCgpO307CiAgaWYoZygicXAiKSlnKCJxcCIpLm9u"
    "Y2xpY2s9KCk9PntRVFk9TWF0aC5taW4obWF4LFFUWSsxKTtwYWludCgpO307CiAgaWYoZygiY3Bu"
    "IikmJk9GRkVSJiYhZygiY3BuIikudmFsdWUpZygiY3BuIikudmFsdWU9T0ZGRVI7CiAgaWYoZygi"
    "Y3BuYXBwbHkiKSlnKCJjcG5hcHBseSIpLm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IHY9KGcoImNw"
    "biIpLnZhbHVlfHwiIikudHJpbSgpLnRvVXBwZXJDYXNlKCk7CiAgICBpZighdil7dG9hc3QoIkVu"
    "dGVyIGEgY291cG9uIGNvZGUiKTtyZXR1cm59CiAgICBDT1VQT049dj09PSJXRUxDT01FMTAiPzEw"
    "OnY9PT0iU0FWRTIwIj8yMDowOwogICAgdG9hc3QoQ09VUE9OP0NPVVBPTisiJSBjb3Vwb24gYXBw"
    "bGllZCI6IkNvdXBvbiBub3QgdmFsaWQiKTtwYWludCgpO307CiAgaWYoZygiYmFkZGNhcnQiKSln"
    "KCJiYWRkY2FydCIpLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYoTElWRSl7CiAgICAgIGNvbnN0"
    "IGQ9YXdhaXQgYXBpVHJ5KCIvYXBpL2NhcnQiLHthY3Rpb246ImFkZCIsY291bnRyeTpzLmMscXR5"
    "OlFUWX0pOwogICAgICBpZighZClyZXR1cm47CiAgICAgIGNsb3NlU2hlZXQoKTt0b2FzdChRVFkr"
    "IiDDlyAiK3MuYysiIGFkZGVkIHRvIGNhcnQiKTthd2FpdCByZWZyZXNoKCk7cmV0dXJuOwogICAg"
    "fQogICAgZm9yKGxldCBpPTA7aTxRVFk7aSsrKUNBUlQucHVzaChzKTthcHBseUZlYXR1cmVzKCk7"
    "Y2xvc2VTaGVldCgpO3RvYXN0KFFUWSsiIMOXICIrcy5jKyIgYWRkZWQgdG8gY2FydCIpO307CiAg"
    "ZygiYmNvbmZpcm0iKS5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHQ9dG90YWwoKTsKICAg"
    "IGlmKEJBTDx0JiYhRkVBVC5zcGxpdFBheSl7dG9hc3QoIk5vdCBlbm91Z2ggYmFsYW5jZSDigJQg"
    "YWRkIGZ1bmRzIik7cmV0dXJufQogICAgaWYoQkFMPHQmJkZFQVQuc3BsaXRQYXkpe2Nsb3NlU2hl"
    "ZXQoKTt0b2FzdCgiUGF5aW5nICIrbW9uZXkodC1CQUwpKyIgYnkgVVBJIik7ZGVwb3NpdFNoZWV0"
    "KE1hdGguY2VpbCh0LUJBTCkpO3JldHVybn0KICAgIGlmKExJVkUpewogICAgICBjb25zdCBidG49"
    "ZygiYmNvbmZpcm0iKTtidG4uZGlzYWJsZWQ9dHJ1ZTtidG4udGV4dENvbnRlbnQ9Ildvcmtpbmfi"
    "gKYiOwogICAgICBjb25zdCBjb2RlPWcoImNwbiIpPyhnKCJjcG4iKS52YWx1ZXx8IiIpLnRyaW0o"
    "KToiIjsKICAgICAgY29uc3QgZD1hd2FpdCBhcGlUcnkoIi9hcGkvYnV5Iix7Y291bnRyeTpzLmMs"
    "cXR5OlFUWSxjb3Vwb246Y29kZX0pOwogICAgICBidG4uZGlzYWJsZWQ9ZmFsc2U7YnRuLnRleHRD"
    "b250ZW50PSJDb25maXJtIEJ1eSI7CiAgICAgIGlmKCFkKXJldHVybjsKICAgICAgaWYodHlwZW9m"
    "IGQuYmFsYW5jZT09PSJudW1iZXIiKUJBTD1kLmJhbGFuY2U7CiAgICAgIHN5bmNCYWwoKTtjbG9z"
    "ZVNoZWV0KCk7YnVyc3QoKTsKICAgICAgY29uc3QgZmlyc3Q9KGQub3JkZXJzJiZkLm9yZGVyc1sw"
    "XSl8fG51bGw7CiAgICAgIHRvYXN0KGZpcnN0PygiRGVsaXZlcmVkIOKAlCAiK2ZpcnN0LmlkKToi"
    "UHVyY2hhc2VkIOKAlCBkZXRhaWxzIGluIE9yZGVycyIpOwogICAgICBhd2FpdCByZWZyZXNoKCk7"
    "CiAgICAgIGlmKGZpcnN0JiZmaXJzdC5wYXlsb2FkKXNldFRpbWVvdXQoKCk9PnNoZWV0KGA8aDMg"
    "c3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj4ke2VzYyhmaXJzdC5pZCl9PC9o"
    "Mz4KICAgICAgICA8cCBjbGFzcz0ic3ViIj5LZWVwIHRoaXMgc2FmZS4gSXQgaXMgYWxzbyBpbiBP"
    "cmRlcnMuPC9wPgogICAgICAgIDxkaXYgY2xhc3M9Imt2IiBzdHlsZT0iZGlzcGxheTpibG9jayI+"
    "PGNvZGUgc3R5bGU9ImZvbnQtc2l6ZTouNzJyZW07d29yZC1icmVhazpicmVhay1hbGwiPiR7ZXNj"
    "KGZpcnN0LnBheWxvYWQpfTwvY29kZT48L2Rpdj4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "YmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiIGRhdGEtY2xvc2U+RG9uZTwvYnV0dG9uPmAp"
    "LDM1MCk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIEJBTC09dDtzeW5jQmFsKCk7Y2xvc2VTaGVl"
    "dCgpO2J1cnN0KCk7CiAgICB0b2FzdCgiUHVyY2hhc2VkIOKAlCBPVFAgaXMgaW4gT3JkZXJzIik7"
    "CiAgICBpZihGRUFULnB1c2hOb3RpZilzZXRUaW1lb3V0KCgpPT50b2FzdCgiQm90IG5vdGlmaWNh"
    "dGlvbiBzZW50IHRvIHlvdXIgY2hhdCIpLDE1MDApO307CiAgcGFpbnQoKTsKfQpmdW5jdGlvbiBj"
    "YXJ0U2hlZXQoKXsKICBpZighQ0FSVC5sZW5ndGgpe3RvYXN0KCJDYXJ0IGlzIGVtcHR5Iik7cmV0"
    "dXJufQogIGNvbnN0IHQ9Q0FSVC5yZWR1Y2UoKGEsYik9PmErYi5wcmljZSwwKTsKICBzaGVldChg"
    "PGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+WW91ciBjYXJ0PC9oMz4K"
    "ICAgIDxwIGNsYXNzPSJzdWIiPiR7Q0FSVC5sZW5ndGh9IGl0ZW0ke0NBUlQubGVuZ3RoPjE/InMi"
    "OiIifSDigJQgb25lIGNvbmZpcm1hdGlvbiwgb25lIGRlYml0LjwvcD4KICAgICR7Q0FSVC5tYXAo"
    "KHMsaSk9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHtzLmZ9ICR7ZXNjKHMuYyl9ICR7cy5jb2Rl"
    "fTwvc3Bhbj4KICAgICAgPGI+JHttb25leShzLnByaWNlKX0gPGJ1dHRvbiBjbGFzcz0iYnRuIHhz"
    "IGdob3N0IiBkYXRhLWNkZWw9IiR7aX0iPsOXPC9idXR0b24+PC9iPjwvZGl2PmApLmpvaW4oIiIp"
    "fQogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlRvdGFsPC9zcGFuPjxiPiR7bW9uZXkodCl9PC9i"
    "PjwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4Ij48"
    "YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj4KICAgICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIgaWQ9ImNhcnRidXkiPkNoZWNrb3V0PC9idXR0b24+PC9k"
    "aXY+YCk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2RlbF0iKS5mb3JFYWNo"
    "KGI9PmIub25jbGljaz0oKT0+e0NBUlQuc3BsaWNlKCtiLmRhdGFzZXQuY2RlbCwxKTthcHBseUZl"
    "YXR1cmVzKCk7Y2FydFNoZWV0KCk7fSk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNhcnRi"
    "dXkiKS5vbmNsaWNrPSgpPT57CiAgICBpZihCQUw8dCl7dG9hc3QoIk5vdCBlbm91Z2ggYmFsYW5j"
    "ZSDigJQgYWRkIGZ1bmRzIik7cmV0dXJufQogICAgQkFMLT10O0NBUlQ9W107c3luY0JhbCgpO2Fw"
    "cGx5RmVhdHVyZXMoKTtjbG9zZVNoZWV0KCk7YnVyc3QoKTt0b2FzdCgiT3JkZXIgcGxhY2VkIOKA"
    "lCBPVFBzIGFyZSBpbiBPcmRlcnMiKTt9Owp9CmZ1bmN0aW9uIGdvVGFiKHQpewogIGNvbnN0IGI9"
    "ZG9jdW1lbnQucXVlcnlTZWxlY3RvcihgI3RhYnMgYnV0dG9uW2RhdGEtdGFiPSIke3R9Il1gKTsK"
    "ICBpZihiKWIuY2xpY2soKTsKfQpmdW5jdGlvbiBiaW5kQ29tbW9uKCl7CiAgZG9jdW1lbnQucXVl"
    "cnlTZWxlY3RvckFsbCgiW2RhdGEtb3JkXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9"
    "PnsKICAgIGNvbnN0IHdoYXQ9Yi5kYXRhc2V0Lm9yZDsKICAgIGNvbnN0IGNhcmQ9Yi5jbG9zZXN0"
    "KCJbZGF0YS1vcmRlci1pZF0iKTsKICAgIGNvbnN0IG9pZD0oY2FyZCYmY2FyZC5kYXRhc2V0Lm9y"
    "ZGVySWQpfHwoT1JERVJTWzBdJiZPUkRFUlNbMF0uaWQpfHwiIjsKICAgIGlmKCFvaWQpe3RvYXN0"
    "KCJObyBvcmRlciBzZWxlY3RlZCIpO3JldHVybjt9CiAgICBpZighTElWRSl7dG9hc3QoIlByZXZp"
    "ZXcgb25seSIpO3JldHVybjt9CiAgICBjb25zdCBsYmw9Yi50ZXh0Q29udGVudDtiLmRpc2FibGVk"
    "PXRydWU7Yi50ZXh0Q29udGVudD0i4oCmIjsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBp"
    "L29yZGVyIix7aWQ6b2lkLHdoYXQ6d2hhdH0pOwogICAgYi5kaXNhYmxlZD1mYWxzZTtiLnRleHRD"
    "b250ZW50PWxibDsKICAgIGlmKCFyKXJldHVybjsKICAgIGlmKHdoYXQ9PT0iZGV0YWlscyIpdG9h"
    "c3QoIkxvZ2luIGRldGFpbHMgc2VudCB0byB5b3VyIGNoYXQiKTsKICAgIGlmKHdoYXQ9PT0iZmls"
    "ZSIpdG9hc3QoIlNlc3Npb24gZmlsZSBzZW50IHRvIHlvdXIgY2hhdCIpOwogICAgaWYod2hhdD09"
    "PSJyZWNlaXB0Iil0b2FzdCgiUmVjZWlwdCBzZW50IHRvIHlvdXIgY2hhdCIpOwogIH0pOwogIGRv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXJhdGUtZ29dIikuZm9yRWFjaChiPT5iLm9u"
    "Y2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3Qgc3RhcnM9K2IuZGF0YXNldC5yYXRlR287CiAgICBj"
    "b25zdCBjPSh3aW5kb3cuX19yYXRlQ291bnRyeSl8fCgoT1JERVJTWzBdfHx7fSkuYyl8fCIiOwog"
    "ICAgaWYoIUxJVkUpe2Nsb3NlU2hlZXQoKTt0b2FzdCgiVGhhbmtzIGZvciB0aGUgIitzdGFycysi"
    "4piFIik7cmV0dXJuO30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL3JldmlldyIse2Nv"
    "dW50cnk6YyxyYXRpbmc6c3RhcnN9KTsKICAgIGlmKCFyKXJldHVybjsKICAgIGNsb3NlU2hlZXQo"
    "KTt0b2FzdCgiVGhhbmtzIOKAlCAiK2MrIiBpcyBub3cgIityLmF2Zysi4piFIik7CiAgICBhd2Fp"
    "dCByZWZyZXNoKCk7CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtYWN0"
    "XSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IHdoYXQ9Yi5kYXRh"
    "c2V0LmFjdDsKICAgIGlmKHdoYXQ9PT0iY2hhbm5lbCIpewogICAgICBjb25zdCBjPShBRE0uY2hh"
    "bm5lbHMmJkFETS5jaGFubmVsc1swXSl8fG51bGw7CiAgICAgIGNvbnN0IGg9Yz9TdHJpbmcoYy5o"
    "YW5kbGV8fCIiKS5yZXBsYWNlKC9eQC8sIiIpOiIiOwogICAgICBpZihoKW9wZW5QYXkoaC5zdGFy"
    "dHNXaXRoKCJodHRwIik/aDooImh0dHBzOi8vdC5tZS8iK2gpKTsKICAgICAgZWxzZSB0b2FzdCgi"
    "Tm8gY2hhbm5lbCBoYXMgYmVlbiBhZGRlZCB5ZXQiKTsKICAgICAgcmV0dXJuOwogICAgfQogICAg"
    "aWYod2hhdD09PSJmbGFzaCIpewogICAgICBjb25zdCBvbj1BRE0ubWFya2V0JiZBRE0ubWFya2V0"
    "LmZsYXNoX29uOwogICAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNp"
    "emU6MXJlbSI+Rmxhc2ggc2FsZTwvaDM+CiAgICAgICAgPHAgY2xhc3M9InN1YiI+JHtvbj8oIkV2"
    "ZXJ5IHByaWNlIGlzICIrKEFETS5tYXJrZXQuZmxhc2h8fDApKyIlIG9mZiByaWdodCBub3cuIik6"
    "Ik5vIHNhbGUgaXMgcnVubmluZyBhdCB0aGUgbW9tZW50LiJ9PC9wPgogICAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCIgZGF0YS1jbG9zZT5DbG9z"
    "ZTwvYnV0dG9uPmApOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09InNvcnQiKXsK"
    "ICAgICAgRklMVEVSRUQ9KEZJTFRFUkVEJiZGSUxURVJFRC5sZW5ndGg/RklMVEVSRUQ6U1RPQ0sp"
    "LnNsaWNlKCkuc29ydCgoeCx5KT0+KHkucXR5fHwwKS0oeC5xdHl8fDApKTsKICAgICAgcmVuZGVy"
    "KCk7dG9hc3QoIlNvcnRlZCBieSB3aGF0IGlzIGluIHN0b2NrIik7CiAgICAgIHJldHVybjsKICAg"
    "IH0KICAgIGlmKHdoYXQ9PT0iYWxlcnRzIil7CiAgICAgIGNvbnN0IG1pbmU9KHdpbmRvdy5fX2Fs"
    "ZXJ0cyl8fFtdOwogICAgICBjb25zdCBvZmY9U1RPQ0suZmlsdGVyKHg9PiF4LnF0eSkubWFwKHg9"
    "PnguYyk7CiAgICAgIGNvbnN0IGxpc3Q9KG9mZi5sZW5ndGg/b2ZmOlNUT0NLLm1hcCh4PT54LmMp"
    "KS5zbGljZSgwLDgpOwogICAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250"
    "LXNpemU6MXJlbSI+UmVzdG9jayBhbGVydHM8L2gzPgogICAgICAgIDxwIGNsYXNzPSJzdWIiPldl"
    "IG1lc3NhZ2UgeW91IHRoZSBtb21lbnQgb25lIG9mIHRoZXNlIGlzIGJhY2suPC9wPgogICAgICAg"
    "ICR7bGlzdC5tYXAoYz0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj4ke2VzYyhjKX08L3NwYW4+CiAg"
    "ICAgICAgICA8Yj48YnV0dG9uIGNsYXNzPSJidG4geHMgJHttaW5lLmluZGV4T2YoYyk+PTA/IiI6"
    "InNvZnQifSIgZGF0YS1hbGVydD0iJHtlc2MoYyl9Ij4KICAgICAgICAgICAgJHttaW5lLmluZGV4"
    "T2YoYyk+PTA/Ik9uIjoiTm90aWZ5IG1lIn08L2J1dHRvbj48L2I+PC9kaXY+YCkuam9pbigiIil9"
    "CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4"
    "IiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRvbj5gKTsKICAgICAgZG9jdW1lbnQucXVlcnlTZWxlY3Rv"
    "ckFsbCgiW2RhdGEtYWxlcnRdIikuZm9yRWFjaChhPT5hLm9uY2xpY2s9YXN5bmMoKT0+ewogICAg"
    "ICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgICAgICBjb25z"
    "dCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hbGVydCIse2NvdW50cnk6YS5kYXRhc2V0LmFsZXJ0fSk7"
    "CiAgICAgICAgaWYoIXIpcmV0dXJuOwogICAgICAgIHdpbmRvdy5fX2FsZXJ0cz1yLmFsZXJ0c3x8"
    "W107CiAgICAgICAgYS50ZXh0Q29udGVudD1yLm9uPyJPbiI6Ik5vdGlmeSBtZSI7CiAgICAgICAg"
    "YS5jbGFzc0xpc3QudG9nZ2xlKCJzb2Z0Iiwhci5vbik7CiAgICAgICAgdG9hc3Qoci5vbj8oIldl"
    "IHdpbGwgdGVsbCB5b3Ugd2hlbiAiK2EuZGF0YXNldC5hbGVydCsiIGlzIGJhY2siKToiQWxlcnQg"
    "cmVtb3ZlZCIpOwogICAgICB9KTsKICAgICAgcmV0dXJuOwogICAgfQogICAgaWYod2hhdD09PSJj"
    "c3YiKXsKICAgICAgY29uc3Qgcm93cz1bWyJvcmRlciIsImNvdW50cnkiLCJwcmljZSIsInN0YXR1"
    "cyIsIndoZW4iXV0KICAgICAgICAuY29uY2F0KE9SREVSUy5tYXAobz0+W28uaWQsby5jLG8ucHJp"
    "Y2Usby5zdCxvLndoZW5dKSk7CiAgICAgIGNvbnN0IGNzdj1yb3dzLm1hcChyPT5yLmpvaW4oIiwi"
    "KSkuam9pbigiXG4iKTsKICAgICAgY29uc3QgYT1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCJhIik7"
    "CiAgICAgIGEuaHJlZj0iZGF0YTp0ZXh0L2NzdjtjaGFyc2V0PXV0Zi04LCIrZW5jb2RlVVJJQ29t"
    "cG9uZW50KGNzdik7CiAgICAgIGEuZG93bmxvYWQ9Im15LW9yZGVycy5jc3YiO2RvY3VtZW50LmJv"
    "ZHkuYXBwZW5kQ2hpbGQoYSk7YS5jbGljaygpO2EucmVtb3ZlKCk7CiAgICAgIHRvYXN0KE9SREVS"
    "Uy5sZW5ndGgrIiBvcmRlcnMgZG93bmxvYWRlZCIpOwogICAgICByZXR1cm47CiAgICB9CiAgICBp"
    "Zih3aGF0PT09InN1cHBvcnQiKXsKICAgICAgY29uc3QgaD0oU1RPUkUuc3VwcG9ydHx8IiIpLnJl"
    "cGxhY2UoL15ALywiIik7CiAgICAgIGlmKGgpb3BlblBheSgiaHR0cHM6Ly90Lm1lLyIraCk7CiAg"
    "ICAgIGVsc2UgdG9hc3QoIlNlbmQgeW91ciBxdWVzdGlvbiBpbiB0aGUgY2hhdCBhbmQgd2Ugd2ls"
    "bCByZXBseSIpOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09InRhc2siKXsKICAg"
    "ICAgY29uc3Qgcm93PWIuY2xvc2VzdCgiLmNhcmQiKTsKICAgICAgY29uc3QgbGFiZWw9cm93Pyhy"
    "b3cucXVlcnlTZWxlY3RvcigiYiIpfHx7fSkudGV4dENvbnRlbnR8fCIiOiIiOwogICAgICBpZigv"
    "Y2hhbm5lbC9pLnRlc3QobGFiZWwpKXtiLmJsdXIoKTtkb2N1bWVudC5xdWVyeVNlbGVjdG9yKCdb"
    "ZGF0YS1hY3Q9ImNoYW5uZWwiXScpPy5jbGljaygpO3JldHVybjt9CiAgICAgIGlmKC9vcmRlci9p"
    "LnRlc3QobGFiZWwpKXtnb1RhYigic2hvcCIpO3RvYXN0KCJQaWNrIGEgY291bnRyeSB0byBwbGFj"
    "ZSB5b3VyIGZpcnN0IG9yZGVyIik7cmV0dXJuO30KICAgICAgZ29UYWIoImVhcm4iKTt0b2FzdCgi"
    "U2hhcmUgeW91ciBpbnZpdGUgbGluayB0byBmaW5pc2ggdGhpcyIpOwogICAgICByZXR1cm47CiAg"
    "ICB9CiAgICBpZih3aGF0PT09InN0YXR1cyIpewogICAgICBjb25zdCBnPShBRE0ucGF5JiZBRE0u"
    "cGF5LmdtYWlsX3NldCk7CiAgICAgIGNvbnN0IHBlbmQ9KEFETS5wYXkmJkFETS5wYXkucGVuZGlu"
    "Zyl8fDA7CiAgICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZTox"
    "cmVtIj5QYXltZW50IGNoZWNraW5nPC9oMz4KICAgICAgICA8cCBjbGFzcz0ic3ViIj4ke2c/IkF1"
    "dG9tYXRpYyBjaGVja2luZyBpcyBvbi4gUGF5LCB0aGVuIHRhcCBWZXJpZnkuIjoKICAgICAgICAg"
    "ICJBdXRvbWF0aWMgY2hlY2tpbmcgaXMgbm90IHNldCB1cCwgc28gYW4gYWRtaW4gY29uZmlybXMg"
    "cGF5bWVudHMgYnkgaGFuZC4ifTwvcD4KICAgICAgICAke3BlbmQ/YDxkaXYgY2xhc3M9Imt2Ij48"
    "c3Bhbj5XYWl0aW5nIGZvciBhbiBhZG1pbjwvc3Bhbj48Yj4ke3BlbmR9PC9iPjwvZGl2PmA6IiJ9"
    "CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4"
    "IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+YCk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlm"
    "KHdoYXQ9PT0iY29weWNvZGUiKXsKICAgICAgY29uc3QgZWw9Yi5jbG9zZXN0KCIuY2FyZCwuc2hl"
    "ZXRib2R5LCNzaGVldCIpfHxkb2N1bWVudDsKICAgICAgY29uc3QgY29kZT0oZWwucXVlcnlTZWxl"
    "Y3RvcigiY29kZSIpfHx7fSkudGV4dENvbnRlbnR8fCIiOwogICAgICBjb3B5VGV4dChjb2RlLnRy"
    "aW0oKSwiQ29kZSIpOwogICAgICByZXR1cm47CiAgICB9CiAgICBpZih3aGF0PT09InJlcGxhY2Ui"
    "KXsKICAgICAgY29uc3QgY2FyZD1iLmNsb3Nlc3QoIltkYXRhLW9yZGVyLWlkXSIpOwogICAgICBj"
    "b25zdCBvaWQ9KGNhcmQmJmNhcmQuZGF0YXNldC5vcmRlcklkKXx8KE9SREVSU1swXSYmT1JERVJT"
    "WzBdLmlkKXx8IiI7CiAgICAgIGlmKCFMSVZFKXtjbG9zZVNoZWV0KCk7dG9hc3QoIlJlcGxhY2Vt"
    "ZW50IHNlbnQiKTtyZXR1cm47fQogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9vcmRl"
    "ciIse2lkOm9pZCx3aGF0OiJyZXBsYWNlIn0pOwogICAgICBpZighcilyZXR1cm47CiAgICAgIGNs"
    "b3NlU2hlZXQoKTsKICAgICAgdG9hc3Qoci5yZWZ1bmRlZD8iTm8gc3RvY2sgbGVmdCDigJQgcmVm"
    "dW5kZWQgdG8geW91ciB3YWxsZXQiOigiUmVwbGFjZWQg4oCUICIrci5vcmRlcikpOwogICAgICBh"
    "d2FpdCByZWZyZXNoKCk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGlmKHdoYXQ9PT0iY3J5cHRv"
    "Iil7CiAgICAgIGlmKCFMSVZFKXtjbG9zZVNoZWV0KCk7dG9hc3QoIldlIHdpbGwgY29uZmlybSB5"
    "b3VyIHRyYW5zZmVyIHNob3J0bHkiKTtyZXR1cm47fQogICAgICBjb25zdCByPWF3YWl0IGFwaVRy"
    "eSgiL2FwaS9jcnlwdG8iLHtyZWY6IiJ9KTsKICAgICAgaWYocil7Y2xvc2VTaGVldCgpO3RvYXN0"
    "KCJXZSB3aWxsIGNvbmZpcm0geW91ciB0cmFuc2ZlciBzaG9ydGx5Iik7fQogICAgICByZXR1cm47"
    "CiAgICB9CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY29weV0iKS5m"
    "b3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgY29uc3Qgd2hhdD1iLmRhdGFzZXQuY29weTsK"
    "ICAgIGlmKHdoYXQ9PT0idXBpIilyZXR1cm4gY29weVRleHQoU1RPUkUudXBpLCJVUEkgSUQiKTsK"
    "ICAgIGlmKHdoYXQ9PT0icmVmIil7CiAgICAgIGxldCBsPUVBUk4ubGlua3x8IiI7CiAgICAgIGlm"
    "KGwmJiEvXmh0dHBzPzovLnRlc3QobCkpbD0iaHR0cHM6Ly8iK2w7CiAgICAgIHJldHVybiBjb3B5"
    "VGV4dChsLCJJbnZpdGUgbGluayIpOwogICAgfQogICAgcmV0dXJuIGNvcHlUZXh0KGIuZGF0YXNl"
    "dC5jb3B5VmFsdWV8fGIudGV4dENvbnRlbnQudHJpbSgpLCJDb3BpZWQiKTsKICB9KTsKICBjb25z"
    "dCByZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicmVkZWVtZ28iKTsKICBpZihyZClyZC5vbmNs"
    "aWNrPWFzeW5jKCk9PnsKICAgIGlmKCFMSVZFKXt0b2FzdCgiUmVkZWVtZWQiKTtyZXR1cm47fQog"
    "ICAgcmQuZGlzYWJsZWQ9dHJ1ZTsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL3JlZGVl"
    "bSIse30pOwogICAgcmQuZGlzYWJsZWQ9ZmFsc2U7CiAgICBpZighcilyZXR1cm47CiAgICBCQUw9"
    "ci5iYWxhbmNlO3N5bmNCYWwoKTtidXJzdCgpOwogICAgdG9hc3Qoci5wb2ludHMrIiBwb2ludHMg"
    "4oaSICIrbW9uZXkoci5hbW91bnQpKTsKICAgIGF3YWl0IHJlZnJlc2goKTsKICB9OwogIGNvbnN0"
    "IG91PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJvZmZlcnVzZSIpOwogIGlmKG91KW91Lm9uY2xp"
    "Y2s9KCk9PnsKICAgIGNvbnN0IGM9KEFETS5jb3Vwb25zJiZBRE0uY291cG9ucy5sZW5ndGgpP0FE"
    "TS5jb3Vwb25zWzBdLmNvZGU6IiI7CiAgICBpZihjKXtPRkZFUj1jO2NvcHlUZXh0KGMsIkNvdXBv"
    "biIpO30KICAgIGVsc2UgdG9hc3QoIk5vIGNvdXBvbiBpcyBydW5uaW5nIHJpZ2h0IG5vdyIpOwog"
    "IH07CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtcHJlZl0iKS5mb3JFYWNoKHQ9"
    "PnQub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBrZXk9dC5kYXRhc2V0LnByZWYsIG9uPSF0"
    "LmNsYXNzTGlzdC5jb250YWlucygib24iKTsKICAgIHQuY2xhc3NMaXN0LnRvZ2dsZSgib24iLG9u"
    "KTtQUkVGU1trZXldPW9uOwogICAgaWYoTElWRSl7CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5"
    "KCIvYXBpL3ByZWZzIix7a2V5OmtleSxvbjpvbn0pOwogICAgICBpZighcil7dC5jbGFzc0xpc3Qu"
    "dG9nZ2xlKCJvbiIsIW9uKTtQUkVGU1trZXldPSFvbjtyZXR1cm47fQogICAgfQogICAgdG9hc3Qo"
    "b24/IlR1cm5lZCBvbiI6IlR1cm5lZCBvZmYiKTsKICB9KTsKICBjb25zdCB0Z2I9ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoInRlcm1zZ28iKTsKICBpZih0Z2IpdGdiLm9uY2xpY2s9KCk9PnNoZWV0"
    "KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5UZXJtcyAmIHJlZnVu"
    "ZHM8L2gzPgogICAgPHAgY2xhc3M9InN1YiIgc3R5bGU9IndoaXRlLXNwYWNlOnByZS13cmFwIj4k"
    "e2VzYyhTVE9SRS50ZXJtc3x8Q01TLnRlcm1zfHwiTm8gdGVybXMgaGF2ZSBiZWVuIHdyaXR0ZW4g"
    "eWV0LiIpfTwvcD4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10"
    "b3A6MTJweCIgZGF0YS1jbG9zZT5DbG9zZTwvYnV0dG9uPmApOwogIGNvbnN0IHNnPWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJzdXBwb3J0Z28iKTsKICBpZihzZylzZy5vbmNsaWNrPSgpPT57CiAg"
    "ICBjb25zdCBoPShTVE9SRS5zdXBwb3J0fHwiIikucmVwbGFjZSgvXkAvLCIiKTsKICAgIGlmKGgp"
    "b3BlblBheSgiaHR0cHM6Ly90Lm1lLyIraCk7CiAgICBlbHNlIHNoZWV0KGA8aDMgc3R5bGU9Im1h"
    "cmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5TdXBwb3J0PC9oMz4KICAgICAgPHAgY2xhc3M9"
    "InN1YiI+U2VuZCB5b3VyIHF1ZXN0aW9uIGluIHRoZSBjaGF0IGFuZCB3ZSB3aWxsIHJlcGx5Ljwv"
    "cD4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4"
    "IiBkYXRhLWNsb3NlPkNsb3NlPC9idXR0b24+YCk7CiAgfTsKfQpmdW5jdGlvbiBiaW5kU3RhZ2Uo"
    "KXsKICBiaW5kQ29tbW9uKCk7CiAgYmluZEJ1eSgpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JB"
    "bGwoIltkYXRhLXNjcm9sbF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoYi5kYXRhc2V0LnNjcm9sbCkuc2Nyb2xsSW50b1ZpZXcoe2JlaGF2aW9yOiJz"
    "bW9vdGgifSkpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLW90cF0iKS5mb3JF"
    "YWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBjYXJkPWIuY2xvc2VzdCgiW2Rh"
    "dGEtb3JkZXItaWRdIik7CiAgICBjb25zdCBvaWQ9KGNhcmQmJmNhcmQuZGF0YXNldC5vcmRlcklk"
    "KXx8Yi5kYXRhc2V0Lm90cHx8IiI7CiAgICBjb25zdCBvcmQ9T1JERVJTLmZpbmQoeD0+eC5pZD09"
    "PW9pZCl8fHt9OwogICAgaWYoIUxJVkUpewogICAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46"
    "MCAwIDRweDtmb250LXNpemU6MXJlbSI+TG9naW4gY29kZTwvaDM+CiAgICAgICAgPHAgY2xhc3M9"
    "InN1YiI+U2FtcGxlIGNvZGUg4oCUIHRoaXMgaXMgdGhlIHByZXZpZXcuPC9wPgogICAgICAgIDxk"
    "aXYgY2xhc3M9Im90cGJveCI+NCAyIDkgMSA3PC9kaXY+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4IiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRv"
    "bj5gKTsKICAgICAgcmV0dXJuOwogICAgfQogICAgY29uc3QgbGJsPWIudGV4dENvbnRlbnQ7Yi5k"
    "aXNhYmxlZD10cnVlO2IudGV4dENvbnRlbnQ9IkZldGNoaW5n4oCmIjsKICAgIGNvbnN0IHI9YXdh"
    "aXQgYXBpVHJ5KCIvYXBpL29yZGVyIix7aWQ6b2lkLHdoYXQ6Im90cCJ9KTsKICAgIGIuZGlzYWJs"
    "ZWQ9ZmFsc2U7Yi50ZXh0Q29udGVudD1sYmw7CiAgICBpZighcilyZXR1cm47CiAgICBzaGVldChg"
    "PGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+TG9naW4gY29kZTwvaDM+"
    "CiAgICAgIDxwIGNsYXNzPSJzdWIiPiR7b3JkLnBob25lPygiRm9yICIrZXNjKG9yZC5waG9uZSkp"
    "OiJGb3IgdGhpcyBhY2NvdW50In08L3A+CiAgICAgIDxkaXYgY2xhc3M9Im90cGJveCI+JHtTdHJp"
    "bmcoci5jb2RlKS5zcGxpdCgiIikuam9pbigiICIpfTwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4+Q29kZXMgbGVmdDwvc3Bhbj48Yj4ke3IubGVmdH08L2I+PC9kaXY+CiAgICAgICR7"
    "ci53aW5kb3c/YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5Gb3J3YXJkaW5nIGVuZHMgaW48L3NwYW4+"
    "PGI+JHtyLndpbmRvd30gbWluPC9iPjwvZGl2PmA6IiJ9CiAgICAgIDxkaXYgY2xhc3M9ImJ0bnJv"
    "dyIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNv"
    "ZnQiIGRhdGEtYWN0PSJjb3B5Y29kZSI+Q29weSBDb2RlPC9idXR0b24+CiAgICAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIiBkYXRhLWNsb3NlPkRvbmU8L2J1dHRvbj48L2Rpdj5gKTsKICB9KTsKICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1jbGFpbV0iKS5mb3JFYWNoKGI9PmIub25j"
    "bGljaz1hc3luYygpPT57CiAgICBpZihiLmRpc2FibGVkKXJldHVybjsKICAgIGIuZGlzYWJsZWQ9"
    "dHJ1ZTsKICAgIGlmKExJVkUpewogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9jaGVj"
    "a2luIix7fSk7CiAgICAgIGlmKCFyKXtiLmRpc2FibGVkPWZhbHNlO3JldHVybjt9CiAgICAgIEJB"
    "TD10eXBlb2Ygci5iYWxhbmNlPT09Im51bWJlciI/ci5iYWxhbmNlOkJBTDsKICAgICAgRUFSTi5j"
    "bGFpbWVkPXRydWU7RUFSTi5zdHJlYWs9ci5zdHJlYWt8fEVBUk4uc3RyZWFrOwogICAgICBzeW5j"
    "QmFsKCk7YnVyc3QoKTt0b2FzdChtb25leShyLmFtb3VudCkrIiBhZGRlZCDCtyBkYXkgIityLnN0"
    "cmVhayk7CiAgICAgIGIudGV4dENvbnRlbnQ9IkNsYWltZWQgdG9kYXkiOwogICAgICBhd2FpdCBy"
    "ZWZyZXNoKCk7CiAgICAgIHJldHVybjsKICAgIH0KICAgIEJBTCs9RUFSTi5kYWlseTtzeW5jQmFs"
    "KCk7YnVyc3QoKTt0b2FzdChtb25leShFQVJOLmRhaWx5KSsiIGFkZGVkIik7CiAgICBiLnRleHRD"
    "b250ZW50PSJDbGFpbWVkIHRvZGF5Ijt9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJb"
    "ZGF0YS1hbXRdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PmRlcG9zaXRTaGVldChiLmRhdGFz"
    "ZXQuYW10KSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZGVwXSIpLmZvckVh"
    "Y2goYj0+Yi5vbmNsaWNrPSgpPT5hbW91bnRTaGVldCgpKTsKICBkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCJbZGF0YS10Z10iKS5mb3JFYWNoKHQ9PnQub25jbGljaz0oKT0+dC5jbGFzc0xpc3Qu"
    "dG9nZ2xlKCJvbiIpKTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1zZXRzZWxd"
    "IikuZm9yRWFjaChzZWw9PnNlbC5vbmNoYW5nZT1hc3luYygpPT57CiAgICBpZighTElWRSl7dG9h"
    "c3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2Fw"
    "aS9hZG1pbi9zZXR0aW5nIix7a2V5OnNlbC5kYXRhc2V0LnNldHNlbCx2YWx1ZTpzZWwudmFsdWV9"
    "KTsKICAgIGlmKHIpdG9hc3QoIlNhdmVkIik7CiAgfSk7CiAgY29uc3QgcT1kb2N1bWVudC5nZXRF"
    "bGVtZW50QnlJZCgicSIpOwogIGlmKHEpe3EudmFsdWU9UVVFUlk7cS5vbmlucHV0PSgpPT57UVVF"
    "Ulk9cS52YWx1ZS50b0xvd2VyQ2FzZSgpO2FwcGx5RmlsdGVyKCl9O30KICBkb2N1bWVudC5xdWVy"
    "eVNlbGVjdG9yQWxsKCIjY2hpcHMgYnV0dG9uIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PntD"
    "SElQPWIuZGF0YXNldC5maWx0ZXI7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjY2hp"
    "cHMgYnV0dG9uIikuZm9yRWFjaCh4PT54LmNsYXNzTGlzdC50b2dnbGUoIm9uIix4PT09YikpO2Fw"
    "cGx5RmlsdGVyKCl9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1jbGFpbS1v"
    "cmRlcl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgY29uc3Qgb2lkPWIuZGF0YXNl"
    "dC5jbGFpbU9yZGVyOwogICAgY29uc3Qgb3JkPU9SREVSUy5maW5kKHg9PnguaWQ9PT1vaWQpfHx7"
    "fTsKICAgIGlmKG9yZC5jbGFpbSl7dG9hc3QoIkEgY2xhaW0gZm9yIHRoaXMgb3JkZXIgaXMgYWxy"
    "ZWFkeSB3aXRoIGFuIGFkbWluIik7cmV0dXJuO30KICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdp"
    "bjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5SZXBvcnQgYSBwcm9ibGVtPC9oMz4KICAgICAgPHAg"
    "Y2xhc3M9InN1YiI+QW4gYWRtaW4gcmVhZHMgZXZlcnkgcmVwb3J0IGFuZCBkZWNpZGVzLiBOb3Ro"
    "aW5nIGlzIHJlZnVuZGVkIGF1dG9tYXRpY2FsbHkuPC9wPgogICAgICA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPldoYXQgaGFwcGVuZWQ/PC9sYWJlbD4K"
    "ICAgICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIGlkPSJjbHIiPgogICAgICAgICAgPG9wdGlvbj5M"
    "b2dpbiBub3Qgd29ya2luZzwvb3B0aW9uPjxvcHRpb24+QWNjb3VudCBiYW5uZWQ8L29wdGlvbj4K"
    "ICAgICAgICAgIDxvcHRpb24+Q29kZSBuZXZlciBhcnJpdmVkPC9vcHRpb24+PG9wdGlvbj5Xcm9u"
    "ZyBjb3VudHJ5PC9vcHRpb24+CiAgICAgICAgICA8b3B0aW9uPlNvbWV0aGluZyBlbHNlPC9vcHRp"
    "b24+PC9zZWxlY3Q+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RGV0YWls"
    "czwvbGFiZWw+CiAgICAgICAgPHRleHRhcmVhIGNsYXNzPSJpbnAiIGlkPSJjbGQiIHJvd3M9IjMi"
    "IHBsYWNlaG9sZGVyPSJUZWxsIHVzIGV4YWN0bHkgd2hhdCB5b3Ugc2F3Ij48L3RleHRhcmVhPjwv"
    "ZGl2PgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBpZD0iY2xnbyI+U2VuZCB0"
    "byBhbiBhZG1pbjwvYnV0dG9uPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QgYmxvY2sg"
    "c20iIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgZGF0YS1jbG9zZT5DYW5jZWw8L2J1dHRvbj5gKTsK"
    "ICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjbGdvIikub25jbGljaz1hc3luYygpPT57CiAg"
    "ICAgIGNvbnN0IHJlYXNvbj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2xyIikudmFsdWU7CiAg"
    "ICAgIGNvbnN0IGRldGFpbHM9KGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjbGQiKS52YWx1ZXx8"
    "IiIpLnRyaW0oKTsKICAgICAgaWYoZGV0YWlscy5sZW5ndGg8NSl7dG9hc3QoIlRlbGwgdXMgd2hh"
    "dCB3ZW50IHdyb25nIik7cmV0dXJuO30KICAgICAgaWYoIUxJVkUpe2Nsb3NlU2hlZXQoKTt0b2Fz"
    "dCgiU2VudCIpO3JldHVybjt9CiAgICAgIGNvbnN0IGJ0bj1kb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgiY2xnbyIpO2J0bi5kaXNhYmxlZD10cnVlO2J0bi50ZXh0Q29udGVudD0iU2VuZGluZ+KApiI7"
    "CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL29yZGVyIix7aWQ6b2lkLHdoYXQ6ImNs"
    "YWltIixyZWFzb246cmVhc29uLGRldGFpbHM6ZGV0YWlsc30pOwogICAgICBidG4uZGlzYWJsZWQ9"
    "ZmFsc2U7YnRuLnRleHRDb250ZW50PSJTZW5kIHRvIGFuIGFkbWluIjsKICAgICAgaWYoIXIpcmV0"
    "dXJuOwogICAgICBjbG9zZVNoZWV0KCk7dG9hc3QoIkNsYWltICMiK3IuaWQrIiBzZW50IOKAlCBh"
    "biBhZG1pbiB3aWxsIHJlcGx5Iik7CiAgICAgIGF3YWl0IHJlZnJlc2goKTsKICAgIH07CiAgfSk7"
    "CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtbG9nZ2VkaW5dIikuZm9yRWFjaChi"
    "PT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3Qgb2lkPWIuZGF0YXNldC5sb2dnZWRpbjsK"
    "ICAgIGlmKCFMSVZFKXt0b2FzdCgiTWFya2VkIGFzIGxvZ2dlZCBpbiIpO3JldHVybjt9CiAgICBz"
    "aGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+Q29uZmlybSB5"
    "b3UgYXJlIGluPC9oMz4KICAgICAgPHAgY2xhc3M9InN1YiI+V2Ugd2lsbCBkZWxldGUgb3VyIGNv"
    "cHkgb2YgdGhlIGxvZ2luIHNlc3Npb24gc3RyYWlnaHQgYXdheSwgc28gdGhlCiAgICAgIGFjY291"
    "bnQgYmVjb21lcyB5b3VycyBhbG9uZS4gQ29kZXMgd2lsbCBzdG9wIGJlaW5nIGZvcndhcmRlZC48"
    "L3A+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEgYmxvY2siIGlkPSJsaW9rIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDoxMnB4Ij5ZZXMsIEkgYW0gbG9nZ2VkIGluPC9idXR0b24+CiAgICAgIDxidXR0"
    "b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4IiBkYXRh"
    "LWNsb3NlPk5vdCB5ZXQ8L2J1dHRvbj5gKTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJs"
    "aW9rIikub25jbGljaz1hc3luYygpPT57CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBp"
    "L29yZGVyIix7aWQ6b2lkLHdoYXQ6ImxvZ2dlZGluIn0pOwogICAgICBpZighcilyZXR1cm47CiAg"
    "ICAgIGNsb3NlU2hlZXQoKTt0b2FzdCgiRG9uZSDigJQgdGhlIGFjY291bnQgaXMgeW91cnMgYWxv"
    "bmUgbm93Iik7CiAgICAgIGF3YWl0IHJlZnJlc2goKTsKICAgIH07CiAgfSk7CiAgZG9jdW1lbnQu"
    "cXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZGlzcHV0ZV0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0o"
    "KT0+ewogICAgY29uc3Qgb3JkPWIuZGF0YXNldC5kaXNwdXRlfHwiIjsKICAgIHNoZWV0KGA8aDMg"
    "c3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj5PcGVuIGEgZGlzcHV0ZTwvaDM+"
    "CiAgICAgPHAgY2xhc3M9InN1YiI+JHtvcmQ/KCJPcmRlciAiK2VzYyhvcmQpKToiQW4gYWRtaW4g"
    "cmV2aWV3cyBldmVyeSBkaXNwdXRlLiJ9PC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+UmVhc29uPC9sYWJlbD4KICAgICAgIDxzZWxlY3Qg"
    "Y2xhc3M9ImlucCIgaWQ9ImRzciI+PG9wdGlvbj5Ob3QgZGVsaXZlcmVkPC9vcHRpb24+PG9wdGlv"
    "bj5BY2NvdW50IGRlYWQgb24gYXJyaXZhbDwvb3B0aW9uPjxvcHRpb24+Q2hhcmdlZCB0d2ljZTwv"
    "b3B0aW9uPjxvcHRpb24+T3RoZXI8L29wdGlvbj48L3NlbGVjdD48L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJmaWVsZCI+PGxhYmVsPldoYXQgaGFwcGVuZWQ/PC9sYWJlbD4KICAgICAgIDx0ZXh0YXJl"
    "YSBjbGFzcz0iaW5wIiBpZD0iZHNkIiByb3dzPSIzIiBwbGFjZWhvbGRlcj0iRGVzY3JpYmUgdGhl"
    "IHByb2JsZW0iPjwvdGV4dGFyZWE+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJl"
    "bD5FdmlkZW5jZSAob3B0aW9uYWwpPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBp"
    "ZD0iZHNmIiB0eXBlPSJmaWxlIiBhY2NlcHQ9ImltYWdlLyoiPgogICAgICAgPHNtYWxsIGNsYXNz"
    "PSJzdWIiIGlkPSJkc2ZuIj48L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IiBkYXRhLWNsb3NlPkNhbmNlbDwvYnV0dG9uPgogICAg"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIgaWQ9ImRzZ28iPlN1Ym1pdDwvYnV0dG9uPjwvZGl2"
    "PmApOwogICAgY29uc3QgZj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZHNmIik7CiAgICBpZihm"
    "KWYub25jaGFuZ2U9KCk9Pntjb25zdCBuPShmLmZpbGVzJiZmLmZpbGVzWzBdKT9mLmZpbGVzWzBd"
    "Lm5hbWU6IiI7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJkc2ZuIikudGV4dENvbnRl"
    "bnQ9bj8oIkF0dGFjaGVkOiAiK24pOiIiO307CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgi"
    "ZHNnbyIpLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25zdCByZWFzb249ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoImRzciIpLnZhbHVlOwogICAgICBjb25zdCBkZXRhaWxzPShkb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgiZHNkIikudmFsdWV8fCIiKS50cmltKCk7CiAgICAgIGlmKCFkZXRhaWxz"
    "KXt0b2FzdCgiRGVzY3JpYmUgd2hhdCBoYXBwZW5lZCIpO3JldHVybjt9CiAgICAgIGNvbnN0IGZp"
    "bGU9KGYmJmYuZmlsZXMmJmYuZmlsZXNbMF0pfHxudWxsOwogICAgICBjb25zdCBmbj1maWxlPygi"
    "c2NyZWVuc2hvdDogIitmaWxlLm5hbWUpOiIiOwogICAgICBpZighTElWRSl7Y2xvc2VTaGVldCgp"
    "O3RvYXN0KCJEaXNwdXRlIG9wZW5lZCIpO3JldHVybjt9CiAgICAgIGNvbnN0IGJ0bj1kb2N1bWVu"
    "dC5nZXRFbGVtZW50QnlJZCgiZHNnbyIpO2J0bi5kaXNhYmxlZD10cnVlO2J0bi50ZXh0Q29udGVu"
    "dD0iU2VuZGluZ+KApiI7CiAgICAgIGxldCBzaG90PSIiOwogICAgICBpZihmaWxlKXsKICAgICAg"
    "ICBpZihmaWxlLnNpemU+OCoxMDI0KjEwMjQpe3RvYXN0KCJUaGF0IGltYWdlIGlzIHRvbyBsYXJn"
    "ZSAoOCBNQiBtYXgpIik7CiAgICAgICAgICBidG4uZGlzYWJsZWQ9ZmFsc2U7YnRuLnRleHRDb250"
    "ZW50PSJTdWJtaXQiO3JldHVybjt9CiAgICAgICAgc2hvdD1hd2FpdCBuZXcgUHJvbWlzZShyZXM9"
    "PnsKICAgICAgICAgIGNvbnN0IHJkPW5ldyBGaWxlUmVhZGVyKCk7CiAgICAgICAgICByZC5vbmxv"
    "YWQ9KCk9PnJlcyhTdHJpbmcocmQucmVzdWx0fHwiIikpOwogICAgICAgICAgcmQub25lcnJvcj0o"
    "KT0+cmVzKCIiKTsKICAgICAgICAgIHJkLnJlYWRBc0RhdGFVUkwoZmlsZSk7CiAgICAgICAgfSk7"
    "CiAgICAgIH0KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvZGlzcHV0ZSIse29yZGVy"
    "Om9yZCxyZWFzb246cmVhc29uLGRldGFpbHM6ZGV0YWlscywKICAgICAgICBldmlkZW5jZTpmbixz"
    "aG90OnNob3R9KTsKICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0iU3Vi"
    "bWl0IjsKICAgICAgaWYoIXIpcmV0dXJuOwogICAgICBjbG9zZVNoZWV0KCk7dG9hc3QoIkRpc3B1"
    "dGUgIyIrci5pZCsiIHNlbnQgdG8gdGhlIGFkbWlucyIpOwogICAgICBhd2FpdCByZWZyZXNoKCk7"
    "CiAgICB9OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXJhdGVdIiku"
    "Zm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9Pnt3aW5kb3cuX19yYXRlQ291bnRyeT1iLmRhdGFzZXQu"
    "cmF0ZTtyZXR1cm4gc2hlZXQoCiAgICBgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNp"
    "emU6MXJlbSI+UmF0ZSAke2IuZGF0YXNldC5yYXRlfTwvaDM+CiAgICAgPHAgY2xhc3M9InN1YiI+"
    "WW91ciByYXRpbmcgc2hvd3Mgb24gdGhlIGNvdW50cnkgaW4gdGhlIHNob3AuPC9wPgogICAgIDxk"
    "aXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6OXB4O2p1c3RpZnktY29udGVudDpjZW50ZXI7bWFy"
    "Z2luOjE2cHggMCI+CiAgICAgICAke1sxLDIsMyw0LDVdLm1hcChuPT5gPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIHNtIHNvZnQiIGRhdGEtcmF0ZS1nbz0iJHtufSI+JHtufeKYhTwvYnV0dG9uPmApLmpvaW4o"
    "IiIpfTwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCBibG9jayBzbSIgZGF0YS1j"
    "bG9zZT5DbG9zZTwvYnV0dG9uPmApO30pOyAKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJb"
    "ZGF0YS1jcnlwdG9dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnNoZWV0KAogICAgYDxoMyBz"
    "dHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkNyeXB0byB0b3AtdXA8L2gzPgog"
    "ICAgIDxwIGNsYXNzPSJzdWIiPlNlbmQgVVNEVC1UUkMyMCDigJQgY3JlZGl0ZWQgYWZ0ZXIgMSBu"
    "ZXR3b3JrIGNvbmZpcm1hdGlvbi48L3A+CiAgICAgPGRpdiBjbGFzcz0icXIiPlFSIENPREU8YnI+"
    "KHdhbGxldCBhZGRyZXNzKTwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5OZXR3b3Jr"
    "PC9zcGFuPjxiPlRSQy0yMDwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+QWRk"
    "cmVzczwvc3Bhbj48YiBzdHlsZT0iZm9udC1zaXplOi43cmVtIj5ub3QtY29uZmlndXJlZDwvYj48"
    "L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+UmF0ZTwvc3Bhbj48Yj4xIFVTRFQgPSDi"
    "grk4OC40MDwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjEycHgiPjxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCIgZGF0YS1jbG9zZT5DbG9zZTwvYnV0"
    "dG9uPgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIgZGF0YS1hY3Q9ImNyeXB0byI+SSd2"
    "ZSBTZW50IEl0PC9idXR0b24+PC9kaXY+YCkpOwogIGNvbnN0IHRnPWRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJ0b3VyZ28iKSx0cz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgidG91cnNraXAiKTsK"
    "ICBpZih0Zyl0Zy5vbmNsaWNrPSgpPT5zaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtm"
    "b250LXNpemU6MXJlbSI+SG93IGl0IHdvcmtzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJzdGVwcyIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTJweCI+CiAgICAgICA8ZGl2IGNsYXNzPSJzdGVwIG9uIj48aT4x"
    "PC9pPlRvcCB1cCB5b3VyIHdhbGxldCBieSBVUEk8L2Rpdj4KICAgICAgIDxkaXYgY2xhc3M9InN0"
    "ZXAgb24iPjxpPjI8L2k+UGljayBhIGNvdW50cnkgYW5kIGJ1eSBpbnN0YW50bHk8L2Rpdj4KICAg"
    "ICAgIDxkaXYgY2xhc3M9InN0ZXAgb24iPjxpPjM8L2k+UmVhZCB0aGUgbG9naW4gY29kZSByaWdo"
    "dCBoZXJlIGluIE9yZGVyczwvZGl2PjwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEg"
    "YmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEtY2xvc2U+R290IGl0PC9idXR0b24+"
    "YCk7CiAgaWYodHMpdHMub25jbGljaz0oKT0+e0ZFQVQub25ib2FyZGluZz1mYWxzZTtyZW5kZXIo"
    "KTt0b2FzdCgiVG91ciBoaWRkZW4iKTt9OwogIHN0YXJ0Q291bnRkb3duKCk7CiAgYmluZEhvbGQo"
    "KTsKfQpmdW5jdGlvbiBhcHBseUZpbHRlcigpewogIEZJTFRFUkVEPVNUT0NLLmZpbHRlcihzPT57"
    "CiAgICBjb25zdCB0PShzLmMrIiAiK3MuY29kZSkudG9Mb3dlckNhc2UoKTsKICAgIGlmKFFVRVJZ"
    "JiYhdC5pbmNsdWRlcyhRVUVSWSkpcmV0dXJuIGZhbHNlOwogICAgaWYoQ0hJUD09PSJzdG9jayIm"
    "JnMucXR5PT09MClyZXR1cm4gZmFsc2U7CiAgICBpZihDSElQPT09ImRlYWwiJiYhcy53YXMpcmV0"
    "dXJuIGZhbHNlOwogICAgaWYoQ0hJUD09PSJjaGVhcCImJnMucHJpY2U+PTM1KXJldHVybiBmYWxz"
    "ZTsKICAgIGlmKENISVA9PT0idjJmYSImJnMucXR5PT09MClyZXR1cm4gZmFsc2U7CiAgICBpZihD"
    "SElQPT09InZtYWlsIiYmIXMuaG90KXJldHVybiBmYWxzZTsKICAgIGlmKENISVA9PT0idnNwYW0i"
    "JiZzLnByaWNlPDMwKXJldHVybiBmYWxzZTsKICAgIHJldHVybiB0cnVlO30pOwogIGNvbnN0IGI9"
    "ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNob3Bib2R5Iik7CiAgaWYoYil7Yi5pbm5lckhUTUw9"
    "c2hvcEJvZHkoKTtiaW5kQnV5KCk7fQp9CmxldCBjZFQ9bnVsbDsKZnVuY3Rpb24gc3RhcnRDb3Vu"
    "dGRvd24oKXsKICBjb25zdCBlbD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2QiKTtjbGVhcklu"
    "dGVydmFsKGNkVCk7aWYoIWVsKXJldHVybjsKICBsZXQgdD0xNCo2MCs1MjsKICBjZFQ9c2V0SW50"
    "ZXJ2YWwoKCk9PntpZighZG9jdW1lbnQuYm9keS5jb250YWlucyhlbCkpe2NsZWFySW50ZXJ2YWwo"
    "Y2RUKTtyZXR1cm59CiAgICB0PXQ+MD90LTE6MTQqNjArNTI7ZWwudGV4dENvbnRlbnQ9U3RyaW5n"
    "KE1hdGguZmxvb3IodC82MCkpLnBhZFN0YXJ0KDIsIjAiKSsiOiIrU3RyaW5nKHQlNjApLnBhZFN0"
    "YXJ0KDIsIjAiKX0sMTAwMCk7Cn0KLyogMDUgwrcgaG9sZC10by1jb25maXJtICovCmZ1bmN0aW9u"
    "IGJpbmRIb2xkKCl7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiLmhvbGQiKS5mb3JFYWNo"
    "KGI9PnsKICAgIGlmKGIuX2gpcmV0dXJuO2IuX2g9MTtjb25zdCBmaWxsPWIucXVlcnlTZWxlY3Rv"
    "cigiLmZpbGwiKTtsZXQgaXY9bnVsbCxwPTA7CiAgICBjb25zdCBzdG9wPSgpPT57Y2xlYXJJbnRl"
    "cnZhbChpdik7cD0wO2lmKGZpbGwpZmlsbC5zdHlsZS53aWR0aD0iMCUifTsKICAgIGNvbnN0IGdv"
    "PWU9PntlLnByZXZlbnREZWZhdWx0KCk7Y2xlYXJJbnRlcnZhbChpdik7aXY9c2V0SW50ZXJ2YWwo"
    "KCk9PntwKz00O2lmKGZpbGwpZmlsbC5zdHlsZS53aWR0aD1wKyIlIjsKICAgICAgaWYocD49MTAw"
    "KXtzdG9wKCk7Yi5jbGFzc0xpc3QuYWRkKCJkb25lIik7c2V0VGltZW91dCgoKT0+Yi5jbGFzc0xp"
    "c3QucmVtb3ZlKCJkb25lIiksNDUwKTtidXJzdCgpO3RvYXN0KGIuZGF0YXNldC5kb25lfHwiQ29u"
    "ZmlybWVkIil9fSwyOCl9OwogICAgYi5hZGRFdmVudExpc3RlbmVyKCJwb2ludGVyZG93biIsZ28p"
    "O1sicG9pbnRlcnVwIiwicG9pbnRlcmxlYXZlIiwicG9pbnRlcmNhbmNlbCJdLmZvckVhY2goZXY9"
    "PmIuYWRkRXZlbnRMaXN0ZW5lcihldixzdG9wKSk7fSk7Cn0KLyog4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQIEtJVCBSVU5USU1FIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKV"
    "kOKVkCAqLwpjb25zdCBSTT1tYXRjaE1lZGlhKCIocHJlZmVycy1yZWR1Y2VkLW1vdGlvbjpyZWR1"
    "Y2UpIikubWF0Y2hlczsKLyogc3ByaW5nIGludGVncmF0b3Ig4oCUIGtpdCB1c2VzIG1hc3MgLjEg"
    "LyBzdGlmZm5lc3MgMTUwIC8gZGFtcGluZyAxMiAqLwpmdW5jdGlvbiBTcHJpbmcodix7bT0uMSxr"
    "PTE1MCxkPTEyfT17fSl7dGhpcy52PXY7dGhpcy50PXY7dGhpcy52ZWw9MDt0aGlzLm09bTt0aGlz"
    "Lms9azt0aGlzLmQ9ZDt9ClNwcmluZy5wcm90b3R5cGUuc3RlcD1mdW5jdGlvbihkdCl7Y29uc3Qg"
    "Rj0tdGhpcy5rKih0aGlzLnYtdGhpcy50KS10aGlzLmQqdGhpcy52ZWw7CiAgdGhpcy52ZWwrPShG"
    "L3RoaXMubSkqZHQ7dGhpcy52Kz10aGlzLnZlbCpkdDtyZXR1cm4gdGhpcy52O307CgovKiAwMiAr"
    "IDI1IMK3IGFuaW1hdGVkIGRvY2s6IGRpc3RhbmNl4oaSc2NhbGUgd2l0aCBzcHJpbmcsIHBsdXMg"
    "c2xpZGluZyBwaWxsICovCihmdW5jdGlvbiBkb2NrKCl7CiAgY29uc3QgZGs9ZG9jdW1lbnQucXVl"
    "cnlTZWxlY3RvcigibmF2I3RhYnMgLmRvY2siKTtpZighZGspcmV0dXJuOwogIGNvbnN0IGJ0bnM9"
    "Wy4uLmRrLnF1ZXJ5U2VsZWN0b3JBbGwoImJ1dHRvbiIpXSxwaWxsPWRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJkcGlsbCIpOwogIGNvbnN0IEZJTkU9bWF0Y2hNZWRpYSgiKGhvdmVyOmhvdmVyKSBh"
    "bmQgKHBvaW50ZXI6ZmluZSkiKS5tYXRjaGVzJiYhUk07CiAgY29uc3Qgc3A9YnRucy5tYXAoKCk9"
    "Pm5ldyBTcHJpbmcoMSkpLHBzPXt4Om5ldyBTcHJpbmcoMCx7azoyNjAsZDoxMX0pLHc6bmV3IFNw"
    "cmluZygwLHtrOjI2MCxkOjExfSl9OwogIGxldCBteD1JbmZpbml0eSxsYXN0PXBlcmZvcm1hbmNl"
    "Lm5vdygpLHBsYWNlZD1mYWxzZTsKICBmdW5jdGlvbiByZXNldFNjYWxlKCl7bXg9SW5maW5pdHk7"
    "aWYoIUZJTkUpYnRucy5mb3JFYWNoKGI9PmIuc3R5bGUudHJhbnNmb3JtPSIiKTt9CiAgaWYoRklO"
    "RSl7CiAgICBkay5hZGRFdmVudExpc3RlbmVyKCJwb2ludGVybW92ZSIsZT0+e2lmKGUucG9pbnRl"
    "clR5cGU9PT0ibW91c2UiKW14PWUuY2xpZW50WH0pOwogICAgZGsuYWRkRXZlbnRMaXN0ZW5lcigi"
    "cG9pbnRlcmxlYXZlIiwoKT0+bXg9SW5maW5pdHkpOwogICAgWyJwb2ludGVydXAiLCJwb2ludGVy"
    "Y2FuY2VsIiwidG91Y2hlbmQiLCJ0b3VjaGNhbmNlbCIsImJsdXIiXS5mb3JFYWNoKGV2PT4KICAg"
    "ICAgYWRkRXZlbnRMaXN0ZW5lcihldixyZXNldFNjYWxlLHtwYXNzaXZlOnRydWV9KSk7CiAgfWVs"
    "c2V7YnRucy5mb3JFYWNoKGI9PmIuc3R5bGUudHJhbnNmb3JtPSIiKTt9CiAgZG9jdW1lbnQuYWRk"
    "RXZlbnRMaXN0ZW5lcigidmlzaWJpbGl0eWNoYW5nZSIscmVzZXRTY2FsZSk7CgogIGZ1bmN0aW9u"
    "IHBsYWNlKHNuYXApewogICAgY29uc3Qgb249ZGsucXVlcnlTZWxlY3RvcigiYnV0dG9uLm9uIik7"
    "CiAgICBpZighb258fCFwaWxsfHwhb24ub2Zmc2V0V2lkdGh8fCFkay5vZmZzZXRXaWR0aClyZXR1"
    "cm47ICAgLy8gbmV2ZXIgcGxhY2Ugd2hpbGUgaGlkZGVuCiAgICBwcy54LnQ9b24ub2Zmc2V0TGVm"
    "dC1kay5jbGllbnRMZWZ0O3BzLncudD1vbi5vZmZzZXRXaWR0aDsKICAgIHBpbGwuc3R5bGUudG9w"
    "PShvbi5vZmZzZXRUb3AtZGsuY2xpZW50VG9wKSsicHgiO3BpbGwuc3R5bGUuaGVpZ2h0PW9uLm9m"
    "ZnNldEhlaWdodCsicHgiOwogICAgaWYoc25hcHx8IXBsYWNlZCl7cGxhY2VkPXRydWU7cHMueC52"
    "PXBzLngudDtwcy54LnZlbD0wO3BzLncudj1wcy53LnQ7cHMudy52ZWw9MDsKICAgICAgcGlsbC5z"
    "dHlsZS50cmFuc2Zvcm09InRyYW5zbGF0ZVgoIitwcy54LnYrInB4KSI7cGlsbC5zdHlsZS53aWR0"
    "aD1wcy53LnYrInB4IjsKICAgICAgcGlsbC5jbGFzc0xpc3QuYWRkKCJyZWFkeSIpO30KICB9CiAg"
    "bmV3IFJlc2l6ZU9ic2VydmVyKCgpPT5wbGFjZSghcGxhY2VkKSkub2JzZXJ2ZShkayk7CiAgYWRk"
    "RXZlbnRMaXN0ZW5lcigicmVzaXplIiwoKT0+cGxhY2UodHJ1ZSkpOwogIGFkZEV2ZW50TGlzdGVu"
    "ZXIoImxvYWQiLCgpPT5wbGFjZSghcGxhY2VkKSk7CiAgc2V0VGltZW91dCgoKT0+cGxhY2UoIXBs"
    "YWNlZCksNjApO3NldFRpbWVvdXQoKCk9PnBsYWNlKCFwbGFjZWQpLDQwMCk7CgogIChmdW5jdGlv"
    "biBsb29wKHQpewogICAgY29uc3QgZHQ9TWF0aC5taW4oLjAzMiwodC1sYXN0KS8xMDAwKTtsYXN0"
    "PXQ7CiAgICBpZihGSU5FKXtidG5zLmZvckVhY2goKGIsaSk9PntsZXQgaz0wOwogICAgICBpZiht"
    "eCE9PUluZmluaXR5KXtjb25zdCByPWIuZ2V0Qm91bmRpbmdDbGllbnRSZWN0KCk7CiAgICAgICAg"
    "Y29uc3QgZGlzdD1NYXRoLmFicyhyLmxlZnQrci53aWR0aC8yLW14KTtrPU1hdGgubWF4KDAsMS1k"
    "aXN0LzE1MCk7fQogICAgICBzcFtpXS50PTErLjM0Kmsqaztjb25zdCB2PXNwW2ldLnN0ZXAoZHQp"
    "OwogICAgICBiLnN0eWxlLnRyYW5zZm9ybT0odj4xLjAwMnx8c3BbaV0udmVsPi4wMXx8c3BbaV0u"
    "dmVsPC0uMDEpCiAgICAgICAgPyAidHJhbnNsYXRlWSgiKygtOCoodi0xKSkudG9GaXhlZCgyKSsi"
    "cHgpIHNjYWxlKCIrdi50b0ZpeGVkKDMpKyIpIiA6ICIiO30pO30KICAgIGlmKHBpbGwmJnBsYWNl"
    "ZCl7cGlsbC5zdHlsZS50cmFuc2Zvcm09InRyYW5zbGF0ZVgoIitwcy54LnN0ZXAoZHQpLnRvRml4"
    "ZWQoMikrInB4KSI7CiAgICAgIHBpbGwuc3R5bGUud2lkdGg9cHMudy5zdGVwKGR0KS50b0ZpeGVk"
    "KDIpKyJweCI7fQogICAgcmVxdWVzdEFuaW1hdGlvbkZyYW1lKGxvb3ApO30pKGxhc3QpOwogIHdp"
    "bmRvdy5fX2RvY2tQbGFjZT1wbGFjZTtwbGFjZSh0cnVlKTsKfSkoKTsKCi8qIDE0IMK3IHYtdGFi"
    "cyBzbGlkaW5nIGluZGljYXRvciBmb3IgdGhlIGFkbWluIHN1Ym5hdiAqLwooZnVuY3Rpb24gdnRh"
    "YnMoKXsKICBjb25zdCBuYXY9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFzdWIiKSxwaWxsPWRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJ2cGlsbCIpO2lmKCFuYXZ8fCFwaWxsKXJldHVybjsKICBj"
    "b25zdCBweD1uZXcgU3ByaW5nKDAse2s6MjEwLGQ6MTl9KSxwdz1uZXcgU3ByaW5nKDAse2s6MjEw"
    "LGQ6MTl9KTtsZXQgbGFzdD1wZXJmb3JtYW5jZS5ub3coKSxpbml0PWZhbHNlOwogIGZ1bmN0aW9u"
    "IHBsYWNlKHNuYXApe2NvbnN0IG9uPW5hdi5xdWVyeVNlbGVjdG9yKCJidXR0b24ub24iKTsKICAg"
    "IGlmKCFvbnx8IW9uLm9mZnNldFdpZHRofHwhbmF2Lm9mZnNldFdpZHRoKXJldHVybjsKICAgIHB4"
    "LnQ9b24ub2Zmc2V0TGVmdC1uYXYuY2xpZW50TGVmdDtwdy50PW9uLm9mZnNldFdpZHRoOwogICAg"
    "cGlsbC5zdHlsZS50b3A9KG9uLm9mZnNldFRvcC1uYXYuY2xpZW50VG9wKSsicHgiO3BpbGwuc3R5"
    "bGUuaGVpZ2h0PW9uLm9mZnNldEhlaWdodCsicHgiOwogICAgaWYoc25hcHx8IWluaXQpe2luaXQ9"
    "dHJ1ZTtweC52PXB4LnQ7cHgudmVsPTA7cHcudj1wdy50O3B3LnZlbD0wOwogICAgICBwaWxsLnN0"
    "eWxlLnRyYW5zZm9ybT0idHJhbnNsYXRlWCgiK3B4LnYrInB4KSI7cGlsbC5zdHlsZS53aWR0aD1w"
    "dy52KyJweCI7fX0KICBuYXYuYWRkRXZlbnRMaXN0ZW5lcigiY2xpY2siLCgpPT5yZXF1ZXN0QW5p"
    "bWF0aW9uRnJhbWUoKCk9PnBsYWNlKGZhbHNlKSkpOwogIG5hdi5hZGRFdmVudExpc3RlbmVyKCJz"
    "Y3JvbGwiLCgpPT5wbGFjZShmYWxzZSkpOwogIG5ldyBSZXNpemVPYnNlcnZlcigoKT0+cGxhY2Uo"
    "IWluaXQpKS5vYnNlcnZlKG5hdik7CiAgYWRkRXZlbnRMaXN0ZW5lcigibG9hZCIsKCk9PnBsYWNl"
    "KCFpbml0KSk7c2V0VGltZW91dCgoKT0+cGxhY2UoIWluaXQpLDgwKTtwbGFjZSh0cnVlKTsKICAo"
    "ZnVuY3Rpb24gbG9vcCh0KXtjb25zdCBkdD1NYXRoLm1pbiguMDMyLCh0LWxhc3QpLzEwMDApO2xh"
    "c3Q9dDsKICAgIGlmKGluaXQpe3BpbGwuc3R5bGUudHJhbnNmb3JtPSJ0cmFuc2xhdGVYKCIrcHgu"
    "c3RlcChkdCkudG9GaXhlZCgyKSsicHgpIjtwaWxsLnN0eWxlLndpZHRoPXB3LnN0ZXAoZHQpLnRv"
    "Rml4ZWQoMikrInB4Ijt9CiAgICByZXF1ZXN0QW5pbWF0aW9uRnJhbWUobG9vcCk7fSkobGFzdCk7"
    "CiAgd2luZG93Ll9fdnRhYlBsYWNlPXBsYWNlOwp9KSgpOwoKLyogMDggwrcga2luZXRpYyBncmlk"
    "IOKAlCBwb2ludGVyIHdhcnAgKyBjbGljayByaXBwbGVzIChjYW52YXMgcG9ydCkgKi8KKGZ1bmN0"
    "aW9uIGtpbmV0aWMoKXsKICBjb25zdCBjdj1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgia2ciKTtp"
    "ZighY3Z8fFJNKXJldHVybjtjb25zdCBjdHg9Y3YuZ2V0Q29udGV4dCgiMmQiKTsKICBjb25zdCBT"
    "UD0zMCxJTkY9MjEwLFdBUlA9MjIsTEVSUD0uMDksUkdCPSIxMjUsMjExLDI1MiI7CiAgbGV0IFc9"
    "MCxIPTAsbW91c2U9e3g6LTk5OTkseTotOTk5OX0sdGd0PXt4Oi05OTk5LHk6LTk5OTl9LHJpcD1b"
    "XSxyYWY9MDsKICBmdW5jdGlvbiBzaXplKCl7Y29uc3QgZD1NYXRoLm1pbigxLjYsZGV2aWNlUGl4"
    "ZWxSYXRpb3x8MSk7Vz1pbm5lcldpZHRoO0g9aW5uZXJIZWlnaHQ7CiAgICBjdi53aWR0aD1XKmQ7"
    "Y3YuaGVpZ2h0PUgqZDtjdi5zdHlsZS53aWR0aD1XKyJweCI7Y3Yuc3R5bGUuaGVpZ2h0PUgrInB4"
    "IjtjdHguc2V0VHJhbnNmb3JtKGQsMCwwLGQsMCwwKTt9CiAgYWRkRXZlbnRMaXN0ZW5lcigicmVz"
    "aXplIixzaXplKTtzaXplKCk7CiAgYWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcm1vdmUiLGU9Pnt0"
    "Z3Q9e3g6ZS5jbGllbnRYLHk6ZS5jbGllbnRZfX0se3Bhc3NpdmU6dHJ1ZX0pOwogIGFkZEV2ZW50"
    "TGlzdGVuZXIoInBvaW50ZXJkb3duIixlPT57aWYocmlwLmxlbmd0aDw0KXJpcC5wdXNoKHt4OmUu"
    "Y2xpZW50WCx5OmUuY2xpZW50WSxyOjAsbzoxfSl9LHtwYXNzaXZlOnRydWV9KTsKICBhZGRFdmVu"
    "dExpc3RlbmVyKCJwb2ludGVybGVhdmUiLCgpPT50Z3Q9e3g6LTk5OTkseTotOTk5OX0pOwogIGZ1"
    "bmN0aW9uIHdhcnAoZ3gsZ3ksYyxyLGNvbHMscm93cyl7CiAgICBjb25zdCBjcD1NYXRoLm1pbihj"
    "LzEuNSwoY29scy0xLWMpLzEuNSwxKSxycD1NYXRoLm1pbihyLzEuNSwocm93cy0xLXIpLzEuNSwx"
    "KSxwaW49Y3AqY3AqcnAqcnA7CiAgICBjb25zdCBkeD1neC1tb3VzZS54LGR5PWd5LW1vdXNlLnks"
    "ZGlzdD1NYXRoLmh5cG90KGR4LGR5KTsKICAgIGNvbnN0IHByb3g9TWF0aC5tYXgoMCwxLWRpc3Qv"
    "SU5GKSpwaW47bGV0IHJ4PTAscnk9MDsKICAgIGZvcihjb25zdCBwIG9mIHJpcCl7Y29uc3QgZGR4"
    "PWd4LXAueCxkZHk9Z3ktcC55LGRkPU1hdGguaHlwb3QoZGR4LGRkeSksZGlmZj1kZC1wLnI7CiAg"
    "ICAgIGlmKE1hdGguYWJzKGRpZmYpPDU1KXtjb25zdCBzdD0oMS1NYXRoLmFicyhkaWZmKS81NSkq"
    "cC5vKjE2KnBpbixhbj1NYXRoLmF0YW4yKGRkeSxkZHgpLHNnPWRpZmY8MD8tMToxOwogICAgICAg"
    "IHJ4Kz1NYXRoLmNvcyhhbikqc3Qqc2cqLTE7cnkrPU1hdGguc2luKGFuKSpzdCpzZyotMTt9fQog"
    "ICAgaWYoZGlzdDxJTkYmJmRpc3Q+MCYmcGluPjApe2NvbnN0IHQ9ZGlzdC9JTkYsZT0oMS10KSoo"
    "MS10KSpNYXRoLm1pbigxLGRpc3QvNjApLHc9ZSpXQVJQKnBpbixhbj1NYXRoLmF0YW4yKGR5LGR4"
    "KTsKICAgICAgcmV0dXJuW2d4LU1hdGguY29zKGFuKSp3K3J4LGd5LU1hdGguc2luKGFuKSp3K3J5"
    "LHByb3hdO30KICAgIHJldHVybltneCtyeCxneStyeSxwcm94XTt9CiAgZnVuY3Rpb24gZnJhbWUo"
    "KXsKICAgIG1vdXNlLngrPSh0Z3QueC1tb3VzZS54KSpMRVJQO21vdXNlLnkrPSh0Z3QueS1tb3Vz"
    "ZS55KSpMRVJQOwogICAgcmlwPXJpcC5maWx0ZXIocD0+e3Aucis9NztwLm8tPS4wMTY7cmV0dXJu"
    "IHAubz4wfSk7CiAgICBjdHguY2xlYXJSZWN0KDAsMCxXLEgpOwogICAgY29uc3QgY29scz1NYXRo"
    "LmNlaWwoVy9TUCkrMSxyb3dzPU1hdGguY2VpbChIL1NQKSsxLHB0cz1bXTsKICAgIGZvcihsZXQg"
    "cj0wO3I8cm93cztyKyspe2NvbnN0IHJvdz1bXTtmb3IobGV0IGM9MDtjPGNvbHM7YysrKXJvdy5w"
    "dXNoKHdhcnAoYypTUCxyKlNQLGMscixjb2xzLHJvd3MpKTtwdHMucHVzaChyb3cpO30KICAgIGZv"
    "cihsZXQgcj0wO3I8cm93cztyKyspZm9yKGxldCBjPTA7Yzxjb2xzO2MrKyl7Y29uc3QgcD1wdHNb"
    "cl1bY107CiAgICAgIGlmKGM8Y29scy0xKXtjb25zdCBxPXB0c1tyXVtjKzFdO2N0eC5zdHJva2VT"
    "dHlsZT0icmdiYSgiK1JHQisiLCIrKC4wNStwWzJdKi4yMikudG9GaXhlZCgzKSsiKSI7CiAgICAg"
    "ICAgY3R4LmxpbmVXaWR0aD0xO2N0eC5iZWdpblBhdGgoKTtjdHgubW92ZVRvKHBbMF0scFsxXSk7"
    "Y3R4LmxpbmVUbyhxWzBdLHFbMV0pO2N0eC5zdHJva2UoKTt9CiAgICAgIGlmKHI8cm93cy0xKXtj"
    "b25zdCBxPXB0c1tyKzFdW2NdO2N0eC5zdHJva2VTdHlsZT0icmdiYSgiK1JHQisiLCIrKC4wNStw"
    "WzJdKi4yMikudG9GaXhlZCgzKSsiKSI7CiAgICAgICAgY3R4LmxpbmVXaWR0aD0xO2N0eC5iZWdp"
    "blBhdGgoKTtjdHgubW92ZVRvKHBbMF0scFsxXSk7Y3R4LmxpbmVUbyhxWzBdLHFbMV0pO2N0eC5z"
    "dHJva2UoKTt9CiAgICAgIGlmKHBbMl0+LjA2KXtjdHguZmlsbFN0eWxlPSJyZ2JhKCIrUkdCKyIs"
    "IisocFsyXSouNSkudG9GaXhlZCgzKSsiKSI7Y3R4LmJlZ2luUGF0aCgpOwogICAgICAgIGN0eC5h"
    "cmMocFswXSxwWzFdLDEuNitwWzJdKjEuNiwwLDYuMjgzKTtjdHguZmlsbCgpO319CiAgICByYWY9"
    "cmVxdWVzdEFuaW1hdGlvbkZyYW1lKGZyYW1lKTt9CiAgZnJhbWUoKTsKICBkb2N1bWVudC5hZGRF"
    "dmVudExpc3RlbmVyKCJ2aXNpYmlsaXR5Y2hhbmdlIiwoKT0+e2lmKGRvY3VtZW50LmhpZGRlbilj"
    "YW5jZWxBbmltYXRpb25GcmFtZShyYWYpO2Vsc2UgZnJhbWUoKTt9KTsKfSkoKTsKCi8qIDAxIMK3"
    "IGRvdCBsb2FkZXIgc3BsYXNoIChraXQgZnJhbWUgZGF0YSkgKi8KKGZ1bmN0aW9uIHNwbGFzaCgp"
    "ewogIGNvbnN0IGc9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImRnIiksc3A9ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoInNwbGFzaCIpO2lmKCFnKXJldHVybjsKICBmb3IobGV0IGk9MDtpPDQ5O2kr"
    "KylnLmFwcGVuZENoaWxkKGRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoImkiKSk7CiAgY29uc3QgY2Vs"
    "bHM9Wy4uLmcuY2hpbGRyZW5dOwogIGNvbnN0IEY9W1sxNCw3LDAsOCw2LDEzLDIwXSxbMTQsNywx"
    "MywyMCwxNiwyNywyMV0sWzE0LDIwLDI3LDIxLDM0LDI0LDI4XSxbMjcsMjEsMzQsMjgsNDEsMzIs"
    "MzVdLAogICBbMzQsMjgsNDEsMzUsNDgsNDAsNDJdLFszNCwyOCw0MSwzNSw0OCw0Miw0Nl0sWzM0"
    "LDI4LDQxLDM1LDQ4LDQyLDM4XSxbMzQsMjgsNDEsMzUsNDgsMzAsMjFdLAogICBbMzQsMjgsNDEs"
    "NDgsMjEsMjIsMTRdLFszNCwyOCw0MSwyMSwxNCwxNiwyN10sWzM0LDI4LDIxLDE0LDEwLDIwLDI3"
    "XSxbMjgsMjEsMTQsNCwxMywyMCwyN10sCiAgIFsyOCwyMSwxNCwxMiw2LDEzLDIwXSxbMjgsMjEs"
    "MTQsNiwxMywyMCwxMV0sWzI4LDIxLDE0LDYsMTMsMjAsMTBdLFsxNCw2LDEzLDIwLDksNywyMV1d"
    "OwogIGxldCBmPTA7Y29uc3QgaXY9c2V0SW50ZXJ2YWwoKCk9PntjZWxscy5mb3JFYWNoKGM9PmMu"
    "Y2xhc3NMaXN0LnJlbW92ZSgiYWN0IikpOwogICAgRltmJUYubGVuZ3RoXS5mb3JFYWNoKGk9PmNl"
    "bGxzW2ldJiZjZWxsc1tpXS5jbGFzc0xpc3QuYWRkKCJhY3QiKSk7ZisrO30sMTAwKTsKICBzZXRU"
    "aW1lb3V0KCgpPT57Y2xlYXJJbnRlcnZhbChpdik7c3AuY2xhc3NMaXN0LmFkZCgiZ29uZSIpOwog"
    "ICAgc2V0VGltZW91dCgoKT0+e3NwLnJlbW92ZSgpO2lmKHdpbmRvdy5fX2RvY2tQbGFjZSl3aW5k"
    "b3cuX19kb2NrUGxhY2UodHJ1ZSk7CiAgICAgIGlmKHdpbmRvdy5fX3Z0YWJQbGFjZSl3aW5kb3cu"
    "X192dGFiUGxhY2UodHJ1ZSk7fSw2MDApO30sUk0/MzAwOjE3NTApOwp9KSgpOwoKLyogc3RhZ2dl"
    "cmVkIHJldmVhbCBvbiBldmVyeSB2aWV3IGNoYW5nZSAqLwpmdW5jdGlvbiByZXZlYWxTdGFnZShl"
    "bCl7CiAgaWYoIWVsfHxSTSlyZXR1cm47CiAgWy4uLmVsLmNoaWxkcmVuXS5zbGljZSgwLDE0KS5m"
    "b3JFYWNoKChjLGkpPT57CiAgICBpZighYy5hbmltYXRlKXJldHVybjsKICAgIHRyeXsKICAgICAg"
    "Yy5nZXRBbmltYXRpb25zKCkuZm9yRWFjaChhPT57aWYoYS5pZD09PSJydiIpYS5jYW5jZWwoKTt9"
    "KTsKICAgICAgY29uc3QgYW49Yy5hbmltYXRlKFt7b3BhY2l0eTowLHRyYW5zZm9ybToidHJhbnNs"
    "YXRlWSgxNHB4KSBzY2FsZSguOTg1KSJ9LHtvcGFjaXR5OjEsdHJhbnNmb3JtOiJub25lIn1dLAog"
    "ICAgICAgIHtkdXJhdGlvbjo1MjAsZGVsYXk6aSo0MCxlYXNpbmc6ImN1YmljLWJlemllciguMjIs"
    "MSwuMzYsMSkiLGZpbGw6ImJhY2t3YXJkcyJ9KTsKICAgICAgYW4uaWQ9InJ2IjsKICAgIH1jYXRj"
    "aChlKXt9CiAgfSk7fQoKLyogbnVtYmVyIHRpY2tlciAqLwpmdW5jdGlvbiB0aWNrVG8oZWwsdG8s"
    "ZHVyKXtpZighZWwpcmV0dXJuO2NvbnN0IGZyb209cGFyc2VGbG9hdChlbC50ZXh0Q29udGVudC5y"
    "ZXBsYWNlKC9bXlxkLl0vZywiIikpfHwwOwogIGlmKFJNKXtlbC50ZXh0Q29udGVudD10by50b0Zp"
    "eGVkKDIpO3JldHVybn0KICBjb25zdCB0MD1wZXJmb3JtYW5jZS5ub3coKTsoZnVuY3Rpb24gc3Qo"
    "dCl7Y29uc3QgcD1NYXRoLm1pbigxLCh0LXQwKS8oZHVyfHw2MDApKTsKICAgIGNvbnN0IGU9MS1N"
    "YXRoLnBvdygxLXAsMyk7ZWwudGV4dENvbnRlbnQ9KGZyb20rKHRvLWZyb20pKmUpLnRvRml4ZWQo"
    "Mik7CiAgICBpZihwPDEpcmVxdWVzdEFuaW1hdGlvbkZyYW1lKHN0KTt9KSh0MCk7fQoKLyogcHJl"
    "c3MgcmlwcGxlIG9uIGV2ZXJ5IGJ1dHRvbiAqLwpkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCJw"
    "b2ludGVyZG93biIsZT0+e2NvbnN0IGI9ZS50YXJnZXQuY2xvc2VzdCgiLmJ0biwuY2hpcHJvdyBi"
    "dXR0b24sLnN1Ym5hdiBidXR0b24iKTsKICBpZighYnx8Uk0pcmV0dXJuO2NvbnN0IHI9Yi5nZXRC"
    "b3VuZGluZ0NsaWVudFJlY3QoKSxkPU1hdGgubWF4KHIud2lkdGgsci5oZWlnaHQpKjEuNDsKICBj"
    "b25zdCBzPWRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoInNwYW4iKTtzLmNsYXNzTmFtZT0icmlwIjsK"
    "ICBzLnN0eWxlLmNzc1RleHQ9ImxlZnQ6IisoZS5jbGllbnRYLXIubGVmdCkrInB4O3RvcDoiKyhl"
    "LmNsaWVudFktci50b3ApKyJweDt3aWR0aDoiK2QrInB4O2hlaWdodDoiK2QrInB4IjsKICBpZihn"
    "ZXRDb21wdXRlZFN0eWxlKGIpLnBvc2l0aW9uPT09InN0YXRpYyIpYi5zdHlsZS5wb3NpdGlvbj0i"
    "cmVsYXRpdmUiOwogIGIuYXBwZW5kQ2hpbGQocyk7c2V0VGltZW91dCgoKT0+cy5yZW1vdmUoKSw1"
    "NjApO30pOwoKLyogMTUgwrcgdGlsdCArIGdsYXJlICovCmZ1bmN0aW9uIGJpbmRUaWx0KCl7ZG9j"
    "dW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtdGlsdF0iKS5mb3JFYWNoKGM9PnsKICBpZihj"
    "Ll90fHxSTSlyZXR1cm47Yy5fdD0xOwogIGMuYWRkRXZlbnRMaXN0ZW5lcigicG9pbnRlcm1vdmUi"
    "LGU9Pntjb25zdCByPWMuZ2V0Qm91bmRpbmdDbGllbnRSZWN0KCk7CiAgICBjb25zdCB4PShlLmNs"
    "aWVudFgtci5sZWZ0KS9yLndpZHRoLHk9KGUuY2xpZW50WS1yLnRvcCkvci5oZWlnaHQ7CiAgICBj"
    "LnN0eWxlLnRyYW5zZm9ybT0icGVyc3BlY3RpdmUoNzAwcHgpIHJvdGF0ZVgoIisoKC41LXkpKjkp"
    "LnRvRml4ZWQoMikrImRlZykgcm90YXRlWSgiKygoeC0uNSkqMTEpLnRvRml4ZWQoMikrImRlZykg"
    "dHJhbnNsYXRlWig0cHgpIjsKICAgIGMuc3R5bGUuc2V0UHJvcGVydHkoIi0tbXgiLCh4KjEwMCkr"
    "IiUiKTtjLnN0eWxlLnNldFByb3BlcnR5KCItLW15IiwoeSoxMDApKyIlIik7fSk7CiAgYy5hZGRF"
    "dmVudExpc3RlbmVyKCJwb2ludGVybGVhdmUiLCgpPT5jLnN0eWxlLnRyYW5zZm9ybT0iIik7fSk7"
    "fQoKLyogc3VjY2VzcyBidXJzdCAqLwpmdW5jdGlvbiBidXJzdCgpe2NvbnN0IGZ4PWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJmeCIpO2lmKCFmeClyZXR1cm47ZnguY2xhc3NMaXN0LmFkZCgib24i"
    "KTsKICBjb25zdCB3PWZ4LnF1ZXJ5U2VsZWN0b3IoIi53YXZlIik7dy5zdHlsZS5hbmltYXRpb249"
    "Im5vbmUiO3ZvaWQgdy5vZmZzZXRXaWR0aDt3LnN0eWxlLmFuaW1hdGlvbj0iIjsKICBzZXRUaW1l"
    "b3V0KCgpPT5meC5jbGFzc0xpc3QucmVtb3ZlKCJvbiIpLDkwMCk7fQoKLyog4pSA4pSAIFBheSAr"
    "IHZlcmlmeSBkZXBvc2l0IGZsb3cgKDUtbWludXRlIHdpbmRvdywgdmVyaWZ5IG9ubHkgb24gdGFw"
    "KSDilIDilIAgKi8KbGV0IGRlcFRpY2s9bnVsbCxkZXBKb2I9bnVsbDsKZnVuY3Rpb24gYW1vdW50"
    "U2hlZXQoKXsKICBjb25zdCBwaWNrcz1bNTAsMTAwLDIwMCw1MDAsMTAwMCwyMDAwXTsKICBzaGVl"
    "dChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+JHtDTVMud2FsS2lj"
    "a3x8IkFkZCBmdW5kcyJ9PC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPlBpY2sgYW4gYW1vdW50IG9y"
    "IHR5cGUgeW91ciBvd24uPC9wPgogICAgPGRpdiBjbGFzcz0iY2hpcHMiIHN0eWxlPSJtYXJnaW46"
    "MTJweCAwIDRweDtkaXNwbGF5OmZsZXg7ZmxleC13cmFwOndyYXA7Z2FwOjdweCI+CiAgICAgICR7"
    "cGlja3MubWFwKHY9PmA8YnV0dG9uIGNsYXNzPSJidG4gc20iIGRhdGEtcGljaz0iJHt2fSIgc3R5"
    "bGU9ImZsZXg6MCAwIGF1dG8iPiR7bW9uZXkodil9PC9idXR0b24+YCkuam9pbigiIil9CiAgICA8"
    "L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFi"
    "ZWw+Q3VzdG9tIGFtb3VudDwvbGFiZWw+CiAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0iYW10"
    "aW4iIHR5cGU9Im51bWJlciIgaW5wdXRtb2RlPSJudW1lcmljIiBtaW49IjEiIHBsYWNlaG9sZGVy"
    "PSJFbnRlciBhbW91bnQiPjwvZGl2PgogICAgPHAgY2xhc3M9InN1YiIgaWQ9ImFtdGhpbnQiIHN0"
    "eWxlPSJtYXJnaW46MCAwIDEwcHg7Zm9udC1zaXplOi43MnJlbSI+PC9wPgogICAgPGJ1dHRvbiBj"
    "bGFzcz0iYnRuIGN0YSBibG9jayIgaWQ9ImFtdGdvIj5Db250aW51ZTwvYnV0dG9uPgogICAgPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGRh"
    "dGEtY2xvc2U+Q2FuY2VsPC9idXR0b24+YCk7CiAgY29uc3QgaW5wPWRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJhbXRpbiIpLCBnbz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYW10Z28iKSwKICAg"
    "ICAgICBoaW50PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJhbXRoaW50Iik7CiAgY29uc3QgbG89"
    "KEFETSYmQURNLnBheSYmQURNLnBheS5taW4pfHwxMCwgaGk9KEFETSYmQURNLnBheSYmQURNLnBh"
    "eS5tYXgpfHwyMDAwMDsKICBoaW50LnRleHRDb250ZW50PSJCZXR3ZWVuICIrbW9uZXkobG8pKyIg"
    "YW5kICIrbW9uZXkoaGkpKyIuIjsKICBjb25zdCBzdGFydD12PT57CiAgICBjb25zdCBuPU51bWJl"
    "cih2KTsKICAgIGlmKCFufHxuPD0wKXt0b2FzdCgiRW50ZXIgYW4gYW1vdW50IGZpcnN0Iik7aW5w"
    "JiZpbnAuZm9jdXMoKTtyZXR1cm47fQogICAgaWYobjxsb3x8bj5oaSl7dG9hc3QoIkFsbG93ZWQg"
    "Iittb25leShsbykrIiB0byAiK21vbmV5KGhpKSk7cmV0dXJuO30KICAgIGRlcG9zaXRTaGVldChu"
    "KTsKICB9OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXBpY2tdIikuZm9yRWFj"
    "aChiPT5iLm9uY2xpY2s9KCk9PnN0YXJ0KGIuZGF0YXNldC5waWNrKSk7CiAgZ28ub25jbGljaz0o"
    "KT0+c3RhcnQoaW5wLnZhbHVlKTsKICBpZihpbnApaW5wLm9ua2V5ZG93bj1lPT57aWYoZS5rZXk9"
    "PT0iRW50ZXIiKXN0YXJ0KGlucC52YWx1ZSk7fTsKICBzZXRUaW1lb3V0KCgpPT57aWYoaW5wKWlu"
    "cC5mb2N1cygpO30sMjUwKTsKfQphc3luYyBmdW5jdGlvbiBsaXZlRGVwb3NpdFNoZWV0KGEpewog"
    "IGNsZWFySW50ZXJ2YWwoZGVwVGljayk7Y2xlYXJUaW1lb3V0KGRlcEpvYik7CiAgY29uc3QgZD1h"
    "d2FpdCBhcGlUcnkoIi9hcGkvZGVwb3NpdC9jcmVhdGUiLHthbW91bnQ6TnVtYmVyKGEpfSk7CiAg"
    "aWYoIWQpcmV0dXJuOwogIGNvbnN0IG1pbnM9ZC53aW5kb3d8fDU7CiAgbGV0IGxlZnQ9TWF0aC5t"
    "YXgoMCxNYXRoLnJvdW5kKChuZXcgRGF0ZShkLmV4cGlyZXNfYXQpLW5ldyBEYXRlKCkpLzEwMDAp"
    "KXx8bWlucyo2MDsKICBjb25zdCB0b3RhbD1taW5zKjYwOwogIGxldCBkb25lPWZhbHNlLGJ1c3k9"
    "ZmFsc2U7CiAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0i"
    "PiR7Q01TLnNoZWV0VGl0bGV9ICR7bW9uZXkoZC5hbW91bnQpfTwvaDM+CiAgICA8cCBjbGFzcz0i"
    "c3ViIj4ke0NNUy5zaGVldFN1Yi5yZXBsYWNlKCJ7bX0iLG1pbnMpfTwvcD4KICAgICR7ZC5xcj9g"
    "PGRpdiBjbGFzcz0icXIiIHN0eWxlPSJwYWRkaW5nOjA7b3ZlcmZsb3c6aGlkZGVuIj48aW1nIHNy"
    "Yz0iJHtkLnFyfSIgYWx0PSJVUEkgUVIiIHN0eWxlPSJ3aWR0aDoxMDAlO2Rpc3BsYXk6YmxvY2si"
    "PjwvZGl2PmA6IiJ9CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VVBJIElEPC9zcGFuPjxiPiR7"
    "ZXNjKGQudXBpfHwiIil9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlBheWVl"
    "PC9zcGFuPjxiPiR7ZXNjKGQucGF5ZWV8fCIiKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4+QW1vdW50PC9zcGFuPjxiPiR7bW9uZXkoZC5hbW91bnQpfTwvYj48L2Rpdj4KICAg"
    "ICR7ZC5ib251cz9gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkJvbnVzPC9zcGFuPjxiPiske21vbmV5"
    "KGQuYm9udXMpfTwvYj48L2Rpdj5gOiIifQogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPk5vdGUg"
    "LyByZWZlcmVuY2U8L3NwYW4+PGI+JHtlc2MoZC5jb2RlKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNs"
    "YXNzPSJrdiI+PHNwYW4+RXhwaXJlcyBpbjwvc3Bhbj48YiBpZD0iZHBjZCI+JHtNYXRoLmZsb29y"
    "KGxlZnQvNjApfToke1N0cmluZyhsZWZ0JTYwKS5wYWRTdGFydCgyLCIwIil9PC9iPjwvZGl2Pgog"
    "ICAgPGRpdiBjbGFzcz0idGltZXJiYXIiIGlkPSJkcGJhciI+PGkgc3R5bGU9IndpZHRoOjEwMCUi"
    "PjwvaT48L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN0ZXBzIiBpZD0iZHN0ZXBzIj4KICAgICAgPGRp"
    "diBjbGFzcz0ic3RlcCBvbiI+PGk+MTwvaT4ke0NNUy5zdGVwMX08L2Rpdj4KICAgICAgPGRpdiBj"
    "bGFzcz0ic3RlcCIgaWQ9InMyIj48aT4yPC9pPiR7Q01TLnN0ZXAyfTwvZGl2PgogICAgICA8ZGl2"
    "IGNsYXNzPSJzdGVwIiBpZD0iczMiPjxpPjM8L2k+JHtDTVMuc3RlcDN9PC9kaXY+CiAgICAgIDxk"
    "aXYgY2xhc3M9InN0ZXAiIGlkPSJzNCI+PGk+NDwvaT4ke0NNUy5zdGVwNH08L2Rpdj4KICAgIDwv"
    "ZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPgogICAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4iIGlkPSJkcGF5Ij7wn4+mIENvcHkgVVBJIElEPC9idXR0b24+"
    "CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBjdGEiIGlkPSJkdmVyIj4ke1NUT1JFLmF1dG9jaGVj"
    "az09PWZhbHNlPyLinIUgSSBoYXZlIHBhaWQiOiLinIUgIitDTVMudmVyaWZ5QnRufTwvYnV0dG9u"
    "PjwvZGl2PgogICAgJHtTVE9SRS5hdXRvY2hlY2s9PT1mYWxzZT8iIjpgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIHNvZnQgYmxvY2sgc20iIGlkPSJkdXRyIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPvCfp74g"
    "U2VuZCB0cmFuc2FjdGlvbiBuby48L2J1dHRvbj5gfQogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdo"
    "b3N0IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1"
    "dHRvbj5gKTsKICBjb25zdCBnPWlkPT5kb2N1bWVudC5nZXRFbGVtZW50QnlJZChpZCk7CiAgY29u"
    "c3QgcGF5Qj1nKCJkcGF5IiksdmVyQj1nKCJkdmVyIiksdXRyQj1nKCJkdXRyIik7CiAgY29uc3Qg"
    "YWxpdmU9KCk9PmcoImRwY2QiKSYmZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNoZWV0IikuY2xh"
    "c3NMaXN0LmNvbnRhaW5zKCJvcGVuIik7CiAgZnVuY3Rpb24gZXhwaXJlKCl7CiAgICBjbGVhcklu"
    "dGVydmFsKGRlcFRpY2spOwogICAgaWYoIWFsaXZlKCkpcmV0dXJuOwogICAgZygiZHBjZCIpLnRl"
    "eHRDb250ZW50PSIwOjAwIjtnKCJkcGJhciIpLmNsYXNzTGlzdC5hZGQoImxvdyIpO2coImRwYmFy"
    "IikuZmlyc3RDaGlsZC5zdHlsZS53aWR0aD0iMCUiOwogICAgdmVyQi5kaXNhYmxlZD10cnVlO3V0"
    "ckIuZGlzYWJsZWQ9dHJ1ZTt2ZXJCLnRleHRDb250ZW50PSJFeHBpcmVkIjsKICAgIHBheUIuY2xh"
    "c3NOYW1lPSJidG4iO3BheUIudGV4dENvbnRlbnQ9Ik5ldyBSZXF1ZXN0IjtwYXlCLm9uY2xpY2s9"
    "KCk9PmRlcG9zaXRTaGVldChhKTsKICAgIHRvYXN0KENNUy5tc2dFeHBpcmVkKTsKICB9CiAgZGVw"
    "VGljaz1zZXRJbnRlcnZhbCgoKT0+ewogICAgaWYoIWFsaXZlKCkpe2NsZWFySW50ZXJ2YWwoZGVw"
    "VGljayk7cmV0dXJuO30KICAgIGxlZnQtLTsKICAgIGcoImRwY2QiKS50ZXh0Q29udGVudD1NYXRo"
    "Lm1heCgwLE1hdGguZmxvb3IobGVmdC82MCkpKyI6IitTdHJpbmcoTWF0aC5tYXgoMCxsZWZ0JTYw"
    "KSkucGFkU3RhcnQoMiwiMCIpOwogICAgZygiZHBiYXIiKS5maXJzdENoaWxkLnN0eWxlLndpZHRo"
    "PShNYXRoLm1heCgwLGxlZnQpL3RvdGFsKjEwMCkrIiUiOwogICAgZygiZHBiYXIiKS5jbGFzc0xp"
    "c3QudG9nZ2xlKCJsb3ciLGxlZnQ8PTYwKTsKICAgIGlmKGxlZnQ8PTApZXhwaXJlKCk7CiAgfSwx"
    "MDAwKTsKICBpZihwYXlCKXBheUIub25jbGljaz0oKT0+ewogICAgaWYoZG9uZSlyZXR1cm47CiAg"
    "ICBnKCJzMiIpLmNsYXNzTmFtZT0ic3RlcCBvbiI7ZygiczMiKS5jbGFzc05hbWU9InN0ZXAgcnVu"
    "IjsKICAgIHZlckIuY2xhc3NMaXN0LnJlbW92ZSgiZ2xhc3MiKTt2ZXJCLmNsYXNzTGlzdC5hZGQo"
    "ImN0YSIpOwogICAgdHJ5e25hdmlnYXRvci5jbGlwYm9hcmQmJm5hdmlnYXRvci5jbGlwYm9hcmQu"
    "d3JpdGVUZXh0KGQudXBpfHwiIik7fWNhdGNoKGUpe30KICAgIHRvYXN0KCJVUEkgSUQgY29waWVk"
    "OiAiKyhkLnVwaXx8IiIpKTsKICB9OwogIGlmKHZlckIpdmVyQi5vbmNsaWNrPWFzeW5jKCk9PnsK"
    "ICAgIGlmKGRvbmV8fGJ1c3l8fGxlZnQ8PTApcmV0dXJuOwogICAgaWYoU1RPUkUuYXV0b2NoZWNr"
    "PT09ZmFsc2UpeyAgICAgICAgICAvKiBubyBhdXRvbWF0aWMgY2hlY2tpbmcg4oCUIGNvbGxlY3Qg"
    "cHJvb2YgKi8KICAgICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXpl"
    "OjFyZW0iPlNlbmQgeW91ciBwYXltZW50IHByb29mPC9oMz4KICAgICAgICA8cCBjbGFzcz0ic3Vi"
    "Ij5BIHNjcmVlbnNob3Qgb2YgdGhlIHN1Y2Nlc3NmdWwgcGF5bWVudCBhbmQgdGhlIFVUUi48L3A+"
    "CiAgICAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJl"
    "bD5TY3JlZW5zaG90PC9sYWJlbD4KICAgICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0icGZz"
    "aG90IiB0eXBlPSJmaWxlIiBhY2NlcHQ9ImltYWdlLyoiPjwvZGl2PgogICAgICAgIDxkaXYgY2xh"
    "c3M9ImZpZWxkIj48bGFiZWw+VVRSIC8gdHJhbnNhY3Rpb24gbnVtYmVyPC9sYWJlbD4KICAgICAg"
    "ICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBpZD0icGZ1dHIiIGlucHV0bW9kZT0ibnVtZXJpYyIgcGxh"
    "Y2Vob2xkZXI9IjEyIGRpZ2l0cyI+PC9kaXY+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGN0"
    "YSBibG9jayIgaWQ9InBmZ28iPlN1Ym1pdCBmb3IgcmV2aWV3PC9idXR0b24+CiAgICAgICAgPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiIGRh"
    "dGEtY2xvc2U+Q2FuY2VsPC9idXR0b24+YCk7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJwZmdvIikub25jbGljaz1hc3luYygpPT57CiAgICAgICAgY29uc3QgZj1kb2N1bWVudC5nZXRF"
    "bGVtZW50QnlJZCgicGZzaG90Iik7CiAgICAgICAgY29uc3QgdT0oZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoInBmdXRyIikudmFsdWV8fCIiKS5yZXBsYWNlKC9cRC9nLCIiKTsKICAgICAgICBpZih1"
    "Lmxlbmd0aDw2KXt0b2FzdCgiRW50ZXIgdGhlIFVUUiBmcm9tIHlvdXIgcGF5bWVudCBhcHAiKTty"
    "ZXR1cm47fQogICAgICAgIGNvbnN0IGZpbGU9KGYmJmYuZmlsZXMmJmYuZmlsZXNbMF0pfHxudWxs"
    "OwogICAgICAgIGxldCBzaG90PSIiOwogICAgICAgIGlmKGZpbGUpewogICAgICAgICAgaWYoZmls"
    "ZS5zaXplPjgqMTAyNCoxMDI0KXt0b2FzdCgiVGhhdCBpbWFnZSBpcyB0b28gbGFyZ2UgKDggTUIg"
    "bWF4KSIpO3JldHVybjt9CiAgICAgICAgICBzaG90PWF3YWl0IG5ldyBQcm9taXNlKHJlcz0+e2Nv"
    "bnN0IHJkPW5ldyBGaWxlUmVhZGVyKCk7CiAgICAgICAgICAgIHJkLm9ubG9hZD0oKT0+cmVzKFN0"
    "cmluZyhyZC5yZXN1bHR8fCIiKSk7cmQub25lcnJvcj0oKT0+cmVzKCIiKTsKICAgICAgICAgICAg"
    "cmQucmVhZEFzRGF0YVVSTChmaWxlKTt9KTsKICAgICAgICB9CiAgICAgICAgY29uc3QgYnRuPWRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJwZmdvIik7YnRuLmRpc2FibGVkPXRydWU7YnRuLnRleHRD"
    "b250ZW50PSJTZW5kaW5n4oCmIjsKICAgICAgICBjb25zdCBycj1hd2FpdCBhcGlUcnkoIi9hcGkv"
    "ZGVwb3NpdC9jbGFpbSIse2lkOmQuaWQsdXRyOnUsc2hvdDpzaG90fSk7CiAgICAgICAgYnRuLmRp"
    "c2FibGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0iU3VibWl0IGZvciByZXZpZXciOwogICAgICAg"
    "IGlmKCFycilyZXR1cm47CiAgICAgICAgY2xvc2VTaGVldCgpO3RvYXN0KCJTZW50IOKAlCB3ZSB3"
    "aWxsIGNvbmZpcm0gaXQgc2hvcnRseSIpOwogICAgICAgIGF3YWl0IHJlZnJlc2goKTsKICAgICAg"
    "fTsKICAgICAgcmV0dXJuOwogICAgfQogICAgYnVzeT10cnVlO3ZlckIuZGlzYWJsZWQ9dHJ1ZTt2"
    "ZXJCLnRleHRDb250ZW50PSJDaGVja2luZ+KApiI7ZygiczMiKS5jbGFzc05hbWU9InN0ZXAgcnVu"
    "IjsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2RlcG9zaXQvdmVyaWZ5Iix7aWQ6ZC5p"
    "ZH0pOwogICAgYnVzeT1mYWxzZTsKICAgIGlmKCFhbGl2ZSgpKXJldHVybjsKICAgIHZlckIuZGlz"
    "YWJsZWQ9ZmFsc2U7dmVyQi50ZXh0Q29udGVudD1DTVMudmVyaWZ5QnRuOwogICAgaWYoIXIpcmV0"
    "dXJuOwogICAgaWYoci5zdGF0dXM9PT0iY29tcGxldGVkIil7CiAgICAgIGRvbmU9dHJ1ZTtjbGVh"
    "ckludGVydmFsKGRlcFRpY2spOwogICAgICBnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCBvbiI7Zygi"
    "czQiKS5jbGFzc05hbWU9InN0ZXAgb24iOwogICAgICBpZih0eXBlb2Ygci5iYWxhbmNlPT09Im51"
    "bWJlciIpQkFMPXIuYmFsYW5jZTsKICAgICAgc3luY0JhbCgpO2J1cnN0KCk7dG9hc3QoQ01TLm1z"
    "Z09rKTsKICAgICAgc2V0VGltZW91dCgoKT0+e2Nsb3NlU2hlZXQoKTtyZWZyZXNoKCk7fSw5MDAp"
    "OwogICAgfWVsc2UgaWYoci5zdGF0dXM9PT0icmV2aWV3Iil7CiAgICAgIGcoInMzIikuY2xhc3NO"
    "YW1lPSJzdGVwIG9uIjt0b2FzdCgiUGF5bWVudCBzZW50IGZvciBjaGVja2luZyDigJQgd2Ugd2ls"
    "bCBjb25maXJtIGl0IHNob3J0bHkiKTsKICAgIH1lbHNlIGlmKHIuc3RhdHVzPT09ImV4cGlyZWQi"
    "KXsKICAgICAgZXhwaXJlKCk7CiAgICB9ZWxzZXsKICAgICAgdG9hc3QoQ01TLm1zZ05vbmUpOwog"
    "ICAgfQogIH07CiAgaWYodXRyQil1dHJCLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3Qgdj1w"
    "cm9tcHQoIkVudGVyIHRoZSAxMi1kaWdpdCBVVFIgZnJvbSB5b3VyIHBheW1lbnQgYXBwIik7CiAg"
    "ICBpZighdilyZXR1cm47CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9kZXBvc2l0L3V0"
    "ciIse2lkOmQuaWQsdXRyOnZ9KTsKICAgIGlmKHIpdG9hc3QoIlRoYW5rcyDigJQgd2UgYXJlIGNo"
    "ZWNraW5nIHlvdXIgcGF5bWVudCIpOwogIH07Cn0KZnVuY3Rpb24gZGVwb3NpdFNoZWV0KGEpewog"
    "IGlmKExJVkUpcmV0dXJuIGxpdmVEZXBvc2l0U2hlZXQoYSk7CiAgY2xlYXJJbnRlcnZhbChkZXBU"
    "aWNrKTtjbGVhclRpbWVvdXQoZGVwSm9iKTsKICBjb25zdCBjb2RlPShDTVMucGF5UHJlZml4fHwi"
    "UEFZIikrTWF0aC5mbG9vcigxMDAwK01hdGgucmFuZG9tKCkqODk5OSk7CiAgY29uc3QgbWlucz1N"
    "YXRoLm1heCgxLHBhcnNlSW50KENNUy5wYXlXaW5kb3csMTApfHw1KSwgdG90YWw9bWlucyo2MDsK"
    "ICBsZXQgbGVmdD10b3RhbCxwYWlkPWZhbHNlLGRvbmU9ZmFsc2UsYnVzeT1mYWxzZTsKICBzaGVl"
    "dChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+JHtDTVMuc2hlZXRU"
    "aXRsZX0gJHttb25leShhKX08L2gzPgogICAgPHAgY2xhc3M9InN1YiI+JHtDTVMuc2hlZXRTdWIu"
    "cmVwbGFjZSgie219IixtaW5zKX08L3A+CiAgICA8ZGl2IGNsYXNzPSJxciI+UVIgQ09ERTxicj4o"
    "Z2VuZXJhdGVkIGJ5IGJvdCk8L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5VUEkgSUQ8"
    "L3NwYW4+PGI+JHtlc2MoU1RPUkUudXBpfHwibm90IHNldCB5ZXQiKX08L2I+PC9kaXY+CiAgICA8"
    "ZGl2IGNsYXNzPSJrdiI+PHNwYW4+UGF5ZWU8L3NwYW4+PGI+JHtlc2MoU1RPUkUucGF5ZWV8fCLi"
    "gJQiKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+QW1vdW50PC9zcGFuPjxi"
    "PiR7bW9uZXkoYSl9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPk5vdGUgLyBy"
    "ZWZlcmVuY2U8L3NwYW4+PGI+JHtjb2RlfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48"
    "c3Bhbj5FeHBpcmVzIGluPC9zcGFuPjxiIGlkPSJkcGNkIj4ke21pbnN9OjAwPC9iPjwvZGl2Pgog"
    "ICAgPGRpdiBjbGFzcz0idGltZXJiYXIiIGlkPSJkcGJhciI+PGkgc3R5bGU9IndpZHRoOjEwMCUi"
    "PjwvaT48L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN0ZXBzIiBpZD0iZHN0ZXBzIj4KICAgICAgPGRp"
    "diBjbGFzcz0ic3RlcCBvbiI+PGk+MTwvaT4ke0NNUy5zdGVwMX08L2Rpdj4KICAgICAgPGRpdiBj"
    "bGFzcz0ic3RlcCIgaWQ9InMyIj48aT4yPC9pPiR7Q01TLnN0ZXAyfTwvZGl2PgogICAgICA8ZGl2"
    "IGNsYXNzPSJzdGVwIiBpZD0iczMiPjxpPjM8L2k+JHtDTVMuc3RlcDN9PC9kaXY+CiAgICAgIDxk"
    "aXYgY2xhc3M9InN0ZXAiIGlkPSJzNCI+PGk+NDwvaT4ke0NNUy5zdGVwNH08L2Rpdj4KICAgIDwv"
    "ZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPgogICAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIiBpZD0iZHBheSI+JHtDTVMucGF5QnRufTwvYnV0dG9u"
    "PgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2xhc3MiIGlkPSJkdmVyIj4ke0NNUy52ZXJpZnlC"
    "dG59PC9idXR0b24+PC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCBibG9jayBzbSIg"
    "aWQ9ImR1dHIiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+JHtDTVMudXRyQnRufTwvYnV0dG9uPgog"
    "ICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IGJsb2NrIHNtIiBzdHlsZT0ibWFyZ2luLXRvcDo4"
    "cHgiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1dHRvbj5gKTsKICBjb25zdCBnPWlkPT5kb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZChpZCk7CiAgY29uc3QgcGF5Qj1nKCJkcGF5IiksdmVyQj1nKCJkdmVyIiks"
    "dXRyQj1nKCJkdXRyIik7CiAgZnVuY3Rpb24gZm10KG4pe3JldHVybiBNYXRoLmZsb29yKG4vNjAp"
    "KyI6IitTdHJpbmcobiU2MCkucGFkU3RhcnQoMiwiMCIpO30KICBmdW5jdGlvbiBhbGl2ZSgpe3Jl"
    "dHVybiBnKCJkcGNkIikmJmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzaGVldCIpLmNsYXNzTGlz"
    "dC5jb250YWlucygib3BlbiIpO30KICBmdW5jdGlvbiBleHBpcmUoKXsKICAgIGNsZWFySW50ZXJ2"
    "YWwoZGVwVGljayk7Y2xlYXJUaW1lb3V0KGRlcEpvYik7CiAgICBpZighYWxpdmUoKSlyZXR1cm47"
    "CiAgICBnKCJkcGNkIikudGV4dENvbnRlbnQ9IjA6MDAiO2coImRwYmFyIikuY2xhc3NMaXN0LmFk"
    "ZCgibG93Iik7ZygiZHBiYXIiKS5maXJzdENoaWxkLnN0eWxlLndpZHRoPSIwJSI7CiAgICBwYXlC"
    "LmRpc2FibGVkPXRydWU7dmVyQi5kaXNhYmxlZD10cnVlO3V0ckIuZGlzYWJsZWQ9dHJ1ZTsKICAg"
    "IGcoInMyIikuY2xhc3NOYW1lPSJzdGVwIjtnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCI7CiAgICB2"
    "ZXJCLnRleHRDb250ZW50PSJFeHBpcmVkIjtwYXlCLmNsYXNzTmFtZT0iYnRuIjtwYXlCLmRpc2Fi"
    "bGVkPWZhbHNlO3BheUIudGV4dENvbnRlbnQ9Ik5ldyBSZXF1ZXN0IjsKICAgIHBheUIub25jbGlj"
    "az0oKT0+ZGVwb3NpdFNoZWV0KGEpOwogICAgdG9hc3QoQ01TLm1zZ0V4cGlyZWQpOwogIH0KICBk"
    "ZXBUaWNrPXNldEludGVydmFsKCgpPT57CiAgICBpZighYWxpdmUoKSl7Y2xlYXJJbnRlcnZhbChk"
    "ZXBUaWNrKTtyZXR1cm47fQogICAgbGVmdC0tO2coImRwY2QiKS50ZXh0Q29udGVudD1mbXQoTWF0"
    "aC5tYXgoMCxsZWZ0KSk7CiAgICBnKCJkcGJhciIpLmZpcnN0Q2hpbGQuc3R5bGUud2lkdGg9KE1h"
    "dGgubWF4KDAsbGVmdCkvdG90YWwqMTAwKSsiJSI7CiAgICBnKCJkcGJhciIpLmNsYXNzTGlzdC50"
    "b2dnbGUoImxvdyIsbGVmdDw9NjApOwogICAgaWYobGVmdDw9MClleHBpcmUoKTsKICB9LDEwMDAp"
    "OwogIGlmKHBheUIpcGF5Qi5vbmNsaWNrPSgpPT57CiAgICBpZihkb25lKXJldHVybjsKICAgIHBh"
    "aWQ9dHJ1ZTtnKCJzMiIpLmNsYXNzTmFtZT0ic3RlcCBvbiI7ZygiczMiKS5jbGFzc05hbWU9InN0"
    "ZXAgcnVuIjsKICAgIHBheUIudGV4dENvbnRlbnQ9IlBheSBBZ2FpbiI7dmVyQi5jbGFzc0xpc3Qu"
    "cmVtb3ZlKCJnbGFzcyIpO3ZlckIuY2xhc3NMaXN0LmFkZCgiY3RhIik7CiAgICB0b2FzdCgiT3Bl"
    "bmluZyB5b3VyIFVQSSBhcHAg4oCUIHBheSAiK21vbmV5KGEpKyIsIHRoZW4gdGFwIFZlcmlmeSIp"
    "OwogIH07CiAgdmVyQi5vbmNsaWNrPSgpPT57CiAgICBpZihkb25lfHxidXN5fHxsZWZ0PD0wKXJl"
    "dHVybjsKICAgIGJ1c3k9dHJ1ZTt2ZXJCLmRpc2FibGVkPXRydWU7dmVyQi50ZXh0Q29udGVudD0i"
    "Q2hlY2tpbmfigKYiOwogICAgZygiczMiKS5jbGFzc05hbWU9InN0ZXAgcnVuIjt0b2FzdChDTVMu"
    "bXNnQ2hlY2tpbmcpOwogICAgZGVwSm9iPXNldFRpbWVvdXQoKCk9PnsKICAgICAgYnVzeT1mYWxz"
    "ZTsKICAgICAgaWYoIWFsaXZlKCkpcmV0dXJuOwogICAgICBpZihwYWlkKXsKICAgICAgICBkb25l"
    "PXRydWU7Y2xlYXJJbnRlcnZhbChkZXBUaWNrKTsKICAgICAgICBnKCJzMyIpLmNsYXNzTmFtZT0i"
    "c3RlcCBvbiI7ZygiczQiKS5jbGFzc05hbWU9InN0ZXAgb24iOwogICAgICAgIHZlckIudGV4dENv"
    "bnRlbnQ9IlZlcmlmaWVkIjsKICAgICAgICBCQUwrPU51bWJlcihhKTtzeW5jQmFsKCk7YnVyc3Qo"
    "KTt0b2FzdChDTVMubXNnT2spOwogICAgICAgIHNldFRpbWVvdXQoY2xvc2VTaGVldCwxMjAwKTsK"
    "ICAgICAgfWVsc2V7CiAgICAgICAgZygiczMiKS5jbGFzc05hbWU9InN0ZXAiO3ZlckIuZGlzYWJs"
    "ZWQ9ZmFsc2U7dmVyQi50ZXh0Q29udGVudD1DTVMudmVyaWZ5QnRuOwogICAgICAgIHRvYXN0KENN"
    "Uy5tc2dOb25lKTsKICAgICAgfQogICAgfSwxNzAwKTsKICB9OwogIHV0ckIub25jbGljaz0oKT0+"
    "e2lmKGxlZnQ+MCl0b2FzdCgiVGhhbmtzIOKAlCB3ZSBhcmUgY2hlY2tpbmcgeW91ciBwYXltZW50"
    "Iik7fTsKfQpmdW5jdGlvbiBzeW5jQmFsKCl7dGlja1RvKGRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJiYWwiKSxCQUwpO3RpY2tUbyhkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiYmFsMiIpLEJBTCk7"
    "fQpkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCJjbGljayIsZT0+e2NvbnN0IGM9ZS50YXJnZXQu"
    "Y2xvc2VzdCgiW2RhdGEtY29uZmlybV0iKTtpZighYylyZXR1cm47CiAgY29uc3QgcD0rYy5kYXRh"
    "c2V0LmNvbmZpcm07aWYoQkFMPHApe3RvYXN0KCJOb3QgZW5vdWdoIGJhbGFuY2Ug4oCUIGFkZCBm"
    "dW5kcyIpO3JldHVybn0KICBCQUwtPXA7c3luY0JhbCgpO2Nsb3NlU2hlZXQoKTtidXJzdCgpO3Rv"
    "YXN0KCJQdXJjaGFzZWQg4oCUIE9UUCBpcyBpbiBPcmRlcnMiKTt9KTsKCi8qIOKUgOKUgCBBRE1J"
    "TiDilIDilIAgKi8KbGV0IEFUQUI9ImRhc2giOwpkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIj"
    "YXN1YiBidXR0b24iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogIGRvY3VtZW50LnF1ZXJ5"
    "U2VsZWN0b3JBbGwoIiNhc3ViIGJ1dHRvbiIpLmZvckVhY2goeD0+eC5jbGFzc0xpc3QucmVtb3Zl"
    "KCJvbiIpKTtiLmNsYXNzTGlzdC5hZGQoIm9uIik7CiAgQVRBQj1iLmRhdGFzZXQuYTtyZW5kZXJB"
    "ZG1pbigpO3dpbmRvdy5zY3JvbGxUbygwLDApO30pOwpmdW5jdGlvbiByZW5kZXJBZG1pbigpewog"
    "IGNvbnN0IHN0PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJhc3RhZ2UiKTsKICBpZighYWRtaW5B"
    "bGxvd2VkKCkpe3N0LmlubmVySFRNTD0nPGRpdiBjbGFzcz0iY2FyZCIgc3R5bGU9Im1hcmdpbjox"
    "NnB4O3RleHQtYWxpZ246Y2VudGVyIj4nCiAgICArJzxoMyBzdHlsZT0ibWFyZ2luOjAgMCA2cHgi"
    "PkFkbWlucyBvbmx5PC9oMz4nCiAgICArJzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+"
    "VGhpcyBwYW5lbCBpcyBub3QgYXZhaWxhYmxlIG9uIHlvdXIgYWNjb3VudC48L3A+PC9kaXY+Jzty"
    "ZXR1cm47fQogIHN0LmlubmVySFRNTD0oe2Rhc2g6YURhc2gsY2hhbm5lbHM6YUNoYW5uZWxzLGxv"
    "Z3M6YUxvZ3MscGF5OmFQYXksY29udGVudDphQ29udGVudCxmZWF0dXJlczphRmVhdHVyZXMsbWFy"
    "a2V0OmFNYXJrZXQsYW5hbHl0aWNzOmFBbmFseXRpY3Msc2VjdXJpdHk6YVNlY3VyaXR5LHN0b2Nr"
    "OmFTdG9jayxvcmRlcnM6YU9yZGVycyx1c2VyczphVXNlcnMsc2V0dGluZ3M6YVNldHRpbmdzfSlb"
    "QVRBQl0oKTsKICBiaW5kQWRtaW4oKTtyZXZlYWxTdGFnZShzdCk7YmluZFRpbHQoKTtpZih3aW5k"
    "b3cuX192dGFiUGxhY2UpcmVxdWVzdEFuaW1hdGlvbkZyYW1lKHdpbmRvdy5fX3Z0YWJQbGFjZSk7"
    "fQoKZnVuY3Rpb24gYURhc2goKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImtwaXMiPiR7W1siUmV2"
    "ZW51ZSB0b2RheSIsbW9uZXkoQURNLmtwaS5yZXYpXSxbIk9yZGVycyB0b2RheSIsYWRtTnVtKEFE"
    "TS5rcGkub3JkZXJzKV0sCiAgICAgIFsiVXNlcnMiLGFkbU51bShBRE0ua3BpLnVzZXJzKV0sWyJB"
    "dXRvLXZlcmlmaWVkIixhZG1OdW0oQURNLmtwaS5hdXRvKV1dCiAgICAubWFwKChbayx2XSk9PmA8"
    "ZGl2IGNsYXNzPSJrcGkiPjxzbWFsbD4ke2t9PC9zbWFsbD48Yj4ke3Z9PC9iPjwvZGl2PmApLmpv"
    "aW4oIiIpfTwvZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpm"
    "bGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwg"
    "b24iPjxpIGNsYXNzPSJkb3QiPjwvaT5QYXltZW50IGNoZWNraW5nPC9zcGFuPgogICAgIDxkaXYg"
    "c3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4"
    "MikiPiR7QURNLnBheS5nbWFpbF9zZXQ/IkdtYWlsIHdhdGNoZXIgb24gwrcgIitBRE0ucGF5LnBl"
    "bmRpbmcrIiBhd2FpdGluZyByZXZpZXciOiJHbWFpbCBub3Qgc2V0IOKAlCBkZXBvc2l0cyBnbyB0"
    "byBtYW51YWwgcmV2aWV3In08L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIg"
    "ZGF0YS1hLWdvPSJwYXkiPk9wZW48L2J1dHRvbj48L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2FyZCI+"
    "PGgzPkxhc3QgNyBkYXlzPC9oMz48cCBjbGFzcz0ic3ViIj5SZXZlbnVlIHRyZW5kPC9wPgogICAg"
    "IDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpmbGV4LWVuZDtnYXA6N3B4O2hl"
    "aWdodDo5NnB4O21hcmdpbi10b3A6MTRweCI+CiAgICAgICAke0FETS5iYXJzLm1hcChoPT5gPGkg"
    "c3R5bGU9ImZsZXg6MTtoZWlnaHQ6JHtofSU7Ym9yZGVyLXJhZGl1czo3cHggN3B4IDNweCAzcHg7"
    "YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7ZGlzcGxheTpibG9jayI+PC9pPmApLmpvaW4oIiIp"
    "fTwvZGl2PjwvZGl2PgogICAke0coImRpZ2VzdCIsYDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0"
    "eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8c3Bh"
    "biBjbGFzcz0icGlsbCBvbiI+PGkgY2xhc3M9ImRvdCI+PC9pPkRpZ2VzdDwvc3Bhbj4KICAgICA8"
    "ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIo"
    "LS10eDIpIj5OZXh0IHN1bW1hcnkgYXQgMjM6MzA8L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4geHMgc29mdCIgZGF0YS1hLWdvPSJhbmFseXRpY3MiPlZpZXc8L2J1dHRvbj48L2Rpdj5gKX0K"
    "ICAgJHtHKCJzYW5kYm94IixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6"
    "ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJwaWxs"
    "Ij5TYW5kYm94PC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250"
    "LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPiR7QURNLnNldHRpbmdzLnNhbmRib3g/IlRl"
    "c3QgbW9kZSBpcyBPTiI6IlRlc3QgbW9kZSBpcyBvZmYg4oCUIHJlYWwgbW9uZXkgaXMgbGl2ZSJ9"
    "PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5zYW5kYm94KT8iIG9u"
    "IjoiIn0iIGRhdGEtc2V0dGluZz0ic2FuZGJveCI+PC9kaXY+PC9kaXY+YCl9CiAgIDxkaXYgY2xh"
    "c3M9InNlYyI+PGgyPlF1aWNrIEFjdGlvbnM8L2gyPjwvZGl2PgogICA8ZGl2IGNsYXNzPSJncmlk"
    "Ij4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0YS1xYT0iYnJvYWRjYXN0Ij5Ccm9h"
    "ZGNhc3Q8L2J1dHRvbj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIgZGF0YS1xYT0ic3Rv"
    "Y2siPkFkZCBTdG9jazwvYnV0dG9uPgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRh"
    "LXFhPSJmbGFzaCI+Rmxhc2ggU2FsZTwvYnV0dG9uPgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBz"
    "b2Z0IiBkYXRhLXFhPSJiYWNrdXAiPkJhY2t1cCBEQjwvYnV0dG9uPjwvZGl2PmA7Cn0KCmZ1bmN0"
    "aW9uIGFDaGFubmVscygpewogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkZvcmNlLWpv"
    "aW4gY2hhbm5lbHM8L2gzPgogICAgPHAgY2xhc3M9InN1YiI+QWRkIG9yIHJlbW92ZSBhbnkgbnVt"
    "YmVyIG9mIGNoYW5uZWxzLiBTYXZlZCB0byB0aGUgZGF0YWJhc2Ug4oCUIGluc3RhbnQsIG5vIHJl"
    "c3RhcnQuPC9wPgogICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEzcHgi"
    "PjxsYWJlbD5DaGFubmVsIGxpbmssIEB1c2VybmFtZSBvciAtMTAwIElEPC9sYWJlbD4KICAgICAg"
    "PGlucHV0IGNsYXNzPSJpbnAiIGlkPSJjaC1pbiIgcGxhY2Vob2xkZXI9IkBjaGFubmVsIMK3IHQu"
    "bWUvK2ludml0ZSDCtyAtMTAw4oCmIj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFi"
    "ZWw+VHlwZTwvbGFiZWw+CiAgICAgIDxzZWxlY3QgY2xhc3M9ImlucCIgaWQ9ImNoLXR5cGUiPjxv"
    "cHRpb24gdmFsdWU9IjEiPlJlcXVpcmVkIOKAlCBtdXN0IGpvaW48L29wdGlvbj48b3B0aW9uIHZh"
    "bHVlPSIwIj5PcHRpb25hbCDigJQganVzdCBzdWdnZXN0ZWQ8L29wdGlvbj48L3NlbGVjdD48L2Rp"
    "dj4KICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBpZD0iY2gtYWRk"
    "Ij4ke2ljKCJwbHVzIil9IEFkZCBDaGFubmVsPC9idXR0b24+CiAgICA8YnV0dG9uIGNsYXNzPSJi"
    "dG4gZ2hvc3QiIGRhdGEtbWlzYz0iY2hhbm5lbHRlc3QiPlRlc3QgQm90PC9idXR0b24+PC9kaXY+"
    "PC9kaXY+CiAgPGRpdiBjbGFzcz0ic2VjIj48aDI+QWN0aXZlICgke0NIQU5ORUxTLmxlbmd0aH0p"
    "PC9oMj48L2Rpdj4KICAke0NIQU5ORUxTLm1hcCgoYyxpKT0+YDxkaXYgY2xhc3M9ImNhcmQgdGln"
    "aHQiPgogICAgIDxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2Fw"
    "OjEwcHgiPgogICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygic2VuZCIpfTwvZGl2PgogICAg"
    "ICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44"
    "M3JlbTtkaXNwbGF5OmJsb2NrO3doaXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4"
    "dC1vdmVyZmxvdzplbGxpcHNpcyI+JHtlc2MoYy5uYW1lKX08L2I+CiAgICAgICAgIDxzbWFsbCBz"
    "dHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4ke2VzYyhjLmhhbmRsZSl9"
    "IMK3ICR7Yy5tZW1iZXJzfSBtZW1iZXJzPC9zbWFsbD48L2Rpdj4KICAgICAgIDxkaXYgY2xhc3M9"
    "InRnIHNtICR7Yy5vbj8ib24iOiIifSIgZGF0YS1jaG9uPSIke2MuaWR9Ij48L2Rpdj48L2Rpdj4K"
    "ICAgICA8ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjZweDttYXJnaW4tdG9wOjEwcHg7Zmxl"
    "eC13cmFwOndyYXAiPgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzICR7Yy5yZXE/IiI6InNv"
    "ZnQifSIgZGF0YS1jaHJlcT0iJHtjLmlkfSI+JHtjLnJlcT8iUmVxdWlyZWQiOiJPcHRpb25hbCJ9"
    "PC9idXR0b24+CiAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1jb3B5PSJ0"
    "ZXh0IiBkYXRhLWNvcHktdmFsdWU9IiR7ZXNjKGMuaGFuZGxlKX0iPkNvcHk8L2J1dHRvbj4KICAg"
    "ICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBiYWQiIHN0eWxlPSJtYXJnaW4tbGVmdDphdXRvIiBk"
    "YXRhLWNoZGVsPSIke2MuaWR9Ij5SZW1vdmU8L2J1dHRvbj48L2Rpdj48L2Rpdj5gKS5qb2luKCIi"
    "KQogICB8fCc8ZGl2IGNsYXNzPSJjYXJkIj48cCBjbGFzcz0ic3ViIj5ObyBjaGFubmVscyDigJQg"
    "dXNlcnMgY2FuIGVudGVyIGZyZWVseS48L3A+PC9kaXY+J31gOwp9CgpmdW5jdGlvbiBhTG9ncygp"
    "ewogIGNvbnN0IG9wdHM9aWQ9PkxPR0NILm1hcChsPT5gPG9wdGlvbiB2YWx1ZT0iJHtsLmlkfSIg"
    "JHtsLmlkPT09aWQ/InNlbGVjdGVkIjoiIn0+JHtlc2MobC5uYW1lKX08L29wdGlvbj5gKS5qb2lu"
    "KCIiKTsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxl"
    "PSJwYWRkaW5nLXRvcDowIj4KICAgICAgPGRpdiBjbGFzcz0idCI+TG9nZ2luZyBtYXN0ZXIgc3dp"
    "dGNoPHNtYWxsPk9mZiA9IHRoZSBib3QgcG9zdHMgbm8gbG9ncyBhbnl3aGVyZTwvc21hbGw+PC9k"
    "aXY+CiAgICAgIDxkaXYgY2xhc3M9InRnICR7TE9HU19NQVNURVI/Im9uIjoiIn0iIGRhdGEtbWFz"
    "dGVyPjwvZGl2PjwvZGl2PjwvZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+TG9nIGNoYW5u"
    "ZWxzPC9oMz48cCBjbGFzcz0ic3ViIj5BZGQgYXMgbWFueSBhcyB5b3UgbGlrZS4gVGhlIHN0YXJy"
    "ZWQgb25lIGlzIHRoZSBwcmVmZXJyZWQvZGVmYXVsdCBjaGFubmVsLjwvcD4KICAgICA8ZGl2IGNs"
    "YXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPkNoYW5uZWwgbGluaywg"
    "QHVzZXJuYW1lIG9yIC0xMDAgSUQ8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGlk"
    "PSJsZy1pbiIgcGxhY2Vob2xkZXI9IkBsb2dzX2NoYW5uZWwgwrcgLTEwMOKApiI+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0iYnRucm93Ij48YnV0dG9uIGNsYXNzPSJidG4iIGlkPSJsZy1hZGQiPiR7"
    "aWMoInBsdXMiKX0gQWRkIExvZyBDaGFubmVsPC9idXR0b24+CiAgICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIHNvZnQiIGRhdGEtbWlzYz0ibG9ndGVzdCI+U2VuZCBUZXN0PC9idXR0b24+PC9kaXY+PC9k"
    "aXY+CiAgICR7TE9HQ0gubWFwKGw9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlz"
    "cGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgIDxkaXYgY2xhc3M9"
    "InN0YXIgJHtsLnByaW1hcnk/Im9uIjoiIn0iIGRhdGEtbGdzdGFyPSIke2wuaWR9Ij4ke2ljKCJz"
    "dGFyIil9PC9kaXY+CiAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5"
    "bGU9ImZvbnQtc2l6ZTouODNyZW07ZGlzcGxheTpibG9jazt3aGl0ZS1zcGFjZTpub3dyYXA7b3Zl"
    "cmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXMiPiR7ZXNjKGwubmFtZSl9PC9iPgog"
    "ICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj4k"
    "e2VzYyhsLmhhbmRsZSl9JHtsLnByaW1hcnk/IiDCtyBwcmVmZXJyZWQiOiIifTwvc21hbGw+PC9k"
    "aXY+CiAgICAgIDxkaXYgY2xhc3M9InRnIHNtICR7bC5vbj8ib24iOiIifSIgZGF0YS1sZ29uPSIk"
    "e2wuaWR9Ij48L2Rpdj4KICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIGJhZCIgZGF0YS1sZ2Rl"
    "bD0iJHtsLmlkfSI+UmVtb3ZlPC9idXR0b24+PC9kaXY+YCkuam9pbigiIikKICAgIHx8JzxkaXYg"
    "Y2xhc3M9ImNhcmQiPjxwIGNsYXNzPSJzdWIiPk5vIGxvZyBjaGFubmVscyB5ZXQuPC9wPjwvZGl2"
    "Pid9CiAgIDxkaXYgY2xhc3M9InNlYyI+PGgyPkV2ZW50cyAmYW1wOyByb3V0aW5nPC9oMj48YSBk"
    "YXRhLWV2LWFsbD5Ub2dnbGUgYWxsPC9hPjwvZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJkIj4ke0VW"
    "RU5UUy5tYXAoKGUsaSk9PmAKICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0IiBz"
    "dHlsZT0iZmxleDoxIj4ke2VzYyhlLmspfQogICAgICAgPHNtYWxsPjxzZWxlY3QgY2xhc3M9Imlu"
    "cCIgc3R5bGU9ImhlaWdodDozMHB4O2ZvbnQtc2l6ZTouN3JlbTttYXJnaW4tdG9wOjVweDttYXgt"
    "d2lkdGg6MTkwcHgiIGRhdGEtZXZjaD0iJHtpfSIgJHtlLm9uPyIiOiJkaXNhYmxlZCJ9PiR7b3B0"
    "cyhlLmNoKX08L3NlbGVjdD48L3NtYWxsPjwvZGl2PgogICAgICAgPGRpdiBjbGFzcz0idGcgJHtl"
    "Lm9uPyJvbiI6IiJ9IiBkYXRhLWV2b249IiR7aX0iPjwvZGl2PjwvZGl2PmApLmpvaW4oIiIpfTwv"
    "ZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+UmVjZW50IGxvZyBvdXRwdXQ8L2gzPgogICAg"
    "ICR7KChBRE0ubG9nZmVlZCYmQURNLmxvZ2ZlZWQubGVuZ3RoKT9BRE0ubG9nZmVlZC5tYXAobD0+"
    "W2wud2hhdCxsLndoZXJlLGwud2hlbl0pOltdKQogICAgICAgLm1hcCgoW3QsYyx3XSk9PmA8ZGl2"
    "IGNsYXNzPSJrdiI+PHNwYW4+JHt0fTwvc3Bhbj48YiBzdHlsZT0iZm9udC1zaXplOi43cmVtIj4k"
    "e2N9IMK3ICR7d308L2I+PC9kaXY+YCkuam9pbigiIil9PC9kaXY+YDsKfQoKZnVuY3Rpb24gYVBh"
    "eSgpewogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9"
    "InBhZGRpbmctdG9wOjAiPgogICAgICA8ZGl2IGNsYXNzPSJ0Ij5HbWFpbCBhdXRvLXZlcmlmeTxz"
    "bWFsbD4ke0FETS5wYXkuZ21haWxfc2V0PyJSZWFkaW5nICIrZXNjKEFETS5wYXkuZ21haWwpOiJO"
    "b3QgY29uZmlndXJlZCDigJQgZGVwb3NpdHMgcXVldWUgZm9yIHJldmlldyJ9PC9zbWFsbD48L2Rp"
    "dj4KICAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5nbWFpbF9lbmFibGVkKT8i"
    "IG9uIjoiIn0iIGRhdGEtc2V0dGluZz0iZ21haWxfZW5hYmxlZCI+PC9kaXY+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+QXV0by1hcHByb3ZlIHNtYWxsIGRlcG9z"
    "aXRzPHNtYWxsPlNraXAgcmV2aWV3IGJlbG93IHRoZSB0aHJlc2hvbGQ8L3NtYWxsPjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuZ21haWxfZW5hYmxlZCk/IiBvbiI6"
    "IiJ9IiBkYXRhLXNldHRpbmc9ImdtYWlsX2VuYWJsZWQiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPk1hbnVhbCBVVFIgZmFsbGJhY2s8c21hbGw+TGV0"
    "IHVzZXJzIHN1Ym1pdCBhIFVUUiBpZiBhdXRvLXZlcmlmeSBtaXNzZXM8L3NtYWxsPjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuYXV0b2RlcCk/IiBvbiI6IiJ9IiBk"
    "YXRhLXNldHRpbmc9ImF1dG9kZXAiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InN3YXAi"
    "PjxkaXYgY2xhc3M9InQiPlZlcmlmeSBvbmx5IHdoZW4gdXNlciB0YXBzIFZlcmlmeTxzbWFsbD5O"
    "byBiYWNrZ3JvdW5kIGNoZWNraW5nIOKAlCBzYXZlcyBHbWFpbCBxdW90YTwvc21hbGw+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy51dHJfZmFsbGJhY2spPyIgb24i"
    "OiIifSIgZGF0YS1zZXR0aW5nPSJ1dHJfZmFsbGJhY2siPjwvZGl2PjwvZGl2PjwvZGl2PgogICA8"
    "ZGl2IGNsYXNzPSJjYXJkIj48aDM+VVBJPC9oMz48cCBjbGFzcz0ic3ViIj4ke0FETS5wYXkudXBp"
    "PyJJbiB1c2UgYnkgZXZlcnkgcGF5bWVudCByZXF1ZXN0LiI6Ik5vdCBzZXQgeWV0IOKAlCB0b3At"
    "dXBzIGFyZSBkaXNhYmxlZCB1bnRpbCB5b3UgYWRkIGl0LiJ9PC9wPgogICAgIDxkaXYgY2xhc3M9"
    "ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+VVBJIElEPC9sYWJlbD4KICAg"
    "ICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0idXBpX2lkIiB2YWx1ZT0iJHtlc2MoQURN"
    "LnBheS51cGl8fCIiKX0iIHBsYWNlaG9sZGVyPSJ5b3VyaGFuZGxlQGJhbmsiPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+UGF5ZWUgbmFtZTwvbGFiZWw+CiAgICAgICA8aW5w"
    "dXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9InVwaV9uYW1lIiB2YWx1ZT0iJHtlc2MoQURNLnBheS5w"
    "YXllZXx8IiIpfSIgcGxhY2Vob2xkZXI9IllvdXIgU3RvcmUiPjwvZGl2PgogICAgIDxkaXYgY2xh"
    "c3M9ImZpZWxkIj48bGFiZWw+Tm90ZSBwcmVmaXg8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNz"
    "PSJpbnAiIGRhdGEtc2V0PSJwYXlfcHJlZml4IiB2YWx1ZT0iJHtlc2MoQURNLnBheS5wcmVmaXh8"
    "fCJQQVkiKX0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+UGF5bWVudCB3"
    "aW5kb3cgKG1pbnV0ZXMpPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNl"
    "dD0icGF5X3dpbmRvdyIgdmFsdWU9IiR7QURNLnBheS53aW5kb3d9Ij48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJmaWVsZCI+PGxhYmVsPk1pbmltdW0gZGVwb3NpdDwvbGFiZWw+CiAgICAgICA8aW5w"
    "dXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9Im1pbl9kZXBvc2l0IiB2YWx1ZT0iJHtBRE0ucGF5Lm1p"
    "bn0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+TWF4aW11bSBkZXBvc2l0"
    "PC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0ibWF4X2RlcG9zaXQi"
    "IHZhbHVlPSIke0FETS5wYXkubWF4fSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxs"
    "YWJlbD5BdXRvLWFwcHJvdmUgdW5kZXIgKDAgPSBvZmYpPC9sYWJlbD4KICAgICAgIDxpbnB1dCBj"
    "bGFzcz0iaW5wIiBkYXRhLXNldD0iYXV0b2RlcF9tYXgiIHZhbHVlPSIke0FETS5wYXkuYXV0b2Rl"
    "cH0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+R21haWwgYWRkcmVzczwv"
    "bGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9ImdtYWlsX2lkIiB2YWx1"
    "ZT0iJHtlc2MoQURNLnBheS5nbWFpbHx8IiIpfSIgcGxhY2Vob2xkZXI9InN0b3JlQGdtYWlsLmNv"
    "bSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5HbWFpbCBhcHAgcGFzc3dv"
    "cmQ8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJnbWFpbF9wYXNz"
    "IiB0eXBlPSJwYXNzd29yZCIgcGxhY2Vob2xkZXI9IiR7QURNLnBheS5nbWFpbF9zZXQ/InNhdmVk"
    "IOKAlCB0eXBlIHRvIHJlcGxhY2UiOiJub3Qgc2V0In0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "Imt2Ij48c3Bhbj5XYWl0aW5nIGZvciByZXZpZXc8L3NwYW4+PGI+JHtBRE0ucGF5LnBlbmRpbmd9"
    "PC9iPjwvZGl2PjwvZGl2PgogICAke0coImNyeXB0byIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5D"
    "cnlwdG8gd2FsbGV0PC9oMz48cCBjbGFzcz0ic3ViIj5VU0RULVRSQzIwIGRlcG9zaXRzLCBhdXRv"
    "LWNvbmZpcm1lZCBvbi1jaGFpbi48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJt"
    "YXJnaW4tdG9wOjEwcHgiPjxsYWJlbD5XYWxsZXQgYWRkcmVzczwvbGFiZWw+PGlucHV0IGNsYXNz"
    "PSJpbnAiIHBsYWNlaG9sZGVyPSJU4oCmIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+"
    "PGxhYmVsPkNvbmZpcm1hdGlvbnMgcmVxdWlyZWQ8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2"
    "YWx1ZT0iMSI+PC9kaXY+CiAgICAgPC9kaXY+YCl9CiAgICR7RygidXBpUm90YXRlIixgPGRpdiBj"
    "bGFzcz0iY2FyZCI+PGgzPlVQSSByb3RhdGlvbjwvaDM+PHAgY2xhc3M9InN1YiI+U3ByZWFkIGxv"
    "YWQgYW5kIHN1cnZpdmUgYSBmcm96ZW4gaGFuZGxlLjwvcD4KICAgICAke1tbImhhbmRsZTFAZmFt"
    "IiwiQWN0aXZlIl0sWyJoYW5kbGUyQGZhbSIsIkFjdGl2ZSJdLFsiaGFuZGxlM0B1cGkiLCJQYXVz"
    "ZWQiXV0KICAgICAgIC5tYXAoKFtoLHN0XSk9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHtofTwv"
    "c3Bhbj48Yj48c3BhbiBjbGFzcz0icGlsbCAke3N0PT09IkFjdGl2ZSI/Im9uIjoib2ZmIn0iPiR7"
    "c3R9PC9zcGFuPjwvYj48L2Rpdj5gKS5qb2luKCIiKX0KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIg"
    "c3R5bGU9Im1hcmdpbi10b3A6MTBweCI+PGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo3cHgi"
    "PgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIHBsYWNlaG9sZGVyPSJhbm90aGVyQHVwaSI+PGJ1"
    "dHRvbiBjbGFzcz0iYnRuIHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9InVwaWFkZCI+QWRk"
    "PC9idXR0b24+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiZGVwQm9udXMiLGA8ZGl2IGNs"
    "YXNzPSJjYXJkIj48aDM+RGVwb3NpdCBib251czwvaDM+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPuKCuTUwMCBhbmQgYWJvdmUgKCUpPC9sYWJl"
    "bD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjUiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZp"
    "ZWxkIj48bGFiZWw+4oK5MTAwMCBhbmQgYWJvdmUgKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9Imlu"
    "cCIgdmFsdWU9IjgiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+4oK5MjAw"
    "MCBhbmQgYWJvdmUgKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjEyIj48L2Rp"
    "dj48L2Rpdj5gKX0KICAgJHtHKCJkZXBMaW1pdHMiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+RGVw"
    "b3NpdCBsaW1pdHM8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRv"
    "cDo4cHgiPjxsYWJlbD5NaW5pbXVtICjigrkpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFs"
    "dWU9IjEwIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPk1heGltdW0gKOKC"
    "uSk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMjAwMDAiPjwvZGl2PjwvZGl2PmAp"
    "fQogICAke0coIndyb25nQW10IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIg"
    "c3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPldyb25nLWFtb3VudCBoYW5kbGlu"
    "ZzxzbWFsbD5DcmVkaXQgd2hhdCB3YXMgYWN0dWFsbHkgcGFpZCBhbmQgZmxhZyBpdDwvc21hbGw+"
    "PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy52ZXJpZnlfb25fdGFw"
    "KT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0idmVyaWZ5X29uX3RhcCI+PC9kaXY+PC9kaXY+PC9k"
    "aXY+YCl9CiAgICR7Rygic3BsaXRQYXkiLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJz"
    "d2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+U3BsaXQgcGF5bWVudDxz"
    "bWFsbD5XYWxsZXQgYmFsYW5jZSBwbHVzIFVQSSBpbiBvbmUgY2hlY2tvdXQ8L3NtYWxsPjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULndyb25nQW10PyIgb24iOiIifSIgZGF0YS1mZWF0"
    "PSJ3cm9uZ0FtdCI+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiYXV0b1JlZnVuZCIsYDxk"
    "aXYgY2xhc3M9ImNhcmQiPjxoMz5BdXRvLXJlZnVuZDwvaDM+CiAgICAgPGRpdiBjbGFzcz0iZmll"
    "bGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPldhcnJhbnR5IHdpbmRvdyAobWludXRl"
    "cyk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMTAiPjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9Imt2Ij48c3Bhbj5SZWZ1bmRlZCB0b2RheTwvc3Bhbj48Yj7igrkxMDUuMDAgwrcgMyBv"
    "cmRlcnM8L2I+PC9kaXY+PC9kaXY+YCl9CiAgIDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5HbWFpbCBh"
    "Y2NvdW50PC9oMz48cCBjbGFzcz0ic3ViIj5UaGUgR21haWwgdGhhdCByZWNlaXZlcyB5b3VyIEZh"
    "bVBheSBwYXltZW50IG1haWxzLiBOb3RoaW5nIGlzIHN0b3JlZCBpbiBjb2RlLjwvcD4KICAgICA8"
    "ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPkdtYWlsIElE"
    "PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgcGxhY2Vob2xkZXI9InlvdXJzdG9yZUBnbWFpbC5j"
    "b20iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+R21haWwgcGFzc3dvcmQ8"
    "L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB0eXBlPSJwYXNzd29yZCIgcGxhY2Vob2xkZXI9IuKA"
    "ouKAouKAouKAouKAouKAouKAouKAouKAouKAouKAouKAoiI+PC9kaXY+CiAgICAgPGRpdiBjbGFz"
    "cz0iYnRucm93Ij48YnV0dG9uIGNsYXNzPSJidG4iIGRhdGEtYWRtaW4tYWN0PSJnbWFpbHRlc3Qi"
    "PlRlc3QgR21haWw8L2J1dHRvbj4KICAgICA8L2Rpdj4KICAgICA8cCBjbGFzcz0ic3ViIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMHB4Ij5UaXA6IGlmIDItc3RlcCB2ZXJpZmljYXRpb24gaXMgb24sIHBh"
    "c3RlIGEgR29vZ2xlIEFwcCBQYXNzd29yZCBoZXJlIGluc3RlYWQgb2YgeW91ciBub3JtYWwgb25l"
    "LjwvcD48L2Rpdj4KICAgPGRpdiBjbGFzcz0ic2VjIj48aDI+UmVjZW50IGF1dG8tdmVyaWZpY2F0"
    "aW9uczwvaDI+PC9kaXY+CiAgICR7KEFETS5kZXBvc2l0c3x8W10pLmxlbmd0aD9BRE0uZGVwb3Np"
    "dHMubWFwKGQ9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2Fs"
    "aWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2lj"
    "KCJ3YWxsZXQiKX08L2Rpdj4KICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+"
    "PGIgc3R5bGU9ImZvbnQtc2l6ZTouODJyZW0iPiR7bW9uZXkoZC5hbXQpfSDCtyAke2VzYyhkLnVz"
    "ZXIpfTwvYj48YnI+CiAgICAgICAgIDxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250"
    "LXNpemU6LjY5cmVtIj4ke2VzYyhkLnV0cnx8Im5vIHJlZmVyZW5jZSB5ZXQiKX08L3NtYWxsPjwv"
    "ZGl2PgogICAgICAgPHNwYW4gY2xhc3M9InBpbGwgJHtkLnN0PT09IkNvbXBsZXRlZCI/Im9uIjoi"
    "aG90In0iPiR7ZXNjKGQuc3QpfTwvc3Bhbj4KICAgICAgICR7ZC5zdD09PSJDb21wbGV0ZWQiPyIi"
    "OmA8YnV0dG9uIGNsYXNzPSJidG4geHMiIGRhdGEtZGVwLW9rPSIke2QuaWR9Ij5BcHByb3ZlPC9i"
    "dXR0b24+YH08L2Rpdj5gKS5qb2luKCIiKQogICAgIDonPGRpdiBjbGFzcz0iY2FyZCB0aWdodCI+"
    "PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBkZXBvc2l0cyB5ZXQuPC9wPjwvZGl2"
    "Pid9YDsKfQoKZnVuY3Rpb24gYUNvbnRlbnQoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQi"
    "PjxoMz5FZGl0IGFwcCB0ZXh0PC9oMz4KICAgICA8cCBjbGFzcz0ic3ViIj5FdmVyeSB3b3JkIHVz"
    "ZXJzIHNlZSDigJQgY2hhbmdlIGl0IGhlcmUgYW5kIHRoZSBzdG9yZSB1cGRhdGVzIGluc3RhbnRs"
    "eS48L3A+CiAgICAgPGRpdiBjbGFzcz0ibXNlYXJjaCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+"
    "PGRpdiBjbGFzcz0iZmllbGR3cmFwIj4ke2ljKCJzZWFyY2giKX08aW5wdXQgaWQ9ImNxIiBwbGFj"
    "ZWhvbGRlcj0iRmluZCB0ZXh0IHRvIGVkaXTigKYiPjwvZGl2PgogICAgICAgPGJ1dHRvbiBjbGFz"
    "cz0iYnRuIHNtIHNvZnQiIGlkPSJjbXNyZXNldCI+UmVzZXQ8L2J1dHRvbj48L2Rpdj4KICAgICA8"
    "cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4Ij5Vc2UgJmx0O2JyJmd0OyBmb3Ig"
    "YSBsaW5lIGJyZWFrLCAmbHQ7YiZndDt3b3JkJmx0Oy9iJmd0OyBmb3IgYm9sZC4ge259ID0gbGl2"
    "ZSBjb3VudHJ5IGNvdW50LCB7bX0gPSBwYXltZW50IG1pbnV0ZXMuPC9wPjwvZGl2PgogICAke0NN"
    "U01FVEEubWFwKChbZ3JwLHJvd3NdKT0+YDxkaXYgY2xhc3M9ImNhcmQgY21zZ3JwIj48aDM+JHtn"
    "cnB9PC9oMz4KICAgICAke3Jvd3MubWFwKChbayxsYmxdKT0+YDxkaXYgY2xhc3M9ImZpZWxkIGNt"
    "c3JvdyIgZGF0YS1mPSIke2VzYygobGJsKyIgIitDTVNba10rIiAiK2dycCkudG9Mb3dlckNhc2Uo"
    "KSl9Ij4KICAgICAgICA8bGFiZWw+JHtsYmx9PC9sYWJlbD4KICAgICAgICA8dGV4dGFyZWEgY2xh"
    "c3M9ImlucCIgcm93cz0iJHtDTVNba10ubGVuZ3RoPjQwPzI6MX0iIGRhdGEtY21zPSIke2t9Ij4k"
    "e2VzYyhDTVNba10pfTwvdGV4dGFyZWE+PC9kaXY+YCkuam9pbigiIil9CiAgIDwvZGl2PmApLmpv"
    "aW4oIiIpfQogICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2Fs"
    "aWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxp"
    "IGNsYXNzPSJkb3QiPjwvaT5MaXZlIGVkaXQ8L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxleDox"
    "O21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+Q2hhbmdlcyBh"
    "cHBseSB0byB0aGUgU3RvcmUgdGFiIHN0cmFpZ2h0IGF3YXk8L2Rpdj4KICAgICA8L2Rpdj5gOwp9"
    "CgpmdW5jdGlvbiBhRmVhdHVyZXMoKXsKICBjb25zdCBvbj1PYmplY3QudmFsdWVzKEZFQVQpLmZp"
    "bHRlcihCb29sZWFuKS5sZW5ndGgsdG90PU9iamVjdC5rZXlzKEZFQVQpLmxlbmd0aDsKICByZXR1"
    "cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5GZWF0dXJlIHN3aXRjaGVzPC9oMz4KICAgICA8cCBj"
    "bGFzcz0ic3ViIj5UdXJuIGFueXRoaW5nIG9mZiBhbmQgaXQgZGlzYXBwZWFycyBmcm9tIHRoZSBh"
    "cHAgY29tcGxldGVseSDigJQgbm8gYnV0dG9ucywgbm8gbWVudXMsIG5vIGxlZnRvdmVycy48L3A+"
    "CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkVuYWJsZWQ8L3NwYW4+PGIgaWQ9ImZjb3VudCI+"
    "JHtvbn0gb2YgJHt0b3R9PC9iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Im1zZWFyY2giIHN0eWxl"
    "PSJtYXJnaW4tdG9wOjEycHgiPjxkaXYgY2xhc3M9ImZpZWxkd3JhcCI+JHtpYygic2VhcmNoIil9"
    "PGlucHV0IGlkPSJmcSIgcGxhY2Vob2xkZXI9IlNlYXJjaCBmZWF0dXJlc+KApiI+PC9kaXY+CiAg"
    "ICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20gc29mdCIgaWQ9ImZhbGwiPkFsbCBvbjwvYnV0dG9u"
    "PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48"
    "YnV0dG9uIGNsYXNzPSJidG4gc20gZ2hvc3QiIGlkPSJmbm9uZSI+QWxsIG9mZjwvYnV0dG9uPgog"
    "ICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNtIHNvZnQiIGlkPSJmcmVzZXQiPlJlc2V0PC9idXR0"
    "b24+PC9kaXY+PC9kaXY+CiAgICR7RkdST1VQUy5tYXAoKFtncnAscm93c10pPT5gPGRpdiBjbGFz"
    "cz0iY2FyZCBmZ3JwIj48aDM+JHtncnB9PC9oMz4KICAgICAke3Jvd3MubWFwKChbayxsLGRdKT0+"
    "YDxkaXYgY2xhc3M9ImZjYXJkIGZyb3ciIGRhdGEtZj0iJHtlc2MoKGwrIiAiK2QrIiAiK2dycCku"
    "dG9Mb3dlckNhc2UoKSl9Ij4KICAgICAgICA8ZGl2IGNsYXNzPSJ0Ij48Yj4ke2x9PC9iPjxzbWFs"
    "bD4ke2R9PC9zbWFsbD48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJ0ZyAke0ZFQVRba10/Im9u"
    "IjoiIn0iIGRhdGEtZmVhdD0iJHtrfSI+PC9kaXY+PC9kaXY+YCkuam9pbigiIil9CiAgIDwvZGl2"
    "PmApLmpvaW4oIiIpfQogICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpm"
    "bGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwg"
    "b24iPjxpIGNsYXNzPSJkb3QiPjwvaT5MaXZlPC9zcGFuPgogICAgIDxkaXYgc3R5bGU9ImZsZXg6"
    "MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPlN3aXRjaGVz"
    "IGFwcGx5IHRvIHRoZSBTdG9yZSB0YWIgaW5zdGFudGx5PC9kaXY+CiAgICAgPC9kaXY+YDsKfQoK"
    "ZnVuY3Rpb24gYU1hcmtldCgpewogIHJldHVybiBgJHtHKCJjb3Vwb25zIixgPGRpdiBjbGFzcz0i"
    "Y2FyZCI+PGgzPkNvdXBvbnM8L2gzPjxwIGNsYXNzPSJzdWIiPlBlcmNlbnRhZ2Ugb2ZmIGF0IGNo"
    "ZWNrb3V0LjwvcD4KICAgICAkeyhBRE0uY291cG9ucyYmQURNLmNvdXBvbnMubGVuZ3RoKT9BRE0u"
    "Y291cG9ucy5tYXAoYz0+YDxkaXYgY2xhc3M9Imt2Ij4KICAgICAgIDxzcGFuPjxiIHN0eWxlPSJj"
    "b2xvcjp2YXIoLS10eCkiPiR7ZXNjKGMuY29kZSl9PC9iPiDCtyAke2MucGVyY2VudH0lIMK3ICR7"
    "Yy51c2VkfS8ke2MubWF4fHwi4oieIn08L3NwYW4+CiAgICAgICA8Yj48c3BhbiBjbGFzcz0icGls"
    "bCAke2MuYWN0aXZlPyJvbiI6Im9mZiJ9Ij4ke2MuYWN0aXZlPyJBY3RpdmUiOiJPZmYifTwvc3Bh"
    "bj48L2I+PC9kaXY+YCkuam9pbigiIikKICAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1h"
    "cmdpbjowIj5ObyBjb3Vwb25zIHlldC48L3A+J30KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPk5ldyBjb2RlPC9sYWJlbD4KICAgICAgIDxkaXYg"
    "c3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4Ij48aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImNwY29k"
    "ZSIgcGxhY2Vob2xkZXI9Ik5FV1lFQVIiPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGlkPSJj"
    "cHBjdCIgcGxhY2Vob2xkZXI9IiUiIHN0eWxlPSJ3aWR0aDo2NnB4Ij4KICAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJjcGFkZCI+QWRkPC9idXR0"
    "b24+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiZmxhc2hTYWxlIixgPGRpdiBjbGFzcz0i"
    "Y2FyZCI+PGgzPkZsYXNoIHNhbGUgc2NoZWR1bGVyPC9oMz48cCBjbGFzcz0ic3ViIj5TdGFydHMs"
    "IGFubm91bmNlcyBhbmQgZW5kcyBieSBpdHNlbGYuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxk"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+RGlzY291bnQgKCUpPC9sYWJlbD48aW5w"
    "dXQgY2xhc3M9ImlucCIgdmFsdWU9IjIwIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+"
    "PGxhYmVsPlN0YXJ0czwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSJUb2RheSAxODow"
    "MCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5FbmRzPC9sYWJlbD48aW5w"
    "dXQgY2xhc3M9ImlucCIgdmFsdWU9IlRvZGF5IDIzOjAwIj48L2Rpdj4KICAgICA8YnV0dG9uIGNs"
    "YXNzPSJidG4gYmxvY2siIGlkPSJmbGFzaGdvIj5TdGFydCBGbGFzaCBTYWxlPC9idXR0b24+PC9k"
    "aXY+YCl9CiAgICR7Rygic2NoZWRCcm9hZGNhc3QiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+U2No"
    "ZWR1bGVkIGJyb2FkY2FzdDwvaDM+PHAgY2xhc3M9InN1YiI+UXVldWUgaXQgYW5kIHRhcmdldCBh"
    "IHNlZ21lbnQuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "MnB4Ij48bGFiZWw+U2VnbWVudDwvbGFiZWw+CiAgICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiPjxv"
    "cHRpb24+RXZlcnlvbmU8L29wdGlvbj48b3B0aW9uPkJ1eWVycyBvbmx5PC9vcHRpb24+PG9wdGlv"
    "bj5OZXZlciBib3VnaHQ8L29wdGlvbj48b3B0aW9uPkJpZyBzcGVuZGVycyAo4oK5MTAwMCspPC9v"
    "cHRpb24+PG9wdGlvbj5JbmFjdGl2ZSAzMCBkYXlzPC9vcHRpb24+PC9zZWxlY3Q+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5TZW5kIGF0PC9sYWJlbD48aW5wdXQgY2xhc3M9"
    "ImlucCIgdmFsdWU9IlRvbW9ycm93IDEwOjAwIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCI+PGxhYmVsPk1lc3NhZ2U8L2xhYmVsPjx0ZXh0YXJlYSBjbGFzcz0iaW5wIiByb3dzPSIyIiBw"
    "bGFjZWhvbGRlcj0iV3JpdGUgeW91ciBicm9hZGNhc3TigKYiPjwvdGV4dGFyZWE+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0iYnRucm93Ij48YnV0dG9uIGNsYXNzPSJidG4iIGlkPSJiY2dvIj5TZW5k"
    "IEJyb2FkY2FzdDwvYnV0dG9uPgogICAgICAgPC9kaXY+PC9kaXY+YCl9CiAgICR7Rygid2luYmFj"
    "ayIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRv"
    "cDowIj48ZGl2IGNsYXNzPSJ0Ij5XaW4tYmFjayBtZXNzYWdlczxzbWFsbD5BdXRvLURNIHVzZXJz"
    "IGluYWN0aXZlIGZvciA3IGFuZCAzMCBkYXlzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNz"
    "PSJ0ZyR7RkVBVC5zcGxpdFBheT8iIG9uIjoiIn0iIGRhdGEtZmVhdD0ic3BsaXRQYXkiPjwvZGl2"
    "PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+RGlzY291bnQgb2ZmZXJlZCAo"
    "JSk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMTAiPjwvZGl2PjwvZGl2PmApfQog"
    "ICAke0coImFiYW5kb25SZW1pbmQiLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlz"
    "cGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9"
    "InBpbGwgb24iPjxpIGNsYXNzPSJkb3QiPjwvaT5SZW1pbmRlcnM8L3NwYW4+CiAgICAgPGRpdiBz"
    "dHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgy"
    "KSI+NCB1bmZpbmlzaGVkIHBheW1lbnRzIG51ZGdlZCB0b2RheTwvZGl2PgogICAgIDwvZGl2PmAp"
    "fQogICAke0coImxveWFsdHkiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+TG95YWx0eSBwb2ludHM8"
    "L2gzPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJl"
    "bD5Qb2ludHMgcGVyIOKCuTEwIHNwZW50PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9"
    "IjEiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+UG9pbnRzIG5lZWRlZCBm"
    "b3Ig4oK5MTA8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMTAwIj48L2Rpdj4KICAg"
    "ICA8YnV0dG9uIGNsYXNzPSJidG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0id2luYmFj"
    "a3NhdmUiPlNhdmU8L2J1dHRvbj48L2Rpdj5gKX0KICAgJHthbnlGKCJyZWZEYXNoIiwicmVmTDIi"
    "KT9gPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlJlZmVycmFsIHBheW91dHM8L2gzPgogICAgIDxkaXYg"
    "Y2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5MZXZlbCAxIGNvbW1p"
    "c3Npb24gKCUpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjUiPjwvZGl2PgogICAg"
    "ICR7RygicmVmTDIiLGA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkxldmVsIDIgY29tbWlzc2lv"
    "biAoJSk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMSI+PC9kaXY+YCl9CiAgICAg"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPlBhaWQgb3V0IHRoaXMgbW9udGg8L3NwYW4+PGI+4oK5Mywy"
    "NDA8L2I+PC9kaXY+PC9kaXY+YDoiIn0KICAgJHtHKCJzYWxlc0ZlZWQiLGA8ZGl2IGNsYXNzPSJj"
    "YXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFzcz0i"
    "dCI+QXV0by1wb3N0IHNhbGVzIHRvIGNoYW5uZWw8c21hbGw+JHtGRUVELmxlbmd0aD9lc2MoRkVF"
    "RFswXVsxXSsiIHNvbGQgwrcgIitGRUVEWzBdWzNdKToibm8gc2FsZXMgeWV0In08L3NtYWxsPjwv"
    "ZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULndpbmJhY2s/IiBvbiI6IiJ9IiBkYXRhLWZl"
    "YXQ9IndpbmJhY2siPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImxlYWRlcmJvYXJkIixg"
    "PGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxlYWRlcmJvYXJkIHByaXplPC9oMz4KICAgICA8ZGl2IGNs"
    "YXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+V2Vla2x5IHByaXplICji"
    "grkpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjUwMCI+PC9kaXY+CiAgICAgPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9InByaXplc2F2ZSI+"
    "U2F2ZTwvYnV0dG9uPjwvZGl2PmApfWA7Cn0KCmZ1bmN0aW9uIGFBbmFseXRpY3MoKXsKICBjb25z"
    "dCB3ZWVrPShBRE0uc2VyaWVzfHxbXSkucmVkdWNlKChhLGIpPT5hK2IsMCk7CiAgY29uc3QgYXZn"
    "PUFETS5rcGkub3JkZXJzP0FETS5rcGkucmV2L0FETS5rcGkub3JkZXJzOjA7CiAgcmV0dXJuIGAk"
    "e0coInJldkNoYXJ0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlJldmVudWU8L2gzPjxwIGNsYXNz"
    "PSJzdWIiPkxhc3QgNyBkYXlzIMK3ICR7bW9uZXkod2Vlayl9IHRvdGFsPC9wPgogICAgIDxkaXYg"
    "Y2xhc3M9ImJhcnMiPiR7QURNLmJhcnMubWFwKGg9PmA8aSBzdHlsZT0iaGVpZ2h0OiR7aH0lIj48"
    "L2k+YCkuam9pbigiIil9PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3BpcyIgc3R5bGU9Im1hcmdp"
    "bi10b3A6MTRweCI+JHtbWyJUb2RheSIsbW9uZXkoQURNLmtwaS5yZXYpXSxbIlRoaXMgbW9udGgi"
    "LG1vbmV5KEFETS5tYXJrZXQubW9udGgpXSxbIkF2ZyBvcmRlciIsbW9uZXkoYXZnKV1dCiAgICAg"
    "ICAubWFwKChbayx2XSk9PmA8ZGl2IGNsYXNzPSJrcGkiPjxzbWFsbD4ke2t9PC9zbWFsbD48Yj4k"
    "e3Z9PC9iPjwvZGl2PmApLmpvaW4oIiIpfTwvZGl2PjwvZGl2PmApfQogICAke0coImZ1bm5lbCIs"
    "YDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Db252ZXJzaW9uIGZ1bm5lbDwvaDM+PHAgY2xhc3M9InN1"
    "YiI+V2hlcmUgcGVvcGxlIGRyb3Agb2ZmPC9wPgogICAgIDxkaXYgY2xhc3M9ImZ1bm5lbCI+JHso"
    "KCk9Pntjb25zdCBmPUFETS5hbmFseXRpY3MuZnVubmVsLHQ9TWF0aC5tYXgoMSxmLnVzZXJzKTsK"
    "ICAgICAgIHJldHVybiBbWyJTdGFydGVkIGJvdCIsZi51c2VycywxMDBdLFsiRGVwb3NpdGVkIixm"
    "LmRlcCxNYXRoLnJvdW5kKGYuZGVwL3QqMTAwKV0sWyJCb3VnaHQiLGYuYnV5LE1hdGgucm91bmQo"
    "Zi5idXkvdCoxMDApXV0KICAgICAgIC5tYXAoKFtrLHYsd10pPT5gPGRpdj48aSBzdHlsZT0id2lk"
    "dGg6JHtNYXRoLm1heCg0LHcpfSUiPjwvaT48c3BhbiBzdHlsZT0id2hpdGUtc3BhY2U6bm93cmFw"
    "Ij4ke2t9IMK3IDxiPiR7dn08L2I+PC9zcGFuPjwvZGl2PmApLmpvaW4oIiIpO30pKCl9PC9kaXY+"
    "PC9kaXY+YCl9CiAgICR7RygicHJvZml0UmVwIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlBlci1j"
    "b3VudHJ5IHByb2ZpdDwvaDM+CiAgICAgJHtBRE0uYW5hbHl0aWNzLmNvdW50cnkubGVuZ3RoP0FE"
    "TS5hbmFseXRpY3MuY291bnRyeS5tYXAoKFtjLG4sc3VtXSk9PmA8ZGl2IGNsYXNzPSJrdiI+PHNw"
    "YW4+JHtlc2MoYyl9IMK3ICR7bn0gc29sZDwvc3Bhbj48YiBzdHlsZT0iY29sb3I6dmFyKC0tb2sp"
    "Ij4ke21vbmV5KHN1bSl9PC9iPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgICA6JzxwIGNsYXNzPSJz"
    "dWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gc2FsZXMgeWV0LjwvcD4nfQogICAgIDxidXR0b24gY2xh"
    "c3M9ImJ0biBibG9jayBzb2Z0IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4IiBkYXRhLWFkbWluLWFj"
    "dD0iZXhwb3J0Ij5FeHBvcnQgQ1NWPC9idXR0b24+PC9kaXY+YCl9CiAgICR7RygiY2x2IixgPGRp"
    "diBjbGFzcz0iY2FyZCI+PGgzPkN1c3RvbWVyIHZhbHVlPC9oMz4KICAgICAke0FETS51c2Vycy5s"
    "ZW5ndGg/QURNLnVzZXJzLnNsaWNlKDAsNSkubWFwKHU9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+"
    "PGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHtlc2ModS5uYW1lKX08L2I+IMK3ICR7dS5vcmRl"
    "cnN9IG9yZGVyczwvc3Bhbj4KICAgICAgICAgPGI+JHttb25leSh1LnNwZW50KX0gPHNwYW4gY2xh"
    "c3M9InBpbGwgJHt1LnNwZW50PjA/Im9uIjoiaG90In0iPiR7dS5zcGVudD4wPyJhY3RpdmUiOiJu"
    "byBzcGVuZCJ9PC9zcGFuPjwvYj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgOic8cCBjbGFzcz0i"
    "c3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIGN1c3RvbWVycyB5ZXQuPC9wPid9PC9kaXY+YCl9CiAg"
    "ICR7RygiZGlnZXN0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9"
    "InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPkRhaWx5IGRpZ2VzdDxzbWFsbD5FbmQtb2Yt"
    "ZGF5IHN1bW1hcnkgdG8gdGhlIGFkbWluIGNoYXQ8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xh"
    "c3M9InRnJHtGRUFULnNhbGVzRmVlZD8iIG9uIjoiIn0iIGRhdGEtZmVhdD0ic2FsZXNGZWVkIj48"
    "L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlNlbmQgYXQ8L2xhYmVs"
    "PjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMjM6MzAiPjwvZGl2PjwvZGl2PmApfWA7Cn0KCmZ1"
    "bmN0aW9uIGFTZWN1cml0eSgpewogIHJldHVybiBgJHtHKCJyYXRlTGltaXQiLGA8ZGl2IGNsYXNz"
    "PSJjYXJkIj48aDM+UmF0ZSBsaW1pdGluZzwvaDM+PHAgY2xhc3M9InN1YiI+U3RvcHMgb25lIHVz"
    "ZXIgaGFtbWVyaW5nIHRoZSBib3QuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDoxMHB4Ij48bGFiZWw+TWF4IGFjdGlvbnMgcGVyIG1pbnV0ZTwvbGFiZWw+PGlu"
    "cHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIyMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "PjxsYWJlbD5Db29sZG93biBhZnRlciBsaW1pdCAocyk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5w"
    "IiB2YWx1ZT0iNjAiPjwvZGl2PjwvZGl2PmApfQogICAke0coIm11bHRpQWNjIixgPGRpdiBjbGFz"
    "cz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xh"
    "c3M9InQiPk11bHRpLWFjY291bnQgZGV0ZWN0aW9uPHNtYWxsPkZsYWdzIHJlZmVycmFsIGZhcm1p"
    "bmcgcGF0dGVybnM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULm11bHRp"
    "QWNjPyIgb24iOiIifSIgZGF0YS1mZWF0PSJtdWx0aUFjYyI+PC9kaXY+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0ia3YiPjxzcGFuPkZsYWdnZWQgdGhpcyB3ZWVrPC9zcGFuPjxiPjcgYWNjb3VudHM8"
    "L2I+PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJsb2NrIHNvZnQiIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEwcHgiIGRhdGEtYS1nbz0ib3JkZXJzIj5SZXZpZXcgRmxhZ2dlZDwvYnV0dG9uPjwv"
    "ZGl2PmApfQogICAke0coInZlbG9jaXR5IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0i"
    "c3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPlB1cmNoYXNlIHZlbG9j"
    "aXR5IGFsZXJ0czxzbWFsbD5DYXRjaGVzIHN0b2xlbiB0b3AtdXBzPC9zbWFsbD48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC52ZWxvY2l0eT8iIG9uIjoiIn0iIGRhdGEtZmVhdD0idmVs"
    "b2NpdHkiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+QWxlcnQg"
    "YWJvdmUgTiBidXlzIC8gMTAgbWluPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjUi"
    "PjwvZGl2PjwvZGl2PmApfQogICAke0coImFkbWluMmZhIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgz"
    "PkFkbWluIDJGQTwvaDM+PHAgY2xhc3M9InN1YiI+UmVxdWlyZWQgYmVmb3JlIGJhbGFuY2UgZWRp"
    "dHMsIGJyb2FkY2FzdHMgYW5kIHdpcGVzLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5"
    "bGU9Im1hcmdpbi10b3A6MTBweCI+PGxhYmVsPk1ldGhvZDwvbGFiZWw+PHNlbGVjdCBjbGFzcz0i"
    "aW5wIj48b3B0aW9uPlBJTjwvb3B0aW9uPjxvcHRpb24+VE9UUCBhcHA8L29wdGlvbj48L3NlbGVj"
    "dD48L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMiIGRhdGEtZmVhdD0iYWRtaW4yZmEi"
    "PlNldCBVcDwvYnV0dG9uPjwvZGl2PmApfQogICAke0coImF1dG9CYW4iLGA8ZGl2IGNsYXNzPSJj"
    "YXJkIj48aDM+QXV0by1iYW4gcnVsZXM8L2gzPgogICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYg"
    "Y2xhc3M9InQiPkNoYXJnZWJhY2s8c21hbGw+QmFuIGluc3RhbnRseTwvc21hbGw+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5iYW5fY2hhcmdlYmFjayk/IiBvbiI6"
    "IiJ9IiBkYXRhLXNldHRpbmc9ImJhbl9jaGFyZ2ViYWNrIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5GYWtlIFVUUiBzdWJtaXR0ZWQ8c21hbGw+QmFu"
    "IGFmdGVyIDIgYXR0ZW1wdHM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURN"
    "LnN3JiZBRE0uc3cuYmFuX2Zha2VfdXRyKT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0iYmFuX2Zh"
    "a2VfdXRyIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0"
    "Ij5SZXBlYXRlZCBmYWlsZWQgdmVyaWZpY2F0aW9uPHNtYWxsPkJhbiBhZnRlciA1PC9zbWFsbD48"
    "L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmJhbl9mYWlsZWRfdmVy"
    "aWZ5KT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0iYmFuX2ZhaWxlZF92ZXJpZnkiPjwvZGl2Pjwv"
    "ZGl2PjwvZGl2PmApfQogICAke0coImJsYWNrbGlzdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5C"
    "bGFja2xpc3Q8L2gzPjxwIGNsYXNzPSJzdWIiPlVzZXIgSURzLCBwaG9uZSBudW1iZXJzIGFuZCBV"
    "UEkgcmVmZXJlbmNlcy48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjEwcHgiPjxkaXYgc3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6N3B4Ij4KICAgICAgIDxpbnB1"
    "dCBjbGFzcz0iaW5wIiBpZD0iYmwtaW4iIHBsYWNlaG9sZGVyPSJJRCwgcGhvbmUgb3IgVVBJIHJl"
    "ZiI+PGJ1dHRvbiBjbGFzcz0iYnRuIHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9ImJsYmxv"
    "Y2siPkJsb2NrPC9idXR0b24+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFu"
    "PkJsb2NrZWQgZW50cmllczwvc3Bhbj48Yj40MjwvYj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJl"
    "bmNyeXB0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRp"
    "bmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPkVuY3J5cHQgc2Vzc2lvbnMgYXQgcmVzdDxzbWFsbD5B"
    "IGxlYWtlZCBkYXRhYmFzZSBmaWxlIHN0YXlzIHVzZWxlc3M8L3NtYWxsPjwvZGl2PgogICAgIDxk"
    "aXYgY2xhc3M9InRnJHtGRUFULmVuY3J5cHQ/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9ImVuY3J5cHQi"
    "PjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5LZXkgc3RhdHVzPC9zcGFu"
    "PjxiIHN0eWxlPSJjb2xvcjp2YXIoLS1vaykiPkxvYWRlZCBmcm9tIGVudjwvYj48L2Rpdj48L2Rp"
    "dj5gKX1gOwp9CgpmdW5jdGlvbiBhU3RvY2soKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQi"
    "PjxoMz5VcGxvYWQgc3RvY2s8L2gzPjxwIGNsYXNzPSJzdWIiPlBhc3RlIHNlc3Npb25zIG9yIHVw"
    "bG9hZCBhIGZpbGUg4oCUIG9uZSBwZXIgbGluZS48L3A+CjxkaXYgY2xhc3M9ImZpZWxkIiBzdHls"
    "ZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+UGFzdGUgYWNjb3VudHMg4oCUIG9uZSBwZXIgbGlu"
    "ZTwvbGFiZWw+CiAgICAgIDx0ZXh0YXJlYSBjbGFzcz0iaW5wIiBpZD0ic3RwYXN0ZSIgcm93cz0i"
    "NCIKICAgICAgICBwbGFjZWhvbGRlcj0iSW5kaWEgfCArOTEgfCAzNSB8IHNlc3Npb24mIzEwO0tl"
    "bnlhIHwgKzI1NCB8IDc3IHwgc2Vzc2lvbiI+PC90ZXh0YXJlYT48L2Rpdj4KICAgIDxkaXYgY2xh"
    "c3M9ImJ0bnJvdyI+PGJ1dHRvbiBjbGFzcz0iYnRuIGN0YSIgaWQ9InN0c2F2ZSI+SW1wb3J0PC9i"
    "dXR0b24+CiAgICAgIDxsYWJlbCBjbGFzcz0iYnRuIHNvZnQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7"
    "YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Y3Vyc29yOnBvaW50ZXIi"
    "PgogICAgICAgIFVwbG9hZCAudHh0PGlucHV0IHR5cGU9ImZpbGUiIGlkPSJzdGZpbGUiIGFjY2Vw"
    "dD0iLnR4dCwuY3N2IiBoaWRkZW4+PC9sYWJlbD48L2Rpdj4KICAgIDxwIGNsYXNzPSJzdWIiIGlk"
    "PSJzdG1zZyIgc3R5bGU9Im1hcmdpbjo4cHggMCAwO2ZvbnQtc2l6ZTouNzJyZW0iPjwvcD4KICAg"
    "ICR7RygiYnVsa1VwbG9hZCIsYDxkaXYgY2xhc3M9InN0ZXBzIiBzdHlsZT0ibWFyZ2luLXRvcDox"
    "MnB4Ij4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCBvbiI+PGk+MTwvaT4ke0FETS5hbmFseXRpY3Mu"
    "c3RvY2tfdG90YWx9IGF2YWlsYWJsZSBub3c8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RlcCBv"
    "biI+PGk+MjwvaT4ke0FETS5hbmFseXRpY3Muc29sZF90b3RhbH0gc29sZCBzbyBmYXI8L2Rpdj4K"
    "ICAgICAgPGRpdiBjbGFzcz0ic3RlcCI+PGk+MzwvaT5BZGQgbW9yZSBmcm9tIHRoZSBib3Q6IEFk"
    "bWluIOKGkiBTdG9jazwvZGl2PjwvZGl2PmApfQogICAgJHtHKCJkdXBEZXRlY3QiLGA8cCBjbGFz"
    "cz0ic3ViIiBzdHlsZT0ibWFyZ2luOjEwcHggMCAwO2ZvbnQtc2l6ZTouN3JlbSI+RHVwbGljYXRl"
    "IHBob25lcyBhbmQgc2Vzc2lvbnMgYXJlIHJlamVjdGVkIGF1dG9tYXRpY2FsbHkuPC9wPmApfQog"
    "ICAgJHtHKCJoZWFsdGhDaGVjayIsYDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPlZh"
    "bGlkYXRlIGJlZm9yZSBzZWxsaW5nPHNtYWxsPkNoZWNrIHRoZSBzZXNzaW9uIGlzIGFsaXZlIGF0"
    "IHB1cmNoYXNlIHRpbWU8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULmhl"
    "YWx0aENoZWNrPyIgb24iOiIifSIgZGF0YS1mZWF0PSJoZWFsdGhDaGVjayI+PC9kaXY+PC9kaXY+"
    "YCl9PC9kaXY+CiAgICR7RygibG93U3RvY2siLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+TG93LXN0"
    "b2NrIGFsZXJ0czwvaDM+PHAgY2xhc3M9InN1YiI+UGVyLWNvdW50cnkgdGhyZXNob2xkIOKAlCBw"
    "aW5nIHdoZW4gaXQgZHJvcHMgYmVsb3cuPC9wPgogICAgICR7KEFETS5zdG9jay5sZW5ndGg/QURN"
    "LnN0b2NrOlNUT0NLKS5zbGljZSgwLDYpLm1hcChzPT5gPGRpdiBjbGFzcz0ia3YiPjxzcGFuPiR7"
    "cy5mfSAke2VzYyhzLmMpfSDCtyAke3MucXR5fHwwfSBsZWZ0PC9zcGFuPgogICAgICAgPGI+PGlu"
    "cHV0IGNsYXNzPSJpbnAiIHN0eWxlPSJoZWlnaHQ6MzBweDt3aWR0aDo2NHB4O3RleHQtYWxpZ246"
    "Y2VudGVyO2ZvbnQtc2l6ZTouNzRyZW0iIHZhbHVlPSIke3MubG93fHwxMH0iPjwvYj48L2Rpdj5g"
    "KS5qb2luKCIiKQogICAgICAgfHwnPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBz"
    "dG9jayB5ZXQuPC9wPid9PC9kaXY+YCl9CiAgICR7Rygic2VsbEhvdXJzIixgPGRpdiBjbGFzcz0i"
    "Y2FyZCI+PGgzPlNlbGxpbmcgaG91cnM8L2gzPjxwIGNsYXNzPSJzdWIiPkNvdW50cmllcyBoaWRl"
    "IHRoZW1zZWx2ZXMgb3V0c2lkZSB0aGUgd2luZG93LjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVs"
    "ZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+T3BlbjwvbGFiZWw+PGlucHV0IGNsYXNz"
    "PSJpbnAiIHZhbHVlPSIwOTowMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJl"
    "bD5DbG9zZTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIwMjowMCI+PC9kaXY+PC9k"
    "aXY+YCl9CiAgICR7RygiZHluUHJpY2UiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+RHluYW1pYyBw"
    "cmljaW5nPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+"
    "PGRpdiBjbGFzcz0idCI+UmFpc2UgcHJpY2Ugd2hlbiBzdG9jayBpcyBsb3c8c21hbGw+KzEwJSB1"
    "bmRlciAyMCB1bml0czwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGcke0ZFQVQuZHlu"
    "UHJpY2U/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9ImR5blByaWNlIj48L2Rpdj48L2Rpdj4KICAgICA8"
    "ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5OaWdodCBkaXNjb3VudDxzbWFsbD7iiJI1"
    "JSBiZXR3ZWVuIDAwOjAwIGFuZCAwNjowMDwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "dGckeyhBRE0uc3cmJkFETS5zdy5uaWdodF9kaXNjb3VudCk/IiBvbiI6IiJ9IiBkYXRhLXNldHRp"
    "bmc9Im5pZ2h0X2Rpc2NvdW50Ij48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJidWxrRGlz"
    "YyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5CdWxrIGRpc2NvdW50czwvaDM+CiAgICAgPHAgY2xh"
    "c3M9InN1YiI+UGVyY2VudCBvZmYgd2hlbiB0aGUgYnV5ZXIgdGFrZXMgc2V2ZXJhbCBhdCBvbmNl"
    "LjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+PGxh"
    "YmVsPjUgb3IgbW9yZSAoJSk8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEt"
    "cHJpY2U9ImJ1bGtfNSIgdmFsdWU9IiR7QURNLnByaWNpbmc/QURNLnByaWNpbmcuYnVsa181OjV9"
    "Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPjEwIG9yIG1vcmUgKCUpPC9s"
    "YWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXByaWNlPSJidWxrXzEwIiB2YWx1"
    "ZT0iJHtBRE0ucHJpY2luZz9BRE0ucHJpY2luZy5idWxrXzEwOjEwfSI+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0iZmllbGQiPjxsYWJlbD4yNSBvciBtb3JlICglKTwvbGFiZWw+CiAgICAgICA8aW5w"
    "dXQgY2xhc3M9ImlucCIgZGF0YS1wcmljZT0iYnVsa18yNSIgdmFsdWU9IiR7QURNLnByaWNpbmc/"
    "QURNLnByaWNpbmcuYnVsa18yNToxNX0iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48"
    "bGFiZWw+RGVmYXVsdCBwcmljZSBmb3IgbmV3IHN0b2NrPC9sYWJlbD4KICAgICAgIDxpbnB1dCBj"
    "bGFzcz0iaW5wIiBkYXRhLXByaWNlPSJkZWZhdWx0X3ByaWNlIiB2YWx1ZT0iJHtBRE0ucHJpY2lu"
    "Zz9BRE0ucHJpY2luZy5kZWZhdWx0X3ByaWNlOjM1fSI+PC9kaXY+CiAgICAgPC9kaXY+YCl9CiAg"
    "ICR7RygidmFyaWFudHMiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+VmFyaWFudHM8L2gzPjxwIGNs"
    "YXNzPSJzdWIiPlByaWNlZCBzZXBhcmF0ZWx5IG9uIGV2ZXJ5IGNvdW50cnkuPC9wPgogICAgIDxk"
    "aXYgY2xhc3M9Imt2Ij48c3Bhbj4yRkEgZW5hYmxlZDwvc3Bhbj48Yj4r4oK5NTwvYj48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+V2l0aCBtYWlsPC9zcGFuPjxiPivigrk4PC9iPjwv"
    "ZGl2PgogICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5TcGFtLWZyZWUgZ3VhcmFudGVlPC9zcGFu"
    "PjxiPivigrk0PC9iPjwvZGl2PjwvZGl2PmApfQogICA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5JbnZl"
    "bnRvcnk8L2gyPjxhIGNsYXNzPSJzdWIiPnRhcCBhIHByaWNlIHRvIGNoYW5nZSBpdDwvYT48L2Rp"
    "dj4KICAgJHtTVE9DSy5tYXAocz0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNw"
    "bGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8ZGl2IGNsYXNzPSJm"
    "bGFnIj4ke3MuZn08L2Rpdj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxi"
    "IHN0eWxlPSJmb250LXNpemU6LjgzcmVtIj4ke3MuY308L2I+PGJyPgogICAgICAgPHNtYWxsIHN0"
    "eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjlyZW0iPiR7cy5xdHl9IGF2YWlsYWJs"
    "ZTwvc21hbGw+PC9kaXY+CiAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtY3ByaWNlPSIke2Vz"
    "YyhzLmMpfSIgdmFsdWU9IiR7cy5wcmljZX0iIHN0eWxlPSJoZWlnaHQ6MzJweDt3aWR0aDo3OHB4"
    "O3RleHQtYWxpZ246Y2VudGVyO2ZvbnQtc2l6ZTouNzZyZW0iPgogICAgIAogICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biB4cyBiYWQiIGRhdGEtbWlzYz0iY2xlYXJzdG9jayIgZGF0YS1taXNjLXZhbHVl"
    "PSIke2VzYyhzLmMpfSI+Q2xlYXI8L2J1dHRvbj48L2Rpdj5gKS5qb2luKCIiKX1gOwp9CmZ1bmN0"
    "aW9uIGFPcmRlcnMoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9InNlYyI+PGgyPlJlY2VudCBPcmRl"
    "cnM8L2gyPjxhIGRhdGEtYWRtaW4tYWN0PSJleHBvcnQiPkV4cG9ydDwvYT48L2Rpdj4KICAgJHtB"
    "RE0ub3JkZXJzLmxlbmd0aD9BRE0ub3JkZXJzLm1hcChvPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdo"
    "dCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAg"
    "IDxkaXYgY2xhc3M9ImZsYWciPiR7by5mfTwvZGl2PjxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lk"
    "dGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODJyZW0iPiR7ZXNjKG8uaWQpfTwvYj48YnI+CiAg"
    "ICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjlyZW0iPiR7ZXNj"
    "KG8udXNlcil9IMK3ICR7bW9uZXkoby5wcmljZSl9IMK3ICR7ZXNjKG8ud2hlbil9PC9zbWFsbD48"
    "L2Rpdj4KICAgICA8c3BhbiBjbGFzcz0icGlsbCAke28uc3Q9PT0iRGVsaXZlcmVkIj8ib24iOiJv"
    "ZmYifSI+JHtlc2Moby5zdCl9PC9zcGFuPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgOic8ZGl2IGNs"
    "YXNzPSJjYXJkIHRpZ2h0Ij48cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIG9yZGVy"
    "cyB5ZXQuPC9wPjwvZGl2Pid9CiAgICR7RygiZGlzcHV0ZSIsYDxkaXYgY2xhc3M9InNlYyI+PGgy"
    "PkRpc3B1dGVzPC9oMj48L2Rpdj4KICAgICAke0FETS5kaXNwdXRlcy5sZW5ndGg/QURNLmRpc3B1"
    "dGVzLm1hcChkPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDth"
    "bGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4k"
    "e2ljKCJzaGllbGQiKX08L2Rpdj4KICAgICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0"
    "aDowIj48YiBzdHlsZT0iZm9udC1zaXplOi44cmVtIj4ke2VzYyhkLmlkKX0gwrcgJHtlc2MoZC51"
    "c2VyKX08L2I+PGJyPjxzbWFsbCBzdHlsZT0iY29sb3I6dmFyKC0tdHgyKTtmb250LXNpemU6LjY4"
    "cmVtIj4ke2VzYyhkLnJlYXNvbil9PC9zbWFsbD48L2Rpdj4KICAgICAgICAgPHNwYW4gY2xhc3M9"
    "InBpbGwgJHtkLnN0YXR1cz09PSJPcGVuIj8iaG90Ijoib24ifSI+JHtlc2MoZC5zdGF0dXMpfTwv"
    "c3Bhbj48L2Rpdj5gKS5qb2luKCIiKQogICAgICAgOic8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0Ij48"
    "cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIGRpc3B1dGVzLjwvcD48L2Rpdj4nfWAp"
    "fQogICAke0coIndhcnJhbnR5IixgPGRpdiBjbGFzcz0ic2VjIj48aDI+UmVwbGFjZW1lbnQgY2xh"
    "aW1zPC9oMj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxh"
    "eTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgICA8ZGl2IGNsYXNzPSJm"
    "bGFnIj4ke2ljKCJzaGllbGQiKX08L2Rpdj4KICAgICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4t"
    "d2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouOHJlbSI+MiBjbGFpbXMgaW5zaWRlIHdhcnJh"
    "bnR5PC9iPjxicj4KICAgICAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQt"
    "c2l6ZTouNjhyZW0iPkF1dG8tcmVwbGFjZWQgZnJvbSBzdG9jazwvc21hbGw+PC9kaXY+CiAgICAg"
    "ICA8L2Rpdj5gKX0KICAgCiAgICR7RygicmV2aWV3cyIsYDxkaXYgY2xhc3M9InNlYyI+PGgyPlJl"
    "dmlld3M8L2gyPjwvZGl2PgogICAgICR7KEFETS5yZXZpZXdzLmxlbmd0aD9BRE0ucmV2aWV3cy5t"
    "YXAocj0+W3IudXNlcixyLmMsci5zdGFycyxyLnRleHRdKTpbXSkKICAgICAgIC5tYXAoKFt1LGMs"
    "cix0XSk9PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMo"
    "InN0YXIiKX08L2Rpdj4KICAgICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48"
    "YiBzdHlsZT0iZm9udC1zaXplOi44cmVtIj4ke3V9IMK3ICR7Y30gwrcgJHtyfTwvYj48YnI+CiAg"
    "ICAgICAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjhyZW0i"
    "PiR7dH08L3NtYWxsPjwvZGl2PgogICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIg"
    "ZGF0YS1yZXY9IiR7ci5pZH0iPkhpZGU8L2J1dHRvbj48L2Rpdj5gKS5qb2luKCIiKX1gKX1gOwp9"
    "CmZ1bmN0aW9uIGFVc2VycygpewogIHJldHVybiBgPGRpdiBjbGFzcz0iZmllbGQiPjxpbnB1dCBj"
    "bGFzcz0iaW5wIiBwbGFjZWhvbGRlcj0iU2VhcmNoIGJ5IElEIG9yIHVzZXJuYW1lIj48L2Rpdj4K"
    "ICAgJHtBRE0udXNlcnMubGVuZ3RoP0FETS51c2Vycy5tYXAodT0+YDxkaXYgY2xhc3M9ImNhcmQg"
    "dGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4K"
    "ICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygidXNlciIpfTwvZGl2PgogICAgICA8ZGl2IHN0"
    "eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjgycmVtIj4ke2Vz"
    "Yyh1Lm5hbWUpfTwvYj48YnI+CiAgICAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIp"
    "O2ZvbnQtc2l6ZTouNjlyZW0iPiR7bW9uZXkodS5zcGVudCl9IHNwZW50IMK3ICR7dS5vcmRlcnN9"
    "IG9yZGVycyDCtyAke21vbmV5KHUuYmFsYW5jZSl9IGJhbGFuY2Uke3UuYmFubmVkPyIgwrcgYmFu"
    "bmVkIjoiIn08L3NtYWxsPjwvZGl2PgogICAgICA8c3BhbiBjbGFzcz0icGlsbCAke3UuYmFubmVk"
    "PyJvZmYiOiJvbiJ9Ij4ke2VzYyh1LnRpZXIpfTwvc3Bhbj48L2Rpdj5gKS5qb2luKCIiKQogICAg"
    "IDonPGRpdiBjbGFzcz0iY2FyZCB0aWdodCI+PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjow"
    "Ij5ObyB1c2VycyB5ZXQuPC9wPjwvZGl2Pid9CiAgICR7RygiYWRtaW5Sb2xlcyIsYDxkaXYgY2xh"
    "c3M9InNlYyI+PGgyPkFkbWluIHJvbGVzPC9oMj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJjYXJk"
    "Ij4KICAgICAgICR7QURNLmFkbWlucy5sZW5ndGg/QURNLmFkbWlucy5tYXAoYT0+YDxkaXYgY2xh"
    "c3M9Imt2Ij48c3Bhbj48YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4ke2VzYyhhLmxhYmVsKX08"
    "L2I+IMK3ICR7ZXNjKGEucGVybXMpfTwvc3Bhbj48Yj48c3BhbiBjbGFzcz0icGlsbCBvbiI+JHtl"
    "c2MoYS5yb2xlKX08L3NwYW4+PC9iPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgICAgIDonPHAgY2xh"
    "c3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5Pbmx5IHlvdSBzbyBmYXIuPC9wPid9CiAgICAgICA8"
    "ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+PGRpdiBzdHlsZT0iZGlz"
    "cGxheTpmbGV4O2dhcDo3cHgiPgogICAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgcGxhY2Vob2xk"
    "ZXI9IkB1c2VybmFtZSI+PHNlbGVjdCBjbGFzcz0iaW5wIiBzdHlsZT0id2lkdGg6YXV0byI+PG9w"
    "dGlvbj5NYW5hZ2VyPC9vcHRpb24+PG9wdGlvbj5TdXBwb3J0PC9vcHRpb24+PC9zZWxlY3Q+CiAg"
    "ICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJh"
    "ZG1pbmFkZCI+QWRkPC9idXR0b24+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiYnV5ZXJC"
    "YWRnZSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5WZXJpZmllZCBidXllciBiYWRnZTwvaDM+CiAg"
    "ICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPk9yZGVy"
    "cyByZXF1aXJlZDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI1Ij48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+VmVyaWZpZWQgdXNlcnM8L3NwYW4+PGI+ODQ8L2I+PC9k"
    "aXY+PC9kaXY+YCl9CiAgICR7RygibXVsdGlBY2MiLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBz"
    "dHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNw"
    "YW4gY2xhc3M9InBpbGwgaG90Ij43IGZsYWdnZWQ8L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxl"
    "eDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+UG9zc2li"
    "bGUgbXVsdGktYWNjb3VudHMgdGhpcyB3ZWVrPC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IHhzIHNvZnQiIGRhdGEtYS1nbz0ic2VjdXJpdHkiPlJldmlldzwvYnV0dG9uPjwvZGl2PmApfWA7"
    "Cn0KZnVuY3Rpb24gYVNldHRpbmdzKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+"
    "U3RvcmU8L2gzPjxwIGNsYXNzPSJzdWIiPkV2ZXJ5dGhpbmcgbGl2ZXMgaW4gdGhlIGRhdGFiYXNl"
    "IOKAlCBubyBjb2RlIGVkaXRzLCBubyByZXN0YXJ0LjwvcD4KICAgIDxkaXYgY2xhc3M9ImZpZWxk"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+U3RvcmUgbmFtZTwvbGFiZWw+CiAgICAg"
    "IDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0ic3RvcmVfbmFtZSIgdmFsdWU9IiR7ZXNjKENN"
    "Uy5zdG9yZU5hbWV8fENGRy5uYW1lKX0iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iZmllbGQiPjxs"
    "YWJlbD5UYWdsaW5lPC9sYWJlbD4KICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJz"
    "dG9yZV90YWdsaW5lIiB2YWx1ZT0iJHtlc2MoQ01TLnN0b3JlVGFnfHxDRkcudGFnKX0iPjwvZGl2"
    "PgogICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5UYWdsaW5lPC9sYWJlbD48aW5wdXQgY2xh"
    "c3M9ImlucCIgdmFsdWU9IiR7Q0ZHLnRhZ30iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "PjxsYWJlbD5TdXBwb3J0IGNvbnRhY3Q8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiBwbGFjZWhv"
    "bGRlcj0iQHlvdXJfc3VwcG9ydCI+PC9kaXY+CiAgICA8L2Rpdj4KICAgPGRpdiBjbGFzcz0iY2Fy"
    "ZCI+PGgzPlRvZ2dsZXM8L2gzPgogICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+"
    "Rm9yY2Ugam9pbjxzbWFsbD5SZXF1aXJlIGNoYW5uZWwgbWVtYmVyc2hpcDwvc21hbGw+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5mb3JjZV9zdWIpPyIgb24iOiIi"
    "fSIgZGF0YS1zZXR0aW5nPSJmb3JjZV9zdWIiPjwvZGl2PjwvZGl2PgogICAgPGRpdiBjbGFzcz0i"
    "c3dhcCI+PGRpdiBjbGFzcz0idCI+TWFpbnRlbmFuY2UgbW9kZTxzbWFsbD5GcmVlemUgdGhlIHN0"
    "b3JlPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3Lm1h"
    "aW50ZW5hbmNlKT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0ibWFpbnRlbmFuY2UiPjwvZGl2Pjwv"
    "ZGl2PgogICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+QXV0byByZWZ1bmRzPHNt"
    "YWxsPlJlZnVuZCBmYWlsZWQgbG9naW5zPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0"
    "ZyR7RkVBVC5hdXRvUmVmdW5kPyIgb24iOiIifSIgZGF0YS1mZWF0PSJhdXRvUmVmdW5kIj48L2Rp"
    "dj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPlJlZmVycmFsIHBy"
    "b2dyYW08c21hbGw+MSUgb2YgaW52aXRlZCBkZXBvc2l0czwvc21hbGw+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0idGcke0ZFQVQucmVmRGFzaD8iIG9uIjoiIn0iIGRhdGEtZmVhdD0icmVmRGFzaCI+"
    "PC9kaXY+PC9kaXY+PC9kaXY+CiAgICR7RygiaTE4biIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5M"
    "YW5ndWFnZXM8L2gzPjxwIGNsYXNzPSJzdWIiPlVzZXJzIHBpY2sgdGhlaXIgb3duOyBldmVyeSBz"
    "dHJpbmcgaXMgdHJhbnNsYXRlZC48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5E"
    "ZWZhdWx0IGxhbmd1YWdlPC9sYWJlbD4KICAgICAgPHNlbGVjdCBjbGFzcz0iaW5wIiBkYXRhLXNl"
    "dHNlbD0ibGFuZ19kZWZhdWx0Ij4KICAgICAgICA8b3B0aW9uIHZhbHVlPSJlbiIgJHsoQURNLnNl"
    "dHRpbmdzJiZBRE0uc2V0dGluZ3MubGFuZz09PSJlbiIpPyJzZWxlY3RlZCI6IiJ9PkVuZ2xpc2g8"
    "L29wdGlvbj4KICAgICAgICA8b3B0aW9uIHZhbHVlPSJoaSIgJHsoQURNLnNldHRpbmdzJiZBRE0u"
    "c2V0dGluZ3MubGFuZz09PSJoaSIpPyJzZWxlY3RlZCI6IiJ9PuCkueCkv+CkqOCljeCkpuClgDwv"
    "b3B0aW9uPgogICAgICA8L3NlbGVjdD48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJzbGFzaENtZHMi"
    "LGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+U2xhc2ggY29tbWFuZHM8L2gzPjxwIGNsYXNzPSJzdWIi"
    "PlJlZ2lzdGVyZWQgaW4gVGVsZWdyYW0ncyBjb21tYW5kIG1lbnUuPC9wPgogICAgICR7W1siL3N0"
    "YXJ0IiwiT3BlbiB0aGUgc3RvcmUiXSxbIi9idXkiLCJKdW1wIHRvIHN0b2NrIl0sWyIvYmFsYW5j"
    "ZSIsIlNob3cgd2FsbGV0Il0sWyIvb3JkZXJzIiwiTXkgb3JkZXJzIl0sWyIvc3VwcG9ydCIsIkNv"
    "bnRhY3Qgc3VwcG9ydCJdXQogICAgICAgLm1hcCgoW2MsZF0pPT5gPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7Y308L2I+PC9zcGFuPjxiIHN0eWxlPSJm"
    "b250LXdlaWdodDo1MDA7Zm9udC1zaXplOi43NHJlbSI+JHtkfTwvYj48L2Rpdj5gKS5qb2luKCIi"
    "KX0KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2sgc29mdCIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTBweCIgZGF0YS1hZG1pbi1hY3Q9InN5bmMiPlN5bmMgQ29tbWFuZHM8L2J1dHRvbj48L2Rpdj5g"
    "KX0KICAgJHtHKCJhdXRvQmFja3VwIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkF1dG8tYmFja3Vw"
    "PC9oMz48cCBjbGFzcz0ic3ViIj5EYXRhYmFzZSB0byBhIHByaXZhdGUgY2hhbm5lbCBvbiBhIHNj"
    "aGVkdWxlLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4"
    "Ij48bGFiZWw+RXZlcnkgKGhvdXJzKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI2"
    "Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+TGFzdCBiYWNrdXA8L3NwYW4+PGI+"
    "NDIgbWluIGFnbzwvYj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJn"
    "aW4tdG9wOjEwcHgiPjxidXR0b24gY2xhc3M9ImJ0biIgZGF0YS1hZG1pbi1hY3Q9ImJhY2t1cCI+"
    "QmFjayBVcCBOb3c8L2J1dHRvbj4KICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCIgZGF0"
    "YS1hZG1pbi1hY3Q9InJlc3RvcmUiPlJlc3RvcmU8L2J1dHRvbj48L2Rpdj48L2Rpdj5gKX0KICAg"
    "JHtHKCJhdWRpdExvZyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5BdWRpdCBsb2c8L2gzPjxwIGNs"
    "YXNzPSJzdWIiPkV2ZXJ5IGFkbWluIGFjdGlvbiwgd2l0aCB1bmRvLjwvcD4KICAgICAkeygoQURN"
    "LmF1ZGl0JiZBRE0uYXVkaXQubGVuZ3RoKT9BRE0uYXVkaXQubWFwKGE9PlthLndobywoYS53aGF0"
    "KyIgIithLmRldGFpbCkudHJpbSgpLGEud2hlbl0pOltdKQogICAgICAgLm1hcCgoW3UsYSx0XSk9"
    "PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+PGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+JHt1fTwv"
    "Yj4gJHthfTwvc3Bhbj4KICAgICAgICAgPGI+JHt0fSA8L2I+PC9kaXY+YCkuam9pbigiIil9PC9k"
    "aXY+YCl9CiAgICR7Rygic2FuZGJveCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3"
    "YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5TYW5kYm94IC8gdGVzdCBt"
    "b2RlPHNtYWxsPkZha2UgbW9uZXksIG5vIHJlYWwgZGVsaXZlcnk8L3NtYWxsPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuc2FuZGJveCk/IiBvbiI6IiJ9IiBkYXRh"
    "LXNldHRpbmc9InNhbmRib3giPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImVuY3J5cHQi"
    "LGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1z"
    "OmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwgb24iPjxpIGNsYXNzPSJk"
    "b3QiPjwvaT5FbmNyeXB0ZWQ8L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0"
    "aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+U2Vzc2lvbnMgYXJlIGVuY3J5"
    "cHRlZCBhdCByZXN0PC9kaXY+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEt"
    "YS1nbz0ic2VjdXJpdHkiPktleXM8L2J1dHRvbj48L2Rpdj5gKX0KICAgPGRpdiBjbGFzcz0iY2Fy"
    "ZCI+PGgzPkRhbmdlciB6b25lPC9oMz48cCBjbGFzcz0ic3ViIj5JcnJldmVyc2libGUuIEFzayB0"
    "d2ljZS48L3A+CiAgICA8ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgi"
    "PjxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWFkbWluLWFjdD0iYmFja3VwIj5CYWNr"
    "dXA8L2J1dHRvbj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBiYWQgaG9sZCIgZGF0YS1kb25lPSJE"
    "YXRhYmFzZSByZXNldCAoZGVtbykiPjxzcGFuIGNsYXNzPSJmaWxsIj48L3NwYW4+SG9sZCB0byBS"
    "ZXNldCBEQjwvYnV0dG9uPjwvZGl2PjwvZGl2PmA7Cn0KCmZ1bmN0aW9uIGJpbmRBZG1pbigpewog"
    "IGJpbmRDb21tb24oKTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1mZWF0XSIp"
    "LmZvckVhY2godD0+dC5vbmNsaWNrPSgpPT57CiAgICBjb25zdCBrPXQuZGF0YXNldC5mZWF0O0ZF"
    "QVRba109IUZFQVRba107dC5jbGFzc0xpc3QudG9nZ2xlKCJvbiIsRkVBVFtrXSk7CiAgICBwdXNo"
    "KCIvYXBpL2FkbWluL2ZlYXR1cmUiLHtrZXk6ayxvbjpGRUFUW2tdfSk7CiAgICBjb25zdCBjPWRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJmY291bnQiKTsKICAgIGlmKGMpYy50ZXh0Q29udGVudD1P"
    "YmplY3QudmFsdWVzKEZFQVQpLmZpbHRlcihCb29sZWFuKS5sZW5ndGgrIiBvZiAiK09iamVjdC5r"
    "ZXlzKEZFQVQpLmxlbmd0aDsKICAgIGFwcGx5RmVhdHVyZXMoKTsKICAgIGNvbnN0IGNhcmQ9dC5j"
    "bG9zZXN0KCIuZmNhcmQiKSwgbmFtZUVsPWNhcmQ/Y2FyZC5xdWVyeVNlbGVjdG9yKCJiIik6bnVs"
    "bDsKICAgIGNvbnN0IGxhYmVsPW5hbWVFbD9uYW1lRWwudGV4dENvbnRlbnQ6ay5yZXBsYWNlKC8o"
    "W0EtWl0pL2csIiAkMSIpLnRvTG93ZXJDYXNlKCk7CiAgICB0b2FzdChsYWJlbCsoRkVBVFtrXT8i"
    "IGVuYWJsZWQiOiIgaGlkZGVuIikpO30pOwogIGNvbnN0IGZxPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJmcSIpOwogIGlmKGZxKWZxLm9uaW5wdXQ9KCk9Pntjb25zdCBxPWZxLnZhbHVlLnRyaW0o"
    "KS50b0xvd2VyQ2FzZSgpOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiLmZyb3ciKS5m"
    "b3JFYWNoKHI9PnIuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZSIsISFxJiYhci5kYXRhc2V0LmYuaW5j"
    "bHVkZXMocSkpKTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5mZ3JwIikuZm9yRWFj"
    "aChnPT5nLmNsYXNzTGlzdC50b2dnbGUoImhpZGUiLCFnLnF1ZXJ5U2VsZWN0b3IoIi5mcm93Om5v"
    "dCguaGlkZSkiKSkpO307CiAgY29uc3Qgc2V0QWxsPXY9PntPYmplY3Qua2V5cyhGRUFUKS5mb3JF"
    "YWNoKGs9PkZFQVRba109dik7YXBwbHlGZWF0dXJlcygpO3JlbmRlckFkbWluKCk7dG9hc3Qodj8i"
    "QWxsIGZlYXR1cmVzIGVuYWJsZWQiOiJBbGwgZmVhdHVyZXMgaGlkZGVuIik7fTsKICBjb25zdCBm"
    "YT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZmFsbCIpLGZuPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJmbm9uZSIpLGZyPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJmcmVzZXQiKTsKICBpZihm"
    "YSlmYS5vbmNsaWNrPSgpPT5zZXRBbGwodHJ1ZSk7CiAgaWYoZm4pZm4ub25jbGljaz0oKT0+c2V0"
    "QWxsKGZhbHNlKTsKICBpZihmcilmci5vbmNsaWNrPSgpPT57T2JqZWN0LmFzc2lnbihGRUFULEZF"
    "QVQwKTthcHBseUZlYXR1cmVzKCk7cmVuZGVyQWRtaW4oKTt0b2FzdCgiRmVhdHVyZXMgcmVzZXQi"
    "KTt9OwogIGNvbnN0IHN0bXNnPW09Pntjb25zdCBlPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJz"
    "dG1zZyIpO2lmKGUpZS50ZXh0Q29udGVudD1tO307CiAgYXN5bmMgZnVuY3Rpb24gaW1wb3J0U3Rv"
    "Y2sodGV4dCl7CiAgICBpZighdGV4dC50cmltKCkpe3RvYXN0KCJQYXN0ZSBzb21lIGxpbmVzIGZp"
    "cnN0Iik7cmV0dXJuO30KICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJu"
    "O30KICAgIHN0bXNnKCJJbXBvcnRpbmfigKYiKTsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIv"
    "YXBpL2FkbWluL3N0b2NrIix7dGV4dDp0ZXh0fSk7CiAgICBpZighcil7c3Rtc2coIiIpO3JldHVy"
    "bjt9CiAgICBjb25zdCBub3RlPXIuYWRkZWQrIiBpbXBvcnRlZCIrKHIuc2tpcHBlZCYmci5za2lw"
    "cGVkLmxlbmd0aD8oIiDCtyAiK3Iuc2tpcHBlZC5sZW5ndGgrIiBza2lwcGVkIik6IiIpOwogICAg"
    "dG9hc3Qoci5hZGRlZCsiIGFjY291bnRzIGFkZGVkIik7CiAgICBjb25zdCBib3g9ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoInN0cGFzdGUiKTtpZihib3gpYm94LnZhbHVlPSIiOwogICAgYXdhaXQg"
    "cmVmcmVzaCgpOwogICAgc3Rtc2cobm90ZSk7CiAgfQogIGNvbnN0IHN0c2F2ZT1kb2N1bWVudC5n"
    "ZXRFbGVtZW50QnlJZCgic3RzYXZlIik7CiAgaWYoc3RzYXZlKXN0c2F2ZS5vbmNsaWNrPSgpPT5p"
    "bXBvcnRTdG9jaygoZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0cGFzdGUiKXx8e30pLnZhbHVl"
    "fHwiIik7CiAgY29uc3Qgc3RmaWxlPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdGZpbGUiKTsK"
    "ICBpZihzdGZpbGUpc3RmaWxlLm9uY2hhbmdlPSgpPT57CiAgICBjb25zdCBmPXN0ZmlsZS5maWxl"
    "cyYmc3RmaWxlLmZpbGVzWzBdO2lmKCFmKXJldHVybjsKICAgIGNvbnN0IHJkPW5ldyBGaWxlUmVh"
    "ZGVyKCk7CiAgICByZC5vbmxvYWQ9KCk9Pntjb25zdCBib3g9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5"
    "SWQoInN0cGFzdGUiKTsKICAgICAgaWYoYm94KWJveC52YWx1ZT1yZC5yZXN1bHQ7c3Rtc2coZi5u"
    "YW1lKyIgbG9hZGVkIOKAlCBwcmVzcyBJbXBvcnQiKTt9OwogICAgcmQucmVhZEFzVGV4dChmKTsK"
    "ICB9OwogIGNvbnN0IGJsPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJibGJsb2NrIik7CiAgaWYo"
    "YmwpYmwub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBpPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJibC1pbiIpOwogICAgY29uc3Qgdj1pPyhpLnZhbHVlfHwiIikudHJpbSgpOiIiOwogICAg"
    "aWYoIXYpe3RvYXN0KCJUeXBlIGFuIGlkLCBwaG9uZSBvciBVUEkgcmVmZXJlbmNlIik7aWYoaSlp"
    "LmZvY3VzKCk7cmV0dXJuO30KICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0"
    "dXJuO30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2JsYWNrbGlzdCIse2Fj"
    "dGlvbjoiYWRkIix2YWx1ZTp2fSk7CiAgICBpZighcilyZXR1cm47CiAgICBpLnZhbHVlPSIiO3Rv"
    "YXN0KCJCbG9ja2VkICIrdik7YXdhaXQgcmVmcmVzaCgpOwogIH07CiAgZG9jdW1lbnQucXVlcnlT"
    "ZWxlY3RvckFsbCgiW2RhdGEtYmwtZGVsXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9"
    "PnsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2JsYWNrbGlzdCIse2FjdGlv"
    "bjoiZGVsIixpZDorYi5kYXRhc2V0LmJsRGVsfSk7CiAgICBpZihyKXt0b2FzdCgiVW5ibG9ja2Vk"
    "Iik7YXdhaXQgcmVmcmVzaCgpO30KICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJb"
    "ZGF0YS1taXNjXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKCFMSVZF"
    "KXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgIGNvbnN0IHdoYXQ9Yi5kYXRhc2V0"
    "Lm1pc2MsIHZhbD1iLmRhdGFzZXQubWlzY1ZhbHVlfHwiIjsKICAgIGNvbnN0IGxibD1iLnRleHRD"
    "b250ZW50O2IuZGlzYWJsZWQ9dHJ1ZTtiLnRleHRDb250ZW50PSLigKYiOwogICAgY29uc3Qgcj1h"
    "d2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vbWlzYyIse3doYXQ6d2hhdCx2YWx1ZTp2YWx9KTsKICAg"
    "IGIuZGlzYWJsZWQ9ZmFsc2U7Yi50ZXh0Q29udGVudD1sYmw7CiAgICBpZighcilyZXR1cm47CiAg"
    "ICBpZihyLmxpbmVzKXNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZTox"
    "cmVtIj5DaGFubmVsIGNoZWNrPC9oMz4KICAgICAgPHAgY2xhc3M9InN1YiIgc3R5bGU9IndoaXRl"
    "LXNwYWNlOnByZS1saW5lIj4ke2VzYyhyLmxpbmVzLmpvaW4oIlxuIikpfTwvcD4KICAgICAgPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4IiBkYXRhLWNsb3Nl"
    "PkNsb3NlPC9idXR0b24+YCk7CiAgICBlbHNlIGlmKHdoYXQ9PT0ibG9ndGVzdCIpdG9hc3QoIlRl"
    "c3QgbWVzc2FnZSBzZW50IHRvIHRoZSBsb2cgY2hhbm5lbCIpOwogICAgZWxzZSBpZih3aGF0PT09"
    "ImNsZWFyc3RvY2siKXRvYXN0KHIucmVtb3ZlZCsiIHJlbW92ZWQiKTsKICAgIGVsc2UgdG9hc3Qo"
    "IkRvbmUiKTsKICAgIGF3YWl0IHJlZnJlc2goKTsKICB9KTsKICBjb25zdCB1cGlhZGQ9ZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoInVwaWFkZCIpOwogIGlmKHVwaWFkZCl1cGlhZGQub25jbGljaz1h"
    "c3luYygpPT57CiAgICBjb25zdCBpPXVwaWFkZC5jbG9zZXN0KCJkaXYiKS5xdWVyeVNlbGVjdG9y"
    "KCJpbnB1dCIpOwogICAgY29uc3Qgdj1pPyhpLnZhbHVlfHwiIikudHJpbSgpOiIiOwogICAgaWYo"
    "IXYpe3RvYXN0KCJQYXN0ZSB0aGUgZXh0cmEgVVBJIGhhbmRsZSIpO3JldHVybjt9CiAgICBjb25z"
    "dCBjdXI9KEFETS5wYXkmJkFETS5wYXkuZXh0cmEpfHwiIjsKICAgIGNvbnN0IHI9YXdhaXQgYXBp"
    "VHJ5KCIvYXBpL2FkbWluL3NldHRpbmciLHtrZXk6InVwaV9leHRyYSIsdmFsdWU6Y3VyPyhjdXIr"
    "IiwiK3YpOnZ9KTsKICAgIGlmKHIpe2kudmFsdWU9IiI7dG9hc3QoIkhhbmRsZSBhZGRlZCIpO2F3"
    "YWl0IHJlZnJlc2goKTt9CiAgfTsKICBjb25zdCBhZG1pbmFkZD1kb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgiYWRtaW5hZGQiKTsKICBpZihhZG1pbmFkZClhZG1pbmFkZC5vbmNsaWNrPWFzeW5jKCk9"
    "PnsKICAgIGNvbnN0IGk9YWRtaW5hZGQuY2xvc2VzdCgiZGl2IikucXVlcnlTZWxlY3RvcigiaW5w"
    "dXQiKTsKICAgIGNvbnN0IHY9aT8oaS52YWx1ZXx8IiIpLnRyaW0oKS5yZXBsYWNlKC9eQC8sIiIp"
    "OiIiOwogICAgaWYoIXYpe3RvYXN0KCJTZW5kIHRoZSBudW1lcmljIHVzZXIgaWQiKTtyZXR1cm47"
    "fQogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vbWlzYyIse3doYXQ6ImFkZGFk"
    "bWluIix2YWx1ZTp2fSk7CiAgICBpZihyKXtpLnZhbHVlPSIiO3RvYXN0KCJBZG1pbiBhZGRlZCB3"
    "aXRoIG5vIHBvd2VycyDigJQgZ3JhbnQgdGhlbSBpbiB0aGUgYm90Iik7YXdhaXQgcmVmcmVzaCgp"
    "O30KICB9OwogIGNvbnN0IHByaXplPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJwcml6ZXNhdmUi"
    "KTsKICBpZihwcml6ZSlwcml6ZS5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGk9cHJpemUu"
    "Y2xvc2VzdCgiZGl2IikucXVlcnlTZWxlY3RvcigiaW5wdXQiKTsKICAgIGNvbnN0IHI9YXdhaXQg"
    "YXBpVHJ5KCIvYXBpL2FkbWluL3NldHRpbmciLHtrZXk6ImxlYWRlcmJvYXJkX3ByaXplIix2YWx1"
    "ZTooaSYmaS52YWx1ZSl8fCIwIn0pOwogICAgaWYocil0b2FzdCgiUHJpemUgc2F2ZWQiKTsKICB9"
    "OwogIGNvbnN0IGNwYWRkPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjcGFkZCIpOwogIGlmKGNw"
    "YWRkKWNwYWRkLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgY29kZT0oKGRvY3VtZW50Lmdl"
    "dEVsZW1lbnRCeUlkKCJjcGNvZGUiKXx8e30pLnZhbHVlfHwiIikudHJpbSgpOwogICAgY29uc3Qg"
    "cGN0PSgoZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNwcGN0Iil8fHt9KS52YWx1ZXx8IiIpLnRy"
    "aW0oKTsKICAgIGlmKCFjb2RlfHwhcGN0KXt0b2FzdCgiQ29kZSBhbmQgcGVyY2VudCwgcGxlYXNl"
    "Iik7cmV0dXJuO30KICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30K"
    "ICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NvdXBvbiIse2NvZGU6Y29kZSxw"
    "ZXJjZW50OnBjdH0pOwogICAgaWYoIXIpcmV0dXJuOwogICAgY29uc3QgYTE9ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoImNwY29kZSIpLGEyPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjcHBjdCIp"
    "OwogICAgaWYoYTEpYTEudmFsdWU9IiI7aWYoYTIpYTIudmFsdWU9IiI7CiAgICB0b2FzdCgiQ291"
    "cG9uICIrY29kZS50b1VwcGVyQ2FzZSgpKyIgaXMgbGl2ZSIpO2F3YWl0IHJlZnJlc2goKTsKICB9"
    "OwogIGNvbnN0IGZnPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJmbGFzaGdvIik7CiAgaWYoZmcp"
    "Zmcub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBjYXJkPWZnLmNsb3Nlc3QoIi5jYXJkIil8"
    "fGRvY3VtZW50OwogICAgY29uc3QgaW5zPWNhcmQucXVlcnlTZWxlY3RvckFsbCgiaW5wdXQiKTsK"
    "ICAgIGNvbnN0IHBjdD0oKGluc1swXSYmaW5zWzBdLnZhbHVlKXx8IjIwIiksIGhycz0oKGluc1sx"
    "XSYmaW5zWzFdLnZhbHVlKXx8IjUiKTsKICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5"
    "Iik7cmV0dXJuO30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2ZsYXNoIix7"
    "cGVyY2VudDpwY3QsaG91cnM6aHJzfSk7CiAgICBpZihyKXt0b2FzdCgiRmxhc2ggc2FsZSBsaXZl"
    "IOKAlCAiK3BjdCsiJSBmb3IgIitocnMrImgiKTthd2FpdCByZWZyZXNoKCk7fQogIH07CiAgY29u"
    "c3QgYmM9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJjZ28iKTsKICBpZihiYyliYy5vbmNsaWNr"
    "PWFzeW5jKCk9PnsKICAgIGNvbnN0IGNhcmQ9YmMuY2xvc2VzdCgiLmNhcmQiKXx8ZG9jdW1lbnQ7"
    "CiAgICBjb25zdCB0YT1jYXJkLnF1ZXJ5U2VsZWN0b3IoInRleHRhcmVhIik7CiAgICBjb25zdCB0"
    "ZXh0PSgodGEmJnRhLnZhbHVlKXx8IiIpLnRyaW0oKTsKICAgIGlmKCF0ZXh0KXt0b2FzdCgiV3Jp"
    "dGUgdGhlIG1lc3NhZ2UgZmlyc3QiKTtpZih0YSl0YS5mb2N1cygpO3JldHVybjt9CiAgICBpZigh"
    "TElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICBiYy5kaXNhYmxlZD10cnVl"
    "O2JjLnRleHRDb250ZW50PSJTZW5kaW5n4oCmIjsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIv"
    "YXBpL2FkbWluL2Jyb2FkY2FzdCIse3RleHQ6dGV4dH0pOwogICAgYmMuZGlzYWJsZWQ9ZmFsc2U7"
    "YmMudGV4dENvbnRlbnQ9IlNlbmQgQnJvYWRjYXN0IjsKICAgIGlmKHIpe3RvYXN0KCJTZW50IHRv"
    "ICIrci5zZW50KyIgcGVvcGxlIik7aWYodGEpdGEudmFsdWU9IiI7fQogIH07CiAgZG9jdW1lbnQu"
    "cXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtZGVwLW9rXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFz"
    "eW5jKCk9PnsKICAgIGNvbnN0IGlkPWIuZGF0YXNldC5kZXBPazsKICAgIGlmKCFpZCl7dG9hc3Qo"
    "Ik5vdGhpbmcgdG8gYXBwcm92ZSIpO3JldHVybjt9CiAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgi"
    "L2FwaS9hZG1pbi9kZXBvc2l0Iix7aWQ6K2lkLGFjdGlvbjoiYXBwcm92ZSJ9KTsKICAgIGlmKHIp"
    "e3RvYXN0KCJDcmVkaXRlZCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgfSk7CiAgZG9jdW1lbnQucXVl"
    "cnlTZWxlY3RvckFsbCgiW2RhdGEtcmV2XSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9"
    "PnsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3JldmlldyIse2lkOitiLmRh"
    "dGFzZXQucmV2fSk7CiAgICBpZihyKXt0b2FzdCgiVXBkYXRlZCIpO2F3YWl0IHJlZnJlc2goKTt9"
    "CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtc2V0dGluZ10iKS5mb3JF"
    "YWNoKHQ9PnsKICAgIHQub25jbGljaz1hc3luYygpPT57CiAgICAgIGNvbnN0IGtleT10LmRhdGFz"
    "ZXQuc2V0dGluZywgb249IXQuY2xhc3NMaXN0LmNvbnRhaW5zKCJvbiIpOwogICAgICB0LmNsYXNz"
    "TGlzdC50b2dnbGUoIm9uIixvbik7CiAgICAgIGlmKCFMSVZFKXt0b2FzdChvbj8iT24iOiJPZmYi"
    "KTtyZXR1cm47fQogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zZXR0aW5n"
    "Iix7a2V5OmtleSx2YWx1ZTpvbj8iMSI6IjAifSk7CiAgICAgIGlmKCFyKXt0LmNsYXNzTGlzdC50"
    "b2dnbGUoIm9uIiwhb24pO3JldHVybjt9CiAgICAgIEFETS5zdz1BRE0uc3d8fHt9O0FETS5zd1tr"
    "ZXldPW9uOwogICAgICB0b2FzdChrZXkucmVwbGFjZSgvXy9nLCIgIikrKG9uPyIgb24iOiIgb2Zm"
    "IikpOwogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1hZG1p"
    "bi1hY3RdIikuZm9yRWFjaChiPT57CiAgICBiLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgICBjb25z"
    "dCBhY3Q9Yi5kYXRhc2V0LmFkbWluQWN0OwogICAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcg"
    "b25seSIpO3JldHVybjt9CiAgICAgIGNvbnN0IGxhYmVsPWIudGV4dENvbnRlbnQ7Yi5kaXNhYmxl"
    "ZD10cnVlO2IudGV4dENvbnRlbnQ9IldvcmtpbmfigKYiOwogICAgICBsZXQgcj1udWxsOwogICAg"
    "ICBpZihhY3Q9PT0iYmFja3VwIikgICAgICByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9iYWNr"
    "dXAiLHt9KTsKICAgICAgZWxzZSBpZihhY3Q9PT0iZGlnZXN0Iikgcj1hd2FpdCBhcGlUcnkoIi9h"
    "cGkvYWRtaW4vZGlnZXN0Iix7fSk7CiAgICAgIGVsc2UgaWYoYWN0PT09ImV4cG9ydCIpIHI9YXdh"
    "aXQgYXBpVHJ5KCIvYXBpL2FkbWluL2V4cG9ydCIse30pOwogICAgICBlbHNlIGlmKGFjdD09PSJz"
    "eW5jIikgICByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9zeW5jIix7fSk7CiAgICAgIGIuZGlz"
    "YWJsZWQ9ZmFsc2U7Yi50ZXh0Q29udGVudD1sYWJlbDsKICAgICAgaWYoIXIpcmV0dXJuOwogICAg"
    "ICBpZihhY3Q9PT0iYmFja3VwIil0b2FzdCgiRGF0YWJhc2Ugc2VudCB0byB5b3UgaW4gY2hhdCIp"
    "OwogICAgICBpZihhY3Q9PT0iZGlnZXN0Iil0b2FzdCgiRGlnZXN0IHNlbnQgdG8gdGhlIGFkbWlu"
    "cyIpOwogICAgICBpZihhY3Q9PT0iZXhwb3J0Iil0b2FzdChyLnJvd3MrIiBvcmRlcnMgc2VudCBh"
    "cyBDU1YiKTsKICAgICAgaWYoYWN0PT09InN5bmMiKXRvYXN0KCJDb21tYW5kcyBzeW5jZWQgd2l0"
    "aCBUZWxlZ3JhbSIpOwogICAgICBhd2FpdCByZWZyZXNoKCk7CiAgICB9OwogIH0pOwogIGRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXByaWNlXSIpLmZvckVhY2goaW5wPT57CiAgICBp"
    "bnAub25jaGFuZ2U9YXN5bmMoKT0+ewogICAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25s"
    "eSIpO3JldHVybjt9CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3ByaWNp"
    "bmciLHtrZXk6aW5wLmRhdGFzZXQucHJpY2UsdmFsdWU6aW5wLnZhbHVlfSk7CiAgICAgIGlmKHIp"
    "e3RvYXN0KCJQcmljaW5nIHVwZGF0ZWQiKTthd2FpdCByZWZyZXNoKCk7fQogICAgfTsKICB9KTsK"
    "ICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1jcHJpY2VdIikuZm9yRWFjaChpbnA9"
    "PnsKICAgIGlucC5vbmNoYW5nZT1hc3luYygpPT57CiAgICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJl"
    "dmlldyBvbmx5Iik7cmV0dXJuO30KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRt"
    "aW4vcHJpY2luZyIse2NvdW50cnk6aW5wLmRhdGFzZXQuY3ByaWNlLHByaWNlOmlucC52YWx1ZX0p"
    "OwogICAgICBpZihyKXt0b2FzdChpbnAuZGF0YXNldC5jcHJpY2UrIiBub3cgIittb25leShpbnAu"
    "dmFsdWUpKTthd2FpdCByZWZyZXNoKCk7fQogICAgfTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNl"
    "bGVjdG9yQWxsKCJbZGF0YS1xYV0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgY29u"
    "c3Qgd2hhdD1iLmRhdGFzZXQucWE7CiAgICBjb25zdCBnbz0odGFiLG1zZyk9PnsKICAgICAgY29u"
    "c3QgdD1kb2N1bWVudC5xdWVyeVNlbGVjdG9yKGAjYXN1YiBidXR0b25bZGF0YS1hPSIke3RhYn0i"
    "XWApOwogICAgICBpZih0KXt0LmNsaWNrKCk7dG9hc3QobXNnKTt9ZWxzZSB0b2FzdChtc2cpOwog"
    "ICAgfTsKICAgIGlmKHdoYXQ9PT0iYnJvYWRjYXN0IilyZXR1cm4gZ28oIm1hcmtldCIsIkNvbXBv"
    "c2UgdGhlIGJyb2FkY2FzdCBmcm9tIHRoZSBib3Q6IC9hZG1pbiDihpIgTWFya2V0aW5nIik7CiAg"
    "ICBpZih3aGF0PT09InN0b2NrIilyZXR1cm4gZ28oInN0b2NrIiwiQWRkIHN0b2NrIGZyb20gdGhl"
    "IGJvdDogL2FkbWluIOKGkiBTdG9jayDihpIgQWRkIHN0b2NrIik7CiAgICBpZih3aGF0PT09ImZs"
    "YXNoIilyZXR1cm4gZ28oIm1hcmtldCIsIkZsYXNoIHNhbGUgbGl2ZXMgaGVyZSIpOwogICAgaWYo"
    "d2hhdD09PSJiYWNrdXAiKXJldHVybiB0b2FzdCgiQmFja3VwcyBhcmUgc2VudCB0byB5b3VyIGxv"
    "ZyBjaGFubmVsIOKAlCAvYWRtaW4g4oaSIFNldHRpbmdzIik7CiAgfSk7CiAgZG9jdW1lbnQucXVl"
    "cnlTZWxlY3RvckFsbCgiW2RhdGEtc2V0XSIpLmZvckVhY2goaW5wPT57CiAgICBpbnAub25jaGFu"
    "Z2U9YXN5bmMoKT0+ewogICAgICBjb25zdCBrZXk9aW5wLmRhdGFzZXQuc2V0LCB2YWw9aW5wLnZh"
    "bHVlOwogICAgICBpZighTElWRSl7dG9hc3QoIlByZXZpZXcgb25seSIpO3JldHVybjt9CiAgICAg"
    "IGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3NldHRpbmciLHtrZXk6a2V5LHZhbHVl"
    "OnZhbH0pOwogICAgICBpZihyKXt0b2FzdChrZXkucmVwbGFjZSgvXy9nLCIgIikrIiBzYXZlZCIp"
    "O2F3YWl0IHJlZnJlc2goKTt9CiAgICB9OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JB"
    "bGwoIltkYXRhLWNtc10iKS5mb3JFYWNoKHQ9PnsKICAgIHQub25pbnB1dD0oKT0+e0NNU1t0LmRh"
    "dGFzZXQuY21zXT10LnZhbHVlO2FwcGx5Q01TKCk7CiAgICAgIGNsZWFyVGltZW91dCh0Ll9zdik7"
    "dC5fc3Y9c2V0VGltZW91dCgoKT0+cHVzaCgiL2FwaS9hZG1pbi9jb250ZW50Iix7a2V5OnQuZGF0"
    "YXNldC5jbXMsdmFsdWU6dC52YWx1ZX0pLDUwMCk7CiAgICAgIHQuY2xvc2VzdCgiLmNtc3JvdyIp"
    "LmRhdGFzZXQuZj0odC5jbG9zZXN0KCIuY21zcm93IikucXVlcnlTZWxlY3RvcigibGFiZWwiKS50"
    "ZXh0Q29udGVudCsiICIrdC52YWx1ZSkudG9Mb3dlckNhc2UoKTt9O30pOwogIGNvbnN0IGNxPWRv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJjcSIpOwogIGlmKGNxKWNxLm9uaW5wdXQ9KCk9Pntjb25z"
    "dCBxPWNxLnZhbHVlLnRyaW0oKS50b0xvd2VyQ2FzZSgpOwogICAgZG9jdW1lbnQucXVlcnlTZWxl"
    "Y3RvckFsbCgiLmNtc3JvdyIpLmZvckVhY2gocj0+ci5jbGFzc0xpc3QudG9nZ2xlKCJoaWRlIiwh"
    "IXEmJiFyLmRhdGFzZXQuZi5pbmNsdWRlcyhxKSkpOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3Rv"
    "ckFsbCgiLmNtc2dycCIpLmZvckVhY2goZz0+Zy5jbGFzc0xpc3QudG9nZ2xlKCJoaWRlIiwhZy5x"
    "dWVyeVNlbGVjdG9yKCIuY21zcm93Om5vdCguaGlkZSkiKSkpO307CiAgY29uc3QgY3I9ZG9jdW1l"
    "bnQuZ2V0RWxlbWVudEJ5SWQoImNtc3Jlc2V0Iik7CiAgaWYoY3IpY3Iub25jbGljaz0oKT0+e09i"
    "amVjdC5hc3NpZ24oQ01TLENNUzApO2FwcGx5Q01TKCk7cmVuZGVyQWRtaW4oKTt0b2FzdCgiVGV4"
    "dCByZXNldCB0byBkZWZhdWx0cyIpO307CiAgY29uc3QgYWRkPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJjaC1hZGQiKTsKICBpZihhZGQpYWRkLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3Qg"
    "aT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2gtaW4iKSx2PShpLnZhbHVlfHwiIikudHJpbSgp"
    "OwogICAgaWYoIXYpe3RvYXN0KCJQYXN0ZSBhIGNoYW5uZWwgbGluayBmaXJzdCIpO2kuZm9jdXMo"
    "KTtyZXR1cm59CiAgICBjb25zdCByZXE9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNoLXR5cGUi"
    "KS52YWx1ZT09PSIxIjsKICAgIGlmKExJVkUpewogICAgICBhZGQuZGlzYWJsZWQ9dHJ1ZTsKICAg"
    "ICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vY2hhbm5lbCIse2FjdGlvbjoiYWRk"
    "IixoYW5kbGU6dixyZXF1aXJlZDpyZXF9KTsKICAgICAgYWRkLmRpc2FibGVkPWZhbHNlOwogICAg"
    "ICBpZighcilyZXR1cm47CiAgICAgIGkudmFsdWU9IiI7YXdhaXQgcmVmcmVzaCgpO3RvYXN0KCJD"
    "aGFubmVsIHNhdmVkIOKAlCB0aGUgYm90IHVzZXMgaXQgbm93Iik7cmV0dXJuOwogICAgfQogICAg"
    "Q0hBTk5FTFMucHVzaCh7aWQ6RGF0ZS5ub3coKSxuYW1lOnYucmVwbGFjZSgvXmh0dHBzPzpcL1wv"
    "dFwubWVcLy8sIkAiKS5zbGljZSgwLDI2KSxoYW5kbGU6dixyZXE6cmVxLG9uOnRydWUsbWVtYmVy"
    "czoi4oCUIn0pOwogICAgaS52YWx1ZT0iIjtyZW5kZXJBZG1pbigpO3RvYXN0KCJDaGFubmVsIGFk"
    "ZGVkIik7fTsKICBjb25zdCBsZ2FkZD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgibGctYWRkIik7"
    "CiAgaWYobGdhZGQpbGdhZGQub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBpPWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJsZy1pbiIpLHY9KGkudmFsdWV8fCIiKS50cmltKCk7CiAgICBpZigh"
    "dil7dG9hc3QoIlBhc3RlIGEgbG9nIGNoYW5uZWwgZmlyc3QiKTtpLmZvY3VzKCk7cmV0dXJufQog"
    "ICAgaWYoTElWRSl7CiAgICAgIGxnYWRkLmRpc2FibGVkPXRydWU7CiAgICAgIGNvbnN0IHI9YXdh"
    "aXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rpb246ImFkZCIsaGFuZGxlOnYsbG9n"
    "OnRydWV9KTsKICAgICAgbGdhZGQuZGlzYWJsZWQ9ZmFsc2U7CiAgICAgIGlmKCFyKXJldHVybjsK"
    "ICAgICAgaS52YWx1ZT0iIjthd2FpdCByZWZyZXNoKCk7dG9hc3QoIkxvZyBjaGFubmVsIHNhdmVk"
    "Iik7cmV0dXJuOwogICAgfQogICAgTE9HQ0gucHVzaCh7aWQ6RGF0ZS5ub3coKSxuYW1lOnYucmVw"
    "bGFjZSgvXmh0dHBzPzpcL1wvdFwubWVcLy8sIkAiKS5zbGljZSgwLDI0KSxoYW5kbGU6dixwcmlt"
    "YXJ5OkxPR0NILmxlbmd0aD09PTAsb246dHJ1ZX0pOwogICAgaS52YWx1ZT0iIjtyZW5kZXJBZG1p"
    "bigpO3RvYXN0KCJMb2cgY2hhbm5lbCBhZGRlZCIpO307CiAgZG9jdW1lbnQucXVlcnlTZWxlY3Rv"
    "ckFsbCgiW2RhdGEtY2hkZWxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAg"
    "aWYoTElWRSl7Y29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vY2hhbm5lbCIse2FjdGlv"
    "bjoiZGVsIixpZDorYi5kYXRhc2V0LmNoZGVsfSk7CiAgICAgIGlmKHIpe2F3YWl0IHJlZnJlc2go"
    "KTt0b2FzdCgiQ2hhbm5lbCByZW1vdmVkIik7fXJldHVybjt9CiAgICBDSEFOTkVMUz1DSEFOTkVM"
    "Uy5maWx0ZXIoYz0+Yy5pZCE9Yi5kYXRhc2V0LmNoZGVsKTtyZW5kZXJBZG1pbigpO3RvYXN0KCJD"
    "aGFubmVsIHJlbW92ZWQiKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNo"
    "cmVxXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKExJVkUpe2NvbnN0"
    "IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rpb246InJlcXVpcmVkIixp"
    "ZDorYi5kYXRhc2V0LmNocmVxfSk7CiAgICAgIGlmKHIpe2F3YWl0IHJlZnJlc2goKTt0b2FzdCgi"
    "VXBkYXRlZCIpO31yZXR1cm47fQogICAgY29uc3QgYz1DSEFOTkVMUy5maW5kKHg9PnguaWQ9PWIu"
    "ZGF0YXNldC5jaHJlcSk7Yy5yZXE9IWMucmVxO3JlbmRlckFkbWluKCk7dG9hc3QoYy5yZXE/Ik5v"
    "dyByZXF1aXJlZCI6Ik5vdyBvcHRpb25hbCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiW2RhdGEtY2hvbl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBpZihM"
    "SVZFKXtjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJ0"
    "b2dnbGUiLGlkOitiLmRhdGFzZXQuY2hvbn0pOwogICAgICBpZihyKXthd2FpdCByZWZyZXNoKCk7"
    "dG9hc3QoIlVwZGF0ZWQiKTt9cmV0dXJuO30KICAgIGNvbnN0IGM9Q0hBTk5FTFMuZmluZCh4PT54"
    "LmlkPT1iLmRhdGFzZXQuY2hvbik7Yy5vbj0hYy5vbjtyZW5kZXJBZG1pbigpO3RvYXN0KGMub24/"
    "IkNoYW5uZWwgZW5hYmxlZCI6IkNoYW5uZWwgcGF1c2VkIil9KTsKICBkb2N1bWVudC5xdWVyeVNl"
    "bGVjdG9yQWxsKCJbZGF0YS1sZ2RlbF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAg"
    "Y29uc3Qgd2FzUD1MT0dDSC5maW5kKGw9PmwuaWQ9PWIuZGF0YXNldC5sZ2RlbCk/LnByaW1hcnk7"
    "CiAgICBMT0dDSD1MT0dDSC5maWx0ZXIobD0+bC5pZCE9Yi5kYXRhc2V0LmxnZGVsKTsKICAgIGlm"
    "KHdhc1AmJkxPR0NIWzBdKUxPR0NIWzBdLnByaW1hcnk9dHJ1ZTtyZW5kZXJBZG1pbigpO3RvYXN0"
    "KCJMb2cgY2hhbm5lbCByZW1vdmVkIil9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJb"
    "ZGF0YS1sZ3N0YXJdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9PnsKICAgIExPR0NILmZvckVh"
    "Y2gobD0+bC5wcmltYXJ5PShsLmlkPT1iLmRhdGFzZXQubGdzdGFyKSk7cmVuZGVyQWRtaW4oKTt0"
    "b2FzdCgiUHJlZmVycmVkIGxvZyBjaGFubmVsIHNldCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxl"
    "Y3RvckFsbCgiW2RhdGEtbGdvbl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+e2NvbnN0IGw9"
    "TE9HQ0guZmluZCh4PT54LmlkPT1iLmRhdGFzZXQubGdvbik7bC5vbj0hbC5vbjtyZW5kZXJBZG1p"
    "bigpO3RvYXN0KGwub24/IkNoYW5uZWwgb24iOiJDaGFubmVsIG11dGVkIil9KTsKICBkb2N1bWVu"
    "dC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1ldm9uXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgp"
    "PT57Y29uc3QgZT1FVkVOVFNbK2IuZGF0YXNldC5ldm9uXTtlLm9uPSFlLm9uO3JlbmRlckFkbWlu"
    "KCl9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1ldmNoXSIpLmZvckVhY2go"
    "cz0+cy5vbmNoYW5nZT0oKT0+e0VWRU5UU1srcy5kYXRhc2V0LmV2Y2hdLmNoPStzLnZhbHVlO3Rv"
    "YXN0KCJSb3V0aW5nIHVwZGF0ZWQiKX0pOwogIGNvbnN0IGFsbD1kb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yKCJbZGF0YS1ldi1hbGxdIik7CiAgaWYoYWxsKWFsbC5vbmNsaWNrPSgpPT57Y29uc3QgdD0h"
    "RVZFTlRTLmV2ZXJ5KGU9PmUub24pO0VWRU5UUy5mb3JFYWNoKGU9PmUub249dCk7cmVuZGVyQWRt"
    "aW4oKTt0b2FzdCh0PyJBbGwgZXZlbnRzIG9uIjoiQWxsIGV2ZW50cyBvZmYiKX07CiAgY29uc3Qg"
    "bT1kb2N1bWVudC5xdWVyeVNlbGVjdG9yKCJbZGF0YS1tYXN0ZXJdIik7CiAgaWYobSltLm9uY2xp"
    "Y2s9KCk9PntMT0dTX01BU1RFUj0hTE9HU19NQVNURVI7cmVuZGVyQWRtaW4oKTt0b2FzdChMT0dT"
    "X01BU1RFUj8iTG9nZ2luZyBlbmFibGVkIjoiQWxsIGxvZ2dpbmcgZGlzYWJsZWQiKX07CiAgZG9j"
    "dW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtYS1nb10iKS5mb3JFYWNoKGI9PmIub25jbGlj"
    "az0oKT0+ewogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI2FzdWIgYnV0dG9uIikuZm9y"
    "RWFjaCh4PT54LmNsYXNzTGlzdC50b2dnbGUoIm9uIix4LmRhdGFzZXQuYT09PWIuZGF0YXNldC5h"
    "R28pKTsKICAgIEFUQUI9Yi5kYXRhc2V0LmFHbztyZW5kZXJBZG1pbigpO3dpbmRvdy5zY3JvbGxU"
    "bygwLDApO30pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXRnXSIpLmZvckVh"
    "Y2godD0+dC5vbmNsaWNrPSgpPT50LmNsYXNzTGlzdC50b2dnbGUoIm9uIikpOwogIGRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLXNldHNlbF0iKS5mb3JFYWNoKHNlbD0+c2VsLm9uY2hh"
    "bmdlPWFzeW5jKCk9PnsKICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJu"
    "O30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3NldHRpbmciLHtrZXk6c2Vs"
    "LmRhdGFzZXQuc2V0c2VsLHZhbHVlOnNlbC52YWx1ZX0pOwogICAgaWYocil0b2FzdCgiU2F2ZWQi"
    "KTsKICB9KTsKfQoKY29uc3QgX2NjPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjYXJ0Y2hpcCIp"
    "OwppZihfY2MpX2NjLm9uY2xpY2s9Y2FydFNoZWV0OwpyZW5kZXIoKTtyZW5kZXJBZG1pbigpO2Fw"
    "cGx5RmVhdHVyZXMoKTsKCi8qIOKUgOKUgCBsaXZlIGRhdGE6IGV2ZXJ5dGhpbmcgYmVsb3cgY29t"
    "ZXMgZnJvbSB0aGUgYm90IGRhdGFiYXNlIHdoZW4gc2VydmVkIGJ5IHRoZSBib3Qg4pSA4pSAICov"
    "Ci8qIExJVkUgLyBNRSAvIFRHIC8gSU5JVCBhcmUgZGVjbGFyZWQgYXQgdGhlIHZlcnkgdG9wIG9m"
    "IHRoaXMgc2NyaXB0ICovCmFzeW5jIGZ1bmN0aW9uIGFwaShwYXRoLGJvZHkpewogIGNvbnN0IG89"
    "e2hlYWRlcnM6eyJDb250ZW50LVR5cGUiOiJhcHBsaWNhdGlvbi9qc29uIiwiWC1Jbml0LURhdGEi"
    "OklOSVQsCiAgICAiWC1UZWxlZ3JhbS1Jbml0LURhdGEiOklOSVQsIlgtQXBwLUtleSI6QVBQS0VZ"
    "fX07CiAgaWYoYm9keSE9PXVuZGVmaW5lZCl7by5tZXRob2Q9IlBPU1QiO28uYm9keT1KU09OLnN0"
    "cmluZ2lmeShib2R5KTt9CiAgY29uc3Qgcj1hd2FpdCBmZXRjaChwYXRoLG8pOwogIGxldCBkPW51"
    "bGw7dHJ5e2Q9YXdhaXQgci5qc29uKCk7fWNhdGNoKGVycil7fQogIGlmKCFyLm9rKXtjb25zdCBl"
    "PW5ldyBFcnJvcigoZCYmZC5lcnJvcil8fChwYXRoKyIgIityLnN0YXR1cykpO2Uuc3RhdHVzPXIu"
    "c3RhdHVzO2UuZGF0YT1kO3Rocm93IGU7fQogIHJldHVybiBkOwp9CmZ1bmN0aW9uIGFkbWluQWxs"
    "b3dlZCgpe3JldHVybiAhTElWRSB8fCAhIShNRSYmTUUuYWRtaW4pO30KZnVuY3Rpb24gYXBwbHlT"
    "dGF0ZShkKXsKICAvKiBldmVyeSBsaXN0IGlzIHJlcGxhY2VkLCBldmVuIHdoZW4gZW1wdHkg4oCU"
    "IGEgbmV3IHNob3AgbXVzdCBuZXZlciBzaG93IGRlbW8gZGF0YSAqLwogIGlmKGQuZmVhdHVyZXMp"
    "T2JqZWN0LmtleXMoZC5mZWF0dXJlcykuZm9yRWFjaChrPT57aWYoayBpbiBGRUFUKUZFQVRba109"
    "ISFkLmZlYXR1cmVzW2tdO30pOwogIGlmKGQuY29udGVudClPYmplY3Qua2V5cyhkLmNvbnRlbnQp"
    "LmZvckVhY2goaz0+e2lmKGsgaW4gQ01TJiZkLmNvbnRlbnRba10hPW51bGwpQ01TW2tdPWQuY29u"
    "dGVudFtrXTt9KTsKICBpZihBcnJheS5pc0FycmF5KGQuc3RvY2spKXtTVE9DSy5sZW5ndGg9MDtk"
    "LnN0b2NrLmZvckVhY2goeD0+U1RPQ0sucHVzaCh4KSk7RklMVEVSRUQ9U1RPQ0suc2xpY2UoKTt9"
    "CiAgaWYoQXJyYXkuaXNBcnJheShkLm9yZGVycykpe09SREVSUy5sZW5ndGg9MDtkLm9yZGVycy5m"
    "b3JFYWNoKHg9Pk9SREVSUy5wdXNoKHgpKTt9CiAgaWYoQXJyYXkuaXNBcnJheShkLnR4KSl7VFgu"
    "bGVuZ3RoPTA7ZC50eC5mb3JFYWNoKHg9PlRYLnB1c2goeCkpO30KICBpZihBcnJheS5pc0FycmF5"
    "KGQuZmVlZCkpRkVFRD1kLmZlZWQ7CiAgaWYoZC5lYXJuKUVBUk49T2JqZWN0LmFzc2lnbih7fSxF"
    "QVJOLGQuZWFybik7CiAgaWYoZC5wcmVmcylQUkVGUz1PYmplY3QuYXNzaWduKHt9LFBSRUZTLGQu"
    "cHJlZnMpOwogIGlmKEFycmF5LmlzQXJyYXkoZC5hbGVydHMpKXdpbmRvdy5fX2FsZXJ0cz1kLmFs"
    "ZXJ0czsKICBpZihkLnN0b3JlKXsKICAgIFNUT1JFPU9iamVjdC5hc3NpZ24oe30sU1RPUkUsZC5z"
    "dG9yZSk7CiAgICBpZihkLnN0b3JlLm5hbWUpQ0ZHLm5hbWU9ZC5zdG9yZS5uYW1lOwogICAgaWYo"
    "ZC5zdG9yZS50YWcpQ0ZHLnRhZz1kLnN0b3JlLnRhZzsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0"
    "b3JBbGwoIltkYXRhLXN0b3JlbmFtZV0iKS5mb3JFYWNoKGU9PmUudGV4dENvbnRlbnQ9Q0ZHLm5h"
    "bWUpOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtc3RvcmV0YWddIikuZm9y"
    "RWFjaChlPT5lLnRleHRDb250ZW50PUNGRy50YWcpOwogICAgZG9jdW1lbnQudGl0bGU9Q0ZHLm5h"
    "bWU7CiAgfQogIGlmKGQuYWRtKXsKICAgIEFETT1PYmplY3QuYXNzaWduKHt9LEFETSxkLmFkbSk7"
    "CiAgICBpZihBcnJheS5pc0FycmF5KEFETS5jaGFubmVscykpCiAgICAgIENIQU5ORUxTPUFETS5j"
    "aGFubmVscy5tYXAoYz0+KHtpZDpjLmlkLG5hbWU6Yy5uYW1lLGhhbmRsZTpjLmhhbmRsZXx8IiIs"
    "CiAgICAgICAgcmVxOiEhYy5yZXF1aXJlZCxvbjohIWMuYWN0aXZlLG1lbWJlcnM6IuKAlCJ9KSk7"
    "CiAgICBpZihBRE0ubG9ncyl7CiAgICAgIExPR1NfTUFTVEVSPSEhQURNLmxvZ3MubWFzdGVyOwog"
    "ICAgICBpZihBcnJheS5pc0FycmF5KEFETS5sb2dzLmNoYW5uZWxzKSkKICAgICAgICBMT0dDSD1B"
    "RE0ubG9ncy5jaGFubmVscy5tYXAoYz0+KHtpZDpjLmlkLG5hbWU6Yy5uYW1lLGhhbmRsZToiIiwK"
    "ICAgICAgICAgIHByaW1hcnk6ISFjLnByaW1hcnksb246ISFjLmFjdGl2ZX0pKTsKICAgICAgaWYo"
    "QXJyYXkuaXNBcnJheShBRE0ubG9ncy5ldmVudHMpJiZBRE0ubG9ncy5ldmVudHMubGVuZ3RoKQog"
    "ICAgICAgIEVWRU5UUz1BRE0ubG9ncy5ldmVudHMubWFwKGU9Pih7azplLmxhYmVsLG9uOiEhZS5v"
    "bixjaDooTE9HQ0hbMF18fHt9KS5pZHx8MX0pKTsKICAgIH0KICB9CiAgaWYoZC5yYXRpbmdzKVJF"
    "VklFV1M9ZC5yYXRpbmdzOwogIGlmKHR5cGVvZiBkLmJhbGFuY2U9PT0ibnVtYmVyIilCQUw9ZC5i"
    "YWxhbmNlOwogIGlmKGQubWUpTUU9ZC5tZTsKICBpZihkLmNhcnQpQ0FSVD1kLmNhcnQ7CiAgaWYo"
    "ZC53aXNobGlzdClXSVNIPWQud2lzaGxpc3Q7Cn0KZnVuY3Rpb24gc2hvd1N3aXRjaChvbil7CiAg"
    "Y29uc3Qgc3c9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN3Iik7CiAgaWYoc3cpc3cuc3R5bGUu"
    "ZGlzcGxheT1vbj8iZmxleCI6Im5vbmUiOwp9CnZhciBMT0NLX1RJVExFPSIiLExPQ0tfTVNHPSIi"
    "OwpmdW5jdGlvbiBsb2NrZWRTY3JlZW4odGl0bGUsbXNnKXsKICBMT0NLRUQ9dHJ1ZTtMT0NLX1RJ"
    "VExFPXRpdGxlO0xPQ0tfTVNHPW1zZzsKICBzaG93U3dpdGNoKGZhbHNlKTsKICBwYWludExvY2tl"
    "ZCgpOwp9CmZ1bmN0aW9uIHBhaW50TG9ja2VkKCl7CiAgY29uc3Qgc3Q9ZG9jdW1lbnQuZ2V0RWxl"
    "bWVudEJ5SWQoInN0YWdlIik7CiAgaWYoc3Qpc3QuaW5uZXJIVE1MPSc8ZGl2IGNsYXNzPSJjYXJk"
    "IiBzdHlsZT0ibWFyZ2luOjE4cHg7dGV4dC1hbGlnbjpjZW50ZXI7cGFkZGluZzoyMnB4Ij4nCiAg"
    "ICArJzxoMyBzdHlsZT0ibWFyZ2luOjAgMCA4cHgiPicrTE9DS19USVRMRSsnPC9oMz4nCiAgICAr"
    "JzxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCAwIDE0cHgiPicrTE9DS19NU0crJzwvcD4n"
    "CiAgICArJzxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgb25jbGljaz0ibG9jYXRpb24ucmVsb2Fk"
    "KCkiPlRyeSBhZ2FpbjwvYnV0dG9uPjwvZGl2Pic7CiAgWyJ0YWJzIiwiZG9jayJdLmZvckVhY2go"
    "aWQ9Pntjb25zdCBlPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKTtpZihlKWUuc3R5bGUuZGlz"
    "cGxheT0ibm9uZSI7fSk7Cn0KYXN5bmMgZnVuY3Rpb24gYXBpVHJ5KHBhdGgsYm9keSl7CiAgdHJ5"
    "e3JldHVybiBhd2FpdCBhcGkocGF0aCxib2R5KTt9CiAgY2F0Y2goZSl7dG9hc3QoU3RyaW5nKGUm"
    "JmUubWVzc2FnZXx8ZSkuc2xpY2UoMCw5MCkpO3JldHVybiBudWxsO30KfQphc3luYyBmdW5jdGlv"
    "biByZWZyZXNoKCl7CiAgaWYoIUxJVkUpcmV0dXJuOwogIHRyeXsKICAgIGFwcGx5U3RhdGUoYXdh"
    "aXQgYXBpKCIvYXBpL3N0YXRlIikpOwogICAgYXBwbHlDTVMoKTthcHBseUZlYXR1cmVzKCk7c3lu"
    "Y0JhbCgpO3JlbmRlcigpO3JlbmRlckFkbWluKCk7CiAgfWNhdGNoKGUpe30KfQphc3luYyBmdW5j"
    "dGlvbiBjb3B5VGV4dCh0LCBsYWJlbCl7CiAgY29uc3QgdHh0PVN0cmluZyh0fHwiIik7CiAgaWYo"
    "IXR4dCl7dG9hc3QoIk5vdGhpbmcgdG8gY29weSIpO3JldHVybiBmYWxzZTt9CiAgdHJ5ewogICAg"
    "aWYobmF2aWdhdG9yLmNsaXBib2FyZCYmd2luZG93LmlzU2VjdXJlQ29udGV4dCl7YXdhaXQgbmF2"
    "aWdhdG9yLmNsaXBib2FyZC53cml0ZVRleHQodHh0KTt9CiAgICBlbHNlewogICAgICBjb25zdCB0"
    "YT1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCJ0ZXh0YXJlYSIpOwogICAgICB0YS52YWx1ZT10eHQ7"
    "dGEuc2V0QXR0cmlidXRlKCJyZWFkb25seSIsIiIpOwogICAgICB0YS5zdHlsZS5jc3NUZXh0PSJw"
    "b3NpdGlvbjpmaXhlZDtsZWZ0Oi05OTk5cHg7b3BhY2l0eTowIjsKICAgICAgZG9jdW1lbnQuYm9k"
    "eS5hcHBlbmRDaGlsZCh0YSk7dGEuc2VsZWN0KCk7dGEuc2V0U2VsZWN0aW9uUmFuZ2UoMCx0eHQu"
    "bGVuZ3RoKTsKICAgICAgY29uc3Qgb2s9ZG9jdW1lbnQuZXhlY0NvbW1hbmQoImNvcHkiKTtkb2N1"
    "bWVudC5ib2R5LnJlbW92ZUNoaWxkKHRhKTsKICAgICAgaWYoIW9rKXRocm93IG5ldyBFcnJvcigi"
    "Y29weSByZWZ1c2VkIik7CiAgICB9CiAgICB0b2FzdCgobGFiZWx8fCJDb3BpZWQiKSsiOiAiKyh0"
    "eHQubGVuZ3RoPjI2P3R4dC5zbGljZSgwLDI2KSsi4oCmIjp0eHQpKTsKICAgIHJldHVybiB0cnVl"
    "OwogIH1jYXRjaChlKXsKICAgIHNoZWV0KGA8aDMgc3R5bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQt"
    "c2l6ZToxcmVtIj5Db3B5IHRoaXM8L2gzPgogICAgICA8cCBjbGFzcz0ic3ViIj5Zb3VyIGJyb3dz"
    "ZXIgYmxvY2tlZCB0aGUgY2xpcGJvYXJkIOKAlCBzZWxlY3QgYW5kIGNvcHkuPC9wPgogICAgICA8"
    "dGV4dGFyZWEgY2xhc3M9ImlucCIgcm93cz0iMyIgcmVhZG9ubHkgb25jbGljaz0idGhpcy5zZWxl"
    "Y3QoKSI+JHtlc2ModHh0KX08L3RleHRhcmVhPgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxv"
    "Y2siIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiIGRhdGEtY2xvc2U+RG9uZTwvYnV0dG9uPmApOwog"
    "ICAgcmV0dXJuIGZhbHNlOwogIH0KfQpsZXQgU1RPUkU9e3VwaToiIixwYXllZToiIixwcmVmaXg6"
    "IlBBWSIsdGVybXM6IiIsc3VwcG9ydDoiIn07CmxldCBQUkVGUz17cmVzdG9jazp0cnVlLHJlY2Vp"
    "cHRzOnRydWUscHJvbW9zOnRydWUscHVzaDp0cnVlfTsKbGV0IE9GRkVSPSIiOwpmdW5jdGlvbiBv"
    "cGVuUGF5KGxpbmspewogIGlmKCFsaW5rKXJldHVybjsKICB0cnl7CiAgICBpZihURyYmVEcub3Bl"
    "bkxpbmsmJi9eaHR0cHM/Oi8udGVzdChsaW5rKSlURy5vcGVuTGluayhsaW5rKTsKICAgIGVsc2Ug"
    "d2luZG93LmxvY2F0aW9uLmhyZWY9bGluazsKICB9Y2F0Y2goZSl7d2luZG93LmxvY2F0aW9uLmhy"
    "ZWY9bGluazt9Cn0KYXN5bmMgZnVuY3Rpb24gYm9vdCgpewogIGlmKFRHKXt0cnl7VEcucmVhZHko"
    "KTtURy5leHBhbmQoKTt9Y2F0Y2goZSl7fX0KICBjb25zdCBpblRHPSEhKChJTklUJiZJTklULmxl"
    "bmd0aCl8fChBUFBLRVkmJkFQUEtFWS5sZW5ndGgpKTsKICBpZihpblRHKXNob3dTd2l0Y2goZmFs"
    "c2UpOyAgICAgICAgICAgICAgLyogZmFpbCBjbG9zZWQ6IG5vIGFkbWluIHN0cmlwIHVudGlsIHBy"
    "b3ZlbiAqLwogIGlmKGxvY2F0aW9uLnByb3RvY29sPT09ImZpbGU6IilyZXR1cm47ICAvKiBvcGVu"
    "ZWQgYXMgYSBwbGFpbiBmaWxlIOKAlCBkZW1vIGdhbGxlcnkgKi8KICBsZXQgZDsKICB0cnl7CiAg"
    "ICBkPWF3YWl0IGFwaSgiL2FwaS9zdGF0ZSIpOwogIH1jYXRjaChlKXsKICAgIGlmKGUmJmUuc3Rh"
    "dHVzPT09NDAxKXsKICAgICAgbGV0IHdoeT0iIixmaXg9IiI7CiAgICAgIHRyeXsKICAgICAgICBj"
    "b25zdCB3PWF3YWl0IChhd2FpdCBmZXRjaCgiL2FwaS93aG9hbWkiLHtoZWFkZXJzOnsiWC1Jbml0"
    "LURhdGEiOklOSVQsCiAgICAgICAgICAiWC1UZWxlZ3JhbS1Jbml0LURhdGEiOklOSVQsIlgtQXBw"
    "LUtleSI6QVBQS0VZfX0pKS5qc29uKCk7CiAgICAgICAgY29uc3QgUj17bWlzc2luZzpbIlRlbGVn"
    "cmFtIGRpZCBub3QgcGFzcyB5b3VyIGFjY291bnQgdG8gdGhpcyBwYWdlLiIsCiAgICAgICAgICAg"
    "ICAgICAgICAiT3BlbiB0aGUgYm90IGFuZCB0YXAgPGI+T3BlbiBTdG9yZTwvYj4sIG9yIHNlbmQg"
    "PGI+L2FwcDwvYj4uIl0sCiAgICAgICAgICAgICAgICAgImJhZC1mb3JtYXQiOlsiVGhlIHNpZ24t"
    "aW4gZGF0YSBhcnJpdmVkIGRhbWFnZWQuIiwiQ2xvc2UgdGhlIGFwcCBhbmQgb3BlbiBpdCBhZ2Fp"
    "biBmcm9tIHRoZSBib3QuIl0sCiAgICAgICAgICAgICAgICAgIm5vLWhhc2giOlsiVGhlIHNpZ24t"
    "aW4gZGF0YSBhcnJpdmVkIGluY29tcGxldGUuIiwiQ2xvc2UgdGhlIGFwcCBhbmQgb3BlbiBpdCBh"
    "Z2FpbiBmcm9tIHRoZSBib3QuIl0sCiAgICAgICAgICAgICAgICAgImJhZC1oYXNoIjpbIlRoZSBz"
    "aWduYXR1cmUgZG9lcyBub3QgbWF0Y2ggdGhpcyBib3QncyB0b2tlbi4iLAogICAgICAgICAgICAg"
    "ICAgICAgIlRoZSBwYWdlIGlzIHNlcnZlZCBieSBhIGRpZmZlcmVudCBib3QsIG9yIEJPVF9UT0tF"
    "TiBvbiB0aGUgc2VydmVyIGhhcyBxdW90ZXMgYXJvdW5kIGl0LiBTZW5kIDxiPi9kaWFnPC9iPiBp"
    "biB0aGUgYm90IHRvIGNvbXBhcmUuIl0sCiAgICAgICAgICAgICAgICAgc3RhbGU6WyJUaGF0IHNp"
    "Z24taW4gaXMgb2xkZXIgdGhhbiB0aGUgYWxsb3dlZCB3aW5kb3cuIiwiUmVvcGVuIHRoZSBhcHAg"
    "ZnJvbSB0aGUgYm90LiJdLAogICAgICAgICAgICAgICAgICJiYWQtdXNlciI6WyJZb3VyIGFjY291"
    "bnQgZGV0YWlscyBjb3VsZCBub3QgYmUgcmVhZC4iLCJSZW9wZW4gdGhlIGFwcCBmcm9tIHRoZSBi"
    "b3QuIl0sCiAgICAgICAgICAgICAgICAgIm5vLWlkIjpbIlRlbGVncmFtIHNlbnQgbm8gdXNlciBp"
    "ZC4iLCJSZW9wZW4gdGhlIGFwcCBmcm9tIHRoZSBib3QuIl19OwogICAgICAgIGNvbnN0IHI9Ult3"
    "LndoeV18fFsiVGhlIHN0b3JlIGNvdWxkIG5vdCBjb25maXJtIHdobyB5b3UgYXJlLiIsIk9wZW4g"
    "aXQgYWdhaW4gZnJvbSB0aGUgYm90LiJdOwogICAgICAgIHdoeT1yWzBdKyIgPGNvZGU+KCIrKHcu"
    "d2h5fHwidW5rbm93biIpKyIpPC9jb2RlPiI7CiAgICAgICAgZml4PXJbMV07CiAgICAgIH1jYXRj"
    "aChlcnIpewogICAgICAgIHdoeT0iVGhlIHN0b3JlIGNvdWxkIG5vdCBjb25maXJtIHdobyB5b3Ug"
    "YXJlLiI7CiAgICAgICAgZml4PSJPcGVuIGl0IGFnYWluIGZyb20gdGhlIGJvdCB3aXRoIHRoZSA8"
    "Yj5PcGVuIFN0b3JlPC9iPiBidXR0b24uIjsKICAgICAgfQogICAgICBsb2NrZWRTY3JlZW4oIkNh"
    "bm5vdCBvcGVuIHRoZSBzdG9yZSIsd2h5KyI8YnI+PGJyPiIrZml4KTsKICAgIH0KICAgIHJldHVy"
    "bjsKICB9CiAgYXBwbHlTdGF0ZShkKTtMSVZFPXRydWU7CiAgc2hvd1N3aXRjaCghIShNRSYmTUUu"
    "YWRtaW4pKTsgICAgICAgICAgIC8qIG9ubHkgYSB2ZXJpZmllZCBhZG1pbiBzZWVzIGl0ICovCiAg"
    "YXBwbHlDTVMoKTthcHBseUZlYXR1cmVzKCk7c3luY0JhbCgpO3JlbmRlcigpO3JlbmRlckFkbWlu"
    "KCk7Cn0KYXN5bmMgZnVuY3Rpb24gcHVzaChwYXRoLGJvZHksb2tNc2cpewogIGlmKCFMSVZFKXJl"
    "dHVybiB0cnVlOwogIHRyeXtjb25zdCByPWF3YWl0IGFwaShwYXRoLGJvZHkpO2lmKG9rTXNnKXRv"
    "YXN0KG9rTXNnKTtyZXR1cm4gcjt9CiAgY2F0Y2goZSl7dG9hc3QoIkNvdWxkIG5vdCBzYXZlIOKA"
    "lCBjaGVjayB5b3VyIGNvbm5lY3Rpb24iKTtyZXR1cm4gZmFsc2U7fQp9CmJvb3QoKTsKPC9zY3Jp"
    "cHQ+CjwvYm9keT48L2h0bWw+Cg=="
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
