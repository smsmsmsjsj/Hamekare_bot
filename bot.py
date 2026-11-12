# -*- coding: utf-8 -*-
# دستیار هوشمند - نسخه پیشرفته
# requirements.txt  ->  python-telegram-bot
# متغیرهای محیطی: BOT_TOKEN , OWNER_ID  (اختیاری: TZ_OFFSET_MIN=210 , DATA_DIR=.)
import ast
import asyncio
import csv
import html
import io
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
from datetime import date as Date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import httpx
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as KB, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict, NetworkError, RetryAfter
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("assistant")
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

BTN_NOTES, BTN_TODO = "📝 یادداشت‌ها", "✅ کارها"
BTN_REM, BTN_EXP = "⏰ یادآورها", "💸 هزینه‌ها"
BTN_HABIT, BTN_POMO = "🎯 عادت‌ها", "🍅 تمرکز"
BTN_EVENT, BTN_TOOLS = "🎂 مناسبت‌ها", "🧰 ابزارها"
BTN_DASH, BTN_SET = "📊 داشبورد", "⚙️ تنظیمات"
BTN_HELP, BTN_ADMIN = "ℹ️ راهنما", "🛠 پنل مدیر"
MENU_BTNS = {BTN_NOTES, BTN_TODO, BTN_REM, BTN_EXP, BTN_HABIT, BTN_POMO, BTN_EVENT, BTN_TOOLS,
             BTN_DASH, BTN_SET, BTN_HELP, BTN_ADMIN}

CATEGORIES = ["🍔 خوراک", "🚕 رفت‌وآمد", "🏠 خانه", "🛍 خرید", "💊 سلامت", "🎮 تفریح", "📚 آموزش", "📦 سایر"]
JMONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
           "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
QUOTES = [
    "قدم کوچکِ هر روز، از جهش‌های گاه‌به‌گاه ارزشمندتر است.",
    "کاری را که می‌توانی همین حالا شروع کنی، به فردا نینداز.",
    "تمرکز یعنی گفتنِ «نه» به چیزهای خوب برای رسیدن به چیزهای عالی.",
    "پیشرفت یعنی امروزت از دیروزت بهتر باشد، نه از دیگران.",
    "استراحتِ به‌موقع هم جزئی از مسیر است.",
    "کارِ بزرگ از تکه‌های کوچک ساخته می‌شود؛ اولین تکه را بردار.",
    "عادت‌های خوب، بی‌سروصدا زندگی را عوض می‌کنند.",
    "آنچه اندازه‌گیری شود، بهتر می‌شود.",
    "خسته‌ای؟ کمی نفس بکش و دوباره شروع کن.",
    "برنامه‌ی ناقص که اجرا شود، بهتر از برنامه‌ی کامل است که نشود.",
]
WHEN_HELP = ("نمونه‌ها:\n• 30m یا 2h یا 1d\n• 90 دقیقه ، 2 ساعت ، 3 روز\n• 14:30 (امروز یا فردا)\n"
             "• فردا 09:00\n• 1405/09/01 18:00 (شمسی)\n• 2026-12-01 18:00 (میلادی)")
HELP_TEXT = (
    "ℹ️ <b>راهنمای دستیار</b>\n\n"
    "📝 <b>یادداشت‌ها:</b> ذخیره، پین، جستجو (با #برچسب) و خروجی فایل\n"
    "✅ <b>کارها:</b> هر خط یک کار؛ اول خط ! بزنید تا مهم (🔴) شود\n"
    "⏰ <b>یادآورها:</b> یک‌بار/روزانه/هفتگی/ماهانه با دکمه‌ی تعویق\n"
    "💸 <b>هزینه‌ها:</b> ثبت، بودجه‌ی ماهانه با هشدار، پیش‌بینی پایان ماه، خروجی CSV\n"
    "🎯 <b>عادت‌ها:</b> ثبت روزانه، رکورد پشت‌سرهم (🔥) و تقویم ۲۱ روزه\n"
    "🍅 <b>تمرکز:</b> تایمر پومودورو و آمار تمرکز امروز\n"
    "🎂 <b>مناسبت‌ها:</b> شمارش معکوس و تولد (شمسی/میلادی، سالانه)\n"
    "🧰 <b>ابزارها:</b> ماشین‌حساب، مبدل واحد، QR، رمز، تبدیل تاریخ، آب‌وهوا، اوقات شرعی، ارز دیجیتال و...\n"
    "📊 <b>داشبورد:</b> خلاصه‌ی همه‌چیز در یک نگاه\n"
    "⚙️ <b>تنظیمات:</b> شهر، گزارش صبحگاهی، بودجه، حذف داده‌ها\n\n"
    "⚡️ <b>ثبت سریع:</b> پیام را با <code>+ متن</code> بفرستید تا یادداشت شود، یا با <code>- متن</code> تا کار شود.\n\n"
    "دستورها: /today /calc عبارت /cancel /id\n\n" + WHEN_HELP)
ADMIN_HELP = (
    "🛠 <b>دستورات مدیر</b>\n\n/stats — آمار\n/broadcast متن (یا ریپلای)\n/ban ID و /unban ID\n"
    "/backup — دریافت فایل پشتیبان دیتابیس\n"
    "/restore — فایل پشتیبان را بفرستید و در کپشن بنویسید /restore\n"
    "/addchannel @channel و /delchannel @channel و /channels\n/newpost — پست زمان‌بندی‌شده\n/posts — لیست و لغو")


# ============================== ابزارهای کوچک ==============================
def esc(s):
    return html.escape(str(s), quote=False)


def fmt(n):
    return f"{int(n):,}"


_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def nd(s):
    return s.translate(_DIG)


def bar(p, w=10):
    n = round(min(max(p, 0), 1) * w)
    return "█" * n + "░" * (w - n)


def now():
    return datetime.now(TZ)


def today_iso():
    return now().strftime("%Y-%m-%d")


_LAST = {}


def throttled(key, seconds):
    t = time.time()
    if t - _LAST.get(key, 0) < seconds:
        return True
    _LAST[key] = t
    return False


def is_owner(uid):
    return uid == OWNER_ID


def page_slice(items, page, per=8):
    pages = max((len(items) + per - 1) // per, 1)
    page = min(max(page, 0), pages - 1)
    return items[page * per:(page + 1) * per], page, pages


def nav_row(prefix, page, pages):
    row = []
    if page > 0:
        row.append(B("⬅️ قبلی", callback_data=f"{prefix}{page - 1}"))
    if page < pages - 1:
        row.append(B("بعدی ➡️", callback_data=f"{prefix}{page + 1}"))
    return row


# ============================== تاریخ شمسی ==============================
def g2j(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1])
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def j2g(jy, jm, jd):
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd
    days += (jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186
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
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    sal = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal[gm]:
        gd -= sal[gm]
        gm += 1
    return gy, gm, gd


def jmonth_len(jy, jm):
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if g2j(*j2g(jy, 12, 30)) == (jy, 12, 30) else 29


def jnow():
    n = now()
    return g2j(n.year, n.month, n.day)


def fmt_day(d):
    jy, jm, jd = g2j(d.year, d.month, d.day)
    return f"{WEEKDAYS[d.weekday()]} {jd} {JMONTHS[jm - 1]} {jy}"


def fmt_dt(ts):
    d = datetime.fromtimestamp(ts, TZ)
    jy, jm, jd = g2j(d.year, d.month, d.day)
    return f"{jy}/{jm:02d}/{jd:02d} {d:%H:%M} ({WEEKDAYS[d.weekday()]})"


def month_start_ts():
    jy, jm, _ = jnow()
    gy, gm, gd = j2g(jy, jm, 1)
    return datetime(gy, gm, gd, tzinfo=TZ).timestamp()


def day_start_ts():
    return now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def add_months(dt, n=1):
    m = dt.month - 1 + n
    y, m = dt.year + m // 12, m % 12 + 1
    for d in (dt.day, 30, 29, 28):
        try:
            return dt.replace(year=y, month=m, day=d)
        except ValueError:
            continue
    return dt


def parse_ymd(t):
    """یک تاریخ شمسی یا میلادی -> (y, m, d, cal) یا None"""
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", nd(t).strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        if y < 1700:
            if not (1 <= mo <= 12 and 1 <= d <= jmonth_len(y, mo)):
                return None
            return y, mo, d, "j"
        Date(y, mo, d)
        return y, mo, d, "g"
    except ValueError:
        return None


def parse_when(text):
    t = nd(text).strip().lower()
    cur = now()
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
        dt = cur.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if tomorrow or dt <= cur:
            dt += timedelta(days=1)
        return dt.timestamp()
    m = re.fullmatch(r"(\S+)(?:\s+(\d{1,2}):(\d{2}))?", t)
    p = parse_ymd(m.group(1)) if m else None
    if p:
        y, mo, d, cal = p
        if cal == "j":
            y, mo, d = j2g(y, mo, d)
        try:
            return datetime(y, mo, d, int(m.group(2) or 9), int(m.group(3) or 0), tzinfo=TZ).timestamp()
        except ValueError:
            return None
    return None


def convert_date(text):
    p = parse_ymd(text)
    if not p:
        return None
    y, mo, d, cal = p
    if cal == "j":
        gy, gm, gd = j2g(y, mo, d)
        return f"🗓 شمسی: {y}/{mo:02d}/{d:02d}\n🌍 میلادی: {gy}-{gm:02d}-{gd:02d}\n📆 {WEEKDAYS[Date(gy, gm, gd).weekday()]}"
    jy, jm, jd = g2j(y, mo, d)
    return f"🌍 میلادی: {y}-{mo:02d}-{d:02d}\n🗓 شمسی: {jy}/{jm:02d}/{jd:02d} ({jd} {JMONTHS[jm - 1]})\n📆 {WEEKDAYS[Date(y, mo, d).weekday()]}"


# ============================== ماشین‌حساب و ابزارها ==============================
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log,
          "log10": math.log10, "abs": abs, "round": round}
_CONSTS = {"pi": math.pi, "e": math.e}


def _ev(node):
    if isinstance(node, ast.Expression):
        return _ev(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
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
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
            and not node.keywords and len(node.args) <= 2):
        return _FUNCS[node.func.id](*[_ev(a) for a in node.args])
    raise ValueError("bad")


def calc_value(expr):
    e = nd(expr).replace("×", "*").replace("÷", "/").replace("^", "**").replace("٫", ".")
    if len(e) > 200:
        raise ValueError("long")
    v = _ev(ast.parse(e.strip(), mode="eval"))
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
    t = nd(text).strip().lower().replace(",", "").replace("٬", "").replace("،", "")
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


UNITS = {
    "طول": {"mm": 0.001, "cm": 0.01, "m": 1, "km": 1000, "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344},
    "وزن": {"mg": 1e-6, "g": 0.001, "kg": 1, "lb": 0.45359237, "oz": 0.0283495},
    "حجم داده": {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4},
    "حجم": {"ml": 0.001, "l": 1, "gal": 3.78541, "cup": 0.236588},
}


def convert_units(text):
    m = re.fullmatch(r"([\d.]+)\s*([a-z]+)\s*(?:to|in|->|به)\s*([a-z]+)", nd(text).strip().lower())
    if not m:
        return None
    v, a, b = float(m.group(1)), m.group(2), m.group(3)
    if a in ("c", "f", "k") and b in ("c", "f", "k"):
        c = v if a == "c" else (v - 32) * 5 / 9 if a == "f" else v - 273.15
        r = c if b == "c" else c * 9 / 5 + 32 if b == "f" else c + 273.15
        return f"🌡 {v:g}°{a.upper()} = {r:.2f}°{b.upper()}"
    for cat, tbl in UNITS.items():
        if a in tbl and b in tbl:
            return f"📏 ({cat}) {v:g} {a} = {v * tbl[a] / tbl[b]:.6g} {b}"
    return None


# ============================== دیتابیس ==============================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, name TEXT, joined_at REAL, banned INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS prefs(user_id INTEGER NOT NULL, k TEXT NOT NULL, v TEXT, PRIMARY KEY(user_id,k));
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);
CREATE TABLE IF NOT EXISTS todos(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL, prio INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_todos_user ON todos(user_id);
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL, due REAL NOT NULL, rep TEXT, sent INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_rem_due ON reminders(sent,due);
CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, category TEXT NOT NULL, note TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_exp_user ON expenses(user_id,created_at);
CREATE TABLE IF NOT EXISTS habits(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS habit_logs(habit_id INTEGER NOT NULL, day TEXT NOT NULL, PRIMARY KEY(habit_id,day));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL, y INTEGER, m INTEGER, d INTEGER, cal TEXT, yearly INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pomo(user_id INTEGER PRIMARY KEY, end_ts REAL NOT NULL, kind TEXT NOT NULL, minutes INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS focus_log(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, day TEXT NOT NULL, minutes INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sent_log(k TEXT PRIMARY KEY, t REAL);
CREATE TABLE IF NOT EXISTS channels(chat_id INTEGER PRIMARY KEY, title TEXT);
CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY AUTOINCREMENT, src_chat INTEGER NOT NULL, src_msg INTEGER NOT NULL, target INTEGER NOT NULL, btn_text TEXT, btn_url TEXT, due REAL NOT NULL, status TEXT NOT NULL, error TEXT, created_at REAL NOT NULL);
"""
USER_TABLES = ("notes", "todos", "reminders", "expenses", "habits", "events", "pomo", "focus_log", "prefs")


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


def once(key):
    with conn() as c:
        return c.execute("INSERT OR IGNORE INTO sent_log(k,t) VALUES(?,?)", (key, time.time())).rowcount == 1


def pref(uid, k, default=""):
    r = db("SELECT v FROM prefs WHERE user_id=? AND k=?", (uid, k), "one")
    return r["v"] if r else default


def set_pref(uid, k, v):
    db("INSERT INTO prefs(user_id,k,v) VALUES(?,?,?) ON CONFLICT(user_id,k) DO UPDATE SET v=excluded.v",
       (uid, k, str(v)))


def make_backup():
    path = os.path.join(DATA_DIR, "_bk.tmp")
    src, dst = sqlite3.connect(DB_PATH), sqlite3.connect(path)
    src.backup(dst)
    dst.close()
    src.close()
    with open(path, "rb") as f:
        data = f.read()
    os.remove(path)
    return data


def total_since(uid, since):
    return db("SELECT COALESCE(SUM(amount),0) s FROM expenses WHERE user_id=? AND created_at>=?", (uid, since), "one")["s"]


def budget_line(uid):
    b = int(pref(uid, "budget", "0") or 0)
    if not b:
        return ""
    tot = total_since(uid, month_start_ts())
    return f"\n🎯 بودجه: {bar(tot / b)} {tot / b * 100:.0f}٪ ({fmt(tot)} از {fmt(b)})"


def expense_report(uid, since, title, month=False):
    rows = db("SELECT category,SUM(amount) s FROM expenses WHERE user_id=? AND created_at>=? GROUP BY category ORDER BY s DESC",
              (uid, since), "all")
    if not rows:
        return f"<b>{title}</b>\n\nهنوز هزینه‌ای ثبت نشده."
    total = sum(r["s"] for r in rows)
    out = [f"<b>{title}</b>", "", f"💰 مجموع: <b>{fmt(total)}</b> تومان"]
    if month:
        jy, jm, jd = jnow()
        out.append(f"📈 پیش‌بینی پایان ماه: {fmt(total / max(jd, 1) * jmonth_len(jy, jm))} تومان")
        if budget_line(uid):
            out.append(budget_line(uid).strip())
    out.append("")
    for r in rows:
        p = r["s"] / total
        out.append(f"{esc(r['category'])}\n{bar(p)} {p * 100:.0f}٪ — {fmt(r['s'])}")
    return "\n".join(out)


def habit_days(hid):
    return {r["day"] for r in db("SELECT day FROM habit_logs WHERE habit_id=?", (hid,), "all")}


def streak(days):
    d = now().date()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def next_occ(r, today):
    y, m, d, cal = r["y"], r["m"], r["d"], r["cal"]
    try:
        base = Date(*j2g(y, m, d)) if cal == "j" else Date(y, m, d)
        if not r["yearly"]:
            return base
        if cal == "j":
            jy = g2j(today.year, today.month, today.day)[0]
            for k in (jy, jy + 1):
                c = Date(*j2g(k, m, min(d, jmonth_len(k, m))))
                if c >= today:
                    return c
        else:
            for k in (today.year, today.year + 1):
                c = Date(k, m, d) if not (m == 2 and d == 29 and k % 4) else Date(k, 2, 28)
                if c >= today:
                    return c
    except ValueError:
        return None
    return None


def days_text(n):
    return "امروز 🎉" if n == 0 else "فردا" if n == 1 else f"{n} روز دیگر" if n > 0 else f"{-n} روز پیش"


# ============================== شبکه (آب‌وهوا، ارز، اوقات) ==============================
WMO = {0: "☀️ صاف", 1: "🌤 کمی ابری", 2: "⛅️ نیمه‌ابری", 3: "☁️ ابری", 45: "🌫 مه", 48: "🌫 مه", 51: "🌦 نم‌نم",
       53: "🌦 نم‌نم", 55: "🌦 نم‌نم", 61: "🌧 باران", 63: "🌧 باران", 65: "🌧 باران شدید", 71: "🌨 برف",
       73: "🌨 برف", 75: "❄️ برف شدید", 80: "🌦 رگبار", 81: "🌧 رگبار", 82: "⛈ رگبار شدید", 95: "⛈ رعدوبرق",
       96: "⛈ تگرگ", 99: "⛈ تگرگ"}


async def http_json(url, **params):
    async with httpx.AsyncClient(timeout=12) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def geocode(name):
    j = await http_json("https://geocoding-api.open-meteo.com/v1/search", name=name, count=1, language="fa")
    res = (j.get("results") or [None])[0]
    return (res["name"], res["latitude"], res["longitude"]) if res else None


async def weather_text(uid):
    lat, lon = pref(uid, "lat"), pref(uid, "lon")
    if not lat:
        return None
    try:
        j = await http_json("https://api.open-meteo.com/v1/forecast", latitude=lat, longitude=lon, timezone="auto",
                            forecast_days=3, current="temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
                            daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code")
    except Exception as e:
        logger.warning(f"weather: {e}")
        return "☁️ دریافت آب‌وهوا ناموفق بود. چند دقیقه بعد دوباره امتحان کنید."
    c, dly = j["current"], j["daily"]
    lines = [f"☁️ <b>آب‌وهوای {esc(pref(uid, 'city'))}</b>",
             f"{WMO.get(c['weather_code'], '🌡')} | 🌡 {c['temperature_2m']:.0f}° (حس {c['apparent_temperature']:.0f}°)",
             f"💧 رطوبت {c['relative_humidity_2m']}٪ | 💨 باد {c['wind_speed_10m']:.0f} km/h", ""]
    for i, label in enumerate(("امروز", "فردا", "پس‌فردا")):
        lines.append(f"{label}: {WMO.get(dly['weather_code'][i], '')} {dly['temperature_2m_min'][i]:.0f}° تا "
                     f"{dly['temperature_2m_max'][i]:.0f}° | ☔️ {dly['precipitation_probability_max'][i] or 0}٪")
    return "\n".join(lines)


async def prayer_text(uid):
    lat, lon = pref(uid, "lat"), pref(uid, "lon")
    if not lat:
        return None
    try:
        j = await http_json("https://api.aladhan.com/v1/timings", latitude=lat, longitude=lon, method=7)
        t = j["data"]["timings"]
    except Exception as e:
        logger.warning(f"prayer: {e}")
        return "🕌 دریافت اوقات شرعی ناموفق بود."
    names = [("Fajr", "اذان صبح"), ("Sunrise", "طلوع آفتاب"), ("Dhuhr", "اذان ظهر"),
             ("Sunset", "غروب آفتاب"), ("Maghrib", "اذان مغرب"), ("Midnight", "نیمه‌شب شرعی")]
    return f"🕌 <b>اوقات شرعی {esc(pref(uid, 'city'))}</b>\n\n" + "\n".join(
        f"{fa}: <b>{t[k].split(' ')[0]}</b>" for k, fa in names)


async def crypto_text():
    try:
        j = await http_json("https://api.coingecko.com/api/v3/simple/price",
                            ids="bitcoin,ethereum,tether,solana,the-open-network,ripple",
                            vs_currencies="usd", include_24hr_change="true")
    except Exception as e:
        logger.warning(f"crypto: {e}")
        return "₿ دریافت قیمت‌ها ناموفق بود."
    names = {"bitcoin": "بیت‌کوین", "ethereum": "اتریوم", "tether": "تتر", "solana": "سولانا",
             "the-open-network": "تون‌کوین", "ripple": "ریپل"}
    out = ["₿ <b>قیمت ارزهای دیجیتال (دلار)</b>\n"]
    for k, fa in names.items():
        if k in j:
            ch = j[k].get("usd_24h_change") or 0
            out.append(f"{'🟢' if ch >= 0 else '🔴'} {fa}: <b>{j[k]['usd']:,.2f}</b> ({ch:+.1f}٪)")
    return "\n".join(out)


# ============================== ارسال و پاسخ ==============================
def main_kb(uid):
    rows = [[BTN_NOTES, BTN_TODO], [BTN_REM, BTN_EXP], [BTN_HABIT, BTN_POMO], [BTN_EVENT, BTN_TOOLS],
            [BTN_DASH, BTN_SET], [BTN_HELP]]
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


async def say(update, text, **kw):
    msg = update.message or update.callback_query.message
    return await msg.reply_text(text, **kw)


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
    db("INSERT OR IGNORE INTO users(user_id,name,joined_at) VALUES(?,?,?)", (u.id, u.full_name, time.time()))
    row = db("SELECT banned FROM users WHERE user_id=?", (u.id,), "one")
    if row and row["banned"] and not is_owner(u.id):
        await say(update, "⛔️ دسترسی شما مسدود شده است.")
        return False
    return True


async def ask(update, context, state, text):
    context.user_data["state"] = state
    await say(update, text)


# ============================== صفحه‌ها ==============================
async def show_notes(update, page=0):
    uid = update.effective_user.id
    rows = db("SELECT id,text,pinned FROM notes WHERE user_id=? ORDER BY pinned DESC,id DESC", (uid,), "all")
    chunk, page, pages = page_slice(rows, page)
    kb = [[B("➕ جدید", callback_data="nn"), B("🔎 جستجو", callback_data="nf"), B("📤 خروجی", callback_data="nx")]]
    for r in chunk:
        kb.append([B(("📌 " if r["pinned"] else "📄 ") + r["text"].replace("\n", " ")[:32], callback_data=f"nv_{r['id']}")])
    nav = nav_row("np_", page, pages)
    if nav:
        kb.append(nav)
    head = f"📝 <b>یادداشت‌های شما</b> ({len(rows)})\nبرای برچسب‌گذاری داخل متن #برچسب بنویسید." if rows else "📝 هنوز یادداشتی ندارید."
    await respond(update, head, KB(kb))


async def show_todos(update):
    uid = update.effective_user.id
    rows = db("SELECT id,text,done,prio FROM todos WHERE user_id=? ORDER BY done,prio DESC,id DESC LIMIT 30", (uid,), "all")
    kb = []
    for r in rows:
        mark = "✅" if r["done"] else ("🔴" if r["prio"] else "⬜")
        kb.append([B(f"{mark} {r['text'][:30]}", callback_data=f"tt_{r['id']}"), B("🗑", callback_data=f"td_{r['id']}")])
    kb.append([B("➕ افزودن", callback_data="tn"), B("🧹 پاک‌کردن انجام‌شده‌ها", callback_data="tc")])
    done = sum(1 for r in rows if r["done"])
    head = (f"✅ <b>کارها</b> — {done}/{len(rows)}\n{bar(done / len(rows))}\n\nروی هر کار بزنید تا وضعیتش عوض شود."
            if rows else "✅ لیست کارها خالی است.")
    await respond(update, head, KB(kb))


async def show_reminders(update):
    uid = update.effective_user.id
    rows = db("SELECT id,text,due,rep FROM reminders WHERE user_id=? AND sent=0 ORDER BY due LIMIT 30", (uid,), "all")
    lines = ["⏰ <b>یادآورهای فعال</b>\n"]
    kb = [[B("➕ یادآور جدید", callback_data="rn")]]
    reps = {"daily": "🔁 روزانه", "weekly": "🔁 هفتگی", "monthly": "🔁 ماهانه"}
    for r in rows:
        lines.append(f"• {fmt_dt(r['due'])} {reps.get(r['rep'] or '', '')}\n  {esc(r['text'][:60])}")
        kb.append([B("🗑 " + r["text"][:25], callback_data=f"rd_{r['id']}")])
    if not rows:
        lines.append("یادآور فعالی ندارید.")
    await respond(update, "\n".join(lines), KB(kb))


async def show_expenses(update):
    uid = update.effective_user.id
    text = (f"💸 <b>مدیریت هزینه‌ها</b>\n\nامروز: <b>{fmt(total_since(uid, day_start_ts()))}</b> تومان\n"
            f"این ماه: <b>{fmt(total_since(uid, month_start_ts()))}</b> تومان{budget_line(uid)}")
    kb = [[B("➕ هزینه جدید", callback_data="en")],
          [B("📊 امروز", callback_data="er_day"), B("📊 ۷ روز", callback_data="er_week"), B("📊 این ماه", callback_data="er_month")],
          [B("🧾 آخرین‌ها", callback_data="el"), B("📤 CSV", callback_data="ex")]]
    await respond(update, text, KB(kb))


async def show_last_expenses(update):
    uid = update.effective_user.id
    rows = db("SELECT id,amount,category,note,created_at FROM expenses WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,), "all")
    kb = []
    if not rows:
        text = "🧾 هنوز هزینه‌ای ثبت نشده."
    else:
        lines = ["🧾 <b>آخرین هزینه‌ها</b>\n"]
        for r in rows:
            note = f" ({esc(r['note'])})" if r["note"] else ""
            lines.append(f"• {fmt(r['amount'])} — {esc(r['category'])}{note}\n  {fmt_dt(r['created_at'])}")
            kb.append([B(f"🗑 حذف {fmt(r['amount'])}", callback_data=f"ed_{r['id']}")])
        text = "\n".join(lines)
    kb.append([B("⬅️ بازگشت", callback_data="eh")])
    await respond(update, text, KB(kb))


async def show_habits(update):
    uid = update.effective_user.id
    rows = db("SELECT id,name FROM habits WHERE user_id=? ORDER BY id", (uid,), "all")
    td, kb, done = today_iso(), [], 0
    for r in rows:
        days = habit_days(r["id"])
        ok = td in days
        done += ok
        kb.append([B(f"{'✅' if ok else '⬜'} {r['name'][:22]} 🔥{streak(days)}", callback_data=f"hb_{r['id']}"),
                   B("📅", callback_data=f"hv_{r['id']}")])
    kb.append([B("➕ عادت جدید", callback_data="hn")])
    head = (f"🎯 <b>عادت‌ها</b> — امروز {done}/{len(rows)}\n{bar(done / len(rows))}\n\nروی عادت بزنید تا امروز ثبت شود."
            if rows else "🎯 هنوز عادتی ندارید. مثلاً «مطالعه»، «ورزش» یا «آب‌خوردن» را اضافه کنید.")
    await respond(update, head, KB(kb))


async def show_habit_detail(update, hid):
    uid = update.effective_user.id
    r = db("SELECT * FROM habits WHERE id=? AND user_id=?", (hid, uid), "one")
    if not r:
        await show_habits(update)
        return
    days = habit_days(hid)
    d0, cells = now().date(), []
    for i in range(20, -1, -1):
        cells.append("🟩" if (d0 - timedelta(days=i)).isoformat() in days else "⬛️")
    grid = "\n".join("".join(cells[i:i + 7]) for i in range(0, 21, 7))
    last21 = sum(c == "🟩" for c in cells)
    text = (f"🎯 <b>{esc(r['name'])}</b>\n\n🔥 رکورد فعلی: {streak(days)} روز\n📅 ۲۱ روز اخیر: {last21}/21\n"
            f"✅ کل دفعات: {len(days)}\n\n{grid}")
    await respond(update, text, KB([[B("🗑 حذف عادت", callback_data=f"hd_{hid}"), B("⬅️ بازگشت", callback_data="hh")]]))


async def show_pomo(update):
    uid = update.effective_user.id
    row = db("SELECT * FROM pomo WHERE user_id=?", (uid,), "one")
    mins = db("SELECT COALESCE(SUM(minutes),0) s FROM focus_log WHERE user_id=? AND day=?", (uid, today_iso()), "one")["s"]
    if row:
        left = max(int((row["end_ts"] - time.time()) // 60) + 1, 0)
        kind = "فوکوس" if row["kind"] == "f" else "استراحت"
        text = f"🍅 <b>{kind} در جریان است</b>\n\n⏳ حدود {left} دقیقه مانده\n📈 تمرکز امروز: {mins} دقیقه"
        kb = [[B("🔄 بروزرسانی", callback_data="pr"), B("⏹ توقف", callback_data="po_stop")]]
    else:
        text = f"🍅 <b>تایمر تمرکز</b>\n\n📈 تمرکز امروز: <b>{mins}</b> دقیقه\n\nیک جلسه شروع کنید:"
        kb = [[B("🍅 ۲۵ دقیقه", callback_data="po_25_f"), B("🍅 ۵۰ دقیقه", callback_data="po_50_f")],
              [B("☕️ ۵ دقیقه استراحت", callback_data="po_5_b"), B("🛋 ۱۵ دقیقه استراحت", callback_data="po_15_b")]]
    await respond(update, text, KB(kb))


async def show_events(update):
    uid = update.effective_user.id
    td = now().date()
    items = []
    for r in db("SELECT * FROM events WHERE user_id=?", (uid,), "all"):
        o = next_occ(r, td)
        if o:
            items.append(((o - td).days, r, o))
    items.sort(key=lambda x: (x[0] < 0, abs(x[0])))
    lines, kb = ["🎂 <b>مناسبت‌ها و شمارش معکوس</b>\n"], [[B("➕ مناسبت جدید", callback_data="vn")]]
    for days, r, o in items[:20]:
        lines.append(f"• {esc(r['title'])} {'🔁' if r['yearly'] else ''}\n  {fmt_day(o)} — <b>{days_text(days)}</b>")
        kb.append([B("🗑 " + r["title"][:25], callback_data=f"vd_{r['id']}")])
    if not items:
        lines.append("هنوز چیزی ثبت نشده. تولد، امتحان، سفر و... را اضافه کنید.")
    await respond(update, "\n".join(lines), KB(kb))


async def show_tools(update):
    kb = [[B("🧮 ماشین‌حساب", callback_data="t_calc"), B("📏 مبدل واحد", callback_data="t_unit")],
          [B("🔐 ساخت رمز", callback_data="t_pass"), B("🔲 ساخت QR", callback_data="t_qr")],
          [B("🔄 تبدیل تاریخ", callback_data="t_date"), B("🔢 شمارش متن", callback_data="t_count")],
          [B("🎯 تصمیم‌گیر", callback_data="t_decide"), B("💬 جمله‌ی روز", callback_data="t_quote")],
          [B("🎲 تاس", callback_data="t_dice"), B("🪙 شیر یا خط", callback_data="t_coin")],
          [B("☁️ آب‌وهوا", callback_data="o_wx"), B("🕌 اوقات شرعی", callback_data="o_pray")],
          [B("₿ ارز دیجیتال", callback_data="o_crypto")]]
    await respond(update, "🧰 <b>ابزارها</b>\n\nیکی را انتخاب کنید:", KB(kb))


def dashboard_text(uid):
    td = today_iso()
    t = db("SELECT COUNT(*) n, COALESCE(SUM(done),0) d FROM todos WHERE user_id=?", (uid,), "one")
    hs = db("SELECT id FROM habits WHERE user_id=?", (uid,), "all")
    hdone, best = 0, 0
    for h in hs:
        days = habit_days(h["id"])
        hdone += td in days
        best = max(best, streak(days))
    foc = db("SELECT COALESCE(SUM(minutes),0) s FROM focus_log WHERE user_id=? AND day=?", (uid, td), "one")["s"]
    nn = db("SELECT COUNT(*) n FROM notes WHERE user_id=?", (uid,), "one")["n"]
    rem = db("SELECT COUNT(*) n FROM reminders WHERE user_id=? AND sent=0", (uid,), "one")["n"]
    nxt, today_d = None, now().date()
    for r in db("SELECT * FROM events WHERE user_id=?", (uid,), "all"):
        o = next_occ(r, today_d)
        if o and o >= today_d and (nxt is None or o < nxt[0]):
            nxt = (o, r["title"])
    lines = [f"📊 <b>داشبورد</b> — {fmt_day(today_d)}\n",
             f"✅ کارها: {t['d']}/{t['n']} {bar(t['d'] / t['n']) if t['n'] else ''}",
             f"🎯 عادت‌های امروز: {hdone}/{len(hs)} (بهترین رکورد 🔥{best})",
             f"🍅 تمرکز امروز: {foc} دقیقه",
             f"💸 هزینه‌ی امروز: {fmt(total_since(uid, day_start_ts()))} | ماه: {fmt(total_since(uid, month_start_ts()))}{budget_line(uid)}",
             f"⏰ یادآورهای فعال: {rem} | 📝 یادداشت‌ها: {nn}"]
    if nxt:
        lines.append(f"🎂 مناسبت بعدی: {esc(nxt[1])} — {days_text((nxt[0] - today_d).days)}")
    return "\n".join(lines)


async def show_settings(update):
    uid = update.effective_user.id
    on = pref(uid, "brief_on", "0") == "1"
    b = int(pref(uid, "budget", "0") or 0)
    text = (f"⚙️ <b>تنظیمات</b>\n\n📍 شهر: {esc(pref(uid, 'city', 'تنظیم نشده'))}\n"
            f"🌅 گزارش صبحگاهی: {'روشن' if on else 'خاموش'} (ساعت {pref(uid, 'brief_time', '08:00')})\n"
            f"🎯 بودجه‌ی ماهانه: {fmt(b) + ' تومان' if b else 'تنظیم نشده'}")
    kb = [[B("📍 تنظیم شهر", callback_data="s_city")],
          [B("🌅 خاموش/روشن گزارش صبحگاهی", callback_data="s_brief"), B("🕗 ساعت گزارش", callback_data="s_time")],
          [B("🎯 تنظیم بودجه‌ی ماهانه", callback_data="s_budget")],
          [B("🗑 حذف همه‌ی داده‌های من", callback_data="s_wipe")]]
    if is_owner(uid):
        kb.insert(3, [B("💾 دریافت فایل پشتیبان", callback_data="s_bak")])
    await respond(update, text, KB(kb))


async def show_today(update):
    n = now()
    await say(update, f"📅 <b>تاریخ امروز</b>\n\n🗓 شمسی: {fmt_day(n.date())}\n🌍 میلادی: {n:%Y-%m-%d}\n🕒 ساعت: {n:%H:%M:%S}",
              parse_mode=HTML)


async def show_help(update):
    await say(update, HELP_TEXT, parse_mode=HTML)


async def show_admin(update):
    await say(update, ADMIN_HELP, parse_mode=HTML)


async def show_dash(update):
    await say(update, dashboard_text(update.effective_user.id), parse_mode=HTML)


MENU_ACTIONS = {BTN_NOTES: show_notes, BTN_TODO: show_todos, BTN_REM: show_reminders, BTN_EXP: show_expenses,
                BTN_HABIT: show_habits, BTN_POMO: show_pomo, BTN_EVENT: show_events, BTN_TOOLS: show_tools,
                BTN_DASH: show_dash, BTN_SET: show_settings, BTN_HELP: show_help}


# ============================== حالت‌ها (ورودی متنی) ==============================
async def st_note_add(update, context, text):
    if len(text) > 3500:
        await update.message.reply_text("❌ یادداشت خیلی بلند است (حداکثر 3500 کاراکتر).")
        return
    db("INSERT INTO notes(user_id,text,created_at) VALUES(?,?,?)", (update.effective_user.id, text, time.time()))
    context.user_data.pop("state", None)
    await update.message.reply_text("✅ یادداشت ذخیره شد.")
    await show_notes(update, 0)


async def st_note_find(update, context, text):
    context.user_data.pop("state", None)
    like = "%" + text.replace("%", "").replace("_", "") + "%"
    rows = db("SELECT id,text FROM notes WHERE user_id=? AND text LIKE ? ORDER BY id DESC LIMIT 20",
              (update.effective_user.id, like), "all")
    if not rows:
        await update.message.reply_text("🔎 چیزی پیدا نشد.")
        return
    kb = [[B("📄 " + r["text"].replace("\n", " ")[:32], callback_data=f"nv_{r['id']}")] for r in rows]
    await update.message.reply_text(f"🔎 {len(rows)} نتیجه:", reply_markup=KB(kb))


async def st_todo_add(update, context, text):
    for ln in [x.strip() for x in text.splitlines() if x.strip()][:20]:
        prio = 1 if ln.startswith("!") else 0
        db("INSERT INTO todos(user_id,text,prio,created_at) VALUES(?,?,?,?)",
           (update.effective_user.id, ln.lstrip("! ")[:200] or ln[:200], prio, time.time()))
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
    kb = [[B("یک‌بار", callback_data="rr_none"), B("🔁 روزانه", callback_data="rr_daily")],
          [B("🔁 هفتگی", callback_data="rr_weekly"), B("🔁 ماهانه", callback_data="rr_monthly")]]
    await update.message.reply_text(f"🕒 {fmt_dt(due)}\n\nتکرار شود؟", reply_markup=KB(kb))


async def st_exp_amount(update, context, text):
    amount = parse_amount(text)
    if amount is None:
        await update.message.reply_text("❌ مبلغ نامعتبر است. مثال: 50000 یا 50k یا 2m")
        return
    context.user_data["exp_amount"] = amount
    context.user_data.pop("state", None)
    kb, row = [], []
    for i, c in enumerate(CATEGORIES):
        row.append(B(c, callback_data=f"ec_{i}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    await update.message.reply_text(f"💰 {fmt(amount)} تومان\n\nدسته را انتخاب کنید:", reply_markup=KB(kb))


async def st_exp_note(update, context, text):
    amount = context.user_data.pop("exp_amount", None)
    cat = context.user_data.pop("exp_cat", None)
    context.user_data.pop("state", None)
    if not amount or not cat:
        await update.message.reply_text("نشست منقضی شد. دوباره از منوی هزینه‌ها شروع کنید.")
        return
    uid = update.effective_user.id
    db("INSERT INTO expenses(user_id,amount,category,note,created_at) VALUES(?,?,?,?,?)",
       (uid, amount, cat, "" if text in ("-", "ندارم") else text[:200], time.time()))
    await update.message.reply_text(f"✅ ثبت شد: {fmt(amount)} تومان — {cat}\n📊 مجموع امروز: {fmt(total_since(uid, day_start_ts()))} تومان")
    b = int(pref(uid, "budget", "0") or 0)
    if b:
        p = total_since(uid, month_start_ts()) / b
        jy, jm, _ = jnow()
        lvl = 100 if p >= 1 else 80 if p >= 0.8 else 0
        if lvl and once(f"bud:{uid}:{jy}{jm}:{lvl}"):
            await update.message.reply_text(
                ("🚨 بودجه‌ی این ماه تمام شد!" if lvl == 100 else "⚠️ بیش از ۸۰٪ بودجه‌ی این ماه مصرف شده.") + budget_line(uid))


async def st_habit_add(update, context, text):
    context.user_data.pop("state", None)
    db("INSERT INTO habits(user_id,name,created_at) VALUES(?,?,?)", (update.effective_user.id, text[:60], time.time()))
    await show_habits(update)


async def st_ev_title(update, context, text):
    context.user_data["ev_title"] = text[:80]
    context.user_data["state"] = "ev_date"
    await update.message.reply_text("📅 تاریخ را بفرستید (شمسی یا میلادی)\nمثال: 1405/02/15 یا 2026-12-25")


async def st_ev_date(update, context, text):
    p = parse_ymd(text)
    if not p:
        await update.message.reply_text("❌ تاریخ نامعتبر. مثال: 1405/02/15")
        return
    context.user_data["ev_date"] = p
    context.user_data.pop("state", None)
    kb = [[B("🔁 هر سال تکرار شود", callback_data="vy_1"), B("فقط یک‌بار", callback_data="vy_0")]]
    await update.message.reply_text("تکرار سالانه (مثلاً برای تولد)؟", reply_markup=KB(kb))


async def st_calc(update, context, text):
    try:
        await update.message.reply_text(f"🧮 <code>{esc(calc_value(text))}</code>", parse_mode=HTML)
    except Exception:
        await update.message.reply_text("❌ عبارت نامعتبر است. مثال: (2+3)*4 یا sqrt(16)")


async def st_unit(update, context, text):
    await update.message.reply_text(convert_units(text) or "❌ فرمت نامعتبر. مثال: 10 km to mi ، 5 kg to lb ، 100 c to f ، 2 gb to mb")


async def st_date_conv(update, context, text):
    await update.message.reply_text(convert_date(text) or "❌ فرمت نامعتبر. مثال: 1405/09/01 یا 2026-12-01")


async def st_text_count(update, context, text):
    context.user_data.pop("state", None)
    no_space = len(re.sub(r"\s", "", text))
    await update.message.reply_text(
        f"🔢 <b>نتیجه شمارش</b>\n\nکاراکتر: {len(text)}\nبدون فاصله: {no_space}\nکلمه: {len(text.split())}\nخط: {len(text.splitlines())}",
        parse_mode=HTML)


async def st_qr(update, context, text):
    context.user_data.pop("state", None)
    try:
        await update.message.reply_photo(
            photo="https://api.qrserver.com/v1/create-qr-code/?size=600x600&data=" + quote(text[:800]), caption="🔲 QR شما آماده است")
    except Exception as e:
        await update.message.reply_text(f"❌ ساخت QR ناموفق بود: {e}")


async def st_decide(update, context, text):
    context.user_data.pop("state", None)
    opts = [x.strip() for x in re.split(r"[\n,،]", text) if x.strip()]
    if len(opts) < 2:
        await update.message.reply_text("حداقل دو گزینه بفرستید (با کاما یا هر کدام در یک خط).")
        return
    await update.message.reply_text(f"🎯 انتخاب من: <b>{esc(random.choice(opts))}</b>", parse_mode=HTML)


async def st_city(update, context, text):
    context.user_data.pop("state", None)
    uid = update.effective_user.id
    try:
        g = await geocode(text)
    except Exception as e:
        await update.message.reply_text(f"❌ جستجوی شهر ناموفق بود: {e}")
        return
    if not g:
        await update.message.reply_text("❌ شهر پیدا نشد. اسم را انگلیسی یا فارسی دقیق‌تر بنویسید.")
        return
    set_pref(uid, "city", g[0])
    set_pref(uid, "lat", g[1])
    set_pref(uid, "lon", g[2])
    await update.message.reply_text(f"📍 شهر روی «{g[0]}» تنظیم شد.")
    await update.message.reply_text(await weather_text(uid), parse_mode=HTML)


async def st_set_time(update, context, text):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", nd(text).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        await update.message.reply_text("❌ ساعت نامعتبر. مثال: 07:30")
        return
    context.user_data.pop("state", None)
    set_pref(update.effective_user.id, "brief_time", f"{int(m.group(1)):02d}:{m.group(2)}")
    await update.message.reply_text("✅ ساعت گزارش صبحگاهی تنظیم شد.")


async def st_set_budget(update, context, text):
    a = parse_amount(text)
    if text.strip() in ("0", "-"):
        a = 0
    if a is None:
        await update.message.reply_text("❌ مبلغ نامعتبر. مثال: 5m (برای حذف: 0)")
        return
    context.user_data.pop("state", None)
    set_pref(update.effective_user.id, "budget", a)
    await update.message.reply_text(f"✅ بودجه‌ی ماهانه: {fmt(a)} تومان")


async def st_post_button(update, context, text):
    p = context.user_data.setdefault("post", {})
    if text.strip().lower() in ("ندارم", "no", "-"):
        p.pop("btn_text", None)
        p.pop("btn_url", None)
    else:
        parts = [x.strip() for x in text.split("|")]
        if len(parts) != 2 or not parts[0] or not re.match(r"^(https?://|tg://|t\.me/)", parts[1]):
            await update.message.reply_text("❌ فرمت درست: متن دکمه | لینک\nیا بنویسید: ندارم")
            return
        p["btn_text"] = parts[0][:60]
        p["btn_url"] = parts[1] if not parts[1].startswith("t.me/") else "https://" + parts[1]
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
    pid = db("INSERT INTO posts(src_chat,src_msg,target,btn_text,btn_url,due,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
             (p["src_chat"], p["src_msg"], p["target"], p.get("btn_text"), p.get("btn_url"), due, "scheduled", time.time()))
    context.user_data.pop("state", None)
    context.user_data.pop("post", None)
    await update.message.reply_text(f"✅ پست #{pid} برای {fmt_dt(due)} زمان‌بندی شد. لغو: /posts")


async def st_post_content(update, context):
    msg = update.message
    context.user_data["post"] = {"src_chat": msg.chat_id, "src_msg": msg.message_id}
    chans = db("SELECT chat_id,title FROM channels", (), "all")
    if not chans:
        context.user_data.pop("state", None)
        await msg.reply_text("هنوز کانالی ندارید. اول /addchannel @channel را بزنید.")
        return
    context.user_data["state"] = "post_channel"
    kb = [[B(c["title"] or str(c["chat_id"]), callback_data=f"pch_{c['chat_id']}")] for c in chans]
    await msg.reply_text("📢 این پست به کدام کانال برود؟", reply_markup=KB(kb))


STATE_HANDLERS = {
    "note_add": st_note_add, "note_find": st_note_find, "todo_add": st_todo_add, "rem_text": st_rem_text,
    "rem_when": st_rem_when, "exp_amount": st_exp_amount, "exp_note": st_exp_note, "habit_add": st_habit_add,
    "ev_title": st_ev_title, "ev_date": st_ev_date, "calc": st_calc, "unit": st_unit, "date_conv": st_date_conv,
    "text_count": st_text_count, "qr": st_qr, "decide": st_decide, "city": st_city, "set_time": st_set_time,
    "set_budget": st_set_budget, "post_button": st_post_button, "post_time": st_post_time,
}
OWNER_STATES = {"post_button", "post_time"}


# ============================== هندلرهای پیام ==============================
async def cmd_start(update, context):
    if not update.message or not await ensure_user(update, context):
        return
    context.user_data.clear()
    u = update.effective_user
    await update.message.reply_text(
        f"👋 سلام <b>{esc(u.first_name)}</b>!\n\nمن دستیار هوشمند توام: یادداشت، کار، یادآور، هزینه و بودجه، عادت‌ها، "
        "تایمر تمرکز، مناسبت‌ها، آب‌وهوا، اوقات شرعی و کلی ابزار دیگر.\n\n"
        "💡 نکته: از «⚙️ تنظیمات» شهرت را بده تا گزارش صبحگاهی هم برایت بفرستم.\nاز منوی پایین شروع کن 👇",
        parse_mode=HTML, reply_markup=main_kb(u.id))


async def cmd_help(update, context):
    if await ensure_user(update, context):
        await show_help(update)


async def cmd_cancel(update, context):
    if await ensure_user(update, context):
        context.user_data.clear()
        await update.message.reply_text("لغو شد. منوی اصلی 👇", reply_markup=main_kb(update.effective_user.id))


async def cmd_id(update, context):
    await update.message.reply_text(f"🆔 آیدی عددی شما: <code>{update.effective_user.id}</code>", parse_mode=HTML)


async def cmd_today(update, context):
    if await ensure_user(update, context):
        await show_today(update)


async def cmd_calc(update, context):
    if not await ensure_user(update, context):
        return
    if not context.args:
        await ask(update, context, "calc", "🧮 عبارت را بفرستید (برای خروج /cancel).")
        return
    await st_calc(update, context, " ".join(context.args))


async def text_router(update, context):
    msg, uid = update.message, update.effective_user.id
    text = (msg.text or "").strip()
    if text in MENU_BTNS:
        context.user_data.pop("state", None)
        if text == BTN_ADMIN:
            if is_owner(uid):
                await show_admin(update)
        else:
            await MENU_ACTIONS[text](update)
        return
    st = context.user_data.get("state")
    handler = STATE_HANDLERS.get(st)
    if handler and (st not in OWNER_STATES or is_owner(uid)):
        await handler(update, context, text)
        return
    if text.startswith("+ ") and len(text) > 2:
        await st_note_add(update, context, text[2:].strip())
        return
    if text.startswith("- ") and len(text) > 2:
        await st_todo_add(update, context, text[2:].strip())
        return
    await msg.reply_text("از منوی پایین یکی را انتخاب کنید 👇\n(یا با «+ متن» یادداشت و با «- متن» کار اضافه کنید)",
                         reply_markup=main_kb(uid))


async def do_restore(update, context):
    doc = update.message.document
    data = bytes(await (await doc.get_file()).download_as_bytearray())
    if not data.startswith(b"SQLite format 3\x00"):
        await update.message.reply_text("❌ این فایل یک دیتابیس معتبر نیست.")
        return
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(DB_PATH + ext)
        except FileNotFoundError:
            pass
    with open(DB_PATH, "wb") as f:
        f.write(data)
    init_db()
    await update.message.reply_text("✅ پشتیبان با موفقیت بازگردانی شد.")


async def msg_router(update, context):
    if not update.message or not await ensure_user(update, context):
        return
    uid = update.effective_user.id
    msg = update.message
    if is_owner(uid) and msg.document and (msg.caption or "").strip().startswith("/restore"):
        await do_restore(update, context)
        return
    if context.user_data.get("state") == "post_content" and is_owner(uid):
        await st_post_content(update, context)
        return
    if msg.text:
        await text_router(update, context)
        return
    await msg.reply_text("از منوی پایین استفاده کنید 👇", reply_markup=main_kb(uid))


# ============================== کال‌بک‌ها ==============================
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
    head, _, arg = d.partition("_")
    parts = arg.split("_")
    msg = q.message

    # ---- یادداشت‌ها ----
    if head == "np":
        await show_notes(update, int(arg))
    elif head == "nn":
        await ask(update, context, "note_add", "📝 متن یادداشت را بفرستید:")
    elif head == "nf":
        await ask(update, context, "note_find", "🔎 کلمه یا #برچسب را بفرستید:")
    elif head == "nx":
        rows = db("SELECT text,created_at FROM notes WHERE user_id=? ORDER BY id", (uid,), "all")
        body = "\n\n".join(f"[{fmt_dt(r['created_at'])}]\n{r['text']}" for r in rows) or "(خالی)"
        await msg.reply_document(document=io.BytesIO(body.encode("utf-8")), filename="notes.txt")
    elif head == "nv":
        r = db("SELECT * FROM notes WHERE id=? AND user_id=?", (int(arg), uid), "one")
        if not r:
            await msg.reply_text("یادداشت پیدا نشد.")
            return
        tags = " ".join(sorted(set(re.findall(r"#\w+", r["text"]))))
        kb = [[B("📌 برداشتن پین" if r["pinned"] else "📌 پین", callback_data=f"nt_{r['id']}"),
               B("🗑 حذف", callback_data=f"nd_{r['id']}"), B("⬅️ بازگشت", callback_data="np_0")]]
        await respond(update, f"📄 <b>یادداشت #{r['id']}</b>\n\n{esc(r['text'])}\n\n{esc(tags)}\n🕒 {fmt_dt(r['created_at'])}", KB(kb))
    elif head == "nt":
        db("UPDATE notes SET pinned=1-pinned WHERE id=? AND user_id=?", (int(arg), uid))
        await show_notes(update, 0)
    elif head == "nd":
        db("DELETE FROM notes WHERE id=? AND user_id=?", (int(arg), uid))
        await show_notes(update, 0)

    # ---- کارها ----
    elif head == "tn":
        await ask(update, context, "todo_add", "✅ کارها را بفرستید (هر خط یک کار، اول خط ! برای مهم):")
    elif head == "tt":
        db("UPDATE todos SET done=1-done WHERE id=? AND user_id=?", (int(arg), uid))
        await show_todos(update)
    elif head == "td":
        db("DELETE FROM todos WHERE id=? AND user_id=?", (int(arg), uid))
        await show_todos(update)
    elif head == "tc":
        db("DELETE FROM todos WHERE user_id=? AND done=1", (uid,))
        await show_todos(update)

    # ---- یادآورها ----
    elif head == "rn":
        await ask(update, context, "rem_text", "⏰ متن یادآوری را بفرستید:")
    elif head == "rd":
        db("DELETE FROM reminders WHERE id=? AND user_id=?", (int(arg), uid))
        await show_reminders(update)
    elif head == "rr":
        text, due = context.user_data.pop("rem_text", None), context.user_data.pop("rem_due", None)
        if not text or not due:
            await msg.reply_text("نشست منقضی شد. دوباره یادآور جدید بسازید.")
            return
        db("INSERT INTO reminders(user_id,text,due,rep) VALUES(?,?,?,?)",
           (uid, text, due, arg if arg in ("daily", "weekly", "monthly") else None))
        await respond(update, f"✅ یادآور ثبت شد\n🕒 {fmt_dt(due)}\n📝 {esc(text)}")
    elif head == "sz":
        r = db("SELECT text FROM reminders WHERE id=? AND user_id=?", (int(parts[0]), uid), "one")
        if not r:
            await respond(update, "این یادآور دیگر وجود ندارد.")
            return
        if parts[1] == "t":
            due = (now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
        else:
            due = time.time() + int(parts[1]) * 60
        db("INSERT INTO reminders(user_id,text,due) VALUES(?,?,?)", (uid, r["text"], due))
        await respond(update, f"😴 تعویق شد تا {fmt_dt(due)}\n📝 {esc(r['text'])}")
    elif head == "rk":
        await respond(update, "✅ انجام شد.")

    # ---- هزینه‌ها ----
    elif head == "en":
        await ask(update, context, "exp_amount", "💰 مبلغ را به تومان بفرستید (مثال: 50000 یا 50k یا 2m):")
    elif head == "ec":
        i = int(arg)
        if not context.user_data.get("exp_amount") or not 0 <= i < len(CATEGORIES):
            await msg.reply_text("نشست منقضی شد. دوباره شروع کنید.")
            return
        context.user_data["exp_cat"] = CATEGORIES[i]
        context.user_data["state"] = "exp_note"
        await respond(update, f"{CATEGORIES[i]} انتخاب شد.\n\nتوضیح کوتاه بنویسید یا - بفرستید.")
    elif head == "er":
        since, title = {"day": (day_start_ts(), "📊 گزارش امروز"), "week": (time.time() - 7 * 86400, "📊 گزارش ۷ روز اخیر"),
                        "month": (month_start_ts(), "📊 گزارش این ماه (شمسی)")}[arg]
        await respond(update, expense_report(uid, since, title, arg == "month"), KB([[B("⬅️ بازگشت", callback_data="eh")]]))
    elif head == "el":
        await show_last_expenses(update)
    elif head == "eh":
        await show_expenses(update)
    elif head == "ed":
        db("DELETE FROM expenses WHERE id=? AND user_id=?", (int(arg), uid))
        await show_last_expenses(update)
    elif head == "ex":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["date", "amount", "category", "note"])
        for r in db("SELECT * FROM expenses WHERE user_id=? ORDER BY id", (uid,), "all"):
            w.writerow([datetime.fromtimestamp(r["created_at"], TZ).strftime("%Y-%m-%d %H:%M"), r["amount"], r["category"], r["note"]])
        await msg.reply_document(document=io.BytesIO(("\ufeff" + out.getvalue()).encode("utf-8")), filename="expenses.csv")

    # ---- عادت‌ها ----
    elif head == "hb":
        hid = int(arg)
        if db("SELECT 1 FROM habits WHERE id=? AND user_id=?", (hid, uid), "one"):
            if db("DELETE FROM habit_logs WHERE habit_id=? AND day=?", (hid, today_iso())) == 0:
                db("INSERT INTO habit_logs(habit_id,day) VALUES(?,?)", (hid, today_iso()))
        await show_habits(update)
    elif head == "hv":
        await show_habit_detail(update, int(arg))
    elif head == "hd":
        hid = int(arg)
        if db("DELETE FROM habits WHERE id=? AND user_id=?", (hid, uid)):
            db("DELETE FROM habit_logs WHERE habit_id=?", (hid,))
        await show_habits(update)
    elif head == "hn":
        await ask(update, context, "habit_add", "🎯 اسم عادت را بفرستید (مثلاً: مطالعه ۳۰ دقیقه):")
    elif head == "hh":
        await show_habits(update)

    # ---- تمرکز ----
    elif head == "po":
        if arg == "stop":
            db("DELETE FROM pomo WHERE user_id=?", (uid,))
        else:
            db("INSERT OR REPLACE INTO pomo(user_id,end_ts,kind,minutes) VALUES(?,?,?,?)",
               (uid, time.time() + int(parts[0]) * 60, parts[1], int(parts[0])))
        await show_pomo(update)
    elif head == "pr":
        await show_pomo(update)

    # ---- مناسبت‌ها ----
    elif head == "vn":
        await ask(update, context, "ev_title", "🎂 عنوان مناسبت را بفرستید (مثلاً: تولد مامان):")
    elif head == "vy":
        title, p = context.user_data.pop("ev_title", None), context.user_data.pop("ev_date", None)
        if not title or not p:
            await msg.reply_text("نشست منقضی شد. دوباره شروع کنید.")
            return
        db("INSERT INTO events(user_id,title,y,m,d,cal,yearly) VALUES(?,?,?,?,?,?,?)",
           (uid, title, p[0], p[1], p[2], p[3], int(arg)))
        await show_events(update)
    elif head == "vd":
        db("DELETE FROM events WHERE id=? AND user_id=?", (int(arg), uid))
        await show_events(update)

    # ---- ابزارها ----
    elif head == "t":
        if arg == "pass":
            await respond(update, "🔐 طول رمز را انتخاب کنید:", KB([[B(str(n), callback_data=f"pw_{n}") for n in (8, 12, 16, 24)]]))
        elif arg == "dice":
            await msg.reply_dice()
        elif arg == "coin":
            await msg.reply_text(random.choice(["🪙 شیر", "🪙 خط"]))
        elif arg == "quote":
            await msg.reply_text("💬 " + random.choice(QUOTES))
        else:
            prompts = {"calc": ("calc", "🧮 عبارت را بفرستید (مثال: (2+3)*4). برای خروج /cancel"),
                       "unit": ("unit", "📏 بفرستید: مقدار + واحد + to + واحد\nمثال: 10 km to mi ، 5 kg to lb ، 100 c to f ، 2 gb to mb"),
                       "qr": ("qr", "🔲 متن یا لینکی که می‌خواهید QR شود را بفرستید:"),
                       "date": ("date_conv", "🔄 تاریخ را بفرستید (شمسی یا میلادی)\nمثال: 1405/09/01 یا 2026-12-01"),
                       "count": ("text_count", "🔢 متن را بفرستید:"),
                       "decide": ("decide", "🎯 گزینه‌ها را بفرستید (با کاما یا هر کدام در یک خط):")}
            if arg in prompts:
                await ask(update, context, *prompts[arg])
    elif head == "pw":
        await msg.reply_text(f"🔐 رمز شما:\n<code>{esc(make_password(min(max(int(arg), 8), 64)))}</code>", parse_mode=HTML)
    elif head == "o":
        if arg == "crypto":
            await msg.reply_text(await crypto_text(), parse_mode=HTML)
        elif not pref(uid, "lat"):
            await ask(update, context, "city", "📍 اسم شهرتان را بفرستید (مثلاً: تهران یا Isfahan):")
        else:
            txt = await (weather_text(uid) if arg == "wx" else prayer_text(uid))
            await msg.reply_text(txt, parse_mode=HTML, reply_markup=KB([[B("📍 تغییر شهر", callback_data="s_city")]]))

    # ---- تنظیمات ----
    elif head == "s":
        if arg == "city":
            await ask(update, context, "city", "📍 اسم شهرتان را بفرستید (مثلاً: تهران یا Isfahan):")
        elif arg == "brief":
            set_pref(uid, "brief_on", "0" if pref(uid, "brief_on", "0") == "1" else "1")
            await show_settings(update)
        elif arg == "time":
            await ask(update, context, "set_time", "🕗 ساعت گزارش صبحگاهی را بفرستید (مثال: 07:30):")
        elif arg == "budget":
            await ask(update, context, "set_budget", "🎯 بودجه‌ی ماهانه را به تومان بفرستید (مثال: 5m). برای حذف: 0")
        elif arg == "bak" and is_owner(uid):
            await msg.reply_document(document=io.BytesIO(make_backup()), filename=f"backup_{now():%Y%m%d_%H%M}.db")
        elif arg == "wipe":
            await respond(update, "⚠️ همه‌ی یادداشت‌ها، کارها، یادآورها، هزینه‌ها، عادت‌ها و تنظیمات شما برای همیشه پاک می‌شود. مطمئنید؟",
                          KB([[B("🗑 بله، پاک کن", callback_data="s_wipeyes"), B("انصراف", callback_data="s_no")]]))
        elif arg == "wipeyes":
            db("DELETE FROM habit_logs WHERE habit_id IN (SELECT id FROM habits WHERE user_id=?)", (uid,))
            for t in USER_TABLES:
                db(f"DELETE FROM {t} WHERE user_id=?", (uid,))
            await respond(update, "🗑 همه‌ی داده‌های شما پاک شد.")
        else:
            await show_settings(update)

    # ---- پست‌گذاری (مدیر) ----
    elif head == "pch" and is_owner(uid):
        p = context.user_data.get("post")
        if not p or "src_msg" not in p:
            await msg.reply_text("نشست منقضی شد. دوباره /newpost را بزنید.")
            return
        p["target"] = int(arg)
        context.user_data["state"] = "post_button"
        await respond(update, "🔘 دکمه زیر پست می‌خواهید؟\n\nبفرستید: <code>متن دکمه | لینک</code>\nیا بنویسید: ندارم")
    elif head == "pcx" and is_owner(uid):
        ok = db("UPDATE posts SET status='cancelled' WHERE id=? AND status='scheduled'", (int(arg),))
        await respond(update, "🚫 پست لغو شد." if ok else "این پست دیگر قابل لغو نیست.")


# ============================== دستورات مدیر ==============================
async def cmd_adminhelp(update, context):
    await show_admin(update)


async def cmd_stats(update, context):
    def n(sql):
        return db(sql, (), "one")["c"]
    sched = n("SELECT COUNT(*) c FROM posts WHERE status='scheduled'")
    await update.message.reply_text(
        "📊 <b>آمار ربات</b>\n\n"
        f"👥 کاربران: {n('SELECT COUNT(*) c FROM users')}\n"
        f"📝 یادداشت‌ها: {n('SELECT COUNT(*) c FROM notes')}\n"
        f"✅ کارها: {n('SELECT COUNT(*) c FROM todos')}\n"
        f"⏰ یادآورهای فعال: {n('SELECT COUNT(*) c FROM reminders WHERE sent=0')}\n"
        f"💸 هزینه‌ها: {n('SELECT COUNT(*) c FROM expenses')}\n"
        f"🎯 عادت‌ها: {n('SELECT COUNT(*) c FROM habits')}\n"
        f"🎂 مناسبت‌ها: {n('SELECT COUNT(*) c FROM events')}\n"
        f"📢 کانال‌ها: {n('SELECT COUNT(*) c FROM channels')}\n"
        f"🗓 پست‌های زمان‌بندی‌شده: {sched}", parse_mode=HTML)


async def cmd_backup(update, context):
    await update.message.reply_document(document=io.BytesIO(make_backup()), filename=f"backup_{now():%Y%m%d_%H%M}.db")


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
    db("INSERT OR IGNORE INTO users(user_id,name,joined_at) VALUES(?,?,?)", (uid, "", time.time()))
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
        await update.message.reply_text("استفاده: /addchannel @channel (ربات باید ادمین با دسترسی Post messages باشد)")
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
    db("INSERT OR REPLACE INTO channels(chat_id,title) VALUES(?,?)", (chat.id, chat.title or str(chat.id)))
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
    rows = db("SELECT chat_id,title FROM channels", (), "all")
    await update.message.reply_text(
        "📢 کانال‌ها:\n" + "\n".join(f"• {r['title']} ({r['chat_id']})" for r in rows) if rows else "کانالی ثبت نشده. /addchannel @channel")


async def cmd_newpost(update, context):
    if not db("SELECT 1 FROM channels", (), "one"):
        await update.message.reply_text("اول یک کانال اضافه کنید: /addchannel @channel")
        return
    context.user_data["state"] = "post_content"
    context.user_data["post"] = {}
    await update.message.reply_text("📤 پیامی را که می‌خواهید تو کانال برود بفرستید (متن، عکس، ویدیو، فایل...).\nبرای انصراف: /cancel")


async def cmd_posts(update, context):
    rows = db("SELECT * FROM posts WHERE status='scheduled' ORDER BY due LIMIT 20", (), "all")
    if not rows:
        await update.message.reply_text("پست زمان‌بندی‌شده‌ای وجود ندارد.")
        return
    lines, kb = ["🗓 <b>پست‌های زمان‌بندی‌شده</b>\n"], []
    for r in rows:
        lines.append(f"#{r['id']} — {fmt_dt(r['due'])}")
        kb.append([B(f"🚫 لغو #{r['id']}", callback_data=f"pcx_{r['id']}")])
    await update.message.reply_text("\n".join(lines), parse_mode=HTML, reply_markup=KB(kb))


# ============================== زمان‌بند پس‌زمینه ==============================
def next_due(due, rep, ref):
    dt = datetime.fromtimestamp(due, TZ)
    while dt.timestamp() <= ref:
        dt = dt + timedelta(days=1 if rep == "daily" else 7) if rep != "monthly" else add_months(dt, 1)
    return dt.timestamp()


async def build_briefing(uid):
    n = now()
    lines = [f"🌅 <b>صبح بخیر!</b>\n🗓 {fmt_day(n.date())}\n"]
    todos = db("SELECT text,prio FROM todos WHERE user_id=? AND done=0 ORDER BY prio DESC,id LIMIT 5", (uid,), "all")
    if todos:
        lines.append(f"✅ <b>کارهای باقی‌مانده</b> ({len(todos)}+):")
        lines += [f"  {'🔴' if r['prio'] else '⬜'} {esc(r['text'][:40])}" for r in todos[:3]]
    end = n.replace(hour=23, minute=59, second=59).timestamp()
    rems = db("SELECT text,due FROM reminders WHERE user_id=? AND sent=0 AND due<=? ORDER BY due", (uid, end), "all")
    if rems:
        lines.append("\n⏰ <b>یادآورهای امروز:</b>")
        lines += [f"  {datetime.fromtimestamp(r['due'], TZ):%H:%M} — {esc(r['text'][:40])}" for r in rems[:4]]
    td = n.date()
    soon = []
    for r in db("SELECT * FROM events WHERE user_id=?", (uid,), "all"):
        o = next_occ(r, td)
        if o and 0 <= (o - td).days <= 7:
            soon.append(f"  🎂 {esc(r['title'])} — {days_text((o - td).days)}")
    if soon:
        lines.append("\n<b>مناسبت‌های نزدیک:</b>")
        lines += soon[:4]
    hs = db("SELECT COUNT(*) n FROM habits WHERE user_id=?", (uid,), "one")["n"]
    if hs:
        lines.append(f"\n🎯 امروز {hs} عادت برای ثبت داری.")
    bl = budget_line(uid)
    if bl:
        lines.append(bl)
    w = await weather_text(uid)
    if w:
        lines.append("\n" + w)
    lines.append("\n💬 " + random.choice(QUOTES))
    return "\n".join(lines)


async def scheduler(app):
    await asyncio.sleep(8)
    while True:
        try:
            await tick(app)
        except Exception as e:
            logger.error(f"scheduler: {e}")
        await asyncio.sleep(20)


async def tick(app):
    t = time.time()
    # یادآورها
    for r in await asyncio.to_thread(db, "SELECT * FROM reminders WHERE sent=0 AND due<=? ORDER BY due LIMIT 50", (t,), "all"):
        if r["rep"] in ("daily", "weekly", "monthly"):
            await asyncio.to_thread(db, "UPDATE reminders SET due=? WHERE id=?", (next_due(r["due"], r["rep"], t), r["id"]))
        else:
            await asyncio.to_thread(db, "UPDATE reminders SET sent=1 WHERE id=?", (r["id"],))
        kb = KB([[B("⏰ ۱۰ دقیقه", callback_data=f"sz_{r['id']}_10"), B("⏰ ۱ ساعت", callback_data=f"sz_{r['id']}_60")],
                 [B("🌙 فردا ۹ صبح", callback_data=f"sz_{r['id']}_t"), B("✅ انجام شد", callback_data="rk")]])
        await safe_send(app.bot, r["user_id"], f"⏰ <b>یادآوری</b>\n\n{esc(r['text'])}", parse_mode=HTML, reply_markup=kb)
    # تایمر تمرکز
    for r in await asyncio.to_thread(db, "SELECT * FROM pomo WHERE end_ts<=?", (t,), "all"):
        await asyncio.to_thread(db, "DELETE FROM pomo WHERE user_id=?", (r["user_id"],))
        if r["kind"] == "f":
            await asyncio.to_thread(db, "INSERT INTO focus_log(user_id,day,minutes) VALUES(?,?,?)", (r["user_id"], today_iso(), r["minutes"]))
            kb = KB([[B("☕️ ۵ دقیقه استراحت", callback_data="po_5_b"), B("🍅 جلسه‌ی بعد", callback_data="po_25_f")]])
            await safe_send(app.bot, r["user_id"], f"🍅 <b>آفرین!</b> {r['minutes']} دقیقه تمرکز تموم شد.", parse_mode=HTML, reply_markup=kb)
        else:
            await safe_send(app.bot, r["user_id"], "☕️ استراحت تمام شد. آماده‌ای برای جلسه‌ی بعد؟",
                            reply_markup=KB([[B("🍅 شروع ۲۵ دقیقه", callback_data="po_25_f")]]))
    # پست‌های کانال
    for p in await asyncio.to_thread(db, "SELECT * FROM posts WHERE status='scheduled' AND due<=? ORDER BY due LIMIT 10", (t,), "all"):
        if not await asyncio.to_thread(db, "UPDATE posts SET status='sending' WHERE id=? AND status='scheduled'", (p["id"],)):
            continue
        try:
            markup = KB([[B(p["btn_text"], url=p["btn_url"])]]) if p["btn_text"] else None
            await app.bot.copy_message(chat_id=p["target"], from_chat_id=p["src_chat"], message_id=p["src_msg"], reply_markup=markup)
            await asyncio.to_thread(db, "UPDATE posts SET status='sent' WHERE id=?", (p["id"],))
            await safe_send(app.bot, OWNER_ID, f"✅ پست #{p['id']} در کانال ارسال شد.")
        except Exception as e:
            await asyncio.to_thread(db, "UPDATE posts SET status='failed', error=? WHERE id=?", (str(e)[:200], p["id"]))
            await safe_send(app.bot, OWNER_ID, f"❌ ارسال پست #{p['id']} ناموفق بود:\n{e}")
    # گزارش صبحگاهی و یادآوری عادت‌ها
    hhmm, td = now().strftime("%H:%M"), today_iso()
    for r in await asyncio.to_thread(db, "SELECT user_id FROM prefs WHERE k='brief_on' AND v='1'", (), "all"):
        uid = r["user_id"]
        if hhmm >= pref(uid, "brief_time", "08:00") and once(f"brief:{uid}:{td}"):
            await safe_send(app.bot, uid, await build_briefing(uid), parse_mode=HTML)
        if hhmm >= "21:00" and once(f"nudge:{uid}:{td}"):
            left = [h["name"] for h in db("SELECT id,name FROM habits WHERE user_id=?", (uid,), "all") if td not in habit_days(h["id"])]
            if left:
                await safe_send(app.bot, uid, "🌙 هنوز این عادت‌ها را امروز ثبت نکردی:\n" + "\n".join(f"• {esc(x)}" for x in left[:8]), parse_mode=HTML)
    # پشتیبان روزانه برای مدیر
    if hhmm >= "03:30" and once(f"backup:{td}"):
        try:
            await app.bot.send_document(OWNER_ID, io.BytesIO(await asyncio.to_thread(make_backup)),
                                        filename=f"backup_{td}.db", caption="💾 پشتیبان روزانه (برای بازگردانی: ارسال فایل با کپشن /restore)")
        except Exception as e:
            logger.warning(f"backup: {e}")


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
    await app.bot.set_my_commands([("start", "شروع و منوی اصلی"), ("help", "راهنما"), ("today", "تاریخ امروز"),
                                   ("calc", "ماشین‌حساب"), ("cancel", "لغو عملیات"), ("id", "آیدی عددی من")])
    _BG.append(asyncio.create_task(scheduler(app)))


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
    for name, fn in (("admin", cmd_adminhelp), ("stats", cmd_stats), ("backup", cmd_backup), ("broadcast", cmd_broadcast),
                     ("ban", cmd_ban), ("unban", cmd_unban), ("addchannel", cmd_addchannel),
                     ("delchannel", cmd_delchannel), ("channels", cmd_channels),
                     ("newpost", cmd_newpost), ("posts", cmd_posts)):
        app.add_handler(CommandHandler(name, fn, filters=owner))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(private & ~filters.COMMAND, msg_router))
    app.add_error_handler(on_error)
    logger.info("دستیار هوشمند در حال اجراست...")
    app.run_polling(bootstrap_retries=-1, drop_pending_updates=True)


if __name__ == "__main__":
    main()
