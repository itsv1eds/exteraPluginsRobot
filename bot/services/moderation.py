from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from bot.cache import get_admins, get_config
from bot.formatting import code_html, quote_html, strip_blockquote_tags, telegram_html
from bot.helpers import link_preview_options
from bot.keyboards import moderation_appeal_kb, moderation_vote_kb


def _forum_reply_markup(entry: dict | None, request_id: str, yes: int, no: int):
    if isinstance(entry, dict) and entry.get("type") == "unban_appeal":
        return moderation_appeal_kb(request_id, yes, no)
    return moderation_vote_kb(request_id, yes, no)
from request_store import get_request_by_id, update_request_payload

VoteValue = Literal["yes", "no"]

def moderation_config() -> dict[str, int]:
    cfg = get_config()
    raw = cfg.get("moderation", {}) if isinstance(cfg, dict) else {}
    if not isinstance(raw, dict):
        raw = {}

    try:
        chat_id = int(raw["forum_chat_id"])
    except Exception as exc:
        raise RuntimeError("moderation.forum_chat_id is not configured in SQLite config") from exc
    if chat_id > 0:
        chat_id = int(f"-100{chat_id}")

    try:
        topic_id = int(raw["forum_topic_id"])
    except Exception as exc:
        raise RuntimeError("moderation.forum_topic_id is not configured in SQLite config") from exc

    try:
        threshold = max(1, int(raw["vote_threshold"]))
    except Exception as exc:
        raise RuntimeError("moderation.vote_threshold is not configured in SQLite config") from exc

    return {"chat_id": chat_id, "topic_id": topic_id, "threshold": threshold}


def is_moderation_forum_chat(chat_id: int | None) -> bool:
    return bool(chat_id and int(chat_id) == moderation_config()["chat_id"])


def vote_counts(entry: dict | None) -> tuple[int, int, int]:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    votes = payload.get("moderation_votes") if isinstance(payload, dict) else {}
    if not isinstance(votes, dict):
        return 0, 0, 0
    yes = 0
    no = 0
    for item in votes.values():
        if not isinstance(item, dict):
            continue
        if item.get("vote") == "yes":
            yes += 1
        elif item.get("vote") == "no":
            no += 1
    return yes, no, yes + no


def rejection_reasons(entry: dict | None) -> list[str]:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    votes = payload.get("moderation_votes") if isinstance(payload, dict) else {}
    out: list[str] = []
    if not isinstance(votes, dict):
        return out
    for item in votes.values():
        if not isinstance(item, dict) or item.get("vote") != "no":
            continue
        reason = str(item.get("reason") or "").strip()
        if reason:
            out.append(strip_blockquote_tags(telegram_html(reason)))
    return out


def vote_summary(entry: dict | None) -> str:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    votes = payload.get("moderation_votes") if isinstance(payload, dict) else {}
    yes, no, total = vote_counts(entry)
    if not isinstance(votes, dict) or not votes:
        return "<b>Голоса:</b> 0"

    header = f"<b>Голоса:</b> {total}  |  За: {yes}  |  Отказано: {no}"
    details: list[str] = []
    for item in votes.values():
        if not isinstance(item, dict):
            continue
        mark = "За" if item.get("vote") == "yes" else "Отказано"
        username = str(item.get("username") or "").strip()
        display = f"@{username}" if username else str(item.get("name") or item.get("user_id") or "?")
        reason = str(item.get("reason") or "").strip()
        reason_text = strip_blockquote_tags(telegram_html(reason)) if reason else "без причины"
        details.append(f"• <b>{mark}</b> — {code_html(display)}:\n{reason_text}")

    if not details:
        return header
    if total > 3:
        return f"{header}\n{quote_html(chr(10).join(details), expandable=True)}"
    return "\n".join([header, *details])


def forum_text_with_votes(entry: dict | None) -> str:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    base = ""
    if isinstance(payload, dict):
        base = str(payload.get("moderation_forum_text") or "").strip()
    if not base:
        base = f"<b>Заявка:</b> {telegram_html(request_title(entry))}"
    return f"{base}\n\n{vote_summary(entry)}"


def request_title(entry: dict | None) -> str:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    if not isinstance(payload, dict):
        return str((entry or {}).get("id") or "—")
    plugin = payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}
    icon = payload.get("icon") if isinstance(payload.get("icon"), dict) else {}
    return (
        plugin.get("name")
        or icon.get("name")
        or payload.get("delete_slug")
        or str((entry or {}).get("id") or "—")
    )


def set_vote(
    request_id: str,
    user_id: int,
    username: str,
    name: str,
    vote: VoteValue,
    reason: str | None = None,
) -> dict | None:
    entry = get_request_by_id(request_id)
    if not entry:
        return None
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    votes = payload.get("moderation_votes")
    if not isinstance(votes, dict):
        votes = {}
    current = votes.get(str(user_id)) if isinstance(votes.get(str(user_id)), dict) else {}
    votes[str(user_id)] = {
        **current,
        "user_id": int(user_id),
        "username": username or current.get("username", ""),
        "name": name or current.get("name", ""),
        "vote": vote,
        "reason": reason if reason is not None else current.get("reason", ""),
        "voted_at": datetime.now(timezone.utc).isoformat(),
    }
    return update_request_payload(request_id, {"moderation_votes": votes})


def set_vote_reason(request_id: str, user_id: int, reason: str) -> dict | None:
    entry = get_request_by_id(request_id)
    if not entry:
        return None
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    votes = payload.get("moderation_votes")
    if not isinstance(votes, dict) or str(user_id) not in votes:
        return None
    item = votes.get(str(user_id))
    if not isinstance(item, dict):
        return None
    item["reason"] = reason
    item["reason_at"] = datetime.now(timezone.utc).isoformat()
    return update_request_payload(request_id, {"moderation_votes": votes})


async def send_request_to_forum(bot, entry: dict, text: str, file_path: str | None = None) -> None:
    cfg = moderation_config()
    request_id = str(entry.get("id") or "")
    if not request_id:
        return

    entry = update_request_payload(request_id, {"moderation_forum_text": text}) or entry
    rendered_text = forum_text_with_votes(entry)
    yes, no, _ = vote_counts(entry)
    reply_markup = _forum_reply_markup(entry, request_id, yes, no)
    chat_id = cfg["chat_id"]
    topic_id = cfg["topic_id"]
    img_key = "appeal" if entry.get("type") == "unban_appeal" else "new"

    msg = await bot.send_message(
        chat_id,
        rendered_text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=False,
        link_preview_options=link_preview_options(img_key),
        message_thread_id=topic_id,
    )
    file_msg = None
    if file_path and Path(file_path).exists():
        file_msg = await bot.send_document(
            chat_id,
            FSInputFile(file_path),
            message_thread_id=topic_id,
            reply_to_message_id=msg.message_id,
            allow_sending_without_reply=True,
        )

    if msg:
        actual_topic_id = int(getattr(msg, "message_thread_id", None) or topic_id)
        info = {
            "chat_id": chat_id,
            "message_thread_id": actual_topic_id,
            "message_id": int(msg.message_id),
        }
        payload_update: dict[str, object] = {"moderation_forum_message": info}
        if file_msg:
            info["file_message_id"] = int(file_msg.message_id)
            document = getattr(file_msg, "document", None)
            file_id = str(getattr(document, "file_id", "") or "").strip()
            file_name = str(getattr(document, "file_name", "") or "").strip()
            if file_id:
                info["file_id"] = file_id
                payload_update["moderation_file_id"] = file_id
            if file_name:
                info["file_name"] = file_name
                payload_update["moderation_file_name"] = file_name
        update_request_payload(request_id, payload_update)


async def refresh_forum_vote_keyboard(bot, entry: dict) -> None:
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    info = payload.get("moderation_forum_message")
    if not isinstance(info, dict):
        return
    yes, no, _ = vote_counts(entry)
    request_id = str(entry.get("id") or "")
    if not request_id:
        return
    chat_id = int(info["chat_id"])
    message_id = int(info["message_id"])
    reply_markup = _forum_reply_markup(entry, request_id, yes, no)
    text_message_id = info.get("text_message_id")
    has_base_text = bool(str(payload.get("moderation_forum_text") or "").strip())
    if not has_base_text:
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        except Exception:
            pass
        return

    rendered_text = forum_text_with_votes(entry)

    if text_message_id:
        try:
            await bot.edit_message_text(
                rendered_text,
                chat_id=chat_id,
                message_id=int(text_message_id),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
                link_preview_options=link_preview_options("new"),
            )
        except Exception:
            pass
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        except Exception:
            pass
        return

    try:
        await bot.edit_message_text(
            rendered_text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            disable_web_page_preview=False,
            link_preview_options=link_preview_options("new"),
        )
        return
    except Exception:
        pass

    if len(rendered_text) <= 1024:
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=rendered_text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            return
        except Exception:
            pass

    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
    except Exception:
        pass


async def delete_forum_request_message(bot, entry: dict | None) -> None:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    info = payload.get("moderation_forum_message") if isinstance(payload, dict) else None
    if not isinstance(info, dict):
        return

    chat_id = info.get("chat_id")
    if not chat_id:
        return

    message_ids: list[int] = []
    for key in ("text_message_id", "file_message_id", "message_id"):
        try:
            message_id = int(info.get(key) or 0)
        except Exception:
            message_id = 0
        if message_id and message_id not in message_ids:
            message_ids.append(message_id)

    for message_id in message_ids:
        try:
            await bot.delete_message(int(chat_id), message_id)
        except Exception:
            pass


def can_vote_in_context(user_id: int | None, chat_id: int | None) -> bool:
    if not user_id:
        return False
    if is_moderation_forum_chat(chat_id):
        return True
    return int(user_id) in get_admins()
