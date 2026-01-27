from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.types import Document, FSInputFile

from plugin_parser import PluginParseError, parse_plugin_file
from bot.helpers import download_document, get_uploads_dir, sanitize_filename
from bot.cache import get_config


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
    
    @property
    def settings_label(self) -> str:
        return "✅" if self.has_settings else "❌"
    
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
        }


async def process_plugin_file(bot: Bot, document: Document) -> PluginData:
    if not document.file_name or not document.file_name.endswith(".plugin"):
        raise ValueError("invalid_file")
    
    uploads = get_uploads_dir()
    
    try:
        temp_path = await download_document(bot, document.file_id, uploads)
    except Exception as e:
        raise ValueError("download_error") from e
    
    try:
        meta = parse_plugin_file(temp_path)
    except (FileNotFoundError, PluginParseError) as e:
        temp_path.unlink(missing_ok=True)
        raise ValueError(f"parse_error:{e}") from e
    
    final_name = f"{sanitize_filename(meta.id)}.plugin"
    final_path = uploads / final_name
    
    if temp_path != final_path:
        final_path.unlink(missing_ok=True)
        temp_path.rename(final_path)
    
    return PluginData(
        id=meta.id,
        name=meta.name,
        description=meta.description,
        author=meta.author,
        version=meta.version,
        min_version=meta.min_version,
        has_settings=meta.has_ui_settings,
        file_path=str(final_path),
        file_id=document.file_id,
    )


def build_submission_payload(
    user_id: int,
    username: str,
    plugin: PluginData,
    description_ru: str,
    description_en: str,
    usage_ru: str,
    usage_en: str,
    category_key: str,
    category_label: str,
) -> Dict[str, Any]:
    return {
        "user_id": user_id,
        "username": username,
        "plugin": plugin.to_dict(),
        "description_ru": description_ru,
        "description_en": description_en,
        "usage_ru": usage_ru,
        "usage_en": usage_en,
        "category_key": category_key,
        "category_label": category_label,
        "submission_type": "plugin",
    }