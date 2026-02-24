import random
from typing import Any, Dict, Iterable, List, Optional, Set

from storage import load_icons, load_plugins

CatalogEntry = Dict[str, Any]

_plugins_cache: Optional[List[CatalogEntry]] = None
_icons_cache: Optional[List[CatalogEntry]] = None
_published_plugins_cache: Optional[List[CatalogEntry]] = None
_published_icons_cache: Optional[List[CatalogEntry]] = None
_slug_index: Dict[str, CatalogEntry] = {}
_icon_slug_index: Dict[str, CatalogEntry] = {}


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
    
    _published_plugins_cache = [p for p in _load_plugins() if p.get("status") == "published"]
    return _published_plugins_cache


def _get_published_icons() -> List[CatalogEntry]:
    global _published_icons_cache
    if _published_icons_cache is not None:
        return _published_icons_cache
    
    _published_icons_cache = [i for i in _load_icons() if i.get("status") == "published"]
    return _published_icons_cache


def list_published_plugins(limit: Optional[int] = None) -> List[CatalogEntry]:
    entries = _get_published_plugins()
    if limit is not None:
        return entries[:limit]
    return entries


def list_published_icons(limit: Optional[int] = None) -> List[CatalogEntry]:
    entries = _get_published_icons()
    if limit is not None:
        return entries[:limit]
    return entries


def list_plugins_by_category(category_key: str) -> List[CatalogEntry]:
    entries = _get_published_plugins()
    if category_key in (None, "", "_all"):
        return entries
    return [p for p in entries if p.get("category") == category_key]


def list_icons_by_category(category_key: str) -> List[CatalogEntry]:
    entries = _get_published_icons()
    if category_key in (None, "", "_all"):
        return entries
    return [i for i in entries if i.get("category") == category_key]


def search_plugins(query: str, limit: int = 10) -> List[CatalogEntry]:
    normalized = query.strip().lower()
    entries = _get_published_plugins()
    
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
