"""Safe ZIP ingestion for the Heritage Archive Studio.

An uploaded ZIP is **never extracted blindly**. Every member is inspected and
one of these limits must hold, or the archive is refused:

======================  =====================================================
entry count             ``HG_ZIP_MAX_ENTRIES``           (default 60)
per-file size           ``HG_ZIP_MAX_ENTRY_BYTES``       (default 25 MiB)
total uncompressed      ``HG_ZIP_MAX_TOTAL_BYTES``       (default 250 MiB)
compression ratio       ``HG_ZIP_MAX_RATIO``             (default 150:1)
nesting depth           ``HG_ZIP_MAX_DEPTH``             (default 1 = the
                        uploaded archive itself; members may not be ZIPs)
======================  =====================================================

Path safety: absolute paths, drive letters, ``..`` segments, NUL bytes and
backslash traversal are all rejected. Only known document/image extensions
are accepted, so an executable renamed to ``.jpg`` is refused by the format
sniffing that follows ingestion.

A rejected *member* does not fail the whole archive (the rest of a real
research collection is still useful), but every rejection is reported back to
the user with its reason. Limits that protect the server (entry count, total
size, ratio) reject the archive as a whole.
"""

from __future__ import annotations

import io
import os
import pathlib
import zipfile
import zlib
from dataclasses import dataclass, field

from ..core.logging import get_logger
from .document_service import supported_kind_for_extension

logger = get_logger("archive.zip")

__all__ = [
    "DEFAULT_LIMITS",
    "ZipLimits",
    "ZipMember",
    "ZipInspection",
    "ZipSafetyError",
    "inspect_zip",
    "limits_from_env",
]

#: Local-file-header magic (an empty archive uses the end-of-central-directory).
_ZIP_MAGIC = b"PK\x03\x04"

#: Extensions that must never be extracted, whatever their declared type.
_EXECUTABLE_EXTENSIONS = frozenset(
    {
        ".exe",
        ".dll",
        ".com",
        ".scr",
        ".msi",
        ".bat",
        ".cmd",
        ".ps1",
        ".vbs",
        ".js",
        ".jse",
        ".wsf",
        ".jar",
        ".sh",
        ".bash",
        ".bin",
        ".so",
        ".dylib",
    }
)


@dataclass(frozen=True)
class ZipLimits:
    """Server-side bounds applied to one uploaded archive."""

    max_entries: int = 60
    max_entry_bytes: int = 25 * 1024 * 1024
    max_total_bytes: int = 250 * 1024 * 1024
    max_ratio: float = 150.0
    max_depth: int = 1

    def __post_init__(self) -> None:
        if min(self.max_entries, self.max_entry_bytes, self.max_total_bytes) <= 0:
            raise ValueError("ZIP limits must be positive.")


DEFAULT_LIMITS = ZipLimits()


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s is not an integer; using %d", name, default)
        return default
    return value if value > 0 else default


def limits_from_env() -> ZipLimits:
    """Limits overridable per deployment (invalid values fall back loudly)."""
    return ZipLimits(
        max_entries=_env_int("HG_ZIP_MAX_ENTRIES", DEFAULT_LIMITS.max_entries),
        max_entry_bytes=_env_int(
            "HG_ZIP_MAX_ENTRY_BYTES", DEFAULT_LIMITS.max_entry_bytes
        ),
        max_total_bytes=_env_int(
            "HG_ZIP_MAX_TOTAL_BYTES", DEFAULT_LIMITS.max_total_bytes
        ),
        max_depth=_env_int("HG_ZIP_MAX_DEPTH", DEFAULT_LIMITS.max_depth),
    )


class ZipSafetyError(ValueError):
    """The archive as a whole was refused (safe, user-facing message)."""


@dataclass(frozen=True)
class ZipMember:
    """One accepted member, already read into memory under the size bound."""

    archive_path: str
    filename: str
    data: bytes
    kind: str


@dataclass
class ZipInspection:
    """Result of inspecting one archive."""

    members: list[ZipMember] = field(default_factory=list)
    #: ``(member name, reason)`` for entries that were skipped.
    rejected: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _safe_member_path(name: str) -> str:
    """Return a normalised relative member path or raise ``ValueError``."""
    if not name or "\x00" in name:
        raise ValueError("The archive contains an entry with an invalid name.")
    normalised = name.replace("\\", "/")
    if normalised.startswith("/"):
        raise ValueError("The archive contains an absolute path.")
    # Windows drive letters ("C:/…") are absolute paths too.
    if len(normalised) >= 2 and normalised[1] == ":":
        raise ValueError("The archive contains an absolute path.")
    parts = [part for part in normalised.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError("The archive contains a path traversal ('..').")
    if not parts:
        raise ValueError("The archive contains an empty path.")
    return "/".join(parts)


def inspect_zip(
    data: bytes,
    *,
    limits: ZipLimits | None = None,
    depth: int = 1,
) -> ZipInspection:
    """Validate and read an uploaded ZIP without extracting it to disk.

    Raises :class:`ZipSafetyError` when the archive itself is unacceptable;
    individual members that cannot be accepted are recorded in
    ``inspection.rejected`` (never silently dropped).
    """
    bounds = limits or limits_from_env()
    if not data.startswith(_ZIP_MAGIC) and not data.startswith(b"PK\x05\x06"):
        raise ZipSafetyError("The uploaded file is not a ZIP archive.")
    if depth > bounds.max_depth:
        raise ZipSafetyError(
            "Nested ZIP archives are not extracted (the configured depth limit "
            f"is {bounds.max_depth}). Upload the inner archive directly."
        )

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ZipSafetyError(
            "The uploaded file could not be read as a ZIP archive (it may be "
            "corrupt or truncated)."
        ) from exc

    inspection = ZipInspection()
    with archive:
        entries = archive.infolist()
        if len(entries) > bounds.max_entries:
            raise ZipSafetyError(
                f"This archive contains {len(entries)} entries, more than the "
                f"{bounds.max_entries} allowed. Split it into smaller archives."
            )
        declared_total = sum(max(0, entry.file_size) for entry in entries)
        if declared_total > bounds.max_total_bytes:
            raise ZipSafetyError(
                "This archive would expand to more than the "
                f"{bounds.max_total_bytes // (1024 * 1024)} MB limit."
            )

        read_total = 0
        for entry in entries:
            if entry.is_dir():
                continue
            name = entry.filename
            try:
                safe_path = _safe_member_path(name)
            except ValueError as exc:
                inspection.rejected.append((name, str(exc)))
                continue

            if entry.flag_bits & 0x1:
                inspection.rejected.append(
                    (name, "The entry is encrypted and cannot be read.")
                )
                continue

            extension = pathlib.PurePath(safe_path).suffix.lower()
            if extension == ".zip":
                inspection.rejected.append(
                    (
                        name,
                        "Nested ZIP archives are not extracted. Upload the inner "
                        "archive directly.",
                    )
                )
                continue
            if extension in _EXECUTABLE_EXTENSIONS:
                inspection.rejected.append(
                    (name, "Executable files are not accepted in an archive.")
                )
                continue
            kind = supported_kind_for_extension(safe_path)
            if kind is None:
                inspection.rejected.append(
                    (
                        name,
                        "Unsupported file type. Accepted: images, PDF, DOCX, "
                        "PPTX, TXT and Markdown.",
                    )
                )
                continue

            if entry.file_size > bounds.max_entry_bytes:
                inspection.rejected.append(
                    (
                        name,
                        f"The file is larger than the "
                        f"{bounds.max_entry_bytes // (1024 * 1024)} MB per-file limit.",
                    )
                )
                continue
            compression = max(1, entry.compress_size)
            ratio = entry.file_size / compression
            if entry.file_size > 1_000_000 and ratio > bounds.max_ratio:
                inspection.rejected.append(
                    (
                        name,
                        "The entry expands too far for its compressed size "
                        "(possible decompression bomb).",
                    )
                )
                continue

            try:
                with archive.open(entry) as stream:
                    # Bounded read: a header can lie about the real size.
                    payload = stream.read(bounds.max_entry_bytes + 1)
            except (RuntimeError, zlib.error, NotImplementedError) as exc:
                inspection.rejected.append(
                    (name, "The entry could not be decompressed.")
                )
                logger.warning("ZIP member %s could not be read: %s", name, type(exc).__name__)
                continue
            except Exception as exc:  # noqa: BLE001 — one bad entry only
                inspection.rejected.append((name, "The entry could not be read."))
                logger.warning("ZIP member %s failed: %s", name, type(exc).__name__)
                continue

            if len(payload) > bounds.max_entry_bytes:
                inspection.rejected.append(
                    (
                        name,
                        f"The file exceeds the "
                        f"{bounds.max_entry_bytes // (1024 * 1024)} MB per-file limit.",
                    )
                )
                continue
            read_total += len(payload)
            if read_total > bounds.max_total_bytes:
                raise ZipSafetyError(
                    "This archive would expand to more than the "
                    f"{bounds.max_total_bytes // (1024 * 1024)} MB limit."
                )

            inspection.members.append(
                ZipMember(
                    archive_path=safe_path,
                    filename=pathlib.PurePosixPath(safe_path).name[:255],
                    data=payload,
                    kind=kind,
                )
            )

    if not inspection.members:
        detail = ""
        if inspection.rejected:
            name, reason = inspection.rejected[0]
            detail = f" First skipped entry — {name}: {reason}"
        raise ZipSafetyError(
            "No supported file could be read from this archive." + detail
        )
    if inspection.rejected:
        inspection.warnings.append(
            f"{len(inspection.rejected)} entr"
            f"{'y was' if len(inspection.rejected) == 1 else 'ies were'} skipped "
            "because they could not be accepted safely."
        )
    return inspection
