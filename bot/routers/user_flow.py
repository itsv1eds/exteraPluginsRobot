import asyncio
import html
import logging
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote
from uuid import uuid4

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
)

from bot.context import get_language, get_lang
from bot.languages import normalize_language
from bot.callback_tokens import decode_slug
from core.plugin_formats import plugin_extension, plugin_file_limit
from core.categories import CATEGORY_KEYS, MAX_CATEGORIES, categories_for, category_fields
from bot import limits
from bot.formatting import plain_html, strip_blockquote_tags, telegram_html, user_mention
from bot.helpers import ack, answer, extract_html_text, spawn_background, try_react_pray
from bot.menu_owner import MenuOwnerMiddleware, remember_menu_owner
from bot.keyboards import (
    quiz_kb,
    quiz_start_kb,
    banned_appeal_kb,
    cancel_confirm_kb,
    cancel_kb,
    comment_skip_kb,
    categories_kb,
    description_lang_kb,
    draft_category_kb,
    draft_edit_kb,
    draft_lang_kb,
    language_kb,
    main_menu_kb,
    submit_type_kb,
    user_plugins_kb,
)
from core.storage import QUIZ_QUESTIONS_PER_RUN, build_quiz
from bot.cache import get_admins, get_admins_plugins, get_categories
from bot.services.approval import approval_status_text, can_edit_request
from bot.services.request_context import author_context, forum_request_text_and_file
from bot.services.submission import (
    PluginData,
    build_submission_payload,
    process_plugin_file,
    prepare_submission_categories,
)
from bot.services.publish import build_channel_post
from bot.services.admin_notifications import send_review_notifications
from bot.services.moderation import can_accept_vote, can_vote_in_context, delete_forum_request_message, send_request_to_forum
from bot.services.versioning import get_min_supported_version, is_valid_version, meets_min_supported, normalize_version
from bot.services.validation import (
    check_duplicate_pending,
    missing_draft_fields,
    submission_fingerprint,
    validate_new_submission,
    validate_update_submission,
    validate_request_replacement,
)
from bot.states import UserFlow
from bot.texts import TEXTS, t
from core.catalog import find_plugin_by_slug, find_plugin_by_deeplink_token, find_user_plugins
from bot.routers.catalog_flow import BOT_USERNAME
from bot.keyboards import catalog_main_kb, admin_menu_kb
from bot.states import AdminFlow
from core.request_store import (
    acquire_request_lock,
    release_request_lock,
    request_callback_token,
    add_draft_request,
    add_request,
    delete_request,
    discard_user_drafts,
    get_user_requests,
    get_request_by_deeplink_token,
    get_request_by_id,
    get_request_by_callback_token,
    promote_draft_request,
    update_request_payload,
    update_request_status,
)
from core.user_store import get_user_language, is_user_banned, set_user_language
from core.user_store import set_broadcast_enabled, set_paid_broadcast_disable

router = Router(name="user-flow")
router.callback_query.middleware(MenuOwnerMiddleware())
logger = logging.getLogger(__name__)
TZ_UTC_PLUS_5 = timezone(timedelta(hours=5))
_START_COOLDOWN_SECONDS = 3.0
_DRAFT_NAME_LIMIT = 64
_recent_starts: dict[int, float] = {}
_COMMENT_MEDIA_GROUP_SETTLE_SECONDS = 0.8
_comment_media_group_buffers: dict[tuple[int, int, str], dict[str, Any]] = {}
_WITHDRAWABLE_REQUEST_STATUSES = {"pending", "rework"}


def _is_broadcast_disable_payment(payload: str | None, currency: str | None, amount: int | None) -> bool:
    from bot.routers.catalog_flow import (
        BROADCAST_DISABLE_CURRENCY,
        BROADCAST_DISABLE_PAYLOAD,
        BROADCAST_DISABLE_PRICE,
    )

    return payload == BROADCAST_DISABLE_PAYLOAD and currency == BROADCAST_DISABLE_CURRENCY \
        and amount == BROADCAST_DISABLE_PRICE


@router.pre_checkout_query()
async def on_pre_checkout_query(pre_checkout_query: PreCheckoutQuery) -> None:
    query = pre_checkout_query
    if _is_broadcast_disable_payment(query.invoice_payload, query.currency, query.total_amount):
        await query.answer(ok=True)
        return
    logger.warning("event=payment.pre_checkout_rejected user_id=%s payload=%s", query.from_user.id, query.invoice_payload)
    await query.answer(ok=False, error_message=t("payment_unknown", get_lang(query.from_user.id)))


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    payment = message.successful_payment
    if not payment:
        return

    if not _is_broadcast_disable_payment(payment.invoice_payload, payment.currency, payment.total_amount):
        logger.warning("event=payment.unexpected payload=%s", payment.invoice_payload)
        return

    if not message.from_user:
        return

    user_id = message.from_user.id
    set_paid_broadcast_disable(user_id, True)
    set_broadcast_enabled(user_id, False)

    spawn_background(notify_admins_broadcast_paid_disable(message.bot, message.from_user, payment))

    lang = get_lang(user_id)
    await message.answer(t("broadcast_payment_thanks", lang), parse_mode=ParseMode.HTML)


async def notify_admins_broadcast_paid_disable(bot, user, payment: SuccessfulPayment) -> None:
    try:
        user_id = getattr(user, "id", 0) or 0
        username = (getattr(user, "username", None) or "").strip()
        full_name = (getattr(user, "full_name", None) or getattr(user, "first_name", None) or "").strip() or "—"

        user_link = user_mention(user_id, username)
        amount = getattr(payment, "total_amount", None)
        currency = getattr(payment, "currency", None) or ""
        amount_str = f"{amount} {currency}" if amount is not None else currency

        targets = set(get_admins())
        delivered = 0
        for admin_id in targets:
            admin_lang = get_lang(admin_id)
            text = t(
                "admin_broadcast_paid_disable",
                admin_lang,
                user=user_link,
                name=html.escape(full_name),
                amount=html.escape(amount_str),
            )
            try:
                await bot.send_message(
                    admin_id,
                    text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                delivered += 1
            except Exception:
                logger.warning("event=broadcast_paid_disable.notify_admins.failed admin_id=%s", admin_id, exc_info=True)
                continue

        logger.info(
            "event=broadcast_paid_disable.notify_admins.done user_id=%s delivered=%s",
            user_id,
            delivered,
        )
    except Exception:
        logger.warning("event=broadcast_paid_disable.notify_admins.crashed", exc_info=True)


def _submission_type(payload: Dict[str, Any]) -> str:
    icon = payload.get("icon", {})
    return payload.get("submission_type") or ("icon" if icon else "plugin")


def _submission_name(payload: Dict[str, Any], submission_type: str) -> str:
    if submission_type == "icon":
        return (payload.get("icon", {}) or {}).get("name", "") or "unknown"
    return (payload.get("plugin", {}) or {}).get("name", "") or "unknown"


async def _ensure_not_banned(target: Message | CallbackQuery, state: FSMContext) -> bool:
    user_id = None
    if isinstance(target, CallbackQuery):
        user_id = target.from_user.id if target.from_user else None
    else:
        user_id = target.from_user.id if target.from_user else None

    if not user_id:
        return True

    if is_user_banned(user_id):
        lang = await get_language(target, state)
        if isinstance(target, CallbackQuery):
            await target.answer(t("user_banned_short", lang), show_alert=True)
        else:
            await target.answer(
                t("user_banned", lang), parse_mode=ParseMode.HTML,
                reply_markup=_appeal_kb_for(user_id, lang),
            )
        return False

    return True


async def _open_category_picker(target: Message | CallbackQuery, state: FSMContext, prefix: str, lang: str) -> None:
    data = await state.get_data()
    selected = categories_for(data)
    picker = {
        "prefix": prefix,
        "keys": selected,
        "request_id": data.get("pending_request_id"),
        "state": await state.get_state(),
    }
    await state.update_data(category_selection=picker, edit_field="category")
    keyboard = categories_kb(get_categories(), lang, selected) if prefix == "submit" else draft_category_kb(prefix, get_categories(), lang, selected)
    sent = await answer(target, t("choose_category", lang), keyboard, None)
    picker["message_id"] = sent.message_id if sent else None
    picker["chat_id"] = sent.chat.id if sent else None
    await state.update_data(category_selection=picker)


async def _category_picker_valid(cb: CallbackQuery, state: FSMContext, prefix: str, lang: str) -> bool:
    data = await state.get_data()
    picker = data.get("category_selection") or {}
    valid = (
        picker.get("prefix") == prefix
        and data.get("edit_field") == "category"
        and picker.get("state") == await state.get_state()
        and cb.message
        and picker.get("message_id") == cb.message.message_id
        and picker.get("chat_id") == cb.message.chat.id
        and picker.get("request_id") == data.get("pending_request_id")
    )
    if valid and prefix in {"pend", "pendupd"}:
        entry = get_request_by_id(data.get("pending_request_id"))
        valid = bool(
            entry
            and can_edit_request(entry)
            and entry.get("type") == ("update" if prefix == "pendupd" else "new")
            and entry.get("payload", {}).get("user_id") == cb.from_user.id
        )
    if not valid:
        await cb.answer(t("not_found", lang), show_alert=True)
    return bool(valid)


async def _choose_categories(cb: CallbackQuery, state: FSMContext, prefix: str, lang: str) -> bool:
    if not await _ensure_not_banned(cb, state) or not await _category_picker_valid(cb, state, prefix, lang):
        return False
    action = (cb.data or "").removeprefix(f"{prefix}:cat:")
    picker = dict((await state.get_data())["category_selection"])
    selected = categories_for(picker.get("keys"))
    if action == "done":
        if not selected:
            await cb.answer(t("categories_required", lang), show_alert=True)
            return False
        data = await state.get_data()
        plugin = dict(data.get("plugin") or {})
        plugin["categories"] = selected
        await state.update_data(**category_fields(selected), plugin=plugin, category_selection=None, edit_field=None)
        return True
    if action not in CATEGORY_KEYS:
        await cb.answer(t("not_found", lang), show_alert=True)
        return False
    if action in selected:
        selected.remove(action)
    elif len(selected) >= MAX_CATEGORIES:
        await cb.answer(t("categories_limit", lang), show_alert=True)
        return False
    else:
        selected.append(action)
    picker["keys"] = selected
    await state.update_data(category_selection=picker)
    keyboard = categories_kb(get_categories(), lang, selected) if prefix == "submit" else draft_category_kb(prefix, get_categories(), lang, selected)
    await cb.message.edit_reply_markup(reply_markup=keyboard)
    await ack(cb)
    return False


def _build_draft_entry(data: Dict[str, Any]) -> Dict[str, Any]:
    plugin = data.get("plugin", {})
    payload = {
        "plugin": plugin,
        "description_ru": data.get("description_ru"),
        "description_en": data.get("description_en"),
        "usage_ru": data.get("usage_ru"),
        "usage_en": data.get("usage_en"),
        **category_fields(data),
        "publish_not_before": data.get("publish_not_before"),
    }
    return {"payload": payload}


def _parse_user_publish_time(value: str) -> datetime:
    return datetime.strptime(value, "%d.%m.%Y %H:%M").replace(tzinfo=TZ_UTC_PLUS_5)


def _format_publish_not_before(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value).astimezone(TZ_UTC_PLUS_5)
    except Exception:
        return ""
    return dt.strftime("%d.%m.%Y %H:%M")


def _render_draft_text(data: Dict[str, Any]) -> str:
    plugin = data.get("plugin", {})
    fallback_desc = plugin.get("description", "")
    payload = {
        **_build_draft_entry(data).get("payload", {}),
        "description_ru": data.get("description_ru") or fallback_desc,
        "description_en": data.get("description_en") or fallback_desc,
    }
    text = build_channel_post({"payload": payload})
    context = author_context(data, data.get("lang") or "ru")
    if context:
        text += "\n\n" + context
    not_before = _format_publish_not_before(data.get("publish_not_before"))
    if not_before:
        text += "\n\n" + t("publish_not_before_label", data.get("lang") or "ru", datetime=not_before)
    return text


def _render_update_text(data: Dict[str, Any]) -> str:
    plugin = data.get("plugin", {})
    payload = {
        "plugin": plugin,
        "description_ru": data.get("description_ru"),
        "description_en": data.get("description_en"),
        "usage_ru": data.get("usage_ru"),
        "usage_en": data.get("usage_en"),
        **category_fields(data),
    }
    text = build_channel_post({"payload": payload})
    context = author_context(data, data.get("lang") or "ru")
    return text + ("\n\n" + context if context else "")


def _welcome_text(user, lang: str) -> str:
    username = str(getattr(user, "username", None) or "").strip()
    name = f"@{username}" if username else str(getattr(user, "full_name", None) or getattr(user, "first_name", None) or "—").strip() or "—"
    return t("welcome", lang, bot=BOT_USERNAME, bot_name=BOT_USERNAME, user_name=plain_html(name))


async def _render_home(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await state.update_data(lang=lang)
    await state.set_state(UserFlow.idle)
    msg = await answer(
        cb,
        _welcome_text(cb.from_user, lang),
        main_menu_kb(lang),
        "welcome",
    )
    if cb.message:
        spawn_background(try_react_pray(cb.message))
    elif msg:
        spawn_background(try_react_pray(msg))
    if msg:
        await remember_menu_owner(cb, state, msg)


async def _render_profile_message(message: Message, state: FSMContext, lang: str) -> None:
    if not message.from_user:
        return
    from bot.routers.catalog_flow import build_profile

    text, keyboard = await build_profile(message.from_user, state, lang)
    sent = await answer(message, text, keyboard, "profile")
    if sent:
        await remember_menu_owner(message, state, sent)


async def _route_start_payload_message(message: Message, state: FSMContext, lang: str, payload: str) -> bool:
    raw_value = (payload or "").strip()
    value = raw_value.lower()
    if not raw_value:
        return False

    if raw_value.startswith(("modvote_yes_", "modvote_no_")):
        vote = "yes" if raw_value.startswith("modvote_yes_") else "no"
        request_token = unquote(raw_value.split("_", 2)[2])
        entry = get_request_by_deeplink_token(request_token)
        request_payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
        inline_public = bool(request_payload.get("moderation_inline_public")) if isinstance(request_payload, dict) else False
        user = message.from_user
        if not entry or not user:
            await message.answer(t("not_found", lang), parse_mode=ParseMode.HTML)
            return True
        if not can_accept_vote(entry):
            await message.answer(t("vote_expired", lang), parse_mode=ParseMode.HTML)
            return True
        moderator = can_vote_in_context(user.id, message.chat.id)
        if not inline_public and not moderator:
            await message.answer(t("admin_denied", lang), parse_mode=ParseMode.HTML)
            return True
        request_id = str(entry.get("id") or "")
        from bot.routers.moderation_flow import start_vote_prompt

        ok = await start_vote_prompt(
            message.bot,
            request_id,
            int(user.id),
            user.username or "",
            user.full_name or "",
            vote,
            lang,
            int(message.chat.id),
            public=not moderator,
        )
        if not ok:
            await message.answer(t("vote_expired", lang), parse_mode=ParseMode.HTML)
        return True

    if value == "catalog":
        await state.set_state(UserFlow.idle)
        sent = await answer(message, t("catalog_title", lang), catalog_main_kb(get_categories(), lang), "catalog")
        if sent:
            await remember_menu_owner(message, state, sent)
        return True

    if value == "submit":
        user = message.from_user
        include_update = False
        if user:
            try:
                include_update = bool(find_user_plugins(user.id, user.username or ""))
            except Exception:
                include_update = False
        await state.set_state(UserFlow.choosing_submission_type)
        sent = await answer(message, _submit_type_text(lang), submit_type_kb(lang, include_update=include_update), "suggestion")
        if sent:
            await remember_menu_owner(message, state, sent)
        return True

    if value == "profile":
        await state.set_state(UserFlow.idle)
        await _render_profile_message(message, state, lang)
        return True

    if value == "notifications":
        await state.set_state(UserFlow.idle)
        try:
            from bot.routers.catalog_flow import _show_subscriptions

            await _show_subscriptions(message, state, page=0)
        except Exception:
            await answer(message, t("subscriptions_title", lang) + "\n\n" + t("subscriptions_hint", lang), None, "profile")
        return True

    if value in {"broadcast", "рассылка"}:
        await state.set_state(UserFlow.idle)
        try:
            from bot.routers.catalog_flow import _show_broadcast_settings

            await _show_broadcast_settings(message, state)
        except Exception:
            await answer(message, t("broadcast_title", lang), None, "profile")
        return True

    if value == "joinly":
        await state.set_state(UserFlow.idle)
        try:
            from bot.routers.catalog_flow import _render_profile_joinly

            await _render_profile_joinly(message, state)
        except Exception:
            await answer(message, t("joinly_deeplink_intro", lang), main_menu_kb(lang), "profile")
        return True

    if value == "poster":
        try:
            from bot.routers.poster_flow import render_home

            await render_home(message, state)
        except Exception:
            logger.exception("event=start.poster_deeplink_failed")
        return True

    if value == "admin":
        user_id = message.from_user.id if message.from_user else None
        if user_id and user_id in get_admins():
            await state.set_state(AdminFlow.menu)
            from bot.routers.admin_flow import _admin_menu_role

            sent = await answer(message, t("admin_title", lang), admin_menu_kb(_admin_menu_role(message), lang=lang), "profile")
            if sent:
                await remember_menu_owner(message, state, sent)
        else:
            sent = await answer(message, _welcome_text(message.from_user, lang), main_menu_kb(lang), "welcome")
            if sent:
                await remember_menu_owner(message, state, sent)
        return True

    return False


def _submit_type_text(lang: str) -> str:
    return f"<b>{t('choose_type', lang)}</b>\n\n{t('rules_before_submit', lang)}"


async def _render_submit_type(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
    user = cb.from_user
    include_update = False
    if user:
        try:
            include_update = bool(find_user_plugins(user.id, user.username or ""))
        except Exception:
            include_update = False
    await state.set_state(UserFlow.choosing_submission_type)
    await answer(cb, _submit_type_text(lang), submit_type_kb(lang, include_update=include_update), "suggestion")


async def _sync_submission_draft(
    state: FSMContext,
    user_id: int,
    username: str,
    submission_type: str = "plugin",
) -> None:
    data = await state.get_data()
    draft_id = data.get("draft_request_id")

    plugin = data.get("plugin", {})
    if not plugin:
        return
    payload = {
        "user_id": user_id,
        "username": username,
        "plugin": plugin,
        "description_ru": data.get("description_ru"),
        "description_en": data.get("description_en"),
        "usage_ru": data.get("usage_ru"),
        "usage_en": data.get("usage_en"),
        **category_fields(data),
        "publish_not_before": data.get("publish_not_before"),
        "submission_type": "plugin",
    }

    if draft_id:
        update_request_payload(draft_id, payload)
    else:
        entry = add_draft_request(payload, request_type="new")
        await state.update_data(draft_request_id=entry.get("id"))


async def _stage_pending_changes(state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("plugin"):
        return
    fields = ["plugin", "description_ru", "description_en", "usage_ru", "usage_en"]
    is_update = data.get("draft_prefix") == "pendupd"
    fields += ["changelog", "update_slug", "old_plugin"] if is_update else ["publish_not_before"]
    changes = {
        **{field: data.get(field) for field in fields},
        **category_fields(data),
        "submission_type": "update" if is_update else "plugin",
    }
    await state.update_data(pending_request_changes=deepcopy(changes))


def _editable_own_request(request_id: str, user_id: int | None) -> dict | None:
    entry = get_request_by_id(request_id) if user_id else None
    if can_edit_request(entry) and entry["payload"].get("user_id") == user_id:
        return entry
    return None


def _request_edit_fingerprint(payload: dict) -> str:
    normalized = {**payload, **category_fields(payload)}
    return ":".join((
        submission_fingerprint(normalized), str((payload.get("plugin") or {}).get("file_path") or ""),
        str(payload.get("publish_not_before") or ""),
    ))


async def _notify_admins_request_updated(bot, entry: Dict[str, Any]) -> bool:
    previous = deepcopy(entry)
    request_id = str(entry["id"])
    try:
        text, file_path = forum_request_text_and_file(entry)
        await send_request_to_forum(bot, entry, text, file_path)
    except Exception:
        logger.warning("event=submission.notify_forum_update.failed request_id=%s", request_id, exc_info=True)
        return False
    from core.storage import flush_documents

    await flush_documents("requests")
    spawn_background(delete_forum_request_message(bot, previous))
    return True


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is not None:
        now = asyncio.get_running_loop().time()
        if len(_recent_starts) >= 10000:
            cutoff = now - _START_COOLDOWN_SECONDS
            for stale_user_id, started_at in list(_recent_starts.items()):
                if started_at < cutoff:
                    _recent_starts.pop(stale_user_id, None)
        previous = _recent_starts.get(user_id, 0.0)
        if now - previous < _START_COOLDOWN_SECONDS:
            logger.info("event=start.duplicate_ignored user_id=%s", user_id)
            return
        _recent_starts[user_id] = now
    await state.clear()
    await remember_menu_owner(message, state)

    if user_id is not None:
        try:
            discard_user_drafts(user_id)
        except Exception:
            logger.exception("event=cmd_start.discard_drafts_failed user_id=%s", user_id)

    payload = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            payload = (parts[1] or "").strip()

    if is_user_banned(user_id):
        lang = get_lang(user_id)
        await answer(message, t("user_banned", lang), _appeal_kb_for(user_id, lang))
        return

    if payload:
        payload = payload.removeprefix("plugin_")
        await state.update_data(start_payload=payload)

    if get_user_language(user_id):
        lang = get_lang(user_id)
        await state.update_data(lang=lang)
        await state.set_state(UserFlow.idle)

        if payload:
            if await _route_start_payload_message(message, state, lang, payload):
                return
            plugin = find_plugin_by_slug(payload) or find_plugin_by_deeplink_token(payload)
            if plugin:
                plugin_slug = plugin.get("slug") or payload
                try:
                    from bot.services.analytics import record_plugin_open
                    record_plugin_open(plugin_slug)
                except Exception:
                    logger.exception("event=start.record_open_failed slug=%s", plugin_slug)
                from bot.routers.catalog_flow import _plugin_detail_view

                text, keyboard, _ = _plugin_detail_view(message.from_user, plugin, lang, "catalog", "catalog")
                sent = await answer(message, text, keyboard, "catalog")
                if sent:
                    await remember_menu_owner(message, state, sent)
                return

        sent = await answer(
            message,
            _welcome_text(message.from_user, lang),
            main_menu_kb(lang),
            "welcome",
        )
        if sent:
            spawn_background(try_react_pray(sent))
            await remember_menu_owner(message, state, sent)
        return

    lang = get_lang(user_id)
    await state.set_state(UserFlow.choosing_language)
    sent = await answer(message, t("language_prompt", lang), language_kb())
    if sent:
        await remember_menu_owner(message, state, sent)


def _has_pending_appeal(user_id: int) -> bool:
    for req in get_user_requests(user_id):
        if req.get("type") == "unban_appeal" and req.get("status") == "pending":
            return True
    return False


def _is_permanent_ban(user_id: int) -> bool:
    from core.user_store import get_user
    return bool(get_user(user_id).get("ban_permanent"))


def _appeal_kb_for(user_id: int, lang: str):
    if _is_permanent_ban(user_id) or _has_pending_appeal(user_id):
        return None
    return banned_appeal_kb(lang)


@router.callback_query(F.data == "appeal:start")
async def on_appeal_start(cb: CallbackQuery, state: FSMContext) -> None:
    user = cb.from_user
    if not user:
        return
    lang = get_lang(user.id)
    if not is_user_banned(user.id):
        await cb.answer()
        return
    if _is_permanent_ban(user.id):
        await cb.answer(t("appeal_permanently_denied", lang), show_alert=True)
        return
    if _has_pending_appeal(user.id):
        await cb.answer(t("appeal_already_pending", lang), show_alert=True)
        return
    await state.set_state(UserFlow.entering_appeal)
    await answer(cb, t("appeal_prompt", lang), None, "appeal")
    await ack(cb)


@router.message(UserFlow.entering_appeal)
async def on_appeal_text(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if not user:
        return
    lang = get_lang(user.id)
    if not is_user_banned(user.id):
        await state.set_state(UserFlow.idle)
        return
    if _is_permanent_ban(user.id):
        await state.set_state(UserFlow.idle)
        await message.answer(t("appeal_permanently_denied", lang))
        return
    if _has_pending_appeal(user.id):
        await state.set_state(UserFlow.idle)
        await message.answer(t("appeal_already_pending", lang))
        return

    text = telegram_html(message.html_text or message.text or "").strip()
    if not text:
        await message.answer(t("need_text", lang), disable_web_page_preview=True)
        return

    payload = {
        "user_id": user.id,
        "username": user.username or "",
        "appeal_text": text,
    }
    entry = add_request(payload, request_type="unban_appeal")

    sender = user_mention(user.id, user.username)
    forum_text = t(
        "appeal_forum_text",
        "ru",
        user=sender,
        text=strip_blockquote_tags(text),
    )
    try:
        await send_request_to_forum(message.bot, entry, forum_text)
    except Exception:
        logger.exception("event=appeal.forum_send_failed user_id=%s", user.id)

    await state.set_state(UserFlow.idle)
    await message.answer(t("appeal_sent", lang), parse_mode=ParseMode.HTML)


@router.message(Command("lang"))
async def cmd_lang(message: Message, state: FSMContext) -> None:
    if not message.from_user or is_user_banned(message.from_user.id):
        return
    lang = get_lang(message.from_user.id)
    await state.set_state(UserFlow.choosing_language)
    await answer(message, t("language_prompt", lang), language_kb())


@router.callback_query(F.data.startswith("lang:"))
async def on_lang(cb: CallbackQuery, state: FSMContext) -> None:
    lang = normalize_language(cb.data.split(":")[1], None)
    if not lang:
        await cb.answer()
        return

    if cb.from_user:
        set_user_language(cb.from_user.id, lang)
    await state.update_data(lang=lang)
    await state.set_state(UserFlow.idle)
    data = await state.get_data()
    start_payload = (data.get("start_payload") or "").strip()
    if start_payload:
        if cb.message and await _route_start_payload_message(cb.message, state, lang, start_payload):
            await cb.answer(t("language_saved", lang))
            return
        plugin = find_plugin_by_slug(start_payload) or find_plugin_by_deeplink_token(start_payload)
        if plugin:
            from bot.routers.catalog_flow import _plugin_detail_view

            text, keyboard, _ = _plugin_detail_view(cb.from_user, plugin, lang, "catalog", "catalog")
            await answer(cb, text, keyboard, "catalog")
            await cb.answer(t("language_saved", lang))
            return

    msg = await answer(
        cb,
        _welcome_text(cb.from_user, lang),
        main_menu_kb(lang),
        "welcome",
    )
    if cb.message:
        spawn_background(try_react_pray(cb.message))
    elif msg:
        spawn_background(try_react_pray(msg))
    await cb.answer(t("language_saved", lang))


@router.callback_query(F.data == "home")
async def on_home(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    await state.set_state(UserFlow.idle)
    msg = await answer(
        cb,
        _welcome_text(cb.from_user, lang),
        main_menu_kb(lang),
        "welcome",
    )
    if cb.message:
        spawn_background(try_react_pray(cb.message))
    elif msg:
        spawn_background(try_react_pray(msg))
    await ack(cb)


_EDITOR_PREFIXES = {"draft", "upd", "pend", "pendupd"}
_EDITOR_INPUT_STATES = {"editing_draft_field", "entering_publish_not_before", "uploading_pending_file"}
_EDITOR_ROOT_STATES = {"confirming_submission", "confirming_update"}
_WIZARD_STATES = {
    "uploading_file",
    "entering_min_version",
    "uploading_update_file",
    "choosing_description_language",
    "editing_description_translation",
    "editing_usage_ru",
    "editing_usage_en",
    "choosing_category",
    "confirming_submission",
    "confirming_update",
    "entering_changelog",
    "choosing_plugin_to_update",
}


def _returns_to_editor(state_name: str, data: Dict[str, Any]) -> bool:
    prefix = data.get("draft_prefix")
    if prefix not in _EDITOR_PREFIXES or not data.get("plugin"):
        return False
    if state_name in _EDITOR_INPUT_STATES:
        return True
    if state_name == "entering_admin_comment":
        return prefix in {"draft", "upd"} and data.get("pending_request_type") in {"new", "update"}
    if state_name == "entering_update_note":
        return prefix in _PENDING_PREFIXES and bool(data.get("pending_request_id")) \
            and data.get("pending_request_id") == data.get("update_note_request_id")
    return False


@router.callback_query(F.data.in_({"cancel", "cancel:confirm"}))
async def on_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    data = await state.get_data()
    picker = data.get("category_selection")
    if picker and not await _category_picker_valid(cb, state, picker.get("prefix"), lang):
        return
    state_name = (await state.get_state() or "").split(":")[-1]
    prefix = data.get("draft_prefix")

    if _returns_to_editor(state_name, data):
        if state_name == "entering_admin_comment":
            await _discard_pending_comment_media_groups(cb)
        await state.update_data(category_selection=None, edit_field=None, edit_lang=None, update_note_request_id=None)
        await _show_editor(cb, state, prefix, lang)
        await ack(cb, t("input_cancelled", lang))
        return

    if cb.data == "cancel" and state_name in _EDITOR_ROOT_STATES and prefix in {"draft", "upd"} and data.get("plugin"):
        await answer(cb, t("cancel_submission_confirm", lang), cancel_confirm_kb(prefix, lang), None)
        await ack(cb)
        return

    await state.update_data(category_selection=None, edit_field=None)
    draft_id = data.get("draft_request_id")
    if draft_id:
        delete_request(draft_id)

    if state_name == "uploading_update_file" and data.get("update_from_profile") and data.get("update_slug"):
        from bot.routers.catalog_flow import _show_plugin_detail

        await state.set_state(UserFlow.idle)
        if await _show_plugin_detail(cb, state, data["update_slug"], "my"):
            await ack(cb, t("submission_cancelled", lang))
        return

    if state_name in _WIZARD_STATES and prefix not in _PENDING_PREFIXES:
        await _render_submit_type(cb, state, lang)
    else:
        await _render_home(cb, state, lang)

    await ack(cb, t("submission_cancelled", lang))


@router.callback_query(F.data == "submit")
async def on_submit(cb: CallbackQuery, state: FSMContext) -> None:
    if is_user_banned(cb.from_user.id):
        lang = await get_language(cb, state)
        await cb.answer(t("user_banned_short", lang), show_alert=True)
        return
    lang = await get_language(cb, state)
    await _render_submit_type(cb, state, lang)
    await ack(cb)


async def _start_quiz(target, state: FSMContext, lang: str) -> None:
    quiz = build_quiz(lang, QUIZ_QUESTIONS_PER_RUN)
    await state.set_state(UserFlow.passing_quiz)
    await state.update_data(quiz=quiz, quiz_index=0)
    await _render_quiz_question(target, state, lang)


async def _render_quiz_question(target, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    quiz = data.get("quiz") or []
    idx = int(data.get("quiz_index") or 0)
    if idx >= len(quiz):
        return
    q = quiz[idx]
    options = "\n".join(f"<b>{number}.</b> {plain_html(option)}" for number, option in enumerate(q["options"], 1))
    await answer(
        target,
        t("quiz_question", lang, current=idx + 1, total=len(quiz), text=f"{plain_html(q['text'])}\n\n{options}"),
        quiz_kb(q["options"], idx, lang),
        "new",
    )


@router.callback_query(UserFlow.passing_quiz, F.data.regexp(r"^quiz:a:\d+:\d+$"))
async def on_quiz_answer(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    _, _, idx_raw, choice_raw = cb.data.split(":")
    data = await state.get_data()
    quiz = data.get("quiz") or []
    idx = int(data.get("quiz_index") or 0)
    if int(idx_raw) != idx or idx >= len(quiz):
        await cb.answer()
        return

    if int(choice_raw) != int(quiz[idx]["correct"]):
        await cb.answer(t("quiz_wrong", lang), show_alert=True)
        await _start_quiz(cb, state, lang)
        return

    idx += 1
    await state.update_data(quiz_index=idx)
    if idx < len(quiz):
        await _render_quiz_question(cb, state, lang)
        await ack(cb)
        return

    await state.update_data(quiz=None, quiz_index=0)
    if cb.from_user:
        discard_user_drafts(cb.from_user.id)
    await state.update_data(draft_request_id=None)
    await state.set_state(UserFlow.uploading_file)
    await answer(cb, t("upload_plugin", lang), cancel_kb(lang), "new")
    await ack(cb, t("quiz_passed", lang))


@router.callback_query(UserFlow.choosing_submission_type, F.data == "submit:plugin")
async def on_submit_plugin(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    await state.set_state(UserFlow.passing_quiz)
    await answer(cb, t("quiz_intro", lang, count=QUIZ_QUESTIONS_PER_RUN), quiz_start_kb(lang), "new")
    await ack(cb)


@router.callback_query(UserFlow.passing_quiz, F.data == "quiz:start")
async def on_quiz_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    await _start_quiz(cb, state, await get_language(cb, state))
    await ack(cb)


@router.callback_query(UserFlow.choosing_submission_type, F.data == "submit:update")
async def on_submit_update(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    user = cb.from_user
    user_plugins = find_user_plugins(user.id, user.username or "")

    if not user_plugins:
        await cb.answer(t("no_plugins_to_update", lang), show_alert=True)
        return

    plugins_list = [(p.get("ru", {}).get("name") or p.get("slug"), p.get("slug")) for p in user_plugins]
    await state.set_state(UserFlow.choosing_plugin_to_update)
    await answer(cb, t("choose_plugin_to_update", lang), user_plugins_kb(plugins_list, lang), "update")
    await ack(cb)


@router.callback_query(UserFlow.choosing_plugin_to_update, F.data.startswith("upd:"))
async def on_choose_plugin_update(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    slug = decode_slug(cb.data.split(":")[1])
    plugin = find_plugin_by_slug(slug)

    if not plugin:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    current_version = plugin.get("ru", {}).get("version") or "?"
    await state.update_data(update_slug=slug, old_plugin=plugin, old_version=current_version, **category_fields(plugin), category_selection=None,
                            update_from_profile=False)
    await state.set_state(UserFlow.uploading_update_file)
    await answer(cb, t("upload_update_file", lang, version=current_version), cancel_kb(lang), "update")
    await ack(cb)


async def _read_plugin_document(message: Message, lang: str):
    document = message.document
    if document and document.file_size and document.file_size > plugin_file_limit(document.file_name or ""):
        await message.answer(t("file_too_large", lang))
        return None
    try:
        return await process_plugin_file(message.bot, document)
    except ValueError as e:
        key, _, details = str(e).partition(":")
        if key == "parse_error" and details:
            await message.answer(t("parse_error", lang, error=details))
        else:
            await message.answer(t(key, lang) if key in TEXTS else t("parse_error", lang, error=str(e)))
        return None


async def _ask_request_comment(cb: CallbackQuery, state: FSMContext, payload: dict, request_type: str, reply_key: str,
                               prompt_key: str, scene: str, *, comment_required: bool, media_required: bool) -> None:
    lang = await get_language(cb, state)
    await state.update_data(pending_payload=payload, pending_request_type=request_type, pending_reply_key=reply_key)
    await _discard_pending_comment_media_groups(cb)
    await state.set_state(UserFlow.entering_admin_comment)
    await state.update_data(pending_comment=None, pending_comment_media=[], comment_required=comment_required, media_required=media_required)
    await answer(cb, t(prompt_key, lang, max=COMMENT_MEDIA_LIMIT),
                 comment_skip_kb(lang, required=True, back=request_type in {"new", "update"}), scene)


_PENDING_PREFIXES = {"pend", "pendupd"}
_UPDATE_PREFIXES = {"upd", "pendupd"}


async def _save_editor_change(state: FSMContext, user, prefix: str) -> None:
    if prefix == "draft":
        await _sync_submission_draft(state, user.id, user.username or "", "plugin")
    elif prefix in _PENDING_PREFIXES and (await state.get_data()).get("pending_request_id"):
        await _stage_pending_changes(state)


async def _show_editor(target: CallbackQuery | Message, state: FSMContext, prefix: str, lang: str) -> None:
    is_update = prefix in _UPDATE_PREFIXES
    await state.set_state(UserFlow.confirming_update if is_update else UserFlow.confirming_submission)
    data = await state.get_data()
    text = _render_update_text(data) if is_update else _render_draft_text(data)
    if prefix in _PENDING_PREFIXES:
        entry = get_request_by_id(str(data.get("pending_request_id") or "")) or {}
        status = approval_status_text(entry, lang) if entry else ""
        if status:
            text = f"{status}\n\n{text}"
        kb = draft_edit_kb(
            prefix, t("btn_save_changes", lang),
            include_checked_on=False,
            include_delete=entry.get("status") in _WITHDRAWABLE_REQUEST_STATUSES,
            include_file=True, include_back=True, back_callback="profile", lang=lang,
        )
        scene = "update" if is_update else "profile"
    else:
        kb = draft_edit_kb(prefix, t("btn_send_to_admin", lang), include_cancel=True, include_checked_on=False, lang=lang)
        scene = "update" if is_update else "new"
    await answer(target, text, kb, scene)


async def _toggle_settings_flag(cb: CallbackQuery, state: FSMContext, prefix: str, lang: str) -> None:
    plugin = dict((await state.get_data()).get("plugin") or {})
    plugin["has_ui_settings"] = not plugin.get("has_ui_settings")
    await state.update_data(plugin=plugin, edit_field=None)
    await _save_editor_change(state, cb.from_user, prefix)
    await _show_editor(cb, state, prefix, lang)
    await ack(cb, t("settings_toggled_on" if plugin["has_ui_settings"] else "settings_toggled_off", lang))


async def _ask_request_update_note(cb: CallbackQuery, state: FSMContext, request_id: str, lang: str) -> None:
    entry = _editable_own_request(request_id, cb.from_user.id if cb.from_user else None)
    if not entry:
        await cb.answer(t("resubmit_expired", lang), show_alert=True)
        return
    await _stage_pending_changes(state)
    await state.update_data(update_note_request_id=request_id)
    await state.set_state(UserFlow.entering_update_note)
    text = t("ask_update_note", lang)
    if entry["status"] in {"approved", "scheduled"}:
        text += "\n\n" + t("update_note_returns_to_review", lang)
    await answer(cb, text, cancel_kb(lang, back=True), "update" if entry["type"] == "update" else "new")
    await ack(cb)


@router.callback_query(F.data.regexp(r"^(?:pendreq|pendupdreq):[^:]+$"))
@router.callback_query(F.data.regexp(r"^pendupd:(?!(?:delete|delete_confirm|back|submit)$)[^:]+$"))
async def on_open_pending_request(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    entry = get_request_by_callback_token(cb.data.rsplit(":", 1)[-1])
    if not can_edit_request(entry):
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    payload = entry["payload"]
    if not cb.from_user or payload.get("user_id") != cb.from_user.id:
        await cb.answer(t("admin_denied", lang), show_alert=True)
        return

    is_update = entry.get("type") == "update"
    fields = ("plugin", "description_ru", "description_en", "usage_ru", "usage_en",
              "admin_comment", "comment_media", "is_appeal", "appeal_comment",
              "changelog", "update_slug", "old_plugin", "publish_not_before")
    old_plugin = payload.get("old_plugin") or {}
    old_locale = old_plugin.get("ru") or old_plugin.get("en") or {}
    await state.clear()
    await state.update_data(
        {field: deepcopy(payload[field]) for field in fields if field in payload},
        lang=lang,
        **category_fields(categories_for(payload) or categories_for(old_plugin)),
        pending_request_fingerprint=_request_edit_fingerprint(payload),
        pending_request_id=str(entry["id"]),
        draft_prefix="pendupd" if is_update else "pend",
        old_version=old_locale.get("version") or "",
    )
    await _show_editor(cb, state, "pendupd" if is_update else "pend", lang)
    await ack(cb)


@router.callback_query(F.data.startswith("profile:delete:"))
async def on_profile_delete(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    slug = decode_slug(cb.data.split(":", 2)[2])
    plugin = next((item for item in find_user_plugins(cb.from_user.id, cb.from_user.username or "")
                   if item.get("slug") == slug), None) if cb.from_user else None

    if not plugin:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    locale = plugin.get(lang) or plugin.get("ru") or {}
    payload = {
        "user_id": cb.from_user.id,
        "username": cb.from_user.username or "",
        "plugin": {
            "id": slug,
            "name": locale.get("name") or slug,
            "version": locale.get("version"),
            "min_version": locale.get("min_version"),
        },
        "delete_slug": slug,
        "submission_type": "delete",
    }

    await _ask_request_comment(cb, state, payload, "delete", "delete_sent", "ask_delete_reason", "delete",
                               comment_required=True, media_required=False)
    await ack(cb)


@router.callback_query(F.data.startswith("profile:update:"))
async def on_profile_update(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    slug = decode_slug(cb.data.split(":", 2)[2])
    plugin = next((item for item in find_user_plugins(cb.from_user.id, cb.from_user.username or "")
                   if item.get("slug") == slug), None) if cb.from_user else None

    if not plugin:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    current_version = plugin.get("ru", {}).get("version") or "?"
    await state.update_data(update_slug=slug, old_plugin=plugin, old_version=current_version, **category_fields(plugin), category_selection=None,
                            update_from_profile=True)
    await state.set_state(UserFlow.uploading_update_file)
    await answer(cb, t("upload_update_file", lang, version=current_version), cancel_kb(lang), "update")
    await ack(cb)


@router.message(UserFlow.uploading_update_file, F.document)
async def on_update_file(message: Message, state: FSMContext) -> None:
    if not await _ensure_not_banned(message, state):
        return
    lang = await get_language(message, state)
    await state.update_data(author_message_id=message.message_id)
    data = await state.get_data()
    old_plugin = data.get("old_plugin", {})
    old_version = data.get("old_version", "")
    is_admin = message.from_user.id in get_admins_plugins() if message.from_user else False

    plugin = await _read_plugin_document(message, lang)
    if plugin is None:
        return

    new_dict = plugin.to_dict()
    if not meets_min_supported(new_dict.get("min_version")):
        await message.answer(_submission_error_text(
            "min_version_too_low", lang, old_version=old_version,
            plugin_min=new_dict.get("min_version") or "",
        ))
        return

    if not is_admin:
        is_valid, error = validate_update_submission(new_dict, old_plugin)
        if not is_valid:
            await message.answer(_submission_error_text(
                error, lang, old_version=old_version, plugin_min=new_dict.get("min_version") or "",
            ))
            return

    new_plugin = plugin.to_dict()
    old_ru = (old_plugin.get("ru") or {}) if isinstance(old_plugin, dict) else {}
    old_en = (old_plugin.get("en") or {}) if isinstance(old_plugin, dict) else {}
    old_name = old_ru.get("name") or old_en.get("name")
    old_desc = old_ru.get("description") or old_en.get("description")
    old_min_version = old_ru.get("min_version") or old_en.get("min_version") or old_plugin.get("min_version")
    old_author = (old_plugin.get("authors") or {}).get("ru") or (old_plugin.get("authors") or {}).get("en")
    old_settings = old_plugin.get("settings") or {}
    old_has_settings = bool(old_settings.get("has_ui"))

    merged_plugin = {
        **new_plugin,
        "name": new_plugin.get("name") or old_name,
        "author": new_plugin.get("author") or old_author,
        "description": new_plugin.get("description") or old_desc or "",
        "min_version": new_plugin.get("min_version") or old_min_version or "",
        "has_ui_settings": new_plugin.get("has_ui_settings", old_has_settings),
    }

    fields = category_fields(categories_for(new_plugin) or categories_for(old_plugin))
    merged_plugin["categories"] = fields["category_keys"]
    await state.update_data(plugin=merged_plugin, **fields)
    await state.set_state(UserFlow.entering_changelog)
    await answer(message, t("enter_changelog", lang), cancel_kb(lang), "update")


@router.message(UserFlow.uploading_update_file)
async def on_update_file_invalid(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    await message.answer(t("invalid_file", lang))


@router.message(UserFlow.entering_changelog)
async def on_changelog(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    changelog = extract_html_text(message).strip()

    if not changelog:
        await message.answer(t("need_text", lang))
        return

    await state.update_data(changelog=changelog)

    data = await state.get_data()
    old_plugin = data.get("old_plugin", {})
    old_ru = (old_plugin.get("ru") or {}) if isinstance(old_plugin, dict) else {}
    old_en = (old_plugin.get("en") or {}) if isinstance(old_plugin, dict) else {}

    await state.update_data(
        description_ru=old_ru.get("description", ""),
        description_en=old_en.get("description", ""),
        usage_ru=old_ru.get("usage", ""),
        usage_en=old_en.get("usage", ""),
        **category_fields(data),
        draft_prefix="upd",
        edit_field=None,
        edit_lang=None,
    )
    await _show_editor(message, state, "upd", lang)


@router.callback_query(UserFlow.confirming_update, F.data.regexp(r"^(upd|pendupd):(?:edit:[^:]+|lang:[^:]+:[^:]+|cat:[^:]+|back|submit)$"))
async def on_update_edit(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)

    prefix = cb.data.split(":", 2)[0]
    parts = cb.data.split(":", 2)
    if len(parts) < 2 or prefix != (await state.get_data()).get("draft_prefix"):
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    action = parts[1]
    if action == "edit":
        if len(parts) != 3:
            await cb.answer(t("not_found", lang), show_alert=True)
            return
        await state.update_data(category_selection=None)
        field = parts[2]
        if field == "file" and prefix == "pendupd":
            await state.set_state(UserFlow.uploading_pending_file)
            await answer(cb, t("pending_upload_update_plugin", lang), cancel_kb(lang, back=True), None)
            await cb.answer()
            return
        if field in {"description", "usage"}:
            await state.update_data(edit_field=field)
            await answer(cb, t("admin_choose_language", lang), draft_lang_kb(prefix, field, lang=lang), None)
            await cb.answer()
            return
        if field == "category":
            await _open_category_picker(cb, state, prefix, lang)
            await cb.answer()
            return
        if field == "settings":
            await _toggle_settings_flag(cb, state, prefix, lang)
            return

        prompt = {
            "name": t("admin_prompt_new_name", lang),
            "author": t("admin_prompt_author", lang),
            "min_version": t("admin_prompt_min_version", lang),
        }.get(field, t("admin_prompt_value", lang))

        await state.update_data(edit_field=field)
        await state.set_state(UserFlow.editing_draft_field)
        await answer(cb, prompt, cancel_kb(lang, back=True), None)
        await cb.answer()
        return

    if action == "lang":
        _, _, field, lang_choice = cb.data.split(":")
        await state.update_data(edit_field=field, edit_lang=lang_choice)
        await state.set_state(UserFlow.editing_draft_field)

        prompt = t("admin_prompt_enter_text_ru", lang) if lang_choice == "ru" else t("admin_prompt_enter_text_en", lang)
        await answer(cb, prompt, cancel_kb(lang, back=True), None)
        await cb.answer()
        return

    if action == "cat":
        if not await _choose_categories(cb, state, prefix, lang):
            return
        await _save_editor_change(state, cb.from_user, prefix)
        await _show_editor(cb, state, prefix, lang)
        await cb.answer()
        return

    if action == "back":
        data = await state.get_data()
        if data.get("category_selection") and not await _category_picker_valid(cb, state, prefix, lang):
            return
        await state.update_data(category_selection=None, edit_field=None)
        await _show_editor(cb, state, prefix, lang)
        await cb.answer()
        return

    if action == "submit":
        if not categories_for(await state.get_data()):
            await cb.answer(t("categories_required", lang), show_alert=True)
            return
        await state.update_data(category_selection=None, edit_field=None)
        if prefix == "pendupd":
            data = await state.get_data()
            req_id = str(data.get("pending_request_id") or "")
            if not req_id:
                await cb.answer(t("not_found", lang), show_alert=True)
                return
            await _ask_request_update_note(cb, state, req_id, lang)
            return

        data = await state.get_data()
        user = cb.from_user
        payload = {
            "user_id": user.id,
            "username": user.username or "",
            "plugin": data.get("plugin", {}),
            "changelog": data.get("changelog", ""),
            "update_slug": data.get("update_slug", ""),
            "old_plugin": data.get("old_plugin", {}),
            "description_ru": data.get("description_ru", ""),
            "description_en": data.get("description_en", ""),
            "usage_ru": data.get("usage_ru", ""),
            "usage_en": data.get("usage_en", ""),
            **category_fields(data),
            "submission_type": "update",
        }

        await _ask_request_comment(cb, state, payload, "update", "update_sent", "ask_update_example", "update",
                                   comment_required=False, media_required=True)
        await cb.answer()
        return

    await ack(cb)


@router.callback_query(UserFlow.confirming_update, F.data == "pendupd:delete")
@router.callback_query(UserFlow.confirming_submission, F.data == "pend:delete")
async def on_pending_delete(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    request_id = str((await state.get_data()).get("pending_request_id") or "")
    entry = _editable_own_request(request_id, cb.from_user.id if cb.from_user else None)
    if not entry or entry.get("status") not in _WITHDRAWABLE_REQUEST_STATUSES:
        await cb.answer(t("resubmit_expired", lang), show_alert=True)
        return
    await state.update_data(withdraw_confirmation_request_id=request_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("btn_confirm", lang), style="danger",
                                     callback_data=f"usr:withdraw_confirm:{request_callback_token(request_id)}"),
                InlineKeyboardButton(text=t("btn_keep_editing", lang), callback_data=cb.data.replace(":delete", ":back")),
            ]
        ]
    )
    await answer(cb, t("pending_delete_confirm", lang), kb, None)
    await ack(cb)


@router.callback_query(UserFlow.confirming_update, F.data == "pendupd:delete_confirm")
@router.callback_query(UserFlow.confirming_submission, F.data == "pend:delete_confirm")
async def on_pending_delete_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    await _withdraw_own_request(cb, state)


async def _withdraw_own_request(cb: CallbackQuery, state: FSMContext, request_id: str | None = None) -> None:
    lang = await get_language(cb, state)
    data = await state.get_data()
    req_id = str(request_id or data.get("withdraw_confirmation_request_id") or "").strip()
    entry = _editable_own_request(req_id, cb.from_user.id if cb.from_user else None)
    if not entry or entry.get("status") not in _WITHDRAWABLE_REQUEST_STATUSES:
        await cb.answer(t("resubmit_expired", lang), show_alert=True)
        return
    if not acquire_request_lock(req_id):
        await cb.answer(t("admin_request_processing", lang), show_alert=True)
        return
    previous = deepcopy(entry)
    try:
        if not delete_request(req_id):
            await cb.answer(t("not_found", lang), show_alert=True)
            return
        from core.storage import flush_documents
        from bot.services.audit import add_audit_event

        add_audit_event("moderation.request_withdrawn", actor_id=cb.from_user.id,
                        actor=user_mention(cb.from_user.id, cb.from_user.username or ""), request_id=req_id)
        await flush_documents("requests")
    finally:
        release_request_lock(req_id)
    spawn_background(_cleanup_withdrawn_request(cb.bot, previous, user_mention(cb.from_user.id, cb.from_user.username or "")))
    await state.clear()
    await state.set_state(UserFlow.idle)
    await answer(cb, t("pending_deleted", lang), main_menu_kb(lang), "welcome")
    await ack(cb)


async def _cleanup_withdrawn_request(bot, entry: dict, actor: str) -> None:
    from bot.services.admin_notifications import finalize_admin_notify_messages

    results = await asyncio.gather(
        delete_forum_request_message(bot, entry),
        finalize_admin_notify_messages(bot, entry, t("pending_withdrawn_admin", "ru"), actor),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            logger.error("event=submission.withdraw_cleanup_failed request_id=%s", entry.get("id"),
                         exc_info=(type(result), result, result.__traceback__))


@router.callback_query(F.data.startswith("usr:withdraw_confirm:"))
async def on_scoped_request_withdraw(cb: CallbackQuery, state: FSMContext) -> None:
    entry = get_request_by_callback_token(cb.data.split(":")[2])
    if not entry:
        await cb.answer(t("resubmit_expired", await get_language(cb, state)), show_alert=True)
        return
    await _withdraw_own_request(cb, state, str(entry["id"]))


@router.message(UserFlow.uploading_file, F.document)
async def on_file(message: Message, state: FSMContext) -> None:
    if not await _ensure_not_banned(message, state):
        return
    lang = await get_language(message, state)

    spawn_background(try_react_pray(message))
    await state.update_data(author_message_id=message.message_id)

    plugin = await _read_plugin_document(message, lang)
    if plugin is None:
        return

    new_dict = plugin.to_dict()
    is_valid, error = validate_new_submission(new_dict)
    if not is_valid:
        await message.answer(_submission_error_text(
            error, lang, plugin_min=new_dict.get("min_version") or "",
        ))
        return

    is_duplicate, _ = check_duplicate_pending(
        plugin.id, plugin.name, exclude_user_id=message.from_user.id
    )
    if is_duplicate:
        await message.answer(t("plugin_pending", lang))
        return

    plugin_dict = plugin.to_dict()
    await state.update_data(
        plugin=plugin_dict,
        **category_fields(plugin_dict),
        draft_prefix="draft",
        category_selection=None,
        description_raw=plugin.description,
    )

    raw_min = plugin_dict.get("min_version")
    if not is_valid_version(raw_min) or not meets_min_supported(raw_min):
        await state.set_state(UserFlow.entering_min_version)
        prompt_key = "require_min_version" if not is_valid_version(raw_min) else "min_version_too_low"
        await answer(
            message,
            t(prompt_key, lang, min=get_min_supported_version(), current=normalize_version(raw_min) or "—"),
            cancel_kb(lang),
        )
        return

    await _continue_after_plugin_parsed(message, state, lang)


async def _continue_after_plugin_parsed(message: Message, state: FSMContext, lang: str) -> None:
    await _sync_submission_draft(state, message.from_user.id, message.from_user.username or "", "plugin")
    await state.set_state(UserFlow.choosing_description_language)

    draft_text = _render_draft_text(await state.get_data())
    await answer(message, f"{draft_text}\n\n<b>{t('choose_description_language', lang)}</b>", description_lang_kb(lang), "new")


def _submission_error_text(error: str, lang: str, *, old_version: str = "", plugin_min: str = "") -> str:
    if error == "min_version_too_low":
        return t(
            "min_version_too_low", lang,
            min=get_min_supported_version(),
            current=normalize_version(plugin_min) or "—",
        )
    if error == "version_not_higher":
        return t("version_not_higher", lang, current=old_version or "—")
    if error == "version_lower":
        suggested = old_version or "—"
        if old_version and old_version.count(".") == 1:
            suggested = f"{old_version}0"
        return t("version_lower", lang, current=old_version or "—", suggested=suggested)
    return t(error, lang)


@router.message(UserFlow.entering_min_version)
async def on_min_version_input(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    raw = (message.text or "").strip()
    if not is_valid_version(raw):
        await message.answer(t("invalid_min_version", lang))
        return
    if not meets_min_supported(raw):
        await message.answer(
            t("min_version_too_low", lang, min=get_min_supported_version(), current=normalize_version(raw)),
            parse_mode=ParseMode.HTML,
        )
        return

    data = await state.get_data()
    plugin = dict(data.get("plugin") or {})
    plugin["min_version"] = normalize_version(raw)
    await state.update_data(plugin=plugin)
    await _continue_after_plugin_parsed(message, state, lang)


@router.message(UserFlow.uploading_file)
async def on_file_invalid(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    await message.answer(t("invalid_file", lang))


@router.callback_query(UserFlow.choosing_description_language, F.data.startswith("desc_lang:"))
async def on_description_language(cb: CallbackQuery, state: FSMContext) -> None:
    lang_choice = cb.data.split(":")[1]
    lang = await get_language(cb, state)
    data = await state.get_data()
    raw_desc = data.get("description_raw", "")

    if lang_choice == "ru":
        await state.update_data(description_ru=raw_desc, description_source_lang="ru")
        prompt = t("enter_description_en", lang)
    else:
        await state.update_data(description_en=raw_desc, description_source_lang="en")
        prompt = t("enter_description_ru", lang)

    await _sync_submission_draft(state, cb.from_user.id, cb.from_user.username or "", "plugin")

    await state.set_state(UserFlow.editing_description_translation)
    await answer(cb, prompt, cancel_kb(lang), None)
    await ack(cb)


@router.message(UserFlow.editing_description_translation)
async def on_description_translation(message: Message, state: FSMContext) -> None:
    text = extract_html_text(message).strip()
    if not text:
        lang = await get_language(message, state)
        await message.answer(t("need_text", lang), disable_web_page_preview=True)
        return

    data = await state.get_data()
    source_lang = data.get("description_source_lang")

    if source_lang == "ru":
        await state.update_data(description_en=text)
    else:
        await state.update_data(description_ru=text)

    await _sync_submission_draft(state, message.from_user.id, message.from_user.username or "", "plugin")

    await state.set_state(UserFlow.editing_usage_ru)
    lang = await get_language(message, state)
    await answer(message, t("enter_usage_ru", lang), cancel_kb(lang))


@router.message(UserFlow.editing_usage_ru)
async def on_usage_ru(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    text = extract_html_text(message).strip()
    if not text:
        await message.answer(t("need_text", lang))
        return
    await state.update_data(usage_ru=text)
    await _sync_submission_draft(state, message.from_user.id, message.from_user.username or "", "plugin")
    await state.set_state(UserFlow.editing_usage_en)
    await answer(message, t("enter_usage_en", lang), cancel_kb(lang))


@router.message(UserFlow.editing_usage_en)
async def on_usage_en(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    text = extract_html_text(message).strip()
    if not text:
        await message.answer(t("need_text", lang))
        return
    await state.update_data(usage_en=text)
    await _sync_submission_draft(state, message.from_user.id, message.from_user.username or "", "plugin")
    await state.set_state(UserFlow.choosing_category)
    await _open_category_picker(message, state, "submit", lang)


@router.callback_query(UserFlow.choosing_category, F.data.startswith("submit:cat:"))
async def on_category_select(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    if cb.data == "submit:cat:back":
        if not await _category_picker_valid(cb, state, "submit", lang):
            return
        await state.update_data(category_selection=None, edit_field=None)
        await state.set_state(UserFlow.editing_usage_en)
        await answer(cb, t("enter_usage_en", lang), cancel_kb(lang), None)
        await ack(cb)
        return
    if not await _choose_categories(cb, state, "submit", lang):
        return
    await _save_editor_change(state, cb.from_user, "draft")
    await _show_editor(cb, state, "draft", lang)
    await ack(cb)


@router.callback_query(UserFlow.confirming_submission, F.data.regexp(r"^(draft|pend):edit:"))
async def on_draft_edit(cb: CallbackQuery, state: FSMContext) -> None:
    prefix = cb.data.split(":", 2)[0]
    field = cb.data.split(":")[2]
    lang = await get_language(cb, state)
    if prefix != (await state.get_data()).get("draft_prefix", "draft"):
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    await state.update_data(category_selection=None)

    if field == "file" and prefix == "pend":
        await state.set_state(UserFlow.uploading_pending_file)
        await answer(cb, t("pending_upload_plugin", lang), cancel_kb(lang, back=True), None)
        await cb.answer()
        return

    if field in {"description", "usage"}:
        await state.update_data(edit_field=field)
        await answer(cb, t("admin_choose_language", lang), draft_lang_kb(prefix, field, lang=lang), None)
        await cb.answer()
        return

    if field == "category":
        await _open_category_picker(cb, state, prefix, lang)
        await cb.answer()
        return
    if field == "settings":
        await _toggle_settings_flag(cb, state, prefix, lang)
        return

    prompt = {
        "name": t("admin_prompt_new_name", lang),
        "author": t("admin_prompt_author", lang),
        "min_version": t("admin_prompt_min_version", lang),
    }.get(field, t("admin_prompt_value", lang))

    await state.update_data(edit_field=field)
    await state.set_state(UserFlow.editing_draft_field)
    await answer(cb, prompt, cancel_kb(lang, back=True), None)
    await ack(cb)


@router.callback_query(UserFlow.confirming_submission, F.data.regexp(r"^(draft|pend):not_before$"))
async def on_draft_not_before(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    prefix = cb.data.split(":", 1)[0]
    lang = await get_language(cb, state)
    await state.update_data(draft_prefix=prefix)
    await state.set_state(UserFlow.entering_publish_not_before)
    await answer(cb, t("publish_not_before_prompt", lang), cancel_kb(lang, back=True), None)
    await ack(cb)


@router.message(UserFlow.entering_publish_not_before)
async def on_draft_not_before_value(message: Message, state: FSMContext) -> None:
    if not await _ensure_not_banned(message, state):
        return
    lang = await get_language(message, state)
    text = (message.text or "").strip()
    try:
        local_dt = _parse_user_publish_time(text)
    except ValueError:
        await message.answer(t("admin_post_schedule_bad_format", lang), parse_mode=ParseMode.HTML)
        return

    if local_dt <= datetime.now(tz=TZ_UTC_PLUS_5):
        await message.answer(t("admin_post_schedule_past", lang), parse_mode=ParseMode.HTML)
        return

    await state.update_data(publish_not_before=local_dt.astimezone(timezone.utc).isoformat())
    prefix = "pend" if (await state.get_data()).get("draft_prefix") == "pend" else "draft"
    await _save_editor_change(state, message.from_user, prefix)
    await _show_editor(message, state, prefix, lang)
    await message.answer(t("publish_not_before_saved", lang), parse_mode=ParseMode.HTML)


@router.callback_query(UserFlow.confirming_submission, F.data.regexp(r"^(draft|pend):lang:"))
async def on_draft_language(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, field, lang_choice = cb.data.split(":")
    await state.update_data(edit_field=field, edit_lang=lang_choice)
    await state.set_state(UserFlow.editing_draft_field)

    lang = await get_language(cb, state)
    prompt = t("admin_prompt_enter_text_ru", lang) if lang_choice == "ru" else t("admin_prompt_enter_text_en", lang)

    await answer(cb, prompt, cancel_kb(lang, back=True), None)
    await ack(cb)


@router.callback_query(UserFlow.confirming_submission, F.data.regexp(r"^(draft|pend):cat:"))
async def on_draft_category(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    prefix = cb.data.split(":", 2)[0]
    if not await _choose_categories(cb, state, prefix, lang):
        return
    await _save_editor_change(state, cb.from_user, prefix)
    await _show_editor(cb, state, prefix, lang)
    await ack(cb)


@router.callback_query(UserFlow.confirming_submission, F.data.regexp(r"^(draft|pend):back$"))
async def on_draft_back(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    prefix = cb.data.split(":", 1)[0]
    data = await state.get_data()
    if data.get("category_selection") and not await _category_picker_valid(cb, state, prefix, lang):
        return
    await state.update_data(category_selection=None, edit_field=None)
    await _show_editor(cb, state, prefix, lang)
    await ack(cb)


@router.message(UserFlow.editing_draft_field)
async def on_draft_field_value(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    text_html = extract_html_text(message).strip()
    text_plain = (message.text or "").replace("\\n", "\n").strip()
    if not text_html and not text_plain:
        await message.answer(
            t("need_text", lang),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
        return

    data = await state.get_data()
    field = data.get("edit_field")
    edit_lang = data.get("edit_lang")
    prefix = (data.get("draft_prefix") or "draft").strip()

    plugin = dict(data.get("plugin") or {})
    if field in {"description", "usage"}:
        await state.update_data({f"{field}_{edit_lang}": text_html})
    elif field in {"name", "author"}:
        if len(text_plain) > _DRAFT_NAME_LIMIT:
            await message.answer(t("draft_name_too_long", lang, max=_DRAFT_NAME_LIMIT))
            return
        plugin[field] = text_plain
        await state.update_data(plugin=plugin)
    elif field == "min_version":
        if not is_valid_version(text_plain):
            await message.answer(t("invalid_min_version", lang))
            return
        if not meets_min_supported(text_plain):
            await message.answer(
                t("min_version_too_low", lang, min=get_min_supported_version(), current=normalize_version(text_plain)),
                parse_mode=ParseMode.HTML,
            )
            return
        plugin["min_version"] = normalize_version(text_plain)
        await state.update_data(plugin=plugin)

    await state.update_data(edit_field=None, edit_lang=None)
    await _save_editor_change(state, message.from_user, prefix)
    await _show_editor(message, state, prefix, lang)


@router.message(UserFlow.uploading_pending_file, F.document)
async def on_pending_file(message: Message, state: FSMContext) -> None:
    if not await _ensure_not_banned(message, state):
        return
    lang = await get_language(message, state)
    data = await state.get_data()
    req_id = str(data.get("pending_request_id") or "").strip()
    if not req_id:
        await state.set_state(UserFlow.idle)
        await message.answer(t("not_found", lang))
        return

    entry = _editable_own_request(req_id, message.from_user.id if message.from_user else None)
    if not entry:
        await message.answer(t("resubmit_expired", lang))
        return
    existing = data.get("plugin", {})
    expected_id = (existing.get("id") or "").strip()

    plugin = await _read_plugin_document(message, lang)
    if plugin is None:
        return

    new_plugin = plugin.to_dict()
    valid, error = validate_request_replacement(entry, new_plugin)
    if not valid:
        await message.answer(_submission_error_text(
            error, lang, old_version=data.get("old_version") or "", plugin_min=new_plugin.get("min_version") or "",
        ))
        return

    merged = {
        **new_plugin,
        "id": expected_id or new_plugin.get("id"),
        "name": new_plugin.get("name") or existing.get("name"),
        "author": new_plugin.get("author") or existing.get("author"),
        "description": new_plugin.get("description") or existing.get("description"),
        "min_version": new_plugin.get("min_version") or existing.get("min_version"),
        "has_ui_settings": new_plugin.get("has_ui_settings", existing.get("has_ui_settings")),
    }

    fields = category_fields(categories_for(data) or categories_for(new_plugin))
    merged["categories"] = fields["category_keys"]
    await state.update_data(plugin=merged, **fields)
    await _stage_pending_changes(state)

    await _show_editor(message, state, "pendupd" if entry.get("type") == "update" else "pend", lang)


@router.message(UserFlow.uploading_pending_file)
async def on_pending_file_invalid(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    await message.answer(t("invalid_file", lang))


@router.callback_query(UserFlow.confirming_submission, F.data.regexp(r"^(draft|pend):submit$"))
async def on_draft_submit(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    prefix = cb.data.split(":", 1)[0]
    if not categories_for(await state.get_data()):
        await cb.answer(t("categories_required", lang), show_alert=True)
        return
    await state.update_data(category_selection=None, edit_field=None)

    if prefix == "pend":
        data = await state.get_data()
        req_id = str(data.get("pending_request_id") or "")
        if not req_id:
            await cb.answer(t("not_found", lang), show_alert=True)
            return
        await _ask_request_update_note(cb, state, req_id, lang)
        return

    data = await state.get_data()
    user = cb.from_user

    missing = missing_draft_fields({
        "plugin": data.get("plugin") or {},
        "description_ru": data.get("description_ru"),
        "description_en": data.get("description_en"),
        "usage_ru": data.get("usage_ru"),
        "usage_en": data.get("usage_en"),
        **category_fields(data),
    })
    if missing:
        labels = ", ".join(t(f"field_{f.replace('.', '_')}", lang) for f in missing)
        await cb.answer(t("draft_missing_fields", lang, fields=labels), show_alert=True)
        return

    plugin_dict = data.get("plugin", {})
    plugin = PluginData(
        id=plugin_dict.get("id", ""),
        name=plugin_dict.get("name", ""),
        description=plugin_dict.get("description", ""),
        author=plugin_dict.get("author", ""),
        version=plugin_dict.get("version", ""),
        min_version=plugin_dict.get("min_version", ""),
        has_settings=plugin_dict.get("has_ui_settings", False),
        file_path=plugin_dict.get("file_path", ""),
        file_id=plugin_dict.get("file_id"),
        file_extension=plugin_dict.get("file_extension", "plugin"),
        app_version=plugin_dict.get("app_version", ""),
        metadata={k: plugin_dict[k] for k in ("sdk_version", "elyx_version", "requirements", "requires", "localized", "compiled", "main", "metainfo", "icon", "link", "categories") if k in plugin_dict},
    )

    payload = build_submission_payload(
        user.id,
        user.username or "",
        plugin,
        data.get("description_ru", ""),
        data.get("description_en", ""),
        data.get("usage_ru", ""),
        data.get("usage_en", ""),
        data.get("category_keys") or categories_for(data),
    )
    payload.update(category_fields(data))
    if data.get("publish_not_before"):
        payload["publish_not_before"] = data.get("publish_not_before")

    await _ask_request_comment(cb, state, payload, "new", "submission_sent", "ask_submission_example", "new",
                               comment_required=False, media_required=True)
    await ack(cb)


COMMENT_MEDIA_LIMIT = limits.ALBUM_ITEMS


def _comment_media_of(message: Message) -> dict | None:
    if message.photo:
        return {"type": "photo", "file_id": message.photo[-1].file_id}
    if message.video:
        return {"type": "video", "file_id": message.video.file_id}
    return None


def _comment_media_owner(target: Message | CallbackQuery) -> tuple[int, int]:
    message = target.message if isinstance(target, CallbackQuery) else target
    chat_id = int(message.chat.id) if message and message.chat else 0
    user_id = int(target.from_user.id) if target.from_user else 0
    return chat_id, user_id


async def _store_comment_media_group(pending: dict[str, Any], *, render: bool = True) -> None:
    state: FSMContext = pending["state"]
    if await state.get_state() != UserFlow.entering_admin_comment.state:
        return

    target: Message = pending["target"]
    data = await state.get_data()
    media = list(data.get("pending_comment_media") or [])
    existing = {
        (str(item.get("type") or ""), str(item.get("file_id") or ""))
        for item in media
        if isinstance(item, dict)
    }
    limit_reached = False
    for item in pending["items"]:
        item_key = (str(item.get("type") or ""), str(item.get("file_id") or ""))
        if item_key in existing:
            continue
        if len(media) >= COMMENT_MEDIA_LIMIT:
            limit_reached = True
            break
        media.append(item)
        existing.add(item_key)

    update: dict[str, Any] = {"pending_comment_media": media}
    caption = str(pending.get("caption") or "").strip()
    if caption:
        update["pending_comment"] = caption
    await state.update_data(**update)

    if render or limit_reached:
        lang = await get_language(target, state)
        if limit_reached:
            await target.answer(t("comment_media_limit", lang, max=COMMENT_MEDIA_LIMIT))
        if render:
            await _render_comment_state(target, state, lang)


async def _queue_comment_media_group(message: Message, state: FSMContext, item: dict) -> None:
    owner = _comment_media_owner(message)
    key = (*owner, str(message.media_group_id))
    pending = _comment_media_group_buffers.setdefault(
        key,
        {"items": [], "caption": "", "target": message, "state": state, "task": None},
    )
    item_key = (str(item.get("type") or ""), str(item.get("file_id") or ""))
    existing = {
        (str(value.get("type") or ""), str(value.get("file_id") or ""))
        for value in pending["items"]
        if isinstance(value, dict)
    }
    if item_key not in existing and len(pending["items"]) < COMMENT_MEDIA_LIMIT:
        pending["items"].append(item)
    caption = extract_html_text(message).strip()
    if caption:
        pending["caption"] = caption
    pending["target"] = message
    pending["state"] = state

    previous = pending.get("task")
    if previous and not previous.done():
        previous.cancel()

    async def _settle() -> None:
        try:
            await asyncio.sleep(_COMMENT_MEDIA_GROUP_SETTLE_SECONDS)
        except asyncio.CancelledError:
            return
        current = _comment_media_group_buffers.get(key)
        if not current or current.get("task") is not asyncio.current_task():
            return
        _comment_media_group_buffers.pop(key, None)
        try:
            await _store_comment_media_group(current)
        except Exception:
            logger.exception("submission: comment media group finalize failed key=%s", key)

    pending["task"] = asyncio.create_task(_settle())


async def _discard_pending_comment_media_groups(target: Message | CallbackQuery) -> None:
    owner = _comment_media_owner(target)
    for key in [key for key in _comment_media_group_buffers if key[:2] == owner]:
        pending = _comment_media_group_buffers.pop(key)
        task = pending.get("task")
        if task and not task.done():
            task.cancel()


async def _flush_pending_comment_media_groups(target: Message | CallbackQuery) -> None:
    owner = _comment_media_owner(target)
    for key in [key for key in _comment_media_group_buffers if key[:2] == owner]:
        pending = _comment_media_group_buffers.pop(key)
        task = pending.get("task")
        if task and not task.done():
            task.cancel()
        await _store_comment_media_group(pending, render=False)


async def _render_comment_state(target, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    comment = str(data.get("pending_comment") or "").strip()
    media = data.get("pending_comment_media") or []
    comment_required = bool(data.get("comment_required"))
    media_required = bool(data.get("media_required"))
    ready = bool(comment or media)
    if comment_required and not comment:
        ready = False
    if media_required and not media:
        ready = False
    if not comment and not media:
        if comment_required:
            text = t("ask_delete_reason", lang, max=COMMENT_MEDIA_LIMIT)
        elif media_required:
            key = "ask_update_example" if data.get("pending_request_type") == "update" else "ask_submission_example"
            text = t(key, lang, max=COMMENT_MEDIA_LIMIT)
        else:
            text = t("ask_admin_comment", lang, max=COMMENT_MEDIA_LIMIT)
    else:
        state_key = "comment_state"
        if media_required:
            state_key = "comment_state_update_media_required" if data.get("pending_request_type") == "update" else "comment_state_media_required"
        text = t(
            state_key, lang,
            comment=strip_blockquote_tags(comment) or t("comment_no_text", lang),
            count=len(media), max=COMMENT_MEDIA_LIMIT,
        )
    await answer(
        target,
        text,
        comment_skip_kb(lang, ready, len(media), comment_required or media_required,
                        back=data.get("pending_request_type") in {"new", "update"}),
        "delete" if comment_required else ("update" if data.get("pending_request_type") == "update" else "new"),
    )


@router.callback_query(UserFlow.entering_admin_comment, F.data == "comment:skip")
async def on_admin_comment_skip(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    data = await state.get_data()
    if data.get("media_required"):
        await cb.answer(t("submission_example_required", await get_language(cb, state)), show_alert=True)
        return
    await _discard_pending_comment_media_groups(cb)
    await state.update_data(pending_comment=None, pending_comment_media=[])
    await _finalize_submission(cb, state, comment=None)


@router.callback_query(UserFlow.entering_admin_comment, F.data == "comment:reset")
async def on_admin_comment_reset(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    await _discard_pending_comment_media_groups(cb)
    lang = await get_language(cb, state)
    await state.update_data(pending_comment=None, pending_comment_media=[])
    await _render_comment_state(cb, state, lang)
    await ack(cb, t("comment_cleared", lang))


@router.callback_query(UserFlow.entering_admin_comment, F.data == "comment:send")
async def on_admin_comment_send(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    await _flush_pending_comment_media_groups(cb)
    data = await state.get_data()
    comment = str(data.get("pending_comment") or "").strip()
    if bool(data.get("comment_required")) and not comment:
        await cb.answer(t("delete_reason_required", await get_language(cb, state)), show_alert=True)
        return
    if bool(data.get("media_required")) and not data.get("pending_comment_media"):
        await cb.answer(t("submission_example_required", await get_language(cb, state)), show_alert=True)
        return
    await _finalize_submission(
        cb, state,
        comment=comment or None,
        media=data.get("pending_comment_media") or [],
    )


@router.message(UserFlow.entering_admin_comment)
async def on_admin_comment(message: Message, state: FSMContext) -> None:
    if not await _ensure_not_banned(message, state):
        return
    lang = await get_language(message, state)
    item = _comment_media_of(message)
    if item:
        if message.media_group_id:
            await _queue_comment_media_group(message, state, item)
            return
        data = await state.get_data()
        media = list(data.get("pending_comment_media") or [])
        if len(media) >= COMMENT_MEDIA_LIMIT:
            await message.answer(t("comment_media_limit", lang, max=COMMENT_MEDIA_LIMIT))
            return
        media.append(item)
        await state.update_data(pending_comment_media=media)
        caption = extract_html_text(message).strip()
        if caption:
            await state.update_data(pending_comment=caption)
        await _render_comment_state(message, state, lang)
        return

    if message.document or message.audio or message.animation or message.voice:
        await message.answer(t("comment_media_unsupported", lang))
        return

    await _flush_pending_comment_media_groups(message)
    text = extract_html_text(message).strip()
    if not text:
        return
    await state.update_data(pending_comment=text)
    await _render_comment_state(message, state, lang)


async def _finalize_submission(
    target: Message | CallbackQuery,
    state: FSMContext,
    comment: str | None,
    media: list | None = None,
) -> None:
    if not await _ensure_not_banned(target, state):
        return
    data = await state.get_data()
    payload = data.get("pending_payload", {})
    if isinstance(payload, dict) and data.get("author_message_id"):
        payload.setdefault("author_message_id", int(data["author_message_id"]))
    request_type = data.get("pending_request_type", "new")
    reply_key = data.get("pending_reply_key", "submission_sent")
    lang = await get_language(target, state)

    if request_type in {"new", "update"} and not media:
        message = t("submission_example_required", lang)
        if isinstance(target, CallbackQuery):
            await target.answer(message, show_alert=True)
        else:
            await target.answer(message)
        return

    if comment:
        payload["admin_comment"] = comment
    if media:
        payload["comment_media"] = list(media)[:COMMENT_MEDIA_LIMIT]

    if request_type in {"new", "update"} and payload.get("plugin"):
        if await prepare_submission_categories(payload):
            payload["moderation_file_id"] = None

    if request_type == "update":
        user_id = payload.get("user_id")
        update_slug_raw = payload.get("update_slug")
        update_slug = str(update_slug_raw or "").strip()
        if not update_slug:
            plugin_payload = payload.get("plugin", {})
            if isinstance(plugin_payload, dict):
                update_slug = str(plugin_payload.get("id") or "").strip()
        if user_id and update_slug:
            existing_updates = [
                r
                for r in get_user_requests(int(user_id))
                if r.get("status") == "pending"
                and r.get("type") == "update"
                and str((r.get("payload", {}) or {}).get("update_slug") or "").strip() == update_slug
            ]

            def _merge_text(old: str | None, new: str | None) -> str:
                old_s = (old or "").strip()
                new_s = (new or "").strip()
                if not old_s:
                    return new_s
                if not new_s:
                    return old_s
                if new_s in old_s:
                    return old_s
                if old_s in new_s:
                    return new_s
                return f"{old_s}\n\n{new_s}"

            def _prefer(old: str | None, new: str | None) -> str:
                new_s = (new or "").strip()
                return new_s if new_s else (old or "")

            if existing_updates:
                keep = existing_updates[0]
                keep_id = keep.get("id")
                keep_payload = keep.get("payload", {}) if isinstance(keep.get("payload"), dict) else {}

                old_plugin = keep_payload.get("plugin", {}) if isinstance(keep_payload.get("plugin"), dict) else {}
                new_plugin = payload.get("plugin", {}) if isinstance(payload.get("plugin"), dict) else {}

                merged_plugin = {**old_plugin, **new_plugin}
                merged_payload = {
                    **keep_payload,
                    **payload,
                    "plugin": merged_plugin,
                    "changelog": _merge_text(keep_payload.get("changelog"), payload.get("changelog")),
                    "description_ru": _prefer(keep_payload.get("description_ru"), payload.get("description_ru")),
                    "description_en": _prefer(keep_payload.get("description_en"), payload.get("description_en")),
                    "usage_ru": _prefer(keep_payload.get("usage_ru"), payload.get("usage_ru")),
                    "usage_en": _prefer(keep_payload.get("usage_en"), payload.get("usage_en")),
                    **category_fields(categories_for(payload) or categories_for(keep_payload)),
                }

                if keep_id:
                    update_request_payload(str(keep_id), merged_payload)

                    for extra in existing_updates[1:]:
                        extra_id = str(extra.get("id") or "").strip()
                        if extra_id:
                            delete_request(extra_id)

                    entry = get_request_by_id(str(keep_id)) or keep
                    spawn_background(notify_admins_request(target.bot, entry))

                    await state.set_state(UserFlow.idle)
                    await state.update_data(draft_request_id=None)
                    await answer(target, t(reply_key, lang), main_menu_kb(lang), "welcome")
                    if isinstance(target, CallbackQuery):
                        await target.answer()
                    return

    draft_id = data.get("draft_request_id")
    if draft_id:
        entry = promote_draft_request(draft_id, payload)
        if entry is None:
            entry = add_request(payload, request_type=request_type)
    else:
        entry = add_request(payload, request_type=request_type)

    submission_type = _submission_type(payload)
    logger.info(
        "event=submission.created request_id=%s request_type=%s submission_type=%s user_id=%s username=%s item=%s has_comment=%s",
        entry.get("id"),
        entry.get("type", request_type),
        submission_type,
        payload.get("user_id"),
        payload.get("username") or "-",
        _submission_name(payload, submission_type),
        bool(comment),
    )

    spawn_background(notify_admins_request(target.bot, entry))

    await state.set_state(UserFlow.idle)
    await state.update_data(draft_request_id=None)
    await answer(target, t(reply_key, lang), main_menu_kb(lang), "welcome")
    if isinstance(target, CallbackQuery):
        await target.answer()


async def notify_admins_request(bot, entry: Dict[str, Any]) -> None:
    payload = entry.get("payload", {})
    submission_type = _submission_type(payload)
    request_id = entry.get("id", "?")
    text, file_path = forum_request_text_and_file(entry)
    logger.info(
        "event=submission.notify_forum.start request_id=%s request_type=%s submission_type=%s item=%s",
        request_id, entry.get("type", "new"), submission_type, _submission_name(payload, submission_type),
    )

    try:
        await send_request_to_forum(bot, entry, text, file_path)
        delivered = 1
        failed = 0
    except Exception:
        delivered = 0
        failed = 1
        logger.warning(
            "event=submission.notify_forum.failed request_id=%s submission_type=%s",
            request_id,
            submission_type,
            exc_info=True,
        )

    logger.info(
        "event=submission.notify_forum.done request_id=%s delivered=%s failed=%s",
        request_id,
        delivered,
        failed,
    )

    await send_review_notifications(bot, entry, text, file_path)


@router.callback_query(F.data.startswith("resub:"))
async def on_resubmit_request(cb: CallbackQuery, state: FSMContext) -> None:
    lang = get_lang(cb.from_user.id if cb.from_user else None)
    request_id = cb.data.split(":", 1)[1]
    entry = get_request_by_callback_token(request_id)
    if (
        not entry
        or entry.get("status") != "rework"
        or entry.get("payload", {}).get("user_id") != (cb.from_user.id if cb.from_user else None)
    ):
        await cb.answer(t("resubmit_expired", lang), show_alert=True)
        return

    request_id = str(entry["id"])
    payload = deepcopy(entry.get("payload") or {})
    payload.update(category_fields(categories_for(payload) or categories_for(payload.get("old_plugin") or {})))
    missing = missing_draft_fields(payload)
    if missing:
        await cb.answer(t("resubmit_missing_fields", lang), show_alert=True)
        return

    snapshot = str(payload.get("rework_fingerprint") or "")
    if snapshot and submission_fingerprint(payload) == snapshot:
        await cb.answer(t("resubmit_no_changes", lang), show_alert=True)
        return

    await state.update_data(update_note_request_id=request_id,
                            pending_request_changes=None,
                            pending_request_fingerprint=_request_edit_fingerprint(payload))
    await state.set_state(UserFlow.entering_update_note)
    await answer(cb, t("ask_update_note", lang), cancel_kb(lang), "profile")
    await ack(cb)


@router.message(UserFlow.entering_update_note)
async def on_update_note(message: Message, state: FSMContext) -> None:
    if not await _ensure_not_banned(message, state):
        return
    lang = await get_language(message, state)
    if (message.text or "").lstrip().startswith("/"):
        await message.answer(t("command_ignored_in_input", lang))
        return
    note = strip_blockquote_tags(telegram_html(message.html_text or message.text or "")).strip()
    if not note:
        await message.answer(t("need_text", lang), parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    req_id = str(data.get("update_note_request_id") or "")
    entry = _editable_own_request(req_id, message.from_user.id if message.from_user else None)
    if not entry:
        await state.set_state(UserFlow.idle)
        await message.answer(t("resubmit_expired", lang))
        return

    if not acquire_request_lock(req_id):
        await message.answer(t("admin_request_processing", lang))
        return
    try:
        payload = entry.get("payload") or {}
        baseline = data.get("pending_request_fingerprint")
        if baseline and baseline != _request_edit_fingerprint(payload):
            await message.answer(t("pending_request_changed", lang))
            return
        candidate = {**deepcopy(payload), **deepcopy(data.get("pending_request_changes") or {})}
        candidate.update(category_fields(categories_for(candidate) or categories_for(candidate.get("old_plugin") or {})))
        if missing_draft_fields(candidate):
            await message.answer(t("resubmit_missing_fields", lang))
            return
        changed = _request_edit_fingerprint(candidate) != _request_edit_fingerprint(payload)
        if entry.get("status") == "rework":
            snapshot = str(payload.get("rework_fingerprint") or "")
            changed = changed or bool(snapshot and submission_fingerprint(candidate) != snapshot)
        if not changed and not payload.get("update_notification_pending"):
            await message.answer(t("resubmit_no_changes", lang))
            return
        plugin = candidate.get("plugin") or {}
        valid, error = validate_request_replacement(entry, plugin)
        if not valid:
            await message.answer(_submission_error_text(error, lang, plugin_min=plugin.get("min_version") or ""))
            return
        path = Path(plugin.get("file_path") or "")
        if not path.is_file():
            await message.answer(t("pending_replacement_missing", lang))
            return
        if changed and str(path) == str((payload.get("plugin") or {}).get("file_path") or ""):
            replacement = path.with_name(f"edit-{uuid4().hex}.{plugin_extension(path)}")
            await asyncio.to_thread(os.link, path, replacement)
            plugin["file_path"] = str(replacement)
            plugin["file_id"] = None
        await prepare_submission_categories(candidate)
        current = _editable_own_request(req_id, message.from_user.id if message.from_user else None)
        if not current or (baseline and baseline != _request_edit_fingerprint(current.get("payload") or {})):
            await message.answer(t("pending_request_changed", lang))
            return
        from bot.services.moderation import reset_vote_round_fields

        previous_status = current["status"]
        actor = "@" + message.from_user.username if message.from_user.username else str(message.from_user.id)
        updates = deepcopy(data.get("pending_request_changes") or {})
        updates.update({
            "plugin": candidate["plugin"], **category_fields(candidate),
            "last_update_note": note, "update_notification_pending": True,
            "moderation_file_id": None, "moderation_file_name": None,
            "moderation_pending_votes": {},
        })
        if changed or previous_status == "rework":
            updates.update(reset_vote_round_fields(current["payload"], after_rework=previous_status == "rework"))
        if previous_status != "pending":
            updates.update(dict.fromkeys((
                "scheduled_at", "publish_retry_at", "approved_at", "approved_by", "approved_by_id", "approval_notified_for",
                "approval_notification_progress", "approval_notification_retry_at", "last_publish_error", "last_publish_error_at",
            )))
            update_request_status(req_id, "pending", comment=note, actor=actor,
                                  actor_id=message.from_user.id, payload_fields=updates)
        else:
            update_request_payload(req_id, updates)
        from core.storage import flush_documents
        from bot.services.audit import add_audit_event

        add_audit_event("moderation.request_updated", actor_id=message.from_user.id, actor=actor,
                        request_id=req_id, details={"comment": note, "previous_status": previous_status})
        await flush_documents("requests")
        delivered = await _notify_admins_request_updated(message.bot, current)
    finally:
        release_request_lock(req_id)
    await state.update_data(update_note_request_id=None, pending_request_changes=None, pending_request_fingerprint=None)
    await state.set_state(UserFlow.idle)
    await message.answer(t("update_note_saved" if delivered else "update_note_delivery_failed", lang), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^(submit|draft|pend|upd|pendupd):cat:"))
async def on_stale_category(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer(t("not_found", await get_language(cb, state)), show_alert=True)
