from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from html import escape

from bot.config import HOURS_22, HOURS_52, HOURS_SUTKI
from bot.db import User, db, free_letters_from_roster
from bot.texts import MONTHS

WEEKDAYS_PRINT = ["пон", "вт", "ср", "чет", "пят", "суб", "вос"]
KIND_ORDER = {"22": 0, "52": 1, "s": 2}


def _job_title(user: User) -> str:
    kind = user.schedule_kind or ""
    offset = user.schedule_offset or ""
    if kind == "22":
        return "сборщик 2/2"
    if kind == "52":
        return "сборщик 5/2"
    if kind == "s" and offset:
        return f"суточник/{offset}"
    return user.schedule_label() or ""


def _hours(user: User) -> str:
    if user.schedule_kind == "22":
        return HOURS_22
    if user.schedule_kind == "52":
        return HOURS_52
    if user.schedule_kind == "s":
        return HOURS_SUTKI
    return ""


def _sort_staff(people: list[User]) -> list[User]:
    return sorted(
        people,
        key=lambda user: (
            KIND_ORDER.get(user.schedule_kind or "", 9),
            user.schedule_offset or "",
            user.full_name.lower(),
        ),
    )


def build_schedule_html(year: int, month: int, *, preliminary: bool, people: list[User], cells: dict[int, dict[int, str]], free_by_day: dict[int, list[str]]) -> str:
    last = calendar.monthrange(year, month)[1]
    title_month = MONTHS[month]
    heading = (
        f"ПРЕДВАРИТЕЛЬНЫЙ ГРАФИК РАБОТЫ СКЛАДА НА {title_month} {year}"
        if preliminary
        else f"ГРАФИК РАБОТЫ СКЛАДА НА {title_month} {year}"
    )
    days = list(range(1, last + 1))
    weekday_cells = "".join(
        f"<th>{WEEKDAYS_PRINT[date(year, month, day).weekday()]}</th>" for day in days
    )
    number_cells = "".join(f"<th>{day}</th>" for day in days)

    rows: list[str] = []
    prev_kind = None
    for user in _sort_staff(people):
        group_class = ""
        if prev_kind is not None and user.schedule_kind != prev_kind:
            group_class = ' class="group"'
        prev_kind = user.schedule_kind
        marks = cells.get(user.id, {})
        day_cells = []
        for day in days:
            mark = marks.get(day, "")
            css = "vac" if mark in {"О", "Б"} else "mark"
            day_cells.append(f'<td class="{css}">{escape(mark)}</td>')
        rows.append(
            "<tr{group}>"
            '<td class="fio">{name}</td>'
            '<td class="job">{job}</td>'
            "{days}"
            '<td class="hours">{hours}</td>'
            "</tr>".format(
                group=group_class,
                name=escape(user.full_name),
                job=escape(_job_title(user)),
                days="".join(day_cells),
                hours=escape(_hours(user)),
            )
        )

    free_cells = []
    for day in days:
        letters = free_by_day.get(day, [])
        free_cells.append(f"<td class='free'>{escape(' '.join(letters))}</td>")

    body = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{escape(heading)}</title>
<style>
@page {{ size: A4 landscape; margin: 8mm; }}
body {{
  font-family: Arial, "Helvetica Neue", sans-serif;
  color: #111;
  margin: 12px;
}}
h1 {{
  font-size: 18px;
  text-align: center;
  margin: 0 0 10px;
  text-transform: uppercase;
}}
table {{
  border-collapse: collapse;
  width: 100%;
  table-layout: fixed;
  font-size: 10px;
}}
th, td {{
  border: 1px solid #222;
  text-align: center;
  padding: 2px 1px;
  vertical-align: middle;
}}
th {{
  background: #f3f3f3;
  font-weight: 600;
}}
.fio {{
  text-align: left;
  white-space: nowrap;
  width: 160px;
  padding-left: 4px;
}}
.job {{
  white-space: nowrap;
  width: 90px;
  font-size: 9px;
}}
.hours {{
  white-space: nowrap;
  width: 110px;
  font-size: 9px;
}}
.mark {{ font-weight: 700; }}
.vac {{ color: #444; }}
.group td {{ border-top: 3px solid #000; }}
.free {{ color: #063; font-weight: 700; }}
.notes {{
  margin-top: 10px;
  font-size: 12px;
}}
.hint {{
  margin-top: 8px;
  color: #555;
  font-size: 12px;
}}
@media print {{
  .hint {{ display: none; }}
  body {{ margin: 0; }}
}}
</style>
</head>
<body>
<h1>{escape(heading)}</h1>
<table>
  <thead>
    <tr>
      <th rowspan="2" class="fio">ФАМИЛИЯ</th>
      <th rowspan="2" class="job"></th>
      {number_cells}
      <th rowspan="2" class="hours">время</th>
    </tr>
    <tr>
      {weekday_cells}
    </tr>
  </thead>
  <tbody>
    {body}
    <tr class="group">
      <td class="fio" colspan="2">свободные смены сборки</td>
      {''.join(free_cells)}
      <td></td>
    </tr>
  </tbody>
</table>
<div class="notes">
  <div>Отсутствие на работе без больничного листа — штраф 20 часов.</div>
  <div>Заявления на отпуск в следующем месяце пишутся до 15 числа текущего месяца.</div>
</div>
<div class="hint">Чтобы напечатать: в браузере откройте «Файл → Печать». Ориентация — альбомная.</div>
</body>
</html>
"""


async def schedule_html(year: int, month: int, *, preliminary: bool) -> str:
    people = await db.list_by_roles(["employee", "boss"])
    shifts = await db.month_shifts(year, month)
    cells: dict[int, dict[int, str]] = defaultdict(dict)
    by_date: dict[str, list] = defaultdict(list)
    used_ids: set[int] = set()
    for shift in shifts:
        day = date.fromisoformat(shift.work_date).day
        cells[shift.user_id][day] = shift.mark or "•"
        by_date[shift.work_date].append(shift)
        used_ids.add(shift.user_id)
    shown = [
        user
        for user in people
        if user.role == "employee" or user.id in used_ids
    ]
    last = calendar.monthrange(year, month)[1]
    free_by_day: dict[int, list[str]] = {}
    for day in range(1, last + 1):
        work_date = f"{year:04d}-{month:02d}-{day:02d}"
        free_by_day[day] = free_letters_from_roster(by_date.get(work_date, []))
    return build_schedule_html(
        year,
        month,
        preliminary=preliminary,
        people=shown,
        cells=cells,
        free_by_day=free_by_day,
    )
