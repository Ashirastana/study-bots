"""Радар возможностей: сам ищет полностью оплачиваемые конференции, гранты, стипендии,
бизнес- и инвест-программы (лидерство, экономика, право, права человека, туризм, дипломатия)
и присылает их в Telegram. Запускается лаунчером main.py (строка bot_grants в bots.list).

Источники: RSS сайтов-агрегаторов, Google News (EN/RU/KZ), публичные Telegram-каналы (t.me/s/<канал>).
Проверка каждые 10 минут, все источники параллельно; одна и та же новость с разных сайтов — одно сообщение.
Команды: /start — подписаться, /now — проверить сейчас, /sources — источники, /stop — отписаться.
ИИ (Groq): кнопка «📄 Документы и требования» под каждым уведомлением; reply на уведомление с вопросом;
ссылка на любую программу; или просто вопрос текстом.
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
from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

try:
    from ai import GROQ_KEY, groq_chat  # тот же бесплатный Groq, что у учебных ботов
except Exception:  # без модуля ai бот всё равно работает, только без ИИ-разбора
    GROQ_KEY, groq_chat = "", None

# Настройки читаются один раз при загрузке (лаунчер меняет os.environ для каждого бота)
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
CHECK_EVERY_MIN = int(os.getenv("CHECK_EVERY_MIN", "10"))
DB_PATH = os.getenv("DB_PATH", "bot.db")
FIRST_RUN_LIMIT = 8  # при самом первом запуске прислать не больше стольких свежих находок

log = logging.getLogger("grants")

# ---------- Источники ----------
RSS_FEEDS = {
    # агрегаторы возможностей (публикуют раньше всех, по много раз в день)
    "Opportunity Desk": "https://opportunitydesk.org/feed/",
    "Opportunities Corners": "https://opportunitiescorners.com/feed/",
    "Opportunities for Youth": "https://opportunitiesforyouth.org/feed/",
    "Opportunities Radar": "https://opportunitiesradar.com/feed/",
    "Opportunities for Africans": "https://www.opportunitiesforafricans.com/feed/",
    "After School Africa": "https://www.afterschoolafrica.com/feed/",
    "Opportunity Portal": "https://opportunityportal.info/feed/",
    "Greatyop": "https://greatyop.com/feed/",
    "FundsForNGOs": "https://www2.fundsforngos.org/feed/",
    "Funds for Individuals": "https://fundsforindividuals.fundsforngos.org/feed/",
    "Scholars4Dev": "https://www.scholars4dev.com/feed/",
    "Scholarship Positions": "https://scholarship-positions.com/feed/",
    "Scholarships Corner": "https://scholarshipscorner.website/feed/",
    "Scholarship Union": "https://scholarshipunion.com/feed/",
    "TrueScho Opportunities": "https://fullopportunities.truescho.com/feed/",
    "TrueScho Scholars": "https://scholars.truescho.com/feed/",
    "Opportunities Circle": "https://www.opportunitiescircle.com/feed/",
    "Mladiinfo (Европа)": "https://www.mladiinfo.eu/feed/",
    "ProFellow (fellowships)": "https://www.profellow.com/feed/",
    "Scholarship Roar": "https://scholarshiproar.com/feed/",
    "Scholarships Hall": "https://scholarshipshall.com/feed/",
    "Youth Opportunities Hub": "https://youthopportunitieshub.com/feed/",
    "Opportunity Desk: конференции": "https://opportunitydesk.org/category/conferences/feed/",
    "Opportunity Desk: стипендии": "https://opportunitydesk.org/category/scholarships/feed/",
}

# Google News по всему миру: свежие новости и анонсы (EN / RU / KZ)
def _gn(q, lang="en"):
    from urllib.parse import quote
    loc = {"en": "hl=en-US&gl=US&ceid=US:en", "ru": "hl=ru&gl=KZ&ceid=KZ:ru", "kk": "hl=kk&gl=KZ&ceid=KZ:kk"}[lang]
    return f"https://news.google.com/rss/search?q={quote(q + ' when:2d')}&{loc}"

NEWS_FEEDS = {
    "Google News: fully funded conference": _gn('"fully funded" conference OR summit OR forum'),
    "Google News: fully funded fellowship": _gn('"fully funded" fellowship OR program leadership'),
    "Google News: law & human rights": _gn('"fully funded" "human rights" OR law OR legal program'),
    "Google News: business & invest": _gn('"fully funded" entrepreneurship OR startup OR investment program'),
    "Google News: tourism": _gn('"fully funded" tourism OR hospitality program'),
    "Google News: call for applications": _gn('"call for applications" "travel costs" youth'),
    "Google News: summer school": _gn('"fully funded" "summer school" OR "winter school"'),
    "Google News: exchange": _gn('"fully funded" exchange program youth OR students'),
    "Google News: travel grant": _gn('"travel grant" OR "travel bursary" conference 2027'),
    "Google News: UN / Council of Europe": _gn('UN OR "Council of Europe" OR OSCE youth programme "travel and accommodation"'),
    "Google News: Erasmus+": _gn('Erasmus+ youth exchange OR training course Kazakhstan OR "Central Asia"'),
    "Google News RU: обмены и школы": _gn('летняя школа OR программа обмена перелет проживание оплачиваются', "ru"),
    "Google News RU: гранты": _gn('грант молодежь конференция OR форум OR стажировка оплачивается', "ru"),
    "Google News RU: Казахстан": _gn('Казахстан грант OR стипендия студентам за рубежом', "ru"),
    "Google News KZ": _gn('грант шетелде студенттер', "kk"),
}

# Публичные Telegram-каналы (читаются через t.me/s/<канал>, без входа)
TG_CHANNELS = [
    "we_project",               # Creative Asia — Центральная Азия
    "joinyouthuz",              # Grants and Opportunities
    "BrightScholarship",
    "scholarshipsee",
    "opportunityportalabroad",
    "scholarshipregion",
    "studyqacom",
    "nextscholargrant",
    "studyabroadl",
    "EngIishSpace",
    # несуществующий канал просто пропускается — добавляйте любые: имя без @
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


GRANT_WORDS = ["grant", "scholarship", "fellowship", "грант", "стипенд", "шәкіртақы"]


def classify(text: str, news: bool = False):
    """None — не подходит; иначе (темы, есть_ли_взнос). Для новостей грант = достаточный признак."""
    t = " " + text.lower() + " "
    funded = any(w in t for w in FUNDED) or (news and any(w in t for w in GRANT_WORDS))
    if not funded or not any(w in t for w in FORMAT):
        return None
    topics = [name for name, words in TOPICS.items() if any(w in t for w in words)] or ["другое"]
    return topics, any(w in t for w in FEE_WORDS)


DEADLINE_RE = re.compile(r"(?:deadline|apply by|closing date|дедлайн|срок подачи|прием заявок до)\s*[:\-–]?\s*"
                         r"([^\n.;|]{4,40})", re.I)


def _deadline(text: str) -> str:
    m = DEADLINE_RE.search(text)
    if not m:
        return ""
    d = m.group(1).strip()
    y = re.search(r"20\d\d", d)  # обрезаем всё после года
    return d[:y.end()] if y else d


def _title_key(title: str) -> str:
    """Одна и та же возможность на разных сайтах — одно уведомление."""
    return "t:" + re.sub(r"[^a-zа-яё0-9]+", "", title.lower())[:60]


# ---------- База ----------
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE IF NOT EXISTS subs (chat_id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "title TEXT, link TEXT, text TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS sent (chat_id INTEGER, msg_id INTEGER, item_id INTEGER, "
                 "PRIMARY KEY (chat_id, msg_id))")
    return conn


def _save_item(title, link, text) -> int:
    with closing(_db()) as c, c:
        return c.execute("INSERT INTO items(title, link, text) VALUES (?,?,?)",
                         (title, link, text[:4000])).lastrowid


def _get_item(item_id):
    with closing(_db()) as c:
        return c.execute("SELECT title, link, text FROM items WHERE id=?", (item_id,)).fetchone()


def _item_by_msg(chat_id, msg_id):
    with closing(_db()) as c:
        r = c.execute("SELECT item_id FROM sent WHERE chat_id=? AND msg_id=?", (chat_id, msg_id)).fetchone()
    return r[0] if r else None


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


async def _rss(s, sem, name, url, news):
    async with sem:
        raw = await _fetch(s, url)
    out = []
    if raw:
        for e in feedparser.parse(raw).entries[:30]:
            title, link = _strip(e.get("title", "")), e.get("link", "")
            text = f"{title} {_strip(e.get('summary', ''))}"
            out.append((link or title, name, title, link, text, news))
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
            out.append((post_id, f"@{ch}", text[:140], f"https://t.me/{post_id}", text, False))
    return out


async def collect():
    """Новые подходящие находки со всех источников разом (каждая помечается как увиденная)."""
    sem = asyncio.Semaphore(8)
    async with aiohttp.ClientSession() as s:
        jobs = [_rss(s, sem, n, u, False) for n, u in RSS_FEEDS.items()]
        jobs += [_rss(s, sem, n, u, True) for n, u in NEWS_FEEDS.items()]
        jobs += [_tg(s, sem, ch) for ch in TG_CHANNELS]
        results = await asyncio.gather(*jobs, return_exceptions=True)
    fresh = []
    for res in results:
        if isinstance(res, Exception):
            log.warning("источник упал: %s", res)
            continue
        for item_id, src, title, link, text, news in res:
            cls = classify(text, news)
            if not cls:
                continue
            new_id = _mark_new(item_id)
            new_title = _mark_new(_title_key(title))
            if new_id and new_title:
                fresh.append((src, title, link, *cls, _deadline(text), text))
    return fresh


def _fmt(src, title, link, topics, fee, deadline="", text=""):
    msg = f"🌍 <b>{html.escape(title)}</b>\n🏷 {', '.join(topics)}\n"
    if deadline:
        msg += f"⏰ Дедлайн: {html.escape(deadline)}\n"
    msg += f"📰 {html.escape(src)}\n{link}"
    if fee:
        msg += "\n⚠️ Похоже, за подачу берут взнос — проверьте организатора."
    return msg


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
        src, title, link = it[0], it[1], it[2]
        item_id = _save_item(title, link, it[-1])
        for chat in chats:
            try:
                m = await bot.send_message(chat, _fmt(*it), reply_markup=_kb(item_id))
                with closing(_db()) as c, c:
                    c.execute("INSERT OR IGNORE INTO sent VALUES (?,?,?)", (chat, m.message_id, item_id))
                await asyncio.sleep(0.5)
            except Exception as e:
                log.warning("send to %s failed: %s", chat, e)


# ---------- ИИ-разбор: документы, требования, как подать ----------
ADVISOR = """Ты — опытный консультант по международным грантам, стипендиям, конференциям и программам обмена.
Пользователь — студент-юрист 3 курса из Казахстана (паспорт Казахстана), говорит по-русски.
Отвечай по-русски, коротко и по делу, в формате Telegram (без таблиц, без markdown-заголовков; можно • и эмодзи).
Опирайся на текст страницы программы, если он дан. Чего на странице нет — так и пиши «на странице не указано»,
а затем, отдельно, дай типичный ответ для таких программ с пометкой «обычно». Ничего не выдумывай как факт."""

DOCS_TASK = """Разбери эту возможность по пунктам:
1. Что это и где/когда проходит.
2. Кто может подать (возраст, страна, уровень, язык — нужен ли IELTS/TOEFL). Подходит ли студенту из Казахстана?
3. Что оплачивают (перелёт, проживание, питание, виза, стипендия) и что НЕ оплачивают.
4. Есть ли взнос за подачу/участие — и сколько. Если есть платный взнос или «полностью оплачено только топ-10» — предупреди.
5. Дедлайн.
6. 📄 Список документов для подачи (CV, мотивационное письмо, эссе, рекомендации, паспорт, справка с учёбы, видео и т.д.).
7. Как подать — шаги и ссылка.
8. 💡 2–3 совета, как повысить шансы именно этому студенту (юрист, Казахстан).
9. Нужна ли виза и как её обычно получают из Казахстана."""

PAGE_RE_DROP = re.compile(r"<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", re.S | re.I)


async def _page_text(link: str) -> str:
    if not link or not link.startswith("http"):
        return ""
    async with aiohttp.ClientSession() as s:
        raw = await _fetch(s, link)
    if not raw:
        return ""
    return _strip(PAGE_RE_DROP.sub(" ", raw))[:12000]


STOP = set("какие какой какая что как где когда нужно нужны нужен есть для про это мне можно или и в на по с о из the a an of for to in on and or is are what which how".split())


def _db_search(question: str, limit=8) -> str:
    """Ищет в базе уже найденных ботом возможностей (лучшие совпадения по словам вопроса)."""
    words = [w for w in re.findall(r"[a-zа-яёәіңғүұқөһ0-9]{3,}", question.lower()) if w not in STOP][:8]
    if not words:
        return ""
    with closing(_db()) as c:
        rows = c.execute("SELECT title, link, text FROM items ORDER BY id DESC LIMIT 2000").fetchall()
    scored = []
    for t, l, x in rows:
        blob = f"{t} {x}".lower()
        sc = sum(blob.count(w[:6]) for w in words)
        if sc:
            scored.append((sc, t, l, x))
    scored.sort(key=lambda r: -r[0])
    return "\n".join(f"• {t} — {l} (дедлайн: {_deadline(x) or 'см. страницу'})" for _, t, l, x in scored[:limit])


async def _web_search(query: str, n=6):
    """Бесплатный поиск в интернете (DuckDuckGo HTML) → [(заголовок, ссылка, сниппет)]."""
    from urllib.parse import quote, unquote
    async with aiohttp.ClientSession() as s:
        raw = await _fetch(s, f"https://html.duckduckgo.com/html/?q={quote(query)}")
    out = []
    if raw:
        for href, title, snip in re.findall(
                r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</', raw, re.S):
            m = re.search(r"uddg=([^&]+)", href)
            url = unquote(m.group(1)) if m else href
            if url.startswith("http") and "duckduckgo.com" not in url:
                out.append((_strip(title), url, _strip(snip)))
            if len(out) >= n:
                break
    if not out:  # запасной вариант — Google News
        async with aiohttp.ClientSession() as s:
            raw = await _fetch(s, _gn(query).replace("%20when%3A2d", ""))
        if raw:
            for e in feedparser.parse(raw).entries[:n]:
                out.append((_strip(e.get("title", "")), e.get("link", ""), _strip(e.get("summary", ""))[:200]))
    return out


async def _ask_ai(question: str, title="", link="", text="") -> str:
    if not (groq_chat and GROQ_KEY):
        return "ИИ не подключён: на хостинге нет переменной GROQ_API_KEY."
    if title or link:
        page = await _page_text(link)
        ctx = f"Программа: {title}\nСсылка: {link}\nКраткое описание: {text[:1500]}\n\nТекст страницы:\n{page}"
        if not page:  # страница не открылась — ищем официальный сайт программы
            found = await _web_search(f"{title or link} official requirements documents how to apply")
            ctx += "\n\nРезультаты поиска:\n" + "\n".join(f"• {t} — {u}: {sn}" for t, u, sn in found)
            if found:
                ctx += "\n\nТекст первой найденной страницы:\n" + (await _page_text(found[0][1]))[:8000]
    else:
        found = await _web_search(question)
        page = (await _page_text(found[0][1]))[:8000] if found else ""
        mine = _db_search(question)
        ctx = ("Свежий поиск в интернете по вопросу:\n" +
               ("\n".join(f"• {t} — {u}: {sn}" for t, u, sn in found) or "ничего не нашлось") +
               (f"\n\nТекст первой страницы ({found[0][1]}):\n{page}" if page else "") +
               (f"\n\nВозможности, которые бот уже нашёл ранее:\n{mine}" if mine else "") +
               "\n\nОпирайся на эти данные, указывай ссылки-источники; если данных мало — дополни общими "
               "знаниями с пометкой «обычно».")
    answer = await groq_chat([
        {"role": "system", "content": ADVISOR},
        {"role": "user", "content": f"{ctx}\n\nВопрос:\n{question}"},
    ], max_tokens=1800, temperature=0.2)
    return answer.strip() or "Не получилось получить ответ, попробуйте ещё раз."


async def _reply_long(m: Message, text: str):
    for i in range(0, len(text), 3900):
        await m.answer(html.escape(text[i:i + 3900]))


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
                   "стипендии и бизнес-программы со всего мира (30+ сайтов, Telegram-каналы, Google News): "
                   f"лидерство, экономика, право, права человека, туризм, дипломатия и другое. Проверяю каждые {CHECK_EVERY_MIN} мин.\n\n"
                   "/now — проверить сейчас\n/list — последние находки (/list право — с фильтром)\n"
                   "/sources — источники\n/stop — отписаться\n\n"
                   "📄 Под каждым уведомлением есть кнопка «Документы и требования».\n"
                   "❓ Ответьте (reply) на уведомление любым вопросом, пришлите ссылку на программу "
                   "или просто спросите что угодно — бот поищет в интернете, прочитает официальную страницу "
                   "и ответит со ссылками. Например: «какие документы нужны для Chevening?», "
                   "«какие сейчас открыты конференции по правам человека?»")


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
    """Последние найденные возможности (можно с фильтром: /list право)."""
    q = (m.text or "").split(maxsplit=1)[1:] or [""]
    with closing(_db()) as c:
        rows = c.execute("SELECT title, link, text FROM items ORDER BY id DESC LIMIT 400").fetchall()
    key = q[0].lower()
    rows = [r for r in rows if not key or key in f"{r[0]} {r[2]}".lower()][:20]
    if not rows:
        await m.answer("Пока ничего не нашлось. Попробуйте позже или /now.")
        return
    lines = [f"• <a href=\"{html.escape(l)}\">{html.escape(t[:90])}</a>"
             + (f" — ⏰ {html.escape(_deadline(x))}" if _deadline(x) else "") for t, l, x in rows]
    await m.answer("Последние возможности" + (f" по «{html.escape(key)}»" if key else "") + ":\n\n"
                   + "\n".join(lines), disable_web_page_preview=True)


@dp.message(Command("now"))
async def cmd_now(m: Message):
    await m.answer("Проверяю…")
    await check(manual_chat=m.chat.id)


@dp.callback_query(F.data.startswith("doc:"))
async def cb_docs(cq: CallbackQuery):
    await cq.answer("Читаю страницу программы…")
    row = _get_item(int(cq.data.split(":", 1)[1]))
    if not row:
        await cq.message.answer("Не нашёл эту запись.")
        return
    await cq.message.answer("⏳ Разбираю требования и документы, это займёт 10–30 секунд…")
    try:
        ans = await _ask_ai(DOCS_TASK, *row)
    except Exception as e:
        log.exception("ai")
        ans = f"Ошибка ИИ: {e}"
    await _reply_long(cq.message, ans)
    await cq.message.answer("Есть вопрос по этой программе? Ответьте (reply) на уведомление о ней и спросите.")


URL_RE = re.compile(r"https?://\S+")


@dp.message(F.text & ~F.text.startswith("/"))
async def free_question(m: Message):
    """Вопрос в ответ на уведомление → про эту программу; со ссылкой → про страницу; иначе — общий."""
    title = link = text = ""
    if m.reply_to_message:
        item_id = _item_by_msg(m.chat.id, m.reply_to_message.message_id)
        row = _get_item(item_id) if item_id else None
        if row:
            title, link, text = row
        else:
            text = m.reply_to_message.text or ""
            urls = URL_RE.findall(text)
            link = urls[0] if urls else ""
    urls = URL_RE.findall(m.text)
    if urls:
        link = urls[0]
    await m.answer("⏳ Думаю…")
    try:
        ans = await _ask_ai(m.text, title, link, text)
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
