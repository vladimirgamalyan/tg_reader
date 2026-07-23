# 0013. Pin photo downloads to the reported still image

Status: Accepted

## Context

`read` describes a photo (`MessageMediaPhoto`) as type `photo` with MIME
`image/jpeg` and a `size_bytes` measured over the photo's still sizes.
But Telethon's `download_media` picks its variant from `photo.sizes`
*plus* `photo.video_sizes`, and a `VideoSize` outranks every still size
in that default choice. For a photo carrying a video variant (an
"animated photo", rare in messages) the two commands would disagree:
`download` would fetch an MP4 video whose size was never checked against
`--max-size` (only the in-transfer guard would cap it) and save it under
a generated `photo.jpg` name.

Alternatives considered:

- **Report the video variant in `read` instead** — rejected: it would
  change the documented meaning of type `photo` (`image/jpeg`, still
  size) for a rare media form, and the still image is what "a photo"
  means to a consumer of the output.
- **Pin the download to the still size (chosen)** — `download_media`
  accepts a `thumb` selector; passing the type string of the largest
  still size (the exact size `media_info` measured) makes `download`
  fetch what `read` reported. The pin is only applied to photos that
  carry `video_sizes`; every other media keeps the default behavior (a
  thumb argument on a document would download its thumbnail).

## Decision

We will pin the download of a photo carrying `video_sizes` to its
largest still size — the same size `read` reports.

## Consequences

- `read` and `download` agree on type, MIME and size for every photo,
  and the `--max-size` pre-check validates the bytes actually
  transferred.
- The video variant of an animated photo is not downloadable through the
  tool. Acceptable: `read` does not advertise it, and no documented
  field refers to it.
- The pin relies on Telethon's `thumb=<type str>` selector semantics
  (matched against the photo's own size list), which the pyproject pin
  to Telethon 1.x already covers.
