from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.context import get_language
from bot.helpers import try_react_pray
from bot.formatting import telegram_html
from bot.services.moderation import (
    can_vote_in_context,
    notify_superadmins_if_threshold,
    refresh_forum_vote_keyboard,
    set_vote,
    set_vote_reason,
)
from bot.states import UserFlow
from bot.texts import t
from request_store import get_request_by_id

router = Router(name="moderation-flow")


@router.callback_query(F.data.startswith("modvote:"))
async def on_moderation_vote(cb: CallbackQuery, state: FSMContext) -> None:
    parts = (cb.data or "").split(":", 2)
    if len(parts) != 3 or parts[1] not in {"yes", "no"}:
        await cb.answer()
        return

    vote = parts[1]
    request_id = parts[2]
    chat_id = cb.message.chat.id if cb.message and cb.message.chat else None
    user = cb.from_user
    if not can_vote_in_context(user.id if user else None, chat_id):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    name = (user.full_name if user else "") or ""
    username = (user.username if user else "") or ""
    entry = set_vote(request_id, int(user.id), username, name, vote)
    if not entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    await refresh_forum_vote_keyboard(cb.bot, entry)
    await notify_superadmins_if_threshold(cb.bot, entry)

    await state.set_state(UserFlow.entering_moderation_vote_reason)
    await state.update_data(moderation_vote_request_id=request_id)
    lang = await get_language(cb, state)
    await cb.answer(t("moderation_vote_reason_prompt", lang), show_alert=True)


@router.message(UserFlow.entering_moderation_vote_reason)
async def on_moderation_vote_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    request_id = str(data.get("moderation_vote_request_id") or "")
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

    reply_to = message.reply_to_message
    if expected_reply_ids and (not reply_to or int(reply_to.message_id) not in expected_reply_ids):
        await state.set_state(UserFlow.idle)
        return

    text = telegram_html(message.html_text or message.text or "")
    if not text:
        return

    entry = set_vote_reason(request_id, int(user.id), text)
    if entry:
        await refresh_forum_vote_keyboard(message.bot, entry)
        await notify_superadmins_if_threshold(message.bot, entry)
        await try_react_pray(message)

    await state.set_state(UserFlow.idle)
