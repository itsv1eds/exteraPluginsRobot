import asyncio
import json
import random
import logging
import os
import sqlite3
import threading
import time
import tempfile
from copy import deepcopy
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from functools import lru_cache

from bot.languages import SUPPORTED_LANGUAGES, localized_value, normalize_language

from core.runtime_config import merge_secrets, persisted_secrets, read_secrets, split_config, write_secrets
import core.storage_layout as _layout
from core.storage_layout import (
    ADMIN_SQLITE_PATH, CATALOG_SQLITE_PATH, DATA_DIR, LEGACY_SQLITE_PATH,
    MIGRATION_KEY, SECRETS_PATH, StorageCorruptError, StorageError,
    check_integrity, connect, initialize, read_config, table_exists,
)

ROOT = _layout.ROOT
SQLITE_PATH = _layout.SQLITE_PATH

_CONFIG_META_KEY = "app_config"

_DOC_PLUGINS = "plugins"
_DOC_ICONS = "icons"
_DOC_REQUESTS = "requests"
_DOC_USERS = "users"
_DOC_SUBSCRIPTIONS = "subscriptions"
_DOC_UPDATED = "updated"
_DOC_JOINLY = "joinly"
_DOC_AUDIT = "audit"
_DOC_POSTER = "poster"
_DOC_DIALOGS = "dialogs"
_DOC_STATS = "stats"
_DOC_QUIZ = "quiz"
_DOC_BROADCASTS = "broadcasts"

_cache: Dict[str, Dict[str, Any]] = {}
_cache_time: Dict[str, float] = {}
_dirty: Dict[str, bool] = {}
_save_locks: Dict[str, asyncio.Lock] = {}
_last_save: Dict[str, float] = {}

_TTL = 30.0
_SAVE_INTERVAL = 3.0
_CONFIG_TTL = 300.0
_CONFIG_DEFAULTS: Dict[str, Any] = {
    "moderation": {
        "forum_chat_id": -1003859317683,
        "forum_topic_id": 97,
        "vote_threshold": 5,
        "notification_chat_ids": [],
        "delete_review_notifications_on_decision": False,
        "admin_notification_preferences": {},
    }
}

_pending_save: Dict[str, bool] = {}
_background_tasks: set = set()
logger = logging.getLogger(__name__)


def _on_background_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background storage save failed",
            exc_info=(type(exc), exc, exc.__traceback__),
        )


def _spawn(loop, coro) -> None:
    task = loop.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_on_background_done)


_config_cache: Optional[Dict[str, Any]] = None
_config_cache_time: float = 0.0
_config_generation = 0
_config_persisted_generation = 0
_config_write_lock = threading.Lock()

_data_snapshot_lock = threading.RLock()
_db_lock = threading.Lock()
_db_ready = False
_migration_report: Dict[str, Any] = {}

def _connect() -> sqlite3.Connection:
    return connect()


def verify_integrity() -> None:
    for path in (CATALOG_SQLITE_PATH, ADMIN_SQLITE_PATH):
        if path.exists():
            try:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)) as conn:
                    check_integrity(conn, path)
            except sqlite3.DatabaseError as exc:
                raise StorageCorruptError(f"SQLite database is unreadable: {path}") from exc


def _config_paths(public):
    result = deepcopy(public)
    storage = result.get("storage")
    if not isinstance(storage, dict):
        storage = result["storage"] = {}
    storage.update({
        "data_dir": str(DATA_DIR),
        "sqlite_path": str(ADMIN_SQLITE_PATH),
        "catalog_sqlite_path": str(CATALOG_SQLITE_PATH),
        "admin_sqlite_path": str(ADMIN_SQLITE_PATH),
        "legacy_sqlite_path": str(LEGACY_SQLITE_PATH),
    })
    return result


def _prepare_config(conn, source):
    config = read_config(source)
    if not config:
        return
    public, secrets = split_config(config)
    existing = read_secrets(SECRETS_PATH)
    merged = merge_secrets(secrets, existing)
    if merged or SECRETS_PATH.exists():
        _write_runtime(merged)
    _set_meta_json(conn, _CONFIG_META_KEY, _config_paths(public))


def _write_runtime(secrets):
    if SECRETS_PATH.exists() and read_secrets(SECRETS_PATH) == secrets:
        return
    if SECRETS_PATH.parent == DATA_DIR / "private":
        SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(SECRETS_PATH.parent, 0o700)
    write_secrets(SECRETS_PATH, secrets)


def _legacy_values(source, table):
    if source is None or not table_exists(source, table):
        return {}
    return {str(row["key"]): (row["value"], row["updated_at"]) for row in source.execute(f"SELECT key, CAST(value AS BLOB) AS value, updated_at FROM {table}")}


def _legacy_json(raw, key):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise StorageError(f"Invalid legacy document: {key}") from exc
    if not isinstance(value, dict):
        raise StorageError(f"Legacy document is not an object: {key}")
    return value


def _import_legacy(conn, source):
    if source is None:
        return
    documents = _legacy_values(source, "meta_store")
    documents.update(_legacy_values(source, "documents"))
    kv = {}
    if table_exists(source, "kv_store"):
        kv = {str(row[0]): row[1] for row in source.execute("SELECT key, CAST(value AS BLOB) FROM kv_store")}
    known = set(_WRITERS) | {"settings"}
    retired = {"stenka", "snowflake"}
    for key, (value, updated_at) in documents.items():
        if key in (MIGRATION_KEY, _CONFIG_META_KEY):
            continue
        if key.removeprefix("meta:").removeprefix("init:").split(":", 1)[0] in retired:
            continue
        if key.startswith(("meta:", "init:")) and key.split(":", 1)[1] not in known:
            continue
        conn.execute("INSERT INTO documents (key,value,updated_at) VALUES (?,?,?)", (key, value.decode("utf-8") if isinstance(value, bytes) else value, updated_at))
    fields = {"plugins": "plugins", "icons": "iconpacks", "requests": "requests", "users": "users", "subscriptions": "subscriptions", "updated": "items", "joinly": None}
    migrated = documents.get("migration:kv_store_to_rows", (b"0", ""))[0]
    migrated = migrated in ("1", b"1")
    for key, writer in _WRITERS.items():
        table = key + "_items"
        meta_key = _meta_key(key)
        raw = documents.get(meta_key, (None, None))[0]
        meta = _legacy_json(raw, meta_key) if raw is not None else {}
        initialized = documents.get(_init_key(key), (None, None))[0] in ("1", b"1")
        exists = key in fields and table_exists(source, table)
        has_rows = exists and source.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
        authoritative = exists and (has_rows or initialized or migrated)
        if authoritative:
            schema = "catalog" if key in (_DOC_PLUGINS, _DOC_ICONS) else "main"
            columns = [row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({table})")]
            names = ",".join('"' + column + '"' for column in columns)
            expressions = ",".join('CAST(payload AS BLOB)' if column == "payload" else '"' + column + '"' for column in columns)
            rows = source.execute(f"SELECT {expressions} FROM {table}")
            conn.executemany(f"INSERT INTO {schema}.{table} ({names}) VALUES ({','.join('?' for column in columns)})", rows)
            field = fields[key]
            if field:
                meta.pop(field, None)
            else:
                meta = {k: v for k, v in meta.items() if not k.lstrip("-").isdigit()}
            _set_meta_json(conn, meta_key, meta)
            _mark_initialized(conn, key)
        elif not initialized and key in kv and not migrated:
            writer(conn, _legacy_json(kv[key], key))
        elif key in fields and raw is not None:
            writer(conn, meta)
    if "settings" in kv and "meta:settings" not in documents:
        _set_meta_json(conn, "meta:settings", _legacy_json(kv["settings"], "settings"))


def _ensure_db() -> None:
    global _db_ready, _migration_report
    if _db_ready:
        return
    with _data_snapshot_lock, _db_lock:
        if _db_ready:
            return
        try:
            _migration_report = initialize(_import_legacy, _prepare_config)
        except sqlite3.DatabaseError as exc:
            raise StorageCorruptError("Unable to initialize storage databases") from exc
        _db_ready = True


def migrate_storage() -> Dict[str, Any]:
    _ensure_db()
    return {**_migration_report, "catalog_sqlite_path": str(CATALOG_SQLITE_PATH), "admin_sqlite_path": str(ADMIN_SQLITE_PATH), "secrets_path": str(SECRETS_PATH)}


def snapshot_databases(directory: Path) -> Dict[str, Path]:
    directory = Path(directory)
    with _data_snapshot_lock:
        _ensure_db()
        directory.mkdir(parents=True, exist_ok=True)
        names = ["catalog.sqlite3", "admin.sqlite3"]
        targets = {name: directory / name for name in names}
        if any(path.exists() for path in targets.values()):
            raise StorageError("Snapshot destination files must not exist")
        published = []
        try:
            with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=directory) as temporary:
                stage = Path(temporary)
                with closing(_connect()) as guard:
                    guard.execute("BEGIN IMMEDIATE")
                    for name, source_path in (("catalog.sqlite3", CATALOG_SQLITE_PATH), ("admin.sqlite3", ADMIN_SQLITE_PATH)):
                        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, timeout=30)) as source, closing(sqlite3.connect(stage / name)) as destination:
                            source.backup(destination)
                            check_integrity(destination, source_path)
                    guard.rollback()
                for name, target in targets.items():
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    os.replace(stage / name, target)
                    published.append(target)
        except BaseException:
            for path in published:
                path.unlink(missing_ok=True)
            raise
        return targets


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meta_key(doc_key: str) -> str:
    return f"meta:{doc_key}"


def _init_key(doc_key: str) -> str:
    return f"init:{doc_key}"


def _get_meta_value(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM documents WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta_value(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO documents (key, value, updated_at) VALUES (?, ?, ?)",
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
    if doc_key == _DOC_POSTER:
        return not (data.get("channels") or data.get("posts"))
    if doc_key == _DOC_AUDIT:
        return not bool(data.get("events"))
    if doc_key == _DOC_QUIZ:
        return not bool(data.get("questions"))
    if doc_key == _DOC_BROADCASTS:
        return not bool(data.get("runs"))
    return len(data) == 0


def _read_items_payload(rows: list[sqlite3.Row]) -> list[Any]:
    out: list[Any] = []
    for row in rows:
        try:
            out.append(_loads_sqlite_json(row["payload"]))
        except Exception:
            continue
    return out


def _loads_sqlite_json(value: Any) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return json.loads(value)


def _read_plugins_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute("SELECT CAST(payload AS BLOB) AS payload FROM plugins_items ORDER BY sort_order").fetchall()
    items = _read_items_payload(rows)
    meta = _get_meta_json(conn, _meta_key(_DOC_PLUGINS), {})
    meta["plugins"] = items
    return meta


def _write_plugins_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    items = payload.pop("plugins", [])
    if not isinstance(items, list):
        items = []

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
            ON CONFLICT(sort_order) DO UPDATE SET
                slug=excluded.slug, status=excluded.status, category=excluded.category,
                updated_at=excluded.updated_at, published_at=excluded.published_at, payload=excluded.payload
            WHERE plugins_items.payload IS NOT excluded.payload
                OR plugins_items.slug IS NOT excluded.slug OR plugins_items.status IS NOT excluded.status
                OR plugins_items.category IS NOT excluded.category OR plugins_items.updated_at IS NOT excluded.updated_at
                OR plugins_items.published_at IS NOT excluded.published_at
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

    conn.execute("DELETE FROM plugins_items WHERE sort_order < 0 OR sort_order >= ?", (len(items),))
    _set_meta_json(conn, _meta_key(_DOC_PLUGINS), payload)
    _mark_initialized(conn, _DOC_PLUGINS)


def _read_icons_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute("SELECT CAST(payload AS BLOB) AS payload FROM icons_items ORDER BY sort_order").fetchall()
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
    rows = conn.execute("SELECT CAST(payload AS BLOB) AS payload FROM requests_items ORDER BY sort_order").fetchall()
    items = _read_items_payload(rows)
    meta = _get_meta_json(conn, _meta_key(_DOC_REQUESTS), {})
    meta["requests"] = items
    return meta


def _write_requests_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    items = payload.pop("requests", [])
    if not isinstance(items, list):
        items = []

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
            ON CONFLICT(sort_order) DO UPDATE SET
                request_id=excluded.request_id, status=excluded.status, request_type=excluded.request_type,
                submitted_at=excluded.submitted_at, updated_at=excluded.updated_at,
                payload_user_id=excluded.payload_user_id, payload=excluded.payload
            WHERE requests_items.payload IS NOT excluded.payload OR requests_items.request_id IS NOT excluded.request_id
                OR requests_items.status IS NOT excluded.status OR requests_items.request_type IS NOT excluded.request_type
                OR requests_items.submitted_at IS NOT excluded.submitted_at OR requests_items.updated_at IS NOT excluded.updated_at
                OR requests_items.payload_user_id IS NOT excluded.payload_user_id
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

    conn.execute("DELETE FROM requests_items WHERE sort_order < 0 OR sort_order >= ?", (len(items),))
    _set_meta_json(conn, _meta_key(_DOC_REQUESTS), payload)
    _mark_initialized(conn, _DOC_REQUESTS)


def _read_users_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute("SELECT user_id, CAST(payload AS BLOB) AS payload FROM users_items ORDER BY user_id").fetchall()
    users: Dict[str, Any] = {}
    for row in rows:
        try:
            users[str(row["user_id"])] = _loads_sqlite_json(row["payload"])
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
    rows = conn.execute("SELECT CAST(payload AS BLOB) AS payload FROM updated_items ORDER BY sort_order").fetchall()
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
    rows = conn.execute("SELECT chat_id, CAST(payload AS BLOB) AS payload FROM joinly_items ORDER BY chat_id").fetchall()
    result = _get_meta_json(conn, _meta_key(_DOC_JOINLY), {})
    for row in rows:
        try:
            result[str(row["chat_id"])] = _loads_sqlite_json(row["payload"])
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


def _read_poster_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    data = _get_meta_json(conn, _meta_key(_DOC_POSTER), {})
    if not isinstance(data.get("channels"), list):
        data["channels"] = []
    if not isinstance(data.get("posts"), list):
        data["posts"] = []
    return data


def _write_poster_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    if not isinstance(payload.get("channels"), list):
        payload["channels"] = []
    if not isinstance(payload.get("posts"), list):
        payload["posts"] = []
    _set_meta_json(conn, _meta_key(_DOC_POSTER), payload)
    _mark_initialized(conn, _DOC_POSTER)


def _read_audit_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    data = _get_meta_json(conn, _meta_key(_DOC_AUDIT), {})
    if not isinstance(data.get("events"), list):
        data["events"] = []
    return data


def _write_audit_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    if not isinstance(payload.get("events"), list):
        payload["events"] = []
    _set_meta_json(conn, _meta_key(_DOC_AUDIT), payload)
    _mark_initialized(conn, _DOC_AUDIT)


def _read_quiz_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    data = _get_meta_json(conn, _meta_key(_DOC_QUIZ), {})
    if not isinstance(data.get("questions"), list):
        data["questions"] = []
    return data


def _read_broadcasts_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    data = _get_meta_json(conn, _meta_key(_DOC_BROADCASTS), {})
    if not isinstance(data.get("runs"), list):
        data["runs"] = []
    return data


def _write_broadcasts_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    if not isinstance(payload.get("runs"), list):
        payload["runs"] = []
    _set_meta_json(conn, _meta_key(_DOC_BROADCASTS), payload)
    _mark_initialized(conn, _DOC_BROADCASTS)


def _write_quiz_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    if not isinstance(payload.get("questions"), list):
        payload["questions"] = []
    _set_meta_json(conn, _meta_key(_DOC_QUIZ), payload)
    _mark_initialized(conn, _DOC_QUIZ)


def _read_dialogs_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    data = _get_meta_json(conn, _meta_key(_DOC_DIALOGS), {})
    if not isinstance(data.get("threads"), dict):
        data["threads"] = {}
    return data


def _write_dialogs_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    if not isinstance(payload.get("threads"), dict):
        payload["threads"] = {}
    _set_meta_json(conn, _meta_key(_DOC_DIALOGS), payload)
    _mark_initialized(conn, _DOC_DIALOGS)


def _read_stats_doc(conn: sqlite3.Connection) -> Dict[str, Any]:
    data = _get_meta_json(conn, _meta_key(_DOC_STATS), {})
    if not isinstance(data.get("plugin_opens"), dict):
        data["plugin_opens"] = {}
    if not isinstance(data.get("moderation_activity"), list):
        data["moderation_activity"] = []
    if not isinstance(data.get("moderation_weeks"), dict):
        data["moderation_weeks"] = {}
    return data


def _write_stats_doc(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    if not isinstance(payload.get("plugin_opens"), dict):
        payload["plugin_opens"] = {}
    if not isinstance(payload.get("moderation_activity"), list):
        payload["moderation_activity"] = []
    if not isinstance(payload.get("moderation_weeks"), dict):
        payload["moderation_weeks"] = {}
    _set_meta_json(conn, _meta_key(_DOC_STATS), payload)
    _mark_initialized(conn, _DOC_STATS)


_READERS = {
    _DOC_PLUGINS: _read_plugins_doc,
    _DOC_ICONS: _read_icons_doc,
    _DOC_REQUESTS: _read_requests_doc,
    _DOC_USERS: _read_users_doc,
    _DOC_SUBSCRIPTIONS: _read_subscriptions_doc,
    _DOC_UPDATED: _read_updated_doc,
    _DOC_JOINLY: _read_joinly_doc,
    _DOC_AUDIT: _read_audit_doc,
    _DOC_POSTER: _read_poster_doc,
    _DOC_DIALOGS: _read_dialogs_doc,
    _DOC_STATS: _read_stats_doc,
    _DOC_QUIZ: _read_quiz_doc,
    _DOC_BROADCASTS: _read_broadcasts_doc,
}

_WRITERS = {
    _DOC_PLUGINS: _write_plugins_doc,
    _DOC_ICONS: _write_icons_doc,
    _DOC_REQUESTS: _write_requests_doc,
    _DOC_USERS: _write_users_doc,
    _DOC_SUBSCRIPTIONS: _write_subscriptions_doc,
    _DOC_UPDATED: _write_updated_doc,
    _DOC_JOINLY: _write_joinly_doc,
    _DOC_AUDIT: _write_audit_doc,
    _DOC_POSTER: _write_poster_doc,
    _DOC_DIALOGS: _write_dialogs_doc,
    _DOC_STATS: _write_stats_doc,
    _DOC_QUIZ: _write_quiz_doc,
    _DOC_BROADCASTS: _write_broadcasts_doc,
}


def _read_sqlite_doc_sync(doc_key: str) -> Dict[str, Any]:
    _ensure_db()
    with _data_snapshot_lock, closing(_connect()) as conn, conn:
        data = _READERS[doc_key](conn)
        if _is_initialized(conn, doc_key):
            return data
        if not _is_doc_empty(doc_key, data):
            _mark_initialized(conn, doc_key)
            conn.commit()
            return data

    return data


def _write_sqlite_doc_sync(doc_key: str, data: Dict[str, Any]) -> None:
    _ensure_db()
    payload = dict(data) if isinstance(data, dict) else {}
    payload.setdefault("updated_at", _now_iso())
    with _data_snapshot_lock, closing(_connect()) as conn, conn:
        _WRITERS[doc_key](conn, payload)
        conn.commit()


def _get_cached(doc_key: str, ttl: float = _TTL) -> Dict[str, Any]:
    now = time.time()
    if doc_key in _cache and (_dirty.get(doc_key) or (now - _cache_time.get(doc_key, 0.0)) < ttl):
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
    delay = _SAVE_INTERVAL - (now - _last_save.get(doc_key, 0.0))
    if delay > 0:
        if _pending_save.get(doc_key):
            return
        _pending_save[doc_key] = True
        try:
            await asyncio.sleep(delay)
        finally:
            _pending_save[doc_key] = False
        now = time.time()

    _last_save[doc_key] = now
    await flush_documents(doc_key)


async def flush_documents(*doc_keys: str) -> None:
    for doc_key in dict.fromkeys(doc_keys):
        if doc_key not in _WRITERS:
            raise ValueError(f"Unknown storage document: {doc_key}")
        await _flush_document(doc_key)


async def _flush_document(doc_key: str) -> None:
    if doc_key not in _save_locks:
        _save_locks[doc_key] = asyncio.Lock()

    async with _save_locks[doc_key]:
        if doc_key in _cache and _dirty.get(doc_key):
            data = deepcopy(_cache[doc_key])
            await asyncio.to_thread(_write_sqlite_doc_sync, doc_key, data)
            if _cache.get(doc_key) == data:
                _dirty[doc_key] = False


def _save_sync(doc_key: str, data: Dict[str, Any]) -> None:
    _set_cached(doc_key, data)
    try:
        loop = asyncio.get_running_loop()
        _spawn(loop, _schedule_save(doc_key))
    except RuntimeError:
        _write_sqlite_doc_sync(doc_key, data)
        _dirty[doc_key] = False


def _normalize_dict(data: Dict[str, Any], default: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    result = data.copy()
    for key, value in default.items():
        if key not in result:
            result[key] = deepcopy(value)
    return result


def _normalize_config_defaults(data: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    result = deepcopy(data)
    changed = False

    for key, default_value in _CONFIG_DEFAULTS.items():
        current_value = result.get(key)
        if isinstance(default_value, dict):
            if not isinstance(current_value, dict):
                result[key] = deepcopy(default_value)
                changed = True
                continue
            for child_key, child_default in default_value.items():
                if child_key not in current_value:
                    current_value[child_key] = deepcopy(child_default)
                    changed = True
        elif key not in result:
            result[key] = deepcopy(default_value)
            changed = True

    return result, changed


def load_config() -> Dict[str, Any]:
    global _config_cache, _config_cache_time
    now = time.time()
    if _config_cache is not None and (now - _config_cache_time) < _CONFIG_TTL:
        with _data_snapshot_lock:
            _, cached_secrets = split_config(_config_cache)
            data = merge_secrets(_config_cache, read_secrets(SECRETS_PATH))
            return merge_secrets(merge_secrets(data, cached_secrets), {}, os.environ)
    _ensure_db()
    with _data_snapshot_lock, closing(_connect()) as conn:
        raw = _get_meta_value(conn, _CONFIG_META_KEY)
        if raw is None:
            raise StorageError(f"Config not found in SQLite: {ADMIN_SQLITE_PATH}. Initialize with save_config(public_config) before startup")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise StorageError("Config format is invalid") from exc
        if not isinstance(data, dict):
            raise StorageError("Config format is invalid")
        data = merge_secrets(data, read_secrets(SECRETS_PATH))
    data, changed = _normalize_config_defaults(data)
    if changed:
        save_config(data)
    else:
        _config_cache = data
        _config_cache_time = now
    return merge_secrets(data, {}, os.environ)


def _write_config_sync(payload: Dict[str, Any], generation: int) -> None:
    global _config_persisted_generation
    with _config_write_lock, _data_snapshot_lock:
        if generation < _config_generation:
            return
        _ensure_db()
        public, secrets = persisted_secrets(payload, read_secrets(SECRETS_PATH))
        public = _config_paths(public)
        if secrets or SECRETS_PATH.exists():
            _write_runtime(secrets)
        with closing(_connect()) as conn, conn:
            _set_meta_json(conn, _CONFIG_META_KEY, public)
        _config_persisted_generation = max(_config_persisted_generation, generation)


def save_config(data: Dict[str, Any]) -> None:
    payload = deepcopy(data) if isinstance(data, dict) else {}
    payload, _ = _normalize_config_defaults(payload)
    payload = _config_paths(payload)
    payload["updated_at"] = _now_iso()

    global _config_cache, _config_cache_time, _config_generation
    _config_generation += 1
    generation = _config_generation
    _config_cache = payload
    _config_cache_time = time.time()

    try:
        loop = asyncio.get_running_loop()
        _spawn(loop, asyncio.to_thread(_write_config_sync, payload, generation))
    except RuntimeError:
        _write_config_sync(payload, generation)


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
    data = _normalize_dict(_get_cached(_DOC_USERS), {"users": {}})
    changed = False
    for user in (data.get("users") or {}).values():
        if isinstance(user, dict) and user.get("language") == "uk":
            user["language"] = "ua"
            changed = True
    if changed:
        save_users(data)
    return data


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


QUIZ_QUESTIONS_PER_RUN = 3
QUIZ_OPTIONS_PER_QUESTION = 4
_QUIZ_SEED_FILE = ROOT / "quiz_seed.json"


def _normalize_quiz_question(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    if "uk" in item:
        item = {**item, "ua": item.get("ua") or item["uk"]}
    result: Dict[str, Any] = {"id": str(item.get("id") or "").strip()}
    if not result["id"]:
        return None
    for lang in SUPPORTED_LANGUAGES:
        block = item.get(lang)
        if isinstance(block, (list, tuple)) and len(block) == 2:
            text, options = block
        elif isinstance(block, dict):
            text, options = block.get("text"), block.get("options")
        else:
            continue
        options = [str(o).strip() for o in (options or []) if str(o).strip()]
        text = str(text or "").strip()
        if text and len(options) >= 2:
            result[lang] = [text, options]
    return result if any(result.get(lang) for lang in SUPPORTED_LANGUAGES) else None


@lru_cache(maxsize=1)
def _quiz_translations() -> dict:
    questions = {}
    for lang in SUPPORTED_LANGUAGES:
        path = ROOT / "bot/locales/quiz" / f"{lang}.json"
        for question_id, block in json.loads(path.read_text(encoding="utf-8")).items():
            questions.setdefault(question_id, {"id": question_id})[lang] = block
    return questions


def _add_quiz_translations(items: list) -> bool:
    changed = False
    defaults = _quiz_translations()
    for item in items:
        seed = defaults.get(item["id"])
        originals = [lang for lang in ("ru", "en") if item.get(lang)]
        if not seed or not originals or any(item[lang] != seed.get(lang) for lang in originals):
            continue
        for lang in SUPPORTED_LANGUAGES:
            if not item.get(lang) and seed.get(lang):
                item[lang] = deepcopy(seed[lang])
                changed = True
    return changed


def quiz_seed_questions() -> List[Dict[str, Any]]:
    try:
        raw = json.loads(_QUIZ_SEED_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = list(_quiz_translations().values())
    items = raw.get("questions") if isinstance(raw, dict) else raw
    questions = [q for q in (_normalize_quiz_question(i) for i in (items or [])) if q]
    _add_quiz_translations(questions)
    return questions


def load_quiz_questions() -> List[Dict[str, Any]]:
    stored = _normalize_dict(_get_cached(_DOC_QUIZ), {"questions": []}).get("questions")
    items = [q for q in (_normalize_quiz_question(i) for i in (stored or [])) if q]
    if items:
        changed = items != stored
        if _add_quiz_translations(items) or changed:
            save_quiz_questions(items)
        return items
    seeded = quiz_seed_questions()
    if seeded:
        save_quiz_questions(seeded)
    return seeded


def save_quiz_questions(items: List[Dict[str, Any]]) -> None:
    _save_sync(_DOC_QUIZ, {
        "questions": [q for q in (_normalize_quiz_question(i) for i in items) if q],
    })


def update_quiz_question(question_id: str, lang: str, text: str | None = None,
                         options: List[str] | None = None) -> bool:
    lang = normalize_language(lang, None)
    if not lang:
        return False
    items = load_quiz_questions()
    for item in items:
        if item["id"] != question_id:
            continue
        lang = normalize_language(lang)
        block = localized_value(item, lang, ["", []])
        item[lang] = [
            str(text if text is not None else block[0]).strip(),
            [str(o).strip() for o in (options if options is not None else block[1]) if str(o).strip()],
        ]
        save_quiz_questions(items)
        return True
    return False


def add_quiz_question(question_id: str, lang: str, text: str, options: List[str]) -> bool:
    lang = normalize_language(lang, None)
    if not lang:
        return False
    items = load_quiz_questions()
    question_id = str(question_id or "").strip()
    if not question_id or any(q["id"] == question_id for q in items):
        return False
    entry = _normalize_quiz_question({"id": question_id, lang: [text, options]})
    if not entry:
        return False
    items.append(entry)
    save_quiz_questions(items)
    return True


def delete_quiz_question(question_id: str) -> bool:
    items = load_quiz_questions()
    remaining = [q for q in items if q["id"] != question_id]
    if len(remaining) == len(items):
        return False
    save_quiz_questions(remaining)
    return True


def restore_quiz_defaults() -> int:
    items = quiz_seed_questions()
    save_quiz_questions(items)
    return len(items)


def build_quiz(lang: str = "ru", count: int = QUIZ_QUESTIONS_PER_RUN) -> List[Dict[str, Any]]:
    pool = load_quiz_questions()
    quiz: List[Dict[str, Any]] = []
    for item in random.sample(pool, min(count, len(pool))):
        text, options = localized_value(item, lang)
        correct = options[0]
        shuffled = list(options)
        random.shuffle(shuffled)
        quiz.append({
            "id": item["id"],
            "text": text,
            "options": shuffled,
            "correct": shuffled.index(correct),
        })
    return quiz


def load_audit() -> Dict[str, Any]:
    return _normalize_dict(_get_cached(_DOC_AUDIT), {"events": []})


def save_audit(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_AUDIT, data)


def load_broadcasts() -> Dict[str, Any]:
    return _normalize_dict(_get_cached(_DOC_BROADCASTS), {"runs": []})


def save_broadcasts(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_BROADCASTS, data)


def load_poster() -> Dict[str, Any]:
    data = _normalize_dict(_get_cached(_DOC_POSTER), {"channels": [], "posts": []})
    if not isinstance(data.get("channels"), list):
        data["channels"] = []
    if not isinstance(data.get("posts"), list):
        data["posts"] = []
    return data


def save_poster(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_POSTER, data)


def load_stats() -> Dict[str, Any]:
    data = _normalize_dict(
        _get_cached(_DOC_STATS),
        {"plugin_opens": {}, "moderation_activity": [], "moderation_weeks": {}},
    )
    if not isinstance(data.get("plugin_opens"), dict):
        data["plugin_opens"] = {}
    if not isinstance(data.get("moderation_activity"), list):
        data["moderation_activity"] = []
    if not isinstance(data.get("moderation_weeks"), dict):
        data["moderation_weeks"] = {}
    return data


def save_stats(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_STATS, data)


def load_dialogs() -> Dict[str, Any]:
    data = _normalize_dict(_get_cached(_DOC_DIALOGS), {"threads": {}})
    if not isinstance(data.get("threads"), dict):
        data["threads"] = {}
    return data


def save_dialogs(data: Dict[str, Any]) -> None:
    _save_sync(_DOC_DIALOGS, data)


async def flush_all() -> None:
    if (
        _config_cache is not None
        and _config_persisted_generation < _config_generation
    ):
        await asyncio.to_thread(
            _write_config_sync,
            deepcopy(_config_cache),
            _config_generation,
        )

    await flush_documents(*list(_dirty))


async def preload_storage() -> None:
    await asyncio.to_thread(load_config)
    for doc_key in (
        _DOC_PLUGINS,
        _DOC_ICONS,
        _DOC_REQUESTS,
        _DOC_USERS,
        _DOC_SUBSCRIPTIONS,
        _DOC_UPDATED,
        _DOC_JOINLY,
        _DOC_AUDIT,
        _DOC_QUIZ,
    ):
        await asyncio.to_thread(_get_cached, doc_key)
