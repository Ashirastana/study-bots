"""Хранилище прогресса в SQLite (файл bot.db рядом с ботом)."""
import sqlite3
from datetime import date, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS students(
  id INTEGER PRIMARY KEY, name TEXT, username TEXT,
  start_date TEXT NOT NULL, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS reports(
  student_id INTEGER, day TEXT, es_done INTEGER DEFAULT 0,
  ai_done INTEGER DEFAULT 0, hours REAL DEFAULT 0,
  PRIMARY KEY(student_id, day));
CREATE TABLE IF NOT EXISTS works(
  id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER,
  day TEXT, kind TEXT, content TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER,
  role TEXT, content TEXT);
CREATE TABLE IF NOT EXISTS voice_checks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER,
  day TEXT, transcript TEXT, score INTEGER, feedback TEXT);
"""


class DB:
    def __init__(self, path="bot.db"):
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        cols = [r[1] for r in self.c.execute("PRAGMA table_info(students)")]
        if "profile" not in cols:
            self.c.execute("ALTER TABLE students ADD COLUMN profile TEXT DEFAULT ''")
        self.c.commit()

    # --- профиль и история диалога ---
    def set_profile(self, uid, text):
        self.c.execute("UPDATE students SET profile=? WHERE id=?", (text, uid))
        self.c.commit()

    def add_msg(self, uid, role, content):
        self.c.execute("INSERT INTO messages(student_id,role,content) VALUES(?,?,?)", (uid, role, content))
        self.c.execute("DELETE FROM messages WHERE student_id=? AND id NOT IN "
                       "(SELECT id FROM messages WHERE student_id=? ORDER BY id DESC LIMIT 40)", (uid, uid))
        self.c.commit()

    def history(self, uid, limit=16):
        rows = self.c.execute("SELECT role, content FROM messages WHERE student_id=? ORDER BY id DESC LIMIT ?",
                              (uid, limit)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def clear_history(self, uid):
        self.c.execute("DELETE FROM messages WHERE student_id=?", (uid,))
        self.c.commit()

    # --- студенты ---
    def add_student(self, uid, name, username, start: date):
        self.c.execute(
            "INSERT INTO students(id,name,username,start_date) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, username=excluded.username, active=1",
            (uid, name, username, start.isoformat()))
        self.c.commit()

    def get_student(self, uid):
        return self.c.execute("SELECT * FROM students WHERE id=?", (uid,)).fetchone()

    def students(self):
        return self.c.execute("SELECT * FROM students WHERE active=1").fetchall()

    def set_start(self, uid, start: date):
        self.c.execute("UPDATE students SET start_date=? WHERE id=?", (start.isoformat(), uid))
        self.c.commit()

    def deactivate(self, uid):
        self.c.execute("UPDATE students SET active=0 WHERE id=?", (uid,))
        self.c.commit()

    def day_index(self, uid, today: date) -> int:
        s = self.get_student(uid)
        return max(0, (today - date.fromisoformat(s["start_date"])).days)

    # --- отчёты ---
    def _ensure(self, uid, day):
        self.c.execute("INSERT OR IGNORE INTO reports(student_id,day) VALUES(?,?)", (uid, day.isoformat()))

    def mark(self, uid, day: date, field: str):
        assert field in ("es_done", "ai_done")
        self._ensure(uid, day)
        self.c.execute(f"UPDATE reports SET {field}=1 WHERE student_id=? AND day=?", (uid, day.isoformat()))
        self.c.commit()

    def add_hours(self, uid, day: date, hours: float):
        self._ensure(uid, day)
        self.c.execute("UPDATE reports SET hours=hours+? WHERE student_id=? AND day=?", (hours, uid, day.isoformat()))
        self.c.commit()

    def report(self, uid, day: date):
        return self.c.execute("SELECT * FROM reports WHERE student_id=? AND day=?", (uid, day.isoformat())).fetchone()

    def has_any_report(self, uid, day: date) -> bool:
        r = self.report(uid, day)
        w = self.c.execute("SELECT 1 FROM works WHERE student_id=? AND day=?", (uid, day.isoformat())).fetchone()
        v = self.c.execute("SELECT 1 FROM voice_checks WHERE student_id=? AND day=?", (uid, day.isoformat())).fetchone()
        return bool((r and (r["es_done"] or r["ai_done"] or r["hours"])) or w or v)

    def add_work(self, uid, day: date, kind, content):
        self.c.execute("INSERT INTO works(student_id,day,kind,content) VALUES(?,?,?,?)",
                       (uid, day.isoformat(), kind, content))
        self.c.commit()

    def add_voice(self, uid, day: date, transcript, score, feedback):
        self.c.execute("INSERT INTO voice_checks(student_id,day,transcript,score,feedback) VALUES(?,?,?,?,?)",
                       (uid, day.isoformat(), transcript, score, feedback))
        self.c.commit()

    def recent_voice(self, uid, limit=5):
        return self.c.execute("SELECT * FROM voice_checks WHERE student_id=? ORDER BY id DESC LIMIT ?",
                              (uid, limit)).fetchall()

    # --- статистика ---
    def stats(self, uid, start: date, end: date) -> dict:
        a, b = start.isoformat(), end.isoformat()
        r = self.c.execute(
            "SELECT COALESCE(SUM(es_done),0) es, COALESCE(SUM(ai_done),0) ai, COALESCE(SUM(hours),0) h "
            "FROM reports WHERE student_id=? AND day BETWEEN ? AND ?", (uid, a, b)).fetchone()
        works = self.c.execute("SELECT * FROM works WHERE student_id=? AND day BETWEEN ? AND ? ORDER BY id",
                               (uid, a, b)).fetchall()
        v = self.c.execute("SELECT COUNT(*) n, AVG(score) s FROM voice_checks WHERE student_id=? AND day BETWEEN ? AND ?",
                           (uid, a, b)).fetchone()
        total_h = self.c.execute("SELECT COALESCE(SUM(hours),0) FROM reports WHERE student_id=?", (uid,)).fetchone()[0]
        days = (end - start).days + 1
        active = sum(1 for i in range(days) if self.has_any_report(uid, start + timedelta(days=i)))
        return dict(es=r["es"], ai=r["ai"], hours=r["h"], works=works, voices=v["n"],
                    avg_score=v["s"], total_hours=total_h, days=days, active_days=active)

    def missed_streak(self, uid, today: date, start: date) -> int:
        """Сколько дней подряд до вчера включительно без отчёта."""
        n, d = 0, today - timedelta(days=1)
        while d >= start and not self.has_any_report(uid, d):
            n += 1
            d -= timedelta(days=1)
        return n
