from typing import List, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.texts import t


def language_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
        InlineKeyboardButton(text="🇺🇸 English", callback_data="lang:en"),
    ]])


def main_menu_kb(lang: str) -> InlineKeyboardMarkup:
    catalog = "📚 Каталог" if lang == "ru" else "📚 Catalog"
    submit = "📝 Предложить" if lang == "ru" else "📝 Submit"
    profile = "👤 Профиль" if lang == "ru" else "👤 Profile"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=catalog, callback_data="catalog")],
        [InlineKeyboardButton(text=submit, callback_data="submit")],
        [InlineKeyboardButton(text=profile, callback_data="profile")],
    ])


def submit_type_kb(lang: str) -> InlineKeyboardMarkup:
    plugin = "🧩 Новый плагин" if lang == "ru" else "🧩 New plugin"
    icons = "🎨 Пак иконок" if lang == "ru" else "🎨 Icon pack"
    idea = t("btn_idea", lang)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=plugin, callback_data="submit:plugin")],
        [InlineKeyboardButton(text=icons, callback_data="submit:icons")],
        [InlineKeyboardButton(text=idea, url="https://t.me/exteraForum")],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="home")],
    ])


def cancel_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="cancel"),
    ]])


def categories_kb(categories: list, lang: str) -> InlineKeyboardMarkup:
    buttons = []
    for cat in categories:
        label = cat.get(lang) or cat.get("ru") or cat.get("key")
        buttons.append(InlineKeyboardButton(
            text=label,
            callback_data=f"cat:{cat.get('key')}",
        ))
    
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="cancel")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def comment_skip_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_skip", lang), callback_data="comment:skip"),
    ]])


def confirm_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_confirm", lang), callback_data="confirm"),
            InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="cancel"),
        ],
    ])


def draft_edit_kb(
    prefix: str,
    submit_label: str,
    include_back: bool = False,
    include_cancel: bool = False,
    include_checked_on: bool = True,
    include_delete: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Название", callback_data=f"{prefix}:edit:name"),
            InlineKeyboardButton(text="Автор", callback_data=f"{prefix}:edit:author"),
        ],
        [
            InlineKeyboardButton(text="Описание", callback_data=f"{prefix}:edit:description"),
            InlineKeyboardButton(text="Использование", callback_data=f"{prefix}:edit:usage"),
        ],
        [
            InlineKeyboardButton(text="Настройки", callback_data=f"{prefix}:edit:settings"),
            InlineKeyboardButton(text="Мин. версия", callback_data=f"{prefix}:edit:min_version"),
        ],
    ]
    if include_checked_on:
        rows.append([InlineKeyboardButton(text="Проверено", callback_data=f"{prefix}:edit:checked_on")])
    rows.append([InlineKeyboardButton(text="Категория", callback_data=f"{prefix}:edit:category")])
    rows.append([InlineKeyboardButton(text=submit_label, callback_data=f"{prefix}:submit")])
    if include_delete:
        rows.append([InlineKeyboardButton(text=t("btn_delete", "ru"), callback_data=f"{prefix}:delete")])
    if include_back:
        rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"{prefix}:back")])
    if include_cancel:
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def draft_lang_kb(prefix: str, field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 RU", callback_data=f"{prefix}:lang:{field}:ru"),
            InlineKeyboardButton(text="🇺🇸 EN", callback_data=f"{prefix}:lang:{field}:en"),
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"{prefix}:back")],
    ])


def description_lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="desc_lang:ru"),
            InlineKeyboardButton(text="🇺🇸 English", callback_data="desc_lang:en"),
        ],
    ])


def draft_category_kb(prefix: str, categories: list) -> InlineKeyboardMarkup:
    buttons = []
    for cat in categories:
        label = cat.get("ru") or cat.get("en") or cat.get("key")
        buttons.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"{prefix}:cat:{cat.get('key')}",
            )
        )

    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"{prefix}:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_plugins_kb(plugins: List[Tuple[str, str]], lang: str) -> InlineKeyboardMarkup:
    rows = []
    for name, slug in plugins:
        rows.append([InlineKeyboardButton(text=f"🧩 {name}", callback_data=f"upd:{slug}")])
    
    rows.append([InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_main_kb(categories: list, lang: str) -> InlineKeyboardMarkup:
    rows = []
    
    rows.append([InlineKeyboardButton(text=t("btn_search", lang), callback_data="search")])
    
    all_label = "📦 Все плагины" if lang == "ru" else "📦 All plugins"
    rows.append([InlineKeyboardButton(text=all_label, callback_data="cat:_all:0")])
    
    cat_buttons = []
    for cat in categories:
        label = cat.get(lang) or cat.get("ru")
        cat_buttons.append(InlineKeyboardButton(
            text=label,
            callback_data=f"cat:{cat.get('key')}:0",
        ))
    rows.extend([cat_buttons[i:i+2] for i in range(0, len(cat_buttons), 2)])
    
    icons_label = "🎨 Иконки" if lang == "ru" else "🎨 Icons"
    rows.append([InlineKeyboardButton(text=icons_label, callback_data="icons:0")])
    
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="home")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def paginated_list_kb(
    items: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    nav_prefix: str,
    back_callback: str,
) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in items]
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{nav_prefix}:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{nav_prefix}:{page+1}"))
    
    if nav:
        rows.append(nav)
    
    rows.append([InlineKeyboardButton(text="🔙", callback_data=back_callback)])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plugin_detail_kb(
    link: Optional[str],
    back: str,
    lang: str,
    update_callback: Optional[str] = None,
    delete_callback: Optional[str] = None,
) -> InlineKeyboardMarkup:
    rows = []
    if link:
        rows.append([InlineKeyboardButton(text=t("btn_open", lang), url=link)])
    if update_callback:
        rows.append([InlineKeyboardButton(text=t("btn_update", lang), callback_data=update_callback)])
    if delete_callback:
        rows.append([InlineKeyboardButton(text=t("btn_delete", lang), callback_data=delete_callback)])
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_kb(lang: str, show_retry: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if show_retry:
        rows.append([InlineKeyboardButton(text=t("btn_retry", lang), callback_data="search")])
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="catalog")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_kb(lang: str, has_plugins: bool, has_icons: bool) -> InlineKeyboardMarkup:
    rows = []
    
    if has_plugins:
        label = "🧩 Мои плагины" if lang == "ru" else "🧩 My plugins"
        rows.append([InlineKeyboardButton(text=label, callback_data="my:plugins:0")])
    
    if has_icons:
        label = "🎨 Мои паки" if lang == "ru" else "🎨 My packs"
        rows.append([InlineKeyboardButton(text=label, callback_data="my:icons:0")])

    rows.append([InlineKeyboardButton(text=t("btn_support", lang), url="https://t.me/itsv2eds")])
    
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="home")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📥 Заявки", callback_data="adm:queue:all:0"),
        ],
        [
            InlineKeyboardButton(text="🧩 Редактировать", callback_data="adm:edit_plugins"),
            InlineKeyboardButton(text="👤 Привязать автора", callback_data="adm:link_author"),
        ],
        [
            InlineKeyboardButton(text="🚫 Заблокированные", callback_data="adm:banned:0"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="adm:config"),
        ],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="adm:broadcast")],
    ])


def admin_config_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Админы", callback_data="adm:config:admins")],
        [InlineKeyboardButton(text="📣 Канал", callback_data="adm:config:channel")],
        [InlineKeyboardButton(text="🔙", callback_data="adm:menu")],
    ])


def admin_broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="adm:broadcast:confirm")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="adm:broadcast:cancel")],
    ])


def admin_queue_kb(
    items: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    queue_type: str,
    back_callback: str = "adm:menu",
) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"adm:review:{rid}")] for label, rid in items]
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm:queue:{queue_type}:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm:queue:{queue_type}:{page+1}"))
    
    if nav:
        rows.append(nav)
    
    rows.append([InlineKeyboardButton(text="🔙", callback_data=back_callback)])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_banned_kb(
    items: List[Tuple[str, int]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows = []
    for label, user_id in items:
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"adm:user_info:{user_id}"),
            InlineKeyboardButton(text="🔓", callback_data=f"adm:unban:{user_id}"),
        ])
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm:banned:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm:banned:{page+1}"))
    
    if nav:
        rows.append(nav)
    
    rows.append([InlineKeyboardButton(text="🔙", callback_data=back_callback)])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_review_kb(
    request_id: str,
    user_id: int,
    submit_label: str = "✅ Опубликовать",
    submit_callback: str | None = None,
) -> InlineKeyboardMarkup:
    submit_callback = submit_callback or f"adm:prepublish:{request_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=submit_label, callback_data=submit_callback),
            InlineKeyboardButton(text="⚙️ Ещё...", callback_data=f"adm:actions:{request_id}:{user_id}"),
        ],
        [InlineKeyboardButton(text="🔙", callback_data="adm:menu")],
    ])


def admin_actions_kb(request_id: str, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm:reject:{request_id}"),
            InlineKeyboardButton(text="🚫 Забанить", callback_data=f"adm:ban:{request_id}:{user_id}"),
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"adm:back_review:{request_id}:{user_id}")],
    ])


def admin_reject_kb(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📝 С причиной", callback_data=f"adm:reject_comment:{request_id}"),
            InlineKeyboardButton(text="🔇 Тихо", callback_data=f"adm:reject_silent:{request_id}"),
        ],
        [InlineKeyboardButton(text="🔙", callback_data=f"adm:menu")],
    ])


def admin_confirm_ban_kb(request_id: str, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Подтвердить бан", callback_data=f"adm:ban_confirm:{request_id}:{user_id}")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"adm:back_review:{request_id}:{user_id}")],
    ])


def admin_plugins_list_kb(
    plugins: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    select_prefix: str = "adm:select_plugin",
    list_prefix: str = "adm:plugins_list",
    back_callback: str = "adm:menu",
) -> InlineKeyboardMarkup:
    rows = []
    for name, slug in plugins:
        rows.append([InlineKeyboardButton(text=f"🧩 {name}", callback_data=f"{select_prefix}:{slug}")])
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{list_prefix}:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{list_prefix}:{page+1}"))
    
    if nav:
        rows.append(nav)
    
    rows.append([InlineKeyboardButton(text="🔙", callback_data="adm:menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)