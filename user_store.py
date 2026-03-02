import asyncio
import time
from typing import Any, Dict, List, Optional

from storage import load_users, save_users

_users_cache: Dict[str, Dict[str, Any]] = {}
_cache_loaded: bool = False
_cache_lock = asyncio.Lock()
_dirty: bool = False
_last_save: float = 0
_SAVE_INTERVAL = 5.0


def _load_from_storage() -> Dict[str, Any]:
    try:
        data = load_users()
    except Exception:
        data = {"users": {}}
    if not isinstance(data, dict):
        return {"users": {}}
    users = data.get("users")
    if not isinstance(users, dict):
        data["users"] = {}
    return data


def _save_to_storage_sync(data: Dict[str, Any]) -> None:
    payload = data if isinstance(data, dict) else {"users": {}}
    users = payload.get("users")
    if not isinstance(users, dict):
        payload = {"users": {}}
    save_users(payload)


async def _ensure_loaded() -> None:
    global _users_cache, _cache_loaded

    if _cache_loaded:
        return

    async with _cache_lock:
        if _cache_loaded:
            return

        data = await asyncio.to_thread(_load_from_storage)
        _users_cache = data.get("users", {})
        _cache_loaded = True


async def _schedule_save() -> None:
    global _dirty, _last_save

    _dirty = True
    now = time.time()

    if now - _last_save < _SAVE_INTERVAL:
        return

    _last_save = now
    _dirty = False

    data = {"users": _users_cache.copy()}
    await asyncio.to_thread(_save_to_storage_sync, data)


def _ensure_loaded_sync() -> None:
    global _users_cache, _cache_loaded
    if not _cache_loaded:
        data = _load_from_storage()
        _users_cache = data.get("users", {})
        _cache_loaded = True


def get_user_language(user_id: int) -> Optional[str]:
    _ensure_loaded_sync()
    return _users_cache.get(str(user_id), {}).get("language")


def get_user(user_id: int) -> Dict[str, Any]:
    _ensure_loaded_sync()
    return _users_cache.get(str(user_id), {}).copy()


def is_broadcast_enabled(user_id: int) -> bool:
    user = get_user(user_id)
    # Default: enabled
    return bool(user.get("broadcast_enabled", True))


def has_paid_broadcast_disable(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user.get("broadcast_paid", False))


def set_broadcast_enabled(user_id: int, enabled: bool) -> None:
    update_user(user_id, broadcast_enabled=bool(enabled))


def set_paid_broadcast_disable(user_id: int, paid: bool) -> None:
    update_user(user_id, broadcast_paid=bool(paid))


def set_user_language(user_id: int, language: str) -> None:
    _ensure_loaded_sync()

    user_key = str(user_id)
    if user_key not in _users_cache:
        _users_cache[user_key] = {}

    _users_cache[user_key]["language"] = language.lower()

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_schedule_save())
    except RuntimeError:
        _save_to_storage_sync({"users": _users_cache})


def update_user(user_id: int, **fields: Any) -> None:
    _ensure_loaded_sync()

    user_key = str(user_id)
    if user_key not in _users_cache:
        _users_cache[user_key] = {}

    _users_cache[user_key].update(fields)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_schedule_save())
    except RuntimeError:
        _save_to_storage_sync({"users": _users_cache})


def is_user_banned(user_id: int) -> bool:
    return get_user(user_id).get("banned", False)


def ban_user(user_id: int, reason: str = "") -> None:
    update_user(user_id, banned=True, ban_reason=reason)


def unban_user(user_id: int) -> None:
    _ensure_loaded_sync()

    user_key = str(user_id)
    if user_key in _users_cache:
        _users_cache[user_key]["banned"] = False
        _users_cache[user_key].pop("ban_reason", None)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_schedule_save())
        except RuntimeError:
            _save_to_storage_sync({"users": _users_cache})


def get_banned_users() -> List[Dict[str, Any]]:
    _ensure_loaded_sync()
    return [
        {"user_id": int(uid), **data}
        for uid, data in _users_cache.items()
        if data.get("banned")
    ]


def list_users() -> List[Dict[str, Any]]:
    _ensure_loaded_sync()
    return [{"user_id": int(uid), **data} for uid, data in _users_cache.items()]


async def init_user_store() -> None:
    await _ensure_loaded()


async def flush_user_store() -> None:
    if _dirty or _users_cache:
        data = {"users": _users_cache.copy()}
        await asyncio.to_thread(_save_to_storage_sync, data)
