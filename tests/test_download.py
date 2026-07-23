"""Unit tests for the media download logic.

No network: the Telethon client is injected and replaced with AsyncMock,
same pattern as tests/test_read.py.
"""

import errno
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import FloodPremiumWaitError, FloodWaitError
from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    MessageMediaDocument,
    MessageMediaPhoto,
    Photo,
    PhotoSize,
    PhotoStrippedSize,
    VideoSize,
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


def make_unknown_size_photo_message(msg_id=555):
    photo = Photo(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=datetime(2026, 7, 3, tzinfo=timezone.utc),
        sizes=[PhotoStrippedSize(type="i", bytes=b"tiny")],
        dc_id=2,
    )
    return SimpleNamespace(id=msg_id, media=MessageMediaPhoto(photo=photo))


def make_animated_photo_message(msg_id=555):
    photo = Photo(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=datetime(2026, 7, 3, tzinfo=timezone.utc),
        sizes=[PhotoSize(type="y", w=1280, h=960, size=1000)],
        video_sizes=[VideoSize(type="u", w=720, h=720, size=5000)],
        dc_id=2,
    )
    return SimpleNamespace(id=msg_id, media=MessageMediaPhoto(photo=photo))


def make_client(message, payload=b"data"):
    """Client mock whose download_media writes payload to the target file."""
    client = AsyncMock()
    client.session = SimpleNamespace(
        get_input_entity=MagicMock(return_value="input-entity"),
        get_entity_rows_by_id=MagicMock(return_value=(-100123, 0)),
    )
    client.get_messages.return_value = message

    async def fake_download(message, file, thumb=None, progress_callback=None):
        Path(file).write_bytes(payload)
        return file

    client.download_media = AsyncMock(side_effect=fake_download)
    return client


# --- download_to_dir ---


async def test_download_saves_file_and_reports_it(tmp_path):
    client = make_client(make_media_message())
    output_dir = tmp_path / "out"

    result = await download_to_dir(client, -100123, 555, output_dir, max_size_mb=100)

    target = output_dir / "-100123_555_report.pdf"
    assert target.read_bytes() == b"data"
    assert result == {
        "message_id": 555,
        "type": "document",
        "file": str(target.resolve()),
        "size_bytes": 4,
    }
    assert not (output_dir / "-100123_555_report.pdf.part").exists()
    client.get_messages.assert_awaited_once_with("input-entity", ids=555)


async def test_download_overwrites_existing_file(tmp_path):
    client = make_client(make_media_message(), payload=b"new content")
    target = tmp_path / "-100123_555_report.pdf"
    target.write_bytes(b"old content")

    await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    assert target.read_bytes() == b"new content"


async def test_download_same_msg_id_from_different_chats_does_not_collide(tmp_path):
    # Message IDs are only unique within one chat: downloading msg 555 from
    # two chats into one directory must produce two files.
    client = make_client(make_media_message())

    await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)
    await download_to_dir(client, -100456, 555, tmp_path, max_size_mb=100)

    assert (tmp_path / "-100123_555_report.pdf").exists()
    assert (tmp_path / "-100456_555_report.pdf").exists()


async def test_download_animated_photo_pinned_to_still_size(tmp_path):
    # A photo carrying video_sizes would make Telethon download its video
    # variant, while media_info reported the still image: the thumb
    # argument pins the transfer to the reported still size.
    client = make_client(make_animated_photo_message())

    await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    assert client.download_media.call_args.kwargs["thumb"] == "y"


async def test_download_document_passes_no_thumb(tmp_path):
    # A non-None thumb for a document would download its thumbnail image
    # instead of the document itself.
    client = make_client(make_media_message())

    await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    assert client.download_media.call_args.kwargs["thumb"] is None


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


async def test_download_refuses_unknown_size_file(tmp_path):
    client = make_client(make_unknown_size_photo_message())

    with pytest.raises(DownloadError, match="size.*unknown"):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    client.download_media.assert_not_called()


async def test_download_refuses_oversized_actual_file(tmp_path):
    client = make_client(make_media_message(size=1000), payload=b"x" * (2 * MB))

    with pytest.raises(DownloadError, match=r"2\.0 MB"):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=1)

    assert list(tmp_path.iterdir()) == []


async def test_download_aborts_transfer_when_declared_size_lies(tmp_path):
    # The declared size passes the pre-check but the actual stream is far
    # larger: the transfer must be aborted mid-download via the progress
    # callback, not billed in full and rejected only afterwards (ADR-0010).
    client = make_client(make_media_message(size=1000))

    async def endless_download(message, file, thumb=None, progress_callback=None):
        with Path(file).open("wb") as stream:
            for _ in range(100):  # 100 MB if never aborted
                stream.write(b"x" * MB)
                progress_callback(stream.tell(), None)
        return file

    client.download_media = AsyncMock(side_effect=endless_download)

    with pytest.raises(DownloadError, match="aborted at"):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=1)

    assert list(tmp_path.iterdir()) == []


async def test_download_no_file_returned_is_permanent_error(tmp_path):
    client = make_client(make_media_message())
    client.download_media = AsyncMock(return_value=None)

    with pytest.raises(DownloadError, match="returned no file"):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    assert list(tmp_path.iterdir()) == []


async def test_download_failure_removes_part_file(tmp_path):
    client = make_client(make_media_message())

    async def failing_download(message, file, thumb=None, progress_callback=None):
        Path(file).write_bytes(b"partial")
        raise RuntimeError("connection lost")

    client.download_media = AsyncMock(side_effect=failing_download)

    with pytest.raises(RuntimeError):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    assert list(tmp_path.iterdir()) == []


async def test_download_replaces_leftover_part_from_killed_run(tmp_path):
    # A .part left by a previous run killed mid-download must not survive
    # or leak into the result: the fresh download replaces it.
    client = make_client(make_media_message(), payload=b"fresh data")
    stale = tmp_path / "-100123_555_report.pdf.part"
    stale.write_bytes(b"stale partial")

    result = await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    target = tmp_path / "-100123_555_report.pdf"
    assert target.read_bytes() == b"fresh data"
    assert result["size_bytes"] == len(b"fresh data")
    assert not stale.exists()


async def test_download_cleanup_failure_does_not_mask_download_error(tmp_path, mocker):
    # If removing the .part in the cleanup fails too (e.g. an antivirus
    # holding it open on Windows), the original permanent error must
    # propagate - not be replaced by the cleanup's OSError, which the
    # session layer would misreport as a transient network failure.
    client = make_client(make_media_message())

    async def locked_write(message, file, thumb=None, progress_callback=None):
        Path(file).write_bytes(b"partial")
        raise PermissionError(errno.EACCES, "Access is denied", file)

    client.download_media = AsyncMock(side_effect=locked_write)
    original_unlink = Path.unlink

    def failing_unlink(path, missing_ok=False):
        # The writability probe's empty file is deleted normally; only the
        # partially written download behaves as locked.
        if path.exists() and path.read_bytes() == b"partial":
            raise PermissionError(errno.EACCES, "Access is denied", str(path))
        return original_unlink(path, missing_ok=missing_ok)

    mocker.patch.object(Path, "unlink", failing_unlink)

    with pytest.raises(DownloadError, match="Cannot write"):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    # The cleanup path was actually exercised: the locked .part survived.
    part = tmp_path / "-100123_555_report.pdf.part"
    assert part.read_bytes() == b"partial"


async def test_download_target_vanishing_after_move_does_not_fail(tmp_path, mocker):
    # An antivirus may quarantine the freshly renamed file: the summary
    # must come from the bytes actually transferred, not from a stat() of
    # the final path - that OSError would escape past the local-error
    # classification and be misreported by the session layer as "cannot
    # reach Telegram" (endless retries).
    client = make_client(make_media_message())
    original_replace = os.replace

    def replace_then_quarantine(src, dst):
        original_replace(src, dst)
        Path(dst).unlink()

    mocker.patch("tg_reader.download.os.replace", replace_then_quarantine)

    result = await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)

    assert result["size_bytes"] == 4


async def test_download_disk_full_is_permanent_error(tmp_path):
    # ENOSPC escapes Telethon's file write with no filename attached; it
    # must still be classified as a local failure, not routed to the
    # session layer's "cannot reach Telegram, retry later" mapping.
    client = make_client(make_media_message())
    client.download_media = AsyncMock(
        side_effect=OSError(errno.ENOSPC, "No space left on device")
    )

    with pytest.raises(DownloadError, match="Cannot write"):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)


async def test_download_locked_part_file_is_permanent_error(tmp_path):
    # A file error carrying the offending path (e.g. an antivirus holding
    # the .part open on Windows) is local and permanent.
    client = make_client(make_media_message())
    client.download_media = AsyncMock(
        side_effect=PermissionError(errno.EACCES, "Access is denied", "x.part")
    )

    with pytest.raises(DownloadError, match="Cannot write"):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)


async def test_download_network_oserror_propagates(tmp_path):
    # A socket-level OSError (no filename, non-filesystem errno) must keep
    # propagating so the session layer maps it to the retry contract.
    client = make_client(make_media_message())
    client.download_media = AsyncMock(
        side_effect=ConnectionResetError(104, "Connection reset by peer")
    )

    with pytest.raises(ConnectionResetError):
        await download_to_dir(client, -100123, 555, tmp_path, max_size_mb=100)


async def test_download_unusable_output_dir_is_permanent_error(tmp_path):
    # An --output path that is not a usable directory (here: an existing file)
    # is a permanent local failure, not a transient network one.
    client = make_client(make_media_message())
    not_a_dir = tmp_path / "output"
    not_a_dir.write_bytes(b"i am a file")

    with pytest.raises(DownloadError, match="not usable"):
        await download_to_dir(client, -100123, 555, not_a_dir, max_size_mb=100)

    client.download_media.assert_not_called()


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
    mocker.patch("tg_reader.session.TelegramClient", return_value=client)
    return client


async def test_run_download_returns_summary(config_dir, tmp_path, mocker):
    client = make_connected_client(mocker, make_media_message())
    output_dir = tmp_path / "files"

    result = await run_download(-100123, 555, output_dir, max_size_mb=100)

    assert result["message_id"] == 555
    assert (output_dir / "-100123_555_report.pdf").exists()
    client.disconnect.assert_awaited_once()


async def test_run_download_unusable_output_is_permanent_not_retry(
    config_dir, tmp_path, mocker
):
    # A filesystem failure inside the session must surface as a permanent
    # DownloadError (exit 1), not be swallowed by the session layer's broad
    # OSError handler and reported as "retry later" (exit 2).
    client = make_connected_client(mocker, make_media_message())
    not_a_dir = tmp_path / "output"
    not_a_dir.write_bytes(b"i am a file")

    with pytest.raises(DownloadError):
        await run_download(-100123, 555, not_a_dir, max_size_mb=100)

    client.disconnect.assert_awaited_once()


async def test_run_download_disk_full_is_permanent_not_retry(
    config_dir, tmp_path, mocker
):
    # Same classification through the whole session stack: a disk-full
    # write failure must exit 1, not map to "retry later" (exit 2).
    client = make_connected_client(mocker, make_media_message())
    client.download_media = AsyncMock(
        side_effect=OSError(errno.ENOSPC, "No space left on device")
    )

    with pytest.raises(DownloadError):
        await run_download(-100123, 555, tmp_path / "files", max_size_mb=100)

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


async def test_run_download_premium_flood_wait_maps_to_retry_later(
    config_dir, tmp_path, mocker
):
    # FLOOD_PREMIUM_WAIT (the free-account transfer speed limit on large
    # downloads) is a sibling of FloodWaitError, not a subclass: it must
    # still map to the retry contract and persist the deadline instead of
    # falling through as a permanent error.
    client = make_connected_client(mocker, make_media_message())
    client.download_media = AsyncMock(
        side_effect=FloodPremiumWaitError(request=None, capture=77)
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


async def test_run_download_refuses_while_flood_wait_active(
    config_dir, tmp_path, mocker
):
    (config_dir / throttle.STATE_FILENAME).write_text(
        json.dumps({"flood_until": time.time() + 100}), encoding="utf-8"
    )
    client_class = mocker.patch("tg_reader.session.TelegramClient")

    with pytest.raises(RetryLaterError):
        await run_download(-100123, 555, tmp_path / "files", max_size_mb=100)

    client_class.assert_not_called()
