# 0018. Message reactions ("likes") for agents

Status: Proposed — deferred; recorded so the design and the measurements
are not redone when the need comes up.

## Context

Agents may want reaction data: "which posts got the most reactions", "how
many likes did my message get", "how did people react", "who liked it".

Measured on the author's account on 2026-11-01:

- Reaction counts arrive in the same GetHistory response as the messages
  (Telethon `message.reactions.results`), so exposing them costs no extra
  request.
- A large broadcast channel: 47 of 50 posts had reactions, up to 10
  distinct reactions per post, up to 936 on one post (45k views). Kinds:
  plain emoji (293 counters), paid Telegram Stars reactions (17; their
  count is stars, not people), custom emoji (a few; only a document ID,
  meaningless to an agent without an extra request).
- A group (SamuAI): 26 of 100 messages had reactions, 1–3 each. A private
  chat: rare.
- Who reacted: `messages.getMessageReactionsList` works in groups and
  private chats, but fails in broadcast channels with BROADCAST_FORBIDDEN.
  Groups also attach 1–3 `recent_reactions` per message (a partial list).
- `chosen_order` marks the account's own reaction; channel posts also carry
  `views`.

Options considered:

1. **Counts in `read` output (recommended)** — one nullable field per
   message, a map from emoji to count, e.g.
   `{"👍": 12, "🔥": 3, "custom": 2, "paid": 17}`: custom emoji summed under
   `custom`, paid reactions (stars) under `paid`, so totals match what the
   Telegram apps show. A map rather than a list of objects (unlike
   `entities`, ADR-0003): a reaction has only a kind and a count, and a
   list of objects would multiply the output size on busy channels. No new
   command or flag; the field flows into `--topic`, `--search` and `sync`
   archives automatically.
2. **Who reacted: `tg-reader reactions CHAT_ID MSG_ID`** — a list of
   `{sender_id, sender_name, reaction, date}` for one message, shaped like
   `download CHAT_ID MSG_ID`; a clear permanent error for channels. One
   extra request per message; only worth it if "who liked my message in a
   group" becomes a real question.
3. **`views` on channel posts** — one line, same response; context for
   reaction counts (936 reactions on 45k views vs 2k views). Not asked for.

Rejected:

- **`recent_reactions` in `read` output** — partial (1–3 reactors) and
  misleading; option 2 is the complete answer.
- **A "my reaction" flag per counter** — little value for the extra field.
- **Sorting or "top posts" in the CLI** — the agent sorts `read` output or
  an archive itself.

## Decision

Not taken yet. When reactions are needed, start with option 1; add option 2
and/or 3 only for a concrete question they answer.

## Consequences

If option 1 is implemented:

- One more nullable field in the stable `read` schema (minor version bump);
  older archive lines lack it (ADR-0014).
- Counts in `sync` archives freeze at archiving time, while fresh posts keep
  collecting reactions: reaction statistics should come from `read`, not
  from an archive.
