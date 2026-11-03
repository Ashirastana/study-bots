"""Тренажёры турецкой грамматики, которые строятся по правилам (без ИИ — значит без выдумок):
гармония гласных в окончаниях и спряжение глаголов (сейчас / вчера / завтра)."""
import random

from tr_vocab_data import WORDS, POS

BACK, FRONT = set("aıou"), set("eiöü")
VOWELS = BACK | FRONT
VOICELESS = set("çfhkpsşt")   # «fıstıkçı şahap»: после них d → t


def tr_lower(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def last_vowel(w: str) -> str:
    for ch in reversed(tr_lower(w)):
        if ch in VOWELS:
            return ch
    return "e"


def h2(w: str) -> str:
    """Двухвариантная гармония: a / e."""
    return "a" if last_vowel(w) in BACK else "e"


def h4(w: str) -> str:
    """Четырёхвариантная гармония: ı / i / u / ü."""
    return {"a": "ı", "ı": "ı", "o": "u", "u": "u", "e": "i", "i": "i", "ö": "ü", "ü": "ü"}[last_vowel(w)]


def ends_vowel(w: str) -> bool:
    return tr_lower(w)[-1] in VOWELS


def D(w: str) -> str:
    return "t" if tr_lower(w)[-1] in VOICELESS else "d"


# ---------------- окончания существительных ----------------

CASES = {
    "pl":  ("множественное число", "-lar / -ler", "много"),
    "loc": ("где? (местный падеж)", "-da / -de / -ta / -te", "в, на"),
    "abl": ("откуда? (исходный падеж)", "-dan / -den / -tan / -ten", "из, от"),
    "dat": ("куда? (направительный падеж)", "-(y)a / -(y)e", "в, к"),
    "acc": ("кого? что? (определённый объект)", "-(y)ı / -(y)i / -(y)u / -(y)ü", "этот конкретный"),
    "p1":  ("мой (принадлежность)", "-(ı)m / -(i)m / -(u)m / -(ü)m", "мой"),
    "q":   ("вопрос «...ли?»", "mı / mi / mu / mü", "ли?"),
}
VOWEL_START = {"dat", "acc", "p1"}


def noun_form(word: str, case: str) -> str:
    w = word
    if case == "pl":
        return w + "l" + h2(w) + "r"
    if case == "loc":
        return w + D(w) + h2(w)
    if case == "abl":
        return w + D(w) + h2(w) + "n"
    if case == "dat":
        return w + ("y" if ends_vowel(w) else "") + h2(w)
    if case == "acc":
        return w + ("y" if ends_vowel(w) else "") + h4(w)
    if case == "p1":
        return w + ("" if ends_vowel(w) else h4(w)) + "m"
    if case == "q":
        return w + " m" + h4(w)
    raise ValueError(case)


def _noun_ok(i: int, case: str) -> bool:
    w = WORDS[i][0]
    if POS[i] != "n" or " " in w or "?" in w or "!" in w or not w.islower():
        return False
    if case in VOWEL_START and tr_lower(w)[-1] in "pçtkg":
        return False  # kitap → kitabı, ağaç → ağacı: чередование — не путаем ученика
    if case in VOWEL_START and tr_lower(w).endswith("nk"):
        return False
    return len(w) >= 2


def _wrong_variants(word: str, case: str, right: str) -> list[str]:
    """Типичные ошибки: не та гармония, не то оглушение, лишняя/пропущенная y."""
    out = set()
    base = word
    if case == "pl":
        out |= {base + "lar", base + "ler"}
    elif case in ("loc", "abl"):
        tail = "n" if case == "abl" else ""
        out |= {base + c + v + tail for c in "dt" for v in "ae"}
    elif case == "dat":
        out |= {base + y + v for y in ("", "y") for v in "ae"}
    elif case == "acc":
        out |= {base + y + v for y in ("", "y") for v in "ıiuü"}
    elif case == "p1":
        out |= {base + v + "m" for v in "ıiuü"} | {base + "m"}
    elif case == "q":
        out |= {base + " m" + v for v in "ıiuü"}
    out.discard(right)
    return list(out)


def harmony_question(known_idx: list[int] | None = None):
    """Вопрос: слово + окончание → выбрать правильную форму. Возвращает dict или None."""
    pool_cases = list(CASES)
    for _ in range(60):
        case = random.choice(pool_cases)
        cand = [i for i in (known_idx or range(len(WORDS))) if _noun_ok(i, case)]
        if not cand:
            cand = [i for i in range(len(WORDS)) if _noun_ok(i, case)]
        i = random.choice(cand)
        word = WORDS[i][0]
        right = noun_form(word, case)
        wrong = _wrong_variants(word, case, right)
        if len(wrong) < 3:
            continue
        opts = random.sample(wrong, 3) + [right]
        random.shuffle(opts)
        title, rule, meaning = CASES[case]
        return {"idx": i, "word": word, "case": case, "title": title, "rule": rule,
                "right": right, "options": opts, "ru": WORDS[i][1]}
    return None


def harmony_explain(q: dict) -> str:
    w, case = q["word"], q["case"]
    lv = last_vowel(w)
    parts = [f"Последняя гласная в <b>{w}</b> — «{lv}»"]
    if case in ("pl", "loc", "abl", "dat"):
        parts.append("после a, ı, o, u → <b>a</b>; после e, i, ö, ü → <b>e</b>")
    else:
        parts.append("после a, ı → <b>ı</b>; e, i → <b>i</b>; o, u → <b>u</b>; ö, ü → <b>ü</b>")
    if case in ("loc", "abl"):
        parts.append("после глухих f, s, t, k, ç, ş, h, p («fıstıkçı şahap») d → <b>t</b>"
                     if tr_lower(w)[-1] in VOICELESS else "слово кончается не на глухую → <b>d</b>")
    if case in VOWEL_START and ends_vowel(w):
        parts.append("слово кончается на гласную → между ними вставляется <b>y</b>"
                     if case != "p1" else "слово кончается на гласную → просто <b>-m</b>")
    return "; ".join(parts) + "."


# ---------------- спряжение глаголов ----------------

PERSONS = ["ben", "sen", "o", "biz", "siz", "onlar"]
PERSON_RU = {"ben": "я", "sen": "ты", "o": "он/она", "biz": "мы", "siz": "вы", "onlar": "они"}
TENSES = {"pres": "сейчас (настоящее, -iyor)", "past": "вчера (прошедшее, -dı)", "fut": "завтра (будущее, -acak)"}


def split_verb(inf: str):
    """'yardım etmek' → ('yardım ', 'et'); 'gelmek' → ('', 'gel')."""
    inf = inf.strip()
    pre, _, last = inf.rpartition(" ")
    pre = pre + " " if pre else ""
    if not (last.endswith("mak") or last.endswith("mek")):
        raise ValueError(inf)
    return pre, last[:-3]


def _soft(stem: str) -> str:
    """git → gid, et → ed, tat → tad (перед гласной)."""
    s = tr_lower(stem)
    if s in ("git", "tat", "güt") or (s.endswith("et") and s not in ("yet", "öt", "it", "bit")):
        return stem[:-1] + "d"
    return stem


def conjugate(inf: str, person: str, tense: str, neg: bool = False) -> str:
    pre, st = split_verb(inf)
    pi = PERSONS.index(person)
    if tense == "pres":
        if neg:
            core = st + "m" + h4(st)
        elif ends_vowel(st):
            base = st[:-1]
            vowel = h4(base) if any(c in VOWELS for c in tr_lower(base)) else "i"  # de→diyor, ye→yiyor
            core = base + vowel
        else:
            core = _soft(st) + h4(st)
        core += "yor"
        end = ["um", "sun", "", "uz", "sunuz", "lar"][pi]
        return pre + core + end
    if tense == "past":
        base = st + ("m" + h2(st) if neg else "")
        v = h4(base)
        end = ["m", "n", "", "k", "n" + v + "z", "l" + h2(base) + "r"][pi]
        return pre + base + D(base) + v + end
    if tense == "fut":
        if neg:
            base = st + "m" + h2(st) + "y"
        elif tr_lower(st) in ("de", "ye"):
            base = st[0] + "iy"          # diyecek, yiyecek
        elif ends_vowel(st):
            base = st + "y"
        else:
            base = _soft(st)
        a = h2(base)
        cak = "c" + a + "k"
        if pi == 0:
            return pre + base + a + "c" + a + "ğ" + h4(a) + "m"
        if pi == 3:
            return pre + base + a + "c" + a + "ğ" + h4(a) + "z"
        end = ["", "s" + h4(a) + "n", "", "", "s" + h4(a) + "n" + h4(a) + "z", "l" + a + "r"][pi]
        return pre + base + a + cak + end
    raise ValueError(tense)


def _verb_ok(i: int) -> bool:
    w = WORDS[i][0]
    return POS[i] == "v" and (w.endswith("mak") or w.endswith("mek")) and "?" not in w


def verb_question(known_idx: list[int] | None = None):
    cand = [i for i in (known_idx or []) if _verb_ok(i)] or [i for i in range(len(WORDS)) if _verb_ok(i)]
    i = random.choice(cand)
    inf = WORDS[i][0]
    person, tense, neg = random.choice(PERSONS), random.choice(list(TENSES)), random.random() < 0.3
    right = conjugate(inf, person, tense, neg)
    wrong = set()
    for _ in range(40):
        p2, t2, n2 = random.choice(PERSONS), random.choice(list(TENSES)), random.random() < 0.3
        f = conjugate(inf, p2, t2, n2)
        if f != right:
            wrong.add(f)
        if len(wrong) >= 8:
            break
    opts = random.sample(sorted(wrong), 3) + [right]
    random.shuffle(opts)
    return {"idx": i, "inf": inf, "ru": WORDS[i][1], "person": person, "tense": tense, "neg": neg,
            "right": right, "options": opts}


# ---------------- проверка написанного ----------------

PLAIN = str.maketrans("ıiğüşöç", "iigusoc")


def check_spelling(answer: str, right: str) -> str:
    """'ok' — точно; 'almost' — верно, но без турецких букв (ı ş ç ğ ö ü); 'wrong' — ошибка."""
    a = " ".join(tr_lower(answer).strip(" .!?").split())
    r = " ".join(tr_lower(right).strip(" .!?").split())
    if a == r:
        return "ok"
    if a.translate(PLAIN) == r.translate(PLAIN):
        return "almost"
    return "wrong"
