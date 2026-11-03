"""Находит Telegram ID преподавателя автоматически: читает последнее сообщение,
которое отправили боту (нажатие Start), и вписывает ID в .env.
Запуск: python fix_admin.py  (из папки бота)"""
import json
import re
import ssl
import sys
import urllib.request

ENV = ".env"


def read_env():
    d = {}
    for line in open(ENV, encoding="utf-8"):
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def main():
    env = read_env()
    if env.get("ADMIN_ID", "").isdigit():
        print(f"✅ ADMIN_ID уже указан: {env['ADMIN_ID']}")
        return 0
    token = env.get("BOT_TOKEN", "")
    if not re.fullmatch(r"\d+:[\w-]{20,}", token):
        print("❌ BOT_TOKEN не похож на токен от @BotFather (вид: 1234567890:AAE...).")
        return 1
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=20, context=ctx()) as r:
            data = json.load(r)
    except Exception as e:
        print(f"❌ Не могу связаться с Telegram: {e}")
        return 1
    if not data.get("ok"):
        print("❌ Telegram не принял токен. Проверь BOT_TOKEN.")
        return 1
    users = []
    for u in data.get("result", []):
        msg = u.get("message") or u.get("edited_message") or {}
        frm = msg.get("from")
        if frm and not frm.get("is_bot"):
            users.append(frm)
    if not users:
        print("❗ Боту ещё никто не писал. Открой бота в Telegram, нажми START (или напиши /start)")
        print("   и запусти установку снова — я сам найду твой ID.")
        return 2
    me = users[-1]
    uid = me["id"]
    name = (me.get("first_name", "") + " " + me.get("last_name", "")).strip()
    lines = open(ENV, encoding="utf-8").read().splitlines()
    out, done = [], False
    for l in lines:
        if l.strip().startswith("ADMIN_ID"):
            out.append(f"ADMIN_ID={uid}")
            done = True
        else:
            out.append(l)
    if not done:
        out.append(f"ADMIN_ID={uid}")
    open(ENV, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(f"✅ Нашёл тебя: {name} (@{me.get('username', '—')}), ID {uid} — вписал в .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
