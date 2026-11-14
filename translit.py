"""Испанская транскрипция русскими буквами с ударением (для ученицы-новичка).

translit("gracias") -> "гра́сиас", translit("me llamo …") -> "мэ я́мо …"

Правила — испанский из Испании, упрощённо для чтения:
  h не читается; j и g перед e/i — «х»; c перед e/i и z — «с» (в Испании это th);
  ll и y перед гласной — «й»; ñ — «нь»; v — «б»; r в начале слова и rr — «рр»;
  qu/gu перед e/i — «к»/«г» (u не читается); e — «э», ie — «ье».
Ударение ставится по правилам испанского (знак над буквой или по окончанию слова).
"""
import re

STRONG = set("aeoáéó")
ACCENTED = set("áéíóú")
VOWELS = set("aeiouáéíóúü")
PLAIN = {"a": "а", "e": "э", "i": "и", "o": "о", "u": "у",
         "á": "а", "é": "э", "í": "и", "ó": "о", "ú": "у", "ü": "у"}
# после «й»/«нь»: a→я, e→е, o→ё нельзя (читается «йо»), поэтому o→«йо»/«ньо», u→ю
SOFT = {"a": "я", "e": "е", "o": "о", "u": "ю", "i": "и"}
CONS = {"b": "б", "v": "б", "d": "д", "f": "ф", "k": "к", "l": "л", "m": "м", "n": "н",
        "p": "п", "s": "с", "t": "т", "z": "с", "x": "кс", "w": "у", "j": "х"}
ACUTE = "́"


def _base(ch):
    return {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u"}.get(ch, ch)


def _vowel_flags(w):
    """Какие позиции слова — гласные (с учётом немой u и y на конце)."""
    flags = []
    for k, ch in enumerate(w):
        nxt = w[k + 1] if k + 1 < len(w) else ""
        prev = w[k - 1] if k else ""
        if ch == "u" and prev in "qg" and nxt in "eiéí":
            flags.append(False)          # que, qui, gue, gui — u молчит
        elif ch == "y":
            # y — гласная, если слово «y» или y на конце после гласной (muy, hoy)
            flags.append(len(w) == 1 or (k == len(w) - 1 and prev in VOWELS))
        else:
            flags.append(ch in VOWELS)
    return flags


def _stress_pos(w, flags):
    """Индекс ударной гласной в слове или None (односложное слово)."""
    nuclei, cur, prev_k = [], [], None
    for k, ch in enumerate(w):
        if not flags[k]:
            if cur:
                nuclei.append(cur)
                cur = []
            continue
        c = "i" if ch == "y" else ch
        if cur and prev_k == k - 1:
            has_strong = any(w[j] in STRONG or w[j] in "íú" for j in cur)
            if (c in STRONG or c in "íú") and has_strong:
                nuclei.append(cur)
                cur = []
        cur.append(k)
        prev_k = k
    if cur:
        nuclei.append(cur)
    if len(nuclei) < 2:
        return None
    for nuc in nuclei:
        for j in nuc:
            if w[j] in ACCENTED:
                return j
    last = w[-1]
    nuc = nuclei[-2] if last in "aeiouns" else nuclei[-1]
    strong = [j for j in nuc if w[j] in STRONG]
    return strong[0] if strong else nuc[-1]


def _word(w):
    low = w.lower()
    if not re.search(r"[a-zñáéíóúü]", low):
        return w
    flags = _vowel_flags(low)
    stress = _stress_pos(low, flags)
    out, k, n = [], 0, len(low)

    def vowel(j, soft=False):
        ch = low[j]
        if ch == "y":                     # y как гласная: «y» = и, muy/hoy = й
            return "и" if n == 1 else "й"
        b = _base(ch)
        prev = low[j - 1] if j else ""
        nxt = low[j + 1] if j + 1 < n else ""
        if soft:
            s = SOFT[b]
        elif b == "i" and flags[j] and ch != "í" and nxt and _base(nxt) == "e" and flags[j + 1] \
                and j > 0 and not flags[j - 1]:
            s = "ь"                       # bien → бьен, siete → сьете
        elif b == "e" and prev == "i" and j >= 2 and not flags[j - 2] and low[j - 1] != "í":
            s = "е"
        elif b == "i" and ch != "í" and prev and flags[j - 1] and _base(prev) in "aeo" \
                and not (nxt and flags[j + 1] if j + 1 < n else False):
            s = "й"                       # seis → сэйс, veinte → бэйнтэ
        else:
            s = PLAIN[ch]
        return s + (ACUTE if j == stress and s not in ("ь", "й") else "")

    while k < n:
        ch = low[k]
        nxt = low[k + 1] if k + 1 < n else ""
        if not ch.isalpha():
            out.append(ch)
            k += 1
            continue
        if flags[k]:
            out.append(vowel(k))
            k += 1
            continue
        if ch == "h":
            k += 1
            continue
        if ch == "c" and nxt == "h":
            out.append("ч")
            k += 2
            continue
        if ch == "c":
            out.append("с" if nxt in "eiéí" else "к")
            k += 1
            continue
        if ch == "q":
            out.append("к")
            k += 1
            continue
        if ch == "g":
            out.append("х" if nxt in "eiéí" else "г")
            k += 1
            continue
        if ch == "u":                      # немая u в que/gue
            k += 1
            continue
        if ch == "r":
            if nxt == "r":
                out.append("рр")
                k += 2
                continue
            out.append("рр" if k == 0 else "р")
            k += 1
            continue
        if (ch == "l" and nxt == "l") or ch == "y":
            k += 2 if ch == "l" else 1
            b = _base(low[k]) if k < n and flags[k] else ""
            if b in ("a", "u"):           # ya → я, yu → ю
                out.append(vowel(k, soft=True))
                k += 1
            elif b == "e":                # ye → йе
                out.append("й" + vowel(k, soft=True))
                k += 1
            elif b in ("o", "i"):         # yo → йо, lli → йи
                out.append("й" + vowel(k))
                k += 1
            else:
                out.append("й")
            continue
        if ch == "ñ":
            k += 1
            if k < n and flags[k]:
                b = _base(low[k])
                out.append("нь" + (vowel(k, soft=True) if b in "aeu" else vowel(k)))
                k += 1
            else:
                out.append("нь")
            continue
        out.append(CONS.get(ch, ch))
        k += 1
    res = "".join(out)
    return res[:1].upper() + res[1:] if w[:1].isupper() else res


def translit(text: str) -> str:
    return re.sub(r"[A-Za-zÑñÁÉÍÓÚÜáéíóúü]+", lambda m: _word(m.group(0)), text)
