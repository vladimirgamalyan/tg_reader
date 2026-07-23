"""Interactive authorization: collect API credentials and log in."""

import getpass

from telethon import TelegramClient, utils

from . import config, throttle
from .errors import PermanentError
from .session import (
    FLOOD_WAIT_ERRORS,
    INVALID_SESSION_ERRORS,
    TRANSIENT_TELEGRAM_ERRORS,
    retry_later_from_transient_error,
)


def _prompt_api_id() -> int:
    while True:
        try:
            api_id = int(input("api_id: "))
        except ValueError:
            print("api_id must be a number, try again.")
            continue
        if api_id > 0:
            return api_id
        # Catch it here rather than let the login fail later with an
        # obscure Telethon or server-side error.
        print("api_id must be a positive number, try again.")


def _load_or_prompt_credentials() -> tuple[int, str, bool]:
    """Return API credentials and whether they were prompted in this run."""
    cfg = config.load_config()
    if cfg is not None:
        return cfg["api_id"], cfg["api_hash"], False
    print("API credentials are required.")
    print("Get them at https://my.telegram.org -> 'API development tools'.")
    api_id = _prompt_api_id()
    print("api_hash input will not be displayed while you type.")
    api_hash = getpass.getpass("api_hash: ").strip()
    return api_id, api_hash, True


def _save_prompted_credentials(
    api_id: int, api_hash: str, credentials_were_prompted: bool
) -> None:
    """Persist newly entered credentials only after they have worked."""
    if not credentials_were_prompted:
        return
    config.save_config(api_id, api_hash)
    print(f"Credentials saved to {config.config_path()}")


async def run_auth() -> None:
    """Entry point for the 'auth' command: interactive one-time login.

    The network part runs under the same inter-process lock, flood-wait
    gate and pacing as 'read' and 'download': the SQLite session file must
    never be opened by two processes at once, and an active Telegram flood
    wait is respected here too. Credentials are prompted for before taking
    the lock.
    """
    api_id, api_hash, credentials_were_prompted = _load_or_prompt_credentials()
    lock = throttle.acquire_lock()
    try:
        throttle.check_flood_deadline()
        throttle.pace()
        client = TelegramClient(
            str(config.session_path()),
            api_id,
            api_hash,
            flood_sleep_threshold=throttle.FLOOD_SLEEP_THRESHOLD,
        )
        invalid_session_error = None
        try:
            await client.connect()
            if await client.is_user_authorized():
                # Save as soon as the credentials are proven, before the
                # cosmetic get_me(): a network failure there must not leave
                # an authorized session with unsaved credentials (the next
                # auth run would prompt for them all over again).
                _save_prompted_credentials(api_id, api_hash, credentials_were_prompted)
                me = await client.get_me()
                print(
                    f"Already authorized as {utils.get_display_name(me)} (id={me.id})."
                )
                return
            # Interactive login flow: phone number -> confirmation code -> 2FA password.
            await client.start()
            _save_prompted_credentials(api_id, api_hash, credentials_were_prompted)
            me = await client.get_me()
            print(f"Authorized as {utils.get_display_name(me)} (id={me.id}).")
            print(f"Session stored at {config.session_path()}")
        except FLOOD_WAIT_ERRORS as error:
            throttle.record_flood_wait(error.seconds)
            raise throttle.RetryLaterError(
                "Telegram requested a flood wait", error.seconds
            ) from error
        except INVALID_SESSION_ERRORS as error:
            # The client still holds the session file open here; it is
            # deleted after the disconnect below.
            invalid_session_error = error
        except TRANSIENT_TELEGRAM_ERRORS as error:
            raise retry_later_from_transient_error(error) from error
        finally:
            # Same as in session.py: a failing disconnect must not replace
            # the in-flight error with a bare OSError.
            try:
                await client.disconnect()
            except OSError:
                pass
        if invalid_session_error is not None:
            # A dead session (revoked, AUTH_KEY_DUPLICATED) fails the same
            # way on every connect; deleting the file lets the next run log
            # in from scratch.
            config.session_path().unlink(missing_ok=True)
            raise PermanentError(
                f"The stored session is no longer valid "
                f"({type(invalid_session_error).__name__}); the dead session "
                "file has been deleted. Run 'tg-reader auth' again to log in."
            ) from invalid_session_error
    finally:
        lock.release()
