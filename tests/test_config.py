"""Unit tests for config.json load/save."""

import json
import os
import stat

import pytest

from tg_reader import config


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    return tmp_path


def test_load_config_missing_file(config_dir):
    assert config.load_config() is None


def test_save_and_load_roundtrip(config_dir):
    config.save_config(1, "hash")

    assert config.load_config() == {"api_id": 1, "api_hash": "hash"}


def test_save_config_leaves_no_temp_file(config_dir):
    # The write-then-rename must not leave its .tmp sibling behind.
    config.save_config(1, "hash")

    assert [path.name for path in config_dir.iterdir()] == [config.CONFIG_FILENAME]


def test_load_config_corrupt_json_treated_as_missing(config_dir):
    (config_dir / config.CONFIG_FILENAME).write_text("{not json", encoding="utf-8")

    assert config.load_config() is None


def test_load_config_missing_keys_treated_as_missing(config_dir):
    (config_dir / config.CONFIG_FILENAME).write_text(
        json.dumps({"api_id": 1}), encoding="utf-8"
    )

    assert config.load_config() is None


@pytest.mark.parametrize(
    "data",
    [
        {"api_id": "1", "api_hash": "hash"},
        {"api_id": True, "api_hash": "hash"},
        {"api_id": 0, "api_hash": "hash"},
        {"api_id": 1, "api_hash": ""},
        {"api_id": 1, "api_hash": "   "},
    ],
)
def test_load_config_invalid_values_treated_as_missing(config_dir, data):
    (config_dir / config.CONFIG_FILENAME).write_text(json.dumps(data), encoding="utf-8")

    assert config.load_config() is None


def test_load_config_strips_api_hash(config_dir):
    (config_dir / config.CONFIG_FILENAME).write_text(
        json.dumps({"api_id": 1, "api_hash": " hash "}), encoding="utf-8"
    )

    assert config.load_config() == {"api_id": 1, "api_hash": "hash"}


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions only")
def test_save_config_makes_directory_private(config_dir):
    config.save_config(1, "hash")

    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
