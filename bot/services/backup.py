from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.storage import (
    ADMIN_SQLITE_PATH, CATALOG_SQLITE_PATH, LEGACY_SQLITE_PATH, SECRETS_PATH,
    flush_all, load_config, save_config,
)
from bot.cache import get_admins_super, get_config, invalidate

logger = logging.getLogger(__name__)

_INTERVAL_PRESETS = [6, 12, 24, 48, 168]
_CHECK_INTERVAL_SECONDS = 600


def get_backup_config() -> Dict[str, Any]:
    cfg = get_config()
    raw = cfg.get("backup") if isinstance(cfg, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    recipients = [int(x) for x in (raw.get("recipients") or []) if str(x).lstrip("-").isdigit()]
    return {
        "auto_enabled": bool(raw.get("auto_enabled")),
        "interval_hours": int(raw.get("interval_hours") or 24),
        "last_run": raw.get("last_run"),
        "recipients": recipients,
    }


def get_backup_recipients() -> list[int]:
    supers = get_admins_super()
    recipients = [rid for rid in get_backup_config()["recipients"] if rid in supers]
    return recipients if recipients else sorted(supers)


def set_backup_config(**fields: Any) -> Dict[str, Any]:
    cfg = load_config()
    backup = cfg.setdefault("backup", {})
    if not isinstance(backup, dict):
        backup = {}
        cfg["backup"] = backup
    backup.update(fields)
    save_config(cfg)
    invalidate("config")
    return get_backup_config()


def cycle_interval(current_hours: int) -> int:
    try:
        idx = _INTERVAL_PRESETS.index(int(current_hours))
    except ValueError:
        return _INTERVAL_PRESETS[0]
    return _INTERVAL_PRESETS[(idx + 1) % len(_INTERVAL_PRESETS)]


def _include_upload(path: Path, relative: Path) -> bool:
    parts = {part.lower() for part in relative.parts}
    if parts & {"private", "sessions", "secrets", "backups"}:
        return False
    if any(part == ".env" or part.startswith(".env.") for part in parts):
        return False
    name = path.name.lower()
    if name in {"runtime.json", "config.json"} or name.endswith((".session", ".session-journal", ".session-wal", ".session-shm")):
        return False
    excluded = {
        Path(str(p) + suffix).resolve()
        for p in (ADMIN_SQLITE_PATH, CATALOG_SQLITE_PATH, LEGACY_SQLITE_PATH, SECRETS_PATH)
        for suffix in ("", "-wal", "-shm", "-journal")
    }
    return path.resolve() not in excluded


def create_backup_zip(include_uploads: bool = False) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tmp_dir = Path(tempfile.mkdtemp(prefix="dbbackup_"))
    try:
        return _build_backup_zip(tmp_dir, ts, include_uploads)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _database_sources() -> list[tuple[tuple[str, Path], ...]]:
    from core.archive_log import ARCHIVE_PATH
    from core.audit_store import AUDIT_PATH
    from core.request_history import HISTORY_PATH

    groups = [(("catalog.sqlite3", CATALOG_SQLITE_PATH), ("admin.sqlite3", ADMIN_SQLITE_PATH))]
    groups += [((name, path),) for name, path in (
        ("history.sqlite3", HISTORY_PATH), ("audit.sqlite3", AUDIT_PATH), ("archive.sqlite3", ARCHIVE_PATH),
    )]
    return [tuple(item for item in group if Path(item[1]).is_file()) for group in groups]


def _write_databases(zf: zipfile.ZipFile, group: tuple[tuple[str, Path], ...]) -> None:
    connections = []
    try:
        for _, path in group:
            conn = sqlite3.connect(Path(path), timeout=30, isolation_level=None)
            connections.append(conn)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for conn in connections:
            conn.execute("BEGIN IMMEDIATE")
        for name, path in group:
            zf.write(path, arcname=name)
            wal = Path(f"{path}-wal")
            if wal.is_file() and wal.stat().st_size:
                zf.write(wal, arcname=f"{name}-wal")
    finally:
        for conn in connections:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            conn.close()


def _build_backup_zip(tmp_dir: Path, ts: str, include_uploads: bool) -> Path:
    from core.storage import _data_snapshot_lock

    zip_path = tmp_dir / f"storage_backup_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for index, group in enumerate(_database_sources()):
            if not group:
                continue
            if index:
                _write_databases(zf, group)
                continue
            with _data_snapshot_lock:
                _write_databases(zf, group)
        if include_uploads:
            try:
                from bot.helpers import get_uploads_dir
                uploads_dir = get_uploads_dir()
                if uploads_dir.exists():
                    for f in sorted(uploads_dir.rglob("*")):
                        if f.is_file() and not f.is_symlink():
                            relative = f.relative_to(uploads_dir)
                            if _include_upload(f, relative):
                                zf.write(f, arcname=str(Path("uploads") / relative))
            except Exception as exc:
                raise RuntimeError("Unable to include uploads in backup") from exc

    os.chmod(zip_path, 0o600)
    return zip_path


async def _send_backup_file(bot, chat_id: int, zip_path: Path, caption: str) -> None:
    from aiogram.types import FSInputFile
    from bot import limits

    if zip_path.stat().st_size <= limits.BOT_UPLOAD_BYTES:
        await bot.send_document(chat_id, FSInputFile(zip_path), caption=caption)
        return
    from bot.services.bot_mtproto import get_file_client
    from bot.services.plugin_files import _file_entity

    client = await get_file_client()
    entity = await _file_entity(client, bot, chat_id)
    await asyncio.wait_for(client.send_file(entity, str(zip_path), caption=caption, force_document=True), timeout=1800)


def _cleanup(zip_path: Path) -> None:
    if zip_path.parent.name.startswith("dbbackup_"):
        shutil.rmtree(zip_path.parent, ignore_errors=True)


def _error_reason(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:200]


async def send_backup(bot, chat_id: int) -> Optional[str]:
    try:
        await flush_all()
        zip_path = await asyncio.to_thread(create_backup_zip)
    except Exception as exc:
        logger.exception("event=backup.create_failed")
        return _error_reason(exc)
    try:
        caption = datetime.now(timezone.utc).strftime("Backup %Y-%m-%d %H:%M UTC")
        await _send_backup_file(bot, chat_id, zip_path, caption)
        return None
    except Exception as exc:
        logger.exception("event=backup.send_failed chat_id=%s", chat_id)
        return _error_reason(exc)
    finally:
        _cleanup(zip_path)


async def send_backup_to_admins(bot) -> int:
    sent = 0
    zip_path: Optional[Path] = None
    try:
        await flush_all()
        zip_path = await asyncio.to_thread(create_backup_zip)
    except Exception:
        logger.exception("event=backup.create_failed")
        return 0
    caption = datetime.now(timezone.utc).strftime("Backup %Y-%m-%d %H:%M UTC")
    for admin_id in get_backup_recipients():
        try:
            await _send_backup_file(bot, admin_id, zip_path, caption)
            sent += 1
        except Exception:
            logger.exception("event=backup.send_failed admin=%s", admin_id)
    _cleanup(zip_path)
    return sent


def _due(last_run: Optional[str], interval_hours: int) -> bool:
    if not last_run:
        return True
    try:
        last = datetime.fromisoformat(str(last_run))
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last >= timedelta(hours=max(1, interval_hours))


_worker_task: Optional[asyncio.Task] = None


async def _worker_loop(bot) -> None:
    while True:
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        try:
            cfg = get_backup_config()
            if not cfg["auto_enabled"]:
                continue
            if not _due(cfg["last_run"], cfg["interval_hours"]):
                continue
            sent = await send_backup_to_admins(bot)
            set_backup_config(last_run=datetime.now(timezone.utc).isoformat())
            logger.info("event=backup.auto_sent sent=%s", sent)
        except Exception:
            logger.exception("event=backup.worker_error")


def start_backup_worker(bot) -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _worker_task = loop.create_task(_worker_loop(bot))


async def stop_backup_worker() -> None:
    global _worker_task
    task = _worker_task
    _worker_task = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
