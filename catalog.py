import hashlib
import random
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from storage import load_icons, load_plugins

CatalogEntry = Dict[str, Any]

_ASCII_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")


def plugin_deeplink_token(slug: Optional[str]) -> str:
    s = (slug or "").strip()
    if _ASCII_SLUG_RE.match(s):
        return s
    return "p" + hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def find_plugin_by_deeplink_token(token: Optional[str]) -> Optional[CatalogEntry]:
    token = (token or "").strip()
    if not token:
        return None
    direct = find_plugin_by_slug(token)
    if direct:
        return direct
    for plugin in _get_published_plugins():
        if plugin_deeplink_token(plugin.get("slug")) == token:
            return plugin
    return None

_plugins_cache: Optional[List[CatalogEntry]] = None
_icons_cache: Optional[List[CatalogEntry]] = None
_published_plugins_cache: Optional[List[CatalogEntry]] = None
_published_icons_cache: Optional[List[CatalogEntry]] = None
_slug_index: Dict[str, CatalogEntry] = {}
_icon_slug_index: Dict[str, CatalogEntry] = {}


SOURCE_ALL = "all"
SOURCE_OFFICIAL = "official"
SOURCE_EXTERNAL = "external"
OFFICIAL_SOURCE_USERNAME = "exteraPluginsSup"


def invalidate_catalog_cache() -> None:
    global _plugins_cache, _icons_cache, _published_plugins_cache, _published_icons_cache
    global _slug_index, _icon_slug_index
    _plugins_cache = None
    _icons_cache = None
    _published_plugins_cache = None
    _published_icons_cache = None
    _slug_index.clear()
    _icon_slug_index.clear()


def _load_plugins() -> List[CatalogEntry]:
    global _plugins_cache, _slug_index
    if _plugins_cache is not None:
        return _plugins_cache
    
    database = load_plugins()
    _plugins_cache = database.get("plugins", [])
    
    _slug_index.clear()
    for plugin in _plugins_cache:
        slug = _normalize_slug(plugin.get("slug"))
        if slug:
            _slug_index[slug] = plugin
    
    return _plugins_cache


def _load_icons() -> List[CatalogEntry]:
    global _icons_cache, _icon_slug_index
    if _icons_cache is not None:
        return _icons_cache
    
    database = load_icons()
    _icons_cache = database.get("iconpacks", [])
    
    _icon_slug_index.clear()
    for icon in _icons_cache:
        slug = _normalize_slug(icon.get("slug"))
        if slug:
            _icon_slug_index[slug] = icon
    
    return _icons_cache


def _get_published_plugins() -> List[CatalogEntry]:
    global _published_plugins_cache
    if _published_plugins_cache is not None:
        return _published_plugins_cache
    
    _published_plugins_cache = sorted(
        [p for p in _load_plugins() if p.get("status") == "published"],
        key=_plugin_sort_key,
        reverse=True,
    )
    return _published_plugins_cache


def _get_published_icons() -> List[CatalogEntry]:
    global _published_icons_cache
    if _published_icons_cache is not None:
        return _published_icons_cache
    
    _published_icons_cache = [i for i in _load_icons() if i.get("status") == "published"]
    return _published_icons_cache


def _plugin_sort_key(plugin: CatalogEntry) -> tuple[str, str]:
    channel_message = plugin.get("channel_message") if isinstance(plugin.get("channel_message"), dict) else {}
    date = (
        plugin.get("published_at")
        or channel_message.get("date")
        or plugin.get("updated_at")
        or ""
    )
    return (str(date).lower(), str(plugin.get("slug") or "").lower())


def plugin_source_type(plugin: CatalogEntry) -> str:
    source = plugin.get("source")
    if isinstance(source, dict):
        source_type = str(source.get("type") or "").strip().lower()
        if source_type:
            return source_type
    if plugin.get("external"):
        return SOURCE_EXTERNAL
    return SOURCE_OFFICIAL


def is_external_plugin(plugin: Optional[CatalogEntry]) -> bool:
    return bool(plugin and plugin_source_type(plugin) == SOURCE_EXTERNAL)


def _filter_plugins_by_source(entries: List[CatalogEntry], source_filter: str = SOURCE_ALL) -> List[CatalogEntry]:
    source_filter = (source_filter or SOURCE_ALL).strip().lower()
    if source_filter in {"", SOURCE_ALL}:
        return entries
    if source_filter == SOURCE_EXTERNAL:
        return [p for p in entries if is_external_plugin(p)]
    if source_filter == SOURCE_OFFICIAL:
        return [p for p in entries if not is_external_plugin(p)]
    return [
        p
        for p in entries
        if isinstance(p.get("source"), dict)
        and str(p["source"].get("id") or p["source"].get("username") or "").strip().lower() == source_filter
    ]


def plugin_source_filter_key(plugin: CatalogEntry) -> str:
    if not is_external_plugin(plugin):
        return SOURCE_OFFICIAL
    source = plugin.get("source") if isinstance(plugin.get("source"), dict) else {}
    return str(source.get("id") or source.get("username") or SOURCE_EXTERNAL).strip().lower() or SOURCE_EXTERNAL


def plugin_source_display(plugin: CatalogEntry) -> str:
    if not is_external_plugin(plugin):
        return f"@{OFFICIAL_SOURCE_USERNAME}"
    source = plugin.get("source") if isinstance(plugin.get("source"), dict) else {}
    username = str(source.get("username") or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    return str(source.get("title") or source.get("id") or "External").strip() or "External"


def list_plugin_sources() -> List[Dict[str, Any]]:
    entries = _get_published_plugins()
    counts: Dict[str, Dict[str, Any]] = {
        SOURCE_ALL: {"key": SOURCE_ALL, "label": "Все источники", "count": len(entries), "type": SOURCE_ALL},
        SOURCE_OFFICIAL: {
            "key": SOURCE_OFFICIAL,
            "label": f"@{OFFICIAL_SOURCE_USERNAME}",
            "count": 0,
            "type": SOURCE_OFFICIAL,
        },
    }

    for plugin in entries:
        key = plugin_source_filter_key(plugin)
        if key == SOURCE_OFFICIAL:
            counts[SOURCE_OFFICIAL]["count"] += 1
            continue
        item = counts.setdefault(
            key,
            {
                "key": key,
                "label": plugin_source_display(plugin),
                "count": 0,
                "type": SOURCE_EXTERNAL,
            },
        )
        item["count"] += 1

    external = sorted(
        [item for key, item in counts.items() if key not in {SOURCE_ALL, SOURCE_OFFICIAL}],
        key=lambda item: str(item.get("label") or "").lower(),
    )
    return [counts[SOURCE_ALL], counts[SOURCE_OFFICIAL], *external]


def list_published_plugins(limit: Optional[int] = None, source_filter: str = SOURCE_ALL) -> List[CatalogEntry]:
    entries = _filter_plugins_by_source(_get_published_plugins(), source_filter)
    if limit is not None:
        return entries[:limit]
    return entries


def list_published_icons(limit: Optional[int] = None) -> List[CatalogEntry]:
    entries = _get_published_icons()
    if limit is not None:
        return entries[:limit]
    return entries


def list_plugins_by_category(category_key: str, source_filter: str = SOURCE_ALL) -> List[CatalogEntry]:
    entries = _filter_plugins_by_source(_get_published_plugins(), source_filter)
    if category_key in (None, "", "_all"):
        return entries
    return [p for p in entries if p.get("category") == category_key]


def list_icons_by_category(category_key: str) -> List[CatalogEntry]:
    entries = _get_published_icons()
    if category_key in (None, "", "_all"):
        return entries
    return [i for i in entries if i.get("category") == category_key]


def search_plugins(query: str, limit: int = 10, source_filter: str = SOURCE_ALL) -> List[CatalogEntry]:
    normalized = query.strip().lower()
    entries = _filter_plugins_by_source(_get_published_plugins(), source_filter)
    
    if not normalized:
        result = entries.copy()
        random.shuffle(result)
        return result[:limit]

    def matches(plugin: CatalogEntry) -> bool:
        haystack_parts: List[str] = []
        for locale in ("ru", "en"):
            locale_data = plugin.get(locale, {}) or {}
            haystack_parts.extend([
                locale_data.get("name"),
                locale_data.get("description"),
                locale_data.get("usage"),
            ])
        haystack_parts.append(plugin.get("slug"))
        haystack_parts.append(plugin.get("category"))
        source = plugin.get("source")
        if isinstance(source, dict):
            haystack_parts.extend([
                source.get("title"),
                source.get("username"),
                source.get("id"),
            ])
        haystack = " ".join(filter(None, haystack_parts)).lower()
        return normalized in haystack

    results = [p for p in entries if matches(p)]
    random.shuffle(results)
    return results[:limit]


def search_icons(query: str, limit: int = 10) -> List[CatalogEntry]:
    normalized = query.strip().lower()
    entries = _get_published_icons()
    
    if not normalized:
        result = entries.copy()
        random.shuffle(result)
        return result[:limit]

    def matches(icon: CatalogEntry) -> bool:
        haystack_parts: List[str] = []
        for locale in ("ru", "en"):
            locale_data = icon.get(locale, {}) or {}
            haystack_parts.extend([
                locale_data.get("name"),
                locale_data.get("description"),
                locale_data.get("usage"),
            ])
        haystack_parts.append(icon.get("slug"))
        haystack = " ".join(filter(None, haystack_parts)).lower()
        return normalized in haystack

    results = [i for i in entries if matches(i)]
    random.shuffle(results)
    return results[:limit]


def _normalize_slug(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _normalize_handle(value: Optional[str]) -> str:
    if not value:
        return ""
    value = value.strip()
    if value.startswith("@"):
        value = value[1:]
    return value.lower()


def find_plugin_by_slug(slug: Optional[str]) -> Optional[CatalogEntry]:
    target = _normalize_slug(slug)
    if not target:
        return None
    
    _load_plugins()
    return _slug_index.get(target)


def find_icon_by_slug(slug: Optional[str]) -> Optional[CatalogEntry]:
    target = _normalize_slug(slug)
    if not target:
        return None
    
    _load_icons()
    return _icon_slug_index.get(target)


def find_user_plugins(user_id: int, username: str = "") -> List[CatalogEntry]:
    results = []
    handle = f"@{username.lower()}" if username else ""
    
    for plugin in _get_published_plugins():
        if is_external_plugin(plugin):
            continue
        submitters = plugin.get("submitters", [])
        
        for sub in submitters:
            if sub.get("user_id") == user_id:
                results.append(plugin)
                break
        else:
            if handle:
                authors = plugin.get("authors", {})
                handles = authors.get("handles", [])
                raw_blocks = plugin.get("raw_blocks", {}) or {}
                raw_ru = raw_blocks.get("ru") if isinstance(raw_blocks.get("ru"), dict) else {}
                raw_en = raw_blocks.get("en") if isinstance(raw_blocks.get("en"), dict) else {}
                
                if any(h.lower() == handle for h in handles):
                    results.append(plugin)
                    continue
                
                for locale in ("ru", "en"):
                    author_text = (authors.get(locale) or "").lower()
                    if handle in author_text:
                        results.append(plugin)
                        break
                else:
                    raw_author_text = " ".join(
                        filter(
                            None,
                            [
                                (raw_ru.get("author") or ""),
                                (raw_ru.get("author_channel") or ""),
                                (raw_en.get("author") or ""),
                                (raw_en.get("author_channel") or ""),
                            ],
                        )
                    ).lower()
                    if handle and handle in raw_author_text:
                        results.append(plugin)
    
    return results


def find_user_icons(user_id: int, username: str = "") -> List[CatalogEntry]:
    results = []
    handle = f"@{username.lower()}" if username else ""
    
    for icon in _get_published_icons():
        submitters = icon.get("submitters", [])
        
        for sub in submitters:
            if sub.get("user_id") == user_id:
                results.append(icon)
                break
        else:
            if handle:
                authors = icon.get("authors", {})
                handles = authors.get("handles", [])
                
                if any(h.lower() == handle for h in handles):
                    results.append(icon)
    
    return results


def find_plugins_by_handles(handles: Iterable[str]) -> List[CatalogEntry]:
    normalized: Set[str] = {_normalize_handle(h) for h in handles if h}
    normalized.discard("")
    if not normalized:
        return []

    results: List[CatalogEntry] = []
    for plugin in _load_plugins():
        authors = plugin.get("authors", {})
        raw_blocks = plugin.get("raw_blocks", {}) or {}
        raw_ru = raw_blocks.get("ru") if isinstance(raw_blocks.get("ru"), dict) else {}
        raw_en = raw_blocks.get("en") if isinstance(raw_blocks.get("en"), dict) else {}
        haystack = " ".join(
            filter(
                None,
                [
                    authors.get("ru"),
                    authors.get("en"),
                    raw_ru.get("author") or "",
                    raw_en.get("author") or "",
                    raw_ru.get("author_channel") or "",
                    raw_en.get("author_channel") or "",
                ],
            )
        ).lower()
        for needle in normalized:
            if needle and needle in haystack:
                results.append(plugin)
                break
    return results


def find_icons_by_handles(handles: Iterable[str]) -> List[CatalogEntry]:
    normalized: Set[str] = {_normalize_handle(h) for h in handles if h}
    normalized.discard("")
    if not normalized:
        return []

    results: List[CatalogEntry] = []
    for icon in _load_icons():
        authors = icon.get("authors", {})
        raw_blocks = icon.get("raw_blocks", {})
        haystack = " ".join(
            filter(
                None,
                [
                    authors.get("ru"),
                    authors.get("en"),
                    raw_blocks.get("ru") if isinstance(raw_blocks.get("ru"), str) else "",
                    raw_blocks.get("en") if isinstance(raw_blocks.get("en"), str) else "",
                ],
            )
        ).lower()
        for needle in normalized:
            if needle and needle in haystack:
                results.append(icon)
                break
    return results
