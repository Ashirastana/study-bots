"""Учебный план из двух PDF: испанский (8 недель) и ИИ (12 недель)."""
from urllib.parse import quote_plus


def yt(q: str) -> str:
    return "https://www.youtube.com/results?search_query=" + quote_plus(q)


YT_SUPER = '<a href="https://www.youtube.com/@DreamingSpanish/search?query=superbeginner">видео для самых начинающих (YouTube)</a>'
YT_BEGIN = '<a href="https://www.youtube.com/@DreamingSpanish/search?query=beginner">видео для начинающих (YouTube)</a>'
YT_EASY = '<a href="https://www.youtube.com/@EasySpanish/search?query=super%20easy%20spanish">Super Easy Spanish (YouTube)</a>'

def es_video_url(week: int) -> str:
    if week <= 2:
        return "https://www.youtube.com/@DreamingSpanish/search?query=superbeginner"
    if week <= 6:
        return "https://www.youtube.com/@DreamingSpanish/search?query=beginner"
    return "https://www.youtube.com/@EasySpanish/search?query=super%20easy%20spanish"


ES_DAILY = (
    "Сегодня 3 шага, ≈15 минут:\n\n"
    "1️⃣ 📚 Слова дня — послушать и повторить\n"
    "2️⃣ 🗣 Учитель — ответить на 1 вопрос\n"
    "3️⃣ 🧠 Вечером — проверить слова"
)

ES_WEEKS = {
    1: ("Знакомство. Смотрите " + YT_SUPER + " — по 1–2 видео в день.",
        "Вы можете поздороваться и сказать, как Вас зовут."),
    2: ("Каждый день " + YT_SUPER + ". Учим говорить о себе: откуда Вы, сколько Вам лет.",
        "Вы отвечаете учителю в боте на 3–5 простых вопросов о себе."),
    3: ("Смотрите " + YT_BEGIN + ". Учимся говорить, что нравится: «me gusta…».",
        "Вы рассказываете минуту о своей семье и о том, что любите."),
    4: ("Каждый день " + YT_BEGIN + ". Попробуйте записать голосовое на 1 минуту «мой день».",
        "Голосовое «мой день» отправлено учителю в боте."),
    5: ("Учимся говорить о прошлом: «ayer…» — вчера я… Видео: " + YT_BEGIN + ".",
        "Вы рассказываете, что делали вчера, 5–6 предложений."),
    6: ("Видео " + YT_BEGIN + ", 20–30 минут в день. Повторяйте фразы за диктором.",
        "Вы понимаете больше половины простого видео."),
    7: ("Попробуйте " + YT_EASY + " — уличные разговоры с субтитрами.",
        "10 минут разговора с учителем в боте без перехода на русский."),
    8: ("Итог двух месяцев: снова голосовое «мой день» на 1–2 минуты. Видео — " + YT_EASY + ".",
        "Сравните с записью 4-й недели — слышно, как стало лучше."),
}
ES_AFTER = ("Продолжаем: видео " + YT_EASY + " каждый день, слова в боте, разговор с учителем.",
            "Каждый месяц Вы понимаете и говорите больше.")

AI_WEEKS = {
    1: ("Карпаты «Intro to LLMs» (1 ч) + AI For Everyone. Конспект 6 терминов своими словами: токен, контекст, температура, галлюцинация, мультимодальность, RAG.",
        "Можешь объяснить другу, почему ИИ «выдумывает»."),
    2: ("Туториал Anthropic по промптингу. 20 промптов по формуле: роль–контекст–задача–формат–примеры–ограничения.",
        "Один промпт даёт одинаково хороший результат 3 раза подряд."),
    3: ("Сравни Claude / ChatGPT / Gemini на 3 задачах. NotebookLM по своему PDF.",
        "Таблица сравнения + выбор модели под задачу."),
    4: ("Персонаж в 10 сценах в одном стиле (Midjourney / Nano Banana / Flux).",
        "10 картинок, персонаж узнаётся везде."),
    5: ("5 клипов image-to-video в двух разных моделях (Kling, Veo, Seedance, Runway…).",
        "Понимаешь, какая модель лучше для какой сцены."),
    6: ("Ролик 30 сек: сценарий → кадры → видео → озвучка → монтаж (CapCut / DaVinci).",
        "Ролик выложен в Reels/Shorts."),
    7: ("Видео с аватаром (HeyGen) или дубляж своего видео на испанский (ElevenLabs).",
        "Аватар/голос звучит естественно."),
    8: ("Вайбкодинг: ТЗ через ИИ → лендинг для бизнеса в Bolt / Lovable / Google AI Studio → публикация.",
        "Сайт открывается по ссылке с телефона, форма работает."),
    9: ("Веб-приложение «тренажёр испанских слов» с ИИ. Одна правка — одна просьба. Версии сохранять в GitHub.",
        "Приложение по ссылке, 3 человека им воспользовались."),
    10: ("Первая автоматизация в n8n или Make.",
         "Работает без ручного запуска."),
    11: ("Telegram-бот с ИИ — собрать вайбкодингом (Cursor / Claude): голосовое → расшифровка → выжимка → Google Таблица.",
         "Бот отвечает 5 разным людям."),
    12: ("Hugging Face Agents Course, модули 1–2.",
         "Агент сам выполняет задачу из 3+ шагов."),
    13: ("Ollama или LM Studio + ComfyUI (если позволяет ПК).",
         "Локальная модель ответила / сгенерировала кадр."),
    14: ("Финал: реклама 30–60 сек + сайт для реального бизнеса, собрать портфолио.",
         "Работу можно показать заказчику."),
}
AI_AFTER = ("Портфолио готово. Каждую неделю — 1 новая работа под заказ и обзор новых моделей.",
            "Минимум 1 работа в неделю в портфолио.")


def week_of(day_index: int) -> int:
    return day_index // 7 + 1


def es_week(w: int):
    return ES_WEEKS.get(w, ES_AFTER)


def ai_week(w: int):
    return AI_WEEKS.get(w, AI_AFTER)


AI_DAILY = (
    "🤖 <b>ИИ — ежедневно (1,5–2 ч)</b>\n"
    "• 20–30 мин теории по заданию недели\n"
    "• 60+ мин практики: делаешь результат недели\n"
    "• Удачные промпты — в свою «библиотеку промптов»\n"
    "❓ Застрял — напиши мне вопрос обычным сообщением, помогу.\n"
    "📎 Результат присылай сюда: фото, видео, ссылку или файл."
)


def today_text(day_index: int, track: str) -> str:
    w = week_of(day_index)
    d = day_index % 7 + 1
    if track == "es":
        task, check = es_week(w)
        return (f"☀️ <b>¡Buenos días, агент!</b> 🕵️\n\n{ES_DAILY}\n\n"
                f"🎯 Цель недели: {check}\n"
                "🎬 Есть время? Видео — кнопкой ниже.")
    task, check = ai_week(w)
    return (f"📅 <b>Неделя {w}, день {d}</b>\n\n{AI_DAILY}\n\n"
            f"🎯 <b>Проект недели:</b> {task}\n✔️ <i>Готово, если:</i> {check}\n\n"
            "Вечером отметь, что сделал, кнопками ниже 👇")


def week_task(day_index: int, track: str):
    w = week_of(day_index)
    return (es_week(w) if track == "es" else ai_week(w)), w


def full_plan_text(track: str) -> str:
    if track == "es":
        lines, items = ["<b>🇪🇸 Испанский — план на первые 8 недель</b>"], ES_WEEKS.items()
    else:
        lines, items = ["<b>🤖 ИИ на практике — 14 недель</b>"], AI_WEEKS.items()
    for w, (t, c) in items:
        lines.append(f"<b>{w}.</b> {t}\n   ✔️ {c}")
    return "\n".join(lines)


# Видео по интересам: ученик сам выбирает тему, которая ему нравится — так
# смотреть интереснее. Ссылки — тематический поиск YouTube (всегда рабочий);
# любую можно заменить на конкретный канал/плейлист. Совет ученику — включать
# субтитры (сначала на русском/английском, позже на испанском).
# Формат: (ключ, кнопка, описание, ссылка).
VIDEO_THEMES = [
    ("beginner", "🟢 Для начинающих", "Dreaming Spanish — медленно, всё понятно по картинкам",
        "https://www.youtube.com/@DreamingSpanish/search?query=superbeginner"),
    ("street", "🗣 Уличные диалоги", "Easy Spanish — живая речь на улице с субтитрами",
        yt("easy spanish street interview subtitles")),
    ("series", "📺 Сериалы", "испанские сериалы с субтитрами для изучающих",
        yt("learn spanish with tv series english subtitles")),
    ("business", "💼 Бизнес и саморазвитие", "про бизнес, деньги и развитие — с субтитрами",
        yt("spanish business self improvement motivation video subtitles")),
    ("vlog", "🎒 Влоги и жизнь", "жизнь в Испании и Латам, влоги с субтитрами",
        yt("spanish vlog daily life subtitles")),
    ("podcast", "🎧 Подкасты", "слушать в дороге: для изучающих, помедленнее",
        yt("spanish podcast for beginners slow")),
    ("songs", "🎵 Песни", "клипы с текстом (letra)",
        yt("spanish songs with lyrics letra")),
]


def video_themes_text() -> str:
    lines = ["🎬 <b>Видео по интересам</b>",
             "Выберите тему, которая вам по душе — смотреть будет интереснее. "
             "Совет: включите <b>субтитры</b> и смотрите по 15–20 минут, "
             "потом расскажите учителю, что поняли.\n"]
    for _key, label, desc, _url in VIDEO_THEMES:
        lines.append(f"{label} — {desc}")
    return "\n".join(lines)
