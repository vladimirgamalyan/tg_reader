"""Local message archive: incremental sync of one chat into a JSONL file."""

import json
from contextlib import suppress
from datetime import date, datetime, timezone
from pathlib import Path

from telethon import TelegramClient

from . import config, throttle
from .errors import PermanentError
from .read import message_to_dict, resolve_chat
from .session import telegram_session

ARCHIVE_DIRNAME = "archive"


class SyncError(PermanentError):
    """Raised when the archive cannot be synced as requested (permanent)."""


def archive_path(marked_chat_id: int) -> Path:
    return config.config_dir() / ARCHIVE_DIRNAME / f"{marked_chat_id}.jsonl"


def _load_lines(path: Path) -> list[str]:
    """Return the archived messages as JSON lines, oldest first.

    Splits on "\\n" only: str.splitlines() would also break on U+2028 and
    the other separators json.dumps(ensure_ascii=False) leaves unescaped
    inside message text.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as error:
        # Must not escape as a bare OSError: the session layer would misread
        # it as a network failure and tell the caller to retry (ADR-0008).
        raise SyncError(f"Cannot read the archive {path} ({error}).") from error
    return [line for line in text.split("\n") if line]


def _save_lines(path: Path, lines: list[str]) -> None:
    """Write the archive via a temporary file, so a crash keeps the old one."""
    temp_path = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(exist_ok=True)
        temp_path.write_text(
            "".join(f"{line}\n" for line in lines), encoding="utf-8", newline="\n"
        )
        temp_path.replace(path)
    except OSError as error:
        raise SyncError(f"Cannot write the archive {path} ({error}).") from error


async def sync_chat(client: TelegramClient, chat_id: int, since: date | None) -> dict:
    """Append the chat's messages newer than the last archived one.

    The archive holds every message from the first sync's start date up to
    the latest sync, oldest first and without gaps: each sync fetches in
    ascending order, starting right after the last archived message. That
    is why 'since' only starts a new archive and is ignored afterwards.
    """
    entity = await resolve_chat(client, chat_id)
    # Named by the resolved marked ID, so the raw and the -100... form of
    # the same chat share one archive.
    path = archive_path(await client.get_peer_id(entity))
    lines = _load_lines(path)
    if lines:
        try:
            start = {"min_id": json.loads(lines[-1])["id"]}
        except (ValueError, KeyError, TypeError) as error:
            raise SyncError(
                f"The archive {path} is damaged ({error}); delete it and "
                "sync again with --since."
            ) from error
    elif since is None:
        raise SyncError(
            f"Chat {chat_id} has no archive yet; pass --since YYYY-MM-DD to start one."
        )
    else:
        start = {
            "offset_date": datetime(
                since.year, since.month, since.day, tzinfo=timezone.utc
            )
        }
    new_lines = []
    try:
        async for message in client.iter_messages(
            entity, reverse=True, wait_time=throttle.MIN_INTERVAL, **start
        ):
            new_lines.append(json.dumps(message_to_dict(message), ensure_ascii=False))
    except BaseException:
        # Keep what an interrupted fetch (a long flood wait, a dropped
        # connection) already got, so the next sync continues after it
        # instead of fetching it again. Best-effort: a failed save must not
        # replace the error in flight, or the session layer would no longer
        # see (and persist) a flood wait.
        if new_lines:
            with suppress(SyncError):
                _save_lines(path, lines + new_lines)
        raise
    if new_lines:
        _save_lines(path, lines + new_lines)
    return {
        "file": str(path),
        "added": len(new_lines),
        "total": len(lines) + len(new_lines),
    }


async def run_sync(chat_id: int, since: date | None) -> dict:
    """Entry point for the 'sync' command.

    A long sync holds the inter-process lock for its whole duration, like
    a large download: one process talking to Telegram at a time.
    """
    async with telegram_session() as client:
        return await sync_chat(client, chat_id, since)
