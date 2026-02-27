import html
import math
from typing import Any, Dict, List

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputTextMessageContent,
    Message,
)

from bot.cache import get_categories, get_icons
from bot.constants import PAGE_SIZE
from bot.context import get_language, get_lang
from bot.callback_tokens import decode_slug, encode_slug
from bot.helpers import answer, strip_html
from bot.keyboards import (
    catalog_main_kb,
    page_picker_kb,
    paginated_list_kb,
    plugin_detail_kb,
    profile_kb,
    search_kb,
)
from bot.states import UserFlow
from bot.texts import t
from catalog import (
    find_icon_by_slug,
    find_plugin_by_slug,
    find_user_icons,
    find_user_plugins,
    list_plugins_by_category,
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

router = Router(name="catalog-flow")

BOT_USERNAME = "exteraPluginsRobot"


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
        lines.append(f"<blockquote expandable>{html.escape(description)}</blockquote>")

    min_version = (entry.get("min_version") or "").strip()
    if min_version:
        lines.append(f"<b>{t('catalog_field_min_version', lang)}:</b> {html.escape(min_version)}")

    if author_channel:
        lines.append(f"<b>{t('catalog_field_author_channel', lang)}:</b> {html.escape(str(author_channel))}")
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
    lines = [t("catalog_inline_header", lang, name=name, author=author)]
    if count is not None:
        lines.append(f"{t('catalog_field_icons', lang)}: {count}")

    if kind == "plugin":
        description = (locale.get("description") or "").strip()
        if description:
            lines.append(f"<blockquote expandable>{html.escape(description)}</blockquote>")

        min_version = (entry.get("min_version") or "").strip()
        if min_version:
            lines.append(f"<b>{t('catalog_field_min_version', lang)}:</b> {html.escape(min_version)}")
    return "\n".join(lines)


@router.callback_query(F.data == "catalog")
async def on_catalog(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    await answer(cb, t("catalog_title", lang), catalog_main_kb(get_categories(), lang), "catalog")
    await cb.answer()


@router.callback_query(F.data == "page:noop")
async def on_page_noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data.startswith("page:picker|"))
async def on_page_picker(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    payload = cb.data.split("|")
    if len(payload) != 4:
        await cb.answer()
        return

    nav_prefix = payload[1]
    try:
        page = int(payload[2])
        total_pages = int(payload[3])
    except ValueError:
        await cb.answer()
        return

    if not cb.message:
        await cb.answer()
        return

    await cb.message.edit_reply_markup(
        reply_markup=page_picker_kb(
            nav_prefix=nav_prefix,
            current_page=page,
            total_pages=total_pages,
            lang=lang,
        )
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cat:"))
async def on_catalog_category(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    cat_key = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    lang = await get_language(cb, state)

    plugins = list_plugins_by_category(cat_key)
    total = len(plugins)

    if total == 0:
        await cb.answer(t("catalog_empty", lang), show_alert=True)
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

    caption = f"{title_html}\n{t('catalog_page', lang, current=page + 1, total=total_pages)}"
    await state.update_data(catalog_back=f"cat:{cat_key}:{page}")

    image_key = "cat_all" if cat_key == "_all" else f"cat_{cat_key}"
    await answer(cb, caption, paginated_list_kb(items, page, total_pages, f"cat:{cat_key}", "catalog", lang=lang), image_key)
    await cb.answer()


@router.callback_query(F.data.startswith("plugin:"))
async def on_plugin_detail(cb: CallbackQuery, state: FSMContext) -> None:
    slug = decode_slug(cb.data.split(":", 1)[1])
    lang = await get_language(cb, state)

    plugin = find_plugin_by_slug(slug)
    if not plugin:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    text = build_plugin_preview(plugin, lang)
    link = plugin.get("channel_message", {}).get("link")
    notify_all_enabled = is_subscribed(cb.from_user.id, ALL_SUBSCRIPTION_KEY) if cb.from_user else False
    subscribed = is_subscribed(cb.from_user.id, slug) if cb.from_user else False
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
            subscribe_callback=(None if notify_all_enabled else f"sub:toggle:{encode_slug(slug)}:catalog"),
            subscribe_label=(None if notify_all_enabled else subscribe_label),
        ),
        "plugins",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("myplugin:"))
async def on_my_plugin_detail(cb: CallbackQuery, state: FSMContext) -> None:
    slug = decode_slug(cb.data.split(":", 1)[1])
    lang = await get_language(cb, state)

    plugin = find_plugin_by_slug(slug)
    if not plugin:
        await cb.answer(t("not_found", lang), show_alert=True)
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
    await cb.answer()


@router.callback_query(F.data.startswith("sub:toggle:"))
async def on_toggle_subscription(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    parts = cb.data.split(":", 3)
    slug = decode_slug(parts[2])
    source = parts[3] if len(parts) > 3 else "catalog"

    if not cb.from_user:
        await cb.answer()
        return

    if is_subscribed(cb.from_user.id, ALL_SUBSCRIPTION_KEY):
        await cb.answer(t("btn_notify_all_on", lang), show_alert=True)
        await _show_subscriptions(cb, state, page=0)
        return

    if is_subscribed(cb.from_user.id, slug):
        remove_subscription(cb.from_user.id, slug)
        await cb.answer(t("unsubscribed", lang))
    else:
        add_subscription(cb.from_user.id, slug)
        await cb.answer(t("subscribed", lang))

    plugin = find_plugin_by_slug(slug)
    if not plugin:
        return

    back = "catalog" if source == "catalog" else "my:plugins:0"
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
    await answer(cb, t("search_prompt", lang), search_kb(lang), "catalog")
    await cb.answer()


@router.message(UserFlow.searching)
async def on_search_query(message: Message, state: FSMContext) -> None:
    lang = await get_language(message, state)
    query = (message.text or "").strip()

    if not query:
        await message.answer(t("need_text", lang))
        return

    results = search_plugins(query, limit=10)
    await state.set_state(UserFlow.idle)

    if not results:
        await answer(message, t("search_empty", lang), search_kb(lang, True), "catalog")
        return

    items = []
    for plugin in results:
        slug = plugin.get("slug")
        if slug:
            locale = plugin.get(lang) or plugin.get("ru") or {}
            name = locale.get("name") or slug
            items.append((name, f"plugin:{encode_slug(slug)}"))

    text = t("search_results", lang, count=len(results))
    await state.update_data(catalog_back="search:0")
    await answer(message, text, paginated_list_kb(items, 0, 1, "search", "catalog", lang=lang), "catalog")


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
    await cb.answer()


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

    await state.update_data(
        my_plugins=[plugin.get("slug") for plugin in user_plugins],
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


async def _show_subscriptions(target: CallbackQuery, state: FSMContext, page: int) -> None:
    lang = await get_language(target, state)
    user = target.from_user
    if not user:
        await target.answer()
        return

    slugs = list_subscriptions(user.id)
    all_enabled = ALL_SUBSCRIPTION_KEY in slugs
    if all_enabled:
        slugs = [ALL_SUBSCRIPTION_KEY]
    if not slugs:
        await answer(target, t("subscriptions_empty", lang), search_kb(lang), "profile")
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

    caption = f"{t('subscriptions_title', lang)}\n{t('catalog_page', lang, current=page + 1, total=total_pages)}"
    await state.update_data(catalog_back="profile:subscriptions")

    kb = paginated_list_kb(items, page, total_pages, "subs:page", "profile", lang=lang)
    toggle_label = t("btn_notify_all_on", lang) if all_enabled else t("btn_notify_all_off", lang)
    kb.inline_keyboard.insert(
        0,
        [
            InlineKeyboardButton(
                text=toggle_label,
                callback_data=f"subs:all_toggle:{page}",
                style="success",
            )
        ],
    )
    await answer(target, caption, kb, "profile")
    await target.answer()


@router.callback_query(F.data.startswith("my:"))
async def on_my_items(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    kind = parts[1]
    page = int(parts[2])
    lang = await get_language(cb, state)

    await state.update_data(catalog_back=f"my:{kind}:{page}")

    data = await state.get_data()
    slugs: List[str] = data.get(f"my_{kind}", [])

    if not slugs:
        await cb.answer(t("catalog_empty", lang), show_alert=True)
        return

    total = len(slugs)
    total_pages = math.ceil(total / PAGE_SIZE)
    start = page * PAGE_SIZE
    page_slugs = slugs[start : start + PAGE_SIZE]

    items = []
    for slug in page_slugs:
        if kind == "plugins":
            entity = find_plugin_by_slug(slug)
        else:
            entity = find_icon_by_slug(slug)

        if entity:
            locale = entity.get(lang) or entity.get("ru") or {}
            name = locale.get("name") or slug
            if kind == "plugins":
                items.append((name, f"myplugin:{encode_slug(slug)}"))
            else:
                items.append((name, f"icon:{encode_slug(slug)}"))

    title = t("icons_title" if kind == "icons" else "catalog_title", lang)
    caption = f"{title}\n{t('catalog_page', lang, current=page + 1, total=total_pages)}"
    await answer(cb, caption, paginated_list_kb(items, page, total_pages, f"my:{kind}", "profile", lang=lang), "profile")
    await cb.answer()


@router.inline_query()
async def on_inline(query: InlineQuery) -> None:
    text = (query.query or "").strip()
    lang = get_lang(query.from_user.id if query.from_user else None)

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

    plugins = search_plugins(text, limit=10)
    icons = search_icons(text, limit=10)

    if not plugins and not icons:
        await query.answer([], cache_time=60)
        return

    results = []
    for plugin in plugins:
        slug = plugin.get("slug")
        if not slug:
            continue

        locale = plugin.get(lang) or plugin.get("ru") or {}
        name = locale.get("name") or slug
        preview = build_inline_preview(plugin, lang, "plugin")
        description = strip_html(locale.get("description") or t("catalog_inline_no_description", lang))
        link = plugin.get("channel_message", {}).get("link")
        reply_markup = None
        deeplink = f"tg://resolve?domain={BOT_USERNAME}&start={slug}"
        if link:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[[ 
                    InlineKeyboardButton(text=t("catalog_inline_download", lang), url=link, style="success"),
                    InlineKeyboardButton(text=t("catalog_inline_open_in_bot", lang), url=deeplink),
                ]]
            )
        else:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=t("catalog_inline_open_in_bot", lang), url=deeplink)]]
            )

        results.append(
            InlineQueryResultArticle(
                id=f"plugin:{encode_slug(slug)}",
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

    for icon in icons:
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
                id=f"icon:{encode_slug(slug)}",
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
