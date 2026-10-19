"""Forum topic listing: topic IDs and titles of one forum group."""

from telethon import TelegramClient
from telethon.errors import ChannelForumMissingError, PeerIdInvalidError
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import ForumTopic

from .errors import PermanentError
from .read import resolve_chat
from .session import telegram_session

# One GetForumTopics request; a forum with more topics is listed up to its
# most recently active ones.
MAX_TOPICS = 100


class NotAForumError(PermanentError):
    """Raised when the chat has no topics (not a forum group)."""


async def list_topics(client: TelegramClient, chat_id: int) -> list[dict]:
    """List the topics of a forum group, in Telegram's topic order."""
    entity = await resolve_chat(client, chat_id)
    try:
        result = await client(
            GetForumTopicsRequest(
                peer=entity,
                offset_date=None,
                offset_id=0,
                offset_topic=0,
                limit=MAX_TOPICS,
            )
        )
    except (ChannelForumMissingError, PeerIdInvalidError) as error:
        # Telegram answers PEER_ID_INVALID for private chats and small
        # groups, CHANNEL_FORUM_MISSING for supergroups and channels.
        raise NotAForumError(
            f"Chat {chat_id} is not a forum: it has no topics."
        ) from error
    return [
        {
            "id": topic.id,
            "title": topic.title,
            "unread_count": topic.unread_count,
        }
        for topic in result.topics
        # ForumTopicDeleted carries only an ID.
        if isinstance(topic, ForumTopic)
    ]


async def run_topics(chat_id: int) -> list[dict]:
    """Entry point for the 'topics' command."""
    async with telegram_session() as client:
        return await list_topics(client, chat_id)
