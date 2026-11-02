import re
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from core.plugin_formats import payload_extension
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

from bot.formatting import join_plain, plain_html, strip_blockquote_tags, telegram_html, visible_html_length
from bot.helpers import blank_and_delete, fit_filename
from bot.icons import emoji_html
from bot.services.channel_text import editing_userbot, send_description, try_edit
from core.storage import flush_documents, load_icons, load_plugins, load_updated, save_icons, save_plugins, save_updated
from core.request_store import update_request_status
from bot.cache import get_categories, invalidate, get_config
from bot import limits
from core.catalog import find_plugin_by_slug, invalidate_catalog_cache, plugin_deeplink_token
from core.categories import categories_for, category_fields
from core.channel_parser import HASHTAG_TO_CATEGORY
from bot.languages import category_labels
from bot.services.submission import prepare_catalog_categories, prepare_submission_categories


def _channel_links_line(bot_username: str, slug: str) -> str:
    bot_username = (bot_username or "").lstrip("@").strip()
    if not bot_username:
        return ""
    token = plugin_deeplink_token(slug)
    open_url = f"https://t.me/{bot_username}?start={token}"
    suggest_url = f"https://t.me/{bot_username}?start=submit"
    return (
        f'<a href="{open_url}">Открыть в боте</a>'
        f' | <a href="{suggest_url}">Предложить плагин</a>'
    )


async def _send_channel_post(
    bot: Bot,
    channel_id: int,
    post_text: str,
    file_path: Optional[str],
    download_name: Optional[str] = None,
    *,
    file_id: Optional[str] = None,
):
    has_file = bool(file_path and Path(file_path).exists())
    if not has_file and not file_id:
        return (await send_description(bot, channel_id, post_text or "—"))[0]

    if not file_id and Path(file_path).stat().st_size > limits.BOT_UPLOAD_BYTES:
        from userbot.client import UserbotClient
        userbot = await UserbotClient.get_instance()
        if not userbot:
            raise ValueError("large_file_transfer_unavailable")
        message = await userbot.send_plugin_file(channel_id, post_text, file_path, download_name or Path(file_path).name)
        return SimpleNamespace(message_id=message.id)

    document = file_id or (FSInputFile(file_path, filename=download_name) if download_name else FSInputFile(file_path))
    overflow = visible_html_length(post_text) > limits.CAPTION
    userbot = await editing_userbot(post_text, caption=True) if overflow else None
    started = time.perf_counter()
    while True:
        try:
            message = await bot.send_document(
                channel_id, document, caption=None if overflow else post_text,
                parse_mode=ParseMode.HTML,
            )
            break
        except TelegramBadRequest as exc:
            from bot.helpers import _is_too_long_error

            if file_id and has_file and any(marker in str(exc).lower() for marker in
                                           ("wrong file", "file_id", "file identifier", "file reference")):
                logger.info("event=publish.cached_file_invalid chat_id=%s", channel_id)
                return await _send_channel_post(bot, channel_id, post_text, file_path, download_name)
            if not _is_too_long_error(exc) or overflow:
                raise
            overflow = True
            userbot = await editing_userbot(post_text, caption=True)
    logger.info("event=publish.document_ready chat_id=%s message_id=%s source=%s duration_ms=%.0f",
                channel_id, message.message_id, "cached" if file_id else "upload", (time.perf_counter() - started) * 1000)
    caption_started = time.perf_counter()
    if overflow and not await try_edit(userbot, channel_id, message.message_id, post_text):
        try:
            await send_description(bot, channel_id, post_text, reply_to_message_id=message.message_id)
        except BaseException:
            await blank_and_delete(bot, channel_id, message.message_id)
            raise
    if overflow:
        logger.info("event=publish.caption_ready chat_id=%s message_id=%s duration_ms=%.0f",
                    channel_id, message.message_id, (time.perf_counter() - caption_started) * 1000)
    return message

logger = logging.getLogger(__name__)


def _cached_publish_file(payload: dict, download_name: str) -> str | None:
    file_id = str(payload.get("moderation_file_id") or "").strip()
    if not file_id:
        return None
    filename = payload.get("moderation_file_name")
    forum = payload.get("moderation_forum_message")
    if not filename and isinstance(forum, dict) and forum.get("file_id") == file_id:
        filename = forum.get("file_name")
    return file_id if filename == download_name else None


def _invalidate_all_plugins() -> None:
    invalidate("plugins")
    invalidate_catalog_cache()


def _invalidate_all_icons() -> None:
    invalidate("icons")
    invalidate_catalog_cache()


def build_channel_post(entry: Dict[str, Any], checked_on: Optional[str] = None) -> str:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})

    category_keys = categories_for(payload)
    categories = [category for category in get_categories() if category.get("key") in category_keys]

    config = get_config()
    channel_cfg = config.get("channel", {})
    tags = [tag for tag in channel_cfg.get("default_tags", []) if str(tag).lower() not in HASHTAG_TO_CATEGORY]

    for category in categories:
        if category.get("tag_ru"):
            tags.append(category["tag_ru"])
        if category.get("tag_en"):
            tags.append(category["tag_en"])

    settings = emoji_html("yes" if plugin.get("has_ui_settings") else "no")
    checked = checked_on or payload.get("checked_on")

    author = plain_html(plugin.get("author", ""))

    desc_fallback = plain_html(plugin.get("description", ""))
    desc_ru = telegram_html(payload.get("description_ru")) or desc_fallback or "—"
    desc_en = telegram_html(payload.get("description_en")) or desc_fallback or "—"
    usage_ru = telegram_html(payload.get("usage_ru")) or "—"
    usage_en = telegram_html(payload.get("usage_en")) or "—"
    min_ver = plain_html(plugin.get("min_version", ""))
    checked_safe = plain_html(checked)

    checked_line = f"<b>Проверено на:</b> {checked_safe}" if checked_safe else ""
    checked_line_en = f"<b>Checked on:</b> {checked_safe}" if checked_safe else ""

    min_version_line = f"<b>Минимальная версия:</b> {min_ver}" if min_ver else ""
    min_version_line_en = f"<b>Min.version:</b> {min_ver}" if min_ver else ""

    ru_lines = [
        f"<b>Название:</b> {plain_html((plugin.get('localized') or {}).get('ru', {}).get('name') or plugin.get('name') or '—')}",
        f"<b>Автор:</b> {author}",
        f"<b>Описание:</b> {desc_ru}",
        f"<b>Использование:</b> {usage_ru}",
        f"<b>Настройки:</b> {settings}",
    ]
    if category_keys:
        ru_lines.append(f"<b>Категории:</b> {plain_html(category_labels(category_keys, 'ru'))}")
    if min_version_line:
        ru_lines.append(min_version_line)
    if checked_line:
        ru_lines.append(checked_line)
    if payload_extension(plugin) != "plugin":
        ru_lines.append(f"<b>Формат:</b> Elyx (.{payload_extension(plugin)})")
        if plugin.get("app_version"):
            ru_lines.append(f"<b>Версия клиента:</b> {plain_html(plugin['app_version'])}")
        if plugin.get("sdk_version"):
            ru_lines.append(f"<b>Версия SDK:</b> {plain_html(plugin['sdk_version'])}")
    ru_block = strip_blockquote_tags("\n".join(ru_lines))

    en_lines = [
        f"<b>Title:</b> {plain_html((plugin.get('localized') or {}).get('en', {}).get('name') or plugin.get('name') or '—')}",
        f"<b>Author:</b> {author}",
        f"<b>Description:</b> {desc_en}",
        f"<b>Usage:</b> {usage_en}",
        f"<b>Settings:</b> {settings}",
    ]
    if category_keys:
        en_lines.append(f"<b>Categories:</b> {plain_html(category_labels(category_keys, 'en'))}")
    if min_version_line_en:
        en_lines.append(min_version_line_en)
    if checked_line_en:
        en_lines.append(checked_line_en)
    if payload_extension(plugin) != "plugin":
        en_lines.append(f"<b>Format:</b> Elyx (.{payload_extension(plugin)})")
        if plugin.get("app_version"):
            en_lines.append(f"<b>Client version:</b> {plain_html(plugin['app_version'])}")
        if plugin.get("sdk_version"):
            en_lines.append(f"<b>SDK version:</b> {plain_html(plugin['sdk_version'])}")
    en_block = strip_blockquote_tags("\n".join(en_lines))

    tags_line = join_plain(list(dict.fromkeys(tags)))

    parts = [
        f"<b>🇷🇺 [RU]:</b>\n<blockquote expandable>{ru_block}</blockquote>",
        f"<b>🇺🇸 [EN]:</b>\n<blockquote expandable>{en_block}</blockquote>",
    ]
    if tags_line:
        parts.append(tags_line)
    return "\n\n".join(parts)


def build_icon_channel_post(entry: Dict[str, Any]) -> str:
    payload = entry.get("payload", {})
    icon = payload.get("icon", {})

    config = get_config()
    channel_cfg = config.get("icons_channel", {})
    tags = list(channel_cfg.get("default_tags", []))

    name = plain_html(icon.get("name", "")) or "—"
    author = plain_html(icon.get("author", "")) or "—"
    version = plain_html(icon.get("version", ""))
    count = plain_html(icon.get("count", 0))

    ru_lines = [
        f"<b>Название:</b> {name}",
        f"<b>Автор:</b> {author}",
    ]
    if version:
        ru_lines.append(f"<b>Версия:</b> {version}")
    ru_lines.append(f"<b>Иконок:</b> {count}")

    en_lines = [
        f"<b>Title:</b> {name}",
        f"<b>Author:</b> {author}",
    ]
    if version:
        en_lines.append(f"<b>Version:</b> {version}")
    en_lines.append(f"<b>Icons:</b> {count}")

    tags_line = join_plain(tags)

    parts = [
        f"<b>🇷🇺 [RU]:</b>\n<blockquote expandable>{chr(10).join(ru_lines)}</blockquote>",
        f"<b>🇺🇸 [EN]:</b>\n<blockquote expandable>{chr(10).join(en_lines)}</blockquote>",
    ]
    if tags_line:
        parts.append(tags_line)
    return "\n\n".join(parts)


async def publish_plugin(
    entry: Dict[str, Any],
    bot: Bot,
    actor: str | None = None,
    actor_id: int | None = None,
) -> Dict[str, Any]:
    payload = entry.get("payload", {})
    preparation_started = time.perf_counter()
    await prepare_submission_categories(payload)
    logger.info("event=publish.prepared request_id=%s duration_ms=%.0f", entry.get("id"),
                (time.perf_counter() - preparation_started) * 1000)
    plugin = payload.get("plugin", {})
    submitter_id = payload.get("user_id")
    submitter_username = payload.get("username", "")

    config = get_config()
    channel_cfg = config.get("channel", {}) or {}
    channel_id = channel_cfg.get("id")
    if not channel_id:
        raise ValueError("channel.id is not configured")
    channel_username = channel_cfg.get("username") or config.get("publish_channel") or ""

    post_text = build_channel_post(entry)
    file_path = plugin.get("file_path")
    slug = make_slug(plugin.get("name") or plugin.get("id"))
    download_name = fit_filename(str(plugin.get('id') or plugin.get('name') or 'plugin'), payload_extension(plugin))

    try:
        me = await bot.me()
        bot_username = me.username or ""
    except Exception:
        bot_username = channel_username
    links_line = _channel_links_line(bot_username, slug)
    if links_line:
        post_text = f"{post_text}\n\n{links_line}"

    cached_file_id = _cached_publish_file(payload, download_name)
    message = await _send_channel_post(bot, channel_id, post_text, file_path, download_name, file_id=cached_file_id)
    catalog_started = time.perf_counter()
    document = getattr(message, "document", None)
    if document and getattr(document, "file_id", None):
        plugin["file_id"] = document.file_id

    update_request_status(entry.get("id"), "published", actor=actor, actor_id=actor_id)

    add_to_catalog(
        entry,
        message.message_id,
        channel_id,
        channel_username,
        submitter_id,
        submitter_username,
    )

    logger.info("event=publish.catalog_updated request_id=%s duration_ms=%.0f", entry.get("id"),
                (time.perf_counter() - catalog_started) * 1000)
    persist_started = time.perf_counter()
    await flush_documents("requests", "plugins")
    logger.info("event=publish.persisted request_id=%s duration_ms=%.0f", entry.get("id"),
                (time.perf_counter() - persist_started) * 1000)

    if file_path:
        Path(file_path).unlink(missing_ok=True)

    link = f"https://t.me/{channel_username}/{message.message_id}" if channel_username else ""
    return {"message_id": message.message_id, "chat_id": channel_id, "link": link}


async def publish_icon(
    entry: Dict[str, Any],
    actor: str | None = None,
    actor_id: int | None = None,
) -> Dict[str, Any]:
    from userbot.client import get_userbot

    userbot = await get_userbot()
    if not userbot:
        raise ValueError("Userbot not available")

    payload = entry.get("payload", {})
    icon = payload.get("icon", {})
    submitter_id = payload.get("user_id")
    submitter_username = payload.get("username", "")

    post_text = build_icon_channel_post(entry)
    file_path = icon.get("file_path")
    download_name = fit_filename(str(icon.get('id') or icon.get('name') or 'icons'), "icons")

    result = await userbot.publish_icon(post_text, file_path, download_name)

    update_request_status(entry.get("id"), "published", actor=actor, actor_id=actor_id)

    config = get_config()
    channel_username = (
        (config.get("icons_channel", {}) or {}).get("username")
        or "exteraIcons"
    )

    add_icon_to_catalog(
        entry,
        result["message_id"],
        result["chat_id"],
        channel_username,
        submitter_id,
        submitter_username,
    )

    await flush_documents("requests", "icons")

    if file_path:
        Path(file_path).unlink(missing_ok=True)

    return result


async def update_plugin(
    entry: Dict[str, Any],
    old_catalog_entry: Dict[str, Any],
    bot: Optional[Bot] = None,
    actor: str | None = None,
    actor_id: int | None = None,
) -> Dict[str, Any]:
    from userbot.client import get_userbot

    userbot = await get_userbot()
    if not userbot:
        raise ValueError("Userbot not available")

    payload = entry.get("payload", {})
    current = find_plugin_by_slug(payload.get("update_slug") or old_catalog_entry.get("slug"))
    expected_id = str((payload.get("plugin") or {}).get("id") or "").strip()
    if current and expected_id and str(current.get("plugin_id") or "").strip() == expected_id:
        old_catalog_entry = current
    if not categories_for(payload):
        payload.update(category_fields(old_catalog_entry))
    await prepare_catalog_categories(payload, old_catalog_entry, userbot)
    plugin = payload.get("plugin", {})

    old_message = old_catalog_entry.get("channel_message", {})
    old_message_id = old_message.get("message_id")

    if not old_message_id:
        raise ValueError("Original message not found")

    new_payload = {
        **payload,
        "usage_ru": payload.get("usage_ru") or old_catalog_entry.get("ru", {}).get("usage", ""),
        "usage_en": payload.get("usage_en") or old_catalog_entry.get("en", {}).get("usage", ""),
        **category_fields(payload),
    }
    entry_copy = {**entry, "payload": new_payload}

    post_text = build_channel_post(entry_copy)
    file_path = plugin.get("file_path")

    footer_slug = (
        old_catalog_entry.get("slug")
        or payload.get("update_slug")
        or make_slug(plugin.get("name") or plugin.get("id"))
    )
    bot_username = ""
    if bot is not None:
        try:
            me = await bot.me()
            bot_username = me.username or ""
        except Exception:
            bot_username = ""
    if not bot_username:
        try:
            from bot.routers.catalog_flow import BOT_USERNAME
            bot_username = BOT_USERNAME
        except Exception:
            bot_username = ""
    links_line = _channel_links_line(bot_username, footer_slug)
    if links_line and "Открыть в боте" not in post_text:
        post_text = f"{post_text}\n\n{links_line}"

    download_name = fit_filename(str(plugin.get('id') or plugin.get('name') or 'plugin'), payload_extension(plugin))
    result = await userbot.update_message(old_message_id, post_text, file_path, download_name)

    update_request_status(entry.get("id"), "published", actor=actor, actor_id=actor_id)
    
    update_catalog_entry(
        old_catalog_entry.get("slug"),
        entry,
        old_message_id,
    )

    slug = old_catalog_entry.get("slug") or payload.get("update_slug") or plugin.get("id")
    name = plugin.get("name") or (old_catalog_entry.get("ru") or {}).get("name") or (old_catalog_entry.get("en") or {}).get("name") or slug
    link = (old_catalog_entry.get("channel_message") or {}).get("link")
    if link:
        add_updated_plugin(name, link)

    await flush_documents("requests", "plugins", "updated")
    
    if file_path:
        Path(file_path).unlink(missing_ok=True)
    
    return result


def add_to_catalog(
    entry: Dict[str, Any],
    message_id: int,
    chat_id: int,
    channel_username: str,
    submitter_id: Optional[int] = None,
    submitter_username: Optional[str] = None,
) -> None:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    
    slug = make_slug(plugin.get("name") or plugin.get("id"))
    author = plugin.get("author", "")
    
    handles = list(set(re.findall(r"@[\w]+", author)))
    
    submitters = []
    if submitter_id:
        submitters.append({"user_id": submitter_id, "username": submitter_username or ""})
    
    catalog_entry = {
        "slug": slug,
        "status": "published",
        "category": (categories_for(payload) or [None])[0],
        "categories": categories_for(payload),
        "authors": {
            "ru": author,
            "en": author,
            "handles": handles,
        },
        "submitters": submitters,
        "ru": {
            "name": (plugin.get("localized") or {}).get("ru", {}).get("name") or plugin.get("name"),
            "description": payload.get("description_ru") or plugin.get("description"),
            "usage": payload.get("usage_ru"),
            "min_version": plugin.get("min_version"),
            "version": plugin.get("version"),
            "settings_label": "✅" if plugin.get("has_ui_settings") else "❌",
        },
        "en": {
            "name": (plugin.get("localized") or {}).get("en", {}).get("name") or plugin.get("name"),
            "description": payload.get("description_en") or plugin.get("description"),
            "usage": payload.get("usage_en"),
            "min_version": plugin.get("min_version"),
            "version": plugin.get("version"),
            "settings_label": "✅" if plugin.get("has_ui_settings") else "❌",
        },
        "settings": {"has_ui": plugin.get("has_ui_settings", False)},
        "requirements": {k: plugin.get(k) for k in ("min_version", "app_version", "sdk_version", "elyx_version", "requirements", "requires")},
        "plugin_id": plugin.get("id"),
        "format": plugin.get("format", "python"),
        "file": {"file_id": plugin.get("file_id"), "file_name": fit_filename(str(plugin.get("id") or "plugin"), payload_extension(plugin)), "file_extension": payload_extension(plugin)},
        "channel_message": {
            "chat_id": chat_id,
            "message_id": message_id,
            "link": f"https://t.me/{channel_username}/{message_id}",
        },
        "published_at": datetime.utcnow().isoformat(),
    }
    
    db = load_plugins()
    plugins = db.setdefault("plugins", [])
    
    idx = next((i for i, p in enumerate(plugins) if p.get("slug") == slug), None)
    if idx is not None:
        old_submitters = plugins[idx].get("submitters", [])
        for sub in old_submitters:
            if sub not in catalog_entry["submitters"]:
                catalog_entry["submitters"].append(sub)
        plugins[idx] = catalog_entry
    else:
        plugins.append(catalog_entry)
    
    save_plugins(db)
    _invalidate_all_plugins()


def add_updated_plugin(name: str, link: str) -> None:
    updated = load_updated()
    items = updated.setdefault("items", [])
    if not link:
        return
    if not isinstance(items, list):
        items = []
        updated["items"] = items

    max_items = 200
    max_age_days = 30
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    pruned: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        added_at = it.get("added_at")
        if isinstance(added_at, str):
            try:
                if datetime.fromisoformat(added_at.replace("Z", "+00:00")).replace(tzinfo=None) < cutoff:
                    continue
            except Exception:
                pass
        pruned.append(it)
    items = pruned[-max_items:]
    updated["items"] = items

    exists = any(isinstance(it, dict) and it.get("link") == link for it in items)
    if exists:
        return
    items.append({"name": name, "link": link, "added_at": datetime.utcnow().isoformat()})
    if len(items) > max_items:
        updated["items"] = items[-max_items:]
    save_updated(updated)


def clear_updated_plugins() -> None:
    updated = load_updated()
    updated["items"] = []
    save_updated(updated)


def seed_updated_plugins() -> int:
    updated = load_updated()
    if updated.get("seeded"):
        return 0

    cutoff = datetime(2026, 1, 27)
    plugins_db = load_plugins()
    plugins = plugins_db.get("plugins", [])
    items: list = []
    for p in plugins:
        if not isinstance(p, dict):
            continue
        updated_at = p.get("updated_at") or p.get("published_at")
        include = False
        if isinstance(updated_at, str):
            try:
                include = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).replace(tzinfo=None) >= cutoff
            except Exception:
                include = False
        checked_ru = (p.get("ru") or {}).get("checked_on")
        checked_en = (p.get("en") or {}).get("checked_on")
        if checked_ru == "12.3.1 (27.01.26)" or checked_en == "12.3.1 (27.01.26)":
            include = True

        if not include:
            continue
        link = (p.get("channel_message", {}) or {}).get("link")
        name = (p.get("ru") or {}).get("name") or (p.get("en") or {}).get("name") or p.get("slug")
        if not link or not name:
            continue
        if any(isinstance(it, dict) and it.get("link") == link for it in items):
            continue
        items.append({"name": name, "link": link, "added_at": datetime.utcnow().isoformat()})

    updated["items"] = items
    updated["seeded"] = True
    if len(items) > 200:
        updated["items"] = items[-200:]
    save_updated(updated)
    return len(items)


def add_icon_to_catalog(
    entry: Dict[str, Any],
    message_id: int,
    chat_id: int,
    channel_username: str,
    submitter_id: Optional[int] = None,
    submitter_username: Optional[str] = None,
) -> None:
    payload = entry.get("payload", {})
    icon = payload.get("icon", {})

    slug = make_slug(icon.get("name") or icon.get("id"))
    author = icon.get("author", "")

    handles = list(set(re.findall(r"@[\w]+", author)))

    submitters = []
    if submitter_id:
        submitters.append({"user_id": submitter_id, "username": submitter_username or ""})

    catalog_entry = {
        "slug": slug,
        "status": "published",
        "category": None,
        "authors": {
            "ru": author,
            "en": author,
            "handles": handles,
        },
        "submitters": submitters,
        "ru": {
            "name": icon.get("name"),
            "description": None,
            "usage": None,
            "min_version": None,
            "version": icon.get("version"),
            "settings_label": "❌",
        },
        "en": {
            "name": icon.get("name"),
            "description": None,
            "usage": None,
            "min_version": None,
            "version": icon.get("version"),
            "settings_label": "❌",
        },
        "settings": {"has_ui": False},
        "requirements": {"min_version": None},
        "channel_message": {
            "chat_id": chat_id,
            "message_id": message_id,
            "link": f"https://t.me/{channel_username}/{message_id}",
        },
        "published_at": datetime.utcnow().isoformat(),
        "file": {
            "file_id": icon.get("file_id"),
            "file_name": Path(icon.get("file_path", "")).name if icon.get("file_path") else None,
        },
    }

    db = load_icons()
    icons = db.setdefault("iconpacks", [])

    idx = next((i for i, p in enumerate(icons) if p.get("slug") == slug), None)
    if idx is not None:
        old_submitters = icons[idx].get("submitters", [])
        for sub in old_submitters:
            if sub not in catalog_entry["submitters"]:
                catalog_entry["submitters"].append(sub)
        icons[idx] = catalog_entry
    else:
        icons.append(catalog_entry)

    save_icons(db)
    _invalidate_all_icons()


def add_submitter_to_iconpack(slug: str, user_id: int, username: str = "") -> bool:
    target = (slug or "").strip().lower()
    if not target:
        return False

    db = load_icons()
    packs = db.get("iconpacks") or []
    if not isinstance(packs, list):
        return False

    for pack in packs:
        if not isinstance(pack, dict):
            continue
        if (pack.get("slug") or "").strip().lower() != target:
            continue

        submitters = pack.setdefault("submitters", [])
        if not isinstance(submitters, list):
            submitters = []
            pack["submitters"] = submitters

        item = {"user_id": int(user_id), "username": username or ""}
        if item not in submitters:
            submitters.append(item)
            save_icons(db)
            _invalidate_all_icons()
        return True

    return False


def update_catalog_entry(slug: str, entry: Dict[str, Any], message_id: int) -> None:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    
    db = load_plugins()
    plugins = db.get("plugins", [])
    
    for p in plugins:
        if p.get("slug") == slug:
            ru_locale = p.setdefault("ru", {})
            en_locale = p.setdefault("en", {})
            requirements = p.setdefault("requirements", {})
            settings = p.setdefault("settings", {})
            authors = p.setdefault("authors", {})

            if categories_for(payload):
                p["categories"] = categories_for(payload)
                p["category"] = p["categories"][0]

            ru_locale["name"] = (plugin.get("localized") or {}).get("ru", {}).get("name") or plugin.get("name") or ru_locale.get("name")
            en_locale["name"] = (plugin.get("localized") or {}).get("en", {}).get("name") or plugin.get("name") or en_locale.get("name")

            author_text = plugin.get("author")
            if author_text:
                authors["ru"] = author_text
                authors["en"] = author_text
                authors["handles"] = list(set(re.findall(r"@[\w]+", author_text)))

            ru_locale["description"] = (
                payload.get("description_ru")
                or plugin.get("description")
                or ru_locale.get("description")
            )
            en_locale["description"] = (
                payload.get("description_en")
                or plugin.get("description")
                or en_locale.get("description")
            )

            ru_locale["usage"] = payload.get("usage_ru") or ru_locale.get("usage")
            en_locale["usage"] = payload.get("usage_en") or en_locale.get("usage")

            ru_locale["version"] = plugin.get("version") or ru_locale.get("version")
            en_locale["version"] = plugin.get("version") or en_locale.get("version")

            ru_locale["min_version"] = plugin.get("min_version") or ru_locale.get("min_version")
            en_locale["min_version"] = plugin.get("min_version") or en_locale.get("min_version")

            settings["has_ui"] = plugin.get("has_ui_settings", settings.get("has_ui"))
            ru_locale["settings_label"] = "✅" if settings["has_ui"] else "❌"
            en_locale["settings_label"] = "✅" if settings["has_ui"] else "❌"

            requirements["min_version"] = plugin.get("min_version") or requirements.get("min_version")
            requirements.update({k: plugin.get(k) for k in ("app_version", "sdk_version", "elyx_version", "requirements", "requires")})
            p["format"] = plugin.get("format", "python")
            p["plugin_id"] = plugin.get("id") or p.get("plugin_id")
            if plugin.get("file_id") or plugin.get("category_file_changed") or Path(plugin.get("file_path") or "").is_file():
                p["file"] = {"file_id": plugin.get("file_id"), "file_name": fit_filename(str(plugin.get("id") or "plugin"), payload_extension(plugin)), "file_extension": payload_extension(plugin)}
            p["updated_at"] = datetime.utcnow().isoformat()
            
            changelog = payload.get("changelog", "")
            if changelog:
                updates = p.setdefault("updates", [])
                updates.append({
                    "version": plugin.get("version"),
                    "changelog": changelog,
                    "date": datetime.utcnow().isoformat(),
                })
            break
    
    save_plugins(db)
    _invalidate_all_plugins()


def add_submitter_to_plugin(slug: str, user_id: int, username: str = "") -> bool:
    db = load_plugins()
    plugins = db.get("plugins", [])
    
    for p in plugins:
        if p.get("slug") == slug:
            submitters = p.setdefault("submitters", [])
            
            for sub in submitters:
                if sub.get("user_id") == user_id:
                    return False

            submitters.append({"user_id": user_id, "username": username})
            save_plugins(db)
            _invalidate_all_plugins()
            return True
    
    return False


def remove_plugin_entry(slug: str) -> bool:
    db = load_plugins()
    plugins = db.get("plugins", [])
    idx = next((i for i, p in enumerate(plugins) if p.get("slug") == slug), None)
    if idx is None:
        return False
    plugins.pop(idx)
    save_plugins(db)
    _invalidate_all_plugins()
    return True


def remove_icon_entry(slug: str) -> bool:
    db = load_icons()
    icons = db.get("iconpacks", [])
    idx = next((i for i, p in enumerate(icons) if p.get("slug") == slug), None)
    if idx is None:
        return False
    icons.pop(idx)
    save_icons(db)
    _invalidate_all_icons()
    return True


async def remove_user_content(user_id: int, username: str = "") -> dict:
    from userbot.client import get_userbot
    from core.catalog import find_user_plugins, find_user_icons

    userbot = await get_userbot()
    removed_plugins: list[str] = []
    removed_icons: list[str] = []
    errors: list[str] = []

    plugins = find_user_plugins(user_id, username)
    for plugin in plugins:
        slug = plugin.get("slug", "")
        message_id = plugin.get("channel_message", {}).get("message_id")
        try:
            if userbot and message_id:
                await userbot.delete_message(message_id)
        except Exception as e:
            errors.append(f"plugin/{slug}: {e}")
        try:
            remove_plugin_entry(slug)
            removed_plugins.append(slug)
        except Exception as e:
            errors.append(f"plugin_db/{slug}: {e}")

    icons = find_user_icons(user_id, username)
    for icon in icons:
        slug = icon.get("slug", "")
        message_id = icon.get("channel_message", {}).get("message_id")
        try:
            if userbot and message_id:
                ub = await get_userbot()
                if ub:
                    entity = await ub.get_icons_publish_entity()
                    await ub.client.delete_messages(entity, message_id)
        except Exception as e:
            errors.append(f"icon/{slug}: {e}")
        try:
            remove_icon_entry(slug)
            removed_icons.append(slug)
        except Exception as e:
            errors.append(f"icon_db/{slug}: {e}")

    return {
        "removed_plugins": removed_plugins,
        "removed_icons": removed_icons,
        "errors": errors,
    }


def make_slug(name: str) -> str:
    slug = (name or "").lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug or "plugin"
