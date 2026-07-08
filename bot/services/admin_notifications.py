from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from bot.cache import (
    get_admins_icons,
    get_admins_plugins,
    get_admins_super,
    get_config,
    invalidate,
)
from bot.context import get_lang
from bot.keyboards import admin_appeal_decision_kb, admin_review_kb
from bot.services.moderation import (
    forum_text_with_votes,
    moderation_config,
    request_title,
    vote_counts,
    vote_summary,
)
from bot.texts import t
from request_store import update_request_payload
from storage import save_config

logger = logging.getLogger(__name__)

NOTIFICATION_PREF_LABEL_KEYS: dict[str, str] = {
    "new_plugins": "admin_notify_pref_new_plugins",
    "updates": "admin_notify_pref_updates",
    "deletions": "admin_notify_pref_deletions",
    "icons": "admin_notify_pref_icons",
    "threshold": "admin_notify_pref_threshold",
}
NOTIFICATION_PREF_DEFAULTS: dict[str, bool] = {
    "new_plugins": True,
    "updates": True,
    "deletions": True,
    "icons": True,
    "threshold": True,
}


def admin_notification_preferences(user_id: int | None) -> dict[str, bool]:
    prefs = dict(NOTIFICATION_PREF_DEFAULTS)
    if not user_id:
        return prefs
    cfg = get_config()
    moderation = cfg.get("moderation", {}) if isinstance(cfg, dict) else {}
    raw_all = moderation.get("admin_notification_preferences") if isinstance(moderation, dict) else {}
    raw_user = raw_all.get(str(user_id)) if isinstance(raw_all, dict) else {}
    if isinstance(raw_user, dict):
        for key in NOTIFICATION_PREF_DEFAULTS:
            if key in raw_user:
                prefs[key] = bool(raw_user.get(key))
    return prefs


def admin_notification_enabled(user_id: int | None, event: str) -> bool:
    if event not in NOTIFICATION_PREF_DEFAULTS:
        return True
    return admin_notification_preferences(user_id).get(event, True)


def set_admin_notification_preference(user_id: int, event: str, enabled: bool) -> dict[str, bool]:
    if event not in NOTIFICATION_PREF_DEFAULTS:
        raise ValueError(event)
    cfg = get_config()
    moderation = cfg.setdefault("moderation", {})
    if not isinstance(moderation, dict):
        moderation = {}
        cfg["moderation"] = moderation
    all_prefs = moderation.setdefault("admin_notification_preferences", {})
    if not isinstance(all_prefs, dict):
        all_prefs = {}
        moderation["admin_notification_preferences"] = all_prefs
    user_prefs = all_prefs.setdefault(str(user_id), {})
    if not isinstance(user_prefs, dict):
        user_prefs = {}
        all_prefs[str(user_id)] = user_prefs
    user_prefs[event] = bool(enabled)
    save_config(cfg)
    invalidate("config")
    return admin_notification_preferences(user_id)


def notification_chat_ids() -> list[int]:
    cfg = get_config()
    moderation = cfg.get("moderation", {}) if isinstance(cfg, dict) else {}
    raw = moderation.get("notification_chat_ids") if isinstance(moderation, dict) else []
    if not isinstance(raw, list):
        raw = []
    result: list[int] = []
    for item in raw:
        try:
            chat_id = int(item)
        except Exception:
            continue
        if chat_id and chat_id not in result:
            result.append(chat_id)
    return result


def request_notification_event(entry: dict[str, Any]) -> str:
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    request_type = str(entry.get("type") or "new")
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        return "icons"
    if request_type == "update":
        return "updates"
    if request_type == "delete":
        return "deletions"
    return "new_plugins"


async def send_review_notification(bot, chat_id: int, entry: dict[str, Any], text: str, file_path: str | None) -> None:
    request_id = str(entry.get("id") or "")
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    user_id = int(payload.get("user_id") or 0)
    try:
        msg = await bot.send_message(
            chat_id,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=admin_review_kb(request_id, user_id, allow_publish=False),
            disable_web_page_preview=True,
        )
        file_msg = None
        if file_path and Path(file_path).exists():
            file_msg = await bot.send_document(
                chat_id,
                FSInputFile(file_path),
                reply_to_message_id=msg.message_id,
                allow_sending_without_reply=True,
                disable_notification=True,
            )
        mapping = payload.get("admin_notify_messages")
        if not isinstance(mapping, dict):
            mapping = {}
        info: dict[str, Any] = {
            "chat_id": int(chat_id),
            "kind": "text",
            "message_id": int(msg.message_id),
        }
        if file_msg:
            info["file_message_id"] = int(file_msg.message_id)
        mapping[str(chat_id)] = info
        payload["admin_notify_messages"] = mapping
        update_request_payload(request_id, {"admin_notify_messages": mapping})
    except Exception:
        logger.warning("event=submission.notify_review_target.failed request_id=%s chat_id=%s", request_id, chat_id, exc_info=True)


async def send_review_notifications(bot, entry: dict[str, Any], text: str, file_path: str | None) -> None:
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    submission_type = payload.get("submission_type") or ("icon" if payload.get("icon") else "plugin")
    superadmins = set(get_admins_super())
    if submission_type == "icon":
        admins = set(get_admins_icons()) - superadmins
    else:
        admins = set(get_admins_plugins()) - superadmins
    event = request_notification_event(entry)
    admin_targets = {
        int(x)
        for x in admins
        if str(x).lstrip("-").isdigit() and admin_notification_enabled(int(x), event)
    }
    targets = sorted(admin_targets | set(notification_chat_ids()))
    for chat_id in targets:
        await send_review_notification(bot, chat_id, entry, text, file_path)


async def refresh_admin_notify_messages(bot, entry: dict) -> None:
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    mapping = payload.get("admin_notify_messages")
    if not isinstance(mapping, dict) or not mapping:
        return

    request_id = str(entry.get("id") or "")
    if not request_id:
        return

    user_id = int(payload.get("user_id") or 0)
    text = forum_text_with_votes(entry)

    for chat_id_str, info in mapping.items():
        if not isinstance(info, dict):
            continue
        try:
            chat_id = int(info.get("chat_id") or chat_id_str)
            message_id = int(info.get("message_id") or 0)
        except Exception:
            continue
        if not message_id:
            continue
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_review_kb(request_id, user_id, allow_publish=False),
                disable_web_page_preview=True,
            )
        except Exception:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=admin_review_kb(request_id, user_id, allow_publish=False),
                )
            except Exception:
                pass


async def finalize_admin_notify_messages(
    bot,
    entry: dict,
    decision_text: str,
    actor_label: str,
) -> None:
    if not isinstance(entry, dict):
        return
    request_id = str(entry.get("id") or "")
    if not request_id:
        return
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    mapping = payload.get("admin_notify_messages")
    if not isinstance(mapping, dict) or not mapping:
        return

    final_text = f"{decision_text} {actor_label}"
    cfg = get_config()
    moderation = cfg.get("moderation", {}) if isinstance(cfg, dict) else {}
    delete_on_decision = bool(
        moderation.get("delete_review_notifications_on_decision")
        if isinstance(moderation, dict)
        else False
    )

    for chat_id_str, info in mapping.items():
        try:
            fallback_chat_id = int(chat_id_str)
        except Exception:
            continue
        if not isinstance(info, dict):
            continue
        chat_id = int(info.get("chat_id") or fallback_chat_id)
        kind = info.get("kind")
        msg_id = info.get("message_id")
        if not msg_id:
            continue
        if delete_on_decision:
            for key in ("file_message_id", "message_id"):
                try:
                    message_id = int(info.get(key) or 0)
                except Exception:
                    message_id = 0
                if not message_id:
                    continue
                try:
                    await bot.delete_message(chat_id, message_id)
                except Exception:
                    pass
            continue
        try:
            if kind == "document":
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=int(msg_id),
                    caption=final_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                )
            else:
                await bot.edit_message_text(
                    final_text,
                    chat_id=chat_id,
                    message_id=int(msg_id),
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                    disable_web_page_preview=True,
                )
        except Exception:
            pass

    try:
        update_request_payload(request_id, {"admin_notify_messages": {}})
    except Exception:
        pass


async def notify_superadmins_if_threshold(bot, entry: dict) -> None:
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    if payload.get("moderation_threshold_notified_at"):
        return
    _, _, total = vote_counts(entry)
    threshold = moderation_config()["threshold"]
    if total < threshold:
        return

    request_id = str(entry.get("id") or "")
    if not request_id:
        return
    is_appeal = entry.get("type") == "unban_appeal"
    if is_appeal:
        base = str(payload.get("moderation_forum_text") or "").strip() or html.escape(request_title(entry))
        text = (
            f"{t('appeal_threshold_title', 'ru')}\n\n"
            f"{base}\n\n"
            f"{vote_summary(entry)}"
        )
    else:
        title = html.escape(request_title(entry))
        text = (
            f"{t('moderation_threshold_title', 'ru')}\n\n"
            f"<b>ID:</b> <code>{html.escape(request_id)}</code>\n"
            f"<b>Заявка:</b> {title}\n\n"
            f"{vote_summary(entry)}"
        )

    delivered = 0
    for admin_id in get_admins_super():
        if not admin_notification_enabled(int(admin_id), "threshold"):
            continue
        try:
            reply_markup = (
                admin_appeal_decision_kb(request_id, lang=get_lang(admin_id))
                if is_appeal else
                admin_review_kb(request_id, 0, lang=get_lang(admin_id))
            )
            await bot.send_message(
                admin_id,
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            delivered += 1
        except Exception:
            continue
    if delivered:
        update_request_payload(
            request_id,
            {"moderation_threshold_notified_at": datetime.now(timezone.utc).isoformat()},
        )
