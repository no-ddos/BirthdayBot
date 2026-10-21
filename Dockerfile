FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY birthdaybot ./birthdaybot

RUN useradd --system --uid 1000 bot && mkdir -p /app/data && chown bot /app/data
USER bot

ENV DB_PATH=/app/data/birthdays.db
VOLUME ["/app/data"]
CMD ["python", "-m", "birthdaybot"]
