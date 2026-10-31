"""India heritage explorer — curated dataset, filters and approximate routing.

The map layer serves the curated, sourced demonstration dataset in
``database/seeds/heritage_sites.json``. The rules enforced here are the same
ones the Gujarat map followed, now applied nationally:

* a site without a real latitude/longitude is returned with ``null``
  coordinates and is never plotted at an invented position;
* coordinates carry their source, accuracy and verification status, so the
  UI labels them approximate rather than implying a survey;
* a site is only stored when its state is one of the 28 states or 8 Union
  Territories, its category is in the documented vocabulary, its period is
  either absent or in the documented buckets, and its coordinates fall
  inside India. Invalid seed data fails loudly at seed time instead of
  appearing on the map;
* the macro-region is derived from the state using :data:`STATE_TO_REGION`,
  so a record cannot claim to be in the wrong region.

Routing is honest about what it is: selected-order stops or a
nearest-neighbour ordering by great-circle distance, with straight-line leg
lengths. It does **not** claim road routing, opening hours, ticket prices or
travel times — none of those are known here, and inventing them would be
fabrication. The response states this explicitly.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..db.models import HeritageSite, HeritageStory
from ..schemas.story import STORY_CATEGORIES, STORY_CATEGORY_LABELS

logger = get_logger("heritage")

__all__ = [
    "seed_heritage_sites",
    "list_sites",
    "sites_payload",
    "heritage_facets",
    "heritage_summary",
    "get_sites_by_ids",
    "build_route",
    "haversine_km",
    "INDIA_STATES",
    "STATE_TO_REGION",
    "REGIONS",
    "CATEGORIES",
    "PERIOD_GROUPS",
    "ROUTE_NOTICE",
    "DATASET_LABEL",
]

#: backend/app/services -> backend -> repo root
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SEED_FILE = _REPO_ROOT / "database" / "seeds" / "heritage_sites.json"

DATASET_LABEL = "curated-demo-india"

#: 28 states and 8 Union Territories, with the documented macro-region.
#: Order is alphabetical; the API returns the same order to the filter.
_STATES: tuple[tuple[str, str, bool], ...] = (
    ("Andhra Pradesh", "South India", False),
    ("Arunachal Pradesh", "Northeast India", False),
    ("Assam", "Northeast India", False),
    ("Bihar", "East India", False),
    ("Chhattisgarh", "Central India", False),
    ("Goa", "West India", False),
    ("Gujarat", "West India", False),
    ("Haryana", "North India", False),
    ("Himachal Pradesh", "North India", False),
    ("Jharkhand", "East India", False),
    ("Karnataka", "South India", False),
    ("Kerala", "South India", False),
    ("Madhya Pradesh", "Central India", False),
    ("Maharashtra", "West India", False),
    ("Manipur", "Northeast India", False),
    ("Meghalaya", "Northeast India", False),
    ("Mizoram", "Northeast India", False),
    ("Nagaland", "Northeast India", False),
    ("Odisha", "East India", False),
    ("Punjab", "North India", False),
    ("Rajasthan", "North India", False),
    ("Sikkim", "Northeast India", False),
    ("Tamil Nadu", "South India", False),
    ("Telangana", "South India", False),
    ("Tripura", "Northeast India", False),
    ("Uttar Pradesh", "North India", False),
    ("Uttarakhand", "North India", False),
    ("West Bengal", "East India", False),
    ("Andaman and Nicobar Islands", "South India", True),
    ("Chandigarh", "North India", True),
    ("Dadra and Nagar Haveli and Daman and Diu", "West India", True),
    ("Delhi", "North India", True),
    ("Jammu and Kashmir", "North India", True),
    ("Ladakh", "North India", True),
    ("Lakshadweep", "South India", True),
    ("Puducherry", "South India", True),
)

#: ``(state, region, is_union_territory)`` — exported for the API filters.
INDIA_STATES = tuple((name, region, is_ut) for name, region, is_ut in _STATES)
STATE_TO_REGION: dict[str, str] = {name: region for name, region, _ in _STATES}
UNION_TERRITORIES = frozenset(name for name, _, is_ut in _STATES if is_ut)

REGIONS: tuple[str, ...] = (
    "North India",
    "South India",
    "East India",
    "West India",
    "Central India",
    "Northeast India",
)

CATEGORIES: tuple[str, ...] = (
    "Forts and Palaces",
    "Temples",
    "Mosques",
    "Churches",
    "Monasteries",
    "Stepwells",
    "Archaeological Sites",
    "Ancient Cities",
    "Caves",
    "Museums",
    "Memorials",
    "Cultural Landscapes",
    "Traditional Architecture",
    "Other Heritage Sites",
)

PERIOD_GROUPS: tuple[str, ...] = (
    "Prehistoric",
    "Ancient",
    "Early Medieval",
    "Medieval",
    "Early Modern",
    "Colonial",
    "Modern",
    "Contemporary",
)

ROUTE_NOTICE = (
    "This is an approximate heritage route built from straight-line "
    "(great-circle) distances between the selected published coordinates. It "
    "is not a road route and it contains no opening hours, travel times, "
    "ticket prices or accessibility information, because none of those are "
    "known for these sites here."
)

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: Rough national bounding box; a coordinate outside it is a data error.
_INDIA_BOUNDS = {"min_lat": 6.0, "max_lat": 37.0, "min_lon": 68.0, "max_lon": 98.0}


@dataclass(frozen=True)
class HeritageFilters:
    """One query against the curated dataset (all optional)."""

    q: Optional[str] = None
    state: Optional[str] = None
    region: Optional[str] = None
    category: Optional[str] = None
    period_group: Optional[str] = None
    unesco: Optional[bool] = None
    #: Only sites with a published story in this category.
    story_category: Optional[str] = None
    sort: str = "name"


def haversine_km(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    """Great-circle distance in kilometres."""
    radius = 6371.0088
    phi_a = math.radians(latitude_a)
    phi_b = math.radians(latitude_b)
    delta_phi = phi_b - phi_a
    delta_lambda = math.radians(longitude_b - longitude_a)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(min(1.0, math.sqrt(value)))


def _read_seed() -> tuple[str, list[dict]]:
    if not SEED_FILE.is_file():
        raise FileNotFoundError(f"Heritage seed file not found: {SEED_FILE}")
    payload = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    sites = payload.get("sites") or []
    if not isinstance(sites, list):
        raise ValueError("Heritage seed file is malformed: 'sites' must be a list.")
    return str(payload.get("dataset") or DATASET_LABEL), sites


def _seed_problem(item: dict) -> Optional[str]:
    """Return a human-readable problem with one seed record, or ``None``.

    This is the gate between a curated demo dataset and the map: a record
    that cannot prove its state, category, period bucket or coordinate is
    rejected at seed time rather than silently drawn somewhere plausible.
    """
    site_id = str(item.get("id") or "")
    if not _SLUG.match(site_id):
        return f"{site_id or '<missing id>'}: id must be a lowercase slug"
    name = str(item.get("name") or "").strip()
    if not name:
        return f"{site_id}: name is required"

    state = str(item.get("state") or "").strip()
    if state not in STATE_TO_REGION:
        return f"{site_id}: unknown state/UT {state!r}"
    if item.get("region") and item["region"] != STATE_TO_REGION[state]:
        return f"{site_id}: region {item['region']!r} does not match {state}"

    category = str(item.get("category") or "").strip()
    if category not in CATEGORIES:
        return f"{site_id}: category {category!r} is not in the documented vocabulary"

    period_group = item.get("period_group")
    if period_group not in (None, "") and period_group not in PERIOD_GROUPS:
        return f"{site_id}: period_group {period_group!r} is not documented"

    alternatives = item.get("alternative_names") or []
    if not isinstance(alternatives, list) or any(
        not isinstance(alias, str) or not alias.strip() for alias in alternatives
    ):
        return f"{site_id}: alternative_names must be a list of non-empty strings"

    refs = item.get("source_refs") or []
    if not isinstance(refs, list):
        return f"{site_id}: source_refs must be a list"
    for ref in refs:
        if not isinstance(ref, dict) or not str(ref.get("label") or "").strip():
            return f"{site_id}: every source reference needs a label"
        if not str(ref.get("url") or "").startswith("https://"):
            return f"{site_id}: every source reference needs an https URL"

    source_url = str(item.get("source_url") or "")
    if not source_url.startswith("https://"):
        return f"{site_id}: source_url must be an https URL"

    latitude, longitude = item.get("latitude"), item.get("longitude")
    if (latitude is None) != (longitude is None):
        return f"{site_id}: latitude and longitude must both be present or both absent"
    if latitude is not None:
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            return f"{site_id}: coordinates must be numbers"
        if latitude == 0 and longitude == 0:
            return f"{site_id}: 0,0 is a placeholder, not a coordinate"
        if not (
            _INDIA_BOUNDS["min_lat"] <= latitude <= _INDIA_BOUNDS["max_lat"]
            and _INDIA_BOUNDS["min_lon"] <= longitude <= _INDIA_BOUNDS["max_lon"]
        ):
            return f"{site_id}: coordinates fall outside India's bounding box"

    unesco = bool(item.get("unesco_status"))
    unesco_year = item.get("unesco_year")
    if unesco_year is not None and not unesco:
        return f"{site_id}: unesco_year is set but unesco_status is false"
    if unesco and not isinstance(unesco_year, int):
        return f"{site_id}: a UNESCO record needs the inscription year"
    return None


async def seed_heritage_sites(session: AsyncSession) -> int:
    """Insert or update the curated sites (idempotent). Returns rows touched.

    The whole seed is validated before anything is written; one malformed
    record fails the load with its id, so bad data never reaches the map.
    """
    dataset, sites = _read_seed()
    seen: set[str] = set()
    problems: list[str] = []
    for item in sites:
        site_id = str(item.get("id") or "")
        if site_id in seen:
            problems.append(f"{site_id}: duplicate id")
        seen.add(site_id)
        problem = _seed_problem(item)
        if problem:
            problems.append(problem)
    if problems:
        raise ValueError("Heritage seed is invalid: " + "; ".join(problems[:10]))

    touched = 0
    for item in sites:
        site = await session.get(HeritageSite, str(item["id"]))
        if site is None:
            site = HeritageSite(id=str(item["id"]))
            session.add(site)
        state = str(item["state"])
        site.name = str(item["name"]).strip()
        site.region = STATE_TO_REGION[state]
        site.state = state
        site.is_union_territory = state in UNION_TERRITORIES
        site.category = str(item["category"])
        site.period_group = item.get("period_group") or None
        site.period = item.get("period") or None
        site.alternative_names = list(item.get("alternative_names") or [])
        site.district = item.get("district") or None
        site.city = item.get("city") or None
        site.architectural_style = item.get("architectural_style") or None
        site.unesco_status = bool(item.get("unesco_status"))
        site.unesco_year = item.get("unesco_year") if site.unesco_status else None
        site.latitude = item.get("latitude")
        site.longitude = item.get("longitude")
        site.coordinate_source = item.get("coordinate_source") or (
            "Published coordinates (Wikipedia), rounded to 4 decimals"
        )
        site.coordinate_accuracy = item.get("coordinate_accuracy") or "approximate"
        site.verification_status = item.get("verification_status") or "unverified"
        site.description = item.get("description")
        site.source_url = item.get("source_url")
        site.source_refs = list(item.get("source_refs") or [])
        site.image_url = item.get("image_url")
        site.image_credit = item.get("image_credit")
        site.image_license = item.get("image_license")
        site.dataset = dataset
        touched += 1
    await session.flush()
    return touched


def _conditions(filters: HeritageFilters) -> list:
    conditions = []
    if filters.state:
        conditions.append(HeritageSite.state == filters.state)
    if filters.region:
        states = [name for name, region, _ in INDIA_STATES if region == filters.region]
        conditions.append(HeritageSite.state.in_(states))
    if filters.category:
        conditions.append(HeritageSite.category == filters.category)
    if filters.period_group:
        conditions.append(HeritageSite.period_group == filters.period_group)
    if filters.unesco is not None:
        conditions.append(HeritageSite.unesco_status.is_(filters.unesco))
    if filters.story_category:
        conditions.append(
            HeritageSite.id.in_(
                select(HeritageStory.site_id).where(
                    HeritageStory.status == "published",
                    HeritageStory.category == filters.story_category,
                )
            )
        )
    if filters.q:
        needle = f"%{filters.q.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(HeritageSite.name).like(needle),
                func.lower(func.coalesce(HeritageSite.city, "")).like(needle),
                func.lower(func.coalesce(HeritageSite.district, "")).like(needle),
                func.lower(HeritageSite.state).like(needle),
                func.lower(cast(HeritageSite.alternative_names, Text)).like(needle),
            )
        )
    return conditions


def _order_by(sort: str):
    if sort == "state":
        return (HeritageSite.state, HeritageSite.name)
    if sort == "category":
        return (HeritageSite.category, HeritageSite.name)
    if sort == "period":
        # The coarse bucket, then the free-text period, then the name. A
        # NULL period sorts last instead of pretending to be year zero.
        return (HeritageSite.period_group.is_(None), HeritageSite.period_group, HeritageSite.name)
    return (HeritageSite.name,)


async def list_sites(
    session: AsyncSession,
    *,
    filters: Optional[HeritageFilters] = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[HeritageSite], int]:
    """Return ``(page, filtered_total)`` for the curated dataset."""
    active = filters or HeritageFilters()
    conditions = _conditions(active)
    base = select(HeritageSite)
    if conditions:
        base = base.where(*conditions)
    total = int(
        (
            await session.scalar(
                select(func.count()).select_from(HeritageSite).where(*conditions)
                if conditions
                else select(func.count()).select_from(HeritageSite)
            )
        )
        or 0
    )
    rows = list(
        (
            await session.scalars(
                base.order_by(*_order_by(active.sort)).limit(limit).offset(offset)
            )
        ).all()
    )
    return rows, total


async def _all_rows(session: AsyncSession) -> list[HeritageSite]:
    return list((await session.scalars(select(HeritageSite))).all())


async def heritage_facets(session: AsyncSession) -> dict:
    """Filter options with real counts.

    Every state and Union Territory is listed even when it currently has no
    record, so the national filter is complete; categories and periods list
    only values that occur (the requirement is not to offer empty buckets).
    """
    rows = await _all_rows(session)
    state_counts: dict[str, int] = {}
    region_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    period_counts: dict[str, int] = {}
    for row in rows:
        state_counts[row.state] = state_counts.get(row.state, 0) + 1
        region_counts[row.region] = region_counts.get(row.region, 0) + 1
        category_counts[row.category] = category_counts.get(row.category, 0) + 1
        if row.period_group:
            period_counts[row.period_group] = period_counts.get(row.period_group, 0) + 1

    story_rows = await session.execute(
        select(HeritageStory.category, func.count(HeritageStory.site_id.distinct())).where(
            HeritageStory.status == "published"
        ).group_by(HeritageStory.category)
    )
    story_counts = {name: int(count) for name, count in story_rows}

    return {
        "states": [
            {
                "name": name,
                "region": region,
                "isUnionTerritory": is_ut,
                "count": state_counts.get(name, 0),
            }
            for name, region, is_ut in INDIA_STATES
        ],
        "regions": [
            {"name": region, "count": region_counts.get(region, 0)} for region in REGIONS
        ],
        "categories": [
            {"name": category, "count": category_counts[category]}
            for category in CATEGORIES
            if category_counts.get(category)
        ],
        "periods": [
            {"name": period, "count": period_counts[period]}
            for period in PERIOD_GROUPS
            if period_counts.get(period)
        ],
        "storyCategories": [
            {
                "name": category,
                "label": STORY_CATEGORY_LABELS[category],
                "count": story_counts[category],
            }
            for category in STORY_CATEGORIES
            if story_counts.get(category)
        ],
    }


async def heritage_summary(session: AsyncSession) -> dict:
    """Counts shown above the map — computed from rows, never hardcoded."""
    rows = await _all_rows(session)
    states = {row.state for row in rows}
    categories = {row.category for row in rows}
    story_total = int(
        (await session.scalar(select(func.count()).select_from(HeritageStory).where(
            HeritageStory.status == "published"
        )))
        or 0
    )
    sites_with_stories = int(
        (await session.scalar(
            select(func.count(HeritageStory.site_id.distinct())).where(
                HeritageStory.status == "published"
            )
        ))
        or 0
    )
    return {
        "totalSites": len(rows),
        "statesRepresented": len(states),
        "unionTerritoriesRepresented": len(states & UNION_TERRITORIES),
        "categories": len(categories),
        "verifiedRecords": sum(1 for row in rows if row.verification_status == "verified"),
        "approximateCoordinates": sum(
            1 for row in rows if row.coordinate_accuracy == "approximate"
        ),
        "unescoSites": sum(1 for row in rows if row.unesco_status),
        "stories": story_total,
        "sitesWithStories": sites_with_stories,
    }


async def get_sites_by_ids(
    session: AsyncSession, site_ids: Sequence[str]
) -> list[HeritageSite]:
    if not site_ids:
        return []
    rows = (
        await session.scalars(select(HeritageSite).where(HeritageSite.id.in_(list(site_ids))))
    ).all()
    order = {site_id: index for index, site_id in enumerate(site_ids)}
    return sorted(rows, key=lambda site: order.get(site.id, 10**9))


def _site_payload(
    site: HeritageSite,
    *,
    story_count: int = 0,
    featured: Optional[list] = None,
    story_categories: Optional[list[str]] = None,
) -> dict:
    refs = site.source_refs or []
    if not refs and site.source_url:
        refs = [{"label": "Source article", "url": site.source_url}]
    return {
        "id": site.id,
        "name": site.name,
        "alternativeNames": list(site.alternative_names or []),
        "region": site.region,
        "state": site.state,
        "isUnionTerritory": bool(site.is_union_territory),
        "district": site.district,
        "city": site.city,
        "category": site.category,
        "periodGroup": site.period_group,
        "period": site.period,
        "architecturalStyle": site.architectural_style,
        "unescoStatus": bool(site.unesco_status),
        "unescoYear": site.unesco_year,
        "latitude": site.latitude,
        "longitude": site.longitude,
        "coordinateSource": site.coordinate_source,
        "coordinateAccuracy": site.coordinate_accuracy,
        "verificationStatus": site.verification_status,
        "description": site.description,
        "sourceUrl": site.source_url,
        "sourceRefs": refs,
        "imageUrl": site.image_url,
        "imageCredit": site.image_credit,
        "imageLicense": site.image_license,
        "dataset": site.dataset,
        "storyCount": story_count,
        "storyCategories": story_categories or [],
        "featuredStories": featured or [],
    }


def sites_payload(
    sites: Iterable[HeritageSite],
    *,
    story_counts: Optional[dict[str, int]] = None,
    featured: Optional[dict[str, list]] = None,
    story_categories: Optional[dict[str, list[str]]] = None,
) -> list[dict]:
    counts = story_counts or {}
    headlines = featured or {}
    categories = story_categories or {}
    return [
        _site_payload(
            site,
            story_count=counts.get(site.id, 0),
            featured=headlines.get(site.id, []),
            story_categories=categories.get(site.id, []),
        )
        for site in sites
    ]


async def build_route(
    session: AsyncSession,
    *,
    site_ids: Sequence[str],
    start_site_id: Optional[str] = None,
    order: str = "distance",
) -> dict:
    """Order selected sites and return straight-line legs.

    ``order="selection"`` preserves the user's order (after dropping sites
    without coordinates); ``order="distance"`` anchors on the start site and
    repeatedly appends the nearest unvisited site. Both are deterministic and
    labelled approximate.
    """
    unique_ids: list[str] = []
    for site_id in site_ids:
        if site_id and site_id not in unique_ids:
            unique_ids.append(site_id)
    if len(unique_ids) < 2:
        raise ValueError("Select at least two heritage sites to build a route.")

    sites = await get_sites_by_ids(session, unique_ids)
    located = [site for site in sites if site.latitude is not None and site.longitude is not None]
    if len(located) < 2:
        raise ValueError(
            "At least two of the selected sites need published coordinates to "
            "build a route."
        )

    if order == "selection":
        ordered = located
    else:
        anchor_id = (
            start_site_id
            if start_site_id in {site.id for site in located}
            else located[0].id
        )
        remaining = [site for site in located if site.id != anchor_id]
        ordered = [next(site for site in located if site.id == anchor_id)]
        current = ordered[0]
        while remaining:
            nearest = min(
                remaining,
                key=lambda site: haversine_km(
                    current.latitude, current.longitude, site.latitude, site.longitude
                ),
            )
            ordered.append(nearest)
            remaining.remove(nearest)
            current = nearest

    legs: list[dict] = []
    total = 0.0
    for previous, current in zip(ordered, ordered[1:]):
        distance = haversine_km(
            previous.latitude, previous.longitude, current.latitude, current.longitude
        )
        legs.append(
            {
                "from": previous.id,
                "to": current.id,
                "distanceKm": round(distance, 1),
            }
        )
        total += distance

    return {
        "stops": sites_payload(ordered),
        "legs": legs,
        "totalDistanceKm": round(total, 1),
        "distanceMethod": "great-circle",
        "ordering": "selection" if order == "selection" else "distance",
        "notice": ROUTE_NOTICE,
        "excludedSiteIds": [
            site.id for site in sites if site.latitude is None or site.longitude is None
        ],
    }
