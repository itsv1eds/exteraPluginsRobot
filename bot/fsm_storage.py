from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from aiogram.fsm.storage.base import StateType, StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

logger = logging.getLogger(__name__)

_IDLE_STATES = {None, "UserFlow:idle"}
_KEEP_SECONDS = 3 * 24 * 3600
_FLUSH_SECONDS = 2.0


def _encode_key(key: StorageKey) -> str:
    return json.dumps([key.bot_id, key.chat_id, key.user_id, key.thread_id, key.business_connection_id, key.destiny])


def _decode_key(raw: str) -> StorageKey:
    bot_id, chat_id, user_id, thread_id, business_connection_id, destiny = json.loads(raw)
    return StorageKey(bot_id=bot_id, chat_id=chat_id, user_id=user_id, thread_id=thread_id,
                      business_connection_id=business_connection_id, destiny=destiny)


class PersistentStorage(MemoryStorage):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = Path(path)
        self._dirty: set[StorageKey] = set()
        self._task: asyncio.Task | None = None
        self._unsaved_logged: set[StorageKey] = set()
        self._load()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS fsm (key TEXT PRIMARY KEY, state TEXT, data TEXT NOT NULL, updated_at REAL NOT NULL)")
        return conn

    def _load(self) -> None:
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("DELETE FROM fsm WHERE updated_at < ?", (time.time() - _KEEP_SECONDS,))
                rows = conn.execute("SELECT key, state, data FROM fsm").fetchall()
        except sqlite3.DatabaseError:
            logger.exception("event=fsm.load_failed path=%s", self.path)
            return
        for raw_key, state, data in rows:
            try:
                record = self.storage[_decode_key(raw_key)]
                record.state, record.data = state, json.loads(data)
            except (ValueError, TypeError):
                logger.warning("event=fsm.row_skipped key=%s", raw_key)
        logger.info("event=fsm.loaded dialogs=%s", len(rows))

    def _touch(self, key: StorageKey) -> None:
        self._dirty.add(key)
        if self._task is None or self._task.done():
            try:
                self._task = asyncio.get_running_loop().create_task(self._flush_later())
            except RuntimeError:
                self._task = None

    async def _flush_later(self) -> None:
        await asyncio.sleep(_FLUSH_SECONDS)
        await self.flush()

    def _snapshot(self) -> tuple[list[tuple], list[tuple]]:
        keys, self._dirty = self._dirty, set()
        saved, removed = [], []
        now = time.time()
        for key in keys:
            record = self.storage.get(key)
            if record is None or record.state in _IDLE_STATES:
                removed.append((_encode_key(key),))
                continue
            try:
                saved.append((_encode_key(key), record.state, json.dumps(record.data, ensure_ascii=False), now))
                self._unsaved_logged.discard(key)
            except (TypeError, ValueError) as exc:
                removed.append((_encode_key(key),))
                if key not in self._unsaved_logged:
                    self._unsaved_logged.add(key)
                    logger.warning("event=fsm.not_serializable state=%s error=%s", record.state, exc)
        return saved, removed

    def _write(self, saved: list[tuple], removed: list[tuple]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executemany("DELETE FROM fsm WHERE key = ?", removed)
            conn.executemany("INSERT OR REPLACE INTO fsm (key, state, data, updated_at) VALUES (?, ?, ?, ?)", saved)

    async def flush(self) -> None:
        if not self._dirty:
            return
        saved, removed = self._snapshot()
        try:
            await asyncio.to_thread(self._write, saved, removed)
        except sqlite3.DatabaseError:
            logger.exception("event=fsm.flush_failed")

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        await self.flush()

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        await super().set_state(key, state)
        self._touch(key)

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await super().set_data(key, data)
        self._touch(key)
