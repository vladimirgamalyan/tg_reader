"""Local message cache: SQLite storage of fetched messages and range coverage.

Every successful 'read' stores its messages in a per-user SQLite database
(cache.db next to config.json). The cache serves two purposes: repeated
history reads are answered locally without spending Telegram requests, and
other local applications can query the accumulated messages directly (the
schema is documented in docs/PROJECT.md).

The cache reflects the state at fetch time: messages edited or deleted in
Telegram afterwards stay stale until the range is re-fetched (--no-cache).
A request for the newest messages (offset_id == 0) is never served from the
cache, because only the server knows whether newer messages exist; only
history pagination (--offset-id) can be served, and only when the requested
window lies entirely inside one covered ID range.

A cache failure must never break 'read': lookups degrade to a miss and
stores degrade to a stderr warning.
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config

CACHE_FILENAME = "cache.db"
# Bumped when the on-disk schema changes. A database with a different
# version is left untouched (other applications may still rely on it) and
# the cache is skipped for the run.
SCHEMA_VERSION = 2
# Time to wait for a concurrent writer (another tg-reader run or an
# external application) before treating the database as unavailable.
BUSY_TIMEOUT = 5.0

_SCHEMA = """\
CREATE TABLE messages (
    chat_id         INTEGER NOT NULL,  -- marked chat ID (Bot-API style)
    id              INTEGER NOT NULL,  -- message ID within the chat
    date            TEXT,              -- ISO 8601 UTC timestamp
    sender_id       INTEGER,
    sender_name     TEXT,
    text            TEXT,
    topic_id        INTEGER,           -- forum topic root message ID
    reply_to_msg_id INTEGER,
    is_service      INTEGER NOT NULL,  -- 0/1 boolean service-message marker
    grouped_id      INTEGER,
    media           TEXT,              -- 'media' object of the 'read' output as JSON
    fetched_at      TEXT NOT NULL,     -- ISO 8601 UTC time the row was written
    PRIMARY KEY (chat_id, id)
);
CREATE TABLE coverage (
    chat_id INTEGER NOT NULL,
    min_id  INTEGER NOT NULL,  -- inclusive
    max_id  INTEGER NOT NULL,  -- inclusive
    PRIMARY KEY (chat_id, min_id)
);
"""


class _UnusableCacheError(Exception):
    """The database file exists but cannot be used safely by this version."""


def cache_path() -> Path:
    return config.config_dir() / CACHE_FILENAME


def _connect(create: bool) -> sqlite3.Connection | None:
    """Open the cache database, initializing the schema on first use.

    Returns None when the database does not exist and create is False.
    """
    path = cache_path()
    if not path.exists() and not create:
        return None
    if create:
        config.ensure_config_dir()
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return conn
        if version == 1:
            _migrate_v1_to_v2(conn)
            return conn
        tables = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        if version != 0 or tables != 0:
            raise _UnusableCacheError(
                f"unsupported cache database at {path} (schema version {version})"
            )
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return conn
    except (sqlite3.Error, _UnusableCacheError):
        conn.close()
        raise


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add read output columns introduced in schema v2."""
    with conn:
        conn.execute("ALTER TABLE messages ADD COLUMN topic_id INTEGER")
        conn.execute(
            "ALTER TABLE messages ADD COLUMN is_service INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _warn(error: Exception) -> None:
    print(f"warning: message cache unavailable ({error})", file=sys.stderr)


def _cached_chat_id(conn: sqlite3.Connection, candidates: list[int]) -> int | None:
    """Pick the cached chat among the marked IDs a user-supplied ID may mean.

    A positive user-supplied ID may denote a user, a channel or a small
    group; without the network only the cache itself can disambiguate. If
    more than one candidate has cached data, the ID stays ambiguous and the
    read falls through to the normal network resolution.
    """
    present = [
        chat_id
        for chat_id in candidates
        if conn.execute(
            "SELECT 1 FROM coverage WHERE chat_id = ? LIMIT 1", (chat_id,)
        ).fetchone()
    ]
    if len(present) == 1:
        return present[0]
    return None


def _row_to_dict(row: tuple) -> dict:
    """Rebuild the 'read' output dict from a messages table row."""
    (
        msg_id,
        date,
        sender_id,
        sender_name,
        text,
        topic_id,
        reply_to,
        is_service,
        grouped_id,
        media,
    ) = row
    return {
        "id": msg_id,
        "date": date,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "text": text,
        "topic_id": topic_id,
        "reply_to_msg_id": reply_to,
        "is_service": bool(is_service),
        "grouped_id": grouped_id,
        "media": json.loads(media) if media is not None else None,
    }


def lookup(
    chat_id_candidates: list[int], limit: int, offset_id: int
) -> list[dict] | None:
    """Serve a paginated read from the cache; None means fetch from Telegram."""
    if offset_id == 0:
        # Only the server knows whether messages newer than the cached ones
        # exist, so the default "newest messages" read always uses the network.
        return None
    try:
        conn = _connect(create=False)
    except (OSError, sqlite3.Error, _UnusableCacheError):
        # An unusable cache must never break 'read'; the store attempt after
        # the network fetch reports the problem to stderr.
        return None
    if conn is None:
        return None
    try:
        chat_id = _cached_chat_id(conn, chat_id_candidates)
        if chat_id is None:
            return None
        target = offset_id - 1
        row = conn.execute(
            "SELECT min_id FROM coverage"
            " WHERE chat_id = ? AND min_id <= ? AND max_id >= ?",
            (chat_id, target, target),
        ).fetchone()
        if row is None:
            return None
        (min_id,) = row
        rows = conn.execute(
            "SELECT id, date, sender_id, sender_name, text,"
            " topic_id, reply_to_msg_id, is_service, grouped_id, media FROM messages"
            " WHERE chat_id = ? AND id >= ? AND id < ?"
            " ORDER BY id DESC LIMIT ?",
            (chat_id, min_id, offset_id, limit),
        ).fetchall()
        if len(rows) < limit and min_id > 1:
            # The requested window continues below the covered range: the
            # cache cannot tell whether older messages exist there.
            return None
        return [_row_to_dict(row) for row in rows]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _covered_interval(
    messages: list[dict], limit: int, offset_id: int
) -> tuple[int, int] | None:
    """The message ID range this fetch is known to cover completely.

    GetHistory returns consecutive messages (IDs absent from the response
    do not exist server-side), so one fetch proves the cache holds
    everything in [lower, upper]:
      - upper is offset_id - 1 for a paginated fetch (nothing exists between
        the newest returned message and offset_id), or the newest returned
        ID for an offset_id=0 fetch (newer messages may appear later);
      - lower is the oldest returned ID, or 1 when the server returned fewer
        messages than requested, which means the start of the visible
        history was reached.
    """
    if messages:
        upper = offset_id - 1 if offset_id else messages[0]["id"]
        lower = messages[-1]["id"] if len(messages) == limit else 1
        return lower, upper
    if offset_id > 1:
        # An empty paginated response: nothing exists below offset_id at all.
        return 1, offset_id - 1
    return None


def _merge_coverage(
    conn: sqlite3.Connection, chat_id: int, lower: int, upper: int
) -> None:
    """Insert [lower, upper], merging overlapping and adjacent ranges."""
    overlapping = conn.execute(
        "SELECT min_id, max_id FROM coverage"
        " WHERE chat_id = ? AND min_id <= ? AND max_id >= ?",
        (chat_id, upper + 1, lower - 1),
    ).fetchall()
    for min_id, max_id in overlapping:
        lower = min(lower, min_id)
        upper = max(upper, max_id)
        conn.execute(
            "DELETE FROM coverage WHERE chat_id = ? AND min_id = ?",
            (chat_id, min_id),
        )
    conn.execute(
        "INSERT INTO coverage (chat_id, min_id, max_id) VALUES (?, ?, ?)",
        (chat_id, lower, upper),
    )


def store(chat_id: int, messages: list[dict], limit: int, offset_id: int) -> None:
    """Record fetched messages and the covered ID range; never raises.

    Called after every successful network fetch, --no-cache runs included,
    so the archive keeps growing and edited messages get refreshed.
    """
    try:
        conn = _connect(create=True)
    except (OSError, sqlite3.Error, _UnusableCacheError) as error:
        _warn(error)
        return
    try:
        fetched_at = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO messages"
                " (chat_id, id, date, sender_id, sender_name, text,"
                "  topic_id, reply_to_msg_id, is_service, grouped_id, media,"
                "  fetched_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        chat_id,
                        message["id"],
                        message["date"],
                        message["sender_id"],
                        message["sender_name"],
                        message["text"],
                        message["topic_id"],
                        message["reply_to_msg_id"],
                        int(message["is_service"]),
                        message["grouped_id"],
                        json.dumps(message["media"], ensure_ascii=False)
                        if message["media"] is not None
                        else None,
                        fetched_at,
                    )
                    for message in messages
                ],
            )
            interval = _covered_interval(messages, limit, offset_id)
            if interval is not None:
                _merge_coverage(conn, chat_id, *interval)
    except sqlite3.Error as error:
        _warn(error)
    finally:
        conn.close()
