# 0015. The agent resolves chat names, helped by a `chats` listing

Status: Accepted

## Context

Every command that acts on a chat takes a numeric `CHAT_ID`. Agents already
map names to IDs through the alias registry of the agent skill
(`aliases.json`, matched by meaning: "my daughter" → the chat aliased
"дочка"). The gap was a chat missing from the registry: the user had to dig
its numeric ID out of a Telegram client by hand, which official clients do
not readily show.

Alternatives considered:

- **Accept a chat name in the CLI** (`tg-reader read "John"`) — rejected:
  titles are not unique and change over time, and the agent passes names in
  arbitrary grammatical forms, so the CLI would have to guess; a wrong guess
  reads a different person's chat. Resolving public `@username`s through
  Telegram (ResolveUsername) is also heavily rate-limited.
- **Let the CLI read `aliases.json`** (`tg-reader read work`) — rejected:
  exact alias matching duplicates what the agent already does better (by
  meaning); it would only help a human typing in a terminal, and the tool
  is used through agents only.
- **A `chats` command listing the dialogs with their IDs (chosen).** The
  agent searches it, asks the user when several chats match, and saves the
  result to the registry.

## Decision

We will add `tg-reader chats [--query TEXT]`, printing the account's dialogs
(archived included, migrated small groups skipped) as JSON objects with a
marked `id`, `title`, `type` and `username`. The CLI keeps taking IDs only;
name matching stays in the skill.

`--query` filters by a case-insensitive substring of the title or username.
It was added from the start, not deferred: the author's account already has
about 400 chats, and the full list (~50 KB of JSON) exceeds what an agent's
tool output holds (Claude Code truncates at 30,000 characters).

## Consequences

- A chat missing from the registry no longer needs a manual ID lookup; the
  registry fills up as the agent offers to save chats it found.
- The `id` from `chats` is the marked form, which resolves without trying
  the user/channel/group candidates in turn.
- Substring matching cannot bridge nicknames or other languages ("my
  daughter" → "Vika"); those stay the registry's job, and the help tells the
  agent to try other forms of the name before falling back to the full list.
- Each lookup walks the whole dialog list: one GetDialogs request per 100
  chats.
