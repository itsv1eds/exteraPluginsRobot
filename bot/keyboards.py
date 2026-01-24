from typing import Iterable, Mapping, Optional, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


LANGUAGE_OPTIONS = {
    "ru": "🇷🇺 Русский",
    "en": "🇺🇸 English",
}


MAIN_MENU_BUTTONS = {
    "profile": {"ru": "👤 Профиль", "en": "👤 Profile"},
    "catalog": {"ru": "📚 Каталог", "en": "📚 Catalog"},
    "submit": {"ru": "📝 Отправить плагин/пак", "en": "📝 Submit plugin/icon pack"},
}

SUBMISSION_TYPE_BUTTONS = {
    "plugin": {"ru": "🧩 Плагин", "en": "🧩 Plugin"},
    "icon_pack": {"ru": "🎨 Пак иконок", "en": "🎨 Icon pack"},
}

SUBMISSION_ACTION_BUTTONS = {
    "confirm": {"ru": "✅ Отправить", "en": "✅ Send"},
    "cancel": {"ru": "↩️ Назад", "en": "↩️ Back"},
}

PROFILE_SECTION_BUTTONS = {
    "plugins": {"ru": "🧩 Мои плагины", "en": "🧩 My plugins"},
    "icon_packs": {"ru": "🎨 Мои паки", "en": "🎨 My icon packs"},
}

PROFILE_ITEM_ACTIONS = {
    "update": {"ru": "Обновить", "en": "Update"},
}

CATALOG_SEARCH_BUTTON = {"ru": "🔍 Поиск", "en": "🔍 Search"}
CATALOG_SEARCH_ACTIONS = {
    "retry": {"ru": "🔄 Новый поиск", "en": "🔄 New search"},
    "cancel": {"ru": "↩️ Каталог", "en": "↩️ Catalog"},
}

EDIT_FIELD_BUTTONS = {
    "file": {"ru": "📁 Файл", "en": "📁 File"},
    "description": {"ru": "📝 Описание", "en": "📝 Description"},
    "usage": {"ru": "⚙️ Использование", "en": "⚙️ Usage"},
    "channel": {"ru": "📣 Канал", "en": "📣 Channel"},
    "category": {"ru": "🏷 Категория", "en": "🏷 Category"},
}

DRAFT_EDITOR_BUTTONS = {
    "name_ru": {"ru": "🇷🇺 Название", "en": "🇷🇺 Name"},
    "name_en": {"ru": "🇺🇸 Название", "en": "🇺🇸 Name"},
    "description_ru": {"ru": "🇷🇺 Описание", "en": "🇷🇺 Description"},
    "description_en": {"ru": "🇺🇸 Описание", "en": "🇺🇸 Description"},
    "usage_ru": {"ru": "🇷🇺 Использование", "en": "🇷🇺 Usage"},
    "usage_en": {"ru": "🇺🇸 Использование", "en": "🇺🇸 Usage"},
    "author": {"ru": "👤 Автор", "en": "👤 Author"},
    "author_channel": {"ru": "📣 Канал автора", "en": "📣 Author channel"},
    "version": {"ru": "🔢 Версия", "en": "🔢 Version"},
    "min_version": {"ru": "🧩 Минимальная версия", "en": "🧩 Min version"},
    "has_ui": {"ru": "⚙️ Настройки", "en": "⚙️ Settings"},
    "category": {"ru": "🏷 Категория", "en": "🏷 Category"},
    "file": {"ru": "📎 Файл", "en": "📎 File"},
    "has_ui_on": {"ru": "⚙️ Настройки: ✅", "en": "⚙️ Settings: ✅"},
    "has_ui_off": {"ru": "⚙️ Настройки: ❌", "en": "⚙️ Settings: ❌"},
}

def _t(options: Mapping[str, str], language: str) -> str:
    return options.get(language) or options.get("ru") or next(iter(options.values()))


def _single_column(buttons: Sequence[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button] for button in buttons])


def language_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"lang:{code}")
        for code, label in LANGUAGE_OPTIONS.items()
    ]
    return _single_column(buttons)


def main_menu_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=_t(MAIN_MENU_BUTTONS["profile"], language), callback_data="menu:profile"),
        InlineKeyboardButton(text=_t(MAIN_MENU_BUTTONS["catalog"], language), callback_data="menu:catalog"),
        InlineKeyboardButton(text=_t(MAIN_MENU_BUTTONS["submit"], language), callback_data="menu:submit"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[buttons[0], buttons[1]], [buttons[2]]])


def submission_type_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=_t(SUBMISSION_TYPE_BUTTONS["plugin"], language),
            callback_data="submit:type:plugin",
        ),
        InlineKeyboardButton(
            text=_t(SUBMISSION_TYPE_BUTTONS["icon_pack"], language),
            callback_data="submit:type:icon_pack",
        ),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[buttons[0], buttons[1]]])


def category_keyboard(options: Iterable[Mapping[str, str]], language: str) -> InlineKeyboardMarkup:
    def _label(option: Mapping[str, str]) -> str:
        return (
            option.get(language)
            or option.get("ru")
            or option.get("en")
            or option.get("key", "Категория")
        )

    buttons = [
        InlineKeyboardButton(
            text=_label(option),
            callback_data=f"category:{option['key']}",
        )
        for option in options
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(SUBMISSION_ACTION_BUTTONS["confirm"], language),
                    callback_data="submission:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(SUBMISSION_ACTION_BUTTONS["cancel"], language),
                    callback_data="submission:cancel",
                )
            ],
        ]
    )


def profile_menu_keyboard(language: str, plugin_count: int, icon_count: int) -> InlineKeyboardMarkup:
    plugin_label = f"{_t(PROFILE_SECTION_BUTTONS['plugins'], language)} ({plugin_count})"
    icon_label = f"{_t(PROFILE_SECTION_BUTTONS['icon_packs'], language)} ({icon_count})"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=plugin_label, callback_data="profile:list:plugin:0")],
            [InlineKeyboardButton(text=icon_label, callback_data="profile:list:icon_pack:0")],
            [InlineKeyboardButton(text=_t(SUBMISSION_ACTION_BUTTONS["cancel"], language), callback_data="menu:home")],
        ]
    )


def profile_items_keyboard(
    kind: str,
    items: list[tuple[str, str]],
    page: int,
    has_prev: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    inline_keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=label, callback_data=f"profile:item:{kind}:{request_id}")]
        for label, request_id in items
    ]
    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"profile:page:{kind}:{page-1}",
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"profile:page:{kind}:{page+1}",
            )
        )
    if nav:
        inline_keyboard.append(nav)
    inline_keyboard.append([InlineKeyboardButton(text="↩️", callback_data="menu:profile")])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def profile_item_actions_keyboard(language: str, request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_t(PROFILE_ITEM_ACTIONS["update"], language), callback_data=f"profile:update:{request_id}")],
            [InlineKeyboardButton(text="↩️", callback_data="menu:profile")],
        ]
    )


def edit_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text=_t(EDIT_FIELD_BUTTONS["file"], language), callback_data="edit:field:file"),
            InlineKeyboardButton(text=_t(EDIT_FIELD_BUTTONS["description"], language), callback_data="edit:field:description"),
        ],
        [
            InlineKeyboardButton(text=_t(EDIT_FIELD_BUTTONS["usage"], language), callback_data="edit:field:usage"),
            InlineKeyboardButton(text=_t(EDIT_FIELD_BUTTONS["channel"], language), callback_data="edit:field:channel"),
        ],
        [
            InlineKeyboardButton(text=_t(EDIT_FIELD_BUTTONS["category"], language), callback_data="edit:field:category"),
        ],
        [
            InlineKeyboardButton(text=_t(SUBMISSION_ACTION_BUTTONS["confirm"], language), callback_data="edit:submit"),
        ],
        [
            InlineKeyboardButton(text=_t(SUBMISSION_ACTION_BUTTONS["cancel"], language), callback_data="edit:cancel"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def edit_back_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️", callback_data="edit:menu")],
            [InlineKeyboardButton(text=_t(SUBMISSION_ACTION_BUTTONS["cancel"], language), callback_data="edit:cancel")],
        ]
    )


def catalog_categories_keyboard(categories: Iterable[Mapping[str, str]], language: str) -> InlineKeyboardMarkup:
    def _label(cat: Mapping[str, str]) -> str:
        return (
            cat.get(language)
            or cat.get("ru")
            or cat.get("en")
            or cat.get("key", "Категория")
        )

    buttons = [
        InlineKeyboardButton(text=_label(cat), callback_data=f"catalog:category:{cat['key']}:0")
        for cat in categories
    ]
    rows = [[InlineKeyboardButton(text=_t(CATALOG_SEARCH_BUTTON, language), callback_data="catalog:search")]]
    rows.extend([buttons[i : i + 2] for i in range(0, len(buttons), 2)])
    icon_text = "🎨 Иконки" if language == "ru" else "🎨 Icon packs"
    rows.append([InlineKeyboardButton(text=icon_text, callback_data="icons:list:0")])
    back_text = "↩️ Главное меню" if language == "ru" else "↩️ Main menu"
    rows.append([InlineKeyboardButton(text=back_text, callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_search_prompt_keyboard(language: str, include_retry: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if include_retry:
        rows.append([
            InlineKeyboardButton(
                text=_t(CATALOG_SEARCH_ACTIONS["retry"], language), callback_data="catalog:search:again"
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text=_t(CATALOG_SEARCH_ACTIONS["cancel"], language), callback_data="catalog:search:cancel"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_search_results_keyboard(
    results: list[tuple[str, str]],
    language: str,
) -> InlineKeyboardMarkup:
    inline_keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=label, callback_data=callback_data)]
        for label, callback_data in results
    ]
    inline_keyboard.append(
        [InlineKeyboardButton(text=_t(CATALOG_SEARCH_ACTIONS["retry"], language), callback_data="catalog:search:again")]
    )
    inline_keyboard.append(
        [InlineKeyboardButton(text=_t(CATALOG_SEARCH_ACTIONS["cancel"], language), callback_data="catalog:search:cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def catalog_navigation_keyboard(
    prefix: str,
    category_key: str,
    page: int,
    has_prev: bool,
    has_next: bool,
    mode: str = "category",
) -> list[InlineKeyboardButton]:
    buttons = []
    if has_prev:
        buttons.append(
            InlineKeyboardButton(text="⬅️", callback_data=_catalog_nav_callback(prefix, category_key, page - 1, mode))
        )
    if has_next:
        buttons.append(
            InlineKeyboardButton(text="➡️", callback_data=_catalog_nav_callback(prefix, category_key, page + 1, mode))
        )
    return buttons


def _catalog_nav_callback(prefix: str, category_key: str, page: int, mode: str) -> str:
    if mode == "list":
        return f"{prefix}:list:{page}"
    return f"{prefix}:category:{category_key}:{page}"


def catalog_items_keyboard(
    category_key: str,
    items: list[tuple[str, str]],
    page: int,
    has_prev: bool,
    has_next: bool,
    prefix: str,
    back_callback: str,
    nav_mode: str = "category",
) -> InlineKeyboardMarkup:
    inline_keyboard = [
        [InlineKeyboardButton(text=label, callback_data=f"{prefix}:item:{slug}")]
        for label, slug in items
    ]
    nav = catalog_navigation_keyboard(prefix, category_key, page, has_prev, has_next, nav_mode)
    if nav:
        inline_keyboard.append(nav)
    inline_keyboard.append([InlineKeyboardButton(text="↩️", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def catalog_plugin_keyboard(link: Optional[str], back_callback: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if link:
        rows.append([InlineKeyboardButton(text="🔗 Открыть пост", url=link)])
    rows.append([InlineKeyboardButton(text="↩️", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Новые заявки", callback_data="admin:list:new:0")],
            [InlineKeyboardButton(text="Обновления", callback_data="admin:list:update:0")],
        ]
    )


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return admin_menu_inline()


def admin_queue_keyboard(
    queue_type: str,
    items: list[tuple[str, str]],
    page: int,
    has_prev: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    inline_keyboard = [
        [InlineKeyboardButton(text=label, callback_data=f"admin:open:{request_id}")]
        for label, request_id in items
    ]

    buttons = []
    if has_prev:
        buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:list:{queue_type}:{page-1}")
        )
    if has_next:
        buttons.append(
            InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"admin:list:{queue_type}:{page+1}")
        )
    if buttons:
        inline_keyboard.append(buttons)

    inline_keyboard.append([InlineKeyboardButton(text="↩️ Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def review_actions_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{request_id}"),
                InlineKeyboardButton(text="✏️ Вернуть", callback_data=f"revise:{request_id}"),
            ],
            [InlineKeyboardButton(text="↩️ В меню", callback_data="admin:menu")],
        ]
    )


def publish_actions_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Опубликовать", callback_data=f"publish:{request_id}"),],
            [InlineKeyboardButton(text="↩️ В меню", callback_data="admin:menu")],
        ]
    )
