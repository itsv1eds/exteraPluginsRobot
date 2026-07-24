import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from storage import load_requests, save_requests

logger = logging.getLogger(__name__)
_requests_cache: Optional[List[Dict[str, Any]]] = None
_id_index: Dict[str, Dict[str, Any]] = {}
_cleanup_task: Optional[asyncio.Task] = None
_reminder_task: Optional[asyncio.Task] = None
_scheduled_task: Optional[asyncio.Task] = None
_draft_expiration = timedelta(hours=1)
_draft_reminder_before = timedelta(minutes=10)
_cleanup_interval_seconds = 300
_reminder_interval_seconds = 300
_scheduled_interval_seconds = 30


def invalidate_requests_cache() -> None:
    global _requests_cache, _id_index
    _requests_cache = None
    _id_index.clear()


def _get_requests_list() -> List[Dict[str, Any]]:
    global _requests_cache, _id_index
    
    if _requests_cache is not None:
        return _requests_cache
    
    database = load_requests()
    _requests_cache = database.get("requests", [])
    
    _id_index.clear()
    for req in _requests_cache:
        req_id = req.get("id")
        if req_id:
            _id_index[req_id] = req
    
    return _requests_cache


def _save_requests_list() -> None:
    if _requests_cache is not None:
        save_requests({"requests": _requests_cache})


def _touch_request(entry: Dict[str, Any]) -> None:
    entry["updated_at"] = datetime.utcnow().isoformat()
    entry.pop("reminder_sent_at", None)


def add_request(payload: Dict[str, Any], request_type: str = "new") -> Dict[str, Any]:
    requests = _get_requests_list()
    
    plugin = payload.get("plugin", {})
    icon_pack = payload.get("icon", {})
    plugin_id = plugin.get("id")
    icon_id = icon_pack.get("id")
    base_id = plugin_id or icon_id or uuid4().hex
    
    existing_ids = _id_index.keys()
    final_id = base_id
    suffix = 1
    while final_id in existing_ids:
        final_id = f"{base_id}+{suffix}"
        suffix += 1

    entry = {
        "id": final_id,
        "type": request_type,
        "status": "pending",
        "submitted_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "payload": payload,
        "history": [],
    }
    
    requests.append(entry)
    _id_index[final_id] = entry
    _save_requests_list()
    
    return entry


def add_draft_request(payload: Dict[str, Any], request_type: str = "new") -> Dict[str, Any]:
    requests = _get_requests_list()

    plugin = payload.get("plugin", {})
    icon_pack = payload.get("icon", {})
    base_id = plugin.get("id") or icon_pack.get("id") or uuid4().hex

    existing_ids = _id_index.keys()
    final_id = base_id
    suffix = 1
    while final_id in existing_ids:
        final_id = f"{base_id}+{suffix}"
        suffix += 1

    entry = {
        "id": final_id,
        "type": request_type,
        "status": "draft",
        "submitted_at": None,
        "updated_at": datetime.utcnow().isoformat(),
        "payload": payload,
        "history": [],
    }

    requests.append(entry)
    _id_index[final_id] = entry
    _save_requests_list()

    return entry


def get_requests(status: str = "pending", request_type: Optional[str] = None) -> List[Dict[str, Any]]:
    requests = _get_requests_list()
    result = [req for req in requests if req.get("status") == status]
    if request_type:
        result = [req for req in result if req.get("type") == request_type]
    return result


def get_all_requests(request_type: Optional[str] = None) -> List[Dict[str, Any]]:
    requests = list(_get_requests_list())
    if request_type:
        requests = [req for req in requests if req.get("type") == request_type]
    return requests


def get_user_requests(user_id: int) -> List[Dict[str, Any]]:
    requests = _get_requests_list()
    return [
        req
        for req in requests
        if req.get("payload", {}).get("user_id") == user_id
    ]


def get_request_by_id(request_id: str) -> Optional[Dict[str, Any]]:
    _get_requests_list()
    return _id_index.get(request_id)


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


def get_request_by_plugin_id(plugin_id: str) -> Optional[Dict[str, Any]]:
    target = str(plugin_id or "").strip()
    if not target:
        return None
    for entry in _get_requests_list():
        if target in _request_plugin_ids(entry):
            return entry
    return None


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
            dest.unlink(missing_ok=True)
            src.replace(dest)
            item["file_path"] = str(dest)
        except Exception:
            logger.exception("Failed to relocate %s to %s", src, dest)


def update_request_status(request_id: str, status: str, comment: Optional[str] = None) -> bool:
    entry = get_request_by_id(request_id)
    if not entry:
        return False

    entry["status"] = status
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
        "changed_at": datetime.utcnow().isoformat(),
    })
    
    _save_requests_list()
    return True


def update_request_payload(request_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = get_request_by_id(request_id)
    if not entry:
        return None

    entry.setdefault("payload", {}).update(fields)
    _touch_request(entry)
    _save_requests_list()
    return entry


def promote_draft_request(request_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = get_request_by_id(request_id)
    if not entry:
        return None

    entry["payload"] = payload
    entry["status"] = "pending"
    entry["submitted_at"] = datetime.utcnow().isoformat()
    _touch_request(entry)
    _save_requests_list()
    return entry


def delete_request_and_file(request_id: str) -> bool:
    entry = get_request_by_id(request_id)
    if entry:
        payload = entry.get("payload", {})
        plugin_path = (payload.get("plugin") or {}).get("file_path")
        icon_path = (payload.get("icon") or {}).get("file_path")
        for path in (plugin_path, icon_path):
            if path:
                Path(path).unlink(missing_ok=True)
    return delete_request(request_id)


def collect_draft_reminders() -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    reminders: List[Dict[str, Any]] = []
    for entry in list(_get_requests_list()):
        if entry.get("status") != "draft":
            continue
        if entry.get("reminder_sent_at"):
            continue
        updated_at = entry.get("updated_at") or entry.get("submitted_at")
        if not updated_at:
            continue
        try:
            updated_dt = datetime.fromisoformat(updated_at)
        except ValueError:
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
    now = datetime.utcnow()
    for entry in list(_get_requests_list()):
        if entry.get("status") != "draft":
            continue
        updated_at = entry.get("updated_at") or entry.get("submitted_at")
        if not updated_at:
            continue
        try:
            updated_dt = datetime.fromisoformat(updated_at)
        except ValueError:
            continue
        if now - updated_dt > _draft_expiration:
            delete_request_and_file(entry.get("id", ""))
            removed += 1
    if removed:
        logger.info("Draft cleanup removed %s entries", removed)
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
        if delete_request_and_file(entry.get("id", "")):
            removed += 1
    if removed:
        logger.info("Discarded %s stale draft(s) for user %s", removed, user_id)
    return removed


def cleanup_orphan_plugin_files() -> int:
    from bot.helpers import get_uploads_dir

    attachments_dir = get_uploads_dir()
    if not attachments_dir.exists():
        return 0
    active_paths = set()
    for entry in _get_requests_list():
        payload = entry.get("payload", {})
        plugin_path = (payload.get("plugin") or {}).get("file_path")
        if plugin_path:
            active_paths.add(Path(plugin_path).resolve())
    removed = 0
    for file_path in attachments_dir.rglob("*.plugin"):
        if file_path.resolve() not in active_paths:
            file_path.unlink(missing_ok=True)
            removed += 1
    if removed:
        logger.info("Removed %s orphan .plugin files", removed)
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


def stop_draft_cleanup_worker() -> None:
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
    _cleanup_task = None


def stop_draft_reminder_worker() -> None:
    global _reminder_task
    if _reminder_task and not _reminder_task.done():
        _reminder_task.cancel()
    _reminder_task = None


async def _scheduled_publish_loop(bot) -> None:
    from aiogram.enums import ParseMode
    from bot.context import get_lang
    from bot.texts import t

    while True:
        await asyncio.sleep(_scheduled_interval_seconds)
        now = datetime.utcnow()
        for entry in list(get_requests(status="scheduled")):
            request_id = entry.get("id")
            if not request_id:
                continue

            payload = entry.get("payload", {})
            scheduled_at = payload.get("scheduled_at")
            if not scheduled_at or not isinstance(scheduled_at, str):
                continue

            try:
                scheduled_dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                continue

            if scheduled_dt > now:
                continue

            try:
                from bot.services.publish import publish_icon, publish_plugin
                from bot.services.admin_notifications import finalize_admin_notify_messages
                from bot.services.audit import add_audit_event
                from bot.services.moderation import delete_forum_request_message

                logger.info("Publishing scheduled request %s", request_id)

                if payload.get("submission_type") == "icon" or payload.get("icon"):
                    result = await publish_icon(entry)
                    notify_key = "notify_icon_published"
                    name = (payload.get("icon") or {}).get("name", "")
                    version = (payload.get("icon") or {}).get("version")
                else:
                    result = await publish_plugin(entry, bot)
                    notify_key = "notify_published"
                    name = (payload.get("plugin") or {}).get("name", "")
                    version = (payload.get("plugin") or {}).get("version")

                update_request_payload(request_id, {"scheduled_at": None})
                update_request_status(request_id, "published")
                await finalize_admin_notify_messages(bot, entry, "Заявка была принята по расписанию", "<code>scheduler</code>")
                await delete_forum_request_message(bot, entry)
                add_audit_event(
                    "moderation.scheduled_publish_success",
                    actor="scheduler",
                    request_id=str(request_id),
                    details={"link": result.get("link", ""), "name": name, "version": version},
                )

                user_id = payload.get("user_id")
                if user_id:
                    lang = get_lang(user_id)
                    try:
                        await bot.send_message(
                            user_id,
                            t(notify_key, lang, name=name or "—", version=version or "—"),
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                    except Exception:
                        pass

            except Exception as exc:
                logger.exception("Scheduled publish error")
                retry_at = datetime.now(timezone.utc) + timedelta(minutes=10)
                update_request_payload(
                    request_id,
                    {
                        "last_publish_error": str(exc),
                        "last_publish_error_at": datetime.now(timezone.utc).isoformat(),
                        "scheduled_at": retry_at.isoformat(),
                    },
                )


def start_scheduled_publish_worker(bot) -> None:
    global _scheduled_task
    if _scheduled_task and not _scheduled_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _scheduled_task = loop.create_task(_scheduled_publish_loop(bot))


def stop_scheduled_publish_worker() -> None:
    global _scheduled_task
    if _scheduled_task and not _scheduled_task.done():
        _scheduled_task.cancel()
    _scheduled_task = None


def delete_request(request_id: str) -> bool:
    global _requests_cache
    
    requests = _get_requests_list()
    
    for i, req in enumerate(requests):
        if req.get("id") == request_id:
            requests.pop(i)
            _id_index.pop(request_id, None)
            _save_requests_list()
            return True
    
    return False


def delete_requests_by_plugin_id(plugin_id: str) -> int:
    target = str(plugin_id or "").strip()
    if not target:
        return 0

    removed = 0
    for entry in list(_get_requests_list()):
        request_id = str(entry.get("id") or "")
        if request_id == target or target in _request_plugin_ids(entry):
            if delete_request_and_file(request_id):
                removed += 1
    return removed


def cleanup_hidden_requests(visible_statuses: Optional[set[str]] = None) -> int:
    visible = visible_statuses or {"draft", "pending", "scheduled", "error"}
    removed = 0
    for entry in list(_get_requests_list()):
        if str(entry.get("status") or "") in visible:
            continue
        request_id = str(entry.get("id") or "")
        if request_id and delete_request_and_file(request_id):
            removed += 1
    return removed
