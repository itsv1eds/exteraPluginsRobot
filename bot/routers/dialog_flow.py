
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from bot.context import get_lang
from bot.formatting import plain_html, strip_blockquote_tags, telegram_html, user_mention
from bot.services.dialogs import get_dialog_ref, register_dialog_message, has_dialog_media, send_dialog_message
from bot.texts import t
from request_store import get_request_by_id

router = Router(name="dialog-flow")
logger = logging.getLogger(__name__)


def _is_dialog_reply(message: Message) -> bool:
    if not message.from_user or not message.reply_to_message:
        return False
    if message.chat.type != "private":
        from bot.services.moderation import is_moderation_forum_chat

        if not is_moderation_forum_chat(message.chat.id):
            return False
    return get_dialog_ref(message.chat.id, message.reply_to_message.message_id) is not None


@router.message(F.reply_to_message, _is_dialog_reply)
async def on_dialog_reply(message: Message) -> None:
    ref = get_dialog_ref(message.chat.id, message.reply_to_message.message_id)
    if not ref:
        return
    sender = message.from_user
    lang = get_lang(sender.id)

    text = telegram_html(message.html_text or message.html_caption or message.text or message.caption or "")
    if not text and not has_dialog_media(message):
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

    sender_label = user_mention(sender.id, sender.username)
    body = strip_blockquote_tags(text) or t("dialog_media_body", lang)
    author_is_sender = int(sender.id) == author_id

    if author_is_sender:
        if message.chat.type != "private" or not peer_id or peer_id == author_id:
            await message.answer(t("dialog_delivered", lang), disable_web_page_preview=True)
            return
        moderator_lang = get_lang(peer_id)
        reply_markup = None
        if request_id and entry:
            from bot.cache import get_admins_super

            if peer_id in get_admins_super():
                from bot.keyboards import dialog_author_reply_kb

                reply_markup = dialog_author_reply_kb(request_id, author_id)
        try:
            delivered_messages = await send_dialog_message(
                message.bot,
                peer_id,
                t("dialog_msg_to_admin", moderator_lang, name=plugin_name, sender=sender_label, text=body),
                source=message,
                reply_markup=reply_markup,
            )
        except Exception:
            logger.exception(
                "event=dialog.author_reply_to_moderator_failed from=%s to=%s request_id=%s",
                sender.id, peer_id, request_id,
            )
            await message.answer(t("dialog_deliver_failed", lang), disable_web_page_preview=True)
            return

        for delivered in delivered_messages:
            register_dialog_message(
                int(peer_id), delivered.message_id,
                peer_id=int(sender.id), request_id=request_id,
                author_id=author_id, admin_id=int(peer_id),
            )
        await message.answer(t("dialog_delivered", lang), disable_web_page_preview=True)
        return

    from bot.cache import get_admins

    if int(sender.id) not in get_admins():
        await message.answer(t("admin_denied", lang), disable_web_page_preview=True)
        return

    if admin_id and int(sender.id) != admin_id:
        await message.answer(t("admin_denied", lang), disable_web_page_preview=True)
        return

    peer_lang = get_lang(peer_id)
    try:
        delivered_messages = await send_dialog_message(
            message.bot,
            peer_id,
            t("dialog_msg_to_author", peer_lang, name=plugin_name, sender=sender_label, text=body),
            source=message,
        )
    except Exception:
        logger.exception(
            "event=dialog.relay_failed from=%s to=%s request_id=%s",
            sender.id, peer_id, request_id,
        )
        await message.answer(t("dialog_deliver_failed", lang), disable_web_page_preview=True)
        return

    for delivered in delivered_messages:
        register_dialog_message(
            peer_id, delivered.message_id,
            peer_id=sender.id, request_id=request_id,
            author_id=author_id, admin_id=sender.id,
        )
    await message.answer(t("dialog_delivered", lang), disable_web_page_preview=True)
