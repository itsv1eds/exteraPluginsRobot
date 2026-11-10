import html
import hashlib
import logging
import math
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InlineQueryResultCachedDocument,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputTextMessageContent,
    Message,
)
from aiogram.exceptions import TelegramBadRequest

from bot import limits
from bot.routers import joinly_flow
from bot.cache import get_admins_super, get_categories, get_icons
from bot.constants import PAGE_SIZE
from bot.context import get_language, get_lang
from bot.languages import category_labels, localized_value
from core.categories import categories_for
from bot.callback_tokens import decode_slug, encode_slug
from bot.formatting import plain_html, quote_html
from bot.helpers import ack, answer, image_path, link_preview_options, localized_image_key, strip_html
from bot.helpers import with_hidden_preview_link as _with_hidden_preview_link
from bot.icons import CATEGORY_FALLBACKS, CATEGORY_ICONS, ICONS
from bot.menu_owner import MenuOwnerMiddleware
from bot.keyboards import (
    _back,
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
from core.catalog import (
    find_icon_by_slug,
    find_plugin_by_slug,
    find_user_icons,
    find_user_plugins,
    list_plugins_by_category,
    search_icons,
    search_plugins_ranked,
)
from core.subscription_store import (
    add_subscription,
    is_subscribed,
    list_subscriptions,
    remove_subscription,
)
from core.subscription_store import ALL_SUBSCRIPTION_KEY
from core.user_store import (
    has_paid_broadcast_disable,
    is_broadcast_enabled,
)
from core.storage import load_joinly
from core.storage import save_joinly
from core.request_store import get_request_by_id, get_request_by_plugin_id, get_user_requests, request_callback_token, update_request_payload
from bot.services.moderation import VOTABLE_REQUEST_STATUSES, forum_text_with_votes, vote_counts
from bot.services.inline import answer_inline, parse_inline_language

router = Router(name="catalog-flow")
BROADCAST_DISABLE_PAYLOAD = "simple_payment:broadcast_disable"
BROADCAST_DISABLE_CURRENCY = "XTR"
BROADCAST_DISABLE_PRICE = 50
_INLINE_PAGE = 20
_INLINE_SEARCH_LIMIT = 200
router.callback_query.middleware(MenuOwnerMiddleware())
logger = logging.getLogger(__name__)

BOT_USERNAME = "exteraPluginsRobot"
GITHUB_IMG_BASE_URL = "https://github.com/itsv1eds/exteraPluginsRobot/blob/main/img"


def _github_img_url(image_key: str) -> str:
    path = image_path(image_key)
    filename = path.name if path else f"{image_key}.jpg"
    return f"{GITHUB_IMG_BASE_URL}/{filename}?raw=true"


def _plugin_category_preview_url(category_key: str, lang: str = "en") -> str:
    keys = categories_for(category_key)
    image_key = f"cat_{keys[0]}" if keys else "cat_all"
    return _github_img_url(localized_image_key(image_key, lang))


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
    if len(strip_html(text)) <= limits.CAPTION:
        return text

    suffix = "\n\nПолный текст есть в отдельной карточке заявки."
    budget = max(200, limits.CAPTION - len(strip_html(suffix)) - 3)
    body, truncated = _truncate_caption_html(text, budget)
    if not truncated:
        return body
    return f"{body}...{suffix}"


def _toggle_label(value: bool, lang: str) -> str:
    return t("state_on" if value else "state_off", lang)


def build_plugin_preview(entry: Dict[str, Any], lang: str) -> str:
    locale = localized_value(entry, lang, {})
    authors = entry.get("authors", {})
    raw_blocks = entry.get("raw_blocks", {}) or {}
    raw_locale = localized_value(raw_blocks, lang, {})
    author_channels = entry.get("author_channels", {}) or {}

    name_value = locale.get("name") or entry.get("slug", "?")
    author = (
        (raw_locale.get("author") if isinstance(raw_locale, dict) else None)
        or localized_value(authors, lang)
        or (raw_locale.get("author_channel") if isinstance(raw_locale, dict) else None)
        or t("catalog_inline_no_description", lang)
    )
    author_channel = (
        (raw_locale.get("author_channel") if isinstance(raw_locale, dict) else None)
        or localized_value(author_channels, lang)
    )
    count = entry.get("count")
    if count is None:
        count = entry.get("icons_count") or entry.get("icon_count")
    if count is None:
        icons = entry.get("icons")
        if isinstance(icons, (list, dict)):
            count = len(icons)

    name = html.escape(str(name_value))
    author_safe = html.escape(str(author)).replace("\\n","\n")
    lines = [f"<b>{name}</b> by {author_safe}"]
    if categories_for(entry):
        lines.append(f"<b>{t('catalog_field_categories', lang)}:</b> {html.escape(category_labels(entry, lang))}")

    description = (locale.get("description") or "").strip()
    if description:
        lines.append(quote_html(description, expandable=True))

    min_version = (entry.get("min_version") or "").strip()
    if min_version:
        lines.append(f"<b>{t('catalog_field_min_version', lang)}:</b> {html.escape(min_version)}")

    if author_channel:
        lines.append(f"<b>{t('catalog_field_author_channel', lang)}:</b> {html.escape(str(author_channel))}")
    if count is not None:
        lines.append(f"<b>{t('catalog_field_icons', lang)}:</b> {count}")

    return "\n".join(lines)


def _inline_description(value: str) -> str:
    text = strip_html(value).replace("\\n", " ")
    return " ".join(re.sub(r"(?<![\w/:])/n", " ", text).split())


def build_inline_preview(entry: Dict[str, Any], lang: str, kind: str = "plugin") -> str:
    locale = localized_value(entry, lang, {})
    authors = entry.get("authors", {})
    raw_blocks = entry.get("raw_blocks", {}) or {}
    raw_locale = localized_value(raw_blocks, lang, {})

    name = html.escape(locale.get("name") or entry.get("slug", "?"))
    author_value = (
        (raw_locale.get("author") if isinstance(raw_locale, dict) else None)
        or localized_value(authors, lang)
        or (raw_locale.get("author_channel") if isinstance(raw_locale, dict) else None)
        or t("catalog_inline_no_description", lang)
    )
    author = html.escape(str(author_value).replace("\\n", "\n"))
    count = entry.get("count")
    if count is None:
        count = entry.get("icons_count") or entry.get("icon_count")
    if count is None:
        icons = entry.get("icons")
        if isinstance(icons, (list, dict)):
            count = len(icons)
    if kind == "plugin":
        category = (categories_for(entry) or [""])[0]
        icon_id = CATEGORY_ICONS.get(category) or ICONS["plugin"]
        fallback = CATEGORY_FALLBACKS.get(category, "🧩")
        lines = [f'<tg-emoji emoji-id="{icon_id}">{fallback}</tg-emoji> <b>{name}</b> by <code>{author}</code>']
        if categories_for(entry):
            lines.append(f"<b>{t('catalog_field_categories', lang)}:</b> {html.escape(category_labels(entry, lang))}")
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
    return "\n".join(lines)


@router.callback_query(F.data == "catalog")
async def on_catalog(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    if await state.get_state() == UserFlow.searching.state:
        await state.set_state(UserFlow.idle)
    await answer(
        cb,
        t("catalog_title", lang),
        catalog_main_kb(get_categories(), lang),
        "catalog",
    )
    await ack(cb)


@router.callback_query(F.data.startswith("catalog:source:"))
async def on_removed_catalog_source(cb: CallbackQuery, state: FSMContext) -> None:
    await on_catalog(cb, state)


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
        await ack(target)


@router.callback_query(F.data == "profile:broadcast")
async def on_profile_broadcast(cb: CallbackQuery, state: FSMContext) -> None:
    await _show_broadcast_settings(cb, state)


@router.callback_query(F.data == "profile:broadcast:toggle")
async def on_profile_broadcast_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    user = cb.from_user
    if not user:
        await cb.answer()
        return

    from core.user_store import set_broadcast_enabled

    enabled = is_broadcast_enabled(user.id)
    set_broadcast_enabled(user.id, not enabled)
    await _show_broadcast_settings(cb, state)
    await ack(cb)


@router.callback_query(F.data == "profile:broadcast:pay")
async def on_profile_broadcast_pay(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    user = cb.from_user
    if not user:
        await cb.answer()
        return

    from aiogram.types import LabeledPrice

    await cb.bot.send_invoice(
        chat_id=user.id,
        title=t("broadcast_invoice_title", lang),
        description=t("broadcast_invoice_description", lang),
        payload=BROADCAST_DISABLE_PAYLOAD,
        provider_token="",
        currency=BROADCAST_DISABLE_CURRENCY,
        prices=[LabeledPrice(label="Disable broadcast", amount=BROADCAST_DISABLE_PRICE)],
    )
    await ack(cb)


@router.callback_query(F.data == "page:noop")
async def on_page_noop(cb: CallbackQuery) -> None:
    await ack(cb)


@router.callback_query(F.data.startswith("page:picker|"))
async def on_page_picker(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    payload = cb.data.split("|")
    if len(payload) != 4:
        await ack(cb)
        return

    nav_prefix = payload[1]
    try:
        page = int(payload[2])
        total_pages = min(int(payload[3]), 10000)
    except ValueError:
        await ack(cb)
        return

    if not cb.message:
        await ack(cb)
        return

    await cb.message.edit_reply_markup(
        reply_markup=page_picker_kb(
            nav_prefix=nav_prefix,
            current_page=page,
            total_pages=total_pages,
            lang=lang,
        )
    )
    await ack(cb)


@router.callback_query(F.data.startswith("cat:"))
async def on_catalog_category(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    cat_key = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    lang = await get_language(cb, state)
    if cat_key != "_all":
        keys = categories_for(cat_key)
        if not keys:
            await cb.answer(t("not_found", lang), show_alert=True)
            return
        cat_key = keys[0]
    plugins = list_plugins_by_category(cat_key)
    total = len(plugins)

    if total == 0:
        await ack(cb, t("catalog_empty", lang), show_alert=True)
        return

    total_pages = math.ceil(total / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = plugins[start : start + PAGE_SIZE]

    items = []
    for plugin in page_items:
        slug = plugin.get("slug")
        if not slug:
            continue
        locale = localized_value(plugin, lang, {})
        name = locale.get("name") or slug
        items.append((name, f"plugin:{encode_slug(slug)}"))

    if cat_key == "_all":
        title_html = t("all_plugins_title", lang)
    else:
        title_html = t(f"category_{cat_key}_label_msg", lang)

    caption = title_html
    await state.update_data(catalog_back=f"cat:{cat_key}:{page}")

    image_key = "cat_all" if cat_key == "_all" else f"cat_{cat_key}"
    await answer(cb, caption, paginated_list_kb(items, page, total_pages, f"cat:{cat_key}", "catalog", lang=lang), image_key)
    await ack(cb)


def _plugin_detail_view(user, plugin: Dict[str, Any], lang: str, back: str, source: str):
    slug = plugin.get("slug") or ""
    token = encode_slug(slug)
    user_id = user.id if user else None
    own = source == "my"
    request = _plugin_request(user_id, slug) if own else None
    notify_all_enabled = bool(user_id) and is_subscribed(user_id, ALL_SUBSCRIPTION_KEY)
    subscribed = bool(user_id) and is_subscribed(user_id, slug)
    keyboard = plugin_detail_kb(
        plugin.get("channel_message", {}).get("link"),
        back,
        lang,
        update_callback=f"profile:update:{token}" if own and not request else None,
        delete_callback=f"profile:delete:{token}" if own and not request else None,
        request_callback=_request_callback(request) if request else None,
        subscribe_callback=None if notify_all_enabled else f"sub:toggle:{token}:{source}",
        subscribe_label=t("btn_unsubscribe" if subscribed else "btn_subscribe", lang),
    )
    category = next(iter(categories_for(plugin)), None)
    text = build_plugin_preview(plugin, lang)
    if request:
        text += "\n\n" + t("plugin_has_request", lang, status=_pending_label(request, lang))
    return text, keyboard, "profile" if own else (f"cat_{category}" if category else "cat_all")


async def _show_plugin_detail(cb: CallbackQuery, state: FSMContext, slug: str, source: str) -> bool:
    lang = await get_language(cb, state)
    plugin = find_plugin_by_slug(slug)
    if not plugin:
        await ack(cb, t("not_found", lang), show_alert=True)
        return False
    default_back = "my:plugins:0" if source == "my" else "catalog"
    back = (await state.get_data()).get("catalog_back") or default_back
    text, keyboard, image = _plugin_detail_view(cb.from_user, plugin, lang, back, source)
    await answer(cb, text, keyboard, image)
    return True


@router.callback_query(F.data.startswith("plugin:"))
async def on_plugin_detail(cb: CallbackQuery, state: FSMContext) -> None:
    if await _show_plugin_detail(cb, state, decode_slug(cb.data.split(":", 1)[1]), "catalog"):
        await ack(cb)


@router.callback_query(F.data.startswith("myplugin:"))
async def on_my_plugin_detail(cb: CallbackQuery, state: FSMContext) -> None:
    if await _show_plugin_detail(cb, state, decode_slug(cb.data.split(":", 1)[1]), "my"):
        await ack(cb)


@router.callback_query(F.data.startswith("sub:toggle:"))
async def on_toggle_subscription(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    parts = cb.data.split(":", 3)
    slug = decode_slug(parts[2])
    source = "my" if len(parts) > 3 and parts[3] == "my" else "catalog"

    if not cb.from_user:
        await ack(cb)
        return

    if is_subscribed(cb.from_user.id, ALL_SUBSCRIPTION_KEY):
        await ack(cb, t("btn_notify_all_on", lang), show_alert=True)
        await _show_subscriptions(cb, state, page=0)
        return

    if is_subscribed(cb.from_user.id, slug):
        remove_subscription(cb.from_user.id, slug)
        await ack(cb, t("unsubscribed", lang))
    elif find_plugin_by_slug(slug):
        add_subscription(cb.from_user.id, slug)
        await ack(cb, t("subscribed", lang))

    await _show_plugin_detail(cb, state, slug, source)


@router.callback_query(F.data == "search")
async def on_search(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    await state.set_state(UserFlow.searching)
    await answer(cb, t("search_prompt", lang), search_kb(lang), "catalog")
    await ack(cb)


@router.message(UserFlow.searching)
async def on_search_query(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    query = (message.text or "").strip()

    if not query:
        await message.answer(t("need_text", lang))
        return

    results, fuzzy = search_plugins_ranked(query, limit=40)
    slugs = [plugin["slug"] for plugin in results if plugin.get("slug")]
    await state.update_data(last_search_query=query, last_search_results=slugs, last_search_fuzzy=fuzzy)
    await _render_search_results(message, state, page=0)


async def _render_search_results(target: Message | CallbackQuery, state: FSMContext, page: int) -> None:
    lang = await get_language(target, state)
    data = await state.get_data()
    query = plain_html(data.get("last_search_query") or "")
    slugs = [slug for slug in data.get("last_search_results", [])
             if (entry := find_plugin_by_slug(slug)) and entry.get("status") == "published"]
    await state.update_data(last_search_results=slugs)
    await state.set_state(UserFlow.searching)
    if not slugs:
        await answer(target, t("search_empty_for", lang, query=query), search_kb(lang), "catalog")
        return

    total_pages = math.ceil(len(slugs) / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    items = []
    for slug in slugs[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        plugin = find_plugin_by_slug(slug)
        name = localized_value(plugin, lang, {}).get("name") or slug
        category = next(iter(categories_for(plugin)), None)
        items.append((name, f"plugin:{encode_slug(slug)}", CATEGORY_ICONS.get(category) and category or "plugin"))

    key = "search_fuzzy_for" if data.get("last_search_fuzzy") else "search_results_for"
    text = t(key, lang, query=query, count=len(slugs))
    await state.update_data(catalog_back=f"search:{page}")
    keyboard = paginated_list_kb(items, page, total_pages, "search", "catalog", lang=lang)
    keyboard.inline_keyboard.insert(-1, [_btn(t("btn_search_inline", lang), switch_inline_query="", icon="search")])
    await answer(target, text, keyboard, "catalog")


@router.callback_query(F.data.startswith("search:"))
async def on_search_page(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":", 1)
    page = 0
    if len(parts) > 1 and parts[1].isdigit():
        page = int(parts[1])
    await _render_search_results(cb, state, page=page)
    await ack(cb)


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
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = icons[start : start + PAGE_SIZE]

    items = []
    for icon in page_items:
        slug = icon.get("slug")
        if not slug:
            continue
        locale = localized_value(icon, lang, {})
        name = locale.get("name") or slug
        items.append((name, f"icon:{encode_slug(slug)}"))

    caption = t("icons_title", lang)
    await state.update_data(catalog_back=f"icons:{page}")
    await answer(cb, caption, paginated_list_kb(items, page, total_pages, "icons", "catalog", lang=lang), "iconpacks")
    await ack(cb)


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
    await ack(cb)


def _pending_kind(req: dict) -> str:
    if req.get("status") in {"approved", "scheduled"}:
        return req["status"]
    if req.get("status") == "rework":
        return "rework"
    kind = str(req.get("type") or "new")
    return kind if kind in {"new", "update", "delete"} else "new"


def _pending_name(req: dict) -> str:
    payload = req.get("payload", {}) if isinstance(req.get("payload"), dict) else {}
    plugin = payload.get("plugin", {}) if isinstance(payload.get("plugin"), dict) else {}
    return str(plugin.get("name") or payload.get("delete_slug") or req.get("id") or "—").strip() or "—"


def _pending_label(req: dict, lang: str) -> str:
    if req.get("status") == "scheduled":
        from bot.services.approval import approval_status_text

        return strip_html(approval_status_text(req, lang))
    return t(f"profile_pending_{_pending_kind(req)}", lang)


@router.callback_query(F.data.startswith("usr:request:"))
async def on_profile_request(cb: CallbackQuery, state: FSMContext) -> None:
    from bot.routers.admin_flow import _render_request_draft
    from bot.services.approval import approval_status_text

    lang = await get_language(cb, state)
    entry = get_request_by_id(cb.data.split(":")[2])
    if not entry or not cb.from_user or (entry.get("payload") or {}).get("user_id") != cb.from_user.id:
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    from bot.services.approval import can_edit_request

    if can_edit_request(entry):
        from bot.routers.user_flow import on_open_pending_request

        await on_open_pending_request(cb, state)
        return
    pending_removal = entry.get("type") == "delete" and entry.get("status") in {"pending", "error", "rework"}
    if entry.get("status") not in {"approved", "scheduled"} and not pending_removal:
        await on_profile(cb, state)
        return
    status_text = t("profile_pending_delete_info", lang) if pending_removal else approval_status_text(entry, lang)
    text = status_text + "\n\n" + _render_request_draft(entry, lang=lang)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        _back(lang, "profile"),
    ]])
    await answer(cb, text, keyboard, "profile")
    await ack(cb)


_ACTIVE_REQUEST_STATUSES = {"pending", "error", "rework", "approved", "scheduled"}
_PENDING_ICONS = {"new": "clock", "update": "updates", "delete": "delete", "rework": "edit",
                  "approved": "yes", "scheduled": "calendar"}


def _own_requests(user_id: int) -> list[dict]:
    result = []
    for req in get_user_requests(user_id):
        if req.get("status") not in _ACTIVE_REQUEST_STATUSES or req.get("type") not in {"new", "update", "delete"}:
            continue
        payload = req.get("payload", {})
        submission_type = (payload.get("submission_type") or payload.get("type") or "").strip()
        if submission_type == "icon" or payload.get("icon"):
            continue
        if submission_type not in {"plugin", "update"} and not payload.get("plugin") and not payload.get("delete_slug"):
            continue
        result.append(req)
    return result


def _request_target_slug(req: dict) -> str:
    payload = req.get("payload") or {}
    return str(payload.get("update_slug") or payload.get("delete_slug") or "").strip().lower()


def _request_callback(req: dict) -> str:
    prefix = "usr:request" if req.get("type") == "delete" else "pendreq"
    return f"{prefix}:{request_callback_token(req['id'])}"


def _plugin_request(user_id: int | None, slug: str) -> dict | None:
    target = str(slug or "").strip().lower()
    if not user_id or not target:
        return None
    return next((req for req in _own_requests(user_id) if _request_target_slug(req) == target), None)


async def build_profile(user, state: FSMContext, lang: str):
    user_plugins = find_user_plugins(user.id, user.username or "")
    user_icons = find_user_icons(user.id, user.username or "")
    pending = _own_requests(user.id)

    text = f"{t('profile_title', lang)}\n\n"
    if user.username:
        text += f"@{user.username}\n"
    text += t("profile_stats", lang, plugins=len(user_plugins), icons=len(user_icons))

    if pending:
        text += "\n\n" + t("profile_pending_title", lang, count=len(pending))
        for req in pending:
            text += f"\n• {html.escape(_pending_name(req))} — {_pending_label(req, lang)}"

    if not user_plugins and not user_icons and not pending:
        text += f"\n\n{t('profile_empty', lang)}"

    notify_all_enabled = is_subscribed(user.id, ALL_SUBSCRIPTION_KEY)
    return text, profile_kb(lang, bool(user_plugins or pending), bool(user_icons), notify_all_enabled=notify_all_enabled)


@router.callback_query(F.data == "profile")
async def on_profile(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    if not cb.from_user:
        await cb.answer()
        return
    text, keyboard = await build_profile(cb.from_user, state, lang)
    await answer(cb, text, keyboard, "profile")
    await ack(cb)


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
    slugs = [] if all_enabled else slugs

    total_pages = max(math.ceil(len(slugs) / PAGE_SIZE), 1)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE

    items = []
    for slug in slugs[start : start + PAGE_SIZE]:
        plugin = find_plugin_by_slug(slug)
        locale = localized_value(plugin, lang, {})
        name = (locale.get("name") if locale else None) or slug
        items.append((name, f"plugin:{encode_slug(slug)}"))

    caption = f"{t('subscriptions_title', lang)}\n{t('subscriptions_hint', lang)}"
    if not slugs and not all_enabled:
        caption += f"\n\n{t('subscriptions_empty', lang)}"
    await state.update_data(catalog_back=f"subs:page:{page}")

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
        await ack(target)


def _user_joinly_chat_ids(db: dict[str, Any], user_id: int) -> list[int]:
    panel_key = f"PanelMessageId:{user_id}"
    chat_ids: list[int] = []
    for k, v in db.items():
        if not str(k).lstrip("-").isdigit():
            continue
        if not isinstance(v, dict):
            continue
        manual = v.get("ManualAdmins") or []
        is_manual = isinstance(manual, list) and user_id in manual
        if int(v.get(panel_key) or 0) <= 0 and not is_manual:
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
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [_btn(t("btn_joinly_add_chat", lang), callback_data="profile:joinly_add", style="success", icon="add")],
            [_back(lang, "profile")],
        ])
        await answer(target, text, kb, "joinly")
        if isinstance(target, CallbackQuery):
            await target.answer()
        return

    bot = target.bot if isinstance(target, Message) else (target.message.bot if target.message else None)
    rows: list[list[InlineKeyboardButton]] = []
    from bot.services.chat_info import get_chat_labels

    labels = await get_chat_labels(bot, chat_ids) if bot else {}
    for chat_id in chat_ids:
        identity = labels.get(chat_id, {})
        title = identity.get("title") or identity.get("name")
        rows.append([_btn(f"{title} · {chat_id}" if title else str(chat_id),
                          callback_data=f"profile:joinly_chat:{chat_id}", icon="joinly")])

    rows.append([_btn(t("btn_joinly_add_chat", lang), callback_data="profile:joinly_add", style="success", icon="add")])
    rows.append([_back(lang, "profile")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await answer(target, text, kb, "joinly")
    if isinstance(target, CallbackQuery):
        await target.answer()


@router.callback_query(F.data == "profile:joinly_add")
async def on_profile_joinly_add(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    await state.set_state(UserFlow.entering_joinly_chat)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_back(lang, "profile:joinly")],
    ])
    await answer(cb, t("joinly_add_prompt", lang), kb, "joinly")
    await ack(cb)


def _parse_chat_ref(raw: str) -> str | int:
    ref = (raw or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/"):
        if ref.lower().startswith(prefix):
            ref = ref[len(prefix):]
            break
    ref = ref.split("?")[0].split("/")[0].strip()
    if ref.lstrip("-").isdigit():
        return int(ref)
    if not ref.startswith("@"):
        ref = f"@{ref}"
    return ref


@router.message(UserFlow.entering_joinly_chat)
async def on_joinly_chat_input(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    user = message.from_user
    if not user:
        return
    ref = _parse_chat_ref(message.text or "")
    if not ref or ref == "@":
        await message.answer(t("joinly_add_err_not_found", lang))
        return

    try:
        chat = await message.bot.get_chat(ref)
    except Exception:
        await message.answer(t("joinly_add_err_not_found", lang))
        return
    if getattr(chat, "type", None) not in {"group", "supergroup", "channel"}:
        await message.answer(t("joinly_add_err_bad_type", lang))
        return

    try:
        member = await message.bot.get_chat_member(chat.id, user.id)
        is_admin = getattr(member, "status", None) in {"administrator", "creator"}
    except Exception:
        is_admin = False
    if not is_admin:
        await message.answer(t("joinly_add_err_not_admin", lang))
        return

    db = load_joinly()
    if not isinstance(db, dict):
        db = {}
    rec = db.setdefault(str(chat.id), {})
    manual = rec.get("ManualAdmins")
    if not isinstance(manual, list):
        manual = []
    if user.id not in manual:
        manual.append(user.id)
    rec["ManualAdmins"] = manual
    save_joinly(db)

    title = (getattr(chat, "title", None) or str(chat.id)).strip() or str(chat.id)
    await state.set_state(None)
    await message.answer(t("joinly_add_ok", lang, title=html.escape(title)), parse_mode=ParseMode.HTML)
    await _render_profile_joinly(message, state)


@router.callback_query(F.data.startswith("profile:joinly_chat:"))
async def on_profile_joinly_chat(cb: CallbackQuery, state: FSMContext) -> None:
    user = cb.from_user
    if not user:
        await cb.answer()
        return

    parts = cb.data.split(":", 2)
    raw_chat_id = parts[2] if len(parts) > 2 else ""
    try:
        chat_id = int(raw_chat_id)
    except Exception:
        await cb.answer(t("not_found", await get_language(cb, state)), show_alert=True)
        return

    await _render_joinly_chat_detail(cb, state, chat_id)
    await ack(cb)


async def _profile_joinly_chat(cb: CallbackQuery, chat_id: int, lang: str):
    db = load_joinly()
    chat_cfg = db.get(str(chat_id)) if isinstance(db, dict) else None
    if not isinstance(chat_cfg, dict):
        await cb.answer(t("not_found", lang), show_alert=True)
        return None
    try:
        member = await cb.bot.get_chat_member(chat_id, cb.from_user.id)
        if member.status not in {"administrator", "creator"}:
            await cb.answer(t("joinly_add_err_not_admin", lang), show_alert=True)
            return None
        return await cb.bot.get_chat(chat_id)
    except Exception:
        await cb.answer(t("joinly_add_err_not_found", lang), show_alert=True)
        return None


async def _render_joinly_chat_detail(cb: CallbackQuery, state: FSMContext, chat_id: int, *, chat=None) -> None:
    lang = await get_language(cb, state)
    chat = chat or await _profile_joinly_chat(cb, chat_id, lang)
    if chat is None:
        return
    chat_cfg = load_joinly().get(str(chat_id)) or {}
    title = (getattr(chat, "title", None) or getattr(chat, "full_name", None) or str(chat_id)).strip() or str(chat_id)

    await state.update_data(joinly_current_chat_id=chat_id)

    text = f"{t('joinly_profile_title', lang)}\n\n"
    text += f"<b>{html.escape(title)}</b>\n"
    text += t("joinly_profile_chat", lang, chat_id=chat_id)

    if chat.type == "channel":
        text += "\n\n" + t("join_welcome_channel_help", lang)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [_back(lang, "profile:joinly")],
        ])
        await answer(cb, text, kb, "joinly")
        return

    text += "\n" + t("join_welcome_profile_mode", lang, mode=t(f"join_welcome_mode_{joinly_flow._welcome_mode(chat_id)}", lang))

    welcome_enabled = bool(chat_cfg.get("WelcomeEnabled"))
    cleanup_enabled = bool(chat_cfg.get("DeleteServiceMessages"))
    enabled = bool(chat_cfg.get("Enabled"))
    ban_enabled = bool(chat_cfg.get("BanMembers"))

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    f"{t('join_btn_welcome_toggle', lang)}: {_toggle_label(welcome_enabled, lang)}",
                    callback_data=f"profile:joinly_welcome:{chat_id}",
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
            [_back(lang, "profile:joinly")],
        ]
    )
    await answer(cb, text, kb, "joinly")


async def _render_profile_joinly_welcome(cb: CallbackQuery, state: FSMContext, chat) -> None:
    lang = await get_language(cb, state)
    enabled = bool(joinly_flow._get_setting(chat.id, "WelcomeEnabled"))
    rows = [
        [_btn(f"{t('join_btn_welcome_toggle', lang)}: {_toggle_label(enabled, lang)}",
              callback_data=f"profile:joinly_toggle:{chat.id}:WelcomeEnabled",
              icon="yes" if enabled else "no", style="success" if enabled else "danger")],
        *joinly_flow._welcome_mode_rows(chat.id, lang, f"profile:joinly_mode:{chat.id}:"),
        [_btn(t("join_welcome_preview", lang), callback_data=f"profile:joinly_preview:{chat.id}", icon="search")],
        [_btn(t("join_welcome_community_preset", lang), callback_data=f"profile:joinly_preset:{chat.id}", icon="file")],
        [_back(lang, f"profile:joinly_chat:{chat.id}")],
    ]
    text = f"<b>{html.escape(chat.title or str(chat.id))}</b>\n\n"
    text += await joinly_flow._welcome_panel_text(cb.bot, chat.id, lang)
    await answer(cb, text, InlineKeyboardMarkup(inline_keyboard=rows), "joinly")


@router.callback_query(F.data.regexp(r"^profile:joinly_(welcome|mode|preview|preset):-?\d+(?::(?:public|personal))?$"))
async def on_profile_joinly_welcome(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    parts = cb.data.split(":")
    action = parts[1].removeprefix("joinly_")
    chat_id = int(parts[2])
    chat = await _profile_joinly_chat(cb, chat_id, lang)
    if chat is None:
        return
    if chat.type not in {"group", "supergroup"}:
        await _render_joinly_chat_detail(cb, state, chat_id, chat=chat)
        await ack(cb)
        return
    if action == "mode":
        error = await joinly_flow._set_welcome_mode(cb.bot, chat_id, parts[3] if len(parts) > 3 else "")
        if error:
            await cb.answer(t(error, lang), show_alert=True)
            return
    elif action == "preset":
        joinly_flow._set_setting(chat_id, "WelcomeText", t("join_welcome_community_template", lang))
    elif action == "preview":
        error = await joinly_flow._personal_welcome_error(cb.bot, chat_id, refresh=True)
        if error:
            await cb.answer(t(error, lang), show_alert=True)
            return
        await ack(cb)
        sent = await joinly_flow._send_welcome(cb.bot, chat, cb.from_user, lang, personal=True)
        if sent is None:
            logger.warning("event=joinly.welcome_preview_failed chat_id=%s", chat_id)
            await ack(cb, t("join_welcome_preview_failed", lang), show_alert=True)
        return
    await _render_profile_joinly_welcome(cb, state, chat)
    await ack(cb, t("join_saved", lang) if action == "preset" else None)


@router.callback_query(F.data.startswith("profile:joinly_toggle:"))
async def on_profile_joinly_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    user = cb.from_user
    if not user:
        await cb.answer()
        return

    parts = cb.data.split(":", 3)
    if len(parts) < 4:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    raw_chat_id = parts[2]
    field = parts[3]
    if field not in {"WelcomeEnabled", "DeleteServiceMessages", "Enabled", "BanMembers"}:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    try:
        chat_id = int(raw_chat_id)
    except Exception:
        await cb.answer(t("not_found", lang), show_alert=True)
        return
    chat = await _profile_joinly_chat(cb, chat_id, lang)
    if chat is None:
        return
    if chat.type not in {"group", "supergroup"}:
        await _render_joinly_chat_detail(cb, state, chat_id, chat=chat)
        await ack(cb)
        return

    db = load_joinly()
    if not isinstance(db, dict):
        db = {}
    chat_cfg = db.get(str(chat_id))
    if not isinstance(chat_cfg, dict):
        chat_cfg = {}
        db[str(chat_id)] = chat_cfg

    current = bool(chat_cfg.get(field))
    if field == "WelcomeEnabled" and not current and joinly_flow._welcome_mode(chat_id) == "personal":
        error = await joinly_flow._personal_welcome_error(cb.bot, chat_id, refresh=True)
        if error:
            await cb.answer(t(error, lang), show_alert=True)
            return
    chat_cfg[field] = (not current)
    save_joinly(db)

    if field == "WelcomeEnabled":
        await _render_profile_joinly_welcome(cb, state, chat)
    else:
        await _render_joinly_chat_detail(cb, state, chat_id, chat=chat)
    await ack(cb)


@router.callback_query(F.data == "profile:joinly")
async def on_profile_joinly(cb: CallbackQuery, state: FSMContext) -> None:
    await _render_profile_joinly(cb, state)


@router.callback_query(F.data.startswith("my:"))
async def on_my_items(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    kind = parts[1]
    page = int(parts[2])
    lang = await get_language(cb, state)

    user = cb.from_user
    all_items = []
    if kind == "plugins":
        requests = _own_requests(user.id)
        by_slug: dict[str, dict] = {}
        for req in requests:
            by_slug.setdefault(_request_target_slug(req), req)
        merged = set()
        for plugin in find_user_plugins(user.id, user.username or ""):
            slug = plugin.get("slug") or ""
            name = localized_value(plugin, lang, {}).get("name") or slug
            req = by_slug.get(slug.strip().lower())
            if req:
                merged.add(req["id"])
                all_items.append((f"{name} — {_pending_label(req, lang)}", f"myplugin:{encode_slug(slug)}",
                                  _PENDING_ICONS[_pending_kind(req)]))
            else:
                all_items.append((name, f"myplugin:{encode_slug(slug)}", "plugin"))
        for req in requests:
            if req["id"] not in merged:
                all_items.append((f"{_pending_name(req)} — {_pending_label(req, lang)}", _request_callback(req),
                                  _PENDING_ICONS[_pending_kind(req)]))
    else:
        for icon in find_user_icons(user.id, user.username or ""):
            slug = icon.get("slug") or ""
            all_items.append((localized_value(icon, lang, {}).get("name") or slug, f"icon:{encode_slug(slug)}", "art"))

    if not all_items:
        await cb.answer(t("catalog_empty", lang), show_alert=True)
        return

    total = len(all_items)
    total_pages = max(math.ceil(total / PAGE_SIZE), 1)
    page = max(0, min(page, total_pages - 1))
    await state.update_data(catalog_back=f"my:{kind}:{page}")
    start = page * PAGE_SIZE
    items = all_items[start : start + PAGE_SIZE]

    title = t("icons_title", lang) if kind == "icons" else f"<b>{t('btn_my_plugins', lang)}</b>"
    caption = title
    await answer(cb, caption, paginated_list_kb(items, page, total_pages, f"my:{kind}", "profile", lang=lang), "profile")
    await ack(cb)


@router.inline_query()
async def on_inline(query: InlineQuery) -> None:
    text, lang = parse_inline_language(query.query, get_lang(query.from_user.id if query.from_user else None))

    lowered = text.lower()
    if lowered in {"donate", "inform"}:
        url = "https://t.me/exteraPluginsSup/302" if lowered == "donate" else "https://t.me/exteraPluginsSup/372"
        message_text = t(
            "catalog_inline_quick_donate" if lowered == "donate" else "catalog_inline_quick_inform",
            lang,
            url=url,
        )
        description = strip_html(message_text)
        result = InlineQueryResultArticle(
            id=f"quick:{lowered}:{lang}",
            title=lowered,
            description=description[:100],
            input_message_content=InputTextMessageContent(
                message_text=message_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            ),
        )
        await answer_inline(query, [result], cache_time=60, is_personal=True)
        return

    results = []
    user_id = query.from_user.id if query.from_user else 0
    request_result_added = False
    if text and int(user_id) in get_admins_super():
        request_entry = get_request_by_plugin_id(text, statuses=VOTABLE_REQUEST_STATUSES)
        if request_entry and request_entry.get("status") in {"pending", "error", "scheduled"}:
            request_id = str(request_entry.get("id") or text)
            update_request_payload(request_id, {"moderation_inline_public": True})
            request_entry = get_request_by_plugin_id(text, statuses=VOTABLE_REQUEST_STATUSES) or request_entry
            payload = request_entry.get("payload", {}) if isinstance(request_entry.get("payload"), dict) else {}
            yes, no, _ = vote_counts(request_entry)
            message_text = forum_text_with_votes(request_entry)
            try:
                bot_username = str((await query.bot.me()).username or BOT_USERNAME)
            except Exception:
                bot_username = BOT_USERNAME
            vote_markup = moderation_inline_vote_url_kb(bot_username, request_id, yes, no, lang=lang)
            results.append(
                InlineQueryResultArticle(
                    id=f"request:{encode_slug(request_id)}",
                    title=f"Заявка {request_id}",
                    description=strip_html(message_text)[:100],
                    input_message_content=InputTextMessageContent(
                        message_text=message_text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    ),
                    reply_markup=vote_markup,
                )
            )
            request_result_added = True
            inline_file = _request_inline_file(payload)
            if inline_file:
                file_id, file_name, file_kind = inline_file
                results.append(
                    InlineQueryResultCachedDocument(
                        id=f"request-file:{encode_slug(request_id)}",
                        title=f"Файл {file_name}",
                        document_file_id=file_id,
                        description=f"Файл заявки {request_id}",
                        caption=_request_inline_file_caption(request_id, file_kind, message_text),
                        parse_mode=ParseMode.HTML,
                        reply_markup=vote_markup,
                    )
                )

    offset = int(query.offset) if (query.offset or "").isdigit() else 0
    found, fuzzy = search_plugins_ranked(text, limit=_INLINE_SEARCH_LIMIT)
    plugins = found[offset:offset + _INLINE_PAGE]
    next_offset = str(offset + _INLINE_PAGE) if offset + _INLINE_PAGE < len(found) else ""
    icons = search_icons(text, limit=10) if offset == 0 else []
    button_key = None
    if text and not found and not icons:
        button_key = "inline_nothing_found"
    elif text and fuzzy and offset == 0:
        button_key = "inline_close_matches"
    elif not text and offset == 0:
        button_key = "inline_open_catalog"
    inline_button = InlineQueryResultsButton(text=t(button_key, lang), start_parameter="catalog") if button_key else None

    if not results and not plugins and not icons:
        await answer_inline(query, [], cache_time=30, is_personal=True, button=inline_button)
        return

    for idx, plugin in enumerate(plugins):
        slug = plugin.get("slug")
        if not slug:
            continue

        locale = localized_value(plugin, lang, {})
        name = locale.get("name") or slug
        category_key = (categories_for(plugin) or [""])[0]
        category_fallback = CATEGORY_FALLBACKS.get(category_key, "")
        title = f"{category_fallback} {name}"
        preview_url = _plugin_category_preview_url(category_key, lang)
        preview = _with_hidden_preview_link(build_inline_preview(plugin, lang, "plugin"), preview_url)
        description = _inline_description(locale.get("description") or t("catalog_inline_no_description", lang))
        author = str((plugin.get("authors") or {}).get(lang) or (plugin.get("authors") or {}).get("ru") or "").strip()
        if author:
            description = f"{strip_html(author)} · {description}"
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
                id=f"plugin:{hashlib.sha256(str(slug).encode()).hexdigest()[:20]}:{lang}:{idx}",
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

        locale = localized_value(icon, lang, {})
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
                id=f"icon:{hashlib.sha256(str(slug).encode()).hexdigest()[:20]}:{lang}:{idx}",
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

    cache_time = 0 if request_result_added else 60
    extra = {"next_offset": next_offset, "button": inline_button}
    try:
        await answer_inline(query, results, cache_time=cache_time, is_personal=True, **extra)
    except TelegramBadRequest:
        article_results = [item for item in results if isinstance(item, InlineQueryResultArticle)]
        if len(article_results) == len(results):
            raise
        logger.warning(
            "Inline cached media rejected; retrying with article results only",
            exc_info=True,
        )
        await answer_inline(query, article_results, cache_time=0, is_personal=True, **extra)
