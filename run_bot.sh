#!/bin/bash
# Запуск одного бота: своя папка данных, свои настройки, свой токен.
set -euo pipefail
NAME="$1"; PREFIX="$2"
DATA="${DATA_DIR:-/data}/$NAME"
mkdir -p "$DATA"
# Первая установка: переносим прогресс учеников с Mac (если на диске ещё нет базы)
if [ ! -f "$DATA/bot.db" ] && [ -f "/app/seed/$NAME.db" ]; then
  cp "/app/seed/$NAME.db" "$DATA/bot.db" && chmod 644 "$DATA/bot.db"
  echo "[$NAME] перенесена база с Mac"
fi
set -a; source "/app/bots/$NAME/settings.env"; set +a
TOKEN_VAR="${PREFIX}_BOT_TOKEN"
if [ -z "${!TOKEN_VAR:-}" ]; then
  echo "[$NAME] нет секрета $TOKEN_VAR — бот не запущен"; sleep 3600; exit 1
fi
export BOT_TOKEN="${!TOKEN_VAR}"
# Свой ключ ИИ для бота (необязательно), иначе общий GROQ_API_KEY
KEY_VAR="${PREFIX}_GROQ_API_KEY"; [ -n "${!KEY_VAR:-}" ] && export GROQ_API_KEY="${!KEY_VAR}"
export DB_PATH="$DATA/bot.db"
cd "$DATA"   # временные файлы бота — в его папке
exec python /app/app/bot.py
