import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"

DATABASE_PLUGINS_PATH = DATA_DIR / "databaseplugins.json"
DATABASE_ICONS_PATH = DATA_DIR / "databaseicons.json"
DATABASE_REQUESTS_PATH = DATA_DIR / "databaserequests.json"
DATABASE_USERS_PATH = DATA_DIR / "databaseusers.json"


class StorageError(RuntimeError):
    """Raised when the storage layer hits an unrecoverable error."""


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise StorageError(f"Config not found: {CONFIG_PATH}")
    return _read_json(CONFIG_PATH)


def _load_json(path: Path, kind: str) -> Dict[str, Any]:
    if not path.exists():
        raise StorageError(f"{kind} not found: {path}")
    return _read_json(path)


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    data.setdefault("updated_at", datetime.utcnow().isoformat())
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_plugins() -> Dict[str, Any]:
    _ensure_data_dir()
    return _load_json(DATABASE_PLUGINS_PATH, "Plugins DB")


def save_plugins(data: Dict[str, Any]) -> None:
    _ensure_data_dir()
    _save_json(DATABASE_PLUGINS_PATH, data)


def load_icons() -> Dict[str, Any]:
    _ensure_data_dir()
    return _load_json(DATABASE_ICONS_PATH, "Icons DB")


def save_icons(data: Dict[str, Any]) -> None:
    _ensure_data_dir()
    _save_json(DATABASE_ICONS_PATH, data)


def load_requests() -> Dict[str, Any]:
    _ensure_data_dir()
    return _load_json(DATABASE_REQUESTS_PATH, "Requests DB")


def save_requests(data: Dict[str, Any]) -> None:
    _ensure_data_dir()
    _save_json(DATABASE_REQUESTS_PATH, data)


def load_users() -> Dict[str, Any]:
    _ensure_data_dir()
    return _load_json(DATABASE_USERS_PATH, "Users DB")


def save_users(data: Dict[str, Any]) -> None:
    _ensure_data_dir()
    _save_json(DATABASE_USERS_PATH, data)


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)
