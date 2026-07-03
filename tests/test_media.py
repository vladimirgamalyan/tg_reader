"""Unit tests for media metadata extraction and safe filename construction."""

from datetime import datetime, timezone

from telethon.tl.types import (
    Document,
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    InputStickerSetEmpty,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaUnsupported,
    Photo,
    PhotoSize,
    PhotoSizeProgressive,
    PhotoStrippedSize,
)

from tg_reader.media import MAX_NAME_LENGTH, build_filename, media_info


def make_document(mime_type="application/pdf", size=1000, attributes=None):
    return Document(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=datetime(2026, 7, 3, tzinfo=timezone.utc),
        mime_type=mime_type,
        size=size,
        dc_id=2,
        attributes=attributes or [],
    )


def document_media(**kwargs):
    return MessageMediaDocument(document=make_document(**kwargs))


def make_photo(sizes):
    return Photo(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=datetime(2026, 7, 3, tzinfo=timezone.utc),
        sizes=sizes,
        dc_id=2,
    )


# --- media_info: non-media and non-file media ---


def test_media_info_none_for_no_media():
    assert media_info(None) is None


def test_media_info_none_for_non_file_media():
    assert media_info(MessageMediaUnsupported()) is None


def test_media_info_none_for_empty_photo():
    # Expired self-destructing media: MessageMediaPhoto without a Photo.
    assert media_info(MessageMediaPhoto()) is None


# --- media_info: photos ---


def test_media_info_photo_takes_largest_size():
    photo = make_photo(
        [
            PhotoStrippedSize(type="i", bytes=b"tiny"),
            PhotoSize(type="m", w=320, h=240, size=15000),
            PhotoSizeProgressive(type="y", w=1280, h=960, sizes=[8000, 40000, 90000]),
        ]
    )

    assert media_info(MessageMediaPhoto(photo=photo)) == {
        "type": "photo",
        "filename": None,
        "mime_type": "image/jpeg",
        "size_bytes": 90000,
    }


def test_media_info_photo_without_usable_sizes():
    photo = make_photo([PhotoStrippedSize(type="i", bytes=b"tiny")])

    assert media_info(MessageMediaPhoto(photo=photo))["size_bytes"] is None


# --- media_info: document classification ---


def test_media_info_plain_document():
    media = document_media(
        attributes=[DocumentAttributeFilename(file_name="report.pdf")]
    )

    assert media_info(media) == {
        "type": "document",
        "filename": "report.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 1000,
    }


def test_media_info_video():
    media = document_media(
        mime_type="video/mp4",
        attributes=[DocumentAttributeVideo(duration=5.0, w=640, h=480)],
    )

    assert media_info(media)["type"] == "video"


def test_media_info_video_note():
    media = document_media(
        mime_type="video/mp4",
        attributes=[
            DocumentAttributeVideo(duration=5.0, w=240, h=240, round_message=True)
        ],
    )

    assert media_info(media)["type"] == "video_note"


def test_media_info_audio():
    media = document_media(
        mime_type="audio/mpeg", attributes=[DocumentAttributeAudio(duration=60)]
    )

    assert media_info(media)["type"] == "audio"


def test_media_info_voice():
    media = document_media(
        mime_type="audio/ogg",
        attributes=[DocumentAttributeAudio(duration=5, voice=True)],
    )

    assert media_info(media)["type"] == "voice"


def test_media_info_gif_wins_over_video():
    media = document_media(
        mime_type="video/mp4",
        attributes=[
            DocumentAttributeVideo(duration=2.0, w=320, h=240),
            DocumentAttributeAnimated(),
            DocumentAttributeFilename(file_name="funny.mp4"),
        ],
    )

    info = media_info(media)

    assert info["type"] == "gif"
    assert info["filename"] == "funny.mp4"


def test_media_info_sticker_wins_over_video():
    media = document_media(
        mime_type="video/webm",
        attributes=[
            DocumentAttributeVideo(duration=2.0, w=512, h=512),
            DocumentAttributeSticker(alt=":)", stickerset=InputStickerSetEmpty()),
        ],
    )

    assert media_info(media)["type"] == "sticker"


# --- build_filename: sanitization ---


def info(media_type="document", filename=None, mime_type=None):
    return {
        "type": media_type,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": None,
    }


def test_build_filename_plain_name():
    assert build_filename(555, info(filename="report.pdf")) == "555_report.pdf"


def test_build_filename_path_traversal_neutralized():
    result = build_filename(555, info(filename="..\\..\\evil.exe"))

    assert "\\" not in result
    assert "/" not in result
    assert result == "555_.._.._evil.exe"


def test_build_filename_forbidden_characters_replaced():
    result = build_filename(555, info(filename='a<b>c:d"e|f?g*h.txt'))

    assert result == "555_a_b_c_d_e_f_g_h.txt"


def test_build_filename_reserved_device_name_neutralized_by_prefix():
    # 'CON.txt' alone is a reserved name on Windows; the msg_id prefix
    # makes the base name harmless.
    assert build_filename(555, info(filename="CON.txt")) == "555_CON.txt"


def test_build_filename_trailing_dots_and_spaces_stripped():
    assert build_filename(555, info(filename="name... ")) == "555_name"


def test_build_filename_dots_only_name_falls_back_to_generated():
    result = build_filename(555, info(media_type="photo", filename="..."))

    assert result == "555_photo.jpg"


def test_build_filename_unnamed_photo():
    assert build_filename(555, info(media_type="photo")) == "555_photo.jpg"


def test_build_filename_unnamed_voice():
    result = build_filename(555, info(media_type="voice", mime_type="audio/ogg"))

    assert result == "555_voice.ogg"


def test_build_filename_unnamed_document_without_mime():
    assert build_filename(555, info()) == "555_document.bin"


def test_build_filename_overlong_name_truncated_keeps_extension():
    result = build_filename(555, info(filename="x" * 300 + ".txt"))

    assert result.startswith("555_x")
    assert result.endswith(".txt")
    assert len(result) == len("555_") + MAX_NAME_LENGTH
