import hashlib
import re
from typing import Dict

from storage import load_icons, load_plugins

_MAX_CALLBACK_SLUG = 40
_slug_tokens: Dict[str, str] = {}
_TOKEN_RE = re.compile(r"^t(?:[0-9a-f]{10}|[0-9a-f]{16})$")


def _token_for(slug: str, digest_size: int = 16) -> str:
    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:digest_size]
    return f"t{digest}"


def encode_slug(slug: str) -> str:
    if not slug:
        return slug
    try:
        slug_bytes_len = len(slug.encode("utf-8"))
    except Exception:
        slug_bytes_len = _MAX_CALLBACK_SLUG + 1

    if slug_bytes_len <= _MAX_CALLBACK_SLUG:
        return slug
    key = _token_for(slug)
    _slug_tokens[key] = slug
    return key


def _stored_slugs() -> list[str]:
    slugs: list[str] = []
    for loader, collection_key in (
        (load_plugins, "plugins"),
        (load_icons, "iconpacks"),
    ):
        try:
            data = loader()
        except Exception:
            continue
        items = data.get(collection_key, []) if isinstance(data, dict) else []
        for item in items if isinstance(items, list) else []:
            slug = item.get("slug") if isinstance(item, dict) else None
            if isinstance(slug, str) and slug:
                slugs.append(slug)
    return slugs


def decode_slug(value: str) -> str:
    if not value:
        return value

    cached = _slug_tokens.get(value)
    if cached:
        return cached
    if not _TOKEN_RE.fullmatch(value):
        return value

    slugs = _stored_slugs()
    # A real slug which happens to look like a token must not be shadowed.
    if value in slugs:
        return value

    for slug in slugs:
        if value in (_token_for(slug), _token_for(slug, digest_size=10)):
            _slug_tokens[value] = slug
            return slug
    return value
