# Architecture Decision Records

This folder records notable architecture and design decisions for
`tg-reader`, together with the reasoning behind them. It exists so that a
decision — especially a debatable or previously-revisited one — gets made
once, on record, instead of being silently re-litigated in every session.

## When to write one

Add a new ADR for a decision that is:

- **Architecturally significant** — affects module boundaries, public CLI
  contract, data formats, or how a whole feature area works.
- **Debatable** — there was a real alternative, and someone (human or agent)
  could reasonably propose reopening it later.
- **Costly to reverse** — changing course later means touching multiple
  files, breaking the CLI contract, or redoing prior work.

Skip an ADR for routine bug fixes, refactors with no behavioral choice to
record, or anything already fully explained by `docs/PROJECT.md`.

## Before proposing a debatable change

Before proposing or re-proposing a decision that feels debatable, check
`adr/` first:

1. Search existing ADRs for the topic (filenames and titles).
2. If a relevant ADR exists and is `Accepted`, treat it as settled. Follow
   it. Only propose reopening it if you have new information the ADR did
   not consider — and say explicitly what that new information is.
3. If you do reopen it, do not edit the old ADR's decision in place. Write a
   new ADR that supersedes it (see below), so the history of *why* stays
   intact.

## File format

- Filename: `NNNN-short-kebab-title.md`, numbered sequentially
  (`0001-...`, `0002-...`).
- Use `template.md` as the starting point for a new record.
- Status is one of: `Proposed`, `Accepted`, `Rejected`, `Superseded by
  NNNN`. When a decision changes, add a new ADR and update the old one's
  status to `Superseded by NNNN` rather than rewriting it.

## Index

- [0001](0001-record-architecture-decisions.md) — Record architecture
  decisions as ADRs
- [0002](0002-local-message-cache-in-sqlite.md) — Local message cache in
  SQLite
- [0003](0003-message-entities-field.md) — Expose message formatting through a
  single extensible `entities` field
- [0004](0004-ship-agent-skill-in-repo.md) — Ship the agent skill in the repo,
  installed by the install prompt
- [0005](0005-tombstone-deleted-messages.md) — Tombstone deleted messages
  instead of dropping them
- [0006](0006-remove-message-cache.md) — Remove the local message cache
- [0007](0007-forum-topic-reply-header.md) — Report forum-topic membership
  headers as "not a reply"
- [0008](0008-classify-local-file-errors-in-download.md) — Classify local
  filesystem errors during download as permanent
