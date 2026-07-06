
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from storage import load_dialogs, save_dialogs

_MAX_THREADS = 500


def _key(chat_id: int, message_id: int) -> str:
    return f"{chat_id}:{message_id}"


def register_dialog_message(
    chat_id: int,
    message_id: int,
    *,
    peer_id: int,
    request_id: str,
    author_id: int,
    admin_id: int,
) -> None:
    doc = load_dialogs()
    threads = doc.get("threads", {})
    threads[_key(chat_id, message_id)] = {
        "peer_id": int(peer_id),
        "request_id": str(request_id),
        "author_id": int(author_id),
        "admin_id": int(admin_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if len(threads) > _MAX_THREADS:
        ordered = sorted(threads.items(), key=lambda kv: str(kv[1].get("created_at") or ""))
        for stale_key, _ in ordered[: len(threads) - _MAX_THREADS]:
            threads.pop(stale_key, None)
    doc["threads"] = threads
    save_dialogs(doc)


def get_dialog_ref(chat_id: int, message_id: int) -> Optional[Dict[str, Any]]:
    doc = load_dialogs()
    ref = doc.get("threads", {}).get(_key(chat_id, message_id))
    return ref if isinstance(ref, dict) else None
