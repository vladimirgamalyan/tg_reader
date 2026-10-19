"""Unit tests for the forum topic listing.

No network: the Telethon client is injected and replaced with AsyncMock,
same pattern as tests/test_read.py.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import ChannelForumMissingError, PeerIdInvalidError
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import (
    ForumTopic,
    ForumTopicDeleted,
    InputPeerChannel,
    PeerNotifySettings,
    PeerUser,
)

from tg_reader import config
from tg_reader.topics import NotAForumError, list_topics, run_topics

ENTITY = InputPeerChannel(1232768830, access_hash=0)


def make_topic(topic_id, title, unread_count=0):
    return ForumTopic(
        id=topic_id,
        date=None,
        peer=PeerUser(1),
        title=title,
        icon_color=0,
        top_message=topic_id,
        read_inbox_max_id=0,
        read_outbox_max_id=0,
        unread_count=unread_count,
        unread_mentions_count=0,
        unread_reactions_count=0,
        unread_poll_votes_count=0,
        from_id=PeerUser(1),
        notify_settings=PeerNotifySettings(),
    )


def make_client(topics=(), error=None):
    client = AsyncMock()
    client.session.get_input_entity = MagicMock(return_value=ENTITY)
    if error is not None:
        client.side_effect = error
    else:
        client.return_value = SimpleNamespace(topics=list(topics))
    return client


async def test_list_topics_returns_ids_titles_and_unread_counts():
    client = make_client(
        [make_topic(1, "General"), make_topic(42, "Rent", unread_count=3)]
    )

    result = await list_topics(client, -1001232768830)

    assert result == [
        {"id": 1, "title": "General", "unread_count": 0},
        {"id": 42, "title": "Rent", "unread_count": 3},
    ]
    request = client.await_args.args[0]
    assert isinstance(request, GetForumTopicsRequest)
    assert request.peer == ENTITY


async def test_list_topics_skips_deleted_topics():
    client = make_client([ForumTopicDeleted(id=7), make_topic(42, "Rent")])

    result = await list_topics(client, -1001232768830)

    assert [topic["id"] for topic in result] == [42]


@pytest.mark.parametrize(
    "error",
    [
        ChannelForumMissingError(request=None),
        PeerIdInvalidError(request=None),
    ],
)
async def test_list_topics_of_a_non_forum_is_a_clear_permanent_error(error):
    client = make_client(error=error)

    with pytest.raises(NotAForumError, match="not a forum"):
        await list_topics(client, -1001232768830)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / config.CONFIG_FILENAME).write_text(
        json.dumps({"api_id": 1, "api_hash": "hash"}), encoding="utf-8"
    )
    return tmp_path


async def test_run_topics_lists_topics_inside_a_session(config_dir, mocker):
    client = make_client([make_topic(1, "General")])
    client.is_user_authorized.return_value = True
    mocker.patch("tg_reader.session.TelegramClient", return_value=client)

    result = await run_topics(-1001232768830)

    assert [topic["id"] for topic in result] == [1]
    client.disconnect.assert_awaited_once()
