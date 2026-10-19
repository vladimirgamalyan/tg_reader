"""Unit tests for the local archive sync.

No network: the Telethon client is injected and replaced with AsyncMock,
same pattern as tests/test_read.py; the config directory is redirected to
a temporary path.
"""

import json
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import FloodWaitError
from telethon.tl.types import InputPeerChannel, User

from tg_reader import config, throttle
from tg_reader.sync import SyncError, archive_path, run_sync, sync_chat
from tg_reader.throttle import RetryLaterError

CHAT_ID = -1001234567890


def make_message(msg_id, text="message text"):
    return SimpleNamespace(
        id=msg_id,
        date=datetime(2026, 9, 1, 12, 0, msg_id % 60, tzinfo=timezone.utc),
        edit_date=None,
        sender_id=111222333,
        sender=User(id=111222333, first_name="John"),
        message=text,
        entities=None,
        reply_to=None,
        reply_to_msg_id=None,
        action=None,
        grouped_id=None,
        media=None,
    )


def messages_iter(messages, error=None):
    """Async iterator like iter_messages(); optionally fails after the items."""

    async def generator():
        for message in messages:
            yield message
        if error is not None:
            raise error

    return generator()


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / config.CONFIG_FILENAME).write_text(
        json.dumps({"api_id": 1, "api_hash": "hash"}), encoding="utf-8"
    )
    return tmp_path


def make_client(messages=(), error=None):
    client = AsyncMock()
    client.session.get_input_entity = MagicMock(
        return_value=InputPeerChannel(1234567890, access_hash=0)
    )
    client.get_peer_id.return_value = CHAT_ID
    client.iter_messages = MagicMock(return_value=messages_iter(messages, error))
    return client


def write_archive(ids):
    path = archive_path(CHAT_ID)
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        "".join(json.dumps({"id": msg_id}) + "\n" for msg_id in ids),
        encoding="utf-8",
    )
    return path


def archived_ids(path):
    return [json.loads(line)["id"] for line in path.read_text("utf-8").splitlines()]


async def test_first_sync_archives_messages_from_since(config_dir):
    client = make_client([make_message(10), make_message(11)])

    result = await sync_chat(client, CHAT_ID, date(2026, 9, 1))

    path = archive_path(CHAT_ID)
    assert result == {"file": str(path), "added": 2, "total": 2}
    assert archived_ids(path) == [10, 11]
    client.iter_messages.assert_called_once_with(
        InputPeerChannel(1234567890, access_hash=0),
        reverse=True,
        wait_time=throttle.MIN_INTERVAL,
        offset_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


async def test_archive_lines_use_read_output_format(config_dir):
    client = make_client([make_message(10, text="Привет")])

    await sync_chat(client, CHAT_ID, date(2026, 9, 1))

    line = archive_path(CHAT_ID).read_text(encoding="utf-8").splitlines()[0]
    # Raw UTF-8, not \u escapes: the file is meant to be searched with grep.
    assert "Привет" in line
    assert json.loads(line)["sender_name"] == "John"


async def test_archive_file_is_named_by_resolved_marked_id(config_dir):
    # A raw positive ID and the -100... form of one channel share an archive.
    client = make_client([make_message(10)])

    await sync_chat(client, 1234567890, date(2026, 9, 1))

    assert archive_path(CHAT_ID).exists()


async def test_later_sync_appends_only_newer_messages(config_dir):
    path = write_archive([10, 11])
    client = make_client([make_message(12)])

    result = await sync_chat(client, CHAT_ID, None)

    assert result == {"file": str(path), "added": 1, "total": 3}
    assert archived_ids(path) == [10, 11, 12]
    assert client.iter_messages.call_args.kwargs["min_id"] == 11
    assert "offset_date" not in client.iter_messages.call_args.kwargs


async def test_since_is_ignored_for_existing_archive(config_dir):
    # Retrying an interrupted first sync repeats --since; it must continue
    # the archive, not fail or restart it.
    path = write_archive([10, 11])
    client = make_client([make_message(12)])

    await sync_chat(client, CHAT_ID, date(2020, 1, 1))

    assert archived_ids(path) == [10, 11, 12]
    assert client.iter_messages.call_args.kwargs["min_id"] == 11


async def test_first_sync_without_since_fails(config_dir):
    client = make_client([make_message(10)])

    with pytest.raises(SyncError, match="--since"):
        await sync_chat(client, CHAT_ID, None)

    client.iter_messages.assert_not_called()


async def test_sync_without_new_messages_leaves_archive_untouched(config_dir):
    path = write_archive([10, 11])
    before = path.stat().st_mtime_ns
    client = make_client([])

    result = await sync_chat(client, CHAT_ID, None)

    assert result["added"] == 0
    assert result["total"] == 2
    assert path.stat().st_mtime_ns == before


async def test_first_sync_with_no_messages_creates_no_file(config_dir):
    # An empty file would read as "no archive" anyway; not creating it keeps
    # the next sync asking for --since instead of silently starting at ID 0.
    client = make_client([])

    result = await sync_chat(client, CHAT_ID, date(2026, 9, 1))

    assert result["total"] == 0
    assert not archive_path(CHAT_ID).exists()


async def test_line_separator_in_text_does_not_split_a_message(config_dir):
    # json.dumps(ensure_ascii=False) leaves U+2028 unescaped; splitting the
    # file with str.splitlines() would cut such a message in two.
    client = make_client([make_message(10, text="one two")])
    await sync_chat(client, CHAT_ID, date(2026, 9, 1))
    client = make_client([make_message(11)])

    result = await sync_chat(client, CHAT_ID, None)

    assert result["total"] == 2
    assert client.iter_messages.call_args.kwargs["min_id"] == 10


async def test_damaged_archive_fails_with_clear_error(config_dir):
    path = archive_path(CHAT_ID)
    path.parent.mkdir()
    path.write_text('{"id": 10}\n{not json\n', encoding="utf-8")
    client = make_client([make_message(11)])

    with pytest.raises(SyncError, match="damaged"):
        await sync_chat(client, CHAT_ID, None)

    client.iter_messages.assert_not_called()


def make_connected_client(mocker, messages=(), error=None):
    """Patch TelegramClient in the session module and return the client mock."""
    client = make_client(messages, error)
    client.is_user_authorized.return_value = True
    mocker.patch("tg_reader.session.TelegramClient", return_value=client)
    return client


async def test_run_sync_returns_summary(config_dir, mocker):
    client = make_connected_client(mocker, [make_message(10)])

    result = await run_sync(CHAT_ID, date(2026, 9, 1))

    assert result["added"] == 1
    client.disconnect.assert_awaited_once()


async def test_interrupted_sync_keeps_progress_and_persists_flood_wait(
    config_dir, mocker
):
    make_connected_client(
        mocker,
        [make_message(10), make_message(11)],
        FloodWaitError(request=None, capture=77),
    )

    with pytest.raises(RetryLaterError):
        await run_sync(CHAT_ID, date(2026, 9, 1))

    assert archived_ids(archive_path(CHAT_ID)) == [10, 11]
    state = json.loads(
        (config_dir / throttle.STATE_FILENAME).read_text(encoding="utf-8")
    )
    assert state["flood_until"] > time.time()


async def test_failed_save_does_not_mask_flood_wait(config_dir, mocker):
    make_connected_client(
        mocker, [make_message(10)], FloodWaitError(request=None, capture=77)
    )
    mocker.patch("tg_reader.sync._save_lines", side_effect=SyncError("Cannot write"))

    with pytest.raises(RetryLaterError):
        await run_sync(CHAT_ID, date(2026, 9, 1))


async def test_unreadable_archive_is_permanent_not_retryable(config_dir, mocker):
    # A directory in place of the archive file makes the read fail with an
    # OSError, which the session layer would otherwise map to "cannot
    # reach Telegram, retry later".
    archive_path(CHAT_ID).mkdir(parents=True)
    make_connected_client(mocker, [make_message(10)])

    with pytest.raises(SyncError, match="Cannot read the archive"):
        await run_sync(CHAT_ID, None)
