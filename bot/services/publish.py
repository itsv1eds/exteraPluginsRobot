import html
import re
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage import load_icons, load_plugins, load_updated, save_icons, save_plugins, save_updated
from request_store import update_request_status
from bot.cache import get_categories, invalidate, get_config
from catalog import invalidate_catalog_cache

logger = logging.getLogger(__name__)


def _invalidate_all_plugins() -> None:
    invalidate("plugins")
    invalidate_catalog_cache()


def _invalidate_all_icons() -> None:
    invalidate("icons")
    invalidate_catalog_cache()


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

    def _norm_text(value: str) -> str:
        return (value or "").replace("\\n", "\n").strip()

    desc_fallback = _norm_text(plugin.get("description", ""))
    desc_ru = html.escape(_norm_text(payload.get("description_ru") or desc_fallback or "")) or "—"
    desc_en = html.escape(_norm_text(payload.get("description_en") or desc_fallback or "")) or "—"
    usage_ru = html.escape(_norm_text(payload.get("usage_ru") or "—"))
    usage_en = html.escape(_norm_text(payload.get("usage_en") or "—"))
    min_ver = html.escape(plugin.get("min_version", ""))

    checked_line = f"<b>Проверено на:</b> {checked}" if checked else ""
    checked_line_en = f"<b>Checked on:</b> {checked}" if checked else ""

    min_version_line = f"<b>Минимальная версия:</b> {min_ver}" if min_ver else ""
    min_version_line_en = f"<b>Min.version:</b> {min_ver}" if min_ver else ""

    ru_lines = [
        f"<b>Название:</b> {name}",
        f"<b>Автор:</b> {author}",
        f"<b>Описание:</b> {desc_ru}",
        f"<b>Использование:</b> {usage_ru}",
        f"<b>Настройки:</b> {settings}",
    ]
    if min_version_line:
        ru_lines.append(min_version_line)
    if checked_line:
        ru_lines.append(checked_line)
    ru_block = "\n".join(ru_lines)

    en_lines = [
        f"<b>Title:</b> {name}",
        f"<b>Author:</b> {author}",
        f"<b>Description:</b> {desc_en}",
        f"<b>Usage:</b> {usage_en}",
        f"<b>Settings:</b> {settings}",
    ]
    if min_version_line_en:
        en_lines.append(min_version_line_en)
    if checked_line_en:
        en_lines.append(checked_line_en)
    en_block = "\n".join(en_lines)

    tags_line = " | ".join(tags)

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

    name = html.escape(icon.get("name", "")) or "—"
    author = html.escape(icon.get("author", "")) or "—"
    version = html.escape(icon.get("version", ""))
    count = icon.get("count", 0)

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

    tags_line = " | ".join(tags)

    parts = [
        f"<b>🇷🇺 [RU]:</b>\n<blockquote expandable>{chr(10).join(ru_lines)}</blockquote>",
        f"<b>🇺🇸 [EN]:</b>\n<blockquote expandable>{chr(10).join(en_lines)}</blockquote>",
    ]
    if tags_line:
        parts.append(tags_line)
    return "\n\n".join(parts)


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
    
    post_text = build_channel_post(entry)
    file_path = plugin.get("file_path")
    
    result = await userbot.publish_plugin(post_text, file_path)
    
    update_request_status(entry.get("id"), "published")
    
    config = get_config()
    channel_username = (
        (config.get("channel", {}) or {}).get("username")
        or config.get("publish_channel")
        or "xzcvzxa"
    )
    
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


async def publish_icon(entry: Dict[str, Any]) -> Dict[str, Any]:
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

    result = await userbot.publish_icon(post_text, file_path)

    update_request_status(entry.get("id"), "published")

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

    slug = old_catalog_entry.get("slug") or payload.get("update_slug") or plugin.get("id")
    name = plugin.get("name") or (old_catalog_entry.get("ru") or {}).get("name") or (old_catalog_entry.get("en") or {}).get("name") or slug
    link = (old_catalog_entry.get("channel_message") or {}).get("link")
    if link:
        add_updated_plugin(name, link)
    
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
    _invalidate_all_plugins()


def add_updated_plugin(name: str, link: str) -> None:
    updated = load_updated()
    items = updated.setdefault("items", [])
    if not link:
        return
    exists = any(isinstance(it, dict) and it.get("link") == link for it in items)
    if exists:
        return
    items.append({"name": name, "link": link, "added_at": datetime.utcnow().isoformat()})
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


def update_icon_catalog_entry(slug: str, payload: Dict[str, Any], message_id: int) -> None:
    icon = payload.get("icon", {}) if isinstance(payload, dict) else {}
    db = load_icons()
    packs = db.get("iconpacks", [])
    if not isinstance(packs, list):
        return

    for p in packs:
        if not isinstance(p, dict):
            continue
        if (p.get("slug") or "").strip().lower() != (slug or "").strip().lower():
            continue

        ru_locale = p.setdefault("ru", {})
        en_locale = p.setdefault("en", {})
        authors = p.setdefault("authors", {})

        name = icon.get("name")
        if name:
            ru_locale["name"] = name
            en_locale["name"] = name

        author_text = icon.get("author")
        if author_text:
            authors["ru"] = author_text
            authors["en"] = author_text
            authors["handles"] = list(set(re.findall(r"@[\w]+", author_text)))

        version = icon.get("version")
        if version is not None:
            ru_locale["version"] = version
            en_locale["version"] = version

        count = icon.get("count")
        if count is not None:
            p["count"] = count

        channel_msg = p.setdefault("channel_message", {})
        channel_msg["message_id"] = message_id

        p["updated_at"] = datetime.utcnow().isoformat()
        break

    save_icons(db)
    _invalidate_all_icons()


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

            if payload.get("category_key"):
                p["category"] = payload.get("category_key")

            ru_locale["name"] = plugin.get("name") or ru_locale.get("name")
            en_locale["name"] = plugin.get("name") or en_locale.get("name")

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
    """Remove all plugins and iconpacks submitted by the given user from the catalog and channel."""
    from userbot.client import get_userbot
    from catalog import find_user_plugins, find_user_icons

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
                from userbot.client import UserbotClient
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