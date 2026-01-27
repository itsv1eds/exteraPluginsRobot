from typing import Optional

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.texts import DEFAULT_LANGUAGE
from user_store import get_user_language


def get_lang(user_id: Optional[int]) -> str:
    if not user_id:
        return DEFAULT_LANGUAGE
    lang = get_user_language(user_id)
    return lang if lang in ("ru", "en") else DEFAULT_LANGUAGE


async def get_language(target: Message | CallbackQuery, state: FSMContext) -> str:
    data = await state.get_data()
    lang = data.get("lang")
    if lang in ("ru", "en"):
        return lang

    user = target.from_user
    lang = get_lang(user.id if user else None)
    await state.update_data(lang=lang)
    return lang
