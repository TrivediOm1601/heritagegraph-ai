"""Data-quality tests for the India Heritage Explorer dataset.

The curated map is only as trustworthy as its seed. These tests run the same
validation the seeder uses, then assert the national coverage, the documented
region mapping and the honest provenance (all coordinates approximate, no
record claimed as verified).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services.heritage_service import (
    CATEGORIES,
    INDIA_STATES,
    PERIOD_GROUPS,
    REGIONS,
    STATE_TO_REGION,
    SEED_FILE,
    UNION_TERRITORIES,
    _seed_problem,
    heritage_facets,
    heritage_summary,
    seed_heritage_sites,
)


def load_seed() -> dict:
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


def test_seed_covers_every_state_and_union_territory():
    payload = load_seed()
    sites = payload["sites"]

    assert payload["dataset"] == "curated-demo-india"
    assert len(sites) == 67
    assert len(INDIA_STATES) == 36
    assert len({name for name, _, _ in INDIA_STATES}) == 36
    assert len(UNION_TERRITORIES) == 8

    states = {site["state"] for site in sites}
    assert states == {name for name, _, _ in INDIA_STATES}
    # A representative record in each macro-region.
    assert {STATE_TO_REGION[state] for state in states} == set(REGIONS)


def test_every_seed_record_is_valid_and_honestly_labelled():
    for site in load_seed()["sites"]:
        assert _seed_problem(site) is None, site["id"]
        assert site["category"] in CATEGORIES
        assert site["period_group"] in (None, *PERIOD_GROUPS)
        assert site["coordinate_accuracy"] == "approximate"
        assert site["verification_status"] == "unverified"
        assert site["source_url"].startswith("https://")
        # Published coordinates are present for every record in this pass.
        assert isinstance(site["latitude"], float)
        assert isinstance(site["longitude"], float)


def test_original_gujarat_records_remain():
    by_id = {site["id"]: site for site in load_seed()["sites"]}
    for site_id in (
        "rani-ki-vav",
        "adalaj-stepwell",
        "modhera-sun-temple",
        "champaner-pavagadh",
        "dholavira",
    ):
        assert site_id in by_id
        assert by_id[site_id]["state"] == "Gujarat"


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ({"state": "Atlantis"}, "unknown state"),
        ({"region": "South India"}, "does not match"),
        ({"category": "Spaceport"}, "not in the documented vocabulary"),
        ({"period_group": "Renaissance"}, "not documented"),
        ({"latitude": 48.8, "longitude": 2.3}, "outside India"),
        ({"latitude": 0, "longitude": 0}, "placeholder"),
        ({"latitude": 27.1, "longitude": None}, "both be present"),
        ({"source_url": "http://example.com"}, "https URL"),
        ({"unesco_status": False, "unesco_year": 1983}, "unesco_year is set"),
        ({"unesco_status": True, "unesco_year": None}, "inscription year"),
        ({"alternative_names": "not-a-list"}, "alternative_names"),
    ],
)
def test_invalid_seed_records_are_rejected(mutation, expected):
    site = dict(load_seed()["sites"][0])
    site.update(mutation)
    problem = _seed_problem(site)
    assert problem is not None
    assert expected in problem


def test_duplicate_ids_are_rejected_by_the_seeder(auth_url):
    """The seeder validates the whole file before writing anything."""
    from app.db.session import session_scope

    payload = load_seed()
    duplicate = dict(payload["sites"][0])

    async def _seed_with_duplicate() -> None:
        from app.services import heritage_service

        original = heritage_service._read_seed
        try:
            heritage_service._read_seed = lambda: (
                payload["dataset"],
                payload["sites"] + [duplicate],
            )
            async with session_scope() as session:
                await seed_heritage_sites(session)
        finally:
            heritage_service._read_seed = original

    with pytest.raises(ValueError, match="duplicate id"):
        asyncio.run(_seed_with_duplicate())


def test_facets_and_summary_match_the_seeded_rows(auth_url):
    from app.db.session import session_scope

    async def _run():
        async with session_scope() as session:
            await seed_heritage_sites(session)
            return await heritage_facets(session), await heritage_summary(session)

    facets, summary = asyncio.run(_run())

    assert len(facets["states"]) == 36
    assert all(item["count"] >= 1 for item in facets["states"])
    assert sum(1 for item in facets["states"] if item["isUnionTerritory"]) == 8
    assert {item["name"] for item in facets["regions"]} == set(REGIONS)
    assert all(item["count"] > 0 for item in facets["categories"])
    assert all(item["count"] > 0 for item in facets["periods"])
    # Only categories that actually occur are offered.
    assert {item["name"] for item in facets["categories"]} == set(CATEGORIES)

    assert summary["totalSites"] == 67
    assert summary["statesRepresented"] == 36
    assert summary["unionTerritoriesRepresented"] == 8
    assert summary["verifiedRecords"] == 0
    assert summary["approximateCoordinates"] == 67
    assert summary["unescoSites"] == 29
    assert summary["categories"] == len(CATEGORIES)


def test_region_mapping_has_no_orphans():
    assert set(STATE_TO_REGION) == {name for name, _, _ in INDIA_STATES}
    assert set(STATE_TO_REGION.values()) == set(REGIONS)
    assert len(STATE_TO_REGION) == 36
