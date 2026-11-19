#!/bin/sh
# Запуск бота на Mac/Linux: ./run_bot.sh
# В первый раз создаёт виртуальное окружение .venv и ставит зависимости.
set -e
cd "$(dirname "$0")"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Создан файл .env — впишите в него BOT_TOKEN от @BotFather и запустите ещё раз."
    exit 1
fi
if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements-bot.txt
fi
exec .venv/bin/python -m flowgen_bot
