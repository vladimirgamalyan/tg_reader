"""Interactive authorization: collect API credentials and log in."""

import getpass

from telethon import TelegramClient, utils
from telethon.errors import FloodWaitError

from . import config, throttle
from .session import TRANSIENT_TELEGRAM_ERRORS, retry_later_from_transient_error


def _prompt_api_id() -> int:
    while True:
        try:
            return int(input("api_id: "))
        except ValueError:
            print("api_id must be a number, try again.")


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
        try:
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                _save_prompted_credentials(api_id, api_hash, credentials_were_prompted)
                print(
                    f"Already authorized as {utils.get_display_name(me)} (id={me.id})."
                )
                return
            # Interactive login flow: phone number -> confirmation code -> 2FA password.
            await client.start()
            me = await client.get_me()
            _save_prompted_credentials(api_id, api_hash, credentials_were_prompted)
            print(f"Authorized as {utils.get_display_name(me)} (id={me.id}).")
            print(f"Session stored at {config.session_path()}")
        except FloodWaitError as error:
            throttle.record_flood_wait(error.seconds)
            raise throttle.RetryLaterError(
                "Telegram requested a flood wait", error.seconds
            ) from error
        except TRANSIENT_TELEGRAM_ERRORS as error:
            raise retry_later_from_transient_error(error) from error
        finally:
            await client.disconnect()
    finally:
        lock.release()
