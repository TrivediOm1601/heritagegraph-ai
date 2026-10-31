"""Bounded retries for live historical-source fetches.

The live sources (Wikipedia search/pages, Commons media, Wikidata) are
public APIs: they intermittently answer **HTTP 429** (rate limited) or a
transient **5xx**, and a single-shot caller turns that into *"no results"*.
That is exactly the failure the product saw — the first search came back
thin and the same search worked a moment later, because the attempt that
failed was never retried.

This module is the one place that decides *when a retry is legitimate*:

* **transient only** — HTTP 408/425/429/500/502/503/504, timeouts and
  connection/DNS/TLS errors. A permanent 4xx (400/401/403/404/…) is never
  retried: retrying our own bad request just burns the source's quota.
* **bounded** — a small attempt count *and* a wall-clock budget for the
  whole operation (:data:`RetryPolicy.max_total_seconds`), so a source
  that hangs cannot multiply the request's latency by the attempt count.
  The budget is checked *before* sleeping, so the first attempt always
  happens and a timeout is never paid twice.
* **`Retry-After` honoured** — a ``429``/``503`` naming a delay waits at
  least that long (capped by :data:`MAX_RETRY_AFTER_SECONDS`) instead of
  guessing with backoff.
* **jittered exponential backoff** — ``base * 2**n`` plus up to
  :data:`JITTER_RATIO` of noise, so several clients rate-limited at the
  same moment do not retry in lockstep.
* **cancellation-safe** — ``asyncio.CancelledError`` is never caught, so a
  cancelled request (client disconnected, server shutting down) stops
  immediately instead of being retried.

Nothing here fabricates anything: when the attempts are exhausted the last
:class:`TransientSourceError` is raised, and each caller keeps its existing
"degrade, do not fail" rule (an unavailable source yields no live results
and a visible notice — never invented records).
"""

from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional, TypeVar

from ..core.logging import get_logger

logger = get_logger("sources.retry")

__all__ = [
    "ENV_ATTEMPTS",
    "MAX_ATTEMPTS",
    "MAX_RETRY_AFTER_SECONDS",
    "DEFAULT_POLICY",
    "JITTER_RATIO",
    "RETRYABLE_STATUSES",
    "RetryPolicy",
    "TransientSourceError",
    "backoff_delay",
    "is_retryable_status",
    "parse_retry_after",
    "policy_from_environ",
    "run_with_retries",
]

T = TypeVar("T")

#: Total attempts (the first try plus retries) for one source operation.
#: Two is deliberate: the failures worth retrying are the fast ones (429,
#: 5xx), and one retry already recovers them while keeping the worst-case
#: latency of a hanging source close to a single attempt.
DEFAULT_ATTEMPTS = 2
#: Upper bound on the configured attempt count.
MAX_ATTEMPTS = 5

#: Backoff: ``base * 2**n`` seconds, capped, plus jitter.
DEFAULT_BASE_DELAY_SECONDS = 0.35
DEFAULT_MAX_DELAY_SECONDS = 4.0
JITTER_RATIO = 0.25

#: Wall-clock budget for one operation's attempts (see module docstring).
DEFAULT_MAX_TOTAL_SECONDS = 10.0

#: ``Retry-After`` values above this are treated as "not worth waiting
#: inline": the caller degrades now and the next request can succeed.
MAX_RETRY_AFTER_SECONDS = 5.0

#: Statuses that are worth another attempt (408 request timeout, 425 too
#: early, 429 rate limited, 5xx server-side). Everything else is permanent.
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Optional override for the attempt count (defaults to DEFAULT_ATTEMPTS).
ENV_ATTEMPTS = "HG_SOURCE_RETRIES"


class TransientSourceError(Exception):
    """A source failure that is worth retrying (never a permanent error).

    ``retry_after`` carries the source's own ``Retry-After`` value in
    seconds when it named one, and ``status`` the HTTP status when there
    was a response at all (``None`` for transport failures).
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: Optional[float] = None,
        status: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.status = status


@dataclass(frozen=True)
class RetryPolicy:
    """How many attempts an operation gets, and how long it may take."""

    attempts: int = DEFAULT_ATTEMPTS
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS
    #: Whole-operation wall-clock budget (checked before each retry sleep).
    max_total_seconds: float = DEFAULT_MAX_TOTAL_SECONDS

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1")


#: The policy used when the caller does not choose one.
DEFAULT_POLICY = RetryPolicy()


def is_retryable_status(status: int) -> bool:
    """True for the HTTP statuses worth another attempt."""
    return status in RETRYABLE_STATUSES


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse a ``Retry-After`` header value into seconds (best effort).

    Only the numeric-seconds form is understood; the HTTP-date form is
    ignored rather than misparsed into a wrong wait (same rule as
    :func:`app.integrations.classify_http_error`).
    """
    if not value:
        return None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return seconds


def policy_from_environ(environ: Optional[Mapping[str, str]] = None) -> RetryPolicy:
    """The policy for this deployment (``HG_SOURCE_RETRIES`` when set).

    An unusable value is logged and ignored rather than failing a search:
    source retries are an enhancement, so a typo in the variable degrades
    to the documented default.
    """
    env = os.environ if environ is None else environ
    raw = (env.get(ENV_ATTEMPTS) or "").strip()
    if not raw:
        return DEFAULT_POLICY
    try:
        attempts = int(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not an integer; using %d attempts",
            ENV_ATTEMPTS,
            raw,
            DEFAULT_ATTEMPTS,
        )
        return DEFAULT_POLICY
    clamped = max(1, min(attempts, MAX_ATTEMPTS))
    if clamped != attempts:
        logger.warning(
            "%s=%d is outside 1..%d; using %d attempts",
            ENV_ATTEMPTS,
            attempts,
            MAX_ATTEMPTS,
            clamped,
        )
    return RetryPolicy(
        attempts=clamped,
        base_delay_seconds=DEFAULT_BASE_DELAY_SECONDS,
        max_delay_seconds=DEFAULT_MAX_DELAY_SECONDS,
        max_total_seconds=DEFAULT_MAX_TOTAL_SECONDS,
    )


def backoff_delay(
    attempt: int,
    policy: RetryPolicy = DEFAULT_POLICY,
    *,
    retry_after: Optional[float] = None,
    rng: Optional[random.Random] = None,
) -> float:
    """Seconds to wait before ``attempt`` (0-based) is tried again.

    A usable ``Retry-After`` wins over the computed backoff — the source
    knows better — but is still capped, and the result never exceeds
    ``policy.max_delay_seconds`` beyond that cap (a rate limiter asking
    for 30s is answered with :data:`MAX_RETRY_AFTER_SECONDS` and a fresh
    request later, not a request that hangs).
    """
    if retry_after is not None and retry_after > 0:
        return min(retry_after, MAX_RETRY_AFTER_SECONDS)
    generator = rng or random
    exponential = policy.base_delay_seconds * (2**attempt)
    capped = min(exponential, policy.max_delay_seconds)
    return capped * (1.0 + generator.uniform(0, JITTER_RATIO))


async def run_with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy = DEFAULT_POLICY,
    label: str = "historical source",
    on_retry: Optional[Callable[[int, TransientSourceError, float], None]] = None,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    rng: Optional[random.Random] = None,
    clock: Optional[Callable[[], float]] = None,
) -> T:
    """Run ``operation`` with bounded retries on transient failures.

    ``operation`` must raise :class:`TransientSourceError` for a failure
    worth retrying; anything else (including ``CancelledError``) travels
    straight out to the caller, so a permanent or programming error is
    never retried and never hidden.

    ``on_retry(attempt_index, error, delay)`` is called before each retry
    wait — the source modules use it to count retries for the API's
    degraded-source notice. ``sleep``/``rng``/``clock`` are injectable so
    tests run instantly and deterministically.
    """
    waits = sleep or asyncio.sleep
    now = clock or asyncio.get_running_loop().time
    started = now()
    last_error: Optional[TransientSourceError] = None

    for attempt in range(policy.attempts):
        try:
            return await operation()
        except TransientSourceError as exc:
            last_error = exc
            if attempt + 1 >= policy.attempts:
                break
            delay = backoff_delay(attempt, policy, retry_after=exc.retry_after, rng=rng)
            elapsed = now() - started
            if elapsed + delay > policy.max_total_seconds:
                # Retrying would blow the operation's time budget: degrade
                # now (the source is unreachable/hanging, not merely busy).
                logger.info(
                    "%s: not retrying after %s (%.1fs elapsed, %.1fs budget)",
                    label,
                    exc,
                    elapsed,
                    policy.max_total_seconds,
                )
                break
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            logger.info(
                "%s: retrying in %.2fs after %s (attempt %d/%d)",
                label,
                delay,
                exc,
                attempt + 2,
                policy.attempts,
            )
            await waits(delay)

    assert last_error is not None  # only reachable through the except branch
    raise last_error


async def fetch_json_with_retries(
    attempt_once: Callable[[], Awaitable[Any]],
    *,
    policy: RetryPolicy,
    label: str,
    on_retry: Optional[Callable[[int, TransientSourceError, float], None]] = None,
) -> Any:
    """``run_with_retries`` for a fetcher that returns ``None`` on failure.

    The wrapped ``attempt_once`` raises :class:`TransientSourceError` only
    for retryable conditions; this adapter converts an exhausted retry (or
    the source's permanent failure) into ``None``, which is the "degrade,
    never fabricate" contract every source module already speaks.
    """
    try:
        return await run_with_retries(
            attempt_once, policy=policy, label=label, on_retry=on_retry
        )
    except TransientSourceError as exc:
        logger.warning("%s unavailable after retries: %s", label, exc)
        return None
