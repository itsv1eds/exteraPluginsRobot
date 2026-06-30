
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.cache import get_admins
from bot.context import get_lang
from bot.formatting import plain_html
from bot.helpers import answer, extract_html_text
from bot.keyboards import _btn
from bot.states import PosterFlow
from bot.texts import t
from bot.services import poster

logger = logging.getLogger(__name__)
router = Router(name="poster-flow")

TZ_DISPLAY = timezone(timedelta(hours=5))
_STATUS_EMOJI = {"scheduled": "", "sent": "✅", "failed": "", "canceled": ""}


def _lang(target) -> str:
    user = getattr(target, "from_user", None)
    return get_lang(getattr(user, "id", None))


def _home_kb(channels: list, lang: str) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        title = str(ch.get("title") or ch.get("chat_id"))
        rows.append([_btn(f"{title}", callback_data=f"pstr:ch:{ch.get('chat_id')}", icon="send")])
    rows.append([_btn(t("poster_btn_add_channel", lang), callback_data="pstr:ch:add", icon="add", style="success")])
    rows.append([_btn(t("poster_btn_my_posts", lang), callback_data="pstr:posts", icon="clock")])
    rows.append([_btn(t("btn_back", lang), callback_data="profile", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _channel_kb(chat_id: int, lang: str, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [[_btn(t("poster_btn_new_post", lang), callback_data=f"pstr:new:{chat_id}", icon="send", style="success")]]
    if is_admin:
        rows.append([_btn(t("poster_btn_updated_plugins", lang), callback_data=f"pstr:upd:{chat_id}", icon="updates")])
    rows.append([_btn(t("poster_btn_remove_channel", lang), callback_data=f"pstr:ch:{chat_id}:del", icon="delete", style="danger")])
    rows.append([_btn(t("btn_back", lang), callback_data="pstr:home", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _skip_kb(step: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("poster_btn_skip", lang), callback_data=f"pstr:skip:{step}", icon="forward")],
        [_btn(t("btn_cancel", lang), callback_data="pstr:home", style="danger", icon="back")],
    ])


def _time_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("poster_time_1h", lang), callback_data="pstr:time:60"),
            _btn(t("poster_time_3h", lang), callback_data="pstr:time:180"),
            _btn(t("poster_time_24h", lang), callback_data="pstr:time:1440"),
        ],
        [_btn(t("btn_cancel", lang), callback_data="pstr:home", style="danger", icon="back")],
    ])


def _posts_kb(posts: list, lang: str) -> InlineKeyboardMarkup:
    rows = []
    for p in posts:
        if p.get("status") != "scheduled":
            continue
        rows.append([_btn(
            f"{p.get('run_at', '')[:16].replace('T', ' ')}",
            callback_data=f"pstr:cancel:{p.get('id')}", icon="delete",
        )])
    rows.append([_btn(t("btn_back", lang), callback_data="pstr:home", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_home(target, state: FSMContext) -> None:
    lang = _lang(target)
    uid = target.from_user.id if target.from_user else 0
    channels = poster.list_channels(uid)
    text = t("poster_home" if channels else "poster_home_empty", lang)
    await state.set_state(None)
    await answer(target, text, _home_kb(channels, lang))


@router.callback_query(F.data == "pstr:home")
async def on_home(cb: CallbackQuery, state: FSMContext) -> None:
    await render_home(cb, state)
    await cb.answer()


@router.callback_query(F.data == "pstr:ch:add")
async def on_channel_add(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PosterFlow.entering_channel_ref)
    await answer(cb, t("poster_add_channel_prompt", _lang(cb)), _skip_kb("noop", _lang(cb)))
    await cb.answer()


def _admin_label(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    return (getattr(user, "full_name", None) or str(getattr(user, "id", "?")))


async def _verify_channel(bot, chat_ref, user_id: int):
    try:
        chat = await bot.get_chat(chat_ref)
    except Exception:
        return None, None, "poster_err_not_found"
    if getattr(chat, "type", None) != "channel":
        return None, None, "poster_err_not_channel"
    try:
        me = await bot.get_me()
        bot_member = await bot.get_chat_member(chat.id, me.id)
    except Exception:
        return None, None, "poster_err_bot_not_admin"
    if getattr(bot_member, "status", None) not in {"administrator", "creator"} or not getattr(bot_member, "can_post_messages", False):
        return None, None, "poster_err_bot_not_admin"
    try:
        chat_admins = await bot.get_chat_administrators(chat.id)
    except Exception:
        return None, None, "poster_err_bot_not_admin"

    admin_ids: list[int] = []
    admin_labels: list[str] = []
    requester_is_admin = False
    for member in chat_admins:
        member_user = getattr(member, "user", None)
        if not member_user or getattr(member_user, "is_bot", False):
            continue
        admin_ids.append(member_user.id)
        admin_labels.append(_admin_label(member_user))
        if member_user.id == user_id:
            requester_is_admin = True
    if not requester_is_admin:
        return None, None, "poster_err_user_not_admin"
    return chat, {"ids": admin_ids, "labels": admin_labels}, None


@router.message(PosterFlow.entering_channel_ref)
async def on_channel_ref(message: Message, state: FSMContext) -> None:
    lang = _lang(message)
    chat_ref = None
    if message.forward_from_chat is not None:
        chat_ref = message.forward_from_chat.id
    else:
        raw = (message.text or "").strip()
        if raw.lstrip("-").isdigit():
            chat_ref = int(raw)
        elif raw:
            chat_ref = raw if raw.startswith("@") else f"@{raw}"
    if chat_ref is None:
        await message.answer(t("poster_add_channel_prompt", lang), parse_mode=ParseMode.HTML)
        return

    chat, admins, err = await _verify_channel(message.bot, chat_ref, message.from_user.id)
    if err:
        await message.answer(t(err, lang), parse_mode=ParseMode.HTML)
        return
    poster.upsert_channel(
        chat.id, getattr(chat, "title", "") or str(chat.id),
        getattr(chat, "username", "") or "", message.from_user.id,
        admin_ids=admins["ids"], admin_labels=admins["labels"],
    )
    access = ", ".join(plain_html(x) for x in admins["labels"]) or "—"
    await message.answer(
        t("poster_channel_added", lang, title=plain_html(getattr(chat, "title", "") or chat.id))
        + "\n\n" + t("poster_channel_access", lang, admins=access),
        parse_mode=ParseMode.HTML,
    )
    await render_home(message, state)


@router.callback_query(F.data.regexp(r"^pstr:ch:-?\d+$"))
async def on_channel_detail(cb: CallbackQuery, state: FSMContext) -> None:
    chat_id = int(cb.data.split(":")[2])
    channel = poster.get_channel(chat_id)
    if not poster.can_manage(channel, cb.from_user.id):
        await cb.answer(t("poster_err_not_found", _lang(cb)), show_alert=True)
        return
    is_admin = cb.from_user.id in get_admins()
    access = ", ".join(plain_html(x) for x in (channel.get("admin_labels") or [])) or "—"
    text = t("poster_channel_detail", _lang(cb),
             title=plain_html(channel.get("title")), username=plain_html(channel.get("username") or "—"))
    text += "\n\n" + t("poster_channel_access", _lang(cb), admins=access)
    await answer(cb, text, _channel_kb(chat_id, _lang(cb), is_admin=is_admin))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pstr:ch:-?\d+:del$"))
async def on_channel_remove(cb: CallbackQuery, state: FSMContext) -> None:
    chat_id = int(cb.data.split(":")[2])
    poster.remove_channel(chat_id, cb.from_user.id)
    await render_home(cb, state)
    await cb.answer(t("poster_channel_removed", _lang(cb)), show_alert=True)


@router.callback_query(F.data.regexp(r"^pstr:new:-?\d+$"))
async def on_new_post(cb: CallbackQuery, state: FSMContext) -> None:
    chat_id = int(cb.data.split(":")[2])
    channel = poster.get_channel(chat_id)
    if not poster.can_manage(channel, cb.from_user.id):
        await cb.answer(t("poster_err_not_found", _lang(cb)), show_alert=True)
        return
    await state.set_state(PosterFlow.composing_text)
    await state.update_data(poster_chat_id=chat_id, poster_media=[], poster_buttons=[])
    await answer(cb, t("poster_compose_text", _lang(cb)), _skip_kb("noop", _lang(cb)))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pstr:upd:-?\d+$"))
async def on_updated_plugins_prefill(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in get_admins():
        await cb.answer(t("admin_denied", _lang(cb)), show_alert=True)
        return
    chat_id = int(cb.data.split(":")[2])
    channel = poster.get_channel(chat_id)
    if not poster.can_manage(channel, cb.from_user.id):
        await cb.answer(t("poster_err_not_found", _lang(cb)), show_alert=True)
        return
    text = poster.build_updated_plugins_text()
    if not text:
        await cb.answer(t("poster_updated_empty", _lang(cb)), show_alert=True)
        return
    await state.update_data(poster_chat_id=chat_id, poster_media=[], poster_buttons=[], poster_html=text)
    await state.set_state(PosterFlow.composing_media)
    await answer(cb, t("poster_compose_media", _lang(cb)), _skip_kb("media", _lang(cb)))
    await cb.answer()


async def _editing(state: FSMContext) -> bool:
    data = await state.get_data()
    return bool(data.get("from_preview"))


@router.message(PosterFlow.composing_text)
async def on_compose_text(message: Message, state: FSMContext) -> None:
    html = extract_html_text(message).strip()
    if not html:
        await message.answer(t("need_text", _lang(message)))
        return
    await state.update_data(poster_html=html)
    if await _editing(state):
        await state.update_data(from_preview=False)
        await _render_preview(message, state)
        return
    await state.set_state(PosterFlow.composing_media)
    await answer(message, t("poster_compose_media", _lang(message)), _skip_kb("media", _lang(message)))


@router.callback_query(PosterFlow.composing_media, F.data == "pstr:skip:media")
async def on_skip_media(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(poster_media=[])
    if await _editing(state):
        await state.update_data(from_preview=False)
        await _render_preview(cb, state)
        await cb.answer()
        return
    await state.set_state(PosterFlow.composing_buttons)
    await answer(cb, t("poster_compose_buttons", _lang(cb)), _skip_kb("buttons", _lang(cb)))
    await cb.answer()


@router.message(PosterFlow.composing_media)
async def on_compose_media(message: Message, state: FSMContext) -> None:
    media = None
    if message.photo:
        media = {"type": "photo", "file_id": message.photo[-1].file_id}
    elif message.video:
        media = {"type": "video", "file_id": message.video.file_id}
    if not media:
        await message.answer(t("poster_err_not_media", _lang(message)))
        return
    await state.update_data(poster_media=[media])
    if await _editing(state):
        await state.update_data(from_preview=False)
        await _render_preview(message, state)
        return
    await state.set_state(PosterFlow.composing_buttons)
    await answer(message, t("poster_compose_buttons", _lang(message)), _skip_kb("buttons", _lang(message)))


_BUTTON_STYLE_ALIASES = {"red": "danger", "green": "success"}
_BUTTON_STYLE_RE = re.compile(r"::([a-zA-Z]+)\s*$")


def _split_button_style(url: str):
    match = _BUTTON_STYLE_RE.search(url)
    if not match:
        return url, None
    name = match.group(1).lower()
    return url[: match.start()].strip(), _BUTTON_STYLE_ALIASES.get(name, name)


def _parse_buttons(text: str) -> list:
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        sep = "|" if "|" in line else (" - " if " - " in line else None)
        if not sep:
            continue
        label, _, url = line.partition(sep)
        label, url = label.strip(), url.strip()
        url, style = _split_button_style(url)
        if label and (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
            btn = {"text": label, "url": url}
            if style:
                btn["style"] = style
            rows.append([btn])
    return rows


def _preview_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("poster_btn_edit_text", lang), callback_data="pstr:edit:text", icon="edit")],
        [
            _btn(t("poster_btn_edit_media", lang), callback_data="pstr:edit:media", icon="art"),
            _btn(t("poster_btn_edit_buttons", lang), callback_data="pstr:edit:buttons", icon="link"),
        ],
        [_btn(t("poster_btn_schedule", lang), callback_data="pstr:schedule", icon="clock", style="success")],
        [_btn(t("btn_cancel", lang), callback_data="pstr:home", style="danger", icon="back")],
    ])


async def _render_preview(target, state: FSMContext) -> None:
    lang = _lang(target)
    data = await state.get_data()
    await state.set_state(PosterFlow.previewing)
    body = poster.normalize_custom_emoji(data.get("poster_html") or "") or "—"
    media = data.get("poster_media") or []
    buttons = data.get("poster_buttons") or []
    if media:
        key = "poster_preview_media_photo" if media[0].get("type") == "photo" else "poster_preview_media_video"
        media_line = t(key, lang)
    else:
        media_line = t("poster_preview_media_none", lang)
    btn_labels = " | ".join(plain_html(b[0].get("text", "")) for b in buttons if b) or "—"
    text = (
        t("poster_preview_header", lang) + "\n\n" + body + "\n\n"
        + t("poster_preview_meta", lang, media=media_line, buttons=btn_labels)
    )
    await answer(target, text, _preview_kb(lang))


@router.callback_query(PosterFlow.composing_buttons, F.data == "pstr:skip:buttons")
async def on_skip_buttons(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(poster_buttons=[], from_preview=False)
    await _render_preview(cb, state)
    await cb.answer()


@router.message(PosterFlow.composing_buttons)
async def on_compose_buttons(message: Message, state: FSMContext) -> None:
    buttons = _parse_buttons(message.text or "")
    if not buttons:
        await message.answer(t("poster_err_bad_buttons", _lang(message)))
        return
    await state.update_data(poster_buttons=buttons, from_preview=False)
    await _render_preview(message, state)


@router.callback_query(PosterFlow.previewing, F.data == "pstr:edit:text")
async def on_edit_text(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(from_preview=True)
    await state.set_state(PosterFlow.composing_text)
    await answer(cb, t("poster_compose_text", _lang(cb)), _skip_kb("noop", _lang(cb)))
    await cb.answer()


@router.callback_query(PosterFlow.previewing, F.data == "pstr:edit:media")
async def on_edit_media(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(from_preview=True)
    await state.set_state(PosterFlow.composing_media)
    await answer(cb, t("poster_compose_media", _lang(cb)), _skip_kb("media", _lang(cb)))
    await cb.answer()


@router.callback_query(PosterFlow.previewing, F.data == "pstr:edit:buttons")
async def on_edit_buttons(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(from_preview=True)
    await state.set_state(PosterFlow.composing_buttons)
    await answer(cb, t("poster_compose_buttons", _lang(cb)), _skip_kb("buttons", _lang(cb)))
    await cb.answer()


@router.callback_query(PosterFlow.previewing, F.data == "pstr:schedule")
async def on_preview_schedule(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PosterFlow.composing_time)
    await answer(cb, t("poster_compose_time", _lang(cb)), _time_kb(_lang(cb)))
    await cb.answer()


async def _finalize(target, state: FSMContext, run_at_utc: datetime) -> None:
    data = await state.get_data()
    chat_id = data.get("poster_chat_id")
    channel = poster.get_channel(chat_id) if chat_id else None
    if not poster.can_manage(channel, target.from_user.id):
        await answer(target, t("poster_err_not_found", _lang(target)), None)
        return
    content = {
        "html_text": data.get("poster_html") or "",
        "media": data.get("poster_media") or [],
        "buttons": data.get("poster_buttons") or [],
    }
    poster.add_post(target.from_user.id, chat_id, run_at_utc.isoformat(), content, kind="manual")
    local = run_at_utc.astimezone(TZ_DISPLAY).strftime("%d.%m.%Y %H:%M")
    await answer(target, t("poster_scheduled", _lang(target), datetime=local), None)
    await render_home(target, state)


@router.callback_query(PosterFlow.composing_time, F.data.regexp(r"^pstr:time:\d+$"))
async def on_time_preset(cb: CallbackQuery, state: FSMContext) -> None:
    minutes = int(cb.data.split(":")[2])
    await _finalize(cb, state, datetime.now(timezone.utc) + timedelta(minutes=minutes))
    await cb.answer()


@router.message(PosterFlow.composing_time)
async def on_time_manual(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        naive = datetime.strptime(raw, "%d.%m.%Y %H:%M")
    except ValueError:
        await message.answer(t("poster_err_bad_time", _lang(message)))
        return
    run_at_utc = naive.replace(tzinfo=TZ_DISPLAY).astimezone(timezone.utc)
    if run_at_utc <= datetime.now(timezone.utc):
        await message.answer(t("poster_err_past_time", _lang(message)))
        return
    await _finalize(message, state, run_at_utc)


@router.callback_query(F.data == "pstr:posts")
async def on_my_posts(cb: CallbackQuery, state: FSMContext) -> None:
    posts = poster.list_user_posts(cb.from_user.id, statuses=("scheduled",))
    text = t("poster_my_posts" if posts else "poster_my_posts_empty", _lang(cb))
    await answer(cb, text, _posts_kb(posts, _lang(cb)))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pstr:cancel:\w+$"))
async def on_cancel_post(cb: CallbackQuery, state: FSMContext) -> None:
    post_id = cb.data.split(":")[2]
    poster.cancel_post(post_id, cb.from_user.id)
    posts = poster.list_user_posts(cb.from_user.id, statuses=("scheduled",))
    text = t("poster_my_posts" if posts else "poster_my_posts_empty", _lang(cb))
    await answer(cb, text, _posts_kb(posts, _lang(cb)))
    await cb.answer(t("poster_post_canceled", _lang(cb)), show_alert=True)
