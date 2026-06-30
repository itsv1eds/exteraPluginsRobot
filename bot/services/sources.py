
from __future__ import annotations

from typing import Any, Dict, List, Optional

from storage import load_config, save_config, load_plugins, save_plugins
from bot.cache import invalidate
from catalog import invalidate_catalog_cache, plugin_source_filter_key


def _norm_id(value: Any) -> str:
    return str(value or "").strip().lstrip("@").lower()


def load_custom_sources() -> List[Dict[str, Any]]:
    cfg = load_config()
    sources = cfg.get("custom_sources")
    return [s for s in sources if isinstance(s, dict)] if isinstance(sources, list) else []


def get_custom_source(source_id: str) -> Optional[Dict[str, Any]]:
    sid = _norm_id(source_id)
    for source in load_custom_sources():
        if _norm_id(source.get("id") or source.get("username")) == sid:
            return source
    return None


def add_custom_source(username: str, title: str = "", link: str = "") -> Optional[Dict[str, Any]]:
    sid = _norm_id(username)
    if not sid:
        return None
    entry = {
        "type": "external",
        "id": sid,
        "username": sid,
        "title": (title or "").strip() or sid,
        "link": (link or "").strip() or f"https://t.me/{sid}",
    }
    cfg = load_config()
    sources = [s for s in load_custom_sources()
               if _norm_id(s.get("id") or s.get("username")) != sid]
    sources.append(entry)
    cfg["custom_sources"] = sources
    save_config(cfg)
    invalidate("config")
    return entry


def delete_custom_source(source_id: str) -> bool:
    sid = _norm_id(source_id)
    cfg = load_config()
    current = load_custom_sources()
    remaining = [s for s in current if _norm_id(s.get("id") or s.get("username")) != sid]
    if len(remaining) == len(current):
        return False
    cfg["custom_sources"] = remaining
    save_config(cfg)
    invalidate("config")
    return True


def count_source_plugins(source_id: str) -> int:
    sid = _norm_id(source_id)
    plugins = load_plugins().get("plugins", []) or []
    return sum(1 for p in plugins if isinstance(p, dict) and plugin_source_filter_key(p) == sid)


def _match_plugin(plugin: Dict[str, Any], target: str) -> bool:
    target = target.strip().lower()
    if str(plugin.get("slug") or "").lower() == target:
        return True
    for loc in ("ru", "en"):
        block = plugin.get(loc)
        if isinstance(block, dict) and str(block.get("id") or "").lower() == target:
            return True
    return False


def attach_plugin_to_source(plugin_ref: str, source: Dict[str, Any]) -> Optional[str]:
    db = load_plugins()
    plugins = db.get("plugins", [])
    for plugin in plugins:
        if isinstance(plugin, dict) and _match_plugin(plugin, plugin_ref):
            plugin["source"] = dict(source)
            save_plugins(db)
            invalidate("plugins")
            invalidate_catalog_cache()
            return plugin.get("slug")
    return None
