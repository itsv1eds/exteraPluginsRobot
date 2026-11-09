from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Lock

from core.storage_layout import DATA_DIR


HISTORY_PATH = DATA_DIR / "history.sqlite3"
_schema_lock = Lock()
_ready_path = None


@contextmanager
def connection():
    global _ready_path
    path = HISTORY_PATH
    with _schema_lock:
        if _ready_path != path or not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600, exist_ok=True)
            with closing(sqlite3.connect(path, timeout=15)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS requests (
                        request_id TEXT PRIMARY KEY, route_token TEXT, status TEXT, request_type TEXT,
                        updated_at TEXT, label TEXT, payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_history_token ON requests(route_token);
                """)
            _ready_path = path
    with closing(sqlite3.connect(path, timeout=15)) as conn, conn:
        yield conn


def _label(entry):
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    item = payload.get("plugin") or payload.get("icon") or {}
    return str((item.get("name") if isinstance(item, dict) else "") or payload.get("delete_slug") or "")


def store(entries):
    rows = [
        (
            str(entry["id"]), entry.get("route_token"), entry.get("status"), entry.get("type"),
            str(entry.get("updated_at") or entry.get("submitted_at") or ""), _label(entry),
            json.dumps(entry, ensure_ascii=False),
        )
        for entry in entries if isinstance(entry, dict) and entry.get("id")
    ]
    with connection() as conn:
        conn.executemany("INSERT OR REPLACE INTO requests VALUES (?,?,?,?,?,?,?)", rows)
    return len(rows)


def _one(sql, value):
    with connection() as conn:
        row = conn.execute(sql, (value,)).fetchone()
    return json.loads(row[0]) if row else None


def get_by_id(request_id):
    return _one("SELECT payload FROM requests WHERE request_id=?", str(request_id))


def get_by_token(token):
    return _one("SELECT payload FROM requests WHERE route_token=?", str(token))


def keys():
    with connection() as conn:
        rows = conn.execute("SELECT request_id, route_token FROM requests").fetchall()
    return {value for row in rows for value in row if value}


def summaries():
    with connection() as conn:
        rows = conn.execute("SELECT request_id, route_token, status, request_type, updated_at, label FROM requests").fetchall()
    return [
        {
            "id": request_id, "route_token": token, "status": status, "type": request_type,
            "updated_at": updated_at, "history_only": True,
            "payload": {"plugin": {"name": label}} if label else {},
        }
        for request_id, token, status, request_type, updated_at, label in rows
    ]


def load_all():
    with connection() as conn:
        rows = conn.execute("SELECT payload FROM requests").fetchall()
    return [json.loads(row[0]) for row in rows]


def remove(request_id):
    with connection() as conn:
        conn.execute("DELETE FROM requests WHERE request_id=?", (str(request_id),))


def snapshot(destination: Path):
    if not HISTORY_PATH.is_file():
        return None
    with connection() as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
    return destination
