from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.context import get_lang
from bot.texts import t

MENU_OWNER_KEY = "menu_owner_user_id"
MENU_OWNER_CHAT_KEY = "menu_owner_chat_id"
MENU_OWNER_MESSAGE_KEY = "menu_owner_message_id"


async def remember_menu_owner(
    target: Message | CallbackQuery,
    state: FSMContext,
    menu_message: Message | None = None,
) -> None:
    user = target.from_user
    if not user:
        return

    msg = menu_message
    if msg is None and isinstance(target, CallbackQuery):
        msg = target.message if isinstance(target.message, Message) else None

    payload = {MENU_OWNER_KEY: int(user.id)}
    if msg:
        payload[MENU_OWNER_CHAT_KEY] = int(msg.chat.id)
        payload[MENU_OWNER_MESSAGE_KEY] = int(msg.message_id)
    await state.update_data(payload)


async def ensure_menu_owner(cb: CallbackQuery, state: FSMContext) -> bool:
    data = await state.get_data()
    owner_id = data.get(MENU_OWNER_KEY)
    owner_chat_id = data.get(MENU_OWNER_CHAT_KEY)
    owner_message_id = data.get(MENU_OWNER_MESSAGE_KEY)
    user = cb.from_user
    message = cb.message if isinstance(cb.message, Message) else None
    if owner_chat_id and owner_message_id:
        if not message:
            return True
        if int(owner_chat_id) != int(message.chat.id) or int(owner_message_id) != int(message.message_id):
            return True
    else:
        return True

    if not owner_id or not user or int(owner_id) == int(user.id):
        return True

    lang = get_lang(user.id)
    await cb.answer(t("menu_owner_mismatch", lang), show_alert=True)
    return False


class MenuOwnerMiddleware:
    async def __call__(self, handler, event: CallbackQuery, data: dict):
        state = data.get("state")
        if isinstance(state, FSMContext):
            if not await ensure_menu_owner(event, state):
                return None
        return await handler(event, data)
