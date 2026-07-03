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

# Cap on the sanitized name (extension included), before the
# '<chat_id>_<msg_id>_' prefix.
# Counted in UTF-8 bytes, not characters: filesystem limits (e.g. 255 bytes
# per name on ext4) apply to bytes, and the name is sender-controlled.
MAX_NAME_BYTES = 100

# Besides path separators and Windows-forbidden punctuation, reject
# invisible Unicode: C0/C1 controls and the zero-width/bidi-control
# characters an attacker can use to visually spoof the extension
# (e.g. 'photo_<U+202E>gpj.exe' renders as 'photo_exe.jpg').
_FORBIDDEN_CHARS = re.compile(
    r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f'  # separators, Windows-forbidden, controls
    r"\u061c"  # Arabic letter mark (bidi)
    r"\u200b-\u200f"  # zero-width space/joiners, LRM, RLM
    r"\u2028\u2029"  # line and paragraph separators
    r"\u202a-\u202e"  # bidi embedding and override controls
    r"\u2066-\u2069"  # bidi isolate controls
    r"\ufeff]"  # zero-width no-break space (BOM)
)


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


def build_filename(chat_id: int, msg_id: int, info: dict) -> str:
    """Deterministic safe filename: '<chat_id>_<msg_id>_<sanitized name>'.

    Original names are sender-controlled and never used verbatim. Message
    IDs are only unique within one chat, so the prefix includes the chat ID:
    downloads from different chats into one directory cannot collide. The
    prefix also neutralizes Windows reserved device names (the base name
    never matches CON, NUL, ...). Media without a name gets a generated
    one, e.g. 'photo.jpg'.
    """
    name = _sanitize(info["filename"] or "")
    if not name:
        name = info["type"] + _extension(info)
    return f"{chat_id}_{msg_id}_{name}"


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
        # The byte cut can land right after an inner space; Windows silently
        # drops trailing spaces and dots, so the reported name would not
        # match the name on disk.
        name = (stem + extension).rstrip(". ")
    return name


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Truncate to at most max_bytes of UTF-8 without splitting a character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _extension(info: dict) -> str:
    """Extension for a generated filename, consistent with the MIME type.

    The per-type default is kept when the MIME type is missing, unknown or
    agrees with it (image/jpeg must stay '.jpg' even though the platform
    MIME table may list '.jpe' first); otherwise the MIME type wins, so an
    unnamed 'audio/flac' track does not get a misleading '.mp3'.
    """
    extension = TYPE_EXTENSIONS[info["type"]]
    if info["mime_type"]:
        valid = mimetypes.guess_all_extensions(info["mime_type"])
        if valid and extension not in valid:
            return valid[0]
    return extension
