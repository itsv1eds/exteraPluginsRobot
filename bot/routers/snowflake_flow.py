import json
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters import ChatMemberUpdatedFilter
from aiogram.filters.chat_member_updated import IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import ChatMemberUpdated, Message

from storage import DATA_DIR
from bot.helpers import try_react_pray

router = Router()

_DB_PATH = DATA_DIR / "snowflake_db.json"

_DEFAULTS: dict[str, Any] = {
    "DeleteServiceMessages": True,
    "BanMembers": False,
    "Enabled": True,
}


def _load_db() -> dict[str, Any]:
    try:
        if _DB_PATH.exists():
            return json.loads(_DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _save_db(db: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _DB_PATH.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _get_setting(chat_id: int, key: str) -> Any:
    db = _load_db()
    chat_key = str(chat_id)
    chat_cfg = db.get(chat_key)
    if not isinstance(chat_cfg, dict):
        chat_cfg = {}
        db[chat_key] = chat_cfg

    if key not in chat_cfg and key in _DEFAULTS:
        chat_cfg[key] = _DEFAULTS[key]
        _save_db(db)

    return chat_cfg.get(key, _DEFAULTS.get(key))


def _set_setting(chat_id: int, key: str, value: Any) -> None:
    db = _load_db()
    chat_key = str(chat_id)
    chat_cfg = db.get(chat_key)
    if not isinstance(chat_cfg, dict):
        chat_cfg = {}
        db[chat_key] = chat_cfg
    chat_cfg[key] = value
    _save_db(db)


def _is_group(message: Message) -> bool:
    return message.chat.type in {"group", "supergroup"}


def _format_user(user) -> tuple[str, str]:
    name = (getattr(user, "full_name", None) or getattr(user, "first_name", None) or "").strip()
    username = (getattr(user, "username", None) or "").strip()
    username_fmt = f"@{username}" if username else "—"
    return name or "—", username_fmt


@router.message(F.new_chat_members)
async def on_new_members(message: Message) -> None:
    if not _is_group(message):
        return

    for u in list(message.new_chat_members or []):
        name, username_fmt = _format_user(u)
        await message.answer(
            f"Дорогой {name} ({username_fmt}), это не чат для общения.\n"
            f"Для общения есть @exteraForum",
            disable_web_page_preview=True,
        )

    if _get_setting(message.chat.id, "DeleteServiceMessages"):
        try:
            await message.delete()
        except Exception:
            pass


@router.message(Command("help"))
async def on_help(message: Message) -> None:
    if not _is_group(message):
        return
    await try_react_pray(message)


@router.message(Command("toggle"))
async def on_toggle(message: Message) -> None:
    if not _is_group(message):
        return
    enabled = bool(_get_setting(message.chat.id, "Enabled"))
    _set_setting(message.chat.id, "Enabled", not enabled)


@router.message(Command("snowflake_ban"))
async def on_toggle_ban_mode(message: Message) -> None:
    if not _is_group(message):
        return
    enabled = bool(_get_setting(message.chat.id, "BanMembers"))
    _set_setting(message.chat.id, "BanMembers", not enabled)


@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_member_join(event: ChatMemberUpdated) -> None:
    gotme = await event.bot.get_me()
    me = await event.bot.get_chat_member(event.chat.id, gotme.id)

    if getattr(me, "status", None) not in {"administrator", "creator"}:
        return

    if not _get_setting(event.chat.id, "Enabled"):
        return

    try:
        await event.chat.ban(event.new_chat_member.user.id)
        if not _get_setting(event.chat.id, "BanMembers"):
            await event.chat.unban(event.new_chat_member.user.id)
    except Exception:
        return


@router.message()
async def on_service_messages(message: Message) -> None:
    if not _is_group(message):
        return

    if not _get_setting(message.chat.id, "DeleteServiceMessages"):
        return

    if message.new_chat_members or message.left_chat_member:
        try:
            gotme = await message.bot.get_me()
            me = await message.bot.get_chat_member(message.chat.id, gotme.id)
            if getattr(me, "status", None) not in {"administrator", "creator"}:
                return
            await message.delete()
        except Exception:
            pass
