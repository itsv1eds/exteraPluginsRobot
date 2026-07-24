import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

MANDATORY_FIELDS = {
    "id": "__id__",
    "name": "__name__",
    "author": "__author__",
    "version": "__version__",
}

OPTIONAL_FIELDS = {
    "description": "__description__",
    "min_version": "__min_version__",
    "app_version": "__app_version__",
    "icon": "__icon__",
    "link": "__link__",
}

_VERSION_RE = re.compile(r"\d+(?:\.\d+)*")


def _version_only(value: Optional[str]) -> str:
    if not value:
        return ""
    match = _VERSION_RE.search(str(value))
    return match.group(0) if match else ""


@dataclass
class PluginMetadata:

    id: str
    name: str
    description: str
    author: str
    version: str
    min_version: str
    has_ui_settings: bool
    raw_text: str
    app_version: str = ""
    optional: Dict[str, Optional[str]] = field(default_factory=dict)

    def as_post_template(self) -> Dict[str, Optional[str]]:

        return {
            "Название": self.name,
            "Автор": self.author,
            "Описание": self.description,
            "Минимальная версия": self.min_version,
            "Настройки": "Да" if self.has_ui_settings else "Нет",
        }


class PluginParseError(RuntimeError):
    pass


def parse_plugin_file(path: Path | str, fallback_version: str | None = None) -> PluginMetadata:

    plugin_path = Path(path)
    if not plugin_path.exists():
        raise FileNotFoundError(plugin_path)
    text = plugin_path.read_text(encoding="utf-8")
    return parse_plugin_text(text, fallback_version=fallback_version)


def parse_plugin_text(text: str, fallback_version: str | None = None) -> PluginMetadata:

    normalized = text.replace("\r\n", "\n")
    fields: Dict[str, Optional[str]] = {}

    for key, dunder in {**MANDATORY_FIELDS, **OPTIONAL_FIELDS}.items():
        value = _extract_dunder_value(normalized, dunder)
        fields[key] = value

    missing = [name for name in MANDATORY_FIELDS if not fields.get(name)]
    if missing:
        if missing == ["version"] and fallback_version:
            fields["version"] = str(fallback_version).strip()
            missing = [name for name in MANDATORY_FIELDS if not fields.get(name)]
        if missing:
            raise PluginParseError(f"Missing mandatory fields: {', '.join(missing)}")

    min_version = fields.get("min_version") or ""
    app_version = fields.get("app_version") or ""
    if not min_version and not app_version:
        raise PluginParseError(
            "не указана версия: нужен __min_version__ или __app_version__"
        )

    has_ui_settings = _detect_ui_settings_import(normalized)

    metadata = PluginMetadata(
        id=fields["id"],
        name=fields["name"],
        description=fields.get("description") or "",
        author=fields["author"],
        version=fields["version"],
        min_version=min_version or _version_only(app_version),
        has_ui_settings=has_ui_settings,
        raw_text=text,
        app_version=app_version,
        optional={key: fields.get(key) for key in OPTIONAL_FIELDS},
    )

    return metadata


def _extract_dunder_value(text: str, dunder_name: str) -> Optional[str]:
    pattern = re.compile(
        rf"^\s*{re.escape(dunder_name)}\s*=\s*(?P<value>.+)$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return None

    raw_value = match.group("value").strip()
    return _strip_literal(raw_value)


def _strip_literal(raw_value: str) -> Optional[str]:
    if not raw_value:
        return None

    if raw_value[0] in {'"', "'"}:
        quote = raw_value[0]
        parts = raw_value.split(quote)
        if len(parts) >= 3:
            return parts[1]
    if raw_value.startswith("("):
        try:
            return ast.literal_eval(raw_value)
        except (ValueError, SyntaxError):
            return raw_value
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return raw_value.strip()


def _detect_ui_settings_import(text: str) -> bool:
    return bool(re.search(r"^from\s+ui\.settings\s+import", text, re.MULTILINE))
