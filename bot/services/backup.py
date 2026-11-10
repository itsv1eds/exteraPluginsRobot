from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.storage import (
    ADMIN_SQLITE_PATH, CATALOG_SQLITE_PATH, LEGACY_SQLITE_PATH, SECRETS_PATH,
    flush_all, load_config, save_config, snapshot_databases,
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


def _build_backup_zip(tmp_dir: Path, ts: str, include_uploads: bool) -> Path:
    snapshots = snapshot_databases(tmp_dir)
    from core.archive_log import snapshot_archive

    journal = snapshot_archive(tmp_dir / "archive.sqlite3")
    if journal is not None:
        snapshots[journal.name] = journal
    from core.request_history import snapshot as snapshot_history

    history = snapshot_history(tmp_dir / "history.sqlite3")
    if history is not None:
        snapshots[history.name] = history
    from core.audit_store import snapshot as snapshot_audit

    audit_copy = snapshot_audit(tmp_dir / "audit.sqlite3")
    if audit_copy is not None:
        snapshots[audit_copy.name] = audit_copy

    zip_path = tmp_dir / f"storage_backup_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, snapshot in snapshots.items():
            zf.write(snapshot, arcname=name)
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
    for snapshot in snapshots.values():
        snapshot.unlink(missing_ok=True)
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


async def send_backup(bot, chat_id: int) -> bool:
    try:
        await flush_all()
        zip_path = await asyncio.to_thread(create_backup_zip)
    except Exception:
        logger.exception("event=backup.create_failed")
        return False
    try:
        caption = datetime.now(timezone.utc).strftime("Backup %Y-%m-%d %H:%M UTC")
        await _send_backup_file(bot, chat_id, zip_path, caption)
        return True
    except Exception:
        logger.exception("event=backup.send_failed chat_id=%s", chat_id)
        return False
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
