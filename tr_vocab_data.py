"""Турецкий словарь бота: ~1300 слов и фраз от самых нужных к более сложным.
WORDS — список (турецкий, русский, казахская подсказка). POS — часть речи по индексу."""
from tr_words_1 import RAW as R1
from tr_words_2 import RAW as R2
from tr_words_3 import RAW as R3
from tr_words_4 import RAW as R4
from tr_words_5 import RAW as R5


def load():
    words, pos, seen = [], [], set()
    for raw in (R1, R2, R3, R4, R5):
        for line in raw.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) != 4 or not parts[0] or parts[0].lower() in seen:
                continue
            seen.add(parts[0].lower())
            words.append((parts[0], parts[1], parts[3]))
            pos.append(parts[2])
    return words, pos


WORDS, POS = load()
