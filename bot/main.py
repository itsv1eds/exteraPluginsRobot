import asyncio
import html
import logging
import math
import os
import random
from pathlib import Path
import copy
from typing import Any, Dict, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from aiogram.types import FSInputFile

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
)
from .states import AdminReview, UserSubmission

CONFIG = load_config()
CATEGORY_OPTIONS = CONFIG.get("categories", [])
ALL_CATEGORY_OPTION = {"key": "_all", "ru": "Все плагины", "en": "All plugins"}
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
    "edit_entry_missing": {
        "ru": "Заявка не найдена.",
        "en": "Submission not found.",
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
    photo = _get_photo_input(photo_path)
    if isinstance(target, CallbackQuery):
        chat_id = None
        if target.message:
            chat_id = target.message.chat.id
        elif target.from_user:
            chat_id = target.from_user.id
        if chat_id is None:
            return
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
    else:
        if photo:
            await target.answer_photo(
                photo,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        else:
            await target.answer(caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def send_welcome_bundle(message: Message, language: str) -> None:
    caption = _build_welcome_caption(language)
    markup = main_menu_keyboard(language)
    photo = _get_welcome_photo()
    if photo:
        await message.answer_photo(photo, caption=caption, parse_mode="HTML", reply_markup=markup)
    else:
        await message.answer(caption, parse_mode="HTML", reply_markup=markup)


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
    text = "Выберите раздел:"
    markup = admin_menu_inline()
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup)


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


async def _delete_callback_message(callback: CallbackQuery) -> bool:
    message = callback.message
    if not message:
        return False
    try:
        await message.delete()
        return True
    except TelegramBadRequest:
        return False


async def _send_new_callback_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    chat_id = None
    if callback.message:
        chat_id = callback.message.chat.id
    elif callback.from_user:
        chat_id = callback.from_user.id
    if chat_id is not None:
        await callback.bot.send_message(chat_id, text, reply_markup=reply_markup)


async def _send_catalog_search_prompt_message(
    callback: CallbackQuery,
    language: str,
    include_retry: bool = False,
) -> None:
    await _send_new_callback_message(
        callback,
        translate("catalog_search_prompt", language),
        catalog_search_prompt_keyboard(language, include_retry=include_retry),
    )


async def _send_catalog_search_results(
    target: Message | CallbackQuery,
    entries: list[Dict[str, Any]],
    query: str,
    language: str,
) -> None:
    if not entries:
        text = translate("catalog_search_empty", language, query=query)
        markup = catalog_search_prompt_keyboard(language, include_retry=True)
        if isinstance(target, CallbackQuery):
            await _send_new_callback_message(target, text, markup)
        else:
            await target.answer(text, reply_markup=markup)
        return

    result_pairs = _build_catalog_search_results(entries, language)
    keyboard = catalog_search_results_keyboard(result_pairs, language)
    text = translate("catalog_search_results", language, count=len(entries), query=query)
    if isinstance(target, CallbackQuery):
        await _send_new_callback_message(target, text, keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


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
    force_new: bool = False,
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

    if isinstance(target, CallbackQuery) and not force_new:
        await _delete_callback_message(target)

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
        await callback.message.answer(
            translate(title_key, language) + "\n" + translate("profile_no_items", language)
        )
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
    title = f"{translate(section_key, language)} — {page + 1}/{math.ceil(total / PROFILE_PAGE_SIZE)}"
    if callback.data.startswith("profile:page:") and callback.message:
        try:
            await callback.message.edit_text(title, reply_markup=keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    else:
        await callback.message.answer(title, reply_markup=keyboard)
    await callback.answer()


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
    force_new: bool = False,
) -> None:
    keyboard = catalog_categories_keyboard(get_catalog_categories(), language=language)
    caption = f"<b>{translate('catalog_intro', language)}</b>\n{translate('catalog_empty', language)}"
    if isinstance(target, CallbackQuery) and not force_new:
        await _delete_callback_message(target)
    await _send_rich_media(target, CATALOG_IMAGE_PATH, caption, keyboard)
    if isinstance(target, CallbackQuery):
        await target.answer()


async def send_catalog_page(
    callback: CallbackQuery,
    category_key: str,
    page: int,
    language: str,
    force_new: bool = False,
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
    if callback.message and not force_new:
        await _delete_callback_message(callback)
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
    if callback.message:
        await _delete_callback_message(callback)
    await _send_rich_media(callback, ICONPACKS_IMAGE_PATH, caption, keyboard)
    await callback.answer()


async def send_icons_list_page(
    callback: CallbackQuery,
    page: int,
    language: str,
    force_new: bool = False,
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
    if callback.message and not force_new:
        await _delete_callback_message(callback)
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


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not user_id or user_id not in ADMIN_IDS:
        await message.answer("У вас нет доступа к админ-панели.")
        return

    await show_admin_menu_prompt(message, state)


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
    await state.set_state(AdminReview.menu)
    await callback.message.answer("Возврат в меню администратора.", reply_markup=admin_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await state.set_state(UserSubmission.choose_action)
    await _delete_callback_message(callback)
    if callback.message:
        await send_welcome_bundle(callback.message, language)
    await callback.answer()


@router.callback_query(F.data == "menu:catalog")
async def menu_catalog(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await _delete_callback_message(callback)
    await send_catalog_categories(callback, language, force_new=True)
    await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def menu_profile(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await _delete_callback_message(callback)
    await send_profile_menu(callback, state, language, force_new=True)
    await callback.answer()


@router.callback_query(F.data == "menu:submit")
async def menu_submit(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await state.set_state(UserSubmission.choose_submission_type)
    await _delete_callback_message(callback)
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
    await _send_new_callback_message(callback, translate("send_plugin", language))
    await callback.answer()


@router.message(UserSubmission.waiting_file, F.document)
async def submission_handle_file(message: Message, state: FSMContext) -> None:
    document = message.document
    language = await get_language_for_message(message, state)
    if not document or not document.file_name.endswith(".plugin"):
        await message.answer(translate("invalid_extension", language))
        return

    try:
        file_path = await download_file(message.bot, document.file_id)
    except Exception:
        logging.exception("Failed to download file")
        await message.answer(translate("download_error", language))
        return

    try:
        metadata = parse_plugin_file(file_path)
    except (FileNotFoundError, PluginParseError) as exc:
        await message.answer(translate("parse_error", language, error=str(exc)))
        return

    try:
        plugin_file = ensure_plugin_file_named(metadata.id, file_path)
    except Exception as exc:
        logging.error("Failed to rename plugin file: %s", exc)
        await message.answer(translate("file_save_error", language))
        return

    plugin_dict = metadata_to_dict(metadata, plugin_file)
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
    await state.set_state(UserSubmission.confirm)
    data = await state.get_data()
    preview = html.escape(format_submission_preview(data))
    caption = f"<b>{translate('category_selected', language)}</b>\n<pre>{preview}</pre>"
    await _send_rich_media(callback, PLUGINS_IMAGE_PATH, caption, confirm_keyboard(language))
    await callback.answer()


@router.callback_query(UserSubmission.confirm, F.data == "submission:confirm")
async def submission_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    data = await state.get_data()
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


@router.callback_query(UserSubmission.confirm, F.data == "submission:cancel")
async def submission_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await reset_user_flow(state, language)
    await _delete_callback_message(callback)
    await send_welcome_bundle(callback.message if callback.message else callback, language)
    await callback.answer(translate("submission_cancelled", language))


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
    await _delete_callback_message(callback)
    await callback.message.answer(preview, parse_mode="HTML", reply_markup=keyboard)
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
    await _delete_callback_message(callback)
    await callback.message.answer(preview, parse_mode="HTML", reply_markup=keyboard)
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
    await _delete_callback_message(callback)
    await _send_catalog_search_prompt_message(callback, language)
    await callback.answer()


@router.callback_query(F.data == "catalog:search:again")
async def catalog_search_again(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await state.set_state(UserSubmission.search_waiting_query)
    await _delete_callback_message(callback)
    await _send_catalog_search_prompt_message(callback, language, include_retry=True)
    await callback.answer()


@router.callback_query(F.data == "catalog:search:cancel")
async def catalog_search_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    language = await get_language_for_callback(callback, state)
    await state.set_state(UserSubmission.choose_action)
    await send_catalog_categories(callback, language, force_new=True)
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


@router.callback_query(F.data.startswith("admin:open:"))
async def admin_open_request_inline(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Некорректный идентификатор", show_alert=True)
        return
    request_id = parts[2]
    await show_request_details(callback.message, state, request_id)
    await callback.answer()


@router.callback_query(F.data.startswith("revise:"))
async def global_revise(callback: CallbackQuery, state: FSMContext) -> None:
    request_id = callback.data.split(":", 1)[1]
    await state.set_state(AdminReview.review_item)
    await state.update_data(current_request_id=request_id)
    await state.set_state(AdminReview.enter_revision_comment)
    await callback.message.answer("Опишите причину возврата (сообщение отправится автору).")
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
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"open:{req['id']}")])

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
