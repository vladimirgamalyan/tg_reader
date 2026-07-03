# tg-reader

A small CLI utility for reading messages from your own Telegram account
(MTProto via Telethon). Designed to be used by AI agents.

Installation:

```
uv tool install git+<repo-url>
```

From a local clone:

```
uv tool install <path-to-repo>
```

- `tg-reader auth` — one-time interactive authorization, run manually by the
  user (stores credentials and the session file)
- `tg-reader read CHAT_ID` — read recent messages from a chat by its numeric
  ID, JSON output; `--help` contains everything an agent needs

See [docs/PROJECT.md](docs/PROJECT.md) for the full project description.
