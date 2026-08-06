import asyncio
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.context import get_language
from bot.formatting import plain_html, telegram_html, user_mention
from bot.helpers import blank_and_delete
from bot.keyboards import moderation_vote_reason_kb, moderation_vote_template_kb
from bot.services.audit import add_audit_event
from bot.services.moderation import (
    can_accept_vote,
    can_vote_in_context,
    clear_pending_vote,
    commit_pending_vote,
    forum_text_with_votes,
    get_pending_vote,
    moderation_vote_kb,
    refresh_forum_vote_keyboard,
    request_title,
    require_vote_reason,
    start_pending_vote,
    update_pending_vote,
    vote_counts,
)
from bot.services.admin_notifications import (
    notify_superadmins_if_threshold,
    refresh_admin_notify_messages,
)
from bot.states import UserFlow
from bot.texts import t
from request_store import get_request_by_callback_token, get_request_by_id

router = Router(name="moderation-flow")
logger = logging.getLogger(__name__)

VOTE_PROMPT_TTL = 60
_prompt_timers: dict[str, asyncio.Task] = {}


def _vote_templates(vote: str | None) -> list[str]:
    from bot.routers.admin_flow import _load_templates

    return _load_templates("approve" if str(vote) == "yes" else "reject")


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


def _prompt_text(entry: dict | None, vote: str, lang: str, moderator: str) -> str:
    key = "vote_reason_title" if require_vote_reason() else "vote_reason_title_optional"
    return t(
        key, lang,
        vote=t(f"vote_value_{vote}", lang),
        name=plain_html(request_title(entry)),
        moderator=moderator,
    )


def _timer_key(request_id: str, user_id: int) -> str:
    return f"{request_id}:{user_id}"


def _pending_vote_is_active(item: dict | None, now: datetime | None = None) -> bool:
    if not isinstance(item, dict):
        return False
    raw_started_at = item.get("started_at")
    if not raw_started_at:
        return False
    try:
        started_at = datetime.fromisoformat(str(raw_started_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    else:
        started_at = started_at.astimezone(timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age = (current.astimezone(timezone.utc) - started_at).total_seconds()
    return -5 <= age <= VOTE_PROMPT_TTL


async def _leave_vote_reason_state(state: FSMContext) -> None:
    current = await state.get_state()
    vote_reason_state = UserFlow.entering_moderation_vote_reason
    if current == vote_reason_state or current == vote_reason_state.state:
        await state.set_state(UserFlow.idle)


def _cancel_prompt_timer(request_id: str, user_id: int) -> None:
    task = _prompt_timers.pop(_timer_key(request_id, user_id), None)
    if task and not task.done():
        task.cancel()


def _schedule_prompt_expiry(bot, request_id: str, user_id: int) -> None:
    _cancel_prompt_timer(request_id, user_id)

    async def _expire() -> None:
        try:
            await asyncio.sleep(VOTE_PROMPT_TTL)
            item = get_pending_vote(request_id, int(user_id))
            if not item:
                return
            await _delete_prompt(bot, item)
            clear_pending_vote(request_id, int(user_id))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("vote prompt expiry failed request_id=%s user_id=%s", request_id, user_id)
        finally:
            _prompt_timers.pop(_timer_key(request_id, user_id), None)

    try:
        _prompt_timers[_timer_key(request_id, user_id)] = asyncio.create_task(_expire())
    except RuntimeError:
        pass


async def _delete_prompt(bot, item: dict | None) -> None:
    if not isinstance(item, dict):
        return
    chat_id = int(item.get("prompt_chat_id") or 0)
    message_id = int(item.get("prompt_message_id") or 0)
    if not chat_id or not message_id:
        return
    await blank_and_delete(bot, chat_id, message_id)


async def start_vote_prompt(
    bot,
    request_id: str,
    user_id: int,
    username: str,
    name: str,
    vote: str,
    lang: str,
    target_chat_id: int,
    *,
    message_thread_id: int | None = None,
) -> bool:
    """Start the shared forum/inline vote flow and send its reason prompt."""
    previous = get_pending_vote(request_id, user_id)
    if previous:
        await _delete_prompt(bot, previous)

    entry = start_pending_vote(request_id, user_id, username, name, vote)
    if not entry:
        return False
    stored_pending = get_pending_vote(request_id, user_id) or {}

    thread_kwargs = {}
    if message_thread_id:
        thread_kwargs["message_thread_id"] = int(message_thread_id)
    try:
        prompt = await bot.send_message(
            int(target_chat_id),
            _prompt_text(entry, vote, lang, user_mention(user_id, username)),
            parse_mode=ParseMode.HTML,
            reply_markup=moderation_vote_reason_kb(
                request_id,
                user_id,
                anonymous=bool(stored_pending.get("anonymous")),
                has_templates=bool(_vote_templates(vote)),
                allow_no_reason=not require_vote_reason(),
                lang=lang,
            ),
            disable_web_page_preview=True,
            **thread_kwargs,
        )
    except Exception:
        clear_pending_vote(request_id, user_id)
        logger.exception(
            "vote prompt send failed request_id=%s user_id=%s chat_id=%s",
            request_id,
            user_id,
            target_chat_id,
        )
        return False

    updated = update_pending_vote(
        request_id,
        user_id,
        prompt_chat_id=int(prompt.chat.id),
        prompt_message_id=int(prompt.message_id),
    )
    if not updated:
        await _delete_prompt(bot, {
            "prompt_chat_id": int(prompt.chat.id),
            "prompt_message_id": int(prompt.message_id),
        })
        return False
    _schedule_prompt_expiry(bot, request_id, user_id)
    return True


async def _finish_vote(bot, request_id: str, user_id: int, reason: str, cb: CallbackQuery | None = None) -> bool:
    item = get_pending_vote(request_id, user_id)
    if not item:
        return False
    if not _pending_vote_is_active(item):
        _cancel_prompt_timer(request_id, int(user_id))
        clear_pending_vote(request_id, user_id)
        await _delete_prompt(bot, item)
        return False
    if not can_accept_vote(get_request_by_id(request_id)):
        _cancel_prompt_timer(request_id, int(user_id))
        clear_pending_vote(request_id, user_id)
        await _delete_prompt(bot, item)
        return False
    _cancel_prompt_timer(request_id, int(user_id))
    entry = commit_pending_vote(request_id, user_id, reason)
    if not entry:
        return False
    await _delete_prompt(bot, item)
    await refresh_forum_vote_keyboard(bot, entry)
    await refresh_admin_notify_messages(bot, entry)
    if cb is not None:
        await _refresh_inline_vote_message(bot, cb.inline_message_id, entry, request_id)
    await notify_superadmins_if_threshold(bot, entry)
    add_audit_event(
        "moderation.vote",
        actor_id=int(user_id),
        actor=str(item.get("username") or item.get("name") or ""),
        request_id=request_id,
        details={"vote": item.get("vote"), "anonymous": bool(item.get("anonymous")), "reason": reason[:200]},
    )
    return True


@router.callback_query(F.data.startswith("modvote:"))
async def on_moderation_vote(cb: CallbackQuery, state: FSMContext) -> None:
    parts = (cb.data or "").split(":", 2)
    if len(parts) != 3 or parts[1] not in {"yes", "no"}:
        await cb.answer()
        return

    vote = parts[1]
    request_token = parts[2]
    chat_id = cb.message.chat.id if cb.message and cb.message.chat else None
    user = cb.from_user
    if not user:
        await cb.answer()
        return
    entry = get_request_by_callback_token(request_token)
    if not entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return
    request_id = str(entry.get("id") or "")
    if not request_id:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return
    if not can_accept_vote(entry):
        await cb.answer(t("vote_expired", "ru"), show_alert=True)
        return
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    inline_public = bool(payload.get("moderation_inline_public")) if isinstance(payload, dict) else False
    if not inline_public and not can_vote_in_context(user.id if user else None, chat_id):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    lang = await get_language(cb, state)
    name = (user.full_name if user else "") or ""
    username = (user.username if user else "") or ""
    target_chat = chat_id or (user.id if user else None)
    if not target_chat:
        await cb.answer()
        return
    thread_id = getattr(cb.message, "message_thread_id", None) if cb.message else None
    ok = await start_vote_prompt(
        cb.bot,
        request_id,
        int(user.id),
        username,
        name,
        vote,
        lang,
        int(target_chat),
        message_thread_id=int(thread_id) if thread_id else None,
    )
    if not ok:
        await cb.answer(t("vote_expired", lang), show_alert=True)
        return
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("mvr:"))
async def on_vote_reason_action(cb: CallbackQuery, state: FSMContext) -> None:
    parts = (cb.data or "").split(":")
    if len(parts) < 4:
        await cb.answer()
        return
    action = parts[1]
    user = cb.from_user
    if not user:
        await cb.answer()
        return
    lang = await get_language(cb, state)
    try:
        owner_id = int(parts[2])
    except ValueError:
        await cb.answer()
        return

    if action == "t":
        if len(parts) < 5:
            await cb.answer()
            return
        tpl_idx_raw, request_token = parts[3], ":".join(parts[4:])
    else:
        tpl_idx_raw, request_token = "", ":".join(parts[3:])

    entry = get_request_by_callback_token(request_token)
    request_id = str(entry.get("id") or "") if entry else ""
    if not request_id:
        await _leave_vote_reason_state(state)
        await cb.answer(t("vote_expired", lang), show_alert=True)
        return

    if int(user.id) != owner_id:
        owner = get_pending_vote(request_id, owner_id) or {}
        owner_name = str(owner.get("username") or "").strip()
        owner_label = f"@{owner_name}" if owner_name else (str(owner.get("name") or "").strip() or str(owner_id))
        await cb.answer(t("vote_not_yours", lang, moderator=owner_label), show_alert=True)
        return

    item = get_pending_vote(request_id, int(user.id))
    if not item or not _pending_vote_is_active(item):
        await _leave_vote_reason_state(state)
        if item:
            _cancel_prompt_timer(request_id, int(user.id))
            clear_pending_vote(request_id, int(user.id))
            await _delete_prompt(cb.bot, item)
        await cb.answer(t("vote_timeout", lang), show_alert=True)
        if not item and cb.message:
            try:
                await blank_and_delete(cb.bot, cb.message.chat.id, cb.message.message_id)
            except Exception:
                pass
        return

    if action == "cancel":
        _cancel_prompt_timer(request_id, int(user.id))
        await _delete_prompt(cb.bot, item)
        clear_pending_vote(request_id, int(user.id))
        await _leave_vote_reason_state(state)
        await cb.answer(t("vote_cancelled", lang), show_alert=True)
        return

    if action == "anon":
        new_value = not bool(item.get("anonymous"))
        update_pending_vote(request_id, int(user.id), anonymous=new_value)
        try:
            await cb.message.edit_reply_markup(reply_markup=moderation_vote_reason_kb(
                request_id,
                owner_id,
                anonymous=new_value,
                has_templates=bool(_vote_templates(item.get("vote"))),
                allow_no_reason=not require_vote_reason(),
                lang=lang,
            ))
        except Exception:
            pass
        await cb.answer(t("vote_anon_on_alert" if new_value else "vote_anon_off_alert", lang))
        return

    if action == "tpl":
        await _leave_vote_reason_state(state)
        templates = _vote_templates(item.get("vote"))
        if not templates:
            await cb.answer(t("admin_rejtpl_empty", lang), show_alert=True)
            return
        try:
            await cb.message.edit_text(
                t("vote_reason_pick_tpl", lang, moderator=user_mention(user.id, user.username or "")),
                parse_mode=ParseMode.HTML,
                reply_markup=moderation_vote_template_kb(request_id, owner_id, templates, lang=lang),
            )
        except Exception:
            pass
        await cb.answer()
        return

    if action == "back":
        await _leave_vote_reason_state(state)
        entry = get_request_by_id(request_id)
        try:
            await cb.message.edit_text(
                _prompt_text(entry, str(item.get("vote") or "no"), lang,
                             user_mention(user.id, user.username or "")),
                parse_mode=ParseMode.HTML,
                reply_markup=moderation_vote_reason_kb(
                    request_id,
                    owner_id,
                    anonymous=bool(item.get("anonymous")),
                    has_templates=bool(_vote_templates(item.get("vote"))),
                    allow_no_reason=not require_vote_reason(),
                    lang=lang,
                ),
            )
        except Exception:
            pass
        await cb.answer()
        return

    if action == "t":
        templates = _vote_templates(item.get("vote"))
        try:
            idx = int(tpl_idx_raw)
        except ValueError:
            await cb.answer()
            return
        if idx < 0 or idx >= len(templates):
            await cb.answer(t("vote_expired", lang), show_alert=True)
            return
        ok = await _finish_vote(cb.bot, request_id, int(user.id), templates[idx], cb)
        await _leave_vote_reason_state(state)
        await cb.answer(t("vote_saved" if ok else "vote_expired", lang), show_alert=True)
        return

    if action == "none":
        if require_vote_reason():
            await cb.answer(t("vote_expired", lang), show_alert=True)
            return
        ok = await _finish_vote(cb.bot, request_id, int(user.id), "", cb)
        await _leave_vote_reason_state(state)
        await cb.answer(t("vote_saved" if ok else "vote_expired", lang), show_alert=True)
        return

    if action == "own":
        entry = get_request_by_id(request_id)
        try:
            await cb.message.edit_text(
                t(
                    "vote_reason_own_prompt", lang,
                    moderator=user_mention(user.id, user.username or ""),
                    vote=t(f"vote_value_{item.get('vote') or 'no'}", lang),
                    name=plain_html(request_title(entry)),
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=moderation_vote_reason_kb(
                    request_id,
                    owner_id,
                    anonymous=bool(item.get("anonymous")),
                    has_templates=bool(_vote_templates(item.get("vote"))),
                    allow_no_reason=not require_vote_reason(),
                    lang=lang,
                ),
            )
        except Exception:
            pass
        await state.set_state(UserFlow.entering_moderation_vote_reason)
        await state.update_data(
            moderation_vote_request_id=request_id,
            moderation_vote_inline_message_id=cb.inline_message_id or "",
            moderation_vote_prompt_message_id=cb.message.message_id if cb.message else 0,
        )
        await cb.answer(t("vote_reason_enter_own", lang), show_alert=True)
        return

    await cb.answer()


@router.message(UserFlow.entering_moderation_vote_reason)
async def on_moderation_vote_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    request_id = str(data.get("moderation_vote_request_id") or "")
    user = message.from_user
    if not request_id or not user:
        await _leave_vote_reason_state(state)
        return

    item = get_pending_vote(request_id, int(user.id))
    if not item or not _pending_vote_is_active(item):
        if item:
            _cancel_prompt_timer(request_id, int(user.id))
            clear_pending_vote(request_id, int(user.id))
            await _delete_prompt(message.bot, item)
        await _leave_vote_reason_state(state)
        return

    prompt_id = int(data.get("moderation_vote_prompt_message_id") or 0)
    reply_to = message.reply_to_message
    if prompt_id and message.chat.type != "private":
        if not reply_to or int(reply_to.message_id) != prompt_id:
            return

    text = telegram_html(message.html_text or message.text or "").strip()
    if not text:
        return

    ok = await _finish_vote(message.bot, request_id, int(user.id), text)
    await _leave_vote_reason_state(state)
    if ok:
        try:
            await message.delete()
        except Exception:
            pass
