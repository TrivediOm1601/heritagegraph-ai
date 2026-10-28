"""D1 — image preparation: safe decode, limits, orientation, representation.

:func:`prepare_image` is the gate B3 calls before any OCR or vision-model
work. It chains the byte-level content gate
(:func:`app.utils.file_validation.validate_image_bytes`) with a guarded
Pillow decode:

1. **content gate first** — unsupported/malformed bytes are rejected
   before a single pixel is decoded (D1's acceptance criterion);
2. **dimension limits before ``load()``** — header-declared sizes are
   checked against :data:`MAX_DIMENSION` / :data:`MAX_DECODED_PIXELS`
   while the file is still lazy, so an "oversized" file costs a header
   parse, not a decode (and Pillow's own decompression-bomb guard is
   caught and mapped to the same controlled 413);
3. **corrupt payloads** — a damaged/truncated decode surfaces as a
   controlled 422, never a Pillow traceback;
4. **orientation** — EXIF orientation is applied (``ImageOps.exif_transpose``)
   so a camera photo is upright for OCR;
5. **representation** — the result is normalised to RGB/RGBA, the two
   modes every downstream provider accepts, with a canonical
   ``to_png_bytes()`` hand-off.

Every error class is Module B's canonical envelope error (413/415/422),
so failures propagate through B3's pipeline without any new response
shape.

Signature note (pending agreement with Member 2): synchronous and
CPU-bound — B3 may wrap it in ``asyncio.to_thread`` if needed; the
return value is a frozen dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from ..core.errors import PayloadTooLargeError, UnprocessableEntityError
from ..utils.file_validation import validate_image_bytes

__all__ = [
    "MAX_DIMENSION",
    "MAX_DECODED_PIXELS",
    "PreparedImage",
    "prepare_image",
]

#: Longest accepted edge after decode (a scan or screenshot wider than
#: this is rejected rather than fed to OCR). Chosen comfortably above
#: real-world documents (a 300 dpi A0 poster is ~5900 px).
MAX_DIMENSION = 16_384

#: Total decoded pixels (~40 MP) — the memory guard. Below Pillow's own
#: bomb threshold (178 MP) so OUR controlled message usually fires first;
#: Pillow's DecompressionBombError is caught regardless and mapped to
#: the same 413.
MAX_DECODED_PIXELS = 40_000_000

_FORMAT_LIST = "JPEG, PNG, WebP, GIF, TIFF or AVIF"


@dataclass(frozen=True)
class PreparedImage:
    """A validated, decoded, orientation-normalised image."""

    #: Canonical source format label ("PNG", "JPEG", ...).
    format: str
    #: Dimensions AFTER EXIF orientation was applied.
    width: int
    height: int
    #: Normalised pixel mode: "RGB" or "RGBA".
    mode: str
    #: The in-memory Pillow image (EXIF-transposed, mode-normalised).
    image: Image.Image
    #: Size of the original upload in bytes (for run metadata/logging).
    source_bytes: int

    def to_png_bytes(self) -> bytes:
        """Canonical lossless hand-off for OCR/vision providers."""
        buffer = BytesIO()
        self.image.save(buffer, format="PNG")
        return buffer.getvalue()


def _normalise_mode(image: Image.Image) -> Image.Image:
    """Reduce every source mode to RGB or RGBA (provider-safe)."""
    if image.mode in ("RGB", "RGBA"):
        return image
    has_alpha = "A" in image.getbands() or "transparency" in image.info
    return image.convert("RGBA" if has_alpha else "RGB")


def prepare_image(
    data: bytes,
    *,
    filename: str = "",
    content_type: str | None = None,
) -> PreparedImage:
    """Validate, decode and normalise one uploaded image.

    Raises ``UnsupportedMediaTypeError`` (415), ``PayloadTooLargeError``
    (413) or ``UnprocessableEntityError`` (422) — always *before* any
    OCR/AI processing can start. Original exceptions stay chained for
    the server log; the raised message is UI-safe.
    """
    # 1. Content gate: size + supported container by magic bytes.
    detected = validate_image_bytes(
        data, filename=filename, content_type=content_type
    )

    # 2. Header parse only (lazy) — format is re-checked here because a
    #    container can be well-formed at byte 0 yet unparseable as an image.
    try:
        with Image.open(BytesIO(data)) as probe:
            declared_size = probe.size
    except Image.DecompressionBombError as exc:
        raise PayloadTooLargeError(
            "The image declares more pixels than this service will decode."
        ) from exc
    except (UnidentifiedImageError, OSError, EOFError, ValueError) as exc:
        raise UnprocessableEntityError(
            f"The file could not be decoded as {detected}. It may be "
            f"damaged or truncated. Use {_FORMAT_LIST}."
        ) from exc

    if not declared_size or len(declared_size) != 2:
        raise UnprocessableEntityError(
            f"The file does not declare a usable image size. Use {_FORMAT_LIST}."
        )
    width, height = declared_size

    # 3. Dimension limits BEFORE decoding any pixel data.
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise PayloadTooLargeError(
            f"The image dimensions ({width}×{height}px) exceed the "
            f"{MAX_DIMENSION}px per-side limit."
        )
    if width * height > MAX_DECODED_PIXELS:
        raise PayloadTooLargeError(
            f"The image's {width * height:,} pixels exceed the "
            f"{MAX_DECODED_PIXELS:,}-pixel decode limit."
        )

    # 4. Full decode — corrupt/truncated payloads fail here, controlled.
    try:
        with Image.open(BytesIO(data)) as decoded:
            decoded.load()
            working = decoded.copy()
    except Image.DecompressionBombError as exc:
        raise PayloadTooLargeError(
            "The image declares more pixels than this service will decode."
        ) from exc
    except (UnidentifiedImageError, OSError, EOFError, ValueError) as exc:
        raise UnprocessableEntityError(
            "The image data is damaged or incomplete and could not be "
            f"decoded. Use {_FORMAT_LIST}."
        ) from exc

    # 5. Orientation normalisation (EXIF Orientation tag, if present).
    oriented = ImageOps.exif_transpose(working) or working

    # 6. Representation normalisation: RGB/RGBA only, canonical PNG hand-off.
    normalised = _normalise_mode(oriented)

    return PreparedImage(
        format=detected,
        width=normalised.width,
        height=normalised.height,
        mode=normalised.mode,
        image=normalised,
        source_bytes=len(data),
    )