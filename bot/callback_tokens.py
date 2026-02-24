import hashlib
from typing import Dict

_MAX_CALLBACK_SLUG = 40
_slug_tokens: Dict[str, str] = {}


def encode_slug(slug: str) -> str:
    if not slug:
        return slug
    if len(slug) <= _MAX_CALLBACK_SLUG:
        return slug
    token = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:10]
    key = f"t{token}"
    _slug_tokens[key] = slug
    return key


def decode_slug(value: str) -> str:
    if not value:
        return value
    return _slug_tokens.get(value, value)
