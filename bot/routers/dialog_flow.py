
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message

from bot.context import get_lang
from bot.formatting import plain_html, strip_blockquote_tags, telegram_html, user_mention
from bot.helpers import ack
from bot.keyboards import dialog_reply_confirm_kb
from bot.services.audit import add_audit_event
from bot.services.dialogs import (
    get_dialog_ref,
    get_dialog_reply_draft,
    has_dialog_media,
    register_dialog_message,
    save_dialog_reply_draft,
    send_dialog_message,
    take_dialog_reply_draft,
)
from bot.texts import t
from core.request_store import get_request_by_id

router = Router(name="dialog-flow")
logger = logging.getLogger(__name__)


def _is_dialog_reply(message: Message) -> bool:
    if not message.from_user or not message.reply_to_message:
        return False
    if re.match(r"^/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?(?:\s|$)", (message.text or message.caption or "").lstrip()):
        return False
    if message.chat.type != "private":
        from bot.services.moderation import is_moderation_forum_chat

        if not is_moderation_forum_chat(message.chat.id):
            return False
    return get_dialog_ref(message.chat.id, message.reply_to_message.message_id) is not None


async def _preview_forum_reply(message: Message, ref: dict, plugin_name: str, body: str, payload: dict) -> None:
    sender = message.from_user
    lang = get_lang(sender.id)
    preview = await message.reply(
        t(
            "dialog_reply_confirm", lang,
            name=plugin_name,
            recipient=user_mention(ref["peer_id"], payload.get("username")),
            sender=user_mention(sender.id, sender.username),
            media=t("dialog_reply_media", lang) if has_dialog_media(message) else "",
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=dialog_reply_confirm_kb(lang),
        disable_web_page_preview=True,
    )
    save_dialog_reply_draft(message.chat.id, preview.message_id, {
        "owner_id": sender.id,
        "owner_label": f"@{sender.username}" if sender.username else sender.full_name,
        "sender_label": user_mention(sender.id, sender.username),
        "peer_id": int(ref["peer_id"]),
        "author_id": int(ref.get("author_id") or 0),
        "request_id": str(ref.get("request_id") or ""),
        "plugin_name": plugin_name,
        "body": body,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": message.model_dump(mode="json", include={
            "message_id", "date", "chat", "from_user", "text", "entities", "caption", "caption_entities",
            "photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker",
        }, exclude_none=True),
    })


async def _finish_reply_prompt(cb: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception:
        logger.exception("event=dialog.reply_prompt_edit_failed chat_id=%s message_id=%s", cb.message.chat.id, cb.message.message_id)


@router.callback_query(F.data.in_({"dlg:reply:send", "dlg:reply:cancel"}))
async def on_confirm_forum_reply(cb: CallbackQuery) -> None:
    lang = get_lang(cb.from_user.id)
    if not isinstance(cb.message, Message):
        await ack(cb, t("dialog_reply_closed", lang), show_alert=True)
        return
    chat_id, message_id = cb.message.chat.id, cb.message.message_id
    draft = get_dialog_reply_draft(chat_id, message_id)
    if not draft:
        await ack(cb, t("dialog_reply_closed", lang), show_alert=True)
        return
    if cb.from_user.id != draft["owner_id"]:
        await ack(cb, t("dialog_reply_owner", lang, moderator=draft["owner_label"]), show_alert=True)
        return
    cancel = cb.data == "dlg:reply:cancel"
    if not cancel:
        from bot.cache import get_admins

        if cb.from_user.id not in get_admins():
            await ack(cb, t("admin_denied", lang), show_alert=True)
            return
    draft = take_dialog_reply_draft(chat_id, message_id)
    if not draft:
        await ack(cb, t("dialog_reply_closed", lang), show_alert=True)
        return
    await ack(cb)
    if cancel:
        await _finish_reply_prompt(cb, t("dialog_reply_cancelled", lang))
        return

    peer_id = draft["peer_id"]
    try:
        source = Message.model_validate(draft["source"]).as_(cb.bot)
        delivered_messages = await send_dialog_message(
            cb.bot, peer_id,
            t("dialog_msg_to_author", get_lang(peer_id), name=draft["plugin_name"], sender=draft["sender_label"], text=draft["body"]),
            source=source,
        )
    except Exception:
        logger.exception("event=dialog.confirmed_reply_failed from=%s to=%s request_id=%s", cb.from_user.id, peer_id, draft["request_id"])
        save_dialog_reply_draft(chat_id, message_id, draft)
        await _finish_reply_prompt(cb, t("dialog_reply_send_failed", lang), dialog_reply_confirm_kb(lang))
        return

    for delivered in delivered_messages:
        register_dialog_message(
            peer_id, delivered.message_id,
            peer_id=cb.from_user.id, request_id=draft["request_id"],
            author_id=draft["author_id"], admin_id=cb.from_user.id,
        )
    add_audit_event(
        "moderation.message_author", actor_id=cb.from_user.id,
        actor=cb.from_user.username or cb.from_user.full_name or "",
        request_id=draft["request_id"], details={"source_chat_id": chat_id, "source_message_id": source.message_id},
    )
    await _finish_reply_prompt(cb, t("dialog_delivered", lang))


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
        add_audit_event("moderation.author_reply", actor_id=sender.id, actor=sender_label,
                        request_id=request_id, details={"user_id": peer_id, "text": body})
        await message.answer(t("dialog_delivered", lang), disable_web_page_preview=True)
        return

    from bot.cache import get_admins

    if int(sender.id) not in get_admins():
        await message.answer(t("admin_denied", lang), disable_web_page_preview=True)
        return

    if admin_id and int(sender.id) != admin_id:
        await message.answer(t("admin_denied", lang), disable_web_page_preview=True)
        return

    if message.chat.type != "private":
        await _preview_forum_reply(message, ref, plugin_name, body, payload)
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
    add_audit_event("moderation.message_author", actor_id=sender.id, actor=sender_label,
                    request_id=request_id, details={"user_id": peer_id, "text": body})
    await message.answer(t("dialog_delivered", lang), disable_web_page_preview=True)
