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
DESIGN = "9-paper"


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
    "store_tagline": "Print clarity",
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
    "storeTag": "Print clarity",
    "tick1": "<b>Instant delivery</b> on every country",
    "tick2": "Wallet top-ups <b>auto-verified</b>",
    "tick3": "Failed login? <b>Auto refund</b>",
    "tick4": "Daily check-in <b>bonus</b>",
    "heroKick": "Catalogue",
    "heroH1": "An honest<br>account shop.",
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
    "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIiBkYXRhLWRlc2lnbj0icGFwZXIiPgo8aGVh"
    "ZD4KPG1ldGEgY2hhcnNldD0idXRmLTgiPgo8bWV0YSBuYW1lPSJ2aWV3cG9ydCIgY29udGVudD0i"
    "d2lkdGg9ZGV2aWNlLXdpZHRoLGluaXRpYWwtc2NhbGU9MSx2aWV3cG9ydC1maXQ9Y292ZXIsbWF4"
    "aW11bS1zY2FsZT0xIj4KPHNjcmlwdCBzcmM9Imh0dHBzOi8vdGVsZWdyYW0ub3JnL2pzL3RlbGVn"
    "cmFtLXdlYi1hcHAuanMiPjwvc2NyaXB0Pgo8dGl0bGU+Wm9ybyBURyBTdG9yZSDigJQgU3RvcmUg"
    "TWluaSBBcHAgKERlc2lnbiA5KTwvdGl0bGU+CjxzdHlsZT4KOnJvb3R7CiAgLS1iZzojZjdmNWVm"
    "OyAtLWJnMjojZmZmZGY4OyAtLWNhcmQ6I2ZmZmRmODsgLS1jYXJkMjojZWZlY2UzOwogIC0tbGlu"
    "ZTojZGVkOWNjOyAtLXR4OiMyMzIxMWM7IC0tdHgyOiM3Yjc2NmE7IC0tYWNjOiM4YTZmM2I7IC0t"
    "YWNjMjojNGI2YjRhOwogIC0tb2s6IzRiN2E0YzsgLS13YXJuOiNhOTdhMjQ7IC0tYmFkOiNhNjRi"
    "NDU7CiAgLS1yOjhweDsgLS1yczo2cHg7IC0tYnRuaDo0NHB4OyAtLWZvbnQ6dWktbW9ub3NwYWNl"
    "LCdTRiBNb25vJyxNZW5sbyxDb25zb2xhcyxtb25vc3BhY2U7IC0tZmhlYWQ6R2VvcmdpYSwnSW93"
    "YW4gT2xkIFN0eWxlJywnVGltZXMgTmV3IFJvbWFuJyxzZXJpZjsKICAtLXNoYWRvdzowIDFweCAw"
    "IHJnYmEoMzUsMzMsMjgsLjA2KTsgLS1waWxsZ3JhZDojMjMyMTFjOyAtLWJ0bmJnOiMyMzIxMWM7"
    "Cn0KKntib3gtc2l6aW5nOmJvcmRlci1ib3g7LXdlYmtpdC10YXAtaGlnaGxpZ2h0LWNvbG9yOnRy"
    "YW5zcGFyZW50fQpodG1sLGJvZHl7bWFyZ2luOjA7cGFkZGluZzowfQpib2R5e2JhY2tncm91bmQ6"
    "dmFyKC0tYmcpO2NvbG9yOnZhcigtLXR4KTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXNp"
    "emU6MTVweDtsaW5lLWhlaWdodDoxLjQ1O21pbi1oZWlnaHQ6MTAwdmg7b3ZlcmZsb3cteDpoaWRk"
    "ZW47cGFkZGluZy10b3A6MzJweDt9CmJvZHk6YmVmb3Jle2NvbnRlbnQ6IiI7cG9zaXRpb246Zml4"
    "ZWQ7aW5zZXQ6MDtwb2ludGVyLWV2ZW50czpub25lO3otaW5kZXg6MDt9Cjo6LXdlYmtpdC1zY3Jv"
    "bGxiYXJ7d2lkdGg6MDtoZWlnaHQ6MH0KI3NoZWxse3Bvc2l0aW9uOnJlbGF0aXZlO3otaW5kZXg6"
    "MTttYXgtd2lkdGg6NTIwcHg7bWFyZ2luOjAgYXV0bzttaW4taGVpZ2h0OjEwMHZoO3BhZGRpbmc6"
    "MCAxNnB4IDExMnB4fQouaGlkZGVue2Rpc3BsYXk6bm9uZSFpbXBvcnRhbnR9CgojdG9wYmFye3Bv"
    "c2l0aW9uOnN0aWNreTt0b3A6MzJweDt6LWluZGV4OjQwO2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7Z2FwOjEwcHg7cGFkZGluZzoxNHB4IDAgMTJweDtiYWNrZ3JvdW5kOmxpbmVhci1n"
    "cmFkaWVudCh2YXIoLS1iZykgNzIlLHRyYW5zcGFyZW50KX0KLmxvZ297d2lkdGg6MzhweDtoZWln"
    "aHQ6MzhweDtib3JkZXItcmFkaXVzOjhweDtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVy"
    "O2ZsZXg6MCAwIGF1dG87YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmRmODtm"
    "b250LXdlaWdodDo4MDA7Zm9udC1zaXplOi45cmVtO2xldHRlci1zcGFjaW5nOi0uMDNlbTt9Ci5i"
    "cmFuZHttaW4td2lkdGg6MDtmbGV4OjF9Ci5icmFuZCBie2Rpc3BsYXk6YmxvY2s7Zm9udC1mYW1p"
    "bHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZTouOTVyZW07Zm9udC13ZWlnaHQ6NjAwO2xldHRlci1z"
    "cGFjaW5nOjA7d2hpdGUtc3BhY2U6bm93cmFwO292ZXJmbG93OmhpZGRlbjt0ZXh0LW92ZXJmbG93"
    "OmVsbGlwc2lzfQouYnJhbmQgc3BhbntkaXNwbGF5OmJsb2NrO2ZvbnQtc2l6ZTouNjZyZW07Y29s"
    "b3I6dmFyKC0tdHgyKTtsZXR0ZXItc3BhY2luZzouMDZlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNh"
    "c2V9Ci5iYWxjaGlwe2Rpc3BsYXk6aW5saW5lLWZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo3"
    "cHg7aGVpZ2h0OjM4cHg7cGFkZGluZzowIDEzcHg7Ym9yZGVyLXJhZGl1czo3cHg7YmFja2dyb3Vu"
    "ZDp2YXIoLS1jYXJkKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2NvbG9yOnZhcigtLXR4"
    "KTtmb250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXdlaWdodDo3MDA7Zm9udC1zaXplOi44NXJl"
    "bTt3aGl0ZS1zcGFjZTpub3dyYXA7Y3Vyc29yOnBvaW50ZXI7fQouYmFsY2hpcCAucGx1c3tkaXNw"
    "bGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO3dpZHRoOjIwcHg7aGVpZ2h0OjIwcHg7Ym9yZGVy"
    "LXJhZGl1czo3cHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmRmODtmb250"
    "LXdlaWdodDo4MDA7Zm9udC1zaXplOi44cmVtO2xpbmUtaGVpZ2h0OjF9CgouY2FyZHtiYWNrZ3Jv"
    "dW5kOnZhcigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJhZGl1"
    "czp2YXIoLS1yKTtwYWRkaW5nOjE2cHg7bWFyZ2luLWJvdHRvbToxMnB4O2JveC1zaGFkb3c6dmFy"
    "KC0tc2hhZG93KTt9Ci5jYXJkLnRpZ2h0e3BhZGRpbmc6MTNweH0KLmNhcmQgaDN7bWFyZ2luOjAg"
    "MCAzcHg7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZTouOTJyZW07Zm9udC13ZWln"
    "aHQ6NjAwO2xldHRlci1zcGFjaW5nOjB9Ci5jYXJkIC5zdWJ7bWFyZ2luOjA7Y29sb3I6dmFyKC0t"
    "dHgyKTtmb250LXNpemU6Ljc4cmVtfQouc2Vje2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50"
    "ZXI7anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47Z2FwOjEwcHg7bWFyZ2luOjE4cHggMnB4"
    "IDEwcHh9Ci5zZWMgaDJ7bWFyZ2luOjA7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6"
    "ZTouODJyZW07Zm9udC13ZWlnaHQ6NjAwO2xldHRlci1zcGFjaW5nOi4xNGVtO3RleHQtdHJhbnNm"
    "b3JtOnVwcGVyY2FzZTtjb2xvcjp2YXIoLS10eDIpfQouc2VjIGF7Zm9udC1zaXplOi43NXJlbTtj"
    "b2xvcjp2YXIoLS1hY2MpO3RleHQtZGVjb3JhdGlvbjpub25lO2ZvbnQtd2VpZ2h0OjYwMDt3aGl0"
    "ZS1zcGFjZTpub3dyYXA7Y3Vyc29yOnBvaW50ZXJ9CgovKiBvbmUgYnV0dG9uIHN5c3RlbSDigJQg"
    "dGV4dCBjYW4gbmV2ZXIgb3ZlcmZsb3cgKi8KLmJ0bntkaXNwbGF5OmlubGluZS1mbGV4O2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2dhcDo3cHg7aGVpZ2h0OnZhcigt"
    "LWJ0bmgpO21pbi13aWR0aDowO21heC13aWR0aDoxMDAlO3BhZGRpbmc6MCAxNnB4O2JvcmRlcjow"
    "O2N1cnNvcjpwb2ludGVyO2JvcmRlci1yYWRpdXM6N3B4O2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQp"
    "O2ZvbnQtc2l6ZTouODNyZW07Zm9udC13ZWlnaHQ6NzAwO2xldHRlci1zcGFjaW5nOi4wMWVtO3do"
    "aXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRkZW47dGV4dC1vdmVyZmxvdzplbGxpcHNpczts"
    "aW5lLWhlaWdodDoxO2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiNmZmZkZjg7dHJh"
    "bnNpdGlvbjp0cmFuc2Zvcm0gLjE1cyxmaWx0ZXIgLjE1czt9Ci5idG46YWN0aXZle3RyYW5zZm9y"
    "bTpzY2FsZSguOTcpfQouYnRuOmhvdmVye2ZpbHRlcjpicmlnaHRuZXNzKDEuMDYpfQouYnRuIHN2"
    "Z3t3aWR0aDoxNnB4O2hlaWdodDoxNnB4O2ZsZXg6MCAwIGF1dG99Ci5idG4uYmxvY2t7ZGlzcGxh"
    "eTpmbGV4O3dpZHRoOjEwMCV9Ci5idG4uc217aGVpZ2h0OjM2cHg7cGFkZGluZzowIDEycHg7Zm9u"
    "dC1zaXplOi43NXJlbTtib3JkZXItcmFkaXVzOjZweH0KLmJ0bi54c3toZWlnaHQ6MzBweDtwYWRk"
    "aW5nOjAgMTBweDtmb250LXNpemU6LjdyZW07Ym9yZGVyLXJhZGl1czo2cHh9Ci5idG4uZ2hvc3R7"
    "YmFja2dyb3VuZDp0cmFuc3BhcmVudDtjb2xvcjp2YXIoLS10eCk7Ym9yZGVyOjFweCBzb2xpZCB2"
    "YXIoLS1saW5lKX0KLmJ0bi5zb2Z0e2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2NvbG9yOnZhcigt"
    "LXR4KTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpfQouYnRuLmJhZHtiYWNrZ3JvdW5kOnRy"
    "YW5zcGFyZW50O2NvbG9yOnZhcigtLWJhZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1iYWQpfQou"
    "YnRuW2Rpc2FibGVkXXtvcGFjaXR5Oi40NTtwb2ludGVyLWV2ZW50czpub25lfQouYnRucm93e2Rp"
    "c3BsYXk6ZmxleDtnYXA6OHB4fQouYnRucm93Pi5idG57ZmxleDoxIDEgMDttaW4td2lkdGg6MH0K"
    "Ci5waWxse2Rpc3BsYXk6aW5saW5lLWZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo1cHg7aGVp"
    "Z2h0OjI0cHg7cGFkZGluZzowIDlweDtib3JkZXItcmFkaXVzOjk5cHg7Zm9udC1zaXplOi42NnJl"
    "bTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LjA0ZW07YmFja2dyb3VuZDp2YXIoLS1j"
    "YXJkMik7Y29sb3I6dmFyKC0tdHgyKTtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO3doaXRl"
    "LXNwYWNlOm5vd3JhcH0KLnBpbGwub257Y29sb3I6dmFyKC0tb2spO2JvcmRlci1jb2xvcjpyZ2Jh"
    "KDc1LDEyMiw3NiwuNDUpfQoucGlsbC5ob3R7Y29sb3I6dmFyKC0td2Fybik7Ym9yZGVyLWNvbG9y"
    "OnJnYmEoMTY5LDEyMiwzNiwuNDUpfQoucGlsbC5vZmZ7Y29sb3I6dmFyKC0tYmFkKTtib3JkZXIt"
    "Y29sb3I6cmdiYSgxNjYsNzUsNjksLjQ1KX0KLmRvdHt3aWR0aDo3cHg7aGVpZ2h0OjdweDtib3Jk"
    "ZXItcmFkaXVzOjUwJTtiYWNrZ3JvdW5kOnZhcigtLW9rKTtib3gtc2hhZG93OjAgMCA4cHggdmFy"
    "KC0tb2spfQoua3Z7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRl"
    "bnQ6c3BhY2UtYmV0d2VlbjtnYXA6MTJweDtwYWRkaW5nOjEwcHggMDtib3JkZXItYm90dG9tOjFw"
    "eCBzb2xpZCB2YXIoLS1saW5lKTtmb250LXNpemU6LjgycmVtO2NvbG9yOnZhcigtLXR4Mil9Ci5r"
    "djpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206MH0KLmt2IGJ7Y29sb3I6dmFyKC0tdHgpO2ZvbnQt"
    "d2VpZ2h0OjcwMDt0ZXh0LWFsaWduOnJpZ2h0O292ZXJmbG93LXdyYXA6YW55d2hlcmV9Ci5mbGFn"
    "e3dpZHRoOjQycHg7aGVpZ2h0OjQycHg7Ym9yZGVyLXJhZGl1czo4cHg7ZGlzcGxheTpncmlkO3Bs"
    "YWNlLWl0ZW1zOmNlbnRlcjtmb250LXNpemU6MS4zNXJlbTtmbGV4OjAgMCBhdXRvO2JhY2tncm91"
    "bmQ6dmFyKC0tY2FyZDIpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSl9Ci5mbGFnIHN2Z3t3"
    "aWR0aDoxOHB4O2hlaWdodDoxOHB4fQoKLmhlcm97cG9zaXRpb246cmVsYXRpdmU7b3ZlcmZsb3c6"
    "aGlkZGVuO2JvcmRlci1yYWRpdXM6MTBweDtwYWRkaW5nOjIycHggMThweDttYXJnaW46NHB4IDAg"
    "MTRweDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JhY2tncm91bmQ6I2ZmZmRmODtib3Jk"
    "ZXItc3R5bGU6ZGFzaGVkfQouaGVybyAua2lja3tmb250LXNpemU6LjZyZW07bGV0dGVyLXNwYWNp"
    "bmc6LjJlbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7Y29sb3I6dmFyKC0tYWNjKTtmb250LXdl"
    "aWdodDo4MDB9Ci5oZXJvIGgxe21hcmdpbjo4cHggMCA2cHg7Zm9udC1mYW1pbHk6dmFyKC0tZmhl"
    "YWQpO2ZvbnQtc2l6ZToxLjc1cmVtO2xpbmUtaGVpZ2h0OjEuMDU7bGV0dGVyLXNwYWNpbmc6MDtm"
    "b250LXdlaWdodDo2MDB9Ci5oZXJvIHB7bWFyZ2luOjAgMCAxNHB4O2ZvbnQtc2l6ZTouOHJlbTtj"
    "b2xvcjp2YXIoLS10eDIpO21heC13aWR0aDoyODBweH0KLmhlcm8gLmJ0bnJvd3ttYXgtd2lkdGg6"
    "MzMwcHh9CgouZ3JpZHtkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjFmciAxZnI7"
    "Z2FwOjEwcHh9Ci5wY2FyZHtiYWNrZ3JvdW5kOnZhcigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQg"
    "dmFyKC0tbGluZSk7Ym9yZGVyLXJhZGl1czp2YXIoLS1yKTtwYWRkaW5nOjE0cHg7Ym94LXNoYWRv"
    "dzp2YXIoLS1zaGFkb3cpO2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47Z2FwOjlw"
    "eDtjdXJzb3I6cG9pbnRlcjt9Ci5wY2FyZCAudG9we2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpj"
    "ZW50ZXI7anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47Z2FwOjhweH0KLnBjYXJkIC5ubXtm"
    "b250LXdlaWdodDo3MDA7Zm9udC1zaXplOi44NXJlbTt3aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZs"
    "b3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXN9Ci5wY2FyZCAubWV0YXtmb250LXNpemU6"
    "LjY4cmVtO2NvbG9yOnZhcigtLXR4Mil9Ci5wY2FyZCAucHJpY2V7Zm9udC1mYW1pbHk6dmFyKC0t"
    "ZmhlYWQpO2ZvbnQtc2l6ZToxLjI1cmVtO2ZvbnQtd2VpZ2h0OjYwMDtsZXR0ZXItc3BhY2luZzot"
    "LjAyZW19Ci5wY2FyZCAucHJpY2Ugc3tmb250LXNpemU6LjcycmVtO2NvbG9yOnZhcigtLXR4Mik7"
    "Zm9udC13ZWlnaHQ6NDAwO21hcmdpbi1yaWdodDo2cHh9Ci5yb3dze2Rpc3BsYXk6ZmxleDtmbGV4"
    "LWRpcmVjdGlvbjpjb2x1bW47Z2FwOjlweH0KLnJvd3tkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6"
    "Y2VudGVyO2dhcDoxMnB4O2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2"
    "YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXIpO3BhZGRpbmc6MTJweCAxM3B4O2JveC1z"
    "aGFkb3c6dmFyKC0tc2hhZG93KTtjdXJzb3I6cG9pbnRlcjt0cmFuc2l0aW9uOnRyYW5zZm9ybSAu"
    "MTVzO30KLnJvdzphY3RpdmV7dHJhbnNmb3JtOnNjYWxlKC45OSl9Ci5yb3cgLm1pZHtmbGV4OjE7"
    "bWluLXdpZHRoOjB9Ci5yb3cgLm1pZCBie2Rpc3BsYXk6YmxvY2s7Zm9udC1zaXplOi44OHJlbTt3"
    "aGl0ZS1zcGFjZTpub3dyYXA7b3ZlcmZsb3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXN9"
    "Ci5yb3cgLm1pZCBzbWFsbHtkaXNwbGF5OmJsb2NrO2ZvbnQtc2l6ZTouN3JlbTtjb2xvcjp2YXIo"
    "LS10eDIpO21hcmdpbi10b3A6MnB4fQoucm93IC5ydHt0ZXh0LWFsaWduOnJpZ2h0O2ZsZXg6MCAw"
    "IGF1dG99Ci5yb3cgLnJ0IC5we2ZvbnQtd2VpZ2h0OjgwMDtmb250LXNpemU6Ljk1cmVtO3doaXRl"
    "LXNwYWNlOm5vd3JhcH0KLnJvdyAucnQgLnAgc3tmb250LXdlaWdodDo0MDA7Zm9udC1zaXplOi43"
    "MnJlbTtjb2xvcjp2YXIoLS10eDIpO21hcmdpbi1yaWdodDo1cHh9Ci5jYXJvdXtkaXNwbGF5OmZs"
    "ZXg7Z2FwOjEwcHg7b3ZlcmZsb3cteDphdXRvO3BhZGRpbmc6MnB4IDAgNnB4O21hcmdpbjowIC0x"
    "NnB4IDZweDtwYWRkaW5nLWxlZnQ6MTZweDtwYWRkaW5nLXJpZ2h0OjE2cHg7c2Nyb2xsLXNuYXAt"
    "dHlwZTp4IG1hbmRhdG9yeX0KLmNhcm91Pip7ZmxleDowIDAgMTcycHg7c2Nyb2xsLXNuYXAtYWxp"
    "Z246c3RhcnR9CgpuYXYjdGFic3twb3NpdGlvbjpmaXhlZDt6LWluZGV4OjYwO2xlZnQ6NTAlO2Jv"
    "dHRvbTpjYWxjKDEycHggKyBlbnYoc2FmZS1hcmVhLWluc2V0LWJvdHRvbSkpO3RyYW5zZm9ybTp0"
    "cmFuc2xhdGVYKC01MCUpO21heC13aWR0aDpjYWxjKDEwMHZ3IC0gMjBweCl9Cm5hdiN0YWJzIC5k"
    "b2Nre2Rpc3BsYXk6ZmxleDtnYXA6MnB4O3BhZGRpbmc6N3B4IDhweDtib3JkZXItcmFkaXVzOjEw"
    "cHg7YmFja2dyb3VuZDojZmZmZGY4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym94LXNo"
    "YWRvdzowIDRweCAxNHB4IHJnYmEoMzUsMzMsMjgsLjA4KX0KbmF2I3RhYnMgYnV0dG9ue3Bvc2l0"
    "aW9uOnJlbGF0aXZlO2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47YWxpZ24taXRl"
    "bXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Z2FwOjRweDttaW4td2lkdGg6NThweDto"
    "ZWlnaHQ6NDhweDtwYWRkaW5nOjAgNnB4O2JvcmRlcjowO2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7"
    "Y3Vyc29yOnBvaW50ZXI7Y29sb3I6dmFyKC0tdHgyKTtmb250LWZhbWlseTp2YXIoLS1mb250KTtm"
    "b250LXNpemU6LjZyZW07Zm9udC13ZWlnaHQ6NzAwO2xldHRlci1zcGFjaW5nOi4wM2VtO2JvcmRl"
    "ci1yYWRpdXM6N3B4O3RyYW5zaXRpb246Y29sb3IgLjJzLGJhY2tncm91bmQgLjJzO3doaXRlLXNw"
    "YWNlOm5vd3JhcH0KbmF2I3RhYnMgYnV0dG9uIHN2Z3t3aWR0aDoxOXB4O2hlaWdodDoxOXB4fQpu"
    "YXYjdGFicyBidXR0b24ub257Y29sb3I6I2ZmZmRmODtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFk"
    "KX0KCi5zdWJuYXZ7ZGlzcGxheTpmbGV4O2dhcDo3cHg7b3ZlcmZsb3cteDphdXRvO3BhZGRpbmc6"
    "MnB4IDAgMTJweDttYXJnaW46MCAtMnB4fQouc3VibmF2IGJ1dHRvbntmbGV4OjAgMCBhdXRvO2hl"
    "aWdodDozNHB4O3BhZGRpbmc6MCAxM3B4O2JvcmRlci1yYWRpdXM6NnB4O2JvcmRlcjoxcHggc29s"
    "aWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgyKTtm"
    "b250LWZhbWlseTp2YXIoLS1mb250KTtmb250LXNpemU6Ljc0cmVtO2ZvbnQtd2VpZ2h0OjcwMDtj"
    "dXJzb3I6cG9pbnRlcjt3aGl0ZS1zcGFjZTpub3dyYXB9Ci5zdWJuYXYgYnV0dG9uLm9ue2JhY2tn"
    "cm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiNmZmZkZjg7Ym9yZGVyLWNvbG9yOnRyYW5zcGFy"
    "ZW50fQoua3Bpc3tkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjFmciAxZnI7Z2Fw"
    "OjEwcHg7bWFyZ2luLWJvdHRvbToxMnB4fQoua3Bpe2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9y"
    "ZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3JkZXItcmFkaXVzOnZhcigtLXJzKTtwYWRkaW5n"
    "OjEzcHg7Ym94LXNoYWRvdzp2YXIoLS1zaGFkb3cpfQoua3BpIHNtYWxse2Rpc3BsYXk6YmxvY2s7"
    "Zm9udC1zaXplOi42NHJlbTtjb2xvcjp2YXIoLS10eDIpO2xldHRlci1zcGFjaW5nOi4xZW07dGV4"
    "dC10cmFuc2Zvcm06dXBwZXJjYXNlfQoua3BpIGJ7ZGlzcGxheTpibG9jazttYXJnaW4tdG9wOjVw"
    "eDtmb250LWZhbWlseTp2YXIoLS1maGVhZCk7Zm9udC1zaXplOjEuMzVyZW07Zm9udC13ZWlnaHQ6"
    "NjAwO2xldHRlci1zcGFjaW5nOi0uMDJlbX0KLmlucCxzZWxlY3QuaW5we3dpZHRoOjEwMCU7aGVp"
    "Z2h0OnZhcigtLWJ0bmgpO3BhZGRpbmc6MCAxM3B4O2JvcmRlci1yYWRpdXM6N3B4O2JvcmRlcjox"
    "cHggc29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0t"
    "dHgpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouODNyZW07b3V0bGluZTpub25l"
    "Oy13ZWJraXQtYXBwZWFyYW5jZTpub25lO2FwcGVhcmFuY2U6bm9uZX0Kc2VsZWN0LmlucHtiYWNr"
    "Z3JvdW5kLWltYWdlOm5vbmV9Ci5pbnA6OnBsYWNlaG9sZGVye2NvbG9yOnZhcigtLXR4Mik7b3Bh"
    "Y2l0eTouNzV9Ci5pbnA6Zm9jdXN7Ym9yZGVyLWNvbG9yOnZhcigtLWFjYyl9CnRleHRhcmVhLmlu"
    "cHtoZWlnaHQ6YXV0bzttaW4taGVpZ2h0OjQwcHg7cGFkZGluZzoxMHB4IDEzcHg7bGluZS1oZWln"
    "aHQ6MS41O3Jlc2l6ZTp2ZXJ0aWNhbH0KLmNtc3Jvdy5oaWRle2Rpc3BsYXk6bm9uZX0KLmljb25j"
    "aGlwe3Bvc2l0aW9uOnJlbGF0aXZlO2hlaWdodDozNHB4O21pbi13aWR0aDozNHB4O3BhZGRpbmc6"
    "MCA5cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7YmFj"
    "a2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgpO2Rpc3BsYXk6aW5saW5lLWdyaWQ7"
    "cGxhY2UtaXRlbXM6Y2VudGVyO2N1cnNvcjpwb2ludGVyfQouaWNvbmNoaXAgc3Zne3dpZHRoOjE3"
    "cHg7aGVpZ2h0OjE3cHh9Ci5pY29uY2hpcCBpe3Bvc2l0aW9uOmFic29sdXRlO3RvcDotNXB4O3Jp"
    "Z2h0Oi01cHg7bWluLXdpZHRoOjE3cHg7aGVpZ2h0OjE3cHg7cGFkZGluZzowIDRweDtib3JkZXIt"
    "cmFkaXVzOjk5cHg7YmFja2dyb3VuZDp2YXIoLS1hY2MpO2NvbG9yOiNmZmZkZjg7Zm9udC1zaXpl"
    "Oi42cmVtO2ZvbnQtc3R5bGU6bm9ybWFsO2ZvbnQtd2VpZ2h0OjgwMDtkaXNwbGF5OmdyaWQ7cGxh"
    "Y2UtaXRlbXM6Y2VudGVyfQouc3RhcnN7ZGlzcGxheTppbmxpbmUtZmxleDtnYXA6MXB4O3ZlcnRp"
    "Y2FsLWFsaWduOm1pZGRsZX0KLnN0YXJzIHN2Z3t3aWR0aDoxMXB4O2hlaWdodDoxMXB4O2NvbG9y"
    "OiNmNWE2MjN9Ci5zdGFycyBie2ZvbnQtc2l6ZTouNjZyZW07Y29sb3I6dmFyKC0tdHgyKTtmb250"
    "LXdlaWdodDo3MDA7bWFyZ2luLWxlZnQ6M3B4fQouaGVhcnR7cG9zaXRpb246YWJzb2x1dGU7dG9w"
    "OjhweDtyaWdodDo4cHg7d2lkdGg6MjZweDtoZWlnaHQ6MjZweDtib3JkZXItcmFkaXVzOjk5cHg7"
    "ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxp"
    "bmUpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2N1cnNvcjpwb2ludGVyO3otaW5kZXg6Mn0KLmhl"
    "YXJ0IHN2Z3t3aWR0aDoxM3B4O2hlaWdodDoxM3B4O2NvbG9yOnZhcigtLXR4Mil9Ci5oZWFydC5v"
    "bntiYWNrZ3JvdW5kOnZhcigtLWFjYyk7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50fQouaGVhcnQu"
    "b24gc3Zne2NvbG9yOiNmZmZkZjh9Ci5xdHl7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRl"
    "cjtnYXA6MDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpO2JvcmRlci1yYWRpdXM6N3B4O292"
    "ZXJmbG93OmhpZGRlbn0KLnF0eSBidXR0b257d2lkdGg6MzhweDtoZWlnaHQ6MzhweDtib3JkZXI6"
    "MDtiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eCk7Zm9udC1zaXplOjFyZW07"
    "Zm9udC13ZWlnaHQ6ODAwO2N1cnNvcjpwb2ludGVyfQoucXR5IGJ7bWluLXdpZHRoOjM4cHg7dGV4"
    "dC1hbGlnbjpjZW50ZXI7Zm9udC1zaXplOi44NXJlbX0KLnRse21hcmdpbjoxMHB4IDAgMDtwYWRk"
    "aW5nLWxlZnQ6MTZweDtib3JkZXItbGVmdDoycHggc29saWQgdmFyKC0tbGluZSl9Ci50bCBkaXZ7"
    "cG9zaXRpb246cmVsYXRpdmU7cGFkZGluZzowIDAgMTFweCAxMHB4O2ZvbnQtc2l6ZTouNzRyZW07"
    "Y29sb3I6dmFyKC0tdHgyKX0KLnRsIGRpdjpsYXN0LWNoaWxke3BhZGRpbmctYm90dG9tOjB9Ci50"
    "bCBkaXY6OmJlZm9yZXtjb250ZW50OiIiO3Bvc2l0aW9uOmFic29sdXRlO2xlZnQ6LTIxcHg7dG9w"
    "OjRweDt3aWR0aDo4cHg7aGVpZ2h0OjhweDtib3JkZXItcmFkaXVzOjk5cHg7YmFja2dyb3VuZDp2"
    "YXIoLS1saW5lKX0KLnRsIGRpdi5vazo6YmVmb3Jle2JhY2tncm91bmQ6dmFyKC0tb2spfQoudGwg"
    "ZGl2Lm9rIGJ7Y29sb3I6dmFyKC0tdHgpfQoudGwgc21hbGx7ZGlzcGxheTpibG9jaztmb250LXNp"
    "emU6LjY2cmVtO29wYWNpdHk6Ljh9Ci5mY2FyZHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Zmxl"
    "eC1zdGFydDtnYXA6MTFweDtwYWRkaW5nOjExcHggMDtib3JkZXItYm90dG9tOjFweCBzb2xpZCB2"
    "YXIoLS1saW5lKX0KLmZjYXJkOmxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRvbTowfQouZmNhcmQgLnR7"
    "ZmxleDoxO21pbi13aWR0aDowfQouZmNhcmQgLnQgYntmb250LXNpemU6LjgxcmVtO2Rpc3BsYXk6"
    "YmxvY2t9Ci5mY2FyZCAudCBzbWFsbHtkaXNwbGF5OmJsb2NrO2ZvbnQtc2l6ZTouNjlyZW07Y29s"
    "b3I6dmFyKC0tdHgyKTtsaW5lLWhlaWdodDoxLjQ1O21hcmdpbi10b3A6MnB4fQouZnJvdy5oaWRl"
    "LC5mZ3JwLmhpZGV7ZGlzcGxheTpub25lfQouYmFyc3tkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6"
    "ZmxleC1lbmQ7Z2FwOjVweDtoZWlnaHQ6ODZweDttYXJnaW4tdG9wOjEycHh9Ci5iYXJzIGl7Zmxl"
    "eDoxO2JvcmRlci1yYWRpdXM6NnB4IDZweCAzcHggM3B4O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdy"
    "YWQpO2Rpc3BsYXk6YmxvY2t9Ci5mdW5uZWwgZGl2e2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpj"
    "ZW50ZXI7Z2FwOjlweDttYXJnaW4tdG9wOjlweDtmb250LXNpemU6Ljc0cmVtfQouZnVubmVsIGl7"
    "aGVpZ2h0OjI2cHg7Ym9yZGVyLXJhZGl1czo3cHg7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7"
    "ZGlzcGxheTpibG9ja30KLmZlZWRsaW5le2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7"
    "Z2FwOjlweDtmb250LXNpemU6LjczcmVtO2NvbG9yOnZhcigtLXR4Mik7cGFkZGluZzo3cHggMDti"
    "b3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIoLS1saW5lKX0KLmZlZWRsaW5lOmxhc3QtY2hpbGR7"
    "Ym9yZGVyLWJvdHRvbTowfQouZmVlZGxpbmUgYntjb2xvcjp2YXIoLS10eCl9Ci5sYntkaXNwbGF5"
    "OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4O3BhZGRpbmc6OXB4IDA7Ym9yZGVyLWJv"
    "dHRvbToxcHggc29saWQgdmFyKC0tbGluZSl9Ci5sYjpsYXN0LWNoaWxke2JvcmRlci1ib3R0b206"
    "MH0KLmxiIC5ya3t3aWR0aDoyNHB4O2hlaWdodDoyNHB4O2JvcmRlci1yYWRpdXM6OTlweDtkaXNw"
    "bGF5OmdyaWQ7cGxhY2UtaXRlbXM6Y2VudGVyO2ZvbnQtc2l6ZTouN3JlbTtmb250LXdlaWdodDo4"
    "MDA7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Y29sb3I6dmFyKC0tdHgyKX0KLmxiIC5yay5ne2Jh"
    "Y2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiNmZmZkZjh9Ci5jbXNncnAuaGlkZXtkaXNw"
    "bGF5Om5vbmV9Ci5jbXNyb3cgbGFiZWwgYntjb2xvcjp2YXIoLS1hY2MpO2ZvbnQtd2VpZ2h0Ojcw"
    "MH0KLnRpbWVyYmFye2hlaWdodDo1cHg7Ym9yZGVyLXJhZGl1czo5OXB4O2JhY2tncm91bmQ6dmFy"
    "KC0tY2FyZDIpO292ZXJmbG93OmhpZGRlbjttYXJnaW46MTBweCAwIDJweH0KLnRpbWVyYmFyIGl7"
    "ZGlzcGxheTpibG9jaztoZWlnaHQ6MTAwJTtiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTt0cmFu"
    "c2l0aW9uOndpZHRoIDFzIGxpbmVhcn0KLnRpbWVyYmFyLmxvdyBpe2JhY2tncm91bmQ6dmFyKC0t"
    "YmFkLCNlNTQ4NGQpfQouZmllbGR7bWFyZ2luLWJvdHRvbTo5cHh9Ci5maWVsZCBsYWJlbHtkaXNw"
    "bGF5OmJsb2NrO2ZvbnQtc2l6ZTouNjhyZW07Y29sb3I6dmFyKC0tdHgyKTtsZXR0ZXItc3BhY2lu"
    "ZzouMDllbTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7bWFyZ2luOjAgMCA1cHggMnB4O2ZvbnQt"
    "d2VpZ2h0OjcwMH0KLnN3YXB7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5"
    "LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjtnYXA6MTJweDtwYWRkaW5nOjExcHggMDtib3JkZXItYm90"
    "dG9tOjFweCBzb2xpZCB2YXIoLS1saW5lKX0KLnN3YXA6bGFzdC1jaGlsZHtib3JkZXItYm90dG9t"
    "OjB9Ci5zd2FwIC50e2ZvbnQtc2l6ZTouODJyZW07Zm9udC13ZWlnaHQ6NjAwO21pbi13aWR0aDow"
    "fQouc3dhcCAudCBzbWFsbHtkaXNwbGF5OmJsb2NrO2ZvbnQtd2VpZ2h0OjQwMDtmb250LXNpemU6"
    "LjdyZW07Y29sb3I6dmFyKC0tdHgyKX0KLnRne3dpZHRoOjQ0cHg7aGVpZ2h0OjI2cHg7Ym9yZGVy"
    "LXJhZGl1czo5OXB4O2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2JvcmRlcjoxcHggc29saWQgdmFy"
    "KC0tbGluZSk7cG9zaXRpb246cmVsYXRpdmU7Y3Vyc29yOnBvaW50ZXI7ZmxleDowIDAgYXV0b30K"
    "LnRnOmFmdGVye2NvbnRlbnQ6IiI7cG9zaXRpb246YWJzb2x1dGU7dG9wOjNweDtsZWZ0OjNweDt3"
    "aWR0aDoxOHB4O2hlaWdodDoxOHB4O2JvcmRlci1yYWRpdXM6NTAlO2JhY2tncm91bmQ6dmFyKC0t"
    "dHgyKTt0cmFuc2l0aW9uOi4yMnN9Ci50Zy5vbntiYWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTti"
    "b3JkZXItY29sb3I6dHJhbnNwYXJlbnR9Ci50Zy5vbjphZnRlcntsZWZ0OjIxcHg7YmFja2dyb3Vu"
    "ZDojZmZmZGY4fQoudGcuc217d2lkdGg6MzZweDtoZWlnaHQ6MjFweH0KLnRnLnNtOmFmdGVye3dp"
    "ZHRoOjE0cHg7aGVpZ2h0OjE0cHg7dG9wOjIuNXB4fQoudGcuc20ub246YWZ0ZXJ7bGVmdDoxOHB4"
    "fQouc3Rhcnt3aWR0aDozMHB4O2hlaWdodDozMHB4O2JvcmRlci1yYWRpdXM6OXB4O2Rpc3BsYXk6"
    "Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNr"
    "Z3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIpO2N1cnNvcjpwb2ludGVyO2ZsZXg6"
    "MCAwIGF1dG99Ci5zdGFyLm9ue2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiNmZmZk"
    "Zjg7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50fQouc3RhciBzdmd7d2lkdGg6MTRweDtoZWlnaHQ6"
    "MTRweH0KCiNzaGVldHtwb3NpdGlvbjpmaXhlZDtpbnNldDowO3otaW5kZXg6OTA7ZGlzcGxheTpu"
    "b25lfQojc2hlZXQub3BlbntkaXNwbGF5OmJsb2NrfQojc2hlZXQgLmJne3Bvc2l0aW9uOmFic29s"
    "dXRlO2luc2V0OjA7YmFja2dyb3VuZDpyZ2JhKDM1LDMzLDI4LC4zNSk7YmFja2Ryb3AtZmlsdGVy"
    "OmJsdXIoM3B4KX0KI3NoZWV0IC5wYW5lbHtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjUwJTtib3R0"
    "b206MDt0cmFuc2Zvcm06dHJhbnNsYXRlWCgtNTAlKTt3aWR0aDoxMDAlO21heC13aWR0aDo1MjBw"
    "eDtiYWNrZ3JvdW5kOnZhcigtLWJnMik7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtib3Jk"
    "ZXItYm90dG9tOjA7Ym9yZGVyLXJhZGl1czoxMnB4IDEycHggMCAwO3BhZGRpbmc6MThweCAxNnB4"
    "IGNhbGMoMjBweCArIGVudihzYWZlLWFyZWEtaW5zZXQtYm90dG9tKSk7YW5pbWF0aW9uOnVwIC4y"
    "OHMgY3ViaWMtYmV6aWVyKC4yLC45LC4zLDEpO21heC1oZWlnaHQ6OTJ2aDtvdmVyZmxvdzphdXRv"
    "fQpAa2V5ZnJhbWVzIHVwe2Zyb217dHJhbnNmb3JtOnRyYW5zbGF0ZSgtNTAlLDEwMCUpfXRve3Ry"
    "YW5zZm9ybTp0cmFuc2xhdGUoLTUwJSwwKX19CiNzaGVldCAuZ3JhYnt3aWR0aDozOHB4O2hlaWdo"
    "dDo0cHg7Ym9yZGVyLXJhZGl1czo5cHg7YmFja2dyb3VuZDp2YXIoLS1saW5lKTttYXJnaW46MCBh"
    "dXRvIDE0cHh9Ci5vdHBib3h7Zm9udC1mYW1pbHk6dWktbW9ub3NwYWNlLE1lbmxvLENvbnNvbGFz"
    "LG1vbm9zcGFjZTtmb250LXNpemU6MS42cmVtO2xldHRlci1zcGFjaW5nOi4zNWVtO3RleHQtYWxp"
    "Z246Y2VudGVyO3BhZGRpbmc6MTZweDtib3JkZXItcmFkaXVzOnZhcigtLXJzKTtiYWNrZ3JvdW5k"
    "OnZhcigtLWNhcmQyKTtib3JkZXI6MXB4IGRhc2hlZCB2YXIoLS1saW5lKTttYXJnaW46MTBweCAw"
    "fQoucXJ7d2lkdGg6MTUwcHg7aGVpZ2h0OjE1MHB4O21hcmdpbjo2cHggYXV0byAxMHB4O2JvcmRl"
    "ci1yYWRpdXM6dmFyKC0tcnMpO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2JvcmRlcjoxcHggc29s"
    "aWQgdmFyKC0tbGluZSk7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtjb2xvcjp2YXIo"
    "LS10eDIpO2ZvbnQtc2l6ZTouNjVyZW07bGV0dGVyLXNwYWNpbmc6LjFlbTt0ZXh0LWFsaWduOmNl"
    "bnRlcn0KLnN0ZXBze21hcmdpbjoxMnB4IDAgNHB4fQouc3RlcHtkaXNwbGF5OmZsZXg7YWxpZ24t"
    "aXRlbXM6Y2VudGVyO2dhcDoxMHB4O3BhZGRpbmc6OHB4IDA7Zm9udC1zaXplOi43OXJlbTtjb2xv"
    "cjp2YXIoLS10eDIpfQouc3RlcCBpe3dpZHRoOjIycHg7aGVpZ2h0OjIycHg7Ym9yZGVyLXJhZGl1"
    "czo1MCU7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtkaXNwbGF5OmdyaWQ7cGxhY2UtaXRl"
    "bXM6Y2VudGVyO2ZvbnQtc3R5bGU6bm9ybWFsO2ZvbnQtc2l6ZTouNjVyZW07Zm9udC13ZWlnaHQ6"
    "ODAwO2ZsZXg6MCAwIGF1dG99Ci5zdGVwLm9ue2NvbG9yOnZhcigtLXR4KX0KLnN0ZXAub24gaXti"
    "YWNrZ3JvdW5kOnZhcigtLXBpbGxncmFkKTtjb2xvcjojZmZmZGY4O2JvcmRlci1jb2xvcjp0cmFu"
    "c3BhcmVudH0KLnN0ZXAucnVuIGl7Ym9yZGVyLWNvbG9yOnZhcigtLWFjYyk7Y29sb3I6dmFyKC0t"
    "YWNjKTthbmltYXRpb246cHVsc2UgMXMgaW5maW5pdGV9CkBrZXlmcmFtZXMgcHVsc2V7NTAle29w"
    "YWNpdHk6LjM1fX0KLnRvYXN0e3Bvc2l0aW9uOmZpeGVkO2xlZnQ6NTAlO2JvdHRvbTpjYWxjKDk2"
    "cHggKyBlbnYoc2FmZS1hcmVhLWluc2V0LWJvdHRvbSkpO3RyYW5zZm9ybTp0cmFuc2xhdGVYKC01"
    "MCUpO3otaW5kZXg6MTIwO2JhY2tncm91bmQ6dmFyKC0tYmcyKTtib3JkZXI6MXB4IHNvbGlkIHZh"
    "cigtLWxpbmUpO2NvbG9yOnZhcigtLXR4KTtwYWRkaW5nOjExcHggMTZweDtib3JkZXItcmFkaXVz"
    "Ojk5cHg7Zm9udC1zaXplOi43OHJlbTtmb250LXdlaWdodDo2MDA7Ym94LXNoYWRvdzp2YXIoLS1z"
    "aGFkb3cpO29wYWNpdHk6MDtwb2ludGVyLWV2ZW50czpub25lO3RyYW5zaXRpb246b3BhY2l0eSAu"
    "MnMsdHJhbnNmb3JtIC4yczt3aGl0ZS1zcGFjZTpub3dyYXA7bWF4LXdpZHRoOjkwdnc7b3ZlcmZs"
    "b3c6aGlkZGVuO3RleHQtb3ZlcmZsb3c6ZWxsaXBzaXN9Ci50b2FzdC5zaG93e29wYWNpdHk6MTt0"
    "cmFuc2Zvcm06dHJhbnNsYXRlWCgtNTAlKSB0cmFuc2xhdGVZKC02cHgpfQoKLyogcHJldmlldyBj"
    "aHJvbWUg4oCUIG1vY2t1cCBvbmx5LCBvd24gc3RyaXAgc28gaXQgbmV2ZXIgb3ZlcmxhcHMgdGhl"
    "IFVJICovCiNzd3twb3NpdGlvbjpmaXhlZDt0b3A6MDtsZWZ0OjA7cmlnaHQ6MDtoZWlnaHQ6MzJw"
    "eDt6LWluZGV4OjIwMDtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29u"
    "dGVudDpjZW50ZXI7Z2FwOjVweDsKICBiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1MywyNDgsLjkyKTti"
    "b3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIoLS1saW5lKTtiYWNrZHJvcC1maWx0ZXI6Ymx1cigx"
    "MHB4KX0KI3N3IGVte2ZvbnQtc3R5bGU6bm9ybWFsO2ZvbnQtc2l6ZTouNTZyZW07bGV0dGVyLXNw"
    "YWNpbmc6LjE4ZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2NvbG9yOnZhcigtLXR4Mik7bWFy"
    "Z2luLXJpZ2h0OjRweH0KI3N3IGJ1dHRvbntoZWlnaHQ6MjJweDtwYWRkaW5nOjAgMTJweDtib3Jk"
    "ZXItcmFkaXVzOjk5cHg7Ym9yZGVyOjA7Y3Vyc29yOnBvaW50ZXI7YmFja2dyb3VuZDp0cmFuc3Bh"
    "cmVudDtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTou"
    "NnJlbTtmb250LXdlaWdodDo3MDA7bGV0dGVyLXNwYWNpbmc6LjA3ZW07dGV4dC10cmFuc2Zvcm06"
    "dXBwZXJjYXNlfQojc3cgYnV0dG9uLm9ue2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9y"
    "OiNmZmZkZjh9CgovKiDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDilZDi"
    "lZAgQVVST1JBIFVJIEtJVCDigJQgcG9ydGVkIGNvbXBvbmVudHMg4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ"
    "4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQ4pWQICovCkBwcm9wZXJ0eSAtLWdhe3N5bnRheDoiPGFu"
    "Z2xlPiI7aW5pdGlhbC12YWx1ZTowZGVnO2luaGVyaXRzOmZhbHNlfQpAcHJvcGVydHkgLS1ncHtz"
    "eW50YXg6IjxwZXJjZW50YWdlPiI7aW5pdGlhbC12YWx1ZTo1JTtpbmhlcml0czpmYWxzZX0KQHBy"
    "b3BlcnR5IC0tZ297c3ludGF4OiI8YW5nbGU+Ijtpbml0aWFsLXZhbHVlOjBkZWc7aW5oZXJpdHM6"
    "ZmFsc2V9CgovKiAyMyDCtyBTaGlueSBDVEEg4oCUIGNvbmljIGJvcmRlciArIGRvdCBtYXNrICsg"
    "aW5uZXIgc2hpbW1lciArIGJyZWF0aGUgKi8KLmJ0bi5jdGF7LS1kdXI6My4yczstLWhsOiNlOGRj"
    "YzI7cG9zaXRpb246cmVsYXRpdmU7aXNvbGF0aW9uOmlzb2xhdGU7Ym9yZGVyOjFweCBzb2xpZCB0"
    "cmFuc3BhcmVudDsKICBiYWNrZ3JvdW5kOmxpbmVhci1ncmFkaWVudCh2YXIoLS1idG5iZyksdmFy"
    "KC0tYnRuYmcpKSBwYWRkaW5nLWJveCwKICAgIGNvbmljLWdyYWRpZW50KGZyb20gY2FsYyh2YXIo"
    "LS1nYSkgLSB2YXIoLS1nbykpLHRyYW5zcGFyZW50LHZhcigtLWhsKSB2YXIoLS1ncCksCiAgICAg"
    "ICNmZmYgY2FsYyh2YXIoLS1ncCkqMiksdmFyKC0taGwpIGNhbGModmFyKC0tZ3ApKjMpLHRyYW5z"
    "cGFyZW50IGNhbGModmFyKC0tZ3ApKjQpKSBib3JkZXItYm94OwogIGJveC1zaGFkb3c6aW5zZXQg"
    "MCAwIDAgMXB4IHJnYmEoMjU1LDI1NSwyNTUsLjA4KTsKICB0cmFuc2l0aW9uOi0tZ28gLjhzIGN1"
    "YmljLWJlemllciguMjUsMSwuNSwxKSwtLWdwIC44cyBjdWJpYy1iZXppZXIoLjI1LDEsLjUsMSks"
    "dHJhbnNmb3JtIC4xNXM7CiAgYW5pbWF0aW9uOmdhIHZhcigtLWR1cikgbGluZWFyIGluZmluaXRl"
    "fQouYnRuLmN0YTpiZWZvcmUsLmJ0bi5jdGE6YWZ0ZXJ7Y29udGVudDoiIjtwb3NpdGlvbjphYnNv"
    "bHV0ZTtsZWZ0OjUwJTt0b3A6NTAlO3RyYW5zbGF0ZTotNTAlIC01MCU7ei1pbmRleDotMTtwb2lu"
    "dGVyLWV2ZW50czpub25lfQouYnRuLmN0YTpiZWZvcmV7d2lkdGg6Y2FsYygxMDAlIC0gNnB4KTto"
    "ZWlnaHQ6Y2FsYygxMDAlIC0gNnB4KTtib3JkZXItcmFkaXVzOmluaGVyaXQ7b3BhY2l0eTouMzU7"
    "CiAgYmFja2dyb3VuZDpyYWRpYWwtZ3JhZGllbnQoY2lyY2xlIGF0IDJweCAycHgsI2ZmZiAuNXB4"
    "LHRyYW5zcGFyZW50IDApIHBhZGRpbmctYm94O2JhY2tncm91bmQtc2l6ZTo0cHggNHB4OwogIC13"
    "ZWJraXQtbWFzay1pbWFnZTpjb25pYy1ncmFkaWVudChmcm9tIGNhbGModmFyKC0tZ2EpICsgNDVk"
    "ZWcpLCMwMDAsdHJhbnNwYXJlbnQgMTAlIDkwJSwjMDAwKTsKICBtYXNrLWltYWdlOmNvbmljLWdy"
    "YWRpZW50KGZyb20gY2FsYyh2YXIoLS1nYSkgKyA0NWRlZyksIzAwMCx0cmFuc3BhcmVudCAxMCUg"
    "OTAlLCMwMDApfQouYnRuLmN0YTphZnRlcnt3aWR0aDoxMDAlO2FzcGVjdC1yYXRpbzoxO29wYWNp"
    "dHk6LjU1OwogIGJhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KC01MGRlZyx0cmFuc3BhcmVudCx2"
    "YXIoLS1obCksdHJhbnNwYXJlbnQpOwogIC13ZWJraXQtbWFzay1pbWFnZTpyYWRpYWwtZ3JhZGll"
    "bnQoY2lyY2xlIGF0IGJvdHRvbSx0cmFuc3BhcmVudCA0MCUsIzAwMCk7CiAgbWFzay1pbWFnZTpy"
    "YWRpYWwtZ3JhZGllbnQoY2lyY2xlIGF0IGJvdHRvbSx0cmFuc3BhcmVudCA0MCUsIzAwMCk7CiAg"
    "YW5pbWF0aW9uOnNoaW1tZXIgdmFyKC0tZHVyKSBsaW5lYXIgaW5maW5pdGV9Ci5idG4uY3RhOmhv"
    "dmVyey0tZ3A6MjIlOy0tZ286OTVkZWd9Ci5idG4uY3RhOmFjdGl2ZXt0cmFuc2xhdGU6MCAxcHh9"
    "CkBrZXlmcmFtZXMgZ2F7dG97LS1nYTozNjBkZWd9fQpAa2V5ZnJhbWVzIHNoaW1tZXJ7dG97cm90"
    "YXRlOjM2MGRlZ319CgovKiAyMiDCtyBMaXF1aWQgZ2xhc3MgKi8KLmJ0bi5nbGFzcywuZ2xhc3Nj"
    "YXJke3Bvc2l0aW9uOnJlbGF0aXZlO2JhY2tncm91bmQ6Y29sb3ItbWl4KGluIHNyZ2IsdmFyKC0t"
    "Y2FyZDIpIDcwJSx0cmFuc3BhcmVudCk7CiAgYm9yZGVyOjFweCBzb2xpZCBjb2xvci1taXgoaW4g"
    "c3JnYiwjZmZmIDE2JSx2YXIoLS1saW5lKSk7Y29sb3I6dmFyKC0tdHgpOwogIGJhY2tkcm9wLWZp"
    "bHRlcjpibHVyKDE0cHgpIHNhdHVyYXRlKDEuNCk7LXdlYmtpdC1iYWNrZHJvcC1maWx0ZXI6Ymx1"
    "cigxNHB4KSBzYXR1cmF0ZSgxLjQpOwogIGJveC1zaGFkb3c6aW5zZXQgMXB4IDFweCAxcHggLS41"
    "cHggcmdiYSgyNTUsMjU1LDI1NSwuNTUpLGluc2V0IC0xcHggLTFweCAxcHggLS41cHggcmdiYSgy"
    "NTUsMjU1LDI1NSwuMyksCiAgICBpbnNldCAwIDAgOHB4IDZweCByZ2JhKDI1NSwyNTUsMjU1LC4w"
    "NyksMCAycHggMTBweCByZ2JhKDAsMCwwLC4xMil9Ci5idG4uZ2xhc3M6YWZ0ZXJ7Y29udGVudDoi"
    "Ijtwb3NpdGlvbjphYnNvbHV0ZTtpbnNldDoxcHggMXB4IGF1dG87aGVpZ2h0OjQyJTtib3JkZXIt"
    "cmFkaXVzOmluaGVyaXQ7CiAgYmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoMTgwZGVnLHJnYmEo"
    "MjU1LDI1NSwyNTUsLjM0KSx0cmFuc3BhcmVudCk7cG9pbnRlci1ldmVudHM6bm9uZX0KLmJ0bi5n"
    "bGFzczpob3Zlcnt0cmFuc2Zvcm06c2NhbGUoMS4wMyl9CgovKiBwcmVzcyByaXBwbGUgKGtpdCBk"
    "b2NrL2J1dHRvbiBmZWVsKSAqLwoucmlwe3Bvc2l0aW9uOmFic29sdXRlO2JvcmRlci1yYWRpdXM6"
    "NTAlO3RyYW5zZm9ybTp0cmFuc2xhdGUoLTUwJSwtNTAlKSBzY2FsZSgwKTtwb2ludGVyLWV2ZW50"
    "czpub25lOwogIGJhY2tncm91bmQ6cmFkaWFsLWdyYWRpZW50KGNpcmNsZSxyZ2JhKDI1NSwyNTUs"
    "MjU1LC41NSksdHJhbnNwYXJlbnQgNzAlKTthbmltYXRpb246cmlwIC41NXMgZWFzZS1vdXQgZm9y"
    "d2FyZHN9CkBrZXlmcmFtZXMgcmlwe3Rve3RyYW5zZm9ybTp0cmFuc2xhdGUoLTUwJSwtNTAlKSBz"
    "Y2FsZSgxKTtvcGFjaXR5OjB9fQoKLyogMjQgwrcgQW5pbWF0ZWQgYmFubmVyICsgMTcgwrcgbG9n"
    "byBjbG91ZCBtYXJxdWVlICovCi5iYW5uZXJ7cG9zaXRpb246cmVsYXRpdmU7b3ZlcmZsb3c6aGlk"
    "ZGVuO2JvcmRlci1yYWRpdXM6N3B4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7CiAgYmFj"
    "a2dyb3VuZDp2YXIoLS1jYXJkKTttYXJnaW46MnB4IDAgMTJweDtoZWlnaHQ6MzhweDtkaXNwbGF5"
    "OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyOwogIC13ZWJraXQtbWFzay1pbWFnZTpsaW5lYXItZ3Jh"
    "ZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCA4JSwjMDAwIDkyJSx0cmFuc3BhcmVudCk7CiAg"
    "bWFzay1pbWFnZTpsaW5lYXItZ3JhZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCA4JSwjMDAw"
    "IDkyJSx0cmFuc3BhcmVudCl9Ci5iYW5uZXIgLnRyYWNre2Rpc3BsYXk6ZmxleDtnYXA6MzBweDt3"
    "aGl0ZS1zcGFjZTpub3dyYXA7YW5pbWF0aW9uOm1hcnEgMjZzIGxpbmVhciBpbmZpbml0ZTt3aWxs"
    "LWNoYW5nZTp0cmFuc2Zvcm19Ci5iYW5uZXI6aG92ZXIgLnRyYWNre2FuaW1hdGlvbi1wbGF5LXN0"
    "YXRlOnBhdXNlZH0KLmJhbm5lciBzcGFue2ZvbnQtc2l6ZTouN3JlbTtjb2xvcjp2YXIoLS10eDIp"
    "O2ZvbnQtd2VpZ2h0OjYwMDtsZXR0ZXItc3BhY2luZzouMDNlbTtkaXNwbGF5OmlubGluZS1mbGV4"
    "O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6NnB4fQouYmFubmVyIHNwYW4gYntjb2xvcjp2YXIoLS1h"
    "Y2MpfQouYmFubmVyIHNwYW4gc3Zne3dpZHRoOjEzcHg7aGVpZ2h0OjEzcHg7Y29sb3I6dmFyKC0t"
    "YWNjKX0KQGtleWZyYW1lcyBtYXJxe3Rve3RyYW5zZm9ybTp0cmFuc2xhdGVYKC01MCUpfX0KLmNs"
    "b3Vke2Rpc3BsYXk6ZmxleDtnYXA6MjZweDtvdmVyZmxvdzpoaWRkZW47cGFkZGluZzo0cHggMCAy"
    "cHg7CiAgLXdlYmtpdC1tYXNrLWltYWdlOmxpbmVhci1ncmFkaWVudCg5MGRlZyx0cmFuc3BhcmVu"
    "dCwjMDAwIDEyJSwjMDAwIDg4JSx0cmFuc3BhcmVudCk7CiAgbWFzay1pbWFnZTpsaW5lYXItZ3Jh"
    "ZGllbnQoOTBkZWcsdHJhbnNwYXJlbnQsIzAwMCAxMiUsIzAwMCA4OCUsdHJhbnNwYXJlbnQpfQou"
    "Y2xvdWQgLnR7ZGlzcGxheTpmbGV4O2dhcDoyNnB4O2FuaW1hdGlvbjptYXJxIDMwcyBsaW5lYXIg"
    "aW5maW5pdGV9Ci5jbG91ZCBpe2ZvbnQtc3R5bGU6bm9ybWFsO2ZvbnQtc2l6ZTouNjhyZW07Zm9u"
    "dC13ZWlnaHQ6ODAwO2xldHRlci1zcGFjaW5nOi4xNGVtO2NvbG9yOnZhcigtLXR4Mik7b3BhY2l0"
    "eTouNTU7d2hpdGUtc3BhY2U6bm93cmFwfQoKLyogMDcgwrcgR3JhZGllbnQgdHJhY2luZyAqLwou"
    "dHJhY2V7ZGlzcGxheTpibG9jazt3aWR0aDoxMDAlO2hlaWdodDoxMHB4O21hcmdpbjotMnB4IDAg"
    "OHB4O292ZXJmbG93OnZpc2libGV9Ci50cmFjZSAuYmFzZXtzdHJva2U6dmFyKC0tbGluZSk7c3Ry"
    "b2tlLXdpZHRoOjEuNH0KLnRyYWNlIC5sZWFke3N0cm9rZS13aWR0aDoxLjg7c3Ryb2tlLWxpbmVj"
    "YXA6cm91bmR9Ci5zZWMgaDJ7cG9zaXRpb246cmVsYXRpdmV9CgovKiAwMSArIDEzIMK3IERvdCBs"
    "b2FkZXIgKi8KLmRvdGdyaWR7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBl"
    "YXQoNywxZnIpO2dhcDozcHg7d2lkdGg6NzBweH0KLmRvdGdyaWQgaXt3aWR0aDoxMDAlO2FzcGVj"
    "dC1yYXRpbzoxO2JvcmRlci1yYWRpdXM6MnB4O2JhY2tncm91bmQ6Y29sb3ItbWl4KGluIHNyZ2Is"
    "dmFyKC0tdHgpIDE0JSx0cmFuc3BhcmVudCk7dHJhbnNpdGlvbjpiYWNrZ3JvdW5kIC4xMnMsdHJh"
    "bnNmb3JtIC4xMnN9Ci5kb3RncmlkIGkuYWN0e2JhY2tncm91bmQ6dmFyKC0tYWNjKTt0cmFuc2Zv"
    "cm06c2NhbGUoMS4xMil9Ci5kb3Rze2Rpc3BsYXk6aW5saW5lLWZsZXg7Z2FwOjRweDthbGlnbi1p"
    "dGVtczpjZW50ZXJ9Ci5kb3RzIGl7d2lkdGg6NXB4O2hlaWdodDo1cHg7Ym9yZGVyLXJhZGl1czo1"
    "MCU7YmFja2dyb3VuZDp2YXIoLS1hY2MpO2FuaW1hdGlvbjpib3VuY2UgMXMgaW5maW5pdGUgZWFz"
    "ZS1pbi1vdXR9Ci5kb3RzIGk6bnRoLWNoaWxkKDIpe2FuaW1hdGlvbi1kZWxheTouMTVzfS5kb3Rz"
    "IGk6bnRoLWNoaWxkKDMpe2FuaW1hdGlvbi1kZWxheTouM3N9CkBrZXlmcmFtZXMgYm91bmNlezAl"
    "LDgwJSwxMDAle3RyYW5zZm9ybTp0cmFuc2xhdGVZKDApO29wYWNpdHk6LjM1fTQwJXt0cmFuc2Zv"
    "cm06dHJhbnNsYXRlWSgtNXB4KTtvcGFjaXR5OjF9fQoKLyogc3BsYXNoICovCiNzcGxhc2h7cG9z"
    "aXRpb246Zml4ZWQ7aW5zZXQ6MDt6LWluZGV4OjMwMDtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rp"
    "b246Y29sdW1uO2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2dhcDox"
    "NnB4OwogIGJhY2tncm91bmQ6dmFyKC0tYmcpO3RyYW5zaXRpb246b3BhY2l0eSAuNXMgZWFzZSx2"
    "aXNpYmlsaXR5IC41c30KI3NwbGFzaC5nb25le29wYWNpdHk6MDt2aXNpYmlsaXR5OmhpZGRlbn0K"
    "I3NwbGFzaCAubWFya3t3aWR0aDo1OHB4O2hlaWdodDo1OHB4O2JvcmRlci1yYWRpdXM6OHB4O2Rp"
    "c3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7YmFja2dyb3VuZDp2YXIoLS1waWxsZ3JhZCk7"
    "CiAgY29sb3I6I2ZmZmRmODtmb250LXdlaWdodDo4MDA7Zm9udC1zaXplOjEuMXJlbTthbmltYXRp"
    "b246bWFya2luIC43cyBjdWJpYy1iZXppZXIoLjIsMS40LC40LDEpIGJvdGh9CiNzcGxhc2ggaDR7"
    "bWFyZ2luOjA7Zm9udC1mYW1pbHk6dmFyKC0tZmhlYWQpO2ZvbnQtc2l6ZToxcmVtO2ZvbnQtd2Vp"
    "Z2h0OjYwMDtsZXR0ZXItc3BhY2luZzowOwogIGFuaW1hdGlvbjpmYWRldXAgLjZzIC4xNXMgYm90"
    "aH0KI3NwbGFzaCBzbWFsbHtjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjVyZW07bGV0dGVy"
    "LXNwYWNpbmc6LjIyZW07dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2FuaW1hdGlvbjpmYWRldXAg"
    "LjZzIC4yNXMgYm90aH0KQGtleWZyYW1lcyBtYXJraW57ZnJvbXtvcGFjaXR5OjA7dHJhbnNmb3Jt"
    "OnNjYWxlKC42KSByb3RhdGUoLTEyZGVnKX10b3tvcGFjaXR5OjE7dHJhbnNmb3JtOm5vbmV9fQpA"
    "a2V5ZnJhbWVzIGZhZGV1cHtmcm9te29wYWNpdHk6MDt0cmFuc2Zvcm06dHJhbnNsYXRlWSg4cHgp"
    "fXRve29wYWNpdHk6MTt0cmFuc2Zvcm06bm9uZX19CgovKiAwOCDCtyBLaW5ldGljIGdyaWQgY2Fu"
    "dmFzICovCiNrZ3twb3NpdGlvbjpmaXhlZDtpbnNldDowO3otaW5kZXg6MDtwb2ludGVyLWV2ZW50"
    "czpub25lO29wYWNpdHk6LjIyfQoKLyogc3RhZ2dlcmVkIHJldmVhbCBpcyBkcml2ZW4gYnkgdGhl"
    "IFdlYiBBbmltYXRpb25zIEFQSSAoY2Fubm90IGNsYXNoIHdpdGggdGhlbWUgQ1NTKSAqLwoKLyog"
    "MDIgKyAyNSDCtyBEb2NrIHdpdGggc3ByaW5nIHBpbGwgKi8KbmF2I3RhYnMgLmRvY2t7cG9zaXRp"
    "b246cmVsYXRpdmV9Cm5hdiN0YWJzIC5kcGlsbHtwb3NpdGlvbjphYnNvbHV0ZTtsZWZ0OjA7dG9w"
    "OjA7ei1pbmRleDowO2JvcmRlci1yYWRpdXM6N3B4O2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQp"
    "OwogIGJveC1zaGFkb3c6MCA2cHggMThweCBjb2xvci1taXgoaW4gc3JnYix2YXIoLS1hY2MpIDM1"
    "JSx0cmFuc3BhcmVudCk7cG9pbnRlci1ldmVudHM6bm9uZX0KbmF2I3RhYnMgYnV0dG9ue3Bvc2l0"
    "aW9uOnJlbGF0aXZlO3otaW5kZXg6MTt3aWxsLWNoYW5nZTp0cmFuc2Zvcm07CiAgdHJhbnNpdGlv"
    "bjpjb2xvciAuMjZzIGN1YmljLWJlemllciguMjIsMSwuMzYsMSksdHJhbnNmb3JtIC4xOHMgY3Vi"
    "aWMtYmV6aWVyKC4zLDEuNSwuNSwxKX0KbmF2I3RhYnMgYnV0dG9uLm9ue2JhY2tncm91bmQ6dHJh"
    "bnNwYXJlbnQ7Y29sb3I6I2ZmZmRmOH0KbmF2I3RhYnMgLmRwaWxse29wYWNpdHk6MH0KbmF2I3Rh"
    "YnMgLmRwaWxsLnJlYWR5e29wYWNpdHk6MX0KCi8qIDE0IMK3IFYtdGFicyBzbGlkaW5nIGluZGlj"
    "YXRvciAqLwouc3VibmF2e3Bvc2l0aW9uOnJlbGF0aXZlfQouc3VibmF2IC52cGlsbHtwb3NpdGlv"
    "bjphYnNvbHV0ZTtsZWZ0OjA7dG9wOjA7ei1pbmRleDowO2JvcmRlci1yYWRpdXM6NnB4O2JhY2tn"
    "cm91bmQ6dmFyKC0tcGlsbGdyYWQpO3RyYW5zaXRpb246bm9uZTtwb2ludGVyLWV2ZW50czpub25l"
    "fQouc3VibmF2IGJ1dHRvbntwb3NpdGlvbjpyZWxhdGl2ZTt6LWluZGV4OjF9Ci5zdWJuYXYgYnV0"
    "dG9uLm9ue2JhY2tncm91bmQ6dHJhbnNwYXJlbnQ7Y29sb3I6I2ZmZmRmODtib3JkZXItY29sb3I6"
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
    "NnB4O2JvcmRlci1yYWRpdXM6OTlweDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWxpbmUpOwogIGJh"
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
    "aHQ6MzBweDtwYWRkaW5nOjAgMTJweDtib3JkZXItcmFkaXVzOjk5cHg7Ym9yZGVyOjFweCBzb2xp"
    "ZCB2YXIoLS1saW5lKTsKICBiYWNrZ3JvdW5kOnZhcigtLWNhcmQyKTtjb2xvcjp2YXIoLS10eDIp"
    "O2ZvbnQtZmFtaWx5OnZhcigtLWZvbnQpO2ZvbnQtc2l6ZTouN3JlbTtmb250LXdlaWdodDo3MDA7"
    "Y3Vyc29yOnBvaW50ZXI7d2hpdGUtc3BhY2U6bm93cmFwOwogIHRyYW5zaXRpb246dHJhbnNmb3Jt"
    "IC4ycyBjdWJpYy1iZXppZXIoLjMsMS41LC41LDEpLGJhY2tncm91bmQgLjJzLGNvbG9yIC4yc30K"
    "LmNoaXByb3cgYnV0dG9uLm9ue2JhY2tncm91bmQ6dmFyKC0tcGlsbGdyYWQpO2NvbG9yOiNmZmZk"
    "Zjg7Ym9yZGVyLWNvbG9yOnRyYW5zcGFyZW50O3RyYW5zZm9ybTpzY2FsZSgxLjA0KX0KCi8qIHNr"
    "ZWxldG9ucyAoMTMgwrcgbG9hZGVyKSAqLwouc2t7cG9zaXRpb246cmVsYXRpdmU7b3ZlcmZsb3c6"
    "aGlkZGVuO2JhY2tncm91bmQ6dmFyKC0tY2FyZDIpO2JvcmRlci1yYWRpdXM6dmFyKC0tcnMpO2hl"
    "aWdodDo3NHB4O21hcmdpbi1ib3R0b206OXB4fQouc2s6YWZ0ZXJ7Y29udGVudDoiIjtwb3NpdGlv"
    "bjphYnNvbHV0ZTtpbnNldDowO2JhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KDkwZGVnLHRyYW5z"
    "cGFyZW50LGNvbG9yLW1peChpbiBzcmdiLHZhcigtLXR4KSAxMCUsdHJhbnNwYXJlbnQpLHRyYW5z"
    "cGFyZW50KTsKICB0cmFuc2Zvcm06dHJhbnNsYXRlWCgtMTAwJSk7YW5pbWF0aW9uOnNoaW0gMS4x"
    "NXMgaW5maW5pdGV9CkBrZXlmcmFtZXMgc2hpbXt0b3t0cmFuc2Zvcm06dHJhbnNsYXRlWCgxMDAl"
    "KX19CgovKiAxOSDCtyBGZWF0dXJlIHN0cmlwICovCi5mZWF0c3tkaXNwbGF5OmdyaWQ7Z3JpZC10"
    "ZW1wbGF0ZS1jb2x1bW5zOnJlcGVhdCgzLDFmcik7Z2FwOjhweDttYXJnaW4tYm90dG9tOjEycHh9"
    "Ci5mZWF0e2JhY2tncm91bmQ6dmFyKC0tY2FyZCk7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1saW5l"
    "KTtib3JkZXItcmFkaXVzOnZhcigtLXJzKTtwYWRkaW5nOjExcHggOXB4O3RleHQtYWxpZ246Y2Vu"
    "dGVyOwogIHRyYW5zaXRpb246dHJhbnNmb3JtIC4yNXMgY3ViaWMtYmV6aWVyKC4yMiwxLC4zNiwx"
    "KX0KLmZlYXQ6aG92ZXJ7dHJhbnNmb3JtOnRyYW5zbGF0ZVkoLTNweCl9Ci5mZWF0IHN2Z3t3aWR0"
    "aDoxN3B4O2hlaWdodDoxN3B4O2NvbG9yOnZhcigtLWFjYyl9Ci5mZWF0IGJ7ZGlzcGxheTpibG9j"
    "aztmb250LXNpemU6LjY4cmVtO21hcmdpbi10b3A6NXB4fQouZmVhdCBzbWFsbHtkaXNwbGF5OmJs"
    "b2NrO2ZvbnQtc2l6ZTouNnJlbTtjb2xvcjp2YXIoLS10eDIpfQoKLyogMDMgwrcgV29ya3Mgd2hl"
    "ZWwg4oCUIHJvdGF0aW5nIGRlYWwgZGlhbCAqLwoud2hlZWx7cG9zaXRpb246cmVsYXRpdmU7aGVp"
    "Z2h0OjExOHB4O21hcmdpbjoycHggMCAxMnB4O292ZXJmbG93OmhpZGRlbjtib3JkZXItcmFkaXVz"
    "OnZhcigtLXIpOwogIGJvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIo"
    "LS1jYXJkKX0KLndoZWVsIC5yaW5ne3Bvc2l0aW9uOmFic29sdXRlO3JpZ2h0Oi01MnB4O3RvcDo1"
    "MCU7d2lkdGg6MTc2cHg7aGVpZ2h0OjE3NnB4O21hcmdpbi10b3A6LTg4cHg7Ym9yZGVyLXJhZGl1"
    "czo1MCU7CiAgYm9yZGVyOjFweCBkYXNoZWQgdmFyKC0tbGluZSk7YW5pbWF0aW9uOnNwaW4gMjZz"
    "IGxpbmVhciBpbmZpbml0ZX0KLndoZWVsIC5yaW5nIGJ7cG9zaXRpb246YWJzb2x1dGU7bGVmdDo1"
    "MCU7dG9wOjUwJTt3aWR0aDo1MHB4O2hlaWdodDoyNHB4O21hcmdpbjotMTJweCAwIDAgLTI1cHg7"
    "Ym9yZGVyLXJhZGl1czo5OXB4OwogIGRpc3BsYXk6Z3JpZDtwbGFjZS1pdGVtczpjZW50ZXI7Zm9u"
    "dC1zaXplOi42MnJlbTtmb250LXdlaWdodDo4MDA7YmFja2dyb3VuZDp2YXIoLS1jYXJkMik7Ym9y"
    "ZGVyOjFweCBzb2xpZCB2YXIoLS1saW5lKTtjb2xvcjp2YXIoLS10eDIpfQoud2hlZWwgLmNhcHtw"
    "b3NpdGlvbjphYnNvbHV0ZTtpbnNldDowO2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1"
    "bW47anVzdGlmeS1jb250ZW50OmNlbnRlcjtwYWRkaW5nOjAgMTZweDsKICBiYWNrZ3JvdW5kOmxp"
    "bmVhci1ncmFkaWVudCg5MGRlZyx2YXIoLS1jYXJkKSAwIDU4JSx0cmFuc3BhcmVudCl9Ci53aGVl"
    "bCAuY2FwPip7bWF4LXdpZHRoOjYyJX0KLndoZWVsIC5jYXAgZW17Zm9udC1zdHlsZTpub3JtYWw7"
    "Zm9udC1zaXplOi42cmVtO2xldHRlci1zcGFjaW5nOi4xOGVtO3RleHQtdHJhbnNmb3JtOnVwcGVy"
    "Y2FzZTtjb2xvcjp2YXIoLS1hY2MpO2ZvbnQtd2VpZ2h0OjgwMH0KLndoZWVsIC5jYXAgYntmb250"
    "LWZhbWlseTp2YXIoLS1maGVhZCk7Zm9udC1zaXplOjEuMDVyZW07Zm9udC13ZWlnaHQ6NjAwfQpA"
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
    "c2l0aW9uOm5vbmUhaW1wb3J0YW50fX0KCmJvZHl7YmFja2dyb3VuZC1pbWFnZTpyZXBlYXRpbmct"
    "bGluZWFyLWdyYWRpZW50KHRyYW5zcGFyZW50LHRyYW5zcGFyZW50IDI3cHgscmdiYSgzNSwzMywy"
    "OCwuMDUpIDI4cHgpfS5jYXJke2JvcmRlci1zdHlsZTpzb2xpZH0uYmFubmVye2JvcmRlci1zdHls"
    "ZTpkYXNoZWR9Lmhlcm8gaDF7Zm9udC1zaXplOjEuNnJlbX0uYnRue2ZvbnQtd2VpZ2h0OjYwMDts"
    "ZXR0ZXItc3BhY2luZzouMDRlbX0uc2VjIGgye2xldHRlci1zcGFjaW5nOi4yZW19LmNhcmR7Ym94"
    "LXNoYWRvdzpub25lfS5yb3csLnBjYXJke2JveC1zaGFkb3c6bm9uZX0KPC9zdHlsZT4KPC9oZWFk"
    "Pgo8Ym9keT4KCjxjYW52YXMgaWQ9ImtnIj48L2NhbnZhcz4KPGRpdiBpZD0ic3BsYXNoIj48ZGl2"
    "IGNsYXNzPSJtYXJrIiBpZD0ic3BtYXJrIj5aVDwvZGl2PjxkaXYgY2xhc3M9ImRvdGdyaWQiIGlk"
    "PSJkZyI+PC9kaXY+CiAgPGg0IGlkPSJzcG5hbWUiPlpvcm8gVEcgU3RvcmU8L2g0PjxzbWFsbCBp"
    "ZD0ic3B0YWciPlBsYWluIGFuZCBob25lc3Q8L3NtYWxsPjwvZGl2Pgo8c3ZnIHdpZHRoPSIwIiBo"
    "ZWlnaHQ9IjAiIHN0eWxlPSJwb3NpdGlvbjphYnNvbHV0ZSI+PGRlZnM+CiAgPGZpbHRlciBpZD0i"
    "bGciIHg9IjAlIiB5PSIwJSIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgY29sb3ItaW50ZXJw"
    "b2xhdGlvbi1maWx0ZXJzPSJzUkdCIj4KICAgIDxmZVR1cmJ1bGVuY2UgdHlwZT0iZnJhY3RhbE5v"
    "aXNlIiBiYXNlRnJlcXVlbmN5PSIwLjA1IDAuMDUiIG51bU9jdGF2ZXM9IjEiIHNlZWQ9IjEiIHJl"
    "c3VsdD0idCIvPgogICAgPGZlR2F1c3NpYW5CbHVyIGluPSJ0IiBzdGREZXZpYXRpb249IjIiIHJl"
    "c3VsdD0iYiIvPgogICAgPGZlRGlzcGxhY2VtZW50TWFwIGluPSJTb3VyY2VHcmFwaGljIiBpbjI9"
    "ImIiIHNjYWxlPSI3MCIgeENoYW5uZWxTZWxlY3Rvcj0iUiIgeUNoYW5uZWxTZWxlY3Rvcj0iQiIv"
    "PgogIDwvZmlsdGVyPjwvZGVmcz48L3N2Zz4KPGRpdiBpZD0iZngiPjxkaXYgY2xhc3M9IndhdmUi"
    "PjwvZGl2PjxkaXYgY2xhc3M9InJpbmciPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJu"
    "b25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyLjQiIHN0cm9rZS1saW5l"
    "Y2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTIwIDYgOSAxN2wt"
    "NS01Ii8+PC9zdmc+PC9kaXY+PC9kaXY+CjxkaXYgaWQ9InN3Ij48ZW0+UHJldmlldzwvZW0+CiAg"
    "PGJ1dHRvbiBkYXRhLXN3PSJhcHAiIGNsYXNzPSJvbiI+U3RvcmU8L2J1dHRvbj4KICA8YnV0dG9u"
    "IGRhdGEtc3c9ImFkbWluIj5BZG1pbjwvYnV0dG9uPgo8L2Rpdj4KCjxkaXYgaWQ9InZpZXctYXBw"
    "Ij4KPGRpdiBpZD0ic2hlbGwiPgogIDxoZWFkZXIgaWQ9InRvcGJhciI+CiAgICA8ZGl2IGNsYXNz"
    "PSJsb2dvIj5aVDwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnJhbmQiPjxiIGlkPSJibmFtZSI+Wm9y"
    "byBURyBTdG9yZTwvYj48c3BhbiBpZD0iYnRhZyI+UGxhaW4gYW5kIGhvbmVzdDwvc3Bhbj48L2Rp"
    "dj4KICAgIDxidXR0b24gY2xhc3M9Imljb25jaGlwIGhpZGRlbiIgaWQ9ImNhcnRjaGlwIiB0aXRs"
    "ZT0iQ2FydCI+PHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3Vy"
    "cmVudENvbG9yIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tl"
    "LWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTYgMiAzIDZ2MTRhMiAyIDAgMCAwIDIgMmgxNGEy"
    "IDIgMCAwIDAgMi0yVjZsLTMtNHoiLz48cGF0aCBkPSJNMyA2aDE4Ii8+PHBhdGggZD0iTTE2IDEw"
    "YTQgNCAwIDAgMS04IDAiLz48L3N2Zz48aSBpZD0iY2FydG4iPjA8L2k+PC9idXR0b24+CiAgICA8"
    "YnV0dG9uIGNsYXNzPSJiYWxjaGlwIiBkYXRhLWdvPSJ3YWxsZXQiPjxzcGFuIGNsYXNzPSJwbHVz"
    "Ij4rPC9zcGFuPuKCuTxzcGFuIGlkPSJiYWwiIGNsYXNzPSJ0aWNrIj4yNDguNTA8L3NwYW4+PC9i"
    "dXR0b24+CiAgPC9oZWFkZXI+CiAgPG1haW4gaWQ9InN0YWdlIj48L21haW4+CjwvZGl2Pgo8bmF2"
    "IGlkPSJ0YWJzIj48ZGl2IGNsYXNzPSJkb2NrIj48c3BhbiBjbGFzcz0iZHBpbGwiIGlkPSJkcGls"
    "bCI+PC9zcGFuPgogIDxidXR0b24gZGF0YS10YWI9InNob3AiIGNsYXNzPSJvbiI+PHN2ZyB2aWV3"
    "Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3VycmVudENvbG9yIiBzdHJva2Ut"
    "d2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+"
    "PHBhdGggZD0iTTYgMiAzIDZ2MTRhMiAyIDAgMCAwIDIgMmgxNGEyIDIgMCAwIDAgMi0yVjZsLTMt"
    "NHoiLz48cGF0aCBkPSJNMyA2aDE4Ii8+PHBhdGggZD0iTTE2IDEwYTQgNCAwIDAgMS04IDAiLz48"
    "L3N2Zz48c3BhbiBkYXRhLW5hdj0ic2hvcCI+U2hvcDwvc3Bhbj48L2J1dHRvbj4KICA8YnV0dG9u"
    "IGRhdGEtdGFiPSJvcmRlcnMiPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBz"
    "dHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91"
    "bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yMSA4YTIgMiAwIDAgMC0xLTEu"
    "NzNsLTctNGEyIDIgMCAwIDAtMiAwbC03IDRBMiAyIDAgMCAwIDMgOHY4YTIgMiAwIDAgMCAxIDEu"
    "NzNsNyA0YTIgMiAwIDAgMCAyIDBsNy00QTIgMiAwIDAgMCAyMSAxNloiLz48cGF0aCBkPSJtMy4z"
    "IDcgOC43IDUgOC43LTUiLz48cGF0aCBkPSJNMTIgMjJWMTIiLz48L3N2Zz48c3BhbiBkYXRhLW5h"
    "dj0ib3JkZXJzIj5PcmRlcnM8L3NwYW4+PC9idXR0b24+CiAgPGJ1dHRvbiBkYXRhLXRhYj0iZWFy"
    "biI+PHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iY3VycmVudENv"
    "bG9yIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVq"
    "b2luPSJyb3VuZCI+PHBhdGggZD0iTTEzIDIgMyAxNGg5bC0xIDggMTAtMTJoLTl6Ii8+PC9zdmc+"
    "PHNwYW4gZGF0YS1uYXY9ImVhcm4iPkVhcm48L3NwYW4+PC9idXR0b24+CiAgPGJ1dHRvbiBkYXRh"
    "LXRhYj0id2FsbGV0Ij48c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tl"
    "PSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBz"
    "dHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTkgN1Y1YTIgMiAwIDAgMC0yLTJINWEy"
    "IDIgMCAwIDAgMCA0aDE1YTIgMiAwIDAgMSAyIDJ2OWEyIDIgMCAwIDEtMiAySDVhMiAyIDAgMCAx"
    "LTItMlY1Ii8+PHBhdGggZD0iTTE4IDEyaC4wMSIvPjwvc3ZnPjxzcGFuIGRhdGEtbmF2PSJ3YWxs"
    "ZXQiPldhbGxldDwvc3Bhbj48L2J1dHRvbj4KICA8YnV0dG9uIGRhdGEtdGFiPSJ5b3UiPjxzdmcg"
    "dmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ry"
    "b2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91"
    "bmQiPjxwYXRoIGQ9Ik0xOSAyMXYtMmE0IDQgMCAwIDAtNC00SDlhNCA0IDAgMCAwLTQgNHYyIi8+"
    "PGNpcmNsZSBjeD0iMTIiIGN5PSI3IiByPSI0Ii8+PC9zdmc+PHNwYW4gZGF0YS1uYXY9InlvdSI+"
    "WW91PC9zcGFuPjwvYnV0dG9uPgo8L2Rpdj48L25hdj4KPC9kaXY+Cgo8ZGl2IGlkPSJ2aWV3LWFk"
    "bWluIiBjbGFzcz0iaGlkZGVuIj48ZGl2IGlkPSJzaGVsbCI+CiAgPGhlYWRlciBpZD0idG9wYmFy"
    "Ij4KICAgIDxkaXYgY2xhc3M9ImxvZ28iPjxzdmcgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJu"
    "b25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNh"
    "cD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yMCAxM2MwIDUtMy41"
    "IDcuNS03LjY2IDguOTVhMSAxIDAgMCAxLS42Ny0uMDFDNy41IDIwLjUgNCAxOCA0IDEzVjZhMSAx"
    "IDAgMCAxIDEtMWMyIDAgNC41LTEuMiA2LjI0LTIuNzJhMS4xNyAxLjE3IDAgMCAxIDEuNTIgMEMx"
    "NC41MSAzLjgxIDE3IDUgMTkgNWExIDEgMCAwIDEgMSAxeiIvPjwvc3ZnPjwvZGl2PgogICAgPGRp"
    "diBjbGFzcz0iYnJhbmQiPjxiPkFkbWluIENvbnNvbGU8L2I+PHNwYW4gaWQ9ImFibmFtZSI+Wm9y"
    "byBURyBTdG9yZTwvc3Bhbj48L2Rpdj4KICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFz"
    "cz0iZG90Ij48L2k+TGl2ZTwvc3Bhbj4KICA8L2hlYWRlcj4KICA8ZGl2IGNsYXNzPSJzdWJuYXYi"
    "IGlkPSJhc3ViIj48c3BhbiBjbGFzcz0idnBpbGwiIGlkPSJ2cGlsbCI+PC9zcGFuPgogICAgPGJ1"
    "dHRvbiBkYXRhLWE9ImRhc2giIGNsYXNzPSJvbiI+RGFzaGJvYXJkPC9idXR0b24+CiAgICA8YnV0"
    "dG9uIGRhdGEtYT0iY2hhbm5lbHMiPkNoYW5uZWxzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEt"
    "YT0ibG9ncyI+TG9nczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9InBheSI+QXV0by1QYXk8"
    "L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJjb250ZW50Ij5Db250ZW50PC9idXR0b24+CiAg"
    "ICA8YnV0dG9uIGRhdGEtYT0iZmVhdHVyZXMiPkZlYXR1cmVzPC9idXR0b24+CiAgICA8YnV0dG9u"
    "IGRhdGEtYT0ibWFya2V0Ij5NYXJrZXRpbmc8L2J1dHRvbj4KICAgIDxidXR0b24gZGF0YS1hPSJh"
    "bmFseXRpY3MiPkFuYWx5dGljczwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9InNlY3VyaXR5"
    "Ij5TZWN1cml0eTwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9InN0b2NrIj5TdG9jazwvYnV0"
    "dG9uPgogICAgPGJ1dHRvbiBkYXRhLWE9Im9yZGVycyI+T3JkZXJzPC9idXR0b24+CiAgICA8YnV0"
    "dG9uIGRhdGEtYT0idXNlcnMiPlVzZXJzPC9idXR0b24+CiAgICA8YnV0dG9uIGRhdGEtYT0ic2V0"
    "dGluZ3MiPlNldHRpbmdzPC9idXR0b24+CiAgPC9kaXY+CiAgPG1haW4gaWQ9ImFzdGFnZSI+PC9t"
    "YWluPgo8L2Rpdj48L2Rpdj4KCjxkaXYgaWQ9InNoZWV0Ij48ZGl2IGNsYXNzPSJiZyIgZGF0YS1j"
    "bG9zZT48L2Rpdj48ZGl2IGNsYXNzPSJwYW5lbCIgaWQ9InBhbmVsIj48L2Rpdj48L2Rpdj4KPGRp"
    "diBjbGFzcz0idG9hc3QiIGlkPSJ0b2FzdCI+PC9kaXY+Cgo8c2NyaXB0PgovKiDilIDilIAgbGl2"
    "ZS1tb2RlIHN0YXRlLCBkZWNsYXJlZCBmaXJzdCBzbyBub3RoaW5nIGNhbiByZWFkIGl0IHRvbyBl"
    "YXJseSDilIDilIAgKi8KdmFyIExJVkU9ZmFsc2UsTE9DS0VEPWZhbHNlLE1FPXthZG1pbjpmYWxz"
    "ZSxvd25lcjpmYWxzZSxwZXJtczp7fX07CnZhciBURz13aW5kb3cuVGVsZWdyYW0mJndpbmRvdy5U"
    "ZWxlZ3JhbS5XZWJBcHB8fG51bGw7CmZ1bmN0aW9uIHJlYWRJbml0RGF0YSgpewogIHRyeXtpZihU"
    "RyYmVEcuaW5pdERhdGEmJlRHLmluaXREYXRhLmxlbmd0aClyZXR1cm4gVEcuaW5pdERhdGE7fWNh"
    "dGNoKGUpe30KICAvKiBUZWxlZ3JhbSBhbHNvIHB1dHMgaXQgaW4gdGhlIFVSTCwgc28gdGhlIGFw"
    "cCBzdGlsbCB3b3JrcyBpZiB0aGUgU0RLIGlzIGJsb2NrZWQgKi8KICB0cnl7CiAgICBjb25zdCBt"
    "PS9bIyY/XXRnV2ViQXBwRGF0YT0oW14mXSopLy5leGVjKGxvY2F0aW9uLmhhc2h8fCIiKTsKICAg"
    "IGlmKG0mJm1bMV0pcmV0dXJuIGRlY29kZVVSSUNvbXBvbmVudChtWzFdKTsKICB9Y2F0Y2goZSl7"
    "fQogIHRyeXsKICAgIGNvbnN0IHE9bmV3IFVSTFNlYXJjaFBhcmFtcyhsb2NhdGlvbi5zZWFyY2gp"
    "OwogICAgY29uc3Qgdj1xLmdldCgidGdXZWJBcHBEYXRhIik7CiAgICBpZih2KXJldHVybiB2Owog"
    "IH1jYXRjaChlKXt9CiAgdHJ5ewogICAgY29uc3Qgc2F2ZWQ9c2Vzc2lvblN0b3JhZ2UuZ2V0SXRl"
    "bSgidGdJbml0Iik7CiAgICBpZihzYXZlZClyZXR1cm4gc2F2ZWQ7CiAgfWNhdGNoKGUpe30KICBy"
    "ZXR1cm4gIiI7Cn0KZnVuY3Rpb24gaW5pdGlhbHMobmFtZSl7CiAgY29uc3Qgdz0obmFtZXx8IiIp"
    "LnRyaW0oKS5zcGxpdCgvXHMrLykuZmlsdGVyKEJvb2xlYW4pOwogIGlmKCF3Lmxlbmd0aClyZXR1"
    "cm4gIlNUIjsKICByZXR1cm4gKCh3WzBdWzBdfHwiIikrKHcubGVuZ3RoPjE/KHdbMV1bMF18fCIi"
    "KTood1swXVsxXXx8IiIpKSkudG9VcHBlckNhc2UoKTsKfQpmdW5jdGlvbiBwYWludEJyYW5kKG5h"
    "bWUsdGFnKXsKICBpZihuYW1lKXsKICAgIGNvbnN0IG49ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQo"
    "InNwbmFtZSIpOyBpZihuKW4udGV4dENvbnRlbnQ9bmFtZTsKICAgIGNvbnN0IG09ZG9jdW1lbnQu"
    "Z2V0RWxlbWVudEJ5SWQoInNwbWFyayIpOyBpZihtKW0udGV4dENvbnRlbnQ9aW5pdGlhbHMobmFt"
    "ZSk7CiAgICBjb25zdCBiPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJibmFtZSIpOyBpZihiKWIu"
    "dGV4dENvbnRlbnQ9bmFtZTsKICAgIGNvbnN0IGFiPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJh"
    "Ym5hbWUiKTsgaWYoYWIpYWIudGV4dENvbnRlbnQ9bmFtZTsKICAgIGRvY3VtZW50LnRpdGxlPW5h"
    "bWU7CiAgfQogIGlmKHRhZyl7CiAgICBjb25zdCB0PWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJz"
    "cHRhZyIpOyBpZih0KXQudGV4dENvbnRlbnQ9dGFnOwogICAgY29uc3QgYnQ9ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoImJ0YWciKTsgaWYoYnQpYnQudGV4dENvbnRlbnQ9dGFnOwogIH0KfQp0cnl7"
    "CiAgY29uc3QgY249bG9jYWxTdG9yYWdlLmdldEl0ZW0oInN0b3JlTmFtZSIpLCBjdD1sb2NhbFN0"
    "b3JhZ2UuZ2V0SXRlbSgic3RvcmVUYWciKTsKICBpZihjbnx8Y3QpcGFpbnRCcmFuZChjbixjdCk7"
    "Cn1jYXRjaChlKXt9CnZhciBJTklUPXJlYWRJbml0RGF0YSgpOwp0cnl7aWYoSU5JVClzZXNzaW9u"
    "U3RvcmFnZS5zZXRJdGVtKCJ0Z0luaXQiLElOSVQpO31jYXRjaChlKXt9CmZ1bmN0aW9uIHJlYWRB"
    "cHBLZXkoKXsKICB0cnl7CiAgICBjb25zdCBxPW5ldyBVUkxTZWFyY2hQYXJhbXMobG9jYXRpb24u"
    "c2VhcmNoKTsKICAgIGlmKHEuZ2V0KCJrIikpcmV0dXJuIHEuZ2V0KCJrIik7CiAgICBjb25zdCBt"
    "PS9bIyY/XWs9KFteJl0qKS8uZXhlYyhsb2NhdGlvbi5oYXNofHwiIik7CiAgICBpZihtJiZtWzFd"
    "KXJldHVybiBkZWNvZGVVUklDb21wb25lbnQobVsxXSk7CiAgICByZXR1cm4gc2Vzc2lvblN0b3Jh"
    "Z2UuZ2V0SXRlbSgiYXBwS2V5Iil8fCIiOwogIH1jYXRjaChlKXtyZXR1cm4gIiI7fQp9CnZhciBB"
    "UFBLRVk9cmVhZEFwcEtleSgpOwp0cnl7aWYoQVBQS0VZKXNlc3Npb25TdG9yYWdlLnNldEl0ZW0o"
    "ImFwcEtleSIsQVBQS0VZKTt9Y2F0Y2goZSl7fQovKiBkZW1vIGRhdGEgb25seSDigJQgbm8gdG9r"
    "ZW5zLCBubyBVUEkgSUQsIG5vIEdtYWlsIGNyZWRlbnRpYWxzICovCmxldCBDRkc9e25hbWU6Ilpv"
    "cm8gVEcgU3RvcmUiLHRhZzoiUGxhaW4gYW5kIGhvbmVzdCIsY3VyOiLigrkifTsKLyog4pSA4pSA"
    "IEVkaXRhYmxlIGNvcHkg4oCUIGV2ZXJ5IHN0cmluZyBiZWxvdyBpcyBlZGl0YWJsZSBpbiBBZG1p"
    "biDilrggQ29udGVudCDilIDilIAgKi8KY29uc3QgQ01TPXsKIHN0b3JlTmFtZToiWm9ybyBURyBT"
    "dG9yZSIsIHN0b3JlVGFnOiJQbGFpbiBhbmQgaG9uZXN0IiwKIGVtcHR5VGl0bGU6IlJlc3RvY2tp"
    "bmciLCBlbXB0eUJvZHk6IkV2ZXJ5dGhpbmcganVzdCBzb2xkIG91dC4gRnJlc2ggYWNjb3VudHMg"
    "bGFuZCBoZXJlIHNob3J0bHkg4oCUIHR1cm4gb24gYW4gYWxlcnQgYW5kIHlvdSB3aWxsIGhlYXIg"
    "Zmlyc3QuIiwKIHRpY2sxOiI8Yj5JbnN0YW50IGRlbGl2ZXJ5PC9iPiBvbiBldmVyeSBjb3VudHJ5"
    "IiwgdGljazI6IlRvcC11cHMgPGI+Y29uZmlybWVkIGF1dG9tYXRpY2FsbHk8L2I+IiwKIHRpY2sz"
    "OiJGYWlsZWQgbG9naW4/IDxiPkF1dG8gcmVmdW5kPC9iPiIsIHRpY2s0OiJEYWlseSBjaGVjay1p"
    "biA8Yj7igrkwLjQwPC9iPiIsCiBoZXJvS2ljazoiQ2F0YWxvZ3VlIiwgaGVyb0gxOiJBbiBob25l"
    "c3Q8YnI+YWNjb3VudCBzaG9wLiIsCiBoZXJvU3ViOiJJbnN0YW50IGRlbGl2ZXJ5LCBPVFAgaW4t"
    "YXBwLCB3YWxsZXQgdG9wLXVwcyB2ZXJpZmllZCBhdXRvbWF0aWNhbGx5LiB7bn0gY291bnRyaWVz"
    "IGxpdmUuIiwKIGhlcm9CdG4xOiJCcm93c2UgU3RvY2siLCBoZXJvQnRuMjoiQWRkIEZ1bmRzIiwK"
    "IGZlYXQxOiJ+OHMiLCBmZWF0MXM6ImRlbGl2ZXJ5IiwgZmVhdDI6IkF1dG8iLCBmZWF0MnM6InJl"
    "ZnVuZHMiLCBmZWF0MzoiVVBJIiwgZmVhdDNzOiJhdXRvLXZlcmlmeSIsCiBmbGFzaFBpbGw6IkZs"
    "YXNoIOKIkjIwJSIsIGZsYXNoVGV4dDoiRW5kcyBpbiIsIGZsYXNoQnRuOiJWaWV3IiwKIHdoZWVs"
    "S2ljazoiUm90YXRpbmcgZGVhbHMiLCB3aGVlbFRpdGxlOiJVcCB0byAyOCUgb2ZmIiwgd2hlZWxX"
    "b3JkczoiSU5TVEFOVCwgQVVUTy1WRVJJRlksIFJFRlVORFMsIDI0w5c3IiwKIHNob3BIZWFkOiJJ"
    "biBTdG9jayIsIHNob3BTb3J0OiJTb3J0Iiwgc2VhcmNoUGg6IlNlYXJjaCBjb3VudHJ5IG9yIGNv"
    "ZGXigKYiLCBhbGVydHNCdG46IkFsZXJ0cyIsCiB3YWxLaWNrOiJCYWxhbmNlIiwgd2FsU3ViOiJQ"
    "YXkgYnkgVVBJIOKAlCB0YXAgVmVyaWZ5IGFmdGVyIHBheWluZyBhbmQgeW91ciB3YWxsZXQgaXMg"
    "Y3JlZGl0ZWQgaW4gc2Vjb25kcy4iLAogd2FsQWRkOiJBZGQgRnVuZHMiLCBwYXlUaXRsZToiUGF5"
    "bWVudCBEZXRhaWxzIiwgcGF5U3ViOiJBZG1pbiBzZXRzIHRoZSBVUEkgaGFuZGxlIOKAlCBub3Ro"
    "aW5nIGlzIGhhcmQtY29kZWQgaW4gdGhlIGFwcC4iLAogcGF5UHJlZml4OiJQQVkiLCBwYXlXaW5k"
    "b3c6IjUiLAogc2hlZXRUaXRsZToiQWRkIiwgc2hlZXRTdWI6IlBheSB0aGUgZXhhY3QgYW1vdW50"
    "LCB0aGVuIHRhcCBWZXJpZnkgUGF5bWVudC4gVGhlIHJlcXVlc3QgZXhwaXJlcyBpbiB7bX0gbWlu"
    "dXRlcy4iLAogcGF5QnRuOiJQYXkgTm93IiwgdmVyaWZ5QnRuOiJWZXJpZnkgUGF5bWVudCIsIHV0"
    "ckJ0bjoiTm90IGRldGVjdGVkPyBTdWJtaXQgVVRSIiwKIHN0ZXAxOiJQYXltZW50IHJlcXVlc3Qg"
    "Y3JlYXRlZCIsIHN0ZXAyOiJQYXkgdGhlIGV4YWN0IGFtb3VudCBpbiBhbnkgVVBJIGFwcCIsCiBz"
    "dGVwMzoiVGFwIFZlcmlmeSDigJQgd2UgY29uZmlybSB5b3VyIHBheW1lbnQiLCBzdGVwNDoiV2Fs"
    "bGV0IGNyZWRpdGVkIiwKIG1zZ0NoZWNraW5nOiJDaGVja2luZyB5b3VyIHBheW1lbnTigKYiLCBt"
    "c2dPazoiVmVyaWZpZWQg4oCUIHdhbGxldCBjcmVkaXRlZCIsCiBtc2dOb25lOiJObyBwYXltZW50"
    "IGZvdW5kIHlldC4gUGF5IGZpcnN0LCB0aGVuIHRhcCBWZXJpZnkuIiwgbXNnRXhwaXJlZDoiUmVx"
    "dWVzdCBleHBpcmVkIOKAlCBjcmVhdGUgYSBuZXcgb25lLiIsCiBuYXZTaG9wOiJTaG9wIiwgbmF2"
    "T3JkZXJzOiJPcmRlcnMiLCBuYXZFYXJuOiJFYXJuIiwgbmF2V2FsbGV0OiJXYWxsZXQiLCBuYXZZ"
    "b3U6IllvdSIKfTsKY29uc3QgQ01TMD1PYmplY3QuYXNzaWduKHt9LENNUyk7CmNvbnN0IENNU01F"
    "VEE9WwogWyJTdG9yZSBoZWFkZXIiLFtbInN0b3JlTmFtZSIsIlN0b3JlIG5hbWUiXSxbInN0b3Jl"
    "VGFnIiwiVGFnbGluZSJdXV0sCiBbIlNjcm9sbGluZyB0aWNrZXIiLFtbInRpY2sxIiwiVGlja2Vy"
    "IDEiXSxbInRpY2syIiwiVGlja2VyIDIiXSxbInRpY2szIiwiVGlja2VyIDMiXSxbInRpY2s0Iiwi"
    "VGlja2VyIDQiXV1dLAogWyJIZXJvIHNlY3Rpb24iLFtbImhlcm9LaWNrIiwiU21hbGwgbGFiZWwg"
    "YWJvdmUgdGl0bGUiXSxbImhlcm9IMSIsIkJpZyB0aXRsZSJdLFsiaGVyb1N1YiIsIlBhcmFncmFw"
    "aCB1bmRlciB0aGUgdGl0bGUiXSxbImhlcm9CdG4xIiwiTGVmdCBidXR0b24iXSxbImhlcm9CdG4y"
    "IiwiUmlnaHQgYnV0dG9uIl1dXSwKIFsiRmVhdHVyZSBiYWRnZXMiLFtbImZlYXQxIiwiQmFkZ2Ug"
    "MSB2YWx1ZSJdLFsiZmVhdDFzIiwiQmFkZ2UgMSBjYXB0aW9uIl0sWyJmZWF0MiIsIkJhZGdlIDIg"
    "dmFsdWUiXSxbImZlYXQycyIsIkJhZGdlIDIgY2FwdGlvbiJdLFsiZmVhdDMiLCJCYWRnZSAzIHZh"
    "bHVlIl0sWyJmZWF0M3MiLCJCYWRnZSAzIGNhcHRpb24iXV1dLAogWyJGbGFzaCBzYWxlIHN0cmlw"
    "IixbWyJmbGFzaFBpbGwiLCJQaWxsIHRleHQiXSxbImZsYXNoVGV4dCIsIkNvdW50ZG93biBsYWJl"
    "bCJdLFsiZmxhc2hCdG4iLCJCdXR0b24iXV1dLAogWyJSb3RhdGluZyBkZWFscyBjYXJkIixbWyJ3"
    "aGVlbEtpY2siLCJTbWFsbCBsYWJlbCJdLFsid2hlZWxUaXRsZSIsIkhlYWRsaW5lIl0sWyJ3aGVl"
    "bFdvcmRzIiwiTWFycXVlZSB3b3JkcyAoY29tbWEgc2VwYXJhdGVkKSJdXV0sCiBbIlNob3AgbGlz"
    "dCIsW1sic2hvcEhlYWQiLCJTZWN0aW9uIGhlYWRpbmciXSxbInNob3BTb3J0IiwiU29ydCBsaW5r"
    "Il0sWyJzZWFyY2hQaCIsIlNlYXJjaCBwbGFjZWhvbGRlciJdLFsiYWxlcnRzQnRuIiwiQWxlcnRz"
    "IGJ1dHRvbiJdXV0sCiBbIldhbGxldCBzY3JlZW4iLFtbIndhbEtpY2siLCJMYWJlbCBhYm92ZSBi"
    "YWxhbmNlIl0sWyJ3YWxTdWIiLCJCYWxhbmNlIGRlc2NyaXB0aW9uIl0sWyJ3YWxBZGQiLCJBZGQg"
    "ZnVuZHMgYnV0dG9uIl0sWyJwYXlUaXRsZSIsIlBheW1lbnQgY2FyZCB0aXRsZSJdLFsicGF5U3Vi"
    "IiwiUGF5bWVudCBjYXJkIGRlc2NyaXB0aW9uIl1dXSwKIFsiUGF5bWVudCBzaGVldCIsW1sic2hl"
    "ZXRUaXRsZSIsIlNoZWV0IHRpdGxlIHByZWZpeCJdLFsic2hlZXRTdWIiLCJTaGVldCBkZXNjcmlw"
    "dGlvbiAoe219ID0gbWludXRlcykiXSxbInBheUJ0biIsIlBheSBidXR0b24iXSxbInZlcmlmeUJ0"
    "biIsIlZlcmlmeSBidXR0b24iXSxbInV0ckJ0biIsIk1hbnVhbCBmYWxsYmFjayBidXR0b24iXSxb"
    "InBheVByZWZpeCIsIlJlZmVyZW5jZSBwcmVmaXgiXSxbInBheVdpbmRvdyIsIlBheW1lbnQgd2lu"
    "ZG93IChtaW51dGVzKSJdXV0sCiBbIlBheW1lbnQgc3RlcHMiLFtbInN0ZXAxIiwiU3RlcCAxIl0s"
    "WyJzdGVwMiIsIlN0ZXAgMiJdLFsic3RlcDMiLCJTdGVwIDMiXSxbInN0ZXA0IiwiU3RlcCA0Il1d"
    "XSwKIFsiUGF5bWVudCBtZXNzYWdlcyIsW1sibXNnQ2hlY2tpbmciLCJXaGlsZSBjaGVja2luZyJd"
    "LFsibXNnT2siLCJWZXJpZmllZCJdLFsibXNnTm9uZSIsIk5vdGhpbmcgZm91bmQiXSxbIm1zZ0V4"
    "cGlyZWQiLCJFeHBpcmVkIl1dXSwKIFsiQm90dG9tIG5hdmlnYXRpb24iLFtbIm5hdlNob3AiLCJU"
    "YWIgMSJdLFsibmF2T3JkZXJzIiwiVGFiIDIiXSxbIm5hdkVhcm4iLCJUYWIgMyJdLFsibmF2V2Fs"
    "bGV0IiwiVGFiIDQiXSxbIm5hdllvdSIsIlRhYiA1Il1dXQpdOwovKiDilZDilZAgRkVBVFVSRSBG"
    "TEFHUyDigJQgZXZlcnkgZmVhdHVyZSBiZWxvdyBpcyBvZmYtYWJsZTsgd2hlbiBvZmYgbm90aGlu"
    "ZyByZW5kZXJzIOKVkOKVkCAqLwpjb25zdCBGR1JPVVBTPVsKIFsiUGF5bWVudHMgJiB3YWxsZXQi"
    "LFsKICBbImNyeXB0byIsIkNyeXB0byAvIFVTRFQgdG9wLXVwIiwiQWNjZXB0IFVTRFQtVFJDMjAs"
    "IEJUQyBldGMuIHdpdGggYXV0by1jb25maXJtIGZyb20gdGhlIGV4cGxvcmVyLiJdLAogIFsidXBp"
    "Um90YXRlIiwiTXVsdGlwbGUgVVBJIElEcyArIHJvdGF0aW9uIiwiUm90YXRlIGhhbmRsZXMgcGVy"
    "IHBheW1lbnQgb3IgYXV0by1zd2l0Y2ggd2hlbiBvbmUgaXMgbGltaXRlZC4iXSwKICBbImF1dG9S"
    "ZWZ1bmQiLCJBdXRvLXJlZnVuZCB0byB3YWxsZXQiLCJEZWFkIGFjY291bnQgaW5zaWRlIHRoZSB3"
    "YXJyYW50eSB3aW5kb3cgaXMgcmVmdW5kZWQgd2l0aG91dCBzdXBwb3J0LiJdLAogIFsic3BsaXRQ"
    "YXkiLCJTcGxpdCBwYXltZW50IiwiUGF5IHBhcnQgZnJvbSBiYWxhbmNlIGFuZCB0aGUgcmVzdCBi"
    "eSBVUEkgaW4gb25lIGZsb3cuIl0sCiAgWyJsZWRnZXIiLCJXYWxsZXQgbGVkZ2VyIHN0YXRlbWVu"
    "dCIsIkZ1bGwgY3JlZGl0L2RlYml0IGhpc3Rvcnkgd2l0aCBydW5uaW5nIGJhbGFuY2UgYW5kIENT"
    "ViBleHBvcnQuIl0sCiAgWyJkZXBMaW1pdHMiLCJNaW4gLyBtYXggZGVwb3NpdCBydWxlcyIsIkds"
    "b2JhbCBvciBwZXItdXNlciBjYXBzIG9uIGEgc2luZ2xlIHRvcC11cC4iXSwKICBbImRlcEJvbnVz"
    "IiwiRGVwb3NpdCBib251cyB0aWVycyIsIkFkZCDigrk1MDAgZ2V0IDUlIGV4dHJhIOKAlCBjb25m"
    "aWd1cmFibGUgc2xhYnMsIGF1dG8tYXBwbGllZC4iXSwKICBbIndyb25nQW10IiwiV3JvbmctYW1v"
    "dW50IGhhbmRsaW5nIiwiQ3JlZGl0IHdoYXRldmVyIHdhcyBhY3R1YWxseSBwYWlkIGFuZCBmbGFn"
    "IHRoZSBtaXNtYXRjaC4iXV1dLAogWyJDYXRhbG9nICYgc2VsbGluZyIsWwogIFsiY2FydCIsIlNo"
    "b3BwaW5nIGNhcnQgLyBidWxrIGJ1eSIsIk1peCBjb3VudHJpZXMgaW4gb25lIGNoZWNrb3V0LCBv"
    "bmUgY29uZmlybWF0aW9uLCBvbmUgZGViaXQuIl0sCiAgWyJidWxrRGlzYyIsIkJ1bGsgcHVyY2hh"
    "c2UgZGlzY291bnQiLCJBdXRvbWF0aWMgcHJpY2UgYnJlYWsgYXQgNSAvIDEwIC8gMjUgdW5pdHMu"
    "Il0sCiAgWyJ3aXNobGlzdCIsIldpc2hsaXN0IC8gZmF2b3VyaXRlcyIsIlNhdmUgY291bnRyaWVz"
    "IGFuZCByZWJ1eSBpbiBvbmUgdGFwLiJdLAogIFsiZHluUHJpY2UiLCJEeW5hbWljIHByaWNpbmci"
    "LCJQcmljZSBtb3ZlcyB3aXRoIHN0b2NrIGxldmVsLCBhZ2UgdGllciBvciB0aW1lIG9mIGRheS4i"
    "XSwKICBbImZsYXNoU2FsZSIsIkZsYXNoIHNhbGUiLCJUaW1lZCBkaXNjb3VudCB3aXRoIGEgbGl2"
    "ZSBjb3VudGRvd24gb24gdGhlIHN0b3JlZnJvbnQuIl0sCiAgWyJjb3Vwb25zIiwiQ291cG9uIGNv"
    "ZGVzIiwiUGVyY2VudGFnZSBvZmYgYXQgY2hlY2tvdXQgd2l0aCBsaW1pdHMgYW5kIGV4cGlyeS4i"
    "XSwKICBbInZhcmlhbnRzIiwiQWNjb3VudCB2YXJpYW50cyIsIlNwYW0tYmxvY2ssIDJGQSwgdXNl"
    "cm5hbWUsIG1haWwg4oCUIGZpbHRlcmFibGUgYW5kIHByaWNlZCBhcGFydC4iXSwKICBbImhlYWx0"
    "aENoZWNrIiwiU3RvY2sgaGVhbHRoIGNoZWNrIiwiU2Vzc2lvbiBpcyB2YWxpZGF0ZWQgbGl2ZSBi"
    "ZWZvcmUgaXQgaXMgc29sZC4iXV1dLAogWyJPcmRlcnMgJiBhZnRlci1zYWxlIixbCiAgWyJ0aW1l"
    "bGluZSIsIk9yZGVyIHN0YXR1cyB0aW1lbGluZSIsIlBlbmRpbmcsIGRlbGl2ZXJlZCwgT1RQIHJl"
    "Y2VpdmVkLCBjbG9zZWQg4oCUIHZpc2libGUgdG8gYm90aCBzaWRlcy4iXSwKICBbIndhcnJhbnR5"
    "IiwiUmVwbGFjZW1lbnQgLyB3YXJyYW50eSIsIlJlcG9ydCBhIHByb2JsZW0gaW5zaWRlIFggaG91"
    "cnMgZm9yIGFuIGF1dG8tcmVwbGFjZW1lbnQuIl0sCiAgWyJyZWRvd25sb2FkIiwiUmUtZG93bmxv"
    "YWQgZGV0YWlscyIsIkZldGNoIGEgcGFzdCBvcmRlcidzIGRldGFpbHMgYWdhaW4sIHJhdGUtbGlt"
    "aXRlZC4iXSwKICBbImRpc3B1dGUiLCJEaXNwdXRlIGZsb3ciLCJSZWFzb24gcGlja2VyIHBsdXMg"
    "ZXZpZGVuY2UgaW5zdGVhZCBvZiBmcmVlLXRleHQgdGlja2V0cy4iXSwKICBbInNlc3Npb25GaWxl"
    "IiwiU2Vzc2lvbiBmaWxlIGRlbGl2ZXJ5IiwiU2VuZCAuc2Vzc2lvbiAvIEpTT04gYXMgYSBkb2N1"
    "bWVudCB3aXRoIGEgc2VsZi1kZXN0cnVjdCB0aW1lci4iXSwKICBbInJlY2VpcHRzIiwiT3JkZXIg"
    "cmVjZWlwdHMiLCJBdXRvLWdlbmVyYXRlZCBpbnZvaWNlIHBlciBwdXJjaGFzZS4iXV1dLAogWyJU"
    "cnVzdCAmIHNvY2lhbCBwcm9vZiIsWwogIFsicmV2aWV3cyIsIlJldmlld3MgJiBzdGFyIHJhdGlu"
    "Z3MiLCJCdXllciByYXRlcyBhZnRlciBkZWxpdmVyeTsgYXZlcmFnZXMgc2hvdyBvbiBldmVyeSBj"
    "b3VudHJ5LiJdLAogIFsic2FsZXNGZWVkIiwiTGl2ZSBzYWxlcyBmZWVkIiwiUmVjZW50IHNhbGVz"
    "IHNob3duIGluLWFwcCBhbmQgYXV0by1wb3N0ZWQgdG8geW91ciBjaGFubmVsLiJdLAogIFsibGVh"
    "ZGVyYm9hcmQiLCJMZWFkZXJib2FyZCIsIlRvcCBidXllcnMgYW5kIHJlZmVycmVycyBvZiB0aGUg"
    "d2Vlaywgd2l0aCBhIHByaXplLiJdLAogIFsiYnV5ZXJCYWRnZSIsIlZlcmlmaWVkIGJ1eWVyIGJh"
    "ZGdlIiwiQmFkZ2UgYW5kIHBlcmtzIGFmdGVyIE4gY29tcGxldGVkIG9yZGVycy4iXV1dLAogWyJH"
    "cm93dGggJiByZXRlbnRpb24iLFsKICBbInJlZkwyIiwiTXVsdGktbGV2ZWwgcmVmZXJyYWwiLCJM"
    "ZXZlbCAxIGFuZCBsZXZlbCAyIGNvbW1pc3Npb24gaW5zdGVhZCBvZiBhIGZsYXQgcmF0ZS4iXSwK"
    "ICBbInJlZkRhc2giLCJSZWZlcnJhbCBkYXNoYm9hcmQiLCJXaG8gam9pbmVkLCB3aG8gZGVwb3Np"
    "dGVkIGFuZCB3aGF0IGVhY2ggaW52aXRlIGVhcm5lZC4iXSwKICBbInN0cmVhayIsIkNoZWNrLWlu"
    "IHN0cmVhayByZXdhcmRzIiwiRXNjYWxhdGluZyBkYWlseSByZXdhcmQgdGhhdCByZXNldHMgd2hl"
    "biBicm9rZW4uIl0sCiAgWyJsb3lhbHR5IiwiTG95YWx0eSBwb2ludHMiLCJFYXJuIHBlciDigrkg"
    "c3BlbnQsIHJlZGVlbSBmb3IgYmFsYW5jZSBvciBhIGZyZWUgYWNjb3VudC4iXSwKICBbIndpbmJh"
    "Y2siLCJSZS1lbmdhZ2VtZW50IERNcyIsIkF1dG8gbWVzc2FnZSB1c2VycyBpbmFjdGl2ZSBmb3Ig"
    "NyAvIDMwIGRheXMuIl0sCiAgWyJhYmFuZG9uUmVtaW5kIiwiQWJhbmRvbmVkLWRlcG9zaXQgcmVt"
    "aW5kZXIiLCJOdWRnZSBiZWZvcmUgYW4gdW5maW5pc2hlZCBwYXltZW50IGV4cGlyZXMuIl0sCiAg"
    "WyJzY2hlZEJyb2FkY2FzdCIsIlNjaGVkdWxlZCAmIHNlZ21lbnRlZCBicm9hZGNhc3RzIiwiUXVl"
    "dWUgZm9yIGxhdGVyLCB0YXJnZXQgYnV5ZXJzLCBub24tYnV5ZXJzIG9yIGJpZyBzcGVuZGVycy4i"
    "XV1dLAogWyJBZG1pbiAmIG9wZXJhdGlvbnMiLFsKICBbImFkbWluUm9sZXMiLCJBZG1pbiByb2xl"
    "cyIsIk93bmVyIC8gbWFuYWdlciAvIHN1cHBvcnQgd2l0aCBzZXBhcmF0ZSBwZXJtaXNzaW9ucy4i"
    "XSwKICBbImF1ZGl0TG9nIiwiQXVkaXQgbG9nIiwiRXZlcnkgYWRtaW4gYWN0aW9uIGxvZ2dlZCB3"
    "aXRoIHRpbWVzdGFtcCBhbmQgdW5kby4iXSwKICBbImNvbnRlbnRFZGl0b3IiLCJJbi1ib3QgY29u"
    "dGVudCBlZGl0b3IiLCJFZGl0IGV2ZXJ5IG1lc3NhZ2UgYW5kIGJ1dHRvbiB3aXRob3V0IHRvdWNo"
    "aW5nIGNvZGUuIl0sCiAgWyJhdXRvQmFja3VwIiwiU2NoZWR1bGVkIGF1dG8tYmFja3VwIiwiRGF0"
    "YWJhc2UgdG8gYSBwcml2YXRlIGNoYW5uZWwgZXZlcnkgTiBob3Vycywgb25lLXRhcCByZXN0b3Jl"
    "LiJdLAogIFsiYnVsa1VwbG9hZCIsIkJ1bGsgdXBsb2FkIHdpdGggdmFsaWRhdGlvbiIsIlZhbGlk"
    "YXRlIGVhY2ggc2Vzc2lvbiwgcmVwb3J0IGRlYWQgb3IgZHVwbGljYXRlLCBpbXBvcnQgdGhlIHJl"
    "c3QuIl0sCiAgWyJkdXBEZXRlY3QiLCJEdXBsaWNhdGUgZGV0ZWN0aW9uIiwiUmVqZWN0IGEgcGhv"
    "bmUgb3Igc2Vzc2lvbiBhbHJlYWR5IGluIHRoZSBkYXRhYmFzZS4iXSwKICBbImxvd1N0b2NrIiwi"
    "TG93LXN0b2NrIGFsZXJ0cyIsIlBpbmcgd2hlbiBhIGNvdW50cnkgZHJvcHMgYmVsb3cgaXRzIG93"
    "biB0aHJlc2hvbGQuIl0sCiAgWyJzZWxsSG91cnMiLCJTZWxsaW5nLWhvdXJzIHNjaGVkdWxlIiwi"
    "QXV0by1oaWRlIGNvdW50cmllcyBvdXRzaWRlIHRoZWlyIHNlbGxpbmcgd2luZG93LiJdLAogIFsi"
    "c2FuZGJveCIsIlRlc3QgbW9kZSAvIHNhbmRib3giLCJSdW4gdGhlIHdob2xlIGZsb3cgb24gZmFr"
    "ZSBtb25leSBiZWZvcmUgZ29pbmcgbGl2ZS4iXSwKICBbImkxOG4iLCJNdWx0aS1sYW5ndWFnZSIs"
    "IlBlci11c2VyIEhpbmRpIC8gRW5nbGlzaCBzd2l0Y2ggYWNyb3NzIGV2ZXJ5IHN0cmluZy4iXV1d"
    "LAogWyJTZWN1cml0eSAmIGFudGktYWJ1c2UiLFsKICBbInJhdGVMaW1pdCIsIlJhdGUgbGltaXRp"
    "bmciLCJQZXItdXNlciBhY3Rpb24gbGltaXRzIHNvIG5vYm9keSBjYW4gaGFtbWVyIHRoZSBib3Qu"
    "Il0sCiAgWyJtdWx0aUFjYyIsIk11bHRpLWFjY291bnQgZGV0ZWN0aW9uIiwiQ2F0Y2ggcmVmZXJy"
    "YWwgZmFybWluZyBhbmQgZmxhZyBzdXNwaWNpb3VzIHNpZ251cHMuIl0sCiAgWyJ2ZWxvY2l0eSIs"
    "IlB1cmNoYXNlIHZlbG9jaXR5IGFsZXJ0cyIsIkZsYWcgYWJub3JtYWxseSBmYXN0IGJ1eWluZyDi"
    "gJQgdHlwaWNhbCBvZiBzdG9sZW4gdG9wLXVwcy4iXSwKICBbImFkbWluMmZhIiwiQWRtaW4gMkZB"
    "IiwiUElOIG9yIFRPVFAgYmVmb3JlIGJhbGFuY2UgZWRpdHMsIGJyb2FkY2FzdHMgb3Igd2lwZXMu"
    "Il0sCiAgWyJhdXRvQmFuIiwiQXV0by1iYW4gcnVsZXMiLCJCYW4gb24gY2hhcmdlYmFjaywgZmFr"
    "ZSBVVFIgb3IgcmVwZWF0ZWQgZmFpbGVkIHZlcmlmaWNhdGlvbi4iXSwKICBbImJsYWNrbGlzdCIs"
    "IkJsYWNrbGlzdCIsIlBlcm1hbmVudGx5IGJsb2NrIHVzZXIgSURzLCBwaG9uZSBudW1iZXJzIGFu"
    "ZCBVUEkgcmVmZXJlbmNlcy4iXSwKICBbImVuY3J5cHQiLCJTZXNzaW9uIGVuY3J5cHRpb24gYXQg"
    "cmVzdCIsIkEgbGVha2VkIGRhdGFiYXNlIGZpbGUgaXMgdXNlbGVzcyB3aXRob3V0IHRoZSBrZXku"
    "Il1dXSwKIFsiQW5hbHl0aWNzIixbCiAgWyJyZXZDaGFydCIsIlJldmVudWUgZGFzaGJvYXJkIiwi"
    "RGFpbHksIHdlZWtseSBhbmQgbW9udGhseSByZXZlbnVlIGFuZCBwcm9maXQgYXMgY2hhcnRzLiJd"
    "LAogIFsiZnVubmVsIiwiQ29udmVyc2lvbiBmdW5uZWwiLCJTZWUgd2hlcmUgdXNlcnMgZHJvcCBv"
    "ZmYgYmV0d2VlbiBqb2luaW5nIGFuZCBidXlpbmcuIl0sCiAgWyJwcm9maXRSZXAiLCJQZXItY291"
    "bnRyeSBwcm9maXQiLCJDb3N0IHZzIHNhbGUgcHJpY2UgdnMgdm9sdW1lIGZvciBldmVyeSBjb3Vu"
    "dHJ5LiJdLAogIFsiY2x2IiwiQ3VzdG9tZXIgbGlmZXRpbWUgdmFsdWUiLCJTcGVuZCwgZnJlcXVl"
    "bmN5IGFuZCBjaHVybiByaXNrIHBlciB1c2VyLiJdLAogIFsiZGlnZXN0IiwiRGFpbHkgZGlnZXN0"
    "IiwiRW5kLW9mLWRheSBzdW1tYXJ5IHB1c2hlZCB0byB0aGUgYWRtaW4gY2hhdC4iXV1dLAogWyJR"
    "dWFsaXR5IG9mIGxpZmUiLFsKICBbImlubGluZVNlYXJjaCIsIklubGluZSBzZWFyY2giLCJUeXBl"
    "IEB5b3VyYm90IGluZGlhIGluIGFueSBjaGF0IHRvIGZpbmQgYW5kIGJ1eS4iXSwKICBbInNsYXNo"
    "Q21kcyIsIlNsYXNoIGNvbW1hbmRzIG1lbnUiLCIvc3RhcnQgL2J1eSAvYmFsYW5jZSAvb3JkZXJz"
    "IHJlZ2lzdGVyZWQgd2l0aCBUZWxlZ3JhbS4iXSwKICBbInN0b2NrQ291bnRlciIsIkxpdmUgc3Rv"
    "Y2sgY291bnRlciIsIkF1dG8tdXBkYXRpbmcgcGlubmVkIHBvc3QgYW5kIGluLWFwcCBsaXZlIGNv"
    "dW50LiJdLAogIFsib25ib2FyZGluZyIsIk9uYm9hcmRpbmcgdG91ciIsIlRocmVlLXN0ZXAgd2Fs"
    "a3Rocm91Z2ggZm9yIGZpcnN0LXRpbWUgdXNlcnMuIl0sCiAgWyJub3RpZlByZWZzIiwiTm90aWZp"
    "Y2F0aW9uIHByZWZlcmVuY2VzIiwiVXNlcnMgcGljayB3aGljaCBhbGVydHMgdGhleSByZWNlaXZl"
    "LiJdLAogIFsicHVzaE5vdGlmIiwiTWluaSBhcHAgcHVzaCIsIk1pbmkgYXBwIGFjdGlvbnMgbm90"
    "aWZ5IHRoZSB1c2VyIHRocm91Z2ggdGhlIGJvdC4iXV1dCl07CmNvbnN0IEZFQVQ9e307CkZHUk9V"
    "UFMuZm9yRWFjaCgoW2cscm93c10pPT5yb3dzLmZvckVhY2gocj0+RkVBVFtyWzBdXT10cnVlKSk7"
    "CmNvbnN0IEZFQVQwPU9iamVjdC5hc3NpZ24oe30sRkVBVCk7CmNvbnN0IEc9KGssaCk9PkZFQVRb"
    "a10/aDoiIjsKY29uc3QgYW55Rj0oLi4ua3MpPT5rcy5zb21lKGs9PkZFQVRba10pOwpsZXQgQ0FS"
    "VD1bXSxXSVNIPVtdOwpsZXQgUkVWSUVXUz17SW5kaWE6WzQuOCw0MTJdLEluZG9uZXNpYTpbNC42"
    "LDE4MF0sQnJhemlsOls0LjcsOTZdLE5pZ2VyaWE6WzQuNCw1NF0sVmlldG5hbTpbNC45LDIzM10s"
    "UGhpbGlwcGluZXM6WzQuNSw3MV19OwpsZXQgRkVFRD1bWyLwn4eu8J+HsyIsIkluZGlhIiwi4oK5"
    "MzUiLCIyIG1pbiBhZ28iXSxbIvCfh7vwn4ezIiwiVmlldG5hbSIsIuKCuTI2IiwiNiBtaW4gYWdv"
    "Il0sWyLwn4en8J+HtyIsIkJyYXppbCIsIuKCuTQ0IiwiMTEgbWluIGFnbyJdXTsKbGV0IEFETT17"
    "CiAga3BpOntyZXY6NDgyMCxvcmRlcnM6NjEsdXNlcnM6MTI4NCxhdXRvOjM4fSwKICBiYXJzOls0"
    "Miw2MSwzOCw3NCw1NSw4OCw2N10sCiAgc3RvY2s6W10sb3JkZXJzOltdLGRpc3B1dGVzOltdLHVz"
    "ZXJzOltdLGFkbWluczpbXSxjaGFubmVsczpbXSwKICBsb2dzOnttYXN0ZXI6dHJ1ZSxjaGFubmVs"
    "czpbXSxldmVudHM6W119LGNvdXBvbnM6W10sYXVkaXQ6W10scmV2aWV3czpbXSxkZXBvc2l0czpb"
    "XSxsb2dmZWVkOltdLAogIHBheTp7dXBpOiIiLHBheWVlOiIiLGdtYWlsOiIiLGdtYWlsX3NldDpm"
    "YWxzZSx3aW5kb3c6NSxwcmVmaXg6IlBBWSIsbWluOjEwLG1heDoyMDAwMCxhdXRvZGVwOjAscGVu"
    "ZGluZzowfSwKICBzZWN1cml0eTp7cmF0ZToyMCx2ZWxvY2l0eTo1LGJsYWNrbGlzdDowLGJhbm5l"
    "ZDowfSwKICBtYXJrZXQ6e2ZsYXNoOjIwLGZsYXNoX29uOmZhbHNlLHJlZjE6NSxyZWYyOjEsZ29h"
    "bDowLG1vbnRoOjB9LAogIHNldHRpbmdzOntzdG9yZToiIix0YWdsaW5lOiIiLHN1cHBvcnQ6IiIs"
    "cHJpY2U6MzUsZGFpbHk6MC40LGFwcDoiIixtYWludGVuYW5jZTpmYWxzZSxzYW5kYm94OmZhbHNl"
    "fSwKICBhbmFseXRpY3M6e2Z1bm5lbDp7dXNlcnM6MCxkZXA6MCxidXk6MH0sY291bnRyeTpbXSxz"
    "dG9ja190b3RhbDowLHNvbGRfdG90YWw6MH0KfTsKZnVuY3Rpb24gYWRtTnVtKG4pe3JldHVybiAo"
    "bnx8MCkudG9Mb2NhbGVTdHJpbmcoImVuLUlOIik7fQpsZXQgRUFSTj17c3RyZWFrOjQsZGFpbHk6"
    "MC40MCxjbGFpbWVkOmZhbHNlLHBvaW50czozNDAscG9pbnRzV29ydGg6MzQsaW52aXRlczoxMiwK"
    "ICBsMTo1Mi40MCxsMjoxMi40MCxlYXJuZWQ6NjQuODAsbGluazoidC5tZS95b3VyX2JvdD9zdGFy"
    "dD1yZWZfZGVtbyIsCiAgcmVjZW50OltbInVzZXJfMTA0MSIsImRlcG9zaXRlZCDigrkyMDAiLCIr"
    "4oK5Mi4wMCJdLFsidXNlcl8xMDM4Iiwiam9pbmVkIiwi4oCUIl0sWyJ1c2VyXzEwMjIiLCJkZXBv"
    "c2l0ZWQg4oK5NTAwIiwiK+KCuTUuMDAiXV0sCiAgbGVhZGVyczpbWyIxIiwiQHJhaHVsIiwi4oK5"
    "NCw4MjAiXSxbIjIiLCJAYW1hbiIsIuKCuTMsMTQwIl0sWyIzIiwiQHlvdSIsIuKCuTEsOTgwIl1d"
    "LAogIHRhc2tzOltbIkpvaW4gdGhlIG1haW4gY2hhbm5lbCIsIuKCuTEuMDAiLDFdLFsiUGxhY2Ug"
    "eW91ciBmaXJzdCBvcmRlciIsIuKCuTIuMDAiLDFdLFsiSW52aXRlIDMgZnJpZW5kcyIsIuKCuTUu"
    "MDAiLDBdXX07CmZ1bmN0aW9uIHN0YXJzKGMpe2NvbnN0IHI9KFJFVklFV1NbY118fFs0LjcsMjBd"
    "KVswXSxuPShSRVZJRVdTW2NdfHxbNC43LDIwXSlbMV07CiAgcmV0dXJuIGA8c3BhbiBjbGFzcz0i"
    "c3RhcnMiPiR7WzEsMiwzLDQsNV0ubWFwKGk9PmljKGk8PU1hdGgucm91bmQocik/InN0YXIiOiJz"
    "dGFyIikpLmpvaW4oIiIpfTxiPiR7cn0gKCR7bn0pPC9iPjwvc3Bhbj5gO30KZnVuY3Rpb24gYXBw"
    "bHlGZWF0dXJlcygpewogIGNvbnN0IGNjPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjYXJ0Y2hp"
    "cCIpOwogIGlmKGNjKXtjYy5jbGFzc0xpc3QudG9nZ2xlKCJoaWRkZW4iLCFGRUFULmNhcnQpO2Rv"
    "Y3VtZW50LmdldEVsZW1lbnRCeUlkKCJjYXJ0biIpLnRleHRDb250ZW50PUNBUlQubGVuZ3RoO30K"
    "ICBjb25zdCBlYXJuPWFueUYoInN0cmVhayIsInJlZkRhc2giLCJyZWZMMiIsImxveWFsdHkiLCJs"
    "ZWFkZXJib2FyZCIsIndpbmJhY2siKTsKICBjb25zdCBlYj1kb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "KCcjdGFicyBidXR0b25bZGF0YS10YWI9ImVhcm4iXScpOwogIGlmKGViKXtlYi5jbGFzc0xpc3Qu"
    "dG9nZ2xlKCJoaWRkZW4iLCFlYXJuKTsKICAgIGlmKCFlYXJuJiZUQUI9PT0iZWFybiIpe1RBQj0i"
    "c2hvcCI7ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3RhYnMgYnV0dG9uIikuZm9yRWFjaCh4"
    "PT54LmNsYXNzTGlzdC50b2dnbGUoIm9uIix4LmRhdGFzZXQudGFiPT09InNob3AiKSk7cmVuZGVy"
    "KCk7fX0KICBjb25zdCBhbWFwPXttYXJrZXQ6YW55RigiY291cG9ucyIsImZsYXNoU2FsZSIsInNj"
    "aGVkQnJvYWRjYXN0Iiwid2luYmFjayIsInNhbGVzRmVlZCIsImxlYWRlcmJvYXJkIiwibG95YWx0"
    "eSIsInJlZkRhc2giLCJyZWZMMiIsImFiYW5kb25SZW1pbmQiKSwKICAgIGFuYWx5dGljczphbnlG"
    "KCJyZXZDaGFydCIsImZ1bm5lbCIsInByb2ZpdFJlcCIsImNsdiIsImRpZ2VzdCIpLAogICAgc2Vj"
    "dXJpdHk6YW55RigicmF0ZUxpbWl0IiwibXVsdGlBY2MiLCJ2ZWxvY2l0eSIsImFkbWluMmZhIiwi"
    "YXV0b0JhbiIsImJsYWNrbGlzdCIsImVuY3J5cHQiKSwKICAgIGNvbnRlbnQ6RkVBVC5jb250ZW50"
    "RWRpdG9yfTsKICBPYmplY3QuZW50cmllcyhhbWFwKS5mb3JFYWNoKChbayx2XSk9Pntjb25zdCBi"
    "PWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoJyNhc3ViIGJ1dHRvbltkYXRhLWE9IicraysnIl0nKTsK"
    "ICAgIGlmKGIpe2IuY2xhc3NMaXN0LnRvZ2dsZSgiaGlkZGVuIiwhdik7CiAgICAgIGlmKCF2JiZB"
    "VEFCPT09ayl7QVRBQj0iZGFzaCI7ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI2FzdWIgYnV0"
    "dG9uIikuZm9yRWFjaCh4PT54LmNsYXNzTGlzdC50b2dnbGUoIm9uIix4LmRhdGFzZXQuYT09PSJk"
    "YXNoIikpO3JlbmRlckFkbWluKCk7fX19KTsKICBpZih3aW5kb3cuX19kb2NrUGxhY2UpcmVxdWVz"
    "dEFuaW1hdGlvbkZyYW1lKCgpPT53aW5kb3cuX19kb2NrUGxhY2UodHJ1ZSkpOwogIGlmKHdpbmRv"
    "dy5fX3Z0YWJQbGFjZSlyZXF1ZXN0QW5pbWF0aW9uRnJhbWUoKCk9PndpbmRvdy5fX3Z0YWJQbGFj"
    "ZSh0cnVlKSk7Cn0KZnVuY3Rpb24gYXBwbHlDTVMoKXsKIGNvbnN0IGc9aWQ9PmRvY3VtZW50Lmdl"
    "dEVsZW1lbnRCeUlkKGlkKTsKIGlmKGcoImJuYW1lIikpZygiYm5hbWUiKS50ZXh0Q29udGVudD1D"
    "TVMuc3RvcmVOYW1lOwogaWYoZygiYWJuYW1lIikpZygiYWJuYW1lIikudGV4dENvbnRlbnQ9Q01T"
    "LnN0b3JlTmFtZTsKIGlmKENNUy5zdG9yZU5hbWUpe0NGRy5uYW1lPUNNUy5zdG9yZU5hbWU7fQog"
    "aWYoQ01TLnN0b3JlVGFnKUNGRy50YWc9Q01TLnN0b3JlVGFnOwogcGFpbnRCcmFuZChDTVMuc3Rv"
    "cmVOYW1lLENNUy5zdG9yZVRhZyk7CiB0cnl7CiAgIGlmKENNUy5zdG9yZU5hbWUpbG9jYWxTdG9y"
    "YWdlLnNldEl0ZW0oInN0b3JlTmFtZSIsQ01TLnN0b3JlTmFtZSk7CiAgIGlmKENNUy5zdG9yZVRh"
    "Zylsb2NhbFN0b3JhZ2Uuc2V0SXRlbSgic3RvcmVUYWciLENNUy5zdG9yZVRhZyk7CiB9Y2F0Y2go"
    "ZSl7fQogaWYoZygiYnRhZyIpKWcoImJ0YWciKS50ZXh0Q29udGVudD1DTVMuc3RvcmVUYWc7CiBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1uYXZdIikuZm9yRWFjaChuPT57CiAgIGNv"
    "bnN0IGs9Im5hdiIrbi5kYXRhc2V0Lm5hdlswXS50b1VwcGVyQ2FzZSgpK24uZGF0YXNldC5uYXYu"
    "c2xpY2UoMSk7CiAgIGlmKENNU1trXSluLnRleHRDb250ZW50PUNNU1trXTt9KTsKIGlmKHdpbmRv"
    "dy5fX2RvY2tQbGFjZSlyZXF1ZXN0QW5pbWF0aW9uRnJhbWUoKCk9PndpbmRvdy5fX2RvY2tQbGFj"
    "ZSh0cnVlKSk7Cn0KbGV0IEJBTD0yNDguNTA7CmNvbnN0IFNUT0NLPVsKIHtjOiJJbmRpYSIsZjoi"
    "8J+HrvCfh7MiLGNvZGU6Iis5MSIscHJpY2U6MzUsd2FzOjQ5LHF0eToxMjgsaG90OjF9LAoge2M6"
    "IkluZG9uZXNpYSIsZjoi8J+HrvCfh6kiLGNvZGU6Iis2MiIscHJpY2U6Mjgsd2FzOjAscXR5OjY0"
    "fSwKIHtjOiJCcmF6aWwiLGY6IvCfh6fwn4e3Iixjb2RlOiIrNTUiLHByaWNlOjQ0LHdhczo1OSxx"
    "dHk6MjJ9LAoge2M6Ik5pZ2VyaWEiLGY6IvCfh7Pwn4esIixjb2RlOiIrMjM0IixwcmljZTozMSx3"
    "YXM6MCxxdHk6MH0sCiB7YzoiVmlldG5hbSIsZjoi8J+Hu/Cfh7MiLGNvZGU6Iis4NCIscHJpY2U6"
    "MjYsd2FzOjAscXR5OjkxfSwKIHtjOiJQaGlsaXBwaW5lcyIsZjoi8J+HtfCfh60iLGNvZGU6Iis2"
    "MyIscHJpY2U6Mzgsd2FzOjAscXR5Ojd9Cl07CmNvbnN0IE9SREVSUz1bCiB7aWQ6Ik9SRC03NzQx"
    "IixjOiJJbmRpYSIsZjoi8J+HrvCfh7MiLG51bToiKzkxIOKAouKAouKAouKAoiDigKLigKIzMiIs"
    "c3Q6IkRlbGl2ZXJlZCIsd2hlbjoiMiBtaW4gYWdvIixwcmljZTozNX0sCiB7aWQ6Ik9SRC03NzI4"
    "IixjOiJWaWV0bmFtIixmOiLwn4e78J+HsyIsbnVtOiIrODQg4oCi4oCi4oCi4oCiIOKAouKAojA3"
    "IixzdDoiRGVsaXZlcmVkIix3aGVuOiJZZXN0ZXJkYXkiLHByaWNlOjI2fSwKIHtpZDoiT1JELTc3"
    "MDIiLGM6IkJyYXppbCIsZjoi8J+Hp/Cfh7ciLG51bToiKzU1IOKAouKAouKAouKAoiDigKLigKI4"
    "OCIsc3Q6IlJlZnVuZGVkIix3aGVuOiIzIGRheXMgYWdvIixwcmljZTo0NH0KXTsKbGV0IENIQU5O"
    "RUxTPVsKIHtpZDoxLG5hbWU6Ik1haW4gQ2hhbm5lbCIsaGFuZGxlOiJAeW91cl9tYWluX2NoYW5u"
    "ZWwiLHJlcTp0cnVlLG9uOnRydWUsbWVtYmVyczoiMTIuNEsifSwKIHtpZDoyLG5hbWU6IkJhY2t1"
    "cCBDaGFubmVsIixoYW5kbGU6IkB5b3VyX2JhY2t1cCIscmVxOnRydWUsb246dHJ1ZSxtZW1iZXJz"
    "OiIzLjFLIn0sCiB7aWQ6MyxuYW1lOiJEaXNjdXNzaW9uIEdyb3VwIixoYW5kbGU6IkB5b3VyX2dy"
    "b3VwIixyZXE6ZmFsc2Usb246dHJ1ZSxtZW1iZXJzOiI1LjhLIn0KXTsKbGV0IExPR0NIPVsKIHtp"
    "ZDoxLG5hbWU6Ik1haW4gTG9ncyIsaGFuZGxlOiJAeW91cl9sb2dzIixwcmltYXJ5OnRydWUsb246"
    "dHJ1ZX0sCiB7aWQ6MixuYW1lOiJQYXltZW50IExvZ3MiLGhhbmRsZToiQHlvdXJfcGF5X2xvZ3Mi"
    "LHByaW1hcnk6ZmFsc2Usb246dHJ1ZX0sCiB7aWQ6MyxuYW1lOiJFcnJvciBMb2dzIixoYW5kbGU6"
    "Ii0xMDB4eHh4eHh4eHh4IixwcmltYXJ5OmZhbHNlLG9uOmZhbHNlfQpdOwpsZXQgTE9HU19NQVNU"
    "RVI9dHJ1ZTsKbGV0IEVWRU5UUz1bCiB7azoiTmV3IHVzZXIgam9pbmVkIixvbjpmYWxzZSxjaDox"
    "fSx7azoiRGVwb3NpdCAvIHBheW1lbnQiLG9uOnRydWUsY2g6Mn0sCiB7azoiUHVyY2hhc2UiLG9u"
    "OnRydWUsY2g6MX0se2s6Ik9UUCByZXF1ZXN0ZWQiLG9uOmZhbHNlLGNoOjF9LAoge2s6IlN0b2Nr"
    "IGFkZGVkIixvbjp0cnVlLGNoOjF9LHtrOiJBZG1pbiBhY3Rpb24iLG9uOnRydWUsY2g6MX0sCiB7"
    "azoiRXJyb3JzICYgd2FybmluZ3MiLG9uOnRydWUsY2g6M30KXTsKY29uc3QgVFg9W3t0OiJEZXBv"
    "c2l0IixhOiIr4oK5MjAwLjAwIix3OiJUb2RheSwgMTQ6MDIifSx7dDoiUHVyY2hhc2UgwrcgSW5k"
    "aWEiLGE6Ii3igrkzNS4wMCIsdzoiVG9kYXksIDE0OjA1In0sCiB7dDoiRGFpbHkgY2hlY2staW4i"
    "LGE6IivigrkwLjQwIix3OiJUb2RheSwgMDk6MTEifSx7dDoiUmVmZXJyYWwgYm9udXMiLGE6Iivi"
    "grkzLjIwIix3OiJZZXN0ZXJkYXkifV07CgpsZXQgRklMVEVSRUQ9U1RPQ0suc2xpY2UoKSwgUVVF"
    "Ulk9IiIsIENISVA9ImFsbCI7CmNvbnN0IEVNUFRZPSc8ZGl2IGNsYXNzPSJjYXJkIiBzdHlsZT0i"
    "dGV4dC1hbGlnbjpjZW50ZXIiPjxwIGNsYXNzPSJzdWIiPk5vdGhpbmcgbWF0Y2hlcyB0aGF0IHNl"
    "YXJjaC48L3A+PC9kaXY+JzsKY29uc3QgSUM9e2JhZzoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0"
    "XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIy"
    "XCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBh"
    "dGggZD1cIk02IDIgMyA2djE0YTIgMiAwIDAgMCAyIDJoMTRhMiAyIDAgMCAwIDItMlY2bC0zLTR6"
    "XCIvPjxwYXRoIGQ9XCJNMyA2aDE4XCIvPjxwYXRoIGQ9XCJNMTYgMTBhNCA0IDAgMCAxLTggMFwi"
    "Lz48L3N2Zz4iLGJveDoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBz"
    "dHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9"
    "XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0yMSA4YTIgMiAw"
    "IDAgMC0xLTEuNzNsLTctNGEyIDIgMCAwIDAtMiAwbC03IDRBMiAyIDAgMCAwIDMgOHY4YTIgMiAw"
    "IDAgMCAxIDEuNzNsNyA0YTIgMiAwIDAgMCAyIDBsNy00QTIgMiAwIDAgMCAyMSAxNlpcIi8+PHBh"
    "dGggZD1cIm0zLjMgNyA4LjcgNSA4LjctNVwiLz48cGF0aCBkPVwiTTEyIDIyVjEyXCIvPjwvc3Zn"
    "PiIsemFwOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1c"
    "ImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5k"
    "XCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwiTTEzIDIgMyAxNGg5bC0xIDgg"
    "MTAtMTJoLTl6XCIvPjwvc3ZnPiIsd2FsbGV0OiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBm"
    "aWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBz"
    "dHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBk"
    "PVwiTTE5IDdWNWEyIDIgMCAwIDAtMi0ySDVhMiAyIDAgMCAwIDAgNGgxNWEyIDIgMCAwIDEgMiAy"
    "djlhMiAyIDAgMCAxLTIgMkg1YTIgMiAwIDAgMS0yLTJWNVwiLz48cGF0aCBkPVwiTTE4IDEyaC4w"
    "MVwiLz48L3N2Zz4iLHVzZXI6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25l"
    "XCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5l"
    "Y2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMTkgMjF2"
    "LTJhNCA0IDAgMCAwLTQtNEg5YTQgNCAwIDAgMC00IDR2MlwiLz48Y2lyY2xlIGN4PVwiMTJcIiBj"
    "eT1cIjdcIiByPVwiNFwiLz48L3N2Zz4iLHNoaWVsZDoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0"
    "XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIy"
    "XCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PHBh"
    "dGggZD1cIk0yMCAxM2MwIDUtMy41IDcuNS03LjY2IDguOTVhMSAxIDAgMCAxLS42Ny0uMDFDNy41"
    "IDIwLjUgNCAxOCA0IDEzVjZhMSAxIDAgMCAxIDEtMWMyIDAgNC41LTEuMiA2LjI0LTIuNzJhMS4x"
    "NyAxLjE3IDAgMCAxIDEuNTIgMEMxNC41MSAzLjgxIDE3IDUgMTkgNWExIDEgMCAwIDEgMSAxelwi"
    "Lz48L3N2Zz4iLGNoZWNrOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwi"
    "IHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjIuNFwiIHN0cm9rZS1saW5l"
    "Y2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3VuZFwiPjxwYXRoIGQ9XCJNMjAgNiA5"
    "IDE3bC01LTVcIi8+PC9zdmc+IixzZW5kOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxs"
    "PVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJv"
    "a2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwi"
    "bTIyIDItNyAyMC00LTktOS00WlwiLz48cGF0aCBkPVwiTTIyIDIgMTEgMTNcIi8+PC9zdmc+Iixw"
    "bHVzOiI8c3ZnIHZpZXdCb3g9XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1"
    "cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjIuNFwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRc"
    "Ij48cGF0aCBkPVwiTTEyIDV2MTRNNSAxMmgxNFwiLz48L3N2Zz4iLGNvcHk6Ijxzdmcgdmlld0Jv"
    "eD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ry"
    "b2tlLXdpZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49"
    "XCJyb3VuZFwiPjxyZWN0IHdpZHRoPVwiMTRcIiBoZWlnaHQ9XCIxNFwiIHg9XCI4XCIgeT1cIjhc"
    "IiByeD1cIjJcIi8+PHBhdGggZD1cIk00IDE2Yy0xLjEgMC0yLS45LTItMlY0YzAtMS4xLjktMiAy"
    "LTJoMTBjMS4xIDAgMiAuOSAyIDJcIi8+PC9zdmc+IixjaGV2OiI8c3ZnIHZpZXdCb3g9XCIwIDAg"
    "MjQgMjRcIiB3aWR0aD1cIjE2XCIgaGVpZ2h0PVwiMTZcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1c"
    "ImN1cnJlbnRDb2xvclwiIHN0cm9rZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5k"
    "XCIgc3Ryb2tlLWxpbmVqb2luPVwicm91bmRcIj48cGF0aCBkPVwibTkgMTggNi02LTYtNlwiLz48"
    "L3N2Zz4iLGZpcmU6Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIHdpZHRoPVwiMTJcIiBoZWln"
    "aHQ9XCIxMlwiIGZpbGw9XCJub25lXCIgc3Ryb2tlPVwiY3VycmVudENvbG9yXCIgc3Ryb2tlLXdp"
    "ZHRoPVwiMlwiIHN0cm9rZS1saW5lY2FwPVwicm91bmRcIiBzdHJva2UtbGluZWpvaW49XCJyb3Vu"
    "ZFwiPjxwYXRoIGQ9XCJNMTIgMmMxIDQgNSA1IDUgOWE1IDUgMCAwIDEtMTAgMGMwLTIgMS0zIDIt"
    "NCAwIDIgMSAzIDIgMyAwLTMtMS01IDEtOFpcIi8+PC9zdmc+Iixkb3duOiI8c3ZnIHZpZXdCb3g9"
    "XCIwIDAgMjQgMjRcIiBmaWxsPVwibm9uZVwiIHN0cm9rZT1cImN1cnJlbnRDb2xvclwiIHN0cm9r"
    "ZS13aWR0aD1cIjJcIiBzdHJva2UtbGluZWNhcD1cInJvdW5kXCIgc3Ryb2tlLWxpbmVqb2luPVwi"
    "cm91bmRcIj48cGF0aCBkPVwiTTEyIDV2MTRNMTkgMTJsLTcgNy03LTdcIi8+PC9zdmc+Iix1cDoi"
    "PHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBzdHJva2U9XCJjdXJyZW50"
    "Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9XCJyb3VuZFwiIHN0cm9r"
    "ZS1saW5lam9pbj1cInJvdW5kXCI+PHBhdGggZD1cIk0xMiAxOVY1TTUgMTJsNy03IDcgN1wiLz48"
    "L3N2Zz4iLHNlYXJjaDoiPHN2ZyB2aWV3Qm94PVwiMCAwIDI0IDI0XCIgZmlsbD1cIm5vbmVcIiBz"
    "dHJva2U9XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIyXCIgc3Ryb2tlLWxpbmVjYXA9"
    "XCJyb3VuZFwiIHN0cm9rZS1saW5lam9pbj1cInJvdW5kXCI+PGNpcmNsZSBjeD1cIjExXCIgY3k9"
    "XCIxMVwiIHI9XCI3XCIvPjxwYXRoIGQ9XCJtMjEgMjEtNC4zLTQuM1wiLz48L3N2Zz4iLHN0YXI6"
    "Ijxzdmcgdmlld0JveD1cIjAgMCAyNCAyNFwiIGZpbGw9XCJjdXJyZW50Q29sb3JcIiBzdHJva2U9"
    "XCJjdXJyZW50Q29sb3JcIiBzdHJva2Utd2lkdGg9XCIxLjVcIiBzdHJva2UtbGluZWpvaW49XCJy"
    "b3VuZFwiPjxwYXRoIGQ9XCJtMTIgMyAyLjYgNS4zIDUuOS45LTQuMyA0LjEgMSA1LjgtNS4yLTIu"
    "Ny01LjIgMi43IDEtNS44TDMuNSA5LjJsNS45LS45elwiLz48L3N2Zz4ifTsKY29uc3QgaWM9bj0+"
    "SUNbbl18fCIiOwpjb25zdCBlc2M9cz0+U3RyaW5nKHMpLnJlcGxhY2UoL1smPD4iXS9nLGM9Pih7"
    "IiYiOiImYW1wOyIsIjwiOiImbHQ7IiwiPiI6IiZndDsiLCciJzoiJnF1b3Q7In1bY10pKTsKY29u"
    "c3QgbW9uZXk9bj0+Q0ZHLmN1citOdW1iZXIobikudG9GaXhlZCgyKTsKCmxldCB0dDtjb25zdCB0"
    "b2FzdD1tPT57Y29uc3QgZT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgidG9hc3QiKTtlLnRleHRD"
    "b250ZW50PW07ZS5jbGFzc0xpc3QuYWRkKCJzaG93Iik7Y2xlYXJUaW1lb3V0KHR0KTt0dD1zZXRU"
    "aW1lb3V0KCgpPT5lLmNsYXNzTGlzdC5yZW1vdmUoInNob3ciKSwxOTAwKX07CmRvY3VtZW50LmFk"
    "ZEV2ZW50TGlzdGVuZXIoImNsaWNrIixlPT57Y29uc3QgYj1lLnRhcmdldC5jbG9zZXN0KCJbZGF0"
    "YS10b2FzdF0iKTtpZihiKXRvYXN0KGIuZGF0YXNldC50b2FzdCl9KTsKY29uc3Qgc2hlZXQ9aD0+"
    "ewogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJwYW5lbCIpLmlubmVySFRNTD0nPGRpdiBjbGFz"
    "cz0iZ3JhYiI+PC9kaXY+JytoOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzaGVldCIpLmNs"
    "YXNzTGlzdC5hZGQoIm9wZW4iKTsKICAvKiBjb250cm9scyBpbnNpZGUgYSBzaGVldCBhcmUgY3Jl"
    "YXRlZCBhZnRlciBiaW5kaW5nLCBzbyBiaW5kIHRoZW0gbm93ICovCiAgdHJ5eyBpZih0eXBlb2Yg"
    "YmluZENvbW1vbj09PSJmdW5jdGlvbiIpIGJpbmRDb21tb24oKTsgfWNhdGNoKGUpe30KICBkb2N1"
    "bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjc2hlZXQgW2RhdGEtY2xvc2VdIikuZm9yRWFjaCh4PT54"
    "Lm9uY2xpY2s9Y2xvc2VTaGVldCk7Cn07CmNvbnN0IGNsb3NlU2hlZXQ9KCk9PmRvY3VtZW50Lmdl"
    "dEVsZW1lbnRCeUlkKCJzaGVldCIpLmNsYXNzTGlzdC5yZW1vdmUoIm9wZW4iKTsKZG9jdW1lbnQu"
    "YWRkRXZlbnRMaXN0ZW5lcigiY2xpY2siLGU9PntpZihlLnRhcmdldC5jbG9zZXN0KCJbZGF0YS1j"
    "bG9zZV0iKSljbG9zZVNoZWV0KCl9KTsKCmRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNzdyBi"
    "dXR0b24iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogIGlmKGIuZGF0YXNldC5zdz09PSJh"
    "ZG1pbiImJiFhZG1pbkFsbG93ZWQoKSl7dG9hc3QoIkFkbWlucyBvbmx5Iik7cmV0dXJuO30KICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjc3cgYnV0dG9uIikuZm9yRWFjaCh4PT54LmNsYXNz"
    "TGlzdC5yZW1vdmUoIm9uIikpO2IuY2xhc3NMaXN0LmFkZCgib24iKTsKICBbImFwcCIsImFkbWlu"
    "Il0uZm9yRWFjaCh2PT5kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgidmlldy0iK3YpLmNsYXNzTGlz"
    "dC50b2dnbGUoImhpZGRlbiIsdiE9PWIuZGF0YXNldC5zdykpOwogIGRvY3VtZW50LmdldEVsZW1l"
    "bnRCeUlkKCJ0YWJzIikuc3R5bGUuZGlzcGxheT1iLmRhdGFzZXQuc3c9PT0iYXBwIj8iIjoibm9u"
    "ZSI7d2luZG93LnNjcm9sbFRvKDAsMCk7CiAgcmVxdWVzdEFuaW1hdGlvbkZyYW1lKCgpPT57aWYo"
    "d2luZG93Ll9fZG9ja1BsYWNlKXdpbmRvdy5fX2RvY2tQbGFjZSh0cnVlKTsKICAgIGlmKHdpbmRv"
    "dy5fX3Z0YWJQbGFjZSl3aW5kb3cuX192dGFiUGxhY2UodHJ1ZSk7fSk7fSk7CgpsZXQgVEFCPSJz"
    "aG9wIjsKZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiI3RhYnMgYnV0dG9uIikuZm9yRWFjaChi"
    "PT5iLm9uY2xpY2s9KCk9PnsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjdGFicyBidXR0"
    "b24iKS5mb3JFYWNoKHg9PnguY2xhc3NMaXN0LnJlbW92ZSgib24iKSk7Yi5jbGFzc0xpc3QuYWRk"
    "KCJvbiIpOwogIFRBQj1iLmRhdGFzZXQudGFiO3JlbmRlcigpO3dpbmRvdy5zY3JvbGxUbygwLDAp"
    "O2lmKHdpbmRvdy5fX2RvY2tQbGFjZSlyZXF1ZXN0QW5pbWF0aW9uRnJhbWUod2luZG93Ll9fZG9j"
    "a1BsYWNlKTt9KTsKZG9jdW1lbnQuYWRkRXZlbnRMaXN0ZW5lcigiY2xpY2siLGU9Pntjb25zdCBn"
    "PWUudGFyZ2V0LmNsb3Nlc3QoIltkYXRhLWdvXSIpO2lmKCFnKXJldHVybjsKICBjb25zdCB0PWcu"
    "ZGF0YXNldC5nbztkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjdGFicyBidXR0b24iKS5mb3JF"
    "YWNoKHg9PnguY2xhc3NMaXN0LnRvZ2dsZSgib24iLHguZGF0YXNldC50YWI9PT10KSk7CiAgVEFC"
    "PXQ7cmVuZGVyKCk7d2luZG93LnNjcm9sbFRvKDAsMCk7fSk7CgpmdW5jdGlvbiByZW5kZXIoKXtj"
    "b25zdCBzdD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RhZ2UiKTsKICBpZihMT0NLRUQpe3Bh"
    "aW50TG9ja2VkKCk7cmV0dXJuO30KICBzdC5pbm5lckhUTUw9JzxkaXYgY2xhc3M9InNrIj48L2Rp"
    "dj48ZGl2IGNsYXNzPSJzayI+PC9kaXY+PGRpdiBjbGFzcz0ic2siIHN0eWxlPSJoZWlnaHQ6MTQw"
    "cHgiPjwvZGl2Pic7CiAgc2V0VGltZW91dCgoKT0+e2lmKExPQ0tFRCl7cGFpbnRMb2NrZWQoKTty"
    "ZXR1cm47fQogICAgc3QuaW5uZXJIVE1MPSh7c2hvcDp2U2hvcCxvcmRlcnM6dk9yZGVycyxlYXJu"
    "OnZFYXJuLHdhbGxldDp2V2FsbGV0LHlvdTp2WW91fSlbVEFCXSgpOwogICAgYmluZFN0YWdlKCk7"
    "cmV2ZWFsU3RhZ2Uoc3QpO2JpbmRUaWx0KCk7fSxSTT8wOjE2MCk7fQoKZnVuY3Rpb24gdlNob3Ao"
    "KXsKICBjb25zdCBsaXZlPVNUT0NLLmZpbHRlcihzPT5zLnF0eT4wKS5sZW5ndGgsIHVuaXRzPVNU"
    "T0NLLnJlZHVjZSgoYSxiKT0+YStiLnF0eSwwKTsKICBpZihMSVZFJiYhU1RPQ0subGVuZ3RoKXsK"
    "ICAgIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCIgc3R5bGU9Im1hcmdpbjoxOHB4IDA7dGV4dC1h"
    "bGlnbjpjZW50ZXI7cGFkZGluZzoyNnB4Ij4KICAgICAgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDZw"
    "eCI+JHtlc2MoQ01TLmVtcHR5VGl0bGV8fCJSZXN0b2NraW5nIil9PC9oMz4KICAgICAgPHAgY2xh"
    "c3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj4ke2VzYyhDTVMuZW1wdHlCb2R5fHwiRXZlcnl0aGlu"
    "ZyBqdXN0IHNvbGQgb3V0LiBGcmVzaCBhY2NvdW50cyBsYW5kIGhlcmUgc2hvcnRseSBcdTIwMTQg"
    "dHVybiBvbiBhbiBhbGVydCBhbmQgeW91IHdpbGwgaGVhciBmaXJzdC4iKX08L3A+CiAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9Im1hcmdpbi10b3A6MTRweCIgZGF0YS1hY3Q9"
    "ImFsZXJ0cyI+Tm90aWZ5IG1lPC9idXR0b24+PC9kaXY+YDsKICB9CiAgcmV0dXJuIGAKICA8ZGl2"
    "IGNsYXNzPSJiYW5uZXIiPjxkaXYgY2xhc3M9InRyYWNrIj4KICAgICR7WzEsMl0ubWFwKCgpPT5g"
    "PHNwYW4+JHtpYygiemFwIil9JHtDTVMudGljazF9PC9zcGFuPjxzcGFuPiR7aWMoIndhbGxldCIp"
    "fSR7Q01TLnRpY2syfTwvc3Bhbj4KICAgIDxzcGFuPiR7aWMoInNoaWVsZCIpfSR7Q01TLnRpY2sz"
    "fTwvc3Bhbj48c3Bhbj4ke2ljKCJzdGFyIil9JHtDTVMudGljazR9PC9zcGFuPmApLmpvaW4oIiIp"
    "fQogIDwvZGl2PjwvZGl2PgogICR7Rygib25ib2FyZGluZyIsYDxkaXYgY2xhc3M9ImNhcmQgdGln"
    "aHQiIGlkPSJ0b3VyY2FyZCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7"
    "Z2FwOjEwcHgiPgogICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygiemFwIil9PC9kaXY+CiAgICA8"
    "ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjhyZW0i"
    "Pk5ldyBoZXJlPyAzLXN0ZXAgdG91cjwvYj4KICAgICAgPGJyPjxzbWFsbCBzdHlsZT0iY29sb3I6"
    "dmFyKC0tdHgyKTtmb250LXNpemU6LjY5cmVtIj5Ub3AgdXAsIHBpY2sgYSBjb3VudHJ5LCBnZXQg"
    "dGhlIE9UUCBpbi1hcHAuPC9zbWFsbD48L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyIg"
    "aWQ9InRvdXJnbyI+U3RhcnQ8L2J1dHRvbj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBnaG9z"
    "dCIgaWQ9InRvdXJza2lwIj5Ta2lwPC9idXR0b24+PC9kaXY+YCl9CiAgPHNlY3Rpb24gY2xhc3M9"
    "Imhlcm8iPgogICAgPGRpdiBjbGFzcz0ia2ljayI+JHtDTVMuaGVyb0tpY2t9PC9kaXY+CiAgICA8"
    "aDE+JHtDTVMuaGVyb0gxfTwvaDE+CiAgICA8cD4ke0NNUy5oZXJvU3ViLnJlcGxhY2UoIntufSIs"
    "bGl2ZSl9PC9wPgogICAgPGRpdiBjbGFzcz0iYnRucm93Ij4KICAgICAgPGJ1dHRvbiBjbGFzcz0i"
    "YnRuIGN0YSIgZGF0YS1zY3JvbGw9InNob3BsaXN0Ij4ke2ljKCJiYWciKX0gJHtDTVMuaGVyb0J0"
    "bjF9PC9idXR0b24+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBnbGFzcyIgZGF0YS1nbz0id2Fs"
    "bGV0Ij4ke0NNUy5oZXJvQnRuMn08L2J1dHRvbj4KICAgIDwvZGl2PgogIDwvc2VjdGlvbj4KICA8"
    "c3ZnIGNsYXNzPSJ0cmFjZSIgdmlld0JveD0iMCAwIDMyMCAxMCIgcHJlc2VydmVBc3BlY3RSYXRp"
    "bz0ibm9uZSI+PHBhdGggY2xhc3M9ImJhc2UiIGQ9Ik0wLDUgTDMyMCw1Ii8+CiAgICA8cGF0aCBj"
    "bGFzcz0ibGVhZCIgZD0iTTAsNSBMMzIwLDUiIHN0cm9rZT0idXJsKCN0ZzEpIi8+CiAgICA8ZGVm"
    "cz48bGluZWFyR3JhZGllbnQgaWQ9InRnMSIgZ3JhZGllbnRVbml0cz0idXNlclNwYWNlT25Vc2Ui"
    "IHgxPSItMTYwIiB5MT0iMCIgeDI9IjAiIHkyPSIwIj4KICAgICAgPHN0b3Agc3RvcC1jb2xvcj0i"
    "dmFyKC0tYWNjKSIgc3RvcC1vcGFjaXR5PSIwIi8+PHN0b3Agb2Zmc2V0PSIuNSIgc3RvcC1jb2xv"
    "cj0idmFyKC0tYWNjKSIvPgogICAgICA8c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29sb3I9InZhcigt"
    "LWFjYzIpIiBzdG9wLW9wYWNpdHk9IjAiLz4KICAgICAgPGFuaW1hdGUgYXR0cmlidXRlTmFtZT0i"
    "eDEiIHZhbHVlcz0iLTE2MDszMjAiIGR1cj0iMi4ycyIgcmVwZWF0Q291bnQ9ImluZGVmaW5pdGUi"
    "Lz4KICAgICAgPGFuaW1hdGUgYXR0cmlidXRlTmFtZT0ieDIiIHZhbHVlcz0iMDs0ODAiIGR1cj0i"
    "Mi4ycyIgcmVwZWF0Q291bnQ9ImluZGVmaW5pdGUiLz4KICAgIDwvbGluZWFyR3JhZGllbnQ+PC9k"
    "ZWZzPjwvc3ZnPgogIDxkaXYgY2xhc3M9ImZlYXRzIj4KICAgIDxkaXYgY2xhc3M9ImZlYXQiPiR7"
    "aWMoInphcCIpfTxiPiR7Q01TLmZlYXQxfTwvYj48c21hbGw+JHtDTVMuZmVhdDFzfTwvc21hbGw+"
    "PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmZWF0Ij4ke2ljKCJzaGllbGQiKX08Yj4ke0NNUy5mZWF0"
    "Mn08L2I+PHNtYWxsPiR7Q01TLmZlYXQyc308L3NtYWxsPjwvZGl2PgogICAgPGRpdiBjbGFzcz0i"
    "ZmVhdCI+JHtpYygid2FsbGV0Iil9PGI+JHtDTVMuZmVhdDN9PC9iPjxzbWFsbD4ke0NNUy5mZWF0"
    "M3N9PC9zbWFsbD48L2Rpdj4KICA8L2Rpdj4KICAke0coInN0b2NrQ291bnRlciIsYDxkaXYgY2xh"
    "c3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dh"
    "cDoxMHB4Ij4KICAgIDxzcGFuIGNsYXNzPSJwaWxsIG9uIj48aSBjbGFzcz0iZG90Ij48L2k+TGl2"
    "ZTwvc3Bhbj4KICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc2"
    "cmVtO2NvbG9yOnZhcigtLXR4MikiPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7dW5pdHN9"
    "PC9iPiBhY2NvdW50cyBpbiBzdG9jayBhY3Jvc3MgPGIgc3R5bGU9ImNvbG9yOnZhcigtLXR4KSI+"
    "JHtsaXZlfTwvYj4gY291bnRyaWVzPC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgc29m"
    "dCIgZGF0YS1hY3Q9ImNoYW5uZWwiPkNoYW5uZWw8L2J1dHRvbj48L2Rpdj5gKX0KICAke0coImZs"
    "YXNoU2FsZSIsYDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxp"
    "Z24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgIDxzcGFuIGNsYXNzPSJwaWxsIGhvdCI+JHtp"
    "YygiZmlyZSIpfSAke0NNUy5mbGFzaFBpbGx9PC9zcGFuPgogICAgPGRpdiBzdHlsZT0iZmxleDox"
    "O21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzZyZW07Y29sb3I6dmFyKC0tdHgyKSI+JHtDTVMuZmxh"
    "c2hUZXh0fSA8YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIiBpZD0iY2QiPjE0OjUyPC9iPjwvZGl2"
    "PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHhzIiBkYXRhLWFjdD0iZmxhc2giPiR7Q01TLmZsYXNo"
    "QnRufTwvYnV0dG9uPjwvZGl2PmApfQogIDxkaXYgY2xhc3M9IndoZWVsIj48ZGl2IGNsYXNzPSJy"
    "aW5nIj4ke1NUT0NLLm1hcCgocyxpKT0+YDxiIHN0eWxlPSJ0cmFuc2Zvcm06cm90YXRlKCR7aSo2"
    "MH1kZWcpIHRyYW5zbGF0ZSg3MHB4KSByb3RhdGUoLSR7aSo2MH1kZWcpIj4ke3MuY29kZX08L2I+"
    "YCkuam9pbigiIil9PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJjYXAiPjxlbT4ke0NNUy53aGVlbEtp"
    "Y2t9PC9lbT48Yj4ke0NNUy53aGVlbFRpdGxlfTwvYj4KICAgICAgPGRpdiBjbGFzcz0iY2xvdWQi"
    "IHN0eWxlPSJtYXJnaW4tdG9wOjZweCI+PGRpdiBjbGFzcz0idCI+JHtbMSwyXS5tYXAoKCk9PkNN"
    "Uy53aGVlbFdvcmRzLnNwbGl0KCIsIikubWFwKHc9PmA8aT4ke3cudHJpbSgpfTwvaT5gKS5qb2lu"
    "KCIiKSkuam9pbigiIil9PC9kaXY+PC9kaXY+PC9kaXY+PC9kaXY+CiAgJHtHKCJzYWxlc0ZlZWQi"
    "LGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+SnVzdCBzb2xkPC9oMz48cCBjbGFzcz0ic3ViIj5MaXZl"
    "IGZlZWQg4oCUIGFsc28gYXV0by1wb3N0ZWQgdG8geW91ciBjaGFubmVsLjwvcD4KICAgICR7RkVF"
    "RC5sZW5ndGg/RkVFRC5tYXAoKFtmLGMsYSx3XSk9PmA8ZGl2IGNsYXNzPSJmZWVkbGluZSI+PHNw"
    "YW4+JHtmfTwvc3Bhbj48Yj4ke2N9PC9iPiBzb2xkIGZvciA8Yj4ke2F9PC9iPjxzcGFuIHN0eWxl"
    "PSJtYXJnaW4tbGVmdDphdXRvIj4ke3d9PC9zcGFuPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgIDon"
    "PHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5ObyBzYWxlcyB5ZXQgdG9kYXkuPC9wPid9"
    "PC9kaXY+YCl9CiAgPGRpdiBjbGFzcz0ic2VjIiBpZD0ic2hvcGxpc3QiPjxoMj4ke0NNUy5zaG9w"
    "SGVhZH08L2gyPjxhIGRhdGEtYWN0PSJzb3J0Ij4ke0NNUy5zaG9wU29ydH08L2E+PC9kaXY+CiAg"
    "PGRpdiBjbGFzcz0ibXNlYXJjaCI+PGRpdiBjbGFzcz0iZmllbGR3cmFwIj4ke2ljKCJzZWFyY2gi"
    "KX08aW5wdXQgaWQ9InEiIHBsYWNlaG9sZGVyPSIke0NNUy5zZWFyY2hQaH0iPjwvZGl2PgogICAg"
    "PGJ1dHRvbiBjbGFzcz0iYnRuIHNtIHNvZnQiIGRhdGEtYWN0PSJhbGVydHMiPiR7Q01TLmFsZXJ0"
    "c0J0bn08L2J1dHRvbj48L2Rpdj4KICA8ZGl2IGNsYXNzPSJjaGlwcm93IiBpZD0iY2hpcHMiPgog"
    "ICAgPGJ1dHRvbiBjbGFzcz0ib24iIGRhdGEtZmlsdGVyPSJhbGwiPkFsbDwvYnV0dG9uPjxidXR0"
    "b24gZGF0YS1maWx0ZXI9InN0b2NrIj5JbiBzdG9jazwvYnV0dG9uPgogICAgPGJ1dHRvbiBkYXRh"
    "LWZpbHRlcj0iZGVhbCI+T24gZGVhbDwvYnV0dG9uPjxidXR0b24gZGF0YS1maWx0ZXI9ImNoZWFw"
    "Ij5VbmRlciDigrkzNTwvYnV0dG9uPgogICAgJHtHKCJ2YXJpYW50cyIsYDxidXR0b24gZGF0YS1m"
    "aWx0ZXI9InYyZmEiPk5vIDJGQTwvYnV0dG9uPjxidXR0b24gZGF0YS1maWx0ZXI9InZtYWlsIj5X"
    "aXRoIG1haWw8L2J1dHRvbj48YnV0dG9uIGRhdGEtZmlsdGVyPSJ2c3BhbSI+U3BhbS1mcmVlPC9i"
    "dXR0b24+YCl9PC9kaXY+CiAgPGRpdiBpZD0ic2hvcGJvZHkiPiR7c2hvcEJvZHkoKX08L2Rpdj5g"
    "Owp9CmZ1bmN0aW9uIHNob3BCb2R5KCl7cmV0dXJuIGAke0ZJTFRFUkVELmxlbmd0aD9gPGRpdiBj"
    "bGFzcz0icm93cyI+JHtGSUxURVJFRC5tYXAoc2hvcFJvdykuam9pbigiIil9PC9kaXY+YDpFTVBU"
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
    "LS1waWxsZ3JhZCk7Y29sb3I6I2ZmZmRmODtib3JkZXItY29sb3I6dHJhbnNwYXJlbnQiOiJiYWNr"
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
    "Rj0yMTAsV0FSUD0yMixMRVJQPS4wOSxSR0I9IjM1LDMzLDI4IjsKICBsZXQgVz0wLEg9MCxtb3Vz"
    "ZT17eDotOTk5OSx5Oi05OTk5fSx0Z3Q9e3g6LTk5OTkseTotOTk5OX0scmlwPVtdLHJhZj0wOwog"
    "IGZ1bmN0aW9uIHNpemUoKXtjb25zdCBkPU1hdGgubWluKDEuNixkZXZpY2VQaXhlbFJhdGlvfHwx"
    "KTtXPWlubmVyV2lkdGg7SD1pbm5lckhlaWdodDsKICAgIGN2LndpZHRoPVcqZDtjdi5oZWlnaHQ9"
    "SCpkO2N2LnN0eWxlLndpZHRoPVcrInB4Ijtjdi5zdHlsZS5oZWlnaHQ9SCsicHgiO2N0eC5zZXRU"
    "cmFuc2Zvcm0oZCwwLDAsZCwwLDApO30KICBhZGRFdmVudExpc3RlbmVyKCJyZXNpemUiLHNpemUp"
    "O3NpemUoKTsKICBhZGRFdmVudExpc3RlbmVyKCJwb2ludGVybW92ZSIsZT0+e3RndD17eDplLmNs"
    "aWVudFgseTplLmNsaWVudFl9fSx7cGFzc2l2ZTp0cnVlfSk7CiAgYWRkRXZlbnRMaXN0ZW5lcigi"
    "cG9pbnRlcmRvd24iLGU9PntpZihyaXAubGVuZ3RoPDQpcmlwLnB1c2goe3g6ZS5jbGllbnRYLHk6"
    "ZS5jbGllbnRZLHI6MCxvOjF9KX0se3Bhc3NpdmU6dHJ1ZX0pOwogIGFkZEV2ZW50TGlzdGVuZXIo"
    "InBvaW50ZXJsZWF2ZSIsKCk9PnRndD17eDotOTk5OSx5Oi05OTk5fSk7CiAgZnVuY3Rpb24gd2Fy"
    "cChneCxneSxjLHIsY29scyxyb3dzKXsKICAgIGNvbnN0IGNwPU1hdGgubWluKGMvMS41LChjb2xz"
    "LTEtYykvMS41LDEpLHJwPU1hdGgubWluKHIvMS41LChyb3dzLTEtcikvMS41LDEpLHBpbj1jcCpj"
    "cCpycCpycDsKICAgIGNvbnN0IGR4PWd4LW1vdXNlLngsZHk9Z3ktbW91c2UueSxkaXN0PU1hdGgu"
    "aHlwb3QoZHgsZHkpOwogICAgY29uc3QgcHJveD1NYXRoLm1heCgwLDEtZGlzdC9JTkYpKnBpbjts"
    "ZXQgcng9MCxyeT0wOwogICAgZm9yKGNvbnN0IHAgb2YgcmlwKXtjb25zdCBkZHg9Z3gtcC54LGRk"
    "eT1neS1wLnksZGQ9TWF0aC5oeXBvdChkZHgsZGR5KSxkaWZmPWRkLXAucjsKICAgICAgaWYoTWF0"
    "aC5hYnMoZGlmZik8NTUpe2NvbnN0IHN0PSgxLU1hdGguYWJzKGRpZmYpLzU1KSpwLm8qMTYqcGlu"
    "LGFuPU1hdGguYXRhbjIoZGR5LGRkeCksc2c9ZGlmZjwwPy0xOjE7CiAgICAgICAgcngrPU1hdGgu"
    "Y29zKGFuKSpzdCpzZyotMTtyeSs9TWF0aC5zaW4oYW4pKnN0KnNnKi0xO319CiAgICBpZihkaXN0"
    "PElORiYmZGlzdD4wJiZwaW4+MCl7Y29uc3QgdD1kaXN0L0lORixlPSgxLXQpKigxLXQpKk1hdGgu"
    "bWluKDEsZGlzdC82MCksdz1lKldBUlAqcGluLGFuPU1hdGguYXRhbjIoZHksZHgpOwogICAgICBy"
    "ZXR1cm5bZ3gtTWF0aC5jb3MoYW4pKncrcngsZ3ktTWF0aC5zaW4oYW4pKncrcnkscHJveF07fQog"
    "ICAgcmV0dXJuW2d4K3J4LGd5K3J5LHByb3hdO30KICBmdW5jdGlvbiBmcmFtZSgpewogICAgbW91"
    "c2UueCs9KHRndC54LW1vdXNlLngpKkxFUlA7bW91c2UueSs9KHRndC55LW1vdXNlLnkpKkxFUlA7"
    "CiAgICByaXA9cmlwLmZpbHRlcihwPT57cC5yKz03O3Auby09LjAxNjtyZXR1cm4gcC5vPjB9KTsK"
    "ICAgIGN0eC5jbGVhclJlY3QoMCwwLFcsSCk7CiAgICBjb25zdCBjb2xzPU1hdGguY2VpbChXL1NQ"
    "KSsxLHJvd3M9TWF0aC5jZWlsKEgvU1ApKzEscHRzPVtdOwogICAgZm9yKGxldCByPTA7cjxyb3dz"
    "O3IrKyl7Y29uc3Qgcm93PVtdO2ZvcihsZXQgYz0wO2M8Y29scztjKyspcm93LnB1c2god2FycChj"
    "KlNQLHIqU1AsYyxyLGNvbHMscm93cykpO3B0cy5wdXNoKHJvdyk7fQogICAgZm9yKGxldCByPTA7"
    "cjxyb3dzO3IrKylmb3IobGV0IGM9MDtjPGNvbHM7YysrKXtjb25zdCBwPXB0c1tyXVtjXTsKICAg"
    "ICAgaWYoYzxjb2xzLTEpe2NvbnN0IHE9cHRzW3JdW2MrMV07Y3R4LnN0cm9rZVN0eWxlPSJyZ2Jh"
    "KCIrUkdCKyIsIisoLjA1K3BbMl0qLjIyKS50b0ZpeGVkKDMpKyIpIjsKICAgICAgICBjdHgubGlu"
    "ZVdpZHRoPTE7Y3R4LmJlZ2luUGF0aCgpO2N0eC5tb3ZlVG8ocFswXSxwWzFdKTtjdHgubGluZVRv"
    "KHFbMF0scVsxXSk7Y3R4LnN0cm9rZSgpO30KICAgICAgaWYocjxyb3dzLTEpe2NvbnN0IHE9cHRz"
    "W3IrMV1bY107Y3R4LnN0cm9rZVN0eWxlPSJyZ2JhKCIrUkdCKyIsIisoLjA1K3BbMl0qLjIyKS50"
    "b0ZpeGVkKDMpKyIpIjsKICAgICAgICBjdHgubGluZVdpZHRoPTE7Y3R4LmJlZ2luUGF0aCgpO2N0"
    "eC5tb3ZlVG8ocFswXSxwWzFdKTtjdHgubGluZVRvKHFbMF0scVsxXSk7Y3R4LnN0cm9rZSgpO30K"
    "ICAgICAgaWYocFsyXT4uMDYpe2N0eC5maWxsU3R5bGU9InJnYmEoIitSR0IrIiwiKyhwWzJdKi41"
    "KS50b0ZpeGVkKDMpKyIpIjtjdHguYmVnaW5QYXRoKCk7CiAgICAgICAgY3R4LmFyYyhwWzBdLHBb"
    "MV0sMS42K3BbMl0qMS42LDAsNi4yODMpO2N0eC5maWxsKCk7fX0KICAgIHJhZj1yZXF1ZXN0QW5p"
    "bWF0aW9uRnJhbWUoZnJhbWUpO30KICBmcmFtZSgpOwogIGRvY3VtZW50LmFkZEV2ZW50TGlzdGVu"
    "ZXIoInZpc2liaWxpdHljaGFuZ2UiLCgpPT57aWYoZG9jdW1lbnQuaGlkZGVuKWNhbmNlbEFuaW1h"
    "dGlvbkZyYW1lKHJhZik7ZWxzZSBmcmFtZSgpO30pOwp9KSgpOwoKLyogMDEgwrcgZG90IGxvYWRl"
    "ciBzcGxhc2ggKGtpdCBmcmFtZSBkYXRhKSAqLwooZnVuY3Rpb24gc3BsYXNoKCl7CiAgY29uc3Qg"
    "Zz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZGciKSxzcD1kb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgic3BsYXNoIik7aWYoIWcpcmV0dXJuOwogIGZvcihsZXQgaT0wO2k8NDk7aSsrKWcuYXBwZW5k"
    "Q2hpbGQoZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgiaSIpKTsKICBjb25zdCBjZWxscz1bLi4uZy5j"
    "aGlsZHJlbl07CiAgY29uc3QgRj1bWzE0LDcsMCw4LDYsMTMsMjBdLFsxNCw3LDEzLDIwLDE2LDI3"
    "LDIxXSxbMTQsMjAsMjcsMjEsMzQsMjQsMjhdLFsyNywyMSwzNCwyOCw0MSwzMiwzNV0sCiAgIFsz"
    "NCwyOCw0MSwzNSw0OCw0MCw0Ml0sWzM0LDI4LDQxLDM1LDQ4LDQyLDQ2XSxbMzQsMjgsNDEsMzUs"
    "NDgsNDIsMzhdLFszNCwyOCw0MSwzNSw0OCwzMCwyMV0sCiAgIFszNCwyOCw0MSw0OCwyMSwyMiwx"
    "NF0sWzM0LDI4LDQxLDIxLDE0LDE2LDI3XSxbMzQsMjgsMjEsMTQsMTAsMjAsMjddLFsyOCwyMSwx"
    "NCw0LDEzLDIwLDI3XSwKICAgWzI4LDIxLDE0LDEyLDYsMTMsMjBdLFsyOCwyMSwxNCw2LDEzLDIw"
    "LDExXSxbMjgsMjEsMTQsNiwxMywyMCwxMF0sWzE0LDYsMTMsMjAsOSw3LDIxXV07CiAgbGV0IGY9"
    "MDtjb25zdCBpdj1zZXRJbnRlcnZhbCgoKT0+e2NlbGxzLmZvckVhY2goYz0+Yy5jbGFzc0xpc3Qu"
    "cmVtb3ZlKCJhY3QiKSk7CiAgICBGW2YlRi5sZW5ndGhdLmZvckVhY2goaT0+Y2VsbHNbaV0mJmNl"
    "bGxzW2ldLmNsYXNzTGlzdC5hZGQoImFjdCIpKTtmKys7fSwxMDApOwogIHNldFRpbWVvdXQoKCk9"
    "PntjbGVhckludGVydmFsKGl2KTtzcC5jbGFzc0xpc3QuYWRkKCJnb25lIik7CiAgICBzZXRUaW1l"
    "b3V0KCgpPT57c3AucmVtb3ZlKCk7aWYod2luZG93Ll9fZG9ja1BsYWNlKXdpbmRvdy5fX2RvY2tQ"
    "bGFjZSh0cnVlKTsKICAgICAgaWYod2luZG93Ll9fdnRhYlBsYWNlKXdpbmRvdy5fX3Z0YWJQbGFj"
    "ZSh0cnVlKTt9LDYwMCk7fSxSTT8zMDA6MTc1MCk7Cn0pKCk7CgovKiBzdGFnZ2VyZWQgcmV2ZWFs"
    "IG9uIGV2ZXJ5IHZpZXcgY2hhbmdlICovCmZ1bmN0aW9uIHJldmVhbFN0YWdlKGVsKXsKICBpZigh"
    "ZWx8fFJNKXJldHVybjsKICBbLi4uZWwuY2hpbGRyZW5dLnNsaWNlKDAsMTQpLmZvckVhY2goKGMs"
    "aSk9PnsKICAgIGlmKCFjLmFuaW1hdGUpcmV0dXJuOwogICAgdHJ5ewogICAgICBjLmdldEFuaW1h"
    "dGlvbnMoKS5mb3JFYWNoKGE9PntpZihhLmlkPT09InJ2IilhLmNhbmNlbCgpO30pOwogICAgICBj"
    "b25zdCBhbj1jLmFuaW1hdGUoW3tvcGFjaXR5OjAsdHJhbnNmb3JtOiJ0cmFuc2xhdGVZKDE0cHgp"
    "IHNjYWxlKC45ODUpIn0se29wYWNpdHk6MSx0cmFuc2Zvcm06Im5vbmUifV0sCiAgICAgICAge2R1"
    "cmF0aW9uOjUyMCxkZWxheTppKjQwLGVhc2luZzoiY3ViaWMtYmV6aWVyKC4yMiwxLC4zNiwxKSIs"
    "ZmlsbDoiYmFja3dhcmRzIn0pOwogICAgICBhbi5pZD0icnYiOwogICAgfWNhdGNoKGUpe30KICB9"
    "KTt9CgovKiBudW1iZXIgdGlja2VyICovCmZ1bmN0aW9uIHRpY2tUbyhlbCx0byxkdXIpe2lmKCFl"
    "bClyZXR1cm47Y29uc3QgZnJvbT1wYXJzZUZsb2F0KGVsLnRleHRDb250ZW50LnJlcGxhY2UoL1te"
    "XGQuXS9nLCIiKSl8fDA7CiAgaWYoUk0pe2VsLnRleHRDb250ZW50PXRvLnRvRml4ZWQoMik7cmV0"
    "dXJufQogIGNvbnN0IHQwPXBlcmZvcm1hbmNlLm5vdygpOyhmdW5jdGlvbiBzdCh0KXtjb25zdCBw"
    "PU1hdGgubWluKDEsKHQtdDApLyhkdXJ8fDYwMCkpOwogICAgY29uc3QgZT0xLU1hdGgucG93KDEt"
    "cCwzKTtlbC50ZXh0Q29udGVudD0oZnJvbSsodG8tZnJvbSkqZSkudG9GaXhlZCgyKTsKICAgIGlm"
    "KHA8MSlyZXF1ZXN0QW5pbWF0aW9uRnJhbWUoc3QpO30pKHQwKTt9CgovKiBwcmVzcyByaXBwbGUg"
    "b24gZXZlcnkgYnV0dG9uICovCmRvY3VtZW50LmFkZEV2ZW50TGlzdGVuZXIoInBvaW50ZXJkb3du"
    "IixlPT57Y29uc3QgYj1lLnRhcmdldC5jbG9zZXN0KCIuYnRuLC5jaGlwcm93IGJ1dHRvbiwuc3Vi"
    "bmF2IGJ1dHRvbiIpOwogIGlmKCFifHxSTSlyZXR1cm47Y29uc3Qgcj1iLmdldEJvdW5kaW5nQ2xp"
    "ZW50UmVjdCgpLGQ9TWF0aC5tYXgoci53aWR0aCxyLmhlaWdodCkqMS40OwogIGNvbnN0IHM9ZG9j"
    "dW1lbnQuY3JlYXRlRWxlbWVudCgic3BhbiIpO3MuY2xhc3NOYW1lPSJyaXAiOwogIHMuc3R5bGUu"
    "Y3NzVGV4dD0ibGVmdDoiKyhlLmNsaWVudFgtci5sZWZ0KSsicHg7dG9wOiIrKGUuY2xpZW50WS1y"
    "LnRvcCkrInB4O3dpZHRoOiIrZCsicHg7aGVpZ2h0OiIrZCsicHgiOwogIGlmKGdldENvbXB1dGVk"
    "U3R5bGUoYikucG9zaXRpb249PT0ic3RhdGljIiliLnN0eWxlLnBvc2l0aW9uPSJyZWxhdGl2ZSI7"
    "CiAgYi5hcHBlbmRDaGlsZChzKTtzZXRUaW1lb3V0KCgpPT5zLnJlbW92ZSgpLDU2MCk7fSk7Cgov"
    "KiAxNSDCtyB0aWx0ICsgZ2xhcmUgKi8KZnVuY3Rpb24gYmluZFRpbHQoKXtkb2N1bWVudC5xdWVy"
    "eVNlbGVjdG9yQWxsKCJbZGF0YS10aWx0XSIpLmZvckVhY2goYz0+ewogIGlmKGMuX3R8fFJNKXJl"
    "dHVybjtjLl90PTE7CiAgYy5hZGRFdmVudExpc3RlbmVyKCJwb2ludGVybW92ZSIsZT0+e2NvbnN0"
    "IHI9Yy5nZXRCb3VuZGluZ0NsaWVudFJlY3QoKTsKICAgIGNvbnN0IHg9KGUuY2xpZW50WC1yLmxl"
    "ZnQpL3Iud2lkdGgseT0oZS5jbGllbnRZLXIudG9wKS9yLmhlaWdodDsKICAgIGMuc3R5bGUudHJh"
    "bnNmb3JtPSJwZXJzcGVjdGl2ZSg3MDBweCkgcm90YXRlWCgiKygoLjUteSkqOSkudG9GaXhlZCgy"
    "KSsiZGVnKSByb3RhdGVZKCIrKCh4LS41KSoxMSkudG9GaXhlZCgyKSsiZGVnKSB0cmFuc2xhdGVa"
    "KDRweCkiOwogICAgYy5zdHlsZS5zZXRQcm9wZXJ0eSgiLS1teCIsKHgqMTAwKSsiJSIpO2Muc3R5"
    "bGUuc2V0UHJvcGVydHkoIi0tbXkiLCh5KjEwMCkrIiUiKTt9KTsKICBjLmFkZEV2ZW50TGlzdGVu"
    "ZXIoInBvaW50ZXJsZWF2ZSIsKCk9PmMuc3R5bGUudHJhbnNmb3JtPSIiKTt9KTt9CgovKiBzdWNj"
    "ZXNzIGJ1cnN0ICovCmZ1bmN0aW9uIGJ1cnN0KCl7Y29uc3QgZng9ZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoImZ4Iik7aWYoIWZ4KXJldHVybjtmeC5jbGFzc0xpc3QuYWRkKCJvbiIpOwogIGNvbnN0"
    "IHc9ZngucXVlcnlTZWxlY3RvcigiLndhdmUiKTt3LnN0eWxlLmFuaW1hdGlvbj0ibm9uZSI7dm9p"
    "ZCB3Lm9mZnNldFdpZHRoO3cuc3R5bGUuYW5pbWF0aW9uPSIiOwogIHNldFRpbWVvdXQoKCk9PmZ4"
    "LmNsYXNzTGlzdC5yZW1vdmUoIm9uIiksOTAwKTt9CgovKiDilIDilIAgUGF5ICsgdmVyaWZ5IGRl"
    "cG9zaXQgZmxvdyAoNS1taW51dGUgd2luZG93LCB2ZXJpZnkgb25seSBvbiB0YXApIOKUgOKUgCAq"
    "LwpsZXQgZGVwVGljaz1udWxsLGRlcEpvYj1udWxsOwpmdW5jdGlvbiBhbW91bnRTaGVldCgpewog"
    "IGNvbnN0IHBpY2tzPVs1MCwxMDAsMjAwLDUwMCwxMDAwLDIwMDBdOwogIHNoZWV0KGA8aDMgc3R5"
    "bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj4ke0NNUy53YWxLaWNrfHwiQWRkIGZ1"
    "bmRzIn08L2gzPgogICAgPHAgY2xhc3M9InN1YiI+UGljayBhbiBhbW91bnQgb3IgdHlwZSB5b3Vy"
    "IG93bi48L3A+CiAgICA8ZGl2IGNsYXNzPSJjaGlwcyIgc3R5bGU9Im1hcmdpbjoxMnB4IDAgNHB4"
    "O2Rpc3BsYXk6ZmxleDtmbGV4LXdyYXA6d3JhcDtnYXA6N3B4Ij4KICAgICAgJHtwaWNrcy5tYXAo"
    "dj0+YDxidXR0b24gY2xhc3M9ImJ0biBzbSIgZGF0YS1waWNrPSIke3Z9IiBzdHlsZT0iZmxleDow"
    "IDAgYXV0byI+JHttb25leSh2KX08L2J1dHRvbj5gKS5qb2luKCIiKX0KICAgIDwvZGl2PgogICAg"
    "PGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5DdXN0b20g"
    "YW1vdW50PC9sYWJlbD4KICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGlkPSJhbXRpbiIgdHlwZT0i"
    "bnVtYmVyIiBpbnB1dG1vZGU9Im51bWVyaWMiIG1pbj0iMSIgcGxhY2Vob2xkZXI9IkVudGVyIGFt"
    "b3VudCI+PC9kaXY+CiAgICA8cCBjbGFzcz0ic3ViIiBpZD0iYW10aGludCIgc3R5bGU9Im1hcmdp"
    "bjowIDAgMTBweDtmb250LXNpemU6LjcycmVtIj48L3A+CiAgICA8YnV0dG9uIGNsYXNzPSJidG4g"
    "Y3RhIGJsb2NrIiBpZD0iYW10Z28iPkNvbnRpbnVlPC9idXR0b24+CiAgICA8YnV0dG9uIGNsYXNz"
    "PSJidG4gZ2hvc3QgYmxvY2sgc20iIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgZGF0YS1jbG9zZT5D"
    "YW5jZWw8L2J1dHRvbj5gKTsKICBjb25zdCBpbnA9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFt"
    "dGluIiksIGdvPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJhbXRnbyIpLAogICAgICAgIGhpbnQ9"
    "ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFtdGhpbnQiKTsKICBjb25zdCBsbz0oQURNJiZBRE0u"
    "cGF5JiZBRE0ucGF5Lm1pbil8fDEwLCBoaT0oQURNJiZBRE0ucGF5JiZBRE0ucGF5Lm1heCl8fDIw"
    "MDAwOwogIGhpbnQudGV4dENvbnRlbnQ9IkJldHdlZW4gIittb25leShsbykrIiBhbmQgIittb25l"
    "eShoaSkrIi4iOwogIGNvbnN0IHN0YXJ0PXY9PnsKICAgIGNvbnN0IG49TnVtYmVyKHYpOwogICAg"
    "aWYoIW58fG48PTApe3RvYXN0KCJFbnRlciBhbiBhbW91bnQgZmlyc3QiKTtpbnAmJmlucC5mb2N1"
    "cygpO3JldHVybjt9CiAgICBpZihuPGxvfHxuPmhpKXt0b2FzdCgiQWxsb3dlZCAiK21vbmV5KGxv"
    "KSsiIHRvICIrbW9uZXkoaGkpKTtyZXR1cm47fQogICAgZGVwb3NpdFNoZWV0KG4pOwogIH07CiAg"
    "ZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtcGlja10iKS5mb3JFYWNoKGI9PmIub25j"
    "bGljaz0oKT0+c3RhcnQoYi5kYXRhc2V0LnBpY2spKTsKICBnby5vbmNsaWNrPSgpPT5zdGFydChp"
    "bnAudmFsdWUpOwogIGlmKGlucClpbnAub25rZXlkb3duPWU9PntpZihlLmtleT09PSJFbnRlciIp"
    "c3RhcnQoaW5wLnZhbHVlKTt9OwogIHNldFRpbWVvdXQoKCk9PntpZihpbnApaW5wLmZvY3VzKCk7"
    "fSwyNTApOwp9CmFzeW5jIGZ1bmN0aW9uIGxpdmVEZXBvc2l0U2hlZXQoYSl7CiAgY2xlYXJJbnRl"
    "cnZhbChkZXBUaWNrKTtjbGVhclRpbWVvdXQoZGVwSm9iKTsKICBjb25zdCBkPWF3YWl0IGFwaVRy"
    "eSgiL2FwaS9kZXBvc2l0L2NyZWF0ZSIse2Ftb3VudDpOdW1iZXIoYSl9KTsKICBpZighZClyZXR1"
    "cm47CiAgY29uc3QgbWlucz1kLndpbmRvd3x8NTsKICBsZXQgbGVmdD1NYXRoLm1heCgwLE1hdGgu"
    "cm91bmQoKG5ldyBEYXRlKGQuZXhwaXJlc19hdCktbmV3IERhdGUoKSkvMTAwMCkpfHxtaW5zKjYw"
    "OwogIGNvbnN0IHRvdGFsPW1pbnMqNjA7CiAgbGV0IGRvbmU9ZmFsc2UsYnVzeT1mYWxzZTsKICBz"
    "aGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+JHtDTVMuc2hl"
    "ZXRUaXRsZX0gJHttb25leShkLmFtb3VudCl9PC9oMz4KICAgIDxwIGNsYXNzPSJzdWIiPiR7Q01T"
    "LnNoZWV0U3ViLnJlcGxhY2UoInttfSIsbWlucyl9PC9wPgogICAgJHtkLnFyP2A8ZGl2IGNsYXNz"
    "PSJxciIgc3R5bGU9InBhZGRpbmc6MDtvdmVyZmxvdzpoaWRkZW4iPjxpbWcgc3JjPSIke2QucXJ9"
    "IiBhbHQ9IlVQSSBRUiIgc3R5bGU9IndpZHRoOjEwMCU7ZGlzcGxheTpibG9jayI+PC9kaXY+YDoi"
    "In0KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5VUEkgSUQ8L3NwYW4+PGI+JHtlc2MoZC51cGl8"
    "fCIiKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+UGF5ZWU8L3NwYW4+PGI+"
    "JHtlc2MoZC5wYXllZXx8IiIpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5B"
    "bW91bnQ8L3NwYW4+PGI+JHttb25leShkLmFtb3VudCl9PC9iPjwvZGl2PgogICAgJHtkLmJvbnVz"
    "P2A8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+Qm9udXM8L3NwYW4+PGI+KyR7bW9uZXkoZC5ib251cyl9"
    "PC9iPjwvZGl2PmA6IiJ9CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+Tm90ZSAvIHJlZmVyZW5j"
    "ZTwvc3Bhbj48Yj4ke2VzYyhkLmNvZGUpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48"
    "c3Bhbj5FeHBpcmVzIGluPC9zcGFuPjxiIGlkPSJkcGNkIj4ke01hdGguZmxvb3IobGVmdC82MCl9"
    "OiR7U3RyaW5nKGxlZnQlNjApLnBhZFN0YXJ0KDIsIjAiKX08L2I+PC9kaXY+CiAgICA8ZGl2IGNs"
    "YXNzPSJ0aW1lcmJhciIgaWQ9ImRwYmFyIj48aSBzdHlsZT0id2lkdGg6MTAwJSI+PC9pPjwvZGl2"
    "PgogICAgPGRpdiBjbGFzcz0ic3RlcHMiIGlkPSJkc3RlcHMiPgogICAgICA8ZGl2IGNsYXNzPSJz"
    "dGVwIG9uIj48aT4xPC9pPiR7Q01TLnN0ZXAxfTwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGVw"
    "IiBpZD0iczIiPjxpPjI8L2k+JHtDTVMuc3RlcDJ9PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0"
    "ZXAiIGlkPSJzMyI+PGk+MzwvaT4ke0NNUy5zdGVwM308L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0i"
    "c3RlcCIgaWQ9InM0Ij48aT40PC9pPiR7Q01TLnN0ZXA0fTwvZGl2PgogICAgPC9kaXY+CiAgICA8"
    "ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+CiAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biIgaWQ9ImRwYXkiPvCfj6YgQ29weSBVUEkgSUQ8L2J1dHRvbj4KICAgICAgPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIGN0YSIgaWQ9ImR2ZXIiPiR7U1RPUkUuYXV0b2NoZWNrPT09ZmFsc2U/"
    "IuKchSBJIGhhdmUgcGFpZCI6IuKchSAiK0NNUy52ZXJpZnlCdG59PC9idXR0b24+PC9kaXY+CiAg"
    "ICAke1NUT1JFLmF1dG9jaGVjaz09PWZhbHNlPyIiOmA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCBi"
    "bG9jayBzbSIgaWQ9ImR1dHIiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+8J+nviBTZW5kIHRyYW5z"
    "YWN0aW9uIG5vLjwvYnV0dG9uPmB9CiAgICA8YnV0dG9uIGNsYXNzPSJidG4gZ2hvc3QgYmxvY2sg"
    "c20iIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgZGF0YS1jbG9zZT5DbG9zZTwvYnV0dG9uPmApOwog"
    "IGNvbnN0IGc9aWQ9PmRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKTsKICBjb25zdCBwYXlCPWco"
    "ImRwYXkiKSx2ZXJCPWcoImR2ZXIiKSx1dHJCPWcoImR1dHIiKTsKICBjb25zdCBhbGl2ZT0oKT0+"
    "ZygiZHBjZCIpJiZkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic2hlZXQiKS5jbGFzc0xpc3QuY29u"
    "dGFpbnMoIm9wZW4iKTsKICBmdW5jdGlvbiBleHBpcmUoKXsKICAgIGNsZWFySW50ZXJ2YWwoZGVw"
    "VGljayk7CiAgICBpZighYWxpdmUoKSlyZXR1cm47CiAgICBnKCJkcGNkIikudGV4dENvbnRlbnQ9"
    "IjA6MDAiO2coImRwYmFyIikuY2xhc3NMaXN0LmFkZCgibG93Iik7ZygiZHBiYXIiKS5maXJzdENo"
    "aWxkLnN0eWxlLndpZHRoPSIwJSI7CiAgICB2ZXJCLmRpc2FibGVkPXRydWU7dXRyQi5kaXNhYmxl"
    "ZD10cnVlO3ZlckIudGV4dENvbnRlbnQ9IkV4cGlyZWQiOwogICAgcGF5Qi5jbGFzc05hbWU9ImJ0"
    "biI7cGF5Qi50ZXh0Q29udGVudD0iTmV3IFJlcXVlc3QiO3BheUIub25jbGljaz0oKT0+ZGVwb3Np"
    "dFNoZWV0KGEpOwogICAgdG9hc3QoQ01TLm1zZ0V4cGlyZWQpOwogIH0KICBkZXBUaWNrPXNldElu"
    "dGVydmFsKCgpPT57CiAgICBpZighYWxpdmUoKSl7Y2xlYXJJbnRlcnZhbChkZXBUaWNrKTtyZXR1"
    "cm47fQogICAgbGVmdC0tOwogICAgZygiZHBjZCIpLnRleHRDb250ZW50PU1hdGgubWF4KDAsTWF0"
    "aC5mbG9vcihsZWZ0LzYwKSkrIjoiK1N0cmluZyhNYXRoLm1heCgwLGxlZnQlNjApKS5wYWRTdGFy"
    "dCgyLCIwIik7CiAgICBnKCJkcGJhciIpLmZpcnN0Q2hpbGQuc3R5bGUud2lkdGg9KE1hdGgubWF4"
    "KDAsbGVmdCkvdG90YWwqMTAwKSsiJSI7CiAgICBnKCJkcGJhciIpLmNsYXNzTGlzdC50b2dnbGUo"
    "ImxvdyIsbGVmdDw9NjApOwogICAgaWYobGVmdDw9MClleHBpcmUoKTsKICB9LDEwMDApOwogIGlm"
    "KHBheUIpcGF5Qi5vbmNsaWNrPSgpPT57CiAgICBpZihkb25lKXJldHVybjsKICAgIGcoInMyIiku"
    "Y2xhc3NOYW1lPSJzdGVwIG9uIjtnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCBydW4iOwogICAgdmVy"
    "Qi5jbGFzc0xpc3QucmVtb3ZlKCJnbGFzcyIpO3ZlckIuY2xhc3NMaXN0LmFkZCgiY3RhIik7CiAg"
    "ICB0cnl7bmF2aWdhdG9yLmNsaXBib2FyZCYmbmF2aWdhdG9yLmNsaXBib2FyZC53cml0ZVRleHQo"
    "ZC51cGl8fCIiKTt9Y2F0Y2goZSl7fQogICAgdG9hc3QoIlVQSSBJRCBjb3BpZWQ6ICIrKGQudXBp"
    "fHwiIikpOwogIH07CiAgaWYodmVyQil2ZXJCLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYoZG9u"
    "ZXx8YnVzeXx8bGVmdDw9MClyZXR1cm47CiAgICBpZihTVE9SRS5hdXRvY2hlY2s9PT1mYWxzZSl7"
    "ICAgICAgICAgIC8qIG5vIGF1dG9tYXRpYyBjaGVja2luZyDigJQgY29sbGVjdCBwcm9vZiAqLwog"
    "ICAgICBzaGVldChgPGgzIHN0eWxlPSJtYXJnaW46MCAwIDRweDtmb250LXNpemU6MXJlbSI+U2Vu"
    "ZCB5b3VyIHBheW1lbnQgcHJvb2Y8L2gzPgogICAgICAgIDxwIGNsYXNzPSJzdWIiPkEgc2NyZWVu"
    "c2hvdCBvZiB0aGUgc3VjY2Vzc2Z1bCBwYXltZW50IGFuZCB0aGUgVVRSLjwvcD4KICAgICAgICA8"
    "ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGxhYmVsPlNjcmVlbnNo"
    "b3Q8L2xhYmVsPgogICAgICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGlkPSJwZnNob3QiIHR5cGU9"
    "ImZpbGUiIGFjY2VwdD0iaW1hZ2UvKiI+PC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "PjxsYWJlbD5VVFIgLyB0cmFuc2FjdGlvbiBudW1iZXI8L2xhYmVsPgogICAgICAgICAgPGlucHV0"
    "IGNsYXNzPSJpbnAiIGlkPSJwZnV0ciIgaW5wdXRtb2RlPSJudW1lcmljIiBwbGFjZWhvbGRlcj0i"
    "MTIgZGlnaXRzIj48L2Rpdj4KICAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gY3RhIGJsb2NrIiBp"
    "ZD0icGZnbyI+U3VibWl0IGZvciByZXZpZXc8L2J1dHRvbj4KICAgICAgICA8YnV0dG9uIGNsYXNz"
    "PSJidG4gZ2hvc3QgYmxvY2sgc20iIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgZGF0YS1jbG9zZT5D"
    "YW5jZWw8L2J1dHRvbj5gKTsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInBmZ28iKS5v"
    "bmNsaWNrPWFzeW5jKCk9PnsKICAgICAgICBjb25zdCBmPWRvY3VtZW50LmdldEVsZW1lbnRCeUlk"
    "KCJwZnNob3QiKTsKICAgICAgICBjb25zdCB1PShkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicGZ1"
    "dHIiKS52YWx1ZXx8IiIpLnJlcGxhY2UoL1xEL2csIiIpOwogICAgICAgIGlmKHUubGVuZ3RoPDYp"
    "e3RvYXN0KCJFbnRlciB0aGUgVVRSIGZyb20geW91ciBwYXltZW50IGFwcCIpO3JldHVybjt9CiAg"
    "ICAgICAgY29uc3QgZmlsZT0oZiYmZi5maWxlcyYmZi5maWxlc1swXSl8fG51bGw7CiAgICAgICAg"
    "bGV0IHNob3Q9IiI7CiAgICAgICAgaWYoZmlsZSl7CiAgICAgICAgICBpZihmaWxlLnNpemU+OCox"
    "MDI0KjEwMjQpe3RvYXN0KCJUaGF0IGltYWdlIGlzIHRvbyBsYXJnZSAoOCBNQiBtYXgpIik7cmV0"
    "dXJuO30KICAgICAgICAgIHNob3Q9YXdhaXQgbmV3IFByb21pc2UocmVzPT57Y29uc3QgcmQ9bmV3"
    "IEZpbGVSZWFkZXIoKTsKICAgICAgICAgICAgcmQub25sb2FkPSgpPT5yZXMoU3RyaW5nKHJkLnJl"
    "c3VsdHx8IiIpKTtyZC5vbmVycm9yPSgpPT5yZXMoIiIpOwogICAgICAgICAgICByZC5yZWFkQXNE"
    "YXRhVVJMKGZpbGUpO30pOwogICAgICAgIH0KICAgICAgICBjb25zdCBidG49ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoInBmZ28iKTtidG4uZGlzYWJsZWQ9dHJ1ZTtidG4udGV4dENvbnRlbnQ9IlNl"
    "bmRpbmfigKYiOwogICAgICAgIGNvbnN0IHJyPWF3YWl0IGFwaVRyeSgiL2FwaS9kZXBvc2l0L2Ns"
    "YWltIix7aWQ6ZC5pZCx1dHI6dSxzaG90OnNob3R9KTsKICAgICAgICBidG4uZGlzYWJsZWQ9ZmFs"
    "c2U7YnRuLnRleHRDb250ZW50PSJTdWJtaXQgZm9yIHJldmlldyI7CiAgICAgICAgaWYoIXJyKXJl"
    "dHVybjsKICAgICAgICBjbG9zZVNoZWV0KCk7dG9hc3QoIlNlbnQg4oCUIHdlIHdpbGwgY29uZmly"
    "bSBpdCBzaG9ydGx5Iik7CiAgICAgICAgYXdhaXQgcmVmcmVzaCgpOwogICAgICB9OwogICAgICBy"
    "ZXR1cm47CiAgICB9CiAgICBidXN5PXRydWU7dmVyQi5kaXNhYmxlZD10cnVlO3ZlckIudGV4dENv"
    "bnRlbnQ9IkNoZWNraW5n4oCmIjtnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCBydW4iOwogICAgY29u"
    "c3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvZGVwb3NpdC92ZXJpZnkiLHtpZDpkLmlkfSk7CiAgICBi"
    "dXN5PWZhbHNlOwogICAgaWYoIWFsaXZlKCkpcmV0dXJuOwogICAgdmVyQi5kaXNhYmxlZD1mYWxz"
    "ZTt2ZXJCLnRleHRDb250ZW50PUNNUy52ZXJpZnlCdG47CiAgICBpZighcilyZXR1cm47CiAgICBp"
    "ZihyLnN0YXR1cz09PSJjb21wbGV0ZWQiKXsKICAgICAgZG9uZT10cnVlO2NsZWFySW50ZXJ2YWwo"
    "ZGVwVGljayk7CiAgICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIG9uIjtnKCJzNCIpLmNsYXNz"
    "TmFtZT0ic3RlcCBvbiI7CiAgICAgIGlmKHR5cGVvZiByLmJhbGFuY2U9PT0ibnVtYmVyIilCQUw9"
    "ci5iYWxhbmNlOwogICAgICBzeW5jQmFsKCk7YnVyc3QoKTt0b2FzdChDTVMubXNnT2spOwogICAg"
    "ICBzZXRUaW1lb3V0KCgpPT57Y2xvc2VTaGVldCgpO3JlZnJlc2goKTt9LDkwMCk7CiAgICB9ZWxz"
    "ZSBpZihyLnN0YXR1cz09PSJyZXZpZXciKXsKICAgICAgZygiczMiKS5jbGFzc05hbWU9InN0ZXAg"
    "b24iO3RvYXN0KCJQYXltZW50IHNlbnQgZm9yIGNoZWNraW5nIOKAlCB3ZSB3aWxsIGNvbmZpcm0g"
    "aXQgc2hvcnRseSIpOwogICAgfWVsc2UgaWYoci5zdGF0dXM9PT0iZXhwaXJlZCIpewogICAgICBl"
    "eHBpcmUoKTsKICAgIH1lbHNlewogICAgICB0b2FzdChDTVMubXNnTm9uZSk7CiAgICB9CiAgfTsK"
    "ICBpZih1dHJCKXV0ckIub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCB2PXByb21wdCgiRW50"
    "ZXIgdGhlIDEyLWRpZ2l0IFVUUiBmcm9tIHlvdXIgcGF5bWVudCBhcHAiKTsKICAgIGlmKCF2KXJl"
    "dHVybjsKICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2RlcG9zaXQvdXRyIix7aWQ6ZC5p"
    "ZCx1dHI6dn0pOwogICAgaWYocil0b2FzdCgiVGhhbmtzIOKAlCB3ZSBhcmUgY2hlY2tpbmcgeW91"
    "ciBwYXltZW50Iik7CiAgfTsKfQpmdW5jdGlvbiBkZXBvc2l0U2hlZXQoYSl7CiAgaWYoTElWRSly"
    "ZXR1cm4gbGl2ZURlcG9zaXRTaGVldChhKTsKICBjbGVhckludGVydmFsKGRlcFRpY2spO2NsZWFy"
    "VGltZW91dChkZXBKb2IpOwogIGNvbnN0IGNvZGU9KENNUy5wYXlQcmVmaXh8fCJQQVkiKStNYXRo"
    "LmZsb29yKDEwMDArTWF0aC5yYW5kb20oKSo4OTk5KTsKICBjb25zdCBtaW5zPU1hdGgubWF4KDEs"
    "cGFyc2VJbnQoQ01TLnBheVdpbmRvdywxMCl8fDUpLCB0b3RhbD1taW5zKjYwOwogIGxldCBsZWZ0"
    "PXRvdGFsLHBhaWQ9ZmFsc2UsZG9uZT1mYWxzZSxidXN5PWZhbHNlOwogIHNoZWV0KGA8aDMgc3R5"
    "bGU9Im1hcmdpbjowIDAgNHB4O2ZvbnQtc2l6ZToxcmVtIj4ke0NNUy5zaGVldFRpdGxlfSAke21v"
    "bmV5KGEpfTwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj4ke0NNUy5zaGVldFN1Yi5yZXBsYWNlKCJ7"
    "bX0iLG1pbnMpfTwvcD4KICAgIDxkaXYgY2xhc3M9InFyIj5RUiBDT0RFPGJyPihnZW5lcmF0ZWQg"
    "YnkgYm90KTwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPlVQSSBJRDwvc3Bhbj48Yj4k"
    "e2VzYyhTVE9SRS51cGl8fCJub3Qgc2V0IHlldCIpfTwvYj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9"
    "Imt2Ij48c3Bhbj5QYXllZTwvc3Bhbj48Yj4ke2VzYyhTVE9SRS5wYXllZXx8IuKAlCIpfTwvYj48"
    "L2Rpdj4KICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5BbW91bnQ8L3NwYW4+PGI+JHttb25leShh"
    "KX08L2I+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+Tm90ZSAvIHJlZmVyZW5jZTwv"
    "c3Bhbj48Yj4ke2NvZGV9PC9iPjwvZGl2PgogICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPkV4cGly"
    "ZXMgaW48L3NwYW4+PGIgaWQ9ImRwY2QiPiR7bWluc306MDA8L2I+PC9kaXY+CiAgICA8ZGl2IGNs"
    "YXNzPSJ0aW1lcmJhciIgaWQ9ImRwYmFyIj48aSBzdHlsZT0id2lkdGg6MTAwJSI+PC9pPjwvZGl2"
    "PgogICAgPGRpdiBjbGFzcz0ic3RlcHMiIGlkPSJkc3RlcHMiPgogICAgICA8ZGl2IGNsYXNzPSJz"
    "dGVwIG9uIj48aT4xPC9pPiR7Q01TLnN0ZXAxfTwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGVw"
    "IiBpZD0iczIiPjxpPjI8L2k+JHtDTVMuc3RlcDJ9PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0"
    "ZXAiIGlkPSJzMyI+PGk+MzwvaT4ke0NNUy5zdGVwM308L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0i"
    "c3RlcCIgaWQ9InM0Ij48aT40PC9pPiR7Q01TLnN0ZXA0fTwvZGl2PgogICAgPC9kaXY+CiAgICA8"
    "ZGl2IGNsYXNzPSJidG5yb3ciIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+CiAgICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBjdGEiIGlkPSJkcGF5Ij4ke0NNUy5wYXlCdG59PC9idXR0b24+CiAgICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBnbGFzcyIgaWQ9ImR2ZXIiPiR7Q01TLnZlcmlmeUJ0bn08L2J1dHRv"
    "bj48L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IGJsb2NrIHNtIiBpZD0iZHV0ciIg"
    "c3R5bGU9Im1hcmdpbi10b3A6OHB4Ij4ke0NNUy51dHJCdG59PC9idXR0b24+CiAgICA8YnV0dG9u"
    "IGNsYXNzPSJidG4gZ2hvc3QgYmxvY2sgc20iIHN0eWxlPSJtYXJnaW4tdG9wOjhweCIgZGF0YS1j"
    "bG9zZT5DbG9zZTwvYnV0dG9uPmApOwogIGNvbnN0IGc9aWQ9PmRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKGlkKTsKICBjb25zdCBwYXlCPWcoImRwYXkiKSx2ZXJCPWcoImR2ZXIiKSx1dHJCPWcoImR1"
    "dHIiKTsKICBmdW5jdGlvbiBmbXQobil7cmV0dXJuIE1hdGguZmxvb3Iobi82MCkrIjoiK1N0cmlu"
    "ZyhuJTYwKS5wYWRTdGFydCgyLCIwIik7fQogIGZ1bmN0aW9uIGFsaXZlKCl7cmV0dXJuIGcoImRw"
    "Y2QiKSYmZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInNoZWV0IikuY2xhc3NMaXN0LmNvbnRhaW5z"
    "KCJvcGVuIik7fQogIGZ1bmN0aW9uIGV4cGlyZSgpewogICAgY2xlYXJJbnRlcnZhbChkZXBUaWNr"
    "KTtjbGVhclRpbWVvdXQoZGVwSm9iKTsKICAgIGlmKCFhbGl2ZSgpKXJldHVybjsKICAgIGcoImRw"
    "Y2QiKS50ZXh0Q29udGVudD0iMDowMCI7ZygiZHBiYXIiKS5jbGFzc0xpc3QuYWRkKCJsb3ciKTtn"
    "KCJkcGJhciIpLmZpcnN0Q2hpbGQuc3R5bGUud2lkdGg9IjAlIjsKICAgIHBheUIuZGlzYWJsZWQ9"
    "dHJ1ZTt2ZXJCLmRpc2FibGVkPXRydWU7dXRyQi5kaXNhYmxlZD10cnVlOwogICAgZygiczIiKS5j"
    "bGFzc05hbWU9InN0ZXAiO2coInMzIikuY2xhc3NOYW1lPSJzdGVwIjsKICAgIHZlckIudGV4dENv"
    "bnRlbnQ9IkV4cGlyZWQiO3BheUIuY2xhc3NOYW1lPSJidG4iO3BheUIuZGlzYWJsZWQ9ZmFsc2U7"
    "cGF5Qi50ZXh0Q29udGVudD0iTmV3IFJlcXVlc3QiOwogICAgcGF5Qi5vbmNsaWNrPSgpPT5kZXBv"
    "c2l0U2hlZXQoYSk7CiAgICB0b2FzdChDTVMubXNnRXhwaXJlZCk7CiAgfQogIGRlcFRpY2s9c2V0"
    "SW50ZXJ2YWwoKCk9PnsKICAgIGlmKCFhbGl2ZSgpKXtjbGVhckludGVydmFsKGRlcFRpY2spO3Jl"
    "dHVybjt9CiAgICBsZWZ0LS07ZygiZHBjZCIpLnRleHRDb250ZW50PWZtdChNYXRoLm1heCgwLGxl"
    "ZnQpKTsKICAgIGcoImRwYmFyIikuZmlyc3RDaGlsZC5zdHlsZS53aWR0aD0oTWF0aC5tYXgoMCxs"
    "ZWZ0KS90b3RhbCoxMDApKyIlIjsKICAgIGcoImRwYmFyIikuY2xhc3NMaXN0LnRvZ2dsZSgibG93"
    "IixsZWZ0PD02MCk7CiAgICBpZihsZWZ0PD0wKWV4cGlyZSgpOwogIH0sMTAwMCk7CiAgaWYocGF5"
    "QilwYXlCLm9uY2xpY2s9KCk9PnsKICAgIGlmKGRvbmUpcmV0dXJuOwogICAgcGFpZD10cnVlO2co"
    "InMyIikuY2xhc3NOYW1lPSJzdGVwIG9uIjtnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCBydW4iOwog"
    "ICAgcGF5Qi50ZXh0Q29udGVudD0iUGF5IEFnYWluIjt2ZXJCLmNsYXNzTGlzdC5yZW1vdmUoImds"
    "YXNzIik7dmVyQi5jbGFzc0xpc3QuYWRkKCJjdGEiKTsKICAgIHRvYXN0KCJPcGVuaW5nIHlvdXIg"
    "VVBJIGFwcCDigJQgcGF5ICIrbW9uZXkoYSkrIiwgdGhlbiB0YXAgVmVyaWZ5Iik7CiAgfTsKICB2"
    "ZXJCLm9uY2xpY2s9KCk9PnsKICAgIGlmKGRvbmV8fGJ1c3l8fGxlZnQ8PTApcmV0dXJuOwogICAg"
    "YnVzeT10cnVlO3ZlckIuZGlzYWJsZWQ9dHJ1ZTt2ZXJCLnRleHRDb250ZW50PSJDaGVja2luZ+KA"
    "piI7CiAgICBnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCBydW4iO3RvYXN0KENNUy5tc2dDaGVja2lu"
    "Zyk7CiAgICBkZXBKb2I9c2V0VGltZW91dCgoKT0+ewogICAgICBidXN5PWZhbHNlOwogICAgICBp"
    "ZighYWxpdmUoKSlyZXR1cm47CiAgICAgIGlmKHBhaWQpewogICAgICAgIGRvbmU9dHJ1ZTtjbGVh"
    "ckludGVydmFsKGRlcFRpY2spOwogICAgICAgIGcoInMzIikuY2xhc3NOYW1lPSJzdGVwIG9uIjtn"
    "KCJzNCIpLmNsYXNzTmFtZT0ic3RlcCBvbiI7CiAgICAgICAgdmVyQi50ZXh0Q29udGVudD0iVmVy"
    "aWZpZWQiOwogICAgICAgIEJBTCs9TnVtYmVyKGEpO3N5bmNCYWwoKTtidXJzdCgpO3RvYXN0KENN"
    "Uy5tc2dPayk7CiAgICAgICAgc2V0VGltZW91dChjbG9zZVNoZWV0LDEyMDApOwogICAgICB9ZWxz"
    "ZXsKICAgICAgICBnKCJzMyIpLmNsYXNzTmFtZT0ic3RlcCI7dmVyQi5kaXNhYmxlZD1mYWxzZTt2"
    "ZXJCLnRleHRDb250ZW50PUNNUy52ZXJpZnlCdG47CiAgICAgICAgdG9hc3QoQ01TLm1zZ05vbmUp"
    "OwogICAgICB9CiAgICB9LDE3MDApOwogIH07CiAgdXRyQi5vbmNsaWNrPSgpPT57aWYobGVmdD4w"
    "KXRvYXN0KCJUaGFua3Mg4oCUIHdlIGFyZSBjaGVja2luZyB5b3VyIHBheW1lbnQiKTt9Owp9CmZ1"
    "bmN0aW9uIHN5bmNCYWwoKXt0aWNrVG8oZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJhbCIpLEJB"
    "TCk7dGlja1RvKGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJiYWwyIiksQkFMKTt9CmRvY3VtZW50"
    "LmFkZEV2ZW50TGlzdGVuZXIoImNsaWNrIixlPT57Y29uc3QgYz1lLnRhcmdldC5jbG9zZXN0KCJb"
    "ZGF0YS1jb25maXJtXSIpO2lmKCFjKXJldHVybjsKICBjb25zdCBwPStjLmRhdGFzZXQuY29uZmly"
    "bTtpZihCQUw8cCl7dG9hc3QoIk5vdCBlbm91Z2ggYmFsYW5jZSDigJQgYWRkIGZ1bmRzIik7cmV0"
    "dXJufQogIEJBTC09cDtzeW5jQmFsKCk7Y2xvc2VTaGVldCgpO2J1cnN0KCk7dG9hc3QoIlB1cmNo"
    "YXNlZCDigJQgT1RQIGlzIGluIE9yZGVycyIpO30pOwoKLyog4pSA4pSAIEFETUlOIOKUgOKUgCAq"
    "LwpsZXQgQVRBQj0iZGFzaCI7CmRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIiNhc3ViIGJ1dHRv"
    "biIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFs"
    "bCgiI2FzdWIgYnV0dG9uIikuZm9yRWFjaCh4PT54LmNsYXNzTGlzdC5yZW1vdmUoIm9uIikpO2Iu"
    "Y2xhc3NMaXN0LmFkZCgib24iKTsKICBBVEFCPWIuZGF0YXNldC5hO3JlbmRlckFkbWluKCk7d2lu"
    "ZG93LnNjcm9sbFRvKDAsMCk7fSk7CmZ1bmN0aW9uIHJlbmRlckFkbWluKCl7CiAgY29uc3Qgc3Q9"
    "ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImFzdGFnZSIpOwogIGlmKCFhZG1pbkFsbG93ZWQoKSl7"
    "c3QuaW5uZXJIVE1MPSc8ZGl2IGNsYXNzPSJjYXJkIiBzdHlsZT0ibWFyZ2luOjE2cHg7dGV4dC1h"
    "bGlnbjpjZW50ZXIiPicKICAgICsnPGgzIHN0eWxlPSJtYXJnaW46MCAwIDZweCI+QWRtaW5zIG9u"
    "bHk8L2gzPicKICAgICsnPHAgY2xhc3M9InN1YiIgc3R5bGU9Im1hcmdpbjowIj5UaGlzIHBhbmVs"
    "IGlzIG5vdCBhdmFpbGFibGUgb24geW91ciBhY2NvdW50LjwvcD48L2Rpdj4nO3JldHVybjt9CiAg"
    "c3QuaW5uZXJIVE1MPSh7ZGFzaDphRGFzaCxjaGFubmVsczphQ2hhbm5lbHMsbG9nczphTG9ncyxw"
    "YXk6YVBheSxjb250ZW50OmFDb250ZW50LGZlYXR1cmVzOmFGZWF0dXJlcyxtYXJrZXQ6YU1hcmtl"
    "dCxhbmFseXRpY3M6YUFuYWx5dGljcyxzZWN1cml0eTphU2VjdXJpdHksc3RvY2s6YVN0b2NrLG9y"
    "ZGVyczphT3JkZXJzLHVzZXJzOmFVc2VycyxzZXR0aW5nczphU2V0dGluZ3N9KVtBVEFCXSgpOwog"
    "IGJpbmRBZG1pbigpO3JldmVhbFN0YWdlKHN0KTtiaW5kVGlsdCgpO2lmKHdpbmRvdy5fX3Z0YWJQ"
    "bGFjZSlyZXF1ZXN0QW5pbWF0aW9uRnJhbWUod2luZG93Ll9fdnRhYlBsYWNlKTt9CgpmdW5jdGlv"
    "biBhRGFzaCgpewogIHJldHVybiBgPGRpdiBjbGFzcz0ia3BpcyI+JHtbWyJSZXZlbnVlIHRvZGF5"
    "Iixtb25leShBRE0ua3BpLnJldildLFsiT3JkZXJzIHRvZGF5IixhZG1OdW0oQURNLmtwaS5vcmRl"
    "cnMpXSwKICAgICAgWyJVc2VycyIsYWRtTnVtKEFETS5rcGkudXNlcnMpXSxbIkF1dG8tdmVyaWZp"
    "ZWQiLGFkbU51bShBRE0ua3BpLmF1dG8pXV0KICAgIC5tYXAoKFtrLHZdKT0+YDxkaXYgY2xhc3M9"
    "ImtwaSI+PHNtYWxsPiR7a308L3NtYWxsPjxiPiR7dn08L2I+PC9kaXY+YCkuam9pbigiIil9PC9k"
    "aXY+CiAgIDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24t"
    "aXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+PGkgY2xh"
    "c3M9ImRvdCI+PC9pPlBheW1lbnQgY2hlY2tpbmc8L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxl"
    "eDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+JHtBRE0u"
    "cGF5LmdtYWlsX3NldD8iR21haWwgd2F0Y2hlciBvbiDCtyAiK0FETS5wYXkucGVuZGluZysiIGF3"
    "YWl0aW5nIHJldmlldyI6IkdtYWlsIG5vdCBzZXQg4oCUIGRlcG9zaXRzIGdvIHRvIG1hbnVhbCBy"
    "ZXZpZXcifTwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWEtZ289"
    "InBheSI+T3BlbjwvYnV0dG9uPjwvZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+TGFzdCA3"
    "IGRheXM8L2gzPjxwIGNsYXNzPSJzdWIiPlJldmVudWUgdHJlbmQ8L3A+CiAgICAgPGRpdiBzdHls"
    "ZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmZsZXgtZW5kO2dhcDo3cHg7aGVpZ2h0Ojk2cHg7"
    "bWFyZ2luLXRvcDoxNHB4Ij4KICAgICAgICR7QURNLmJhcnMubWFwKGg9PmA8aSBzdHlsZT0iZmxl"
    "eDoxO2hlaWdodDoke2h9JTtib3JkZXItcmFkaXVzOjdweCA3cHggM3B4IDNweDtiYWNrZ3JvdW5k"
    "OnZhcigtLXBpbGxncmFkKTtkaXNwbGF5OmJsb2NrIj48L2k+YCkuam9pbigiIil9PC9kaXY+PC9k"
    "aXY+CiAgICR7RygiZGlnZXN0IixgPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3Bs"
    "YXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxzcGFuIGNsYXNzPSJw"
    "aWxsIG9uIj48aSBjbGFzcz0iZG90Ij48L2k+RGlnZXN0PC9zcGFuPgogICAgIDxkaXYgc3R5bGU9"
    "ImZsZXg6MTttaW4td2lkdGg6MDtmb250LXNpemU6Ljc0cmVtO2NvbG9yOnZhcigtLXR4MikiPk5l"
    "eHQgc3VtbWFyeSBhdCAyMzozMDwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0"
    "IiBkYXRhLWEtZ289ImFuYWx5dGljcyI+VmlldzwvYnV0dG9uPjwvZGl2PmApfQogICAke0coInNh"
    "bmRib3giLGA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWdu"
    "LWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPHNwYW4gY2xhc3M9InBpbGwiPlNhbmRib3g8"
    "L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowO2ZvbnQtc2l6ZTouNzRy"
    "ZW07Y29sb3I6dmFyKC0tdHgyKSI+JHtBRE0uc2V0dGluZ3Muc2FuZGJveD8iVGVzdCBtb2RlIGlz"
    "IE9OIjoiVGVzdCBtb2RlIGlzIG9mZiDigJQgcmVhbCBtb25leSBpcyBsaXZlIn08L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LnNhbmRib3gpPyIgb24iOiIifSIgZGF0"
    "YS1zZXR0aW5nPSJzYW5kYm94Ij48L2Rpdj48L2Rpdj5gKX0KICAgPGRpdiBjbGFzcz0ic2VjIj48"
    "aDI+UXVpY2sgQWN0aW9uczwvaDI+PC9kaXY+CiAgIDxkaXYgY2xhc3M9ImdyaWQiPgogICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLXFhPSJicm9hZGNhc3QiPkJyb2FkY2FzdDwvYnV0"
    "dG9uPgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBzb2Z0IiBkYXRhLXFhPSJzdG9jayI+QWRkIFN0"
    "b2NrPC9idXR0b24+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEtcWE9ImZsYXNo"
    "Ij5GbGFzaCBTYWxlPC9idXR0b24+CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIHNvZnQiIGRhdGEt"
    "cWE9ImJhY2t1cCI+QmFja3VwIERCPC9idXR0b24+PC9kaXY+YDsKfQoKZnVuY3Rpb24gYUNoYW5u"
    "ZWxzKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+Rm9yY2Utam9pbiBjaGFubmVs"
    "czwvaDM+CiAgICA8cCBjbGFzcz0ic3ViIj5BZGQgb3IgcmVtb3ZlIGFueSBudW1iZXIgb2YgY2hh"
    "bm5lbHMuIFNhdmVkIHRvIHRoZSBkYXRhYmFzZSDigJQgaW5zdGFudCwgbm8gcmVzdGFydC48L3A+"
    "CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTNweCI+PGxhYmVsPkNo"
    "YW5uZWwgbGluaywgQHVzZXJuYW1lIG9yIC0xMDAgSUQ8L2xhYmVsPgogICAgICA8aW5wdXQgY2xh"
    "c3M9ImlucCIgaWQ9ImNoLWluIiBwbGFjZWhvbGRlcj0iQGNoYW5uZWwgwrcgdC5tZS8raW52aXRl"
    "IMK3IC0xMDDigKYiPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5UeXBlPC9s"
    "YWJlbD4KICAgICAgPHNlbGVjdCBjbGFzcz0iaW5wIiBpZD0iY2gtdHlwZSI+PG9wdGlvbiB2YWx1"
    "ZT0iMSI+UmVxdWlyZWQg4oCUIG11c3Qgam9pbjwvb3B0aW9uPjxvcHRpb24gdmFsdWU9IjAiPk9w"
    "dGlvbmFsIOKAlCBqdXN0IHN1Z2dlc3RlZDwvb3B0aW9uPjwvc2VsZWN0PjwvZGl2PgogICAgPGRp"
    "diBjbGFzcz0iYnRucm93Ij48YnV0dG9uIGNsYXNzPSJidG4iIGlkPSJjaC1hZGQiPiR7aWMoInBs"
    "dXMiKX0gQWRkIENoYW5uZWw8L2J1dHRvbj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biBnaG9zdCIg"
    "ZGF0YS1taXNjPSJjaGFubmVsdGVzdCI+VGVzdCBCb3Q8L2J1dHRvbj48L2Rpdj48L2Rpdj4KICA8"
    "ZGl2IGNsYXNzPSJzZWMiPjxoMj5BY3RpdmUgKCR7Q0hBTk5FTFMubGVuZ3RofSk8L2gyPjwvZGl2"
    "PgogICR7Q0hBTk5FTFMubWFwKChjLGkpPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCI+CiAgICAg"
    "PGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAg"
    "ICAgICA8ZGl2IGNsYXNzPSJmbGFnIj4ke2ljKCJzZW5kIil9PC9kaXY+CiAgICAgICA8ZGl2IHN0"
    "eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJmb250LXNpemU6LjgzcmVtO2Rpc3Bs"
    "YXk6YmxvY2s7d2hpdGUtc3BhY2U6bm93cmFwO292ZXJmbG93OmhpZGRlbjt0ZXh0LW92ZXJmbG93"
    "OmVsbGlwc2lzIj4ke2VzYyhjLm5hbWUpfTwvYj4KICAgICAgICAgPHNtYWxsIHN0eWxlPSJjb2xv"
    "cjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjlyZW0iPiR7ZXNjKGMuaGFuZGxlKX0gwrcgJHtjLm1l"
    "bWJlcnN9IG1lbWJlcnM8L3NtYWxsPjwvZGl2PgogICAgICAgPGRpdiBjbGFzcz0idGcgc20gJHtj"
    "Lm9uPyJvbiI6IiJ9IiBkYXRhLWNob249IiR7Yy5pZH0iPjwvZGl2PjwvZGl2PgogICAgIDxkaXYg"
    "c3R5bGU9ImRpc3BsYXk6ZmxleDtnYXA6NnB4O21hcmdpbi10b3A6MTBweDtmbGV4LXdyYXA6d3Jh"
    "cCI+CiAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgJHtjLnJlcT8iIjoic29mdCJ9IiBkYXRh"
    "LWNocmVxPSIke2MuaWR9Ij4ke2MucmVxPyJSZXF1aXJlZCI6Ik9wdGlvbmFsIn08L2J1dHRvbj4K"
    "ICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLWNvcHk9InRleHQiIGRhdGEt"
    "Y29weS12YWx1ZT0iJHtlc2MoYy5oYW5kbGUpfSI+Q29weTwvYnV0dG9uPgogICAgICAgPGJ1dHRv"
    "biBjbGFzcz0iYnRuIHhzIGJhZCIgc3R5bGU9Im1hcmdpbi1sZWZ0OmF1dG8iIGRhdGEtY2hkZWw9"
    "IiR7Yy5pZH0iPlJlbW92ZTwvYnV0dG9uPjwvZGl2PjwvZGl2PmApLmpvaW4oIiIpCiAgIHx8Jzxk"
    "aXYgY2xhc3M9ImNhcmQiPjxwIGNsYXNzPSJzdWIiPk5vIGNoYW5uZWxzIOKAlCB1c2VycyBjYW4g"
    "ZW50ZXIgZnJlZWx5LjwvcD48L2Rpdj4nfWA7Cn0KCmZ1bmN0aW9uIGFMb2dzKCl7CiAgY29uc3Qg"
    "b3B0cz1pZD0+TE9HQ0gubWFwKGw9PmA8b3B0aW9uIHZhbHVlPSIke2wuaWR9IiAke2wuaWQ9PT1p"
    "ZD8ic2VsZWN0ZWQiOiIifT4ke2VzYyhsLm5hbWUpfTwvb3B0aW9uPmApLmpvaW4oIiIpOwogIHJl"
    "dHVybiBgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmct"
    "dG9wOjAiPgogICAgICA8ZGl2IGNsYXNzPSJ0Ij5Mb2dnaW5nIG1hc3RlciBzd2l0Y2g8c21hbGw+"
    "T2ZmID0gdGhlIGJvdCBwb3N0cyBubyBsb2dzIGFueXdoZXJlPC9zbWFsbD48L2Rpdj4KICAgICAg"
    "PGRpdiBjbGFzcz0idGcgJHtMT0dTX01BU1RFUj8ib24iOiIifSIgZGF0YS1tYXN0ZXI+PC9kaXY+"
    "PC9kaXY+PC9kaXY+CiAgIDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Mb2cgY2hhbm5lbHM8L2gzPjxw"
    "IGNsYXNzPSJzdWIiPkFkZCBhcyBtYW55IGFzIHlvdSBsaWtlLiBUaGUgc3RhcnJlZCBvbmUgaXMg"
    "dGhlIHByZWZlcnJlZC9kZWZhdWx0IGNoYW5uZWwuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxk"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+Q2hhbm5lbCBsaW5rLCBAdXNlcm5hbWUg"
    "b3IgLTEwMCBJRDwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImxnLWluIiBw"
    "bGFjZWhvbGRlcj0iQGxvZ3NfY2hhbm5lbCDCtyAtMTAw4oCmIj48L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJidG5yb3ciPjxidXR0b24gY2xhc3M9ImJ0biIgaWQ9ImxnLWFkZCI+JHtpYygicGx1cyIp"
    "fSBBZGQgTG9nIENoYW5uZWw8L2J1dHRvbj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc29mdCIg"
    "ZGF0YS1taXNjPSJsb2d0ZXN0Ij5TZW5kIFRlc3Q8L2J1dHRvbj48L2Rpdj48L2Rpdj4KICAgJHtM"
    "T0dDSC5tYXAobD0+YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7"
    "YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICAgPGRpdiBjbGFzcz0ic3RhciAke2wu"
    "cHJpbWFyeT8ib24iOiIifSIgZGF0YS1sZ3N0YXI9IiR7bC5pZH0iPiR7aWMoInN0YXIiKX08L2Rp"
    "dj4KICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0iZm9udC1z"
    "aXplOi44M3JlbTtkaXNwbGF5OmJsb2NrO3doaXRlLXNwYWNlOm5vd3JhcDtvdmVyZmxvdzpoaWRk"
    "ZW47dGV4dC1vdmVyZmxvdzplbGxpcHNpcyI+JHtlc2MobC5uYW1lKX08L2I+CiAgICAgICAgPHNt"
    "YWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjlyZW0iPiR7ZXNjKGwuaGFu"
    "ZGxlKX0ke2wucHJpbWFyeT8iIMK3IHByZWZlcnJlZCI6IiJ9PC9zbWFsbD48L2Rpdj4KICAgICAg"
    "PGRpdiBjbGFzcz0idGcgc20gJHtsLm9uPyJvbiI6IiJ9IiBkYXRhLWxnb249IiR7bC5pZH0iPjwv"
    "ZGl2PgogICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgYmFkIiBkYXRhLWxnZGVsPSIke2wuaWR9"
    "Ij5SZW1vdmU8L2J1dHRvbj48L2Rpdj5gKS5qb2luKCIiKQogICAgfHwnPGRpdiBjbGFzcz0iY2Fy"
    "ZCI+PHAgY2xhc3M9InN1YiI+Tm8gbG9nIGNoYW5uZWxzIHlldC48L3A+PC9kaXY+J30KICAgPGRp"
    "diBjbGFzcz0ic2VjIj48aDI+RXZlbnRzICZhbXA7IHJvdXRpbmc8L2gyPjxhIGRhdGEtZXYtYWxs"
    "PlRvZ2dsZSBhbGw8L2E+PC9kaXY+CiAgIDxkaXYgY2xhc3M9ImNhcmQiPiR7RVZFTlRTLm1hcCgo"
    "ZSxpKT0+YAogICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiIHN0eWxlPSJmbGV4"
    "OjEiPiR7ZXNjKGUuayl9CiAgICAgICA8c21hbGw+PHNlbGVjdCBjbGFzcz0iaW5wIiBzdHlsZT0i"
    "aGVpZ2h0OjMwcHg7Zm9udC1zaXplOi43cmVtO21hcmdpbi10b3A6NXB4O21heC13aWR0aDoxOTBw"
    "eCIgZGF0YS1ldmNoPSIke2l9IiAke2Uub24/IiI6ImRpc2FibGVkIn0+JHtvcHRzKGUuY2gpfTwv"
    "c2VsZWN0Pjwvc21hbGw+PC9kaXY+CiAgICAgICA8ZGl2IGNsYXNzPSJ0ZyAke2Uub24/Im9uIjoi"
    "In0iIGRhdGEtZXZvbj0iJHtpfSI+PC9kaXY+PC9kaXY+YCkuam9pbigiIil9PC9kaXY+CiAgIDxk"
    "aXYgY2xhc3M9ImNhcmQiPjxoMz5SZWNlbnQgbG9nIG91dHB1dDwvaDM+CiAgICAgJHsoKEFETS5s"
    "b2dmZWVkJiZBRE0ubG9nZmVlZC5sZW5ndGgpP0FETS5sb2dmZWVkLm1hcChsPT5bbC53aGF0LGwu"
    "d2hlcmUsbC53aGVuXSk6W10pCiAgICAgICAubWFwKChbdCxjLHddKT0+YDxkaXYgY2xhc3M9Imt2"
    "Ij48c3Bhbj4ke3R9PC9zcGFuPjxiIHN0eWxlPSJmb250LXNpemU6LjdyZW0iPiR7Y30gwrcgJHt3"
    "fTwvYj48L2Rpdj5gKS5qb2luKCIiKX08L2Rpdj5gOwp9CgpmdW5jdGlvbiBhUGF5KCl7CiAgcmV0"
    "dXJuIGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10"
    "b3A6MCI+CiAgICAgIDxkaXYgY2xhc3M9InQiPkdtYWlsIGF1dG8tdmVyaWZ5PHNtYWxsPiR7QURN"
    "LnBheS5nbWFpbF9zZXQ/IlJlYWRpbmcgIitlc2MoQURNLnBheS5nbWFpbCk6Ik5vdCBjb25maWd1"
    "cmVkIOKAlCBkZXBvc2l0cyBxdWV1ZSBmb3IgcmV2aWV3In08L3NtYWxsPjwvZGl2PgogICAgICA8"
    "ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmdtYWlsX2VuYWJsZWQpPyIgb24iOiIifSIg"
    "ZGF0YS1zZXR0aW5nPSJnbWFpbF9lbmFibGVkIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNz"
    "PSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5BdXRvLWFwcHJvdmUgc21hbGwgZGVwb3NpdHM8c21hbGw+"
    "U2tpcCByZXZpZXcgYmVsb3cgdGhlIHRocmVzaG9sZDwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBj"
    "bGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5nbWFpbF9lbmFibGVkKT8iIG9uIjoiIn0iIGRhdGEt"
    "c2V0dGluZz0iZ21haWxfZW5hYmxlZCI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ic3dh"
    "cCI+PGRpdiBjbGFzcz0idCI+TWFudWFsIFVUUiBmYWxsYmFjazxzbWFsbD5MZXQgdXNlcnMgc3Vi"
    "bWl0IGEgVVRSIGlmIGF1dG8tdmVyaWZ5IG1pc3Nlczwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBj"
    "bGFzcz0idGckeyhBRE0uc3cmJkFETS5zdy5hdXRvZGVwKT8iIG9uIjoiIn0iIGRhdGEtc2V0dGlu"
    "Zz0iYXV0b2RlcCI+PC9kaXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFz"
    "cz0idCI+VmVyaWZ5IG9ubHkgd2hlbiB1c2VyIHRhcHMgVmVyaWZ5PHNtYWxsPk5vIGJhY2tncm91"
    "bmQgY2hlY2tpbmcg4oCUIHNhdmVzIEdtYWlsIHF1b3RhPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LnV0cl9mYWxsYmFjayk/IiBvbiI6IiJ9IiBkYXRh"
    "LXNldHRpbmc9InV0cl9mYWxsYmFjayI+PC9kaXY+PC9kaXY+PC9kaXY+CiAgIDxkaXYgY2xhc3M9"
    "ImNhcmQiPjxoMz5VUEk8L2gzPjxwIGNsYXNzPSJzdWIiPiR7QURNLnBheS51cGk/IkluIHVzZSBi"
    "eSBldmVyeSBwYXltZW50IHJlcXVlc3QuIjoiTm90IHNldCB5ZXQg4oCUIHRvcC11cHMgYXJlIGRp"
    "c2FibGVkIHVudGlsIHlvdSBhZGQgaXQuIn08L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0"
    "eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJlbD5VUEkgSUQ8L2xhYmVsPgogICAgICAgPGlucHV0"
    "IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJ1cGlfaWQiIHZhbHVlPSIke2VzYyhBRE0ucGF5LnVwaXx8"
    "IiIpfSIgcGxhY2Vob2xkZXI9InlvdXJoYW5kbGVAYmFuayI+PC9kaXY+CiAgICAgPGRpdiBjbGFz"
    "cz0iZmllbGQiPjxsYWJlbD5QYXllZSBuYW1lPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0i"
    "aW5wIiBkYXRhLXNldD0idXBpX25hbWUiIHZhbHVlPSIke2VzYyhBRE0ucGF5LnBheWVlfHwiIil9"
    "IiBwbGFjZWhvbGRlcj0iWW91ciBTdG9yZSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQi"
    "PjxsYWJlbD5Ob3RlIHByZWZpeDwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0"
    "YS1zZXQ9InBheV9wcmVmaXgiIHZhbHVlPSIke2VzYyhBRE0ucGF5LnByZWZpeHx8IlBBWSIpfSI+"
    "PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5QYXltZW50IHdpbmRvdyAobWlu"
    "dXRlcyk8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJwYXlfd2lu"
    "ZG93IiB2YWx1ZT0iJHtBRE0ucGF5LndpbmRvd30iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZp"
    "ZWxkIj48bGFiZWw+TWluaW11bSBkZXBvc2l0PC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0i"
    "aW5wIiBkYXRhLXNldD0ibWluX2RlcG9zaXQiIHZhbHVlPSIke0FETS5wYXkubWlufSI+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5NYXhpbXVtIGRlcG9zaXQ8L2xhYmVsPgog"
    "ICAgICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtc2V0PSJtYXhfZGVwb3NpdCIgdmFsdWU9IiR7"
    "QURNLnBheS5tYXh9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkF1dG8t"
    "YXBwcm92ZSB1bmRlciAoMCA9IG9mZik8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAi"
    "IGRhdGEtc2V0PSJhdXRvZGVwX21heCIgdmFsdWU9IiR7QURNLnBheS5hdXRvZGVwfSI+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5HbWFpbCBhZGRyZXNzPC9sYWJlbD4KICAg"
    "ICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBkYXRhLXNldD0iZ21haWxfaWQiIHZhbHVlPSIke2VzYyhB"
    "RE0ucGF5LmdtYWlsfHwiIil9IiBwbGFjZWhvbGRlcj0ic3RvcmVAZ21haWwuY29tIj48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkdtYWlsIGFwcCBwYXNzd29yZDwvbGFiZWw+"
    "CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9ImdtYWlsX3Bhc3MiIHR5cGU9InBh"
    "c3N3b3JkIiBwbGFjZWhvbGRlcj0iJHtBRE0ucGF5LmdtYWlsX3NldD8ic2F2ZWQg4oCUIHR5cGUg"
    "dG8gcmVwbGFjZSI6Im5vdCBzZXQifSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFu"
    "PldhaXRpbmcgZm9yIHJldmlldzwvc3Bhbj48Yj4ke0FETS5wYXkucGVuZGluZ308L2I+PC9kaXY+"
    "PC9kaXY+CiAgICR7RygiY3J5cHRvIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkNyeXB0byB3YWxs"
    "ZXQ8L2gzPjxwIGNsYXNzPSJzdWIiPlVTRFQtVFJDMjAgZGVwb3NpdHMsIGF1dG8tY29uZmlybWVk"
    "IG9uLWNoYWluLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6"
    "MTBweCI+PGxhYmVsPldhbGxldCBhZGRyZXNzPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgcGxh"
    "Y2Vob2xkZXI9IlTigKYiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+Q29u"
    "ZmlybWF0aW9ucyByZXF1aXJlZDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxIj48"
    "L2Rpdj4KICAgICA8L2Rpdj5gKX0KICAgJHtHKCJ1cGlSb3RhdGUiLGA8ZGl2IGNsYXNzPSJjYXJk"
    "Ij48aDM+VVBJIHJvdGF0aW9uPC9oMz48cCBjbGFzcz0ic3ViIj5TcHJlYWQgbG9hZCBhbmQgc3Vy"
    "dml2ZSBhIGZyb3plbiBoYW5kbGUuPC9wPgogICAgICR7W1siaGFuZGxlMUBmYW0iLCJBY3RpdmUi"
    "XSxbImhhbmRsZTJAZmFtIiwiQWN0aXZlIl0sWyJoYW5kbGUzQHVwaSIsIlBhdXNlZCJdXQogICAg"
    "ICAgLm1hcCgoW2gsc3RdKT0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj4ke2h9PC9zcGFuPjxiPjxz"
    "cGFuIGNsYXNzPSJwaWxsICR7c3Q9PT0iQWN0aXZlIj8ib24iOiJvZmYifSI+JHtzdH08L3NwYW4+"
    "PC9iPjwvZGl2PmApLmpvaW4oIiIpfQogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFy"
    "Z2luLXRvcDoxMHB4Ij48ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7Z2FwOjdweCI+CiAgICAgICA8"
    "aW5wdXQgY2xhc3M9ImlucCIgcGxhY2Vob2xkZXI9ImFub3RoZXJAdXBpIj48YnV0dG9uIGNsYXNz"
    "PSJidG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0idXBpYWRkIj5BZGQ8L2J1dHRvbj48"
    "L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJkZXBCb251cyIsYDxkaXYgY2xhc3M9ImNhcmQi"
    "PjxoMz5EZXBvc2l0IGJvbnVzPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1h"
    "cmdpbi10b3A6OHB4Ij48bGFiZWw+4oK5NTAwIGFuZCBhYm92ZSAoJSk8L2xhYmVsPjxpbnB1dCBj"
    "bGFzcz0iaW5wIiB2YWx1ZT0iNSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJl"
    "bD7igrkxMDAwIGFuZCBhYm92ZSAoJSk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0i"
    "OCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD7igrkyMDAwIGFuZCBhYm92"
    "ZSAoJSk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMTIiPjwvZGl2PjwvZGl2PmAp"
    "fQogICAke0coImRlcExpbWl0cyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5EZXBvc2l0IGxpbWl0"
    "czwvaDM+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxh"
    "YmVsPk1pbmltdW0gKOKCuSk8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMTAiPjwv"
    "ZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+TWF4aW11bSAo4oK5KTwvbGFiZWw+"
    "PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIyMDAwMCI+PC9kaXY+PC9kaXY+YCl9CiAgICR7Rygi"
    "d3JvbmdBbXQiLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFk"
    "ZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+V3JvbmctYW1vdW50IGhhbmRsaW5nPHNtYWxsPkNy"
    "ZWRpdCB3aGF0IHdhcyBhY3R1YWxseSBwYWlkIGFuZCBmbGFnIGl0PC9zbWFsbD48L2Rpdj4KICAg"
    "ICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LnZlcmlmeV9vbl90YXApPyIgb24iOiIi"
    "fSIgZGF0YS1zZXR0aW5nPSJ2ZXJpZnlfb25fdGFwIj48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAg"
    "JHtHKCJzcGxpdFBheSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYgY2xhc3M9InN3YXAiIHN0eWxl"
    "PSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5TcGxpdCBwYXltZW50PHNtYWxsPldhbGxl"
    "dCBiYWxhbmNlIHBsdXMgVVBJIGluIG9uZSBjaGVja291dDwvc21hbGw+PC9kaXY+CiAgICAgPGRp"
    "diBjbGFzcz0idGcke0ZFQVQud3JvbmdBbXQ/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9Indyb25nQW10"
    "Ij48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJhdXRvUmVmdW5kIixgPGRpdiBjbGFzcz0i"
    "Y2FyZCI+PGgzPkF1dG8tcmVmdW5kPC9oMz4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9"
    "Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+V2FycmFudHkgd2luZG93IChtaW51dGVzKTwvbGFiZWw+"
    "PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxMCI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ia3Yi"
    "PjxzcGFuPlJlZnVuZGVkIHRvZGF5PC9zcGFuPjxiPuKCuTEwNS4wMCDCtyAzIG9yZGVyczwvYj48"
    "L2Rpdj48L2Rpdj5gKX0KICAgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkdtYWlsIGFjY291bnQ8L2gz"
    "PjxwIGNsYXNzPSJzdWIiPlRoZSBHbWFpbCB0aGF0IHJlY2VpdmVzIHlvdXIgRmFtUGF5IHBheW1l"
    "bnQgbWFpbHMuIE5vdGhpbmcgaXMgc3RvcmVkIGluIGNvZGUuPC9wPgogICAgIDxkaXYgY2xhc3M9"
    "ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48bGFiZWw+R21haWwgSUQ8L2xhYmVsPjxp"
    "bnB1dCBjbGFzcz0iaW5wIiBwbGFjZWhvbGRlcj0ieW91cnN0b3JlQGdtYWlsLmNvbSI+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5HbWFpbCBwYXNzd29yZDwvbGFiZWw+PGlu"
    "cHV0IGNsYXNzPSJpbnAiIHR5cGU9InBhc3N3b3JkIiBwbGFjZWhvbGRlcj0i4oCi4oCi4oCi4oCi"
    "4oCi4oCi4oCi4oCi4oCi4oCi4oCi4oCiIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJidG5yb3ci"
    "PjxidXR0b24gY2xhc3M9ImJ0biIgZGF0YS1hZG1pbi1hY3Q9ImdtYWlsdGVzdCI+VGVzdCBHbWFp"
    "bDwvYnV0dG9uPgogICAgIDwvZGl2PgogICAgIDxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjEwcHgiPlRpcDogaWYgMi1zdGVwIHZlcmlmaWNhdGlvbiBpcyBvbiwgcGFzdGUgYSBHb29n"
    "bGUgQXBwIFBhc3N3b3JkIGhlcmUgaW5zdGVhZCBvZiB5b3VyIG5vcm1hbCBvbmUuPC9wPjwvZGl2"
    "PgogICA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5SZWNlbnQgYXV0by12ZXJpZmljYXRpb25zPC9oMj48"
    "L2Rpdj4KICAgJHsoQURNLmRlcG9zaXRzfHxbXSkubGVuZ3RoP0FETS5kZXBvc2l0cy5tYXAoZD0+"
    "YDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6"
    "Y2VudGVyO2dhcDoxMHB4Ij4KICAgICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoIndhbGxldCIp"
    "fTwvZGl2PgogICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBzdHlsZT0i"
    "Zm9udC1zaXplOi44MnJlbSI+JHttb25leShkLmFtdCl9IMK3ICR7ZXNjKGQudXNlcil9PC9iPjxi"
    "cj4KICAgICAgICAgPHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjly"
    "ZW0iPiR7ZXNjKGQudXRyfHwibm8gcmVmZXJlbmNlIHlldCIpfTwvc21hbGw+PC9kaXY+CiAgICAg"
    "ICA8c3BhbiBjbGFzcz0icGlsbCAke2Quc3Q9PT0iQ29tcGxldGVkIj8ib24iOiJob3QifSI+JHtl"
    "c2MoZC5zdCl9PC9zcGFuPgogICAgICAgJHtkLnN0PT09IkNvbXBsZXRlZCI/IiI6YDxidXR0b24g"
    "Y2xhc3M9ImJ0biB4cyIgZGF0YS1kZXAtb2s9IiR7ZC5pZH0iPkFwcHJvdmU8L2J1dHRvbj5gfTwv"
    "ZGl2PmApLmpvaW4oIiIpCiAgICAgOic8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0Ij48cCBjbGFzcz0i"
    "c3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIGRlcG9zaXRzIHlldC48L3A+PC9kaXY+J31gOwp9Cgpm"
    "dW5jdGlvbiBhQ29udGVudCgpewogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkVkaXQg"
    "YXBwIHRleHQ8L2gzPgogICAgIDxwIGNsYXNzPSJzdWIiPkV2ZXJ5IHdvcmQgdXNlcnMgc2VlIOKA"
    "lCBjaGFuZ2UgaXQgaGVyZSBhbmQgdGhlIHN0b3JlIHVwZGF0ZXMgaW5zdGFudGx5LjwvcD4KICAg"
    "ICA8ZGl2IGNsYXNzPSJtc2VhcmNoIiBzdHlsZT0ibWFyZ2luLXRvcDoxMnB4Ij48ZGl2IGNsYXNz"
    "PSJmaWVsZHdyYXAiPiR7aWMoInNlYXJjaCIpfTxpbnB1dCBpZD0iY3EiIHBsYWNlaG9sZGVyPSJG"
    "aW5kIHRleHQgdG8gZWRpdOKApiI+PC9kaXY+CiAgICAgICA8YnV0dG9uIGNsYXNzPSJidG4gc20g"
    "c29mdCIgaWQ9ImNtc3Jlc2V0Ij5SZXNldDwvYnV0dG9uPjwvZGl2PgogICAgIDxwIGNsYXNzPSJz"
    "dWIiIHN0eWxlPSJtYXJnaW4tdG9wOjEwcHgiPlVzZSAmbHQ7YnImZ3Q7IGZvciBhIGxpbmUgYnJl"
    "YWssICZsdDtiJmd0O3dvcmQmbHQ7L2ImZ3Q7IGZvciBib2xkLiB7bn0gPSBsaXZlIGNvdW50cnkg"
    "Y291bnQsIHttfSA9IHBheW1lbnQgbWludXRlcy48L3A+PC9kaXY+CiAgICR7Q01TTUVUQS5tYXAo"
    "KFtncnAscm93c10pPT5gPGRpdiBjbGFzcz0iY2FyZCBjbXNncnAiPjxoMz4ke2dycH08L2gzPgog"
    "ICAgICR7cm93cy5tYXAoKFtrLGxibF0pPT5gPGRpdiBjbGFzcz0iZmllbGQgY21zcm93IiBkYXRh"
    "LWY9IiR7ZXNjKChsYmwrIiAiK0NNU1trXSsiICIrZ3JwKS50b0xvd2VyQ2FzZSgpKX0iPgogICAg"
    "ICAgIDxsYWJlbD4ke2xibH08L2xhYmVsPgogICAgICAgIDx0ZXh0YXJlYSBjbGFzcz0iaW5wIiBy"
    "b3dzPSIke0NNU1trXS5sZW5ndGg+NDA/MjoxfSIgZGF0YS1jbXM9IiR7a30iPiR7ZXNjKENNU1tr"
    "XSl9PC90ZXh0YXJlYT48L2Rpdj5gKS5qb2luKCIiKX0KICAgPC9kaXY+YCkuam9pbigiIil9CiAg"
    "IDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6"
    "Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+PGkgY2xhc3M9ImRv"
    "dCI+PC9pPkxpdmUgZWRpdDwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRo"
    "OjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj5DaGFuZ2VzIGFwcGx5IHRvIHRo"
    "ZSBTdG9yZSB0YWIgc3RyYWlnaHQgYXdheTwvZGl2PgogICAgIDwvZGl2PmA7Cn0KCmZ1bmN0aW9u"
    "IGFGZWF0dXJlcygpewogIGNvbnN0IG9uPU9iamVjdC52YWx1ZXMoRkVBVCkuZmlsdGVyKEJvb2xl"
    "YW4pLmxlbmd0aCx0b3Q9T2JqZWN0LmtleXMoRkVBVCkubGVuZ3RoOwogIHJldHVybiBgPGRpdiBj"
    "bGFzcz0iY2FyZCI+PGgzPkZlYXR1cmUgc3dpdGNoZXM8L2gzPgogICAgIDxwIGNsYXNzPSJzdWIi"
    "PlR1cm4gYW55dGhpbmcgb2ZmIGFuZCBpdCBkaXNhcHBlYXJzIGZyb20gdGhlIGFwcCBjb21wbGV0"
    "ZWx5IOKAlCBubyBidXR0b25zLCBubyBtZW51cywgbm8gbGVmdG92ZXJzLjwvcD4KICAgICA8ZGl2"
    "IGNsYXNzPSJrdiI+PHNwYW4+RW5hYmxlZDwvc3Bhbj48YiBpZD0iZmNvdW50Ij4ke29ufSBvZiAk"
    "e3RvdH08L2I+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0ibXNlYXJjaCIgc3R5bGU9Im1hcmdpbi10"
    "b3A6MTJweCI+PGRpdiBjbGFzcz0iZmllbGR3cmFwIj4ke2ljKCJzZWFyY2giKX08aW5wdXQgaWQ9"
    "ImZxIiBwbGFjZWhvbGRlcj0iU2VhcmNoIGZlYXR1cmVz4oCmIj48L2Rpdj4KICAgICAgIDxidXR0"
    "b24gY2xhc3M9ImJ0biBzbSBzb2Z0IiBpZD0iZmFsbCI+QWxsIG9uPC9idXR0b24+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0iYnRucm93IiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxidXR0b24gY2xh"
    "c3M9ImJ0biBzbSBnaG9zdCIgaWQ9ImZub25lIj5BbGwgb2ZmPC9idXR0b24+CiAgICAgICA8YnV0"
    "dG9uIGNsYXNzPSJidG4gc20gc29mdCIgaWQ9ImZyZXNldCI+UmVzZXQ8L2J1dHRvbj48L2Rpdj48"
    "L2Rpdj4KICAgJHtGR1JPVVBTLm1hcCgoW2dycCxyb3dzXSk9PmA8ZGl2IGNsYXNzPSJjYXJkIGZn"
    "cnAiPjxoMz4ke2dycH08L2gzPgogICAgICR7cm93cy5tYXAoKFtrLGwsZF0pPT5gPGRpdiBjbGFz"
    "cz0iZmNhcmQgZnJvdyIgZGF0YS1mPSIke2VzYygobCsiICIrZCsiICIrZ3JwKS50b0xvd2VyQ2Fz"
    "ZSgpKX0iPgogICAgICAgIDxkaXYgY2xhc3M9InQiPjxiPiR7bH08L2I+PHNtYWxsPiR7ZH08L3Nt"
    "YWxsPjwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9InRnICR7RkVBVFtrXT8ib24iOiIifSIgZGF0"
    "YS1mZWF0PSIke2t9Ij48L2Rpdj48L2Rpdj5gKS5qb2luKCIiKX0KICAgPC9kaXY+YCkuam9pbigi"
    "Iil9CiAgIDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24t"
    "aXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+PGkgY2xh"
    "c3M9ImRvdCI+PC9pPkxpdmU8L3NwYW4+CiAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0"
    "aDowO2ZvbnQtc2l6ZTouNzRyZW07Y29sb3I6dmFyKC0tdHgyKSI+U3dpdGNoZXMgYXBwbHkgdG8g"
    "dGhlIFN0b3JlIHRhYiBpbnN0YW50bHk8L2Rpdj4KICAgICA8L2Rpdj5gOwp9CgpmdW5jdGlvbiBh"
    "TWFya2V0KCl7CiAgcmV0dXJuIGAke0coImNvdXBvbnMiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+"
    "Q291cG9uczwvaDM+PHAgY2xhc3M9InN1YiI+UGVyY2VudGFnZSBvZmYgYXQgY2hlY2tvdXQuPC9w"
    "PgogICAgICR7KEFETS5jb3Vwb25zJiZBRE0uY291cG9ucy5sZW5ndGgpP0FETS5jb3Vwb25zLm1h"
    "cChjPT5gPGRpdiBjbGFzcz0ia3YiPgogICAgICAgPHNwYW4+PGIgc3R5bGU9ImNvbG9yOnZhcigt"
    "LXR4KSI+JHtlc2MoYy5jb2RlKX08L2I+IMK3ICR7Yy5wZXJjZW50fSUgwrcgJHtjLnVzZWR9LyR7"
    "Yy5tYXh8fCLiiJ4ifTwvc3Bhbj4KICAgICAgIDxiPjxzcGFuIGNsYXNzPSJwaWxsICR7Yy5hY3Rp"
    "dmU/Im9uIjoib2ZmIn0iPiR7Yy5hY3RpdmU/IkFjdGl2ZSI6Ik9mZiJ9PC9zcGFuPjwvYj48L2Rp"
    "dj5gKS5qb2luKCIiKQogICAgICAgOic8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5v"
    "IGNvdXBvbnMgeWV0LjwvcD4nfQogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2lu"
    "LXRvcDoxMnB4Ij48bGFiZWw+TmV3IGNvZGU8L2xhYmVsPgogICAgICAgPGRpdiBzdHlsZT0iZGlz"
    "cGxheTpmbGV4O2dhcDo3cHgiPjxpbnB1dCBjbGFzcz0iaW5wIiBpZD0iY3Bjb2RlIiBwbGFjZWhv"
    "bGRlcj0iTkVXWUVBUiI+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgaWQ9ImNwcGN0IiBwbGFj"
    "ZWhvbGRlcj0iJSIgc3R5bGU9IndpZHRoOjY2cHgiPgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9ImNwYWRkIj5BZGQ8L2J1dHRvbj48L2Rpdj48"
    "L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJmbGFzaFNhbGUiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+"
    "Rmxhc2ggc2FsZSBzY2hlZHVsZXI8L2gzPjxwIGNsYXNzPSJzdWIiPlN0YXJ0cywgYW5ub3VuY2Vz"
    "IGFuZCBlbmRzIGJ5IGl0c2VsZi48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJt"
    "YXJnaW4tdG9wOjEycHgiPjxsYWJlbD5EaXNjb3VudCAoJSk8L2xhYmVsPjxpbnB1dCBjbGFzcz0i"
    "aW5wIiB2YWx1ZT0iMjAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+U3Rh"
    "cnRzPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IlRvZGF5IDE4OjAwIj48L2Rpdj4K"
    "ICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkVuZHM8L2xhYmVsPjxpbnB1dCBjbGFzcz0i"
    "aW5wIiB2YWx1ZT0iVG9kYXkgMjM6MDAiPjwvZGl2PgogICAgIDxidXR0b24gY2xhc3M9ImJ0biBi"
    "bG9jayIgaWQ9ImZsYXNoZ28iPlN0YXJ0IEZsYXNoIFNhbGU8L2J1dHRvbj48L2Rpdj5gKX0KICAg"
    "JHtHKCJzY2hlZEJyb2FkY2FzdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5TY2hlZHVsZWQgYnJv"
    "YWRjYXN0PC9oMz48cCBjbGFzcz0ic3ViIj5RdWV1ZSBpdCBhbmQgdGFyZ2V0IGEgc2VnbWVudC48"
    "L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPjxsYWJl"
    "bD5TZWdtZW50PC9sYWJlbD4KICAgICAgIDxzZWxlY3QgY2xhc3M9ImlucCI+PG9wdGlvbj5FdmVy"
    "eW9uZTwvb3B0aW9uPjxvcHRpb24+QnV5ZXJzIG9ubHk8L29wdGlvbj48b3B0aW9uPk5ldmVyIGJv"
    "dWdodDwvb3B0aW9uPjxvcHRpb24+QmlnIHNwZW5kZXJzICjigrkxMDAwKyk8L29wdGlvbj48b3B0"
    "aW9uPkluYWN0aXZlIDMwIGRheXM8L29wdGlvbj48L3NlbGVjdD48L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJmaWVsZCI+PGxhYmVsPlNlbmQgYXQ8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1"
    "ZT0iVG9tb3Jyb3cgMTA6MDAiPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+"
    "TWVzc2FnZTwvbGFiZWw+PHRleHRhcmVhIGNsYXNzPSJpbnAiIHJvd3M9IjIiIHBsYWNlaG9sZGVy"
    "PSJXcml0ZSB5b3VyIGJyb2FkY2FzdOKApiI+PC90ZXh0YXJlYT48L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJidG5yb3ciPjxidXR0b24gY2xhc3M9ImJ0biIgaWQ9ImJjZ28iPlNlbmQgQnJvYWRjYXN0"
    "PC9idXR0b24+CiAgICAgICA8L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJ3aW5iYWNrIixgPGRpdiBj"
    "bGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9InBhZGRpbmctdG9wOjAiPjxkaXYg"
    "Y2xhc3M9InQiPldpbi1iYWNrIG1lc3NhZ2VzPHNtYWxsPkF1dG8tRE0gdXNlcnMgaW5hY3RpdmUg"
    "Zm9yIDcgYW5kIDMwIGRheXM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFU"
    "LnNwbGl0UGF5PyIgb24iOiIifSIgZGF0YS1mZWF0PSJzcGxpdFBheSI+PC9kaXY+PC9kaXY+CiAg"
    "ICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5EaXNjb3VudCBvZmZlcmVkICglKTwvbGFiZWw+"
    "PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxMCI+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiYWJh"
    "bmRvblJlbWluZCIsYDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7"
    "YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+"
    "PGkgY2xhc3M9ImRvdCI+PC9pPlJlbWluZGVyczwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4"
    "OjE7bWluLXdpZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj40IHVuZmlu"
    "aXNoZWQgcGF5bWVudHMgbnVkZ2VkIHRvZGF5PC9kaXY+CiAgICAgPC9kaXY+YCl9CiAgICR7Rygi"
    "bG95YWx0eSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Mb3lhbHR5IHBvaW50czwvaDM+CiAgICAg"
    "PGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPlBvaW50cyBw"
    "ZXIg4oK5MTAgc3BlbnQ8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iMSI+PC9kaXY+"
    "CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5Qb2ludHMgbmVlZGVkIGZvciDigrkxMDwv"
    "bGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxMDAiPjwvZGl2PgogICAgIDxidXR0b24g"
    "Y2xhc3M9ImJ0biBzbSIgc3R5bGU9ImZsZXg6MCAwIGF1dG8iIGlkPSJ3aW5iYWNrc2F2ZSI+U2F2"
    "ZTwvYnV0dG9uPjwvZGl2PmApfQogICAke2FueUYoInJlZkRhc2giLCJyZWZMMiIpP2A8ZGl2IGNs"
    "YXNzPSJjYXJkIj48aDM+UmVmZXJyYWwgcGF5b3V0czwvaDM+CiAgICAgPGRpdiBjbGFzcz0iZmll"
    "bGQiIHN0eWxlPSJtYXJnaW4tdG9wOjhweCI+PGxhYmVsPkxldmVsIDEgY29tbWlzc2lvbiAoJSk8"
    "L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iNSI+PC9kaXY+CiAgICAgJHtHKCJyZWZM"
    "MiIsYDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+TGV2ZWwgMiBjb21taXNzaW9uICglKTwvbGFi"
    "ZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSIxIj48L2Rpdj5gKX0KICAgICA8ZGl2IGNsYXNz"
    "PSJrdiI+PHNwYW4+UGFpZCBvdXQgdGhpcyBtb250aDwvc3Bhbj48Yj7igrkzLDI0MDwvYj48L2Rp"
    "dj48L2Rpdj5gOiIifQogICAke0coInNhbGVzRmVlZCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxkaXYg"
    "Y2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNzPSJ0Ij5BdXRvLXBv"
    "c3Qgc2FsZXMgdG8gY2hhbm5lbDxzbWFsbD4ke0ZFRUQubGVuZ3RoP2VzYyhGRUVEWzBdWzFdKyIg"
    "c29sZCDCtyAiK0ZFRURbMF1bM10pOiJubyBzYWxlcyB5ZXQifTwvc21hbGw+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0idGcke0ZFQVQud2luYmFjaz8iIG9uIjoiIn0iIGRhdGEtZmVhdD0id2luYmFj"
    "ayI+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygibGVhZGVyYm9hcmQiLGA8ZGl2IGNsYXNz"
    "PSJjYXJkIj48aDM+TGVhZGVyYm9hcmQgcHJpemU8L2gzPgogICAgIDxkaXYgY2xhc3M9ImZpZWxk"
    "IiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5XZWVrbHkgcHJpemUgKOKCuSk8L2xhYmVs"
    "PjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iNTAwIj48L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNz"
    "PSJidG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0icHJpemVzYXZlIj5TYXZlPC9idXR0"
    "b24+PC9kaXY+YCl9YDsKfQoKZnVuY3Rpb24gYUFuYWx5dGljcygpewogIGNvbnN0IHdlZWs9KEFE"
    "TS5zZXJpZXN8fFtdKS5yZWR1Y2UoKGEsYik9PmErYiwwKTsKICBjb25zdCBhdmc9QURNLmtwaS5v"
    "cmRlcnM/QURNLmtwaS5yZXYvQURNLmtwaS5vcmRlcnM6MDsKICByZXR1cm4gYCR7RygicmV2Q2hh"
    "cnQiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+UmV2ZW51ZTwvaDM+PHAgY2xhc3M9InN1YiI+TGFz"
    "dCA3IGRheXMgwrcgJHttb25leSh3ZWVrKX0gdG90YWw8L3A+CiAgICAgPGRpdiBjbGFzcz0iYmFy"
    "cyI+JHtBRE0uYmFycy5tYXAoaD0+YDxpIHN0eWxlPSJoZWlnaHQ6JHtofSUiPjwvaT5gKS5qb2lu"
    "KCIiKX08L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrcGlzIiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4"
    "Ij4ke1tbIlRvZGF5Iixtb25leShBRE0ua3BpLnJldildLFsiVGhpcyBtb250aCIsbW9uZXkoQURN"
    "Lm1hcmtldC5tb250aCldLFsiQXZnIG9yZGVyIixtb25leShhdmcpXV0KICAgICAgIC5tYXAoKFtr"
    "LHZdKT0+YDxkaXYgY2xhc3M9ImtwaSI+PHNtYWxsPiR7a308L3NtYWxsPjxiPiR7dn08L2I+PC9k"
    "aXY+YCkuam9pbigiIil9PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiZnVubmVsIixgPGRpdiBjbGFz"
    "cz0iY2FyZCI+PGgzPkNvbnZlcnNpb24gZnVubmVsPC9oMz48cCBjbGFzcz0ic3ViIj5XaGVyZSBw"
    "ZW9wbGUgZHJvcCBvZmY8L3A+CiAgICAgPGRpdiBjbGFzcz0iZnVubmVsIj4keygoKT0+e2NvbnN0"
    "IGY9QURNLmFuYWx5dGljcy5mdW5uZWwsdD1NYXRoLm1heCgxLGYudXNlcnMpOwogICAgICAgcmV0"
    "dXJuIFtbIlN0YXJ0ZWQgYm90IixmLnVzZXJzLDEwMF0sWyJEZXBvc2l0ZWQiLGYuZGVwLE1hdGgu"
    "cm91bmQoZi5kZXAvdCoxMDApXSxbIkJvdWdodCIsZi5idXksTWF0aC5yb3VuZChmLmJ1eS90KjEw"
    "MCldXQogICAgICAgLm1hcCgoW2ssdix3XSk9PmA8ZGl2PjxpIHN0eWxlPSJ3aWR0aDoke01hdGgu"
    "bWF4KDQsdyl9JSI+PC9pPjxzcGFuIHN0eWxlPSJ3aGl0ZS1zcGFjZTpub3dyYXAiPiR7a30gwrcg"
    "PGI+JHt2fTwvYj48L3NwYW4+PC9kaXY+YCkuam9pbigiIik7fSkoKX08L2Rpdj48L2Rpdj5gKX0K"
    "ICAgJHtHKCJwcm9maXRSZXAiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+UGVyLWNvdW50cnkgcHJv"
    "Zml0PC9oMz4KICAgICAke0FETS5hbmFseXRpY3MuY291bnRyeS5sZW5ndGg/QURNLmFuYWx5dGlj"
    "cy5jb3VudHJ5Lm1hcCgoW2MsbixzdW1dKT0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj4ke2VzYyhj"
    "KX0gwrcgJHtufSBzb2xkPC9zcGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS1vaykiPiR7bW9uZXko"
    "c3VtKX08L2I+PC9kaXY+YCkuam9pbigiIikKICAgICAgIDonPHAgY2xhc3M9InN1YiIgc3R5bGU9"
    "Im1hcmdpbjowIj5ObyBzYWxlcyB5ZXQuPC9wPid9CiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJs"
    "b2NrIHNvZnQiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEtYWRtaW4tYWN0PSJleHBvcnQi"
    "PkV4cG9ydCBDU1Y8L2J1dHRvbj48L2Rpdj5gKX0KICAgJHtHKCJjbHYiLGA8ZGl2IGNsYXNzPSJj"
    "YXJkIj48aDM+Q3VzdG9tZXIgdmFsdWU8L2gzPgogICAgICR7QURNLnVzZXJzLmxlbmd0aD9BRE0u"
    "dXNlcnMuc2xpY2UoMCw1KS5tYXAodT0+YDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj48YiBzdHlsZT0i"
    "Y29sb3I6dmFyKC0tdHgpIj4ke2VzYyh1Lm5hbWUpfTwvYj4gwrcgJHt1Lm9yZGVyc30gb3JkZXJz"
    "PC9zcGFuPgogICAgICAgICA8Yj4ke21vbmV5KHUuc3BlbnQpfSA8c3BhbiBjbGFzcz0icGlsbCAk"
    "e3Uuc3BlbnQ+MD8ib24iOiJob3QifSI+JHt1LnNwZW50PjA/ImFjdGl2ZSI6Im5vIHNwZW5kIn08"
    "L3NwYW4+PC9iPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgICA6JzxwIGNsYXNzPSJzdWIiIHN0eWxl"
    "PSJtYXJnaW46MCI+Tm8gY3VzdG9tZXJzIHlldC48L3A+J308L2Rpdj5gKX0KICAgJHtHKCJkaWdl"
    "c3QiLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10"
    "b3A6MCI+PGRpdiBjbGFzcz0idCI+RGFpbHkgZGlnZXN0PHNtYWxsPkVuZC1vZi1kYXkgc3VtbWFy"
    "eSB0byB0aGUgYWRtaW4gY2hhdDwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGcke0ZF"
    "QVQuc2FsZXNGZWVkPyIgb24iOiIifSIgZGF0YS1mZWF0PSJzYWxlc0ZlZWQiPjwvZGl2PjwvZGl2"
    "PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+U2VuZCBhdDwvbGFiZWw+PGlucHV0IGNs"
    "YXNzPSJpbnAiIHZhbHVlPSIyMzozMCI+PC9kaXY+PC9kaXY+YCl9YDsKfQoKZnVuY3Rpb24gYVNl"
    "Y3VyaXR5KCl7CiAgcmV0dXJuIGAke0coInJhdGVMaW1pdCIsYDxkaXYgY2xhc3M9ImNhcmQiPjxo"
    "Mz5SYXRlIGxpbWl0aW5nPC9oMz48cCBjbGFzcz0ic3ViIj5TdG9wcyBvbmUgdXNlciBoYW1tZXJp"
    "bmcgdGhlIGJvdC48L3A+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4tdG9w"
    "OjEwcHgiPjxsYWJlbD5NYXggYWN0aW9ucyBwZXIgbWludXRlPC9sYWJlbD48aW5wdXQgY2xhc3M9"
    "ImlucCIgdmFsdWU9IjIwIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkNv"
    "b2xkb3duIGFmdGVyIGxpbWl0IChzKTwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHZhbHVlPSI2"
    "MCI+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygibXVsdGlBY2MiLGA8ZGl2IGNsYXNzPSJjYXJkIj48"
    "ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+TXVs"
    "dGktYWNjb3VudCBkZXRlY3Rpb248c21hbGw+RmxhZ3MgcmVmZXJyYWwgZmFybWluZyBwYXR0ZXJu"
    "czwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGcke0ZFQVQubXVsdGlBY2M/IiBvbiI6"
    "IiJ9IiBkYXRhLWZlYXQ9Im11bHRpQWNjIj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJr"
    "diI+PHNwYW4+RmxhZ2dlZCB0aGlzIHdlZWs8L3NwYW4+PGI+NyBhY2NvdW50czwvYj48L2Rpdj4K"
    "ICAgICA8YnV0dG9uIGNsYXNzPSJidG4gYmxvY2sgc29mdCIgc3R5bGU9Im1hcmdpbi10b3A6MTBw"
    "eCIgZGF0YS1hLWdvPSJvcmRlcnMiPlJldmlldyBGbGFnZ2VkPC9idXR0b24+PC9kaXY+YCl9CiAg"
    "ICR7RygidmVsb2NpdHkiLGA8ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHls"
    "ZT0icGFkZGluZy10b3A6MCI+PGRpdiBjbGFzcz0idCI+UHVyY2hhc2UgdmVsb2NpdHkgYWxlcnRz"
    "PHNtYWxsPkNhdGNoZXMgc3RvbGVuIHRvcC11cHM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xh"
    "c3M9InRnJHtGRUFULnZlbG9jaXR5PyIgb24iOiIifSIgZGF0YS1mZWF0PSJ2ZWxvY2l0eSI+PC9k"
    "aXY+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5BbGVydCBhYm92ZSBOIGJ1"
    "eXMgLyAxMCBtaW48L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2YWx1ZT0iNSI+PC9kaXY+PC9k"
    "aXY+YCl9CiAgICR7RygiYWRtaW4yZmEiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QWRtaW4gMkZB"
    "PC9oMz48cCBjbGFzcz0ic3ViIj5SZXF1aXJlZCBiZWZvcmUgYmFsYW5jZSBlZGl0cywgYnJvYWRj"
    "YXN0cyBhbmQgd2lwZXMuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2lu"
    "LXRvcDoxMHB4Ij48bGFiZWw+TWV0aG9kPC9sYWJlbD48c2VsZWN0IGNsYXNzPSJpbnAiPjxvcHRp"
    "b24+UElOPC9vcHRpb24+PG9wdGlvbj5UT1RQIGFwcDwvb3B0aW9uPjwvc2VsZWN0PjwvZGl2Pgog"
    "ICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyIgZGF0YS1mZWF0PSJhZG1pbjJmYSI+U2V0IFVwPC9i"
    "dXR0b24+PC9kaXY+YCl9CiAgICR7RygiYXV0b0JhbiIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5B"
    "dXRvLWJhbiBydWxlczwvaDM+CiAgICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+"
    "Q2hhcmdlYmFjazxzbWFsbD5CYW4gaW5zdGFudGx5PC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNs"
    "YXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmJhbl9jaGFyZ2ViYWNrKT8iIG9uIjoiIn0iIGRhdGEt"
    "c2V0dGluZz0iYmFuX2NoYXJnZWJhY2siPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InN3"
    "YXAiPjxkaXYgY2xhc3M9InQiPkZha2UgVVRSIHN1Ym1pdHRlZDxzbWFsbD5CYW4gYWZ0ZXIgMiBh"
    "dHRlbXB0czwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGckeyhBRE0uc3cmJkFETS5z"
    "dy5iYW5fZmFrZV91dHIpPyIgb24iOiIifSIgZGF0YS1zZXR0aW5nPSJiYW5fZmFrZV91dHIiPjwv"
    "ZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InN3YXAiPjxkaXYgY2xhc3M9InQiPlJlcGVhdGVk"
    "IGZhaWxlZCB2ZXJpZmljYXRpb248c21hbGw+QmFuIGFmdGVyIDU8L3NtYWxsPjwvZGl2PgogICAg"
    "IDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cuYmFuX2ZhaWxlZF92ZXJpZnkpPyIgb24i"
    "OiIifSIgZGF0YS1zZXR0aW5nPSJiYW5fZmFpbGVkX3ZlcmlmeSI+PC9kaXY+PC9kaXY+PC9kaXY+"
    "YCl9CiAgICR7RygiYmxhY2tsaXN0IixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkJsYWNrbGlzdDwv"
    "aDM+PHAgY2xhc3M9InN1YiI+VXNlciBJRHMsIHBob25lIG51bWJlcnMgYW5kIFVQSSByZWZlcmVu"
    "Y2VzLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6MTBweCI+"
    "PGRpdiBzdHlsZT0iZGlzcGxheTpmbGV4O2dhcDo3cHgiPgogICAgICAgPGlucHV0IGNsYXNzPSJp"
    "bnAiIGlkPSJibC1pbiIgcGxhY2Vob2xkZXI9IklELCBwaG9uZSBvciBVUEkgcmVmIj48YnV0dG9u"
    "IGNsYXNzPSJidG4gc20iIHN0eWxlPSJmbGV4OjAgMCBhdXRvIiBpZD0iYmxibG9jayI+QmxvY2s8"
    "L2J1dHRvbj48L2Rpdj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+QmxvY2tlZCBl"
    "bnRyaWVzPC9zcGFuPjxiPjQyPC9iPjwvZGl2PjwvZGl2PmApfQogICAke0coImVuY3J5cHQiLGA8"
    "ZGl2IGNsYXNzPSJjYXJkIj48ZGl2IGNsYXNzPSJzd2FwIiBzdHlsZT0icGFkZGluZy10b3A6MCI+"
    "PGRpdiBjbGFzcz0idCI+RW5jcnlwdCBzZXNzaW9ucyBhdCByZXN0PHNtYWxsPkEgbGVha2VkIGRh"
    "dGFiYXNlIGZpbGUgc3RheXMgdXNlbGVzczwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0i"
    "dGcke0ZFQVQuZW5jcnlwdD8iIG9uIjoiIn0iIGRhdGEtZmVhdD0iZW5jcnlwdCI+PC9kaXY+PC9k"
    "aXY+CiAgICAgPGRpdiBjbGFzcz0ia3YiPjxzcGFuPktleSBzdGF0dXM8L3NwYW4+PGIgc3R5bGU9"
    "ImNvbG9yOnZhcigtLW9rKSI+TG9hZGVkIGZyb20gZW52PC9iPjwvZGl2PjwvZGl2PmApfWA7Cn0K"
    "CmZ1bmN0aW9uIGFTdG9jaygpewogIHJldHVybiBgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPlVwbG9h"
    "ZCBzdG9jazwvaDM+PHAgY2xhc3M9InN1YiI+UGFzdGUgc2Vzc2lvbnMgb3IgdXBsb2FkIGEgZmls"
    "ZSDigJQgb25lIHBlciBsaW5lLjwvcD4KPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJtYXJnaW4t"
    "dG9wOjEycHgiPjxsYWJlbD5QYXN0ZSBhY2NvdW50cyDigJQgb25lIHBlciBsaW5lPC9sYWJlbD4K"
    "ICAgICAgPHRleHRhcmVhIGNsYXNzPSJpbnAiIGlkPSJzdHBhc3RlIiByb3dzPSI0IgogICAgICAg"
    "IHBsYWNlaG9sZGVyPSJJbmRpYSB8ICs5MSB8IDM1IHwgc2Vzc2lvbiYjMTA7S2VueWEgfCArMjU0"
    "IHwgNzcgfCBzZXNzaW9uIj48L3RleHRhcmVhPjwvZGl2PgogICAgPGRpdiBjbGFzcz0iYnRucm93"
    "Ij48YnV0dG9uIGNsYXNzPSJidG4gY3RhIiBpZD0ic3RzYXZlIj5JbXBvcnQ8L2J1dHRvbj4KICAg"
    "ICAgPGxhYmVsIGNsYXNzPSJidG4gc29mdCIgc3R5bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVt"
    "czpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjtjdXJzb3I6cG9pbnRlciI+CiAgICAgICAg"
    "VXBsb2FkIC50eHQ8aW5wdXQgdHlwZT0iZmlsZSIgaWQ9InN0ZmlsZSIgYWNjZXB0PSIudHh0LC5j"
    "c3YiIGhpZGRlbj48L2xhYmVsPjwvZGl2PgogICAgPHAgY2xhc3M9InN1YiIgaWQ9InN0bXNnIiBz"
    "dHlsZT0ibWFyZ2luOjhweCAwIDA7Zm9udC1zaXplOi43MnJlbSI+PC9wPgogICAgJHtHKCJidWxr"
    "VXBsb2FkIixgPGRpdiBjbGFzcz0ic3RlcHMiIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiPgogICAg"
    "ICA8ZGl2IGNsYXNzPSJzdGVwIG9uIj48aT4xPC9pPiR7QURNLmFuYWx5dGljcy5zdG9ja190b3Rh"
    "bH0gYXZhaWxhYmxlIG5vdzwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGVwIG9uIj48aT4yPC9p"
    "PiR7QURNLmFuYWx5dGljcy5zb2xkX3RvdGFsfSBzb2xkIHNvIGZhcjwvZGl2PgogICAgICA8ZGl2"
    "IGNsYXNzPSJzdGVwIj48aT4zPC9pPkFkZCBtb3JlIGZyb20gdGhlIGJvdDogQWRtaW4g4oaSIFN0"
    "b2NrPC9kaXY+PC9kaXY+YCl9CiAgICAke0coImR1cERldGVjdCIsYDxwIGNsYXNzPSJzdWIiIHN0"
    "eWxlPSJtYXJnaW46MTBweCAwIDA7Zm9udC1zaXplOi43cmVtIj5EdXBsaWNhdGUgcGhvbmVzIGFu"
    "ZCBzZXNzaW9ucyBhcmUgcmVqZWN0ZWQgYXV0b21hdGljYWxseS48L3A+YCl9CiAgICAke0coImhl"
    "YWx0aENoZWNrIixgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+VmFsaWRhdGUgYmVm"
    "b3JlIHNlbGxpbmc8c21hbGw+Q2hlY2sgdGhlIHNlc3Npb24gaXMgYWxpdmUgYXQgcHVyY2hhc2Ug"
    "dGltZTwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0idGcke0ZFQVQuaGVhbHRoQ2hlY2s/"
    "IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9ImhlYWx0aENoZWNrIj48L2Rpdj48L2Rpdj5gKX08L2Rpdj4K"
    "ICAgJHtHKCJsb3dTdG9jayIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5Mb3ctc3RvY2sgYWxlcnRz"
    "PC9oMz48cCBjbGFzcz0ic3ViIj5QZXItY291bnRyeSB0aHJlc2hvbGQg4oCUIHBpbmcgd2hlbiBp"
    "dCBkcm9wcyBiZWxvdy48L3A+CiAgICAgJHsoQURNLnN0b2NrLmxlbmd0aD9BRE0uc3RvY2s6U1RP"
    "Q0spLnNsaWNlKDAsNikubWFwKHM9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+JHtzLmZ9ICR7ZXNj"
    "KHMuYyl9IMK3ICR7cy5xdHl8fDB9IGxlZnQ8L3NwYW4+CiAgICAgICA8Yj48aW5wdXQgY2xhc3M9"
    "ImlucCIgc3R5bGU9ImhlaWdodDozMHB4O3dpZHRoOjY0cHg7dGV4dC1hbGlnbjpjZW50ZXI7Zm9u"
    "dC1zaXplOi43NHJlbSIgdmFsdWU9IiR7cy5sb3d8fDEwfSI+PC9iPjwvZGl2PmApLmpvaW4oIiIp"
    "CiAgICAgICB8fCc8cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIHN0b2NrIHlldC48"
    "L3A+J308L2Rpdj5gKX0KICAgJHtHKCJzZWxsSG91cnMiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+"
    "U2VsbGluZyBob3VyczwvaDM+PHAgY2xhc3M9InN1YiI+Q291bnRyaWVzIGhpZGUgdGhlbXNlbHZl"
    "cyBvdXRzaWRlIHRoZSB3aW5kb3cuPC9wPgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0i"
    "bWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5PcGVuPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFs"
    "dWU9IjA5OjAwIj48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkNsb3NlPC9s"
    "YWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjAyOjAwIj48L2Rpdj48L2Rpdj5gKX0KICAg"
    "JHtHKCJkeW5QcmljZSIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5EeW5hbWljIHByaWNpbmc8L2gz"
    "PgogICAgIDxkaXYgY2xhc3M9InN3YXAiIHN0eWxlPSJwYWRkaW5nLXRvcDowIj48ZGl2IGNsYXNz"
    "PSJ0Ij5SYWlzZSBwcmljZSB3aGVuIHN0b2NrIGlzIGxvdzxzbWFsbD4rMTAlIHVuZGVyIDIwIHVu"
    "aXRzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7RkVBVC5keW5QcmljZT8iIG9u"
    "IjoiIn0iIGRhdGEtZmVhdD0iZHluUHJpY2UiPjwvZGl2PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9"
    "InN3YXAiPjxkaXYgY2xhc3M9InQiPk5pZ2h0IGRpc2NvdW50PHNtYWxsPuKIkjUlIGJldHdlZW4g"
    "MDA6MDAgYW5kIDA2OjAwPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0ZyR7KEFETS5z"
    "dyYmQURNLnN3Lm5pZ2h0X2Rpc2NvdW50KT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0ibmlnaHRf"
    "ZGlzY291bnQiPjwvZGl2PjwvZGl2PjwvZGl2PmApfQogICAke0coImJ1bGtEaXNjIixgPGRpdiBj"
    "bGFzcz0iY2FyZCI+PGgzPkJ1bGsgZGlzY291bnRzPC9oMz4KICAgICA8cCBjbGFzcz0ic3ViIj5Q"
    "ZXJjZW50IG9mZiB3aGVuIHRoZSBidXllciB0YWtlcyBzZXZlcmFsIGF0IG9uY2UuPC9wPgogICAg"
    "IDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4Ij48bGFiZWw+NSBvciBt"
    "b3JlICglKTwvbGFiZWw+CiAgICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1wcmljZT0iYnVs"
    "a181IiB2YWx1ZT0iJHtBRE0ucHJpY2luZz9BRE0ucHJpY2luZy5idWxrXzU6NX0iPjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48bGFiZWw+MTAgb3IgbW9yZSAoJSk8L2xhYmVsPgogICAg"
    "ICAgPGlucHV0IGNsYXNzPSJpbnAiIGRhdGEtcHJpY2U9ImJ1bGtfMTAiIHZhbHVlPSIke0FETS5w"
    "cmljaW5nP0FETS5wcmljaW5nLmJ1bGtfMTA6MTB9Ij48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJm"
    "aWVsZCI+PGxhYmVsPjI1IG9yIG1vcmUgKCUpPC9sYWJlbD4KICAgICAgIDxpbnB1dCBjbGFzcz0i"
    "aW5wIiBkYXRhLXByaWNlPSJidWxrXzI1IiB2YWx1ZT0iJHtBRE0ucHJpY2luZz9BRE0ucHJpY2lu"
    "Zy5idWxrXzI1OjE1fSI+PC9kaXY+CiAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5EZWZh"
    "dWx0IHByaWNlIGZvciBuZXcgc3RvY2s8L2xhYmVsPgogICAgICAgPGlucHV0IGNsYXNzPSJpbnAi"
    "IGRhdGEtcHJpY2U9ImRlZmF1bHRfcHJpY2UiIHZhbHVlPSIke0FETS5wcmljaW5nP0FETS5wcmlj"
    "aW5nLmRlZmF1bHRfcHJpY2U6MzV9Ij48L2Rpdj4KICAgICA8L2Rpdj5gKX0KICAgJHtHKCJ2YXJp"
    "YW50cyIsYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5WYXJpYW50czwvaDM+PHAgY2xhc3M9InN1YiI+"
    "UHJpY2VkIHNlcGFyYXRlbHkgb24gZXZlcnkgY291bnRyeS48L3A+CiAgICAgPGRpdiBjbGFzcz0i"
    "a3YiPjxzcGFuPjJGQSBlbmFibGVkPC9zcGFuPjxiPivigrk1PC9iPjwvZGl2PgogICAgIDxkaXYg"
    "Y2xhc3M9Imt2Ij48c3Bhbj5XaXRoIG1haWw8L3NwYW4+PGI+K+KCuTg8L2I+PC9kaXY+CiAgICAg"
    "PGRpdiBjbGFzcz0ia3YiPjxzcGFuPlNwYW0tZnJlZSBndWFyYW50ZWU8L3NwYW4+PGI+K+KCuTQ8"
    "L2I+PC9kaXY+PC9kaXY+YCl9CiAgIDxkaXYgY2xhc3M9InNlYyI+PGgyPkludmVudG9yeTwvaDI+"
    "PGEgY2xhc3M9InN1YiI+dGFwIGEgcHJpY2UgdG8gY2hhbmdlIGl0PC9hPjwvZGl2PgogICAke1NU"
    "T0NLLm1hcChzPT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5bGU9ImRpc3BsYXk6ZmxleDth"
    "bGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7cy5m"
    "fTwvZGl2PgogICAgIDxkaXYgc3R5bGU9ImZsZXg6MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZv"
    "bnQtc2l6ZTouODNyZW0iPiR7cy5jfTwvYj48YnI+CiAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9y"
    "OnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+JHtzLnF0eX0gYXZhaWxhYmxlPC9zbWFsbD48"
    "L2Rpdj4KICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1jcHJpY2U9IiR7ZXNjKHMuYyl9IiB2"
    "YWx1ZT0iJHtzLnByaWNlfSIgc3R5bGU9ImhlaWdodDozMnB4O3dpZHRoOjc4cHg7dGV4dC1hbGln"
    "bjpjZW50ZXI7Zm9udC1zaXplOi43NnJlbSI+CiAgICAgCiAgICAgPGJ1dHRvbiBjbGFzcz0iYnRu"
    "IHhzIGJhZCIgZGF0YS1taXNjPSJjbGVhcnN0b2NrIiBkYXRhLW1pc2MtdmFsdWU9IiR7ZXNjKHMu"
    "Yyl9Ij5DbGVhcjwvYnV0dG9uPjwvZGl2PmApLmpvaW4oIiIpfWA7Cn0KZnVuY3Rpb24gYU9yZGVy"
    "cygpewogIHJldHVybiBgPGRpdiBjbGFzcz0ic2VjIj48aDI+UmVjZW50IE9yZGVyczwvaDI+PGEg"
    "ZGF0YS1hZG1pbi1hY3Q9ImV4cG9ydCI+RXhwb3J0PC9hPjwvZGl2PgogICAke0FETS5vcmRlcnMu"
    "bGVuZ3RoP0FETS5vcmRlcnMubWFwKG89PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0i"
    "ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweCI+CiAgICAgPGRpdiBjbGFz"
    "cz0iZmxhZyI+JHtvLmZ9PC9kaXY+PGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48YiBz"
    "dHlsZT0iZm9udC1zaXplOi44MnJlbSI+JHtlc2Moby5pZCl9PC9iPjxicj4KICAgICA8c21hbGwg"
    "c3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OXJlbSI+JHtlc2Moby51c2VyKX0g"
    "wrcgJHttb25leShvLnByaWNlKX0gwrcgJHtlc2Moby53aGVuKX08L3NtYWxsPjwvZGl2PgogICAg"
    "IDxzcGFuIGNsYXNzPSJwaWxsICR7by5zdD09PSJEZWxpdmVyZWQiPyJvbiI6Im9mZiJ9Ij4ke2Vz"
    "YyhvLnN0KX08L3NwYW4+PC9kaXY+YCkuam9pbigiIikKICAgICA6JzxkaXYgY2xhc3M9ImNhcmQg"
    "dGlnaHQiPjxwIGNsYXNzPSJzdWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gb3JkZXJzIHlldC48L3A+"
    "PC9kaXY+J30KICAgJHtHKCJkaXNwdXRlIixgPGRpdiBjbGFzcz0ic2VjIj48aDI+RGlzcHV0ZXM8"
    "L2gyPjwvZGl2PgogICAgICR7QURNLmRpc3B1dGVzLmxlbmd0aD9BRE0uZGlzcHV0ZXMubWFwKGQ9"
    "PmA8ZGl2IGNsYXNzPSJjYXJkIHRpZ2h0IiBzdHlsZT0iZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1z"
    "OmNlbnRlcjtnYXA6MTBweCI+CiAgICAgICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMoInNoaWVs"
    "ZCIpfTwvZGl2PgogICAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0"
    "eWxlPSJmb250LXNpemU6LjhyZW0iPiR7ZXNjKGQuaWQpfSDCtyAke2VzYyhkLnVzZXIpfTwvYj48"
    "YnI+PHNtYWxsIHN0eWxlPSJjb2xvcjp2YXIoLS10eDIpO2ZvbnQtc2l6ZTouNjhyZW0iPiR7ZXNj"
    "KGQucmVhc29uKX08L3NtYWxsPjwvZGl2PgogICAgICAgICA8c3BhbiBjbGFzcz0icGlsbCAke2Qu"
    "c3RhdHVzPT09Ik9wZW4iPyJob3QiOiJvbiJ9Ij4ke2VzYyhkLnN0YXR1cyl9PC9zcGFuPjwvZGl2"
    "PmApLmpvaW4oIiIpCiAgICAgICA6JzxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiPjxwIGNsYXNzPSJz"
    "dWIiIHN0eWxlPSJtYXJnaW46MCI+Tm8gZGlzcHV0ZXMuPC9wPjwvZGl2Pid9YCl9CiAgICR7Rygi"
    "d2FycmFudHkiLGA8ZGl2IGNsYXNzPSJzZWMiPjxoMj5SZXBsYWNlbWVudCBjbGFpbXM8L2gyPjwv"
    "ZGl2PgogICAgIDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxp"
    "Z24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICAgIDxkaXYgY2xhc3M9ImZsYWciPiR7aWMo"
    "InNoaWVsZCIpfTwvZGl2PgogICAgICAgPGRpdiBzdHlsZT0iZmxleDoxO21pbi13aWR0aDowIj48"
    "YiBzdHlsZT0iZm9udC1zaXplOi44cmVtIj4yIGNsYWltcyBpbnNpZGUgd2FycmFudHk8L2I+PGJy"
    "PgogICAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OHJl"
    "bSI+QXV0by1yZXBsYWNlZCBmcm9tIHN0b2NrPC9zbWFsbD48L2Rpdj4KICAgICAgIDwvZGl2PmAp"
    "fQogICAKICAgJHtHKCJyZXZpZXdzIixgPGRpdiBjbGFzcz0ic2VjIj48aDI+UmV2aWV3czwvaDI+"
    "PC9kaXY+CiAgICAgJHsoQURNLnJldmlld3MubGVuZ3RoP0FETS5yZXZpZXdzLm1hcChyPT5bci51"
    "c2VyLHIuYyxyLnN0YXJzLHIudGV4dF0pOltdKQogICAgICAgLm1hcCgoW3UsYyxyLHRdKT0+YDxk"
    "aXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2Vu"
    "dGVyO2dhcDoxMHB4Ij4KICAgICAgICAgPGRpdiBjbGFzcz0iZmxhZyI+JHtpYygic3RhciIpfTwv"
    "ZGl2PgogICAgICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjAiPjxiIHN0eWxlPSJm"
    "b250LXNpemU6LjhyZW0iPiR7dX0gwrcgJHtjfSDCtyAke3J9PC9iPjxicj4KICAgICAgICAgICA8"
    "c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXplOi42OHJlbSI+JHt0fTwvc21h"
    "bGw+PC9kaXY+CiAgICAgICAgIDxidXR0b24gY2xhc3M9ImJ0biB4cyBzb2Z0IiBkYXRhLXJldj0i"
    "JHtyLmlkfSI+SGlkZTwvYnV0dG9uPjwvZGl2PmApLmpvaW4oIiIpfWApfWA7Cn0KZnVuY3Rpb24g"
    "YVVzZXJzKCl7CiAgcmV0dXJuIGA8ZGl2IGNsYXNzPSJmaWVsZCI+PGlucHV0IGNsYXNzPSJpbnAi"
    "IHBsYWNlaG9sZGVyPSJTZWFyY2ggYnkgSUQgb3IgdXNlcm5hbWUiPjwvZGl2PgogICAke0FETS51"
    "c2Vycy5sZW5ndGg/QURNLnVzZXJzLm1hcCh1PT5gPGRpdiBjbGFzcz0iY2FyZCB0aWdodCIgc3R5"
    "bGU9ImRpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEwcHgiPgogICAgICA8ZGl2"
    "IGNsYXNzPSJmbGFnIj4ke2ljKCJ1c2VyIil9PC9kaXY+CiAgICAgIDxkaXYgc3R5bGU9ImZsZXg6"
    "MTttaW4td2lkdGg6MCI+PGIgc3R5bGU9ImZvbnQtc2l6ZTouODJyZW0iPiR7ZXNjKHUubmFtZSl9"
    "PC9iPjxicj4KICAgICAgICA8c21hbGwgc3R5bGU9ImNvbG9yOnZhcigtLXR4Mik7Zm9udC1zaXpl"
    "Oi42OXJlbSI+JHttb25leSh1LnNwZW50KX0gc3BlbnQgwrcgJHt1Lm9yZGVyc30gb3JkZXJzIMK3"
    "ICR7bW9uZXkodS5iYWxhbmNlKX0gYmFsYW5jZSR7dS5iYW5uZWQ/IiDCtyBiYW5uZWQiOiIifTwv"
    "c21hbGw+PC9kaXY+CiAgICAgIDxzcGFuIGNsYXNzPSJwaWxsICR7dS5iYW5uZWQ/Im9mZiI6Im9u"
    "In0iPiR7ZXNjKHUudGllcil9PC9zcGFuPjwvZGl2PmApLmpvaW4oIiIpCiAgICAgOic8ZGl2IGNs"
    "YXNzPSJjYXJkIHRpZ2h0Ij48cCBjbGFzcz0ic3ViIiBzdHlsZT0ibWFyZ2luOjAiPk5vIHVzZXJz"
    "IHlldC48L3A+PC9kaXY+J30KICAgJHtHKCJhZG1pblJvbGVzIixgPGRpdiBjbGFzcz0ic2VjIj48"
    "aDI+QWRtaW4gcm9sZXM8L2gyPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImNhcmQiPgogICAgICAg"
    "JHtBRE0uYWRtaW5zLmxlbmd0aD9BRE0uYWRtaW5zLm1hcChhPT5gPGRpdiBjbGFzcz0ia3YiPjxz"
    "cGFuPjxiIHN0eWxlPSJjb2xvcjp2YXIoLS10eCkiPiR7ZXNjKGEubGFiZWwpfTwvYj4gwrcgJHtl"
    "c2MoYS5wZXJtcyl9PC9zcGFuPjxiPjxzcGFuIGNsYXNzPSJwaWxsIG9uIj4ke2VzYyhhLnJvbGUp"
    "fTwvc3Bhbj48L2I+PC9kaXY+YCkuam9pbigiIikKICAgICAgICAgOic8cCBjbGFzcz0ic3ViIiBz"
    "dHlsZT0ibWFyZ2luOjAiPk9ubHkgeW91IHNvIGZhci48L3A+J30KICAgICAgIDxkaXYgY2xhc3M9"
    "ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4Ij48ZGl2IHN0eWxlPSJkaXNwbGF5OmZsZXg7"
    "Z2FwOjdweCI+CiAgICAgICAgIDxpbnB1dCBjbGFzcz0iaW5wIiBwbGFjZWhvbGRlcj0iQHVzZXJu"
    "YW1lIj48c2VsZWN0IGNsYXNzPSJpbnAiIHN0eWxlPSJ3aWR0aDphdXRvIj48b3B0aW9uPk1hbmFn"
    "ZXI8L29wdGlvbj48b3B0aW9uPlN1cHBvcnQ8L29wdGlvbj48L3NlbGVjdD4KICAgICAgICAgPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIHNtIiBzdHlsZT0iZmxleDowIDAgYXV0byIgaWQ9ImFkbWluYWRkIj5B"
    "ZGQ8L2J1dHRvbj48L2Rpdj48L2Rpdj48L2Rpdj5gKX0KICAgJHtHKCJidXllckJhZGdlIixgPGRp"
    "diBjbGFzcz0iY2FyZCI+PGgzPlZlcmlmaWVkIGJ1eWVyIGJhZGdlPC9oMz4KICAgICA8ZGl2IGNs"
    "YXNzPSJmaWVsZCIgc3R5bGU9Im1hcmdpbi10b3A6OHB4Ij48bGFiZWw+T3JkZXJzIHJlcXVpcmVk"
    "PC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjUiPjwvZGl2PgogICAgIDxkaXYgY2xh"
    "c3M9Imt2Ij48c3Bhbj5WZXJpZmllZCB1c2Vyczwvc3Bhbj48Yj44NDwvYj48L2Rpdj48L2Rpdj5g"
    "KX0KICAgJHtHKCJtdWx0aUFjYyIsYDxkaXYgY2xhc3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNw"
    "bGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0i"
    "cGlsbCBob3QiPjcgZmxhZ2dlZDwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdp"
    "ZHRoOjA7Zm9udC1zaXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj5Qb3NzaWJsZSBtdWx0aS1h"
    "Y2NvdW50cyB0aGlzIHdlZWs8L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIg"
    "ZGF0YS1hLWdvPSJzZWN1cml0eSI+UmV2aWV3PC9idXR0b24+PC9kaXY+YCl9YDsKfQpmdW5jdGlv"
    "biBhU2V0dGluZ3MoKXsKICByZXR1cm4gYDxkaXYgY2xhc3M9ImNhcmQiPjxoMz5TdG9yZTwvaDM+"
    "PHAgY2xhc3M9InN1YiI+RXZlcnl0aGluZyBsaXZlcyBpbiB0aGUgZGF0YWJhc2Ug4oCUIG5vIGNv"
    "ZGUgZWRpdHMsIG5vIHJlc3RhcnQuPC9wPgogICAgPGRpdiBjbGFzcz0iZmllbGQiIHN0eWxlPSJt"
    "YXJnaW4tdG9wOjEycHgiPjxsYWJlbD5TdG9yZSBuYW1lPC9sYWJlbD4KICAgICAgPGlucHV0IGNs"
    "YXNzPSJpbnAiIGRhdGEtc2V0PSJzdG9yZV9uYW1lIiB2YWx1ZT0iJHtlc2MoQ01TLnN0b3JlTmFt"
    "ZXx8Q0ZHLm5hbWUpfSI+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlRhZ2xp"
    "bmU8L2xhYmVsPgogICAgICA8aW5wdXQgY2xhc3M9ImlucCIgZGF0YS1zZXQ9InN0b3JlX3RhZ2xp"
    "bmUiIHZhbHVlPSIke2VzYyhDTVMuc3RvcmVUYWd8fENGRy50YWcpfSI+PC9kaXY+CiAgICA8ZGl2"
    "IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlRhZ2xpbmU8L2xhYmVsPjxpbnB1dCBjbGFzcz0iaW5wIiB2"
    "YWx1ZT0iJHtDRkcudGFnfSI+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlN1"
    "cHBvcnQgY29udGFjdDwvbGFiZWw+PGlucHV0IGNsYXNzPSJpbnAiIHBsYWNlaG9sZGVyPSJAeW91"
    "cl9zdXBwb3J0Ij48L2Rpdj4KICAgIDwvZGl2PgogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+VG9n"
    "Z2xlczwvaDM+CiAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5Gb3JjZSBqb2lu"
    "PHNtYWxsPlJlcXVpcmUgY2hhbm5lbCBtZW1iZXJzaGlwPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2"
    "IGNsYXNzPSJ0ZyR7KEFETS5zdyYmQURNLnN3LmZvcmNlX3N1Yik/IiBvbiI6IiJ9IiBkYXRhLXNl"
    "dHRpbmc9ImZvcmNlX3N1YiI+PC9kaXY+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJzd2FwIj48ZGl2"
    "IGNsYXNzPSJ0Ij5NYWludGVuYW5jZSBtb2RlPHNtYWxsPkZyZWV6ZSB0aGUgc3RvcmU8L3NtYWxs"
    "PjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHsoQURNLnN3JiZBRE0uc3cubWFpbnRlbmFuY2Up"
    "PyIgb24iOiIifSIgZGF0YS1zZXR0aW5nPSJtYWludGVuYW5jZSI+PC9kaXY+PC9kaXY+CiAgICA8"
    "ZGl2IGNsYXNzPSJzd2FwIj48ZGl2IGNsYXNzPSJ0Ij5BdXRvIHJlZnVuZHM8c21hbGw+UmVmdW5k"
    "IGZhaWxlZCBsb2dpbnM8L3NtYWxsPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9InRnJHtGRUFULmF1"
    "dG9SZWZ1bmQ/IiBvbiI6IiJ9IiBkYXRhLWZlYXQ9ImF1dG9SZWZ1bmQiPjwvZGl2PjwvZGl2Pgog"
    "ICAgPGRpdiBjbGFzcz0ic3dhcCI+PGRpdiBjbGFzcz0idCI+UmVmZXJyYWwgcHJvZ3JhbTxzbWFs"
    "bD4xJSBvZiBpbnZpdGVkIGRlcG9zaXRzPC9zbWFsbD48L2Rpdj4KICAgICA8ZGl2IGNsYXNzPSJ0"
    "ZyR7RkVBVC5yZWZEYXNoPyIgb24iOiIifSIgZGF0YS1mZWF0PSJyZWZEYXNoIj48L2Rpdj48L2Rp"
    "dj48L2Rpdj4KICAgJHtHKCJpMThuIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkxhbmd1YWdlczwv"
    "aDM+PHAgY2xhc3M9InN1YiI+VXNlcnMgcGljayB0aGVpciBvd247IGV2ZXJ5IHN0cmluZyBpcyB0"
    "cmFuc2xhdGVkLjwvcD4KICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPkRlZmF1bHQgbGFu"
    "Z3VhZ2U8L2xhYmVsPgogICAgICA8c2VsZWN0IGNsYXNzPSJpbnAiIGRhdGEtc2V0c2VsPSJsYW5n"
    "X2RlZmF1bHQiPgogICAgICAgIDxvcHRpb24gdmFsdWU9ImVuIiAkeyhBRE0uc2V0dGluZ3MmJkFE"
    "TS5zZXR0aW5ncy5sYW5nPT09ImVuIik/InNlbGVjdGVkIjoiIn0+RW5nbGlzaDwvb3B0aW9uPgog"
    "ICAgICAgIDxvcHRpb24gdmFsdWU9ImhpIiAkeyhBRE0uc2V0dGluZ3MmJkFETS5zZXR0aW5ncy5s"
    "YW5nPT09ImhpIik/InNlbGVjdGVkIjoiIn0+4KS54KS/4KSo4KWN4KSm4KWAPC9vcHRpb24+CiAg"
    "ICAgIDwvc2VsZWN0PjwvZGl2PjwvZGl2PmApfQogICAke0coInNsYXNoQ21kcyIsYDxkaXYgY2xh"
    "c3M9ImNhcmQiPjxoMz5TbGFzaCBjb21tYW5kczwvaDM+PHAgY2xhc3M9InN1YiI+UmVnaXN0ZXJl"
    "ZCBpbiBUZWxlZ3JhbSdzIGNvbW1hbmQgbWVudS48L3A+CiAgICAgJHtbWyIvc3RhcnQiLCJPcGVu"
    "IHRoZSBzdG9yZSJdLFsiL2J1eSIsIkp1bXAgdG8gc3RvY2siXSxbIi9iYWxhbmNlIiwiU2hvdyB3"
    "YWxsZXQiXSxbIi9vcmRlcnMiLCJNeSBvcmRlcnMiXSxbIi9zdXBwb3J0IiwiQ29udGFjdCBzdXBw"
    "b3J0Il1dCiAgICAgICAubWFwKChbYyxkXSk9PmA8ZGl2IGNsYXNzPSJrdiI+PHNwYW4+PGIgc3R5"
    "bGU9ImNvbG9yOnZhcigtLXR4KSI+JHtjfTwvYj48L3NwYW4+PGIgc3R5bGU9ImZvbnQtd2VpZ2h0"
    "OjUwMDtmb250LXNpemU6Ljc0cmVtIj4ke2R9PC9iPjwvZGl2PmApLmpvaW4oIiIpfQogICAgIDxi"
    "dXR0b24gY2xhc3M9ImJ0biBibG9jayBzb2Z0IiBzdHlsZT0ibWFyZ2luLXRvcDoxMHB4IiBkYXRh"
    "LWFkbWluLWFjdD0ic3luYyI+U3luYyBDb21tYW5kczwvYnV0dG9uPjwvZGl2PmApfQogICAke0co"
    "ImF1dG9CYWNrdXAiLGA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+QXV0by1iYWNrdXA8L2gzPjxwIGNs"
    "YXNzPSJzdWIiPkRhdGFiYXNlIHRvIGEgcHJpdmF0ZSBjaGFubmVsIG9uIGEgc2NoZWR1bGUuPC9w"
    "PgogICAgIDxkaXYgY2xhc3M9ImZpZWxkIiBzdHlsZT0ibWFyZ2luLXRvcDo4cHgiPjxsYWJlbD5F"
    "dmVyeSAoaG91cnMpPC9sYWJlbD48aW5wdXQgY2xhc3M9ImlucCIgdmFsdWU9IjYiPjwvZGl2Pgog"
    "ICAgIDxkaXYgY2xhc3M9Imt2Ij48c3Bhbj5MYXN0IGJhY2t1cDwvc3Bhbj48Yj40MiBtaW4gYWdv"
    "PC9iPjwvZGl2PgogICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTBw"
    "eCI+PGJ1dHRvbiBjbGFzcz0iYnRuIiBkYXRhLWFkbWluLWFjdD0iYmFja3VwIj5CYWNrIFVwIE5v"
    "dzwvYnV0dG9uPgogICAgICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGdob3N0IiBkYXRhLWFkbWluLWFj"
    "dD0icmVzdG9yZSI+UmVzdG9yZTwvYnV0dG9uPjwvZGl2PjwvZGl2PmApfQogICAke0coImF1ZGl0"
    "TG9nIixgPGRpdiBjbGFzcz0iY2FyZCI+PGgzPkF1ZGl0IGxvZzwvaDM+PHAgY2xhc3M9InN1YiI+"
    "RXZlcnkgYWRtaW4gYWN0aW9uLCB3aXRoIHVuZG8uPC9wPgogICAgICR7KChBRE0uYXVkaXQmJkFE"
    "TS5hdWRpdC5sZW5ndGgpP0FETS5hdWRpdC5tYXAoYT0+W2Eud2hvLChhLndoYXQrIiAiK2EuZGV0"
    "YWlsKS50cmltKCksYS53aGVuXSk6W10pCiAgICAgICAubWFwKChbdSxhLHRdKT0+YDxkaXYgY2xh"
    "c3M9Imt2Ij48c3Bhbj48YiBzdHlsZT0iY29sb3I6dmFyKC0tdHgpIj4ke3V9PC9iPiAke2F9PC9z"
    "cGFuPgogICAgICAgICA8Yj4ke3R9IDwvYj48L2Rpdj5gKS5qb2luKCIiKX08L2Rpdj5gKX0KICAg"
    "JHtHKCJzYW5kYm94IixgPGRpdiBjbGFzcz0iY2FyZCI+PGRpdiBjbGFzcz0ic3dhcCIgc3R5bGU9"
    "InBhZGRpbmctdG9wOjAiPjxkaXYgY2xhc3M9InQiPlNhbmRib3ggLyB0ZXN0IG1vZGU8c21hbGw+"
    "RmFrZSBtb25leSwgbm8gcmVhbCBkZWxpdmVyeTwvc21hbGw+PC9kaXY+CiAgICAgPGRpdiBjbGFz"
    "cz0idGckeyhBRE0uc3cmJkFETS5zdy5zYW5kYm94KT8iIG9uIjoiIn0iIGRhdGEtc2V0dGluZz0i"
    "c2FuZGJveCI+PC9kaXY+PC9kaXY+PC9kaXY+YCl9CiAgICR7RygiZW5jcnlwdCIsYDxkaXYgY2xh"
    "c3M9ImNhcmQgdGlnaHQiIHN0eWxlPSJkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dh"
    "cDoxMHB4Ij4KICAgICA8c3BhbiBjbGFzcz0icGlsbCBvbiI+PGkgY2xhc3M9ImRvdCI+PC9pPkVu"
    "Y3J5cHRlZDwvc3Bhbj4KICAgICA8ZGl2IHN0eWxlPSJmbGV4OjE7bWluLXdpZHRoOjA7Zm9udC1z"
    "aXplOi43NHJlbTtjb2xvcjp2YXIoLS10eDIpIj5TZXNzaW9ucyBhcmUgZW5jcnlwdGVkIGF0IHJl"
    "c3Q8L2Rpdj4KICAgICA8YnV0dG9uIGNsYXNzPSJidG4geHMgc29mdCIgZGF0YS1hLWdvPSJzZWN1"
    "cml0eSI+S2V5czwvYnV0dG9uPjwvZGl2PmApfQogICA8ZGl2IGNsYXNzPSJjYXJkIj48aDM+RGFu"
    "Z2VyIHpvbmU8L2gzPjxwIGNsYXNzPSJzdWIiPklycmV2ZXJzaWJsZS4gQXNrIHR3aWNlLjwvcD4K"
    "ICAgIDxkaXYgY2xhc3M9ImJ0bnJvdyIgc3R5bGU9Im1hcmdpbi10b3A6MTJweCI+PGJ1dHRvbiBj"
    "bGFzcz0iYnRuIHhzIHNvZnQiIGRhdGEtYWRtaW4tYWN0PSJiYWNrdXAiPkJhY2t1cDwvYnV0dG9u"
    "PgogICAgPGJ1dHRvbiBjbGFzcz0iYnRuIGJhZCBob2xkIiBkYXRhLWRvbmU9IkRhdGFiYXNlIHJl"
    "c2V0IChkZW1vKSI+PHNwYW4gY2xhc3M9ImZpbGwiPjwvc3Bhbj5Ib2xkIHRvIFJlc2V0IERCPC9i"
    "dXR0b24+PC9kaXY+PC9kaXY+YDsKfQoKZnVuY3Rpb24gYmluZEFkbWluKCl7CiAgYmluZENvbW1v"
    "bigpOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWZlYXRdIikuZm9yRWFjaCh0"
    "PT50Lm9uY2xpY2s9KCk9PnsKICAgIGNvbnN0IGs9dC5kYXRhc2V0LmZlYXQ7RkVBVFtrXT0hRkVB"
    "VFtrXTt0LmNsYXNzTGlzdC50b2dnbGUoIm9uIixGRUFUW2tdKTsKICAgIHB1c2goIi9hcGkvYWRt"
    "aW4vZmVhdHVyZSIse2tleTprLG9uOkZFQVRba119KTsKICAgIGNvbnN0IGM9ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoImZjb3VudCIpOwogICAgaWYoYyljLnRleHRDb250ZW50PU9iamVjdC52YWx1"
    "ZXMoRkVBVCkuZmlsdGVyKEJvb2xlYW4pLmxlbmd0aCsiIG9mICIrT2JqZWN0LmtleXMoRkVBVCku"
    "bGVuZ3RoOwogICAgYXBwbHlGZWF0dXJlcygpOwogICAgY29uc3QgY2FyZD10LmNsb3Nlc3QoIi5m"
    "Y2FyZCIpLCBuYW1lRWw9Y2FyZD9jYXJkLnF1ZXJ5U2VsZWN0b3IoImIiKTpudWxsOwogICAgY29u"
    "c3QgbGFiZWw9bmFtZUVsP25hbWVFbC50ZXh0Q29udGVudDprLnJlcGxhY2UoLyhbQS1aXSkvZywi"
    "ICQxIikudG9Mb3dlckNhc2UoKTsKICAgIHRvYXN0KGxhYmVsKyhGRUFUW2tdPyIgZW5hYmxlZCI6"
    "IiBoaWRkZW4iKSk7fSk7CiAgY29uc3QgZnE9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImZxIik7"
    "CiAgaWYoZnEpZnEub25pbnB1dD0oKT0+e2NvbnN0IHE9ZnEudmFsdWUudHJpbSgpLnRvTG93ZXJD"
    "YXNlKCk7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIuZnJvdyIpLmZvckVhY2gocj0+"
    "ci5jbGFzc0xpc3QudG9nZ2xlKCJoaWRlIiwhIXEmJiFyLmRhdGFzZXQuZi5pbmNsdWRlcyhxKSkp"
    "OwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiLmZncnAiKS5mb3JFYWNoKGc9PmcuY2xh"
    "c3NMaXN0LnRvZ2dsZSgiaGlkZSIsIWcucXVlcnlTZWxlY3RvcigiLmZyb3c6bm90KC5oaWRlKSIp"
    "KSk7fTsKICBjb25zdCBzZXRBbGw9dj0+e09iamVjdC5rZXlzKEZFQVQpLmZvckVhY2goaz0+RkVB"
    "VFtrXT12KTthcHBseUZlYXR1cmVzKCk7cmVuZGVyQWRtaW4oKTt0b2FzdCh2PyJBbGwgZmVhdHVy"
    "ZXMgZW5hYmxlZCI6IkFsbCBmZWF0dXJlcyBoaWRkZW4iKTt9OwogIGNvbnN0IGZhPWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJmYWxsIiksZm49ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImZub25l"
    "IiksZnI9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImZyZXNldCIpOwogIGlmKGZhKWZhLm9uY2xp"
    "Y2s9KCk9PnNldEFsbCh0cnVlKTsKICBpZihmbilmbi5vbmNsaWNrPSgpPT5zZXRBbGwoZmFsc2Up"
    "OwogIGlmKGZyKWZyLm9uY2xpY2s9KCk9PntPYmplY3QuYXNzaWduKEZFQVQsRkVBVDApO2FwcGx5"
    "RmVhdHVyZXMoKTtyZW5kZXJBZG1pbigpO3RvYXN0KCJGZWF0dXJlcyByZXNldCIpO307CiAgY29u"
    "c3Qgc3Rtc2c9bT0+e2NvbnN0IGU9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0bXNnIik7aWYo"
    "ZSllLnRleHRDb250ZW50PW07fTsKICBhc3luYyBmdW5jdGlvbiBpbXBvcnRTdG9jayh0ZXh0KXsK"
    "ICAgIGlmKCF0ZXh0LnRyaW0oKSl7dG9hc3QoIlBhc3RlIHNvbWUgbGluZXMgZmlyc3QiKTtyZXR1"
    "cm47fQogICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgc3Rt"
    "c2coIkltcG9ydGluZ+KApiIpOwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4v"
    "c3RvY2siLHt0ZXh0OnRleHR9KTsKICAgIGlmKCFyKXtzdG1zZygiIik7cmV0dXJuO30KICAgIGNv"
    "bnN0IG5vdGU9ci5hZGRlZCsiIGltcG9ydGVkIisoci5za2lwcGVkJiZyLnNraXBwZWQubGVuZ3Ro"
    "PygiIMK3ICIrci5za2lwcGVkLmxlbmd0aCsiIHNraXBwZWQiKToiIik7CiAgICB0b2FzdChyLmFk"
    "ZGVkKyIgYWNjb3VudHMgYWRkZWQiKTsKICAgIGNvbnN0IGJveD1kb2N1bWVudC5nZXRFbGVtZW50"
    "QnlJZCgic3RwYXN0ZSIpO2lmKGJveClib3gudmFsdWU9IiI7CiAgICBhd2FpdCByZWZyZXNoKCk7"
    "CiAgICBzdG1zZyhub3RlKTsKICB9CiAgY29uc3Qgc3RzYXZlPWRvY3VtZW50LmdldEVsZW1lbnRC"
    "eUlkKCJzdHNhdmUiKTsKICBpZihzdHNhdmUpc3RzYXZlLm9uY2xpY2s9KCk9PmltcG9ydFN0b2Nr"
    "KChkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RwYXN0ZSIpfHx7fSkudmFsdWV8fCIiKTsKICBj"
    "b25zdCBzdGZpbGU9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0ZmlsZSIpOwogIGlmKHN0Zmls"
    "ZSlzdGZpbGUub25jaGFuZ2U9KCk9PnsKICAgIGNvbnN0IGY9c3RmaWxlLmZpbGVzJiZzdGZpbGUu"
    "ZmlsZXNbMF07aWYoIWYpcmV0dXJuOwogICAgY29uc3QgcmQ9bmV3IEZpbGVSZWFkZXIoKTsKICAg"
    "IHJkLm9ubG9hZD0oKT0+e2NvbnN0IGJveD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RwYXN0"
    "ZSIpOwogICAgICBpZihib3gpYm94LnZhbHVlPXJkLnJlc3VsdDtzdG1zZyhmLm5hbWUrIiBsb2Fk"
    "ZWQg4oCUIHByZXNzIEltcG9ydCIpO307CiAgICByZC5yZWFkQXNUZXh0KGYpOwogIH07CiAgY29u"
    "c3QgYmw9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJsYmxvY2siKTsKICBpZihibClibC5vbmNs"
    "aWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGk9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImJsLWlu"
    "Iik7CiAgICBjb25zdCB2PWk/KGkudmFsdWV8fCIiKS50cmltKCk6IiI7CiAgICBpZighdil7dG9h"
    "c3QoIlR5cGUgYW4gaWQsIHBob25lIG9yIFVQSSByZWZlcmVuY2UiKTtpZihpKWkuZm9jdXMoKTty"
    "ZXR1cm47fQogICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAg"
    "Y29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vYmxhY2tsaXN0Iix7YWN0aW9uOiJhZGQi"
    "LHZhbHVlOnZ9KTsKICAgIGlmKCFyKXJldHVybjsKICAgIGkudmFsdWU9IiI7dG9hc3QoIkJsb2Nr"
    "ZWQgIit2KTthd2FpdCByZWZyZXNoKCk7CiAgfTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxs"
    "KCJbZGF0YS1ibC1kZWxdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29u"
    "c3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vYmxhY2tsaXN0Iix7YWN0aW9uOiJkZWwiLGlk"
    "OitiLmRhdGFzZXQuYmxEZWx9KTsKICAgIGlmKHIpe3RvYXN0KCJVbmJsb2NrZWQiKTthd2FpdCBy"
    "ZWZyZXNoKCk7fQogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLW1pc2Nd"
    "IikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYoIUxJVkUpe3RvYXN0KCJQ"
    "cmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgY29uc3Qgd2hhdD1iLmRhdGFzZXQubWlzYywgdmFs"
    "PWIuZGF0YXNldC5taXNjVmFsdWV8fCIiOwogICAgY29uc3QgbGJsPWIudGV4dENvbnRlbnQ7Yi5k"
    "aXNhYmxlZD10cnVlO2IudGV4dENvbnRlbnQ9IuKApiI7CiAgICBjb25zdCByPWF3YWl0IGFwaVRy"
    "eSgiL2FwaS9hZG1pbi9taXNjIix7d2hhdDp3aGF0LHZhbHVlOnZhbH0pOwogICAgYi5kaXNhYmxl"
    "ZD1mYWxzZTtiLnRleHRDb250ZW50PWxibDsKICAgIGlmKCFyKXJldHVybjsKICAgIGlmKHIubGlu"
    "ZXMpc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0iPkNoYW5u"
    "ZWwgY2hlY2s8L2gzPgogICAgICA8cCBjbGFzcz0ic3ViIiBzdHlsZT0id2hpdGUtc3BhY2U6cHJl"
    "LWxpbmUiPiR7ZXNjKHIubGluZXMuam9pbigiXG4iKSl9PC9wPgogICAgICA8YnV0dG9uIGNsYXNz"
    "PSJidG4gYmxvY2siIHN0eWxlPSJtYXJnaW4tdG9wOjEycHgiIGRhdGEtY2xvc2U+Q2xvc2U8L2J1"
    "dHRvbj5gKTsKICAgIGVsc2UgaWYod2hhdD09PSJsb2d0ZXN0Iil0b2FzdCgiVGVzdCBtZXNzYWdl"
    "IHNlbnQgdG8gdGhlIGxvZyBjaGFubmVsIik7CiAgICBlbHNlIGlmKHdoYXQ9PT0iY2xlYXJzdG9j"
    "ayIpdG9hc3Qoci5yZW1vdmVkKyIgcmVtb3ZlZCIpOwogICAgZWxzZSB0b2FzdCgiRG9uZSIpOwog"
    "ICAgYXdhaXQgcmVmcmVzaCgpOwogIH0pOwogIGNvbnN0IHVwaWFkZD1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgidXBpYWRkIik7CiAgaWYodXBpYWRkKXVwaWFkZC5vbmNsaWNrPWFzeW5jKCk9PnsK"
    "ICAgIGNvbnN0IGk9dXBpYWRkLmNsb3Nlc3QoImRpdiIpLnF1ZXJ5U2VsZWN0b3IoImlucHV0Iik7"
    "CiAgICBjb25zdCB2PWk/KGkudmFsdWV8fCIiKS50cmltKCk6IiI7CiAgICBpZighdil7dG9hc3Qo"
    "IlBhc3RlIHRoZSBleHRyYSBVUEkgaGFuZGxlIik7cmV0dXJuO30KICAgIGNvbnN0IGN1cj0oQURN"
    "LnBheSYmQURNLnBheS5leHRyYSl8fCIiOwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkv"
    "YWRtaW4vc2V0dGluZyIse2tleToidXBpX2V4dHJhIix2YWx1ZTpjdXI/KGN1cisiLCIrdik6dn0p"
    "OwogICAgaWYocil7aS52YWx1ZT0iIjt0b2FzdCgiSGFuZGxlIGFkZGVkIik7YXdhaXQgcmVmcmVz"
    "aCgpO30KICB9OwogIGNvbnN0IGFkbWluYWRkPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJhZG1p"
    "bmFkZCIpOwogIGlmKGFkbWluYWRkKWFkbWluYWRkLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29u"
    "c3QgaT1hZG1pbmFkZC5jbG9zZXN0KCJkaXYiKS5xdWVyeVNlbGVjdG9yKCJpbnB1dCIpOwogICAg"
    "Y29uc3Qgdj1pPyhpLnZhbHVlfHwiIikudHJpbSgpLnJlcGxhY2UoL15ALywiIik6IiI7CiAgICBp"
    "Zighdil7dG9hc3QoIlNlbmQgdGhlIG51bWVyaWMgdXNlciBpZCIpO3JldHVybjt9CiAgICBjb25z"
    "dCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9taXNjIix7d2hhdDoiYWRkYWRtaW4iLHZhbHVl"
    "OnZ9KTsKICAgIGlmKHIpe2kudmFsdWU9IiI7dG9hc3QoIkFkbWluIGFkZGVkIHdpdGggbm8gcG93"
    "ZXJzIOKAlCBncmFudCB0aGVtIGluIHRoZSBib3QiKTthd2FpdCByZWZyZXNoKCk7fQogIH07CiAg"
    "Y29uc3QgcHJpemU9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInByaXplc2F2ZSIpOwogIGlmKHBy"
    "aXplKXByaXplLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29uc3QgaT1wcml6ZS5jbG9zZXN0KCJk"
    "aXYiKS5xdWVyeVNlbGVjdG9yKCJpbnB1dCIpOwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9h"
    "cGkvYWRtaW4vc2V0dGluZyIse2tleToibGVhZGVyYm9hcmRfcHJpemUiLHZhbHVlOihpJiZpLnZh"
    "bHVlKXx8IjAifSk7CiAgICBpZihyKXRvYXN0KCJQcml6ZSBzYXZlZCIpOwogIH07CiAgY29uc3Qg"
    "Y3BhZGQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNwYWRkIik7CiAgaWYoY3BhZGQpY3BhZGQu"
    "b25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBjb2RlPSgoZG9jdW1lbnQuZ2V0RWxlbWVudEJ5"
    "SWQoImNwY29kZSIpfHx7fSkudmFsdWV8fCIiKS50cmltKCk7CiAgICBjb25zdCBwY3Q9KChkb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgiY3BwY3QiKXx8e30pLnZhbHVlfHwiIikudHJpbSgpOwogICAg"
    "aWYoIWNvZGV8fCFwY3Qpe3RvYXN0KCJDb2RlIGFuZCBwZXJjZW50LCBwbGVhc2UiKTtyZXR1cm47"
    "fQogICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgY29uc3Qg"
    "cj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vY291cG9uIix7Y29kZTpjb2RlLHBlcmNlbnQ6cGN0"
    "fSk7CiAgICBpZighcilyZXR1cm47CiAgICBjb25zdCBhMT1kb2N1bWVudC5nZXRFbGVtZW50QnlJ"
    "ZCgiY3Bjb2RlIiksYTI9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNwcGN0Iik7CiAgICBpZihh"
    "MSlhMS52YWx1ZT0iIjtpZihhMilhMi52YWx1ZT0iIjsKICAgIHRvYXN0KCJDb3Vwb24gIitjb2Rl"
    "LnRvVXBwZXJDYXNlKCkrIiBpcyBsaXZlIik7YXdhaXQgcmVmcmVzaCgpOwogIH07CiAgY29uc3Qg"
    "Zmc9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImZsYXNoZ28iKTsKICBpZihmZylmZy5vbmNsaWNr"
    "PWFzeW5jKCk9PnsKICAgIGNvbnN0IGNhcmQ9ZmcuY2xvc2VzdCgiLmNhcmQiKXx8ZG9jdW1lbnQ7"
    "CiAgICBjb25zdCBpbnM9Y2FyZC5xdWVyeVNlbGVjdG9yQWxsKCJpbnB1dCIpOwogICAgY29uc3Qg"
    "cGN0PSgoaW5zWzBdJiZpbnNbMF0udmFsdWUpfHwiMjAiKSwgaHJzPSgoaW5zWzFdJiZpbnNbMV0u"
    "dmFsdWUpfHwiNSIpOwogICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47"
    "fQogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vZmxhc2giLHtwZXJjZW50OnBj"
    "dCxob3VyczpocnN9KTsKICAgIGlmKHIpe3RvYXN0KCJGbGFzaCBzYWxlIGxpdmUg4oCUICIrcGN0"
    "KyIlIGZvciAiK2hycysiaCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgfTsKICBjb25zdCBiYz1kb2N1"
    "bWVudC5nZXRFbGVtZW50QnlJZCgiYmNnbyIpOwogIGlmKGJjKWJjLm9uY2xpY2s9YXN5bmMoKT0+"
    "ewogICAgY29uc3QgY2FyZD1iYy5jbG9zZXN0KCIuY2FyZCIpfHxkb2N1bWVudDsKICAgIGNvbnN0"
    "IHRhPWNhcmQucXVlcnlTZWxlY3RvcigidGV4dGFyZWEiKTsKICAgIGNvbnN0IHRleHQ9KCh0YSYm"
    "dGEudmFsdWUpfHwiIikudHJpbSgpOwogICAgaWYoIXRleHQpe3RvYXN0KCJXcml0ZSB0aGUgbWVz"
    "c2FnZSBmaXJzdCIpO2lmKHRhKXRhLmZvY3VzKCk7cmV0dXJuO30KICAgIGlmKCFMSVZFKXt0b2Fz"
    "dCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgIGJjLmRpc2FibGVkPXRydWU7YmMudGV4dENv"
    "bnRlbnQ9IlNlbmRpbmfigKYiOwogICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4v"
    "YnJvYWRjYXN0Iix7dGV4dDp0ZXh0fSk7CiAgICBiYy5kaXNhYmxlZD1mYWxzZTtiYy50ZXh0Q29u"
    "dGVudD0iU2VuZCBCcm9hZGNhc3QiOwogICAgaWYocil7dG9hc3QoIlNlbnQgdG8gIityLnNlbnQr"
    "IiBwZW9wbGUiKTtpZih0YSl0YS52YWx1ZT0iIjt9CiAgfTsKICBkb2N1bWVudC5xdWVyeVNlbGVj"
    "dG9yQWxsKCJbZGF0YS1kZXAtb2tdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewog"
    "ICAgY29uc3QgaWQ9Yi5kYXRhc2V0LmRlcE9rOwogICAgaWYoIWlkKXt0b2FzdCgiTm90aGluZyB0"
    "byBhcHByb3ZlIik7cmV0dXJuO30KICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWlu"
    "L2RlcG9zaXQiLHtpZDoraWQsYWN0aW9uOiJhcHByb3ZlIn0pOwogICAgaWYocil7dG9hc3QoIkNy"
    "ZWRpdGVkIik7YXdhaXQgcmVmcmVzaCgpO30KICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "QWxsKCJbZGF0YS1yZXZdIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgY29u"
    "c3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vcmV2aWV3Iix7aWQ6K2IuZGF0YXNldC5yZXZ9"
    "KTsKICAgIGlmKHIpe3RvYXN0KCJVcGRhdGVkIik7YXdhaXQgcmVmcmVzaCgpO30KICB9KTsKICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1zZXR0aW5nXSIpLmZvckVhY2godD0+ewog"
    "ICAgdC5vbmNsaWNrPWFzeW5jKCk9PnsKICAgICAgY29uc3Qga2V5PXQuZGF0YXNldC5zZXR0aW5n"
    "LCBvbj0hdC5jbGFzc0xpc3QuY29udGFpbnMoIm9uIik7CiAgICAgIHQuY2xhc3NMaXN0LnRvZ2ds"
    "ZSgib24iLG9uKTsKICAgICAgaWYoIUxJVkUpe3RvYXN0KG9uPyJPbiI6Ik9mZiIpO3JldHVybjt9"
    "CiAgICAgIGNvbnN0IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3NldHRpbmciLHtrZXk6a2V5"
    "LHZhbHVlOm9uPyIxIjoiMCJ9KTsKICAgICAgaWYoIXIpe3QuY2xhc3NMaXN0LnRvZ2dsZSgib24i"
    "LCFvbik7cmV0dXJuO30KICAgICAgQURNLnN3PUFETS5zd3x8e307QURNLnN3W2tleV09b247CiAg"
    "ICAgIHRvYXN0KGtleS5yZXBsYWNlKC9fL2csIiAiKSsob24/IiBvbiI6IiBvZmYiKSk7CiAgICB9"
    "OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWFkbWluLWFjdF0iKS5m"
    "b3JFYWNoKGI9PnsKICAgIGIub25jbGljaz1hc3luYygpPT57CiAgICAgIGNvbnN0IGFjdD1iLmRh"
    "dGFzZXQuYWRtaW5BY3Q7CiAgICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0"
    "dXJuO30KICAgICAgY29uc3QgbGFiZWw9Yi50ZXh0Q29udGVudDtiLmRpc2FibGVkPXRydWU7Yi50"
    "ZXh0Q29udGVudD0iV29ya2luZ+KApiI7CiAgICAgIGxldCByPW51bGw7CiAgICAgIGlmKGFjdD09"
    "PSJiYWNrdXAiKSAgICAgIHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2JhY2t1cCIse30pOwog"
    "ICAgICBlbHNlIGlmKGFjdD09PSJkaWdlc3QiKSByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9k"
    "aWdlc3QiLHt9KTsKICAgICAgZWxzZSBpZihhY3Q9PT0iZXhwb3J0Iikgcj1hd2FpdCBhcGlUcnko"
    "Ii9hcGkvYWRtaW4vZXhwb3J0Iix7fSk7CiAgICAgIGVsc2UgaWYoYWN0PT09InN5bmMiKSAgIHI9"
    "YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL3N5bmMiLHt9KTsKICAgICAgYi5kaXNhYmxlZD1mYWxz"
    "ZTtiLnRleHRDb250ZW50PWxhYmVsOwogICAgICBpZighcilyZXR1cm47CiAgICAgIGlmKGFjdD09"
    "PSJiYWNrdXAiKXRvYXN0KCJEYXRhYmFzZSBzZW50IHRvIHlvdSBpbiBjaGF0Iik7CiAgICAgIGlm"
    "KGFjdD09PSJkaWdlc3QiKXRvYXN0KCJEaWdlc3Qgc2VudCB0byB0aGUgYWRtaW5zIik7CiAgICAg"
    "IGlmKGFjdD09PSJleHBvcnQiKXRvYXN0KHIucm93cysiIG9yZGVycyBzZW50IGFzIENTViIpOwog"
    "ICAgICBpZihhY3Q9PT0ic3luYyIpdG9hc3QoIkNvbW1hbmRzIHN5bmNlZCB3aXRoIFRlbGVncmFt"
    "Iik7CiAgICAgIGF3YWl0IHJlZnJlc2goKTsKICAgIH07CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlT"
    "ZWxlY3RvckFsbCgiW2RhdGEtcHJpY2VdIikuZm9yRWFjaChpbnA9PnsKICAgIGlucC5vbmNoYW5n"
    "ZT1hc3luYygpPT57CiAgICAgIGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJu"
    "O30KICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vcHJpY2luZyIse2tleTpp"
    "bnAuZGF0YXNldC5wcmljZSx2YWx1ZTppbnAudmFsdWV9KTsKICAgICAgaWYocil7dG9hc3QoIlBy"
    "aWNpbmcgdXBkYXRlZCIpO2F3YWl0IHJlZnJlc2goKTt9CiAgICB9OwogIH0pOwogIGRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWNwcmljZV0iKS5mb3JFYWNoKGlucD0+ewogICAgaW5w"
    "Lm9uY2hhbmdlPWFzeW5jKCk9PnsKICAgICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHki"
    "KTtyZXR1cm47fQogICAgICBjb25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9wcmljaW5n"
    "Iix7Y291bnRyeTppbnAuZGF0YXNldC5jcHJpY2UscHJpY2U6aW5wLnZhbHVlfSk7CiAgICAgIGlm"
    "KHIpe3RvYXN0KGlucC5kYXRhc2V0LmNwcmljZSsiIG5vdyAiK21vbmV5KGlucC52YWx1ZSkpO2F3"
    "YWl0IHJlZnJlc2goKTt9CiAgICB9OwogIH0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IltkYXRhLXFhXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgICBjb25zdCB3aGF0PWIu"
    "ZGF0YXNldC5xYTsKICAgIGNvbnN0IGdvPSh0YWIsbXNnKT0+ewogICAgICBjb25zdCB0PWRvY3Vt"
    "ZW50LnF1ZXJ5U2VsZWN0b3IoYCNhc3ViIGJ1dHRvbltkYXRhLWE9IiR7dGFifSJdYCk7CiAgICAg"
    "IGlmKHQpe3QuY2xpY2soKTt0b2FzdChtc2cpO31lbHNlIHRvYXN0KG1zZyk7CiAgICB9OwogICAg"
    "aWYod2hhdD09PSJicm9hZGNhc3QiKXJldHVybiBnbygibWFya2V0IiwiQ29tcG9zZSB0aGUgYnJv"
    "YWRjYXN0IGZyb20gdGhlIGJvdDogL2FkbWluIOKGkiBNYXJrZXRpbmciKTsKICAgIGlmKHdoYXQ9"
    "PT0ic3RvY2siKXJldHVybiBnbygic3RvY2siLCJBZGQgc3RvY2sgZnJvbSB0aGUgYm90OiAvYWRt"
    "aW4g4oaSIFN0b2NrIOKGkiBBZGQgc3RvY2siKTsKICAgIGlmKHdoYXQ9PT0iZmxhc2giKXJldHVy"
    "biBnbygibWFya2V0IiwiRmxhc2ggc2FsZSBsaXZlcyBoZXJlIik7CiAgICBpZih3aGF0PT09ImJh"
    "Y2t1cCIpcmV0dXJuIHRvYXN0KCJCYWNrdXBzIGFyZSBzZW50IHRvIHlvdXIgbG9nIGNoYW5uZWwg"
    "4oCUIC9hZG1pbiDihpIgU2V0dGluZ3MiKTsKICB9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9y"
    "QWxsKCJbZGF0YS1zZXRdIikuZm9yRWFjaChpbnA9PnsKICAgIGlucC5vbmNoYW5nZT1hc3luYygp"
    "PT57CiAgICAgIGNvbnN0IGtleT1pbnAuZGF0YXNldC5zZXQsIHZhbD1pbnAudmFsdWU7CiAgICAg"
    "IGlmKCFMSVZFKXt0b2FzdCgiUHJldmlldyBvbmx5Iik7cmV0dXJuO30KICAgICAgY29uc3Qgcj1h"
    "d2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc2V0dGluZyIse2tleTprZXksdmFsdWU6dmFsfSk7CiAg"
    "ICAgIGlmKHIpe3RvYXN0KGtleS5yZXBsYWNlKC9fL2csIiAiKSsiIHNhdmVkIik7YXdhaXQgcmVm"
    "cmVzaCgpO30KICAgIH07CiAgfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEt"
    "Y21zXSIpLmZvckVhY2godD0+ewogICAgdC5vbmlucHV0PSgpPT57Q01TW3QuZGF0YXNldC5jbXNd"
    "PXQudmFsdWU7YXBwbHlDTVMoKTsKICAgICAgY2xlYXJUaW1lb3V0KHQuX3N2KTt0Ll9zdj1zZXRU"
    "aW1lb3V0KCgpPT5wdXNoKCIvYXBpL2FkbWluL2NvbnRlbnQiLHtrZXk6dC5kYXRhc2V0LmNtcyx2"
    "YWx1ZTp0LnZhbHVlfSksNTAwKTsKICAgICAgdC5jbG9zZXN0KCIuY21zcm93IikuZGF0YXNldC5m"
    "PSh0LmNsb3Nlc3QoIi5jbXNyb3ciKS5xdWVyeVNlbGVjdG9yKCJsYWJlbCIpLnRleHRDb250ZW50"
    "KyIgIit0LnZhbHVlKS50b0xvd2VyQ2FzZSgpO307fSk7CiAgY29uc3QgY3E9ZG9jdW1lbnQuZ2V0"
    "RWxlbWVudEJ5SWQoImNxIik7CiAgaWYoY3EpY3Eub25pbnB1dD0oKT0+e2NvbnN0IHE9Y3EudmFs"
    "dWUudHJpbSgpLnRvTG93ZXJDYXNlKCk7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIu"
    "Y21zcm93IikuZm9yRWFjaChyPT5yLmNsYXNzTGlzdC50b2dnbGUoImhpZGUiLCEhcSYmIXIuZGF0"
    "YXNldC5mLmluY2x1ZGVzKHEpKSk7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIuY21z"
    "Z3JwIikuZm9yRWFjaChnPT5nLmNsYXNzTGlzdC50b2dnbGUoImhpZGUiLCFnLnF1ZXJ5U2VsZWN0"
    "b3IoIi5jbXNyb3c6bm90KC5oaWRlKSIpKSk7fTsKICBjb25zdCBjcj1kb2N1bWVudC5nZXRFbGVt"
    "ZW50QnlJZCgiY21zcmVzZXQiKTsKICBpZihjciljci5vbmNsaWNrPSgpPT57T2JqZWN0LmFzc2ln"
    "bihDTVMsQ01TMCk7YXBwbHlDTVMoKTtyZW5kZXJBZG1pbigpO3RvYXN0KCJUZXh0IHJlc2V0IHRv"
    "IGRlZmF1bHRzIik7fTsKICBjb25zdCBhZGQ9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNoLWFk"
    "ZCIpOwogIGlmKGFkZClhZGQub25jbGljaz1hc3luYygpPT57CiAgICBjb25zdCBpPWRvY3VtZW50"
    "LmdldEVsZW1lbnRCeUlkKCJjaC1pbiIpLHY9KGkudmFsdWV8fCIiKS50cmltKCk7CiAgICBpZigh"
    "dil7dG9hc3QoIlBhc3RlIGEgY2hhbm5lbCBsaW5rIGZpcnN0Iik7aS5mb2N1cygpO3JldHVybn0K"
    "ICAgIGNvbnN0IHJlcT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY2gtdHlwZSIpLnZhbHVlPT09"
    "IjEiOwogICAgaWYoTElWRSl7CiAgICAgIGFkZC5kaXNhYmxlZD10cnVlOwogICAgICBjb25zdCBy"
    "PWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJhZGQiLGhhbmRsZTp2"
    "LHJlcXVpcmVkOnJlcX0pOwogICAgICBhZGQuZGlzYWJsZWQ9ZmFsc2U7CiAgICAgIGlmKCFyKXJl"
    "dHVybjsKICAgICAgaS52YWx1ZT0iIjthd2FpdCByZWZyZXNoKCk7dG9hc3QoIkNoYW5uZWwgc2F2"
    "ZWQg4oCUIHRoZSBib3QgdXNlcyBpdCBub3ciKTtyZXR1cm47CiAgICB9CiAgICBDSEFOTkVMUy5w"
    "dXNoKHtpZDpEYXRlLm5vdygpLG5hbWU6di5yZXBsYWNlKC9eaHR0cHM/OlwvXC90XC5tZVwvLywi"
    "QCIpLnNsaWNlKDAsMjYpLGhhbmRsZTp2LHJlcTpyZXEsb246dHJ1ZSxtZW1iZXJzOiLigJQifSk7"
    "CiAgICBpLnZhbHVlPSIiO3JlbmRlckFkbWluKCk7dG9hc3QoIkNoYW5uZWwgYWRkZWQiKTt9Owog"
    "IGNvbnN0IGxnYWRkPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJsZy1hZGQiKTsKICBpZihsZ2Fk"
    "ZClsZ2FkZC5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGNvbnN0IGk9ZG9jdW1lbnQuZ2V0RWxlbWVu"
    "dEJ5SWQoImxnLWluIiksdj0oaS52YWx1ZXx8IiIpLnRyaW0oKTsKICAgIGlmKCF2KXt0b2FzdCgi"
    "UGFzdGUgYSBsb2cgY2hhbm5lbCBmaXJzdCIpO2kuZm9jdXMoKTtyZXR1cm59CiAgICBpZihMSVZF"
    "KXsKICAgICAgbGdhZGQuZGlzYWJsZWQ9dHJ1ZTsKICAgICAgY29uc3Qgcj1hd2FpdCBhcGlUcnko"
    "Ii9hcGkvYWRtaW4vY2hhbm5lbCIse2FjdGlvbjoiYWRkIixoYW5kbGU6dixsb2c6dHJ1ZX0pOwog"
    "ICAgICBsZ2FkZC5kaXNhYmxlZD1mYWxzZTsKICAgICAgaWYoIXIpcmV0dXJuOwogICAgICBpLnZh"
    "bHVlPSIiO2F3YWl0IHJlZnJlc2goKTt0b2FzdCgiTG9nIGNoYW5uZWwgc2F2ZWQiKTtyZXR1cm47"
    "CiAgICB9CiAgICBMT0dDSC5wdXNoKHtpZDpEYXRlLm5vdygpLG5hbWU6di5yZXBsYWNlKC9eaHR0"
    "cHM/OlwvXC90XC5tZVwvLywiQCIpLnNsaWNlKDAsMjQpLGhhbmRsZTp2LHByaW1hcnk6TE9HQ0gu"
    "bGVuZ3RoPT09MCxvbjp0cnVlfSk7CiAgICBpLnZhbHVlPSIiO3JlbmRlckFkbWluKCk7dG9hc3Qo"
    "IkxvZyBjaGFubmVsIGFkZGVkIik7fTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0"
    "YS1jaGRlbF0iKS5mb3JFYWNoKGI9PmIub25jbGljaz1hc3luYygpPT57CiAgICBpZihMSVZFKXtj"
    "b25zdCByPWF3YWl0IGFwaVRyeSgiL2FwaS9hZG1pbi9jaGFubmVsIix7YWN0aW9uOiJkZWwiLGlk"
    "OitiLmRhdGFzZXQuY2hkZWx9KTsKICAgICAgaWYocil7YXdhaXQgcmVmcmVzaCgpO3RvYXN0KCJD"
    "aGFubmVsIHJlbW92ZWQiKTt9cmV0dXJuO30KICAgIENIQU5ORUxTPUNIQU5ORUxTLmZpbHRlcihj"
    "PT5jLmlkIT1iLmRhdGFzZXQuY2hkZWwpO3JlbmRlckFkbWluKCk7dG9hc3QoIkNoYW5uZWwgcmVt"
    "b3ZlZCIpfSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtY2hyZXFdIikuZm9y"
    "RWFjaChiPT5iLm9uY2xpY2s9YXN5bmMoKT0+ewogICAgaWYoTElWRSl7Y29uc3Qgcj1hd2FpdCBh"
    "cGlUcnkoIi9hcGkvYWRtaW4vY2hhbm5lbCIse2FjdGlvbjoicmVxdWlyZWQiLGlkOitiLmRhdGFz"
    "ZXQuY2hyZXF9KTsKICAgICAgaWYocil7YXdhaXQgcmVmcmVzaCgpO3RvYXN0KCJVcGRhdGVkIik7"
    "fXJldHVybjt9CiAgICBjb25zdCBjPUNIQU5ORUxTLmZpbmQoeD0+eC5pZD09Yi5kYXRhc2V0LmNo"
    "cmVxKTtjLnJlcT0hYy5yZXE7cmVuZGVyQWRtaW4oKTt0b2FzdChjLnJlcT8iTm93IHJlcXVpcmVk"
    "IjoiTm93IG9wdGlvbmFsIil9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1j"
    "aG9uXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPWFzeW5jKCk9PnsKICAgIGlmKExJVkUpe2NvbnN0"
    "IHI9YXdhaXQgYXBpVHJ5KCIvYXBpL2FkbWluL2NoYW5uZWwiLHthY3Rpb246InRvZ2dsZSIsaWQ6"
    "K2IuZGF0YXNldC5jaG9ufSk7CiAgICAgIGlmKHIpe2F3YWl0IHJlZnJlc2goKTt0b2FzdCgiVXBk"
    "YXRlZCIpO31yZXR1cm47fQogICAgY29uc3QgYz1DSEFOTkVMUy5maW5kKHg9PnguaWQ9PWIuZGF0"
    "YXNldC5jaG9uKTtjLm9uPSFjLm9uO3JlbmRlckFkbWluKCk7dG9hc3QoYy5vbj8iQ2hhbm5lbCBl"
    "bmFibGVkIjoiQ2hhbm5lbCBwYXVzZWQiKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwo"
    "IltkYXRhLWxnZGVsXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAgICBjb25zdCB3YXNQ"
    "PUxPR0NILmZpbmQobD0+bC5pZD09Yi5kYXRhc2V0LmxnZGVsKT8ucHJpbWFyeTsKICAgIExPR0NI"
    "PUxPR0NILmZpbHRlcihsPT5sLmlkIT1iLmRhdGFzZXQubGdkZWwpOwogICAgaWYod2FzUCYmTE9H"
    "Q0hbMF0pTE9HQ0hbMF0ucHJpbWFyeT10cnVlO3JlbmRlckFkbWluKCk7dG9hc3QoIkxvZyBjaGFu"
    "bmVsIHJlbW92ZWQiKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWxnc3Rh"
    "cl0iKS5mb3JFYWNoKGI9PmIub25jbGljaz0oKT0+ewogICAgTE9HQ0guZm9yRWFjaChsPT5sLnBy"
    "aW1hcnk9KGwuaWQ9PWIuZGF0YXNldC5sZ3N0YXIpKTtyZW5kZXJBZG1pbigpO3RvYXN0KCJQcmVm"
    "ZXJyZWQgbG9nIGNoYW5uZWwgc2V0Iil9KTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJb"
    "ZGF0YS1sZ29uXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57Y29uc3QgbD1MT0dDSC5maW5k"
    "KHg9PnguaWQ9PWIuZGF0YXNldC5sZ29uKTtsLm9uPSFsLm9uO3JlbmRlckFkbWluKCk7dG9hc3Qo"
    "bC5vbj8iQ2hhbm5lbCBvbiI6IkNoYW5uZWwgbXV0ZWQiKX0pOwogIGRvY3VtZW50LnF1ZXJ5U2Vs"
    "ZWN0b3JBbGwoIltkYXRhLWV2b25dIikuZm9yRWFjaChiPT5iLm9uY2xpY2s9KCk9Pntjb25zdCBl"
    "PUVWRU5UU1srYi5kYXRhc2V0LmV2b25dO2Uub249IWUub247cmVuZGVyQWRtaW4oKX0pOwogIGRv"
    "Y3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIltkYXRhLWV2Y2hdIikuZm9yRWFjaChzPT5zLm9uY2hh"
    "bmdlPSgpPT57RVZFTlRTWytzLmRhdGFzZXQuZXZjaF0uY2g9K3MudmFsdWU7dG9hc3QoIlJvdXRp"
    "bmcgdXBkYXRlZCIpfSk7CiAgY29uc3QgYWxsPWRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoIltkYXRh"
    "LWV2LWFsbF0iKTsKICBpZihhbGwpYWxsLm9uY2xpY2s9KCk9Pntjb25zdCB0PSFFVkVOVFMuZXZl"
    "cnkoZT0+ZS5vbik7RVZFTlRTLmZvckVhY2goZT0+ZS5vbj10KTtyZW5kZXJBZG1pbigpO3RvYXN0"
    "KHQ/IkFsbCBldmVudHMgb24iOiJBbGwgZXZlbnRzIG9mZiIpfTsKICBjb25zdCBtPWRvY3VtZW50"
    "LnF1ZXJ5U2VsZWN0b3IoIltkYXRhLW1hc3Rlcl0iKTsKICBpZihtKW0ub25jbGljaz0oKT0+e0xP"
    "R1NfTUFTVEVSPSFMT0dTX01BU1RFUjtyZW5kZXJBZG1pbigpO3RvYXN0KExPR1NfTUFTVEVSPyJM"
    "b2dnaW5nIGVuYWJsZWQiOiJBbGwgbG9nZ2luZyBkaXNhYmxlZCIpfTsKICBkb2N1bWVudC5xdWVy"
    "eVNlbGVjdG9yQWxsKCJbZGF0YS1hLWdvXSIpLmZvckVhY2goYj0+Yi5vbmNsaWNrPSgpPT57CiAg"
    "ICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCIjYXN1YiBidXR0b24iKS5mb3JFYWNoKHg9Pngu"
    "Y2xhc3NMaXN0LnRvZ2dsZSgib24iLHguZGF0YXNldC5hPT09Yi5kYXRhc2V0LmFHbykpOwogICAg"
    "QVRBQj1iLmRhdGFzZXQuYUdvO3JlbmRlckFkbWluKCk7d2luZG93LnNjcm9sbFRvKDAsMCk7fSk7"
    "CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2RhdGEtdGddIikuZm9yRWFjaCh0PT50Lm9u"
    "Y2xpY2s9KCk9PnQuY2xhc3NMaXN0LnRvZ2dsZSgib24iKSk7CiAgZG9jdW1lbnQucXVlcnlTZWxl"
    "Y3RvckFsbCgiW2RhdGEtc2V0c2VsXSIpLmZvckVhY2goc2VsPT5zZWwub25jaGFuZ2U9YXN5bmMo"
    "KT0+ewogICAgaWYoIUxJVkUpe3RvYXN0KCJQcmV2aWV3IG9ubHkiKTtyZXR1cm47fQogICAgY29u"
    "c3Qgcj1hd2FpdCBhcGlUcnkoIi9hcGkvYWRtaW4vc2V0dGluZyIse2tleTpzZWwuZGF0YXNldC5z"
    "ZXRzZWwsdmFsdWU6c2VsLnZhbHVlfSk7CiAgICBpZihyKXRvYXN0KCJTYXZlZCIpOwogIH0pOwp9"
    "Cgpjb25zdCBfY2M9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNhcnRjaGlwIik7CmlmKF9jYylf"
    "Y2Mub25jbGljaz1jYXJ0U2hlZXQ7CnJlbmRlcigpO3JlbmRlckFkbWluKCk7YXBwbHlGZWF0dXJl"
    "cygpOwoKLyog4pSA4pSAIGxpdmUgZGF0YTogZXZlcnl0aGluZyBiZWxvdyBjb21lcyBmcm9tIHRo"
    "ZSBib3QgZGF0YWJhc2Ugd2hlbiBzZXJ2ZWQgYnkgdGhlIGJvdCDilIDilIAgKi8KLyogTElWRSAv"
    "IE1FIC8gVEcgLyBJTklUIGFyZSBkZWNsYXJlZCBhdCB0aGUgdmVyeSB0b3Agb2YgdGhpcyBzY3Jp"
    "cHQgKi8KYXN5bmMgZnVuY3Rpb24gYXBpKHBhdGgsYm9keSl7CiAgY29uc3Qgbz17aGVhZGVyczp7"
    "IkNvbnRlbnQtVHlwZSI6ImFwcGxpY2F0aW9uL2pzb24iLCJYLUluaXQtRGF0YSI6SU5JVCwKICAg"
    "ICJYLVRlbGVncmFtLUluaXQtRGF0YSI6SU5JVCwiWC1BcHAtS2V5IjpBUFBLRVl9fTsKICBpZihi"
    "b2R5IT09dW5kZWZpbmVkKXtvLm1ldGhvZD0iUE9TVCI7by5ib2R5PUpTT04uc3RyaW5naWZ5KGJv"
    "ZHkpO30KICBjb25zdCByPWF3YWl0IGZldGNoKHBhdGgsbyk7CiAgbGV0IGQ9bnVsbDt0cnl7ZD1h"
    "d2FpdCByLmpzb24oKTt9Y2F0Y2goZXJyKXt9CiAgaWYoIXIub2spe2NvbnN0IGU9bmV3IEVycm9y"
    "KChkJiZkLmVycm9yKXx8KHBhdGgrIiAiK3Iuc3RhdHVzKSk7ZS5zdGF0dXM9ci5zdGF0dXM7ZS5k"
    "YXRhPWQ7dGhyb3cgZTt9CiAgcmV0dXJuIGQ7Cn0KZnVuY3Rpb24gYWRtaW5BbGxvd2VkKCl7cmV0"
    "dXJuICFMSVZFIHx8ICEhKE1FJiZNRS5hZG1pbik7fQpmdW5jdGlvbiBhcHBseVN0YXRlKGQpewog"
    "IC8qIGV2ZXJ5IGxpc3QgaXMgcmVwbGFjZWQsIGV2ZW4gd2hlbiBlbXB0eSDigJQgYSBuZXcgc2hv"
    "cCBtdXN0IG5ldmVyIHNob3cgZGVtbyBkYXRhICovCiAgaWYoZC5mZWF0dXJlcylPYmplY3Qua2V5"
    "cyhkLmZlYXR1cmVzKS5mb3JFYWNoKGs9PntpZihrIGluIEZFQVQpRkVBVFtrXT0hIWQuZmVhdHVy"
    "ZXNba107fSk7CiAgaWYoZC5jb250ZW50KU9iamVjdC5rZXlzKGQuY29udGVudCkuZm9yRWFjaChr"
    "PT57aWYoayBpbiBDTVMmJmQuY29udGVudFtrXSE9bnVsbClDTVNba109ZC5jb250ZW50W2tdO30p"
    "OwogIGlmKEFycmF5LmlzQXJyYXkoZC5zdG9jaykpe1NUT0NLLmxlbmd0aD0wO2Quc3RvY2suZm9y"
    "RWFjaCh4PT5TVE9DSy5wdXNoKHgpKTtGSUxURVJFRD1TVE9DSy5zbGljZSgpO30KICBpZihBcnJh"
    "eS5pc0FycmF5KGQub3JkZXJzKSl7T1JERVJTLmxlbmd0aD0wO2Qub3JkZXJzLmZvckVhY2goeD0+"
    "T1JERVJTLnB1c2goeCkpO30KICBpZihBcnJheS5pc0FycmF5KGQudHgpKXtUWC5sZW5ndGg9MDtk"
    "LnR4LmZvckVhY2goeD0+VFgucHVzaCh4KSk7fQogIGlmKEFycmF5LmlzQXJyYXkoZC5mZWVkKSlG"
    "RUVEPWQuZmVlZDsKICBpZihkLmVhcm4pRUFSTj1PYmplY3QuYXNzaWduKHt9LEVBUk4sZC5lYXJu"
    "KTsKICBpZihkLnByZWZzKVBSRUZTPU9iamVjdC5hc3NpZ24oe30sUFJFRlMsZC5wcmVmcyk7CiAg"
    "aWYoQXJyYXkuaXNBcnJheShkLmFsZXJ0cykpd2luZG93Ll9fYWxlcnRzPWQuYWxlcnRzOwogIGlm"
    "KGQuc3RvcmUpewogICAgU1RPUkU9T2JqZWN0LmFzc2lnbih7fSxTVE9SRSxkLnN0b3JlKTsKICAg"
    "IGlmKGQuc3RvcmUubmFtZSlDRkcubmFtZT1kLnN0b3JlLm5hbWU7CiAgICBpZihkLnN0b3JlLnRh"
    "ZylDRkcudGFnPWQuc3RvcmUudGFnOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgiW2Rh"
    "dGEtc3RvcmVuYW1lXSIpLmZvckVhY2goZT0+ZS50ZXh0Q29udGVudD1DRkcubmFtZSk7CiAgICBk"
    "b2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCJbZGF0YS1zdG9yZXRhZ10iKS5mb3JFYWNoKGU9PmUu"
    "dGV4dENvbnRlbnQ9Q0ZHLnRhZyk7CiAgICBkb2N1bWVudC50aXRsZT1DRkcubmFtZTsKICB9CiAg"
    "aWYoZC5hZG0pewogICAgQURNPU9iamVjdC5hc3NpZ24oe30sQURNLGQuYWRtKTsKICAgIGlmKEFy"
    "cmF5LmlzQXJyYXkoQURNLmNoYW5uZWxzKSkKICAgICAgQ0hBTk5FTFM9QURNLmNoYW5uZWxzLm1h"
    "cChjPT4oe2lkOmMuaWQsbmFtZTpjLm5hbWUsaGFuZGxlOmMuaGFuZGxlfHwiIiwKICAgICAgICBy"
    "ZXE6ISFjLnJlcXVpcmVkLG9uOiEhYy5hY3RpdmUsbWVtYmVyczoi4oCUIn0pKTsKICAgIGlmKEFE"
    "TS5sb2dzKXsKICAgICAgTE9HU19NQVNURVI9ISFBRE0ubG9ncy5tYXN0ZXI7CiAgICAgIGlmKEFy"
    "cmF5LmlzQXJyYXkoQURNLmxvZ3MuY2hhbm5lbHMpKQogICAgICAgIExPR0NIPUFETS5sb2dzLmNo"
    "YW5uZWxzLm1hcChjPT4oe2lkOmMuaWQsbmFtZTpjLm5hbWUsaGFuZGxlOiIiLAogICAgICAgICAg"
    "cHJpbWFyeTohIWMucHJpbWFyeSxvbjohIWMuYWN0aXZlfSkpOwogICAgICBpZihBcnJheS5pc0Fy"
    "cmF5KEFETS5sb2dzLmV2ZW50cykmJkFETS5sb2dzLmV2ZW50cy5sZW5ndGgpCiAgICAgICAgRVZF"
    "TlRTPUFETS5sb2dzLmV2ZW50cy5tYXAoZT0+KHtrOmUubGFiZWwsb246ISFlLm9uLGNoOihMT0dD"
    "SFswXXx8e30pLmlkfHwxfSkpOwogICAgfQogIH0KICBpZihkLnJhdGluZ3MpUkVWSUVXUz1kLnJh"
    "dGluZ3M7CiAgaWYodHlwZW9mIGQuYmFsYW5jZT09PSJudW1iZXIiKUJBTD1kLmJhbGFuY2U7CiAg"
    "aWYoZC5tZSlNRT1kLm1lOwogIGlmKGQuY2FydClDQVJUPWQuY2FydDsKICBpZihkLndpc2hsaXN0"
    "KVdJU0g9ZC53aXNobGlzdDsKfQpmdW5jdGlvbiBzaG93U3dpdGNoKG9uKXsKICBjb25zdCBzdz1k"
    "b2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3ciKTsKICBpZihzdylzdy5zdHlsZS5kaXNwbGF5PW9u"
    "PyJmbGV4Ijoibm9uZSI7Cn0KdmFyIExPQ0tfVElUTEU9IiIsTE9DS19NU0c9IiI7CmZ1bmN0aW9u"
    "IGxvY2tlZFNjcmVlbih0aXRsZSxtc2cpewogIExPQ0tFRD10cnVlO0xPQ0tfVElUTEU9dGl0bGU7"
    "TE9DS19NU0c9bXNnOwogIHNob3dTd2l0Y2goZmFsc2UpOwogIHBhaW50TG9ja2VkKCk7Cn0KZnVu"
    "Y3Rpb24gcGFpbnRMb2NrZWQoKXsKICBjb25zdCBzdD1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgi"
    "c3RhZ2UiKTsKICBpZihzdClzdC5pbm5lckhUTUw9JzxkaXYgY2xhc3M9ImNhcmQiIHN0eWxlPSJt"
    "YXJnaW46MThweDt0ZXh0LWFsaWduOmNlbnRlcjtwYWRkaW5nOjIycHgiPicKICAgICsnPGgzIHN0"
    "eWxlPSJtYXJnaW46MCAwIDhweCI+JytMT0NLX1RJVExFKyc8L2gzPicKICAgICsnPHAgY2xhc3M9"
    "InN1YiIgc3R5bGU9Im1hcmdpbjowIDAgMTRweCI+JytMT0NLX01TRysnPC9wPicKICAgICsnPGJ1"
    "dHRvbiBjbGFzcz0iYnRuIGJsb2NrIiBvbmNsaWNrPSJsb2NhdGlvbi5yZWxvYWQoKSI+VHJ5IGFn"
    "YWluPC9idXR0b24+PC9kaXY+JzsKICBbInRhYnMiLCJkb2NrIl0uZm9yRWFjaChpZD0+e2NvbnN0"
    "IGU9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoaWQpO2lmKGUpZS5zdHlsZS5kaXNwbGF5PSJub25l"
    "Ijt9KTsKfQphc3luYyBmdW5jdGlvbiBhcGlUcnkocGF0aCxib2R5KXsKICB0cnl7cmV0dXJuIGF3"
    "YWl0IGFwaShwYXRoLGJvZHkpO30KICBjYXRjaChlKXt0b2FzdChTdHJpbmcoZSYmZS5tZXNzYWdl"
    "fHxlKS5zbGljZSgwLDkwKSk7cmV0dXJuIG51bGw7fQp9CmFzeW5jIGZ1bmN0aW9uIHJlZnJlc2go"
    "KXsKICBpZighTElWRSlyZXR1cm47CiAgdHJ5ewogICAgYXBwbHlTdGF0ZShhd2FpdCBhcGkoIi9h"
    "cGkvc3RhdGUiKSk7CiAgICBhcHBseUNNUygpO2FwcGx5RmVhdHVyZXMoKTtzeW5jQmFsKCk7cmVu"
    "ZGVyKCk7cmVuZGVyQWRtaW4oKTsKICB9Y2F0Y2goZSl7fQp9CmFzeW5jIGZ1bmN0aW9uIGNvcHlU"
    "ZXh0KHQsIGxhYmVsKXsKICBjb25zdCB0eHQ9U3RyaW5nKHR8fCIiKTsKICBpZighdHh0KXt0b2Fz"
    "dCgiTm90aGluZyB0byBjb3B5Iik7cmV0dXJuIGZhbHNlO30KICB0cnl7CiAgICBpZihuYXZpZ2F0"
    "b3IuY2xpcGJvYXJkJiZ3aW5kb3cuaXNTZWN1cmVDb250ZXh0KXthd2FpdCBuYXZpZ2F0b3IuY2xp"
    "cGJvYXJkLndyaXRlVGV4dCh0eHQpO30KICAgIGVsc2V7CiAgICAgIGNvbnN0IHRhPWRvY3VtZW50"
    "LmNyZWF0ZUVsZW1lbnQoInRleHRhcmVhIik7CiAgICAgIHRhLnZhbHVlPXR4dDt0YS5zZXRBdHRy"
    "aWJ1dGUoInJlYWRvbmx5IiwiIik7CiAgICAgIHRhLnN0eWxlLmNzc1RleHQ9InBvc2l0aW9uOmZp"
    "eGVkO2xlZnQ6LTk5OTlweDtvcGFjaXR5OjAiOwogICAgICBkb2N1bWVudC5ib2R5LmFwcGVuZENo"
    "aWxkKHRhKTt0YS5zZWxlY3QoKTt0YS5zZXRTZWxlY3Rpb25SYW5nZSgwLHR4dC5sZW5ndGgpOwog"
    "ICAgICBjb25zdCBvaz1kb2N1bWVudC5leGVjQ29tbWFuZCgiY29weSIpO2RvY3VtZW50LmJvZHku"
    "cmVtb3ZlQ2hpbGQodGEpOwogICAgICBpZighb2spdGhyb3cgbmV3IEVycm9yKCJjb3B5IHJlZnVz"
    "ZWQiKTsKICAgIH0KICAgIHRvYXN0KChsYWJlbHx8IkNvcGllZCIpKyI6ICIrKHR4dC5sZW5ndGg+"
    "MjY/dHh0LnNsaWNlKDAsMjYpKyLigKYiOnR4dCkpOwogICAgcmV0dXJuIHRydWU7CiAgfWNhdGNo"
    "KGUpewogICAgc2hlZXQoYDxoMyBzdHlsZT0ibWFyZ2luOjAgMCA0cHg7Zm9udC1zaXplOjFyZW0i"
    "PkNvcHkgdGhpczwvaDM+CiAgICAgIDxwIGNsYXNzPSJzdWIiPllvdXIgYnJvd3NlciBibG9ja2Vk"
    "IHRoZSBjbGlwYm9hcmQg4oCUIHNlbGVjdCBhbmQgY29weS48L3A+CiAgICAgIDx0ZXh0YXJlYSBj"
    "bGFzcz0iaW5wIiByb3dzPSIzIiByZWFkb25seSBvbmNsaWNrPSJ0aGlzLnNlbGVjdCgpIj4ke2Vz"
    "Yyh0eHQpfTwvdGV4dGFyZWE+CiAgICAgIDxidXR0b24gY2xhc3M9ImJ0biBibG9jayIgc3R5bGU9"
    "Im1hcmdpbi10b3A6MTBweCIgZGF0YS1jbG9zZT5Eb25lPC9idXR0b24+YCk7CiAgICByZXR1cm4g"
    "ZmFsc2U7CiAgfQp9CmxldCBTVE9SRT17dXBpOiIiLHBheWVlOiIiLHByZWZpeDoiUEFZIix0ZXJt"
    "czoiIixzdXBwb3J0OiIifTsKbGV0IFBSRUZTPXtyZXN0b2NrOnRydWUscmVjZWlwdHM6dHJ1ZSxw"
    "cm9tb3M6dHJ1ZSxwdXNoOnRydWV9OwpsZXQgT0ZGRVI9IiI7CmZ1bmN0aW9uIG9wZW5QYXkobGlu"
    "ayl7CiAgaWYoIWxpbmspcmV0dXJuOwogIHRyeXsKICAgIGlmKFRHJiZURy5vcGVuTGluayYmL15o"
    "dHRwcz86Ly50ZXN0KGxpbmspKVRHLm9wZW5MaW5rKGxpbmspOwogICAgZWxzZSB3aW5kb3cubG9j"
    "YXRpb24uaHJlZj1saW5rOwogIH1jYXRjaChlKXt3aW5kb3cubG9jYXRpb24uaHJlZj1saW5rO30K"
    "fQphc3luYyBmdW5jdGlvbiBib290KCl7CiAgaWYoVEcpe3RyeXtURy5yZWFkeSgpO1RHLmV4cGFu"
    "ZCgpO31jYXRjaChlKXt9fQogIGNvbnN0IGluVEc9ISEoKElOSVQmJklOSVQubGVuZ3RoKXx8KEFQ"
    "UEtFWSYmQVBQS0VZLmxlbmd0aCkpOwogIGlmKGluVEcpc2hvd1N3aXRjaChmYWxzZSk7ICAgICAg"
    "ICAgICAgICAvKiBmYWlsIGNsb3NlZDogbm8gYWRtaW4gc3RyaXAgdW50aWwgcHJvdmVuICovCiAg"
    "aWYobG9jYXRpb24ucHJvdG9jb2w9PT0iZmlsZToiKXJldHVybjsgIC8qIG9wZW5lZCBhcyBhIHBs"
    "YWluIGZpbGUg4oCUIGRlbW8gZ2FsbGVyeSAqLwogIGxldCBkOwogIHRyeXsKICAgIGQ9YXdhaXQg"
    "YXBpKCIvYXBpL3N0YXRlIik7CiAgfWNhdGNoKGUpewogICAgaWYoZSYmZS5zdGF0dXM9PT00MDEp"
    "ewogICAgICBsZXQgd2h5PSIiLGZpeD0iIjsKICAgICAgdHJ5ewogICAgICAgIGNvbnN0IHc9YXdh"
    "aXQgKGF3YWl0IGZldGNoKCIvYXBpL3dob2FtaSIse2hlYWRlcnM6eyJYLUluaXQtRGF0YSI6SU5J"
    "VCwKICAgICAgICAgICJYLVRlbGVncmFtLUluaXQtRGF0YSI6SU5JVCwiWC1BcHAtS2V5IjpBUFBL"
    "RVl9fSkpLmpzb24oKTsKICAgICAgICBjb25zdCBSPXttaXNzaW5nOlsiVGVsZWdyYW0gZGlkIG5v"
    "dCBwYXNzIHlvdXIgYWNjb3VudCB0byB0aGlzIHBhZ2UuIiwKICAgICAgICAgICAgICAgICAgICJP"
    "cGVuIHRoZSBib3QgYW5kIHRhcCA8Yj5PcGVuIFN0b3JlPC9iPiwgb3Igc2VuZCA8Yj4vYXBwPC9i"
    "Pi4iXSwKICAgICAgICAgICAgICAgICAiYmFkLWZvcm1hdCI6WyJUaGUgc2lnbi1pbiBkYXRhIGFy"
    "cml2ZWQgZGFtYWdlZC4iLCJDbG9zZSB0aGUgYXBwIGFuZCBvcGVuIGl0IGFnYWluIGZyb20gdGhl"
    "IGJvdC4iXSwKICAgICAgICAgICAgICAgICAibm8taGFzaCI6WyJUaGUgc2lnbi1pbiBkYXRhIGFy"
    "cml2ZWQgaW5jb21wbGV0ZS4iLCJDbG9zZSB0aGUgYXBwIGFuZCBvcGVuIGl0IGFnYWluIGZyb20g"
    "dGhlIGJvdC4iXSwKICAgICAgICAgICAgICAgICAiYmFkLWhhc2giOlsiVGhlIHNpZ25hdHVyZSBk"
    "b2VzIG5vdCBtYXRjaCB0aGlzIGJvdCdzIHRva2VuLiIsCiAgICAgICAgICAgICAgICAgICAiVGhl"
    "IHBhZ2UgaXMgc2VydmVkIGJ5IGEgZGlmZmVyZW50IGJvdCwgb3IgQk9UX1RPS0VOIG9uIHRoZSBz"
    "ZXJ2ZXIgaGFzIHF1b3RlcyBhcm91bmQgaXQuIFNlbmQgPGI+L2RpYWc8L2I+IGluIHRoZSBib3Qg"
    "dG8gY29tcGFyZS4iXSwKICAgICAgICAgICAgICAgICBzdGFsZTpbIlRoYXQgc2lnbi1pbiBpcyBv"
    "bGRlciB0aGFuIHRoZSBhbGxvd2VkIHdpbmRvdy4iLCJSZW9wZW4gdGhlIGFwcCBmcm9tIHRoZSBi"
    "b3QuIl0sCiAgICAgICAgICAgICAgICAgImJhZC11c2VyIjpbIllvdXIgYWNjb3VudCBkZXRhaWxz"
    "IGNvdWxkIG5vdCBiZSByZWFkLiIsIlJlb3BlbiB0aGUgYXBwIGZyb20gdGhlIGJvdC4iXSwKICAg"
    "ICAgICAgICAgICAgICAibm8taWQiOlsiVGVsZWdyYW0gc2VudCBubyB1c2VyIGlkLiIsIlJlb3Bl"
    "biB0aGUgYXBwIGZyb20gdGhlIGJvdC4iXX07CiAgICAgICAgY29uc3Qgcj1SW3cud2h5XXx8WyJU"
    "aGUgc3RvcmUgY291bGQgbm90IGNvbmZpcm0gd2hvIHlvdSBhcmUuIiwiT3BlbiBpdCBhZ2FpbiBm"
    "cm9tIHRoZSBib3QuIl07CiAgICAgICAgd2h5PXJbMF0rIiA8Y29kZT4oIisody53aHl8fCJ1bmtu"
    "b3duIikrIik8L2NvZGU+IjsKICAgICAgICBmaXg9clsxXTsKICAgICAgfWNhdGNoKGVycil7CiAg"
    "ICAgICAgd2h5PSJUaGUgc3RvcmUgY291bGQgbm90IGNvbmZpcm0gd2hvIHlvdSBhcmUuIjsKICAg"
    "ICAgICBmaXg9Ik9wZW4gaXQgYWdhaW4gZnJvbSB0aGUgYm90IHdpdGggdGhlIDxiPk9wZW4gU3Rv"
    "cmU8L2I+IGJ1dHRvbi4iOwogICAgICB9CiAgICAgIGxvY2tlZFNjcmVlbigiQ2Fubm90IG9wZW4g"
    "dGhlIHN0b3JlIix3aHkrIjxicj48YnI+IitmaXgpOwogICAgfQogICAgcmV0dXJuOwogIH0KICBh"
    "cHBseVN0YXRlKGQpO0xJVkU9dHJ1ZTsKICBzaG93U3dpdGNoKCEhKE1FJiZNRS5hZG1pbikpOyAg"
    "ICAgICAgICAgLyogb25seSBhIHZlcmlmaWVkIGFkbWluIHNlZXMgaXQgKi8KICBhcHBseUNNUygp"
    "O2FwcGx5RmVhdHVyZXMoKTtzeW5jQmFsKCk7cmVuZGVyKCk7cmVuZGVyQWRtaW4oKTsKfQphc3lu"
    "YyBmdW5jdGlvbiBwdXNoKHBhdGgsYm9keSxva01zZyl7CiAgaWYoIUxJVkUpcmV0dXJuIHRydWU7"
    "CiAgdHJ5e2NvbnN0IHI9YXdhaXQgYXBpKHBhdGgsYm9keSk7aWYob2tNc2cpdG9hc3Qob2tNc2cp"
    "O3JldHVybiByO30KICBjYXRjaChlKXt0b2FzdCgiQ291bGQgbm90IHNhdmUg4oCUIGNoZWNrIHlv"
    "dXIgY29ubmVjdGlvbiIpO3JldHVybiBmYWxzZTt9Cn0KYm9vdCgpOwo8L3NjcmlwdD4KPC9ib2R5"
    "PjwvaHRtbD4K"
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
