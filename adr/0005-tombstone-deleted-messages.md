# 0005. Tombstone deleted messages instead of dropping them

Status: Accepted

Amends the deletion-handling aspect of
[0002](0002-local-message-cache-in-sqlite.md) (Local message cache in SQLite);
the rest of 0002 stands.

## Context

ADR 0002 stated that "edits and deletions appear only when the range is
re-fetched". That is true for edits (a re-fetch upserts the new row) but was
false for deletions: `store` only upserted the messages a fetch returned and
never removed a row, so a message deleted in Telegram stayed in the cache
forever and `lookup` kept serving it as if it still existed — even after
`--no-cache`. The documented "re-fetch to refresh" escape hatch did not
clear deletions.

The cache already tracks, per fetch, a range `[lower, upper]` it proves
complete (`_covered_interval`): GetHistory returns consecutive messages, so
within that range the returned set is authoritative. Any message the cache
holds inside that range but the fetch did not return is gone server-side.

Alternatives considered:

- **Documentation only (append-only archive)** — declare that deletions are
  retained and stop claiming re-fetch clears them. Minimal and honest, but
  silent: a consumer cannot tell a live message from a deleted one, and
  history pagination keeps returning phantoms with no signal.
- **Hard delete on re-fetch** — remove the missing rows inside the proven
  range. Makes the cache mirror the server, but is destructive: a wrong
  completeness boundary (or a message hidden from this account, e.g. "not
  displayable due to local laws") permanently loses data, and the tool's
  second purpose is a durable local archive other applications read.
- **Tombstone (chosen)** — keep the row, add a `deleted` flag. No data loss,
  the deletion is observable, and a wrong flag is self-correcting.

## Decision

We will keep deleted messages in the cache and mark them instead of dropping
them. A new `messages.deleted` column (schema v4, `0/1`, default `0`) carries
the flag, and it is exposed as a `deleted` boolean in the `read` output
(always `false` for messages fetched fresh from the network).

On every `store`, within the range the fetch proves complete the whole window
is tombstoned (`deleted = 1`) and the upsert then clears the flag on every
message that still exists, so only genuinely absent IDs stay marked. A
message that reappears in a later complete fetch is un-flagged, so a
transiently hidden message heals itself. `lookup` returns tombstoned rows
with `deleted = true` rather than hiding them.

## Consequences

- Cache-served `read` output can now include messages the server no longer
  returns, each carrying `deleted = true`; consumers filter on the flag.
  This is a deliberate divergence from a plain server mirror: the cache is a
  richer archive.
- A false positive is only possible when a message exists server-side but is
  withheld from this account within a proven-complete window (regional/legal
  exclusion, lost history access) — indistinguishable from deletion for any
  local cache, and cleared automatically if the message is ever returned
  again. No data is lost either way, unlike a hard delete.
- Deleting the single newest cached message on an `offset_id = 0` fetch stays
  unflagged: that fetch only proves completeness up to its newest returned
  ID, matching the existing coverage boundary. Accepted as a documented
  limitation rather than adding a separate reconciliation interval.
- Adds schema v4 and one output field; old databases migrate in place
  (`ALTER TABLE ADD COLUMN`), the same non-destructive path as v2 and v3.
