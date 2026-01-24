import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

MANDATORY_FIELDS = {
    "id": "__id__",
    "name": "__name__",
    "description": "__description__",
    "author": "__author__",
    "version": "__version__",
    "min_version": "__min_version__",
}

OPTIONAL_FIELDS = {
    "icon": "__icon__",
    "link": "__link__",
}


@dataclass
class PluginMetadata:
    """Structured representation of the dunder metadata in plugin files."""

    id: str
    name: str
    description: str
    author: str
    version: str
    min_version: str
    has_ui_settings: bool
    raw_text: str
    optional: Dict[str, Optional[str]] = field(default_factory=dict)

    def as_post_template(self) -> Dict[str, Optional[str]]:
        """Return core fields handy for template rendering."""

        return {
            "Название": self.name,
            "Автор": self.author,
            "Описание": self.description,
            "Минимальная версия": self.min_version,
            "Настройки": "✅" if self.has_ui_settings else "❌",
        }


class PluginParseError(RuntimeError):
    """Raised when a plugin file lacks mandatory metadata."""


def parse_plugin_file(path: Path | str) -> PluginMetadata:
    """Read the provided plugin file and parse the metadata section."""

    plugin_path = Path(path)
    if not plugin_path.exists():
        raise FileNotFoundError(plugin_path)
    text = plugin_path.read_text(encoding="utf-8")
    return parse_plugin_text(text)


def parse_plugin_text(text: str) -> PluginMetadata:
    """Parse metadata directly from a plugin file as text."""

    normalized = text.replace("\r\n", "\n")
    fields: Dict[str, Optional[str]] = {}

    for key, dunder in {**MANDATORY_FIELDS, **OPTIONAL_FIELDS}.items():
        value = _extract_dunder_value(normalized, dunder)
        fields[key] = value

    missing = [name for name in MANDATORY_FIELDS if not fields.get(name)]
    if missing:
        raise PluginParseError(f"Missing mandatory fields: {', '.join(missing)}")

    has_ui_settings = _detect_ui_settings_import(normalized)

    metadata = PluginMetadata(
        id=fields["id"],
        name=fields["name"],
        description=fields["description"],
        author=fields["author"],
        version=fields["version"],
        min_version=fields["min_version"],
        has_ui_settings=has_ui_settings,
        raw_text=text,
        optional={key: fields.get(key) for key in OPTIONAL_FIELDS},
    )

    return metadata


def _extract_dunder_value(text: str, dunder_name: str) -> Optional[str]:
    pattern = re.compile(
        rf"^{re.escape(dunder_name)}\s*=\s*(?P<value>.+)$",
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
        # support triple-quoted strings split via parentheses.
        try:
            return eval(raw_value, {})  # noqa: S307 - controlled metadata parsing
        except Exception:
            return raw_value
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return raw_value.strip()


def _detect_ui_settings_import(text: str) -> bool:
    return bool(re.search(r"^from\s+ui\.settings\s+import", text, re.MULTILINE))
