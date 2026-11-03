FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Asia/Almaty DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg supervisor tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app/requirements.txt app/requirements.txt
RUN pip install -r app/requirements.txt
COPY app/ app/
COPY bots/ bots/
COPY seed/ seed/
COPY run_bot.sh start.sh ./
RUN chmod +x run_bot.sh start.sh && mkdir -p /data
CMD ["/app/start.sh"]
