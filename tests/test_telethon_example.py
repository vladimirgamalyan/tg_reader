"""Example: unit-testing Telethon code by mocking the client.

Pattern: the function under test receives a TelegramClient through its arguments
(dependency injection), so tests pass an AsyncMock instead of a real client and
never touch the network or Telegram servers. Telethon client methods such as
get_messages / send_message are coroutines, so they are mocked with AsyncMock;
plain attributes (a message's .text) use MagicMock.

`asyncio_mode = "auto"` in pyproject.toml lets `async def test_*` run without the
@pytest.mark.asyncio decorator.

Replace the local `fetch_message_texts` below with an import from your real
module once it exists.
"""

from unittest.mock import AsyncMock, MagicMock


async def fetch_message_texts(client, entity, limit=10):
    """Placeholder for the real reader code under test."""
    messages = await client.get_messages(entity, limit=limit)
    return [message.text for message in messages]


async def test_fetch_message_texts_returns_texts():
    fake_message = MagicMock()
    fake_message.text = "hello world"

    client = AsyncMock()
    client.get_messages.return_value = [fake_message]

    texts = await fetch_message_texts(client, "@somechannel", limit=1)

    assert texts == ["hello world"]
    client.get_messages.assert_awaited_once_with("@somechannel", limit=1)
