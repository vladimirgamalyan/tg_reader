# 0019. Batch download over one connection, with run limits and a download-run budget

Status: Accepted

Related: [0008](0008-classify-local-file-errors-in-download.md) and
[0010](0010-enforce-max-size-during-transfer.md) (the single-download
transfer code the batch reuses), [0014](0014-incremental-chat-archive-via-sync.md)
(`sync`, the other long run that holds the lock).

## Context

`download` fetches one message per run. Batching was considered earlier
and left out for simplicity (that choice was never recorded as an ADR).
New information: a caller archiving a conversation (about 2500
attachments, 3.3 GB, 1059 of them voice messages) ran `download` once per
message, and on 2026-11-19, after about 100 runs in a row, Telegram
assigned a FloodWait of 3026 s. The state file kept only the deadline, so
which request got the wait is unknown.

What one `download` run sends, read from the Telethon 1.44 sources:

- connect: a new TCP connection, InvokeWithLayer(InitConnection(
  help.GetConfig)), then updates.GetState for the authorization check;
- one messages.GetMessages (the chat resolves from the session cache);
- if the file is stored on a data center other than the account's home DC
  (`client.session.dc_id != dc_id`): `_borrow_exported_sender` creates a
  sender with no auth key, so a fresh key is negotiated with that DC, then
  sends help.GetConfig (cached per process), auth.ExportAuthorization to
  the home DC and auth.ImportAuthorization to the file DC. The exported
  sender lives in `client._borrowed_senders` and is never saved to the
  session file;
- the upload.GetFile chunks.

So ~100 runs meant ~100 new connections, ~100 new auth keys on file DCs
and up to ~100 ExportAuthorization/ImportAuthorization pairs within
minutes. The export is the most plausible culprit: each one creates a
new authorization of the account on another DC, the kind of operation
Telegram guards, while GetMessages and GetFile at that rate are routine
client traffic. Connection churn (a new connection and InitConnection
per run) is the second candidate. This is an inference, not a proof:
Telegram does not publish these limits, and the request that got the
wait was not recorded. A long-lived client, by contrast, keeps one
exported sender per DC for its lifetime (after 60 s idle it disconnects it
but keeps its key, and reconnects without exporting again).

Alternatives considered:

- **Keep the single download and tell callers to slow down** — rejected:
  the per-file connection and export stay; slowing down only stretches
  the same pattern.
- **Persist exported auth keys across runs** — rejected: Telethon 1.x
  sessions store one key; it would mean reaching into client internals.
- **Let `download` take many MSG_IDs** — rejected: its output (one object)
  and exit codes would change meaning; a separate command leaves the
  existing contract intact.
- **A takeout session** (account.initTakeoutSession, the path of Telegram
  Desktop's chat export, with relaxed limits) — rejected for now: it must
  be confirmed in another app, Telegram may delay the first export by up
  to a day, and it leaves a visible data-export session on the account.
- **A new `download-batch` command plus a budget on download runs
  (chosen).**

## Decision

1. **`tg-reader download-batch CHAT_ID [MSG_ID ...] [--ids-file FILE|-]
   --output DIR`**: one connection for the whole run. IDs are processed in
   the given order (the caller puts what matters first). Messages are
   fetched up to 100 per GetMessages request, right before their files are
   transferred, and fetched again when older than 30 minutes (file
   references expire). File names and the transfer code are those of
   `download`. A message whose file is already in DIR (any name with the
   `<chat_id>_<msg_id>_` prefix, `.part` excluded) is skipped without a
   request; if nothing else is left, the run does not connect. Each ID gets
   an outcome (`downloaded`, `skipped` or `failed` with a reason, or
   `pending` when the run stopped first); the JSON report is printed
   whenever the run got to downloading. Exit codes: 0 all done, 1 a
   permanent error or failed messages, 2 stopped early (rerun continues).
2. **Pace and caps inside a run**: a pause of 3 s plus a random 0–50 %
   between transfers (`--pause`, 1..60); at most 200 transfers
   (`--max-files`, 1..1000) and 1024 MB of declared size (`--max-total`,
   1..4096, not below `--max-size`) per run. A run stopped by a cap exits
   with code 2 and an advisory `retry after 900s`.
3. **FloodWait**: waits up to 30 s are still slept through in place by
   Telethon; the batch echoes Telethon's log line about it to stderr. A
   longer wait stops the batch: the deadline is persisted as for every
   command, the report lists what is still pending, exit code 2. Every
   persisted flood wait now also writes `last_flood` to `throttle.json`
   (time, seconds, the name of the request that got it; for a batch also
   the files and bytes it had downloaded), and the error message names the
   request.
4. **Download-run budget**: `download` and `download-batch` runs draw on a
   token bucket in `throttle.json` — up to 20 runs back to back, refilled
   at 20 per hour (one per 3 minutes) — checked under the lock before
   connecting. When it is empty, the run fails with exit code 2, a
   `retry after` until the next run is available, and a pointer to
   `download-batch`. A batch that needs no network costs nothing.

Why these defaults:

- **3 s pause**: 1.5 times the 2 s already kept between runs. With the
  transfer itself that is at most ~1000 files per hour, slower than the
  official apps fetch media while a chat is scrolled with auto-download
  on. The jitter keeps transfers from ticking at a machine-regular
  interval.
- **200 files / 1 GB per run**: a run of roughly 15–30 minutes. It bounds
  how long one run holds the global lock (agents' `read` calls fail with
  exit code 2 meanwhile) and how much a mistaken invocation can fetch.
  With the 900 s rest, the 2500-file conversation takes about 13 runs.
- **20 download runs per hour**: a fifth of the ~100 runs that drew the
  penalty, and still plenty for an agent fetching a few files or batch
  reruns. A loop of single downloads hits it within minutes and is told
  to switch to `download-batch`.
- **Caps per run, budget per hour**: no limit on the volume of file
  downloads is known for user accounts; the known-limited operation is the
  per-connection authorization export. So the enforced cross-run limit
  counts runs (connections), while the per-run caps and the rest after
  them stay advisory pacing. Tracking a cross-run volume quota would add
  state without a known limit to aim at.

## Consequences

- A batch run costs one connection, one GetState, one GetMessages per 100
  messages, at most one authorization export per foreign DC, and the
  GetFile chunks. Downloading the 2500 attachments no longer repeats the
  connection and export 2500 times.
- `download` in a loop is refused after 20 runs within an hour: a
  deliberate behavior change for callers that loop it.
- A batch holds the global lock for its whole run, up to tens of minutes
  with the default caps; other commands wait 30 s, then exit with code 2.
- Unlike the other commands, `download-batch` prints its report on stdout
  even when it exits with a non-zero code; stdout is empty only when it
  could not start.
- The skip check trusts the prefix: a file a caller derives from a
  download and names after it (e.g. `<file>.transcript.json`) counts too,
  and a damaged file under a valid name is never re-fetched. A message
  whose real file name ends in `.part` is re-fetched on every run.
- The stderr note on short flood waits relies on the text of Telethon's
  log message (pinned to 1.x); if it changes, the note disappears, the
  behavior does not.
- After the next long FloodWait, `last_flood` names the request, so the
  explanation above can be confirmed or corrected.
