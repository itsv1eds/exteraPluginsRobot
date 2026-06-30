
from __future__ import annotations

import re
from typing import Optional, Tuple

try:
    from packaging.version import InvalidVersion, Version
except Exception:
    Version = None
    InvalidVersion = Exception

_OPERATORS = (">=", "<=", "==", "!=", "~=", ">", "<", "=")
_OP_RE = re.compile(r"^\s*(>=|<=|==|!=|~=|>|<|=)\s*(.*)$")
_VERSION_RE = re.compile(r"\d+(?:\.\d+)*")


def split_operator(spec: object) -> Tuple[str, str]:
    text = str(spec or "").strip()
    match = _OP_RE.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    return "", text


def normalize_version(value: object) -> str:
    _, rest = split_operator(value)
    match = _VERSION_RE.search(rest)
    return match.group(0) if match else ""


def _key(value: object):
    norm = normalize_version(value)
    if not norm:
        return (0,)
    if Version is not None:
        try:
            return Version(norm)
        except InvalidVersion:
            pass
    return tuple(int(part) for part in norm.split("."))


def compare_versions(left: object, right: object) -> int:
    a, b = _key(left), _key(right)
    try:
        if a < b:
            return -1
        if a > b:
            return 1
        return 0
    except TypeError:
        sa, sb = normalize_version(left), normalize_version(right)
        ta = tuple(int(p) for p in sa.split(".")) if sa else (0,)
        tb = tuple(int(p) for p in sb.split(".")) if sb else (0,)
        return (ta > tb) - (ta < tb)


def is_valid_version(value: object) -> bool:
    return bool(normalize_version(value))


def satisfies(candidate: object, spec: object, *, default_operator: str = ">=") -> bool:
    operator, _ = split_operator(spec)
    operator = operator or default_operator
    if operator == "=":
        operator = "=="
    cmp = compare_versions(candidate, spec)
    if operator == ">=":
        return cmp >= 0
    if operator == ">":
        return cmp > 0
    if operator == "<=":
        return cmp <= 0
    if operator == "<":
        return cmp < 0
    if operator == "==":
        return cmp == 0
    if operator == "!=":
        return cmp != 0
    if operator == "~=":
        return cmp >= 0 and _key(candidate) >= _key(spec)
    return cmp >= 0


def is_compatible(client_version: object, min_version: object) -> bool:
    if not is_valid_version(min_version):
        return True
    if not is_valid_version(client_version):
        return True
    return satisfies(client_version, min_version, default_operator=">=")
