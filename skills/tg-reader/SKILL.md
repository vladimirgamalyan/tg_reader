---
name: tg-reader
description: "Read messages from the user's own Telegram account through the tg-reader CLI. Use whenever the user wants to read, check, or fetch Telegram messages from a chat — for example phrases like read telegram, check my telegram, what's new in the ... chat, latest messages from ... (in any language the user speaks). Resolves a chat name to its numeric ID via a local alias registry or the account's chat list, and can add new chats to that registry on request. Works from any project or folder."
---

# tg-reader

`tg-reader` is a CLI (already on PATH) for reading messages from the user's own
Telegram account over MTProto. This skill maps a natural request like
"read the work chat" to the right numeric chat ID and runs the CLI.

## 1. Resolve the chat name → ID

Chat IDs live in a shared alias registry — the same file for every session and
for both Claude Code and Codex:

- Windows: `%APPDATA%\tg-reader\aliases.json`
- Linux: `~/.config/tg-reader/aliases.json`

Read it and match the user's wording against each chat's `title` and `aliases`.

- Match case-insensitively and by meaning ("work" → the chat whose aliases
  include `work`), in whatever language the user and the registry use.
- If several chats plausibly match (e.g. "John" vs "John (work)"), ask the
  user which one before reading.
- If nothing matches, look the chat up in the account's chat list:

  ```
  tg-reader chats --query <part of the name>
  ```

  It matches a case-insensitive substring of the chat title or username, so
  query a distinctive part of the name, and try other forms (a word stem,
  the Latin spelling) if nothing comes back. Use the printed `id` as is.
  - Exactly one chat clearly matches → use it, and name the chat in your
    answer so the user sees which one was read.
  - Several plausibly match → show them and ask the user which one.
  - Nothing matches → ask the user. Never guess or invent an ID: a wrong ID
    reads a different person's chat.

  After resolving a chat this way, offer to save it to the registry
  (see section 5) so the next request finds it directly.

## 2. Read messages

```
tg-reader read <CHAT_ID> [--limit N] [--offset-id MSG_ID] [--topic TOPIC_ID | --search TEXT]
```

- `<CHAT_ID>` is the resolved numeric ID from the registry.
- Output is a JSON array on stdout, newest message first.
- For the full flag list, the exact JSON schema, album/media semantics, and the
  exit-code contract, run `tg-reader read --help` — it is written to be
  self-sufficient. Do not rely on memory for the schema; check `--help`.

Present the result to the user readably (sender, time, text) — do not dump raw
JSON unless they ask for it. For a forwarded message (`forwarded_from` is set),
say where it came from, and mind that its original date may be much older.

Pick the right kind of read for the request:

- **"What's new" across Telegram** (no chat named): run
  `tg-reader chats --unread`, then read the chats that matter with
  `--limit` set to their `unread_count` (at most 100 per run). Summarize per
  chat; skip noisy channels unless the user cares about them.
- **A topic of a forum group** ("the rent topic in …"): run
  `tg-reader topics <CHAT_ID>`, match the user's wording against the topic
  titles (same rules as chat names: ask when several fit), then
  `tg-reader read <CHAT_ID> --topic <TOPIC_ID>`.
- **Finding something said earlier** ("when did X mention Y"): use
  `--search <word>`. It is Telegram's own search: whole words, case-insensitive,
  word forms for Russian, no partial words — search by a full word, and try
  synonyms if nothing comes back.

## 3. Long conversations: the local archive

When the user wants to go through a long stretch of a chat (more than a few
hundred messages), or to come back to a conversation read earlier, keep a
local archive instead of paging with `--offset-id`:

```
tg-reader sync <CHAT_ID>                      # update an existing archive
tg-reader sync <CHAT_ID> --since YYYY-MM-DD   # first sync of this chat
```

- Run it without `--since` first. If the chat has no archive yet, it fails
  and asks for `--since`: take the date from the user's request, or ask the
  user how far back to go. Pick the latest date that covers what is needed —
  an early date on a busy chat means many requests.
- The output names the archive file: JSONL, one message per line, oldest
  first, same fields as the `read` output. Search and read that file
  directly (Grep, jq) rather than loading all of it at once.
- `--since` is ignored once an archive exists. If the user needs history
  from before the archive's first message, delete the file and sync again
  with the earlier date.
- The archive is never refreshed: edits and deletions made after a message
  was archived do not show up. Mention this if it matters for the answer.

See `tg-reader sync --help` for the full contract.

## 4. Download media

If a message carries media (its `media` field is not null) and the user wants
the file:

```
tg-reader download <CHAT_ID> <MSG_ID> --output <DIR>
```

See `tg-reader download --help` for size limits and album handling.

## 5. Add a chat to the registry

When the user asks to remember a chat (e.g. "remember this chat as 'work', id
-100123"), append an entry to `aliases.json`, keeping it valid JSON:

```json
{
  "chats": [
    { "id": 123456789, "title": "John", "aliases": ["john", "johnny"] }
  ]
}
```

- `id` — numeric chat ID exactly as the user gives it or as `tg-reader chats`
  prints it (`tg-reader` accepts both raw MTProto IDs and Bot-API-style
  `-100...` IDs).
- `title` — human name to show when confirming ("reading 'John'?").
- `aliases` — lowercase short names to match the user's wording against.
- Optional `note` — free-text context (e.g. "channel", a short description).

Only record IDs the user provides or `tg-reader chats` printed; do not
fabricate an ID.
