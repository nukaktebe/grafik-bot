import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from aiogram.types import BotCommand

from bot.config import BOT_TOKEN
from bot.db import db
from bot.handlers import router
from bot.middlewares import DbUserMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    if not BOT_TOKEN:
        logger.error("Укажите BOT_TOKEN в файле .env")
        sys.exit(1)

    await db.init()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.middleware(DbUserMiddleware())
    dispatcher.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть меню"),
            BotCommand(command="help", description="Как пользоваться"),
        ]
    )
    logger.info("Бот запущен")
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
