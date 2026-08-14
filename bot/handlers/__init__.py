from bot.handlers.boss import router as boss_router
from bot.handlers.employee import router as employee_router
from bot.handlers.start import router as start_router
from aiogram import Router

router = Router()
router.include_router(start_router)
router.include_router(boss_router)
router.include_router(employee_router)
