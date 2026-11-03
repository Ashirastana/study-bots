"""Турецкий: слова дня + интервальное повторение (система Лейтнера, как в Anki).
Коробка 1→через 1 день, 2→3, 3→7, 4→14, 5→30, дальше 60. Ошибка — назад в коробку 1."""
import random
from datetime import date, timedelta

from tr_vocab_data import WORDS

INTERVALS = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30, 6: 60}
SCHEMA = """
CREATE TABLE IF NOT EXISTS word_progress(
  student_id INTEGER, idx INTEGER, box INTEGER DEFAULT 1, due TEXT,
  introduced TEXT, right_n INTEGER DEFAULT 0, wrong_n INTEGER DEFAULT 0,
  PRIMARY KEY(student_id, idx));
"""


class Vocab:
    def __init__(self, conn, per_day=10):
        self.c = conn
        self.per_day = per_day
        self.c.executescript(SCHEMA)
        self.c.commit()

    def introduced_today(self, uid, day: date):
        rows = self.c.execute("SELECT idx FROM word_progress WHERE student_id=? AND introduced=? ORDER BY idx",
                              (uid, day.isoformat())).fetchall()
        return [r[0] for r in rows]

    def give_new(self, uid, day: date):
        """Выдаёт 10 новых слов на сегодня (повторный вызов в тот же день вернёт те же)."""
        already = self.introduced_today(uid, day)
        if already:
            return already
        start = self.c.execute("SELECT COALESCE(MAX(idx)+1,0) FROM word_progress WHERE student_id=?",
                               (uid,)).fetchone()[0]
        new = list(range(start, min(start + self.per_day, len(WORDS))))
        for i in new:
            self.c.execute("INSERT OR IGNORE INTO word_progress(student_id,idx,box,due,introduced) VALUES(?,?,1,?,?)",
                           (uid, i, day.isoformat(), day.isoformat()))
        self.c.commit()
        return new

    def due(self, uid, day: date, limit=30):
        rows = self.c.execute("SELECT idx FROM word_progress WHERE student_id=? AND due<=? ORDER BY box, due LIMIT ?",
                              (uid, day.isoformat(), limit)).fetchall()
        return [r[0] for r in rows]

    def answer(self, uid, idx, correct: bool, day: date):
        r = self.c.execute("SELECT box FROM word_progress WHERE student_id=? AND idx=?", (uid, idx)).fetchone()
        if not r:
            return
        box = min(r[0] + 1, 6) if correct else 1
        nxt = day + timedelta(days=INTERVALS[box] if correct else 1)
        col = "right_n" if correct else "wrong_n"
        self.c.execute(f"UPDATE word_progress SET box=?, due=?, {col}={col}+1 WHERE student_id=? AND idx=?",
                       (box, nxt.isoformat(), uid, idx))
        self.c.commit()

    def question(self, idx):
        """Турецкое слово + 4 варианта перевода (1 верный)."""
        es, ru, kk = WORDS[idx]
        pool = [i for i in range(len(WORDS)) if i != idx and WORDS[i][1] != ru]
        wrong = random.sample(pool, 3)
        opts = wrong + [idx]
        random.shuffle(opts)
        return es, opts

    def stats(self, uid):
        r = self.c.execute(
            "SELECT COUNT(*), SUM(box>=3), SUM(right_n), SUM(wrong_n) FROM word_progress WHERE student_id=?",
            (uid,)).fetchone()
        total, learned, right, wrong = (r[0] or 0), (r[1] or 0), (r[2] or 0), (r[3] or 0)
        acc = round(100 * right / (right + wrong)) if (right + wrong) else None
        return dict(seen=total, learned=learned, accuracy=acc, left=len(WORDS) - total)

    def anki_export(self, uid) -> str:
        rows = self.c.execute("SELECT idx FROM word_progress WHERE student_id=? ORDER BY idx", (uid,)).fetchall()
        return "\n".join(f"{WORDS[r[0]][0]};{WORDS[r[0]][1]}" for r in rows)


def word_card(idx, show_kk: bool = True) -> str:
    tr, ru, kk = WORDS[idx]
    return f"<b>{tr}</b> — {ru}" + (f"  <i>🇰🇿 {kk}</i>" if kk and show_kk else "")


def distractors(idx, n=3, pool=None):
    """Случайные другие слова (с другим переводом) — для игр."""
    src = [i for i in (pool or range(len(WORDS))) if i != idx and WORDS[i][1] != WORDS[idx][1]]
    if len(src) < n:
        src = [i for i in range(len(WORDS)) if i != idx and WORDS[i][1] != WORDS[idx][1]]
    return random.sample(src, n)
