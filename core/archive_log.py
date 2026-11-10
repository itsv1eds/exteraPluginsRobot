from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from threading import Lock

from core.storage_layout import DATA_DIR


ARCHIVE_PATH = DATA_DIR / "archive.sqlite3"
_schema_lock = Lock()
_ready_path = None
_token = re.compile(r"(?<![\w-])\d{7,12}:[A-Za-z0-9_-]{30,60}(?![\w-])")
_secret_fields = {"bot_token", "api_hash", "api_id", "session_string", "string_session", "password", "secret_token"}


def _safe(value):
    if isinstance(value, dict):
        return {str(key): "[redacted]" if str(key).lower() in _secret_fields else _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        return _token.sub("[redacted]", value)
    return value


def _json(value):
    return json.dumps(_safe(value), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


@contextmanager
def connection():
    global _ready_path
    path = ARCHIVE_PATH
    with _schema_lock:
        if _ready_path != path or not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600, exist_ok=True)
            with closing(sqlite3.connect(path, timeout=15)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY, event_key TEXT UNIQUE,
                        kind TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS delivery (
                        channel_id INTEGER NOT NULL, stream TEXT NOT NULL, position INTEGER NOT NULL,
                        PRIMARY KEY (channel_id, stream)
                    );
                    CREATE TABLE IF NOT EXISTS files (
                        id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL,
                        path TEXT NOT NULL, size INTEGER NOT NULL, retry_at REAL NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS media (
                        id INTEGER PRIMARY KEY, file_id TEXT UNIQUE NOT NULL,
                        kind TEXT NOT NULL, filename TEXT, size INTEGER,
                        path TEXT, retry_at REAL NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS request_cards (
                        channel_id INTEGER NOT NULL, request_id TEXT NOT NULL, state TEXT NOT NULL,
                        PRIMARY KEY (channel_id, request_id)
                    );
                """)
                if "source" not in {row[1] for row in conn.execute("PRAGMA table_info(media)")}:
                    conn.execute("ALTER TABLE media ADD COLUMN source TEXT")
            _ready_path = path
    with closing(sqlite3.connect(path, timeout=15)) as conn, conn:
        yield conn


def record_events(events):
    now = datetime.now(timezone.utc).isoformat()
    source = iter(events)
    while batch := list(islice(source, 200)):
        rows = []
        for kind, payload, key in batch:
            value = _json(payload)
            if key is True:
                key = f"{kind}:{hashlib.sha256(value.encode()).hexdigest()}"
            rows.append((key, kind, now, value))
        with connection() as conn:
            conn.executemany("INSERT OR IGNORE INTO events (event_key, kind, recorded_at, payload) VALUES (?,?,?,?)", rows)


def record_event(kind, payload, key=None):
    record_events([(kind, payload, key)])


def record_file(path, digest, size):
    with connection() as conn:
        conn.execute("INSERT OR IGNORE INTO files (sha256, path, size) VALUES (?,?,?)", (digest, str(path), size))


def known_files():
    with connection() as conn:
        return {(path, size) for path, size in conn.execute("SELECT path, size FROM files")}


def position(channel_id, stream):
    with connection() as conn:
        row = conn.execute("SELECT position FROM delivery WHERE channel_id=? AND stream=?", (channel_id, stream)).fetchone()
    return row[0] if row else 0


def advance(channel_id, stream, value):
    with connection() as conn:
        conn.execute("""
            INSERT INTO delivery (channel_id, stream, position) VALUES (?,?,?)
            ON CONFLICT (channel_id, stream) DO UPDATE SET position=max(position,excluded.position)
        """, (channel_id, stream, value))


def next_events(after, limit=3000):
    with connection() as conn:
        return conn.execute("SELECT id, kind, recorded_at, payload FROM events WHERE id>? ORDER BY id LIMIT ?", (after, limit)).fetchall()


def prune_events(upto):
    with connection() as conn:
        return conn.execute("UPDATE events SET payload='' WHERE id<=? AND payload<>''", (upto,)).rowcount


def compact(min_waste_bytes=32 * 1024 * 1024):
    with connection() as conn:
        size = conn.execute("PRAGMA page_count").fetchone()[0] * conn.execute("PRAGMA page_size").fetchone()[0]
        live = sum(conn.execute(f"SELECT COALESCE(SUM(length({column})), 0) + COUNT(*) * 64 FROM {table}").fetchone()[0]
                   for table, column in (("events", "payload"), ("files", "path"), ("media", "file_id"), ("request_cards", "state")))
    if size - live < min_waste_bytes:
        return False
    with closing(sqlite3.connect(ARCHIVE_PATH, timeout=15, isolation_level=None)) as conn:
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return True


def initialize_readable_feed(channel_id, previous_channel_id=0):
    with connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO delivery (channel_id, stream, position)
            VALUES (?, 'readable', COALESCE(
                (SELECT position FROM delivery WHERE channel_id=? AND stream='readable'),
                (SELECT MAX(id) FROM events), 0
            ))
        """, (channel_id, previous_channel_id))


def load_request_card(channel_id, request_id):
    with connection() as conn:
        row = conn.execute("SELECT state FROM request_cards WHERE channel_id=? AND request_id=?", (channel_id, request_id)).fetchone()
    return json.loads(row[0]) if row else {}


def save_request_card(channel_id, request_id, state):
    with connection() as conn:
        conn.execute("""
            INSERT INTO request_cards (channel_id, request_id, state) VALUES (?,?,?)
            ON CONFLICT (channel_id, request_id) DO UPDATE SET state=excluded.state
        """, (channel_id, request_id, _json(state)))


def next_file(channel_id, chunk_size, now):
    with connection() as conn:
        return conn.execute("""
            SELECT files.id, sha256, path, size, COALESCE(delivery.position, 0)
            FROM files LEFT JOIN delivery ON delivery.channel_id=? AND delivery.stream='file:' || files.id
            WHERE retry_at<=? AND path NOT LIKE 'media/%' AND COALESCE(delivery.position, 0)<max(1,(size+?-1)/?)
            ORDER BY files.id LIMIT 1
        """, (channel_id, now, chunk_size, chunk_size)).fetchone()


def retry_file(file_id, now):
    with connection() as conn:
        conn.execute("UPDATE files SET retry_at=? WHERE id=?", (now, file_id))


def record_media(rows):
    rows = [tuple(row) + (None,) * (5 - len(row)) for row in rows]
    with connection() as conn:
        conn.executemany("INSERT OR IGNORE INTO media (file_id, kind, filename, size, source) VALUES (?,?,?,?,?)", rows)


def next_media(now):
    with connection() as conn:
        return conn.execute("SELECT id, file_id, kind, path, source FROM media WHERE kind<>'sticker' AND (path IS NULL OR path NOT LIKE 'channel:%') AND retry_at<=? ORDER BY id LIMIT 1", (now,)).fetchone()


def update_media(media_id, path=None, retry_at=0):
    with connection() as conn:
        conn.execute("UPDATE media SET path=?,retry_at=? WHERE id=?", (path, retry_at, media_id))


def snapshot_archive(destination: Path):
    if not ARCHIVE_PATH.is_file():
        return None
    with connection() as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
    return destination
