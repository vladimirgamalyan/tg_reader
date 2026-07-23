"""Message reading logic: chat ID resolution and message formatting."""

from telethon import TelegramClient, utils
from telethon.tl.types import (
    MessageEntityTextUrl,
    MessageEntityUrl,
    PeerChannel,
    PeerChat,
    PeerUser,
)

from . import media
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
        "edit_date": message.edit_date.isoformat() if message.edit_date else None,
        "sender_id": message.sender_id,
        "sender_name": utils.get_display_name(message.sender) or None,
        "text": message.message or None,
        "entities": _entities(message),
        "topic_id": _topic_id(reply_to),
        "reply_to_msg_id": _reply_to_msg_id(message, reply_to),
        "is_service": getattr(message, "action", None) is not None,
        "grouped_id": message.grouped_id,
        "media": media.media_info(message.media),
    }


def _entities(message) -> list[dict] | None:
    """Extract URL-bearing message entities as {type, text, url} objects.

    Telegram carries hyperlinks as message entities: a MessageEntityTextUrl
    hides a target URL (its .url attribute) behind arbitrary display text,
    while a MessageEntityUrl marks a plain URL that is its own text. The
    visible message text keeps only the display text, so the hidden targets
    of text_url entities would be lost without this.

    Entity offsets and lengths are counted in UTF-16 code units, not Python
    characters, so the display text is sliced out through a UTF-16 encoding
    (an emoji such as the leading link glyph is one such multi-unit case).

    Only URL entities are emitted for now; other formatting entities (bold,
    mentions, code, ...) are skipped. The field is an extensible container:
    new 'type' values can be added later without a new output field.
    """
    text = message.message
    entities = getattr(message, "entities", None)
    if not text or not entities:
        return None
    utf16 = text.encode("utf-16-le")
    result = []
    for entity in entities:
        if isinstance(entity, MessageEntityTextUrl):
            entity_type, url = "text_url", entity.url
        elif isinstance(entity, MessageEntityUrl):
            entity_type, url = "url", None
        else:
            continue
        start = entity.offset * 2
        display = utf16[start : start + entity.length * 2].decode("utf-16-le")
        result.append({"type": entity_type, "text": display, "url": url or display})
    return result or None


def _topic_id(reply_to) -> int | None:
    """Return the forum topic root message ID for a reply header, if known."""
    if reply_to is None or not getattr(reply_to, "forum_topic", False):
        return None
    if getattr(reply_to, "reply_to_peer_id", None) is not None:
        # A cross-chat quote reply: reply_to_msg_id points into the other
        # chat, so only reply_to_top_id can name the local topic root.
        return getattr(reply_to, "reply_to_top_id", None)
    return getattr(reply_to, "reply_to_top_id", None) or getattr(
        reply_to, "reply_to_msg_id", None
    )


def _reply_to_msg_id(message, reply_to) -> int | None:
    """Return the ID of the message this one actually replies to.

    A header carrying reply_to_peer_id is a cross-chat quote reply: its
    reply_to_msg_id lives in that other chat, so reporting it would break
    the documented meaning "an ID in this chat" (ADR-0009).

    Telegram marks every message inside a forum topic with a reply header:
    a plain post carries forum_topic and reply_to_msg_id pointing at the
    topic root without being a reply. A real reply inside a topic also
    carries reply_to_top_id (the topic root), so its absence tells the two
    apart. The one unavoidable loss: a genuine reply to the topic root
    message itself looks identical to a plain post and is reported as not
    a reply (the Bot API makes the same tradeoff).
    """
    if reply_to is not None and getattr(reply_to, "reply_to_peer_id", None) is not None:
        return None
    if (
        reply_to is not None
        and getattr(reply_to, "forum_topic", False)
        and getattr(reply_to, "reply_to_top_id", None) is None
    ):
        return None
    return message.reply_to_msg_id


async def fetch_messages(
    client: TelegramClient, chat_id: int, limit: int, offset_id: int
) -> list[dict]:
    """Fetch recent messages from a chat, newest first, as plain dicts."""
    entity = await resolve_chat(client, chat_id)
    messages = await client.get_messages(entity, limit=limit, offset_id=offset_id)
    return [message_to_dict(m) for m in messages]


async def run_read(chat_id: int, limit: int, offset_id: int) -> list[dict]:
    """Entry point for the 'read' command."""
    async with telegram_session() as client:
        return await fetch_messages(client, chat_id, limit, offset_id)
