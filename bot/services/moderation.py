from __future__ import annotations

import logging

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from bot.cache import get_admins, get_config
from bot.formatting import code_html, quote_html, split_html, strip_blockquote_tags, telegram_html
from bot.helpers import blank_and_delete, link_preview_options
from bot.keyboards import moderation_appeal_kb, moderation_vote_kb
from bot import limits

logger = logging.getLogger(__name__)


def _forum_reply_markup(entry: dict | None, request_id: str, yes: int, no: int):
    if isinstance(entry, dict) and entry.get("type") == "unban_appeal":
        return moderation_appeal_kb(request_id, yes, no)
    return moderation_vote_kb(request_id, yes, no)
from request_store import get_request_by_id, update_request_payload

VoteValue = Literal["yes", "no"]
VOTABLE_REQUEST_STATUSES = frozenset({"pending", "error", "scheduled"})


def can_accept_vote(entry: dict | None) -> bool:
    return bool(isinstance(entry, dict) and entry.get("status") in VOTABLE_REQUEST_STATUSES)

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


def _format_votes_block(votes: dict, header_label: str = "Голоса") -> str:
    if not isinstance(votes, dict) or not votes:
        return f"<b>{header_label}:</b> 0"
    yes = sum(1 for v in votes.values() if isinstance(v, dict) and v.get("vote") == "yes")
    no = sum(1 for v in votes.values() if isinstance(v, dict) and v.get("vote") == "no")
    total = yes + no
    header = f"<b>{header_label}:</b> {total}  |  За: {yes}  |  Отказано: {no}"
    details: list[str] = []
    for item in votes.values():
        if not isinstance(item, dict):
            continue
        mark = "За" if item.get("vote") == "yes" else "Отказано"
        if item.get("anonymous"):
            display = "Аноним"
        else:
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


def vote_summary(entry: dict | None) -> str:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    votes = payload.get("moderation_votes") if isinstance(payload, dict) else {}
    return _format_votes_block(votes if isinstance(votes, dict) else {})


def previous_rounds_text(entry: dict | None) -> str:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    rounds = payload.get("previous_vote_rounds") if isinstance(payload, dict) else None
    if not isinstance(rounds, list) or not rounds:
        return ""
    blocks: list[str] = []
    for rnd in rounds:
        if not isinstance(rnd, dict):
            continue
        votes = rnd.get("votes")
        if not isinstance(votes, dict) or not votes:
            continue
        n = rnd.get("round") or (len(blocks) + 1)
        blocks.append(_format_votes_block(votes, header_label=f"Раунд {n} (до доработки)"))
    if not blocks:
        return ""
    return "🗳 <b>Прошлое голосование:</b>\n" + "\n\n".join(blocks)


def archive_votes_for_rework(request_id: str) -> dict | None:
    entry = get_request_by_id(request_id)
    if not entry:
        return None
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    votes = payload.get("moderation_votes")
    rounds = payload.get("previous_vote_rounds")
    rounds = list(rounds) if isinstance(rounds, list) else []
    if isinstance(votes, dict) and votes:
        rounds.append({
            "round": len(rounds) + 1,
            "votes": votes,
            "archived_at": datetime.now(timezone.utc).isoformat(),
        })
    return update_request_payload(request_id, {
        "moderation_votes": {},
        "previous_vote_rounds": rounds,
        "resubmitted_after_rework": True,
    })


def forum_text_with_votes(entry: dict | None) -> str:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    base = ""
    if isinstance(payload, dict):
        base = str(payload.get("moderation_forum_text") or "").strip()
    if not base:
        base = f"<b>Заявка:</b> {telegram_html(request_title(entry))}"

    parts = [base]
    if is_unban_appeal(entry):
        from bot.texts import t as _t

        parts.append(_t("appeal_forum_badge", "ru"))
    if isinstance(payload, dict) and payload.get("is_appeal"):
        from bot.texts import t as _t

        parts.append(_t(
            "admin_appeal_badge", "ru",
            comment=strip_blockquote_tags(telegram_html(str(payload.get("appeal_comment") or "—"))),
        ))
    if isinstance(payload, dict) and payload.get("resubmitted_after_rework"):
        parts.append("♻️ <b>Отправлено после доработки</b> (плагин уже был на модерации)")
    parts.append(vote_summary(entry))
    prev = previous_rounds_text(entry)
    if prev:
        parts.append(prev)
    return "\n\n".join(parts)


def author_reply_kwargs(entry: dict | None) -> dict:
    payload = entry.get("payload") if isinstance(entry, dict) else None
    message_id = (payload or {}).get("author_message_id") if isinstance(payload, dict) else None
    try:
        message_id = int(message_id or 0)
    except (TypeError, ValueError):
        message_id = 0
    if not message_id:
        return {}
    return {"reply_to_message_id": message_id, "allow_sending_without_reply": True}


def is_unban_appeal(entry: dict | None) -> bool:
    return isinstance(entry, dict) and entry.get("type") == "unban_appeal"


def request_title(entry: dict | None) -> str:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    if not isinstance(payload, dict):
        return str((entry or {}).get("id") or "—")
    if is_unban_appeal(entry):
        from bot.texts import t as _t

        username = str(payload.get("username") or "").strip()
        who = f"@{username}" if username else str(payload.get("user_id") or "—")
        return _t("appeal_request_title", "ru", user=who)
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
    anonymous: bool | None = None,
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
        "anonymous": bool(current.get("anonymous")) if anonymous is None else bool(anonymous),
        "voted_at": datetime.now(timezone.utc).isoformat(),
    }
    return update_request_payload(request_id, {"moderation_votes": votes})


def send_reasons_to_author_default() -> bool:
    cfg = get_config()
    raw = (cfg.get("moderation") or {}) if isinstance(cfg, dict) else {}
    value = raw.get("send_reasons_to_author")
    return True if value is None else bool(value)


def require_vote_reason() -> bool:
    cfg = get_config()
    raw = (cfg.get("moderation") or {}) if isinstance(cfg, dict) else {}
    value = raw.get("require_vote_reason")
    return True if value is None else bool(value)


def _pending_map(entry: dict | None) -> dict:
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    pending = payload.get("moderation_pending_votes") if isinstance(payload, dict) else None
    return pending if isinstance(pending, dict) else {}


def get_pending_vote(request_id: str, user_id: int) -> dict | None:
    item = _pending_map(get_request_by_id(request_id)).get(str(user_id))
    return item if isinstance(item, dict) else None


def start_pending_vote(
    request_id: str,
    user_id: int,
    username: str,
    name: str,
    vote: VoteValue,
) -> dict | None:
    entry = get_request_by_id(request_id)
    if not entry:
        return None
    pending = dict(_pending_map(entry))
    prev = pending.get(str(user_id)) if isinstance(pending.get(str(user_id)), dict) else {}
    pending[str(user_id)] = {
        "user_id": int(user_id),
        "username": username or prev.get("username", ""),
        "name": name or prev.get("name", ""),
        "vote": vote,
        "anonymous": bool(prev.get("anonymous")),
        "prompt_chat_id": int(prev.get("prompt_chat_id") or 0),
        "prompt_message_id": int(prev.get("prompt_message_id") or 0),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    return update_request_payload(request_id, {"moderation_pending_votes": pending})


def update_pending_vote(request_id: str, user_id: int, **fields) -> dict | None:
    entry = get_request_by_id(request_id)
    if not entry:
        return None
    pending = dict(_pending_map(entry))
    item = pending.get(str(user_id))
    if not isinstance(item, dict):
        return None
    item.update(fields)
    pending[str(user_id)] = item
    return update_request_payload(request_id, {"moderation_pending_votes": pending})


def clear_pending_vote(request_id: str, user_id: int) -> dict | None:
    entry = get_request_by_id(request_id)
    if not entry:
        return None
    pending = dict(_pending_map(entry))
    if pending.pop(str(user_id), None) is None:
        return entry
    return update_request_payload(request_id, {"moderation_pending_votes": pending})


def commit_pending_vote(request_id: str, user_id: int, reason: str) -> dict | None:
    item = get_pending_vote(request_id, user_id)
    if not item:
        return None
    entry = set_vote(
        request_id,
        int(user_id),
        str(item.get("username") or ""),
        str(item.get("name") or ""),
        item.get("vote"),
        reason=reason,
        anonymous=bool(item.get("anonymous")),
    )
    clear_pending_vote(request_id, user_id)
    return get_request_by_id(request_id) or entry


_FORUM_IMG_BY_TYPE = {"unban_appeal": "appeal", "update": "update", "delete": "delete"}


def _forum_image_key(entry: dict | None) -> str:
    entry = entry or {}
    return _FORUM_IMG_BY_TYPE.get(str(entry.get("type")), "new")


async def send_media_group(bot, chat_id: int, media: list, *, topic_id: int | None = None,
                           reply_to: int | None = None, caption: str | None = None) -> list:
    from aiogram.types import InputMediaPhoto, InputMediaVideo

    items = [m for m in (media or []) if isinstance(m, dict) and m.get("file_id")][: limits.ALBUM_ITEMS]
    if not items:
        return []
    group = []
    for idx, item in enumerate(items):
        cls = InputMediaVideo if item.get("type") == "video" else InputMediaPhoto
        kwargs = {"media": item["file_id"]}
        if idx == 0 and caption:
            kwargs["caption"] = caption[: limits.CAPTION]
            kwargs["parse_mode"] = ParseMode.HTML
        group.append(cls(**kwargs))
    kw = {}
    if topic_id:
        kw["message_thread_id"] = topic_id
    if reply_to:
        kw["reply_to_message_id"] = reply_to
        kw["allow_sending_without_reply"] = True
    try:
        sent = await bot.send_media_group(chat_id, group, **kw)
        return [int(m.message_id) for m in sent]
    except Exception:
        logger.exception("send_media_group failed chat_id=%s count=%s", chat_id, len(group))
        return []


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
    img_key = _forum_image_key(entry)

    parts = split_html(rendered_text, limits.MESSAGE_TEXT)
    sent_message_ids: list[int] = []
    msg = None
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        msg = await bot.send_message(
            chat_id,
            part,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup if last else None,
            disable_web_page_preview=False,
            link_preview_options=link_preview_options(img_key) if last else None,
            message_thread_id=topic_id,
        )
        sent_message_ids.append(int(msg.message_id))
    if msg is None:
        return
    if len(parts) > 1:
        logger.info("forum request sent in parts request_id=%s parts=%s", request_id, len(parts))
    file_msg = None
    try:
        if file_path and Path(file_path).exists():
            file_msg = await bot.send_document(
                chat_id,
                FSInputFile(file_path),
                message_thread_id=topic_id,
                reply_to_message_id=msg.message_id,
                allow_sending_without_reply=True,
            )
            sent_message_ids.append(int(file_msg.message_id))

        payload_now = entry.get("payload", {}) if isinstance(entry, dict) else {}
        comment_media = payload_now.get("comment_media") if isinstance(payload_now, dict) else None
        media_ids = []
        if comment_media:
            media_ids = await send_media_group(
                bot, chat_id, comment_media,
                topic_id=topic_id, reply_to=msg.message_id,
            )
            if not media_ids:
                raise RuntimeError("Failed to send request comment media")
            sent_message_ids.extend(media_ids)

        actual_topic_id = int(getattr(msg, "message_thread_id", None) or topic_id)
        info = {
            "chat_id": chat_id,
            "message_thread_id": actual_topic_id,
            "message_id": int(msg.message_id),
        }
        payload_update: dict[str, object] = {"moderation_forum_message": info}
        head_ids = [i for i in sent_message_ids if i != int(msg.message_id)]
        if head_ids:
            info["extra_message_ids"] = head_ids
        if media_ids:
            info["comment_media_message_ids"] = media_ids
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
    except Exception:
        for message_id in reversed(sent_message_ids):
            try:
                await blank_and_delete(bot, chat_id, message_id)
            except Exception:
                logger.warning(
                    "forum delivery rollback failed request_id=%s message_id=%s",
                    request_id,
                    message_id,
                    exc_info=True,
                )
        raise


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
                link_preview_options=link_preview_options(_forum_image_key(entry)),
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
            link_preview_options=link_preview_options(_forum_image_key(entry)),
        )
        return
    except Exception:
        pass

    if len(rendered_text) <= limits.CAPTION:
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
    for raw in (info.get("extra_message_ids") or []):
        try:
            message_id = int(raw or 0)
        except Exception:
            continue
        if message_id and message_id not in message_ids:
            message_ids.append(message_id)

    for message_id in message_ids:
        try:
            await blank_and_delete(bot, int(chat_id), message_id)
        except Exception:
            pass


def can_vote_in_context(user_id: int | None, chat_id: int | None) -> bool:
    if not user_id:
        return False
    if is_moderation_forum_chat(chat_id):
        return True
    return int(user_id) in get_admins()
