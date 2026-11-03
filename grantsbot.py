"""Радар возможностей: сам ищет полностью оплачиваемые конференции, гранты, стипендии,
бизнес- и инвест-программы (лидерство, экономика, право, права человека, туризм, дипломатия)
и присылает их в Telegram. Запускается лаунчером main.py (строка bot_grants в bots.list).

Источники: RSS сайтов-агрегаторов + публичные Telegram-каналы (t.me/s/<канал>).
Команды: /start — подписаться, /now — проверить сейчас, /sources — источники, /stop — отписаться.
"""
import asyncio
import html
import logging
import os
import re
import sqlite3
from contextlib import closing

import aiohttp
import feedparser
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

# Настройки читаются один раз при загрузке (лаунчер меняет os.environ для каждого бота)
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
CHECK_EVERY_MIN = int(os.getenv("CHECK_EVERY_MIN", "120"))
DB_PATH = os.getenv("DB_PATH", "bot.db")
FIRST_RUN_LIMIT = 8  # при самом первом запуске прислать не больше стольких свежих находок

log = logging.getLogger("grants")

# ---------- Источники ----------
RSS_FEEDS = {
    "Opportunity Desk": "https://opportunitydesk.org/feed/",
    "Opportunities Corners": "https://opportunitiescorners.com/feed/",
    "Opportunities for Youth": "https://opportunitiesforyouth.org/feed/",
    "Opportunities Radar": "https://opportunitiesradar.com/feed/",
    "Greatyop": "https://greatyop.com/feed/",
    "FundsForNGOs": "https://www2.fundsforngos.org/feed/",
    "Funds for Individuals": "https://fundsforindividuals.fundsforngos.org/feed/",
    "Scholars4Dev": "https://www.scholars4dev.com/feed/",
    "TrueScho Opportunities": "https://fullopportunities.truescho.com/feed/",
}
TG_CHANNELS = [
    "we_project",   # Creative Asia — карьера, гранты, стипендии в Центральной Азии
    "joinyouthuz",  # Grants and Opportunities
]

# ---------- Фильтры ----------
FUNDED = [
    "fully funded", "fully-funded", "fully sponsored", "full scholarship", "all expenses",
    "expenses covered", "airfare", "travel grant", "travel support", "covers travel",
    "stipend", "funded trip", "полностью оплач", "полное финансирование", "оплачивают перел",
    "покрывает", "толық қаржыландыр",
]
TOPICS = {
    "лидерство": ["leadership", "leader", "лидер", "көшбасшы"],
    "экономика/бизнес": ["econom", "business", "entrepreneur", "startup", "invest", "finance",
                         "экономи", "бизнес", "инвест", "предприним", "стартап"],
    "право": ["law", "legal", "justice", "court", "lawyer", "право", "юрид", "юрист", "құқық"],
    "права человека": ["human rights", "права человека", "democracy", "демократ", "rule of law"],
    "туризм": ["tourism", "hospitality", "туризм"],
    "дипломатия": ["diplomac", "diplomat", "international relations", "дипломат", "united nations"],
}
FORMAT = ["conference", "summit", "forum", "fellowship", "grant", "scholarship", "exchange",
          "program", "school", "конференц", "саммит", "форум", "грант", "стипенд", "обмен", "стажир"]
FEE_WORDS = ["application fee", "registration fee", "pay the fee", "взнос", "оплатить заявку"]


def classify(text: str):
    t = " " + text.lower() + " "
    if not any(w in t for w in FUNDED):
        return None
    topics = [name for name, words in TOPICS.items() if any(w in t for w in words)]
    if not topics or not any(w in t for w in FORMAT):
        return None
    return topics, any(w in t for w in FEE_WORDS)


# ---------- База ----------
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE IF NOT EXISTS subs (chat_id INTEGER PRIMARY KEY)")
    return conn


def _seen_count() -> int:
    with closing(_db()) as c:
        return c.execute("SELECT COUNT(*) FROM seen").fetchone()[0]


def _mark_new(item_id: str) -> bool:
    with closing(_db()) as c, c:
        return c.execute("INSERT OR IGNORE INTO seen(id) VALUES (?)", (item_id,)).rowcount == 1


def _subscribers() -> set:
    with closing(_db()) as c:
        ids = {r[0] for r in c.execute("SELECT chat_id FROM subs")}
    ids.add(ADMIN_ID)
    return ids


# ---------- Сбор ----------
HEADERS = {"User-Agent": "Mozilla/5.0 (GrantsRadarBot)"}


async def _fetch(s, url):
    try:
        async with s.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status == 200:
                return await r.text()
            log.warning("%s -> HTTP %s", url, r.status)
    except Exception as e:
        log.warning("%s -> %s", url, e)
    return None


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


async def collect():
    """Новые подходящие находки (каждая помечается как увиденная)."""
    found = []
    async with aiohttp.ClientSession() as s:
        for name, url in RSS_FEEDS.items():
            raw = await _fetch(s, url)
            if raw:
                for e in feedparser.parse(raw).entries[:30]:
                    title, link = e.get("title", ""), e.get("link", "")
                    found.append((link or title, name, title, link,
                                  f"{title} {_strip(e.get('summary', ''))}"))
        for ch in TG_CHANNELS:
            raw = await _fetch(s, f"https://t.me/s/{ch}")
            if raw:
                posts = re.findall(r'data-post="([^"]+)".*?<div class="tgme_widget_message_text[^>]*>(.*?)</div>',
                                   raw, flags=re.S)
                for post_id, body in posts[-20:]:
                    text = _strip(body)
                    found.append((post_id, f"@{ch}", text[:140], f"https://t.me/{post_id}", text))
    fresh = []
    for item_id, src, title, link, text in found:
        res = classify(text)
        if res and _mark_new(item_id):
            fresh.append((src, title, link, *res))
    return fresh


def _fmt(src, title, link, topics, fee):
    msg = f"🌍 <b>{html.escape(title)}</b>\n🏷 {', '.join(topics)}\n📰 {html.escape(src)}\n{link}"
    if fee:
        msg += "\n⚠️ Похоже, за подачу берут взнос — проверьте организатора."
    return msg


# ---------- Бот ----------
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
_lock = asyncio.Lock()


async def _send(chats, items):
    for chat in chats:
        for it in items:
            try:
                await bot.send_message(chat, _fmt(*it))
                await asyncio.sleep(0.5)
            except Exception as e:
                log.warning("send to %s failed: %s", chat, e)


async def check(manual_chat=None):
    async with _lock:
        first = _seen_count() == 0
        items = await collect()
        if first:
            items = items[:FIRST_RUN_LIMIT]  # остальное старое — просто запомнили
        if manual_chat and not items:
            await bot.send_message(manual_chat, "Новых подходящих возможностей пока нет. Проверяю каждые "
                                                f"{CHECK_EVERY_MIN} мин.")
        await _send([manual_chat] if manual_chat else _subscribers(), items)


@dp.message(Command("start"))
async def cmd_start(m: Message):
    with closing(_db()) as c, c:
        c.execute("INSERT OR IGNORE INTO subs(chat_id) VALUES (?)", (m.chat.id,))
    await m.answer("Подписка включена ✅\nБуду присылать полностью оплачиваемые конференции, гранты, "
                   "стипендии и бизнес-программы: лидерство, экономика, право, права человека, туризм, "
                   f"дипломатия. Проверяю каждые {CHECK_EVERY_MIN} мин.\n\n"
                   "/now — проверить сейчас\n/sources — источники\n/stop — отписаться")


@dp.message(Command("stop"))
async def cmd_stop(m: Message):
    with closing(_db()) as c, c:
        c.execute("DELETE FROM subs WHERE chat_id=?", (m.chat.id,))
    await m.answer("Отписал. /start — включить снова.")


@dp.message(Command("sources"))
async def cmd_sources(m: Message):
    lines = [f"• {n}" for n in RSS_FEEDS] + [f"• @{c}" for c in TG_CHANNELS]
    await m.answer("Источники:\n" + "\n".join(lines))


@dp.message(Command("now"))
async def cmd_now(m: Message):
    await m.answer("Проверяю…")
    await check(manual_chat=m.chat.id)


async def _scheduler():
    await asyncio.sleep(30)
    while True:
        try:
            await check()
        except Exception:
            log.exception("проверка упала")
        await asyncio.sleep(CHECK_EVERY_MIN * 60)


_sched_task = None


async def main():
    global _sched_task
    if _sched_task is None or _sched_task.done():  # лаунчер может перезапускать main()
        _sched_task = asyncio.create_task(_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
