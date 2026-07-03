"""Message reading logic: chat ID resolution and message formatting."""

from telethon import TelegramClient, utils
from telethon.tl.types import PeerChannel, PeerChat, PeerUser

from . import media
from .session import telegram_session


class ChatNotFoundError(Exception):
    """Raised when a chat ID cannot be resolved to any known chat."""


def candidate_peers(chat_id: int) -> list:
    """Map a user-supplied numeric ID to the MTProto peers it may refer to.

    Negative IDs are Bot-API-style "marked" IDs and identify the peer type
    unambiguously (-100... is a channel, other negatives are small group
    chats). A positive ID is a raw MTProto ID and is ambiguous: it may belong
    to a user, a channel or a small group chat.
    """
    if chat_id < 0:
        real_id, peer_type = utils.resolve_id(chat_id)
        return [peer_type(real_id)]
    return [PeerUser(chat_id), PeerChannel(chat_id), PeerChat(chat_id)]


async def resolve_chat(client: TelegramClient, chat_id: int):
    """Resolve a numeric chat ID to an input entity.

    Tries the session entity cache first; on a miss, walks the account's
    dialogs, which also repopulates the cache so subsequent runs hit it.

    The cache is queried via client.session directly instead of
    client.get_input_entity(): the latter fabricates an InputPeerChat for any
    PeerChat without consulting the cache, which would break the dialog
    fallback for ambiguous positive IDs.
    """
    peers = candidate_peers(chat_id)
    for peer in peers:
        try:
            return client.session.get_input_entity(peer)
        except ValueError:
            continue
    marked_ids = {utils.get_peer_id(peer) for peer in peers}
    async for dialog in client.iter_dialogs():
        if dialog.id in marked_ids:
            return dialog.input_entity
    raise ChatNotFoundError(
        f"Chat {chat_id} not found among this account's dialogs. "
        "Check the ID and make sure the account is a member of the chat."
    )


def message_to_dict(message) -> dict:
    """Convert a Telethon message into the JSON output format."""
    return {
        "id": message.id,
        "date": message.date.isoformat() if message.date else None,
        "sender_id": message.sender_id,
        "sender_name": utils.get_display_name(message.sender) or None,
        "text": message.message or None,
        "grouped_id": message.grouped_id,
        "media": media.media_info(message.media),
    }


async def fetch_messages(
    client: TelegramClient, chat_id: int, limit: int, offset_id: int
) -> list[dict]:
    """Fetch recent messages from a chat, newest first, as plain dicts."""
    entity = await resolve_chat(client, chat_id)
    messages = await client.get_messages(entity, limit=limit, offset_id=offset_id)
    return [message_to_dict(message) for message in messages]


async def run_read(chat_id: int, limit: int, offset_id: int) -> list[dict]:
    """Entry point for the 'read' command: one fetch inside a Telegram session."""
    async with telegram_session() as client:
        return await fetch_messages(client, chat_id, limit, offset_id)
