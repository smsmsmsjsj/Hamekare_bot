# requirements.txt  ->  python-telegram-bot==22.*
#
# متغیرهای محیطی:
#   BOT_TOKEN      توکن ربات (از BotFather)
#   OWNER_ID       آیدی عددی خودت (از @userinfobot)
#   MIN_INTERVAL   اختیاری، حداقل فاصله مجاز به ثانیه (پیش‌فرض 5)
#   DATA_DIR       اختیاری، پوشه دیتابیس
import asyncio
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import (BadRequest, ChatMigrated, Conflict, Forbidden,
                            NetworkError, RetryAfter)
from telegram.ext import (Application, CallbackQueryHandler, ChatMemberHandler,
                          CommandHandler, MessageHandler, filters)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def env_required(name):
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"متغیر محیطی {name} تنظیم نشده است!")
    return v


TOKEN = env_required("BOT_TOKEN")
OWNER_ID = int(env_required("OWNER_ID"))
MIN_INTERVAL = max(int(os.environ.get("MIN_INTERVAL", "5")), 1)
DATA_DIR = os.environ.get("DATA_DIR", ".")
PORT = os.environ.get("PORT")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "sender.db")
HTML = ParseMode.HTML

HELP = (
    "👋 <b>ربات ارسال زمان‌بندی‌شده</b>\n\n"
    "1️⃣ ربات را به گروه یا کانال اضافه کنید (در کانال باید ادمین با دسترسی Post messages باشد)\n"
    "2️⃣ /newjob را بزنید، پیام موردنظر را بفرستید، مقصد، فاصله و تعداد را انتخاب کنید\n"
    "3️⃣ با /jobs کارها را متوقف، ادامه یا حذف کنید\n\n"
    "دستورها:\n"
    "/newjob — ساخت کار جدید\n"
    "/jobs — مدیریت کارها\n"
    "/groups — گروه‌ها و کانال‌های ثبت‌شده\n"
    "/stopall — توقف همه کارها\n"
    "/cancel — انصراف از عملیات جاری\n\n"
    "فاصله را می‌توانید این‌طور بنویسید: <code>10</code> (ثانیه)، <code>20s</code>، "
    "<code>1m</code>، <code>2h</code> یا <code>1d</code>.\n"
    "⚠️ پیام اصلی را از چت با ربات پاک نکنید، چون ربات از روی همان کپی می‌کند.")


# ============================== ابزارها ==============================
def esc(s):
    import html
    return html.escape(str(s), quote=False)


_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(s):
    return s.translate(_DIGITS)


def parse_interval(text):
    t = normalize_digits(text).strip().lower()
    m = re.fullmatch(r"(\d+)\s*(s|sec|ثانیه|m|min|دقیقه|h|ساعت|d|روز)?", t)
    if not m:
        return None
    n = int(m.group(1))
    u = m.group(2) or "s"
    if u in ("s", "sec", "ثانیه"):
        mult = 1
    elif u in ("m", "min", "دقیقه"):
        mult = 60
    elif u in ("h", "ساعت"):
        mult = 3600
    else:
        mult = 86400
    sec = n * mult
    return sec if 1 <= sec <= 30 * 86400 else None


def parse_count(text):
    t = normalize_digits(text).strip().lower()
    if t in ("inf", "نامحدود", "∞", "بی‌نهایت", "بینهایت"):
        return -1
    if t.isdigit() and 1 <= int(t) <= 1_000_000:
        return int(t)
    return None


def fmt_interval(sec):
    if sec % 86400 == 0:
        return f"{sec // 86400} روز"
    if sec % 3600 == 0:
        return f"{sec // 3600} ساعت"
    if sec % 60 == 0:
        return f"{sec // 60} دقیقه"
    return f"{sec} ثانیه"


# ============================== دیتابیس ==============================
SCHEMA = """
CREATE TABLE IF NOT EXISTS groups(chat_id INTEGER PRIMARY KEY, title TEXT, type TEXT);
CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, src_chat INTEGER NOT NULL, src_msg INTEGER NOT NULL,
    target INTEGER NOT NULL, interval INTEGER NOT NULL, total INTEGER NOT NULL,
    remaining INTEGER NOT NULL, sent INTEGER NOT NULL DEFAULT 0, next_due REAL NOT NULL,
    status TEXT NOT NULL, last_error TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, next_due);
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


def group_title(cid):
    r = db("SELECT title FROM groups WHERE chat_id=?", (cid,), "one")
    return r["title"] if r else str(cid)


STATUS_FA = {"active": "🟢 فعال", "paused": "⏸ متوقف", "done": "✅ تمام‌شده",
             "failed": "❌ خطا", "stopped": "⏹ لغو"}


def job_line(r):
    total = "∞" if r["total"] < 0 else r["total"]
    return (f"#{r['id']} | {esc(group_title(r['target']))} | هر {fmt_interval(r['interval'])} | "
            f"{r['sent']}/{total} | {STATUS_FA.get(r['status'], r['status'])}")


# ============================== ارسال ==============================
async def safe_send(bot, chat_id, text, **kw):
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kw)
        return True
    except Exception as e:
        logger.warning(f"ارسال به {chat_id} ناموفق: {e}")
        return False


async def respond(update, text, markup=None):
    q = update.callback_query
    if q:
        try:
            await q.edit_message_text(text, parse_mode=HTML, reply_markup=markup)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
        except Exception:
            pass
        await q.message.reply_text(text, parse_mode=HTML, reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode=HTML, reply_markup=markup)


async def run_job(app, job):
    jid = job["id"]
    try:
        await app.bot.copy_message(chat_id=job["target"], from_chat_id=job["src_chat"],
                                   message_id=job["src_msg"])
    except RetryAfter as e:
        wait = int(e.retry_after) + 1
        await asyncio.to_thread(db, "UPDATE jobs SET next_due=? WHERE id=? AND status='active'",
                                (time.time() + wait, jid))
        return
    except ChatMigrated as e:
        new_id = e.new_chat_id
        await asyncio.to_thread(db, "UPDATE jobs SET target=?, next_due=? WHERE target=?",
                                (new_id, time.time() + 2, job["target"]))
        await asyncio.to_thread(db, "UPDATE groups SET chat_id=? WHERE chat_id=?", (new_id, job["target"]))
        return
    except (Forbidden, BadRequest) as e:
        await asyncio.to_thread(db, "UPDATE jobs SET status='failed', last_error=? WHERE id=?",
                                (str(e)[:200], jid))
        await safe_send(app.bot, OWNER_ID,
                        f"❌ کار #{jid} متوقف شد (خطا در ارسال به {esc(group_title(job['target']))}):\n"
                        f"<code>{esc(e)}</code>\n\nمعمولاً یعنی ربات از آنجا حذف شده، اجازهٔ ارسال ندارد "
                        "یا پیام اصلی پاک شده است.", parse_mode=HTML)
        return
    except NetworkError:
        await asyncio.to_thread(db, "UPDATE jobs SET next_due=? WHERE id=? AND status='active'",
                                (time.time() + 10, jid))
        return
    except Exception as e:
        await asyncio.to_thread(db, "UPDATE jobs SET status='failed', last_error=? WHERE id=?",
                                (str(e)[:200], jid))
        await safe_send(app.bot, OWNER_ID, f"❌ کار #{jid} با خطا متوقف شد: {esc(e)}", parse_mode=HTML)
        return

    sent = job["sent"] + 1
    rem = job["remaining"]
    if rem > 0:
        rem -= 1
    if rem == 0:
        await asyncio.to_thread(db, "UPDATE jobs SET sent=?, remaining=0, status='done' "
                                    "WHERE id=? AND status='active'", (sent, jid))
        await safe_send(app.bot, OWNER_ID, f"✅ کار #{jid} تمام شد ({sent} پیام ارسال شد).")
    else:
        await asyncio.to_thread(db, "UPDATE jobs SET sent=?, remaining=?, next_due=? "
                                    "WHERE id=? AND status='active'",
                                (sent, rem, time.time() + job["interval"], jid))


_INFLIGHT = set()
_TASKS = set()


async def _guard(app, job):
    _INFLIGHT.add(job["id"])
    try:
        await run_job(app, job)
    finally:
        _INFLIGHT.discard(job["id"])


async def jobs_loop(app):
    await asyncio.sleep(3)
    while True:
        try:
            rows = await asyncio.to_thread(
                db, "SELECT * FROM jobs WHERE status='active' AND next_due<=? ORDER BY next_due LIMIT 30",
                (time.time(),), "all")
            for r in rows:
                if r["id"] in _INFLIGHT:
                    continue
                t = asyncio.create_task(_guard(app, r))
                _TASKS.add(t)
                t.add_done_callback(_TASKS.discard)
        except Exception as e:
            logger.error(f"jobs_loop: {e}")
        await asyncio.sleep(1)


# ============================== دسترسی ==============================
async def owner_only(update):
    u = update.effective_user
    if u and u.id == OWNER_ID:
        return True
    t = update.message or (update.callback_query.message if update.callback_query else None)
    if t:
        await t.reply_text("🔒 این ربات خصوصی است.")
    return False


# ============================== دستورات ==============================
async def cmd_start(update, context):
    if not await owner_only(update):
        return
    context.user_data.clear()
    await update.message.reply_text(HELP, parse_mode=HTML)


async def cmd_cancel(update, context):
    if not await owner_only(update):
        return
    context.user_data.clear()
    await update.message.reply_text("لغو شد.")


async def cmd_newjob(update, context):
    if not await owner_only(update):
        return
    context.user_data.clear()
    context.user_data["state"] = "job_msg"
    await update.message.reply_text(
        "📤 پیامی را که می‌خواهید تکرار شود بفرستید (متن، عکس، ویدیو، فایل...).\n"
        "برای انصراف: /cancel")


async def show_jobs(update):
    rows = db("SELECT * FROM jobs WHERE status IN ('active','paused') ORDER BY id DESC LIMIT 20",
              (), "all")
    if not rows:
        await respond(update, "کار فعالی وجود ندارد. با /newjob یکی بسازید.")
        return
    lines = ["📋 <b>کارها</b>\n"]
    kb = []
    for r in rows:
        lines.append(job_line(r))
        toggle = (InlineKeyboardButton(f"⏸ توقف #{r['id']}", callback_data=f"jp_{r['id']}")
                  if r["status"] == "active"
                  else InlineKeyboardButton(f"▶️ ادامه #{r['id']}", callback_data=f"jp_{r['id']}"))
        kb.append([toggle, InlineKeyboardButton(f"🗑 حذف #{r['id']}", callback_data=f"jx_{r['id']}")])
    await respond(update, "\n".join(lines), InlineKeyboardMarkup(kb))


async def cmd_jobs(update, context):
    if await owner_only(update):
        await show_jobs(update)


async def cmd_groups(update, context):
    if not await owner_only(update):
        return
    rows = db("SELECT chat_id, title, type FROM groups", (), "all")
    if not rows:
        await update.message.reply_text("هنوز گروه یا کانالی ثبت نشده. ربات را به آن‌ها اضافه کنید.")
        return
    await update.message.reply_text(
        "📢 <b>مقصدها</b>\n\n" + "\n".join(
            f"• {esc(r['title'])} ({r['type']}) <code>{r['chat_id']}</code>" for r in rows),
        parse_mode=HTML)


async def cmd_stopall(update, context):
    if not await owner_only(update):
        return
    n = db("UPDATE jobs SET status='stopped' WHERE status IN ('active','paused')")
    await update.message.reply_text(f"⏹ {n} کار متوقف شد.")


# ============================== مراحل ساخت کار ==============================
async def st_job_msg(update, context):
    msg = update.message
    context.user_data["job"] = {"src_chat": msg.chat_id, "src_msg": msg.message_id}
    groups = db("SELECT chat_id, title FROM groups", (), "all")
    kb = [[InlineKeyboardButton(g["title"] or str(g["chat_id"]), callback_data=f"jg_{g['chat_id']}")]
          for g in groups]
    kb.append([InlineKeyboardButton("✍️ وارد کردن آیدی یا یوزرنیم", callback_data="jg_manual")])
    context.user_data["state"] = "job_pick"
    await msg.reply_text("📢 به کدام گروه یا کانال ارسال شود؟", reply_markup=InlineKeyboardMarkup(kb))


async def ask_interval(message, context):
    context.user_data["state"] = "job_interval"
    await message.reply_text(
        f"⏱ فاصله بین پیام‌ها چقدر باشد؟\n\nمثال: 10 (ثانیه) ، 20s ، 1m ، 2h\n"
        f"حداقل مجاز: {MIN_INTERVAL} ثانیه")


async def st_job_target(update, context, text):
    ref = normalize_digits(text).strip()
    if re.fullmatch(r"-?\d+", ref):
        ref = int(ref)
    elif ref.startswith("https://t.me/"):
        ref = "@" + ref.rsplit("/", 1)[-1]
    elif not ref.startswith("@"):
        ref = "@" + ref
    try:
        chat = await context.bot.get_chat(ref)
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
    except Exception as e:
        await update.message.reply_text(f"❌ پیدا نشد یا ربات عضو آنجا نیست:\n{e}")
        return
    if member.status not in ("member", "administrator", "creator"):
        await update.message.reply_text("❌ ربات عضو این گروه یا کانال نیست.")
        return
    db("INSERT OR REPLACE INTO groups(chat_id, title, type) VALUES(?,?,?)",
       (chat.id, chat.title or str(chat.id), chat.type))
    context.user_data["job"]["target"] = chat.id
    await ask_interval(update.message, context)


async def st_job_interval(update, context, text):
    sec = parse_interval(text)
    if sec is None:
        await update.message.reply_text("❌ فرمت نامعتبر. مثال: 10 یا 20s یا 1m یا 2h")
        return
    if sec < MIN_INTERVAL:
        await update.message.reply_text(
            f"❌ حداقل فاصله {MIN_INTERVAL} ثانیه است (تلگرام در هر گروه حدود ۲۰ پیام در دقیقه را مجاز می‌داند).")
        return
    context.user_data["job"]["interval"] = sec
    context.user_data["state"] = "job_count"
    await update.message.reply_text("🔢 چند بار ارسال شود؟\n\nیک عدد بفرستید (مثلاً 50) یا بنویسید: نامحدود")


async def st_job_count(update, context, text):
    cnt = parse_count(text)
    if cnt is None:
        await update.message.reply_text("❌ یک عدد (1 تا 1000000) یا «نامحدود» بفرستید.")
        return
    job = context.user_data.get("job") or {}
    if not all(k in job for k in ("src_chat", "src_msg", "target", "interval")):
        context.user_data.clear()
        await update.message.reply_text("نشست منقضی شد. دوباره /newjob را بزنید.")
        return
    jid = db("INSERT INTO jobs(src_chat, src_msg, target, interval, total, remaining, next_due, status, created_at) "
             "VALUES(?,?,?,?,?,?,?,?,?)",
             (job["src_chat"], job["src_msg"], job["target"], job["interval"], cnt, cnt,
              time.time(), "active", time.time()))
    context.user_data.clear()
    total = "نامحدود" if cnt < 0 else str(cnt)
    await update.message.reply_text(
        f"✅ کار #{jid} شروع شد.\n\n📢 مقصد: {group_title(job['target'])}\n"
        f"⏱ فاصله: {fmt_interval(job['interval'])}\n🔢 تعداد: {total}\n\n"
        "مدیریت: /jobs")


STATE_HANDLERS = {"job_target": st_job_target, "job_interval": st_job_interval,
                  "job_count": st_job_count}


async def msg_router(update, context):
    msg = update.message
    if not msg or not await owner_only(update):
        return
    st = context.user_data.get("state")
    if st == "job_msg":
        await st_job_msg(update, context)
        return
    h = STATE_HANDLERS.get(st)
    if h and msg.text:
        await h(update, context, msg.text.strip())
        return
    await msg.reply_text("برای شروع /newjob را بزنید. راهنما: /start")


# ============================== کال‌بک‌ها ==============================
async def callback_router(update, context):
    q = update.callback_query
    d = q.data or ""
    try:
        await q.answer()
    except Exception:
        pass
    if not await owner_only(update):
        return

    if d == "jg_manual":
        context.user_data["state"] = "job_target"
        await q.message.reply_text("✍️ آیدی عددی یا یوزرنیم گروه/کانال را بفرستید (مثلاً @mygroup).\n"
                                   "ربات باید عضو آنجا باشد.")
    elif d.startswith("jg_"):
        job = context.user_data.get("job")
        if not job:
            await q.message.reply_text("نشست منقضی شد. دوباره /newjob را بزنید.")
            return
        job["target"] = int(d[3:])
        await ask_interval(q.message, context)
    elif d.startswith("jp_"):
        jid = int(d[3:])
        r = db("SELECT status FROM jobs WHERE id=?", (jid,), "one")
        if r and r["status"] == "active":
            db("UPDATE jobs SET status='paused' WHERE id=?", (jid,))
        elif r and r["status"] == "paused":
            db("UPDATE jobs SET status='active', next_due=? WHERE id=?", (time.time(), jid))
        await show_jobs(update)
    elif d.startswith("jx_"):
        db("UPDATE jobs SET status='stopped' WHERE id=?", (int(d[3:]),))
        await show_jobs(update)


# ============================== ثبت خودکار گروه‌ها ==============================
async def on_my_chat_member(update, context):
    cm = update.my_chat_member
    if not cm or cm.chat.type == "private":
        return
    chat = cm.chat
    new = cm.new_chat_member.status
    if new in ("member", "administrator"):
        db("INSERT OR REPLACE INTO groups(chat_id, title, type) VALUES(?,?,?)",
           (chat.id, chat.title or str(chat.id), chat.type))
        await safe_send(context.bot, OWNER_ID,
                        f"✅ ربات به «{esc(chat.title)}» اضافه شد و در لیست مقصدها ثبت شد.\n"
                        "برای ساخت کار: /newjob", parse_mode=HTML)
    elif new in ("left", "kicked"):
        db("DELETE FROM groups WHERE chat_id=?", (chat.id,))
        n = db("UPDATE jobs SET status='failed', last_error='bot removed' "
               "WHERE target=? AND status IN ('active','paused')", (chat.id,))
        await safe_send(context.bot, OWNER_ID,
                        f"⚠️ ربات از «{esc(chat.title)}» حذف شد."
                        + (f" {n} کار مربوط به آن متوقف شد." if n else ""), parse_mode=HTML)


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


_LAST_ERR = [0.0]


async def on_error(update, context):
    err = context.error
    if isinstance(err, Conflict):
        logger.warning("Conflict: نمونه دیگری با همین توکن در حال اجراست")
        return
    logger.error("خطا:", exc_info=err)
    if isinstance(err, (NetworkError, RetryAfter)):
        return
    now = time.time()
    if now - _LAST_ERR[0] > 60:
        _LAST_ERR[0] = now
        await safe_send(context.bot, OWNER_ID, f"⚠️ خطا: {type(err).__name__}: {err}")


_BG = []


async def post_init(app):
    await app.bot.set_my_commands([
        ("start", "راهنما"), ("newjob", "ساخت کار جدید"), ("jobs", "مدیریت کارها"),
        ("groups", "گروه‌ها و کانال‌ها"), ("stopall", "توقف همه"), ("cancel", "انصراف")])
    _BG.append(asyncio.create_task(jobs_loop(app)))


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
    for name, fn in (("start", cmd_start), ("help", cmd_start), ("cancel", cmd_cancel),
                     ("newjob", cmd_newjob), ("jobs", cmd_jobs), ("groups", cmd_groups),
                     ("stopall", cmd_stopall)):
        app.add_handler(CommandHandler(name, fn, filters=private))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(private & ~filters.COMMAND, msg_router))
    app.add_error_handler(on_error)

    logger.info("ربات ارسال زمان‌بندی‌شده در حال اجراست...")
    app.run_polling(bootstrap_retries=-1, drop_pending_updates=True)


if __name__ == "__main__":
    main()
