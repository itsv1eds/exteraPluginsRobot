from bot.formatting import plain_html, strip_blockquote_tags, telegram_html, user_mention
from bot.texts import t
from core.categories import category_fields


def archive_context(plugin: dict, lang: str = "ru") -> str:
    if plugin.get("format") != "elyx":
        return ""
    parts = []
    if plugin.get("compiled"):
        parts.append(t("elyx_compiled_notice", lang))
    if plugin.get("requirements"):
        parts.append(t("elyx_python_dependencies", lang, dependencies=plain_html(str(plugin["requirements"]))))
    if plugin.get("requires"):
        parts.append(t("elyx_plugin_dependencies", lang, dependencies=plain_html(", ".join(plugin["requires"]))))
    return "\n\n".join(parts)


def author_context(payload: dict, lang: str = "ru", *, existing_text: str = "") -> str:
    plugin = payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}
    archive = archive_context(plugin, lang)
    parts = [archive] if archive and archive not in existing_text else []
    comment = str(payload.get("admin_comment") or "").strip()
    if comment:
        parts.append(t("admin_request_comment", lang, comment=strip_blockquote_tags(telegram_html(comment))))
    note = str(payload.get("last_update_note") or "").strip()
    if note:
        parts.append(t("admin_request_update_note", lang, note=strip_blockquote_tags(telegram_html(note))).strip())
    media = [item for item in payload.get("comment_media") or [] if isinstance(item, dict) and item.get("file_id")]
    if media:
        parts.append(t("admin_request_comment_media", lang, count=len(media)))
    if payload.get("is_appeal"):
        parts.append(t(
            "admin_appeal_badge", lang,
            comment=strip_blockquote_tags(telegram_html(str(payload.get("appeal_comment") or "—"))),
        ))
    return "\n\n".join(part for part in parts if part not in existing_text)


def render_request_draft(entry: dict, *, include_context: bool = True, lang: str = "ru") -> str:
    from bot.services.publish import build_channel_post, build_icon_channel_post

    payload = entry.get("payload", {})
    if entry.get("type") == "unban_appeal":
        username = str(payload.get("username") or "").strip()
        who = f"@{username}" if username else str(payload.get("user_id") or "—")
        return t(
            "appeal_request_draft", lang,
            user=who,
            uid=payload.get("user_id") or "—",
            text=strip_blockquote_tags(telegram_html(str(payload.get("appeal_text") or "—"))),
        )
    if payload.get("submission_type") == "icon" or payload.get("icon"):
        return build_icon_channel_post(entry)

    plugin = payload.get("plugin", {})
    fallback_desc = plugin.get("description", "")
    patched_payload = {
        **payload,
        **category_fields(payload),
        "description_ru": payload.get("description_ru") or fallback_desc,
        "description_en": payload.get("description_en") or fallback_desc,
    }
    text = build_channel_post({"payload": patched_payload})
    context = author_context(payload, lang=lang) if include_context else ""
    return text + ("\n\n" + context.strip() if context.strip() else "")


def forum_request_text_and_file(entry: dict, *, lang: str = "ru") -> tuple[str, str | None]:
    payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
    plugin = payload.get("plugin", {}) if isinstance(payload.get("plugin"), dict) else {}
    icon = payload.get("icon", {}) if isinstance(payload.get("icon"), dict) else {}
    user_id = payload.get("user_id", 0)
    username = str(payload.get("username") or "").strip()
    request_type = entry.get("type", "new")
    submission_type = payload.get("submission_type") or ("icon" if icon else "plugin")
    request_id = entry.get("id", "?")
    user_link = user_mention(user_id, username)
    file_path = plugin.get("file_path") or icon.get("file_path")

    if request_type == "unban_appeal":
        return t(
            "appeal_forum_text",
            lang,
            user=user_link,
            text=strip_blockquote_tags(telegram_html(str(payload.get("appeal_text") or "—"))),
        ), None

    if request_type == "update":
        changelog = payload.get("changelog", "—")
        old_plugin = payload.get("old_plugin", {}) if isinstance(payload.get("old_plugin"), dict) else {}
        old_locale = old_plugin.get("ru") or old_plugin.get("en") or {}
        old_version = old_locale.get("version") or "?"
        text = t(
            "admin_request_update",
            lang,
            id=plain_html(request_id),
            name=plain_html(plugin.get("name") or "—"),
            old_version=plain_html(old_version),
            version=plain_html(plugin.get("version") or "—"),
            min_version=plain_html(plugin.get("min_version") or "—"),
            changelog=strip_blockquote_tags(telegram_html(changelog)) or "—",
            user=user_link,
        )
        if lang == "ru" and (payload.get("is_auto_update") or (username == "auto_updates" and str(user_id) == "0")):
            text = text.rsplit("\n\n<b>От:</b>", 1)[0]
    elif request_type == "delete":
        delete_slug = payload.get("delete_slug") or plugin.get("id") or "—"
        text = t(
            "admin_request_delete",
            lang,
            id=plain_html(request_id),
            name=plain_html(plugin.get("name") or "—"),
            slug=plain_html(delete_slug),
            user=user_link,
        )
    elif submission_type == "icon":
        text = t(
            "admin_request_icon",
            lang,
            id=plain_html(request_id),
            name=plain_html(icon.get("name") or "—"),
            author=plain_html(icon.get("author") or "—"),
            version=plain_html(icon.get("version") or "—"),
            count=plain_html(icon.get("count") or 0),
            user=user_link,
        )
    else:
        text = t(
            "admin_request_plugin",
            lang,
            id=plain_html(request_id),
            draft=render_request_draft(entry, include_context=False, lang=lang),
            user=user_link,
        )

    context = author_context(payload, lang, existing_text=text)
    if context:
        text += "\n\n" + context
    return text, str(file_path) if file_path else None
