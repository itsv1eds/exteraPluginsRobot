import asyncio
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from uuid import uuid4

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Document

from bot import limits
from core.plugin_parser import PluginParseError, parse_plugin_file
from core.plugin_formats import ELYX_EXTENSIONS, plugin_extension, plugin_file_limit
from core.categories import categories_for, category_fields
from core.plugin_metadata import write_plugin_categories
from bot.services.plugin_files import download_large_plugin
from bot.helpers import get_uploads_subdir, sanitize_filename
from bot.formatting import plain_html


def _unique_upload_name(base_id: str, ext: str) -> str:
    return f"{sanitize_filename(base_id)}-{uuid4().hex[:8]}.{ext}"


@dataclass
class PluginData:
    id: str
    name: str
    description: str
    author: str
    version: str
    min_version: str
    has_settings: bool
    file_path: str
    file_id: Optional[str] = None
    storage: Optional[Dict[str, Any]] = None
    file_extension: str = "plugin"
    app_version: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "version": self.version,
            "min_version": self.min_version,
            "has_ui_settings": self.has_settings,
            "file_path": self.file_path,
            "file_id": self.file_id,
            "storage": self.storage,
            "file_extension": self.file_extension,
            "file_name": f"{self.id}.{self.file_extension}",
            "format": "python" if self.file_extension == "plugin" else "elyx",
            "app_version": self.app_version,
            **self.metadata,
        }


async def process_plugin_file(bot: Bot, document: Document) -> PluginData:
    extension = plugin_extension(document.file_name or "") if document else ""
    if not extension:
        raise ValueError("invalid_file")
    maximum = plugin_file_limit(document.file_name)
    if document.file_size and document.file_size > maximum:
        raise ValueError("file_too_large")
    uploads = get_uploads_subdir("plugins")
    with tempfile.TemporaryDirectory(prefix="submission-", dir=uploads) as directory:
        temp = Path(directory) / ("upload." + extension)
        try:
            if document.file_size and document.file_size > limits.BOT_DOWNLOAD_BYTES:
                await download_large_plugin(bot, document, temp)
            else:
                try:
                    await bot.download(document, destination=temp, timeout=120)
                except TelegramBadRequest as exc:
                    if extension == "plugin" or "file is too big" not in str(exc).lower():
                        raise
                    await download_large_plugin(bot, document, temp)
            if not temp.is_file():
                raise ValueError("download_error")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("download_error") from exc
        if temp.stat().st_size > maximum:
            raise ValueError("file_too_large")
        try:
            meta = await asyncio.to_thread(parse_plugin_file, temp)
        except (PluginParseError, UnicodeError) as exc:
            raise ValueError(f"parse_error:{plain_html(str(exc))}") from exc
        final_path = uploads / _unique_upload_name(meta.id, extension)
        temp.replace(final_path)
    return PluginData(
        id=meta.id, name=meta.name, description=meta.description, author=meta.author,
        version=meta.version, min_version=meta.min_version, has_settings=meta.has_ui_settings,
        file_path=str(final_path), file_id=document.file_id,
        file_extension=extension, app_version=meta.app_version, metadata={**{k: v for k, v in meta.optional.items() if k not in ("id", "name", "author", "version", "description", "min_version", "app_version")}, "categories": meta.categories},
    )


def build_submission_payload(
    user_id: int,
    username: str,
    plugin: PluginData,
    description_ru: str,
    description_en: str,
    usage_ru: str,
    usage_en: str,
    category_key: str | list[str],
) -> Dict[str, Any]:
    return {
        "user_id": user_id,
        "username": username,
        "plugin": plugin.to_dict(),
        "description_ru": description_ru,
        "description_en": description_en,
        "usage_ru": usage_ru,
        "usage_en": usage_en,
        **category_fields(category_key),
        "submission_type": "plugin",
    }


async def prepare_submission_categories(payload: Dict[str, Any]) -> bool:
    keys = categories_for(payload)
    if not keys:
        keys = categories_for(payload.get("old_plugin") or {})
    if not keys:
        raise PluginParseError("Choose at least one plugin category")
    payload.update(category_fields(keys))
    plugin = payload.get("plugin") or {}
    path = plugin.get("file_path")
    changed = False
    if path and Path(path).is_file():
        changed = await asyncio.to_thread(write_plugin_categories, path, keys, versions_dir=Path(path).parent / "versions")
    plugin["categories"] = keys
    if changed:
        plugin["file_id"] = None
        plugin["category_file_changed"] = True
        payload.pop("moderation_file_id", None)
    payload["plugin"] = plugin
    return changed


async def prepare_catalog_categories(payload: Dict[str, Any], catalog: Dict[str, Any], userbot) -> None:
    plugin = payload.setdefault("plugin", {})
    if not categories_for(payload):
        payload.update(category_fields(catalog))
    path = Path(plugin.get("file_path") or "")
    if not path.is_file():
        file = catalog.get("file") or {}
        message_id = file.get("message_id") or (catalog.get("channel_message") or {}).get("message_id")
        entity = await userbot.get_publish_entity()
        message = await asyncio.wait_for(userbot.client.get_messages(entity, ids=message_id), timeout=60)
        if not message or not message.document:
            raise PluginParseError("Original plugin document was not found")
        filename = file.get("file_name") or userbot._get_file_name(message) or "plugin.plugin"
        extension = plugin_extension(filename)
        if not extension or message.document.size > plugin_file_limit(filename):
            raise PluginParseError("Original plugin file has an unsupported format or size")
        uploads = get_uploads_subdir("plugins")
        path = uploads / _unique_upload_name(plugin.get("id") or catalog.get("plugin_id") or "plugin", extension)
        with tempfile.TemporaryDirectory(prefix="submission-", dir=uploads) as directory:
            temporary = Path(directory) / path.name
            await asyncio.wait_for(userbot.client.download_media(message, file=str(temporary)), timeout=600)
            metadata = await asyncio.to_thread(parse_plugin_file, temporary, plugin.get("version") or None)
            expected = plugin.get("id") or catalog.get("plugin_id")
            if expected and metadata.id != expected:
                raise PluginParseError("Original plugin ID does not match the catalog")
            temporary.replace(path)
        plugin.update(id=metadata.id, file_path=str(path), file_extension=extension, file_id=None,
                      format="elyx" if extension in ELYX_EXTENSIONS else "python", category_file_changed=True)
        plugin.setdefault("app_version", metadata.app_version)
        for key, value in metadata.optional.items():
            plugin.setdefault(key, value)
    await prepare_submission_categories(payload)
