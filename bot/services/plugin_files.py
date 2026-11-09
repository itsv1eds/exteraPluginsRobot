import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramBadRequest

from bot import limits
from bot.helpers import blank_and_delete, fit_filename
from core.plugin_formats import payload_extension, plugin_extension
from bot.services.bot_mtproto import get_file_client

logger = logging.getLogger(__name__)


def plugin_document(plugin: dict, file_id: str | None = None):
    if file_id:
        return file_id
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


async def send_plugin_document(bot, chat_id, plugin, *, file_id: str | None = None, **kwargs):
    if file_id:
        try:
            return await bot.send_document(chat_id, document=plugin_document(plugin, file_id), **kwargs)
        except TelegramBadRequest as exc:
            if (not any(marker in str(exc).lower() for marker in ("wrong file", "file_id", "file identifier", "file reference"))
                    or not Path(plugin.get("file_path") or "").is_file()):
                raise
            plugin = {**plugin, "file_id": None}
    path = Path(plugin.get("file_path") or "")
    if not path.is_file() or path.stat().st_size <= limits.BOT_UPLOAD_BYTES or plugin.get("file_id"):
        return await bot.send_document(chat_id, document=plugin_document(plugin), **kwargs)
    from telethon.tl.types import DocumentAttributeFilename
    from telethon.utils import pack_bot_file_id

    client = await get_file_client()
    entity = await _file_entity(client, bot, chat_id)
    name = fit_filename(str(plugin.get("id") or "plugin"), payload_extension(plugin))
    caption = {"caption": kwargs["caption"], "parse_mode": kwargs.get("parse_mode")} if kwargs.get("caption") is not None else {}
    message = await asyncio.wait_for(client.send_file(
        entity, str(path), force_document=True, attributes=[DocumentAttributeFilename(name)],
        reply_to=kwargs.get("reply_to_message_id") or kwargs.get("message_thread_id"),
        silent=bool(kwargs.get("disable_notification")),
        **caption,
    ), timeout=600)
    file_id = pack_bot_file_id(message.document)
    if not file_id:
        raise ValueError("large_file_transfer_unavailable")
    plugin["file_id"] = file_id
    return SimpleNamespace(message_id=message.id, document=SimpleNamespace(file_id=file_id, file_name=name))


async def _file_entity(client, bot, chat_id):
    try:
        return await client.get_entity(chat_id)
    except ValueError:
        chat = await bot.get_chat(chat_id)
        if chat.username:
            return await client.get_entity(chat.username)
        from telethon.tl.types import InputChannel

        return await client.get_entity(InputChannel(abs(int(chat_id)) - 1000000000000, 0))


async def download_large_plugin(bot, document, destination: Path):
    from bot.services.moderation import moderation_config
    client = await get_file_client()
    from bot.cache import get_config

    transfer_chat = (get_config().get("userbot") or {}).get("file_transfer_chat_id")
    cfg = {"chat_id": int(transfer_chat), "topic_id": None} if transfer_chat else moderation_config()
    entity = await _file_entity(client, bot, cfg["chat_id"])
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
