"""Запускает всех учебных ботов из bots.list в ОДНОМ процессе (экономит память на бесплатном
тарифе: библиотека aiogram грузится один раз, а не на каждого бота).

У каждого бота свои настройки (<бот>.env), свой токен (переменная <ПРЕФИКС>_BOT_TOKEN),
своя папка данных и своя база SQLite (/data/<бот>/bot.db). Если бот падает,
перезапускается только он, остальные продолжают работать.
"""
import os
import sys

# Экономия памяти: меньше «арен» у системного malloc (иначе потоки раздувают память).
# Переменная должна быть задана до старта процесса, поэтому перезапускаемся один раз с ней.
if os.environ.get("MALLOC_ARENA_MAX") is None:
    os.environ["MALLOC_ARENA_MAX"] = "2"
    os.execv(sys.executable, [sys.executable] + sys.argv)

import asyncio  # noqa: E402
import importlib.util  # noqa: E402
import logging  # noqa: E402
import signal  # noqa: E402

from aiogram import Dispatcher

HERE = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("launcher")

# Сигналы остановки обрабатывает лаунчер, а не каждый бот по отдельности
_orig_start_polling = Dispatcher.start_polling


async def _start_polling(self, *bots, **kw):
    kw["handle_signals"] = False
    return await _orig_start_polling(self, *bots, **kw)


Dispatcher.start_polling = _start_polling


def data_root() -> str:
    for d in (os.getenv("DATA_DIR"), "/data", os.path.join(HERE, "data")):
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, ".write_test")
            open(test, "w").close()
            os.remove(test)
            return d
        except OSError:
            continue
    raise SystemExit("Нет папки для данных")


def read_env(path: str) -> dict:
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def read_bots() -> list:
    """Строки bots.list: <бот> <ПРЕФИКС> [файл кода, по умолчанию studybot.py]."""
    bots = []
    with open(os.path.join(HERE, "bots.list"), encoding="utf-8") as f:
        for line in f:
            parts = line.split("#", 1)[0].split()
            if len(parts) >= 2:
                bots.append((parts[0], parts[1], parts[2] if len(parts) > 2 else "studybot.py"))
    return bots


def load_bot(name: str, prefix: str, root: str, first: bool = False, script: str = "studybot.py"):
    """Загружает studybot.py как отдельный модуль со своими настройками. None — если нет токена."""
    token = os.getenv(f"{prefix}_BOT_TOKEN", "").strip()
    if not token and first:  # поле «Telegram bot token» на хостинге = первый бот из списка
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log.warning("%s: нет переменной %s_BOT_TOKEN — бот не запущен", name, prefix)
        return None
    folder = os.path.join(root, name)
    os.makedirs(folder, exist_ok=True)
    settings = read_env(os.path.join(HERE, f"{name}.env"))
    settings["BOT_TOKEN"] = token
    settings["DB_PATH"] = os.path.join(folder, "bot.db")
    os.environ.update(settings)  # studybot.py читает настройки при загрузке
    spec = importlib.util.spec_from_file_location(f"bot_{name}", os.path.join(HERE, script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    log.info("%s: загружен, база %s", name, settings["DB_PATH"])
    return mod


async def run_forever(name: str, mod):
    fails = 0
    while True:
        started = asyncio.get_running_loop().time()
        try:
            await mod.main()
            log.warning("%s: опрос завершился, перезапуск", name)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s: упал", name)
        if asyncio.get_running_loop().time() - started > 120:
            fails = 0
        fails += 1
        delay = min(300, 5 * 2 ** min(fails - 1, 6))
        log.info("%s: перезапуск через %s с", name, delay)
        await asyncio.sleep(delay)


async def main():
    root = data_root()
    loaded = []
    for i, (name, prefix, script) in enumerate(read_bots()):
        try:
            mod = load_bot(name, prefix, root, first=(i == 0), script=script)
        except Exception:
            log.exception("%s: не удалось загрузить", name)
            continue
        if mod:
            loaded.append((name, mod))
    if not loaded:
        log.error("Ни один бот не запущен — проверьте переменные *_BOT_TOKEN")
        while True:
            await asyncio.sleep(3600)
    log.info("Данные: %s. Работают: %s", root, ", ".join(n for n, _ in loaded))
    tasks = [asyncio.create_task(run_forever(n, m)) for n, m in loaded]
    loop = asyncio.get_running_loop()
    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(s, lambda: [t.cancel() for t in tasks])
        except (NotImplementedError, RuntimeError):
            pass
    await asyncio.gather(*tasks, return_exceptions=True)
    for _, m in loaded:
        try:
            await m.bot.session.close()
        except Exception:
            pass
    log.info("Остановлено")


if __name__ == "__main__":
    asyncio.run(main())
