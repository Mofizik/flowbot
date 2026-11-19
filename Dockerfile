# Бот flowgen в контейнере: docker compose up -d
FROM python:3.12-slim

# Liberation Serif по ширине букв совпадает с Times New Roman — схемы выглядят так же, как на Mac
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-bot.txt ./
RUN pip install --no-cache-dir -r requirements-bot.txt

COPY flowgen ./flowgen
COPY flowgen_bot ./flowgen_bot

ENV PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    DB_PATH=/app/data/flowgen_bot.sqlite3
RUN mkdir -p /app/data
# шрифты matplotlib ищутся один раз при сборке, а не при первом запросе
RUN python -c "import matplotlib.font_manager as f; f.findfont('Liberation Serif')"

CMD ["python", "-m", "flowgen_bot"]
