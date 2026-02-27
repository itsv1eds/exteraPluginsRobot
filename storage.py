import asyncio
import json
import os
import sqlite3
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"


def _load_storage_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            config = json.load(file)
    except Exception:
        return {}
    storage_cfg = config.get("storage", {})
    return storage_cfg if isinstance(storage_cfg, dict) else {}


def _resolve_path(value: Optional[str], fallback: Path) -> Path:
    if not value:
        return fallback
    path = Path(value)
    if not path.is_absolute():
        path = CONFIG_PATH.parent / path
    return path


_storage_cfg = _load_storage_config()

DATA_DIR = _resolve_path(
    os.environ.get("DATA_DIR") or _storage_cfg.get("data_dir"),
    Path("/app/data"),
)

SQLITE_PATH = _resolve_path(
    os.environ.get("SQLITE_PATH") or _storage_cfg.get("sqlite_path"),
    DATA_DIR / "storage.sqlite3",
)

DATABASE_PLUGINS_PATH = DATA_DIR / "databaseplugins.json"
DATABASE_ICONS_PATH = DATA_DIR / "databaseicons.json"
DATABASE_REQUESTS_PATH = DATA_DIR / "databaserequests.json"
DATABASE_USERS_PATH = DATA_DIR / "databaseusers.json"
DATABASE_SUBSCRIPTIONS_PATH = DATA_DIR / "databasesubscriptions.json"
DATABASE_UPDATED_PATH = DATA_DIR / "databaseupdated.json"
DATABASE_USERS_ALT_PATH = DATA_DIR / "users.json"

_DOC_PLUGINS = "plugins"
_DOC_ICONS = "icons"
_DOC_REQUESTS = "requests"
_DOC_USERS = "users"
_DOC_SUBSCRIPTIONS = "subscriptions"
_DOC_UPDATED = "updated"
_DOC_JOINLY = "joinly"

_LEGACY_PATHS = {
    _DOC_PLUGINS: [DATABASE_PLUGINS_PATH],
    _DOC_ICONS: [DATABASE_ICONS_PATH],
    _DOC_REQUESTS: [DATABASE_REQUESTS_PATH],
    _DOC_USERS: [DATABASE_USERS_ALT_PATH, DATABASE_USERS_PATH],
    _DOC_SUBSCRIPTIONS: [DATABASE_SUBSCRIPTIONS_PATH],
    _DOC_UPDATED: [DATABASE_UPDATED_PATH],
}

_cache: Dict[str, Dict[str, Any]] = {}
_cache_time: Dict[str, float] = {}
_dirty: Dict[str, bool] = {}
_save_locks: Dict[str, asyncio.Lock] = {}
_last_save: Dict[str, float] = {}

_TTL = 30.0
_SAVE_INTERVAL = 3.0
_CONFIG_TTL = 300.0

_config_cache: Optional[Dict[str, Any]] = None
_config_cache_time: float = 0.0

_db_lock = threading.Lock()
_db_ready = False


class StorageError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _meta_key(doc_key: str) -> str:
    return f"meta:{doc_key}"


def _init_key(doc_key: str) -> str:
    return f"init:{doc_key}"


def _get_meta_value(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta_store WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta_value(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta_store (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, _now_iso()),
    )


def _get_meta_json(conn: sqlite3.Connection, key: str, default: Dict[str, Any]) -> Dict[str, Any]:
    raw = _get_meta_value(conn, key)
    if not raw:
        return dict(default)
    try:
        parsed = json.loads(raw)
    except Exception:
        return dict(default)
    return parsed if isinstance(parsed, dict) else dict(default)


def _set_meta_json(conn: sqlite3.Connection, key: str, value: Dict[str, Any]) -> None:
    payload = value if isinstance(value, dict) else {}
    _set_meta_value(conn, key, json.dumps(payload, ensure_ascii=False))


def _is_initialized(conn: sqlite3.Connection, doc_key: str) -> bool:
    return _get_meta_value(conn, _init_key(doc_key)) == "1"


def _mark_initialized(conn: sqlite3.Connection, doc_key: str) -> None:
    _set_meta_value(conn, _init_key(doc_key), "1")


def _is_doc_empty(doc_key: str, data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return True
    if doc_key == _DOC_PLUGINS:
        return not bool(data.get("plugins"))
    if doc_key == _DOC_ICONS:
        return not bool(data.get("iconpacks"))
    if doc_key == _DOC_REQUESTS:
        return not bool(data.get("requests"))
    if doc_key == _DOC_USERS:
        return not bool(data.get("users"))
    if doc_key == _DOC_SUBSCRIPTIONS:
        return not bool(data.get("subscriptions"))
    if doc_key == _DOC_UPDATED:
        return not bool(data.get("items"))
    if doc_key == _DOC_JOINLY:
        return len(data) == 0
    return len(data) == 0


def _ensure_db() -> None:
    global _db_ready
    if _db_ready:
        return

    with _db_lock:
        if _db_ready:
            return

        _ensure_data_dir()
        with _connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta_store (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plugins_items (
                    sort_order INTEGER PRIMARY KEY,
                    slug TEXT,
                    status TEXT,
                    category TEXT,
                    updated_at TEXT,
                    published_at TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_plugins_slug ON plugins_items(slug)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_plugins_status ON plugins_items(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_plugins_category ON plugins_items(category)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS icons_items (
                    sort_order INTEGER PRIMARY KEY,
                    slug TEXT,
                    status TEXT,
                    category TEXT,
                    updated_at TEXT,
                    published_at TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_icons_slug ON icons_items(slug)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_icons_status ON icons_items(status)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS requests_items (
                    sort_order INTEGER PRIMARY KEY,
                    request_id TEXT,
                    status TEXT,
                    request_type TEXT,
                    submitted_at TEXT,
                    updated_at TEXT,
                    payload_user_id INTEGER,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_id ON requests_items(request_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_status ON requests_items(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_type ON requests_items(request_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_user ON requests_items(payload_user_id)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users_items (
                    user_id TEXT PRIMARY KEY,
                    language TEXT,
                    banned INTEGER,
                    ban_reason TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_language ON users_items(language)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_banned ON users_items(banned)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions_items (
                    user_id TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (user_id, slug)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_slug ON subscriptions_items(slug)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS updated_items (
                    sort_order INTEGER PRIMARY KEY,
                    link TEXT,
                    name TEXT,
                    added_at TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_updated_link ON updated_items(link)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS joinly_items (
                    chat_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )

            # Remove legacy storage completely.
            legacy_doc = "".join(
                [
                    chr(115),
                    chr(110),
                    chr(111),
                    chr(119),
                    chr(102),
                    chr(108),
                    chr(97),
                    chr(107),
                    chr(101),
                ]
            )
            legacy_table = legacy_doc + "_items"
            conn.execute(f"DROP TABLE IF EXISTS {legacy_table}")
            conn.execute(
                "DELETE FROM meta_store WHERE key IN (?, ?)",
                (_meta_key(legacy_doc), _init_key(legacy_doc)),
            )

            _migrate_from_kv_store(conn)
            conn.commit()

        _db_ready = True


def _migrate_from_kv_store(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "kv_store"):
        return
    if _get_meta_value(conn, "migration:kv_store_to_rows") == "1":
        return

    docs: Dict[str, Dict[str, Any]] = {}
    rows = conn.execute("SELECT key, value FROM kv_store").fetchall()
    for row in rows:
        key = str(row["key"])
        if key not in _WRITERS:
            continue
        try:
            parsed = json.loads(row["value"])
        except Exception:
            continue
        if isinstance(parsed, dict):
            docs[key] = parsed

    for key, doc in docs.items():
        if _is_initialized(conn, key):
            continue
        _WRITERS[key](conn, doc)

    _set_meta_value(conn, "migration:kv_store_to_rows", "1")


def _read_legacy_json(doc_key: str) -> Dict[str, Any]:
    for path in _LEGACY_PATHS.get(doc_key, []):
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _read_items_payload(rows: list[sqlite3.Row]) -> list[Any]:
    out: list[Any] = []
    for row in rows:
        try:
            out.append(json.loads(row["payload"]))
        except Exception:
            continue
    return out


def _read_plugins_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute("SELECT payload FROM plugins_items ORDER BY sort_order").fetchall()
    items = _read_items_payload(rows)
    meta = _get_meta_json(conn, _meta_key(_DOC_PLUGINS), {})
    meta["plugins"] = items
    return meta


def _write_plugins_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    items = payload.pop("plugins", [])
    if not isinstance(items, list):
        items = []

    conn.execute("DELETE FROM plugins_items")
    for idx, item in enumerate(items):
        slug = status = category = updated_at = published_at = None
        if isinstance(item, dict):
            slug = item.get("slug")
            status = item.get("status")
            category = item.get("category")
            updated_at = item.get("updated_at")
            published_at = item.get("published_at")
        conn.execute(
            """
            INSERT INTO plugins_items (
                sort_order, slug, status, category, updated_at, published_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idx,
                slug,
                status,
                category,
                updated_at,
                published_at,
                json.dumps(item, ensure_ascii=False),
            ),
        )

    _set_meta_json(conn, _meta_key(_DOC_PLUGINS), payload)
    _mark_initialized(conn, _DOC_PLUGINS)


def _read_icons_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute("SELECT payload FROM icons_items ORDER BY sort_order").fetchall()
    items = _read_items_payload(rows)
    meta = _get_meta_json(conn, _meta_key(_DOC_ICONS), {})
    meta["iconpacks"] = items
    return meta


def _write_icons_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    items = payload.pop("iconpacks", [])
    if not isinstance(items, list):
        items = []

    conn.execute("DELETE FROM icons_items")
    for idx, item in enumerate(items):
        slug = status = category = updated_at = published_at = None
        if isinstance(item, dict):
            slug = item.get("slug")
            status = item.get("status")
            category = item.get("category")
            updated_at = item.get("updated_at")
            published_at = item.get("published_at")
        conn.execute(
            """
            INSERT INTO icons_items (
                sort_order, slug, status, category, updated_at, published_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idx,
                slug,
                status,
                category,
                updated_at,
                published_at,
                json.dumps(item, ensure_ascii=False),
            ),
        )

    _set_meta_json(conn, _meta_key(_DOC_ICONS), payload)
    _mark_initialized(conn, _DOC_ICONS)


def _read_requests_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute("SELECT payload FROM requests_items ORDER BY sort_order").fetchall()
    items = _read_items_payload(rows)
    meta = _get_meta_json(conn, _meta_key(_DOC_REQUESTS), {})
    meta["requests"] = items
    return meta


def _write_requests_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    items = payload.pop("requests", [])
    if not isinstance(items, list):
        items = []

    conn.execute("DELETE FROM requests_items")
    for idx, item in enumerate(items):
        request_id = status = request_type = submitted_at = updated_at = None
        payload_user_id = None
        if isinstance(item, dict):
            request_id = item.get("id")
            status = item.get("status")
            request_type = item.get("type")
            submitted_at = item.get("submitted_at")
            updated_at = item.get("updated_at")
            user_id = (item.get("payload") or {}).get("user_id")
            if isinstance(user_id, int):
                payload_user_id = user_id

        conn.execute(
            """
            INSERT INTO requests_items (
                sort_order, request_id, status, request_type, submitted_at, updated_at, payload_user_id, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idx,
                request_id,
                status,
                request_type,
                submitted_at,
                updated_at,
                payload_user_id,
                json.dumps(item, ensure_ascii=False),
            ),
        )

    _set_meta_json(conn, _meta_key(_DOC_REQUESTS), payload)
    _mark_initialized(conn, _DOC_REQUESTS)


def _read_users_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute("SELECT user_id, payload FROM users_items ORDER BY user_id").fetchall()
    users: Dict[str, Any] = {}
    for row in rows:
        try:
            users[str(row["user_id"])] = json.loads(row["payload"])
        except Exception:
            continue
    meta = _get_meta_json(conn, _meta_key(_DOC_USERS), {})
    meta["users"] = users
    return meta


def _write_users_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    users = payload.pop("users", {})
    if not isinstance(users, dict):
        users = {}

    conn.execute("DELETE FROM users_items")
    for user_id, user_data in users.items():
        user_payload = user_data if isinstance(user_data, dict) else {}
        language = user_payload.get("language")
        banned = 1 if user_payload.get("banned") else 0
        ban_reason = user_payload.get("ban_reason")
        conn.execute(
            """
            INSERT INTO users_items (user_id, language, banned, ban_reason, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(user_id),
                language if isinstance(language, str) else None,
                banned,
                ban_reason if isinstance(ban_reason, str) else None,
                json.dumps(user_payload, ensure_ascii=False),
            ),
        )

    _set_meta_json(conn, _meta_key(_DOC_USERS), payload)
    _mark_initialized(conn, _DOC_USERS)


def _read_subscriptions_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute(
        "SELECT user_id, slug FROM subscriptions_items ORDER BY user_id, position"
    ).fetchall()
    subscriptions: Dict[str, list[str]] = {}
    for row in rows:
        user_id = str(row["user_id"])
        subscriptions.setdefault(user_id, []).append(str(row["slug"]))

    meta = _get_meta_json(conn, _meta_key(_DOC_SUBSCRIPTIONS), {})
    meta["subscriptions"] = subscriptions
    return meta


def _write_subscriptions_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    subscriptions = payload.pop("subscriptions", {})
    if not isinstance(subscriptions, dict):
        subscriptions = {}

    conn.execute("DELETE FROM subscriptions_items")
    for user_id, user_slugs in subscriptions.items():
        if not isinstance(user_slugs, list):
            continue
        for position, slug in enumerate(user_slugs):
            if not isinstance(slug, str):
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO subscriptions_items (user_id, slug, position)
                VALUES (?, ?, ?)
                """,
                (str(user_id), slug, position),
            )

    _set_meta_json(conn, _meta_key(_DOC_SUBSCRIPTIONS), payload)
    _mark_initialized(conn, _DOC_SUBSCRIPTIONS)


def _read_updated_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute("SELECT payload FROM updated_items ORDER BY sort_order").fetchall()
    items = _read_items_payload(rows)
    meta = _get_meta_json(conn, _meta_key(_DOC_UPDATED), {})
    meta["items"] = items
    if "seeded" not in meta:
        meta["seeded"] = False
    return meta


def _write_updated_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    items = payload.pop("items", [])
    if not isinstance(items, list):
        items = []

    conn.execute("DELETE FROM updated_items")
    for idx, item in enumerate(items):
        link = name = added_at = None
        if isinstance(item, dict):
            link = item.get("link")
            name = item.get("name")
            added_at = item.get("added_at")
        conn.execute(
            """
            INSERT INTO updated_items (sort_order, link, name, added_at, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (idx, link, name, added_at, json.dumps(item, ensure_ascii=False)),
        )

    _set_meta_json(conn, _meta_key(_DOC_UPDATED), payload)
    _mark_initialized(conn, _DOC_UPDATED)


def _read_joinly_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute("SELECT chat_id, payload FROM joinly_items ORDER BY chat_id").fetchall()
    result = _get_meta_json(conn, _meta_key(_DOC_JOINLY), {})
    for row in rows:
        try:
            result[str(row["chat_id"])] = json.loads(row["payload"])
        except Exception:
            continue
    return result


def _write_joinly_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = data if isinstance(data, dict) else {}
    meta: Dict[str, Any] = {}
    chats: Dict[str, Any] = {}

    for key, value in payload.items():
        key_str = str(key)
        if key_str.lstrip("-").isdigit():
            chats[key_str] = value
        else:
            meta[key_str] = value

    conn.execute("DELETE FROM joinly_items")
    for chat_id, chat_payload in chats.items():
        conn.execute(
            "INSERT OR REPLACE INTO joinly_items (chat_id, payload) VALUES (?, ?)",
            (chat_id, json.dumps(chat_payload, ensure_ascii=False)),
        )

    _set_meta_json(conn, _meta_key(_DOC_JOINLY), meta)
    _mark_initialized(conn, _DOC_JOINLY)


_READERS = {
    _DOC_PLUGINS: _read_plugins_doc,
    _DOC_ICONS: _read_icons_doc,
    _DOC_REQUESTS: _read_requests_doc,
    _DOC_USERS: _read_users_doc,
    _DOC_SUBSCRIPTIONS: _read_subscriptions_doc,
    _DOC_UPDATED: _read_updated_doc,
    _DOC_JOINLY: _read_joinly_doc,
}

_WRITERS = {
    _DOC_PLUGINS: _write_plugins_doc,
    _DOC_ICONS: _write_icons_doc,
    _DOC_REQUESTS: _write_requests_doc,
    _DOC_USERS: _write_users_doc,
    _DOC_SUBSCRIPTIONS: _write_subscriptions_doc,
    _DOC_UPDATED: _write_updated_doc,
    _DOC_JOINLY: _write_joinly_doc,
}


def _read_sqlite_doc_sync(doc_key: str) -> Dict[str, Any]:
    _ensure_db()
    with _connect() as conn:
        data = _READERS[doc_key](conn)
        if _is_initialized(conn, doc_key):
            return data
        if not _is_doc_empty(doc_key, data):
            _mark_initialized(conn, doc_key)
            conn.commit()
            return data

    legacy = _read_legacy_json(doc_key)
    if legacy:
        _write_sqlite_doc_sync(doc_key, legacy)
        return legacy

    return data


def _write_sqlite_doc_sync(doc_key: str, data: Dict[str, Any]) -> None:
    _ensure_db()
    payload = dict(data) if isinstance(data, dict) else {}
    payload.setdefault("updated_at", _now_iso())
    with _connect() as conn:
        _WRITERS[doc_key](conn, payload)
        conn.commit()


def _get_cached(doc_key: str, ttl: float = _TTL) -> Dict[str, Any]:
    now = time.time()
    if doc_key in _cache and (now - _cache_time.get(doc_key, 0.0)) < ttl:
        return _cache[doc_key]

    data = _read_sqlite_doc_sync(doc_key)
    _cache[doc_key] = data
    _cache_time[doc_key] = now
    return data


def _set_cached(doc_key: str, data: Dict[str, Any]) -> None:
    _cache[doc_key] = data
    _cache_time[doc_key] = time.time()
    _dirty[doc_key] = True


async def _schedule_save(doc_key: str) -> None:
    now = time.time()
    if now - _last_save.get(doc_key, 0.0) < _SAVE_INTERVAL:
        return

    _last_save[doc_key] = now
    if doc_key not in _save_locks:
        _save_locks[doc_key] = asyncio.Lock()

    async with _save_locks[doc_key]:
        if doc_key in _cache and _dirty.get(doc_key):
            data = _cache[doc_key].copy()
            await asyncio.to_thread(_write_sqlite_doc_sync, doc_key, data)
            _dirty[doc_key] = False


def _save_sync(doc_key: str, data: Dict[str, Any]) -> None:
    _set_cached(doc_key, data)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_schedule_save(doc_key))
    except RuntimeError:
        _write_sqlite_doc_sync(doc_key, data)
        _dirty[doc_key] = False


def invalidate_cache(doc_key: Optional[str] = None) -> None:
    if doc_key:
        _cache.pop(doc_key, None)
        _cache_time.pop(doc_key, None)
        _dirty.pop(doc_key, None)
        _last_save.pop(doc_key, None)
        return

    _cache.clear()
    _cache_time.clear()
    _dirty.clear()
    _last_save.clear()


def _normalize_dict(data: Dict[str, Any], default: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    result = data.copy()
    for key, value in default.items():
        if key not in result:
            result[key] = deepcopy(value)
    return result


def load_config() -> Dict[str, Any]:
    global _config_cache, _config_cache_time
    if not CONFIG_PATH.exists():
        raise StorageError(f"Config not found: {CONFIG_PATH}")

    now = time.time()
    if _config_cache is not None and (now - _config_cache_time) < _CONFIG_TTL:
        return _config_cache

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise StorageError("Config format is invalid")

    _config_cache = data
    _config_cache_time = now
    return data


def save_config(data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    payload.setdefault("updated_at", _now_iso())
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    global _config_cache, _config_cache_time
    _config_cache = payload
    _config_cache_time = time.time()


def load_plugins() -> Dict[str, Any]:
    return _normalize_dict(_get_cached(_DOC_PLUGINS), {"plugins": []})


def save_plugins(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_PLUGINS, data)


def load_icons() -> Dict[str, Any]:
    return _normalize_dict(_get_cached(_DOC_ICONS), {"iconpacks": []})


def save_icons(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_ICONS, data)


def load_requests() -> Dict[str, Any]:
    return _normalize_dict(_get_cached(_DOC_REQUESTS), {"requests": []})


def save_requests(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_REQUESTS, data)


def load_users() -> Dict[str, Any]:
    return _normalize_dict(_get_cached(_DOC_USERS), {"users": {}})


def save_users(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_USERS, data)


def load_subscriptions() -> Dict[str, Any]:
    return _normalize_dict(_get_cached(_DOC_SUBSCRIPTIONS), {"subscriptions": {}})


def save_subscriptions(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_SUBSCRIPTIONS, data)


def load_updated() -> Dict[str, Any]:
    data = _normalize_dict(_get_cached(_DOC_UPDATED), {"items": [], "seeded": False})
    if not isinstance(data.get("items"), list):
        data["items"] = []
    if "seeded" not in data:
        data["seeded"] = False
    return data


def save_updated(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_UPDATED, data)


def load_joinly() -> Dict[str, Any]:
    data = _get_cached(_DOC_JOINLY)
    return data if isinstance(data, dict) else {}


def save_joinly(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_JOINLY, data)


async def flush_all() -> None:
    for doc_key, is_dirty in list(_dirty.items()):
        if is_dirty and doc_key in _cache:
            data = _cache[doc_key].copy()
            await asyncio.to_thread(_write_sqlite_doc_sync, doc_key, data)
            _dirty[doc_key] = False


async def preload_storage() -> None:
    for doc_key in (
        _DOC_PLUGINS,
        _DOC_ICONS,
        _DOC_REQUESTS,
        _DOC_USERS,
        _DOC_SUBSCRIPTIONS,
        _DOC_UPDATED,
        _DOC_JOINLY,
    ):
        await asyncio.to_thread(_get_cached, doc_key)
