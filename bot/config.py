import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(item)
    for item in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
    if item
}
DB_PATH = os.getenv("DB_PATH", "data/schedule.db")
TZ = ZoneInfo("Europe/Moscow")
DEFAULT_NEEDED = 13
# Сколько людей каждого графика должно стоять на складе за один день.
TARGET_DAY_22 = 9
TARGET_DAY_52 = 2
TARGET_DAY_SUTKI = 2
HOURS_22 = "с 8:30 до 20:30"
HOURS_52 = "с 8:30 до 17:30"
HOURS_SUTKI = "с 8:30 до 8:30"

# Буквы функционала на день. code — для кнопок, label — как на бумаге, cap — сколько человек.
# 0 = без ограничения (отпуск).
DAY_MARKS = (
    ("a", "а", 1),
    ("v", "в", 1),
    ("c", "с", 1),
    ("d", "д", 1),
    ("e", "е", 1),
    ("f", "f", 1),
    ("m", "м", 1),
    ("x", "х", 1),
    ("r", "р", 1),
    ("st", "*", 4),
    ("o", "О", 0),
    ("b", "Б", 0),
)
VACATION_MARK = "О"
SICK_MARK = "Б"
STAR_MARK = "*"
ABSENT_MARKS = {VACATION_MARK, SICK_MARK}
MARK_LABEL_BY_CODE = {code: label for code, label, _cap in DAY_MARKS}
MARK_CAP_BY_LABEL = {label: cap for _code, label, cap in DAY_MARKS}
MARK_CODE_BY_LABEL = {label: code for code, label, _cap in DAY_MARKS}
