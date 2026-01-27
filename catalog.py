import random
from typing import Any, Dict, Iterable, List, Optional, Set

from storage import load_icons, load_plugins


CatalogEntry = Dict[str, Any]


def _load_plugins() -> List[CatalogEntry]:
    database = load_plugins()
    return database.get("plugins", [])


def _load_icons() -> List[CatalogEntry]:
    database = load_icons()
    return database.get("iconpacks", [])


def list_published_plugins(limit: Optional[int] = None) -> List[CatalogEntry]:
    entries = [plugin for plugin in _load_plugins() if plugin.get("status") == "published"]
    if limit is not None:
        return entries[:limit]
    return entries


def list_published_icons(limit: Optional[int] = None) -> List[CatalogEntry]:
    entries = [icon for icon in _load_icons() if icon.get("status") == "published"]
    if limit is not None:
        return entries[:limit]
    return entries


def list_plugins_by_category(category_key: str) -> List[CatalogEntry]:
    entries = list_published_plugins()
    if category_key in (None, "", "_all"):
        return entries
    return [
        plugin
        for plugin in entries
        if plugin.get("category") == category_key
    ]


def list_icons_by_category(category_key: str) -> List[CatalogEntry]:
    entries = list_published_icons()
    if category_key in (None, "", "_all"):
        return entries
    return [
        icon
        for icon in entries
        if icon.get("category") == category_key
    ]


def search_plugins(query: str, limit: int = 10) -> List[CatalogEntry]:
    normalized = query.strip().lower()
    entries = list_published_plugins()
    if not normalized:
        random.shuffle(entries)
        return entries[:limit]

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

    results = [plugin for plugin in entries if matches(plugin)]
    random.shuffle(results)
    return results[:limit]


def search_icons(query: str, limit: int = 10) -> List[CatalogEntry]:
    normalized = query.strip().lower()
    entries = list_published_icons()
    if not normalized:
        random.shuffle(entries)
        return entries[:limit]

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

    results = [icon for icon in entries if matches(icon)]
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
    for plugin in _load_plugins():
        if _normalize_slug(plugin.get("slug")) == target:
            return plugin
    return None


def find_icon_by_slug(slug: Optional[str]) -> Optional[CatalogEntry]:
    target = _normalize_slug(slug)
    if not target:
        return None
    for icon in _load_icons():
        if _normalize_slug(icon.get("slug")) == target:
            return icon
    return None


def find_user_plugins(user_id: int, username: str = "") -> List[CatalogEntry]:
    results = []
    
    for plugin in list_published_plugins():
        submitters = plugin.get("submitters", [])
        for sub in submitters:
            if sub.get("user_id") == user_id:
                results.append(plugin)
                break
        else:
            if username:
                handle = f"@{username.lower()}"
                authors = plugin.get("authors", {})
                handles = authors.get("handles", [])
                
                if any(h.lower() == handle for h in handles):
                    results.append(plugin)
                    continue
                
                for locale in ("ru", "en"):
                    author_text = (authors.get(locale) or "").lower()
                    if handle in author_text:
                        results.append(plugin)
                        break
    
    return results


def find_user_icons(user_id: int, username: str = "") -> List[CatalogEntry]:
    results = []
    
    for icon in list_published_icons():
        submitters = icon.get("submitters", [])
        for sub in submitters:
            if sub.get("user_id") == user_id:
                results.append(icon)
                break
        else:
            if username:
                handle = f"@{username.lower()}"
                authors = icon.get("authors", {})
                handles = authors.get("handles", [])
                
                if any(h.lower() == handle for h in handles):
                    results.append(icon)
    
    return results


def find_plugins_by_handles(handles: Iterable[str]) -> List[CatalogEntry]:
    normalized: Set[str] = {
        _normalize_handle(handle) for handle in handles if handle
    }
    normalized.discard("")
    if not normalized:
        return []

    results: List[CatalogEntry] = []
    for plugin in _load_plugins():
        authors = plugin.get("authors", {})
        raw_blocks = plugin.get("raw_blocks", {})
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
                results.append(plugin)
                break
    return results


def find_icons_by_handles(handles: Iterable[str]) -> List[CatalogEntry]:
    normalized: Set[str] = {
        _normalize_handle(handle) for handle in handles if handle
    }
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