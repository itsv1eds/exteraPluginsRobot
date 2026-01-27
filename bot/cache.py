import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

_cache: Dict[str, Any] = {}
_cache_time: Dict[str, float] = {}
_TTL = 30.0

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def _get_cached(key: str, loader: callable, ttl: float = _TTL) -> Any:
    now = time.time()
    if key in _cache and (now - _cache_time.get(key, 0)) < ttl:
        return _cache[key]
    
    data = loader()
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


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_config() -> Dict[str, Any]:
    return _get_cached("config", lambda: _load_json(ROOT / "config.json"), ttl=300)


def get_plugins() -> List[Dict[str, Any]]:
    data = _get_cached("plugins", lambda: _load_json(DATA_DIR / "databaseplugins.json"))
    return data.get("plugins", [])


def get_icons() -> List[Dict[str, Any]]:
    data = _get_cached("icons", lambda: _load_json(DATA_DIR / "databaseicons.json"))
    return data.get("iconpacks", [])


def get_categories() -> List[Dict[str, Any]]:
    return get_config().get("categories", [])


def get_admins() -> set:
    return set(get_config().get("admins", []))


def get_channel_config() -> Dict[str, Any]:
    return get_config().get("channel", {})