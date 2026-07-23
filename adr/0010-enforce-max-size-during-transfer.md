# 0010. Enforce --max-size during the transfer, not only around it

Status: Accepted

## Context

`download` checked `--max-size` twice: before the transfer against the
size Telegram declares, and after the transfer against the actual file
size. The post-check exists because the declared size is not treated as
fully trustworthy — but that made the trust model inconsistent: if the
declared size can lie, a file claiming 1 KB while actually being 2 GB
would be downloaded in full (bandwidth, time under the global lock, the
`.part` file growing to 2 GB — potentially filling the disk) and only
then rejected.

Alternatives considered:

- **Trust the declared size and drop the post-check** — rejected: the size
  is computed server-side and lying is unlikely, but the cost of being
  wrong (an unbounded transfer) is much higher than the cost of a guard.
- **Reimplement the download with `client.iter_download` and a byte
  counter** — rejected: it means reimplementing what `download_media`
  already does (part sizing, file naming, photo size selection) just to
  count bytes.
- **Abort via `progress_callback` (chosen)** — `download_media` invokes
  the callback after every chunk with the cumulative byte count; raising
  from it aborts the transfer immediately, Telethon closes the `.part`
  handle on the way out, and the existing cleanup deletes the file. At
  most one chunk beyond the limit is ever transferred.

## Decision

We will pass a `progress_callback` to `download_media` that raises
`DownloadError` as soon as the received byte count exceeds the
`--max-size` limit. The pre-transfer check (fail fast, no bytes spent)
and the post-transfer check (final authority on what was actually
written) both remain.

## Consequences

- A transfer can no longer cost meaningfully more traffic, time or disk
  than `--max-size` allows, regardless of what size Telegram declared.
- The abort error names the byte count at which the transfer stopped,
  not the file's real total size (unknowable without finishing); the
  agent-facing remedy is the same — raise `--max-size`.
- Three checks guard one limit, each with a distinct job: refuse known
  oversizes for free, cap the unknown mid-flight, verify the result.
