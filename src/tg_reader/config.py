"""User configuration: config directory resolution, config.json load/save."""

import json
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "tg-reader"
CONFIG_FILENAME = "config.json"
SESSION_FILENAME = "tg_reader.session"


def config_dir() -> Path:
    """Per-user configuration directory (independent of the working directory)."""
    return Path(user_config_dir(APP_NAME, appauthor=False, roaming=True))


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def session_path() -> Path:
    return config_dir() / SESSION_FILENAME


def load_config() -> dict | None:
    """Return the stored credentials or None if the config does not exist."""
    path = config_path()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(api_id: int, api_hash: str) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    data = {"api_id": api_id, "api_hash": api_hash}
    config_path().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
