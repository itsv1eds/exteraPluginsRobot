from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.context import get_language
from bot.helpers import try_react_pray
from bot.formatting import telegram_html
from bot.services.audit import add_audit_event
from bot.services.moderation import (
    can_vote_in_context,
    forum_text_with_votes,
    moderation_vote_kb,
    refresh_forum_vote_keyboard,
    set_vote,
    set_vote_reason,
    vote_counts,
)
from bot.services.admin_notifications import (
    notify_superadmins_if_threshold,
    refresh_admin_notify_messages,
)
from bot.states import UserFlow
from bot.texts import t
from request_store import get_request_by_id

router = Router(name="moderation-flow")


async def _refresh_inline_vote_message(bot, inline_message_id: str | None, entry: dict | None, request_id: str) -> None:
    if not inline_message_id or not entry:
        return
    yes, no, _ = vote_counts(entry)
    try:
        await bot.edit_message_text(
            forum_text_with_votes(entry),
            inline_message_id=inline_message_id,
            parse_mode="HTML",
            reply_markup=moderation_vote_kb(request_id, yes, no),
            disable_web_page_preview=True,
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("modvote:"))
async def on_moderation_vote(cb: CallbackQuery, state: FSMContext) -> None:
    parts = (cb.data or "").split(":", 2)
    if len(parts) != 3 or parts[1] not in {"yes", "no"}:
        await cb.answer()
        return

    vote = parts[1]
    request_id = parts[2]
    chat_id = cb.message.chat.id if cb.message and cb.message.chat else None
    chat_type = getattr(cb.message.chat, "type", "") if cb.message and cb.message.chat else ""
    is_private_chat = (getattr(chat_type, "value", chat_type) == "private")
    user = cb.from_user
    entry = get_request_by_id(request_id)
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    inline_public = bool(payload.get("moderation_inline_public")) if isinstance(payload, dict) else False
    if not inline_public and not can_vote_in_context(user.id if user else None, chat_id):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    name = (user.full_name if user else "") or ""
    username = (user.username if user else "") or ""
    entry = set_vote(request_id, int(user.id), username, name, vote)
    if not entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    await refresh_forum_vote_keyboard(cb.bot, entry)
    await refresh_admin_notify_messages(cb.bot, entry)
    await _refresh_inline_vote_message(cb.bot, cb.inline_message_id, entry, request_id)
    await notify_superadmins_if_threshold(cb.bot, entry)
    add_audit_event(
        "moderation.vote",
        actor_id=int(user.id),
        actor=username or name,
        request_id=request_id,
        details={"vote": vote, "chat_id": chat_id},
    )

    await state.set_state(UserFlow.entering_moderation_vote_reason)
    await state.update_data(
        moderation_vote_request_id=request_id,
        moderation_vote_inline_message_id=cb.inline_message_id or "",
        moderation_vote_dm=is_private_chat,
        moderation_vote_source_chat_id=chat_id or 0,
        moderation_vote_source_message_id=cb.message.message_id if cb.message else 0,
    )
    lang = await get_language(cb, state)
    prompt_key = "moderation_vote_reason_dm_prompt" if is_private_chat else "moderation_vote_reason_prompt"
    await cb.answer(t(prompt_key, lang), show_alert=True)


@router.message(UserFlow.entering_moderation_vote_reason)
async def on_moderation_vote_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    request_id = str(data.get("moderation_vote_request_id") or "")
    inline_message_id = str(data.get("moderation_vote_inline_message_id") or "")
    dm_reason = bool(data.get("moderation_vote_dm"))
    if not request_id:
        await state.set_state(UserFlow.idle)
        return

    user = message.from_user
    if not user:
        await state.set_state(UserFlow.idle)
        return

    entry = get_request_by_id(request_id)
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    info = payload.get("moderation_forum_message") if isinstance(payload, dict) else {}
    expected_reply_ids: set[int] = set()
    if isinstance(info, dict):
        for key in ("message_id", "text_message_id"):
            try:
                value = int(info.get(key) or 0)
            except Exception:
                value = 0
            if value:
                expected_reply_ids.add(value)
    try:
        source_chat_id = int(data.get("moderation_vote_source_chat_id") or 0)
        source_message_id = int(data.get("moderation_vote_source_message_id") or 0)
    except Exception:
        source_chat_id = 0
        source_message_id = 0
    if source_message_id and source_chat_id == message.chat.id:
        expected_reply_ids.add(source_message_id)

    reply_to = message.reply_to_message
    if expected_reply_ids and not inline_message_id and not dm_reason and (not reply_to or int(reply_to.message_id) not in expected_reply_ids):
        await state.set_state(UserFlow.idle)
        return

    text = telegram_html(message.html_text or message.text or "")
    if not text:
        return

    entry = set_vote_reason(request_id, int(user.id), text)
    if entry:
        await refresh_forum_vote_keyboard(message.bot, entry)
        await refresh_admin_notify_messages(message.bot, entry)
        await _refresh_inline_vote_message(message.bot, inline_message_id, entry, request_id)
        await notify_superadmins_if_threshold(message.bot, entry)
        await try_react_pray(message)
        add_audit_event(
            "moderation.vote_reason",
            actor_id=int(user.id),
            actor=user.username or user.full_name or "",
            request_id=request_id,
        )

    await state.set_state(UserFlow.idle)
