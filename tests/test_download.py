"""Unit tests for the media download logic.

No network: the Telethon client is injected and replaced with AsyncMock,
same pattern as tests/test_read.py.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    MessageMediaDocument,
)

from tg_reader import config, throttle
from tg_reader.download import DownloadError, download_to_dir, run_download
from tg_reader.throttle import RetryLaterError

MB = 1024 * 1024


def make_media_message(msg_id=555, size=1000, filename="report.pdf"):
    document = Document(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=datetime(2026, 7, 3, tzinfo=timezone.utc),
        mime_type="application/pdf",
        size=size,
        dc_id=2,
        attributes=[DocumentAttributeFilename(file_name=filename)],
    )
    return SimpleNamespace(id=msg_id, media=MessageMediaDocument(document=document))


def make_client(message, payload=b"data"):
    """Client mock whose download_media writes payload to the target file."""
    client = AsyncMock()
    client.session.get_input_entity = MagicMock(return_value="input-entity")
    client.get_messages.return_value = message

    async def fake_download(message, file):
        Path(file).write_bytes(payload)
        return file

    client.download_media = AsyncMock(side_effect=fake_download)
    return client


# --- download_to_dir ---


async def test_download_saves_file_and_reports_it(tmp_path):
    client = make_client(make_media_message())
    output_dir = tmp_path / "out"

    result = await download_to_dir(client, -100123, 555, output_dir, max_size_mb=100)

    target = output_dir / "555_report.pdf"
    assert target.read_bytes() == b"data"
    assert result == {
        "message_id": 555,
        "type": "document",
        "file": str(target.resolve()),
        "size_bytes": 4,
    }
    assert not (output_dir / "555_report.pdf.part").exists()
    client.get_messages.assert_awaited_once_with("input-entity", ids=555)


async def test_download_overwrites_existing_file(tmp_path):
    client = make_client(make_media_message(), payload=b"new content")
    target = tmp_path / "555_report.pdf"
    target.write_bytes(b"old content")

    await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    assert target.read_bytes() == b"new content"


async def test_download_message_not_found(tmp_path):
    client = make_client(None)

    with pytest.raises(DownloadError, match="not found"):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)


async def test_download_message_without_media(tmp_path):
    client = make_client(SimpleNamespace(id=555, media=None))

    with pytest.raises(DownloadError, match="no downloadable media"):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)


async def test_download_refuses_oversized_file(tmp_path):
    client = make_client(make_media_message(size=200 * MB))

    with pytest.raises(DownloadError, match=r"200\.0 MB"):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    client.download_media.assert_not_called()


async def test_download_no_file_returned_is_permanent_error(tmp_path):
    client = make_client(make_media_message())
    client.download_media = AsyncMock(return_value=None)

    with pytest.raises(DownloadError, match="returned no file"):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    assert list(tmp_path.iterdir()) == []


async def test_download_failure_removes_part_file(tmp_path):
    client = make_client(make_media_message())

    async def failing_download(message, file):
        Path(file).write_bytes(b"partial")
        raise RuntimeError("connection lost")

    client.download_media = AsyncMock(side_effect=failing_download)

    with pytest.raises(RuntimeError):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    assert list(tmp_path.iterdir()) == []


# --- run_download: flood protection wiring ---


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / config.CONFIG_FILENAME).write_text(
        json.dumps({"api_id": 1, "api_hash": "hash"}), encoding="utf-8"
    )
    return tmp_path


def make_connected_client(mocker, message):
    client = make_client(message)
    client.is_user_authorized.return_value = True
    mocker.patch("tg_reader.download.TelegramClient", return_value=client)
    return client


async def test_run_download_returns_summary(config_dir, tmp_path, mocker):
    client = make_connected_client(mocker, make_media_message())
    output_dir = tmp_path / "files"

    result = await run_download(-100123, 555, output_dir, max_size_mb=100)

    assert result["message_id"] == 555
    assert (output_dir / "555_report.pdf").exists()
    client.disconnect.assert_awaited_once()


async def test_run_download_flood_wait_persists_deadline(config_dir, tmp_path, mocker):
    client = make_connected_client(mocker, make_media_message())
    client.download_media = AsyncMock(
        side_effect=FloodWaitError(request=None, capture=77)
    )

    with pytest.raises(RetryLaterError):
        await run_download(-100123, 555, tmp_path / "files", max_size_mb=100)

    state = json.loads(
        (config_dir / throttle.STATE_FILENAME).read_text(encoding="utf-8")
    )
    assert state["flood_until"] > time.time()
    client.disconnect.assert_awaited_once()


async def test_run_download_network_error_maps_to_retry_later(
    config_dir, tmp_path, mocker
):
    client = make_connected_client(mocker, make_media_message())
    client.connect.side_effect = ConnectionError("Connection to Telegram failed")

    with pytest.raises(RetryLaterError, match="cannot reach Telegram"):
        await run_download(-100123, 555, tmp_path / "files", max_size_mb=100)

    client.disconnect.assert_awaited_once()


async def test_run_download_refuses_while_flood_wait_active(config_dir, tmp_path, mocker):
    (config_dir / throttle.STATE_FILENAME).write_text(
        json.dumps({"flood_until": time.time() + 100}), encoding="utf-8"
    )
    client_class = mocker.patch("tg_reader.download.TelegramClient")

    with pytest.raises(RetryLaterError):
        await run_download(-100123, 555, tmp_path / "files", max_size_mb=100)

    client_class.assert_not_called()
