from typing import Any, Dict, List, Optional

from storage import load_users, save_users


def _ensure_users_container(database: Dict[str, Any]) -> Dict[str, Any]:
    return database.setdefault("users", {})


def get_user(user_id: int) -> Dict[str, Any]:
    database = load_users()
    users = database.get("users", {})
    return users.get(str(user_id), {}).copy()


def get_user_language(user_id: int) -> Optional[str]:
    user = get_user(user_id)
    return user.get("language")


def set_user_language(user_id: int, language: str) -> None:
    database = load_users()
    users = _ensure_users_container(database)
    entry = users.setdefault(str(user_id), {})
    entry["language"] = language.lower()
    save_users(database)


def update_user(user_id: int, **fields: Any) -> None:
    database = load_users()
    users = _ensure_users_container(database)
    entry = users.setdefault(str(user_id), {})
    entry.update(fields)
    save_users(database)


def is_user_banned(user_id: int) -> bool:
    user = get_user(user_id)
    return user.get("banned", False)


def ban_user(user_id: int, reason: str = "") -> None:
    database = load_users()
    users = _ensure_users_container(database)
    entry = users.setdefault(str(user_id), {})
    entry["banned"] = True
    entry["ban_reason"] = reason
    save_users(database)


def unban_user(user_id: int) -> None:
    database = load_users()
    users = _ensure_users_container(database)
    entry = users.get(str(user_id), {})
    entry["banned"] = False
    entry.pop("ban_reason", None)
    users[str(user_id)] = entry
    save_users(database)


def get_banned_users() -> List[Dict[str, Any]]:
    database = load_users()
    users = database.get("users", {})
    banned = []
    for uid, data in users.items():
        if data.get("banned"):
            banned.append({"user_id": int(uid), **data})
    return banned


def list_users() -> List[Dict[str, Any]]:
    database = load_users()
    users = database.get("users", {})
    return [{"user_id": int(uid), **data} for uid, data in users.items()]