"""ИИ-функции турецкого бота: расшифровка голоса, проверка турецкого, тесты.
БЕСПЛАТНО: Groq (GROQ_API_KEY) — и голос (Whisper), и текст (GPT-OSS / Llama — что доступно ключу).
Платно (по желанию): Claude (ANTHROPIC_API_KEY) или OpenAI (OPENAI_API_KEY).
Без ключей бот работает, просто без ИИ-проверки."""
import json
import os
import re

GROQ_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip()  # пусто = выбрать автоматически
# Порядок предпочтения: сильная и бесплатная модель первой. Бот сам берёт первую доступную ключу.
GROQ_PREFS = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3.8-27b",
              "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
_groq_model_cache = {"model": GROQ_MODEL or None}


async def groq_model(force_refresh=False) -> str:
    """Возвращает рабочую модель Groq. Если указанная недоступна — спрашивает у Groq список и выбирает."""
    if _groq_model_cache["model"] and not force_refresh:
        return _groq_model_cache["model"]
    try:
        data = await _groq_request("GET", "/models")
        have = {m["id"] for m in data.get("data", [])}
    except Exception:
        have = set()
    for m in GROQ_PREFS:
        if m in have:
            _groq_model_cache["model"] = m
            return m
    chat_like = sorted(m for m in have if not any(x in m for x in ("whisper", "guard", "orpheus", "tts")))
    _groq_model_cache["model"] = chat_like[0] if chat_like else GROQ_PREFS[0]
    return _groq_model_cache["model"]


def groq_extra(model: str) -> dict:
    # у reasoning-моделей gpt-oss «думание» тратит токены — держим его коротким
    # medium — заметно умнее в диалоге, всё ещё быстро и бесплатно
    return {"reasoning_effort": os.getenv("GROQ_REASONING", "medium")} if "gpt-oss" in model else {}


async def _groq_request(method: str, path: str, json_body=None, form=None) -> dict:
    """Лёгкий клиент Groq на aiohttp (уже есть в aiogram) — без тяжёлой библиотеки openai,
    чтобы экономить память на бесплатном хостинге."""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=120)
    headers = {"Authorization": f"Bearer {GROQ_KEY}"}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, GROQ_URL + path, headers=headers,
                                   json=json_body, data=form) as r:
            text = await r.text()
            if r.status >= 400:
                raise RuntimeError(f"Groq {r.status}: {text[:500]}")
            return json.loads(text) if text else {}


async def groq_chat(messages: list, max_tokens: int, temperature: float) -> str:
    for attempt in range(2):
        model = await groq_model(force_refresh=attempt > 0)
        try:
            body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
                    "messages": messages, **groq_extra(model)}
            r = await _groq_request("POST", "/chat/completions", json_body=body)
            return (r["choices"][0]["message"].get("content") or "")
        except Exception as e:
            msg = str(e).lower()
            if attempt == 0 and ("model_not_found" in msg or "does not exist" in msg or "decommission" in msg):
                _groq_model_cache["model"] = None
                continue
            raise
# large-v3 точнее turbo на акценте и коротких фразах новичка
GROQ_STT = os.getenv("GROQ_STT_MODEL", "whisper-large-v3")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "whisper-1")

LEVELS = {1: "A0–A1", 2: "A1", 3: "A1", 4: "A1–A2", 5: "A2", 6: "A2", 7: "A2", 8: "A2–B1"}

CHECK_PROMPT = """Ты — строгий, но доброжелательный репетитор турецкого.
Ученик (родной язык русский, уровень {level}, неделя обучения {week}) прислал устную речь.
Ниже — автоматическая расшифровка (могут быть ошибки распознавания — не считай их ошибками ученика, если смысл ясен).

<transcript>
{text}
</transcript>

Ответь СТРОГО в JSON без пояснений вокруг:
{{"score": <целое 1-10 с учётом уровня>,
  "corrections": [{{"wrong": "...", "right": "...", "why": "кратко по-русски"}}],
  "good": "что получилось хорошо, 1 фраза по-русски",
  "tip": "один главный совет на завтра, по-русски",
  "anki": ["турецкая фраза ; русский перевод", "..."]}}
Максимум 4 исправления (самые важные), 3 карточки anki. Без лишней похвалы."""

TEST_PROMPT_ES = """Составь мини-тест по турецкому для ученика (родной язык русский, знает казахский), неделя {week}.
Тема недели: {task}
Дай 5 заданий: 2 — перевести фразу на турецкий, 2 — вставить пропущенное окончание или слово, 1 — ответить на вопрос по-турецки.
Пронумеруй. Ответы НЕ пиши. Простой текст для Telegram, инструкции по-русски."""

TEST_PROMPT_AI = """Составь мини-тест по практическому ИИ для ученика, неделя {week}.
Проект недели: {task}
Дай 5 практических вопросов (не теория ради теории): например, «улучши этот промпт», «какой инструмент выберешь и почему», «что делать, если…».
Пронумеруй. Ответы НЕ пиши. Простой текст для Telegram, по-русски."""

MENTOR_PROMPT = """Ты — практичный наставник по ИИ (промптинг, ИИ-видео и картинки, работа с LLM, вайбкодинг, автоматизация n8n/Make, агенты).
Ученик на неделе {week} программы. Проект недели: {task}

Вопрос ученика:
<question>
{q}
</question>

Ответь по-русски, коротко и по делу (до 1200 символов): конкретные шаги, готовый промпт для копирования если уместно.
Предпочитай бесплатные инструменты. Если инструмент мог измениться — скажи проверить на сайте.
Не помогай с дипфейками реальных людей без согласия. Формат — простой текст для Telegram, без markdown-заголовков."""


async def mentor(q: str, week: int, task: str) -> str:
    return await _chat(MENTOR_PROMPT.format(week=week, task=task, q=q), max_tokens=900)


def enabled() -> bool:
    return bool(GROQ_KEY or ANTHROPIC_KEY or OPENAI_KEY)


def voice_enabled() -> bool:
    return bool(GROQ_KEY or OPENAI_KEY)


class AIError(Exception):
    """Понятная ошибка для студента (без технических подробностей)."""


async def with_retry(fn, tries=3):
    import asyncio
    last = None
    for i in range(tries):
        try:
            return await fn()
        except Exception as e:  # сеть, 429, 5xx
            last = e
            msg = str(e).lower()
            if "model" in msg and ("not found" in msg or "decommission" in msg or "does not exist" in msg):
                raise AIError("MODEL") from e
            if "401" in msg or "invalid api key" in msg or "authentication" in msg:
                raise AIError("KEY") from e
            await asyncio.sleep(2 * (i + 1) + (5 if "429" in msg or "rate" in msg else 0))
    raise AIError("BUSY") from last


async def _chat(prompt: str, max_tokens=1200) -> str:
    return await with_retry(lambda: _chat_once(prompt, max_tokens))


async def _chat_once(prompt: str, max_tokens=1200) -> str:
    if GROQ_KEY:
        return await groq_chat([{"role": "user", "content": prompt}], max_tokens, 0.3)
    if ANTHROPIC_KEY:
        from anthropic import AsyncAnthropic
        r = await AsyncAnthropic(api_key=ANTHROPIC_KEY).messages.create(
            model=CLAUDE_MODEL, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    from openai import AsyncOpenAI
    r = await AsyncOpenAI(api_key=OPENAI_KEY).chat.completions.create(
        model=OPENAI_MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}])
    return r.choices[0].message.content


async def transcribe(path: str) -> str:
    """Простая расшифровка (используется для совместимости)."""
    r = await with_retry(lambda: _transcribe_once(path, "tr", ""))
    return r["text"]


def _get(o, k, default=None):
    return o.get(k, default) if isinstance(o, dict) else getattr(o, k, default)


async def _transcribe_once(path: str, language, prompt: str) -> dict:
    if GROQ_KEY:
        import aiohttp
        form = aiohttp.FormData()
        with open(path, "rb") as f:
            form.add_field("file", f.read(), filename=os.path.basename(path))
        form.add_field("model", GROQ_STT)
        form.add_field("response_format", "verbose_json")
        form.add_field("temperature", "0")
        if language:
            form.add_field("language", language)
        if prompt:
            form.add_field("prompt", prompt[:800])
        r = await _groq_request("POST", "/audio/transcriptions", form=form)
    else:
        from openai import AsyncOpenAI
        kw = dict(model=TRANSCRIBE_MODEL, response_format="verbose_json", temperature=0)
        if language:
            kw["language"] = language
        if prompt:
            kw["prompt"] = prompt[:800]
        with open(path, "rb") as f:
            r = await AsyncOpenAI(api_key=OPENAI_KEY).audio.transcriptions.create(
                file=(os.path.basename(path), f), **kw)
    segs = _get(r, "segments") or []
    lps = [_get(x, "avg_logprob", 0.0) for x in segs]
    nsp = [_get(x, "no_speech_prob", 0.0) for x in segs]
    return {"text": (_get(r, "text") or "").strip(),
            "lang": str(_get(r, "language") or "").lower(),
            "avg_logprob": sum(lps) / len(lps) if lps else -9.0,
            "no_speech": max(nsp) if nsp else 1.0}


CYR = re.compile(r"[а-яё]", re.I)
# Настоящие русские слова (а не турецкие слова, записанные кириллицей)
RU_WORDS = set("""как что это сказать скажите понимаю понял поняла не мне меня вы ты спасибо привет пожалуйста
помогите помоги повторите повтори почему где когда хочу можно переведи переведите слово слова турецкий турецки
по-турецки знаю нет хорошо плохо зовут так вот ещё еще сейчас здравствуйте извините простите давай давайте
говорить говорю правильно неправильно сложно трудно устала устал сегодня вчера завтра какой какая надо нужно
помощь объясни объясните забыла забыл""".split())


def looks_russian(text: str) -> bool:
    toks = re.findall(r"[а-яё-]+", text.lower())
    if not toks:
        return False
    hits = sum(t in RU_WORDS for t in toks)
    return hits >= 1 and hits / len(toks) >= 0.3


async def transcribe_smart(path: str, context: str = "") -> dict:
    """Две параллельные расшифровки: как турецкий (с подсказкой-контекстом урока) и с
    автоопределением языка. Возвращает {"kind": "es"|"ru"|"noise", "text", "conf"}.
    Новички часто вставляют русские слова — тогда честно передаём их как русскую речь."""
    import asyncio
    es_task = with_retry(lambda: _transcribe_once(path, "tr", context))
    auto_task = with_retry(lambda: _transcribe_once(path, None, ""))
    es, auto = await asyncio.gather(es_task, auto_task, return_exceptions=True)
    if isinstance(es, Exception):
        raise es
    auto = None if isinstance(auto, Exception) else auto
    log_lp, nsp = es["avg_logprob"], es["no_speech"]
    if auto and CYR.search(auto["text"]) and looks_russian(auto["text"]):
        return {"kind": "ru", "text": auto["text"], "conf": auto["avg_logprob"], "es_guess": es["text"]}
    if not es["text"] or looks_like_noise(es["text"]) or log_lp < -1.15 or (nsp > 0.6 and log_lp < -0.7):
        return {"kind": "noise", "text": es["text"], "conf": log_lp}
    return {"kind": "es", "text": es["text"], "conf": log_lp,
            "unsure": log_lp < -0.6}


RESTORE_PROMPT = """Ты помогаешь распознаванию речи. Ученик-новичок (русский/казахский акцент) сказал фразу по-турецки.
Автоматическая расшифровка могла исказить слова на похожие по звучанию (например «Mer haba» вместо «Merhaba», «teşekür» вместо «teşekkür», «benim adım Ali» превратилось в «benim adam Ali», «Almatı'dan» — в «Almata dan»).
Контекст: учитель только что спросил: «{question}». Слова, которые она учит: {words}.

Расшифровка: «{text}»

Верни ТОЛЬКО то, что она скорее всего хотела сказать, исправив ТОЛЬКО искажения распознавания (похожие по звучанию слова и написание).
НЕ исправляй грамматику (окончания, гармонию гласных, форму глагола, порядок слов): если он сказал «evda» вместо «evde» или «ben gidiyor» — оставь как есть.
Имена собственные и числа оставь. Если не уверен — оставь слово как в расшифровке. Ответ — одна строка, без кавычек и пояснений."""


async def restore_voice(text: str, question: str = "", words: str = "") -> str:
    """Исправляет только ошибки распознавания речи (не грамматику ученицы)."""
    if not GROQ_KEY or not text.strip():
        return text
    try:
        out = await with_retry(lambda: groq_chat(
            [{"role": "user", "content": RESTORE_PROMPT.format(question=question or "—", words=words or "—", text=text)}],
            300, 0.0))
        out = (out or "").strip().strip("«»\"'").splitlines()[0].strip() if out else ""
        # защита: результат не должен сильно отличаться по длине
        if out and 0.4 <= len(out) / max(1, len(text)) <= 2.5:
            return out
    except Exception:
        pass
    return text


def parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no json")
    return json.loads(m.group(0))


def format_check(d: dict) -> str:
    out = [f"📊 <b>Оценка: {d.get('score', '?')}/10</b>"]
    corr = d.get("corrections") or []
    if corr:
        out.append("\n✏️ <b>Corrección:</b>")
        for c in corr:
            out.append(f"• <s>{esc(c.get('wrong',''))}</s> → <b>{esc(c.get('right',''))}</b>\n  <i>{esc(c.get('why',''))}</i>")
    else:
        out.append("\n✅ Ошибок не найдено.")
    if d.get("good"):
        out.append(f"\n👍 {esc(d['good'])}")
    if d.get("tip"):
        out.append(f"🎯 <b>На завтра:</b> {esc(d['tip'])}")
    if d.get("anki"):
        out.append("\n🗂 <b>В Anki:</b>\n<code>" + "\n".join(esc(x) for x in d["anki"]) + "</code>")
    return "\n".join(out)


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def check_spanish(text: str, week: int) -> dict:
    raw = await _chat(CHECK_PROMPT.format(level=LEVELS.get(week, "B1"), week=week, text=text))
    try:
        return parse_json(raw)
    except Exception:
        return {"score": None, "corrections": [], "tip": raw[:800]}


async def make_test(track: str, week: int, task: str) -> str:
    tpl = TEST_PROMPT_ES if track in ("es", "tr") else TEST_PROMPT_AI
    return await _chat(tpl.format(week=week, task=task), max_tokens=700)


# ================= Промпты из PDF, адаптированные под Telegram =================

TUTOR_ES = """Ты — тёплый, терпеливый и требовательный личный учитель турецкого языка. Ты общаешься в Telegram с учеником, который учит турецкий С НУЛЯ интенсивно (1,5–2 часа в день).

ОБ УЧЕНИКЕ:
- Родной язык: русский. Знает ли казахский — смотри в «О нём» (если знает — это огромная подсказка: турецкий и казахский — родственные тюркские языки). Неделя занятий: {week}.
- Тема недели и урок дня: {task}
- О нём (уровень, цель, интересы): {profile}

{level_block}

КАК ТЫ ОБЩАЕШЬСЯ:
1. Обращайся на «Вы» по-русски, по-турецки — на «sen» (так проще новичку). Пиши простым русским, без терминов («окончание», а не «аффикс»; «как меняется глагол», а не «спряжение»).
2. Коротко: до 700 символов. Одна мысль — один абзац.
3. ПЕРВАЯ строка каждого ответа — твоя реплика по-турецки, начинается с «🇹🇷 ». На СЛЕДУЮЩЕЙ строке — перевод в скобках. Турецкий — на уровне ученика.
3а. Помни весь разговор: НЕ спрашивай то, что он уже рассказал. Реагируй на его слова, двигай разговор дальше.
4. Блок «✏️ Исправление» — ТОЛЬКО если ученик в этом сообщении сам написал или сказал что-то по-турецки. В блоке: его фраза → правильная; одна строка объяснения; не больше 2 исправлений. Особенно следи за гармонией гласных, окончаниями падежей и порядком слов (глагол в конце). Если ошибок НЕТ — одна строка: «✅ Без ошибок — отлично!».
5. Перед исправлением — одна строка честной конкретной похвалы. Мягко, но не скрывай ошибки.
6. Заканчивай одним вопросом по-турецки. На ступенях 1–4 под вопросом дай образец ответа: «💡 Можно ответить: Benim adım … (Меня зовут …)».
7. Если он пишет по-русски — покажи, как это сказать по-турецки, и попроси повторить (текстом или голосом).
8. ТОЛЬКО если ученик знает казахский — используй казахский как мост: похожие слова (su = су, göz = көз, kitap = кітап, gelmek = келу), похожие окончания (-da/-de = -да/-де, -dan = -дан, -dı = -ды, -ıp = -ып, -sa = -са, mı = ма/ме), одинаковый порядок слов. Казахские слова пиши только если уверен, что они действительно похожи. Если казахского не знает — не упоминай его. Предупреждай о «ложных друзьях»: hakim — судья (а не аким), nazik — вежливый (а не нежный), taze — свежий.
9. Если в теме дня есть урок грамматики — давай упражнения именно на него, по одному заданию за раз.
10. Команда «/урок»: короткий урок (5 минут) по его частой ошибке: объяснение по-русски, 3 турецких примера с переводом, 3 задания.
11. Команда «/тест»: 5 слов или фраз на перевод из пройденного (вопросы по-русски).
12. Если он растерялся или пишет «не понимаю» — успокой, объясни по-русски одним-двумя предложениями и предложи самый простой шаг.

ЧЕСТНОСТЬ (обязательно):
- Ты ИИ-учитель, не человек. Если спросят — скажи честно.
- Никогда не выдумывай правила и слова. Не уверен — скажи «тут я не уверен, лучше проверить в словаре».
- Сообщения с [ГОЛОС] — автоматическая расшифровка речи. Распознавание ошибается в буквах у новичков, ПОЭТОМУ в голосовых НЕ исправляй написание и похожие по звучанию слова (ı/i, ş/s, ç/c, ğ). Исправляй только явные ошибки окончаний, формы глагола или выбор слова. Если смысл неясен — переспроси без оценки.
- Сообщения [ГОЛОС по-русски] — он говорил по-русски: помоги сказать это по-турецки, без оценки.
- Если сообщение написано по-русски — НИКОГДА не давай «Исправление» и оценку, просто помоги.
- Не придумывай ошибки ради исправлений.

ФОРМАТ: простой текст и эмодзи, без markdown (* # _).
Если в этом сообщении ученик сам написал или сказал что-то по-турецки — последней отдельной строкой напиши: ОЦЕНКА: N/10. Иначе оценку не пиши.
Как оценивать новичка: без ошибок — 9–10; одна мелкая ошибка — 7–8; несколько ошибок, но смысл понятен — 6; понятна только часть — 4–5."""

MENTOR_AI = """Ты — мой личный наставник по практическому ИИ: промптинг, генерация изображений и видео, работа с LLM, вайбкодинг (сайты и приложения с помощью ИИ), автоматизация. Ты практик, а не лектор.

ОБО МНЕ:
- Неделя программы: {week} из 14. Проект этой недели: {task}
- О себе (уровень, цель, компьютер, бюджет): {profile}

КАК ТЫ РАБОТАЕШЬ:
1. Учи через проекты: каждое занятие = конкретный результат, который я сделаю сегодня. Теория — максимум 20% и только нужная для задачи.
2. Формат занятия: ЦЕЛЬ (1 строка) → ШАГИ (нумерованные, с конкретными кнопками/настройками) → ГОТОВЫЙ ПРОМПТ для копирования → КАК ПРОВЕРИТЬ → ДОМАШНЕЕ ЗАДАНИЕ.
3. Если я присылаю свой промпт или результат — разбери: что слабое, почему, исправленная версия. Без лишней похвалы.
4. Если инструмент мог измениться после твоих данных — так и скажи и дай способ проверить актуальность.
5. Предлагай бесплатные варианты первыми.
6. В конце каждого занятия пиши строку «ПРОГРЕСС: неделя X, сделано: ..., следующий шаг: ...».
7. Команды:
   /план — план на 14 недель под мои цели (опирайся на программу курса)
   /видео — помоги с раскадровкой и промптами для ролика
   /разбор — проанализируй мой промпт по формуле роль-контекст-задача-формат-примеры-ограничения
   /код — вайбкодинг: составь ТЗ, выбери инструмент и веди меня по шагам; одна правка за раз, проверка после каждой
   /тест — 5 практических вопросов по пройденному
8. Этика: не помогай делать дипфейки реальных людей без согласия и вводящий в заблуждение контент.
9. Если «О себе» не указано — сначала задай 3 коротких уточняющих вопроса.
10. Ученик свободно знает английский — промпты для картинок и видео пиши на английском (так модели работают лучше), объяснения — по-русски. Англоязычные курсы и документацию можно советовать без перевода.

АКТУАЛЬНАЯ СПРАВКА (ноябрь 2026, из программы курса — опирайся на неё, твои собственные знания могут быть устаревшими):
- Видео: Google Veo 3.1 (реализм, звук), Kling 3.0 (кино, мульти-кадры, липсинк), Seedance 2.0 (движение, следование промпту), Runway (генерация + монтаж), Hailuo и Grok Imagine (дёшево, быстро), открытые Wan и LTX через ComfyUI.
- Картинки: Midjourney, Nano Banana (Gemini), GPT Image, Flux.
- Голос/музыка/монтаж: ElevenLabs, Suno, HeyGen/Synthesia (аватары), CapCut, DaVinci Resolve.
- Вайбкодинг: Claude (артефакты), Google AI Studio, Bolt, Lovable, Replit, Cursor, Claude Code.
- Автоматизация и агенты: n8n, Make, Hugging Face Agents Course, Microsoft Generative AI for Beginners, DeepLearning.AI, Anthropic Academy.
- Локально: Ollama, LM Studio, ComfyUI.
- Видео-промпт: [камера] + [субъект] + [действие] + [место] + [свет] + [стиль] + [звук]; одна сцена = одно действие; лучше image-to-video.

ЧЕСТНОСТЬ (обязательно):
- Ты ИИ-наставник, не человек.
- НЕ выдумывай ссылки, цены, лимиты бесплатных тарифов, названия кнопок и версии моделей. Если не уверен — прямо скажи «проверь на сайте сервиса», и не называй конкретных чисел.
- Не упоминай модели и версии, которых нет в справке, как будто точно знаешь, что они существуют.
- Ты НЕ видишь фото, видео и файлы, которые ученик присылает в бот: их смотрит преподаватель. Если ученик спрашивает про свою картинку или видео — попроси описать словами или вставить промпт; никогда не делай вид, что видел.
- Не обещай результат, который нельзя гарантировать («точно станет вирусным», «заработаешь X»).
- Если не знаешь ответа — так и скажи и предложи, где проверить.

ФОРМАТ TELEGRAM: простой текст и эмодзи, без markdown-разметки (* # _). Промпты для копирования — отдельным абзацем. До 2500 символов."""

STEPS = {
    1: "Самое начало: merhaba, nasılsın, iyiyim, teşekkür ederim, evet/hayır, benim adım. Вопросы да/нет или с вариантами («Çay mı, kahve mi?»). Твоя турецкая фраза — максимум 5 слов.",
    2: "О себе: Ben … -ım, nereli, kaç yaşında, nerede oturuyorsun. Числа 1–20. Фраза до 6 слов.",
    3: "Настоящее время -iyor частых глаголов, var/yok, -da/-de. Фраза до 7 слов.",
    4: "Повседневные темы: семья, еда, город, покупки. Вопросы ne, nerede, ne zaman, kaç para. Фраза до 8 слов.",
    5: "Ответы из 2–3 предложений. Прошедшее -dı и будущее -acak. Распорядок дня, планы.",
    6: "Просьбы и вежливые формы (-abilir miyim?), сравнение (daha, en). Фразы до 10 слов.",
    7: "Разговор на знакомые темы, 3–4 предложения. Широкое время, -malı, -ebil.",
    8: "Свободнее: мнения (bence…, çünkü…), пересказ (-miş), условия (-sa). Больше турецкого, меньше перевода.",
    9: "Почти свободный разговор, причастия (-an, -dık), перевод — только для новых слов.",
    10: "Уверенный A2–B1: разговор целиком по-турецки, перевод — только если просит.",
}


def step_block(step: int, known_words: list[str]) -> str:
    words = ", ".join(known_words[-60:]) if known_words else "пока нет"
    return (f"СТУПЕНЬ СЛОЖНОСТИ: {step} из 10. {STEPS[step]}\n"
            f"Слова, которые он уже изучал в боте (опирайся на них, новые слова вводи по одному, с переводом): {words}.")


LEVEL_NAMES = {1: "A0–A1", 2: "A1", 3: "A1", 4: "A1–A2", 5: "A2", 6: "A2", 7: "A2", 8: "A2–B1"}


def build_system(track: str, week: int, task: str, profile: str, step: int = 1, known_words=None) -> str:
    profile = profile.strip() or "не указано"
    task = re.sub(r"<[^>]+>", "", task)
    if track in ("es", "tr"):
        return TUTOR_ES.format(week=week, task=task, profile=profile,
                               level_block=step_block(step, known_words or []))
    return MENTOR_AI.format(week=week, task=task, profile=profile)


async def converse(system: str, messages: list, max_tokens=1500) -> str:
    return await with_retry(lambda: _converse_once(system, messages, max_tokens))


async def _converse_once(system: str, messages: list, max_tokens=1500) -> str:
    """Диалог с историей: messages = [{'role': 'user'|'assistant', 'content': ...}]."""
    if GROQ_KEY:
        return await groq_chat([{"role": "system", "content": system}] + messages, max_tokens, 0.5)
    if OPENAI_KEY and not ANTHROPIC_KEY:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_KEY)
        model = OPENAI_MODEL
        r = await client.chat.completions.create(
            model=model, max_tokens=max_tokens, temperature=0.5,
            messages=[{"role": "system", "content": system}] + messages)
        return r.choices[0].message.content or ""
    from anthropic import AsyncAnthropic
    r = await AsyncAnthropic(api_key=ANTHROPIC_KEY).messages.create(
        model=CLAUDE_MODEL, max_tokens=max_tokens, system=system, messages=messages)
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


SCORE_RE = re.compile(r"ОЦЕНКА:\s*(\d{1,2})\s*/\s*10", re.I)


def split_score(text: str):
    """Достаёт оценку из последней строки и убирает её из текста для студента."""
    m = SCORE_RE.search(text)
    if not m:
        return text.strip(), None
    score = max(1, min(10, int(m.group(1))))
    return (text[:m.start()] + text[m.end():]).strip(), score


def clean_md(text: str) -> str:
    """Убирает markdown, который модель могла вставить, и экранирует HTML."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"\1", text)   # *курсив*
    text = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"\1", text)       # _курсив_
    text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text).strip()
    return esc(text)


NOISE = ("amara.org", "altyazı", "izlediğiniz için teşekkür", "abone ol", "thanks for watching", "müzik")


def looks_like_noise(text: str) -> bool:
    """Whisper на тишине иногда «слышит» фразы из субтитров YouTube — отсекаем их."""
    t = text.strip().lower()
    if len(t) < 2:
        return True
    return any(n in t for n in NOISE) and len(t) < 80
