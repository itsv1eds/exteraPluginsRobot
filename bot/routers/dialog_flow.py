
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import Message

from bot.context import get_lang
from bot.formatting import plain_html, strip_blockquote_tags, telegram_html, user_mention
from bot.services.dialogs import get_dialog_ref, register_dialog_message
from bot.texts import t
from request_store import get_request_by_id

router = Router(name="dialog-flow")
logger = logging.getLogger(__name__)


def _is_dialog_reply(message: Message) -> bool:
    if not message.from_user or not message.reply_to_message:
        return False
    if message.chat.type != "private":
        return False
    return get_dialog_ref(message.chat.id, message.reply_to_message.message_id) is not None


@router.message(F.reply_to_message, _is_dialog_reply)
async def on_dialog_reply(message: Message) -> None:
    ref = get_dialog_ref(message.chat.id, message.reply_to_message.message_id)
    if not ref:
        return
    sender = message.from_user
    lang = get_lang(sender.id)

    text = telegram_html(message.html_text or message.text or "")
    if not text:
        await message.answer(t("dialog_need_text", lang), disable_web_page_preview=True)
        return

    peer_id = int(ref.get("peer_id") or 0)
    author_id = int(ref.get("author_id") or 0)
    admin_id = int(ref.get("admin_id") or 0)
    request_id = str(ref.get("request_id") or "")
    if not peer_id:
        return

    entry = get_request_by_id(request_id) if request_id else None
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    item = payload.get("plugin") or payload.get("icon") or {}
    plugin_name = plain_html(item.get("name") or "—")

    template = "dialog_msg_to_author" if peer_id == author_id else "dialog_msg_to_admin"
    peer_lang = get_lang(peer_id)
    sender_label = user_mention(sender.id, sender.username)
    body = strip_blockquote_tags(text)

    try:
        delivered = await message.bot.send_message(
            peer_id,
            t(template, peer_lang, name=plugin_name, sender=sender_label, text=body),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception(
            "event=dialog.relay_failed from=%s to=%s request_id=%s",
            sender.id, peer_id, request_id,
        )
        await message.answer(t("dialog_deliver_failed", lang), disable_web_page_preview=True)
        return

    register_dialog_message(
        peer_id, delivered.message_id,
        peer_id=sender.id, request_id=request_id,
        author_id=author_id, admin_id=admin_id,
    )
    await message.answer(t("dialog_delivered", lang), disable_web_page_preview=True)
