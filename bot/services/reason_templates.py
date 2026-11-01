from __future__ import annotations

import html
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from bot.languages import SUPPORTED_LANGUAGES, normalize_language

REASON_TEMPLATES_VERSION = 4
TEMPLATES_LIMIT = 30
TEMPLATE_PAGE_SIZE = 6
TEMPLATE_PAGE_BUDGET = 2600

_TRANSLATIONS = {
    lang: json.loads((Path(__file__).resolve().parents[1] / "locales/reasons" / f"{lang}.json").read_text(encoding="utf-8"))
    for lang in SUPPORTED_LANGUAGES
}
TEMPLATE_CONFIG_KEYS = {
    "reject": "reject_templates",
    "approve": "approve_templates",
    "update_reject": "update_reject_templates",
    "update_approve": "update_approve_templates",
}
DEFAULTS = {kind: _TRANSLATIONS["ru"][kind] for kind in TEMPLATE_CONFIG_KEYS}
DEFAULT_APPROVE_TEMPLATES = DEFAULTS["approve"]
DEFAULT_REJECT_TEMPLATES = DEFAULTS["reject"]
DEFAULT_UPDATE_APPROVE_TEMPLATES = DEFAULTS["update_approve"]
DEFAULT_UPDATE_REJECT_TEMPLATES = DEFAULTS["update_reject"]
ADDITIONS = {
    "reject": DEFAULTS["reject"][:7],
    "update_reject": DEFAULTS["update_reject"][:5],
    "approve": DEFAULTS["approve"][-1:],
    "update_approve": DEFAULTS["update_approve"][-1:],
}
LEGACY_REJECT_TEMPLATES = _TRANSLATIONS["ru"]["legacy_reject"]
LEGACY_UPDATE_REJECT_TEMPLATES = _TRANSLATIONS["ru"]["legacy_update_reject"]
LEGACY_DEFAULTS = {**DEFAULTS, "reject": LEGACY_REJECT_TEMPLATES, "update_reject": LEGACY_UPDATE_REJECT_TEMPLATES}
BUILTIN_TRANSLATIONS = {}
for _kind in _TRANSLATIONS["ru"]:
    for _index, _source in enumerate(_TRANSLATIONS["ru"][_kind]):
        _bundle = {lang: values[_kind][_index] for lang, values in _TRANSLATIONS.items() if _kind in values}
        for _value in _bundle.values():
            BUILTIN_TRANSLATIONS[_value] = _bundle


def localize_reason(value: str, lang: str = "ru") -> str:
    language = normalize_language(lang)
    lines = []
    for line in str(value or "").split("\n"):
        match = re.fullmatch(r"(\s*(?:(?:\d+[.)]|[•*\-])\s+)?)(.*?)(\s*)", line)
        if match:
            prefix, body, suffix = match.groups()
            translated = BUILTIN_TRANSLATIONS.get(body, {}).get(language)
            if translated:
                line = prefix + translated + suffix
        lines.append(line)
    return "\n".join(lines)


def template_kind(raw: str) -> str:
    return raw if raw in TEMPLATE_CONFIG_KEYS else "reject"


def load_reason_templates(config: dict, kind: str = "reject", lang: str = "ru") -> list[str]:
    kind = template_kind(kind)
    raw = config.get(TEMPLATE_CONFIG_KEYS[kind])
    values = raw if isinstance(raw, list) else DEFAULTS[kind]
    language = normalize_language(lang)
    return [BUILTIN_TRANSLATIONS.get(value, {}).get(language, value)
            for item in values if (value := str(item).strip())]


def reason_template_id(value: str) -> str:
    canonical = localize_reason(str(value).strip(), "ru")
    return hashlib.blake2s(canonical.encode("utf-8"), digest_size=10).hexdigest()


def visible_reason_templates(values: list[str], kind: str, user_id: int) -> list[str]:
    from core.user_store import hidden_moderation_templates

    hidden = set(hidden_moderation_templates(user_id, kind))
    return [value for value in values if reason_template_id(value) not in hidden] if hidden else values


def upgrade_reason_templates(config: dict) -> bool:
    try:
        current = int(config.get("reason_templates_version") or 0)
    except (ValueError, TypeError):
        current = 0
    if current >= REASON_TEMPLATES_VERSION:
        return False
    previous = {key: deepcopy(config[key]) for key in TEMPLATE_CONFIG_KEYS.values() if key in config}
    archive = config.get("templates_archive")
    if not isinstance(archive, list):
        archive = [] if archive is None else [deepcopy(archive)]
    else:
        archive = deepcopy(archive)
    archive.append({"version": current, "upgraded_to": REASON_TEMPLATES_VERSION,
                    "archived_at": datetime.now(timezone.utc).isoformat(), "templates": previous})
    for kind, key in TEMPLATE_CONFIG_KEYS.items():
        raw = config.get(key)
        if not isinstance(raw, list) or raw == LEGACY_DEFAULTS[kind]:
            config[key] = list(DEFAULTS[kind])
        elif raw and kind in ADDITIONS:
            known = {localize_reason(str(value), "ru") for value in raw}
            additions = [value for value in ADDITIONS[kind] if value not in known]
            values = [*raw, *additions[:max(0, TEMPLATES_LIMIT - len(raw))]]
            priority = {value: index for index, value in enumerate(ADDITIONS[kind])} if kind in {"reject", "update_reject"} else {}
            config[key] = sorted(values, key=lambda value: priority.get(localize_reason(str(value), "ru"), len(priority)))
    config["templates_archive"] = archive
    config["reason_templates_version"] = REASON_TEMPLATES_VERSION
    return True


def templates_allowed(entry: dict | None, *, check_access: bool = True) -> bool:
    if not isinstance(entry, dict) or entry.get("type") not in {"new", "update"}:
        return False
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    if payload.get("is_appeal") or str(entry.get("status")) in {"banned", "blocked"}:
        return False
    if any(payload.get(key) or entry.get(key) for key in ("banned", "blocked", "is_banned", "is_blocked")):
        return False
    if not check_access:
        return True
    from bot.services.validation import is_plugin_blocked
    from core.user_store import is_user_banned

    plugin = payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}
    plugin_id = str(plugin.get("id") or "").strip()
    if plugin_id and is_plugin_blocked(plugin_id):
        return False
    user_id = payload.get("user_id")
    try:
        return not (user_id and is_user_banned(int(user_id)))
    except (ValueError, TypeError):
        return False


def _utf16_size(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _template_chunks(text: str) -> list[str]:
    chunks = []
    current = []
    size = 0
    for char in str(text):
        width = _utf16_size(html.escape(char, quote=False))
        if size + width > TEMPLATE_PAGE_BUDGET:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += width
    chunks.append("".join(current))
    return chunks


def template_pages(templates: list[str]) -> list[list[tuple[int, str]]]:
    pages, page, size = [], [], 0
    for index, value in enumerate(templates):
        for chunk in _template_chunks(value):
            width = _utf16_size(html.escape(chunk, quote=False)) + 24
            if page and (len(page) >= TEMPLATE_PAGE_SIZE or size + width > TEMPLATE_PAGE_BUDGET):
                pages.append(page)
                page, size = [], 0
            page.append((index, chunk))
            size += width
    if page:
        pages.append(page)
    return pages or [[]]


def template_page(templates: list[str], page: int = 0) -> tuple[str, list[int], int, int]:
    pages = template_pages(templates)
    page = max(0, min(int(page), len(pages) - 1))
    items = pages[page]
    text = "\n".join(f"<b>{idx + 1}.</b> {html.escape(' '.join(value.split()), quote=False)}" for idx, value in items)
    return text, list(dict.fromkeys(idx for idx, _ in items)), page, len(pages)


def selected_templates(values, count: int) -> list[int]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(value for value in values if type(value) is int and 0 <= value < count))


def parse_template_numbers(text: str, count: int) -> list[int]:
    if not isinstance(text, str) or not re.fullmatch(r"\s*[0-9]+(?:[\s,;]+[0-9]+)*\s*", text):
        raise ValueError("Enter reason numbers separated by commas or spaces")
    numbers = [int(value) for value in re.findall(r"[0-9]+", text)]
    if any(value < 1 or value > count for value in numbers):
        raise ValueError("Reason number is out of range")
    return list(dict.fromkeys(value - 1 for value in numbers))


def combine_template_reasons(templates: list[str], selected: list[int]) -> str:
    return "\n".join(f"{position + 1}. {html.escape(templates[index], quote=False)}"
                     for position, index in enumerate(selected_templates(selected, len(templates))))
