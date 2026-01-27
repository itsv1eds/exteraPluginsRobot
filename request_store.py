from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from storage import load_requests, save_requests


def add_request(payload: Dict[str, Any], request_type: str = "new") -> Dict[str, Any]:
    database = load_requests()
    requests = database.setdefault("requests", [])
    plugin = payload.get("plugin", {})
    plugin_id = plugin.get("id")
    base_id = plugin_id if plugin_id else uuid4().hex
    existing_ids = {req.get("id") for req in requests if req.get("id")}
    final_id = base_id
    suffix = 1
    while final_id in existing_ids:
        final_id = f"{base_id}+{suffix}"
        suffix += 1

    entry = {
        "id": final_id,
        "type": request_type,
        "status": "pending",
        "submitted_at": datetime.utcnow().isoformat(),
        "payload": payload,
        "history": [],
    }
    requests.append(entry)
    save_requests(database)
    return entry


def get_requests(status: str = "pending", request_type: str | None = None) -> list[Dict[str, Any]]:
    database = load_requests()
    requests = database.get("requests", [])
    result = [req for req in requests if req.get("status") == status]
    if request_type:
        result = [req for req in result if req.get("type") == request_type]
    return result


def get_user_requests(user_id: int) -> list[Dict[str, Any]]:
    database = load_requests()
    requests = database.get("requests", [])
    return [
        req
        for req in requests
        if req.get("payload", {}).get("user_id") == user_id
    ]


def get_request_by_id(request_id: str) -> Optional[Dict[str, Any]]:
    database = load_requests()
    for entry in database.get("requests", []):
        if entry.get("id") == request_id:
            return entry
    return None


def get_request_by_plugin_id(plugin_id: str) -> Optional[Dict[str, Any]]:
    database = load_requests()
    for entry in database.get("requests", []):
        payload = entry.get("payload", {})
        plugin = payload.get("plugin", {})
        if plugin.get("id") == plugin_id:
            return entry
    return None


def update_request_status(request_id: str, status: str, comment: str | None = None) -> bool:
    database = load_requests()
    updated = False
    for entry in database.get("requests", []):
        if entry.get("id") == request_id:
            entry["status"] = status
            history = entry.setdefault("history", [])
            history.append(
                {
                    "status": status,
                    "comment": comment,
                    "changed_at": datetime.utcnow().isoformat(),
                }
            )
            updated = True
            break
    if updated:
        save_requests(database)
    return updated


def update_request_payload(request_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    database = load_requests()
    updated_entry = None
    for entry in database.get("requests", []):
        if entry.get("id") == request_id:
            entry.setdefault("payload", {}).update(fields)
            updated_entry = entry
            break
    if updated_entry:
        save_requests(database)
    return updated_entry
