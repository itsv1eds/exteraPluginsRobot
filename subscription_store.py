from typing import Any, Dict, List

from storage import StorageError, load_subscriptions, save_subscriptions


def _load_db() -> Dict[str, Any]:
    try:
        return load_subscriptions()
    except StorageError:
        data: Dict[str, Any] = {"subscriptions": {}}
        save_subscriptions(data)
        return data


def _save_db(data: Dict[str, Any]) -> None:
    save_subscriptions(data)


def _get_user_key(user_id: int) -> str:
    return str(user_id)


def list_subscriptions(user_id: int) -> List[str]:
    db = _load_db()
    subs = db.get("subscriptions", {})
    return list(subs.get(_get_user_key(user_id), []))


def is_subscribed(user_id: int, slug: str) -> bool:
    return slug in list_subscriptions(user_id)


def add_subscription(user_id: int, slug: str) -> None:
    db = _load_db()
    subs = db.setdefault("subscriptions", {})
    user_subs = subs.setdefault(_get_user_key(user_id), [])
    if slug not in user_subs:
        user_subs.append(slug)
        _save_db(db)


def remove_subscription(user_id: int, slug: str) -> None:
    db = _load_db()
    subs = db.setdefault("subscriptions", {})
    user_key = _get_user_key(user_id)
    user_subs = subs.get(user_key, [])
    if slug in user_subs:
        user_subs.remove(slug)
        subs[user_key] = user_subs
        _save_db(db)


def list_subscribers(slug: str) -> List[int]:
    db = _load_db()
    subs = db.get("subscriptions", {})
    users = []
    for uid, user_subs in subs.items():
        if slug in (user_subs or []):
            try:
                users.append(int(uid))
            except ValueError:
                continue
    return users
