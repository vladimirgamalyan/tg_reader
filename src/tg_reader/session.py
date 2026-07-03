"""Shared Telegram session scaffolding for the non-interactive commands.

Wraps one networked run: config check, inter-process lock, flood-wait
gate, pacing, connect, authorization check, and the mapping of transient
failures (FloodWait, network) to RetryLaterError. 'read' and 'download'
only supply the work done inside the session.
"""

from contextlib import asynccontextmanager

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from . import config, throttle


class NotAuthorizedError(Exception):
    """Raised when the tool is used before 'tg-reader auth' has been run."""


@asynccontextmanager
async def telegram_session():
    """Yield a connected, authorized client under the inter-process lock.

    The whole session runs under the lock: Telegram is only ever accessed
    by one tg-reader process at a time, and the SQLite session file is
    never opened concurrently.
    """
    cfg = config.load_config()
    if cfg is None:
        raise NotAuthorizedError(
            "No configuration found. Run 'tg-reader auth' first (interactive)."
        )
    lock = throttle.acquire_lock()
    try:
        throttle.check_flood_deadline()
        throttle.pace()
        client = TelegramClient(
            str(config.session_path()),
            cfg["api_id"],
            cfg["api_hash"],
            flood_sleep_threshold=throttle.FLOOD_SLEEP_THRESHOLD,
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise NotAuthorizedError(
                    "Not authorized. Run 'tg-reader auth' first (interactive)."
                )
            yield client
        except FloodWaitError as error:
            throttle.record_flood_wait(error.seconds)
            raise throttle.RetryLaterError(
                "Telegram requested a flood wait", error.seconds
            ) from error
        except (ConnectionError, TimeoutError) as error:
            raise throttle.RetryLaterError(
                f"cannot reach Telegram ({error})", throttle.NETWORK_RETRY_HINT
            ) from error
        finally:
            await client.disconnect()
    finally:
        lock.release()
