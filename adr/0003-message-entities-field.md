# 0003. Expose message formatting through a single extensible `entities` field

Status: Accepted

## Context

Telegram delivers rich-text formatting (links, bold, mentions, code, ...) as
a list of message *entities* alongside the plain `message` text. The `read`
output carried only the plain text, so hyperlinks hidden behind display text
(a `MessageEntityTextUrl`, whose target URL lives in the entity, not the text)
were lost: a consumer saw the word "Original" but never its href. Recovering
those targets is the immediate need.

The consumer is an AI agent reading JSON, and the maintainer expects to expose
more entity types (bold, italic, `@mention`, `pre`) later. The output schema
is also a versioned, cached contract (see ADR 0002): every stored field forces
a `cache.db` schema bump, and changing the shape later is costly.

Alternatives considered:

- **A `links` field** (`[{text, url}]`, links only) — smallest change that
  fixes the reported bug. Rejected as the primary shape: it cannot hold
  non-link formatting, so the planned bold/mention/pre support would need a
  *second* field plus a *second* cache migration, with URL data split across
  two overlapping fields.
- **Raw `entities` with UTF-16 offsets** — most general, but pushes UTF-16
  offset-to-substring arithmetic (the exact source of emoji-boundary bugs)
  onto every consumer.
- **Markdown-rendered text** (`text` or `text_markdown` as
  `[Original](url)`) — no structured field, but forces agents to parse
  markdown to recover URLs and entangles formatting with content.

## Decision

We will expose formatting through a single `entities` array on the `read`
output, where each element is a resolved object `{ "type", "text", ... }`:
the display substring is sliced out by tg-reader (correctly, in UTF-16), and
type-specific payload is added per entity (`url` for link entities; room for
`user_id`, `language`, etc. later).

For now only URL entities are populated — `text_url` (hidden target in `url`)
and `url` (a plain URL, `url` equals `text`). Other entity types are skipped.
The field is documented as an extensible container: new `type` values (and new
per-type keys) may be added later, and consumers must ignore unrecognized
`type` values. The column is stored as JSON in `cache.db` (schema bumped to
v3), mirroring how `media` is stored.

## Consequences

- The reported problem is fixed: hidden link targets are recoverable, and each
  link keeps its display text so multiple labelled links stay distinguishable.
- UTF-16 offset handling lives in one place; consumers never see raw offsets.
- Adding bold/italic/mention/pre later is purely additive — new `type` values
  and optional keys in the same JSON column. It needs a tool version bump but
  **no new cache schema migration and no new output field**, because the
  container already exists. That one-time v2→v3 migration is paid now.
- We give up the smaller diff of a links-only field, and we accept storing
  visible URLs redundantly (they also appear in `text`) in exchange for
  `entities` meaning "all hyperlinks with their targets" without a footgun.
- Offsets are deliberately omitted for now: only resolved `text` is exposed,
  which suffices for extraction but not for faithful re-rendering of where a
  span sits. If re-rendering is needed later, an offset key can be added
  additively (and the offset-unit question decided then, not blindly now).
