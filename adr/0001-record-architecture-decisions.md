# 0001. Record architecture decisions as ADRs

Status: Accepted

## Context

Debatable design choices (CLI contract shape, error/exit-code scheme,
flood-protection policy, file-naming rules, etc.) tend to get re-discussed
from scratch in later sessions, sometimes landing on a different answer
with no record of why the previous one was chosen or what was rejected and
why.

## Decision

We will keep a lightweight ADR log in `adr/`, one Markdown file per
decision, numbered sequentially. `AGENTS.md` instructs agents to check this
log before proposing a debatable change and to add a record after making
one.

## Consequences

Debatable decisions are made once and referenced afterward instead of
being re-argued. Adds a small amount of overhead: a decision worth
recording needs a short file written for it.
