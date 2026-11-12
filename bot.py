import asyncio
import html
import logging
import os
import re
import sqlite3
import time

from telegram import (Update, InlineKeyboardButton as B,
                      InlineKeyboardMarkup as M, ChatMember)
from telegram.constants import ChatType, ParseMode
from telegram.error import (RetryAfter, Forbidden, BadRequest,
                            ChatMigrated, TelegramError)
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ChatMemberHandler, ContextTypes, filters)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("autosender")

TOKEN = os.environ["BOT_TOKEN"]
OWNERS = {int(x) for x in os.environ["OWNER_ID"].replace(" ", "").split(",") if x}
MIN_INTERVAL = int(os.environ.get("MIN_INTERVAL", "5"))

# ───────────────────────── دیتابیس ─────────────────────────
db = sqlite3.connect("autosender.db", check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS chats(
    chat_id INTEGER PRIMARY KEY, title TEXT, type TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target INTEGER,            -- 0 = همه چت‌ها
    src_chat INTEGER, src_msg INTEGER,
    interval INTEGER, delete_prev INTEGER, pin INTEGER,
    remaining INTEGER,         -- -1 = نامحدود
    enabled INTEGER DEFAULT 1, sent INTEGER DEFAULT 0, created INTEGER);
CREATE TABLE IF NOT EXISTS last(
    job_id INTEGER, chat_id INTEGER, msg_id INTEGER, PRIMARY KEY(job_id, chat_id));
""")
db.commit()


def run(sql, args=()):
    cur = db.execute(sql, args)
    db.commit()
    return cur


def one(sql, args=()):
    return db.execute(sql, args).fetchone()


def many(sql, args=()):
    return db.execute(sql, args).fetchall()


# ───────────────────────── ابزارها ─────────────────────────
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
UNITS = {"": 1, "s": 1, "ثانیه": 1, "m": 60, "دقیقه": 60,
         "h": 3600, "ساعت": 3600, "d": 86400, "روز": 86400}


def parse_interval(s):
    s = s.strip().lower().translate(DIGITS)
    m = re.fullmatch(r"(\d+)\s*(s|m|h|d|ثانیه|دقیقه|ساعت|روز)?", s)
    if not m:
        return None
    return int(m.group(1)) * UNITS[m.group(2) or ""]


def fmt_interval(sec):
    if sec % 86400 == 0:
        return f"{sec // 86400} روز"
    if sec % 3600 == 0:
        return f"{sec // 3600} ساعت"
    if sec % 60 == 0:
        return f"{sec // 60} دقیقه"
    return f"{sec} ثانیه"


def is_owner(update: Update):
    u = update.effective_user
    return bool(u and u.id in OWNERS)


def chat_name(cid):
    if not cid:
        return "همهٔ چت‌ها"
    r = one("SELECT title FROM chats WHERE chat_id=?", (cid,))
    return html.escape(r["title"] or str(cid)) if r else str(cid)


async def notify(bot, text):
    for o in OWNERS:
        try:
            await bot.send_message(o, text, parse_mode=ParseMode.HTML)
        except TelegramError:
            pass


# ───────────────────────── موتور ارسال ─────────────────────────
ERR_SEEN = {}


def deactivate(cid):
    run("UPDATE chats SET active=0 WHERE chat_id=?", (cid,))


async def send_one(bot, row, cid):
    for _ in range(3):
        try:
            msg = await bot.copy_message(chat_id=cid, from_chat_id=row["src_chat"],
                                         message_id=row["src_msg"])
            old = one("SELECT msg_id FROM last WHERE job_id=? AND chat_id=?", (row["id"], cid))
            run("INSERT OR REPLACE INTO last(job_id,chat_id,msg_id) VALUES(?,?,?)",
                (row["id"], cid, msg.message_id))
            if row["delete_prev"] and old:
                try:
                    await bot.delete_message(cid, old["msg_id"])
                except TelegramError:
                    pass
            if row["pin"]:
                try:
                    await bot.pin_chat_message(cid, msg.message_id, disable_notification=True)
                except TelegramError:
                    pass
            ERR_SEEN.pop((row["id"], cid), None)
            return True
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except ChatMigrated as e:
            deactivate(cid)
            run("INSERT OR REPLACE INTO chats(chat_id,title,type,active) "
                "SELECT ?, title, type, 1 FROM chats WHERE chat_id=?", (e.new_chat_id, cid))
            cid = e.new_chat_id
        except Forbidden as e:
            deactivate(cid)
            await notify(bot, f"⛔️ دسترسی به «{chat_name(cid)}» رو از دست دادم و غیرفعالش کردم.\n<code>{html.escape(str(e))}</code>")
            return False
        except BadRequest as e:
            key = (row["id"], cid)
            if ERR_SEEN.get(key) != str(e):
                ERR_SEEN[key] = str(e)
                await notify(bot, f"⚠️ جاب #{row['id']} تو «{chat_name(cid)}» خطا داد:\n<code>{html.escape(str(e))}</code>")
            if "chat not found" in str(e).lower():
                deactivate(cid)
            return False
        except TelegramError as e:
            log.warning("خطای تلگرام: %s", e)
            await asyncio.sleep(2)
    return False


async def run_job(context: ContextTypes.DEFAULT_TYPE):
    jid = context.job.data
    row = one("SELECT * FROM jobs WHERE id=?", (jid,))
    if not row or not row["enabled"]:
        context.job.schedule_removal()
        return
    if row["target"]:
        targets = [row["target"]]
    else:
        targets = [r["chat_id"] for r in many("SELECT chat_id FROM chats WHERE active=1")]
    ok = 0
    for cid in targets:
        if await send_one(context.bot, row, cid):
            ok += 1
        await asyncio.sleep(0.1)
    run("UPDATE jobs SET sent=sent+? WHERE id=?", (ok, jid))
    if row["remaining"] > 0:
        left = row["remaining"] - 1
        run("UPDATE jobs SET remaining=? WHERE id=?", (left, jid))
        if left == 0:
            run("UPDATE jobs SET enabled=0 WHERE id=?", (jid,))
            context.job.schedule_removal()
            await notify(context.bot, f"✅ جاب #{jid} تموم شد (تعداد تعیین‌شده ارسال شد).")


def schedule(app, jid, interval):
    unschedule(app, jid)
    app.job_queue.run_repeating(run_job, interval=interval, first=1, name=f"job_{jid}",
                                data=jid, job_kwargs={"coalesce": True, "misfire_grace_time": 30})


def unschedule(app, jid):
    for j in app.job_queue.get_jobs_by_name(f"job_{jid}"):
        j.schedule_removal()


# ───────────────────────── ثبت خودکار چت‌ها ─────────────────────────
async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = update.my_chat_member
    chat = c.chat
    if chat.type == ChatType.PRIVATE:
        return
    st = c.new_chat_member.status
    if st in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR):
        run("INSERT OR REPLACE INTO chats(chat_id,title,type,active) VALUES(?,?,?,1)",
            (chat.id, chat.title or str(chat.id), chat.type))
        msg = f"✅ بات به «{html.escape(chat.title or str(chat.id))}» ({chat.type}) اضافه شد."
        if chat.type == ChatType.CHANNEL and not getattr(c.new_chat_member, "can_post_messages", False):
            msg += "\n⚠️ تو کانال باید بات ادمین باشه و دسترسی «Post Messages» داشته باشه."
        await notify(context.bot, msg)
    else:
        deactivate(chat.id)
        await notify(context.bot, f"➖ بات از «{html.escape(chat.title or str(chat.id))}» خارج شد؛ غیرفعال شد.")


# ───────────────────────── ساخت جاب (ویزارد) ─────────────────────────
def opts_view(w):
    cnt = "نامحدود" if w.get("count", -1) < 0 else str(w["count"])
    text = (f"⚙️ تنظیمات نهایی\n\n🎯 هدف: {chat_name(w['target'])}\n"
            f"⏱ فاصله: {fmt_interval(w['interval'])}\n🔢 تعداد: {cnt}")
    kb = M([
        [B(("✅" if w.get("del") else "⬜️") + " پاک کردن پیام قبلی", callback_data="o_del"),
         B(("✅" if w.get("pin") else "⬜️") + " پین کردن", callback_data="o_pin")],
        [B("🔢 محدود کردن تعداد", callback_data="o_count"),
         B("♾ نامحدود", callback_data="o_inf")],
        [B("▶️ شروع ارسال", callback_data="o_go")],
        [B("❌ انصراف", callback_data="w_cancel")],
    ])
    return text, kb


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text(
        "🤖 بات ارسال خودکار\n\n"
        "1️⃣ بات رو به گروه یا کانال اضافه کن (کانال: ادمین با دسترسی Post)\n"
        "2️⃣ /new ← ساخت ارسال جدید\n"
        "/jobs ← مدیریت ارسال‌ها\n"
        "/chats ← چت‌های ثبت‌شده\n"
        "/pauseall و /resumeall ← مکث یا ادامهٔ همه\n"
        "/cancel ← لغو مرحلهٔ جاری")


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    rows = many("SELECT * FROM chats ORDER BY active DESC, title")
    if not rows:
        await update.message.reply_text("هنوز بات به هیچ گروه یا کانالی اضافه نشده.")
        return
    lines = [f"{'🟢' if r['active'] else '🔴'} {html.escape(r['title'] or '')} — <code>{r['chat_id']}</code> ({r['type']})"
             for r in rows]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    context.user_data["w"] = {"step": "content", "count": -1}
    await update.message.reply_text(
        "✍️ پیامی که می‌خوای ارسال بشه رو همینجا بفرست (متن، عکس، ویدیو، فایل… هر چی).\n"
        "⚠️ این پیام رو بعداً از چت با بات پاک نکن، چون بات از روی همین کپی می‌کنه.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    context.user_data.pop("w", None)
    await update.message.reply_text("لغو شد.")


async def on_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    w = context.user_data.get("w")
    msg = update.message
    if not w:
        await msg.reply_text("برای ساخت ارسال جدید /new رو بزن.")
        return

    if w["step"] == "content":
        w["src_chat"], w["src_msg"] = msg.chat_id, msg.message_id
        w["step"] = "target"
        chats = many("SELECT * FROM chats WHERE active=1 ORDER BY title")
        if not chats:
            context.user_data.pop("w", None)
            await msg.reply_text("هیچ چت فعالی نیست. اول بات رو به گروه یا کانال اضافه کن، بعد /new بزن.")
            return
        kb = [[B(f"📣 {r['title']}"[:60], callback_data=f"t_{r['chat_id']}")] for r in chats]
        kb.append([B("🌐 همهٔ چت‌ها", callback_data="t_0")])
        kb.append([B("❌ انصراف", callback_data="w_cancel")])
        await msg.reply_text("🎯 به کجا ارسال بشه؟", reply_markup=M(kb))

    elif w["step"] == "interval":
        sec = parse_interval(msg.text or "")
        if not sec or sec < MIN_INTERVAL:
            await msg.reply_text(f"❌ نامعتبره. مثال: 10  یا  30s  یا  5m  یا  2h  (حداقل {MIN_INTERVAL} ثانیه)")
            return
        w["interval"] = sec
        w["step"] = "opts"
        text, kb = opts_view(w)
        await msg.reply_text(text, reply_markup=kb)

    elif w["step"] == "count":
        t = (msg.text or "").strip().translate(DIGITS)
        if not t.isdigit() or int(t) < 1:
            await msg.reply_text("❌ یه عدد بزرگ‌تر از صفر بفرست.")
            return
        w["count"] = int(t)
        w["step"] = "opts"
        text, kb = opts_view(w)
        await msg.reply_text(text, reply_markup=kb)


def job_view(r):
    status = "▶️ فعال" if r["enabled"] else "⏸ متوقف"
    rem = "نامحدود" if r["remaining"] < 0 else str(r["remaining"])
    text = (f"#{r['id']} — {status}\n🎯 {chat_name(r['target'])}\n"
            f"⏱ هر {fmt_interval(r['interval'])}\n"
            f"📤 ارسال‌شده: {r['sent']} | 🔢 باقی‌مانده: {rem}\n"
            f"🧹 پاک‌کردن قبلی: {'بله' if r['delete_prev'] else 'خیر'} | 📌 پین: {'بله' if r['pin'] else 'خیر'}")
    kb = M([[B("⏸ مکث" if r["enabled"] else "▶️ ادامه", callback_data=f"jp_{r['id']}"),
             B("📨 همین الان", callback_data=f"jn_{r['id']}"),
             B("🗑 حذف", callback_data=f"jd_{r['id']}")]])
    return text, kb


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    rows = many("SELECT * FROM jobs ORDER BY id")
    if not rows:
        await update.message.reply_text("هیچ ارسالی تعریف نشده. /new")
        return
    for r in rows:
        text, kb = job_view(r)
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_pauseall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    for r in many("SELECT id FROM jobs WHERE enabled=1"):
        unschedule(context.application, r["id"])
    run("UPDATE jobs SET enabled=0")
    await update.message.reply_text("⏸ همهٔ ارسال‌ها متوقف شد.")


async def cmd_resumeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    run("UPDATE jobs SET enabled=1 WHERE remaining!=0")
    for r in many("SELECT * FROM jobs WHERE enabled=1"):
        schedule(context.application, r["id"], r["interval"])
    await update.message.reply_text("▶️ همهٔ ارسال‌ها ادامه پیدا کرد.")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in OWNERS:
        await q.answer()
        return
    d = q.data
    w = context.user_data.get("w")
    await q.answer()

    if d == "w_cancel":
        context.user_data.pop("w", None)
        await q.edit_message_text("لغو شد.")

    elif d.startswith("t_") and w:
        w["target"] = int(d[2:])
        w["step"] = "interval"
        await q.edit_message_text(
            "⏱ فاصلهٔ ارسال رو بفرست:\n\n"
            "مثال‌ها: 10 (ثانیه) ، 30s ، 5m ، 2h ، 1d\n"
            f"حداقل: {MIN_INTERVAL} ثانیه")

    elif d in ("o_del", "o_pin") and w:
        key = "del" if d == "o_del" else "pin"
        w[key] = not w.get(key)
        text, kb = opts_view(w)
        await q.edit_message_text(text, reply_markup=kb)

    elif d == "o_inf" and w:
        w["count"] = -1
        text, kb = opts_view(w)
        await q.edit_message_text(text, reply_markup=kb)

    elif d == "o_count" and w:
        w["step"] = "count"
        await q.edit_message_text("🔢 چند بار ارسال بشه؟ یه عدد بفرست:")

    elif d == "o_go" and w:
        cur = run("INSERT INTO jobs(target,src_chat,src_msg,interval,delete_prev,pin,remaining,created) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (w["target"], w["src_chat"], w["src_msg"], w["interval"],
                   int(bool(w.get("del"))), int(bool(w.get("pin"))), w.get("count", -1), int(time.time())))
        schedule(context.application, cur.lastrowid, w["interval"])
        context.user_data.pop("w", None)
        await q.edit_message_text(f"🚀 ارسال #{cur.lastrowid} شروع شد. مدیریت: /jobs")

    elif d.startswith(("jp_", "jd_", "jn_")):
        act, jid = d[:2], int(d[3:])
        r = one("SELECT * FROM jobs WHERE id=?", (jid,))
        if not r:
            await q.edit_message_text("این ارسال دیگه وجود نداره.")
            return
        if act == "jd":
            unschedule(context.application, jid)
            run("DELETE FROM jobs WHERE id=?", (jid,))
            run("DELETE FROM last WHERE job_id=?", (jid,))
            await q.edit_message_text(f"🗑 ارسال #{jid} حذف شد.")
            return
        if act == "jp":
            if r["enabled"]:
                run("UPDATE jobs SET enabled=0 WHERE id=?", (jid,))
                unschedule(context.application, jid)
            else:
                run("UPDATE jobs SET enabled=1 WHERE id=?", (jid,))
                schedule(context.application, jid, r["interval"])
        else:  # ارسال فوری
            targets = [r["target"]] if r["target"] else [x["chat_id"] for x in many("SELECT chat_id FROM chats WHERE active=1")]
            for cid in targets:
                await send_one(context.bot, r, cid)
                await asyncio.sleep(0.1)
        text, kb = job_view(one("SELECT * FROM jobs WHERE id=?", (jid,)))
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except BadRequest:
            pass


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("خطا:", exc_info=context.error)


async def post_init(app: Application):
    for r in many("SELECT * FROM jobs WHERE enabled=1"):
        schedule(app, r["id"], r["interval"])
    log.info("بات آماده است.")


def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("chats", cmd_chats))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("pauseall", cmd_pauseall))
    app.add_handler(CommandHandler("resumeall", cmd_resumeall))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_private))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
