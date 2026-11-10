from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
import os
import resource
import time
from collections import Counter
from contextvars import ContextVar
from pathlib import Path

from aiogram.client.default import Default
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputFile

from bot.cache import get_config
from bot.helpers import get_uploads_dir
from core import archive_log, memory
from core.storage_layout import DATA_DIR
from core.plugin_formats import plugin_extension


logger = logging.getLogger(__name__)
_sending = ContextVar("archive_sending", default=False)
action_actor = ContextVar("archive_action_actor", default=None)
_worker = None
_readable_worker = None
_handler = None
_file_part_bytes = 40 * 1024 * 1024
_events_interval = 60
_file_interval = 60
_resources_interval = 300
_media_types = {"document", "photo", "video", "animation", "audio", "voice", "video_note"}


def _channel_id(value) -> int:
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return value if value < -1000000000000 else 0


def suppress_journal():
    return _sending.set(True)


def restore_journal(token):
    _sending.reset(token)


def channel_ids() -> tuple[int, int]:
    config = get_config().get("archive")
    if not isinstance(config, dict):
        return 0, 0
    readable = _channel_id(config.get("channel_id"))
    technical = _channel_id(config.get("technical_channel_id"))
    return readable, technical if technical != readable else 0


async def validate_archive_channel(bot, chat_id: int) -> bool:
    chat = await bot.get_chat(chat_id)
    if chat.type != "channel" or chat.username or getattr(chat, "active_usernames", None):
        return False
    member = await bot.get_chat_member(chat_id, bot.id)
    return member.status == "creator" or (member.status == "administrator" and bool(member.can_post_messages))


class ArchiveHandler(logging.Handler):
    def emit(self, record):
        if _sending.get() or record.name == __name__:
            return
        try:
            archive_log.record_event("log", {
                "logger": record.name,
                "level": record.levelname,
                "message": self.format(record),
            })
        except Exception:
            self.handleError(record)


def _api_payload(value):
    if isinstance(value, InputFile):
        return {"filename": value.filename}
    if isinstance(value, Default):
        return {"default": value.name}
    if isinstance(value, dict):
        return {key: _api_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_api_payload(item) for item in value]
    return value


class ArchiveApiMiddleware:
    async def __call__(self, make_request, bot, method):
        name = method.__api_method__
        if name == "getUpdates":
            result = await make_request(bot, method)
            if result:
                try:
                    await asyncio.to_thread(record_updates, bot.id, result, channel_ids())
                except Exception:
                    logger.exception("event=archive.updates_record_failed")
            return result
        if _sending.get() or not name.startswith(("send", "edit", "delete", "forward", "copy", "ban", "unban", "restrict", "approve", "decline", "answer", "pin", "unpin", "setMessage")) or name == "deleteWebhook":
            return await make_request(bot, method)
        payload = {"method": name, "parameters": _api_payload(method.model_dump(exclude_none=True, warnings=False))}
        if actor := action_actor.get():
            payload["actor_id"], payload["actor"] = actor
        try:
            result = await make_request(bot, method)
        except Exception as exc:
            payload.update(ok=False, error=type(exc).__name__)
            await self._record(payload)
            raise
        payload["ok"] = True
        if isinstance(result, list):
            payload["message_ids"] = [getattr(message, "message_id", None) for message in result]
        elif hasattr(result, "message_id"):
            payload["message_id"] = result.message_id
        await self._record(payload)
        return result

    async def _record(self, payload):
        try:
            await asyncio.to_thread(archive_log.record_event, "telegram.api", payload)
        except Exception:
            logger.exception("event=archive.api_record_failed method=%s", payload["method"])


def _seed_history():
    from core.request_store import get_all_requests
    from core.storage import load_stats, load_broadcasts, load_poster

    sources = (
        ("request", get_all_requests()),
        ("moderation", load_stats().get("moderation_activity") or []),
        ("broadcast", load_broadcasts().get("runs") or []),
        ("poster", load_poster().get("posts") or []),
    )
    archive_log.record_events((kind, record, True) for kind, records in sources for record in records if isinstance(record, dict))
    media = []
    for entry in sources[0][1]:
        payload = entry.get("payload") or {}
        source = _request_media_source(entry)
        for item in payload.get("comment_media") or []:
            if isinstance(item, dict) and item.get("file_id") and item.get("type") in _media_types:
                media.append((item["file_id"], item["type"], None, None, source))
    if media:
        archive_log.record_media(media)


def _request_media_source(entry):
    payload = entry.get("payload") or {}
    plugin = payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}
    who = f"@{payload['username']}" if payload.get("username") else str(payload.get("user_id") or "?")
    return f"Пример к заявке {entry.get('id')} · {plugin.get('name') or plugin.get('id') or '—'} · от {who}"


def _message_media_source(name, message):
    sender = message.get("from_user") or message.get("from") or {}
    who = f"@{sender['username']}" if sender.get("username") else str(sender.get("id") or "?")
    kind = "изменённое сообщение" if name.startswith("edited") else "сообщение"
    moment = datetime.fromtimestamp(int(message.get("date") or 0), timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    return f"Личное {kind} боту от {who} (id {sender.get('id', '?')}) · #{message.get('message_id', '?')} · {moment}"


def record_updates(bot_id, updates, archive_chat_ids):
    events = []
    media = []
    for update in updates:
        post = update.channel_post or update.edited_channel_post
        if post and post.chat.id in archive_chat_ids:
            continue
        payload = _api_payload(update.model_dump(exclude_none=True, exclude_unset=True, warnings=False))
        events.append(("telegram.update", {"bot_id": bot_id, **payload}, True))
        for name in ("message", "edited_message"):
            message = payload.get(name) or {}
            if (message.get("chat") or {}).get("type") != "private":
                continue
            for kind in _media_types:
                item = message.get(kind)
                if kind == "photo" and item:
                    item = item[-1]
                if isinstance(item, dict) and item.get("file_id"):
                    media.append((item["file_id"], kind, item.get("file_name"), item.get("file_size"),
                                  _message_media_source(name, message)))
    archive_log.record_events(events)
    if media:
        archive_log.record_media(media)


def _drop_cache(stream):
    if hasattr(os, "posix_fadvise"):
        try:
            os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass


def _resources():
    values = {}
    for line in Path("/proc/self/status").read_text().splitlines() if Path("/proc/self/status").is_file() else ():
        if line.startswith(("VmRSS:", "VmHWM:", "Threads:")):
            values[line.split(":")[0].lower()] = int(line.split()[1])
    for name in ("memory.current", "memory/memory.usage_in_bytes", "memory/memory.max_usage_in_bytes", "memory/memory.failcnt"):
        path = Path("/sys/fs/cgroup") / name
        if path.is_file():
            values[name.rsplit("/", 1)[-1].replace(".", "_")] = int(path.read_text().split()[0])
    for name in ("memory.stat", "memory/memory.stat"):
        path = Path("/sys/fs/cgroup") / name
        if path.is_file():
            stat = dict(line.split() for line in path.read_text().splitlines() if line.count(" ") == 1)
            for key in ("cache", "rss", "file", "anon"):
                if key in stat:
                    values[f"stat_{key}"] = int(stat[key])
            break
    usage = resource.getrusage(resource.RUSAGE_SELF)
    values["cpu_ms"] = int((usage.ru_utime + usage.ru_stime) * 1000)
    for name in ("cpuacct/cpuacct.usage", "cpu/cpuacct.usage", "cpuacct.usage"):
        path = Path("/sys/fs/cgroup") / name
        if path.is_file():
            values["cgroup_cpu_ms"] = int(path.read_text().split()[0]) // 1_000_000
            break
    net = Path("/proc/net/dev")
    if net.is_file():
        rx = tx = 0
        for line in net.read_text().splitlines()[2:]:
            name, _, data = line.partition(":")
            fields = data.split()
            if name.strip() != "lo" and len(fields) >= 9:
                rx += int(fields[0])
                tx += int(fields[8])
        values["net_rx_kib"], values["net_tx_kib"] = rx // 1024, tx // 1024
    fd_dir = Path("/proc/self/fd")
    if fd_dir.is_dir():
        values["fds"] = sum(1 for _ in fd_dir.iterdir())
    total = 0
    for directory, _, files in os.walk(DATA_DIR):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_blocks * 512
            except OSError:
                pass
    values["data_kib"] = total // 1024
    return values


def _scan_files(seen):
    root = get_uploads_dir()
    known = archive_log.known_files()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part.startswith((".", "submission-")) for part in relative.parts) or relative.parts[0] == "media" or not plugin_extension(path) or path.is_symlink() or not path.is_file():
            continue
        try:
            stat = path.stat()
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if seen.get(path) == signature:
                continue
            if (str(relative), stat.st_size) in known:
                seen[path] = signature
                continue
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
                _drop_cache(source)
            current = path.stat()
            if (current.st_ino, current.st_size, current.st_mtime_ns) != signature:
                continue
            archive_log.record_file(relative, digest, stat.st_size)
            seen[path] = signature
        except FileNotFoundError:
            continue


async def _send_events(bot, chat_id):
    after = await asyncio.to_thread(archive_log.position, chat_id, "events")
    rows = await asyncio.to_thread(archive_log.next_events, after)
    if not rows:
        return False
    lines = []
    size = 0
    for row in rows:
        line = json.dumps({"id": row[0], "kind": row[1], "recorded_at": row[2], "data": json.loads(row[3])}, ensure_ascii=False).encode() + b"\n"
        if lines and size + len(line) > 8 * 1024 * 1024:
            break
        lines.append(line)
        size += len(line)
    rows = rows[:len(lines)]
    data = b"".join(lines)
    first, last = rows[0][0], rows[-1][0]
    counts = Counter(row[1] for row in rows)
    caption = f"Журнал · #{first}–{last}\n" + " · ".join(f"{kind} {count}" for kind, count in counts.items())
    compressed = await asyncio.to_thread(gzip.compress, data, compresslevel=3)
    await bot.send_document(chat_id, BufferedInputFile(compressed, filename=f"events-{first}-{last}.jsonl.gz"), caption=caption, parse_mode=None, disable_notification=True)
    await asyncio.to_thread(archive_log.advance, chat_id, "events", last)
    return True


async def _forward_media(bot, chat_id):
    row = await asyncio.to_thread(archive_log.next_media, time.time())
    if not row:
        return False
    number, file_id, kind, local, source = row
    caption = f"Медиа #{number} · {kind}\n{source or 'Источник не записан (сохранено до 10.11.2026)'}"
    try:
        if kind in {"video_note"}:
            message = await bot.send_video_note(chat_id, file_id, disable_notification=True)
        else:
            message = await getattr(bot, f"send_{kind}")(chat_id, file_id, caption=caption, parse_mode=None,
                                                         disable_notification=True)
    except TelegramRetryAfter:
        raise
    except TelegramBadRequest as exc:
        await asyncio.to_thread(archive_log.update_media, number, local, time.time() + 86400)
        logger.warning("event=archive.media_forward_failed media_id=%s type=%s error=%s", number, kind, exc.message)
        return False
    await asyncio.to_thread(archive_log.update_media, number, f"channel:{chat_id}:{message.message_id}")
    await asyncio.to_thread(archive_log.record_event, "media.archived", {"file_id": file_id, "kind": kind, "channel_id": chat_id, "message_id": message.message_id}, True)
    if local:
        await asyncio.to_thread(_drop_local_media, local)
    return True


def _drop_local_media(relative):
    root = (get_uploads_dir() / "media").resolve()
    path = (get_uploads_dir() / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        return
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


def _prune_delivered(readable_chat_id, chat_id):
    upto = archive_log.position(chat_id, "events")
    if readable_chat_id:
        upto = min(upto, archive_log.position(readable_chat_id, "readable"))
    if upto and archive_log.prune_events(upto):
        archive_log.compact()


def _open_file_part(relative, digest, size, part):
    root = get_uploads_dir().resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("archive path escapes uploads")
    for source in (path, path.parent / "versions" / digest / path.name):
        if source.is_symlink() or not source.is_file() or source.stat().st_size != size:
            continue
        stream = source.open("rb")
        try:
            matches = hashlib.file_digest(stream, "sha256").hexdigest() == digest
            _drop_cache(stream)
            if matches:
                stream.seek(part * _file_part_bytes)
                return stream
        except BaseException:
            stream.close()
            raise
        stream.close()
    raise FileNotFoundError(relative)


class ArchiveFilePart(InputFile):
    def __init__(self, stream, length, filename):
        super().__init__(filename)
        self.stream = stream
        self.length = length

    async def read(self, bot):
        remaining = self.length
        while remaining:
            chunk = await asyncio.to_thread(self.stream.read, min(self.chunk_size, remaining))
            if not chunk:
                raise OSError("archive file changed during upload")
            yield chunk
            remaining -= len(chunk)
        _drop_cache(self.stream)


async def _send_file(bot, chat_id):
    row = await asyncio.to_thread(archive_log.next_file, chat_id, _file_part_bytes, time.time())
    if not row:
        return False
    number, digest, relative, size, done = row
    try:
        stream = await asyncio.to_thread(_open_file_part, relative, digest, size, done)
    except OSError:
        await asyncio.to_thread(archive_log.retry_file, number, time.time() + 900)
        logger.warning("event=archive.local_file_unavailable file_id=%s", number)
        return False
    count = max(1, (size + _file_part_bytes - 1) // _file_part_bytes)
    name = Path(relative).name
    filename = name if count == 1 else f"{digest[:12]}-{done + 1:03d}-of-{count:03d}.part"
    caption = f"Файл #{number} · {done + 1}/{count}\n{name}\nSHA-256 {digest}"
    with stream:
        document = ArchiveFilePart(stream, min(_file_part_bytes, size - done * _file_part_bytes), filename)
        await bot.send_document(chat_id, document, caption=caption, parse_mode=None, disable_notification=True)
    await asyncio.to_thread(archive_log.advance, chat_id, f"file:{number}", done + 1)
    return True


async def _run(bot):
    seen = {}
    next_scan = 0.0
    next_prune = 0.0
    next_events = 0.0
    next_file = 0.0
    next_resources = 0.0
    checked = 0
    valid_until = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            now = loop.time()
            if now >= next_scan:
                await asyncio.to_thread(_scan_files, seen)
                next_scan = now + 60
            readable_chat_id, chat_id = channel_ids()
            busy = False
            token = _sending.set(True)
            try:
                if chat_id and (checked != chat_id or now >= valid_until):
                    if not await validate_archive_channel(bot, chat_id):
                        logger.warning("event=archive.channel_not_private_or_not_writable chat_id=%s", chat_id)
                        chat_id = 0
                    else:
                        checked, valid_until = chat_id, now + 60
                if chat_id:
                    if now >= next_events:
                        busy = await _send_events(bot, chat_id)
                        next_events = now + _events_interval
                    if not busy and now >= next_file:
                        busy = await _send_file(bot, chat_id)
                        if busy:
                            next_file = now + _file_interval
                    if not busy:
                        busy = await _forward_media(bot, chat_id)
            finally:
                _sending.reset(token)
            if now >= next_resources:
                await asyncio.to_thread(memory.trim)
                logger.info("event=archive.resources %s", " ".join(f"{key}={value}" for key, value in (await asyncio.to_thread(_resources)).items()))
                next_resources = now + _resources_interval
            if chat_id and now >= next_prune:
                await asyncio.to_thread(_prune_delivered, readable_chat_id, chat_id)
                next_prune = now + 600
            await asyncio.sleep(3 if busy else 5)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(max(1, exc.retry_after))
        except Exception as exc:
            logger.warning("event=archive.delivery_failed type=%s", type(exc).__name__)
            await asyncio.sleep(30)


async def start_archive(bot):
    global _worker, _readable_worker, _handler
    if _worker and not _worker.done():
        return
    await asyncio.to_thread(_seed_history)
    from bot.services.archive_feed import run_readable_feed

    chat_id, technical_chat_id = channel_ids()
    if chat_id:
        await asyncio.to_thread(archive_log.initialize_readable_feed, chat_id, technical_chat_id)
    _handler = ArchiveHandler(logging.INFO)
    logging.getLogger().addHandler(_handler)
    _worker = asyncio.create_task(_run(bot))
    _readable_worker = asyncio.create_task(run_readable_feed(bot))


async def stop_archive():
    global _worker, _readable_worker, _handler
    if _handler:
        logging.getLogger().removeHandler(_handler)
        _handler.close()
        _handler = None
    workers = [task for task in (_worker, _readable_worker) if task is not None]
    for task in workers:
        task.cancel()
    if workers:
        await asyncio.gather(*workers, return_exceptions=True)
    _worker = _readable_worker = None
