import logging
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

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
from bot.helpers import answer
from bot.keyboards import (
    admin_actions_kb,
    admin_banned_kb,
    admin_broadcast_confirm_kb,
    admin_post_confirm_kb,
    admin_config_kb,
    admin_confirm_ban_kb,
    admin_icons_section_kb,
    admin_manage_admins_kb,
    admin_menu_kb,
    admin_plugins_section_kb,
    admin_plugins_list_kb,
    admin_queue_kb,
    admin_reject_kb,
    admin_review_kb,
    admin_confirm_delete_plugin_kb,
    admin_cancel_kb,
    cancel_kb,
    draft_category_kb,
    draft_edit_kb,
    draft_lang_kb,
    icon_draft_edit_kb,
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
from bot.routers.catalog_flow import build_inline_preview, build_plugin_preview
from bot.services.submission import PluginData, process_icon_file, process_plugin_file
from bot.states import AdminFlow
from bot.texts import TEXTS, t
from catalog import find_icon_by_slug, find_plugin_by_slug, list_published_icons, list_published_plugins
from request_store import get_request_by_id, get_requests, update_request_payload, update_request_status
from storage import save_config
from user_store import ban_user, get_banned_users, get_user_language, is_broadcast_enabled, list_users, unban_user
from subscription_store import list_subscribers

logger = logging.getLogger(__name__)

TZ_UTC_PLUS_5 = timezone(timedelta(hours=5))
router = Router(name="admin-flow")

_NAV_STACK_KEY = "nav_stack"


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
    await answer(cb, _tr(cb, "admin_title"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "profile")


async def _render_config(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    await state.set_state(AdminFlow.menu)
    await answer(cb, _tr(cb, "admin_settings_title"), admin_config_kb(lang=lang), "profile")


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
        "profile",
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
    msg = await answer(cb, _tr(cb, "admin_prompt_broadcast"), admin_cancel_kb(lang), "profile")
    if msg:
        await state.update_data(broadcast_message_id=msg.message_id)


async def _render_queue(cb: CallbackQuery, state: FSMContext, token: str) -> None:
    lang = _lang_for(cb)
    parts = token.split(":")
    queue_type = parts[2] if len(parts) > 2 else "plugins"
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0

    if queue_type == "icons":
        requests = [
            r for r in get_requests(status="pending")
            if r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon")
        ]
    else:
        req_type = "new" if queue_type == "plugins" else (None if queue_type == "all" else ("update" if queue_type == "update" else "new"))
        requests = [
            r for r in get_requests(status="pending", request_type=req_type)
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
        label = f"{name} v{version}" if version else f"{name}"
        items.append((label, entry["id"]))

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
    msg = await answer(cb, caption, admin_queue_kb(items, page, total_pages, queue_type, lang=lang), "profile")
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
        draft_text = _render_request_draft(entry)
        msg = await answer(cb, draft_text, icon_draft_edit_kb(lang=lang), "iconpacks")
        if msg:
            await state.update_data(draft_message_id=msg.message_id)
        return

    draft_text = _render_request_draft(entry)
    msg = await answer(cb, draft_text, admin_review_kb(request_id, payload.get("user_id", 0), lang=lang), "profile")
    if msg:
        await state.update_data(draft_message_id=msg.message_id)


async def _render_nav_token(cb: CallbackQuery, state: FSMContext, token: str) -> None:
    lang = _lang_for(cb)
    if token == "adm:menu":
        await _render_menu(cb, state)
    elif token == "adm:section:plugins":
        await state.set_state(AdminFlow.menu)
        await answer(cb, _tr(cb, "admin_section_plugins"), admin_plugins_section_kb(lang=lang), "profile")
    elif token == "adm:section:icons":
        await state.set_state(AdminFlow.menu)
        await answer(cb, _tr(cb, "admin_section_icons"), admin_icons_section_kb(lang=lang), "profile")
    elif token == "adm:config":
        await _render_config(cb, state)
    elif token == "adm:broadcast":
        await _render_broadcast_enter(cb, state)
    elif token == "adm:post":
        await state.set_state(AdminFlow.entering_post)
        await state.update_data(post_message_id=cb.message.message_id if cb.message else None)
        await answer(cb, _tr(cb, "admin_post_prompt"), admin_cancel_kb(lang), "profile")
    elif token.startswith("adm:queue:"):
        await _render_queue(cb, state, token)
    elif token.startswith("adm:review:"):
        await _render_review(cb, state, token)
    else:
        await _render_menu(cb, state)


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
    changes = changelog or "—"

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


def _ensure_request_role(cb: CallbackQuery | Message, entry: dict) -> bool:
    payload = entry.get("payload", {})
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        return _ensure_admin_role(cb, "icons")
    return _ensure_admin_role(cb, "plugins")


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin(message):
        await message.answer(_tr(message, "admin_denied"))
        return
    await state.set_state(AdminFlow.menu)
    await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "profile")


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
    await answer(cb, _tr(cb, "admin_enter_user_id"), admin_cancel_kb(lang), "profile")
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
        "profile",
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
        "profile",
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
    msg = await answer(cb, _tr(cb, "admin_prompt_search_plugin"), admin_cancel_kb(lang), "profile")
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
    msg = await answer(cb, _tr(cb, "admin_prompt_search_plugin"), admin_cancel_kb(lang), "profile")
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
        "profile",
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
        "profile",
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
    await answer(cb, _tr(cb, "admin_enter_user_id"), admin_cancel_kb(lang), "profile")
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
    await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "profile")


@router.callback_query(F.data == "adm:section:plugins")
async def on_admin_section_plugins(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    await _nav_push(state, "adm:section:plugins")
    await state.set_state(AdminFlow.menu)
    await answer(cb, _tr(cb, "admin_section_plugins"), admin_plugins_section_kb(lang=lang), "profile")
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
    await answer(cb, _tr(cb, "admin_section_icons"), admin_icons_section_kb(lang=lang), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:post")
async def on_admin_post(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    await _nav_push(state, "adm:post")
    await state.set_state(AdminFlow.entering_post)
    await state.update_data(post_message_id=cb.message.message_id if cb.message else None)
    msg = await answer(cb, _tr(cb, "admin_post_prompt"), admin_cancel_kb(lang), "profile")
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

    text = (message.html_text or message.text or "").strip()
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


async def _send_admin_post(cb: CallbackQuery, state: FSMContext, include_updates: bool) -> None:
    lang = _lang_for(cb)
    data = await state.get_data()
    text = (data.get("post_text") or "").strip()
    if not text:
        await cb.answer(_tr(cb, "admin_post_no_text"), show_alert=True)
        return

    final_text = text
    if include_updates:
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

    if include_updates:
        clear_updated_plugins()

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await answer(
        cb,
        _tr(cb, "admin_post_sent", link=result.get("link", "")),
        admin_menu_kb(_admin_menu_role(cb), lang=lang),
        "profile",
    )


@router.callback_query(F.data == "adm:post:send")
async def on_admin_post_send(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _send_admin_post(cb, state, include_updates=False)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:post:send_updates")
async def on_admin_post_send_updates(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    await _send_admin_post(cb, state, include_updates=True)
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
    await answer(cb, _tr(cb, "admin_post_schedule_prompt"), admin_cancel_kb(lang), "profile")
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
        await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "profile")
        return

    entry = get_request_by_id(request_id)
    if not entry:
        await state.set_state(AdminFlow.menu)
        await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "profile")
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
            "profile",
        )
    except Exception as exc:
        logger.exception("Schedule error")
        await answer(
            message,
            f"Ошибка:\n<code>{exc}</code>",
            admin_menu_kb(_admin_menu_role(message), lang=lang),
            "profile",
        )


@router.callback_query(F.data == "adm:post:schedule")
async def on_admin_post_schedule(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return
    data = await state.get_data()
    if not data.get("post_text"):
        await cb.answer(_tr(cb, "admin_post_no_text"), show_alert=True)
        return
    await state.set_state(AdminFlow.scheduling_post)
    await answer(cb, _tr(cb, "admin_post_schedule_prompt"), admin_cancel_kb(lang), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.scheduling_post)
async def on_admin_schedule_datetime(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    if not _ensure_admin_role(message, "super"):
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
        await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "profile")
        return

    from userbot.client import get_userbot
    userbot = await get_userbot()
    if not userbot:
        await message.answer(_tr(message, "admin_userbot_unavailable"), parse_mode=ParseMode.HTML)
        return

    schedule_dt_utc = schedule_dt_local.astimezone(timezone.utc)
    result = await userbot.schedule_post(post_text, schedule_dt_utc)

    await state.clear()
    await state.set_state(AdminFlow.menu)
    dt_str = schedule_dt_local.strftime("%d.%m.%Y %H:%M")
    await answer(
        message,
        _tr(message, "admin_post_scheduled", datetime=dt_str, link=result.get("link", "")),
        admin_menu_kb(_admin_menu_role(message), lang=lang),
        "profile",
    )


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
    await answer(cb, _tr(cb, "admin_prompt_enter_admin_id"), admin_cancel_kb(lang), "profile")
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
        "profile",
    )
    if msg:
        await state.update_data(config_message_id=msg.message_id)
    try:
        await cb.answer(_tr(cb, "admin_removed_short", admin_id=admin_id))
    except Exception:
        pass


@router.callback_query(F.data == "adm:menu")
async def on_admin_menu(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
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
        lang = (user.get("language") or "unknown").lower()
        counts[lang] = counts.get(lang, 0) + 1

    lines = [_tr(cb, "admin_label_users", total=total)]
    if counts:
        for lang, count in sorted(counts.items()):
            label = lang.upper() if lang not in {"unknown", ""} else _tr(cb, "admin_label_not_set")
            lines.append(f"{label}: {count}")

    msg = await answer(cb, "\n".join(lines), admin_menu_kb(_admin_menu_role(cb), lang=lang), "profile")
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


@router.callback_query(F.data.startswith("adm:config:"))
async def on_admin_config_edit(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin(cb):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    field = cb.data.split(":")[2]
    if field in {"admins_super", "admins_plugins", "admins_icons"}:
        await _nav_push(state, f"adm:config:{field}")
        await _render_admins_manage(cb, state, field)
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
        await answer(cb, _tr(cb, "admin_unknown_setting"), admin_config_kb(lang=lang), "profile")
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
            await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "profile")
    else:
        await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "profile")


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

    text = (message.html_text or message.text or "").strip()
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
    await answer(cb, _tr(cb, "admin_title"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "profile")
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

    if queue_type == "icons":
        requests = [
            r for r in get_requests(status="pending")
            if r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon")
        ]
    else:
        req_type = "new" if queue_type == "plugins" else (None if queue_type == "all" else ("update" if queue_type == "update" else "new"))
        requests = [
            r for r in get_requests(status="pending", request_type=req_type)
            if not (r.get("payload", {}).get("submission_type") == "icon" or r.get("payload", {}).get("icon"))
        ]

    if not requests:
        await cb.answer(_tr(cb, "admin_queue_empty"), show_alert=True)
        return

    total = len(requests)
    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = requests[start : start + PAGE_SIZE]

    items = []
    for entry in page_items:
        payload = entry.get("payload", {})
        if queue_type == "icons" or payload.get("submission_type") == "icon" or payload.get("icon"):
            icon = payload.get("icon", {})
            name = icon.get("name", "?")
            version = icon.get("version", "")
            type_icon = ""
        else:
            plugin = payload.get("plugin", {})
            name = plugin.get("name", "?")
            version = plugin.get("version", "")
            type_icon = ""
        label_base = f"{name} v{version}" if version else f"{name}"
        label = f"{type_icon} {label_base}" if type_icon else label_base
        items.append((label, entry["id"]))

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
    await answer(cb, caption, admin_queue_kb(items, page, total_pages, queue_type, lang=lang), "profile")
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
    await answer(cb, caption, admin_banned_kb(items, page, total_pages, lang=lang), "profile")
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
        await answer(cb, _tr(cb, "admin_title"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "profile")


@router.callback_query(F.data == "adm:link_author")
async def on_admin_link_author(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(_tr(cb, "admin_denied"), show_alert=True)
        return

    await state.set_state(AdminFlow.searching_plugin)
    await state.update_data(search_message_id=cb.message.message_id if cb.message else None, search_purpose="link_plugin")
    msg = await answer(cb, _tr(cb, "admin_prompt_search_plugin"), admin_cancel_kb(lang), "profile")
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
    await answer(cb, _tr(cb, "admin_title"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "profile")
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
    await answer(cb, _tr(cb, "admin_select_plugin"), admin_plugins_list_kb(items, page, total_pages, lang=lang), "profile")
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
    await answer(cb, _tr(cb, "admin_enter_user_id"), admin_cancel_kb(lang), "profile")
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
    await answer(message, _tr(message, "admin_title"), admin_menu_kb(_admin_menu_role(message), lang=lang), "profile")


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
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        await state.set_state(AdminFlow.reviewing)
        await state.update_data(
            current_request=request_id,
            draft_message_id=cb.message.message_id if cb.message else None,
        )
        draft_text = _render_request_draft(entry)
        await answer(cb, draft_text, icon_draft_edit_kb(lang=lang), "iconpacks")
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

    user_link = f"@{username}" if username else f"<code>{user_id}</code>"
    settings = _tr(cb, "admin_yes") if plugin.get("has_ui_settings") else _tr(cb, "admin_no")

    if request_type == "update":
        changelog = payload.get("changelog", "—")
        old_plugin = payload.get("old_plugin", {})
        old_version = _localized_block(old_plugin, lang).get("version") or "?"
        text = (
            f"<b>Обновление</b>\n\n"
            f"<b>ID:</b> <code>{request_id}</code>\n"
            f"<b>Плагин:</b> {plugin.get('name', '—')}\n"
            f"<b>Версия:</b> {old_version} → {plugin.get('version', '—')}\n"
            f"<b>Мин. версия:</b> {plugin.get('min_version', '—')}\n\n"
            f"<b>Изменения:</b>\n<blockquote expandable>{changelog}</blockquote>\n\n"
            f"<b>От:</b> {user_link}"
        )
    elif request_type == "delete":
        delete_slug = payload.get("delete_slug") or plugin.get("id") or "—"
        text = (
            f"<b>Удаление</b>\n\n"
            f"<b>ID:</b> <code>{request_id}</code>\n"
            f"<b>Плагин:</b> {plugin.get('name', '—')}\n"
            f"<b>Slug:</b> <code>{delete_slug}</code>\n\n"
            f"<b>От:</b> {user_link}"
        )
    elif submission_type == "icon":
        icon = payload.get("icon", {})
        text = (
            f"<b>Новый пак иконок</b>\n\n"
            f"<b>ID:</b> <code>{request_id}</code>\n"
            f"<b>Название:</b> {icon.get('name', '—')}\n"
            f"<b>Автор:</b> {icon.get('author', '—')}\n"
            f"<b>Версия:</b> {icon.get('version', '—')}\n"
            f"<b>Иконок:</b> {icon.get('count', 0)}\n\n"
            f"<b>От:</b> {user_link}"
        )
    else:
        draft_text = _render_request_draft(entry)
        text = (
            f"<b>Новый плагин</b>\n\n"
            f"<b>ID:</b> <code>{request_id}</code>\n\n"
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
        )
    else:
        kb = admin_review_kb(request_id, user_id, lang=lang)

    await answer(cb, text, kb, "profile")

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

    await _nav_push(state, f"adm:actions:{request_id}")

    allow_ban = bool(cb.from_user and cb.from_user.id in get_admins_super())
    await cb.message.edit_reply_markup(reply_markup=admin_actions_kb(request_id, allow_ban=allow_ban, lang=lang))
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
        )
    else:
        kb = admin_review_kb(request_id, user_id, lang=lang)

    await cb.message.edit_reply_markup(reply_markup=kb)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:delete:"))
async def on_admin_delete(cb: CallbackQuery, state: FSMContext) -> None:
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

    draft_text = _render_request_draft(entry)
    payload = entry.get("payload", {})
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        await answer(cb, draft_text, icon_draft_edit_kb(include_schedule=True, lang=lang), "iconpacks")
    else:
        await answer(
            cb,
            draft_text,
            draft_edit_kb("adm", _tr(cb, "admin_submit_publish"), include_back=True, include_schedule=True, lang=lang),
            "plugins",
        )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_icon:edit:"))
async def on_admin_icon_edit_field(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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
    text = (message.html_text or message.text or "").strip()
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
    field = cb.data.split(":")[2]

    await _nav_push(state, f"adm:edit:{field}")

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
    text = (message.html_text or message.text or "").strip()
    if not text:
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
        payload[f"{field}_{edit_lang}"] = text
    elif field == "name":
        plugin["name"] = text
    elif field == "author":
        plugin["author"] = text
    elif field == "min_version":
        plugin["min_version"] = text
    elif field == "checked_on":
        payload["checked_on"] = text
    elif field == "settings":
        plugin["has_ui_settings"] = text.lower() in {"да", "yes", "1", "true"}

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
    await answer(cb, _tr(cb, "admin_title"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:lang:"))
async def on_admin_draft_language(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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
        await answer(
            cb,
            _render_request_draft(entry),
            draft_edit_kb("adm", _tr(cb, "admin_submit_publish"), include_back=True, lang=lang),
        )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:back")
async def on_admin_draft_back(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
    data = await state.get_data()
    entry = get_request_by_id(data.get("current_request", ""))
    if entry:
        await answer(
            cb,
            _render_request_draft(entry),
            draft_edit_kb("adm", _tr(cb, "admin_submit_publish"), include_back=True, lang=lang),
            "plugins",
        )
    try:
            await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.editing_draft_field)
async def on_admin_draft_field_value(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    text = (message.html_text or message.text or "").strip()
    if not text:
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
        updates[f"{field}_{edit_lang}"] = text
    elif field == "name":
        plugin["name"] = text
        updates["plugin"] = plugin
    elif field == "author":
        plugin["author"] = text
        updates["plugin"] = plugin
    elif field == "min_version":
        plugin["min_version"] = text
        updates["plugin"] = plugin
    elif field == "checked_on":
        updates["checked_on"] = text
    elif field == "settings":
        has_settings = text.lower() in {"да", "yes", "1", "true"}
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
        if draft_message_id:
            try:
                await message.bot.edit_message_text(
                    draft_text,
                    chat_id=message.chat.id,
                    message_id=draft_message_id,
                    parse_mode=ParseMode.HTML,
                    reply_markup=draft_edit_kb("adm", _tr(message, "admin_submit_publish"), include_back=True, lang=lang),
                    disable_web_page_preview=True,
                )
            except Exception:
                sent = await answer(
                    message,
                    draft_text,
                    draft_edit_kb("adm", _tr(message, "admin_submit_publish"), include_back=True, lang=lang),
                    "plugins",
                )
                if sent:
                    await state.update_data(draft_message_id=sent.message_id)
        else:
            sent = await answer(
                message,
                draft_text,
                draft_edit_kb("adm", _tr(message, "admin_submit_publish"), include_back=True, lang=lang),
                "plugins",
            )
            if sent:
                await state.update_data(draft_message_id=sent.message_id)


@router.callback_query(F.data == "adm:submit")
async def on_admin_publish(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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
        update_request_status(request_id, "error", comment=str(exc))
        await answer(
            cb,
            f"Ошибка:\n<code>{exc}</code>",
            admin_menu_kb(_admin_menu_role(cb), lang=lang),
            "profile",
        )


@router.callback_query(F.data.startswith("adm:reject:"))
async def on_admin_reject(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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
    await answer(cb, _tr(cb, "admin_enter_reject_reason"), None, "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.entering_reject_comment)
async def on_admin_enter_reject_comment(message: Message, state: FSMContext) -> None:
    lang = _lang_for(message)
    comment = (message.text or "").strip()
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

    entry = get_request_by_id(request_id)
    if entry:
        update_request_status(request_id, "rejected", comment=comment)
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
    await answer(message, _tr(message, "admin_rejected_done"), admin_menu_kb(_admin_menu_role(message), lang=lang), "profile")


@router.callback_query(F.data.startswith("adm:reject_silent:"))
async def on_admin_reject_silent(cb: CallbackQuery, state: FSMContext) -> None:
    lang = _lang_for(cb)
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
    await answer(cb, _tr(cb, "admin_rejected_done"), admin_menu_kb(_admin_menu_role(cb), lang=lang), "profile")
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
