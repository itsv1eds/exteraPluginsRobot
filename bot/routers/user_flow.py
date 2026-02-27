import asyncio
import html
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from bot.context import get_language, get_lang
from bot.callback_tokens import decode_slug, encode_slug
from bot.helpers import answer, extract_html_text, try_react_pray
from bot.keyboards import (
    cancel_kb,
    comment_skip_kb,
    confirm_kb,
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
from bot.keyboards import admin_review_kb
from bot.cache import get_admins_icons, get_admins_plugins, get_categories
from bot.services.submission import (
    PluginData,
    build_icon_submission_payload,
    build_submission_payload,
    process_icon_file,
    process_plugin_file,
)
from bot.services.publish import build_channel_post, update_plugin
from bot.services.validation import (
    check_duplicate_icon_pending,
    check_duplicate_pending,
    validate_icon_submission,
    validate_new_submission,
    validate_update_submission,
)
from bot.states import UserFlow
from bot.texts import t
from catalog import find_plugin_by_slug, find_user_plugins
from bot.routers.catalog_flow import build_plugin_preview
from bot.keyboards import plugin_detail_kb
from subscription_store import is_subscribed, ALL_SUBSCRIPTION_KEY
from request_store import (
    add_draft_request,
    add_request,
    delete_request_and_file,
    promote_draft_request,
    update_request_payload,
)
from user_store import get_user_language, is_user_banned, set_user_language

router = Router(name="user-flow")
logger = logging.getLogger(__name__)


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
    }
    return {"payload": payload}


def _render_draft_text(data: Dict[str, Any]) -> str:
    plugin = data.get("plugin", {})
    fallback_desc = plugin.get("description", "")
    payload = {
        **_build_draft_entry(data).get("payload", {}),
        "description_ru": data.get("description_ru") or fallback_desc,
        "description_en": data.get("description_en") or fallback_desc,
    }
    return build_channel_post({"payload": payload})


async def _render_home(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await state.update_data(lang=lang)
    await state.set_state(UserFlow.idle)
    await answer(cb, t("welcome", lang), main_menu_kb(lang), "welcome")


async def _render_submit_type(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.set_state(UserFlow.choosing_submission_type)
    await answer(cb, t("choose_type", lang), submit_type_kb(lang), "suggestion")


async def _sync_submission_draft(
    state: FSMContext,
    user_id: int,
    username: str,
    submission_type: str = "plugin",
) -> None:
    data = await state.get_data()
    draft_id = data.get("draft_request_id")

    if submission_type == "icon":
        icon = data.get("icon", {})
        if not icon:
            return
        payload = {
            "user_id": user_id,
            "username": username,
            "icon": icon,
            "submission_type": "icon",
        }
    else:
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
            "submission_type": "plugin",
        }

    if draft_id:
        update_request_payload(draft_id, payload)
    else:
        entry = add_draft_request(payload, request_type="new")
        await state.update_data(draft_request_id=entry.get("id"))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id if message.from_user else None

    payload = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            payload = (parts[1] or "").strip()

    if is_user_banned(user_id):
        lang = get_lang(user_id)
        await message.answer(
            t("user_banned", lang),
            parse_mode=ParseMode.HTML,
        )
        return

    if payload:
        if payload.startswith("plugin_"):
            payload = payload[len("plugin_") :]
        await state.update_data(start_payload=payload)

    if get_user_language(user_id):
        lang = get_lang(user_id)
        await state.update_data(lang=lang)
        await state.set_state(UserFlow.idle)

        if payload:
            plugin = find_plugin_by_slug(payload)
            if plugin:
                text = build_plugin_preview(plugin, lang)
                link = plugin.get("channel_message", {}).get("link")
                notify_all_enabled = is_subscribed(user_id, ALL_SUBSCRIPTION_KEY)
                await answer(
                    message,
                    text,
                    plugin_detail_kb(
                        link,
                        back="catalog",
                        lang=lang,
                        subscribe_callback=(None if notify_all_enabled else f"sub:toggle:{encode_slug(payload)}:catalog"),
                    ),
                    "catalog",
                )
                return

        await answer(message, t("welcome", lang), main_menu_kb(lang), "welcome")
        return

    lang = get_lang(user_id)
    await state.set_state(UserFlow.choosing_language)
    await message.answer(
        t("language_prompt", lang),
        reply_markup=language_kb(),
        parse_mode=ParseMode.HTML,
    )


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
        plugin = find_plugin_by_slug(start_payload)
        if plugin:
            text = build_plugin_preview(plugin, lang)
            link = plugin.get("channel_message", {}).get("link")
            notify_all_enabled = is_subscribed(cb.from_user.id, ALL_SUBSCRIPTION_KEY) if cb.from_user else False
            await answer(
                cb,
                text,
                plugin_detail_kb(
                    link,
                    back="catalog",
                    lang=lang,
                    subscribe_callback=(None if notify_all_enabled else f"sub:toggle:{encode_slug(start_payload)}:catalog"),
                ),
                "catalog",
            )
            await cb.answer(t("language_saved", lang))
            return

    await answer(cb, t("welcome", lang), main_menu_kb(lang), "welcome")
    await cb.answer(t("language_saved", lang))


@router.callback_query(F.data == "home")
async def on_home(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    await state.set_state(UserFlow.idle)
    await answer(cb, t("welcome", lang), main_menu_kb(lang), "welcome")
    await cb.answer()


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
        "uploading_update_file",
        "uploading_icon_file",
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
    await state.set_state(UserFlow.choosing_submission_type)
    await answer(cb, t("choose_type", lang), submit_type_kb(lang), "suggestion")
    await cb.answer()


@router.callback_query(UserFlow.choosing_submission_type, F.data == "submit:plugin")
async def on_submit_plugin(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    await state.set_state(UserFlow.uploading_file)
    await answer(cb, t("upload_plugin", lang), cancel_kb(lang), "plugins")
    await cb.answer()


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
    await cb.answer()


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
    await cb.answer()


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
    await cb.answer()


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
    await cb.answer()


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
    await state.set_state(UserFlow.confirming_update)

    data = await state.get_data()
    plugin = data.get("plugin", {})
    old_version = data.get("old_version", "?")

    text = t(
        "confirm_update",
        lang,
        name=plugin.get("name", "—"),
        old_version=old_version,
        version=plugin.get("version", "—"),
        min_version=plugin.get("min_version", "—"),
        changelog=changelog,
    )
    await answer(message, text, confirm_kb(lang), "plugins")


@router.callback_query(UserFlow.confirming_update, F.data == "confirm")
async def on_confirm_update(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    data = await state.get_data()
    user = cb.from_user

    payload = {
        "user_id": user.id,
        "username": user.username or "",
        "plugin": data.get("plugin", {}),
        "changelog": data.get("changelog", ""),
        "update_slug": data.get("update_slug", ""),
        "old_plugin": data.get("old_plugin", {}),
        "description_ru": data.get("old_plugin", {}).get("ru", {}).get("description", ""),
        "description_en": data.get("old_plugin", {}).get("en", {}).get("description", ""),
        "usage_ru": data.get("old_plugin", {}).get("ru", {}).get("usage", ""),
        "usage_en": data.get("old_plugin", {}).get("en", {}).get("usage", ""),
        "category_key": data.get("old_plugin", {}).get("category", ""),
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


@router.callback_query(UserFlow.choosing_submission_type, F.data == "submit:icons")
async def on_submit_icons(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
    await state.set_state(UserFlow.uploading_icon_file)
    await answer(cb, t("upload_icon", lang), cancel_kb(lang), "iconpacks")
    await cb.answer()


@router.message(UserFlow.uploading_icon_file, F.document)
async def on_icon_file(message: Message, state: FSMContext) -> None:
    if not await _ensure_not_banned(message, state):
        return
    lang = await get_language(message, state)

    if message.document and message.document.file_size:
        if message.document.file_size > 8 * 1024 * 1024:
            await message.answer(t("file_too_large", lang))
            return

    try:
        icon = await process_icon_file(message.bot, message.document)
    except ValueError as e:
        key, _, details = str(e).partition(":")
        if key == "parse_error" and details:
            await message.answer(t("parse_error", lang, error=details))
        else:
            await message.answer(t(key, lang) if key in TEXTS else t("parse_error", lang, error=str(e)))
        return

    await state.update_data(icon=icon.to_dict())

    is_valid, error = validate_icon_submission(icon.to_dict())
    if not is_valid:
        await message.answer(t(error, lang))
        return

    is_duplicate, _ = check_duplicate_icon_pending(icon.id, icon.name)
    if is_duplicate:
        await message.answer(t("icon_pending", lang))
        return
    await _sync_submission_draft(state, message.from_user.id, message.from_user.username or "", "icon")

    preview = t(
        "icon_parsed",
        lang,
        name=icon.name or "—",
        author=icon.author or "—",
        version=icon.version or "—",
        count=icon.count or 0,
    )
    await answer(message, preview, None, None)

    payload = build_icon_submission_payload(message.from_user.id, message.from_user.username or "", icon)
    await state.update_data(
        pending_payload=payload,
        pending_request_type="new",
        pending_reply_key="submission_sent",
    )
    await state.set_state(UserFlow.entering_admin_comment)
    await answer(message, t("ask_admin_comment", lang), comment_skip_kb(lang), "iconpacks")


@router.message(UserFlow.uploading_icon_file)
async def on_icon_file_invalid(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    await message.answer(t("invalid_icon_file", lang))


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

    is_duplicate, _ = check_duplicate_pending(plugin.id, plugin.name)
    if is_duplicate:
        await message.answer(t("plugin_pending", lang))
        return

    await state.update_data(
        plugin=plugin.to_dict(),
        description_raw=plugin.description,
    )
    await _sync_submission_draft(state, message.from_user.id, message.from_user.username or "", "plugin")
    await state.set_state(UserFlow.choosing_description_language)

    draft_text = _render_draft_text(await state.get_data())
    await answer(message, draft_text, image=None)
    await answer(message, t("choose_description_language", lang), description_lang_kb(), None)


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
    await cb.answer()


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
    await cb.answer()


@router.callback_query(UserFlow.confirming_submission, F.data.startswith("draft:edit:"))
async def on_draft_edit(cb: CallbackQuery, state: FSMContext) -> None:
    field = cb.data.split(":")[2]
    lang = await get_language(cb, state)

    if field in {"description", "usage"}:
        await state.update_data(edit_field=field)
        await answer(cb, t("admin_choose_language", lang), draft_lang_kb("draft", field, lang=lang), None)
        await cb.answer()
        return

    if field == "category":
        await state.update_data(edit_field=field)
        await answer(cb, t("admin_choose_category", lang), draft_category_kb("draft", get_categories(), lang=lang), None)
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


@router.callback_query(UserFlow.confirming_submission, F.data.startswith("draft:lang:"))
async def on_draft_language(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, field, lang_choice = cb.data.split(":")
    await state.update_data(edit_field=field, edit_lang=lang_choice)
    await state.set_state(UserFlow.editing_draft_field)

    lang = await get_language(cb, state)
    prompt = t("admin_prompt_enter_text_ru", lang) if lang_choice == "ru" else t("admin_prompt_enter_text_en", lang)

    msg = await answer(cb, prompt, None, None)
    if msg:
        await state.update_data(draft_message_id=msg.message_id)
    await cb.answer()


@router.callback_query(UserFlow.confirming_submission, F.data.startswith("draft:cat:"))
async def on_draft_category(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    cat_key = cb.data.split(":")[2]
    category = next((c for c in get_categories() if c.get("key") == cat_key), None)
    if category:
        await state.update_data(
            category_key=cat_key,
            category_label=f"{category.get('ru', '')} / {category.get('en', '')}",
        )
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
    await cb.answer()


@router.callback_query(UserFlow.confirming_submission, F.data == "draft:back")
async def on_draft_back(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    await state.set_state(UserFlow.confirming_submission)
    draft_text = _render_draft_text(await state.get_data())
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
    await cb.answer()


@router.message(UserFlow.editing_draft_field)
async def on_draft_field_value(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    text = extract_html_text(message).strip()
    if not text:
        await message.answer(
            t("need_text", lang),
            disable_web_page_preview=True,
            parse_mode=ParseMode.HTML,
        )
        return

    data = await state.get_data()
    field = data.get("edit_field")
    edit_lang = data.get("edit_lang")

    if field in {"description", "usage"}:
        key = f"{field}_{edit_lang}"
        await state.update_data({key: text})
    elif field == "name":
        plugin = data.get("plugin", {})
        plugin["name"] = text
        await state.update_data(plugin=plugin)
    elif field == "author":
        plugin = data.get("plugin", {})
        plugin["author"] = text
        await state.update_data(plugin=plugin)
    elif field == "min_version":
        plugin = data.get("plugin", {})
        plugin["min_version"] = text
        await state.update_data(plugin=plugin)
    elif field == "settings":
        value = text.lower()
        has_settings = value in {"да", "yes", "1", "true"}
        plugin = data.get("plugin", {})
        plugin["has_ui_settings"] = has_settings
        await state.update_data(plugin=plugin)

    await state.update_data(edit_field=None, edit_lang=None)
    await _sync_submission_draft(state, message.from_user.id, message.from_user.username or "", "plugin")
    await state.set_state(UserFlow.confirming_submission)

    draft_text = _render_draft_text(await state.get_data())
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
                    "draft",
                    t("btn_send_to_admin", lang),
                    include_cancel=True,
                    include_checked_on=False,
                    lang=lang,
                ),
                disable_web_page_preview=True,
            )
        except Exception:
            await answer(
                message,
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
    else:
        await answer(
            message,
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


@router.callback_query(UserFlow.confirming_submission, F.data == "draft:submit")
async def on_draft_submit(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_not_banned(cb, state):
        return
    lang = await get_language(cb, state)
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

    await state.update_data(
        pending_payload=payload,
        pending_request_type="new",
        pending_reply_key="submission_sent",
    )
    await state.set_state(UserFlow.entering_admin_comment)
    await answer(cb, t("ask_admin_comment", lang), comment_skip_kb(lang), "plugins")
    await cb.answer()


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
    request_type = entry.get("type", "new")
    admin_comment = payload.get("admin_comment")
    submission_type = _submission_type(payload)
    request_id = entry.get("id", "?")

    user_link = f"@{username}" if username else f"<code>{user_id}</code>"
    file_path = plugin.get("file_path") or icon.get("file_path")
    target_admins = get_admins_icons() if submission_type == "icon" else get_admins_plugins()

    logger.info(
        "event=submission.notify_admins.start request_id=%s request_type=%s submission_type=%s item=%s admins=%s",
        request_id,
        request_type,
        submission_type,
        _submission_name(payload, submission_type),
        len(target_admins),
    )

    delivered = 0
    failed = 0
    for admin_id in target_admins:
        admin_lang = get_lang(admin_id)
        if request_type == "update":
            changelog = payload.get("changelog", "—")
            old_plugin = payload.get("old_plugin", {})
            old_locale = old_plugin.get(admin_lang) or old_plugin.get("ru") or {}
            old_version = old_locale.get("version") or "?"
            text = t(
                "admin_request_update",
                admin_lang,
                id=entry["id"],
                name=plugin.get("name", "—"),
                old_version=old_version,
                version=plugin.get("version", "—"),
                min_version=plugin.get("min_version", "—"),
                changelog=changelog,
                user=user_link,
            )
            kb = admin_review_kb(entry["id"], user_id, lang=admin_lang)
        elif request_type == "delete":
            delete_slug = payload.get("delete_slug") or plugin.get("id") or "—"
            text = t(
                "admin_request_delete",
                admin_lang,
                id=entry["id"],
                name=plugin.get("name", "—"),
                slug=delete_slug,
                user=user_link,
            )
            kb = admin_review_kb(
                entry["id"],
                user_id,
                submit_label=t("btn_delete", admin_lang),
                submit_callback=f"adm:delete:{entry['id']}",
                lang=admin_lang,
            )
        elif submission_type == "icon":
            text = t(
                "admin_request_icon",
                admin_lang,
                id=entry["id"],
                name=icon.get("name", "—"),
                author=icon.get("author", "—"),
                version=icon.get("version", "—"),
                count=icon.get("count", 0),
                user=user_link,
            )
            kb = admin_review_kb(entry["id"], user_id, lang=admin_lang)
        else:
            draft_text = build_channel_post(entry)
            text = t(
                "admin_request_plugin",
                admin_lang,
                id=entry["id"],
                draft=draft_text,
                user=user_link,
            )
            kb = admin_review_kb(entry["id"], user_id, lang=admin_lang)

        try:
            if file_path and Path(file_path).exists():
                await bot.send_document(
                    admin_id,
                    FSInputFile(file_path),
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
            else:
                await bot.send_message(
                    admin_id,
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
            if admin_comment:
                await bot.send_message(
                    admin_id,
                    t("admin_request_comment", admin_lang, comment=html.escape(admin_comment)),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            delivered += 1
        except Exception:
            failed += 1
            logger.warning(
                "event=submission.notify_admins.failed request_id=%s admin_id=%s submission_type=%s",
                request_id,
                admin_id,
                submission_type,
                exc_info=True,
            )
        continue

    logger.info(
        "event=submission.notify_admins.done request_id=%s delivered=%s failed=%s",
        request_id,
        delivered,
        failed,
    )
