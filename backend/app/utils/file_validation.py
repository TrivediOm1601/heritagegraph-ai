"""D1 — byte-level image file validation.

The **content gate** that runs before any decode, OCR or vision-model
call (acceptance criterion of D1): bytes are checked against the shared
contract's size limit and sniffed for a supported container by *magic
bytes* — never by filename or the client-declared Content-Type, which an
attacker controls.

Controlled errors reuse Module B's canonical API error classes, so every
failure already speaks the agreed envelope
(``{"error": {"code", "message", "request_id"}}``) and never exposes
parser internals.

Contract source of truth: ``app.schemas.analysis`` (field name, accepted
MIME types and the 10 MiB limit live there for every member).
"""

from __future__ import annotations

from ..core.errors import (
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    UnprocessableEntityError,
)
from ..schemas.analysis import MAX_UPLOAD_BYTES

__all__ = [
    "SUPPORTED_FORMATS",
    "detect_image_format",
    "validate_image_bytes",
]

#: Canonical format label -> contract MIME type. Key order is the order
#: used in user-facing messages (mirrors the frontend's guard wording).
SUPPORTED_FORMATS: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "TIFF": "image/tiff",
    "AVIF": "image/avif",
}

_FORMAT_LIST = "JPEG, PNG, WebP, GIF, TIFF or AVIF"

#: Brands accepted as AVIF (major brand at offset 8; ISO-BMFF containers
#: for HEIC/HEIF deliberately NOT accepted — they are outside the contract).
_AVIF_BRANDS = (b"avif", b"avis")


def detect_image_format(data: bytes) -> str | None:
    """Return the canonical format label for a supported container.

    Content-based sniffing only; ``None`` means the bytes are not one of
    the contract's supported formats.
    """
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if len(data) >= 4 and data[:4] in (b"II*\x00", b"MM\x00*"):
        return "TIFF"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _AVIF_BRANDS:
        return "AVIF"
    return None


def validate_image_bytes(
    data: bytes,
    *,
    filename: str = "",
    content_type: str | None = None,
) -> str:
    """Validate one upload's bytes; return its canonical format label.

    Checks, in order (cheap first):

    1. non-empty,
    2. size against the contract's ``MAX_UPLOAD_BYTES``,
    3. magic-byte detection of a supported format.

    Raises the canonical API errors (422 / 413 / 415) with messages safe
    for the UI. ``filename``/``content_type`` only decorate messages —
    the verdict is based purely on content.
    """
    if not data:
        raise UnprocessableEntityError("The uploaded file is empty.")

    if len(data) > MAX_UPLOAD_BYTES:
        limit_mib = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise PayloadTooLargeError(
            f"The uploaded image exceeds the {limit_mib} MB limit."
        )

    detected = detect_image_format(data)
    if detected is None:
        shown = filename or content_type or "The file"
        if filename:
            shown = f'"{filename}"'
        raise UnsupportedMediaTypeError(
            f"{shown} is not a supported image file. Use {_FORMAT_LIST}."
        )
    return detected