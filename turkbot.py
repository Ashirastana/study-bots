"""Турецкий бот (TRACK=tr): интенсив с нуля — 30 слов в день, уроки, игры, ИИ-учитель.
Устроен как испанский бот (studybot.py), но со своим контентом и модулями tr_*."""
import asyncio
import logging
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, F  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.filters import Command, CommandObject, CommandStart  # noqa: E402
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError  # noqa: E402
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, ErrorEvent,  # noqa: E402
                           InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup)

import random  # noqa: E402

import tr_ai as ai  # noqa: E402
import tr_plan as plan  # noqa: E402
import tr_lessons as lessons  # noqa: E402
import tr_games as games  # noqa: E402
from db import DB  # noqa: E402
from tr_vocab import Vocab, word_card as _word_card, distractors  # noqa: E402
from tr_vocab_data import WORDS  # noqa: E402
import tr_tts as tts  # noqa: E402

TRACK = os.getenv("TRACK", "tr").strip().lower()
assert TRACK == "tr", "turkbot.py — только для TRACK=tr"
ES = True  # турецкий бот работает по схеме испанского (язык, слова, учитель)
TITLE = "🇹🇷 Турецкий интенсив"

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
INVITE_CODE = os.getenv("INVITE_CODE", "").strip()
TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Almaty"))
MORNING = os.getenv("MORNING_TIME", "09:00")
EVENING = os.getenv("EVENING_TIME", "21:00")
SUMMARY = os.getenv("SUMMARY_TIME", "20:00")  # воскресенье
HOURS_GOAL = float(os.getenv("WEEK_HOURS_GOAL", "10"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(TRACK)

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
db = DB(os.getenv("DB_PATH", "bot.db"))
db.c.execute("CREATE TABLE IF NOT EXISTS sent_log(key TEXT, day TEXT, PRIMARY KEY(key, day))")
db.c.commit()
vocab = Vocab(db.c, per_day=int(os.getenv("WORDS_PER_DAY", "30"))) if ES else None
LOCKS: dict = {}
_last_admin_alert = {"t": 0.0}


def strip_tags(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def chunks(text: str, size=3900):
    """Режет длинный текст по абзацам, чтобы не ломать сообщение."""
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > size:
            out.append(line[:size]); line = line[size:]
        if len(cur) + len(line) + 1 > size:
            out.append(cur); cur = ""
        cur += (("\n" if cur else "") + line)
    if cur:
        out.append(cur)
    return out or [""]


async def send(chat_id: int, text: str, reply_markup=None):
    """Надёжная отправка: длинное режет по абзацам; если HTML не прошёл — шлёт простым текстом.
    Кнопки прикрепляются к последней части."""
    parts = chunks(text)
    for i, part in enumerate(parts):
        kb = reply_markup if i == len(parts) - 1 else None
        try:
            await bot.send_message(chat_id, part, reply_markup=kb)
        except TelegramBadRequest:
            await bot.send_message(chat_id, strip_tags(part), parse_mode=None, reply_markup=kb)


def lock_for(uid):
    if uid not in LOCKS:
        LOCKS[uid] = asyncio.Lock()
    return LOCKS[uid]


def sent_once(key: str, d: date) -> bool:
    """True, если событие ещё не отправлялось сегодня (переживает перезапуск бота)."""
    try:
        db.c.execute("INSERT INTO sent_log(key, day) VALUES(?, ?)", (key, d.isoformat()))
        db.c.commit()
        return True
    except Exception:
        return False


def streak(uid) -> int:
    n, d = 0, today()
    if not db.has_any_report(uid, d):
        d -= timedelta(days=1)
    start = date.fromisoformat(db.get_student(uid)["start_date"])
    while d >= start and db.has_any_report(uid, d):
        n += 1
        d -= timedelta(days=1)
    return n
DONE = "es_done"  # отметка «день сделан» (общее поле языковых ботов в базе)


def today() -> date:
    return datetime.now(TZ).date()


def is_admin(m) -> bool:
    return m.from_user.id == ADMIN_ID


def student_or_none(uid):
    s = db.get_student(uid)
    return s if s and s["active"] else None


def show_kk(uid) -> bool:
    """Казахские подсказки — если ученик не сказал, что казахского не знает."""
    s = db.get_student(uid) if uid else None
    return not (s and s["profile"] and "Казахский: нет" in s["profile"])


def word_card(idx, uid=None) -> str:
    return _word_card(idx, show_kk(uid))


def V(es_text: str, ai_text: str) -> str:
    """Языковой бот — на «Вы»."""
    return es_text if ES else ai_text


BTN_TODAY, BTN_WORDS = "📅 Задание на сегодня", "📚 Слова дня"
BTN_LISTEN, BTN_QUIZ = "🔊 Послушать слова", "🧠 Проверить слова"
BTN_TALK, BTN_PROGRESS = "🗣 Поговорить с учителем", "📈 Мои успехи"
BTN_HELP = "❓ Как пользоваться"
BTN_LESSON, BTN_GAMES, BTN_VIDEO = "📖 Урок дня", "🎮 Игры", "🎬 Видео и подкасты"


def menu_kb():
    if not ES:
        return None
    K = KeyboardButton
    return ReplyKeyboardMarkup(keyboard=[
        [K(text=BTN_TODAY), K(text=BTN_LESSON)],
        [K(text=BTN_WORDS), K(text=BTN_LISTEN)],
        [K(text=BTN_QUIZ), K(text=BTN_GAMES)],
        [K(text=BTN_TALK), K(text=BTN_VIDEO)],
        [K(text=BTN_PROGRESS), K(text=BTN_HELP)],
    ], resize_keyboard=True, is_persistent=True, input_field_placeholder="Пишите по-турецки или запишите голос 🎤")


def report_kb(uid: int | None = None) -> InlineKeyboardMarkup:
    if ES:
        w = plan.week_of(db.day_index(uid, today())) if uid and db.get_student(uid) else 1
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"▶️ Смотреть: {plan.video_name(w)}", url=plan.video_url(w))],
            [InlineKeyboardButton(text="✅ Сегодня всё сделано", callback_data="mark")],
            [InlineKeyboardButton(text="🎬 Посмотрел(а) видео (30 минут)", callback_data="hours:0.5")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сегодня всё сделал", callback_data="mark")],
        [InlineKeyboardButton(text="⏱ +0,5 ч", callback_data="hours:0.5"),
         InlineKeyboardButton(text="⏱ +1 ч", callback_data="hours:1"),
         InlineKeyboardButton(text="⏱ +2 ч", callback_data="hours:2")],
    ])


def who(s) -> str:
    u = f" @{s['username']}" if s["username"] else ""
    return f"{ai.esc(s['name'])}{u}"


def es_step(uid) -> int:
    """Ступень 1–10: растёт с неделями, ускоряется при хороших оценках, замедляется при трудностях."""
    w = plan.week_of(db.day_index(uid, today()))
    step = min(10, w)
    rows = db.c.execute("SELECT score FROM voice_checks WHERE student_id=? AND score IS NOT NULL "
                        "ORDER BY id DESC LIMIT 8", (uid,)).fetchall()
    scores = [r[0] for r in rows if r[0] is not None]
    if len(scores) >= 4:
        avg = sum(scores) / len(scores)
        if avg >= 8.5:
            step += 1
        elif avg < 5.5:
            step -= 1
    return max(1, min(10, step))


def known_words(uid) -> list:
    rows = db.c.execute("SELECT idx FROM word_progress WHERE student_id=? ORDER BY idx", (uid,)).fetchall()
    return [WORDS[r[0]][0] for r in rows if r[0] < len(WORDS)]


def week_info(uid):
    """((цель недели + урок дня, критерий), неделя) — учитель видит, что сегодня проходим."""
    di = db.day_index(uid, today())
    (task, check), w = plan.week_task(di, TRACK)
    return (task + " " + lessons.lesson_topic(di), check), w


async def alert_admin_throttled(text: str):
    import time
    if time.time() - _last_admin_alert["t"] > 1800:
        _last_admin_alert["t"] = time.time()
        await notify_admin(text)


AI_ERR_STUDENT = {
    "BUSY": V("⏳ Учитель сейчас занят. Подождите минуту и отправьте сообщение ещё раз — Вы ничего не сломали 🙂",
              "⏳ ИИ сейчас перегружен. Подожди минуту и отправь ещё раз — это не твоя ошибка."),
    "KEY": V("⚠️ Техническая неполадка. Я уже сообщил преподавателю, он всё исправит.",
             "⚠️ Техническая проблема с ИИ. Я уже сообщил преподавателю."),
    "MODEL": V("⚠️ Техническая неполадка. Я уже сообщил преподавателю, он всё исправит.",
               "⚠️ Техническая проблема с ИИ. Я уже сообщил преподавателю."),
}
AI_ERR_ADMIN = {
    "BUSY": "ИИ (Groq) не отвечает или лимит. Если повторяется часто — проверь console.groq.com.",
    "KEY": "Неверный GROQ_API_KEY в .env. Создай новый ключ на console.groq.com и перезапусти бота.",
    "MODEL": "Groq больше не поддерживает модель. Поменяй GROQ_MODEL / GROQ_STT_MODEL в .env (console.groq.com/docs/models).",
}


async def ai_failed(m: Message, e: Exception):
    code = str(e) if isinstance(e, ai.AIError) else "BUSY"
    await m.answer(AI_ERR_STUDENT.get(code, AI_ERR_STUDENT["BUSY"]))
    await alert_admin_throttled("⚠️ " + AI_ERR_ADMIN.get(code, str(e)))


async def notify_admin(text: str):
    try:
        await bot.send_message(ADMIN_ID, f"[{TITLE}] {text}")
    except Exception as e:
        log.warning("admin notify failed: %s", e)


HELP_ES = ("❓ <b>Как пользоваться ботом</b>\n\n"
           "Всё делается кнопками внизу экрана 👇\n\n"
           "📅 <b>Задание на сегодня</b> — план дня на 1,5–2 часа.\n"
           "📖 <b>Урок дня</b> — правило грамматики с примерами и заданием.\n"
           "📚 <b>Слова дня</b> — 30 новых слов (3 порции по 10). Новые приходят каждое утро сами.\n"
           "🔊 <b>Послушать слова</b> — диктор прочитает слова, повторяйте вслух.\n"
           "🧠 <b>Проверить слова</b> — повторение: бот помнит, что Вы знаете, и возвращает забытое.\n"
           "🎮 <b>Игры</b> — перевод в обе стороны, на слух, правописание, окончания, глаголы.\n"
           "🗣 <b>Поговорить с учителем</b> — ИИ-учитель по-турецки. Отвечайте текстом или голосом "
           "(<b>нажмите и держите 🎤</b>). Он поправит ошибки и поставит оценку.\n"
           "🎬 <b>Видео и подкасты</b> — что смотреть и слушать на Вашем уровне.\n"
           "📈 <b>Мои успехи</b> — серия дней, слова, оценки.\n\n"
           "⏰ Каждое утро в {m} — задание, вечером в {e} — напоминание повторить слова.\n\n"
           "Если что-то непонятно — напишите по-русски «не понимаю» 🙂")
HELP_AI = ("• Каждое утро в {m} — задание дня\n"
           "• ❓ Пиши вопрос обычным сообщением — веду как наставник: шаги, готовые промпты, проверка\n"
           "• 📎 Результаты (фото, видео, ссылки, файлы) — приму и передам преподавателю\n"
           "• В {e} напомню, если не было отчёта\n\n"
           "/segodnya — задание · /plan — план · /progress — прогресс\n"
           "/moiplan — личный план · /video — раскадровка и промпты для ролика\n"
           "/razbor промпт — разбор промпта · /kod идея — вайбкодинг по шагам\n"
           "/test — практический тест · /o_sebe — о себе · /novyi — новый разговор\n"
           "/chasy 1.5 — записать часы")


# ---------------- студент ----------------

@dp.message(CommandStart())
async def start(m: Message, command: CommandObject):
    if is_admin(m):
        me = await bot.me()
        await m.answer(
            f"👋 Ты — преподаватель. Бот: <b>{TITLE}</b>\n\n"
            "/students — список и активность\n"
            "/svodka — сводка за неделю сейчас\n"
            + ("/obnovit — показать ученику новое меню и знакомство (выберешь кнопкой)\n" if ES else "") +
            "/reset — начать план ученика заново (выберешь кнопкой)\n"
            "/remove — отключить ученика или выйти из своего тестового режима (кнопкой)\n"
            "/say ID текст — написать ученику от бота\n"
            "/ya_student — попробовать бота как ученик\n\n"
            f"Ссылка для студента: https://t.me/{me.username}"
            + (f"?start={INVITE_CODE}" if INVITE_CODE else ""))
        return
    if student_or_none(m.from_user.id):
        await m.answer(V("Вы уже занимаетесь 🙂 Кнопки внизу — всё, что нужно.",
                         "Ты уже в программе. /segodnya — задание на сегодня."), reply_markup=menu_kb())
        return
    if INVITE_CODE and (command.args or "").strip() != INVITE_CODE:
        await m.answer(V("🔒 Этот бот работает по приглашению. Попросите ссылку у преподавателя.",
                         "🔒 Нужен код приглашения. Попроси ссылку у преподавателя."))
        return
    u = m.from_user
    db.add_student(u.id, u.full_name, u.username or "", today())
    if ES:
        sent_once(f"morning:{u.id}", today())
        await notify_admin(f"🆕 Новый студент: {ai.esc(u.full_name)} (ID <code>{u.id}</code>)")
        await m.answer(
            f"Merhaba, {ai.esc(u.first_name)}! 👋\n\n"
            "Я — Ваш помощник по турецкому. Это <b>интенсив с нуля</b>: каждый день урок грамматики, "
            "30 новых слов, игры, видео и разговор с ИИ-учителем — около 1,5–2 часов.\n\n"
            "Ошибаться — нормально, для этого я и нужен 🙂\n\n"
            "Давайте познакомимся — <b>4 коротких вопроса</b>, отвечайте кнопками.")
        await ask_onboarding(u.id, 0)
        return
    hello = f"Merhaba, {ai.esc(u.first_name)}! 🎉"
    await m.answer(f"{hello}\n\nЯ веду твою программу: <b>{TITLE}</b>.\n\n"
                   + (HELP_ES if ES else HELP_AI).format(m=MORNING, e=EVENING)
                   + "\n\n👉 Первым делом расскажи о себе: /o_sebe (цели, уровень, интересы)")
    sent_once(f"morning:{u.id}", today())
    await send_today(u.id)
    await notify_admin(f"🆕 Новый студент: {ai.esc(u.full_name)} (ID <code>{u.id}</code>)")


# ---------------- знакомство ----------------

ONBOARD = [
    ("lvl", "1️⃣ Как хорошо Вы знаете турецкий?",
     ["Совсем не знаю", "Знаю несколько слов", "Могу немного говорить"]),
    ("kk", "2️⃣ Знаете казахский? Он родственный турецкому — бот будет подсказывать похожие слова.",
     ["Да, знаю", "Немного", "Нет"]),
    ("goal", "3️⃣ Для чего Вам турецкий?",
     ["Поездки и общение", "Сериалы и музыка", "Учёба, работа, переезд", "Экзамен"]),
    ("time", "4️⃣ Сколько времени в день готовы заниматься?",
     ["30–40 минут", "1 час", "1,5–2 часа", "Больше 2 часов"]),
]
KK_MAP = {"Да, знаю": "да", "Немного": "немного", "Нет": "нет"}
ONB_STATE: dict = {}


async def ask_onboarding(uid: int, step: int):
    key, q, opts = ONBOARD[step]
    kb = [[InlineKeyboardButton(text=o, callback_data=f"ob:{step}:{i}")] for i, o in enumerate(opts)]
    await send(uid, q, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


if ES:
    @dp.callback_query(F.data.startswith("ob:"))
    async def cb_onboarding(c: CallbackQuery):
        uid = c.from_user.id
        if not student_or_none(uid):
            return await c.answer()
        try:
            _, step, i = c.data.split(":")
            step, i = int(step), int(i)
            key, q, opts = ONBOARD[step]
            choice = opts[i]
        except (ValueError, IndexError):
            return await c.answer()
        await c.answer("Записал ✅")
        try:
            await c.message.edit_text(f"{q}\n<b>→ {choice}</b>", reply_markup=None)
        except Exception:
            pass
        ONB_STATE.setdefault(uid, {})[key] = choice
        if step + 1 < len(ONBOARD):
            return await ask_onboarding(uid, step + 1)
        st = ONB_STATE.pop(uid, {})
        db.set_profile(uid, f"Уровень: {st.get('lvl')}. Казахский: {KK_MAP.get(st.get('kk'), 'нет')}. "
                            f"Цель: {st.get('goal')}. Время в день: {st.get('time')}.")
        light = st.get("time") == "30–40 минут"
        await send(uid, "Спасибо! 🙏 Я всё запомнил и подстрою занятия под Вас."
                        + ("\n\nРаз у Вас 30–40 минут — делайте каждый день хотя бы урок, слова и разговор "
                           "с учителем, а игры и видео — когда будет время." if light else ""))
        await send(uid, HELP_ES.format(m=MORNING, e=EVENING), reply_markup=menu_kb())
        await send(uid, "А вот Ваше первое задание 👇")
        await send_today(uid)


async def send_today(uid: int):
    di = db.day_index(uid, today())
    await send(uid, plan.today_text(di, TRACK), reply_markup=report_kb(uid))
    await send(uid, lessons.lesson_text(di))
    await send_words(uid)


# ---------------- слова дня + повторение ----------------

PART = 10  # слов в одной порции


async def send_words(uid: int):
    new = vocab.give_new(uid, today())
    if not new:
        await send(uid, "📚 Встроенный словарь (%d слов) пройден! 🎉\n"
                        "Дальше — повторение здесь (/povtor), сериалы с турецкими субтитрами и колода Anki "
                        "«Turkish frequency». Учитель поможет с новыми словами из сериалов." % len(WORDS))
        return
    parts = [new[i:i + PART] for i in range(0, len(new), PART)]
    for p, chunk in enumerate(parts):
        first = p * PART
        head = (f"📚 <b>Слова дня — порция {p + 1} из {len(parts)}</b>\n" if len(parts) > 1
                else "📚 <b>Слова дня</b>\n")
        lines = [head] + [f"{first + n}. {word_card(i, uid)}" for n, i in enumerate(chunk, 1)]
        if p == len(parts) - 1:
            lines.append("\n💡 <b>Как учить:</b> «🔊 Послушать» → повторяйте вслух за диктором → "
                         "закройте перевод и вспомните → вечером «🧠 Проверить» и «🎮 Игры»."
                         + ("\n🇰🇿 — похожее казахское слово: опирайтесь на него!" if show_kk(uid) else ""))
        kb = [[InlineKeyboardButton(text=f"🔊 Послушать {first + 1}–{first + len(chunk)}",
                                    callback_data=f"listen:{p}")]]
        if p == len(parts) - 1:
            kb.append([InlineKeyboardButton(text="🧠 Проверить себя", callback_data="povtor"),
                       InlineKeyboardButton(text="🎮 Игры", callback_data="gm")])
        await send(uid, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


async def send_listen(uid: int, part: int | None = None):
    idx = vocab.introduced_today(uid, today()) or vocab.give_new(uid, today())
    if not idx:
        return await send(uid, "Новых слов сегодня нет 🙂")
    parts = [idx[i:i + PART] for i in range(0, len(idx), PART)]
    todo = [parts[part]] if part is not None and 0 <= part < len(parts) else parts
    for k, chunk in enumerate(todo):
        await bot.send_chat_action(uid, "record_voice")
        audio, fname = await tts.speak(tts.words_script([WORDS[i][0] for i in chunk]), rate=130)
        if not audio:
            await alert_admin_throttled("⚠️ Озвучка турецкого не работает (edge-tts).")
            return await send(uid, "🔊 Озвучка сейчас недоступна. Я сообщил преподавателю.")
        n = (part if part is not None else k) + 1
        await bot.send_audio(uid, BufferedInputFile(audio, filename=f"kelimeler_{n}" + fname[-4:]),
                             duration=tts.duration(audio, fname),
                             title=f"Слова дня — порция {n}", performer="Учитель турецкого",
                             caption="🔊 Каждое слово — 2 раза. После паузы повторите вслух.")


QUIZ: dict = {}  # uid -> {"queue": [idx...], "right": n, "total": n}
LAST_ES: dict = {}  # uid -> последняя турецкая реплика учителя (для озвучки)


async def quiz_next(uid: int):
    st = QUIZ.get(uid)
    if not st or not st["queue"]:
        if st:
            r, t = st["right"], st["total"]
            QUIZ.pop(uid, None)
            if t:
                db.mark(uid, today(), DONE)
                verdict = ("Превосходно! 🔥" if r == t else
                           "Хорошо 👍 Слова с ошибками я повторю с Вами завтра." if r / t >= 0.7 else
                           "Ничего страшного! Слова с ошибками я повторю завтра — так они и запоминаются 💪")
                await send(uid, f"✅ Готово! Правильно: <b>{r} из {t}</b>. {verdict}")
        return
    idx = st["queue"][0]
    es, opts = vocab.question(idx)
    kb = [[InlineKeyboardButton(text=WORDS[o][1][:60], callback_data=f"q:{idx}:{o}")] for o in opts]
    left = len(st["queue"])
    await send(uid, f"🧠 Что значит <b>{es}</b>?  <i>(осталось {left})</i>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


async def start_quiz(uid: int, chat_answer):
    due = vocab.due(uid, today())
    if not due:
        nxt = ("Новые слова придут завтра утром." if vocab.introduced_today(uid, today())
               else "Нажмите «📚 Слова дня», чтобы получить новые слова.")
        return await chat_answer("🎉 На сегодня все слова повторены! " + nxt)
    QUIZ[uid] = {"queue": due, "right": 0, "total": 0}
    await quiz_next(uid)


if ES:
    @dp.message(Command("slova"))
    async def cmd_slova(m: Message):
        if not student_or_none(m.from_user.id):
            return await m.answer("Сначала /start")
        await send_words(m.from_user.id)

    @dp.message(Command("povtor"))
    async def cmd_povtor(m: Message):
        if not student_or_none(m.from_user.id):
            return await m.answer("Сначала /start")
        await start_quiz(m.from_user.id, m.answer)

    @dp.callback_query(F.data.startswith("listen"))
    async def cb_listen(c: CallbackQuery):
        await c.answer("Готовлю аудио…")
        part = None
        if ":" in c.data:
            try:
                part = int(c.data.split(":")[1])
            except ValueError:
                part = None
        if student_or_none(c.from_user.id):
            await send_listen(c.from_user.id, part)

    @dp.callback_query(F.data == "misheard")
    async def cb_misheard(c: CallbackQuery):
        uid = c.from_user.id
        if not student_or_none(uid):
            return await c.answer()
        await c.answer()
        db.c.execute("DELETE FROM voice_checks WHERE id = (SELECT MAX(id) FROM voice_checks WHERE student_id=?)", (uid,))
        db.c.commit()
        db.add_msg(uid, "user", "(Расшифровка моего последнего голосового была неверной — не учитывай её.)")
        db.add_msg(uid, "assistant", "Понял, предыдущую расшифровку не учитываю.")
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await send(uid, "Простите, я неправильно расслышал 🙏 Оценку за это не считаю.\n\n"
                        "Скажите, пожалуйста, ещё раз — <b>медленно и чуть громче</b>, телефон ближе ко рту. "
                        "Или напишите текстом — так тоже отлично.")
        await notify_admin(f"🔁 Ученик отметил неверное распознавание голоса ({ai.esc(c.from_user.full_name)})")

    @dp.callback_query(F.data == "say_last")
    async def cb_say_last(c: CallbackQuery):
        line = LAST_ES.get(c.from_user.id)
        if not line:
            return await c.answer("Нечего озвучить", show_alert=False)
        await c.answer("Готовлю аудио…")
        audio, fname = await tts.speak(line, rate=135)
        if not audio:
            return await send(c.from_user.id, "🔊 Озвучка сейчас недоступна.")
        await bot.send_audio(c.from_user.id, BufferedInputFile(audio, filename="frase" + fname[-4:]),
                             duration=tts.duration(audio, fname),
                             title=line[:60], performer="Учитель")

    @dp.callback_query(F.data == "povtor")
    async def cb_povtor(c: CallbackQuery):
        await c.answer()
        if student_or_none(c.from_user.id):
            await start_quiz(c.from_user.id, lambda t: send(c.from_user.id, t))

    @dp.callback_query(F.data.startswith("q:"))
    async def cb_quiz(c: CallbackQuery):
        uid = c.from_user.id
        try:
            _, idx, choice = c.data.split(":")
            idx, choice = int(idx), int(choice)
        except ValueError:
            return await c.answer()
        st = QUIZ.get(uid)
        if not st or not st["queue"] or st["queue"][0] != idx:
            return await c.answer("Этот вопрос уже позади 🙂 Нажмите «🧠 Проверить слова», чтобы начать заново.", show_alert=True)
        ok = choice == idx
        vocab.answer(uid, idx, ok, today())
        st["queue"].pop(0)
        st["total"] += 1
        st["right"] += int(ok)
        await c.answer("✅ Верно!" if ok else "❌ Неверно")
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        res = "✅ Верно!" if ok else f"❌ Вы выбрали: {ai.esc(WORDS[choice][1])}\nПравильно:"
        await send(uid, f"{res} {word_card(idx, uid)}")
        await quiz_next(uid)

    @dp.message(Command("anki"))
    async def cmd_anki(m: Message):
        if not student_or_none(m.from_user.id):
            return await m.answer("Сначала /start")
        txt = vocab.anki_export(m.from_user.id)
        if not txt:
            return await m.answer("Слов пока нет. Начни с /slova")
        await m.answer_document(BufferedInputFile(txt.encode("utf-8"), filename="turkish_words.txt"),
                                caption="Файл для Anki: Файл → Импорт, разделитель — точка с запятой.")


# ---------------- игры ----------------

GAME: dict = {}  # uid -> {"type", "left", "right", "total", "q"}
GAME_ROUND = 10
GAME_NAMES = {"rt": "🔁 Перевод: русский → турецкий", "tr": "🧠 Перевод: турецкий → русский",
              "au": "🎧 На слух", "sp": "✍️ Правописание", "hm": "🧩 Окончания (гармония гласных)",
              "vb": "⚙️ Глаголы: сейчас / вчера / завтра"}
GAME_HELP = ("🎮 <b>Игры</b> — по 10 вопросов, на Ваших словах.\n\n"
             "🔁 <b>Русский → турецкий</b> — вспомнить слово.\n"
             "🎧 <b>На слух</b> — диктор говорит, Вы выбираете перевод.\n"
             "✍️ <b>Правописание</b> — пишете слово сами (ı, ş, ç, ğ, ö, ü).\n"
             "🧩 <b>Окончания</b> — -lar/-ler, -da/-de, -a/-e, mı/mi… Главный закон турецкого.\n"
             "⚙️ <b>Глаголы</b> — gidiyorum, gittim, gideceğim: время и лицо.")


def games_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=GAME_NAMES[k], callback_data=f"gs:{k}")]
                                                 for k in ("rt", "au", "sp", "hm", "vb")])


async def show_games(uid: int):
    await send(uid, GAME_HELP, reply_markup=games_kb())


def known_idx(uid) -> list:
    rows = db.c.execute("SELECT idx FROM word_progress WHERE student_id=?", (uid,)).fetchall()
    return [r[0] for r in rows if r[0] < len(WORDS)]


def game_pool(uid) -> list:
    k = known_idx(uid)
    return k if len(k) >= 8 else list(range(min(60, len(WORDS))))


async def game_start(uid: int, kind: str):
    GAME[uid] = {"type": kind, "left": GAME_ROUND, "right": 0, "total": 0, "q": None}
    await game_next(uid)


def _opts_kb(labels, n):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(l)[:60], callback_data=f"ga:{n}:{i}")]
                                                 for i, l in enumerate(labels)])


async def game_next(uid: int):
    st = GAME.get(uid)
    if not st:
        return
    if st["left"] <= 0:
        r, t = st["right"], st["total"]
        GAME.pop(uid, None)
        db.mark(uid, today(), DONE)
        verdict = ("Превосходно! 🔥" if r == t else "Хорошо 👍 Ошибки — повод сыграть ещё раз." if r / max(t, 1) >= 0.7
                   else "Ничего страшного — так слова и запоминаются 💪 Сыграйте ещё раз!")
        return await send(uid, f"🏁 Игра окончена: <b>{r} из {t}</b>. {verdict}", reply_markup=games_kb())
    kind, pool, n = st["type"], game_pool(uid), st["total"]
    used = st.setdefault("used", set())
    pool = [i for i in pool if i not in used] or pool  # без повторов в одной игре
    num = f"{n + 1}/{GAME_ROUND}"
    if kind in ("rt", "au", "tr"):
        idx = random.choice(pool)
        used.add(idx)
        opts = distractors(idx, 3, game_pool(uid)) + [idx]
        random.shuffle(opts)
        st["q"] = {"idx": idx, "right": opts.index(idx)}
        if kind == "rt":
            return await send(uid, f"🔁 {num}. Как по-турецки: <b>{ai.esc(WORDS[idx][1])}</b>?",
                              reply_markup=_opts_kb([WORDS[o][0] for o in opts], n))
        labels = [WORDS[o][1] for o in opts]
        if kind == "au":
            audio, fname = await tts.speak(WORDS[idx][0], rate=120)
            if audio:
                await bot.send_audio(uid, BufferedInputFile(audio, filename="kelime" + fname[-4:]),
                                     title=f"Слово {num}", performer="🎧")
                return await send(uid, f"🎧 {num}. Что за слово? Выберите перевод:", reply_markup=_opts_kb(labels, n))
            return await send(uid, f"🎧 Озвучка недоступна. {num}. Что значит <b>{WORDS[idx][0]}</b>?",
                              reply_markup=_opts_kb(labels, n))
        return await send(uid, f"🧠 {num}. Что значит <b>{WORDS[idx][0]}</b>?", reply_markup=_opts_kb(labels, n))
    if kind == "sp":
        idx = random.choice(pool)
        used.add(idx)
        st["q"] = {"idx": idx}
        tr, ru, kk = WORDS[idx]
        hint = f"\n<i>🇰🇿 подсказка: {kk}</i>" if kk and show_kk(uid) else ""
        return await send(uid, f"✍️ {num}. Напишите по-турецки: <b>{ai.esc(ru)}</b>{hint}\n"
                               f"<i>Начинается на «{tr[0]}…», букв: {len(tr)}</i>",
                          reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                              text="🤷 Не знаю — показать", callback_data=f"ga:{n}:skip")]]))
    if kind == "hm":
        q = games.harmony_question(pool)
        used.add(q.get("idx"))
        q["right_i"] = q["options"].index(q["right"])
        st["q"] = q
        return await send(uid, f"🧩 {num}. <b>{q['word']}</b> ({ai.esc(q['ru'])})\n"
                               f"Нужно: <b>{q['title']}</b>, окончание {q['rule']}\nВыберите правильную форму:",
                          reply_markup=_opts_kb(q["options"], n))
    if kind == "vb":
        q = games.verb_question(pool)
        used.add(q.get("idx"))
        q["right_i"] = q["options"].index(q["right"])
        st["q"] = q
        neg = " — <b>отрицание (НЕ)</b>" if q["neg"] else ""
        return await send(uid, f"⚙️ {num}. <b>{q['inf']}</b> ({ai.esc(q['ru'])})\n"
                               f"Кто: <b>{q['person']}</b> ({games.PERSON_RU[q['person']]}) · "
                               f"Когда: {games.TENSES[q['tense']]}{neg}",
                          reply_markup=_opts_kb(q["options"], n))


async def game_answered(uid: int, ok: bool, msg: str):
    st = GAME.get(uid)
    if not st:
        return
    st["q"] = None
    st["left"] -= 1
    st["total"] += 1
    st["right"] += int(ok)
    await send(uid, msg)
    await game_next(uid)


@dp.callback_query(F.data == "gm")
async def cb_games_menu(c: CallbackQuery):
    await c.answer()
    if student_or_none(c.from_user.id):
        await show_games(c.from_user.id)


@dp.callback_query(F.data.startswith("gs:"))
async def cb_game_start(c: CallbackQuery):
    uid = c.from_user.id
    kind = c.data.split(":")[1]
    if not student_or_none(uid) or kind not in GAME_NAMES:
        return await c.answer()
    await c.answer(GAME_NAMES[kind])
    await game_start(uid, kind)


@dp.callback_query(F.data.startswith("ga:"))
async def cb_game_answer(c: CallbackQuery):
    uid = c.from_user.id
    st = GAME.get(uid)
    try:
        _, n, choice = c.data.split(":")
        n = int(n)
    except ValueError:
        return await c.answer()
    if not st or not st["q"] or n != st["total"]:
        return await c.answer("Этот вопрос уже позади 🙂 Откройте «🎮 Игры», чтобы сыграть ещё.", show_alert=True)
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    q, kind = st["q"], st["type"]
    if kind == "sp":
        await c.answer()
        idx = q["idx"]
        vocab.answer(uid, idx, False, today())
        return await game_answered(uid, False, f"Правильно: {word_card(idx, uid)}\nЗапомните и попробуйте в следующий раз 💪")
    i = int(choice)
    if kind in ("rt", "au", "tr"):
        ok = i == q["right"]
        vocab.answer(uid, q["idx"], ok, today())
        await c.answer("✅ Верно!" if ok else "❌ Неверно")
        return await game_answered(uid, ok, ("✅ Верно! " if ok else "❌ Правильно: ") + word_card(q["idx"], uid))
    ok = i == q["right_i"]
    await c.answer("✅ Верно!" if ok else "❌ Неверно")
    if kind == "hm":
        msg = (f"✅ Верно: <b>{q['right']}</b>" if ok else f"❌ Правильно: <b>{q['right']}</b>") + \
              f"\n<i>{games.harmony_explain(q)}</i>"
    else:
        msg = (f"✅ Верно: <b>{q['person']} {q['right']}</b>" if ok else
               f"❌ Правильно: <b>{q['person']} {q['right']}</b>") + f" — {games.TENSES[q['tense']]}"
    await game_answered(uid, ok, msg)


async def spelling_answer(m: Message, s) -> bool:
    """Ответ в игре «Правописание». True — сообщение обработано."""
    st = GAME.get(s["id"])
    if not st or st["type"] != "sp" or not st["q"]:
        return False
    idx = st["q"]["idx"]
    res = games.check_spelling(m.text, WORDS[idx][0])
    ok = res != "wrong"
    vocab.answer(s["id"], idx, ok, today())
    if res == "ok":
        msg = f"✅ Верно! {word_card(idx, s['id'])}"
    elif res == "almost":
        msg = (f"✅ Почти! Слово верное, но обратите внимание на турецкие буквы: <b>{WORDS[idx][0]}</b>\n"
               "<i>ı, ş, ç, ğ, ö, ü — на телефоне: удерживайте i, s, c, g, o, u.</i>")
    else:
        msg = f"❌ Вы написали: <i>{ai.esc(m.text)}</i>\nПравильно: {word_card(idx, s['id'])}"
    await game_answered(s["id"], ok, msg)
    return True


@dp.message(Command("igry", "games"))
async def cmd_games(m: Message):
    if not student_or_none(m.from_user.id):
        return await m.answer("Сначала /start")
    await show_games(m.from_user.id)


@dp.message(Command("urok_dnya", "lesson"))
async def cmd_lesson(m: Message):
    s = student_or_none(m.from_user.id)
    if not s:
        return await m.answer("Сначала /start")
    await b_lesson(m, s)


@dp.message(Command("video"))
async def cmd_video_tr(m: Message):
    s = student_or_none(m.from_user.id)
    if not s:
        return await m.answer(plan.resources_text())
    await b_video(m, s)



@dp.message(Command("ya_student"))
async def cmd_ya_student(m: Message):
    if not is_admin(m):
        return
    u = m.from_user
    db.add_student(u.id, u.full_name + " (тест)", u.username or "", today())
    sent_once(f"morning:{u.id}", today())
    await m.answer("🧪 Ты теперь и ученик — пробуй всё сам. Выйти: /remove и нажми на своё имя.")
    if ES:
        return await ask_onboarding(u.id, 0)
    await send_today(u.id)


@dp.message(Command("segodnya", "today"))
async def cmd_today(m: Message):
    if not student_or_none(m.from_user.id):
        return await m.answer("Сначала /start")
    await send_today(m.from_user.id)


@dp.message(Command("plan"))
async def cmd_plan(m: Message):
    await m.answer(plan.full_plan_text(TRACK))


@dp.message(Command("help", "pomosh"))
async def cmd_help(m: Message):
    await m.answer((HELP_ES if ES else HELP_AI).format(m=MORNING, e=EVENING), reply_markup=menu_kb())


@dp.message(Command("progress"))
async def cmd_progress(m: Message):
    s = student_or_none(m.from_user.id)
    if not s:
        return await m.answer("Сначала /start")
    await m.answer(summary_text(s, for_admin=False))


@dp.message(Command("chasy", "hours"))
async def cmd_hours(m: Message, command: CommandObject):
    if not student_or_none(m.from_user.id):
        return await m.answer("Сначала /start")
    try:
        h = float((command.args or "").replace(",", "."))
        assert 0 < h <= 12
    except Exception:
        return await m.answer("Пример: /chasy 1.5")
    db.add_hours(m.from_user.id, today(), h)
    total = db.stats(m.from_user.id, today(), today())["total_hours"]
    await m.answer(f"⏱ +{h:g} ч. Всего часов: <b>{total:g}</b>")


@dp.callback_query(F.data == "mark")
async def cb_mark(c: CallbackQuery):
    if not student_or_none(c.from_user.id):
        return await c.answer("Сначала /start", show_alert=True)
    db.mark(c.from_user.id, today(), DONE)
    await c.answer(V("Записал ✅ Вы молодец!", "Записал ✅ Молодец!"), show_alert=ES)


@dp.callback_query(F.data.startswith("hours:"))
async def cb_hours(c: CallbackQuery):
    if not student_or_none(c.from_user.id):
        return await c.answer("Сначала /start", show_alert=True)
    h = float(c.data.split(":")[1])
    db.add_hours(c.from_user.id, today(), h)
    r = db.report(c.from_user.id, today())
    if ES:
        db.mark(c.from_user.id, today(), DONE)
        return await c.answer(f"🎬 Записал! Сегодня видео: {round(r['hours'] * 60)} мин 👏", show_alert=True)
    await c.answer(f"+{h:g} ч. Сегодня: {r['hours']:g} ч")


async def dialog(m: Message, s, user_text: str, prefix: str = "", no_score: bool = False, extra_kb=None):
    """Разговор по промпту из PDF: репетитор (es) или наставник (ai), с памятью диалога."""
    if not ai.enabled():
        return await m.answer("ИИ не подключён (нет ключа GROQ_API_KEY в .env).")
    (task, _), w = week_info(s["id"])
    if ES:
        system = ai.build_system(TRACK, w, task, s["profile"] or "", es_step(s["id"]), known_words(s["id"]))
    else:
        system = ai.build_system(TRACK, w, task, s["profile"] or "")
    async with lock_for(s["id"]):
        msgs = db.history(s["id"]) + [{"role": "user", "content": user_text}]
        await bot.send_chat_action(m.chat.id, "typing")
        try:
            raw = await ai.converse(system, msgs)
        except Exception as e:
            log.exception("dialog")
            return await ai_failed(m, e)
        if not (raw or "").strip():
            return await m.answer(V("🤔 Не получилось ответить. Отправьте, пожалуйста, сообщение ещё раз.",
                                    "🤔 ИИ вернул пустой ответ. Отправь сообщение ещё раз."))
        await _finish_dialog(m, s, user_text, raw, prefix, no_score, extra_kb)


async def _finish_dialog(m: Message, s, user_text: str, raw: str, prefix: str, no_score: bool = False,
                         extra_kb=None):
    text, score = ai.split_score(raw)
    spanish_attempt = bool(re.search(r"[a-zçğıöşüâî]", user_text.replace("[ГОЛОС]", ""), re.I))
    if (user_text.startswith("(") or user_text.startswith("/") or user_text.startswith("[ГОЛОС по-русски]")
            or not spanish_attempt):
        # служебный старт, команда или русский текст — оценивать и «исправлять» нечего
        score = None
        text = re.sub(r"✏️\s*Исправление.*?(?=\n\s*\n|\Z)", "", text, flags=re.S).strip()
    if no_score:  # голос расслышан неуверенно — подсказки оставляем, оценку не ставим
        score = None
    db.add_msg(s["id"], "user", user_text)
    db.add_msg(s["id"], "assistant", raw)
    if ES:
        db.mark(s["id"], today(), DONE)
        if score is not None:
            k = text.find("Исправление")
            fb = " ".join(text[k:].replace("**", "").split())[:200] if k >= 0 else ""
            db.add_voice(s["id"], today(), user_text.replace("[ГОЛОС] ", ""), score, fb)
    out = prefix + ai.clean_md(text)
    kb = None
    if ES:
        if score is not None:
            out += f"\n\n📊 <b>Оценка: {score}/10</b>"
        es_line = next((l.replace("🇹🇷", "").strip() for l in text.splitlines() if l.strip().startswith("🇹🇷")), "")
        es_line = re.sub(r"\s*\([^)]*\)", "", es_line).strip()  # убрать русский перевод в скобках
        if es_line and await asyncio.to_thread(tts.spanish_voice):
            LAST_ES[m.from_user.id] = es_line
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="🔊 Послушать, как это звучит", callback_data="say_last")]])
        if extra_kb:
            rows = (kb.inline_keyboard if kb else []) + extra_kb
            kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await send(m.chat.id, out, reply_markup=kb)


async def cmd_dialog(m: Message, text: str):
    s = student_or_none(m.from_user.id)
    if not s:
        return await m.answer("Сначала /start")
    await dialog(m, s, text)


@dp.message(Command("o_sebe"))
async def cmd_profile(m: Message, command: CommandObject):
    s = student_or_none(m.from_user.id)
    if not s:
        return await m.answer("Сначала /start")
    if not command.args:
        example = ("уровень A0, знаю казахский, цель — переезд в Турцию, интересы: сериалы, футбол" if ES else
                   "новичок, цель — делать ИИ-видео на заказ, ноутбук без видеокарты, бюджет 0")
        cur = f"\n\nСейчас: <i>{ai.esc(s['profile'])}</i>" if s["profile"] else ""
        return await m.answer(f"Напиши о себе после команды, например:\n/o_sebe {example}{cur}")
    db.set_profile(s["id"], command.args.strip())
    await m.answer("✅ Запомнил. Теперь занятия будут под тебя.")


@dp.message(Command("novyi"))
async def cmd_new(m: Message):
    if student_or_none(m.from_user.id):
        db.clear_history(m.from_user.id)
        await m.answer("🧹 Начали разговор с чистого листа.")


@dp.message(Command("test"))
async def cmd_test(m: Message):
    await cmd_dialog(m, "/тест")


if ES:
    @dp.message(Command("urok"))
    async def cmd_urok(m: Message):
        await cmd_dialog(m, "/урок")
else:
    @dp.message(Command("moiplan"))
    async def cmd_moiplan(m: Message):
        await cmd_dialog(m, "/план")

    @dp.message(Command("video"))
    async def cmd_video(m: Message, command: CommandObject):
        await cmd_dialog(m, "/видео " + (command.args or ""))

    @dp.message(Command("razbor"))
    async def cmd_razbor(m: Message, command: CommandObject):
        if not command.args:
            return await m.answer("Пришли промпт после команды: /razbor твой промпт")
        await cmd_dialog(m, "/разбор " + command.args)

    @dp.message(Command("kod"))
    async def cmd_kod(m: Message, command: CommandObject):
        await cmd_dialog(m, "/код " + (command.args or ""))


@dp.message(F.voice | F.audio)
async def on_voice(m: Message):
    s = student_or_none(m.from_user.id)
    if not s:
        return
    if not ES or not ai.voice_enabled():
        await m.copy_to(ADMIN_ID)
        await notify_admin(f"🎙 Голосовое от {who(s)}")
        db.mark(s["id"], today(), DONE)
        return await m.answer("🎙 Получил и передал преподавателю.")
    media = m.voice or m.audio
    dur = getattr(media, "duration", 0) or 0
    if dur and dur < 1:
        return await m.answer("Слишком коротко — скажите хотя бы одну фразу 🙂")
    if dur > 180:
        return await m.answer("Голосовое длиннее 3 минут. Разбейте, пожалуйста, на части — так я точнее проверю.")
    await m.answer("🎧 Слушаю…")
    context = voice_context(s["id"])
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "v.ogg")
        await bot.download(media, destination=path)
        try:
            r = await ai.transcribe_smart(path, "")  # без подсказок: слышим как есть
        except Exception as e:
            log.exception("transcribe")
            return await ai_failed(m, e)
    log.info("voice uid=%s kind=%s conf=%.2f text=%r", s["id"], r["kind"], r.get("conf", 0), r["text"][:80])
    if r["kind"] == "noise":
        return await m.answer("🎧 Не расслышал. Скажите, пожалуйста, ещё раз — медленно, чуть громче "
                              "и ближе к телефону. Или напишите текстом 🙂")
    wrong_kb = [[InlineKeyboardButton(text="🔁 Я сказал(а) не это", callback_data="misheard")]]
    if r["kind"] == "ru":
        return await dialog(m, s, "[ГОЛОС по-русски] " + r["text"],
                            prefix=f"📝 <b>Я услышал (по-русски):</b> <i>{ai.esc(r['text'])}</i>\n\n",
                            no_score=True, extra_kb=wrong_kb)
    heard = r["text"]
    q = LAST_ES.get(s["id"], "")
    # Не «исправляем» расшифровку: иначе ошибки произношения исчезают
    unsure = r.get("unsure")
    note = " <i>(мог расслышать неточно)</i>" if unsure else ""
    await dialog(m, s, "[ГОЛОС] " + r["text"],
                 prefix=f"📝 <b>Я услышал (как есть):</b> <i>{ai.esc(r['text'])}</i>{note}\n\n",
                 no_score=bool(unsure), extra_kb=wrong_kb)


def voice_context(uid) -> str:
    """Подсказка для распознавания: что сейчас спросил учитель и какие слова она учит."""
    last = LAST_ES.get(uid, "")
    if not last:
        for h in reversed(db.history(uid, 6)):
            if h["role"] == "assistant":
                line = next((l for l in h["content"].splitlines() if l.strip().startswith("🇹🇷")), "")
                last = re.sub(r"\s*\([^)]*\)", "", line.replace("🇹🇷", "")).strip()
                break
    words = ", ".join(w.replace(" …", "") for w in known_words(uid)[-30:])
    return f"{last} Merhaba. Benim adım Ali. Teşekkür ederim. {words}".strip()


@dp.message(F.photo | F.video | F.video_note | F.document | F.animation)
async def on_work(m: Message):
    s = student_or_none(m.from_user.id)
    if not s:
        return
    kind = "photo" if m.photo else "video" if (m.video or m.video_note) else "file"
    db.add_work(s["id"], today(), kind, m.caption or "")
    db.mark(s["id"], today(), DONE)
    await m.copy_to(ADMIN_ID)
    await notify_admin(f"📎 Работа от {who(s)}")
    await m.answer("📎 Принято и отправлено преподавателю. День отмечен ✅")


URL_RE = re.compile(r"https?://\S+")


@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    s = student_or_none(m.from_user.id)
    if is_admin(m) and not s:
        return await m.answer("Чтобы попробовать бота как студент: /ya_student")
    if not s:
        return await m.answer(V("Нажмите /start, чтобы начать 🙂", "Нажми /start"))
    if m.text in BUTTONS:
        return await BUTTONS[m.text](m, s)
    if await spelling_answer(m, s):
        return
    links = URL_RE.findall(m.text)
    if links:
        for l in links:
            db.add_work(s["id"], today(), "link", l)
        db.mark(s["id"], today(), DONE)
        await notify_admin(f"🔗 Работа от {who(s)}:\n" + "\n".join(links))
        return await m.answer("🔗 Ссылку принял и отправил преподавателю ✅")
    await dialog(m, s, m.text)


async def b_today(m, s):
    await send_today(s["id"])


async def b_words(m, s):
    await send_words(s["id"])


async def b_listen(m, s):
    await send_listen(s["id"])


async def b_quiz(m, s):
    await start_quiz(s["id"], m.answer)


async def b_talk(m, s):
    await m.answer("🗣 Отвечайте учителю текстом или голосом — <b>нажмите и держите 🎤</b> справа внизу.")
    await dialog(m, s, "(Ученик нажал «Поговорить с учителем». Поздоровайся и дай ОДНО простое задание по уроку "
                       "дня (см. тему) или задай вопрос по-турецки о его жизни — такой, на который он ещё не "
                       "отвечал. Блок «Исправление» и оценку сейчас НЕ давай.)")


async def b_progress(m, s):
    await send(s["id"], summary_text(s, for_admin=False))


async def b_help(m, s):
    await send(s["id"], HELP_ES.format(m=MORNING, e=EVENING), reply_markup=menu_kb())


async def b_lesson(m, s):
    await send(s["id"], lessons.lesson_text(db.day_index(s["id"], today())))


async def b_games(m, s):
    await show_games(s["id"])


async def b_video(m, s):
    w = plan.week_of(db.day_index(s["id"], today()))
    await send(s["id"], plan.resources_text(), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"▶️ Видео этой недели: {plan.video_name(w)}", url=plan.video_url(w))]]))


BUTTONS = {BTN_TODAY: b_today, BTN_WORDS: b_words, BTN_LISTEN: b_listen, BTN_QUIZ: b_quiz,
           BTN_TALK: b_talk, BTN_PROGRESS: b_progress, BTN_HELP: b_help,
           BTN_LESSON: b_lesson, BTN_GAMES: b_games, BTN_VIDEO: b_video}


# ---------------- преподаватель ----------------

def summary_text(s, for_admin=True, days=7) -> str:
    end = today()
    st = db.stats(s["id"], end - timedelta(days=days - 1), end)
    (task, _), w = week_info(s["id"])
    if ES and not for_admin:
        vs = vocab.stats(s["id"])
        acc = f"{vs['accuracy']}%" if vs["accuracy"] is not None else "—"
        avg = f"{st['avg_score']:.1f} из 10" if st["avg_score"] else "—"
        mins = round(st["hours"] * 60)
        return (f"📈 <b>Ваши успехи — неделя {w}</b>\n\n"
                f"🔥 Занимаетесь {streak(s['id'])} дн. подряд\n"
                f"📅 За эту неделю: {st['active_days']} из 7 дней\n"
                f"📚 Слов изучено: {vs['seen']}, из них уже хорошо помните: {vs['learned']}\n"
                f"🎯 Правильных ответов в проверке слов: {acc}\n"
                f"🗣 Разговоров с учителем за неделю: {st['voices']}, средняя оценка: {avg}\n"
                f"🎬 Видео за неделю: {mins} мин\n\n"
                + ("Вы отлично занимаетесь! 👏" if st["active_days"] >= 5 else
                   "Каждый день по чуть-чуть — и турецкий пойдёт 🙂"))
    lines = [
        f"👤 <b>{who(s)}</b> — неделя {w}" if for_admin else f"📈 <b>Твой прогресс — неделя {w}</b>",
        f"Активных дней: <b>{st['active_days']}/{st['days']}</b>",
        f"Часы за 7 дн: {st['hours']:g} · всего: <b>{st['total_hours']:g} ч</b>",
    ]
    lines.append(f"🔥 Серия: {streak(s['id'])} дн. подряд")
    if ES:
        avg = f"{st['avg_score']:.1f}/10" if st["avg_score"] else "—"
        lines.append(f"🎙 Проверок речи/текста: {st['voices']} · средняя оценка: {avg}")
        vs = vocab.stats(s["id"])
        acc = f"{vs['accuracy']}%" if vs["accuracy"] is not None else "—"
        lines.append(f"📚 Слова: выдано {vs['seen']}, выучено (3+ повтора) {vs['learned']}, точность {acc}")
    else:
        lines.append(f"📎 Работ прислано: {len(st['works'])}")
    links = [x["content"] for x in st["works"] if x["kind"] == "link"]
    if for_admin:
        lines.append(f"Задание недели: {ai.esc(strip_tags(task))}")
        if links:
            lines.append("Ссылки: " + " ".join(links[-5:]))
        if ES:
            tips = [v["feedback"] for v in db.recent_voice(s["id"], 3) if v["feedback"]]
            if tips:
                lines.append("Слабые места: " + ai.esc(" | ".join(t[:120] for t in tips)))
    else:
        lines.append(f"Цель недели — {HOURS_GOAL:g} ч. " +
                     ("Отлично, держи темп 💪" if st["hours"] >= HOURS_GOAL
                      else f"Осталось {HOURS_GOAL - st['hours']:g} ч."))
    return "\n".join(lines)


@dp.message(Command("students"))
async def cmd_students(m: Message):
    if not is_admin(m):
        return
    ss = db.students()
    if not ss:
        return await m.answer("Студентов пока нет. Отправь ссылку из /start.")
    out = []
    for s in ss:
        di = db.day_index(s["id"], today())
        mark = "🟢" if db.has_any_report(s["id"], today()) else "⚪️"
        out.append(f"{mark} {who(s)} — ID <code>{s['id']}</code>, неделя {plan.week_of(di)}, день {di % 7 + 1}")
    await m.answer("\n".join(out) + "\n\n🟢 — сегодня уже отчитался")


@dp.message(Command("svodka"))
async def cmd_svodka(m: Message):
    if not is_admin(m):
        return
    await m.answer("\n\n".join(summary_text(s) for s in db.students()) or "Студентов нет.")


def students_kb(action: str) -> InlineKeyboardMarkup | None:
    ss = db.students()
    if not ss:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=f"{s['name'] or 'Без имени'}" + (f" (@{s['username']})" if s["username"] else ""),
        callback_data=f"adm:{action}:{s['id']}")] for s in ss])


async def do_obnovit(uid: int) -> str:
    db.clear_history(uid)
    await send(uid, "Merhaba! 👋\n\nВсё делается <b>кнопками внизу экрана</b>: урок, слова, игры, учитель.\n\n"
                    "Давайте познакомимся — <b>4 коротких вопроса</b>, отвечайте кнопками.")
    await ask_onboarding(uid, 0)
    return "✅ Отправил ученику новое меню и знакомство."


@dp.callback_query(F.data.startswith("adm:"))
async def cb_admin(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer()
    try:
        _, action, uid = c.data.split(":")
        uid = int(uid)
    except ValueError:
        return await c.answer()
    s = db.get_student(uid)
    if not s:
        return await c.answer("Ученик не найден", show_alert=True)
    if action == "obnovit" and ES:
        msg = await do_obnovit(uid)
    elif action == "remove":
        db.deactivate(uid)
        msg = f"✅ {ai.esc(s['name'])} отключён(а) от бота."
    elif action == "reset":
        db.set_start(uid, today())
        msg = f"✅ План для {ai.esc(s['name'])} начат заново с сегодняшнего дня."
    else:
        return await c.answer()
    await c.answer("Готово")
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await send(ADMIN_ID, msg)


def _id_arg(command: CommandObject):
    try:
        return int((command.args or "").split()[0])
    except Exception:
        return None


@dp.message(Command("reset"))
async def cmd_reset(m: Message, command: CommandObject):
    if not is_admin(m):
        return
    uid = _id_arg(command)
    if not uid:
        kb = students_kb("reset")
        return await m.answer("Кому начать план заново?" if kb else "Учеников пока нет.", reply_markup=kb)
    if not db.get_student(uid):
        return await m.answer("Ученик с таким ID не найден. Нажми /reset без номера — покажу список.")
    db.set_start(uid, today())
    await m.answer("План перезапущен с сегодняшнего дня.")


@dp.message(Command("obnovit"))
async def cmd_obnovit(m: Message, command: CommandObject):
    """Показать студенту новую версию: знакомство по шагам и кнопки меню."""
    if not is_admin(m) or not ES:
        return
    uid = _id_arg(command)
    if not uid:
        kb = students_kb("obnovit")
        return await m.answer("Кому показать новое меню и знакомство?" if kb else "Учеников пока нет.",
                              reply_markup=kb)
    if not student_or_none(uid):
        return await m.answer("Ученик с таким ID не найден. Нажми /obnovit без номера — покажу список.")
    await m.answer(await do_obnovit(uid))


@dp.message(Command("remove"))
async def cmd_remove(m: Message, command: CommandObject):
    if not is_admin(m):
        return
    uid = _id_arg(command)
    if not uid:
        kb = students_kb("remove")
        return await m.answer("Кого отключить от бота?" if kb else "Учеников пока нет.", reply_markup=kb)
    db.deactivate(uid)
    await m.answer("Студент отключён.")


@dp.message(Command("say"))
async def cmd_say(m: Message, command: CommandObject):
    if not is_admin(m):
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        return await m.answer("Пример: /say 123456789 Молодец, так держать!")
    await bot.send_message(int(parts[0]), "👨‍🏫 " + ai.esc(parts[1]))
    await m.answer("Отправлено.")


# ---------------- расписание ----------------

async def scheduler():
    """Срабатывает и после перезапуска/сна компьютера: если утреннее задание
    ещё не отправлено сегодня — отправит, как только бот снова запустится."""
    while True:
        try:
            now = datetime.now(TZ)
            hm, d = now.strftime("%H:%M"), now.date()

            if MORNING <= hm < EVENING:
                for s in db.students():
                    if not sent_once(f"morning:{s['id']}", d):
                        continue
                    try:
                        await send_today(s["id"])
                    except TelegramForbiddenError:
                        await notify_admin(f"🚫 {who(s)} заблокировал(а) бота")
                    except Exception as e:
                        log.warning("morning %s: %s", s["id"], e)
                    miss = db.missed_streak(s["id"], d, date.fromisoformat(s["start_date"]))
                    if miss >= 2:
                        await notify_admin(f"⚠️ {who(s)} не отчитывается {miss} дн. подряд")

            if EVENING <= hm < "23:30":
                for s in db.students():
                    if db.has_any_report(s["id"], d) and not (ES and vocab.due(s["id"], d, 1)):
                        continue
                    if not sent_once(f"evening:{s['id']}", d):
                        continue
                    parts = []
                    if ES:
                        parts.append("🌙 Добрый вечер! Осталось совсем немного на сегодня:")
                        if vocab.due(s["id"], d, 1):
                            parts.append("🧠 Нажмите «Проверить слова» — это 3 минуты.")
                        if not db.has_any_report(s["id"], d):
                            parts.append("🗣 И скажите учителю хотя бы одну фразу по-турецки 🙂")
                    elif not db.has_any_report(s["id"], d):
                        parts.append("⏰ Сегодня ещё нет отчёта. Отметь, что сделал(а), или пришли результат 📎")
                    try:
                        kb = (InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                            text="🧠 Проверить слова", callback_data="povtor")]]) if ES else report_kb())
                        await send(s["id"], "\n\n".join(parts), reply_markup=kb)
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
            await bot.send_message(msg.chat.id, "😕 Что-то пошло не так. Попробуй ещё раз.", parse_mode=None)
    except Exception:
        pass
    await alert_admin_throttled(f"⚠️ Ошибка в боте: {ai.esc(str(event.exception)[:300])}")
    return True


async def main():
    base = [BotCommand(command="segodnya", description="Задание на сегодня"),
            BotCommand(command="progress", description="Мой прогресс"),
            BotCommand(command="plan", description="План курса")]
    if ES:
        await bot.set_my_commands([BotCommand(command="start", description="Начать / меню"),
                                   BotCommand(command="igry", description="Игры"),
                                   BotCommand(command="urok_dnya", description="Урок дня"),
                                   BotCommand(command="help", description="Как пользоваться ботом")])
        asyncio.create_task(scheduler())
        log.info("Бот %s запущен. ИИ: %s, голос: %s, озвучка: %s", TITLE, ai.enabled(), ai.voice_enabled(),
                 tts.spanish_voice())
        return await dp.start_polling(bot)
    if ES:
        extra = [BotCommand(command="slova", description="10 слов на сегодня"),
                 BotCommand(command="povtor", description="Повторить слова (проверка)"),
                 BotCommand(command="urok", description="Урок по моей частой ошибке"),
                 BotCommand(command="test", description="Тест от репетитора"),
                 BotCommand(command="anki", description="Выгрузить мои слова для Anki")]
    else:
        extra = [BotCommand(command="moiplan", description="Личный план под мои цели"),
                 BotCommand(command="video", description="Раскадровка и промпты для ролика"),
                 BotCommand(command="razbor", description="Разбор моего промпта"),
                 BotCommand(command="kod", description="Вайбкодинг по шагам"),
                 BotCommand(command="test", description="Практический тест")]
    tail = [BotCommand(command="o_sebe", description="Рассказать о себе"),
            BotCommand(command="novyi", description="Начать разговор заново"),
            BotCommand(command="chasy", description="Записать часы"),
            BotCommand(command="help", description="Что умеет бот")]
    cmds = base + extra + tail
    await bot.set_my_commands(cmds)
    asyncio.create_task(scheduler())
    log.info("Бот %s запущен. ИИ: %s, голос: %s", TITLE, ai.enabled(), ai.voice_enabled())
    await dp.start_polling(bot)


async def self_check() -> bool:
    """python bot.py --check — проверяет токен, ID преподавателя и ключ ИИ до запуска."""
    ok = True
    print(f"Режим: {TITLE}")
    try:
        me = await bot.get_me()
        print(f"✅ Токен Telegram верный: @{me.username}")
    except Exception as e:
        ok = False
        print(f"❌ BOT_TOKEN не работает: {e}")
    try:
        await bot.send_message(ADMIN_ID, f"✅ Самопроверка: бот <b>{TITLE}</b> настроен и готов.")
        print("✅ ADMIN_ID верный (отправил тебе сообщение в Telegram)")
    except Exception as e:
        ok = False
        print(f"❌ Не могу написать ADMIN_ID={ADMIN_ID}: {e}\n   Сначала нажми /start в этом боте со своего аккаунта.")
    if ai.enabled():
        try:
            r = await ai.converse("Отвечай одним словом.", [{"role": "user", "content": "Скажи по-русски: работает"}],
                                  max_tokens=300)
            model = await ai.groq_model() if ai.GROQ_KEY else "—"
            print(f"✅ ИИ отвечает ({model}): {r.strip()[:40]}")
        except Exception as e:
            ok = False
            print(f"❌ ИИ не отвечает ({e}). Проверь GROQ_API_KEY.")
    else:
        print("⚠️ GROQ_API_KEY не указан — бот будет работать без ИИ.")
    if ES:
        print(f"✅ Словарь: {len(WORDS)} слов ({len(WORDS) // vocab.per_day} дней по {vocab.per_day})")
        v = tts.spanish_voice()
        print(f"✅ Озвучка слов: голос {v}" if v else "⚠️ Турецкий голос не найден — озвучка будет недоступна")
    await bot.session.close()
    print("\nИТОГ: " + ("всё готово ✅" if ok else "есть ошибки ❌ — исправь .env и запусти проверку снова"))
    return ok


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        sys.exit(0 if asyncio.run(self_check()) else 1)
    asyncio.run(main())
