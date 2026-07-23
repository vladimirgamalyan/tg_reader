# 0006. Remove the local message cache

Status: Accepted

Supersedes [0002](0002-local-message-cache-in-sqlite.md) (Local message cache
in SQLite) and [0005](0005-tombstone-deleted-messages.md) (Tombstone deleted
messages instead of dropping them).

## Context

ADR 0002 introduced the SQLite message cache for two purposes:

1. **Saving Telegram requests** — repeated `--offset-id` pagination over an
   already-covered window is served locally, skipping the lock/pacing path.
2. **A shared local archive** — other local applications can query `cache.db`
   directly for everything ever read.

Reassessing both against their cost:

- The request-saving is narrow. A cache hit needs a paginated (`--offset-id`)
  request whose whole window is already covered; the default "newest messages"
  read always goes to the network, and first-time pagination is always a miss.
  It only helps *re-reads* of the same historical window. Flood safety does not
  depend on it either — `throttle.py` (lock, 2 s pacing, FloodWait gate) is the
  safety layer; the cache is a pure optimization on top, and an un-cached
  re-read costs one paced GetHistory, which is not dangerous.
- The shared-archive purpose is not being used: no other application reads
  `cache.db`, and none is planned.

Against that, the cache is the most intricate and largest part of the codebase:
`cache.py` plus `test_cache.py` are ~900 lines, and they carry the subtlest
logic in the project — coverage-range math (`_covered_interval`,
`_merge_coverage`), the deletion-tombstone dance from 0005, SQLite schema
versioning with four in-place migrations, and a documented staleness contract
(the `deleted` output field, `--no-cache`). None of that complexity earns its
keep once the archive purpose is dropped and the request-saving is judged too
narrow to justify it.

Alternatives considered:

- **Keep as-is** — rejected: pays the full complexity for a narrow
  optimization and an unused archive.
- **Slim to a write-only archive** — keep `INSERT OR REPLACE` of every fetched
  message for external readers, drop lookup/coverage/tombstone/`deleted`/
  `--no-cache`. Rejected because the archive purpose it preserves is the one
  that turned out unused; keeping any SQLite store just to feed a consumer that
  does not exist is speculative.
- **Remove the cache entirely (chosen)** — `read` becomes a linear
  resolve → GetHistory → format with no local storage.

## Decision

We will remove the local message cache entirely. `read` always fetches from
Telegram through the existing session/flood-protection path and returns the
result; nothing is stored on disk.

Concretely:

- Delete `cache.py` and `test_cache.py`.
- `run_read` drops its `use_cache` parameter and the lookup/store calls; it
  just fetches and returns.
- The `--no-cache` flag is removed from the `read` command.
- The `deleted` field is removed from the `read` output schema — it only ever
  existed to expose cache tombstones (0005); a fresh network fetch has no
  notion of a deleted message.
- `cache.db` is no longer created. An existing `cache.db` from an older
  version is simply left on disk, unused and harmless; the tool never touches
  it.

This is a user-visible output-contract change (a dropped field and a dropped
flag), so it ships with a minor version bump.

## Consequences

- The codebase loses its largest and most subtle component: no SQLite schema
  versioning or migrations, no coverage-range reasoning, no deletion
  tombstoning, no staleness contract. `read` is now a straight-through fetch.
- Repeated history pagination over the same window costs one paced GetHistory
  request again instead of zero. This is accepted: flood protection already
  bounds the risk, and the hit rate was narrow.
- The "other applications read `cache.db`" capability is gone. If a durable
  local archive is ever genuinely needed, it should be reintroduced as its own
  deliberate feature (likely write-only), not as a side effect of `read`.
- Consumers that read the `deleted` field must stop; it is no longer emitted.
  Everything else in the `read`/`download` output is unchanged.
