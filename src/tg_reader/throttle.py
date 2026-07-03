"""Anti-flood protection: inter-process lock, FloodWait persistence, pacing.

Telegram punishes clients that keep sending requests during an assigned
FLOOD_WAIT, so the wait deadline is persisted to disk and every subsequent
run refuses to touch the network until it expires. A single inter-process
lock serializes runs (parallel processes would also corrupt the SQLite
session file), and a minimum interval between runs smooths out bursts from
a misbehaving caller.
"""

import json
import math
import time

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
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A state file damaged e.g. by a killed process must not brick the
        # tool; losing throttle state is harmless.
        return {}


def _save_state(state: dict) -> None:
    config.ensure_config_dir()
    path = _state_path()
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
    temp_path.replace(path)


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
    if remaining > MAX_FLOOD_WAIT:
        # An impossible deadline must not lock the tool out indefinitely:
        # rewrite it as the maximum so it is guaranteed to expire.
        remaining = MAX_FLOOD_WAIT
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


def record_flood_wait(seconds: float) -> None:
    """Persist the FloodWait deadline so later runs refuse until it expires."""
    state = _load_state()
    state["flood_until"] = time.time() + seconds
    _save_state(state)
