import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

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
    "sdk_version": "__sdk_version__",
    "elyx_version": "__elyx_version__",
    "requirements": "__requirements__",
    "requires": "__requires__",
}

_VERSION_RE = re.compile(r"\d+(?:\.\d+)*")
_DUNDER_FIELD_BY_NAME = {
    dunder: key
    for key, dunder in {**MANDATORY_FIELDS, **OPTIONAL_FIELDS}.items()
}
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
    optional: Dict[str, Any] = field(default_factory=dict)

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
    from plugin_formats import ELYX_EXTENSIONS, plugin_extension, plugin_file_limit

    extension = plugin_extension(plugin_path)
    if not extension:
        raise PluginParseError("Unsupported plugin extension")
    if plugin_path.stat().st_size > plugin_file_limit(plugin_path):
        raise PluginParseError("Plugin file exceeds its size limit")
    if extension in ELYX_EXTENSIONS:
        from elyx_parser import parse_elyx_file
        return parse_elyx_file(plugin_path, fallback_version)
    text = plugin_path.read_text(encoding="utf-8")
    return parse_plugin_text(text, fallback_version=fallback_version)


def parse_plugin_text(text: str, fallback_version: str | None = None) -> PluginMetadata:

    fields: Dict[str, Any] = {key: None for key in _DUNDER_FIELD_BY_NAME.values()}
    if text.startswith("\ufeff"):
        raise PluginParseError("UTF-8 BOM is not supported")
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise PluginParseError(f"Invalid Python source: {exc}") from exc
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Name) or target.id not in _DUNDER_FIELD_BY_NAME:
                continue
            key = _DUNDER_FIELD_BY_NAME[target.id]
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError, RecursionError) as exc:
                raise PluginParseError(f"Metadata {target.id} must be a literal") from exc
            if key == "requires":
                if not isinstance(value, dict):
                    raise PluginParseError("__requires__ must be an object")
            elif not isinstance(value, str):
                raise PluginParseError(f"Metadata {target.id} must be a string")
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


def _detect_ui_settings_import(text: str) -> bool:
    return bool(_UI_SETTINGS_IMPORT_RE.search(text))
