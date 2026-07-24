from typing import Any, Dict, Optional, Tuple

from catalog import find_plugin_by_slug, is_external_plugin, list_published_plugins
from bot.services.publish import make_slug
from bot.services.versioning import compare_versions, meets_min_supported

REQUIRED_DRAFT_FIELDS = (
    "description_ru",
    "description_en",
    "usage_ru",
    "usage_en",
    "category_key",
)

_FINGERPRINT_KEYS = (
    "description_ru",
    "description_en",
    "usage_ru",
    "usage_en",
    "category_key",
    "changelog",
    "changelog_ru",
    "changelog_en",
)


def missing_draft_fields(payload: Dict[str, Any]) -> list[str]:
    missing = []
    for key in REQUIRED_DRAFT_FIELDS:
        if not str(payload.get(key) or "").strip():
            missing.append(key)
    plugin = payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}
    for key in ("name", "version", "min_version"):
        if not str(plugin.get(key) or "").strip():
            missing.append(f"plugin.{key}")
    return missing


def submission_fingerprint(payload: Dict[str, Any]) -> str:
    import hashlib
    import json

    plugin = payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}
    parts: Dict[str, Any] = {k: str(payload.get(k) or "").strip() for k in _FINGERPRINT_KEYS}
    for k in ("name", "version", "min_version", "description", "author", "file_id"):
        parts[f"plugin.{k}"] = str(plugin.get(k) or "").strip()
    parts["plugin.has_ui_settings"] = bool(plugin.get("has_ui_settings"))
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def validate_new_submission(plugin: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    plugin_id = plugin.get("id", "")
    plugin_name = plugin.get("name", "")
    plugin_version = plugin.get("version", "")

    if not plugin_id or not plugin_name:
        return False, "missing_plugin_info"

    if not meets_min_supported(plugin.get("min_version")):
        return False, "min_version_too_low"

    slug = make_slug(plugin_name)
    existing = find_plugin_by_slug(slug)
    
    if existing and not is_external_plugin(existing):
        return False, "plugin_already_exists"
    
    all_plugins = list_published_plugins(source_filter="official")
    for p in all_plugins:
        p_id = p.get("ru", {}).get("id") or p.get("slug", "")
        if p_id.lower() == plugin_id.lower():
            return False, "plugin_id_exists"
    
    return True, None


def validate_update_submission(
    plugin: Dict[str, Any],
    old_plugin: Dict[str, Any],
) -> Tuple[bool, Optional[str]]:
    new_version = plugin.get("version", "")
    old_version = old_plugin.get("ru", {}).get("version") or old_plugin.get("en", {}).get("version") or ""
    
    if not new_version:
        return False, "missing_version"

    if not meets_min_supported(plugin.get("min_version")):
        return False, "min_version_too_low"

    if not old_version:
        return True, None

    cmp = compare_versions(new_version, old_version)
    if cmp == 0:
        return False, "version_same"
    if cmp < 0:
        return False, "version_lower"

    return True, None


def check_duplicate_pending(
    plugin_id: str,
    plugin_name: str,
    exclude_user_id: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    from request_store import get_requests

    pending = get_requests(status="pending") + get_requests(status="draft")

    slug = make_slug(plugin_name)
    target_id = (plugin_id or "").lower()

    for req in pending:
        payload = req.get("payload", {})

        if (
            req.get("status") == "draft"
            and exclude_user_id is not None
            and payload.get("user_id") == exclude_user_id
        ):
            continue

        req_plugin = payload.get("plugin", {})
        req_id = req_plugin.get("id", "")
        req_name = req_plugin.get("name", "")
        req_slug = make_slug(req_name)

        if req_id and req_id.lower() == target_id:
            return True, req.get("id")

        if req_slug == slug:
            return True, req.get("id")

    return False, None


