import asyncio
import hashlib
import html
import logging
import re
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from typing import Dict, Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
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

_image_file_ids: Dict[str, str] = {}
logger = logging.getLogger(__name__)

_LINK_PREVIEW_IMAGE_URLS = {
    key: f"https://raw.githubusercontent.com/itsv1eds/exteraPluginsRobot/main/img/{key}.jpg"
    for key in ("admin", "new", "update", "appeal", "delete")
}


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
        if image_path(russian) or _link_preview_url(russian):
            return russian
    return image


def _remember_image(image: str, message):
    if isinstance(message, Message) and message.photo:
        _image_file_ids[image] = message.photo[-1].file_id
    return message


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


@lru_cache(maxsize=64)
def _link_preview_url(image: Optional[str]) -> Optional[str]:
    if not image:
        return None
    key = image.removesuffix("_ru")
    url = _LINK_PREVIEW_IMAGE_URLS.get(key)
    if not url:
        return None
    path = image_path(image)
    filename = path.name if path else f"{image}.jpg"
    preview_url = f"{url.rsplit('/', 1)[0]}/{filename}"
    if path:
        version = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        preview_url += f"?v={version}"
    return preview_url


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
        date = sent_at if isinstance(sent_at, datetime) else datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
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
    text_error = caption_error = None

    for method in (("caption", "text") if is_media else ("text", "caption")):
        for candidate in BLANK_CANDIDATES:
            try:
                if method == "caption":
                    await bot.edit_message_caption(
                        chat_id=int(chat_id), message_id=int(message_id), caption=candidate, reply_markup=None,
                    )
                else:
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
                if method == "caption":
                    caption_error = _short_error(exc)
                else:
                    text_error = _short_error(exc)
                if not _is_empty_text_error(exc):
                    break
        if blanked:
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
        logger.warning(
            "event=blank_before_delete.failed chat_id=%s message_id=%s text_error=%s caption_error=%s",
            chat_id, message_id, text_error, caption_error,
        )
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


async def download_document(bot: Bot, file_id: str, dest_dir: Path) -> Path:
    file = await bot.get_file(file_id)
    name = Path(file.file_path).name if file.file_path else f"{file_id}.plugin"
    dest = dest_dir / f"{uuid4().hex}-{name}"
    try:
        await bot.download_file(file.file_path, dest)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return dest


async def answer(
    target: Message | CallbackQuery,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
    image: Optional[str] = None,
    *,
    lang: Optional[str] = None,
) -> Optional[Message]:
    if image:
        try:
            user = (target.from_user if isinstance(target, Message) else (target.from_user or (target.message.from_user if target.message else None)))
            lang = lang or get_lang(getattr(user, "id", None))
            image = localized_image_key(image, lang)
        except Exception:
            pass
    preview_options = link_preview_options(image)
    if preview_options:
        text = with_hidden_preview_link(text, preview_options.url)
        image = None
    text_length = visible_html_length(text)
    disable_web_page_preview = not bool(preview_options)

    if image and text_length > limits.CAPTION:
        image = None

    if isinstance(target, CallbackQuery):
        msg = target.message
        if not msg:
            return None
        
        bot = msg.bot
        chat_id = msg.chat.id
        thread_kwargs = _topic_kwargs(msg)

        async def replace_with_text():
            if text_length > limits.MESSAGE_TEXT:
                sent = await send_in_parts(
                    bot, chat_id, text, kb, disable_web_page_preview=disable_web_page_preview,
                    link_preview_options=preview_options, **thread_kwargs,
                )
            else:
                sent = await bot.send_message(
                    chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=kb,
                    disable_web_page_preview=disable_web_page_preview,
                    link_preview_options=preview_options, **thread_kwargs,
                )
            spawn_background(blank_and_delete_message(msg))
            return sent

        try:
            if msg.photo and text_length > limits.CAPTION:
                return await replace_with_text()

            if preview_options and msg.photo:
                return await replace_with_text()

            if image and not msg.photo:
                file_id = _image_file_ids.get(image)
                path = image_path(image)
                if file_id or path is not None:
                    sent = await msg.edit_media(
                        InputMediaPhoto(media=file_id or FSInputFile(path), caption=text, parse_mode=ParseMode.HTML),
                        reply_markup=kb,
                    )
                    return _remember_image(image, sent)

            if image and msg.photo:
                file_id = _image_file_ids.get(image)

                if file_id and msg.photo[-1].file_id == file_id:
                    return await msg.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)

                if file_id:
                    edited = await msg.edit_media(
                        InputMediaPhoto(
                            media=file_id,
                            caption=text,
                            parse_mode=ParseMode.HTML,
                        ),
                        reply_markup=kb,
                    )
                    return _remember_image(image, edited)

                path = image_path(image)
                if path is not None:
                    edited = await msg.edit_media(
                        InputMediaPhoto(
                            media=FSInputFile(path),
                            caption=text,
                            parse_mode=ParseMode.HTML,
                        ),
                        reply_markup=kb,
                    )
                    return _remember_image(image, edited)

                return await msg.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)

            elif msg.photo:
                return await msg.edit_caption(
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
            else:
                return await msg.edit_text(
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=disable_web_page_preview,
                    link_preview_options=preview_options,
                )

        except Exception as exc:
            if isinstance(exc, TelegramBadRequest) and (
                _is_not_modified_error(exc) or _is_superseded_error(exc)
            ):
                try:
                    await target.answer()
                except Exception:
                    pass
                return msg

            if _is_transient_network_error(exc):
                logger.warning(
                    "event=answer.callback_edit_transient chat_id=%s message_id=%s type=%s error=%s",
                    chat_id,
                    getattr(msg, "message_id", None),
                    type(exc).__name__,
                    _short_error(exc),
                )
                return msg

            if isinstance(exc, TelegramBadRequest) and _is_message_missing_error(exc):
                logger.info(
                    "event=answer.callback_message_replaced chat_id=%s message_id=%s",
                    chat_id,
                    getattr(msg, "message_id", None),
                )
                try:
                    file_id = _image_file_ids.get(image or "")
                    path = image_path(image)
                    if image and file_id:
                        return await bot.send_photo(
                            chat_id, file_id, caption=text, parse_mode=ParseMode.HTML,
                            reply_markup=kb, **thread_kwargs,
                        )
                    if path and path.exists():
                        sent = await bot.send_photo(
                            chat_id, FSInputFile(path), caption=text, parse_mode=ParseMode.HTML,
                            reply_markup=kb, **thread_kwargs,
                        )
                        if sent.photo:
                            _image_file_ids[image] = sent.photo[-1].file_id
                        return sent
                    return await bot.send_message(
                        chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=kb,
                        disable_web_page_preview=disable_web_page_preview,
                        link_preview_options=preview_options, **thread_kwargs,
                    )
                except Exception as replacement_exc:
                    logger.warning(
                        "event=answer.callback_message_replace_failed chat_id=%s error=%s",
                        chat_id, _short_error(replacement_exc),
                    )
                    return None

            logger.exception(
                "event=answer.callback_edit_failed chat_id=%s message_id=%s image=%s has_photo=%s text_len=%s error=%s",
                chat_id,
                getattr(msg, "message_id", None),
                image or "-",
                bool(getattr(msg, "photo", None)),
                len(text or ""),
                _short_error(exc, 500),
            )
            if _is_entities_error(exc):
                repaired = telegram_html(text)
                logger.warning(
                    "event=answer.callback_entities_repair chat_id=%s text_len=%s error=%s",
                    chat_id, len(text or ""), _short_error(exc),
                )
                try:
                    if getattr(msg, "photo", None):
                        return await msg.edit_caption(
                            caption=repaired, parse_mode=ParseMode.HTML, reply_markup=kb,
                        )
                    return await msg.edit_text(
                        text=repaired,
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb,
                        disable_web_page_preview=disable_web_page_preview,
                        link_preview_options=preview_options,
                    )
                except Exception as repair_exc:
                    logger.exception(
                        "event=answer.callback_entities_repair_failed chat_id=%s error=%s",
                        chat_id, _short_error(repair_exc, 300),
                    )

            if isinstance(exc, TelegramBadRequest) and _is_too_long_error(exc):
                try:
                    sent = await send_in_parts(
                        bot, chat_id, text, kb,
                        disable_web_page_preview=disable_web_page_preview,
                        link_preview_options=preview_options,
                        **thread_kwargs,
                    )
                    return sent
                except Exception as send_exc:
                    logger.exception(
                        "event=answer.callback_too_long_fallback_failed chat_id=%s text_len=%s error=%s",
                        chat_id,
                        len(text or ""),
                        _short_error(send_exc, 500),
                    )
            try:
                await target.answer(t("err_update_message_failed", get_lang(getattr(target.from_user, "id", None)), error=_short_error(exc)), show_alert=True)
            except Exception:
                pass
        return None
        
    else:
        chat_id = target.chat.id
        bot = target.bot
        thread_kwargs = _topic_kwargs(target)

    async def _message_send(send_text: str) -> Optional[Message]:
        if visible_html_length(send_text) > limits.MESSAGE_TEXT:
            return await send_in_parts(
                bot, chat_id, send_text, kb,
                disable_web_page_preview=disable_web_page_preview,
                link_preview_options=preview_options, **thread_kwargs,
            )
        if image:
            file_id = _image_file_ids.get(image)
            if file_id:
                return await bot.send_photo(
                    chat_id,
                    photo=file_id,
                    caption=send_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    **thread_kwargs,
                )
            path = image_path(image)
            if path is not None:
                msg = await bot.send_photo(
                    chat_id,
                    photo=FSInputFile(path),
                    caption=send_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    **thread_kwargs,
                )
                if msg.photo:
                    _image_file_ids[image] = msg.photo[-1].file_id
                return msg
        return await bot.send_message(
            chat_id,
            text=send_text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=disable_web_page_preview,
            link_preview_options=preview_options,
            **thread_kwargs,
        )

    try:
        return await _message_send(text)
    except TelegramBadRequest as exc:
        if _is_too_long_error(exc):
            return await send_in_parts(
                bot, chat_id, text, kb,
                disable_web_page_preview=disable_web_page_preview,
                link_preview_options=preview_options,
                **thread_kwargs,
            )
        if not _is_entities_error(exc):
            raise
        repaired = telegram_html(text)
        logger.warning(
            "event=answer.entities_repair chat_id=%s text_len=%s error=%s",
            chat_id, len(text or ""), _short_error(exc),
        )
        try:
            return await _message_send(repaired)
        except Exception as repair_exc:
            logger.exception(
                "event=answer.entities_repair_failed chat_id=%s error=%s",
                chat_id, _short_error(repair_exc, 300),
            )
            return None


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def extract_html_text(message: Message) -> str:
    return telegram_html(message.html_text or message.text or "")
