#!/bin/bash
# Быстрый запуск «Не Школы Барабанов»

set -e
cd "$(dirname "$0")"

echo "=== Не Школа Барабанов ==="

# Виртуальное окружение
if [ ! -d ".venv" ]; then
  echo "→ Создаём виртуальное окружение..."
  python3 -m venv .venv
fi
source .venv/bin/activate

# Зависимости
echo "→ Устанавливаем зависимости..."
pip install -q -r requirements.txt

# Миграции
echo "→ Применяем миграции..."
alembic upgrade head

echo ""
echo "✅ Готово! Запускаем сервер..."
echo "   Админ-панель: http://localhost:8000/admin"
echo "   Логин: admin / Пароль: admin (менять в .env)"
echo ""
echo "   Для запуска бота в отдельном терминале:"
echo "   source .venv/bin/activate && python -m app.bot.telegram_bot"
echo ""

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
