"""Unit tests for the local message cache (SQLite storage and coverage)."""

import sqlite3

import pytest

from tg_reader import cache, config


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    return tmp_path


def make_message(msg_id, **overrides):
    message = {
        "id": msg_id,
        "date": "2026-07-03T12:34:56+00:00",
        "sender_id": 111222333,
        "sender_name": "John Doe",
        "text": f"message {msg_id}",
        "entities": None,
        "topic_id": None,
        "reply_to_msg_id": None,
        "is_service": False,
        "grouped_id": None,
        "media": None,
    }
    message.update(overrides)
    return message


def coverage_rows():
    conn = sqlite3.connect(str(cache.cache_path()))
    try:
        return conn.execute(
            "SELECT min_id, max_id FROM coverage ORDER BY min_id"
        ).fetchall()
    finally:
        conn.close()


# --- store/lookup roundtrip ---


def test_store_then_lookup_roundtrip(cache_dir):
    media = {
        "type": "document",
        "filename": "отчёт.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 2048,
    }
    messages = [
        make_message(7, media=media, grouped_id=777),
        make_message(6, text=None, sender_id=None, sender_name=None, date=None),
        make_message(5, topic_id=1, reply_to_msg_id=3, is_service=True),
    ]
    cache.store(-100123, messages, limit=3, offset_id=8)

    assert cache.lookup([-100123], 3, 8) == messages


def test_store_then_lookup_preserves_entities(cache_dir):
    entities = [
        {"type": "text_url", "text": "🔗 Оригинал", "url": "https://site/post"},
        {"type": "url", "text": "https://t.me/x", "url": "https://t.me/x"},
    ]
    cache.store(-100123, [make_message(5, entities=entities)], limit=1, offset_id=6)

    assert cache.lookup([-100123], 1, 6)[0]["entities"] == entities


def test_refetch_replaces_cached_message(cache_dir):
    cache.store(-100123, [make_message(5, text="old")], limit=1, offset_id=6)
    cache.store(-100123, [make_message(5, text="edited")], limit=1, offset_id=6)

    assert cache.lookup([-100123], 1, 6)[0]["text"] == "edited"


# --- serving rules ---


def test_lookup_newest_request_is_always_a_miss(cache_dir):
    cache.store(-100123, [make_message(5)], limit=1, offset_id=0)

    assert cache.lookup([-100123], 1, 0) is None


def test_lookup_uncovered_range_misses(cache_dir):
    cache.store(-100123, [make_message(5)], limit=1, offset_id=6)  # covers [5, 5]

    assert cache.lookup([-100123], 1, 100) is None


def test_lookup_window_extending_below_coverage_misses(cache_dir):
    # Covers [5, 6]; a limit of 3 would need message 4, which is unknown.
    cache.store(-100123, [make_message(6), make_message(5)], limit=2, offset_id=7)

    assert cache.lookup([-100123], 3, 7) is None


def test_lookup_missing_database_misses(cache_dir):
    assert cache.lookup([-100123], 1, 6) is None
    assert not cache.cache_path().exists()  # a lookup never creates the file


def test_short_fetch_marks_history_start(cache_dir):
    # Fewer messages than requested: the start of history was reached, so
    # a later short answer from the cache is complete, not a miss.
    cache.store(-100123, [make_message(6), make_message(5)], limit=10, offset_id=7)

    result = cache.lookup([-100123], 10, 7)

    assert [message["id"] for message in result] == [6, 5]


def test_empty_paginated_fetch_is_cached(cache_dir):
    cache.store(-100123, [], limit=10, offset_id=50)

    assert cache.lookup([-100123], 10, 50) == []


def test_empty_newest_fetch_records_no_coverage(cache_dir):
    cache.store(-100123, [], limit=10, offset_id=0)

    assert cache.lookup([-100123], 10, 50) is None


# --- coverage merging ---


def test_adjacent_fetches_merge_into_one_range(cache_dir):
    cache.store(-100123, [make_message(6), make_message(5)], limit=2, offset_id=7)
    cache.store(-100123, [make_message(4), make_message(3)], limit=2, offset_id=5)

    result = cache.lookup([-100123], 4, 7)

    assert [message["id"] for message in result] == [6, 5, 4, 3]
    assert coverage_rows() == [(3, 6)]


def test_disjoint_fetches_stay_separate(cache_dir):
    cache.store(-100123, [make_message(9)], limit=1, offset_id=10)  # [9, 9]
    cache.store(-100123, [make_message(5)], limit=1, offset_id=6)  # [5, 5]

    assert coverage_rows() == [(5, 5), (9, 9)]
    # The gap 6..8 is unknown, so a window spanning it is a miss...
    assert cache.lookup([-100123], 2, 10) is None
    # ...while each covered range is still served.
    assert [m["id"] for m in cache.lookup([-100123], 1, 10)] == [9]


# --- chat ID disambiguation ---


def test_single_cached_candidate_is_used(cache_dir):
    cache.store(-100123, [make_message(5)], limit=1, offset_id=6)

    result = cache.lookup([123, -100123, -123], 1, 6)

    assert [message["id"] for message in result] == [5]


def test_ambiguous_positive_id_misses(cache_dir):
    # The same positive ID is cached both as a user and as a channel:
    # without the network the cache cannot tell which chat is meant.
    cache.store(123, [make_message(5)], limit=1, offset_id=6)
    cache.store(-100123, [make_message(7)], limit=1, offset_id=8)

    assert cache.lookup([123, -100123, -123], 1, 6) is None


# --- schema migration ---


def test_schema_v1_is_migrated(cache_dir):
    conn = sqlite3.connect(str(cache.cache_path()))
    conn.executescript(
        """
        CREATE TABLE messages (
            chat_id         INTEGER NOT NULL,
            id              INTEGER NOT NULL,
            date            TEXT,
            sender_id       INTEGER,
            sender_name     TEXT,
            text            TEXT,
            reply_to_msg_id INTEGER,
            grouped_id      INTEGER,
            media           TEXT,
            fetched_at      TEXT NOT NULL,
            PRIMARY KEY (chat_id, id)
        );
        CREATE TABLE coverage (
            chat_id INTEGER NOT NULL,
            min_id  INTEGER NOT NULL,
            max_id  INTEGER NOT NULL,
            PRIMARY KEY (chat_id, min_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO messages"
        " (chat_id, id, date, sender_id, sender_name, text, reply_to_msg_id,"
        "  grouped_id, media, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            -100123,
            5,
            "2026-07-03T12:34:56+00:00",
            111222333,
            "John Doe",
            "old message",
            None,
            None,
            None,
            "2026-07-03T12:35:00+00:00",
        ),
    )
    conn.execute("INSERT INTO coverage VALUES (?, ?, ?)", (-100123, 5, 5))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    assert cache.lookup([-100123], 1, 6) == [make_message(5, text="old message")]
    conn = sqlite3.connect(str(cache.cache_path()))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        conn.close()


def test_schema_v2_is_migrated(cache_dir):
    # A v2 database predates the 'entities' column; migrating it must keep the
    # existing row and expose entities as null (the old form, not an error).
    conn = sqlite3.connect(str(cache.cache_path()))
    conn.executescript(
        """
        CREATE TABLE messages (
            chat_id         INTEGER NOT NULL,
            id              INTEGER NOT NULL,
            date            TEXT,
            sender_id       INTEGER,
            sender_name     TEXT,
            text            TEXT,
            topic_id        INTEGER,
            reply_to_msg_id INTEGER,
            is_service      INTEGER NOT NULL,
            grouped_id      INTEGER,
            media           TEXT,
            fetched_at      TEXT NOT NULL,
            PRIMARY KEY (chat_id, id)
        );
        CREATE TABLE coverage (
            chat_id INTEGER NOT NULL,
            min_id  INTEGER NOT NULL,
            max_id  INTEGER NOT NULL,
            PRIMARY KEY (chat_id, min_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO messages"
        " (chat_id, id, date, sender_id, sender_name, text, topic_id,"
        "  reply_to_msg_id, is_service, grouped_id, media, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            -100123,
            5,
            "2026-07-03T12:34:56+00:00",
            111222333,
            "John Doe",
            "old message",
            None,
            None,
            0,
            None,
            None,
            "2026-07-03T12:35:00+00:00",
        ),
    )
    conn.execute("INSERT INTO coverage VALUES (?, ?, ?)", (-100123, 5, 5))
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    assert cache.lookup([-100123], 1, 6) == [make_message(5, text="old message")]
    conn = sqlite3.connect(str(cache.cache_path()))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        conn.close()


# --- failure behavior ---


def test_corrupt_database_never_breaks_read(cache_dir, capsys):
    cache.cache_path().write_text("this is not an SQLite database", encoding="utf-8")

    assert cache.lookup([-100123], 1, 6) is None
    cache.store(-100123, [make_message(5)], limit=1, offset_id=6)

    assert "warning: message cache unavailable" in capsys.readouterr().err


def test_unknown_schema_version_is_left_untouched(cache_dir, capsys):
    # A database written by an incompatible version (or another program)
    # must be skipped, not migrated or destroyed: other applications may
    # rely on its contents.
    conn = sqlite3.connect(str(cache.cache_path()))
    conn.execute("PRAGMA user_version = 99")
    conn.execute("CREATE TABLE messages (x)")
    conn.execute("INSERT INTO messages VALUES ('foreign data')")
    conn.commit()
    conn.close()

    assert cache.lookup([-100123], 1, 6) is None
    cache.store(-100123, [make_message(5)], limit=1, offset_id=6)

    assert "warning: message cache unavailable" in capsys.readouterr().err
    conn = sqlite3.connect(str(cache.cache_path()))
    try:
        assert conn.execute("SELECT x FROM messages").fetchall() == [("foreign data",)]
    finally:
        conn.close()
