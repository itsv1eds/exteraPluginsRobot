import re
from pathlib import Path
from typing import Dict, Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from bot.cache import get_admins, get_categories, get_config
from storage import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = BASE_DIR / "img"

_image_file_ids: Dict[str, str] = {}
_pending_upload: Dict[str, bool] = {}


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
    if isinstance(target, CallbackQuery):
        msg = target.message
        if not msg:
            return None
        
        bot = msg.bot
        chat_id = msg.chat.id
        
        try:
            if image and not msg.photo:
                sent = await answer(msg, text, kb, image)
                if sent:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                return sent

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
                        disable_web_page_preview=True,
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
                    disable_web_page_preview=True,
                )

                return None
                
        except Exception:
            try:
                return await bot.send_message(
                    chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
        return None
        
    else:
        chat_id = target.chat.id
        bot = target.bot
    
    if image:
        file_id = _image_file_ids.get(image)
        
        if file_id:
            return await bot.send_photo(
                chat_id,
                photo=file_id,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            path = IMAGES_DIR / f"{image}.png"
            if path.exists():
                msg = await bot.send_photo(
                    chat_id,
                    photo=FSInputFile(path),
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                if msg.photo:
                    _image_file_ids[image] = msg.photo[-1].file_id
                return msg
            else:
                return await bot.send_message(
                    chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
    else:
        return await bot.send_message(
            chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )

    return None


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def extract_html_text(message: Message) -> str:
    return message.html_text or message.text or ""


async def preload_images(bot: Bot) -> None:
    cfg = get_config()
    admins = list(get_admins())
    
    if not admins:
        return
    
    admin_chat_id = admins[0]

    image_keys = ["welcome", "plugins", "profile", "catalog", "icons", "cat_all", "suggestion"]
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