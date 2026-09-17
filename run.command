#!/bin/bash
# Запускалка бота двойным кликом (macOS).
# Первый запуск: сама установит библиотеку, это займет пару минут.
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null; then
  echo "Python не найден. Установите с https://www.python.org/downloads/ и повторите."
  read -p "Нажмите Enter, чтобы закрыть окно…"
  exit 1
fi

if ! python3 -c "import telegram" 2>/dev/null; then
  echo "Первый запуск: устанавливаю библиотеку Telegram…"
  python3 -m pip install --quiet -r requirements.txt || {
    echo "Не удалось установить библиотеку. Проверьте интернет и попробуйте снова."
    read -p "Нажмите Enter, чтобы закрыть окно…"
    exit 1
  }
  echo "Готово."
fi

echo "Запускаю бота. Чтобы остановить: закройте это окно (или Ctrl+C)."
python3 bot.py

read -p "Бот остановлен. Нажмите Enter, чтобы закрыть окно…"