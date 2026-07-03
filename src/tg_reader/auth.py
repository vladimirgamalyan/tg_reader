"""Interactive authorization: collect API credentials and log in."""

from telethon import TelegramClient, utils
from telethon.errors import FloodWaitError

from . import config, throttle


def _prompt_api_id() -> int:
    while True:
        try:
            return int(input("api_id: "))
        except ValueError:
            print("api_id must be a number, try again.")


def _load_or_prompt_credentials() -> tuple[int, str]:
    """Return stored API credentials, prompting for and saving them if missing."""
    cfg = config.load_config()
    if cfg is not None:
        return cfg["api_id"], cfg["api_hash"]
    print("API credentials are required.")
    print("Get them at https://my.telegram.org -> 'API development tools'.")
    api_id = _prompt_api_id()
    api_hash = input("api_hash: ").strip()
    config.save_config(api_id, api_hash)
    print(f"Credentials saved to {config.config_path()}")
    return api_id, api_hash


async def run_auth() -> None:
    """Entry point for the 'auth' command: interactive one-time login.

    The network part runs under the same inter-process lock, flood-wait
    gate and pacing as 'read' and 'download': the SQLite session file must
    never be opened by two processes at once, and an active Telegram flood
    wait is respected here too. Credentials are prompted for before taking
    the lock.
    """
    api_id, api_hash = _load_or_prompt_credentials()
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
        await client.connect()
        try:
            if await client.is_user_authorized():
                me = await client.get_me()
                print(
                    f"Already authorized as {utils.get_display_name(me)} (id={me.id})."
                )
                return
            # Interactive login flow: phone number -> confirmation code -> 2FA password.
            await client.start()
            me = await client.get_me()
            print(f"Authorized as {utils.get_display_name(me)} (id={me.id}).")
            print(f"Session stored at {config.session_path()}")
        except FloodWaitError as error:
            throttle.record_flood_wait(error.seconds)
            raise throttle.RetryLaterError(
                "Telegram requested a flood wait", error.seconds
            ) from error
        finally:
            await client.disconnect()
    finally:
        lock.release()
