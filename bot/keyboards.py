from typing import List, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.callback_tokens import encode_slug
from bot.texts import t


def language_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
        InlineKeyboardButton(text="🇺🇸 English", callback_data="lang:en"),
    ]])


def main_menu_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_catalog", lang), callback_data="catalog")],
        [InlineKeyboardButton(text=t("btn_submit", lang), callback_data="submit", style="success")],
        [InlineKeyboardButton(text=t("btn_profile", lang), callback_data="profile")],
    ])


def submit_type_kb(lang: str) -> InlineKeyboardMarkup:
    idea = t("btn_idea", lang)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_new_plugin", lang), callback_data="submit:plugin")],
        [InlineKeyboardButton(text=t("btn_icon_pack", lang), callback_data="submit:icons")],
        [InlineKeyboardButton(text=idea, url="https://t.me/exteraForum")],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="home", style="danger")],
    ])


def cancel_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="cancel"),
    ]])


def admin_cancel_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="adm:cancel")]])


def categories_kb(categories: list, lang: str) -> InlineKeyboardMarkup:
    buttons = []
    for cat in categories:
        label = cat.get(lang) or cat.get("ru") or cat.get("key")
        buttons.append(InlineKeyboardButton(
            text=label,
            callback_data=f"submit:cat:{cat.get('key')}",
        ))
    
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="cancel")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notify_all_kb(lang: str, enabled: bool, back: str = "profile") -> InlineKeyboardMarkup:
    toggle_cb = "profile:notify_all:toggle"
    toggle_label = t("btn_notify_all_on", lang) if enabled else t("btn_notify_all_off", lang)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_label, callback_data=toggle_cb, style="success")],
            [InlineKeyboardButton(text=t("btn_back", lang), callback_data=back, style="danger")],
        ]
    )


def icon_draft_edit_kb(
    prefix: str = "adm_icon",
    submit_label: Optional[str] = None,
    include_schedule: bool = False,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    submit_label = submit_label or t("btn_publish", lang)
    rows = [
        [
            InlineKeyboardButton(text=t("kb_field_name", lang), callback_data=f"{prefix}:edit:name"),
            InlineKeyboardButton(text=t("kb_field_author", lang), callback_data=f"{prefix}:edit:author"),
        ],
        [
            InlineKeyboardButton(text=t("kb_field_version", lang), callback_data=f"{prefix}:edit:version"),
            InlineKeyboardButton(text=t("kb_field_count", lang), callback_data=f"{prefix}:edit:count"),
        ],
        [InlineKeyboardButton(text=submit_label, callback_data=f"{prefix}:submit")],
    ]
    if include_schedule:
        rows.append([InlineKeyboardButton(text=t("btn_schedule", lang), callback_data="adm:schedule")])
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:cancel", style="danger")])
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
    include_file: bool = False,
    include_schedule: bool = False,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=t("kb_field_name", lang), callback_data=f"{prefix}:edit:name"),
            InlineKeyboardButton(text=t("kb_field_author", lang), callback_data=f"{prefix}:edit:author"),
        ],
        [
            InlineKeyboardButton(text=t("kb_field_description", lang), callback_data=f"{prefix}:edit:description"),
            InlineKeyboardButton(text=t("kb_field_usage", lang), callback_data=f"{prefix}:edit:usage"),
        ],
        [
            InlineKeyboardButton(text=t("kb_field_settings", lang), callback_data=f"{prefix}:edit:settings"),
            InlineKeyboardButton(text=t("kb_field_min_version", lang), callback_data=f"{prefix}:edit:min_version"),
        ],
    ]
    if include_checked_on:
        rows.append([InlineKeyboardButton(text=t("kb_field_checked_on", lang), callback_data=f"{prefix}:edit:checked_on")])
    if include_file:
        rows.append([InlineKeyboardButton(text=t("kb_field_file", lang), callback_data=f"{prefix}:edit:file")])
    rows.append([InlineKeyboardButton(text=t("kb_field_category", lang), callback_data=f"{prefix}:edit:category")])
    rows.append([InlineKeyboardButton(text=submit_label, callback_data=f"{prefix}:submit")])
    if include_schedule:
        rows.append([InlineKeyboardButton(text=t("btn_schedule", lang), callback_data="adm:schedule")])
    if include_delete:
        rows.append([InlineKeyboardButton(text=t("btn_delete", lang), callback_data=f"{prefix}:delete")])
    if include_back:
        back_cb = "adm:cancel" if prefix.startswith("adm") else f"{prefix}:back"
        rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data=back_cb, style="danger")])
    if include_cancel:
        cancel_cb = "adm:cancel" if prefix.startswith("adm") else "cancel"
        rows.append([InlineKeyboardButton(text=t("btn_cancel", lang), callback_data=cancel_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def draft_lang_kb(prefix: str, field: str, lang: str = "ru") -> InlineKeyboardMarkup:
    back_cb = "adm:cancel" if prefix.startswith("adm") else f"{prefix}:back"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 RU", callback_data=f"{prefix}:lang:{field}:ru"),
            InlineKeyboardButton(text="🇺🇸 EN", callback_data=f"{prefix}:lang:{field}:en"),
        ],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data=back_cb, style="danger")],
    ])


def description_lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="desc_lang:ru"),
            InlineKeyboardButton(text="🇺🇸 English", callback_data="desc_lang:en"),
        ],
    ])


def draft_category_kb(prefix: str, categories: list, lang: str = "ru") -> InlineKeyboardMarkup:
    buttons = []
    for cat in categories:
        label = cat.get(lang) or cat.get("ru") or cat.get("en") or cat.get("key")
        buttons.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"{prefix}:cat:{cat.get('key')}",
            )
        )

    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    back_cb = "cancel" if prefix.startswith("adm") else f"{prefix}:back"
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data=back_cb, style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_plugins_kb(plugins: List[Tuple[str, str]], lang: str) -> InlineKeyboardMarkup:
    rows = []
    for name, slug in plugins:
        rows.append([InlineKeyboardButton(text=name, callback_data=f"upd:{encode_slug(slug)}")])
    
    rows.append([InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_main_kb(categories: list, lang: str) -> InlineKeyboardMarkup:
    rows = []

    rows.append([
        InlineKeyboardButton(
            text="🔎 " + t("btn_search", lang),
            callback_data="search",
            style="success",
        )
    ])

    rows.append([InlineKeyboardButton(text=t("btn_all_plugins", lang), callback_data="cat:_all:0")])
    
    cat_buttons = []
    for cat in categories:
        label = cat.get(lang) or cat.get("ru")
        cat_buttons.append(InlineKeyboardButton(
            text=label,
            callback_data=f"cat:{cat.get('key')}:0",
        ))
    rows.extend([cat_buttons[i:i+2] for i in range(0, len(cat_buttons), 2)])
    
    rows.append([InlineKeyboardButton(text=t("btn_icons", lang), callback_data="icons:0")])
    
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="home", style="danger")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def paginated_list_kb(
    items: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    nav_prefix: str,
    back_callback: str,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    total_pages = max(total_pages, 1)
    page = min(max(page, 0), total_pages - 1)
    rows = [[InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in items]

    prev_callback = f"{nav_prefix}:{page-1}" if page > 0 else "page:noop"
    next_callback = f"{nav_prefix}:{page+1}" if page < total_pages - 1 else "page:noop"
    page_picker_callback = (
        f"page:picker|{nav_prefix}|{page}|{total_pages}"
        if total_pages > 1
        else "page:noop"
    )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("btn_back", lang) if page > 0 else " ",
                callback_data=prev_callback,
            ),
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data=page_picker_callback,
            ),
            InlineKeyboardButton(
                text=t("btn_forward", lang) if page < total_pages - 1 else " ",
                callback_data=next_callback,
            ),
        ]
    )

    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data=back_callback, style="danger")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def page_picker_kb(
    nav_prefix: str,
    current_page: int,
    total_pages: int,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    total_pages = max(total_pages, 1)
    current_page = min(max(current_page, 0), total_pages - 1)

    rows = []
    page_buttons = []
    for page in range(total_pages):
        label = f"[{page + 1}]" if page == current_page else str(page + 1)
        page_buttons.append(InlineKeyboardButton(text=label, callback_data=f"{nav_prefix}:{page}"))

    for index in range(0, len(page_buttons), 5):
        rows.append(page_buttons[index : index + 5])

    rows.append(
        [
            InlineKeyboardButton(
                text=t("btn_back", lang),
                callback_data=f"{nav_prefix}:{current_page}",
                style="danger",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plugin_detail_kb(
    link: Optional[str],
    back: str,
    lang: str,
    update_callback: Optional[str] = None,
    delete_callback: Optional[str] = None,
    subscribe_callback: Optional[str] = None,
    subscribe_label: Optional[str] = None,
) -> InlineKeyboardMarkup:
    rows = []
    if link:
        rows.append([
            InlineKeyboardButton(
                text=t("btn_open", lang),
                url=link,
                style="success",
            )
        ])
    if update_callback:
        rows.append([InlineKeyboardButton(text=t("btn_update", lang), callback_data=update_callback)])
    if delete_callback:
        rows.append([InlineKeyboardButton(text=t("btn_delete", lang), callback_data=delete_callback)])
    if subscribe_callback:
        label = subscribe_label or t("btn_subscribe", lang)
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=subscribe_callback,
                style="success",
            )
        ])
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data=back, style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_kb(lang: str, show_retry: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if show_retry:
        rows.append([InlineKeyboardButton(text=t("btn_retry", lang), callback_data="search")])
    rows.append([
        InlineKeyboardButton(
            text=t("btn_back", lang),
            callback_data="catalog",
            style="danger",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_kb(
    lang: str,
    has_plugins: bool,
    has_icons: bool,
    notify_all_enabled: bool = False,
) -> InlineKeyboardMarkup:
    rows = []
    
    if has_plugins:
        rows.append([InlineKeyboardButton(text=t("btn_my_plugins", lang), callback_data="my:plugins:0")])
    
    if has_icons:
        rows.append([InlineKeyboardButton(text=t("btn_my_packs", lang), callback_data="my:icons:0")])

    rows.append([
        InlineKeyboardButton(
            text=t("btn_subscriptions", lang),
            callback_data="profile:subscriptions",
        )
    ])

    rows.append([InlineKeyboardButton(text=t("btn_support", lang), url="https://t.me/itsv2eds")])
    
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="home", style="danger")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu_kb(role: str | None = None, lang: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    if role in {"super", None}:
        rows.append([
            InlineKeyboardButton(text=t("admin_btn_plugins", lang), callback_data="adm:section:plugins"),
            InlineKeyboardButton(text=t("admin_btn_icons", lang), callback_data="adm:section:icons"),
        ])
        rows.append([
            InlineKeyboardButton(text=t("admin_btn_post", lang), callback_data="adm:post"),
            InlineKeyboardButton(text=t("admin_btn_broadcast", lang), callback_data="adm:broadcast"),
        ])
        rows.append([
            InlineKeyboardButton(text=t("admin_btn_banned", lang), callback_data="adm:banned:0"),
            InlineKeyboardButton(text=t("admin_btn_stats", lang), callback_data="adm:stats"),
        ])
        rows.append([
            InlineKeyboardButton(text=t("admin_btn_config", lang), callback_data="adm:config"),
        ])
    elif role == "plugins":
        rows.append([
            InlineKeyboardButton(text=t("admin_btn_plugins", lang), callback_data="adm:section:plugins"),
        ])
        rows.append([
            InlineKeyboardButton(text=t("admin_btn_stats", lang), callback_data="adm:stats"),
        ])
    else:
        rows.append([
            InlineKeyboardButton(text=t("admin_btn_icons", lang), callback_data="adm:section:icons"),
        ])
        rows.append([
            InlineKeyboardButton(text=t("admin_btn_stats", lang), callback_data="adm:stats"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_plugins_section_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("admin_btn_requests", lang), callback_data="adm:queue:plugins:0"),
            InlineKeyboardButton(text=t("admin_btn_updates", lang), callback_data="adm:queue:update:0"),
        ],
        [
            InlineKeyboardButton(text=t("admin_btn_edit_plugins", lang), callback_data="adm:edit_plugins"),
            InlineKeyboardButton(text=t("admin_btn_link_author_search", lang), callback_data="adm:link_author"),
        ],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:menu", style="danger")],
    ])


def admin_icons_section_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("admin_btn_requests", lang), callback_data="adm:queue:icons:0")],
        [
            InlineKeyboardButton(text=t("admin_btn_edit_icons", lang), callback_data="adm:edit_icons"),
            InlineKeyboardButton(text=t("admin_btn_link_author_icons", lang), callback_data="adm:link_author_icons"),
        ],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:menu", style="danger")],
    ])


def admin_config_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("admin_cfg_superadmins", lang), callback_data="adm:config:admins_super")],
        [InlineKeyboardButton(text=t("admin_cfg_admins_plugins", lang), callback_data="adm:config:admins_plugins")],
        [InlineKeyboardButton(text=t("admin_cfg_admins_icons", lang), callback_data="adm:config:admins_icons")],
        [InlineKeyboardButton(text=t("admin_cfg_channel", lang), callback_data="adm:config:channel")],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:cancel", style="danger")],
    ])


def admin_manage_admins_kb(field: str, admin_ids: List[int], lang: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    for admin_id in admin_ids:
        rows.append([
            InlineKeyboardButton(text=str(admin_id), callback_data=f"adm:admins:noop:{field}"),
            InlineKeyboardButton(text=t("btn_delete", lang), callback_data=f"adm:admins:rm:{field}:{admin_id}"),
        ])
    rows.append([InlineKeyboardButton(text=t("btn_add", lang), callback_data=f"adm:admins:add:{field}")])
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:cancel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_broadcast_confirm_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_send", lang), callback_data="adm:broadcast:confirm")],
        [InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="adm:cancel")],
    ])


def admin_post_confirm_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_send", lang), callback_data="adm:post:send")],
        [InlineKeyboardButton(text=t("btn_send", lang) + " + " + t("admin_queue_title_updates", lang), callback_data="adm:post:send_updates")],
        [InlineKeyboardButton(text=t("btn_schedule", lang), callback_data="adm:post:schedule")],
        [InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="adm:cancel")],
    ])


def admin_queue_kb(
    items: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    queue_type: str,
    back_callback: str = "adm:cancel",
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"adm:review:{rid}")] for label, rid in items]
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"adm:queue:{queue_type}:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text=">", callback_data=f"adm:queue:{queue_type}:{page+1}"))
    
    if nav:
        rows.append(nav)
    
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data=back_callback, style="danger")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_banned_kb(
    items: List[Tuple[str, int]],
    page: int,
    total_pages: int,
    back_callback: str = "adm:cancel",
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = []
    for label, user_id in items:
        rows.append(
            [
                InlineKeyboardButton(text=label, callback_data=f"adm:user_info:{user_id}"),
                InlineKeyboardButton(text=t("kb_admin_unban", lang), callback_data=f"adm:unban:{user_id}"),
            ]
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"adm:banned:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text=">", callback_data=f"adm:banned:{page+1}"))

    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data=back_callback, style="danger")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_review_kb(
    request_id: str,
    user_id: int,
    submit_label: Optional[str] = None,
    submit_callback: str | None = None,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    submit_callback = submit_callback or f"adm:prepublish:{request_id}"
    submit_label = submit_label or t("btn_publish", lang)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=submit_label, callback_data=submit_callback),
            InlineKeyboardButton(text=t("btn_more", lang), callback_data=f"adm:actions:{request_id}"),
        ],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:cancel", style="danger")],
    ])


def admin_actions_kb(request_id: str, allow_ban: bool = False, lang: str = "ru") -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text=t("kb_admin_reject", lang), callback_data=f"adm:reject:{request_id}")]
    if allow_ban:
        row.append(InlineKeyboardButton(text=t("kb_admin_ban", lang), callback_data=f"adm:ban:{request_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        row,
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:cancel", style="danger")],
    ])


def admin_reject_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("kb_admin_reject_with_reason", lang), callback_data=f"adm:reject_comment:{request_id}"),
            InlineKeyboardButton(text=t("kb_admin_reject_silent", lang), callback_data=f"adm:reject_silent:{request_id}"),
        ],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:cancel", style="danger")],
    ])


def admin_confirm_delete_plugin_kb(slug: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_confirm", lang), callback_data=f"adm_edit:delete_confirm:{encode_slug(slug)}"),
            InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="adm:cancel"),
        ]
    ])


def admin_confirm_ban_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("kb_admin_confirm_ban", lang), callback_data=f"adm:ban_confirm:{request_id}")],
        [InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="adm:cancel")],
    ])


def admin_plugins_list_kb(
    plugins: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    select_prefix: str = "adm:select_plugin",
    list_prefix: str = "adm:plugins_list",
    back_callback: str = "adm:cancel",
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = []
    for name, slug in plugins:
        rows.append([InlineKeyboardButton(text=name, callback_data=f"{select_prefix}:{encode_slug(slug)}")])
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"{list_prefix}:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text=">", callback_data=f"{list_prefix}:{page+1}"))
    
    if nav:
        rows.append(nav)
    
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data=back_callback, style="danger")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)
