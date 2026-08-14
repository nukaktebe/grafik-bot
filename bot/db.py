from __future__ import annotations

import calendar
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite
import sqlite3

from bot.config import (
    DB_PATH,
    DEFAULT_NEEDED,
    HOURS_22,
    HOURS_52,
    HOURS_SUTKI,
    MARK_CAP_BY_LABEL,
    VACATION_MARK,
    SICK_MARK,
    ABSENT_MARKS,
    DAY_MARKS,
)

ROLE_BOSS = "boss"
ROLE_EMPLOYEE = "employee"
ROLE_PENDING = "pending"
ROLE_REJECTED = "rejected"

SOURCE_ASSIGNED = "assigned"
SOURCE_EXTRA = "extra"


@dataclass
class User:
    id: int
    telegram_id: int
    username: str | None
    full_name: str
    role: str
    schedule_kind: str | None = None
    schedule_offset: str | None = None

    @property
    def is_boss(self) -> bool:
        return self.role == ROLE_BOSS

    @property
    def is_employee(self) -> bool:
        return self.role in {ROLE_EMPLOYEE, ROLE_BOSS}

    @property
    def mention(self) -> str:
        if self.username:
            return f"@{self.username.lstrip('@')}"
        return self.full_name

    @property
    def is_waiting_bot(self) -> bool:
        return self.telegram_id < 0

    def schedule_label(self) -> str:
        kind = self.schedule_kind or ""
        offset = self.schedule_offset or ""
        if kind == "22" and offset == "a":
            return "2/2 А"
        if kind == "22" and offset == "c":
            return "2/2 Б"
        if kind == "22" and offset == "b":
            return "2/2 со 2-го"
        if kind == "52":
            return "5/2"
        if kind == "s" and offset:
            return f"сутки {offset}"
        return ""

    def hours_label(self) -> str:
        if self.schedule_kind == "22":
            return HOURS_22
        if self.schedule_kind == "52":
            return HOURS_52
        if self.schedule_kind == "s":
            return HOURS_SUTKI
        return ""

    def pattern_code(self) -> str | None:
        if self.schedule_kind == "22" and self.schedule_offset:
            return f"22{self.schedule_offset}"
        if self.schedule_kind == "52":
            return "52"
        if self.schedule_kind == "s" and self.schedule_offset:
            return f"s{self.schedule_offset}"
        return None


@dataclass
class Shift:
    id: int
    work_date: str
    user_id: int
    source: str
    full_name: str
    schedule_kind: str | None = None
    schedule_offset: str | None = None
    mark: str | None = None


def _row_shift(row: aiosqlite.Row) -> Shift:
    keys = row.keys()
    return Shift(
        id=row["id"],
        work_date=row["work_date"],
        user_id=row["user_id"],
        source=row["source"],
        full_name=row["full_name"],
        schedule_kind=row["schedule_kind"] if "schedule_kind" in keys else None,
        schedule_offset=row["schedule_offset"] if "schedule_offset" in keys else None,
        mark=row["mark"] if "mark" in keys else None,
    )


def _row_user(row: aiosqlite.Row) -> User:
    keys = row.keys()
    return User(
        id=row["id"],
        telegram_id=row["telegram_id"],
        username=row["username"],
        full_name=row["full_name"],
        role=row["role"],
        schedule_kind=row["schedule_kind"] if "schedule_kind" in keys else None,
        schedule_offset=row["schedule_offset"] if "schedule_offset" in keys else None,
    )


class Database:
    def __init__(self, path: str = DB_PATH) -> None:
        self.path = path

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA busy_timeout = 5000")
            yield conn

    async def init(self) -> None:
        async with self.connection() as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL UNIQUE,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shifts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_date TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(work_date, user_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS day_settings (
                    work_date TEXT PRIMARY KEY,
                    needed INTEGER,
                    open_extra INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS employee_messages (
                    user_id INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, year, month),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS extra_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_date TEXT NOT NULL,
                    letter TEXT NOT NULL,
                    released_by INTEGER NOT NULL,
                    claimed_by INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (released_by) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS extra_alert_users (
                    alert_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY (alert_id, user_id),
                    FOREIGN KEY (alert_id) REFERENCES extra_alerts(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_shifts_date ON shifts(work_date);
                CREATE INDEX IF NOT EXISTS idx_shifts_user ON shifts(user_id);
                """
            )
            await db.commit()
            columns = {
                row[1]
                for row in await (await db.execute("PRAGMA table_info(users)")).fetchall()
            }
            if "schedule_kind" not in columns:
                await db.execute("ALTER TABLE users ADD COLUMN schedule_kind TEXT")
            if "schedule_offset" not in columns:
                await db.execute("ALTER TABLE users ADD COLUMN schedule_offset TEXT")
            shift_cols = {
                row[1]
                for row in await (await db.execute("PRAGMA table_info(shifts)")).fetchall()
            }
            if "mark" not in shift_cols:
                await db.execute("ALTER TABLE shifts ADD COLUMN mark TEXT")
            await db.execute("DROP INDEX IF EXISTS idx_one_extra_per_day")
            current = await db.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("default_needed",),
            )
            row = await current.fetchone()
            if row is None or row["value"] == "4":
                await db.execute(
                    """
                    INSERT INTO settings (key, value) VALUES ('default_needed', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(DEFAULT_NEEDED),),
                )
            await db.commit()

    async def get_user_by_username(self, username: str) -> User | None:
        nick = username.lower().lstrip("@")
        async with self.connection() as db:
            cur = await db.execute(
                """
                SELECT * FROM users
                WHERE lower(REPLACE(COALESCE(username, ''), '@', '')) = ?
                """,
                (nick,),
            )
            row = await cur.fetchone()
            return _row_user(row) if row else None

    async def add_by_username(self, username: str, full_name: str) -> str:
        nick = username.lower().lstrip("@")
        name = full_name.strip() or nick
        existing = await self.get_user_by_username(nick)
        if existing:
            if existing.role == ROLE_PENDING:
                await self.set_role(existing.id, ROLE_EMPLOYEE)
            if name != nick:
                await self.set_full_name(existing.id, name)
            return "exists"
        async with self.connection() as db:
            cur = await db.execute("SELECT MIN(telegram_id) AS m FROM users")
            row = await cur.fetchone()
            lowest = int(row["m"]) if row and row["m"] is not None else 0
            placeholder = min(lowest - 1, -1)
            await db.execute(
                """
                INSERT INTO users (telegram_id, username, full_name, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    placeholder,
                    nick,
                    name,
                    ROLE_EMPLOYEE,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            await db.commit()
        return "created"

    async def bind_invited_user(
        self, username: str, telegram_id: int, telegram_name: str
    ) -> User | None:
        invited = await self.get_user_by_username(username)
        if invited is None or invited.telegram_id > 0:
            return None
        taken = await self.get_user_by_telegram(telegram_id)
        if taken is not None:
            return None
        async with self.connection() as db:
            await db.execute(
                """
                UPDATE users
                SET telegram_id = ?, username = ?
                WHERE id = ?
                """,
                (telegram_id, username.lower().lstrip("@"), invited.id),
            )
            await db.commit()
        return await self.get_user(invited.id)

    async def get_user_by_telegram(self, telegram_id: int) -> User | None:
        async with self.connection() as db:
            cur = await db.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cur.fetchone()
            return _row_user(row) if row else None

    async def get_user(self, user_id: int) -> User | None:
        async with self.connection() as db:
            cur = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = await cur.fetchone()
            return _row_user(row) if row else None

    async def count_users(self) -> int:
        async with self.connection() as db:
            cur = await db.execute("SELECT COUNT(*) AS n FROM users")
            row = await cur.fetchone()
            return int(row["n"])

    async def upsert_telegram_user(
        self,
        telegram_id: int,
        username: str | None,
        full_name: str,
        default_role: str,
    ) -> User:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        async with self.connection() as db:
            await db.execute(
                """
                INSERT INTO users (telegram_id, username, full_name, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = CASE
                        WHEN users.role = ? THEN excluded.full_name
                        ELSE users.full_name
                    END
                """,
                (telegram_id, username, full_name, default_role, now, ROLE_PENDING),
            )
            await db.commit()
        user = await self.get_user_by_telegram(telegram_id)
        assert user is not None
        return user

    async def set_role(self, user_id: int, role: str) -> None:
        async with self.connection() as db:
            await db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            await db.commit()

    async def set_full_name(self, user_id: int, full_name: str) -> None:
        async with self.connection() as db:
            await db.execute(
                "UPDATE users SET full_name = ? WHERE id = ?",
                (full_name, user_id),
            )
            await db.commit()

    async def set_schedule(self, user_id: int, kind: str, offset: str) -> None:
        async with self.connection() as db:
            await db.execute(
                "UPDATE users SET schedule_kind = ?, schedule_offset = ? WHERE id = ?",
                (kind, offset, user_id),
            )
            await db.commit()

    async def list_by_roles(self, roles: list[str]) -> list[User]:
        placeholders = ",".join("?" * len(roles))
        async with self.connection() as db:
            cur = await db.execute(
                f"SELECT * FROM users WHERE role IN ({placeholders}) ORDER BY full_name COLLATE NOCASE",
                roles,
            )
            rows = await cur.fetchall()
            return [_row_user(row) for row in rows]

    async def get_setting(self, key: str, default: str) -> str:
        async with self.connection() as db:
            cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = await cur.fetchone()
            return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        async with self.connection() as db:
            await db.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            await db.commit()

    async def default_needed(self) -> int:
        raw = await self.get_setting("default_needed", str(DEFAULT_NEEDED))
        return max(0, int(raw))

    async def extra_on_empty(self) -> bool:
        return (await self.get_setting("extra_on_empty", "0")) == "1"

    async def day_config(self, work_date: str) -> tuple[int, bool]:
        default_needed = await self.default_needed()
        async with self.connection() as db:
            cur = await db.execute(
                "SELECT needed, open_extra FROM day_settings WHERE work_date = ?",
                (work_date,),
            )
            row = await cur.fetchone()
        if not row:
            return default_needed, False
        needed = default_needed if row["needed"] is None else int(row["needed"])
        return needed, bool(row["open_extra"])

    async def set_day_needed(self, work_date: str, needed: int) -> None:
        async with self.connection() as db:
            await db.execute(
                """
                INSERT INTO day_settings (work_date, needed, open_extra)
                VALUES (?, ?, 0)
                ON CONFLICT(work_date) DO UPDATE SET needed = excluded.needed
                """,
                (work_date, needed),
            )
            await db.commit()

    async def toggle_open_extra(self, work_date: str) -> bool:
        needed, open_extra = await self.day_config(work_date)
        new_value = 0 if open_extra else 1
        async with self.connection() as db:
            await db.execute(
                """
                INSERT INTO day_settings (work_date, needed, open_extra)
                VALUES (?, ?, ?)
                ON CONFLICT(work_date) DO UPDATE SET open_extra = excluded.open_extra
                """,
                (work_date, needed, new_value),
            )
            await db.commit()
        return bool(new_value)

    async def shift_count(self, work_date: str) -> int:
        async with self.connection() as db:
            cur = await db.execute(
                "SELECT COUNT(*) AS n FROM shifts WHERE work_date = ? AND IFNULL(mark, '') NOT IN (?, ?)",
                (work_date, VACATION_MARK, SICK_MARK),
            )
            row = await cur.fetchone()
            return int(row["n"])

    async def extra_slots(self, work_date: str) -> int:
        return len(await self.free_letters(work_date))

    async def free_letters(self, work_date: str) -> list[str]:
        return free_letters_from_roster(await self.day_roster(work_date))

    async def month_shifts(self, year: int, month: int) -> list[Shift]:
        start = f"{year:04d}-{month:02d}-01"
        last = calendar.monthrange(year, month)[1]
        end = f"{year:04d}-{month:02d}-{last:02d}"
        async with self.connection() as db:
            cur = await db.execute(
                """
                SELECT s.id, s.work_date, s.user_id, s.source, u.full_name,
                       u.schedule_kind, u.schedule_offset, s.mark
                FROM shifts s
                JOIN users u ON u.id = s.user_id
                WHERE s.work_date BETWEEN ? AND ?
                ORDER BY s.work_date, u.full_name COLLATE NOCASE
                """,
                (start, end),
            )
            rows = await cur.fetchall()
            return [_row_shift(row) for row in rows]

    async def user_shift_dates(self, user_id: int, year: int, month: int) -> dict[str, tuple[str, str | None]]:
        start = f"{year:04d}-{month:02d}-01"
        last = calendar.monthrange(year, month)[1]
        end = f"{year:04d}-{month:02d}-{last:02d}"
        async with self.connection() as db:
            cur = await db.execute(
                """
                SELECT work_date, source, mark FROM shifts
                WHERE user_id = ? AND work_date BETWEEN ? AND ?
                """,
                (user_id, start, end),
            )
            rows = await cur.fetchall()
            return {row["work_date"]: (row["source"], row["mark"]) for row in rows}

    async def day_roster(self, work_date: str) -> list[Shift]:
        async with self.connection() as db:
            cur = await db.execute(
                """
                SELECT s.id, s.work_date, s.user_id, s.source, u.full_name,
                       u.schedule_kind, u.schedule_offset, s.mark
                FROM shifts s
                JOIN users u ON u.id = s.user_id
                WHERE s.work_date = ?
                ORDER BY s.source, u.full_name COLLATE NOCASE
                """,
                (work_date,),
            )
            rows = await cur.fetchall()
            return [_row_shift(row) for row in rows]

    async def set_day_mark(self, user_id: int, work_date: str, label: str) -> str:
        cap = MARK_CAP_BY_LABEL.get(label)
        if cap is None:
            return "unknown"
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT user_id FROM shifts WHERE work_date = ? AND mark = ?",
                (work_date, label),
            )
            holders = [int(row["user_id"]) for row in await cur.fetchall()]
            if cap > 1 and user_id not in holders and len(holders) >= cap:
                await db.execute("ROLLBACK")
                return "full"
            if cap == 1:
                await db.execute(
                    """
                    UPDATE shifts SET mark = NULL
                    WHERE work_date = ? AND mark = ? AND user_id != ?
                    """,
                    (work_date, label, user_id),
                )
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            await db.execute(
                """
                INSERT INTO shifts (work_date, user_id, source, created_at, mark)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(work_date, user_id) DO UPDATE SET mark = excluded.mark
                """,
                (work_date, user_id, SOURCE_ASSIGNED, now, label),
            )
            await db.commit()
        return "ok"

    async def clear_day_mark(self, user_id: int, work_date: str) -> None:
        async with self.connection() as db:
            await db.execute(
                "UPDATE shifts SET mark = NULL WHERE user_id = ? AND work_date = ?",
                (user_id, work_date),
            )
            await db.commit()

    async def has_shift(self, user_id: int, work_date: str) -> bool:
        async with self.connection() as db:
            cur = await db.execute(
                "SELECT 1 FROM shifts WHERE user_id = ? AND work_date = ?",
                (user_id, work_date),
            )
            return await cur.fetchone() is not None

    async def toggle_assigned(self, user_id: int, work_date: str) -> bool:
        async with self.connection() as db:
            cur = await db.execute(
                "SELECT id FROM shifts WHERE user_id = ? AND work_date = ?",
                (user_id, work_date),
            )
            row = await cur.fetchone()
            if row:
                await db.execute("DELETE FROM shifts WHERE id = ?", (row["id"],))
                await db.commit()
                return False
            await db.execute(
                """
                INSERT INTO shifts (work_date, user_id, source, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (work_date, user_id, SOURCE_ASSIGNED, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            await db.commit()
            return True

    async def apply_dates(self, user_id: int, dates: list[str], source: str = SOURCE_ASSIGNED) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        async with self.connection() as db:
            await db.executemany(
                """
                INSERT INTO shifts (work_date, user_id, source, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(work_date, user_id) DO UPDATE SET source = excluded.source
                """,
                [(day, user_id, source, now) for day in dates],
            )
            await db.commit()

    async def clear_user_month(self, user_id: int, year: int, month: int) -> None:
        start = f"{year:04d}-{month:02d}-01"
        last = calendar.monthrange(year, month)[1]
        end = f"{year:04d}-{month:02d}-{last:02d}"
        async with self.connection() as db:
            await db.execute(
                "DELETE FROM shifts WHERE user_id = ? AND work_date BETWEEN ? AND ?",
                (user_id, start, end),
            )
            await db.commit()

    async def claim_extra_letter(self, user_id: int, work_date: str, label: str) -> str:
        """Returns ok | already | taken | closed."""
        cap = MARK_CAP_BY_LABEL.get(label, 0)
        if cap <= 0:
            return "closed"
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT 1 FROM shifts WHERE user_id = ? AND work_date = ?",
                (user_id, work_date),
            )
            if await cur.fetchone():
                await db.execute("ROLLBACK")
                return "already"
            hold_raw = await db.execute(
                "SELECT COUNT(*) AS n FROM shifts WHERE work_date = ? AND mark = ?",
                (work_date, label),
            )
            hold_row = await hold_raw.fetchone()
            taken = int(hold_row["n"])
            if taken >= cap:
                await db.execute("ROLLBACK")
                return "taken"
            work_raw = await db.execute(
                """
                SELECT COUNT(*) AS n FROM shifts
                WHERE work_date = ? AND IFNULL(mark, '') NOT IN (?, ?)
                """,
                (work_date, VACATION_MARK, SICK_MARK),
            )
            work_row = await work_raw.fetchone()
            if int(work_row["n"]) == 0:
                await db.execute("ROLLBACK")
                return "closed"
            try:
                await db.execute(
                    """
                    INSERT INTO shifts (work_date, user_id, source, created_at, mark)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        work_date,
                        user_id,
                        SOURCE_EXTRA,
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        label,
                    ),
                )
                await db.commit()
            except (sqlite3.IntegrityError, aiosqlite.IntegrityError):
                await db.execute("ROLLBACK")
                return "already"
            return "ok"

    async def cancel_extra(self, user_id: int, work_date: str) -> bool:
        async with self.connection() as db:
            cur = await db.execute(
                """
                DELETE FROM shifts
                WHERE user_id = ? AND work_date = ? AND source = ?
                """,
                (user_id, work_date, SOURCE_EXTRA),
            )
            await db.commit()
            return cur.rowcount > 0

    async def users_off_on(self, work_date: str, exclude_user_id: int | None = None) -> list[User]:
        busy_ids = {shift.user_id for shift in await self.day_roster(work_date)}
        people = await self.list_by_roles([ROLE_EMPLOYEE])
        result: list[User] = []
        for user in people:
            if exclude_user_id is not None and user.id == exclude_user_id:
                continue
            if user.telegram_id < 0:
                continue
            if user.id in busy_ids:
                continue
            result.append(user)
        return result

    async def create_extra_alert(self, work_date: str, letter: str, released_by: int) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        async with self.connection() as db:
            cur = await db.execute(
                """
                INSERT INTO extra_alerts (work_date, letter, released_by, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (work_date, letter, released_by, now),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def save_alert_recipient(
        self, alert_id: int, user_id: int, chat_id: int, message_id: int
    ) -> None:
        async with self.connection() as db:
            await db.execute(
                """
                INSERT INTO extra_alert_users (alert_id, user_id, chat_id, message_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(alert_id, user_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    message_id = excluded.message_id
                """,
                (alert_id, user_id, chat_id, message_id),
            )
            await db.commit()

    async def open_alert_recipients(
        self, work_date: str, letter: str
    ) -> list[tuple[int, int, int]]:
        async with self.connection() as db:
            cur = await db.execute(
                """
                SELECT u.user_id, u.chat_id, u.message_id
                FROM extra_alert_users u
                JOIN extra_alerts a ON a.id = u.alert_id
                WHERE a.work_date = ? AND a.letter = ? AND a.claimed_by IS NULL
                """,
                (work_date, letter),
            )
            rows = await cur.fetchall()
            return [(int(row["user_id"]), int(row["chat_id"]), int(row["message_id"])) for row in rows]

    async def close_extra_alerts(self, work_date: str, letter: str, claimed_by: int) -> None:
        async with self.connection() as db:
            await db.execute(
                """
                UPDATE extra_alerts
                SET claimed_by = ?
                WHERE work_date = ? AND letter = ? AND claimed_by IS NULL
                """,
                (claimed_by, work_date, letter),
            )
            await db.commit()

    async def save_employee_message(
        self, user_id: int, year: int, month: int, chat_id: int, message_id: int
    ) -> None:
        async with self.connection() as db:
            await db.execute(
                """
                INSERT INTO employee_messages (user_id, year, month, chat_id, message_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, year, month) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    message_id = excluded.message_id
                """,
                (user_id, year, month, chat_id, message_id),
            )
            await db.commit()

    async def get_employee_messages(self, year: int, month: int) -> list[aiosqlite.Row]:
        async with self.connection() as db:
            cur = await db.execute(
                """
                SELECT user_id, chat_id, message_id
                FROM employee_messages
                WHERE year = ? AND month = ?
                """,
                (year, month),
            )
            return await cur.fetchall()

    async def get_employee_message(
        self, user_id: int, year: int, month: int
    ) -> tuple[int, int] | None:
        async with self.connection() as db:
            cur = await db.execute(
                """
                SELECT chat_id, message_id FROM employee_messages
                WHERE user_id = ? AND year = ? AND month = ?
                """,
                (user_id, year, month),
            )
            row = await cur.fetchone()
            if not row:
                return None
            return int(row["chat_id"]), int(row["message_id"])


def free_letters_from_roster(roster: list[Shift]) -> list[str]:
    working = [item for item in roster if (item.mark or "") not in ABSENT_MARKS]
    if not working:
        return []
    used: dict[str, int] = {}
    for item in roster:
        if item.mark and item.mark not in ABSENT_MARKS:
            used[item.mark] = used.get(item.mark, 0) + 1
    free: list[str] = []
    for _code, label, cap in DAY_MARKS:
        if cap <= 0:
            continue
        if used.get(label, 0) < cap:
            free.append(label)
    return free


def today() -> date:
    from bot.config import TZ

    return datetime.now(TZ).date()


db = Database()
