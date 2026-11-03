#!/bin/bash
# Собирает конфиг supervisord из bots.list: каждый бот — отдельный процесс,
# упал один — перезапускается только он.
set -euo pipefail
CONF=/tmp/supervisord.conf
cat > $CONF <<HDR
[supervisord]
nodaemon=true
user=root
logfile=/dev/null
logfile_maxbytes=0
pidfile=/tmp/supervisord.pid
HDR
grep -vE '^\s*(#|$)' /app/bots.list | while read -r NAME PREFIX; do
cat >> $CONF <<PRG

[program:$NAME]
command=/app/run_bot.sh $NAME $PREFIX
autorestart=true
startsecs=10
startretries=999999
stopwaitsecs=15
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
redirect_stderr=true
PRG
done
echo "Запускаю ботов: $(grep -vE '^\s*(#|$)' /app/bots.list | awk '{print $1}' | xargs)"
exec supervisord -c $CONF
