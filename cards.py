"""Карточки «слово + картинка + транскрипция» для испанского бота (метод визуальных ассоциаций).

Слова дня приходят одной картинкой-сеткой: на каждой карточке эмодзи-картинка,
испанское слово (первая буква красная), транскрипция русскими буквами (ударная
гласная красная) и перевод на английский.

Картинки — эмодзи Noto (Google, лицензия Apache 2.0) в PNG, скачиваются один раз
и кэшируются на диске. Если картинку скачать не удалось — карточка всё равно
рисуется, просто без неё. Любая ошибка → None, и бот пришлёт слова текстом.
Pillow импортируется только здесь и только при рисовании (экономия памяти).
"""
import asyncio
import io
import logging
import os
import re

from translit import translit

log = logging.getLogger("cards")
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

# Картинка для слова (только там, где она честно помогает понять смысл).
EMOJI = {
    "hola": "👋", "adiós": "👋", "gracias": "🙏", "por favor": "🥺", "sí": "👍", "no": "👎",
    "me llamo …": "🪪", "¿cómo estás?": "🙂", "bien": "😊", "mal": "😞", "mucho gusto": "🤝",
    "de nada": "😌", "perdón": "🙇", "buenos días": "🌅", "buenas noches": "🌙", "hasta luego": "🚶",
    "no entiendo": "🤷", "¿cómo se dice …?": "💬", "más despacio, por favor": "🐢",
    "yo": "🙋", "tú": "👉", "usted": "🎩", "él / ella": "👫", "y": "➕", "pero": "☝️", "muy": "💯",
    "agua": "💧", "café": "☕", "casa": "🏠", "familia": "👨‍👩‍👧", "amigo / amiga": "🧑‍🤝‍🧑",
    "uno": "1️⃣", "dos": "2️⃣", "tres": "3️⃣", "cuatro": "4️⃣", "cinco": "5️⃣", "seis": "6️⃣",
    "siete": "7️⃣", "ocho": "8️⃣", "nueve": "9️⃣", "diez": "🔟",
    "con": "🤝", "sin": "🚫", "nosotros": "👨‍👩‍👦", "ellos / ellas": "👥",
    "ser": "🧍", "tener": "🤲", "hacer": "🛠️", "ir": "🚶", "querer": "😍", "poder": "💪",
    "decir": "🗣️", "ver": "👀", "dar": "🎁",
    "qué": "❓", "quién": "👤", "dónde": "📍", "cuándo": "⏰", "cómo": "🤔", "por qué": "❔",
    "cuánto": "💰", "aquí": "👇", "allí": "👉", "ahora": "⏱️", "hoy": "📅", "mañana": "🌄",
    "ayer": "⏪", "siempre": "♾️", "nunca": "🙅", "también": "➕", "mucho": "📚", "poco": "🤏",
    "más": "➕", "menos": "➖", "bueno": "👍", "malo": "👎", "grande": "🐘", "pequeño": "🐭",
    "nuevo": "✨", "viejo": "👴", "día": "☀️", "noche": "🌙", "año": "📆", "semana": "🗓️",
    "mes": "🗓️", "hora": "🕐", "tiempo": "⏳", "persona": "🧑", "gente": "👥", "hombre": "👨",
    "mujer": "👩", "niño / niña": "🧒", "padre": "👨", "madre": "👩", "hermano / hermana": "👫",
    "hijo / hija": "👶", "trabajo": "💼", "trabajar": "👷", "estudiar": "📖", "hablar": "🗣️",
    "comer": "🍽️", "beber": "🥤", "vivir": "🏡", "saber": "🧠", "conocer": "🤝", "venir": "🏃",
    "salir": "🚪", "llegar": "🏁", "volver": "🔙", "pensar": "🤔", "creer": "🙏",
    "encontrar": "🔍", "buscar": "🔎", "llamar": "📞", "llamarse": "🪪", "esperar": "⏳",
    "necesitar": "🆘", "gustar": "❤️", "comprar": "🛒", "pagar": "💳", "abrir": "🔓",
    "cerrar": "🔒", "empezar": "🏁", "terminar": "🔚", "entender": "💡", "aprender": "🎓",
    "escribir": "✍️", "leer": "📖", "escuchar": "👂", "mirar": "👀", "jugar": "🎲",
    "dormir": "😴", "ayudar": "🆘", "usar": "🔧", "poner": "📥", "tomar": "🫴", "dejar": "📤",
    "seguir": "👣", "pasar": "🚶", "sentir": "💓", "deber": "📌",
    "comida": "🍲", "pan": "🍞", "leche": "🥛", "carne": "🥩", "pollo": "🍗", "pescado": "🐟",
    "fruta": "🍎", "cerveza": "🍺", "vino": "🍷", "desayuno": "🥐", "almuerzo": "🥗", "cena": "🍝",
    "restaurante": "🍴", "cuenta": "🧾", "dinero": "💵", "precio": "🏷️", "caro": "💎",
    "barato": "🪙", "tienda": "🏪", "calle": "🛣️", "ciudad": "🏙️", "país": "🗺️", "mundo": "🌍",
    "lugar": "📍", "coche": "🚗", "autobús": "🚌", "tren": "🚆", "avión": "✈️", "aeropuerto": "🛫",
    "hotel": "🏨", "habitación": "🛏️", "baño": "🛁", "puerta": "🚪", "ventana": "🪟", "mesa": "🪑",
    "cama": "🛏️", "libro": "📕", "teléfono": "📱", "ordenador": "💻", "escuela": "🏫",
    "universidad": "🎓", "clase": "🧑‍🏫", "pregunta": "❓", "respuesta": "💬", "palabra": "🔤",
    "idioma": "🗣️", "español": "🇪🇸", "inglés": "🇬🇧", "ruso": "🇷🇺", "nombre": "🪪",
    "número": "🔢", "cosa": "📦", "parte": "🧩", "vez": "🔁", "forma": "🔷", "problema": "⚠️",
    "idea": "💡", "historia": "📜", "vida": "🌱", "amor": "❤️", "salud": "🩺", "música": "🎵",
    "película": "🎬", "foto": "📷", "fiesta": "🎉", "viaje": "🧳", "playa": "🏖️", "mar": "🌊",
    "sol": "☀️", "lluvia": "🌧️", "frío": "🥶", "calor": "🥵", "cerca": "📌", "lejos": "🔭",
    "izquierda": "⬅️", "derecha": "➡️", "todo": "💯", "nada": "🫙", "algo": "🎁", "alguien": "👤",
    "nadie": "🚫", "otro": "🔄", "mismo": "🟰", "cada": "🔁", "primero": "🥇", "último": "🏁",
    "veinte": "🔢", "cien": "💯", "mil": "🔢",
    "lunes": "📅", "martes": "📅", "miércoles": "📅", "jueves": "📅", "viernes": "📅",
    "sábado": "🎉", "domingo": "😴",
    "fácil": "😎", "difícil": "😓", "importante": "❗", "posible": "✅", "diferente": "🔀",
    "igual": "🟰", "feliz": "😄", "triste": "😢", "cansado": "🥱", "enfermo": "🤒", "listo": "✅",
    "ocupado": "📵", "libre": "🕊️", "gratis": "🆓", "rápido": "⚡", "lento": "🐌", "temprano": "🐓",
    "tarde": "🌇", "antes": "⏮️", "después": "⏭️", "entre": "↔️", "sobre": "⬆️", "para": "🎯",
    "hasta": "🏁", "desde": "🛫", "casi": "🤏", "solo": "☝️", "ya": "✅", "todavía": "⏳",
    "quizás": "🤷", "claro": "👌", "entonces": "➡️", "además": "➕", "mejor": "🏆", "peor": "👎",
    "verdad": "✅", "mentira": "🤥", "ejemplo": "📝", "información": "ℹ️", "nación": "🏳️",
    "embarazada": "🤰", "éxito": "🏆", "salida": "🚪", "en realidad": "🧐", "librería": "📚",
    "biblioteca": "🏛️", "constipado": "🤧", "carpeta": "📁", "alfombra": "🧶", "sensible": "🥹",
    "sensato": "🧠", "recordar": "🧠", "grabar": "🎙️", "asistir": "🙋", "largo": "📏", "ropa": "👕",
}

_mem: dict = {}
_lock = asyncio.Lock()


def _code(emoji: str, pad: int = 4) -> str:
    """Имя файла эмодзи: Noto — коды с 4 цифрами через «_», Twemoji — без нулей через «-»."""
    return "_".join(f"{ord(c):0{pad}x}" for c in emoji if ord(c) != 0xFE0F)


async def _emoji_png(emoji: str, cache_dir: str):
    """PNG эмодзи (кэш в памяти и на диске). None — если не получилось."""
    if not emoji or len(emoji) > 8:
        return None
    if emoji in _mem:
        return _mem[emoji]
    code = _code(emoji)
    path = os.path.join(cache_dir, f"{code}.png")
    data = None
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = f.read()
    else:
        import aiohttp
        urls = [f"https://raw.githubusercontent.com/googlefonts/noto-emoji/main/png/128/emoji_u{code}.png",
                f"https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/72x72/{_code(emoji, 1).replace('_', '-')}.png"]
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                for u in urls:
                    try:
                        async with s.get(u) as r:
                            if r.status == 200:
                                data = await r.read()
                                break
                    except Exception:
                        continue
        except Exception as e:
            log.warning("emoji %s: %s", emoji, e)
        if data:
            try:
                os.makedirs(cache_dir, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
            except OSError:
                pass
    if len(_mem) < 300:
        _mem[emoji] = data
    return data


# ---------- рисование ----------

W, H, COLS = 360, 430, 3
RED, INK, GREY, LINE = (220, 50, 40), (30, 30, 35), (120, 120, 130), (190, 190, 200)


def _font(bold: bool, size: int):
    from PIL import ImageFont
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size, layout_engine=ImageFont.Layout.BASIC)


def _fit(draw, text, bold, size, max_w):
    while size > 14:
        f = _font(bold, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(bold, size)


def _center_runs(draw, runs, y, x0, font):
    """Рисует строку из кусочков [(текст, цвет)] по центру карточки."""
    total = sum(draw.textlength(t, font=font) for t, _ in runs)
    x = x0 + (W - total) / 2
    for t, color in runs:
        draw.text((x, y), t, font=font, fill=color)
        x += draw.textlength(t, font=font)
    return x0 + (W - total) / 2, total


def _stress_runs(tr: str):
    """«гра́сиас» → [("гр", INK), ("а", RED), ("сиас", INK)] — ударная гласная красная."""
    runs, buf = [], ""
    for ch in tr:
        if ch == "́" and buf:
            if buf[:-1]:
                runs.append((buf[:-1], INK))
            runs.append((buf[-1], RED))
            buf = ""
        else:
            buf += ch
    if buf:
        runs.append((buf, INK))
    return runs


def _render(items) -> bytes:
    from PIL import Image, ImageDraw
    rows = (len(items) + COLS - 1) // COLS
    cols = min(COLS, len(items))
    img = Image.new("RGB", (cols * W + 20, rows * H + 20), "white")
    d = ImageDraw.Draw(img)
    for n, (es, tr, en, png) in enumerate(items):
        x0, y0 = 10 + (n % COLS) * W, 10 + (n // COLS) * H
        # пунктирная рамка, как у карточек для вырезания
        for x in range(x0, x0 + W, 12):
            d.line([(x, y0), (x + 6, y0)], fill=LINE, width=2)
            d.line([(x, y0 + H), (x + 6, y0 + H)], fill=LINE, width=2)
        for y in range(y0, y0 + H, 12):
            d.line([(x0, y), (x0, y + 6)], fill=LINE, width=2)
            d.line([(x0 + W, y), (x0 + W, y + 6)], fill=LINE, width=2)
        if png:
            try:
                pic = Image.open(io.BytesIO(png)).convert("RGBA")
                pic.thumbnail((190, 190)) if max(pic.size) > 190 else None
                if max(pic.size) < 190:
                    k = 190 / max(pic.size)
                    pic = pic.resize((int(pic.width * k), int(pic.height * k)), Image.LANCZOS)
                img.paste(pic, (x0 + (W - pic.width) // 2, y0 + 25 + (190 - pic.height) // 2), pic)
            except Exception as e:
                log.warning("emoji paint: %s", e)
                png = None
        if not png:  # без картинки — большая первая буква в кружке
            d.ellipse([x0 + W / 2 - 80, y0 + 40, x0 + W / 2 + 80, y0 + 200], fill=(255, 236, 230))
            f = _font(True, 110)
            letter = es.lstrip("¿¡")[:1].upper()
            d.text((x0 + W / 2, y0 + 120), letter, font=f, fill=RED, anchor="mm")
        # слово: первая буква красная и подчёркнута
        word = es.replace(" …", "…")
        fw = _fit(d, word, True, 46, W - 30)
        lead = len(word) - len(word.lstrip("¿¡"))
        first = word[:lead + 1]
        sx, _ = _center_runs(d, [(first, RED), (word[lead + 1:], INK)], y0 + 240, x0, fw)
        fx = sx + d.textlength(word[:lead], font=fw)
        fl = d.textlength(word[lead:lead + 1], font=fw)
        bottom = y0 + 240 + fw.getbbox("Hg")[3] + 4
        d.line([(fx, bottom), (fx + fl, bottom)], fill=RED, width=4)
        # транскрипция: ударная гласная красная
        runs = _stress_runs(tr.replace(" …", "…"))
        ft = _fit(d, "".join(t for t, _ in runs), False, 34, W - 30)
        _center_runs(d, runs, y0 + 318, x0, ft)
        # перевод на английский
        fe = _fit(d, en, False, 24, W - 30)
        _center_runs(d, [(en, GREY)], y0 + 372, x0, fe)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


async def cards_image(items, cache_dir: str):
    """items: [(слово, транскрипция, перевод, эмодзи), ...] → PNG с карточками или None при ошибке."""
    try:
        async with _lock:
            ready = []
            for word, tr, meaning, emoji in items:
                ready.append((word, tr, meaning, await _emoji_png(emoji or "", cache_dir)))
            return await asyncio.to_thread(_render, ready)
    except Exception as e:
        log.warning("cards: %s", e)
        return None


async def words_image(words, cache_dir: str):
    """Испанский: words = [(es, ru, en), ...] → карточки с английским переводом."""
    return await cards_image([(es, translit(es), en, EMOJI.get(es, "")) for es, _ru, en in words], cache_dir)


# Картинка по русскому переводу (для турецкого бота): первое слово перевода → эмодзи.
RU_EMOJI = {
    "привет": "👋", "здравствуйте": "👋", "доброе": "🌅", "добрый": "🌆", "спокойной": "🌙",
    "да": "👍", "нет": "👎", "спасибо": "🙏", "пожалуйста": "🥺", "извините": "🙇", "я": "🙋",
    "ты": "👉", "он": "🧍", "мы": "👨‍👩‍👦", "вы": "👥", "они": "👥", "что": "❓", "кто": "👤",
    "где": "📍", "куда": "➡️", "откуда": "⬅️", "когда": "⏰", "как": "🤔", "почему": "❔",
    "сколько": "🔢", "какой": "❓", "есть": "✅", "очень": "💯", "немного": "🤏", "сейчас": "⏱️",
    "здесь": "👇", "там": "👉", "быть": "🧍", "делать": "🛠️", "прийти": "🏃", "приходить": "🏃",
    "идти": "🚶", "уходить": "🚶", "хотеть": "😍", "знать": "🧠", "видеть": "👀", "брать": "🫴",
    "взять": "🫴", "давать": "🎁", "дать": "🎁", "сказать": "🗣️", "говорить": "🗣️", "понимать": "💡",
    "есть,": "🍽️", "пить": "🥤", "читать": "📖", "писать": "✍️", "работать": "💼", "сидеть": "🪑",
    "оставаться": "🏠", "смотреть": "👀", "любить": "❤️", "искать": "🔎", "ждать": "⏳",
    "начинать": "🏁", "заканчиваться": "🔚", "учить": "🎓", "слушать": "👂", "спрашивать": "❓",
    "спать": "😴", "покупать": "🛒", "платить": "💳", "открывать": "🔓", "закрывать": "🔒",
    "играть": "🎲", "помогать": "🆘", "жить": "🏡", "звонить": "📞",
    "один": "1️⃣", "два": "2️⃣", "три": "3️⃣", "четыре": "4️⃣", "пять": "5️⃣", "шесть": "6️⃣",
    "семь": "7️⃣", "восемь": "8️⃣", "девять": "9️⃣", "десять": "🔟", "сто": "💯",
    "вода": "💧", "кофе": "☕", "чай": "🍵", "дом": "🏠", "семья": "👨‍👩‍👧", "друг": "🧑‍🤝‍🧑",
    "хлеб": "🍞", "молоко": "🥛", "мясо": "🥩", "курица": "🍗", "рыба": "🐟", "фрукт": "🍎",
    "фрукты": "🍎", "яблоко": "🍎", "пиво": "🍺", "вино": "🍷", "еда": "🍲", "завтрак": "🥐",
    "обед": "🥗", "ужин": "🍝", "сыр": "🧀", "яйцо": "🥚", "суп": "🍲", "сахар": "🍬", "соль": "🧂",
    "рис": "🍚", "мёд": "🍯", "мед": "🍯", "овощи": "🥦", "картошка": "🥔", "помидор": "🍅",
    "ресторан": "🍴", "счёт": "🧾", "деньги": "💵", "цена": "🏷️", "дорогой": "💎", "дешёвый": "🪙",
    "магазин": "🏪", "улица": "🛣️", "город": "🏙️", "страна": "🗺️", "мир": "🌍", "место": "📍",
    "машина": "🚗", "автобус": "🚌", "поезд": "🚆", "самолёт": "✈️", "аэропорт": "🛫",
    "гостиница": "🏨", "отель": "🏨", "комната": "🛏️", "ванная": "🛁", "дверь": "🚪", "окно": "🪟",
    "стол": "🪑", "стул": "🪑", "кровать": "🛏️", "книга": "📕", "телефон": "📱", "компьютер": "💻",
    "школа": "🏫", "университет": "🎓", "вопрос": "❓", "ответ": "💬", "слово": "🔤", "язык": "🗣️",
    "имя": "🪪", "число": "🔢", "номер": "🔢", "вещь": "📦", "проблема": "⚠️", "идея": "💡",
    "жизнь": "🌱", "любовь": "❤️", "здоровье": "🩺", "музыка": "🎵", "фильм": "🎬", "фото": "📷",
    "праздник": "🎉", "путешествие": "🧳", "море": "🌊", "пляж": "🏖️", "солнце": "☀️",
    "дождь": "🌧️", "снег": "❄️", "холодный": "🥶", "холодно": "🥶", "жарко": "🥵", "горячий": "🔥",
    "близко": "📌", "далеко": "🔭", "левый": "⬅️", "правый": "➡️", "налево": "⬅️", "направо": "➡️",
    "день": "☀️", "ночь": "🌙", "утро": "🌅", "вечер": "🌆", "год": "📆", "неделя": "🗓️",
    "месяц": "🗓️", "час": "🕐", "время": "⏳", "сегодня": "📅", "завтра": "🌄", "вчера": "⏪",
    "человек": "🧑", "люди": "👥", "мужчина": "👨", "женщина": "👩", "ребёнок": "🧒", "ребенок": "🧒",
    "отец": "👨", "мать": "👩", "мама": "👩", "папа": "👨", "брат": "👦", "сестра": "👧",
    "сын": "👦", "дочь": "👧", "муж": "🤵", "жена": "👰", "бабушка": "👵", "дедушка": "👴",
    "работа": "💼", "большой": "🐘", "маленький": "🐭", "хороший": "👍", "плохой": "👎",
    "новый": "✨", "старый": "👴", "красивый": "🌸", "счастливый": "😄", "грустный": "😢",
    "усталый": "🥱", "больной": "🤒", "быстрый": "⚡", "медленный": "🐌", "лёгкий": "🪶",
    "трудный": "😓", "правильный": "✅", "голова": "🙂", "глаз": "👁️", "рука": "✋", "нога": "🦵",
    "сердце": "❤️", "собака": "🐶", "кошка": "🐱", "кот": "🐱", "птица": "🐦", "лошадь": "🐴",
    "цветок": "🌸", "дерево": "🌳", "гора": "⛰️", "река": "🏞️", "небо": "🌤️", "звезда": "⭐",
    "луна": "🌙", "огонь": "🔥", "белый": "⚪", "чёрный": "⚫", "красный": "🔴", "синий": "🔵",
    "зелёный": "🟢", "жёлтый": "🟡", "одежда": "👕", "обувь": "👟", "платье": "👗", "шапка": "🧢",
    "понедельник": "📅", "вторник": "📅", "среда": "📅", "четверг": "📅", "пятница": "📅",
    "суббота": "🎉", "воскресенье": "😴", "врач": "🧑‍⚕️", "больница": "🏥", "аптека": "💊",
    "банк": "🏦", "мечеть": "🕌", "море,": "🌊", "кино": "🎬", "подарок": "🎁", "письмо": "✉️",
    "ключ": "🔑", "сумка": "👜", "часы": "⌚", "чашка": "☕", "стакан": "🥛", "нож": "🔪",
    "учитель": "🧑‍🏫", "студент": "🧑‍🎓", "ученик": "🧑‍🎓", "полиция": "👮", "футбол": "⚽",
}


EXACT_ONLY = {"да", "нет", "один", "правый", "дорогой"}  # только если это весь перевод


def ru_emoji(ru: str) -> str:
    low = ru.strip().lower()
    if low.startswith("до свидания"):
        return "👋"
    first = re.split(r"[\s,;(/]+", low, maxsplit=1)[0] if low else ""
    if first in EXACT_ONLY and re.split(r"\s*\(", low)[0].strip() != first:
        return ""
    return RU_EMOJI.get(first, "")


# ---------- картинки для теста ----------

def _paste_pic(img, png, x, y, size):
    from PIL import Image
    pic = Image.open(io.BytesIO(png)).convert("RGBA")
    k = size / max(pic.size)
    pic = pic.resize((max(1, int(pic.width * k)), max(1, int(pic.height * k))), Image.LANCZOS)
    img.paste(pic, (x + (size - pic.width) // 2, y + (size - pic.height) // 2), pic)


def _render_one(png) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (640, 420), "white")
    _paste_pic(img, png, 170, 60, 300)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _render_grid(pngs) -> bytes:
    from PIL import Image, ImageDraw
    cell = 300
    img = Image.new("RGB", (2 * cell + 30, 2 * cell + 30), "white")
    d = ImageDraw.Draw(img)
    f = _font(True, 40)
    for n, png in enumerate(pngs):
        x, y = 10 + (n % 2) * (cell + 10), 10 + (n // 2) * (cell + 10)
        d.rounded_rectangle([x, y, x + cell, y + cell], radius=24, outline=LINE, width=3)
        _paste_pic(img, png, x + 50, y + 50, cell - 100)
        d.ellipse([x + 12, y + 12, x + 68, y + 68], fill=RED)
        d.text((x + 40, y + 40), str(n + 1), font=f, fill="white", anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


async def picture_image(emoji: str, cache_dir: str):
    """Одна большая картинка (тест «что это?»). None — если картинки нет."""
    try:
        png = await _emoji_png(emoji, cache_dir) if emoji else None
        return await asyncio.to_thread(_render_one, png) if png else None
    except Exception as e:
        log.warning("picture: %s", e)
        return None


async def grid_image(emojis, cache_dir: str):
    """4 картинки с номерами 1–4 (тест «где это слово?»). None — если хоть одной нет."""
    try:
        pngs = [await _emoji_png(e, cache_dir) if e else None for e in emojis]
        if not all(pngs):
            return None
        return await asyncio.to_thread(_render_grid, pngs)
    except Exception as e:
        log.warning("grid: %s", e)
        return None
