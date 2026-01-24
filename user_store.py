from typing import Any, Dict, Optional

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
    language = language.lower()
    database = load_users()
    users = _ensure_users_container(database)
    entry = users.setdefault(str(user_id), {})
    entry["language"] = language
    save_users(database)


def update_user(user_id: int, **fields: Any) -> None:
    database = load_users()
    users = _ensure_users_container(database)
    entry = users.setdefault(str(user_id), {})
    entry.update(fields)
    save_users(database)
