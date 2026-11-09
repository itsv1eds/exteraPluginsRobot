from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from bot import limits
from bot.formatting import plain_html, quote_html, split_html, telegram_html, user_mention
from bot.helpers import fit_filename, get_uploads_dir, strip_html
from bot.services.moderation import previous_rounds_text, send_media_group, vote_summary
from bot.services.plugin_files import send_plugin_document
from bot.services.publish import _cached_publish_file
from bot.services.request_context import author_context, forum_request_text_and_file
from core import archive_log
from core.plugin_formats import payload_extension, plugin_extension
from core.request_store import get_request_by_id


logger = logging.getLogger(__name__)
_zone = timezone(timedelta(hours=5))
_status = {
    "pending": "На проверке", "rework": "На доработке", "approved": "Одобрена",
    "scheduled": "Запланирована", "published": "Опубликована", "rejected": "Отклонена",
    "deleted": "Перенесена в историю", "error": "Ошибка публикации",
}
_actions = {
    "moderation.vote": ("VOTE", "Голос модерации"),
    "moderation.vote_reason": ("VOTE", "Изменена причина голоса"),
    "moderation.reject": ("REJECT", "Заявка отклонена"),
    "moderation.rework": ("REWORK", "Отправлена на доработку"),
    "moderation.request_approved": ("APPROVE", "Заявка одобрена"),
    "moderation.request_scheduled": ("SCHEDULE", "Публикация запланирована"),
    "moderation.request_unscheduled": ("UNSCHEDULE", "Публикация снята с расписания"),
    "moderation.publish_success": ("PUBLISH", "Заявка опубликована"),
    "moderation.plugin_deleted": ("PLUGIN_DELETE", "Плагин удалён из канала"),
    "moderation.scheduled_publish_success": ("PUBLISH", "Заявка опубликована по расписанию"),
    "moderation.publish_validation_failed": ("PUBLISH_ERROR", "Заявка не прошла проверку перед публикацией"),
    "moderation.request_deleted": ("ARCHIVE", "Заявка перенесена в историю"),
    "moderation.request_withdrawn": ("WITHDRAW", "Автор отозвал заявку"),
    "moderation.request_updated": ("EDIT_REQUEST", "Автор обновил заявку"),
    "moderation.file_replaced": ("REPLACE_FILE", "Файл заявки заменён"),
    "moderation.plugin_blocked": ("BLOCK_PLUGIN", "ID плагина заблокирован"),
    "moderation.plugin_unblocked": ("UNBLOCK_PLUGIN", "ID плагина разблокирован"),
    "moderation.appeal_submitted": ("APPEAL", "Подана апелляция"),
    "moderation.appeal_approved": ("APPEAL_APPROVED", "Апелляция одобрена"),
    "moderation.appeal_denied": ("APPEAL_DENIED", "Апелляция отклонена"),
    "moderation.appeal_banned_final": ("APPEAL_DENIED", "Апелляция отклонена, бан окончательный"),
    "moderation.author_question": ("AUTHOR_QUESTION", "Вопрос автора"),
    "moderation.message_author": ("MOD_REPLY", "Ответ модерации"),
    "moderation.author_reply": ("AUTHOR_REPLY", "Ответ автора"),
    "admin.archive_channel_changed": ("SETTINGS", "Изменён канал журнала"),
    "admin.config_changed": ("SETTINGS", "Изменены настройки бота"),
    "broadcast.running": ("BROADCAST", "Рассылка запущена"),
    "broadcast.completed": ("BROADCAST", "Рассылка завершена"),
    "broadcast.interrupted": ("BROADCAST", "Рассылка прервана"),
    "broadcast.failed": ("BROADCAST", "Ошибка рассылки"),
    "poster.scheduled": ("POST_SCHEDULED", "Пост запланирован"),
    "poster.edited": ("POST_EDITED", "Запланированный пост изменён"),
    "poster.canceled": ("POST_CANCELED", "Публикация отменена"),
    "poster.sent": ("POST_PUBLISHED", "Пост опубликован"),
    "poster.failed": ("POST_FAILED", "Ошибка публикации поста"),
    "poster.deleted": ("POST_DELETED", "Пост удалён"),
}
_telegram_actions = {
    "restrictChatMember": ("RESTRICT", "Изменены ограничения пользователя"),
    "approveChatJoinRequest": ("JOIN_APPROVED", "Запрос на вступление одобрен"),
    "declineChatJoinRequest": ("JOIN_DENIED", "Запрос на вступление отклонён"),
    "pinChatMessage": ("PIN", "Сообщение закреплено"),
    "unpinChatMessage": ("UNPIN", "Сообщение откреплено"),
}
_fields = {
    "item": "Плагин", "plugin_id": "ID плагина", "user_id": "ID пользователя",
    "reason": "Причина", "comment": "Комментарий", "vote": "Голос", "anonymous": "Анонимно",
    "link": "Публикация", "scheduled_at": "Дата публикации", "run_at": "Дата публикации",
    "chat_id": "Чат", "channel_id": "Канал", "source_chat_id": "Исходный чат",
    "source_message_id": "Исходное сообщение", "message_id": "Сообщение", "post_id": "ID поста",
    "until_date": "До", "username": "Юзернейм", "request_type": "Тип заявки",
    "previous_status": "Предыдущий статус",
    "run_id": "ID рассылки", "total": "Получателей", "sent": "Отправлено", "failed": "Ошибок",
    "field": "Настройка", "value": "Значение", "text": "Текст", "error": "Ошибка", "kind": "Тип",
}


def _time(value):
    try:
        if isinstance(value, (int, float)):
            stamp = datetime.fromtimestamp(value, timezone.utc)
        else:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return str(value or "—")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(_zone).strftime("%d.%m.%Y %H:%M UTC+5")


def _link(chat_id, message_id, label):
    chat = str(chat_id)
    if not chat.startswith("-100") or not chat[4:].isdigit() or not str(message_id).isdigit():
        return plain_html(label)
    return f'<a href="https://t.me/c/{chat[4:]}/{int(message_id)}">{plain_html(label)}</a>'


def _actor(value, actor_id=None):
    label = telegram_html(value)
    if label:
        return label
    return user_mention(actor_id) if actor_id else "Бот"


def _request_history(entry):
    lines = []
    for item in entry.get("history") or []:
        if not isinstance(item, dict) or item.get("status") not in _status:
            continue
        parts = [plain_html(_time(item.get("changed_at"))), plain_html(_status[item["status"]])]
        if item.get("actor") or item.get("actor_id"):
            parts.append(_actor(item.get("actor"), item.get("actor_id")))
        lines.append(" · ".join(parts))
    if not lines:
        return ""
    return "<b>Ход рассмотрения</b>\n" + quote_html("\n".join(lines), expandable=True)


def _request_parts(entry):
    payload = entry.get("payload") or {}
    request_type = entry.get("type")
    tag = {"update": "PLUGIN_UPDATE", "delete": "PLUGIN_DELETE", "unban_appeal": "BAN_APPEAL"}.get(request_type, "NEW_PLUGIN")
    if payload.get("is_appeal"):
        tag = "PLUGIN_APPEAL"
    status = entry.get("status") or "pending"
    label = _status.get(status, status)
    if status == "published":
        label = {"unban_appeal": "Бан снят", "delete": "Плагин удалён"}.get(request_type, label)
    header = [f"#{tag}:", f"<b>Заявка:</b> <code>{plain_html(entry.get('id'))}</code>",
              f"<b>Статус:</b> {plain_html(label)}", f"<b>Подана:</b> {plain_html(_time(entry.get('submitted_at')))}"]
    if status in {"published", "rejected", "deleted", "approved", "scheduled", "rework"}:
        decision = next((row for row in reversed(entry.get("history") or []) if isinstance(row, dict) and row.get("status") == status), {})
        actor = decision.get("actor") if decision else entry.get("decided_by")
        actor_id = decision.get("actor_id") if decision else entry.get("decided_by_id")
        moment = decision.get("changed_at") if decision else entry.get("decided_at")
        if actor or actor_id or moment:
            heading = "Итоговое решение" if status in {"published", "rejected", "deleted"} else "Решение модерации"
            decision_line = [_actor(actor, actor_id)] if actor or actor_id else []
            if moment:
                decision_line.append(plain_html(_time(moment)))
            header.append(f"<b>{heading}:</b> " + " · ".join(decision_line))
        if decision.get("comment"):
            header.append(f"<b>Причина:</b>\n{quote_html(decision['comment'], expandable=True)}")
    if status == "scheduled" and payload.get("scheduled_at"):
        header.append(f"<b>Выйдет:</b> {plain_html(_time(payload['scheduled_at']))}")
    if payload.get("resubmitted_after_rework"):
        header.append("<b>Подана повторно после доработки.</b>")
    body, _ = forum_request_text_and_file(entry)
    context = author_context(payload, existing_text=body)
    parts = ["\n".join(header), body, context, _request_history(entry)]
    if request_type != "delete":
        parts.extend((vote_summary(entry), previous_rounds_text(entry)))
    return split_html("\n\n".join(part for part in parts if part), limits.MESSAGE_TEXT - 180)


async def _post(bot, chat_id, text, message_id=None):
    kwargs = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    for attempt in range(2):
        try:
            if message_id:
                await bot.edit_message_text(message_id=message_id, **kwargs)
                return message_id
            return (await bot.send_message(disable_notification=True, **kwargs)).message_id
        except TelegramBadRequest as exc:
            problem = str(exc).lower()
            if message_id and "message is not modified" in problem:
                return message_id
            if message_id and any(marker in problem for marker in ("message to edit not found", "message can't be edited", "message_id_invalid")):
                return await _post(bot, chat_id, text)
            if attempt or not any(marker in problem for marker in ("parse entities", "entity", "custom emoji")):
                raise
            kwargs.update(text=html.unescape(strip_html(text)), parse_mode=None)


async def _attachments(bot, chat_id, request_id, payload, state):
    anchor = state["parts"][0]["id"]
    link = _link(chat_id, anchor, f"Заявка {request_id}")
    item = payload.get("plugin") or payload.get("icon") or {}
    raw = Path(item.get("file_path") or "")
    source = raw.resolve() if raw.is_file() else None
    if source and (not source.is_relative_to(get_uploads_dir().resolve()) or not plugin_extension(source)):
        source = None
    name = fit_filename(str(item.get("id") or "plugin"), payload_extension(item))
    file_id = _cached_publish_file(payload, name) or item.get("file_id")
    stat = source.stat() if source else None
    signature = [source.name, stat.st_size, stat.st_mtime_ns] if source else [file_id]
    if (source or file_id) and state.get("file") != signature:
        try:
            await send_plugin_document(
                bot, chat_id, {**item, "file_path": str(source) if source else ""},
                file_id=file_id, caption=f"#REQUEST_FILE\n{link}", parse_mode="HTML", disable_notification=True,
            )
        except TelegramBadRequest:
            await _post(bot, chat_id, f"#REQUEST_FILE\n{link}\nФайл недоступен по сохранённому идентификатору.")
        state["file"] = signature
        await asyncio.to_thread(archive_log.save_request_card, chat_id, request_id, state)
    media = [item for item in payload.get("comment_media") or [] if isinstance(item, dict) and item.get("file_id")]
    signature = [[item.get("type"), item["file_id"]] for item in media]
    if signature and state.get("media") != signature:
        try:
            await send_media_group(bot, chat_id, media, caption=f"#REQUEST_MEDIA\n{link}", raise_errors=True)
        except TelegramBadRequest:
            await _post(bot, chat_id, f"#REQUEST_MEDIA\n{link}\nПримеры недоступны по сохранённым идентификаторам.")
        state["media"] = signature
        await asyncio.to_thread(archive_log.save_request_card, chat_id, request_id, state)


async def _update_card(bot, chat_id, entry, *, attach=True):
    request_id = str(entry.get("id") or "")
    if not request_id or entry.get("history_only") or entry.get("status") == "draft" or entry.get("archived_status") == "draft":
        return None
    state = await asyncio.to_thread(archive_log.load_request_card, chat_id, request_id)
    saved = state.setdefault("parts", [])
    for index, text in enumerate(_request_parts(entry)):
        if index:
            text = f"#REQUEST_DETAILS\n{_link(chat_id, saved[0]['id'], 'Заявка ' + request_id)}\n\n{text}"
        digest = hashlib.sha256(text.encode()).hexdigest()
        old = saved[index] if index < len(saved) else {}
        if old.get("digest") == digest:
            continue
        message_id = await _post(bot, chat_id, text, old.get("id"))
        if index == 0 and old.get("id") != message_id:
            state.pop("file", None)
            state.pop("media", None)
        value = {"id": message_id, "digest": digest}
        if index < len(saved):
            saved[index] = value
        else:
            saved.append(value)
        await asyncio.to_thread(archive_log.save_request_card, chat_id, request_id, state)
    if attach:
        await _attachments(bot, chat_id, request_id, entry.get("payload") or {}, state)
    return saved[0]["id"]


def _detail(key, value):
    if key in {"reason", "comment", "text"}:
        return f"<b>{_fields[key]}:</b>\n{quote_html(value, expandable=True)}"
    if key == "until_date":
        value = _time(value) if value else "Бессрочно"
    elif key in {"scheduled_at", "run_at"}:
        value = _time(value)
    elif key == "vote":
        value = {"yes": "За", "no": "Против"}.get(str(value), value)
    elif key == "previous_status":
        value = _status.get(str(value), value)
    elif isinstance(value, bool):
        value = "Да" if value else "Нет"
    elif isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return f"<b>{plain_html(_fields.get(key, key))}:</b> {plain_html(value)}"


def _action(kind, data):
    if kind == "audit":
        name = str(data.get("event") or "Событие")
        if name in {"moderation.ban", "moderation.unban"}:
            return None
        if name in {"moderation.publish_success", "moderation.scheduled_publish_success"} and (data.get("details") or {}).get("request_type") == "delete":
            return "PLUGIN_DELETE", "Плагин удалён из канала", data
        fallback = re.sub(r"\W+", "_", name.upper()).strip("_") or "ACTION"
        return (*_actions.get(name, (fallback, name)), data)
    if kind == "telegram.api" and data.get("ok") and data.get("method") in _telegram_actions:
        parameters = data.get("parameters") or {}
        tag, title = _telegram_actions[data["method"]]
        permissions = parameters.get("permissions")
        if tag == "RESTRICT" and isinstance(permissions, dict) and permissions.get("can_send_messages") is False:
            tag, title = "MUTE", "Пользователю запрещено писать в чат"
        return (tag, title, {
            "actor_id": data.get("actor_id"), "actor": data.get("actor"),
            "details": {k: parameters[k] for k in ("chat_id", "user_id", "message_id", "until_date") if k in parameters},
        })
    if kind == "telegram.update" and data.get("chat_join_request"):
        request = data["chat_join_request"]
        chat = request.get("chat") or {}
        user = request.get("from") or request.get("from_user") or {}
        return "JOINREQUEST", "Запрос на вступление", {
            "actor": user_mention(user.get("id"), user.get("username")),
            "details": {"chat_id": chat.get("title") or chat.get("id"), "user_id": user.get("id")},
        }
    return None


async def _send_action(bot, chat_id, number, recorded_at, action):
    tag, title, data = action
    details = data.get("details") or {}
    anonymous = bool(details.get("anonymous"))
    actor = "Аноним" if anonymous else _actor(data.get("actor"), data.get("actor_id"))
    lines = [f"#{tag}: <b>{plain_html(title)}</b>"]
    if details.get("chat_id") is not None:
        lines.append(_detail("chat_id", details["chat_id"]))
    if not anonymous and data.get("actor") and data.get("actor_id"):
        actor += f" · <code>{plain_html(data['actor_id'])}</code>"
    label = "Пользователь" if tag == "JOINREQUEST" else "Кто"
    lines.append(f"<b>{label}:</b> {actor}")
    request_id = str(data.get("request_id") or "")
    if request_id:
        state = await asyncio.to_thread(archive_log.load_request_card, chat_id, request_id)
        anchor = state["parts"][0]["id"] if state.get("parts") else None
        if anchor is None and (entry := get_request_by_id(request_id)):
            anchor = await _update_card(bot, chat_id, entry, attach=False)
        lines.append(_link(chat_id, anchor, f"Заявка {request_id}"))
    lines.append(f"<b>Когда:</b> {plain_html(_time(data.get('created_at') or recorded_at))}")
    lines.extend(_detail(key, value) for key, value in details.items()
                 if value is not None and key != "chat_id" and not (anonymous and key in {"user_id", "actor_id", "actor", "username"}))
    parts = split_html("\n".join(lines), limits.MESSAGE_TEXT)
    stream = f"readable-action:{number}"
    start = await asyncio.to_thread(archive_log.position, chat_id, stream) if len(parts) > 1 else 0
    for index in range(start, len(parts)):
        await _post(bot, chat_id, parts[index])
        if len(parts) > 1:
            await asyncio.to_thread(archive_log.advance, chat_id, stream, index + 1)


async def deliver_readable(bot, chat_id):
    after = await asyncio.to_thread(archive_log.position, chat_id, "readable")
    rows = await asyncio.to_thread(archive_log.next_events, after, 150)
    for number, kind, stamp, payload in rows:
        if kind not in {"request", "audit", "telegram.update", "telegram.api"}:
            continue
        data = json.loads(payload)
        if kind == "request":
            await _update_card(bot, chat_id, data)
        elif (action := _action(kind, data)) is not None:
            await _send_action(bot, chat_id, number, stamp, action)
        else:
            continue
        await asyncio.to_thread(archive_log.advance, chat_id, "readable", number)
        return True
    if rows:
        await asyncio.to_thread(archive_log.advance, chat_id, "readable", rows[-1][0])
    return bool(rows)


async def run_readable_feed(bot):
    from bot.services.archive import _sending, channel_ids, validate_archive_channel

    current = 0
    validated_at = 0.0
    loop = asyncio.get_running_loop()
    while True:
        token = _sending.set(True)
        try:
            chat_id, technical_chat_id = channel_ids()
            if not chat_id:
                await asyncio.sleep(5)
                continue
            if chat_id != current or loop.time() >= validated_at:
                await asyncio.to_thread(archive_log.initialize_readable_feed, chat_id, technical_chat_id)
                if not await validate_archive_channel(bot, chat_id):
                    await asyncio.sleep(30)
                    continue
                current, validated_at = chat_id, loop.time() + 60
            delivered = await deliver_readable(bot, chat_id)
            await asyncio.sleep(0.7 if delivered else 3)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(max(1, exc.retry_after))
        except Exception:
            logger.exception("event=archive_feed.delivery_failed")
            await asyncio.sleep(15)
        finally:
            _sending.reset(token)
