"""Интенсивная программа турецкого: 16 недель, 1,5–2 часа в день, с нуля до A2–B1."""
from urllib.parse import quote_plus

from tr_lessons import lesson_for_day


def yt(q: str) -> str:
    return "https://www.youtube.com/results?search_query=" + quote_plus(q)


# Видео и подкасты для изучающих турецкий (по уровням). Ссылки — поиск YouTube,
# чтобы не ломались, если канал поменяет адрес.
RESOURCES = {
    "Turkishle": (yt("Turkishle learn Turkish"), "фразы для жизни и поездок, A0–A1"),
    "Learn Turkish with Turkishaholic": (yt("Learn Turkish with Turkishaholic"), "грамматика по шагам, A0–A2"),
    "TurkishClass101": (yt("TurkishClass101 absolute beginner"), "уроки-диалоги, A0–A2"),
    "Learn Turkish with Burcu": (yt("Learn Turkish with Burcu"), "живой разговорный турецкий, A1–B1"),
    "Learn Turkish with Neslihan": (yt("Learn Turkish with Neslihan"), "уроки для всех уровней, A1–B1"),
    "Easy Turkish": (yt("Easy Turkish street interviews"), "уличные интервью с субтитрами, A2–B2"),
    "Turkish Tea Time": (yt("Turkish Tea Time podcast"), "подкаст: диалог + грамматика, A1–B1"),
    "Ebru ile Türkçe Podcast": (yt("Ebru ile Türkçe podcast"), "подкаст медленно и ясно, A2–B1"),
    "Learn Turkish with Turkish Coffee": (yt("Learn Turkish with Turkish Coffee podcast"), "подкаст целиком на турецком, B1"),
    "TRT Çocuk": (yt("TRT Çocuk çizgi film"), "мультики — простая чистая речь, A1–A2"),
    "TRT Haber": (yt("TRT Haber bugün"), "новости — для B1+"),
}

DAILY = (
    "План на сегодня (≈1,5–2 часа) — делайте по порядку:\n\n"
    "1️⃣ <b>Урок грамматики</b> — 15 мин. Придёт следующим сообщением.\n"
    "2️⃣ <b>30 новых слов</b> — 25 мин. Три порции по 10: «🔊 Послушать», повторяйте вслух.\n"
    "3️⃣ <b>Игры</b> — 20 мин. «🎮 Игры»: перевод, на слух, правописание, окончания, глаголы.\n"
    "4️⃣ <b>Видео или подкаст</b> — 30 мин. Кнопка «▶️ Смотреть» под этим сообщением.\n"
    "5️⃣ <b>Разговор с учителем</b> — 15 мин. Выполните задание урока голосом 🎤.\n"
    "6️⃣ 🌙 <b>Вечером</b> — «🧠 Проверить слова» (повторение), 10 мин."
)

# неделя: (что делаем, получилось если, видео недели)
WEEKS = {
    1: ("Алфавит, гармония гласных, «я — студент». Привыкаем к звучанию.",
        "Вы можете представиться: имя, откуда, кто Вы, как дела.", "Turkishle"),
    2: ("Вопросы, множественное число, var/yok, настоящее время -iyor.",
        "Вы рассказываете, что делаешь сейчас и что есть у тебя дома.", "Learn Turkish with Turkishaholic"),
    3: ("Где? Куда? Откуда? (-da, -a, -dan), «мой, твой» (-ım, -ın).",
        "Вы объясняете, где живёте и как добраться до центра.", "TurkishClass101"),
    4: ("Определённый объект (-ı), «чей» (-ın), числа, время, цены.",
        "Вы покупаете продукты на рынке в ролевой игре с учителем.", "Learn Turkish with Burcu"),
    5: ("Прошедшее (-dı) и будущее (-acak) время.",
        "Вы рассказываете, что делал вчера и что будешь делать завтра.", "Turkish Tea Time"),
    6: ("Этот/тот, ile/için/gibi, сравнение, вежливые просьбы.",
        "Вы справляетесь с диалогами: кафе, такси, гостиница.", "Learn Turkish with Neslihan"),
    7: ("Широкое время (привычки), «могу» (-ebil), «надо» (-malı, gerek).",
        "Вы рассказываете о своих привычках и обязанностях.", "TRT Çocuk"),
    8: ("«Хочу» + глагол, даты, «делал тогда» (-iyordu).",
        "Голосовое учителю на 2 минуты «моя неделя» почти без пауз.", "Easy Turkish"),
    9: ("Пересказ (-miş) и условие (-sa).",
        "Вы пересказываете серию турецкого сериала своими словами.", "Easy Turkish"),
    10: ("Причастия: -an (который делает), -dık (который я сделал).",
         "Вы описываете людей и вещи сложными фразами.", "Ebru ile Türkçe Podcast"),
    11: ("Деепричастия (-ınca, -ken, -ıp, -madan) и отглагольные формы.",
         "Вы рассказываете длинную историю, связывая фразы.", "Ebru ile Türkçe Podcast"),
    12: ("Залоги и косвенная речь (diye).",
         "Вы пересказываете чужой разговор.", "Learn Turkish with Turkish Coffee"),
    13: ("Повторение A2 + экзамен: чтение и аудирование.",
         "Понимаете основное в подкасте B1 без перевода.", "Learn Turkish with Turkish Coffee"),
    14: ("Экзамен: письмо (100–150 слов) и говорение (монолог 2–3 мин).",
         "Учитель ставит за письмо 7+ из 10.", "TRT Haber"),
    15: ("Пробный экзамен по всем навыкам с учителем.",
         "Вы сдаёте пробный экзамен на 70%+.", "TRT Haber"),
    16: ("Итог курса: монолог 3 минуты, сравнение с записью 8-й недели.",
         "Слышно, насколько Вы выросли. Дальше — сериалы без субтитров и B1+.", "Easy Turkish"),
}
AFTER = ("Поддержка уровня: каждый день слова, игра, 30 мин сериала или подкаста, разговор с учителем.",
         "Каждый месяц понимаете больше без перевода.", "Easy Turkish")


def week_of(day_index: int) -> int:
    return day_index // 7 + 1


def week(w: int):
    return WEEKS.get(w, AFTER)


def video_url(w: int) -> str:
    return RESOURCES[week(w)[2]][0]


def video_name(w: int) -> str:
    return week(w)[2]


def today_text(day_index: int, track: str = "tr") -> str:
    w, d = week_of(day_index), day_index % 7 + 1
    task, check, vid = week(w)
    num, (title, *_), part = lesson_for_day(day_index)
    return (f"☀️ <b>Günaydın! Неделя {w}, день {d}</b>\n\n{DAILY}\n\n"
            f"📖 Урок дня: <b>{num}. {title}</b>" + (" (закрепляем)" if part == 2 else "") + "\n"
            f"🎬 Видео недели: <b>{vid}</b> — {RESOURCES[vid][1]}\n\n"
            f"🎯 <b>Цель недели:</b> {task}\n✔️ <i>Получилось, если:</i> {check}")


def week_task(day_index: int, track: str = "tr"):
    w = week_of(day_index)
    task, check, _ = week(w)
    return (task, check), w


def full_plan_text(track: str = "tr") -> str:
    lines = ["<b>🇹🇷 Турецкий интенсив — 16 недель (1,5–2 часа в день)</b>"]
    for w, (t, c, v) in WEEKS.items():
        lines.append(f"<b>{w}.</b> {t}\n   ✔️ {c}\n   🎬 {v}")
    return "\n".join(lines)


def resources_text() -> str:
    lines = ["🎬 <b>Видео и подкасты для турецкого</b>\n"]
    for name, (url, desc) in RESOURCES.items():
        lines.append(f'• <a href="{url}">{name}</a> — {desc}')
    lines.append("\n📺 <b>Сериалы</b> (с недели 9): включайте турецкие субтитры, смотрите по 10–15 минут, "
                 "пересказывайте учителю. Начните с того, что уже видели на русском.\n"
                 "🎵 <b>Песни</b>: найдите текст (şarkı sözleri), переведите с учителем, пойте вслух.")
    return "\n".join(lines)
