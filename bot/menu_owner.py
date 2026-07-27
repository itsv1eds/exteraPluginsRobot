from __future__ import annotations

from collections import OrderedDict

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.context import get_lang
from bot.texts import t

MENU_OWNER_KEY = "menu_owner_user_id"
MENU_OWNER_CHAT_KEY = "menu_owner_chat_id"
MENU_OWNER_MESSAGE_KEY = "menu_owner_message_id"

_MAX_TRACKED_MENUS = 2000
_menu_owners: "OrderedDict[tuple[int, int], int]" = OrderedDict()


def _remember(chat_id: int, message_id: int, owner_id: int) -> None:
    key = (int(chat_id), int(message_id))
    _menu_owners[key] = int(owner_id)
    _menu_owners.move_to_end(key)
    while len(_menu_owners) > _MAX_TRACKED_MENUS:
        _menu_owners.popitem(last=False)


def menu_owner_of(chat_id: int | None, message_id: int | None) -> int | None:
    if not chat_id or not message_id:
        return None
    return _menu_owners.get((int(chat_id), int(message_id)))


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
        _remember(msg.chat.id, msg.message_id, user.id)
    await state.update_data(payload)


async def ensure_menu_owner(cb: CallbackQuery, state: FSMContext) -> bool:
    user = cb.from_user
    message = cb.message if isinstance(cb.message, Message) else None
    if not user or not message:
        return True

    if getattr(message.chat, "type", "private") == "private":
        return True

    owner_id = menu_owner_of(message.chat.id, message.message_id)
    if owner_id is None:
        data = await state.get_data()
        owner_chat_id = data.get(MENU_OWNER_CHAT_KEY)
        owner_message_id = data.get(MENU_OWNER_MESSAGE_KEY)
        if (
            owner_chat_id
            and owner_message_id
            and int(owner_chat_id) == int(message.chat.id)
            and int(owner_message_id) == int(message.message_id)
        ):
            owner_id = data.get(MENU_OWNER_KEY)

    if owner_id is None or int(owner_id) == int(user.id):
        return True

    await cb.answer(t("menu_owner_mismatch", get_lang(user.id)), show_alert=True)
    return False


class MenuOwnerMiddleware:
    async def __call__(self, handler, event: CallbackQuery, data: dict):
        state = data.get("state")
        if isinstance(state, FSMContext):
            if not await ensure_menu_owner(event, state):
                return None
        return await handler(event, data)
