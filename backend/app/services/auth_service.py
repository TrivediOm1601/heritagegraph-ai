"""Authentication primitives: password hashing, session tokens, validation.

Design decisions (documented so they can be reviewed):

* **Password hashing** — PBKDF2-HMAC-SHA256 with a per-password 16-byte
  salt and 600 000 iterations (OWASP's current PBKDF2-SHA256 guidance),
  from the Python standard library. Argon2id would be preferable in a
  greenfield service, but adding a native-extension dependency to this
  project would complicate the documented setup for no security gain at
  these parameters; PBKDF2-SHA256 is a modern, appropriate KDF.
* **Session tokens** — 32 random bytes from :func:`secrets.token_urlsafe`.
  Only the SHA-256 digest is stored, so the database never holds a usable
  token. Tokens are transmitted solely in an HttpOnly cookie.
* **Timing** — verification uses :func:`hmac.compare_digest`, and a failed
  login against an unknown address still performs a dummy verification so
  response timing does not reveal which emails have accounts.
* **Enumeration** — signup never confirms whether an account already
  exists; login always returns the same generic error.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

__all__ = [
    "SESSION_COOKIE",
    "SESSION_TTL_HOURS_DEFAULT",
    "PBKDF2_ITERATIONS",
    "hash_password",
    "verify_password",
    "normalize_email",
    "validate_email",
    "validate_password",
    "new_session_token",
    "session_token_hash",
    "session_expiry",
    "as_utc",
    "DUMMY_PASSWORD_HASH",
]

#: Cookie name carrying the opaque session token.
SESSION_COOKIE = "hg_session"

#: Default session lifetime (7 days); override with SESSION_TTL_HOURS.
SESSION_TTL_HOURS_DEFAULT = 168

#: OWASP current guidance for PBKDF2-HMAC-SHA256.
PBKDF2_ITERATIONS = 600_000

_ALGORITHM = "pbkdf2_sha256"

#: Address-shaped check. Deliberately permissive (plus-addressing, IDN,
#: long TLDs) but anchored and length-bounded so it cannot be used to smuggle
#: line breaks or control characters into a header/log.
_EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_EMAIL_LENGTH = 320


def normalize_email(email: str | None) -> str:
    """Trim and lowercase an address for storage and lookup."""
    return (email or "").strip().lower()


def validate_email(email: str) -> str | None:
    """Return an actionable message when ``email`` is unusable."""
    if not email:
        return "Enter your email address."
    if len(email) > MAX_EMAIL_LENGTH:
        return f"An email address can be at most {MAX_EMAIL_LENGTH} characters."
    if not _EMAIL_PATTERN.match(email):
        return "Enter a valid email address, for example name@example.org."
    return None


def validate_password(password: str) -> str | None:
    """Policy for new passwords (login is never policy-checked)."""
    if not password:
        return "Enter a password."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters for your password."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters for your password."
    return None


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Hash ``password`` as ``pbkdf2_sha256$iterations$salt$digest``."""
    if not password:
        raise ValueError("hash_password requires a non-empty password.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification of ``password`` against a stored hash."""
    if not password or not encoded:
        return False
    try:
        algorithm, iterations_raw, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iterations_raw)
        if iterations < 1 or iterations > 5_000_000:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(candidate, expected)


#: A real hash of a random password, used to equalise timing for unknown
#: accounts (never matches any user-supplied password in practice).
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))


def new_session_token() -> str:
    """A 256-bit opaque session token (cookie value)."""
    return secrets.token_urlsafe(32)


def session_token_hash(token: str) -> str:
    """Digest stored in the database instead of the token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_ttl_hours(environ: dict[str, str] | None = None) -> int:
    """Configured session lifetime in hours (SESSION_TTL_HOURS)."""
    env = os.environ if environ is None else environ
    raw = (env.get("SESSION_TTL_HOURS") or "").strip()
    if not raw:
        return SESSION_TTL_HOURS_DEFAULT
    try:
        hours = int(raw)
    except ValueError:
        return SESSION_TTL_HOURS_DEFAULT
    # Clamp: at least one hour, at most 30 days.
    return max(1, min(hours, 24 * 30))


def session_expiry(hours: int | None = None) -> datetime:
    """UTC instant at which a new session expires."""
    lifetime = SESSION_TTL_HOURS_DEFAULT if hours is None else hours
    return datetime.now(timezone.utc) + timedelta(hours=lifetime)


def as_utc(value: datetime | None) -> datetime | None:
    """Normalise a stored datetime to timezone-aware UTC.

    SQLite returns naive datetimes even for ``DateTime(timezone=True)``
    columns, so comparisons must normalise rather than trust the driver.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
