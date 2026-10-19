# 0016. Expose a forwarded message's origin as one `forwarded_from` object

Status: Accepted

## Context

`read` reported a forwarded message exactly like an original one: the sender
was the person who forwarded it, and nothing said where the content came
from or when it was first posted. In group chats that loses context an agent
needs ("X forwarded a channel post from 2023" reads very differently from
"X wrote").

Telegram's forward header (`MessageFwdHeader`) carries the origin peer
(`from_id`), a bare name when the author hides their account
(`from_name`), the original date, and more niche fields (channel post ID,
post signature, "saved from" peer, PSA type).

Alternatives considered:

- **Flat fields** (`forward_from_id`, `forward_from_name`, `forward_date`) —
  rejected: three nullable top-level fields that are all null together for
  most messages; one nullable object states "not forwarded" once.
- **Mirror the whole header** — rejected: the niche fields have no current
  consumer, and every output field is a contract (and, since ADR-0014, is
  also stored in archive files).

## Decision

We will add one nullable field, `forwarded_from`, to every `read` element:
`{"id": int|null, "name": str|null, "date": str|null}`.

- `id` is the marked peer ID of the original author or channel, `null` when
  the author hides their account from forwards.
- `name` comes from the entity Telethon attached to the header (user display
  name or chat title), falling back to `from_name` for hidden accounts.
- `date` is the original message's time in ISO 8601 UTC.

## Consequences

- Agents can tell forwarded content and its age apart from what the sender
  wrote, at the cost of one more field in a stable schema.
- The object can grow fields later (e.g. the original post ID) without a
  new top-level field, like `entities` (ADR-0003).
- Archive lines written by `sync` before this change lack the field; per
  ADR-0014, consumers treat a missing field as absent.
