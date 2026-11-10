import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

from core.storage import flush_documents, load_users, save_users

_users_cache: Dict[str, Dict[str, Any]] = {}
_cache_loaded: bool = False
_cache_lock = asyncio.Lock()
_save_lock = asyncio.Lock()
_dirty: bool = False
_last_save: float = 0
_pending_save: bool = False
_SAVE_INTERVAL = 5.0
_background_tasks: set = set()
logger = logging.getLogger(__name__)


def _on_background_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background user-store save failed",
            exc_info=(type(exc), exc, exc.__traceback__),
        )


def _spawn(loop) -> None:
    task = loop.create_task(_schedule_save())
    _background_tasks.add(task)
    task.add_done_callback(_on_background_done)


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
    global _dirty, _last_save, _pending_save

    _dirty = True
    delay = _SAVE_INTERVAL - (time.time() - _last_save)
    if delay > 0:
        if _pending_save:
            return
        _pending_save = True
        try:
            await asyncio.sleep(delay)
        finally:
            _pending_save = False

    async with _save_lock:
        if not _dirty:
            return
        _last_save = time.time()
        _dirty = False
        _save_to_storage_sync({"users": _users_cache})
        await flush_documents("users")


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


def moderation_preferences(user_id: int) -> dict:
    raw = get_user(user_id).get("moderation_preferences")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "show_templates": raw.get("show_templates") is not False,
        "template_input": "reply" if raw.get("template_input") == "reply" else "buttons",
    }


def set_moderation_preference(user_id: int, key: str, value) -> None:
    if key == "show_templates":
        if type(value) is not bool:
            raise ValueError("show_templates must be a boolean")
    elif key == "template_input":
        if value not in ("buttons", "reply"):
            raise ValueError("template_input must be buttons or reply")
    else:
        raise ValueError("Unknown moderation preference")
    prefs = moderation_preferences(user_id)
    prefs[key] = value
    update_user(user_id, moderation_preferences=prefs)


def hidden_moderation_templates(user_id: int, kind: str) -> list[str]:
    raw = get_user(user_id).get("hidden_moderation_templates")
    values = raw.get(kind) if isinstance(raw, dict) else None
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(value for value in values
                             if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{20}", value)))


def set_moderation_template_hidden(user_id: int, kind: str, template_id: str, hidden: bool) -> None:
    if kind not in {"reject", "approve", "update_reject", "update_approve"}:
        raise ValueError("Unknown moderation template kind")
    if not isinstance(template_id, str) or not re.fullmatch(r"[0-9a-f]{20}", template_id) or type(hidden) is not bool:
        raise ValueError("Invalid moderation template preference")
    raw = get_user(user_id).get("hidden_moderation_templates")
    prefs = dict(raw) if isinstance(raw, dict) else {}
    values = hidden_moderation_templates(user_id, kind)
    if hidden and template_id not in values:
        values.append(template_id)
    elif not hidden and template_id in values:
        values.remove(template_id)
    prefs[kind] = values
    update_user(user_id, hidden_moderation_templates=prefs)


def is_broadcast_enabled(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user.get("broadcast_enabled", True))


def has_paid_broadcast_disable(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user.get("broadcast_paid", False))


def set_broadcast_enabled(user_id: int, enabled: bool) -> None:
    update_user(user_id, broadcast_enabled=bool(enabled))


def set_paid_broadcast_disable(user_id: int, paid: bool) -> None:
    update_user(user_id, broadcast_paid=bool(paid))


def set_user_language(user_id: int, language: str) -> None:
    from bot.languages import normalize_language

    _ensure_loaded_sync()

    user_key = str(user_id)
    if user_key not in _users_cache:
        _users_cache[user_key] = {}

    _users_cache[user_key]["language"] = normalize_language(language)

    try:
        loop = asyncio.get_running_loop()
        _spawn(loop)
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
        _spawn(loop)
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
        _users_cache[user_key].pop("ban_permanent", None)

        try:
            loop = asyncio.get_running_loop()
            _spawn(loop)
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


def language_counts() -> tuple[int, Dict[str, int]]:
    _ensure_loaded_sync()
    counts: Dict[str, int] = {}
    for data in _users_cache.values():
        key = str(data.get("language") or "unknown").lower()
        counts[key] = counts.get(key, 0) + 1
    return len(_users_cache), counts


async def init_user_store() -> None:
    await _ensure_loaded()


async def flush_user_store() -> None:
    global _dirty

    tasks = [task for task in _background_tasks if not task.done()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    if _dirty or _users_cache:
        _dirty = False
        _save_to_storage_sync({"users": _users_cache})
        await flush_documents("users")
