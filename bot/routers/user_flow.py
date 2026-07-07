import asyncio
import re
import html
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

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
from aiogram.exceptions import TelegramBadRequest

from bot.context import get_language, get_lang
from bot.callback_tokens import decode_slug, encode_slug
from bot.formatting import plain_html, strip_blockquote_tags, telegram_html, user_mention
from bot.helpers import answer, extract_html_text, try_react_pray
from bot.menu_owner import MenuOwnerMiddleware, remember_menu_owner
from bot.keyboards import (
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
from storage import load_stenka, save_stenka
from bot.cache import get_admins, get_admins_icons, get_admins_plugins, get_admins_super, get_categories, get_config
from bot.services.submission import (
    PluginData,
    build_submission_payload,
    process_plugin_file,
)
from bot.services.publish import build_channel_post, update_plugin
from bot.services.admin_notifications import refresh_admin_notify_messages, send_review_notifications
from bot.services.moderation import can_vote_in_context, send_request_to_forum, set_vote
from bot.services.versioning import is_valid_version, normalize_version
from bot.services.validation import (
    check_duplicate_pending,
    validate_new_submission,
    validate_update_submission,
)
from bot.states import UserFlow
from bot.texts import TEXTS, t
from catalog import find_plugin_by_slug, find_plugin_by_deeplink_token, find_user_plugins, is_external_plugin
from bot.routers.catalog_flow import build_plugin_preview
from bot.routers.catalog_flow import BOT_USERNAME
from bot.keyboards import plugin_detail_kb
from subscription_store import is_subscribed, ALL_SUBSCRIPTION_KEY
from bot.keyboards import catalog_main_kb, profile_kb, admin_menu_kb
from bot.states import AdminFlow
from catalog import find_user_icons
from request_store import (
    add_draft_request,
    add_request,
    delete_request_and_file,
    discard_user_drafts,
    get_user_requests,
    get_request_by_id,
    promote_draft_request,
    update_request_payload,
    update_request_status,
)
from user_store import get_user_language, is_user_banned, set_user_language
from user_store import set_broadcast_enabled, set_paid_broadcast_disable

router = Router(name="user-flow")
router.callback_query.middleware(MenuOwnerMiddleware())
logger = logging.getLogger(__name__)
TZ_UTC_PLUS_5 = timezone(timedelta(hours=5))


@router.pre_checkout_query()
async def on_pre_checkout_query(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    payment = message.successful_payment
    if not payment:
        return

    if (payment.invoice_payload or "") != "simple_payment:broadcast_disable":
        return

    if not message.from_user:
        return

    user_id = message.from_user.id
    set_paid_broadcast_disable(user_id, True)
    set_broadcast_enabled(user_id, False)

    asyncio.create_task(notify_admins_broadcast_paid_disable(message.bot, message.from_user, payment))

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
            await target.answer(t("user_banned", lang), parse_mode=ParseMode.HTML)
        return False

    return True


def _build_draft_entry(data: Dict[str, Any]) -> Dict[str, Any]:
    plugin = data.get("plugin", {})
    payload = {
        "plugin": plugin,
        "description_ru": data.get("description_ru"),
        "description_en": data.get("description_en"),
        "usage_ru": data.get("usage_ru"),
        "usage_en": data.get("usage_en"),
        "category_key": data.get("category_key"),
        "publish_not_before": data.get("publish_not_before"),
    }
    return {"payload": payload}


def _parse_user_publish_time(value: str) -> datetime:
    return datetime.strptime(value, "%d.%m.%Y %H:%M").replace(tzinfo=TZ_UTC_PLUS_5)


def _format_publish_not_before(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ_UTC_PLUS_5)
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
    not_before = _format_publish_not_before(data.get("publish_not_before"))
    if not_before:
        text += f"\n\n<b>Не публиковать раньше:</b> <code>{not_before} UTC+5</code>"
    return text


def _render_update_text(data: Dict[str, Any]) -> str:
    plugin = data.get("plugin", {})
    payload = {
        "plugin": plugin,
        "description_ru": data.get("description_ru"),
        "description_en": data.get("description_en"),
        "usage_ru": data.get("usage_ru"),
        "usage_en": data.get("usage_en"),
        "category_key": data.get("category_key"),
    }
    return build_channel_post({"payload": payload})


async def _render_home(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await state.update_data(lang=lang)
    await state.set_state(UserFlow.idle)
    if cb.from_user and (cb.from_user.username or "").strip():
        user_name = f"@{cb.from_user.username.strip()}"
    else:
        user_name = (cb.from_user.full_name if cb.from_user else "").strip() or (cb.from_user.first_name if cb.from_user else "").strip() or "—"
    msg = await answer(
        cb,
        t("welcome", lang, bot=BOT_USERNAME, bot_name=BOT_USERNAME, user_name=user_name),
        main_menu_kb(lang),
        "welcome",
    )
    if cb.message:
        await try_react_pray(cb.message)
    elif msg:
        await try_react_pray(msg)
    if msg:
        await remember_menu_owner(cb, state, msg)


async def _render_profile_message(message: Message, state: FSMContext, lang: str) -> None:
    user = message.from_user
    if not user:
        return

    user_plugins = find_user_plugins(user.id, user.username or "")
    user_icons = find_user_icons(user.id, user.username or "")

    pending = []
    for req in get_user_requests(user.id):
        if req.get("status") != "pending":
            continue
        if req.get("type") not in {"new", "update"}:
            continue
        payload = req.get("payload", {})
        submission_type = (payload.get("submission_type") or payload.get("type") or "").strip()
        if submission_type not in {"plugin", "update"} and not payload.get("plugin"):
            continue
        pending.append(req)

    await state.update_data(
        my_plugins=[plugin.get("slug") for plugin in user_plugins],
        my_pending_plugins=[req.get("id") for req in pending if req.get("id")],
        my_icons=[icon.get("slug") for icon in user_icons],
    )

    text = f"{t('profile_title', lang)}\n\n"
    if user.username:
        text += f"@{user.username}\n"
    text += t("profile_stats", lang, plugins=len(user_plugins), icons=len(user_icons))
    if not user_plugins and not user_icons:
        text += f"\n\n{t('profile_empty', lang)}"

    notify_all_enabled = is_subscribed(user.id, ALL_SUBSCRIPTION_KEY)
    sent = await answer(
        message,
        text,
        profile_kb(lang, has_plugins=bool(user_plugins), has_icons=bool(user_icons), notify_all_enabled=notify_all_enabled),
        "profile",
    )
    if sent:
        await remember_menu_owner(message, state, sent)


async def _route_start_payload_message(message: Message, state: FSMContext, lang: str, payload: str) -> bool:
    raw_value = (payload or "").strip()
    value = raw_value.lower()
    if not raw_value:
        return False

    if raw_value.startswith(("modvote_yes_", "modvote_no_")):
        vote = "yes" if raw_value.startswith("modvote_yes_") else "no"
        request_id = unquote(raw_value.split("_", 2)[2])
        entry = get_request_by_id(request_id)
        request_payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
        inline_public = bool(request_payload.get("moderation_inline_public")) if isinstance(request_payload, dict) else False
        user = message.from_user
        if not entry or not user:
            await message.answer(t("not_found", lang), parse_mode=ParseMode.HTML)
            return True
        if not inline_public and not can_vote_in_context(user.id, message.chat.id):
            await message.answer(t("admin_denied", lang), parse_mode=ParseMode.HTML)
            return True
        entry = set_vote(
            request_id,
            int(user.id),
            user.username or "",
            user.full_name or "",
            vote,
        )
        if entry:
            await refresh_admin_notify_messages(message.bot, entry)
        await state.set_state(UserFlow.entering_moderation_vote_reason)
        await state.update_data(
            moderation_vote_request_id=request_id,
            moderation_vote_inline_message_id="",
            moderation_vote_dm=True,
        )
        await message.answer(t("moderation_vote_reason_dm_prompt", lang), parse_mode=ParseMode.HTML)
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
        sent = await answer(message, t("choose_type", lang), submit_type_kb(lang, include_update=include_update), "suggestion")
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
            sent = await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "profile")
            if sent:
                await remember_menu_owner(message, state, sent)
        else:
            sent = await answer(message, t("welcome", lang, bot=BOT_USERNAME), main_menu_kb(lang), "welcome")
            if sent:
                await remember_menu_owner(message, state, sent)
        return True

    return False


async def _render_submit_type(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
    user = cb.from_user
    include_update = False
    if user:
        try:
            include_update = bool(find_user_plugins(user.id, user.username or ""))
        except Exception:
            include_update = False
    await state.set_state(UserFlow.choosing_submission_type)
    await answer(cb, t("choose_type", lang), submit_type_kb(lang, include_update=include_update), "suggestion")


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
        "category_key": data.get("category_key"),
        "category_label": data.get("category_label"),
        "publish_not_before": data.get("publish_not_before"),
        "submission_type": "plugin",
    }

    if draft_id:
        update_request_payload(draft_id, payload)
    else:
        entry = add_draft_request(payload, request_type="new")
        await state.update_data(draft_request_id=entry.get("id"))


async def _sync_pending_plugin_request(state: FSMContext, request_id: str) -> None:
    data = await state.get_data()
    plugin = data.get("plugin", {})
    if not plugin:
        return
    payload = {
        "plugin": plugin,
        "description_ru": data.get("description_ru"),
        "description_en": data.get("description_en"),
        "usage_ru": data.get("usage_ru"),
        "usage_en": data.get("usage_en"),
        "category_key": data.get("category_key"),
        "category_label": data.get("category_label"),
        "publish_not_before": data.get("publish_not_before"),
        "submission_type": "plugin",
    }
    update_request_payload(request_id, payload)


async def _sync_pending_update_request(state: FSMContext, request_id: str) -> None:
    data = await state.get_data()
    plugin = data.get("plugin", {})
    if not plugin:
        return
    payload = {
        "plugin": plugin,
        "changelog": data.get("changelog"),
        "update_slug": data.get("update_slug"),
        "old_plugin": data.get("old_plugin"),
        "description_ru": data.get("description_ru"),
        "description_en": data.get("description_en"),
        "usage_ru": data.get("usage_ru"),
        "usage_en": data.get("usage_en"),
        "category_key": data.get("category_key"),
        "category_label": data.get("category_label"),
        "submission_type": "update",
    }
    update_request_payload(request_id, payload)


async def _notify_admins_request_updated(bot, entry: Dict[str, Any]) -> None:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    user_id = payload.get("user_id", 0)
    username = payload.get("username", "")
    request_id = entry.get("id", "?")
    user_link = user_mention(user_id, username)
    name = plugin.get("name") or plugin.get("id") or "—"
    text = t("admin_request_updated", "ru", id=request_id, name=name, user=user_link)
    try:
        await send_request_to_forum(bot, entry, text, plugin.get("file_path"))
    except Exception:
        logger.warning("event=submission.notify_forum_update.failed request_id=%s", request_id, exc_info=True)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await remember_menu_owner(message, state)
    user_id = message.from_user.id if message.from_user else None

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
        await answer(message, t("user_banned", lang))
        return

    if payload:
        if payload.startswith("plugin_"):
            payload = payload[len("plugin_") :]
        if payload.startswith("stenka_"):
            wall_id = payload[len("stenka_") :]
            await state.update_data(lang=get_lang(user_id), stenka_wall_id=wall_id)
            await state.set_state(UserFlow.entering_stenka_tag)
            await answer(message, t("stenka_prompt_enter_tag", get_lang(user_id)))
            return
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
                text = build_plugin_preview(plugin, lang)
                link = plugin.get("channel_message", {}).get("link")
                external = is_external_plugin(plugin)
                notify_all_enabled = False if external else is_subscribed(user_id, ALL_SUBSCRIPTION_KEY)
                sent = await answer(
                    message,
                    text,
                    plugin_detail_kb(
                        link,
                        back="catalog",
                        lang=lang,
                        subscribe_callback=(None if external or notify_all_enabled else f"sub:toggle:{encode_slug(plugin_slug)}:catalog"),
                    ),
                    "catalog",
                )
                if sent:
                    await remember_menu_owner(message, state, sent)
                return

        if message.from_user and (message.from_user.username or "").strip():
            user_name = f"@{message.from_user.username.strip()}"
        else:
            user_name = (message.from_user.full_name if message.from_user else "").strip() or (message.from_user.first_name if message.from_user else "").strip() or "—"
        sent = await answer(
            message,
            t("welcome", lang, bot=BOT_USERNAME, bot_name=BOT_USERNAME, user_name=user_name),
            main_menu_kb(lang),
            "welcome",
        )
        if sent:
            await try_react_pray(sent)
            await remember_menu_owner(message, state, sent)
        return

    lang = get_lang(user_id)
    await state.set_state(UserFlow.choosing_language)
    sent = await answer(message, t("language_prompt", lang), language_kb())
    if sent:
        await remember_menu_owner(message, state, sent)


@router.message(UserFlow.entering_stenka_tag)
async def on_stenka_tag_dm(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    lang = await get_language(message, state)
    data = await state.get_data()
    wall_id = str(data.get("stenka_wall_id") or "").strip()
    if not wall_id:
        await message.answer(t("stenka_err_not_found", lang), parse_mode=ParseMode.HTML)
        await state.clear()
        await state.set_state(UserFlow.idle)
        return

    raw = (message.text or "").strip()
    raw = raw.replace(" ", "")
    if raw.startswith("#"):
        raw = raw[1:]
    if not raw:
        await message.answer(t("need_text", lang), parse_mode=ParseMode.HTML)
        return
    if len(raw) > 15:
        await message.answer(t("stenka_err_tag_too_long", lang), parse_mode=ParseMode.HTML)
        return
    if not re.fullmatch(r"\w+", raw, flags=re.UNICODE):
        await message.answer(t("stenka_err_tag_format", lang), parse_mode=ParseMode.HTML)
        return
    tag = raw

    db = load_stenka()
    if not isinstance(db, dict):
        db = {}
    walls = db.get("walls") if isinstance(db.get("walls"), dict) else {}
    wall = walls.get(wall_id) if isinstance(walls, dict) else None
    if not isinstance(wall, dict):
        await message.answer(t("stenka_err_not_found", lang), parse_mode=ParseMode.HTML)
        await state.clear()
        await state.set_state(UserFlow.idle)
        return

    tags = wall.get("tags") if isinstance(wall.get("tags"), list) else []
    if any(str(t).strip() == tag for t in tags):
        await message.answer(t("stenka_err_tag_taken", lang), parse_mode=ParseMode.HTML)
        return

    users = wall.get("users") if isinstance(wall.get("users"), dict) else {}
    uid_str = str(message.from_user.id)
    if uid_str in users:
        await message.answer(t("stenka_err_already_wrote", lang, tag=users.get(uid_str, "")), parse_mode=ParseMode.HTML)
        return

    tags.append(tag)
    wall["tags"] = tags
    users[uid_str] = tag
    wall["users"] = users
    save_stenka(db)

    from bot.routers.catalog_flow import _stenka_render_text, _stenka_kb

    text = _stenka_render_text(wall_id)
    try:
        if wall.get("inline_message_id"):
            await message.bot.edit_message_text(
                inline_message_id=str(wall.get("inline_message_id")),
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=_stenka_kb(wall_id, lang=lang),
                disable_web_page_preview=True,
            )
        elif wall.get("chat_id") and wall.get("message_id"):
            await message.bot.edit_message_text(
                chat_id=int(wall.get("chat_id")),
                message_id=int(wall.get("message_id")),
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=_stenka_kb(wall_id, lang=lang),
                disable_web_page_preview=True,
            )
    except Exception:
        pass

    await message.answer(t("stenka_ok_saved", lang), parse_mode=ParseMode.HTML)
    await state.clear()
    await state.set_state(UserFlow.idle)


@router.message(Command("lang"))
async def cmd_lang(message: Message, state: FSMContext) -> None:
    if is_user_banned(message.from_user.id):
        return
    lang = get_lang(message.from_user.id if message.from_user else None)
    await state.set_state(UserFlow.choosing_language)
    await message.answer(
        t("language_prompt", lang),
        reply_markup=language_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("lang:"))
async def on_lang(cb: CallbackQuery, state: FSMContext) -> None:
    lang = cb.data.split(":")[1]
    if lang not in ("ru", "en"):
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
        plugin = find_plugin_by_slug(start_payload)
        if plugin:
            text = build_plugin_preview(plugin, lang)
            link = plugin.get("channel_message", {}).get("link")
            external = is_external_plugin(plugin)
            notify_all_enabled = False if external else (is_subscribed(cb.from_user.id, ALL_SUBSCRIPTION_KEY) if cb.from_user else False)
            await answer(
                cb,
                text,
                plugin_detail_kb(
                    link,
                    back="catalog",
                    lang=lang,
                    subscribe_callback=(None if external or notify_all_enabled else f"sub:toggle:{encode_slug(start_payload)}:catalog"),
                ),
                "catalog",
            )
            await cb.answer(t("language_saved", lang))
            return

    user_name = (cb.from_user.full_name if cb.from_user else "").strip() or (cb.from_user.first_name if cb.from_user else "").strip() or "—"
    msg = await answer(
        cb,
        t("welcome", lang, bot=BOT_USERNAME, bot_name=BOT_USERNAME, user_name=user_name),
        main_menu_kb(lang),
        "welcome",
    )
    if cb.message:
        await try_react_pray(cb.message)
    elif msg:
        await try_react_pray(msg)
    await cb.answer(t("language_saved", lang))


@router.callback_query(F.data.startswith("pendupd:"))
async def on_open_pending_update_request(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user:
        await cb.answer()
        return
    lang = await get_language(cb, state)
    request_id = cb.data.split(":", 1)[1]
    req = get_request_by_id(request_id)
    if not isinstance(req, dict):
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    if req.get("status") != "pending" or req.get("type") != "update":
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    payload = req.get("payload", {})
    if not isinstance(payload, dict) or payload.get("user_id") != cb.from_user.id:
        await cb.answer(t("admin_denied", lang), show_alert=True)
        return
    if (payload.get("submission_type") or payload.get("type")) != "update":
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    plugin = payload.get("plugin", {}) if isinstance(payload.get("plugin"), dict) else {}
    await state.clear()
    await state.update_data(
        lang=lang,
        plugin=plugin,
        changelog=payload.get("changelog", ""),
        update_slug=payload.get("update_slug", ""),
        old_plugin=payload.get("old_plugin", {}),
        description_ru=payload.get("description_ru", ""),
        description_en=payload.get("description_en", ""),
        usage_ru=payload.get("usage_ru", ""),
        usage_en=payload.get("usage_en", ""),
        category_key=payload.get("category_key", ""),
        category_label=payload.get("category_label", ""),
        draft_prefix="pendupd",
        pending_request_id=request_id,
        edit_field=None,
        edit_lang=None,
        draft_message_id=cb.message.message_id if cb.message else None,
    )
    await state.set_state(UserFlow.confirming_update)
    draft_text = _render_update_text(await state.get_data())
    await answer(
        cb,
        draft_text,
        draft_edit_kb(
            "pendupd",
            t("btn_save_changes", lang),
            include_cancel=True,
            include_checked_on=False,
            include_delete=True,
            include_file=True,
            include_back=True,
            lang=lang,
        ),
        "profile",
    )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "home")
async def on_home(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    await state.set_state(UserFlow.idle)
    if cb.from_user and (cb.from_user.username or "").strip():
        user_name = f"@{cb.from_user.username.strip()}"
    else:
        user_name = (cb.from_user.full_name if cb.from_user else "").strip() or (cb.from_user.first_name if cb.from_user else "").strip() or "—"
    msg = await answer(
        cb,
        t("welcome", lang, bot=BOT_USERNAME, bot_name=BOT_USERNAME, user_name=user_name),
        main_menu_kb(lang),
        "welcome",
    )
    if cb.message:
        await try_react_pray(cb.message)
    elif msg:
        await try_react_pray(msg)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "cancel")
async def on_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    data = await state.get_data()
    draft_id = data.get("draft_request_id")
    if draft_id:
        delete_request_and_file(draft_id)

    current_state = await state.get_state() or ""
    state_name = current_state.split(":")[-1]

    if state_name in {
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
    }:
        await _render_submit_type(cb, state, lang)
    else:
        await _render_home(cb, state, lang)

    try:
        await cb.answer(t("submission_cancelled", lang))
    except Exception:
        pass
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "submit")
async def on_submit(cb: CallbackQuery, state: FSMContext) -> None:
    if is_user_banned(cb.from_user.id):
        lang = await get_language(cb, state)
        await cb.answer(t("user_banned_short", lang), show_alert=True)
        return
    lang = await get_language(cb, state)
    user = cb.from_user
    include_update = False
    if user:
        try:
            include_update = bool(find_user_plugins(user.id, user.username or ""))
        except Exception:
            include_update = False
    await state.set_state(UserFlow.choosing_submission_type)
    await answer(cb, t("choose_type", lang), submit_type_kb(lang, include_update=include_update), "suggestion")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(UserFlow.choosing_submission_type, F.data == "submit:plugin")
async def on_submit_plugin(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    if cb.from_user:
        discard_user_drafts(cb.from_user.id)
    await state.update_data(draft_request_id=None)
    await state.set_state(UserFlow.uploading_file)
    await answer(cb, t("upload_plugin", lang), cancel_kb(lang), "plugins")
    try:
        await cb.answer()
    except Exception:
        pass


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
    await answer(cb, t("choose_plugin_to_update", lang), user_plugins_kb(plugins_list, lang), "plugins")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(UserFlow.choosing_plugin_to_update, F.data.startswith("upd:"))
async def on_choose_plugin_update(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    slug = decode_slug(cb.data.split(":")[1])
    plugin = find_plugin_by_slug(slug)

    if not plugin:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    current_version = plugin.get("ru", {}).get("version") or "?"
    await state.update_data(update_slug=slug, old_plugin=plugin, old_version=current_version)
    await state.set_state(UserFlow.uploading_update_file)
    await answer(cb, t("upload_update_file", lang, version=current_version), cancel_kb(lang), "plugins")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("pendreq:"))
async def on_open_pending_request(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user:
        await cb.answer()
        return
    lang = await get_language(cb, state)
    request_id = cb.data.split(":", 1)[1]
    req = get_request_by_id(request_id)
    if not isinstance(req, dict):
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    if req.get("status") != "pending" or req.get("type") != "new":
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    payload = req.get("payload", {})
    if not isinstance(payload, dict) or payload.get("user_id") != cb.from_user.id:
        await cb.answer(t("admin_denied", lang), show_alert=True)
        return
    if (payload.get("submission_type") or payload.get("type")) != "plugin":
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    plugin = payload.get("plugin", {}) if isinstance(payload.get("plugin"), dict) else {}
    await state.clear()
    await state.update_data(
        lang=lang,
        plugin=plugin,
        description_ru=payload.get("description_ru", ""),
        description_en=payload.get("description_en", ""),
        usage_ru=payload.get("usage_ru", ""),
        usage_en=payload.get("usage_en", ""),
        category_key=payload.get("category_key", ""),
        category_label=payload.get("category_label", ""),
        publish_not_before=payload.get("publish_not_before"),
        draft_prefix="pend",
        pending_request_id=request_id,
        edit_field=None,
        edit_lang=None,
        draft_message_id=cb.message.message_id if cb.message else None,
    )
    await state.set_state(UserFlow.confirming_submission)
    draft_text = _render_draft_text(await state.get_data())
    await answer(
        cb,
        draft_text,
        draft_edit_kb(
            "pend",
            t("btn_save_changes", lang),
            include_cancel=True,
            include_checked_on=False,
            include_delete=True,
            include_file=True,
            include_back=True,
            lang=lang,
        ),
        "profile",
    )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("profile:delete:"))
async def on_profile_delete(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    slug = decode_slug(cb.data.split(":", 2)[2])
    plugin = find_plugin_by_slug(slug)

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

    await state.update_data(
        pending_payload=payload,
        pending_request_type="delete",
        pending_reply_key="delete_sent",
    )
    await state.set_state(UserFlow.entering_admin_comment)
    await answer(cb, t("ask_admin_comment", lang), comment_skip_kb(lang), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("profile:update:"))
async def on_profile_update(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    slug = decode_slug(cb.data.split(":", 2)[2])
    plugin = find_plugin_by_slug(slug)

    if not plugin:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    current_version = plugin.get("ru", {}).get("version") or "?"
    await state.update_data(update_slug=slug, old_plugin=plugin, old_version=current_version)
    await state.set_state(UserFlow.uploading_update_file)
    await answer(cb, t("upload_update_file", lang, version=current_version), cancel_kb(lang), "plugins")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(UserFlow.uploading_update_file, F.document)
async def on_update_file(message: Message, state: FSMContext) -> None:
    if not await _ensure_not_banned(message, state):
        return
    lang = await get_language(message, state)
    data = await state.get_data()
    old_plugin = data.get("old_plugin", {})
    old_version = data.get("old_version", "")
    is_admin = message.from_user.id in get_admins_plugins() if message.from_user else False

    if message.document and message.document.file_size:
        if message.document.file_size > 8 * 1024 * 1024:
            await message.answer(t("file_too_large", lang))
            return

    try:
        plugin = await process_plugin_file(message.bot, message.document)
    except ValueError as e:
        key, _, details = str(e).partition(":")
        if key == "parse_error" and details:
            await message.answer(t("parse_error", lang, error=details))
        else:
            await message.answer(t(key, lang) if key in TEXTS else t("parse_error", lang, error=str(e)))
        return

    if not is_admin:
        is_valid, error = validate_update_submission(plugin.to_dict(), old_plugin)
        if not is_valid:
            if error == "version_not_higher":
                await message.answer(t("version_not_higher", lang, current=old_version))
            elif error == "version_lower":
                suggested = old_version
                if old_version and old_version.count(".") == 1:
                    suggested = f"{old_version}0"
                await message.answer(t("version_lower", lang, current=old_version, suggested=suggested))
            else:
                await message.answer(t(error, lang))
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
        "name": old_name or new_plugin.get("name"),
        "author": old_author or new_plugin.get("author"),
        "description": old_desc or "",
        "min_version": old_min_version or "",
        "has_ui_settings": old_has_settings,
    }

    await state.update_data(plugin=merged_plugin)
    await state.set_state(UserFlow.entering_changelog)
    await answer(message, t("enter_changelog", lang), cancel_kb(lang), "plugins")


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

    category_key = (old_plugin.get("category") if isinstance(old_plugin, dict) else "") or ""
    category_key = str(category_key).strip()
    category_label = ""
    if category_key:
        category = next((c for c in get_categories() if c.get("key") == category_key), None)
        if category:
            category_label = f"{category.get('ru', '')} / {category.get('en', '')}"

    await state.update_data(
        description_ru=old_ru.get("description", ""),
        description_en=old_en.get("description", ""),
        usage_ru=old_ru.get("usage", ""),
        usage_en=old_en.get("usage", ""),
        category_key=category_key,
        category_label=category_label,
        draft_prefix="upd",
        edit_field=None,
        edit_lang=None,
    )

    await state.set_state(UserFlow.confirming_update)
    draft_text = _render_update_text(await state.get_data())
    sent = await answer(
        message,
        draft_text,
        draft_edit_kb(
            "upd",
            t("btn_send_to_admin", lang),
            include_cancel=True,
            include_checked_on=False,
            lang=lang,
        ),
        "plugins",
    )
    if sent:
        await state.update_data(draft_message_id=sent.message_id)


@router.callback_query(UserFlow.confirming_update, F.data.regexp(r"^(upd|pendupd):"))
async def on_update_edit(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)

    prefix = cb.data.split(":", 2)[0]
    action = cb.data.split(":", 2)[1]
    if action == "edit":
        field = cb.data.split(":", 2)[2]
        if field == "file" and prefix == "pendupd":
            await state.set_state(UserFlow.uploading_pending_update_file)
            await answer(cb, t("pending_upload_update_plugin", lang), cancel_kb(lang), None)
            await cb.answer()
            return
        if field in {"description", "usage"}:
            await state.update_data(edit_field=field)
            await answer(cb, t("admin_choose_language", lang), draft_lang_kb(prefix, field, lang=lang), None)
            await cb.answer()
            return
        if field == "category":
            await state.update_data(edit_field=field)
            from bot.cache import get_categories

            await answer(cb, t("admin_choose_category", lang), draft_category_kb(prefix, get_categories(), lang=lang), None)
            await cb.answer()
            return

        prompt = {
            "name": t("admin_prompt_new_name", lang),
            "author": t("admin_prompt_author", lang),
            "settings": t("admin_prompt_has_settings", lang),
            "min_version": t("admin_prompt_min_version", lang),
        }.get(field, t("admin_prompt_value", lang))

        await state.update_data(edit_field=field)
        await state.set_state(UserFlow.editing_draft_field)
        await answer(cb, prompt, None, None)
        await cb.answer()
        return

    if action == "lang":
        _, _, field, lang_choice = cb.data.split(":")
        await state.update_data(edit_field=field, edit_lang=lang_choice)
        await state.set_state(UserFlow.editing_draft_field)

        prompt = t("admin_prompt_enter_text_ru", lang) if lang_choice == "ru" else t("admin_prompt_enter_text_en", lang)
        msg = await answer(cb, prompt, None, None)
        if msg:
            await state.update_data(draft_message_id=msg.message_id)
        await cb.answer()
        return

    if action == "cat":
        from bot.cache import get_categories

        cat_key = cb.data.split(":")[2]
        category = next((c for c in get_categories() if c.get("key") == cat_key), None)
        if category:
            await state.update_data(
                category_key=cat_key,
                category_label=f"{category.get('ru', '')} / {category.get('en', '')}",
            )

            if prefix == "pendupd":
                data = await state.get_data()
                req_id = str(data.get("pending_request_id") or "")
                if req_id:
                    await _sync_pending_update_request(state, req_id)

        await state.set_state(UserFlow.confirming_update)
        draft_text = _render_update_text(await state.get_data())
        await answer(
            cb,
            draft_text,
            draft_edit_kb(
                prefix,
                (t("btn_send_to_admin", lang) if prefix == "upd" else t("btn_save_changes", lang)),
                include_cancel=True,
                include_checked_on=False,
                include_delete=(prefix == "pendupd"),
                include_file=(prefix == "pendupd"),
                lang=lang,
            ),
            "plugins",
        )
        await cb.answer()
        return

    if action == "back":
        await state.set_state(UserFlow.confirming_update)
        draft_text = _render_update_text(await state.get_data())
        await answer(
            cb,
            draft_text,
            draft_edit_kb(
                prefix,
                (t("btn_send_to_admin", lang) if prefix == "upd" else t("btn_save_changes", lang)),
                include_cancel=True,
                include_checked_on=False,
                include_delete=(prefix == "pendupd"),
                include_file=(prefix == "pendupd"),
                lang=lang,
            ),
            "plugins",
        )
        await cb.answer()
        return

    if action == "submit":
        if prefix == "pendupd":
            data = await state.get_data()
            req_id = str(data.get("pending_request_id") or "")
            if req_id:
                await _sync_pending_update_request(state, req_id)
                entry = get_request_by_id(req_id)
                if isinstance(entry, dict):
                    asyncio.create_task(_notify_admins_request_updated(cb.bot, entry))
            await cb.answer(t("pending_saved", lang), show_alert=True)
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
            "category_key": data.get("category_key", ""),
            "submission_type": "update",
        }

        await state.update_data(
            pending_payload=payload,
            pending_request_type="update",
            pending_reply_key="update_sent",
        )
        await state.set_state(UserFlow.entering_admin_comment)
        await answer(cb, t("ask_admin_comment", lang), comment_skip_kb(lang), "plugins")
        await cb.answer()
        return

    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(UserFlow.confirming_submission, F.data == "pend:delete")
async def on_pending_delete(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("btn_confirm", lang), callback_data="pend:delete_confirm"),
                InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="pend:back"),
            ]
        ]
    )
    await answer(cb, t("pending_delete_confirm", lang), kb, None)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(UserFlow.confirming_submission, F.data == "pend:delete_confirm")
async def on_pending_delete_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    data = await state.get_data()
    req_id = str(data.get("pending_request_id") or "").strip()
    if not req_id:
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    delete_request_and_file(req_id)
    await state.clear()
    await state.set_state(UserFlow.idle)
    await answer(cb, t("pending_deleted", lang), main_menu_kb(lang), "welcome")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(UserFlow.confirming_update, F.data == "pendupd:delete")
async def on_pending_update_delete(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("btn_confirm", lang), callback_data="pendupd:delete_confirm"),
                InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="pendupd:back"),
            ]
        ]
    )
    await answer(cb, t("pending_delete_confirm", lang), kb, None)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(UserFlow.confirming_update, F.data == "pendupd:delete_confirm")
async def on_pending_update_delete_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    data = await state.get_data()
    req_id = str(data.get("pending_request_id") or "").strip()
    if not req_id:
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    delete_request_and_file(req_id)
    await state.clear()
    await state.set_state(UserFlow.idle)
    await answer(cb, t("pending_deleted", lang), main_menu_kb(lang), "welcome")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(UserFlow.uploading_pending_update_file, F.document)
async def on_pending_update_file(message: Message, state: FSMContext) -> None:
    if not await _ensure_not_banned(message, state):
        return
    lang = await get_language(message, state)
    data = await state.get_data()
    req_id = str(data.get("pending_request_id") or "").strip()
    if not req_id:
        await state.set_state(UserFlow.idle)
        await message.answer(t("not_found", lang))
        return

    existing = data.get("plugin", {})
    expected_id = (existing.get("id") or "").strip()
    old_plugin = data.get("old_plugin", {})
    old_version = data.get("old_version", "")

    if message.document and message.document.file_size:
        if message.document.file_size > 8 * 1024 * 1024:
            await message.answer(t("file_too_large", lang))
            return

    try:
        plugin = await process_plugin_file(message.bot, message.document)
    except ValueError as e:
        key, _, details = str(e).partition(":")
        if key == "parse_error" and details:
            await message.answer(t("parse_error", lang, error=details))
        else:
            await message.answer(t(key, lang) if key in TEXTS else t("parse_error", lang, error=str(e)))
        return

    new_plugin = plugin.to_dict()
    if expected_id and (new_plugin.get("id") or "").strip() != expected_id:
        await message.answer(t("pending_file_id_mismatch", lang))
        return

    is_valid, error = validate_update_submission(new_plugin, old_plugin)
    if not is_valid:
        if error == "version_not_higher":
            await message.answer(t("version_not_higher", lang, current=old_version or "—"))
        elif error == "version_lower":
            suggested = old_version or "—"
            if old_version and old_version.count(".") == 1:
                suggested = f"{old_version}0"
            await message.answer(t("version_lower", lang, current=old_version or "—", suggested=suggested))
        else:
            await message.answer(t(error, lang))
        return

    old_path = (existing.get("file_path") or "").strip()
    if old_path and old_path != new_plugin.get("file_path"):
        Path(old_path).unlink(missing_ok=True)

    merged = {
        **new_plugin,
        "id": expected_id or new_plugin.get("id"),
        "name": existing.get("name") or new_plugin.get("name"),
        "author": existing.get("author") or new_plugin.get("author"),
        "description": existing.get("description") or new_plugin.get("description"),
        "min_version": existing.get("min_version") or new_plugin.get("min_version"),
        "has_ui_settings": existing.get("has_ui_settings", new_plugin.get("has_ui_settings")),
    }

    await state.update_data(plugin=merged)
    await _sync_pending_update_request(state, req_id)
    entry = get_request_by_id(req_id)
    if isinstance(entry, dict):
        asyncio.create_task(_notify_admins_request_updated(message.bot, entry))

    await state.set_state(UserFlow.confirming_update)
    draft_text = _render_update_text(await state.get_data())
    await answer(
        message,
        draft_text,
        draft_edit_kb(
            "pendupd",
            t("btn_save_changes", lang),
            include_cancel=True,
            include_checked_on=False,
            include_delete=True,
            include_file=True,
            include_back=True,
            lang=lang,
        ),
        "profile",
    )


@router.message(UserFlow.uploading_pending_update_file)
async def on_pending_update_file_invalid(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    await message.answer(t("invalid_file", lang))


@router.message(UserFlow.uploading_file, F.document)
async def on_file(message: Message, state: FSMContext) -> None:
    if not await _ensure_not_banned(message, state):
        return
    lang = await get_language(message, state)

    await try_react_pray(message)

    if message.document and message.document.file_size:
        if message.document.file_size > 8 * 1024 * 1024:
            await message.answer(t("file_too_large", lang))
            return

    try:
        plugin = await process_plugin_file(message.bot, message.document)
    except ValueError as e:
        key, _, details = str(e).partition(":")
        if key == "parse_error" and details:
            await message.answer(t("parse_error", lang, error=details))
        else:
            await message.answer(t(key, lang) if key in TEXTS else t("parse_error", lang, error=str(e)))
        return

    is_valid, error = validate_new_submission(plugin.to_dict())
    if not is_valid:
        await message.answer(t(error, lang))
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
        description_raw=plugin.description,
    )

    if not is_valid_version(plugin_dict.get("min_version")):
        await state.set_state(UserFlow.entering_min_version)
        await message.answer(
            t("require_min_version", lang),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=cancel_kb(lang),
        )
        return

    await _continue_after_plugin_parsed(message, state, lang)


async def _continue_after_plugin_parsed(message: Message, state: FSMContext, lang: str) -> None:
    await _sync_submission_draft(state, message.from_user.id, message.from_user.username or "", "plugin")
    await state.set_state(UserFlow.choosing_description_language)

    draft_text = _render_draft_text(await state.get_data())
    await answer(message, draft_text, image=None)
    await answer(message, t("choose_description_language", lang), description_lang_kb(), None)


@router.message(UserFlow.entering_min_version)
async def on_min_version_input(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    raw = (message.text or "").strip()
    if not is_valid_version(raw):
        await message.answer(t("invalid_min_version", lang), reply_markup=cancel_kb(lang))
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
    await answer(cb, prompt, None, None)
    try:
        await cb.answer()
    except Exception:
        pass


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
    await message.answer(
        t("enter_usage_ru", lang),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=cancel_kb(lang),
    )


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
    from bot.cache import get_categories

    await answer(message, t("choose_category", lang), categories_kb(get_categories(), lang))


@router.callback_query(UserFlow.choosing_category, F.data.startswith("submit:cat:"))
async def on_category_select(cb: CallbackQuery, state: FSMContext) -> None:
    from bot.cache import get_categories

    lang = await get_language(cb, state)
    cat_key = cb.data.split(":")[2]
    category = next((c for c in get_categories() if c.get("key") == cat_key), None)

    if not category:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    cat_label = f"{category.get('ru', '')} / {category.get('en', '')}"
    await state.update_data(category_key=cat_key, category_label=cat_label)
    await _sync_submission_draft(state, cb.from_user.id, cb.from_user.username or "", "plugin")
    await state.set_state(UserFlow.confirming_submission)

    draft_text = _render_draft_text(await state.get_data())
    await state.update_data(draft_message_id=cb.message.message_id if cb.message else None)
    await answer(
        cb,
        draft_text,
        draft_edit_kb(
            "draft",
            t("btn_send_to_admin", lang),
            include_cancel=True,
            include_checked_on=False,
            lang=lang,
        ),
        "plugins",
    )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(UserFlow.confirming_submission, F.data.regexp(r"^(draft|pend):edit:"))
async def on_draft_edit(cb: CallbackQuery, state: FSMContext) -> None:
    prefix = cb.data.split(":", 2)[0]
    field = cb.data.split(":")[2]
    lang = await get_language(cb, state)

    if field == "file" and prefix == "pend":
        await state.set_state(UserFlow.uploading_pending_file)
        await answer(cb, t("pending_upload_plugin", lang), cancel_kb(lang), None)
        await cb.answer()
        return

    if field in {"description", "usage"}:
        await state.update_data(edit_field=field)
        await answer(cb, t("admin_choose_language", lang), draft_lang_kb(prefix, field, lang=lang), None)
        await cb.answer()
        return

    if field == "category":
        await state.update_data(edit_field=field)
        await answer(cb, t("admin_choose_category", lang), draft_category_kb(prefix, get_categories(), lang=lang), None)
        await cb.answer()
        return

    prompt = {
        "name": t("admin_prompt_new_name", lang),
        "author": t("admin_prompt_author", lang),
        "settings": t("admin_prompt_has_settings", lang),
        "min_version": t("admin_prompt_min_version", lang),
    }.get(field, t("admin_prompt_value", lang))

    await state.update_data(edit_field=field)
    await state.set_state(UserFlow.editing_draft_field)
    await answer(cb, prompt, None, None)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(UserFlow.confirming_submission, F.data.regexp(r"^(draft|pend):not_before$"))
async def on_draft_not_before(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    prefix = cb.data.split(":", 1)[0]
    lang = await get_language(cb, state)
    await state.update_data(draft_prefix=prefix)
    await state.set_state(UserFlow.entering_publish_not_before)
    await answer(cb, t("publish_not_before_prompt", lang), cancel_kb(lang), None)
    try:
        await cb.answer()
    except Exception:
        pass


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
    data = await state.get_data()
    prefix = str(data.get("draft_prefix") or "draft")
    if prefix == "pend":
        req_id = str(data.get("pending_request_id") or "")
        if req_id:
            await _sync_pending_plugin_request(state, req_id)
            entry = get_request_by_id(req_id)
            if isinstance(entry, dict):
                asyncio.create_task(_notify_admins_request_updated(message.bot, entry))
    else:
        await _sync_submission_draft(state, message.from_user.id, message.from_user.username or "", "plugin")

    await state.set_state(UserFlow.confirming_submission)
    draft_text = _render_draft_text(await state.get_data())
    await answer(
        message,
        draft_text,
        draft_edit_kb(
            prefix,
            (t("btn_send_to_admin", lang) if prefix != "pend" else t("btn_save_changes", lang)),
            include_cancel=True,
            include_checked_on=False,
            include_delete=(prefix == "pend"),
            include_file=(prefix == "pend"),
            lang=lang,
        ),
        "plugins",
    )
    await message.answer(t("publish_not_before_saved", lang), parse_mode=ParseMode.HTML)


@router.callback_query(UserFlow.confirming_submission, F.data.regexp(r"^(draft|pend):lang:"))
async def on_draft_language(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, field, lang_choice = cb.data.split(":")
    await state.update_data(edit_field=field, edit_lang=lang_choice)
    await state.set_state(UserFlow.editing_draft_field)

    lang = await get_language(cb, state)
    prompt = t("admin_prompt_enter_text_ru", lang) if lang_choice == "ru" else t("admin_prompt_enter_text_en", lang)

    msg = await answer(cb, prompt, None, None)
    if msg:
        await state.update_data(draft_message_id=msg.message_id)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(UserFlow.confirming_submission, F.data.regexp(r"^(draft|pend):cat:"))
async def on_draft_category(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    prefix = cb.data.split(":", 2)[0]
    cat_key = cb.data.split(":")[2]
    category = next((c for c in get_categories() if c.get("key") == cat_key), None)
    if category:
        await state.update_data(
            category_key=cat_key,
            category_label=f"{category.get('ru', '')} / {category.get('en', '')}",
        )
        if prefix == "draft":
            await _sync_submission_draft(state, cb.from_user.id, cb.from_user.username or "", "plugin")
        else:
            data = await state.get_data()
            req_id = str(data.get("pending_request_id") or "")
            if req_id:
                await _sync_pending_plugin_request(state, req_id)

    await state.set_state(UserFlow.confirming_submission)
    draft_text = _render_draft_text(await state.get_data())
    await state.update_data(draft_message_id=cb.message.message_id if cb.message else None)
    await answer(
        cb,
        draft_text,
        draft_edit_kb(
            prefix,
            (t("btn_send_to_admin", lang) if prefix == "draft" else t("btn_save_changes", lang)),
            include_cancel=True,
            include_checked_on=False,
            include_delete=(prefix == "pend"),
            include_file=(prefix == "pend"),
            lang=lang,
        ),
        "plugins",
    )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(UserFlow.confirming_submission, F.data.regexp(r"^(draft|pend):back$"))
async def on_draft_back(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    prefix = cb.data.split(":", 1)[0]
    await state.set_state(UserFlow.confirming_submission)
    draft_text = _render_draft_text(await state.get_data())
    await answer(
        cb,
        draft_text,
        draft_edit_kb(
            prefix,
            (t("btn_send_to_admin", lang) if prefix == "draft" else t("btn_save_changes", lang)),
            include_cancel=True,
            include_checked_on=False,
            lang=lang,
        ),
        "plugins",
    )
    try:
        await cb.answer()
    except Exception:
        pass


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

    if field in {"description", "usage"}:
        key = f"{field}_{edit_lang}"
        await state.update_data({key: text_html})
    elif field == "name":
        plugin = data.get("plugin", {})
        plugin["name"] = text_plain
        await state.update_data(plugin=plugin)
    elif field == "author":
        plugin = data.get("plugin", {})
        plugin["author"] = text_plain
        await state.update_data(plugin=plugin)
    elif field == "min_version":
        plugin = data.get("plugin", {})
        plugin["min_version"] = text_plain
        await state.update_data(plugin=plugin)
    elif field == "settings":
        value = text_plain.lower()
        has_settings = value in {"да", "yes", "1", "true"}
        plugin = data.get("plugin", {})
        plugin["has_ui_settings"] = has_settings
        await state.update_data(plugin=plugin)

    await state.update_data(edit_field=None, edit_lang=None)
    if prefix == "draft":
        await _sync_submission_draft(state, message.from_user.id, message.from_user.username or "", "plugin")
        await state.set_state(UserFlow.confirming_submission)
        draft_text = _render_draft_text(await state.get_data())
    elif prefix == "pend":
        data = await state.get_data()
        req_id = str(data.get("pending_request_id") or "")
        if req_id:
            await _sync_pending_plugin_request(state, req_id)
        await state.set_state(UserFlow.confirming_submission)
        draft_text = _render_draft_text(await state.get_data())
    else:
        await state.set_state(UserFlow.confirming_update)
        draft_text = _render_update_text(await state.get_data())

    data = await state.get_data()
    draft_message_id = data.get("draft_message_id")
    if draft_message_id:
        try:
            await message.bot.edit_message_text(
                draft_text,
                chat_id=message.chat.id,
                message_id=draft_message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=draft_edit_kb(
                    prefix,
                    (t("btn_send_to_admin", lang) if prefix != "pend" else t("btn_save_changes", lang)),
                    include_cancel=True,
                    include_checked_on=False,
                    include_delete=(prefix in {"pend", "pendupd"}),
                    include_file=(prefix in {"pend", "pendupd"}),
                    lang=lang,
                ),
                disable_web_page_preview=True,
            )
        except Exception:
            await answer(
                message,
                draft_text,
                draft_edit_kb(
                    prefix,
                    (t("btn_send_to_admin", lang) if prefix != "pend" else t("btn_save_changes", lang)),
                    include_cancel=True,
                    include_checked_on=False,
                    include_delete=(prefix in {"pend", "pendupd"}),
                    include_file=(prefix in {"pend", "pendupd"}),
                    lang=lang,
                ),
                "plugins",
            )
    else:
        await answer(
            message,
            draft_text,
            draft_edit_kb(
                prefix,
                (t("btn_send_to_admin", lang) if prefix != "pend" else t("btn_save_changes", lang)),
                include_cancel=True,
                include_checked_on=False,
                include_delete=(prefix in {"pend", "pendupd"}),
                include_file=(prefix in {"pend", "pendupd"}),
                lang=lang,
            ),
            "plugins",
        )


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

    existing = data.get("plugin", {})
    expected_id = (existing.get("id") or "").strip()

    if message.document and message.document.file_size:
        if message.document.file_size > 8 * 1024 * 1024:
            await message.answer(t("file_too_large", lang))
            return

    try:
        plugin = await process_plugin_file(message.bot, message.document)
    except ValueError as e:
        key, _, details = str(e).partition(":")
        if key == "parse_error" and details:
            await message.answer(t("parse_error", lang, error=details))
        else:
            await message.answer(t(key, lang) if key in TEXTS else t("parse_error", lang, error=str(e)))
        return

    new_plugin = plugin.to_dict()
    if expected_id and (new_plugin.get("id") or "").strip() != expected_id:
        await message.answer(t("pending_file_id_mismatch", lang))
        return

    old_path = (existing.get("file_path") or "").strip()
    if old_path and old_path != new_plugin.get("file_path"):
        Path(old_path).unlink(missing_ok=True)

    merged = {
        **new_plugin,
        "id": expected_id or new_plugin.get("id"),
        "name": existing.get("name") or new_plugin.get("name"),
        "author": existing.get("author") or new_plugin.get("author"),
        "description": existing.get("description") or new_plugin.get("description"),
        "min_version": existing.get("min_version") or new_plugin.get("min_version"),
        "has_ui_settings": existing.get("has_ui_settings", new_plugin.get("has_ui_settings")),
    }

    await state.update_data(plugin=merged)
    await _sync_pending_plugin_request(state, req_id)
    entry = get_request_by_id(req_id)
    if isinstance(entry, dict):
        asyncio.create_task(_notify_admins_request_updated(message.bot, entry))

    await state.set_state(UserFlow.confirming_submission)
    draft_text = _render_draft_text(await state.get_data())
    await answer(
        message,
        draft_text,
        draft_edit_kb(
            "pend",
            t("btn_save_changes", lang),
            include_cancel=True,
            include_checked_on=False,
            include_delete=True,
            include_file=True,
            include_back=True,
            lang=lang,
        ),
        "profile",
    )


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

    if prefix == "pend":
        data = await state.get_data()
        req_id = str(data.get("pending_request_id") or "")
        if req_id:
            await _sync_pending_plugin_request(state, req_id)
            entry = get_request_by_id(req_id)
            if isinstance(entry, dict):
                asyncio.create_task(_notify_admins_request_updated(cb.bot, entry))
        await cb.answer(t("pending_saved", lang), show_alert=True)
        return

    data = await state.get_data()
    user = cb.from_user

    if cb.message:
        await cb.message.answer(
            t("rules_before_submit", lang),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )

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
    )

    payload = build_submission_payload(
        user.id,
        user.username or "",
        plugin,
        data.get("description_ru", ""),
        data.get("description_en", ""),
        data.get("usage_ru", ""),
        data.get("usage_en", ""),
        data.get("category_key", ""),
        data.get("category_label", ""),
    )
    if data.get("publish_not_before"):
        payload["publish_not_before"] = data.get("publish_not_before")

    await state.update_data(
        pending_payload=payload,
        pending_request_type="new",
        pending_reply_key="submission_sent",
    )
    await state.set_state(UserFlow.entering_admin_comment)
    await answer(cb, t("ask_admin_comment", lang), comment_skip_kb(lang), "plugins")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(UserFlow.entering_admin_comment, F.data == "comment:skip")
async def on_admin_comment_skip(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    await _finalize_submission(cb, state, comment=None)


@router.message(UserFlow.entering_admin_comment)
async def on_admin_comment(message: Message, state: FSMContext) -> None:
    if not await _ensure_not_banned(message, state):
        return
    text = extract_html_text(message).strip()
    await _finalize_submission(message, state, comment=text or None)


async def _finalize_submission(
    target: Message | CallbackQuery,
    state: FSMContext,
    comment: str | None,
) -> None:
    if not await _ensure_not_banned(target, state):
        return
    data = await state.get_data()
    payload = data.get("pending_payload", {})
    request_type = data.get("pending_request_type", "new")
    reply_key = data.get("pending_reply_key", "submission_sent")
    lang = await get_language(target, state)

    if comment:
        payload["admin_comment"] = comment

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

                old_path = str(old_plugin.get("file_path") or "").strip()
                new_path = str(new_plugin.get("file_path") or "").strip()

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
                    "category_key": _prefer(keep_payload.get("category_key"), payload.get("category_key")),
                    "category_label": _prefer(keep_payload.get("category_label"), payload.get("category_label")),
                }

                if keep_id:
                    update_request_payload(str(keep_id), merged_payload)

                    if new_path and old_path and old_path != new_path:
                        try:
                            Path(old_path).unlink(missing_ok=True)
                        except Exception:
                            pass

                    for extra in existing_updates[1:]:
                        extra_id = str(extra.get("id") or "").strip()
                        if extra_id:
                            delete_request_and_file(extra_id)

                    entry = get_request_by_id(str(keep_id)) or keep
                    asyncio.create_task(notify_admins_request(target.bot, entry))

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

    asyncio.create_task(notify_admins_request(target.bot, entry))

    await state.set_state(UserFlow.idle)
    await state.update_data(draft_request_id=None)
    await answer(target, t(reply_key, lang), main_menu_kb(lang), "welcome")
    if isinstance(target, CallbackQuery):
        await target.answer()


async def notify_admins_request(bot, entry: Dict[str, Any]) -> None:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    icon = payload.get("icon", {})
    user_id = payload.get("user_id", 0)
    username = payload.get("username", "")
    is_auto_update = bool(payload.get("is_auto_update")) or (str(username).strip() == "auto_updates" and str(user_id) == "0")
    request_type = entry.get("type", "new")
    admin_comment = payload.get("admin_comment")
    submission_type = _submission_type(payload)
    request_id = entry.get("id", "?")

    user_link = user_mention(user_id, username)
    file_path = plugin.get("file_path") or icon.get("file_path")
    logger.info(
        "event=submission.notify_forum.start request_id=%s request_type=%s submission_type=%s item=%s",
        request_id,
        request_type,
        submission_type,
        _submission_name(payload, submission_type),
    )

    if request_type == "update":
        changelog = strip_blockquote_tags(telegram_html(payload.get("changelog"))) or "—"
        old_plugin = payload.get("old_plugin", {})
        old_locale = old_plugin.get("ru") or old_plugin.get("en") or {}
        old_version = old_locale.get("version") or "?"
        text = t(
            "admin_request_update",
            "ru",
            id=entry["id"],
            name=plain_html(plugin.get("name", "—")),
            old_version=plain_html(old_version),
            version=plain_html(plugin.get("version", "—")),
            min_version=plain_html(plugin.get("min_version", "—")),
            changelog=changelog,
            user=user_link,
        )
        if is_auto_update:
            text = text.rsplit("\n\n<b>От:</b>", 1)[0]
    elif request_type == "delete":
        delete_slug = payload.get("delete_slug") or plugin.get("id") or "—"
        text = t(
            "admin_request_delete",
            "ru",
            id=entry["id"],
            name=plain_html(plugin.get("name", "—")),
            slug=plain_html(delete_slug),
            user=user_link,
        )
    elif submission_type == "icon":
        text = t(
            "admin_request_icon",
            "ru",
            id=entry["id"],
            name=plain_html(icon.get("name", "—")),
            author=plain_html(icon.get("author", "—")),
            version=plain_html(icon.get("version", "—")),
            count=plain_html(icon.get("count", 0)),
            user=user_link,
        )
    else:
        draft_text = build_channel_post(entry)
        text = t(
            "admin_request_plugin",
            "ru",
            id=entry["id"],
            draft=draft_text,
            user=user_link,
        )

    if admin_comment:
        text += "\n\n" + t("admin_request_comment", "ru", comment=strip_blockquote_tags(telegram_html(admin_comment)))

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
    entry = get_request_by_id(request_id)
    if (
        not entry
        or entry.get("status") != "rework"
        or entry.get("payload", {}).get("user_id") != (cb.from_user.id if cb.from_user else None)
    ):
        await cb.answer(t("resubmit_expired", lang), show_alert=True)
        return

    update_request_status(request_id, "pending")
    update_request_payload(request_id, {"moderation_votes": {}})
    entry = get_request_by_id(request_id)
    logger.info(
        "event=submission.resubmit request_id=%s user_id=%s",
        request_id, cb.from_user.id if cb.from_user else None,
    )
    try:
        await notify_admins_request(cb.bot, entry)
    except Exception:
        logger.exception("event=submission.resubmit.notify_failed request_id=%s", request_id)

    try:
        if cb.message:
            await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await cb.message.answer(t("resubmit_done", lang), parse_mode=ParseMode.HTML)
    except Exception:
        pass
    try:
        await cb.answer()
    except Exception:
        pass
