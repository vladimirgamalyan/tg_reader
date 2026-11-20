"""Anti-flood protection: inter-process lock, FloodWait persistence, pacing.

Telegram punishes clients that keep sending requests during an assigned
FLOOD_WAIT, so the wait deadline is persisted to disk and every subsequent
run refuses to touch the network until it expires. A single inter-process
lock serializes runs (parallel processes would also corrupt the SQLite
session file), and a minimum interval between runs smooths out bursts from
a misbehaving caller. Download runs also draw on an hourly budget, so a
loop of single downloads cannot repeat the per-run connection and
authorization export that once drew a 50-minute FloodWait.
"""

import json
import math
import time
from datetime import datetime, timezone

from filelock import FileLock, Timeout

from . import config

STATE_FILENAME = "throttle.json"
LOCK_FILENAME = "tg_reader.lock"

# How long a run waits for another tg-reader process before giving up.
LOCK_TIMEOUT = 30
# Minimum interval between consecutive runs, in seconds.
MIN_INTERVAL = 2.0
# FloodWait up to this many seconds is slept through by Telethon in-place;
# longer waits abort the run and persist the deadline.
FLOOD_SLEEP_THRESHOLD = 30
# Retry hint, in seconds, reported when Telegram cannot be reached over
# the network (a transient condition: exit code 2, not 1).
NETWORK_RETRY_HINT = 30
# Longest flood wait Telegram realistically assigns (one day). A persisted
# deadline further in the future than this is impossible: the system clock
# jumped backwards or the state file is damaged.
MAX_FLOOD_WAIT = 86400
# Upper bound for --limit: keeps one run to a single GetHistory request.
MAX_LIMIT = 100
# Budget of download runs ('download' and 'download-batch'), as a token
# bucket: up to this many runs back to back, refilled at this many per
# hour. Every such run connects anew and exports the account's
# authorization to each other data center its files live on: the likeliest
# trigger of a 50-minute FloodWait after ~100 single downloads (ADR-0019).
# One 'download-batch' run costs one run however many files it fetches.
DOWNLOAD_RUNS_PER_HOUR = 20
# Diagnostic record of the last persisted flood wait: which request got it
# and how far a batch had come. Written for a human reading the state file;
# the tool never acts on it.
LAST_FLOOD_KEY = "last_flood"


class RetryLaterError(Exception):
    """Raised when the request cannot run now but may succeed later.

    The CLI maps this to exit code 2 so agents can tell "wait and retry"
    apart from fatal errors.
    """

    def __init__(self, message: str, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"{message}; retry after {math.ceil(retry_after)}s")


def _state_path():
    return config.config_dir() / STATE_FILENAME


def _load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A state file damaged e.g. by a killed process must not brick the
        # tool; losing throttle state is harmless.
        return {}
    if not isinstance(data, dict):
        return {}
    # Guard against JSON-valid damage (e.g. a hand-edited file) as well: a
    # non-numeric value would raise TypeError in the arithmetic below on
    # every run until the file is deleted. The diagnostic flood record is
    # the one non-numeric entry; it is never computed with.
    return {
        key: value
        for key, value in data.items()
        if (isinstance(value, (int, float)) and not isinstance(value, bool))
        or (key == LAST_FLOOD_KEY and isinstance(value, dict))
    }


def _save_state(state: dict) -> None:
    # Best-effort: losing throttle state is harmless (see _load_state), but
    # failing to write it must not crash an otherwise working run — and in
    # record_flood_wait it runs inside an except clause, where an OSError
    # (e.g. the disk a download just filled) would replace the
    # RetryLaterError being raised and turn a retryable failure into a
    # permanent one.
    try:
        config.ensure_config_dir()
        path = _state_path()
        temp_path = path.with_name(path.name + ".tmp")
        temp_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
        temp_path.replace(path)
    except OSError:
        pass


def acquire_lock() -> FileLock:
    """Take the global inter-process lock; the caller must release it."""
    config.ensure_config_dir()
    lock = FileLock(str(config.config_dir() / LOCK_FILENAME))
    try:
        lock.acquire(timeout=LOCK_TIMEOUT)
    except Timeout:
        raise RetryLaterError(
            "another tg-reader process is running", LOCK_TIMEOUT
        ) from None
    return lock


def check_flood_deadline() -> None:
    """Refuse to run while a persisted Telegram flood wait is active."""
    state = _load_state()
    remaining = state.get("flood_until", 0) - time.time()
    # A remaining wait longer than the flood Telegram actually assigned
    # (or than the longest wait it realistically assigns) is impossible:
    # the system clock jumped backwards or the state file is damaged.
    # Cap and rewrite the deadline so a stale one cannot lock the tool
    # out for longer than the flood itself.
    longest = state.get("flood_seconds", MAX_FLOOD_WAIT)
    if not 0 < longest <= MAX_FLOOD_WAIT:
        # The tool never records a duration outside (0, MAX_FLOOD_WAIT];
        # such a value is damage (e.g. a hand-edited file) and must fall
        # back to the global cap — a negative value taken at face value
        # would cancel an active deadline instead of bounding it.
        longest = MAX_FLOOD_WAIT
    if remaining > longest:
        remaining = longest
        state["flood_until"] = time.time() + remaining
        _save_state(state)
    if remaining > 0:
        raise RetryLaterError("Telegram flood wait is active", remaining)


def pace() -> None:
    """Keep runs at least MIN_INTERVAL apart, then record this run.

    Must be called under the lock: it read-modify-writes the state file.
    """
    state = _load_state()
    wait = state.get("last_request_at", 0) + MIN_INTERVAL - time.time()
    if wait > 0:
        # A last_request_at in the future (backwards clock jump, damaged
        # state file) must not stall the run - and the global lock - for
        # longer than the pacing interval itself.
        time.sleep(min(wait, MIN_INTERVAL))
    state["last_request_at"] = time.time()
    _save_state(state)


def take_download_run() -> None:
    """Spend one download run from the hourly budget, or refuse.

    Must be called under the lock: it read-modify-writes the state file.
    """
    state = _load_state()
    now = time.time()
    rate = DOWNLOAD_RUNS_PER_HOUR / 3600
    # Clamped on both ends: a backwards clock jump or a damaged state file
    # must neither mint runs nor lock downloads out for longer than one
    # refill interval.
    elapsed = max(0.0, now - state.get("download_runs_at", now))
    available = min(
        DOWNLOAD_RUNS_PER_HOUR,
        max(0.0, state.get("download_runs", DOWNLOAD_RUNS_PER_HOUR)) + elapsed * rate,
    )
    if available < 1:
        raise RetryLaterError(
            f"the budget of {DOWNLOAD_RUNS_PER_HOUR} download runs per hour is "
            "used up; fetch many files with one 'tg-reader download-batch' run "
            "instead of one 'download' run per file",
            (1 - available) / rate,
        )
    state["download_runs"] = available - 1
    state["download_runs_at"] = now
    _save_state(state)


def record_flood_wait(seconds: float, details: dict | None = None) -> None:
    """Persist the FloodWait deadline so later runs refuse until it expires.

    The duration is stored alongside the deadline: after a backwards clock
    jump it bounds how long the stale deadline may stall the tool. 'details'
    (the request that got the wait, a batch's progress) goes into the
    diagnostic 'last_flood' record.
    """
    state = _load_state()
    now = time.time()
    state["flood_until"] = now + seconds
    state["flood_seconds"] = seconds
    state[LAST_FLOOD_KEY] = {
        "at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "seconds": seconds,
        **(details or {}),
    }
    _save_state(state)
