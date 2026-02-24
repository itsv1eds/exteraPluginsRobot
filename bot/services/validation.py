from typing import Any, Dict, Optional, Tuple
from packaging import version as pkg_version

from catalog import find_icon_by_slug, find_plugin_by_slug, list_published_plugins
from bot.services.publish import make_slug


def validate_new_submission(plugin: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    plugin_id = plugin.get("id", "")
    plugin_name = plugin.get("name", "")
    plugin_version = plugin.get("version", "")
    
    if not plugin_id or not plugin_name:
        return False, "missing_plugin_info"
    
    slug = make_slug(plugin_name)
    existing = find_plugin_by_slug(slug)
    
    if existing:
        return False, "plugin_already_exists"
    
    all_plugins = list_published_plugins()
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
    
    try:
        new_v = pkg_version.parse(new_version)
        old_v = pkg_version.parse(old_version)
        
        if new_v <= old_v:
            return False, "version_not_higher"
    except Exception:
        if new_version == old_version:
            return False, "version_same"
    
    return True, None


def check_duplicate_pending(plugin_id: str, plugin_name: str) -> Tuple[bool, Optional[str]]:
    from request_store import get_requests
    
    pending = get_requests(status="pending") + get_requests(status="draft")
    
    slug = make_slug(plugin_name)
    
    for req in pending:
        payload = req.get("payload", {})
        req_plugin = payload.get("plugin", {})
        
        req_id = req_plugin.get("id", "")
        req_name = req_plugin.get("name", "")
        req_slug = make_slug(req_name)
        
        if req_id.lower() == plugin_id.lower():
            return True, req.get("id")
        
        if req_slug == slug:
            return True, req.get("id")
    
    return False, None


def validate_icon_submission(icon: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    icon_id = icon.get("id", "")
    icon_name = icon.get("name", "")
    if not icon_id or not icon_name:
        return False, "missing_icon_info"

    slug = make_slug(icon_name)
    existing = find_icon_by_slug(slug)
    if existing:
        return False, "icon_already_exists"

    return True, None


def check_duplicate_icon_pending(icon_id: str, icon_name: str) -> Tuple[bool, Optional[str]]:
    from request_store import get_requests

    pending = get_requests(status="pending") + get_requests(status="draft")
    slug = make_slug(icon_name)

    for req in pending:
        payload = req.get("payload", {})
        req_icon = payload.get("icon", {})
        req_id = req_icon.get("id", "")
        req_name = req_icon.get("name", "")
        req_slug = make_slug(req_name)

        if req_id and icon_id and req_id.lower() == icon_id.lower():
            return True, req.get("id")
        if req_slug == slug:
            return True, req.get("id")

    return False, None