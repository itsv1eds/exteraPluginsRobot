import logging
import re
from pathlib import Path
from typing import Dict, Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
    Message,
)

from bot.cache import get_admins, get_categories, get_config
from bot.context import get_lang
from bot.formatting import telegram_html
from storage import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = BASE_DIR / "img"

_image_file_ids: Dict[str, str] = {}
_pending_upload: Dict[str, bool] = {}
logger = logging.getLogger(__name__)

_LINK_PREVIEW_IMAGE_URLS = {
    "admin": "https://github.com/itsv1eds/exteraPluginsRobot/blob/main/img/admin.png?raw=true",
    "new": "https://github.com/itsv1eds/exteraPluginsRobot/blob/main/img/new.png?raw=true",
}


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


def sanitize_filename(value: str) -> str:
    return re.sub(r"[^\w\-@.]", "", value).strip("._") or "plugin"


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


def _is_entities_error(exc: Exception) -> bool:
    if not isinstance(exc, TelegramBadRequest):
        return False
    text = str(exc).lower()
    return "can't parse entities" in text or "unsupported start tag" in text


def _link_preview_url(image: Optional[str]) -> Optional[str]:
    if not image:
        return None
    key = image.removesuffix("_ru")
    return _LINK_PREVIEW_IMAGE_URLS.get(key)


def link_preview_options(image: Optional[str] = None, url: Optional[str] = None) -> Optional[LinkPreviewOptions]:
    preview_url = url or _link_preview_url(image)
    if not preview_url:
        return None
    return LinkPreviewOptions(
        url=preview_url,
        is_disabled=False,
        prefer_large_media=True,
        show_above_text=True,
    )


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


async def download_document(bot: Bot, file_id: str, dest_dir: Path) -> Path:
    file = await bot.get_file(file_id)
    name = Path(file.file_path).name if file.file_path else f"{file_id}.plugin"
    dest = dest_dir / name
    await bot.download_file(file.file_path, dest)
    return dest


async def _get_photo_input(key: str, bot: Bot, chat_id: int) -> Optional[str]:
    if key in _image_file_ids:
        return _image_file_ids[key]
    
    path = IMAGES_DIR / f"{key}.png"
    if not path.exists():
        return None

    return None


async def answer(
    target: Message | CallbackQuery,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
    image: Optional[str] = None,
) -> Optional[Message]:
    if image:
        try:
            user = (target.from_user if isinstance(target, Message) else (target.from_user or (target.message.from_user if target.message else None)))
            lang = get_lang(getattr(user, "id", None))
            if lang == "ru":
                ru_key = f"{image}_ru"
                ru_path = IMAGES_DIR / f"{ru_key}.png"
                if ru_path.exists():
                    image = ru_key
        except Exception:
            pass
    preview_options = link_preview_options(image)
    if preview_options:
        image = None
    disable_web_page_preview = not bool(preview_options)

    if image and len(strip_html(text or "")) > 1024:
        image = None

    if isinstance(target, CallbackQuery):
        msg = target.message
        if not msg:
            return None
        
        bot = msg.bot
        chat_id = msg.chat.id
        thread_kwargs = _topic_kwargs(msg)

        try:
            if msg.photo and len(strip_html(text or "")) > 1024:
                try:
                    await msg.delete()
                except Exception:
                    pass
                return await bot.send_message(
                    chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=disable_web_page_preview,
                    link_preview_options=preview_options,
                    **thread_kwargs,
                )

            if preview_options and msg.photo:
                try:
                    await msg.delete()
                except Exception:
                    pass
                return await bot.send_message(
                    chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=False,
                    link_preview_options=preview_options,
                    **thread_kwargs,
                )

            if image and not msg.photo:
                return await msg.edit_text(
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=disable_web_page_preview,
                    link_preview_options=preview_options,
                )

            if image and msg.photo:
                file_id = _image_file_ids.get(image)

                if file_id:
                    return await msg.edit_media(
                        InputMediaPhoto(
                            media=file_id,
                            caption=text,
                            parse_mode=ParseMode.HTML,
                        ),
                        reply_markup=kb,
                    )

                path = IMAGES_DIR / f"{image}.png"
                if path.exists():
                    return await msg.edit_media(
                        InputMediaPhoto(
                            media=FSInputFile(path),
                            caption=text,
                            parse_mode=ParseMode.HTML,
                        ),
                        reply_markup=kb,
                    )

                if msg.photo:
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

                return None
            
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

                return None
                
        except Exception as exc:
            if isinstance(exc, TelegramBadRequest) and (
                _is_not_modified_error(exc) or _is_superseded_error(exc)
            ):
                try:
                    await target.answer()
                except Exception:
                    pass
                return msg

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
                    sent = await bot.send_message(
                        chat_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb,
                        disable_web_page_preview=disable_web_page_preview,
                        link_preview_options=preview_options,
                        **thread_kwargs,
                    )
                    await target.answer("Текст слишком длинный для редактирования, открыл новым сообщением.", show_alert=True)
                    return sent
                except Exception as send_exc:
                    logger.exception(
                        "event=answer.callback_too_long_fallback_failed chat_id=%s text_len=%s error=%s",
                        chat_id,
                        len(text or ""),
                        _short_error(send_exc, 500),
                    )
            try:
                await target.answer(f"Не удалось обновить сообщение: {_short_error(exc)}", show_alert=True)
            except Exception:
                pass
        return None
        
    else:
        chat_id = target.chat.id
        bot = target.bot
        thread_kwargs = _topic_kwargs(target)

    async def _message_send(send_text: str) -> Optional[Message]:
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
            path = IMAGES_DIR / f"{image}.png"
            if path.exists():
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


async def preload_images(bot: Bot) -> None:
    cfg = get_config()
    admins = list(get_admins())
    
    if not admins:
        return
    
    admin_chat_id = admins[0]

    image_keys = ["welcome", "plugins", "profile", "catalog", "icons", "cat_all", "suggestion", "admin", "notifications", "joinly"]
    for cat in get_categories():
        key = cat.get("key")
        if key:
            image_keys.append(f"cat_{key}")
    
    for key in image_keys:
        if key not in _image_file_ids:
            try:
                await _get_photo_input(key, bot, admin_chat_id)
            except Exception:
                pass
