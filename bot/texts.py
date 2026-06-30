from typing import Any, Dict

from bot.icons import ICONS, emoji_html

DEFAULT_LANGUAGE = "ru"

TEXTS: Dict[str, Dict[str, str]] = {
    "category_informational_label_btn": {"ru": "Информационные", "en": "Informational"},
    "category_informational_label_msg": {
        "ru": f'{emoji_html("informational", "📊")} Информационные',
        "en": f'{emoji_html("informational", "📊")} Informational',
    },
    "category_informational_tag": {
        "ru": "#Информационные",
        "en": "#Informational",
    },
    "category_utilities_label_btn": {"ru": "Утилиты", "en": "Utilities"},
    "category_utilities_label_msg": {
        "ru": f'{emoji_html("utilities", "🛠")} Утилиты',
        "en": f'{emoji_html("utilities", "🛠")} Utilities',
    },
    "category_utilities_tag": {
        "ru": "#Утилиты",
        "en": "#Utilities",
    },
    "category_customization_label_btn": {"ru": "Кастомизация", "en": "Customization"},
    "category_customization_label_msg": {
        "ru": f'{emoji_html("customization", "🎨")} Кастомизация',
        "en": f'{emoji_html("customization", "🎨")} Customization',
    },
    "category_customization_tag": {
        "ru": "#Кастомизация",
        "en": "#Customization",
    },
    "category_fun_label_btn": {"ru": "Развлечения", "en": "Fun"},
    "category_fun_label_msg": {
        "ru": f'{emoji_html("fun", "🎮")} Развлечения',
        "en": f'{emoji_html("fun", "🎮")} Fun',
    },
    "category_fun_tag": {
        "ru": "#Развлечения",
        "en": "#Fun",
    },
    "category_library_label_btn": {"ru": "Библиотека", "en": "Library"},
    "category_library_label_msg": {
        "ru": f'{emoji_html("library", "📚")} Библиотека',
        "en": f'{emoji_html("library", "📚")} Library',
    },

    "all_plugins_title": {
        "ru": f'{emoji_html("all_plugins", "🧩")} <b>Все плагины</b>',
        "en": f'{emoji_html("all_plugins", "🧩")} <b>All plugins</b>',
    },
    "category_library_tag": {
        "ru": "#Библиотека",
        "en": "#Library",
    },
    "admin_author_linked": {
        "ru": "Автор привязан к плагину",
        "en": "Author linked to plugin",
    },
    "admin_btn_banned": {"ru": "Баны", "en": "Bans"},
    "admin_btn_broadcast": {"ru": "Рассылка", "en": "Broadcast"},
    "admin_btn_config": {"ru": "Настройки", "en": "Settings"},
    "admin_btn_sources": {"ru": "Источники", "en": "Sources"},
    "admin_btn_maintenance": {"ru": "Обслуживание", "en": "Maintenance"},
    "admin_btn_backup": {"ru": "Бэкап БД", "en": "DB backup"},
    "admin_backup_title": {
        "ru": "<b>Бэкап базы данных</b>\n\nДоступно только владельцу. Бот пришлёт архив с БД.\n\n<b>Автобэкап:</b> {state}",
        "en": "<b>Database backup</b>\n\nOwner-only. The bot sends the DB as an archive.\n\n<b>Auto-backup:</b> {state}",
    },
    "admin_backup_now": {"ru": "Сделать бэкап сейчас", "en": "Back up now"},
    "admin_backup_auto_on": {"ru": "Автобэкап: включён", "en": "Auto-backup: on"},
    "admin_backup_auto_off": {"ru": "Автобэкап: выключен", "en": "Auto-backup: off"},
    "admin_backup_auto_state": {"ru": "включён, раз в {hours} ч", "en": "on, every {hours}h"},
    "admin_backup_interval": {"ru": "Интервал: {hours} ч", "en": "Interval: {hours}h"},
    "admin_backup_started": {"ru": "Создаю бэкап…", "en": "Creating backup…"},
    "admin_backup_sent": {"ru": "✅ Бэкап отправлен.", "en": "✅ Backup sent."},
    "admin_backup_failed": {"ru": "Не удалось создать/отправить бэкап.", "en": "Failed to create/send the backup."},
    "admin_maint_title": {
        "ru": "<b>Обслуживание</b>\n\nДиагностика и ручные операции с каталогом и заявками.",
        "en": "<b>Maintenance</b>\n\nDiagnostics and manual catalog/request operations.",
    },
    "admin_maint_health": {"ru": "Health / диагностика", "en": "Health / diagnostics"},
    "admin_maint_sync_version": {"ru": "Обновить версию плагина", "en": "Sync plugin version"},
    "admin_maint_sync_catalog": {"ru": "Привязать пост к заявке", "en": "Link post to request"},
    "admin_maint_erase_id": {"ru": "Удалить заявки по ID", "en": "Delete requests by ID"},
    "admin_maint_erase_hidden": {"ru": "Очистить скрытые заявки", "en": "Clear hidden requests"},
    "admin_maint_prompt_sync_version": {
        "ru": "Введите <code>id_или_slug версия</code>\n\nНапример: <code>voicetiming 1.2.3</code>",
        "en": "Send <code>id_or_slug version</code>\n\nExample: <code>voicetiming 1.2.3</code>",
    },
    "admin_maint_prompt_sync_catalog": {
        "ru": "Введите <code>ссылка_на_пост request_id</code>\n\nНапример: <code>https://t.me/channel/123 abc-123</code>",
        "en": "Send <code>post_link request_id</code>\n\nExample: <code>https://t.me/channel/123 abc-123</code>",
    },
    "admin_maint_prompt_erase_id": {
        "ru": "Введите <code>id_или_slug</code> плагина — будут удалены все связанные заявки.",
        "en": "Send a plugin <code>id_or_slug</code> — all its requests will be deleted.",
    },
    "admin_maint_erase_confirm": {
        "ru": "Очистить все <b>скрытые</b> заявки (не draft/pending/scheduled/error)?",
        "en": "Clear all <b>hidden</b> requests (not draft/pending/scheduled/error)?",
    },
    "admin_maint_done": {"ru": "✅ Готово: {result}", "en": "✅ Done: {result}"},
    "admin_maint_bad_format": {"ru": "Неверный формат, попробуйте ещё раз.", "en": "Invalid format, try again."},
    "admin_sources_title": {
        "ru": "<b>Кастомные источники</b>\n\nВыберите источник или добавьте новый.",
        "en": "<b>Custom sources</b>\n\nPick a source or add a new one.",
    },
    "admin_sources_empty": {
        "ru": "<b>Кастомные источники</b>\n\nПока нет ни одного источника.",
        "en": "<b>Custom sources</b>\n\nNo sources yet.",
    },
    "admin_source_add": {"ru": "Добавить источник", "en": "Add source"},
    "admin_source_attach": {"ru": "Привязать плагин", "en": "Attach a plugin"},
    "admin_source_delete": {"ru": "Удалить источник", "en": "Delete source"},
    "admin_source_detail": {
        "ru": "<b>{title}</b>\n\n<b>Username:</b> @{username}\n<b>Ссылка:</b> {link}\n<b>Плагинов привязано:</b> {count}",
        "en": "<b>{title}</b>\n\n<b>Username:</b> @{username}\n<b>Link:</b> {link}\n<b>Plugins attached:</b> {count}",
    },
    "admin_source_prompt_username": {
        "ru": "Введите <b>username</b> источника (например, <code>@meeowPlugins</code> или <code>meeowPlugins</code>):",
        "en": "Send the source <b>username</b> (e.g. <code>@meeowPlugins</code> or <code>meeowPlugins</code>):",
    },
    "admin_source_prompt_title": {
        "ru": "Введите <b>заголовок</b> источника (отображаемое имя). Отправьте <code>-</code>, чтобы пропустить.",
        "en": "Send a <b>title</b> (display name). Send <code>-</code> to skip.",
    },
    "admin_source_prompt_link": {
        "ru": "Введите <b>ссылку</b> источника. Отправьте <code>-</code>, чтобы использовать t.me/username.",
        "en": "Send a <b>link</b>. Send <code>-</code> to use t.me/username.",
    },
    "admin_source_prompt_attach": {
        "ru": "Введите <code>slug</code> или <code>id</code> плагина, который привязать к этому источнику:",
        "en": "Send a plugin <code>slug</code> or <code>id</code> to attach to this source:",
    },
    "admin_source_added": {"ru": "✅ Источник <b>{title}</b> сохранён.", "en": "✅ Source <b>{title}</b> saved."},
    "admin_source_deleted": {"ru": "✅ Источник удалён.", "en": "✅ Source deleted."},
    "admin_source_del_confirm": {
        "ru": "Удалить источник <b>{title}</b>? Привязанные плагины сохранятся, но потеряют этот источник в фильтре.",
        "en": "Delete source <b>{title}</b>? Attached plugins remain but lose this source in the filter.",
    },
    "admin_source_attached": {"ru": "✅ Плагин <code>{slug}</code> привязан.", "en": "✅ Plugin <code>{slug}</code> attached."},
    "admin_source_attach_notfound": {"ru": "Плагин не найден.", "en": "Plugin not found."},
    "admin_source_not_found": {"ru": "Источник не найден.", "en": "Source not found."},
    "poster_btn": {"ru": "Отложенные посты", "en": "Scheduled posts"},
    "poster_home": {
        "ru": "<b>Poster</b>\n\nВыберите канал или добавьте новый. Бот должен быть админом канала с правом публикации.",
        "en": "<b>Poster</b>\n\nPick a channel or add a new one. The bot must be a channel admin with post rights.",
    },
    "poster_home_empty": {
        "ru": "<b>Poster</b>\n\nУ вас пока нет каналов. Добавьте бота админом в свой канал и нажмите «Добавить канал».",
        "en": "<b>Poster</b>\n\nNo channels yet. Add the bot as an admin to your channel, then tap “Add channel”.",
    },
    "poster_btn_add_channel": {"ru": "Добавить канал", "en": "Add channel"},
    "poster_btn_my_posts": {"ru": "Мои отложенные", "en": "My scheduled"},
    "poster_btn_new_post": {"ru": "Новый пост", "en": "New post"},
    "poster_btn_updated_plugins": {"ru": "Пост: обновлённые плагины", "en": "Post: updated plugins"},
    "poster_updated_empty": {"ru": "Список обновлённых плагинов пуст.", "en": "The updated-plugins list is empty."},
    "poster_btn_remove_channel": {"ru": "Отвязать канал", "en": "Remove channel"},
    "poster_btn_skip": {"ru": "⏭ Пропустить", "en": "⏭ Skip"},
    "poster_time_1h": {"ru": "+1ч", "en": "+1h"},
    "poster_time_3h": {"ru": "+3ч", "en": "+3h"},
    "poster_time_24h": {"ru": "+24ч", "en": "+24h"},
    "poster_add_channel_prompt": {
        "ru": "Перешлите любой пост из вашего канала, или отправьте его <code>@username</code> / <code>-100…</code> ID.\n\nБот должен быть в нём админом, и вы тоже.",
        "en": "Forward any post from your channel, or send its <code>@username</code> / <code>-100…</code> ID.\n\nThe bot must be an admin there, and so must you.",
    },
    "poster_channel_added": {"ru": "✅ Канал <b>{title}</b> добавлен.", "en": "✅ Channel <b>{title}</b> added."},
    "poster_channel_access": {
        "ru": "Доступ к публикации есть у админов канала: {admins}",
        "en": "Posting access belongs to the channel admins: {admins}",
    },
    "poster_btn_edit_text": {"ru": "Изменить текст", "en": "Edit text"},
    "poster_btn_edit_media": {"ru": "Медиа", "en": "Media"},
    "poster_btn_edit_buttons": {"ru": "Кнопки", "en": "Buttons"},
    "poster_btn_schedule": {"ru": "Запланировать", "en": "Schedule"},
    "poster_preview_header": {"ru": "<b>Предпросмотр поста</b>", "en": "<b>Post preview</b>"},
    "poster_preview_meta": {
        "ru": "<b>Медиа:</b> {media}\n<b>Кнопки:</b> {buttons}",
        "en": "<b>Media:</b> {media}\n<b>Buttons:</b> {buttons}",
    },
    "poster_preview_media_photo": {"ru": "фото", "en": "photo"},
    "poster_preview_media_video": {"ru": "видео", "en": "video"},
    "poster_preview_media_none": {"ru": "нет", "en": "none"},
    "poster_channel_detail": {
        "ru": "<b>{title}</b>\n@{username}\n\nЧто делаем?",
        "en": "<b>{title}</b>\n@{username}\n\nWhat next?",
    },
    "poster_channel_removed": {"ru": "Канал отвязан", "en": "Channel removed"},
    "poster_compose_text": {
        "ru": "Отправьте <b>текст</b> поста (с форматированием и кастом-эмодзи поддерживается):",
        "en": "Send the post <b>text</b> (formatting and custom emoji supported):",
    },
    "poster_compose_media": {
        "ru": "Прикрепите <b>фото</b> или <b>видео</b>, либо пропустите.",
        "en": "Attach a <b>photo</b> or <b>video</b>, or skip.",
    },
    "poster_compose_buttons": {
        "ru": "Отправьте кнопки — по одной на строку в формате <code>Текст | https://ссылка</code>.\n\nЦвет кнопки — добавьте в конце <code>::red</code> или <code>::green</code>, например <code>Текст | https://ссылка::red</code>. Или пропустите.",
        "en": "Send buttons — one per line as <code>Text | https://link</code>.\n\nColor a button by appending <code>::red</code> or <code>::green</code>, e.g. <code>Text | https://link::red</code>. Or skip.",
    },
    "poster_compose_time": {
        "ru": "Когда опубликовать? Выберите быстрый вариант или отправьте дату <code>ДД.ММ.ГГГГ ЧЧ:ММ</code> (UTC+5).",
        "en": "When to publish? Pick a quick option or send a date <code>DD.MM.YYYY HH:MM</code> (UTC+5).",
    },
    "poster_scheduled": {"ru": "✅ Пост запланирован на <b>{datetime}</b>.", "en": "✅ Post scheduled for <b>{datetime}</b>."},
    "poster_my_posts": {"ru": "<b>Запланированные посты</b>\n\nНажмите, чтобы отменить:", "en": "<b>Scheduled posts</b>\n\nTap to cancel:"},
    "poster_my_posts_empty": {"ru": "Запланированных постов нет.", "en": "No scheduled posts."},
    "poster_post_canceled": {"ru": "Пост отменён", "en": "Post canceled"},
    "poster_err_not_found": {"ru": "Канал не найден.", "en": "Channel not found."},
    "poster_err_not_channel": {"ru": "Это не канал.", "en": "That's not a channel."},
    "poster_err_bot_not_admin": {"ru": "Бот не админ канала (нужно право публикации).", "en": "The bot is not a channel admin (post rights required)."},
    "poster_err_user_not_admin": {"ru": "Вы не админ этого канала.", "en": "You are not an admin of this channel."},
    "poster_err_not_media": {"ru": "Пришлите фото или видео, либо пропустите.", "en": "Send a photo or video, or skip."},
    "poster_err_bad_buttons": {"ru": "Не распознал кнопки. Формат: <code>Текст | https://ссылка</code>.", "en": "Couldn't parse buttons. Format: <code>Text | https://link</code>."},
    "poster_err_bad_time": {"ru": "Неверная дата. Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>.", "en": "Invalid date. Format: <code>DD.MM.YYYY HH:MM</code>."},
    "poster_err_past_time": {"ru": "Время уже прошло, укажите будущее.", "en": "That time is in the past."},
    "admin_btn_my_notifications": {"ru": "Мои уведомления", "en": "My notifications"},
    "admin_btn_check_updates": {"ru": "Проверить обновления", "en": "Check updates"},
    "admin_btn_scheduled": {"ru": "Отложенные", "en": "Scheduled"},
    "admin_btn_scheduled_posts": {"ru": "Посты по расписанию", "en": "Scheduled posts"},
    "admin_btn_edit": {"ru": "Редактировать", "en": "Edit"},
    "admin_btn_link_author": {"ru": "Привязать автора", "en": "Link author"},
    "admin_btn_plugins": {"ru": "Плагины", "en": "Plugins"},
    "admin_btn_icons": {"ru": "Иконки", "en": "Icons"},
    "admin_btn_requests": {"ru": "Заявки", "en": "Requests"},
    "admin_btn_updates": {"ru": "Обновления", "en": "Updates"},
    "admin_btn_edit_plugins": {"ru": "Каталог", "en": "Catalog"},
    "admin_btn_link_author_search": {"ru": "Авторы", "en": "Authors"},
    "admin_btn_edit_icons": {"ru": "Каталог", "en": "Catalog"},
    "admin_btn_link_author_icons": {"ru": "Авторы", "en": "Authors"},
    "admin_section_plugins": {"ru": "<b>Плагины</b>", "en": "<b>Plugins</b>"},
    "admin_section_icons": {"ru": "<b>Иконки</b>", "en": "<b>Icons</b>"},
    "admin_btn_queue_icons": {"ru": "Заявки иконок", "en": "Icon requests"},
    "admin_btn_queue_plugins": {"ru": "Заявки плагинов", "en": "Plugin requests"},
    "admin_btn_stats": {"ru": "Статистика", "en": "Stats"},
    "admin_cfg_admins_icons": {"ru": "Админы иконок", "en": "Icon admins"},
    "admin_cfg_admins_plugins": {"ru": "Админы плагинов", "en": "Plugin admins"},
    "admin_cfg_admins": {"ru": "Админы", "en": "Admins"},
    "admin_cfg_channel": {"ru": "Канал", "en": "Channel"},
    "admin_cfg_section_admins": {"ru": "Админы", "en": "Admins"},
    "admin_cfg_section_channels": {"ru": "Каналы", "en": "Channels"},
    "admin_cfg_section_moderation": {"ru": "Модерация", "en": "Moderation"},
    "admin_cfg_section_other": {"ru": "Прочее", "en": "Other"},
    "admin_cfg_channel_id": {"ru": "ID канала плагинов", "en": "Plugin channel ID"},
    "admin_cfg_channel_username": {"ru": "Username плагинов", "en": "Plugin username"},
    "admin_cfg_channel_title": {"ru": "Название плагинов", "en": "Plugin title"},
    "admin_cfg_publish_channel": {"ru": "Публикация", "en": "Publish channel"},
    "admin_cfg_channel_default_tags": {"ru": "Теги плагинов", "en": "Plugin tags"},
    "admin_cfg_channel_locale_order": {"ru": "Языки плагинов", "en": "Plugin locales"},
    "admin_cfg_icons_channel_id": {"ru": "ID канала иконок", "en": "Icon channel ID"},
    "admin_cfg_icons_channel_username": {"ru": "Username иконок", "en": "Icon username"},
    "admin_cfg_icons_channel_title": {"ru": "Название иконок", "en": "Icon title"},
    "admin_cfg_icons_channel_default_tags": {"ru": "Теги иконок", "en": "Icon tags"},
    "admin_cfg_icons_channel_locale_order": {"ru": "Языки иконок", "en": "Icon locales"},
    "admin_cfg_moderation_forum_chat_id": {"ru": "ID форума", "en": "Forum ID"},
    "admin_cfg_moderation_forum_topic_id": {"ru": "ID топика", "en": "Topic ID"},
    "admin_cfg_moderation_vote_threshold": {"ru": "Порог голосов", "en": "Vote threshold"},
    "admin_cfg_moderation_notification_chat_ids": {"ru": "Чаты уведомлений", "en": "Notification chats"},
    "admin_cfg_moderation_delete_review_notifications_on_decision": {
        "ru": "Удалять заявки после решения",
        "en": "Delete review messages after decision",
    },
    "admin_cfg_checked_on_version": {"ru": "Версия проверки", "en": "Checked version"},
    "admin_notifications_title": {"ru": "<b>Мои уведомления</b>", "en": "<b>My notifications</b>"},
    "admin_notify_pref_new_plugins": {"ru": "Новые плагины", "en": "New plugins"},
    "admin_notify_pref_updates": {"ru": "Обновления", "en": "Updates"},
    "admin_notify_pref_deletions": {"ru": "Удаления", "en": "Deletions"},
    "admin_notify_pref_icons": {"ru": "Иконки", "en": "Icons"},
    "admin_notify_pref_threshold": {"ru": "Порог голосов", "en": "Vote threshold"},
    "admin_notify_pref_on": {"ru": "включено", "en": "on"},
    "admin_notify_pref_off": {"ru": "выключено", "en": "off"},
    "admin_cfg_superadmins": {"ru": "Суперадмины", "en": "Superadmins"},
    "admin_choose_action": {"ru": "Выберите действие:", "en": "Choose action:"},
    "admin_added": {"ru": "Добавлен: <code>{admin_id}</code>", "en": "Added: <code>{admin_id}</code>"},
    "admin_added_short": {"ru": "Добавлен: {admin_id}", "en": "Added: {admin_id}"},
    "admin_removed": {"ru": "Удалён: <code>{admin_id}</code>", "en": "Removed: <code>{admin_id}</code>"},
    "admin_removed_short": {"ru": "Удалён: {admin_id}", "en": "Removed: {admin_id}"},
    "admin_denied": {
        "ru": "Иди нахуй.",
        "en": "Access denied",
    },
    "admin_request_processing": {
        "ru": "Заявка уже обрабатывается, подождите…",
        "en": "This request is already being processed, please wait…",
    },
    "menu_owner_mismatch": {
        "ru": "Это меню открыто другим пользователем.",
        "en": "This menu belongs to another user.",
    },
    "admin_updates_check_started": {
        "ru": "Проверка обновлений запущена",
        "en": "Updates check started",
    },
    "admin_updates_check_already_running": {
        "ru": "Проверка обновлений уже запущена",
        "en": "Updates check is already running",
    },
    "admin_updates_check_done": {
        "ru": "Проверка обновлений завершена",
        "en": "Updates check completed",
    },
    "admin_broadcast_confirm": {
        "ru": "Отправить всем пользователям?",
        "en": "Send to all users?",
    },
    "admin_broadcast_cancelled": {
        "ru": "Отменено",
        "en": "Cancelled",
    },
    "admin_broadcast_no_text": {
        "ru": "Нет текста для рассылки",
        "en": "No broadcast text",
    },
    "admin_prompt_broadcast": {
        "ru": "Введите сообщение для рассылки:",
        "en": "Enter broadcast message:",
    },
    "admin_prompt_enter_text_ru": {
        "ru": "Введите текст (RU):",
        "en": "Enter text (RU):",
    },
    "admin_prompt_enter_text_en": {
        "ru": "Введите текст (EN):",
        "en": "Enter text (EN):",
    },
    "admin_prompt_enter_admin_id": {
        "ru": "Введите ID админа:",
        "en": "Enter admin ID:",
    },
    "admin_prompt_search_plugin": {
        "ru": "Введите запрос для поиска плагина:",
        "en": "Enter plugin search query:",
    },
    "admin_prompt_channel": {
        "ru": "Введите канал: <code>id username title publish_channel</code>",
        "en": "Enter channel: <code>id username title publish_channel</code>",
    },
    "admin_prompt_channel_example": {
        "ru": "Пример: <code>-1001234567890 mychannel ExteraPlugins exteraplugintest</code>",
        "en": "Example: <code>-1001234567890 mychannel ExteraPlugins exteraplugintest</code>",
    },
    "admin_prompt_config_value": {
        "ru": "Введите новое значение для <b>{name}</b>.\n\n<b>Текущее:</b> <code>{current}</code>",
        "en": "Enter new value for <b>{name}</b>.\n\n<b>Current:</b> <code>{current}</code>",
    },
    "admin_prompt_config_list": {
        "ru": "Введите значения для <b>{name}</b> через запятую.\n\n<b>Текущее:</b> <code>{current}</code>",
        "en": "Enter values for <b>{name}</b> separated by commas.\n\n<b>Current:</b> <code>{current}</code>",
    },
    "admin_config_updated": {
        "ru": "Настройка обновлена",
        "en": "Setting updated",
    },
    "admin_prompt_checked_on_version": {
        "ru": "Введите версию для поля «Проверено» (например <code>12.5.1</code>). Дата подставится автоматически.",
        "en": "Enter version for the 'Checked on' field (e.g. <code>12.5.1</code>). Date will be filled automatically.",
    },
    "admin_unknown_setting": {
        "ru": "Неизвестная настройка",
        "en": "Unknown setting",
    },
    "admin_bad_id": {
        "ru": "Не удалось распознать ID",
        "en": "Could not parse ID",
    },
    "admin_channel_min_parts": {
        "ru": "Укажите минимум id и username",
        "en": "Provide at least id and username",
    },
    "admin_bad_channel_id": {
        "ru": "Некорректный id канала",
        "en": "Invalid channel id",
    },
    "admin_channel_updated": {
        "ru": "Канал обновлён",
        "en": "Channel updated",
    },
    "admin_search_results_title": {
        "ru": "Найденные плагины:",
        "en": "Found plugins:",
    },
    "admin_search_nothing_found": {
        "ru": "Ничего не найдено",
        "en": "Nothing found",
    },
    "admin_enter_valid_user_id": {
        "ru": "Введите корректный user_id",
        "en": "Enter a valid user_id",
    },
    "admin_link_failed": {
        "ru": "Не удалось привязать",
        "en": "Linking failed",
    },
    "admin_need_number": {
        "ru": "Введите число",
        "en": "Enter a number",
    },
    "admin_prompt_new_name": {
        "ru": "Введите новое название:",
        "en": "Enter new name:",
    },
    "admin_prompt_author": {
        "ru": "Введите автора:",
        "en": "Enter author:",
    },
    "admin_prompt_version": {
        "ru": "Введите версию:",
        "en": "Enter version:",
    },
    "admin_prompt_icons_count": {
        "ru": "Введите количество иконок:",
        "en": "Enter icons count:",
    },
    "admin_prompt_min_version": {
        "ru": "Введите минимальную версию:",
        "en": "Enter min version:",
    },
    "admin_prompt_checked_on": {
        "ru": "Введите версию проверки и дату, например: <code>12.4.1 (27.01.26)</code>",
        "en": "Enter checked version and date, e.g.: <code>12.4.1 (27.01.26)</code>",
    },
    "admin_prompt_value": {
        "ru": "Введите значение:",
        "en": "Enter value:",
    },
    "admin_prompt_has_settings": {
        "ru": "Есть настройки? (да/нет)",
        "en": "Has settings? (yes/no)",
    },
    "admin_choose_language": {
        "ru": "Выберите язык:",
        "en": "Choose language:",
    },
    "admin_choose_category": {
        "ru": "Выберите категорию:",
        "en": "Choose category:",
    },
    "admin_send_plugin_file": {
        "ru": "Отправьте файл плагина (.plugin):",
        "en": "Send plugin file (.plugin):",
    },
    "admin_send_plugin_file_short": {
        "ru": "Отправьте файл .plugin",
        "en": "Send .plugin file",
    },
    "admin_banned_empty": {
        "ru": "Нет заблокированных",
        "en": "No banned users",
    },
    "admin_queue_title_icons": {"ru": "Иконки", "en": "Icons"},
    "admin_queue_title_updates": {"ru": "Обновления", "en": "Updates"},
    "admin_queue_title_new": {"ru": "Новые заявки", "en": "New requests"},
    "admin_queue_title_plugins": {"ru": "Плагины", "en": "Plugins"},
    "admin_queue_title_all": {"ru": "Заявки", "en": "Requests"},
    "admin_page": {"ru": "Стр. {current}/{total}", "en": "Page {current}/{total}"},
    "admin_label_users": {"ru": "<b>Пользователи:</b> {total}", "en": "<b>Users:</b> {total}"},
    "admin_label_not_set": {"ru": "Не указано", "en": "Not set"},
    "admin_yes": {"ru": "Да", "en": "Yes"},
    "admin_no": {"ru": "Нет", "en": "No"},
    "admin_submit_publish": {"ru": "Опубликовать", "en": "Publish"},
    "admin_submit_update": {"ru": "Обновить", "en": "Update"},
    "admin_submit_delete": {"ru": "Удалить", "en": "Delete"},
    "admin_delete_confirm": {
        "ru": "Удалить плагин?",
        "en": "Delete plugin?",
    },
    "admin_delete_progress": {
        "ru": "Удаление...",
        "en": "Deleting...",
    },
    "admin_deleted_success": {
        "ru": "Удалено",
        "en": "Deleted",
    },
    "admin_delete_failed": {
        "ru": "Не удалось удалить",
        "en": "Failed to delete",
    },
    "admin_channel_message_not_found": {
        "ru": "Сообщение канала не найдено",
        "en": "Channel message not found",
    },
    "admin_userbot_unavailable": {
        "ru": "Userbot недоступен",
        "en": "Userbot unavailable",
    },
    "admin_btn_post": {"ru": "Посты", "en": "Posts"},
    "admin_post_prompt": {
        "ru": "Введите текст для поста:",
        "en": "Enter post text:",
    },
    "admin_post_confirm": {
        "ru": "Отправить пост?",
        "en": "Send post?",
    },
    "admin_post_no_text": {
        "ru": "Нет текста",
        "en": "No text",
    },
    "admin_post_sent": {
        "ru": "Опубликовано!\n\n{link}",
        "en": "Published!\n\n{link}",
    },
    "admin_updated_block_title": {
        "ru": "<b>Обновленные плагины:</b>",
        "en": "<b>Updated plugins:</b>",
    },
    "admin_broadcast_done": {
        "ru": "Рассылка завершена. Отправлено: {sent}, ошибок: {failed}",
        "en": "Broadcast finished. Sent: {sent}, failed: {failed}",
    },
    "admin_user_banned": {
        "ru": "Пользователь <code>{user_id}</code> заблокирован",
        "en": "User <code>{user_id}</code> banned",
    },
    "user_banned_by_admin": {
        "ru": "<b>Вы заблокированы</b>",
        "en": "<b>You are banned</b>",
    },
    "admin_settings_title": {
        "ru": "<b>Настройки</b>",
        "en": "<b>Settings</b>",
    },
    "admin_enter_user_id": {"ru": "Введите user_id пользователя:", "en": "Enter user ID:"},
    "admin_enter_reject_reason": {
        "ru": "Введите причину:",
        "en": "Enter reason:",
    },
    "admin_queue_empty": {
        "ru": "Пусто",
        "en": "Empty",
    },
    "admin_request_comment": {
        "ru": "<b>Комментарий:</b>\n<blockquote>{comment}</blockquote>",
        "en": "<b>Comment:</b>\n<blockquote>{comment}</blockquote>",
    },
    "admin_request_delete": {
        "ru": "<b>Удаление</b>\n\n<b>ID:</b> <code>{id}</code>\n<b>Плагин:</b> {name}\n<b>Slug:</b> <code>{slug}</code>\n\n<b>От:</b> {user}",
        "en": "<b>Delete</b>\n\n<b>ID:</b> <code>{id}</code>\n<b>Plugin:</b> {name}\n<b>Slug:</b> <code>{slug}</code>\n\n<b>From:</b> {user}",
    },
    "admin_request_icon": {
        "ru": "<b>Новый пак иконок</b>\n\n<b>ID:</b> <code>{id}</code>\n<b>Название:</b> {name}\n<b>Автор:</b> {author}\n<b>Версия:</b> {version}\n<b>Иконок:</b> {count}\n\n<b>От:</b> {user}",
        "en": "<b>New icon pack</b>\n\n<b>ID:</b> <code>{id}</code>\n<b>Name:</b> {name}\n<b>Author:</b> {author}\n<b>Version:</b> {version}\n<b>Icons:</b> {count}\n\n<b>From:</b> {user}",
    },
    "admin_request_plugin": {
        "ru": "<b>Новый плагин</b>\n\n<b>ID:</b> <code>{id}</code>\n\n{draft}\n\n<b>От:</b> {user}",
        "en": "<b>New plugin</b>\n\n<b>ID:</b> <code>{id}</code>\n\n{draft}\n\n<b>From:</b> {user}",
    },
    "admin_request_update": {
        "ru": "<b>Обновление</b>\n\n<b>ID:</b> <code>{id}</code>\n<b>Плагин:</b> {name}\n<b>Версия:</b> {old_version} → {version}\n<b>Мин. версия:</b> {min_version}\n\n<b>Изменения:</b>\n<blockquote expandable>{changelog}</blockquote>\n\n<b>От:</b> {user}",
        "en": "<b>Update</b>\n\n<b>ID:</b> <code>{id}</code>\n<b>Plugin:</b> {name}\n<b>Version:</b> {old_version} → {version}\n<b>Min version:</b> {min_version}\n\n<b>Changes:</b>\n<blockquote expandable>{changelog}</blockquote>\n\n<b>From:</b> {user}",
    },
    "admin_select_plugin": {"ru": "Выберите плагин:", "en": "Select plugin:"},
    "admin_title": {
        "ru": "<b>Админ-панель</b>",
        "en": "<b>Admin Panel</b>",
    },
    "admin_user_unbanned": {
        "ru": "Пользователь разблокирован",
        "en": "User unbanned",
    },

    "ask_admin_comment": {
        "ru": "Добавьте комментарий для администратора (необязательно).\n\nМожно пропустить.",
        "en": "Add a comment for the admin (optional).\n\nYou can skip.",
    },

    "btn_add": {
        "ru": "Добавить",
        "en": "Add",
    },
    "btn_all_plugins": {"ru": "Все плагины", "en": "All plugins"},
    "btn_back": {"ru": "Назад", "en": "Back"},
    "btn_forward": {"ru": "Вперёд", "en": "Forward"},
    "btn_cancel": {"ru": "Отмена", "en": "Cancel"},
    "btn_catalog": {"ru": "Каталог", "en": "Catalog"},
    "btn_confirm": {"ru": "Подтвердить", "en": "Confirm"},
    "btn_delete": {"ru": "Удалить", "en": "Delete"},
    "btn_icon_pack": {"ru": "Пак иконок", "en": "Icon pack"},
    "btn_icons": {"ru": "Иконки", "en": "Icons"},
    "btn_idea": {"ru": "Предложить идею", "en": "Suggest an idea"},
    "btn_joinly": {"ru": "Joinly", "en": "Joinly"},
    "btn_more": {"ru": "Ещё...", "en": "More..."},
    "btn_my_packs": {"ru": "Мои паки", "en": "My packs"},
    "btn_my_plugins": {"ru": "Мои плагины", "en": "My plugins"},
    "btn_new_plugin": {"ru": "Новый плагин", "en": "New plugin"},
    "btn_open": {"ru": "Открыть", "en": "Open"},
    "btn_profile": {"ru": "Профиль", "en": "Profile"},
    "btn_publish": {"ru": "Опубликовать", "en": "Publish"},
    "btn_publish_now": {"ru": "Опубликовать сейчас", "en": "Publish now"},
    "btn_publish_not_before": {"ru": "Не раньше...", "en": "Not before..."},
    "btn_vote_yes": {"ru": "За", "en": "Yes"},
    "btn_vote_no": {"ru": "Отказано", "en": "No"},
    "btn_retry": {"ru": "Ещё раз", "en": "Try again"},
    "btn_search": {"ru": "Поиск", "en": "Search"},
    "btn_send": {"ru": "Отправить", "en": "Send"},
    "btn_send_to_admin": {
        "ru": "Отправить админу",
        "en": "Send to admin",
    },
    "btn_skip": {"ru": "Пропустить", "en": "Skip"},
    "btn_submit": {"ru": "Предложить", "en": "Submit"},
    "btn_subscribe": {"ru": "Уведомлять", "en": "Notify"},
    "btn_subscriptions": {"ru": "Уведомления", "en": "Notifications"},
    "btn_support": {"ru": "Техподдержка", "en": "Support"},
    "btn_unsubscribe": {"ru": "Не уведомлять", "en": "Mute"},
    "btn_update": {"ru": "Обновить", "en": "Update"},

    "btn_notify_all_on": {"ru": "Все плагины: включено", "en": "All plugins: on"},
    "btn_notify_all_off": {"ru": "Все плагины: выключено", "en": "All plugins: off"},

    "rules_before_submit": {
        "ru": "Обязательно прочитайте правила распространения плагинов перед отправкой заявки администратору: https://teletype.in/@exterasquad/forum-rules-ru#veFl",
        "en": "Please read the plugin distribution rules before sending your request to the admins: https://teletype.in/@exterasquad/forum-rules-ru#veFl",
    },

    "kb_field_author": {"ru": "Автор", "en": "Author"},
    "kb_field_category": {"ru": "Категория", "en": "Category"},
    "kb_field_checked_on": {"ru": "Проверено", "en": "Checked on"},
    "kb_field_count": {"ru": "Кол-во", "en": "Count"},
    "kb_field_description": {"ru": "Описание", "en": "Description"},
    "kb_field_file": {"ru": "Файл", "en": "File"},
    "kb_field_min_version": {"ru": "Мин. версия", "en": "Min version"},
    "kb_field_name": {"ru": "Название", "en": "Name"},
    "kb_field_settings": {"ru": "Настройки", "en": "Settings"},
    "kb_field_usage": {"ru": "Использование", "en": "Usage"},
    "kb_field_version": {"ru": "Версия", "en": "Version"},

    "kb_admin_unban": {"ru": "Разбанить", "en": "Unban"},
    "kb_admin_reject": {"ru": "Отклонить", "en": "Reject"},
    "kb_admin_ban": {"ru": "Забанить", "en": "Ban"},
    "kb_admin_reject_with_reason": {"ru": "С причиной", "en": "With reason"},
    "kb_admin_reject_silent": {"ru": "Без уведомления", "en": "Silent"},
    "kb_admin_confirm_ban": {"ru": "Подтвердить бан", "en": "Confirm ban"},

    "catalog_empty": {"ru": "Пусто", "en": "Empty"},
    "catalog_page": {"ru": "Стр. {current}/{total}", "en": "Page {current}/{total}"},
    "catalog_source_title": {
        "ru": f'{emoji_html("tag", "🔖")} <b>Источники каталога</b>',
        "en": f'{emoji_html("tag", "🔖")} <b>Catalog Sources</b>',
    },
    "catalog_source_hint": {
        "ru": "Выбери, чьи плагины показывать в каталоге и обычном поиске.",
        "en": "Choose whose plugins are shown in the catalog and regular search.",
    },
    "catalog_source_all": {"ru": "Все источники", "en": "All sources"},
    "catalog_source_current": {"ru": "Источник: {source}", "en": "Source: {source}"},
    "catalog_source_count": {"ru": "{count} плагинов", "en": "{count} plugins"},
    "btn_catalog_source": {"ru": "Источник: {source}", "en": "Source: {source}"},
    "catalog_field_title": {"ru": "Название", "en": "Title"},
    "catalog_field_author": {"ru": "Автор", "en": "Author"},
    "catalog_field_author_channel": {"ru": "Канал автора", "en": "Author channel"},
    "catalog_field_icons": {"ru": "Иконок", "en": "Icons"},
    "catalog_field_link": {"ru": "Ссылка", "en": "Link"},
    "catalog_field_min_version": {"ru": "Минимальная версия", "en": "Min version"},
    "catalog_inline_header": {
        "ru": f'<a href="tg://emoji?id={ICONS["plugin"]}"></a> <b>{{name}}</b> by <code>{{author}}</code>',
        "en": f'<a href="tg://emoji?id={ICONS["plugin"]}"></a> <b>{{name}}</b> by <code>{{author}}</code>',
    },
    "catalog_inline_download": {"ru": "Скачать", "en": "Download"},
    "catalog_inline_open_in_bot": {"ru": "Открыть в боте", "en": "Open in bot"},
    "catalog_inline_no_description": {"ru": "—", "en": "—"},
    "catalog_inline_quick_donate": {
        "ru": '<a href="tg://emoji?id=5222374383019920631"></a> <b>Поддержать канал:</b> {url}',
        "en": '<a href="tg://emoji?id=5222374383019920631"></a> <b>Support the channel:</b> {url}',
    },
    "catalog_inline_quick_inform": {
        "ru": '<a href="tg://emoji?id=5222374383019920631"></a> <b>Прочитай этот пост:</b> {url}',
        "en": '<a href="tg://emoji?id=5222374383019920631"></a> <b>Read this post:</b> {url}',
    },

    "broadcast_title": {"ru": "<b>Рассылка</b>", "en": "<b>Broadcast</b>"},
    "btn_broadcast": {"ru": "Рассылка", "en": "Broadcast"},
    "btn_broadcast_on": {"ru": "Рассылка: включена", "en": "Broadcast: on"},
    "btn_broadcast_off": {"ru": "Рассылка: выключена", "en": "Broadcast: off"},
    "btn_broadcast_paid": {"ru": "Я заплатил за это.", "en": "I paid for this."},
    "btn_broadcast_paid_disable": {"ru": "Выключить за 50 Stars", "en": "Disable for 50 Stars"},
    "broadcast_paid_note": {"ru": "Платное выключение активно.", "en": "Paid disable is active."},
    "broadcast_invoice_title": {"ru": "Платное выключение рассылки", "en": "Paid broadcast disable"},
    "broadcast_invoice_description": {"ru": "Ты можешь выключить рассылку и беслпатно, это просто ПРИОРИТЕТНОЕ выключение.", "en": "You can disable broadcast for free, this is simply a PRIORITY disable."},
    "broadcast_payment_thanks": {"ru": "Готово. Теперь рассылка выключена.", "en": "Done. Broadcast is now disabled."},

    "admin_broadcast_paid_disable": {
        "ru": "Купили выключение рассылки\n\nПользователь: {name} ({user})\nСумма: {amount}",
        "en": "Paid broadcast disable purchased\n\nUser: {name} ({user})\nAmount: {amount}",
    },

    "join_settings_title": {"ru": "Настройки входа:", "en": "Join settings:"},
    "join_btn_welcome": {"ru": "Приветствие", "en": "Welcome"},
    "join_btn_enabled": {"ru": "Кик при заходе", "en": "Kick on join"},
    "join_btn_ban_on_join": {"ru": "Бан при заходе", "en": "Ban on join"},
    "join_btn_service_cleanup": {"ru": "Очищать сервисные", "en": "Clean service"},
    "join_btn_join_reaction": {"ru": "Реакция при входе", "en": "Join reaction"},
    "join_btn_post_guard": {"ru": "Посты канала", "en": "Channel posts"},
    "join_btn_post_guard_toggle": {"ru": "Закрывать чат", "en": "Lock chat"},
    "join_btn_post_rules_toggle": {"ru": "Правила к посту", "en": "Post rules"},
    "join_btn_post_lock_seconds": {"ru": "Время закрытия", "en": "Lock time"},
    "join_btn_post_permissions": {"ru": "Права закрытия", "en": "Lock permissions"},
    "join_btn_post_rules_edit": {"ru": "Текст правил", "en": "Rules text"},
    "join_btn_post_unlock_now": {"ru": "Открыть сейчас", "en": "Unlock now"},
    "join_btn_welcome_toggle": {"ru": "Приветствие", "en": "Welcome"},
    "join_btn_edit": {"ru": "Редактировать", "en": "Edit"},
    "join_prompt_welcome": {"ru": "Отправь новый текст приветствия.", "en": "Send the new welcome text."},
    "join_prompt_post_lock_seconds": {
        "ru": "Отправь время закрытия чата в секундах. Например: <code>30</code>, <code>120</code>. Чтобы выключить закрытие, отправь <code>0</code>.",
        "en": "Send chat lock duration in seconds. Example: <code>30</code>, <code>120</code>. Send <code>0</code> to disable locking.",
    },
    "join_prompt_post_rules": {
        "ru": "Отправь текст правил, который бот будет писать ответом на каждый новый пост канала.",
        "en": "Send the rules text the bot should reply with under each new channel post.",
    },
    "join_bad_seconds": {
        "ru": "Нужно число секунд от 0 до 86400.",
        "en": "Send a number of seconds from 0 to 86400.",
    },
    "join_prompt_reaction": {
        "ru": "Отправь один эмодзи для реакции (или напиши off чтобы выключить).",
        "en": "Send one emoji for reaction (or type off to disable).",
    },
    "join_saved": {"ru": "Сохранено.", "en": "Saved."},
    "join_chat_unlocked": {"ru": "Чат открыт.", "en": "Chat unlocked."},
    "join_chat_unlock_failed": {"ru": "Не удалось открыть чат. Проверь права бота.", "en": "Failed to unlock chat. Check bot permissions."},
    "join_post_guard_help": {
        "ru": "<b>Посты канала</b>\n\n"
        "Бот реагирует на автофорварды из привязанного канала в чате обсуждений.\n"
        "Можно временно закрывать чат после нового поста и отправлять правила ответом на пост.\n\n"
        "<b>Важно:</b> бот должен быть админом с правом менять разрешения чата.",
        "en": "<b>Channel posts</b>\n\n"
        "The bot reacts to automatic forwards from the linked channel in the discussion chat.\n"
        "It can temporarily lock the chat after a new post and reply with rules.\n\n"
        "<b>Important:</b> the bot must be an admin with permission to change chat permissions.",
    },
    "join_post_rules_default": {
        "ru": "<b>Правила обсуждения</b>\n\nПишите по теме поста, без спама, оскорблений и флуда.",
        "en": "<b>Discussion rules</b>\n\nStay on topic, avoid spam, insults, and flooding.",
    },
    "join_reaction_help": {
        "ru": "<b>Реакция при входе</b>\n\n"
        "Бот поставит реакцию на сервисное сообщение о входе пользователя.\n\n",
        "en": "<b>Join reaction</b>\n\n"
        "The bot will react to the service message about a user joining.\n\n"
    },
    "join_welcome_help": {
        "ru": "<b>Редактор приветствия</b>\n\n"
        "Поддерживается <b>MarkdownV2</b> и <b>кнопки</b>.\n\n"
        "<b>Важно про MarkdownV2</b>\n"
        "Если Telegram пишет <i>can't parse entities</i> — значит в тексте есть спецсимвол без экранирования.\n"
        "Например скобки нужно писать так: <pre><code>\\(  \\)</code></pre>"
        "<pre><code>_ * [ ] ( ) ~ ` > # + - = | { } . !</code></pre>\n"
        "<b>Плейсхолдеры</b>\n"
        "<pre><code>{first} {last} {fullname} {username}\n{mention} {id} {chatname}</code></pre>\n"
        "<b>Флаги</b>\n"
        "<pre><code>{preview} {nonotif} {protect}</code></pre>\n"
        "<b>Кнопки</b>\n"
        "<pre><code>[Текст](buttonurl://https://example.com)\n[A](buttonurl://https://a.com) [B](buttonurl://https://b.com:same)</code></pre>\n"
        "<b>Пример шаблона</b>\n"
        "<pre><code>*Дорогой {fullname} \\({username}\\)*, этот чат не для общения.\n"
        "Для общения есть @exteraForum\n\n"
        "[Перейти](buttonurl://https://t.me/exteraForum)</code></pre>",
        "en": "<b>Welcome editor</b>\n\n"
        "<b>MarkdownV2</b> and <b>buttons</b> are supported.\n\n"
        "<b>MarkdownV2 note</b>\n"
        "If Telegram says <i>can't parse entities</i>, you have a reserved character without escaping.\n"
        "Example: parentheses must be escaped like <pre><code>\\(  \\)</code></pre>"
        "<pre><code>_ * [ ] ( ) ~ ` > # + - = | { } . !</code></pre>\n"
        "<b>Placeholders</b>\n"
        "<pre><code>{first} {last} {fullname} {username}\n{mention} {id} {chatname}</code></pre>\n"
        "<b>Flags</b>\n"
        "<pre><code>{preview} {nonotif} {protect}</code></pre>\n"
        "<b>Buttons</b>\n"
        "<pre><code>[Text](buttonurl://https://example.com)\n[A](buttonurl://https://a.com) [B](buttonurl://https://b.com:same)</code></pre>\n"
        "<b>Template example</b>\n"
        "<pre><code>*Dear {fullname} \\({username}\\)*, this is not a chat for communication.\n"
        "For chatting use @exteraForum\n\n"
        "[Open](buttonurl://https://t.me/exteraForum)</code></pre>"
    },
    "join_welcome_default": {
        "ru": "Дорогой {fullname} \\({username}\\), это не чат для общения.\nДля общения есть @exteraForum",
        "en": "Dear {fullname} \\({username}\\), this is not a chat for communication.\nFor chatting use @exteraForum"
    },
    "catalog_title": {
        "ru": f'{emoji_html("catalog", "🧩")} <b>Каталог плагинов</b>',
        "en": f'{emoji_html("catalog", "🧩")} <b>Plugin Catalog</b>',
    },

    "stenka_title": {"ru": "Социальная стенка", "en": "Social wall"},
    "stenka_btn_leave_tag": {"ru": "оставить тег", "en": "leave tag"},
    "stenka_inline_description": {"ru": "Оставь тег на стенке", "en": "Leave a tag on the wall"},
    "stenka_alert_open_bot": {"ru": "Открой бота и отправь тег", "en": "Open the bot and send a tag"},
    "stenka_prompt_enter_tag": {"ru": "Отправь тег (до 15 символов).", "en": "Send a tag (up to 15 chars)."},
    "stenka_err_not_found": {"ru": "Стенка не найдена", "en": "Wall not found"},
    "stenka_err_token_invalid": {"ru": "Ссылка устарела или неверная", "en": "Link expired or invalid"},
    "stenka_err_tag_taken": {"ru": "Тег уже занят", "en": "Tag is already taken"},
    "stenka_err_tag_too_long": {"ru": "Максимум 15 символов", "en": "Max 15 characters"},
    "stenka_err_tag_format": {
        "ru": "Тег может содержать только буквы, цифры и _",
        "en": "Tag may contain only letters, digits and _",
    },
    "stenka_err_already_wrote": {
        "ru": "Ты уже оставил тег: {tag}",
        "en": "You already left a tag: {tag}",
    },
    "stenka_ok_saved": {"ru": "Готово", "en": "Done"},

    "btn_save_changes": {"ru": "Сохранить", "en": "Save"},
    "pending_saved": {"ru": "Сохранено.", "en": "Saved."},

    "pending_upload_plugin": {
        "ru": "Пришли новый файл <code>.plugin</code> для этой заявки.",
        "en": "Send a new <code>.plugin</code> file for this request.",
    },
    "pending_upload_update_plugin": {
        "ru": "Пришли новый файл <code>.plugin</code> для обновления.",
        "en": "Send a new <code>.plugin</code> file for the update.",
    },
    "pending_file_id_mismatch": {
        "ru": "ID плагина в файле не совпадает с заявкой.",
        "en": "Plugin ID in the file does not match the request.",
    },
    "pending_delete_confirm": {
        "ru": "Удалить заявку?",
        "en": "Delete this request?",
    },
    "pending_deleted": {
        "ru": "Заявка удалена.",
        "en": "Request deleted.",
    },

    "admin_request_updated": {
        "ru": "Обновили заявку\n\nID: <code>{id}</code>\nПлагин: <b>{name}</b>\nПользователь: {user}",
        "en": "Request updated\n\nID: <code>{id}</code>\nPlugin: <b>{name}</b>\nUser: {user}",
    },

    "choose_category": {
        "ru": "Выберите категорию:",
        "en": "Choose category:",
    },
    "choose_description_language": {
        "ru": "На каком языке описание?",
        "en": "Which language is the description in?",
    },
    "choose_plugin_to_update": {
        "ru": "Выберите плагин:",
        "en": "Choose plugin:",
    },
    "choose_type": {"ru": "Что хотите сделать?", "en": "What would you like to do?"},

    "confirm_submission": {
        "ru": f'{emoji_html("yes", "✅")} <b>Проверьте заявку</b>\n\n<b>Название:</b> {{name}}\n<b>Автор:</b> {{author}}\n<b>Описание:</b> {{description}}\n<b>Версия:</b> {{version}}\n<b>Мин. версия:</b> {{min_version}}\n<b>Настройки:</b> {{settings}}\n<b>Категория:</b> {{category}}\n\n🇷🇺 <b>Использование:</b>\n{{usage_ru}}\n\n🇺🇸 <b>Usage:</b>\n{{usage_en}}\n\nВсё верно?',
        "en": f'{emoji_html("yes", "✅")} <b>Review submission</b>\n\n<b>Name:</b> {{name}}\n<b>Author:</b> {{author}}\n<b>Description:</b> {{description}}\n<b>Version:</b> {{version}}\n<b>Min version:</b> {{min_version}}\n<b>Settings:</b> {{settings}}\n<b>Category:</b> {{category}}\n\n🇷🇺 <b>Использование:</b>\n{{usage_ru}}\n\n🇺🇸 <b>Usage:</b>\n{{usage_en}}\n\nIs everything correct?',
    },
    "confirm_update": {
        "ru": f'{emoji_html("yes", "✅")} <b>Проверьте обновление</b>\n\n<b>Плагин:</b> {{name}}\n<b>Версия:</b> {{old_version}} → {{version}}\n<b>Мин. версия:</b> {{min_version}}\n\n<b>Что нового:</b>\n{{changelog}}\n\nОтправить?',
        "en": f'{emoji_html("yes", "✅")} <b>Review update</b>\n\n<b>Plugin:</b> {{name}}\n<b>Version:</b> {{old_version}} → {{version}}\n<b>Min version:</b> {{min_version}}\n\n<b>What\'s new:</b>\n{{changelog}}\n\nSubmit?',
    },

    "delete_sent": {
        "ru": "<b>Запрос на удаление отправлен</b>\n\nМодератор рассмотрит его в ближайшее время.",
        "en": "<b>Delete request sent</b>\n\nA moderator will review it soon.",
    },
    "download_error": {
        "ru": "Ошибка загрузки",
        "en": "Download failed",
    },
    "draft_expiring": {
        "ru": "Ваш черновик будет удалён через 10 минут без активности.",
        "en": "Your draft will be deleted in 10 minutes if there is no activity.",
    },

    "enter_changelog": {
        "ru": "<b>Что нового?</b>\n\nОпишите изменения:",
        "en": "<b>What's new?</b>\n\nDescribe the changes:",
    },
    "enter_description_en": {
        "ru": "Введите описание на английском:",
        "en": "Enter the description in English:",
    },
    "enter_description_ru": {
        "ru": "Введите описание на русском:",
        "en": "Enter the description in Russian:",
    },
    "enter_usage_en": {
        "ru": "Отлично\n\nТеперь введите <b>использование на английском</b>.\nПример: <code>Open a chat and type /calc 2+2</code>\nЕсли использование автоматическое — пишите пассивно (e.g. <code>Automatically shows weather when a chat opens</code>).",
        "en": "Great\n\nNow enter <b>usage in English</b>.\nExample: <code>Open a chat and type /calc 2+2</code>\nIf usage is automatic, write in passive voice (e.g. <code>Automatically shows weather when a chat opens</code>).",
    },
    "enter_usage_ru": {
        "ru": "Введите <b>использование на русском</b>.\nПример: <code>Откройте чат и напишите /calc 2+2</code>\nЕсли использование автоматическое — напишите пассивно (напр. <code>Автоматически показывает погоду при открытии чата</code>).",
        "en": "Enter <b>usage in Russian</b>.\nExample (in Russian): <code>Откройте чат и напишите /calc 2+2</code>\nIf usage is automatic, write in passive voice (e.g. <code>Автоматически показывает погоду при открытии чата</code>).",
    },

    "file_too_large": {
        "ru": "Файл больше 8 МБ",
        "en": "File is larger than 8 MB",
    },

    "icon_already_exists": {
        "ru": "Такой пак уже есть в каталоге",
        "en": "This icon pack already exists",
    },
    "icon_meta_invalid": {
        "ru": "Неверный формат metadata.json",
        "en": "Invalid metadata.json format",
    },
    "icon_meta_missing": {
        "ru": "В архиве нет metadata.json",
        "en": "metadata.json is missing in the archive",
    },
    "icon_parsed": {
        "ru": "<b>Пак распознан</b>\n\n<b>Название:</b> {name}\n<b>Автор:</b> {author}\n<b>Версия:</b> {version}\n<b>Иконок:</b> {count}",
        "en": "<b>Icon pack recognized</b>\n\n<b>Name:</b> {name}\n<b>Author:</b> {author}\n<b>Version:</b> {version}\n<b>Icons:</b> {count}",
    },
    "icon_pending": {
        "ru": "Заявка на этот пак уже на рассмотрении",
        "en": "A submission for this icon pack is already pending",
    },

    "icons_soon": {"ru": "Скоро", "en": "Coming soon"},
    "icons_title": {
        "ru": f'{emoji_html("art", "🎨")} <b>Паки иконок</b>',
        "en": f'{emoji_html("art", "🎨")} <b>Icon Packs</b>',
    },

    "invalid_file": {
        "ru": "Отправьте файл <code>.plugin</code>",
        "en": "Please send a <code>.plugin</code> file",
    },
    "invalid_icon_file": {
        "ru": "Отправьте файл <code>.icons</code>",
        "en": "Please send a <code>.icons</code> file",
    },

    "language_prompt": {
        "ru": "Выберите язык",
        "en": "Choose language",
    },
    "language_saved": {
        "ru": "Русский язык установлен",
        "en": "English language set",
    },

    "missing_icon_info": {
        "ru": "Не удалось найти данные пака",
        "en": "Icon pack details not found",
    },
    "missing_plugin_info": {
        "ru": "Не удалось распознать данные плагина",
        "en": "Plugin details not found",
    },
    "missing_version": {
        "ru": "Не удалось распознать версию",
        "en": "Version is missing",
    },
    "require_min_version": {
        "ru": "В файле плагина не указана <b>минимальная версия</b> (<code>__min_version__</code>).\n\nВведите её вручную (например, <code>11.12.0</code>):",
        "en": "The plugin file has no <b>minimum version</b> (<code>__min_version__</code>).\n\nPlease enter it manually (e.g. <code>11.12.0</code>):",
    },
    "invalid_min_version": {
        "ru": "Это не похоже на версию. Введите номер версии, например <code>11.12.0</code>.",
        "en": "That doesn't look like a version. Enter a version number, e.g. <code>11.12.0</code>.",
    },
    "need_text": {
        "ru": "Введите текст",
        "en": "Enter text",
    },
    "no_plugins_to_update": {
        "ru": "У вас нет плагинов",
        "en": "You don't have any plugins",
    },
    "not_found": {
        "ru": "Не найдено",
        "en": "Not found",
    },

    "notify_deleted": {
        "ru": "Плагин <b>{name}</b> удалён",
        "en": "Plugin <b>{name}</b> was deleted",
    },
    "notify_plugin_removed_author": {
        "ru": f'{emoji_html("delete", "🗑")} <b>Ваш плагин удалён</b>\n\n<b>Плагин:</b> {{name}}\n\nПлагин был удалён из каталога администратором.',
        "en": f'{emoji_html("delete", "🗑")} <b>Your plugin was removed</b>\n\n<b>Plugin:</b> {{name}}\n\nThe plugin has been removed from the catalog by an administrator.',
    },
    "notify_icon_published": {
        "ru": "Пак иконок <b>{name}</b> опубликован",
        "en": "Icon pack <b>{name}</b> published",
    },
    "notify_published": {
        "ru": "Плагин <b>{name}</b> опубликован",
        "en": "Plugin <b>{name}</b> published",
    },
    "notify_rejected": {
        "ru": f'{emoji_html("no", "❌")} <b>Заявка отклонена</b>\n\n<b>Плагин:</b> {{name}}\n\n<b>Причина отказа:</b>\n<blockquote expandable>{{comment}}</blockquote>',
        "en": f'{emoji_html("no", "❌")} <b>Submission rejected</b>\n\n<b>Plugin:</b> {{name}}\n\n<b>Reason:</b>\n<blockquote expandable>{{comment}}</blockquote>',
    },
    "notify_subscription_update": {
        "ru": "Плагин {name} обновился до версии <b>{version}</b>\n\n<b>Что нового:</b>\n<blockquote expandable>{changelog}</blockquote>",
        "en": "Plugin {name} updated to <b>{version}</b>\n\n<b>What's new:</b>\n<blockquote expandable>{changelog}</blockquote>",
    },
    "notify_update_published": {
        "ru": "Обновление <b>{name}</b> опубликовано (v<b>{version}</b>)",
        "en": "Update <b>{name}</b> published (v<b>{version}</b>)",
    },

    "parse_error": {
        "ru": "Ошибка: {error}",
        "en": "Error: {error}",
    },
    "plugin_already_exists": {
        "ru": "Плагин с таким названием уже существует в каталоге",
        "en": "A plugin with this name already exists",
    },
    "plugin_parsed": {
        "ru": "<b>Плагин распознан</b>\n\n<b>Название:</b> {name}\n<b>Автор:</b> {author}\n<b>Описание:</b> {description}\n<b>Версия:</b> {version}\n<b>Мин. версия:</b> {min_version}\n<b>Настройки:</b> {settings}\n\nВведите <b>инструкцию по использованию</b> на русском:",
        "en": "<b>Plugin recognized</b>\n\n<b>Name:</b> {name}\n<b>Author:</b> {author}\n<b>Description:</b> {description}\n<b>Version:</b> {version}\n<b>Min version:</b> {min_version}\n<b>Settings:</b> {settings}\n\nEnter <b>usage instructions</b> in Russian:",
    },
    "plugin_pending": {
        "ru": "Заявка на этот плагин уже на рассмотрении",
        "en": "A submission for this plugin is already pending",
    },
    "plugin_id_exists": {
        "ru": "Плагин с таким ID уже существует в каталоге",
        "en": "A plugin with this ID already exists in the catalog",
    },

    "profile_empty": {"ru": "Нет работ в каталоге", "en": "No works in catalog"},
    "profile_stats": {"ru": "Плагинов: <b>{plugins}</b> · Паков: <b>{icons}</b>", "en": "Plugins: <b>{plugins}</b> · Packs: <b>{icons}</b>"},
    "profile_title": {
        "ru": f'{emoji_html("profile", "👤")} <b>Профиль</b>',
        "en": f'{emoji_html("profile", "👤")} <b>Profile</b>',
    },

    "search_empty": {
        "ru": "Ничего не найдено",
        "en": "Nothing found",
    },
    "search_prompt": {
        "ru": "Введите запрос:",
        "en": "Enter query:",
    },
    "search_results": {
        "ru": "Найдено <b>{count}</b>",
        "en": "Found <b>{count}</b>",
    },
    "catalog_field_source": {
        "ru": "Источник",
        "en": "Source",
    },

    "submission_cancelled": {
        "ru": "Заявка отменена",
        "en": "Submission cancelled",
    },
    "submission_sent": {
        "ru": "<b>Заявка отправлена</b>\n\nМодератор рассмотрит её в ближайшее время.",
        "en": "<b>Submission sent</b>\n\nA moderator will review it soon.",
    },

    "subscribed": {
        "ru": "Уведомления включены",
        "en": "Notifications enabled",
    },
    "version_same": {
        "ru": "Версия не изменилась",
        "en": "Version is unchanged",
    },
    "version_lower": {
        "ru": "Указанная версия ниже текущей ({current}). Возможно, вы имели в виду <b>{suggested}</b>",
        "en": "Provided version is lower than current ({current}). Maybe you meant <b>{suggested}</b>",
    },
    "subscriptions_empty": {
        "ru": "Нет уведомлений",
        "en": "No notifications",
    },
    "subscriptions_title": {
        "ru": f'{emoji_html("bell", "🔔")} Мои уведомления',
        "en": f'{emoji_html("bell", "🔔")} My notifications',
    },
    "subscriptions_hint": {
        "ru": "Вы можете включить уведомления на конкретные плагины, выбрав их в каталоге, или включить уведомления на все плагины.",
        "en": "You can enable notifications for specific plugins by selecting them in the catalog, or enable notifications for all plugins.",
    },

    "joinly_profile_title": {
        "ru": "<b>Joinly</b> — настройки входов в чат",
        "en": "<b>Joinly</b> — chat join settings",
    },
    "joinly_profile_no_chats": {
        "ru": "Пока не найдено ни одного чата, где вы настраивали Joinly. Добавьте бота в чат и выполните команду /settings.",
        "en": "No chats found where you configured Joinly yet. Add the bot to a chat and run /settings.",
    },
    "joinly_profile_chat": {
        "ru": "Чат: <code>{chat_id}</code>",
        "en": "Chat: <code>{chat_id}</code>",
    },
    "joinly_profile_settings": {
        "ru": "Настройки: приветствие {welcome} · очистка сервисных {cleanup} · кик при заходе {enabled} · бан {ban}",
        "en": "Settings: welcome {welcome} · cleanup {cleanup} · kick on join {enabled} · ban {ban}",
    },
    "joinly_deeplink_intro": {
        "ru": "Чтобы настроить Joinly, добавьте бота в чат и выполните команду <code>/settings</code> (нужны права администратора).",
        "en": "To configure Joinly, add the bot to your chat and run <code>/settings</code> (admin rights required).",
    },
    "joinly_bot_added": {
        "ru": "Бот добавлен в чат. Для настройки Joinly выполните команду <code>/settings</code> (нужны права администратора).",
        "en": "The bot was added to the chat. To configure Joinly, run <code>/settings</code> (admin rights required).",
    },

    "notify_all_title": {
        "ru": f'{emoji_html("bell", "🔔")} Уведомления на все плагины',
        "en": f'{emoji_html("bell", "🔔")} All plugins notifications',
    },
    "notify_all_item": {
        "ru": "Уведомления на все плагины",
        "en": "All plugins notifications",
    },

    "admin_rejected_done": {
        "ru": f'{emoji_html("no", "❌")} Отклонено',
        "en": f'{emoji_html("no", "❌")} Rejected',
    },

    "admin_publish_done": {
        "ru": "Опубликовано!\n\n{link}",
        "en": "Published!\n\n{link}",
    },
    "moderation_vote_reason_prompt": {
        "ru": "Голос учтён. Чтобы добавить причину, ответьте на сообщение с плагином.",
        "en": "Vote saved. To add a reason, reply to the plugin message.",
    },
    "moderation_vote_reason_dm_prompt": {
        "ru": "Голос учтён. Напишите причину следующим сообщением.",
        "en": "Vote saved. Send the reason as your next message.",
    },
    "moderation_vote_reason_saved": {
        "ru": "Причина сохранена",
        "en": "Reason saved",
    },
    "moderation_threshold_title": {
        "ru": "<b>Заявка набрала минимум голосов</b>",
        "en": "<b>Request reached the voting threshold</b>",
    },
    "publish_not_before_prompt": {
        "ru": "Введите время, раньше которого публикация не должна выйти.\nФормат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code> (UTC+5).",
        "en": "Enter the earliest publication time.\nFormat: <code>DD.MM.YYYY HH:MM</code> (UTC+5).",
    },
    "publish_not_before_saved": {
        "ru": "Время сохранено",
        "en": "Earliest publication time saved",
    },
    "admin_request_scheduled_by_limit": {
        "ru": "Заявка принята и запланирована на {datetime} UTC+5",
        "en": "Request approved and scheduled for {datetime} UTC+5",
    },
    "admin_post_schedule_prompt": {
        "ru": "⏰ Введите дату и время публикации в формате <code>ДД.ММ.ГГГГ ЧЧ:ММ</code> (UTC+5):",
        "en": "⏰ Enter the scheduled date and time in format <code>DD.MM.YYYY HH:MM</code> (UTC+5):",
    },
    "admin_schedule_presets_title": {
        "ru": "Выберите пресет времени или введите вручную:",
        "en": "Choose a time preset or enter manually:",
    },
    "admin_scheduled_title": {
        "ru": "<b>Отложенные</b>",
        "en": "<b>Scheduled</b>",
    },
    "admin_scheduled_empty": {
        "ru": "Нет отложенных публикаций",
        "en": "No scheduled publications",
    },
    "admin_scheduled_posts_title": {
        "ru": "<b>Отложенные посты</b>",
        "en": "<b>Scheduled posts</b>",
    },
    "admin_scheduled_posts_empty": {
        "ru": "Нет отложенных постов",
        "en": "No scheduled posts",
    },
    "btn_edit_text": {"ru": "Изменить текст", "en": "Edit text"},
    "btn_delete_post": {"ru": "Удалить пост", "en": "Delete post"},
    "admin_post_edit_prompt": {
        "ru": "Введите новый текст поста:",
        "en": "Enter new post text:",
    },
    "btn_unschedule": {"ru": "Убрать отложку", "en": "Unschedule"},
    "btn_change_time": {"ru": "Изменить время", "en": "Change time"},
    "btn_move_up": {"ru": "Вверх", "en": "Up"},
    "btn_move_down": {"ru": "Вниз", "en": "Down"},
    "admin_schedule_preset_add_prompt": {
        "ru": "Введите дату и время для нового пресета в формате <code>ДД.ММ.ГГГГ ЧЧ:ММ</code> (UTC+5):",
        "en": "Enter date/time for a new preset in format <code>DD.MM.YYYY HH:MM</code> (UTC+5):",
    },
    "btn_add_preset": {"ru": "+", "en": "+"},
    "admin_post_scheduled": {
        "ru": "Пост запланирован на {datetime} UTC+5\n\n{link}",
        "en": "Post scheduled for {datetime} UTC+5\n\n{link}",
    },
    "admin_plugin_scheduled": {
        "ru": "Публикация запланирована на {datetime} UTC+5",
        "en": "Publication scheduled for {datetime} UTC+5",
    },
    "admin_post_schedule_bad_format": {
        "ru": "Неверный формат. Используйте <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>",
        "en": "Invalid format. Use <code>DD.MM.YYYY HH:MM</code>",
    },
    "admin_post_schedule_past": {
        "ru": "Дата должна быть в будущем",
        "en": "Date must be in the future",
    },
    "btn_schedule": {
        "ru": "Запланировать",
        "en": "Schedule",
    },
    "unsubscribed": {
        "ru": "Уведомления отключены",
        "en": "Notifications disabled",
    },

    "update_sent": {
        "ru": "<b>Обновление отправлено</b>",
        "en": "<b>Update submitted</b>",
    },
    "upload_icon": {
        "ru": "<b>Отправьте файл пака иконок</b>\n\nФайл должен иметь расширение <code>.icons</code>",
        "en": "<b>Send your icon pack file</b>\n\nFile must have <code>.icons</code> extension",
    },
    "upload_plugin": {
        "ru": "<b>Отправьте файл плагина</b>\n\nФайл должен иметь расширение <code>.plugin</code>\nМетаданные будут извлечены автоматически",
        "en": "<b>Send your plugin file</b>\n\nFile must have <code>.plugin</code> extension\nMetadata will be extracted automatically",
    },
    "upload_update_file": {
        "ru": "<b>Отправьте обновлённый файл</b>\n\nТекущая версия: <b>{version}</b>",
        "en": "<b>Send updated file</b>\n\nCurrent version: <b>{version}</b>",
    },

    "user_banned": {
        "ru": "Вы заблокированы",
        "en": "You are banned",
    },
    "user_banned_short": {
        "ru": "Заблокированы",
        "en": "Banned",
    },

    "version_not_higher": {
        "ru": "Новая версия должна быть выше текущей ({current})",
        "en": "New version must be higher than current ({current})",
    },

    "welcome": {
        "ru": f'{emoji_html("bot", "🤖")} <b>Добро пожаловать в {{bot_name}}, {{user_name}}!</b>\n\n{{bot_name}} — это твой помощник для:\n• <a href="https://t.me/{{bot}}?start=catalog">Поиска и просмотра</a> плагинов (есть и inline поиск), а также <a href="https://t.me/{{bot}}?start=submit">предложить</a> свой плагин\n• <a href="https://t.me/{{bot}}?start=notifications">Уведомлений</a>, когда плагин был обновлён\n• <a href="https://t.me/{{bot}}?start=joinly">Управление</a> входами в вашем чате с помощью Joinly\n• <a href="https://t.me/{{bot}}?start=poster">Отложенного постинга</a> в ваши каналы (Poster) — в разделе «Профиль»\n\nВыбери действие:',
        "en": f'{emoji_html("bot", "🤖")} <b>Welcome to {{bot_name}}, {{user_name}}!</b>\n\n{{bot_name}} is your assistant for:\n• <a href="https://t.me/{{bot}}?start=catalog">Browsing</a> plugins (inline search included) and <a href="https://t.me/{{bot}}?start=submit">submitting</a> your own\n• <a href="https://t.me/{{bot}}?start=notifications">Notifications</a> when a plugin is updated\n• <a href="https://t.me/{{bot}}?start=joinly">Managing</a> chat joins with Joinly\n• <a href="https://t.me/{{bot}}?start=poster">Scheduled posting</a> to your channels (Poster) — in the “Profile” tab\n\nChoose an action:',
    },
}


def t(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    texts = TEXTS.get(key, {})
    text = texts.get(lang) or texts.get(DEFAULT_LANGUAGE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except KeyError:
            return text
    return text
