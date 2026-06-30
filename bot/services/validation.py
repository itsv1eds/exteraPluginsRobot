from typing import Any, Dict, Optional, Tuple

from catalog import find_plugin_by_slug, is_external_plugin, list_published_plugins
from bot.services.publish import make_slug
from bot.services.versioning import compare_versions


def validate_new_submission(plugin: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    plugin_id = plugin.get("id", "")
    plugin_name = plugin.get("name", "")
    plugin_version = plugin.get("version", "")
    
    if not plugin_id or not plugin_name:
        return False, "missing_plugin_info"
    
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


