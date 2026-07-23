# 0008. Classify local filesystem errors during download as permanent

Status: Accepted

## Context

The session layer maps any `OSError` escaping a command to "cannot reach
Telegram, retry after Ns" (exit code 2), because a connection that dies
mid-request surfaces as a raw socket-level `OSError`. But
`client.download_media` writes to the local filesystem inside that same
scope: a full disk (`ENOSPC`) or an antivirus locking the `.part` file
also raises `OSError`. Those failures were misreported as transient
network problems, so an agent following the exit-code contract would
retry a full disk forever, with a misleading error message.

The pre-flight writability check in `download.py` (touch/unlink) catches
an unusable output directory, but not failures that strike mid-transfer.

The two error families overlap in type, so download needs a heuristic to
tell them apart. Alternatives considered:

- **Match on exception subclass** (`ConnectionError` etc.) — rejected:
  Telethon delivers plain `OSError` (e.g. `socket.gaierror`) for network
  failures and plain `OSError` for write failures; subclasses do not
  separate the families.
- **Treat every `OSError` from the download call as local** — rejected: a
  connection reset mid-transfer is common and must keep exit code 2.
- **Discriminate by filename and errno (chosen)** — local file errors
  from `open()`/`stat()` carry the offending path in `.filename`; a failed
  `write()` carries no filename but fails with a filesystem-only errno
  (`ENOSPC`, `EFBIG`, `EDQUOT`) that no socket operation produces. Errnos
  shared with sockets (e.g. `EACCES`, which Windows uses for
  firewall-blocked connects) are deliberately not in the list — for them
  the filename check is the only safe signal.

## Decision

We will classify an `OSError` raised during the media transfer as a
permanent local failure (exit code 1) when it carries a filename or a
filesystem-only errno (`ENOSPC`, `EFBIG`, `EDQUOT`); every other `OSError`
keeps propagating to the session layer's transient mapping (exit code 2).
The classification lives in `download.py`, next to the only place both
error families can originate from one call.

## Consequences

- A full disk or a locked `.part` file fails fast with exit code 1 and an
  error naming the file, instead of an endless "cannot reach Telegram"
  retry loop.
- The heuristic is conservative: an unrecognized local error without a
  filename and outside the errno list still maps to "retry later". That
  direction of mistake is the safer one — a retry loop on a genuinely
  local error is bounded by the caller's patience, while misclassifying a
  transient network error as permanent would make an agent give up on a
  recoverable download.
- The errno list is small and explicit; if a new local failure mode shows
  up in practice, it is a one-line addition.
