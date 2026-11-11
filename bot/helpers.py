import asyncio
import hashlib
import html
import logging
import re
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from bot import limits
from bot.cache import get_config
from bot.context import get_lang
from bot.languages import normalize_language
from bot.formatting import split_html, telegram_html, visible_html_length
from bot.texts import t
from bot.telemetry import callback_trace
from core.storage import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = BASE_DIR / "img"

logger = logging.getLogger(__name__)

_IMAGE_BASE_URL = "https://raw.githubusercontent.com/itsv1eds/exteraPluginsRobot/main/img"


def image_path(image: Optional[str]) -> Optional[Path]:
    if image:
        for extension in (".jpg", ".png"):
            path = IMAGES_DIR / f"{image}{extension}"
            if path.is_file():
                return path
    return None


def localized_image_key(image: Optional[str], lang: Optional[str]) -> Optional[str]:
    if image and normalize_language(lang) in {"ru", "ua"} and not image.endswith("_ru"):
        russian = f"{image}_ru"
        if image_path(russian):
            return russian
    return image


def get_uploads_dir() -> Path:
    cfg = get_config()
    storage_cfg = cfg.get("storage", {})
    uploads_dir = storage_cfg.get("uploads_dir")
    attachments_dir = storage_cfg.get("attachments_dir")
    base_dir_cfg = storage_cfg.get("base_dir")
    if base_dir_cfg:
        base_dir = Path(base_dir_cfg)
    else:
        base_dir = DATA_DIR
        if DATA_DIR.name == "data" and DATA_DIR.parent.name == "data":
            base_dir = DATA_DIR.parent

    raw = uploads_dir or attachments_dir
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = base_dir / path
    else:
        path = base_dir / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_uploads_subdir(name: str) -> Path:
    path = get_uploads_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename(value: str) -> str:
    return re.sub(r"[^\w\-@.]", "", value).strip("._") or "plugin"


def fit_filename(name: str, ext: str) -> str:
    base = sanitize_filename(name)
    room = limits.FILENAME - len(ext) - 1
    if room > 0 and len(base) > room:
        base = base[:room].rstrip("._-") or "plugin"
    return f"{base}.{ext}"


def _short_error(exc: Exception, limit: int = 160) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _is_too_long_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "message is too long" in text
        or "message text is too long" in text
        or "caption is too long" in text
        or "message caption is too long" in text
        or "caption_too_long" in text
        or "media_caption_too_long" in text
        or "message_too_long" in text
    )


def _is_not_modified_error(exc: Exception) -> bool:
    return "message is not modified" in str(exc).lower()


def _is_superseded_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "canceled by new" in text or "message can't be edited" in text


def _is_message_missing_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "message to edit not found" in text or "message identifier is not specified" in text


def _is_entities_error(exc: Exception) -> bool:
    if not isinstance(exc, TelegramBadRequest):
        return False
    text = str(exc).lower()
    return "can't parse entities" in text or "unsupported start tag" in text


def _is_transient_network_error(exc: Exception) -> bool:
    return isinstance(exc, (TelegramNetworkError, TelegramServerError, TelegramRetryAfter))


@lru_cache(maxsize=128)
def _link_preview_url(image: Optional[str]) -> Optional[str]:
    path = image_path(image)
    if not path:
        return None
    version = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return f"{_IMAGE_BASE_URL}/{path.name}?v={version}"


def link_preview_options(image: Optional[str] = None, url: Optional[str] = None,
                         *, lang: Optional[str] = None) -> Optional[LinkPreviewOptions]:
    if lang is not None:
        image = localized_image_key(image, lang)
    preview_url = url or _link_preview_url(image)
    if not preview_url:
        return None
    return LinkPreviewOptions(
        url=preview_url,
        is_disabled=False,
        prefer_large_media=True,
        show_above_text=True,
    )


def with_hidden_preview_link(text: str, url: str) -> str:
    prefix = f'<a href="{html.escape(url, quote=True)}">&#8203;</a>'
    return text if text.startswith(prefix) else prefix + text


def _topic_kwargs(target: Message | None) -> dict[str, int]:
    if not target:
        return {}
    try:
        from bot.services.forum import moderation_topic_kwargs

        return moderation_topic_kwargs(target)
    except Exception:
        return {}


async def try_react_pray(message: Message) -> None:
    try:
        bot = message.bot
        if not hasattr(bot, "set_message_reaction"):
            return

        try:
            from aiogram.types import ReactionTypeEmoji

            reaction = [ReactionTypeEmoji(emoji="🙏")]
        except Exception:
            reaction = ["🙏"]

        await bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=reaction,
        )
    except Exception:
        return


BLANK_CHAR = "."
BLANK_CANDIDATES = (BLANK_CHAR,)
_EMPTY_TEXT_MARKERS = (
    "text must be non-empty",
    "message text is empty",
    "caption must be non-empty",
    "message_empty",
    "message is empty",
    "text_empty",
    "text is empty",
)


def _is_empty_text_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _EMPTY_TEXT_MARKERS)

_background_tasks: set = set()


async def ack(cb: CallbackQuery, text: str | None = None, *, show_alert: bool = False) -> None:
    try:
        await cb.answer(text, show_alert=show_alert)
    except Exception:
        pass


def spawn_background(coro) -> None:
    trace_token = callback_trace.set(None)
    try:
        task = asyncio.create_task(coro)
    finally:
        callback_trace.reset(trace_token)
    _background_tasks.add(task)

    def _done(completed: asyncio.Task) -> None:
        _background_tasks.discard(completed)
        if completed.cancelled():
            return
        exc = completed.exception()
        if exc is not None:
            logger.error(
                "Background bot task failed",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    task.add_done_callback(_done)



BLANK_SETTLE_DELAY = 1.5


def message_is_too_old(sent_at) -> bool:
    try:
        date = sent_at if isinstance(sent_at, datetime) else datetime.fromisoformat(str(sent_at))
    except (ValueError, TypeError):
        return False
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - date >= timedelta(hours=48)


async def send_in_parts(bot, chat_id: int, text: str, kb=None, *,
                        disable_web_page_preview: bool = True,
                        link_preview_options=None, **kwargs):
    preview_url = getattr(link_preview_options, "url", None)
    if preview_url:
        text = text.removeprefix(with_hidden_preview_link("", preview_url))
    parts = split_html(text, limits.MESSAGE_TEXT - bool(preview_url))
    if not parts:
        return None
    sent = None
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if last and preview_url:
            part = with_hidden_preview_link(part, preview_url)
        sent = await bot.send_message(
            chat_id,
            text=part,
            parse_mode=ParseMode.HTML,
            reply_markup=kb if last else None,
            disable_web_page_preview=disable_web_page_preview if last else True,
            link_preview_options=link_preview_options if last else None,
            **kwargs,
        )
    logger.info("event=message.sent_in_parts chat_id=%s parts=%s", chat_id, len(parts))
    return sent


async def blank_and_delete(bot, chat_id: int, message_id: int, *, is_media: bool = False) -> bool:
    if not chat_id or not message_id:
        return False

    blanked = False
    blanked_with = ""
    text_error = None

    for candidate in () if is_media else BLANK_CANDIDATES:
        try:
            await bot.edit_message_text(
                candidate, chat_id=int(chat_id), message_id=int(message_id), reply_markup=None,
            )
            blanked = True
            blanked_with = candidate
            break
        except Exception as exc:
            if isinstance(exc, TelegramRetryAfter):
                async def retry_blank(wait=exc.retry_after):
                    await asyncio.sleep(max(1, float(wait)))
                    await blank_and_delete(bot, chat_id, message_id, is_media=is_media)

                spawn_background(retry_blank())
                return False
            if isinstance(exc, TelegramBadRequest) and "message to edit not found" in str(exc).lower():
                return True
            if _is_not_modified_error(exc):
                blanked = True
                blanked_with = candidate
                break
            text_error = _short_error(exc)
            if not _is_empty_text_error(exc):
                break

    async def _delete(delay: float) -> bool:
        if delay:
            await asyncio.sleep(delay)
        try:
            await bot.delete_message(int(chat_id), int(message_id))
            return True
        except TelegramRetryAfter as exc:
            spawn_background(_delete(max(1, float(exc.retry_after))))
            return False
        except TelegramBadRequest as exc:
            error = str(exc).lower()
            if "message to delete not found" in error:
                return True
            if "message can't be deleted" in error:
                logger.info("event=blank_before_delete.not_deletable chat_id=%s message_id=%s", chat_id, message_id)
                return False
            logger.warning(
                "event=blank_before_delete.delete_failed chat_id=%s message_id=%s error=%s",
                chat_id, message_id, _short_error(exc),
            )
            return False
        except Exception as exc:
            logger.warning(
                "event=blank_before_delete.delete_failed chat_id=%s message_id=%s error=%s",
                chat_id, message_id, _short_error(exc),
            )
            return False

    if not blanked:
        if text_error:
            logger.info("event=blank_before_delete.skipped chat_id=%s message_id=%s error=%s",
                        chat_id, message_id, text_error)
        return await _delete(0)

    if blanked_with != BLANK_CANDIDATES[0]:
        logger.info(
            "event=blank_before_delete.fallback chat_id=%s message_id=%s char=U+%04X",
            chat_id, message_id, ord(blanked_with),
        )
    spawn_background(_delete(BLANK_SETTLE_DELAY))
    return True


async def blank_and_delete_message(msg) -> bool:
    if msg is None:
        return False
    try:
        is_media = getattr(msg, "content_type", None) in {"photo", "video", "animation", "document", "audio", "voice"}
        return await blank_and_delete(msg.bot, msg.chat.id, msg.message_id, is_media=is_media)
    except Exception:
        return False


_SCREEN_LIMIT = 5000
_screens: Dict[int, tuple[int, datetime]] = {}


def track_screen(target: Message | CallbackQuery, sent, kb) -> None:
    if kb is None or not isinstance(sent, Message) or sent.chat.type != "private":
        return
    chat_id = sent.chat.id
    previous = _screens.pop(chat_id, None)
    _screens[chat_id] = (sent.message_id, datetime.now(timezone.utc))
    while len(_screens) > _SCREEN_LIMIT:
        _screens.pop(next(iter(_screens)))
    if not previous or previous[0] == sent.message_id:
        return
    pressed = target.message if isinstance(target, CallbackQuery) else None
    if isinstance(target, Message) or (pressed and pressed.message_id == previous[0]):
        spawn_background(_retire_screen(sent.bot, chat_id, *previous))


async def _retire_screen(bot, chat_id: int, message_id: int, sent_at: datetime) -> None:
    if not message_is_too_old(sent_at):
        await blank_and_delete(bot, chat_id, message_id)
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception as exc:
        logger.info("event=screen.retire_failed chat_id=%s message_id=%s error=%s", chat_id, message_id, _short_error(exc))


async def answer(
    target: Message | CallbackQuery,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
    image: Optional[str] = None,
    *,
    lang: Optional[str] = None,
) -> Optional[Message]:
    sent = await _render_screen(target, text, kb, image, lang=lang)
    track_screen(target, sent, kb)
    return sent


async def _render_screen(
    target: Message | CallbackQuery,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
    image: Optional[str] = None,
    *,
    lang: Optional[str] = None,
) -> Optional[Message]:
    callback = target if isinstance(target, CallbackQuery) else None
    message = callback.message if callback else target
    if not message:
        return None
    if image:
        user = target.from_user or message.from_user
        image = localized_image_key(image, lang or get_lang(getattr(user, "id", None)))
    preview_options = link_preview_options(image)
    if preview_options:
        text = with_hidden_preview_link(text, preview_options.url)
    bot = message.bot
    chat_id = message.chat.id
    options = {
        "parse_mode": ParseMode.HTML,
        "disable_web_page_preview": not preview_options,
        "link_preview_options": preview_options,
    }
    thread_kwargs = _topic_kwargs(message)

    async def send(body: str) -> Optional[Message]:
        if visible_html_length(body) > limits.MESSAGE_TEXT:
            return await send_in_parts(
                bot, chat_id, body, kb, disable_web_page_preview=not preview_options,
                link_preview_options=preview_options, **thread_kwargs,
            )
        return await bot.send_message(chat_id, text=body, reply_markup=kb, **options, **thread_kwargs)

    async def send_repaired(body: str) -> Optional[Message]:
        try:
            return await send(body)
        except TelegramBadRequest as exc:
            if _is_too_long_error(exc):
                return await send_in_parts(
                    bot, chat_id, body, kb, disable_web_page_preview=not preview_options,
                    link_preview_options=preview_options, **thread_kwargs,
                )
            if not _is_entities_error(exc):
                raise
            logger.warning("event=answer.entities_repair chat_id=%s text_len=%s error=%s", chat_id, len(body), _short_error(exc))
            try:
                return await send(telegram_html(body))
            except Exception as repair_exc:
                logger.exception("event=answer.entities_repair_failed chat_id=%s error=%s", chat_id, _short_error(repair_exc, 300))
                return None

    if not callback:
        return await send_repaired(text)

    async def replace() -> Optional[Message]:
        sent = await send_repaired(text)
        if sent and message.chat.type == "private":
            spawn_background(blank_and_delete_message(message))
        return sent

    try:
        if message.content_type != "text" or visible_html_length(text) > limits.MESSAGE_TEXT:
            return await replace()
        return await message.edit_text(text=text, reply_markup=kb, **options)
    except Exception as exc:
        message_id = getattr(message, "message_id", None)
        if isinstance(exc, TelegramBadRequest) and (_is_not_modified_error(exc) or _is_superseded_error(exc)):
            await ack(callback)
            return message
        if _is_transient_network_error(exc):
            logger.warning(
                "event=answer.callback_edit_transient chat_id=%s message_id=%s type=%s error=%s",
                chat_id, message_id, type(exc).__name__, _short_error(exc),
            )
            return message
        try:
            if isinstance(exc, TelegramBadRequest) and _is_message_missing_error(exc):
                logger.info("event=answer.callback_message_replaced chat_id=%s message_id=%s", chat_id, message_id)
                return await send_repaired(text)
            if isinstance(exc, TelegramBadRequest) and _is_too_long_error(exc):
                return await replace()
            if _is_entities_error(exc):
                logger.warning("event=answer.callback_entities_repair chat_id=%s text_len=%s error=%s", chat_id, len(text), _short_error(exc))
                return await message.edit_text(text=telegram_html(text), reply_markup=kb, **options)
        except Exception as fallback_exc:
            logger.exception("event=answer.callback_fallback_failed chat_id=%s error=%s", chat_id, _short_error(fallback_exc, 300))
            return None
        logger.exception(
            "event=answer.callback_edit_failed chat_id=%s message_id=%s image=%s text_len=%s error=%s",
            chat_id, message_id, image or "-", len(text or ""), _short_error(exc, 500),
        )
        await ack(callback, t("err_update_message_failed", get_lang(getattr(callback.from_user, "id", None)), error=_short_error(exc)), show_alert=True)
        return None


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def extract_html_text(message: Message) -> str:
    return telegram_html(message.html_text or message.text or "")
