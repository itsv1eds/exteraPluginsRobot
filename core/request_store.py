import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from core import request_history
from core.archive_log import record_event
from core.storage import load_requests, save_requests

logger = logging.getLogger(__name__)
_requests_cache: Optional[List[Dict[str, Any]]] = None
_id_index: Dict[str, Dict[str, Any]] = {}
_route_token_index: Dict[str, Dict[str, Any]] = {}
_route_tokens_ready = False
_route_tokens_source_id: Optional[int] = None
_history_keys: set[str] = set()
_cleanup_task: Optional[asyncio.Task] = None
_reminder_task: Optional[asyncio.Task] = None
_scheduled_task: Optional[asyncio.Task] = None
_draft_expiration = timedelta(hours=1)
_draft_reminder_before = timedelta(minutes=10)
_cleanup_interval_seconds = 300
_reminder_interval_seconds = 300
_scheduled_interval_seconds = 30
_processing_requests: set[str] = set()

_REQUEST_ROUTE_TOKEN_RE = re.compile(r"^q[0-9a-f]{20}$")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def acquire_request_lock(request_id: str) -> bool:
    if not request_id:
        return True
    if request_id in _processing_requests:
        return False
    _processing_requests.add(request_id)
    return True


def release_request_lock(request_id: str) -> None:
    _processing_requests.discard(request_id)


def _parse_datetime_utc(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _scheduled_time_is_due(value: Any, now: Optional[datetime] = None) -> bool:
    scheduled_at = _parse_datetime_utc(value)
    if scheduled_at is None:
        return False
    current = now or _now_utc()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    return scheduled_at <= current


def _get_requests_list() -> List[Dict[str, Any]]:
    global _requests_cache, _route_tokens_ready, _route_tokens_source_id
    
    if _requests_cache is not None:
        return _requests_cache
    
    database = load_requests()
    _requests_cache = database.get("requests", [])
    _offload_history(_requests_cache)

    _id_index.clear()
    _route_token_index.clear()
    _route_tokens_ready = False
    _route_tokens_source_id = None
    for req in _requests_cache:
        req_id = req.get("id")
        if req_id:
            _id_index[req_id] = req
    
    return _requests_cache


def _offload_history(requests: List[Dict[str, Any]]) -> None:
    global _history_keys
    history = [entry for entry in requests if isinstance(entry, dict) and entry.get("history_only") and entry.get("id")]
    if history:
        reserved = _reserved_request_values(requests)
        for entry in history:
            token = str(entry.get("route_token") or "").strip()
            if not _REQUEST_ROUTE_TOKEN_RE.fullmatch(token):
                nonce = 0
                while (token := _route_token_candidate(str(entry["id"]), nonce)) in reserved:
                    nonce += 1
                entry["route_token"] = token
                reserved.add(token)
        request_history.store(history)
        moved = {id(entry) for entry in history}
        requests[:] = [entry for entry in requests if id(entry) not in moved]
        save_requests({"requests": requests})
        logger.info("event=requests.history_offloaded count=%s", len(history))
    _history_keys = request_history.keys()


def _history_entry(request_id: str = "", token: str = "") -> Optional[Dict[str, Any]]:
    if request_id and request_id in _history_keys:
        return request_history.get_by_id(request_id)
    if token and token in _history_keys:
        return request_history.get_by_token(token)
    return None


def _ensure_active(entry: Dict[str, Any]) -> None:
    global _route_tokens_ready
    request_id = str(entry.get("id") or "")
    if not request_id or _id_index.get(request_id) is entry:
        return
    _get_requests_list().append(entry)
    _id_index[request_id] = entry
    _route_tokens_ready = False
    request_history.remove(request_id)
    _history_keys.discard(request_id)
    _history_keys.discard(str(entry.get("route_token") or ""))


def _save_requests_list() -> None:
    if _requests_cache is not None:
        save_requests({"requests": _requests_cache})


def _route_token_candidate(request_id: str, nonce: int = 0) -> str:
    source = request_id if nonce == 0 else f"{nonce}\0{request_id}"
    digest = hashlib.blake2s(source.encode("utf-8"), digest_size=10).hexdigest()
    return f"q{digest}"


def _reserved_request_values(requests: List[Dict[str, Any]]) -> set[str]:
    reserved: set[str] = set()
    for item in requests:
        if not isinstance(item, dict):
            continue
        request_id = str(item.get("id") or "").strip()
        route_token = str(item.get("route_token") or "").strip()
        if request_id:
            reserved.add(request_id)
        if _REQUEST_ROUTE_TOKEN_RE.fullmatch(route_token):
            reserved.add(route_token)
    return reserved | _history_keys


def _ensure_all_request_route_tokens() -> List[Dict[str, Any]]:
    global _route_tokens_ready, _route_tokens_source_id
    requests = _get_requests_list()
    if _route_tokens_ready and _route_tokens_source_id == id(requests):
        return requests

    request_ids = {
        str(entry.get("id") or "").strip()
        for entry in requests
        if isinstance(entry, dict) and str(entry.get("id") or "").strip()
    }

    last_valid_owner: Dict[str, Dict[str, Any]] = {}
    for entry in requests:
        if not isinstance(entry, dict):
            continue
        token = str(entry.get("route_token") or "").strip()
        if _REQUEST_ROUTE_TOKEN_RE.fullmatch(token):
            last_valid_owner[token] = entry

    reserved = set(request_ids) | _history_keys
    reserved.update(last_valid_owner)
    rebuilt_index: Dict[str, Dict[str, Any]] = {}
    changed = False
    for entry in requests:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        request_id = str(entry.get("id") or "").strip()
        current = str(entry.get("route_token") or "").strip()
        if _REQUEST_ROUTE_TOKEN_RE.fullmatch(current) and last_valid_owner.get(current) is entry:
            token = current
        else:
            nonce = 0
            while True:
                token = _route_token_candidate(request_id, nonce)
                if token not in reserved:
                    break
                nonce += 1
            entry["route_token"] = token
            changed = True
        reserved.add(token)
        rebuilt_index[token] = entry

    _route_token_index.clear()
    _route_token_index.update(rebuilt_index)
    _route_tokens_ready = True
    _route_tokens_source_id = id(requests)
    if changed:
        _save_requests_list()
    return requests


def _find_request_by_route_token(value: str) -> Optional[Dict[str, Any]]:
    _ensure_all_request_route_tokens()
    return _route_token_index.get(value) or _history_entry(token=value)


def _touch_request(entry: Dict[str, Any]) -> None:
    entry["updated_at"] = _now_utc().isoformat()
    entry.pop("reminder_sent_at", None)


def add_request(payload: Dict[str, Any], request_type: str = "new") -> Dict[str, Any]:
    global _route_tokens_ready
    requests = _get_requests_list()
    _ensure_all_request_route_tokens()
    
    plugin = payload.get("plugin", {})
    icon_pack = payload.get("icon", {})
    plugin_id = plugin.get("id")
    icon_id = icon_pack.get("id")
    base_id = str(plugin_id or icon_id or uuid4().hex).strip() or uuid4().hex
    
    reserved_values = _reserved_request_values(requests)
    final_id = base_id
    suffix = 1
    while final_id in reserved_values:
        final_id = f"{base_id}+{suffix}"
        suffix += 1

    entry = {
        "id": final_id,
        "type": request_type,
        "status": "pending",
        "submitted_at": _now_utc().isoformat(),
        "updated_at": _now_utc().isoformat(),
        "payload": payload,
        "history": [],
    }
    
    requests.append(entry)
    _id_index[final_id] = entry
    _route_tokens_ready = False
    _ensure_all_request_route_tokens()
    record_event("request", entry, True)
    
    return entry


def add_draft_request(payload: Dict[str, Any], request_type: str = "new") -> Dict[str, Any]:
    global _route_tokens_ready
    requests = _get_requests_list()
    _ensure_all_request_route_tokens()

    plugin = payload.get("plugin", {})
    icon_pack = payload.get("icon", {})
    base_id = str(plugin.get("id") or icon_pack.get("id") or uuid4().hex).strip() or uuid4().hex

    reserved_values = _reserved_request_values(requests)
    final_id = base_id
    suffix = 1
    while final_id in reserved_values:
        final_id = f"{base_id}+{suffix}"
        suffix += 1

    entry = {
        "id": final_id,
        "type": request_type,
        "status": "draft",
        "submitted_at": None,
        "updated_at": _now_utc().isoformat(),
        "payload": payload,
        "history": [],
    }

    requests.append(entry)
    _id_index[final_id] = entry
    _route_tokens_ready = False
    _ensure_all_request_route_tokens()
    record_event("request", entry, True)

    return entry


def get_requests(status: str = "pending", request_type: Optional[str] = None) -> List[Dict[str, Any]]:
    requests = _get_requests_list()
    result = [req for req in requests if req.get("status") == status and not req.get("history_only")]
    if request_type:
        result = [req for req in result if req.get("type") == request_type]
    return result


def get_all_requests(request_type: Optional[str] = None, *, include_history: bool = False) -> List[Dict[str, Any]]:
    requests = list(_get_requests_list())
    if include_history:
        requests += request_history.load_all()
    if request_type:
        requests = [req for req in requests if req.get("type") == request_type]
    return requests


def get_user_requests(user_id: int) -> List[Dict[str, Any]]:
    requests = _get_requests_list()
    return [
        req
        for req in requests
        if req.get("payload", {}).get("user_id") == user_id and not req.get("history_only")
    ]


def get_request_by_id(request_id: str) -> Optional[Dict[str, Any]]:
    _get_requests_list()
    return _id_index.get(request_id) or _history_entry(request_id=str(request_id or ""))


def get_history_summaries() -> List[Dict[str, Any]]:
    _get_requests_list()
    return request_history.summaries()


def _request_plugin_ids(entry: Dict[str, Any]) -> set[str]:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    if not isinstance(payload, dict):
        return set()

    ids: set[str] = set()
    for key in ("plugin", "icon", "old_plugin"):
        item = payload.get(key)
        if not isinstance(item, dict):
            continue
        for id_key in ("id", "slug"):
            value = str(item.get(id_key) or "").strip()
            if value:
                ids.add(value)

    for key in ("update_slug", "delete_slug"):
        value = str(payload.get(key) or "").strip()
        if value:
            ids.add(value)

    return ids


def get_request_by_plugin_id(
    plugin_id: str,
    statuses: Optional[set[str] | frozenset[str]] = None,
) -> Optional[Dict[str, Any]]:
    target = str(plugin_id or "").strip()
    if not target:
        return None
    for entry in reversed(_get_requests_list()):
        if entry.get("history_only"):
            continue
        if statuses is not None and entry.get("status") not in statuses:
            continue
        if target in _request_plugin_ids(entry):
            return entry
    return None


def request_deeplink_token(request_id: str) -> str:
    value = str(request_id or "").strip()
    _ensure_all_request_route_tokens()
    entry = get_request_by_id(value) or _route_token_index.get(value)
    if not entry:
        raise ValueError(f"Unknown request id: {value!r}")
    return str(entry["route_token"])


def get_request_by_deeplink_token(token: str) -> Optional[Dict[str, Any]]:
    value = str(token or "").strip()
    if not value:
        return None
    return _find_request_by_route_token(value) or get_request_by_id(value)


def request_callback_token(request_id: str) -> str:
    return request_deeplink_token(request_id)


def get_request_by_callback_token(token: str) -> Optional[Dict[str, Any]]:
    value = str(token or "").strip()
    if not value:
        return None
    return _find_request_by_route_token(value) or get_request_by_id(value)


def _relocate_request_files(entry: Dict[str, Any], subdir: str) -> None:
    try:
        from bot.helpers import get_uploads_subdir
    except Exception:
        return
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return
    dest_dir = get_uploads_subdir(subdir)
    for key in ("plugin", "icon"):
        item = payload.get(key)
        if not isinstance(item, dict):
            continue
        raw = item.get("file_path")
        if not raw:
            continue
        src = Path(raw)
        dest = dest_dir / src.name
        if src.resolve() == dest.resolve():
            continue
        try:
            if not src.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and not src.samefile(dest):
                dest = dest_dir / f"{uuid4().hex[:8]}-{src.name}"
            if not dest.exists():
                os.link(src, dest)
            item["file_path"] = str(dest)
        except Exception:
            logger.exception("Failed to relocate %s to %s", src, dest)


DECISION_STATUSES = {"published", "rejected", "rework", "deleted", "scheduled", "approved"}


def update_request_status(
    request_id: str,
    status: str,
    comment: Optional[str] = None,
    actor: Optional[str] = None,
    actor_id: Optional[int] = None,
    *,
    payload_fields: Dict[str, Any] | None = None,
) -> bool:
    entry = get_request_by_id(request_id)
    if not entry:
        return False

    _ensure_active(entry)
    if payload_fields:
        entry.setdefault("payload", {}).update(payload_fields)
    entry["status"] = status
    entry.pop("history_only", None)
    if status != "deleted":
        entry.pop("archived_at", None)
        entry.pop("archived_status", None)
    if status in DECISION_STATUSES and (actor or actor_id):
        entry["decided_by"] = actor or ""
        entry["decided_by_id"] = int(actor_id) if actor_id else 0
        entry["decided_at"] = _now_utc().isoformat()
    if status == "rejected":
        _relocate_request_files(entry, "rejected")
    elif status == "published" and entry.get("type") != "unban_appeal":
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        subdir = "icons" if isinstance(payload.get("icon"), dict) and (payload.get("icon") or {}).get("file_path") else "plugins"
        _relocate_request_files(entry, subdir)
    _touch_request(entry)
    history = entry.setdefault("history", [])
    history.append({
        "status": status,
        "comment": comment,
        "actor": actor or "",
        "actor_id": int(actor_id) if actor_id else 0,
        "changed_at": _now_utc().isoformat(),
    })
    
    _save_requests_list()
    record_event("request", entry, True)
    if status in DECISION_STATUSES and (actor or actor_id):
        try:
            from bot.services.moderation_stats import record_decision

            record_decision(entry, status, actor, actor_id, history[-1]["changed_at"])
        except Exception:
            logger.exception("Failed to record moderation decision request_id=%s", request_id)
    return True


def update_request_payload(request_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = get_request_by_id(request_id)
    if not entry:
        return None

    _ensure_active(entry)
    entry.setdefault("payload", {}).update(fields)
    _touch_request(entry)
    _save_requests_list()
    record_event("request", entry, True)
    return entry


def promote_draft_request(request_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = get_request_by_id(request_id)
    if not entry:
        return None

    entry["payload"] = payload
    entry["status"] = "pending"
    entry["submitted_at"] = _now_utc().isoformat()
    _touch_request(entry)
    _save_requests_list()
    record_event("request", entry, True)
    return entry


def collect_draft_reminders() -> List[Dict[str, Any]]:
    now = _now_utc()
    reminders: List[Dict[str, Any]] = []
    for entry in list(_get_requests_list()):
        if entry.get("status") != "draft":
            continue
        if entry.get("reminder_sent_at"):
            continue
        updated_at = entry.get("updated_at") or entry.get("submitted_at")
        if not updated_at:
            continue
        updated_dt = _parse_datetime_utc(updated_at)
        if updated_dt is None:
            continue
        age = now - updated_dt
        if age >= (_draft_expiration - _draft_reminder_before) and age < _draft_expiration:
            entry["reminder_sent_at"] = now.isoformat()
            reminders.append(entry)
    if reminders:
        _save_requests_list()
    return reminders


def cleanup_expired_drafts() -> int:
    removed = 0
    now = _now_utc()
    for entry in list(_get_requests_list()):
        if entry.get("status") != "draft":
            continue
        updated_at = entry.get("updated_at") or entry.get("submitted_at")
        if not updated_at:
            continue
        updated_dt = _parse_datetime_utc(updated_at)
        if updated_dt is None:
            continue
        if now - updated_dt > _draft_expiration:
            delete_request(entry.get("id", ""))
            removed += 1
    if removed:
        logger.info("Archived %s expired drafts", removed)
    return removed


def discard_user_drafts(user_id: int, plugin_id: Optional[str] = None) -> int:
    target = str(plugin_id).strip() if plugin_id else None
    removed = 0
    for entry in list(_get_requests_list()):
        if entry.get("status") != "draft":
            continue
        payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
        if not isinstance(payload, dict) or payload.get("user_id") != user_id:
            continue
        if target and target not in _request_plugin_ids(entry):
            continue
        if delete_request(entry.get("id", "")):
            removed += 1
    if removed:
        logger.info("Discarded %s stale draft(s) for user %s", removed, user_id)
    return removed


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(_cleanup_interval_seconds)
        cleanup_expired_drafts()


async def _reminder_loop(bot) -> None:
    from aiogram.enums import ParseMode
    from bot.context import get_lang
    from bot.texts import t

    while True:
        await asyncio.sleep(_reminder_interval_seconds)
        reminders = collect_draft_reminders()
        if not reminders:
            continue
        for entry in reminders:
            payload = entry.get("payload", {})
            user_id = payload.get("user_id")
            if not user_id:
                continue
            lang = get_lang(user_id)
            try:
                await bot.send_message(
                    user_id,
                    t("draft_expiring", lang),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception:
                continue
        logger.info("Draft reminder sent to %s users", len(reminders))


def start_draft_cleanup_worker() -> None:
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _cleanup_task = loop.create_task(_cleanup_loop())


def start_draft_reminder_worker(bot) -> None:
    global _reminder_task
    if _reminder_task and not _reminder_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _reminder_task = loop.create_task(_reminder_loop(bot))


async def _cancel_worker(task: Optional[asyncio.Task]) -> None:
    if not task or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def stop_draft_cleanup_worker() -> None:
    global _cleanup_task
    task = _cleanup_task
    _cleanup_task = None
    await _cancel_worker(task)


async def stop_draft_reminder_worker() -> None:
    global _reminder_task
    task = _reminder_task
    _reminder_task = None
    await _cancel_worker(task)


async def _publish_due_request(bot, request_id: str, now: datetime) -> None:
    from bot.services.request_publication import publish_request, validate_publication

    if not acquire_request_lock(request_id):
        return
    try:
        entry = get_request_by_id(request_id)
        if not entry or entry.get("history_only") or entry.get("status") != "scheduled":
            return
        payload = entry.get("payload") or {}
        if not _scheduled_time_is_due(payload.get("scheduled_at"), now):
            return
        retry_at = _parse_datetime_utc(payload.get("publish_retry_at"))
        if retry_at and retry_at > now:
            return
        try:
            errors = validate_publication(entry)
            if errors:
                raise ValueError("\n".join(errors))
            await publish_request(bot, entry, "scheduler", scheduled=True)
        except Exception as exc:
            logger.exception("event=moderation.scheduled_publish.failed request_id=%s", request_id)
            if entry.get("status") == "scheduled":
                update_request_payload(request_id, {
                    "last_publish_error": str(exc),
                    "last_publish_error_at": _now_utc().isoformat(),
                    "publish_retry_at": (_now_utc() + timedelta(minutes=10)).isoformat(),
                })
    finally:
        release_request_lock(request_id)


async def _scheduled_publish_loop(bot) -> None:
    from bot.services.approval import queue_pending_approval_notifications

    while True:
        await asyncio.sleep(_scheduled_interval_seconds)
        now = _now_utc()
        queue_pending_approval_notifications(bot)
        for entry in list(get_requests(status="scheduled")):
            if entry.get("id"):
                await _publish_due_request(bot, str(entry["id"]), now)


def start_scheduled_publish_worker(bot) -> None:
    global _scheduled_task
    if _scheduled_task and not _scheduled_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _scheduled_task = loop.create_task(_scheduled_publish_loop(bot))


async def stop_scheduled_publish_worker() -> None:
    global _scheduled_task
    task = _scheduled_task
    _scheduled_task = None
    await _cancel_worker(task)


def delete_request(request_id: str) -> bool:
    entry = get_request_by_id(request_id)
    if not entry or entry.get("archived_at"):
        return False
    _ensure_active(entry)
    entry["archived_at"] = _now_utc().isoformat()
    entry["archived_status"] = entry.get("status")
    return update_request_status(request_id, "deleted")


def delete_requests_by_plugin_id(plugin_id: str) -> int:
    target = str(plugin_id or "").strip()
    if not target:
        return 0

    removed = 0
    for entry in list(_get_requests_list()):
        request_id = str(entry.get("id") or "")
        if request_id == target or target in _request_plugin_ids(entry):
            if delete_request(request_id):
                removed += 1
    return removed


def cleanup_hidden_requests() -> int:
    return cleanup_expired_drafts()
