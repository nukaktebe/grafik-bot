# График смен — Telegram-бот

Бот для начальника и сотрудников склада: график на месяц, буквы функционала, подработка, таблица на печать.

## Что нужно

- Python 3.9+
- токен бота от [@BotFather](https://t.me/BotFather)

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Создайте файл `.env` (его нет в репозитории специально):

```
BOT_TOKEN=токен_от_BotFather
ADMIN_IDS=ваш_telegram_id
```

Затем:

```bash
python main.py
```

Остановка: `Ctrl+C`.

Файл `.env` на GitHub не загружается — в нём секретный ключ бота.
