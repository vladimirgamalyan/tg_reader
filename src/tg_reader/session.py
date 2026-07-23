"""Shared Telegram session scaffolding for the non-interactive commands.

Wraps one networked run: config check, inter-process lock, flood-wait
gate, pacing, connect, authorization check, and the mapping of transient
failures (FloodWait, network) to RetryLaterError. 'read' and 'download'
only supply the work done inside the session.
"""

import asyncio
from contextlib import asynccontextmanager

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyDuplicatedError,
    FloodPremiumWaitError,
    FloodWaitError,
    ServerError,
    TimedOutError,
    UnauthorizedError,
)

from . import config, throttle
from .errors import PermanentError


class NotAuthorizedError(PermanentError):
    """Raised when the tool is used before 'tg-reader auth' has been run."""


# Telegram-assigned waits that Telethon re-raises when they exceed
# FLOOD_SLEEP_THRESHOLD. FloodPremiumWaitError (the free-account transfer
# speed limit, seen on large media downloads) is a sibling of
# FloodWaitError, not a subclass; both carry .seconds.
FLOOD_WAIT_ERRORS = (
    FloodWaitError,
    FloodPremiumWaitError,
)

# When the connection dies mid-request and Telethon's automatic reconnect
# fails, the pending request future receives the raw socket-level error, not
# a ConnectionError: any OSError (e.g. socket.gaierror on DNS failure) or an
# asyncio.IncompleteReadError (an EOFError subclass). OSError also covers
# ConnectionError and TimeoutError.
TRANSIENT_TELEGRAM_ERRORS = (
    OSError,
    asyncio.IncompleteReadError,
    ServerError,
    TimedOutError,
)

# The session stopped being valid mid-use: revoked from another device,
# expired, or the session file was copied to a second machine
# (AUTH_KEY_DUPLICATED). Telling the agent to retry would loop forever;
# the fix is a new interactive login.
INVALID_SESSION_ERRORS = (
    UnauthorizedError,
    AuthKeyDuplicatedError,
)


def retry_later_from_transient_error(error: BaseException) -> throttle.RetryLaterError:
    """Map network and transient Telegram RPC errors to the retry contract."""
    return throttle.RetryLaterError(
        f"cannot reach Telegram ({error})", throttle.NETWORK_RETRY_HINT
    )


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
        except FLOOD_WAIT_ERRORS as error:
            throttle.record_flood_wait(error.seconds)
            raise throttle.RetryLaterError(
                "Telegram requested a flood wait", error.seconds
            ) from error
        except INVALID_SESSION_ERRORS as error:
            raise NotAuthorizedError(
                f"The stored session is no longer valid "
                f"({type(error).__name__}). "
                "Run 'tg-reader auth' again (interactive)."
            ) from error
        except TRANSIENT_TELEGRAM_ERRORS as error:
            raise retry_later_from_transient_error(error) from error
        finally:
            # A failing disconnect (likely the same dying network that broke
            # the command) must not replace the in-flight error: a bare
            # OSError escaping here would turn an already-mapped
            # RetryLaterError into a permanent CLI failure.
            try:
                await client.disconnect()
            except OSError:
                pass
    finally:
        lock.release()
