from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser

from bot.config import ADMIN_IDS
from bot.db import ROLE_BOSS, ROLE_PENDING, db


class DbUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        existing = await db.get_user_by_telegram(tg_user.id)
        joined_from_list = False
        if existing is None and tg_user.username:
            bound = await db.bind_invited_user(
                tg_user.username,
                tg_user.id,
                " ".join(part for part in (tg_user.first_name, tg_user.last_name) if part),
            )
            if bound is not None:
                existing = bound
                joined_from_list = True

        if existing is None:
            if tg_user.id in ADMIN_IDS:
                role = ROLE_BOSS
            elif not ADMIN_IDS and await db.count_users() == 0:
                role = ROLE_BOSS
            else:
                role = ROLE_PENDING
        else:
            role = existing.role
            if tg_user.id in ADMIN_IDS and existing.role != ROLE_BOSS:
                await db.set_role(existing.id, ROLE_BOSS)
                role = ROLE_BOSS

        full_name = " ".join(part for part in (tg_user.first_name, tg_user.last_name) if part)
        data["is_new_user"] = existing is None
        data["joined_from_list"] = joined_from_list
        if joined_from_list:
            data["db_user"] = existing
            return await handler(event, data)

        data["db_user"] = await db.upsert_telegram_user(
            telegram_id=tg_user.id,
            username=tg_user.username,
            full_name=full_name or f"id{tg_user.id}",
            default_role=role,
        )
        return await handler(event, data)
