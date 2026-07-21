"""Media download logic: fetch one message and save its attachment."""

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
        # Remove any .part left by a previous run killed mid-download, and
        # confirm the directory is writable. Telethon refuses to overwrite an
        # existing file: a surviving .part would divert this download to a
        # "<name> (1).part" sibling, leaving the stale part to be delivered as
        # the result. Doing the filesystem check here also keeps the session
        # layer's broad OSError handler from misreporting an unwritable
        # --output directory as "cannot reach Telegram" (a retryable error).
        part.unlink(missing_ok=True)
        part.touch()
        part.unlink()
    except OSError as error:
        raise DownloadError(
            f"Output directory {output_dir} is not usable ({error})."
        ) from error
    try:
        downloaded = await client.download_media(message, file=str(part))
        if downloaded is None:
            raise DownloadError(f"Telegram returned no file for message {msg_id}.")
        downloaded_size = part.stat().st_size
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
        part.unlink(missing_ok=True)
    return {
        "message_id": msg_id,
        "type": info["type"],
        "file": str(target.resolve()),
        "size_bytes": target.stat().st_size,
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
