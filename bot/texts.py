from typing import Any, Dict

DEFAULT_LANGUAGE = "ru"

TEXTS: Dict[str, Dict[str, str]] = {
    "language_prompt": {"ru": "🌐 Выберите язык", "en": "🌐 Choose language"},
    "language_saved": {"ru": "✅ Русский язык установлен", "en": "✅ English language set"},
    "welcome": {
        "ru": "<tg-emoji emoji-id=\"5319016550248751722\">👋</tg-emoji> <b>Добро пожаловать!</b>\n\nЗдесь вы можете:\n• Найти плагины в каталоге\n• Предложить свой плагин\n• Управлять своими работами",
        "en": "<tg-emoji emoji-id=\"5319016550248751722\">👋</tg-emoji> <b>Welcome!</b>\n\nHere you can:\n• Browse the plugin catalog\n• Submit your plugin\n• Manage your submissions",
    },
    "choose_type": {"ru": "Что хотите сделать?", "en": "What would you like to do?"},
    "upload_plugin": {
        "ru": "📎 <b>Отправьте файл плагина</b>\n\nФайл должен иметь расширение <code>.plugin</code>\nМетаданные будут извлечены автоматически",
        "en": "📎 <b>Send your plugin file</b>\n\nFile must have <code>.plugin</code> extension\nMetadata will be extracted automatically",
    },
    "plugin_parsed": {
        "ru": "✅ <b>Плагин распознан</b>\n\n📦 <b>Название:</b> {name}\n👤 <b>Автор:</b> {author}\n📝 <b>Описание:</b> {description}\n🔢 <b>Версия:</b> {version}\n📱 <b>Мин. версия:</b> {min_version}\n⚙️ <b>Настройки:</b> {settings}\n\nВведите <b>инструкцию по использованию</b> на русском:",
        "en": "✅ <b>Plugin recognized</b>\n\n📦 <b>Name:</b> {name}\n👤 <b>Author:</b> {author}\n📝 <b>Description:</b> {description}\n🔢 <b>Version:</b> {version}\n📱 <b>Min version:</b> {min_version}\n⚙️ <b>Settings:</b> {settings}\n\nEnter <b>usage instructions</b> in Russian:",
    },
    "choose_description_language": {
        "ru": "На каком языке описание?",
        "en": "Which language is the description in?",
    },
    "enter_description_ru": {
        "ru": "Введите описание на русском:",
        "en": "Enter the description in Russian:",
    },
    "enter_description_en": {
        "ru": "Введите описание на английском:",
        "en": "Enter the description in English:",
    },
    "enter_usage_ru": {
        "ru": "✍️ Введите <b>использование на русском</b>.\nПример: <code>Откройте чат и напишите /calc 2+2</code>\nЕсли использование автоматическое — напишите пассивно (напр. <code>Автоматически показывает погоду при открытии чата</code>).",
        "en": "✍️ Enter <b>usage in Russian</b>.\nExample (in Russian): <code>Откройте чат и напишите /calc 2+2</code>\nIf usage is automatic, write in passive voice (e.g. <code>Автоматически показывает погоду при открытии чата</code>).",
    },
    "enter_usage_en": {
        "ru": "👍 Отлично!\n\nТеперь введите <b>использование на английском</b>.\nПример: <code>Open a chat and type /calc 2+2</code>\nЕсли использование автоматическое — пишите пассивно (e.g. <code>Automatically shows weather when a chat opens</code>).",
        "en": "👍 Great!\n\nNow enter <b>usage in English</b>.\nExample: <code>Open a chat and type /calc 2+2</code>\nIf usage is automatic, write in passive voice (e.g. <code>Automatically shows weather when a chat opens</code>).",
    },
    "choose_category": {"ru": "🏷 Выберите категорию:", "en": "🏷 Choose category:"},
    "confirm_submission": {
        "ru": "📋 <b>Проверьте заявку</b>\n\n📦 <b>Название:</b> {name}\n👤 <b>Автор:</b> {author}\n📝 <b>Описание:</b> {description}\n🔢 <b>Версия:</b> {version}\n📱 <b>Мин. версия:</b> {min_version}\n⚙️ <b>Настройки:</b> {settings}\n🏷 <b>Категория:</b> {category}\n\n🇷🇺 <b>Использование:</b>\n{usage_ru}\n\n🇺🇸 <b>Usage:</b>\n{usage_en}\n\nВсё верно?",
        "en": "📋 <b>Review submission</b>\n\n📦 <b>Name:</b> {name}\n👤 <b>Author:</b> {author}\n📝 <b>Description:</b> {description}\n🔢 <b>Version:</b> {version}\n📱 <b>Min version:</b> {min_version}\n⚙️ <b>Settings:</b> {settings}\n🏷 <b>Category:</b> {category}\n\n🇷🇺 <b>Использование:</b>\n{usage_ru}\n\n🇺🇸 <b>Usage:</b>\n{usage_en}\n\nIs everything correct?",
    },
    "submission_sent": {
        "ru": "🎉 <b>Заявка отправлена!</b>\n\nМодератор рассмотрит её в ближайшее время.",
        "en": "🎉 <b>Submission sent!</b>\n\nA moderator will review it soon.",
    },
    "delete_sent": {
        "ru": "🗑 <b>Запрос на удаление отправлен!</b>\n\nМодератор рассмотрит его в ближайшее время.",
        "en": "🗑 <b>Delete request sent!</b>\n\nA moderator will review it soon.",
    },
    "ask_admin_comment": {
        "ru": "💬 Добавьте комментарий для администратора (необязательно).\n\nМожно пропустить.",
        "en": "💬 Add a comment for the admin (optional).\n\nYou can skip.",
    },
    "submission_cancelled": {"ru": "❌ Заявка отменена", "en": "❌ Submission cancelled"},
    "invalid_file": {"ru": "❌ Отправьте файл <code>.plugin</code>", "en": "❌ Please send a <code>.plugin</code> file"},
    "parse_error": {"ru": "❌ Ошибка: {error}", "en": "❌ Error: {error}"},
    "download_error": {"ru": "❌ Ошибка загрузки", "en": "❌ Download failed"},
    "need_text": {"ru": "✏️ Введите текст", "en": "✏️ Enter text"},
    "file_too_large": {
        "ru": "❌ Файл больше 8 МБ",
        "en": "❌ File is larger than 8 MB",
    },
    "plugin_already_exists": {
        "ru": "❌ Плагин с таким названием уже существует в каталоге",
        "en": "❌ A plugin with this name already exists",
    },
    "plugin_pending": {
        "ru": "❌ Заявка на этот плагин уже на рассмотрении",
        "en": "❌ A submission for this plugin is already pending",
    },
    "choose_plugin_to_update": {"ru": "🔄 Выберите плагин:", "en": "🔄 Choose plugin:"},
    "no_plugins_to_update": {"ru": "❌ У вас нет плагинов", "en": "❌ You don't have any plugins"},
    "upload_update_file": {
        "ru": "📎 <b>Отправьте обновлённый файл</b>\n\nТекущая версия: <b>{version}</b>",
        "en": "📎 <b>Send updated file</b>\n\nCurrent version: <b>{version}</b>",
    },
    "enter_changelog": {
        "ru": "📝 <b>Что нового?</b>\n\nОпишите изменения:",
        "en": "📝 <b>What's new?</b>\n\nDescribe the changes:",
    },
    "confirm_update": {
        "ru": "📋 <b>Проверьте обновление</b>\n\n📦 <b>Плагин:</b> {name}\n🔢 <b>Версия:</b> {old_version} → {version}\n📱 <b>Мин. версия:</b> {min_version}\n\n<b>Что нового:</b>\n{changelog}\n\nОтправить?",
        "en": "📋 <b>Review update</b>\n\n📦 <b>Plugin:</b> {name}\n🔢 <b>Version:</b> {old_version} → {version}\n📱 <b>Min version:</b> {min_version}\n\n<b>What's new:</b>\n{changelog}\n\nSubmit?",
    },
    "update_sent": {"ru": "🎉 <b>Обновление отправлено!</b>", "en": "🎉 <b>Update submitted!</b>"},
    "version_not_higher": {
        "ru": "❌ Новая версия должна быть выше текущей ({current})",
        "en": "❌ New version must be higher than current ({current})",
    },
    "catalog_title": {"ru": "📚 <b>Каталог плагинов</b>", "en": "📚 <b>Plugin Catalog</b>"},
    "catalog_empty": {"ru": "Пусто", "en": "Empty"},
    "catalog_page": {"ru": "Стр. {current}/{total}", "en": "Page {current}/{total}"},
    "search_prompt": {"ru": "🔍 Введите запрос:", "en": "🔍 Enter query:"},
    "search_results": {"ru": "🔍 Найдено <b>{count}</b>", "en": "🔍 Found <b>{count}</b>"},
    "search_empty": {"ru": "😕 Ничего не найдено", "en": "😕 Nothing found"},
    "not_found": {"ru": "❌ Не найдено", "en": "❌ Not found"},
    "profile_title": {"ru": "👤 <b>Профиль</b>", "en": "👤 <b>Profile</b>"},
    "profile_stats": {"ru": "Плагинов: <b>{plugins}</b> · Паков: <b>{icons}</b>", "en": "Plugins: <b>{plugins}</b> · Packs: <b>{icons}</b>"},
    "profile_empty": {"ru": "Нет работ в каталоге", "en": "No works in catalog"},
    "icons_title": {"ru": "🎨 <b>Паки иконок</b>", "en": "🎨 <b>Icon Packs</b>"},
    "icons_soon": {"ru": "🚧 Скоро", "en": "🚧 Coming soon"},
    "admin_denied": {"ru": "🚫 Нет доступа", "en": "🚫 Access denied"},
    "admin_title": {"ru": "👮 <b>Админ-панель</b>", "en": "👮 <b>Admin Panel</b>"},
    "admin_queue_empty": {"ru": "📭 Пусто", "en": "📭 Empty"},
    "admin_enter_user_id": {"ru": "Введите user_id пользователя:", "en": "Enter user ID:"},
    "admin_select_plugin": {"ru": "Выберите плагин:", "en": "Select plugin:"},
    "admin_author_linked": {"ru": "✅ Автор привязан к плагину", "en": "✅ Author linked to plugin"},
    "admin_user_unbanned": {"ru": "✅ Пользователь разблокирован", "en": "✅ User unbanned"},
    "notify_published": {"ru": "🎉 Плагин <b>{name}</b> опубликован!", "en": "🎉 Plugin <b>{name}</b> published!"},
    "notify_update_published": {"ru": "🎉 Обновление <b>{name}</b> опубликовано!", "en": "🎉 Update <b>{name}</b> published!"},
    "notify_deleted": {"ru": "🗑 Плагин <b>{name}</b> удалён.", "en": "🗑 Plugin <b>{name}</b> was deleted."},
    "notify_rejected": {"ru": "❌ <b>Заявка отклонена</b>\n\n{comment}", "en": "❌ <b>Submission rejected</b>\n\n{comment}"},
    "btn_back": {"ru": "🔙 Назад", "en": "🔙 Back"},
    "btn_cancel": {"ru": "❌ Отмена", "en": "❌ Cancel"},
    "btn_idea": {"ru": "💡 Предложить идею", "en": "💡 Suggest an idea"},
    "btn_support": {"ru": "🆘 Техподдержка", "en": "🆘 Support"},
    "btn_skip": {"ru": "⏭ Пропустить", "en": "⏭ Skip"},
    "btn_delete": {"ru": "🗑 Удалить", "en": "🗑 Delete"},
    "btn_confirm": {"ru": "✅ Подтвердить", "en": "✅ Confirm"},
    "btn_search": {"ru": "🔍 Поиск", "en": "🔍 Search"},
    "btn_retry": {"ru": "🔄 Ещё раз", "en": "🔄 Try again"},
    "btn_open": {"ru": "🔗 Открыть", "en": "🔗 Open"},
    "btn_update": {"ru": "🔄 Обновить", "en": "🔄 Update"},
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