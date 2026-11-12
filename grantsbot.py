"""Радар возможностей — общий бот: каждый подписчик описывает себя (язык, возраст, страна, что ищет, темы),
и бот присылает ему только то, что подходит именно ему. Языки: русский, español, English.
Запускается лаунчером main.py (строка bot_grants в bots.list).

Как работает поиск (каждые CHECK_EVERY_MIN минут):
1. Сбор: ~40 RSS сайтов-агрегаторов (полный текст), ~50 лент Google News (EN/RU/KZ/ES), Telegram-каналы.
   Записи старше MAX_AGE_DAYS не берём. Статус каждого источника виден в /sources.
2. Предфильтр без ИИ: оставляем только то, что похоже на возможность (конференция, грант, стажировка,
   стипендия, обмен…), лучшие сначала.
3. Для кандидатов бот открывает страницу и ищет ссылку на официальный сайт.
4. ИИ (Groq, отдельная быстрая модель GR_AI_MODEL — не тратит лимит учебных ботов) один раз извлекает факты:
   вид, онлайн/очно, что оплачивают, взнос, возраст, страны, экзамены, дедлайн, темы, название на ru/es.
   Если ИИ не ответил — находка НЕ теряется, проверится в следующий раз.
5. Для каждого подписчика факты сравниваются с его профилем — каждому приходит своё, на его языке.
6. Анти-дубли: одна программа с разных сайтов — одно сообщение.

Команды: /start, /profile, /now, /list [слово], /sources, /stop.
ИИ-консультант: кнопка «📄 Документы и требования»; reply на уведомление с вопросом; ссылка; вопрос текстом.
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
try:
    from ai import _groq_request
except Exception:
    _groq_request = None

# Настройки читаются один раз при загрузке (лаунчер меняет os.environ для каждого бота)
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
CHECK_EVERY_MIN = int(os.getenv("CHECK_EVERY_MIN", "10"))
DB_PATH = os.getenv("DB_PATH", "bot.db")
MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "21"))    # записи старше — не смотрим
AI_PER_ROUND = int(os.getenv("AI_PER_ROUND", "36"))    # сколько кандидатов ИИ разбирает за один проход
AI_BATCH = int(os.getenv("AI_BATCH", "6"))
AI_PAUSE = int(os.getenv("AI_PAUSE", "20"))            # пауза между пачками (лимит токенов в минуту у Groq)
GR_AI_MODEL = os.getenv("GR_AI_MODEL", "openai/gpt-oss-20b").strip()  # отдельная модель → отдельный лимит
FIRST_RUN_LIMIT = 8

log = logging.getLogger("grants")


def today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=5)).date()  # Казахстан, UTC+5


# ======================= ИСТОЧНИКИ =======================
# Нерабочая лента просто пропускается; что работает — видно в /sources (✅/❌).
RSS_FEEDS = {
    # общие агрегаторы возможностей
    "Opportunity Desk": "https://opportunitydesk.org/feed/",
    "Opportunity Desk: conferences": "https://opportunitydesk.org/category/conferences/feed/",
    "Opportunity Desk: fellowships": "https://opportunitydesk.org/category/fellowships/feed/",
    "Opportunity Desk: grants": "https://opportunitydesk.org/category/grants/feed/",
    "Opportunity Desk: training": "https://opportunitydesk.org/category/training/feed/",
    "Opportunity Desk: competitions": "https://opportunitydesk.org/category/competitions/feed/",
    "Opportunity Desk: scholarships": "https://opportunitydesk.org/category/scholarships/feed/",
    "Opportunities Corners": "https://opportunitiescorners.com/feed/",
    "Opportunities for Youth": "https://opportunitiesforyouth.org/feed/",
    "Opportunities Radar": "https://opportunitiesradar.com/feed/",
    "Opportunities for Africans": "https://www.opportunitiesforafricans.com/feed/",
    "Opportunity Portal": "https://opportunityportal.info/feed/",
    "Opportunities Circle": "https://www.opportunitiescircle.com/feed/",
    "Greatyop": "https://greatyop.com/feed/",
    "Global South Opportunities": "https://www.globalsouthopportunities.com/feed/",
    "Youth Hub Africa": "https://www.youthhubafrica.org/feed/",
    "Youthop": "https://www.youthop.com/feed",
    "Youth Opportunities Hub": "https://youthopportunitieshub.com/feed/",
    "After School Africa": "https://www.afterschoolafrica.com/feed/",
    "TrueScho Opportunities": "https://fullopportunities.truescho.com/feed/",
    "ProFellow (fellowships)": "https://www.profellow.com/feed/",
    "DiploFoundation": "https://www.diplomacy.edu/feed/",
    # гранты
    "FundsForNGOs": "https://www2.fundsforngos.org/feed/",
    "FundsForNGOs (main)": "https://www.fundsforngos.org/feed/",
    "Funds for Individuals": "https://fundsforindividuals.fundsforngos.org/feed/",
    # учёба и стипендии (придут только тем, кто выбрал «учёба»)
    "Scholars4Dev": "https://www.scholars4dev.com/feed/",
    "Scholarship Positions": "https://scholarship-positions.com/feed/",
    "Scholarships Corner": "https://scholarshipscorner.website/feed/",
    "Scholarship Union": "https://scholarshipunion.com/feed/",
    "TrueScho Scholars": "https://scholars.truescho.com/feed/",
    "Scholarship Roar": "https://scholarshiproar.com/feed/",
    "Scholarships Hall": "https://scholarshipshall.com/feed/",
    "Scholarships Ads": "https://www.scholarshipsads.com/feed/",
    "Scholarship Region": "https://www.scholarshipregion.com/feed/",
    "World Scholarship Forum": "https://worldscholarshipforum.com/feed/",
    "Study Abroad Nations": "https://studyabroadnations.com/feed/",
    # Европа
    "Mladiinfo (Europe)": "https://www.mladiinfo.eu/feed/",
    "Eurodesk": "https://www.eurodesk.eu/feed/",
    # на русском
    "Грантист": "https://grantist.com/feed/",
}


def _gn(q, lang="en", days=3):
    """Лента Google News по запросу. days=0 — без ограничения по дате."""
    loc = {"en": "hl=en-US&gl=US&ceid=US:en", "ru": "hl=ru&gl=KZ&ceid=KZ:ru", "kk": "hl=kk&gl=KZ&ceid=KZ:kk",
           "es": "hl=es&gl=ES&ceid=ES:es"}[lang]
    q = f"{q} when:{days}d" if days else q
    return f"https://news.google.com/rss/search?q={quote(q)}&{loc}"


_NEWS_QUERIES = [
    # EN — профессионалы и лидеры
    ("lawyers conference", '"fully funded" conference OR seminar lawyers OR judges OR "legal professionals"', "en"),
    ("study visit", '"study visit" OR "study tour" "travel and accommodation" professionals', "en"),
    ("leadership program", '"fully funded" leadership program executives OR "senior professionals"', "en"),
    ("rule of law training", '"rule of law" OR "human rights" training "travel and accommodation covered"', "en"),
    ("anti-corruption", 'anti-corruption OR integrity seminar "fully funded" professionals', "en"),
    ("IVLP / exchange", 'IVLP OR "Open World" OR "professional exchange" lawyers OR "civil society leaders"', "en"),
    ("OSCE / UN / CoE", 'OSCE OR UNDP OR "Council of Europe" seminar Kazakhstan OR "Central Asia" lawyers', "en"),
    ("arbitration mediation", 'arbitration OR mediation conference "travel grant" OR "fully funded"', "en"),
    ("cultural exchange", '"cultural exchange" program professionals OR leaders "fully funded"', "en"),
    ("christian leaders", 'christian OR church OR pastors leadership conference "travel and accommodation" OR "fully funded"', "en"),
    ("family & fatherhood", 'family OR fatherhood OR parenting conference OR seminar "fully funded" OR "travel covered"', "en"),
    ("language course", '"language course" OR "language program" professionals "fully funded" short-term', "en"),
    ("volunteering", 'volunteer program OR volunteering short-term "travel and accommodation" OR "fully funded" adults', "en"),
    # EN — широкие
    ("fully funded conference", '"fully funded" conference 2026 OR 2027', "en"),
    ("fully funded training", '"fully funded" training program OR workshop "apply"', "en"),
    ("fully funded exchange", '"fully funded" exchange program 2026 OR 2027', "en"),
    ("call for applications", '"call for applications" "fully funded"', "en"),
    ("call for participants", '"call for participants" "costs covered" OR "travel costs"', "en"),
    ("travel grant", '"travel grant" conference 2026 OR 2027', "en"),
    ("fully funded fellowship", '"fully funded" fellowship 2026 OR 2027', "en"),
    ("fully funded internship", '"fully funded" internship 2026 OR 2027', "en"),
    ("fully funded scholarship", '"fully funded" scholarship 2026 OR 2027 apply', "en"),
    ("summer school", '"fully funded" "summer school" 2026 OR 2027', "en"),
    ("grants for individuals", 'grant for individuals OR "individual grant" "apply now"', "en"),
    ("entrepreneurs", 'entrepreneurs OR startups accelerator OR program "fully funded" OR "all expenses"', "en"),
    ("investment / business", 'business delegation OR "trade mission" OR "investment forum" "travel covered" OR "fully funded"', "en"),
    ("tourism", 'tourism OR hospitality conference OR training "fully funded" OR "travel grant"', "en"),
    ("Erasmus+ training", 'Erasmus+ "training course" OR "seminar" participants "travel costs" reimbursed', "en"),
    ("journalists / media", 'journalists OR media fellowship OR training "fully funded" OR "travel and accommodation"', "en"),
    ("women leaders", 'women leaders program OR fellowship "fully funded"', "en"),
    # RU / KZ
    ("RU: юристы", 'семинар OR стажировка для юристов перелет и проживание оплачиваются', "ru"),
    ("RU: руководители", 'программа для руководителей обмен опытом перелет проживание за счет', "ru"),
    ("RU: Казахстан", 'Казахстан стажировка за рубежом юристы OR судьи OR адвокаты', "ru"),
    ("RU: христиане", 'христианская конференция OR семинар для служителей пасторов перелет проживание', "ru"),
    ("RU: семья", 'семейные ценности OR отцовство OR воспитание детей форум OR семинар перелет проживание', "ru"),
    ("RU: оплачиваемая конференция", 'полностью оплачиваемая конференция OR форум OR семинар', "ru"),
    ("RU: стажировка", 'стажировка за рубежом полностью оплачивается OR "за счет принимающей стороны"', "ru"),
    ("RU: обмен", 'прием заявок программа обмена перелет проживание оплачиваются', "ru"),
    ("RU: грант", 'грант прием заявок 2026 OR 2027 Казахстан OR СНГ', "ru"),
    ("RU: стипендия", 'стипендия полностью покрывает обучение прием заявок', "ru"),
    ("KZ: заңгерлер", 'заңгерлер семинар шетелде тегін', "kk"),
    ("KZ: грант", 'шетелде оқу гранты OR тағылымдама тегін', "kk"),
    # ES
    ("ES: beca financiada", 'beca "totalmente financiada" OR "100% financiada"', "es"),
    ("ES: seminario internacional", 'convocatoria seminario OR congreso internacional "gastos de viaje" alojamiento', "es"),
    ("ES: intercambio", 'programa de intercambio profesional financiado convocatoria', "es"),
    ("ES: Erasmus+", 'Erasmus+ "curso de formación" OR "intercambio juvenil" convocatoria gastos cubiertos', "es"),
    ("ES: profesionales extranjero", 'becas para profesionales en el extranjero convocatoria abierta', "es"),
    ("ES: bolsa de viaje", 'congreso internacional "bolsa de viaje" convocatoria', "es"),
    ("ES: abogados", 'abogados OR juristas programa internacional beca OR financiado convocatoria', "es"),
    ("ES: prácticas", 'prácticas internacionales remuneradas convocatoria 2026 OR 2027', "es"),
    ("ES: emprendedores", 'emprendedores programa internacional gastos cubiertos convocatoria', "es"),
]
NEWS_FEEDS = {f"Google News {name}": _gn(q, lang) for name, q, lang in _NEWS_QUERIES}

# Публичные Telegram-каналы (t.me/s/<канал>, без входа). Несуществующий канал просто пропускается.
TG_CHANNELS = [
    "we_project", "joinyouthuz", "opportunityportalabroad", "grantist", "BrightScholarship", "scholarshipsee",
    "scholarshipregion", "studyqacom", "nextscholargrant", "studyabroadl", "EngIishSpace",
]

# ======================= ТЕМЫ И ВИДЫ =======================
TOPICS = {  # id: ({ru, es, en}, ключевые слова EN/RU/KZ/ES)
    "leadership": ({"ru": "лидерство и управление", "es": "liderazgo y gestión", "en": "leadership & management"},
                   ["leadership", "leader", "management", "executive", "governance", "public administration", "лидер",
                    "руковод", "управлен", "көшбасшы", "liderazgo", "líder", "gestión", "gobernanza"]),
    "law": ({"ru": "право и юристы", "es": "derecho", "en": "law"},
            ["law ", "laws", "legal", "justice", "court", "lawyer", "judge", "judicia", "attorney", "advocate",
             "bar association", "arbitration", "mediation", "право", "правов", "юрид", "юрист", "адвокат", "судь",
             "судеб", "арбитраж", "медиац", "құқық", "заңгер", "derecho", "juríd", "abogad", "justicia", "juez",
             "jueces", "arbitraje", "mediación"]),
    "human_rights": ({"ru": "права человека", "es": "derechos humanos", "en": "human rights"},
                     ["human rights", "rule of law", "democracy", "права человека", "демократ", "верховенств",
                      "derechos humanos", "democracia", "estado de derecho"]),
    "anticorruption": ({"ru": "антикоррупция", "es": "anticorrupción", "en": "anti-corruption"},
                       ["anti-corruption", "anticorruption", "integrity", "коррупц", "corrupción", "integridad"]),
    "business": ({"ru": "экономика, бизнес, инвестиции", "es": "economía, negocios, inversión",
                  "en": "economy, business, investment"},
                 ["econom", "business", "entrepreneur", "invest", "finance", "trade", "startup", "start-up",
                  "accelerator", "экономи", "бизнес", "инвест", "предприним", "торгов", "стартап", "economía",
                  "empresa", "emprend", "inversi", "finanzas", "comercio"]),
    "diplomacy": ({"ru": "госуправление и дипломатия", "es": "diplomacia y política pública",
                   "en": "diplomacy & public policy"},
                  ["diplomac", "diplomat", "international relations", "public policy", "civil serv", "дипломат",
                   "госслуж", "государственн", "diplomacia", "relaciones internacionales", "política pública"]),
    "tourism": ({"ru": "туризм", "es": "turismo", "en": "tourism"},
                ["tourism", "hospitality", "туризм", "turismo", "hostelería"]),
    "culture": ({"ru": "культура и культурные обмены", "es": "cultura e intercambio cultural",
                 "en": "culture & cultural exchange"},
                ["cultural exchange", "intercultural", "cross-cultural", "culture", "arts ", "artist", "культур",
                 "межкультур", "мәдени", "искусств", "cultura", "arte ", "artista"]),
    "religion": ({"ru": "христианство и вера", "es": "cristianismo y fe", "en": "christianity & faith"},
                 ["christian", "church", "protestant", "evangelical", "pastor", "bible", "biblical", "theolog",
                  "faith-based", "faith leaders", "religious freedom", "interfaith", "spiritual", "христиан", "церк",
                  "протестант", "евангел", "пастор", "библ", "богослов", "духовн", "служител", "cristian", "iglesia",
                  "evangélic", "religios"]),
    "family": ({"ru": "семья, дети, отцовство", "es": "familia, crianza, paternidad", "en": "family & parenting"},
               ["family", "marriage", "parenting", "parents", "upbringing", "fatherhood", "fathers", "husband",
                "семейн", "семья", "семьи", "брак", "отбасы", "воспитан", "родител", "отцовств", "отец", "отцы",
                "отцов", "әке", "familia", "crianza", "paternidad"]),
    "languages": ({"ru": "языки", "es": "idiomas", "en": "languages"},
                  ["language course", "language program", "linguistic", "translation", "языков", "лингв", "перевод",
                   "тіл", "idioma", "lingüíst", "traducción"]),
    "volunteering": ({"ru": "волонтёрство", "es": "voluntariado", "en": "volunteering"},
                     ["volunteer", "voluntar", "волонтер", "волонтёр", "доброволь", "ерікті"]),
    "education": ({"ru": "образование и наука", "es": "educación y ciencia", "en": "education & science"},
                  ["education", "teacher", "research", "science", "academic", "образован", "учител", "педагог",
                   "наук", "исследован", "educación", "docente", "profesor", "investigación", "ciencia"]),
    "tech": ({"ru": "технологии и ИИ", "es": "tecnología e IA", "en": "technology & AI"},
             ["technology", "digital", "artificial intelligence", "data science", "cyber", "tech ", "технолог",
              "цифров", "искусственн", "tecnolog", "inteligencia artificial"]),
    "health": ({"ru": "здоровье и медицина", "es": "salud y medicina", "en": "health & medicine"},
               ["health", "medical", "medicine", "здоров", "медицин", "salud", "médic"]),
    "environment": ({"ru": "экология и климат", "es": "medio ambiente y clima", "en": "environment & climate"},
                    ["climate", "environment", "sustainab", "renewable", "эколог", "климат", "устойчив", "clima",
                     "medio ambiente", "sostenib"]),
    "media": ({"ru": "журналистика и медиа", "es": "periodismo y medios", "en": "journalism & media"},
              ["journalis", "media ", "журналист", "медиа", "сми ", "periodis", "medios"]),
    "women": ({"ru": "женщины и гендер", "es": "mujeres y género", "en": "women & gender"},
              ["women", "gender", "женщин", "гендер", "mujeres", "género"]),
    "sport": ({"ru": "спорт", "es": "deporte", "en": "sport"}, ["sport", "спорт", "deport"]),
}
ITEM_KINDS = ["conference", "exchange", "internship", "fellowship", "degree", "summer_school", "grant", "business",
              "volunteer", "competition", "job", "other"]
KINDS = {  # что выбирает подписчик: id → ({ru, es, en}, виды находок)
    "events": ({"ru": "конференции, семинары, тренинги", "es": "congresos, seminarios, formaciones",
                "en": "conferences, seminars, trainings"}, {"conference"}),
    "exchange": ({"ru": "обмены и учебные визиты", "es": "intercambios y visitas de estudio",
                  "en": "exchanges & study visits"}, {"exchange"}),
    "internship": ({"ru": "стажировки и fellowship", "es": "prácticas y fellowships",
                    "en": "internships & fellowships"}, {"internship", "fellowship"}),
    "study": ({"ru": "учёба: стипендии, летние школы", "es": "estudios: becas, escuelas de verano",
               "en": "study: scholarships, summer schools"}, {"degree", "summer_school"}),
    "grants": ({"ru": "гранты на проекты и исследования", "es": "subvenciones para proyectos",
                "en": "project & research grants"}, {"grant"}),
    "business": ({"ru": "бизнес, стартапы, инвестиции", "es": "negocios, startups, inversión",
                  "en": "business, startups, investment"}, {"business"}),
    "volunteer": ({"ru": "волонтёрство", "es": "voluntariado", "en": "volunteering"}, {"volunteer"}),
    "competition": ({"ru": "конкурсы и премии", "es": "concursos y premios", "en": "competitions & awards"},
                    {"competition"}),
}
KIND_EMOJI = {"conference": "🎤", "exchange": "🌍", "internship": "💼", "fellowship": "💼", "degree": "🎓",
              "summer_school": "🎓", "grant": "💰", "business": "🚀", "volunteer": "🤝", "competition": "🏆"}
COVERS = {"flights": {"ru": "перелёт", "es": "vuelos", "en": "flights"},
          "accommodation": {"ru": "проживание", "es": "alojamiento", "en": "accommodation"},
          "meals": {"ru": "питание", "es": "comidas", "en": "meals"},
          "visa": {"ru": "виза", "es": "visado", "en": "visa"},
          "insurance": {"ru": "страховка", "es": "seguro", "en": "insurance"},
          "stipend": {"ru": "стипендия/суточные", "es": "dietas/estipendio", "en": "stipend"},
          "tuition": {"ru": "обучение", "es": "matrícula", "en": "tuition"},
          "local_transport": {"ru": "местный транспорт", "es": "transporte local", "en": "local transport"}}
LANG_NAME = {"ru": "Russian", "es": "Spanish", "en": "English"}

# Профиль по умолчанию — для тех, кто был подписан до появления профилей (юрист 50+ из Казахстана)
DEFAULT_PROFILE = {
    "lang": "ru", "age": 50, "country": "Kazakhstan", "student": False,
    "kinds": ["events", "exchange", "internship"],
    "topics": ["leadership", "law", "human_rights", "anticorruption", "business", "diplomacy", "tourism", "culture",
               "religion", "family", "languages", "volunteering"],
    "full_only": True, "has_exam": False, "max_days": 31, "online_ok": False,
    "summary": "Practising lawyer, manager and community leader from Kazakhstan, 50+, not a student, "
               "no IELTS/TOEFL; wants only short in-person programs where travel and accommodation are covered.",
}

# ======================= ТЕКСТЫ ИНТЕРФЕЙСА =======================
TXT = {
    "ru": {
        "ask_about": "Расскажите о себе одним сообщением — буду присылать только то, что подходит вам:\n"
                     "• возраст и страна паспорта\n• кто вы (студент, юрист, предприниматель, учитель…)\n"
                     "• что ищете: конференции и семинары, обмены, стажировки, учёбу и стипендии, гранты, "
                     "бизнес-программы, волонтёрство, конкурсы\n• темы\n"
                     "• только полностью оплачиваемое или частично тоже; есть ли IELTS/TOEFL\n\n"
                     "Пример: «Мне 52, Казахстан, практикующий юрист и руководитель. Ищу короткие конференции и "
                     "учебные визиты по праву и лидерству, только полностью оплаченные, IELTS нет.»",
        "parsing": "⏳ Составляю ваш профиль…",
        "parse_fail": "Не получилось разобрать. Напишите ещё раз чуть подробнее.",
        "card": "👤 Ваш профиль", "age": "Возраст", "country": "Страна", "status": "Статус",
        "student": "студент", "pro": "специалист", "looking": "Ищу", "topics": "Темы", "any": "любые",
        "pay": "Оплата", "full_only": "только полностью оплаченное", "partial_ok": "частично оплаченное тоже",
        "exam": "Сертификат IELTS/TOEFL", "yes": "есть", "no": "нет", "length": "Длительность",
        "days": "до {n} дн.", "online": "Онлайн-события", "ok_btn": "✅ Всё верно", "redo_btn": "✏️ Описать заново",
        "lang_btn": "🌐 Язык",
        "saved": "Готово ✅ Буду присылать то, что подходит вам. Проверяю источники каждые {n} мин.",
        "help": "/now — проверить сейчас\n/list — актуальные находки для вас (/list право — с фильтром)\n"
                "/profile — мой профиль\n/sources — источники\n/stop — отписаться\n\n"
                "📄 Под каждым уведомлением — кнопка «Документы и требования».\n"
                "❓ Ответьте (reply) на уведомление вопросом, пришлите ссылку или просто спросите — "
                "бот поищет в интернете и ответит со ссылками.",
        "stopped": "Отписал. /start — включить снова.",
        "checking": "Проверяю все источники, ИИ читает находки… (1–5 минут)",
        "busy": "Проверка уже идёт — пришлю результат, как закончится.",
        "stats": "Источников ответило: {ok} из {all}. Новых записей: {new}, кандидатов: {cand}, "
                 "ИИ разобрал: {judged}, вам подошло: {mine}.",
        "queue": "Ещё {n} в очереди — ИИ разберёт в следующие проходы.",
        "ai_err": "⚠️ ИИ временно не ответил (лимит Groq?) — непроверенное не потеряется.",
        "no_active": "Актуальных находок для вас пока нет{q}. Попробуйте /now или спросите текстом — "
                     "бот поищет в интернете.",
        "list_head": "Актуальные возможности для вас{q}:",
        "thinking": "⏳ Ищу и читаю страницы…",
        "docs_wait": "⏳ Читаю страницу и официальный сайт, разбираю требования… 10–40 секунд",
        "docs_follow": "Есть вопрос по этой программе? Ответьте (reply) на уведомление о ней.",
        "ai_off": "ИИ не подключён: на хостинге нет переменной GROQ_API_KEY.",
        "deadline": "Дедлайн", "deadline_none": "не указан — уточните на сайте", "left": "осталось {n} дн.",
        "today": "сегодня!", "passed": "прошёл", "official": "Официальный сайт", "covers": "Оплачивают",
        "docs_btn": "📄 Документы и требования", "sources": "Источники (✅ — работает, ❌ — не ответил при "
                                                       "последней проверке, ⏳ — ещё не проверялся):",
        "not_found": "Не нашёл эту запись.", "ai_error": "Ошибка ИИ: {e}",
    },
    "es": {
        "ask_about": "Cuéntame sobre ti en un mensaje — te enviaré solo lo que encaje contigo:\n"
                     "• edad y país del pasaporte\n• quién eres (estudiante, abogado, emprendedor, docente…)\n"
                     "• qué buscas: congresos y seminarios, intercambios, prácticas, estudios y becas, "
                     "subvenciones, programas de negocios, voluntariado, concursos\n• temas\n"
                     "• solo totalmente financiado o también parcial; si tienes IELTS/TOEFL\n\n"
                     "Ejemplo: «Tengo 34 años, soy de España, abogado. Busco congresos, intercambios y prácticas "
                     "sobre derecho y derechos humanos, totalmente financiados. No tengo certificado de inglés.»",
        "parsing": "⏳ Preparando tu perfil…",
        "parse_fail": "No he podido entenderlo. Escríbelo otra vez con un poco más de detalle.",
        "card": "👤 Tu perfil", "age": "Edad", "country": "País", "status": "Situación",
        "student": "estudiante", "pro": "profesional", "looking": "Busco", "topics": "Temas", "any": "cualquiera",
        "pay": "Financiación", "full_only": "solo totalmente financiado", "partial_ok": "también parcial",
        "exam": "Certificado IELTS/TOEFL", "yes": "sí", "no": "no", "length": "Duración",
        "days": "hasta {n} días", "online": "Eventos online", "ok_btn": "✅ Correcto", "redo_btn": "✏️ Describir de nuevo",
        "lang_btn": "🌐 Idioma",
        "saved": "¡Listo ✅! Te enviaré lo que encaje contigo. Reviso las fuentes cada {n} min.",
        "help": "/now — buscar ahora\n/list — oportunidades vigentes para ti (/list derecho — con filtro)\n"
                "/profile — mi perfil\n/sources — fuentes\n/stop — darse de baja\n\n"
                "📄 Bajo cada aviso hay un botón «Documentos y requisitos».\n"
                "❓ Responde (reply) a un aviso con una pregunta, envía un enlace o pregunta lo que quieras — "
                "el bot buscará en internet y responderá con enlaces.",
        "stopped": "Baja hecha. /start — activar de nuevo.",
        "checking": "Revisando todas las fuentes, la IA lee los resultados… (1–5 minutos)",
        "busy": "Ya hay una búsqueda en marcha — te aviso cuando termine.",
        "stats": "Fuentes que respondieron: {ok} de {all}. Entradas nuevas: {new}, candidatas: {cand}, "
                 "analizadas por la IA: {judged}, encajan contigo: {mine}.",
        "queue": "Quedan {n} en cola — la IA las analizará en las próximas pasadas.",
        "ai_err": "⚠️ La IA no respondió (¿límite de Groq?) — lo pendiente no se pierde.",
        "no_active": "Todavía no hay oportunidades vigentes para ti{q}. Prueba /now o pregunta con texto — "
                     "el bot buscará en internet.",
        "list_head": "Oportunidades vigentes para ti{q}:",
        "thinking": "⏳ Buscando y leyendo páginas…",
        "docs_wait": "⏳ Leyendo la página y el sitio oficial, analizando requisitos… 10–40 segundos",
        "docs_follow": "¿Tienes una pregunta sobre este programa? Responde (reply) al aviso.",
        "ai_off": "La IA no está conectada: falta la variable GROQ_API_KEY en el servidor.",
        "deadline": "Plazo", "deadline_none": "no indicado — consúltalo en la web", "left": "quedan {n} días",
        "today": "¡hoy!", "passed": "vencido", "official": "Web oficial", "covers": "Cubren",
        "docs_btn": "📄 Documentos y requisitos", "sources": "Fuentes (✅ — funciona, ❌ — no respondió en la "
                                                          "última revisión, ⏳ — aún sin revisar):",
        "not_found": "No encontré este registro.", "ai_error": "Error de la IA: {e}",
    },
    "en": {
        "ask_about": "Tell me about yourself in one message — I'll send only what fits you:\n"
                     "• age and passport country\n• who you are (student, lawyer, entrepreneur, teacher…)\n"
                     "• what you look for: conferences & seminars, exchanges, internships, study & scholarships, "
                     "grants, business programs, volunteering, competitions\n• topics\n"
                     "• fully funded only or partial too; whether you have IELTS/TOEFL\n\n"
                     "Example: «I'm 34, from Spain, a lawyer. Looking for fully funded conferences, exchanges and "
                     "internships on law and human rights. No English certificate.»",
        "parsing": "⏳ Building your profile…",
        "parse_fail": "Couldn't understand that. Please write again with a bit more detail.",
        "card": "👤 Your profile", "age": "Age", "country": "Country", "status": "Status",
        "student": "student", "pro": "professional", "looking": "Looking for", "topics": "Topics", "any": "any",
        "pay": "Funding", "full_only": "fully funded only", "partial_ok": "partial too",
        "exam": "IELTS/TOEFL certificate", "yes": "yes", "no": "no", "length": "Duration",
        "days": "up to {n} days", "online": "Online events", "ok_btn": "✅ Correct", "redo_btn": "✏️ Describe again",
        "lang_btn": "🌐 Language",
        "saved": "Done ✅ I'll send what fits you. I check the sources every {n} min.",
        "help": "/now — check now\n/list — open opportunities for you (/list law — with a filter)\n"
                "/profile — my profile\n/sources — sources\n/stop — unsubscribe\n\n"
                "📄 Every alert has a «Documents & requirements» button.\n"
                "❓ Reply to an alert with a question, send a link or just ask — "
                "the bot will search the web and answer with links.",
        "stopped": "Unsubscribed. /start — turn on again.",
        "checking": "Checking all sources, the AI reads the results… (1–5 minutes)",
        "busy": "A check is already running — I'll report when it's done.",
        "stats": "Sources responded: {ok} of {all}. New entries: {new}, candidates: {cand}, "
                 "analysed by AI: {judged}, fit you: {mine}.",
        "queue": "{n} more in the queue — the AI will analyse them in the next rounds.",
        "ai_err": "⚠️ The AI didn't respond (Groq limit?) — nothing pending is lost.",
        "no_active": "No open opportunities for you yet{q}. Try /now or ask in text — the bot will search the web.",
        "list_head": "Open opportunities for you{q}:",
        "thinking": "⏳ Searching and reading pages…",
        "docs_wait": "⏳ Reading the page and the official site, analysing requirements… 10–40 seconds",
        "docs_follow": "Have a question about this program? Reply to its alert.",
        "ai_off": "AI is not connected: GROQ_API_KEY is missing on the server.",
        "deadline": "Deadline", "deadline_none": "not stated — check the website", "left": "{n} days left",
        "today": "today!", "passed": "passed", "official": "Official website", "covers": "Covered",
        "docs_btn": "📄 Documents & requirements", "sources": "Sources (✅ — working, ❌ — no response at the "
                                                          "last check, ⏳ — not checked yet):",
        "not_found": "Record not found.", "ai_error": "AI error: {e}",
    },
}


def tr(lang: str, key: str, **kw) -> str:
    s = TXT.get(lang, TXT["ru"]).get(key) or TXT["ru"][key]
    return s.format(**kw) if kw else s


# ======================= ПРЕДФИЛЬТР =======================
def _rx(words):
    """Совпадение по началу слова (работает с основами: «юрист» → «юристов»)."""
    return re.compile(r"(?<![a-zа-яёәіңғүұқөһáéíóúñ0-9])(?:" + "|".join(re.escape(w) for w in words) + ")", re.I)


FORMAT = ["conference", "summit", "forum", "seminar", "workshop", "training", "study visit", "study tour",
          "exchange", "professional development", "capacity building", "short course", "short-term", "visiting",
          "ivlp", "open world", "internship", "fellowship", "symposium", "congress", "convention", "retreat",
          "masterclass", "bootcamp", "delegation", "summer school", "winter school", "scholarship", "grant",
          "award", "prize", "competition", "contest", "accelerator", "incubator", "call for applications",
          "call for participants", "call for proposals", "applications open", "apply now", "volunteer",
          "конференц", "семинар", "форум", "тренинг", "стажировк", "повышени", "квалификац", "обмен", "саммит",
          "симпозиум", "конгресс", "мастер-класс", "курс", "стипенди", "грант", "конкурс", "премия",
          "прием заявок", "приём заявок", "волонтер", "волонтёр", "акселератор", "тағылымдама",
          "beca", "convocatoria", "congreso", "seminario", "jornada", "curso", "intercambio", "prácticas",
          "subvención", "ayuda", "premio", "concurso", "voluntariado", "formación"]
FUNDED = [
    "fully funded", "fully-funded", "fully sponsored", "fully covered", "all expenses", "all-expenses",
    "expenses covered", "expenses paid", "costs covered", "costs will be covered", "all costs", "travel and accommodation",
    "accommodation and travel", "travel, accommodation", "airfare", "air ticket", "flight ticket",
    "flights and accommodation", "round-trip", "return flight", "travel costs", "travel grant", "travel support",
    "travel bursar", "covers travel", "free accommodation", "accommodation will be provided", "per diem", "stipend",
    "funded opportunity", "funded program", "funded conference", "full scholarship",
    "полностью оплач", "полное финансирование", "оплачивают перел", "перелет и проживание", "перелёт и проживание",
    "проезд и проживание", "покрываются расходы", "расходы покрыва", "за счет организатор", "за счёт организатор",
    "за счет принимающ", "за счёт принимающ", "полная стипендия", "толық қаржыландыр", "тегін",
    "totalmente financiad", "100% financiad", "gastos cubiertos", "gastos de viaje", "bolsa de viaje",
    "alojamiento y manutención", "financiación completa", "remunerad", "beca completa",
]
JUNK = ["vacancy", "vacancies", "job opening", "we are hiring", "вакансия", "вакансии", "oferta de empleo"]
FORMAT_RE, FUNDED_RE, JUNK_RE = _rx(FORMAT), _rx(FUNDED), _rx(JUNK)
TOPIC_RES = {tid: _rx(words) for tid, (_, words) in TOPICS.items()}


def kw_topics(t: str) -> list:
    return [tid for tid, rx in TOPIC_RES.items() if rx.search(t)]


def prescore(title: str, text: str, news: bool = False):
    """Быстрая оценка без ИИ: похоже ли на возможность. None — точно нет; иначе dict(score, topics, funded)."""
    tl = " " + title.lower() + " "
    t = " " + f"{title} {text}".lower() + " "
    if JUNK_RE.search(tl):
        return None
    fmt_title, fmt_any = bool(FORMAT_RE.search(tl)), bool(FORMAT_RE.search(t))
    if not fmt_any:
        return None
    funded = bool(FUNDED_RE.search(t)) or (news and "fully" in t)
    if news and not funded:
        return None                                    # новости без слов про оплату — почти всегда мусор
    topics = kw_topics(t)
    score = (3 if funded else 0) + (2 if fmt_title else 1) + min(len(topics), 3) + (1 if "deadline" in t else 0)
    if score < 3:
        return None
    return {"score": score, "kw_topics": topics, "funded": funded}


# ======================= ДАТЫ =======================
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
          "nov": 11, "dec": 12, "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7,
          "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12, "ene": 1, "abr": 4, "ago": 8, "dic": 12,
          "set": 9}


def parse_date(s: str):
    """'15 December 2026', 'December 15, 2026', '15.12.2026', '2026-12-15', '15 декабря 2026',
    '15 de diciembre de 2026' → date."""
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
        m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+|de\s+)?([a-zа-яё]{3,})\.?,?\s+(?:de\s+)?(20\d\d)", s)
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
                         r"при[её]м заявок до|заявки принимаются до|plazo|fecha l[ií]mite)\s*"
                         r"(?:is|:|-|–|de inscripci[oó]n|hasta el)?\s*([^\n;|]{4,40})", re.I)


def find_deadline(text: str):
    for m in DEADLINE_RE.finditer(text or ""):
        d = parse_date(m.group(1))
        if d:
            return d
    return None


def fmt_deadline(iso: str, lang: str = "ru") -> str:
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    left = (d - today()).days
    tail = tr(lang, "today") if left == 0 else (tr(lang, "left", n=left) if left > 0 else tr(lang, "passed"))
    return f"{d:%d.%m.%Y} ({tail})"


# ======================= АНТИ-ДУБЛИ =======================
_GENERIC = set("fully funded apply now call for applications application open the of and in to a an for on at with by "
               "2025 2026 2027 2028 opportunity opportunities deadline program programme international global "
               "полностью оплачиваемая программа на для и в по de la el en y para del los las".split())


def _title_key(title: str) -> str:
    return "t:" + re.sub(r"[^a-zа-яё0-9]+", "", title.lower())[:60]


def _words(title: str) -> set:
    return {w for w in re.findall(r"[a-zа-яёáéíóúñ0-9]{3,}", title.lower()) if w not in _GENERIC}


def similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    if len(wa) < 2 or len(wb) < 2:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.6


# ======================= БАЗА =======================
ITEM_COLS = {"title": "TEXT", "link": "TEXT", "text": "TEXT", "added": "TEXT", "deadline": "TEXT",
             "score": "INTEGER", "official": "TEXT", "note": "TEXT", "src": "TEXT", "topics": "TEXT", "facts": "TEXT"}


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE IF NOT EXISTS subs (chat_id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE IF NOT EXISTS profiles (chat_id INTEGER PRIMARY KEY, data TEXT, step TEXT, "
                 "draft TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "title TEXT, link TEXT, text TEXT)")
    have = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    for col, typ in ITEM_COLS.items():  # миграция старой базы
        if col not in have:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")
    conn.execute("CREATE TABLE IF NOT EXISTS sent (chat_id INTEGER, msg_id INTEGER, item_id INTEGER, "
                 "PRIMARY KEY (chat_id, msg_id))")
    return conn


def _migrate_profiles():
    """Подписчикам, которые были до профилей, — профиль юриста 50+ (как раньше работал бот)."""
    with closing(_db()) as c, c:
        ids = {r[0] for r in c.execute("SELECT chat_id FROM subs")} | {ADMIN_ID}
        for cid in ids:
            c.execute("INSERT OR IGNORE INTO profiles(chat_id, data, step) VALUES (?,?,'')",
                      (cid, json.dumps(DEFAULT_PROFILE, ensure_ascii=False)))


def get_profile(chat_id):
    with closing(_db()) as c:
        r = c.execute("SELECT data FROM profiles WHERE chat_id=?", (chat_id,)).fetchone()
    if r and r[0]:
        try:
            return {**DEFAULT_PROFILE, **json.loads(r[0])}
        except Exception:
            pass
    return None


def lang_of(chat_id) -> str:
    p = get_profile(chat_id)
    if p:
        return p.get("lang", "ru")
    with closing(_db()) as c:
        r = c.execute("SELECT draft FROM profiles WHERE chat_id=?", (chat_id,)).fetchone()
    try:
        return json.loads(r[0]).get("lang", "ru") if r and r[0] else "ru"
    except Exception:
        return "ru"


def save_profile(chat_id, data):
    with closing(_db()) as c, c:
        c.execute("INSERT INTO profiles(chat_id, data, step, draft) VALUES (?,?,'',NULL) ON CONFLICT(chat_id) DO "
                  "UPDATE SET data=excluded.data, step='', draft=NULL", (chat_id, json.dumps(data, ensure_ascii=False)))


def set_step(chat_id, step, draft=None):
    with closing(_db()) as c, c:
        c.execute("INSERT INTO profiles(chat_id, step, draft) VALUES (?,?,?) ON CONFLICT(chat_id) DO "
                  "UPDATE SET step=excluded.step, draft=COALESCE(excluded.draft, profiles.draft)",
                  (chat_id, step, json.dumps(draft, ensure_ascii=False) if draft is not None else None))


def get_step(chat_id):
    with closing(_db()) as c:
        r = c.execute("SELECT step, draft FROM profiles WHERE chat_id=?", (chat_id,)).fetchone()
    if not r:
        return "", {}
    try:
        return r[0] or "", json.loads(r[1]) if r[1] else {}
    except Exception:
        return r[0] or "", {}


def _subscribers() -> list:
    """[(chat_id, профиль)] — только подписанные и с готовым профилем."""
    with closing(_db()) as c:
        ids = {r[0] for r in c.execute("SELECT chat_id FROM subs")} | {ADMIN_ID}
    out = []
    for cid in ids:
        p = get_profile(cid)
        if p:
            out.append((cid, p))
    return out


def _save_item(it: dict) -> int:
    with closing(_db()) as c, c:
        return c.execute(
            "INSERT INTO items(title, link, text, added, deadline, score, official, note, src, topics, facts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (it["title"], it["link"], it["text"][:4000], today().isoformat(), it.get("deadline", ""),
             it.get("score", 0), it.get("official", ""), "", it["src"], ", ".join(it.get("topics", [])),
             json.dumps(it.get("facts", {}), ensure_ascii=False))).lastrowid


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
                                        "ORDER BY id DESC LIMIT 2000", (since,))]


def _active_items(limit=1500) -> list:
    """Находки с фактами ИИ и непрошедшим дедлайном (или без дедлайна, но свежие) → [dict]."""
    t0, fresh = today().isoformat(), (today() - timedelta(days=45)).isoformat()
    with closing(_db()) as c:
        rows = c.execute("SELECT id, title, link, text, deadline, official, src, facts, added FROM items "
                         "WHERE facts IS NOT NULL AND facts != '' AND facts != '{}' AND "
                         "(deadline >= ? OR ((deadline IS NULL OR deadline = '') AND added >= ?)) "
                         "ORDER BY id DESC LIMIT ?", (t0, fresh, limit)).fetchall()
    out = []
    for iid, t, l, x, dl, off, src, facts, added in rows:
        try:
            f = json.loads(facts)
        except Exception:
            continue
        out.append({"id": iid, "title": t, "link": l, "text": x, "deadline": dl or "", "official": off or "",
                    "src": src or "", "facts": f, "topics": f.get("topics", [])})
    return out


def _seen_count() -> int:
    with closing(_db()) as c:
        return c.execute("SELECT COUNT(*) FROM seen").fetchone()[0]


def _is_seen(*keys) -> bool:
    with closing(_db()) as c:
        return any(c.execute("SELECT 1 FROM seen WHERE id=?", (k,)).fetchone() for k in keys)


def _mark_seen(*keys):
    with closing(_db()) as c, c:
        c.executemany("INSERT OR IGNORE INTO seen(id) VALUES (?)", [(k,) for k in keys])


# ======================= ЗАГРУЗКА СТРАНИЦ =======================
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36 GrantsRadarBot",
           "Accept-Language": "en-US,en;q=0.9,ru;q=0.8,es;q=0.7"}


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
                         r"click here|details|подать|заявк|официальн|регистрац|подробн|solicitud|inscripci|"
                         r"web oficial|más información|bases", re.I)
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
                    r"ielts|toefl|english|who can apply|benefit|location|venue|dates?|duration|countr|"
                    r"дедлайн|срок|требован|оплач|проживан|перел|взнос|возраст|кто может|место|даты|стран|"
                    r"plazo|requisito|gastos|alojamiento|viaje|edad|países|financ", re.I)


def focus(text: str, head=1200, extra=1500) -> str:
    """Начало текста + предложения про условия/оплату/дедлайн — ИИ видит главное и не тратит токены."""
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


# ======================= СБОР =======================
SRC_STATUS = {}  # источник → число записей при последней проверке (None — не ответил)


def _entry_date(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    try:
        return date(*t[:3]) if t else None
    except Exception:
        return None


async def _rss(s, sem, name, url, news):
    async with sem:
        raw = await _fetch(s, url)
    parsed = feedparser.parse(raw) if raw else None
    if not parsed or not parsed.entries:
        SRC_STATUS[name] = None
        return []
    SRC_STATUS[name] = len(parsed.entries)
    out, oldest = [], today() - timedelta(days=MAX_AGE_DAYS)
    for e in parsed.entries[:50]:
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
    posts = re.findall(r'data-post="([^"]+)".*?<div class="tgme_widget_message_text[^>]*>(.*?)</div>',
                       raw or "", flags=re.S)
    SRC_STATUS[f"@{ch}"] = len(posts) if posts else None
    out = []
    for post_id, body in posts[-20:]:
        text = _strip(body)
        links = [h for h in re.findall(r'href="(https?://[^"]+)"', body) if "t.me" not in _domain(h)]
        first_line = re.split(r"[\n.!]", text, 1)[0]
        out.append({"id": post_id, "src": f"@{ch}", "title": (first_line or text)[:140],
                    "link": f"https://t.me/{post_id}", "text": text, "news": False, "full": True,
                    "official": html.unescape(links[0]) if links else ""})
    return out


async def collect(stats: dict):
    """Новые записи со всех источников → кандидаты для ИИ (лучшие сначала)."""
    sem = asyncio.Semaphore(10)
    async with aiohttp.ClientSession() as s:
        jobs = [_rss(s, sem, n, u, False) for n, u in RSS_FEEDS.items()]
        jobs += [_rss(s, sem, n, u, True) for n, u in NEWS_FEEDS.items()]
        jobs += [_tg(s, sem, ch) for ch in TG_CHANNELS]
        results = await asyncio.gather(*jobs, return_exceptions=True)
    stats["sources_all"] = len(jobs)
    stats["sources_ok"] = sum(1 for v in SRC_STATUS.values() if v)
    cands, round_keys = [], set()
    for res in results:
        if isinstance(res, Exception):
            log.warning("источник упал: %s", res)
            continue
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
    sem = asyncio.Semaphore(6)

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


# ======================= ИИ: ФАКТЫ О НАХОДКЕ =======================
EXTRACT = """Today is {today}. For each numbered entry below extract facts. Do not guess: use "unknown", null or ""
when the text does not say. Reply with ONLY a JSON array, one object per entry:
{{"n": number,
 "is_opportunity": true only if it is a real call people can apply to now (false for news reports, past events, listicles, ads, jobs),
 "kind": one of {kinds}
   (conference = conference/seminar/forum/workshop/training/short course; exchange = study visit, professional exchange, IVLP-type;
    degree = scholarship for bachelor/master/PhD/university study; business = accelerator, investment, trade mission),
 "online": true/false,
 "duration_days": integer or null,
 "funding": "full" (travel AND accommodation covered, or full scholarship, or grant money with nothing to pay) | "partial" | "none" | "unknown",
 "covers": list from ["flights","accommodation","meals","visa","insurance","stipend","tuition","local_transport"],
 "fee": true if participants must pay an application/registration/participation fee,
 "students_only": bool, "youth_only": bool, "age_min": integer or null, "age_max": integer or null,
 "countries": ["all"] or list of eligible countries/regions in English,
 "exam_required": true if IELTS/TOEFL/other language or entrance test certificate is required,
 "deadline": "YYYY-MM-DD" or "",
 "place": "City, Country" or "Online" or "",
 "dates": "event dates or duration, short" or "",
 "topics": list from {topics},
 "title_ru": "short title in Russian", "title_es": "short title in Spanish",
 "summary_ru": "one short sentence in Russian: what it is and for whom", "summary_es": "same in Spanish",
 "summary_en": "same in English"}}"""


async def ai_fast(messages, max_tokens=3500) -> str:
    """Быстрая модель для разбора находок (отдельный лимит Groq), при её отсутствии — основная."""
    if _groq_request and GR_AI_MODEL:
        body = {"model": GR_AI_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.1}
        if "gpt-oss" in GR_AI_MODEL:
            body["reasoning_effort"] = "low"
        try:
            r = await _groq_request("POST", "/chat/completions", json_body=body)
            return r["choices"][0]["message"].get("content") or ""
        except Exception as e:
            msg = str(e).lower()
            if not any(x in msg for x in ("model_not_found", "does not exist", "decommission", "not found")):
                raise
            log.warning("модель %s недоступна — беру основную", GR_AI_MODEL)
    return await groq_chat(messages, max_tokens=max_tokens, temperature=0.1)


def _parse_json_list(raw: str) -> list:
    raw = re.sub(r"^\x60{3}(?:json)?|\x60{3}$", "", (raw or "").strip(), flags=re.M)
    m = re.search(r"\[.*\]", raw, re.S)
    data = json.loads(m.group(0) if m else raw)
    return data if isinstance(data, list) else []


def _int(v):
    try:
        return int(float(v)) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def clean_facts(v: dict, it: dict) -> dict:
    kind = str(v.get("kind") or "other").lower()
    f = {
        "is_opportunity": bool(v.get("is_opportunity", True)),
        "kind": kind if kind in ITEM_KINDS else "other",
        "online": bool(v.get("online")),
        "duration_days": _int(v.get("duration_days")),
        "funding": str(v.get("funding") or "unknown").lower(),
        "covers": [c for c in (v.get("covers") or []) if c in COVERS],
        "fee": bool(v.get("fee")),
        "students_only": bool(v.get("students_only")),
        "youth_only": bool(v.get("youth_only")),
        "age_min": _int(v.get("age_min")), "age_max": _int(v.get("age_max")),
        "countries": [str(c) for c in (v.get("countries") or ["all"])][:30],
        "exam_required": bool(v.get("exam_required")),
        "place": str(v.get("place") or "")[:80], "dates": str(v.get("dates") or "")[:80],
        "topics": sorted(set(t for t in (v.get("topics") or []) if t in TOPICS) | set(it.get("kw_topics", []))),
    }
    for k in ("title_ru", "title_es", "summary_ru", "summary_es", "summary_en"):
        f[k] = str(v.get(k) or "").strip()[:250]
    dl = parse_date(str(v.get("deadline") or "")) or find_deadline(it["text"])
    f["deadline"] = dl.isoformat() if dl else ""
    return f


def kw_facts(it: dict) -> dict:
    """Запасной разбор без ИИ (по ключевым словам)."""
    t = it["text"].lower()
    kind = ("degree" if re.search(r"master|bachelor|phd|магистр|бакалавр|máster|grado", t) else
            "internship" if re.search(r"internship|стажировк|prácticas", t) else
            "grant" if re.search(r"\bgrant|грант|subvenci", t) else "conference")
    dl = find_deadline(it["text"])
    return {"is_opportunity": True, "kind": kind, "online": False, "duration_days": None,
            "funding": "full" if it.get("funded") else "unknown", "covers": [], "fee": False,
            "students_only": False, "youth_only": False, "age_min": None, "age_max": None, "countries": ["all"],
            "exam_required": False, "place": "", "dates": "", "topics": it.get("kw_topics", []),
            "deadline": dl.isoformat() if dl else ""}


def _usable(f: dict) -> bool:
    """Стоит ли хранить и рассылать (хоть кому-то)."""
    if not f["is_opportunity"] or f["kind"] in ("job", "other") or f["fee"] or f["funding"] == "none":
        return False
    return not (f["deadline"] and f["deadline"] < today().isoformat())


async def ai_extract(cands: list, stats: dict) -> list:
    """ИИ извлекает факты. Разобранные помечаются как просмотренные; при сбое ИИ — остаются на потом."""
    if not cands:
        return []
    out = []
    if not (groq_chat and GROQ_KEY):
        for it in cands:
            _mark_seen(it["id"], _title_key(it["title"]))
            it["facts"] = kw_facts(it)
            if it["funded"] and _usable(it["facts"]):
                out.append(it)
        return out
    todo = cands[:AI_PER_ROUND]
    await enrich(todo)
    sys_prompt = EXTRACT.format(today=today().isoformat(), kinds="|".join(ITEM_KINDS), topics=list(TOPICS))
    for i in range(0, len(todo), AI_BATCH):
        if i:
            await asyncio.sleep(AI_PAUSE)  # бережём лимит токенов в минуту
        batch = todo[i:i + AI_BATCH]
        listing = "\n\n".join(f"{n + 1}. {it['title']}\nSource: {it['src']}\n{focus(it['text'], 900, 1100)}"
                              for n, it in enumerate(batch))
        try:
            raw = await ai_fast([{"role": "system", "content": sys_prompt}, {"role": "user", "content": listing}])
            verdicts = {}
            for v in _parse_json_list(raw):
                if isinstance(v, dict) and str(v.get("n", "")).strip().isdigit():
                    verdicts[int(str(v["n"]).strip()) - 1] = v
        except Exception as e:
            log.warning("ИИ-разбор: %s — остальное разберу в следующий раз", e)
            stats["ai_error"] = str(e)[:120]
            break
        for n, it in enumerate(batch):
            _mark_seen(it["id"], _title_key(it["title"]))
            if n in verdicts:
                it["facts"] = clean_facts(verdicts[n], it)
                if _usable(it["facts"]):
                    out.append(it)
        stats["judged"] = stats.get("judged", 0) + len(batch)
    return out


# ======================= СОПОСТАВЛЕНИЕ С ПРОФИЛЕМ =======================
REGIONS = {
    "kazakhstan": ["kazakhstan", "central asia", "cis", "commonwealth of independent states", "asia", "eurasia",
                   "osce", "post-soviet", "former soviet", "developing", "low and middle income", "lmic",
                   "global south", "emerging", "oic"],
    "spain": ["spain", "eu", "european union", "europe", "schengen", "erasmus", "oecd", "osce", "mediterranean",
              "ibero", "spanish"],
}
OPEN_WORDS = ("all", "any", "worldwide", "global", "international", "all countries", "all nationalities", "everyone")


def country_ok(countries: list, user_country: str) -> bool:
    cs = [c.lower().strip() for c in countries or [] if c]
    if not cs or any(c in OPEN_WORDS or "all countr" in c or "worldwide" in c or "any countr" in c for c in cs):
        return True
    if not user_country:
        return True
    uc = user_country.lower().strip()
    aliases = REGIONS.get(uc, []) + [uc]
    rx = re.compile(r"\b(?:" + "|".join(re.escape(a) for a in aliases) + r")\b")
    return any(rx.search(c) for c in cs)


def match(p: dict, f: dict) -> bool:
    """Подходит ли находка (факты ИИ) этому подписчику."""
    kinds = set().union(*(KINDS[k][1] for k in p.get("kinds") or KINDS if k in KINDS))
    if f["kind"] not in kinds:
        return False
    if f["online"] and not p.get("online_ok"):
        return False
    if p.get("full_only", True):
        if f["funding"] != "full" and not (f["funding"] == "unknown" and f["kind"] in ("exchange", "grant")):
            return False
    elif f["funding"] not in ("full", "partial", "unknown"):
        return False
    if f["students_only"] and not p.get("student"):
        return False
    age = p.get("age")
    if age:
        if f.get("age_max") and age > f["age_max"]:
            return False
        if f.get("age_min") and age < f["age_min"]:
            return False
        if f["youth_only"] and age > 35:
            return False
    if f["exam_required"] and not p.get("has_exam"):
        return False
    if p.get("max_days") and f.get("duration_days") and f["duration_days"] > p["max_days"]:
        return False
    if not country_ok(f.get("countries"), p.get("country", "")):
        return False
    if p.get("topics") and not set(p["topics"]) & set(f.get("topics") or []):
        return False
    return True


# ======================= СООБЩЕНИЯ =======================
def fmt_item(it: dict, lang: str) -> str:
    f = it["facts"]
    title = (f.get(f"title_{lang}") or it["title"]) if lang in ("ru", "es") else it["title"]
    msg = f"{KIND_EMOJI.get(f['kind'], '🌍')} <b>{html.escape(title)}</b>\n"
    if title.lower() != it["title"].lower():
        msg += f"<i>{html.escape(it['title'][:160])}</i>\n"
    summary = f.get(f"summary_{lang}") or f.get("summary_en") or ""
    if summary:
        msg += f"ℹ️ {html.escape(summary)}\n"
    if f.get("topics"):
        msg += "🏷 " + ", ".join(TOPICS[t][0][lang] for t in f["topics"][:3] if t in TOPICS) + "\n"
    where = " · ".join(x for x in (f.get("place"), f.get("dates")) if x)
    if where:
        msg += f"📍 {html.escape(where)}\n"
    if f.get("covers"):
        msg += f"💸 {tr(lang, 'covers')}: " + ", ".join(COVERS[c][lang] for c in f["covers"]) + "\n"
    dl = fmt_deadline(f.get("deadline", ""), lang) or tr(lang, "deadline_none")
    msg += f"⏰ {tr(lang, 'deadline')}: {dl}\n"
    if it.get("official") and it["official"] != it["link"]:
        msg += f"🔗 {tr(lang, 'official')}: {html.escape(it['official'])}\n"
    msg += f"📰 {html.escape(it['src'])}: {html.escape(it['link'])}"
    return msg


def profile_card(p: dict) -> str:
    L = p.get("lang", "ru")
    kinds = ", ".join(KINDS[k][0][L] for k in p.get("kinds", []) if k in KINDS) or tr(L, "any")
    topics = ", ".join(TOPICS[t][0][L] for t in p.get("topics", []) if t in TOPICS) or tr(L, "any")
    rows = [f"<b>{tr(L, 'card')}</b>",
            f"• {tr(L, 'age')}: {p.get('age') or '—'}",
            f"• {tr(L, 'country')}: {html.escape(p.get('country') or '—')}",
            f"• {tr(L, 'status')}: {tr(L, 'student') if p.get('student') else tr(L, 'pro')}",
            f"• {tr(L, 'looking')}: {kinds}",
            f"• {tr(L, 'topics')}: {topics}",
            f"• {tr(L, 'pay')}: {tr(L, 'full_only') if p.get('full_only', True) else tr(L, 'partial_ok')}",
            f"• {tr(L, 'exam')}: {tr(L, 'yes') if p.get('has_exam') else tr(L, 'no')}",
            f"• {tr(L, 'length')}: {tr(L, 'days', n=p['max_days']) if p.get('max_days') else tr(L, 'any')}",
            f"• {tr(L, 'online')}: {tr(L, 'yes') if p.get('online_ok') else tr(L, 'no')}"]
    return "\n".join(rows)


PARSE_PROFILE = """Turn the person's self-description into a JSON profile for an opportunities bot.
Allowed "kinds": {kinds}. Allowed "topics": {topics}.
Return ONLY a JSON object:
{{"age": integer or null, "country": "passport country in English or empty",
 "student": true if currently a student,
 "kinds": list from allowed kinds (if not stated: all except "study", but include "study" if the person is a student or mentions studying/scholarships),
 "topics": list from allowed topics ([] if the person wants any topic),
 "full_only": true unless the person accepts partially funded,
 "has_exam": true only if the person has IELTS/TOEFL or similar certificate,
 "max_days": integer or null (e.g. 31 if they want programs up to a month),
 "online_ok": true only if they want online events too,
 "summary": "1-2 sentences in English describing the person (age, country, occupation, goals)"}}"""


async def parse_profile(text: str, lang: str):
    raw = await groq_chat([{"role": "system", "content": PARSE_PROFILE.format(kinds=list(KINDS), topics=list(TOPICS))},
                           {"role": "user", "content": text}], max_tokens=1200, temperature=0.1)
    raw = re.sub(r"^\x60{3}(?:json)?|\x60{3}$", "", (raw or "").strip(), flags=re.M)
    m = re.search(r"\{.*\}", raw, re.S)
    d = json.loads(m.group(0) if m else raw)
    return {
        "lang": lang, "age": _int(d.get("age")), "country": str(d.get("country") or "").strip(),
        "student": bool(d.get("student")),
        "kinds": [k for k in d.get("kinds") or [] if k in KINDS] or [k for k in KINDS if k != "study"],
        "topics": [t for t in d.get("topics") or [] if t in TOPICS],
        "full_only": bool(d.get("full_only", True)), "has_exam": bool(d.get("has_exam")),
        "max_days": _int(d.get("max_days")), "online_ok": bool(d.get("online_ok")),
        "summary": str(d.get("summary") or "")[:400], "about": text[:1000],
    }


# ======================= БОТ =======================
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
_lock = asyncio.Lock()


def _kb(item_id, lang):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr(lang, "docs_btn"), callback_data=f"doc:{item_id}")]])


LANG_KB = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
    InlineKeyboardButton(text="🇪🇸 Español", callback_data="lang:es"),
    InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en")]])


def _profile_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(lang, "ok_btn"), callback_data="pf:ok"),
         InlineKeyboardButton(text=tr(lang, "redo_btn"), callback_data="pf:redo")],
        [InlineKeyboardButton(text=tr(lang, "lang_btn"), callback_data="pf:lang")]])


def dedupe(items: list) -> list:
    """Убирает то, что уже присылали (похожий заголовок за 60 дней), и дубли внутри прохода."""
    recent, out = _recent_titles(), []
    for it in sorted(items, key=lambda x: -x.get("score", 0)):
        f = it["facts"]
        names = [n for n in (it["title"], f.get("title_ru"), f.get("title_es")) if n]
        if any(similar(n, r) for n in names for r in recent):
            continue
        recent.extend(names)
        out.append(it)
    return out


async def _send_one(chat, it, lang):
    try:
        m = await bot.send_message(chat, fmt_item(it, lang), reply_markup=_kb(it["item_id"], lang),
                                   disable_web_page_preview=True)
        with closing(_db()) as c, c:
            c.execute("INSERT OR IGNORE INTO sent VALUES (?,?,?)", (chat, m.message_id, it["item_id"]))
        await asyncio.sleep(0.4)
        return True
    except Exception as e:
        log.warning("send to %s failed: %s", chat, e)
        return False


async def check(manual_chat=None):
    async with _lock:
        stats = {}
        first = _seen_count() == 0
        cands = await collect(stats)
        items = dedupe(await ai_extract(cands, stats))
        for it in items:
            it["deadline"] = it["facts"].get("deadline", "")
            it["topics"] = it["facts"].get("topics", [])
            it["item_id"] = _save_item(it)
        items.sort(key=lambda x: (-x.get("score", 0), x["facts"].get("deadline") or "9999"))
        mine = 0
        for chat, p in _subscribers():
            fits = [it for it in items if match(p, it["facts"])]
            if first:
                fits = fits[:FIRST_RUN_LIMIT]
            for it in fits:
                await _send_one(chat, it, p.get("lang", "ru"))
            if chat == manual_chat:
                mine = len(fits)
        log.info("проверка: источников %s/%s, новых %s, кандидатов %s, ИИ разобрал %s, годных %s",
                 stats.get("sources_ok", 0), stats.get("sources_all", 0), stats.get("new", 0),
                 stats.get("candidates", 0), stats.get("judged", 0), len(items))
        if manual_chat:
            L = lang_of(manual_chat)
            txt = tr(L, "stats", ok=stats.get("sources_ok", 0), all=stats.get("sources_all", 0),
                     new=stats.get("new", 0), cand=stats.get("candidates", 0), judged=stats.get("judged", 0),
                     mine=mine)
            left = max(0, stats.get("candidates", 0) - stats.get("judged", 0))
            if left:
                txt += "\n" + tr(L, "queue", n=left)
            if stats.get("ai_error"):
                txt += "\n" + tr(L, "ai_err")
            await bot.send_message(manual_chat, txt)


# ======================= ИИ-КОНСУЛЬТАНТ =======================
ADVISOR = """You are an experienced consultant on international conferences, exchanges, internships, grants and
scholarships. Today is {today}. The user: {summary} (age: {age}, passport: {country}).
Answer in {language}, short and practical, Telegram format (no tables, no markdown headings or asterisks; • and emoji
are fine). Rely on the provided page texts and give source links. If something is not on the pages, say so, then
give the typical answer clearly marked as "usually". If the deadline has passed, say it in the first line.
Never present guesses as facts."""

DOCS_TASK = """Analyse this opportunity point by point:
1. What it is, where and when.
2. Who can apply (age, country, experience, language — is IELTS/TOEFL or another test needed). Does it fit this user?
3. What is covered (flights, accommodation, meals, visa, stipend) and what is NOT.
4. Any application/participation fee — how much. Warn if only some participants are funded.
5. Deadline and how many days are left.
6. 📄 Documents to submit (CV, motivation letter, essays, references, passport, video, etc.).
7. How to apply — steps and link to the official form.
8. 💡 2–3 tips to raise this user's chances.
9. Is a visa needed for this user's passport and how it is usually obtained."""

STOP = set("какие какой какая что как где когда нужно нужны нужен есть для про это мне можно или и в на по с о из "
           "сейчас открыты открыт the a an of for to in on and or is are what which how que qué cuál cuáles "
           "para por con los las del una uno hay".split())


def _db_search(question: str, p: dict, limit=8) -> str:
    """Актуальные находки бота, подходящие профилю и похожие на вопрос."""
    words = [w for w in re.findall(r"[a-zа-яёәіңғүұқөһáéíóúñ0-9]{3,}", question.lower()) if w not in STOP][:8]
    if not words:
        return ""
    scored = []
    for it in _active_items():
        if not match(p, it["facts"]):
            continue
        blob = f"{it['title']} {it['text']}".lower()
        sc = sum(blob.count(w[:6]) for w in words)
        if sc:
            scored.append((sc, it))
    scored.sort(key=lambda r: -r[0])
    L = p.get("lang", "ru")
    return "\n".join(f"• {it['title']} — {it['link']} ({fmt_deadline(it['deadline'], L) or '?'})"
                     for _, it in scored[:limit])


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
            raw = await _fetch(s, f"https://www.bing.com/search?q={quote(query)}", 15)
            for href, title, rest in re.findall(
                    r'<li class="b_algo".*?<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)</li>', raw or "", re.S):
                p = re.search(r"<p[^>]*>(.*?)</p>", rest, re.S)
                add(title, _bing_url(html.unescape(href)), p.group(1) if p else "")
        if len(out) < 2:
            raw = await _fetch(s, _gn(query, days=0), 15)
            for e in feedparser.parse(raw or "").entries[:n]:
                add(e.get("title", ""), e.get("link", ""), e.get("summary", ""))
    return out


async def _queries(question: str, lang: str) -> list:
    """ИИ превращает вопрос в 2 поисковых запроса (английский — там больше программ)."""
    try:
        raw = await groq_chat([{"role": "user", "content":
                                "Write 2 short web search queries (max 8 words) to find official program pages for "
                                f"the question below: the first in English, the second in {LANG_NAME[lang]}. "
                                "Reply with ONLY a JSON array of strings.\n\nQuestion: " + question}],
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


async def ask_ai(question: str, p: dict, title="", link="", text="", official="") -> str:
    L = p.get("lang", "ru")
    if not (groq_chat and GROQ_KEY):
        return tr(L, "ai_off")
    if title or link:
        main, found_official = await page_text(link)
        official = official or found_official
        ctx = f"Program: {title}\nLink: {link}\nShort description: {text[:1500]}\n\n"
        if main:
            ctx += f"Page text:\n{focus(main, 2500, 3000)}\n\n"
        if official and official != link:
            off_text, _ = await page_text(official)
            if off_text:
                ctx += f"Official page ({official}):\n{focus(off_text, 2500, 3000)}\n\n"
        if not main or not official:
            found = await web_search(f"{title or link} official how to apply eligibility")
            ctx += "Search results:\n" + "\n".join(f"• {t} — {u}: {sn}" for t, u, sn in found)
            if found and not main:
                ctx += "\n\n" + await _read_pages([u for _, u, _ in found], 2)
    else:
        found = []
        for q in await _queries(question, L):
            for r in await web_search(q, 5):
                if r[1] not in {f[1] for f in found}:
                    found.append(r)
        pages = await _read_pages([u for _, u, _ in found], 3) if found else ""
        mine = _db_search(question, p)
        ctx = ("Fresh web search for the question:\n" +
               ("\n".join(f"• {t} — {u}: {sn}" for t, u, sn in found[:8]) or "nothing found") +
               (f"\n\nTexts of found pages:\n{pages}" if pages else "") +
               (f"\n\nOpen opportunities the bot already found for this user:\n{mine}" if mine else "") +
               "\n\nUse this data and cite links; if data is thin, add general knowledge marked as \"usually\".")
    answer = await groq_chat([
        {"role": "system", "content": ADVISOR.format(today=today().isoformat(), summary=p.get("summary", ""),
                                                     age=p.get("age") or "?", country=p.get("country") or "?",
                                                     language=LANG_NAME.get(L, "Russian"))},
        {"role": "user", "content": f"{ctx[:14000]}\n\nQuestion:\n{question}"},
    ], max_tokens=2200, temperature=0.2)
    return answer.strip() or "…"


async def _reply_long(m: Message, text: str):
    for i in range(0, len(text), 3900):
        await m.answer(html.escape(text[i:i + 3900]), disable_web_page_preview=True)


# ======================= КОМАНДЫ =======================
LANG_PROMPT = "🌐 Выберите язык · Elige idioma · Choose language"


@dp.message(Command("start"))
async def cmd_start(m: Message):
    with closing(_db()) as c, c:
        c.execute("INSERT OR IGNORE INTO subs(chat_id) VALUES (?)", (m.chat.id,))
    p = get_profile(m.chat.id)
    if p:
        L = p.get("lang", "ru")
        await m.answer(profile_card(p), reply_markup=_profile_kb(L))
        await m.answer(tr(L, "saved", n=CHECK_EVERY_MIN) + "\n\n" + tr(L, "help"))
        return
    await m.answer(LANG_PROMPT, reply_markup=LANG_KB)


@dp.message(Command("profile"))
async def cmd_profile(m: Message):
    p = get_profile(m.chat.id)
    if not p:
        await m.answer(LANG_PROMPT, reply_markup=LANG_KB)
        return
    await m.answer(profile_card(p), reply_markup=_profile_kb(p.get("lang", "ru")))


@dp.callback_query(F.data.startswith("lang:"))
async def cb_lang(cq: CallbackQuery):
    L = cq.data.split(":", 1)[1]
    await cq.answer()
    p = get_profile(cq.message.chat.id)
    if p:  # смена языка у готового профиля
        p["lang"] = L
        save_profile(cq.message.chat.id, p)
        await cq.message.answer(profile_card(p), reply_markup=_profile_kb(L))
        return
    set_step(cq.message.chat.id, "about", {"lang": L})
    await cq.message.answer(tr(L, "ask_about"))


@dp.callback_query(F.data.startswith("pf:"))
async def cb_profile(cq: CallbackQuery):
    action, cid = cq.data.split(":", 1)[1], cq.message.chat.id
    await cq.answer()
    step, draft = get_step(cid)
    L = draft.get("lang") or lang_of(cid)
    if action == "lang":
        await cq.message.answer(LANG_PROMPT, reply_markup=LANG_KB)
    elif action == "redo":
        set_step(cid, "about", {"lang": L})
        await cq.message.answer(tr(L, "ask_about"))
    elif action == "ok":
        if step == "confirm" and draft.get("kinds"):
            save_profile(cid, draft)
            with closing(_db()) as c, c:
                c.execute("INSERT OR IGNORE INTO subs(chat_id) VALUES (?)", (cid,))
        p = get_profile(cid) or {}
        L = p.get("lang", L)
        await cq.message.answer(tr(L, "saved", n=CHECK_EVERY_MIN) + "\n\n" + tr(L, "help"))


async def _handle_about(m: Message, draft: dict):
    L = draft.get("lang", "ru")
    if not (groq_chat and GROQ_KEY):  # без ИИ — общий профиль с выбранным языком
        p = {**DEFAULT_PROFILE, "lang": L, "about": m.text[:1000], "summary": m.text[:400],
             "kinds": list(KINDS), "topics": [], "max_days": None, "age": None, "country": ""}
    else:
        await m.answer(tr(L, "parsing"))
        try:
            p = await parse_profile(m.text, L)
        except Exception as e:
            log.warning("профиль: %s", e)
            await m.answer(tr(L, "parse_fail"))
            return
    set_step(m.chat.id, "confirm", p)
    await m.answer(profile_card(p), reply_markup=_profile_kb(L))


@dp.message(Command("stop"))
async def cmd_stop(m: Message):
    with closing(_db()) as c, c:
        c.execute("DELETE FROM subs WHERE chat_id=?", (m.chat.id,))
    await m.answer(tr(lang_of(m.chat.id), "stopped"))


@dp.message(Command("sources"))
async def cmd_sources(m: Message):
    def mark(name):
        v = SRC_STATUS.get(name, "?")
        return "⏳" if v == "?" else ("✅" if v else "❌")

    lines = [f"{mark(n)} {n}" for n in RSS_FEEDS] + [f"{mark(n)} {n}" for n in NEWS_FEEDS] + \
            [f"{mark('@' + c)} @{c}" for c in TG_CHANNELS]
    text = tr(lang_of(m.chat.id), "sources") + "\n" + "\n".join(lines)
    for i in range(0, len(text), 3900):
        await m.answer(html.escape(text[i:i + 3900]))


@dp.message(Command("list"))
async def cmd_list(m: Message):
    """Актуальные находки, подходящие профилю: ближайшие дедлайны сверху."""
    p = get_profile(m.chat.id) or DEFAULT_PROFILE
    L = p.get("lang", "ru")
    key = ((m.text or "").split(maxsplit=1)[1:] or [""])[0].lower().strip()
    rows = [it for it in _active_items() if match(p, it["facts"])
            and (not key or key in f"{it['title']} {it['text']} {it['facts'].get('title_' + L, '')}".lower())]
    rows.sort(key=lambda it: it["deadline"] or "9999")
    q = f" — «{html.escape(key)}»" if key else ""
    if not rows:
        await m.answer(tr(L, "no_active", q=q))
        return
    lines = []
    for it in rows[:25]:
        f = it["facts"]
        t = (f.get(f"title_{L}") if L in ("ru", "es") else "") or it["title"]
        s = f"{KIND_EMOJI.get(f['kind'], '•')} <a href=\"{html.escape(it['link'])}\">{html.escape(t[:90])}</a>"
        s += f" — ⏰ {fmt_deadline(it['deadline'], L)}" if it["deadline"] else ""
        lines.append(s)
    await m.answer(tr(L, "list_head", q=q) + "\n\n" + "\n".join(lines), disable_web_page_preview=True)


@dp.message(Command("now"))
async def cmd_now(m: Message):
    L = lang_of(m.chat.id)
    await m.answer(tr(L, "busy") if _lock.locked() else tr(L, "checking"))
    await check(manual_chat=m.chat.id)


@dp.callback_query(F.data.startswith("doc:"))
async def cb_docs(cq: CallbackQuery):
    p = get_profile(cq.message.chat.id) or DEFAULT_PROFILE
    L = p.get("lang", "ru")
    await cq.answer("⏳")
    row = _get_item(int(cq.data.split(":", 1)[1]))
    if not row:
        await cq.message.answer(tr(L, "not_found"))
        return
    await cq.message.answer(tr(L, "docs_wait"))
    try:
        ans = await ask_ai(DOCS_TASK, p, row["title"], row["link"], row["text"], row.get("official") or "")
    except Exception as e:
        log.exception("ai")
        ans = tr(L, "ai_error", e=e)
    await _reply_long(cq.message, ans)
    await cq.message.answer(tr(L, "docs_follow"))


URL_RE = re.compile(r"https?://\S+")


@dp.message(F.text & ~F.text.startswith("/"))
async def free_text(m: Message):
    """Рассказ о себе (при настройке профиля) или вопрос: про программу из уведомления, по ссылке, общий."""
    step, draft = get_step(m.chat.id)
    if step in ("about", "confirm"):
        await _handle_about(m, draft)
        return
    p = get_profile(m.chat.id)
    if not p:
        await m.answer(LANG_PROMPT, reply_markup=LANG_KB)
        return
    L = p.get("lang", "ru")
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
    await m.answer(tr(L, "thinking"))
    try:
        ans = await ask_ai(m.text, p, title, link, text, official)
    except Exception as e:
        log.exception("ai")
        ans = tr(L, "ai_error", e=e)
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
    _migrate_profiles()
    if _sched_task is None or _sched_task.done():  # лаунчер может перезапускать main()
        _sched_task = asyncio.create_task(_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
