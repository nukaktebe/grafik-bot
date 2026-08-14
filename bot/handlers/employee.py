from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from bot.config import MARK_LABEL_BY_CODE
from bot.db import SOURCE_EXTRA, User, db, today
from bot.keyboards import (
    cancel_extra_keyboard,
    employee_calendar,
    extra_confirm_keyboard,
    extra_offer_keyboard,
)
from bot.services import (
    employee_month_view,
    format_day,
    is_past,
    notify_bosses,
    notify_extra_claimed,
    notify_extra_released,
    parse_ym,
    refresh_month_messages,
    send_or_edit_employee_schedule,
)
from bot.texts import BTN_MY_SCHEDULE

router = Router()


def _can_use(user: User) -> bool:
    return user.is_employee


async def show_my_schedule(target, user: User, year: int, month: int) -> None:
    text, marks = await employee_month_view(user, year, month)
    markup = employee_calendar(year, month, marks)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            message = await target.message.answer(text, reply_markup=markup)
            await db.save_employee_message(user.id, year, month, message.chat.id, message.message_id)
            return
        await db.save_employee_message(
            user.id, year, month, target.message.chat.id, target.message.message_id
        )
    else:
        message = await target.answer(text, reply_markup=markup)
        await db.save_employee_message(user.id, year, month, message.chat.id, message.message_id)


@router.message(F.text == BTN_MY_SCHEDULE)
async def my_schedule(message: Message, db_user: User) -> None:
    if not _can_use(db_user):
        await message.answer("Сначала дождитесь подтверждения начальника.")
        return
    now = today()
    await show_my_schedule(message, db_user, now.year, now.month)


@router.callback_query(F.data.startswith("ecal:"))
async def employee_calendar_nav(callback: CallbackQuery, db_user: User) -> None:
    if not _can_use(db_user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    year, month = parse_ym(callback.data.split(":")[1])
    await show_my_schedule(callback, db_user, year, month)
    await callback.answer()


@router.callback_query(F.data.startswith("ex:"))
async def offer_extra(callback: CallbackQuery, db_user: User) -> None:
    if not _can_use(db_user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    work_date = callback.data.split(":", 1)[1]
    if is_past(work_date):
        await callback.answer("Этот день уже прошёл", show_alert=True)
        return
    if await db.has_shift(db_user.id, work_date):
        await callback.answer("Вы уже стоите в графике в этот день", show_alert=True)
        return
    letters = await db.free_letters(work_date)
    if not letters:
        await callback.answer("Опоздали: свободных букв уже нет", show_alert=True)
        year, month, _ = work_date.split("-")
        await send_or_edit_employee_schedule(callback.bot, db_user, int(year), int(month))
        return
    if len(letters) == 1:
        text = (
            f"{format_day(work_date)} свободна буква <b>{letters[0]}</b>.\n"
            "Нажмите букву, если хотите взять подработку."
        )
    else:
        text = (
            f"{format_day(work_date)} свободны буквы: <b>{', '.join(letters)}</b>.\n"
            "Нажмите ту, которую берёте."
        )
    await callback.message.answer(text, reply_markup=extra_offer_keyboard(work_date, letters))
    await callback.answer()


@router.callback_query(F.data.startswith("exc:"))
async def choose_extra_letter(callback: CallbackQuery, db_user: User) -> None:
    if not _can_use(db_user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, work_date, code = callback.data.split(":", 2)
    label = MARK_LABEL_BY_CODE.get(code)
    if not label:
        await callback.answer("Нет такой буквы", show_alert=True)
        return
    if is_past(work_date):
        await callback.answer("Этот день уже прошёл", show_alert=True)
        return
    letters = await db.free_letters(work_date)
    if label not in letters:
        await callback.answer("Опоздали: эту букву уже взяли", show_alert=True)
        year, month, _ = work_date.split("-")
        await send_or_edit_employee_schedule(callback.bot, db_user, int(year), int(month))
        try:
            await callback.message.edit_text("Эту букву уже взяли.")
        except TelegramBadRequest:
            pass
        return
    await callback.message.edit_text(
        f"Берёте подработку {format_day(work_date)} — буква <b>{label}</b>?",
        reply_markup=extra_confirm_keyboard(work_date, code),
    )
    await callback.answer()


@router.callback_query(F.data == "exn")
async def decline_extra(callback: CallbackQuery, db_user: User) -> None:
    if not _can_use(db_user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        await callback.message.edit_text("Подработку не взяли.")
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("exy:"))
async def confirm_extra_letter(callback: CallbackQuery, db_user: User) -> None:
    if not _can_use(db_user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, work_date, code = callback.data.split(":", 2)
    label = MARK_LABEL_BY_CODE.get(code)
    if not label:
        await callback.answer("Нет такой буквы", show_alert=True)
        return
    if is_past(work_date):
        await callback.answer("Этот день уже прошёл", show_alert=True)
        return
    result = await db.claim_extra_letter(db_user.id, work_date, label)
    messages = {
        "ok": f"Вы выходите {format_day(work_date)} — буква {label}",
        "already": "Вы уже стоите в графике в этот день",
        "taken": f"Опоздали: букву {label} уже взяли",
        "closed": "Этот день закрыт для подработки",
    }
    await callback.answer(messages[result], show_alert=True)
    parsed_year, parsed_month, _ = work_date.split("-")
    if result == "ok":
        try:
            await callback.message.edit_text(
                f"Вы взяли подработку {format_day(work_date)} — буква {label}."
            )
        except TelegramBadRequest:
            pass
        await send_or_edit_employee_schedule(
            callback.bot, db_user, int(parsed_year), int(parsed_month)
        )
        await notify_bosses(
            callback.bot,
            f"★ {escape(db_user.full_name)} взял подработку "
            f"{format_day(work_date)} — буква {label}",
        )
        await notify_extra_claimed(callback.bot, work_date, label, db_user)
        await refresh_month_messages(callback.bot, work_date)
    elif result in {"taken", "already", "closed"}:
        try:
            await callback.message.edit_text(messages[result])
        except TelegramBadRequest:
            pass
        await send_or_edit_employee_schedule(
            callback.bot, db_user, int(parsed_year), int(parsed_month)
        )


@router.callback_query(F.data.startswith("un:"))
async def ask_cancel_extra(callback: CallbackQuery, db_user: User) -> None:
    if not _can_use(db_user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    work_date = callback.data.split(":", 1)[1]
    if is_past(work_date):
        await callback.answer("Этот день уже прошёл", show_alert=True)
        return
    letter = None
    source = None
    for shift in await db.day_roster(work_date):
        if shift.user_id == db_user.id:
            letter = shift.mark
            source = shift.source
            break
    if source != SOURCE_EXTRA:
        await callback.answer("Основную смену может снять только начальник", show_alert=True)
        return
    letter_html = f" — буква <b>{letter}</b>" if letter else ""
    await callback.message.answer(
        f"Отменить подработку {format_day(work_date)}{letter_html}?",
        reply_markup=cancel_extra_keyboard(work_date),
    )
    await callback.answer()


@router.callback_query(F.data == "unn")
async def decline_cancel_extra(callback: CallbackQuery, db_user: User) -> None:
    if not _can_use(db_user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        await callback.message.edit_text("Подработку оставили.")
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("uny:"))
async def confirm_cancel_extra(callback: CallbackQuery, db_user: User) -> None:
    if not _can_use(db_user):
        await callback.answer("Нет доступа", show_alert=True)
        return
    work_date = callback.data.split(":", 1)[1]
    if is_past(work_date):
        await callback.answer("Этот день уже прошёл", show_alert=True)
        return
    letter = None
    for shift in await db.day_roster(work_date):
        if shift.user_id == db_user.id:
            letter = shift.mark
            break
    removed = await db.cancel_extra(db_user.id, work_date)
    if not removed:
        await callback.answer("Основную смену может снять только начальник", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            f"Подработка {format_day(work_date)}"
            + (f" — буква {letter}" if letter else "")
            + " отменена."
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Подработка отменена")
    parsed_year, parsed_month, _ = work_date.split("-")
    await send_or_edit_employee_schedule(
        callback.bot, db_user, int(parsed_year), int(parsed_month)
    )
    await refresh_month_messages(callback.bot, work_date)
    if letter:
        await notify_extra_released(callback.bot, work_date, letter, db_user)
