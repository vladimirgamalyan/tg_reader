# tg-reader

[![CI](https://github.com/vladimirgamalyan/tg_reader/actions/workflows/ci.yml/badge.svg)](https://github.com/vladimirgamalyan/tg_reader/actions/workflows/ci.yml)

A small CLI utility for reading messages from your own Telegram account
(MTProto via Telethon). Designed to be used by AI agents.

## Installation

Ask your AI agent to install this utility by giving it the address of this page.

Or install it manually:

```sh
uv tool install git+https://github.com/vladimirgamalyan/tg_reader.git
```

## Commands

- `tg-reader auth` — one-time interactive authorization, run manually by the
  user (stores credentials and the session file)
- `tg-reader read CHAT_ID` — read recent messages from a chat by its numeric
  ID, JSON output; `--help` contains everything an agent needs
- `tg-reader download CHAT_ID MSG_ID --output DIR` — download the media
  attachment of one message (message IDs and media metadata come from `read`)

See [docs/PROJECT.md](docs/PROJECT.md) for the full project description.
