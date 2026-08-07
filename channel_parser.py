import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

HASHTAG_TO_CATEGORY = {
    "#информационные": "informational",
    "#informational": "informational",
    "#утилиты": "utilities",
    "#utilities": "utilities",
    "#кастомизация": "customization",
    "#customization": "customization",
    "#развлечения": "fun",
    "#fun": "fun",
    "#библиотека": "library",
    "#library": "library",
}

FIELD_MAPPING_RU = {
    "название": "name",
    "автор": "author",
    "авторы": "author",
    "канал автора": "author_channel",
    "каналы авторов": "author_channel",
    "описание": "description",
    "использование": "usage",
    "настройки": "settings",
    "минимальная версия": "min_version",
    "проверено на": "checked_on",
    "обновлено": "updated_on",
}

FIELD_MAPPING_EN = {
    "title": "name",
    "author": "author",
    "authors": "author",
    "authors channel": "author_channel",
    "author channel": "author_channel",
    "description": "description",
    "usage": "usage",
    "settings": "settings",
    "min.version": "min_version",
    "min version": "min_version",
    "checked on": "checked_on",
    "updated": "updated_on",
}


@dataclass
class ParsedPost:
    ru: Dict[str, str] = field(default_factory=dict)
    en: Dict[str, str] = field(default_factory=dict)
    category: Optional[str] = None
    hashtags: List[str] = field(default_factory=list)
    is_plugin: bool = True
    raw_text: str = ""
    raw_html: str = ""
    message_id: Optional[int] = None
    message_date: Optional[datetime] = None
    
    def get_slug(self) -> str:
        name = self.ru.get("name") or self.en.get("name") or ""
        slug = name.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        return slug or f"plugin-{self.message_id or 'unknown'}"
    
    def get_handles(self) -> List[str]:
        handles = []
        for locale in (self.ru, self.en):
            for key in ("author", "author_channel"):
                val = locale.get(key, "")
                handles.extend(re.findall(r"@[\w]+", val))
        return list(set(handles))
    
    def has_settings(self) -> bool:
        for locale in (self.ru, self.en):
            s = locale.get("settings", "").strip().lower()
            if s in ("yes", "да", "true", "1"):
                return True
        return False
    
    def to_catalog_entry(self, chat_id: int, channel_username: str) -> Dict[str, Any]:
        slug = self.get_slug()
        handles = self.get_handles()
        
        return {
            "slug": slug,
            "status": "published",
            "category": self.category,
            "authors": {
                "ru": self.ru.get("author") or self.ru.get("author_channel"),
                "en": self.en.get("author") or self.en.get("author_channel"),
                "handles": handles,
            },
            "ru": {
                "name": self.ru.get("name"),
                "description": self.ru.get("description"),
                "usage": self.ru.get("usage"),
                "min_version": self.ru.get("min_version"),
                "settings_label": "Да" if self.has_settings() else "Нет",
                "checked_on": self.ru.get("checked_on"),
            },
            "en": {
                "name": self.en.get("name"),
                "description": self.en.get("description"),
                "usage": self.en.get("usage"),
                "min_version": self.en.get("min_version"),
                "settings_label": "Да" if self.has_settings() else "Нет",
                "checked_on": self.en.get("checked_on"),
            },
            "settings": {"has_ui": self.has_settings()},
            "requirements": {"min_version": self.ru.get("min_version") or self.en.get("min_version")},
            "channel_message": {
                "chat_id": chat_id,
                "message_id": self.message_id,
                "date": self.message_date.isoformat() if self.message_date else None,
                "link": f"https://t.me/{channel_username}/{self.message_id}" if channel_username else None,
            },
            "raw_blocks": {"ru": self.ru, "en": self.en},
            "raw_html": self.raw_html,
            "hashtags": self.hashtags,
            "parsed_at": datetime.utcnow().isoformat(),
        }


def parse_channel_post(
    text: str,
    html_text: str = "",
    message_id: int = None,
    message_date: datetime = None,
) -> Optional[ParsedPost]:
    if not text or not text.strip():
        return None
    
    is_plugin = "#plugins" in text.lower() or "использование:" in text.lower() or "usage:" in text.lower()
    is_icon = "#iconpacks" in text.lower() or "#иконки" in text.lower()
    
    if not is_plugin and not is_icon:
        if "🇷🇺" not in text and "🇺🇸" not in text:
            return None
    
    result = ParsedPost(
        is_plugin=is_plugin and not is_icon,
        raw_text=text,
        raw_html=html_text or text,
        message_id=message_id,
        message_date=message_date,
    )
    
    hashtags = re.findall(r"#[\w@]+", text.lower())
    result.hashtags = hashtags
    
    for tag in hashtags:
        if tag.lower() in HASHTAG_TO_CATEGORY:
            result.category = HASHTAG_TO_CATEGORY[tag.lower()]
            break
    
    source = html_text if html_text else text
    
    ru_match = re.search(r"🇷🇺\s*\[RU\]:?\s*\n(.*?)(?=🇺🇸|\Z)", source, re.DOTALL | re.IGNORECASE)
    if ru_match:
        result.ru = _parse_block(ru_match.group(1), FIELD_MAPPING_RU)
    
    en_match = re.search(r"🇺🇸\s*\[EN\]:?\s*\n(.*?)(?=#|\Z)", source, re.DOTALL | re.IGNORECASE)
    if en_match:
        result.en = _parse_block(en_match.group(1), FIELD_MAPPING_EN)
    
    if not result.ru and not result.en:
        result.ru = _parse_block(source, FIELD_MAPPING_RU)
        result.en = _parse_block(source, FIELD_MAPPING_EN)
    
    return result


def _parse_block(text: str, mapping: Dict[str, str]) -> Dict[str, str]:
    result = {}
    lines = text.strip().split("\n")
    
    current_field = None
    current_value = []
    
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        clean_line = re.sub(r"<[^>]+>", "", line)
        
        found = False
        for pattern, field_key in mapping.items():
            if clean_line.lower().startswith(pattern + ":"):
                if current_field and current_value:
                    result[current_field] = " ".join(current_value).strip()
                
                current_field = field_key
                parts = line.split(":", 1)
                value = parts[1].strip() if len(parts) > 1 else ""
                value = re.sub(r"^<[^>]+>|<[^>]+>$", "", value).strip()
                current_value = [value] if value else []
                found = True
                break
        
        if not found and current_field:
            current_value.append(line)
    
    if current_field and current_value:
        result[current_field] = " ".join(current_value).strip()
    
    return result


