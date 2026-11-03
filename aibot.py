"""ИИ-бот (TRACK=ai): короткий урок каждый день — объяснение простыми словами, готовый промпт,
задание «сделай сейчас», вопрос. Можно пропустить то, что уже знаешь. Игры, наставник, XP и серия.
Контент — ai_lessons.py и ai_games.py. Запускается из main.py (bots.list: bot_ii AI aibot.py)."""
import asyncio
import logging
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, F  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError  # noqa: E402
from aiogram.filters import Command, CommandObject, CommandStart  # noqa: E402
from aiogram.types import (BotCommand, CallbackQuery, ErrorEvent, InlineKeyboardButton,  # noqa: E402
                           InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup)

import ai  # noqa: E402
import ai_games as games  # noqa: E402
import ai_lessons as L  # noqa: E402
from db import DB  # noqa: E402

TRACK = os.getenv("TRACK", "ai").strip().lower()
assert TRACK == "ai", "aibot.py — только для TRACK=ai"
TITLE = "🤖 ИИ на практике"

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
INVITE_CODE = os.getenv("INVITE_CODE", "").strip()
TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Almaty"))
MORNING = os.getenv("MORNING_TIME", "09:05")
EVENING = os.getenv("EVENING_TIME", "21:00")
SUMMARY = os.getenv("SUMMARY_TIME", "20:00")  # воскресенье

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("aibot")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
db = DB(os.getenv("DB_PATH", "bot.db"))
db.c.executescript("""
CREATE TABLE IF NOT EXISTS sent_log(key TEXT, day TEXT, PRIMARY KEY(key, day));
CREATE TABLE IF NOT EXISTS ai_course(student_id INTEGER PRIMARY KEY, cur INTEGER DEFAULT 0, xp INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS ai_done(student_id INTEGER, n INTEGER, day TEXT, how TEXT,
                                   PRIMARY KEY(student_id, n));
CREATE TABLE IF NOT EXISTS ai_seen(student_id INTEGER, qid TEXT, PRIMARY KEY(student_id, qid));
CREATE TABLE IF NOT EXISTS ai_games_log(student_id INTEGER, day TEXT, kind TEXT, good INTEGER, total INTEGER);
""")
db.c.commit()
DONE = "ai_done"
LOCKS: dict = {}
_last_admin_alert = {"t": 0.0}
HELP_CTX: dict = {}  # uid -> номер урока, в котором нажали «🆘 Не получается»
ONB_STATE: dict = {}
GAME: dict = {}
GAME_ROUND = 7

LEVELS = [(0, "🌱 Новичок"), (60, "🙂 Уверенный пользователь"), (150, "✍️ Мастер промптов"),
          (280, "🎨 Создатель"), (420, "🚀 Профи"), (600, "💼 ИИ-предприниматель")]


# ---------------- общее ----------------

def today() -> date:
    return datetime.now(TZ).date()


def esc(s) -> str:
    return ai.esc(s)


def strip_tags(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def chunks(text: str, size=3900):
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > size:
            out.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = ""
        cur += (("\n" if cur else "") + line)
    if cur:
        out.append(cur)
    return out or [""]


async def send(chat_id: int, text: str, reply_markup=None):
    parts = chunks(text)
    for i, part in enumerate(parts):
        kb = reply_markup if i == len(parts) - 1 else None
        try:
            await bot.send_message(chat_id, part, reply_markup=kb, disable_web_page_preview=True)
        except TelegramBadRequest:
            await bot.send_message(chat_id, strip_tags(part), parse_mode=None, reply_markup=kb,
                                   disable_web_page_preview=True)


def lock_for(uid):
    return LOCKS.setdefault(uid, asyncio.Lock())


def sent_once(key: str, d: date) -> bool:
    try:
        db.c.execute("INSERT INTO sent_log(key, day) VALUES(?, ?)", (key, d.isoformat()))
        db.c.commit()
        return True
    except Exception:
        return False


def is_admin(m) -> bool:
    return m.from_user.id == ADMIN_ID


def student_or_none(uid):
    s = db.get_student(uid)
    return s if s and s["active"] else None


def who(s) -> str:
    u = f" @{s['username']}" if s["username"] else ""
    return f"{esc(s['name'])}{u}"


def btn(text, data=None, url=None):
    return InlineKeyboardButton(text=text, callback_data=data) if data else InlineKeyboardButton(text=text, url=url)


def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


async def notify_admin(text: str):
    try:
        await bot.send_message(ADMIN_ID, f"[{TITLE}] {text}", disable_web_page_preview=True)
    except Exception as e:
        log.warning("admin notify failed: %s", e)


async def alert_admin_throttled(text: str):
    if time.time() - _last_admin_alert["t"] > 1800:
        _last_admin_alert["t"] = time.time()
        await notify_admin(text)


# ---------------- прогресс ----------------

def course(uid):
    db.c.execute("INSERT OR IGNORE INTO ai_course(student_id) VALUES(?)", (uid,))
    db.c.commit()
    return db.c.execute("SELECT * FROM ai_course WHERE student_id=?", (uid,)).fetchone()


def cur_lesson(uid) -> int:
    return course(uid)["cur"]


def add_xp(uid, n: int) -> int:
    course(uid)
    db.c.execute("UPDATE ai_course SET xp=xp+? WHERE student_id=?", (n, uid))
    db.c.commit()
    return course(uid)["xp"]


def done_set(uid) -> dict:
    return {r["n"]: r["how"] for r in db.c.execute("SELECT n, how FROM ai_done WHERE student_id=?", (uid,))}


def done_today(uid) -> bool:
    return bool(db.c.execute("SELECT 1 FROM ai_done WHERE student_id=? AND day=? AND how='done'",
                             (uid, today().isoformat())).fetchone())


def finish(uid, n: int, how: str) -> bool:
    """Отмечает урок пройденным. True — если впервые."""
    course(uid)
    first = not db.c.execute("SELECT 1 FROM ai_done WHERE student_id=? AND n=?", (uid, n)).fetchone()
    if first:
        db.c.execute("INSERT INTO ai_done(student_id, n, day, how) VALUES(?,?,?,?)",
                     (uid, n, today().isoformat(), how))
    db.c.execute("UPDATE ai_course SET cur=MAX(cur, ?) WHERE student_id=?", (min(n + 1, L.TOTAL), uid))
    db.c.commit()
    db.mark(uid, today(), DONE)
    return first


def level(xp: int):
    name, nxt = LEVELS[0][1], None
    for i, (need, nm) in enumerate(LEVELS):
        if xp >= need:
            name = nm
            nxt = LEVELS[i + 1][0] if i + 1 < len(LEVELS) else None
    return name, nxt


def streak(uid) -> int:
    n, d = 0, today()
    if not db.has_any_report(uid, d):
        d -= timedelta(days=1)
    start = date.fromisoformat(db.get_student(uid)["start_date"])
    while d >= start and db.has_any_report(uid, d):
        n += 1
        d -= timedelta(days=1)
    return n


def progress_bar(done: int, total: int, width=10) -> str:
    k = round(width * done / total) if total else 0
    return "🟩" * k + "⬜️" * (width - k)


# ---------------- тексты ----------------

HELP = ("❓ <b>Как пользоваться</b>\n\n"
        "Каждый день — <b>один короткий урок на 5–10 минут</b>. В уроке:\n"
        "1️⃣ объяснение простыми словами\n"
        "2️⃣ готовый промпт — нажмите на него, он скопируется\n"
        "3️⃣ «⚡ Сделай сейчас» — маленькое задание в бесплатном сервисе\n"
        "4️⃣ один вопрос — проверить себя\n\n"
        "Уже знаете тему? Жмите <b>«⏭ Я это знаю»</b> — один вопрос, и сразу дальше.\n"
        "Непонятно? <b>«🤔 Объясни проще»</b> — объясню по-другому, на примере.\n\n"
        "📖 <b>Урок дня</b> — продолжить курс\n"
        "🎮 <b>Игры</b> — повторение за 2 минуты\n"
        "📋 <b>Мои промпты</b> — все готовые промпты из пройденных уроков\n"
        "🗺 <b>Программа</b> — все темы; можно перейти к любой\n"
        "🙋 <b>Спросить наставника</b> — любой вопрос текстом или голосом 🎤\n"
        "📈 <b>Мои успехи</b> — очки, уровень, серия дней\n\n"
        "📎 Свои работы (картинки, видео, ссылки) присылайте сюда — я передам преподавателю.\n"
        "⏰ Утром в {m} — напомню про урок, вечером в {e} — если не успели.")

BTN_LESSON, BTN_GAMES = "📖 Урок дня", "🎮 Игры"
BTN_PROMPTS, BTN_PROGRAM = "📋 Мои промпты", "🗺 Программа"
BTN_ASK, BTN_PROGRESS = "🙋 Спросить наставника", "📈 Мои успехи"
BTN_HELP = "❓ Как пользоваться"


def menu_kb():
    K = KeyboardButton
    return ReplyKeyboardMarkup(keyboard=[
        [K(text=BTN_LESSON), K(text=BTN_GAMES)],
        [K(text=BTN_PROMPTS), K(text=BTN_PROGRAM)],
        [K(text=BTN_ASK), K(text=BTN_PROGRESS)],
        [K(text=BTN_HELP)],
    ], resize_keyboard=True, is_persistent=True, input_field_placeholder="Спросите что угодно или запишите голос 🎤")


# ---------------- урок по шагам ----------------

def lesson_head(n: int) -> str:
    x = L.LESSONS[n]
    return (f"📖 <b>Урок {n + 1} из {L.TOTAL}</b> · {L.block_name(n)} · ⏱ ~{x['min']} мин\n\n"
            f"<b>{esc(x['title'])}</b>")


async def open_lesson(uid: int, n: int):
    x = L.lesson(n)
    if not x:
        return await course_finished(uid)
    HELP_CTX.pop(uid, None)
    note = " <i>(уже пройден — можно повторить)</i>" if n in done_set(uid) else ""
    await send(uid, f"{lesson_head(n)}{note}\n\n{x['text']}", reply_markup=kb(
        [btn("✅ Понятно, дальше", f"p:{n}")],
        [btn("🤔 Объясни проще", f"e:{n}"), btn("⏭ Я это знаю", f"k:{n}")]))


async def show_prompt(uid: int, n: int, only=False):
    x = L.LESSONS[n]
    text = (f"📋 <b>Готовый промпт</b> — нажмите на него, чтобы скопировать:\n\n<pre>{esc(x['prompt'])}</pre>\n\n"
            f"💡 {x['tip']}")
    if only:
        return await send(uid, text)
    await send(uid, text, reply_markup=kb([btn("⚡ Дальше: сделай сейчас", f"t:{n}")]))


async def show_task(uid: int, n: int):
    x = L.LESSONS[n]
    tool, url = x["tool"]
    await send(uid, f"⚡ <b>Сделай сейчас</b> (5 минут, бесплатно — {esc(tool)})\n\n{x['task']}\n\n"
                    "<i>Сервисы иногда меняют кнопки. Если что-то выглядит иначе — жмите «🆘 Не получается».</i>",
               reply_markup=kb([btn(f"🔗 Открыть {tool}", url=url)],
                               [btn("✅ Сделал(а)!", f"d:{n}"), btn("🆘 Не получается", f"h:{n}")],
                               [btn("⏩ Сделаю позже — к вопросу", f"q:{n}")]))


async def show_quiz(uid: int, n: int, know=False):
    x = L.LESSONS[n]
    head = ("⏭ <b>Проверим одним вопросом</b> — ответите верно, и урок засчитан:\n\n" if know
            else "🎯 <b>Последний шаг — вопрос по уроку:</b>\n\n")
    tag = "ka" if know else "qa"
    await send(uid, head + esc(x["q"]), reply_markup=kb(*[[btn(o[:60], f"{tag}:{n}:{i}")]
                                                          for i, o in enumerate(x["opts"])]))


async def lesson_done(uid: int, n: int, how: str, bonus: int = 0):
    HELP_CTX.pop(uid, None)
    first = finish(uid, n, how)
    gained = (10 if how == "done" else 5) + bonus if first else bonus
    xp = add_xp(uid, gained) if gained else course(uid)["xp"]
    lvl, nxt = level(xp)
    nxt_line = f" · до следующего уровня {nxt - xp}" if nxt else ""
    head = "🎉 <b>Урок пройден!</b>" if how == "done" else "✅ <b>Урок засчитан — Вы это знаете!</b>"
    nxt_n = n + 1
    lines = [head, f"⭐ +{gained} очков · всего {xp} · {lvl}{nxt_line}", f"🔥 Серия: {streak(uid)} дн. подряд"]
    rows = []
    if nxt_n < L.TOTAL:
        lines.append(f"\n➡️ Дальше: <b>{esc(L.LESSONS[nxt_n]['title'])}</b>")
        if how == "done" and done_today_count(uid) == 1:
            lines.append("Завтра утром напомню. Есть время и желание — можно прямо сейчас 🙂")
        rows.append([btn("▶️ Следующий урок", f"l:{nxt_n}")])
    else:
        lines.append("\n🏆 Вы прошли весь курс!")
    if how == "skip":
        rows.append([btn("📋 Показать промпт этого урока", f"pp:{n}")])
    rows.append([btn("🎮 Сыграть 2 минуты", "gm")])
    await send(uid, "\n".join(lines), reply_markup=kb(*rows))
    if nxt_n >= L.TOTAL and first:
        await notify_admin(f"🏆 {who(db.get_student(uid))} прошёл(ла) весь курс!")


def done_today_count(uid) -> int:
    return db.c.execute("SELECT COUNT(*) FROM ai_done WHERE student_id=? AND day=?",
                        (uid, today().isoformat())).fetchone()[0]


async def course_finished(uid: int):
    await send(uid, "🏆 <b>Весь курс пройден!</b>\n\nТеперь главное — практика:\n"
                    "🎮 игры для повторения · 📋 все промпты · 🙋 наставник поможет с заказами и проектами.\n"
                    "Любой урок можно повторить в «🗺 Программа».",
               reply_markup=kb([btn("🎮 Игры", "gm"), btn("🗺 Программа", "prog")]))


async def lesson_of_day(uid: int):
    n = cur_lesson(uid)
    if n >= L.TOTAL:
        return await course_finished(uid)
    if done_today(uid):
        return await send(uid, f"✅ Сегодня урок уже пройден — отлично!\n\nХотите ещё? Следующий: "
                               f"<b>{esc(L.LESSONS[n]['title'])}</b> (~{L.LESSONS[n]['min']} мин)",
                          reply_markup=kb([btn("▶️ Да, ещё урок", f"l:{n}")], [btn("🎮 Лучше игра", "gm")]))
    await open_lesson(uid, n)


async def _drop_kb(c: CallbackQuery):
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


def _num(data: str, k=1):
    try:
        return int(data.split(":")[k])
    except (ValueError, IndexError):
        return None


@dp.callback_query(F.data.regexp(r"^(l|p|e|k|t|d|h|q|pp):\d+$"))
async def cb_lesson(c: CallbackQuery):
    uid = c.from_user.id
    if not student_or_none(uid):
        return await c.answer("Сначала /start", show_alert=True)
    step, n = c.data.split(":")[0], _num(c.data)
    if not L.lesson(n):
        return await c.answer()
    await c.answer()
    if step != "pp":
        await _drop_kb(c)
    if step == "l":
        return await open_lesson(uid, n)
    if step == "p":
        return await show_prompt(uid, n)
    if step == "pp":
        return await show_prompt(uid, n, only=True)
    if step == "t":
        return await show_task(uid, n)
    if step == "k":
        return await show_quiz(uid, n, know=True)
    if step == "q":
        return await show_quiz(uid, n)
    if step == "d":
        if sent_once(f"task:{uid}:{n}", date(2000, 1, 1)):  # бонус за задание — один раз на урок
            add_xp(uid, 5)
            await send(uid, "💪 Супер! +5 очков за практику. Пришлите результат сюда 📎 — преподавателю будет приятно.")
        return await show_quiz(uid, n)
    if step == "h":
        HELP_CTX[uid] = n
        return await send(uid, "🆘 Разберёмся вместе!\n\nНапишите (или скажите голосом 🎤), <b>на каком шаге застряли "
                               "и что видите на экране</b>. Например: «нажала Нарисуй, а он пишет, что не может».",
                          reply_markup=kb([btn("⏩ Пропустить задание — к вопросу", f"q:{n}")]))
    if step == "e":
        return await explain_simpler(uid, n)


@dp.callback_query(F.data.regexp(r"^(qa|ka):\d+:\d+$"))
async def cb_quiz(c: CallbackQuery):
    uid = c.from_user.id
    if not student_or_none(uid):
        return await c.answer()
    tag, n, i = c.data.split(":")
    n, i = int(n), int(i)
    x = L.lesson(n)
    if not x or i >= len(x["opts"]):
        return await c.answer()
    ok = i == x["a"]
    await c.answer("✅ Верно!" if ok else "❌ Не совсем")
    await _drop_kb(c)
    verdict = ("✅ Верно!" if ok else f"❌ Не совсем. Правильно: <b>{esc(x['opts'][x['a']])}</b>") + f"\n<i>{esc(x['why'])}</i>"
    if tag == "ka":
        if ok:
            await send(uid, verdict)
            return await lesson_done(uid, n, "skip")
        await send(uid, verdict + "\n\nДавайте быстро пройдём этот урок — пара минут, и будет понятно 🙂")
        return await open_lesson(uid, n)
    await send(uid, verdict)
    await lesson_done(uid, n, "done", bonus=5 if ok else 0)


# ---------------- наставник (ИИ) ----------------

MENTOR = """Ты — личный наставник по практическому ИИ в Telegram-боте «{title}». Ты учишь взрослого новичка, который не программист и не очень привык к технологиям (но может уже что-то знать). Ученик хорошо знает английский.

ГЛАВНОЕ:
- Объясняй так, чтобы понял человек без технического опыта: простые слова, примеры из жизни. Любой термин сразу объясняй в скобках.
- Коротко и по делу, без воды: обычно до 900 символов. Подробнее — только если просят.
- Действия — нумерованные шаги, одно действие в шаге. Если человек застрял — веди по ОДНОМУ шагу и жди ответа.
- Обращайся на «Вы», тепло и спокойно. Хвали за конкретное, не льсти.
- Если ученик говорит, что тему уже знает — проверь одним коротким вопросом и предложи следующий шаг посложнее.
- Промпты для копирования — отдельным абзацем. Промпты для картинок и видео — на английском, ниже одной строкой перевод.
- Бесплатные инструменты предлагай первыми.
- Темы курса: картинки и видео с ИИ, сайты и боты (вайбкодинг), автоматизации, заработок на ИИ-услугах (фриланс, услуги для бизнеса).

КУРС:
- Сейчас ученик на уроке: {lesson}
- Уже пройдено: {done}
- О себе: {profile}
- Кнопки бота: «📖 Урок дня», «🎮 Игры», «📋 Мои промпты», «🗺 Программа», «📈 Мои успехи». Других не придумывай.
{extra}
АКТУАЛЬНАЯ СПРАВКА (ноябрь 2026 — опирайся на неё, твои знания могут быть устаревшими):
- Чаты: ChatGPT, Gemini (в нём Gems — свои помощники), Claude (артефакты — страницы рядом с чатом).
- Картинки: Nano Banana в Gemini, GPT Image, Midjourney, Flux, Canva.
- Видео: Kling, Hailuo, Grok Imagine (есть бесплатные кредиты), Google Veo, Seedance, Runway. Монтаж: CapCut.
- Голос/музыка: ElevenLabs, Suno.
- Сайты и приложения: Claude, Google AI Studio (Build), Lovable, Bolt, Replit.
- Боты и автоматизации: @BotFather, Make, n8n; ключ Gemini API — в Google AI Studio.
- Фриланс: Kwork, Upwork, Fiverr; местные клиенты — 2ГИС, Instagram.
- Видео-промпт: камера + кто + действие + где + свет + стиль + звук; одна сцена = одно действие; лучше image-to-video.

ЧЕСТНОСТЬ (обязательно):
- Ты ИИ-наставник, не человек.
- НЕ выдумывай ссылки, цены, лимиты бесплатных тарифов, названия кнопок, версии моделей. Не уверен — скажи «проверьте на сайте сервиса».
- Ты НЕ видишь фото, видео и файлы, которые присылают в бот (их смотрит преподаватель). Попроси описать словами.
- Не обещай заработок и «вирусность». Не помогай с дипфейками без согласия, фальшивыми отзывами и обманом.
- Секреты (токены, API-ключи, пароли) никогда не проси прислать в чат.

ФОРМАТ TELEGRAM: простой текст и эмодзи, без markdown (* # _ `)."""


def mentor_system(uid: int, extra: str = "") -> str:
    s = db.get_student(uid)
    n = min(cur_lesson(uid), L.TOTAL - 1)
    ds = sorted(done_set(uid))
    done = "; ".join(f"{i + 1}. {L.LESSONS[i]['title']}" for i in ds[-12:]) or "пока ничего"
    lesson = f"{n + 1}. {L.LESSONS[n]['title']} ({L.block_name(n)})"
    return MENTOR.format(title=TITLE, lesson=lesson, done=done, profile=(s["profile"] or "не указано"),
                         extra=(extra + "\n") if extra else "")


def lesson_context(n: int) -> str:
    x = L.LESSONS[n]
    return (f"КОНТЕКСТ УРОКА {n + 1} «{x['title']}»:\n{strip_tags(x['text'])}\n"
            f"Промпт урока: {x['prompt']}\nЗадание: {x['task']}")


async def ai_answer(uid: int, user_text: str, system: str, save=True, max_tokens=1500) -> str | None:
    if not ai.enabled():
        await send(uid, "ИИ-наставник сейчас недоступен. Я сообщил преподавателю.")
        await alert_admin_throttled("⚠️ Нет GROQ_API_KEY — наставник не работает.")
        return None
    async with lock_for(uid):
        msgs = (db.history(uid, 12) if save else []) + [{"role": "user", "content": user_text}]
        await bot.send_chat_action(uid, "typing")
        try:
            raw = await ai.converse(system, msgs, max_tokens=max_tokens)
        except Exception as e:
            log.exception("mentor")
            code = str(e) if isinstance(e, ai.AIError) else "BUSY"
            if code == "BUSY":
                await send(uid, "⏳ Наставник сейчас перегружен. Подождите минуту и отправьте ещё раз — Вы ничего не сломали 🙂")
            else:
                await send(uid, "⚠️ Техническая неполадка. Я уже сообщил преподавателю.")
            await alert_admin_throttled(f"⚠️ Groq: {esc(code)}. Проверьте console.groq.com / GROQ_API_KEY.")
            return None
    raw = (raw or "").strip()
    if not raw:
        await send(uid, "🤔 Не получилось ответить. Отправьте, пожалуйста, ещё раз.")
        return None
    if save:
        db.add_msg(uid, "user", user_text)
        db.add_msg(uid, "assistant", raw)
    return ai.clean_md(raw)


async def explain_simpler(uid: int, n: int):
    x = L.LESSONS[n]
    out = await ai_answer(uid, f"Объясни мне ещё проще тему урока «{x['title']}». Я не до конца понял(а).",
                          mentor_system(uid, lesson_context(n) + "\nОбъясни по-другому, чем в уроке: через сравнение "
                                        "из обычной жизни, 5–8 коротких предложений, без терминов. В конце один "
                                        "простой пример."), max_tokens=1200)
    if out:
        await send(uid, "🤔 <b>Попробую иначе:</b>\n\n" + out, reply_markup=kb(
            [btn("✅ Теперь понятно, дальше", f"p:{n}")],
            [btn("🙋 Остался вопрос — напишу его", "ask")]))


async def mentor(uid: int, text: str, prefix: str = ""):
    extra = ""
    n = HELP_CTX.get(uid)
    if n is not None:
        extra = (lesson_context(n) + "\nУченик застрял на задании этого урока. Помоги пройти его по шагам: "
                 "по одному шагу, жди ответа. Если сервис изменился — предложи равноценную бесплатную замену из справки.")
    out = await ai_answer(uid, text, mentor_system(uid, extra))
    if not out:
        return
    db.mark(uid, today(), DONE)
    rows = []
    if n is not None:
        rows = [[btn("✅ Получилось!", f"d:{n}"), btn("⏩ К вопросу урока", f"q:{n}")]]
    await send(uid, prefix + out, reply_markup=kb(*rows) if rows else None)


@dp.callback_query(F.data == "ask")
async def cb_ask(c: CallbackQuery):
    await c.answer()
    await send(c.from_user.id, "🙋 Напишите вопрос обычным сообщением или скажите голосом 🎤 — отвечу простыми словами.")


# ---------------- игры ----------------

def games_kb():
    return kb(*[[btn(name, f"gs:{k}")] for k, name in games.GAMES.items()])


async def show_games(uid: int):
    await send(uid, "🎮 <b>Игры</b> — по 7 вопросов, 2–3 минуты. За каждый верный ответ +1 очко.\n"
                    "Вопросы не повторяются, пока не пройдёте все.", reply_markup=games_kb())


def seen_ids(uid) -> set:
    return {r[0] for r in db.c.execute("SELECT qid FROM ai_seen WHERE student_id=?", (uid,))}


async def game_start(uid: int, kind: str):
    seen = seen_ids(uid)
    prefix = {"tool": "t", "prompt": "p", "term": "w", "myth": "m"}[kind]
    mine = {q for q in seen if q[:1] == prefix}
    if len(mine) + GAME_ROUND > games.total(kind):  # почти всё пройдено — новый круг
        db.c.execute("DELETE FROM ai_seen WHERE student_id=? AND substr(qid,1,1)=?", (uid, prefix))
        db.c.commit()
        mine = set()
    qs = games.pick(kind, mine, GAME_ROUND)
    GAME[uid] = {"kind": kind, "qs": qs, "i": 0, "right": 0}
    await game_next(uid)


async def game_next(uid: int):
    st = GAME.get(uid)
    if not st:
        return
    if st["i"] >= len(st["qs"]):
        r, t = st["right"], len(st["qs"])
        GAME.pop(uid, None)
        db.c.execute("INSERT INTO ai_games_log VALUES(?,?,?,?,?)", (uid, today().isoformat(), st["kind"], r, t))
        db.c.commit()
        db.mark(uid, today(), DONE)
        xp = add_xp(uid, r)
        verdict = ("Идеально! 🔥" if r == t else "Отлично 👍" if r / t >= 0.7 else "Хорошая тренировка 💪 Сыграйте ещё!")
        return await send(uid, f"🏁 <b>{r} из {t}</b>. {verdict}\n⭐ +{r} очков · всего {xp}", reply_markup=games_kb())
    q = st["qs"][st["i"]]
    num = f"{st['i'] + 1}/{len(st['qs'])}"
    await send(uid, f"<i>{num}</i> {q['q']}",
               reply_markup=kb(*[[btn(o[:60], f"ga:{st['i']}:{j}")] for j, o in enumerate(q["opts"])]))


@dp.callback_query(F.data == "gm")
async def cb_games_menu(c: CallbackQuery):
    await c.answer()
    if student_or_none(c.from_user.id):
        await show_games(c.from_user.id)


@dp.callback_query(F.data.startswith("gs:"))
async def cb_game_start(c: CallbackQuery):
    kind = c.data.split(":")[1]
    if not student_or_none(c.from_user.id) or kind not in games.GAMES:
        return await c.answer()
    await c.answer(games.GAMES[kind])
    await game_start(c.from_user.id, kind)


@dp.callback_query(F.data.regexp(r"^ga:\d+:\d+$"))
async def cb_game_answer(c: CallbackQuery):
    uid = c.from_user.id
    st = GAME.get(uid)
    _, i, j = c.data.split(":")
    i, j = int(i), int(j)
    if not st or i != st["i"]:
        return await c.answer("Этот вопрос уже позади 🙂 Откройте «🎮 Игры», чтобы сыграть ещё.", show_alert=True)
    q = st["qs"][i]
    ok = j == q["a"]
    st["right"] += int(ok)
    st["i"] += 1
    db.c.execute("INSERT OR IGNORE INTO ai_seen VALUES(?,?)", (uid, q["id"]))
    db.c.commit()
    await c.answer("✅ Верно!" if ok else "❌ Неверно")
    await _drop_kb(c)
    right = q["opts"][q["a"]]
    await send(uid, ("✅ Верно! " if ok else f"❌ Правильно: <b>{esc(right)}</b>. ")
               + f"<i>{esc(q['why'])}</i>")
    await game_next(uid)


# ---------------- программа и промпты ----------------

def program_text(uid: int) -> str:
    ds, cur = done_set(uid), cur_lesson(uid)
    lines = [f"🗺 <b>Программа курса</b> — {len(ds)} из {L.TOTAL} уроков\n{progress_bar(len(ds), L.TOTAL)}\n"]
    for code, first in L.block_starts():
        idx = [i for i, x in enumerate(L.LESSONS) if x["block"] == code]
        k = sum(1 for i in idx if i in ds)
        lines.append(f"{L.BLOCKS[code]} — {k}/{len(idx)}")
    lines.append("\nВыберите тему — покажу уроки. Можно перейти к любому, даже вперёд 👇")
    if cur < L.TOTAL:
        lines.insert(1, f"Сейчас: <b>{esc(L.short(cur))}</b>\n")
    return "\n".join(lines)


def program_kb():
    return kb(*[[btn(L.BLOCKS[code], f"bl:{code}")] for code, _ in L.block_starts()])


@dp.callback_query(F.data == "prog")
async def cb_prog(c: CallbackQuery):
    await c.answer()
    if student_or_none(c.from_user.id):
        await send(c.from_user.id, program_text(c.from_user.id), reply_markup=program_kb())


@dp.callback_query(F.data.startswith("bl:"))
async def cb_block(c: CallbackQuery):
    uid = c.from_user.id
    code = c.data.split(":")[1]
    if not student_or_none(uid) or code not in L.BLOCKS:
        return await c.answer()
    await c.answer()
    ds = done_set(uid)
    rows = [[btn(f"{'✅' if i in ds else '▫️'} {i + 1}. {x['title']}"[:60], f"l:{i}")]
            for i, x in enumerate(L.LESSONS) if x["block"] == code]
    await send(uid, f"{L.BLOCKS[code]}\n✅ — пройдено. Нажмите на урок, чтобы открыть:", reply_markup=kb(*rows))


async def show_prompts(uid: int):
    ds = done_set(uid)
    if not ds:
        return await send(uid, "📋 Здесь будут все готовые промпты из пройденных уроков.\n"
                               "Пройдите первый урок — и первый промпт появится тут 🙂",
                          reply_markup=kb([btn("📖 Начать урок", f"l:{cur_lesson(uid)}")]))
    codes = [code for code, _ in L.block_starts() if any(L.LESSONS[i]["block"] == code for i in ds)]
    await send(uid, f"📋 <b>Мои промпты</b> — {len(ds)} шт. Выберите тему:",
               reply_markup=kb(*[[btn(L.BLOCKS[c], f"pb:{c}")] for c in codes]))


@dp.callback_query(F.data.startswith("pb:"))
async def cb_prompt_block(c: CallbackQuery):
    uid = c.from_user.id
    code = c.data.split(":")[1]
    if not student_or_none(uid) or code not in L.BLOCKS:
        return await c.answer()
    await c.answer()
    ds = done_set(uid)
    parts = [f"{L.BLOCKS[code]} — нажмите на промпт, чтобы скопировать\n"]
    for i, x in enumerate(L.LESSONS):
        if x["block"] == code and i in ds:
            parts.append(f"<b>{i + 1}. {esc(x['title'])}</b>\n<pre>{esc(x['prompt'])}</pre>\n")
    await send(uid, "\n".join(parts))


# ---------------- успехи ----------------

def summary_text(s, for_admin=True, days=7) -> str:
    uid = s["id"]
    end = today()
    start = end - timedelta(days=days - 1)
    st = db.stats(uid, start, end)
    c = course(uid)
    ds = done_set(uid)
    week = db.c.execute("SELECT COUNT(*), SUM(how='skip') FROM ai_done WHERE student_id=? AND day BETWEEN ? AND ?",
                        (uid, start.isoformat(), end.isoformat())).fetchone()
    g = db.c.execute("SELECT COALESCE(SUM(good),0), COALESCE(SUM(total),0) FROM ai_games_log "
                     "WHERE student_id=? AND day BETWEEN ? AND ?", (uid, start.isoformat(), end.isoformat())).fetchone()
    lvl, nxt = level(c["xp"])
    games_line = f"{g[0]} из {g[1]} верно" if g[1] else "пока не играли"
    cur = f"{c['cur'] + 1}. {L.LESSONS[c['cur']]['title']}" if c["cur"] < L.TOTAL else "курс пройден 🏆"
    if not for_admin:
        return (f"📈 <b>Ваши успехи</b>\n\n"
                f"⭐ Очки: <b>{c['xp']}</b> · {lvl}" + (f" (до следующего — {nxt - c['xp']})" if nxt else "") + "\n"
                f"🔥 Серия: {streak(uid)} дн. подряд · за неделю {st['active_days']} из 7 дней\n"
                f"📖 Уроков пройдено: {len(ds)} из {L.TOTAL}\n{progress_bar(len(ds), L.TOTAL)}\n"
                f"🎮 Игры за неделю: {games_line}\n"
                f"📎 Работ прислано за неделю: {len(st['works'])}\n\n"
                f"➡️ Сейчас: {esc(cur)}\n\n"
                + ("Вы отлично занимаетесь! 👏" if st["active_days"] >= 5 else "Каждый день по 10 минут — и всё получится 🙂"))
    links = [x["content"] for x in st["works"] if x["kind"] == "link"]
    lines = [f"👤 <b>{who(s)}</b>",
             f"Активных дней: <b>{st['active_days']}/{st['days']}</b> · серия {streak(uid)}",
             f"Уроков за неделю: {week[0] or 0} (из них «знаю» — {week[1] or 0}) · всего {len(ds)}/{L.TOTAL}",
             f"Сейчас: {esc(cur)}",
             f"Очки: {c['xp']} · {lvl} · игры: {games_line}",
             f"📎 Работ: {len(st['works'])}"]
    if links:
        lines.append("Ссылки: " + " ".join(links[-5:]))
    return "\n".join(lines)


# ---------------- старт и знакомство ----------------

ONBOARD = [
    ("exp", "1️⃣ Вы уже пользовались ИИ (ChatGPT, Gemini и т.п.)?", ["Ни разу", "Иногда", "Часто"]),
    ("want", "2️⃣ Что Вам интереснее всего?",
     ["🎨 Картинки и видео", "💻 Сайты и боты", "💰 Заработать на ИИ", "Всё понемногу"]),
    ("dev", "3️⃣ С чего будете заниматься?", ["📱 Телефон", "💻 Компьютер", "И то, и то"]),
]


async def ask_onboarding(uid: int, step: int):
    _, q, opts = ONBOARD[step]
    await send(uid, q, reply_markup=kb(*[[btn(o, f"ob:{step}:{i}")] for i, o in enumerate(opts)]))


async def intro(uid: int, first_name: str):
    course(uid)
    db.clear_history(uid)
    await send(uid, f"Привет, {esc(first_name)}! 👋\n\n"
                    "Я научу Вас пользоваться ИИ <b>на практике</b>:\n"
                    "🎨 картинки и видео · 💻 сайты и боты без программирования · 💰 как на этом зарабатывать.\n\n"
                    "Как это устроено: <b>каждый день один урок на 5–10 минут</b> — простыми словами, "
                    "с готовым промптом и маленьким заданием. Без лекций и воды.\n\n"
                    "Сначала 3 коротких вопроса — отвечайте кнопками.")
    await ask_onboarding(uid, 0)


@dp.callback_query(F.data.regexp(r"^ob:\d+:\d+$"))
async def cb_onboarding(c: CallbackQuery):
    uid = c.from_user.id
    if not student_or_none(uid):
        return await c.answer()
    _, step, i = c.data.split(":")
    step, i = int(step), int(i)
    if step >= len(ONBOARD) or i >= len(ONBOARD[step][2]):
        return await c.answer()
    key, q, opts = ONBOARD[step]
    await c.answer("Записал ✅")
    try:
        await c.message.edit_text(f"{q}\n<b>→ {opts[i]}</b>", reply_markup=None)
    except Exception:
        pass
    ONB_STATE.setdefault(uid, {})[key] = opts[i]
    if step + 1 < len(ONBOARD):
        return await ask_onboarding(uid, step + 1)
    st = ONB_STATE.pop(uid, {})
    db.set_profile(uid, f"Опыт с ИИ: {st.get('exp')}. Интересно: {st.get('want')}. Устройство: {st.get('dev')}.")
    tip = ("\n\nРаз Вы уже пользуетесь ИИ — первые уроки можно пролетать кнопкой <b>«⏭ Я это знаю»</b>."
           if st.get("exp") == "Часто" else "")
    jump = ""
    want = st.get("want", "")
    if "Картинки" in want or "Сайты" in want or "Заработать" in want:
        jump = "\nЛюбимую тему можно открыть сразу в <b>«🗺 Программа»</b>, но лучше начать с основ — они короткие."
    await send(uid, "Спасибо! 🙏 Учту это." + tip + jump)
    await send(uid, HELP.format(m=MORNING, e=EVENING), reply_markup=menu_kb())
    await send(uid, "Готовы? Первый урок — 5 минут 👇", reply_markup=kb([btn("📖 Начать первый урок", f"l:{cur_lesson(uid)}")]))


@dp.message(CommandStart())
async def start(m: Message, command: CommandObject):
    u = m.from_user
    if is_admin(m) and not student_or_none(u.id):
        me = await bot.me()
        return await m.answer(
            f"👋 Ты — преподаватель. Бот: <b>{TITLE}</b>\n\n"
            "/students — ученики и активность\n/svodka — сводка за неделю\n"
            "/obnovit — показать ученику новое знакомство и меню\n"
            "/reset — начать курс ученика заново\n/remove — отключить ученика\n"
            "/say ID текст — написать ученику от бота\n/ya_student — попробовать как ученик\n\n"
            f"Ссылка для ученика: https://t.me/{me.username}" + (f"?start={INVITE_CODE}" if INVITE_CODE else ""),
            disable_web_page_preview=True)
    if student_or_none(u.id):
        return await m.answer("Вы уже учитесь 🙂 Кнопки внизу — всё, что нужно.", reply_markup=menu_kb())
    if INVITE_CODE and (command.args or "").strip() != INVITE_CODE:
        return await m.answer("🔒 Этот бот работает по приглашению. Попросите ссылку у преподавателя.")
    db.add_student(u.id, u.full_name, u.username or "", today())
    sent_once(f"morning:{u.id}", today())
    sent_once(f"v2intro:{u.id}", date(2000, 1, 1))
    await notify_admin(f"🆕 Новый ученик: {esc(u.full_name)} (ID <code>{u.id}</code>)")
    await intro(u.id, u.first_name)


# ---------------- кнопки и сообщения ----------------

async def b_lesson(m, s):
    await lesson_of_day(s["id"])


async def b_games(m, s):
    await show_games(s["id"])


async def b_prompts(m, s):
    await show_prompts(s["id"])


async def b_program(m, s):
    await send(s["id"], program_text(s["id"]), reply_markup=program_kb())


async def b_ask(m, s):
    HELP_CTX.pop(s["id"], None)
    await send(s["id"], "🙋 <b>Спросите что угодно</b> про ИИ, картинки, видео, сайты, ботов или заработок — "
                        "текстом или голосом 🎤 (нажмите и держите микрофон).\n\n"
                        "Например:\n• «Как сделать аватарку для Instagram?»\n• «Что предложить кафе, которое знаю?»\n"
                        "• «Проверь мой промпт: …»")


async def b_progress(m, s):
    await send(s["id"], summary_text(s, for_admin=False))


async def b_help(m, s):
    await send(s["id"], HELP.format(m=MORNING, e=EVENING), reply_markup=menu_kb())


BUTTONS = {BTN_LESSON: b_lesson, BTN_GAMES: b_games, BTN_PROMPTS: b_prompts, BTN_PROGRAM: b_program,
           BTN_ASK: b_ask, BTN_PROGRESS: b_progress, BTN_HELP: b_help}


def _student_cmd(fn):
    async def handler(m: Message):
        s = student_or_none(m.from_user.id)
        if not s:
            return await m.answer("Сначала /start")
        await fn(m, s)
    return handler


dp.message.register(_student_cmd(b_lesson), Command("urok", "segodnya", "today", "lesson"))
dp.message.register(_student_cmd(b_games), Command("igry", "games"))
dp.message.register(_student_cmd(b_prompts), Command("promty", "prompts"))
dp.message.register(_student_cmd(b_program), Command("programma", "plan"))
dp.message.register(_student_cmd(b_progress), Command("progress"))
dp.message.register(_student_cmd(b_help), Command("help", "pomosh"))


@dp.message(Command("novyi"))
async def cmd_new(m: Message):
    if student_or_none(m.from_user.id):
        db.clear_history(m.from_user.id)
        HELP_CTX.pop(m.from_user.id, None)
        await m.answer("🧹 Начали разговор с наставником с чистого листа.")


@dp.message(Command("o_sebe"))
async def cmd_profile(m: Message, command: CommandObject):
    s = student_or_none(m.from_user.id)
    if not s:
        return await m.answer("Сначала /start")
    if not command.args:
        cur = f"\n\nСейчас: <i>{esc(s['profile'])}</i>" if s["profile"] else ""
        return await m.answer("Напишите о себе после команды, например:\n"
                              "/o_sebe хочу делать ролики для кафе, есть знакомые с бизнесом, 30 минут в день" + cur)
    db.set_profile(s["id"], command.args.strip()[:500])
    await m.answer("✅ Запомнил. Наставник будет это учитывать.")


@dp.message(F.voice | F.audio)
async def on_voice(m: Message):
    s = student_or_none(m.from_user.id)
    if not s:
        return
    media = m.voice or m.audio
    dur = getattr(media, "duration", 0) or 0
    if not ai.voice_enabled():
        await m.copy_to(ADMIN_ID)
        await notify_admin(f"🎙 Голосовое от {who(s)}")
        return await m.answer("🎙 Получил и передал преподавателю.")
    if dur > 180:
        return await m.answer("Голосовое длиннее 3 минут — разбейте, пожалуйста, на части 🙂")
    await bot.send_chat_action(m.chat.id, "typing")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "v.ogg")
        await bot.download(media, destination=path)
        try:
            r = await ai.with_retry(lambda: ai._transcribe_once(path, "ru", ""))
        except Exception as e:
            log.exception("transcribe")
            code = str(e) if isinstance(e, ai.AIError) else "BUSY"
            await alert_admin_throttled(f"⚠️ Распознавание голоса: {esc(code)}")
            return await m.answer("⏳ Не получилось распознать голос. Попробуйте ещё раз или напишите текстом.")
    text = (r.get("text") or "").strip()
    if not text or ai.looks_like_noise(text) or r.get("no_speech", 0) > 0.8:
        return await m.answer("🎧 Не расслышал. Скажите ещё раз чуть громче или напишите текстом 🙂")
    await mentor(s["id"], text, prefix=f"📝 <i>{esc(text)}</i>\n\n")


@dp.message(F.photo | F.video | F.video_note | F.document | F.animation)
async def on_work(m: Message):
    s = student_or_none(m.from_user.id)
    if not s:
        return
    kind = "photo" if m.photo else "video" if (m.video or m.video_note) else "file"
    db.add_work(s["id"], today(), kind, m.caption or "")
    db.mark(s["id"], today(), DONE)
    await m.copy_to(ADMIN_ID)
    await notify_admin(f"📎 Работа от {who(s)}" + (f": {esc(m.caption)}" if m.caption else ""))
    bonus = ""
    for k in range(3):  # до 3 работ в день дают очки
        if sent_once(f"work{k}:{s['id']}", today()):
            bonus = f" ⭐ +5 очков (всего {add_xp(s['id'], 5)})"
            break
    note = ("\n\nЕсли нужен совет по этой работе — опишите словами, что получилось и что хотели: "
            "я не вижу картинки, их смотрит преподаватель." if HELP_CTX.get(s["id"]) is not None else "")
    await m.answer(f"📎 Принято — передал преподавателю 👏{bonus}{note}")


URL_RE = re.compile(r"https?://\S+")


@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    s = student_or_none(m.from_user.id)
    if is_admin(m) and not s:
        return await m.answer("Чтобы попробовать бота как ученик: /ya_student")
    if not s:
        return await m.answer("Нажмите /start, чтобы начать 🙂")
    if m.text in BUTTONS:
        return await BUTTONS[m.text](m, s)
    links = URL_RE.findall(m.text)
    if links:
        for link in links:
            db.add_work(s["id"], today(), "link", link)
        db.mark(s["id"], today(), DONE)
        await notify_admin(f"🔗 Работа от {who(s)}:\n" + "\n".join(links))
        if len(m.text.strip()) <= len(" ".join(links)) + 3:
            return await m.answer("🔗 Ссылку принял и передал преподавателю 👏")
    await mentor(s["id"], m.text)


# ---------------- преподаватель ----------------

@dp.message(Command("students"))
async def cmd_students(m: Message):
    if not is_admin(m):
        return
    ss = db.students()
    if not ss:
        return await m.answer("Учеников пока нет. Ссылка — в /start.")
    out = []
    for s in ss:
        c = course(s["id"])
        mark = "🟢" if db.has_any_report(s["id"], today()) else "⚪️"
        out.append(f"{mark} {who(s)} — ID <code>{s['id']}</code>, урок {min(c['cur'] + 1, L.TOTAL)}/{L.TOTAL}, "
                   f"очки {c['xp']}, серия {streak(s['id'])}")
    await m.answer("\n".join(out) + "\n\n🟢 — сегодня занимался(ась)")


@dp.message(Command("svodka"))
async def cmd_svodka(m: Message):
    if is_admin(m):
        await send(ADMIN_ID, "\n\n".join(summary_text(s) for s in db.students()) or "Учеников нет.")


def students_kb(action: str):
    ss = db.students()
    if not ss:
        return None
    return kb(*[[btn(f"{s['name'] or 'Без имени'}" + (f" (@{s['username']})" if s["username"] else ""),
                     f"adm:{action}:{s['id']}")] for s in ss])


@dp.message(Command("reset", "remove", "obnovit"))
async def cmd_admin_pick(m: Message, command: CommandObject):
    if not is_admin(m):
        return
    action = command.command
    arg = (command.args or "").split()
    if arg and arg[0].isdigit():
        return await m.answer(await admin_action(action, int(arg[0])))
    k = students_kb(action)
    q = {"reset": "Кому начать курс заново?", "remove": "Кого отключить?",
         "obnovit": "Кому показать новое знакомство и меню?"}[action]
    await m.answer(q if k else "Учеников пока нет.", reply_markup=k)


async def admin_action(action: str, uid: int) -> str:
    s = db.get_student(uid)
    if not s:
        return "Ученик не найден."
    if action == "remove":
        db.deactivate(uid)
        return f"✅ {esc(s['name'])} отключён(а)."
    if action == "reset":
        db.set_start(uid, today())
        db.c.execute("DELETE FROM ai_done WHERE student_id=?", (uid,))
        db.c.execute("UPDATE ai_course SET cur=0, xp=0 WHERE student_id=?", (uid,))
        db.c.commit()
        return f"✅ Курс для {esc(s['name'])} начат заново."
    if action == "obnovit":
        await intro(uid, (s["name"] or "").split(" ")[0] or "друг")
        return "✅ Отправил ученику знакомство и новое меню."
    return "?"


@dp.callback_query(F.data.startswith("adm:"))
async def cb_admin(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer()
    try:
        _, action, uid = c.data.split(":")
        uid = int(uid)
    except ValueError:
        return await c.answer()
    await c.answer("Готово")
    await _drop_kb(c)
    await send(ADMIN_ID, await admin_action(action, uid))


@dp.message(Command("say"))
async def cmd_say(m: Message, command: CommandObject):
    if not is_admin(m):
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        return await m.answer("Пример: /say 123456789 Молодец, так держать!")
    await bot.send_message(int(parts[0]), "👨‍🏫 " + esc(parts[1]))
    await m.answer("Отправлено.")


@dp.message(Command("ya_student"))
async def cmd_ya_student(m: Message):
    if not is_admin(m):
        return
    u = m.from_user
    db.add_student(u.id, u.full_name + " (тест)", u.username or "", today())
    sent_once(f"morning:{u.id}", today())
    sent_once(f"v2intro:{u.id}", date(2000, 1, 1))
    await m.answer("🧪 Ты теперь и ученик — пробуй всё сам. Выйти: /remove и нажми на своё имя.")
    await intro(u.id, u.first_name)


# ---------------- расписание ----------------

async def morning_msg(s):
    uid = s["id"]
    n = cur_lesson(uid)
    sr = streak(uid)
    fire = f" 🔥 Серия: {sr} дн." if sr else ""
    if n >= L.TOTAL:
        return await send(uid, f"☀️ Доброе утро!{fire}\nКурс пройден — 2 минуты игры, чтобы не забывать? 🎮",
                          reply_markup=kb([btn("🎮 Игра дня", "gm")]))
    x = L.LESSONS[n]
    await send(uid, f"☀️ <b>Доброе утро!</b>{fire}\n\nУрок дня — {L.block_name(n)}\n"
                    f"<b>{esc(x['title'])}</b>\n⏱ ~{x['min']} минут",
               reply_markup=kb([btn("📖 Начать урок", f"l:{n}")]))


async def migrate_students():
    """Ученикам, которые были в старой версии бота, — один раз знакомство с новой."""
    for s in db.students():
        if sent_once(f"v2intro:{s['id']}", date(2000, 1, 1)):
            try:
                sent_once(f"morning:{s['id']}", today())
                await intro(s["id"], (s["name"] or "").split(" ")[0] or "друг")
            except TelegramForbiddenError:
                pass
            except Exception as e:
                log.warning("migrate %s: %s", s["id"], e)


async def scheduler():
    await migrate_students()
    while True:
        try:
            now = datetime.now(TZ)
            hm, d = now.strftime("%H:%M"), now.date()
            if MORNING <= hm < EVENING:
                for s in db.students():
                    if not sent_once(f"morning:{s['id']}", d):
                        continue
                    try:
                        await morning_msg(s)
                    except TelegramForbiddenError:
                        await notify_admin(f"🚫 {who(s)} заблокировал(а) бота")
                    except Exception as e:
                        log.warning("morning %s: %s", s["id"], e)
                    miss = db.missed_streak(s["id"], d, date.fromisoformat(s["start_date"]))
                    if miss >= 2:
                        await notify_admin(f"⚠️ {who(s)} не занимается {miss} дн. подряд")
            if EVENING <= hm < "23:30":
                for s in db.students():
                    if db.has_any_report(s["id"], d) or not sent_once(f"evening:{s['id']}", d):
                        continue
                    sr = streak(s["id"])
                    keep = f" и серия 🔥{sr} не прервётся" if sr else ""
                    n = cur_lesson(s["id"])
                    rows = [[btn("🎮 Быстрая игра (2 мин)", "gm")]]
                    if n < L.TOTAL:
                        rows.insert(0, [btn("📖 Урок (5–10 мин)", f"l:{n}")])
                    try:
                        await send(s["id"], f"🌙 Сегодня ещё не занимались. Всего 5 минут{keep} 🙂",
                                   reply_markup=kb(*rows))
                    except Exception as e:
                        log.warning("evening %s: %s", s["id"], e)
            if now.weekday() == 6 and hm >= SUMMARY and sent_once("summary", d):
                ss = db.students()
                if ss:
                    await notify_admin("📊 <b>Сводка за неделю</b>\n\n" + "\n\n".join(summary_text(s) for s in ss))
                for s in ss:
                    try:
                        await send(s["id"], summary_text(s, for_admin=False))
                    except Exception as e:
                        log.warning("summary %s: %s", s["id"], e)
        except Exception:
            log.exception("scheduler")
        await asyncio.sleep(30)


@dp.errors()
async def on_error(event: ErrorEvent):
    if "query is too old" in str(event.exception):  # нажали старую кнопку — не страшно, молчим
        return True
    log.exception("Ошибка обработки: %s", event.exception)
    try:
        msg = event.update.message or (event.update.callback_query and event.update.callback_query.message)
        if msg:
            await bot.send_message(msg.chat.id, "😕 Что-то пошло не так. Попробуйте ещё раз.", parse_mode=None)
    except Exception:
        pass
    await alert_admin_throttled(f"⚠️ Ошибка в боте: {esc(str(event.exception)[:300])}")
    return True


async def main():
    await bot.set_my_commands([
        BotCommand(command="urok", description="Урок дня"),
        BotCommand(command="igry", description="Игры"),
        BotCommand(command="promty", description="Мои промпты"),
        BotCommand(command="programma", description="Программа курса"),
        BotCommand(command="progress", description="Мои успехи"),
        BotCommand(command="help", description="Как пользоваться"),
    ])
    asyncio.create_task(scheduler())
    log.info("Бот %s запущен. Уроков: %d. ИИ: %s, голос: %s", TITLE, L.TOTAL, ai.enabled(), ai.voice_enabled())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
