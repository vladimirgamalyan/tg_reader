# 0009. Report cross-chat quote replies as "not a reply"

Status: Accepted

## Context

Telegram lets a user quote-reply to a message from a different chat. The
reply header of such a message carries `reply_to_peer_id` (the other chat)
next to `reply_to_msg_id`, which then holds an ID **in that other chat**.
Until now `read` ignored `reply_to_peer_id` and passed `reply_to_msg_id`
through, so the output claimed a same-chat reply: an agent following the
documented contract ("pass it to --offset-id ... or to 'tg-reader
download'") would fetch an unrelated message that happens to share the ID,
or fail on a nonexistent one.

The same header interacts with forum topics: for a cross-chat quote posted
inside a topic, only `reply_to_top_id` can name the local topic root — the
foreign `reply_to_msg_id` must not be used as the `topic_id` fallback
either.

Alternatives considered:

- **Keep passing the raw ID through** — rejected: it points consumers at
  the wrong message in the wrong chat; that is data corruption, not a
  simplification.
- **Add fields for the external target (peer + message ID)** — rejected
  for now: the Bot API models this as a separate `external_reply` object,
  but no consumer of this tool has asked for cross-chat reply targets, and
  the schema can grow such a field later without breaking anything.
- **Suppress the phantom value (chosen)** — `reply_to_msg_id` is `null`
  when the header carries `reply_to_peer_id`, mirroring the ADR-0007
  decision for forum membership headers: the field keeps its documented
  meaning "an ID in this chat".

## Decision

We will report `reply_to_msg_id` as `null` when the reply header carries
`reply_to_peer_id`, and never use the foreign `reply_to_msg_id` as the
`topic_id` fallback (`reply_to_top_id` alone names the topic root then).

## Consequences

- `reply_to_msg_id` values in `read` output are always usable in the same
  chat; reply chains built from them cannot cross into other chats.
- The information that a message quotes another chat is dropped. If a
  consumer ever needs it, an additive schema change (an `external_reply`
  object) can restore it without touching the meaning of existing fields.
- The suppression lives next to the ADR-0007 logic in `read.py`; both
  interpret the same header in one place.
