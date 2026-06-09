import html
import time
import math
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedDocument,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputTextMessageContent,
    Message,
)
from aiogram.exceptions import TelegramBadRequest

from bot.cache import get_admins_super, get_categories, get_icons
from bot.constants import PAGE_SIZE
from bot.context import get_language, get_lang
from bot.callback_tokens import decode_slug, encode_slug
from bot.formatting import quote_html
from bot.helpers import answer, link_preview_options, strip_html
from bot.icons import CATEGORY_FALLBACKS, CATEGORY_ICONS, ICONS
from bot.menu_owner import MenuOwnerMiddleware
from bot.keyboards import (
    _btn,
    catalog_main_kb,
    page_picker_kb,
    paginated_list_kb,
    plugin_detail_kb,
    profile_kb,
    broadcast_kb,
    search_kb,
    moderation_inline_vote_url_kb,
)
from bot.states import UserFlow
from bot.texts import t
from catalog import (
    SOURCE_ALL,
    SOURCE_OFFICIAL,
    find_icon_by_slug,
    find_plugin_by_slug,
    find_user_icons,
    find_user_plugins,
    is_external_plugin,
    list_plugin_sources,
    list_plugins_by_category,
    plugin_source_type,
    search_icons,
    search_plugins,
)
from subscription_store import (
    add_subscription,
    is_subscribed,
    list_subscriptions,
    remove_subscription,
)
from subscription_store import ALL_SUBSCRIPTION_KEY
from user_store import (
    has_paid_broadcast_disable,
    is_broadcast_enabled,
)
from storage import load_stenka, save_stenka
from storage import load_joinly
from storage import save_joinly
from request_store import get_request_by_plugin_id, get_user_requests, update_request_payload
from bot.services.moderation import forum_text_with_votes, vote_counts

router = Router(name="catalog-flow")
router.callback_query.middleware(MenuOwnerMiddleware())

BOT_USERNAME = "exteraPluginsRobot"
GITHUB_IMG_BASE_URL = "https://github.com/itsv1eds/exteraPluginsRobot/blob/main/img"
SOURCE_PAGE_SIZE = 8


def _github_img_url(image_key: str) -> str:
    return f"{GITHUB_IMG_BASE_URL}/{image_key}.png?raw=true"


def _plugin_category_preview_url(category_key: str) -> str:
    known = {str(category.get("key")) for category in get_categories()}
    image_key = f"cat_{category_key}" if category_key in known else "cat_all"
    return _github_img_url(image_key)


def _with_hidden_preview_link(text: str, url: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">&#8203;</a>{text}'


def _plugin_source_label(entry: Dict[str, Any]) -> str:
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    if plugin_source_type(entry) == "external":
        username = str(source.get("username") or "").strip()
        title = str(source.get("title") or source.get("id") or username or "External").strip()
        return f"@{username}" if username else title
    return "@exteraPluginsSup"


def _source_label(source: Dict[str, Any], lang: str) -> str:
    key = str(source.get("key") or "")
    if key == SOURCE_ALL:
        return t("catalog_source_all", lang)
    return str(source.get("label") or key).strip() or key


def _source_label_for_filter(source_filter: str, lang: str) -> str:
    source_filter = (source_filter or SOURCE_ALL).strip().lower()
    for source in list_plugin_sources():
        if str(source.get("key") or "").strip().lower() == source_filter:
            return _source_label(source, lang)
    return t("catalog_source_all", lang)


async def _catalog_source_filter(state: FSMContext) -> str:
    data = await state.get_data()
    return str(data.get("catalog_source_filter") or SOURCE_ALL).strip().lower() or SOURCE_ALL


def _catalog_title(lang: str, source_label: str) -> str:
    return f"{t('catalog_title', lang)}\n{t('catalog_source_current', lang, source=html.escape(source_label))}"


def _source_list_kb(sources: list[Dict[str, Any]], selected: str, page: int, total_pages: int, lang: str) -> InlineKeyboardMarkup:
    selected = (selected or SOURCE_ALL).strip().lower()
    page = max(0, min(page, max(total_pages, 1) - 1))
    start = page * SOURCE_PAGE_SIZE
    page_sources = sources[start : start + SOURCE_PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []

    for idx, source in enumerate(page_sources, start=start):
        key = str(source.get("key") or SOURCE_ALL).strip().lower()
        count = int(source.get("count") or 0)
        label = _source_label(source, lang)
        text = f"{label} · {t('catalog_source_count', lang, count=count)}"
        icon = "yes" if key == selected else ("catalog" if key == SOURCE_OFFICIAL else "profile")
        rows.append([
            _btn(
                text,
                callback_data=f"catalog:source:set:{idx}",
                icon=icon,
                style="success" if key == selected else None,
            )
        ])

    if total_pages > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("btn_back", lang) if page > 0 else " ",
                    callback_data=f"catalog:source:{page - 1}" if page > 0 else "page:noop",
                ),
                InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="page:noop"),
                InlineKeyboardButton(
                    text=t("btn_forward", lang) if page < total_pages - 1 else " ",
                    callback_data=f"catalog:source:{page + 1}" if page < total_pages - 1 else "page:noop",
                ),
            ]
        )

    rows.append([_btn(t("btn_back", lang), callback_data="catalog", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class _CaptionHTMLTruncator(HTMLParser):
    _simple_tags = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "tg-spoiler"}

    def __init__(self, max_visible: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_visible = max_visible
        self.visible = 0
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.truncated = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.truncated:
            return
        tag = tag.lower()
        attrs_map = {str(k).lower(): v for k, v in attrs}
        if tag in self._simple_tags:
            if tag == "code":
                class_name = str(attrs_map.get("class") or "")
                if class_name.startswith("language-"):
                    self.parts.append(f'<code class="{html.escape(class_name, quote=True)}">')
                else:
                    self.parts.append("<code>")
            else:
                self.parts.append(f"<{tag}>")
            self.stack.append(tag)
            return
        if tag == "span" and attrs_map.get("class") == "tg-spoiler":
            self.parts.append('<span class="tg-spoiler">')
            self.stack.append(tag)
            return
        if tag == "blockquote":
            expandable = " expandable" if "expandable" in attrs_map else ""
            self.parts.append(f"<blockquote{expandable}>")
            self.stack.append(tag)
            return
        if tag == "a":
            href = str(attrs_map.get("href") or "").strip()
            if href.startswith(("http://", "https://", "tg://", "mailto:")):
                self.parts.append(f'<a href="{html.escape(href, quote=True)}">')
                self.stack.append(tag)
            return
        if tag == "tg-emoji":
            emoji_id = str(attrs_map.get("emoji-id") or "").strip()
            if emoji_id.isdigit():
                self.parts.append(f'<tg-emoji emoji-id="{emoji_id}">')
                self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag not in self.stack:
            return
        while self.stack:
            opened = self.stack.pop()
            self.parts.append(f"</{opened}>")
            if opened == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.truncated or not data:
            return
        left = self.max_visible - self.visible
        if left <= 0:
            self.truncated = True
            return
        chunk = data[:left]
        self.parts.append(html.escape(chunk, quote=False))
        self.visible += len(chunk)
        if len(data) > left:
            self.truncated = True

    def close_open_tags(self) -> None:
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")


def _truncate_caption_html(value: str, max_visible: int) -> tuple[str, bool]:
    parser = _CaptionHTMLTruncator(max_visible)
    parser.feed(value)
    parser.close_open_tags()
    return "".join(parser.parts).strip(), parser.truncated


def _request_inline_file(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    plugin = payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}
    icon = payload.get("icon") if isinstance(payload.get("icon"), dict) else {}
    item = plugin or icon
    if not isinstance(item, dict):
        return None
    file_id = str(item.get("file_id") or payload.get("moderation_file_id") or "").strip()
    if not file_id:
        return None
    file_path = str(item.get("file_path") or "").strip()
    name = (
        str(item.get("name") or "").strip()
        or str(item.get("id") or "").strip()
        or Path(file_path).name
        or "request file"
    )
    kind = "icon" if icon else "plugin"
    return file_id, name, kind


def _request_inline_file_caption(request_id: str, file_kind: str, message_text: str) -> str:
    text = str(message_text or "").strip()
    if len(strip_html(text)) <= 1024:
        return text

    suffix = "\n\nПолный текст есть в отдельной карточке заявки."
    budget = max(200, 1024 - len(strip_html(suffix)) - 3)
    body, truncated = _truncate_caption_html(text, budget)
    if not truncated:
        return body
    return f"{body}...{suffix}"


def _toggle_label(value: bool, lang: str) -> str:
    if lang == "en":
        return "on" if value else "off"
    return "вкл" if value else "выкл"


def _stenka_db() -> dict[str, Any]:
    db = load_stenka()
    if not isinstance(db, dict):
        db = {}
    if "counter" not in db or not isinstance(db.get("counter"), int):
        db["counter"] = 0
    if "walls" not in db or not isinstance(db.get("walls"), dict):
        db["walls"] = {}
    return db


def _stenka_next_id() -> str:
    db = _stenka_db()
    db["counter"] = int(db.get("counter") or 0) + 1
    wall_id = f"stenka{db['counter']}"
    walls = db.setdefault("walls", {})
    if isinstance(walls, dict):
        walls.setdefault(wall_id, {"tags": [], "users": {}, "created_at": int(time.time())})
    save_stenka(db)
    return wall_id


def _stenka_render_text(wall_id: str) -> str:
    db = _stenka_db()
    walls = db.get("walls") if isinstance(db.get("walls"), dict) else {}
    wall = walls.get(wall_id) if isinstance(walls, dict) else None
    tags: list[str] = []
    if isinstance(wall, dict):
        raw_tags = wall.get("tags")
        if isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    base = t("stenka_title", "ru")
    if tags:
        return f"{html.escape(base)}\n\n{html.escape(', '.join(tags))}"
    return f"{html.escape(base)}"


def _stenka_kb(wall_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("stenka_btn_leave_tag", lang), callback_data=f"stenka:tag:{wall_id}")]]
    )


def _stenka_kb_url(wall_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    url = f"https://t.me/{BOT_USERNAME}?start=stenka_{wall_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("stenka_btn_leave_tag", lang), url=url)]]
    )


@router.callback_query(F.data.startswith("stenka:tag:"))
async def on_stenka_tag(cb: CallbackQuery) -> None:
    if not cb.from_user:
        await cb.answer()
        return

    lang = get_lang(cb.from_user.id)

    wall_id = cb.data.split(":", 2)[2]

    db = _stenka_db()
    walls = db.get("walls") if isinstance(db.get("walls"), dict) else {}
    wall = walls.get(wall_id) if isinstance(walls, dict) else None
    if not isinstance(wall, dict):
        await cb.answer(t("stenka_err_not_found", lang), show_alert=True)
        return
    if cb.message:
        wall["chat_id"] = cb.message.chat.id
        wall["message_id"] = cb.message.message_id
    if cb.inline_message_id:
        wall["inline_message_id"] = cb.inline_message_id
    save_stenka(db)

    try:
        if cb.message:
            await cb.message.edit_reply_markup(reply_markup=_stenka_kb_url(wall_id, lang=lang))
        elif cb.inline_message_id:
            await cb.bot.edit_message_reply_markup(inline_message_id=cb.inline_message_id, reply_markup=_stenka_kb_url(wall_id, lang=lang))
    except Exception:
        pass

    await cb.answer(t("stenka_alert_open_bot", lang), show_alert=True)


def build_plugin_preview(entry: Dict[str, Any], lang: str) -> str:
    locale = entry.get(lang) or entry.get("ru") or entry.get("en") or {}
    authors = entry.get("authors", {})
    raw_blocks = entry.get("raw_blocks", {}) or {}
    raw_locale = raw_blocks.get(lang) or raw_blocks.get("ru") or {}
    author_channels = entry.get("author_channels", {}) or {}

    name_value = locale.get("name") or entry.get("slug", "?")
    author = (
        (raw_locale.get("author") if isinstance(raw_locale, dict) else None)
        or authors.get(lang)
        or authors.get("ru")
        or (raw_locale.get("author_channel") if isinstance(raw_locale, dict) else None)
        or t("catalog_inline_no_description", lang)
    )
    author_channel = (
        (raw_locale.get("author_channel") if isinstance(raw_locale, dict) else None)
        or author_channels.get(lang)
        or author_channels.get("ru")
    )
    link = entry.get("channel_message", {}).get("link")
    count = entry.get("count")
    if count is None:
        count = entry.get("icons_count") or entry.get("icon_count")
    if count is None:
        icons = entry.get("icons")
        if isinstance(icons, (list, dict)):
            count = len(icons)

    name = html.escape(str(name_value))
    author_safe = html.escape(str(author))
    lines = [f"<b>{name}</b> by {author_safe}"]

    description = (locale.get("description") or "").strip()
    if description:
        lines.append(quote_html(description, expandable=True))

    min_version = (entry.get("min_version") or "").strip()
    if min_version:
        lines.append(f"<b>{t('catalog_field_min_version', lang)}:</b> {html.escape(min_version)}")

    if author_channel:
        lines.append(f"<b>{t('catalog_field_author_channel', lang)}:</b> {html.escape(str(author_channel))}")
    if entry.get("source"):
        lines.append(f"<b>{t('catalog_field_source', lang)}:</b> {html.escape(_plugin_source_label(entry))}")
    if count is not None:
        lines.append(f"<b>{t('catalog_field_icons', lang)}:</b> {count}")

    return "\n".join(lines)


def build_inline_preview(entry: Dict[str, Any], lang: str, kind: str = "plugin") -> str:
    locale = entry.get(lang) or entry.get("ru") or entry.get("en") or {}
    authors = entry.get("authors", {})
    raw_blocks = entry.get("raw_blocks", {}) or {}
    raw_locale = raw_blocks.get(lang) or raw_blocks.get("ru") or {}

    name = html.escape(locale.get("name") or entry.get("slug", "?"))
    author_value = (
        (raw_locale.get("author") if isinstance(raw_locale, dict) else None)
        or authors.get(lang)
        or authors.get("ru")
        or (raw_locale.get("author_channel") if isinstance(raw_locale, dict) else None)
        or t("catalog_inline_no_description", lang)
    )
    author = html.escape(author_value)
    count = entry.get("count")
    if count is None:
        count = entry.get("icons_count") or entry.get("icon_count")
    if count is None:
        icons = entry.get("icons")
        if isinstance(icons, (list, dict)):
            count = len(icons)
    if kind == "plugin":
        category = str(entry.get("category") or "").strip()
        icon_id = CATEGORY_ICONS.get(category) or ICONS["plugin"]
        fallback = CATEGORY_FALLBACKS.get(category, "🧩")
        lines = [f'<tg-emoji emoji-id="{icon_id}">{fallback}</tg-emoji> <b>{name}</b> by <code>{author}</code>']
    else:
        lines = [t("catalog_inline_header", lang, name=name, author=author)]
    if count is not None:
        lines.append(f"{t('catalog_field_icons', lang)}: {count}")

    if kind == "plugin":
        description = (locale.get("description") or "").strip()
        if description:
            lines.append(quote_html(description, expandable=True))

        min_version = (entry.get("min_version") or "").strip()
        if min_version:
            lines.append(f"<b>{t('catalog_field_min_version', lang)}:</b> {html.escape(min_version)}")
        if entry.get("source"):
            lines.append(f"<b>{t('catalog_field_source', lang)}:</b> {html.escape(_plugin_source_label(entry))}")
    return "\n".join(lines)


@router.callback_query(F.data == "catalog")
async def on_catalog(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    source_filter = await _catalog_source_filter(state)
    source_label = _source_label_for_filter(source_filter, lang)
    await answer(
        cb,
        _catalog_title(lang, source_label),
        catalog_main_kb(get_categories(), lang, source_label=source_label),
        "catalog",
    )
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


async def _render_catalog_sources(target: CallbackQuery | Message, state: FSMContext, page: int = 0) -> None:
    lang = await get_language(target, state)
    selected = await _catalog_source_filter(state)
    sources = list_plugin_sources()
    total_pages = max(1, math.ceil(len(sources) / SOURCE_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    selected_label = _source_label_for_filter(selected, lang)
    text = (
        f"{t('catalog_source_title', lang)}\n"
        f"{t('catalog_source_current', lang, source=html.escape(selected_label))}\n\n"
        f"{t('catalog_source_hint', lang)}"
    )
    await answer(target, text, _source_list_kb(sources, selected, page, total_pages, lang), "catalog")


@router.callback_query(F.data.startswith("catalog:source:"))
async def on_catalog_source(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    lang = await get_language(cb, state)

    if len(parts) >= 4 and parts[2] == "set":
        try:
            idx = int(parts[3])
        except ValueError:
            idx = 0
        sources = list_plugin_sources()
        if not 0 <= idx < len(sources):
            try:
                await cb.answer(t("not_found", lang), show_alert=True)
            except TelegramBadRequest:
                pass
            return
        key = str(sources[idx].get("key") or SOURCE_ALL).strip().lower() or SOURCE_ALL
        await state.update_data(catalog_source_filter=key)
        page = idx // SOURCE_PAGE_SIZE
        try:
            await cb.answer(_source_label(sources[idx], lang))
        except TelegramBadRequest:
            pass
        await _render_catalog_sources(cb, state, page=page)
        return

    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        page = 0
    await _render_catalog_sources(cb, state, page=page)
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


async def _show_broadcast_settings(target: CallbackQuery | Message, state: FSMContext) -> None:
    lang = await get_language(target, state)
    user = target.from_user
    if not user:
        if isinstance(target, CallbackQuery):
            await target.answer()
        return

    paid = has_paid_broadcast_disable(user.id)
    enabled = is_broadcast_enabled(user.id)

    text = f"{t('broadcast_title', lang)}"
    if paid:
        text += f"\n{t('broadcast_paid_note', lang)}"

    await answer(target, text, broadcast_kb(lang, enabled=enabled, paid=paid, back="profile"), "profile")
    if isinstance(target, CallbackQuery):
        try:
            await target.answer()
        except TelegramBadRequest:
            pass


@router.callback_query(F.data == "profile:broadcast")
async def on_profile_broadcast(cb: CallbackQuery, state: FSMContext) -> None:
    await _show_broadcast_settings(cb, state)


@router.callback_query(F.data == "profile:broadcast:toggle")
async def on_profile_broadcast_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    user = cb.from_user
    if not user:
        await cb.answer()
        return

    from user_store import set_broadcast_enabled

    enabled = is_broadcast_enabled(user.id)
    set_broadcast_enabled(user.id, not enabled)
    await _show_broadcast_settings(cb, state)
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "profile:broadcast:pay")
async def on_profile_broadcast_pay(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    user = cb.from_user
    if not user:
        await cb.answer()
        return

    # Telegram Stars payments use currency XTR and provider_token can be empty.
    from aiogram.types import LabeledPrice

    await cb.bot.send_invoice(
        chat_id=user.id,
        title=t("broadcast_invoice_title", lang),
        description=t("broadcast_invoice_description", lang),
        payload="simple_payment:broadcast_disable",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Disable broadcast", amount=50)],
    )
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "page:noop")
async def on_page_noop(cb: CallbackQuery) -> None:
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("page:picker|"))
async def on_page_picker(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    payload = cb.data.split("|")
    if len(payload) != 4:
        try:
            await cb.answer()
        except TelegramBadRequest:
            pass
        return

    nav_prefix = payload[1]
    try:
        page = int(payload[2])
        total_pages = int(payload[3])
    except ValueError:
        try:
            await cb.answer()
        except TelegramBadRequest:
            pass
        return

    if not cb.message:
        try:
            await cb.answer()
        except TelegramBadRequest:
            pass
        return

    await cb.message.edit_reply_markup(
        reply_markup=page_picker_kb(
            nav_prefix=nav_prefix,
            current_page=page,
            total_pages=total_pages,
            lang=lang,
        )
    )
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("cat:"))
async def on_catalog_category(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    cat_key = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    lang = await get_language(cb, state)
    source_filter = await _catalog_source_filter(state)
    source_label = _source_label_for_filter(source_filter, lang)

    plugins = list_plugins_by_category(cat_key, source_filter=source_filter)
    total = len(plugins)

    if total == 0:
        try:
            await cb.answer(t("catalog_empty", lang), show_alert=True)
        except TelegramBadRequest:
            pass
        return

    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = plugins[start : start + PAGE_SIZE]

    items = []
    for plugin in page_items:
        slug = plugin.get("slug")
        if not slug:
            continue
        locale = plugin.get(lang) or plugin.get("ru") or {}
        name = locale.get("name") or slug
        items.append((name, f"plugin:{encode_slug(slug)}"))

    if cat_key == "_all":
        title_html = t("all_plugins_title", lang)
    else:
        title_html = t(f"category_{cat_key}_label_msg", lang)

    caption = (
        f"{title_html}\n"
        f"{t('catalog_source_current', lang, source=html.escape(source_label))}\n"
        f"{t('catalog_page', lang, current=page + 1, total=total_pages)}"
    )
    await state.update_data(catalog_back=f"cat:{cat_key}:{page}")

    image_key = "cat_all" if cat_key == "_all" else f"cat_{cat_key}"
    await answer(cb, caption, paginated_list_kb(items, page, total_pages, f"cat:{cat_key}", "catalog", lang=lang), image_key)
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("plugin:"))
async def on_plugin_detail(cb: CallbackQuery, state: FSMContext) -> None:
    slug = decode_slug(cb.data.split(":", 1)[1])
    lang = await get_language(cb, state)

    plugin = find_plugin_by_slug(slug)
    if not plugin:
        try:
            await cb.answer(t("not_found", lang), show_alert=True)
        except TelegramBadRequest:
            pass
        return

    text = build_plugin_preview(plugin, lang)
    link = plugin.get("channel_message", {}).get("link")
    external = is_external_plugin(plugin)
    notify_all_enabled = False if external else (is_subscribed(cb.from_user.id, ALL_SUBSCRIPTION_KEY) if cb.from_user else False)
    subscribed = False if external else (is_subscribed(cb.from_user.id, slug) if cb.from_user else False)
    subscribe_label = t("btn_unsubscribe", lang) if subscribed else t("btn_subscribe", lang)
    data = await state.get_data()
    back = data.get("catalog_back") or "catalog"
    await answer(
        cb,
        text,
        plugin_detail_kb(
            link,
            back,
            lang,
            subscribe_callback=(None if external or notify_all_enabled else f"sub:toggle:{encode_slug(slug)}:catalog"),
            subscribe_label=(None if notify_all_enabled else subscribe_label),
        ),
        "plugins",
    )
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("myplugin:"))
async def on_my_plugin_detail(cb: CallbackQuery, state: FSMContext) -> None:
    slug = decode_slug(cb.data.split(":", 1)[1])
    lang = await get_language(cb, state)

    plugin = find_plugin_by_slug(slug)
    if not plugin:
        try:
            await cb.answer(t("not_found", lang), show_alert=True)
        except TelegramBadRequest:
            pass
        return

    text = build_plugin_preview(plugin, lang)
    link = plugin.get("channel_message", {}).get("link")
    notify_all_enabled = is_subscribed(cb.from_user.id, ALL_SUBSCRIPTION_KEY) if cb.from_user else False
    subscribed = is_subscribed(cb.from_user.id, slug) if cb.from_user else False
    subscribe_label = t("btn_unsubscribe", lang) if subscribed else t("btn_subscribe", lang)
    data = await state.get_data()
    back = data.get("catalog_back") or "my:plugins:0"
    await answer(
        cb,
        text,
        plugin_detail_kb(
            link,
            back,
            lang,
            update_callback=f"profile:update:{encode_slug(slug)}",
            delete_callback=f"profile:delete:{encode_slug(slug)}",
            subscribe_callback=(None if notify_all_enabled else f"sub:toggle:{encode_slug(slug)}:my"),
            subscribe_label=(None if notify_all_enabled else subscribe_label),
        ),
        "profile",
    )
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("sub:toggle:"))
async def on_toggle_subscription(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    parts = cb.data.split(":", 3)
    slug = decode_slug(parts[2])
    source = parts[3] if len(parts) > 3 else "catalog"

    if not cb.from_user:
        try:
            await cb.answer()
        except TelegramBadRequest:
            pass
        return

    if is_subscribed(cb.from_user.id, ALL_SUBSCRIPTION_KEY):
        try:
            await cb.answer(t("btn_notify_all_on", lang), show_alert=True)
        except TelegramBadRequest:
            pass
        await _show_subscriptions(cb, state, page=0)
        return

    if is_subscribed(cb.from_user.id, slug):
        remove_subscription(cb.from_user.id, slug)
        try:
            await cb.answer(t("unsubscribed", lang))
        except TelegramBadRequest:
            pass
    else:
        add_subscription(cb.from_user.id, slug)
        try:
            await cb.answer(t("subscribed", lang))
        except TelegramBadRequest:
            pass

    plugin = find_plugin_by_slug(slug)
    if not plugin:
        return

    data = await state.get_data()
    back = data.get("catalog_back") or ("catalog" if source == "catalog" else "my:plugins:0")
    text = build_plugin_preview(plugin, lang)
    link = plugin.get("channel_message", {}).get("link")
    notify_all_enabled = is_subscribed(cb.from_user.id, ALL_SUBSCRIPTION_KEY)
    subscribed = is_subscribed(cb.from_user.id, slug)
    subscribe_label = t("btn_unsubscribe", lang) if subscribed else t("btn_subscribe", lang)
    await answer(
        cb,
        text,
        plugin_detail_kb(
            link,
            back,
            lang,
            update_callback=f"profile:update:{encode_slug(slug)}" if source == "my" else None,
            delete_callback=f"profile:delete:{encode_slug(slug)}" if source == "my" else None,
            subscribe_callback=(None if notify_all_enabled else f"sub:toggle:{encode_slug(slug)}:{source}"),
            subscribe_label=(None if notify_all_enabled else subscribe_label),
        ),
        "profile" if source == "my" else "plugins",
    )


@router.callback_query(F.data == "search")
async def on_search(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    await state.set_state(UserFlow.searching)
    source_filter = await _catalog_source_filter(state)
    source_label = _source_label_for_filter(source_filter, lang)
    text = f"{t('search_prompt', lang)}\n{t('catalog_source_current', lang, source=html.escape(source_label))}"
    await answer(cb, text, search_kb(lang), "catalog")
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


@router.message(UserFlow.searching)
async def on_search_query(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    query = (message.text or "").strip()

    if not query:
        await message.answer(t("need_text", lang))
        return

    source_filter = await _catalog_source_filter(state)
    results = search_plugins(query, limit=25, source_filter=source_filter)
    await state.set_state(UserFlow.idle)

    if not results:
        await answer(message, t("search_empty", lang), search_kb(lang, True), "catalog")
        return

    slugs: list[str] = []
    for plugin in results:
        slug = plugin.get("slug")
        if slug:
            slugs.append(slug)

    await state.update_data(last_search_query=query, last_search_results=slugs)
    await _render_search_results(message, state, page=0)


async def _render_search_results(target: Message | CallbackQuery, state: FSMContext, page: int) -> None:
    lang = await get_language(target, state)
    data = await state.get_data()
    slugs: list[str] = data.get("last_search_results", [])
    if not slugs:
        await answer(target, t("search_empty", lang), search_kb(lang, True), "catalog")
        return

    total = len(slugs)
    total_pages = math.ceil(total / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_slugs = slugs[start : start + PAGE_SIZE]

    items = []
    for slug in page_slugs:
        plugin = find_plugin_by_slug(slug)
        locale = (plugin.get(lang) if plugin else None) or (plugin.get("ru") if plugin else None) or {}
        name = (locale.get("name") if isinstance(locale, dict) else None) or slug
        items.append((name, f"plugin:{encode_slug(slug)}"))

    source_filter = await _catalog_source_filter(state)
    source_label = _source_label_for_filter(source_filter, lang)
    text = f"{t('search_results', lang, count=total)}\n{t('catalog_source_current', lang, source=html.escape(source_label))}"
    await state.update_data(catalog_back=f"search:{page}")
    await answer(
        target,
        text,
        paginated_list_kb(items, page, total_pages, "search", "catalog", lang=lang),
        "catalog",
    )


@router.callback_query(F.data.startswith("search:"))
async def on_search_page(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":", 1)
    page = 0
    if len(parts) > 1 and parts[1].isdigit():
        page = int(parts[1])
    await _render_search_results(cb, state, page=page)
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("icons:"))
async def on_icons_list(cb: CallbackQuery, state: FSMContext) -> None:
    page = int(cb.data.split(":")[1])
    lang = await get_language(cb, state)

    icons = [icon for icon in get_icons() if icon.get("status") == "published"]
    total = len(icons)

    if total == 0:
        await cb.answer(t("catalog_empty", lang), show_alert=True)
        return

    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = icons[start : start + PAGE_SIZE]

    items = []
    for icon in page_items:
        slug = icon.get("slug")
        if not slug:
            continue
        locale = icon.get(lang) or icon.get("ru") or {}
        name = locale.get("name") or slug
        items.append((name, f"icon:{encode_slug(slug)}"))

    caption = f"{t('icons_title', lang)}\n{t('catalog_page', lang, current=page + 1, total=total_pages)}"
    await state.update_data(catalog_back=f"icons:{page}")
    await answer(cb, caption, paginated_list_kb(items, page, total_pages, "icons", "catalog", lang=lang), "iconpacks")
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("icon:"))
async def on_icon_detail(cb: CallbackQuery, state: FSMContext) -> None:
    slug = decode_slug(cb.data.split(":", 1)[1])
    lang = await get_language(cb, state)

    icon = find_icon_by_slug(slug)
    if not icon:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    text = build_plugin_preview(icon, lang)
    link = icon.get("channel_message", {}).get("link")
    data = await state.get_data()
    back = data.get("catalog_back") or "icons:0"
    await answer(cb, text, plugin_detail_kb(link, back, lang), "iconpacks")
    await cb.answer()


@router.callback_query(F.data == "profile")
async def on_profile(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    user = cb.from_user

    if not user:
        await cb.answer()
        return

    user_plugins = find_user_plugins(user.id, user.username or "")
    user_icons = find_user_icons(user.id, user.username or "")

    pending = []
    for req in get_user_requests(user.id):
        if req.get("status") != "pending":
            continue
        if req.get("type") not in {"new", "update"}:
            continue
        payload = req.get("payload", {})
        submission_type = (payload.get("submission_type") or payload.get("type") or "").strip()
        if submission_type not in {"plugin", "update"} and not payload.get("plugin"):
            continue
        pending.append(req)

    await state.update_data(
        my_plugins=[plugin.get("slug") for plugin in user_plugins],
        my_pending_plugins=[req.get("id") for req in pending if req.get("id")],
        my_icons=[icon.get("slug") for icon in user_icons],
    )

    text = f"{t('profile_title', lang)}\n\n"
    if user.username:
        text += f"@{user.username}\n"
    text += t("profile_stats", lang, plugins=len(user_plugins), icons=len(user_icons))

    if not user_plugins and not user_icons:
        text += f"\n\n{t('profile_empty', lang)}"

    notify_all_enabled = is_subscribed(user.id, ALL_SUBSCRIPTION_KEY)
    await answer(
        cb,
        text,
        profile_kb(lang, bool(user_plugins), bool(user_icons), notify_all_enabled=notify_all_enabled),
        "profile",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("subs:all_toggle:"))
async def on_subscriptions_all_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    user = cb.from_user
    if not user:
        await cb.answer()
        return

    page = int(cb.data.split(":")[2])

    enabled = is_subscribed(user.id, ALL_SUBSCRIPTION_KEY)
    if enabled:
        remove_subscription(user.id, ALL_SUBSCRIPTION_KEY)
        await cb.answer(t("unsubscribed", lang))
    else:
        add_subscription(user.id, ALL_SUBSCRIPTION_KEY)
        await cb.answer(t("subscribed", lang))

    await _show_subscriptions(cb, state, page=page)


@router.callback_query(F.data == "profile:subscriptions")
async def on_profile_subscriptions(cb: CallbackQuery, state: FSMContext) -> None:
    await _show_subscriptions(cb, state, page=0)


@router.callback_query(F.data.startswith("subs:page:"))
async def on_subscriptions_page(cb: CallbackQuery, state: FSMContext) -> None:
    page = int(cb.data.split(":")[2])
    await _show_subscriptions(cb, state, page=page)


async def _show_subscriptions(target: CallbackQuery | Message, state: FSMContext, page: int) -> None:
    lang = await get_language(target, state)
    user = target.from_user
    if not user:
        if isinstance(target, CallbackQuery):
            await target.answer()
        return

    slugs = list_subscriptions(user.id)
    all_enabled = ALL_SUBSCRIPTION_KEY in slugs
    if all_enabled:
        slugs = [ALL_SUBSCRIPTION_KEY]
    if not slugs:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=t("btn_back", lang), callback_data="profile", style="danger")]]
        )
        await answer(target, t("subscriptions_empty", lang), kb, "profile")
        if isinstance(target, CallbackQuery):
            await target.answer()
        return

    total = len(slugs)
    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_slugs = slugs[start : start + PAGE_SIZE]

    items = []
    for slug in page_slugs:
        if slug == ALL_SUBSCRIPTION_KEY:
            continue

        plugin = find_plugin_by_slug(slug)
        locale = plugin.get(lang) if plugin else {}
        name = (locale.get("name") if locale else None) or slug
        items.append((name, f"plugin:{encode_slug(slug)}"))

    caption = (
        f"{t('subscriptions_title', lang)}\n"
        f"{t('subscriptions_hint', lang)}\n"
        f"{t('catalog_page', lang, current=page + 1, total=total_pages)}"
    )
    await state.update_data(catalog_back="profile:subscriptions")

    kb = paginated_list_kb(items, page, total_pages, "subs:page", "profile", lang=lang)
    toggle_label = t("btn_notify_all_on", lang) if all_enabled else t("btn_notify_all_off", lang)
    kb.inline_keyboard.insert(
        0,
        [
            _btn(
                toggle_label,
                callback_data=f"subs:all_toggle:{page}",
                style="success" if all_enabled else "danger",
                icon="bell",
            )
        ],
    )
    await answer(target, caption, kb, "notifications")
    if isinstance(target, CallbackQuery):
        await target.answer()


def _user_joinly_chat_ids(db: dict[str, Any], user_id: int) -> list[int]:
    panel_key = f"PanelMessageId:{user_id}"
    chat_ids: list[int] = []
    for k, v in db.items():
        if not str(k).lstrip("-").isdigit():
            continue
        if not isinstance(v, dict):
            continue
        if int(v.get(panel_key) or 0) <= 0:
            continue
        try:
            chat_ids.append(int(k))
        except Exception:
            continue
    chat_ids.sort()
    return chat_ids


async def _render_profile_joinly(target: CallbackQuery | Message, state: FSMContext) -> None:
    lang = await get_language(target, state)
    user = target.from_user
    if not user:
        if isinstance(target, CallbackQuery):
            await target.answer()
        return

    db = load_joinly()
    if not isinstance(db, dict):
        db = {}

    text = f"{t('joinly_profile_title', lang)}\n\n"
    chat_ids = _user_joinly_chat_ids(db, user.id)
    if not chat_ids:
        text += t("joinly_profile_no_chats", lang)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=t("btn_back", lang), callback_data="profile", style="danger")]]
        )
        await answer(target, text, kb, "joinly")
        if isinstance(target, CallbackQuery):
            await target.answer()
        return

    bot = target.bot if isinstance(target, Message) else (target.message.bot if target.message else None)
    rows: list[list[InlineKeyboardButton]] = []
    for chat_id in chat_ids:
        title = str(chat_id)
        if bot:
            try:
                chat = await bot.get_chat(chat_id)
                title = (getattr(chat, "title", None) or getattr(chat, "full_name", None) or str(chat_id)).strip() or str(chat_id)
            except Exception:
                title = str(chat_id)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{title} · {chat_id}",
                    callback_data=f"profile:joinly_chat:{chat_id}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="profile", style="danger")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await answer(target, text, kb, "joinly")
    if isinstance(target, CallbackQuery):
        await target.answer()


@router.callback_query(F.data.startswith("profile:joinly_chat:"))
async def on_profile_joinly_chat(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    user = cb.from_user
    if not user:
        await cb.answer()
        return

    parts = cb.data.split(":", 2)
    raw_chat_id = parts[2] if len(parts) > 2 else ""
    try:
        chat_id = int(raw_chat_id)
    except Exception:
        await cb.answer("Not found", show_alert=True)
        return

    await _render_joinly_chat_detail(cb, state, chat_id)
    await cb.answer()


async def _render_joinly_chat_detail(cb: CallbackQuery, state: FSMContext, chat_id: int) -> None:
    lang = await get_language(cb, state)

    db = load_joinly()
    chat_cfg = db.get(str(chat_id)) if isinstance(db, dict) else None
    if not isinstance(chat_cfg, dict):
        await cb.answer("Not found", show_alert=True)
        return

    try:
        chat = await cb.bot.get_chat(chat_id)
        title = (getattr(chat, "title", None) or getattr(chat, "full_name", None) or str(chat_id)).strip() or str(chat_id)
    except Exception:
        title = str(chat_id)

    await state.update_data(joinly_current_chat_id=chat_id)

    text = f"{t('joinly_profile_title', lang)}\n\n"
    text += f"<b>{title}</b>\n"
    text += t("joinly_profile_chat", lang, chat_id=chat_id)

    welcome_enabled = bool(chat_cfg.get("WelcomeEnabled"))
    cleanup_enabled = bool(chat_cfg.get("DeleteServiceMessages"))
    enabled = bool(chat_cfg.get("Enabled"))
    ban_enabled = bool(chat_cfg.get("BanMembers"))

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    f"{t('join_btn_welcome_toggle', lang)}: {_toggle_label(welcome_enabled, lang)}",
                    callback_data=f"profile:joinly_toggle:{chat_id}:WelcomeEnabled",
                    icon="yes" if welcome_enabled else "no",
                    style="success" if welcome_enabled else "danger",
                )
            ],
            [
                _btn(
                    f"{t('join_btn_service_cleanup', lang)}: {_toggle_label(cleanup_enabled, lang)}",
                    callback_data=f"profile:joinly_toggle:{chat_id}:DeleteServiceMessages",
                    icon="yes" if cleanup_enabled else "no",
                    style="success" if cleanup_enabled else "danger",
                )
            ],
            [
                _btn(
                    f"{t('join_btn_enabled', lang)}: {_toggle_label(enabled, lang)}",
                    callback_data=f"profile:joinly_toggle:{chat_id}:Enabled",
                    icon="yes" if enabled else "no",
                    style="success" if enabled else "danger",
                ),
                _btn(
                    f"{t('join_btn_ban_on_join', lang)}: {_toggle_label(ban_enabled, lang)}",
                    callback_data=f"profile:joinly_toggle:{chat_id}:BanMembers",
                    icon="yes" if ban_enabled else "no",
                    style="success" if ban_enabled else "danger",
                ),
            ],
            [_btn(t("btn_back", lang), callback_data="profile:joinly", style="danger", icon="back")],
        ]
    )
    await answer(cb, text, kb, "joinly")


@router.callback_query(F.data.startswith("profile:joinly_toggle:"))
async def on_profile_joinly_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    user = cb.from_user
    if not user:
        await cb.answer()
        return

    parts = cb.data.split(":", 3)
    if len(parts) < 4:
        await cb.answer("Not found", show_alert=True)
        return

    raw_chat_id = parts[2]
    field = parts[3]
    if field not in {"WelcomeEnabled", "DeleteServiceMessages", "Enabled", "BanMembers"}:
        await cb.answer("Not found", show_alert=True)
        return

    try:
        chat_id = int(raw_chat_id)
    except Exception:
        await cb.answer("Not found", show_alert=True)
        return

    # Verify user is admin/creator in that chat before allowing edits.
    try:
        member = await cb.bot.get_chat_member(chat_id, user.id)
        status = getattr(member, "status", None)
        if status not in {"administrator", "creator"}:
            await cb.answer("Недостаточно прав" if lang == "ru" else "Not enough rights", show_alert=True)
            return
    except Exception:
        await cb.answer("Недостаточно прав" if lang == "ru" else "Not enough rights", show_alert=True)
        return

    db = load_joinly()
    if not isinstance(db, dict):
        db = {}
    chat_cfg = db.get(str(chat_id))
    if not isinstance(chat_cfg, dict):
        chat_cfg = {}
        db[str(chat_id)] = chat_cfg

    current = bool(chat_cfg.get(field))
    chat_cfg[field] = (not current)
    try:
        save_joinly(db)
    except Exception:
        pass

    await _render_joinly_chat_detail(cb, state, chat_id)


@router.callback_query(F.data == "profile:joinly")
async def on_profile_joinly(cb: CallbackQuery, state: FSMContext) -> None:
    await _render_profile_joinly(cb, state)


@router.callback_query(F.data.startswith("my:"))
async def on_my_items(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    kind = parts[1]
    page = int(parts[2])
    lang = await get_language(cb, state)

    await state.update_data(catalog_back=f"my:{kind}:{page}")

    data = await state.get_data()
    slugs: List[str] = data.get(f"my_{kind}", [])
    pending_ids: List[str] = data.get("my_pending_plugins", []) if kind == "plugins" else []

    if not slugs and not pending_ids:
        await cb.answer(t("catalog_empty", lang), show_alert=True)
        return

    all_items = []
    for slug in slugs:
        if kind == "plugins":
            entity = find_plugin_by_slug(slug)
        else:
            entity = find_icon_by_slug(slug)

        if entity:
            locale = entity.get(lang) or entity.get("ru") or {}
            name = locale.get("name") or slug
            if kind == "plugins":
                all_items.append((name, f"myplugin:{encode_slug(slug)}"))
            else:
                all_items.append((name, f"icon:{encode_slug(slug)}"))

    if kind == "plugins" and cb.from_user:
        reqs = get_user_requests(cb.from_user.id)
        for req_id in pending_ids:
            req = next((r for r in reqs if r.get("id") == req_id), None)
            payload = req.get("payload", {}) if isinstance(req, dict) else {}
            plugin = payload.get("plugin", {}) if isinstance(payload, dict) else {}
            name = str(plugin.get("name") or req_id).strip() or req_id
            req_type = req.get("type") if isinstance(req, dict) else "new"
            cb_data = f"pendupd:{req_id}" if req_type == "update" else f"pendreq:{req_id}"
            all_items.append((f"{name} ✍", cb_data))

    total = len(all_items)
    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    items = all_items[start : start + PAGE_SIZE]

    title = t("icons_title" if kind == "icons" else "catalog_title", lang)
    caption = f"{title}\n{t('catalog_page', lang, current=page + 1, total=total_pages)}"
    await answer(cb, caption, paginated_list_kb(items, page, total_pages, f"my:{kind}", "profile", lang=lang), "profile")
    await cb.answer()


@router.inline_query()
async def on_inline(query: InlineQuery) -> None:
    text = (query.query or "").strip()
    lang = get_lang(query.from_user.id if query.from_user else None)

    lowered = text.lower()
    if lowered.startswith("stenka"):
        wall_id = _stenka_next_id()
        msg_text = _stenka_render_text(wall_id)
        result = InlineQueryResultArticle(
            id=f"stenka:{wall_id}",
            title=t("stenka_title", lang),
            description=t("stenka_inline_description", lang),
            input_message_content=InputTextMessageContent(
                message_text=msg_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            ),
            reply_markup=_stenka_kb(wall_id, lang=lang),
        )
        await query.answer([result], cache_time=0, is_personal=True)
        return
    if lowered in {"donate", "inform"}:
        url = "https://t.me/exteraPluginsSup/302" if lowered == "donate" else "https://t.me/exteraPluginsSup/372"
        message_text = t(
            "catalog_inline_quick_donate" if lowered == "donate" else "catalog_inline_quick_inform",
            lang,
            url=url,
        )
        description = strip_html(message_text)
        result = InlineQueryResultArticle(
            id=f"quick:{lowered}",
            title=lowered,
            description=description[:100],
            input_message_content=InputTextMessageContent(
                message_text=message_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            ),
        )
        await query.answer([result], cache_time=60, is_personal=True)
        return

    results = []
    user_id = query.from_user.id if query.from_user else 0
    if text and int(user_id) in get_admins_super():
        request_entry = get_request_by_plugin_id(text)
        if request_entry and request_entry.get("status") in {"pending", "error", "scheduled"}:
            request_id = str(request_entry.get("id") or text)
            update_request_payload(request_id, {"moderation_inline_public": True})
            request_entry = get_request_by_plugin_id(text) or request_entry
            payload = request_entry.get("payload", {}) if isinstance(request_entry.get("payload"), dict) else {}
            yes, no, _ = vote_counts(request_entry)
            message_text = forum_text_with_votes(request_entry)
            vote_markup = moderation_inline_vote_url_kb(BOT_USERNAME, request_id, yes, no, lang=lang)
            results.append(
                InlineQueryResultArticle(
                    id=f"request:{encode_slug(request_id)}",
                    title=f"🗂 Заявка {request_id}",
                    description=strip_html(message_text)[:100],
                    input_message_content=InputTextMessageContent(
                        message_text=message_text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    ),
                    reply_markup=vote_markup,
                )
            )
            inline_file = _request_inline_file(payload)
            if inline_file:
                file_id, file_name, file_kind = inline_file
                results.append(
                    InlineQueryResultCachedDocument(
                        id=f"request-file:{encode_slug(request_id)}",
                        title=f"📎 Файл {file_name}",
                        document_file_id=file_id,
                        description=f"Файл заявки {request_id}",
                        caption=_request_inline_file_caption(request_id, file_kind, message_text),
                        parse_mode=ParseMode.HTML,
                        reply_markup=vote_markup,
                    )
                )

    plugins = search_plugins(text, limit=10)
    icons = search_icons(text, limit=10)

    if not results and not plugins and not icons:
        await query.answer([], cache_time=60)
        return

    for idx, plugin in enumerate(plugins):
        slug = plugin.get("slug")
        if not slug:
            continue

        locale = plugin.get(lang) or plugin.get("ru") or {}
        name = locale.get("name") or slug
        category_key = str(plugin.get("category") or "").strip()
        category_fallback = CATEGORY_FALLBACKS.get(category_key, "🧩")
        title = f"{category_fallback} {name}"
        preview_url = _plugin_category_preview_url(category_key)
        preview = _with_hidden_preview_link(build_inline_preview(plugin, lang, "plugin"), preview_url)
        description = strip_html(locale.get("description") or t("catalog_inline_no_description", lang))
        link = plugin.get("channel_message", {}).get("link")
        reply_markup = None
        deeplink = f"tg://resolve?domain={BOT_USERNAME}&start={slug}"
        if link:
            buttons = [
                InlineKeyboardButton(text=t("catalog_inline_download", lang), url=link, style="success"),
                InlineKeyboardButton(text=t("catalog_inline_open_in_bot", lang), url=deeplink),
            ]
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[buttons])
        else:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=t("catalog_inline_open_in_bot", lang), url=deeplink)]]
            )

        results.append(
            InlineQueryResultArticle(
                id=f"plugin:{encode_slug(slug)}:{idx}",
                title=title,
                description=description[:100],
                input_message_content=InputTextMessageContent(
                    message_text=preview,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                    link_preview_options=link_preview_options(url=preview_url),
                ),
                reply_markup=reply_markup,
            )
        )

    for idx, icon in enumerate(icons):
        slug = icon.get("slug")
        if not slug:
            continue

        locale = icon.get(lang) or icon.get("ru") or {}
        name = locale.get("name") or slug
        preview = build_inline_preview(icon, lang, "icon")
        description = ""
        link = icon.get("channel_message", {}).get("link")
        reply_markup = None
        if link:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=t("catalog_inline_download", lang), url=link, style="success")]]
            )

        results.append(
            InlineQueryResultArticle(
                id=f"icon:{encode_slug(slug)}:{idx}",
                title=name,
                description=description[:100],
                input_message_content=InputTextMessageContent(
                    message_text=preview,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                ),
                reply_markup=reply_markup,
            )
        )

    await query.answer(results, cache_time=60, is_personal=True)
