# tg-reader — Project Description

## Purpose

A minimal CLI utility for reading messages from the owner's personal Telegram
account via MTProto (user account, not a bot).

The primary consumer is AI agents, not humans: the `read` command is designed
for machine use (JSON on stdout, errors on stderr, exit codes, self-sufficient
`--help`). The exception is the `auth` command: authorization is interactive
and is run manually by the user.

## Scope

A single `tg-reader` command with three subcommands:

1. **`tg-reader auth`** — one-time interactive authorization; stores
   credentials and creates a Telethon session file.
2. **`tg-reader read`** — fetches recent messages from a chat by its numeric
   ID and prints them as JSON, including media metadata.
3. **`tg-reader download`** — downloads the media attachment of one message
   to a local directory.

Non-goals for now: sending messages, full history export, real-time
monitoring.

## Stack

- Python 3.14, managed with `uv`
- [Telethon](https://docs.telethon.dev/) — MTProto client
- platformdirs — user data directory resolution
- filelock — inter-process lock for the flood protection layer
- pytest + pytest-asyncio + pytest-mock — tests (no network, mocked client)
- ruff — linter (default rules plus BLE)

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
and do not depend on the current working directory. The directory holds
secrets (`api_hash`, the session file grants full account access), so on
POSIX it is made private to the user (mode 0700) when the credentials are
saved:

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
- The network part runs under the same inter-process lock as the other
  commands, so the session file is never opened by two processes at once.

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
    "text": "message text or media caption; null if none",
    "grouped_id": 13579246812345678,
    "media": {
      "type": "document",
      "filename": "report.pdf",
      "mime_type": "application/pdf",
      "size_bytes": 123456
    }
  }
  ```

- `grouped_id` — shared album ID. An album (several photos/files sent
  together) is several separate messages, each with its own `id` and its own
  `media`; the caption is carried by only one of them. Clients render such
  messages as a single bubble, but the API (and this tool) treats them
  individually. `null` for standalone messages. Album message IDs are usually
  consecutive but this is not guaranteed — always use the explicit IDs. An
  album can also be cut off by the `--limit` window: if the oldest returned
  messages carry a `grouped_id`, the rest of that album may lie beyond the
  window — paginate with `--offset-id` to fetch it.
- `media` — present when the message carries a downloadable file, `null`
  otherwise (plain text, polls, geo, contacts; link-preview images are not
  counted as message media). Fields:
  - `type` — one of `photo`, `video`, `audio`, `voice`, `video_note`,
    `sticker`, `gif`, `document`;
  - `filename` — original filename as sent, or `null` when the media has no
    name (photos, voice messages);
  - `mime_type` — MIME type, or `null` when unknown;
  - `size_bytes` — file size (for photos: the largest available variant);
    pass it against the `download` size cap.

- Errors go to stderr; stdout stays empty. Exit codes:
  - `0` — success;
  - `1` — permanent error, do not retry with the same arguments (bad
    arguments, unknown chat, not authorized — the latter with a hint to run
    `tg-reader auth`);
  - `2` — temporarily unavailable, the stderr message ends with
    `retry after Ns`: wait and retry (another process holds the lock, a
    Telegram flood wait is active, or the network is down).
- `--help` (on both `tg-reader` and its subcommands) — a self-sufficient help
  text aimed at an AI agent: the utility's purpose, all arguments, accepted
  `CHAT_ID` formats, the JSON output schema with field descriptions, usage
  examples, and error behavior. An agent must be able to understand how to
  use the utility from this help alone, without reading the code or docs.
  In particular, the help of both `read` and `download` must explain album
  semantics: an album is several messages sharing one `grouped_id`, each
  with its own message ID and media; to fetch a whole album, download each
  of its messages one by one, and paginate with `--offset-id` if the album
  is cut off by the `--limit` window.

### tg-reader download

```
tg-reader download CHAT_ID MSG_ID --output DIR [--max-size MB]
```

Downloads the media attachment of a single message. The intended flow for an
agent: run `read`, inspect the `media` metadata (type, size), then download
the specific messages that are worth fetching.

- `CHAT_ID` — same formats as `read`. `MSG_ID` — message ID from the `read`
  output. One message per run; to fetch an album, run `download` once per
  album message (see `grouped_id` above).
- `--output DIR` — required destination directory; created if missing.
- `--max-size MB` — refuse to download files larger than this (default:
  100). Protects an agent from accidentally pulling a multi-GB video; the
  error names the actual file size so the agent can retry with an explicit
  higher limit.
- The message is re-fetched at download time: Telegram file references
  expire after a few hours, so downloading cannot rely on data captured by
  an earlier `read` run.
- Output: a single JSON object on stdout:

  ```json
  {
    "message_id": 12345,
    "type": "document",
    "file": "C:\\work\\in\\12345_report.pdf",
    "size_bytes": 123456
  }
  ```

  `file` is the absolute path of the downloaded file.
- Errors go to stderr with the same exit-code contract as `read`: `1` for
  permanent errors (unknown chat, message not found, message has no
  downloadable media, file exceeds the size cap), `2` for "temporarily
  unavailable, retry after Ns" (lock held by another process, active flood
  wait, network down).

#### File naming

Filenames of documents are attacker-controlled (the sender picks them), so
they are never used verbatim:

- The final name is `<msg_id>_<sanitized original name>`. Media without an
  original name (photos, voice messages, video notes) gets a generated name:
  `<msg_id>_<type>.<ext>` with the extension derived from the media type or
  MIME type (e.g. `12345_photo.jpg`).
- Sanitization: path separators and Windows-forbidden characters
  (`<>:"/\|?*`, control chars) are replaced, reserved device names (`CON`,
  `NUL`, ...) are prefixed, trailing dots/spaces are stripped, overly long
  names are truncated preserving the extension (the cap is counted in UTF-8
  bytes, matching filesystem name limits).
- The name is deterministic and an existing file is silently overwritten:
  re-running the same download is idempotent (same message — same path, same
  content), and the `<msg_id>_` prefix rules out collisions between
  different messages.
- Data is written to a `<name>.part` temporary file and renamed on success,
  so an interrupted run never leaves a half-written file under the final
  name.

### access_hash resolution

Telethon needs an `access_hash` to address channels and users; known entities
are cached in the session file. `tg-reader read` and `tg-reader download`
resolve the chat automatically:

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

- **Inter-process lock** — the whole networked part of a run (connect →
  work → disconnect; all three commands, including `auth`) executes under a
  global file lock, so only one `tg-reader` process talks to Telegram at a
  time and the SQLite session file is never opened concurrently. A second
  process waits up to 30 s, then fails with exit code 2.
- **FloodWait handling** — waits up to 30 s are slept through in place
  (Telethon's `flood_sleep_threshold`). Longer waits abort the run and the
  deadline is persisted to `throttle.json`; until it expires, every run
  fails fast with exit code 2 without touching the network, so retries and
  parallel callers cannot escalate the penalty.
- **Pacing** — consecutive runs are spaced at least 2 s apart (the process
  sleeps under the lock), smoothing out bursts from a misbehaving caller.
- **Bounded requests** — `--limit` is capped at 100, so one `read` run costs
  one GetHistory request. Downloads are inherently unbounded in request
  count (a file is fetched in ~512 KB chunks, one GetFile request each), so
  the analogue is the size cap: `--max-size` defaults to 100 MB and refusal
  is checked against the metadata before any transfer starts.
- **Downloads hold the lock** — a large download keeps the global lock for
  its whole duration; concurrent `tg-reader` runs fail fast with exit code 2
  until it finishes. This is intentional: one process talking to Telegram at
  a time is the whole point of the lock.

## Testing

Unit tests only, no network. The Telethon client is passed into the logic
functions as an argument (dependency injection) and replaced with `AsyncMock`
in tests — see `tests/test_read.py` for the pattern. Coverage
targets: ID normalization, entity resolution fallback, message-to-dict
formatting (including media metadata extraction per media type), flood
protection (FloodWait deadline persistence, pacing, lock behavior —
`time.sleep` is mocked, the config directory is redirected to a temporary
path), filename sanitization (path traversal, Windows-forbidden characters,
reserved device names, missing/overlong names), and the download flow
(size-cap refusal, `.part` rename on success and cleanup on failure,
no-media and message-not-found errors).

CI (GitHub Actions, `.github/workflows/ci.yml`) runs ruff and the suite on
Ubuntu and Windows for every push to `main` and every pull request. Since users
install straight from the git repository, the workflow also smoke-tests
the packaged entry point (`uvx --from . tg-reader ... --help`) and the
unauthorized exit-code contract. The real Telegram API is never touched:
CI guards the logic and the install path, not live protocol behavior.

## Layout

A package with src layout and a `tg-reader` entry point in `[project.scripts]`:

```
src/tg_reader/
    __init__.py
    cli.py        # argparse: auth/read/download subcommands, --help texts
    auth.py       # authorization logic
    session.py    # shared session scaffolding: lock, flood gate, connect, auth check
    read.py       # message reading logic
    media.py      # media metadata extraction, safe filename construction
    download.py   # media download logic
    throttle.py   # flood protection: lock, FloodWait state, pacing
    config.py     # user directory, config.json load/save
docs/PROJECT.md   # this file
tests/
```
