# 0020. Pace message fetches and cap message IDs in download-batch

Status: Accepted

Related: [0019](0019-batch-download-and-download-run-budget.md) (the batch
command and its per-run caps; this record adds to its decision 2).

## Context

ADR-0019 paces and caps file transfers, and states that the per-run caps
bound "how much a mistaken invocation can fetch". A security review found
that they do not bound the GetMessages requests: an ID that turns out to
be missing or without media costs its share of a GetMessages request but
takes no `--max-files` slot and no pause. The number of IDs was not
limited either. A mocked run with 100,000 IDs sent 1,000 GetMessages
requests back to back with no sleep in between. The case is realistic:
an agent asked for "every attachment of a chat" may pass a whole range
of IDs instead of the IDs of messages with media.

Alternatives considered:

- **A cap on GetMessages requests per run that stops the run with exit
  code 2** — rejected: a rerun walks the same list from the start, and
  missing or media-less IDs are never recorded as done (only files on
  disk are), so a long stretch of such IDs at the head of the list would
  stop every rerun at the same place and never make progress.
- **Pacing alone, no cap** — rejected: with the 3 s pause, 100,000 IDs
  would still mean about an hour of requests while holding the global
  lock.
- **Pace the fetches and refuse lists over a fixed size up front
  (chosen).**

## Decision

1. Two GetMessages requests of one run are separated by the same pause
   as two transfers (`--pause` plus its random 0–50 %); the run's first
   request is not delayed.
2. `download-batch` refuses more than 5000 message IDs (counted after
   dropping duplicates) before it starts: a usage error, exit code 1,
   telling the caller to pass only messages with media or to split the
   list. The limit is not a flag.

Why 5000: the conversation that motivated ADR-0019 had about 2500
attachments, so one list still covers it. The bound is 50 GetMessages
requests per run, under three minutes of pauses at the default `--pause`.

## Consequences

- A mistaken range costs at most 50 paced GetMessages requests instead of
  an unbounded burst.
- A chat with more than 5000 attachments needs its list split across
  runs; the caller has to do that.
- A run that refetches stale messages, or crosses a chunk boundary, waits
  one more pause per extra GetMessages request; this is negligible next
  to the transfers.
