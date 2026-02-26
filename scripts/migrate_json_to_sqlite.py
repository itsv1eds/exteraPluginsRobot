from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DOC_PLUGINS = "plugins"
DOC_ICONS = "icons"
DOC_REQUESTS = "requests"
DOC_USERS = "users"
DOC_SUBSCRIPTIONS = "subscriptions"
DOC_UPDATED = "updated"
DOC_SNOWFLAKE = "snowflake"


@dataclass(frozen=True)
class DocSpec:
    key: str
    file_name: str
    default: Dict[str, Any]


DOC_SPECS = [
    DocSpec(DOC_PLUGINS, "databaseplugins.json", {"plugins": []}),
    DocSpec(DOC_ICONS, "databaseicons.json", {"iconpacks": []}),
    DocSpec(DOC_REQUESTS, "databaserequests.json", {"requests": []}),
    DocSpec(DOC_SUBSCRIPTIONS, "databasesubscriptions.json", {"subscriptions": {}}),
    DocSpec(DOC_UPDATED, "databaseupdated.json", {"items": [], "seeded": False}),
    DocSpec(DOC_SNOWFLAKE, "snowflake_db.json", {}),
]


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return dict(default)


def _load_docs(source_dir: Path) -> Dict[str, Dict[str, Any]]:
    docs: Dict[str, Dict[str, Any]] = {}
    for spec in DOC_SPECS:
        docs[spec.key] = _read_json(source_dir / spec.file_name, spec.default)

    users_path = source_dir / "users.json"
    if not users_path.exists():
        users_path = source_dir / "databaseusers.json"
    docs[DOC_USERS] = _read_json(users_path, {"users": {}})
    return docs


def _rewrite_request_file_paths(docs: Dict[str, Dict[str, Any]], uploads_dir: Path) -> tuple[int, int]:
    requests = docs.get(DOC_REQUESTS, {}).get("requests", [])
    if not isinstance(requests, list):
        return 0, 0

    rewritten = 0
    missing = 0
    for entry in requests:
        payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
        if not isinstance(payload, dict):
            continue

        for content_key in ("plugin", "icon"):
            content = payload.get(content_key, {})
            if not isinstance(content, dict):
                continue

            file_path = content.get("file_path")
            if not isinstance(file_path, str) or not file_path.strip():
                continue

            new_path = (uploads_dir / Path(file_path).name).resolve()
            content["file_path"] = str(new_path)
            rewritten += 1
            if not new_path.exists():
                missing += 1
    return rewritten, missing


def _count_rows(docs: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    subs = docs.get(DOC_SUBSCRIPTIONS, {}).get("subscriptions", {})
    subs_rows = 0
    if isinstance(subs, dict):
        for value in subs.values():
            subs_rows += len(value) if isinstance(value, list) else 0

    snowflake = docs.get(DOC_SNOWFLAKE, {})
    snowflake_chats = 0
    if isinstance(snowflake, dict):
        snowflake_chats = len([k for k in snowflake.keys() if str(k).lstrip("-").isdigit()])

    return {
        "plugins": len(docs.get(DOC_PLUGINS, {}).get("plugins", [])),
        "iconpacks": len(docs.get(DOC_ICONS, {}).get("iconpacks", [])),
        "requests": len(docs.get(DOC_REQUESTS, {}).get("requests", [])),
        "users": len(docs.get(DOC_USERS, {}).get("users", {})),
        "subscription_users": len(subs) if isinstance(subs, dict) else 0,
        "subscription_rows": subs_rows,
        "updated_items": len(docs.get(DOC_UPDATED, {}).get("items", [])),
        "snowflake_chats": snowflake_chats,
    }


def _print_counts(prefix: str, counts: Dict[str, int]) -> None:
    print(prefix)
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy JSON storage to SQLite")
    parser.add_argument(
        "--source-dir",
        default="shit/data",
        help="Directory with legacy JSON files (default: shit/data)",
    )
    parser.add_argument(
        "--sqlite-path",
        default="data/data/storage.sqlite3",
        help="Target SQLite file (default: data/data/storage.sqlite3)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with non-zero code when post-migration counts mismatch",
    )
    parser.add_argument(
        "--uploads-dir",
        default="",
        help="Optional uploads dir to rewrite request payload file_path values",
    )
    parser.add_argument(
        "--keep-kv-store",
        action="store_true",
        help="Keep legacy kv_store table after successful migration",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_dir = Path(args.source_dir)
    sqlite_path = Path(args.sqlite_path)

    if not source_dir.exists():
        print(f"Source directory does not exist: {source_dir}")
        return 2

    docs = _load_docs(source_dir)
    if args.uploads_dir:
        uploads_dir = Path(args.uploads_dir)
        rewritten, missing = _rewrite_request_file_paths(docs, uploads_dir)
        print(f"Rewritten request file_path values: {rewritten}")
        print(f"Rewritten file_path missing on disk: {missing}")

    expected = _count_rows(docs)
    _print_counts("Source counts:", expected)

    os.environ["SQLITE_PATH"] = str(sqlite_path)
    os.environ["DATA_DIR"] = str(sqlite_path.parent)

    from storage import (
        flush_all,
        load_icons,
        load_plugins,
        load_requests,
        load_snowflake,
        load_subscriptions,
        load_updated,
        load_users,
        save_icons,
        save_plugins,
        save_requests,
        save_snowflake,
        save_subscriptions,
        save_updated,
        save_users,
    )

    save_plugins(docs.get(DOC_PLUGINS, {"plugins": []}))
    save_icons(docs.get(DOC_ICONS, {"iconpacks": []}))
    save_requests(docs.get(DOC_REQUESTS, {"requests": []}))
    save_users(docs.get(DOC_USERS, {"users": {}}))
    save_subscriptions(docs.get(DOC_SUBSCRIPTIONS, {"subscriptions": {}}))
    save_updated(docs.get(DOC_UPDATED, {"items": [], "seeded": False}))
    save_snowflake(docs.get(DOC_SNOWFLAKE, {}))
    asyncio.run(flush_all())

    actual_docs = {
        DOC_PLUGINS: load_plugins(),
        DOC_ICONS: load_icons(),
        DOC_REQUESTS: load_requests(),
        DOC_USERS: load_users(),
        DOC_SUBSCRIPTIONS: load_subscriptions(),
        DOC_UPDATED: load_updated(),
        DOC_SNOWFLAKE: load_snowflake(),
    }

    actual = _count_rows(actual_docs)
    _print_counts("SQLite counts:", actual)

    if expected != actual:
        print("WARNING: count mismatch detected")
        for key in sorted(set(expected) | set(actual)):
            if expected.get(key) != actual.get(key):
                print(f"  {key}: source={expected.get(key)} sqlite={actual.get(key)}")
        if args.strict:
            return 1
    else:
        print("Migration check passed")

    if not args.keep_kv_store:
        conn = sqlite3.connect(sqlite_path)
        try:
            conn.execute("DROP TABLE IF EXISTS kv_store")
            conn.commit()
        finally:
            conn.close()
        print("Dropped legacy table: kv_store")

    print(f"SQLite path: {sqlite_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
