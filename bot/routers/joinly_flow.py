import logging
import asyncio
import time
from typing import Any

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.filters import ChatMemberUpdatedFilter
from aiogram.filters.chat_member_updated import IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import CallbackQuery, ChatMemberUpdated, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Message

from storage import load_joinly, save_joinly
from bot.cache import get_admins_super
from bot.helpers import try_react_pray
from bot.context import get_lang
from bot.formatting import telegram_html
from bot.keyboards import _btn
from bot.texts import t

router = Router()

logger = logging.getLogger(__name__)
_post_guard_unlock_tasks: dict[int, asyncio.Task] = {}

_RETRY_CAP_SECONDS = 5.0
_ADMIN_STATUS_TTL = 60.0
_me_cache: dict[int, Any] = {}
_admin_status_cache: dict[int, tuple[float, bool]] = {}


async def _safe_telegram(factory, *, retries: int = 1):
    attempt = 0
    while True:
        try:
            return await factory()
        except TelegramRetryAfter as exc:
            if attempt >= retries:
                logger.warning("joinly: giving up after RetryAfter=%ss", exc.retry_after)
                return None
            attempt += 1
            await asyncio.sleep(min(float(exc.retry_after or 1), _RETRY_CAP_SECONDS))
        except TelegramBadRequest as exc:
            logger.debug("joinly: bad request: %s", exc)
            return None
        except Exception:
            logger.exception("joinly: telegram call failed")
            return None


async def _get_me_cached(bot):
    me = _me_cache.get(id(bot))
    if me is None:
        me = await bot.get_me()
        _me_cache[id(bot)] = me
    return me


async def _bot_is_admin(bot, chat_id: int) -> bool:
    now = time.monotonic()
    cached = _admin_status_cache.get(chat_id)
    if cached and (now - cached[0]) < _ADMIN_STATUS_TTL:
        return cached[1]
    try:
        me = await _get_me_cached(bot)
        member = await bot.get_chat_member(chat_id, me.id)
        is_admin = getattr(member, "status", None) in {"administrator", "creator"}
    except Exception:
        is_admin = False
    _admin_status_cache[chat_id] = (now, is_admin)
    return is_admin

_DEFAULTS: dict[str, Any] = {
    "DeleteServiceMessages": False,
    "BanMembers": False,
    "Enabled": False,
    "WelcomeEnabled": False,
    "WelcomeText": t("join_welcome_default", "ru"),
    "JoinReactionEmoji": "",
    "PostGuardEnabled": False,
    "PostLockSeconds": 0,
    "PostRulesEnabled": False,
    "PostRulesText": t("join_post_rules_default", "ru"),
    "PostLockPermissions": ["can_send_messages"],
    "PostLockedPermissions": [],
    "PostLastKey": "",
    "PostLockUntil": 0,
    "PostOriginalPermissions": {},
}

_CHAT_PERMISSION_FIELDS = (
    "can_send_messages",
    "can_send_audios",
    "can_send_documents",
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
    "can_send_polls",
    "can_send_other_messages",
    "can_add_web_page_previews",
    "can_change_info",
    "can_invite_users",
    "can_pin_messages",
    "can_manage_topics",
)

_POST_LOCK_PERMISSION_LABELS = {
    "can_send_messages": {"ru": "Сообщения", "en": "Messages"},
    "can_send_audios": {"ru": "Аудио", "en": "Audio"},
    "can_send_documents": {"ru": "Файлы", "en": "Files"},
    "can_send_photos": {"ru": "Фото", "en": "Photos"},
    "can_send_videos": {"ru": "Видео", "en": "Videos"},
    "can_send_video_notes": {"ru": "Кружки", "en": "Video notes"},
    "can_send_voice_notes": {"ru": "Голосовые", "en": "Voice notes"},
    "can_send_polls": {"ru": "Опросы", "en": "Polls"},
    "can_send_other_messages": {"ru": "Стикеры/GIF", "en": "Stickers/GIF"},
    "can_add_web_page_previews": {"ru": "Превью ссылок", "en": "Link previews"},
}


def _lang_for(target: Message | CallbackQuery | int | None) -> str:
    if isinstance(target, int):
        return get_lang(target)
    user = target.from_user if target else None
    return get_lang(user.id if user else None)


def _escape_md_v2(text: str) -> str:
    return "".join(("\\" + ch) if ch in "_[]()~`>#+-=|{}.!" else ch for ch in (text or ""))


def _unescape_md_v2(text: str) -> str:
    import re

    return re.sub(r"\\([_\[\]\(\)~`>#+\-=|{}\.!*])", r"\1", text or "")


def _build_welcome_vars(message: Message, user) -> dict[str, str]:
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    fullname = (getattr(user, "full_name", None) or (first + (" " + last if last else ""))).strip()
    username_raw = (getattr(user, "username", None) or "").strip()
    username = f"@{username_raw}" if username_raw else "—"
    chatname = (getattr(message.chat, "title", None) or "").strip() or "—"
    user_id = str(getattr(user, "id", "") or "")
    mention_name = _escape_md_v2(first or fullname or "user")
    mention = f"[{mention_name}](tg://user?id={user_id})" if user_id else mention_name
    return {
        "first": first or "—",
        "last": last or "—",
        "fullname": fullname or "—",
        "username": username,
        "mention": mention,
        "id": user_id,
        "chatname": chatname,
        "name": fullname or first or "—",
    }


def _escape_vars_for_md(vars_map: dict[str, str]) -> dict[str, str]:
    escaped: dict[str, str] = {}
    for k, v in (vars_map or {}).items():
        if k == "mention":
            escaped[k] = v
        else:
            escaped[k] = _escape_md_v2(v)
    return escaped


def _parse_buttonurl_md(text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    import re

    if not text:
        return text, None

    pattern = re.compile(r"\[(?P<label>[^\]]+)\]\(buttonurl://(?P<url>[^)]+)\)")
    buttons: list[tuple[str, str, bool]] = []

    def _collect(m: re.Match) -> str:
        raw_url = (m.group("url") or "").strip()
        same = False
        if raw_url.endswith(":same"):
            same = True
            raw_url = raw_url[: -len(":same")]
        buttons.append((m.group("label"), raw_url, same))
        return ""

    cleaned = pattern.sub(_collect, text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()
    if not buttons:
        return cleaned, None

    rows: list[list[InlineKeyboardButton]] = []
    for label, url, same in buttons:
        btn = InlineKeyboardButton(text=label, url=url)
        if not rows or not same:
            rows.append([btn])
        else:
            rows[-1].append(btn)
    return cleaned, InlineKeyboardMarkup(inline_keyboard=rows)


def _extract_flags(text: str) -> tuple[str, dict[str, bool]]:
    flags = {
        "preview": False,
        "nonotif": False,
        "protect": False,
        "mediaspoiler": False,
    }
    out = text or ""
    for key in list(flags.keys()):
        token = "{" + key + "}"
        if token in out:
            flags[key] = True
            out = out.replace(token, "")
    return out.strip(), flags


def _onoff(value: bool, lang: str) -> str:
    if lang == "en":
        return "on" if value else "off"
    return "вкл" if value else "выкл"


def _status_icon(value: bool) -> str:
    return "yes" if value else "no"


def _status_style(value: bool) -> str:
    return "success" if value else "danger"


def _settings_kb(chat_id: int, lang: str) -> InlineKeyboardMarkup:
    enabled = bool(_get_setting(chat_id, "Enabled"))
    ban = bool(_get_setting(chat_id, "BanMembers"))
    cleanup = bool(_get_setting(chat_id, "DeleteServiceMessages"))
    welcome_enabled = bool(_get_setting(chat_id, "WelcomeEnabled"))
    post_guard_enabled = bool(_get_setting(chat_id, "PostGuardEnabled"))
    reaction_emoji = str(_get_setting(chat_id, "JoinReactionEmoji") or "").strip()
    welcome_label = f"{t('join_btn_welcome', lang)}: {_onoff(welcome_enabled, lang)}"
    enabled_label = f"{t('join_btn_enabled', lang)}: {_onoff(enabled, lang)}"
    ban_label = f"{t('join_btn_ban_on_join', lang)}: {_onoff(ban, lang)}"
    cleanup_label = f"{t('join_btn_service_cleanup', lang)}: {_onoff(cleanup, lang)}"
    post_guard_label = f"{t('join_btn_post_guard', lang)}: {_onoff(post_guard_enabled, lang)}"

    rows: list[list[InlineKeyboardButton]] = [
        [_btn(welcome_label, callback_data="join:welcome", icon=_status_icon(welcome_enabled), style=_status_style(welcome_enabled))],
        [_btn(post_guard_label, callback_data="join:post_guard", icon="broadcast", style=_status_style(post_guard_enabled))],
        [
            _btn(enabled_label, callback_data="join:toggle_enabled", icon=_status_icon(enabled), style=_status_style(enabled)),
            _btn(ban_label, callback_data="join:toggle_ban", icon=_status_icon(ban), style=_status_style(ban)),
        ],
        [_btn(cleanup_label, callback_data="join:toggle_service", icon=_status_icon(cleanup), style=_status_style(cleanup))],
    ]

    if not cleanup:
        label = "задана" if reaction_emoji else "не задана"
        if lang == "en":
            label = "set" if reaction_emoji else "not set"
        rows.append(
            [_btn(f"{t('join_btn_join_reaction', lang)}: {label}", callback_data="join:reaction", icon="edit")]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _panel_kb_back(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(t("btn_back", lang), callback_data="join:back", icon="back")]])


def _panel_kb_welcome(chat_id: int, lang: str) -> InlineKeyboardMarkup:
    welcome_enabled = bool(_get_setting(chat_id, "WelcomeEnabled"))
    welcome_toggle_label = f"{t('join_btn_welcome_toggle', lang)}: {_onoff(welcome_enabled, lang)}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(welcome_toggle_label, callback_data="join:welcome_toggle", icon=_status_icon(welcome_enabled), style=_status_style(welcome_enabled))],
            [_btn(t("join_btn_edit", lang), callback_data="join:welcome_edit", icon="edit")],
            [_btn(t("btn_back", lang), callback_data="join:back", icon="back")],
        ]
    )


def _panel_kb_post_guard(chat_id: int, lang: str) -> InlineKeyboardMarkup:
    guard_enabled = bool(_get_setting(chat_id, "PostGuardEnabled"))
    rules_enabled = bool(_get_setting(chat_id, "PostRulesEnabled"))
    seconds = int(_get_setting(chat_id, "PostLockSeconds") or 0)
    guard_label = f"{t('join_btn_post_guard_toggle', lang)}: {_onoff(guard_enabled, lang)}"
    rules_label = f"{t('join_btn_post_rules_toggle', lang)}: {_onoff(rules_enabled, lang)}"
    seconds_label = f"{t('join_btn_post_lock_seconds', lang)}: {seconds}s"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(guard_label, callback_data="join:post_guard_toggle", icon=_status_icon(guard_enabled), style=_status_style(guard_enabled)),
                _btn(rules_label, callback_data="join:post_rules_toggle", icon=_status_icon(rules_enabled), style=_status_style(rules_enabled)),
            ],
            [_btn(seconds_label, callback_data="join:post_lock_seconds", icon="clock")],
            [_btn(t("join_btn_post_permissions", lang), callback_data="join:post_permissions", icon="lock")],
            [_btn(t("join_btn_post_rules_edit", lang), callback_data="join:post_rules_edit", icon="edit")],
            [_btn(t("join_btn_post_unlock_now", lang), callback_data="join:post_unlock_now", icon="lock", style="success")],
            [_btn(t("btn_back", lang), callback_data="join:back", icon="back")],
        ]
    )


def _post_lock_permissions(chat_id: int) -> list[str]:
    allowed = set(_CHAT_PERMISSION_FIELDS)
    raw = _get_setting(chat_id, "PostLockPermissions")
    if not isinstance(raw, list):
        raw = []
    values = [str(item) for item in raw if str(item) in allowed]
    return values or ["can_send_messages"]


def _post_lock_permission_label(field: str, lang: str) -> str:
    labels = _POST_LOCK_PERMISSION_LABELS.get(field) or {}
    return labels.get(lang) or labels.get("ru") or field


def _panel_kb_post_permissions(chat_id: int, lang: str) -> InlineKeyboardMarkup:
    selected = set(_post_lock_permissions(chat_id))
    rows: list[list[InlineKeyboardButton]] = []
    fields = [field for field in _CHAT_PERMISSION_FIELDS if field in _POST_LOCK_PERMISSION_LABELS]
    for i in range(0, len(fields), 2):
        row = []
        for field in fields[i : i + 2]:
            enabled = field in selected
            label = _post_lock_permission_label(field, lang)
            row.append(_btn(label, callback_data=f"join:post_perm:{field}", icon=_status_icon(enabled), style=_status_style(enabled)))
        rows.append(row)
    rows.append([_btn(t("btn_back", lang), callback_data="join:post_guard", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _panel_kb_reaction(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(t("join_btn_edit", lang), callback_data="join:reaction_edit", icon="edit")],
            [_btn(t("btn_back", lang), callback_data="join:back", icon="back")],
        ]
    )


async def _show_panel_main(message: Message, lang: str) -> None:
    await message.edit_text(t("join_settings_title", lang), reply_markup=_settings_kb(message.chat.id, lang))


async def _show_panel_main_by_id(bot, chat_id: int, message_id: int, lang: str) -> None:
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=t("join_settings_title", lang),
        reply_markup=_settings_kb(chat_id, lang),
    )


async def _can_manage_chat_settings(bot, chat_id: int, user_id: int | None) -> bool:
    if not user_id:
        return False
    if int(user_id) in get_admins_super():
        return True
    try:
        member = await bot.get_chat_member(chat_id, int(user_id))
        return getattr(member, "status", None) in {"administrator", "creator"}
    except Exception:
        return False


async def _is_chat_admin(message: Message) -> bool:
    return await _can_manage_chat_settings(
        message.bot,
        message.chat.id,
        message.from_user.id if message.from_user else None,
    )


def _load_db() -> dict[str, Any]:
    try:
        db = load_joinly()
        if isinstance(db, dict):
            return db
    except Exception:
        return {}
    return {}


def _save_db(db: dict[str, Any]) -> None:
    try:
        save_joinly(db)
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


def _panel_key(user_id: int) -> str:
    return f"PanelMessageId:{user_id}"


def _get_panel_message_id(chat_id: int, user_id: int) -> int:
    raw = _get_setting(chat_id, _panel_key(user_id))
    try:
        return int(raw or 0)
    except Exception:
        return 0


def _set_panel_message_id(chat_id: int, user_id: int, message_id: int) -> None:
    _set_setting(chat_id, _panel_key(user_id), int(message_id or 0))


def _is_group(message: Message) -> bool:
    return message.chat.type in {"group", "supergroup"}


def _format_user(user) -> tuple[str, str]:
    name = (getattr(user, "full_name", None) or getattr(user, "first_name", None) or "").strip()
    username = (getattr(user, "username", None) or "").strip()
    username_fmt = f"@{username}" if username else "—"
    return name or "—", username_fmt


def _permissions_to_dict(permissions: Any) -> dict[str, bool]:
    if not permissions:
        return {}
    try:
        data = permissions.model_dump(exclude_none=True)
    except Exception:
        data = {}
        for field in getattr(ChatPermissions, "model_fields", {}):
            value = getattr(permissions, field, None)
            if value is not None:
                data[field] = value
    return {k: bool(v) for k, v in data.items() if k.startswith("can_")}


def _permissions_from_dict(data: Any) -> ChatPermissions:
    allowed = set(getattr(ChatPermissions, "model_fields", {}).keys())
    if not isinstance(data, dict):
        data = {}
    values = {k: bool(v) for k, v in data.items() if k in allowed and k.startswith("can_")}
    if values and any(values.values()):
        return ChatPermissions(**values)
    return _open_permissions()


def _permissions_with_updates(permissions: Any, updates: dict[str, bool]) -> ChatPermissions:
    allowed = set(getattr(ChatPermissions, "model_fields", {}).keys())
    values = _permissions_to_dict(permissions)
    for field in _CHAT_PERMISSION_FIELDS:
        if field in allowed:
            values.setdefault(field, True)
    for field, value in updates.items():
        if field in allowed and field.startswith("can_"):
            values[field] = bool(value)
    return ChatPermissions(**values)


def _permissions_with_value(value: bool) -> ChatPermissions:
    allowed = set(getattr(ChatPermissions, "model_fields", {}).keys())
    values = {field: value for field in _CHAT_PERMISSION_FIELDS if field in allowed}
    values.setdefault("can_send_messages", value)
    return ChatPermissions(**values)


def _open_permissions() -> ChatPermissions:
    return _permissions_with_value(True)


def _locked_permissions() -> ChatPermissions:
    return _permissions_with_value(False)


async def _set_chat_permissions(bot, chat_id: int, permissions: ChatPermissions) -> None:
    try:
        await bot.set_chat_permissions(
            chat_id=chat_id,
            permissions=permissions,
            use_independent_chat_permissions=True,
        )
    except TypeError:
        await bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)


async def _force_open_chat_permissions(bot, chat_id: int) -> None:
    permissions = _open_permissions()
    errors: list[Exception] = []
    for kwargs in (
        {"chat_id": chat_id, "permissions": permissions, "use_independent_chat_permissions": True},
        {"chat_id": chat_id, "permissions": permissions, "use_independent_chat_permissions": False},
        {"chat_id": chat_id, "permissions": permissions},
    ):
        try:
            await bot.set_chat_permissions(**kwargs)
            return
        except TypeError as exc:
            errors.append(exc)
            continue
        except Exception as exc:
            errors.append(exc)
            continue
    if errors:
        raise errors[-1]


async def _chat_allows_messages(bot, chat_id: int) -> bool:
    try:
        chat = await bot.get_chat(chat_id)
    except Exception:
        return True
    permissions = getattr(chat, "permissions", None)
    if not permissions:
        return True
    value = getattr(permissions, "can_send_messages", None)
    return value is not False


async def _get_chat_permissions(bot, chat_id: int) -> Any:
    try:
        chat = await bot.get_chat(chat_id)
        return getattr(chat, "permissions", None)
    except Exception:
        return None


def _post_source_key(message: Message) -> str:
    origin = getattr(message, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None)
    origin_chat_id = getattr(origin_chat, "id", None)
    origin_message_id = getattr(origin, "message_id", None)
    if origin_chat_id:
        return f"{origin_chat_id}:{origin_message_id or message.message_id}"

    sender_chat = getattr(message, "sender_chat", None)
    sender_chat_id = getattr(sender_chat, "id", None)
    if sender_chat_id:
        return f"{sender_chat_id}:{message.message_id}"

    return f"{message.chat.id}:{message.message_id}"


def _is_channel_auto_post(message: Message) -> bool:
    if not _is_group(message):
        return False
    if not bool(getattr(message, "is_automatic_forward", False)):
        return False

    sender_chat = getattr(message, "sender_chat", None)
    if getattr(sender_chat, "type", None) == "channel":
        return True

    origin = getattr(message, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None)
    return getattr(origin_chat, "type", None) == "channel"


async def _restore_post_guard_permissions(bot, chat_id: int, lock_until: int) -> None:
    try:
        delay = max(0, lock_until - int(time.time()))
        if delay:
            await asyncio.sleep(delay)

        current_until = int(_get_setting(chat_id, "PostLockUntil") or 0)
        if current_until != int(lock_until):
            return

        original = _get_setting(chat_id, "PostOriginalPermissions")
        locked = _get_setting(chat_id, "PostLockedPermissions")
        if not isinstance(original, dict):
            original = {}
        if not isinstance(locked, list):
            locked = list(original.keys())
        updates: dict[str, bool] = {}
        for field in locked:
            field = str(field)
            if field not in _CHAT_PERMISSION_FIELDS:
                continue
            value = original.get(field, True)
            updates[field] = bool(value) if value is not None else True
        permissions = await _get_chat_permissions(bot, chat_id)
        await _set_chat_permissions(bot, chat_id, _permissions_with_updates(permissions, updates))
    except Exception:
        logger.exception("event=joinly.post_guard.unlock_failed chat_id=%s", chat_id)
        return

    _set_setting(chat_id, "PostLockUntil", 0)


async def _unlock_chat_now(bot, chat_id: int) -> None:
    old_task = _post_guard_unlock_tasks.pop(chat_id, None)
    if old_task and not old_task.done():
        old_task.cancel()
    await _force_open_chat_permissions(bot, chat_id)
    _set_setting(chat_id, "PostLockUntil", 0)


def _schedule_post_guard_unlock(bot, chat_id: int, lock_until: int) -> None:
    old_task = _post_guard_unlock_tasks.get(chat_id)
    if old_task and not old_task.done():
        old_task.cancel()

    task = asyncio.create_task(_restore_post_guard_permissions(bot, chat_id, lock_until))
    _post_guard_unlock_tasks[chat_id] = task

    def _clear(done_task: asyncio.Task) -> None:
        if _post_guard_unlock_tasks.get(chat_id) is done_task:
            _post_guard_unlock_tasks.pop(chat_id, None)

    task.add_done_callback(_clear)


async def _lock_chat_after_post(message: Message, seconds: int) -> None:
    if seconds <= 0:
        return

    lock_until = int(time.time()) + seconds
    current_until = int(_get_setting(message.chat.id, "PostLockUntil") or 0)
    selected_permissions = _post_lock_permissions(message.chat.id)
    if current_until <= int(time.time()):
        permissions = await _get_chat_permissions(message.bot, message.chat.id) or getattr(message.chat, "permissions", None)
        original = {}
        for field in selected_permissions:
            value = getattr(permissions, field, None) if permissions else None
            original[field] = bool(value) if value is not None else True
        _set_setting(message.chat.id, "PostOriginalPermissions", original)
        _set_setting(message.chat.id, "PostLockedPermissions", selected_permissions)
    else:
        locked = _get_setting(message.chat.id, "PostLockedPermissions")
        if isinstance(locked, list):
            selected_permissions = sorted(set(str(item) for item in locked) | set(selected_permissions))
            _set_setting(message.chat.id, "PostLockedPermissions", selected_permissions)

    _set_setting(message.chat.id, "PostLockUntil", lock_until)
    try:
        permissions = await _get_chat_permissions(message.bot, message.chat.id) or getattr(message.chat, "permissions", None)
        await _set_chat_permissions(
            message.bot,
            message.chat.id,
            _permissions_with_updates(permissions, {field: False for field in selected_permissions}),
        )
        after_permissions = await _get_chat_permissions(message.bot, message.chat.id)
        original = _get_setting(message.chat.id, "PostOriginalPermissions")
        if not isinstance(original, dict):
            original = {}
        locked = set(str(item) for item in (_get_setting(message.chat.id, "PostLockedPermissions") or []))
        for field in _CHAT_PERMISSION_FIELDS:
            before = getattr(permissions, field, None) if permissions else None
            after = getattr(after_permissions, field, None) if after_permissions else None
            if before is not False and after is False:
                original.setdefault(field, True if before is None else bool(before))
                locked.add(field)
        _set_setting(message.chat.id, "PostOriginalPermissions", original)
        _set_setting(message.chat.id, "PostLockedPermissions", [field for field in _CHAT_PERMISSION_FIELDS if field in locked])
    except Exception:
        logger.exception("event=joinly.post_guard.lock_failed chat_id=%s message_id=%s", message.chat.id, message.message_id)
        return

    _schedule_post_guard_unlock(message.bot, message.chat.id, lock_until)


async def _ensure_post_guard_unlock(bot, chat_id: int) -> None:
    lock_until = int(_get_setting(chat_id, "PostLockUntil") or 0)
    if lock_until <= 0:
        return
    if lock_until <= int(time.time()):
        await _restore_post_guard_permissions(bot, chat_id, lock_until)
        return
    current_task = _post_guard_unlock_tasks.get(chat_id)
    if current_task and not current_task.done():
        return
    _schedule_post_guard_unlock(bot, chat_id, lock_until)


async def schedule_pending_post_guard_unlocks(bot) -> None:
    db = _load_db()
    for chat_id_raw, cfg in db.items():
        if not isinstance(cfg, dict):
            continue
        try:
            chat_id = int(chat_id_raw)
            lock_until = int(cfg.get("PostLockUntil") or 0)
        except Exception:
            continue
        if lock_until <= 0:
            continue
        if lock_until <= int(time.time()):
            await _restore_post_guard_permissions(bot, chat_id, lock_until)
        else:
            _schedule_post_guard_unlock(bot, chat_id, lock_until)


async def _send_post_rules(message: Message, lang: str) -> None:
    if not _get_setting(message.chat.id, "PostRulesEnabled"):
        return
    text = str(_get_setting(message.chat.id, "PostRulesText") or t("join_post_rules_default", lang)).strip()
    if not text:
        return
    try:
        await message.reply(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            allow_sending_without_reply=True,
        )
    except TelegramBadRequest:
        await message.reply(
            telegram_html(text),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            allow_sending_without_reply=True,
        )


async def _handle_channel_post(message: Message) -> bool:
    if not _is_channel_auto_post(message):
        return False
    if not bool(_get_setting(message.chat.id, "PostGuardEnabled")) and not bool(_get_setting(message.chat.id, "PostRulesEnabled")):
        return False

    key = _post_source_key(message)
    if key and str(_get_setting(message.chat.id, "PostLastKey") or "") == key:
        return True
    _set_setting(message.chat.id, "PostLastKey", key)

    lang = _lang_for(message)
    seconds = int(_get_setting(message.chat.id, "PostLockSeconds") or 0)
    if bool(_get_setting(message.chat.id, "PostGuardEnabled")):
        await _lock_chat_after_post(message, seconds)
    await _send_post_rules(message, lang)
    return True


@router.message(F.new_chat_members)
async def on_new_members(message: Message) -> None:
    if not _is_group(message):
        return

    try:
        gotme = await _get_me_cached(message.bot)
    except Exception:
        gotme = None

    if gotme and any(getattr(u, "id", None) == getattr(gotme, "id", None) for u in list(message.new_chat_members or [])):
        await _safe_telegram(lambda: message.answer(
            t("joinly_bot_added", _lang_for(message)), parse_mode=ParseMode.HTML
        ))

    if not _get_setting(message.chat.id, "WelcomeEnabled"):
        return

    if not gotme:
        gotme = await _get_me_cached(message.bot)

    if not _get_setting(message.chat.id, "DeleteServiceMessages"):
        emoji = str(_get_setting(message.chat.id, "JoinReactionEmoji") or "").strip()
        if emoji and hasattr(message.bot, "set_message_reaction"):
            if await _bot_is_admin(message.bot, message.chat.id):
                try:
                    from aiogram.types import ReactionTypeEmoji

                    reaction = [ReactionTypeEmoji(emoji=emoji)]
                except Exception:
                    reaction = [emoji]
                await _safe_telegram(lambda: message.bot.set_message_reaction(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reaction=reaction,
                ))
            else:
                logger.info("Join reaction skipped: bot is not admin")
        elif emoji:
            logger.info("Join reaction skipped: set_message_reaction is not available")

    for u in list(message.new_chat_members or []):
        if getattr(u, "id", None) == getattr(gotme, "id", None):
            continue
        raw_vars = _build_welcome_vars(message, u)
        vars_map = _escape_vars_for_md(raw_vars)
        template = str(_get_setting(message.chat.id, "WelcomeText") or _DEFAULTS["WelcomeText"])
        templ, flags = _extract_flags(template)
        rendered = templ.format(**vars_map)
        rendered, kb = _parse_buttonurl_md(rendered)
        sent = await _safe_telegram(lambda: message.answer(
            rendered,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=kb,
            disable_web_page_preview=(not flags.get("preview")),
            disable_notification=bool(flags.get("nonotif")),
            protect_content=bool(flags.get("protect")),
        ))
        if sent is None:
            plain_template = _unescape_md_v2(templ)
            plain_text = plain_template.format(**raw_vars)
            plain_text, safe_kb = _parse_buttonurl_md(plain_text)
            await _safe_telegram(lambda: message.answer(
                plain_text,
                reply_markup=safe_kb,
                disable_web_page_preview=(not flags.get("preview")),
                disable_notification=bool(flags.get("nonotif")),
                protect_content=bool(flags.get("protect")),
            ))

    if _get_setting(message.chat.id, "DeleteServiceMessages"):
        try:
            await message.delete()
        except Exception:
            pass


@router.message(Command("settings"))
async def on_settings(message: Message) -> None:
    if not _is_group(message):
        return
    await _ensure_post_guard_unlock(message.bot, message.chat.id)
    if not await _is_chat_admin(message):
        return
    await try_react_pray(message)
    lang = _lang_for(message)
    current_welcome = _get_setting(message.chat.id, "WelcomeText")
    if not isinstance(current_welcome, str) or not current_welcome.strip():
        _set_setting(message.chat.id, "WelcomeText", t("join_welcome_default", lang))

    user_id = message.from_user.id if message.from_user else 0
    panel_id = _get_panel_message_id(message.chat.id, user_id)
    if panel_id:
        try:
            await _show_panel_main_by_id(message.bot, message.chat.id, panel_id, lang)
            return
        except Exception:
            _set_panel_message_id(message.chat.id, user_id, 0)

    sent = await message.answer(t("join_settings_title", lang), reply_markup=_settings_kb(message.chat.id, lang))
    if user_id and sent:
        _set_panel_message_id(message.chat.id, user_id, sent.message_id)


@router.message(Command("unlockchat"))
async def on_unlock_chat(message: Message) -> None:
    if not _is_group(message):
        return
    if not await _is_chat_admin(message):
        return
    lang = _lang_for(message)
    try:
        await _unlock_chat_now(message.bot, message.chat.id)
    except Exception:
        logger.exception("event=joinly.post_guard.manual_unlock_failed chat_id=%s", message.chat.id)
        await message.answer(t("join_chat_unlock_failed", lang))
        return
    await message.answer(t("join_chat_unlocked", lang))


@router.callback_query(F.data.startswith("join:"))
async def on_settings_cb(cb: CallbackQuery) -> None:
    if cb.message is None:
        await cb.answer()
        return
    if cb.message.chat.type not in {"group", "supergroup"}:
        await cb.answer()
        return
    await _ensure_post_guard_unlock(cb.bot, cb.message.chat.id)
    if cb.from_user is None:
        await cb.answer()
        return

    if not await _can_manage_chat_settings(cb.bot, cb.message.chat.id, cb.from_user.id):
        await cb.answer()
        return

    action = cb.data.split(":", 1)[1]
    if action == "toggle_enabled":
        enabled = bool(_get_setting(cb.message.chat.id, "Enabled"))
        _set_setting(cb.message.chat.id, "Enabled", not enabled)
    elif action == "toggle_ban":
        ban = bool(_get_setting(cb.message.chat.id, "BanMembers"))
        _set_setting(cb.message.chat.id, "BanMembers", not ban)
    elif action == "toggle_service":
        cleanup = bool(_get_setting(cb.message.chat.id, "DeleteServiceMessages"))
        _set_setting(cb.message.chat.id, "DeleteServiceMessages", not cleanup)
    elif action == "post_guard":
        lang = _lang_for(cb)
        await cb.message.edit_text(
            t("join_post_guard_help", lang),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_panel_kb_post_guard(cb.message.chat.id, lang),
        )
        await cb.answer()
        return
    elif action == "post_guard_toggle":
        current = bool(_get_setting(cb.message.chat.id, "PostGuardEnabled"))
        _set_setting(cb.message.chat.id, "PostGuardEnabled", not current)
        if not current and int(_get_setting(cb.message.chat.id, "PostLockSeconds") or 0) <= 0:
            _set_setting(cb.message.chat.id, "PostLockSeconds", 30)
        lang = _lang_for(cb)
        await cb.message.edit_reply_markup(reply_markup=_panel_kb_post_guard(cb.message.chat.id, lang))
        await cb.answer()
        return
    elif action == "post_rules_toggle":
        current = bool(_get_setting(cb.message.chat.id, "PostRulesEnabled"))
        _set_setting(cb.message.chat.id, "PostRulesEnabled", not current)
        lang = _lang_for(cb)
        await cb.message.edit_reply_markup(reply_markup=_panel_kb_post_guard(cb.message.chat.id, lang))
        await cb.answer()
        return
    elif action == "post_lock_seconds":
        _set_setting(cb.message.chat.id, "PostLockEditingUser", int(cb.from_user.id))
        lang = _lang_for(cb)
        await cb.message.edit_text(
            t("join_prompt_post_lock_seconds", lang),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_panel_kb_back(lang),
        )
        await cb.answer()
        return
    elif action == "post_permissions":
        lang = _lang_for(cb)
        await cb.message.edit_text(
            t("join_post_guard_help", lang),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_panel_kb_post_permissions(cb.message.chat.id, lang),
        )
        await cb.answer()
        return
    elif action.startswith("post_perm:"):
        field = action.split(":", 1)[1]
        if field not in _CHAT_PERMISSION_FIELDS:
            await cb.answer()
            return
        selected = set(_post_lock_permissions(cb.message.chat.id))
        if field in selected:
            selected.remove(field)
        else:
            selected.add(field)
        if not selected:
            selected.add("can_send_messages")
        ordered = [item for item in _CHAT_PERMISSION_FIELDS if item in selected]
        _set_setting(cb.message.chat.id, "PostLockPermissions", ordered)
        lang = _lang_for(cb)
        await cb.message.edit_reply_markup(reply_markup=_panel_kb_post_permissions(cb.message.chat.id, lang))
        await cb.answer()
        return
    elif action == "post_rules_edit":
        _set_setting(cb.message.chat.id, "PostRulesEditingUser", int(cb.from_user.id))
        lang = _lang_for(cb)
        await cb.message.edit_text(
            t("join_prompt_post_rules", lang),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_panel_kb_back(lang),
        )
        await cb.answer()
        return
    elif action == "post_unlock_now":
        lang = _lang_for(cb)
        try:
            await _unlock_chat_now(cb.bot, cb.message.chat.id)
        except Exception:
            logger.exception("event=joinly.post_guard.manual_unlock_failed chat_id=%s", cb.message.chat.id)
            await cb.answer(t("join_chat_unlock_failed", lang), show_alert=True)
            return
        await cb.answer(t("join_chat_unlocked", lang), show_alert=True)
        try:
            await cb.message.edit_reply_markup(reply_markup=_panel_kb_post_guard(cb.message.chat.id, lang))
        except Exception:
            pass
        return
    elif action == "welcome":
        lang = _lang_for(cb)
        await cb.message.edit_text(
            t("join_welcome_help", lang),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_panel_kb_welcome(cb.message.chat.id, lang),
        )
        await cb.answer()
        return
    elif action == "welcome_edit":
        _set_setting(cb.message.chat.id, "WelcomeEditingUser", int(cb.from_user.id))
        lang = _lang_for(cb)
        await cb.message.edit_text(
            t("join_prompt_welcome", lang),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_panel_kb_back(lang),
        )
        await cb.answer()
        return
    elif action == "welcome_toggle":
        current = bool(_get_setting(cb.message.chat.id, "WelcomeEnabled"))
        _set_setting(cb.message.chat.id, "WelcomeEnabled", (not current))
        lang = _lang_for(cb)
        try:
            await cb.message.edit_text(
                t("join_welcome_help", lang),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=_panel_kb_welcome(cb.message.chat.id, lang),
            )
        except Exception:
            pass
        await cb.answer()
        return
    elif action == "reaction":
        lang = _lang_for(cb)
        await cb.message.edit_text(
            t("join_reaction_help", lang),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_panel_kb_reaction(lang),
        )
        await cb.answer()
        return
    elif action == "reaction_edit":
        _set_setting(cb.message.chat.id, "ReactionEditingUser", int(cb.from_user.id))
        lang = _lang_for(cb)
        await cb.message.edit_text(
            t("join_prompt_reaction", lang),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_panel_kb_back(lang),
        )
        await cb.answer()
        return
    elif action == "back":
        lang = _lang_for(cb)
        try:
            await _show_panel_main(cb.message, lang)
        except Exception:
            pass
        await cb.answer()
        return

    try:
        lang = _lang_for(cb)
        await cb.message.edit_reply_markup(reply_markup=_settings_kb(cb.message.chat.id, lang))
    except Exception:
        pass
    try:
        await cb.answer()
    except Exception:
        pass


@router.message(F.text)
async def on_welcome_edit(message: Message) -> None:
    if not _is_group(message):
        return
    if await _handle_channel_post(message):
        return
    if not message.from_user:
        return
    lang = _lang_for(message)
    post_lock_editing_user = _get_setting(message.chat.id, "PostLockEditingUser")
    if int(post_lock_editing_user or 0) == int(message.from_user.id):
        raw = (message.text or "").strip()
        try:
            seconds = int(raw)
        except Exception:
            await message.answer(t("join_bad_seconds", lang), parse_mode=ParseMode.HTML)
            return
        if seconds < 0 or seconds > 86400:
            await message.answer(t("join_bad_seconds", lang), parse_mode=ParseMode.HTML)
            return
        _set_setting(message.chat.id, "PostLockSeconds", seconds)
        _set_setting(message.chat.id, "PostLockEditingUser", 0)
        if seconds <= 0:
            _set_setting(message.chat.id, "PostGuardEnabled", False)
        panel_id = _get_panel_message_id(message.chat.id, message.from_user.id)
        if panel_id:
            try:
                await _show_panel_main_by_id(message.bot, message.chat.id, panel_id, lang)
            except Exception:
                await message.answer(t("join_saved", lang), reply_markup=_settings_kb(message.chat.id, lang))
        else:
            await message.answer(t("join_saved", lang), reply_markup=_settings_kb(message.chat.id, lang))
        return

    post_rules_editing_user = _get_setting(message.chat.id, "PostRulesEditingUser")
    if int(post_rules_editing_user or 0) == int(message.from_user.id):
        _set_setting(message.chat.id, "PostRulesText", telegram_html(message.html_text or message.text or ""))
        _set_setting(message.chat.id, "PostRulesEditingUser", 0)
        panel_id = _get_panel_message_id(message.chat.id, message.from_user.id)
        if panel_id:
            try:
                await _show_panel_main_by_id(message.bot, message.chat.id, panel_id, lang)
            except Exception:
                await message.answer(t("join_saved", lang), reply_markup=_settings_kb(message.chat.id, lang))
        else:
            await message.answer(t("join_saved", lang), reply_markup=_settings_kb(message.chat.id, lang))
        return

    editing_user = _get_setting(message.chat.id, "WelcomeEditingUser")
    if int(editing_user or 0) != int(message.from_user.id):
        reaction_editing_user = _get_setting(message.chat.id, "ReactionEditingUser")
        if int(reaction_editing_user or 0) != int(message.from_user.id):
            return

        raw = (message.text or "").strip()
        lowered = raw.lower()
        emoji = ""
        if lowered in {"off", "none", "нет", "0", "-"}:
            emoji = ""
        else:
            emoji = raw.split(maxsplit=1)[0].strip()[:16]
        _set_setting(message.chat.id, "JoinReactionEmoji", emoji)
        _set_setting(message.chat.id, "ReactionEditingUser", 0)

        panel_id = _get_panel_message_id(message.chat.id, message.from_user.id)
        if panel_id:
            try:
                await _show_panel_main_by_id(message.bot, message.chat.id, panel_id, lang)
            except Exception:
                await message.answer(t("join_saved", lang), reply_markup=_settings_kb(message.chat.id, lang))
        else:
            await message.answer(t("join_saved", lang), reply_markup=_settings_kb(message.chat.id, lang))
        return

    _set_setting(message.chat.id, "WelcomeText", message.text or "")
    _set_setting(message.chat.id, "WelcomeEditingUser", 0)
    panel_id = _get_panel_message_id(message.chat.id, message.from_user.id)
    if panel_id:
        try:
            await _show_panel_main_by_id(message.bot, message.chat.id, panel_id, lang)
        except Exception:
            await message.answer(t("join_saved", lang), reply_markup=_settings_kb(message.chat.id, lang))
    else:
        await message.answer(t("join_saved", lang), reply_markup=_settings_kb(message.chat.id, lang))


@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_member_join(event: ChatMemberUpdated) -> None:
    if not _get_setting(event.chat.id, "Enabled"):
        return
    if not await _bot_is_admin(event.bot, event.chat.id):
        return

    user_id = event.new_chat_member.user.id
    ban_members = bool(_get_setting(event.chat.id, "BanMembers"))

    banned = await _safe_telegram(lambda: event.chat.ban(user_id))
    if banned is None:
        logger.warning("joinly: ban failed chat=%s user=%s", event.chat.id, user_id)
        return
    if not ban_members:
        await _safe_telegram(lambda: event.chat.unban(user_id))


@router.message()
async def on_service_messages(message: Message) -> None:
    if not _is_group(message):
        return

    handled_channel_post = await _handle_channel_post(message)
    if handled_channel_post:
        return

    if not _get_setting(message.chat.id, "DeleteServiceMessages"):
        return

    if message.new_chat_members or message.left_chat_member:
        if await _bot_is_admin(message.bot, message.chat.id):
            await _safe_telegram(lambda: message.delete())
