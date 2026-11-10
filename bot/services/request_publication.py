import asyncio
import html
import logging
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.callback_tokens import encode_slug
from bot.context import get_lang
from bot.formatting import plain_html, split_html, strip_blockquote_tags, telegram_html
from bot.helpers import spawn_background
from bot.languages import localized_value
from bot.services.admin_notifications import finalize_admin_notify_messages, is_unreachable_chat
from bot.services.audit import add_audit_event
from bot.services.example_media import schedule_example_media
from bot.services.moderation import delete_forum_request_message, with_author_vote_summary
from bot.services.publish import publish_plugin, remove_plugin_entry, update_plugin
from bot.texts import t
from core.catalog import find_plugin_by_slug
from core.categories import categories_for
from core.request_store import acquire_request_lock, release_request_lock, update_request_status
from core.storage import flush_documents
from core.subscription_store import list_subscribers

logger = logging.getLogger(__name__)


def validate_publication(entry: dict | None) -> list[str]:
    if not isinstance(entry, dict):
        return ["Заявка не найдена"]
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    request_type = str(entry.get("type") or "new")
    submission_type = payload.get("submission_type") or ("icon" if payload.get("icon") else "plugin")
    errors: list[str] = []

    if request_type == "delete":
        delete_slug = str(payload.get("delete_slug") or payload.get("plugin", {}).get("id") or "").strip()
        if not delete_slug:
            errors.append("Не указан slug для удаления")
        elif not find_plugin_by_slug(delete_slug):
            errors.append(f"Плагин для удаления не найден: {delete_slug}")
        return errors

    item = payload.get("icon") if submission_type == "icon" else payload.get("plugin")
    if not isinstance(item, dict):
        errors.append("Нет данных файла в payload")
        return errors

    item_id = str(item.get("id") or "").strip()
    name = str(item.get("name") or "").strip()
    version = str(item.get("version") or "").strip()
    file_path = str(item.get("file_path") or "").strip()
    if not item_id:
        errors.append("Не указан ID/slug")
    if not name:
        errors.append("Не указано название")
    if not version:
        errors.append("Не указана версия")
    if not file_path:
        errors.append("Не указан путь к файлу")
    elif not Path(file_path).is_file():
        errors.append(f"Файл не найден: {file_path}")
    if submission_type != "icon":
        if not (categories_for(payload) or categories_for(payload.get("old_plugin") or {})):
            errors.append("Не указана категория")
    return errors


def plugin_display_name(plugin_entry: Dict[str, Any]) -> str:
    if not isinstance(plugin_entry, dict):
        return "—"
    for key in ("ru", "en"):
        block = plugin_entry.get(key)
        if isinstance(block, dict) and block.get("name"):
            return str(block["name"])
    return str(plugin_entry.get("slug") or "—")


async def notify_plugin_authors_removed(bot, plugin_entry: Dict[str, Any], reason: str = "") -> int:
    if not isinstance(plugin_entry, dict):
        return 0

    from bot.keyboards import author_plugin_removed_kb

    name = plain_html(plugin_display_name(plugin_entry))
    slug = str(plugin_entry.get("slug") or "").strip()
    reason = strip_blockquote_tags(telegram_html(str(reason or ""))).strip()
    submitters = plugin_entry.get("submitters") or []
    seen: set[int] = set()
    notified = 0
    for sub in submitters:
        if not isinstance(sub, dict):
            continue
        uid = sub.get("user_id")
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            continue
        if uid in seen or uid <= 0:
            continue
        seen.add(uid)
        try:
            author_lang = get_lang(uid)
            body = t("notify_plugin_removed_author", author_lang, name=name)
            if reason:
                body += t("notify_plugin_removed_reason", author_lang, reason=reason)
            await bot.send_message(
                uid,
                body,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=author_plugin_removed_kb(slug, author_lang) if slug else None,
            )
            notified += 1
        except Exception:
            logger.exception(
                "event=delete.notify_author_failed user_id=%s slug=%s",
                uid, plugin_entry.get("slug"),
            )
    return notified


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
    localized: dict[str, tuple[list[str], InlineKeyboardMarkup]] = {}

    for user_id in list_subscribers(slug):
        lang = get_lang(user_id)
        if lang not in localized:
            locale = localized_value(entry, lang, {})
            raw_name = plain_html(plugin.get("name") or locale.get("name") or slug)
            link_safe = html.escape(plugin_link, quote=True) if plugin_link else ""
            name = f'<a href="{link_safe}"><b>{raw_name}</b></a>' if plugin_link else f"<b>{raw_name}</b>"
            text = t("notify_subscription_update", lang, name=name,
                     version=plain_html(plugin.get("version") or locale.get("version") or "—"), changelog=changes)
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=t("btn_open", lang), callback_data=f"plugin:{encode_slug(slug)}"),
            ]])
            localized[lang] = (split_html(text, 3500), reply_markup)
        parts, reply_markup = localized[lang]
        try:
            for index, part in enumerate(parts):
                while True:
                    try:
                        await bot.send_message(
                            user_id, part, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                            reply_markup=reply_markup if index + 1 == len(parts) else None,
                        )
                        break
                    except TelegramRetryAfter as exc:
                        await asyncio.sleep(max(1, float(exc.retry_after)))
        except Exception as exc:
            if not is_unreachable_chat(exc):
                logger.warning("event=subscription.update_notification_failed user_id=%s slug=%s", user_id, slug, exc_info=True)


async def _finish_publish_notifications(bot, entry: dict, notify_key: str, actor: str,
                                        removed_plugin: dict | None = None, *, scheduled: bool = False) -> None:
    payload = entry.get("payload", {})
    request_id = str(entry.get("id") or "")

    async def notify_author():
        user_id = payload.get("user_id")
        if not user_id:
            return
        plugin = payload.get("plugin") or payload.get("icon") or {}
        lang = get_lang(user_id)
        text = t(notify_key, lang, name=plain_html(plugin.get("name") or "—"),
                 version=plain_html(plugin.get("version") or "—"))
        if notify_key in {"notify_published", "notify_update_published", "notify_icon_published"}:
            text = with_author_vote_summary(text, entry, lang)
        for part in split_html(text, 3500):
            await bot.send_message(
                user_id, part, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )

    calls = [
        ("admin_notifications", finalize_admin_notify_messages(
            bot, entry, "Заявка была принята по расписанию" if scheduled else "Заявка была принята", actor,
        )),
        ("forum", delete_forum_request_message(bot, entry)),
        ("author", notify_author()),
    ]
    if entry.get("type") == "update":
        calls.append(("subscribers", _notify_subscribers(
            bot, payload.get("update_slug") or payload.get("plugin", {}).get("id"),
            payload.get("plugin", {}), payload.get("changelog"),
        )))
    if removed_plugin:
        calls.append(("removed_authors", notify_plugin_authors_removed(
            bot, removed_plugin, str(payload.get("admin_comment") or ""),
        )))
    started = time.perf_counter()
    results = await asyncio.gather(*(call for _, call in calls), return_exceptions=True)
    for (name, _), result in zip(calls, results):
        if isinstance(result, BaseException):
            logger.error("event=moderation.publish.followup_failed request_id=%s surface=%s", request_id, name,
                         exc_info=(type(result), result, result.__traceback__))
    logger.info("event=moderation.publish.followups_done request_id=%s duration_ms=%.0f", request_id,
                (time.perf_counter() - started) * 1000)


async def remove_published_plugin(slug: str) -> dict:
    from userbot.client import get_userbot

    lock = f"catalog-delete:{slug}"
    if not acquire_request_lock(lock):
        raise ValueError("Plugin removal already in progress")
    try:
        plugin = find_plugin_by_slug(slug)
        if not plugin:
            raise ValueError("Plugin not found")
        message_id = (plugin.get("channel_message") or {}).get("message_id")
        if message_id:
            userbot = await get_userbot()
            if not userbot:
                raise ValueError("Userbot not available")
            await userbot.delete_message(message_id)
        if not remove_plugin_entry(slug):
            raise ValueError("Failed to remove plugin")
        await flush_documents("plugins")
        return plugin
    finally:
        release_request_lock(lock)


async def publish_request(bot, entry: dict, actor: str, actor_id: int | None = None, *, scheduled: bool = False) -> dict:
    request_id = str(entry["id"])
    payload = entry.get("payload") or {}
    request_type = entry.get("type", "new")
    is_icon = payload.get("submission_type") == "icon" or payload.get("icon")
    item = payload.get("icon" if is_icon else "plugin") or {}
    removed_plugin = None
    started = time.perf_counter()
    if request_type == "update":
        result = await update_plugin(entry, payload.get("old_plugin") or {}, bot, actor=actor, actor_id=actor_id)
        notify_key = "notify_update_published"
    elif request_type == "delete":
        removed_plugin = await remove_published_plugin(payload.get("delete_slug") or item.get("id"))
        result = {"link": (removed_plugin.get("channel_message") or {}).get("link", "")}
        update_request_status(request_id, "deleted", actor=actor, actor_id=actor_id)
        await flush_documents("requests")
        notify_key = "notify_deleted"
    elif is_icon:
        raise ValueError("Icon packs are added to the catalog from the super admin panel")
    else:
        result = await publish_plugin(entry, bot, actor=actor, actor_id=actor_id)
        notify_key = "notify_published"

    logger.info("event=moderation.publish.channel_done request_id=%s duration_ms=%.0f",
                request_id, (time.perf_counter() - started) * 1000)
    try:
        add_audit_event(
            "moderation.scheduled_publish_success" if scheduled else "moderation.publish_success",
            actor_id=actor_id, actor=actor, request_id=request_id,
            details={"request_type": request_type, "submission_type": "icon" if is_icon else "plugin",
                     "item": item.get("name") or payload.get("delete_slug"), "link": result.get("link", "")},
        )
    except Exception:
        logger.exception("event=moderation.publish.audit_failed request_id=%s", request_id)
    if request_type in {"new", "update"} and not is_icon and (payload.get("comment_media") or request_type == "update"):
        spawn_background(schedule_example_media(bot, request_id))
    spawn_background(_finish_publish_notifications(bot, deepcopy(entry), notify_key, actor, removed_plugin, scheduled=scheduled))
    return result
