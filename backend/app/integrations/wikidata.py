"""Stable subject identity — Wikidata ``instance of`` for retrieved topics.

A related topic must not be "just a display string": the UI needs to know
whether "Lalit Modi" is a person, a place or an event before it renders a
card, and the project already has a canonical vocabulary for that
(``person``/``place``/``event``/``date``/``other`` in
:mod:`app.db.models`).

Rather than guessing a type from the title's wording, this module asks the
same authority the topic already has an identity in: Wikidata. The
MediaWiki ``pageprops`` gives each page a ``wikibase_item`` (``Q…``), and
one batched ``wbgetentities`` call returns the ``instance of`` (P31)
claims for all of them.

Two deliberate limits:

* the P31 → canonical-type map is **small and explicit** (see
  :data:`_INSTANCE_TYPES`); an unmapped ``instance of`` value yields no
  type at all, and the UI simply omits the chip. Inventing "other" for a
  castle would be a mislabelling, not a fallback;
* the ``other`` type is only used for QIDs the file *does* classify
  (e.g. an organisation the app has no category for), never as a default.

Every failure degrades to ``{}`` — topics then render without a type.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import httpx

from ..core.logging import get_logger
from . import ProviderConfigurationError, read_timeout
from .wikipedia import USER_AGENT

logger = get_logger("sources.wikidata")

__all__ = [
    "ENTITY_TYPES",
    "SubjectClass",
    "clear_cache",
    "fetch_subject_classes",
    "is_index_description",
]

API_PATH = "https://www.wikidata.org/w/api.php"

DEFAULT_TIMEOUT_SECONDS = 8.0
PROVIDER = "historical source service"
ENV_TIMEOUT = "HG_SOURCE_TIMEOUT"

#: Upper bound on ids per request (the API allows 50).
MAX_IDS = 50

_CACHE_TTL_SECONDS = 600.0
_CACHE_MAX_ENTRIES = 128

@dataclass(frozen=True)
class SubjectClass:
    """What the source authority says a topic *is*.

    ``type`` is a canonical entity type (``None`` when the instance-of
    values are not mapped — deliberately untyped rather than guessed);
    ``is_index`` marks navigation pages (disambiguation/list) that must not
    be rendered as subjects.
    """

    type: Optional[str] = None
    is_index: bool = False
    wikibase_item: Optional[str] = None


_cache: dict[str, tuple[float, SubjectClass]] = {}

#: Canonical vocabulary, mirroring ``app.db.models.ENTITY_TYPES``.
ENTITY_TYPES = ("person", "place", "event", "date", "other")

#: ``instance of`` (P31) → canonical type. **Every label in this table was
#: read back from Wikidata** (``wbgetentities&props=labels``) when the table
#: was written — a QID from memory would silently mislabel a subject. An
#: unmapped value yields no type at all rather than a guessed one.
_INSTANCE_TYPES: dict[str, str] = {
    # people
    "Q5": "person",           # human
    # places: settlements, countries, landforms
    "Q515": "place",          # city
    "Q3957": "place",         # town
    "Q532": "place",          # village
    "Q486972": "place",       # human settlement
    "Q65064889": "place",     # Bronze Age settlement
    "Q15661340": "place",     # ancient city
    "Q6256": "place",         # country
    "Q3624078": "place",      # sovereign state
    "Q7275": "place",         # state
    "Q3024240": "place",      # historical country
    "Q48349": "place",        # empire
    "Q17334923": "place",     # physical location
    "Q82794": "place",        # region
    "Q4022": "place",         # river
    "Q8502": "place",         # mountain
    "Q23442": "place",        # island
    "Q35509": "place",        # cave
    # places: built heritage
    "Q41176": "place",        # building
    "Q23413": "place",        # castle
    "Q57821": "place",        # fortification
    "Q751876": "place",       # château
    "Q839954": "place",       # archaeological site
    "Q109607": "place",       # ruins
    "Q1497375": "place",      # architectural ensemble
    "Q5156823": "place",      # compound
    "Q44539": "place",        # temple
    "Q842402": "place",       # Hindu temple
    "Q1922704": "place",      # sun temple
    "Q16560": "place",        # palace
    "Q32815": "place",        # mosque
    "Q16970": "place",        # church building
    "Q108325": "place",       # chapel
    "Q44613": "place",        # monastery
    "Q33506": "place",        # museum
    "Q48356": "place",        # minaret
    "Q381885": "place",       # tomb
    "Q162875": "place",       # mausoleum
    "Q1473950": "place",      # stepwell
    "Q22698": "place",        # park
    "Q174782": "place",       # square
    "Q79007": "place",        # street
    "Q12280": "place",        # bridge
    "Q16917": "place",        # hospital
    # events
    "Q1656682": "event",      # planned event
    "Q178561": "event",       # battle
    "Q198": "event",          # war
    "Q10931": "event",        # revolution
    "Q131569": "event",       # treaty
    "Q3839081": "event",      # disaster
    "Q3318733": "event",      # independentism
    "Q40231": "event",        # public election
    "Q15275719": "event",     # recurring event
    # dates / periods
    "Q3186692": "date",       # calendar year
    "Q11514315": "date",      # historical period
    "Q15401699": "date",      # archaeological age
    # organisations and works the app has no finer category for
    "Q43229": "other",        # organization
    "Q4830453": "other",      # business
    "Q7278": "other",         # political party
    "Q163740": "other",       # nonprofit organization
    "Q79913": "other",        # non-governmental organization
    "Q11424": "other",        # film
    "Q571": "other",          # book
    "Q11032": "other",        # newspaper
    "Q3305213": "other",      # painting
    "Q860861": "other",       # sculpture
    "Q7725634": "other",      # literary work
}

#: ``instance of`` values that mark a *navigation* page rather than a
#: subject: a disambiguation or list page describes no single topic, so it
#: must never become a related-topic card ("Modi (disambiguation)").
_INDEX_PAGE_TYPES: frozenset[str] = frozenset(
    {
        "Q4167410",  # Wikimedia disambiguation page
        "Q22808320",  # Wikimedia human name disambiguation page
        "Q13406463",  # Wikimedia list article
        "Q4167836",  # Wikimedia category
        "Q11266439",  # Wikimedia template
        "Q17379835",  # Wikimedia page outside the main knowledge tree
    }
)

#: Description prefixes ``pageterms`` uses for those pages. Used as a
#: second, independent signal (a page may be a list without P31 data).
_INDEX_DESCRIPTION_MARKERS = (
    "wikimedia disambiguation",
    "wikimedia list",
    "wikimedia category",
    "wikimedia template",
    "disambiguation page",
    "topics referred to by the same term",
)

#: Precedence when a page has several ``instance of`` values: the most
#: specific category the app can render wins.
_TYPE_PRECEDENCE = ("person", "place", "event", "date", "other")


def clear_cache() -> None:
    """Drop the type cache (tests)."""
    _cache.clear()


def _timeout(environ: Optional[Mapping[str, str]]) -> float:
    try:
        return read_timeout(PROVIDER, ENV_TIMEOUT, default=DEFAULT_TIMEOUT_SECONDS, environ=environ)
    except ProviderConfigurationError as exc:
        logger.warning("source timeout misconfigured, using default: %s", exc)
        return DEFAULT_TIMEOUT_SECONDS


def _request_url(ids: Sequence[str]) -> str:
    params = urllib.parse.urlencode(
        {
            "action": "wbgetentities",
            "ids": "|".join(ids),
            "props": "claims",
            "format": "json",
            "formatversion": "2",
        }
    )
    return f"{API_PATH}?{params}"


def _fetch_via_urllib(url: str, *, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


async def _fetch_json(
    url: str, *, client: Optional[httpx.AsyncClient], timeout: float
) -> Any:
    if client is None:
        try:
            status, body = await asyncio.to_thread(_fetch_via_urllib, url, timeout=timeout)
        except urllib.error.HTTPError as exc:
            logger.warning("wikidata answered HTTP %d", exc.code)
            return None
        except Exception as exc:  # noqa: BLE001 — typing is an enhancement only
            logger.warning("wikidata fetch failed: %s", type(exc).__name__)
            return None
        if status >= 400:
            logger.warning("wikidata answered HTTP %d", status)
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("wikidata returned non-JSON content")
            return None

    request = httpx.Request(
        "GET", url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    request.extensions["timeout"] = {
        "connect": timeout,
        "read": timeout,
        "write": timeout,
        "pool": timeout,
    }
    try:
        response = await client.send(request)
    except httpx.HTTPError as exc:
        logger.warning("wikidata fetch failed: %s", type(exc).__name__)
        return None
    if response.status_code >= 400:
        logger.warning("wikidata answered HTTP %d", response.status_code)
        return None
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError):
        logger.warning("wikidata returned non-JSON content")
        return None


def is_index_description(description: Optional[str]) -> bool:
    """True when a ``pageterms`` description marks a navigation page."""
    if not description:
        return False
    lowered = " ".join(description.split()).casefold()
    return any(marker in lowered for marker in _INDEX_DESCRIPTION_MARKERS)


def _classify(payload: Any) -> SubjectClass:
    """Map one entity's P31 claims to a canonical type / index flag."""
    if not isinstance(payload, dict):
        return SubjectClass()
    claims = payload.get("claims")
    statements = claims.get("P31") if isinstance(claims, dict) else None
    if not isinstance(statements, list):
        return SubjectClass()

    found: list[str] = []
    is_index = False
    for statement in statements:
        mainsnak = statement.get("mainsnak") if isinstance(statement, dict) else None
        datavalue = mainsnak.get("datavalue") if isinstance(mainsnak, dict) else None
        value = datavalue.get("value") if isinstance(datavalue, dict) else None
        qid = None
        if isinstance(value, dict):
            qid = value.get("id")
        elif isinstance(value, str):
            qid = value
        if not isinstance(qid, str):
            continue
        key = qid.strip().upper()
        if key in _INDEX_PAGE_TYPES:
            is_index = True
            continue
        mapped = _INSTANCE_TYPES.get(key)
        if mapped:
            found.append(mapped)

    type_value = next(
        (candidate for candidate in _TYPE_PRECEDENCE if candidate in found), None
    )
    return SubjectClass(type=type_value, is_index=is_index)


def _parse_classes(payload: Any) -> dict[str, SubjectClass]:
    if not isinstance(payload, dict):
        return {}
    entities = payload.get("entities")
    if not isinstance(entities, dict):
        return {}
    result: dict[str, SubjectClass] = {}
    for qid, entity in entities.items():
        if str(qid).startswith("-"):
            # Placeholder for an id that does not exist.
            continue
        key = str(qid).upper()
        classified = _classify(entity)
        result[key] = SubjectClass(
            type=classified.type, is_index=classified.is_index, wikibase_item=key
        )
    return result


async def fetch_subject_classes(
    qids: Sequence[str],
    *,
    client: Optional[httpx.AsyncClient] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, SubjectClass]:
    """Classify Wikidata items (type + navigation-page flag).

    Ids that exist but carry no mapped ``instance of`` value come back as
    an untyped :class:`SubjectClass`. Any failure yields ``{}`` — topics
    then render without a type chip and without an index-page filter.
    """
    wanted: list[str] = []
    result: dict[str, SubjectClass] = {}
    for raw in qids:
        qid = str(raw or "").strip().upper()
        if not qid.startswith("Q") or qid in wanted:
            continue
        entry = _cache.get(qid)
        if entry is not None and time.monotonic() <= entry[0]:
            result[qid] = entry[1]
            continue
        wanted.append(qid)

    if not wanted:
        return result

    timeout = _timeout(environ)
    fetched: dict[str, SubjectClass] = {}
    for start in range(0, len(wanted), MAX_IDS):
        batch = wanted[start : start + MAX_IDS]
        payload = await _fetch_json(
            _request_url(batch), client=client, timeout=timeout
        )
        fetched.update(_parse_classes(payload))

    now = time.monotonic() + _CACHE_TTL_SECONDS
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        for stale in list(_cache)[: _CACHE_MAX_ENTRIES // 4]:
            _cache.pop(stale, None)
    for qid in wanted:
        value = fetched.get(qid, SubjectClass(wikibase_item=qid))
        _cache[qid] = (now, value)
        result[qid] = value
    return result
