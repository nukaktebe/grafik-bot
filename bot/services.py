from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from bot.config import (
    ABSENT_MARKS,
    DAY_MARKS,
    SICK_MARK,
    STAR_MARK,
    TARGET_DAY_22,
    TARGET_DAY_52,
    TARGET_DAY_SUTKI,
    VACATION_MARK,
)
from bot.db import (
    SOURCE_ASSIGNED,
    SOURCE_EXTRA,
    User,
    db,
    free_letters_from_roster,
    today,
)
from bot.keyboards import employee_calendar, extra_offer_keyboard
from bot.texts import MONTHS, MONTHS_GEN


def month_title(year: int, month: int) -> str:
    return f"{MONTHS[month]} {year}"


def format_day(work_date: str) -> str:
    parsed = date.fromisoformat(work_date)
    return f"{parsed.day} {MONTHS_GEN[parsed.month]}"


def pattern_dates(year: int, month: int, code: str) -> list[str]:
    last = calendar.monthrange(year, month)[1]
    dates: list[str] = []
    for day in range(1, last + 1):
        current = date(year, month, day)
        take = False
        if code == "22a":
            take = (day - 1) % 4 < 2
        elif code == "22b":
            take = (day - 2) % 4 < 2
        elif code == "22c":
            take = (day - 3) % 4 < 2
        elif code == "33":
            take = (day - 1) % 6 < 3
        elif code == "52":
            take = current.weekday() < 5
        elif code in {"s1", "s2", "s3", "s4"}:
            start = int(code[1])
            take = (day - start) % 4 == 0
        if take:
            dates.append(current.isoformat())
    return dates


def parse_pattern_code(code: str) -> tuple[str, str] | None:
    if code.startswith("22") and len(code) == 3:
        return "22", code[2]
    if code == "52":
        return "52", ""
    if code in {"s1", "s2", "s3", "s4"}:
        return "s", code[1]
    return None


def _join_days(days: list[int]) -> str:
    if not days:
        return "нет"
    return ", ".join(str(day) for day in days)


def _join_days_text(days: list[str]) -> str:
    if not days:
        return "нет"
    return ", ".join(days)


async def boss_month_marks(year: int, month: int) -> dict[int, str]:
    shifts = await db.month_shifts(year, month)
    counts: dict[int, int] = defaultdict(int)
    for shift in shifts:
        if shift.mark in ABSENT_MARKS:
            continue
        counts[date.fromisoformat(shift.work_date).day] += 1
    last = calendar.monthrange(year, month)[1]
    extra_on_empty = await db.extra_on_empty()
    marks: dict[int, str] = {}
    for day in range(1, last + 1):
        work_date = f"{year:04d}-{month:02d}-{day:02d}"
        needed, open_extra = await db.day_config(work_date)
        count = counts.get(day, 0)
        if count == 0 and not open_extra and not extra_on_empty:
            marks[day] = str(day)
        elif count >= needed and needed > 0:
            marks[day] = f"{day}✓"
        elif count < needed:
            marks[day] = f"{day}!"
        else:
            marks[day] = f"{day}·{count}" if count else str(day)
    return marks


async def boss_month_text(year: int, month: int) -> str:
    shifts = await db.month_shifts(year, month)
    by_day: dict[int, list[str]] = defaultdict(list)
    for shift in shifts:
        if shift.mark in ABSENT_MARKS:
            continue
        mark = "★" if shift.source == SOURCE_EXTRA else ""
        by_day[date.fromisoformat(shift.work_date).day].append(f"{escape(shift.full_name)}{mark}")
    last = calendar.monthrange(year, month)[1]
    short: list[str] = []
    under: list[int] = []
    extra_on_empty = await db.extra_on_empty()
    for day in range(1, last + 1):
        work_date = f"{year:04d}-{month:02d}-{day:02d}"
        needed, open_extra = await db.day_config(work_date)
        count = len(by_day.get(day, []))
        if count and count < needed:
            under.append(day)
        elif count == 0 and (open_extra or extra_on_empty) and needed > 0:
            under.append(day)
    lines = [
        f"<b>График — {month_title(year, month)}</b>",
        "",
        "Нажмите дату, чтобы посмотреть, кто выходит.",
        "✓ 13 человек   ! меньше 13   свободную букву сотрудники могут взять как подработку",
        "",
        f"Недобор: {_join_days(under)}",
    ]
    return "\n".join(lines)


async def boss_day_text(work_date: str) -> str:
    roster = await db.day_roster(work_date)
    working = [item for item in roster if (item.mark or "") not in ABSENT_MARKS]
    needed, open_extra = await db.day_config(work_date)
    count = len(working)
    free = max(0, needed - count)
    parsed = date.fromisoformat(work_date)
    target_52 = 0 if parsed.weekday() >= 5 else TARGET_DAY_52
    n22 = sum(1 for item in working if item.schedule_kind == "22")
    n52 = sum(1 for item in working if item.schedule_kind == "52")
    nsutki = sum(1 for item in working if item.schedule_kind == "s")
    lines = [
        f"<b>{format_day(work_date)}</b>",
        f"На смене должно быть <b>{needed}</b> человек. Сейчас: {count}. Свободно: {free}.",
        "",
        "Нажмите букву — это функционал на день. Потом выберите человека.",
        "а в с д е f м х р — по одному. * — четверо. О — отпуск. Б — больничный.",
        "Свободную букву после публикации могут взять сотрудники как подработку.",
        "",
    ]
    by_mark: dict[str, list[str]] = {}
    for item in roster:
        if item.mark:
            by_mark.setdefault(item.mark, []).append(item.full_name)
    for _code, label, cap in DAY_MARKS:
        names = by_mark.get(label, [])
        if label == STAR_MARK:
            shown = ", ".join(
                escape(item.full_name) + (" ★" if item.source == SOURCE_EXTRA else "")
                for item in roster
                if item.mark == STAR_MARK
            ) or "—"
            lines.append(f"* : {shown} ({len(names)}/4)")
        elif label == VACATION_MARK:
            lines.append(f"О отпуск: {', '.join(escape(n) for n in names) or '—'}")
        elif label == SICK_MARK:
            lines.append(f"Б больничный: {', '.join(escape(n) for n in names) or '—'}")
        else:
            extra = ""
            if names:
                person = next((item for item in roster if item.mark == label), None)
                if person and person.source == SOURCE_EXTRA:
                    extra = " ★ подработка"
            lines.append(f"{label} : {(escape(names[0]) + extra) if names else '—'}")
    lines.extend(
        [
            "",
            f"Дневные 2/2: {n22} из {TARGET_DAY_22}",
            f"5/2: {n52} из {target_52}" + (" (выходной у пятидневки)" if parsed.weekday() >= 5 else ""),
            f"Сутки: {nsutki} из {TARGET_DAY_SUTKI}",
        ]
    )
    no_letter = [item for item in working if not item.mark]
    if no_letter:
        lines.append("")
        lines.append("Выходят, но буква ещё не стоит:")
        for item in no_letter:
            lines.append(f"• {escape(item.full_name)}")
    free_letters = free_letters_from_roster(roster)
    if free_letters:
        lines.append("")
        lines.append(f"Свободные буквы для подработки: {', '.join(free_letters)}")
    if open_extra:
        lines.append("Пустой день открыт для подработки.")
    return "\n".join(lines)


async def employee_month_view(user: User, year: int, month: int) -> tuple[str, dict[int, tuple[str, str]]]:
    my = await db.user_shift_dates(user.id, year, month)
    month_roster: dict[str, list] = defaultdict(list)
    for shift in await db.month_shifts(year, month):
        month_roster[shift.work_date].append(shift)
    last = calendar.monthrange(year, month)[1]
    now = today()
    work_days: list[str] = []
    extra_days: list[str] = []
    free_days: list[str] = []
    vacation_days: list[int] = []
    sick_days: list[int] = []
    marks: dict[int, tuple[str, str]] = {}

    for day in range(1, last + 1):
        work_date = f"{year:04d}-{month:02d}-{day:02d}"
        current = date(year, month, day)
        source, letter = my.get(work_date, (None, None))
        free = free_letters_from_roster(month_roster.get(work_date, []))

        if letter == VACATION_MARK:
            marks[day] = (f"{day}О", "none")
            vacation_days.append(day)
        elif letter == SICK_MARK:
            marks[day] = (f"{day}Б", "none")
            sick_days.append(day)
        elif source == SOURCE_ASSIGNED:
            shown = f"{day}{letter}" if letter else f"{day}●"
            marks[day] = (shown, "work")
            work_days.append(f"{day}{letter or ''}")
        elif source == SOURCE_EXTRA:
            shown = f"{day}{letter}" if letter else f"{day}●"
            marks[day] = (shown, "extra_mine")
            extra_days.append(f"{day}{letter or ''}")
        elif free and current >= now:
            marks[day] = (f"{day}+", "extra_free")
            free_days.append(str(day))
        else:
            marks[day] = (str(day), "none")

    hours = user.hours_label()
    hours_line = f"Время смены: {hours}" if hours else ""
    lines = [
        f"<b>Ваш график — {month_title(year, month)}</b>",
        hours_line,
        "",
        f"Смены: {_join_days_text(work_days)}",
        f"Подработка: {_join_days_text(extra_days)}",
        f"Отпуск: {_join_days(vacation_days)}",
        f"Больничный: {_join_days(sick_days)}",
        f"Свободные дни: {_join_days_text(free_days)}",
        "",
        "15а — ваша смена   15О — отпуск   15+ — есть свободная буква",
        "Нажмите дату со знаком +, выберите букву и подтвердите. "
        "Кто нажал «Да» первым — тот и выходит в этот день. "
        "Свою подработку можно отменить: нажмите эту дату ещё раз.",
    ]
    return "\n".join(lines), marks


async def notify_bosses(bot: Bot, text: str, reply_markup=None) -> None:
    bosses = await db.list_by_roles(["boss"])
    for boss in bosses:
        if boss.telegram_id < 0:
            continue
        try:
            await bot.send_message(boss.telegram_id, text, reply_markup=reply_markup)
        except TelegramAPIError:
            continue


async def notify_extra_released(
    bot: Bot,
    work_date: str,
    letter: str,
    released_by: User,
) -> None:
    await notify_bosses(
        bot,
        f"{escape(released_by.full_name)} отменил подработку "
        f"{format_day(work_date)} — буква {letter} снова свободна.",
    )
    off_users = await db.users_off_on(work_date, exclude_user_id=released_by.id)
    if not off_users:
        return
    alert_id = await db.create_extra_alert(work_date, letter, released_by.id)
    text = (
        f"Освободилась буква <b>{letter}</b> на {format_day(work_date)}.\n"
        "Можно взять подработку. Нажмите букву, затем «Да». "
        "Кто подтвердит первым — тот и выходит."
    )
    markup = extra_offer_keyboard(work_date, [letter])
    for user in off_users:
        try:
            message = await bot.send_message(user.telegram_id, text, reply_markup=markup)
        except TelegramAPIError:
            continue
        await db.save_alert_recipient(alert_id, user.id, message.chat.id, message.message_id)


async def notify_extra_claimed(
    bot: Bot,
    work_date: str,
    letter: str,
    claimed_by: User,
) -> None:
    recipients = await db.open_alert_recipients(work_date, letter)
    await db.close_extra_alerts(work_date, letter, claimed_by.id)
    for user_id, chat_id, message_id in recipients:
        if user_id == claimed_by.id:
            text = (
                f"Вы взяли подработку {format_day(work_date)} — буква {letter}."
            )
        else:
            text = (
                f"Свободная дата закрыта: {format_day(work_date)}, "
                f"букву {letter} уже взяли."
            )
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest:
            if user_id == claimed_by.id:
                continue
            person = await db.get_user(user_id)
            if person is None or person.telegram_id < 0:
                continue
            try:
                await bot.send_message(person.telegram_id, text)
            except TelegramAPIError:
                continue


async def send_or_edit_employee_schedule(
    bot: Bot,
    user: User,
    year: int,
    month: int,
    *,
    force_new: bool = False,
) -> None:
    text, marks = await employee_month_view(user, year, month)
    markup = employee_calendar(year, month, marks)
    stored = None if force_new else await db.get_employee_message(user.id, year, month)
    if stored:
        chat_id, message_id = stored
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=markup,
            )
            return
        except TelegramBadRequest:
            pass
    try:
        message = await bot.send_message(user.telegram_id, text, reply_markup=markup)
        await db.save_employee_message(user.id, year, month, message.chat.id, message.message_id)
    except TelegramAPIError:
        return


async def publish_month(bot: Bot, year: int, month: int) -> tuple[int, int]:
    employees = await db.list_by_roles(["employee", "boss"])
    notice = (
        f"<b>Предварительный график на {month_title(year, month)} сформирован.</b>\n\n"
        "Пожалуйста, проверьте свои смены. "
        "Если в календаре видите свободную букву — можете взять подработку на этот день. "
        "Кто нажал первым, тот и выходит."
    )
    sent = 0
    failed = 0
    for user in employees:
        if user.telegram_id < 0:
            failed += 1
            continue
        try:
            await bot.send_message(user.telegram_id, notice)
        except TelegramAPIError:
            failed += 1
            continue
        await send_or_edit_employee_schedule(bot, user, year, month)
        after = await db.get_employee_message(user.id, year, month)
        if after:
            sent += 1
        else:
            failed += 1
    return sent, failed


async def refresh_month_messages(bot: Bot, work_date: str) -> None:
    parsed = date.fromisoformat(work_date)
    employees = await db.list_by_roles(["employee", "boss"])
    for user in employees:
        stored = await db.get_employee_message(user.id, parsed.year, parsed.month)
        if stored:
            await send_or_edit_employee_schedule(bot, user, parsed.year, parsed.month)


def parse_ym(value: str) -> tuple[int, int]:
    year_s, month_s = value.split("-")
    return int(year_s), int(month_s)


def is_past(work_date: str) -> bool:
    return date.fromisoformat(work_date) < today()
