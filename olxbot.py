"""OLX-помощник: пишешь обычным текстом, что ищешь, а ИИ сам ищет на olx.kz, читает
ПОЛНОЕ описание каждого объявления и присылает только то, что реально подходит.
Отсеивает «не работает», «на запчасти», «разбит», «в залоге» и всё, что противоречит твоим требованиям.
Запускается лаунчером main.py (строка bot_olx в bots.list).

Как пользоваться:
  • Просто напиши, что ищешь: «iPhone 13 в Астане до 200 тыс, только рабочий, без замен экрана».
    Бот разберёт запрос, сразу пришлёт лучшее из текущих объявлений и дальше будет следить за новыми.
  • 👍 / 👎 под карточкой — бот учится твоему вкусу для этого поиска.
  • 🔍 Подробно — разбор: соответствие требованиям, цена против рынка, риски, что спросить у продавца.
  • Ответь (reply) на карточку вопросом — ИИ ответит по этому объявлению.
  • Ответь (reply) на сообщение «🔎 Поиск #N» — уточнишь поиск («и чтобы с коробкой»).
Команды: /my — мои поиски, /now — проверить сейчас, /fav — избранное, /help, /invite (админ).
"""
import asyncio
import html
import json
import logging
import os
import re
import sqlite3
import statistics
import time
from contextlib import closing

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

try:
    import ai as _ai  # общий бесплатный Groq
    GROQ_KEY = _ai.GROQ_KEY
except Exception:
    _ai, GROQ_KEY = None, ""

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in re.findall(r"\d+", os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "")))}
# Код лучше держать в секретах хостинга (OLX_INVITE_CODE): репозиторий публичный
INVITE_CODE = (os.getenv("OLX_INVITE_CODE") or os.getenv("INVITE_CODE") or "").strip()
CHECK_EVERY_MIN = int(os.getenv("CHECK_EVERY_MIN", "10"))
DB_PATH = os.getenv("DB_PATH", "olx.db")
SCREEN_MODEL = os.getenv("OLX_SCREEN_MODEL", "openai/gpt-oss-20b")  # отдельный лимит Groq от других ботов
MAX_SEARCHES = int(os.getenv("MAX_SEARCHES", "6"))
FIRST_SCAN = int(os.getenv("FIRST_SCAN", "150"))  # сколько объявлений просмотреть при создании поиска
MIN_SCORE = int(os.getenv("MIN_SCORE", "7"))

log = logging.getLogger("olx")
API = "https://www.olx.kz/api/v1"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
           "Accept": "application/json", "Accept-Language": "ru-RU,ru;q=0.9"}

# ---------- Справочники OLX ----------
CATEGORIES = {  # ключ ИИ → id категории olx.kz (проверено)
    "cars": 108, "transport": 3, "real_estate": 1, "flat_rent": 2979, "flat_daily": 2974,
    "flat_sale": 2982, "phones": 85, "laptops": 80,
}
CITIES = {
    "астана": 87, "нур-султан": 87, "astana": 87, "алматы": 1, "almaty": 1, "алма-ата": 1,
    "шымкент": 47, "караганда": 39, "актобе": 26, "тараз": 53, "павлодар": 36,
    "усть-каменогорск": 52, "оскемен": 52, "семей": 58, "атырау": 28, "костанай": 51,
    "кызылорда": 45, "уральск": 19, "петропавловск": 7, "актау": 5633, "туркестан": 74,
    "кокшетау": 24, "талдыкорган": 40, "экибастуз": 85, "темиртау": 65, "жезказган": 38,
    "каскелен": 20, "косшы": 7301,
}
# Явные признаки «не то» — отсекаются ещё до ИИ, если человек хочет рабочую/целую вещь
DEFECT_WORDS = ["не работает", "не рабочий", "нерабочий", "на запчасти", "на запчасть", "под ремонт",
                "требует ремонта", "не включается", "разбит", "треснут", "утоплен", "icloud", "айклауд",
                "заблокирован", "в залоге", "под залогом", "аварийн", "после дтп", "битый", "не на ходу",
                "не заводится", "без документов", "дубликат птс", "істемейді", "жұмыс істемейді"]


# ---------- База ----------
def _db():
    c = sqlite3.connect(DB_PATH)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE IF NOT EXISTS searches (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, wish TEXT,
        spec TEXT, active INTEGER DEFAULT 1, created REAL, likes TEXT DEFAULT '[]', dislikes TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS seen (search_id INTEGER, offer_id INTEGER, PRIMARY KEY (search_id, offer_id));
    CREATE TABLE IF NOT EXISTS prices (search_id INTEGER, offer_id INTEGER, price INTEGER,
        PRIMARY KEY (search_id, offer_id));
    CREATE TABLE IF NOT EXISTS offers (id INTEGER PRIMARY KEY, data TEXT);
    CREATE TABLE IF NOT EXISTS sent (chat_id INTEGER, msg_id INTEGER, search_id INTEGER, offer_id INTEGER,
        verdict TEXT, PRIMARY KEY (chat_id, msg_id));
    CREATE TABLE IF NOT EXISTS search_msgs (chat_id INTEGER, msg_id INTEGER, search_id INTEGER,
        PRIMARY KEY (chat_id, msg_id));
    CREATE TABLE IF NOT EXISTS fav (chat_id INTEGER, offer_id INTEGER, PRIMARY KEY (chat_id, offer_id));
    """)
    return c


def q(sql, args=(), one=False):
    with closing(_db()) as c, c:
        cur = c.execute(sql, args)
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows


def allowed(chat_id) -> bool:
    return chat_id in ADMIN_IDS or not INVITE_CODE or bool(q("SELECT 1 FROM users WHERE chat_id=?", (chat_id,), True))


def get_search(sid):
    r = q("SELECT id, chat_id, wish, spec, active, likes, dislikes FROM searches WHERE id=?", (sid,), True)
    if not r:
        return None
    return {"id": r[0], "chat_id": r[1], "wish": r[2], "spec": json.loads(r[3]), "active": r[4],
            "likes": json.loads(r[5]), "dislikes": json.loads(r[6])}


# ---------- OLX ----------
def _price(o):
    for p in o.get("params", []):
        if p.get("key") == "price":
            v = p.get("value") or {}
            return v.get("value"), v.get("label", "")
    return None, ""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<br\s*/?>", "\n", s or "").replace("\n", " ; "))).strip()


def _params_text(o) -> str:
    return "; ".join(f"{p.get('name')}: {(p.get('value') or {}).get('label', '')}" for p in o.get("params", [])
                     if p.get("key") != "price")


def _photo(o):
    ph = o.get("photos") or []
    return ph[0]["link"].replace("{width}", "800").replace("{height}", "600") if ph and ph[0].get("link") else None


def _city(o) -> str:
    loc = o.get("location") or {}
    city = (loc.get("city") or {}).get("name", "")
    dist = (loc.get("district") or {}).get("name", "")
    return f"{city}, {dist.strip()}" if dist else city


async def olx_fetch(spec: dict, limit_total: int) -> list:
    """Свежие объявления по поиску (до limit_total), постранично по 50."""
    params = {"limit": 50, "sort_by": "created_at:desc"}
    if spec.get("query"):
        params["query"] = spec["query"]
    if CATEGORIES.get(spec.get("category") or ""):
        params["category_id"] = CATEGORIES[spec["category"]]
    cid = CITIES.get((spec.get("city") or "").lower().strip())
    if cid:
        params["city_id"] = cid
    if spec.get("price_from"):
        params["filter_float_price:from"] = int(spec["price_from"])
    if spec.get("price_to"):
        params["filter_float_price:to"] = int(spec["price_to"])
    out, ids = [], set()
    async with aiohttp.ClientSession(headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as s:
        for offset in range(0, limit_total, 50):
            try:
                async with s.get(f"{API}/offers/", params={**params, "offset": offset}) as r:
                    if r.status != 200:
                        log.warning("OLX HTTP %s: %s", r.status, (await r.text())[:200])
                        break
                    data = (await r.json()).get("data", [])
            except Exception as e:
                log.warning("OLX: %s", e)
                break
            for o in data:
                if o["id"] not in ids:
                    ids.add(o["id"])
                    out.append(o)
            if len(data) < 50:
                break
            await asyncio.sleep(0.7)
    return out


def prefilter(spec: dict, offers: list) -> list:
    """Дешёвый отсев без ИИ: цена, город, слова-«стоп» в заголовке и ПОЛНОМ описании."""
    stop = [w.lower() for w in spec.get("reject_words") or [] if w.strip()]
    if spec.get("working_only", True):
        stop += DEFECT_WORDS
    city = (spec.get("city") or "").lower().strip()
    pf, pt = spec.get("price_from"), spec.get("price_to")
    res = []
    for o in offers:
        text = f"{o.get('title', '')} {_clean(o.get('description', ''))}".lower()
        if any(w in text for w in stop):
            continue
        price, _ = _price(o)
        if price is not None and ((pf and price < pf) or (pt and price > pt)):
            continue
        if city and city not in CITIES and city not in _city(o).lower():
            continue
        res.append(o)
    return res


# ---------- ИИ ----------
async def _llm(messages, max_tokens=1500, model=None, temperature=0.2, effort="low") -> str:
    """Запрос к Groq с повтором при лимите (429)."""
    if not (_ai and GROQ_KEY):
        raise RuntimeError("Нет GROQ_API_KEY")
    for attempt in range(5):
        m = model or await _ai.groq_model()
        body = {"model": m, "max_tokens": max_tokens, "temperature": temperature, "messages": messages}
        if "gpt-oss" in m:
            body["reasoning_effort"] = effort
        try:
            r = await _ai._groq_request("POST", "/chat/completions", json_body=body)
            return r["choices"][0]["message"].get("content") or ""
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate_limit" in msg:
                w = re.search(r"try again in ([\d.]+)s", msg)
                await asyncio.sleep(min(60, float(w.group(1)) + 1 if w else 15 * (attempt + 1)))
                continue
            if model and ("model_not_found" in msg or "does not exist" in msg or "decommission" in msg):
                model = None  # запасной вариант — модель по умолчанию
                continue
            raise
    raise RuntimeError("ИИ перегружен, попробуйте чуть позже")


def _json(text: str):
    text = re.sub(r"^\x60{3}(?:json)?|\x60{3}$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    return json.loads(m.group(0) if m else text)


PARSE_PROMPT = """Ты помогаешь искать на OLX.kz (Казахстан). Преврати желание пользователя в JSON поиска.
Поля:
"query": короткий поисковый запрос для OLX (2–4 слова, как люди пишут в заголовках: «iphone 13», «toyota camry», «2-комнатная квартира»). Без цены, города и прилагательных.
"category": одно из cars, transport, real_estate, flat_rent (долгосрочная аренда квартиры), flat_daily (посуточно), flat_sale (купить квартиру), phones, laptops — или null, если не подходит ни одна.
"city": город по-русски («Астана», «Алматы», ...) или null.
"price_from", "price_to": числа в тенге или null («до 200к» → 200000, «3 млн» → 3000000).
"requirements": список ОБЯЗАТЕЛЬНЫХ условий своими словами (что должно быть).
"nice_to_have": список желательного.
"reject_if": список того, что точно не нужно (по смыслу).
"reject_words": короткие точные фразы, при которых объявление отсеивается сразу (например «посуточно», «на запчасти», «обмен»). Не добавляй слишком общих слов.
"working_only": true, если вещь нужна целая/рабочая/без серьёзных дефектов (почти всегда true; false — если человек сам ищет на запчасти/под ремонт/аварийное).
"summary": одна строка — как ты понял запрос.
Ответь ТОЛЬКО JSON."""


async def parse_wish(wish: str, old: dict | None = None) -> dict:
    user = wish if not old else (f"Текущий поиск (JSON): {json.dumps(old, ensure_ascii=False)}\n"
                                 f"Пользователь уточняет: {wish}\nВерни обновлённый JSON целиком.")
    spec = _json(await _llm([{"role": "system", "content": PARSE_PROMPT}, {"role": "user", "content": user}],
                            max_tokens=1200, effort="medium"))
    for k in ("price_from", "price_to"):
        try:
            spec[k] = int(spec[k]) if spec.get(k) not in (None, "", 0) else None
        except (TypeError, ValueError):
            spec[k] = None
    if spec.get("category") not in CATEGORIES:
        spec["category"] = None
    spec.setdefault("working_only", True)
    return spec


SCREEN_PROMPT = """Ты строгий и внимательный помощник покупателя на OLX.kz. Пользователь хочет:
{wish}
Обязательно: {req}
Желательно: {nice}
Точно не нужно: {rej}
{taste}
Тебе дан список объявлений. ВНИМАТЕЛЬНО прочитай заголовок, параметры и ВСЁ описание каждого.
Отсеивай всё, что противоречит требованиям, даже если сказано мимоходом: «не работает …», «есть нюанс», «менял экран/дисплей»,
«в залоге», «обмен только», «посуточно» при аренде, перекупы/салоны если просили от хозяина, явный спам, не тот товар
(чехол вместо телефона, запчасть вместо машины), подозрительно низкая цена.
Для каждого объявления верни объект: {{"id": <id>, "score": 0-10 (насколько это реально понравится пользователю), "why": "1 строка почему подходит",
"flags": "минусы/риски одной строкой или пусто"}}.
score 8–10 — отлично подходит, 6–7 — неплохо, но есть минусы, 0–5 — не подходит.
Если о важном требовании в объявлении ничего не сказано — не выдумывай, снизь score на 1 и укажи в flags «не указано: …».
Ответь ТОЛЬКО JSON-массивом."""


def _offer_brief(o, desc_len=700) -> str:
    price, label = _price(o)
    return (f"id={o['id']} | {o.get('title', '')} | цена: {label or price} | {_city(o)} | "
            f"{'компания' if o.get('business') else 'частник'}\nПараметры: {_params_text(o)}\n"
            f"Описание: {_clean(o.get('description', ''))[:desc_len]}")


async def screen(search: dict, offers: list) -> list:
    """ИИ читает объявления пачками → [(offer, score, why, flags)] отсортировано по score."""
    sp = search["spec"]
    taste = ""
    if search["likes"]:
        taste += "Пользователю ПОНРАВИЛИСЬ: " + " | ".join(search["likes"][-8:]) + "\n"
    if search["dislikes"]:
        taste += "Пользователь ОТКЛОНИЛ (ищи не такое): " + " | ".join(search["dislikes"][-8:]) + "\n"
    system = SCREEN_PROMPT.format(wish=search["wish"], req="; ".join(sp.get("requirements") or []) or "—",
                                  nice="; ".join(sp.get("nice_to_have") or []) or "—",
                                  rej="; ".join(sp.get("reject_if") or []) or "—", taste=taste)
    by_id = {o["id"]: o for o in offers}
    res = []
    for i in range(0, len(offers), 8):
        batch = offers[i:i + 8]
        try:
            raw = await _llm([{"role": "system", "content": system},
                              {"role": "user", "content": "\n\n".join(_offer_brief(o) for o in batch)}],
                             max_tokens=1800, model=SCREEN_MODEL)
            for v in _json(raw):
                o = by_id.get(int(v.get("id", 0)))
                if o:
                    res.append((o, int(v.get("score", 0)), str(v.get("why", "")), str(v.get("flags", ""))))
        except Exception as e:
            log.warning("screen batch: %s", e)
    res.sort(key=lambda r: -r[1])
    return res


# ---------- Telegram ----------
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
_locks: dict = {}


def _lock(sid):
    return _locks.setdefault(sid, asyncio.Lock())


def _median(sid):
    vals = [r[0] for r in q("SELECT price FROM prices WHERE search_id=? AND price>0", (sid,))]
    return int(statistics.median(vals)) if len(vals) >= 5 else None


def _money(v):
    return f"{v:,}".replace(",", " ") + " ₸"


def card_kb(sid, oid, url):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👍", callback_data=f"like:{sid}:{oid}"),
         InlineKeyboardButton(text="👎 Не то", callback_data=f"dis:{sid}:{oid}"),
         InlineKeyboardButton(text="⭐", callback_data=f"fav:{oid}")],
        [InlineKeyboardButton(text="🔍 Подробно", callback_data=f"deep:{sid}:{oid}"),
         InlineKeyboardButton(text="Открыть на OLX", url=url or "https://www.olx.kz")],
    ])


def _card_text(sid, o, score, why, flags):
    price, label = _price(o)
    med = _median(sid)
    rel = ""
    if med and price:
        d = round((price - med) / med * 100)
        rel = f" ({'дешевле' if d < 0 else 'дороже'} медианы на {abs(d)}%)" if abs(d) >= 5 else " (около медианы)"
    stars = "🟢" if score >= 8 else "🟡"
    t = (f"{stars} <b>{html.escape(o.get('title', '')[:120])}</b>\n💰 {html.escape(label)}{rel}\n"
         f"📍 {html.escape(_city(o))}{' · компания' if o.get('business') else ''}\n"
         f"🤖 {score}/10 — {html.escape(why[:300])}")
    if flags:
        t += f"\n⚠️ {html.escape(flags[:250])}"
    return t + f"\n<a href=\"{html.escape(o.get('url', ''))}\">Объявление</a> · поиск #{sid}"


async def send_card(chat, sid, o, score, why, flags):
    q("INSERT OR REPLACE INTO offers(id, data) VALUES (?,?)", (o["id"], json.dumps(o, ensure_ascii=False)))
    kb, text, photo = card_kb(sid, o["id"], o.get("url")), _card_text(sid, o, score, why, flags), _photo(o)
    try:
        m = await (bot.send_photo(chat, photo, caption=text[:1024], reply_markup=kb) if photo
                   else bot.send_message(chat, text, reply_markup=kb, disable_web_page_preview=True))
    except Exception:
        try:
            m = await bot.send_message(chat, text, reply_markup=kb, disable_web_page_preview=True)
        except Exception as e:
            log.warning("send to %s failed: %s", chat, e)
            return
    q("INSERT OR REPLACE INTO sent VALUES (?,?,?,?,?)", (chat, m.message_id, sid, o["id"], why))
    await asyncio.sleep(0.4)


async def run_search(sid: int, first: bool = False, notify_empty_chat=None) -> int:
    """Новые объявления по поиску → ИИ → карточки. Возвращает число отправленных."""
    s = get_search(sid)
    if not s or (not s["active"] and not first and not notify_empty_chat):
        return 0
    async with _lock(sid):
        offers = await olx_fetch(s["spec"], FIRST_SCAN if first else 100)
        with closing(_db()) as c, c:
            seen = {r[0] for r in c.execute("SELECT offer_id FROM seen WHERE search_id=?", (sid,))}
            for o in offers:
                p, _ = _price(o)
                c.execute("INSERT OR IGNORE INTO prices VALUES (?,?,?)", (sid, o["id"], p or 0))
        new = [o for o in offers if o["id"] not in seen]
        cand = prefilter(s["spec"], new)
        log.info("поиск #%s: %s объявл., новых %s, после отсева %s", sid, len(offers), len(new), len(cand))
        if first:
            await bot.send_message(s["chat_id"], f"📥 Нашёл {len(offers)} объявлений, явный мусор отсеял — "
                                                 f"ИИ читает {len(cand)} описаний целиком…")
        res = await screen(s, cand) if cand else []
        with closing(_db()) as c, c:
            c.executemany("INSERT OR IGNORE INTO seen VALUES (?,?)", [(sid, o["id"]) for o in new])
        good = [r for r in res if r[1] >= MIN_SCORE][:10 if first else 15]
        for r in good:
            await send_card(s["chat_id"], sid, *r)
        if not good and (first or notify_empty_chat):
            await bot.send_message(notify_empty_chat or s["chat_id"],
                                   f"Поиск #{sid}: пока ничего действительно подходящего. "
                                   f"Слежу за новыми объявлениями каждые {CHECK_EVERY_MIN} мин."
                                   + ("\nЕсли хочешь — смягчи условия ответом на сообщение поиска." if first else ""))
        elif first:
            await bot.send_message(s["chat_id"], f"Это лучшее из текущих ({len(good)} из {len(offers)}). "
                                                 f"Дальше пришлю только новые подходящие. /my — мои поиски")
        return len(good)


def search_text(s) -> str:
    sp = s["spec"]
    price = ""
    if sp.get("price_from") or sp.get("price_to"):
        price = f"{_money(sp['price_from']) if sp.get('price_from') else '0'} – " \
                f"{_money(sp['price_to']) if sp.get('price_to') else '∞'}"
    lines = [f"🔎 <b>Поиск #{s['id']}</b>{'' if s['active'] else ' (на паузе)'}",
             f"💬 {html.escape(sp.get('summary') or s['wish'])}",
             f"OLX: «{html.escape(sp.get('query') or '')}»"
             + (f" · {html.escape(sp['city'])}" if sp.get("city") else " · весь Казахстан")
             + (f" · {price}" if price else "")]
    for title, key in (("✅ Обязательно", "requirements"), ("➕ Желательно", "nice_to_have"),
                       ("⛔ Не нужно", "reject_if")):
        if sp.get(key):
            lines.append(f"{title}: " + html.escape("; ".join(sp[key])))
    lines.append("\n<i>Ответь на это сообщение, чтобы уточнить поиск.</i>")
    return "\n".join(lines)


def search_kb(s):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Проверить", callback_data=f"run:{s['id']}"),
        InlineKeyboardButton(text="⏸ Пауза" if s["active"] else "▶️ Включить", callback_data=f"tog:{s['id']}"),
        InlineKeyboardButton(text="🗑", callback_data=f"del:{s['id']}"),
    ]])


async def show_search(chat, s):
    m = await bot.send_message(chat, search_text(s), reply_markup=search_kb(s))
    q("INSERT OR REPLACE INTO search_msgs VALUES (?,?,?)", (chat, m.message_id, s["id"]))


HELP = ("Я ищу на OLX.kz за тебя 🕵️\n\nПросто напиши, что нужно, со всеми деталями — например:\n"
        "• «iPhone 13 или 14 в Астане до 220 тыс, рабочий, экран родной, батарея от 85%»\n"
        "• «Camry 50 или 55, 2015–2018, автомат, не битая, без окраса, до 9 млн, Алматы»\n"
        "• «1-комнатная в аренду в Астане, Есиль, от хозяина, до 200 тыс, с мебелью, можно с котом»\n\n"
        "Я прочитаю описания ВСЕХ объявлений, выкину то, что не подходит («не работает», «на запчасти», "
        "«посуточно», перекупов — если скажешь), пришлю лучшее и буду следить за новыми.\n\n"
        "👍/👎 под карточками — я подстраиваюсь под твой вкус.\n🔍 Подробно — разбор объявления, цена против рынка, "
        "что спросить у продавца.\n↩️ Ответь на карточку вопросом — отвечу по объявлению.\n\n"
        "/my — мои поиски · /now — проверить всё сейчас · /fav — избранное")


@dp.message(Command("start"))
async def cmd_start(m: Message, command: CommandObject):
    if not allowed(m.chat.id):
        if INVITE_CODE and (command.args or "").strip() == INVITE_CODE:
            q("INSERT OR IGNORE INTO users VALUES (?,?)", (m.chat.id, m.from_user.full_name))
        else:
            await m.answer("🔒 Бот закрытый. Пришли инвайт-код.")
            return
    await m.answer(HELP)


@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(HELP)


@dp.message(Command("invite"))
async def cmd_invite(m: Message):
    if m.chat.id not in ADMIN_IDS:
        return
    me = await bot.get_me()
    await m.answer(f"Ссылка для друга: https://t.me/{me.username}?start={INVITE_CODE}" if INVITE_CODE
                   else "Инвайт-код не задан (секрет OLX_INVITE_CODE) — бот открыт для всех.")


@dp.message(Command("my"))
async def cmd_my(m: Message):
    if not allowed(m.chat.id):
        return
    rows = q("SELECT id FROM searches WHERE chat_id=? ORDER BY id", (m.chat.id,))
    if not rows:
        await m.answer("Поисков пока нет. Просто напиши, что ищешь.")
    for (sid,) in rows:
        await show_search(m.chat.id, get_search(sid))


@dp.message(Command("now"))
async def cmd_now(m: Message):
    if not allowed(m.chat.id):
        return
    rows = q("SELECT id FROM searches WHERE chat_id=? AND active=1", (m.chat.id,))
    if not rows:
        await m.answer("Нет активных поисков.")
        return
    await m.answer("Проверяю новые объявления…")
    total = 0
    for (sid,) in rows:
        total += await run_search(sid)
    if not total:
        await m.answer("Новых подходящих пока нет.")


@dp.message(Command("fav"))
async def cmd_fav(m: Message):
    rows = q("SELECT o.data FROM fav f JOIN offers o ON o.id=f.offer_id WHERE f.chat_id=?", (m.chat.id,))
    if not rows:
        await m.answer("Избранное пусто. Жми ⭐ под карточкой.")
        return
    lines = []
    for (d,) in rows[-30:]:
        o = json.loads(d)
        lines.append(f"• <a href=\"{html.escape(o['url'])}\">{html.escape(o['title'][:70])}</a> — "
                     f"{html.escape(_price(o)[1])}")
    await m.answer("⭐ Избранное:\n" + "\n".join(lines), disable_web_page_preview=True)


def _offer_from_db(oid):
    r = q("SELECT data FROM offers WHERE id=?", (oid,), True)
    return json.loads(r[0]) if r else None


@dp.callback_query(F.data.startswith(("like:", "dis:")))
async def cb_taste(cq: CallbackQuery):
    kind, sid, oid = cq.data.split(":")
    o, s = _offer_from_db(int(oid)), get_search(int(sid))
    if not (o and s):
        await cq.answer("Не нашёл")
        return
    col = "likes" if kind == "like" else "dislikes"
    lst = s[col] + [f"{o['title'][:80]} ({_price(o)[1]})"]
    q(f"UPDATE searches SET {col}=? WHERE id=?", (json.dumps(lst[-20:], ensure_ascii=False), s["id"]))
    if kind == "like":
        await cq.answer("Запомнил: такое нравится 👍")
    else:
        await cq.answer("Понял, такое больше не предлагаю")
        try:
            await cq.message.delete()
        except Exception:
            pass


@dp.callback_query(F.data.startswith("fav:"))
async def cb_fav(cq: CallbackQuery):
    q("INSERT OR IGNORE INTO fav VALUES (?,?)", (cq.message.chat.id, int(cq.data.split(":")[1])))
    await cq.answer("В избранном ⭐ (/fav)")


DEEP_PROMPT = """Ты опытный и честный помощник покупателя на OLX.kz. Пиши по-русски, коротко, для Telegram
(без таблиц и markdown-заголовков; можно • и эмодзи). Не выдумывай фактов, которых нет в объявлении."""


async def _answer(chat_msg: Message, sid, o, question: str):
    s = get_search(sid) if sid else None
    med = _median(sid) if sid else None
    ctx = (f"Пользователь ищет: {s['wish'] if s else '—'}\n"
           f"Требования: {json.dumps(s['spec'], ensure_ascii=False) if s else '—'}\n"
           f"Медианная цена похожих объявлений: {_money(med) if med else 'нет данных'}\n\n"
           f"Объявление:\n{_offer_brief(o, 4000)}\nСсылка: {o.get('url')}\n"
           f"Опубликовано: {o.get('created_time', '')[:10]}")
    ans = await _llm([{"role": "system", "content": DEEP_PROMPT},
                      {"role": "user", "content": f"{ctx}\n\nЗадача:\n{question}"}], max_tokens=2000, effort="medium")
    for i in range(0, len(ans), 3900):
        await chat_msg.answer(html.escape(ans[i:i + 3900]), disable_web_page_preview=True)


@dp.callback_query(F.data.startswith("deep:"))
async def cb_deep(cq: CallbackQuery):
    _, sid, oid = cq.data.split(":")
    o = _offer_from_db(int(oid))
    if not o:
        await cq.answer("Не нашёл объявление")
        return
    await cq.answer("Разбираю…")
    await cq.message.answer("⏳ Читаю объявление целиком…")
    try:
        await _answer(cq.message, int(sid), o,
                      "1. Соответствие каждому требованию: ✅ есть / ❌ нет / ❓ не указано.\n"
                      "2. Красные флаги и что насторожило в тексте.\n"
                      "3. Цена: дёшево/нормально/дорого против медианы и почему; за сколько реально торговаться.\n"
                      "4. 5–7 вопросов продавцу перед встречей.\n"
                      "5. Что проверить при осмотре (конкретно для этого товара).\n"
                      "6. Итог одной строкой: брать / смотреть / пропустить.")
    except Exception as e:
        log.exception("deep")
        await cq.message.answer(f"Ошибка ИИ: {html.escape(str(e)[:200])}")


@dp.callback_query(F.data.startswith(("run:", "tog:", "del:")))
async def cb_search(cq: CallbackQuery):
    kind, sid = cq.data.split(":")
    s = get_search(int(sid))
    if not s or s["chat_id"] != cq.message.chat.id:
        await cq.answer("Нет такого поиска")
        return
    if kind == "del":
        q("DELETE FROM searches WHERE id=?", (s["id"],))
        q("DELETE FROM seen WHERE search_id=?", (s["id"],))
        q("DELETE FROM prices WHERE search_id=?", (s["id"],))
        await cq.answer("Удалил")
        await cq.message.edit_text(f"🗑 Поиск #{s['id']} удалён.")
    elif kind == "tog":
        q("UPDATE searches SET active=? WHERE id=?", (0 if s["active"] else 1, s["id"]))
        s = get_search(s["id"])
        await cq.answer("Включил" if s["active"] else "На паузе")
        await cq.message.edit_text(search_text(s), reply_markup=search_kb(s))
    else:
        await cq.answer("Проверяю…")
        await run_search(s["id"], notify_empty_chat=cq.message.chat.id)


async def new_search(m: Message, wish: str):
    n = q("SELECT COUNT(*) FROM searches WHERE chat_id=?", (m.chat.id,), True)[0]
    if n >= MAX_SEARCHES and m.chat.id not in ADMIN_IDS:
        await m.answer(f"У тебя уже {n} поисков (максимум {MAX_SEARCHES}). Удали лишний в /my.")
        return
    await m.answer("🧠 Разбираю, что тебе нужно…")
    try:
        spec = await parse_wish(wish)
    except Exception as e:
        log.exception("parse")
        await m.answer(f"Не получилось разобрать запрос: {html.escape(str(e)[:200])}")
        return
    with closing(_db()) as c, c:
        sid = c.execute("INSERT INTO searches(chat_id, wish, spec, created) VALUES (?,?,?,?)",
                        (m.chat.id, wish, json.dumps(spec, ensure_ascii=False), time.time())).lastrowid
    await show_search(m.chat.id, get_search(sid))
    await run_search(sid, first=True)


async def refine_search(m: Message, sid: int):
    s = get_search(sid)
    await m.answer("🧠 Уточняю поиск…")
    try:
        spec = await parse_wish(m.text, s["spec"])
    except Exception as e:
        await m.answer(f"Не получилось: {html.escape(str(e)[:200])}")
        return
    wish = f"{s['wish']}. Уточнение: {m.text}"
    q("UPDATE searches SET spec=?, wish=? WHERE id=?", (json.dumps(spec, ensure_ascii=False), wish, sid))
    q("DELETE FROM seen WHERE search_id=?", (sid,))  # пересмотреть всё по новым условиям
    await show_search(m.chat.id, get_search(sid))
    await run_search(sid, first=True)


@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    if not allowed(m.chat.id):
        if INVITE_CODE and m.text.strip() == INVITE_CODE:
            q("INSERT OR IGNORE INTO users VALUES (?,?)", (m.chat.id, m.from_user.full_name))
            await m.answer("✅ Доступ открыт!\n\n" + HELP)
        else:
            await m.answer("🔒 Бот закрытый. Пришли инвайт-код.")
        return
    if m.reply_to_message:
        mid = m.reply_to_message.message_id
        r = q("SELECT search_id FROM search_msgs WHERE chat_id=? AND msg_id=?", (m.chat.id, mid), True)
        if r and get_search(r[0]):
            await refine_search(m, r[0])
            return
        r = q("SELECT search_id, offer_id FROM sent WHERE chat_id=? AND msg_id=?", (m.chat.id, mid), True)
        o = _offer_from_db(r[1]) if r else None
        if o:
            await m.answer("⏳ Думаю…")
            try:
                await _answer(m, r[0], o, m.text)
            except Exception as e:
                await m.answer(f"Ошибка ИИ: {html.escape(str(e)[:200])}")
            return
    if len(m.text.strip()) < 3:
        await m.answer(HELP)
        return
    await new_search(m, m.text.strip())


async def _scheduler():
    await asyncio.sleep(30)
    while True:
        for (sid,) in q("SELECT id FROM searches WHERE active=1"):
            try:
                await run_search(sid)
            except Exception:
                log.exception("поиск #%s упал", sid)
            await asyncio.sleep(3)
        await asyncio.sleep(CHECK_EVERY_MIN * 60)


_sched_task = None


async def main():
    global _sched_task
    _db().close()
    if _sched_task is None or _sched_task.done():
        _sched_task = asyncio.create_task(_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
