from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.context import get_lang
from bot.formatting import plain_html, strip_blockquote_tags, telegram_html, user_mention
from bot.services.audit import add_audit_event
from bot.services.dialogs import register_dialog_message
from bot.services.moderation import moderation_config, request_title
from bot.states import UserFlow
from bot.texts import t
from request_store import get_request_by_id, update_request_payload, update_request_status

router = Router(name="author-flow")
logger = logging.getLogger(__name__)

_APPEAL_MIN_LEN = 40


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
    try:
        await cb.answer()
    except Exception:
        pass


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
    text = telegram_html(message.html_text or message.text or "").strip()
    if not text:
        await message.answer(t("dialog_need_text", lang), disable_web_page_preview=True)
        return

    cfg = moderation_config()
    body = t(
        "modcontact_forum", "ru",
        name=plain_html(request_title(entry) if entry else slug),
        sender=user_mention(user.id, user.username),
        text=strip_blockquote_tags(text),
    )
    try:
        delivered = await message.bot.send_message(
            cfg["chat_id"], body,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            message_thread_id=cfg["topic_id"],
        )
    except Exception:
        logger.exception("event=modcontact.deliver_failed user_id=%s request_id=%s", user.id, request_id)
        await message.answer(t("modcontact_failed", lang), disable_web_page_preview=True)
        await state.set_state(UserFlow.idle)
        return

    register_dialog_message(
        int(cfg["chat_id"]), int(delivered.message_id),
        peer_id=int(user.id), request_id=str(request_id or slug),
        author_id=int(user.id), admin_id=0,
    )
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
    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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
    from bot.services.moderation import is_moderation_forum_chat
    from bot.services.validation import block_plugin
    from user_store import ban_user

    parts = (cb.data or "").split(":")
    if len(parts) < 4:
        await cb.answer()
        return
    action = parts[1]
    actor = cb.from_user
    chat_id = cb.message.chat.id if cb.message and cb.message.chat else None
    if not actor or (actor.id not in get_admins_super() and not is_moderation_forum_chat(chat_id)):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    if actor.id not in get_admins_super():
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    try:
        author_id = int(parts[2])
    except ValueError:
        await cb.answer()
        return
    request_id = ":".join(parts[3:])
    entry = get_request_by_id(request_id)

    if action == "ban":
        ban_user(author_id, reason="Нарушение в диалоге с модерацией")
        add_audit_event(
            "moderation.author_banned",
            actor_id=actor.id, actor=actor.username or actor.full_name or "",
            request_id=request_id, details={"user_id": author_id},
        )
        await cb.answer(t("dialog_author_banned", "ru"), show_alert=True)
    elif action == "rejapp":
        plugin_id = ""
        if isinstance(entry, dict):
            payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
            item = payload.get("plugin") or payload.get("icon") or {}
            plugin_id = str(item.get("id") or "").strip()
            update_request_status(request_id, "rejected", comment="Апелляция отклонена модерацией")
        if plugin_id:
            block_plugin(plugin_id)
        add_audit_event(
            "moderation.appeal_denied",
            actor_id=actor.id, actor=actor.username or actor.full_name or "",
            request_id=request_id, details={"plugin_id": plugin_id},
        )
        try:
            await cb.bot.send_message(
                author_id,
                t("notify_rejected_blocked", get_lang(author_id)).strip(),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("event=dialog.notify_appeal_rejected_failed user_id=%s", author_id)
        await cb.answer(t("dialog_appeal_rejected", "ru"), show_alert=True)
    else:
        await cb.answer()
        return

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
