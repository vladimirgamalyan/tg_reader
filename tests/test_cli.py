"""Unit tests for CLI argument handling, output and exit codes."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tg_reader import cli
from tg_reader.download import DownloadError
from tg_reader.throttle import RetryLaterError


def test_download_requires_output():
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(["download", "-100123", "555"])

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


def test_download_retry_later_exits_2(mocker, capsys):
    mocker.patch(
        "tg_reader.cli.run_download",
        new=AsyncMock(side_effect=RetryLaterError("flood wait", 42)),
    )

    exit_code = cli.main(["download", "-100123", "555", "--output", "out"])

    assert exit_code == 2
    assert "retry after 42s" in capsys.readouterr().err
