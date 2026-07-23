"""Unit tests for CLI argument handling, output and exit codes."""

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tg_reader import cli
from tg_reader.download import DownloadError
from tg_reader.throttle import RetryLaterError


def test_version_flag_prints_version_and_exits_0(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])

    assert excinfo.value.code == 0
    assert re.fullmatch(r"tg-reader \d+\.\d+\.\d+\n", capsys.readouterr().out)


def test_download_requires_output():
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(["download", "-100123", "555"])

    assert excinfo.value.code == 1


def test_read_rejects_zero_chat_id():
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(["read", "0"])

    assert excinfo.value.code == 1


def test_read_rejects_negative_offset_id():
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(["read", "-100123", "--offset-id", "-5"])

    assert excinfo.value.code == 1


def test_read_accepts_zero_offset_id(mocker):
    # 0 is the "start from the newest" default; an agent passing it
    # explicitly must not be rejected.
    run_read = mocker.patch("tg_reader.cli.run_read", new=AsyncMock(return_value=[]))

    exit_code = cli.main(["read", "-100123", "--offset-id", "0"])

    assert exit_code == 0
    run_read.assert_awaited_once_with(-100123, 20, 0)


def test_read_passes_arguments_to_run_read(mocker, capsys):
    run_read = mocker.patch("tg_reader.cli.run_read", new=AsyncMock(return_value=[]))

    exit_code = cli.main(["read", "-100123"])

    assert exit_code == 0
    run_read.assert_awaited_once_with(-100123, 20, 0)


def test_download_rejects_zero_chat_id():
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(["download", "0", "555", "--output", "out"])

    assert excinfo.value.code == 1


def test_download_rejects_non_positive_msg_id():
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(["download", "-100123", "0", "--output", "out"])

    assert excinfo.value.code == 1


def test_download_rejects_non_positive_max_size():
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(
            ["download", "-100123", "555", "--output", "out", "--max-size", "0"]
        )

    assert excinfo.value.code == 1


def test_download_success_prints_json(mocker, capsys):
    summary = {"message_id": 555, "type": "photo", "file": "x", "size_bytes": 4}
    run_download = mocker.patch(
        "tg_reader.cli.run_download", new=AsyncMock(return_value=summary)
    )

    exit_code = cli.main(["download", "-100123", "555", "--output", "out"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == summary
    run_download.assert_awaited_once_with(-100123, 555, Path("out"), 100)


def test_download_error_exits_1(mocker, capsys):
    mocker.patch(
        "tg_reader.cli.run_download",
        new=AsyncMock(side_effect=DownloadError("no media")),
    )

    exit_code = cli.main(["download", "-100123", "555", "--output", "out"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no media" in captured.err


def test_keyboard_interrupt_exits_130(mocker, capsys):
    # run_read is replaced with a non-async mock so that no coroutine is
    # created (the patched asyncio.run would never await it).
    mocker.patch("tg_reader.cli.run_read", new=MagicMock())
    mocker.patch("tg_reader.cli.asyncio.run", side_effect=KeyboardInterrupt)

    exit_code = cli.main(["read", "-100123"])

    assert exit_code == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "interrupted" in captured.err


def test_eof_during_interactive_prompt_exits_1_with_clear_message(mocker, capsys):
    # Ctrl+Z / stdin running dry during an 'auth' prompt raises EOFError;
    # the raw "error: EOFError:" message would not explain what happened.
    mocker.patch("tg_reader.cli.run_auth", new=MagicMock())
    mocker.patch("tg_reader.cli.asyncio.run", side_effect=EOFError)

    exit_code = cli.main(["auth"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "stdin closed" in captured.err


def test_download_retry_later_exits_2(mocker, capsys):
    mocker.patch(
        "tg_reader.cli.run_download",
        new=AsyncMock(side_effect=RetryLaterError("flood wait", 42)),
    )

    exit_code = cli.main(["download", "-100123", "555", "--output", "out"])

    assert exit_code == 2
    assert "retry after 42s" in capsys.readouterr().err
