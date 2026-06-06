import logging
import math
import time
import asyncio
import html
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.cache import (
    get_admin_role,
    get_admins,
    get_admins_icons,
    get_admins_plugins,
    get_admins_super,
    get_categories,
    get_config,
    invalidate,
)
from bot.constants import PAGE_SIZE
from bot.context import get_language, get_lang
from bot.callback_tokens import decode_slug, encode_slug
from bot.formatting import plain_html, strip_blockquote_tags, telegram_html
from bot.helpers import answer
from bot.menu_owner import MenuOwnerMiddleware, remember_menu_owner
from bot.keyboards import (
    admin_actions_kb,
    admin_banned_kb,
    admin_broadcast_confirm_kb,
    admin_post_confirm_kb,
    admin_config_kb,
    admin_config_admins_kb,
    admin_config_channels_kb,
    admin_config_moderation_kb,
    admin_config_other_kb,
    admin_confirm_ban_kb,
    admin_icons_section_kb,
    admin_post_section_kb,
    admin_updates_list_kb,
    admin_manage_admins_kb,
    admin_menu_kb,
    admin_plugins_list_kb,
    admin_plugins_section_kb,
    admin_queue_kb,
    admin_review_kb,
    admin_reject_kb,
    admin_confirm_delete_plugin_kb,
    admin_cancel_kb,
    cancel_kb,
    draft_category_kb,
    draft_edit_kb,
    draft_lang_kb,
    icon_draft_edit_kb,
    admin_scheduled_list_kb,
    admin_scheduled_item_kb,
    admin_scheduled_post_kb,
    admin_scheduled_posts_list_kb,
)
from bot.services.publish import (
    add_submitter_to_plugin,
    add_submitter_to_iconpack,
    add_to_catalog,
    add_icon_to_catalog,
    build_channel_post,
    build_icon_channel_post,
    publish_icon,
    publish_plugin,
    remove_plugin_entry,
    remove_user_content,
    clear_updated_plugins,
    update_plugin,
    update_icon_catalog_entry,
    update_catalog_entry,
)
from bot.services.moderation import (
    delete_forum_request_message,
    is_moderation_forum_chat,
    moderation_config,
    send_request_to_forum,
    vote_summary,
)
from bot.routers.catalog_flow import build_inline_preview, build_plugin_preview
from bot.services.submission import PluginData, process_icon_file, process_plugin_file
from bot.states import AdminFlow
from bot.icons import emoji_html
from bot.texts import TEXTS, t
from catalog import find_icon_by_slug, find_plugin_by_slug, list_published_icons, list_published_plugins
from request_store import (
    cleanup_hidden_requests,
    delete_requests_by_plugin_id,
    get_request_by_id,
    get_requests,
    update_request_payload,
    update_request_status,
)
from storage import load_plugins, save_config, save_plugins
from user_store import ban_user, get_banned_users, get_user_language, is_broadcast_enabled, list_users, unban_user
from subscription_store import list_subscribers
from catalog import invalidate_catalog_cache

logger = logging.getLogger(__name__)

TZ_UTC_PLUS_5 = timezone(timedelta(hours=5))
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
    if not isinstance(entry, dict):
        return {}
    preferred = entry.get(lang)
    if isinstance(preferred, dict):
        return preferred
    fallback_ru = entry.get("ru")
    if isinstance(fallback_ru, dict):
        return fallback_ru
    fallback_en = entry.get("en")
    if isinstance(fallback_en, dict):
        return fallback_en
    return {}


def _localized_name(entry: dict | None, lang: str) -> str:
    if not isinstance(entry, dict):
        return ""
    return _localized_block(entry, lang).get("name") or entry.get("name") or entry.get("slug") or ""


def _localized_author(authors: dict | None, lang: str) -> str:
    if not isinstance(authors, dict):
        return ""
    return authors.get(lang) or authors.get("ru") or authors.get("en") or ""


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


def _review_meta_block(entry: dict) -> str:
    parts: list[str] = []
    not_before = _publish_not_before_dt_utc(entry)
    if not_before:
        dt_str = not_before.astimezone(TZ_UTC_PLUS_5).strftime("%d.%m.%Y %H:%M")
        parts.append(f"<b>Не публиковать раньше:</b> <code>{dt_str} UTC+5</code>")
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    if isinstance(payload, dict):
        last_error = str(payload.get("last_publish_error") or "").strip()
        if last_error:
            error_at = _parse_dt_utc(payload.get("last_publish_error_at"))
            suffix = ""
            if error_at:
                suffix = f" ({error_at.astimezone(TZ_UTC_PLUS_5).strftime('%d.%m.%Y %H:%M')} UTC+5)"
            parts.append(
                "<b>Последняя ошибка публикации"
                f"{suffix}:</b>\n<blockquote expandable>{html.escape(last_error)}</blockquote>"
            )
    parts.append(vote_summary(entry))
    return "\n\n".join(parts)


async def _render_scheduled_list(target: CallbackQuery | Message, state: FSMContext, page: int) -> None:
    lang = _lang_for(target)
    requests = list(get_requests(status="scheduled"))

    # show only plugin requests
    filtered: list[dict] = []
    for r in requests:
        payload = (r.get("payload") or {}) if isinstance(r, dict) else {}
        if payload.get("submission_type") == "icon" or payload.get("icon"):
            continue
        filtered.append(r)

    if not filtered:
        await answer(target, _tr(target, "admin_scheduled_empty"), admin_plugins_section_kb(lang=lang), "admin")
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
    lang = _lang_for(target)
    entry = get_request_by_id(request_id)
    if not entry:
        await answer(target, _tr(target, "not_found"), admin_plugins_section_kb(lang=lang), "admin")
        return
    dt = _scheduled_dt_utc(entry)
    dt_str = dt.astimezone(TZ_UTC_PLUS_5).strftime("%d.%m.%Y %H:%M") if dt else "—"
    meta = _review_meta_block(entry)
    text = f"{_render_request_draft(entry)}\n\n<b>Отложено на:</b> <code>{dt_str}</code>"
    if meta:
        text = f"{text}\n\n{meta}"
    await state.update_data(scheduled_current_request=str(request_id))
    await state.set_state(AdminFlow.menu)
    await answer(target, text, admin_scheduled_item_kb(str(request_id), lang=lang), "admin")


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


def stop_scheduled_posts_cleanup_worker() -> None:
    global _scheduled_posts_cleanup_task
    if _scheduled_posts_cleanup_task and not _scheduled_posts_cleanup_task.done():
        _scheduled_posts_cleanup_task.cancel()
    _scheduled_posts_cleanup_task = None


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


async def _nav_push(state: FSMContext, token: str) -> None:
    data = await state.get_data()
    stack = data.get(_NAV_STACK_KEY)
    if not isinstance(stack, list):
        stack = []
    if stack and stack[-1] == token:
        return
    stack.append(token)
    if len(stack) > 30:
        stack = stack[-30:]
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


async def _render_menu(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    await state.set_state(AdminFlow.menu)
    await answer(cb, _admin_menu_text(lang), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")


def _admin_request_counts() -> dict[str, int]:
    pending = list(get_requests(status="pending")) + list(get_requests(status="error"))
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
        "scheduled_posts": len(_cleanup_scheduled_posts()),
    }


def _admin_menu_text(lang: str) -> str:
    c = _admin_request_counts()
    if lang == "en":
        return (
            "<b>Admin Panel</b>\n\n"
            f"Plugins: {c['plugin_pending']} pending, {c['scheduled_plugins']} scheduled\n"
            f"Icons: {c['icon_pending']} pending\n"
            f"Posts: {c['scheduled_posts']} scheduled"
        )
    return (
        "<b>Админ-панель</b>\n\n"
        f"Плагины: {c['plugin_pending']} заявок, {c['scheduled_plugins']} отложено\n"
        f"Иконки: {c['icon_pending']} заявок\n"
        f"Посты: {c['scheduled_posts']} по расписанию"
    )


def _plugins_section_text(lang: str) -> str:
    c = _admin_request_counts()
    if lang == "en":
        return (
            f"{t('admin_section_plugins', lang)}\n\n"
            f"New: {c['new_plugins']}\n"
            f"Updates: {c['updates']}\n"
            f"Deletes: {c['deletes']}\n"
            f"Scheduled: {c['scheduled_plugins']}"
        )
    return (
        f"{t('admin_section_plugins', lang)}\n\n"
        f"Новые: {c['new_plugins']}\n"
        f"Обновления: {c['updates']}\n"
        f"Удаления: {c['deletes']}\n"
        f"Отложенные: {c['scheduled_plugins']}"
    )


def _icons_section_text(lang: str) -> str:
    c = _admin_request_counts()
    if lang == "en":
        return f"{t('admin_section_icons', lang)}\n\nPending requests: {c['icon_pending']}"
    return f"{t('admin_section_icons', lang)}\n\nЗаявки: {c['icon_pending']}"


def _post_section_text(lang: str) -> str:
    c = _admin_request_counts()
    title = t("admin_btn_post", lang)
    if lang == "en":
        return f"<b>{title}</b>\n\nScheduled posts: {c['scheduled_posts']}"
    return f"<b>{title}</b>\n\nПосты по расписанию: {c['scheduled_posts']}"


async def _render_config(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    await state.set_state(AdminFlow.menu)
    await answer(cb, _tr(cb, "admin_settings_title"), admin_config_kb(lang=lang), "admin")


_CONFIG_FIELD_LABEL_KEYS: dict[str, str] = {
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
}

_CONFIG_INT_FIELDS = {
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
    if path.startswith("moderation."):
        moderation = moderation_config()
        return {
            "moderation.forum_chat_id": moderation.get("chat_id"),
            "moderation.forum_topic_id": moderation.get("topic_id"),
            "moderation.vote_threshold": moderation.get("threshold"),
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
    if value is None or value == "":
        return "—"
    return str(value)


def _parse_config_value(path: str, raw: str) -> Any:
    text = raw.strip()
    if path in _CONFIG_LIST_FIELDS:
        return [item.strip() for item in text.split(",") if item.strip()]
    if path in _CONFIG_INT_FIELDS:
        value = int(text)
        if path == "moderation.vote_threshold":
            value = max(1, value)
        if path == "moderation.forum_chat_id" and value > 0:
            value = int(f"-100{value}")
        return value
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
    await state.set_state(AdminFlow.editing_config)
    title = _admins_title(field, cb)

    links = "\n".join([f"<a href=\"tg://user?id={aid}\">{aid}</a>" for aid in admin_ids])
    links_block = f"\n\n{links}" if links else ""
    msg = await answer(
        cb,
        f"<b>{title}</b>\n\n{_tr(cb, 'admin_choose_action')}{links_block}",
        admin_manage_admins_kb(field, admin_ids, lang=lang),
        "admin",
    )
    if msg:
        await state.update_data(config_message_id=msg.message_id)


def _admins_title(field: str, target: CallbackQuery | Message | int | None) -> str:
    key = {
        "admins_super": "admin_cfg_superadmins",
        "admins_plugins": "admin_cfg_admins_plugins",
        "admins_icons": "admin_cfg_admins_icons",
    }.get(field, "admin_cfg_admins")
    return _tr(target, key)


async def _render_broadcast_enter(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    await state.set_state(AdminFlow.entering_broadcast)
    await state.update_data(broadcast_message_id=cb.message.message_id if cb.message else None)
    msg = await answer(cb, _tr(cb, "admin_prompt_broadcast"), admin_cancel_kb(lang), "admin")
    if msg:
        await state.update_data(broadcast_message_id=msg.message_id)


async def _render_queue(cb: CallbackQuery, state: FSMContext, token: str) -> None:
    lang = _lang_for(cb)
    parts = token.split(":")
    queue_type = parts[2] if len(parts) > 2 else "plugins"
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    visible_requests = list(get_requests(status="pending")) + list(get_requests(status="error"))

    if queue_type == "icons":
        requests = [
            r for r in visible_requests
            if r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon")
        ]
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
        await _render_menu(cb, state)
        return

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
            prefix = "Удаление "
            row_icon = "delete"
        elif request_type == "update":
            prefix = "Обновление: "
            row_icon = "updates"
        else:
            prefix = ""
            row_icon = None
        if entry.get("status") == "error" or payload.get("last_publish_error"):
            prefix = f"Ошибка: {prefix}"
            row_icon = row_icon or "warning"
        label = f"{name} v{version}" if version else f"{name}"
        label = f"{prefix}{label}"
        items.append((label, entry["id"], row_icon))

    if queue_type == "icons":
        title = _tr(cb, "admin_queue_title_icons")
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
    msg = await answer(cb, caption, admin_queue_kb(items, page, total_pages, queue_type, lang=lang), "admin")
    if msg:
        pass


async def _render_review(cb: CallbackQuery, state: FSMContext, token: str) -> None:
    lang = _lang_for(cb)
    request_id = token.split(":")[2] if ":" in token else token
    entry = get_request_by_id(request_id)
    if not entry:
        await _render_menu(cb, state)
        return
    await state.set_state(AdminFlow.reviewing)
    await state.update_data(current_request=request_id, draft_message_id=cb.message.message_id if cb.message else None)

    payload = entry.get("payload", {})
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        draft_text = f"{_render_request_draft(entry)}\n\n{_review_meta_block(entry)}"
        if _is_super_admin(cb):
            kb = icon_draft_edit_kb(lang=lang)
        else:
            kb = admin_review_kb(request_id, payload.get("user_id", 0), lang=lang, allow_publish=False)
        msg = await answer(cb, draft_text, kb, "iconpacks")
        if msg:
            await state.update_data(draft_message_id=msg.message_id)
        return

    draft_text = _render_request_draft(entry)
    full_text = f"{draft_text}\n\n{_review_meta_block(entry)}"
    msg = await answer(
        cb,
        full_text,
        admin_review_kb(
            request_id,
            payload.get("user_id", 0),
            lang=lang,
            allow_publish=_is_super_admin(cb),
        ),
        "admin",
    )
    if msg:
        await state.update_data(draft_message_id=msg.message_id)


async def _render_nav_token(cb: CallbackQuery, state: FSMContext, token: str) -> None:
    lang = _lang_for(cb)
    if token == "adm:menu":
        await _render_menu(cb, state)
    elif token == "adm:section:plugins":
        await state.set_state(AdminFlow.menu)
        await answer(cb, _plugins_section_text(lang), admin_plugins_section_kb(lang=lang), "admin")
    elif token == "adm:section:updates":
        await _render_updates_list(cb, state, 0)
    elif token == "adm:section:icons":
        await state.set_state(AdminFlow.menu)
        await answer(cb, _icons_section_text(lang), admin_icons_section_kb(lang=lang), "admin")
    elif token == "adm:section:post":
        await state.set_state(AdminFlow.menu)
        await answer(cb, _post_section_text(lang), admin_post_section_kb(lang=lang), "admin")
    elif token == "adm:config":
        await _render_config(cb, state)
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
        await _render_broadcast_enter(cb, state)
    elif token == "adm:post":
        await state.set_state(AdminFlow.menu)
        await answer(cb, _post_section_text(lang), admin_post_section_kb(lang=lang), "admin")
    elif token.startswith("adm:queue:"):
        await _render_queue(cb, state, token)
    elif token.startswith("adm:review:"):
        await _render_review(cb, state, token)
    else:
        await _render_menu(cb, state)


def _can_schedule_request(entry: dict | None) -> bool:
    if not isinstance(entry, dict):
        return False
    req_type = entry.get("type", "new")
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        return False
    return req_type not in {"update", "delete"}


def _render_request_draft(entry: dict) -> str:
    payload = entry.get("payload", {})
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        return build_icon_channel_post(entry)

    plugin = payload.get("plugin", {})
    fallback_desc = plugin.get("description", "")
    patched_payload = {
        **payload,
        "description_ru": payload.get("description_ru") or fallback_desc,
        "description_en": payload.get("description_en") or fallback_desc,
    }
    return build_channel_post({"payload": patched_payload})


def _forum_request_text_and_file(entry: dict) -> tuple[str, str | None]:
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    plugin = payload.get("plugin", {}) if isinstance(payload.get("plugin"), dict) else {}
    icon = payload.get("icon", {}) if isinstance(payload.get("icon"), dict) else {}
    user_id = payload.get("user_id", 0)
    username = str(payload.get("username") or "").strip()
    request_type = entry.get("type", "new")
    submission_type = payload.get("submission_type") or ("icon" if icon else "plugin")
    request_id = entry.get("id", "?")
    user_link = f"@{plain_html(username)}" if username else f"<code>{plain_html(user_id or '—')}</code>"
    file_path = plugin.get("file_path") or icon.get("file_path")

    if request_type == "update":
        changelog = payload.get("changelog", "—")
        old_plugin = payload.get("old_plugin", {}) if isinstance(payload.get("old_plugin"), dict) else {}
        old_locale = old_plugin.get("ru") or old_plugin.get("en") or {}
        old_version = old_locale.get("version") or "?"
        text = t(
            "admin_request_update",
            "ru",
            id=request_id,
            name=plain_html(plugin.get("name") or "—"),
            old_version=plain_html(old_version),
            version=plain_html(plugin.get("version") or "—"),
            min_version=plain_html(plugin.get("min_version") or "—"),
            changelog=strip_blockquote_tags(telegram_html(changelog)) or "—",
            user=user_link,
        )
    elif request_type == "delete":
        delete_slug = payload.get("delete_slug") or plugin.get("id") or "—"
        text = t(
            "admin_request_delete",
            "ru",
            id=request_id,
            name=plain_html(plugin.get("name") or "—"),
            slug=plain_html(delete_slug),
            user=user_link,
        )
    elif submission_type == "icon":
        text = t(
            "admin_request_icon",
            "ru",
            id=request_id,
            name=plain_html(icon.get("name") or "—"),
            author=plain_html(icon.get("author") or "—"),
            version=plain_html(icon.get("version") or "—"),
            count=plain_html(icon.get("count") or 0),
            user=user_link,
        )
    else:
        text = t(
            "admin_request_plugin",
            "ru",
            id=request_id,
            draft=_render_request_draft(entry),
            user=user_link,
        )

    admin_comment = payload.get("admin_comment")
    if admin_comment:
        text += "\n\n" + t("admin_request_comment", "ru", comment=strip_blockquote_tags(telegram_html(admin_comment)))
    return text, str(file_path) if file_path else None


async def _notify_subscribers(
    bot,
    slug: str | None,
    plugin: dict,
    changelog: str | None = None,
) -> None:
    if not slug:
        return

    entry = find_plugin_by_slug(slug) or {}
    plugin_link = (entry.get("channel_message", {}) or {}).get("link")
    changes = strip_blockquote_tags(telegram_html(changelog)) or "—"

    for user_id in list_subscribers(slug):
        lang = get_lang(user_id)
        locale = _localized_block(entry, lang)
        raw_name = plugin.get("name") or locale.get("name") or slug
        name = f'<a href="{plugin_link}"><b>{raw_name}</b></a>' if plugin_link else f"<b>{raw_name}</b>"
        version = plugin.get("version") or locale.get("version") or "—"
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("btn_open", lang),
                        callback_data=f"plugin:{encode_slug(slug)}",
                    )
                ]
            ]
        )
        try:
            await bot.send_message(
                user_id,
                t(
                    "notify_subscription_update",
                    lang,
                    name=name,
                    version=version,
                    changelog=changes,
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
        except Exception:
            continue


def _ensure_admin(cb: CallbackQuery | Message) -> bool:
    user = cb.from_user if isinstance(cb, CallbackQuery) else cb.from_user
    return bool(user and user.id in get_admins())


def _ensure_admin_role(cb: CallbackQuery | Message, role: str) -> bool:
    user = cb.from_user if isinstance(cb, CallbackQuery) else cb.from_user
    if not user:
        return False
    if user.id in get_admins_super():
        return True
    if role == "plugins":
        return user.id in get_admins_plugins()
    if role == "icons":
        return user.id in get_admins_icons()
    return False


def _admin_menu_role(cb: CallbackQuery | Message) -> str | None:
    user = cb.from_user if isinstance(cb, CallbackQuery) else cb.from_user
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


def _admin_actor_label(target: CallbackQuery | Message) -> str:
    user = target.from_user if isinstance(target, CallbackQuery) else target.from_user
    if not user:
        return "<code>?</code>"
    if user.username:
        return f"@{user.username}"
    return f"<code>{user.id}</code>"


async def _finalize_admin_notify_messages(
    bot,
    entry: dict,
    decision_text: str,
    actor_label: str,
) -> None:
    if not isinstance(entry, dict):
        return
    request_id = str(entry.get("id") or "")
    if not request_id:
        return
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    mapping = payload.get("admin_notify_messages")
    if not isinstance(mapping, dict) or not mapping:
        return

    final_text = f"{decision_text} {actor_label}"

    for admin_id_str, info in mapping.items():
        try:
            admin_id = int(admin_id_str)
        except Exception:
            continue
        if not isinstance(info, dict):
            continue
        kind = info.get("kind")
        msg_id = info.get("message_id")
        if not msg_id:
            continue
        try:
            if kind == "document":
                await bot.edit_message_caption(
                    chat_id=admin_id,
                    message_id=int(msg_id),
                    caption=final_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                )
            else:
                await bot.edit_message_text(
                    final_text,
                    chat_id=admin_id,
                    message_id=int(msg_id),
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                    disable_web_page_preview=True,
                )
        except Exception:
            pass

    try:
        update_request_payload(request_id, {"admin_notify_messages": {}})
    except Exception:
        pass


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
        await message.answer(_tr(message, "admin_denied"))
        return
    await state.clear()
    await state.set_state(AdminFlow.menu)
    sent = await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")
    if sent:
        await remember_menu_owner(message, state, sent)


@router.message(Command("new"))
async def cmd_resend_forum_requests(message: Message) -> None:
    if not _ensure_admin_role(message, "super"):
        return

    if not is_moderation_forum_chat(message.chat.id):
        await message.answer("Команда /new работает только в форуме модерации.")
        return

    cfg = moderation_config()
    if getattr(message, "message_thread_id", None) and int(message.message_thread_id) != int(cfg["topic_id"]):
        await message.answer("Команду нужно отправлять в настроенном топике модерации.")
        return

    requests = (
        list(get_requests(status="pending"))
        + list(get_requests(status="error"))
        + list(get_requests(status="scheduled"))
    )
    if not requests:
        await message.answer("Активных заявок нет.")
        return

    sent = 0
    failed = 0
    for entry in requests:
        try:
            await delete_forum_request_message(message.bot, entry)
            text, file_path = _forum_request_text_and_file(entry)
            await send_request_to_forum(message.bot, entry, text, file_path)
            sent += 1
        except Exception:
            failed += 1
            logger.warning("event=admin.resend_forum_request.failed request_id=%s", entry.get("id"), exc_info=True)

    await message.answer(f"Отправлено заявок: {sent}\nОшибок: {failed}")


@router.message(Command("adminhelp"))
async def cmd_admin_help(message: Message) -> None:
    text = (
        "<b>Команды бота</b>\n\n"
        "<b>Пользовательские</b>\n"
        "/start — открыть главное меню. Поддерживает deeplink-переходы: catalog, submit, profile, notifications, joinly, admin, plugin slug.\n"
        "/lang — сменить язык интерфейса.\n\n"
        "<b>Joinly</b>\n"
        "/settings — настройки Joinly в группе. Работает только в группе и только для админов чата.\n\n"
        "<b>Админка</b>\n"
        "/admin — открыть админ-панель. Работает для админов и суперадминов.\n"
        "/adminhelp — показать этот список команд.\n\n"
        "<b>Суперадмины</b>\n"
        "/new — переотправить в форум модерации все активные заявки: pending, error, scheduled. Работает только в настроенном форуме/топике.\n"
        "/sync_catalog &lt;t.me link&gt; &lt;request_id&gt; — вручную привязать опубликованное сообщение к заявке и добавить в каталог.\n\n"
        "<b>Админы плагинов</b>\n"
        "/sync_version &lt;plugin_id_or_slug&gt; &lt;version&gt; — вручную обновить версию плагина в каталоге.\n"
        "/erase — очистить скрытые заявки.\n"
        "/erase &lt;plugin_id_or_slug&gt; — удалить заявки по ID/slug плагина."
    )
    await message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@router.message(Command("sync_catalog"))
async def cmd_sync_catalog(message: Message) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "super"):
        return

    text = (message.text or "").strip()
    parts = text.split()
    if len(parts) < 3:
        await message.answer(
            "Использование: /sync_catalog <t.me link> <request_id>",
            disable_web_page_preview=True,
        )
        return

    link = parts[1].strip()
    request_id = parts[2].strip()

    entry = get_request_by_id(request_id)
    if not entry:
        await message.answer(_tr(message, "not_found"))
        return

    try:
        # link: https://t.me/<username>/<message_id>
        raw = link.split("?")[0].rstrip("/")
        msg_id_str = raw.rsplit("/", 1)[-1]
        msg_id = int(msg_id_str)
        channel_username = raw.split("/")[-2]
    except Exception:
        await message.answer("Неверная ссылка")
        return

    cfg = get_config()
    payload = entry.get("payload", {})

    if payload.get("submission_type") == "icon" or payload.get("icon"):
        chat_id = (cfg.get("icons_channel", {}) or {}).get("id")
        if not chat_id:
            await message.answer("icons_channel.id не задан в config")
            return
        add_icon_to_catalog(
            entry,
            msg_id,
            chat_id,
            channel_username,
            payload.get("user_id"),
            payload.get("username", ""),
        )
    else:
        chat_id = (cfg.get("channel", {}) or {}).get("id")
        if not chat_id:
            await message.answer("channel.id не задан в config")
            return
        add_to_catalog(
            entry,
            msg_id,
            chat_id,
            channel_username,
            payload.get("user_id"),
            payload.get("username", ""),
        )

    update_request_status(request_id, "published")
    await message.answer("Добавлено в каталог")


@router.callback_query(F.data == "adm:cancel")
async def on_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        try:
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        except Exception:
            pass
        return

    prev = await _nav_prev(state)
    if prev:
        await _render_nav_token(cb, state, prev)
    else:
        await _render_menu(cb, state)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:section:updates")
async def on_admin_section_updates(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    await _nav_push(state, "adm:section:updates")
    await _render_updates_list(cb, state, 0)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:updates:"))
async def on_admin_updates_list(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    parts = cb.data.split(":")
    page = 0
    if len(parts) > 2 and parts[2].isdigit():
        page = int(parts[2])
    await _nav_push(state, "adm:menu")
    await _nav_push(state, f"adm:updates:{page}")
    await _render_updates_list(cb, state, page)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:section:post")
async def on_admin_section_post(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    await _nav_push(state, "adm:section:post")
    await state.set_state(AdminFlow.menu)
    await answer(cb, _post_section_text(lang), admin_post_section_kb(lang=lang), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(Command("sync_version"))
async def on_sync_version(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "plugins"):
        return

    parts = (message.text or "").strip().split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /sync_version [id] [version]")
        return
    target_id = parts[1]
    new_version = parts[2].strip()
    if not new_version:
        await message.answer("Версия не задана")
        return

    db = load_plugins()
    plugins = db.get("plugins", [])
    updated = False
    for p in plugins:
        if not isinstance(p, dict):
            continue
        if _match_plugin_id(p, target_id):
            ru = p.setdefault("ru", {})
            en = p.setdefault("en", {})
            ru["version"] = new_version
            en["version"] = new_version
            p["updated_at"] = datetime.utcnow().isoformat()
            updated = True
            break

    if not updated:
        await message.answer("Плагин не найден")
        return

    save_plugins(db)
    invalidate_catalog_cache()
    await message.answer("Версия обновлена")


@router.message(Command("erase"))
async def cmd_erase(message: Message) -> None:
    if not _ensure_admin_role(message, "plugins"):
        return

    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        target_id = parts[1].strip()
        removed = delete_requests_by_plugin_id(target_id)
        if removed:
            await message.answer(f"Удалено заявок: {removed}")
        else:
            await message.answer("Заявки с таким ID не найдены")
        return

    removed = cleanup_hidden_requests()
    await message.answer(f"Очищено скрытых заявок: {removed}")



@router.callback_query(F.data.regexp(r"^adm:scheduled:\d+$"))
async def on_admin_scheduled(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins") and not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    parts = cb.data.split(":")
    page = 0
    if len(parts) > 2 and parts[2].isdigit():
        page = int(parts[2])

    await _nav_push(state, "adm:menu")
    await _nav_push(state, f"adm:scheduled:{page}")
    await _render_scheduled_list(cb, state, page)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:scheduled:back")
async def on_admin_scheduled_back(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    page = data.get("scheduled_list_page")
    if not isinstance(page, int):
        page = 0
    await _render_scheduled_list(cb, state, page)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:scheduled:view:"))
async def on_admin_scheduled_view(cb: CallbackQuery, state: FSMContext) -> None:
    request_id = cb.data.split(":")[3]
    await _render_scheduled_view(cb, state, request_id)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:scheduled:unschedule:"))
async def on_admin_scheduled_unschedule(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    request_id = cb.data.split(":")[3]
    entry = get_request_by_id(request_id)
    if not entry:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    update_request_payload(request_id, {"scheduled_at": None})
    update_request_status(request_id, "pending")
    data = await state.get_data()
    page = data.get("scheduled_list_page")
    if not isinstance(page, int):
        page = 0
    await _render_scheduled_list(cb, state, page)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:scheduled:up:"))
async def on_admin_scheduled_up(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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

    update_request_payload(request_id, {"scheduled_at": prev_dt.isoformat()})
    update_request_payload(prev_id, {"scheduled_at": cur_dt.isoformat()})
    await cb.answer()
    await on_admin_scheduled_view(cb, state)


@router.callback_query(F.data.startswith("adm:scheduled:down:"))
async def on_admin_scheduled_down(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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

    update_request_payload(request_id, {"scheduled_at": next_dt.isoformat()})
    update_request_payload(next_id, {"scheduled_at": cur_dt.isoformat()})
    await cb.answer()
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
        await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")
        return

    schedule_dt_utc = schedule_dt_local.astimezone(timezone.utc)
    update_request_payload(request_id, {"scheduled_at": schedule_dt_utc.isoformat()})
    await state.set_state(AdminFlow.menu)
    await _render_scheduled_view(message, state, request_id)


@router.callback_query(F.data.startswith("adm:scheduled:change_preset:"))
async def on_admin_scheduled_change_preset(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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
    update_request_payload(request_id, {"scheduled_at": schedule_dt_utc.isoformat()})
    await state.set_state(AdminFlow.menu)
    await cb.answer()
    await on_admin_scheduled_view(cb, state)


@router.callback_query(F.data == "adm:auto_updates")
async def on_admin_auto_updates(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    from bot.services.catalog_auto_updates import start_manual_catalog_auto_updates

    chat_id = cb.message.chat.id if cb.message else (cb.from_user.id if cb.from_user else None)
    message_id = cb.message.message_id if cb.message else None

    async def _done() -> None:
        if chat_id and message_id:
            try:
                tmp = CallbackQuery(id="0", from_user=cb.from_user, chat_instance="0", message=cb.message, data="")  # type: ignore
                await _render_updates_list(tmp, state, 0)
            except Exception:
                try:
                    await cb.bot.edit_message_text(
                        _tr(cb, "admin_updates_check_done"),
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode=ParseMode.HTML,
                        reply_markup=None,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    pass

    started = start_manual_catalog_auto_updates(cb.bot, on_done=_done)
    if not started:
        await cb.answer(_tr(cb, "admin_updates_check_already_running"), show_alert=True)
        return

    await cb.answer(_tr(cb, "admin_updates_check_started"), show_alert=True)
    if cb.message:
        try:
            await cb.message.edit_text(
                _tr(cb, "admin_updates_check_started"),
                parse_mode=ParseMode.HTML,
                reply_markup=None,
                disable_web_page_preview=True,
            )
        except Exception:
            pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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
        _tr(cb, "admin_search_results"),
        admin_plugins_list_kb(items, page, total_pages, select_prefix="adm:link_select", list_prefix="adm:link_list", lang=lang),
        "admin",
    )
    try:
        await cb.answer()
    except Exception:
        pass


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
        _tr(cb, "admin_search_results"),
        admin_plugins_list_kb(items, page, total_pages, select_prefix="adm:icon_edit_select", list_prefix="adm:icon_edit_list", lang=lang),
        "admin",
    )
    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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
        _tr(message, "admin_search_results"),
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
        _tr(cb, "admin_search_results"),
        admin_plugins_list_kb(items, page, total_pages, select_prefix="adm:icon_link_select", list_prefix="adm:icon_link_list", lang=lang),
        "admin",
    )
    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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
    await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")


@router.callback_query(F.data == "adm:section:plugins")
async def on_admin_section_plugins(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    await _nav_push(state, "adm:section:plugins")
    await state.set_state(AdminFlow.menu)
    await answer(cb, _plugins_section_text(lang), admin_plugins_section_kb(lang=lang), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:section:icons")
async def on_admin_section_icons(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    await _nav_push(state, "adm:section:icons")
    await state.set_state(AdminFlow.menu)
    await answer(cb, _icons_section_text(lang), admin_icons_section_kb(lang=lang), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.in_({"adm:post", "adm:post:new"}))
async def on_admin_post(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    await _nav_push(state, "adm:section:post")
    await state.set_state(AdminFlow.entering_post)
    await state.update_data(post_message_id=cb.message.message_id if cb.message else None)
    msg = await answer(cb, _tr(cb, "admin_post_prompt"), admin_cancel_kb(lang), "admin")
    if msg:
        await state.update_data(post_message_id=msg.message_id)
    try:
        await cb.answer()
    except Exception:
        pass


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
    from storage import load_updated

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
            lines.append(f"• <a href=\"{link}\">{name}</a>")
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
        from storage import load_updated

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
            lines.append(f"• <a href=\"{link}\">{name}</a>")
        if len(lines) > 1:
            return f"{final_text}\n\n" + "\n".join(lines)
    except Exception:
        pass

    return final_text


def _has_updated_plugins() -> bool:
    try:
        from storage import load_updated

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
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:post:send_updates")
async def on_admin_post_send_updates(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _send_admin_post(cb, state)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:schedule")
async def on_admin_plugin_schedule(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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
        await cb.answer("Для этого типа заявки отложка не поддерживается", show_alert=True)
        return
    await state.set_state(AdminFlow.scheduling_plugin)
    await answer(cb, _tr(cb, "admin_post_schedule_prompt"), admin_cancel_kb(lang), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.scheduling_plugin)
async def on_admin_plugin_schedule_datetime(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin(message):
        return

    text = (message.text or "").strip()
    try:
        schedule_dt_local = datetime.strptime(text, "%d.%m.%Y %H:%M").replace(tzinfo=TZ_UTC_PLUS_5)
    except ValueError:
        await message.answer(_tr(message, "admin_post_schedule_bad_format"), parse_mode=ParseMode.HTML)
        return

    now_local = datetime.now(tz=TZ_UTC_PLUS_5)
    if schedule_dt_local <= now_local:
        await message.answer(_tr(message, "admin_post_schedule_past"), parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    request_id = data.get("current_request")
    if not request_id:
        await state.set_state(AdminFlow.menu)
        await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")
        return

    entry = get_request_by_id(request_id)
    if not entry:
        await state.set_state(AdminFlow.menu)
        await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")
        return

    try:
        schedule_dt_utc = schedule_dt_local.astimezone(timezone.utc)
        update_request_payload(request_id, {"scheduled_at": schedule_dt_utc.isoformat()})
        update_request_status(request_id, "scheduled")

        dt_str = schedule_dt_local.strftime("%d.%m.%Y %H:%M")
        await state.set_state(AdminFlow.menu)
        await answer(
            message,
            _tr(message, "admin_plugin_scheduled", datetime=dt_str),
            admin_menu_kb(_admin_menu_role(message), lang=lang),
            "admin",
        )
    except Exception as exc:
        logger.exception("Schedule error")
        await answer(
            message,
            f"Ошибка:\n<code>{exc}</code>",
            admin_menu_kb(_admin_menu_role(message), lang=lang),
            "admin",
        )


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
    await answer(cb, _tr(cb, "admin_post_schedule_prompt"), admin_cancel_kb(lang), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.scheduling_post)
async def on_admin_schedule_datetime(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "plugins"):
        return

    text = (message.text or "").strip()
    try:
        schedule_dt_local = datetime.strptime(text, "%d.%m.%Y %H:%M").replace(tzinfo=TZ_UTC_PLUS_5)
    except ValueError:
        await message.answer(_tr(message, "admin_post_schedule_bad_format"), parse_mode=ParseMode.HTML)
        return

    now_local = datetime.now(tz=TZ_UTC_PLUS_5)
    if schedule_dt_local <= now_local:
        await message.answer(_tr(message, "admin_post_schedule_past"), parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    post_text = (data.get("post_text") or "").strip()
    if not post_text:
        await state.set_state(AdminFlow.menu)
        await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")
        return

    includes_updated_plugins = _has_updated_plugins()
    post_text = _with_updated_plugins_block(message, post_text)

    from userbot.client import get_userbot
    userbot = await get_userbot()
    if not userbot:
        await message.answer(_tr(message, "admin_userbot_unavailable"), parse_mode=ParseMode.HTML)
        return

    schedule_dt_utc = schedule_dt_local.astimezone(timezone.utc)
    result = await userbot.schedule_post(post_text, schedule_dt_utc)

    # persist scheduled post
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
    dt_str = schedule_dt_local.strftime("%d.%m.%Y %H:%M")
    await answer(
        message,
        _tr(message, "admin_post_scheduled", datetime=dt_str, link=result.get("link", "")),
        admin_menu_kb(_admin_menu_role(message), lang=lang),
        "admin",
    )


@router.callback_query(F.data.regexp(r"^adm:scheduled_posts:\d+$"))
async def on_admin_scheduled_posts(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    parts = cb.data.split(":")
    page = 0
    if len(parts) > 2 and parts[2].isdigit():
        page = int(parts[2])

    await _nav_push(state, "adm:menu")
    await _nav_push(state, f"adm:scheduled_posts:{page}")
    await _render_scheduled_posts_list(cb, state, page)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:scheduled_posts:back")
async def on_admin_scheduled_posts_back(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    page = data.get("scheduled_posts_list_page")
    if not isinstance(page, int):
        page = 0
    await _render_scheduled_posts_list(cb, state, page)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:scheduled_posts:view:"))
async def on_admin_scheduled_posts_view(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    post_id = cb.data.split(":")[3]
    await _render_scheduled_post_view(cb, state, post_id)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:scheduled_posts:delete:"))
async def on_admin_scheduled_posts_delete(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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
    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.scheduled_post_edit_text)
async def on_admin_scheduled_posts_edit_text_value(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
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
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:scheduled_posts:change_preset:"))
async def on_admin_scheduled_posts_change_preset(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.scheduled_post_edit_time)
async def on_admin_scheduled_posts_change_time_value(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
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
    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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

    if target == "post":
        await state.set_state(AdminFlow.scheduling_post)
        kb = admin_schedule_presets_kb(_cleanup_schedule_presets(), "adm:post:schedule:preset", "adm:post:schedule:preset:add", lang=lang)
        await answer(message, _render_schedule_prompt_text(message), kb, "admin")
        return

    await state.set_state(AdminFlow.scheduling_plugin)
    kb = admin_schedule_presets_kb(_cleanup_schedule_presets(), "adm:schedule:preset", "adm:schedule:preset:add", lang=lang)
    await answer(message, _render_schedule_prompt_text(message), kb, "admin")


@router.callback_query(F.data.startswith("adm:admins:noop:"))
async def on_admins_noop(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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

    title = _admins_title(field, cb)
    msg = await answer(
        cb,
        f"<b>{title}</b>\n\n{_tr(cb, 'admin_removed', admin_id=admin_id)}",
        admin_manage_admins_kb(field, updated, lang=lang),
        "admin",
    )
    if msg:
        await state.update_data(config_message_id=msg.message_id)
    try:
        await cb.answer(_tr(cb, "admin_removed_short", admin_id=admin_id))
    except Exception:
        pass


@router.callback_query(F.data == "adm:menu")
async def on_admin_menu(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, "adm:menu")
    await _render_menu(cb, state)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:stats")
async def on_admin_stats(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    users = list_users()
    total = len(users)
    counts: Dict[str, int] = {}
    for user in users:
        user_lang = (user.get("language") or "unknown").lower()
        counts[user_lang] = counts.get(user_lang, 0) + 1

    lines = [_tr(cb, "admin_label_users", total=total)]
    if counts:
        for user_lang, count in sorted(counts.items()):
            label = user_lang.upper() if user_lang not in {"unknown", ""} else _tr(cb, "admin_label_not_set")
            lines.append(f"{label}: {count}")

    msg = await answer(cb, "\n".join(lines), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")
    if msg:
        await state.update_data(stats_message_id=msg.message_id)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:config")
async def on_admin_config(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _nav_push(state, "adm:config")
    await _render_config(cb, state)
    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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
            f"{_tr(cb, 'admin_prompt_checked_on_version')}\n\n<b>Текущая:</b> <code>{current}</code>",
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
    try:
        await cb.answer()
    except Exception:
        pass


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
        config_message_id = data.get("config_message_id")
        if config_message_id:
            try:
                await message.bot.edit_message_text(
                    f"<b>{_admins_title(field, message)}</b>\n\n{_tr(message, 'admin_added', admin_id=admin_id)}",
                    chat_id=message.chat.id,
                    message_id=config_message_id,
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_manage_admins_kb(field, sorted(set(updated)), lang=lang),
                    disable_web_page_preview=True,
                )
                return
            except Exception:
                pass
        sent_msg = await message.answer(
            _tr(message, "admin_added_short", admin_id=admin_id),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
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

        _config_set_path(config, field, value)
        save_config(config)
        invalidate("config")

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
            await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")
    else:
        await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")


@router.callback_query(F.data == "adm:broadcast")
async def on_admin_broadcast(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    await _nav_push(state, "adm:broadcast")
    await _render_broadcast_enter(cb, state)
    try:
        await cb.answer()
    except Exception:
        pass


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

    await state.update_data(broadcast_text=text)
    await state.set_state(AdminFlow.confirming_broadcast)
    broadcast_message_id = (await state.get_data()).get("broadcast_message_id")
    if broadcast_message_id:
        try:
            await message.bot.edit_message_text(
                f"<b>{_tr(message, 'admin_btn_broadcast')}</b>\n\n{text}\n\n{_tr(message, 'admin_broadcast_confirm')}",
                chat_id=message.chat.id,
                message_id=broadcast_message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_broadcast_confirm_kb(lang=lang),
                disable_web_page_preview=True,
            )
        except Exception:
            sent_msg = await message.answer(
                f"<b>{_tr(message, 'admin_btn_broadcast')}</b>\n\n{text}\n\n{_tr(message, 'admin_broadcast_confirm')}",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_broadcast_confirm_kb(lang=lang),
                disable_web_page_preview=True,
            )
            if sent_msg:
                await state.update_data(broadcast_message_id=sent_msg.message_id)
    else:
        sent_msg = await message.answer(
            f"<b>{_tr(message, 'admin_btn_broadcast')}</b>\n\n{text}\n\n{_tr(message, 'admin_broadcast_confirm')}",
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

    users = list_users()
    sent = 0
    failed = 0
    for user in users:
        user_id = user.get("user_id")
        if not user_id or user.get("banned"):
            continue
        if not is_broadcast_enabled(int(user_id)):
            continue
        try:
            await cb.bot.send_message(
                user_id,
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception:
            failed += 1

    await state.clear()
    await state.set_state(AdminFlow.menu)
    try:
        await cb.message.edit_text(
            _tr(cb, "admin_broadcast_done", sent=sent, failed=failed),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_kb(_admin_menu_role(cb), lang=lang),
            disable_web_page_preview=True,
        )
    except Exception:
        await answer(
            cb,
            _tr(cb, "admin_broadcast_done", sent=sent, failed=failed),
            admin_menu_kb(_admin_menu_role(cb), lang=lang),
            "profile",
        )
    try:
        await cb.answer()
    except Exception:
        pass


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
    elif queue_type == "icons":
        if not _ensure_admin_role(cb, "icons"):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return
    else:
        if not _ensure_admin(cb):
            await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
            return
    page = int(parts[3]) if len(parts) > 3 else 0

    await _nav_push(state, "adm:menu")
    await _nav_push(state, f"adm:queue:{queue_type}:{page}")

    visible_requests = list(get_requests(status="pending")) + list(get_requests(status="error"))

    if queue_type == "icons":
        requests = [
            r for r in visible_requests
            if r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon")
        ]
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
        await cb.answer(_tr(cb, "admin_queue_empty"), show_alert=True)
        return

    total = len(requests)
    total_pages = math.ceil(total / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = requests[start : start + PAGE_SIZE]

    items: List[tuple[str, str] | tuple[str, str, str | None]] = []
    for entry in page_items:
        payload = entry.get("payload", {})
        if queue_type == "icons" or payload.get("submission_type") == "icon" or payload.get("icon"):
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
            prefix = "Удаление "
            row_icon = "delete"
        elif request_type == "update":
            prefix = "Обновление: "
            row_icon = "updates"
        else:
            prefix = ""
        if entry.get("status") == "error" or payload.get("last_publish_error"):
            prefix = f"Ошибка: {prefix}"
            row_icon = row_icon or "warning"
        label = f"{name} v{version}" if version else f"{name}"
        label = f"{prefix}{label}"
        items.append((label, entry["id"], row_icon))

    if queue_type == "icons":
        title = _tr(cb, "admin_queue_title_icons")
    elif queue_type == "update":
        title = _tr(cb, "admin_queue_title_updates")
    elif queue_type == "new":
        title = _tr(cb, "admin_queue_title_new")
    elif queue_type == "plugins":
        title = _tr(cb, "admin_queue_title_plugins")
    else:
        title = _tr(cb, "admin_queue_title_all")
    caption = f"<b>{title}</b>\n{_tr(cb, 'admin_page', current=page + 1, total=total_pages)}"
    await answer(cb, caption, admin_queue_kb(items, page, total_pages, queue_type, lang=lang), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:banned:"))
async def on_admin_banned(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    page = int(cb.data.split(":")[2])
    banned = get_banned_users()

    if not banned:
        await cb.answer(_tr(cb, "admin_banned_empty"), show_alert=True)
        return

    total = len(banned)
    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = banned[start : start + PAGE_SIZE]

    items = [(f"{user.get('username') or user['user_id']}", user["user_id"]) for user in page_items]

    caption = f"<b>{_tr(cb, 'admin_btn_banned')}</b>\n{_tr(cb, 'admin_page', current=page + 1, total=total_pages)}"
    await answer(cb, caption, admin_banned_kb(items, page, total_pages, lang=lang), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:unban:"))
async def on_admin_unban(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    user_id = int(cb.data.split(":")[2])
    unban_user(user_id)
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
        await answer(cb, _tr(cb, "admin_title"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")


@router.callback_query(F.data == "adm:link_author")
async def on_admin_link_author(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await state.set_state(AdminFlow.searching_plugin)
    await state.update_data(search_message_id=cb.message.message_id if cb.message else None, search_purpose="link_plugin")
    msg = await answer(cb, _tr(cb, "admin_prompt_search_plugin"), admin_cancel_kb(lang), "admin")
    if msg:
        await state.update_data(search_message_id=msg.message_id)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:edit_plugins")
async def on_admin_edit_plugins(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
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
    try:
        await cb.answer()
    except Exception:
        pass


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
        _tr(cb, "admin_search_results"),
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
    try:
        await cb.answer()
    except Exception:
        pass


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
        "category_key": plugin.get("category") or "",
    }

    draft_text = build_channel_post({"payload": edit_payload})
    await state.update_data(
        edit_slug=slug,
        edit_payload=edit_payload,
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
    try:
        await cb.answer()
    except Exception:
        pass


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

    await answer(cb, _tr(cb, "admin_delete_confirm"), admin_confirm_delete_plugin_kb(slug, lang=lang), None)
    try:
        await cb.answer()
    except Exception:
        pass


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

    message_id = plugin_entry.get("channel_message", {}).get("message_id")
    if not message_id:
        await cb.answer(_tr(cb, "admin_channel_message_not_found"), show_alert=True)
        return

    try:
        await cb.answer(_tr(cb, "admin_delete_progress"))
    except Exception:
        pass

    from userbot.client import get_userbot

    userbot = await get_userbot()
    if not userbot:
        await cb.answer(_tr(cb, "admin_userbot_unavailable"), show_alert=True)
        return

    try:
        await userbot.delete_message(message_id)
    except Exception:
        pass

    removed = remove_plugin_entry(slug)
    invalidate("plugins")
    if not removed:
        await cb.answer(_tr(cb, "admin_delete_failed"), show_alert=True)
        return

    await state.set_state(AdminFlow.menu)
    await answer(cb, _tr(cb, "admin_title"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")
    try:
        await cb.answer(_tr(cb, "admin_deleted_success"), show_alert=True)
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:select_plugin:"))
async def on_admin_select_plugin(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    slug = decode_slug(cb.data.split(":")[2])
    await state.update_data(link_plugin_slug=slug)
    await state.set_state(AdminFlow.linking_author_user)
    await answer(cb, _tr(cb, "admin_enter_user_id"), admin_cancel_kb(lang), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


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
    await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")


@router.callback_query(F.data.startswith("adm:review:"))
async def on_admin_review(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)

    if not entry:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    if not _ensure_request_role(cb, entry):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, f"adm:review:{request_id}")

    payload = entry.get("payload", {})
    allow_publish = _is_super_admin(cb)
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        await state.set_state(AdminFlow.reviewing)
        await state.update_data(
            current_request=request_id,
            draft_message_id=cb.message.message_id if cb.message else None,
        )
        draft_text = f"{_render_request_draft(entry)}\n\n{_review_meta_block(entry)}"
        if allow_publish:
            kb = icon_draft_edit_kb(lang=lang)
        else:
            kb = admin_review_kb(request_id, payload.get("user_id", 0), lang=lang, allow_publish=False)
        await answer(cb, draft_text, kb, "iconpacks")
        try:
            await cb.answer()
        except Exception:
            pass
        return

    await state.set_state(AdminFlow.reviewing)
    await state.update_data(
        current_request=request_id,
        draft_message_id=cb.message.message_id if cb.message else None,
    )

    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    user_id = payload.get("user_id", 0)
    username = payload.get("username", "")
    request_type = entry.get("type", "new")
    submission_type = payload.get("submission_type") or ("icon" if payload.get("icon") else "plugin")

    user_link = f"@{plain_html(username)}" if username else f"<code>{plain_html(user_id)}</code>"
    settings = _tr(cb, "admin_yes") if plugin.get("has_ui_settings") else _tr(cb, "admin_no")

    if request_type == "update":
        changelog = strip_blockquote_tags(telegram_html(payload.get("changelog"))) or "—"
        old_plugin = payload.get("old_plugin", {})
        old_version = _localized_block(old_plugin, lang).get("version") or "?"
        text = (
            f"<b>Обновление</b>\n\n"
            f"<b>ID:</b> <code>{plain_html(request_id)}</code>\n"
            f"<b>Плагин:</b> {plain_html(plugin.get('name', '—'))}\n"
            f"<b>Версия:</b> {plain_html(old_version)} → {plain_html(plugin.get('version', '—'))}\n"
            f"<b>Мин. версия:</b> {plain_html(plugin.get('min_version', '—'))}\n\n"
            f"<b>Изменения:</b>\n<blockquote expandable>{changelog}</blockquote>\n\n"
            f"<b>От:</b> {user_link}"
        )
    elif request_type == "delete":
        delete_slug = payload.get("delete_slug") or plugin.get("id") or "—"
        text = (
            f"{emoji_html('delete', '🗑')} <b>Удаление <code>{plain_html(delete_slug)}</code></b>\n\n"
            f"<b>ID:</b> <code>{plain_html(request_id)}</code>\n"
            f"<b>Плагин:</b> {plain_html(plugin.get('name', '—'))}\n"
            f"<b>Slug:</b> <code>{plain_html(delete_slug)}</code>\n\n"
            f"<b>От:</b> {user_link}"
        )
    elif submission_type == "icon":
        icon = payload.get("icon", {})
        text = (
            f"<b>Новый пак иконок</b>\n\n"
            f"<b>ID:</b> <code>{plain_html(request_id)}</code>\n"
            f"<b>Название:</b> {plain_html(icon.get('name', '—'))}\n"
            f"<b>Автор:</b> {plain_html(icon.get('author', '—'))}\n"
            f"<b>Версия:</b> {plain_html(icon.get('version', '—'))}\n"
            f"<b>Иконок:</b> {plain_html(icon.get('count', 0))}\n\n"
            f"<b>От:</b> {user_link}"
        )
    else:
        draft_text = _render_request_draft(entry)
        text = (
            f"<b>Новый плагин</b>\n\n"
            f"<b>ID:</b> <code>{plain_html(request_id)}</code>\n\n"
            f"{draft_text}\n\n"
            f"<b>От:</b> {user_link}"
        )

    file_path = plugin.get("file_path") or payload.get("icon", {}).get("file_path")
    if request_type == "delete":
        kb = admin_review_kb(
            request_id,
            user_id,
            submit_label=_tr(cb, "admin_submit_delete"),
            submit_callback=f"adm:delete:{request_id}",
            lang=lang,
            allow_publish=allow_publish,
        )
    else:
        kb = admin_review_kb(request_id, user_id, lang=lang, allow_publish=allow_publish)

    text = f"{text}\n\n{_review_meta_block(entry)}"
    await answer(cb, text, kb, "admin")

    if file_path and Path(file_path).exists() and cb.message:
        data = await state.get_data()
        sent_files = data.get("sent_review_files")
        if not isinstance(sent_files, list):
            sent_files = []

        if request_id not in sent_files:
            try:
                await cb.bot.send_document(
                    cb.message.chat.id,
                    document=FSInputFile(file_path),
                    disable_notification=True,
                )
                sent_files.append(request_id)
                await state.update_data(sent_review_files=sent_files)
            except Exception:
                pass

    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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
        )
    else:
        kb = admin_review_kb(request_id, user_id, lang=lang, allow_publish=_is_super_admin(cb))

    await cb.message.edit_reply_markup(reply_markup=kb)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:delete:"))
async def on_admin_delete(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry and not _ensure_request_role(cb, entry):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    request_id = cb.data.split(":")[2]
    await state.update_data(current_request=request_id)
    await on_admin_publish(cb, state)


@router.callback_query(F.data.startswith("adm:prepublish:"))
async def on_admin_prepublish(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)

    if not entry:
        await cb.answer(_tr(cb, "not_found"), show_alert=True)
        return

    if not _ensure_request_role(cb, entry):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await state.set_state(AdminFlow.reviewing)
    await state.update_data(current_request=request_id)

    draft_text = f"{_render_request_draft(entry)}\n\n{_review_meta_block(entry)}"
    payload = entry.get("payload", {})
    include_schedule = _can_schedule_request(entry)
    not_before = _publish_not_before_dt_utc(entry)
    include_force_publish = bool(
        cb.from_user
        and cb.from_user.id in get_admins_super()
        and not_before
        and not_before > datetime.now(timezone.utc)
    )
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        await answer(cb, draft_text, icon_draft_edit_kb(include_schedule=False, lang=lang), "iconpacks")
    else:
        checked_on_set = bool(str(payload.get("checked_on") or "").strip())
        await answer(
            cb,
            draft_text,
            draft_edit_kb(
                "adm",
                _tr(cb, "admin_submit_publish"),
                include_back=True,
                include_schedule=include_schedule,
                include_force_publish=include_force_publish,
                checked_on_set=checked_on_set,
                lang=lang,
            ),
            "plugins",
        )
    try:
        await cb.answer()
    except Exception:
        pass


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
        try:
            await cb.answer()
        except Exception:
            pass
        return

    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm_icon:back")
async def on_admin_icon_back(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    data = await state.get_data()
    entry = get_request_by_id(data.get("current_request", ""))
    if entry:
        draft_text = _render_request_draft(entry)
        await answer(cb, draft_text, icon_draft_edit_kb(lang=lang), "iconpacks")
    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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
        draft_text = _render_request_draft(entry)
        await answer(message, draft_text, icon_draft_edit_kb(lang=lang), "iconpacks")


@router.callback_query(F.data.startswith("adm:edit:"))
async def on_admin_draft_edit(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    field = cb.data.split(":")[2]

    await _nav_push(state, f"adm:edit:{field}")

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
        draft_text = _render_request_draft(entry)
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
                include_schedule=include_schedule,
                checked_on_set=checked_on_set,
                lang=lang,
            ),
            "plugins",
        )
        try:
            await cb.answer()
        except Exception:
            pass
        return

    if field in {"description", "usage"}:
        await state.update_data(edit_field=field)
        await answer(cb, _tr(cb, "admin_choose_language"), draft_lang_kb("adm", field, lang=lang), None)
        try:
            await cb.answer()
        except Exception:
            pass
        return

    if field == "category":
        await state.update_data(edit_field=field)
        await answer(cb, _tr(cb, "admin_choose_category"), draft_category_kb("adm", get_categories(), lang=lang), None)
        try:
            await cb.answer()
        except Exception:
            pass
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
    try:
        await cb.answer()
    except Exception:
        pass


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
        draft_text = _render_request_draft(entry)
        await answer(
            cb,
            draft_text,
            draft_edit_kb("adm_edit", _tr(cb, "admin_submit_update"), include_back=True, include_file=True, lang=lang),
            "profile",
        )
        try:
            await cb.answer()
        except Exception:
            pass
        return

    if field == "file":
        await state.set_state(AdminFlow.uploading_catalog_file)
        await answer(cb, _tr(cb, "admin_send_plugin_file"), admin_cancel_kb(lang), None)
        try:
            await cb.answer()
        except Exception:
            pass
        return

    if field in {"description", "usage"}:
        await state.update_data(edit_field=field)
        await answer(cb, _tr(cb, "admin_choose_language"), draft_lang_kb("adm_edit", field, lang=lang), None)
        try:
            await cb.answer()
        except Exception:
            pass
        return

    if field == "category":
        await state.update_data(edit_field=field)
        await answer(cb, _tr(cb, "admin_choose_category"), draft_category_kb("adm_edit", get_categories(), lang=lang), None)
        try:
            await cb.answer()
        except Exception:
            pass
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
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_edit:lang:"))
async def on_admin_catalog_language(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    _, _, field, lang_choice = cb.data.split(":")
    await state.update_data(edit_field=field, edit_lang=lang_choice)
    await state.set_state(AdminFlow.editing_catalog_field)

    prompt = _tr(cb, "admin_prompt_enter_text_ru") if lang_choice == "ru" else _tr(cb, "admin_prompt_enter_text_en")
    await answer(cb, prompt, admin_cancel_kb(lang), None)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_edit:cat:"))
async def on_admin_catalog_category(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    cat_key = cb.data.split(":")[2]
    category = next((c for c in get_categories() if c.get("key") == cat_key), None)
    if category:
        data = await state.get_data()
        payload = data.get("edit_payload", {})
        payload["category_key"] = cat_key
        payload["category_label"] = f"{category.get('ru', '')} / {category.get('en', '')}"
        await state.update_data(edit_payload=payload)

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
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm_edit:back")
async def on_admin_catalog_back(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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
    try:
        await cb.answer()
    except Exception:
        pass


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
    payload["plugin"] = plugin_dict
    await state.update_data(edit_payload=payload)
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
        await cb.answer("Сообщение канала не найдено", show_alert=True)
        return

    await cb.answer("Обновление...")
    from userbot.client import get_userbot

    userbot = await get_userbot()
    if not userbot:
        await cb.answer("Userbot недоступен", show_alert=True)
        return

    entry = {"payload": payload}
    post_text = build_channel_post(entry)
    file_path = payload.get("plugin", {}).get("file_path")
    await userbot.update_message(message_id, post_text, file_path)
    update_catalog_entry(slug, entry, message_id)

    await state.set_state(AdminFlow.menu)
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
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:cat:"))
async def on_admin_draft_category(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    cat_key = cb.data.split(":")[2]
    category = next((c for c in get_categories() if c.get("key") == cat_key), None)
    if category:
        await state.update_data(
            category_key=cat_key,
            category_label=f"{category.get('ru', '')} / {category.get('en', '')}",
        )

        data = await state.get_data()
        request_id = data.get("current_request")
        if request_id:
            update_request_payload(request_id, {
                "category_key": cat_key,
                "category_label": f"{category.get('ru', '')} / {category.get('en', '')}",
            })

    entry = get_request_by_id((await state.get_data()).get("current_request"))
    if entry:
        include_schedule = _can_schedule_request(entry)
        await answer(
            cb,
            _render_request_draft(entry),
            draft_edit_kb(
                "adm",
                _tr(cb, "admin_submit_publish"),
                include_back=True,
                include_schedule=include_schedule,
                lang=lang,
            ),
        )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:back")
async def on_admin_draft_back(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_super_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    prev = await _nav_prev(state)
    if prev:
        await _render_nav_token(cb, state, prev)
    else:
        await _render_menu(cb, state)
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.editing_draft_field)
async def on_admin_draft_field_value(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
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
    if not entry:
        await state.set_state(AdminFlow.menu)
        return

    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})

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

    await state.update_data(edit_field=None, edit_lang=None)
    await state.set_state(AdminFlow.reviewing)
    entry = get_request_by_id(request_id)
    if entry:
        data = await state.get_data()
        draft_message_id = data.get("draft_message_id")
        draft_text = _render_request_draft(entry)
        include_schedule = _can_schedule_request(entry)
        if draft_message_id:
            try:
                await message.bot.edit_message_text(
                    draft_text,
                    chat_id=message.chat.id,
                    message_id=draft_message_id,
                    parse_mode=ParseMode.HTML,
                    reply_markup=draft_edit_kb(
                        "adm",
                        _tr(message, "admin_submit_publish"),
                        include_back=True,
                        include_schedule=include_schedule,
                        lang=lang,
                    ),
                    disable_web_page_preview=True,
                )
            except Exception:
                sent = await answer(
                    message,
                    draft_text,
                    draft_edit_kb(
                        "adm",
                        _tr(message, "admin_submit_publish"),
                        include_back=True,
                        include_schedule=include_schedule,
                        lang=lang,
                    ),
                    "plugins",
                )
                if sent:
                    await state.update_data(draft_message_id=sent.message_id)
        else:
            sent = await answer(
                message,
                draft_text,
                draft_edit_kb(
                    "adm",
                    _tr(message, "admin_submit_publish"),
                    include_back=True,
                    include_schedule=include_schedule,
                    lang=lang,
                ),
                "plugins",
            )
            if sent:
                await state.update_data(draft_message_id=sent.message_id)


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

    payload = entry.get("payload", {})
    request_type = entry.get("type", "new")
    submission_type = payload.get("submission_type") or ("icon" if payload.get("icon") else "plugin")
    item_name = payload.get("plugin", {}).get("name") or payload.get("icon", {}).get("name") or payload.get("delete_slug") or "unknown"
    admin_id = cb.from_user.id if cb.from_user else None
    force_publish = cb.data == "adm:submit_force"

    not_before = _publish_not_before_dt_utc(entry)
    if (
        not force_publish
        and request_type not in {"update", "delete"}
        and submission_type != "icon"
        and not_before
        and not_before > datetime.now(timezone.utc)
    ):
        update_request_payload(request_id, {"scheduled_at": not_before.isoformat()})
        update_request_status(request_id, "scheduled")
        dt_str = not_before.astimezone(TZ_UTC_PLUS_5).strftime("%d.%m.%Y %H:%M")
        user_id = entry.get("payload", {}).get("user_id")
        if user_id:
            try:
                await cb.bot.send_message(
                    user_id,
                    _tr(user_id, "admin_request_scheduled_by_limit", datetime=dt_str),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
        await answer(
            cb,
            _tr(cb, "admin_request_scheduled_by_limit", datetime=dt_str),
            admin_menu_kb(_admin_menu_role(cb), lang=lang),
            "admin",
        )
        try:
            await cb.answer()
        except Exception:
            pass
        return

    logger.info(
        "event=moderation.publish.start request_id=%s request_type=%s submission_type=%s item=%s admin_id=%s",
        request_id,
        request_type,
        submission_type,
        item_name,
        admin_id,
    )

    try:
        await cb.answer("Публикация...")
    except Exception:
        pass

    try:
        if request_type == "update":
            old_plugin = payload.get("old_plugin", {})
            result = await update_plugin(entry, old_plugin)
            update_slug = payload.get("update_slug") or payload.get("plugin", {}).get("id")
            await _notify_subscribers(
                cb.bot,
                update_slug,
                payload.get("plugin", {}),
                payload.get("changelog"),
            )
            notify_key = "notify_update_published"
        elif submission_type == "icon":
            result = await publish_icon(entry)
            notify_key = "notify_icon_published"
        elif request_type == "delete":
            delete_slug = payload.get("delete_slug") or payload.get("plugin", {}).get("id")
            plugin_entry = find_plugin_by_slug(delete_slug)
            if not plugin_entry:
                raise ValueError("Plugin not found")
            message_id = plugin_entry.get("channel_message", {}).get("message_id")
            from userbot.client import get_userbot
            userbot = await get_userbot()
            if userbot and message_id:
                await userbot.delete_message(message_id)
            removed = remove_plugin_entry(delete_slug)
            if not removed:
                raise ValueError("Failed to remove plugin")
            update_request_status(request_id, "deleted")
            result = {"link": plugin_entry.get("channel_message", {}).get("link", "")}
            notify_key = "notify_deleted"
        else:
            result = await publish_plugin(entry)
            notify_key = "notify_published"

        try:
            await _finalize_admin_notify_messages(
                cb.bot,
                entry,
                "Заявка была принята",
                _admin_actor_label(cb),
            )
        except Exception:
            pass
        await delete_forum_request_message(cb.bot, entry)

        user_id = entry.get("payload", {}).get("user_id")
        payload = entry.get("payload", {})
        name = payload.get("plugin", {}).get("name") or payload.get("icon", {}).get("name", "")
        version = payload.get("plugin", {}).get("version")

        if user_id:
            notify_lang = get_lang(user_id)
            try:
                await cb.bot.send_message(
                    user_id,
                    t(notify_key, notify_lang, name=name, version=version or "—"),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

        await answer(
            cb,
            _tr(cb, "admin_publish_done", link=result.get("link", "")),
            admin_menu_kb(_admin_menu_role(cb), lang=lang),
            "profile",
        )

        logger.info(
            "event=moderation.publish.success request_id=%s request_type=%s submission_type=%s item=%s admin_id=%s user_id=%s link=%s",
            request_id,
            request_type,
            submission_type,
            item_name,
            admin_id,
            user_id or "-",
            result.get("link", ""),
        )

    except Exception as exc:
        logger.exception(
            "event=moderation.publish.failed request_id=%s request_type=%s submission_type=%s item=%s admin_id=%s",
            request_id,
            request_type,
            submission_type,
            item_name,
            admin_id,
        )
        update_request_payload(
            request_id,
            {
                "last_publish_error": str(exc),
                "last_publish_error_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        await answer(
            cb,
            f"Ошибка:\n<code>{exc}</code>",
            admin_menu_kb(_admin_menu_role(cb), lang=lang),
            "profile",
        )


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

    await _nav_push(state, f"adm:reject:{request_id}")
    await cb.message.edit_reply_markup(reply_markup=admin_reject_kb(request_id, lang=lang))
    try:
        await cb.answer()
    except Exception:
        pass


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
    await state.update_data(reject_request_id=request_id)
    await state.set_state(AdminFlow.entering_reject_comment)
    await answer(cb, _tr(cb, "admin_enter_reject_reason"), None, "admin")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.entering_reject_comment)
async def on_admin_enter_reject_comment(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    comment = telegram_html(message.html_text or message.text or "")
    if not comment:
        await message.answer(_tr(message, "need_text"), disable_web_page_preview=True)
        return

    data = await state.get_data()
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

    entry = get_request_by_id(request_id)
    if entry:
        update_request_status(request_id, "rejected", comment=comment)
        try:
            await _finalize_admin_notify_messages(
                message.bot,
                entry,
                "Заявка была отклонена",
                _admin_actor_label(message),
            )
        except Exception:
            pass
        await delete_forum_request_message(message.bot, entry)
        user_id = entry.get("payload", {}).get("user_id")
        if user_id:
            lang = get_lang(user_id)
            try:
                await message.bot.send_message(
                    user_id,
                    t("notify_rejected", lang, comment=comment),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await answer(message, _tr(message, "admin_rejected_done"), admin_menu_kb(_admin_menu_role(message), lang=lang), "admin")


@router.callback_query(F.data.startswith("adm:reject_silent:"))
async def on_admin_reject_silent(cb: CallbackQuery, state: FSMContext) -> None:
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

    await _nav_push(state, f"adm:reject_silent:{request_id}")
    update_request_status(request_id, "rejected")
    if entry:
        try:
            await _finalize_admin_notify_messages(
                cb.bot,
                entry,
                "Заявка была отклонена",
                _admin_actor_label(cb),
            )
        except Exception:
            pass
        await delete_forum_request_message(cb.bot, entry)
    await answer(cb, _tr(cb, "admin_rejected_done"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "admin")
    try:
        await cb.answer()
    except Exception:
        pass


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
    try:
        await cb.answer()
    except Exception:
        pass


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

    await _nav_push(state, f"adm:ban_confirm:{request_id}")

    ban_user(user_id, reason="Заблокирован администратором")
    update_request_status(request_id, "rejected", comment="Пользователь заблокирован")

    try:
        await _finalize_admin_notify_messages(
            cb.bot,
            entry,
            "Заявка была отклонена",
            _admin_actor_label(cb),
        )
    except Exception:
        pass
    await delete_forum_request_message(cb.bot, entry)

    try:
        await cb.bot.send_message(
            user_id,
            t("user_banned_by_admin", get_lang(user_id)),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        pass

    from user_store import get_user
    user_data = get_user(user_id)
    username = user_data.get("username", "")
    removal = await remove_user_content(user_id, username)
    removed_count = len(removal["removed_plugins"]) + len(removal["removed_icons"])

    await answer(
        cb,
        _tr(cb, "admin_user_banned", user_id=user_id)
        + (f"\n\nУдалено плагинов/иконок: {removed_count}" if removed_count else ""),
        admin_menu_kb(_admin_menu_role(cb), lang=lang),
        "profile",
    )
    try:
        await cb.answer()
    except Exception:
        pass
