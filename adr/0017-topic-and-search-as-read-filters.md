# 0017. Forum topics and search as `read` filters, plus a `topics` listing

Status: Accepted

## Context

Fourteen of the author's groups are forums. Reading one topic meant paging
through the whole group and filtering by `topic_id`, and an agent had no way
to map a topic title ("the rent topic") to its ID: the topic-creation
service message carries the title, but `read` does not expose it. Finding
something said long ago likewise meant paging back through history, or
archiving it first with `sync`.

Alternatives considered:

- **Separate `search` and `topic` commands** — rejected: their output is
  exactly `read`'s (same schema, newest first, `--limit`/`--offset-id`
  paging), so separate commands would duplicate the whole `read` contract
  and its help text.
- **Searching the local archive only** — rejected: it covers only what was
  synced from `--since` on, while Telegram's server-side search covers the
  whole history in one request.
- **Expose topic titles inside `read` output** (from the topic-creation
  service message) — rejected: the title is only visible if that message
  happens to be in the fetched window.

## Decision

We will add two mutually exclusive filters to `read`: `--topic TOPIC_ID`
(Telethon `reply_to`, i.e. GetReplies on the topic root) and `--search TEXT`
(Telethon `search`, i.e. messages.Search). They cannot be combined because
Telethon silently drops the search when given both.

We will add `tg-reader topics CHAT_ID`, which lists a forum's topics as
`{"id", "title", "unread_count"}` with a single GetForumTopics request (at
most 100 topics). A chat that is not a forum fails as a permanent error with
a clear message.

## Consequences

- An agent resolves "topic X of chat Y" with two calls and reads it with the
  usual paging; "when did someone mention Z" is one search request.
- Search semantics are Telegram's, not ours: whole words, word forms for
  some languages, no partial words. The help says so, because a substring
  habit (like `chats --query`) finds nothing here.
- Forums with more than 100 topics are listed only up to their 100 most
  recently active topics; paging was left out until a forum needs it.
- An invalid topic ID surfaces as Telegram's own TOPIC_ID_INVALID error
  (exit code 1) rather than a tailored message.
