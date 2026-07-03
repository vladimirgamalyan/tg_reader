"""User configuration: config directory resolution, config.json load/save."""

import json
import os
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
    """Return the stored credentials, or None if absent or unusable.

    A corrupt or incomplete config file is treated as missing: 'read' and
    'download' then direct the user to 'tg-reader auth', which re-prompts
    for the credentials and rewrites the file.
    """
    path = config_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "api_id" not in data or "api_hash" not in data:
        return None
    return data


def save_config(api_id: int, api_hash: str) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # The directory holds secrets (api_hash, the Telethon session file that
    # grants full account access): keep it private to the user on POSIX.
    # Effectively a no-op on Windows.
    os.chmod(directory, 0o700)
    data = {"api_id": api_id, "api_hash": api_hash}
    config_path().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
