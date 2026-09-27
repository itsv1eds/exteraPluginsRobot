from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from aiogram.exceptions import TelegramRetryAfter
from aiogram.enums import ParseMode
from telethon.errors import MessageIdInvalidError, MsgIdInvalidError

from bot import limits
from bot.formatting import plain_html, split_html, strip_blockquote_tags, telegram_html
from bot.services.moderation import send_media_group
from bot.texts import t
from catalog import find_plugin_by_slug
from request_store import get_all_requests, get_request_by_id, update_request_payload

logger = logging.getLogger(__name__)

_worker_task: asyncio.Task | None = None
_active: set[str] = set()
_interval = 60
EXAMPLE_MEDIA_DELAY_SECONDS = 5


async def _get_userbot():
    from userbot.client import get_userbot

    return await get_userbot()


def _media(entry: dict[str, Any]) -> list[dict[str, Any]]:
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    return [
        item for item in (payload.get("comment_media") or [])
        if isinstance(item, dict) and item.get("file_id") and item.get("type") in {"photo", "video"}
    ]


def _channel_message(entry: dict[str, Any]) -> dict[str, Any]:
    direct = entry.get("channel_message")
    if isinstance(direct, dict) and direct.get("message_id"):
        return direct
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    plugin = payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}
    candidates = [payload.get("update_slug"), plugin.get("id"), plugin.get("name")]
    from bot.services.publish import make_slug

    for candidate in candidates:
        if not candidate:
            continue
        catalog_entry = find_plugin_by_slug(str(candidate)) or find_plugin_by_slug(make_slug(str(candidate)))
        channel_message = catalog_entry.get("channel_message") if isinstance(catalog_entry, dict) else None
        if isinstance(channel_message, dict) and channel_message.get("message_id"):
            return channel_message
    return {}


def eligible(entry: dict[str, Any]) -> bool:
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    has_changelog = bool(
        entry.get("type") == "update"
        and payload.get("example_changelog_pending")
        and str(payload.get("changelog") or "").strip()
    )
    not_before = str(payload.get("example_media_not_before") or "")
    if not_before:
        try:
            scheduled_at = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
            if scheduled_at > datetime.now(timezone.utc):
                return False
        except ValueError:
            pass
    return bool(
        entry.get("status") == "published"
        and entry.get("type") in {"new", "update"}
        and (_media(entry) or has_changelog)
        and payload.get("example_media_status") not in {"published", "sending", "uncertain"}
    )


def _changelog_text(entry: dict[str, Any]) -> str:
    if entry.get("type") != "update":
        return ""
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    changelog = strip_blockquote_tags(telegram_html(str(payload.get("changelog") or ""))).strip()
    if not changelog:
        return ""
    plugin = payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}
    return t(
        "notify_subscription_update",
        "ru",
        name=plain_html(plugin.get("name") or plugin.get("id") or "—"),
        version=plain_html(plugin.get("version") or "—"),
        changelog=changelog,
    )


async def publish_example_media(bot, request_id: str) -> str:
    request_id = str(request_id or "")
    if not request_id or request_id in _active:
        return "skipped"
    _active.add(request_id)
    try:
        entry = get_request_by_id(request_id)
        if not entry or not eligible(entry):
            return "skipped"
        channel_message = _channel_message(entry)
        channel_message_id = int(channel_message.get("message_id") or 0)
        if not channel_message_id:
            update_request_payload(request_id, {
                "example_media_status": "pending",
                "example_media_error": "channel_message_not_found",
            })
            return "pending"
        try:
            userbot = await _get_userbot()
            discussion = await userbot.resolve_discussion_message(channel_message_id) if userbot else None
        except (MessageIdInvalidError, MsgIdInvalidError):
            update_request_payload(request_id, {
                "example_media_status": "uncertain",
                "example_media_error": "discussion_message_invalid",
            })
            logger.warning("event=example_media.discussion_invalid request_id=%s manual_retry_required", request_id)
            return "uncertain"
        except Exception as exc:
            logger.warning("event=example_media.resolve_failed request_id=%s error=%s", request_id, exc)
            discussion = None
        if not discussion:
            update_request_payload(request_id, {
                "example_media_status": "pending",
                "example_media_error": "discussion_message_not_ready",
            })
            return "pending"
        discussion_chat_id, discussion_message_id = discussion
        update_request_payload(request_id, {
            "example_media_status": "sending",
            "example_media_started_at": datetime.now(timezone.utc).isoformat(),
            "example_media_discussion_chat_id": discussion_chat_id,
            "example_media_discussion_message_id": discussion_message_id,
        })
        try:
            changelog_message_ids: list[int] = []
            changelog_text = _changelog_text(entry)
            for part in split_html(changelog_text, limits.MESSAGE_TEXT) if changelog_text else []:
                sent = await bot.send_message(
                    discussion_chat_id,
                    part,
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=discussion_message_id,
                    allow_sending_without_reply=True,
                )
                changelog_message_ids.append(int(sent.message_id))
            message_ids = await send_media_group(
                bot,
                discussion_chat_id,
                _media(entry),
                reply_to=discussion_message_id,
                raise_errors=True,
            )
        except TelegramRetryAfter as exc:
            update_request_payload(request_id, {
                "example_media_status": "pending",
                "example_media_error": "floodwait",
                "example_media_retry_after": int(getattr(exc, "retry_after", 1) or 1),
            })
            return "pending"
        except Exception as exc:
            update_request_payload(request_id, {
                "example_media_status": "uncertain",
                "example_media_error": str(exc)[:300] or "telegram_delivery_failed",
            })
            return "uncertain"
        update_request_payload(request_id, {
            "example_media_status": "published",
            "example_media_message_ids": message_ids,
            "example_changelog_message_ids": changelog_message_ids,
            "example_changelog_pending": False,
            "example_media_published_at": datetime.now(timezone.utc).isoformat(),
            "example_media_error": "",
            "example_media_not_before": "",
        })
        return "published"
    finally:
        _active.discard(request_id)


async def process_pending_example_media(bot, limit: int = 20) -> dict[str, int]:
    counts = {"published": 0, "pending": 0, "uncertain": 0, "skipped": 0}
    requests = [entry for entry in get_all_requests() if eligible(entry)][:max(1, int(limit))]
    for entry in requests:
        result = await publish_example_media(bot, str(entry.get("id") or ""))
        counts[result] = counts.get(result, 0) + 1
        current = get_request_by_id(str(entry.get("id") or "")) or {}
        payload = current.get("payload") if isinstance(current.get("payload"), dict) else {}
        if payload.get("example_media_error") == "floodwait":
            break
        await asyncio.sleep(1)
    return counts


async def schedule_example_media(bot, request_id: str, delay: int = EXAMPLE_MEDIA_DELAY_SECONDS) -> str:
    await asyncio.sleep(max(0, int(delay)))
    return await publish_example_media(bot, request_id)


async def _worker(bot) -> None:
    while True:
        try:
            await process_pending_example_media(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("event=example_media.worker_failed")
        await asyncio.sleep(_interval)


def start_example_media_worker(bot) -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_worker(bot))


async def stop_example_media_worker() -> None:
    global _worker_task
    task = _worker_task
    _worker_task = None
    if not task:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
