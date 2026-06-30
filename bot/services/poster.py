
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from storage import load_poster, save_poster

logger = logging.getLogger(__name__)

POST_STATUSES = ("scheduled", "sent", "failed", "canceled")
_RETRY_CAP_SECONDS = 5.0

_TG_EMOJI_ANCHOR_RE = re.compile(
    r'<a\s+href="tg://emoji\?id=(\d+)"\s*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)


def normalize_custom_emoji(html: str) -> str:
    if not html:
        return ""
    return _TG_EMOJI_ANCHOR_RE.sub(r'<tg-emoji emoji-id="\1">\2</tg-emoji>', html)


def build_updated_plugins_text(limit: int = 30) -> str:
    import html as _html
    from storage import load_updated

    items = load_updated().get("items") or []
    lines = ["<b>Обновлённые плагины</b>", ""]
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
    return "\n".join(lines) if len(lines) > 2 else ""


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


def _update_post(post_id: str, **fields: Any) -> None:
    doc = load_poster()
    for post in doc.get("posts", []):
        if isinstance(post, dict) and post.get("id") == post_id:
            post.update(fields)
            break
    save_poster(doc)


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


def _build_keyboard(buttons: List[List[Dict[str, Any]]]):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for row in buttons or []:
        built = []
        for btn in row:
            text = str(btn.get("text") or "").strip()
            url = str(btn.get("url") or "").strip()
            style = btn.get("style") or None
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
            await asyncio.sleep(min(float(exc.retry_after or 1), _RETRY_CAP_SECONDS))
        except TelegramBadRequest:
            raise


async def deliver_post(bot, post: Dict[str, Any]) -> bool:
    from aiogram.enums import ParseMode

    post_id = post.get("id")
    content = post.get("content") or {}
    text = normalize_custom_emoji(content.get("html_text") or "")
    media = content.get("media") or []
    kb = _build_keyboard(content.get("buttons") or [])
    chat_id = post.get("chat_id")

    async def _send():
        if media:
            item = media[0]
            if item.get("type") == "video":
                return await bot.send_video(
                    chat_id, item["file_id"], caption=(text or None),
                    parse_mode=ParseMode.HTML, reply_markup=kb)
            return await bot.send_photo(
                chat_id, item["file_id"], caption=(text or None),
                parse_mode=ParseMode.HTML, reply_markup=kb)
        return await bot.send_message(
            chat_id, text or "—", parse_mode=ParseMode.HTML,
            reply_markup=kb, disable_web_page_preview=True)

    try:
        message = await _safe_send(_send)
        _update_post(post_id, status="sent", sent_message_id=getattr(message, "message_id", None), error=None)
        return True
    except Exception as exc:
        logger.exception("poster: delivery failed post=%s chat=%s", post_id, chat_id)
        _update_post(post_id, status="failed", error=str(exc)[:300])
        return False


_worker_task: Optional[asyncio.Task] = None
_WORKER_INTERVAL_SECONDS = 30


async def _worker_loop(bot) -> None:
    while True:
        await asyncio.sleep(_WORKER_INTERVAL_SECONDS)
        try:
            for post in due_posts():
                await deliver_post(bot, post)
        except Exception:
            logger.exception("poster: worker loop error")


def start_poster_worker(bot) -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _worker_task = loop.create_task(_worker_loop(bot))


def stop_poster_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
    _worker_task = None
