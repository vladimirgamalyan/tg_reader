"""Unit tests for the batch download ('download-batch').

No network and no real waiting: the Telethon client is a mock, the pause
between transfers (asyncio.sleep) is mocked, and the config directory is
redirected to a temporary path - same patterns as tests/test_download.py.
"""

import errno
import io
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import FileReferenceExpiredError, FloodWaitError
from telethon.tl.functions.upload import GetFileRequest
from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    InputDocumentFileLocation,
    MessageMediaDocument,
)

from tg_reader import batch, cli, config, throttle
from tg_reader.batch import BatchLimits, run_download_batch

CHAT = 468932869
KB = 1024
MB = 1024 * 1024


def make_message(msg_id, size=1000):
    document = Document(
        id=msg_id,
        access_hash=2,
        file_reference=b"",
        date=datetime(2026, 7, 3, tzinfo=timezone.utc),
        mime_type="application/pdf",
        size=size,
        dc_id=2,
        attributes=[DocumentAttributeFilename(file_name=f"doc{msg_id}.pdf")],
    )
    return SimpleNamespace(id=msg_id, media=MessageMediaDocument(document=document))


def get_file_request():
    location = InputDocumentFileLocation(
        id=1, access_hash=2, file_reference=b"", thumb_size=""
    )
    return GetFileRequest(location=location, offset=0, limit=128 * KB)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    path = tmp_path / "config"
    path.mkdir()
    monkeypatch.setattr(config, "config_dir", lambda: path)
    (path / config.CONFIG_FILENAME).write_text(
        json.dumps({"api_id": 1, "api_hash": "hash"}), encoding="utf-8"
    )
    return path


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out"


@pytest.fixture
def sleep(mocker):
    return mocker.patch("tg_reader.batch.asyncio.sleep", new=AsyncMock())


def make_client(mocker, messages):
    """Connected client mock: serves messages by ID, writes downloaded files."""
    by_id = {message.id: message for message in messages}
    client = AsyncMock()
    client.session = SimpleNamespace(
        get_input_entity=MagicMock(return_value="input-entity"),
        get_entity_rows_by_id=MagicMock(return_value=None),
    )
    client.is_user_authorized.return_value = True

    async def get_messages(entity, ids):
        return [by_id.get(msg_id) for msg_id in ids]

    async def download_media(message, file, thumb=None, progress_callback=None):
        Path(file).write_bytes(b"x" * message.media.document.size)
        return file

    client.get_messages = AsyncMock(side_effect=get_messages)
    client.download_media = AsyncMock(side_effect=download_media)
    client_class = mocker.patch("tg_reader.session.TelegramClient", return_value=client)
    return client, client_class


def downloaded_ids(client):
    return [call.args[0].id for call in client.download_media.call_args_list]


def statuses(report):
    return [(entry["message_id"], entry["status"]) for entry in report["results"]]


def read_state(config_dir):
    path = config_dir / throttle.STATE_FILENAME
    return json.loads(path.read_text(encoding="utf-8"))


# --- order, skipping what is already there ---


async def test_downloads_in_the_given_order(config_dir, out, sleep, mocker):
    client, _ = make_client(mocker, [make_message(i) for i in (1, 2, 3)])

    report = await run_download_batch(CHAT, [3, 1, 2], out, BatchLimits())

    assert downloaded_ids(client) == [3, 1, 2]
    assert statuses(report) == [(3, "downloaded"), (1, "downloaded"), (2, "downloaded")]
    first = report["results"][0]
    target = out / f"{CHAT}_3_doc3.pdf"
    assert first == {
        "message_id": 3,
        "status": "downloaded",
        "type": "document",
        "file": str(target.resolve()),
        "size_bytes": 1000,
    }
    assert target.read_bytes() == b"x" * 1000
    assert report["summary"] == {
        "requested": 3,
        "downloaded": 3,
        "skipped": 0,
        "failed": 0,
        "pending": 0,
        "downloaded_bytes": 3000,
    }
    assert report["stopped"] is None


async def test_everything_already_downloaded_makes_no_connection(
    config_dir, out, sleep, mocker
):
    out.mkdir()
    (out / f"{CHAT}_1_doc1.pdf").write_bytes(b"12345")
    (out / f"{CHAT}_2_voice.ogg").write_bytes(b"123")
    _, client_class = make_client(mocker, [])

    report = await run_download_batch(CHAT, [1, 2], out, BatchLimits())

    client_class.assert_not_called()
    assert report["results"][0] == {
        "message_id": 1,
        "status": "skipped",
        "reason": "already_downloaded",
        "file": str((out / f"{CHAT}_1_doc1.pdf").resolve()),
        "size_bytes": 5,
    }
    assert statuses(report) == [(1, "skipped"), (2, "skipped")]
    # No connection, so no draw on the download-run budget either.
    assert not (config_dir / throttle.STATE_FILENAME).exists()


async def test_only_missing_messages_are_fetched(config_dir, out, sleep, mocker):
    out.mkdir()
    (out / f"{CHAT}_2_doc2.pdf").write_bytes(b"done")
    client, _ = make_client(mocker, [make_message(i) for i in (1, 2, 3)])

    report = await run_download_batch(CHAT, [1, 2, 3], out, BatchLimits())

    client.get_messages.assert_awaited_once_with("input-entity", ids=[1, 3])
    assert downloaded_ids(client) == [1, 3]
    assert statuses(report) == [(1, "downloaded"), (2, "skipped"), (3, "downloaded")]


async def test_part_file_does_not_count_as_downloaded(config_dir, out, sleep, mocker):
    out.mkdir()
    (out / f"{CHAT}_1_doc1.pdf.part").write_bytes(b"half")
    client, _ = make_client(mocker, [make_message(1)])

    report = await run_download_batch(CHAT, [1], out, BatchLimits())

    assert statuses(report) == [(1, "downloaded")]
    assert not (out / f"{CHAT}_1_doc1.pdf.part").exists()


async def test_prefix_match_is_exact(config_dir, out, sleep, mocker):
    # Message 12's file must not pass for message 1's, nor another chat's
    # file for this chat's.
    out.mkdir()
    (out / f"{CHAT}_12_doc12.pdf").write_bytes(b"other message")
    (out / f"1{CHAT}_1_doc1.pdf").write_bytes(b"other chat")
    client, _ = make_client(mocker, [make_message(1)])

    report = await run_download_batch(CHAT, [1], out, BatchLimits())

    assert downloaded_ids(client) == [1]
    assert statuses(report) == [(1, "downloaded")]


def test_find_downloaded_prefers_the_media_file_over_derived_files(tmp_path):
    (tmp_path / f"{CHAT}_7_voice.ogg.transcript.json").write_text("{}")
    (tmp_path / f"{CHAT}_7_voice.ogg").write_bytes(b"ogg")

    found = batch.find_downloaded(tmp_path, CHAT)

    assert found == {7: (tmp_path / f"{CHAT}_7_voice.ogg", 3)}


async def test_unusable_output_fails_before_connecting(config_dir, tmp_path, mocker):
    not_a_dir = tmp_path / "output"
    not_a_dir.write_bytes(b"i am a file")
    _, client_class = make_client(mocker, [make_message(1)])

    with pytest.raises(batch.DownloadError, match="not usable"):
        await run_download_batch(CHAT, [1], not_a_dir, BatchLimits())

    client_class.assert_not_called()


# --- message fetching ---


async def test_messages_are_fetched_100_per_request(config_dir, out, sleep, mocker):
    ids = list(range(1, 151))
    client, _ = make_client(mocker, [make_message(i, size=10) for i in ids])

    report = await run_download_batch(CHAT, ids, out, BatchLimits())

    requested = [call.kwargs["ids"] for call in client.get_messages.await_args_list]
    assert requested == [ids[:100], ids[100:]]
    assert report["summary"]["downloaded"] == 150


async def test_stale_messages_are_fetched_again(config_dir, out, sleep, mocker):
    # File references expire after a few hours: a message fetched longer
    # than REFETCH_AFTER ago is fetched again before its transfer.
    client, _ = make_client(mocker, [make_message(i) for i in (1, 2, 3)])
    clock = [
        0,
        batch.REFETCH_AFTER + 1,
        batch.REFETCH_AFTER + 1,
        batch.REFETCH_AFTER + 2,
    ]
    mocker.patch.object(batch, "time", SimpleNamespace(monotonic=lambda: clock.pop(0)))

    await run_download_batch(CHAT, [1, 2, 3], out, BatchLimits())

    requested = [call.kwargs["ids"] for call in client.get_messages.await_args_list]
    assert requested == [[1, 2, 3], [2, 3]]


async def test_messages_without_a_file_are_skipped_with_a_reason(
    config_dir, out, sleep, mocker
):
    no_media = SimpleNamespace(id=1, media=None)
    huge = make_message(3, size=200 * MB)
    client, _ = make_client(mocker, [no_media, huge, make_message(4)])

    report = await run_download_batch(CHAT, [1, 2, 3, 4], out, BatchLimits())

    assert downloaded_ids(client) == [4]
    results = {entry["message_id"]: entry for entry in report["results"]}
    assert results[1]["status"] == "skipped"
    assert results[1]["reason"] == "no_media"
    assert results[2]["reason"] == "not_found"
    assert results[3]["reason"] == "too_large"
    assert "200.0 MB" in results[3]["error"]
    assert results[4]["status"] == "downloaded"
    assert report["stopped"] is None


# --- pauses and run limits ---


async def test_pauses_only_between_transfers(config_dir, out, sleep, mocker):
    no_media = SimpleNamespace(id=1, media=None)
    make_client(mocker, [no_media, make_message(2), make_message(3), make_message(4)])

    await run_download_batch(CHAT, [1, 2, 3, 4], out, BatchLimits(pause=3))

    # Three transfers, two pauses: none before the first, none for the
    # message without media, none after the last.
    delays = [call.args[0] for call in sleep.await_args_list]
    assert len(delays) == 2
    assert all(3 <= delay <= 3 * (1 + batch.PAUSE_JITTER) for delay in delays)


async def test_max_files_stops_the_run(config_dir, out, sleep, mocker):
    client, _ = make_client(mocker, [make_message(i) for i in range(1, 6)])

    report = await run_download_batch(
        CHAT, [1, 2, 3, 4, 5], out, BatchLimits(max_files=2)
    )

    assert downloaded_ids(client) == [1, 2]
    assert statuses(report)[2:] == [(3, "pending"), (4, "pending"), (5, "pending")]
    assert report["summary"]["pending"] == 3
    assert report["stopped"]["reason"] == "max_files"
    assert report["stopped"]["retry_after"] == batch.RUN_LIMIT_RETRY


async def test_max_total_stops_before_exceeding(config_dir, out, sleep, mocker):
    client, _ = make_client(mocker, [make_message(i, size=400 * KB) for i in (1, 2, 3)])

    report = await run_download_batch(CHAT, [1, 2, 3], out, BatchLimits(max_total_mb=1))

    assert downloaded_ids(client) == [1, 2]
    assert statuses(report)[2] == (3, "pending")
    assert report["stopped"]["reason"] == "max_total"


# --- per-message failures ---


async def test_rejected_file_request_fails_that_message_only(
    config_dir, out, sleep, mocker
):
    client, _ = make_client(mocker, [make_message(1), make_message(2)])
    write_file = client.download_media.side_effect

    async def reject_first(message, file, thumb=None, progress_callback=None):
        if message.id == 1:
            raise FileReferenceExpiredError(request=None)
        return await write_file(message, file, thumb, progress_callback)

    client.download_media.side_effect = reject_first

    report = await run_download_batch(CHAT, [1, 2], out, BatchLimits())

    assert statuses(report) == [(1, "failed"), (2, "downloaded")]
    assert report["results"][0]["reason"] == "telegram_error"
    assert report["stopped"] is None


async def test_local_file_error_stops_the_run(config_dir, out, sleep, mocker):
    client, _ = make_client(mocker, [make_message(1), make_message(2)])
    client.download_media.side_effect = OSError(errno.ENOSPC, "No space left")

    report = await run_download_batch(CHAT, [1, 2], out, BatchLimits())

    assert statuses(report) == [(1, "failed"), (2, "pending")]
    assert report["results"][0]["reason"] == "local_file_error"
    assert report["stopped"]["reason"] == "local_file_error"
    assert report["stopped"]["retry_after"] is None


# --- flood waits and network failures ---


async def test_long_flood_wait_stops_with_partial_report(
    config_dir, out, sleep, mocker
):
    client, _ = make_client(mocker, [make_message(i) for i in (1, 2, 3, 4)])
    write_file = client.download_media.side_effect

    async def flood_on_third(message, file, thumb=None, progress_callback=None):
        if message.id == 3:
            raise FloodWaitError(request=get_file_request(), capture=3026)
        return await write_file(message, file, thumb, progress_callback)

    client.download_media.side_effect = flood_on_third

    report = await run_download_batch(CHAT, [1, 2, 3, 4], out, BatchLimits())

    assert statuses(report) == [
        (1, "downloaded"),
        (2, "downloaded"),
        (3, "pending"),
        (4, "pending"),
    ]
    assert report["stopped"]["reason"] == "flood_wait"
    assert report["stopped"]["retry_after"] == 3026
    assert "GetFileRequest" in report["stopped"]["error"]
    assert (out / f"{CHAT}_1_doc1.pdf").exists()
    state = read_state(config_dir)
    assert state["flood_seconds"] == 3026
    assert state["flood_until"] > time.time() + 3000
    assert state["last_flood"]["request"] == "GetFileRequest"
    assert state["last_flood"]["files_downloaded"] == 2
    assert state["last_flood"]["bytes_downloaded"] == 2000
    client.disconnect.assert_awaited_once()


async def test_rerun_after_flood_wait_continues(config_dir, out, sleep, mocker):
    out.mkdir()
    (out / f"{CHAT}_1_doc1.pdf").write_bytes(b"x")
    (out / f"{CHAT}_2_doc2.pdf").write_bytes(b"x")
    client, _ = make_client(mocker, [make_message(i) for i in (1, 2, 3, 4)])

    report = await run_download_batch(CHAT, [1, 2, 3, 4], out, BatchLimits())

    assert downloaded_ids(client) == [3, 4]
    assert statuses(report) == [
        (1, "skipped"),
        (2, "skipped"),
        (3, "downloaded"),
        (4, "downloaded"),
    ]


async def test_active_flood_wait_refuses_before_connecting(
    config_dir, out, sleep, mocker
):
    (config_dir / throttle.STATE_FILENAME).write_text(
        json.dumps({"flood_until": time.time() + 100}), encoding="utf-8"
    )
    _, client_class = make_client(mocker, [make_message(1)])

    with pytest.raises(throttle.RetryLaterError):
        await run_download_batch(CHAT, [1], out, BatchLimits())

    client_class.assert_not_called()


async def test_network_failure_stops_with_retry(config_dir, out, sleep, mocker):
    client, _ = make_client(mocker, [make_message(1), make_message(2)])
    client.download_media.side_effect = ConnectionResetError(104, "reset")

    report = await run_download_batch(CHAT, [1, 2], out, BatchLimits())

    assert statuses(report) == [(1, "pending"), (2, "pending")]
    assert report["stopped"]["reason"] == "network"
    assert report["stopped"]["retry_after"] == throttle.NETWORK_RETRY_HINT


async def test_short_flood_wait_is_noted_on_stderr(
    config_dir, out, sleep, mocker, capsys
):
    # Telethon sleeps through short waits itself and only logs them; the
    # batch surfaces that log line.
    client, _ = make_client(mocker, [make_message(1), make_message(2)])
    write_file = client.download_media.side_effect
    telethon_log = logging.getLogger("telethon.client.users")

    async def sleep_through_flood(message, file, thumb=None, progress_callback=None):
        if message.id == 2:
            telethon_log.info(
                "Sleeping%s for %ds (%s) on %s flood wait",
                "",
                5,
                "0:00:05",
                "GetFileRequest",
            )
        return await write_file(message, file, thumb, progress_callback)

    client.download_media.side_effect = sleep_through_flood

    await run_download_batch(CHAT, [1, 2], out, BatchLimits())

    err = capsys.readouterr().err
    assert "note: Sleeping for 5s (0:00:05) on GetFileRequest flood wait" in err
    assert "after 1 files downloaded" in err
    assert telethon_log.handlers == []
    assert telethon_log.level == logging.NOTSET


async def test_run_draws_on_the_download_run_budget(config_dir, out, sleep, mocker):
    make_client(mocker, [make_message(1), make_message(2)])

    await run_download_batch(CHAT, [1, 2], out, BatchLimits())

    state = read_state(config_dir)
    assert state["download_runs"] == pytest.approx(throttle.DOWNLOAD_RUNS_PER_HOUR - 1)


# --- CLI: input, output, exit codes ---


def run_cli(argv, capsys):
    exit_code = cli.main(argv)
    captured = capsys.readouterr()
    report = json.loads(captured.out) if captured.out else None
    return exit_code, report, captured.err


def test_cli_success_exits_0(config_dir, out, sleep, mocker, capsys):
    make_client(mocker, [make_message(1), make_message(2)])

    exit_code, report, err = run_cli(
        ["download-batch", str(CHAT), "1", "2", "--output", str(out)], capsys
    )

    assert exit_code == 0
    assert report["summary"]["downloaded"] == 2
    assert err == ""


def test_cli_flood_wait_exits_2_with_report(config_dir, out, sleep, mocker, capsys):
    client, _ = make_client(mocker, [make_message(1)])
    client.download_media.side_effect = FloodWaitError(
        request=get_file_request(), capture=3026
    )

    exit_code, report, err = run_cli(
        ["download-batch", str(CHAT), "1", "--output", str(out)], capsys
    )

    assert exit_code == 2
    assert report["stopped"]["reason"] == "flood_wait"
    assert err.strip().endswith("retry after 3026s")


def test_cli_run_limit_exits_2(config_dir, out, sleep, mocker, capsys):
    make_client(mocker, [make_message(1), make_message(2)])

    exit_code, report, err = run_cli(
        [
            "download-batch",
            str(CHAT),
            "1",
            "2",
            "--output",
            str(out),
            "--max-files",
            "1",
        ],
        capsys,
    )

    assert exit_code == 2
    assert report["summary"]["pending"] == 1
    assert err.strip().endswith(f"retry after {batch.RUN_LIMIT_RETRY}s")


def test_cli_failed_message_exits_1_with_report(config_dir, out, sleep, mocker, capsys):
    client, _ = make_client(mocker, [make_message(1)])
    client.download_media.side_effect = FileReferenceExpiredError(request=None)

    exit_code, report, err = run_cli(
        ["download-batch", str(CHAT), "1", "--output", str(out)], capsys
    )

    assert exit_code == 1
    assert report["summary"]["failed"] == 1
    assert "failed" in err


def test_cli_reads_ids_from_file_after_arguments(
    config_dir, out, tmp_path, sleep, mocker, capsys
):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("3\n1\n\n2 3\n", encoding="utf-8")
    client, _ = make_client(mocker, [make_message(i) for i in (1, 2, 3, 4)])

    exit_code, report, _ = run_cli(
        [
            "download-batch",
            str(CHAT),
            "4",
            "--ids-file",
            str(ids_file),
            "--output",
            str(out),
        ],
        capsys,
    )

    assert exit_code == 0
    assert [entry["message_id"] for entry in report["results"]] == [4, 3, 1, 2]
    assert downloaded_ids(client) == [4, 3, 1, 2]


def test_cli_reads_ids_from_stdin(config_dir, out, sleep, mocker, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("2\n1\n"))
    client, _ = make_client(mocker, [make_message(1), make_message(2)])

    exit_code, _, _ = run_cli(
        ["download-batch", str(CHAT), "--ids-file", "-", "--output", str(out)], capsys
    )

    assert exit_code == 0
    assert downloaded_ids(client) == [2, 1]


@pytest.mark.parametrize(
    "extra",
    [
        [],  # no message IDs at all
        ["1", "--pause", "0"],
        ["1", "--max-files", "0"],
        ["1", "--max-total", "50"],  # below the default --max-size of 100
    ],
)
def test_cli_rejects_bad_arguments(extra, out):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["download-batch", str(CHAT), *extra, "--output", str(out)])

    assert excinfo.value.code == 1


def test_cli_rejects_bad_token_in_ids_file(tmp_path, out):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("1\nabc\n", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "download-batch",
                str(CHAT),
                "--ids-file",
                str(ids_file),
                "--output",
                str(out),
            ]
        )

    assert excinfo.value.code == 1
