"""Unit tests for the authorization flow wiring.

No network and no real input: the Telethon client and the lock are mocked.
The interactive login flow itself is not tested (it is Telethon's code).
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyDuplicatedError,
    FloodWaitError,
    ServerError,
)
from telethon.tl.types import User

from tg_reader import config, throttle
from tg_reader.auth import (
    _load_or_prompt_credentials,
    _prompt_api_hash,
    _prompt_api_id,
    run_auth,
)
from tg_reader.errors import PermanentError
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


def test_prompt_api_hash_rejects_empty(capsys, mocker):
    # An accidental Enter (or whitespace) would only fail later with an
    # obscure server-side error; the prompt must re-ask instead.
    mocker.patch("tg_reader.auth.getpass.getpass", side_effect=["", "   ", " hash "])

    assert _prompt_api_hash() == "hash"

    assert capsys.readouterr().out.count("try again") == 2


def test_prompt_api_id_rejects_non_positive(monkeypatch, capsys):
    # 0 and negatives would only fail later with an obscure Telethon or
    # server-side error; the prompt must re-ask instead.
    answers = iter(["0", "-5", "abc", "123"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert _prompt_api_id() == 123

    assert capsys.readouterr().out.count("try again") == 3


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


async def test_run_auth_rpc_server_error_maps_to_retry_later(config_dir, mocker):
    client = make_client(mocker)
    client.get_me.side_effect = ServerError(
        request=None, message="server error", code=500
    )
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


async def test_run_auth_saves_prompted_credentials_even_if_get_me_fails(
    tmp_path, monkeypatch, mocker
):
    # After a successful login the session file is already authorized: a
    # network failure in the cosmetic get_me() must not leave the prompted
    # credentials unsaved, or the next auth run would ask for them again.
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "123")
    mocker.patch("tg_reader.auth.getpass.getpass", return_value=" hash ")
    client = make_client(mocker)
    client.is_user_authorized.return_value = False
    client.get_me.side_effect = ServerError(
        request=None, message="server error", code=500
    )
    lock = MagicMock()
    mocker.patch("tg_reader.auth.throttle.acquire_lock", return_value=lock)

    with pytest.raises(RetryLaterError):
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


async def test_run_auth_dead_session_is_deleted(config_dir, mocker):
    # AUTH_KEY_DUPLICATED at connect time: the stored session is dead and
    # would fail the same way on every connect, so auth must delete the
    # file (after the client releases it) and explain what happened.
    client = make_client(mocker)
    client.connect.side_effect = AuthKeyDuplicatedError(request=None)
    session_file = config_dir / config.SESSION_FILENAME
    session_file.write_text("dead", encoding="utf-8")

    with pytest.raises(PermanentError, match="no longer valid"):
        await run_auth()

    assert not session_file.exists()
    client.disconnect.assert_awaited_once()


async def test_run_auth_disconnect_failure_does_not_mask_error(config_dir, mocker):
    # Same guard as in session.py: a failing disconnect must not replace
    # the in-flight error, or a transient failure would be reported as a
    # permanent one.
    client = make_client(mocker)
    client.connect.side_effect = ConnectionError("boom")
    client.disconnect.side_effect = OSError("socket already closed")
    lock = MagicMock()
    mocker.patch("tg_reader.auth.throttle.acquire_lock", return_value=lock)

    with pytest.raises(RetryLaterError, match="cannot reach Telegram"):
        await run_auth()

    lock.release.assert_called_once()


async def test_run_auth_disconnect_eof_failure_does_not_mask_error(config_dir, mocker):
    # asyncio.IncompleteReadError is an EOFError, not an OSError: the
    # disconnect guard must swallow it too, or it would replace the
    # in-flight error and be blamed on stdin by the CLI.
    client = make_client(mocker)
    client.connect.side_effect = ConnectionError("boom")
    client.disconnect.side_effect = asyncio.IncompleteReadError(b"", 4)
    lock = MagicMock()
    mocker.patch("tg_reader.auth.throttle.acquire_lock", return_value=lock)

    with pytest.raises(RetryLaterError, match="cannot reach Telegram"):
        await run_auth()

    lock.release.assert_called_once()


async def test_run_auth_rejected_stored_credentials_are_deleted(config_dir, mocker):
    # Stored api_id/api_hash rejected by Telegram would fail the same way
    # on every run while auth keeps loading them instead of re-prompting:
    # a dead end unless the stored pair is removed.
    client = make_client(mocker)
    client.connect.side_effect = ApiIdInvalidError(request=None)

    with pytest.raises(PermanentError, match="has been deleted"):
        await run_auth()

    assert config.load_config() is None
    client.disconnect.assert_awaited_once()


async def test_run_auth_rejected_prompted_credentials_explain_the_fix(
    tmp_path, monkeypatch, mocker
):
    # Credentials mistyped at the prompt are never saved; the error must
    # say to re-run auth and re-enter them, not mention any stored file.
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "123")
    mocker.patch("tg_reader.auth.getpass.getpass", return_value="hash")
    client = make_client(mocker)
    client.connect.side_effect = ApiIdInvalidError(request=None)
    lock = MagicMock()
    mocker.patch("tg_reader.auth.throttle.acquire_lock", return_value=lock)

    with pytest.raises(PermanentError, match="re-enter"):
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
