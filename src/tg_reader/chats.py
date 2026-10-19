"""Dialog listing: the account's chats with their IDs, for finding a chat by name."""

from telethon import TelegramClient

from .session import telegram_session


def dialog_to_dict(dialog) -> dict:
    """Convert a Telethon dialog into the JSON output format."""
    if dialog.is_user:
        chat_type = "user"
    elif dialog.is_group:
        chat_type = "group"
    else:
        chat_type = "channel"
    return {
        "id": dialog.id,
        "title": dialog.name or None,
        "type": chat_type,
        # Small group chats have no username attribute at all.
        "username": getattr(dialog.entity, "username", None),
    }


async def list_chats(client: TelegramClient, query: str | None) -> list[dict]:
    """List dialogs (archived included), in Telegram's dialog order.

    With a query, only chats whose title or username contains it
    (case-insensitively) are kept: the full list of an account with a few
    hundred chats is tens of KB, too much for an agent's tool output.

    Small groups that were upgraded to supergroups are skipped, as the
    official apps do: the dead group keeps its title, and picking it by
    name would read a chat that no longer gets messages.
    """
    chats = [
        dialog_to_dict(dialog)
        async for dialog in client.iter_dialogs(ignore_migrated=True)
    ]
    if query is None:
        return chats
    needle = query.casefold()
    return [
        chat
        for chat in chats
        if needle in (chat["title"] or "").casefold()
        or needle in (chat["username"] or "").casefold()
    ]


async def run_chats(query: str | None) -> list[dict]:
    """Entry point for the 'chats' command."""
    async with telegram_session() as client:
        return await list_chats(client, query)
