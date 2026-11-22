"""Batch download: the media of many messages of one chat in one run.

A loop of single 'download' runs pays for a fresh connection per file and,
for a file stored on another data center, a fresh authorization export
there; about a hundred such runs in a row earned a 50-minute FloodWait. One
batch run pays that once, because Telethon keeps an exported authorization
for the client's lifetime (ADR-0019). Within the run, transfers are paused
and capped, and a long FloodWait ends the run with a report instead of a
bare error, so running the same command later continues where it stopped.
"""

import asyncio
import logging
import os
import random
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import BadRequestError

from . import throttle
from .download import (
    DEFAULT_MAX_SIZE_MB,
    DownloadError,
    media_to_download,
    save_media,
)
from .read import resolve_chat
from .session import (
    FLOOD_WAIT_ERRORS,
    TRANSIENT_TELEGRAM_ERRORS,
    flood_request_name,
    telegram_session,
)

# Pause between two transfers, in seconds, and its allowed range.
DEFAULT_PAUSE = 3
MIN_PAUSE = 1
MAX_PAUSE = 60
# Random extra on top of each pause, as a fraction of it (3 s -> 3..4.5 s),
# so transfers do not tick at a machine-regular interval.
PAUSE_JITTER = 0.5
# Caps on one run: transfers started, and their total declared size in MB.
DEFAULT_MAX_FILES = 200
MAX_MAX_FILES = 1000
DEFAULT_MAX_TOTAL_MB = 1024
MAX_MAX_TOTAL_MB = 4096
# Retry hint after a run stopped at a cap: the advised rest before the next
# run. Advisory only; the download-run budget is what is enforced.
RUN_LIMIT_RETRY = 900
# Message IDs per GetMessages request: Telegram's own maximum.
FETCH_CHUNK = 100
# Message IDs one run accepts. IDs without a file to transfer (not found,
# no media) cost GetMessages requests but no --max-files slot, so this is
# what bounds the requests of a mistaken invocation, e.g. a whole ID range:
# at most MAX_IDS / FETCH_CHUNK of them (ADR-0020).
MAX_IDS = 5000
# A fetched message older than this is fetched again before its file is
# transferred: file references expire after a few hours, and a run on a
# slow link can take that long.
REFETCH_AFTER = 1800

# Refusals that are a property of the message itself: retrying cannot help.
_SKIP_REASONS = {"not_found", "no_media", "unknown_size", "too_large"}

_MB = 1024 * 1024


@dataclass(frozen=True)
class BatchLimits:
    max_size_mb: int = DEFAULT_MAX_SIZE_MB
    pause: float = DEFAULT_PAUSE
    max_files: int = DEFAULT_MAX_FILES
    max_total_mb: int = DEFAULT_MAX_TOTAL_MB


def find_downloaded(output_dir: Path, chat_id: int) -> dict[int, tuple[Path, int]]:
    """Map message IDs to the files (and sizes) already in output_dir.

    A file counts when its name starts with the '<chat_id>_<msg_id>_'
    prefix every download of this chat gets; a '.part' file is an
    unfinished transfer and does not count. Local only, no network.
    """
    prefix = re.compile(rf"{re.escape(str(chat_id))}_([0-9]+)_")
    try:
        with os.scandir(output_dir) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise DownloadError(
            f"Output directory {output_dir} is not usable ({error}).",
            "local_file_error",
        ) from error
    found = {}
    for entry in entries:
        match = prefix.match(entry.name)
        if match is None or entry.name.endswith(".part"):
            continue
        try:
            if not entry.is_file():
                continue
            size = entry.stat().st_size
        except OSError:
            continue
        # Sorted by name, so the media file wins over a file named after it
        # (e.g. a '<name>.transcript.json' a caller keeps alongside).
        found.setdefault(int(match.group(1)), (Path(entry.path), size))
    return found


class _FloodSleepNotes(logging.Handler):
    """Echo the short flood waits Telethon sleeps through to stderr.

    Telethon sleeps through a FloodWait of up to FLOOD_SLEEP_THRESHOLD
    seconds by itself and only logs it; a batch surfaces it, with how far
    the run had got, because it shows the run brushing against Telegram's
    limits.
    """

    _LOGGER = "telethon.client.users"

    def __init__(self, downloaded_files):
        super().__init__()
        self._downloaded_files = downloaded_files
        self._saved_level = logging.NOTSET

    def emit(self, record):
        message = record.getMessage()
        if "flood wait" in message:
            print(
                f"note: {message}, after {self._downloaded_files()} files "
                "downloaded in this run",
                file=sys.stderr,
            )

    def __enter__(self):
        logger = logging.getLogger(self._LOGGER)
        self._saved_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(self)
        return self

    def __exit__(self, *exc_info):
        logger = logging.getLogger(self._LOGGER)
        logger.removeHandler(self)
        logger.setLevel(self._saved_level)


def _refused(error: DownloadError) -> dict:
    status = "skipped" if error.reason in _SKIP_REASONS else "failed"
    return {"status": status, "reason": error.reason, "error": str(error)}


def _stopped(reason: str, error: str, retry_after: float | None) -> dict:
    return {"reason": reason, "error": error, "retry_after": retry_after}


async def _pause(limits: BatchLimits) -> None:
    """Sleep the pause between two requests, plus its random extra."""
    await asyncio.sleep(limits.pause * random.uniform(1, 1 + PAUSE_JITTER))


async def _download_pending(
    client: TelegramClient,
    chat_id: int,
    pending: list[int],
    output_dir: Path,
    limits: BatchLimits,
    results: dict,
) -> dict | None:
    """Download the pending messages in the given order, filling results.

    Returns None when every message got an outcome, otherwise why the run
    stopped early; messages left without an outcome are reported as
    pending, and the next run picks them up.
    """
    transfers = 0
    downloaded_files = 0
    downloaded_bytes = 0
    with _FloodSleepNotes(lambda: downloaded_files):
        try:
            entity = await resolve_chat(client, chat_id)
            fetched = {}
            fetched_at = 0.0
            fetches = 0
            for index, msg_id in enumerate(pending):
                if (
                    msg_id not in fetched
                    or time.monotonic() - fetched_at > REFETCH_AFTER
                ):
                    # Paced like transfers: a run of IDs without media
                    # would otherwise fetch back to back.
                    if fetches:
                        await _pause(limits)
                    fetches += 1
                    chunk = pending[index : index + FETCH_CHUNK]
                    messages = await client.get_messages(entity, ids=chunk)
                    fetched = dict(zip(chunk, messages))
                    fetched_at = time.monotonic()
                message = fetched[msg_id]
                if message is None:
                    results[msg_id] = _refused(
                        DownloadError(
                            f"Message {msg_id} not found in chat {chat_id}.",
                            "not_found",
                        )
                    )
                    continue
                try:
                    info = media_to_download(message, limits.max_size_mb)
                except DownloadError as error:
                    results[msg_id] = _refused(error)
                    continue
                if transfers >= limits.max_files:
                    return _stopped(
                        "max_files",
                        f"the run limit of {limits.max_files} files is reached",
                        RUN_LIMIT_RETRY,
                    )
                if downloaded_bytes + info["size_bytes"] > limits.max_total_mb * _MB:
                    return _stopped(
                        "max_total",
                        f"the run limit of {limits.max_total_mb} MB is reached",
                        RUN_LIMIT_RETRY,
                    )
                if transfers:
                    await _pause(limits)
                transfers += 1
                try:
                    saved = await save_media(
                        client, chat_id, message, info, output_dir, limits.max_size_mb
                    )
                except DownloadError as error:
                    results[msg_id] = _refused(error)
                    if error.reason == "local_file_error":
                        # A full disk or an unwritable directory fails every
                        # later file the same way, after spending requests.
                        return _stopped("local_file_error", str(error), None)
                    continue
                except BadRequestError as error:
                    # Telegram rejected a request about this one file (e.g.
                    # its file reference); the next message may well work.
                    results[msg_id] = {
                        "status": "failed",
                        "reason": "telegram_error",
                        "error": str(error),
                    }
                    continue
                results[msg_id] = {
                    "status": "downloaded",
                    "type": saved["type"],
                    "file": saved["file"],
                    "size_bytes": saved["size_bytes"],
                }
                downloaded_files += 1
                downloaded_bytes += saved["size_bytes"]
        except FLOOD_WAIT_ERRORS as error:
            # Waits up to FLOOD_SLEEP_THRESHOLD never get here: Telethon
            # sleeps through them in place.
            request = flood_request_name(error)
            throttle.record_flood_wait(
                error.seconds,
                {
                    "request": request,
                    "command": "download-batch",
                    "files_downloaded": downloaded_files,
                    "bytes_downloaded": downloaded_bytes,
                },
            )
            return _stopped(
                "flood_wait",
                f"Telegram requested a flood wait on {request} after "
                f"{downloaded_files} files downloaded in this run",
                error.seconds,
            )
        except TRANSIENT_TELEGRAM_ERRORS as error:
            return _stopped(
                "network",
                f"cannot reach Telegram ({error})",
                throttle.NETWORK_RETRY_HINT,
            )
    return None


def _report(msg_ids: list[int], results: dict, stopped: dict | None) -> dict:
    entries = [
        {"message_id": msg_id, **results[msg_id]}
        if msg_id in results
        else {"message_id": msg_id, "status": "pending"}
        for msg_id in msg_ids
    ]
    counts = Counter(entry["status"] for entry in entries)
    return {
        "results": entries,
        "summary": {
            "requested": len(entries),
            "downloaded": counts["downloaded"],
            "skipped": counts["skipped"],
            "failed": counts["failed"],
            "pending": counts["pending"],
            "downloaded_bytes": sum(
                entry["size_bytes"]
                for entry in entries
                if entry["status"] == "downloaded"
            ),
        },
        "stopped": stopped,
    }


async def run_download_batch(
    chat_id: int, msg_ids: list[int], output_dir: Path, limits: BatchLimits
) -> dict:
    """Entry point for 'download-batch': fetch what is missing, return the report.

    Messages whose file is already in output_dir are reported without a
    single request; when nothing else is left, the run does not connect at
    all. Once connected, the report comes back whatever stopped the run,
    except errors no retry fixes (an invalid session) or that strike while
    connecting.
    """
    downloaded = find_downloaded(output_dir, chat_id)
    results = {}
    for msg_id in msg_ids:
        if msg_id in downloaded:
            path, size = downloaded[msg_id]
            results[msg_id] = {
                "status": "skipped",
                "reason": "already_downloaded",
                "file": str(path.resolve()),
                "size_bytes": size,
            }
    pending = [msg_id for msg_id in msg_ids if msg_id not in results]
    stopped = None
    if pending:
        async with telegram_session(download=True) as client:
            stopped = await _download_pending(
                client, chat_id, pending, output_dir, limits, results
            )
    return _report(msg_ids, results, stopped)
