from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.cache import get_categories
from storage import DATA_DIR, load_icons, load_plugins, save_icons, save_plugins

TEST_PREFIX = "test-nav-"
DEFAULT_PLUGINS = 42
DEFAULT_ICONS = 24
CHANNEL_CHAT_ID = -1003200378257


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def _cleanup_entries(entries: List[Dict], prefix: str) -> Tuple[List[Dict], int]:
    kept = [item for item in entries if not str(item.get("slug", "")).startswith(prefix)]
    return kept, len(entries) - len(kept)


def _plugin_entry(index: int, category_key: str, now: datetime) -> Dict:
    slug = f"{TEST_PREFIX}plugin-{index:03d}"
    name = f"Navigation Test Plugin {index:03d}"
    has_ui = index % 2 == 0
    version = f"1.{index // 10}.{index % 10}"
    min_version = "12.0.0" if index % 3 == 0 else "11.5.0"
    author = f"@test_author_{index:03d}"
    author_channel = f"@testChannel{index:03d}"
    published_at = now - timedelta(days=index)
    message_id = 100000 + index

    return {
        "slug": slug,
        "status": "published",
        "category": category_key,
        "authors": {
            "ru": author,
            "en": author,
            "handles": [author],
        },
        "submitters": [
            {
                "user_id": 6000000000 + index,
                "username": f"tester_{index:03d}",
            }
        ],
        "ru": {
            "name": name,
            "description": f"Test plugin #{index} for pagination and back-navigation checks.",
            "usage": f"Open test #{index} and tap the action button.",
            "min_version": min_version,
            "version": version,
            "settings_label": "YES" if has_ui else "NO",
            "checked_on": f"12.4.{index % 10} (25.02.26)",
        },
        "en": {
            "name": name,
            "description": f"Test plugin #{index} for pagination and back-navigation checks.",
            "usage": f"Open test #{index} and tap the action button.",
            "min_version": min_version,
            "version": version,
            "settings_label": "YES" if has_ui else "NO",
            "checked_on": f"12.4.{index % 10} (25.02.26)",
        },
        "settings": {
            "has_ui": has_ui,
        },
        "requirements": {
            "min_version": min_version,
        },
        "channel_message": {
            "chat_id": CHANNEL_CHAT_ID,
            "message_id": message_id,
            "link": f"https://t.me/exteraPluginsTest/{message_id}",
        },
        "raw_blocks": {
            "ru": {
                "author": author,
                "author_channel": author_channel,
            },
            "en": {
                "author": author,
                "author_channel": author_channel,
            },
        },
        "published_at": _iso(published_at),
        "updated_at": _iso(published_at + timedelta(hours=1)),
    }


def _icon_entry(index: int, now: datetime) -> Dict:
    slug = f"{TEST_PREFIX}iconpack-{index:03d}"
    name = f"Navigation Test IconPack {index:03d}"
    version = f"2.{index // 10}.{index % 10}"
    author = f"@icon_author_{index:03d}"
    author_channel = f"@iconChannel{index:03d}"
    published_at = now - timedelta(days=index)
    message_id = 200000 + index
    count = 18 + (index % 17)

    return {
        "slug": slug,
        "status": "published",
        "category": None,
        "count": count,
        "authors": {
            "ru": author,
            "en": author,
            "handles": [author],
        },
        "submitters": [
            {
                "user_id": 7000000000 + index,
                "username": f"icon_tester_{index:03d}",
            }
        ],
        "ru": {
            "name": name,
            "description": None,
            "usage": None,
            "min_version": None,
            "version": version,
            "settings_label": "NO",
        },
        "en": {
            "name": name,
            "description": None,
            "usage": None,
            "min_version": None,
            "version": version,
            "settings_label": "NO",
        },
        "settings": {
            "has_ui": False,
        },
        "requirements": {
            "min_version": None,
        },
        "channel_message": {
            "chat_id": CHANNEL_CHAT_ID,
            "message_id": message_id,
            "link": f"https://t.me/exteraIconsTest/{message_id}",
        },
        "raw_blocks": {
            "ru": {
                "author": author,
                "author_channel": author_channel,
            },
            "en": {
                "author": author,
                "author_channel": author_channel,
            },
        },
        "published_at": _iso(published_at),
        "updated_at": _iso(published_at + timedelta(hours=1)),
        "file": {
            "file_id": f"TEST_ICON_FILE_{index:03d}",
            "file_name": f"{_slug(name)}.icons",
        },
    }


def seed(plugins_count: int, icons_count: int, prefix: str = TEST_PREFIX) -> None:
    now = datetime.now(timezone.utc)

    categories = [cat.get("key") for cat in get_categories() if cat.get("key")]
    if not categories:
        raise RuntimeError("No categories available in cache")

    plugins_db = load_plugins()
    existing_plugins = plugins_db.get("plugins", [])
    plugins_base, removed_plugins = _cleanup_entries(existing_plugins, prefix)

    seeded_plugins = [
        _plugin_entry(index=i + 1, category_key=categories[i % len(categories)], now=now)
        for i in range(plugins_count)
    ]

    plugins_db["plugins"] = plugins_base + seeded_plugins
    plugins_db["version"] = plugins_db.get("version", 1)
    plugins_db["updated_at"] = _iso(now)
    save_plugins(plugins_db)

    icons_db = load_icons()
    existing_icons = icons_db.get("iconpacks", [])
    icons_base, removed_icons = _cleanup_entries(existing_icons, prefix)

    seeded_icons = [_icon_entry(index=i + 1, now=now) for i in range(icons_count)]

    icons_db["iconpacks"] = icons_base + seeded_icons
    icons_db["version"] = icons_db.get("version", 1)
    icons_db["updated_at"] = _iso(now)
    save_icons(icons_db)

    print(f"DATA_DIR={DATA_DIR}")
    print(
        "Plugins: total={total} (kept={kept}, removed_old_test={removed}, added_test={added})".format(
            total=len(plugins_db["plugins"]),
            kept=len(plugins_base),
            removed=removed_plugins,
            added=len(seeded_plugins),
        )
    )
    print(
        "Icons: total={total} (kept={kept}, removed_old_test={removed}, added_test={added})".format(
            total=len(icons_db["iconpacks"]),
            kept=len(icons_base),
            removed=removed_icons,
            added=len(seeded_icons),
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed test catalog data")
    parser.add_argument("--plugins", type=int, default=DEFAULT_PLUGINS, help="number of test plugins to generate")
    parser.add_argument("--icons", type=int, default=DEFAULT_ICONS, help="number of test icon packs to generate")
    parser.add_argument("--prefix", type=str, default=TEST_PREFIX, help="slug prefix for generated test entries")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    seed(plugins_count=max(0, args.plugins), icons_count=max(0, args.icons), prefix=args.prefix)
