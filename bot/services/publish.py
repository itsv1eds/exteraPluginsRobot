import html
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage import load_plugins, save_plugins
from request_store import update_request_status
from bot.cache import get_categories, invalidate, get_config

logger = logging.getLogger(__name__)


def build_channel_post(entry: Dict[str, Any], checked_on: Optional[str] = None) -> str:
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})

    cat_key = payload.get("category_key")
    categories = get_categories()
    category = next((c for c in categories if c.get("key") == cat_key), None)

    config = get_config()
    channel_cfg = config.get("channel", {})
    tags = list(channel_cfg.get("default_tags", []))

    if category:
        if category.get("tag_ru"):
            tags.append(category["tag_ru"])
        if category.get("tag_en"):
            tags.append(category["tag_en"])

    settings = "✅" if plugin.get("has_ui_settings") else "❌"
    checked = checked_on or payload.get("checked_on")

    author = html.escape(plugin.get("author", ""))

    name = html.escape(plugin.get("name", "")) or "—"
    desc_fallback = plugin.get("description", "")
    desc_ru = html.escape(payload.get("description_ru") or desc_fallback or "") or "—"
    desc_en = html.escape(payload.get("description_en") or desc_fallback or "") or "—"
    usage_ru = html.escape(payload.get("usage_ru") or "—")
    usage_en = html.escape(payload.get("usage_en") or "—")
    min_ver = html.escape(plugin.get("min_version", ""))

    checked_line = f"<b>Проверено на: </b>{checked}" if checked else ""
    checked_line_en = f"<b>Checked on: </b>{checked}" if checked else ""

    min_version_line = f"<b>Минимальная версия: </b>{min_ver}" if min_ver else ""
    min_version_line_en = f"<b>Min.version: </b>{min_ver}" if min_ver else ""

    ru_lines = [
        f"<b>Название:</b> {name}",
        f"<b>Автор: </b>{author}",
        f"<b>Описание: </b>{desc_ru}",
        f"<b>Использование: </b>{usage_ru}",
        f"<b>Настройки: </b>{settings}",
    ]
    if min_version_line:
        ru_lines.append(min_version_line)
    if checked_line:
        ru_lines.append(checked_line)
    ru_block = "\n".join(ru_lines)

    en_lines = [
        f"<b>Title: </b>{name}",
        f"<b>Author: </b>{author}",
        f"<b>Description: </b>{desc_en}",
        f"<b>Usage: </b>{usage_en}",
        f"<b>Settings: </b>{settings}",
    ]
    if min_version_line_en:
        en_lines.append(min_version_line_en)
    if checked_line_en:
        en_lines.append(checked_line_en)
    en_block = "\n".join(en_lines)

    tags_line = " | ".join(tags)

    return (
        f"<b>🇷🇺 [RU]:</b>\n"
        f"<blockquote>\n{ru_block}\n</blockquote>\n\n"
        f"<b>🇺🇸 [EN]:</b>\n"
        f"<blockquote>\n{en_block}\n</blockquote>\n\n"
        f"{tags_line}"
    )


async def publish_plugin(entry: Dict[str, Any]) -> Dict[str, Any]:
    from userbot.client import get_userbot
    
    userbot = await get_userbot()
    if not userbot:
        raise ValueError("Userbot not available")
    
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    submitter_id = payload.get("user_id")
    submitter_username = payload.get("username", "")
    checked_on = payload.get("checked_on")
    if not checked_on:
        raise ValueError("Checked_on is required")
    
    post_text = build_channel_post(entry)
    file_path = plugin.get("file_path")
    
    result = await userbot.publish_plugin(post_text, file_path)
    
    update_request_status(entry.get("id"), "published")
    
    config = get_config()
    channel_username = config.get("publish_channel", "xzcvzxa")
    
    add_to_catalog(
        entry,
        result["message_id"],
        result["chat_id"],
        channel_username,
        submitter_id,
        submitter_username,
    )
    
    if file_path:
        Path(file_path).unlink(missing_ok=True)
    
    return result


async def update_plugin(entry: Dict[str, Any], old_catalog_entry: Dict[str, Any]) -> Dict[str, Any]:
    from userbot.client import get_userbot
    
    userbot = await get_userbot()
    if not userbot:
        raise ValueError("Userbot not available")
    
    payload = entry.get("payload", {})
    plugin = payload.get("plugin", {})
    
    old_message = old_catalog_entry.get("channel_message", {})
    old_message_id = old_message.get("message_id")
    
    if not old_message_id:
        raise ValueError("Original message not found")
    
    new_payload = {
        **payload,
        "usage_ru": payload.get("usage_ru") or old_catalog_entry.get("ru", {}).get("usage", ""),
        "usage_en": payload.get("usage_en") or old_catalog_entry.get("en", {}).get("usage", ""),
        "category_key": payload.get("category_key") or old_catalog_entry.get("category", ""),
    }
    entry_copy = {**entry, "payload": new_payload}
    
    post_text = build_channel_post(entry_copy)
    file_path = plugin.get("file_path")
    
    result = await userbot.update_message(old_message_id, post_text, file_path)
    
    update_request_status(entry.get("id"), "published")
    
    update_catalog_entry(
        old_catalog_entry.get("slug"),
        entry,
        old_message_id,
    )
    
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
        "category": payload.get("category_key"),
        "authors": {
            "ru": author,
            "en": author,
            "handles": handles,
        },
        "submitters": submitters,
        "ru": {
            "name": plugin.get("name"),
            "description": payload.get("description_ru") or plugin.get("description"),
            "usage": payload.get("usage_ru"),
            "min_version": plugin.get("min_version"),
            "version": plugin.get("version"),
            "settings_label": "✅" if plugin.get("has_ui_settings") else "❌",
        },
        "en": {
            "name": plugin.get("name"),
            "description": payload.get("description_en") or plugin.get("description"),
            "usage": payload.get("usage_en"),
            "min_version": plugin.get("min_version"),
            "version": plugin.get("version"),
            "settings_label": "✅" if plugin.get("has_ui_settings") else "❌",
        },
        "settings": {"has_ui": plugin.get("has_ui_settings", False)},
        "requirements": {"min_version": plugin.get("min_version")},
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
    invalidate("plugins")


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

            if payload.get("category_key"):
                p["category"] = payload.get("category_key")

            ru_locale["name"] = plugin.get("name") or ru_locale.get("name")
            en_locale["name"] = plugin.get("name") or en_locale.get("name")

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
    invalidate("plugins")


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
            invalidate("plugins")
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
    invalidate("plugins")
    return True


def make_slug(name: str) -> str:
    slug = (name or "").lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug or "plugin"