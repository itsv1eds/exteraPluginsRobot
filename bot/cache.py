import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from storage import load_icons, load_plugins
from bot.texts import t

_cache: Dict[str, Any] = {}
_cache_time: Dict[str, float] = {}
_TTL = 30.0
_lock = asyncio.Lock()

ROOT = Path(__file__).resolve().parent.parent


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _get_cached_sync(key: str, loader: Callable, ttl: float = _TTL) -> Any:
    now = time.time()
    if key in _cache and (now - _cache_time.get(key, 0)) < ttl:
        return _cache[key]
    
    data = loader()
    _cache[key] = data
    _cache_time[key] = now
    return data


async def _get_cached_async(key: str, loader: Callable, ttl: float = _TTL) -> Any:
    now = time.time()
    
    if key in _cache and (now - _cache_time.get(key, 0)) < ttl:
        return _cache[key]
    
    async with _lock:
        if key in _cache and (now - _cache_time.get(key, 0)) < ttl:
            return _cache[key]
        
        data = await asyncio.to_thread(loader)
        _cache[key] = data
        _cache_time[key] = now
        return data


def invalidate(key: Optional[str] = None) -> None:
    if key:
        _cache.pop(key, None)
        _cache_time.pop(key, None)
    else:
        _cache.clear()
        _cache_time.clear()


def get_config() -> Dict[str, Any]:
    return _get_cached_sync("config", lambda: _load_json(ROOT / "config.json"), ttl=300)


def get_plugins() -> List[Dict[str, Any]]:
    data = _get_cached_sync("plugins", load_plugins)
    return data.get("plugins", [])


def get_icons() -> List[Dict[str, Any]]:
    data = _get_cached_sync("icons", load_icons)
    return data.get("iconpacks", [])


def get_categories() -> List[Dict[str, Any]]:
    keys = [
        ("informational", "5208833059805238499"),
        ("utilities", "5208908006984563084"),
        ("customization", "5208480086507952450"),
        ("fun", "5208648268837324812"),
        ("library", "5208481645581079281"),
    ]
    categories: List[Dict[str, Any]] = []
    for key, emoji_id in keys:
        categories.append(
            {
                "key": key,
                "ru": t(f"category_{key}_label_btn", "ru"),
                "en": t(f"category_{key}_label_btn", "en"),
                "tag_ru": t(f"category_{key}_tag", "ru"),
                "tag_en": t(f"category_{key}_tag", "en"),
                "emoji_id": emoji_id,
            }
        )
    return categories


def _get_admin_list(key: str) -> set:
    return set(get_config().get(key, []))


def get_admins_super() -> set:
    return _get_admin_list("admins_super")


def get_admins_plugins() -> set:
    fallback = set(get_config().get("admins", []))
    return get_admins_super() | _get_admin_list("admins_plugins") | fallback


def get_admins_icons() -> set:
    fallback = set(get_config().get("admins", []))
    return get_admins_super() | _get_admin_list("admins_icons") | fallback


def get_admins() -> set:
    fallback = set(get_config().get("admins", []))
    return get_admins_super() | _get_admin_list("admins_plugins") | _get_admin_list("admins_icons") | fallback


def get_admin_role(user_id: int) -> Optional[str]:
    if user_id in get_admins_super():
        return "super"
    if user_id in _get_admin_list("admins_plugins"):
        return "plugins"
    if user_id in _get_admin_list("admins_icons"):
        return "icons"
    if user_id in get_admins():
        return "super"
    return None


def get_channel_config() -> Dict[str, Any]:
    return get_config().get("channel", {})


async def get_config_async() -> Dict[str, Any]:
    return await _get_cached_async("config", lambda: _load_json(ROOT / "config.json"), ttl=300)


async def get_plugins_async() -> List[Dict[str, Any]]:
    data = await _get_cached_async("plugins", load_plugins)
    return data.get("plugins", [])


async def get_icons_async() -> List[Dict[str, Any]]:
    data = await _get_cached_async("icons", load_icons)
    return data.get("iconpacks", [])


async def get_admins_async() -> set:
    config = await get_config_async()
    fallback = set(config.get("admins", []))
    return set(config.get("admins_super", [])) | set(config.get("admins_plugins", [])) | set(config.get("admins_icons", [])) | fallback


async def preload_cache() -> None:
    await get_config_async()
    await get_plugins_async()
    await get_icons_async()
