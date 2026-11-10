import difflib
import hashlib
import random
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

from core.storage import load_icons, load_plugins
from core.categories import categories_for, normalize_categories

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
_official_plugin_id_index: set[str] = set()


def _is_legacy_external(plugin: CatalogEntry) -> bool:
    source = plugin.get("source")
    return bool(plugin.get("external") or
                (isinstance(source, dict) and str(source.get("type") or "").strip().lower() == "external"))


def invalidate_catalog_cache() -> None:
    global _plugins_cache, _icons_cache, _published_plugins_cache, _published_icons_cache
    _plugins_cache = None
    _icons_cache = None
    _published_plugins_cache = None
    _published_icons_cache = None
    _slug_index.clear()
    _icon_slug_index.clear()
    _official_plugin_id_index.clear()


def _load_plugins() -> List[CatalogEntry]:
    global _plugins_cache
    if _plugins_cache is not None:
        return _plugins_cache
    
    database = load_plugins()
    _plugins_cache = [plugin for plugin in database.get("plugins", [])
                      if isinstance(plugin, dict) and not _is_legacy_external(plugin)]
    
    _slug_index.clear()
    _official_plugin_id_index.clear()
    for plugin in _plugins_cache:
        plugin["categories"] = categories_for(plugin)
        plugin["category"] = (plugin["categories"] or [None])[0]
        slug = _normalize_slug(plugin.get("slug"))
        if slug:
            _slug_index[slug] = plugin
        if plugin.get("status") == "published":
            localized = plugin.get("ru") if isinstance(plugin.get("ru"), dict) else {}
            plugin_id = str(plugin.get("plugin_id") or localized.get("id") or plugin.get("slug") or "").strip().lower()
            if plugin_id:
                _official_plugin_id_index.add(plugin_id)
    
    return _plugins_cache


def _load_icons() -> List[CatalogEntry]:
    global _icons_cache
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
    selected = normalize_categories(category_key)
    return [p for p in entries if any(key in categories_for(p) for key in selected)]


_RU_LAYOUT = "йцукенгшщзхъфывапролджэячсмитьбю"
_EN_LAYOUT = "qwertyuiop[]asdfghjkl;'zxcvbnm,."
_TO_EN = str.maketrans(_RU_LAYOUT, _EN_LAYOUT)
_TO_RU = str.maketrans(_EN_LAYOUT, _RU_LAYOUT)


def _fold(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def _query_variants(query: str) -> List[str]:
    base = _fold(query).lstrip("@#")
    variants = [base, base.translate(_TO_EN), base.translate(_TO_RU)]
    return [value for value in dict.fromkeys(variants) if value]


def _search_fields(entry: CatalogEntry, extra) -> tuple[List[str], str]:
    names = [entry.get("slug"), entry.get("plugin_id")]
    details = []
    for locale in ("ru", "en"):
        locale_data = entry.get(locale, {}) or {}
        names += [locale_data.get("name"), locale_data.get("id")]
        details += [locale_data.get("description"), locale_data.get("usage")]
    authors = entry.get("authors") or {}
    details += [authors.get("ru"), authors.get("en"), *(authors.get("handles") or [])]
    if extra:
        details += extra(entry)
    return [_fold(name) for name in names if name], _fold(" ".join(str(item) for item in details if item))


def _search(entries: List[CatalogEntry], query: str, limit: int, extra=None) -> List[CatalogEntry]:
    variants = _query_variants(query)
    if not variants:
        result = entries.copy()
        random.shuffle(result)
        return [(entry, 0) for entry in result[:limit]]

    ranked = []
    fields = []
    for position, entry in enumerate(entries):
        names, details = _search_fields(entry, extra)
        fields.append((names, entry))
        rank = None
        for variant in variants:
            if any(name.startswith(variant) for name in names):
                rank = 0
            elif any(variant in name for name in names):
                rank = 1 if rank is None else min(rank, 1)
            elif all(word in " ".join(names) + " " + details for word in variant.split()):
                rank = 2 if rank is None else min(rank, 2)
            if rank == 0:
                break
        if rank is not None:
            ranked.append((rank, position, entry))
    if not ranked:
        lookup = {}
        for names, entry in fields:
            for name in names:
                lookup.setdefault(name, entry)
        close = difflib.get_close_matches(variants[0], list(lookup), n=limit, cutoff=0.6)
        seen = set()
        for position, name in enumerate(close):
            entry = lookup[name]
            if id(entry) not in seen:
                seen.add(id(entry))
                ranked.append((3, position, entry))
    ranked.sort(key=lambda item: item[:2])
    return [(entry, rank) for rank, _, entry in ranked[:limit]]


def search_plugins_ranked(query: str, limit: int = 10) -> tuple[List[CatalogEntry], bool]:
    found = _search(_get_published_plugins(), query, limit, categories_for)
    return [entry for entry, _ in found], bool(found) and all(rank == 3 for _, rank in found)


def search_plugins(query: str, limit: int = 10) -> List[CatalogEntry]:
    return search_plugins_ranked(query, limit)[0]


def search_icons(query: str, limit: int = 10) -> List[CatalogEntry]:
    return [entry for entry, _ in _search(_get_published_icons(), query, limit)]


def _normalize_slug(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def find_plugin_by_slug(slug: Optional[str]) -> Optional[CatalogEntry]:
    target = _normalize_slug(slug)
    if not target:
        return None
    
    _load_plugins()
    return _slug_index.get(target)


def has_official_plugin_id(plugin_id: Optional[str]) -> bool:
    target = str(plugin_id or "").strip().lower()
    if not target:
        return False
    _load_plugins()
    return target in _official_plugin_id_index


def find_icon_by_slug(slug: Optional[str]) -> Optional[CatalogEntry]:
    target = _normalize_slug(slug)
    if not target:
        return None
    
    _load_icons()
    return _icon_slug_index.get(target)


@lru_cache(maxsize=512)
def _handle_pattern(handle: str) -> re.Pattern:
    return re.compile(rf"(?<![\w@]){re.escape(handle)}(?!\w)")


def _mentions_handle(text: Any, handle: str) -> bool:
    return bool(text) and handle in (lowered := str(text).lower()) and bool(_handle_pattern(handle).search(lowered))


def find_user_plugins(user_id: int, username: str = "") -> List[CatalogEntry]:
    results = []
    handle = f"@{username.lower()}" if username else ""

    for plugin in _get_published_plugins():
        if any(sub.get("user_id") == user_id for sub in plugin.get("submitters", [])):
            results.append(plugin)
            continue
        if not handle:
            continue
        authors = plugin.get("authors", {})
        if any(str(item).lower() == handle for item in authors.get("handles", [])):
            results.append(plugin)
            continue
        raw_blocks = plugin.get("raw_blocks", {}) or {}
        candidates = [authors.get("ru"), authors.get("en")]
        for locale in ("ru", "en"):
            block = raw_blocks.get(locale) if isinstance(raw_blocks.get(locale), dict) else {}
            candidates += [block.get("author"), block.get("author_channel")]
        if any(_mentions_handle(text, handle) for text in candidates):
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
