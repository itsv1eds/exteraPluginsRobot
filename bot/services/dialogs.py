
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from storage import load_dialogs, save_dialogs

_MAX_THREADS = 500


def _key(chat_id: int, message_id: int) -> str:
    return f"{chat_id}:{message_id}"


def register_dialog_message(
    chat_id: int,
    message_id: int,
    *,
    peer_id: int,
    request_id: str,
    author_id: int,
    admin_id: int,
) -> None:
    doc = load_dialogs()
    threads = doc.get("threads", {})
    threads[_key(chat_id, message_id)] = {
        "peer_id": int(peer_id),
        "request_id": str(request_id),
        "author_id": int(author_id),
        "admin_id": int(admin_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if len(threads) > _MAX_THREADS:
        ordered = sorted(threads.items(), key=lambda kv: str(kv[1].get("created_at") or ""))
        for stale_key, _ in ordered[: len(threads) - _MAX_THREADS]:
            threads.pop(stale_key, None)
    doc["threads"] = threads
    save_dialogs(doc)


def get_dialog_ref(chat_id: int, message_id: int) -> Optional[Dict[str, Any]]:
    doc = load_dialogs()
    ref = doc.get("threads", {}).get(_key(chat_id, message_id))
    return ref if isinstance(ref, dict) else None


def has_dialog_media(message) -> bool:
    return any(getattr(message, kind, None) for kind in (
        "photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker",
    ))


async def send_dialog_message(bot, chat_id: int, text: str, *, source=None, reply_markup=None, **kwargs):
    from aiogram.enums import ParseMode

    delivered = await bot.send_message(
        chat_id, text, parse_mode=ParseMode.HTML,
        disable_web_page_preview=True, reply_markup=reply_markup, **kwargs,
    )
    messages = [delivered]
    if source is not None and has_dialog_media(source):
        copy_kwargs = {
            "reply_to_message_id": delivered.message_id,
            "allow_sending_without_reply": True,
        }
        if kwargs.get("message_thread_id"):
            copy_kwargs["message_thread_id"] = kwargs["message_thread_id"]
        if any(getattr(source, kind, None) for kind in (
            "photo", "video", "animation", "document", "audio", "voice",
        )):
            copy_kwargs["caption"] = ""
        try:
            copied = await source.copy_to(chat_id, **copy_kwargs)
        except Exception:
            from bot.helpers import blank_and_delete

            await blank_and_delete(bot, chat_id, delivered.message_id)
            raise
        messages.append(copied)
    return messages
