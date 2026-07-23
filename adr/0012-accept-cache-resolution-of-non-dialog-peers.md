# 0012. Accept session-cache resolution of non-dialog peers

Status: Accepted

## Context

`resolve_chat` resolves a numeric chat ID in two steps: the session entity
cache first, then a walk of the account's dialog list. The documentation
("the account must be a member of the chat; unknown IDs are looked up in
the dialog list") implies the two steps agree on which IDs resolve.

They do not. Telethon populates the session cache with every entity seen
in any server response — message senders in a group being read, forward
authors — not just dialogs. Consequences:

- A positive user ID with no open dialog (e.g. a member of a group read
  earlier) resolves from the cache; `read` then returns an empty array
  instead of the documented "chat not found" error.
- The same command can fail after the session file is recreated
  (re-auth), because the cache entry is gone and the dialog walk does not
  find the peer: run-to-run behavior depends on cache state.
- In the (rare) case of a numeric user/channel ID collision, a user
  cached after the first run can shadow a channel that the dialog walk
  originally resolved, silently switching the target.

Alternatives considered:

- **Verify dialog membership on every cache hit** — rejected: it costs
  the dialog-list request the cache exists to avoid, on every run, to
  guard against a mostly harmless outcome (an empty array).
- **Skip the cache for ambiguous positive IDs** — rejected: same cost for
  the common case (reading a user chat by raw ID), and negative marked
  IDs would still hit the cache, keeping the inconsistency.
- **Document the behavior (chosen)** — the cache path is the fast path by
  design; the membership requirement is a statement about the dialog-walk
  fallback, not an access-control check.

## Decision

We will keep cache-first resolution as is and document that entities the
account has already encountered resolve without an open dialog; the
"member of the chat" requirement applies to the dialog-walk fallback.

## Consequences

- Repeat runs stay cheap: one cache lookup, no dialog-list request.
- The contract is honest: `read` on a cached non-dialog peer may return
  an empty array rather than an error, and the same command may start
  failing with "chat not found" after a re-auth loses the cache.
- The ID-collision shadowing case remains theoretically possible and
  undetected; fixing it would require dialog verification on cache hits,
  which this decision explicitly trades away.
