"""Command-line interface: argparse subcommands and agent-oriented help texts."""

import argparse
import asyncio
import json
import sys

from .auth import run_auth
from .read import ChatNotFoundError, NotAuthorizedError, run_read
from .throttle import MAX_LIMIT, RetryLaterError

MAIN_DESCRIPTION = """\
Read messages from your own Telegram account (MTProto user account, not a bot).

This tool is designed for use by AI agents:
  - 'read' prints machine-readable JSON to stdout and nothing else;
  - errors go to stderr and the exit code is non-zero on failure:
    1 means a permanent error (do not retry with the same arguments),
    2 means temporarily unavailable (wait and retry; the stderr message
    says 'retry after Ns');
  - 'auth' is the one exception: it is interactive and must be run
    manually by a human once, before 'read' can be used.
"""

MAIN_EPILOG = """\
examples:
  tg-reader auth                      one-time interactive authorization
  tg-reader read -1001234567890       last 20 messages from a channel
  tg-reader read --help               full 'read' reference (arguments,
                                      CHAT_ID formats, JSON output schema)
"""

AUTH_DESCRIPTION = """\
One-time interactive authorization. Not intended for AI agents: run it
manually in a terminal.

Prompts for the Telegram API credentials (api_id and api_hash, obtained at
https://my.telegram.org -> 'API development tools') unless they are already
stored, then runs the interactive login flow: phone number -> confirmation
code -> two-factor password (if enabled).

Stores in the per-user config directory (Windows: %APPDATA%\\tg-reader,
Linux: ~/.config/tg-reader):
  - config.json          api_id and api_hash
  - tg_reader.session    Telethon session file (login state)

If the session is already authorized, prints the account info and exits.
"""

READ_DESCRIPTION = """\
Fetch recent messages from one chat and print them as a JSON array to stdout,
newest message first.

CHAT_ID is a numeric chat/channel/user ID. Accepted forms:
  -100<id>   Bot-API-style marked ID of a channel or supergroup
  -<id>      Bot-API-style marked ID of a small group chat
  <id>       raw positive MTProto ID (user, channel or small group chat)

The account must be a member of the chat (or have the dialog open). Unknown
IDs are looked up in the account's dialog list; if the chat is still not
found, the command fails with an error on stderr.
"""

READ_EPILOG = """\
output schema (JSON array on stdout, newest message first), each element:
  id           int         message ID; pass it to --offset-id to paginate
  date         str|null    ISO 8601 timestamp in UTC,
                           e.g. "2026-07-03T12:34:56+00:00"
  sender_id    int|null    numeric ID of the sender (marked format)
  sender_name  str|null    display name of the sender
  text         str|null    message text or media caption; null if none
                           (service messages, media without caption)

errors and exit codes:
  All errors are printed to stderr; stdout stays empty.
  0  success
  1  permanent error: bad arguments, unknown chat, not authorized.
     Do not retry with the same arguments. If the tool is not authorized
     yet, the error says to run 'tg-reader auth' -- that command is
     interactive and must be run by a human, do not run it as an agent.
  2  temporarily unavailable, the stderr message ends with
     'retry after Ns': wait that long, then retry the same command.
     Causes: another tg-reader process is running, or Telegram assigned
     a flood wait (rate limiting).

flood protection (automatic, no configuration):
  Only one tg-reader process talks to Telegram at a time; consecutive
  runs are spaced at least 2 seconds apart (excess calls just wait).
  Telegram-assigned flood waits are respected across runs: until the
  wait expires, every run fails fast with exit code 2.

examples:
  tg-reader read -1001234567890                    last 20 channel messages
  tg-reader read 123456789 --limit 50              last 50 messages of a user chat
  tg-reader read -1001234567890 --offset-id 5000   messages older than message 5000
"""


class Parser(argparse.ArgumentParser):
    """ArgumentParser that exits with code 1 on usage errors.

    The default argparse exit code for usage errors is 2, which this tool
    reserves for "temporarily unavailable, retry later".
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"error: {message}\n")


def limit_type(value: str) -> int:
    limit = int(value)
    if not 1 <= limit <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_LIMIT}")
    return limit


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="tg-reader",
        description=MAIN_DESCRIPTION,
        epilog=MAIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "auth",
        help="one-time interactive authorization (run manually, not for agents)",
        description=AUTH_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    read_parser = subparsers.add_parser(
        "read",
        help="read recent messages from a chat by numeric ID, JSON output",
        description=READ_DESCRIPTION,
        epilog=READ_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    read_parser.add_argument(
        "chat_id",
        metavar="CHAT_ID",
        type=int,
        help="numeric chat/channel/user ID (see accepted forms above)",
    )
    read_parser.add_argument(
        "--limit",
        metavar="N",
        type=limit_type,
        default=20,
        help=f"number of messages to fetch, 1..{MAX_LIMIT} (default: %(default)s)",
    )
    read_parser.add_argument(
        "--offset-id",
        metavar="MSG_ID",
        type=int,
        default=0,
        help="fetch only messages older than this message ID (default: newest)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # On Windows, redirected stdout defaults to the legacy ANSI code page,
    # which cannot encode arbitrary message text (emoji etc.). JSON output
    # must always be UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "auth":
            asyncio.run(run_auth())
        else:
            messages = asyncio.run(run_read(args.chat_id, args.limit, args.offset_id))
            json.dump(messages, sys.stdout, ensure_ascii=False, indent=2)
            print()
    except RetryLaterError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (NotAuthorizedError, ChatNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - CLI boundary, report and exit
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
