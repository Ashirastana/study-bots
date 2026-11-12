"""Радар возможностей: сам ищет полностью оплачиваемые короткие программы (семинары, конференции,
учебные визиты, повышение квалификации) для практикующего юриста/руководителя 50+ из Казахстана
и присылает их в Telegram. Запускается лаунчером main.py (строка bot_grants в bots.list).

Как работает поиск (каждые CHECK_EVERY_MIN минут):
1. Сбор: RSS сайтов-агрегаторов (полный текст записи), Google News (EN/RU/KZ), публичные Telegram-каналы.
   Записи старше MAX_AGE_DAYS дней не берём.
2. Предфильтр без ИИ: сразу отсекаем университетское (степени, студенты, летние школы), молодёжное,
   возрастной потолок < 50, экзамены. Остальное получает баллы (оплата, формат, тема) — лучшие идут дальше.
3. Для кандидатов бот открывает страницу программы и ищет ссылку на официальный сайт.
4. ИИ (Groq) читает текст, ставит оценку 0–10, извлекает дедлайн, место, даты, что оплачивают.
   Присылаем только ok и оценку ≥ MIN_SCORE, с непрошедшим дедлайном. Если ИИ не ответил — находка
   НЕ теряется, её проверят в следующий раз.
5. Анти-дубли: одна и та же программа с разных сайтов (даже с другим заголовком) — одно сообщение.

Команды: /start, /now, /list [слово], /sources, /stop.
ИИ: кнопка «📄 Документы и требования» под уведомлением; reply на уведомление с вопросом;
ссылка на любую программу; или просто вопрос текстом (поиск в интернете + база найденного).
"""
import asyncio
import base64
import html
import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, unquote, urlparse

import aiohttp
import feedparser
from aiogram import Bot, Dispatcher
from aiogram import F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

try:
    from ai import GROQ_KEY, groq_chat  # тот же бесплатный Groq, что у учебных ботов
except Exception:  # без модуля ai бот всё равно работает, только без ИИ
    GROQ_KEY, groq_chat = "", None

# Настройки читаются один раз при загрузке (лаунчер меняет os.environ для каждого бота)
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
CHECK_EVERY_MIN = int(os.getenv("CHECK_EVERY_MIN", "10"))
DB_PATH = os.getenv("DB_PATH", "bot.db")
MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "21"))     # записи старше — не смотрим
MIN_SCORE = int(os.getenv("MIN_SCORE", "6"))            # минимальная оценка ИИ (0–10) для отправки
AI_PER_ROUND = int(os.getenv("AI_PER_ROUND", "20"))     # сколько кандидатов ИИ проверяет за один проход
AI_BATCH = 5
FIRST_RUN_LIMIT = 8  # при самом первом запуске прислать не больше стольких находок

log = logging.getLogger("grants")


def today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=5)).date()  # Казахстан, UTC+5


# ---------- Источники ----------
# Только агрегаторы, где много конференций/семинаров/обменов. Чисто «стипендии на учёбу» убраны.
RSS_FEEDS = {
    "Opportunity Desk": "https://opportunitydesk.org/feed/",
    "Opportunity Desk: конференции": "https://opportunitydesk.org/category/conferences/feed/",
    "Opportunities Corners": "https://opportunitiescorners.com/feed/",
    "Opportunities for Youth": "https://opportunitiesforyouth.org/feed/",
    "Opportunities Radar": "https://opportunitiesradar.com/feed/",
    "Opportunities for Africans": "https://www.opportunitiesforafricans.com/feed/",
    "Opportunity Portal": "https://opportunityportal.info/feed/",
    "Greatyop": "https://greatyop.com/feed/",
    "FundsForNGOs": "https://www2.fundsforngos.org/feed/",
    "Funds for Individuals": "https://fundsforindividuals.fundsforngos.org/feed/",
    "TrueScho Opportunities": "https://fullopportunities.truescho.com/feed/",
    "Opportunities Circle": "https://www.opportunitiescircle.com/feed/",
    "ProFellow (fellowships)": "https://www.profellow.com/feed/",
}


def _gn(q, lang="en", days=3):
    """Лента Google News по запросу. days=0 — без ограничения по дате."""
    loc = {"en": "hl=en-US&gl=US&ceid=US:en", "ru": "hl=ru&gl=KZ&ceid=KZ:ru", "kk": "hl=kk&gl=KZ&ceid=KZ:kk"}[lang]
    q = f"{q} when:{days}d" if days else q
    return f"https://news.google.com/rss/search?q={quote(q)}&{loc}"


NEWS_FEEDS = {
    "Google News: lawyers conference": _gn('"fully funded" conference OR seminar lawyers OR judges OR "legal professionals"'),
    "Google News: study visit": _gn('"study visit" OR "study tour" "travel and accommodation" professionals'),
    "Google News: leadership program": _gn('"fully funded" leadership program executives OR "senior professionals"'),
    "Google News: rule of law training": _gn('"rule of law" OR "human rights" training "travel and accommodation covered"'),
    "Google News: anti-corruption": _gn('anti-corruption OR integrity seminar "fully funded" professionals'),
    "Google News: IVLP / exchange": _gn('IVLP OR "Open World" OR "professional exchange" lawyers OR "civil society leaders"'),
    "Google News: OSCE / UN / CoE": _gn('OSCE OR UNDP OR "Council of Europe" seminar Kazakhstan OR "Central Asia" lawyers'),
    "Google News: arbitration mediation": _gn('arbitration OR mediation conference "travel grant" OR "fully funded"'),
    "Google News RU: юристы": _gn('семинар OR стажировка для юристов перелет и проживание оплачиваются', "ru"),
    "Google News RU: руководители": _gn('программа для руководителей обмен опытом перелет проживание за счет', "ru"),
    "Google News RU: Казахстан": _gn('Казахстан стажировка за рубежом юристы OR судьи OR адвокаты', "ru"),
    "Google News KZ": _gn('заңгерлер семинар шетелде тегін', "kk"),
    "Google News: cultural exchange": _gn('"cultural exchange" program professionals OR leaders "fully funded"'),
    "Google News: christian leaders": _gn('christian OR church OR pastors leadership conference "travel and accommodation" OR "fully funded"'),
    "Google News: family & fatherhood": _gn('family OR fatherhood OR parenting conference OR seminar "fully funded" OR "travel covered"'),
    "Google News: language course": _gn('"language course" OR "language program" professionals "fully funded" short-term'),
    "Google News RU: христиане": _gn('христианская конференция OR семинар для служителей пасторов перелет проживание', "ru"),
    "Google News: volunteering": _gn('volunteer program OR volunteering short-term "travel and accommodation" OR "fully funded" adults'),
    "Google News RU: семья": _gn('семейные ценности OR отцовство OR воспитание детей форум OR семинар перелет проживание', "ru"),
}

# Публичные Telegram-каналы (читаются через t.me/s/<канал>, без входа). Каналы только про учёбу за рубежом убраны.
TG_CHANNELS = [
    "we_project",               # Creative Asia — Центральная Азия
    "joinyouthuz",              # Grants and Opportunities
    "opportunityportalabroad",
    # несуществующий канал просто пропускается — добавляйте любые: имя без @
]

# ---------- Профиль и фильтры ----------
PROFILE = ("практикующий юрист, руководитель и общественный лидер из Казахстана, возраст 50+, НЕ студент, "
           "без сертификатов IELTS/TOEFL и без возможности сдавать экзамены")

TOPICS = {
    "лидерство и управление": ["leadership", "leader", "management", "executive", "governance", "public administration",
                               "лидер", "руковод", "управлен", "көшбасшы"],
    "право и юристы": ["law ", "laws", "legal", "justice", "court", "lawyer", "judge", "judicia", "attorney", "advocate",
                       "bar association", "arbitration", "mediation", "право", "правов", "юрид", "юрист", "адвокат",
                       "судь", "судеб", "арбитраж", "медиац", "құқық", "заңгер"],
    "права человека": ["human rights", "права человека", "rule of law", "democracy", "демократ", "верховенств"],
    "антикоррупция": ["anti-corruption", "anticorruption", "integrity", "коррупц"],
    "экономика и бизнес": ["econom", "business", "entrepreneur", "invest", "finance", "trade",
                           "экономи", "бизнес", "инвест", "предприним", "торгов"],
    "госуправление и дипломатия": ["diplomac", "diplomat", "international relations", "public policy",
                                   "civil serv", "дипломат", "госслуж", "государственн"],
    "туризм": ["tourism", "hospitality", "туризм"],
    "культурные обмены": ["cultural exchange", "intercultural", "cross-cultural", "culture", "культурн",
                          "межкультур", "мәдени"],
    "христианство (протестанты)": ["christian", "church", "protestant", "evangelical", "pastor", "bible", "biblical",
                                   "theolog", "faith-based", "faith leaders", "religious freedom", "interfaith",
                                   "spiritual", "христиан", "церк", "протестант", "евангел", "пастор", "библ",
                                   "богослов", "духовн", "служител"],
    "семейные ценности": ["family values", "family", "marriage", "семейн", "семья", "семьи", "брак", "отбасы"],
    "воспитание детей": ["parenting", "parents", "upbringing", "воспитан", "родител", "бала тәрбие"],
    "отцовство и роль мужа": ["fatherhood", "fathers", "husband", "men's role", "role of men", "отцовств",
                              "отец", "отцы", "отцов", "мужчин", "әке"],
    "языки": ["language course", "language program", "linguistic", "translation", "языков", "лингв", "перевод", "тіл"],
    "волонтёрство": ["volunteer", "volunteering", "волонтер", "волонтёр", "доброволь", "ерікті"],
}
FORMAT = ["conference", "summit", "forum", "seminar", "workshop", "training", "study visit", "study tour",
          "exchange program", "exchange programme", "professional development", "capacity building",
          "short course", "short-term", "visiting program", "ivlp", "open world", "internship", "fellowship",
          "symposium", "congress", "convention", "retreat", "masterclass", "bootcamp", "delegation",
          "конференц", "семинар", "форум", "тренинг", "стажировк", "повышени", "квалификац",
          "обмен опытом", "саммит", "симпозиум", "конгресс", "мастер-класс", "language course", "language program",
          "cultural exchange", "курс", "культурный обмен", "volunteer program", "volunteering", "волонтер", "волонтёр"]
FUNDED = [
    "fully funded", "fully-funded", "fully sponsored", "fully covered", "all expenses", "all-expenses",
    "expenses covered", "expenses paid", "costs covered", "costs will be covered", "all costs",
    "travel and accommodation", "accommodation and travel", "travel, accommodation", "airfare", "air ticket",
    "flight ticket", "flights and accommodation", "round-trip", "return flight", "travel costs", "travel grant",
    "travel support", "travel bursar", "covers travel", "free accommodation", "accommodation will be provided",
    "per diem", "stipend", "funded opportunity", "funded program", "funded conference",
    "полностью оплач", "полное финансирование", "оплачивают перел", "перелет и проживание", "перелёт и проживание",
    "проезд и проживание", "покрываются расходы", "расходы покрыва", "за счет организатор", "за счёт организатор",
    "за счет принимающ", "за счёт принимающ", "толық қаржыландыр",
]
# Университетское и студенческое — не интересует совсем
DEGREE = ["bachelor", "master's", "masters degree", "masters program", "master degree", "master program",
          "master programme", "masters programme", "mba", "ll.m", "llm ", "phd", "ph.d", "doctoral", "doctorate",
          "postdoc", "post-doc", "undergraduate", "postgraduate", "graduate program", "graduate programme",
          "graduate school", "graduate students", "degree program", "degree programme", "tuition", "study in ",
          "study abroad", "scholarships for international students", "university scholarship", "summer school",
          "winter school", "student exchange", "erasmus mundus", "high school", "secondary school",
          "university admission", "for students", "students only", "university students",
          "магистр", "бакалавр", "докторант", "аспирант", "поступлени", "летняя школа", "зимняя школа",
          "школьник", "обучение в университете", "стипендия на обучение", "грант на обучение"]
YOUNG = ["youth", "young", "student", "teen", "молодеж", "молодёж", "студент", "жастар"]
EXAMS = ["ielts", "toefl", "duolingo english", "gre ", "gmat", "pte academic", "cambridge english"]
EXAM_OK = ["no ielts", "ielts not required", "ielts is not required", "without ielts", "no toefl",
           "toefl not required", "без ielts", "без toefl", "no english test", "no language test"]
FEE_WORDS = ["application fee", "registration fee", "participation fee", "pay the fee", "self-funded",
             "partially funded", "partial funding", "partially-funded", "взнос", "оплатить заявку",
             "за свой счет", "за свой счёт", "частично оплач"]
ONLINE = ["online", "virtual", "webinar", "онлайн", "вебинар"]


def _rx(words):
    """Совпадение по началу слова (работает с русскими основами: «юрист» → «юристов»)."""
    return re.compile(r"(?<![a-zа-яёәіңғүұқөһ0-9])(?:" + "|".join(re.escape(w) for w in words) + ")", re.I)


FORMAT_RE, FUNDED_RE, DEGREE_RE, YOUNG_RE = _rx(FORMAT), _rx(FUNDED), _rx(DEGREE), _rx(YOUNG)
EXAMS_RE, EXAM_OK_RE, FEE_RE, ONLINE_RE = _rx(EXAMS), _rx(EXAM_OK), _rx(FEE_WORDS), _rx(ONLINE)
TOPIC_RES = {name: _rx(words) for name, words in TOPICS.items()}
AGE_CAP = re.compile(r"(?:aged?|age(?:d)?\s+between|under|below|not older than|up to|до)\s*(?:the age of\s*)?"
                     r"(?:1[5-9]\s*(?:-|–|to|and)\s*)?([1-4]\d)\s*(?:years|лет|y\.?o)?", re.I)
AGE_RANGE = re.compile(r"(?<!\d)(1[6-9]|[2-3]\d)\s*(?:-|–|—|to|and|до)\s*([2-4]\d)\s*(?:years|year-old|yrs|лет|y\.?o)",
                       re.I)
NO_FEE_RE = re.compile(r"no (?:application |registration |participation )?fees?|free of charge|without (?:any )?fee|"
                       r"без (?:взноса|оплаты)|бесплатн|free to (?:apply|attend|participate)", re.I)


def _topics(t: str) -> list:
    return [name for name, rx in TOPIC_RES.items() if rx.search(t)]


def _age_capped(t: str) -> bool:
    for m in AGE_CAP.finditer(t):
        if int(m.group(1)) < 50 and re.search(r"age|aged|лет|years|older", m.group(0), re.I):
            return True
    return any(int(m.group(2)) < 50 for m in AGE_RANGE.finditer(t))


def prescore(title: str, text: str, news: bool = False):
    """Быстрая оценка без ИИ. None — точно не подходит; иначе dict(score, topics, fee, funded)."""
    tl = " " + title.lower() + " "
    t = " " + f"{title} {text}".lower() + " "
    if DEGREE_RE.search(tl) or YOUNG_RE.search(tl):
        return None                                   # университетское / молодёжное прямо в заголовке
    if re.search(r"partially[ -]funded|частично", tl) or ONLINE_RE.search(tl):
        return None
    if EXAMS_RE.search(t) and not EXAM_OK_RE.search(t):
        return None
    if _age_capped(t):
        return None
    fmt_title, fmt_any = bool(FORMAT_RE.search(tl)), bool(FORMAT_RE.search(t))
    if not fmt_any:
        return None
    funded = bool(FUNDED_RE.search(t)) or (news and "fully" in t)
    if news and not funded:
        return None                                   # новости без слов про оплату — почти всегда мусор
    topics = _topics(t)
    fee = bool(FEE_RE.search(NO_FEE_RE.sub(" ", t)))
    score = (3 if funded else 0) + (3 if fmt_title else 1) + min(len(topics), 3)
    if DEGREE_RE.search(t):
        score -= 2                                    # упоминание студентов/степеней в тексте — минус, но не отказ
    if YOUNG_RE.search(t):
        score -= 1
    if fee:
        score -= 2
    if score < 4:
        return None
    return {"score": score, "topics": topics, "fee": fee, "funded": funded}


# ---------- Даты и дедлайны ----------
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
          "nov": 11, "dec": 12, "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7,
          "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}


def parse_date(s: str):
    """'15 December 2026', 'December 15, 2026', '15.12.2026', '2026-12-15', '15 декабря 2026' → date."""
    if not s:
        return None
    s = s.lower()
    try:
        m = re.search(r"(20\d\d)-(\d{1,2})-(\d{1,2})", s)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = re.search(r"(\d{1,2})[./](\d{1,2})[./](20\d\d)", s)
        if m:
            return date(int(m[3]), int(m[2]), int(m[1]))
        m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-zа-яё]{3,})\.?,?\s+(20\d\d)", s)
        if m and m[2][:3] in MONTHS:
            return date(int(m[3]), MONTHS[m[2][:3]], int(m[1]))
        m = re.search(r"([a-z]{3,})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)", s)
        if m and m[1][:3] in MONTHS:
            return date(int(m[3]), MONTHS[m[1][:3]], int(m[2]))
    except ValueError:
        return None
    return None


DEADLINE_RE = re.compile(r"(?:application deadline|submission deadline|deadline|apply by|apply before|closing date|"
                         r"applications? (?:close|closes|are due|due)(?: on)?|дедлайн|срок подачи|"
                         r"при[её]м заявок до|заявки принимаются до)\s*(?:is|:|-|–)?\s*([^\n;|]{4,40})", re.I)


def find_deadline(text: str):
    """Первый распознанный дедлайн в тексте → date или None."""
    for m in DEADLINE_RE.finditer(text or ""):
        d = parse_date(m.group(1))
        if d:
            return d
    return None


def fmt_deadline(iso: str) -> str:
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    left = (d - today()).days
    tail = "сегодня!" if left == 0 else (f"осталось {left} дн." if left > 0 else "прошёл")
    return f"{d:%d.%m.%Y} ({tail})"


# ---------- Анти-дубли ----------
_GENERIC = set("fully funded apply now call for applications application open the of and in to a an for on at with by "
               "2025 2026 2027 2028 opportunity opportunities deadline program programme international global "
               "полностью оплачиваемая программа на для и в по".split())


def _title_key(title: str) -> str:
    return "t:" + re.sub(r"[^a-zа-яё0-9]+", "", title.lower())[:60]


def _words(title: str) -> set:
    return {w for w in re.findall(r"[a-zа-яё0-9]{3,}", title.lower()) if w not in _GENERIC}


def similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    if len(wa) < 2 or len(wb) < 2:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.6


# ---------- База ----------
ITEM_COLS = {"title": "TEXT", "link": "TEXT", "text": "TEXT", "added": "TEXT", "deadline": "TEXT",
             "score": "INTEGER", "official": "TEXT", "note": "TEXT", "src": "TEXT", "topics": "TEXT"}


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE IF NOT EXISTS subs (chat_id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "title TEXT, link TEXT, text TEXT)")
    have = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    for col, typ in ITEM_COLS.items():  # миграция старой базы: новые колонки
        if col not in have:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")
    conn.execute("CREATE TABLE IF NOT EXISTS sent (chat_id INTEGER, msg_id INTEGER, item_id INTEGER, "
                 "PRIMARY KEY (chat_id, msg_id))")
    return conn


def _save_item(it: dict) -> int:
    with closing(_db()) as c, c:
        return c.execute(
            "INSERT INTO items(title, link, text, added, deadline, score, official, note, src, topics) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (it["title"], it["link"], it["text"][:4000], today().isoformat(), it.get("deadline", ""),
             it.get("score", 0), it.get("official", ""), it.get("note", ""), it["src"],
             ", ".join(it.get("topics", [])))).lastrowid


def _get_item(item_id):
    with closing(_db()) as c:
        r = c.execute("SELECT title, link, text, official FROM items WHERE id=?", (item_id,)).fetchone()
    return dict(zip(("title", "link", "text", "official"), r)) if r else None


def _item_by_msg(chat_id, msg_id):
    with closing(_db()) as c:
        r = c.execute("SELECT item_id FROM sent WHERE chat_id=? AND msg_id=?", (chat_id, msg_id)).fetchone()
    return r[0] if r else None


def _recent_titles(days=60) -> list:
    since = (today() - timedelta(days=days)).isoformat()
    with closing(_db()) as c:
        return [r[0] for r in c.execute("SELECT title FROM items WHERE added IS NULL OR added >= ? "
                                        "ORDER BY id DESC LIMIT 1500", (since,))]


def _seen_count() -> int:
    with closing(_db()) as c:
        return c.execute("SELECT COUNT(*) FROM seen").fetchone()[0]


def _is_seen(*keys) -> bool:
    with closing(_db()) as c:
        return any(c.execute("SELECT 1 FROM seen WHERE id=?", (k,)).fetchone() for k in keys)


def _mark_seen(*keys):
    with closing(_db()) as c, c:
        c.executemany("INSERT OR IGNORE INTO seen(id) VALUES (?)", [(k,) for k in keys])


def _subscribers() -> set:
    with closing(_db()) as c:
        ids = {r[0] for r in c.execute("SELECT chat_id FROM subs")}
    ids.add(ADMIN_ID)
    return ids


# ---------- Загрузка страниц ----------
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36 GrantsRadarBot",
           "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"}


async def _fetch(s, url, timeout=25):
    try:
        async with s.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status == 200:
                return await r.text(errors="ignore")
            log.warning("%s -> HTTP %s", url[:120], r.status)
    except Exception as e:
        log.warning("%s -> %s", url[:120], e)
    return None


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


PAGE_RE_DROP = re.compile(r"<(script|style|noscript|svg|header|footer|nav|aside|form)[^>]*>.*?</\1>|<!--.*?-->",
                          re.S | re.I)
MAIN_RE = re.compile(r"<article\b.*?</article>|<div[^>]+class=\"[^\"]*(?:entry-content|post-content|"
                     r"article-content|td-post-content)[^\"]*\".*", re.S | re.I)
OFFICIAL_RE = re.compile(r"official|apply|application|register|registration|website|learn more|more info|"
                         r"click here|details|подать|заявк|официальн|регистрац|подробн", re.I)
SKIP_DOMAINS = ("facebook.", "twitter.", "x.com", "whatsapp.", "t.me", "telegram.", "linkedin.", "pinterest.",
                "instagram.", "youtube.", "tiktok.", "reddit.", "wa.me", "google.", "bit.ly", "feedburner.",
                "wordpress.", "gravatar.", "amazon.", "play.google", "apple.com")


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def find_official(raw_html: str, page_url: str = "") -> str:
    """Ссылка на официальную страницу программы внутри статьи агрегатора ('Apply here', 'Official website')."""
    own = _domain(page_url) if page_url else ""
    for href, inner in re.findall(r'<a\s[^>]*href=["\'](https?://[^"\']+)["\'][^>]*>(.*?)</a>', raw_html or "",
                                  re.S | re.I):
        d = _domain(href)
        if not d or d == own or any(x in d for x in SKIP_DOMAINS) or "share" in href.lower():
            continue
        if OFFICIAL_RE.search(_strip(inner)):
            return html.unescape(href)
    return ""


def page_main(raw: str) -> str:
    raw = PAGE_RE_DROP.sub(" ", raw or "")
    m = MAIN_RE.search(raw)
    return _strip(m.group(0) if m else raw)


KEY_RE = re.compile(r"deadline|eligib|requirement|cover|fund|accommodation|travel|flight|airfare|fee|age|years old|"
                    r"ielts|toefl|english|who can apply|benefit|location|venue|dates?|duration|"
                    r"дедлайн|срок|требован|оплач|проживан|перел|взнос|возраст|кто может|место|даты", re.I)


def focus(text: str, head=1200, extra=1500) -> str:
    """Начало текста + предложения про условия/оплату/дедлайн — чтобы ИИ видел главное и не тратил токены."""
    text = (text or "").strip()
    if len(text) <= head + extra:
        return text
    picked, total = [], 0
    for s in re.split(r"(?<=[.!?])\s+", text[head:]):
        if KEY_RE.search(s):
            picked.append(s[:300])
            total += len(picked[-1])
            if total >= extra:
                break
    return text[:head] + (" … " + " ".join(picked) if picked else "")


async def page_text(link: str, s=None) -> tuple:
    """(основной текст страницы, ссылка на официальный сайт)."""
    if not link or not link.startswith("http") or "news.google.com" in link:
        return "", ""
    if s is None:
        async with aiohttp.ClientSession() as s2:
            raw = await _fetch(s2, link)
    else:
        raw = await _fetch(s, link)
    if not raw:
        return "", ""
    return page_main(raw)[:15000], find_official(raw, link)


# ---------- Сбор ----------
def _entry_date(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    try:
        return date(*t[:3]) if t else None
    except Exception:
        return None


async def _rss(s, sem, name, url, news):
    async with sem:
        raw = await _fetch(s, url)
    out = []
    if not raw:
        return out
    oldest = today() - timedelta(days=MAX_AGE_DAYS)
    for e in feedparser.parse(raw).entries[:50]:
        d = _entry_date(e)
        if d and d < oldest:
            continue
        title, link = _strip(e.get("title", "")), e.get("link", "")
        content_html = (e.get("content") or [{}])[0].get("value", "") if e.get("content") else ""
        body = _strip(content_html) or _strip(e.get("summary", ""))
        out.append({"id": link or title, "src": name, "title": title, "link": link, "text": f"{title}. {body}",
                    "news": news, "full": len(body) > 1500,
                    "official": find_official(content_html, link) if content_html else ""})
    return out


async def _tg(s, sem, ch):
    async with sem:
        raw = await _fetch(s, f"https://t.me/s/{ch}")
    out = []
    if raw:
        posts = re.findall(r'data-post="([^"]+)".*?<div class="tgme_widget_message_text[^>]*>(.*?)</div>',
                           raw, flags=re.S)
        for post_id, body in posts[-20:]:
            text = _strip(body)
            links = [h for h in re.findall(r'href="(https?://[^"]+)"', body) if "t.me" not in _domain(h)]
            first_line = re.split(r"[\n.!]", text, 1)[0]
            out.append({"id": post_id, "src": f"@{ch}", "title": (first_line or text)[:140],
                        "link": f"https://t.me/{post_id}", "text": text, "news": False, "full": True,
                        "official": html.unescape(links[0]) if links else ""})
    return out


async def collect(stats: dict):
    """Новые записи со всех источников → кандидаты для ИИ (лучшие сначала). Явно неподходящие помечаются
    как просмотренные сразу, кандидаты — только после решения ИИ."""
    sem = asyncio.Semaphore(8)
    async with aiohttp.ClientSession() as s:
        jobs = [_rss(s, sem, n, u, False) for n, u in RSS_FEEDS.items()]
        jobs += [_rss(s, sem, n, u, True) for n, u in NEWS_FEEDS.items()]
        jobs += [_tg(s, sem, ch) for ch in TG_CHANNELS]
        results = await asyncio.gather(*jobs, return_exceptions=True)
    cands, round_keys = [], set()
    for res in results:
        if isinstance(res, Exception):
            log.warning("источник упал: %s", res)
            continue
        stats["sources_ok"] = stats.get("sources_ok", 0) + (1 if res else 0)
        for it in res:
            tkey = _title_key(it["title"])
            if not it["title"] or tkey in round_keys or it["id"] in round_keys or _is_seen(it["id"], tkey):
                continue
            round_keys.update((it["id"], tkey))
            stats["new"] = stats.get("new", 0) + 1
            pre = prescore(it["title"], it["text"], it["news"])
            if not pre:
                _mark_seen(it["id"], tkey)
                continue
            it.update(pre)
            cands.append(it)
    cands.sort(key=lambda x: -x["score"])
    stats["candidates"] = len(cands)
    return cands


async def enrich(items):
    """Открывает страницу каждого кандидата (если в ленте был только анонс) и ищет официальный сайт."""
    sem = asyncio.Semaphore(5)

    async def one(s, it):
        if it.get("full") and it.get("official"):
            return
        async with sem:
            text, official = await page_text(it["link"], s)
        if text and len(text) > len(it["text"]):
            it["text"] = f"{it['title']}. {text}"
        it["official"] = it.get("official") or official

    async with aiohttp.ClientSession() as s:
        await asyncio.gather(*(one(s, it) for it in items), return_exceptions=True)
    return items


# ---------- ИИ-отбор ----------
AI_FILTER = """Сегодня {today}. Ты строго отбираешь возможности для одного человека: {profile}.
Его темы: {topics}.
Подходит (ok=true) ТОЛЬКО если ВСЁ выполнено:
1) это реальная программа с открытым приёмом заявок (не новость о прошедшем событии, не реклама, не список-подборка);
2) формат: семинар, конференция, форум, тренинг, повышение квалификации, учебный визит или стажировка НЕ ДОЛЬШЕ 1 месяца,
   очно (не онлайн). Любое университетское обучение (бакалавриат, магистратура, PhD, MBA/LLM, летние/зимние школы,
   стипендии на учёбу, студенческие обмены) — всегда НЕ подходит. Длинные fellowship/стажировки > 1 месяца — НЕ подходит;
3) для специалистов/руководителей, НЕ для студентов и НЕ только для молодёжи; нет возрастного ограничения ниже 50 лет;
4) бесплатно для участника (нет платы за участие/регистрацию/подачу);
5) организаторы оплачивают ПЕРЕЛЁТ и ПРОЖИВАНИЕ (если только что-то одно или «частично» — не подходит).
   Если прямо не сказано, но программа такого типа обычно полностью финансируется (IVLP, Open World, программы
   правительств, ООН, ОБСЕ, Совета Европы) — ok=true, но score не выше 6 и в why напиши, что уточнить;
6) не требуется IELTS, TOEFL или другой экзамен/сертификат;
7) можно подать из Казахстана (или из любой страны);
8) дедлайн ещё не прошёл (если указан).
score 0–10 — насколько подходит этому человеку (10: тема по профилю, всё оплачено, рассчитано на юристов/руководителей).
Ответь ТОЛЬКО JSON-массивом, по объекту на КАЖДЫЙ пункт:
{{"n": номер, "ok": true/false, "score": 0-10, "title_ru": "короткое название по-русски",
"why": "1 строка почему", "covers": "что оплачивают или пусто", "dates": "даты/длительность или пусто",
"place": "страна, город или пусто", "deadline": "YYYY-MM-DD или пусто", "fee": true/false}}"""


def _parse_json_list(raw: str) -> list:
    raw = re.sub(r"^\x60{3}(?:json)?|\x60{3}$", "", (raw or "").strip(), flags=re.M)
    m = re.search(r"\[.*\]", raw, re.S)
    data = json.loads(m.group(0) if m else raw)
    return data if isinstance(data, list) else []


def _apply_verdict(it: dict, v: dict) -> bool:
    """Заполняет находку данными ИИ. True — отправлять."""
    try:
        score = int(float(v.get("score", 0)))
    except (TypeError, ValueError):
        score = 0
    dl = parse_date(str(v.get("deadline") or "")) or find_deadline(it["text"])
    if dl and dl < today():
        return False
    if not v.get("ok") or score < MIN_SCORE:
        return False
    it.update(score=score, deadline=dl.isoformat() if dl else "", title_ru=str(v.get("title_ru") or "").strip(),
              why=str(v.get("why") or "").strip(), covers=str(v.get("covers") or "").strip(),
              dates=str(v.get("dates") or "").strip(), place=str(v.get("place") or "").strip(),
              fee=bool(v.get("fee")) or it.get("fee", False))
    return True


async def ai_judge(cands: list, stats: dict) -> list:
    """ИИ оценивает кандидатов. Решённые помечаются как просмотренные; при сбое ИИ — остаются на потом."""
    if not cands:
        return []
    if not (groq_chat and GROQ_KEY):  # без ИИ — строгий запасной режим по ключевым словам
        keep = []
        for it in cands:
            _mark_seen(it["id"], _title_key(it["title"]))
            dl = find_deadline(it["text"])
            if it["funded"] and it["topics"] and not it["fee"] and not (dl and dl < today()):
                it["deadline"] = dl.isoformat() if dl else ""
                keep.append(it)
        return keep
    todo = cands[:AI_PER_ROUND]
    await enrich(todo)
    keep = []
    for i in range(0, len(todo), AI_BATCH):
        batch = todo[i:i + AI_BATCH]
        listing = "\n\n".join(f"{n + 1}. {it['title']}\nИсточник: {it['src']}\n{focus(it['text'])}"
                              for n, it in enumerate(batch))
        try:
            raw = await groq_chat([
                {"role": "system", "content": AI_FILTER.format(today=today().isoformat(), profile=PROFILE,
                                                               topics=", ".join(TOPICS))},
                {"role": "user", "content": listing}], max_tokens=2500, temperature=0.1)
            verdicts = {}
            for v in _parse_json_list(raw):
                if isinstance(v, dict) and str(v.get("n", "")).strip().isdigit():
                    verdicts[int(str(v["n"]).strip()) - 1] = v
        except Exception as e:
            log.warning("ИИ-отбор: %s — остальное проверю в следующий раз", e)
            stats["ai_error"] = str(e)[:120]
            break
        for n, it in enumerate(batch):
            _mark_seen(it["id"], _title_key(it["title"]))
            if n in verdicts and _apply_verdict(it, verdicts[n]):
                keep.append(it)
        stats["judged"] = stats.get("judged", 0) + len(batch)
        await asyncio.sleep(2)  # бережём лимит бесплатного Groq
    return keep


def dedupe(items: list) -> list:
    """Убирает то, что уже присылали (похожий заголовок за 60 дней), и дубли внутри прохода."""
    recent, out = _recent_titles(), []
    for it in sorted(items, key=lambda x: -x.get("score", 0)):
        names = [it["title"], it.get("title_ru", "")]
        if any(similar(n, r) for n in names if n for r in recent):
            continue
        recent.extend(n for n in names if n)
        out.append(it)
    return out


# ---------- Сообщение ----------
def fmt_item(it: dict) -> str:
    title = it.get("title_ru") or it["title"]
    msg = f"🌍 <b>{html.escape(title)}</b>\n"
    if it.get("title_ru") and it["title_ru"].lower() != it["title"].lower():
        msg += f"<i>{html.escape(it['title'][:160])}</i>\n"
    head = []
    if it.get("score"):
        head.append(f"⭐ {it['score']}/10")
    if it.get("topics"):
        head.append("🏷 " + ", ".join(it["topics"][:3]))
    if head:
        msg += " · ".join(head) + "\n"
    where = " · ".join(x for x in (it.get("place"), it.get("dates")) if x)
    if where:
        msg += f"📍 {html.escape(where)}\n"
    if it.get("covers"):
        msg += f"💸 Оплачивают: {html.escape(it['covers'])}\n"
    if it.get("why"):
        msg += f"✅ {html.escape(it['why'])}\n"
    if it.get("deadline"):
        msg += f"⏰ Дедлайн: {fmt_deadline(it['deadline'])}\n"
    else:
        msg += "⏰ Дедлайн: не указан — уточните на сайте\n"
    if it.get("official") and it["official"] != it["link"]:
        msg += f"🔗 Официальный сайт: {html.escape(it['official'])}\n"
    msg += f"📰 {html.escape(it['src'])}: {html.escape(it['link'])}"
    if it.get("fee"):
        msg += "\n⚠️ Похоже, есть взнос за подачу/участие — проверьте."
    return msg


def _note(it: dict) -> str:
    return " | ".join(x for x in (it.get("why"), it.get("covers"), it.get("place"), it.get("dates")) if x)


# ---------- Бот ----------
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
_lock = asyncio.Lock()


def _kb(item_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📄 Документы и требования", callback_data=f"doc:{item_id}"),
    ]])


async def _send(chats, items):
    for it in items:
        it["note"] = _note(it)
        item_id = _save_item(it)
        for chat in chats:
            try:
                m = await bot.send_message(chat, fmt_item(it), reply_markup=_kb(item_id),
                                           disable_web_page_preview=True)
                with closing(_db()) as c, c:
                    c.execute("INSERT OR IGNORE INTO sent VALUES (?,?,?)", (chat, m.message_id, item_id))
                await asyncio.sleep(0.5)
            except Exception as e:
                log.warning("send to %s failed: %s", chat, e)


async def check(manual_chat=None):
    async with _lock:
        stats = {}
        first = _seen_count() == 0
        cands = await collect(stats)
        items = dedupe(await ai_judge(cands, stats))
        items.sort(key=lambda x: (-x.get("score", 0), x.get("deadline") or "9999"))
        if first:
            items = items[:FIRST_RUN_LIMIT]
        log.info("проверка: новых %s, кандидатов %s, ИИ проверил %s, отправляю %s",
                 stats.get("new", 0), stats.get("candidates", 0), stats.get("judged", 0), len(items))
        if manual_chat:
            left = max(0, stats.get("candidates", 0) - stats.get("judged", 0))
            txt = (f"Проверено источников: {stats.get('sources_ok', 0)}. Новых записей: {stats.get('new', 0)}, "
                   f"подходящих по ключевым словам: {stats.get('candidates', 0)}, ИИ проверил: "
                   f"{stats.get('judged', 0)}, подошло: {len(items)}.")
            if left:
                txt += f"\nЕщё {left} в очереди — ИИ проверит в следующие проходы."
            if stats.get("ai_error"):
                txt += "\n⚠️ ИИ временно не ответил (лимит Groq?) — непроверенное не потеряется."
            await bot.send_message(manual_chat, txt)
        await _send([manual_chat] if manual_chat else _subscribers(), items)


# ---------- ИИ-консультант: документы, требования, вопросы ----------
ADVISOR = """Ты — опытный консультант по международным грантам, конференциям, учебным визитам и программам обмена.
Сегодня {today}. Пользователь — практикующий юрист, руководитель и лидер из Казахстана (паспорт Казахстана), 50+ лет,
не студент, без IELTS/TOEFL, говорит по-русски. Университетские программы ему не интересны.
Отвечай по-русски, коротко и по делу, в формате Telegram (без таблиц, без markdown-заголовков и звёздочек;
можно • и эмодзи). Опирайся на тексты страниц, если они даны, и указывай ссылки-источники.
Чего на страницах нет — так и пиши «на странице не указано», а затем отдельно дай типичный ответ с пометкой «обычно».
Если дедлайн уже прошёл — скажи об этом в первой строке. Ничего не выдумывай как факт."""

DOCS_TASK = """Разбери эту возможность по пунктам:
1. Что это и где/когда проходит.
2. Кто может подать (возраст, страна, опыт, язык — нужен ли IELTS/TOEFL или другой экзамен). Подходит ли юристу/руководителю 50+ из Казахстана без IELTS?
3. Что оплачивают (перелёт, проживание, питание, виза, суточные) и что НЕ оплачивают.
4. Есть ли взнос за подачу/участие — и сколько. Если есть платный взнос или «полностью оплачено только части участников» — предупреди.
5. Дедлайн (и сколько дней осталось).
6. 📄 Список документов для подачи (CV, мотивационное письмо, эссе, рекомендации, паспорт, видео и т.д.).
7. Как подать — шаги и ссылка на официальную форму.
8. 💡 2–3 совета, как повысить шансы именно этому человеку (практикующий юрист и руководитель 50+, Казахстан).
9. Нужна ли виза и как её обычно получают из Казахстана."""

STOP = set("какие какой какая что как где когда нужно нужны нужен есть для про это мне можно или и в на по с о из "
           "сейчас открыты открыт the a an of for to in on and or is are what which how".split())


def _db_search(question: str, limit=8) -> str:
    """Ищет в базе уже найденных ботом возможностей (только актуальные, лучшие совпадения)."""
    words = [w for w in re.findall(r"[a-zа-яёәіңғүұқөһ0-9]{3,}", question.lower()) if w not in STOP][:8]
    if not words:
        return ""
    t0 = today().isoformat()
    with closing(_db()) as c:
        rows = c.execute("SELECT title, link, text, deadline FROM items WHERE deadline IS NULL OR deadline = '' "
                         "OR deadline >= ? ORDER BY id DESC LIMIT 2000", (t0,)).fetchall()
    scored = []
    for t, l, x, dl in rows:
        blob = f"{t} {x}".lower()
        sc = sum(blob.count(w[:6]) for w in words)
        if sc:
            scored.append((sc, t, l, dl))
    scored.sort(key=lambda r: -r[0])
    return "\n".join(f"• {t} — {l} (дедлайн: {fmt_deadline(dl) or 'см. страницу'})" for _, t, l, dl in scored[:limit])


def _bing_url(href: str) -> str:
    """bing.com/ck/a?...&u=a1<base64> → настоящая ссылка."""
    m = re.search(r"[?&]u=a1([^&]+)", href)
    if not m:
        return href
    try:
        s = m.group(1)
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "ignore")
    except Exception:
        return href


async def web_search(query: str, n=6) -> list:
    """Бесплатный поиск: DuckDuckGo → DuckDuckGo Lite → Bing → Google News. [(заголовок, ссылка, сниппет)]"""
    out, seen = [], set()

    def add(title, url, snip):
        url = html.unescape(url)
        if url.startswith("http") and "duckduckgo.com" not in url and "bing.com" not in _domain(url) \
                and url not in seen and len(out) < n:
            seen.add(url)
            out.append((_strip(title), url, _strip(snip)[:300]))

    async with aiohttp.ClientSession() as s:
        raw = await _fetch(s, f"https://html.duckduckgo.com/html/?q={quote(query)}", 15)
        for href, title, snip in re.findall(
                r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</',
                raw or "", re.S):
            m = re.search(r"uddg=([^&]+)", href)
            add(title, unquote(m.group(1)) if m else href, snip)
        if len(out) < 3:
            raw = await _fetch(s, f"https://lite.duckduckgo.com/lite/?q={quote(query)}", 15)
            for href, title in re.findall(r'<a[^>]+href="([^"]+)"[^>]*class=.result-link.[^>]*>(.*?)</a>',
                                          raw or "", re.S):
                m = re.search(r"uddg=([^&]+)", href)
                add(title, unquote(m.group(1)) if m else href, "")
        if len(out) < 3:
            raw = await _fetch(s, f"https://www.bing.com/search?q={quote(query)}&setlang=ru", 15)
            for href, title, rest in re.findall(
                    r'<li class="b_algo".*?<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)</li>', raw or "", re.S):
                p = re.search(r"<p[^>]*>(.*?)</p>", rest, re.S)
                add(title, _bing_url(html.unescape(href)), p.group(1) if p else "")
        if len(out) < 2:
            raw = await _fetch(s, _gn(query, days=0), 15)
            for e in feedparser.parse(raw or "").entries[:n]:
                add(e.get("title", ""), e.get("link", ""), e.get("summary", ""))
    return out


async def _queries(question: str) -> list:
    """ИИ превращает вопрос в 2 поисковых запроса (английский — там больше программ)."""
    try:
        raw = await groq_chat([{"role": "user", "content":
                                "Составь 2 коротких поисковых запроса (до 8 слов) для поиска в Google по вопросу ниже: "
                                "первый на английском, второй на языке вопроса. Ищем официальные страницы программ. "
                                "Ответь ТОЛЬКО JSON-массивом строк.\n\nВопрос: " + question}],
                              max_tokens=600, temperature=0.2)
        qs = [q for q in _parse_json_list(raw) if isinstance(q, str) and q.strip()][:2]
        return qs or [question]
    except Exception:
        return [question]


async def _read_pages(urls: list, limit=3) -> str:
    async with aiohttp.ClientSession() as s:
        texts = await asyncio.gather(*(page_text(u, s) for u in urls[:limit]), return_exceptions=True)
    parts = []
    for u, t in zip(urls, texts):
        if isinstance(t, tuple) and t[0]:
            parts.append(f"--- {u}\n{focus(t[0], 1500, 2000)}")
    return "\n\n".join(parts)


async def ask_ai(question: str, title="", link="", text="", official="") -> str:
    if not (groq_chat and GROQ_KEY):
        return "ИИ не подключён: на хостинге нет переменной GROQ_API_KEY."
    if title or link:
        main, found_official = await page_text(link)
        official = official or found_official
        ctx = f"Программа: {title}\nСсылка: {link}\nКраткое описание: {text[:1500]}\n\n"
        if main:
            ctx += f"Текст страницы:\n{focus(main, 2500, 3000)}\n\n"
        if official and official != link:
            off_text, _ = await page_text(official)
            if off_text:
                ctx += f"Официальная страница ({official}):\n{focus(off_text, 2500, 3000)}\n\n"
        if not main or not official:  # мало данных — ищем официальный сайт программы
            found = await web_search(f"{title or link} official how to apply eligibility")
            ctx += "Результаты поиска:\n" + "\n".join(f"• {t} — {u}: {sn}" for t, u, sn in found)
            if found and not main:
                ctx += "\n\n" + await _read_pages([u for _, u, _ in found], 2)
    else:
        found = []
        for q in await _queries(question):
            for r in await web_search(q, 5):
                if r[1] not in {f[1] for f in found}:
                    found.append(r)
        pages = await _read_pages([u for _, u, _ in found], 3) if found else ""
        mine = _db_search(question)
        ctx = ("Свежий поиск в интернете по вопросу:\n" +
               ("\n".join(f"• {t} — {u}: {sn}" for t, u, sn in found[:8]) or "ничего не нашлось") +
               (f"\n\nТексты найденных страниц:\n{pages}" if pages else "") +
               (f"\n\nАктуальные возможности, которые бот уже нашёл:\n{mine}" if mine else "") +
               "\n\nОпирайся на эти данные, указывай ссылки-источники; если данных мало — дополни общими "
               "знаниями с пометкой «обычно».")
    answer = await groq_chat([
        {"role": "system", "content": ADVISOR.format(today=today().isoformat())},
        {"role": "user", "content": f"{ctx[:14000]}\n\nВопрос:\n{question}"},
    ], max_tokens=2200, temperature=0.2)
    return answer.strip() or "Не получилось получить ответ, попробуйте ещё раз."


async def _reply_long(m: Message, text: str):
    for i in range(0, len(text), 3900):
        await m.answer(html.escape(text[i:i + 3900]), disable_web_page_preview=True)


# ---------- Команды ----------
@dp.message(Command("start"))
async def cmd_start(m: Message):
    with closing(_db()) as c, c:
        c.execute("INSERT OR IGNORE INTO subs(chat_id) VALUES (?)", (m.chat.id,))
    await m.answer("Подписка включена ✅\nПрисылаю только короткие очные программы для практикующих юристов, "
                   "руководителей и лидеров 50+: семинары, конференции, учебные визиты, повышение квалификации, "
                   "стажировки до 1 месяца. Бесплатно, перелёт и проживание оплачивают, без IELTS/TOEFL, "
                   "без университетских программ. Каждую находку ИИ читает целиком и ставит оценку; просроченное "
                   "не присылаю.\n"
                   "Темы: " + ", ".join(TOPICS) + f". Проверяю каждые {CHECK_EVERY_MIN} мин.\n\n"
                   "/now — проверить сейчас\n/list — актуальные находки по дедлайну (/list право — с фильтром)\n"
                   "/sources — источники\n/stop — отписаться\n\n"
                   "📄 Под каждым уведомлением есть кнопка «Документы и требования».\n"
                   "❓ Ответьте (reply) на уведомление вопросом, пришлите ссылку на программу "
                   "или просто спросите — бот поищет в интернете, прочитает страницы и ответит со ссылками. "
                   "Например: «какие сейчас открыты конференции по правам человека?»")


@dp.message(Command("stop"))
async def cmd_stop(m: Message):
    with closing(_db()) as c, c:
        c.execute("DELETE FROM subs WHERE chat_id=?", (m.chat.id,))
    await m.answer("Отписал. /start — включить снова.")


@dp.message(Command("sources"))
async def cmd_sources(m: Message):
    lines = [f"• {n}" for n in RSS_FEEDS] + [f"• {n}" for n in NEWS_FEEDS] + [f"• @{c}" for c in TG_CHANNELS]
    await m.answer("Источники:\n" + "\n".join(lines))


@dp.message(Command("list"))
async def cmd_list(m: Message):
    """Актуальные находки: дедлайн не прошёл (или не указан и найдено за 45 дней), ближайшие сверху."""
    key = ((m.text or "").split(maxsplit=1)[1:] or [""])[0].lower().strip()
    t0, fresh = today().isoformat(), (today() - timedelta(days=45)).isoformat()
    with closing(_db()) as c:
        raw = c.execute("SELECT title, link, text, deadline, score, added FROM items WHERE deadline >= ? "
                        "OR deadline IS NULL OR deadline = '' ORDER BY id DESC LIMIT 1000", (t0,)).fetchall()
    rows = []
    for t, l, x, dl, sc, added in raw:
        if key and key not in f"{t} {x}".lower():
            continue
        if not dl:  # старые записи: ищем дедлайн в тексте
            d = find_deadline(x)
            if d:
                if d < today():
                    continue
                dl = d.isoformat()
            elif not added or added < fresh:
                continue
        rows.append((t, l, x, dl, sc))
    rows.sort(key=lambda r: r[3] or "9999")
    rows = rows[:25]
    if not rows:
        await m.answer("Актуальных находок пока нет" + (f" по «{html.escape(key)}»" if key else "") +
                       ". Попробуйте /now или задайте вопрос текстом — бот поищет в интернете.")
        return
    lines = []
    for t, l, x, dl, sc in rows:
        s = f"• <a href=\"{html.escape(l)}\">{html.escape(t[:90])}</a>"
        if sc:
            s += f" ⭐{sc}"
        s += f" — ⏰ {fmt_deadline(dl)}" if dl else " — ⏰ см. сайт"
        lines.append(s)
    await m.answer("Актуальные возможности" + (f" по «{html.escape(key)}»" if key else "") + ":\n\n"
                   + "\n".join(lines), disable_web_page_preview=True)


@dp.message(Command("now"))
async def cmd_now(m: Message):
    await m.answer("Проверяю все источники, ИИ читает находки… (1–3 минуты)")
    await check(manual_chat=m.chat.id)


@dp.callback_query(F.data.startswith("doc:"))
async def cb_docs(cq: CallbackQuery):
    await cq.answer("Читаю страницу программы…")
    row = _get_item(int(cq.data.split(":", 1)[1]))
    if not row:
        await cq.message.answer("Не нашёл эту запись.")
        return
    await cq.message.answer("⏳ Читаю страницу и официальный сайт, разбираю требования… 10–40 секунд")
    try:
        ans = await ask_ai(DOCS_TASK, row["title"], row["link"], row["text"], row.get("official") or "")
    except Exception as e:
        log.exception("ai")
        ans = f"Ошибка ИИ: {e}"
    await _reply_long(cq.message, ans)
    await cq.message.answer("Есть вопрос по этой программе? Ответьте (reply) на уведомление о ней и спросите.")


URL_RE = re.compile(r"https?://\S+")


@dp.message(F.text & ~F.text.startswith("/"))
async def free_question(m: Message):
    """Вопрос в ответ на уведомление → про эту программу; со ссылкой → про страницу; иначе — общий поиск."""
    title = link = text = official = ""
    if m.reply_to_message:
        item_id = _item_by_msg(m.chat.id, m.reply_to_message.message_id)
        row = _get_item(item_id) if item_id else None
        if row:
            title, link, text, official = row["title"], row["link"], row["text"], row.get("official") or ""
        else:
            text = m.reply_to_message.text or ""
            urls = URL_RE.findall(text)
            link = urls[0] if urls else ""
    urls = URL_RE.findall(m.text)
    if urls:
        link = urls[0]
    await m.answer("⏳ Ищу и читаю страницы…")
    try:
        ans = await ask_ai(m.text, title, link, text, official)
    except Exception as e:
        log.exception("ai")
        ans = f"Ошибка ИИ: {e}"
    await _reply_long(m, ans)


async def _scheduler():
    await asyncio.sleep(20)
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
