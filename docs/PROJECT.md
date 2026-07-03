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
- filelock — inter-process lock for the flood protection layer
- pytest + pytest-asyncio + pytest-mock — tests (no network, mocked client)

## Installation and usage on the end user's machine

Distribution is via the git repository, installation via `uv`:

```
uv tool install git+<repo-url>     # puts the tg-reader command on PATH
uv tool upgrade tg-reader          # upgrade
```

From a local clone (installs the working tree, no push required):

```
uv tool install <path-to-repo>             # snapshot; reinstall after code changes
uv tool install --editable <path-to-repo>  # changes apply without reinstall
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
- `throttle.json` — flood protection state (last run timestamp, active
  FloodWait deadline); created and managed automatically
- `tg_reader.lock` — inter-process lock file; created automatically

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
- `--limit N` — number of messages to fetch, 1..100 (default: 20). The cap
  keeps one run to a single GetHistory API request.
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

- Errors go to stderr; stdout stays empty. Exit codes:
  - `0` — success;
  - `1` — permanent error, do not retry with the same arguments (bad
    arguments, unknown chat, not authorized — the latter with a hint to run
    `tg-reader auth`);
  - `2` — temporarily unavailable, the stderr message ends with
    `retry after Ns`: wait and retry (another process holds the lock, or a
    Telegram flood wait is active).
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

## Flood protection

Reading history is a low-risk operation, but ignoring server-assigned
FLOOD_WAIT penalties is the main way accounts get escalating restrictions.
The protection layer (`throttle.py`) is automatic and not configurable;
policy constants live at the top of the module.

- **Inter-process lock** — the whole `read` run (connect → fetch →
  disconnect) executes under a global file lock, so only one `tg-reader`
  process talks to Telegram at a time and the SQLite session file is never
  opened concurrently. A second process waits up to 30 s, then fails with
  exit code 2.
- **FloodWait handling** — waits up to 30 s are slept through in place
  (Telethon's `flood_sleep_threshold`). Longer waits abort the run and the
  deadline is persisted to `throttle.json`; until it expires, every run
  fails fast with exit code 2 without touching the network, so retries and
  parallel callers cannot escalate the penalty.
- **Pacing** — consecutive runs are spaced at least 2 s apart (the process
  sleeps under the lock), smoothing out bursts from a misbehaving caller.
- **Bounded requests** — `--limit` is capped at 100, so one run costs one
  GetHistory request.

## Testing

Unit tests only, no network. The Telethon client is passed into the logic
functions as an argument (dependency injection) and replaced with `AsyncMock`
in tests — see `tests/test_read.py` for the pattern. Coverage
targets: ID normalization, entity resolution fallback, message-to-dict
formatting, flood protection (FloodWait deadline persistence, pacing, lock
behavior — `time.sleep` is mocked, the config directory is redirected to a
temporary path).

## Layout

A package with src layout and a `tg-reader` entry point in `[project.scripts]`:

```
src/tg_reader/
    __init__.py
    cli.py        # argparse: auth/read subcommands, --help texts
    auth.py       # authorization logic
    read.py       # message reading logic
    throttle.py   # flood protection: lock, FloodWait state, pacing
    config.py     # user directory, config.json load/save
docs/PROJECT.md   # this file
tests/
```
