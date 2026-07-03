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


def ensure_config_dir() -> Path:
    """Create the per-user config directory and make it private on POSIX."""
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(directory, 0o700)
    return directory


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
    if not isinstance(data, dict):
        return None
    api_id = data.get("api_id")
    api_hash = data.get("api_hash")
    if not isinstance(api_id, int) or isinstance(api_id, bool) or api_id <= 0:
        return None
    if not isinstance(api_hash, str) or not api_hash.strip():
        return None
    return {"api_id": api_id, "api_hash": api_hash.strip()}


def save_config(api_id: int, api_hash: str) -> None:
    ensure_config_dir()
    data = {"api_id": api_id, "api_hash": api_hash}
    config_path().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
