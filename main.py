import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.cache import get_config, preload_cache
from bot.routers import admin_flow, catalog_flow, dialog_flow, user_flow, joinly_flow, moderation_flow, poster_flow
from bot.middlewares import (
    CallbackAckWatchdogMiddleware,
    UserActionLoggingMiddleware,
    on_transient_error,
    start_log_worker,
    stop_log_worker,
)


class PollingFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return "getUpdates" not in msg and "Updates handled" not in msg


def _resolve_level(level_name: str | None, default: int) -> int:
    if not level_name:
        return default
    value = str(level_name).upper()
    parsed = getattr(logging, value, None)
    return parsed if isinstance(parsed, int) else default


def _configure_external_loggers() -> None:
    config = get_config()
    logging_cfg = config.get("logging", {}) if isinstance(config, dict) else {}
    levels = logging_cfg.get("levels", {}) if isinstance(logging_cfg, dict) else {}

    aiogram_event_level = _resolve_level(levels.get("aiogram.event"), logging.WARNING)
    telethon_uploads_level = _resolve_level(levels.get("telethon.client.uploads"), logging.WARNING)

    logging.getLogger("aiogram.event").setLevel(aiogram_event_level)
    logging.getLogger("telethon.client.uploads").setLevel(telethon_uploads_level)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

logging.getLogger("aiogram.event").addFilter(PollingFilter())
logging.getLogger("aiogram.dispatcher").addFilter(PollingFilter())
logging.getLogger("httpx").addFilter(PollingFilter())
_configure_external_loggers()


async def start_userbot() -> None:
    from userbot.client import get_userbot
    try:
        userbot = await get_userbot()
        if userbot:
            logger.info("Userbot started in background")
    except Exception:
        logger.exception("Failed to initialize userbot in background")


async def on_startup(bot: Bot) -> None:
    from storage import verify_integrity, StorageCorruptError
    try:
        verify_integrity()
    except StorageCorruptError as exc:
        logger.critical("DATABASE CORRUPT — refusing to start. %s", exc)
        raise

    await preload_cache()

    from storage import preload_storage
    await preload_storage()

    from bot.services.publish import seed_updated_plugins
    seed_updated_plugins()
    
    from user_store import init_user_store
    await init_user_store()

    from request_store import (
        start_draft_cleanup_worker,
        start_draft_reminder_worker,
        start_scheduled_publish_worker,
        cleanup_orphan_plugin_files,
    )
    start_draft_cleanup_worker()
    start_draft_reminder_worker(bot)
    start_scheduled_publish_worker(bot)
    cleanup_orphan_plugin_files()

    admin_flow.start_scheduled_posts_cleanup_worker()

    from bot.services.poster import start_poster_worker
    start_poster_worker(bot)

    from bot.services.backup import start_backup_worker
    start_backup_worker(bot)

    await joinly_flow.schedule_pending_post_guard_unlocks(bot)
    
    await start_log_worker()
    
    from bot.helpers import preload_images
    await preload_images(bot)
    
    logger.info("Bot initialized")


async def on_shutdown(bot: Bot) -> None:
    from user_store import flush_user_store
    await flush_user_store()

    from request_store import (
        stop_draft_cleanup_worker,
        stop_draft_reminder_worker,
        stop_scheduled_publish_worker,
    )
    stop_draft_cleanup_worker()
    stop_draft_reminder_worker()
    stop_scheduled_publish_worker()

    admin_flow.stop_scheduled_posts_cleanup_worker()

    from bot.services.poster import stop_poster_worker
    stop_poster_worker()

    from bot.services.backup import stop_backup_worker
    stop_backup_worker()
    
    from storage import flush_all
    await flush_all()
    
    await stop_log_worker()
    
    from userbot.client import UserbotClient
    if UserbotClient._instance:
        await UserbotClient._instance.stop()
    
    logger.info("Bot shutdown complete")


async def main() -> None:
    token = get_config().get("bot_token", "")
    if not token:
        raise RuntimeError("BOT_TOKEN not set")

    asyncio.create_task(start_userbot())

    bot = Bot(
        token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    
    dp = Dispatcher()
    
    dp.update.middleware(UserActionLoggingMiddleware(enabled=True))
    dp.callback_query.outer_middleware(CallbackAckWatchdogMiddleware(delay=1.5))
    
    dp.include_router(dialog_flow.router)
    dp.include_router(admin_flow.router)
    dp.include_router(moderation_flow.router)
    dp.include_router(poster_flow.router)
    dp.include_router(user_flow.router)
    dp.include_router(catalog_flow.router)
    dp.include_router(joinly_flow.router)

    dp.errors.register(on_transient_error)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("Starting bot...")

    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
