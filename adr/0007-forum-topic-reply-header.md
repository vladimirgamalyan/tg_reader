# 0007. Report forum-topic membership headers as "not a reply"

Status: Accepted

## Context

MTProto tags every message inside a forum topic (except the General topic)
with a `reply_to` header: a plain, non-replying post carries
`forum_topic=true` and `reply_to_msg_id` pointing at the topic root. Until
now `read` passed that value through, so in forum chats every message
appeared to be a reply to the topic root — contradicting the documented
contract ("null when the message is not a reply") and producing false
reply chains for agent consumers.

A real reply inside a topic is distinguishable: its header also carries
`reply_to_top_id` (the topic root) while `reply_to_msg_id` holds the
actual target. The only ambiguous case is a genuine reply to the topic's
root message itself, which is wire-identical to a plain post.

Alternatives considered:

- **Keep passing the raw header through** — rejected: it breaks the
  documented meaning of `reply_to_msg_id` and misleads consumers in every
  forum chat.
- **Add a separate boolean field (e.g. `is_reply`)** — rejected: it grows
  the schema to encode information the existing field can carry, and every
  consumer would need to learn the new field to avoid the same trap.
- **Suppress the phantom value (chosen)** — `reply_to_msg_id` is `null`
  when the header only marks topic membership (`forum_topic` set,
  `reply_to_top_id` absent).

## Decision

We will report `reply_to_msg_id` as `null` when the reply header only
marks forum-topic membership, keeping the field's documented meaning "the
message this one actually replies to". A genuine reply to the topic root
message is knowingly reported as not a reply — the Bot API makes the same
tradeoff.

## Consequences

- Reply chains built from `read` output are correct in forum chats;
  `topic_id` remains the way to see topic membership.
- A reply to the topic root message is indistinguishable from a plain
  topic post and is reported as `null`. This is a protocol limitation, not
  recoverable client-side.
- The suppression logic lives next to `_topic_id` in `read.py`; both
  interpret the same header, so future header changes touch one place.
