"""Interactive authorization: collect API credentials and log in."""

from telethon import TelegramClient, utils

from . import config


def _load_or_prompt_credentials() -> tuple[int, str]:
    """Return stored API credentials, prompting for and saving them if missing."""
    cfg = config.load_config()
    if cfg is not None:
        return cfg["api_id"], cfg["api_hash"]
    print("API credentials are required.")
    print("Get them at https://my.telegram.org -> 'API development tools'.")
    api_id = int(input("api_id: "))
    api_hash = input("api_hash: ").strip()
    config.save_config(api_id, api_hash)
    print(f"Credentials saved to {config.config_path()}")
    return api_id, api_hash


async def run_auth() -> None:
    """Entry point for the 'auth' command: interactive one-time login."""
    api_id, api_hash = _load_or_prompt_credentials()
    client = TelegramClient(str(config.session_path()), api_id, api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"Already authorized as {utils.get_display_name(me)} (id={me.id}).")
            return
        # Interactive login flow: phone number -> confirmation code -> 2FA password.
        await client.start()
        me = await client.get_me()
        print(f"Authorized as {utils.get_display_name(me)} (id={me.id}).")
        print(f"Session stored at {config.session_path()}")
    finally:
        await client.disconnect()
