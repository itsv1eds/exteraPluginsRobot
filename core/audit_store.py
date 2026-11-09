from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Lock

from core.storage_layout import DATA_DIR


AUDIT_PATH = DATA_DIR / "audit.sqlite3"
_schema_lock = Lock()
_ready_path = None


@contextmanager
def connection():
    global _ready_path
    path = AUDIT_PATH
    with _schema_lock:
        if _ready_path != path or not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600, exist_ok=True)
            with closing(sqlite3.connect(path, timeout=15)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            _ready_path = path
    with closing(sqlite3.connect(path, timeout=15)) as conn, conn:
        yield conn


def append(records):
    rows = [(json.dumps(record, ensure_ascii=False),) for record in records if isinstance(record, dict)]
    with connection() as conn:
        conn.executemany("INSERT INTO events (payload) VALUES (?)", rows)
    return len(rows)


def count():
    with connection() as conn:
        return conn.execute("SELECT count(*) FROM events").fetchone()[0]


def newest(offset, limit):
    with connection() as conn:
        rows = conn.execute("SELECT payload FROM events ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [json.loads(row[0]) for row in rows]


def snapshot(destination: Path):
    if not AUDIT_PATH.is_file():
        return None
    with connection() as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
    return destination
