"""Unit tests for the dialog listing.

No network: the Telethon client is injected and replaced with AsyncMock,
same pattern as tests/test_read.py.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tg_reader import config
from tg_reader.chats import dialog_to_dict, list_chats, run_chats


def make_dialog(dialog_id, name, is_user=False, is_group=False, entity=None):
    return SimpleNamespace(
        id=dialog_id,
        name=name,
        is_user=is_user,
        is_group=is_group,
        entity=entity if entity is not None else SimpleNamespace(),
    )


def dialogs_iter(dialogs):
    async def generator():
        for dialog in dialogs:
            yield dialog

    return generator()


def test_dialog_to_dict_user():
    dialog = make_dialog(
        468932869, "John Doe", is_user=True, entity=SimpleNamespace(username="jdoe")
    )

    assert dialog_to_dict(dialog) == {
        "id": 468932869,
        "title": "John Doe",
        "type": "user",
        "username": "jdoe",
    }


def test_dialog_to_dict_small_group_has_no_username():
    # Telethon's Chat type has no username attribute at all.
    dialog = make_dialog(-123456, "Family", is_group=True)

    assert dialog_to_dict(dialog) == {
        "id": -123456,
        "title": "Family",
        "type": "group",
        "username": None,
    }


def test_dialog_to_dict_channel():
    dialog = make_dialog(-1001234567890, "News", entity=SimpleNamespace(username=None))

    assert dialog_to_dict(dialog)["type"] == "channel"


def test_dialog_to_dict_empty_name_is_null():
    dialog = make_dialog(555, "", is_user=True)

    assert dialog_to_dict(dialog)["title"] is None


async def test_list_chats_keeps_order_and_skips_migrated_groups():
    client = AsyncMock()
    client.iter_dialogs = MagicMock(
        return_value=dialogs_iter(
            [make_dialog(2, "B", is_user=True), make_dialog(1, "A", is_user=True)]
        )
    )

    result = await list_chats(client, None)

    assert [chat["id"] for chat in result] == [2, 1]
    client.iter_dialogs.assert_called_once_with(ignore_migrated=True)


async def test_list_chats_query_matches_title_and_username_case_insensitively():
    dialogs = [
        make_dialog(1, "Настолки Самуи", is_group=True),
        make_dialog(
            2, "Sergey", is_user=True, entity=SimpleNamespace(username="samui_bot")
        ),
        make_dialog(3, "Family", is_group=True),
        make_dialog(4, "", is_user=True),
    ]
    client = AsyncMock()
    client.iter_dialogs = MagicMock(side_effect=lambda **_: dialogs_iter(dialogs))

    by_title = await list_chats(client, "САМУИ")
    by_username = await list_chats(client, "Samui")

    assert [chat["id"] for chat in by_title] == [1]
    assert [chat["id"] for chat in by_username] == [2]


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / config.CONFIG_FILENAME).write_text(
        json.dumps({"api_id": 1, "api_hash": "hash"}), encoding="utf-8"
    )
    return tmp_path


async def test_run_chats_lists_dialogs_inside_a_session(config_dir, mocker):
    client = AsyncMock()
    client.is_user_authorized.return_value = True
    client.iter_dialogs = MagicMock(
        return_value=dialogs_iter([make_dialog(1, "A", is_user=True)])
    )
    mocker.patch("tg_reader.session.TelegramClient", return_value=client)

    result = await run_chats(None)

    assert [chat["id"] for chat in result] == [1]
    client.disconnect.assert_awaited_once()
