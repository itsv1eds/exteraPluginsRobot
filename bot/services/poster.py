
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from bot import limits
from bot.formatting import telegram_html, visible_html_length
from bot.helpers import BLANK_CHAR
from bot.services.channel_text import editing_userbot, send_description, try_edit

from storage import load_poster, save_poster

logger = logging.getLogger(__name__)

POST_STATUSES = ("scheduled", "sending", "sent", "failed", "canceled")

_TG_EMOJI_ANCHOR_RE = re.compile(
    r'<a\s+href="tg://emoji\?id=(\d+)"\s*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)


@dataclass(slots=True)
class SentContent:
    primary: Any
    messages: List[Any]

    @property
    def message_id(self) -> Optional[int]:
        value = getattr(self.primary, "message_id", None)
        return int(value) if value is not None else None

    @property
    def message_ids(self) -> List[int]:
        return sent_message_ids(self)


def sent_message_ids(result: Any) -> List[int]:
    messages = result.messages if isinstance(result, SentContent) else [result]
    ids: List[int] = []
    for message in messages:
        value = getattr(message, "message_id", None)
        if value is None:
            continue
        message_id = int(value)
        if message_id not in ids:
            ids.append(message_id)
    return ids


async def _rollback_sent_messages(bot, chat_id: int, messages: List[Any]) -> None:
    for message in reversed(messages):
        message_id = getattr(message, "message_id", None)
        if message_id is None:
            continue
        try:
            await bot.delete_message(chat_id, int(message_id))
        except Exception:
            logger.warning(
                "poster: rollback delete failed chat=%s msg=%s",
                chat_id,
                message_id,
                exc_info=True,
            )


def normalize_custom_emoji(html: str) -> str:
    if not html:
        return ""
    return _TG_EMOJI_ANCHOR_RE.sub(r'<tg-emoji emoji-id="\1">\2</tg-emoji>', html)


def updated_block_title() -> str:
    from bot.texts import t
    return t("admin_updated_block_title", "ru")


def ensure_updated_plugins_quote(text: str) -> str:
    title = updated_block_title()
    position = text.rfind(title) if title else -1
    if position < 0:
        return text
    suffix = text[position + len(title):]
    if not suffix.strip():
        return text
    quote = re.match(
        r"(?P<space>\s*)<blockquote(?P<attributes>[^>]*)>(?P<body>.*?)</blockquote>",
        suffix,
        re.IGNORECASE | re.DOTALL,
    )
    if quote:
        attributes = quote.group("attributes") or ""
        if re.search(r"\bexpandable\b", attributes, re.IGNORECASE):
            return text
        replacement = (
            f"{quote.group('space')}<blockquote{attributes} expandable>"
            f"{quote.group('body')}</blockquote>"
        )
        prefix = text[:position + len(title)]
        return prefix + suffix[:quote.start()] + replacement + suffix[quote.end():]
    prefix = text[:position + len(title)]
    return f"{prefix}\n<blockquote expandable>\n{suffix.strip()}\n</blockquote>"


def build_updated_plugins_text(limit: int = 30) -> str:
    import html as _html
    from storage import load_updated

    items = load_updated().get("items") or []
    lines = [updated_block_title()]
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        link = str(item.get("link") or "").strip()
        if not name:
            continue
        if link:
            lines.append(f'• <a href="{_html.escape(link, quote=True)}">{_html.escape(name)}</a>')
        else:
            lines.append(f"• {_html.escape(name)}")
    return ensure_updated_plugins_quote("\n".join(lines)) if len(lines) > 1 else ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def can_manage(channel: Optional[Dict[str, Any]], user_id: int) -> bool:
    if not isinstance(channel, dict):
        return False
    if channel.get("owner_user_id") == user_id:
        return True
    return user_id in (channel.get("admin_ids") or [])


def list_channels(user_id: int) -> List[Dict[str, Any]]:
    doc = load_poster()
    return [c for c in doc.get("channels", [])
            if isinstance(c, dict) and can_manage(c, user_id)]


def get_channel(chat_id: int) -> Optional[Dict[str, Any]]:
    doc = load_poster()
    for c in doc.get("channels", []):
        if isinstance(c, dict) and c.get("chat_id") == chat_id:
            return c
    return None


def upsert_channel(chat_id: int, title: str, username: str, owner_user_id: int,
                   admin_ids: Optional[List[int]] = None,
                   admin_labels: Optional[List[str]] = None) -> Dict[str, Any]:
    doc = load_poster()
    channels = [c for c in doc.get("channels", []) if isinstance(c, dict)]
    channels = [c for c in channels if c.get("chat_id") != chat_id]
    entry = {
        "chat_id": chat_id,
        "title": title or str(chat_id),
        "username": (username or "").lstrip("@"),
        "owner_user_id": owner_user_id,
        "admin_ids": list(admin_ids or []),
        "admin_labels": list(admin_labels or []),
        "added_at": _now_iso(),
    }
    channels.append(entry)
    doc["channels"] = channels
    save_poster(doc)
    return entry


def remove_channel(chat_id: int, user_id: int) -> bool:
    doc = load_poster()
    channels = [c for c in doc.get("channels", []) if isinstance(c, dict)]
    kept = [c for c in channels
            if not (c.get("chat_id") == chat_id and can_manage(c, user_id))]
    if len(kept) == len(channels):
        return False
    for post in doc.get("posts", []):
        if isinstance(post, dict) and post.get("chat_id") == chat_id and post.get("status") == "scheduled":
            post["status"] = "canceled"
    doc["channels"] = kept
    save_poster(doc)
    return True


def list_user_posts(owner_user_id: int, statuses: Optional[tuple] = None) -> List[Dict[str, Any]]:
    doc = load_poster()
    out = []
    for post in doc.get("posts", []):
        if not isinstance(post, dict) or post.get("owner_user_id") != owner_user_id:
            continue
        if statuses and post.get("status") not in statuses:
            continue
        out.append(post)
    out.sort(key=lambda p: str(p.get("run_at") or ""))
    return out


def get_post(post_id: str) -> Optional[Dict[str, Any]]:
    doc = load_poster()
    for post in doc.get("posts", []):
        if isinstance(post, dict) and post.get("id") == post_id:
            return post
    return None


def add_post(owner_user_id: int, chat_id: int, run_at_iso: str,
             content: Dict[str, Any], kind: str = "manual") -> Dict[str, Any]:
    doc = load_poster()
    posts = [p for p in doc.get("posts", []) if isinstance(p, dict)]
    entry = {
        "id": uuid4().hex[:12],
        "owner_user_id": owner_user_id,
        "chat_id": chat_id,
        "run_at": run_at_iso,
        "status": "scheduled",
        "kind": kind,
        "content": content,
        "created_at": _now_iso(),
        "sent_message_id": None,
        "sent_message_ids": [],
        "error": None,
    }
    posts.append(entry)
    doc["posts"] = posts
    save_poster(doc)
    return entry


def cancel_post(post_id: str, owner_user_id: int) -> bool:
    doc = load_poster()
    changed = False
    for post in doc.get("posts", []):
        if (isinstance(post, dict) and post.get("id") == post_id
                and post.get("owner_user_id") == owner_user_id
                and post.get("status") == "scheduled"):
            post["status"] = "canceled"
            changed = True
    if changed:
        save_poster(doc)
    return changed


_in_flight: set = set()


def _claim_post(post_id: str) -> bool:
    if not post_id or post_id in _in_flight:
        return False
    doc = load_poster()
    for post in doc.get("posts", []):
        if isinstance(post, dict) and post.get("id") == post_id:
            if post.get("status") != "scheduled":
                return False
            post["status"] = "sending"
            save_poster(doc)
            _in_flight.add(post_id)
            return True
    return False


def _update_post(post_id: str, **fields: Any) -> None:
    doc = load_poster()
    for post in doc.get("posts", []):
        if isinstance(post, dict) and post.get("id") == post_id:
            post.update(fields)
            break
    save_poster(doc)


def update_post(post_id: str, owner_user_id: int,
                content: Optional[Dict[str, Any]] = None,
                run_at_iso: Optional[str] = None) -> bool:
    doc = load_poster()
    changed = False
    for post in doc.get("posts", []):
        if (isinstance(post, dict) and post.get("id") == post_id
                and post.get("owner_user_id") == owner_user_id
                and post.get("status") == "scheduled"):
            if content is not None:
                post["content"] = content
            if run_at_iso is not None:
                post["run_at"] = run_at_iso
            changed = True
            break
    if changed:
        save_poster(doc)
    return changed


def due_posts(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or _now()
    out = []
    for post in load_poster().get("posts", []):
        if not isinstance(post, dict) or post.get("status") != "scheduled":
            continue
        run_at = _parse_dt(post.get("run_at"))
        if run_at and run_at <= now:
            out.append(post)
    return out


VALID_BUTTON_STYLES = {"danger", "success", "primary"}


def _build_keyboard(buttons: List[List[Dict[str, Any]]]):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for row in buttons or []:
        built = []
        for btn in row:
            text = str(btn.get("text") or "").strip()
            url = str(btn.get("url") or "").strip()
            style = btn.get("style")
            if style not in VALID_BUTTON_STYLES:
                style = None
            if text and url:
                built.append(InlineKeyboardButton(text=text, url=url, style=style))
        if built:
            rows.append(built)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def _safe_send(factory, *, retries: int = 1):
    from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

    attempt = 0
    while True:
        try:
            return await factory()
        except TelegramRetryAfter as exc:
            if attempt >= retries:
                raise
            attempt += 1
            await asyncio.sleep(float(exc.retry_after or 1))
        except TelegramBadRequest:
            raise


CAPTION_LIMIT = limits.CAPTION
RICH_TEXT_LIMIT = limits.RICH_TEXT

RICH_MEDIA_BLOCKS = {
    "photo": ("photo", '<img src="tg://photo?id={id}"/>'),
    "video": ("video", '<video src="tg://video?id={id}"></video>'),
    "animation": ("video", '<video src="tg://video?id={id}"></video>'),
    "audio": ("audio", '<audio src="tg://audio?id={id}"></audio>'),
}

MEDIA_SEND_METHODS = {
    "photo": "send_photo",
    "video": "send_video",
    "animation": "send_animation",
    "audio": "send_audio",
    "document": "send_document",
}


def rich_unsupported_media(content: Dict[str, Any]) -> list:
    return [
        str(item.get("type") or "?")
        for item in (content.get("media") or [])
        if item.get("type") not in RICH_MEDIA_BLOCKS
    ]


def build_rich_message(content: Dict[str, Any]):
    from aiogram.types import (
        InputMediaAnimation, InputMediaAudio, InputMediaPhoto, InputMediaVideo,
        InputRichMessage, InputRichMessageMedia,
    )

    media_classes = {
        "photo": InputMediaPhoto,
        "video": InputMediaVideo,
        "animation": InputMediaAnimation,
        "audio": InputMediaAudio,
    }

    text = normalize_custom_emoji(content.get("html_text") or "")
    blocks: list[str] = []
    attachments = []

    for index, item in enumerate(content.get("media") or []):
        kind = item.get("type")
        block = RICH_MEDIA_BLOCKS.get(kind)
        if not block:
            continue
        media_id = f"m{index}"
        blocks.append(block[1].format(id=media_id))
        attachments.append(InputRichMessageMedia(
            id=media_id,
            media=media_classes[kind](media=item["file_id"]),
        ))

    html = "\n".join(blocks + ([text] if text else [])) or "—"
    return InputRichMessage(html=html, media=attachments or None)


def _build_media_group(media: List[Dict[str, Any]], caption: str | None):
    from aiogram.enums import ParseMode
    from aiogram.types import InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo

    kinds = {str(item.get("type") or "") for item in media}
    if kinds <= {"photo", "video"}:
        classes = {"photo": InputMediaPhoto, "video": InputMediaVideo}
    elif kinds == {"audio"}:
        classes = {"audio": InputMediaAudio}
    elif kinds == {"document"}:
        classes = {"document": InputMediaDocument}
    else:
        return None

    group = []
    for index, item in enumerate(media):
        kwargs: Dict[str, Any] = {"media": item["file_id"]}
        if index == 0 and caption:
            kwargs["caption"] = caption
            kwargs["parse_mode"] = ParseMode.HTML
        group.append(classes[str(item.get("type"))](**kwargs))
    return group


async def send_content(bot, chat_id: int, content: Dict[str, Any], *, text_editor=None):
    from aiogram.enums import ParseMode

    content = dict(content)
    content["html_text"] = ensure_updated_plugins_quote(str(content.get("html_text") or ""))
    if content.get("rich") and rich_unsupported_media(content):
        content = dict(content)
        content["rich"] = False
        content["html_text"] = telegram_html(content.get("html_text") or "")
    text = normalize_custom_emoji(content.get("html_text") or "")
    media = [
        item
        for item in (content.get("media") or [])
        if isinstance(item, dict) and item.get("file_id")
    ][: limits.ALBUM_ITEMS]
    kb = _build_keyboard(content.get("buttons") or [])
    visible_len = visible_length(text)

    async def _send():
        if content.get("rich"):
            return await _safe_send(lambda: bot.send_rich_message(
                chat_id=chat_id, rich_message=build_rich_message(content), reply_markup=kb))

        sent: List[Any] = []

        def result(primary):
            return SentContent(primary=primary, messages=sent) if len(sent) > 1 else primary

        async def attach_keyboard(message, *, album=False):
            if not kb:
                return
            if not album:
                try:
                    await _safe_send(lambda: bot.edit_message_reply_markup(
                        chat_id=chat_id, message_id=message.message_id, reply_markup=kb))
                    return
                except Exception:
                    logger.warning("poster: cannot attach markup chat=%s msg=%s",
                                   chat_id, message.message_id, exc_info=True)
            sent.append(await _safe_send(lambda: bot.send_message(
                chat_id, BLANK_CHAR, reply_markup=kb, disable_web_page_preview=True)))

        try:
            if len(media) > 1:
                caption = text if text and visible_len <= CAPTION_LIMIT else None
                group = _build_media_group(media, caption)
                if group is not None:
                    sent.extend(await _safe_send(lambda: bot.send_media_group(chat_id, group)))
                else:
                    for item in media:
                        send_media = getattr(
                            bot,
                            MEDIA_SEND_METHODS.get(item.get("type"), "send_document"),
                        )
                        sent.append(await _safe_send(lambda: send_media(chat_id, item["file_id"])))

                text_was_caption = bool(group is not None and caption)
                if text and not text_was_caption:
                    text_was_caption = await try_edit(text_editor, chat_id, sent[0].message_id, text)
                if text and not text_was_caption:
                    sent.extend(await send_description(bot, chat_id, text, reply_markup=kb))
                    return result(sent[-1])
                await attach_keyboard(sent[0], album=group is not None)
                return result(sent[0])

            if media:
                item = media[0]
                send_media = getattr(bot, MEDIA_SEND_METHODS.get(item.get("type"), "send_photo"))
                if visible_len > CAPTION_LIMIT:
                    media_message = await _safe_send(lambda: send_media(chat_id, item["file_id"]))
                    sent.append(media_message)
                    if await try_edit(text_editor, chat_id, media_message.message_id, text):
                        await attach_keyboard(media_message)
                        return result(media_message)
                    sent.extend(await send_description(bot, chat_id, text, reply_markup=kb))
                    return result(sent[-1])
                return await _safe_send(lambda: send_media(
                    chat_id, item["file_id"], caption=(text or None),
                    parse_mode=ParseMode.HTML, reply_markup=kb))

            if visible_len > limits.MESSAGE_TEXT and text_editor:
                placeholder = await _safe_send(lambda: bot.send_message(
                    chat_id, BLANK_CHAR, disable_web_page_preview=True))
                sent.append(placeholder)
                if await try_edit(text_editor, chat_id, placeholder.message_id, text):
                    await attach_keyboard(placeholder)
                    return result(placeholder)
                await bot.delete_message(chat_id, placeholder.message_id)
                sent.clear()
            sent.extend(await send_description(bot, chat_id, text or "—", reply_markup=kb))
            return result(sent[-1])
        except BaseException:
            await _rollback_sent_messages(bot, chat_id, sent)
            raise

    return await _send()


async def _try_userbot_edit(chat_id: int, message_id: int, text: str) -> bool:
    from userbot.client import get_userbot

    ub = await get_userbot()
    if not ub:
        return False
    channel = get_channel(chat_id)
    ref = (channel or {}).get("username") or chat_id
    return await ub.edit_channel_text(ref, message_id, text)


async def _apply_custom_emoji(bot, chat_id: int, message_id: int, text: str, kb) -> None:
    try:
        if await _try_userbot_edit(chat_id, message_id, text):
            logger.info("poster: custom emoji applied via userbot edit chat=%s msg=%s",
                        chat_id, message_id)
            return
    except Exception:
        logger.warning("poster: userbot edit for custom emoji failed chat=%s msg=%s",
                       chat_id, message_id, exc_info=True)

    if not kb:
        return

    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception:
        logger.warning("poster: cannot detach markup for emoji retry chat=%s msg=%s",
                       chat_id, message_id, exc_info=True)
        return

    try:
        if await _try_userbot_edit(chat_id, message_id, text):
            logger.info("poster: custom emoji applied after markup detach chat=%s msg=%s",
                        chat_id, message_id)
    except Exception:
        logger.warning("poster: userbot edit retry failed chat=%s msg=%s",
                       chat_id, message_id, exc_info=True)
    finally:
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=kb)
        except Exception:
            logger.error("poster: FAILED to restore markup chat=%s msg=%s",
                         chat_id, message_id, exc_info=True)


def visible_length(text: str) -> int:
    return visible_html_length(text)


async def _send_content_for_delivery(bot, chat_id: int, content: Dict[str, Any]):
    content = dict(content)
    content["html_text"] = ensure_updated_plugins_quote(str(content.get("html_text") or ""))
    if content.get("rich") and rich_unsupported_media(content):
        content["rich"] = False
        content["html_text"] = telegram_html(content.get("html_text") or "")
    text = normalize_custom_emoji(content.get("html_text") or "")
    text_editor = None
    if not content.get("rich"):
        media = [item for item in (content.get("media") or []) if isinstance(item, dict) and item.get("file_id")]
        limit = CAPTION_LIMIT if media else limits.MESSAGE_TEXT
        if visible_length(text) > limit:
            text_editor = await editing_userbot(text, caption=bool(media))

    message = await send_content(bot, chat_id, content, text_editor=text_editor)
    message_id = getattr(message, "message_id", None)
    if content.get("rich"):
        return message
    if "tg-emoji" in text and message_id and not isinstance(message, SentContent) and text_editor is None:
        await _apply_custom_emoji(
            bot, chat_id, message_id, text, _build_keyboard(content.get("buttons") or [])
        )
    return message


def _content_has_updated_block(content: Dict[str, Any]) -> bool:
    text = str((content or {}).get("html_text") or "")
    title = updated_block_title()
    return bool(title and title in text)


async def deliver_post(bot, post: Dict[str, Any]) -> bool:
    post_id = post.get("id")
    content = post.get("content") or {}
    chat_id = post.get("chat_id")

    if not _claim_post(post_id):
        logger.warning("poster: skip duplicate delivery post=%s", post_id)
        return False

    message = None
    try:
        message = await _send_content_for_delivery(bot, chat_id, content)
        message_ids = sent_message_ids(message)
        extra: Dict[str, Any] = {}
        delete_at_abs = _parse_dt(content.get("delete_at_iso"))
        if delete_at_abs:
            extra["delete_at"] = delete_at_abs.isoformat()
        else:
            try:
                delete_after = int(content.get("delete_after_minutes") or 0)
            except (TypeError, ValueError):
                delete_after = 0
            if delete_after > 0:
                extra["delete_at"] = (_now() + timedelta(minutes=delete_after)).isoformat()
        _update_post(
            post_id,
            status="sent",
            sent_message_id=getattr(message, "message_id", None),
            sent_message_ids=message_ids,
            error=None,
            **extra,
        )
        _schedule_repeat(post, content)
        if _content_has_updated_block(content):
            try:
                from bot.services.publish import clear_updated_plugins
                clear_updated_plugins()
            except Exception:
                logger.exception("poster: clear_updated_plugins failed post=%s", post_id)
        return True
    except Exception as exc:
        logger.exception("poster: delivery failed post=%s chat=%s", post_id, chat_id)
        if message is not None:
            await _rollback_sent_messages(
                bot,
                int(chat_id),
                message.messages if isinstance(message, SentContent) else [message],
            )
        _update_post(post_id, status="failed", error=str(exc)[:300])
        return False
    finally:
        _in_flight.discard(post_id)


def _schedule_repeat(post: Dict[str, Any], content: Dict[str, Any]) -> None:
    try:
        repeat_days = int(content.get("repeat_days") or 0)
    except (TypeError, ValueError):
        repeat_days = 0
    if repeat_days <= 0:
        return
    base = _parse_dt(post.get("run_at")) or _now()
    next_run = base + timedelta(days=repeat_days)
    now = _now()
    while next_run <= now:
        next_run += timedelta(days=repeat_days)
    try:
        add_post(post.get("owner_user_id"), post.get("chat_id"), next_run.isoformat(),
                 dict(content), kind="repeat")
    except Exception:
        logger.exception("poster: failed to schedule repeat for post=%s", post.get("id"))


def due_deletions(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or _now()
    out = []
    for post in load_poster().get("posts", []):
        if not isinstance(post, dict) or post.get("status") != "sent":
            continue
        if not post.get("delete_at"):
            continue
        da = _parse_dt(post.get("delete_at"))
        if da and da <= now:
            out.append(post)
    return out


async def delete_sent_post(bot, post: Dict[str, Any]) -> None:
    chat_id = post.get("chat_id")
    raw_ids = post.get("sent_message_ids")
    message_ids = raw_ids if isinstance(raw_ids, list) else []
    if not message_ids and post.get("sent_message_id"):
        message_ids = [post.get("sent_message_id")]
    failed_ids: List[int] = []
    for message_id in message_ids:
        try:
            from bot.helpers import blank_and_delete

            deleted = await blank_and_delete(bot, chat_id, message_id)
            if not deleted:
                failed_ids.append(int(message_id))
        except Exception:
            failed_ids.append(int(message_id))
            logger.exception(
                "poster: auto-delete failed post=%s chat=%s msg=%s",
                post.get("id"),
                chat_id,
                message_id,
            )
    if failed_ids:
        _update_post(
            post.get("id"),
            status="sent",
            sent_message_id=failed_ids[0],
            sent_message_ids=failed_ids,
            error=f"auto-delete pending for {len(failed_ids)} message(s)",
        )
        return
    _update_post(
        post.get("id"),
        status="deleted",
        delete_at=None,
        sent_message_id=None,
        sent_message_ids=[],
    )


_worker_task: Optional[asyncio.Task] = None
_WORKER_INTERVAL_SECONDS = 30


async def _worker_loop(bot) -> None:
    while True:
        await asyncio.sleep(_WORKER_INTERVAL_SECONDS)
        try:
            for post in due_posts():
                await deliver_post(bot, post)
            for post in due_deletions():
                await delete_sent_post(bot, post)
        except Exception:
            logger.exception("poster: worker loop error")


def recover_stuck_posts() -> int:
    doc = load_poster()
    fixed = 0
    for post in doc.get("posts", []):
        if isinstance(post, dict) and post.get("status") == "sending":
            post["status"] = "failed"
            post["error"] = "Delivery interrupted by restart. Check the channel before retrying; the post may already be published."
            fixed += 1
    if fixed:
        save_poster(doc)
        logger.warning("poster: recovered %s stuck post(s) after restart", fixed)
    return fixed


def start_poster_worker(bot) -> None:
    global _worker_task
    try:
        recover_stuck_posts()
    except Exception:
        logger.exception("poster: recover_stuck_posts failed")
    if _worker_task and not _worker_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _worker_task = loop.create_task(_worker_loop(bot))


async def stop_poster_worker() -> None:
    global _worker_task
    task = _worker_task
    _worker_task = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
