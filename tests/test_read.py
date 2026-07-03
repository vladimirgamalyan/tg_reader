"""Unit tests for the message reading logic.

No network: the Telethon client is injected into the logic functions and
replaced with AsyncMock (client coroutine methods) / MagicMock (plain
attributes and async iterators).
"""

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon import utils
from telethon.errors import FloodWaitError, TimedOutError
from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    MessageMediaDocument,
    PeerChannel,
    PeerChat,
    PeerUser,
    User,
)

from tg_reader import config, throttle
from tg_reader.read import (
    ChatNotFoundError,
    candidate_peers,
    fetch_messages,
    message_to_dict,
    resolve_chat,
    run_read,
)
from tg_reader.session import NotAuthorizedError
from tg_reader.throttle import RetryLaterError


def async_iter(items):
    """Wrap a list into an async iterator, as returned by iter_dialogs()."""

    async def generator():
        for item in items:
            yield item

    return generator()


def make_message(**overrides):
    defaults = {
        "id": 12345,
        "date": datetime(2026, 7, 3, 12, 34, 56, tzinfo=timezone.utc),
        "sender_id": 111222333,
        "sender": User(id=111222333, first_name="John", last_name="Doe"),
        "message": "message text",
        "reply_to_msg_id": None,
        "grouped_id": None,
        "media": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- ID normalization ---


def test_candidate_peers_marked_channel_id():
    assert candidate_peers(-1001234567890) == [PeerChannel(1234567890)]


def test_candidate_peers_marked_chat_id():
    assert candidate_peers(-123456) == [PeerChat(123456)]


def test_candidate_peers_raw_id_is_ambiguous():
    assert candidate_peers(123456) == [
        PeerUser(123456),
        PeerChannel(123456),
        PeerChat(123456),
    ]


# --- entity resolution ---


async def test_resolve_chat_uses_session_cache():
    client = AsyncMock()
    client.session.get_input_entity = MagicMock(return_value="input-entity")
    client.iter_dialogs = MagicMock()

    result = await resolve_chat(client, -1001234567890)

    assert result == "input-entity"
    client.session.get_input_entity.assert_called_once_with(PeerChannel(1234567890))
    client.iter_dialogs.assert_not_called()


async def test_resolve_chat_raw_id_tries_next_candidate_on_cache_miss():
    # PeerUser misses the cache, PeerChannel hits: no dialog iteration needed.
    client = AsyncMock()
    client.session.get_input_entity = MagicMock(
        side_effect=[ValueError, "channel-input-entity"]
    )
    client.iter_dialogs = MagicMock()

    result = await resolve_chat(client, 456)

    assert result == "channel-input-entity"
    assert client.session.get_input_entity.call_args_list == [
        ((PeerUser(456),),),
        ((PeerChannel(456),),),
    ]
    client.iter_dialogs.assert_not_called()


async def test_resolve_chat_falls_back_to_dialogs():
    client = AsyncMock()
    client.session.get_input_entity = MagicMock(side_effect=ValueError)
    dialogs = [
        SimpleNamespace(id=111, input_entity="other"),
        SimpleNamespace(id=-1001234567890, input_entity="channel-input-entity"),
    ]
    client.iter_dialogs = MagicMock(return_value=async_iter(dialogs))

    result = await resolve_chat(client, -1001234567890)

    assert result == "channel-input-entity"


async def test_resolve_chat_raw_id_matches_channel_dialog():
    client = AsyncMock()
    client.session.get_input_entity = MagicMock(side_effect=ValueError)
    marked_id = utils.get_peer_id(PeerChannel(456))
    dialogs = [SimpleNamespace(id=marked_id, input_entity="channel-input-entity")]
    client.iter_dialogs = MagicMock(return_value=async_iter(dialogs))

    result = await resolve_chat(client, 456)

    assert result == "channel-input-entity"


async def test_resolve_chat_not_found():
    client = AsyncMock()
    client.session.get_input_entity = MagicMock(side_effect=ValueError)
    dialogs = [SimpleNamespace(id=999, input_entity="other")]
    client.iter_dialogs = MagicMock(return_value=async_iter(dialogs))

    with pytest.raises(ChatNotFoundError):
        await resolve_chat(client, -1001234567890)


# --- message formatting ---


def test_message_to_dict_all_fields():
    assert message_to_dict(make_message()) == {
        "id": 12345,
        "date": "2026-07-03T12:34:56+00:00",
        "sender_id": 111222333,
        "sender_name": "John Doe",
        "text": "message text",
        "reply_to_msg_id": None,
        "grouped_id": None,
        "media": None,
    }


def test_message_to_dict_nulls():
    # Service messages / media without caption: no text, no known sender.
    message = make_message(date=None, sender_id=None, sender=None, message=None)

    assert message_to_dict(message) == {
        "id": 12345,
        "date": None,
        "sender_id": None,
        "sender_name": None,
        "text": None,
        "reply_to_msg_id": None,
        "grouped_id": None,
        "media": None,
    }


def test_message_to_dict_media_and_album():
    document = Document(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=datetime(2026, 7, 3, tzinfo=timezone.utc),
        mime_type="application/pdf",
        size=2048,
        dc_id=2,
        attributes=[DocumentAttributeFilename(file_name="report.pdf")],
    )
    message = make_message(
        grouped_id=777, media=MessageMediaDocument(document=document)
    )

    result = message_to_dict(message)

    assert result["grouped_id"] == 777
    assert result["media"] == {
        "type": "document",
        "filename": "report.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 2048,
    }


def test_message_to_dict_empty_text_is_null():
    assert message_to_dict(make_message(message=""))["text"] is None


def test_message_to_dict_reply():
    message = make_message(reply_to_msg_id=67890)

    assert message_to_dict(message)["reply_to_msg_id"] == 67890


# --- fetching ---


async def test_fetch_messages_returns_dicts_newest_first():
    client = AsyncMock()
    client.session.get_input_entity = MagicMock(return_value="input-entity")
    client.get_messages.return_value = [make_message(id=2), make_message(id=1)]

    result = await fetch_messages(client, -1001234567890, limit=2, offset_id=0)

    assert [message["id"] for message in result] == [2, 1]
    client.get_messages.assert_awaited_once_with("input-entity", limit=2, offset_id=0)


# --- run_read: flood protection wiring ---


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / config.CONFIG_FILENAME).write_text(
        json.dumps({"api_id": 1, "api_hash": "hash"}), encoding="utf-8"
    )
    return tmp_path


def make_connected_client(mocker):
    """Patch TelegramClient in the session module and return the client mock."""
    client = AsyncMock()
    client.session.get_input_entity = MagicMock(return_value="input-entity")
    client.is_user_authorized.return_value = True
    mocker.patch("tg_reader.session.TelegramClient", return_value=client)
    return client


async def test_run_read_returns_messages(config_dir, mocker):
    client = make_connected_client(mocker)
    client.get_messages.return_value = [make_message()]

    result = await run_read(-1001234567890, limit=1, offset_id=0)

    assert [message["id"] for message in result] == [12345]
    client.disconnect.assert_awaited_once()


async def test_run_read_flood_wait_persists_deadline(config_dir, mocker):
    client = make_connected_client(mocker)
    client.get_messages.side_effect = FloodWaitError(request=None, capture=77)

    with pytest.raises(RetryLaterError):
        await run_read(-1001234567890, limit=1, offset_id=0)

    state = json.loads(
        (config_dir / throttle.STATE_FILENAME).read_text(encoding="utf-8")
    )
    assert state["flood_until"] > time.time()
    client.disconnect.assert_awaited_once()


async def test_run_read_corrupt_config_asks_for_auth(config_dir, mocker):
    (config_dir / config.CONFIG_FILENAME).write_text("{not json", encoding="utf-8")
    client_class = mocker.patch("tg_reader.session.TelegramClient")

    with pytest.raises(NotAuthorizedError):
        await run_read(-1001234567890, limit=1, offset_id=0)

    client_class.assert_not_called()


async def test_run_read_network_error_maps_to_retry_later(config_dir, mocker):
    client = make_connected_client(mocker)
    client.connect.side_effect = ConnectionError("Connection to Telegram failed")

    with pytest.raises(RetryLaterError, match="cannot reach Telegram"):
        await run_read(-1001234567890, limit=1, offset_id=0)

    client.disconnect.assert_awaited_once()


async def test_run_read_rpc_timeout_maps_to_retry_later(config_dir, mocker):
    client = make_connected_client(mocker)
    client.get_messages.side_effect = TimedOutError(
        request=None, message="timed out", code=500
    )

    with pytest.raises(RetryLaterError, match="cannot reach Telegram"):
        await run_read(-1001234567890, limit=1, offset_id=0)

    client.disconnect.assert_awaited_once()


async def test_run_read_refuses_while_flood_wait_active(config_dir, mocker):
    (config_dir / throttle.STATE_FILENAME).write_text(
        json.dumps({"flood_until": time.time() + 100}), encoding="utf-8"
    )
    client_class = mocker.patch("tg_reader.session.TelegramClient")

    with pytest.raises(RetryLaterError):
        await run_read(-1001234567890, limit=1, offset_id=0)

    client_class.assert_not_called()
