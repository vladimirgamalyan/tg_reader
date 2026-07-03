# tg-reader — Project Description

## Purpose

A minimal CLI utility for reading messages from the owner's personal Telegram
account via MTProto (user account, not a bot).

The primary consumer is AI agents, not humans: the `read` command is designed
for machine use (JSON on stdout, errors on stderr, exit codes, self-sufficient
`--help`). The exception is the `auth` command: authorization is interactive
and is run manually by the user.

## Scope (v0)

A single `tg-reader` command with two subcommands:

1. **`tg-reader auth`** — one-time interactive authorization; stores
   credentials and creates a Telethon session file.
2. **`tg-reader read`** — fetches recent messages from a chat by its numeric
   ID and prints them as JSON.

Non-goals for now: sending messages, downloading media, full history export,
real-time monitoring.

## Stack

- Python 3.14, managed with `uv`
- [Telethon](https://docs.telethon.dev/) — MTProto client
- platformdirs — user data directory resolution
- pytest + pytest-asyncio + pytest-mock — tests (no network, mocked client)

## Installation and usage on the end user's machine

Distribution is via the git repository, installation via `uv`:

```
uv tool install git+<repo-url>     # puts the tg-reader command on PATH
uv tool upgrade tg-reader          # upgrade
```

Running without installation:

```
uvx --from git+<repo-url> tg-reader ...
```

For development inside a repo clone: `uv run tg-reader ...`.

## Configuration

All utility files live in a per-user directory (resolved via `platformdirs`)
and do not depend on the current working directory:

- Windows: `%APPDATA%\tg-reader\`
- Linux: `~/.config/tg-reader/`

Directory contents:

- `config.json` — `api_id` and `api_hash`; created by the `auth` command
- `tg_reader.session` — Telethon session file; created by the `auth` command

Credentials are obtained at <https://my.telegram.org> → "API development tools".

## Commands

### tg-reader auth

```
tg-reader auth
```

Run manually by the user (interactive input); this command is not intended
for AI agents.

- Prompts for `api_id` and `api_hash` (unless already present in the config)
  and saves them to `config.json`.
- If the session is already authorized: prints the logged-in account info and
  exits.
- Otherwise runs the interactive login flow: phone number → confirmation code →
  2FA password (if enabled). On success the session file is created.

### tg-reader read

```
tg-reader read CHAT_ID [--limit N] [--offset-id MSG_ID]
```

- `CHAT_ID` — numeric chat/channel/user ID. Both raw MTProto IDs and
  Bot-API-style `-100...` IDs are accepted.
- `--limit N` — number of messages to fetch (default: 20).
- `--offset-id MSG_ID` — fetch messages older than this message ID (manual
  pagination).
- Output: a JSON array on stdout, newest message first. Each element:

  ```json
  {
    "id": 12345,
    "date": "2026-07-03T12:34:56+00:00",
    "sender_id": 111222333,
    "sender_name": "John Doe",
    "text": "message text or media caption; null if none"
  }
  ```

- Errors go to stderr with a non-zero exit code. Special case: running
  without authorization fails with a hint to run `tg-reader auth`.
- `--help` (on both `tg-reader` and its subcommands) — a self-sufficient help
  text aimed at an AI agent: the utility's purpose, all arguments, accepted
  `CHAT_ID` formats, the JSON output schema with field descriptions, usage
  examples, and error behavior. An agent must be able to understand how to
  use the utility from this help alone, without reading the code or docs.

### access_hash resolution

Telethon needs an `access_hash` to address channels and users; known entities
are cached in the session file. `tg-reader read` resolves the chat
automatically:

1. Try the session entity cache first.
2. If the ID is unknown, iterate the account's dialogs until the chat is found
   (this also populates the cache, so subsequent runs hit step 1).
3. If still not found, exit with a clear error — the account must actually be
   a member of the chat.

## Testing

Unit tests only, no network. The Telethon client is passed into the logic
functions as an argument (dependency injection) and replaced with `AsyncMock`
in tests — see `tests/test_telethon_example.py` for the pattern. Coverage
targets: ID normalization, entity resolution fallback, message-to-dict
formatting.

## Layout

A package with src layout and a `tg-reader` entry point in `[project.scripts]`:

```
src/tg_reader/
    __init__.py
    cli.py        # argparse: auth/read subcommands, --help texts
    auth.py       # authorization logic
    read.py       # message reading logic
    config.py     # user directory, config.json load/save
docs/PROJECT.md   # this file
tests/
```

`main.py` is a leftover IDE placeholder and will be removed when the package
is added.
