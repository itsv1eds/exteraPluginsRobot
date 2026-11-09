import logging
import math
import re
import time
import asyncio
import html
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from bot.services.plugin_files import send_plugin_document
from core.plugin_formats import payload_extension
from bot.services import reason_templates
from bot.services.approval import (
    approval_status_text, approve_request, can_approve_request, can_edit_request,
    notify_author_approval, schedule_request,
)
from bot.languages import normalize_language, localized_value
from bot.services.reason_templates import (
    TEMPLATE_CONFIG_KEYS, TEMPLATES_LIMIT, load_reason_templates,
    template_kind, template_page, templates_allowed,
)
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import limits
from bot.cache import (
    get_admin_role,
    get_admins,
            get_admins_super,
    get_categories,
    get_config,
    invalidate,
)
from bot.constants import PAGE_SIZE
from bot.context import get_lang
from bot.callback_tokens import decode_slug
from bot.formatting import split_html, plain_html, strip_blockquote_tags, telegram_html, user_mention
from bot.helpers import ack, answer, spawn_background
from bot.menu_owner import MenuOwnerMiddleware, remember_menu_owner
from bot.services.audit import add_audit_event, audit_events_count, audit_events_page, recent_audit_events
from bot.keyboards import (
    admin_quiz_item_kb,
    admin_quiz_list_kb,
    admin_schedule_presets_kb,
    admin_actions_kb,
    admin_banned_kb,
    admin_broadcast_confirm_kb,
    admin_broadcast_kind_kb,
    admin_broadcast_progress_kb,
    admin_broadcast_history_kb,
    admin_post_confirm_kb,
    admin_config_kb,
    admin_config_admins_kb,
    admin_config_channels_kb,
    admin_config_moderation_kb,
    admin_config_other_kb,
    admin_confirm_ban_kb,
    admin_confirm_ban_user_kb,
    admin_post_section_kb,
    admin_updates_list_kb,
    admin_manage_admins_kb,
    admin_maintenance_kb,
    admin_maint_confirm_kb,
    admin_menu_kb,
    admin_vote_settings_kb,
    admin_vote_templates_kb,
    admin_stats_kb,
    moderation_person_kb,
    moderation_week_kb,
    admin_notification_settings_kb,
    admin_plugins_list_kb,
    admin_appeal_decision_kb,
    admin_audit_log_kb,
    admin_blocklist_kb,
    admin_rejected_kb,
    admin_rejected_detail_kb,
    admin_rejected_appeals_kb,
    admin_rejected_appeal_detail_kb,
    admin_plugins_section_kb,
    admin_queue_kb,
    admin_review_kb,
    admin_reject_kb,
    admin_reject_templates_kb,
    admin_reject_templates_cfg_kb,
    admin_confirm_delete_plugin_kb,
    admin_cancel_kb,
    draft_category_kb,
    draft_edit_kb,
    draft_lang_kb,
    icon_draft_edit_kb,
    admin_scheduled_list_kb,
    admin_schedule_controls,
    admin_scheduled_post_kb,
    admin_scheduled_posts_list_kb,
)
from bot.services.request_context import (
    forum_request_text_and_file as _forum_request_text_and_file,
    render_request_draft as _render_request_draft,
)
from bot.services.publish import (
    add_submitter_to_plugin,
    add_submitter_to_iconpack,
    add_to_catalog,
    add_icon_to_catalog,
    build_channel_post,
    build_icon_channel_post,
    remove_user_content,
    clear_updated_plugins,
    update_catalog_entry,
)
from bot.services.admin_notifications import (
    NOTIFICATION_PREF_LABEL_KEYS,
    admin_notification_preferences,
    finalize_admin_notify_messages,
    set_admin_notification_preference,
)
from bot.services.dialogs import register_dialog_message, has_dialog_media, send_dialog_message
from bot.services.forum import answer_in_moderation_topic
from bot.services.moderation import (
    author_reply_kwargs,
    can_accept_vote,
    delete_forum_request_message,
    is_moderation_forum_chat,
    moderation_config,
    rejection_reasons,
    send_request_to_forum,
    previous_rounds_text,
    vote_counts,
    vote_summary,
    with_author_vote_summary,
    request_image_key,
)
from bot.services.request_publication import (
    notify_plugin_authors_removed, plugin_display_name, publish_request, remove_published_plugin,
    validate_publication as _validate_request_before_publish,
)
from bot.services.submission import prepare_catalog_categories, process_plugin_file
from core.categories import CATEGORY_KEYS, MAX_CATEGORIES, categories_for, category_fields
from bot.states import AdminFlow
from bot.icons import emoji_html
from bot.texts import TEXTS, t
from core.catalog import find_icon_by_slug, find_plugin_by_slug, list_published_icons, list_published_plugins
from core.request_store import (
    acquire_request_lock as _acquire_request_lock,
    release_request_lock as _release_request_lock,
    cleanup_hidden_requests,
    delete_request,
    delete_requests_by_plugin_id,
    get_request_by_callback_token,
    get_request_by_id,
    get_all_requests,
    get_history_summaries,
    get_requests,
    request_callback_token,
    update_request_payload,
    update_request_status,
)
from core.storage import ADMIN_SQLITE_PATH, CATALOG_SQLITE_PATH, DATA_DIR, QUIZ_OPTIONS_PER_QUESTION, QUIZ_QUESTIONS_PER_RUN, add_quiz_question, delete_quiz_question, load_config, load_plugins, load_quiz_questions, restore_quiz_defaults, save_config, save_plugins, update_quiz_question
from core.user_store import ban_user, get_banned_users, hidden_moderation_templates, is_broadcast_enabled, list_users, moderation_preferences, set_moderation_preference, set_moderation_template_hidden, unban_user
from core.catalog import invalidate_catalog_cache

DEFAULT_APPROVE_TEMPLATES = reason_templates.DEFAULT_APPROVE_TEMPLATES
DEFAULT_REJECT_TEMPLATES = reason_templates.DEFAULT_REJECT_TEMPLATES
DEFAULT_UPDATE_APPROVE_TEMPLATES = reason_templates.DEFAULT_UPDATE_APPROVE_TEMPLATES
DEFAULT_UPDATE_REJECT_TEMPLATES = reason_templates.DEFAULT_UPDATE_REJECT_TEMPLATES

logger = logging.getLogger(__name__)


async def _open_admin_category_picker(cb: CallbackQuery, state: FSMContext, prefix: str, payload: dict) -> None:
    data = await state.get_data()
    selected = categories_for(payload) or categories_for(payload.get("old_plugin") or {})
    picker = {
        "prefix": prefix,
        "keys": selected,
        "context": data.get("current_request" if prefix == "adm" else "edit_slug"),
        "state": await state.get_state(),
    }
    await state.update_data(category_selection=picker, edit_field="category", **category_fields(selected))
    sent = await answer(cb, _tr(cb, "admin_choose_category"), draft_category_kb(prefix, get_categories(), _lang_for(cb), selected), None)
    picker["message_id"] = sent.message_id if sent else None
    picker["chat_id"] = sent.chat.id if sent else None
    await state.update_data(category_selection=picker)


async def _admin_category_picker_valid(cb: CallbackQuery, state: FSMContext, prefix: str) -> bool:
    allowed = _is_super_admin(cb) if prefix == "adm" else _ensure_admin_role(cb, "plugins")
    if not allowed:
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return False
    data = await state.get_data()
    picker = data.get("category_selection") or {}
    context = data.get("current_request" if prefix == "adm" else "edit_slug")
    valid = (
        context
        and picker.get("context") == context
        and picker.get("prefix") == prefix
        and data.get("edit_field") == "category"
        and picker.get("state") == await state.get_state()
        and cb.message
        and picker.get("message_id") == cb.message.message_id
        and picker.get("chat_id") == cb.message.chat.id
    )
    if valid and prefix == "adm":
        entry = get_request_by_id(context)
        valid = bool(entry and entry.get("type") in {"new", "update"}
                     and entry.get("status") in {"draft", "pending", "rework", "approved", "scheduled", "error"}
                     and not entry.get("payload", {}).get("icon") and _ensure_request_role(cb, entry))
    if valid and prefix == "adm_edit":
        valid = bool(data.get("edit_payload") and find_plugin_by_slug(context))
    if not valid:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
    return bool(valid)


async def _choose_admin_categories(cb: CallbackQuery, state: FSMContext, prefix: str) -> bool:
    if not await _admin_category_picker_valid(cb, state, prefix):
        return False
    data = await state.get_data()
    picker = dict(data["category_selection"])
    selected = categories_for(picker.get("keys"))
    action = (cb.data or "").removeprefix(f"{prefix}:cat:")
    if action == "done":
        if not selected:
            await cb.answer(_tr(cb, "categories_required"), show_alert=True)
            return False
        fields = category_fields(selected)
        if prefix == "adm":
            entry = get_request_by_id(data["current_request"])
            plugin = dict(entry.get("payload", {}).get("plugin") or {})
            plugin["categories"] = selected
            update_request_payload(data["current_request"], {**fields, "plugin": plugin})
        else:
            payload = dict(data["edit_payload"])
            plugin = dict(payload.get("plugin") or {})
            plugin["categories"] = selected
            payload.update(fields, plugin=plugin)
            await state.update_data(edit_payload=payload)
        await state.update_data(**fields, category_selection=None, edit_field=None)
        return True
    if action not in CATEGORY_KEYS:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return False
    if action in selected:
        selected.remove(action)
    elif len(selected) >= MAX_CATEGORIES:
        await cb.answer(_tr(cb, "categories_limit"), show_alert=True)
        return False
    else:
        selected.append(action)
    picker["keys"] = selected
    await state.update_data(category_selection=picker)
    await cb.message.edit_reply_markup(reply_markup=draft_category_kb(prefix, get_categories(), _lang_for(cb), selected))
    await ack(cb)
    return False


TZ_UTC_PLUS_5 = timezone(timedelta(hours=5))


_SCHEDULE_FORMAT = "%d.%m.%Y %H:%M"


def _parse_local_schedule(text: str) -> Optional[datetime]:
    try:
        return datetime.strptime((text or "").strip(), _SCHEDULE_FORMAT).replace(tzinfo=TZ_UTC_PLUS_5)
    except ValueError:
        return None


def _get_schedule_presets_local_str() -> list[str]:
    raw = get_config().get("schedule_presets")
    return [str(item) for item in raw if str(item).strip()] if isinstance(raw, list) else []


def _cleanup_schedule_presets() -> list[str]:
    presets = _get_schedule_presets_local_str()
    now_local = datetime.now(tz=TZ_UTC_PLUS_5)
    alive = [p for p in presets if (_parse_local_schedule(p) or now_local) > now_local]
    if alive != presets:
        cfg = get_config()
        cfg["schedule_presets"] = alive
        save_config(cfg)
        invalidate("config")
    return alive


def _preset_at(index: int) -> Optional[datetime]:
    presets = _cleanup_schedule_presets()
    if index < 0 or index >= len(presets):
        return None
    moment = _parse_local_schedule(presets[index])
    if not moment or moment <= datetime.now(tz=TZ_UTC_PLUS_5):
        return None
    return moment


def _schedule_prompt_kb(select_prefix: str, add_callback: str, lang: str):
    presets = _cleanup_schedule_presets()
    if not presets:
        return admin_cancel_kb(lang)
    return admin_schedule_presets_kb(presets, select_prefix, add_callback, lang=lang)


def _render_schedule_prompt_text(target) -> str:
    return f"{_tr(target, 'admin_post_schedule_prompt')}\n\n{_tr(target, 'admin_schedule_presets_title')}"
router = Router(name="admin-flow")
router.callback_query.middleware(MenuOwnerMiddleware())

_NAV_STACK_KEY = "nav_stack"
_scheduled_posts_cleanup_task: Optional[asyncio.Task] = None
_scheduled_posts_cleanup_interval_seconds = 30


def _lang_for(target: CallbackQuery | Message | int | None) -> str:
    if isinstance(target, int):
        return get_lang(target)
    user = target.from_user if target else None
    return get_lang(user.id if user else None)


def _tr(target: CallbackQuery | Message | int | None, key: str, **kwargs) -> str:
    return t(key, _lang_for(target), **kwargs)


def _localized_block(entry: dict | None, lang: str) -> dict:
    block = localized_value(entry, lang, {})
    return block if isinstance(block, dict) else {}


def _localized_name(entry: dict | None, lang: str) -> str:
    if not isinstance(entry, dict):
        return ""
    return _localized_block(entry, lang).get("name") or entry.get("name") or entry.get("slug") or ""


def _localized_author(authors: dict | None, lang: str) -> str:
    return localized_value(authors, lang, "")


def _checked_on_value_now() -> str:
    cfg = get_config()
    template = str(cfg.get("checked_on_version") or "").strip()
    date_str = datetime.now(tz=TZ_UTC_PLUS_5).strftime("%d.%m.%y")
    if template:
        return f"{template} ({date_str})"
    return date_str


def _match_plugin_id(entry: dict, target: str) -> bool:
    t = (target or "").strip().lower()
    if not t:
        return False
    slug = str(entry.get("slug") or "").strip().lower()
    ru_id = str((entry.get("ru") or {}).get("id") or "").strip().lower()
    en_id = str((entry.get("en") or {}).get("id") or "").strip().lower()
    return t in {slug, ru_id, en_id}


def _scheduled_posts_cfg_key() -> str:
    return "scheduled_posts"


def _get_scheduled_posts() -> list[dict]:
    cfg = get_config()
    raw = cfg.get(_scheduled_posts_cfg_key()) or []
    return raw if isinstance(raw, list) else []


def _save_scheduled_posts(items: list[dict]) -> None:
    cfg = get_config()
    cfg[_scheduled_posts_cfg_key()] = items
    save_config(cfg)
    invalidate("config")


def _parse_dt_utc(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _scheduled_dt_utc(entry: dict | None) -> datetime | None:
    if not isinstance(entry, dict):
        return None
    payload = entry.get("payload", {})
    if not isinstance(payload, dict):
        return None
    return _parse_dt_utc(payload.get("scheduled_at"))


def _publish_not_before_dt_utc(entry: dict | None) -> datetime | None:
    if not isinstance(entry, dict):
        return None
    payload = entry.get("payload", {})
    if not isinstance(payload, dict):
        return None
    return _parse_dt_utc(payload.get("publish_not_before"))


def _comment_media_of_entry(entry: dict) -> list:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    if not isinstance(payload, dict):
        return []
    return [m for m in (payload.get("comment_media") or []) if isinstance(m, dict) and m.get("file_id")]


async def _send_review_document(cb: CallbackQuery, entry: dict, reply_message) -> None:
    if not cb.message:
        return
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    plugin = dict(payload.get("plugin", {})) if isinstance(payload.get("plugin"), dict) else {}
    stored_file_id = str(payload.get("moderation_file_id") or plugin.get("file_id") or "").strip()
    if not stored_file_id and not Path(plugin.get("file_path") or "").is_file():
        return
    send_kwargs = {
        "disable_notification": True,
        "reply_to_message_id": reply_message.message_id if reply_message else cb.message.message_id,
        "allow_sending_without_reply": True,
    }
    try:
        if stored_file_id:
            try:
                await cb.bot.send_document(cb.message.chat.id, document=stored_file_id, **send_kwargs)
                return
            except TelegramBadRequest as exc:
                if not any(marker in str(exc).lower() for marker in ("wrong file", "file_id", "file identifier", "file reference")):
                    raise
                if not Path(plugin.get("file_path") or "").is_file():
                    raise
                plugin["file_id"] = None
        sent = await send_plugin_document(cb.bot, cb.message.chat.id, plugin, **send_kwargs)
        file_id = getattr(getattr(sent, "document", None), "file_id", None)
        current = get_request_by_id(str(entry.get("id") or ""))
        current_payload = (current.get("payload") or {}) if current else {}
        current_plugin = current_payload.get("plugin") or {}
        if isinstance(file_id, str) and file_id and current_plugin.get("file_path") == plugin.get("file_path"):
            update_request_payload(str(entry["id"]), {"moderation_file_id": file_id})
    except Exception:
        logger.exception("event=admin.review_file_failed request_id=%s", entry.get("id"))


def _author_comment_block(entry: dict, lang: str = "ru") -> str:
    from bot.services.request_context import author_context

    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    return author_context(payload, lang=lang) if isinstance(payload, dict) else ""


def _review_meta_block(entry: dict, lang: str = "ru") -> str:
    parts: list[str] = []
    approval = approval_status_text(entry, lang)
    if approval:
        parts.append(approval)
    not_before = _publish_not_before_dt_utc(entry)
    if not_before:
        dt_str = not_before.astimezone(TZ_UTC_PLUS_5).strftime("%d.%m.%Y %H:%M")
        parts.append(t("review_not_before", lang, date=f"{dt_str} UTC+5"))
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    if isinstance(payload, dict):
        last_error = str(payload.get("last_publish_error") or "").strip()
        if last_error:
            error_at = _parse_dt_utc(payload.get("last_publish_error_at"))
            suffix = ""
            if error_at:
                suffix = f" ({error_at.astimezone(TZ_UTC_PLUS_5).strftime('%d.%m.%Y %H:%M')} UTC+5)"
            parts.append(t("review_last_error", lang, suffix=suffix, error=html.escape(last_error)))
    if isinstance(payload, dict) and payload.get("resubmitted_after_rework"):
        parts.append(t("review_after_rework", lang))
    parts.append(vote_summary(entry, lang=lang))
    prev = previous_rounds_text(entry, lang=lang)
    if prev:
        parts.append(prev)
    return "\n\n".join(parts)


async def _render_scheduled_list(target: CallbackQuery | Message, state: FSMContext, page: int) -> None:
    lang = _lang_for(target)
    requests = list(get_requests(status="scheduled"))

    filtered: list[dict] = []
    for r in requests:
        payload = (r.get("payload") or {}) if isinstance(r, dict) else {}
        if payload.get("submission_type") == "icon" or payload.get("icon"):
            continue
        filtered.append(r)

    if not filtered:
        await answer(target, _tr(target, "admin_scheduled_empty"), admin_plugins_section_kb(lang=lang, role=_admin_menu_role(target)), "admin")
        return

    sortable = [(r, _scheduled_dt_utc(r)) for r in filtered]
    sortable.sort(key=lambda x: x[1] or datetime.max.replace(tzinfo=timezone.utc))
    sorted_requests = [r for r, _ in sortable]

    total = len(sorted_requests)
    total_pages = math.ceil(total / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = sorted_requests[start : start + PAGE_SIZE]

    items: List[tuple[str, str] | tuple[str, str, str | None]] = []
    for r in page_items:
        payload = r.get("payload", {})
        plugin = payload.get("plugin", {}) if isinstance(payload.get("plugin"), dict) else {}
        name = (plugin.get("name") if isinstance(plugin, dict) else None) or r.get("id")
        if isinstance(payload, dict) and payload.get("last_publish_error"):
            name = f"Ошибка: {name}"
        dt = _scheduled_dt_utc(r)
        dt_str = dt.astimezone(TZ_UTC_PLUS_5).strftime("%d.%m.%Y %H:%M") if dt else "—"
        items.append((f"{dt_str} — {name}", str(r.get("id"))))

    caption = f"{_tr(target, 'admin_scheduled_title')}\n{_tr(target, 'admin_page', current=page + 1, total=total_pages)}"
    await state.update_data(scheduled_list_page=page)
    await state.set_state(AdminFlow.menu)
    await answer(target, caption, admin_scheduled_list_kb(items, page, total_pages, back_callback="adm:section:plugins", lang=lang), "admin")


async def _render_scheduled_view(target: CallbackQuery | Message, state: FSMContext, request_id: str) -> None:
    await _render_review(target, state, request_id)


def _scheduled_post_includes_updated_plugins(item: dict) -> bool:
    if item.get("includes_updated_plugins") is True:
        return True

    text = str(item.get("text") or "")
    if not text:
        return False

    titles = TEXTS.get("admin_updated_block_title", {})
    if not isinstance(titles, dict):
        return False
    return any(str(title or "").strip() and str(title).strip() in text for title in titles.values())


def _count_future_scheduled_posts() -> int:
    now = datetime.now(timezone.utc)
    count = 0
    for it in _get_scheduled_posts():
        if not isinstance(it, dict):
            continue
        dt = _parse_dt_utc(it.get("scheduled_at"))
        if dt and dt > now:
            count += 1
    return count


def _cleanup_scheduled_posts() -> list[dict]:
    now = datetime.now(timezone.utc)
    kept: list[dict] = []
    released_with_updates = False
    for it in _get_scheduled_posts():
        if not isinstance(it, dict):
            continue
        dt = _parse_dt_utc(it.get("scheduled_at"))
        if not dt:
            continue
        if dt > now:
            kept.append(it)
        elif _scheduled_post_includes_updated_plugins(it):
            released_with_updates = True
    if kept != _get_scheduled_posts():
        _save_scheduled_posts(kept)
    if released_with_updates:
        clear_updated_plugins()
    return kept


async def _scheduled_posts_cleanup_loop() -> None:
    while True:
        await asyncio.sleep(_scheduled_posts_cleanup_interval_seconds)
        try:
            _cleanup_scheduled_posts()
        except Exception:
            logger.exception("Scheduled posts cleanup error")


def start_scheduled_posts_cleanup_worker() -> None:
    global _scheduled_posts_cleanup_task
    if _scheduled_posts_cleanup_task and not _scheduled_posts_cleanup_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _cleanup_scheduled_posts()
    _scheduled_posts_cleanup_task = loop.create_task(_scheduled_posts_cleanup_loop())


async def stop_scheduled_posts_cleanup_worker() -> None:
    global _scheduled_posts_cleanup_task
    task = _scheduled_posts_cleanup_task
    _scheduled_posts_cleanup_task = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _format_scheduled_post_label(it: dict) -> str:
    dt = _parse_dt_utc(it.get("scheduled_at"))
    dt_part = ""
    if dt:
        dt_part = dt.astimezone(TZ_UTC_PLUS_5).strftime("%d.%m.%Y %H:%M")
    text = str(it.get("text") or "").strip().replace("\n", " ")
    preview = (text[:40] + "…") if len(text) > 40 else text
    return f"{dt_part} — {preview}" if dt_part else preview


def _find_scheduled_post(post_id: str) -> dict | None:
    for it in _cleanup_scheduled_posts():
        if str(it.get("id")) == str(post_id):
            return it
    return None


def _upsert_scheduled_post(item: dict) -> None:
    items = _cleanup_scheduled_posts()
    pid = str(item.get("id"))
    out: list[dict] = []
    replaced = False
    for it in items:
        if str(it.get("id")) == pid:
            out.append(item)
            replaced = True
        else:
            out.append(it)
    if not replaced:
        out.append(item)
    _save_scheduled_posts(out)


def _delete_scheduled_post(post_id: str) -> None:
    items = [it for it in _cleanup_scheduled_posts() if str(it.get("id")) != str(post_id)]
    _save_scheduled_posts(items)


async def _render_scheduled_posts_list(target: CallbackQuery | Message, state: FSMContext, page: int) -> None:
    lang = _lang_for(target)
    items = _cleanup_scheduled_posts()
    if not items:
        await answer(target, _tr(target, "admin_scheduled_posts_empty"), admin_post_section_kb(lang=lang), "admin")
        return

    sortable = [(it, _parse_dt_utc(it.get("scheduled_at"))) for it in items]
    sortable.sort(key=lambda x: x[1] or datetime.max.replace(tzinfo=timezone.utc))
    items_sorted = [it for it, _ in sortable]

    total = len(items_sorted)
    total_pages = math.ceil(total / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = items_sorted[start : start + PAGE_SIZE]

    kb_items: List[tuple[str, str]] = []
    for it in page_items:
        kb_items.append((_format_scheduled_post_label(it), str(it.get("id"))))

    caption = f"{_tr(target, 'admin_scheduled_posts_title')}\n{_tr(target, 'admin_page', current=page + 1, total=total_pages)}"
    await state.update_data(scheduled_posts_list_page=page)
    await state.set_state(AdminFlow.menu)
    await answer(target, caption, admin_scheduled_posts_list_kb(kb_items, page, total_pages, lang=lang), "admin")


async def _render_scheduled_post_view(target: CallbackQuery | Message, state: FSMContext, post_id: str) -> None:
    lang = _lang_for(target)
    it = _find_scheduled_post(post_id)
    if not it:
        await answer(target, _tr(target, "admin_scheduled_posts_empty"), admin_post_section_kb(lang=lang), "admin")
        return

    dt = _parse_dt_utc(it.get("scheduled_at"))
    dt_local = dt.astimezone(TZ_UTC_PLUS_5).strftime("%d.%m.%Y %H:%M") if dt else "—"
    link = str(it.get("link") or "")
    text = str(it.get("text") or "")

    body = f"{text}\n\n<b>Отложено на:</b> <code>{dt_local}</code>"
    if link:
        body += f"\n\n{link}"

    await state.update_data(scheduled_post_current_id=str(post_id))
    await answer(target, body, admin_scheduled_post_kb(str(post_id), lang=lang), "admin")


_PAGINATED_TOKEN_RE = re.compile(
    r"^(adm:(?:queue:[a-z_]+|vtemplates:[a-z_]+|updates|scheduled|scheduled_posts|banned|broadcast_history|quiz|auditlog|rejapp|audit(?::[a-z]+)?)):\d+$"
)


def _nav_family(token: str) -> str:
    if token.startswith("adm:audit:"):
        return "adm:audit"
    if token.startswith("adm:modstats:") and not token.startswith("adm:modstats:user:"):
        return "adm:modstats"
    match = _PAGINATED_TOKEN_RE.match(token or "")
    return match.group(1) if match else token


_TRANSIENT_TOKEN_PREFIXES = (
    "adm:reject_comment:", "adm:reject_silent:", "adm:rework:", "adm:msgauthor:",
    "adm:edit:", "adm_edit:edit:", "adm_icon:edit:", "adm:ban_manual",
    "adm:backup:recipients",
    "adm:broadcast:all", "adm:broadcast:moderation",
)


_TRANSIENT_ADMIN_STATES = {
    "AdminFlow:entering_reject_comment", "AdminFlow:entering_rework_comment",
    "AdminFlow:entering_author_message", "AdminFlow:entering_ban_user",
    "AdminFlow:entering_backup_recipient", "AdminFlow:entering_reject_template",
    "AdminFlow:editing_draft_field", "AdminFlow:editing_icon_field",
    "AdminFlow:editing_config", "AdminFlow:editing_catalog_field",
    "AdminFlow:linking_author_user", "AdminFlow:uploading_catalog_file",
    "AdminFlow:adding_schedule_preset", "AdminFlow:editing_scheduled_time",
    "AdminFlow:scheduled_post_edit_text",
    "AdminFlow:entering_broadcast",
    "AdminFlow:confirming_broadcast", "AdminFlow:editing_quiz_text",
    "AdminFlow:editing_quiz_options", "AdminFlow:adding_quiz_question",
    "AdminFlow:uploading_request_file", "AdminFlow:entering_catalog_delete_reason",
    "AdminFlow:entering_post", "AdminFlow:confirming_post", "AdminFlow:scheduling_post",
    "AdminFlow:scheduling_plugin", "AdminFlow:scheduled_post_edit_time",
    "AdminFlow:entering_sync_version", "AdminFlow:entering_sync_catalog", "AdminFlow:entering_erase_id",
}


def _is_transient_token(token: str) -> bool:
    return str(token or "").startswith(_TRANSIENT_TOKEN_PREFIXES)


async def _nav_push(state: FSMContext, token: str) -> None:
    if _is_transient_token(token):
        return
    data = await state.get_data()
    stack = data.get(_NAV_STACK_KEY)
    if not isinstance(stack, list):
        stack = []
    if not stack or stack[0] != "adm:menu":
        stack = ["adm:menu"] + [s for s in stack if s != "adm:menu"]
    if stack and stack[-1] == token:
        return

    family = _nav_family(token)
    if stack and _nav_family(stack[-1]) == family:
        stack[-1] = token
    elif family in [_nav_family(s) for s in stack]:
        cut = max(i for i, s in enumerate(stack) if _nav_family(s) == family)
        stack = stack[:cut]
        stack.append(token)
    else:
        stack.append(token)

    if len(stack) > 30:
        stack = ["adm:menu"] + stack[-29:]
    await state.update_data(**{_NAV_STACK_KEY: stack})


async def _nav_prev(state: FSMContext) -> Optional[str]:
    data = await state.get_data()
    stack = data.get(_NAV_STACK_KEY)
    if not isinstance(stack, list) or not stack:
        return None
    if len(stack) == 1:
        return stack[0]
    stack.pop()
    prev = stack[-1] if stack else None
    await state.update_data(**{_NAV_STACK_KEY: stack})
    return prev


async def _reset_menu_navigation(state: FSMContext) -> None:
    await state.set_state(AdminFlow.menu)
    await state.update_data(**{_NAV_STACK_KEY: ["adm:menu"]})


async def _render_menu(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    await _reset_menu_navigation(state)
    await answer(cb, _admin_menu_text(lang), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")


@router.callback_query(F.data == "adm:menu")
async def on_admin_menu(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _render_menu(cb, state)
    await ack(cb)


def _is_resubmit_appeal(entry: dict) -> bool:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    return bool(isinstance(payload, dict) and payload.get("is_appeal"))


def _is_appeal(entry: dict) -> bool:
    return isinstance(entry, dict) and entry.get("type") == "unban_appeal"


def _admin_request_counts() -> dict[str, int]:
    pending = [r for r in (list(get_requests(status="pending")) + list(get_requests(status="error"))) if not _is_appeal(r)]
    scheduled = list(get_requests(status="scheduled"))

    def is_icon(entry: dict) -> bool:
        payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
        return bool(isinstance(payload, dict) and (payload.get("submission_type") == "icon" or payload.get("icon")))

    plugin_pending = [r for r in pending if not is_icon(r)]
    icon_pending = [r for r in pending if is_icon(r)]
    updates = [r for r in plugin_pending if r.get("type") == "update"]
    new_plugins = [r for r in plugin_pending if r.get("type") in {None, "new"}]
    deletes = [r for r in plugin_pending if r.get("type") == "delete"]
    scheduled_plugins = [r for r in scheduled if not is_icon(r)]
    return {
        "new_plugins": len(new_plugins),
        "updates": len(updates),
        "deletes": len(deletes),
        "plugin_pending": len(plugin_pending),
        "icon_pending": len(icon_pending),
        "scheduled_plugins": len(scheduled_plugins),
        "approved_plugins": len([r for r in get_requests(status="approved") if not is_icon(r) and not _is_appeal(r)]),
        "scheduled_posts": _count_future_scheduled_posts(),
    }


def _admin_menu_text(lang: str) -> str:
    return t("admin_menu_counts", lang, **_admin_request_counts())


def _plugins_section_text(lang: str) -> str:
    return t("admin_section_plugins", lang) + "\n\n" + t("admin_plugins_counts", lang, **_admin_request_counts())


def _post_section_text(lang: str) -> str:
    return f"<b>{t('admin_btn_post', lang)}</b>\n\n" + t("admin_posts_counts", lang, **_admin_request_counts())


async def _render_config(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    await state.set_state(AdminFlow.menu)
    await answer(cb, _tr(cb, "admin_settings_title"), admin_config_kb(lang=lang, role=_admin_menu_role(cb)), "settings")


_ARCHIVE_CHANNEL_FIELDS = {"archive.channel_id", "archive.technical_channel_id"}

_CONFIG_FIELD_LABEL_KEYS: dict[str, str] = {
    "archive.channel_id": "admin_cfg_archive_channel",
    "archive.technical_channel_id": "admin_cfg_archive_technical_channel",
    "channel.id": "admin_cfg_channel_id",
    "channel.username": "admin_cfg_channel_username",
    "channel.title": "admin_cfg_channel_title",
    "publish_channel": "admin_cfg_publish_channel",
    "channel.default_tags": "admin_cfg_channel_default_tags",
    "channel.locale_order": "admin_cfg_channel_locale_order",
    "icons_channel.id": "admin_cfg_icons_channel_id",
    "icons_channel.username": "admin_cfg_icons_channel_username",
    "icons_channel.title": "admin_cfg_icons_channel_title",
    "icons_channel.default_tags": "admin_cfg_icons_channel_default_tags",
    "icons_channel.locale_order": "admin_cfg_icons_channel_locale_order",
    "moderation.forum_chat_id": "admin_cfg_moderation_forum_chat_id",
    "moderation.forum_topic_id": "admin_cfg_moderation_forum_topic_id",
    "moderation.vote_threshold": "admin_cfg_moderation_vote_threshold",
    "moderation.notification_chat_ids": "admin_cfg_moderation_notification_chat_ids",
    "moderation.delete_review_notifications_on_decision": "admin_cfg_moderation_delete_review_notifications_on_decision",
    "moderation.min_supported_version": "admin_cfg_moderation_min_supported_version",
    "moderation.require_vote_reason": "admin_cfg_moderation_require_vote_reason",
    "moderation.send_reasons_to_author": "admin_cfg_moderation_send_reasons_to_author",
}

_CONFIG_VERSION_FIELDS = {
    "moderation.min_supported_version",
}

_CONFIG_INT_FIELDS = {
    *_ARCHIVE_CHANNEL_FIELDS,
    "channel.id",
    "icons_channel.id",
    "moderation.forum_chat_id",
    "moderation.forum_topic_id",
    "moderation.vote_threshold",
}

_CONFIG_LIST_FIELDS = {
    "channel.default_tags",
    "channel.locale_order",
    "icons_channel.default_tags",
    "icons_channel.locale_order",
    "moderation.notification_chat_ids",
}

_CONFIG_BOOL_FIELDS = {
    "moderation.delete_review_notifications_on_decision",
    "moderation.require_vote_reason",
    "moderation.send_reasons_to_author",
}


def _config_get_path(config: dict[str, Any], path: str) -> Any:
    cur: Any = config
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _config_current_value(config: dict[str, Any], path: str) -> Any:
    value = _config_get_path(config, path)
    if value is not None:
        return value
    if path == "moderation.send_reasons_to_author":
        from bot.services.moderation import send_reasons_to_author_default

        return send_reasons_to_author_default()
    if path == "moderation.require_vote_reason":
        from bot.services.moderation import require_vote_reason

        return require_vote_reason()
    if path == "moderation.min_supported_version":
        from bot.services.versioning import get_min_supported_version

        return get_min_supported_version()
    if path.startswith("moderation."):
        moderation = moderation_config()
        return {
            "moderation.forum_chat_id": moderation.get("chat_id"),
            "moderation.forum_topic_id": moderation.get("topic_id"),
            "moderation.vote_threshold": moderation.get("threshold"),
            "moderation.notification_chat_ids": [],
            "moderation.delete_review_notifications_on_decision": False,
        }.get(path)
    return value


def _config_set_path(config: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: dict[str, Any] = config
    for part in parts[:-1]:
        child = cur.get(part)
        if not isinstance(child, dict):
            child = {}
            cur[part] = child
        cur = child
    cur[parts[-1]] = value


def _format_config_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(x) for x in value) if value else "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None or value == "":
        return "—"
    return str(value)


def _parse_config_value(path: str, raw: str) -> Any:
    text = raw.strip()
    if path in _CONFIG_VERSION_FIELDS:
        from bot.services.versioning import normalize_version

        value = normalize_version(text)
        if not value:
            raise ValueError(text)
        return value
    if path in _CONFIG_LIST_FIELDS:
        items = [item.strip() for item in text.split(",") if item.strip()]
        if path == "moderation.notification_chat_ids":
            return [int(item) for item in items]
        return items
    if path in _CONFIG_INT_FIELDS:
        value = int(text)
        if path in _ARCHIVE_CHANNEL_FIELDS and value != 0 and value >= -1000000000000:
            raise ValueError(text)
        if path == "moderation.vote_threshold":
            value = max(1, value)
        if path == "moderation.forum_chat_id" and value > 0:
            value = int(f"-100{value}")
        return value
    if path in _CONFIG_BOOL_FIELDS:
        normalized = text.lower()
        if normalized in {"1", "true", "yes", "y", "on", "да", "д", "вкл", "включено"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "нет", "н", "выкл", "выключено"}:
            return False
        raise ValueError(text)
    if path in {"channel.username", "icons_channel.username", "publish_channel"}:
        return text.lstrip("@")
    return text


def _config_section_text(section: str, lang: str) -> str:
    cfg = get_config()
    if section == "channels":
        fields = [
            "channel.id",
            "channel.username",
            "channel.title",
            "publish_channel",
            "channel.default_tags",
            "channel.locale_order",
            "archive.channel_id",
            "archive.technical_channel_id",
            "icons_channel.id",
            "icons_channel.username",
            "icons_channel.title",
            "icons_channel.default_tags",
            "icons_channel.locale_order",
        ]
        title = t("admin_cfg_section_channels", lang)
    elif section == "moderation":
        fields = [
            "moderation.forum_chat_id",
            "moderation.forum_topic_id",
            "moderation.vote_threshold",
            "moderation.notification_chat_ids",
            "moderation.delete_review_notifications_on_decision",
            "moderation.min_supported_version",
            "moderation.require_vote_reason",
            "moderation.send_reasons_to_author",
        ]
        title = t("admin_cfg_section_moderation", lang)
    elif section == "admins":
        return f"<b>{t('admin_cfg_section_admins', lang)}</b>"
    else:
        fields = ["checked_on_version"]
        title = t("admin_cfg_section_other", lang)

    lines = [f"<b>{title}</b>"]
    for field in fields:
        label_key = _CONFIG_FIELD_LABEL_KEYS.get(field, f"admin_cfg_{field}")
        label = t(label_key, lang)
        value = _format_config_value(_config_current_value(cfg, field))
        lines.append(f"{label}: <code>{html.escape(value)}</code>")
    return "\n".join(lines)


async def _render_admins_manage(cb: CallbackQuery, state: FSMContext, field: str) -> None:
    lang = _lang_for(cb)
    config = get_config()
    admin_ids = sorted({int(x) for x in (config.get(field, []) or []) if str(x).isdigit()})
    await state.update_data(config_field=field, config_message_id=cb.message.message_id if cb.message else None)
    await state.set_state(AdminFlow.menu)
    text, keyboard = await _admins_manage_view(cb.bot, cb, field, admin_ids, lang)
    msg = await answer(cb, text, keyboard, "admin")
    if msg:
        await state.update_data(config_message_id=msg.message_id)


async def _admins_manage_view(bot, target, field: str, admin_ids: list[int], lang: str):
    title = _admins_title(field, target)
    identities = await _resolve_admin_identities(bot, admin_ids, lang)
    entries = [
        _tr(target, "admin_identity_line", username=identities[admin_id]["html"], admin_id=admin_id)
        for admin_id in admin_ids
    ]
    text = f"<b>{title}</b>\n{_admin_staff_counts(target)}"
    if entries:
        text += "\n\n<blockquote expandable>" + "\n".join(entries) + "</blockquote>"
    text += "\n\n" + _tr(target, "admin_choose_action")
    return (
        text,
        admin_manage_admins_kb(
            field,
            admin_ids,
            lang=lang,
            labels={admin_id: identities[admin_id]["button"] for admin_id in admin_ids},
        ),
    )


def _admins_title(field: str, target: CallbackQuery | Message | int | None) -> str:
    key = {
        "admins_super": "admin_cfg_superadmins",
        "admins_plugins": "admin_cfg_admins_plugins",
        "admins_icons": "admin_cfg_admins_icons",
    }.get(field, "admin_cfg_admins")
    return _tr(target, key)


def _admin_staff_counts(target: CallbackQuery | Message | int | None) -> str:
    superadmins = get_admins_super()
    moderators = get_admins() - superadmins
    return _tr(
        target,
        "admin_staff_counts",
        superadmins=len(superadmins),
        moderators=len(moderators),
    )


async def _resolve_admin_identities(
    bot,
    admin_ids: list[int],
    lang: str,
    hints: dict[int, str] | None = None,
) -> dict[int, dict[str, str]]:
    from core.user_store import get_user
    from bot.services.chat_info import get_chat_labels

    stored = {admin_id: get_user(admin_id) for admin_id in admin_ids}
    hints = hints or {}
    missing = []
    for admin_id in admin_ids:
        try:
            fresh = 0 <= time.time() - float(stored[admin_id].get("identity_checked_at") or 0) < 300
        except (TypeError, ValueError):
            fresh = False
        if not fresh or not str(stored[admin_id].get("username") or "").strip():
            missing.append(admin_id)
    labels = await get_chat_labels(bot, missing, resolve_members=True)
    result: dict[int, dict[str, str]] = {}
    for admin_id in admin_ids:
        hint = str(hints.get(admin_id) or "").strip().lstrip("@")
        if hint in {"unknown", f"id{admin_id}"}:
            hint = ""
        user = stored.get(admin_id, {})
        username = str(user.get("username") or "").strip().lstrip("@") or hint
        name = str(user.get("full_name") or user.get("first_name") or "").strip()
        identity = labels.get(admin_id, {})
        if identity:
            username = identity.get("username") or ""
            name = identity.get("name") or name
        if username:
            button = f"@{username}"
            html_label = f"@{plain_html(username)}"
        else:
            button = name or t("admin_unknown_username", lang)
            html_label = (
                f'<a href="tg://user?id={admin_id}">{plain_html(name)}</a>'
                if name
                else t("admin_unknown_username", lang)
            )
        result[admin_id] = {"button": button[:64], "html": html_label}
    return result


async def _render_broadcast_enter(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    await state.set_state(AdminFlow.entering_broadcast)
    await state.update_data(broadcast_message_id=cb.message.message_id if cb.message else None)
    data = await state.get_data()
    prompt = "admin_broadcast_prompt_moderation" if data.get("broadcast_kind") == "moderation" else "admin_prompt_broadcast"
    msg = await answer(cb, _tr(cb, prompt), admin_cancel_kb(lang), "admin")
    if msg:
        await state.update_data(broadcast_message_id=msg.message_id)


async def _render_broadcast_kind(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    await state.set_state(AdminFlow.menu)
    await answer(cb, _tr(cb, "admin_broadcast_choose"), admin_broadcast_kind_kb(lang), "admin")


def _broadcast_kind_label(kind: str, lang: str) -> str:
    return t("admin_broadcast_moderation" if kind == "moderation" else "admin_broadcast_all", lang)


def _broadcast_progress_text(kind: str, total: int, current: int, sent: int, failed: int, lang: str) -> str:
    return t(
        "admin_broadcast_progress",
        lang,
        kind=_broadcast_kind_label(kind, lang),
        total=total,
        current=current,
        sent=sent,
        failed=failed,
    )


def _broadcast_date(value) -> str:
    date = _parse_dt_utc(value)
    return date.strftime("%d.%m.%Y %H:%M UTC") if date else "—"


async def _render_broadcast_history(cb: CallbackQuery, state: FSMContext, page: int) -> None:
    from bot.services.broadcast import broadcast_history

    lang = _lang_for(cb)
    records = broadcast_history()
    total_pages = max(1, math.ceil(len(records) / 8))
    page = min(max(0, page), total_pages - 1)
    items = [
        (item["id"], f"{_broadcast_date(item.get('created_at'))} · {item.get('actor') or '—'}"[:64])
        for item in records[page * 8:(page + 1) * 8]
    ]
    text = t("admin_broadcast_history_title", lang, count=len(records), page=page + 1, total=total_pages)
    if not records:
        text += "\n\n" + t("admin_broadcast_history_empty", lang)
    await state.set_state(AdminFlow.menu)
    await answer(cb, text, admin_broadcast_history_kb(items, page, total_pages, lang), "admin")


async def _render_broadcast_record(cb: CallbackQuery, state: FSMContext, run_id: str) -> None:
    from bot.services.broadcast import get_broadcast

    lang = _lang_for(cb)
    item = get_broadcast(run_id)
    if not item:
        await _render_broadcast_history(cb, state, 0)
        return
    actor_id = item.get("actor_id")
    actor = plain_html(item.get("actor") or "—")
    if actor_id:
        actor = f'<a href="tg://user?id={int(actor_id)}">{actor}</a>'
    text = t(
        "admin_broadcast_record", lang,
        date=_broadcast_date(item.get("created_at")), actor=actor, actor_id=actor_id or "—",
        kind=_broadcast_kind_label(str(item.get("kind") or "all"), lang),
        status=t(f"admin_broadcast_status_{item.get('status', 'interrupted')}", lang),
        current=item.get("current", 0), total=item.get("total", 0), sent=item.get("sent", 0),
        failed=item.get("failed", 0), finished=_broadcast_date(item.get("finished_at")),
        content=telegram_html(str(item.get("text") or "—")),
    )
    await state.set_state(AdminFlow.menu)
    await answer(cb, text, admin_broadcast_history_kb([], 0, 1, lang), "admin")


async def _run_broadcast_delivery(bot, chat_id: int, message_id: int, recipients: list[int], text: str, run, lang: str) -> None:
    from bot.services.broadcast import deliver_broadcast, hide_progress_view, progress_view_visible, show_progress_view

    show_progress_view(chat_id, message_id)
    task = asyncio.create_task(deliver_broadcast(bot, recipients, text, run))
    previous = None
    while not task.done():
        progress = (run.current, run.sent, run.failed)
        if progress != previous and progress_view_visible(chat_id, message_id):
            try:
                await bot.edit_message_text(
                    _broadcast_progress_text(run.kind, run.total, *progress, lang),
                    chat_id=chat_id, message_id=message_id, parse_mode=ParseMode.HTML,
                    reply_markup=admin_broadcast_progress_kb(lang),
                )
                previous = progress
            except Exception:
                pass
        await asyncio.wait({task}, timeout=2)
    try:
        await task
    except Exception:
        logger.exception("event=broadcast.delivery_failed run_id=%s", run.id)
    text = t("admin_broadcast_done", lang, sent=run.sent, failed=run.failed)
    if run.status != "completed":
        text += "\n" + t(f"admin_broadcast_status_{run.status}", lang)
    try:
        if progress_view_visible(chat_id, message_id):
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, parse_mode=ParseMode.HTML,
                reply_markup=admin_broadcast_kind_kb(lang), disable_web_page_preview=True,
            )
        elif chat_id:
            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML,
                                   reply_markup=admin_broadcast_kind_kb(lang), disable_web_page_preview=True)
    except Exception:
        pass
    finally:
        hide_progress_view(chat_id, message_id)


def _my_vote(entry: dict, user_id: int) -> str | None:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    votes = payload.get("moderation_votes") if isinstance(payload, dict) else None
    item = votes.get(str(user_id)) if isinstance(votes, dict) else None
    return item.get("vote") if isinstance(item, dict) else None


def _has_my_vote(entry: dict, user_id: int) -> bool:
    return _my_vote(entry, user_id) is not None


async def _render_queue(cb: CallbackQuery, state: FSMContext, token: str) -> None:
    lang = _lang_for(cb)
    parts = token.split(":")
    queue_type = parts[2] if len(parts) > 2 else "plugins"
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    visible_requests = [r for r in (list(get_requests(status="pending")) + list(get_requests(status="error"))) if not _is_appeal(r)]

    if queue_type == "approved":
        requests = [r for r in get_requests(status="approved") if not _is_appeal(r)
                    and not (r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon"))]
    elif queue_type == "update":
        requests = [
            r for r in visible_requests
            if r.get("type") == "update"
            if not (r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon"))
        ]
    elif queue_type == "plugins":
        requests = [
            r for r in visible_requests
            if not (r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon"))
            and r.get("type") != "update"
        ]
    else:
        requests = [
            r for r in visible_requests
            if not (r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon"))
        ]

    if not requests:
        if queue_type == "approved":
            await state.set_state(AdminFlow.menu)
            await answer(cb, _tr(cb, "admin_approved_empty"), admin_plugins_section_kb(lang=lang, role=_admin_menu_role(cb)), "admin")
            return
        await _render_menu(cb, state)
        return

    viewer_id = cb.from_user.id if cb.from_user else 0
    requests.sort(key=lambda r: (
        1 if _is_resubmit_appeal(r) else 0,
        1 if _has_my_vote(r, viewer_id) else 0,
        str(r.get("submitted_at") or ""),
    ))

    total = len(requests)
    total_pages = math.ceil(total / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = requests[start : start + PAGE_SIZE]

    items: List[tuple[str, str]] = []
    for entry in page_items:
        payload = entry.get("payload", {})
        if payload.get("submission_type") == "icon" or payload.get("icon"):
            icon = payload.get("icon", {})
            name = icon.get("name", "?")
            version = icon.get("version", "")
        else:
            plugin = payload.get("plugin", {})
            name = plugin.get("name", "?")
            version = plugin.get("version", "")
        request_type = entry.get("type")
        if request_type == "delete":
            name = payload.get("delete_slug") or name
            prefix = t("request_kind_delete", lang) + ": "
            row_icon = "delete"
        elif request_type == "update":
            prefix = t("request_kind_update", lang) + ": "
            row_icon = "updates"
        else:
            prefix = ""
            row_icon = None
        if request_type != "delete":
            my = _my_vote(entry, viewer_id)
            if my in {"yes", "no"}:
                row_icon = my
        if entry.get("status") == "error" or payload.get("last_publish_error"):
            prefix = f"Ошибка: {prefix}"
            row_icon = row_icon or "warning"
        label = f"{name} v{version}" if version else f"{name}"
        label = f"{prefix}{label}"
        items.append((label, entry["id"], row_icon))

    if queue_type == "approved":
        title = _tr(cb, "admin_approved_title")
    elif queue_type == "update":
        title = _tr(cb, "admin_queue_title_updates")
    elif queue_type == "new":
        title = _tr(cb, "admin_queue_title_new")
    elif queue_type == "plugins":
        title = _tr(cb, "admin_queue_title_plugins")
    else:
        title = _tr(cb, "admin_queue_title_all")
    caption = f"<b>{title}</b>\n{_tr(cb, 'admin_page', current=page + 1, total=total_pages)}"
    await state.set_state(AdminFlow.menu)
    await answer(cb, caption, admin_queue_kb(items, page, total_pages, queue_type, lang=lang), "moderation")


async def _render_review(
    target: CallbackQuery | Message, state: FSMContext, token: str, *, send_document: bool = False,
) -> None:
    lang = _lang_for(target)
    request_id = token.split(":", 2)[-1] if token.startswith("adm:review:") else token
    entry = get_request_by_id(request_id)
    if not entry or not _ensure_request_role(target, entry):
        await answer(target, _tr(target, "not_found"), admin_cancel_kb(lang), "admin")
        return

    payload = entry.get("payload") or {}
    is_super = _is_super_admin(target)
    request_id = str(entry["id"])
    previous_message = target if isinstance(target, Message) else target.message
    await state.set_state(AdminFlow.reviewing)
    await state.update_data(
        current_request=request_id, category_selection=None, **category_fields(payload),
        draft_message_id=previous_message.message_id if previous_message else None,
    )
    body, _ = _forum_request_text_and_file(entry, lang=lang)
    text = f"{body}\n\n{_review_meta_block(entry, lang=lang)}"
    actionable = not entry.get("history_only") and entry.get("status") not in {"published", "deleted"}
    if _is_appeal(entry):
        kb = admin_appeal_decision_kb(request_id, lang=lang) if is_super and actionable else admin_cancel_kb(lang)
    elif is_super and actionable and (payload.get("submission_type") == "icon" or payload.get("icon")):
        kb = icon_draft_edit_kb(lang=lang)
    else:
        is_delete = entry.get("type") == "delete"
        kb = admin_review_kb(
            request_id, payload.get("user_id", 0), lang=lang,
            submit_label=_tr(target, "admin_submit_delete") if is_delete else None,
            submit_callback=f"adm:delete:{request_id}" if is_delete else None,
            allow_publish=is_super and actionable,
            allow_vote=can_accept_vote(entry),
            allow_approve=can_approve_request(entry),
            media_count=len(_comment_media_of_entry(entry)),
        )
        if is_super and entry.get("status") == "scheduled":
            kb.inline_keyboard[-1:-1] = admin_schedule_controls(request_id, lang)
    sent = await answer(target, text, kb, request_image_key(entry))
    if sent:
        await state.update_data(draft_message_id=sent.message_id)
    if send_document and isinstance(target, CallbackQuery):
        spawn_background(_send_review_document(target, entry, sent))


_SUPER_ONLY_NAV_PREFIXES = (
    "adm:config", "adm:broadcast", "adm:stats", "adm:modstats", "adm:banned", "adm:blocklist", "adm:backup",
    "adm:maint", "adm:audit", "adm:auditlog", "adm:rejreq", "adm:quiz",
    "adm:edit_plugins", "adm:link_author", "adm:icons", "adm:prepublish:",
)


def _nav_token_allowed(cb: CallbackQuery, token: str) -> bool:
    if token == "adm:config":
        return True
    if not token.startswith(_SUPER_ONLY_NAV_PREFIXES):
        return True
    return _is_super_admin(cb)


async def _render_nav_token(cb: CallbackQuery, state: FSMContext, token: str) -> None:
    if token.startswith(("adm:sources", "adm:source:")):
        token = "adm:config"
    lang = _lang_for(cb)
    if not _nav_token_allowed(cb, token):
        await _render_menu(cb, state)
        return
    await state.set_state(AdminFlow.menu)
    if token == "adm:menu":
        await _render_menu(cb, state)
    elif token == "adm:section:plugins":
        await state.set_state(AdminFlow.menu)
        await answer(cb, _plugins_section_text(lang), admin_plugins_section_kb(lang=lang, role=_admin_menu_role(cb)), "admin")
    elif token == "adm:section:updates":
        await _render_updates_list(cb, state, 0)
    elif token == "adm:section:post":
        await state.set_state(AdminFlow.menu)
        await answer(cb, _post_section_text(lang), admin_post_section_kb(lang=lang), "admin")
    elif token == "adm:config":
        await _render_config(cb, state)
    elif token.startswith("adm:quiz:view:"):
        await _render_quiz_item(cb, state, token.split(":", 3)[3])
    elif token.startswith("adm:quiz:"):
        await _render_quiz_list(cb, state, int(token.rsplit(":", 1)[-1]))
    elif token.startswith("adm:config:admins_"):
        await _render_admins_manage(cb, state, token.split(":", 2)[2])
    elif token == "adm:stats":
        await on_admin_stats(cb.model_copy(update={"data": token}), state)
    elif token.startswith("adm:modstats:user:"):
        await on_admin_moderation_person(cb.model_copy(update={"data": token}), state)
    elif token.startswith("adm:modstats:"):
        await on_admin_moderation_stats(cb.model_copy(update={"data": token}), state)
    elif token == "adm:notifs":
        await _render_admin_notifications(cb, state)
    elif token == "adm:vote_prefs":
        await _render_vote_preferences(cb, state)
    elif token.startswith("adm:vtemplates:"):
        parts = token.split(":")
        await _render_vote_template_visibility(cb, state, parts[2], int(parts[3]))
    elif token.startswith("adm:config_section:"):
        section = token.split(":")[2]
        if section == "admins":
            await answer(cb, _config_section_text("admins", lang), admin_config_admins_kb(lang=lang), "admin")
        elif section == "channels":
            await answer(cb, _config_section_text("channels", lang), admin_config_channels_kb(lang=lang), "admin")
        elif section == "moderation":
            await answer(cb, _config_section_text("moderation", lang), admin_config_moderation_kb(lang=lang), "admin")
        elif section == "other":
            await answer(cb, _config_section_text("other", lang), admin_config_other_kb(lang=lang), "admin")
        else:
            await _render_config(cb, state)
    elif token == "adm:broadcast":
        await _render_broadcast_kind(cb, state)
    elif token.startswith("adm:broadcast_history:"):
        await _render_broadcast_history(cb, state, int(token.rsplit(":", 1)[-1]))
    elif token.startswith("adm:broadcast_run:"):
        await _render_broadcast_record(cb, state, token.rsplit(":", 1)[-1])
    elif token == "adm:post":
        await state.set_state(AdminFlow.menu)
        await answer(cb, _post_section_text(lang), admin_post_section_kb(lang=lang), "admin")
    elif token.startswith("adm:queue:"):
        await _render_queue(cb, state, token)
    elif token.startswith("adm:review:"):
        await _render_review(cb, state, token)
    elif token.startswith("adm:prepublish:"):
        entry = get_request_by_id(token.split(":", 2)[2])
        if entry and not entry.get("history_only") and entry.get("status") not in {"published", "deleted"}:
            await _render_publish_editor(cb, state, entry)
        else:
            await _render_menu(cb, state)
    elif token.startswith("adm:updates:"):
        parts = token.split(":")
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _render_updates_list(cb, state, page)
    elif token.startswith("adm:scheduled_posts:"):
        parts = token.split(":")
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _render_scheduled_posts_list(cb, state, page)
    elif token.startswith("adm:scheduled:view:"):
        await _render_scheduled_view(cb, state, token.split(":")[3])
    elif token.startswith("adm:scheduled:"):
        parts = token.split(":")
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _render_scheduled_list(cb, state, page)
    elif token == "adm:backup":
        await _render_backup(cb, state)
    elif token == "adm:maint":
        await answer(cb, _tr(cb, "admin_maint_title"), admin_maintenance_kb(lang), "admin")
    elif token.startswith("adm:rejtpl_cfg"):
        parts = token.split(":")
        await _render_rejtpl_cfg(cb, state, parts[2] if len(parts) > 2 else "reject")
    elif token.startswith("adm:banned:"):
        parts = token.split(":")
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _render_banned(cb, state, page)
    elif token.startswith("adm:audit:"):
        parts = token.split(":")
        status = parts[2] if len(parts) > 3 else "all"
        page = int(parts[-1]) if parts[-1].isdigit() else 0
        await _render_rejected(cb, state, page, status)
    elif token.startswith("adm:auditlog:"):
        parts = token.split(":")
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _render_audit_log(cb, state, page)
    elif token == "adm:blocklist":
        await _render_blocklist(cb, state)
    elif token.startswith("adm:rejapp:"):
        parts = token.split(":")
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _render_rejected_appeals(cb, state, page)
    elif token.startswith("adm:actions:"):
        await _render_review(cb, state, f"adm:review:{token.split(':')[2]}")
    elif token.startswith("adm:reject:") or token.startswith("adm:ban:") or token.startswith("adm:ban_confirm:"):
        await _render_review(cb, state, f"adm:review:{token.split(':')[2]}")
    elif token.startswith("adm:rejreq:"):
        await on_admin_rejected_detail(cb.model_copy(update={"data": token}), state)
    elif token.startswith("adm:appd:"):
        await on_admin_rejected_appeal_detail(cb.model_copy(update={"data": token}), state)
    else:
        await _render_menu(cb, state)


def _can_schedule_request(entry: dict | None) -> bool:
    if not isinstance(entry, dict):
        return False
    req_type = entry.get("type", "new")
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        return False
    return req_type == "new"


def _ensure_admin(cb: CallbackQuery | Message) -> bool:
    user = cb.from_user
    return bool(user and user.id in get_admins())


def _ensure_admin_role(cb: CallbackQuery | Message, role: str) -> bool:
    user = cb.from_user
    if not user:
        return False
    if user.id in get_admins_super():
        return True
    return role != "super" and user.id in get_admins()


def _admin_menu_role(cb: CallbackQuery | Message) -> str | None:
    user = cb.from_user
    if not user:
        return None
    return get_admin_role(user.id)


def _is_super_admin(target: CallbackQuery | Message | int | None) -> bool:
    if isinstance(target, int):
        user_id = target
    else:
        user = target.from_user if target else None
        user_id = user.id if user else None
    return bool(user_id and int(user_id) in get_admins_super())


def _ensure_request_role(cb: CallbackQuery | Message, entry: dict) -> bool:
    payload = entry.get("payload", {})
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        return _ensure_admin_role(cb, "icons")
    return _ensure_admin_role(cb, "plugins")


def _decision_line(target, entry: dict) -> str:
    actor = str(entry.get("decided_by") or "").strip()
    if not actor:
        return ""
    when = ""
    raw = str(entry.get("decided_at") or "").strip()
    if raw:
        try:
            moment = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
            when = " · " + moment.astimezone(TZ_UTC_PLUS_5).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            when = ""
    return _tr(target, "admin_decided_by", actor=actor, when=when)


def _actor_id(target: CallbackQuery | Message) -> int:
    user = getattr(target, "from_user", None)
    return int(getattr(user, "id", 0) or 0)


def _admin_actor_label(target: CallbackQuery | Message) -> str:
    user = target.from_user
    if not user:
        return "<code>?</code>"
    return user_mention(user.id, user.username)


def _admin_notification_label_items(user_id: int | None, lang: str) -> list[tuple[str, str]]:
    role = get_admin_role(user_id) if user_id else None
    return [
        (key, t(label_key, lang))
        for key, label_key in NOTIFICATION_PREF_LABEL_KEYS.items()
        if role == "super" or key not in {"appeals", "author_replies", "threshold"}
    ]


def _admin_notifications_text(user_id: int | None, lang: str) -> str:
    prefs = admin_notification_preferences(user_id)
    lines = [t("admin_notifications_title", lang)]
    for key, label in _admin_notification_label_items(user_id, lang):
        state = t("admin_notify_pref_on", lang) if prefs.get(key, True) else t("admin_notify_pref_off", lang)
        lines.append(f"{label}: <code>{state}</code>")
    return "\n".join(lines)


async def _render_admin_notifications(target: CallbackQuery | Message, state: FSMContext) -> None:
    lang = _lang_for(target)
    user = target.from_user
    if not user or not _ensure_admin(target):
        return
    prefs = admin_notification_preferences(user.id)
    labels = _admin_notification_label_items(user.id, lang)
    await state.set_state(AdminFlow.menu)
    await answer(
        target,
        _admin_notifications_text(user.id, lang),
        admin_notification_settings_kb(prefs, labels, lang=lang),
        "admin",
    )


async def _render_vote_preferences(target: CallbackQuery | Message, state: FSMContext) -> None:
    if not target.from_user or not _ensure_admin(target):
        return
    lang = _lang_for(target)
    prefs = moderation_preferences(target.from_user.id)
    counts = {}
    for kind in TEMPLATE_CONFIG_KEYS:
        templates = _load_templates(kind)
        hidden = set(hidden_moderation_templates(target.from_user.id, kind))
        counts[kind] = (sum(reason_templates.reason_template_id(value) not in hidden for value in templates), len(templates))
    text = t("admin_vote_settings_title", lang,
             visibility=t("admin_vote_templates_shown" if prefs["show_templates"] else "admin_vote_templates_hidden", lang),
             mode=t(f"admin_vote_mode_{prefs['template_input']}", lang))
    await state.set_state(AdminFlow.menu)
    await answer(target, text, admin_vote_settings_kb(prefs, lang, counts), "admin")


async def _render_vote_template_visibility(target: CallbackQuery, state: FSMContext, kind: str, page: int = 0) -> None:
    if not target.from_user or not _ensure_admin(target) or kind not in TEMPLATE_CONFIG_KEYS:
        return
    lang = _lang_for(target)
    templates = _load_templates(kind, lang)
    hidden = set(hidden_moderation_templates(target.from_user.id, kind))
    _, _, page, pages = template_page(templates, page)
    lines = []
    for index, value in reason_templates.template_pages(templates)[page]:
        text = plain_html(" ".join(value.split()))
        if reason_templates.reason_template_id(templates[index]) in hidden:
            text = f"<s>{text}</s>"
        lines.append(f"<b>{index + 1}.</b> {text}")
    text = t("admin_vote_templates_visibility", lang,
             title=t(f"admin_vote_templates_{kind}", lang),
             visible=sum(reason_templates.reason_template_id(value) not in hidden for value in templates),
             total=len(templates), templates="\n".join(lines) or "—", page=page + 1, pages=pages)
    await state.set_state(AdminFlow.menu)
    await answer(target, text, admin_vote_templates_kb(kind, templates, hidden, page, lang), "admin")


async def _render_updates_list(target: CallbackQuery | Message, state: FSMContext, page: int) -> None:
    lang = _lang_for(target)
    requests = list(get_requests(status="pending", request_type="update"))
    if not requests:
        caption = f"<b>{_tr(target, 'admin_queue_title_updates')}</b>\n{_tr(target, 'admin_queue_empty')}" if 'admin_queue_empty' in TEXTS else f"<b>{_tr(target, 'admin_queue_title_updates')}</b>\n—"
        await state.update_data(updates_list_page=0)
        await state.set_state(AdminFlow.menu)
        await answer(target, caption, admin_updates_list_kb([], 0, 1, back_callback="adm:section:plugins", lang=lang), "admin")
        return

    total = len(requests)
    total_pages = math.ceil(total / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = requests[start : start + PAGE_SIZE]

    items: list[tuple[str, str]] = []
    for entry in page_items:
        payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
        plugin = payload.get("plugin", {}) if isinstance(payload.get("plugin"), dict) else {}
        name = plugin.get("name") or entry.get("id")
        version = plugin.get("version") or ""
        label = f"{name} v{version}" if version else f"{name}"
        items.append((label, str(entry.get("id"))))

    caption = f"<b>{_tr(target, 'admin_queue_title_updates')}</b>\n{_tr(target, 'admin_page', current=page + 1, total=total_pages)}"
    await state.update_data(updates_list_page=page)
    await state.set_state(AdminFlow.menu)
    await answer(target, caption, admin_updates_list_kb(items, page, total_pages, back_callback="adm:section:plugins", lang=lang), "admin")


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin(message):
        await answer(message, _tr(message, "admin_denied"))
        return
    await state.clear()
    await state.set_state(AdminFlow.menu)
    await state.update_data(**{_NAV_STACK_KEY: ["adm:menu"]})
    await _reset_menu_navigation(state)
    sent = await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")
    if sent:
        await remember_menu_owner(message, state, sent)


_FORUM_RESEND_DELAY = 1.0
_FORUM_RETRY_MAX_ATTEMPTS = 5


async def _send_forum_request_with_retry(bot, entry: dict, text: str, file_path) -> None:
    for attempt in range(_FORUM_RETRY_MAX_ATTEMPTS):
        try:
            await send_request_to_forum(bot, entry, text, file_path)
            return
        except TelegramRetryAfter as exc:
            wait = max(1, int(getattr(exc, "retry_after", 1))) + 1
            logger.warning(
                "event=admin.resend_forum_request.floodwait request_id=%s attempt=%s wait=%s",
                entry.get("id"), attempt + 1, wait,
            )
            await asyncio.sleep(wait)
    raise RuntimeError(f"floodwait retries exhausted for {entry.get('id')}")


@router.message(Command("new"))
async def cmd_resend_forum_requests(message: Message) -> None:
    if not _ensure_admin_role(message, "super"):
        return

    if not is_moderation_forum_chat(message.chat.id):
        await answer(message, _tr(message, "admin_new_forum_only"))
        return

    cfg = moderation_config()
    try:
        current_thread_id = int(getattr(message, "message_thread_id", None) or 0)
    except Exception:
        current_thread_id = 0
    if current_thread_id != int(cfg["topic_id"]):
        await answer_in_moderation_topic(message, "Команду нужно отправлять в настроенном топике модерации.")
        return

    active = list(get_requests(status="pending")) + list(get_requests(status="error"))
    appeals = [e for e in active if _is_appeal(e)]
    plugins = [e for e in active if not _is_appeal(e)]
    requests = plugins + appeals
    if not requests:
        await answer_in_moderation_topic(message, "Активных заявок нет.")
        return

    sent_plugins = 0
    sent_appeals = 0
    failed = 0
    for index, entry in enumerate(requests):
        try:
            await delete_forum_request_message(message.bot, entry)
            text, file_path = _forum_request_text_and_file(entry)
            await _send_forum_request_with_retry(message.bot, entry, text, file_path)
            if _is_appeal(entry):
                sent_appeals += 1
            else:
                sent_plugins += 1
        except Exception:
            failed += 1
            logger.warning("event=admin.resend_forum_request.failed request_id=%s", entry.get("id"), exc_info=True)
        if index + 1 < len(requests):
            await asyncio.sleep(_FORUM_RESEND_DELAY)

    report = [f"Заявок на плагины: {sent_plugins}"]
    if sent_appeals or appeals:
        report.append(f"Апелляций: {sent_appeals}")
    report.append(f"Ошибок: {failed}")
    await answer_in_moderation_topic(message, "\n".join(report))


def _build_health_text() -> str:
    cfg = get_config()
    moderation = cfg.get("moderation", {}) if isinstance(cfg, dict) else {}
    forum_cfg = moderation_config()
    counts = {
        "pending": len(get_requests(status="pending")),
        "error": len(get_requests(status="error")),
        "scheduled": len(get_requests(status="scheduled")),
        "published": len(get_requests(status="published")),
        "rejected": len(get_requests(status="rejected")),
    }
    plugins_count = len(load_plugins().get("plugins", []) or [])
    audit_count = audit_events_count()
    latest_audit = recent_audit_events(1)
    latest_audit_line = "—"
    if latest_audit:
        event = latest_audit[0]
        latest_audit_line = f"{plain_html(event.get('event') or '—')} / {plain_html(event.get('created_at') or '—')}"

    lines = [
        "<b>Health</b>",
        f"Admin SQLite: <code>{plain_html(ADMIN_SQLITE_PATH)}</code> {emoji_html('yes' if ADMIN_SQLITE_PATH.exists() else 'no')}",
        f"Catalog SQLite: <code>{plain_html(CATALOG_SQLITE_PATH)}</code> {emoji_html('yes' if CATALOG_SQLITE_PATH.exists() else 'no')}",
        f"Data dir: <code>{plain_html(DATA_DIR)}</code> {emoji_html('yes' if DATA_DIR.exists() else 'no')}",
        f"Plugins in catalog: <code>{plugins_count}</code>",
        f"Requests pending/error/scheduled: <code>{counts['pending']}/{counts['error']}/{counts['scheduled']}</code>",
        f"Requests published/rejected: <code>{counts['published']}/{counts['rejected']}</code>",
        f"Forum chat: <code>{forum_cfg['chat_id']}</code>",
        f"Forum topic: <code>{forum_cfg['topic_id']}</code>",
        f"Vote threshold: <code>{forum_cfg['threshold']}</code>",
        f"Notification chats: <code>{plain_html(', '.join(str(x) for x in (moderation.get('notification_chat_ids') or [])) or '—')}</code>",
        f"Audit events: <code>{audit_count}</code>",
        f"Latest audit: <code>{latest_audit_line}</code>",
    ]
    return "\n".join(lines)


def _do_sync_version(target_id: str, new_version: str) -> str:
    db = load_plugins()
    plugins = db.get("plugins", [])
    for p in plugins:
        if isinstance(p, dict) and _match_plugin_id(p, target_id):
            p.setdefault("ru", {})["version"] = new_version
            p.setdefault("en", {})["version"] = new_version
            p["updated_at"] = datetime.utcnow().isoformat()
            save_plugins(db)
            invalidate_catalog_cache()
            return f"версия «{plain_html(target_id)}» → {plain_html(new_version)}"
    return "плагин не найден"


def _do_sync_catalog(link: str, request_id: str, actor: str = "", actor_id: int = 0) -> str:
    entry = get_request_by_id(request_id)
    if not entry:
        return "заявка не найдена"
    try:
        raw = link.split("?")[0].rstrip("/")
        msg_id = int(raw.rsplit("/", 1)[-1])
        channel_username = raw.split("/")[-2]
    except Exception:
        return "неверная ссылка"
    cfg = get_config()
    payload = entry.get("payload", {})
    is_icon = payload.get("submission_type") == "icon" or payload.get("icon")
    chat_id = ((cfg.get("icons_channel") if is_icon else cfg.get("channel")) or {}).get("id")
    if not chat_id:
        return ("icons_channel.id" if is_icon else "channel.id") + " не задан в config"
    adder = add_icon_to_catalog if is_icon else add_to_catalog
    adder(entry, msg_id, chat_id, channel_username, payload.get("user_id"), payload.get("username", ""))
    update_request_status(request_id, "published", actor=actor, actor_id=actor_id)
    return f"пост {msg_id} привязан к заявке {plain_html(request_id)}"


@router.callback_query(F.data == "adm:maint")
async def on_admin_maintenance(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, "adm:maint")
    await state.set_state(AdminFlow.menu)
    await answer(cb, _tr(cb, "admin_maint_title"), admin_maintenance_kb(_lang_for(cb)), "admin")
    await ack(cb)


@router.callback_query(F.data == "adm:maint:health")
async def on_admin_maint_health(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await answer(cb, _build_health_text(), admin_maintenance_kb(_lang_for(cb)), "admin")
    await ack(cb)


@router.callback_query(F.data.in_({"adm:maint:sync_version", "adm:maint:sync_catalog", "adm:maint:erase_id"}))
async def on_admin_maint_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    action = cb.data.split(":")[2]
    prompt_key, target_state = {
        "sync_version": ("admin_maint_prompt_sync_version", AdminFlow.entering_sync_version),
        "sync_catalog": ("admin_maint_prompt_sync_catalog", AdminFlow.entering_sync_catalog),
        "erase_id": ("admin_maint_prompt_erase_id", AdminFlow.entering_erase_id),
    }[action]
    await state.set_state(target_state)
    await answer(cb, _tr(cb, prompt_key), admin_cancel_kb(_lang_for(cb)), "admin")
    await ack(cb)


@router.message(AdminFlow.entering_sync_version)
async def on_admin_maint_sync_version_input(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "super"):
        return
    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(_tr(message, "admin_maint_bad_format"))
        return
    result = _do_sync_version(parts[0].strip(), parts[1].strip())
    await state.set_state(AdminFlow.menu)
    await answer(message, _tr(message, "admin_maint_done", result=result), admin_maintenance_kb(_lang_for(message)), "admin")


@router.message(AdminFlow.entering_sync_catalog)
async def on_admin_maint_sync_catalog_input(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "super"):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await message.answer(_tr(message, "admin_maint_bad_format"))
        return
    result = _do_sync_catalog(
        parts[0].strip(), parts[1].strip(),
        actor=_admin_actor_label(message), actor_id=_actor_id(message),
    )
    await state.set_state(AdminFlow.menu)
    await answer(message, _tr(message, "admin_maint_done", result=result), admin_maintenance_kb(_lang_for(message)), "admin")


@router.message(AdminFlow.entering_erase_id)
async def on_admin_maint_erase_id_input(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "super"):
        return
    target_id = (message.text or "").strip()
    if not target_id:
        await message.answer(_tr(message, "admin_maint_bad_format"))
        return
    removed = delete_requests_by_plugin_id(target_id)
    await state.set_state(AdminFlow.menu)
    await answer(message, _tr(message, "admin_maint_done", result=_tr(message, "admin_maint_archived_count", count=removed)),
                 admin_maintenance_kb(_lang_for(message)), "admin")


@router.callback_query(F.data == "adm:maint:erase")
async def on_admin_maint_erase_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await answer(cb, _tr(cb, "admin_maint_erase_confirm"), admin_maint_confirm_kb("erase", _lang_for(cb)), "admin")
    await ack(cb)


@router.callback_query(F.data == "adm:maint:erase:confirm")
async def on_admin_maint_erase_apply(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    removed = cleanup_hidden_requests()
    await answer(cb, _tr(cb, "admin_maint_done", result=_tr(cb, "admin_maint_cleaned_count", count=removed)),
                 admin_maintenance_kb(_lang_for(cb)), "admin")
    await ack(cb)


async def _render_backup(target: CallbackQuery | Message, state: FSMContext) -> None:
    from bot.services.backup import get_backup_config
    from bot.keyboards import admin_backup_kb

    lang = _lang_for(target)
    cfg = get_backup_config()
    next_run = "—"
    if cfg["auto_enabled"]:
        next_run = _tr(target, "admin_backup_auto_state", hours=cfg["interval_hours"])
    text = _tr(target, "admin_backup_title", state=next_run)
    await state.set_state(AdminFlow.menu)
    await answer(target, text, admin_backup_kb(cfg, lang), "admin")


@router.callback_query(F.data == "adm:backup")
async def on_admin_backup(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, "adm:backup")
    await _render_backup(cb, state)
    await ack(cb)


@router.callback_query(F.data == "adm:backup:now")
async def on_admin_backup_now(cb: CallbackQuery, state: FSMContext) -> None:
    from bot.services.backup import send_backup

    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await ack(cb, _tr(cb, "admin_backup_started"))
    ok = await send_backup(cb.bot, cb.from_user.id)
    add_audit_event(
        "backup.manual",
        actor_id=cb.from_user.id if cb.from_user else None,
        actor=_admin_actor_label(cb),
        details={"ok": ok},
    )
    await answer(cb, _tr(cb, "admin_backup_sent" if ok else "admin_backup_failed"),
                 None, "admin")
    await _render_backup(cb, state)


@router.callback_query(F.data == "adm:backup:toggle")
async def on_admin_backup_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    from bot.services.backup import get_backup_config, set_backup_config

    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    cfg = get_backup_config()
    set_backup_config(auto_enabled=not cfg["auto_enabled"])
    await _render_backup(cb, state)
    await ack(cb)


@router.callback_query(F.data == "adm:backup:interval")
async def on_admin_backup_interval(cb: CallbackQuery, state: FSMContext) -> None:
    from bot.services.backup import get_backup_config, set_backup_config, cycle_interval

    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    cfg = get_backup_config()
    set_backup_config(interval_hours=cycle_interval(cfg["interval_hours"]))
    await _render_backup(cb, state)
    await ack(cb)


async def _render_backup_recipients(target: CallbackQuery | Message, state: FSMContext) -> None:
    from bot.services.backup import get_backup_config
    from bot.keyboards import admin_backup_recipients_kb
    lang = _lang_for(target)
    recipients = get_backup_config()["recipients"]
    await state.set_state(AdminFlow.menu)
    await answer(target, _tr(target, "admin_backup_recipients_title"),
                 admin_backup_recipients_kb(recipients, lang), "admin")


@router.callback_query(F.data == "adm:backup:recipients")
async def on_admin_backup_recipients(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, "adm:backup:recipients")
    await _render_backup_recipients(cb, state)
    await ack(cb)


@router.callback_query(F.data == "adm:backup:rcp_noop")
async def on_admin_backup_rcp_noop(cb: CallbackQuery, state: FSMContext) -> None:
    await ack(cb)


@router.callback_query(F.data == "adm:backup:rcp_add")
async def on_admin_backup_rcp_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await state.set_state(AdminFlow.entering_backup_recipient)
    await answer(cb, _tr(cb, "admin_backup_recipient_prompt"), admin_cancel_kb(_lang_for(cb)), "admin")
    await ack(cb)


@router.message(AdminFlow.entering_backup_recipient)
async def on_admin_backup_recipient_input(message: Message, state: FSMContext) -> None:
    from bot.services.backup import get_backup_config, set_backup_config
    if not _ensure_admin_role(message, "super"):
        return
    raw = (message.text or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer(_tr(message, "admin_bad_id"), disable_web_page_preview=True)
        return
    rid = int(raw)
    current = get_backup_config()["recipients"]
    if rid not in current:
        current = sorted(set(current) | {rid})
        set_backup_config(recipients=current)
    await state.set_state(AdminFlow.menu)
    await _render_backup_recipients(message, state)


@router.callback_query(F.data.regexp(r"^adm:backup:rcp_rm:-?\d+$"))
async def on_admin_backup_rcp_rm(cb: CallbackQuery, state: FSMContext) -> None:
    from bot.services.backup import get_backup_config, set_backup_config
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    rid = int(cb.data.split(":")[3])
    current = get_backup_config()["recipients"]
    if rid in current:
        set_backup_config(recipients=[x for x in current if x != rid])
    await _render_backup_recipients(cb, state)
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:sources?(?::|$)"))
async def on_removed_admin_source(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await ack(cb, _tr(cb, "admin_denied"), show_alert=True)
        return
    await state.set_state(AdminFlow.menu)
    await _reset_menu_navigation(state)
    await _render_nav_token(cb, state, "adm:config")
    await ack(cb)


@router.callback_query(F.data == "adm:cancel")
async def on_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await ack(cb, _tr(cb, "admin_denied"), show_alert=True)
        return

    picker = (await state.get_data()).get("category_selection")
    if picker and not await _admin_category_picker_valid(cb, state, picker.get("prefix")):
        return
    await state.update_data(category_selection=None, edit_field=None)
    current = await state.get_state()
    if current in _TRANSIENT_ADMIN_STATES:
        if current == AdminFlow.entering_reject_comment.state:
            await _discard_pending_reject_media_groups(cb)
        data = await state.get_data()
        stack = data.get(_NAV_STACK_KEY)
        top = stack[-1] if isinstance(stack, list) and stack else None
        await _render_nav_token(cb, state, top) if top else await _render_menu(cb, state)
        await ack(cb)
        return

    prev = await _nav_prev(state)
    if prev:
        await _render_nav_token(cb, state, prev)
    else:
        await _render_menu(cb, state)
    await ack(cb)


@router.callback_query(F.data == "adm:section:updates")
async def on_admin_section_updates(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:section:updates")
    await _render_updates_list(cb, state, 0)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:updates:"))
async def on_admin_updates_list(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = cb.data.split(":")
    page = 0
    if len(parts) > 2 and parts[2].isdigit():
        page = int(parts[2])
    await _nav_push(state, f"adm:updates:{page}")
    await _render_updates_list(cb, state, page)
    await ack(cb)


@router.callback_query(F.data == "adm:section:post")
async def on_admin_section_post(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:section:post")
    await state.set_state(AdminFlow.menu)
    await answer(cb, _post_section_text(lang), admin_post_section_kb(lang=lang), "admin")
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:scheduled:\d+$"))
async def on_admin_scheduled(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins") and not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    parts = cb.data.split(":")
    page = 0
    if len(parts) > 2 and parts[2].isdigit():
        page = int(parts[2])

    await _nav_push(state, f"adm:scheduled:{page}")
    await _render_scheduled_list(cb, state, page)
    await ack(cb)


@router.callback_query(F.data == "adm:scheduled:back")
async def on_admin_scheduled_back(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    page = data.get("scheduled_list_page")
    if not isinstance(page, int):
        page = 0
    await _render_scheduled_list(cb, state, page)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:scheduled:view:"))
async def on_admin_scheduled_view(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[3]
    await _nav_push(state, f"adm:scheduled:view:{request_id}")
    await _render_review(cb, state, request_id, send_document=True)
    await ack(cb)


async def _approval_followups(bot, request_id: str) -> None:
    from bot.services.moderation import refresh_forum_vote_keyboard

    entry = get_request_by_id(request_id)
    if not entry:
        return
    results = await asyncio.gather(
        notify_author_approval(bot, request_id), refresh_forum_vote_keyboard(bot, entry),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            logger.error("event=moderation.approval_followup_failed request_id=%s", request_id,
                         exc_info=(type(result), result, result.__traceback__))


def _save_plugin_schedule(target, request_id: str, moment: datetime) -> dict | None:
    if not _is_super_admin(target) or not _acquire_request_lock(request_id):
        return None
    try:
        entry = schedule_request(request_id, moment, _admin_actor_label(target), _actor_id(target))
        if entry:
            spawn_background(_approval_followups(target.bot, request_id))
        return entry
    finally:
        _release_request_lock(request_id)


def _swap_plugin_schedules(target, first_id: str, second_id: str) -> bool:
    if not _is_super_admin(target):
        return False
    locked = []
    try:
        for request_id in sorted({first_id, second_id}):
            if not _acquire_request_lock(request_id):
                return False
            locked.append(request_id)
        first, second = get_request_by_id(first_id), get_request_by_id(second_id)
        if not first or not second or first.get("status") != "scheduled" or second.get("status") != "scheduled":
            return False
        first_dt, second_dt = _scheduled_dt_utc(first), _scheduled_dt_utc(second)
        now = datetime.now(timezone.utc)
        if not first_dt or not second_dt or min(first_dt, second_dt) <= now:
            return False
        actor, actor_id = _admin_actor_label(target), _actor_id(target)
        if not _can_schedule_request(first) or not _can_schedule_request(second):
            return False
        schedule_request(first_id, second_dt, actor, actor_id)
        schedule_request(second_id, first_dt, actor, actor_id)
        for request_id in locked:
            spawn_background(_approval_followups(target.bot, request_id))
        return True
    finally:
        for request_id in locked:
            _release_request_lock(request_id)


@router.callback_query(F.data.startswith("adm:approve:"))
async def on_admin_approve(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if not can_approve_request(entry):
        await cb.answer(_tr(cb, "admin_request_already_handled"), show_alert=True)
        return
    if not _acquire_request_lock(request_id):
        await cb.answer(_tr(cb, "admin_request_processing"), show_alert=True)
        return
    try:
        errors = _validate_request_before_publish(entry)
        if errors:
            await cb.answer(_tr(cb, "admin_has_errors"), show_alert=True)
            await answer(cb, _tr(cb, "admin_publish_stopped", errors="\n".join(plain_html(value) for value in errors)), admin_cancel_kb(_lang_for(cb)), "admin")
            return
        if not approve_request(request_id, _admin_actor_label(cb), _actor_id(cb)):
            await cb.answer(_tr(cb, "admin_request_already_handled"), show_alert=True)
            return
    finally:
        _release_request_lock(request_id)
    spawn_background(_approval_followups(cb.bot, request_id))
    await _reset_menu_navigation(state)
    await _nav_push(state, "adm:section:plugins")
    await _nav_push(state, "adm:queue:approved:0")
    await _render_queue(cb, state, "adm:queue:approved:0")
    await ack(cb, _tr(cb, "admin_request_approved"))


@router.callback_query(F.data.startswith("adm:scheduled:unschedule:"))
async def on_admin_scheduled_unschedule(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[3]
    entry = get_request_by_id(request_id)
    if not entry or entry.get("status") != "scheduled":
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    if not _acquire_request_lock(request_id):
        await cb.answer(_tr(cb, "admin_request_processing"), show_alert=True)
        return
    try:
        payload = entry.get("payload") or {}
        update_request_status(request_id, "approved", actor=_admin_actor_label(cb), actor_id=_actor_id(cb), payload_fields={
            "scheduled_at": None, "publish_retry_at": None, "moderation_pending_votes": {},
            "approved_at": payload.get("approved_at") or datetime.now(timezone.utc).isoformat(),
            "approved_by": payload.get("approved_by") or _admin_actor_label(cb),
            "approved_by_id": payload.get("approved_by_id") or _actor_id(cb),
        })
        add_audit_event("moderation.request_unscheduled", actor=_admin_actor_label(cb), actor_id=_actor_id(cb), request_id=request_id)
    finally:
        _release_request_lock(request_id)
    spawn_background(_approval_followups(cb.bot, request_id))
    data = await state.get_data()
    page = data.get("scheduled_list_page")
    if not isinstance(page, int):
        page = 0
    await _render_scheduled_list(cb, state, page)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:scheduled:up:"))
async def on_admin_scheduled_up(cb: CallbackQuery, state: FSMContext) -> None:
    request_id = cb.data.split(":")[3]

    requests = list(get_requests(status="scheduled"))
    sortable = [(r, _scheduled_dt_utc(r)) for r in requests]
    sortable.sort(key=lambda x: x[1] or datetime.max.replace(tzinfo=timezone.utc))
    ids = [str(r.get("id")) for r, _ in sortable]
    if request_id not in ids:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    idx = ids.index(request_id)
    if idx == 0:
        await cb.answer()
        return

    prev_id = ids[idx - 1]
    cur = get_request_by_id(request_id)
    prev = get_request_by_id(prev_id)
    if not cur or not prev:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    cur_dt = _scheduled_dt_utc(cur)
    prev_dt = _scheduled_dt_utc(prev)
    if not cur_dt or not prev_dt:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    if not _swap_plugin_schedules(cb, request_id, prev_id):
        await cb.answer(_tr(cb, "admin_request_already_handled"), show_alert=True)
        return
    await ack(cb)
    await on_admin_scheduled_view(cb, state)


@router.callback_query(F.data.startswith("adm:scheduled:down:"))
async def on_admin_scheduled_down(cb: CallbackQuery, state: FSMContext) -> None:
    request_id = cb.data.split(":")[3]

    requests = list(get_requests(status="scheduled"))
    sortable = [(r, _scheduled_dt_utc(r)) for r in requests]
    sortable.sort(key=lambda x: x[1] or datetime.max.replace(tzinfo=timezone.utc))
    ids = [str(r.get("id")) for r, _ in sortable]
    if request_id not in ids:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    idx = ids.index(request_id)
    if idx >= len(ids) - 1:
        await cb.answer()
        return

    next_id = ids[idx + 1]
    cur = get_request_by_id(request_id)
    nxt = get_request_by_id(next_id)
    if not cur or not nxt:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    cur_dt = _scheduled_dt_utc(cur)
    next_dt = _scheduled_dt_utc(nxt)
    if not cur_dt or not next_dt:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    if not _swap_plugin_schedules(cb, request_id, next_id):
        await cb.answer(_tr(cb, "admin_request_already_handled"), show_alert=True)
        return
    await ack(cb)
    await on_admin_scheduled_view(cb, state)


@router.callback_query(F.data.startswith("adm:scheduled:change_time:"))
async def on_admin_scheduled_change_time(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    request_id = cb.data.split(":")[3]
    entry = get_request_by_id(request_id)
    if not entry:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    await state.update_data(schedule_preset_target="scheduled_change", scheduled_current_request=request_id)
    await state.set_state(AdminFlow.editing_scheduled_time)
    presets = _cleanup_schedule_presets()
    kb = admin_schedule_presets_kb(presets, "adm:scheduled:change_preset", "adm:schedule:preset:add", lang=lang)
    await answer(cb, _render_schedule_prompt_text(cb), kb, "admin")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.editing_scheduled_time)
async def on_admin_scheduled_change_time_value(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin(message):
        return

    text = (message.text or "").strip()
    schedule_dt_local = _parse_local_schedule(text)
    if not schedule_dt_local:
        await message.answer(_tr(message, "admin_post_schedule_bad_format"), parse_mode=ParseMode.HTML)
        return

    now_local = datetime.now(tz=TZ_UTC_PLUS_5)
    if schedule_dt_local <= now_local:
        await message.answer(_tr(message, "admin_post_schedule_past"), parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    request_id = data.get("scheduled_current_request")
    if not request_id:
        await state.set_state(AdminFlow.menu)
        await _reset_menu_navigation(state)
        await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")
        return

    schedule_dt_utc = schedule_dt_local.astimezone(timezone.utc)
    if not _save_plugin_schedule(message, request_id, schedule_dt_utc):
        await message.answer(_tr(message, "admin_request_already_handled"))
        return
    await state.set_state(AdminFlow.menu)
    await _render_scheduled_view(message, state, request_id)


@router.callback_query(F.data.startswith("adm:scheduled:change_preset:"))
async def on_admin_scheduled_change_preset(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    if len(parts) < 4:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    try:
        idx = int(parts[3])
    except ValueError:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    presets = _cleanup_schedule_presets()
    if idx < 0 or idx >= len(presets):
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    schedule_dt_local = _parse_local_schedule(presets[idx])
    if not schedule_dt_local:
        await cb.answer(_tr(cb, "admin_post_schedule_bad_format"), show_alert=True)
        return

    now_local = datetime.now(tz=TZ_UTC_PLUS_5)
    if schedule_dt_local <= now_local:
        await cb.answer(_tr(cb, "admin_post_schedule_past"), show_alert=True)
        return

    data = await state.get_data()
    request_id = data.get("scheduled_current_request")
    if not request_id:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    schedule_dt_utc = schedule_dt_local.astimezone(timezone.utc)
    if not _save_plugin_schedule(cb, request_id, schedule_dt_utc):
        await cb.answer(_tr(cb, "admin_request_already_handled"), show_alert=True)
        return
    await state.set_state(AdminFlow.menu)
    await ack(cb)
    await on_admin_scheduled_view(cb, state)

@router.callback_query(F.data.startswith("adm:icon_edit_select:"))
async def on_admin_icon_edit_select(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    slug = decode_slug(cb.data.split(":")[2])
    icon = find_icon_by_slug(slug)
    if not icon:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    authors = icon.get("authors", {}) or {}
    icon_locale = _localized_block(icon, lang)
    edit_payload = {
        "icon": {
            "name": icon_locale.get("name") or "",
            "author": _localized_author(authors, lang),
            "version": icon_locale.get("version") or "",
            "count": icon.get("count") or 0,
        }
    }

    draft_text = build_icon_channel_post({"payload": edit_payload})
    await state.update_data(
        edit_icon_slug=slug,
        edit_icon_payload=edit_payload,
    )
    await state.set_state(AdminFlow.editing_catalog_icon)
    await answer(
        cb,
        draft_text,
        icon_draft_edit_kb(prefix="adm_icon_edit", submit_label=_tr(cb, "admin_submit_update"), lang=lang),
        "iconpacks",
    )
    await ack(cb)


@router.callback_query(F.data.startswith("adm:select_plugin:"))
@router.callback_query(F.data.startswith("adm:link_select:"))
async def on_admin_link_select_plugin(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    slug = decode_slug(cb.data.split(":")[2])
    await state.update_data(link_plugin_slug=slug)
    await state.set_state(AdminFlow.linking_author_user)
    await answer(cb, _tr(cb, "admin_enter_user_id"), admin_cancel_kb(lang), "admin")
    await ack(cb)


@router.callback_query(F.data.startswith("adm:link_list:"))
async def on_admin_link_list_plugins(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    page = int(cb.data.split(":")[2])
    data = await state.get_data()
    slugs = data.get("edit_plugins_list") or []
    plugins = [find_plugin_by_slug(s) for s in slugs]
    plugins = [p for p in plugins if p]
    if not plugins:
        await cb.answer(_tr(cb, "admin_search_nothing_found"), show_alert=True)
        return
    total_pages = math.ceil(len(plugins) / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = plugins[start : start + PAGE_SIZE]
    items = [(_localized_name(p, lang), p.get("slug")) for p in page_items]
    await answer(
        cb,
        _tr(cb, "admin_search_results_title"),
        admin_plugins_list_kb(items, page, total_pages, select_prefix="adm:link_select", list_prefix="adm:link_list", lang=lang),
        "admin",
    )
    await ack(cb)


@router.callback_query(F.data.startswith("adm:icon_edit_list:"))
async def on_admin_icon_edit_list(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    page = int(cb.data.split(":")[2])
    data = await state.get_data()
    slugs = data.get("edit_icons_list") or []
    icons = [find_icon_by_slug(s) for s in slugs]
    icons = [i for i in icons if i]
    if not icons:
        await cb.answer(_tr(cb, "admin_search_nothing_found"), show_alert=True)
        return
    total_pages = math.ceil(len(icons) / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = icons[start : start + PAGE_SIZE]
    items = [(_localized_name(i, lang), i.get("slug")) for i in page_items]
    await answer(
        cb,
        _tr(cb, "admin_search_results_title"),
        admin_plugins_list_kb(items, page, total_pages, select_prefix="adm:icon_edit_select", list_prefix="adm:icon_edit_list", lang=lang),
        "admin",
    )
    await ack(cb)


@router.callback_query(F.data == "adm:link_author_icons")
async def on_admin_link_author_icons(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await state.set_state(AdminFlow.searching_icon)
    await state.update_data(search_message_id=cb.message.message_id if cb.message else None, search_purpose="link_icon")
    msg = await answer(cb, _tr(cb, "admin_prompt_search_plugin"), admin_cancel_kb(lang), "admin")
    if msg:
        await state.update_data(search_message_id=msg.message_id)
    await ack(cb)


@router.callback_query(F.data == "adm:edit_icons")
async def on_admin_edit_icons(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await state.set_state(AdminFlow.searching_icon)
    await state.update_data(search_message_id=cb.message.message_id if cb.message else None, search_purpose="edit_icon")
    msg = await answer(cb, _tr(cb, "admin_prompt_search_plugin"), admin_cancel_kb(lang), "admin")
    if msg:
        await state.update_data(search_message_id=msg.message_id)
    await ack(cb)


@router.message(AdminFlow.searching_icon)
async def on_admin_search_icons(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "icons"):
        return
    if (message.text or "").strip().startswith("/"):
        return
    query = (message.text or "").strip().lower()
    if not query:
        await message.answer(_tr(message, "need_text"), disable_web_page_preview=True)
        return

    icons = list_published_icons()
    filtered = [
        i
        for i in icons
        if query in (i.get("slug", "").lower())
        or query in _localized_name(i, lang).lower()
        or query in ((i.get("ru") or {}).get("name") or "").lower()
        or query in ((i.get("en") or {}).get("name") or "").lower()
    ]
    await state.update_data(edit_icons_list=[i.get("slug") for i in filtered])
    data = await state.get_data()
    purpose = data.get("search_purpose")
    select_prefix = "adm:icon_edit_select" if purpose != "link_icon" else "adm:icon_link_select"
    list_prefix = "adm:icon_edit_list" if purpose != "link_icon" else "adm:icon_link_list"
    if not filtered:
        await message.answer(_tr(message, "admin_search_nothing_found"), disable_web_page_preview=True)
        return

    total_pages = math.ceil(len(filtered) / PAGE_SIZE)
    items = [(_localized_name(i, lang), i.get("slug")) for i in filtered[:PAGE_SIZE]]
    await answer(
        message,
        _tr(message, "admin_search_results_title"),
        admin_plugins_list_kb(items, 0, total_pages, select_prefix=select_prefix, list_prefix=list_prefix, lang=lang),
        "admin",
    )


@router.callback_query(F.data.startswith("adm:icon_link_list:"))
async def on_admin_icon_link_list(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    page = int(cb.data.split(":")[2])
    data = await state.get_data()
    slugs = data.get("edit_icons_list") or []
    icons = [find_icon_by_slug(s) for s in slugs]
    icons = [i for i in icons if i]
    if not icons:
        await cb.answer(_tr(cb, "admin_search_nothing_found"), show_alert=True)
        return
    total_pages = math.ceil(len(icons) / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = icons[start : start + PAGE_SIZE]
    items = [(_localized_name(i, lang), i.get("slug")) for i in page_items]
    await answer(
        cb,
        _tr(cb, "admin_search_results_title"),
        admin_plugins_list_kb(items, page, total_pages, select_prefix="adm:icon_link_select", list_prefix="adm:icon_link_list", lang=lang),
        "admin",
    )
    await ack(cb)


@router.callback_query(F.data.startswith("adm:icon_link_select:"))
async def on_admin_icon_link_select(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    slug = decode_slug(cb.data.split(":")[2])
    await state.update_data(link_icon_slug=slug)
    await state.set_state(AdminFlow.linking_author_icon_user)
    await answer(cb, _tr(cb, "admin_enter_user_id"), admin_cancel_kb(lang), "admin")
    await ack(cb)


@router.message(AdminFlow.linking_author_icon_user)
async def on_admin_icon_link_user(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "icons"):
        return

    data = await state.get_data()
    slug = data.get("link_icon_slug")
    if not slug:
        await state.set_state(AdminFlow.menu)
        return
    text = (message.text or "").strip()
    parts = text.split()
    if not parts:
        await message.answer(_tr(message, "admin_enter_valid_user_id"), parse_mode=ParseMode.HTML)
        return
    try:
        user_id = int(parts[0])
    except ValueError:
        await message.answer(_tr(message, "admin_enter_valid_user_id"), parse_mode=ParseMode.HTML)
        return
    username = parts[1].lstrip("@") if len(parts) > 1 else ""

    success = add_submitter_to_iconpack(slug, user_id, username)
    if success:
        await message.answer(_tr(message, "admin_author_linked"))
    else:
        await message.answer(_tr(message, "admin_link_failed"), parse_mode=ParseMode.HTML)

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await _reset_menu_navigation(state)
    await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")


@router.callback_query(F.data == "adm:section:plugins")
async def on_admin_section_plugins(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:section:plugins")
    await state.set_state(AdminFlow.menu)
    await answer(cb, _plugins_section_text(lang), admin_plugins_section_kb(lang=lang, role=_admin_menu_role(cb)), "admin")
    await ack(cb)


_REJECTED_PER_PAGE = 8

_AUDIT_FILTERS = ("all", "published", "rejected", "pending", "rework", "scheduled", "approved", "deleted")

_STATUS_ICONS = {
    "approved": "yes",
    "published": "yes",
    "rejected": "no",
    "pending": "clock",
    "rework": "edit",
    "scheduled": "calendar",
    "draft": "file",
    "deleted": "delete",
    "error": "warning",
}


def _audit_requests(status: str = "all") -> list[dict]:
    reqs = [r for r in get_all_requests() + get_history_summaries() if r.get("type") != "unban_appeal"]
    if status != "all":
        reqs = [r for r in reqs if r.get("status") == status]
    reqs.sort(key=lambda r: str(r.get("updated_at") or r.get("submitted_at") or ""), reverse=True)
    return reqs


def _request_label(entry: dict, lang: str = "ru") -> str:
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    item = payload.get("plugin") or payload.get("icon") or {}
    name = item.get("name") or payload.get("delete_slug") or entry.get("id") or "—"
    typ = entry.get("type")
    prefix = f"{t(f'request_kind_{typ}', lang)}: " if typ in {"update", "delete"} else ""
    return f"{prefix}{name}"[:48]


def _reject_comment(entry: dict) -> str:
    for h in reversed(entry.get("history") or []):
        if isinstance(h, dict) and h.get("status") == "rejected" and h.get("comment"):
            return str(h.get("comment") or "")
    return ""


def _last_status_comment(entry: dict) -> str:
    for h in reversed(entry.get("history") or []):
        if isinstance(h, dict) and h.get("comment"):
            return str(h.get("comment") or "")
    return ""


async def _render_rejected(
    target: CallbackQuery | Message, state: FSMContext, page: int, status: str = "all"
) -> None:
    lang = _lang_for(target)
    if status not in _AUDIT_FILTERS:
        status = "all"
    reqs = _audit_requests(status)
    total = len(reqs)
    total_pages = max(1, math.ceil(total / _REJECTED_PER_PAGE))
    page = max(0, min(page, total_pages - 1))
    await _nav_push(state, f"adm:audit:{status}:{page}")
    page_items = reqs[page * _REJECTED_PER_PAGE:(page + 1) * _REJECTED_PER_PAGE]
    items = [(_request_label(r, lang), str(r.get("id")), _STATUS_ICONS.get(str(r.get("status")), "file")) for r in page_items]
    filter_label = _tr(target, f"admin_audit_filter_{status}")
    if reqs:
        header = _tr(
            target, "admin_audit_title",
            filter=filter_label, current=page + 1, total=total_pages, count=total,
        )
    else:
        header = _tr(target, "admin_audit_empty", filter=filter_label)
    await state.set_state(AdminFlow.menu)
    await answer(target, header, admin_rejected_kb(items, page, total_pages, status=status, lang=lang), "admin")


@router.callback_query(F.data.regexp(r"^adm:audit:(?:[a-z]+:)?\d+$"))
async def on_admin_audit(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = cb.data.split(":")
    status = parts[2] if len(parts) > 3 else "all"
    page = int(parts[-1])
    await _render_rejected(cb, state, page, status)
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:rejreq:[^:]+$"))
async def on_admin_rejected_detail(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if not entry:
        await cb.answer(_tr(cb, "admin_request_not_found"), show_alert=True)
        await _render_rejected(cb, state, 0)
        return
    await _nav_push(state, cb.data)
    status = str(entry.get("status") or "—")
    status_label = f"{_STATUS_ICONS.get(status, '•')} {_tr(cb, f'admin_audit_filter_{status}') if status in _AUDIT_FILTERS else status}"
    text = _tr(cb, "admin_audit_status", status=status_label) + "\n\n" + _render_request_draft(entry, lang=_lang_for(cb))
    if entry.get("history_only"):
        text += "\n\n" + _tr(cb, "admin_request_history_only")
    decision = _decision_line(cb, entry)
    if decision:
        text += "\n\n" + decision
    comment = _last_status_comment(entry)
    if comment:
        text += "\n\n" + _tr(cb, "admin_rejected_reason", comment=strip_blockquote_tags(telegram_html(reason_templates.localize_reason(comment, _lang_for(cb)))))
    if vote_counts(entry)[2]:
        text += "\n\n" + vote_summary(entry, lang=_lang_for(cb))
    await answer(cb, text, admin_rejected_detail_kb(request_id, _lang_for(cb)), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.regexp(r"^adm:rejdel:[^:]+$"))
async def on_admin_rejected_delete(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    delete_request(request_id)
    add_audit_event("moderation.request_deleted", actor_id=cb.from_user.id if cb.from_user else None,
                    actor=_admin_actor_label(cb), request_id=str(request_id))
    await _render_rejected(cb, state, 0)
    await ack(cb, _tr(cb, "admin_rejected_deleted"), show_alert=True)


def _rejected_appeals() -> list[dict]:
    reqs = [r for r in get_requests(status="rejected") if r.get("type") == "unban_appeal"]
    reqs.sort(key=lambda r: str(r.get("updated_at") or r.get("submitted_at") or ""), reverse=True)
    return reqs


def _appeal_label(entry: dict) -> str:
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    name = payload.get("username") or payload.get("user_id") or entry.get("id") or "—"
    return f"@{name}" if payload.get("username") else str(name)


async def _render_rejected_appeals(target: CallbackQuery | Message, state: FSMContext, page: int) -> None:
    lang = _lang_for(target)
    reqs = _rejected_appeals()
    total = len(reqs)
    total_pages = max(1, math.ceil(total / _REJECTED_PER_PAGE))
    page = max(0, min(page, total_pages - 1))
    await _nav_push(state, f"adm:rejapp:{page}")
    page_items = reqs[page * _REJECTED_PER_PAGE:(page + 1) * _REJECTED_PER_PAGE]
    items = [(_appeal_label(r), str(r.get("id")), "ban" if _reject_comment(r).endswith("окончательный") else "lock") for r in page_items]
    if reqs:
        header = _tr(target, "admin_rejected_appeals_title", current=page + 1, total=total_pages, count=total)
    else:
        header = _tr(target, "admin_rejected_appeals_empty")
    await state.set_state(AdminFlow.menu)
    await answer(target, header, admin_rejected_appeals_kb(items, page, total_pages, lang=lang), "admin")


@router.callback_query(F.data.regexp(r"^adm:rejapp:\d+$"))
async def on_admin_rejected_appeals(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    page = int(cb.data.split(":")[2])
    await _render_rejected_appeals(cb, state, page)
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:appd:[^:]+$"))
async def on_admin_rejected_appeal_detail(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if not entry or entry.get("type") != "unban_appeal":
        await cb.answer(_tr(cb, "admin_request_not_found"), show_alert=True)
        await _render_rejected_appeals(cb, state, 0)
        return
    await _nav_push(state, cb.data)
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    user_id = int(payload.get("user_id") or 0)
    mention = user_mention(user_id, payload.get("username"))
    appeal_text = strip_blockquote_tags(telegram_html(payload.get("appeal_text") or "")) or "—"
    text = _tr(cb, "admin_rejected_appeal_detail", user=mention, uid=user_id, text=appeal_text)
    comment = _reject_comment(entry)
    if comment:
        text += "\n\n" + _tr(cb, "admin_rejected_reason", comment=strip_blockquote_tags(telegram_html(reason_templates.localize_reason(comment, _lang_for(cb)))))
    await answer(cb, text, admin_rejected_appeal_detail_kb(request_id, _lang_for(cb)), "admin")
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:appunb:[^:]+$"))
async def on_admin_rejected_appeal_unban(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if not entry or entry.get("type") != "unban_appeal":
        await cb.answer(_tr(cb, "admin_request_not_found"), show_alert=True)
        await _render_rejected_appeals(cb, state, 0)
        return
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    user_id = int(payload.get("user_id") or 0)
    if user_id:
        unban_user(user_id)
    update_request_status(request_id, "published", comment="Апелляция одобрена (пересмотр)", actor=_admin_actor_label(cb), actor_id=_actor_id(cb))
    add_audit_event("moderation.appeal_approved", actor_id=cb.from_user.id if cb.from_user else None,
                    actor=_admin_actor_label(cb), request_id=str(request_id), details={"user_id": user_id})
    if user_id:
        try:
            await cb.bot.send_message(
                user_id,
                t("appeal_approved", get_lang(user_id)),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("event=appeal.notify_user_failed user_id=%s", user_id)
    await _render_rejected_appeals(cb, state, 0)
    await ack(cb, _tr(cb, "appeal_done_unban"), show_alert=True)


@router.callback_query(F.data.regexp(r"^adm:appdel:[^:]+$"))
async def on_admin_rejected_appeal_delete(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    delete_request(request_id)
    add_audit_event("moderation.request_deleted", actor_id=cb.from_user.id if cb.from_user else None,
                    actor=_admin_actor_label(cb), request_id=str(request_id))
    await _render_rejected_appeals(cb, state, 0)
    await ack(cb, _tr(cb, "admin_rejected_deleted"), show_alert=True)


@router.callback_query(F.data.in_({"adm:post", "adm:post:new"}))
async def on_admin_post(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:section:post")
    await state.set_state(AdminFlow.entering_post)
    await state.update_data(post_message_id=cb.message.message_id if cb.message else None)
    msg = await answer(cb, _tr(cb, "admin_post_prompt"), admin_cancel_kb(lang), "admin")
    if msg:
        await state.update_data(post_message_id=msg.message_id)
    await ack(cb)


@router.message(AdminFlow.entering_post)
async def on_admin_post_text(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "super"):
        return

    text = telegram_html(message.html_text or message.text or "")
    if not text:
        await message.answer(_tr(message, "admin_post_no_text"))
        return

    await state.update_data(post_text=text)
    await state.set_state(AdminFlow.confirming_post)

    preview = f"{text}\n\n{_tr(message, 'admin_post_confirm')}"
    post_message_id = (await state.get_data()).get("post_message_id")
    if post_message_id:
        try:
            await message.bot.edit_message_text(
                preview,
                chat_id=message.chat.id,
                message_id=post_message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_post_confirm_kb(lang=lang),
                disable_web_page_preview=True,
            )
            await state.update_data(post_message_id=post_message_id)
            return
        except Exception:
            pass

    sent = await message.answer(
        preview,
        parse_mode=ParseMode.HTML,
        reply_markup=admin_post_confirm_kb(lang=lang),
        disable_web_page_preview=True,
    )
    if sent:
        await state.update_data(post_message_id=sent.message_id)


async def _send_admin_post(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    data = await state.get_data()
    text = (data.get("post_text") or "").strip()
    if not text:
        await cb.answer(_tr(cb, "admin_post_no_text"), show_alert=True)
        return

    final_text = text
    from core.storage import load_updated

    updated = load_updated()
    items = updated.get("items") or []
    if items:
        lines = [_tr(cb, "admin_updated_block_title")]
        for it in items:
            if not isinstance(it, dict):
                continue
            name = (it.get("name") or "").strip()
            link = (it.get("link") or "").strip()
            if not name or not link:
                continue
            lines.append(f'• <a href="{html.escape(link, quote=True)}">{plain_html(name)}</a>')
        if len(lines) > 1:
            final_text = f"{final_text}\n\n" + "\n".join(lines)

    from userbot.client import get_userbot

    userbot = await get_userbot()
    if not userbot:
        await cb.answer(_tr(cb, "admin_userbot_unavailable"), show_alert=True)
        return

    result = await userbot.publish_post(final_text)

    clear_updated_plugins()

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await _reset_menu_navigation(state)
    await answer(
        cb,
        _tr(cb, "admin_post_sent", link=result.get("link", "")),
        admin_menu_kb(_admin_menu_role(cb), lang=lang),
        "admin",
    )


def _with_updated_plugins_block(target: CallbackQuery | Message, base_text: str) -> str:
    final_text = (base_text or "").strip()
    if not final_text:
        return ""

    try:
        from core.storage import load_updated

        updated = load_updated()
        items = updated.get("items") or []
        if not items:
            return final_text

        lines = [_tr(target, "admin_updated_block_title")]
        for it in items:
            if not isinstance(it, dict):
                continue
            name = (it.get("name") or "").strip()
            link = (it.get("link") or "").strip()
            if not name or not link:
                continue
            lines.append(f'• <a href="{html.escape(link, quote=True)}">{plain_html(name)}</a>')
        if len(lines) > 1:
            return f"{final_text}\n\n" + "\n".join(lines)
    except Exception:
        pass

    return final_text


def _has_updated_plugins() -> bool:
    try:
        from core.storage import load_updated

        updated = load_updated()
        items = updated.get("items") or []
        return any(isinstance(it, dict) and it.get("name") and it.get("link") for it in items)
    except Exception:
        return False


@router.callback_query(F.data == "adm:post:send")
async def on_admin_post_send(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _send_admin_post(cb, state)
    await ack(cb)


@router.callback_query(F.data == "adm:post:send_updates")
async def on_admin_post_send_updates(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _send_admin_post(cb, state)
    await ack(cb)


@router.callback_query(F.data == "adm:schedule")
async def on_admin_plugin_schedule(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    data = await state.get_data()
    request_id = data.get("current_request")
    if not request_id:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    entry = get_request_by_id(request_id)
    if not entry:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    if not _ensure_request_role(cb, entry):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    req_type = entry.get("type", "new")
    if req_type in {"update", "delete"}:
        await cb.answer(_tr(cb, "admin_schedule_unsupported_type"), show_alert=True)
        return
    await state.set_state(AdminFlow.scheduling_plugin)
    await answer(cb, _tr(cb, "admin_post_schedule_prompt"), admin_cancel_kb(lang), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


async def _apply_plugin_schedule(target, state: FSMContext, schedule_dt_local: datetime) -> None:
    lang = _lang_for(target)
    data = await state.get_data()
    request_id = data.get("current_request")
    entry = get_request_by_id(request_id) if request_id else None
    if not entry:
        await state.set_state(AdminFlow.menu)
        await _reset_menu_navigation(state)
        await answer(target, _tr(target, "admin_title"), admin_menu_kb(_admin_menu_role(target), lang=lang), "admin")
        return

    try:
        if not _save_plugin_schedule(target, request_id, schedule_dt_local):
            await answer(target, _tr(target, "admin_request_already_handled"), admin_cancel_kb(lang), "admin")
            return
        await state.set_state(AdminFlow.menu)
        await _reset_menu_navigation(state)
        await answer(
            target,
            _tr(target, "admin_plugin_scheduled", datetime=schedule_dt_local.strftime(_SCHEDULE_FORMAT)),
            admin_menu_kb(_admin_menu_role(target), lang=lang),
            "admin",
        )
    except Exception as exc:
        logger.exception("Schedule error")
        await _reset_menu_navigation(state)
        await answer(
            target,
            _tr(target, "admin_generic_error", error=plain_html(exc)),
            admin_menu_kb(_admin_menu_role(target), lang=lang),
            "admin",
        )


@router.message(AdminFlow.scheduling_plugin)
async def on_admin_plugin_schedule_datetime(message: Message, state: FSMContext) -> None:
    if not _ensure_admin(message):
        return

    schedule_dt_local = _parse_local_schedule(message.text or "")
    if not schedule_dt_local:
        await message.answer(_tr(message, "admin_post_schedule_bad_format"), parse_mode=ParseMode.HTML)
        return
    if schedule_dt_local <= datetime.now(tz=TZ_UTC_PLUS_5):
        await message.answer(_tr(message, "admin_post_schedule_past"), parse_mode=ParseMode.HTML)
        return

    await _apply_plugin_schedule(message, state, schedule_dt_local)


@router.callback_query(F.data.regexp(r"^adm:schedule:preset:\d+$"))
async def on_admin_plugin_schedule_preset(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    schedule_dt_local = _preset_at(int(cb.data.split(":")[3]))
    if not schedule_dt_local:
        await cb.answer(_tr(cb, "admin_post_schedule_past"), show_alert=True)
        return

    await _apply_plugin_schedule(cb, state, schedule_dt_local)
    await ack(cb)


@router.callback_query(F.data == "adm:post:schedule")
async def on_admin_post_schedule(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    data = await state.get_data()
    if not data.get("post_text"):
        await cb.answer(_tr(cb, "admin_post_no_text"), show_alert=True)
        return
    await state.set_state(AdminFlow.scheduling_post)
    await state.update_data(schedule_preset_target="post")
    await answer(
        cb,
        _render_schedule_prompt_text(cb),
        _schedule_prompt_kb("adm:post:schedule:preset", "adm:post:schedule:preset:add", lang),
        "admin",
    )
    await ack(cb)


async def _apply_post_schedule(target, state: FSMContext, schedule_dt_local: datetime) -> None:
    lang = _lang_for(target)
    data = await state.get_data()
    post_text = (data.get("post_text") or "").strip()
    if not post_text:
        await state.set_state(AdminFlow.menu)
        await _reset_menu_navigation(state)
        await answer(target, _tr(target, "admin_title"), admin_menu_kb(_admin_menu_role(target), lang=lang), "admin")
        return

    includes_updated_plugins = _has_updated_plugins()
    post_text = _with_updated_plugins_block(target, post_text)

    from userbot.client import get_userbot
    userbot = await get_userbot()
    if not userbot:
        await answer(target, _tr(target, "admin_userbot_unavailable"), admin_cancel_kb(lang), "admin")
        return

    schedule_dt_utc = schedule_dt_local.astimezone(timezone.utc)
    result = await userbot.schedule_post(post_text, schedule_dt_utc)

    post_id = str(result.get("message_id") or int(time.time() * 1000))
    _upsert_scheduled_post(
        {
            "id": post_id,
            "text": post_text,
            "scheduled_at": result.get("scheduled_at") or schedule_dt_utc.isoformat(),
            "message_id": result.get("message_id"),
            "chat_id": result.get("chat_id"),
            "link": result.get("link"),
            "includes_updated_plugins": includes_updated_plugins,
        }
    )

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await _reset_menu_navigation(state)
    await answer(
        target,
        _tr(
            target,
            "admin_post_scheduled",
            datetime=schedule_dt_local.strftime(_SCHEDULE_FORMAT),
            link=result.get("link", ""),
        ),
        admin_menu_kb(_admin_menu_role(target), lang=lang),
        "admin",
    )


@router.message(AdminFlow.scheduling_post)
async def on_admin_schedule_datetime(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "plugins"):
        return

    schedule_dt_local = _parse_local_schedule(message.text or "")
    if not schedule_dt_local:
        await message.answer(_tr(message, "admin_post_schedule_bad_format"), parse_mode=ParseMode.HTML)
        return
    if schedule_dt_local <= datetime.now(tz=TZ_UTC_PLUS_5):
        await message.answer(_tr(message, "admin_post_schedule_past"), parse_mode=ParseMode.HTML)
        return

    await _apply_post_schedule(message, state, schedule_dt_local)


@router.callback_query(F.data.regexp(r"^adm:post:schedule:preset:\d+$"))
async def on_admin_post_schedule_preset(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    schedule_dt_local = _preset_at(int(cb.data.split(":")[4]))
    if not schedule_dt_local:
        await cb.answer(_tr(cb, "admin_post_schedule_past"), show_alert=True)
        return

    await _apply_post_schedule(cb, state, schedule_dt_local)
    await ack(cb)


@router.callback_query(F.data.in_({"adm:schedule:preset:add", "adm:post:schedule:preset:add"}))
async def on_admin_schedule_preset_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    data = await state.get_data()
    target = data.get("schedule_preset_target")
    if not target:
        target = "post" if cb.data.startswith("adm:post:") else "plugin"
        await state.update_data(schedule_preset_target=target)

    await state.set_state(AdminFlow.adding_schedule_preset)
    await answer(cb, _tr(cb, "admin_schedule_preset_add_prompt"), admin_cancel_kb(_lang_for(cb)), "admin")
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:scheduled_posts:\d+$"))
async def on_admin_scheduled_posts(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    parts = cb.data.split(":")
    page = 0
    if len(parts) > 2 and parts[2].isdigit():
        page = int(parts[2])

    await _nav_push(state, f"adm:scheduled_posts:{page}")
    await _render_scheduled_posts_list(cb, state, page)
    await ack(cb)


@router.callback_query(F.data == "adm:scheduled_posts:back")
async def on_admin_scheduled_posts_back(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    page = data.get("scheduled_posts_list_page")
    if not isinstance(page, int):
        page = 0
    await _render_scheduled_posts_list(cb, state, page)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:scheduled_posts:view:"))
async def on_admin_scheduled_posts_view(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    post_id = cb.data.split(":")[3]
    await _render_scheduled_post_view(cb, state, post_id)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:scheduled_posts:delete:"))
async def on_admin_scheduled_posts_delete(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    post_id = cb.data.split(":")[3]
    it = _find_scheduled_post(post_id)
    if it:
        from userbot.client import get_userbot

        userbot = await get_userbot()
        if userbot and it.get("message_id"):
            try:
                await userbot.delete_message(int(it.get("message_id")))
            except Exception:
                pass
        _delete_scheduled_post(post_id)

    data = await state.get_data()
    page = data.get("scheduled_posts_list_page")
    if not isinstance(page, int):
        page = 0
    await _render_scheduled_posts_list(cb, state, page)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:scheduled_posts:edit_text:"))
async def on_admin_scheduled_posts_edit_text(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    post_id = cb.data.split(":")[3]
    if not _find_scheduled_post(post_id):
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    await state.update_data(scheduled_post_current_id=post_id)
    await state.set_state(AdminFlow.scheduled_post_edit_text)
    await answer(cb, _tr(cb, "admin_post_edit_prompt"), admin_cancel_kb(lang), "admin")
    await ack(cb)


@router.message(AdminFlow.scheduled_post_edit_text)
async def on_admin_scheduled_posts_edit_text_value(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "plugins"):
        return
    text = telegram_html(message.html_text or message.text or "")
    if not text:
        await message.answer(_tr(message, "need_text"))
        return
    data = await state.get_data()
    post_id = data.get("scheduled_post_current_id")
    it = _find_scheduled_post(str(post_id)) if post_id else None
    if not it:
        await state.set_state(AdminFlow.menu)
        return
    it = {**it, "text": text}
    _upsert_scheduled_post(it)
    await state.set_state(AdminFlow.menu)
    await _render_scheduled_post_view(message, state, str(post_id))


@router.callback_query(F.data.startswith("adm:scheduled_posts:change_time:"))
async def on_admin_scheduled_posts_change_time(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    post_id = cb.data.split(":")[3]
    if not _find_scheduled_post(post_id):
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    await state.update_data(scheduled_post_current_id=post_id, schedule_preset_target="scheduled_post")
    await state.set_state(AdminFlow.scheduled_post_edit_time)
    kb = admin_schedule_presets_kb(_cleanup_schedule_presets(), "adm:scheduled_posts:change_preset", "adm:post:schedule:preset:add", lang=lang)
    await answer(cb, _render_schedule_prompt_text(cb), kb, "admin")
    await ack(cb)


@router.callback_query(F.data.startswith("adm:scheduled_posts:change_preset:"))
async def on_admin_scheduled_posts_change_preset(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) < 4:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    try:
        idx = int(parts[3])
    except ValueError:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    presets = _cleanup_schedule_presets()
    if idx < 0 or idx >= len(presets):
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    schedule_dt_local = _parse_local_schedule(presets[idx])
    if not schedule_dt_local:
        await cb.answer(_tr(cb, "admin_post_schedule_bad_format"), show_alert=True)
        return
    now_local = datetime.now(tz=TZ_UTC_PLUS_5)
    if schedule_dt_local <= now_local:
        await cb.answer(_tr(cb, "admin_post_schedule_past"), show_alert=True)
        return

    data = await state.get_data()
    post_id = data.get("scheduled_post_current_id")
    it = _find_scheduled_post(str(post_id)) if post_id else None
    if not it:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    from userbot.client import get_userbot

    userbot = await get_userbot()
    if not userbot:
        await cb.answer(_tr(cb, "admin_userbot_unavailable"), show_alert=True)
        return

    old_message_id = it.get("message_id")
    if old_message_id:
        try:
            await userbot.delete_message(int(old_message_id))
        except Exception:
            pass

    new_dt_utc = schedule_dt_local.astimezone(timezone.utc)
    res = await userbot.schedule_post(str(it.get("text") or ""), new_dt_utc)
    updated_item = {
        **it,
        "scheduled_at": res.get("scheduled_at") or new_dt_utc.isoformat(),
        "message_id": res.get("message_id"),
        "chat_id": res.get("chat_id"),
        "link": res.get("link"),
    }
    _upsert_scheduled_post(updated_item)
    await state.set_state(AdminFlow.menu)
    await _render_scheduled_post_view(cb, state, str(post_id))
    await ack(cb)


@router.message(AdminFlow.scheduled_post_edit_time)
async def on_admin_scheduled_posts_change_time_value(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "plugins"):
        return
    text = (message.text or "").strip()
    schedule_dt_local = _parse_local_schedule(text)
    if not schedule_dt_local:
        await message.answer(_tr(message, "admin_post_schedule_bad_format"), parse_mode=ParseMode.HTML)
        return
    now_local = datetime.now(tz=TZ_UTC_PLUS_5)
    if schedule_dt_local <= now_local:
        await message.answer(_tr(message, "admin_post_schedule_past"), parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    post_id = data.get("scheduled_post_current_id")
    it = _find_scheduled_post(str(post_id)) if post_id else None
    if not it:
        await state.set_state(AdminFlow.menu)
        return

    from userbot.client import get_userbot

    userbot = await get_userbot()
    if not userbot:
        await message.answer(_tr(message, "admin_userbot_unavailable"), parse_mode=ParseMode.HTML)
        return

    old_message_id = it.get("message_id")
    if old_message_id:
        try:
            await userbot.delete_message(int(old_message_id))
        except Exception:
            pass

    new_dt_utc = schedule_dt_local.astimezone(timezone.utc)
    res = await userbot.schedule_post(str(it.get("text") or ""), new_dt_utc)
    updated_item = {
        **it,
        "scheduled_at": res.get("scheduled_at") or new_dt_utc.isoformat(),
        "message_id": res.get("message_id"),
        "chat_id": res.get("chat_id"),
        "link": res.get("link"),
    }
    _upsert_scheduled_post(updated_item)
    await state.set_state(AdminFlow.menu)
    await _render_scheduled_post_view(message, state, str(post_id))


@router.callback_query(F.data.startswith("adm:scheduled_posts:up:"))
async def on_admin_scheduled_posts_up(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    post_id = cb.data.split(":")[3]
    items = _cleanup_scheduled_posts()
    sortable = [(it, _parse_dt_utc(it.get("scheduled_at"))) for it in items]
    sortable.sort(key=lambda x: x[1] or datetime.max.replace(tzinfo=timezone.utc))
    ids = [str(it.get("id")) for it, _ in sortable]
    if post_id not in ids:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    idx = ids.index(post_id)
    if idx == 0:
        await cb.answer()
        return
    prev_id = ids[idx - 1]
    cur = _find_scheduled_post(post_id)
    prev = _find_scheduled_post(prev_id)
    if not cur or not prev:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    cur_at = cur.get("scheduled_at")
    prev_at = prev.get("scheduled_at")
    _upsert_scheduled_post({**cur, "scheduled_at": prev_at})
    _upsert_scheduled_post({**prev, "scheduled_at": cur_at})
    await _render_scheduled_post_view(cb, state, post_id)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:scheduled_posts:down:"))
async def on_admin_scheduled_posts_down(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    post_id = cb.data.split(":")[3]
    items = _cleanup_scheduled_posts()
    sortable = [(it, _parse_dt_utc(it.get("scheduled_at"))) for it in items]
    sortable.sort(key=lambda x: x[1] or datetime.max.replace(tzinfo=timezone.utc))
    ids = [str(it.get("id")) for it, _ in sortable]
    if post_id not in ids:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    idx = ids.index(post_id)
    if idx >= len(ids) - 1:
        await cb.answer()
        return
    next_id = ids[idx + 1]
    cur = _find_scheduled_post(post_id)
    nxt = _find_scheduled_post(next_id)
    if not cur or not nxt:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    cur_at = cur.get("scheduled_at")
    next_at = nxt.get("scheduled_at")
    _upsert_scheduled_post({**cur, "scheduled_at": next_at})
    _upsert_scheduled_post({**nxt, "scheduled_at": cur_at})
    await _render_scheduled_post_view(cb, state, post_id)
    await ack(cb)


@router.message(AdminFlow.adding_schedule_preset)
async def on_admin_add_schedule_preset(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin(message):
        return

    text = (message.text or "").strip()
    schedule_dt_local = _parse_local_schedule(text)
    if not schedule_dt_local:
        await message.answer(_tr(message, "admin_post_schedule_bad_format"), parse_mode=ParseMode.HTML)
        return

    now_local = datetime.now(tz=TZ_UTC_PLUS_5)
    if schedule_dt_local <= now_local:
        await message.answer(_tr(message, "admin_post_schedule_past"), parse_mode=ParseMode.HTML)
        return

    dt_str = schedule_dt_local.strftime("%d.%m.%Y %H:%M")
    cfg = get_config()
    presets = _get_schedule_presets_local_str()
    if dt_str not in presets:
        presets.append(dt_str)
    cfg["schedule_presets"] = presets
    save_config(cfg)
    invalidate("config")

    data = await state.get_data()
    target = data.get("schedule_preset_target")

    returns = {
        "post": (AdminFlow.scheduling_post, "adm:post:schedule:preset", "adm:post:schedule:preset:add"),
        "scheduled_post": (AdminFlow.scheduled_post_edit_time, "adm:scheduled_posts:change_preset", "adm:post:schedule:preset:add"),
        "scheduled_change": (AdminFlow.editing_scheduled_time, "adm:scheduled:change_preset", "adm:schedule:preset:add"),
    }
    next_state, select_prefix, add_callback = returns.get(
        target, (AdminFlow.scheduling_plugin, "adm:schedule:preset", "adm:schedule:preset:add")
    )
    await state.set_state(next_state)
    await answer(
        message,
        _render_schedule_prompt_text(message),
        _schedule_prompt_kb(select_prefix, add_callback, lang),
        "admin",
    )


@router.callback_query(F.data.startswith("adm:admins:noop:"))
async def on_admins_noop(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await ack(cb)


@router.callback_query(F.data.startswith("adm:admins:add:"))
async def on_admins_add_start(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    field = cb.data.split(":")[3]
    await state.update_data(config_field=field, config_message_id=cb.message.message_id if cb.message else None)
    await state.set_state(AdminFlow.editing_config)
    await answer(cb, _tr(cb, "admin_prompt_enter_admin_id"), admin_cancel_kb(lang), "admin")
    await ack(cb)


@router.callback_query(F.data.startswith("adm:admins:rm:"))
async def on_admins_remove(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = cb.data.split(":")
    field = parts[3] if len(parts) > 3 else ""
    raw_id = parts[4] if len(parts) > 4 else ""
    try:
        admin_id = int(raw_id)
    except ValueError:
        await cb.answer(_tr(cb, "parse_error", error="bad id"), show_alert=True)
        return
    if field not in {"admins_super", "admins_plugins", "admins_icons"}:
        await cb.answer(_tr(cb, "parse_error", error="bad field"), show_alert=True)
        return

    config = get_config()
    current = [int(x) for x in (config.get(field, []) or []) if str(x).isdigit()]
    updated = sorted({x for x in current if x != admin_id})
    config[field] = updated
    save_config(config)
    invalidate("config")

    text, keyboard = await _admins_manage_view(cb.bot, cb, field, updated, lang)
    msg = await answer(cb, text, keyboard, "admin")
    if msg:
        await state.update_data(config_message_id=msg.message_id)
    await ack(cb, _tr(cb, "admin_removed_short"))


@router.callback_query(F.data == "adm:notifs")
async def on_admin_notifications(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, "adm:config")
    await _nav_push(state, "adm:notifs")
    await _render_admin_notifications(cb, state)
    await ack(cb)


@router.callback_query(F.data == "adm:vote_prefs")
async def on_admin_vote_preferences(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, "adm:config")
    await _nav_push(state, "adm:vote_prefs")
    await _render_vote_preferences(cb, state)
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:vtemplates:(reject|approve|update_reject|update_approve):\d+$"))
async def on_admin_vote_templates(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    _, _, kind, page = (cb.data or "").split(":")
    await _nav_push(state, cb.data)
    await _render_vote_template_visibility(cb, state, kind, int(page))
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:vtemplate:(reject|approve|update_reject|update_approve):[0-9a-f]{20}:\d+$"))
async def on_admin_vote_template_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    user = cb.from_user
    if not user or not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    _, _, kind, identity, page = (cb.data or "").split(":")
    if kind not in TEMPLATE_CONFIG_KEYS or identity not in {reason_templates.reason_template_id(value) for value in _load_templates(kind)}:
        await cb.answer(_tr(cb, "reason_templates_changed"), show_alert=True)
        return
    hidden = identity not in hidden_moderation_templates(user.id, kind)
    set_moderation_template_hidden(user.id, kind, identity, hidden)
    add_audit_event("admin.vote_template_visibility_changed", actor_id=user.id,
                    actor=user.username or user.full_name or "", details={"kind": kind, "template_id": identity, "hidden": hidden})
    await _render_vote_template_visibility(cb, state, kind, int(page))
    await ack(cb)


@router.callback_query(F.data.startswith("adm:vote_prefs:"))
async def on_admin_vote_preference_change(cb: CallbackQuery, state: FSMContext) -> None:
    user = cb.from_user
    if not user or not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    action = (cb.data or "").removeprefix("adm:vote_prefs:")
    if action == "templates":
        key, value = "show_templates", not moderation_preferences(user.id)["show_templates"]
    elif action in {"mode:buttons", "mode:reply"}:
        key, value = "template_input", action.split(":")[1]
    else:
        await cb.answer()
        return
    set_moderation_preference(user.id, key, value)
    add_audit_event("admin.vote_preference_changed", actor_id=user.id,
                    actor=user.username or user.full_name or "", details={"key": key, "value": value})
    await _render_vote_preferences(cb, state)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:notifs:toggle:"))
async def on_admin_notifications_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    user = cb.from_user
    if not user or not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    key = cb.data.rsplit(":", 1)[-1]
    allowed = {item_key for item_key, _ in _admin_notification_label_items(user.id, lang)}
    if key not in allowed:
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    prefs = admin_notification_preferences(user.id)
    enabled = not prefs.get(key, True)
    set_admin_notification_preference(user.id, key, enabled)
    add_audit_event(
        "admin.notification_preference_changed",
        actor_id=user.id,
        actor=user.username or user.full_name or "",
        details={"key": key, "enabled": enabled},
    )
    await _render_admin_notifications(cb, state)
    await ack(cb)


@router.callback_query(F.data == "adm:stats")
async def on_admin_stats(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:stats")

    users = list_users()
    total = len(users)
    counts: Dict[str, int] = {}
    for user in users:
        user_lang = (user.get("language") or "unknown").lower()
        counts[user_lang] = counts.get(user_lang, 0) + 1

    lines = [_tr(cb, "admin_label_users", total=total), _admin_staff_counts(cb)]
    if counts:
        for user_lang, count in sorted(counts.items()):
            label = user_lang.upper() if user_lang not in {"unknown", ""} else _tr(cb, "admin_label_not_set")
            lines.append(f"{label}: {count}")

    try:
        from bot.services.analytics import top_plugin_opens, total_plugin_opens
        top = top_plugin_opens(10)
        if top:
            lines.append("")
            lines.append(_tr(cb, "admin_stats_plugin_opens", total=total_plugin_opens()))
            for slug, opens in top:
                lines.append(f"• <code>{plain_html(slug)}</code> — {opens}")
    except Exception:
        logger.exception("event=admin_stats.opens_failed")

    msg = await answer(cb, "\n".join(lines), admin_stats_kb(_admin_menu_role(cb), lang=lang), "admin")
    if msg:
        await state.update_data(stats_message_id=msg.message_id)
    await ack(cb)


def _moderation_week_label(value: str, lang: str) -> str:
    from bot.services.moderation_stats import week_dates

    start, end = week_dates(value)
    if lang == "en":
        return f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"
    return f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"


def _moderator_from_week(report: dict, user_id: int) -> dict | None:
    for moderator in report.get("moderators") or []:
        if isinstance(moderator, dict) and int(moderator.get("user_id") or 0) == int(user_id):
            return moderator
    return None


@router.callback_query(F.data.regexp(r"^adm:modstats:(?:current|\d{4}-\d{2}-\d{2})$"))
async def on_admin_moderation_stats(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    from bot.services.moderation_stats import moderation_week, shift_week, sync_moderation_history, week_key

    lang = _lang_for(cb)
    raw_week = cb.data.rsplit(":", 1)[1]
    selected_week = week_key() if raw_week == "current" else week_key(raw_week)
    await _nav_push(state, f"adm:modstats:{selected_week}")
    sync_moderation_history()
    report = moderation_week(selected_week)
    moderators = report.get("moderators") or []
    identities = await _resolve_admin_identities(
        cb.bot,
        [int(item.get("user_id") or 0) for item in moderators],
        lang,
        {
            int(item.get("user_id") or 0): str(item.get("username") or "")
            for item in moderators
        },
    )
    lines = [
        _tr(cb, "admin_moderation_stats_title", period=_moderation_week_label(selected_week, lang)),
        _admin_staff_counts(cb),
        _tr(cb, "admin_moderation_stats_total", count=len(moderators)),
    ]
    if not moderators:
        lines.append(_tr(cb, "admin_moderation_stats_empty"))
    else:
        entries = []
        for item in moderators:
            entries.append(_tr(
                cb,
                "admin_moderation_stats_person",
                username=identities[int(item["user_id"])]["html"],
                admin_id=int(item["user_id"]),
                checked=item["checked"],
                yes=len(item["yes"]),
                no=len(item["no"]),
                decisions=len(item["decisions"]),
            ))
        lines.append("\n<blockquote expandable>" + "\n".join(entries) + "</blockquote>")
    current = week_key()
    next_week = shift_week(selected_week, 1) if selected_week < current else None
    items = [
        (
            item["user_id"],
            identities[int(item["user_id"])]["button"],
            item["checked"],
            len(item["yes"]),
            len(item["no"]),
        )
        for item in moderators
    ]
    await answer(
        cb,
        "\n".join(lines),
        moderation_week_kb(items, selected_week, shift_week(selected_week, -1), next_week, lang),
        "admin",
    )
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:modstats:user:\d{4}-\d{2}-\d{2}:\d+$"))
async def on_admin_moderation_person(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    from bot.services.moderation_stats import moderation_week

    lang = _lang_for(cb)
    parts = cb.data.split(":")
    selected_week = parts[3]
    moderator = _moderator_from_week(moderation_week(selected_week), int(parts[4]))
    if not moderator:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    await _nav_push(state, cb.data)
    identity = (await _resolve_admin_identities(
        cb.bot,
        [int(moderator["user_id"])],
        lang,
        {int(moderator["user_id"]): str(moderator.get("username") or "")},
    ))[int(moderator["user_id"])]
    lines = [
        _tr(
            cb,
            "admin_moderation_stats_detail",
            username=identity["html"],
            admin_id=int(moderator["user_id"]),
            period=_moderation_week_label(selected_week, lang),
            checked=moderator["checked"],
            yes=len(moderator["yes"]),
            no=len(moderator["no"]),
        )
    ]
    for value, title_key in (("yes", "admin_moderation_stats_yes"), ("no", "admin_moderation_stats_no")):
        lines.append(_tr(cb, title_key))
        events = moderator[value]
        if not events:
            lines.append(_tr(cb, "admin_moderation_stats_none"))
        for event in events:
            lines.append(_tr(
                cb,
                "admin_moderation_stats_vote",
                request=plain_html(event.get("request_name") or event.get("request_id") or "—"),
                reason=plain_html(reason_templates.localize_reason(event.get("reason") or "—", lang)),
            ))
    lines.append(_tr(cb, "admin_moderation_stats_decisions"))
    if not moderator["decisions"]:
        lines.append(_tr(cb, "admin_moderation_stats_none"))
    for event in moderator["decisions"]:
        lines.append(_tr(
            cb,
            "admin_moderation_stats_decision",
            request=plain_html(event.get("request_name") or event.get("request_id") or "—"),
            status=plain_html(_tr(cb, f"request_status_{event.get('value')}") if f"request_status_{event.get('value')}" in TEXTS else event.get("value") or "—"),
        ))
    await answer(cb, "\n".join(lines), moderation_person_kb(selected_week, lang), "admin")
    await ack(cb)


@router.callback_query(F.data == "adm:examples:publish")
async def on_admin_publish_examples(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    from bot.services.example_media import process_pending_example_media

    await _nav_push(state, "adm:config")
    await _nav_push(state, "adm:maint")
    await state.set_state(AdminFlow.menu)
    result = await process_pending_example_media(cb.bot, limit=100)
    await answer(
        cb,
        _tr(
            cb,
            "admin_examples_result",
            published=result.get("published", 0),
            pending=result.get("pending", 0),
            uncertain=result.get("uncertain", 0),
        ),
        admin_maintenance_kb(_lang_for(cb)),
        "admin",
    )
    await ack(cb)


@router.callback_query(F.data == "adm:config")
async def on_admin_config(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:config")
    await _render_config(cb, state)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:config_section:"))
async def on_admin_config_section(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    section = cb.data.split(":")[2]
    await _nav_push(state, f"adm:config_section:{section}")
    await state.set_state(AdminFlow.menu)

    if section == "admins":
        text = _config_section_text("admins", lang)
        kb = admin_config_admins_kb(lang=lang)
    elif section == "channels":
        text = _config_section_text("channels", lang)
        kb = admin_config_channels_kb(lang=lang)
    elif section == "moderation":
        text = _config_section_text("moderation", lang)
        kb = admin_config_moderation_kb(lang=lang)
    elif section == "other":
        text = _config_section_text("other", lang)
        kb = admin_config_other_kb(lang=lang)
    else:
        text = _tr(cb, "admin_unknown_setting")
        kb = admin_config_kb(lang=lang)

    await answer(cb, text, kb, "admin")
    await ack(cb)


@router.callback_query(F.data.startswith("adm:config:"))
async def on_admin_config_edit(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    field = cb.data.split(":", 2)[2]
    if field in {"admins_super", "admins_plugins", "admins_icons"}:
        await _nav_push(state, f"adm:config:{field}")
        await _render_admins_manage(cb, state, field)
    elif field == "checked_on_version":
        cfg = get_config()
        current = str(cfg.get("checked_on_version") or "").strip() or "—"
        await state.update_data(config_field=field, config_message_id=cb.message.message_id if cb.message else None)
        await state.set_state(AdminFlow.editing_config)
        await answer(
            cb,
            f"{_tr(cb, 'admin_prompt_checked_on_version')}\n\n{_tr(cb, 'admin_current_value', value=plain_html(current))}",
            admin_config_kb(lang=lang),
            "profile",
        )
    elif field in _CONFIG_FIELD_LABEL_KEYS:
        cfg = get_config()
        current = _format_config_value(_config_current_value(cfg, field))
        label = t(_CONFIG_FIELD_LABEL_KEYS[field], lang)
        await state.update_data(config_field=field, config_message_id=cb.message.message_id if cb.message else None)
        await state.set_state(AdminFlow.editing_config)
        prompt_key = "admin_prompt_config_list" if field in _CONFIG_LIST_FIELDS else "admin_prompt_config_value"
        if field in _ARCHIVE_CHANNEL_FIELDS:
            prompt_key = "admin_prompt_archive_channel"
        await answer(
            cb,
            _tr(cb, prompt_key, name=label, current=html.escape(current)),
            admin_cancel_kb(lang),
            "profile",
        )
    elif field == "channel":
        await state.update_data(config_field=field, config_message_id=cb.message.message_id if cb.message else None)
        await state.set_state(AdminFlow.editing_config)
        await answer(
            cb,
            f"{_tr(cb, 'admin_prompt_channel')}\n{_tr(cb, 'admin_prompt_channel_example')}",
            admin_config_kb(lang=lang),
            "profile",
        )
    else:
        await state.update_data(config_field=field, config_message_id=cb.message.message_id if cb.message else None)
        await state.set_state(AdminFlow.editing_config)
        await answer(cb, _tr(cb, "admin_unknown_setting"), admin_config_kb(lang=lang), "admin")
    await ack(cb)


@router.message(AdminFlow.editing_config)
async def on_admin_config_value(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "super"):
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer(
            _tr(message, "need_text"),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
        return

    data = await state.get_data()
    field = data.get("config_field")
    config = get_config()

    if field in {"admins_super", "admins_plugins", "admins_icons"}:
        try:
            admin_id = int(text)
        except ValueError:
            await message.answer(
                _tr(message, "admin_bad_id"),
                disable_web_page_preview=True,
                parse_mode=ParseMode.HTML,
            )
            return
        current = [int(x) for x in (config.get(field, []) or []) if str(x).isdigit()]
        updated = sorted(set(current + [admin_id]))
        config[field] = updated
        save_config(config)
        invalidate("config")
        await state.set_state(AdminFlow.menu)
        view_text, keyboard = await _admins_manage_view(message.bot, message, field, updated, lang)
        config_message_id = data.get("config_message_id")
        if config_message_id:
            try:
                await message.bot.edit_message_text(
                    view_text,
                    chat_id=message.chat.id,
                    message_id=config_message_id,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                return
            except Exception:
                pass
        sent_msg = await answer(message, view_text, keyboard, "admin")
        if sent_msg:
            await state.update_data(config_message_id=sent_msg.message_id)
        return
    elif field == "checked_on_version":
        config[field] = text
        save_config(config)
        invalidate("config")
        await state.update_data(config_field=None, config_message_id=None)
        await state.set_state(AdminFlow.menu)
        await answer(message, _config_section_text("other", lang), admin_config_other_kb(lang=lang), "admin")
        return
    elif field in _CONFIG_FIELD_LABEL_KEYS:
        try:
            value = _parse_config_value(field, text)
        except ValueError:
            await message.answer(
                _tr(message, "admin_bad_id"),
                disable_web_page_preview=True,
                parse_mode=ParseMode.HTML,
            )
            return

        if field in _ARCHIVE_CHANNEL_FIELDS and value:
            from bot.services.archive import validate_archive_channel

            try:
                valid = await validate_archive_channel(message.bot, value)
            except Exception:
                valid = False
            if not valid:
                await message.answer(_tr(message, "admin_archive_channel_invalid"))
                return
            config = get_config()
            other_field = next(iter(_ARCHIVE_CHANNEL_FIELDS - {field}))
            if str(_config_get_path(config, other_field)) == str(value):
                await message.answer(_tr(message, "admin_archive_channels_must_differ"))
                return

        _config_set_path(config, field, value)
        save_config(config)
        invalidate("config")
        if field in _ARCHIVE_CHANNEL_FIELDS:
            from core.storage import flush_all

            add_audit_event("admin.archive_channel_changed", actor_id=_actor_id(message), actor=_admin_actor_label(message), details={"field": field, "channel_id": value})
            await flush_all()
        else:
            add_audit_event("admin.config_changed", actor_id=_actor_id(message), actor=_admin_actor_label(message), details={"field": field, "value": value})

        if field.startswith("moderation."):
            section = "moderation"
            kb = admin_config_moderation_kb(lang=lang)
        else:
            section = "channels"
            kb = admin_config_channels_kb(lang=lang)

        await state.update_data(config_field=None, config_message_id=None)
        await state.set_state(AdminFlow.menu)
        await answer(
            message,
            f"{_tr(message, 'admin_config_updated')}\n\n{_config_section_text(section, lang)}",
            kb,
            "admin",
        )
        return
    elif field == "channel":
        parts = text.split()
        if len(parts) < 2:
            await message.answer(
                _tr(message, "admin_channel_min_parts"),
                disable_web_page_preview=True,
                parse_mode=ParseMode.HTML,
            )
            return
        channel_id = parts[0]
        username = parts[1].lstrip("@")
        title = parts[2] if len(parts) > 2 else config.get("channel", {}).get("title", "")
        publish_channel = parts[3] if len(parts) > 3 else config.get("publish_channel", "")
        try:
            channel_id_value = int(channel_id)
        except ValueError:
            await message.answer(
                _tr(message, "admin_bad_channel_id"),
                disable_web_page_preview=True,
                parse_mode=ParseMode.HTML,
            )
            return
        config.setdefault("channel", {})
        config["channel"]["id"] = channel_id_value
        config["channel"]["username"] = username
        config["channel"]["title"] = title
        config["publish_channel"] = publish_channel
        save_config(config)
        invalidate("config")
        await message.answer(
            _tr(message, "admin_channel_updated"),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(
            _tr(message, "admin_unknown_setting"),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )

    config_message_id = data.get("config_message_id")
    await state.update_data(config_field=None, config_message_id=None)
    await state.set_state(AdminFlow.menu)
    if config_message_id:
        try:
            await message.bot.edit_message_text(
                _tr(message, "admin_title"),
                chat_id=message.chat.id,
                message_id=config_message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_menu_kb(_admin_menu_role(message), lang=lang),
                disable_web_page_preview=True,
            )
        except Exception:
            await _reset_menu_navigation(state)
            await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")
    else:
        await _reset_menu_navigation(state)
        await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")


@router.callback_query(F.data == "adm:broadcast")
async def on_admin_broadcast(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:broadcast")
    await _render_broadcast_kind(cb, state)
    await ack(cb)


@router.callback_query(F.data.in_({"adm:broadcast:all", "adm:broadcast:moderation"}))
async def on_admin_broadcast_kind(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    kind = cb.data.rsplit(":", 1)[-1]
    await _nav_push(state, "adm:broadcast")
    await state.update_data(broadcast_kind=kind)
    await _render_broadcast_enter(cb, state)
    await ack(cb)


@router.message(AdminFlow.entering_broadcast)
async def on_admin_broadcast_message(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "super"):
        return

    text = telegram_html(message.html_text or message.text or "")
    if not text:
        await message.answer(
            _tr(message, "need_text"),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
        return

    data = await state.get_data()
    kind = str(data.get("broadcast_kind") or "all")
    await state.update_data(broadcast_text=text)
    await state.set_state(AdminFlow.confirming_broadcast)
    broadcast_message_id = (await state.get_data()).get("broadcast_message_id")
    if broadcast_message_id:
        try:
            await message.bot.edit_message_text(
                f"<b>{_tr(message, 'admin_btn_broadcast')}</b>\n\n{text}\n\n{_tr(message, 'admin_broadcast_confirm_moderation' if kind == 'moderation' else 'admin_broadcast_confirm')}",
                chat_id=message.chat.id,
                message_id=broadcast_message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_broadcast_confirm_kb(lang=lang),
                disable_web_page_preview=True,
            )
        except Exception:
            sent_msg = await message.answer(
                f"<b>{_tr(message, 'admin_btn_broadcast')}</b>\n\n{text}\n\n{_tr(message, 'admin_broadcast_confirm_moderation' if kind == 'moderation' else 'admin_broadcast_confirm')}",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_broadcast_confirm_kb(lang=lang),
                disable_web_page_preview=True,
            )
            if sent_msg:
                await state.update_data(broadcast_message_id=sent_msg.message_id)
    else:
        sent_msg = await message.answer(
            f"<b>{_tr(message, 'admin_btn_broadcast')}</b>\n\n{text}\n\n{_tr(message, 'admin_broadcast_confirm_moderation' if kind == 'moderation' else 'admin_broadcast_confirm')}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_broadcast_confirm_kb(lang=lang),
            disable_web_page_preview=True,
        )
        if sent_msg:
            await state.update_data(broadcast_message_id=sent_msg.message_id)


@router.callback_query(F.data == "adm:broadcast:cancel")
async def on_admin_broadcast_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await _reset_menu_navigation(state)
    await answer(cb, _tr(cb, "admin_title"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")
    await cb.answer(_tr(cb, "admin_broadcast_cancelled"))


@router.callback_query(F.data == "adm:broadcast:confirm")
async def on_admin_broadcast_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    data = await state.get_data()
    text = data.get("broadcast_text", "")
    if not text:
        await cb.answer(_tr(cb, "admin_broadcast_no_text"), show_alert=True)
        return

    kind = str(data.get("broadcast_kind") or "all")
    if kind == "moderation":
        recipients = sorted(get_admins())
    else:
        recipients = sorted({
            int(user.get("user_id"))
            for user in list_users()
            if user.get("user_id") and not user.get("banned") and is_broadcast_enabled(int(user["user_id"]))
        })
    from bot.services.broadcast import claim_broadcast

    run = await claim_broadcast(
        kind, len(recipients), text=text, actor_id=cb.from_user.id,
        actor=f"@{cb.from_user.username}" if cb.from_user.username else cb.from_user.full_name,
    )
    if not run:
        await cb.answer(_tr(cb, "admin_broadcast_already_running"), show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminFlow.menu)
    progress_chat_id = cb.message.chat.id if cb.message else 0
    progress_message_id = cb.message.message_id if cb.message else 0
    try:
        await cb.message.edit_text(
            _broadcast_progress_text(kind, run.total, run.current, run.sent, run.failed, lang),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_broadcast_progress_kb(lang),
            disable_web_page_preview=True,
        )
    except Exception:
        sent_message = await answer(
            cb,
            _broadcast_progress_text(kind, run.total, run.current, run.sent, run.failed, lang),
            admin_broadcast_progress_kb(lang),
            "admin",
        )
        if sent_message:
            progress_chat_id = sent_message.chat.id
            progress_message_id = sent_message.message_id
    spawn_background(_run_broadcast_delivery(cb.bot, progress_chat_id, progress_message_id, recipients, text, run, lang))
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:broadcast_history:\d+$"))
async def on_admin_broadcast_history(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await ack(cb, _tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, "adm:broadcast")
    await _nav_push(state, cb.data)
    await _render_broadcast_history(cb, state, int(cb.data.rsplit(":", 1)[-1]))
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:broadcast_run:[0-9a-f]{32}$"))
async def on_admin_broadcast_record(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await ack(cb, _tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, cb.data)
    await _render_broadcast_record(cb, state, cb.data.rsplit(":", 1)[-1])
    await ack(cb)


@router.callback_query(F.data == "adm:broadcast:progress")
async def on_admin_broadcast_progress(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    from bot.services.broadcast import active_broadcast

    run = active_broadcast()
    if not run:
        await cb.answer(_tr(cb, "admin_broadcast_cancelled"), show_alert=True)
        return
    from bot.services.broadcast import show_progress_view

    if cb.message and not run.finished:
        show_progress_view(cb.message.chat.id, cb.message.message_id)
    if run.finished:
        await _reset_menu_navigation(state)
    try:
        await cb.message.edit_text(
            _broadcast_progress_text(run.kind, run.total, run.current, run.sent, run.failed, _lang_for(cb)),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_broadcast_progress_kb(_lang_for(cb)) if not run.finished else admin_menu_kb("super", lang=_lang_for(cb)),
        )
    except Exception:
        pass
    await ack(cb)


@router.callback_query(F.data.startswith("adm:queue:"))
async def on_admin_queue(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = cb.data.split(":")
    queue_type = parts[2]
    if queue_type in {"plugins", "new", "update"}:
        if not _ensure_admin_role(cb, "plugins"):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return
    else:
        if not _ensure_admin(cb):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return
    page = int(parts[3]) if len(parts) > 3 else 0

    await _nav_push(state, f"adm:queue:{queue_type}:{page}")

    visible_requests = [r for r in (list(get_requests(status="pending")) + list(get_requests(status="error"))) if not _is_appeal(r)]

    if queue_type == "approved":
        requests = [r for r in get_requests(status="approved") if not _is_appeal(r)
                    and not (r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon"))]
    elif queue_type == "update":
        requests = [
            r for r in visible_requests
            if r.get("type") == "update"
            if not (r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon"))
        ]
    elif queue_type == "plugins":
        requests = [
            r for r in visible_requests
            if not (r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon"))
            and r.get("type") != "update"
        ]
    else:
        requests = [
            r for r in visible_requests
            if not (r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon"))
        ]

    if not requests:
        if queue_type == "approved":
            await state.set_state(AdminFlow.menu)
            await answer(cb, _tr(cb, "admin_approved_empty"), admin_plugins_section_kb(lang=lang, role=_admin_menu_role(cb)), "admin")
            await ack(cb)
            return
        await cb.answer(_tr(cb, "admin_queue_empty"), show_alert=True)
        return

    viewer_id = cb.from_user.id if cb.from_user else 0
    requests.sort(key=lambda r: (
        1 if _is_resubmit_appeal(r) else 0,
        1 if _has_my_vote(r, viewer_id) else 0,
        str(r.get("submitted_at") or ""),
    ))

    total = len(requests)
    total_pages = math.ceil(total / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = requests[start : start + PAGE_SIZE]

    items: List[tuple[str, str] | tuple[str, str, str | None]] = []
    for entry in page_items:
        payload = entry.get("payload", {})
        if payload.get("submission_type") == "icon" or payload.get("icon"):
            icon = payload.get("icon", {})
            name = icon.get("name", "?")
            version = icon.get("version", "")
            row_icon = None
        else:
            plugin = payload.get("plugin", {})
            name = plugin.get("name", "?")
            version = plugin.get("version", "")
            row_icon = None
        request_type = entry.get("type")
        if request_type == "delete":
            name = payload.get("delete_slug") or name
            prefix = t("request_kind_delete", lang) + ": "
            row_icon = "delete"
        elif request_type == "update":
            prefix = t("request_kind_update", lang) + ": "
            row_icon = "updates"
        else:
            prefix = ""
        if request_type != "delete":
            my = _my_vote(entry, viewer_id)
            if my in {"yes", "no"}:
                row_icon = my
        if entry.get("status") == "error" or payload.get("last_publish_error"):
            prefix = f"Ошибка: {prefix}"
            row_icon = row_icon or "warning"
        label = f"{name} v{version}" if version else f"{name}"
        label = f"{prefix}{label}"
        items.append((label, entry["id"], row_icon))

    if queue_type == "approved":
        title = _tr(cb, "admin_approved_title")
    elif queue_type == "update":
        title = _tr(cb, "admin_queue_title_updates")
    elif queue_type == "new":
        title = _tr(cb, "admin_queue_title_new")
    elif queue_type == "plugins":
        title = _tr(cb, "admin_queue_title_plugins")
    else:
        title = _tr(cb, "admin_queue_title_all")
    caption = f"<b>{title}</b>\n{_tr(cb, 'admin_page', current=page + 1, total=total_pages)}"
    await answer(cb, caption, admin_queue_kb(items, page, total_pages, queue_type, lang=lang), "moderation")
    await ack(cb)


@router.callback_query(F.data.startswith("adm:banned:"))
async def on_admin_banned(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    page = int(cb.data.split(":")[2])
    await _nav_push(state, "adm:stats")
    await _nav_push(state, f"adm:banned:{page}")
    await _render_banned(cb, state, page)
    await ack(cb)


async def _render_banned(target: CallbackQuery | Message, state: FSMContext, page: int) -> None:
    lang = _lang_for(target)
    banned = get_banned_users()
    total = len(banned)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = banned[start : start + PAGE_SIZE]
    items = [(f"{user.get('username') or user['user_id']}", user["user_id"]) for user in page_items]
    if banned:
        caption = f"<b>{_tr(target, 'admin_btn_banned')}</b>\n{_tr(target, 'admin_page', current=page + 1, total=total_pages)}"
    else:
        caption = f"<b>{_tr(target, 'admin_btn_banned')}</b>\n\n{_tr(target, 'admin_banned_empty')}"
    await state.set_state(AdminFlow.menu)
    await state.update_data(banned_page=page)
    await answer(target, caption, admin_banned_kb(items, page, total_pages, lang=lang), "admin")


async def _execute_ban(bot, user_id: int, delete_plugins: bool, actor_label: str, actor_id) -> int:
    ban_user(user_id, reason="Заблокирован администратором")
    add_audit_event(
        "moderation.ban",
        actor_id=actor_id,
        actor=actor_label,
        details={"user_id": user_id, "deleted_plugins": delete_plugins},
    )
    try:
        await bot.send_message(
            user_id, t("user_banned_by_admin", get_lang(user_id)),
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        )
    except Exception:
        pass
    removed_count = 0
    if delete_plugins:
        from core.user_store import get_user
        user_data = get_user(user_id)
        removal = await remove_user_content(user_id, user_data.get("username", ""))
        removed_count = len(removal["removed_plugins"]) + len(removal["removed_icons"])
    return removed_count


async def _resolve_ban_target(bot, raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.lstrip("-").isdigit():
        return int(raw)
    uname = raw.lstrip("@").lower()
    for u in list_users():
        if str(u.get("username") or "").lower() == uname:
            try:
                return int(u.get("user_id"))
            except (TypeError, ValueError):
                continue
    try:
        chat = await bot.get_chat("@" + uname)
        return int(chat.id)
    except Exception:
        return None


@router.callback_query(F.data == "adm:ban_manual")
async def on_admin_ban_manual(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, "adm:ban_manual")
    await state.set_state(AdminFlow.entering_ban_user)
    await answer(cb, _tr(cb, "admin_ban_manual_prompt"), admin_cancel_kb(_lang_for(cb)), "admin")
    await ack(cb)


@router.message(AdminFlow.entering_ban_user)
async def on_admin_ban_manual_input(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "super"):
        return
    user_id = await _resolve_ban_target(message.bot, message.text or "")
    if not user_id:
        await message.answer(_tr(message, "admin_ban_manual_not_found"))
        return
    if user_id in get_admins():
        await message.answer(_tr(message, "admin_ban_manual_is_admin"))
        return
    await state.set_state(AdminFlow.menu)
    await answer(message, _tr(message, "admin_ban_manual_confirm", user_id=user_id),
                 admin_confirm_ban_user_kb(user_id, lang=lang), "admin")


@router.callback_query(F.data.regexp(r"^adm:banuid:-?\d+:(del|keep)$"))
async def on_admin_ban_uid(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = cb.data.split(":")
    user_id = int(parts[2])
    delete_plugins = parts[3] == "del"
    if user_id in get_admins():
        await cb.answer(_tr(cb, "admin_ban_manual_is_admin"), show_alert=True)
        return

    removed_count = await _execute_ban(
        cb.bot, user_id, delete_plugins,
        _admin_actor_label(cb), cb.from_user.id if cb.from_user else None,
    )
    extra = (f"\n\nУдалено плагинов/иконок: {removed_count}" if removed_count
             else "\n\nПлагины оставлены в канале." if not delete_plugins else "")
    await _reset_menu_navigation(state)
    await answer(
        cb,
        _tr(cb, "admin_user_banned", user_id=user_id) + extra,
        admin_menu_kb(_admin_menu_role(cb), lang=lang),
        "profile",
    )
    await ack(cb)


@router.callback_query(F.data.startswith("adm:unban:"))
async def on_admin_unban(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    user_id = int(cb.data.split(":")[2])
    unban_user(user_id)
    add_audit_event("moderation.unban", actor_id=_actor_id(cb), actor=_admin_actor_label(cb), details={"user_id": user_id})
    await cb.answer(_tr(cb, "admin_user_unbanned"), show_alert=True)

    banned = get_banned_users()
    if banned:
        items = [(f"{user.get('username') or user['user_id']}", user["user_id"]) for user in banned[:PAGE_SIZE]]
        total_pages = math.ceil(len(banned) / PAGE_SIZE)
        await answer(
            cb,
            f"<b>{_tr(cb, 'admin_btn_banned')}</b>",
            admin_banned_kb(items, 0, total_pages, lang=lang),
            "profile",
        )
    else:
        await _reset_menu_navigation(state)
        await answer(cb, _tr(cb, "admin_title"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")


@router.callback_query(F.data.startswith("adm:user_info:"))
async def on_admin_user_info(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    try:
        user_id = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer()
        return
    info = next((u for u in get_banned_users() if u.get("user_id") == user_id), None)
    lines = [f"ID: {user_id}"]
    if info:
        if info.get("username"):
            lines.append(f"@{info['username']}")
        reason = str(info.get("ban_reason") or "").strip()
        if reason:
            lines.append(f"{_tr(cb, 'admin_user_info_reason')}: {reason}")
    await cb.answer("\n".join(lines), show_alert=True)


@router.callback_query(F.data == "adm:link_author")
async def on_admin_link_author(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await state.set_state(AdminFlow.searching_plugin)
    await state.update_data(search_message_id=cb.message.message_id if cb.message else None, search_purpose="link_plugin")
    msg = await answer(cb, _tr(cb, "admin_prompt_search_plugin"), admin_cancel_kb(lang), "admin")
    if msg:
        await state.update_data(search_message_id=msg.message_id)
    await ack(cb)


@router.callback_query(F.data == "adm:edit_plugins")
async def on_admin_edit_plugins(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await state.set_state(AdminFlow.searching_plugin)
    await state.update_data(search_message_id=cb.message.message_id if cb.message else None)
    msg = await answer(
        cb,
        _tr(cb, "admin_prompt_search_plugin"),
        admin_cancel_kb(lang),
        "profile",
    )
    if msg:
        await state.update_data(search_message_id=msg.message_id)
    await ack(cb)


@router.message(AdminFlow.searching_plugin)
async def on_admin_search_plugins(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "plugins"):
        return

    if (message.text or "").strip().startswith("/"):
        return

    query = (message.text or "").strip().lower()
    if not query:
        await message.answer(
            _tr(message, "need_text"),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
        return

    plugins = list_published_plugins()
    filtered = [
        p
        for p in plugins
        if query in (p.get("slug", "").lower())
        or query in _localized_name(p, lang).lower()
        or query in ((p.get("ru") or {}).get("name") or "").lower()
        or query in ((p.get("en") or {}).get("name") or "").lower()
    ]

    await state.update_data(edit_plugins_list=[p.get("slug") for p in filtered])

    data = await state.get_data()
    purpose = data.get("search_purpose")
    select_prefix = "adm:edit_select" if purpose != "link_plugin" else "adm:link_select"
    list_prefix = "adm:edit_list" if purpose != "link_plugin" else "adm:link_list"

    if not filtered:
        await message.answer(
            _tr(message, "admin_search_nothing_found"),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
        return

    items = [(_localized_name(p, lang), p.get("slug")) for p in filtered[:PAGE_SIZE]]
    total_pages = math.ceil(len(filtered) / PAGE_SIZE)
    data = await state.get_data()
    message_id = data.get("search_message_id")
    if message_id:
        try:
            await message.bot.edit_message_text(
                _tr(message, "admin_search_results_title"),
                chat_id=message.chat.id,
                message_id=message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_plugins_list_kb(
                    items,
                    0,
                    total_pages,
                    select_prefix=select_prefix,
                    list_prefix=list_prefix,
                    lang=lang,
                ),
                disable_web_page_preview=True,
            )
            await state.update_data(search_message_id=message_id)
            return
        except Exception:
            pass

    sent_msg = await message.answer(
        _tr(message, "admin_search_results_title"),
        reply_markup=admin_plugins_list_kb(
            items,
            0,
            total_pages,
            select_prefix=select_prefix,
            list_prefix=list_prefix,
            lang=lang,
        ),
    )
    if sent_msg:
        await state.update_data(search_message_id=sent_msg.message_id)


@router.callback_query(F.data.startswith("adm:edit_list:"))
async def on_admin_edit_list(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    page = int(cb.data.split(":")[2])
    data = await state.get_data()
    slugs = data.get("edit_plugins_list", [])
    plugins = [find_plugin_by_slug(slug) for slug in slugs]
    plugins = [p for p in plugins if p]

    if not plugins:
        await cb.answer(_tr(cb, "catalog_empty"), show_alert=True)
        return

    total_pages = math.ceil(len(plugins) / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = plugins[start : start + PAGE_SIZE]

    items = [(_localized_name(p, lang), p.get("slug")) for p in page_items]
    await answer(
        cb,
        _tr(cb, "admin_search_results_title"),
        admin_plugins_list_kb(
            items,
            page,
            total_pages,
            select_prefix="adm:edit_select",
            list_prefix="adm:edit_list",
            lang=lang,
        ),
        "profile",
    )
    await ack(cb)


@router.callback_query(F.data.startswith("adm:edit_select:"))
async def on_admin_edit_select(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    slug = decode_slug(cb.data.split(":")[2])
    plugin = find_plugin_by_slug(slug)
    if not plugin:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    authors = plugin.get("authors", {})
    raw_blocks = plugin.get("raw_blocks", {}) or {}
    raw_ru = raw_blocks.get("ru") if isinstance(raw_blocks.get("ru"), dict) else {}
    raw_en = raw_blocks.get("en") if isinstance(raw_blocks.get("en"), dict) else {}
    raw_locale = raw_blocks.get(lang) if isinstance(raw_blocks.get(lang), dict) else {}
    plugin_locale = _localized_block(plugin, lang)
    edit_payload = {
        "plugin": {
            "name": plugin_locale.get("name") or "",
            "author": raw_locale.get("author") or raw_ru.get("author") or raw_en.get("author") or _localized_author(authors, lang),
            "description": plugin_locale.get("description") or "",
            "version": plugin_locale.get("version") or "",
            "min_version": plugin.get("requirements", {}).get("min_version") or "",
            "has_ui_settings": plugin.get("settings", {}).get("has_ui", False),
            "file_path": plugin.get("file", {}).get("file_path") or "",
            "file_id": plugin.get("file", {}).get("file_id"),
        },
        "description_ru": plugin.get("ru", {}).get("description") or "",
        "description_en": plugin.get("en", {}).get("description") or "",
        "usage_ru": plugin.get("ru", {}).get("usage") or "",
        "usage_en": plugin.get("en", {}).get("usage") or "",
        **category_fields(plugin),
    }

    draft_text = build_channel_post({"payload": edit_payload})
    await state.update_data(
        edit_slug=slug,
        edit_payload=edit_payload,
        **category_fields(edit_payload),
        category_selection=None,
        edit_message_id=cb.message.message_id if cb.message else None,
    )
    await state.set_state(AdminFlow.editing_catalog_plugin)
    msg = await answer(
        cb,
        draft_text,
        draft_edit_kb(
            "adm_edit",
            _tr(cb, "admin_submit_update"),
            include_back=True,
            include_file=True,
            include_delete=True,
            lang=lang,
        ),
        "profile",
    )
    if msg:
        await state.update_data(edit_message_id=msg.message_id)
    await ack(cb)


@router.callback_query(F.data == "adm_edit:delete")
async def on_admin_catalog_delete(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    data = await state.get_data()
    slug = data.get("edit_slug")
    if not slug:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    plugin_entry = find_plugin_by_slug(slug) or {}
    await state.set_state(AdminFlow.entering_catalog_delete_reason)
    await state.update_data(delete_slug=slug, delete_reason="")
    await answer(
        cb,
        _tr(cb, "admin_delete_ask_reason", name=plain_html(plugin_display_name(plugin_entry))),
        admin_cancel_kb(lang),
        None,
    )
    await ack(cb)


@router.message(AdminFlow.entering_catalog_delete_reason)
async def on_admin_catalog_delete_reason(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "plugins"):
        return

    reason = strip_blockquote_tags(telegram_html(message.html_text or message.text or "")).strip()
    if not reason:
        await message.answer(_tr(message, "delete_reason_required"), parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    slug = data.get("delete_slug")
    if not slug:
        await state.set_state(AdminFlow.menu)
        await _reset_menu_navigation(state)
        await answer(message, _tr(message, "not_found"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")
        return

    plugin_entry = find_plugin_by_slug(slug) or {}
    await state.update_data(delete_reason=reason)
    await state.set_state(AdminFlow.menu)
    await answer(
        message,
        _tr(
            message,
            "admin_delete_confirm_with_reason",
            name=plain_html(plugin_display_name(plugin_entry)),
            reason=reason,
        ),
        admin_confirm_delete_plugin_kb(slug, lang=lang),
        None,
    )


@router.callback_query(F.data.startswith("adm_edit:delete_confirm:"))
async def on_admin_catalog_delete_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    slug = decode_slug(cb.data.split(":")[2])
    plugin_entry = find_plugin_by_slug(slug)
    if not plugin_entry:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    data = await state.get_data()
    delete_reason = str(data.get("delete_reason") or "").strip()
    if not delete_reason:
        await cb.answer(_tr(cb, "delete_reason_required"), show_alert=True)
        return

    message_id = plugin_entry.get("channel_message", {}).get("message_id")
    if not message_id:
        await cb.answer(_tr(cb, "admin_channel_message_not_found"), show_alert=True)
        return

    try:
        await cb.answer(_tr(cb, "admin_delete_progress"))
    except Exception:
        pass

    try:
        removed = await remove_published_plugin(slug)
    except Exception:
        logger.exception("event=delete.plugin_failed slug=%s", slug)
        await cb.answer(_tr(cb, "admin_delete_failed"), show_alert=True)
        return

    add_audit_event("moderation.plugin_deleted", actor_id=_actor_id(cb), actor=_admin_actor_label(cb),
                    details={"item": slug, "reason": delete_reason})
    spawn_background(notify_plugin_authors_removed(cb.bot, removed, delete_reason))
    await state.set_state(AdminFlow.menu)
    await _reset_menu_navigation(state)
    await answer(cb, _tr(cb, "admin_title"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")
    await ack(cb, _tr(cb, "admin_deleted_success"), show_alert=True)


@router.callback_query(F.data.startswith("adm:plugins_list:"))
async def on_admin_plugins_list(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    page = int(cb.data.split(":")[2])
    plugins = list_published_plugins()

    total = len(plugins)
    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = plugins[start : start + PAGE_SIZE]

    items = [(_localized_name(plugin, lang), plugin.get("slug")) for plugin in page_items]
    await answer(cb, _tr(cb, "admin_select_plugin"), admin_plugins_list_kb(items, page, total_pages, lang=lang), "admin")
    await ack(cb)


@router.message(AdminFlow.linking_author_user)
async def on_admin_link_author_user(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "plugins"):
        return

    data = await state.get_data()
    slug = data.get("link_plugin_slug")
    if not slug:
        await state.set_state(AdminFlow.menu)
        return
    text = (message.text or "").strip()
    parts = text.split()
    if not parts:
        await message.answer(
            _tr(message, "admin_enter_valid_user_id"),
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        user_id = int(parts[0])
    except ValueError:
        await message.answer(
            _tr(message, "admin_enter_valid_user_id"),
            parse_mode=ParseMode.HTML,
        )
        return

    username = parts[1] if len(parts) > 1 else ""
    username = username.lstrip("@")

    success = add_submitter_to_plugin(slug, user_id, username)

    if success:
        await message.answer(_tr(message, "admin_author_linked"))
    else:
        await message.answer(
            _tr(message, "admin_link_failed"),
            parse_mode=ParseMode.HTML,
        )

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await _reset_menu_navigation(state)
    await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")


@router.callback_query(F.data.startswith("adm:review:"))
async def on_admin_review(cb: CallbackQuery, state: FSMContext) -> None:
    request_id = cb.data.split(":", 2)[2]
    entry = get_request_by_id(request_id)
    if not entry:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    if not _ensure_request_role(cb, entry):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, f"adm:review:{request_id}")
    await _render_review(cb, state, request_id, send_document=True)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:actions:"))
async def on_admin_actions(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry and not _ensure_request_role(cb, entry):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, f"adm:actions:{request_id}")

    await cb.message.edit_reply_markup(reply_markup=admin_actions_kb(request_id, allow_ban=True, lang=lang))
    await ack(cb)


@router.callback_query(F.data.startswith("adm:back_review:"))
async def on_admin_back_review(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry and not _ensure_request_role(cb, entry):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    payload = entry.get("payload", {}) if entry else {}
    user_id = int(payload.get("user_id") or 0)

    entry = get_request_by_id(request_id)
    if entry and entry.get("type") == "delete":
        kb = admin_review_kb(
            request_id,
            user_id,
            submit_label=_tr(cb, "admin_submit_delete"),
            submit_callback=f"adm:delete:{request_id}",
            lang=lang,
            allow_publish=_is_super_admin(cb),
            allow_vote=False,
            media_count=len(_comment_media_of_entry(entry)),
        )
    else:
        kb = admin_review_kb(request_id, user_id, lang=lang, allow_publish=_is_super_admin(cb),
                             allow_approve=can_approve_request(entry), allow_vote=can_accept_vote(entry),
                             media_count=len(_comment_media_of_entry(entry)))

    await cb.message.edit_reply_markup(reply_markup=kb)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:delete:"))
async def on_admin_delete(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if not entry or entry.get("type") != "delete" or not _ensure_request_role(cb, entry):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await state.update_data(current_request=request_id)
    await on_admin_publish(cb, state)


async def _render_publish_editor(
    target: CallbackQuery | Message, state: FSMContext, entry: dict, *, edit_message_id: int | None = None,
) -> None:
    lang = _lang_for(target)
    payload = entry.get("payload") or {}
    await state.set_state(AdminFlow.reviewing)
    await state.update_data(current_request=entry["id"])
    text = f"{_render_request_draft(entry, lang=lang)}\n\n{_review_meta_block(entry, lang=lang)}"
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        kb = icon_draft_edit_kb(lang=lang)
    else:
        not_before = _publish_not_before_dt_utc(entry)
        kb = draft_edit_kb(
            "adm", t("admin_submit_publish", lang), include_back=True,
            include_file=can_edit_request(entry), include_schedule=_can_schedule_request(entry),
            include_force_publish=bool(not_before and not_before > datetime.now(timezone.utc)),
            checked_on_set=bool(str(payload.get("checked_on") or "").strip()), lang=lang,
        )
    if edit_message_id and isinstance(target, Message):
        try:
            await target.bot.edit_message_text(
                text, chat_id=target.chat.id, message_id=edit_message_id, parse_mode=ParseMode.HTML,
                reply_markup=kb, disable_web_page_preview=True,
            )
            return
        except TelegramBadRequest:
            pass
    sent = await answer(target, text, kb, request_image_key(entry))
    if sent:
        await state.update_data(draft_message_id=sent.message_id)


@router.callback_query(F.data.startswith("adm:prepublish:"))
async def on_admin_prepublish(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":", 2)[2]
    entry = get_request_by_id(request_id)
    if not entry or entry.get("history_only") or entry.get("status") in {"published", "deleted"}:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    await _nav_push(state, f"adm:prepublish:{entry['id']}")
    await _render_publish_editor(cb, state, entry)
    await ack(cb)


@router.callback_query(F.data.startswith("adm_icon:edit:"))
async def on_admin_icon_edit_field(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    field = cb.data.split(":")[2]

    await _nav_push(state, f"adm_icon:edit:{field}")

    if field in {"name", "author", "version", "count"}:
        await state.update_data(edit_field=field)
        await state.set_state(AdminFlow.editing_icon_field)
        prompt = {
            "name": _tr(cb, "admin_prompt_name"),
            "author": _tr(cb, "admin_prompt_author"),
            "version": _tr(cb, "admin_prompt_version"),
            "count": _tr(cb, "admin_prompt_icons_count"),
        }.get(field, _tr(cb, "admin_prompt_value"))
        await answer(cb, prompt, admin_cancel_kb(lang), None)
        await ack(cb)
        return

    await ack(cb)


@router.callback_query(F.data == "adm_icon:back")
async def on_admin_icon_back(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    data = await state.get_data()
    entry = get_request_by_id(data.get("current_request", ""))
    if entry:
        draft_text = _render_request_draft(entry, lang=_lang_for(cb))
        await answer(cb, draft_text, icon_draft_edit_kb(lang=lang), "iconpacks")
    await ack(cb)


@router.callback_query(F.data == "adm_icon:submit")
async def on_admin_icon_submit(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    data = await state.get_data()
    request_id = data.get("current_request")
    if request_id:
        await state.update_data(current_request=request_id)
        await on_admin_publish(cb, state)
        return
    await ack(cb)


@router.message(AdminFlow.editing_icon_field)
async def on_admin_icon_field_value(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _is_super_admin(message):
        await state.clear()
        await state.set_state(AdminFlow.menu)
        return
    text = (message.text or "").replace("\\n", "\n").strip()
    if not text:
        await message.answer(_tr(message, "need_text"), disable_web_page_preview=True)
        return

    data = await state.get_data()
    request_id = data.get("current_request")
    field = data.get("edit_field")

    if not request_id:
        await state.set_state(AdminFlow.menu)
        return

    entry = get_request_by_id(request_id)
    if not entry:
        await state.set_state(AdminFlow.menu)
        return

    payload = entry.get("payload", {})
    icon = payload.get("icon", {})
    if field == "count":
        try:
            icon["count"] = int(text)
        except ValueError:
            await message.answer(_tr(message, "admin_need_number"), disable_web_page_preview=True)
            return
    else:
        icon[field] = text

    update_request_payload(request_id, {"icon": icon})

    await state.update_data(edit_field=None)
    await state.set_state(AdminFlow.reviewing)
    entry = get_request_by_id(request_id)
    if entry:
        draft_text = _render_request_draft(entry, lang=_lang_for(message))
        await answer(message, draft_text, icon_draft_edit_kb(lang=lang), "iconpacks")


@router.callback_query(F.data.startswith("adm:edit:"))
async def on_admin_draft_edit(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    field = cb.data.split(":")[2]

    await _nav_push(state, f"adm:edit:{field}")

    if field == "file":
        data = await state.get_data()
        if not data.get("current_request"):
            await cb.answer(_tr(cb, "not_found"), show_alert=True)
            return
        await state.set_state(AdminFlow.uploading_request_file)
        await answer(cb, _tr(cb, "admin_send_plugin_file"), admin_cancel_kb(lang), None)
        await ack(cb)
        return

    if field == "checked_on":
        data = await state.get_data()
        request_id = data.get("current_request")
        if not request_id:
            await cb.answer(_tr(cb, "not_found"), show_alert=True)
            return
        entry = get_request_by_id(request_id)
        if not entry:
            await cb.answer(_tr(cb, "not_found"), show_alert=True)
            return
        if not _ensure_request_role(cb, entry):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return

        update_request_payload(request_id, {"checked_on": _checked_on_value_now()})
        entry = get_request_by_id(request_id) or entry
        draft_text = _render_request_draft(entry, lang=_lang_for(cb))
        payload = entry.get("payload", {})
        checked_on_set = bool(str(payload.get("checked_on") or "").strip())
        include_schedule = _can_schedule_request(entry)
        await answer(
            cb,
            draft_text,
            draft_edit_kb(
                "adm",
                _tr(cb, "admin_submit_publish"),
                include_back=True,
                include_file=True,
                include_schedule=include_schedule,
                checked_on_set=checked_on_set,
                lang=lang,
            ),
            request_image_key(entry),
        )
        await ack(cb)
        return

    if field in {"description", "usage"}:
        await state.update_data(edit_field=field)
        await answer(cb, _tr(cb, "admin_choose_language"), draft_lang_kb("adm", field, lang=lang), None)
        await ack(cb)
        return

    if field == "category":
        data = await state.get_data()
        entry = get_request_by_id(data.get("current_request")) if data.get("current_request") else None
        if not entry or entry.get("type") not in {"new", "update"} or not _ensure_request_role(cb, entry):
            await cb.answer(_tr(cb, "not_found"), show_alert=True)
            return
        await _open_admin_category_picker(cb, state, "adm", entry.get("payload", {}))
        await ack(cb)
        return

    prompt = {
        "name": _tr(cb, "admin_prompt_name"),
        "author": _tr(cb, "admin_prompt_author"),
        "settings": _tr(cb, "admin_prompt_has_settings"),
        "min_version": _tr(cb, "admin_prompt_min_version"),
        "checked_on": _tr(cb, "admin_prompt_checked_on"),
    }.get(field, _tr(cb, "admin_prompt_value"))

    await state.update_data(edit_field=field)
    await state.set_state(AdminFlow.editing_draft_field)
    await answer(cb, prompt, admin_cancel_kb(lang), None)
    await ack(cb)


@router.callback_query(F.data.startswith("adm_edit:edit:"))
async def on_admin_catalog_edit(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    field = cb.data.split(":")[2]

    await _nav_push(state, f"adm_edit:edit:{field}")

    if field == "checked_on":
        data = await state.get_data()
        request_id = data.get("current_request")
        if not request_id:
            await cb.answer(_tr(cb, "not_found"), show_alert=True)
            return
        entry = get_request_by_id(request_id)
        if not entry:
            await cb.answer(_tr(cb, "not_found"), show_alert=True)
            return
        if not _ensure_request_role(cb, entry):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return

        update_request_payload(request_id, {"checked_on": _checked_on_value_now()})
        entry = get_request_by_id(request_id) or entry
        draft_text = _render_request_draft(entry, lang=_lang_for(cb))
        await answer(
            cb,
            draft_text,
            draft_edit_kb("adm_edit", _tr(cb, "admin_submit_update"), include_back=True, include_file=True, lang=lang),
            "profile",
        )
        await ack(cb)
        return

    if field == "file":
        await state.set_state(AdminFlow.uploading_catalog_file)
        await answer(cb, _tr(cb, "admin_send_plugin_file"), admin_cancel_kb(lang), None)
        await ack(cb)
        return
    if field in {"description", "usage"}:
        await state.update_data(edit_field=field)
        await answer(cb, _tr(cb, "admin_choose_language"), draft_lang_kb("adm_edit", field, lang=lang), None)
        await ack(cb)
        return

    if field == "category":
        if not _ensure_admin_role(cb, "plugins"):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return
        data = await state.get_data()
        if not data.get("edit_slug") or not data.get("edit_payload") or await state.get_state() != AdminFlow.editing_catalog_plugin.state:
            await cb.answer(_tr(cb, "not_found"), show_alert=True)
            return
        await _open_admin_category_picker(cb, state, "adm_edit", data["edit_payload"])
        await ack(cb)
        return

    prompt = {
        "name": _tr(cb, "admin_prompt_new_name"),
        "author": _tr(cb, "admin_prompt_author"),
        "settings": _tr(cb, "admin_prompt_has_settings"),
        "min_version": _tr(cb, "admin_prompt_min_version"),
        "checked_on": _tr(cb, "admin_prompt_checked_on"),
    }.get(field, _tr(cb, "admin_prompt_value"))

    await state.update_data(edit_field=field)
    await state.set_state(AdminFlow.editing_catalog_field)
    await answer(cb, prompt, admin_cancel_kb(lang), None)
    await ack(cb)


@router.callback_query(F.data.startswith("adm_edit:lang:"))
async def on_admin_catalog_language(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    _, _, field, lang_choice = cb.data.split(":")
    await state.update_data(edit_field=field, edit_lang=lang_choice)
    await state.set_state(AdminFlow.editing_catalog_field)

    prompt = _tr(cb, "admin_prompt_enter_text_ru") if lang_choice == "ru" else _tr(cb, "admin_prompt_enter_text_en")
    await answer(cb, prompt, admin_cancel_kb(lang), None)
    await ack(cb)


@router.callback_query(F.data.startswith("adm_edit:cat:"))
async def on_admin_catalog_category(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not await _choose_admin_categories(cb, state, "adm_edit"):
        return

    data = await state.get_data()
    payload = data.get("edit_payload", {})
    draft_text = build_channel_post({"payload": payload})
    await state.set_state(AdminFlow.editing_catalog_plugin)
    await answer(
        cb,
        draft_text,
        draft_edit_kb("adm_edit", _tr(cb, "admin_submit_update"), include_back=True, include_file=True, lang=lang),
        "profile",
    )
    await ack(cb)


@router.callback_query(F.data == "adm_edit:back")
async def on_admin_catalog_back(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    data = await state.get_data()
    if data.get("category_selection") and not await _admin_category_picker_valid(cb, state, "adm_edit"):
        return
    await state.update_data(category_selection=None, edit_field=None)
    payload = data.get("edit_payload", {})
    draft_text = build_channel_post({"payload": payload})
    await state.set_state(AdminFlow.editing_catalog_plugin)
    await answer(
        cb,
        draft_text,
        draft_edit_kb("adm_edit", _tr(cb, "admin_submit_update"), include_back=True, include_file=True, lang=lang),
        "profile",
    )
    await ack(cb)


@router.message(AdminFlow.editing_catalog_field)
async def on_admin_catalog_field_value(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    text_html = telegram_html(message.html_text or message.text or "")
    text_plain = (message.text or "").replace("\\n", "\n").strip()
    if not text_html and not text_plain:
        await message.answer(
            _tr(message, "need_text"),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
        return

    data = await state.get_data()
    field = data.get("edit_field")
    edit_lang = data.get("edit_lang")
    payload = data.get("edit_payload", {})
    plugin = payload.get("plugin", {})

    if field in {"description", "usage"}:
        payload[f"{field}_{edit_lang}"] = text_html
    elif field == "name":
        plugin["name"] = text_plain
    elif field == "author":
        plugin["author"] = text_plain
    elif field == "min_version":
        plugin["min_version"] = text_plain
    elif field == "checked_on":
        payload["checked_on"] = text_plain
    elif field == "settings":
        plugin["has_ui_settings"] = text_plain.lower() in {"да", "yes", "1", "true"}

    payload["plugin"] = plugin
    await state.update_data(edit_payload=payload, edit_field=None, edit_lang=None)
    await state.set_state(AdminFlow.editing_catalog_plugin)

    draft_text = build_channel_post({"payload": payload})
    data = await state.get_data()
    message_id = data.get("edit_message_id")
    if message_id:
        try:
            await message.bot.edit_message_text(
                draft_text,
                chat_id=message.chat.id,
                message_id=message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=draft_edit_kb("adm_edit", _tr(message, "admin_submit_update"), include_back=True, include_file=True, lang=lang),
                disable_web_page_preview=True,
            )
            return
        except Exception:
            pass

    sent = await answer(
        message,
        draft_text,
        draft_edit_kb("adm_edit", _tr(message, "admin_submit_update"), include_back=True, include_file=True, lang=lang),
        "profile",
    )
    if sent:
        await state.update_data(edit_message_id=sent.message_id)


@router.message(AdminFlow.uploading_catalog_file, F.document)
async def on_admin_catalog_file(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "plugins"):
        return

    try:
        plugin = await process_plugin_file(message.bot, message.document)
    except ValueError as exc:
        key, _, details = str(exc).partition(":")
        if key == "parse_error" and details:
            await message.answer(_tr(message, "parse_error", error=details))
        else:
            await message.answer(_tr(message, key) if key in TEXTS else str(exc))
        return

    data = await state.get_data()
    payload = data.get("edit_payload", {})
    plugin_dict = plugin.to_dict()
    plugin_dict["file_path"] = plugin_dict.get("file_path")
    fields = category_fields(categories_for(payload) or categories_for(plugin_dict))
    plugin_dict["categories"] = fields["category_keys"]
    payload.update(fields, plugin=plugin_dict)
    await state.update_data(edit_payload=payload, **fields, category_selection=None)
    await state.set_state(AdminFlow.editing_catalog_plugin)

    draft_text = build_channel_post({"payload": payload})
    await answer(
        message,
        draft_text,
        draft_edit_kb("adm_edit", _tr(message, "admin_submit_update"), include_back=True, include_file=True, lang=lang),
    )


@router.message(AdminFlow.uploading_catalog_file)
async def on_admin_catalog_file_invalid(message: Message) -> None:
    if not _ensure_admin_role(message, "plugins"):
        return
    await message.answer(_tr(message, "admin_send_plugin_file_short"))


@router.callback_query(F.data == "adm_edit:submit")
async def on_admin_catalog_submit(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    data = await state.get_data()
    slug = data.get("edit_slug")
    payload = data.get("edit_payload", {})
    if not slug:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    plugin_entry = find_plugin_by_slug(slug)
    if not plugin_entry:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    message_id = plugin_entry.get("channel_message", {}).get("message_id")
    if not message_id:
        await cb.answer(_tr(cb, "admin_channel_message_missing"), show_alert=True)
        return

    if not categories_for(payload):
        await cb.answer(_tr(cb, "categories_required"), show_alert=True)
        return
    await cb.answer(_tr(cb, "admin_updating"))
    from userbot.client import get_userbot

    userbot = await get_userbot()
    if not userbot:
        await cb.answer(_tr(cb, "admin_userbot_unavailable"), show_alert=True)
        return

    await prepare_catalog_categories(payload, plugin_entry, userbot)
    await state.update_data(edit_payload=payload, **category_fields(payload), category_selection=None)
    entry = {"payload": payload}
    post_text = build_channel_post(entry)
    file_path = payload.get("plugin", {}).get("file_path")
    from bot.helpers import fit_filename
    download_name = fit_filename(str(payload.get("plugin", {}).get("id") or "plugin"), payload_extension(payload.get("plugin") or {}))
    await userbot.update_message(message_id, post_text, file_path, download_name)
    update_catalog_entry(slug, entry, message_id)
    from core.storage import flush_documents
    await flush_documents("plugins")
    await state.set_state(AdminFlow.menu)
    await _reset_menu_navigation(state)
    await answer(cb, _tr(cb, "admin_title"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:lang:"))
async def on_admin_draft_language(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    _, _, field, lang_choice = cb.data.split(":")
    await state.update_data(edit_field=field, edit_lang=lang_choice)
    await state.set_state(AdminFlow.editing_draft_field)

    prompt = _tr(cb, "admin_prompt_enter_text_ru") if lang_choice == "ru" else _tr(cb, "admin_prompt_enter_text_en")
    await answer(cb, prompt, admin_cancel_kb(lang), None)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:cat:"))
async def on_admin_draft_category(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    if not await _choose_admin_categories(cb, state, "adm"):
        return

    entry = get_request_by_id((await state.get_data()).get("current_request"))
    if entry:
        include_schedule = _can_schedule_request(entry)
        await answer(
            cb,
            _render_request_draft(entry, lang=_lang_for(cb)),
            draft_edit_kb(
                "adm",
                _tr(cb, "admin_submit_publish"),
                include_back=True,
                include_file=True,
                include_schedule=include_schedule,
                lang=lang,
            ),
        )
    await ack(cb)


@router.callback_query(F.data == "adm:back")
async def on_admin_draft_back(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    data = await state.get_data()
    picker = data.get("category_selection") if isinstance(data, dict) else None
    if isinstance(picker, dict) and picker.get("prefix") in {"adm", "adm_edit"}:
        if not await _admin_category_picker_valid(cb, state, "adm"):
            return
        await state.update_data(category_selection=None, edit_field=None)
        entry = get_request_by_id(data.get("current_request"))
        await state.set_state(AdminFlow.reviewing)
        await answer(cb, _render_request_draft(entry, lang=_lang_for(cb)), draft_edit_kb(
            "adm", _tr(cb, "admin_submit_publish"), include_back=True, include_file=True,
            include_schedule=_can_schedule_request(entry), lang=_lang_for(cb),
        ))
        await ack(cb)
        return
    spawn_background(ack(cb))
    prev = await _nav_prev(state)
    if prev:
        await _render_nav_token(cb, state, prev)
    else:
        await _render_menu(cb, state)
    await ack(cb)


@router.message(AdminFlow.editing_draft_field)
async def on_admin_draft_field_value(message: Message, state: FSMContext) -> None:
    if not _is_super_admin(message):
        await state.clear()
        await state.set_state(AdminFlow.menu)
        return
    text_html = telegram_html(message.html_text or message.text or "")
    text_plain = (message.text or "").replace("\\n", "\n").strip()
    if not text_html and not text_plain:
        await message.answer(_tr(message, "need_text"), disable_web_page_preview=True)
        return

    data = await state.get_data()
    request_id = data.get("current_request")
    field = data.get("edit_field")
    edit_lang = data.get("edit_lang")

    if not request_id:
        await state.set_state(AdminFlow.menu)
        return

    entry = get_request_by_id(request_id)
    if not entry or entry.get("history_only") or entry.get("status") in {"published", "deleted"}:
        await state.set_state(AdminFlow.menu)
        await message.answer(_tr(message, "admin_request_already_handled"))
        return
    if not _acquire_request_lock(request_id):
        await message.answer(_tr(message, "admin_request_processing"))
        return
    try:
        payload = entry.get("payload", {})
        plugin = dict(payload.get("plugin") or {})

        updates = {}
        if field in {"description", "usage"}:
            updates[f"{field}_{edit_lang}"] = text_html
        elif field == "name":
            plugin["name"] = text_plain
            updates["plugin"] = plugin
        elif field == "author":
            plugin["author"] = text_plain
            updates["plugin"] = plugin
        elif field == "min_version":
            plugin["min_version"] = text_plain
            updates["plugin"] = plugin
        elif field == "checked_on":
            updates["checked_on"] = text_plain
        elif field == "settings":
            has_settings = text_plain.lower() in {"да", "yes", "1", "true"}
            plugin["has_ui_settings"] = has_settings
            updates["plugin"] = plugin

        if updates:
            update_request_payload(request_id, updates)
    finally:
        _release_request_lock(request_id)

    await state.update_data(edit_field=None, edit_lang=None)
    await _render_publish_editor(message, state, entry, edit_message_id=data.get("draft_message_id"))


@router.callback_query(F.data.regexp(r"^adm:submit(_force)?$"))
async def on_admin_publish(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    data = await state.get_data()
    request_id = data.get("current_request")
    entry = get_request_by_id(request_id)

    if not entry:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    if _is_appeal(entry):
        await cb.answer(_tr(cb, "appeal_not_a_plugin"), show_alert=True)
        return
    if entry.get("history_only") or entry.get("status") in {"published", "deleted"}:
        await cb.answer(_tr(cb, "admin_request_already_handled"), show_alert=True)
        return

    if not _acquire_request_lock(request_id):
        await cb.answer(_tr(cb, "admin_request_processing"), show_alert=True)
        return

    try:
        errors = _validate_request_before_publish(entry)
        if errors:
            update_request_payload(request_id, {
                "last_publish_error": "\n".join(errors),
                "last_publish_error_at": datetime.now(timezone.utc).isoformat(),
            })
            add_audit_event(
                "moderation.publish_validation_failed", actor_id=_actor_id(cb), actor=_admin_actor_label(cb),
                request_id=str(request_id), details={"errors": errors},
            )
            await _reset_menu_navigation(state)
            await answer(
                cb, _tr(cb, "admin_publish_stopped", errors="\n".join(f"• {plain_html(error)}" for error in errors)),
                admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin",
            )
            await ack(cb, _tr(cb, "admin_has_errors"), show_alert=True)
            return

        actor, actor_id = _admin_actor_label(cb), _actor_id(cb)
        not_before = _publish_not_before_dt_utc(entry)
        if (cb.data != "adm:submit_force" and _can_schedule_request(entry)
                and not_before and not_before > datetime.now(timezone.utc)):
            if not schedule_request(request_id, not_before, actor, actor_id):
                await cb.answer(_tr(cb, "admin_request_already_handled"), show_alert=True)
                return
            spawn_background(_approval_followups(cb.bot, request_id))
            dt_str = not_before.astimezone(TZ_UTC_PLUS_5).strftime("%d.%m.%Y %H:%M")
            await _reset_menu_navigation(state)
            await answer(cb, _tr(cb, "admin_request_scheduled_by_limit", datetime=dt_str),
                         admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")
            await ack(cb)
            return

        await ack(cb, _tr(cb, "admin_publishing"))
        started = time.perf_counter()
        try:
            result = await publish_request(cb.bot, entry, actor, actor_id)
        except Exception as exc:
            logger.exception("event=moderation.publish.failed request_id=%s admin_id=%s", request_id, actor_id)
            update_request_payload(request_id, {
                "last_publish_error": str(exc),
                "last_publish_error_at": datetime.now(timezone.utc).isoformat(),
            })
            await _reset_menu_navigation(state)
            await answer(cb, _tr(cb, "admin_generic_error", error=plain_html(exc)),
                         admin_menu_kb(_admin_menu_role(cb), lang=lang), "profile")
            return

        await _reset_menu_navigation(state)
        await answer(cb, _tr(cb, "admin_publish_done", link=result.get("link", "")),
                     admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")
        logger.info("event=moderation.publish.ui_ready request_id=%s duration_ms=%.0f", request_id,
                    (time.perf_counter() - started) * 1000)
    finally:
        _release_request_lock(request_id)


@router.callback_query(F.data.startswith("adm:reject:"))
async def on_admin_reject(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry:
        payload = entry.get("payload", {})
        if payload.get("submission_type") == "icon" or payload.get("icon"):
            if not _ensure_admin_role(cb, "icons"):
                await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
                return
        else:
            if not _ensure_admin_role(cb, "plugins"):
                await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
                return
    else:
        if not _ensure_admin(cb):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return

    from bot.services.moderation import send_reasons_to_author_default

    default_votes = send_reasons_to_author_default()
    await _nav_push(state, f"adm:reject:{request_id}")
    await state.update_data(reject_show_votes=default_votes)
    await cb.message.edit_reply_markup(
        reply_markup=admin_reject_kb(request_id, lang=lang, show_votes=default_votes, allow_templates=templates_allowed(entry)))
    await ack(cb)


@router.callback_query(F.data.startswith("adm:reject_votes:"))
async def on_admin_reject_votes_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry and not _ensure_request_role(cb, entry):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    data = await state.get_data()
    show_votes = not bool(data.get("reject_show_votes"))
    await state.update_data(reject_show_votes=show_votes)
    try:
        await cb.message.edit_reply_markup(
            reply_markup=admin_reject_kb(request_id, lang=lang, show_votes=show_votes, allow_templates=templates_allowed(entry)))
    except Exception:
        pass
    await ack(cb)


@router.callback_query(F.data.startswith("adm:reject_comment:"))
async def on_admin_reject_comment(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry:
        payload = entry.get("payload", {})
        if payload.get("submission_type") == "icon" or payload.get("icon"):
            if not _ensure_admin_role(cb, "icons"):
                await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
                return
        else:
            if not _ensure_admin_role(cb, "plugins"):
                await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
                return
    else:
        if not _ensure_admin(cb):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return

    await _nav_push(state, f"adm:reject_comment:{request_id}")
    await _discard_pending_reject_media_groups(cb)
    await state.update_data(
        reject_request_id=request_id,
        reject_media=[],
        reject_comment_draft=None,
    )
    await state.set_state(AdminFlow.entering_reject_comment)
    await answer(cb, _tr(cb, "admin_enter_reject_reason"), admin_cancel_kb(_lang_for(cb)), "admin")
    await ack(cb)


def _schedule_decision_cleanup(bot, entry: dict, decision_text: str, actor: str) -> None:
    snapshot = deepcopy(entry)

    async def finish():
        results = await asyncio.gather(
            finalize_admin_notify_messages(bot, snapshot, decision_text, actor),
            delete_forum_request_message(bot, snapshot),
            return_exceptions=True,
        )
        for surface, result in zip(("admin_notifications", "forum"), results):
            if isinstance(result, BaseException):
                logger.error(
                    "event=moderation.decision_cleanup_failed request_id=%s surface=%s",
                    snapshot.get("id"), surface,
                    exc_info=(type(result), result, result.__traceback__),
                )

    spawn_background(finish())


async def _finalize_rejection(
    bot,
    actor_target: CallbackQuery | Message,
    entry: dict,
    request_id: str,
    comment: str,
    show_votes: bool,
    media: list | None = None,
) -> None:
    update_request_status(
        request_id, "rejected", comment=comment,
        actor=_admin_actor_label(actor_target), actor_id=_actor_id(actor_target),
    )
    actor_user = actor_target.from_user
    add_audit_event(
        "moderation.reject",
        actor_id=actor_user.id if actor_user else None,
        actor=_admin_actor_label(actor_target),
        request_id=str(request_id),
        details={"silent": False, "reason": comment},
    )
    _schedule_decision_cleanup(bot, entry, "Заявка была отклонена", _admin_actor_label(actor_target))
    payload = entry.get("payload", {})
    user_id = payload.get("user_id")
    if not user_id:
        return
    author_lang = get_lang(user_id)
    item = payload.get("plugin") or payload.get("icon") or {}
    plugin_name = plain_html(item.get("name") or "—")
    reason = strip_blockquote_tags(reason_templates.localize_reason(comment, author_lang)) or "—"
    notify_text = t("notify_rejected", author_lang, name=plugin_name, comment=reason)
    if show_votes:
        reasons = rejection_reasons(entry, lang=author_lang)
        if reasons:
            reasons_text = "\n".join(f"• {r}" for r in reasons)
            notify_text += "\n\n" + t("notify_rejected_votes", author_lang, reasons=reasons_text)

    from bot.keyboards import author_rejected_kb
    from bot.services.validation import block_plugin

    was_appeal = bool(payload.get("is_appeal"))
    plugin_id = str(item.get("id") or "").strip()
    if was_appeal and plugin_id:
        if block_plugin(plugin_id):
            add_audit_event(
                "moderation.plugin_blocked",
                actor_id=actor_user.id if actor_user else None,
                actor=_admin_actor_label(actor_target),
                request_id=str(request_id),
                details={"plugin_id": plugin_id},
            )
        notify_text += t("notify_rejected_blocked", author_lang)

    reply_kwargs = author_reply_kwargs(entry)

    async def notify_author():
        try:
            parts = split_html(notify_text, 3500)
            for index, part in enumerate(parts):
                await bot.send_message(
                    user_id,
                    part,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=author_rejected_kb(str(request_id), can_appeal=not was_appeal, lang=author_lang) if index + 1 == len(parts) else None,
                    **reply_kwargs,
                )
            if media:
                from bot.services.moderation import send_media_group

                await send_media_group(bot, int(user_id), media)
        except Exception:
            logger.exception(
                "event=reject.notify_author_failed user_id=%s request_id=%s",
                user_id, request_id,
            )

    spawn_background(notify_author())


REJECT_MEDIA_LIMIT = limits.ALBUM_ITEMS
_REJECT_MEDIA_GROUP_SETTLE_SECONDS = 0.8
_reject_media_group_buffers: dict[tuple[int, int, str], dict[str, Any]] = {}


def _reject_media_of(message: Message) -> dict | None:
    if message.photo:
        return {"type": "photo", "file_id": message.photo[-1].file_id}
    if message.video:
        return {"type": "video", "file_id": message.video.file_id}
    return None


def _reject_media_owner(target: Message | CallbackQuery) -> tuple[int, int]:
    message = target.message if isinstance(target, CallbackQuery) else target
    chat_id = int(message.chat.id) if message and message.chat else 0
    user_id = int(target.from_user.id) if target.from_user else 0
    return chat_id, user_id


async def _store_reject_media_group(pending: dict[str, Any], *, notify: bool = True) -> None:
    state: FSMContext = pending["state"]
    if await state.get_state() != AdminFlow.entering_reject_comment.state:
        return

    data = await state.get_data()
    media = list(data.get("reject_media") or [])
    existing = {
        (str(item.get("type") or ""), str(item.get("file_id") or ""))
        for item in media
        if isinstance(item, dict)
    }
    for item in pending["items"]:
        item_key = (str(item.get("type") or ""), str(item.get("file_id") or ""))
        if item_key in existing or len(media) >= REJECT_MEDIA_LIMIT:
            continue
        media.append(item)
        existing.add(item_key)

    update: dict[str, Any] = {"reject_media": media}
    caption = str(pending.get("caption") or "").strip()
    if caption:
        update["reject_comment_draft"] = caption
    await state.update_data(**update)
    if notify:
        await pending["target"].answer(
            _tr(
                pending["target"],
                "admin_reject_media_added",
                count=len(media),
                max=REJECT_MEDIA_LIMIT,
            )
        )


async def _queue_reject_media_group(message: Message, state: FSMContext, item: dict) -> None:
    key = (*_reject_media_owner(message), str(message.media_group_id))
    pending = _reject_media_group_buffers.setdefault(
        key,
        {"items": [], "caption": "", "target": message, "state": state, "task": None},
    )
    item_key = (str(item.get("type") or ""), str(item.get("file_id") or ""))
    existing = {
        (str(value.get("type") or ""), str(value.get("file_id") or ""))
        for value in pending["items"]
        if isinstance(value, dict)
    }
    if item_key not in existing and len(pending["items"]) < REJECT_MEDIA_LIMIT:
        pending["items"].append(item)
    caption = telegram_html(message.html_text or message.caption or "")
    if caption:
        pending["caption"] = caption
    pending["target"] = message
    pending["state"] = state

    previous = pending.get("task")
    if previous and not previous.done():
        previous.cancel()

    async def _settle() -> None:
        try:
            await asyncio.sleep(_REJECT_MEDIA_GROUP_SETTLE_SECONDS)
        except asyncio.CancelledError:
            return
        current = _reject_media_group_buffers.get(key)
        if not current or current.get("task") is not asyncio.current_task():
            return
        _reject_media_group_buffers.pop(key, None)
        try:
            await _store_reject_media_group(current)
        except Exception:
            logger.exception("admin: reject media group finalize failed key=%s", key)

    pending["task"] = asyncio.create_task(_settle())


async def _discard_pending_reject_media_groups(target: Message | CallbackQuery) -> None:
    owner = _reject_media_owner(target)
    for key in [key for key in _reject_media_group_buffers if key[:2] == owner]:
        pending = _reject_media_group_buffers.pop(key)
        task = pending.get("task")
        if task and not task.done():
            task.cancel()


async def _flush_pending_reject_media_groups(target: Message | CallbackQuery) -> None:
    owner = _reject_media_owner(target)
    for key in [key for key in _reject_media_group_buffers if key[:2] == owner]:
        pending = _reject_media_group_buffers.pop(key)
        task = pending.get("task")
        if task and not task.done():
            task.cancel()
        await _store_reject_media_group(pending, notify=False)


@router.message(AdminFlow.entering_reject_comment)
async def on_admin_enter_reject_comment(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    item = _reject_media_of(message)
    if item:
        if message.media_group_id:
            await _queue_reject_media_group(message, state, item)
            return
        data = await state.get_data()
        media = list(data.get("reject_media") or [])
        if len(media) >= REJECT_MEDIA_LIMIT:
            await message.answer(_tr(message, "comment_media_limit", max=REJECT_MEDIA_LIMIT))
            return
        media.append(item)
        await state.update_data(reject_media=media)
        caption = telegram_html(message.html_text or message.caption or "")
        if caption:
            await state.update_data(reject_comment_draft=caption)
        await message.answer(_tr(message, "admin_reject_media_added", count=len(media), max=REJECT_MEDIA_LIMIT))
        return

    await _flush_pending_reject_media_groups(message)
    data = await state.get_data()
    comment = telegram_html(message.html_text or message.text or "") or str(data.get("reject_comment_draft") or "")
    if not comment:
        await message.answer(_tr(message, "need_text"), disable_web_page_preview=True)
        return

    request_id = data.get("reject_request_id")
    if not request_id:
        await state.clear()
        await state.set_state(AdminFlow.menu)
        return

    entry = get_request_by_id(request_id)
    if entry and not _ensure_request_role(message, entry):
        return
    if not _is_super_admin(message):
        return

    if entry:
        await _finalize_rejection(
            message.bot, message, entry, request_id, comment,
            show_votes=bool(data.get("reject_show_votes")),
            media=data.get("reject_media") or [],
        )

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await _reset_menu_navigation(state)
    await answer(message, _tr(message, "admin_rejected_done"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")


@router.callback_query(F.data.startswith("adm:reject_silent:"))
async def on_admin_reject_silent(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry and not templates_allowed(entry):
        await cb.answer(_tr(cb, "reason_templates_forbidden"), show_alert=True)
        return
    if entry:
        payload = entry.get("payload", {})
        if payload.get("submission_type") == "icon" or payload.get("icon"):
            if not _ensure_admin_role(cb, "icons"):
                await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
                return
        else:
            if not _ensure_admin_role(cb, "plugins"):
                await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
                return
    else:
        if not _ensure_admin(cb):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return

    await _nav_push(state, f"adm:reject_silent:{request_id}")
    update_request_status(request_id, "rejected", actor=_admin_actor_label(cb), actor_id=_actor_id(cb))
    if entry:
        add_audit_event(
            "moderation.reject",
            actor_id=cb.from_user.id if cb.from_user else None,
            actor=_admin_actor_label(cb),
            request_id=str(request_id),
            details={"silent": True},
        )
        _schedule_decision_cleanup(cb.bot, entry, "Заявка была отклонена", _admin_actor_label(cb))
        data = await state.get_data()
        if data.get("reject_show_votes"):
            payload = entry.get("payload", {})
            user_id = payload.get("user_id")
            author_lang = get_lang(user_id) if user_id else "ru"
            reasons = rejection_reasons(entry, lang=author_lang)
            if user_id and reasons:
                author_lang = get_lang(user_id)
                item = payload.get("plugin") or payload.get("icon") or {}
                plugin_name = plain_html(item.get("name") or "—")
                reasons_text = "\n".join(f"• {r}" for r in reasons)
                try:
                    await cb.bot.send_message(
                        user_id,
                        t("notify_rejected_moderation", author_lang, name=plugin_name, reasons=reasons_text),
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                        **author_reply_kwargs(entry),
                    )
                except Exception:
                    logger.exception(
                        "event=reject.notify_votes_failed user_id=%s request_id=%s",
                        user_id, request_id,
                    )
    await _reset_menu_navigation(state)
    await answer(cb, _tr(cb, "admin_rejected_done"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")
    await ack(cb)


_REJECT_TEMPLATES_LIMIT = TEMPLATES_LIMIT
_TEMPLATE_CONFIG_KEYS = TEMPLATE_CONFIG_KEYS


def _template_kind(raw: str) -> str:
    return template_kind(raw)


def _load_templates(kind: str = "reject", lang: str = "ru") -> list[str]:
    return load_reason_templates(get_config(), kind, lang)


def _save_templates(templates: list[str], kind: str = "reject") -> None:
    kind = _template_kind(kind)
    cfg = get_config()
    cfg[_TEMPLATE_CONFIG_KEYS[kind]] = list(templates)
    save_config(cfg)
    invalidate("config")


async def _render_rejtpl_cfg(target, state: FSMContext, kind: str = "reject", page: int = 0) -> None:
    lang = _lang_for(target)
    kind = _template_kind(kind)
    templates = _load_templates(kind, lang)
    title_key = {
        "approve": "admin_apptpl_cfg_title",
        "reject": "admin_rejtpl_cfg_title",
        "update_approve": "admin_update_apptpl_cfg_title",
        "update_reject": "admin_update_rejtpl_cfg_title",
    }[kind]
    empty_key = {
        "approve": "admin_apptpl_cfg_empty",
        "reject": "admin_rejtpl_cfg_empty",
        "update_approve": "admin_update_apptpl_cfg_empty",
        "update_reject": "admin_update_rejtpl_cfg_empty",
    }[kind]
    body, _, page, total = template_page(templates, page)
    text = t(title_key, lang, templates=body) if templates else t(empty_key, lang)
    if templates:
        text += "\n\n" + t("reason_templates_page", lang, page=page + 1, total=total)
    await answer(target, text, admin_reject_templates_cfg_kb(templates, kind=kind, lang=lang, page=page), "admin")


@router.callback_query(F.data.regexp(r"^adm:rejtpl_cfgpage:(reject|approve|update_reject|update_approve):\d+$"))
async def on_admin_rejtpl_cfg_page(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = (cb.data or "").split(":")
    await _render_rejtpl_cfg(cb, state, parts[2], int(parts[3]))
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:rejtpl_cfg(?::(?:reject|approve|update_reject|update_approve))?$"))
async def on_admin_rejtpl_cfg(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = (cb.data or "").split(":")
    kind = _template_kind(parts[2]) if len(parts) > 2 else "reject"
    await _nav_push(state, f"adm:rejtpl_cfg:{kind}")
    await state.update_data(tpl_kind=kind)
    await state.set_state(AdminFlow.menu)
    await _render_rejtpl_cfg(cb, state, kind)
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:rejtpl_add(?::(?:reject|approve|update_reject|update_approve))?$"))
async def on_admin_rejtpl_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = (cb.data or "").split(":")
    kind = _template_kind(parts[2]) if len(parts) > 2 else "reject"
    await state.update_data(tpl_kind=kind)
    if len(_load_templates(kind)) >= _REJECT_TEMPLATES_LIMIT:
        await cb.answer(_tr(cb, "admin_rejtpl_limit", limit=_REJECT_TEMPLATES_LIMIT), show_alert=True)
        return
    await state.set_state(AdminFlow.entering_reject_template)
    prompt_key = {
        "approve": "admin_enter_approve_template",
        "reject": "admin_enter_reject_template",
        "update_approve": "admin_enter_update_approve_template",
        "update_reject": "admin_enter_update_reject_template",
    }[kind]
    await answer(cb, _tr(cb, prompt_key), admin_cancel_kb(_lang_for(cb)), "admin")
    await ack(cb)


@router.message(AdminFlow.entering_reject_template)
async def on_admin_enter_reject_template(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "super"):
        return
    text = " ".join((message.text or "").split()).strip()
    if not text:
        await message.answer(_tr(message, "need_text"), disable_web_page_preview=True)
        return
    kind = _template_kind((await state.get_data()).get("tpl_kind"))
    templates = _load_templates(kind)
    if len(templates) >= _REJECT_TEMPLATES_LIMIT:
        await message.answer(_tr(message, "admin_rejtpl_limit", limit=_REJECT_TEMPLATES_LIMIT))
        return
    templates.append(text)
    _save_templates(templates, kind)
    await state.set_state(AdminFlow.menu)
    await _render_rejtpl_cfg(message, state, kind)


@router.callback_query(F.data.regexp(r"^adm:rejtpl_del:(?:reject|approve|update_reject|update_approve):\d+$"))
async def on_admin_rejtpl_del(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = cb.data.split(":")
    kind = _template_kind(parts[2])
    idx = int(parts[3])
    templates = _load_templates(kind)
    if 0 <= idx < len(templates):
        templates.pop(idx)
        _save_templates(templates, kind)
    await _render_rejtpl_cfg(cb, state, kind)
    await ack(cb)


async def _template_request(cb: CallbackQuery) -> dict | None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return None
    request_id = (cb.data or "").split(":")[2]
    entry = get_request_by_callback_token(request_id)
    if not entry:
        await cb.answer(_tr(cb, "admin_request_not_found"), show_alert=True)
        return None
    if not _ensure_request_role(cb, entry):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return None
    if not templates_allowed(entry):
        await cb.answer(_tr(cb, "reason_templates_forbidden"), show_alert=True)
        return None
    if entry.get("status") not in {"pending", "error", "scheduled", "rework"}:
        await cb.answer(_tr(cb, "vote_expired"), show_alert=True)
        return None
    return entry


def _selected_templates(data: dict, request_id: str, count: int) -> list[int]:
    if data.get("reject_tpl_request_id") != request_id:
        return []
    return list(dict.fromkeys(i for i in (data.get("reject_tpl_sel") or [])
                              if isinstance(i, int) and 0 <= i < count))


async def _check_template_snapshot(cb: CallbackQuery, state: FSMContext, entry: dict, data: dict) -> bool:
    kind = "update_reject" if entry.get("type") == "update" else "reject"
    if data.get("reject_tpl_request_id") == str(entry["id"]) and data.get("reject_tpl_options") == _load_templates(kind):
        return True
    await state.update_data(reject_tpl_sel=[], reject_tpl_options=None, reject_tpl_request_id=None)
    await cb.answer(_tr(cb, "reason_templates_changed"), show_alert=True)
    return False


async def _render_rejection_templates(cb: CallbackQuery, state: FSMContext, entry: dict, page: int = 0) -> None:
    lang = _lang_for(cb)
    request_id = str(entry["id"])
    kind = "update_reject" if entry.get("type") == "update" else "reject"
    templates = _load_templates(kind, lang)
    if not templates:
        await cb.answer(_tr(cb, "admin_rejtpl_empty"), show_alert=True)
        return
    data = await state.get_data()
    if not await _check_template_snapshot(cb, state, entry, data):
        return
    selected = _selected_templates(data, request_id, len(templates))
    body, _, page, total = template_page(templates, page)
    text = t("admin_rejtpl_pick_title", lang, templates=body)
    text += "\n\n" + t("reason_templates_page", lang, page=page + 1, total=total)
    if selected:
        text += "\n" + t("reason_templates_selected", lang, numbers=", ".join(str(i + 1) for i in selected))
    await state.update_data(reject_tpl_page=page)
    await answer(cb, text, admin_reject_templates_kb(request_id, templates, selected, lang=lang, page=page), "admin")


@router.callback_query(F.data.startswith("adm:rejtpl_pick:"))
async def on_admin_rejtpl_pick(cb: CallbackQuery, state: FSMContext) -> None:
    entry = await _template_request(cb)
    if not entry:
        return
    kind = "update_reject" if entry.get("type") == "update" else "reject"
    await state.update_data(reject_tpl_request_id=str(entry["id"]), reject_tpl_sel=[], reject_tpl_page=0,
                            reject_tpl_options=_load_templates(kind))
    await _render_rejection_templates(cb, state, entry)
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:rejtpl_page:[^:]+:\d+$"))
async def on_admin_rejtpl_page(cb: CallbackQuery, state: FSMContext) -> None:
    entry = await _template_request(cb)
    if not entry:
        return
    await _render_rejection_templates(cb, state, entry, int((cb.data or "").split(":")[3]))
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:rejtpl_t:[^:]+:\d+$"))
async def on_admin_rejtpl_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    entry = await _template_request(cb)
    if not entry:
        return
    request_id = str(entry["id"])
    idx = int((cb.data or "").split(":")[3])
    kind = "update_reject" if entry.get("type") == "update" else "reject"
    templates = _load_templates(kind, _lang_for(cb))
    if not 0 <= idx < len(templates):
        await cb.answer(_tr(cb, "vote_expired"), show_alert=True)
        return
    data = await state.get_data()
    if not await _check_template_snapshot(cb, state, entry, data):
        return
    selected = _selected_templates(data, request_id, len(templates))
    if idx in selected:
        selected.remove(idx)
    else:
        selected.append(idx)
    await state.update_data(reject_tpl_request_id=request_id, reject_tpl_sel=selected)
    await _render_rejection_templates(cb, state, entry, data.get("reject_tpl_page", 0))
    await ack(cb)


@router.callback_query(F.data.startswith("adm:rejtpl_go:"))
async def on_admin_rejtpl_go(cb: CallbackQuery, state: FSMContext) -> None:
    entry = await _template_request(cb)
    if not entry:
        return
    lang = _lang_for(cb)
    request_id = str(entry["id"])
    kind = "update_reject" if entry.get("type") == "update" else "reject"
    author_id = (entry.get("payload") or {}).get("user_id")
    templates = _load_templates(kind, get_lang(author_id) if author_id else lang)
    data = await state.get_data()
    if not await _check_template_snapshot(cb, state, entry, data):
        return
    selected = _selected_templates(data, request_id, len(templates))
    if not selected:
        await cb.answer(_tr(cb, "admin_rejtpl_empty"), show_alert=True)
        return
    comment = "\n".join(f"{pos + 1}. {plain_html(templates[i])}" for pos, i in enumerate(selected))
    await _finalize_rejection(
        cb.bot, cb, entry, request_id, comment,
        show_votes=bool(data.get("reject_show_votes")),
    )
    await state.update_data(reject_tpl_sel=[], reject_tpl_options=None, reject_tpl_request_id=None)
    await _reset_menu_navigation(state)
    await answer(cb, _tr(cb, "admin_rejected_done"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")
    await ack(cb)


async def _notify_author_rework(bot, entry: dict, comment: str) -> None:
    payload = entry.get("payload") or {}
    user_id = payload.get("user_id")
    if not user_id:
        return
    request_id = str(entry["id"])
    author_lang = get_lang(user_id)
    item = payload.get("plugin") or payload.get("icon") or {}
    reason = telegram_html(reason_templates.localize_reason(strip_blockquote_tags(comment), author_lang)) or "—"
    text = with_author_vote_summary(
        t("notify_rework", author_lang, name=plain_html(item.get("name") or "—"), comment=reason),
        entry, author_lang,
    )
    resubmit_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_resubmit", author_lang),
                             callback_data=f"resub:{request_callback_token(request_id)}", style="success"),
    ]])

    async def deliver(factory):
        while True:
            try:
                return await factory()
            except TelegramRetryAfter as exc:
                await asyncio.sleep(max(1, exc.retry_after))

    try:
        parts = split_html(text, 3500)
        for index, part in enumerate(parts):
            await deliver(lambda: bot.send_message(
                user_id, part, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                reply_markup=resubmit_kb if index + 1 == len(parts) else None,
                **author_reply_kwargs(entry),
            ))
        file_id = str(item.get("file_id") or payload.get("moderation_file_id") or "").strip()
        file_path = str(item.get("file_path") or "").strip()
        if file_id or (file_path and Path(file_path).is_file()):
            try:
                await deliver(lambda: send_plugin_document(
                    bot, user_id, item, file_id=file_id or None,
                    caption=t("notify_rework_request", author_lang), parse_mode=ParseMode.HTML,
                    reply_markup=resubmit_kb,
                ))
                return
            except Exception:
                logger.warning("event=rework.notify_author_file_failed user_id=%s request_id=%s",
                               user_id, request_id, exc_info=True)
        await deliver(lambda: bot.send_message(
            user_id, t("notify_rework_request", author_lang), parse_mode=ParseMode.HTML,
            reply_markup=resubmit_kb, disable_web_page_preview=True,
        ))
    except Exception:
        logger.exception("event=rework.notify_author_failed user_id=%s request_id=%s", user_id, request_id)


@router.callback_query(F.data.startswith("adm:rework:"))
async def on_admin_rework(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry:
        if not _ensure_request_role(cb, entry):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return
    else:
        if not _ensure_admin(cb):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return

    await _nav_push(state, f"adm:rework:{request_id}")
    await state.update_data(rework_request_id=request_id)
    await state.set_state(AdminFlow.entering_rework_comment)
    await answer(cb, _tr(cb, "admin_enter_rework_reason"), admin_cancel_kb(_lang_for(cb)), "admin")
    await ack(cb)


@router.message(AdminFlow.entering_rework_comment)
async def on_admin_enter_rework_comment(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    comment = telegram_html(message.html_text or message.text or "")
    if not comment:
        await message.answer(_tr(message, "need_text"), disable_web_page_preview=True)
        return

    data = await state.get_data()
    request_id = data.get("rework_request_id")
    if not request_id:
        await state.clear()
        await state.set_state(AdminFlow.menu)
        return

    entry = get_request_by_id(request_id)
    if entry and not _ensure_request_role(message, entry):
        return
    if not _is_super_admin(message):
        return

    if entry:
        from bot.services.validation import submission_fingerprint

        update_request_payload(
            request_id,
            {"rework_fingerprint": submission_fingerprint(entry.get("payload") or {})},
        )
        update_request_status(request_id, "rework", comment=comment, actor=_admin_actor_label(message), actor_id=_actor_id(message))
        add_audit_event(
            "moderation.rework",
            actor_id=message.from_user.id if message.from_user else None,
            actor=_admin_actor_label(message),
            request_id=str(request_id),
            details={"reason": comment},
        )
        _schedule_decision_cleanup(message.bot, entry, "Заявка отправлена на доработку", _admin_actor_label(message))
        from core.storage import flush_documents

        await flush_documents("requests")
        spawn_background(_notify_author_rework(message.bot, deepcopy(entry), comment))

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await _reset_menu_navigation(state)
    await answer(message, _tr(message, "admin_rework_done"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")


@router.callback_query(F.data.startswith("adm:msgauthor:"))
async def on_admin_msg_author(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if not entry or not entry.get("payload", {}).get("user_id"):
        await cb.answer(_tr(cb, "admin_request_not_found"), show_alert=True)
        return
    if not _ensure_request_role(cb, entry):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, f"adm:msgauthor:{request_id}")
    await state.update_data(author_msg_request_id=request_id)
    await state.set_state(AdminFlow.entering_author_message)
    await answer(cb, _tr(cb, "admin_enter_author_message"), admin_cancel_kb(_lang_for(cb)), "admin")
    await ack(cb)


@router.message(AdminFlow.entering_author_message)
async def on_admin_enter_author_message(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    text = telegram_html(message.html_text or message.html_caption or message.text or message.caption or "")
    if not text and not has_dialog_media(message):
        await message.answer(_tr(message, "need_text"), disable_web_page_preview=True)
        return

    data = await state.get_data()
    request_id = data.get("author_msg_request_id")
    entry = get_request_by_id(request_id) if request_id else None
    if not entry or not _ensure_request_role(message, entry):
        await state.clear()
        await state.set_state(AdminFlow.menu)
        return

    payload = entry.get("payload", {})
    user_id = payload.get("user_id")
    admin_user = message.from_user
    if not user_id or not admin_user:
        await state.clear()
        await state.set_state(AdminFlow.menu)
        return

    author_lang = get_lang(user_id)
    item = payload.get("plugin") or payload.get("icon") or {}
    plugin_name = plain_html(item.get("name") or "—")
    sender_label = user_mention(admin_user.id, admin_user.username)
    body = strip_blockquote_tags(text) or t("dialog_media_body", author_lang)
    delivered_messages = []
    try:
        delivered_messages = await send_dialog_message(
            message.bot,
            user_id,
            t("dialog_msg_to_author", author_lang, name=plugin_name, sender=sender_label, text=body),
            source=message,
        )
    except Exception:
        logger.exception(
            "event=dialog.notify_author_failed user_id=%s request_id=%s",
            user_id, request_id,
        )

    if delivered_messages:
        for delivered in delivered_messages:
            register_dialog_message(
                int(user_id), delivered.message_id,
                peer_id=admin_user.id, request_id=str(request_id),
                author_id=int(user_id), admin_id=admin_user.id,
            )
        add_audit_event(
            "moderation.message_author",
            actor_id=admin_user.id,
            actor=_admin_actor_label(message),
            request_id=str(request_id),
        )

    await state.clear()
    await state.set_state(AdminFlow.menu)
    result_key = "admin_author_message_sent" if delivered_messages else "admin_author_message_failed"
    await _reset_menu_navigation(state)
    await answer(message, _tr(message, result_key), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")


@router.callback_query(F.data.startswith("adm:ban:"))
async def on_admin_ban(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    parts = cb.data.split(":")
    request_id = parts[2]
    entry = get_request_by_id(request_id)
    if not entry:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    user_id = int((entry.get("payload", {}) or {}).get("user_id") or 0)
    if not user_id:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    await _nav_push(state, f"adm:ban:{request_id}")

    await cb.message.edit_reply_markup(reply_markup=admin_confirm_ban_kb(request_id, lang=lang))
    await ack(cb)


@router.callback_query(F.data.startswith("adm:ban_confirm:"))
async def on_admin_ban_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    parts = cb.data.split(":")
    request_id = parts[2]
    entry = get_request_by_id(request_id)
    if not entry:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    user_id = int((entry.get("payload", {}) or {}).get("user_id") or 0)
    if not user_id:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    delete_plugins = (parts[3] if len(parts) > 3 else "del") == "del"

    await _nav_push(state, f"adm:ban_confirm:{request_id}")

    ban_user(user_id, reason="Заблокирован администратором")
    update_request_status(request_id, "rejected", comment="Пользователь заблокирован", actor=_admin_actor_label(cb), actor_id=_actor_id(cb))

    _schedule_decision_cleanup(cb.bot, entry, "Заявка была отклонена", _admin_actor_label(cb))

    try:
        await cb.bot.send_message(
            user_id,
            t("user_banned_by_admin", get_lang(user_id)),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        pass

    removed_count = 0
    if delete_plugins:
        from core.user_store import get_user
        user_data = get_user(user_id)
        username = user_data.get("username", "")
        removal = await remove_user_content(user_id, username)
        removed_count = len(removal["removed_plugins"]) + len(removal["removed_icons"])

    await _reset_menu_navigation(state)
    await answer(
        cb,
        _tr(cb, "admin_user_banned", user_id=user_id)
        + (f"\n\nУдалено плагинов/иконок: {removed_count}" if removed_count
           else "\n\nПлагины оставлены в канале." if not delete_plugins else ""),
        admin_menu_kb(_admin_menu_role(cb), lang=lang),
        "profile",
    )
    await ack(cb)


@router.callback_query(F.data.startswith("adm:appeal:"))
async def on_admin_appeal_decision(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = cb.data.split(":")
    action = parts[2] if len(parts) > 2 else ""
    request_id = parts[3] if len(parts) > 3 else ""
    entry = get_request_by_id(request_id)
    if not entry or entry.get("type") != "unban_appeal":
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return
    if entry.get("status") != "pending":
        await cb.answer(_tr(cb, "appeal_already_handled"), show_alert=True)
        return

    user_id = int((entry.get("payload", {}) or {}).get("user_id") or 0)
    approved = action == "approve"
    banfinal = action == "banfinal"

    if approved:
        if user_id:
            unban_user(user_id)
        update_request_status(request_id, "published", comment="Апелляция одобрена", actor=_admin_actor_label(cb), actor_id=_actor_id(cb))
        add_audit_event("moderation.appeal_approved", actor_id=cb.from_user.id if cb.from_user else None,
                        actor=_admin_actor_label(cb), request_id=str(request_id))
    elif banfinal:
        from core.user_store import update_user
        if user_id:
            update_user(user_id, banned=True, ban_permanent=True)
        update_request_status(request_id, "rejected", comment="Апелляция отклонена, бан окончательный", actor=_admin_actor_label(cb), actor_id=_actor_id(cb))
        add_audit_event("moderation.appeal_banned_final", actor_id=cb.from_user.id if cb.from_user else None,
                        actor=_admin_actor_label(cb), request_id=str(request_id), details={"user_id": user_id})
    else:
        update_request_status(request_id, "rejected", comment="Апелляция отклонена")
        add_audit_event("moderation.appeal_denied", actor_id=cb.from_user.id if cb.from_user else None,
                        actor=_admin_actor_label(cb), request_id=str(request_id))

    await delete_forum_request_message(cb.bot, entry)

    if user_id:
        notify_key = "appeal_approved" if approved else "appeal_banned_final" if banfinal else "appeal_denied"
        try:
            await cb.bot.send_message(
                user_id,
                t(notify_key, get_lang(user_id)),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("event=appeal.notify_user_failed user_id=%s", user_id)

    done_key = "appeal_done_unban" if approved else "appeal_done_banfinal" if banfinal else "appeal_done_deny"
    await ack(cb, _tr(cb, done_key), show_alert=True)


_AUDIT_LOG_PER_PAGE = 10

_AUDIT_LABELS = {
    "moderation.vote": f"{emoji_html('vote')} Голос",
    "moderation.vote_reason": f"{emoji_html('vote')} Причина голоса",
    "moderation.reject": f"{emoji_html('no')} Отклонено",
    "moderation.rework": f"{emoji_html('edit')} На доработку",
    "moderation.publish_success": f"{emoji_html('yes')} Опубликовано",
    "moderation.request_deleted": f"{emoji_html('delete')} Заявка удалена",
    "moderation.plugin_blocked": f"{emoji_html('ban')} Плагин заблокирован",
    "moderation.plugin_unblocked": f"{emoji_html('updates')} Плагин разблокирован",
    "moderation.appeal_submitted": f"{emoji_html('updates')} Подана апелляция",
    "moderation.appeal_approved": f"{emoji_html('yes')} Апелляция одобрена",
    "moderation.appeal_denied": f"{emoji_html('no')} Апелляция отклонена",
    "moderation.appeal_banned_final": f"{emoji_html('ban')} Апелляция отклонена, бан",
    "moderation.author_question": f"{emoji_html('support')} Вопрос автора",
    "moderation.message_author": f"{emoji_html('support')} Сообщение автору",
}


def _audit_line(event: dict, lang: str = "ru") -> str:
    stamp = str(event.get("created_at") or "")[:16].replace("T", " ")
    name = _AUDIT_LABELS.get(str(event.get("event")), str(event.get("event") or "—"))
    approval_label = {
        "moderation.request_approved": "request_status_approved",
        "moderation.request_scheduled": "request_status_scheduled",
        "moderation.request_unscheduled": "btn_unschedule",
        "moderation.request_updated": "audit_request_updated",
        "moderation.request_withdrawn": "pending_withdrawn_admin",
    }.get(str(event.get("event")))
    if approval_label:
        name = t(approval_label, lang)
    actor = str(event.get("actor") or "").strip()
    req = str(event.get("request_id") or "").strip()
    parts = [f"<code>{plain_html(stamp)}</code>", name]
    if actor:
        parts.append(actor)
    line = " · ".join(parts)
    if req:
        line += f"\n    <code>{plain_html(req)}</code>"
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    bits = [f"{k}={v}" for k, v in details.items() if k in {"vote", "anonymous", "plugin_id", "user_id"}]
    if bits:
        line += f"  ({plain_html(', '.join(str(b) for b in bits))})"
    return line


async def _render_audit_log(target, state: FSMContext, page: int) -> None:
    await _nav_push(state, f"adm:auditlog:{page}")
    lang = _lang_for(target)
    events, total = audit_events_page(page, _AUDIT_LOG_PER_PAGE)
    total_pages = max(1, math.ceil(total / _AUDIT_LOG_PER_PAGE)) if total else 1
    if not events:
        text = _tr(target, "admin_audit_log_empty")
    else:
        text = _tr(
            target, "admin_audit_log_title",
            current=page + 1, total=total_pages, count=total,
            events="\n\n".join(_audit_line(e, lang) for e in events),
        )
    await state.set_state(AdminFlow.menu)
    await answer(target, text, admin_audit_log_kb(page, total_pages, lang=lang), "admin")


@router.callback_query(F.data.regexp(r"^adm:auditlog:\d+$"))
async def on_admin_audit_log(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, "adm:config")
    await _nav_push(state, "adm:maint")
    await _render_audit_log(cb, state, int(cb.data.split(":")[2]))
    await ack(cb)


async def _render_blocklist(target, state: FSMContext) -> None:
    lang = _lang_for(target)
    cfg = get_config()
    items = [str(x) for x in (cfg.get("plugin_blocklist") or []) if str(x).strip()]
    text = _tr(target, "admin_blocklist_title", count=len(items)) if items else _tr(target, "admin_blocklist_empty")
    await state.set_state(AdminFlow.menu)
    await answer(target, text, admin_blocklist_kb(items, lang=lang), "admin")


@router.callback_query(F.data == "adm:blocklist")
async def on_admin_blocklist(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    data = await state.get_data()
    await _nav_push(state, f"adm:banned:{int(data.get('banned_page') or 0)}")
    await _nav_push(state, "adm:blocklist")
    await _render_blocklist(cb, state)
    await ack(cb)


@router.callback_query(F.data.regexp(r"^adm:unblock:.+$"))
async def on_admin_unblock(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    plugin_id = cb.data.split(":", 2)[2]
    cfg = load_config()
    items = [str(x) for x in (cfg.get("plugin_blocklist") or []) if str(x).strip()]
    remaining = [x for x in items if x.strip().lower() != plugin_id.strip().lower()]
    if len(remaining) != len(items):
        cfg["plugin_blocklist"] = remaining
        save_config(cfg)
        invalidate("config")
        add_audit_event(
            "moderation.plugin_unblocked",
            actor_id=cb.from_user.id if cb.from_user else None,
            actor=_admin_actor_label(cb),
            details={"plugin_id": plugin_id},
        )
    await _render_blocklist(cb, state)
    await ack(cb, _tr(cb, "admin_unblocked", id=plugin_id), show_alert=True)


_QUIZ_PAGE_SIZE = 8


def _quiz_edit_lang(data: dict) -> str:
    return normalize_language(data.get("quiz_edit_lang"))


def _quiz_block(question: dict, lang: str) -> tuple[str, list[str]]:
    block = localized_value(question, lang, ["", []])
    return str(block[0]), list(block[1])


async def _render_quiz_list(target, state: FSMContext, page: int) -> None:
    lang = _lang_for(target)
    questions = load_quiz_questions()
    total_pages = max(1, math.ceil(len(questions) / _QUIZ_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    await _nav_push(state, f"adm:quiz:{page}")
    chunk = questions[page * _QUIZ_PAGE_SIZE:(page + 1) * _QUIZ_PAGE_SIZE]

    edit_lang = _quiz_edit_lang(await state.get_data())
    items = []
    for index, question in enumerate(chunk, start=page * _QUIZ_PAGE_SIZE + 1):
        text, _ = _quiz_block(question, edit_lang)
        items.append((f"{index}. {text}", question["id"]))

    await state.set_state(AdminFlow.menu)
    await answer(
        target,
        _tr(target, "admin_quiz_title", count=len(questions), per_quiz=QUIZ_QUESTIONS_PER_RUN),
        admin_quiz_list_kb(items, page, total_pages, lang=lang),
        "admin",
    )


async def _render_quiz_item(target, state: FSMContext, question_id: str) -> None:
    lang = _lang_for(target)
    questions = load_quiz_questions()
    question = next((q for q in questions if q["id"] == question_id), None)
    if not question:
        await _render_quiz_list(target, state, 0)
        return

    edit_lang = _quiz_edit_lang(await state.get_data())
    text, options = _quiz_block(question, edit_lang)
    rendered = "\n".join(
        f"{emoji_html('yes') if idx == 0 else '•'} {plain_html(option)}" for idx, option in enumerate(options)
    )
    body = _tr(
        target, "admin_quiz_view",
        index=questions.index(question) + 1, total=len(questions),
        id=plain_html(question["id"]), text=plain_html(text), options=rendered,
    ) + "\n\n" + _tr(target, "admin_quiz_correct_hint")

    await state.update_data(quiz_current=question_id)
    await _nav_push(state, f"adm:quiz:view:{question_id}")
    await state.set_state(AdminFlow.menu)
    await answer(target, body, admin_quiz_item_kb(question_id, lang=lang, edit_lang=edit_lang), "admin")


@router.callback_query(F.data.regexp(r"^adm:quiz:\d+$"))
async def on_admin_quiz(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    page = int(cb.data.split(":")[2])
    await _nav_push(state, "adm:config")
    await _nav_push(state, "adm:config_section:moderation")
    await _nav_push(state, "adm:quiz:0")
    await _render_quiz_list(cb, state, page)
    await ack(cb)


@router.callback_query(F.data.startswith("adm:quiz:view:"))
async def on_admin_quiz_view(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _render_quiz_item(cb, state, cb.data.split(":", 3)[3])
    await ack(cb)


@router.callback_query(F.data.startswith("adm:quiz:lang:"))
async def on_admin_quiz_lang(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = cb.data.split(":")
    edit_lang = normalize_language(parts[4], None)
    if not edit_lang:
        await ack(cb)
        return
    await state.update_data(quiz_edit_lang=edit_lang)
    await _render_quiz_item(cb, state, parts[3])
    await ack(cb)


@router.callback_query(F.data.startswith("adm:quiz:edit_text:"))
async def on_admin_quiz_edit_text(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    edit_lang = _quiz_edit_lang(await state.get_data())
    await state.update_data(quiz_current=cb.data.split(":", 3)[3])
    await state.set_state(AdminFlow.editing_quiz_text)
    await answer(cb, _tr(cb, "admin_quiz_edit_text", value=edit_lang.upper()), admin_cancel_kb(_lang_for(cb)), "admin")
    await ack(cb)


@router.callback_query(F.data.startswith("adm:quiz:edit_options:"))
async def on_admin_quiz_edit_options(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    edit_lang = _quiz_edit_lang(await state.get_data())
    await state.update_data(quiz_current=cb.data.split(":", 3)[3])
    await state.set_state(AdminFlow.editing_quiz_options)
    await answer(
        cb,
        _tr(cb, "admin_quiz_edit_options", value=edit_lang.upper(), count=QUIZ_OPTIONS_PER_QUESTION),
        admin_cancel_kb(_lang_for(cb)),
        "admin",
    )
    await ack(cb)


@router.callback_query(F.data.startswith("adm:quiz:del:"))
async def on_admin_quiz_delete(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    delete_quiz_question(cb.data.split(":", 3)[3])
    await _render_quiz_list(cb, state, 0)
    await ack(cb, _tr(cb, "admin_quiz_deleted"))


@router.callback_query(F.data == "adm:quiz:add")
async def on_admin_quiz_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await state.set_state(AdminFlow.adding_quiz_question)
    await answer(cb, _tr(cb, "admin_quiz_add"), admin_cancel_kb(_lang_for(cb)), "admin")
    await ack(cb)


@router.callback_query(F.data == "adm:quiz:restore")
async def on_admin_quiz_restore(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    count = restore_quiz_defaults()
    await _render_quiz_list(cb, state, 0)
    await ack(cb, _tr(cb, "admin_quiz_restored", count=count), show_alert=True)


@router.message(AdminFlow.editing_quiz_text)
async def on_admin_quiz_text_value(message: Message, state: FSMContext) -> None:
    if not _is_super_admin(message):
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer(_tr(message, "need_text"), parse_mode=ParseMode.HTML)
        return
    data = await state.get_data()
    update_quiz_question(str(data.get("quiz_current") or ""), _quiz_edit_lang(data), text=text)
    await _render_quiz_item(message, state, str(data.get("quiz_current") or ""))


@router.message(AdminFlow.editing_quiz_options)
async def on_admin_quiz_options_value(message: Message, state: FSMContext) -> None:
    if not _is_super_admin(message):
        return
    options = [line.strip() for line in (message.text or "").splitlines() if line.strip()]
    if len(options) < 2:
        await message.answer(_tr(message, "admin_quiz_bad_format"), parse_mode=ParseMode.HTML)
        return
    data = await state.get_data()
    update_quiz_question(str(data.get("quiz_current") or ""), _quiz_edit_lang(data), options=options)
    await _render_quiz_item(message, state, str(data.get("quiz_current") or ""))


@router.message(AdminFlow.adding_quiz_question)
async def on_admin_quiz_add_value(message: Message, state: FSMContext) -> None:
    if not _is_super_admin(message):
        return
    lines = [line.strip() for line in (message.text or "").splitlines() if line.strip()]
    if len(lines) < 4:
        await message.answer(_tr(message, "admin_quiz_bad_format"), parse_mode=ParseMode.HTML)
        return
    data = await state.get_data()
    if not add_quiz_question(lines[0], _quiz_edit_lang(data), lines[1], lines[2:]):
        await message.answer(_tr(message, "admin_quiz_bad_id"), parse_mode=ParseMode.HTML)
        return
    await _render_quiz_item(message, state, lines[0])


@router.callback_query(F.data.startswith("adm:media:"))
async def on_admin_show_media(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    token = cb.data.split(":", 2)[2]
    entry = get_request_by_callback_token(token) or get_request_by_id(token)
    media = _comment_media_of_entry(entry) if entry else []
    if not media:
        await cb.answer(_tr(cb, "admin_media_empty"), show_alert=True)
        return
    from bot.services.moderation import send_media_group

    chat_id = cb.message.chat.id if cb.message else (cb.from_user.id if cb.from_user else 0)
    thread_id = getattr(cb.message, "message_thread_id", None) if cb.message else None
    await send_media_group(cb.bot, chat_id, media, topic_id=thread_id)
    await ack(cb)


@router.message(AdminFlow.uploading_request_file, F.document)
async def on_admin_request_file_upload(message: Message, state: FSMContext) -> None:
    if not _is_super_admin(message):
        return
    request_id = (await state.get_data()).get("current_request")
    entry = get_request_by_id(request_id)
    if not can_edit_request(entry):
        await state.set_state(AdminFlow.menu)
        await message.answer(_tr(message, "resubmit_expired"))
        return
    original_path = (entry["payload"].get("plugin") or {}).get("file_path")

    from bot.services.validation import validate_request_replacement
    from bot.services.versioning import get_min_supported_version

    try:
        parsed = await process_plugin_file(message.bot, message.document)
    except ValueError as exc:
        await message.answer(_tr(message, "admin_generic_error", error=plain_html(exc)), parse_mode=ParseMode.HTML)
        return
    if not _acquire_request_lock(request_id):
        await message.answer(_tr(message, "admin_request_processing"))
        return
    try:
        current = get_request_by_id(request_id)
        replacement = parsed.to_dict()
        valid, error = validate_request_replacement(current or {}, replacement)
        if not valid:
            await message.answer(_tr(message, error, current=plain_html(parsed.min_version),
                                      min=get_min_supported_version()), parse_mode=ParseMode.HTML)
            return
        payload = current["payload"]
        if (payload.get("plugin") or {}).get("file_path") != original_path:
            await message.answer(_tr(message, "pending_request_changed"))
            return
        fields = category_fields(categories_for(payload) or categories_for(replacement))
        replacement["categories"] = fields["category_keys"]
        actor = _admin_actor_label(message)
        updated = update_request_payload(request_id, {
            **fields, "plugin": replacement, "moderation_file_id": None, "moderation_file_name": None,
            "last_publish_error": None, "last_publish_error_at": None, "publish_retry_at": None, "file_replaced_by": actor,
        })
        add_audit_event("moderation.file_replaced", actor_id=_actor_id(message), actor=actor, request_id=request_id,
                        details={"file": replacement.get("file_path") or ""})
        from core.storage import flush_documents

        await flush_documents("requests")
    finally:
        _release_request_lock(request_id)
    await state.update_data(**fields, category_selection=None)
    await _render_publish_editor(message, state, updated)


@router.message(AdminFlow.uploading_request_file)
async def on_admin_request_file_invalid(message: Message, state: FSMContext) -> None:
    if _is_super_admin(message):
        await message.answer(_tr(message, "admin_send_plugin_file"), parse_mode=ParseMode.HTML)
