from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage import load_audit, save_audit

MAX_AUDIT_EVENTS = 1000


def add_audit_event(
    event: str,
    *,
    actor_id: int | None = None,
    actor: str | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    db = load_audit()
    events = db.get("events")
    if not isinstance(events, list):
        events = []
    events.append(
        {
            "event": event,
            "actor_id": actor_id,
            "actor": actor,
            "request_id": request_id,
            "details": details or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    db["events"] = events[-MAX_AUDIT_EVENTS:]
    save_audit(db)


def recent_audit_events(limit: int = 20) -> list[dict[str, Any]]:
    db = load_audit()
    events = db.get("events")
    if not isinstance(events, list):
        return []
    return [event for event in events[-max(1, int(limit)):][::-1] if isinstance(event, dict)]
