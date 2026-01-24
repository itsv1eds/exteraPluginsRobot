import asyncio
import html
import logging
import math
import os
import random
import re
from pathlib import Path
import copy
from typing import Any, Dict, Optional, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    Document,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InputMediaPhoto,
    Message,
)
from aiogram.types import FSInputFile

router = Router()

from catalog import (
    find_icon_by_slug,
    find_plugin_by_slug,
    find_icons_by_handles,
    find_plugins_by_handles,
    list_icons_by_category,
    list_plugins_by_category,
    list_published_icons,
    list_published_plugins,
    search_icons,
    search_plugins,
)
from plugin_parser import PluginParseError, parse_plugin_file
from requests_store import (
    add_request,
    get_request_by_id,
    get_request_by_plugin_id,
    get_requests,
    get_user_requests,
    update_request_payload,
    update_request_status,
)
from storage import load_config
from user_store import get_user_language, set_user_language

from .keyboards import (
    admin_menu_inline,
    admin_menu_keyboard,
    admin_queue_keyboard,
    catalog_categories_keyboard,
    catalog_items_keyboard,
    catalog_plugin_keyboard,
    catalog_navigation_keyboard,
    catalog_search_prompt_keyboard,
    catalog_search_results_keyboard,
    category_keyboard,
    confirm_keyboard,
    edit_back_keyboard,
    edit_menu_keyboard,
    language_keyboard,
    main_menu_keyboard,
    profile_item_actions_keyboard,
    profile_items_keyboard,
    profile_menu_keyboard,
    publish_actions_keyboard,
    review_actions_keyboard,
    submission_type_keyboard,
    LANGUAGE_OPTIONS,
    DRAFT_EDITOR_BUTTONS,
)
from .states import AdminReview, UserSubmission

CONFIG = load_config()
CATEGORY_OPTIONS = CONFIG.get("categories", [])
ALL_CATEGORY_OPTION = {"key": "_all", "ru": "Все плагины", "en": "All plugins"}


class PluginFileError(Exception):
    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


DRAFT_FIELD_SPECS = {
    "name_ru": {
        "group": "plugin",
        "key": "name",
        "label_key": "draft_field_name",
        "prompt_key": "draft_prompt_name",
        "required": True,
    },
    "name_en": {
        "group": "data",
        "key": "en_name",
        "label_key": "draft_field_name",
        "prompt_key": "draft_prompt_name_en",
        "required": True,
    },
    "description_ru": {
        "group": "plugin",
        "key": "description",
        "label_key": "draft_field_description",
        "prompt_key": "draft_prompt_description",
        "required": True,
    },
    "description_en": {
        "group": "data",
        "key": "en_description",
        "label_key": "draft_field_description",
        "prompt_key": "draft_prompt_description_en",
        "required": True,
    },
    "usage_ru": {
        "group": "data",
        "key": "usage",
        "label_key": "draft_field_usage",
        "prompt_key": "draft_prompt_usage",
        "required": True,
    },
    "usage_en": {
        "group": "data",
        "key": "en_usage",
        "label_key": "draft_field_usage",
        "prompt_key": "draft_prompt_usage_en",
        "required": True,
    },
    "author": {
        "group": "plugin",
        "key": "author",
        "label_key": "draft_field_author",
        "prompt_key": "draft_prompt_author",
        "required": True,
    },
    "author_channel": {
        "group": "data",
        "key": "author_channel",
        "label_key": "draft_field_author_channel",
        "prompt_key": "draft_prompt_author_channel",
        "required": True,
    },
    "version": {
        "group": "plugin",
        "key": "version",
        "label_key": "draft_field_version",
        "prompt_key": "draft_prompt_version",
        "required": True,
    },
    "min_version": {
        "group": "plugin",
        "key": "min_version",
        "label_key": "draft_field_min_version",
        "prompt_key": "draft_prompt_min_version",
        "required": True,
    },
    "has_ui": {
        "group": "plugin",
        "key": "has_ui_settings",
        "label_key": "draft_field_has_ui",
        "required": True,
    },
    "category": {
        "group": "category",
        "label_key": "draft_field_category",
        "prompt_key": "draft_prompt_category",
        "required": True,
    },
    "file": {
        "group": "file",
        "label_key": "draft_field_file",
        "prompt_key": "draft_prompt_file",
        "required": True,
    },
}


async def _ingest_plugin_document(bot: Bot, document: Document) -> Dict[str, Any]:
    if not document or not document.file_name.endswith(".plugin"):
        raise PluginFileError("invalid_extension")

    try:
        file_path = await download_file(bot, document.file_id)
    except Exception:
        raise PluginFileError("download_error")

    try:
        metadata = parse_plugin_file(file_path)
    except (FileNotFoundError, PluginParseError) as exc:
        raise PluginFileError("parse_error", error=str(exc))

    try:
        plugin_file = ensure_plugin_file_named(metadata.id, file_path)
    except Exception as exc:
        logging.error("Failed to rename plugin file: %s", exc)
        raise PluginFileError("file_save_error")

    plugin_dict = metadata_to_dict(metadata, plugin_file)
    storage_meta = await store_plugin_file(bot, metadata.id, plugin_file)
    if storage_meta:
        plugin_dict["storage"] = storage_meta
    return plugin_dict


DRAFT_FIELD_LAYOUT = [
    ["name_ru", "name_en"],
    ["description_ru", "description_en"],
    ["usage_ru", "usage_en"],
    ["author", "author_channel"],
    ["version", "min_version"],
    ["has_ui", "category"],
    ["file"],
]

USER_DRAFT_STATES: Tuple[Any, ...] = (
    UserSubmission.draft_editor,
    UserSubmission.draft_waiting_value,
    UserSubmission.draft_waiting_file,
    UserSubmission.draft_choose_category,
)

ADMIN_DRAFT_STATES: Tuple[Any, ...] = (
    AdminReview.review_item,
    AdminReview.draft_waiting_value,
    AdminReview.draft_waiting_file,
    AdminReview.draft_choose_category,
)

DRAFT_CONTEXTS: Dict[str, Dict[str, Any]] = {
    "user": {
        "title_key": "draft_editor_title",
        "submit_blocked_key": "draft_editor_submit_blocked",
        "submit_ready_key": "draft_editor_submit_ready",
        "submit_callback": "draft:submit",
        "cancel_callback": "draft:cancel",
        "menu_callback": "draft:menu",
        "category_prompt_key": "ask_category",
        "file_prompt_key": "draft_prompt_file",
        "plugin_caption_key": "draft_plugin_file",
        "field_callback_prefix": "draft:field:",
        "base_state": UserSubmission.draft_editor,
        "waiting_value_state": UserSubmission.draft_waiting_value,
        "waiting_file_state": UserSubmission.draft_waiting_file,
        "choose_category_state": UserSubmission.draft_choose_category,
        "store_key": "plugin",
    },
    "admin": {
        "title_key": "admin_draft_title",
        "submit_blocked_key": "draft_editor_submit_blocked",
        "submit_ready_key": "draft_editor_submit_ready",
        "submit_callback": "admin:submit",
        "cancel_callback": "admin:menu",
        "menu_callback": "admin:draft:menu",
        "category_prompt_key": "ask_category",
        "file_prompt_key": "draft_prompt_file",
        "plugin_caption_key": "admin_plugin_file",
        "field_callback_prefix": "admin:field:",
        "base_state": AdminReview.review_item,
        "waiting_value_state": AdminReview.draft_waiting_value,
        "waiting_file_state": AdminReview.draft_waiting_file,
        "choose_category_state": AdminReview.draft_choose_category,
        "store_key": "admin_draft",
    },
}


def get_admin_missing_fields(payload: Dict[str, Any], language: str) -> list[str]:
    data = {"admin_draft": payload or {}}
    return get_draft_missing_fields(data, language, context="admin")


def admin_payload_is_complete(payload: Dict[str, Any]) -> bool:
    data = {"admin_draft": payload or {}}
    return draft_is_complete(data, context="admin")


def _get_context_store(data: Dict[str, Any], context: str) -> Dict[str, Any]:
    if context == "admin":
        return data.get("admin_draft", {})
    return data


def _copy_plugin(data: Dict[str, Any], context: str = "user") -> Dict[str, Any]:
    source = _get_context_store(data, context)
    plugin = source.get("plugin") or {}
    return copy.deepcopy(plugin)


def _update_state_plugin(data: Dict[str, Any], plugin: Dict[str, Any], context: str = "user") -> None:
    if context == "admin":
        draft = data.get("admin_draft") or {}
        draft["plugin"] = plugin
        data["admin_draft"] = draft
    else:
        data["plugin"] = plugin


def _get_draft_field_value(data: Dict[str, Any], field_id: str, context: str = "user") -> Any:
    spec = DRAFT_FIELD_SPECS.get(field_id)
    if not spec:
        return None
    group = spec["group"]
    store = data if context == "user" else data.get("admin_draft", {})
    if group == "plugin":
        return (store.get("plugin") or {}).get(spec["key"])
    if group == "data":
        return store.get(spec["key"])
    if group == "category":
        return store.get("category_label")
    if group == "file":
        plugin = store.get("plugin") or {}
        storage = plugin.get("storage") or {}
        if storage.get("file_id") or storage.get("message_id"):
            return storage
        return plugin.get("file_path")
    return None


def _draft_field_filled(data: Dict[str, Any], field_id: str, context: str = "user") -> bool:
    value = _get_draft_field_value(data, field_id, context)
    store = _get_context_store(data, context)
    if field_id == "category":
        return bool(store.get("category_key"))
    if field_id == "file":
        if isinstance(value, dict):
            return bool(value.get("file_id") or value.get("message_id"))
        return bool(value)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def get_draft_missing_fields(data: Dict[str, Any], language: str, context: str = "user") -> list[str]:
    missing: list[str] = []
    for field_id, spec in DRAFT_FIELD_SPECS.items():
        if not spec.get("required"):
            continue
        if not _draft_field_filled(data, field_id, context):
            label = translate(spec["label_key"], language)
            missing.append(label)
    return missing


def draft_is_complete(data: Dict[str, Any], context: str = "user") -> bool:
    for field_id, spec in DRAFT_FIELD_SPECS.items():
        if spec.get("required") and not _draft_field_filled(data, field_id, context):
            return False
    store = data if context == "user" else data.get("admin_draft", {})
    plugin = store.get("plugin") or {}
    storage = plugin.get("storage") or {}
    if not storage and not plugin.get("file_path"):
        return False
    return True


def _get_draft_field_status_icon(field_id: str, data: Dict[str, Any], context: str = "user") -> str:
    return "✅" if _draft_field_filled(data, field_id, context) else "⚠️"


def _get_draft_button_text(field_id: str, data: Dict[str, Any], language: str, context: str = "user") -> str:
    if field_id == "has_ui":
        store = data if context == "user" else data.get("admin_draft", {})
        plugin = store.get("plugin") or {}
        has_ui = plugin.get("has_ui_settings")
        button_key = "has_ui_on" if has_ui else "has_ui_off"
        base_map = DRAFT_EDITOR_BUTTONS.get(button_key, {})
        base = base_map.get(language) or base_map.get("ru") or field_id
        return f"{_get_draft_field_status_icon(field_id, data, context)} {base}"

    base_map = DRAFT_EDITOR_BUTTONS.get(field_id) or {}
    base = base_map.get(language) or base_map.get("ru") or field_id
    if field_id == "file":
        store = data if context == "user" else data.get("admin_draft", {})
        plugin = store.get("plugin") or {}
        storage = plugin.get("storage") or {}
        if storage.get("message_id"):
            base = translate("draft_file_storage", language)
        elif plugin.get("file_path"):
            try:
                filename = Path(plugin.get("file_path")).name
            except Exception:
                filename = plugin.get("file_path", "file")
            base = translate("draft_file_local", language, name=filename)
    return f"{_get_draft_field_status_icon(field_id, data)} {base}"


def build_draft_editor_keyboard(
    data: Dict[str, Any],
    language: str,
    *,
    context: str = "user",
) -> InlineKeyboardMarkup:
    inline_keyboard: list[list[InlineKeyboardButton]] = []
    for row in DRAFT_FIELD_LAYOUT:
        buttons: list[InlineKeyboardButton] = []
        for field_id in row:
            buttons.append(
                InlineKeyboardButton(
                    text=_get_draft_button_text(field_id, data, language, context),
                    callback_data=f"{DRAFT_CONTEXTS[context]['field_callback_prefix']}{field_id}",
                )
            )
        inline_keyboard.append(buttons)

    submit_button = InlineKeyboardButton(
        text=translate("draft_submit_button", language), callback_data=DRAFT_CONTEXTS[context]["submit_callback"]
    )
    cancel_button = InlineKeyboardButton(
        text=translate("draft_cancel_button", language), callback_data=DRAFT_CONTEXTS[context]["cancel_callback"]
    )
    inline_keyboard.append([submit_button])
    inline_keyboard.append([cancel_button])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def build_draft_editor_caption(data: Dict[str, Any], language: str, *, context: str = "user") -> str:
    ctx = DRAFT_CONTEXTS[context]
    lines = [f"<b>{translate(ctx['title_key'], language)}</b>"]
    missing = get_draft_missing_fields(data, language, context)
    if missing:
        lines.append(translate(ctx["submit_blocked_key"], language, fields=", ".join(missing)))
    else:
        lines.append(translate(ctx["submit_ready_key"], language))
    lines.append("")

    for row in DRAFT_FIELD_LAYOUT:
        parts: list[str] = []
        for field_id in row:
            spec = DRAFT_FIELD_SPECS.get(field_id)
            if not spec:
                continue
            label = translate(spec["label_key"], language)
            parts.append(f"{_get_draft_field_status_icon(field_id, data, context)} {label}")
        if parts:
            lines.append(" · ".join(parts))

    return "\n".join(lines)


def _draft_prompt_keyboard(language: str, context: str = "user") -> InlineKeyboardMarkup:
    ctx = DRAFT_CONTEXTS[context]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=translate("draft_back_button", language),
                    callback_data=ctx["menu_callback"],
                )
            ]
        ]
    )


async def _save_draft_field(state: FSMContext, field_id: str, value: Any, context: str = "user") -> None:
    spec = DRAFT_FIELD_SPECS.get(field_id)
    if not spec:
        return
    data = await state.get_data()
    group = spec.get("group")
    if group == "plugin":
        plugin = _copy_plugin(data, context)
        plugin[spec["key"]] = value
        if context == "admin":
            draft = data.get("admin_draft") or {}
            draft["plugin"] = plugin
            await state.update_data(admin_draft=draft)
        else:
            await state.update_data(plugin=plugin)
    elif group == "data":
        if context == "admin":
            draft = data.get("admin_draft") or {}
            draft[spec["key"]] = value
            await state.update_data(admin_draft=draft)
        else:
            await state.update_data(**{spec["key"]: value})


async def _persist_admin_draft(state: FSMContext) -> None:
    data = await state.get_data()
    request_id = data.get("current_request_id")
    payload = data.get("admin_draft")
    if not request_id or not payload:
        return
    try:
        update_request_payload(request_id, payload)
    except Exception:
        logging.exception("Failed to persist admin draft for %s", request_id)


async def _render_editor(
    target: Message | CallbackQuery,
    state: FSMContext,
    language: str,
    context: str,
    *,
    send_file: bool = False,
) -> None:
    if context == "admin":
        await show_admin_draft_editor(target, state, language, send_file=send_file)
    else:
        await show_draft_editor(target, state, language, send_file=send_file)


async def _handle_draft_field_select(
    callback: CallbackQuery,
    state: FSMContext,
    field_id: str,
    context: str,
) -> None:
    language = await get_language_for_callback(callback, state)
    spec = DRAFT_FIELD_SPECS.get(field_id)
    ctx = DRAFT_CONTEXTS[context]
    if not spec:
        await callback.answer("Unknown field", show_alert=True)
        return
    if field_id == "has_ui":
        data = await state.get_data()
        plugin = _copy_plugin(data, context)
        plugin["has_ui_settings"] = not plugin.get("has_ui_settings", False)
        await _save_draft_field(state, field_id, plugin["has_ui_settings"], context)
        if context == "admin":
            await _persist_admin_draft(state)
        await _render_editor(callback, state, language, context)
        await callback.answer()
        return
    if field_id == "category":
        await state.set_state(ctx["choose_category_state"])
        await _send_rich_media(
            callback,
            PLUGINS_IMAGE_PATH,
            translate(ctx["category_prompt_key"], language),
            category_keyboard(CATEGORY_OPTIONS, language),
        )
        await callback.answer()
        return
    if field_id == "file":
        await state.update_data(draft_edit_field=field_id)
        await state.set_state(ctx["waiting_file_state"])
        await _send_rich_media(
            callback,
            PLUGINS_IMAGE_PATH,
            translate(ctx["file_prompt_key"], language),
            _draft_prompt_keyboard(language, context),
        )
        await callback.answer()
        return

    prompt_key = spec.get("prompt_key")
    message = translate(prompt_key, language) if prompt_key else translate("draft_field_need_text", language)
    await state.update_data(draft_edit_field=field_id)
    await state.set_state(ctx["waiting_value_state"])
    await _send_rich_media(
        callback,
        PLUGINS_IMAGE_PATH,
        message,
        _draft_prompt_keyboard(language, context),
    )
    await callback.answer()


async def _handle_draft_value_input(message: Message, state: FSMContext, context: str) -> None:
    language = await get_language_for_message(message, state)
    data = await state.get_data()
    field_id = data.get("draft_edit_field")
    if not field_id:
        await message.answer(translate("draft_field_need_text", language))
        return
    text = get_formatted_text(message).strip()
    if not text:
        await message.answer(translate("draft_field_need_text", language))
        return
    await _save_draft_field(state, field_id, text, context)
    if context == "admin":
        await _persist_admin_draft(state)
    await state.set_state(DRAFT_CONTEXTS[context]["base_state"])
    await state.update_data(draft_edit_field=None)
    await message.answer(translate("draft_field_updated", language))
    await _render_editor(message, state, language, context)


async def _handle_draft_file_input(message: Message, state: FSMContext, context: str) -> None:
    language = await get_language_for_message(message, state)
    document = message.document
    try:
        plugin_dict = await _ingest_plugin_document(message.bot, document)
    except PluginFileError as exc:
        await message.answer(translate(exc.key, language, **exc.params))
        return

    if context == "admin":
        data = await state.get_data()
        draft = data.get("admin_draft") or {}
        draft["plugin"] = plugin_dict
        await state.update_data(admin_draft=draft, draft_edit_field=None)
        await _persist_admin_draft(state)
    else:
        await state.update_data(plugin=plugin_dict, draft_edit_field=None)

    await state.set_state(DRAFT_CONTEXTS[context]["base_state"])
    await message.answer(translate("draft_field_updated", language))
    await _render_editor(message, state, language, context, send_file=True)


async def _handle_draft_file_fallback(message: Message, state: FSMContext, context: str) -> None:
    language = await get_language_for_message(message, state)
    await message.answer(translate("invalid_extension", language))


async def _handle_category_select(
    callback: CallbackQuery,
    state: FSMContext,
    category: Dict[str, Any],
    context: str,
) -> None:
    language = await get_language_for_callback(callback, state)
    label = f"{category.get('ru', '')} / {category.get('en', '')}"
    if context == "admin":
        data = await state.get_data()
        draft = data.get("admin_draft") or {}
        draft["category_key"] = category.get("key")
        draft["category_label"] = label
        await state.update_data(admin_draft=draft)
        await _persist_admin_draft(state)
    else:
        await state.update_data(
            category_key=category.get("key"),
            category_label=label,
        )
    await state.set_state(DRAFT_CONTEXTS[context]["base_state"])
    await _render_editor(callback, state, language, context)
    await callback.answer(translate("category_selected", language))

async def show_draft_editor(
    target: Message | CallbackQuery,
    state: FSMContext,
    language: str,
    *,
    send_file: bool = False,
) -> None:
    data = await state.get_data()
    caption = build_draft_editor_caption(data, language)
    keyboard = build_draft_editor_keyboard(data, language)
    await _send_rich_media(target, PLUGINS_IMAGE_PATH, caption, keyboard)
    if send_file:
        plugin = data.get("plugin", {})
        await _send_plugin_file(
            target,
            plugin,
            f"<b>{translate('draft_plugin_file', language)}</b>",
        )
ADMIN_IDS = {int(admin_id) for admin_id in CONFIG.get("admins", [])}
BASE_DIR = Path(__file__).resolve().parent.parent
WELCOME_IMAGE_PATH = BASE_DIR / "img" / "welcome.png"
CATALOG_IMAGE_PATH = BASE_DIR / "img" / "catalog.png"
PROFILE_IMAGE_PATH = BASE_DIR / "img" / "profile.png"
PLUGINS_IMAGE_PATH = BASE_DIR / "img" / "plugins.png"
ICONPACKS_IMAGE_PATH = BASE_DIR / "img" / "iconpacks.png"

DEFAULT_LANGUAGE = "ru"
LANGUAGE_PROMPT = "Выберите язык интерфейса / Choose interface language"

TEXTS = {
    "language_saved": {
        "ru": "✅ Готово! Интерфейс теперь на русском.",
        "en": "✅ Done! Interface switched to English.",
    },
    "welcome": {
        "ru": "<b>👋 Привет!</b>\nЭтот бот поможет предложить или обновить плагин для канала exteraGram Utilities, а также найти нужный плагин.\nВыберите действие в меню, чтобы начать.",
        "en": "<b>👋 Hi!</b>\nThis bot lets you submit or update plugins for the exteraGram Utilities channel and quickly find what you need.\nChoose an option below to get started.",
    },
    "send_plugin": {
        "ru": "Пришлите .plugin файл. После загрузки бот автоматически заполнит основные поля.",
        "en": "Send a .plugin file. After uploading, the bot will fill in the basic fields.",
    },
    "update_placeholder": {
        "ru": "Функция обновления скоро будет доступна.",
        "en": "The update feature will be available soon.",
    },
    "menu_unknown": {
        "ru": "Пожалуйста, используйте кнопки ниже.",
        "en": "Please use the buttons below.",
    },
    "invalid_extension": {
        "ru": "Нужно прислать файл с расширением .plugin",
        "en": "Please send a file with the .plugin extension.",
    },
    "file_uploaded": {
        "ru": "Файл загружен: {filename}",
        "en": "File uploaded: {filename}",
    },
    "download_error": {
        "ru": "Не удалось скачать файл. Повторите попытку.",
        "en": "Could not download the file. Please try again.",
    },
    "parse_error": {
        "ru": "Не удалось обработать файл: {error}",
        "en": "Failed to parse the file: {error}",
    },
    "ask_usage": {
        "ru": "Нашёл плагин {name} от {author}. Теперь опиши использование (например, .сmd [текст]).",
        "en": "Found plugin {name} by {author}. Now describe how to use it (for example, .cmd [text]).",
    },
    "waiting_file_fallback": {
        "ru": "Пришлите файл с расширением .plugin",
        "en": "Please send a file with the .plugin extension.",
    },
    "usage_required": {
        "ru": "Нужен текст для поля \"Использование\".",
        "en": "Usage field can't be empty.",
    },
    "ask_author_channel": {
        "ru": "Укажи канал или контакт автора.",
        "en": "Provide the author's channel or contact.",
    },
    "channel_required": {
        "ru": "Нужен текстовый ответ для канала автора.",
        "en": "Please send the author's channel as text.",
    },
    "ask_category": {
        "ru": "Выбери категорию для плагина.",
        "en": "Choose a category for the plugin.",
    },
    "category_unknown": {
        "ru": "Неизвестная категория",
        "en": "Unknown category",
    },
    "category_selected": {
        "ru": "Категория выбрана",
        "en": "Category selected",
    },
    "confirm_missing_text": {
        "ru": "Используйте кнопки \"Подтвердить\" или \"Отмена\".",
        "en": "Use the \"Confirm\" or \"Cancel\" buttons.",
    },
    "submission_saved": {
        "ru": "Заявка отправлена на модерацию. Мы сообщим, когда всё будет готово.",
        "en": "Your submission was sent for moderation. We'll notify you when it's reviewed.",
    },
    "action_cancelled": {
        "ru": "Действие отменено.",
        "en": "Action cancelled.",
    },
    "confirm_invalid": {
        "ru": "Ответьте кнопками ниже.",
        "en": "Please use the buttons below.",
    },
    "revision_message": {
        "ru": "Ваша заявка была возвращена на доработку.\nКомментарий модератора: {comment}\n\nОтправьте исправления заново через бота.",
        "en": "Your submission was sent back for revisions.\nModerator comment: {comment}\n\nPlease submit the updated version via the bot again.",
    },
    "profile_title": {
        "ru": "Ваш профиль",
        "en": "Your profile",
    },
    "profile_pending": {
        "ru": "Заявки в обработке",
        "en": "Pending requests",
    },
    "profile_published": {
        "ru": "Опубликованные плагины",
        "en": "Published plugins",
    },
    "profile_empty": {
        "ru": "Пока нет ни одной заявки.",
        "en": "No submissions yet.",
    },
    "profile_catalog": {
        "ru": "Найдено в каталоге",
        "en": "Found in catalog",
    },
    "profile_page_label": {
        "ru": "Страница {current} из {total}",
        "en": "Page {current} of {total}",
    },
    "draft_plugin_file": {
        "ru": "📎 Файл плагина",
        "en": "📎 Plugin file",
    },
    "draft_editor_title": {
        "ru": "✏️ Проверь черновик и заполни поля",
        "en": "✏️ Review the draft and fill all fields",
    },
    "draft_editor_submit_ready": {
        "ru": "✅ Всё заполнено. Можно отправлять.",
        "en": "✅ All fields look good. Ready to submit.",
    },
    "draft_editor_submit_blocked": {
        "ru": "⚠️ Заполни обязательные поля: {fields}",
        "en": "⚠️ Please fill the required fields: {fields}",
    },
    "draft_field_updated": {
        "ru": "Поле обновлено.",
        "en": "Field updated.",
    },
    "draft_field_need_text": {
        "ru": "Нужно текстовое значение.",
        "en": "Text value required.",
    },
    "draft_back_button": {
        "ru": "↩️ К редактору",
        "en": "↩️ Back to editor",
    },
    "draft_prompt_name": {
        "ru": "Пришли новое название плагина.",
        "en": "Send the new plugin name.",
    },
    "draft_prompt_author": {
        "ru": "Пришли имя или ник автора.",
        "en": "Send the author name or nickname.",
    },
    "draft_prompt_author_channel": {
        "ru": "Пришли канал или контакт автора.",
        "en": "Send the author's channel/contact.",
    },
    "draft_prompt_description": {
        "ru": "Пришли новое описание.",
        "en": "Send the new description.",
    },
    "draft_prompt_usage": {
        "ru": "Пришли новый блок \"Использование\".",
        "en": "Send the new Usage block.",
    },
    "draft_prompt_usage_en": {
        "ru": "Пришли английский блок Usage.",
        "en": "Send the English Usage block.",
    },
    "draft_prompt_version": {
        "ru": "Пришли версию плагина.",
        "en": "Send the plugin version.",
    },
    "draft_prompt_min_version": {
        "ru": "Пришли минимальную версию exteraGram.",
        "en": "Send the minimum exteraGram version.",
    },
    "draft_prompt_category": {
        "ru": "Выбери категорию черновика.",
        "en": "Pick a category for the draft.",
    },
    "draft_prompt_file": {
        "ru": "Пришли новый .plugin файл.",
        "en": "Send the new .plugin file.",
    },
    "draft_prompt_name_en": {
        "ru": "Пришли английское название.",
        "en": "Send the English title.",
    },
    "draft_prompt_description_en": {
        "ru": "Пришли английское описание.",
        "en": "Send the English description.",
    },
    "draft_toggle_has_ui_on": {
        "ru": "Настройки включены (✅)",
        "en": "Settings enabled (✅)",
    },
    "draft_toggle_has_ui_off": {
        "ru": "Настройки отключены (❌)",
        "en": "Settings disabled (❌)",
    },
    "draft_submit_locked": {
        "ru": "Сначала заполни обязательные поля.",
        "en": "Please fill the required fields first.",
    },
    "draft_submit_button": {
        "ru": "✅ Отправить на проверку",
        "en": "✅ Submit for review",
    },
    "draft_cancel_button": {
        "ru": "↩️ Отмена",
        "en": "↩️ Cancel",
    },
    "draft_file_storage": {
        "ru": "Файл в канале",
        "en": "File in storage channel",
    },
    "draft_file_local": {
        "ru": "Файл: {name}",
        "en": "File: {name}",
    },
    "draft_field_name": {
        "ru": "Название",
        "en": "Name",
    },
    "draft_field_author": {
        "ru": "Автор",
        "en": "Author",
    },
    "draft_field_author_channel": {
        "ru": "Канал автора",
        "en": "Author channel",
    },
    "draft_field_description": {
        "ru": "Описание",
        "en": "Description",
    },
    "draft_field_usage": {
        "ru": "Использование",
        "en": "Usage",
    },
    "draft_field_version": {
        "ru": "Версия",
        "en": "Version",
    },
    "draft_field_min_version": {
        "ru": "Минимальная версия",
        "en": "Min version",
    },
    "draft_field_has_ui": {
        "ru": "Есть настройки",
        "en": "Has settings",
    },
    "draft_field_category": {
        "ru": "Категория",
        "en": "Category",
    },
    "draft_field_file": {
        "ru": "Файл",
        "en": "File",
    },
    "existing_notice": {
        "ru": "⚠️ Плагин с таким идентификатором уже есть в каталоге: {title}",
        "en": "⚠️ A plugin with this identifier already exists: {title}",
    },
    "edit_menu_title": {
        "ru": "Черновик заявки для обновления",
        "en": "Draft of your updated submission",
    },
    "edit_prompt_file": {
        "ru": "Пришли новый .plugin файл — заменим текущий.",
        "en": "Send a new .plugin file to replace the current one.",
    },
    "edit_prompt_description": {
        "ru": "Отправь новое описание (форматирование сохраняется).",
        "en": "Send the new description (formatting will be preserved).",
    },
    "edit_prompt_usage": {
        "ru": "Отправь обновлённый блок \"Использование\".",
        "en": "Send the updated usage instructions.",
    },
    "edit_prompt_channel": {
        "ru": "Пришли новый канал или контакт автора.",
        "en": "Send the new author channel or contact.",
    },
    "edit_prompt_category": {
        "ru": "Выбери новую категорию для заявки.",
        "en": "Select a new category for the submission.",
    },
    "edit_file_saved": {
        "ru": "Файл обновлён.",
        "en": "File updated.",
    },
    "edit_text_saved": {
        "ru": "Поле обновлено.",
        "en": "Field updated.",
    },
    "edit_saved": {
        "ru": "Обновлённая заявка отправлена на модерацию. Мы сообщим, когда всё проверят.",
        "en": "Your updated submission was sent for moderation. We'll notify you once it's reviewed.",
    },
    "edit_need_text": {
        "ru": "Нужен текстовый ответ.",
        "en": "A text response is required.",
    },
    "catalog_intro": {
        "ru": "📚 Выберите категорию ниже",
        "en": "📚 Pick a category below",
    },
    "catalog_empty": {
        "ru": "Пока ничего нет, но скоро что-нибудь появится ✨",
        "en": "Nothing here yet, but stay tuned ✨",
    },
    "catalog_item_missing": {
        "ru": "Запись не найдена.",
        "en": "Entry not found.",
    },
    "catalog_search_prompt": {
        "ru": "✍️ Введите ключевые слова из названия или описания плагина. Можно прислать несколько слов.",
        "en": "✍️ Type keywords from the plugin name or description. You can send multiple words.",
    },
    "catalog_search_need_text": {
        "ru": "Нужен текстовый запрос.",
        "en": "Please send a text query.",
    },
    "catalog_search_results": {
        "ru": "🔍 Найдено {count} совпадений для «{query}». Выберите плагин ниже.",
        "en": "🔍 Found {count} matches for “{query}”. Pick a plugin below.",
    },
    "catalog_search_empty": {
        "ru": "Ничего не найдено для «{query}». Попробуйте другие слова.",
        "en": "No plugins found for “{query}”. Try different keywords.",
    },
    "icons_list_title": {
        "ru": "🎨 Все иконпаки — страница {current} из {total}",
        "en": "🎨 All icon packs — page {current} of {total}",
    },
    "choose_submission_type": {
        "ru": "Что хотите отправить?",
        "en": "What would you like to submit?",
    },
    "placeholder_icon_pack": {
        "ru": "Отправка паков иконок скоро появится.",
        "en": "Icon pack submissions are coming soon.",
    },
    "placeholder_update_request": {
        "ru": "Редактирование заявок ещё в разработке.",
        "en": "Updating existing requests is still in development.",
    },
    "profile_greeting": {
        "ru": "Привет, @{username}!",
        "en": "Hi, @{username}!",
    },
    "file_save_error": {
        "ru": "Не удалось сохранить файл. Попробуйте снова.",
        "en": "Failed to save the file. Please try again.",
    },
    "edit_update_failed": {
        "ru": "Не удалось обновить заявку. Попробуйте позже.",
        "en": "Failed to update the submission. Please try again later.",
    },
    "request_not_found": {
        "ru": "Заявка не найдена.",
        "en": "Request not found.",
    },
    "admin_plugin_file": {
        "ru": "📎 Файл для проверки",
        "en": "📎 File for review",
    },
    "admin_draft_title": {
        "ru": "👮‍♂️ Заявка автора",
        "en": "👮‍♂️ Submitter draft",
    },
    "admin_edit_button": {
        "ru": "✏️ Редактор",
        "en": "✏️ Edit",
    },
    "admin_approve_button": {
        "ru": "✅ Одобрить",
        "en": "✅ Approve",
    },
    "edit_field_save_failed": {
        "ru": "Не удалось сохранить поле.",
        "en": "Failed to save the field.",
    },
    "submission_sent": {
        "ru": "Отправлено",
        "en": "Sent",
    },
    "submission_cancelled": {
        "ru": "Отменено",
        "en": "Cancelled",
    },
    "admin_access_denied": {
        "ru": "У вас нет доступа к админ-панели.",
        "en": "You don't have access to the admin panel.",
    },
    "admin_panel_opened": {
        "ru": "Админ-панель открыта.",
        "en": "Admin panel opened.",
    },
    "admin_section_unknown": {
        "ru": "Неизвестный раздел",
        "en": "Unknown section",
    },
    "admin_list_empty": {
        "ru": "Список пуст",
        "en": "The list is empty",
    },
    "admin_review_format": {
        "ru": "Используйте формат: review <id> или #id из списка.",
        "en": "Use format: review <id> or #id from the list.",
    },
    "admin_request_not_found": {
        "ru": "Заявка не найдена. Убедитесь, что указали корректный ID.",
        "en": "Request not found. Please check the ID.",
    },
    "admin_back_to_menu": {
        "ru": "Возврат в меню администратора.",
        "en": "Back to the admin menu.",
    },
    "admin_en_description_required": {
        "ru": "Нужен текст для EN-описания.",
        "en": "EN description is required.",
    },
    "admin_en_description_prompt": {
        "ru": "Введите EN-описание для поста.",
        "en": "Enter the EN description for the post.",
    },
    "admin_en_usage_prompt": {
        "ru": "Теперь отправьте EN-usage.",
        "en": "Now send the EN usage.",
    },
    "admin_en_usage_required": {
        "ru": "Нужен текст для EN-usage.",
        "en": "EN usage is required.",
    },
    "admin_checked_prompt": {
        "ru": "Введите строку вида `версия (дата)` для блока Checked on.",
        "en": "Enter a value like `version (data)` for the Checked on block.",
    },
    "admin_checked_required": {
        "ru": "Нужен формат `версия (дата)`.",
        "en": "Use the `version (date)` format.",
    },
    "admin_request_id_missing": {
        "ru": "Не удалось определить заявку. Попробуйте снова.",
        "en": "Could not determine the request. Please try again.",
    },
    "admin_request_update_failed": {
        "ru": "Не удалось обновить заявку.",
        "en": "Failed to update the request.",
    },
    "admin_request_approved": {
        "ru": "Заявка #{request_id} одобрена. Предпросмотр поста:\n\n{preview}",
        "en": "Request #{request_id} approved. Post preview:\n\n{preview}",
    },
    "admin_revision_prompt": {
        "ru": "Опишите причину возврата (сообщение отправится автору).",
        "en": "Describe the reason for returning it (the author will see this).",
    },
    "admin_revision_comment_required": {
        "ru": "Нужно текстовое пояснение.",
        "en": "A text explanation is required.",
    },
    "admin_request_sent_revision": {
        "ru": "Заявка #{request_id} отправлена на доработку.",
        "en": "Request #{request_id} has been sent back for revisions.",
    },
    "admin_fill_en_first": {
        "ru": "Сначала заполните EN-описание, EN-usage и Checked on",
        "en": "Please fill EN description, EN usage, and Checked on first",
    },
    "admin_channel_missing": {
        "ru": "ID канала не настроен",
        "en": "Channel ID is not configured",
    },
    "admin_published": {
        "ru": "Опубликовано",
        "en": "Published",
    },
    "admin_published_message": {
        "ru": "Пост опубликован в канале (msg_id={message_id}).",
        "en": "Post published in the channel (msg_id={message_id}).",
    },
    "admin_publish_error": {
        "ru": "Ошибка публикации: {error}",
        "en": "Publish error: {error}",
    },
    "admin_requests_empty": {
        "ru": "На этой странице заявок нет.",
        "en": "There are no requests on this page.",
    },
    "admin_requests_page_title": {
        "ru": "Заявки ({queue}) — страница {page}",
        "en": "Requests ({queue}) — page {page}",
    },
    "admin_nav_prev": {
        "ru": "⬅️ Назад",
        "en": "⬅️ Back",
    },
    "admin_nav_next": {
        "ru": "➡️ Вперед",
        "en": "➡️ Next",
    },
    "admin_new_request_notice": {
        "ru": "📥 Новая заявка: #{request_id} — {title}\nАвтор: {author}\nИспользование: {usage}",
        "en": "📥 New submission: #{request_id} — {title}\nAuthor: {author}\nUsage: {usage}",
    },
    "profile_no_items": {
        "ru": "Заявок пока нет.",
        "en": "No submissions yet.",
    },
    "profile_section_plugins": {
        "ru": "Мои плагины",
        "en": "My plugins",
    },
    "profile_section_icon_packs": {
        "ru": "Мои паки иконок",
        "en": "My icon packs",
    },
    "status_pending": {
        "ru": "Ждёт проверки",
        "en": "Pending review",
    },
    "status_approved": {
        "ru": "Одобрено",
        "en": "Approved",
    },
    "status_published": {
        "ru": "Опубликовано",
        "en": "Published",
    },
    "status_needs_revision": {
        "ru": "Нужна доработка",
        "en": "Needs revision",
    },
}

PROFILE_PAGE_SIZE = 5
ADMIN_PAGE_SIZE = 5
CATALOG_PAGE_SIZE = 5
QUEUE_CONFIG = {
    "new": {"title": "Новые заявки", "request_type": "new", "badge": "🆕", "status": "pending"},
    "update": {"title": "Обновления", "request_type": "update", "badge": "♻️", "status": "pending"},
}


def translate(key: str, language: str, **kwargs: Any) -> str:
    templates = TEXTS.get(key, {})
    template = templates.get(language) or templates.get(DEFAULT_LANGUAGE) or ""
    return template.format(**kwargs)


def _build_welcome_caption(language: str) -> str:
    return translate("welcome", language)


def _get_welcome_photo() -> Optional[FSInputFile]:
    if WELCOME_IMAGE_PATH.exists():
        return FSInputFile(WELCOME_IMAGE_PATH)
    return None


HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html_tags(text: str) -> str:
    return HTML_TAG_RE.sub("", text)


def _get_photo_input(path: Path | None) -> Optional[FSInputFile]:
    if path and path.exists():
        return FSInputFile(path)
    return None


async def _send_rich_media(
    target: Message | CallbackQuery,
    photo_path: Path | None,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if isinstance(target, CallbackQuery) and target.message:
        message = target.message
        try:
            if photo_path:
                media = InputMediaPhoto(
                    media=FSInputFile(photo_path),
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
                await message.edit_media(media=media, reply_markup=reply_markup)
            elif message.photo:
                await message.edit_caption(caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
            else:
                await message.edit_text(caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
            logging.warning("Failed to edit message, sending a new one: %s", exc)

        chat_id = message.chat.id
        photo = _get_photo_input(photo_path)
        if photo:
            await target.bot.send_photo(
                chat_id,
                photo=photo,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        else:
            await target.bot.send_message(
                chat_id,
                caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        return

    photo = _get_photo_input(photo_path)
    if photo:
        await target.answer_photo(
            photo,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    else:
        await target.answer(caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def _send_plugin_file(
    target: Message | CallbackQuery,
    plugin: Dict[str, Any],
    caption: str,
) -> None:
    storage_meta = plugin.get("storage") or {}
    target_chat_id = _resolve_target_chat_id(target)
    bot = getattr(target, "bot", None)
    if bot is None:
        logging.warning("Bot instance not available to send plugin file")
        return

    if storage_meta and target_chat_id:
        chat_id = storage_meta.get("chat_id")
        message_id = storage_meta.get("message_id")
        if chat_id and message_id:
            try:
                await bot.copy_message(
                    target_chat_id,
                    chat_id,
                    message_id,
                    caption=caption,
                    parse_mode="HTML",
                )
                return
            except Exception as exc:
                logging.warning("Failed to copy stored plugin file: %s", exc)

    file_path = plugin.get("file_path")
    if not file_path:
        return
    path = Path(file_path)
    if not path.exists():
        logging.warning("Plugin file %s not found on disk", file_path)
        return
    document = FSInputFile(path)
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer_document(document, caption=caption, parse_mode=ParseMode.HTML)
        elif target.from_user:
            await bot.send_document(
                target.from_user.id,
                document=document,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
    else:
        await target.answer_document(document, caption=caption, parse_mode=ParseMode.HTML)


def _resolve_target_chat_id(target: Message | CallbackQuery) -> Optional[int]:
    if isinstance(target, CallbackQuery):
        if target.message:
            return target.message.chat.id
        if target.from_user:
            return target.from_user.id
    else:
        return target.chat.id
    return None


async def send_welcome_bundle(target: Message | CallbackQuery, language: str) -> None:
    caption = _build_welcome_caption(language)
    markup = main_menu_keyboard(language)
    await _send_rich_media(target, WELCOME_IMAGE_PATH, caption, markup)


async def send_welcome_bundle_to_chat(bot: Bot, chat_id: int, language: str) -> None:
    caption = _build_welcome_caption(language)
    markup = main_menu_keyboard(language)
    photo = _get_welcome_photo()
    if photo:
        await bot.send_photo(chat_id, photo=photo, caption=caption, parse_mode="HTML", reply_markup=markup)
    else:
        await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=markup)


def get_user_language_or_default(user_id: Optional[int]) -> str:
    if not user_id:
        return DEFAULT_LANGUAGE
    stored = get_user_language(user_id)
    if stored in LANGUAGE_OPTIONS:
        return stored
    return DEFAULT_LANGUAGE


async def ensure_state_language(state: FSMContext, user_id: Optional[int]) -> str:
    data = await state.get_data()
    language = data.get("language")
    if language in LANGUAGE_OPTIONS:
        return language
    language = get_user_language_or_default(user_id)
    await state.update_data(language=language)
    return language


async def get_language_for_message(message: Message, state: FSMContext) -> str:
    user_id = message.from_user.id if message.from_user else None
    return await ensure_state_language(state, user_id)


async def get_language_for_callback(callback: CallbackQuery, state: FSMContext) -> str:
    user_id = callback.from_user.id if callback.from_user else None
    return await ensure_state_language(state, user_id)


def format_status_label(status: str, language: str) -> str:
    key = f"status_{status}"
    if key in TEXTS:
        return translate(key, language)
    return status


async def show_admin_menu_prompt(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminReview.menu)
    user_id = target.from_user.id if isinstance(target, CallbackQuery) and target.from_user else (target.from_user.id if isinstance(target, Message) and target.from_user else None)
    caption = translate("admin_menu_prompt", await ensure_state_language(state, user_id))
    markup = admin_menu_inline()
    await _send_rich_media(target, PROFILE_IMAGE_PATH, caption, markup)


async def show_admin_queue(
    target: Message | CallbackQuery,
    state: FSMContext,
    queue_type: str,
    page: int = 0,
) -> None:
    await state.set_state(AdminReview.menu)
    requests = get_queue_requests(queue_type)
    per_page = 5
    total = len(requests)
    start = page * per_page
    end = start + per_page
    slice_items = requests[start:end]
    if isinstance(target, CallbackQuery):
        user_id = target.from_user.id if target.from_user else None
    else:
        user_id = target.from_user.id if target.from_user else None
    language = await ensure_state_language(state, user_id)

    if not slice_items:
        await _send_rich_media(
            target,
            PROFILE_IMAGE_PATH,
            translate("admin_list_empty", language),
            admin_menu_inline(),
        )
        return

    labels = [
        (format_user_request_label(entry, language), entry["id"])
        for entry in slice_items
    ]
    has_prev = start > 0
    has_next = end < total
    markup = admin_queue_keyboard(queue_type, labels, page, has_prev, has_next)
    queue_config = QUEUE_CONFIG.get(queue_type, {})
    queue_title = queue_config.get("title", queue_type)
    caption = translate("admin_requests_page_title", language, queue=queue_title, page=page + 1)
    await _send_rich_media(target, PROFILE_IMAGE_PATH, caption, markup)


def get_queue_requests(queue_type: str) -> list[Dict[str, Any]]:
    config = QUEUE_CONFIG.get(queue_type)
    if not config:
        return []
    status = config.get("status")
    request_type = config.get("type")
    return [
        req
        for req in get_requests(status=status)
        if not request_type or req.get("type") == request_type
    ]


def get_bot_token() -> str:
    return os.environ.get("BOT_TOKEN") or CONFIG.get("bot_token", "")


def get_category_by_key(key: Optional[str]) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    return next((c for c in CATEGORY_OPTIONS if c.get("key") == key), None)


def get_uploads_dir() -> Path:
    storage_config = CONFIG.get("storage", {})
    dir_path = storage_config.get("attachments_dir", "uploads")
    root = Path(dir_path)
    root.mkdir(parents=True, exist_ok=True)
    return root


async def reset_user_flow(state: FSMContext, language: str) -> None:
    await state.clear()
    await state.update_data(language=language)
    await state.set_state(UserSubmission.choose_action)


def sanitize_filename_component(value: str) -> str:
    allowed = set("-_@.")
    cleaned = [
        ch
        for ch in value
        if ch.isalnum() or ch in allowed
    ]
    result = "".join(cleaned).strip("._")
    return result or "plugin"


def build_plugin_file_path(plugin_id: str) -> Path:
    uploads = get_uploads_dir()
    safe_name = sanitize_filename_component(plugin_id)
    return uploads / f"{safe_name}.plugin"


def ensure_plugin_file_named(plugin_id: str, source_path: Path) -> Path:
    destination = build_plugin_file_path(plugin_id)
    if source_path.resolve() == destination.resolve():
        return destination
    if destination.exists():
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_path.replace(destination)
    return destination


async def store_plugin_file(bot: Bot, plugin_id: str, file_path: Path) -> Optional[Dict[str, Any]]:
    storage_config = CONFIG.get("storage", {})
    channel_id = storage_config.get("file_storage_channel_id")
    if not channel_id:
        logging.warning("file_storage_channel_id is not configured; skipping upload for %s", plugin_id)
        return None

    try:
        document = FSInputFile(file_path)
        caption = f"Plugin: {plugin_id}"
        message = await bot.send_document(channel_id, document=document, caption=caption)
    except Exception as exc:
        logging.error("Failed to upload plugin %s to storage channel: %s", plugin_id, exc)
        return None

    file = message.document
    if not file:
        logging.warning("Storage message for %s has no document", plugin_id)
        return None

    return {
        "file_id": file.file_id,
        "file_unique_id": file.file_unique_id,
        "file_name": file.file_name,
        "mime_type": file.mime_type,
        "file_size": file.file_size,
        "chat_id": channel_id,
        "message_id": message.message_id,
        "stored_at": message.date.isoformat() if hasattr(message, "date") else None,
    }


def get_formatted_text(message: Message) -> str:
    return message.html_text or message.text or ""


def filter_requests_by_kind(requests: list[Dict[str, Any]], kind: str) -> list[Dict[str, Any]]:
    normalized = kind or "plugin"
    result: list[Dict[str, Any]] = []
    for entry in requests:
        payload = entry.get("payload", {})
        submission_type = payload.get("submission_type", "plugin")
        if submission_type == normalized:
            result.append(entry)
    return result


def format_user_request_label(entry: Dict[str, Any], language: str) -> str:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    name = plugin.get("name") or "Без названия"
    version = plugin.get("version")
    status = format_status_label(entry.get("status", "pending"), language)
    version_part = f" v{version}" if version else ""
    return f"{name}{version_part} — {status}"


async def _send_catalog_search_prompt_message(
    callback: CallbackQuery,
    language: str,
    include_retry: bool = False,
) -> None:
    caption = translate("catalog_search_prompt", language)
    keyboard = catalog_search_prompt_keyboard(language, include_retry=include_retry)
    await _send_rich_media(callback, CATALOG_IMAGE_PATH, caption, keyboard)


async def _send_catalog_search_results(
    target: Message | CallbackQuery,
    entries: list[Dict[str, Any]],
    query: str,
    language: str,
) -> None:
    text = (
        translate("catalog_search_results", language, count=len(entries), query=query)
        if entries
        else translate("catalog_search_empty", language, query=query)
    )
    keyboard = (
        catalog_search_results_keyboard(_build_catalog_search_results(entries, language), language)
        if entries
        else catalog_search_prompt_keyboard(language, include_retry=True)
    )
    await _send_rich_media(target, CATALOG_IMAGE_PATH, text, keyboard)


def format_user_request_details(entry: Dict[str, Any], language: str) -> str:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    lines = [
        f"Название: {plugin.get('name', '—')}",
        f"Версия: {plugin.get('version', '—')}",
        f"Статус: {format_status_label(entry.get('status', 'pending'), language)}",
        f"Категория: {payload.get('category_label', '—')}",
        f"Использование: {payload.get('usage', '—')}",
    ]
    return "\n".join(lines)


async def send_profile_menu(
    target: Message | CallbackQuery,
    state: FSMContext,
    language: str,
) -> None:
    user = target.from_user if isinstance(target, Message) else target.from_user
    if not user or not user.id:
        return

    requests = get_user_requests(user.id)
    plugins = filter_requests_by_kind(requests, "plugin")
    icon_packs = filter_requests_by_kind(requests, "icon_pack")

    greeting = translate("profile_greeting", language, username=user.username or "user")
    caption = (
        f"<b>{greeting}</b>\n"
        f"{translate('profile_title', language)}\n\n"
        f"{translate('profile_section_plugins', language)}: <b>{len(plugins)}</b>\n"
        f"{translate('profile_section_icon_packs', language)}: <b>{len(icon_packs)}</b>"
    )
    markup = profile_menu_keyboard(language, len(plugins), len(icon_packs))

    await _send_rich_media(target, PROFILE_IMAGE_PATH, caption, markup)


async def send_profile_list(
    callback: CallbackQuery,
    kind: str,
    page: int,
    language: str,
) -> None:
    user = callback.from_user
    if not user or not user.id:
        await callback.answer("Недоступно", show_alert=True)
        return

    requests = filter_requests_by_kind(get_user_requests(user.id), kind)
    total = len(requests)
    if total == 0:
        title_key = "profile_section_plugins" if kind == "plugin" else "profile_section_icon_packs"
        caption = (
            f"<b>{translate(title_key, language)}</b>\n"
            f"{translate('profile_no_items', language)}"
        )
        await _send_rich_media(callback, PROFILE_IMAGE_PATH, caption, profile_menu_keyboard(language, 0, 0))
        await callback.answer()
        return

    start = page * PROFILE_PAGE_SIZE
    end = start + PROFILE_PAGE_SIZE
    page_items = requests[start:end]
    labels = [
        (format_user_request_label(entry, language), entry["id"])
        for entry in page_items
    ]
    has_prev = start > 0
    has_next = end < total
    keyboard = profile_items_keyboard(kind, labels, page, has_prev, has_next)
    section_key = "profile_section_plugins" if kind == "plugin" else "profile_section_icon_packs"
    title = (
        f"<b>{translate(section_key, language)}</b>\n"
        f"{translate('profile_page_label', language, current=page + 1, total=math.ceil(total / PROFILE_PAGE_SIZE))}"
    )
    await _send_rich_media(callback, PROFILE_IMAGE_PATH, title, keyboard)
    await callback.answer()


async def show_request_details(
    callback: CallbackQuery,
    state: FSMContext,
    request_id: str,
) -> None:
    entry = get_request_by_id(request_id)
    language = await get_language_for_callback(callback, state)
    if not entry:
        await callback.answer(translate("request_not_found", language), show_alert=True)
        return

    await state.set_state(AdminReview.review_item)
    payload = copy.deepcopy(entry.get("payload") or {})
    await state.update_data(current_request_id=request_id, admin_draft=payload)
    await show_admin_draft_editor(callback, state, language, send_file=True)
    details = html.escape(format_request_details(entry))
    await callback.message.answer(
        f"<pre>{details}</pre>",
        parse_mode="HTML",
        reply_markup=review_actions_keyboard(request_id),
    )


async def show_admin_draft_editor(
    target: Message | CallbackQuery,
    state: FSMContext,
    language: str,
    *,
    send_file: bool = False,
) -> None:
    data = await state.get_data()
    payload = data.get("admin_draft") or {}
    caption = build_draft_editor_caption(data, language, context="admin")
    keyboard = build_draft_editor_keyboard(data, language, context="admin")
    await _send_rich_media(target, PLUGINS_IMAGE_PATH, caption, keyboard)
    if send_file:
        plugin = payload.get("plugin", {})
        await _send_plugin_file(
            target,
            plugin,
            f"<b>{translate('admin_plugin_file', language)}</b>",
        )


def _get_localized_block(entry: Dict[str, Any], language: str) -> Dict[str, Any]:
    return (
        entry.get(language)
        or entry.get(DEFAULT_LANGUAGE)
        or entry.get("ru")
        or entry.get("en")
        or {}
    )


AUTHOR_LINE_MARKERS = {
    "ru": ("автор:", "авторы:"),
    "en": ("author:", "authors:"),
}


def _extract_author_from_raw(entry: Dict[str, Any], language: str) -> Optional[str]:
    raw_blocks = entry.get("raw_blocks") or {}
    locales: list[str] = []
    for code in (language, DEFAULT_LANGUAGE, "ru", "en"):
        if code and code not in locales:
            locales.append(code)
    for code in locales:
        block = raw_blocks.get(code)
        if not block:
            continue
        markers = AUTHOR_LINE_MARKERS.get(code, ())
        # if markers missing for locale, reuse RU markers as fallback
        if not markers:
            markers = AUTHOR_LINE_MARKERS.get("ru", ()) + AUTHOR_LINE_MARKERS.get("en", ())
        lines = block.splitlines()
        for marker in markers:
            marker_lower = marker.lower()
            for line in lines:
                stripped = line.strip()
                if stripped.lower().startswith(marker_lower):
                    _, _, value = stripped.partition(":")
                    candidate = value.strip()
                    if candidate:
                        return candidate
    return None


def _get_author_text(entry: Dict[str, Any], language: str) -> str:
    direct_author = entry.get("author") or entry.get("meta", {}).get("author")
    if direct_author:
        return direct_author

    raw_author = _extract_author_from_raw(entry, language)
    if raw_author:
        return raw_author

    authors = entry.get("authors", {}) or {}
    return (
        authors.get(language)
        or authors.get(DEFAULT_LANGUAGE)
        or authors.get("ru")
        or authors.get("en")
        or "—"
    )


def format_catalog_item_label(entry: Dict[str, Any], language: str, include_badges: bool = True, is_icon: bool = False) -> str:
    block = _get_localized_block(entry, language)
    name = block.get("name") or entry.get("slug", "Без названия")
    prefix = "🎨" if is_icon else "🧩"
    label = f"{prefix} {name}" if not name.startswith(prefix) else name
    if include_badges:
        badge = block.get("settings_label") or entry.get("settings", {}).get("label")
        if badge:
            label = f"{label} {badge}"
    return label


def _build_catalog_search_results(entries: list[Dict[str, Any]], language: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for entry in entries:
        slug = entry.get("slug")
        if not slug:
            continue
        label = format_catalog_item_label(entry, language, include_badges=False, is_icon=False)
        results.append((label, f"catalog:item:{slug}"))
    return results


def build_catalog_preview(entry: Dict[str, Any], language: str, inline: bool = False) -> str:
    block = _get_localized_block(entry, language)
    name = block.get("name") or entry.get("slug", "Без названия")
    desc = block.get("description") or "Описание скоро будет"
    author_text = _get_author_text(entry, language)
    link = build_channel_link(entry)

    lines = [
        _format_line("Название", name),
        _format_line("Автор", author_text),
        _format_line("Описание", desc),
    ]
    if link:
        lines.append(_format_line("Пост", link))

    body = "\n".join(lines)
    if inline:
        return body
    return '<blockquote expandable="true">' + body + "</blockquote>"


def get_catalog_categories() -> list[Dict[str, Any]]:
    return [ALL_CATEGORY_OPTION, *CATEGORY_OPTIONS]


def get_catalog_icon_categories() -> list[Dict[str, Any]]:
    return [ALL_CATEGORY_OPTION, *CATEGORY_OPTIONS]


def derive_plugin_category_key(plugin: Dict[str, Any]) -> Optional[str]:
    return (
        plugin.get("category")
        or plugin.get("category_key")
        or plugin.get("meta", {}).get("category")
    )


async def send_catalog_categories(
    target: Message | CallbackQuery,
    language: str,
) -> None:
    keyboard = catalog_categories_keyboard(get_catalog_categories(), language=language)
    caption = f"<b>{translate('catalog_intro', language)}</b>"
    await _send_rich_media(target, CATALOG_IMAGE_PATH, caption, keyboard)
    if isinstance(target, CallbackQuery):
        await target.answer()


async def send_catalog_page(
    callback: CallbackQuery,
    category_key: str,
    page: int,
    language: str,
) -> None:
    if category_key == ALL_CATEGORY_OPTION["key"]:
        category = ALL_CATEGORY_OPTION
    else:
        category = get_category_by_key(category_key)
    if not category:
        await callback.answer(translate("category_unknown", language), show_alert=True)
        return
    plugins = list_plugins_by_category(category_key)
    total = len(plugins)
    if total == 0:
        await callback.answer(translate("catalog_empty", language), show_alert=True)
        return

    start = page * CATALOG_PAGE_SIZE
    end = start + CATALOG_PAGE_SIZE
    slice_items = plugins[start:end]
    labels = [
        (format_catalog_item_label(plugin, language, include_badges=True, is_icon=False), plugin.get("slug", ""))
        for plugin in slice_items
        if plugin.get("slug")
    ]
    has_prev = start > 0
    has_next = end < total
    keyboard = catalog_items_keyboard(
        category_key,
        labels,
        page,
        has_prev,
        has_next,
        prefix="catalog",
        back_callback="menu:catalog",
        nav_mode="category",
    )
    title = f"<b>{category['ru']} / {category['en']}</b>\nСтраница {page + 1}/{math.ceil(total / CATALOG_PAGE_SIZE)}"
    await _send_rich_media(callback, PLUGINS_IMAGE_PATH, title, keyboard)
    await callback.answer()


async def send_icons_page(
    callback: CallbackQuery,
    category_key: str,
    page: int,
    language: str,
) -> None:
    if category_key == ALL_CATEGORY_OPTION["key"]:
        category = ALL_CATEGORY_OPTION
    else:
        category = get_category_by_key(category_key)
    if not category:
        await callback.answer(translate("category_unknown", language), show_alert=True)
        return
    icons = list_icons_by_category(category_key)
    total = len(icons)
    if total == 0:
        await callback.answer(translate("catalog_empty", language), show_alert=True)
        return

    start = page * CATALOG_PAGE_SIZE
    end = start + CATALOG_PAGE_SIZE
    slice_items = icons[start:end]
    labels = [
        (format_catalog_item_label(icon, language, include_badges=True, is_icon=True), icon.get("slug", ""))
        for icon in slice_items
        if icon.get("slug")
    ]
    has_prev = start > 0
    has_next = end < total
    keyboard = catalog_items_keyboard(
        category_key,
        labels,
        page,
        has_prev,
        has_next,
        prefix="icons",
        back_callback="menu:catalog",
        nav_mode="category",
    )
    caption = f"<b>{category['ru']} / {category['en']}</b>\nСтраница {page + 1}/{math.ceil(total / CATALOG_PAGE_SIZE)}"
    await _send_rich_media(callback, ICONPACKS_IMAGE_PATH, caption, keyboard)
    await callback.answer()


async def send_icons_list_page(
    callback: CallbackQuery,
    page: int,
    language: str,
) -> None:
    icons = list_published_icons()
    total = len(icons)
    if total == 0:
        await callback.answer(translate("catalog_empty", language), show_alert=True)
        return

    start = page * CATALOG_PAGE_SIZE
    end = start + CATALOG_PAGE_SIZE
    slice_items = icons[start:end]
    labels = [
        (format_catalog_item_label(icon, language, include_badges=True, is_icon=True), icon.get("slug", ""))
        for icon in slice_items
        if icon.get("slug")
    ]
    has_prev = start > 0
    has_next = end < total
    keyboard = catalog_items_keyboard(
        "_all",
        labels,
        page,
        has_prev,
        has_next,
        prefix="icons",
        back_callback="menu:catalog",
        nav_mode="list",
    )
    total_pages = max(1, math.ceil(total / CATALOG_PAGE_SIZE))
    title = translate("icons_list_title", language, current=page + 1, total=total_pages)
    await _send_rich_media(callback, ICONPACKS_IMAGE_PATH, title, keyboard)
    await callback.answer()


def build_channel_link(plugin: Dict[str, Any]) -> Optional[str]:
    channel_message = plugin.get("channel_message") or {}
    link = channel_message.get("link")
    if link:
        return link
    username = CONFIG.get("channel", {}).get("username")
    message_id = channel_message.get("message_id")
    if username and message_id:
        return f"https://t.me/{username}/{message_id}"
    return None
router = Router()


@router.message(Command("profile"))
async def cmd_profile(message: Message, state: FSMContext) -> None:
    language = await get_language_for_message(message, state)
    await send_profile_menu(message, state, language)


@router.message(Command("catalog"))
async def cmd_catalog(message: Message, state: FSMContext) -> None:
    language = await get_language_for_message(message, state)
    await send_catalog_categories(message, language)


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    logging.info("/admin invoked by %s", user_id)
    if not user_id or user_id not in ADMIN_IDS:
        await message.answer("У вас нет доступа к админ-панели.")
        return

    await show_admin_menu_prompt(message, state)


@router.inline_query()
async def handle_inline_catalog(query: InlineQuery, state: FSMContext) -> None:
    user_id = query.from_user.id if query.from_user else None
    language = await ensure_state_language(state, user_id)
    text = (query.query or "").strip()
    entries = search_plugins(text, limit=10)
    if not entries:
        await query.answer(
            [],
            switch_pm_text=translate("catalog_search_empty", language, query=text or "*"),
            switch_pm_parameter="catalog",
        )
        return

    results: list[InlineQueryResultArticle] = []
    for entry in entries:
        slug = entry.get("slug") or entry.get("id")
        if not slug:
            continue
        title = format_catalog_item_label(entry, language, include_badges=False)
        description = build_catalog_preview(entry, language)
        plain_description = strip_html_tags(description).replace("\n", " ")[:256]
        results.append(
            InlineQueryResultArticle(
                id=slug,
                title=title,
                description=plain_description,
                input_message_content=InputTextMessageContent(
                    message_text=description,
                    parse_mode="HTML",
                ),
            )
        )

    await query.answer(results, cache_time=0, is_personal=True)


def get_bot_token() -> str:
    return os.environ.get("BOT_TOKEN") or CONFIG.get("bot_token", "")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id if message.from_user else None
    stored_language = get_user_language_or_default(user_id)
    has_language = get_user_language(user_id) is not None
    await state.update_data(language=stored_language)
    if has_language:
        await state.set_state(UserSubmission.choose_action)
        await send_welcome_bundle(message, stored_language)
    else:
        await state.set_state(UserSubmission.choose_language)
        await message.answer(LANGUAGE_PROMPT, reply_markup=language_keyboard())


@router.message(Command("lang"))
async def cmd_language(message: Message, state: FSMContext) -> None:
    await state.set_state(UserSubmission.choose_language)
    language = await get_language_for_message(message, state)
    await message.answer(
        translate("language_saved", language)
        + "\n\n"
        + LANGUAGE_PROMPT,
        reply_markup=language_keyboard(),
    )


@router.callback_query(UserSubmission.choose_language, F.data.startswith("lang:"))
async def handle_language_choice(callback: CallbackQuery, state: FSMContext) -> None:
    code = callback.data.split(":", 1)[1]
    if code not in LANGUAGE_OPTIONS:
        await callback.answer("Unknown language", show_alert=True)
        return
    user_id = callback.from_user.id if callback.from_user else None
    if user_id:
        set_user_language(user_id, code)
    await state.update_data(language=code)
    await state.set_state(UserSubmission.choose_action)
    await callback.message.answer(
        translate("language_saved", code),
        reply_markup=main_menu_keyboard(code),
    )
    await send_welcome_bundle(callback.message, code)
    await callback.answer()


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await state.set_state(UserSubmission.choose_action)
    await send_welcome_bundle(callback, language)
    await callback.answer()


@router.callback_query(F.data == "menu:catalog")
async def menu_catalog(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await send_catalog_categories(callback, language)
    await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def menu_profile(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await send_profile_menu(callback, state, language)
    await callback.answer()


@router.callback_query(F.data == "menu:submit")
async def menu_submit(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await state.set_state(UserSubmission.choose_submission_type)
    caption = f"<b>{translate('choose_submission_type', language)}</b>"
    await _send_rich_media(
        callback,
        PLUGINS_IMAGE_PATH,
        caption,
        submission_type_keyboard(language),
    )
    await callback.answer()


@router.callback_query(UserSubmission.choose_submission_type, F.data.startswith("submit:type:"))
async def choose_submission_type(callback: CallbackQuery, state: FSMContext) -> None:
    submit_type = callback.data.split(":")[-1]
    await state.update_data(submission_type=submit_type)
    language = await get_language_for_callback(callback, state)
    if submit_type == "icon_pack":
        await state.set_state(UserSubmission.choose_action)
        await callback.answer(translate("placeholder_icon_pack", language), show_alert=True)
        return

    await state.set_state(UserSubmission.waiting_file)
    await _send_rich_media(callback, PLUGINS_IMAGE_PATH, translate("send_plugin", language), None)
    await callback.answer()


@router.message(UserSubmission.waiting_file, F.document)
async def submission_handle_file(message: Message, state: FSMContext) -> None:
    document = message.document
    language = await get_language_for_message(message, state)
    try:
        plugin_dict = await _ingest_plugin_document(message.bot, document)
    except PluginFileError as exc:
        await message.answer(translate(exc.key, language, **exc.params))
        return
    await state.update_data(plugin=plugin_dict, submission_type="plugin")

    existing = find_plugin_by_slug(plugin_dict.get("id"))
    if existing:
        await message.answer(
            translate(
                "existing_notice",
                language,
                title=existing.get("ru", {}).get("name")
                or existing.get("en", {}).get("name")
                or plugin_dict.get("name", plugin_dict.get("id", "")),
            )
        )

    await state.set_state(UserSubmission.enter_usage)
    await message.answer(
        translate(
            "ask_usage",
            language,
            name=plugin_dict.get("name", "plugin"),
            author=plugin_dict.get("author", "—"),
        )
    )


@router.message(UserSubmission.waiting_file)
async def submission_waiting_file_fallback(message: Message, state: FSMContext) -> None:
    language = await get_language_for_message(message, state)
    await message.answer(translate("waiting_file_fallback", language))


@router.message(UserSubmission.enter_usage)
async def submission_collect_usage(message: Message, state: FSMContext) -> None:
    language = await get_language_for_message(message, state)
    text = get_formatted_text(message).strip()
    if not text:
        await message.answer(translate("usage_required", language))
        return
    await state.update_data(usage=text)
    await state.set_state(UserSubmission.enter_channel)
    await message.answer(translate("ask_author_channel", language))


@router.message(UserSubmission.enter_channel)
async def submission_collect_channel(message: Message, state: FSMContext) -> None:
    language = await get_language_for_message(message, state)
    text = get_formatted_text(message).strip()
    if not text:
        await message.answer(translate("channel_required", language))
        return
    await state.update_data(author_channel=text)
    await state.set_state(UserSubmission.choose_category)
    await message.answer(
        translate("ask_category", language),
        reply_markup=category_keyboard(CATEGORY_OPTIONS, language),
    )


@router.callback_query(UserSubmission.choose_category, F.data.startswith("category:"))
async def submission_choose_category(callback: CallbackQuery, state: FSMContext) -> None:
    category_key = callback.data.split(":", 1)[1]
    language = await get_language_for_callback(callback, state)
    category = get_category_by_key(category_key)
    if not category:
        await callback.answer(translate("category_unknown", language), show_alert=True)
        return

    await state.update_data(
        category_key=category.get("key"),
        category_label=f"{category.get('ru', '')} / {category.get('en', '')}",
    )
    await state.set_state(UserSubmission.draft_editor)
    await show_draft_editor(callback, state, language, send_file=True)
    await callback.answer()


@router.callback_query(UserSubmission.draft_editor, F.data == "draft:submit")
async def submission_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    data = await state.get_data()
    if not draft_is_complete(data):
        missing = ", ".join(get_draft_missing_fields(data, language))
        await callback.answer(translate("draft_submit_locked", language) + f"\n{missing}", show_alert=True)
        await show_draft_editor(callback, state, language)
        return
    user_id = callback.from_user.id if callback.from_user else None
    submission = build_submission_payload(user_id, data)
    entry = add_request(submission, request_type="new")
    await notify_admins_new_request(callback.bot, entry)
    if callback.message:
        await callback.message.edit_reply_markup()
    await _send_rich_media(
        callback,
        PLUGINS_IMAGE_PATH,
        translate("submission_saved", language),
        main_menu_keyboard(language),
    )
    await reset_user_flow(state, language)
    await callback.answer(translate("submission_sent", language))


@router.callback_query(UserSubmission.draft_editor, F.data == "draft:cancel")
async def submission_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await reset_user_flow(state, language)
    await send_welcome_bundle(callback, language)
    await callback.answer(translate("submission_cancelled", language))


@router.callback_query(
    StateFilter(*USER_DRAFT_STATES),
    F.data == "draft:menu",
)
async def draft_menu(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await state.set_state(UserSubmission.draft_editor)
    await show_draft_editor(callback, state, language)
    await callback.answer()


@router.callback_query(UserSubmission.draft_editor, F.data.startswith("draft:field:"))
async def draft_field_select(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, field_id = callback.data.split(":", 2)
    await _handle_draft_field_select(callback, state, field_id, "user")


@router.message(UserSubmission.draft_waiting_value)
async def draft_value_input(message: Message, state: FSMContext) -> None:
    await _handle_draft_value_input(message, state, "user")


@router.message(UserSubmission.draft_waiting_file, F.document)
async def draft_file_input(message: Message, state: FSMContext) -> None:
    await _handle_draft_file_input(message, state, "user")


@router.message(UserSubmission.draft_waiting_file)
async def draft_file_input_fallback(message: Message, state: FSMContext) -> None:
    await _handle_draft_file_fallback(message, state, "user")


@router.callback_query(UserSubmission.draft_choose_category, F.data.startswith("category:"))
async def draft_category_select(callback: CallbackQuery, state: FSMContext) -> None:
    _, category_key = callback.data.split(":", 1)
    language = await get_language_for_callback(callback, state)
    category = get_category_by_key(category_key)
    if not category:
        await callback.answer(translate("category_unknown", language), show_alert=True)
        return
    await _handle_category_select(callback, state, category, "user")


@router.callback_query(
    StateFilter(*ADMIN_DRAFT_STATES),
    F.data == "admin:draft:menu",
)
async def admin_draft_menu(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await state.set_state(AdminReview.review_item)
    await show_admin_draft_editor(callback, state, language)
    await callback.answer()


@router.callback_query(AdminReview.review_item, F.data.startswith("admin:field:"))
async def admin_draft_field_select(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, field_id = callback.data.split(":", 2)
    await _handle_draft_field_select(callback, state, field_id, "admin")


@router.callback_query(AdminReview.review_item, F.data == "admin:submit")
async def admin_draft_submit(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    data = await state.get_data()
    payload = data.get("admin_draft") or {}
    if not admin_payload_is_complete(payload):
        missing = ", ".join(get_admin_missing_fields(payload, language))
        await callback.answer(translate("draft_submit_locked", language) + f"\n{missing}", show_alert=True)
        await show_admin_draft_editor(callback, state, language)
        return
    await callback.answer(translate("draft_editor_submit_ready", language), show_alert=True)


@router.message(AdminReview.draft_waiting_value)
async def admin_draft_value_input(message: Message, state: FSMContext) -> None:
    await _handle_draft_value_input(message, state, "admin")


@router.message(AdminReview.draft_waiting_file, F.document)
async def admin_draft_file_input(message: Message, state: FSMContext) -> None:
    await _handle_draft_file_input(message, state, "admin")


@router.message(AdminReview.draft_waiting_file)
async def admin_draft_file_input_fallback(message: Message, state: FSMContext) -> None:
    await _handle_draft_file_fallback(message, state, "admin")


@router.callback_query(AdminReview.draft_choose_category, F.data.startswith("category:"))
async def admin_draft_category_select(callback: CallbackQuery, state: FSMContext) -> None:
    _, category_key = callback.data.split(":", 1)
    language = await get_language_for_callback(callback, state)
    category = get_category_by_key(category_key)
    if not category:
        await callback.answer(translate("category_unknown", language), show_alert=True)
        return
    await _handle_category_select(callback, state, category, "admin")


@router.callback_query(F.data.startswith("profile:list:"))
async def profile_list_callback(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, kind, page = callback.data.split(":")
    language = await get_language_for_callback(callback, state)
    await send_profile_list(callback, kind, int(page), language)


@router.callback_query(F.data.startswith("profile:page:"))
async def profile_paginate_callback(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, kind, page = callback.data.split(":")
    language = await get_language_for_callback(callback, state)
    await send_profile_list(callback, kind, int(page), language)


@router.callback_query(F.data.startswith("profile:item:"))
async def profile_item_details(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, kind, request_id = callback.data.split(":", 3)
    language = await get_language_for_callback(callback, state)
    entry = get_request_by_id(request_id)
    if not entry:
        await callback.answer("Не найдено", show_alert=True)
        return
    payload = entry.get("payload", {})
    user_id = payload.get("user_id")
    if callback.from_user.id != user_id:
        await callback.answer("Недоступно", show_alert=True)
        return
    text = html.escape(format_user_request_details(entry, language))
    caption = f"<pre>{text}</pre>"
    await _send_rich_media(
        callback,
        PROFILE_IMAGE_PATH,
        caption,
        profile_item_actions_keyboard(language, entry["id"]),
    )
    plugin = payload.get("plugin", {})
    await _send_plugin_file(
        callback,
        plugin,
        f"<b>{translate('draft_plugin_file', language)}</b>",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("profile:update:"))
async def profile_update_placeholder(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await callback.answer(translate("placeholder_update_request", language), show_alert=True)


@router.callback_query(F.data.startswith("catalog:category:"))
async def catalog_category_callback(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, category_key, page = callback.data.split(":")
    language = await get_language_for_callback(callback, state)
    await send_catalog_page(callback, category_key, int(page), language)


@router.callback_query(F.data.startswith("icons:category:"))
async def icons_category_callback(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, category_key, page = callback.data.split(":")
    language = await get_language_for_callback(callback, state)
    await send_icons_page(callback, category_key, int(page), language)


@router.callback_query(F.data.startswith("catalog:item:"))
async def catalog_item_details(callback: CallbackQuery, state: FSMContext) -> None:
    slug = callback.data.split(":", 2)[2]
    language = await get_language_for_callback(callback, state)
    entry = find_plugin_by_slug(slug)
    if not entry:
        await callback.answer(translate("catalog_item_missing", language), show_alert=True)
        return
    preview = build_catalog_preview(entry, language)
    keyboard = catalog_plugin_keyboard(build_channel_link(entry), "menu:catalog")
    await _send_rich_media(callback, PLUGINS_IMAGE_PATH, preview, keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("icons:item:"))
async def catalog_icon_details(callback: CallbackQuery, state: FSMContext) -> None:
    slug = callback.data.split(":", 2)[2]
    language = await get_language_for_callback(callback, state)
    entry = find_icon_by_slug(slug)
    if not entry:
        await callback.answer(translate("catalog_item_missing", language), show_alert=True)
        return
    preview = build_catalog_preview(entry, language)
    keyboard = catalog_plugin_keyboard(build_channel_link(entry), "menu:catalog")
    await _send_rich_media(callback, ICONPACKS_IMAGE_PATH, preview, keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("icons:list:"))
async def icons_list_callback(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, page = callback.data.split(":")
    language = await get_language_for_callback(callback, state)
    await send_icons_list_page(callback, int(page), language)


@router.callback_query(F.data == "catalog:search")
async def catalog_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await state.set_state(UserSubmission.search_waiting_query)
    await _send_catalog_search_prompt_message(callback, language)
    await callback.answer()


@router.callback_query(F.data == "catalog:search:again")
async def catalog_search_again(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await state.set_state(UserSubmission.search_waiting_query)
    await _send_catalog_search_prompt_message(callback, language, include_retry=True)
    await callback.answer()


@router.callback_query(F.data == "catalog:search:cancel")
async def catalog_search_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await state.set_state(UserSubmission.choose_action)
    await send_catalog_categories(callback, language)
    await callback.answer()


@router.message(UserSubmission.search_waiting_query)
async def catalog_search_handle_query(message: Message, state: FSMContext) -> None:
    language = await get_language_for_message(message, state)
    query = get_formatted_text(message).strip()
    if not query:
        await message.answer(translate("catalog_search_need_text", language))
        return
    plugins = search_plugins(query, limit=10)
    await state.set_state(UserSubmission.choose_action)
    await _send_catalog_search_results(message, plugins, query, language)


@router.callback_query(F.data == "admin:menu")
async def admin_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await show_admin_menu_prompt(callback, state)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:list:"))
async def admin_list_callback(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, queue_type, page_str = callback.data.split(":")
    page = int(page_str)
    await show_admin_queue(callback, state, queue_type, page)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:open:"))
async def admin_open_request_inline(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) == 3:
        # Format: admin:open:<request_id>
        request_id = parts[2]
    elif len(parts) >= 4:
        # Format: admin:open:<queue_type>:<request_id>
        request_id = parts[3]
    else:
        await callback.answer("Некорректный идентификатор", show_alert=True)
        return
    await show_request_details(callback, state, request_id)
    await callback.answer()


@router.callback_query(F.data.startswith("revise:"))
async def global_revise(callback: CallbackQuery, state: FSMContext) -> None:
    request_id = callback.data.split(":", 1)[1]
    await state.set_state(AdminReview.review_item)
    await state.update_data(current_request_id=request_id)
    await state.set_state(AdminReview.enter_revision_comment)
    await callback.message.answer("Опишите причину возврата (сообщение отправится автору).")
    await callback.answer()


@router.callback_query(AdminReview.review_item, F.data.startswith("approve:"))
async def admin_approve_request(callback: CallbackQuery, state: FSMContext) -> None:
    request_id = callback.data.split(":", 1)[1]
    entry = get_request_by_id(request_id)
    language = await get_language_for_callback(callback, state)
    if not entry:
        await callback.answer(translate("request_not_found", language), show_alert=True)
        return

    update_request_status(request_id, "approved")
    preview = html.escape(build_publication_preview(entry))
    text = translate("admin_request_approved", language, request_id=request_id[:6], preview=preview)
    await callback.message.answer(text, parse_mode="HTML", reply_markup=publish_actions_keyboard(request_id))
    await callback.answer()


@router.callback_query(AdminReview.review_item, F.data.startswith("revise:"))
async def admin_request_revision(callback: CallbackQuery, state: FSMContext) -> None:
    request_id = callback.data.split(":", 1)[1]
    await state.update_data(current_request_id=request_id)
    await state.set_state(AdminReview.enter_revision_comment)
    await callback.message.answer("Опишите причину возврата (сообщение отправится автору).")
    await callback.answer()
async def admin_submit_revision_comment(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Нужно текстовое пояснение.")
        return

    data = await state.get_data()
    request_id = data.get("current_request_id")
    if not request_id:
        await message.answer("Не удалось определить заявку. Попробуйте снова.")
        await state.set_state(AdminReview.menu)
        return

    comment = message.text.strip()
    entry = update_request_payload(request_id, {"revision_comment": comment})
    update_request_status(request_id, "needs_revision", comment=comment)

    if entry:
        await notify_user_revision(message.bot, entry, comment)
    await message.answer(
        f"Заявка #{request_id[:6]} отправлена на доработку.",
        reply_markup=admin_menu_keyboard(),
    )
    await state.set_state(AdminReview.menu)


@router.callback_query(F.data.startswith("publish:"))
async def admin_publish(callback: CallbackQuery) -> None:
    request_id = callback.data.split(":", 1)[1]
    entry = get_request_by_id(request_id)
    if not entry:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    # Проверяем, что EN-данные заполнены
    payload = entry.get("payload", {})
    if not payload.get("en_description") or not payload.get("en_usage") or not payload.get("checked_on"):
        await callback.answer("Сначала заполните EN-описание, EN-usage и Checked on", show_alert=True)
        return

    preview = build_publication_preview(entry)
    channel_id = CONFIG.get("channel", {}).get("id")
    if not channel_id:
        await callback.answer("ID канала не настроен", show_alert=True)
        return

    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    file_path = plugin.get("file_path")
    document = None
    if file_path and Path(file_path).exists():
        document = FSInputFile(file_path)

    try:
        if document:
            msg = await callback.bot.send_document(
                channel_id,
                document=document,
                caption=preview,
                parse_mode="HTML",
            )
        else:
            msg = await callback.bot.send_message(
                channel_id,
                preview,
                parse_mode="HTML",
            )
        update_request_payload(
            request_id,
            {
                "published_message_id": msg.message_id,
                "published_chat_id": channel_id,
                "published_at": msg.date.isoformat(),
            },
        )
        update_request_status(request_id, "published")
        if file_path:
            try:
                Path(file_path).unlink(missing_ok=True)
            except Exception:
                logging.warning("Failed to delete file %s after publish", file_path)
        await callback.answer("Опубликовано")
        await callback.message.edit_text(
            f"Пост опубликован в канале (msg_id={msg.message_id}).",
            reply_markup=None,
        )
    except Exception as e:
        logging.error(f"Publish error: {e}")
        await callback.answer(f"Ошибка публикации: {e}", show_alert=True)


async def notify_admins_new_request(bot: Bot, entry: Dict[str, Any]) -> None:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    text = (
        f"📥 Новая заявка: #{entry['id'][:6]} — {plugin.get('name', 'Без названия')}\n"
        f"Автор: {plugin.get('author', '—')}\n"
        f"Использование: {payload.get('usage', '—')}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logging.warning("Failed to notify admin %s about new request", admin_id)


async def show_requests_page(message: Message, state: FSMContext, req_type: str) -> None:
    data = await state.get_data()
    pending = data.get(f"pending_{req_type}", [])
    page = data.get(f"page_{req_type}", 0)
    per_page = 5
    start = page * per_page
    end = start + per_page
    page_items = pending[start:end]

    if not page_items:
        await message.answer("На этой странице заявок нет.")
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = []
    for req in page_items:
        plugin = req["payload"]["plugin"]
        btn_text = f"{plugin.get('name', 'Без названия')} (#{req['id'][:6]})"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"admin:open:{req_type}:{req['id']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page:{req_type}:{page-1}"))
    if end < len(pending):
        nav.append(InlineKeyboardButton(text="➡️ Вперед", callback_data=f"page:{req_type}:{page+1}"))
    if nav:
        buttons.append(nav)

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(f"Заявки ({req_type}) — страница {page+1}:", reply_markup=kb)


@router.callback_query(F.data.startswith("page:"))
async def paginate_requests(callback: CallbackQuery, state: FSMContext) -> None:
    _, req_type, page_str = callback.data.split(":")
    page = int(page_str)
    await state.update_data(**{f"page_{req_type}": page})
    await show_requests_page(callback.message, state, req_type)
    await callback.answer()


@router.callback_query(F.data.startswith("open:"))
async def open_request(callback: CallbackQuery, state: FSMContext) -> None:
    request_id = callback.data.split(":", 1)[1]
    entry = get_request_by_id(request_id)
    if not entry:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    await state.set_state(AdminReview.review_item)
    await state.update_data(current_request_id=request_id)
    await callback.message.answer(
        format_request_details(entry),
        reply_markup=review_actions_keyboard(request_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("icon_pack:"))
async def icon_pack_submission(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Заявка на иконку принята", show_alert=True)


async def download_file(bot: Bot, file_id: str) -> Path:
    try:
        file = await bot.get_file(file_id)
        logging.info(f"File info: {file}")
        
        root = Path("uploads")
        root.mkdir(exist_ok=True)
        
        # Используем file_name из document если доступно, иначе из file_path
        filename = Path(file.file_path).name if file.file_path else f"plugin_{file_id}.plugin"
        destination = root / filename
        
        logging.info(f"Downloading to: {destination}")
        await bot.download_file(file.file_path, destination)
        
        if not destination.exists():
            raise FileNotFoundError(f"File not saved to {destination}")
            
        logging.info(f"File downloaded successfully: {destination}")
        return destination
    except Exception as e:
        logging.error(f"Download error: {e}")
        raise


def metadata_to_dict(metadata, file_path: Path) -> Dict[str, Optional[str]]:
    return {
        "id": metadata.id,
        "name": metadata.name,
        "description": metadata.description,
        "author": metadata.author,
        "version": metadata.version,
        "min_version": metadata.min_version,
        "has_ui_settings": metadata.has_ui_settings,
        "file_path": str(file_path),
    }


def format_submission_preview(data: Dict[str, Any]) -> str:
    plugin = data.get("plugin", {})
    usage = data.get("usage", "—")
    author_channel = data.get("author_channel", plugin.get("author"))
    category_label = data.get("category_label", "Не выбрана")
    has_ui = plugin.get("has_ui_settings", False)

    return (
        "Черновик заявки:\n"
        f"Название: {plugin.get('name', '—')}\n"
        f"Автор: {plugin.get('author', '—')}\n"
        f"Канал автора: {author_channel}\n"
        f"Описание: {plugin.get('description', '—')}\n"
        f"Использование: {usage}\n"
        f"Настройки: {'✅' if has_ui else '❌'}\n"
        f"Минимальная версия: {plugin.get('min_version', '—')}\n"
        f"Категория: {category_label}\n"
    )


def get_request_title(entry: Dict[str, Any]) -> str:
    plugin = entry.get("payload", {}).get("plugin", {})
    name = plugin.get("name")
    version = plugin.get("version")
    if name and version:
        return f"{name} v{version}"
    return name or "Без названия"


def format_request_details(entry: Dict[str, Any]) -> str:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    lines = [
        f"Плагин: {get_request_title(entry)}",
        f"Тип: {entry.get('type')}",
        f"Статус: {entry.get('status')}",
        f"Автор: {plugin.get('author', '—')}",
        f"Канал автора: {payload.get('author_channel', '—')}",
        f"Описание: {plugin.get('description', '—')}",
        f"Использование: {payload.get('usage', '—')}",
        f"Категория: {payload.get('category_label', '—')}",
        f"Настройки: {'✅' if plugin.get('has_ui_settings') else '❌'}",
    ]
    return "\n".join(lines)


def _format_line(label: str, value: Optional[str]) -> str:
    safe_label = html.escape(label)
    safe_value = html.escape(value or "—")
    return f"<b>{safe_label}:</b> {safe_value}"


def build_publication_preview(entry: Dict[str, Any]) -> str:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    category_key = payload.get("category_key")
    category = next((c for c in CATEGORY_OPTIONS if c.get("key") == category_key), None)
    tags = CONFIG.get("channel", {}).get("default_tags", []).copy()
    if category:
        if category.get("tag_ru"):
            tags.append(category["tag_ru"])
        if category.get("tag_en"):
            tags.append(category["tag_en"])
    tags_str = " | ".join(filter(None, tags))

    has_ui = plugin.get("has_ui_settings")
    settings_icon = "✅" if has_ui else "❌"
    checked_on = payload.get("checked_on", "—")

    ru_lines = [
        _format_line("Название:", plugin.get("name")),
        _format_line("Автор:", plugin.get("author")),
        _format_line("Канал автора:", payload.get("author_channel")),
        _format_line("Описание:", plugin.get("description")),
        _format_line("Использование:", payload.get("usage")),
        _format_line("Настройки:", settings_icon),
        _format_line("Минимальная версия:", plugin.get("min_version")),
        _format_line("Проверено на:", checked_on),
    ]

    en_lines = [
        _format_line("Title:", plugin.get("name")),
        _format_line("Author:", plugin.get("author")),
        _format_line("Authors channel:", payload.get("author_channel")),
        _format_line("Description:", payload.get("en_description")),
        _format_line("Usage:", payload.get("en_usage")),
        _format_line("Settings:", settings_icon),
        _format_line("Min.version:", plugin.get("min_version")),
        _format_line("Checked on:", checked_on),
    ]

    ru_block = "🇷🇺 [RU]:\n<blockquote>" + "<br>".join(ru_lines) + "</blockquote>"
    en_block = "🇺🇸 [EN]:\n<blockquote>" + "<br>".join(en_lines) + "</blockquote>"

    return "\n\n".join(filter(None, [ru_block, en_block, html.escape(tags_str)])).strip()


async def notify_user_revision(bot: Bot, entry: Dict[str, Any], comment: str) -> None:
    payload = entry.get("payload", {})
    user_id = payload.get("user_id")
    if not user_id:
        return
    text = (
        "Ваша заявка была возвращена на доработку.\n"
        f"Комментарий модератора: {comment}\n\n"
        "Отправьте исправления, заново пройдя процесс отправки в боте."
    )
    try:
        await bot.send_message(user_id, text)
    except Exception:
        logging.warning("Failed to notify user %s about revision", user_id)


def build_submission_payload(user_id: Optional[int], data: Dict[str, Any]) -> Dict[str, Any]:
    plugin = data.get("plugin", {})
    payload = {
        "user_id": user_id,
        "plugin": plugin,
        "usage": data.get("usage"),
        "author_channel": data.get("author_channel"),
        "category_key": data.get("category_key"),
        "category_label": data.get("category_label"),
        "status": "pending",
    }
    return payload


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = get_bot_token()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set (env or config 'bot_token')")

    bot = Bot(token)
    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
