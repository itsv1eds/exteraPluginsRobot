from __future__ import annotations

from typing import Any

from aiogram.types import Message

from bot.services.moderation import is_moderation_forum_chat, moderation_config


def moderation_topic_kwargs(message: Message) -> dict[str, int]:
    try:
        if is_moderation_forum_chat(message.chat.id):
            return {"message_thread_id": int(moderation_config()["topic_id"])}
    except Exception:
        pass
    return {}


async def answer_in_moderation_topic(message: Message, text: str, **kwargs: Any) -> Message:
    params = {**kwargs, **moderation_topic_kwargs(message)}
    return await message.bot.send_message(message.chat.id, text, **params)
