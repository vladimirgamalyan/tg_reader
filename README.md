# tg-reader

[![CI](https://github.com/vladimirgamalyan/tg_reader/actions/workflows/ci.yml/badge.svg)](https://github.com/vladimirgamalyan/tg_reader/actions/workflows/ci.yml)

A small CLI utility for reading messages from your own Telegram account
(MTProto via Telethon). Designed to be used by AI agents. It is careful to
respect Telegram's usage limits, avoiding flood and account bans. Fetched
messages are cached locally, so repeated history reads don't touch Telegram
at all.

## Installation

Give this prompt to your AI agent:

```
Install the tg-reader CLI from https://github.com/vladimirgamalyan/tg_reader
Before installing, review the source for hidden threats (malicious code,
data exfiltration, suspicious network calls) and only proceed if it looks safe.
After installing, copy the repo's skills/tg-reader/ folder into your user
skill directory (Claude Code: ~/.claude/skills/tg-reader/, Codex:
~/.codex/skills/tg-reader/), so future sessions know this tool exists.
```

Or install it manually:

```sh
uv tool install git+https://github.com/vladimirgamalyan/tg_reader.git
```

## Commands

- `tg-reader auth` — one-time interactive authorization, run manually by the
  user (stores credentials and the session file)
- `tg-reader read CHAT_ID` — read recent messages from a chat by its numeric
  ID, JSON output; `--help` contains everything an agent needs. Fetched
  messages are also stored in a local SQLite cache (`cache.db`) that answers
  repeated history reads without touching Telegram and that other local
  tools can query directly
- `tg-reader download CHAT_ID MSG_ID --output DIR` — download the media
  attachment of one message (message IDs and media metadata come from `read`)

## Agent skill

[skills/tg-reader/](skills/tg-reader/) is an agent skill that makes AI agents
aware of this CLI in future sessions: when to reach for it, how to resolve a
chat name to its numeric ID via a local alias registry, and to consult
`--help` for the exact CLI contract instead of relying on memory. The install
prompt above installs it; to install manually, copy the folder to
`~/.claude/skills/tg-reader/` (Claude Code) or `~/.codex/skills/tg-reader/`
(Codex).

See [docs/PROJECT.md](docs/PROJECT.md) for the full project description.
