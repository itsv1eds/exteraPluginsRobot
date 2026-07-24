from typing import List, Optional, Tuple
from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.callback_tokens import encode_slug
from bot.icons import CATEGORY_ICONS, ICONS
from bot.texts import t


def _btn(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
    icon: str | None = None,
) -> InlineKeyboardButton:
    emoji_id = ICONS.get(icon or "") or CATEGORY_ICONS.get(icon or "") or icon
    if emoji_id and text and not text.startswith((" ", "\n")):
        text = f" {text}"
    return InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
        url=url,
        style=style,
        icon_custom_emoji_id=emoji_id,
    )


def language_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
        InlineKeyboardButton(text="🇺🇸 English", callback_data="lang:en"),
    ]])


def main_menu_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("btn_catalog", lang), callback_data="catalog", icon="catalog"),
            _btn(t("btn_profile", lang), callback_data="profile", icon="profile"),
        ],
        [_btn(t("btn_submit", lang), callback_data="submit", style="success", icon="submit")],
    ])


def admin_scheduled_posts_list_kb(
    items: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    back_callback: str = "adm:section:post",
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"adm:scheduled_posts:view:{pid}")] for label, pid in items]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"adm:scheduled_posts:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text=">", callback_data=f"adm:scheduled_posts:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data=back_callback, style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_scheduled_post_kb(post_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_edit_text", lang), callback_data=f"adm:scheduled_posts:edit_text:{post_id}"),
            InlineKeyboardButton(text=t("btn_change_time", lang), callback_data=f"adm:scheduled_posts:change_time:{post_id}"),
        ],
        [
            InlineKeyboardButton(text=t("btn_delete_post", lang), callback_data=f"adm:scheduled_posts:delete:{post_id}"),
        ],
        [
            InlineKeyboardButton(text=t("btn_move_up", lang), callback_data=f"adm:scheduled_posts:up:{post_id}"),
            InlineKeyboardButton(text=t("btn_move_down", lang), callback_data=f"adm:scheduled_posts:down:{post_id}"),
        ],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:scheduled_posts:back", style="danger")],
    ])


def submit_type_kb(lang: str, include_update: bool = False) -> InlineKeyboardMarkup:
    idea = t("btn_idea", lang)

    rows: list[list[InlineKeyboardButton]] = [
        [
            _btn(t("btn_new_plugin", lang), callback_data="submit:plugin", icon="plugin"),
        ]
    ]

    second_row: list[InlineKeyboardButton] = []
    if include_update:
        second_row.append(_btn(t("btn_update", lang), callback_data="submit:update", icon="updates"))
    second_row.append(_btn(idea, url="https://t.me/exteraForum", icon="support"))
    rows.append(second_row)

    rows.append([_btn(t("btn_back", lang), callback_data="home", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn(t("btn_cancel", lang), callback_data="cancel", icon="cancel"),
    ]])


def admin_cancel_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(t("btn_cancel", lang), callback_data="adm:cancel", icon="cancel")]])


def admin_schedule_presets_kb(
    presets: list[str],
    select_prefix: str,
    add_callback: str,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, label in enumerate(presets):
        rows.append([InlineKeyboardButton(text=label, callback_data=f"{select_prefix}:{idx}")])
    rows.append([InlineKeyboardButton(text=t("btn_add_preset", lang), callback_data=add_callback)])
    rows.append([InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_scheduled_list_kb(
    items: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    back_callback: str = "adm:section:plugins",
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"adm:review:{rid}")] for label, rid in items]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"adm:scheduled:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text=">", callback_data=f"adm:scheduled:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data=back_callback, style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_scheduled_item_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_change_time", lang), callback_data=f"adm:scheduled:change_time:{request_id}"),
            InlineKeyboardButton(text=t("btn_unschedule", lang), callback_data=f"adm:scheduled:unschedule:{request_id}"),
        ],
        [
            InlineKeyboardButton(text=t("btn_move_up", lang), callback_data=f"adm:scheduled:up:{request_id}"),
            InlineKeyboardButton(text=t("btn_move_down", lang), callback_data=f"adm:scheduled:down:{request_id}"),
        ],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:scheduled:back", style="danger")],
    ])


def categories_kb(categories: list, lang: str) -> InlineKeyboardMarkup:
    buttons = []
    for cat in categories:
        label = cat.get(lang) or cat.get("ru") or cat.get("key")
        buttons.append(_btn(
            label,
            callback_data=f"submit:cat:{cat.get('key')}",
            icon=cat.get("emoji_id") or cat.get("key"),
        ))
    
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([_btn(t("btn_cancel", lang), callback_data="cancel", icon="cancel")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notify_all_kb(lang: str, enabled: bool, back: str = "profile") -> InlineKeyboardMarkup:
    toggle_cb = "profile:notify_all:toggle"
    toggle_label = t("btn_notify_all_on", lang) if enabled else t("btn_notify_all_off", lang)
    toggle_style = "success" if enabled else "danger"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(toggle_label, callback_data=toggle_cb, style=toggle_style, icon="bell")],
            [_btn(t("btn_back", lang), callback_data=back, style="danger", icon="back")],
        ]
    )


def broadcast_kb(
    lang: str,
    enabled: bool,
    paid: bool,
    back: str = "profile",
) -> InlineKeyboardMarkup:
    toggle_cb = "profile:broadcast:toggle"
    if paid:
        state_label = "включена" if enabled else "выключена"
        if lang == "en":
            state_label = "on" if enabled else "off"
        toggle_label = f"{t('btn_broadcast_paid', lang)}: {state_label}"
    else:
        toggle_label = t("btn_broadcast_on", lang) if enabled else t("btn_broadcast_off", lang)

    rows = [[_btn(toggle_label, callback_data=toggle_cb, style=("success" if enabled else "danger"), icon="broadcast")]]
    if not paid:
        rows.append([_btn(t("btn_broadcast_paid_disable", lang), callback_data="profile:broadcast:pay", icon="star")])
    rows.append([_btn(t("btn_back", lang), callback_data=back, style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def icon_draft_edit_kb(
    prefix: str = "adm_icon",
    submit_label: Optional[str] = None,
    include_schedule: bool = False,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    submit_label = submit_label or t("btn_publish", lang)
    rows = [
        [
            _btn(t("kb_field_name", lang), callback_data=f"{prefix}:edit:name", icon="edit"),
            _btn(t("kb_field_author", lang), callback_data=f"{prefix}:edit:author", icon="profile"),
        ],
        [
            _btn(t("kb_field_version", lang), callback_data=f"{prefix}:edit:version", icon="stats"),
            _btn(t("kb_field_count", lang), callback_data=f"{prefix}:edit:count", icon="stats"),
        ],
        [_btn(submit_label, callback_data=f"{prefix}:submit", icon="send")],
    ]
    if include_schedule:
        rows.append([_btn(t("btn_schedule", lang), callback_data="adm:schedule", icon="calendar")])
    rows.append([_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def comment_skip_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn(t("btn_skip", lang), callback_data="comment:skip", icon="forward"),
    ]])


def draft_edit_kb(
    prefix: str,
    submit_label: str,
    include_back: bool = False,
    include_cancel: bool = False,
    include_checked_on: bool = True,
    checked_on_set: bool = False,
    include_delete: bool = False,
    include_file: bool = False,
    include_schedule: bool = False,
    include_not_before: bool = False,
    include_force_publish: bool = False,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = [
        [
            _btn(t("kb_field_name", lang), callback_data=f"{prefix}:edit:name", icon="edit"),
            _btn(t("kb_field_author", lang), callback_data=f"{prefix}:edit:author", icon="profile"),
        ],
        [
            _btn(t("kb_field_description", lang), callback_data=f"{prefix}:edit:description", icon="file"),
            _btn(t("kb_field_usage", lang), callback_data=f"{prefix}:edit:usage", icon="library"),
        ],
        [
            _btn(t("kb_field_settings", lang), callback_data=f"{prefix}:edit:settings", icon="settings"),
            _btn(t("kb_field_min_version", lang), callback_data=f"{prefix}:edit:min_version", icon="lock"),
        ],
    ]
    if include_checked_on:
        label = t("kb_field_checked_on", lang)
        if checked_on_set:
            label = f"{label}: да" if lang == "ru" else f"{label}: yes"
        rows.append([_btn(label, callback_data=f"{prefix}:edit:checked_on", icon="yes")])
    if include_file:
        rows.append([_btn(t("kb_field_file", lang), callback_data=f"{prefix}:edit:file", icon="file")])
    rows.append([_btn(t("kb_field_category", lang), callback_data=f"{prefix}:edit:category", icon="plugin")])
    if include_not_before or prefix in {"draft", "pend"}:
        rows.append([_btn(t("btn_publish_not_before", lang), callback_data=f"{prefix}:not_before", icon="clock")])
    rows.append([_btn(submit_label, callback_data=f"{prefix}:submit", icon="send")])
    if include_force_publish:
        rows.append([_btn(t("btn_publish_now", lang), callback_data="adm:submit_force", icon="yes")])
    if include_schedule:
        rows.append([_btn(t("btn_schedule", lang), callback_data="adm:schedule", icon="calendar")])
    if include_delete:
        rows.append([_btn(t("btn_delete", lang), callback_data=f"{prefix}:delete", icon="delete")])
    if include_back:
        back_cb = "adm:cancel" if prefix.startswith("adm") else f"{prefix}:back"
        rows.append([_btn(t("btn_back", lang), callback_data=back_cb, style="danger", icon="back")])
    if include_cancel:
        cancel_cb = "adm:cancel" if prefix.startswith("adm") else "cancel"
        rows.append([_btn(t("btn_cancel", lang), callback_data=cancel_cb, icon="cancel")])
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
            _btn(
                label,
                callback_data=f"{prefix}:cat:{cat.get('key')}",
                icon=cat.get("emoji_id") or cat.get("key"),
            )
        )

    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    back_cb = "adm:cancel" if prefix.startswith("adm") else f"{prefix}:back"
    rows.append([_btn(t("btn_back", lang), callback_data=back_cb, style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_plugins_kb(plugins: List[Tuple[str, str]], lang: str) -> InlineKeyboardMarkup:
    rows = []
    for name, slug in plugins:
        rows.append([InlineKeyboardButton(text=name, callback_data=f"upd:{encode_slug(slug)}")])
    
    rows.append([InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_main_kb(categories: list, lang: str, source_label: str | None = None) -> InlineKeyboardMarkup:
    rows = []

    rows.append([_btn(t("btn_search", lang), callback_data="search", style="success", icon="search")])
    if source_label:
        rows.append([
            _btn(
                t("btn_catalog_source", lang, source=source_label),
                callback_data="catalog:source:0",
                icon="tag",
            )
        ])

    rows.append([
        _btn(t("btn_all_plugins", lang), callback_data="cat:_all:0", icon="all_plugins"),
        _btn(t("btn_icons", lang), callback_data="icons:0", icon="art"),
    ])
    
    cat_buttons = []
    for cat in categories:
        label = cat.get(lang) or cat.get("ru")
        cat_buttons.append(_btn(
            text=label,
            callback_data=f"cat:{cat.get('key')}:0",
            icon=cat.get("emoji_id") or cat.get("key"),
        ))
    rows.extend([cat_buttons[i:i+2] for i in range(0, len(cat_buttons), 2)])
    
    rows.append([_btn(t("btn_back", lang), callback_data="home", style="danger", icon="back")])
    
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
    notify_all_callback: Optional[str] = None,
    notify_all_label: Optional[str] = None,
) -> InlineKeyboardMarkup:
    rows = []
    if link:
        rows.append([_btn(t("btn_open", lang), url=link, style="success", icon="open")])
    if update_callback:
        rows.append([_btn(t("btn_update", lang), callback_data=update_callback, icon="updates")])
    if delete_callback:
        rows.append([_btn(t("btn_delete", lang), callback_data=delete_callback, icon="delete")])
    if subscribe_callback:
        label = subscribe_label or t("btn_subscribe", lang)
        rows.append([
            _btn(label, callback_data=subscribe_callback, icon="bell")
        ])
    if notify_all_callback:
        label = notify_all_label or t("btn_subscriptions", lang)
        rows.append([_btn(label, callback_data=notify_all_callback, icon="bell")])
    rows.append([_btn(t("btn_back", lang), callback_data=back, style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_kb(lang: str, show_retry: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if show_retry:
        rows.append([_btn(t("btn_retry", lang), callback_data="search", icon="updates")])
    rows.append([
        _btn(
            t("btn_back", lang),
            callback_data="catalog",
            style="danger",
            icon="back",
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

    owned: list[InlineKeyboardButton] = []
    if has_plugins:
        owned.append(_btn(t("btn_my_plugins", lang), callback_data="my:plugins:0", icon="plugin"))
    if has_icons:
        owned.append(_btn(t("btn_my_packs", lang), callback_data="my:icons:0", icon="art"))
    if owned:
        rows.append(owned)

    rows.append([
        _btn(t("btn_subscriptions", lang), callback_data="profile:subscriptions", icon="bell"),
        _btn(t("btn_broadcast", lang), callback_data="profile:broadcast", icon="broadcast"),
    ])

    rows.append([
        _btn(t("btn_joinly", lang), callback_data="profile:joinly", icon="joinly"),
        _btn(t("poster_btn", lang), callback_data="pstr:start", icon="clock"),
    ])

    rows.append([_btn(t("btn_support", lang), url="https://t.me/itsv2eds", icon="support")])
    
    rows.append([_btn(t("btn_back", lang), callback_data="home", style="danger", icon="back")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu_kb(role: str | None = None, lang: str = "ru") -> InlineKeyboardMarkup:
    is_super = role in {"super", None}
    rows = [
        [_btn(t("admin_btn_plugins", lang), callback_data="adm:section:plugins", icon="plugin")],
        [
            _btn(t("poster_btn", lang), callback_data="pstr:admin", icon="send"),
            _btn(t("admin_btn_my_notifications", lang), callback_data="adm:notifs", icon="bell"),
        ],
    ]
    if is_super:
        rows.append([
            _btn(t("admin_btn_broadcast", lang), callback_data="adm:broadcast", icon="broadcast"),
            _btn(t("admin_btn_stats", lang), callback_data="adm:stats", icon="stats"),
        ])
        rows.append([
            _btn(t("admin_btn_banned", lang), callback_data="adm:banned:0", icon="ban"),
            _btn(t("admin_btn_config", lang), callback_data="adm:config", icon="settings"),
        ])
    if not is_super:
        rows.append([_btn(t("admin_btn_stats", lang), callback_data="adm:stats", icon="stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_backup_kb(cfg: dict, lang: str = "ru") -> InlineKeyboardMarkup:
    auto = bool(cfg.get("auto_enabled"))
    interval = int(cfg.get("interval_hours") or 24)
    auto_label = t("admin_backup_auto_on", lang) if auto else t("admin_backup_auto_off", lang)
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_backup_now", lang), callback_data="adm:backup:now", icon="download", style="success")],
        [_btn(f"{auto_label}", callback_data="adm:backup:toggle",
              icon=("yes" if auto else "no"), style=("success" if auto else "danger"))],
        [_btn(t("admin_backup_interval", lang, hours=interval), callback_data="adm:backup:interval", icon="clock")],
        [_btn(t("admin_backup_recipients", lang), callback_data="adm:backup:recipients", icon="bell")],
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


def admin_backup_recipients_kb(recipient_ids: List[int], lang: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    for rid in recipient_ids:
        rows.append([
            _btn(str(rid), callback_data="adm:backup:rcp_noop", icon="profile"),
            _btn(t("btn_delete", lang), callback_data=f"adm:backup:rcp_rm:{rid}", icon="delete"),
        ])
    rows.append([_btn(t("btn_add", lang), callback_data="adm:backup:rcp_add", icon="add", style="success")])
    rows.append([_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_maintenance_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_btn_backup", lang), callback_data="adm:backup", icon="download")],
        [
            _btn(t("admin_maint_health", lang), callback_data="adm:maint:health", icon="stats"),
            _btn(t("admin_maint_sync_version", lang), callback_data="adm:maint:sync_version", icon="updates"),
        ],
        [
            _btn(t("admin_maint_sync_catalog", lang), callback_data="adm:maint:sync_catalog", icon="link"),
            _btn(t("admin_maint_erase_id", lang), callback_data="adm:maint:erase_id", icon="delete"),
        ],
        [_btn(t("admin_maint_erase_hidden", lang), callback_data="adm:maint:erase", icon="delete", style="danger")],
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


def admin_maint_confirm_kb(action: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("btn_confirm", lang), callback_data=f"adm:maint:{action}:confirm", style="danger", icon="yes"),
            _btn(t("btn_cancel", lang), callback_data="adm:maint", icon="back"),
        ],
    ])


def admin_sources_kb(sources: list[dict], lang: str = "ru") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for src in sources:
        sid = str(src.get("id") or src.get("username") or "").strip().lstrip("@").lower()
        if not sid:
            continue
        title = str(src.get("title") or src.get("username") or sid)
        count = src.get("_count")
        label = f"{title} · {count}" if count is not None else title
        rows.append([_btn(label, callback_data=f"adm:source:{sid}", icon="link")])
    rows.append([_btn(t("admin_source_add", lang), callback_data="adm:sources:add", icon="add", style="success")])
    rows.append([_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_source_detail_kb(source_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_source_attach", lang), callback_data=f"adm:source:{source_id}:attach", icon="link")],
        [_btn(t("admin_source_delete", lang), callback_data=f"adm:source:{source_id}:del", icon="delete", style="danger")],
        [_btn(t("btn_back", lang), callback_data="adm:sources", style="danger", icon="back")],
    ])


def admin_source_del_confirm_kb(source_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("btn_confirm", lang), callback_data=f"adm:source:{source_id}:del:yes", style="danger", icon="yes"),
            _btn(t("btn_cancel", lang), callback_data=f"adm:source:{source_id}", icon="back"),
        ],
    ])


def admin_notification_settings_kb(prefs: dict[str, bool], labels: list[tuple[str, str]], lang: str = "ru") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, label in labels:
        enabled = bool(prefs.get(key, True))
        state = t("admin_notify_pref_on", lang) if enabled else t("admin_notify_pref_off", lang)
        rows.append([
            _btn(
                f"{label}: {state}",
                callback_data=f"adm:notifs:toggle:{key}",
                icon=("yes" if enabled else "no"),
                style=("success" if enabled else "danger"),
            )
        ])
    rows.append([_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_plugins_section_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("admin_btn_requests", lang), callback_data="adm:queue:plugins:0", icon="requests"),
            _btn(t("admin_btn_updates", lang), callback_data="adm:section:updates", icon="updates"),
        ],
        [
            _btn(t("admin_btn_scheduled", lang), callback_data="adm:scheduled:0", icon="clock"),
        ],
        [
            _btn(t("admin_btn_edit_plugins", lang), callback_data="adm:edit_plugins", icon="edit"),
            _btn(t("admin_btn_link_author_search", lang), callback_data="adm:link_author", icon="link"),
        ],
        [_btn(t("admin_btn_audit", lang), callback_data="adm:audit:0", icon="file")],
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


_AUDIT_FILTER_BUTTONS = (
    ("all", "admin_audit_filter_all"),
    ("published", "admin_audit_filter_published"),
    ("rejected", "admin_audit_filter_rejected"),
    ("pending", "admin_audit_filter_pending"),
    ("rework", "admin_audit_filter_rework"),
    ("scheduled", "admin_audit_filter_scheduled"),
)


def admin_rejected_kb(
    items: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    status: str = "all",
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = [[_btn(label, callback_data=f"adm:rejreq:{rid}", icon="file")] for label, rid in items]

    filters: List[InlineKeyboardButton] = []
    for key, text_key in _AUDIT_FILTER_BUTTONS:
        label = t(text_key, lang)
        if key == status:
            filters.append(_btn(f"· {label} ·", callback_data=f"adm:audit:{key}:0", style="success"))
        else:
            filters.append(_btn(label, callback_data=f"adm:audit:{key}:0"))
    for i in range(0, len(filters), 3):
        rows.append(filters[i:i + 3])

    nav = []
    if page > 0:
        nav.append(_btn("<", callback_data=f"adm:audit:{status}:{page-1}", icon="back"))
    if page < total_pages - 1:
        nav.append(_btn(">", callback_data=f"adm:audit:{status}:{page+1}", icon="forward"))
    if nav:
        rows.append(nav)
    rows.append([_btn(t("btn_back", lang), callback_data="adm:section:plugins", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_rejected_detail_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_rej_review", lang), callback_data=f"adm:review:{request_id}", icon="edit", style="success")],
        [_btn(t("admin_rej_delete", lang), callback_data=f"adm:rejdel:{request_id}", icon="delete", style="danger")],
        [_btn(t("btn_back", lang), callback_data="adm:audit:0", style="danger", icon="back")],
    ])


def admin_updates_section_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_btn_updates", lang), callback_data="adm:queue:update:0", icon="updates")],
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


def admin_updates_list_kb(
    items: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    back_callback: str = "adm:section:plugins",
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.extend([[InlineKeyboardButton(text=label, callback_data=f"adm:review:{rid}")] for label, rid in items])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("<", callback_data=f"adm:updates:{page-1}", icon="back"))
    if page < total_pages - 1:
        nav.append(_btn(">", callback_data=f"adm:updates:{page+1}", icon="forward"))
    if nav:
        rows.append(nav)

    rows.append([_btn(t("btn_back", lang), callback_data=back_callback, style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_post_section_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_btn_post", lang), callback_data="adm:post:new", icon="send")],
        [_btn(t("admin_btn_scheduled_posts", lang), callback_data="adm:scheduled_posts:0", icon="calendar")],
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


def admin_config_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("admin_cfg_section_admins", lang), callback_data="adm:config_section:admins", icon="admin"),
            _btn(t("admin_cfg_section_channels", lang), callback_data="adm:config_section:channels", icon="broadcast"),
        ],
        [
            _btn(t("admin_cfg_section_moderation", lang), callback_data="adm:config_section:moderation", icon="requests"),
            _btn(t("admin_cfg_section_other", lang), callback_data="adm:config_section:other", icon="settings"),
        ],
        [
            _btn(t("admin_btn_sources", lang), callback_data="adm:sources", icon="link"),
            _btn(t("admin_btn_maintenance", lang), callback_data="adm:maint", icon="settings"),
        ],
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


def admin_config_admins_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_cfg_superadmins", lang), callback_data="adm:config:admins_super", icon="admin")],
        [
            _btn(t("admin_cfg_admins_plugins", lang), callback_data="adm:config:admins_plugins", icon="plugin"),
            _btn(t("admin_cfg_admins_icons", lang), callback_data="adm:config:admins_icons", icon="art"),
        ],
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


def admin_config_channels_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("admin_cfg_channel_id", lang), callback_data="adm:config:channel.id", icon="broadcast"),
            _btn(t("admin_cfg_channel_username", lang), callback_data="adm:config:channel.username", icon="link"),
        ],
        [
            _btn(t("admin_cfg_channel_title", lang), callback_data="adm:config:channel.title", icon="edit"),
            _btn(t("admin_cfg_publish_channel", lang), callback_data="adm:config:publish_channel", icon="send"),
        ],
        [
            _btn(t("admin_cfg_channel_default_tags", lang), callback_data="adm:config:channel.default_tags", icon="tag"),
            _btn(t("admin_cfg_channel_locale_order", lang), callback_data="adm:config:channel.locale_order", icon="library"),
        ],
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


def admin_config_moderation_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("admin_cfg_moderation_forum_chat_id", lang), callback_data="adm:config:moderation.forum_chat_id", icon="requests"),
            _btn(t("admin_cfg_moderation_forum_topic_id", lang), callback_data="adm:config:moderation.forum_topic_id", icon="file"),
        ],
        [
            _btn(t("admin_cfg_moderation_vote_threshold", lang), callback_data="adm:config:moderation.vote_threshold", icon="vote"),
            _btn(t("admin_cfg_moderation_notification_chat_ids", lang), callback_data="adm:config:moderation.notification_chat_ids", icon="bell"),
        ],
        [
            _btn(t("admin_cfg_reject_templates", lang), callback_data="adm:rejtpl_cfg", icon="file"),
            _btn(t("admin_cfg_moderation_delete_review_notifications_on_decision", lang), callback_data="adm:config:moderation.delete_review_notifications_on_decision", icon="delete"),
        ],
        [_btn(t("admin_cfg_moderation_min_supported_version", lang), callback_data="adm:config:moderation.min_supported_version", icon="updates")],
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


def admin_config_other_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_cfg_checked_on_version", lang), callback_data="adm:config:checked_on_version", icon="yes")],
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


def admin_manage_admins_kb(field: str, admin_ids: List[int], lang: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    for admin_id in admin_ids:
        rows.append([
            _btn(str(admin_id), callback_data=f"adm:admins:noop:{field}", icon="profile"),
            _btn(t("btn_delete", lang), callback_data=f"adm:admins:rm:{field}:{admin_id}", icon="delete"),
        ])
    rows.append([_btn(t("btn_add", lang), callback_data=f"adm:admins:add:{field}", icon="add")])
    rows.append([_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_broadcast_confirm_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("btn_send", lang), callback_data="adm:broadcast:confirm", icon="send")],
        [_btn(t("btn_cancel", lang), callback_data="adm:cancel", icon="cancel")],
    ])


def admin_post_confirm_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("btn_send", lang), callback_data="adm:post:send", style="success", icon="send")],
        [_btn(t("btn_schedule", lang), callback_data="adm:post:schedule", icon="calendar")],
    ])


def admin_queue_kb(
    items: List[Tuple[str, str] | Tuple[str, str, str | None]],
    page: int,
    total_pages: int,
    queue_type: str,
    back_callback: str = "adm:cancel",
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        label = item[0]
        rid = item[1]
        icon = item[2] if len(item) > 2 else None
        if icon:
            rows.append([_btn(label, callback_data=f"adm:review:{rid}", icon=icon)])
        else:
            rows.append([InlineKeyboardButton(text=label, callback_data=f"adm:review:{rid}")])
    
    nav = []
    if page > 0:
        nav.append(_btn("<", callback_data=f"adm:queue:{queue_type}:{page-1}", icon="back"))
    if page < total_pages - 1:
        nav.append(_btn(">", callback_data=f"adm:queue:{queue_type}:{page+1}", icon="forward"))
    
    if nav:
        rows.append(nav)
    
    rows.append([_btn(t("btn_back", lang), callback_data=back_callback, style="danger", icon="back")])
    
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
                _btn(label, callback_data=f"adm:user_info:{user_id}", icon="profile"),
                _btn(t("kb_admin_unban", lang), callback_data=f"adm:unban:{user_id}", icon="yes"),
            ]
        )

    nav = []
    if page > 0:
        nav.append(_btn("<", callback_data=f"adm:banned:{page-1}", icon="back"))
    if page < total_pages - 1:
        nav.append(_btn(">", callback_data=f"adm:banned:{page+1}", icon="forward"))

    if nav:
        rows.append(nav)

    rows.append([_btn(t("kb_admin_ban_manual", lang), callback_data="adm:ban_manual", icon="ban", style="danger")])
    rows.append([_btn(t("admin_btn_rejected_appeals", lang), callback_data="adm:rejapp:0", icon="file")])
    rows.append([_btn(t("btn_back", lang), callback_data=back_callback, style="danger", icon="back")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_rejected_appeals_kb(items: List[Tuple[str, str]], page: int, total_pages: int, lang: str = "ru") -> InlineKeyboardMarkup:
    rows = [[_btn(label, callback_data=f"adm:appd:{rid}", icon="profile")] for label, rid in items]
    nav = []
    if page > 0:
        nav.append(_btn("<", callback_data=f"adm:rejapp:{page-1}", icon="back"))
    if page < total_pages - 1:
        nav.append(_btn(">", callback_data=f"adm:rejapp:{page+1}", icon="forward"))
    if nav:
        rows.append(nav)
    rows.append([_btn(t("btn_back", lang), callback_data="adm:banned:0", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_rejected_appeal_detail_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("kb_appeal_unban", lang), callback_data=f"adm:appunb:{request_id}", icon="yes", style="success")],
        [_btn(t("admin_rej_delete", lang), callback_data=f"adm:appdel:{request_id}", icon="delete", style="danger")],
        [_btn(t("btn_back", lang), callback_data="adm:rejapp:0", style="danger", icon="back")],
    ])


def admin_confirm_ban_user_kb(user_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("kb_admin_ban_delete", lang), callback_data=f"adm:banuid:{user_id}:del", icon="delete", style="danger")],
        [_btn(t("kb_admin_ban_keep", lang), callback_data=f"adm:banuid:{user_id}:keep", icon="ban")],
        [_btn(t("btn_cancel", lang), callback_data="adm:cancel", icon="back")],
    ])


def admin_review_kb(
    request_id: str,
    user_id: int,
    submit_label: Optional[str] = None,
    submit_callback: str | None = None,
    lang: str = "ru",
    allow_publish: bool = True,
) -> InlineKeyboardMarkup:
    submit_callback = submit_callback or f"adm:prepublish:{request_id}"
    submit_label = submit_label or t("btn_publish", lang)
    rows: list[list[InlineKeyboardButton]] = []
    if allow_publish:
        rows.append([
            _btn(submit_label, callback_data=submit_callback, icon="yes"),
            _btn(t("btn_more", lang), callback_data=f"adm:actions:{request_id}", icon="menu"),
        ])
    rows.append([
        _btn(t("btn_vote_yes", lang), callback_data=f"modvote:yes:{request_id}", icon="yes", style="success"),
        _btn(t("btn_vote_no", lang), callback_data=f"modvote:no:{request_id}", icon="no", style="danger"),
    ])
    if user_id:
        rows.append([_btn(t("kb_admin_msg_author", lang), callback_data=f"adm:msgauthor:{request_id}", icon="edit")])
    rows.append([_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_vote_kb(request_id: str, yes_count: int = 0, no_count: int = 0, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(f"{t('btn_vote_yes', lang)} ({yes_count})", callback_data=f"modvote:yes:{request_id}", icon="yes", style="success"),
            _btn(f"{t('btn_vote_no', lang)} ({no_count})", callback_data=f"modvote:no:{request_id}", icon="no", style="danger"),
        ],
    ])


def moderation_inline_vote_url_kb(bot_username: str, request_id: str, yes_count: int = 0, no_count: int = 0, lang: str = "ru") -> InlineKeyboardMarkup:
    token = quote(str(request_id), safe="")
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(f"{t('btn_vote_yes', lang)} ({yes_count})", url=f"https://t.me/{bot_username}?start=modvote_yes_{token}", icon="yes", style="success"),
            _btn(f"{t('btn_vote_no', lang)} ({no_count})", url=f"https://t.me/{bot_username}?start=modvote_no_{token}", icon="no", style="danger"),
        ],
    ])


def admin_actions_kb(request_id: str, allow_ban: bool = False, lang: str = "ru") -> InlineKeyboardMarkup:
    row = [_btn(t("kb_admin_reject", lang), callback_data=f"adm:reject:{request_id}", icon="no", style="danger")]
    if allow_ban:
        row.append(_btn(t("kb_admin_ban", lang), callback_data=f"adm:ban:{request_id}", icon="ban", style="danger"))
    return InlineKeyboardMarkup(inline_keyboard=[
        row,
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


def admin_reject_kb(request_id: str, lang: str = "ru", show_votes: bool = False) -> InlineKeyboardMarkup:
    votes_key = "kb_admin_reject_votes_on" if show_votes else "kb_admin_reject_votes_off"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("kb_admin_reject_with_reason", lang), callback_data=f"adm:reject_comment:{request_id}", icon="edit"),
            _btn(t("kb_admin_reject_silent", lang), callback_data=f"adm:reject_silent:{request_id}", icon="no"),
        ],
        [_btn(t("kb_admin_reject_template", lang), callback_data=f"adm:rejtpl_pick:{request_id}", icon="file")],
        [_btn(t(votes_key, lang), callback_data=f"adm:reject_votes:{request_id}",
              icon=("yes" if show_votes else "no"), style=("success" if show_votes else None))],
        [_btn(t("kb_admin_rework", lang), callback_data=f"adm:rework:{request_id}", icon="updates")],
        [_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")],
    ])


def _tpl_label(text: str, limit: int = 28) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def admin_reject_templates_kb(
    request_id: str,
    templates: List[str],
    selected: List[int],
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = []
    for idx, tpl in enumerate(templates):
        if idx in selected:
            label = f"[{selected.index(idx) + 1}] {_tpl_label(tpl)}"
            style = "success"
        else:
            label = f"{idx + 1}. {_tpl_label(tpl)}"
            style = None
        rows.append([_btn(label, callback_data=f"adm:rejtpl_t:{request_id}:{idx}", style=style)])
    if selected:
        rows.append([_btn(t("kb_admin_reject_tpl_send", lang),
                          callback_data=f"adm:rejtpl_go:{request_id}", icon="yes", style="success")])
    rows.append([_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_reject_templates_cfg_kb(templates: List[str], lang: str = "ru") -> InlineKeyboardMarkup:
    rows = [
        [_btn(f"{idx + 1}. {_tpl_label(tpl)}", callback_data=f"adm:rejtpl_del:{idx}", icon="delete")]
        for idx, tpl in enumerate(templates)
    ]
    rows.append([_btn(t("kb_admin_rejtpl_add", lang), callback_data="adm:rejtpl_add", icon="edit", style="success")])
    rows.append([_btn(t("btn_back", lang), callback_data="adm:cancel", style="danger", icon="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_confirm_delete_plugin_kb(slug: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("btn_confirm", lang), callback_data=f"adm_edit:delete_confirm:{encode_slug(slug)}", icon="yes", style="success"),
            _btn(t("btn_cancel", lang), callback_data="adm:cancel", icon="cancel"),
        ]
    ])


def admin_confirm_ban_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("kb_admin_ban_delete", lang), callback_data=f"adm:ban_confirm:{request_id}:del", icon="delete", style="danger")],
        [_btn(t("kb_admin_ban_keep", lang), callback_data=f"adm:ban_confirm:{request_id}:keep", icon="ban")],
        [_btn(t("btn_cancel", lang), callback_data="adm:cancel", icon="back")],
    ])


def moderation_appeal_kb(request_id: str, yes_count: int = 0, no_count: int = 0, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(f"{t('btn_vote_yes', lang)} ({yes_count})", callback_data=f"modvote:yes:{request_id}", icon="yes", style="success"),
            _btn(f"{t('btn_vote_no', lang)} ({no_count})", callback_data=f"modvote:no:{request_id}", icon="no", style="danger"),
        ],
        [
            _btn(t("kb_appeal_unban", lang), callback_data=f"adm:appeal:approve:{request_id}", icon="yes", style="success"),
            _btn(t("kb_appeal_deny", lang), callback_data=f"adm:appeal:deny:{request_id}", icon="no", style="danger"),
        ],
        [_btn(t("kb_appeal_banfinal", lang), callback_data=f"adm:appeal:banfinal:{request_id}", icon="ban", style="danger")],
    ])


def banned_appeal_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("kb_appeal_submit", lang), callback_data="appeal:start", icon="edit")],
    ])


def admin_appeal_decision_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("kb_appeal_unban", lang), callback_data=f"adm:appeal:approve:{request_id}", icon="yes", style="success"),
            _btn(t("kb_appeal_deny", lang), callback_data=f"adm:appeal:deny:{request_id}", icon="no", style="danger"),
        ],
        [_btn(t("kb_appeal_banfinal", lang), callback_data=f"adm:appeal:banfinal:{request_id}", icon="ban", style="danger")],
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
