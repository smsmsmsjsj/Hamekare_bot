# requirements.txt  ->  python-telegram-bot==22.*
#
# متغیرهای محیطی:
#   BOT_TOKEN       توکن ربات (از BotFather)
#   OWNER_ID        آیدی عددی خودت (از @userinfobot)
#   TZ_OFFSET_MIN   اختیاری، اختلاف ساعت به دقیقه (ایران: 210)
#   DATA_DIR        اختیاری، پوشه دیتابیس
import ast
import asyncio
import html
import logging
import math
import operator
import os
import random
import re
import secrets
import sqlite3
import string
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict, NetworkError, RetryAfter
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ============================== تنظیمات ==============================
def env_required(name):
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"متغیر محیطی {name} تنظیم نشده است!")
    return v


TOKEN = env_required("BOT_TOKEN")
OWNER_ID = int(env_required("OWNER_ID"))
DATA_DIR = os.environ.get("DATA_DIR", ".")
PORT = os.environ.get("PORT")
TZ = timezone(timedelta(minutes=int(os.environ.get("TZ_OFFSET_MIN", "210"))))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "assistant.db")
HTML = ParseMode.HTML

BTN_NOTES = "📝 یادداشت‌ها"
BTN_TODO = "✅ کارها"
BTN_REM = "⏰ یادآورها"
BTN_EXP = "💸 هزینه‌ها"
BTN_TOOLS = "🧰 ابزارها"
BTN_TODAY = "📅 تاریخ امروز"
BTN_HELP = "ℹ️ راهنما"
BTN_ADMIN = "🛠 پنل مدیر"
MENU_BTNS = {BTN_NOTES, BTN_TODO, BTN_REM, BTN_EXP, BTN_TOOLS, BTN_TODAY, BTN_HELP, BTN_ADMIN}

CATEGORIES = ["🍔 خوراک", "🚕 رفت‌وآمد", "🏠 خانه", "🛍 خرید",
              "💊 سلامت", "🎮 تفریح", "📚 آموزش", "📦 سایر"]
JMONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
           "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]

WHEN_HELP = ("نمونه‌ها:\n"
             "• 30m یا 2h یا 1d (دقیقه/ساعت/روز بعد)\n"
             "• 90 دقیقه ، 2 ساعت ، 3 روز\n"
             "• 14:30 (امروز یا فردا اگر گذشته باشد)\n"
             "• فردا 09:00\n"
             "• 1405/09/01 18:00 (شمسی)\n"
             "• 2026-12-01 18:00 (میلادی)")

HELP_TEXT = (
    "ℹ️ <b>راهنمای دستیار</b>\n\n"
    "📝 <b>یادداشت‌ها:</b> ذخیره، جستجو و حذف یادداشت\n"
    "✅ <b>کارها:</b> لیست کار؛ هر خط یک کار. روی کار بزنید تا انجام‌شده شود\n"
    "⏰ <b>یادآورها:</b> یادآوری یک‌بار، روزانه یا هفتگی\n"
    "💸 <b>هزینه‌ها:</b> ثبت هزینه و گزارش روزانه، هفتگی و ماهانه (مبلغ را می‌توانید 50k یا 2m هم بنویسید)\n"
    "🧰 <b>ابزارها:</b> ماشین‌حساب، ساخت رمز قوی، تبدیل تاریخ شمسی/میلادی، شمارش متن\n\n"
    "دستورها: /today /calc عبارت /cancel /id\n\n" + WHEN_HELP)

ADMIN_HELP = (
    "🛠 <b>دستورات مدیر</b>\n\n"
    "/stats — آمار\n"
    "/broadcast متن (یا ریپلای) — پیام همگانی\n"
    "/ban ID و /unban ID\n"
    "/addchannel @channel — افزودن کانال (ربات باید ادمین باشد)\n"
    "/delchannel @channel\n/channels — لیست کانال‌ها\n"
    "/newpost — ساخت پست زمان‌بندی‌شده برای کانال\n"
    "/posts — پست‌های زمان‌بندی‌شده و لغو")


# ============================== ابزارهای کوچک ==============================
def esc(s):
    return html.escape(str(s), quote=False)


def fmt(n):
    return f"{int(n):,}"


_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(s):
    return s.translate(_DIGITS)


_LAST = {}


def throttled(key, seconds):
    now = time.time()
    if now - _LAST.get(key, 0) < seconds:
        return True
    _LAST[key] = now
    return False


def is_owner(uid):
    return uid == OWNER_ID


def now_local():
    return datetime.now(TZ)


def bar(p, width=10):
    n = round(p * width)
    return "█" * n + "░" * (width - n)


def page_slice(items, page, per=8):
    pages = max((len(items) + per - 1) // per, 1)
    page = min(max(page, 0), pages - 1)
    return items[page * per:(page + 1) * per], page, pages


def nav_row(prefix, page, pages):
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"{prefix}{page - 1}"))
    if page < pages - 1:
        row.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"{prefix}{page + 1}"))
    return row


# ============================== تاریخ شمسی ==============================
def g2j(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100)
            + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1])
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def j2g(jy, jm, jd):
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    sal_a = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 12 and gd > sal_a[gm + 1 if False else gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def fmt_dt(ts):
    d = datetime.fromtimestamp(ts, TZ)
    jy, jm, jd = g2j(d.year, d.month, d.day)
    return f"{jy}/{jm:02d}/{jd:02d} {d:%H:%M} ({WEEKDAYS[d.weekday()]})"


def today_text():
    d = now_local()
    jy, jm, jd = g2j(d.year, d.month, d.day)
    return (f"📅 <b>تاریخ امروز</b>\n\n"
            f"🗓 شمسی: {WEEKDAYS[d.weekday()]} {jd} {JMONTHS[jm - 1]} {jy}\n"
            f"🌍 میلادی: {d:%Y-%m-%d}\n"
            f"🕒 ساعت: {d:%H:%M:%S}")


def month_start_ts():
    n = now_local()
    jy, jm, _ = g2j(n.year, n.month, n.day)
    gy, gm, gd = j2g(jy, jm, 1)
    return datetime(gy, gm, gd, tzinfo=TZ).timestamp()


def day_start_ts():
    return now_local().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def parse_when(text):
    t = normalize_digits(text).strip().lower()
    now = now_local()
    if t in ("الان", "now"):
        return time.time()
    m = re.fullmatch(r"(\d+)\s*(m|min|دقیقه|h|ساعت|d|روز)", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        mult = 60 if unit in ("m", "min", "دقیقه") else 3600 if unit in ("h", "ساعت") else 86400
        if n <= 0 or n * mult > 366 * 86400:
            return None
        return time.time() + n * mult
    tomorrow = False
    for w in ("فردا", "tomorrow"):
        if t.startswith(w):
            tomorrow = True
            t = t[len(w):].strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh > 23 or mm > 59:
            return None
        dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if tomorrow or dt <= now:
            dt += timedelta(days=1)
        return dt.timestamp()
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh, mm = int(m.group(4) or 9), int(m.group(5) or 0)
        try:
            if y < 1700:
                if not (1 <= mo <= 12 and 1 <= d <= (31 if mo <= 6 else 30)):
                    return None
                y, mo, d = j2g(y, mo, d)
            return datetime(y, mo, d, hh, mm, tzinfo=TZ).timestamp()
        except ValueError:
            return None
    return None


def convert_date(text):
    t = normalize_digits(text).strip()
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", t)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        if y < 1700:
            if not (1 <= mo <= 12 and 1 <= d <= (31 if mo <= 6 else 30)):
                return None
            gy, gm, gd = j2g(y, mo, d)
            wd = WEEKDAYS[datetime(gy, gm, gd).weekday()]
            return f"🗓 شمسی: {y}/{mo:02d}/{d:02d}\n🌍 میلادی: {gy}-{gm:02d}-{gd:02d}\n📆 {wd}"
        wd = WEEKDAYS[datetime(y, mo, d).weekday()]
        jy, jm, jd = g2j(y, mo, d)
        return f"🌍 میلادی: {y}-{mo:02d}-{d:02d}\n🗓 شمسی: {jy}/{jm:02d}/{jd:02d} ({jd} {JMONTHS[jm - 1]})\n📆 {wd}"
    except ValueError:
        return None


# ============================== ماشین‌حساب و ابزارها ==============================
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
          "log": math.log, "log10": math.log10, "abs": abs, "round": round}
_CONSTS = {"pi": math.pi, "e": math.e}


def _ev(node):
    if isinstance(node, ast.Expression):
        return _ev(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _ev(node.left), _ev(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
            raise ValueError("big")
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_ev(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in _FUNCS and not node.keywords and len(node.args) <= 2:
        return _FUNCS[node.func.id](*[_ev(a) for a in node.args])
    raise ValueError("bad")


def calc_value(expr):
    e = (normalize_digits(expr).replace("×", "*").replace("÷", "/")
         .replace("^", "**").replace("٫", "."))
    if len(e) > 200:
        raise ValueError("long")
    v = _ev(ast.parse(e, mode="eval"))
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            raise ValueError("nan")
        v = int(v) if v == int(v) and abs(v) < 1e15 else round(v, 10)
    return v


def make_password(n):
    symbols = "!@#$%&*?"
    alphabet = string.ascii_letters + string.digits + symbols
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(n))
        if (any(c.islower() for c in p) and any(c.isupper() for c in p)
                and any(c.isdigit() for c in p) and any(c in symbols for c in p)):
            return p


def parse_amount(text):
    t = (normalize_digits(text).strip().lower()
         .replace(",", "").replace("٬", "").replace("،", ""))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(k|هزار|m|میلیون)?", t)
    if not m:
        return None
    n = float(m.group(1))
    if m.group(2) in ("k", "هزار"):
        n *= 1_000
    elif m.group(2) in ("m", "میلیون"):
        n *= 1_000_000
    n = int(round(n))
    return n if 0 < n <= 10 ** 12 else None


# ============================== دیتابیس ==============================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, name TEXT, joined_at REAL, banned INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);
CREATE TABLE IF NOT EXISTS todos(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_todos_user ON todos(user_id);
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL, due REAL NOT NULL, rep TEXT, sent INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_rem_due ON reminders(sent, due);
CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, category TEXT NOT NULL, note TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_exp_user ON expenses(user_id, created_at);
CREATE TABLE IF NOT EXISTS channels(chat_id INTEGER PRIMARY KEY, title TEXT);
CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY AUTOINCREMENT, src_chat INTEGER NOT NULL, src_msg INTEGER NOT NULL, target INTEGER NOT NULL, btn_text TEXT, btn_url TEXT, due REAL NOT NULL, status TEXT NOT NULL, error TEXT, created_at REAL NOT NULL);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def db(sql, params=(), fetch=None):
    with conn() as c:
        cur = c.execute(sql, params)
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        if sql.lstrip().upper().startswith("INSERT"):
            return cur.lastrowid
        return cur.rowcount


def total_since(uid, since):
    return db("SELECT COALESCE(SUM(amount),0) s FROM expenses WHERE user_id=? AND created_at>=?",
              (uid, since), "one")["s"]


def expense_report(uid, since, title):
    rows = db("SELECT category, SUM(amount) s FROM expenses WHERE user_id=? AND created_at>=? "
              "GROUP BY category ORDER BY s DESC", (uid, since), "all")
    if not rows:
        return f"<b>{title}</b>\n\nهنوز هزینه‌ای ثبت نشده."
    total = sum(r["s"] for r in rows)
    lines = [f"<b>{title}</b>", "", f"💰 مجموع: <b>{fmt(total)}</b> تومان", ""]
    for r in rows:
        p = r["s"] / total
        lines.append(f"{esc(r['category'])}\n{bar(p)} {p * 100:.0f}٪ — {fmt(r['s'])}")
    return "\n".join(lines)


# ============================== ارسال و پاسخ ==============================
def main_kb(uid):
    rows = [[BTN_NOTES, BTN_TODO], [BTN_REM, BTN_EXP], [BTN_TOOLS, BTN_TODAY], [BTN_HELP]]
    if is_owner(uid):
        rows[-1].append(BTN_ADMIN)
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def respond(update, text, markup=None, parse_mode=HTML):
    q = update.callback_query
    if q:
        try:
            await q.edit_message_text(text, parse_mode=parse_mode, reply_markup=markup)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
        except Exception:
            pass
        await q.message.reply_text(text, parse_mode=parse_mode, reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=markup)


async def safe_send(bot, chat_id, text, **kw):
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kw)
        return True
    except Exception as e:
        logger.warning(f"ارسال به {chat_id} ناموفق: {e}")
        return False


async def ensure_user(update, context):
    u = update.effective_user
    if not u:
        return False
    db("INSERT OR IGNORE INTO users(user_id, name, joined_at) VALUES(?,?,?)",
       (u.id, u.full_name, time.time()))
    row = db("SELECT banned FROM users WHERE user_id=?", (u.id,), "one")
    if row and row["banned"] and not is_owner(u.id):
        t = update.message or (update.callback_query.message if update.callback_query else None)
        if t:
            await t.reply_text("⛔️ دسترسی شما مسدود شده است.")
        return False
    return True


# ============================== صفحه‌های اصلی ==============================
async def show_notes(update, page=0):
    uid = update.effective_user.id
    rows = db("SELECT id, text FROM notes WHERE user_id=? ORDER BY id DESC", (uid,), "all")
    chunk, page, pages = page_slice(rows, page)
    kb = [[InlineKeyboardButton("➕ یادداشت جدید", callback_data="nn"),
           InlineKeyboardButton("🔎 جستجو", callback_data="nf")]]
    for r in chunk:
        kb.append([InlineKeyboardButton("📄 " + r["text"].replace("\n", " ")[:32],
                                        callback_data=f"nv_{r['id']}")])
    nav = nav_row("np_", page, pages)
    if nav:
        kb.append(nav)
    head = f"📝 <b>یادداشت‌های شما</b> ({len(rows)})" if rows else "📝 هنوز یادداشتی ندارید."
    await respond(update, head, InlineKeyboardMarkup(kb))


async def show_todos(update):
    uid = update.effective_user.id
    rows = db("SELECT id, text, done FROM todos WHERE user_id=? ORDER BY done, id DESC LIMIT 30",
              (uid,), "all")
    kb = []
    for r in rows:
        mark = "✅" if r["done"] else "⬜"
        kb.append([InlineKeyboardButton(f"{mark} {r['text'][:30]}", callback_data=f"tt_{r['id']}"),
                   InlineKeyboardButton("🗑", callback_data=f"td_{r['id']}")])
    kb.append([InlineKeyboardButton("➕ افزودن", callback_data="tn"),
               InlineKeyboardButton("🧹 پاک‌کردن انجام‌شده‌ها", callback_data="tc")])
    done = sum(1 for r in rows if r["done"])
    head = (f"✅ <b>کارها</b> — انجام‌شده: {done}/{len(rows)}\n\nروی هر کار بزنید تا وضعیتش عوض شود."
            if rows else "✅ لیست کارها خالی است.")
    await respond(update, head, InlineKeyboardMarkup(kb))


async def show_reminders(update):
    uid = update.effective_user.id
    rows = db("SELECT id, text, due, rep FROM reminders WHERE user_id=? AND sent=0 "
              "ORDER BY due LIMIT 30", (uid,), "all")
    lines = ["⏰ <b>یادآورهای فعال</b>\n"]
    kb = [[InlineKeyboardButton("➕ یادآور جدید", callback_data="rn")]]
    for r in rows:
        rep = {"daily": "🔁 روزانه", "weekly": "🔁 هفتگی"}.get(r["rep"] or "", "")
        lines.append(f"• {fmt_dt(r['due'])} {rep}\n  {esc(r['text'][:60])}")
        kb.append([InlineKeyboardButton("🗑 " + r["text"][:25], callback_data=f"rd_{r['id']}")])
    if not rows:
        lines.append("یادآور فعالی ندارید.")
    await respond(update, "\n".join(lines), InlineKeyboardMarkup(kb))


async def show_expenses(update):
    uid = update.effective_user.id
    text = (f"💸 <b>مدیریت هزینه‌ها</b>\n\n"
            f"امروز: <b>{fmt(total_since(uid, day_start_ts()))}</b> تومان\n"
            f"این ماه: <b>{fmt(total_since(uid, month_start_ts()))}</b> تومان")
    kb = [[InlineKeyboardButton("➕ هزینه جدید", callback_data="en")],
          [InlineKeyboardButton("📊 امروز", callback_data="er_day"),
           InlineKeyboardButton("📊 ۷ روز اخیر", callback_data="er_week"),
           InlineKeyboardButton("📊 این ماه", callback_data="er_month")],
          [InlineKeyboardButton("🧾 آخرین هزینه‌ها", callback_data="el")]]
    await respond(update, text, InlineKeyboardMarkup(kb))


async def show_last_expenses(update):
    uid = update.effective_user.id
    rows = db("SELECT id, amount, category, note, created_at FROM expenses WHERE user_id=? "
              "ORDER BY id DESC LIMIT 10", (uid,), "all")
    kb = []
    if not rows:
        text = "🧾 هنوز هزینه‌ای ثبت نشده."
    else:
        lines = ["🧾 <b>آخرین هزینه‌ها</b>\n"]
        for r in rows:
            note = f" ({esc(r['note'])})" if r["note"] else ""
            lines.append(f"• {fmt(r['amount'])} — {esc(r['category'])}{note}\n  {fmt_dt(r['created_at'])}")
            kb.append([InlineKeyboardButton(f"🗑 حذف {fmt(r['amount'])}", callback_data=f"ed_{r['id']}")])
        text = "\n".join(lines)
    kb.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="eh")])
    await respond(update, text, InlineKeyboardMarkup(kb))


async def show_tools(update):
    kb = [[InlineKeyboardButton("🧮 ماشین‌حساب", callback_data="t_calc"),
           InlineKeyboardButton("🔐 ساخت رمز", callback_data="t_pass")],
          [InlineKeyboardButton("🔄 تبدیل تاریخ", callback_data="t_date"),
           InlineKeyboardButton("🔢 شمارش متن", callback_data="t_count")],
          [InlineKeyboardButton("🎲 تاس", callback_data="t_dice"),
           InlineKeyboardButton("🪙 شیر یا خط", callback_data="t_coin")]]
    await respond(update, "🧰 <b>ابزارها</b>\n\nیکی را انتخاب کنید:", InlineKeyboardMarkup(kb))


# ============================== هندلرهای حالت‌ها ==============================
async def st_note_add(update, context, text):
    if len(text) > 3500:
        await update.message.reply_text("❌ یادداشت خیلی بلند است (حداکثر 3500 کاراکتر).")
        return
    db("INSERT INTO notes(user_id, text, created_at) VALUES(?,?,?)",
       (update.effective_user.id, text, time.time()))
    context.user_data.pop("state", None)
    await update.message.reply_text("✅ یادداشت ذخیره شد.")
    await show_notes(update, 0)


async def st_note_find(update, context, text):
    context.user_data.pop("state", None)
    like = "%" + text.replace("%", "").replace("_", "") + "%"
    rows = db("SELECT id, text FROM notes WHERE user_id=? AND text LIKE ? ORDER BY id DESC LIMIT 20",
              (update.effective_user.id, like), "all")
    if not rows:
        await update.message.reply_text("🔎 چیزی پیدا نشد.")
        return
    kb = [[InlineKeyboardButton("📄 " + r["text"].replace("\n", " ")[:32], callback_data=f"nv_{r['id']}")]
          for r in rows]
    await update.message.reply_text(f"🔎 {len(rows)} نتیجه:", reply_markup=InlineKeyboardMarkup(kb))


async def st_todo_add(update, context, text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:20]
    for ln in lines:
        db("INSERT INTO todos(user_id, text, created_at) VALUES(?,?,?)",
           (update.effective_user.id, ln[:200], time.time()))
    context.user_data.pop("state", None)
    await show_todos(update)


async def st_rem_text(update, context, text):
    context.user_data["rem_text"] = text[:500]
    context.user_data["state"] = "rem_when"
    await update.message.reply_text("⏰ چه زمانی یادآوری کنم؟\n\n" + WHEN_HELP)


async def st_rem_when(update, context, text):
    due = parse_when(text)
    if due is None or due < time.time() - 60:
        await update.message.reply_text("❌ زمان نامعتبر یا گذشته است. دوباره بفرستید.\n\n" + WHEN_HELP)
        return
    context.user_data["rem_due"] = due
    context.user_data.pop("state", None)
    kb = [[InlineKeyboardButton("یک‌بار", callback_data="rr_none"),
           InlineKeyboardButton("🔁 روزانه", callback_data="rr_daily"),
           InlineKeyboardButton("🔁 هفتگی", callback_data="rr_weekly")]]
    await update.message.reply_text(f"🕒 {fmt_dt(due)}\n\nتکرار شود؟", reply_markup=InlineKeyboardMarkup(kb))


async def st_exp_amount(update, context, text):
    amount = parse_amount(text)
    if amount is None:
        await update.message.reply_text("❌ مبلغ نامعتبر است. مثال: 50000 یا 50k یا 2m")
        return
    context.user_data["exp_amount"] = amount
    context.user_data.pop("state", None)
    kb, row = [], []
    for i, c in enumerate(CATEGORIES):
        row.append(InlineKeyboardButton(c, callback_data=f"ec_{i}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    await update.message.reply_text(f"💰 {fmt(amount)} تومان\n\nدسته را انتخاب کنید:",
                                    reply_markup=InlineKeyboardMarkup(kb))


async def st_exp_note(update, context, text):
    amount = context.user_data.pop("exp_amount", None)
    cat = context.user_data.pop("exp_cat", None)
    context.user_data.pop("state", None)
    if not amount or not cat:
        await update.message.reply_text("نشست منقضی شد. دوباره از منوی هزینه‌ها شروع کنید.")
        return
    note = "" if text in ("-", "ندارم") else text[:200]
    db("INSERT INTO expenses(user_id, amount, category, note, created_at) VALUES(?,?,?,?,?)",
       (update.effective_user.id, amount, cat, note, time.time()))
    uid = update.effective_user.id
    await update.message.reply_text(
        f"✅ ثبت شد: {fmt(amount)} تومان — {cat}\n"
        f"📊 مجموع امروز: {fmt(total_since(uid, day_start_ts()))} تومان")


async def st_calc(update, context, text):
    try:
        v = calc_value(text)
        await update.message.reply_text(f"🧮 <code>{esc(v)}</code>", parse_mode=HTML)
    except Exception:
        await update.message.reply_text("❌ عبارت نامعتبر است. مثال: (2+3)*4 یا sqrt(16)")


async def st_date_conv(update, context, text):
    res = convert_date(text)
    await update.message.reply_text(res or "❌ فرمت نامعتبر. مثال: 1405/09/01 یا 2026-12-01")


async def st_text_count(update, context, text):
    context.user_data.pop("state", None)
    
    # متغیرهای کمکی برای جلوگیری از خطای بک‌اسلش داخل f-string
    chars_len = len(text)
    no_space_len = len(re.sub(r'\s', '', text))
    words_len = len(text.split())
    lines_count = len(text.splitlines())

    await update.message.reply_text(
        f"🔢 <b>نتیجه شمارش</b>\n\n"
        f"کاراکتر: {chars_len}\n"
        f"بدون فاصله: {no_space_len}\n"
        f"کلمه: {words_len}\n"
        f"خط: {lines_count}", parse_mode=HTML
    )


async def st_post_button(update, context, text):
    p = context.user_data.setdefault("post", {})
    if text.strip().lower() in ("ندارم", "no", "-"):
        p.pop("btn_text", None)
        p.pop("btn_url", None)
    else:
        parts = [x.strip() for x in text.split("|")]
        if len(parts) != 2 or not parts[0] or not re.match(r"^(https?://|tg://|t\.me/)", parts[1]):
            await update.message.reply_text("❌ فرمت درست: متن دکمه | لینک\nمثال: ورود به ربات | https://t.me/mybot\nیا بنویسید: ندارم")
            return
        url = parts[1] if not parts[1].startswith("t.me/") else "https://" + parts[1]
        p["btn_text"], p["btn_url"] = parts[0][:60], url
    context.user_data["state"] = "post_time"
    await update.message.reply_text("🕒 چه زمانی ارسال شود؟ (now = همین الان)\n\n" + WHEN_HELP)


async def st_post_time(update, context, text):
    due = parse_when(text)
    if due is None or due < time.time() - 60:
        await update.message.reply_text("❌ زمان نامعتبر یا گذشته است.\n\n" + WHEN_HELP)
        return
    p = context.user_data.get("post") or {}
    if not all(k in p for k in ("src_chat", "src_msg", "target")):
        context.user_data.pop("state", None)
        await update.message.reply_text("نشست منقضی شد. دوباره /newpost را بزنید.")
        return
    pid = db("INSERT INTO posts(src_chat, src_msg, target, btn_text, btn_url, due, status, created_at) "
             "VALUES(?,?,?,?,?,?,?,?)",
             (p["src_chat"], p["src_msg"], p["target"], p.get("btn_text"), p.get("btn_url"),
              due, "scheduled", time.time()))
    context.user_data.pop("state", None)
    context.user_data.pop("post", None)
    markup = (InlineKeyboardMarkup([[InlineKeyboardButton(p["btn_text"], url=p["btn_url"])]])
              if p.get("btn_text") else None)
    try:
        await context.bot.copy_message(update.effective_chat.id, p["src_chat"], p["src_msg"],
                                       reply_markup=markup)
    except Exception:
        pass
    await update.message.reply_text(f"✅ پست #{pid} برای {fmt_dt(due)} زمان‌بندی شد.\n"
                                    "(پیام بالا پیش‌نمایش آن است.)\nلغو: /posts")


async def st_post_content(update, context):
    msg = update.message
    context.user_data["post"] = {"src_chat": msg.chat_id, "src_msg": msg.message_id}
    chans = db("SELECT chat_id, title FROM channels", (), "all")
    if not chans:
        context.user_data.pop("state", None)
        await msg.reply_text("هنوز کانالی ندارید. اول /addchannel @channel را بزنید.")
        return
    context.user_data["state"] = "post_channel"
    kb = [[InlineKeyboardButton(c["title"] or str(c["chat_id"]), callback_data=f"pch_{c['chat_id']}")]
          for c in chans]
    await msg.reply_text("📢 این پست به کدام کانال برود؟", reply_markup=InlineKeyboardMarkup(kb))


STATE_HANDLERS = {
    "note_add": st_note_add, "note_find": st_note_find, "todo_add": st_todo_add,
    "rem_text": st_rem_text, "rem_when": st_rem_when, "exp_amount": st_exp_amount,
    "exp_note": st_exp_note, "calc": st_calc, "date_conv": st_date_conv,
    "text_count": st_text_count, "post_button": st_post_button, "post_time": st_post_time,
}
OWNER_STATES = {"post_button", "post_time"}


# ============================== هندلرهای پیام ==============================
async def cmd_start(update, context):
    if not update.message or not await ensure_user(update, context):
        return
    context.user_data.clear()
    u = update.effective_user
    await update.message.reply_text(
        f"👋 سلام <b>{esc(u.first_name)}</b>!\n\n"
        "من دستیار شخصی توام: یادداشت، کارها، یادآور، هزینه‌ها و چند ابزار کاربردی.\n"
        "از منوی پایین شروع کن 👇", parse_mode=HTML, reply_markup=main_kb(u.id))


async def cmd_help(update, context):
    if await ensure_user(update, context):
        await update.message.reply_text(HELP_TEXT, parse_mode=HTML)


async def cmd_cancel(update, context):
    if await ensure_user(update, context):
        context.user_data.clear()
        await update.message.reply_text("لغو شد. منوی اصلی 👇", reply_markup=main_kb(update.effective_user.id))


async def cmd_id(update, context):
    await update.message.reply_text(f"🆔 آیدی عددی شما: <code>{update.effective_user.id}</code>", parse_mode=HTML)


async def cmd_today(update, context):
    if await ensure_user(update, context):
        await update.message.reply_text(today_text(), parse_mode=HTML)


async def cmd_calc(update, context):
    if not await ensure_user(update, context):
        return
    if not context.args:
        context.user_data["state"] = "calc"
        await update.message.reply_text("🧮 عبارت را بفرستید (برای خروج /cancel).")
        return
    await st_calc(update, context, " ".join(context.args))


async def text_router(update, context):
    msg = update.message
    uid = update.effective_user.id
    text = (msg.text or "").strip()

    if text in MENU_BTNS:
        context.user_data.pop("state", None)
        if text == BTN_NOTES:
            await show_notes(update, 0)
        elif text == BTN_TODO:
            await show_todos(update)
        elif text == BTN_REM:
            await show_reminders(update)
        elif text == BTN_EXP:
            await show_expenses(update)
        elif text == BTN_TOOLS:
            await show_tools(update)
        elif text == BTN_TODAY:
            await msg.reply_text(today_text(), parse_mode=HTML)
        elif text == BTN_HELP:
            await msg.reply_text(HELP_TEXT, parse_mode=HTML)
        elif text == BTN_ADMIN and is_owner(uid):
            await msg.reply_text(ADMIN_HELP, parse_mode=HTML)
        return

    st = context.user_data.get("state")
    handler = STATE_HANDLERS.get(st)
    if handler and (st not in OWNER_STATES or is_owner(uid)):
        await handler(update, context, text)
        return
    await msg.reply_text("از منوی پایین یکی را انتخاب کنید 👇", reply_markup=main_kb(uid))


async def msg_router(update, context):
    if not update.message or not await ensure_user(update, context):
        return
    if context.user_data.get("state") == "post_content" and is_owner(update.effective_user.id):
        await st_post_content(update, context)
        return
    if update.message.text:
        await text_router(update, context)
        return
    await update.message.reply_text("از منوی پایین استفاده کنید 👇", reply_markup=main_kb(update.effective_user.id))


# ============================== کال‌بک‌ها ==============================
async def ask(update, context, state, text):
    context.user_data["state"] = state
    await update.callback_query.message.reply_text(text)


async def callback_router(update, context):
    q = update.callback_query
    d = q.data or ""
    uid = q.from_user.id
    try:
        await q.answer()
    except Exception:
        pass
    if not await ensure_user(update, context):
        return

    # ---- یادداشت‌ها ----
    if d.startswith("np_"):
        await show_notes(update, int(d[3:]))
    elif d == "nn":
        await ask(update, context, "note_add", "📝 متن یادداشت را بفرستید:")
    elif d == "nf":
        await ask(update, context, "note_find", "🔎 کلمه مورد جستجو را بفرستید:")
    elif d.startswith("nv_"):
        r = db("SELECT * FROM notes WHERE id=? AND user_id=?", (int(d[3:]), uid), "one")
        if not r:
            await q.message.reply_text("یادداشت پیدا نشد.")
            return
        kb = [[InlineKeyboardButton("🗑 حذف", callback_data=f"nd_{r['id']}"),
               InlineKeyboardButton("⬅️ بازگشت", callback_data="np_0")]]
        await respond(update, f"📄 <b>یادداشت #{r['id']}</b>\n\n{esc(r['text'])}\n\n🕒 {fmt_dt(r['created_at'])}",
                      InlineKeyboardMarkup(kb))
    elif d.startswith("nd_"):
        db("DELETE FROM notes WHERE id=? AND user_id=?", (int(d[3:]), uid))
        await show_notes(update, 0)

    # ---- کارها ----
    elif d == "tn":
        await ask(update, context, "todo_add", "✅ کارها را بفرستید (هر خط یک کار):")
    elif d.startswith("tt_"):
        db("UPDATE todos SET done=1-done WHERE id=? AND user_id=?", (int(d[3:]), uid))
        await show_todos(update)
    elif d.startswith("td_"):
        db("DELETE FROM todos WHERE id=? AND user_id=?", (int(d[3:]), uid))
        await show_todos(update)
    elif d == "tc":
        db("DELETE FROM todos WHERE user_id=? AND done=1", (uid,))
        await show_todos(update)

    # ---- یادآورها ----
    elif d == "rn":
        await ask(update, context, "rem_text", "⏰ متن یادآوری را بفرستید:")
    elif d.startswith("rd_"):
        db("DELETE FROM reminders WHERE id=? AND user_id=?", (int(d[3:]), uid))
        await show_reminders(update)
    elif d.startswith("rr_"):
        text = context.user_data.pop("rem_text", None)
        due = context.user_data.pop("rem_due", None)
        if not text or not due:
            await q.message.reply_text("نشست منقضی شد. دوباره یادآور جدید بسازید.")
            return
        rep = d[3:] if d[3:] in ("daily", "weekly") else None
        db("INSERT INTO reminders(user_id, text, due, rep) VALUES(?,?,?,?)", (uid, text, due, rep))
        await respond(update, f"✅ یادآور ثبت شد\n🕒 {fmt_dt(due)}\n📝 {esc(text)}")

    # ---- هزینه‌ها ----
    elif d == "en":
        await ask(update, context, "exp_amount", "💰 مبلغ را به تومان بفرستید (مثال: 50000 یا 50k یا 2m):")
    elif d.startswith("ec_"):
        i = int(d[3:])
        if not context.user_data.get("exp_amount") or not 0 <= i < len(CATEGORIES):
            await q.message.reply_text("نشست منقضی شد. دوباره شروع کنید.")
            return
        context.user_data["exp_cat"] = CATEGORIES[i]
        context.user_data["state"] = "exp_note"
        await respond(update, f"{CATEGORIES[i]} انتخاب شد.\n\nتوضیح کوتاه بنویسید یا - بفرستید.")
    elif d in ("er_day", "er_week", "er_month"):
        since, title = {"er_day": (day_start_ts(), "📊 گزارش امروز"),
                        "er_week": (time.time() - 7 * 86400, "📊 گزارش ۷ روز اخیر"),
                        "er_month": (month_start_ts(), "📊 گزارش این ماه (شمسی)")}[d]
        kb = [[InlineKeyboardButton("⬅️ بازگشت", callback_data="eh")]]
        await respond(update, expense_report(uid, since, title), InlineKeyboardMarkup(kb))
    elif d == "el":
        await show_last_expenses(update)
    elif d == "eh":
        await show_expenses(update)
    elif d.startswith("ed_"):
        db("DELETE FROM expenses WHERE id=? AND user_id=?", (int(d[3:]), uid))
        await show_last_expenses(update)

    # ---- ابزارها ----
    elif d == "t_calc":
        await ask(update, context, "calc", "🧮 عبارت را بفرستید (مثال: (2+3)*4). برای خروج /cancel")
    elif d == "t_pass":
        kb = [[InlineKeyboardButton(str(n), callback_data=f"pw_{n}") for n in (8, 12, 16, 24)]]
        await respond(update, "🔐 طول رمز را انتخاب کنید:", InlineKeyboardMarkup(kb))
    elif d.startswith("pw_"):
        n = min(max(int(d[3:]), 8), 64)
        await q.message.reply_text(f"🔐 رمز شما:\n<code>{esc(make_password(n))}</code>", parse_mode=HTML)
    elif d == "t_date":
        await ask(update, context, "date_conv", "🔄 تاریخ را بفرستید (شمسی یا میلادی)\nمثال: 1405/09/01 یا 2026-12-01")
    elif d == "t_count":
        await ask(update, context, "text_count", "🔢 متن را بفرستید:")
    elif d == "t_dice":
        await q.message.reply_dice()
    elif d == "t_coin":
        await q.message.reply_text(random.choice(["🪙 شیر", "🪙 خط"]))

    # ---- پست‌گذاری (مدیر) ----
    elif d.startswith("pch_") and is_owner(uid):
        p = context.user_data.get("post")
        if not p or "src_msg" not in p:
            await q.message.reply_text("نشست منقضی شد. دوباره /newpost را بزنید.")
            return
        p["target"] = int(d[4:])
        context.user_data["state"] = "post_button"
        await respond(update, "🔘 دکمه زیر پست می‌خواهید؟\n\nبفرستید: <code>متن دکمه | لینک</code>\n"
                              "مثال: <code>ورود به ربات | https://t.me/mybot</code>\nیا بنویسید: ندارم")
    elif d.startswith("pcx_") and is_owner(uid):
        ok = db("UPDATE posts SET status='cancelled' WHERE id=? AND status='scheduled'", (int(d[4:]),))
        await respond(update, "🚫 پست لغو شد." if ok else "این پست دیگر قابل لغو نیست.")
    elif d == "noop":
        pass


# ============================== دستورات مدیر ==============================
async def cmd_adminhelp(update, context):
    await update.message.reply_text(ADMIN_HELP, parse_mode=HTML)


async def cmd_stats(update, context):
    def n(sql):
        return db(sql, (), "one")["c"]
    await update.message.reply_text(
        "📊 <b>آمار ربات</b>\n\n"
        f"👥 کاربران: {n('SELECT COUNT(*) c FROM users')}\n"
        f"📝 یادداشت‌ها: {n('SELECT COUNT(*) c FROM notes')}\n"
        f"✅ کارها: {n('SELECT COUNT(*) c FROM todos')}\n"
        f"⏰ یادآورهای فعال: {n('SELECT COUNT(*) c FROM reminders WHERE sent=0')}\n"
        f"💸 هزینه‌های ثبت‌شده: {n('SELECT COUNT(*) c FROM expenses')}\n"
        f"📢 کانال‌ها: {n('SELECT COUNT(*) c FROM channels')}\n"
        f"🗓 پست‌های زمان‌بندی‌شده: {n(\"SELECT COUNT(*) c FROM posts WHERE status='scheduled'\")}",
        parse_mode=HTML)


async def cmd_broadcast(update, context):
    msg = update.message
    src, text = msg.reply_to_message, None
    if not src:
        parts = (msg.text or "").split(None, 1)
        if len(parts) < 2:
            await msg.reply_text("استفاده: /broadcast متن   یا ریپلای روی یک پیام")
            return
        text = parts[1]
    ids = [r["user_id"] for r in db("SELECT user_id FROM users WHERE banned=0", (), "all")]
    ok = fail = 0
    for uid in ids:
        try:
            if text is not None:
                await context.bot.send_message(uid, text)
            else:
                await context.bot.copy_message(uid, msg.chat_id, src.message_id)
            ok += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await msg.reply_text(f"📣 ارسال شد: {ok} | ناموفق: {fail}")


async def cmd_ban(update, context):
    try:
        uid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /ban user_id")
        return
    if uid == OWNER_ID:
        await update.message.reply_text("نمی‌شود مدیر را مسدود کرد.")
        return
    db("INSERT OR IGNORE INTO users(user_id, name, joined_at) VALUES(?,?,?)", (uid, "", time.time()))
    db("UPDATE users SET banned=1 WHERE user_id=?", (uid,))
    await update.message.reply_text(f"⛔️ کاربر {uid} مسدود شد.")


async def cmd_unban(update, context):
    try:
        uid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /unban user_id")
        return
    db("UPDATE users SET banned=0 WHERE user_id=?", (uid,))
    await update.message.reply_text(f"✅ کاربر {uid} آزاد شد.")


def _chan_ref(arg):
    arg = arg.strip()
    if re.fullmatch(r"-?\d+", arg):
        return int(arg)
    return arg if arg.startswith("@") else "@" + arg


async def cmd_addchannel(update, context):
    if not context.args:
        await update.message.reply_text("استفاده: /addchannel @channel\n(ربات باید ادمین کانال با دسترسی Post messages باشد)")
        return
    try:
        chat = await context.bot.get_chat(_chan_ref(context.args[0]))
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
    except Exception as e:
        await update.message.reply_text(f"❌ کانال پیدا نشد یا ربات عضو/ادمین آن نیست:\n{e}")
        return
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ ربات در این کانال ادمین نیست.")
        return
    if chat.type == "channel" and getattr(member, "can_post_messages", None) is False:
        await update.message.reply_text("❌ ربات دسترسی «Post messages» ندارد.")
        return
    db("INSERT OR REPLACE INTO channels(chat_id, title) VALUES(?,?)", (chat.id, chat.title or str(chat.id)))
    await update.message.reply_text(f"✅ کانال «{chat.title}» اضافه شد.")


async def cmd_delchannel(update, context):
    if not context.args:
        await update.message.reply_text("استفاده: /delchannel @channel")
        return
    try:
        chat = await context.bot.get_chat(_chan_ref(context.args[0]))
        ok = db("DELETE FROM channels WHERE chat_id=?", (chat.id,))
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {e}")
        return
    await update.message.reply_text("✅ حذف شد." if ok else "این کانال در لیست نبود.")


async def cmd_channels(update, context):
    rows = db("SELECT chat_id, title FROM channels", (), "all")
    if not rows:
        await update.message.reply_text("کانالی ثبت نشده. /addchannel @channel")
        return
    await update.message.reply_text("📢 کانال‌ها:\n" + "\n".join(f"• {r['title']} ({r['chat_id']})" for r in rows))


async def cmd_newpost(update, context):
    if not db("SELECT 1 FROM channels", (), "one"):
        await update.message.reply_text("اول یک کانال اضافه کنید: /addchannel @channel")
        return
    context.user_data["state"] = "post_content"
    context.user_data["post"] = {}
    await update.message.reply_text(
        "📤 پیامی را که می‌خواهید تو کانال برود بفرستید (متن، عکس، ویدیو، فایل...).\n"
        "برای انصراف: /cancel")


async def cmd_posts(update, context):
    rows = db("SELECT * FROM posts WHERE status='scheduled' ORDER BY due LIMIT 20", (), "all")
    if not rows:
        await update.message.reply_text("پست زمان‌بندی‌شده‌ای وجود ندارد.")
        return
    lines, kb = ["🗓 <b>پست‌های زمان‌بندی‌شده</b>\n"], []
    for r in rows:
        lines.append(f"#{r['id']} — {fmt_dt(r['due'])}")
        kb.append([InlineKeyboardButton(f"🚫 لغو #{r['id']}", callback_data=f"pcx_{r['id']}")])
    await update.message.reply_text("\n".join(lines), parse_mode=HTML, reply_markup=InlineKeyboardMarkup(kb))


# ============================== حلقه‌های پس‌زمینه ==============================
async def reminders_loop(app):
    await asyncio.sleep(8)
    while True:
        try:
            now = time.time()
            rows = await asyncio.to_thread(
                db, "SELECT * FROM reminders WHERE sent=0 AND due<=? ORDER BY due LIMIT 50", (now,), "all")
            for r in rows:
                step = {"daily": 86400, "weekly": 7 * 86400}.get(r["rep"] or "")
                if step:
                    nd = r["due"] + step
                    while nd <= now:
                        nd += step
                    await asyncio.to_thread(db, "UPDATE reminders SET due=? WHERE id=?", (nd, r["id"]))
                else:
                    await asyncio.to_thread(db, "UPDATE reminders SET sent=1 WHERE id=?", (r["id"],))
                await safe_send(app.bot, r["user_id"], f"⏰ <b>یادآوری</b>\n\n{esc(r['text'])}", parse_mode=HTML)
        except Exception as e:
            logger.error(f"reminders_loop: {e}")
        await asyncio.sleep(15)


async def posts_loop(app):
    await asyncio.sleep(10)
    while True:
        try:
            rows = await asyncio.to_thread(
                db, "SELECT * FROM posts WHERE status='scheduled' AND due<=? ORDER BY due LIMIT 10",
                (time.time(),), "all")
            for p in rows:
                claimed = await asyncio.to_thread(
                    db, "UPDATE posts SET status='sending' WHERE id=? AND status='scheduled'", (p["id"],))
                if not claimed:
                    continue
                try:
                    markup = (InlineKeyboardMarkup([[InlineKeyboardButton(p["btn_text"], url=p["btn_url"])]])
                              if p["btn_text"] else None)
                    await app.bot.copy_message(chat_id=p["target"], from_chat_id=p["src_chat"],
                                               message_id=p["src_msg"], reply_markup=markup)
                    await asyncio.to_thread(db, "UPDATE posts SET status='sent' WHERE id=?", (p["id"],))
                    await safe_send(app.bot, OWNER_ID, f"✅ پست #{p['id']} در کانال ارسال شد.")
                except Exception as e:
                    await asyncio.to_thread(db, "UPDATE posts SET status='failed', error=? WHERE id=?",
                                            (str(e)[:200], p["id"]))
                    await safe_send(app.bot, OWNER_ID, f"❌ ارسال پست #{p['id']} ناموفق بود:\n{e}")
        except Exception as e:
            logger.error(f"posts_loop: {e}")
        await asyncio.sleep(15)


# ============================== سلامت و خطا ==============================
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - bot is running")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def run_health_server():
    try:
        ThreadingHTTPServer(("0.0.0.0", int(PORT)), Health).serve_forever()
    except OSError as e:
        logger.warning(f"وب‌سرور اجرا نشد: {e}")


async def on_error(update, context):
    err = context.error
    if isinstance(err, Conflict):
        logger.warning("Conflict: نمونه دیگری با همین توکن در حال اجراست")
        return
    logger.error("خطا:", exc_info=err)
    if isinstance(err, (NetworkError, RetryAfter)):
        return
    if isinstance(err, BadRequest) and "not modified" in str(err).lower():
        return
    if not throttled("err", 60):
        await safe_send(context.bot, OWNER_ID, f"⚠️ خطا: {type(err).__name__}: {err}")


_BG = []


async def post_init(app):
    await app.bot.set_my_commands([
        ("start", "شروع و منوی اصلی"), ("help", "راهنما"), ("today", "تاریخ امروز"),
        ("calc", "ماشین‌حساب"), ("cancel", "لغو عملیات"), ("id", "آیدی عددی من")])
    _BG.append(asyncio.create_task(reminders_loop(app)))
    _BG.append(asyncio.create_task(posts_loop(app)))


async def post_shutdown(app):
    for t in _BG:
        t.cancel()
    _BG.clear()


def main():
    init_db()
    if PORT:
        threading.Thread(target=run_health_server, daemon=True).start()

    app = (Application.builder().token(TOKEN).concurrent_updates(True)
           .post_init(post_init).post_shutdown(post_shutdown).build())

    private = filters.ChatType.PRIVATE
    owner = filters.User(OWNER_ID) & private
    for name, fn in (("start", cmd_start), ("help", cmd_help), ("cancel", cmd_cancel),
                     ("id", cmd_id), ("today", cmd_today), ("calc", cmd_calc)):
        app.add_handler(CommandHandler(name, fn, filters=private))
    for name, fn in (("admin", cmd_adminhelp), ("stats", cmd_stats), ("broadcast", cmd_broadcast),
                     ("ban", cmd_ban), ("unban", cmd_unban), ("addchannel", cmd_addchannel),
                     ("delchannel", cmd_delchannel), ("channels", cmd_channels),
                     ("newpost", cmd_newpost), ("posts", cmd_posts)):
        app.add_handler(CommandHandler(name, fn, filters=owner))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(private & ~filters.COMMAND, msg_router))
    app.add_error_handler(on_error)

    logger.info("دستیار در حال اجراست...")
    app.run_polling(bootstrap_retries=-1, drop_pending_updates=True)


if __name__ == "__main__":
    main()
