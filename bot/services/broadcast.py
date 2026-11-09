from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from bot.services.archive import restore_journal, suppress_journal
from bot.services.audit import add_audit_event
from core.archive_log import record_event
from core.storage import flush_documents, load_broadcasts, save_broadcasts


@dataclass
class BroadcastRun:
    kind: str
    total: int
    sent: int = 0
    failed: int = 0
    current: int = 0
    started_at: float = 0.0
    finished: bool = False
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    actor_id: int | None = None
    actor: str = ""
    text: str = ""
    status: str = "running"


_run: BroadcastRun | None = None
logger = logging.getLogger(__name__)
_lock = asyncio.Lock()
_progress_views: set[tuple[int, int]] = set()


def show_progress_view(chat_id: int, message_id: int) -> None:
    if chat_id and message_id:
        _progress_views.add((int(chat_id), int(message_id)))


def hide_progress_view(chat_id: int, message_id: int) -> None:
    _progress_views.discard((int(chat_id), int(message_id)))


def progress_view_visible(chat_id: int, message_id: int) -> bool:
    return (int(chat_id), int(message_id)) in _progress_views


def active_broadcast() -> BroadcastRun | None:
    return _run


def _save_run(run: BroadcastRun) -> None:
    db = load_broadcasts()
    runs = [item for item in db.get("runs", []) if isinstance(item, dict)]
    db["runs"] = runs
    record = asdict(run)
    record.pop("started_at")
    previous_status = None
    for index, previous in enumerate(runs):
        if previous.get("id") == run.id:
            previous_status = previous.get("status")
            runs[index] = record
            break
    else:
        runs.append(record)
    save_broadcasts(db)
    if previous_status != run.status:
        _audit_run(record)


def _audit_run(record: dict) -> None:
    try:
        add_audit_event(
            f"broadcast.{record['status']}", actor_id=record.get("actor_id"), actor=record.get("actor"),
            details={"run_id": record["id"], **{key: record.get(key) for key in ("kind", "total", "sent", "failed")}},
        )
    except Exception:
        logger.exception("event=broadcast.audit_failed run_id=%s", record["id"])


def broadcast_history() -> list[dict]:
    db = load_broadcasts()
    records = [item for item in db.get("runs", []) if isinstance(item, dict)]
    active_id = _run.id if _run and not _run.finished else None
    changed = False
    for item in records:
        if item.get("status") == "running" and item.get("id") != active_id:
            item.update(status="interrupted", finished=True, finished_at=datetime.now(timezone.utc).isoformat())
            _audit_run(item)
            changed = True
    if changed:
        db["runs"] = records
        save_broadcasts(db)
    if active_id:
        records = [dict(item, current=_run.current, sent=_run.sent, failed=_run.failed)
                   if item.get("id") == active_id else item for item in records]
    return sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def get_broadcast(run_id: str) -> dict | None:
    return next((item for item in broadcast_history() if item.get("id") == run_id), None)


async def claim_broadcast(kind: str, total: int, *, text: str = "", actor_id: int | None = None,
                          actor: str = "") -> BroadcastRun | None:
    global _run
    async with _lock:
        if _run and not _run.finished:
            return None
        broadcast_history()
        _run = BroadcastRun(kind=kind, total=total, started_at=monotonic(), text=text,
                            actor_id=actor_id, actor=actor)
        try:
            _save_run(_run)
            await flush_documents("broadcasts")
        except BaseException:
            _run = None
            raise
        return _run


async def deliver_broadcast(bot, recipients: list[int], text: str, run: BroadcastRun) -> BroadcastRun:
    run.text = text
    last_save = monotonic()
    token = suppress_journal()
    try:
        for user_id in recipients:
            while True:
                try:
                    await bot.send_message(
                        user_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                    )
                    run.sent += 1
                    break
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(max(1, int(exc.retry_after)))
                except Exception:
                    run.failed += 1
                    break
            run.current += 1
            if monotonic() - last_save >= 5:
                _save_run(run)
                last_save = monotonic()
            await asyncio.sleep(0.04)
        run.status = "completed"
    except BaseException as exc:
        run.status = "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed"
        raise
    finally:
        restore_journal(token)
        run.finished = True
        run.finished_at = datetime.now(timezone.utc).isoformat()
        _save_run(run)
        await flush_documents("broadcasts")
        record = asdict(run)
        record.pop("started_at", None)
        try:
            record_event("broadcast", record, True)
        except Exception:
            logger.exception("event=broadcast.journal_failed run_id=%s", run.id)
    return run
