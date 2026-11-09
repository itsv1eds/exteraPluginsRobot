from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from core.request_store import get_all_requests
from core.storage import load_stats, save_stats

WEEK_TIMEZONE = timezone(timedelta(hours=5))
DECISION_STATUSES = frozenset({"published", "rejected", "rework", "deleted", "approved"})
_activity_events: list | None = None
_activity_index: dict[str, int] = {}


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def week_key(value: Any = None) -> str:
    moment = _datetime(value) if value is not None else datetime.now(timezone.utc)
    local = (moment or datetime.now(timezone.utc)).astimezone(WEEK_TIMEZONE)
    return (local.date() - timedelta(days=local.weekday())).isoformat()


def shift_week(value: str, weeks: int) -> str:
    return (date.fromisoformat(value) + timedelta(days=7 * int(weeks))).isoformat()


def week_dates(value: str) -> tuple[date, date]:
    start = date.fromisoformat(value)
    return start, start + timedelta(days=6)


def _request_name(entry: dict[str, Any]) -> str:
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    plugin = payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}
    return str(
        plugin.get("name")
        or plugin.get("id")
        or payload.get("delete_slug")
        or entry.get("id")
        or "—"
    )


def _username(value: Any, user_id: Any = None) -> str:
    raw = str(value or "").strip()
    match = re.search(r"@([A-Za-z0-9_]{1,32})", raw)
    if match:
        return match.group(1)
    clean = raw.lstrip("@").strip()
    if re.fullmatch(r"[A-Za-z0-9_]{1,32}", clean):
        return clean
    return f"id{int(user_id)}" if str(user_id or "").isdigit() else "unknown"


def _ensure_activity_index(events: list) -> None:
    global _activity_events
    if events is _activity_events:
        return
    _activity_events = events
    _activity_index.clear()
    for index, current in enumerate(events):
        if isinstance(current, dict) and current.get("id"):
            _activity_index[str(current["id"])] = index


def _store_event(event: dict[str, Any]) -> None:
    doc = load_stats()
    events = doc.get("moderation_activity")
    if not isinstance(events, list):
        events = []
        doc["moderation_activity"] = events
    _ensure_activity_index(events)
    event_id = str(event.get("id") or "")
    existing_index = _activity_index.get(event_id)
    if existing_index is not None:
        events[existing_index] = event
    else:
        _activity_index[event_id] = len(events)
        events.append(event)
    doc["moderation_activity"] = events
    save_stats(doc)


def _vote_event(entry: dict[str, Any], vote: dict[str, Any], round_number: int | None = None) -> dict[str, Any] | None:
    if entry.get("type") not in {"new", "update"}:
        return None
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        return None
    request_id = str(entry.get("id") or "")
    user_id = int(vote.get("user_id") or 0)
    voted_at = str(vote.get("voted_at") or "")
    value = str(vote.get("vote") or "")
    if not request_id or not user_id or value not in {"yes", "no"} or not _datetime(voted_at):
        return None
    event_id = f"vote:{request_id}:{user_id}:{voted_at}"
    return {
        "id": event_id,
        "kind": "vote",
        "request_id": request_id,
        "request_name": _request_name(entry),
        "request_type": str(entry.get("type") or "new"),
        "submission_type": "plugin",
        "user_id": user_id,
        "username": _username(vote.get("username"), user_id),
        "value": value,
        "reason": str(vote.get("reason") or "").strip(),
        "round": round_number,
        "created_at": voted_at,
    }


def record_vote(entry: dict[str, Any], vote: dict[str, Any], round_number: int | None = None) -> None:
    event = _vote_event(entry, vote, round_number)
    if event:
        _store_event(event)


def _decision_event(
    entry: dict[str, Any],
    status: str,
    actor: Any,
    actor_id: Any,
    created_at: Any,
) -> dict[str, Any] | None:
    if entry.get("type") not in {"new", "update"}:
        return None
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        return None
    user_id = int(actor_id or 0)
    moment = str(created_at or "")
    request_id = str(entry.get("id") or "")
    if status not in DECISION_STATUSES or not request_id or not user_id or not _datetime(moment):
        return None
    return {
        "id": f"decision:{request_id}:{status}:{user_id}:{moment}",
        "kind": "decision",
        "request_id": request_id,
        "request_name": _request_name(entry),
        "request_type": str(entry.get("type") or "new"),
        "submission_type": "plugin",
        "user_id": user_id,
        "username": _username(actor, user_id),
        "value": status,
        "reason": "",
        "created_at": moment,
    }


def record_decision(
    entry: dict[str, Any],
    status: str,
    actor: Any,
    actor_id: Any,
    created_at: Any,
) -> None:
    event = _decision_event(entry, status, actor, actor_id, created_at)
    if event:
        _store_event(event)


def sync_moderation_history() -> int:
    doc = load_stats()
    stored = doc.get("moderation_activity")
    if doc.get("moderation_history_synced"):
        _ensure_activity_index(stored if isinstance(stored, list) else [])
        return 0
    events = {
        str(event.get("id")): event
        for event in (stored if isinstance(stored, list) else [])
        if isinstance(event, dict) and event.get("id")
    }
    before = len(events)
    for entry in get_all_requests(include_history=True):
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        rounds = payload.get("previous_vote_rounds")
        if isinstance(rounds, list):
            for index, round_data in enumerate(rounds, 1):
                votes = round_data.get("votes") if isinstance(round_data, dict) else None
                if isinstance(votes, dict):
                    for vote in votes.values():
                        if isinstance(vote, dict):
                            event = _vote_event(entry, vote, index)
                            if event:
                                events[event["id"]] = event
        votes = payload.get("moderation_votes")
        if isinstance(votes, dict):
            for vote in votes.values():
                if isinstance(vote, dict):
                    event = _vote_event(entry, vote)
                    if event:
                        events[event["id"]] = event
        history = entry.get("history")
        if isinstance(history, list):
            for item in history:
                if not isinstance(item, dict):
                    continue
                if item.get("source_event") == "moderation.request_withdrawn":
                    continue
                event = _decision_event(
                    entry,
                    str(item.get("status") or ""),
                    item.get("actor"),
                    item.get("actor_id"),
                    item.get("changed_at"),
                )
                if event:
                    events[event["id"]] = event
    activity = list(events.values())
    doc["moderation_activity"] = activity
    doc["moderation_history_synced"] = True
    _ensure_activity_index(activity)
    save_stats(doc)
    return max(0, len(events) - before)


def moderation_week(value: str) -> dict[str, Any]:
    key = week_key(value)
    doc = load_stats()
    events = [
        event for event in (doc.get("moderation_activity") or [])
        if isinstance(event, dict)
        and event.get("request_type") in {"new", "update"}
        and event.get("submission_type", "plugin") != "icon"
        and week_key(event.get("created_at")) == key
    ]
    moderators: dict[str, dict[str, Any]] = {}
    for event in sorted(events, key=lambda item: str(item.get("created_at") or "")):
        user_id = str(int(event.get("user_id") or 0))
        item = moderators.setdefault(user_id, {
            "user_id": int(event.get("user_id") or 0),
            "username": _username(event.get("username"), event.get("user_id")),
            "requests": set(),
            "yes": [],
            "no": [],
            "decisions": [],
        })
        if event.get("username") and event.get("username") != "unknown":
            item["username"] = _username(event.get("username"), event.get("user_id"))
        if event.get("request_id"):
            item["requests"].add(str(event["request_id"]))
        if event.get("kind") == "vote" and event.get("value") in {"yes", "no"}:
            item[str(event["value"])].append(event)
        elif event.get("kind") == "decision":
            item["decisions"].append(event)
    result = []
    for item in moderators.values():
        requests = item.pop("requests")
        result.append({**item, "checked": len(requests)})
    result.sort(key=lambda item: (-item["checked"], item["username"].lower()))
    snapshot = {
        "week": key,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "moderators": result,
    }
    weeks = doc.get("moderation_weeks")
    weeks = dict(weeks) if isinstance(weeks, dict) else {}
    weeks[key] = snapshot
    doc["moderation_weeks"] = weeks
    save_stats(doc)
    return snapshot


