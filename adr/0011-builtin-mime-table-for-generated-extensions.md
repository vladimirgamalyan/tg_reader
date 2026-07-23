# 0011. Use the stdlib builtin MIME table for generated extensions

Status: Accepted

## Context

Unnamed media gets a generated filename whose extension is derived from
the MIME type Telegram reports, via the `mimetypes` module. The
module-level `mimetypes` functions consult platform sources on top of the
builtin table: on Windows the OS registry, on Linux `/etc/mime.types` and
similar files. Those sources vary between machines and are writable by
any installed application, so the same media could be saved under
different extensions on different machines (registry entries also add
oddities such as `.jfif` for `image/jpeg`).

The extension choice feeds `build_filename`, whose output is documented
as deterministic ("re-running the same download is idempotent"); a
machine-dependent component contradicts that.

Alternatives considered:

- **Keep the platform-merged table** — rejected: the platform table knows
  a few more types, but at the cost of machine-dependent, third-party
  -influenced output for a tool whose filenames are part of its contract.
- **Ship a hand-curated extension map** — rejected: reimplements the
  stdlib table for marginal control; the builtin table plus the existing
  `_MIME_EXTENSIONS` override (for Telegram-specific types) already
  covers everything observed.
- **Use a private `mimetypes.MimeTypes()` instance (chosen)** — an
  instance is built from the stdlib's builtin table only and never
  touches platform sources, so results depend solely on the Python
  version.

## Decision

We will resolve MIME types to extensions through a module-level
`mimetypes.MimeTypes()` instance in `media.py`, not the module-level
`mimetypes` functions, keeping generated filenames machine-independent.

## Consequences

- Generated extensions are deterministic across machines for a given
  Python version; a polluted OS registry cannot influence output names.
- MIME types known only to a platform source now fall back to the
  per-type default extension (`.bin` for documents etc.). If a real type
  shows up in practice, it is a one-line `_MIME_EXTENSIONS` addition.
- The builtin table still evolves with Python versions, so extensions
  are pinned per Python version, not forever.
