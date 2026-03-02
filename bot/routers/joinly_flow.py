import logging
from typing import Any

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters import ChatMemberUpdatedFilter
from aiogram.filters.chat_member_updated import IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message

from storage import load_joinly, save_joinly
from bot.helpers import try_react_pray
from bot.context import get_lang
from bot.texts import t

router = Router()

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "DeleteServiceMessages": True,
    "BanMembers": False,
    "Enabled": True,
    "WelcomeEnabled": True,
    "WelcomeText": t("join_welcome_default", "ru"),
    "JoinReactionEmoji": "",
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


def _settings_kb(chat_id: int, lang: str) -> InlineKeyboardMarkup:
    enabled = bool(_get_setting(chat_id, "Enabled"))
    ban = bool(_get_setting(chat_id, "BanMembers"))
    cleanup = bool(_get_setting(chat_id, "DeleteServiceMessages"))
    welcome_enabled = bool(_get_setting(chat_id, "WelcomeEnabled"))
    reaction_emoji = str(_get_setting(chat_id, "JoinReactionEmoji") or "").strip()
    welcome_label = f"{t('join_btn_welcome', lang)}: {'✅' if welcome_enabled else '❌'}"
    enabled_label = f"{t('join_btn_enabled', lang)}: {'✅' if enabled else '❌'}"
    ban_label = f"{t('join_btn_ban_on_join', lang)}: {'✅' if ban else '❌'}"
    cleanup_label = f"{t('join_btn_service_cleanup', lang)}: {'✅' if cleanup else '❌'}"

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=welcome_label, callback_data="join:welcome")],
        [
            InlineKeyboardButton(text=enabled_label, callback_data="join:toggle_enabled"),
            InlineKeyboardButton(text=ban_label, callback_data="join:toggle_ban"),
        ],
        [InlineKeyboardButton(text=cleanup_label, callback_data="join:toggle_service")],
    ]

    if not cleanup:
        label = reaction_emoji if reaction_emoji else "❌"
        rows.append(
            [InlineKeyboardButton(text=f"{t('join_btn_join_reaction', lang)}: {label}", callback_data="join:reaction")]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _panel_kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="←", callback_data="join:back")]])


def _panel_kb_welcome(chat_id: int, lang: str) -> InlineKeyboardMarkup:
    welcome_enabled = bool(_get_setting(chat_id, "WelcomeEnabled"))
    welcome_toggle_label = f"{t('join_btn_welcome_toggle', lang)}: {'✅' if welcome_enabled else '❌'}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=welcome_toggle_label, callback_data="join:welcome_toggle")],
            [InlineKeyboardButton(text=t("join_btn_edit", lang), callback_data="join:welcome_edit")],
            [InlineKeyboardButton(text="←", callback_data="join:back")],
        ]
    )


def _panel_kb_reaction(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("join_btn_edit", lang), callback_data="join:reaction_edit")],
            [InlineKeyboardButton(text="←", callback_data="join:back")],
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



async def _is_chat_admin(message: Message) -> bool:
    if not message.from_user:
        return False
    try:
        member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
        return getattr(member, "status", None) in {"administrator", "creator"}
    except Exception:
        return False


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


@router.message(F.new_chat_members)
async def on_new_members(message: Message) -> None:
    if not _is_group(message):
        return

    if not _get_setting(message.chat.id, "WelcomeEnabled"):
        return

    gotme = await message.bot.get_me()

    if not _get_setting(message.chat.id, "DeleteServiceMessages"):
        emoji = str(_get_setting(message.chat.id, "JoinReactionEmoji") or "").strip()
        if emoji and hasattr(message.bot, "set_message_reaction"):
            try:
                me = await message.bot.get_chat_member(message.chat.id, gotme.id)
                if getattr(me, "status", None) in {"administrator", "creator"}:
                    try:
                        from aiogram.types import ReactionTypeEmoji

                        reaction = [ReactionTypeEmoji(emoji=emoji)]
                    except Exception:
                        reaction = [emoji]
                    logger.info("Setting join reaction: %s", emoji)
                    await message.bot.set_message_reaction(
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        reaction=reaction,
                    )
                else:
                    logger.info("Join reaction skipped: bot is not admin")
            except Exception:
                logger.exception("Failed to set join reaction")
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
        try:
            await message.answer(
                rendered,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=kb,
                disable_web_page_preview=(not flags.get("preview")),
                disable_notification=bool(flags.get("nonotif")),
                protect_content=bool(flags.get("protect")),
            )
        except TelegramBadRequest:
            plain_template = _unescape_md_v2(templ)
            plain_text = plain_template.format(**raw_vars)
            plain_text, safe_kb = _parse_buttonurl_md(plain_text)
            await message.answer(
                plain_text,
                reply_markup=safe_kb,
                disable_web_page_preview=(not flags.get("preview")),
                disable_notification=bool(flags.get("nonotif")),
                protect_content=bool(flags.get("protect")),
            )

    if _get_setting(message.chat.id, "DeleteServiceMessages"):
        try:
            await message.delete()
        except Exception:
            pass


@router.message(Command("settings"))
async def on_settings(message: Message) -> None:
    if not _is_group(message):
        return
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


@router.callback_query(F.data.startswith("join:"))
async def on_settings_cb(cb: CallbackQuery) -> None:
    if cb.message is None:
        await cb.answer()
        return
    if cb.message.chat.type not in {"group", "supergroup"}:
        await cb.answer()
        return
    if cb.from_user is None:
        await cb.answer()
        return

    try:
        member = await cb.bot.get_chat_member(cb.message.chat.id, cb.from_user.id)
        if getattr(member, "status", None) not in {"administrator", "creator"}:
            await cb.answer()
            return
    except Exception:
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
            reply_markup=_panel_kb_back(),
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
            reply_markup=_panel_kb_back(),
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
    await cb.answer()


@router.message(F.text)
async def on_welcome_edit(message: Message) -> None:
    if not _is_group(message):
        return
    if not message.from_user:
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

        lang = _lang_for(message)
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
    lang = _lang_for(message)
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
