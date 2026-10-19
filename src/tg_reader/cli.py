"""Command-line interface: argparse subcommands and agent-oriented help texts."""

import argparse
import asyncio
import errno
import json
import os
import re
import sys
from datetime import date
from importlib.metadata import version as package_version
from pathlib import Path

from .auth import run_auth
from .chats import run_chats
from .download import DEFAULT_MAX_SIZE_MB, run_download
from .errors import PermanentError
from .read import run_read
from .sync import run_sync
from .throttle import MAX_LIMIT, RetryLaterError
from .topics import MAX_TOPICS, run_topics

MAIN_DESCRIPTION = """\
Read messages and download media from your own Telegram account (MTProto
user account, not a bot).

This tool is designed for use by AI agents:
  - every command except 'auth' prints machine-readable JSON to stdout
    and nothing else;
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
  tg-reader chats                     list chats with their IDs
  tg-reader chats --unread            chats with unread messages
  tg-reader read -1001234567890       last 20 messages from a channel
  tg-reader topics -1001234567890     topics of a forum group
  tg-reader download -1001234567890 555 --output ./files
                                      save the media of message 555
  tg-reader sync -1001234567890 --since 2026-09-01
                                      archive a long conversation locally
  tg-reader chats --help              full 'chats' reference
  tg-reader read --help               full 'read' reference (arguments,
                                      CHAT_ID formats, JSON output schema)
  tg-reader download --help           full 'download' reference
  tg-reader topics --help             full 'topics' reference
  tg-reader sync --help               full 'sync' reference
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

CHATS_DESCRIPTION = """\
List the chats of this account (its dialog list, archived chats included)
with their IDs, and print them as a JSON array to stdout. Use it to find
the CHAT_ID of a chat known only by name, then pass 'id' to the other
commands.

Prefer --query with a distinctive part of the name: it keeps only chats
whose title or username contains that text (case-insensitive substring).
The full list of an account with a few hundred chats is tens of KB. If
nothing matches, try another part or spelling of the name (e.g. a Latin
transliteration) before falling back to the full list.

Titles are not unique and can change: when several chats plausibly match
a name, do not pick one at random.

To find out where new messages are, use --unread: it keeps only chats with
unread messages. Unread counts are those of the account's Telegram apps;
tg-reader itself never marks anything as read.
"""

CHATS_EPILOG = """\
output schema (JSON array on stdout, in the order of the Telegram chat
list: pinned chats first, then by latest activity), each element:
  id            int       chat ID in Bot-API-style marked form (-100...
                          for channels and supergroups, -... for small
                          groups, positive for users); pass it as
                          CHAT_ID to the other commands
  title         str|null  chat title, or the user's display name; null
                          if empty (e.g. a deleted account)
  type          str       'user' (private chat, bots included), 'group'
                          (small group or supergroup) or 'channel'
  username      str|null  public @username without the '@'; null if none
  unread_count  int       number of unread messages; to read them, pass
                          it to 'tg-reader read --limit' (at most 100 per
                          run, paginate with --offset-id for more)

Small groups that were upgraded to a supergroup are left out, as in the
official apps: only the supergroup is listed.

errors and exit codes:
  All errors are printed to stderr; stdout stays empty.
  0  success
  1  permanent error: not authorized. Do not retry. Also reported as
     'stdout closed early, output truncated' when the consumer of stdout
     stopped reading: the JSON on stdout is incomplete.
  2  temporarily unavailable, the stderr message ends with
     'retry after Ns': wait that long, then retry the same command.
     Causes: another tg-reader process is running, Telegram assigned a
     flood wait (rate limiting), or the network is down.

flood protection (automatic, no configuration):
  Same as 'read'. The listing costs one Telegram request per 100 chats.

examples:
  tg-reader chats --query samui      chats with 'samui' in the title or username
  tg-reader chats --unread           chats with unread messages
  tg-reader chats                    the whole chat list
"""

READ_DESCRIPTION = """\
Fetch recent messages from one chat and print them as a JSON array to stdout,
newest message first.

CHAT_ID is a non-zero numeric chat/channel/user ID. Accepted forms:
  -100<id>   Bot-API-style marked ID of a channel or supergroup
  -<id>      Bot-API-style marked ID of a small group chat
  <id>       raw positive MTProto ID (user, channel or small group chat)
'tg-reader chats' lists the account's chats with their IDs.

The account must be a member of the chat (or have the dialog open). Unknown
IDs are looked up in the account's dialog list; if the chat is still not
found, the command fails with an error on stderr. Chats and users this
account has already encountered (for example the sender of a message in a
group read earlier) resolve without an open dialog; reading such a peer
can succeed and return an empty array instead of failing.

Two optional filters narrow the messages; they cannot be combined:
  --topic TOPIC_ID  only the messages of one topic of a forum group;
                    'tg-reader topics CHAT_ID' lists the topics with their
                    IDs and titles
  --search TEXT     only the messages Telegram's own search finds, over the
                    whole chat history (the search box of the Telegram
                    apps): case-insensitive whole words, with word forms
                    matched for some languages (e.g. Russian), but no
                    partial words ('clau' does not find 'Claude')
Both work with --limit and --offset-id like an unfiltered read.
"""

READ_EPILOG = """\
output schema (JSON array on stdout, newest message first), each element:
  id               int       message ID; pass it to --offset-id to
                             paginate, or to 'tg-reader download' to
                             fetch the media
  date             str|null  ISO 8601 timestamp in UTC,
                             e.g. "2026-07-03T12:34:56+00:00"
  edit_date        str|null  ISO 8601 UTC time the message was last edited;
                             null if it was never edited
  sender_id        int|null  numeric ID of the sender (marked format)
  sender_name      str|null  display name of the sender
  text             str|null  message text or media caption; null if none
                             (service messages, media without caption)
  entities         arr|null  hyperlinks found in 'text', in document order;
                             null when the message has none. Only URL entities
                             are emitted for now; treat unknown 'type' values
                             as ignorable. Each element:
    type         str       'text_url' (a URL hidden behind display text) or
                           'url' (a plain URL that is its own text)
    text         str       the displayed text of the link (e.g. "Original")
    url          str       the target URL; for 'text_url' the hidden href
                           behind 'text', for 'url' equal to 'text'. Use this
                           to recover link targets that 'text' alone drops
  topic_id         int|null  forum topic root message ID; null outside
                             forum topics, in the General topic, or when
                             Telegram does not expose it
  reply_to_msg_id  int|null  ID of the message this one replies to; null
                             when the message is not a reply. Plain posts
                             inside a forum topic are not replies, even
                             though Telegram tags them with the topic root.
                             Quote replies to a message in another chat are
                             also null: the target is not in this chat
  is_service       bool      true for Telegram service/action messages
                             (joins, pins, title changes, topic events)
  grouped_id       int|null  album ID, see 'albums' below; null for
                             standalone messages
  forwarded_from   obj|null  origin of a forwarded message; null when the
                             message was not forwarded. Fields:
    id           int|null  marked ID of the original author or channel;
                           null when the author hides their account
    name         str|null  name of the original author or channel; null
                           if Telegram does not expose it
    date         str|null  ISO 8601 UTC time of the original message; it
                           can be much older than 'date' (the forward time)
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
  1  permanent error: bad arguments, unknown chat, an invalid --topic ID
     (TOPIC_ID_INVALID), not authorized. Do not retry with the same
     arguments. If the tool is not authorized yet, the error says to run
     'tg-reader auth' -- that command is interactive and must be run by
     a human, do not run it as an agent.
     Also reported as 'stdout closed early, output truncated' when the
     consumer of stdout stopped reading (e.g. piping into 'head'): the
     JSON on stdout is incomplete and must not be parsed as-is.
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
  tg-reader read -1001234567890 --topic 42         last 20 messages of topic 42
  tg-reader read -1001234567890 --search rent      last 20 messages about 'rent'
"""

TOPICS_DESCRIPTION = """\
List the topics of a forum group (a supergroup with topics) and print them
as a JSON array to stdout. Use it to find the TOPIC_ID for
'tg-reader read --topic' when a topic is known only by its title.

CHAT_ID is a non-zero numeric chat/channel/user ID, same accepted forms as
in 'tg-reader read' (see its --help).
"""

TOPICS_EPILOG = f"""\
output schema (JSON array on stdout, in the order of the Telegram topic
list: pinned topics first, then by latest activity; at most the
{MAX_TOPICS} most recently active topics), each element:
  id            int  topic ID; pass it to 'tg-reader read --topic'. The
                     General topic has ID 1
  title         str  topic title
  unread_count  int  number of unread messages in the topic

errors and exit codes:
  All errors are printed to stderr; stdout stays empty.
  0  success
  1  permanent error: bad arguments, unknown chat, the chat is not a forum
     (it has no topics: read it without --topic), not authorized. Do not
     retry with the same arguments. Also reported as 'stdout closed
     early, output truncated' when the consumer of stdout stopped
     reading: the JSON on stdout is incomplete.
  2  temporarily unavailable, the stderr message ends with
     'retry after Ns': wait that long, then retry the same command.
     Causes: another tg-reader process is running, Telegram assigned a
     flood wait (rate limiting), or the network is down.

flood protection (automatic, no configuration):
  Same as 'read'.

examples:
  tg-reader topics -1001234567890
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
     --max-size (the error names the actual size; a file that turns out
     larger than declared is aborted mid-transfer), a local file problem
     (unwritable --output directory, disk full), not authorized. Do not
     retry with the same arguments. Also reported as 'stdout closed
     early, output truncated' when the consumer of stdout stopped
     reading: the JSON on stdout is incomplete and must not be parsed
     as-is (the downloaded file itself is intact).
  2  temporarily unavailable, the stderr message ends with
     'retry after Ns': wait that long, then retry the same command.
     Causes: another tg-reader run (downloads can take a while; a
     concurrent run waits up to 30s for the lock, then fails), a Telegram
     flood wait, or the network is down.

flood protection (automatic, no configuration):
  Same as 'read': only one tg-reader process talks to Telegram at a time,
  consecutive runs are spaced at least 2 seconds apart, and Telegram
  flood waits are respected across runs.

examples:
  tg-reader download -1001234567890 555 --output ./files
  tg-reader download -1001234567890 555 --output ./files --max-size 500
"""

SYNC_DESCRIPTION = """\
Bring the local archive of one chat up to date, then print a JSON summary
to stdout. Use it to go through a long conversation, or to come back to one
days later, instead of paging through it with 'tg-reader read'.

The archive is a JSONL file in the per-user config directory (Windows:
%APPDATA%\\tg-reader\\archive, Linux: ~/.config/tg-reader/archive), one per
chat: one message per line, oldest first, each line a JSON object with the
same fields as an element of the 'tg-reader read' output. Read or search
that file directly; its path is printed in the output.

The first sync of a chat needs --since and fetches every message sent from
that date (UTC) on. Every later sync fetches only the messages newer than
the last archived one, so coming back to a conversation costs a request or
two instead of re-reading it. --since only starts a new archive: for an
existing one it is ignored, and the archive keeps its original start date.
The archive has no gaps: an interrupted sync keeps what it fetched, and the
next sync continues from there.

CHAT_ID is a non-zero numeric chat/channel/user ID, same accepted forms as
in 'tg-reader read' (see its --help).
"""

SYNC_EPILOG = """\
output schema (single JSON object on stdout):
  file    str  absolute path of the archive file; the file is not created
               until at least one message is archived ('total' > 0)
  added   int  number of messages appended by this run
  total   int  number of messages in the archive after this run

staleness:
  Archived messages are never refreshed: edits and deletions made after a
  message was archived do not show up in the file ('edit_date' reflects
  the time of archiving). To rebuild an archive, or to start it from an
  earlier date, delete the file and sync again with --since.

errors and exit codes:
  All errors are printed to stderr; stdout stays empty.
  0  success
  1  permanent error: bad arguments, unknown chat, --since missing for a
     chat that has no archive yet, an unreadable, unwritable or damaged
     archive file, not authorized. Do not retry with the same arguments.
     Also reported as 'stdout closed early, output truncated' when the
     consumer of stdout stopped reading: the JSON on stdout is incomplete
     and must not be parsed as-is (the archive itself is intact).
  2  temporarily unavailable, the stderr message ends with
     'retry after Ns': wait that long, then retry the same command.
     Causes: another tg-reader run (a long sync makes a concurrent run
     wait up to 30s for the lock, then fail), a Telegram flood wait, or
     the network is down. Messages fetched before the failure are already
     archived; the retry continues after them.

flood protection (automatic, no configuration):
  Same as 'read', plus: a sync makes one Telegram request per 100
  messages, spaced at least 2 seconds apart, and holds the lock for its
  whole duration. An early --since on a busy chat means many requests:
  pick the latest date that covers what is needed.

examples:
  tg-reader sync -1001234567890 --since 2026-09-01   start an archive
  tg-reader sync -1001234567890                      fetch new messages
"""


class Parser(argparse.ArgumentParser):
    """ArgumentParser that exits with code 1 on usage errors.

    The default argparse exit code for usage errors is 2, which this tool
    reserves for "temporarily unavailable, retry later".
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"error: {message}\n")


# ASCII digits with an optional minus sign only: int() alone also accepts
# digit-grouping underscores ("1_0" == 10), surrounding whitespace, "+" and
# non-ASCII decimal digits — a mistyped chat ID must be rejected, not
# silently read as a different chat.
_INTEGER = re.compile(r"-?[0-9]+")


def _parse_int(value: str) -> int:
    if not _INTEGER.fullmatch(value):
        raise argparse.ArgumentTypeError("must be an integer")
    return int(value)


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


def offset_id_type(value: str) -> int:
    # 0 is the documented default ("start from the newest"); an agent
    # passing it explicitly must not be rejected.
    number = _parse_int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return number


def limit_type(value: str) -> int:
    limit = _parse_int(value)
    if not 1 <= limit <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_LIMIT}")
    return limit


def max_size_type(value: str) -> int:
    return positive_int_type(value)


def date_type(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "must be a date in YYYY-MM-DD format"
        ) from None


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

    chats_parser = subparsers.add_parser(
        "chats",
        help="list the account's chats with their IDs, JSON output",
        description=CHATS_DESCRIPTION,
        epilog=CHATS_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    chats_parser.add_argument(
        "--query",
        metavar="TEXT",
        help="only chats whose title or username contains TEXT (case-insensitive)",
    )
    chats_parser.add_argument(
        "--unread",
        action="store_true",
        help="only chats with unread messages",
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
        type=offset_id_type,
        default=0,
        help="fetch only messages older than this message ID; "
        "0 starts from the newest (default: %(default)s)",
    )
    read_filter = read_parser.add_mutually_exclusive_group()
    read_filter.add_argument(
        "--topic",
        metavar="TOPIC_ID",
        type=positive_int_type,
        help="only messages of this forum topic (IDs from 'tg-reader topics')",
    )
    read_filter.add_argument(
        "--search",
        metavar="TEXT",
        help="only messages found by Telegram's search for TEXT",
    )

    topics_parser = subparsers.add_parser(
        "topics",
        help="list the topics of a forum group, JSON output",
        description=TOPICS_DESCRIPTION,
        epilog=TOPICS_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    topics_parser.add_argument(
        "chat_id",
        metavar="CHAT_ID",
        type=chat_id_type,
        help="non-zero numeric chat ID of a forum group, same forms as in 'read'",
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

    sync_parser = subparsers.add_parser(
        "sync",
        help="update the local archive of a chat, JSON output",
        description=SYNC_DESCRIPTION,
        epilog=SYNC_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sync_parser.add_argument(
        "chat_id",
        metavar="CHAT_ID",
        type=chat_id_type,
        help="non-zero numeric chat/channel/user ID, same forms as in 'read'",
    )
    sync_parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        type=date_type,
        help="start date (UTC) of a new archive; required for the first sync "
        "of a chat, ignored once the archive exists",
    )
    return parser


def _exit_closed_stdout() -> int:
    """Exit path for a stdout consumer that closed the pipe early.

    Piping into e.g. 'head' is not a tool failure, but the output was
    truncated, so the exit code stays non-zero — and the documented
    contract says every failure explains itself on stderr, which is not
    part of the closed pipe. Closing stdout keeps the interpreter's exit
    flush from raising again.
    """
    try:
        print("error: stdout closed early, output truncated", file=sys.stderr)
    except OSError:
        pass
    try:
        sys.stdout.close()
    except OSError:
        pass
    return 1


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
        elif args.command == "chats":
            chats = asyncio.run(run_chats(args.query, args.unread))
            json.dump(chats, sys.stdout, ensure_ascii=False, indent=2)
            print()
        elif args.command == "read":
            messages = asyncio.run(
                run_read(
                    args.chat_id, args.limit, args.offset_id, args.topic, args.search
                )
            )
            json.dump(messages, sys.stdout, ensure_ascii=False, indent=2)
            print()
        elif args.command == "topics":
            topics = asyncio.run(run_topics(args.chat_id))
            json.dump(topics, sys.stdout, ensure_ascii=False, indent=2)
            print()
        elif args.command == "sync":
            result = asyncio.run(run_sync(args.chat_id, args.since))
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            result = asyncio.run(
                run_download(
                    args.chat_id, args.msg_id, Path(args.output), args.max_size
                )
            )
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
            print()
        # Force the buffered output out to the pipe now, while a consumer
        # that already closed it still surfaces as BrokenPipeError (or
        # EINVAL on Windows) here, where the handlers below map it to the
        # documented "output truncated" (exit 1). A small JSON payload stays
        # buffered otherwise, and that first write to the closed pipe would
        # happen in the interpreter's shutdown flush — after main() returned
        # 0 — escaping as an "Exception ignored" note and a 120 exit code.
        sys.stdout.flush()
    except RetryLaterError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except PermanentError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # Interactive 'auth' is the realistic target of a Ctrl+C; exit
        # quietly with the conventional code instead of a traceback.
        print("interrupted", file=sys.stderr)
        return 130
    except EOFError as error:
        # The 'auth' prompts (api_id, phone number, code) read stdin; when
        # it is closed (Ctrl+Z / Ctrl+D, piped input running dry) the raw
        # "EOFError:" message would not explain what happened. The other
        # commands never read stdin (an EOFError there is e.g. a stray
        # asyncio.IncompleteReadError), so the stdin explanation would
        # mislead: report the raw error instead.
        if args.command == "auth":
            print(
                "error: stdin closed while waiting for interactive input",
                file=sys.stderr,
            )
        else:
            print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return _exit_closed_stdout()
    except OSError as error:
        if os.name == "nt" and error.errno == errno.EINVAL:
            # The Windows C runtime reports a write to a closed pipe as
            # EINVAL instead of BrokenPipeError.
            return _exit_closed_stdout()
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - CLI boundary, report and exit
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
