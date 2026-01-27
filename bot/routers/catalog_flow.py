import math
from typing import Any, Dict, List

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)

from bot.cache import get_categories, get_icons
from bot.constants import PAGE_SIZE
from bot.context import get_language, get_lang
from bot.helpers import answer, strip_html
from bot.keyboards import (
    catalog_main_kb,
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
    search_plugins,
)

router = Router(name="catalog-flow")


def build_plugin_preview(entry: Dict[str, Any], lang: str) -> str:
    locale = entry.get(lang) or entry.get("ru") or entry.get("en") or {}
    authors = entry.get("authors", {})

    name = locale.get("name") or entry.get("slug", "?")
    desc = locale.get("description") or "—"
    author = authors.get(lang) or authors.get("ru") or "—"
    min_ver = locale.get("min_version") or entry.get("requirements", {}).get("min_version") or ""
    link = entry.get("channel_message", {}).get("link")

    if lang == "ru":
        lines = [
            f"<b>Название:</b> {name}",
            f"<b>Автор:</b> {author}",
            f"<b>Описание:</b> {desc}",
        ]
        if min_ver:
            lines.append(f"<b>Минимальная версия:</b> {min_ver}")
        if link:
            lines.append(f"<b>Ссылка:</b> {link}")
    else:
        lines = [
            f"<b>Title:</b> {name}",
            f"<b>Author:</b> {author}",
            f"<b>Description:</b> {desc}",
        ]
        if min_ver:
            lines.append(f"<b>Min.version:</b> {min_ver}")
        if link:
            lines.append(f"<b>Link:</b> {link}")

    return "\n".join(lines)


@router.callback_query(F.data == "catalog")
async def on_catalog(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_language(cb, state)
    await answer(cb, t("catalog_title", lang), catalog_main_kb(get_categories(), lang), "catalog")
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
        items.append((f"🧩 {name}", f"plugin:{slug}"))

    if cat_key == "_all":
        title = "📦 Все плагины" if lang == "ru" else "📦 All plugins"
    else:
        category = next((c for c in get_categories() if c.get("key") == cat_key), None)
        title = category.get(lang) if category else cat_key

    caption = f"<b>{title}</b>\n{t('catalog_page', lang, current=page + 1, total=total_pages)}"
    await answer(cb, caption, paginated_list_kb(items, page, total_pages, f"cat:{cat_key}", "catalog"), "plugins")
    await cb.answer()


@router.callback_query(F.data.startswith("plugin:"))
async def on_plugin_detail(cb: CallbackQuery, state: FSMContext) -> None:
    slug = cb.data.split(":", 1)[1]
    lang = await get_language(cb, state)

    plugin = find_plugin_by_slug(slug)
    if not plugin:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    text = build_plugin_preview(plugin, lang)
    link = plugin.get("channel_message", {}).get("link")
    await answer(cb, text, plugin_detail_kb(link, "catalog", lang), "plugins")
    await cb.answer()


@router.callback_query(F.data.startswith("myplugin:"))
async def on_my_plugin_detail(cb: CallbackQuery, state: FSMContext) -> None:
    slug = cb.data.split(":", 1)[1]
    lang = await get_language(cb, state)

    plugin = find_plugin_by_slug(slug)
    if not plugin:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    text = build_plugin_preview(plugin, lang)
    link = plugin.get("channel_message", {}).get("link")
    await answer(
        cb,
        text,
        plugin_detail_kb(
            link,
            "my:plugins:0",
            lang,
            update_callback=f"profile:update:{slug}",
            delete_callback=f"profile:delete:{slug}",
        ),
        "profile",
    )
    await cb.answer()


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
            items.append((f"🧩 {name}", f"plugin:{slug}"))

    text = t("search_results", lang, count=len(results))
    await answer(message, text, paginated_list_kb(items, 0, 1, "search", "catalog"), "catalog")


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
        items.append((f"🎨 {name}", f"icon:{slug}"))

    caption = f"{t('icons_title', lang)}\n{t('catalog_page', lang, current=page + 1, total=total_pages)}"
    await answer(cb, caption, paginated_list_kb(items, page, total_pages, "icons", "catalog"), "iconpacks")
    await cb.answer()


@router.callback_query(F.data.startswith("icon:"))
async def on_icon_detail(cb: CallbackQuery, state: FSMContext) -> None:
    slug = cb.data.split(":", 1)[1]
    lang = await get_language(cb, state)

    icon = find_icon_by_slug(slug)
    if not icon:
        await cb.answer(t("not_found", lang), show_alert=True)
        return

    text = build_plugin_preview(icon, lang)
    link = icon.get("channel_message", {}).get("link")
    await answer(cb, text, plugin_detail_kb(link, "icons:0", lang), "iconpacks")
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

    await answer(cb, text, profile_kb(lang, bool(user_plugins), bool(user_icons)), "profile")
    await cb.answer()


@router.callback_query(F.data.startswith("my:"))
async def on_my_items(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    kind = parts[1]
    page = int(parts[2])
    lang = await get_language(cb, state)

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
            icon = "🧩"
        else:
            entity = find_icon_by_slug(slug)
            icon = "🎨"

        if entity:
            locale = entity.get(lang) or entity.get("ru") or {}
            name = locale.get("name") or slug
            if kind == "plugins":
                items.append((f"{icon} {name}", f"myplugin:{slug}"))
            else:
                items.append((f"{icon} {name}", f"icon:{slug}"))

    title = t("icons_title" if kind == "icons" else "catalog_title", lang)
    caption = f"{title}\n{t('catalog_page', lang, current=page + 1, total=total_pages)}"
    await answer(cb, caption, paginated_list_kb(items, page, total_pages, f"my:{kind}", "profile"), "profile")
    await cb.answer()


@router.inline_query()
async def on_inline(query: InlineQuery) -> None:
    text = (query.query or "").strip()
    lang = get_lang(query.from_user.id if query.from_user else None)

    plugins = search_plugins(text, limit=10)

    if not plugins:
        await query.answer([], cache_time=60)
        return

    results = []
    for plugin in plugins:
        slug = plugin.get("slug")
        if not slug:
            continue

        locale = plugin.get(lang) or plugin.get("ru") or {}
        name = locale.get("name") or slug
        preview = build_plugin_preview(plugin, lang)

        results.append(
            InlineQueryResultArticle(
                id=slug,
                title=f"🧩 {name}",
                description=strip_html(preview)[:100],
                input_message_content=InputTextMessageContent(message_text=preview, parse_mode=ParseMode.HTML),
            )
        )

    await query.answer(results, cache_time=60, is_personal=True)
