FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Asia/Almaty DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg supervisor tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN chmod +x run_bot.sh start.sh && mkdir -p /data
CMD ["/app/start.sh"]
