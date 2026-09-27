import asyncio
import logging
from pathlib import Path

from aiogram.types import FSInputFile

from bot import limits
from bot.helpers import blank_and_delete, fit_filename
from plugin_formats import payload_extension, plugin_extension
from bot.services.bot_mtproto import get_file_client

logger = logging.getLogger(__name__)


def plugin_document(plugin: dict, file_id: str | None = None):
    path = Path(plugin.get("file_path") or "")
    stored_id = file_id or plugin.get("file_id")
    if path.is_file():
        if path.stat().st_size > limits.BOT_UPLOAD_BYTES:
            if stored_id:
                return stored_id
            raise ValueError("large_file_transfer_unavailable")
        if not plugin_extension(path):
            return FSInputFile(path)
        name = fit_filename(str(plugin.get("id") or "plugin"), payload_extension(plugin))
        return FSInputFile(path, filename=name)
    return stored_id or None


async def download_large_plugin(bot, document, destination: Path):
    from bot.services.moderation import moderation_config
    client = await get_file_client()
    from bot.cache import get_config

    transfer_chat = (get_config().get("userbot") or {}).get("file_transfer_chat_id")
    cfg = {"chat_id": int(transfer_chat), "topic_id": None} if transfer_chat else moderation_config()
    try:
        entity = await client.get_entity(cfg["chat_id"])
    except ValueError:
        chat = await bot.get_chat(cfg["chat_id"])
        if chat.username:
            entity = await client.get_entity(chat.username)
        else:
            from telethon.tl.types import InputChannel
            entity = await client.get_entity(InputChannel(abs(int(cfg["chat_id"])) - 1000000000000, 0))
    if not transfer_chat and not getattr(entity, "megagroup", False):
        raise ValueError("large_file_transfer_unavailable")
    staging = None
    try:
        staging = await bot.send_document(
            cfg["chat_id"], document.file_id,
            message_thread_id=cfg["topic_id"], disable_notification=True,
        )
        source = await client.get_messages(entity, ids=staging.message_id)
        if not source or not source.document:
            raise ValueError("download_error")
        if source.document.size > limits.ELYX_FILE_BYTES:
            raise ValueError("file_too_large")
        result = await asyncio.wait_for(
            client.download_media(source, file=str(destination)), timeout=600,
        )
        if not result:
            raise ValueError("download_error")
    except Exception:
        logger.exception("event=plugin.large_download_failed bot_id=%s size=%s", bot.id, document.file_size)
        raise
    finally:
        if staging:
            await blank_and_delete(bot, cfg["chat_id"], staging.message_id)
