from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from bot.context import get_lang
from bot.helpers import ack
from bot.formatting import plain_html, strip_blockquote_tags, telegram_html, user_mention
from bot.menu_owner import MenuOwnerMiddleware
from bot.services.audit import add_audit_event
from bot.services.dialogs import register_dialog_message, has_dialog_media, send_dialog_message
from bot.services.moderation import moderation_config, request_title
from bot.states import UserFlow
from bot.texts import t
from request_store import get_request_by_id, update_request_payload, update_request_status

router = Router(name="author-flow")
router.callback_query.middleware(MenuOwnerMiddleware())
logger = logging.getLogger(__name__)

_APPEAL_MIN_LEN = 40


def _question_plugin_context(entry: dict | None) -> tuple[str, str | None, str]:
    payload = entry.get("payload") if isinstance(entry, dict) and isinstance(entry.get("payload"), dict) else {}
    plugin = payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}
    description = str(payload.get("description_ru") or payload.get("description_en") or plugin.get("description") or "—")
    file_path = str(plugin.get("file_path") or "").strip()
    file_id = str(payload.get("moderation_file_id") or plugin.get("file_id") or "").strip()
    if file_path and Path(file_path).exists() and Path(file_path).stat().st_size <= 50 * 1024 * 1024:
        return description, file_path, ""
    return description, None, file_id


def _own_request(cb: CallbackQuery, request_id: str) -> dict | None:
    entry = get_request_by_id(request_id)
    if not isinstance(entry, dict):
        return None
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    if payload.get("user_id") != (cb.from_user.id if cb.from_user else None):
        return None
    return entry


@router.callback_query(F.data.startswith("usr:modcontact:"))
async def on_contact_moderation(cb: CallbackQuery, state: FSMContext) -> None:
    request_id = cb.data.split(":", 2)[2]
    lang = get_lang(cb.from_user.id if cb.from_user else None)
    entry = _own_request(cb, request_id)
    if not entry:
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    await state.set_state(UserFlow.entering_moderation_contact)
    await state.update_data(modcontact_request_id=request_id)
    try:
        await cb.message.answer(
            t("modcontact_prompt", lang, name=plain_html(request_title(entry))),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        pass
    await ack(cb)


@router.message(UserFlow.entering_moderation_contact)
async def on_moderation_contact_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    request_id = str(data.get("modcontact_request_id") or "")
    slug = str(data.get("modcontact_slug") or "")
    user = message.from_user
    if not user or (not request_id and not slug):
        await state.set_state(UserFlow.idle)
        return

    entry = get_request_by_id(request_id) if request_id else None
    if request_id and not isinstance(entry, dict):
        await state.set_state(UserFlow.idle)
        return

    lang = get_lang(user.id)
    text = telegram_html(message.html_text or message.html_caption or message.text or message.caption or "").strip()
    if not text and not has_dialog_media(message):
        await message.answer(t("dialog_need_text", lang), disable_web_page_preview=True)
        return

    cfg = moderation_config()
    description, file_path, file_id = _question_plugin_context(entry)
    body = t(
        "modcontact_forum", "ru",
        name=plain_html(request_title(entry) if entry else slug),
        sender=user_mention(user.id, user.username),
        description=strip_blockquote_tags(telegram_html(description)),
        text=strip_blockquote_tags(text) or t("dialog_media_body", "ru"),
    )
    try:
        delivered_messages = await send_dialog_message(
            message.bot, cfg["chat_id"], body,
            source=message, message_thread_id=cfg["topic_id"],
        )
        delivered = delivered_messages[0]
    except Exception:
        logger.exception("event=modcontact.deliver_failed user_id=%s request_id=%s", user.id, request_id)
        await message.answer(t("modcontact_failed", lang), disable_web_page_preview=True)
        await state.set_state(UserFlow.idle)
        return
    try:
        if file_path:
            await message.bot.send_document(
                cfg["chat_id"],
                FSInputFile(file_path),
                message_thread_id=cfg["topic_id"],
                reply_to_message_id=delivered.message_id,
                allow_sending_without_reply=True,
            )
        elif file_id:
            await message.bot.send_document(
                cfg["chat_id"],
                file_id,
                message_thread_id=cfg["topic_id"],
                reply_to_message_id=delivered.message_id,
                allow_sending_without_reply=True,
            )
    except Exception:
        logger.exception("event=modcontact.plugin_file_failed user_id=%s request_id=%s", user.id, request_id)

    for sent in delivered_messages:
        register_dialog_message(
            int(cfg["chat_id"]), int(sent.message_id),
            peer_id=int(user.id), request_id=str(request_id or slug),
            author_id=int(user.id), admin_id=0,
        )
    try:
        from bot.services.admin_notifications import notify_admins_event

        await notify_admins_event(
            message.bot, "author_replies",
            t("admin_notify_author_reply", "ru",
              name=plain_html(request_title(entry) if entry else slug),
              sender=user_mention(user.id, user.username),
              text=strip_blockquote_tags(text)),
        )
    except Exception:
        logger.exception("event=modcontact.notify_admins_failed user_id=%s", user.id)

    add_audit_event(
        "moderation.author_question",
        actor_id=int(user.id),
        actor=user.username or user.full_name or "",
        request_id=str(request_id or slug),
    )
    await message.answer(t("modcontact_sent", lang), disable_web_page_preview=True)
    await state.set_state(UserFlow.idle)


@router.callback_query(F.data.startswith("usr:modremoved:"))
async def on_contact_moderation_removed(cb: CallbackQuery, state: FSMContext) -> None:
    slug = cb.data.split(":", 2)[2]
    lang = get_lang(cb.from_user.id if cb.from_user else None)
    await state.set_state(UserFlow.entering_moderation_contact)
    await state.update_data(modcontact_request_id="", modcontact_slug=slug)
    try:
        await cb.message.answer(
            t("modremoved_prompt", lang, name=plain_html(slug)),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        pass
    await ack(cb)


@router.callback_query(F.data.startswith("usr:appeal:"))
async def on_request_appeal(cb: CallbackQuery, state: FSMContext) -> None:
    request_id = cb.data.split(":", 2)[2]
    lang = get_lang(cb.from_user.id if cb.from_user else None)
    entry = _own_request(cb, request_id)
    if not entry:
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    if payload.get("is_appeal"):
        await cb.answer(t("appeal_already_used", lang), show_alert=True)
        return
    if entry.get("status") != "rejected":
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    await state.set_state(UserFlow.entering_request_appeal)
    await state.update_data(appeal_request_id=request_id)
    try:
        await cb.message.answer(
            t("appeal_prompt_comment", lang, name=plain_html(request_title(entry))),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        pass
    await ack(cb)


@router.message(UserFlow.entering_request_appeal)
async def on_request_appeal_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    request_id = str(data.get("appeal_request_id") or "")
    user = message.from_user
    if not request_id or not user:
        await state.set_state(UserFlow.idle)
        return

    lang = get_lang(user.id)
    entry = get_request_by_id(request_id)
    if not isinstance(entry, dict) or entry.get("status") != "rejected":
        await state.set_state(UserFlow.idle)
        return

    text = telegram_html(message.html_text or message.text or "").strip()
    if len(strip_blockquote_tags(text)) < _APPEAL_MIN_LEN:
        await message.answer(
            t("appeal_comment_too_short", lang, min=_APPEAL_MIN_LEN),
            disable_web_page_preview=True,
        )
        return

    update_request_payload(request_id, {
        "is_appeal": True,
        "appeal_comment": text,
        "moderation_votes": {},
    })
    update_request_status(request_id, "pending", comment="Апелляция автора")
    entry = get_request_by_id(request_id)

    from bot.routers.user_flow import notify_admins_request

    try:
        await notify_admins_request(message.bot, entry)
    except Exception:
        logger.exception("event=appeal.notify_failed request_id=%s", request_id)

    try:
        from bot.services.admin_notifications import notify_admins_event

        await notify_admins_event(
            message.bot, "appeals",
            t("admin_notify_appeal", "ru",
              name=plain_html(request_title(entry)),
              sender=user_mention(user.id, user.username),
              comment=strip_blockquote_tags(text)),
        )
    except Exception:
        logger.exception("event=appeal.notify_admins_failed request_id=%s", request_id)

    add_audit_event(
        "moderation.appeal_submitted",
        actor_id=int(user.id),
        actor=user.username or user.full_name or "",
        request_id=str(request_id),
    )
    await message.answer(t("appeal_sent", lang), disable_web_page_preview=True)
    await state.set_state(UserFlow.idle)


@router.callback_query(F.data.startswith("dlg:"))
async def on_dialog_moderation_action(cb: CallbackQuery, state: FSMContext) -> None:
    from bot.cache import get_admins_super
    from user_store import ban_user

    parts = (cb.data or "").split(":")
    if len(parts) < 4:
        await cb.answer()
        return
    action = parts[1]
    actor = cb.from_user
    if not actor or actor.id not in get_admins_super():
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    try:
        author_id = int(parts[2])
    except ValueError:
        await cb.answer()
        return
    request_id = ":".join(parts[3:])

    if action == "ban":
        ban_user(author_id, reason="Нарушение в диалоге с модерацией")
        add_audit_event(
            "moderation.author_banned",
            actor_id=actor.id, actor=actor.username or actor.full_name or "",
            request_id=request_id, details={"user_id": author_id},
        )
        await cb.answer(t("dialog_author_banned", "ru"), show_alert=True)
    else:
        await cb.answer()
        return

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
