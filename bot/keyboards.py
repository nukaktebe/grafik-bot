from __future__ import annotations

import calendar
from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import DAY_MARKS, STAR_MARK, VACATION_MARK, SICK_MARK, MARK_CODE_BY_LABEL
from bot.texts import (
    BTN_HELP,
    BTN_MY_SCHEDULE,
    BTN_PRINT,
    BTN_PUBLISH,
    BTN_SCHEDULE,
    BTN_SETTINGS,
    BTN_STAFF,
    BTN_TODAY,
    WEEKDAYS,
)

IGN = "ign"


def boss_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SCHEDULE), KeyboardButton(text=BTN_TODAY)],
            [KeyboardButton(text=BTN_STAFF), KeyboardButton(text=BTN_PUBLISH)],
            [KeyboardButton(text=BTN_PRINT)],
            [KeyboardButton(text=BTN_SETTINGS), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )


def employee_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MY_SCHEDULE)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )


def pending_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_HELP)]],
        resize_keyboard=True,
    )


def _nav_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month += delta
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return year, month


def month_calendar(
    year: int,
    month: int,
    marks: dict[int, str],
    day_prefix: str,
    nav_prefix: str,
    footer_button: tuple[str, str] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        *[InlineKeyboardButton(text=name, callback_data=IGN) for name in WEEKDAYS]
    )
    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    for week in weeks:
        row: list[InlineKeyboardButton] = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data=IGN))
                continue
            label = marks.get(day, str(day))
            row.append(
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{day_prefix}:{year:04d}-{month:02d}-{day:02d}",
                )
            )
        builder.row(*row)
    prev_y, prev_m = _nav_month(year, month, -1)
    next_y, next_m = _nav_month(year, month, 1)
    builder.row(
        InlineKeyboardButton(text="◀", callback_data=f"{nav_prefix}:{prev_y:04d}-{prev_m:02d}"),
        InlineKeyboardButton(text="▶", callback_data=f"{nav_prefix}:{next_y:04d}-{next_m:02d}"),
    )
    if footer_button:
        text, callback_data = footer_button
        builder.row(InlineKeyboardButton(text=text, callback_data=callback_data))
    return builder.as_markup()


def boss_day_keyboard(
    work_date: str,
    holders: dict[str, list[str]],
    open_extra: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for code, label, cap in DAY_MARKS:
        names = holders.get(label, [])
        if label == STAR_MARK:
            text = f"* {len(names)}/4"
        elif label == VACATION_MARK:
            text = f"О {len(names)}" if names else "О отпуск"
        elif label == SICK_MARK:
            text = f"Б {len(names)}" if names else "Б больн."
        elif names:
            short = names[0] if len(names[0]) <= 10 else names[0][:9] + "…"
            text = f"{label} {short}"
        else:
            text = f"{label} —"
        row.append(InlineKeyboardButton(text=text, callback_data=f"blet:{work_date}:{code}"))
        if label in {"д", "х"} or len(row) == 4:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)
    builder.row(
        InlineKeyboardButton(text="− норма", callback_data=f"nneed:{work_date}:-1"),
        InlineKeyboardButton(text="+ норма", callback_data=f"nneed:{work_date}:1"),
    )
    extra_label = "Подработка: открыта" if open_extra else "Открыть пустой день"
    builder.row(InlineKeyboardButton(text=extra_label, callback_data=f"bopen:{work_date}"))
    year, month, _ = work_date.split("-")
    builder.row(
        InlineKeyboardButton(text="◀ К календарю", callback_data=f"bcal:{year}-{month}")
    )
    return builder.as_markup()


def boss_pick_person_keyboard(
    work_date: str,
    code: str,
    people: list[tuple[int, str, str | None]],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user_id, name, current in people:
        suffix = f" · {current}" if current else ""
        short = name if len(name) <= 24 else name[:23] + "…"
        builder.row(
            InlineKeyboardButton(
                text=f"{short}{suffix}",
                callback_data=f"bset:{work_date}:{code}:{user_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="◀ Назад к буквам", callback_data=f"bday:{work_date}")
    )
    return builder.as_markup()


def staff_list_keyboard(users: list[tuple[int, str, str]], ym: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user_id, name, role in users:
        suffix = ""
        if role == "boss":
            suffix = " · нач."
        elif role == "pending":
            suffix = " · заявка"
        builder.row(
            InlineKeyboardButton(
                text=f"{name}{suffix}",
                callback_data=f"emp:{user_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="Добавить по никам Telegram",
            callback_data="addnicks",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Проставить месяц всем по шаблонам",
            callback_data=f"fillall:{ym}",
        )
    )
    return builder.as_markup()


def pending_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data=f"ok:{user_id}"),
                InlineKeyboardButton(text="Отклонить", callback_data=f"no:{user_id}"),
            ]
        ]
    )


def employee_card_keyboard(user_id: int, year: int, month: int, role: str = "employee") -> InlineKeyboardMarkup:
    ym = f"{year:04d}-{month:02d}"
    rows: list[list[InlineKeyboardButton]] = []
    if role == "pending":
        rows.append(
            [
                InlineKeyboardButton(text="Подтвердить", callback_data=f"ok:{user_id}"),
                InlineKeyboardButton(text="Отклонить", callback_data=f"no:{user_id}"),
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(text="2/2 бригада А", callback_data=f"pat:{user_id}:{ym}:22a"),
                InlineKeyboardButton(text="2/2 бригада Б", callback_data=f"pat:{user_id}:{ym}:22c"),
            ],
            [
                InlineKeyboardButton(text="5/2 пн–пт", callback_data=f"pat:{user_id}:{ym}:52"),
            ],
            [
                InlineKeyboardButton(text="Сутки с 1-го", callback_data=f"pat:{user_id}:{ym}:s1"),
                InlineKeyboardButton(text="Сутки с 2-го", callback_data=f"pat:{user_id}:{ym}:s2"),
            ],
            [
                InlineKeyboardButton(text="Сутки с 3-го", callback_data=f"pat:{user_id}:{ym}:s3"),
                InlineKeyboardButton(text="Сутки с 4-го", callback_data=f"pat:{user_id}:{ym}:s4"),
            ],
            [
                InlineKeyboardButton(text="Очистить месяц", callback_data=f"clr:{user_id}:{ym}"),
            ],
            [
                InlineKeyboardButton(text="Переименовать", callback_data=f"ren:{user_id}"),
            ],
            [
                InlineKeyboardButton(text="◀ К списку", callback_data="staff"),
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def publish_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    ym = f"{year:04d}-{month:02d}"
    prev_y, prev_m = _nav_month(year, month, -1)
    next_y, next_m = _nav_month(year, month, 1)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="◀", callback_data=f"pubnav:{prev_y:04d}-{prev_m:02d}"),
                InlineKeyboardButton(text="▶", callback_data=f"pubnav:{next_y:04d}-{next_m:02d}"),
            ],
            [
                InlineKeyboardButton(
                    text=f"Отправить предварительный график {month:02d}.{year}",
                    callback_data=f"pubgo:{ym}",
                )
            ],
        ]
    )


def settings_keyboard(needed: int, extra_on_empty: bool) -> InlineKeyboardMarkup:
    extra = "Пустые дни: открыты" if extra_on_empty else "Пустые дни: закрыты"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="−", callback_data="setn:-1"),
                InlineKeyboardButton(text=f"Норма: {needed}", callback_data=IGN),
                InlineKeyboardButton(text="+", callback_data="setn:1"),
            ],
            [InlineKeyboardButton(text=extra, callback_data="setempty")],
        ]
    )


def print_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    ym = f"{year:04d}-{month:02d}"
    prev_y, prev_m = _nav_month(year, month, -1)
    next_y, next_m = _nav_month(year, month, 1)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="◀", callback_data=f"prtnav:{prev_y:04d}-{prev_m:02d}"),
                InlineKeyboardButton(text="▶", callback_data=f"prtnav:{next_y:04d}-{next_m:02d}"),
            ],
            [
                InlineKeyboardButton(
                    text="Предварительный график",
                    callback_data=f"prtgo:{ym}:pre",
                )
            ],
            [
                InlineKeyboardButton(
                    text="График работы склада",
                    callback_data=f"prtgo:{ym}:fin",
                )
            ],
        ]
    )


def extra_offer_keyboard(work_date: str, letters: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for label in letters:
        code = MARK_CODE_BY_LABEL.get(label)
        if not code:
            continue
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"exc:{work_date}:{code}",
            )
        )
        if len(row) == 4:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)
    builder.row(InlineKeyboardButton(text="Закрыть", callback_data=IGN))
    return builder.as_markup()


def extra_confirm_keyboard(work_date: str, code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"exy:{work_date}:{code}"),
                InlineKeyboardButton(text="Нет", callback_data="exn"),
            ]
        ]
    )


def cancel_extra_keyboard(work_date: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, отменить", callback_data=f"uny:{work_date}"),
                InlineKeyboardButton(text="Нет", callback_data="unn"),
            ]
        ]
    )


def employee_day_action(work_date: str, kind: str) -> str:
    if kind == "extra_free":
        return f"ex:{work_date}"
    if kind == "extra_mine":
        return f"un:{work_date}"
    return IGN


def employee_calendar(
    year: int,
    month: int,
    marks: dict[int, tuple[str, str]],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        *[InlineKeyboardButton(text=name, callback_data=IGN) for name in WEEKDAYS]
    )
    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    for week in weeks:
        row: list[InlineKeyboardButton] = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data=IGN))
                continue
            label, kind = marks.get(day, (str(day), "none"))
            row.append(
                InlineKeyboardButton(
                    text=label,
                    callback_data=employee_day_action(
                        f"{year:04d}-{month:02d}-{day:02d}",
                        kind,
                    ),
                )
            )
        builder.row(*row)
    prev_y, prev_m = _nav_month(year, month, -1)
    next_y, next_m = _nav_month(year, month, 1)
    builder.row(
        InlineKeyboardButton(text="◀", callback_data=f"ecal:{prev_y:04d}-{prev_m:02d}"),
        InlineKeyboardButton(text="▶", callback_data=f"ecal:{next_y:04d}-{next_m:02d}"),
    )
    return builder.as_markup()


def month_from_today(today: date, offset: int = 0) -> tuple[int, int]:
    return _nav_month(today.year, today.month, offset)
