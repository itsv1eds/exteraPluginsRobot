from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.archive_log import record_event
from core import audit_store
from core.storage import load_audit, save_audit

_migrated = False


def _ensure_migrated() -> None:
    global _migrated
    if _migrated:
        return
    db = load_audit()
    events = db.get("events")
    if isinstance(events, list) and events:
        audit_store.append(events)
        save_audit({key: value for key, value in db.items() if key != "events"} | {"events": []})
    _migrated = True


def add_audit_event(
    event: str,
    *,
    actor_id: int | None = None,
    actor: str | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    _ensure_migrated()
    record = {
        "event": event,
        "actor_id": actor_id,
        "actor": actor,
        "request_id": request_id,
        "details": details or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    record_event("audit", record, True)
    audit_store.append([record])


def recent_audit_events(limit: int = 20) -> list[dict[str, Any]]:
    _ensure_migrated()
    return audit_store.newest(0, max(1, int(limit)))


def audit_events_count() -> int:
    _ensure_migrated()
    return audit_store.count()


def audit_events_page(page: int = 0, per_page: int = 10) -> tuple[list[dict[str, Any]], int]:
    _ensure_migrated()
    return audit_store.newest(max(0, int(page)) * per_page, per_page), audit_store.count()
