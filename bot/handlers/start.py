from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.db import ROLE_REJECTED, User
from bot.keyboards import boss_menu, employee_menu, pending_keyboard, pending_menu
from bot.services import notify_bosses
from bot.texts import HELP_BOSS, HELP_EMPLOYEE, START_BOSS, START_EMPLOYEE, START_PENDING, BTN_HELP

router = Router()


def menu_for(user: User):
    if user.is_boss:
        return boss_menu()
    if user.role == "employee":
        return employee_menu()
    return pending_menu()


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    db_user: User,
    is_new_user: bool = False,
    joined_from_list: bool = False,
) -> None:
    if db_user.is_boss:
        await message.answer(START_BOSS, reply_markup=boss_menu())
        return
    if db_user.role == "employee":
        await message.answer(START_EMPLOYEE, reply_markup=employee_menu())
        if joined_from_list:
            await notify_bosses(
                message.bot,
                f"{escape(db_user.full_name)} открыл бота. Можно отправлять график.",
            )
        return
    if db_user.role == ROLE_REJECTED:
        await message.answer(
            "Доступ отклонён. Если это ошибка, напишите начальнику.",
            reply_markup=pending_menu(),
        )
        return

    await message.answer(START_PENDING, reply_markup=pending_menu())
    if not is_new_user:
        return
    await notify_bosses(
        message.bot,
        f"Новая заявка: {escape(db_user.full_name)} ({escape(db_user.mention)}, id {db_user.telegram_id})",
        reply_markup=pending_keyboard(db_user.id),
    )


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message, db_user: User) -> None:
    text = HELP_BOSS if db_user.is_boss else HELP_EMPLOYEE
    await message.answer(text, reply_markup=menu_for(db_user))
