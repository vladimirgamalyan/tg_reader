"""Unit tests for the anti-flood protection layer.

No network and no real waiting: time.sleep is mocked, the config directory
is redirected to a temporary path.
"""

import json
import os
import stat
import time

import pytest
from filelock import Timeout

from tg_reader import config, throttle
from tg_reader.throttle import RetryLaterError


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    return tmp_path


def write_state(config_dir, **state):
    path = config_dir / throttle.STATE_FILENAME
    path.write_text(json.dumps(state), encoding="utf-8")


def read_state(config_dir):
    path = config_dir / throttle.STATE_FILENAME
    return json.loads(path.read_text(encoding="utf-8"))


# --- RetryLaterError ---


def test_retry_later_error_message_and_attribute():
    error = RetryLaterError("busy", 12.3)

    assert error.retry_after == 12.3
    assert str(error) == "busy; retry after 13s"


# --- flood deadline ---


def test_check_flood_deadline_no_state(config_dir):
    throttle.check_flood_deadline()


def test_check_flood_deadline_active(config_dir):
    write_state(config_dir, flood_until=time.time() + 100)

    with pytest.raises(RetryLaterError) as excinfo:
        throttle.check_flood_deadline()

    assert 0 < excinfo.value.retry_after <= 100


def test_check_flood_deadline_expired(config_dir):
    write_state(config_dir, flood_until=time.time() - 1)

    throttle.check_flood_deadline()


def test_corrupt_state_is_ignored(config_dir):
    (config_dir / throttle.STATE_FILENAME).write_text("{not json", encoding="utf-8")

    throttle.check_flood_deadline()


def test_record_flood_wait(config_dir):
    before = time.time()

    throttle.record_flood_wait(60)

    flood_until = read_state(config_dir)["flood_until"]
    assert before + 60 <= flood_until <= time.time() + 60


def test_record_flood_wait_preserves_old_state_when_temp_write_fails(
    config_dir, mocker
):
    write_state(config_dir, flood_until=12345.0)
    original_write_text = type(config_dir).write_text

    def fail_temp_write(path, *args, **kwargs):
        if path.name == f"{throttle.STATE_FILENAME}.tmp":
            raise OSError("disk full")
        return original_write_text(path, *args, **kwargs)

    mocker.patch.object(type(config_dir), "write_text", fail_temp_write)

    with pytest.raises(OSError, match="disk full"):
        throttle.record_flood_wait(60)

    assert read_state(config_dir)["flood_until"] == 12345.0


# --- pacing ---


def test_pace_first_run_does_not_sleep(config_dir, mocker):
    sleep = mocker.patch("tg_reader.throttle.time.sleep")
    before = time.time()

    throttle.pace()

    sleep.assert_not_called()
    assert read_state(config_dir)["last_request_at"] >= before


def test_pace_after_recent_run_sleeps(config_dir, mocker):
    write_state(config_dir, last_request_at=time.time())
    sleep = mocker.patch("tg_reader.throttle.time.sleep")

    throttle.pace()

    sleep.assert_called_once()
    assert 0 < sleep.call_args[0][0] <= throttle.MIN_INTERVAL


def test_pace_after_old_run_does_not_sleep(config_dir, mocker):
    write_state(config_dir, last_request_at=time.time() - 10)
    sleep = mocker.patch("tg_reader.throttle.time.sleep")

    throttle.pace()

    sleep.assert_not_called()


def test_pace_preserves_flood_deadline(config_dir, mocker):
    write_state(config_dir, flood_until=12345.0)
    mocker.patch("tg_reader.throttle.time.sleep")

    throttle.pace()

    assert read_state(config_dir)["flood_until"] == 12345.0


# --- inter-process lock ---


def test_acquire_lock_and_release(config_dir):
    lock = throttle.acquire_lock()

    assert lock.is_locked
    lock.release()
    assert not lock.is_locked


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions only")
def test_acquire_lock_makes_directory_private(config_dir):
    lock = throttle.acquire_lock()

    try:
        assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    finally:
        lock.release()


def test_acquire_lock_busy(config_dir, mocker):
    lock = mocker.MagicMock()
    lock.acquire.side_effect = Timeout("lockfile")
    mocker.patch("tg_reader.throttle.FileLock", return_value=lock)

    with pytest.raises(RetryLaterError, match="another tg-reader process"):
        throttle.acquire_lock()
