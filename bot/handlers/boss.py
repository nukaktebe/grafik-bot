from html import escape
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.config import MARK_LABEL_BY_CODE, SICK_MARK, STAR_MARK, VACATION_MARK
from bot.db import ROLE_EMPLOYEE, ROLE_REJECTED, User, db, today
from bot.keyboards import (
    boss_day_keyboard,
    boss_menu,
    boss_pick_person_keyboard,
    employee_card_keyboard,
    employee_menu,
    month_calendar,
    print_keyboard,
    publish_keyboard,
    settings_keyboard,
    staff_list_keyboard,
)
from bot.services import (
    boss_day_text,
    boss_month_marks,
    boss_month_text,
    month_title,
    parse_pattern_code,
    parse_ym,
    pattern_dates,
    publish_month,
    refresh_month_messages,
)
from bot.table import schedule_html
from bot.texts import BTN_PRINT, BTN_PUBLISH, BTN_SCHEDULE, BTN_SETTINGS, BTN_STAFF, BTN_TODAY

router = Router()


class RenameEmployee(StatesGroup):
    waiting_name = State()


class AddNicks(StatesGroup):
    waiting_list = State()


def _boss_only(user: User) -> bool:
    return user.is_boss


async def _safe_edit(callback: CallbackQuery, text: str, markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup)


async def show_boss_calendar(callback_or_message, year: int, month: int) -> None:
    text = await boss_month_text(year, month)
    marks = await boss_month_marks(year, month)
    markup = month_calendar(
        year,
        month,
        marks,
        "bday",
        "bcal",
        footer_button=("🖨 Таблица на печать / экран", f"prt:{year:04d}-{month:02d}"),
    )
    if isinstance(callback_or_message, CallbackQuery):
        await _safe_edit(callback_or_message, text, markup)
    else:
        await callback_or_message.answer(text, reply_markup=markup)


async def show_boss_day(callback: CallbackQuery, work_date: str) -> None:
    roster = await db.day_roster(work_date)
    holders: dict[str, list[str]] = {}
    for shift in roster:
        if shift.mark:
            holders.setdefault(shift.mark, []).append(shift.full_name)
    _, open_extra = await db.day_config(work_date)
    await _safe_edit(
        callback,
        await boss_day_text(work_date),
        boss_day_keyboard(work_date, holders, open_extra),
    )


@router.message(F.text == BTN_SCHEDULE)
async def open_schedule(message: Message, db_user: User) -> None:
    if not _boss_only(db_user):
        return
    now = today()
    await show_boss_calendar(message, now.year, now.month)


@router.callback_query(F.data == "ign")
async def ignore_callback(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("bcal:"))
async def boss_calendar_nav(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, ym = callback.data.split(":", 1)
    year, month = parse_ym(ym)
    await show_boss_calendar(callback, year, month)
    await callback.answer()


@router.callback_query(F.data.startswith("bday:"))
async def boss_open_day(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    work_date = callback.data.split(":", 1)[1]
    await show_boss_day(callback, work_date)
    await callback.answer()


@router.callback_query(F.data.startswith("blet:"))
async def boss_pick_letter(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, work_date, code = callback.data.split(":")
    label = MARK_LABEL_BY_CODE.get(code)
    if not label:
        await callback.answer("Нет такой буквы", show_alert=True)
        return
    roster = {shift.user_id: shift.mark for shift in await db.day_roster(work_date)}
    people = await db.list_by_roles(["employee", "boss"])
    rows = [(user.id, user.full_name, roster.get(user.id)) for user in people]
    title = f"Буква {label} — нажмите человека"
    if label == VACATION_MARK:
        title = "Отпуск — нажмите человека"
    elif label == SICK_MARK:
        title = "Больничный — нажмите человека"
    elif label == STAR_MARK:
        title = "Значок * — до четырёх человек. Нажмите, кого поставить."
    await _safe_edit(callback, title, boss_pick_person_keyboard(work_date, code, rows))
    await callback.answer()


@router.callback_query(F.data.startswith("bset:"))
async def boss_set_letter(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, work_date, code, user_id_s = callback.data.split(":")
    label = MARK_LABEL_BY_CODE.get(code)
    if not label:
        await callback.answer("Нет такой буквы", show_alert=True)
        return
    user_id = int(user_id_s)
    current = None
    for shift in await db.day_roster(work_date):
        if shift.user_id == user_id:
            current = shift.mark
            break
    if current == label:
        await db.clear_day_mark(user_id, work_date)
        await show_boss_day(callback, work_date)
        await refresh_month_messages(callback.bot, work_date)
        await callback.answer("Снято")
        return
    result = await db.set_day_mark(user_id, work_date, label)
    if result == "full":
        await callback.answer("Мест на эту букву больше нет", show_alert=True)
        await show_boss_day(callback, work_date)
        return
    await show_boss_day(callback, work_date)
    await refresh_month_messages(callback.bot, work_date)
    if label == VACATION_MARK:
        await callback.answer("Отпуск")
    else:
        await callback.answer(f"Поставлено: {label}")


@router.callback_query(F.data.startswith("nneed:"))
async def boss_change_needed(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, work_date, delta_s = callback.data.split(":")
    needed, _ = await db.day_config(work_date)
    await db.set_day_needed(work_date, max(0, needed + int(delta_s)))
    await show_boss_day(callback, work_date)
    await refresh_month_messages(callback.bot, work_date)
    await callback.answer()


@router.callback_query(F.data.startswith("bopen:"))
async def boss_toggle_open_extra(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    work_date = callback.data.split(":", 1)[1]
    opened = await db.toggle_open_extra(work_date)
    await show_boss_day(callback, work_date)
    await refresh_month_messages(callback.bot, work_date)
    await callback.answer("День открыт для подработки" if opened else "День закрыт")


@router.message(F.text == BTN_TODAY)
async def today_roster(message: Message, db_user: User) -> None:
    if not _boss_only(db_user):
        return
    work_date = today().isoformat()
    roster = await db.day_roster(work_date)
    holders: dict[str, list[str]] = {}
    for shift in roster:
        if shift.mark:
            holders.setdefault(shift.mark, []).append(shift.full_name)
    _, open_extra = await db.day_config(work_date)
    await message.answer(
        await boss_day_text(work_date),
        reply_markup=boss_day_keyboard(work_date, holders, open_extra),
    )


@router.message(F.text == BTN_STAFF)
async def staff_list(message: Message, db_user: User) -> None:
    if not _boss_only(db_user):
        return
    now = today()
    ym = f"{now.year:04d}-{now.month:02d}"
    await message.answer(
        await _staff_text(),
        reply_markup=staff_list_keyboard(await _staff_rows(), ym),
    )


@router.callback_query(F.data == "staff")
async def staff_list_cb(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    now = today()
    ym = f"{now.year:04d}-{now.month:02d}"
    await _safe_edit(
        callback,
        await _staff_text(),
        staff_list_keyboard(await _staff_rows(), ym),
    )
    await callback.answer()


def parse_staff_lines(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        found = re.search(r"@([A-Za-z0-9_]{4,32})", line)
        if found:
            nick = found.group(1)
            name = line[: found.start()].strip(" ,;-")
        else:
            parts = line.split()
            nick = parts[-1].lstrip("@")
            name = " ".join(parts[:-1]).strip()
            if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", nick):
                continue
        nick = nick.lower()
        if nick in seen:
            continue
        seen.add(nick)
        rows.append((nick, name or nick))
    return rows


@router.callback_query(F.data == "addnicks")
async def start_add_nicks(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.set_state(AddNicks.waiting_list)
    await callback.message.answer(
        "Пришлите ники одним сообщением, каждого с новой строки.\n\n"
        "Можно так:\n"
        "<code>@ivanov</code>\n"
        "<code>Рубцов Дмитрий @rubtsov</code>\n\n"
        "Подтверждать каждого не нужно. "
        "Человек один раз нажимает Start — и сразу попадает в сотрудники.\n"
        "Пока он не откроет бота, график ему отправить нельзя: так устроен Telegram."
    )
    await callback.answer()


@router.message(AddNicks.waiting_list, F.text)
async def save_nicks(message: Message, db_user: User, state: FSMContext) -> None:
    if not _boss_only(db_user):
        await state.clear()
        return
    menu_buttons = {
        BTN_SCHEDULE,
        BTN_STAFF,
        BTN_PUBLISH,
        BTN_SETTINGS,
        BTN_TODAY,
        BTN_HELP,
    }
    if message.text in menu_buttons:
        await state.clear()
        await message.answer("Добавление ников отменено. Нажмите нужную кнопку ещё раз.")
        return
    rows = parse_staff_lines(message.text)
    if not rows:
        await message.answer(
            "Не нашёл ников. Напишите как @ivanov, каждого с новой строки."
        )
        return
    created = 0
    existed = 0
    for nick, name in rows:
        result = await db.add_by_username(nick, name)
        if result == "created":
            created += 1
        else:
            existed += 1
    await state.clear()
    await message.answer(
        f"Готово. Новых: {created}. Уже были в списке: {existed}.\n"
        "Теперь откройте человека и поставьте ему график. "
        "Когда он нажмёт Start, подтверждение не понадобится.",
        reply_markup=boss_menu(),
    )


async def _staff_rows() -> list[tuple[int, str, str]]:
    users = await db.list_by_roles(["employee", "boss", "pending"])
    rows = []
    for user in users:
        label = user.schedule_label()
        name = f"{user.full_name} · {label}" if label else user.full_name
        if user.is_waiting_bot:
            name = f"{name} · ждёт Start"
        rows.append((user.id, name, user.role))
    return rows


async def _staff_text() -> str:
    pending = await db.list_by_roles(["pending"])
    extra = ""
    if pending:
        extra = f"\nОжидают подтверждения: {len(pending)}"
    return (
        "<b>Сотрудники</b>\n\n"
        "Каждый день на складе 13 человек: 9 дневных 2/2, двое 5/2 и двое сутки.\n\n"
        "Нажмите имя и выберите график. "
        "Дневных 2/2 делите поровну: бригада А и бригада Б. "
        "Сутки: пары на «с 1-го», «с 2-го», «с 3-го», «с 4-го» — тогда каждый день выходят двое.\n\n"
        "Нажмите «Добавить по никам Telegram» и пришлите список. "
        "Подтверждать каждого не нужно. Человек один раз нажимает Start в боте — и сразу сотрудник.\n\n"
        f"{extra}"
    )


@router.callback_query(F.data.startswith("emp:"))
async def employee_card(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    user_id = int(callback.data.split(":")[1])
    person = await db.get_user(user_id)
    if person is None:
        await callback.answer("Сотрудник не найден", show_alert=True)
        return
    now = today()
    await _safe_edit(callback, await _card_text(person, now.year, now.month), employee_card_keyboard(person.id, now.year, now.month, person.role))
    await callback.answer()


async def _card_text(person: User, year: int, month: int) -> str:
    shifts = await db.user_shift_dates(person.id, year, month)
    work_days = sorted(
        int(day[-2:])
        for day, (_source, letter) in shifts.items()
        if letter != VACATION_MARK
    )
    vac_days = sorted(
        int(day[-2:])
        for day, (_source, letter) in shifts.items()
        if letter == VACATION_MARK
    )
    days_s = ", ".join(str(day) for day in work_days) if work_days else "нет смен"
    vac_s = f"\nОтпуск: {', '.join(str(day) for day in vac_days)}" if vac_days else ""
    role = "начальник" if person.is_boss else "сотрудник" if person.role == "employee" else person.role
    graph = person.schedule_label()
    hours = person.hours_label()
    graph_line = f"График: {graph}" if graph else ""
    if hours:
        graph_line = f"{graph_line} · {hours}".strip(" ·")
    graph_line = f"{graph_line}\n" if graph_line else ""
    return (
        f"<b>{escape(person.full_name)}</b>\n"
        f"{escape(person.mention)} · {role}\n"
        f"{graph_line}\n"
        f"{month_title(year, month)}: {days_s}{vac_s}\n\n"
        "Нажмите шаблон — смены проставятся на этот месяц, график запомнится.\n\n"
        "Чтобы каждый день выходили 9 дневных: часть людей — бригада А, часть — бригада Б.\n"
        "Чтобы каждый день выходили двое сутки: двух человек на «Сутки с 1-го», "
        "двух на «с 2-го», двух на «с 3-го», двух на «с 4-го»."
    )


@router.callback_query(F.data.startswith("pat:"))
async def apply_pattern(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, user_id_s, ym, code = callback.data.split(":")
    user_id = int(user_id_s)
    year, month = parse_ym(ym)
    dates = pattern_dates(year, month, code)
    parsed = parse_pattern_code(code)
    if parsed:
        await db.set_schedule(user_id, parsed[0], parsed[1])
    await db.apply_dates(user_id, dates)
    person = await db.get_user(user_id)
    if person is None:
        await callback.answer("Сотрудник не найден", show_alert=True)
        return
    await _safe_edit(callback, await _card_text(person, year, month), employee_card_keyboard(user_id, year, month, person.role))
    await refresh_month_messages(callback.bot, f"{year:04d}-{month:02d}-01")
    await callback.answer(f"Проставлено дней: {len(dates)}")


@router.callback_query(F.data.startswith("fillall:"))
async def fill_all_month(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    year, month = parse_ym(callback.data.split(":")[1])
    people = await db.list_by_roles(["employee", "boss"])
    filled = 0
    skipped = 0
    for person in people:
        code = person.pattern_code()
        if not code:
            skipped += 1
            continue
        dates = pattern_dates(year, month, code)
        await db.apply_dates(person.id, dates)
        filled += 1
    await refresh_month_messages(callback.bot, f"{year:04d}-{month:02d}-01")
    await callback.answer()
    await callback.message.answer(
        f"Месяц проставлен: {filled} человек. "
        f"Без шаблона пропущено: {skipped}. "
        "Сначала откройте человека и нажмите его график, потом эту кнопку.",
        reply_markup=boss_menu(),
    )


@router.callback_query(F.data.startswith("clr:"))
async def clear_month(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, user_id_s, ym = callback.data.split(":")
    user_id = int(user_id_s)
    year, month = parse_ym(ym)
    await db.clear_user_month(user_id, year, month)
    person = await db.get_user(user_id)
    if person is None:
        await callback.answer("Сотрудник не найден", show_alert=True)
        return
    await _safe_edit(callback, await _card_text(person, year, month), employee_card_keyboard(user_id, year, month, person.role))
    await refresh_month_messages(callback.bot, f"{year:04d}-{month:02d}-01")
    await callback.answer("Месяц очищен")


@router.callback_query(F.data.startswith("ren:"))
async def rename_start(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    user_id = int(callback.data.split(":")[1])
    await state.set_state(RenameEmployee.waiting_name)
    await state.update_data(user_id=user_id)
    await callback.message.answer("Напишите новое имя сотрудника (как в графике).")
    await callback.answer()


@router.message(RenameEmployee.waiting_name, F.text)
async def rename_save(message: Message, db_user: User, state: FSMContext) -> None:
    if not _boss_only(db_user):
        await state.clear()
        return
    name = message.text.strip()
    if not name or len(name) > 64:
        await message.answer("Имя должно быть от 1 до 64 символов.")
        return
    data = await state.get_data()
    await db.set_full_name(int(data["user_id"]), name)
    await state.clear()
    await message.answer(f"Имя обновлено: {name}", reply_markup=boss_menu())


@router.callback_query(F.data.startswith("ok:"))
async def approve_user(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    person = await db.get_user(int(callback.data.split(":")[1]))
    if person is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await db.set_role(person.id, ROLE_EMPLOYEE)
    try:
        await callback.bot.send_message(
            person.telegram_id,
            "Вас подтвердили. Откройте «Мой график», чтобы увидеть смены.",
            reply_markup=employee_menu(),
        )
    except TelegramBadRequest:
        pass
    await callback.message.edit_text(f"{escape(person.full_name)} добавлен в сотрудники.")
    await callback.answer("Подтверждён")


@router.callback_query(F.data.startswith("no:"))
async def reject_user(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    person = await db.get_user(int(callback.data.split(":")[1]))
    if person is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await db.set_role(person.id, ROLE_REJECTED)
    await callback.message.edit_text(f"{escape(person.full_name)}: заявка отклонена.")
    await callback.answer("Отклонён")


@router.message(F.text == BTN_PRINT)
async def print_prompt(message: Message, db_user: User) -> None:
    if not _boss_only(db_user):
        return
    now = today()
    await message.answer(
        f"Таблица графика за <b>{month_title(now.year, now.month)}</b>.\n"
        "Откроется файл — его можно смотреть на компьютере и печатать, как бумажный график.",
        reply_markup=print_keyboard(now.year, now.month),
    )


@router.callback_query(F.data.startswith("prt:"))
async def print_from_calendar(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    year, month = parse_ym(callback.data.split(":")[1])
    await callback.message.answer(
        f"Таблица графика за <b>{month_title(year, month)}</b>.\n"
        "Выберите, какую шапку поставить на лист.",
        reply_markup=print_keyboard(year, month),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prtnav:"))
async def print_nav(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    year, month = parse_ym(callback.data.split(":")[1])
    await _safe_edit(
        callback,
        f"Таблица графика за <b>{month_title(year, month)}</b>.\n"
        "Откроется файл — его можно смотреть на компьютере и печатать.",
        print_keyboard(year, month),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prtgo:"))
async def print_go(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, ym, kind = callback.data.split(":")
    year, month = parse_ym(ym)
    preliminary = kind == "pre"
    await callback.answer("Готовлю таблицу…")
    html = await schedule_html(year, month, preliminary=preliminary)
    filename = (
        f"predvaritelnyy-grafik-{year:04d}-{month:02d}.html"
        if preliminary
        else f"grafik-sklada-{year:04d}-{month:02d}.html"
    )
    title = "предварительный график" if preliminary else "график работы склада"
    await callback.message.answer_document(
        BufferedInputFile(html.encode("utf-8"), filename=filename),
        caption=(
            f"{title.capitalize()} за {month_title(year, month)}.\n"
            "Откройте файл на компьютере в браузере. "
            "Чтобы напечатать: в браузере «Файл → Печать», ориентация альбомная."
        ),
        reply_markup=boss_menu(),
    )


@router.message(F.text == BTN_PUBLISH)
async def publish_prompt(message: Message, db_user: User) -> None:
    if not _boss_only(db_user):
        return
    now = today()
    await message.answer(
        f"Отправить сотрудникам предварительный график за <b>{month_title(now.year, now.month)}</b>?\n"
        "Каждый получит свои смены и свободные буквы. "
        "На подработку выходит тот, кто первым взял свободную букву.",
        reply_markup=publish_keyboard(now.year, now.month),
    )


@router.callback_query(F.data.startswith("pubnav:"))
async def publish_nav(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    year, month = parse_ym(callback.data.split(":")[1])
    await _safe_edit(
        callback,
        f"Отправить сотрудникам предварительный график за <b>{month_title(year, month)}</b>?",
        publish_keyboard(year, month),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pubgo:"))
async def publish_go(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    year, month = parse_ym(callback.data.split(":")[1])
    await callback.answer("Отправляю…")
    sent, failed = await publish_month(callback.bot, year, month)
    await callback.message.answer(
        f"Предварительный график за {month_title(year, month)} отправлен.\n"
        f"Доставлено: {sent}. Не удалось: {failed}.",
        reply_markup=boss_menu(),
    )


@router.message(F.text == BTN_SETTINGS)
async def settings(message: Message, db_user: User) -> None:
    if not _boss_only(db_user):
        return
    needed = await db.default_needed()
    extra_on_empty = await db.extra_on_empty()
    await message.answer(
        "<b>Настройки</b>\n\n"
        "Норма — сколько человек должно выйти <b>за один день</b>. "
        "У вас это 13: 9 дневных 2/2, двое 5/2 и двое сутки.\n\n"
        "Если человек в отпуске или на больничном, его буква остаётся свободной. "
        "После публикации сотрудники могут взять свободную букву как подработку. "
        "Кто нажал первым — тот и выходит в этот день.\n\n"
        "Пустые дни: если закрыты, на день без смен записаться нельзя "
        "(выходной склада).",
        reply_markup=settings_keyboard(needed, extra_on_empty),
    )


@router.callback_query(F.data.startswith("setn:"))
async def settings_needed(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    delta = int(callback.data.split(":")[1])
    needed = max(0, await db.default_needed() + delta)
    await db.set_setting("default_needed", str(needed))
    extra_on_empty = await db.extra_on_empty()
    await _safe_edit(
        callback,
        "<b>Настройки</b>\n\nНорма людей в смену обновлена.",
        settings_keyboard(needed, extra_on_empty),
    )
    await callback.answer(f"Норма: {needed}")


@router.callback_query(F.data == "setempty")
async def settings_empty(callback: CallbackQuery, db_user: User) -> None:
    if not _boss_only(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    extra_on_empty = not await db.extra_on_empty()
    await db.set_setting("extra_on_empty", "1" if extra_on_empty else "0")
    await _safe_edit(
        callback,
        "<b>Настройки</b>\n\nПравило для пустых дней обновлено.",
        settings_keyboard(await db.default_needed(), extra_on_empty),
    )
    await callback.answer()
