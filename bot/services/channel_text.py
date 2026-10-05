import asyncio
import logging

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter

from bot import limits
from bot.formatting import split_html, visible_html_length

logger = logging.getLogger(__name__)


async def editing_userbot(text: str, *, caption: bool):
    from userbot.client import get_userbot

    try:
        userbot = await get_userbot()
        if userbot is None:
            return None
        caption_limit, message_limit = await userbot.text_limits()
        limit = caption_limit if caption else message_limit
        return userbot if visible_html_length(text) <= limit else None
    except Exception:
        logger.warning("event=channel_text.userbot_unavailable", exc_info=True)
        return None


async def try_edit(userbot, chat_id, message_id: int, text: str) -> bool:
    if userbot is None:
        return False
    try:
        return bool(await userbot.edit_channel_text(chat_id, message_id, text))
    except Exception:
        logger.warning(
            "event=channel_text.edit_failed chat_id=%s message_id=%s",
            chat_id, message_id, exc_info=True,
        )
        return False


async def send_description(bot, chat_id: int, text: str, *, reply_markup=None, reply_to_message_id=None):
    parts = split_html(text, limits.MESSAGE_TEXT)
    sent = []
    try:
        for index, part in enumerate(parts):
            for attempt in range(2):
                try:
                    message = await bot.send_message(
                        chat_id, part, parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup if index == len(parts) - 1 else None,
                        reply_to_message_id=reply_to_message_id,
                        disable_web_page_preview=True,
                    )
                except TelegramRetryAfter as exc:
                    if attempt:
                        raise
                    await asyncio.sleep(float(exc.retry_after or 1))
                else:
                    sent.append(message)
                    break
    except BaseException:
        for message in reversed(sent):
            try:
                await bot.delete_message(chat_id, message.message_id)
            except Exception:
                logger.warning(
                    "event=channel_text.rollback_failed chat_id=%s message_id=%s",
                    chat_id, message.message_id, exc_info=True,
                )
        raise
    return sent
