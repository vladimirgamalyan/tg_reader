---
name: tg-reader
description: "Read messages from the user's own Telegram account through the tg-reader CLI. Use whenever the user wants to read, check, or fetch Telegram messages from a chat — for example phrases like read telegram, check my telegram, what's new in the ... chat, latest messages from ... (in any language the user speaks). Resolves a chat name to its numeric ID via a local alias registry, and can add new chats to that registry on request. Works from any project or folder."
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
- If nothing matches, ask the user for the numeric chat ID — never guess or
  invent an ID, because a wrong ID reads a different person's chat. Offer to
  save it (see section 4).

## 2. Read messages

```
tg-reader read <CHAT_ID> [--limit N] [--offset-id MSG_ID]
```

- `<CHAT_ID>` is the resolved numeric ID from the registry.
- Output is a JSON array on stdout, newest message first.
- For the full flag list, the exact JSON schema, album/media semantics, and the
  exit-code contract, run `tg-reader read --help` — it is written to be
  self-sufficient. Do not rely on memory for the schema; check `--help`.

Present the result to the user readably (sender, time, text) — do not dump raw
JSON unless they ask for it.

## 3. Download media

If a message carries media (its `media` field is not null) and the user wants
the file:

```
tg-reader download <CHAT_ID> <MSG_ID> --output <DIR>
```

See `tg-reader download --help` for size limits and album handling.

## 4. Add a chat to the registry

When the user asks to remember a chat (e.g. "remember this chat as 'work', id
-100123"), append an entry to `aliases.json`, keeping it valid JSON:

```json
{
  "chats": [
    { "id": 123456789, "title": "John", "aliases": ["john", "johnny"] }
  ]
}
```

- `id` — numeric chat ID exactly as the user gives it (`tg-reader` accepts both
  raw MTProto IDs and Bot-API-style `-100...` IDs).
- `title` — human name to show when confirming ("reading 'John'?").
- `aliases` — lowercase short names to match the user's wording against.
- Optional `note` — free-text context (e.g. "channel", a short description).

Only record what the user provides; do not fabricate an ID.
