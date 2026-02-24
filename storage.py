import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"


def _load_data_dir_from_config() -> Optional[Path]:
    if not CONFIG_PATH.exists():
        return None
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            config = json.load(file)
    except Exception:
        return None
    data_dir = config.get("storage", {}).get("data_dir")
    if not data_dir:
        return None
    path = Path(data_dir)
    if not path.is_absolute():
        path = CONFIG_PATH.parent / path
    return path


DATA_DIR = Path(
    os.environ.get("DATA_DIR")
    or str(_load_data_dir_from_config() or "/app/data")
)

DATABASE_PLUGINS_PATH = DATA_DIR / "databaseplugins.json"
DATABASE_ICONS_PATH = DATA_DIR / "databaseicons.json"
DATABASE_REQUESTS_PATH = DATA_DIR / "databaserequests.json"
DATABASE_USERS_PATH = DATA_DIR / "databaseusers.json"
DATABASE_SUBSCRIPTIONS_PATH = DATA_DIR / "databasesubscriptions.json"
DATABASE_UPDATED_PATH = DATA_DIR / "databaseupdated.json"

_cache: Dict[str, Dict[str, Any]] = {}
_cache_time: Dict[str, float] = {}
_dirty: Dict[str, bool] = {}
_save_locks: Dict[str, asyncio.Lock] = {}
_TTL = 30.0
_SAVE_INTERVAL = 3.0
_last_save: Dict[str, float] = {}


class StorageError(RuntimeError):
    pass


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_json_sync(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _write_json_sync(path: Path, data: Dict[str, Any]) -> None:
    _ensure_data_dir()
    data.setdefault("updated_at", datetime.utcnow().isoformat())
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_cache_key(path: Path) -> str:
    return str(path)


def _get_cached(path: Path, ttl: float = _TTL) -> Dict[str, Any]:
    key = _get_cache_key(path)
    now = time.time()
    
    if key in _cache and (now - _cache_time.get(key, 0)) < ttl:
        return _cache[key]
    
    data = _read_json_sync(path)
    _cache[key] = data
    _cache_time[key] = now
    return data


def _set_cached(path: Path, data: Dict[str, Any]) -> None:
    key = _get_cache_key(path)
    _cache[key] = data
    _cache_time[key] = time.time()
    _dirty[key] = True


async def _schedule_save(path: Path) -> None:
    key = _get_cache_key(path)
    now = time.time()
    
    if now - _last_save.get(key, 0) < _SAVE_INTERVAL:
        return
    
    _last_save[key] = now
    
    if key not in _save_locks:
        _save_locks[key] = asyncio.Lock()
    
    async with _save_locks[key]:
        if key in _cache and _dirty.get(key):
            data = _cache[key].copy()
            await asyncio.to_thread(_write_json_sync, path, data)
            _dirty[key] = False


def _save_sync(path: Path, data: Dict[str, Any]) -> None:
    _set_cached(path, data)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_schedule_save(path))
    except RuntimeError:
        _write_json_sync(path, data)
        _dirty[_get_cache_key(path)] = False


def invalidate_cache(path: Optional[Path] = None) -> None:
    if path:
        key = _get_cache_key(path)
        _cache.pop(key, None)
        _cache_time.pop(key, None)
    else:
        _cache.clear()
        _cache_time.clear()


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise StorageError(f"Config not found: {CONFIG_PATH}")
    return _get_cached(CONFIG_PATH, ttl=300)


def save_config(data: Dict[str, Any]) -> None:
    _save_sync(CONFIG_PATH, data)


def load_plugins() -> Dict[str, Any]:
    _ensure_data_dir()
    if not DATABASE_PLUGINS_PATH.exists():
        return {"plugins": []}
    return _get_cached(DATABASE_PLUGINS_PATH)


def save_plugins(data: Dict[str, Any]) -> None:
    _save_sync(DATABASE_PLUGINS_PATH, data)


def load_icons() -> Dict[str, Any]:
    _ensure_data_dir()
    if not DATABASE_ICONS_PATH.exists():
        return {"iconpacks": []}
    return _get_cached(DATABASE_ICONS_PATH)


def save_icons(data: Dict[str, Any]) -> None:
    _save_sync(DATABASE_ICONS_PATH, data)


def load_requests() -> Dict[str, Any]:
    _ensure_data_dir()
    if not DATABASE_REQUESTS_PATH.exists():
        return {"requests": []}
    return _get_cached(DATABASE_REQUESTS_PATH)


def save_requests(data: Dict[str, Any]) -> None:
    _save_sync(DATABASE_REQUESTS_PATH, data)


def load_users() -> Dict[str, Any]:
    _ensure_data_dir()
    if not DATABASE_USERS_PATH.exists():
        return {"users": {}}
    return _get_cached(DATABASE_USERS_PATH)


def save_users(data: Dict[str, Any]) -> None:
    _save_sync(DATABASE_USERS_PATH, data)


def load_subscriptions() -> Dict[str, Any]:
    _ensure_data_dir()
    if not DATABASE_SUBSCRIPTIONS_PATH.exists():
        return {"subscriptions": {}}
    return _get_cached(DATABASE_SUBSCRIPTIONS_PATH)


def save_subscriptions(data: Dict[str, Any]) -> None:
    _save_sync(DATABASE_SUBSCRIPTIONS_PATH, data)


def load_updated() -> Dict[str, Any]:
    _ensure_data_dir()
    if not DATABASE_UPDATED_PATH.exists():
        return {"items": [], "seeded": False}
    data = _get_cached(DATABASE_UPDATED_PATH)
    if not isinstance(data.get("items"), list):
        data["items"] = []
    if "seeded" not in data:
        data["seeded"] = False
    return data


def save_updated(data: Dict[str, Any]) -> None:
    _save_sync(DATABASE_UPDATED_PATH, data)


async def flush_all() -> None:
    for path_str, is_dirty in list(_dirty.items()):
        if is_dirty and path_str in _cache:
            path = Path(path_str)
            data = _cache[path_str].copy()
            await asyncio.to_thread(_write_json_sync, path, data)
            _dirty[path_str] = False


async def preload_storage() -> None:
    _ensure_data_dir()
    
    paths = [
        DATABASE_PLUGINS_PATH,
        DATABASE_ICONS_PATH,
        DATABASE_REQUESTS_PATH,
        DATABASE_USERS_PATH,
        DATABASE_SUBSCRIPTIONS_PATH,
        DATABASE_UPDATED_PATH,
    ]
    
    for path in paths:
        if path.exists():
            await asyncio.to_thread(_get_cached, path)
