"""Unit tests for the authorization flow wiring.

No network and no real input: the Telethon client and the lock are mocked.
The interactive login flow itself is not tested (it is Telethon's code).
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.tl.types import User

from tg_reader import config
from tg_reader.auth import run_auth


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

    with pytest.raises(ConnectionError):
        await run_auth()

    lock.release.assert_called_once()
