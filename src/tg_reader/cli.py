"""Command-line interface: argparse subcommands and agent-oriented help texts."""

import argparse
import asyncio
import json
import sys
from importlib.metadata import version as package_version
from pathlib import Path

from .auth import run_auth
from .download import DEFAULT_MAX_SIZE_MB, run_download
from .errors import PermanentError
from .read import run_read
from .throttle import MAX_LIMIT, RetryLaterError

MAIN_DESCRIPTION = """\
Read messages and download media from your own Telegram account (MTProto
user account, not a bot).

This tool is designed for use by AI agents:
  - 'read' and 'download' print machine-readable JSON to stdout and
    nothing else;
  - errors go to stderr and the exit code is non-zero on failure:
    1 means a permanent error (do not retry with the same arguments),
    2 means temporarily unavailable (wait and retry; the stderr message
    says 'retry after Ns');
  - 'auth' is the one exception: it is interactive and must be run
    manually by a human once, before the other commands can be used.
"""

MAIN_EPILOG = """\
examples:
  tg-reader auth                      one-time interactive authorization
  tg-reader read -1001234567890       last 20 messages from a channel
  tg-reader download -1001234567890 555 --output ./files
                                      save the media of message 555
  tg-reader read --help               full 'read' reference (arguments,
                                      CHAT_ID formats, JSON output schema)
  tg-reader download --help           full 'download' reference
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

CHAT_ID is a non-zero numeric chat/channel/user ID. Accepted forms:
  -100<id>   Bot-API-style marked ID of a channel or supergroup
  -<id>      Bot-API-style marked ID of a small group chat
  <id>       raw positive MTProto ID (user, channel or small group chat)

The account must be a member of the chat (or have the dialog open). Unknown
IDs are looked up in the account's dialog list; if the chat is still not
found, the command fails with an error on stderr.
"""

READ_EPILOG = """\
output schema (JSON array on stdout, newest message first), each element:
  id               int       message ID; pass it to --offset-id to
                             paginate, or to 'tg-reader download' to
                             fetch the media
  date             str|null  ISO 8601 timestamp in UTC,
                             e.g. "2026-07-03T12:34:56+00:00"
  sender_id        int|null  numeric ID of the sender (marked format)
  sender_name      str|null  display name of the sender
  text             str|null  message text or media caption; null if none
                             (service messages, media without caption)
  reply_to_msg_id  int|null  ID of the message this one replies to; null
                             when the message is not a reply
  grouped_id       int|null  album ID, see 'albums' below; null for
                             standalone messages
  media            obj|null  downloadable attachment metadata; null when
                             the message has none (plain text, polls, geo,
                             contacts; link previews do not count). Fields:
    type         str       photo|video|audio|voice|video_note|sticker|
                           gif|document
    filename     str|null  original filename; null if unnamed (photos,
                           voice messages)
    mime_type    str|null  MIME type; null if unknown
    size_bytes   int|null  file size; null when Telegram does not expose it;
                           compare it against the --max-size limit of
                           'tg-reader download' (download refuses unknown size)

albums (grouped media):
  An album (several photos/files sent together) is several separate
  messages sharing one grouped_id, each with its own id and its own media;
  the caption text is carried by only one of them. To fetch a whole album,
  run 'tg-reader download' once per album message. Album message IDs are
  usually consecutive, but that is not guaranteed -- always use the actual
  id values. If the oldest messages in the output carry a grouped_id, the
  album may be cut off by --limit: paginate with --offset-id to fetch the
  rest of it.

errors and exit codes:
  All errors are printed to stderr; stdout stays empty.
  0  success
  1  permanent error: bad arguments, unknown chat, not authorized.
     Do not retry with the same arguments. If the tool is not authorized
     yet, the error says to run 'tg-reader auth' -- that command is
     interactive and must be run by a human, do not run it as an agent.
  2  temporarily unavailable, the stderr message ends with
     'retry after Ns': wait that long, then retry the same command.
     Causes: another tg-reader process is running, Telegram assigned a
     flood wait (rate limiting), or the network is down.

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

DOWNLOAD_DESCRIPTION = """\
Download the media attachment of a single message into a directory, then
print a JSON object describing the saved file to stdout.

Intended flow: run 'tg-reader read' first, inspect the 'media' field of the
messages (type, size) and pick the message IDs worth fetching, then run this
command once per message. The message is re-fetched at download time, so it
does not matter how old the 'read' output is.

CHAT_ID is a non-zero numeric chat/channel/user ID, same accepted forms as in
'tg-reader read' (see its --help). MSG_ID is the positive 'id' field from
the 'read' output; the message must actually carry media ('media' is not null).

The file is saved into --output DIR (created if missing) under the name
'<CHAT_ID>_<MSG_ID>_<original filename>', sanitized; media without a name
(photos, voice messages) gets a generated name such as
'-1001234567890_555_photo.jpg'. The name is deterministic and an existing
file is silently overwritten, so re-running the same download is idempotent;
the CHAT_ID prefix keeps downloads from different chats apart even in a
shared output directory. The exact absolute path is printed in the
output. Media with unknown size is refused because --max-size cannot be
checked before the transfer.
"""

DOWNLOAD_EPILOG = """\
output schema (single JSON object on stdout):
  message_id   int    ID of the message the file came from
  type         str    photo|video|audio|voice|video_note|sticker|gif|document
  file         str    absolute path of the saved file
  size_bytes   int    actual size of the saved file in bytes

albums (grouped media):
  An album is several messages sharing one 'grouped_id' (see the 'read'
  output), each with its own message ID and its own media. This command
  downloads exactly one message; to fetch a whole album, run it once per
  album message.

errors and exit codes:
  All errors are printed to stderr; stdout stays empty.
  0  success
  1  permanent error: bad arguments, unknown chat, message not found,
     message has no downloadable media, file size is unknown, file exceeds
     --max-size (the error names the actual size), not authorized. Do not
     retry with the same arguments.
  2  temporarily unavailable, the stderr message ends with
     'retry after Ns': wait that long, then retry the same command.
     Causes: another tg-reader run (downloads can take a while; any other
     run fails fast meanwhile), a Telegram flood wait, or the network is
     down.

flood protection (automatic, no configuration):
  Same as 'read': only one tg-reader process talks to Telegram at a time,
  consecutive runs are spaced at least 2 seconds apart, and Telegram
  flood waits are respected across runs.

examples:
  tg-reader download -1001234567890 555 --output ./files
  tg-reader download -1001234567890 555 --output ./files --max-size 500
"""


class Parser(argparse.ArgumentParser):
    """ArgumentParser that exits with code 1 on usage errors.

    The default argparse exit code for usage errors is 2, which this tool
    reserves for "temporarily unavailable, retry later".
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"error: {message}\n")


def _parse_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None


def chat_id_type(value: str) -> int:
    chat_id = _parse_int(value)
    if chat_id == 0:
        raise argparse.ArgumentTypeError("must be a non-zero integer")
    return chat_id


def positive_int_type(value: str) -> int:
    number = _parse_int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def limit_type(value: str) -> int:
    limit = _parse_int(value)
    if not 1 <= limit <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_LIMIT}")
    return limit


def max_size_type(value: str) -> int:
    return positive_int_type(value)


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="tg-reader",
        description=MAIN_DESCRIPTION,
        epilog=MAIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version('tg-reader')}",
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
        type=chat_id_type,
        help="non-zero numeric chat/channel/user ID (see accepted forms above)",
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
        type=positive_int_type,
        default=0,
        help="fetch only messages older than this positive message ID (default: newest)",
    )

    download_parser = subparsers.add_parser(
        "download",
        help="download the media attachment of one message, JSON output",
        description=DOWNLOAD_DESCRIPTION,
        epilog=DOWNLOAD_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    download_parser.add_argument(
        "chat_id",
        metavar="CHAT_ID",
        type=chat_id_type,
        help="non-zero numeric chat/channel/user ID, same forms as in 'read'",
    )
    download_parser.add_argument(
        "msg_id",
        metavar="MSG_ID",
        type=positive_int_type,
        help="positive message ID (the 'id' field of the 'read' output)",
    )
    download_parser.add_argument(
        "--output",
        metavar="DIR",
        required=True,
        help="destination directory; created if missing",
    )
    download_parser.add_argument(
        "--max-size",
        metavar="MB",
        type=max_size_type,
        default=DEFAULT_MAX_SIZE_MB,
        help="refuse to download files larger than this many MB (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # On Windows, redirected stdout/stderr default to the legacy ANSI code
    # page, which cannot encode arbitrary text (emoji, paths etc.). JSON
    # output and error messages must always be UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "auth":
            asyncio.run(run_auth())
        elif args.command == "read":
            messages = asyncio.run(run_read(args.chat_id, args.limit, args.offset_id))
            json.dump(messages, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            result = asyncio.run(
                run_download(
                    args.chat_id, args.msg_id, Path(args.output), args.max_size
                )
            )
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
            print()
    except RetryLaterError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except PermanentError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - CLI boundary, report and exit
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
