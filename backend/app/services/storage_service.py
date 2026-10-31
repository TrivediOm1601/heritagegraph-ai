"""Archive file storage — one small abstraction over the local filesystem.

The upload pipeline never writes binary content into the database: originals
are stored here (addressed by their SHA-256, so identical content is stored
once) and only the relative ``storage_key`` plus metadata are persisted.

Why an abstraction at all: the deployment is a single machine, so a proxied
object store would be unnecessary infrastructure. The three functions below
are the only places that know the storage is a filesystem, so replacing them
with an S3/R2 client later does not touch the pipeline.

Safety rules enforced here:

* a storage key is validated against a strict pattern and resolved with
  :meth:`pathlib.Path.resolve` under the storage root — a key can never
  escape the directory (no ``..``, no absolute path, no drive letter);
* writes are atomic (temporary file + ``os.replace``), so a crash cannot
  leave a half-written original that a later read would treat as valid;
* files are never served directly by the web server: the download endpoint
  authenticates, checks ownership, and streams the bytes itself.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import tempfile
from dataclasses import dataclass
from functools import lru_cache

__all__ = [
    "StorageError",
    "StoredObject",
    "storage_root",
    "save_bytes",
    "read_bytes",
    "delete_bytes",
    "key_exists",
]

#: A storage key is ``<2 hex>/<64 hex>`` — nothing else is accepted.
_KEY_PATTERN = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{64}$")

#: Environment variable overriding the storage directory (tests, deployments).
ENV_STORAGE_DIR = "HG_STORAGE_DIR"


class StorageError(RuntimeError):
    """A stored object could not be written or read.

    Messages are safe to log (they never include a full filesystem path).
    """


@dataclass(frozen=True)
class StoredObject:
    """Result of storing bytes: content hash plus its relative key."""

    sha256: str
    storage_key: str
    size_bytes: int


def _default_root() -> pathlib.Path:
    """``<backend>/var/archive`` — created on first use, git-ignored."""
    return pathlib.Path(__file__).resolve().parents[2] / "var" / "archive"


@lru_cache(maxsize=1)
def storage_root() -> pathlib.Path:
    """Absolute storage root (``HG_STORAGE_DIR`` overrides the default)."""
    configured = (os.environ.get(ENV_STORAGE_DIR) or "").strip()
    root = pathlib.Path(configured).expanduser() if configured else _default_root()
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def reset_storage_root() -> None:
    """Drop the cached root (tests that point storage at a temp directory)."""
    storage_root.cache_clear()


def _resolve(key: str) -> pathlib.Path:
    """Validate ``key`` and return its path under the storage root."""
    candidate = (key or "").strip().replace("\\", "/")
    if not _KEY_PATTERN.fullmatch(candidate):
        raise StorageError("The stored object reference is invalid.")
    root = storage_root()
    path = (root / candidate).resolve()
    if root != path and root not in path.parents:
        raise StorageError("The stored object reference is invalid.")
    return path


def _atomic_write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".upload-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise StorageError(
            "The uploaded file could not be stored. Check the server's disk "
            "space and permissions."
        ) from exc


def save_bytes(data: bytes) -> StoredObject:
    """Store ``data`` under its SHA-256 and return the object reference.

    Identical content maps to the same key, so re-uploading a file is a
    no-op on disk (the database still records the new upload as a duplicate).
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise StorageError("An empty file cannot be stored.")
    payload = bytes(data)
    digest = hashlib.sha256(payload).hexdigest()
    key = f"{digest[:2]}/{digest}"
    path = _resolve(key)
    if not path.exists():
        _atomic_write(path, payload)
    return StoredObject(sha256=digest, storage_key=key, size_bytes=len(payload))


def read_bytes(key: str) -> bytes:
    """Return the stored bytes for ``key`` (``StorageError`` when missing)."""
    path = _resolve(key)
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise StorageError(
            "The stored original is no longer available on this server."
        ) from exc
    except OSError as exc:
        raise StorageError("The stored original could not be read.") from exc


def key_exists(key: str) -> bool:
    try:
        return _resolve(key).is_file()
    except StorageError:
        return False


def delete_bytes(key: str) -> None:
    """Remove one stored object (best-effort; missing is not an error)."""
    path = _resolve(key)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StorageError("The stored original could not be removed.") from exc
