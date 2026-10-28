"""D1 tests — image validation and preparation (the pre-OCR gate).

Every test is self-contained: images are generated with Pillow in the
test itself, and "excessive dimensions" files are *header-only* PNGs
that declare a huge size without carrying pixel data — proof that the
limit checks run **before** decoding (a decode attempt on those files
would fail with 422 instead of the expected 413).
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image as PILImage

from app.core.errors import (
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    UnprocessableEntityError,
)
from app.schemas.analysis import MAX_UPLOAD_BYTES
from app.services.image_service import (
    MAX_DECODED_PIXELS,
    MAX_DIMENSION,
    prepare_image,
)
from app.utils.file_validation import detect_image_format, validate_image_bytes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _png(width: int = 64, height: int = 48, color=(200, 100, 50)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(width: int = 64, height: int = 48, exif_orientation: int | None = None) -> bytes:
    buffer = io.BytesIO()
    kwargs: dict = {}
    if exif_orientation is not None:
        exif = PILImage.Exif()
        exif[0x0112] = exif_orientation  # Orientation tag
        kwargs["exif"] = exif.tobytes()
    PILImage.new("RGB", (width, height), (10, 20, 30)).save(
        buffer, format="JPEG", **kwargs
    )
    return buffer.getvalue()


def _gif(width: int = 32, height: int = 32) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (width, height), (255, 0, 0)).save(buffer, format="GIF")
    return buffer.getvalue()


def _png_declaring(width: int, height: int) -> bytes:
    """Header-only PNG: declares `width×height` with ZERO pixel data.

    If validation runs before decoding, limits raise 413; if it did not,
    decoding this file would fail with 422 instead.
    """
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", b"") + chunk(b"IEND", b"")


# ---------------------------------------------------------------------------
# format detection (contract formats, by magic bytes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "header"),
    [
        ("PNG", b"\x89PNG\r\n\x1a\nrest"),
        ("JPEG", b"\xff\xd8\xff\xe0rest"),
        ("GIF", b"GIF89a-rest"),
        ("WEBP", b"RIFF\x00\x00\x00\x00WEBPrest"),
        ("TIFF", b"II*\x00rest"),
        ("AVIF", b"\x00\x00\x00\x18ftypavifrest"),
    ],
)
def test_supported_containers_are_detected_by_magic_bytes(label, header):
    assert detect_image_format(header) == label


def test_unsupported_or_unknown_bytes_detect_as_none():
    assert detect_image_format(b"BM\x00\x00windows bitmap") is None
    assert detect_image_format(b"plain text, not an image") is None
    assert detect_image_format(b"") is None
    # HEIC/HEIF is an ISO container but outside the contract.
    assert detect_image_format(b"\x00\x00\x00\x18ftypheicwhatever") is None


# ---------------------------------------------------------------------------
# byte-level validation
# ---------------------------------------------------------------------------


def test_valid_png_bytes_validate_to_their_format():
    assert validate_image_bytes(_png(), filename="scan.png") == "PNG"
    assert validate_image_bytes(_jpeg(), filename="photo.jpg") == "JPEG"


def test_empty_file_is_rejected():
    with pytest.raises(UnprocessableEntityError, match="empty"):
        validate_image_bytes(b"", filename="empty.png")


def test_invalid_bytes_are_rejected_as_unsupported_format():
    with pytest.raises(UnsupportedMediaTypeError) as excinfo:
        validate_image_bytes(b"definitely not an image", filename="plaque.png")

    message = str(excinfo.value)
    assert "plaque.png" in message
    assert "JPEG, PNG, WebP, GIF, TIFF or AVIF" in message
    assert "Traceback" not in message


def test_invalid_bytes_without_filename_still_get_a_safe_message():
    with pytest.raises(UnsupportedMediaTypeError, match="image/png"):
        validate_image_bytes(b"???", filename="", content_type="image/png")


def test_real_but_unsupported_format_is_rejected():
    bitmap = b"BM" + b"\x00" * 40  # a genuine BMP, outside the contract
    with pytest.raises(UnsupportedMediaTypeError):
        validate_image_bytes(bitmap, filename="legacy.bmp")


def test_oversized_bytes_are_rejected_before_content_inspection():
    blob = b"\x00" * (MAX_UPLOAD_BYTES + 1)  # not even a valid image
    with pytest.raises(PayloadTooLargeError, match="10 MB"):
        validate_image_bytes(blob, filename="huge.png")


# ---------------------------------------------------------------------------
# preparation: decode, limits, orientation, representation
# ---------------------------------------------------------------------------


def test_valid_png_is_prepared_and_round_trips():
    prepared = prepare_image(_png(64, 48), filename="x.png", content_type="image/png")

    assert prepared.format == "PNG"
    assert (prepared.width, prepared.height) == (64, 48)
    assert prepared.mode == "RGB"
    assert prepared.source_bytes > 0

    # Canonical hand-off is itself a valid PNG with the same geometry.
    reparsed = PILImage.open(io.BytesIO(prepared.to_png_bytes()))
    assert reparsed.format == "PNG"
    assert reparsed.size == (64, 48)


def test_valid_jpeg_is_prepared():
    prepared = prepare_image(_jpeg(100, 50), filename="photo.jpg")

    assert prepared.format == "JPEG"
    assert (prepared.width, prepared.height) == (100, 50)
    assert prepared.mode == "RGB"


def test_exif_orientation_is_normalised():
    sideways = prepare_image(_jpeg(100, 50, exif_orientation=6), filename="rotated.jpg")
    upright = prepare_image(_jpeg(100, 50), filename="plain.jpg")

    # Orientation 6 (rotate 90° CW) must be baked into the dimensions.
    assert (sideways.width, sideways.height) == (50, 100)
    # Without the tag nothing is transposed.
    assert (upright.width, upright.height) == (100, 50)


def test_palette_images_normalise_to_rgb():
    prepared = prepare_image(_gif(32, 32), filename="anim.gif")

    assert prepared.format == "GIF"
    assert prepared.mode in ("RGB", "RGBA")  # never palette/P at hand-off


def test_empty_file_is_rejected_by_the_preparation_gate():
    with pytest.raises(UnprocessableEntityError, match="empty"):
        prepare_image(b"", filename="empty.png")


def test_malformed_bytes_are_rejected_before_any_decode(monkeypatch):
    """D1 acceptance: the content gate runs before Pillow ever opens the
    file — decoding must not even be attempted."""

    def explode(*_args, **_kwargs):
        raise AssertionError("Image.open must not run for unsupported bytes")

    monkeypatch.setattr(
        "app.services.image_service.Image.open", explode
    )

    with pytest.raises(UnsupportedMediaTypeError):
        prepare_image(b"not an image at all", filename="fake.png")


def test_truncated_image_fails_with_a_controlled_decode_error():
    full = _png(128, 128)
    truncated = full[: len(full) // 2]

    with pytest.raises(UnprocessableEntityError) as excinfo:
        prepare_image(truncated, filename="cut.png")

    message = str(excinfo.value)
    assert "damaged or incomplete" in message or "could not be decoded" in message
    assert "Traceback" not in message and "PIL" not in message


def test_excessive_per_side_dimensions_rejected_before_decode():
    # Header declares 20000×100; the file carries no pixels at all.
    with pytest.raises(PayloadTooLargeError, match="per-side limit"):
        prepare_image(_png_declaring(20000, 100), filename="wide.png")

    assert MAX_DIMENSION == 16_384


def test_excessive_total_pixels_rejected_before_decode():
    # 8000×8000 = 64 MP: each side fine, total over the decode budget —
    # and still below Pillow's own bomb threshold, so this is OUR check.
    with pytest.raises(PayloadTooLargeError, match="decode limit"):
        prepare_image(_png_declaring(8000, 8000), filename="huge.png")

    assert MAX_DECODED_PIXELS == 40_000_000


def test_decompression_bomb_is_mapped_to_a_controlled_413():
    with pytest.raises(PayloadTooLargeError) as excinfo:
        prepare_image(_png_declaring(50_000, 50_000), filename="bomb.png")

    message = str(excinfo.value)
    assert "Traceback" not in message and "bomb" not in message.lower()