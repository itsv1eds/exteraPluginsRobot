import re
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from bot.cache import get_config

BASE_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = BASE_DIR / "img"

_image_cache: Dict[str, FSInputFile] = {}


def get_image(key: str) -> Optional[FSInputFile]:
    if key in _image_cache:
        return _image_cache[key]
    
    path = IMAGES_DIR / f"{key}.png"
    if path.exists():
        _image_cache[key] = FSInputFile(path)
        return _image_cache[key]
    return None


def get_uploads_dir() -> Path:
    cfg = get_config()
    path = Path(cfg.get("storage", {}).get("attachments_dir", "uploads"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename(value: str) -> str:
    return re.sub(r"[^\w\-@.]", "", value).strip("._") or "plugin"


async def download_document(bot: Bot, file_id: str, dest_dir: Path) -> Path:
    file = await bot.get_file(file_id)
    name = Path(file.file_path).name if file.file_path else f"{file_id}.plugin"
    dest = dest_dir / name
    await bot.download_file(file.file_path, dest)
    return dest


async def answer(
    target: Message | CallbackQuery,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
    image: Optional[str] = None,
) -> None:
    photo = get_image(image) if image else None
    
    if isinstance(target, CallbackQuery):
        msg = target.message
        if not msg:
            return
        
        try:
            if photo:
                await msg.edit_media(
                    InputMediaPhoto(
                        media=photo,
                        caption=text,
                        parse_mode=ParseMode.HTML,
                    ),
                    reply_markup=kb,
                )
            elif msg.photo:
                await msg.edit_caption(
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
            else:
                await msg.edit_text(
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
            return
        except Exception:
            pass
        
        chat_id = msg.chat.id
        bot = msg.bot
    else:
        chat_id = target.chat.id
        bot = target.bot
    
    if photo:
        await bot.send_photo(
            chat_id,
            photo=photo,
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    else:
        await bot.send_message(
            chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def extract_html_text(message: Message) -> str:
    return message.html_text or message.text or ""