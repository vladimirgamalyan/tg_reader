"""Media download logic: fetch one message and save its attachment."""

import errno
import os
from pathlib import Path

from telethon import TelegramClient

from .errors import PermanentError
from .media import build_filename, media_info
from .read import resolve_chat
from .session import telegram_session

# Default --max-size value, in MB (MiB); protects an agent from
# accidentally pulling a multi-GB video.
DEFAULT_MAX_SIZE_MB = 100

_MB = 1024 * 1024


class DownloadError(PermanentError):
    """Raised when the message cannot provide the requested file (permanent)."""


# Errnos a local file write fails with; no socket operation raises these.
# They identify a filesystem problem even when the OSError carries no
# filename: Python attaches the path to open()/stat() errors but not to
# write() errors, and disk-full strikes mid-write. EDQUOT is POSIX-only,
# hence the getattr fallback.
_LOCAL_FILE_ERRNOS = {
    errno.ENOSPC,
    errno.EFBIG,
    getattr(errno, "EDQUOT", errno.ENOSPC),
}


def _abort_when_over_limit(msg_id: int, max_size_bytes: int, max_size_mb: int):
    """Progress callback that aborts the transfer once it exceeds the limit.

    The pre-transfer check trusts the size Telegram declares; this guard
    covers a declared size that turns out to be wrong, so a lying size
    cannot cost unbounded traffic and disk — without it the .part file
    would grow to the real size before the post-download check rejects it
    (ADR-0010).
    """

    def check(received_bytes: int, _total) -> None:
        if received_bytes > max_size_bytes:
            raise DownloadError(
                f"Download of message {msg_id} was aborted at "
                f"{received_bytes / _MB:.1f} MB, which exceeds the "
                f"{max_size_mb} MB limit; pass --max-size to raise it."
            )

    return check


def _is_local_file_error(error: OSError) -> bool:
    """Tell a local filesystem failure apart from a network failure.

    The session layer maps any OSError escaping the command to "cannot
    reach Telegram, retry later" (exit code 2); a disk-full or
    access-denied error must not take that route, or the caller would
    retry a permanent local failure forever. Socket-level errors carry
    neither a filename nor a filesystem errno.
    """
    return error.filename is not None or error.errno in _LOCAL_FILE_ERRNOS


async def download_to_dir(
    client: TelegramClient,
    chat_id: int,
    msg_id: int,
    output_dir: Path,
    max_size_mb: int,
) -> dict:
    """Download the media of one message into output_dir, return a summary.

    The message is re-fetched here: Telegram file references expire after a
    few hours, so data captured by an earlier 'read' run cannot be reused.
    The file is written to a '.part' name and renamed on success, so an
    interrupted run never leaves a half-written file under the final name;
    an existing file is overwritten (same message - same path, same content).
    """
    entity = await resolve_chat(client, chat_id)
    message = await client.get_messages(entity, ids=msg_id)
    if message is None:
        raise DownloadError(f"Message {msg_id} not found in chat {chat_id}.")
    info = media_info(message.media)
    if info is None:
        raise DownloadError(f"Message {msg_id} has no downloadable media.")
    size = info["size_bytes"]
    max_size_bytes = max_size_mb * _MB
    if size is None:
        raise DownloadError(
            f"Media size of message {msg_id} is unknown, so it cannot be checked "
            f"against the {max_size_mb} MB limit."
        )
    if size > max_size_bytes:
        raise DownloadError(
            f"Media of message {msg_id} is {size / _MB:.1f} MB, which exceeds "
            f"the {max_size_mb} MB limit; pass --max-size to raise it."
        )
    target = output_dir / build_filename(chat_id, msg_id, info)
    part = target.with_name(target.name + ".part")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        # Probe the directory with a touch/unlink round-trip: an unwritable
        # --output must fail here, as a permanent error, not from inside the
        # transfer where the session layer's broad OSError handler would
        # misreport it as "cannot reach Telegram" (a retryable error). The
        # leading unlink clears any .part left by a previous run killed
        # mid-download, so the probe exercises creating a fresh file.
        part.unlink(missing_ok=True)
        part.touch()
        part.unlink()
    except OSError as error:
        raise DownloadError(
            f"Output directory {output_dir} is not usable ({error})."
        ) from error
    try:
        try:
            downloaded = await client.download_media(
                message,
                file=str(part),
                progress_callback=_abort_when_over_limit(
                    msg_id, max_size_bytes, max_size_mb
                ),
            )
            if downloaded is None:
                raise DownloadError(f"Telegram returned no file for message {msg_id}.")
            downloaded_size = part.stat().st_size
        except OSError as error:
            # Telethon surfaces local file-write failures (disk full, an
            # antivirus locking the .part) and network failures alike as
            # OSError; only network ones may propagate to the session
            # layer's retry mapping.
            if not _is_local_file_error(error):
                raise
            raise DownloadError(
                f"Cannot write the download to {part} ({error})."
            ) from error
        if downloaded_size > max_size_bytes:
            raise DownloadError(
                f"Downloaded media of message {msg_id} is "
                f"{downloaded_size / _MB:.1f} MB, which exceeds the "
                f"{max_size_mb} MB limit."
            )
        try:
            os.replace(part, target)
        except OSError as error:
            raise DownloadError(
                f"Cannot move the download into place at {target} ({error})."
            ) from error
    finally:
        # Best-effort cleanup: a failure here (e.g. an antivirus still
        # holding the .part open) must not replace the download error being
        # raised - a bare OSError would be misread by the session layer as
        # a transient network failure and retried forever. The next run's
        # probe clears or reports the leftover.
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
    return {
        "message_id": msg_id,
        "type": info["type"],
        "file": str(target.resolve()),
        # The size measured on the .part before the rename: a stat() of the
        # final path would sit outside the OSError classification above, so
        # a file vanishing right after the rename (antivirus quarantine)
        # would be misreported as a transient network failure and retried
        # forever.
        "size_bytes": downloaded_size,
    }


async def run_download(
    chat_id: int, msg_id: int, output_dir: Path, max_size_mb: int
) -> dict:
    """Entry point for the 'download' command: one download inside a session.

    A large download holds the inter-process lock for its whole duration by
    design: one process talking to Telegram at a time.
    """
    async with telegram_session() as client:
        return await download_to_dir(client, chat_id, msg_id, output_dir, max_size_mb)
