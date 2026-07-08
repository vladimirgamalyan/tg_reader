"""Message reading logic: chat ID resolution and message formatting."""

from telethon import TelegramClient, utils
from telethon.tl.types import PeerChannel, PeerChat, PeerUser

from . import cache, media
from .errors import PermanentError
from .session import telegram_session


class ChatNotFoundError(PermanentError):
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


def _is_cached_peer_chat(client: TelegramClient, peer: PeerChat) -> bool:
    """Return whether a small group peer is present in Telethon's entity cache."""
    get_rows = getattr(client.session, "get_entity_rows_by_id", None)
    if get_rows is None:
        return False
    return get_rows(utils.get_peer_id(peer), exact=True) is not None


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
        if isinstance(peer, PeerChat) and not _is_cached_peer_chat(client, peer):
            # Telethon can build InputPeerChat from a bare PeerChat ID without
            # consulting the entity cache. Treat that as a cache miss so raw
            # positive IDs still get disambiguated through the dialog list.
            continue
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
    reply_to = getattr(message, "reply_to", None)
    return {
        "id": message.id,
        "date": message.date.isoformat() if message.date else None,
        "sender_id": message.sender_id,
        "sender_name": utils.get_display_name(message.sender) or None,
        "text": message.message or None,
        "topic_id": _topic_id(reply_to),
        "reply_to_msg_id": message.reply_to_msg_id,
        "is_service": getattr(message, "action", None) is not None,
        "grouped_id": message.grouped_id,
        "media": media.media_info(message.media),
    }


def _topic_id(reply_to) -> int | None:
    """Return the forum topic root message ID for a reply header, if known."""
    if reply_to is None or not getattr(reply_to, "forum_topic", False):
        return None
    return getattr(reply_to, "reply_to_top_id", None) or getattr(
        reply_to, "reply_to_msg_id", None
    )


def cache_chat_id_candidates(chat_id: int) -> list[int]:
    """Marked chat IDs a user-supplied ID may refer to, for cache lookup."""
    return [utils.get_peer_id(peer) for peer in candidate_peers(chat_id)]


async def fetch_messages(
    client: TelegramClient, chat_id: int, limit: int, offset_id: int
) -> tuple[int, list[dict]]:
    """Fetch recent messages from a chat, newest first, as plain dicts.

    Returns the resolved marked chat ID (the cache key) and the messages.
    """
    entity = await resolve_chat(client, chat_id)
    messages = await client.get_messages(entity, limit=limit, offset_id=offset_id)
    return utils.get_peer_id(entity), [message_to_dict(m) for m in messages]


async def run_read(
    chat_id: int, limit: int, offset_id: int, use_cache: bool = True
) -> list[dict]:
    """Entry point for the 'read' command.

    A paginated request whose window is fully covered by the local cache is
    served without touching Telegram (no lock, no pacing, no session). Every
    network fetch refreshes the cache, --no-cache runs included.
    """
    if use_cache:
        cached = cache.lookup(cache_chat_id_candidates(chat_id), limit, offset_id)
        if cached is not None:
            return cached
    async with telegram_session() as client:
        marked_chat_id, messages = await fetch_messages(
            client, chat_id, limit, offset_id
        )
    cache.store(marked_chat_id, messages, limit, offset_id)
    return messages
