import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from bot.context import get_lang
from bot.formatting import plain_html, split_html
from bot.services.audit import add_audit_event
from bot.texts import t
from core.request_store import get_request_by_id, get_requests, update_request_payload, update_request_status
from core.storage import flush_documents

logger = logging.getLogger(__name__)
SCHEDULE_TIMEZONE = timezone(timedelta(hours=5))
APPROVABLE_STATUSES = frozenset({"pending", "error", "rework", "rejected"})
EDITABLE_REQUEST_STATUSES = frozenset({"pending", "error", "rework", "approved", "scheduled"})
_notifications_in_flight: set[tuple[str, str]] = set()


def is_plugin_request(entry: dict | None) -> bool:
    if not isinstance(entry, dict) or entry.get("type", "new") not in {"new", "update"}:
        return False
    payload = entry.get("payload") or {}
    if not isinstance(payload, dict):
        return False
    return bool(isinstance(payload.get("plugin"), dict) and not payload.get("icon")
                and payload.get("submission_type") != "icon")


def can_approve_request(entry: dict | None) -> bool:
    return is_plugin_request(entry) and not entry.get("history_only") and entry.get("status") in APPROVABLE_STATUSES


def can_edit_request(entry: dict | None) -> bool:
    return is_plugin_request(entry) and not entry.get("history_only") and entry.get("status") in EDITABLE_REQUEST_STATUSES


def approval_status_text(entry: dict, lang: str) -> str:
    payload = entry.get("payload") or {}
    if entry.get("status") == "approved":
        return t("review_approved", lang)
    if entry.get("status") == "scheduled" and payload.get("scheduled_at"):
        try:
            value = datetime.fromisoformat(str(payload["scheduled_at"]))
        except ValueError:
            return t("review_approved", lang)
        value = value.replace(tzinfo=value.tzinfo or timezone.utc)
        return t("review_scheduled", lang, datetime=value.astimezone(SCHEDULE_TIMEZONE).strftime("%d.%m.%Y %H:%M UTC+5"))
    return ""


def approve_request(request_id: str, actor: str, actor_id: int | None) -> dict | None:
    entry = get_request_by_id(request_id)
    if not can_approve_request(entry):
        return None
    update_request_status(request_id, "approved", actor=actor, actor_id=actor_id, payload_fields={
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": actor, "approved_by_id": actor_id,
        "scheduled_at": None, "publish_retry_at": None, "moderation_pending_votes": {},
    })
    add_audit_event("moderation.request_approved", actor=actor, actor_id=actor_id,
                    request_id=request_id)
    return get_request_by_id(request_id)


def schedule_request(request_id: str, scheduled_at: datetime, actor: str, actor_id: int | None) -> dict | None:
    entry = get_request_by_id(request_id)
    if not can_edit_request(entry) or entry.get("type") != "new":
        return None
    if entry.get("status") not in {"pending", "error", "approved", "scheduled"}:
        return None
    payload = entry.get("payload") or {}
    moment = scheduled_at.replace(tzinfo=scheduled_at.tzinfo or timezone.utc).astimezone(timezone.utc)
    if moment <= datetime.now(timezone.utc):
        return None
    previous = payload.get("scheduled_at")
    fields = {
        "scheduled_at": moment.isoformat(), "publish_retry_at": None,
        "approved_at": payload.get("approved_at") or datetime.now(timezone.utc).isoformat(),
        "approved_by": payload.get("approved_by") or actor,
        "approved_by_id": payload.get("approved_by_id") or actor_id,
    }
    if entry.get("status") != "scheduled":
        update_request_status(request_id, "scheduled", actor=actor, actor_id=actor_id, payload_fields=fields)
    else:
        update_request_payload(request_id, fields)
    if previous != moment.isoformat():
        add_audit_event("moderation.request_scheduled", actor=actor, actor_id=actor_id,
                        request_id=request_id, details={"scheduled_at": moment.isoformat(), "previous": previous})
    return get_request_by_id(request_id)


_UNREACHABLE_MARKERS = ("chat not found", "user is deactivated", "bot was blocked", "user not found", "peer_id_invalid")


def _author_unreachable(exc: Exception) -> bool:
    return isinstance(exc, TelegramForbiddenError) or any(marker in str(exc).lower() for marker in _UNREACHABLE_MARKERS)


def _notification_key(entry: dict) -> str:
    if entry.get("status") == "approved":
        return "approved:" + str((entry.get("payload") or {}).get("approved_at") or "")
    if entry.get("status") == "scheduled":
        return "scheduled:" + str((entry.get("payload") or {}).get("scheduled_at") or "")
    return ""


async def notify_author_approval(bot, request_id: str) -> None:
    from bot.services.moderation import with_author_vote_summary

    entry = get_request_by_id(request_id)
    if not entry or not is_plugin_request(entry):
        return
    payload = entry.get("payload") or {}
    key = _notification_key(entry)
    notification = (request_id, key)
    if (not key or not payload.get("user_id") or payload.get("approval_notified_for") == key
            or notification in _notifications_in_flight):
        return
    _notifications_in_flight.add(notification)
    try:
        await flush_documents("requests")
        lang = get_lang(payload["user_id"])
        plugin = payload.get("plugin") or {}
        text = t("notify_request_approved", lang, name=plain_html(plugin.get("name") or plugin.get("id") or "—"))
        if entry.get("status") == "scheduled":
            text += "\n" + approval_status_text(entry, lang)
        else:
            text += "\n" + t("notify_request_approved_waiting", lang)
        progress = payload.get("approval_notification_progress")
        if not isinstance(progress, dict) or progress.get("key") != key:
            progress = {"key": key, "parts": split_html(with_author_vote_summary(text, entry, lang), 3500), "next_part": 0}
            update_request_payload(request_id, {"approval_notification_progress": progress})
            await flush_documents("requests")
        for index in range(progress["next_part"], len(progress["parts"])):
            while True:
                current = get_request_by_id(request_id)
                if not current or _notification_key(current) != key:
                    return
                try:
                    await bot.send_message(payload["user_id"], progress["parts"][index], parse_mode="HTML", disable_web_page_preview=True)
                    break
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(max(1, exc.retry_after))
            current = get_request_by_id(request_id)
            if not current or _notification_key(current) != key:
                return
            progress["next_part"] = index + 1
            update_request_payload(request_id, {"approval_notification_progress": progress})
            await flush_documents("requests")
        current = get_request_by_id(request_id)
        if current and _notification_key(current) == key:
            update_request_payload(request_id, {
                "approval_notified_for": key, "approval_notification_retry_at": None,
                "approval_notification_progress": None,
            })
            await flush_documents("requests")
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        if not _author_unreachable(exc):
            update_request_payload(request_id, {
                "approval_notification_retry_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            })
            logger.exception("event=moderation.approval_notification_failed request_id=%s", request_id)
            return
        update_request_payload(request_id, {
            "approval_notified_for": key, "approval_notification_retry_at": None,
            "approval_notification_progress": None,
        })
        logger.warning("event=moderation.approval_notification_undeliverable request_id=%s error=%s", request_id, exc)
    except Exception:
        update_request_payload(request_id, {
            "approval_notification_retry_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        })
        logger.exception("event=moderation.approval_notification_failed request_id=%s", request_id)
    finally:
        _notifications_in_flight.discard(notification)


def queue_pending_approval_notifications(bot) -> None:
    from bot.helpers import spawn_background

    now = datetime.now(timezone.utc)
    for status in ("approved", "scheduled"):
        for entry in get_requests(status=status):
            payload = entry.get("payload") or {}
            key = _notification_key(entry)
            if not payload.get("approved_at") or not payload.get("user_id") or payload.get("approval_notified_for") == key:
                continue
            retry_at = payload.get("approval_notification_retry_at")
            if retry_at:
                try:
                    value = datetime.fromisoformat(str(retry_at))
                    if value.replace(tzinfo=value.tzinfo or timezone.utc) > now:
                        continue
                except ValueError:
                    pass
            if (str(entry["id"]), key) not in _notifications_in_flight:
                spawn_background(notify_author_approval(bot, str(entry["id"])))
