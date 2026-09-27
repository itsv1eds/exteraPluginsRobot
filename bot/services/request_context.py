from bot.formatting import plain_html, strip_blockquote_tags, telegram_html
from bot.texts import t


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
    media = [item for item in payload.get("comment_media") or [] if isinstance(item, dict) and item.get("file_id")]
    if media:
        parts.append(t("admin_request_comment_media", lang, count=len(media)))
    if payload.get("is_appeal"):
        parts.append(t(
            "admin_appeal_badge", lang,
            comment=strip_blockquote_tags(telegram_html(str(payload.get("appeal_comment") or "—"))),
        ))
    return "\n\n".join(part for part in parts if part not in existing_text)
