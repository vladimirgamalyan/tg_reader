# 0002. Local message cache in SQLite

Status: Accepted

## Context

Agents repeatedly re-read the same history ranges, and every `read` run
costs a Telegram GetHistory request plus the flood-protection overhead
(inter-process lock, 2 s pacing). The user also wants other local
applications to consume already-fetched messages without going through
tg-reader, which asks for a widely readable on-disk format.

Alternatives considered:

- **JSONL file per chat** — trivially greppable, but deduplication,
  refreshing edited messages and range bookkeeping all need custom sidecar
  state, and concurrent writers are unsafe.
- **Write-only archive** (no cache serving) — simplest, no staleness
  questions, but saves zero requests.
- **Serving the newest page (`offset_id = 0`) from the cache with a TTL** —
  rejected: only the server knows whether newer messages exist, and a TTL
  would just move the staleness window around while breaking the "newest
  messages" contract.
- **Opt-in flag instead of default-on** — rejected: the primary consumers
  are agents driven by `--help`; an opt-in cache would simply never be used.

## Decision

We will store every fetched message in an SQLite database (`cache.db` in
the per-user config directory) and serve `read --offset-id` requests from
it when the requested window is fully covered — enabled by default, with
`--no-cache` to force a network fetch (which still refreshes the cache).

Key points:

- A `messages` table with explicit columns mirroring the `read` output
  plus `(chat_id, id)` primary key and `fetched_at`; `media` stays a JSON
  string. Explicit columns make the file directly queryable by other
  applications (the shared-format goal); SQLite gives dedup/refresh via
  upsert and safe concurrent readers for free.
- A `coverage` table of per-chat `[min_id, max_id]` ranges known to be
  complete. Only requests whose window lies inside one range are served
  locally; a fetch returning fewer messages than requested marks the start
  of the visible history (`min_id = 1`).
- `offset_id = 0` (the default "newest messages" call) always goes to the
  network.
- Staleness is accepted and documented: cached rows reflect fetch time;
  edits and deletions appear only when the range is re-fetched.
- The schema is versioned via `PRAGMA user_version`. A broken or
  foreign-version `cache.db` disables caching for the run (stderr warning);
  it is never deleted or migrated destructively, because other applications
  may rely on the data.

## Consequences

- Repeated history pagination costs zero API requests and skips the whole
  lock/pacing path; other applications get a documented, queryable local
  store of everything ever read.
- `read` output may be stale for edited or deleted messages unless
  `--no-cache` is passed; this is part of the documented contract.
- The cache grows without bound (text and metadata only, no media files);
  pruning is deferred until it becomes a real problem.
- A future output-schema change now also requires a cache schema version
  bump, and old databases stop being served until re-fetched (or migrated,
  if the accumulated data matters by then).
