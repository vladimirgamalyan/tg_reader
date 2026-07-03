"""Unit tests for the authorization flow wiring.

No network and no real input: the Telethon client and the lock are mocked.
The interactive login flow itself is not tested (it is Telethon's code).
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import FloodWaitError
from telethon.tl.types import User

from tg_reader import config, throttle
from tg_reader.auth import _load_or_prompt_credentials, run_auth
from tg_reader.throttle import RetryLaterError


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / config.CONFIG_FILENAME).write_text(
        json.dumps({"api_id": 1, "api_hash": "hash"}), encoding="utf-8"
    )
    return tmp_path


def make_client(mocker):
    client = AsyncMock()
    client.is_user_authorized.return_value = True
    client.get_me.return_value = User(id=42, first_name="John")
    mocker.patch("tg_reader.auth.TelegramClient", return_value=client)
    return client


def test_load_or_prompt_credentials_hides_api_hash(
    tmp_path, monkeypatch, capsys, mocker
):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "123")
    getpass = mocker.patch("tg_reader.auth.getpass.getpass", return_value=" hash ")

    assert _load_or_prompt_credentials() == (123, "hash", True)

    getpass.assert_called_once_with("api_hash: ")
    output = capsys.readouterr().out
    assert "api_hash input will not be displayed while you type." in output
    assert config.load_config() is None


def test_load_or_prompt_credentials_reports_stored_credentials(config_dir):
    assert _load_or_prompt_credentials() == (1, "hash", False)


async def test_run_auth_holds_lock_around_the_session(config_dir, mocker):
    client = make_client(mocker)
    lock = MagicMock()
    acquire_lock = mocker.patch(
        "tg_reader.auth.throttle.acquire_lock", return_value=lock
    )

    await run_auth()

    acquire_lock.assert_called_once()
    lock.release.assert_called_once()
    client.disconnect.assert_awaited_once()


async def test_run_auth_releases_lock_on_failure(config_dir, mocker):
    client = make_client(mocker)
    client.connect.side_effect = ConnectionError("boom")
    lock = MagicMock()
    mocker.patch("tg_reader.auth.throttle.acquire_lock", return_value=lock)

    with pytest.raises(RetryLaterError, match="cannot reach Telegram"):
        await run_auth()

    lock.release.assert_called_once()
    client.disconnect.assert_awaited_once()


async def test_run_auth_saves_prompted_credentials_after_success(
    tmp_path, monkeypatch, mocker
):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "123")
    mocker.patch("tg_reader.auth.getpass.getpass", return_value=" hash ")
    client = make_client(mocker)
    client.is_user_authorized.return_value = False
    lock = MagicMock()
    mocker.patch("tg_reader.auth.throttle.acquire_lock", return_value=lock)

    await run_auth()

    assert config.load_config() == {"api_id": 123, "api_hash": "hash"}
    client.start.assert_awaited_once()


async def test_run_auth_does_not_save_prompted_credentials_on_login_failure(
    tmp_path, monkeypatch, mocker
):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "123")
    mocker.patch("tg_reader.auth.getpass.getpass", return_value=" hash ")
    client = make_client(mocker)
    client.is_user_authorized.return_value = False
    client.start.side_effect = RuntimeError("bad login")
    lock = MagicMock()
    mocker.patch("tg_reader.auth.throttle.acquire_lock", return_value=lock)

    with pytest.raises(RuntimeError, match="bad login"):
        await run_auth()

    assert config.load_config() is None


async def test_run_auth_refuses_while_flood_wait_active(config_dir, mocker):
    (config_dir / throttle.STATE_FILENAME).write_text(
        json.dumps({"flood_until": time.time() + 100}), encoding="utf-8"
    )
    client_class = mocker.patch("tg_reader.auth.TelegramClient")

    with pytest.raises(RetryLaterError):
        await run_auth()

    client_class.assert_not_called()


async def test_run_auth_flood_wait_persists_deadline(config_dir, mocker):
    client = make_client(mocker)
    client.is_user_authorized.return_value = False
    client.start.side_effect = FloodWaitError(request=None, capture=77)

    with pytest.raises(RetryLaterError):
        await run_auth()

    state = json.loads(
        (config_dir / throttle.STATE_FILENAME).read_text(encoding="utf-8")
    )
    assert state["flood_until"] > time.time()
    client.disconnect.assert_awaited_once()
