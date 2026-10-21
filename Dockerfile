FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY birthdaybot ./birthdaybot

RUN useradd --system --uid 1000 bot && mkdir -p /app/data /app/logs && chown bot /app/data /app/logs
USER bot

ENV DB_PATH=/app/data/birthdays.db \
    LOG_DIR=/app/logs
VOLUME ["/app/data", "/app/logs"]
CMD ["python", "-m", "birthdaybot"]
