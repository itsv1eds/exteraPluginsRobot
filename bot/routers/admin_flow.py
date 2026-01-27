import logging
import math
from pathlib import Path

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from bot.cache import get_admins, get_categories, get_config, invalidate
from bot.constants import PAGE_SIZE
from bot.context import get_language, get_lang
from bot.helpers import answer
from bot.keyboards import (
    admin_actions_kb,
    admin_banned_kb,
    admin_confirm_ban_kb,
    admin_config_kb,
    admin_menu_kb,
    admin_plugins_list_kb,
    admin_queue_kb,
    admin_reject_kb,
    admin_review_kb,
    admin_broadcast_confirm_kb,
    draft_category_kb,
    draft_edit_kb,
    draft_lang_kb,
)
from bot.services.publish import (
    add_submitter_to_plugin,
    build_channel_post,
    publish_plugin,
    remove_plugin_entry,
    update_plugin,
    update_catalog_entry,
)
from bot.states import AdminFlow
from bot.texts import t
from catalog import find_plugin_by_slug, list_published_plugins
from request_store import get_request_by_id, get_requests, update_request_payload, update_request_status
from storage import save_config
from user_store import ban_user, get_banned_users, list_users, unban_user

logger = logging.getLogger(__name__)
router = Router(name="admin-flow")


def _render_request_draft(entry: dict) -> str:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    fallback_desc = plugin.get("description", "")
    patched_payload = {
        **payload,
        "description_ru": payload.get("description_ru") or fallback_desc,
        "description_en": payload.get("description_en") or fallback_desc,
    }
    return build_channel_post({"payload": patched_payload})


def _ensure_admin(cb: CallbackQuery | Message) -> bool:
    user = cb.from_user if isinstance(cb, CallbackQuery) else cb.from_user
    return bool(user and user.id in get_admins())


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if not _ensure_admin(message):
        await message.answer(t("admin_denied", "ru"))
        return
    await state.set_state(AdminFlow.menu)
    await answer(message, t("admin_title", "ru"), admin_menu_kb(), "profile")


@router.callback_query(F.data == "adm:menu")
async def on_admin_menu(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return
    await state.set_state(AdminFlow.menu)
    await answer(cb, t("admin_title", "ru"), admin_menu_kb(), "profile")
    await cb.answer()


@router.callback_query(F.data == "adm:config")
async def on_admin_config(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await state.set_state(AdminFlow.menu)
    await answer(cb, "⚙️ <b>Настройки</b>", admin_config_kb(), "profile")
    await cb.answer()


@router.callback_query(F.data.startswith("adm:config:"))
async def on_admin_config_edit(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    field = cb.data.split(":")[2]
    await state.update_data(config_field=field, config_message_id=cb.message.message_id if cb.message else None)
    await state.set_state(AdminFlow.editing_config)

    if field == "admins":
        await answer(
            cb,
            "Введите список админов (ID через пробел или запятую):",
            admin_config_kb(),
            "profile",
        )
    elif field == "channel":
        await answer(
            cb,
            "Введите канал: <code>id username title publish_channel</code>\n"
            "Пример: <code>-1001234567890 mychannel ExteraPlugins exteraplugintest</code>",
            admin_config_kb(),
            "profile",
        )
    else:
        await answer(cb, "Неизвестная настройка", admin_config_kb(), "profile")
    await cb.answer()


@router.message(AdminFlow.editing_config)
async def on_admin_config_value(message: Message, state: FSMContext) -> None:
    if not _ensure_admin(message):
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer("✏️ Введите текст", disable_web_page_preview=True)
        return

    data = await state.get_data()
    field = data.get("config_field")
    config = get_config()

    if field == "admins":
        raw = text.replace(",", " ").split()
        admins = []
        for part in raw:
            try:
                admins.append(int(part))
            except ValueError:
                continue
        if not admins:
            await message.answer("❌ Не удалось распознать список ID", disable_web_page_preview=True)
            return
        config["admins"] = sorted(set(admins))
        save_config(config)
        invalidate("config")
        await message.answer("✅ Список админов обновлён", disable_web_page_preview=True)
    elif field == "channel":
        parts = text.split()
        if len(parts) < 2:
            await message.answer("❌ Укажите минимум id и username", disable_web_page_preview=True)
            return
        channel_id = parts[0]
        username = parts[1].lstrip("@")
        title = parts[2] if len(parts) > 2 else config.get("channel", {}).get("title", "")
        publish_channel = parts[3] if len(parts) > 3 else config.get("publish_channel", "")
        try:
            channel_id_value = int(channel_id)
        except ValueError:
            await message.answer("❌ Некорректный id канала", disable_web_page_preview=True)
            return
        config.setdefault("channel", {})
        config["channel"]["id"] = channel_id_value
        config["channel"]["username"] = username
        config["channel"]["title"] = title
        config["publish_channel"] = publish_channel
        save_config(config)
        invalidate("config")
        await message.answer("✅ Канал обновлён", disable_web_page_preview=True)
    else:
        await message.answer("❌ Неизвестная настройка", disable_web_page_preview=True)

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
                reply_markup=admin_menu_kb(),
                disable_web_page_preview=True,
            )
        except Exception:
            await answer(message, t("admin_title", "ru"), admin_menu_kb(), "profile")
    else:
        await answer(message, t("admin_title", "ru"), admin_menu_kb(), "profile")


@router.callback_query(F.data == "adm:broadcast")
async def on_admin_broadcast(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await state.set_state(AdminFlow.entering_broadcast)
    await state.update_data(broadcast_message_id=cb.message.message_id if cb.message else None)
    await answer(cb, "Введите сообщение для рассылки:", None, "profile")
    await cb.answer()


@router.message(AdminFlow.entering_broadcast)
async def on_admin_broadcast_message(message: Message, state: FSMContext) -> None:
    if not _ensure_admin(message):
        return

    text = (message.html_text or message.text or "").strip()
    if not text:
        await message.answer("✏️ Введите текст", disable_web_page_preview=True)
        return

    await state.update_data(broadcast_text=text)
    await state.set_state(AdminFlow.confirming_broadcast)
    broadcast_message_id = (await state.get_data()).get("broadcast_message_id")
    if broadcast_message_id:
        try:
            await message.bot.edit_message_text(
                f"📣 <b>Рассылка</b>\n\n{text}\n\nОтправить всем пользователям?",
                chat_id=message.chat.id,
                message_id=broadcast_message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_broadcast_confirm_kb(),
                disable_web_page_preview=True,
            )
        except Exception:
            await message.answer(
                f"📣 <b>Рассылка</b>\n\n{text}\n\nОтправить всем пользователям?",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_broadcast_confirm_kb(),
                disable_web_page_preview=True,
            )
    else:
        await message.answer(
            f"📣 <b>Рассылка</b>\n\n{text}\n\nОтправить всем пользователям?",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_broadcast_confirm_kb(),
            disable_web_page_preview=True,
        )


@router.callback_query(F.data == "adm:broadcast:cancel")
async def on_admin_broadcast_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await answer(cb, t("admin_title", "ru"), admin_menu_kb(), "profile")
    await cb.answer("Отменено")


@router.callback_query(F.data == "adm:broadcast:confirm")
async def on_admin_broadcast_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    data = await state.get_data()
    text = data.get("broadcast_text", "")
    if not text:
        await cb.answer("Нет текста для рассылки", show_alert=True)
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
            f"✅ Рассылка завершена. Отправлено: {sent}, ошибок: {failed}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_kb(),
            disable_web_page_preview=True,
        )
    except Exception:
        await cb.message.answer(
            f"✅ Рассылка завершена. Отправлено: {sent}, ошибок: {failed}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_kb(),
            disable_web_page_preview=True,
        )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:queue:"))
async def on_admin_queue(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    parts = cb.data.split(":")
    queue_type = parts[2]
    page = int(parts[3]) if len(parts) > 3 else 0

    req_type = None if queue_type == "all" else ("update" if queue_type == "update" else "new")
    requests = get_requests(status="pending", request_type=req_type)

    if not requests:
        await cb.answer(t("admin_queue_empty", "ru"), show_alert=True)
        return

    total = len(requests)
    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = requests[start : start + PAGE_SIZE]

    items = []
    for entry in page_items:
        plugin = entry.get("payload", {}).get("plugin", {})
        name = plugin.get("name", "?")
        version = plugin.get("version", "")
        if entry.get("type") == "update":
            type_icon = "🔄"
        elif entry.get("type") == "delete":
            type_icon = "🗑"
        else:
            type_icon = "🆕"
        label = f"{type_icon} {name} v{version}" if version else f"{type_icon} {name}"
        items.append((label, entry["id"]))

    if queue_type == "update":
        title = "🔄 Обновления"
    elif queue_type == "new":
        title = "🆕 Новые заявки"
    else:
        title = "📥 Заявки"
    caption = f"<b>{title}</b>\nСтр. {page + 1}/{total_pages}"
    await answer(cb, caption, admin_queue_kb(items, page, total_pages, queue_type), "profile")
    await cb.answer()


@router.callback_query(F.data.startswith("adm:banned:"))
async def on_admin_banned(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    page = int(cb.data.split(":")[2])
    banned = get_banned_users()

    if not banned:
        await cb.answer("📭 Нет заблокированных", show_alert=True)
        return

    total = len(banned)
    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = banned[start : start + PAGE_SIZE]

    items = [(f"👤 {user.get('username') or user['user_id']}", user["user_id"]) for user in page_items]

    caption = f"<b>🚫 Заблокированные</b>\nСтр. {page + 1}/{total_pages}"
    await answer(cb, caption, admin_banned_kb(items, page, total_pages), "profile")
    await cb.answer()


@router.callback_query(F.data.startswith("adm:unban:"))
async def on_admin_unban(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    user_id = int(cb.data.split(":")[2])
    unban_user(user_id)
    await cb.answer(t("admin_user_unbanned", "ru"), show_alert=True)

    banned = get_banned_users()
    if banned:
        items = [(f"👤 {user.get('username') or user['user_id']}", user["user_id"]) for user in banned[:PAGE_SIZE]]
        total_pages = math.ceil(len(banned) / PAGE_SIZE)
        await answer(cb, "<b>🚫 Заблокированные</b>", admin_banned_kb(items, 0, total_pages), "profile")
    else:
        await answer(cb, t("admin_title", "ru"), admin_menu_kb(), "profile")


@router.callback_query(F.data == "adm:link_author")
async def on_admin_link_author(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    plugins = list_published_plugins()
    if not plugins:
        await cb.answer(t("catalog_empty", "ru"), show_alert=True)
        return

    items = [(plugin.get("ru", {}).get("name") or plugin.get("slug"), plugin.get("slug")) for plugin in plugins[:PAGE_SIZE]]
    total_pages = math.ceil(len(plugins) / PAGE_SIZE)

    await answer(cb, t("admin_select_plugin", "ru"), admin_plugins_list_kb(items, 0, total_pages), "profile")
    await cb.answer()


@router.callback_query(F.data == "adm:edit_plugins")
async def on_admin_edit_plugins(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    await state.set_state(AdminFlow.searching_plugin)
    await answer(cb, "🔍 Введите запрос для поиска плагина:", None, "profile")
    await cb.answer()


@router.message(AdminFlow.searching_plugin)
async def on_admin_search_plugins(message: Message, state: FSMContext) -> None:
    if not _ensure_admin(message):
        return

    query = (message.text or "").strip().lower()
    if not query:
        await message.answer("✏️ Введите текст", disable_web_page_preview=True)
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

    if not filtered:
        await message.answer("😕 Ничего не найдено", disable_web_page_preview=True)
        return

    items = [(p.get("ru", {}).get("name") or p.get("slug"), p.get("slug")) for p in filtered[:PAGE_SIZE]]
    total_pages = math.ceil(len(filtered) / PAGE_SIZE)
    await message.answer(
        "🧩 Найденные плагины:",
        reply_markup=admin_plugins_list_kb(
            items,
            0,
            total_pages,
            select_prefix="adm:edit_select",
            list_prefix="adm:edit_list",
        ),
    )


@router.callback_query(F.data.startswith("adm:edit_list:"))
async def on_admin_edit_list(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
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
        "🧩 Найденные плагины:",
        admin_plugins_list_kb(
            items,
            page,
            total_pages,
            select_prefix="adm:edit_select",
            list_prefix="adm:edit_list",
        ),
        "profile",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:edit_select:"))
async def on_admin_edit_select(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    slug = cb.data.split(":")[2]
    plugin = find_plugin_by_slug(slug)
    if not plugin:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    edit_payload = {
        "plugin": {
            "name": plugin.get("ru", {}).get("name") or "",
            "author": plugin.get("author") or "",
            "description": plugin.get("ru", {}).get("description") or "",
            "version": plugin.get("ru", {}).get("version") or "",
            "min_version": plugin.get("requirements", {}).get("min_version") or "",
            "has_ui_settings": plugin.get("requirements", {}).get("settings", False),
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
    await answer(
        cb,
        draft_text,
        draft_edit_kb("adm_edit", "✅ Обновить", include_back=True),
        "profile",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:plugins_list:"))
async def on_admin_plugins_list(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
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
    await cb.answer()


@router.callback_query(F.data.startswith("adm:select_plugin:"))
async def on_admin_select_plugin(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    slug = cb.data.split(":")[2]
    await state.update_data(link_plugin_slug=slug)
    await state.set_state(AdminFlow.linking_author_user)
    await cb.message.answer(t("admin_enter_user_id", "ru"))
    await cb.answer()


@router.message(AdminFlow.linking_author_user)
async def on_admin_link_author_user(message: Message, state: FSMContext) -> None:
    if not _ensure_admin(message):
        return

    data = await state.get_data()
    slug = data.get("link_plugin_slug")
    if not slug:
        await state.set_state(AdminFlow.menu)
        return

    text = (message.text or "").strip()
    parts = text.split()
    if not parts:
        await message.answer("❌ Введите корректный user_id")
        return

    try:
        user_id = int(parts[0])
    except ValueError:
        await message.answer("❌ Введите корректный user_id")
        return

    username = parts[1] if len(parts) > 1 else ""
    username = username.lstrip("@")

    success = add_submitter_to_plugin(slug, user_id, username)

    if success:
        await message.answer(t("admin_author_linked", "ru"))
    else:
        await message.answer("❌ Не удалось привязать")

    await state.clear()
    await state.set_state(AdminFlow.menu)
    await answer(message, t("admin_title", "ru"), admin_menu_kb(), "profile")


@router.callback_query(F.data.startswith("adm:review:"))
async def on_admin_review(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)

    if not entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
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

    user_link = f"@{username}" if username else f"<code>{user_id}</code>"
    settings = "✅" if plugin.get("has_ui_settings") else "❌"

    if request_type == "update":
        changelog = payload.get("changelog", "—")
        old_plugin = payload.get("old_plugin", {})
        old_version = old_plugin.get("ru", {}).get("version") or "?"
        text = (
            f"🔄 <b>Обновление</b>\n\n"
            f"<b>ID:</b> <code>{request_id}</code>\n"
            f"<b>Плагин:</b> {plugin.get('name', '—')}\n"
            f"<b>Версия:</b> {old_version} → {plugin.get('version', '—')}\n"
            f"<b>Мин. версия:</b> {plugin.get('min_version', '—')}\n\n"
            f"<b>Изменения:</b>\n<blockquote>{changelog}</blockquote>\n\n"
            f"<b>От:</b> {user_link}"
        )
    elif request_type == "delete":
        delete_slug = payload.get("delete_slug") or plugin.get("id") or "—"
        text = (
            f"🗑 <b>Удаление</b>\n\n"
            f"<b>ID:</b> <code>{request_id}</code>\n"
            f"<b>Плагин:</b> {plugin.get('name', '—')}\n"
            f"<b>Slug:</b> <code>{delete_slug}</code>\n\n"
            f"<b>От:</b> {user_link}"
        )
    else:
        draft_text = _render_request_draft(entry)
        text = (
            f"📋 <b>Новый плагин</b>\n\n"
            f"<b>ID:</b> <code>{request_id}</code>\n\n"
            f"{draft_text}\n\n"
            f"<b>От:</b> {user_link}"
        )

    file_path = plugin.get("file_path")
    if request_type == "delete":
        kb = admin_review_kb(request_id, user_id, submit_label="🗑 Удалить", submit_callback=f"adm:delete:{request_id}")
    else:
        kb = admin_review_kb(request_id, user_id)

    if file_path and Path(file_path).exists():
        await cb.message.answer_document(
            FSInputFile(file_path),
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    else:
        await cb.message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )

    await cb.answer()


@router.callback_query(F.data.startswith("adm:actions:"))
async def on_admin_actions(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    parts = cb.data.split(":")
    request_id = parts[2]
    user_id = int(parts[3])

    await cb.message.edit_reply_markup(reply_markup=admin_actions_kb(request_id, user_id))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:back_review:"))
async def on_admin_back_review(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
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
            submit_label="🗑 Удалить",
            submit_callback=f"adm:delete:{request_id}",
        )
    else:
        kb = admin_review_kb(request_id, user_id)

    await cb.message.edit_reply_markup(reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:delete:"))
async def on_admin_delete(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    request_id = cb.data.split(":")[2]
    await state.update_data(current_request=request_id)
    await on_admin_publish(cb, state)


@router.callback_query(F.data.startswith("adm:prepublish:"))
async def on_admin_prepublish(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    request_id = cb.data.split(":")[2]
    entry = get_request_by_id(request_id)

    if not entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    await state.set_state(AdminFlow.reviewing)
    await state.update_data(current_request=request_id)

    draft_text = _render_request_draft(entry)
    await answer(
        cb,
        draft_text,
        draft_edit_kb("adm", "✅ Опубликовать", include_back=True),
        "plugins",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:edit:"))
async def on_admin_draft_edit(cb: CallbackQuery, state: FSMContext) -> None:
    field = cb.data.split(":")[2]

    if field in {"description", "usage"}:
        await state.update_data(edit_field=field)
        await answer(cb, "Выберите язык:", draft_lang_kb("adm", field), None)
        await cb.answer()
        return

    if field == "category":
        await state.update_data(edit_field=field)
        await answer(cb, "Выберите категорию:", draft_category_kb("adm", get_categories()), None)
        await cb.answer()
        return

    prompt = {
        "name": "Введите новое название:",
        "author": "Введите автора:",
        "settings": "Есть настройки? (да/нет)",
        "min_version": "Введите минимальную версию:",
        "checked_on": "Введите версию проверки и дату, например: <code>1.4.2 (27.01.26)</code>",
    }.get(field, "Введите значение:")

    await state.update_data(edit_field=field)
    await state.set_state(AdminFlow.editing_draft_field)
    await answer(cb, prompt, None, None)
    await cb.answer()


@router.callback_query(F.data.startswith("adm_edit:edit:"))
async def on_admin_catalog_edit(cb: CallbackQuery, state: FSMContext) -> None:
    field = cb.data.split(":")[2]

    if field in {"description", "usage"}:
        await state.update_data(edit_field=field)
        await answer(cb, "Выберите язык:", draft_lang_kb("adm_edit", field), None)
        await cb.answer()
        return

    if field == "category":
        await state.update_data(edit_field=field)
        await answer(cb, "Выберите категорию:", draft_category_kb("adm_edit", get_categories()), None)
        await cb.answer()
        return

    prompt = {
        "name": "Введите новое название:",
        "author": "Введите автора:",
        "settings": "Есть настройки? (да/нет)",
        "min_version": "Введите минимальную версию:",
        "checked_on": "Введите версию проверки и дату, например: <code>1.4.2 (27.01.26)</code>",
    }.get(field, "Введите значение:")

    await state.update_data(edit_field=field)
    await state.set_state(AdminFlow.editing_catalog_field)
    await answer(cb, prompt, None, None)
    await cb.answer()


@router.callback_query(F.data.startswith("adm_edit:lang:"))
async def on_admin_catalog_language(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, field, lang_choice = cb.data.split(":")
    await state.update_data(edit_field=field, edit_lang=lang_choice)
    await state.set_state(AdminFlow.editing_catalog_field)

    prompt = "Введите текст (RU):" if lang_choice == "ru" else "Введите текст (EN):"
    await answer(cb, prompt, None, None)
    await cb.answer()


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
        draft_edit_kb("adm_edit", "✅ Обновить", include_back=True),
        "profile",
    )
    await cb.answer()


@router.callback_query(F.data == "adm_edit:back")
async def on_admin_catalog_back(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    payload = data.get("edit_payload", {})
    draft_text = build_channel_post({"payload": payload})
    await state.set_state(AdminFlow.editing_catalog_plugin)
    await answer(
        cb,
        draft_text,
        draft_edit_kb("adm_edit", "✅ Обновить", include_back=True),
        "profile",
    )
    await cb.answer()


@router.message(AdminFlow.editing_catalog_field)
async def on_admin_catalog_field_value(message: Message, state: FSMContext) -> None:
    text = (message.html_text or message.text or "").strip()
    if not text:
        await message.answer("✏️ Введите текст", disable_web_page_preview=True)
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
        plugin["has_ui_settings"] = text.lower() in {"да", "yes", "1", "✅", "true"}

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
                reply_markup=draft_edit_kb("adm_edit", "✅ Обновить", include_back=True),
                disable_web_page_preview=True,
            )
            return
        except Exception:
            pass

    await answer(
        message,
        draft_text,
        draft_edit_kb("adm_edit", "✅ Обновить", include_back=True),
        "profile",
    )


@router.callback_query(F.data == "adm_edit:submit")
async def on_admin_catalog_submit(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
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
        await cb.answer("❌ Сообщение канала не найдено", show_alert=True)
        return

    await cb.answer("⏳ Обновление...")
    from userbot.client import get_userbot

    userbot = await get_userbot()
    if not userbot:
        await cb.answer("❌ Userbot недоступен", show_alert=True)
        return

    entry = {"payload": payload}
    post_text = build_channel_post(entry)
    await userbot.update_message(message_id, post_text, None)
    update_catalog_entry(slug, entry, message_id)

    await state.set_state(AdminFlow.menu)
    await answer(cb, t("admin_title", "ru"), admin_menu_kb(), "profile")
    await cb.answer()


@router.callback_query(F.data.startswith("adm:lang:"))
async def on_admin_draft_language(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, field, lang_choice = cb.data.split(":")
    await state.update_data(edit_field=field, edit_lang=lang_choice)
    await state.set_state(AdminFlow.editing_draft_field)

    prompt = "Введите текст (RU):" if lang_choice == "ru" else "Введите текст (EN):"
    await answer(cb, prompt, None, None)
    await cb.answer()


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
            draft_edit_kb("adm", "✅ Опубликовать", include_back=True),
            "plugins",
        )
    await cb.answer()


@router.callback_query(F.data == "adm:back")
async def on_admin_draft_back(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    entry = get_request_by_id(data.get("current_request", ""))
    if entry:
        await answer(
            cb,
            _render_request_draft(entry),
            draft_edit_kb("adm", "✅ Опубликовать", include_back=True),
            "plugins",
        )
    await cb.answer()


@router.message(AdminFlow.editing_draft_field)
async def on_admin_draft_field_value(message: Message, state: FSMContext) -> None:
    text = (message.html_text or message.text or "").strip()
    if not text:
        await message.answer("✏️ Введите текст", disable_web_page_preview=True)
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
        has_settings = text.lower() in {"да", "yes", "1", "✅", "true"}
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
                    reply_markup=draft_edit_kb("adm", "✅ Опубликовать", include_back=True),
                    disable_web_page_preview=True,
                )
            except Exception:
                await answer(
                    message,
                    draft_text,
                    draft_edit_kb("adm", "✅ Опубликовать", include_back=True),
                    "plugins",
                )
        else:
            await answer(
                message,
                draft_text,
                draft_edit_kb("adm", "✅ Опубликовать", include_back=True),
                "plugins",
            )


@router.callback_query(F.data == "adm:submit")
async def on_admin_publish(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    data = await state.get_data()
    request_id = data.get("current_request")
    entry = get_request_by_id(request_id)

    if not entry:
        await cb.answer(t("not_found", "ru"), show_alert=True)
        return

    await cb.answer("⏳ Публикация...")

    try:
        request_type = entry.get("type", "new")

        if request_type == "update":
            payload = entry.get("payload", {})
            old_plugin = payload.get("old_plugin", {})
            result = await update_plugin(entry, old_plugin)
            notify_key = "notify_update_published"
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
        name = entry.get("payload", {}).get("plugin", {}).get("name", "")

        if user_id:
            lang = get_lang(user_id)
            try:
                await cb.bot.send_message(
                    user_id,
                    t(notify_key, lang, name=name),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

        await cb.message.answer(
            f"✅ <b>Опубликовано!</b>\n\n🔗 {result.get('link', '')}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_kb(),
            disable_web_page_preview=True,
        )

    except Exception as exc:
        logger.exception("Publish error")
        update_request_status(request_id, "error", comment=str(exc))
        await cb.message.answer(
            f"❌ Ошибка:\n<code>{exc}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_kb(),
            disable_web_page_preview=True,
        )


@router.callback_query(F.data.startswith("adm:reject:"))
async def on_admin_reject(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    request_id = cb.data.split(":")[2]
    await cb.message.edit_reply_markup(reply_markup=admin_reject_kb(request_id))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:reject_comment:"))
async def on_admin_reject_comment(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    request_id = cb.data.split(":")[2]
    await state.update_data(reject_request_id=request_id)
    await state.set_state(AdminFlow.entering_reject_comment)
    await cb.message.answer("📝 Введите причину:", disable_web_page_preview=True)
    await cb.answer()


@router.message(AdminFlow.entering_reject_comment)
async def on_admin_enter_reject_comment(message: Message, state: FSMContext) -> None:
    if not _ensure_admin(message):
        return

    comment = (message.text or "").strip()
    if not comment:
        await message.answer("✏️ Введите текст", disable_web_page_preview=True)
        return

    data = await state.get_data()
    request_id = data.get("reject_request_id")
    if not request_id:
        await state.set_state(AdminFlow.menu)
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
    await answer(message, "❌ Отклонено", admin_menu_kb(), "profile")


@router.callback_query(F.data.startswith("adm:reject_silent:"))
async def on_admin_reject_silent(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    request_id = cb.data.split(":")[2]
    update_request_status(request_id, "rejected")
    await cb.message.answer("❌ Отклонено", reply_markup=admin_menu_kb(), disable_web_page_preview=True)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ban:"))
async def on_admin_ban(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    parts = cb.data.split(":")
    request_id = parts[2]
    user_id = int(parts[3])

    await cb.message.edit_reply_markup(reply_markup=admin_confirm_ban_kb(request_id, user_id))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ban_confirm:"))
async def on_admin_ban_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    if not _ensure_admin(cb):
        await cb.answer(t("admin_denied", "ru"), show_alert=True)
        return

    parts = cb.data.split(":")
    request_id = parts[2]
    user_id = int(parts[3])

    ban_user(user_id, reason="Заблокирован администратором")
    update_request_status(request_id, "rejected", comment="Пользователь заблокирован")

    try:
        await cb.bot.send_message(
            user_id,
            "🚫 <b>Вы заблокированы</b>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        pass

    await cb.message.answer(
        f"🚫 Пользователь <code>{user_id}</code> заблокирован",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_kb(),
    )
    await cb.answer()
