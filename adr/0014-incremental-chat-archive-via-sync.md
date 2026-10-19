# 0014. Incremental local chat archive via a `sync` command

Status: Accepted

Related: [0006](0006-remove-message-cache.md) (Remove the local message
cache), whose consequences call for a durable archive to come back, if ever
needed, as its own deliberate feature rather than as a side effect of
`read`.

## Context

A concrete use case appeared that ADR 0006 did not weigh: going through a
long conversation (thousands of messages) and coming back to it days later.
With `read` alone every pass costs one run per 100 messages: dozens of
paced GetHistory requests and tool calls, and the agent has to pull the
whole history into its context instead of searching it.

Alternatives considered:

- **Bring back a read-through cache in `read`** — rejected for the reasons
  in 0006: serving `--offset-id` windows locally needs coverage-range
  bookkeeping, deletion handling and schema migrations.
- **Let `read` write every fetched page into an archive** — rejected: the
  archive would have gaps wherever nobody paged, so it could not be trusted
  as a complete transcript without that same coverage bookkeeping.
- **Skill-only: the agent saves `read` pages to a file itself** — rejected:
  every agent would re-implement the "fetch only what is new and merge it"
  logic, and get it wrong eventually.
- **SQLite instead of JSONL** — rejected: nothing needs queries beyond
  grep/jq, the global lock already rules out concurrent writers, and a
  JSONL file needs no schema versioning.
- **An explicit `sync` command keeping a gap-free JSONL file (chosen).**

## Decision

We will add `tg-reader sync CHAT_ID [--since YYYY-MM-DD]`, which keeps one
JSONL file per chat in `<config dir>/archive/<marked chat id>.jsonl`: one
message per line, oldest first, in the `read` output format. `read` stays
unchanged and never touches the archive.

- The first sync of a chat requires `--since` and fetches every message from
  that date (UTC) on. Later syncs fetch only messages newer than the last
  archived one (`iter_messages(reverse=True, min_id=...)`). This keeps the
  file a contiguous range of history with no gaps, so no coverage bookkeeping
  is needed: the last line alone says where to continue.
- `--since` is ignored for an existing archive instead of being rejected, so
  "retry the same command" after an interrupted first sync continues the
  archive. Starting from an earlier date means deleting the file.
- The file is rewritten through a temporary file and renamed, never appended
  in place, so a crash cannot leave a half-written line. A fetch interrupted
  by a flood wait or a network failure still saves what it got, and the next
  sync resumes from there; a failure of that save never replaces the error
  in flight.
- Requests within a run are paced like separate runs (at least 2 s apart,
  Telethon's `wait_time`), and the run holds the global lock throughout.

## Consequences

- Coming back to a conversation costs a request or two, and the agent
  searches a local file instead of paging the history through its context.
- The archive is stale by design: edits and deletions after a message was
  archived never show up (documented in `sync --help`; rebuild by deleting
  the file).
- A sync is unbounded in request count, like a download; the bound is the
  `--since` date chosen by the caller. A long first sync holds the lock for
  minutes, and concurrent runs fail with exit code 2 meanwhile.
- Archive lines follow the `read` schema of the version that wrote them; a
  later schema change leaves older lines with the old field set. No
  migration is planned: a consumer treats missing fields as absent.
- Message text is now stored on disk, in the same private config directory
  as the session file.
