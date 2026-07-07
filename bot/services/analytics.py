
from __future__ import annotations

from typing import List, Tuple

from storage import load_stats, save_stats


def record_plugin_open(slug: str) -> None:
    slug = str(slug or "").strip()
    if not slug:
        return
    doc = load_stats()
    opens = doc.get("plugin_opens")
    if not isinstance(opens, dict):
        opens = {}
    opens[slug] = int(opens.get(slug, 0) or 0) + 1
    doc["plugin_opens"] = opens
    doc["total_opens"] = int(doc.get("total_opens", 0) or 0) + 1
    save_stats(doc)


def top_plugin_opens(limit: int = 10) -> List[Tuple[str, int]]:
    opens = load_stats().get("plugin_opens") or {}
    items = [(str(k), int(v or 0)) for k, v in opens.items() if int(v or 0) > 0]
    items.sort(key=lambda kv: -kv[1])
    return items[:limit]


def total_plugin_opens() -> int:
    return int(load_stats().get("total_opens", 0) or 0)
