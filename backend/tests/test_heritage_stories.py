"""Stories & Legends — API, provenance, versions and moderation tests.

These exercise the real migrated SQLite database and the real seed. Provider
calls are not involved: story discovery is curation, not generation, and the
question endpoint answers only from stored records.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def seeded_stories(auth_url):
    """Seed the curated sites and their stories into this test's database."""
    from app.db.session import session_scope
    from app.services.heritage_service import seed_heritage_sites
    from app.services.story_service import seed_heritage_stories

    async def _seed() -> tuple[int, int]:
        async with session_scope() as session:
            sites = await seed_heritage_sites(session)
            stories = await seed_heritage_stories(session)
        return sites, stories

    return asyncio.run(_seed())


def test_seed_loads_sourced_stories_for_ten_sites(auth_client, seeded_stories):
    sites, stories = seeded_stories
    assert sites == 67
    assert stories == 18

    body = auth_client.get("/api/heritage/sites/rani-ki-vav/stories").json()
    assert body["siteName"] == "Rani ki Vav"
    assert body["total"] == 2
    assert {story["category"] for story in body["stories"]} == {"history", "architecture"}
    history = next(s for s in body["stories"] if s["category"] == "history")
    assert history["narrativeType"] == "historically-documented"
    assert history["narrativeTypeLabel"] == "Historically documented"
    assert history["sourceCount"] >= 2
    assert history["language"] == "en"
    assert history["isCommunity"] is False


def test_story_detail_keeps_alternative_traditions_separate(auth_client, seeded_stories):
    response = auth_client.get("/api/heritage/stories/konark-origin")
    assert response.status_code == 200
    story = response.json()

    assert story["narrativeType"] == "conflicting-accounts"
    assert story["category"] == "mythology"
    assert len(story["versions"]) == 2
    labels = [version["label"] for version in story["versions"]]
    assert any("Temple tradition" in label for label in labels)
    assert any("Scholarly account" in label for label in labels)
    for version in story["versions"]:
        assert version["summary"]
        assert version["differences"]
        assert version["verificationStatus"] == "source-checked"
        assert version["sources"], version["id"]
    # The versions are not merged into one narrative or one source list.
    assert story["sources"]
    assert story["relatedStories"]


def test_story_sources_are_traceable_and_typed(auth_client, seeded_stories):
    story = auth_client.get("/api/heritage/stories/taj-mahal-black-taj").json()
    assert story["verificationStatus"] == "unverified"
    assert story["narrativeType"] == "local-folklore"
    source = story["sources"][0]
    assert source["url"].startswith("https://")
    assert source["title"]
    assert source["sourceType"] == "secondary-reference"
    assert source["licence"]


def test_category_and_language_filters_use_published_records(auth_client, seeded_stories):
    architecture = auth_client.get(
        "/api/heritage/sites/konark-sun-temple/stories", params={"category": "architecture"}
    ).json()
    assert architecture["total"] == 1
    assert architecture["stories"][0]["category"] == "architecture"

    english = auth_client.get(
        "/api/heritage/sites/rani-ki-vav/stories", params={"language": "en"}
    ).json()
    assert english["total"] == 2
    hindi = auth_client.get(
        "/api/heritage/sites/rani-ki-vav/stories", params={"language": "hi"}
    ).json()
    assert hindi["total"] == 0

    assert auth_client.get("/api/heritage/sites/not-a-site/stories").status_code == 404
    assert auth_client.get("/api/heritage/stories/not-a-story").status_code == 404


def test_map_payload_exposes_story_counts_and_story_filters(auth_client, seeded_stories):
    body = auth_client.get("/api/heritage/sites").json()
    assert body["summary"]["stories"] == 18
    assert body["summary"]["sitesWithStories"] == 10
    categories = {item["name"]: item["count"] for item in body["facets"]["storyCategories"]}
    assert categories["history"] >= 5
    assert categories["mythology"] == 2

    by_id = {site["id"]: site for site in body["sites"]}
    assert by_id["rani-ki-vav"]["storyCount"] == 2
    assert len(by_id["rani-ki-vav"]["featuredStories"]) == 2
    assert by_id["dholavira"]["storyCount"] == 0
    assert by_id["dholavira"]["featuredStories"] == []

    mythology = auth_client.get(
        "/api/heritage/sites", params={"story": "mythology"}
    ).json()
    assert {site["id"] for site in mythology["sites"]} == {
        "hampi",
        "konark-sun-temple",
    }
    legend = auth_client.get("/api/heritage/sites", params={"story": "local-legend"}).json()
    assert {site["id"] for site in legend["sites"]} == {"taj-mahal"}
    # A story filter composes with the national filters rather than replacing them.
    combined = auth_client.get(
        "/api/heritage/sites", params={"story": "history", "state": "Gujarat"}
    ).json()
    assert {site["id"] for site in combined["sites"]} == {
        "rani-ki-vav",
        "modhera-sun-temple",
    }


def test_grounded_question_uses_stored_stories_and_citations(auth_client, seeded_stories):
    response = auth_client.post(
        "/api/heritage/stories/ask",
        json={
            "siteId": "konark-sun-temple",
            "question": "Are there multiple versions of the origin story?",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "Two traditions" in body["answer"]
    assert body["matchedStories"][0]["id"] == "konark-origin"
    assert body["citations"]
    assert "whc.unesco.org" in " ".join(body["citations"])


def test_question_off_the_library_says_so_instead_of_inventing(auth_client, seeded_stories):
    body = auth_client.post(
        "/api/heritage/stories/ask",
        json={
            "siteId": "rani-ki-vav",
            "question": "What did the stepwell's chief mason eat for breakfast?",
        },
    ).json()
    assert body["matchedStories"] == []
    assert body["citations"] == []
    assert "do not address" in body["answer"]
    assert body["limitation"]

    empty = auth_client.post(
        "/api/heritage/stories/ask",
        json={"siteId": "dholavira", "question": "What legends are associated here?"},
    ).json()
    assert empty["matchedStories"] == []
    assert "No stories are stored" in empty["answer"]


def test_submission_requires_sign_in_consent_and_is_not_public(auth_client, client, seeded_stories):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    anonymous = TestClient(create_app(Settings.from_env()))
    payload = {
        "title": "A story told by my grandmother",
        "narrative": "She described the stepwell as a place where travellers rested and exchanged news.",
        "language": "gu",
        "community": "Patan",
        "tradition": "Family oral history",
        "sourceNote": "Interview with the contributor's grandmother, 1998.",
        "consent": True,
    }
    assert anonymous.post("/api/heritage/sites/rani-ki-vav/stories", json=payload).status_code == 401

    without_consent = dict(payload, consent=False)
    response = auth_client.post(
        "/api/heritage/sites/rani-ki-vav/stories", json=without_consent
    )
    assert response.status_code == 422

    created = auth_client.post("/api/heritage/sites/rani-ki-vav/stories", json=payload)
    assert created.status_code == 202, created.text
    story = created.json()["story"]
    assert created.json()["status"] == "pending"
    assert story["language"] == "gu"
    assert story["languageLabel"] == "Gujarati"
    assert story["narrativeType"] == "community-account"
    assert story["verificationStatus"] == "pending-review"
    assert story["isCommunity"] is True

    # Pending content is not in a public list, a public detail or the map counts.
    site_stories = auth_client.get("/api/heritage/sites/rani-ki-vav/stories").json()
    assert story["id"] not in {item["id"] for item in site_stories["stories"]}
    assert auth_client.get(f"/api/heritage/stories/{story['id']}").status_code == 404
    summary = auth_client.get("/api/heritage/sites").json()["summary"]
    assert summary["stories"] == 18


def test_moderation_is_allowlisted_and_publication_stays_a_community_account(
    auth_client, seeded_stories, monkeypatch
):
    payload = {
        "title": "The tank keeper's account",
        "narrative": "The contributor recalls the tank being cleaned by volunteers each summer.",
        "language": "en",
        "community": None,
        "tradition": None,
        "sourceNote": None,
        "consent": True,
    }
    story_id = auth_client.post(
        "/api/heritage/sites/modhera-sun-temple/stories", json=payload
    ).json()["story"]["id"]

    # No moderator allowlist configured: even an authenticated user is refused.
    monkeypatch.delenv("STORY_MODERATOR_EMAILS", raising=False)
    refused = auth_client.patch(
        f"/api/heritage/stories/{story_id}/moderation", json={"decision": "approve"}
    )
    assert refused.status_code == 403

    monkeypatch.setenv("STORY_MODERATOR_EMAILS", "test.user@example.org")
    approved = auth_client.patch(
        f"/api/heritage/stories/{story_id}/moderation", json={"decision": "approve"}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "published"

    published = auth_client.get(f"/api/heritage/stories/{story_id}").json()
    assert published["isCommunity"] is True
    assert published["narrativeType"] == "community-account"
    assert published["verificationStatus"] == "community-approved"
    assert published["community"] is None
    # Still not a historical claim, and still not "verified".
    assert published["narrativeType"] != "historically-documented"

    again = auth_client.patch(
        f"/api/heritage/stories/{story_id}/moderation", json={"decision": "reject"}
    )
    assert again.status_code == 409

    second = auth_client.post(
        "/api/heritage/sites/modhera-sun-temple/stories",
        json=dict(payload, title="A rejected account", narrative="A second contributor account that is not accepted."),
    ).json()["story"]["id"]
    rejected = auth_client.patch(
        f"/api/heritage/stories/{second}/moderation", json={"decision": "reject"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert auth_client.get(f"/api/heritage/stories/{second}").status_code == 404


def test_reseeding_is_idempotent_and_does_not_touch_community_rows(
    auth_client, seeded_stories, monkeypatch
):
    payload = {
        "title": "A local memory",
        "narrative": "A short community account used to prove reseeding does not delete submissions.",
        "language": "en",
        "community": None,
        "tradition": None,
        "sourceNote": None,
        "consent": True,
    }
    story_id = auth_client.post(
        "/api/heritage/sites/rani-ki-vav/stories", json=payload
    ).json()["story"]["id"]

    from app.db.session import session_scope
    from app.services.story_service import seed_heritage_stories

    async def _reseed() -> int:
        async with session_scope() as session:
            return await seed_heritage_stories(session)

    assert asyncio.run(_reseed()) == 18
    site_stories = auth_client.get("/api/heritage/sites/rani-ki-vav/stories").json()
    assert len(site_stories["stories"]) == 2  # the curated two, not the pending one
    assert story_id not in {item["id"] for item in site_stories["stories"]}


def test_seed_validator_rejects_unusable_story_data():
    from app.services.story_service import _seed_problem

    base = {
        "id": "test-story",
        "site_id": "rani-ki-vav",
        "title": "A title",
        "summary": "A summary",
        "narrative": "A narrative",
        "category": "history",
        "narrative_type": "historically-documented",
        "language": "en",
        "verification_status": "source-checked",
        "sources": [
            {"title": "Source", "source_type": "secondary-reference", "url": "https://example.org"}
        ],
    }
    seen: set[str] = set()
    assert _seed_problem(base, {"rani-ki-vav"}, seen) is None

    assert _seed_problem(dict(base, site_id="nowhere"), {"rani-ki-vav"}, set())
    assert _seed_problem(dict(base, category="legend"), {"rani-ki-vav"}, set())
    assert _seed_problem(dict(base, sources=[]), {"rani-ki-vav"}, set())
    assert _seed_problem(
        dict(base, sources=[{"title": "x", "source_type": "secondary-reference", "url": "http://x"}]),
        {"rani-ki-vav"},
        set(),
    )
    assert _seed_problem(
        dict(
            base,
            versions=[
                {"id": "v1", "label": "Only version", "summary": "s", "language": "en"}
            ],
        ),
        {"rani-ki-vav"},
        set(),
    )
