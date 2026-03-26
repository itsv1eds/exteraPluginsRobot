import asyncio
import json
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram import Bot
from packaging import version as pkg_version

from bot.helpers import get_uploads_dir, sanitize_filename
from catalog import list_published_plugins
from plugin_parser import PluginParseError, parse_plugin_file
from request_store import add_request, get_requests, update_request_payload

logger = logging.getLogger(__name__)

DEFAULT_STORE_JSON_URLS = [
    "https://raw.githubusercontent.com/KangelPlugins/Plugins-Store/refs/heads/main/store.json",
    "https://raw.githubusercontent.com/KangelPlugins/Plugins-Store/main/store.json",
]

_worker_task: Optional[asyncio.Task] = None
_interval_seconds = 60 * 30
_manual_task: Optional[asyncio.Task] = None


@dataclass
class StoreItem:
    plugin_id: str
    url: str
    version: str


def _norm_store_id(value: str) -> str:
    return str(value or "").strip().lower()


def _parse_store_json(data: Any) -> Dict[str, StoreItem]:
    out: Dict[str, StoreItem] = {}
    if not isinstance(data, dict):
        return out

    for plugin_id, value in data.items():
        pid = str(plugin_id or "").strip()
        if not pid:
            continue

        url = ""
        version = ""
        if isinstance(value, str):
            url = value
        elif isinstance(value, dict):
            url = str(value.get("url") or "")
            version = str(value.get("version") or "")
        else:
            continue

        url = url.strip()
        version = version.strip()
        if not url:
            continue

        item = StoreItem(plugin_id=pid, url=url, version=version)
        out[_norm_store_id(pid)] = item

    return out


def _http_get_json(url: str, timeout_sec: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "exteraPluginsRobot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    return json.loads(raw)


def _download_file(url: str, dest: Path, timeout_sec: int = 60) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "exteraPluginsRobot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        dest.write_bytes(resp.read())


def _is_version_higher(current: str, candidate: str) -> bool:
    cur = (current or "").strip()
    cand = (candidate or "").strip()
    if not cand:
        return False
    if not cur:
        return True
    try:
        return pkg_version.parse(cand) > pkg_version.parse(cur)
    except Exception:
        return cand != cur


def _get_catalog_plugin_id(entry: Dict[str, Any]) -> str:
    ru = entry.get("ru") or {}
    en = entry.get("en") or {}
    val = ""
    if isinstance(ru, dict):
        val = ru.get("id") or ""
    if not val and isinstance(en, dict):
        val = en.get("id") or ""
    return str(val or "").strip()


def _get_catalog_match_keys(entry: Dict[str, Any]) -> list[str]:
    keys: list[str] = []

    ru = entry.get("ru") or {}
    en = entry.get("en") or {}
    if isinstance(ru, dict) and ru.get("id"):
        keys.append(str(ru.get("id") or ""))
    if isinstance(en, dict) and en.get("id"):
        keys.append(str(en.get("id") or ""))

    if entry.get("id"):
        keys.append(str(entry.get("id") or ""))

    if entry.get("slug"):
        keys.append(str(entry.get("slug") or ""))

    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        kn = _norm_store_id(k)
        if not kn or kn in seen:
            continue
        seen.add(kn)
        out.append(kn)
    return out


def _get_catalog_version(entry: Dict[str, Any]) -> str:
    ru = entry.get("ru") or {}
    en = entry.get("en") or {}
    val = ""
    if isinstance(ru, dict):
        val = ru.get("version") or ""
    if not val and isinstance(en, dict):
        val = en.get("version") or ""
    return str(val or "").strip()


def _find_pending_update(slug: str) -> Optional[Dict[str, Any]]:
    for req in get_requests(status="pending", request_type="update"):
        payload = req.get("payload", {}) if isinstance(req.get("payload"), dict) else {}
        if str(payload.get("update_slug") or "").strip() == slug:
            return req
    return None


async def _notify_admins(bot: Bot, entry: Dict[str, Any]) -> None:
    try:
        from bot.routers.user_flow import notify_admins_request

        await notify_admins_request(bot, entry)
    except Exception:
        logger.exception("event=catalog_auto_updates.notify_admins.failed request_id=%s", entry.get("id"))


async def _create_or_update_request(bot: Bot, catalog_entry: Dict[str, Any], meta_path: Path, meta: Any, store_version: str) -> None:
    slug = str(catalog_entry.get("slug") or "").strip()
    if not slug:
        return

    old_version = _get_catalog_version(catalog_entry)

    if not _is_version_higher(old_version, meta.version):
        try:
            meta_path.unlink(missing_ok=True)
        except Exception:
            pass
        return

    plugin_payload = {
        "id": meta.id,
        "name": meta.name,
        "description": meta.description,
        "author": meta.author,
        "version": meta.version,
        "min_version": meta.min_version,
        "has_ui_settings": meta.has_ui_settings,
        "file_path": str(meta_path),
    }

    category_key = str(catalog_entry.get("category") or "").strip()
    payload: Dict[str, Any] = {
        "user_id": 0,
        "username": "auto_updates",
        "plugin": plugin_payload,
        "changelog": f"Автообновление из GitHub store.json: {old_version or '—'} → {store_version or meta.version}",
        "update_slug": slug,
        "old_plugin": catalog_entry,
        "description_ru": (catalog_entry.get("ru") or {}).get("description", "") if isinstance(catalog_entry.get("ru"), dict) else "",
        "description_en": (catalog_entry.get("en") or {}).get("description", "") if isinstance(catalog_entry.get("en"), dict) else "",
        "usage_ru": (catalog_entry.get("ru") or {}).get("usage", "") if isinstance(catalog_entry.get("ru"), dict) else "",
        "usage_en": (catalog_entry.get("en") or {}).get("usage", "") if isinstance(catalog_entry.get("en"), dict) else "",
        "category_key": category_key,
        "submission_type": "update",
        "admin_comment": "Автоматическая заявка: обнаружена новая версия в GitHub каталоге.",
    }

    existing = _find_pending_update(slug)
    if existing:
        existing_payload = existing.get("payload", {}) if isinstance(existing.get("payload"), dict) else {}
        existing_version = ((existing_payload.get("plugin") or {}) if isinstance(existing_payload.get("plugin"), dict) else {}).get("version")
        if str(existing_version or "").strip() == str(meta.version or "").strip():
            try:
                meta_path.unlink(missing_ok=True)
            except Exception:
                pass
            return

        req_id = str(existing.get("id") or "").strip()
        if req_id:
            update_request_payload(req_id, payload)
            updated = {**existing, "payload": {**existing_payload, **payload}}
            asyncio.create_task(_notify_admins(bot, updated))
        return

    entry = add_request(payload, request_type="update")
    asyncio.create_task(_notify_admins(bot, entry))


async def run_catalog_auto_updates_once(bot: Bot, store_urls: Optional[list[str]] = None) -> None:
    urls = store_urls or DEFAULT_STORE_JSON_URLS

    store_data = None
    last_exc: Optional[Exception] = None
    for url in urls:
        try:
            store_data = _http_get_json(url)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            continue

    if store_data is None:
        if last_exc:
            logger.warning("event=catalog_auto_updates.store_fetch_failed error=%s", last_exc)
        return

    store = _parse_store_json(store_data)
    if not store:
        return

    for plugin in list_published_plugins():
        try:
            item: StoreItem | None = None
            matched_key = ""
            for key in _get_catalog_match_keys(plugin):
                candidate = store.get(key)
                if candidate:
                    item = candidate
                    matched_key = key
                    break
            if not item:
                continue

            our_version = _get_catalog_version(plugin)
            store_version = item.version

            if store_version and not _is_version_higher(our_version, store_version):
                continue

            uploads = get_uploads_dir()
            temp_id = item.plugin_id or matched_key or (plugin.get("slug") or "")
            temp_path = uploads / f"auto_{sanitize_filename(str(temp_id))}.plugin"
            _download_file(item.url, temp_path)

            try:
                meta = parse_plugin_file(temp_path, fallback_version=store_version)
            except (FileNotFoundError, PluginParseError) as exc:
                temp_path.unlink(missing_ok=True)
                logger.warning("event=catalog_auto_updates.parse_failed plugin_id=%s error=%s", temp_id, exc)
                continue

            if not _is_version_higher(our_version, meta.version):
                temp_path.unlink(missing_ok=True)
                continue

            final_name = f"{sanitize_filename(meta.id)}.plugin"
            final_path = uploads / final_name
            if temp_path != final_path:
                final_path.unlink(missing_ok=True)
                temp_path.rename(final_path)

            await _create_or_update_request(bot, plugin, final_path, meta, store_version or meta.version)

        except Exception:
            logger.exception("event=catalog_auto_updates.failed")


async def _worker_loop(bot: Bot) -> None:
    while True:
        try:
            await run_catalog_auto_updates_once(bot)
        except Exception:
            logger.exception("event=catalog_auto_updates.loop_error")
        await asyncio.sleep(_interval_seconds)


def start_manual_catalog_auto_updates(
    bot: Bot,
    on_done: Optional[callable] = None,
) -> bool:
    global _manual_task
    if _manual_task and not _manual_task.done():
        return False

    async def _run() -> None:
        try:
            await run_catalog_auto_updates_once(bot)
        except Exception:
            logger.exception("event=catalog_auto_updates.manual_error")
        finally:
            try:
                if on_done:
                    res = on_done()
                    if asyncio.iscoroutine(res):
                        await res
            except Exception:
                logger.exception("event=catalog_auto_updates.manual_done_callback_error")

    _manual_task = asyncio.create_task(_run())
    return True


def start_catalog_auto_updates_worker(bot: Bot, interval_seconds: int = _interval_seconds) -> None:
    global _worker_task, _interval_seconds
    _interval_seconds = interval_seconds
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_worker_loop(bot))


def stop_catalog_auto_updates_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
    _worker_task = None
