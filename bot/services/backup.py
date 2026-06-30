from __future__ import annotations

import asyncio
import logging
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from storage import SQLITE_PATH, load_config, save_config
from bot.cache import get_config, get_owners, invalidate

logger = logging.getLogger(__name__)

_INTERVAL_PRESETS = [6, 12, 24, 48, 168]
_CHECK_INTERVAL_SECONDS = 600


def get_backup_config() -> Dict[str, Any]:
    cfg = get_config()
    raw = cfg.get("backup") if isinstance(cfg, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    return {
        "auto_enabled": bool(raw.get("auto_enabled")),
        "interval_hours": int(raw.get("interval_hours") or 24),
        "last_run": raw.get("last_run"),
    }


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


def create_backup_zip() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tmp_dir = Path(tempfile.mkdtemp(prefix="dbbackup_"))
    snapshot = tmp_dir / f"storage_{ts}.sqlite3"
    conn = sqlite3.connect(str(SQLITE_PATH), timeout=60)
    try:
        conn.execute("VACUUM INTO ?", (str(snapshot),))
    finally:
        conn.close()
    zip_path = tmp_dir / f"storage_backup_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(snapshot, arcname="storage.sqlite3")
    snapshot.unlink(missing_ok=True)
    return zip_path


def _cleanup(zip_path: Path) -> None:
    try:
        zip_path.unlink(missing_ok=True)
        zip_path.parent.rmdir()
    except Exception:
        pass


async def send_backup(bot, chat_id: int) -> bool:
    from aiogram.types import FSInputFile

    try:
        zip_path = await asyncio.to_thread(create_backup_zip)
    except Exception:
        logger.exception("event=backup.create_failed")
        return False
    try:
        caption = datetime.now(timezone.utc).strftime("Backup %Y-%m-%d %H:%M UTC")
        await bot.send_document(chat_id, FSInputFile(str(zip_path)), caption=caption)
        return True
    except Exception:
        logger.exception("event=backup.send_failed chat_id=%s", chat_id)
        return False
    finally:
        _cleanup(zip_path)


async def send_backup_to_owners(bot) -> int:
    sent = 0
    zip_path: Optional[Path] = None
    try:
        zip_path = await asyncio.to_thread(create_backup_zip)
    except Exception:
        logger.exception("event=backup.create_failed")
        return 0
    from aiogram.types import FSInputFile

    caption = datetime.now(timezone.utc).strftime("Backup %Y-%m-%d %H:%M UTC")
    for owner_id in get_owners():
        try:
            await bot.send_document(owner_id, FSInputFile(str(zip_path)), caption=caption)
            sent += 1
        except Exception:
            logger.exception("event=backup.send_failed owner=%s", owner_id)
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
            sent = await send_backup_to_owners(bot)
            set_backup_config(last_run=datetime.now(timezone.utc).isoformat())
            logger.info("event=backup.auto_sent owners=%s", sent)
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


def stop_backup_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
    _worker_task = None
