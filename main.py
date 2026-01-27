import asyncio
import logging
import os

from aiogram import Bot, Dispatcher

from bot.cache import get_config
from bot.routers import admin_flow, catalog_flow, user_flow

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def start_userbot() -> None:
    from userbot.client import get_userbot

    userbot = await get_userbot()
    if userbot:
        logger.info("Userbot started in background")


async def main() -> None:
    token = os.environ.get("BOT_TOKEN") or get_config().get("bot_token", "")
    if not token:
        raise RuntimeError("BOT_TOKEN not set")

    await start_userbot()

    bot = Bot(token)
    dp = Dispatcher()
    dp.include_router(user_flow.router)
    dp.include_router(catalog_flow.router)
    dp.include_router(admin_flow.router)

    logger.info("Starting bot...")

    try:
        await dp.start_polling(bot)
    finally:
        from userbot.client import UserbotClient

        if UserbotClient._instance:
            await UserbotClient._instance.stop()


if __name__ == "__main__":
    asyncio.run(main())
