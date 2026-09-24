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
_DUNDER_FIELD_BY_NAME = {
    dunder: key
    for key, dunder in {**MANDATORY_FIELDS, **OPTIONAL_FIELDS}.items()
}
_DUNDER_RE = re.compile(
    r"^[ \t]*(?P<name>__(?:id|name|author|version|description|min_version|app_version|icon|link)__)[ \t]*=[ \t]*(?P<value>.+)$",
    re.MULTILINE,
)
_UI_SETTINGS_IMPORT_RE = re.compile(r"^from\s+ui\.settings\s+import", re.MULTILINE)


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

    fields: Dict[str, Optional[str]] = {key: None for key in _DUNDER_FIELD_BY_NAME.values()}
    for match in _DUNDER_RE.finditer(text):
        key = _DUNDER_FIELD_BY_NAME[match.group("name")]
        if fields[key] is None:
            fields[key] = _strip_literal(match.group("value").strip())

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

    has_ui_settings = _detect_ui_settings_import(text)

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


def _strip_literal(raw_value: str) -> Optional[str]:
    if not raw_value:
        return None

    raw_value = raw_value.strip()
    if raw_value.startswith(('"""', "'''")):
        quote = raw_value[:3]
        if raw_value.endswith(quote) and len(raw_value) >= 6:
            return raw_value[3:-3]
        parts = raw_value.split(quote)
        if len(parts) >= 3:
            return parts[1]
    elif raw_value.startswith(('"', "'")):
        quote = raw_value[0]
        if raw_value.endswith(quote) and len(raw_value) >= 2:
            return raw_value[1:-1]
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
    except Exception:
        return raw_value.strip()


def _detect_ui_settings_import(text: str) -> bool:
    return bool(_UI_SETTINGS_IMPORT_RE.search(text))
