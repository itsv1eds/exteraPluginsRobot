import logging
from typing import List, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from core.categories import categories_for

from bot.callback_tokens import encode_slug
from bot.icons import CATEGORY_ICONS, ICONS
from bot.texts import t
from bot.languages import LANGUAGE_FLAGS, LANGUAGE_NAMES, SUPPORTED_LANGUAGES, category_label
from bot.services.reason_templates import reason_template_id, template_page, templates_allowed
from core.request_store import get_request_by_callback_token, request_callback_token, request_deeplink_token
from core.user_store import moderation_preferences

logger = logging.getLogger(__name__)


def _btn(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
    icon: str | None = None,
    switch_inline_query_current_chat: str | None = None,
    switch_inline_query: str | None = None,
) -> InlineKeyboardButton:
    emoji_id = ICONS.get(icon or "") or CATEGORY_ICONS.get(icon or "") or icon
    if emoji_id and not str(emoji_id).isdigit():
        logger.warning("event=keyboard.unknown_icon icon=%s", emoji_id)
        emoji_id = None
    if emoji_id and text and not text.startswith((" ", "\n")):
        text = f" {text}"
    return InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
        url=url,
        style=style,
        icon_custom_emoji_id=emoji_id,
        switch_inline_query_current_chat=switch_inline_query_current_chat,
        switch_inline_query=switch_inline_query,
    )


_EXIT_STYLE = "danger"


def _back(lang: str, callback_data: str) -> InlineKeyboardButton:
    return _btn(t("btn_back", lang), callback_data=callback_data, style=_EXIT_STYLE, icon="back")


def _cancel(lang: str, callback_data: str = "cancel") -> InlineKeyboardButton:
    return _btn(t("btn_cancel", lang), callback_data=callback_data, style=_EXIT_STYLE, icon="cancel")


def _dismiss(lang: str, callback_data: str) -> InlineKeyboardButton:
    return _btn(t("btn_cancel", lang), callback_data=callback_data, icon="back")


PAGE_NOOP = "page:noop"
_PAGE_PICKER_MAX = 64
_PAGE_PICKER_WIDTH = 8


def pager_row(page: int, total_pages: int, prefix: str | None = None, *, callback=None) -> list[InlineKeyboardButton]:
    total_pages = max(int(total_pages or 1), 1)
    if total_pages == 1:
        return []
    page = min(max(int(page or 0), 0), total_pages - 1)
    target = callback or (lambda number: f"{prefix}:{number}")
    picker = f"page:picker|{prefix}|{page}|{total_pages}"
    return [
        InlineKeyboardButton(text="‹", callback_data=target((page - 1) % total_pages)),
        InlineKeyboardButton(
            text=f"{page + 1} / {total_pages}",
            callback_data=picker if prefix and total_pages > 2 and len(picker.encode()) <= 64 else PAGE_NOOP,
        ),
        InlineKeyboardButton(text="›", callback_data=target((page + 1) % total_pages)),
    ]


def _add_pager(rows: list, page: int, total_pages: int, prefix: str | None = None, *, callback=None) -> None:
    row = pager_row(page, total_pages, prefix, callback=callback)
    if row:
        rows.append(row)


def language_kb() -> InlineKeyboardMarkup:
    buttons = [_btn(f"{LANGUAGE_FLAGS[code]} {LANGUAGE_NAMES[code]}", callback_data=f"lang:{code}") for code in SUPPORTED_LANGUAGES]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[:2], buttons[2:]])


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
    rows = [[_btn(label, callback_data=f"adm:scheduled_posts:view:{pid}", icon="clock")] for label, pid in items]

    _add_pager(rows, page, total_pages, "adm:scheduled_posts")

    rows.append([_back(lang, back_callback)])
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
        [_back(lang, "adm:scheduled_posts:back")],
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

    rows.append([_back(lang, "home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_confirm_kb(prefix: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn(t("btn_cancel_submission", lang), callback_data="cancel:confirm", style="danger", icon="delete"),
        _btn(t("btn_keep_editing", lang), callback_data=f"{prefix}:back", icon="back"),
    ]])


def cancel_kb(lang: str, back: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_back(lang, "cancel") if back else _cancel(lang)]])


def admin_cancel_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_cancel(lang, "adm:cancel")]])


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
    rows.append([_cancel(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_scheduled_list_kb(
    items: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    back_callback: str = "adm:section:plugins",
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = [[_btn(label, callback_data=f"adm:review:{request_callback_token(rid)}", icon="clock")] for label, rid in items]

    _add_pager(rows, page, total_pages, "adm:scheduled")

    rows.append([_back(lang, back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_schedule_controls(request_id: str, lang: str) -> list[list[InlineKeyboardButton]]:
    token = request_callback_token(request_id)
    return [
        [
            _btn(t("btn_change_time", lang), callback_data=f"adm:scheduled:change_time:{token}", icon="clock"),
            _btn(t("btn_unschedule", lang), callback_data=f"adm:scheduled:unschedule:{token}", icon="cancel"),
        ],
        [
            _btn(t("btn_move_up", lang), callback_data=f"adm:scheduled:up:{token}"),
            _btn(t("btn_move_down", lang), callback_data=f"adm:scheduled:down:{token}"),
        ],
    ]


def categories_kb(categories: list, lang: str, selected=None) -> InlineKeyboardMarkup:
    selected = categories_for(selected)
    buttons = []
    for cat in categories:
        label = category_label(cat, lang)
        buttons.append(_btn(
            label,
            callback_data=f"submit:cat:{cat.get('key')}",
            style="success" if cat.get("key") in selected else None,
            icon=CATEGORY_ICONS.get(cat.get("key")) or cat.get("emoji_id") or cat.get("key"),
        ))
    
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([_btn(t("btn_categories_done", lang), callback_data="submit:cat:done", icon="yes")])
    rows.append([_back(lang, "submit:cat:back"), _cancel(lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast_kb(
    lang: str,
    enabled: bool,
    paid: bool,
    back: str = "profile",
) -> InlineKeyboardMarkup:
    toggle_cb = "profile:broadcast:toggle"
    if paid:
        state_label = t("state_on" if enabled else "state_off", lang)
        toggle_label = f"{t('btn_broadcast_paid', lang)}: {state_label}"
    else:
        toggle_label = t("btn_broadcast_on", lang) if enabled else t("btn_broadcast_off", lang)

    rows = [[_btn(toggle_label, callback_data=toggle_cb, style=("success" if enabled else "danger"), icon="broadcast")]]
    if not paid:
        rows.append([_btn(t("btn_broadcast_paid_disable", lang), callback_data="profile:broadcast:pay", icon="star")])
    rows.append([_back(lang, back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def icon_pack_draft_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("kb_field_link", lang), callback_data="adm:pack:edit:link", icon="link"),
            _btn(t("kb_field_name", lang), callback_data="adm:pack:edit:name", icon="edit"),
        ],
        [
            _btn(t("kb_field_author", lang), callback_data="adm:pack:edit:author", icon="profile"),
            _btn(t("kb_field_count", lang), callback_data="adm:pack:edit:count", icon="stats"),
        ],
        [_btn(t("btn_add_to_catalog", lang), callback_data="adm:pack:save", icon="add", style="success")],
        [_cancel(lang, "adm:pack:cancel")],
    ])


def quiz_kb(options: List[str], index: int, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(str(number + 1), callback_data=f"quiz:a:{index}:{number}") for number in range(len(options))],
        [_cancel(lang)],
    ])


def quiz_start_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("quiz_btn_start", lang), callback_data="quiz:start", icon="yes", style="success")],
        [_cancel(lang, "cancel")],
    ])


def comment_skip_kb(lang: str, has_content: bool = False, media_count: int = 0,
                    required: bool = False, back: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if has_content:
        rows.append([_btn(t("btn_comment_send", lang, count=media_count),
                          callback_data="comment:send", icon="send", style="success")])
        rows.append([_btn(t("btn_comment_reset", lang), callback_data="comment:reset", icon="delete", style="danger")])
    if not required:
        rows.append([_btn(t("btn_skip", lang), callback_data="comment:skip", icon="forward")])
    rows.append([_back(lang, "cancel") if back else _cancel(lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def author_plugin_removed_kb(slug: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("kb_contact_moderation", lang), callback_data=f"usr:modremoved:{slug}", icon="support")],
    ])


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
    back_callback: str | None = None,
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
            label = f"{label}: {t('value_yes', lang)}"
        rows.append([_btn(label, callback_data=f"{prefix}:edit:checked_on", icon="yes")])
    if include_file:
        rows.append([_btn(t("kb_field_file", lang), callback_data=f"{prefix}:edit:file", icon="file")])
    rows.append([_btn(t("kb_field_category", lang), callback_data=f"{prefix}:edit:category", icon="plugin")])
    if include_not_before or prefix in {"draft", "pend"}:
        rows.append([_btn(t("btn_publish_not_before", lang), callback_data=f"{prefix}:not_before", icon="clock")])
    rows.append([_btn(submit_label, callback_data=f"{prefix}:submit", icon="send", style="success")])
    if include_force_publish:
        rows.append([_btn(t("btn_publish_now", lang), callback_data="adm:submit_force", icon="yes")])
    if include_schedule:
        rows.append([_btn(t("btn_schedule", lang), callback_data="adm:schedule", icon="calendar")])
    if include_delete:
        rows.append([_btn(t("btn_delete", lang), callback_data=f"{prefix}:delete", icon="delete", style="danger")])
    if include_back:
        back_cb = back_callback or ("adm:cancel" if prefix.startswith("adm") else f"{prefix}:back")
        rows.append([_back(lang, back_cb)])
    if include_cancel:
        cancel_cb = "adm:cancel" if prefix.startswith("adm") else "cancel"
        rows.append([_cancel(lang, cancel_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def draft_lang_kb(prefix: str, field: str, lang: str = "ru") -> InlineKeyboardMarkup:
    back_cb = "adm:cancel" if prefix.startswith("adm") else f"{prefix}:back"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 RU", callback_data=f"{prefix}:lang:{field}:ru"),
            InlineKeyboardButton(text="🇺🇸 EN", callback_data=f"{prefix}:lang:{field}:en"),
        ],
        [_back(lang, back_cb)],
    ])


def description_lang_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="desc_lang:ru"),
            InlineKeyboardButton(text="🇺🇸 English", callback_data="desc_lang:en"),
        ],
        [_cancel(lang)],
    ])


def draft_category_kb(prefix: str, categories: list, lang: str = "ru", selected=None) -> InlineKeyboardMarkup:
    selected = categories_for(selected)
    buttons = []
    for cat in categories:
        label = category_label(cat, lang)
        buttons.append(
            _btn(
                label,
                callback_data=f"{prefix}:cat:{cat.get('key')}",
                style="success" if cat.get("key") in selected else None,
                icon=CATEGORY_ICONS.get(cat.get("key")) or cat.get("emoji_id") or cat.get("key"),
            )
        )

    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([_btn(t("btn_categories_done", lang), callback_data=f"{prefix}:cat:done", icon="yes")])
    rows.append([_back(lang, f"{prefix}:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_plugins_kb(plugins: List[Tuple[str, str]], lang: str) -> InlineKeyboardMarkup:
    rows = []
    for name, slug in plugins:
        rows.append([InlineKeyboardButton(text=name, callback_data=f"upd:{encode_slug(slug)}")])
    
    rows.append([_cancel(lang, "cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_main_kb(categories: list, lang: str) -> InlineKeyboardMarkup:
    rows = []

    rows.append([_btn(t("btn_search", lang), callback_data="search", style="success", icon="search")])

    rows.append([
        _btn(t("btn_all_plugins", lang), callback_data="cat:_all:0", icon="all_plugins"),
        _btn(t("btn_icons", lang), callback_data="icons:0", icon="art"),
    ])
    
    cat_buttons = []
    for cat in categories:
        label = category_label(cat, lang)
        cat_buttons.append(_btn(
            text=label,
            callback_data=f"cat:{cat.get('key')}:0",
            icon=CATEGORY_ICONS.get(cat.get("key")) or cat.get("emoji_id") or cat.get("key"),
        ))
    rows.extend([cat_buttons[i:i+2] for i in range(0, len(cat_buttons), 2)])
    
    rows.append([_back(lang, "home")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def paginated_list_kb(
    items: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    nav_prefix: str,
    back_callback: str,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = [[_btn(item[0], callback_data=item[1], icon=item[2] if len(item) > 2 else None)] for item in items]
    _add_pager(rows, page, total_pages, nav_prefix)
    rows.append([_back(lang, back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _picker_pages(current_page: int, total_pages: int) -> list[int]:
    if total_pages <= _PAGE_PICKER_MAX:
        return list(range(total_pages))
    step = -(-total_pages // (_PAGE_PICKER_MAX - 8))
    pages = set(range(0, total_pages, step)) | {total_pages - 1}
    pages.update(number for number in range(current_page - 2, current_page + 3) if 0 <= number < total_pages)
    return sorted(pages)


def page_picker_kb(
    nav_prefix: str,
    current_page: int,
    total_pages: int,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    total_pages = max(total_pages, 1)
    current_page = min(max(current_page, 0), total_pages - 1)
    buttons = [
        InlineKeyboardButton(
            text=str(number + 1),
            callback_data=f"{nav_prefix}:{number}",
            style="success" if number == current_page else None,
        )
        for number in _picker_pages(current_page, total_pages)
    ]
    rows = [buttons[index:index + _PAGE_PICKER_WIDTH] for index in range(0, len(buttons), _PAGE_PICKER_WIDTH)]
    rows.append([_back(lang, f"{nav_prefix}:{current_page}")])
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
    request_callback: Optional[str] = None,
) -> InlineKeyboardMarkup:
    rows = []
    if link:
        rows.append([_btn(t("btn_open", lang), url=link, style="success", icon="open")])
    manage = []
    if request_callback:
        rows.append([_btn(t("btn_open_request", lang), callback_data=request_callback, icon="requests")])
    if update_callback:
        manage.append(_btn(t("btn_update", lang), callback_data=update_callback, icon="updates"))
    if delete_callback:
        manage.append(_btn(t("btn_delete", lang), callback_data=delete_callback, icon="delete", style="danger"))
    if subscribe_callback:
        label = subscribe_label or t("btn_subscribe", lang)
        rows.append([
            _btn(label, callback_data=subscribe_callback, icon="bell")
        ])
    if manage:
        rows.append(manage)
    if notify_all_callback:
        label = notify_all_label or t("btn_subscriptions", lang)
        rows.append([_btn(label, callback_data=notify_all_callback, icon="bell")])
    rows.append([_back(lang, back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_kb(lang: str, show_retry: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("btn_search_inline", lang), switch_inline_query="", icon="search")],
        [_back(lang, "catalog")],
    ])


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
    
    rows.append([_back(lang, "home")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu_kb(role: str | None = None, lang: str = "ru") -> InlineKeyboardMarkup:
    is_super = role == "super"
    rows = [
        [
            _btn(t("admin_btn_plugins", lang), callback_data="adm:section:plugins", icon="plugin"),
            _btn(t("poster_btn", lang), callback_data="pstr:admin", icon="send"),
        ],
    ]
    if is_super:
        rows.append([
            _btn(t("admin_btn_broadcast", lang), callback_data="adm:broadcast", icon="broadcast"),
            _btn(t("admin_btn_stats", lang), callback_data="adm:stats", icon="stats"),
        ])
    rows.append([_btn(t("admin_btn_config", lang), callback_data="adm:config", icon="settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_stats_kb(role: str | None = None, lang: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    if role == "super":
        rows.append([
            _btn(t("admin_btn_moderation_stats", lang), callback_data="adm:modstats:current", icon="vote"),
            _btn(t("admin_btn_banned", lang), callback_data="adm:banned:0", icon="ban"),
        ])
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_week_kb(
    moderators: list[tuple[int, str, int, int, int]],
    week: str,
    previous_week: str,
    next_week: str | None,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows = []
    for user_id, username, checked, yes, no in moderators:
        label = t(
            "admin_moderation_stats_person_button",
            lang,
            username=username,
            checked=checked,
            yes=yes,
            no=no,
        )
        rows.append([_btn(label, callback_data=f"adm:modstats:user:{week}:{user_id}", icon="profile")])
    nav = [_btn("‹", callback_data=f"adm:modstats:{previous_week}")]
    if next_week:
        nav.append(_btn("›", callback_data=f"adm:modstats:{next_week}"))
    rows.append(nav)
    rows.append([_back(lang, "adm:stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_person_kb(week: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_back(lang, f"adm:modstats:{week}")],
    ])


def admin_quiz_list_kb(items: List[Tuple[str, str]], page: int, total_pages: int,
                       lang: str = "ru") -> InlineKeyboardMarkup:
    rows = [[_btn(label, callback_data=f"adm:quiz:view:{qid}", icon="file")] for label, qid in items]
    _add_pager(rows, page, total_pages, "adm:quiz")
    rows.append([_btn(t("admin_quiz_btn_add", lang), callback_data="adm:quiz:add", icon="add", style="success")])
    rows.append([_btn(t("admin_quiz_btn_restore", lang), callback_data="adm:quiz:restore", icon="updates")])
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_quiz_item_kb(question_id: str, lang: str = "ru", edit_lang: str = "ru") -> InlineKeyboardMarkup:
    other = SUPPORTED_LANGUAGES[(SUPPORTED_LANGUAGES.index(edit_lang) + 1) % len(SUPPORTED_LANGUAGES)]
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"{LANGUAGE_FLAGS[edit_lang]} {t('admin_quiz_btn_lang', lang, value=edit_lang.upper())}",
              callback_data=f"adm:quiz:lang:{question_id}:{other}")],
        [_btn(t("admin_quiz_btn_text", lang), callback_data=f"adm:quiz:edit_text:{question_id}", icon="edit")],
        [_btn(t("admin_quiz_btn_options", lang), callback_data=f"adm:quiz:edit_options:{question_id}", icon="edit")],
        [_btn(t("btn_delete", lang), callback_data=f"adm:quiz:del:{question_id}", icon="delete", style="danger")],
        [_back(lang, "adm:cancel")],
    ])


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
        [_back(lang, "adm:cancel")],
    ])


def admin_backup_recipients_kb(recipient_ids: List[int], lang: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    for rid in recipient_ids:
        rows.append([
            _btn(str(rid), callback_data="adm:backup:rcp_noop", icon="profile"),
            _btn(t("btn_delete", lang), callback_data=f"adm:backup:rcp_rm:{rid}", icon="delete"),
        ])
    rows.append([_btn(t("btn_add", lang), callback_data="adm:backup:rcp_add", icon="add", style="success")])
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_maintenance_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("admin_btn_backup", lang), callback_data="adm:backup", icon="download"),
            _btn(t("admin_btn_audit_log", lang), callback_data="adm:auditlog:0", icon="file"),
        ],
        [_btn(t("admin_btn_publish_examples", lang), callback_data="adm:examples:publish", icon="art")],
        [_btn(t("admin_maint_health", lang), callback_data="adm:maint:health", icon="stats")],
        [_btn(t("admin_maint_sync_version", lang), callback_data="adm:maint:sync_version", icon="updates")],
        [_btn(t("admin_maint_sync_catalog", lang), callback_data="adm:maint:sync_catalog", icon="link")],
        [_btn(t("admin_maint_erase_id", lang), callback_data="adm:maint:erase_id", icon="delete")],
        [_btn(t("admin_maint_erase_hidden", lang), callback_data="adm:maint:erase", icon="delete", style="danger")],
        [_back(lang, "adm:cancel")],
    ])


def admin_maint_confirm_kb(action: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("btn_confirm", lang), callback_data=f"adm:maint:{action}:confirm", style="danger", icon="yes"),
            _dismiss(lang, "adm:maint"),
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
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_vote_settings_kb(prefs: dict, lang: str = "ru", counts: dict | None = None) -> InlineKeyboardMarkup:
    visible = prefs["show_templates"]
    visibility = t("admin_vote_templates_shown" if visible else "admin_vote_templates_hidden", lang)
    rows = [
        [_btn(t("admin_vote_templates_toggle", lang, state=visibility), callback_data="adm:vote_prefs:templates",
              icon="yes" if visible else "no", style="success" if visible else "danger")],
        [_btn(t(f"admin_vote_mode_{mode}", lang), callback_data=f"adm:vote_prefs:mode:{mode}",
              style="success" if prefs["template_input"] == mode else None)
         for mode in ("buttons", "reply")],
    ]
    for kinds in (("approve", "reject"), ("update_approve", "update_reject")):
        row = []
        for kind in kinds:
            label = t(f"admin_vote_templates_{kind}", lang)
            if counts and kind in counts:
                visible_count, total = counts[kind]
                label += f" ({visible_count}/{total})"
            row.append(_btn(label, callback_data=f"adm:vtemplates:{kind}:0", icon="file"))
        rows.append(row)
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_vote_templates_kb(kind: str, templates: list[str], hidden: set[str], page: int = 0,
                            lang: str = "ru") -> InlineKeyboardMarkup:
    _, indices, page, total = template_page(templates, page)
    buttons = []
    for index in indices:
        identity = reason_template_id(templates[index])
        visible = identity not in hidden
        buttons.append(_btn(str(index + 1), callback_data=f"adm:vtemplate:{kind}:{identity}:{page}",
                            icon="yes" if visible else "no", style="success" if visible else "danger"))
    width = max(1, (len(buttons) + 1) // 2)
    rows = [buttons[index:index + width] for index in range(0, len(buttons), width)]
    _add_pager(rows, page, total, f"adm:vtemplates:{kind}")
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_plugins_section_kb(lang: str = "ru", role: str | None = None) -> InlineKeyboardMarkup:
    rows = [
        [
            _btn(t("admin_btn_requests", lang), callback_data="adm:queue:plugins:0", icon="requests"),
            _btn(t("admin_btn_updates", lang), callback_data="adm:section:updates", icon="updates"),
        ],
        [
            _btn(t("admin_btn_approved", lang), callback_data="adm:queue:approved:0", icon="yes"),
            _btn(t("admin_btn_scheduled", lang), callback_data="adm:scheduled:0", icon="clock"),
        ],
    ]
    if role == "super":
        rows.append([
            _btn(t("admin_btn_edit_plugins", lang), callback_data="adm:edit_plugins", icon="edit"),
            _btn(t("admin_btn_link_author_search", lang), callback_data="adm:link_author", icon="link"),
        ])
        rows.append([
            _btn(t("admin_btn_audit", lang), callback_data="adm:audit:0", icon="file"),
            _btn(t("admin_btn_add_icon_pack", lang), callback_data="adm:pack:new", icon="art"),
        ])
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_AUDIT_FILTER_BUTTONS = (
    ("all", "admin_audit_filter_all"),
    ("published", "admin_audit_filter_published"),
    ("rejected", "admin_audit_filter_rejected"),
    ("pending", "admin_audit_filter_pending"),
    ("rework", "admin_audit_filter_rework"),
    ("scheduled", "admin_audit_filter_scheduled"),
    ("approved", "admin_audit_filter_approved"),
    ("deleted", "admin_audit_filter_deleted"),
)


def admin_rejected_kb(
    items: List[Tuple[str, str] | Tuple[str, str, str]],
    page: int,
    total_pages: int,
    status: str = "all",
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    filter_icons = {"all": "file", "published": "yes", "rejected": "no", "pending": "clock",
                    "rework": "edit", "scheduled": "calendar", "approved": "yes", "deleted": "delete"}
    filters = [
        _btn(t(text_key, lang), callback_data=f"adm:audit:{key}:0", icon=filter_icons[key],
             style="success" if key == status else None)
        for key, text_key in _AUDIT_FILTER_BUTTONS
    ]
    rows = [filters[index:index + 4] for index in range(0, len(filters), 4)]
    rows += [[_btn(item[0], callback_data=f"adm:rejreq:{request_callback_token(item[1])}",
                   icon=item[2] if len(item) > 2 else "file")] for item in items]
    _add_pager(rows, page, total_pages, f"adm:audit:{status}")
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_rejected_detail_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_rej_review", lang), callback_data=f"adm:review:{request_callback_token(request_id)}", icon="edit", style="success")],
        [_btn(t("admin_rej_delete", lang), callback_data=f"adm:rejdel:{request_callback_token(request_id)}", icon="delete", style="danger")],
        [_back(lang, "adm:cancel")],
    ])


def admin_updates_list_kb(
    items: List[Tuple[str, str]],
    page: int,
    total_pages: int,
    back_callback: str = "adm:section:plugins",
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.extend([[_btn(label, callback_data=f"adm:review:{request_callback_token(rid)}", icon="updates")] for label, rid in items])

    _add_pager(rows, page, total_pages, "adm:updates")

    rows.append([_back(lang, back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_post_section_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_btn_post", lang), callback_data="adm:post:new", icon="send")],
        [_btn(t("admin_btn_scheduled_posts", lang), callback_data="adm:scheduled_posts:0", icon="calendar")],
        [_back(lang, "adm:cancel")],
    ])


def admin_config_kb(lang: str = "ru", role: str | None = "super") -> InlineKeyboardMarkup:
    rows = [[
        _btn(t("admin_btn_vote_settings", lang), callback_data="adm:vote_prefs", icon="vote"),
        _btn(t("admin_btn_my_notifications", lang), callback_data="adm:notifs", icon="bell"),
    ]]
    if role == "super":
        rows.extend([
            [
                _btn(t("admin_cfg_section_admins", lang), callback_data="adm:config_section:admins", icon="admin"),
                _btn(t("admin_cfg_section_channels", lang), callback_data="adm:config_section:channels", icon="broadcast"),
            ],
            [
                _btn(t("admin_cfg_section_moderation", lang), callback_data="adm:config_section:moderation", icon="requests"),
                _btn(t("admin_cfg_section_other", lang), callback_data="adm:config_section:other", icon="settings"),
            ],
            [
                _btn(t("admin_btn_maintenance", lang), callback_data="adm:maint", icon="settings"),
            ],
        ])
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_config_admins_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_cfg_superadmins", lang), callback_data="adm:config:admins_super", icon="admin")],
        [_btn(t("admin_cfg_admins_plugins", lang), callback_data="adm:config:admins_plugins", icon="plugin")],
        [_back(lang, "adm:cancel")],
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
        [
            _btn(t("admin_cfg_archive_channel", lang), callback_data="adm:config:archive.channel_id", icon="file"),
            _btn(t("admin_cfg_archive_technical_channel", lang), callback_data="adm:config:archive.technical_channel_id", icon="file"),
        ],
        [_back(lang, "adm:cancel")],
    ])


def admin_config_moderation_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("admin_btn_quiz", lang), callback_data="adm:quiz:0", icon="requests"),
            _btn(t("admin_cfg_reject_templates", lang), callback_data="adm:rejtpl_cfg:reject", icon="file"),
        ],
        [
            _btn(t("admin_cfg_moderation_forum_chat_id", lang), callback_data="adm:config:moderation.forum_chat_id", icon="requests"),
            _btn(t("admin_cfg_moderation_forum_topic_id", lang), callback_data="adm:config:moderation.forum_topic_id", icon="file"),
        ],
        [
            _btn(t("admin_cfg_moderation_vote_threshold", lang), callback_data="adm:config:moderation.vote_threshold", icon="vote"),
            _btn(t("admin_cfg_moderation_notification_chat_ids", lang), callback_data="adm:config:moderation.notification_chat_ids", icon="bell"),
        ],
        [_btn(t("admin_cfg_moderation_min_supported_version", lang), callback_data="adm:config:moderation.min_supported_version", icon="updates")],
        [_btn(t("admin_cfg_moderation_require_vote_reason", lang), callback_data="adm:config:moderation.require_vote_reason", icon="vote")],
        [_btn(t("admin_cfg_moderation_send_reasons_to_author", lang), callback_data="adm:config:moderation.send_reasons_to_author", icon="send")],
        [_btn(t("admin_cfg_moderation_delete_review_notifications_on_decision", lang), callback_data="adm:config:moderation.delete_review_notifications_on_decision", icon="delete")],
        [_back(lang, "adm:cancel")],
    ])


def admin_config_other_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_cfg_checked_on_version", lang), callback_data="adm:config:checked_on_version", icon="yes")],
        [_back(lang, "adm:cancel")],
    ])


def admin_manage_admins_kb(
    field: str,
    admin_ids: List[int],
    lang: str = "ru",
    labels: dict[int, str] | None = None,
) -> InlineKeyboardMarkup:
    rows = []
    labels = labels or {}
    for admin_id in admin_ids:
        rows.append([
            _btn(labels.get(admin_id) or t("admin_unknown_username", lang), callback_data=f"adm:admins:noop:{field}", icon="profile"),
            _btn(t("btn_delete", lang), callback_data=f"adm:admins:rm:{field}:{admin_id}", icon="delete"),
        ])
    rows.append([_btn(t("btn_add", lang), callback_data=f"adm:admins:add:{field}", icon="add")])
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_broadcast_confirm_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("btn_send", lang), callback_data="adm:broadcast:confirm", icon="send")],
        [_cancel(lang, "adm:cancel")],
    ])


def admin_broadcast_kind_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_broadcast_all", lang), callback_data="adm:broadcast:all", icon="broadcast")],
        [_btn(t("admin_broadcast_moderation", lang), callback_data="adm:broadcast:moderation", icon="admin")],
        [_btn(t("admin_broadcast_history", lang), callback_data="adm:broadcast_history:0", icon="clock")],
        [_back(lang, "adm:cancel")],
    ])


def admin_broadcast_progress_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("btn_refresh", lang), callback_data="adm:broadcast:progress", icon="updates")],
        [_btn(t("admin_broadcast_history", lang), callback_data="adm:broadcast_history:0", icon="clock")],
        [_back(lang, "adm:cancel")],
    ])


def admin_broadcast_history_kb(items: list[tuple[str, str]], page: int, total_pages: int,
                               lang: str = "ru") -> InlineKeyboardMarkup:
    rows = [[_btn(label, callback_data=f"adm:broadcast_run:{run_id}", icon="broadcast")]
            for run_id, label in items]
    _add_pager(rows, page, total_pages, "adm:broadcast_history")
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        icon = (item[2] if len(item) > 2 else None) or "plugin"
        rows.append([_btn(label, callback_data=f"adm:review:{request_callback_token(rid)}", icon=icon)])
    
    _add_pager(rows, page, total_pages, f"adm:queue:{queue_type}")
    
    rows.append([_back(lang, back_callback)])
    
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

    _add_pager(rows, page, total_pages, "adm:banned")

    rows.append([_btn(t("kb_admin_ban_manual", lang), callback_data="adm:ban_manual", icon="ban", style="danger")])
    rows.append([_btn(t("admin_btn_blocklist", lang), callback_data="adm:blocklist", icon="ban")])
    rows.append([_btn(t("admin_btn_rejected_appeals", lang), callback_data="adm:rejapp:0", icon="file")])
    rows.append([_back(lang, back_callback)])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_rejected_appeals_kb(items: List[Tuple[str, str] | Tuple[str, str, str]], page: int, total_pages: int, lang: str = "ru") -> InlineKeyboardMarkup:
    rows = [[_btn(item[0], callback_data=f"adm:appd:{request_callback_token(item[1])}",
                  icon=item[2] if len(item) > 2 else "profile")] for item in items]
    _add_pager(rows, page, total_pages, "adm:rejapp")
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_rejected_appeal_detail_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("kb_appeal_unban", lang), callback_data=f"adm:appunb:{request_callback_token(request_id)}", icon="yes", style="success")],
        [_btn(t("admin_rej_delete", lang), callback_data=f"adm:appdel:{request_callback_token(request_id)}", icon="delete", style="danger")],
        [_back(lang, "adm:cancel")],
    ])


def admin_confirm_ban_user_kb(user_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("kb_admin_ban_delete", lang), callback_data=f"adm:banuid:{user_id}:del", icon="delete", style="danger")],
        [_btn(t("kb_admin_ban_keep", lang), callback_data=f"adm:banuid:{user_id}:keep", icon="ban")],
        [_dismiss(lang, "adm:cancel")],
    ])


def admin_review_kb(
    request_id: str,
    user_id: int,
    submit_label: Optional[str] = None,
    submit_callback: str | None = None,
    lang: str = "ru",
    allow_publish: bool = True,
    allow_vote: bool = True,
    media_count: int = 0,
    allow_approve: bool = False,
    share_query: str | None = None,
) -> InlineKeyboardMarkup:
    vote_token = request_callback_token(request_id)
    if not submit_callback:
        submit_callback = f"adm:prepublish:{vote_token}"
    elif submit_callback.endswith(f":{request_id}"):
        submit_callback = submit_callback.removesuffix(request_id) + vote_token
    submit_label = submit_label or t("btn_publish", lang)
    rows: list[list[InlineKeyboardButton]] = []
    if allow_publish and allow_approve:
        rows.append([_btn(t("admin_btn_approve", lang), callback_data=f"adm:approve:{vote_token}", icon="yes", style="success")])
    if allow_publish:
        rows.append([
            _btn(submit_label, callback_data=submit_callback, icon="yes"),
            _btn(t("btn_more", lang), callback_data=f"adm:actions:{request_callback_token(request_id)}", icon="menu"),
        ])
    if allow_vote:
        rows.append([
            _btn(t("btn_vote_yes", lang), callback_data=f"modvote:yes:{vote_token}", icon="yes", style="success"),
            _btn(t("btn_vote_no", lang), callback_data=f"modvote:no:{vote_token}", icon="no", style="danger"),
        ])
    if allow_vote and share_query:
        rows.append([_btn(t("admin_btn_public_vote", lang), switch_inline_query=share_query, icon="send")])
    if media_count:
        rows.append([_btn(t("admin_btn_show_media", lang, count=media_count),
                          callback_data=f"adm:media:{request_callback_token(request_id)}", icon="art")])
    if user_id:
        rows.append([_btn(t("kb_admin_msg_author", lang), callback_data=f"adm:msgauthor:{request_callback_token(request_id)}", icon="edit")])
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_vote_kb(request_id: str, yes_count: int = 0, no_count: int = 0, lang: str = "ru") -> InlineKeyboardMarkup:
    token = request_callback_token(request_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(f"{t('btn_vote_yes', lang)} ({yes_count})", callback_data=f"modvote:yes:{token}", icon="yes", style="success"),
            _btn(f"{t('btn_vote_no', lang)} ({no_count})", callback_data=f"modvote:no:{token}", icon="no", style="danger"),
        ],
    ])


def moderation_delete_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    token = request_callback_token(request_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("admin_submit_delete", lang), callback_data=f"adm:delete:{token}", icon="delete", style="danger")],
    ])


def author_rejected_kb(request_id: str, can_appeal: bool = False, lang: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    if can_appeal:
        rows.append([_btn(t("kb_submit_appeal", lang), callback_data=f"usr:appeal:{request_callback_token(request_id)}",
                          icon="updates", style="success")])
    rows.append([_btn(t("kb_contact_moderation", lang), callback_data=f"usr:modcontact:{request_callback_token(request_id)}", icon="support")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dialog_author_reply_kb(request_id: str, author_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("kb_dialog_ban_author", lang), callback_data=f"dlg:ban:{author_id}:{request_callback_token(request_id)}",
              icon="ban", style="danger")],
    ])


def dialog_reply_confirm_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn(t("dialog_reply_send", lang), callback_data="dlg:reply:send", icon="send", style="success"),
        _cancel(lang, "dlg:reply:cancel"),
    ]])


def moderation_vote_reason_kb(
    request_id: str,
    owner_id: int,
    anonymous: bool = False,
    has_templates: bool = False,
    allow_no_reason: bool = False,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    token = request_callback_token(request_id)
    allowed = templates_allowed(get_request_by_callback_token(request_id))
    anon_key = "kb_vote_anon_on" if anonymous else "kb_vote_anon_off"
    rows = [[_btn(t(anon_key, lang), callback_data=f"mvr:anon:{owner_id}:{token}",
                  icon="profile", style="success" if anonymous else None)]]
    if has_templates and allowed and moderation_preferences(owner_id)["show_templates"]:
        rows.append([_btn(t("kb_vote_reason_tpl", lang), callback_data=f"mvr:tpl:{owner_id}:{token}", icon="file")])
    rows.append([_btn(t("kb_vote_reason_own", lang), callback_data=f"mvr:own:{owner_id}:{token}", icon="edit")])
    if allow_no_reason and allowed:
        rows.append([_btn(t("kb_vote_reason_skip", lang), callback_data=f"mvr:none:{owner_id}:{token}", icon="yes")])
    rows.append([_btn(t("kb_vote_cancel", lang), callback_data=f"mvr:cancel:{owner_id}:{token}", icon="no", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_vote_template_kb(request_id: str, owner_id: int, templates: List[str], lang: str = "ru", page: int = 0,
                                selected: list[int] | None = None, mode: str = "buttons") -> InlineKeyboardMarkup:
    if not templates_allowed(get_request_by_callback_token(request_id)):
        return moderation_vote_reason_kb(request_id, owner_id, lang=lang)
    token = request_callback_token(request_id)
    _, indices, page, total = template_page(templates, page)
    selected = selected or []
    buttons = [
        _btn(str(idx + 1), callback_data=f"mvr:t:{owner_id}:{idx}:{token}",
             icon="yes" if idx in selected else "file", style="success" if idx in selected else None)
        for idx in indices if mode == "buttons"
    ]
    width = max(1, (len(buttons) + 1) // 2)
    rows = [buttons[index:index + width] for index in range(0, len(buttons), width)]
    _add_pager(rows, page, total, callback=lambda number: f"mvr:p:{owner_id}:{number}:{token}")
    if selected:
        rows.append([_btn(t("kb_vote_templates_send", lang), callback_data=f"mvr:go:{owner_id}:{token}",
                          icon="yes", style="success")])
        rows.append([_btn(t("kb_vote_templates_clear", lang), callback_data=f"mvr:clear:{owner_id}:{token}", icon="delete")])
    rows.append([_back(lang, f"mvr:back:{owner_id}:{token}")])
    rows[-1].append(_btn(t("kb_vote_cancel", lang), callback_data=f"mvr:cancel:{owner_id}:{token}", icon="no", style="danger"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_inline_vote_url_kb(bot_username: str, request_id: str, yes_count: int = 0, no_count: int = 0, lang: str = "ru") -> InlineKeyboardMarkup:
    token = request_deeplink_token(request_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(f"{t('btn_vote_yes', lang)} ({yes_count})", url=f"https://t.me/{bot_username}?start=modvote_yes_{token}", icon="yes", style="success"),
            _btn(f"{t('btn_vote_no', lang)} ({no_count})", url=f"https://t.me/{bot_username}?start=modvote_no_{token}", icon="no", style="danger"),
        ],
    ])


def admin_actions_kb(request_id: str, allow_ban: bool = False, lang: str = "ru") -> InlineKeyboardMarkup:
    token = request_callback_token(request_id)
    rows = [[_btn(t("kb_admin_reject", lang), callback_data=f"adm:reject:{token}", icon="no", style="danger")]]
    if allow_ban:
        rows.append([_btn(t("kb_admin_ban", lang), callback_data=f"adm:ban:{token}", icon="ban", style="danger")])
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_reject_kb(request_id: str, lang: str = "ru", show_votes: bool = False, allow_templates: bool = True) -> InlineKeyboardMarkup:
    token = request_callback_token(request_id)
    votes_key = "kb_admin_reject_votes_on" if show_votes else "kb_admin_reject_votes_off"
    allow_templates = allow_templates and templates_allowed(get_request_by_callback_token(request_id))
    reasons = [_btn(t("kb_admin_reject_with_reason", lang), callback_data=f"adm:reject_comment:{token}", icon="edit")]
    rows = [
        [_btn(t(votes_key, lang), callback_data=f"adm:reject_votes:{token}",
              icon=("yes" if show_votes else "no"), style=("success" if show_votes else None))],
        reasons,
    ]
    if allow_templates:
        reasons.append(_btn(t("kb_admin_reject_template", lang), callback_data=f"adm:rejtpl_pick:{token}", icon="file"))
        rows.append([_btn(t("kb_admin_reject_silent", lang), callback_data=f"adm:reject_silent:{token}", icon="no")])
    rows.extend([
        [_btn(t("kb_admin_rework", lang), callback_data=f"adm:rework:{token}", icon="updates")],
        [_back(lang, "adm:cancel")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_reject_templates_kb(
    request_id: str,
    templates: List[str],
    selected: List[int],
    lang: str = "ru",
    page: int = 0,
) -> InlineKeyboardMarkup:
    if not templates_allowed(get_request_by_callback_token(request_id)):
        return admin_reject_kb(request_id, lang=lang, allow_templates=False)
    buttons = []
    _, indices, page, total = template_page(templates, page)
    for idx in indices:
        if idx in selected:
            label = f"{idx + 1} [{selected.index(idx) + 1}]"
            style = "success"
        else:
            label = str(idx + 1)
            style = None
        buttons.append(_btn(label, callback_data=f"adm:rejtpl_t:{request_callback_token(request_id)}:{idx}",
                            style=style, icon="yes" if idx in selected else None))
    width = max(1, (len(buttons) + 1) // 2)
    rows = [buttons[index:index + width] for index in range(0, len(buttons), width)]
    _add_pager(rows, page, total, f"adm:rejtpl_page:{request_callback_token(request_id)}")
    if selected:
        rows.append([_btn(t("kb_admin_reject_tpl_send", lang),
                          callback_data=f"adm:rejtpl_go:{request_callback_token(request_id)}", icon="yes", style="success")])
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_reject_templates_cfg_kb(templates: List[str], kind: str = "reject", lang: str = "ru", page: int = 0) -> InlineKeyboardMarkup:
    _, indices, page, total = template_page(templates, page)
    rows = [
        [_btn(str(idx + 1), callback_data=f"adm:rejtpl_del:{kind}:{idx}", icon="delete")]
        for idx in indices
    ]
    _add_pager(rows, page, total, f"adm:rejtpl_cfgpage:{kind}")
    rows.append([_btn(t("kb_admin_rejtpl_add", lang), callback_data=f"adm:rejtpl_add:{kind}", icon="edit", style="success")])
    kinds = ("approve", "reject", "update_approve", "update_reject")
    for other in kinds:
        if other == kind:
            continue
        rows.append([_btn(t(f"kb_admin_tpl_switch_{other}", lang), callback_data=f"adm:rejtpl_cfg:{other}",
                          icon=("yes" if other.endswith("approve") else "no"))])
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_confirm_delete_plugin_kb(slug: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("btn_confirm", lang), callback_data=f"adm_edit:delete_confirm:{encode_slug(slug)}", icon="yes", style="success"),
            _dismiss(lang, "adm:cancel"),
        ]
    ])


def admin_confirm_ban_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("kb_admin_ban_delete", lang), callback_data=f"adm:ban_confirm:{request_callback_token(request_id)}:del", icon="delete", style="danger")],
        [_btn(t("kb_admin_ban_keep", lang), callback_data=f"adm:ban_confirm:{request_callback_token(request_id)}:keep", icon="ban")],
        [_dismiss(lang, "adm:cancel")],
    ])


def moderation_appeal_kb(request_id: str, yes_count: int = 0, no_count: int = 0, lang: str = "ru") -> InlineKeyboardMarkup:
    vote_token = request_callback_token(request_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(f"{t('btn_vote_yes', lang)} ({yes_count})", callback_data=f"modvote:yes:{vote_token}", icon="yes", style="success"),
            _btn(f"{t('btn_vote_no', lang)} ({no_count})", callback_data=f"modvote:no:{vote_token}", icon="no", style="danger"),
        ],
        [
            _btn(t("kb_appeal_unban", lang), callback_data=f"adm:appeal:approve:{request_callback_token(request_id)}", icon="yes", style="success"),
            _btn(t("kb_appeal_deny", lang), callback_data=f"adm:appeal:deny:{request_callback_token(request_id)}", icon="no", style="danger"),
        ],
        [_btn(t("kb_appeal_banfinal", lang), callback_data=f"adm:appeal:banfinal:{request_callback_token(request_id)}", icon="ban", style="danger")],
    ])


def banned_appeal_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t("kb_appeal_submit", lang), callback_data="appeal:start", icon="edit")],
    ])


def admin_appeal_decision_kb(request_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(t("kb_appeal_unban", lang), callback_data=f"adm:appeal:approve:{request_callback_token(request_id)}", icon="yes", style="success"),
            _btn(t("kb_appeal_deny", lang), callback_data=f"adm:appeal:deny:{request_callback_token(request_id)}", icon="no", style="danger"),
        ],
        [_btn(t("kb_appeal_banfinal", lang), callback_data=f"adm:appeal:banfinal:{request_callback_token(request_id)}", icon="ban", style="danger")],
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
    
    _add_pager(rows, page, total_pages, list_prefix)
    
    rows.append([_back(lang, back_callback)])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_audit_log_kb(page: int, total_pages: int, lang: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    _add_pager(rows, page, total_pages, "adm:auditlog")
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_blocklist_kb(items: List[str], lang: str = "ru") -> InlineKeyboardMarkup:
    rows = [
        [_btn(pid, callback_data=f"adm:unblock:{pid}", icon="ban", style="danger")]
        for pid in items[:40]
    ]
    rows.append([_back(lang, "adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
