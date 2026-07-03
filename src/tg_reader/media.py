"""Media metadata extraction and safe filename construction."""

import mimetypes
import os
import re

from telethon.tl.types import (
    Document,
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPhoto,
    Photo,
    PhotoSize,
    PhotoSizeProgressive,
)

# Fallback extensions for generated filenames when the MIME type is
# missing or does not resolve to anything.
TYPE_EXTENSIONS = {
    "photo": ".jpg",
    "video": ".mp4",
    "video_note": ".mp4",
    "gif": ".mp4",
    "audio": ".mp3",
    "voice": ".ogg",
    "sticker": ".webp",
    "document": ".bin",
}

# Cap on the sanitized name (extension included), before the msg_id prefix.
# Counted in UTF-8 bytes, not characters: filesystem limits (e.g. 255 bytes
# per name on ext4) apply to bytes, and the name is sender-controlled.
MAX_NAME_BYTES = 100

_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def media_info(media) -> dict | None:
    """Describe a downloadable message attachment.

    Returns a dict with 'type', 'filename', 'mime_type' and 'size_bytes'
    keys, or None when the message has no media or the media is not a
    downloadable file (polls, geo, contacts, link previews, expired media).
    """
    if isinstance(media, MessageMediaPhoto) and isinstance(media.photo, Photo):
        return {
            "type": "photo",
            "filename": None,
            "mime_type": "image/jpeg",
            "size_bytes": _photo_size_bytes(media.photo),
        }
    if isinstance(media, MessageMediaDocument) and isinstance(media.document, Document):
        document = media.document
        return {
            "type": _document_type(document),
            "filename": _document_filename(document),
            "mime_type": document.mime_type or None,
            "size_bytes": document.size,
        }
    return None


def _photo_size_bytes(photo: Photo) -> int | None:
    """Byte size of the largest photo variant (the one that gets downloaded)."""
    best = None
    for size in photo.sizes:
        if isinstance(size, PhotoSizeProgressive):
            candidate = max(size.sizes, default=None)
        elif isinstance(size, PhotoSize):
            candidate = size.size
        else:
            # Thumbnail-only variants (stripped/cached) are never downloaded.
            continue
        if candidate is not None and (best is None or candidate > best):
            best = candidate
    return best


def _document_type(document: Document) -> str:
    """Classify a document by its attributes into a friendly type name."""
    attributes = {type(attribute): attribute for attribute in document.attributes}
    # A sticker or a GIF also carries a video/image attribute, so the more
    # specific attributes are checked first.
    if DocumentAttributeSticker in attributes:
        return "sticker"
    if DocumentAttributeAnimated in attributes:
        return "gif"
    video = attributes.get(DocumentAttributeVideo)
    if video is not None:
        return "video_note" if video.round_message else "video"
    audio = attributes.get(DocumentAttributeAudio)
    if audio is not None:
        return "voice" if audio.voice else "audio"
    return "document"


def _document_filename(document: Document) -> str | None:
    for attribute in document.attributes:
        if isinstance(attribute, DocumentAttributeFilename):
            return attribute.file_name
    return None


def build_filename(msg_id: int, info: dict) -> str:
    """Deterministic safe filename: '<msg_id>_<sanitized name>'.

    Original names are sender-controlled and never used verbatim. The
    msg_id prefix rules out collisions between messages and neutralizes
    Windows reserved device names (the base name never matches CON, NUL,
    ...). Media without a name gets a generated one, e.g. 'photo.jpg'.
    """
    name = _sanitize(info["filename"] or "")
    if not name:
        name = info["type"] + _extension(info)
    return f"{msg_id}_{name}"


def _sanitize(name: str) -> str:
    name = _FORBIDDEN_CHARS.sub("_", name)
    # Windows silently drops trailing dots and spaces; strip them ourselves
    # so the name on disk matches the name we report.
    name = name.strip().rstrip(". ")
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        stem, extension = os.path.splitext(name)
        # An overlong "extension" is not a real one; keep it bounded too.
        extension = _truncate_utf8(extension, 20)
        stem = _truncate_utf8(stem, MAX_NAME_BYTES - len(extension.encode("utf-8")))
        name = stem + extension
    return name


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Truncate to at most max_bytes of UTF-8 without splitting a character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _extension(info: dict) -> str:
    extension = TYPE_EXTENSIONS[info["type"]]
    if extension == ".bin" and info["mime_type"]:
        guessed = mimetypes.guess_extension(info["mime_type"])
        if guessed:
            return guessed
    return extension
