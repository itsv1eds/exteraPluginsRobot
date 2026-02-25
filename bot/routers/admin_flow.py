import logging
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

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
from bot.callback_tokens import decode_slug
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
from bot.services.submission import PluginData, process_plugin_file
from bot.states import AdminFlow
from bot.texts import TEXTS, t
from catalog import find_icon_by_slug, find_plugin_by_slug, list_published_icons, list_published_plugins
from request_store import get_request_by_id, get_requests, update_request_payload, update_request_status
from storage import save_config
from user_store import ban_user, get_banned_users, list_users, unban_user
from subscription_store import list_subscribers

logger = logging.getLogger(__name__)

TZ_UTC_PLUS_5 = timezone(timedelta(hours=5))
router = Router(name="admin-flow")

_NAV_STACK_KEY = "nav_stack"


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
    await state.set_state(AdminFlow.menu)
    await answer(cb, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(cb)), "profile")


async def _render_config(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.menu)
    await answer(cb, t("admin_settings_title", "ru"), admin_config_kb(), "profile")


async def _render_admins_manage(cb: CallbackQuery, state: FSMContext, field: str) -> None:
    config = get_config()
    admin_ids = sorted({int(x) for x in (config.get(field, []) or []) if str(x).isdigit()})
    await state.update_data(config_field=field, config_message_id=cb.message.message_id if cb.message else None)
    await state.set_state(AdminFlow.editing_config)
    title = {
        "admins_super": "Суперадмины",
        "admins_plugins": "Админы плагинов",
        "admins_icons": "Админы иконок",
    }.get(field, "Админы")

    links = "\n".join([f"<a href=\"tg://user?id={aid}\">{aid}</a>" for aid in admin_ids])
    links_block = f"\n\n{links}" if links else ""
    msg = await answer(
        cb,
        f"<b>{title}</b>\n\nВыберите действие:{links_block}",
        admin_manage_admins_kb(field, admin_ids),
        "profile",
    )
    if msg:
        await state.update_data(config_message_id=msg.message_id)


def _admins_title(field: str) -> str:
    return {
        "admins_super": "Суперадмины",
        "admins_plugins": "Админы плагинов",
        "admins_icons": "Админы иконок",
    }.get(field, "Админы")


async def _render_broadcast_enter(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.entering_broadcast)
    await state.update_data(broadcast_message_id=cb.message.message_id if cb.message else None)
    msg = await answer(cb, t("admin_prompt_broadcast", "ru"), admin_cancel_kb(), "profile")
    if msg:
        await state.update_data(broadcast_message_id=msg.message_id)


async def _render_queue(cb: CallbackQuery, state: FSMContext, token: str) -> None:
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
        title = t("admin_queue_title_icons", "ru")
    elif queue_type == "update":
        title = t("admin_queue_title_updates", "ru")
    elif queue_type == "new":
        title = t("admin_queue_title_new", "ru")
    elif queue_type == "plugins":
        title = t("admin_queue_title_plugins", "ru")
    else:
        title = t("admin_queue_title_all", "ru")
    caption = f"<b>{title}</b>\n{t('admin_page', 'ru', current=page + 1, total=total_pages)}"
    await state.set_state(AdminFlow.menu)
    msg = await answer(cb, caption, admin_queue_kb(items, page, total_pages, queue_type), "profile")
    if msg:
        pass


async def _render_review(cb: CallbackQuery, state: FSMContext, token: str) -> None:
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
        msg = await answer(cb, draft_text, icon_draft_edit_kb(), "iconpacks")
        if msg:
            await state.update_data(draft_message_id=msg.message_id)
        return

    draft_text = _render_request_draft(entry)
    msg = await answer(cb, draft_text, admin_review_kb(request_id, payload.get("user_id", 0)), "profile")
    if msg:
        await state.update_data(draft_message_id=msg.message_id)


async def _render_nav_token(cb: CallbackQuery, state: FSMContext, token: str) -> None:
    if token == "adm:menu":
        await _render_menu(cb, state)
    elif token == "adm:section:plugins":
        await state.set_state(AdminFlow.menu)
        await answer(cb, t("admin_section_plugins", "ru"), admin_plugins_section_kb(), "profile")
    elif token == "adm:section:icons":
        await state.set_state(AdminFlow.menu)
        await answer(cb, t("admin_section_icons", "ru"), admin_icons_section_kb(), "profile")
    elif token == "adm:config":
        await _render_config(cb, state)
    elif token == "adm:broadcast":
        await _render_broadcast_enter(cb, state)
    elif token == "adm:post":
        await state.set_state(AdminFlow.entering_post)
        await state.update_data(post_message_id=cb.message.message_id if cb.message else None)
        await answer(cb, t("admin_post_prompt", "ru"), admin_cancel_kb(), "profile")
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


async def _notify_subscribers(bot, slug: str | None, plugin: dict) -> None:
    if not slug:
        return

    entry = find_plugin_by_slug(slug)
    locale = (entry or {}).get("ru") or {}
    name = plugin.get("name") or locale.get("name") or slug
    version = plugin.get("version") or locale.get("version") or ""

    for user_id in list_subscribers(slug):
        lang = get_lang(user_id)
        try:
            await bot.send_message(
                user_id,
                t("notify_subscription_update", lang, name=name, version=version or "—"),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
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
    if not _ensure_admin(message):
        await message.answer(t("admin_denied", "ru"))
        return
    await state.set_state(AdminFlow.menu)
    await answer(message, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(message)), "profile")


@router.message(Command("sync_catalog"))
async def cmd_sync_catalog(message: Message) -> None:
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
        await message.answer(t("not_found", "ru"))
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
            await cb.answer(t("admin_denied", "ru"), show_alert=True)
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
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    slug = decode_slug(cb.data.split(":")[2])
    icon = find_icon_by_slug(slug)
    if not icon:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    authors = icon.get("authors", {}) or {}
    edit_payload = {
        "icon": {
            "name": (icon.get("ru") or {}).get("name") or "",
            "author": authors.get("ru") or authors.get("en") or "",
            "version": (icon.get("ru") or {}).get("version") or (icon.get("en") or {}).get("version") or "",
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
        icon_draft_edit_kb(prefix="adm_icon_edit", submit_label=t("admin_submit_update", "ru")),
        "iconpacks",
    )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:link_select:"))
async def on_admin_link_select_plugin(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    slug = decode_slug(cb.data.split(":")[2])
    await state.update_data(link_plugin_slug=slug)
    await state.set_state(AdminFlow.linking_author_user)
    await answer(cb, t("admin_enter_user_id", "ru"), admin_cancel_kb(), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:link_list:"))
async def on_admin_link_list_plugins(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    page = int(cb.data.split(":")[2])
    data = await state.get_data()
    slugs = data.get("edit_plugins_list") or []
    plugins = [find_plugin_by_slug(s) for s in slugs]
    plugins = [p for p in plugins if p]
    if not plugins:
        await cb.answer(t("admin_search_nothing_found", "ru"), show_alert=True)
        return
    total_pages = math.ceil(len(plugins) / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = plugins[start : start + PAGE_SIZE]
    items = [(p.get("ru", {}).get("name") or p.get("slug"), p.get("slug")) for p in page_items]
    await answer(
        cb,
        t("admin_search_results", "ru"),
        admin_plugins_list_kb(items, page, total_pages, select_prefix="adm:link_select", list_prefix="adm:link_list"),
        "profile",
    )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:icon_edit_list:"))
async def on_admin_icon_edit_list(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    page = int(cb.data.split(":")[2])
    data = await state.get_data()
    slugs = data.get("edit_icons_list") or []
    icons = [find_icon_by_slug(s) for s in slugs]
    icons = [i for i in icons if i]
    if not icons:
        await cb.answer(t("admin_search_nothing_found", "ru"), show_alert=True)
        return
    total_pages = math.ceil(len(icons) / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = icons[start : start + PAGE_SIZE]
    items = [((i.get("ru") or {}).get("name") or i.get("slug"), i.get("slug")) for i in page_items]
    await answer(
        cb,
        t("admin_search_results", "ru"),
        admin_plugins_list_kb(items, page, total_pages, select_prefix="adm:icon_edit_select", list_prefix="adm:icon_edit_list"),
        "profile",
    )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:link_author_icons")
async def on_admin_link_author_icons(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    await state.set_state(AdminFlow.searching_icon)
    await state.update_data(search_message_id=cb.message.message_id if cb.message else None, search_purpose="link_icon")
    msg = await answer(cb, t("admin_prompt_search_plugin", "ru"), admin_cancel_kb(), "profile")
    if msg:
        await state.update_data(search_message_id=msg.message_id)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:edit_icons")
async def on_admin_edit_icons(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    await state.set_state(AdminFlow.searching_icon)
    await state.update_data(search_message_id=cb.message.message_id if cb.message else None, search_purpose="edit_icon")
    msg = await answer(cb, t("admin_prompt_search_plugin", "ru"), admin_cancel_kb(), "profile")
    if msg:
        await state.update_data(search_message_id=msg.message_id)
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.searching_icon)
async def on_admin_search_icons(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "icons"):
        return
    if (message.text or "").strip().startswith("/"):
        return
    query = (message.text or "").strip().lower()
    if not query:
        await message.answer(t("need_text", "ru"), disable_web_page_preview=True)
        return

    icons = list_published_icons()
    filtered = [
        i
        for i in icons
        if query in (i.get("slug", "").lower())
        or query in ((i.get("ru") or {}).get("name") or "").lower()
        or query in ((i.get("en") or {}).get("name") or "").lower()
    ]
    await state.update_data(edit_icons_list=[i.get("slug") for i in filtered])
    data = await state.get_data()
    purpose = data.get("search_purpose")
    select_prefix = "adm:icon_edit_select" if purpose != "link_icon" else "adm:icon_link_select"
    list_prefix = "adm:icon_edit_list" if purpose != "link_icon" else "adm:icon_link_list"
    if not filtered:
        await message.answer(t("admin_search_nothing_found", "ru"), disable_web_page_preview=True)
        return

    total_pages = math.ceil(len(filtered) / PAGE_SIZE)
    items = [((i.get("ru") or {}).get("name") or i.get("slug"), i.get("slug")) for i in filtered[:PAGE_SIZE]]
    await answer(
        message,
        t("admin_search_results", "ru"),
        admin_plugins_list_kb(items, 0, total_pages, select_prefix=select_prefix, list_prefix=list_prefix),
        "profile",
    )


@router.callback_query(F.data.startswith("adm:icon_link_list:"))
async def on_admin_icon_link_list(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    page = int(cb.data.split(":")[2])
    data = await state.get_data()
    slugs = data.get("edit_icons_list") or []
    icons = [find_icon_by_slug(s) for s in slugs]
    icons = [i for i in icons if i]
    if not icons:
        await cb.answer(t("admin_search_nothing_found", "ru"), show_alert=True)
        return
    total_pages = math.ceil(len(icons) / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = icons[start : start + PAGE_SIZE]
    items = [((i.get("ru") or {}).get("name") or i.get("slug"), i.get("slug")) for i in page_items]
    await answer(
        cb,
        t("admin_search_results", "ru"),
        admin_plugins_list_kb(items, page, total_pages, select_prefix="adm:icon_link_select", list_prefix="adm:icon_link_list"),
        "profile",
    )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:icon_link_select:"))
async def on_admin_icon_link_select(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    slug = decode_slug(cb.data.split(":")[2])
    await state.update_data(link_icon_slug=slug)
    await state.set_state(AdminFlow.linking_author_icon_user)
    await answer(cb, t("admin_enter_user_id", "ru"), admin_cancel_kb(), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.linking_author_icon_user)
async def on_admin_icon_link_user(message: Message, state: FSMContext) -> None:
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
        await message.answer(t("admin_enter_valid_user_id", "ru"), parse_mode=ParseMode.HTML)
        return
    try:
        user_id = int(parts[0])
    except ValueError:
        await message.answer(t("admin_enter_valid_user_id", "ru"), parse_mode=ParseMode.HTML)
        return
    username = parts[1].lstrip("@") if len(parts) > 1 else ""

    success = add_submitter_to_iconpack(slug, user_id, username)
    if success:
        await message.answer(t("admin_author_linked", "ru"))
    else:
        await message.answer(t("admin_link_failed", "ru"), parse_mode=ParseMode.HTML)

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await answer(message, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(message)), "profile")


@router.callback_query(F.data == "adm:section:plugins")
async def on_admin_section_plugins(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    await _nav_push(state, "adm:section:plugins")
    await state.set_state(AdminFlow.menu)
    await answer(cb, t("admin_section_plugins", "ru"), admin_plugins_section_kb(), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:section:icons")
async def on_admin_section_icons(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "icons"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    await _nav_push(state, "adm:section:icons")
    await state.set_state(AdminFlow.menu)
    await answer(cb, t("admin_section_icons", "ru"), admin_icons_section_kb(), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:post")
async def on_admin_post(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    await _nav_push(state, "adm:post")
    await state.set_state(AdminFlow.entering_post)
    await state.update_data(post_message_id=cb.message.message_id if cb.message else None)
    msg = await answer(cb, t("admin_post_prompt", "ru"), admin_cancel_kb(), "profile")
    if msg:
        await state.update_data(post_message_id=msg.message_id)
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.entering_post)
async def on_admin_post_text(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "super"):
        return

    text = (message.html_text or message.text or "").strip()
    if not text:
        await message.answer(t("admin_post_no_text", "ru"))
        return

    await state.update_data(post_text=text)
    await state.set_state(AdminFlow.confirming_post)

    preview = f"{text}\n\n{t('admin_post_confirm', 'ru')}"
    post_message_id = (await state.get_data()).get("post_message_id")
    if post_message_id:
        try:
            await message.bot.edit_message_text(
                preview,
                chat_id=message.chat.id,
                message_id=post_message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_post_confirm_kb(),
                disable_web_page_preview=True,
            )
            await state.update_data(post_message_id=post_message_id)
            return
        except Exception:
            pass

    sent = await message.answer(
        preview,
        parse_mode=ParseMode.HTML,
        reply_markup=admin_post_confirm_kb(),
        disable_web_page_preview=True,
    )
    if sent:
        await state.update_data(post_message_id=sent.message_id)


async def _send_admin_post(cb: CallbackQuery, state: FSMContext, include_updates: bool) -> None:
    data = await state.get_data()
    text = (data.get("post_text") or "").strip()
    if not text:
        await cb.answer(t("admin_post_no_text", "ru"), show_alert=True)
        return

    final_text = text
    if include_updates:
        from storage import load_updated

        updated = load_updated()
        items = updated.get("items") or []
        if items:
            lines = [t("admin_updated_block_title", "ru")]
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
        await cb.answer(t("admin_userbot_unavailable", "ru"), show_alert=True)
        return

    result = await userbot.publish_post(final_text)

    if include_updates:
        clear_updated_plugins()

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await answer(
        cb,
        t("admin_post_sent", "ru", link=result.get("link", "")),
        admin_menu_kb(_admin_menu_role(cb)),
        "profile",
    )


@router.callback_query(F.data == "adm:post:send")
async def on_admin_post_send(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    await _send_admin_post(cb, state, include_updates=False)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:post:send_updates")
async def on_admin_post_send_updates(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    await _send_admin_post(cb, state, include_updates=True)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:schedule")
async def on_admin_plugin_schedule(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    request_id = data.get("current_request")
    if not request_id:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return
    entry = get_request_by_id(request_id)
    if not entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return
    if not _ensure_request_role(cb, entry):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    req_type = entry.get("type", "new")
    if req_type in {"update", "delete"}:
        await cb.answer("Для этого типа заявки отложка не поддерживается", show_alert=True)
        return
    await state.set_state(AdminFlow.scheduling_plugin)
    await answer(cb, t("admin_post_schedule_prompt", "ru"), admin_cancel_kb(), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.scheduling_plugin)
async def on_admin_plugin_schedule_datetime(message: Message, state: FSMContext) -> None:
    if not _ensure_admin(message):
        return

    text = (message.text or "").strip()
    try:
        schedule_dt_local = datetime.strptime(text, "%d.%m.%Y %H:%M").replace(tzinfo=TZ_UTC_PLUS_5)
    except ValueError:
        await message.answer(t("admin_post_schedule_bad_format", "ru"), parse_mode=ParseMode.HTML)
        return

    now_local = datetime.now(tz=TZ_UTC_PLUS_5)
    if schedule_dt_local <= now_local:
        await message.answer(t("admin_post_schedule_past", "ru"), parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    request_id = data.get("current_request")
    if not request_id:
        await state.set_state(AdminFlow.menu)
        await answer(message, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(message)), "profile")
        return

    entry = get_request_by_id(request_id)
    if not entry:
        await state.set_state(AdminFlow.menu)
        await answer(message, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(message)), "profile")
        return

    try:
        schedule_dt_utc = schedule_dt_local.astimezone(timezone.utc)
        update_request_payload(request_id, {"scheduled_at": schedule_dt_utc.isoformat()})
        update_request_status(request_id, "scheduled")

        dt_str = schedule_dt_local.strftime("%d.%m.%Y %H:%M")
        await state.set_state(AdminFlow.menu)
        await answer(
            message,
            t("admin_plugin_scheduled", "ru", datetime=dt_str),
            admin_menu_kb(_admin_menu_role(message)),
            "profile",
        )
    except Exception as exc:
        logger.exception("Schedule error")
        await answer(
            message,
            f"Ошибка:\n<code>{exc}</code>",
            admin_menu_kb(_admin_menu_role(message)),
            "profile",
        )


@router.callback_query(F.data == "adm:post:schedule")
async def on_admin_post_schedule(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    data = await state.get_data()
    if not data.get("post_text"):
        await cb.answer(t("admin_post_no_text", "ru"), show_alert=True)
        return
    await state.set_state(AdminFlow.scheduling_post)
    await answer(cb, t("admin_post_schedule_prompt", "ru"), admin_cancel_kb(), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.scheduling_post)
async def on_admin_schedule_datetime(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "super"):
        return

    text = (message.text or "").strip()
    try:
        schedule_dt_local = datetime.strptime(text, "%d.%m.%Y %H:%M").replace(tzinfo=TZ_UTC_PLUS_5)
    except ValueError:
        await message.answer(t("admin_post_schedule_bad_format", "ru"), parse_mode=ParseMode.HTML)
        return

    now_local = datetime.now(tz=TZ_UTC_PLUS_5)
    if schedule_dt_local <= now_local:
        await message.answer(t("admin_post_schedule_past", "ru"), parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    post_text = (data.get("post_text") or "").strip()
    if not post_text:
        await state.set_state(AdminFlow.menu)
        await answer(message, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(message)), "profile")
        return

    from userbot.client import get_userbot
    userbot = await get_userbot()
    if not userbot:
        await message.answer(t("admin_userbot_unavailable", "ru"), parse_mode=ParseMode.HTML)
        return

    schedule_dt_utc = schedule_dt_local.astimezone(timezone.utc)
    result = await userbot.schedule_post(post_text, schedule_dt_utc)

    await state.clear()
    await state.set_state(AdminFlow.menu)
    dt_str = schedule_dt_local.strftime("%d.%m.%Y %H:%M")
    await answer(
        message,
        t("admin_post_scheduled", "ru", datetime=dt_str, link=result.get("link", "")),
        admin_menu_kb(_admin_menu_role(message)),
        "profile",
    )


@router.callback_query(F.data.startswith("adm:admins:noop:"))
async def on_admins_noop(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:admins:add:"))
async def on_admins_add_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    field = cb.data.split(":")[3]
    await state.update_data(config_field=field, config_message_id=cb.message.message_id if cb.message else None)
    await state.set_state(AdminFlow.editing_config)
    await answer(cb, t("admin_prompt_enter_admin_id", "ru"), admin_cancel_kb(), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:admins:rm:"))
async def on_admins_remove(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    parts = cb.data.split(":")
    field = parts[3] if len(parts) > 3 else ""
    raw_id = parts[4] if len(parts) > 4 else ""
    try:
        admin_id = int(raw_id)
    except ValueError:
        await cb.answer(t("parse_error", "ru", error="bad id"), show_alert=True)
        return
    if field not in {"admins_super", "admins_plugins", "admins_icons"}:
        await cb.answer(t("parse_error", "ru", error="bad field"), show_alert=True)
        return

    config = get_config()
    current = [int(x) for x in (config.get(field, []) or []) if str(x).isdigit()]
    updated = sorted({x for x in current if x != admin_id})
    config[field] = updated
    save_config(config)
    invalidate("config")

    title = _admins_title(field)
    msg = await answer(
        cb,
        f"<b>{title}</b>\n\nУдалён: <code>{admin_id}</code>",
        admin_manage_admins_kb(field, updated),
        "profile",
    )
    if msg:
        await state.update_data(config_message_id=msg.message_id)
    try:
        await cb.answer(f"Удалён: {admin_id}")
    except Exception:
        pass


@router.callback_query(F.data == "adm:menu")
async def on_admin_menu(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    await _nav_push(state, "adm:menu")
    await _render_menu(cb, state)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:stats")
async def on_admin_stats(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    users = list_users()
    total = len(users)
    counts: Dict[str, int] = {}
    for user in users:
        lang = (user.get("language") or "unknown").lower()
        counts[lang] = counts.get(lang, 0) + 1

    lines = [t("admin_label_users", "ru", total=total)]
    if counts:
        for lang, count in sorted(counts.items()):
            label = lang.upper() if lang not in {"unknown", ""} else t("admin_label_not_set", "ru")
            lines.append(f"{label}: {count}")

    msg = await answer(cb, "\n".join(lines), admin_menu_kb(_admin_menu_role(cb)), "profile")
    if msg:
        await state.update_data(stats_message_id=msg.message_id)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:config")
async def on_admin_config(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await _nav_push(state, "adm:menu")
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    await _nav_push(state, "adm:config")
    await _render_config(cb, state)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:config:"))
async def on_admin_config_edit(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
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
            f"{t('admin_prompt_channel', 'ru')}\n{t('admin_prompt_channel_example', 'ru')}",
            admin_config_kb(),
            "profile",
        )
    else:
        await state.update_data(config_field=field, config_message_id=cb.message.message_id if cb.message else None)
        await state.set_state(AdminFlow.editing_config)
        await answer(cb, t("admin_unknown_setting", "ru"), admin_config_kb(), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.editing_config)
async def on_admin_config_value(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "super"):
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer(
            t("need_text", "ru"),
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
                t("admin_bad_id", "ru"),
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
                    f"<b>{_admins_title(field)}</b>\n\nДобавлен: <code>{admin_id}</code>",
                    chat_id=message.chat.id,
                    message_id=config_message_id,
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_manage_admins_kb(field, sorted(set(updated))),
                    disable_web_page_preview=True,
                )
                return
            except Exception:
                pass
        sent_msg = await message.answer(
            f"Добавлен: {admin_id}",
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
                t("admin_channel_min_parts", "ru"),
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
                t("admin_bad_channel_id", "ru"),
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
            t("admin_channel_updated", "ru"),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(
            t("admin_unknown_setting", "ru"),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )

    config_message_id = data.get("config_message_id")
    await state.update_data(config_field=None, config_message_id=None)
    await state.set_state(AdminFlow.menu)
    if config_message_id:
        try:
            await message.bot.edit_message_text(
                t("admin_title", "ru"),
                chat_id=message.chat.id,
                message_id=config_message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_menu_kb(_admin_menu_role(message)),
                disable_web_page_preview=True,
            )
        except Exception:
            await answer(message, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(message)), "profile")
    else:
        await answer(message, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(message)), "profile")


@router.callback_query(F.data == "adm:broadcast")
async def on_admin_broadcast(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
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
    if not _ensure_admin_role(message, "super"):
        return

    text = (message.html_text or message.text or "").strip()
    if not text:
        await message.answer(
            t("need_text", "ru"),
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
                f"<b>{t('admin_btn_broadcast', 'ru')}</b>\n\n{text}\n\n{t('admin_broadcast_confirm', 'ru')}",
                chat_id=message.chat.id,
                message_id=broadcast_message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_broadcast_confirm_kb(),
                disable_web_page_preview=True,
            )
        except Exception:
            sent_msg = await message.answer(
                f"<b>{t('admin_btn_broadcast', 'ru')}</b>\n\n{text}\n\n{t('admin_broadcast_confirm', 'ru')}",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_broadcast_confirm_kb(),
                disable_web_page_preview=True,
            )
            if sent_msg:
                await state.update_data(broadcast_message_id=sent_msg.message_id)
    else:
        sent_msg = await message.answer(
            f"<b>{t('admin_btn_broadcast', 'ru')}</b>\n\n{text}\n\n{t('admin_broadcast_confirm', 'ru')}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_broadcast_confirm_kb(),
            disable_web_page_preview=True,
        )
        if sent_msg:
            await state.update_data(broadcast_message_id=sent_msg.message_id)


@router.callback_query(F.data == "adm:broadcast:cancel")
async def on_admin_broadcast_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await answer(cb, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(cb)), "profile")
    await cb.answer(t("admin_broadcast_cancelled", "ru"))


@router.callback_query(F.data == "adm:broadcast:confirm")
async def on_admin_broadcast_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    data = await state.get_data()
    text = data.get("broadcast_text", "")
    if not text:
        await cb.answer(t("admin_broadcast_no_text", "ru"), show_alert=True)
        return

    users = list_users()
    sent = 0
    failed = 0
    for user in users:
        user_id = user.get("user_id")
        if not user_id or user.get("banned"):
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
            t("admin_broadcast_done", "ru", sent=sent, failed=failed),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_kb(_admin_menu_role(cb)),
            disable_web_page_preview=True,
        )
    except Exception:
        await answer(
            cb,
            t("admin_broadcast_done", "ru", sent=sent, failed=failed),
            admin_menu_kb(_admin_menu_role(cb)),
            "profile",
        )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:queue:"))
async def on_admin_queue(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    parts = cb.data.split(":")
    queue_type = parts[2]
    if queue_type in {"plugins", "new", "update"}:
        if not _ensure_admin_role(cb, "plugins"):
            await cb.answer(t("admin_denied", "ru"), show_alert=True)
            return
    elif queue_type == "icons":
        if not _ensure_admin_role(cb, "icons"):
            await cb.answer(t("admin_denied", "ru"), show_alert=True)
            return
    else:
        if not _ensure_admin(cb):
            await cb.answer(t("admin_denied", "ru"), show_alert=True)
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
        await cb.answer(t("admin_queue_empty", "ru"), show_alert=True)
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
        title = t("admin_queue_title_icons", "ru")
    elif queue_type == "update":
        title = t("admin_queue_title_updates", "ru")
    elif queue_type == "new":
        title = t("admin_queue_title_new", "ru")
    elif queue_type == "plugins":
        title = t("admin_queue_title_plugins", "ru")
    else:
        title = t("admin_queue_title_all", "ru")
    caption = f"<b>{title}</b>\n{t('admin_page', 'ru', current=page + 1, total=total_pages)}"
    await answer(cb, caption, admin_queue_kb(items, page, total_pages, queue_type), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:banned:"))
async def on_admin_banned(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    page = int(cb.data.split(":")[2])
    banned = get_banned_users()

    if not banned:
        await cb.answer(t("admin_banned_empty", "ru"), show_alert=True)
        return

    total = len(banned)
    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = banned[start : start + PAGE_SIZE]

    items = [(f"{user.get('username') or user['user_id']}", user["user_id"]) for user in page_items]

    caption = f"<b>{t('admin_btn_banned', 'ru')}</b>\n{t('admin_page', 'ru', current=page + 1, total=total_pages)}"
    await answer(cb, caption, admin_banned_kb(items, page, total_pages), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:unban:"))
async def on_admin_unban(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    user_id = int(cb.data.split(":")[2])
    unban_user(user_id)
    await cb.answer(t("admin_user_unbanned", "ru"), show_alert=True)

    banned = get_banned_users()
    if banned:
        items = [(f"{user.get('username') or user['user_id']}", user["user_id"]) for user in banned[:PAGE_SIZE]]
        total_pages = math.ceil(len(banned) / PAGE_SIZE)
        await answer(
            cb,
            f"<b>{t('admin_btn_banned', 'ru')}</b>",
            admin_banned_kb(items, 0, total_pages),
            "profile",
        )
    else:
        await answer(cb, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(cb)), "profile")


@router.callback_query(F.data == "adm:link_author")
async def on_admin_link_author(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await state.set_state(AdminFlow.searching_plugin)
    await state.update_data(search_message_id=cb.message.message_id if cb.message else None, search_purpose="link_plugin")
    msg = await answer(cb, t("admin_prompt_search_plugin", "ru"), admin_cancel_kb(), "profile")
    if msg:
        await state.update_data(search_message_id=msg.message_id)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:edit_plugins")
async def on_admin_edit_plugins(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await state.set_state(AdminFlow.searching_plugin)
    await state.update_data(search_message_id=cb.message.message_id if cb.message else None)
    msg = await answer(
        cb,
        t("admin_prompt_search_plugin", "ru"),
        admin_cancel_kb(),
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
    if not _ensure_admin_role(message, "plugins"):
        return

    if (message.text or "").strip().startswith("/"):
        return

    query = (message.text or "").strip().lower()
    if not query:
        await message.answer(
            t("need_text", "ru"),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
        return

    plugins = list_published_plugins()
    filtered = [
        p
        for p in plugins
        if query in (p.get("slug", "").lower())
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
            t("admin_search_nothing_found", "ru"),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
        return

    items = [(p.get("ru", {}).get("name") or p.get("slug"), p.get("slug")) for p in filtered[:PAGE_SIZE]]
    total_pages = math.ceil(len(filtered) / PAGE_SIZE)
    data = await state.get_data()
    message_id = data.get("search_message_id")
    if message_id:
        try:
            await message.bot.edit_message_text(
                t("admin_search_results_title", "ru"),
                chat_id=message.chat.id,
                message_id=message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_plugins_list_kb(
                    items,
                    0,
                    total_pages,
                    select_prefix=select_prefix,
                    list_prefix=list_prefix,
                ),
                disable_web_page_preview=True,
            )
            await state.update_data(search_message_id=message_id)
            return
        except Exception:
            pass

    sent_msg = await message.answer(
        t("admin_search_results_title", "ru"),
        reply_markup=admin_plugins_list_kb(
            items,
            0,
            total_pages,
            select_prefix=select_prefix,
            list_prefix=list_prefix,
        ),
    )
    if sent_msg:
        await state.update_data(search_message_id=sent_msg.message_id)


@router.callback_query(F.data.startswith("adm:edit_list:"))
async def on_admin_edit_list(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    page = int(cb.data.split(":")[2])
    data = await state.get_data()
    slugs = data.get("edit_plugins_list", [])
    plugins = [find_plugin_by_slug(slug) for slug in slugs]
    plugins = [p for p in plugins if p]

    if not plugins:
        await cb.answer(t("catalog_empty", "ru"), show_alert=True)
        return

    total_pages = math.ceil(len(plugins) / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = plugins[start : start + PAGE_SIZE]

    items = [(p.get("ru", {}).get("name") or p.get("slug"), p.get("slug")) for p in page_items]
    await answer(
        cb,
        t("admin_search_results", "ru"),
        admin_plugins_list_kb(
            items,
            page,
            total_pages,
            select_prefix="adm:edit_select",
            list_prefix="adm:edit_list",
        ),
        "profile",
    )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:edit_select:"))
async def on_admin_edit_select(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    slug = decode_slug(cb.data.split(":")[2])
    plugin = find_plugin_by_slug(slug)
    if not plugin:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    authors = plugin.get("authors", {})
    raw_blocks = plugin.get("raw_blocks", {}) or {}
    raw_ru = raw_blocks.get("ru") if isinstance(raw_blocks.get("ru"), dict) else {}
    raw_en = raw_blocks.get("en") if isinstance(raw_blocks.get("en"), dict) else {}
    edit_payload = {
        "plugin": {
            "name": plugin.get("ru", {}).get("name") or "",
            "author": raw_ru.get("author") or raw_en.get("author") or authors.get("ru") or authors.get("en") or "",
            "description": plugin.get("ru", {}).get("description") or "",
            "version": plugin.get("ru", {}).get("version") or "",
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
            t("admin_submit_update", "ru"),
            include_back=True,
            include_file=True,
            include_delete=True,
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
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    data = await state.get_data()
    slug = data.get("edit_slug")
    if not slug:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    await answer(cb, t("admin_delete_confirm", "ru"), admin_confirm_delete_plugin_kb(slug), None)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_edit:delete_confirm:"))
async def on_admin_catalog_delete_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    slug = decode_slug(cb.data.split(":")[2])
    plugin_entry = find_plugin_by_slug(slug)
    if not plugin_entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    message_id = plugin_entry.get("channel_message", {}).get("message_id")
    if not message_id:
        await cb.answer(t("admin_channel_message_not_found", "ru"), show_alert=True)
        return

    try:
        await cb.answer(t("admin_delete_progress", "ru"))
    except Exception:
        pass

    from userbot.client import get_userbot

    userbot = await get_userbot()
    if not userbot:
        await cb.answer(t("admin_userbot_unavailable", "ru"), show_alert=True)
        return

    try:
        await userbot.delete_message(message_id)
    except Exception:
        pass

    removed = remove_plugin_entry(slug)
    invalidate("plugins")
    if not removed:
        await cb.answer(t("admin_delete_failed", "ru"), show_alert=True)
        return

    await state.set_state(AdminFlow.menu)
    await answer(cb, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(cb)), "profile")
    try:
        await cb.answer(t("admin_deleted_success", "ru"), show_alert=True)
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:plugins_list:"))
async def on_admin_plugins_list(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    page = int(cb.data.split(":")[2])
    plugins = list_published_plugins()

    total = len(plugins)
    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = plugins[start : start + PAGE_SIZE]

    items = [(plugin.get("ru", {}).get("name") or plugin.get("slug"), plugin.get("slug")) for plugin in page_items]
    await answer(cb, t("admin_select_plugin", "ru"), admin_plugins_list_kb(items, page, total_pages), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:select_plugin:"))
async def on_admin_select_plugin(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    slug = decode_slug(cb.data.split(":")[2])
    await state.update_data(link_plugin_slug=slug)
    await state.set_state(AdminFlow.linking_author_user)
    await answer(cb, t("admin_enter_user_id", "ru"), admin_cancel_kb(), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.linking_author_user)
async def on_admin_link_author_user(message: Message, state: FSMContext) -> None:
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
            t("admin_enter_valid_user_id", "ru"),
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        user_id = int(parts[0])
    except ValueError:
        await message.answer(
            t("admin_enter_valid_user_id", "ru"),
            parse_mode=ParseMode.HTML,
        )
        return

    username = parts[1] if len(parts) > 1 else ""
    username = username.lstrip("@")

    success = add_submitter_to_plugin(slug, user_id, username)

    if success:
        await message.answer(t("admin_author_linked", "ru"))
    else:
        await message.answer(
            t("admin_link_failed", "ru"),
            parse_mode=ParseMode.HTML,
        )

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await answer(message, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(message)), "profile")


@router.callback_query(F.data.startswith("adm:review:"))
async def on_admin_review(cb: CallbackQuery, state: FSMContext) -> None:
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)

    if not entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    if not _ensure_request_role(cb, entry):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
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
        await answer(cb, draft_text, icon_draft_edit_kb(), "iconpacks")
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
    settings = t("admin_yes", "ru") if plugin.get("has_ui_settings") else t("admin_no", "ru")

    if request_type == "update":
        changelog = payload.get("changelog", "—")
        old_plugin = payload.get("old_plugin", {})
        old_version = old_plugin.get("ru", {}).get("version") or "?"
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
            submit_label=t("admin_submit_delete", "ru"),
            submit_callback=f"adm:delete:{request_id}",
        )
    else:
        kb = admin_review_kb(request_id, user_id)

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
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry and not _ensure_request_role(cb, entry):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    parts = cb.data.split(":")
    request_id = parts[2]
    user_id = int(parts[3])

    await _nav_push(state, f"adm:actions:{request_id}:{user_id}")

    allow_ban = bool(cb.from_user and cb.from_user.id in get_admins_super())
    await cb.message.edit_reply_markup(reply_markup=admin_actions_kb(request_id, user_id, allow_ban=allow_ban))
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:back_review:"))
async def on_admin_back_review(cb: CallbackQuery, state: FSMContext) -> None:
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry and not _ensure_request_role(cb, entry):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    parts = cb.data.split(":")
    request_id = parts[2]
    user_id = int(parts[3])

    entry = get_request_by_id(request_id)
    if entry and entry.get("type") == "delete":
        kb = admin_review_kb(
            request_id,
            user_id,
            submit_label=t("admin_submit_delete", "ru"),
            submit_callback=f"adm:delete:{request_id}",
        )
    else:
        kb = admin_review_kb(request_id, user_id)

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
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    request_id = cb.data.split(":")[2]
    await state.update_data(current_request=request_id)
    await on_admin_publish(cb, state)


@router.callback_query(F.data.startswith("adm:prepublish:"))
async def on_admin_prepublish(cb: CallbackQuery, state: FSMContext) -> None:
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)

    if not entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    if not _ensure_request_role(cb, entry):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await state.set_state(AdminFlow.reviewing)
    await state.update_data(current_request=request_id)

    draft_text = _render_request_draft(entry)
    payload = entry.get("payload", {})
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        await answer(cb, draft_text, icon_draft_edit_kb(include_schedule=True), "iconpacks")
    else:
        await answer(
            cb,
            draft_text,
            draft_edit_kb("adm", t("admin_submit_publish", "ru"), include_back=True, include_schedule=True),
            "plugins",
        )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_icon:edit:"))
async def on_admin_icon_edit_field(cb: CallbackQuery, state: FSMContext) -> None:
    field = cb.data.split(":")[2]

    await _nav_push(state, f"adm_icon:edit:{field}")

    if field in {"name", "author", "version", "count"}:
        await state.update_data(edit_field=field)
        await state.set_state(AdminFlow.editing_icon_field)
        prompt = {
            "name": t("admin_prompt_name", "ru"),
            "author": t("admin_prompt_author", "ru"),
            "version": t("admin_prompt_version", "ru"),
            "count": t("admin_prompt_icons_count", "ru"),
        }.get(field, t("admin_prompt_value", "ru"))
        await answer(cb, prompt, admin_cancel_kb(), None)
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
    data = await state.get_data()
    entry = get_request_by_id(data.get("current_request", ""))
    if entry:
        draft_text = _render_request_draft(entry)
        await answer(cb, draft_text, icon_draft_edit_kb(), "iconpacks")
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
    text = (message.html_text or message.text or "").strip()
    if not text:
        await message.answer(t("need_text", "ru"), disable_web_page_preview=True)
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
            await message.answer(t("admin_need_number", "ru"), disable_web_page_preview=True)
            return
    else:
        icon[field] = text

    update_request_payload(request_id, {"icon": icon})

    await state.update_data(edit_field=None)
    await state.set_state(AdminFlow.reviewing)
    entry = get_request_by_id(request_id)
    if entry:
        draft_text = _render_request_draft(entry)
        await answer(message, draft_text, icon_draft_edit_kb(), "iconpacks")


@router.callback_query(F.data.startswith("adm:edit:"))
async def on_admin_draft_edit(cb: CallbackQuery, state: FSMContext) -> None:
    field = cb.data.split(":")[2]

    await _nav_push(state, f"adm:edit:{field}")

    if field in {"description", "usage"}:
        await state.update_data(edit_field=field)
        await answer(cb, t("admin_choose_language", "ru"), draft_lang_kb("adm", field), None)
        try:
            await cb.answer()
        except Exception:
            pass
        return

    if field == "category":
        await state.update_data(edit_field=field)
        await answer(cb, t("admin_choose_category", "ru"), draft_category_kb("adm", get_categories()), None)
        try:
            await cb.answer()
        except Exception:
            pass
        return

    prompt = {
        "name": t("admin_prompt_name", "ru"),
        "author": t("admin_prompt_author", "ru"),
        "settings": t("admin_prompt_has_settings", "ru"),
        "min_version": t("admin_prompt_min_version", "ru"),
        "checked_on": t("admin_prompt_checked_on", "ru"),
    }.get(field, t("admin_prompt_value", "ru"))

    await state.update_data(edit_field=field)
    await state.set_state(AdminFlow.editing_draft_field)
    await answer(cb, prompt, admin_cancel_kb(), None)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_edit:edit:"))
async def on_admin_catalog_edit(cb: CallbackQuery, state: FSMContext) -> None:
    field = cb.data.split(":")[2]

    await _nav_push(state, f"adm_edit:edit:{field}")

    if field == "file":
        await state.set_state(AdminFlow.uploading_catalog_file)
        await answer(cb, t("admin_send_plugin_file", "ru"), admin_cancel_kb(), None)
        try:
            await cb.answer()
        except Exception:
            pass
        return

    if field in {"description", "usage"}:
        await state.update_data(edit_field=field)
        await answer(cb, t("admin_choose_language", "ru"), draft_lang_kb("adm_edit", field), None)
        try:
            await cb.answer()
        except Exception:
            pass
        return

    if field == "category":
        await state.update_data(edit_field=field)
        await answer(cb, t("admin_choose_category", "ru"), draft_category_kb("adm_edit", get_categories()), None)
        try:
            await cb.answer()
        except Exception:
            pass
        return

    prompt = {
        "name": t("admin_prompt_new_name", "ru"),
        "author": t("admin_prompt_author", "ru"),
        "settings": t("admin_prompt_has_settings", "ru"),
        "min_version": t("admin_prompt_min_version", "ru"),
        "checked_on": t("admin_prompt_checked_on", "ru"),
    }.get(field, t("admin_prompt_value", "ru"))

    await state.update_data(edit_field=field)
    await state.set_state(AdminFlow.editing_catalog_field)
    await answer(cb, prompt, admin_cancel_kb(), None)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_edit:lang:"))
async def on_admin_catalog_language(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, field, lang_choice = cb.data.split(":")
    await state.update_data(edit_field=field, edit_lang=lang_choice)
    await state.set_state(AdminFlow.editing_catalog_field)

    prompt = t("admin_prompt_enter_text_ru", "ru") if lang_choice == "ru" else t("admin_prompt_enter_text_en", "ru")
    await answer(cb, prompt, admin_cancel_kb(), None)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_edit:cat:"))
async def on_admin_catalog_category(cb: CallbackQuery, state: FSMContext) -> None:
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
        draft_edit_kb("adm_edit", t("admin_submit_update", "ru"), include_back=True, include_file=True),
        "profile",
    )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm_edit:back")
async def on_admin_catalog_back(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    payload = data.get("edit_payload", {})
    draft_text = build_channel_post({"payload": payload})
    await state.set_state(AdminFlow.editing_catalog_plugin)
    await answer(
        cb,
        draft_text,
        draft_edit_kb("adm_edit", t("admin_submit_update", "ru"), include_back=True, include_file=True),
        "profile",
    )
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.editing_catalog_field)
async def on_admin_catalog_field_value(message: Message, state: FSMContext) -> None:
    text = (message.html_text or message.text or "").strip()
    if not text:
        await message.answer(
            t("need_text", "ru"),
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
                reply_markup=draft_edit_kb("adm_edit", t("admin_submit_update", "ru"), include_back=True, include_file=True),
                disable_web_page_preview=True,
            )
            return
        except Exception:
            pass

    sent = await answer(
        message,
        draft_text,
        draft_edit_kb("adm_edit", t("admin_submit_update", "ru"), include_back=True, include_file=True),
        "profile",
    )
    if sent:
        await state.update_data(edit_message_id=sent.message_id)


@router.message(AdminFlow.uploading_catalog_file, F.document)
async def on_admin_catalog_file(message: Message, state: FSMContext) -> None:
    if not _ensure_admin_role(message, "plugins"):
        return

    try:
        plugin = await process_plugin_file(message.bot, message.document)
    except ValueError as exc:
        key, _, details = str(exc).partition(":")
        if key == "parse_error" and details:
            await message.answer(t("parse_error", "ru", error=details))
        else:
            await message.answer(t(key, "ru") if key in TEXTS else str(exc))
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
        draft_edit_kb("adm_edit", t("admin_submit_update", "ru"), include_back=True, include_file=True),
    )


@router.message(AdminFlow.uploading_catalog_file)
async def on_admin_catalog_file_invalid(message: Message) -> None:
    if not _ensure_admin_role(message, "plugins"):
        return
    await message.answer(t("admin_send_plugin_file_short", "ru"))


@router.callback_query(F.data == "adm_edit:submit")
async def on_admin_catalog_submit(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "plugins"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    data = await state.get_data()
    slug = data.get("edit_slug")
    payload = data.get("edit_payload", {})
    if not slug:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    plugin_entry = find_plugin_by_slug(slug)
    if not plugin_entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
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
    await answer(cb, t("admin_title", "ru"), admin_menu_kb(_admin_menu_role(cb)), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:lang:"))
async def on_admin_draft_language(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, field, lang_choice = cb.data.split(":")
    await state.update_data(edit_field=field, edit_lang=lang_choice)
    await state.set_state(AdminFlow.editing_draft_field)

    prompt = t("admin_prompt_enter_text_ru", "ru") if lang_choice == "ru" else t("admin_prompt_enter_text_en", "ru")
    await answer(cb, prompt, admin_cancel_kb(), None)
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:cat:"))
async def on_admin_draft_category(cb: CallbackQuery, state: FSMContext) -> None:
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
            draft_edit_kb("adm", t("admin_submit_publish", "ru"), include_back=True),
        )
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data == "adm:back")
async def on_admin_draft_back(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    entry = get_request_by_id(data.get("current_request", ""))
    if entry:
        await answer(
            cb,
            _render_request_draft(entry),
            draft_edit_kb("adm", t("admin_submit_publish", "ru"), include_back=True),
            "plugins",
        )
    try:
            await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.editing_draft_field)
async def on_admin_draft_field_value(message: Message, state: FSMContext) -> None:
    text = (message.html_text or message.text or "").strip()
    if not text:
        await message.answer(t("need_text", "ru"), disable_web_page_preview=True)
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
                    reply_markup=draft_edit_kb("adm", t("admin_submit_publish", "ru"), include_back=True),
                    disable_web_page_preview=True,
                )
            except Exception:
                sent = await answer(
                    message,
                    draft_text,
                    draft_edit_kb("adm", t("admin_submit_publish", "ru"), include_back=True),
                    "plugins",
                )
                if sent:
                    await state.update_data(draft_message_id=sent.message_id)
        else:
            sent = await answer(
                message,
                draft_text,
                draft_edit_kb("adm", t("admin_submit_publish", "ru"), include_back=True),
                "plugins",
            )
            if sent:
                await state.update_data(draft_message_id=sent.message_id)


@router.callback_query(F.data == "adm:submit")
async def on_admin_publish(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    request_id = data.get("current_request")
    entry = get_request_by_id(request_id)

    if not entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    try:
        await cb.answer("Публикация...")
    except Exception:
        pass

    try:
        request_type = entry.get("type", "new")
        payload = entry.get("payload", {})

        if request_type == "update":
            old_plugin = payload.get("old_plugin", {})
            result = await update_plugin(entry, old_plugin)
            update_slug = payload.get("update_slug") or payload.get("plugin", {}).get("id")
            await _notify_subscribers(cb.bot, update_slug, payload.get("plugin", {}))
            notify_key = "notify_update_published"
        elif payload.get("submission_type") == "icon" or payload.get("icon"):
            result = await publish_icon(entry)
            notify_key = "notify_icon_published"
        elif request_type == "delete":
            payload = entry.get("payload", {})
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
            lang = get_lang(user_id)
            try:
                await cb.bot.send_message(
                    user_id,
                    t(notify_key, lang, name=name, version=version or "—"),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

        await answer(
            cb,
            t("admin_publish_done", "ru", link=result.get("link", "")),
            admin_menu_kb(_admin_menu_role(cb)),
            "profile",
        )

    except Exception as exc:
        logger.exception("Publish error")
        update_request_status(request_id, "error", comment=str(exc))
        await answer(
            cb,
            f"Ошибка:\n<code>{exc}</code>",
            admin_menu_kb(_admin_menu_role(cb)),
            "profile",
        )


@router.callback_query(F.data.startswith("adm:reject:"))
async def on_admin_reject(cb: CallbackQuery, state: FSMContext) -> None:
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry:
        payload = entry.get("payload", {})
        if payload.get("submission_type") == "icon" or payload.get("icon"):
            if not _ensure_admin_role(cb, "icons"):
                await cb.answer(t("admin_denied", "ru"), show_alert=True)
                return
        else:
            if not _ensure_admin_role(cb, "plugins"):
                await cb.answer(t("admin_denied", "ru"), show_alert=True)
                return
    else:
        if not _ensure_admin(cb):
            await cb.answer(t("admin_denied", "ru"), show_alert=True)
            return

    await _nav_push(state, f"adm:reject:{request_id}")
    await cb.message.edit_reply_markup(reply_markup=admin_reject_kb(request_id))
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
                await cb.answer(t("admin_denied", "ru"), show_alert=True)
                return
        else:
            if not _ensure_admin_role(cb, "plugins"):
                await cb.answer(t("admin_denied", "ru"), show_alert=True)
                return
    else:
        if not _ensure_admin(cb):
            await cb.answer(t("admin_denied", "ru"), show_alert=True)
            return

    await _nav_push(state, f"adm:reject_comment:{request_id}")
    await state.update_data(reject_request_id=request_id)
    await state.set_state(AdminFlow.entering_reject_comment)
    await answer(cb, t("admin_enter_reject_reason", "ru"), None, "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(AdminFlow.entering_reject_comment)
async def on_admin_enter_reject_comment(message: Message, state: FSMContext) -> None:
    comment = (message.text or "").strip()
    if not comment:
        await message.answer(t("need_text", "ru"), disable_web_page_preview=True)
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
    await answer(message, t("admin_rejected_done", "ru"), admin_menu_kb(_admin_menu_role(message)), "profile")


@router.callback_query(F.data.startswith("adm:reject_silent:"))
async def on_admin_reject_silent(cb: CallbackQuery, state: FSMContext) -> None:
    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)
    if entry:
        payload = entry.get("payload", {})
        if payload.get("submission_type") == "icon" or payload.get("icon"):
            if not _ensure_admin_role(cb, "icons"):
                await cb.answer(t("admin_denied", "ru"), show_alert=True)
                return
        else:
            if not _ensure_admin_role(cb, "plugins"):
                await cb.answer(t("admin_denied", "ru"), show_alert=True)
                return
    else:
        if not _ensure_admin(cb):
            await cb.answer(t("admin_denied", "ru"), show_alert=True)
            return

    await _nav_push(state, f"adm:reject_silent:{request_id}")
    update_request_status(request_id, "rejected")
    await answer(cb, t("admin_rejected_done", "ru"), admin_menu_kb(_admin_menu_role(cb)), "profile")
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:ban:"))
async def on_admin_ban(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    parts = cb.data.split(":")
    request_id = parts[2]
    user_id = int(parts[3])

    await _nav_push(state, f"adm:ban:{request_id}:{user_id}")

    await cb.message.edit_reply_markup(reply_markup=admin_confirm_ban_kb(request_id, user_id))
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:ban_confirm:"))
async def on_admin_ban_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin_role(cb, "super"):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    parts = cb.data.split(":")
    request_id = parts[2]
    user_id = int(parts[3])

    await _nav_push(state, f"adm:ban_confirm:{request_id}:{user_id}")

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
        t("admin_user_banned", "ru", user_id=user_id)
        + (f"\n\nУдалено плагинов/иконок: {removed_count}" if removed_count else ""),
        admin_menu_kb(_admin_menu_role(cb)),
        "profile",
    )
    try:
        await cb.answer()
    except Exception:
        pass
