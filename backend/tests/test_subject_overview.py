"""Repair R6 — the subject overview: photograph, type, related topics.

Covers the two features the search page gained:

* a **subject profile** (name, canonical type, the source's own one-line
  description and a real photograph with license/credit);
* **related topics with identity** — each retrieved topic becomes a card
  with a stable id, a canonical type, a description, a thumbnail and the
  exact query that opens it, and navigation pages (disambiguation/list
  pages) are excluded because they are not subjects.

Transport is a fixture; nothing here dials out. The tests also pin the
rule that with live sources *off* no media request is made at all.
"""

from __future__ import annotations

import httpx
import pytest

from app.db.models import HistoricalRecord
from app.services import briefing_service
from app.services.briefing_service import build_briefing
from app.services.retrieval_service import RetrievedRecord

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clear_caches():
    from app.integrations.wikipedia import clear_cache as clear_wikipedia
    from app.integrations.wikidata import clear_cache as clear_wikidata
    from app.integrations.wikimedia_media import clear_cache as clear_media

    clear_wikipedia()
    clear_wikidata()
    clear_media()
    yield
    clear_wikipedia()
    clear_wikidata()
    clear_media()


MODI_PAGE = (
    "Narendra Modi is an Indian politician who has served as the Prime Minister of "
    "India since 2014. He was the Chief Minister of Gujarat from 2001 to 2014."
)

ADMIN_PAGE = (
    "Lalit Modi is an Indian cricket administrator and businessman. He was the "
    "first chairman of the Indian Premier League."
)


def _hit(title: str, *, page_id: str, score: float, content: str = "") -> RetrievedRecord:
    return RetrievedRecord(
        record=HistoricalRecord(
            id=f"wiki:{page_id}",
            title=title,
            source="Wikipedia",
            url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
            content=content,
        ),
        excerpt=content or None,
        score=score,
    )


def _page(title: str, text: str) -> dict:
    return {
        "query": {
            "pages": [
                {
                    "pageid": 1,
                    "title": title,
                    "canonicalurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    "extract": text,
                }
            ]
        }
    }


def _media_page(
    title: str,
    *,
    page_id: int,
    description: str | None,
    thumb: str | None,
    file_name: str | None = None,
    wikibase: str | None = None,
) -> dict:
    page: dict = {
        "pageid": page_id,
        "title": title,
        "canonicalurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
    }
    if thumb:
        page["thumbnail"] = {"source": thumb}
    if file_name:
        page["pageimage"] = file_name
    if description:
        page["terms"] = {"description": [description]}
    if wikibase:
        page["pageprops"] = {"wikibase_item": wikibase}
    return page


MODI_THUMB = "https://upload.wikimedia.org/modi.jpg"
LALIT_THUMB = "https://upload.wikimedia.org/lalit.jpg"


def _transport(*, media_titles: dict[str, dict], classes: dict[str, list[str]]):
    """One transport serving all four source calls."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        params = dict(httpx.QueryParams(request.url.query))
        titles = params.get("titles", "")

        if "wikidata.org" in url:
            ids = [qid.strip() for qid in params.get("ids", "").split("|") if qid.strip()]
            entities = {
                qid: {
                    "claims": {
                        "P31": [
                            {"mainsnak": {"datavalue": {"value": {"id": value}}}}
                            for value in classes.get(qid, [])
                        ]
                    }
                }
                for qid in ids
            }
            return httpx.Response(200, json={"entities": entities})

        if "commons.wikimedia.org" in url:
            # The real API answers one page per requested file, echoing the
            # file title — the fixture must do the same or it would test a
            # shape the source never produces.
            files = [token.strip() for token in titles.split("|") if token.strip()]
            pages = [
                {
                    "title": file_title,
                    "imageinfo": [
                        {
                            "descriptionurl": "https://commons.wikimedia.org/wiki/"
                            + file_title.replace(" ", "_"),
                            "extmetadata": {
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                "Artist": {"value": "A Photographer"},
                            },
                        }
                    ],
                }
                for file_title in files
            ]
            return httpx.Response(200, json={"query": {"pages": pages}})

        # Article requests: the full-page call carries ``explaintext``; the
        # media call carries ``pageimages``.
        if "pageimages" in params.get("prop", ""):
            wanted = [token.strip() for token in titles.split("|") if token.strip()]
            pages = [
                media_titles[title]
                for title in wanted
                if title in media_titles
            ] or [{"missing": True}]
            return httpx.Response(200, json={"query": {"pages": pages}})

        wanted = [token.strip() for token in titles.split("|") if token.strip()]
        key = wanted[0] if wanted else titles
        if not key:
            return httpx.Response(200, json={"query": {"pages": [{"missing": True}]}})
        # Any other title resolves to a page too: the point of these tests is
        # the media/topic layer, not which article the source returns.
        text = ADMIN_PAGE if key == "Lalit Modi" else MODI_PAGE
        return httpx.Response(200, json=_page(key, text))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_subject_profile_carries_type_description_and_photograph():
    hits = [
        _hit("Narendra Modi", page_id="444222", score=0.95, content=MODI_PAGE),
        _hit("Lalit Modi", page_id="15938793", score=0.7, content=ADMIN_PAGE),
    ]
    client = _transport(
        media_titles={
            "Narendra Modi": _media_page(
                "Narendra Modi",
                page_id=444222,
                description="Prime Minister of India since 2014",
                thumb=MODI_THUMB,
                file_name="Modi.jpg",
                wikibase="Q1058",
            ),
            "Lalit Modi": _media_page(
                "Lalit Modi",
                page_id=15938793,
                description="Indian cricket administrator and businessman",
                thumb=LALIT_THUMB,
                file_name="Lalit.jpg",
                wikibase="Q3241458",
            ),
        },
        classes={"Q1058": ["Q5"], "Q3241458": ["Q5"]},
    )

    briefing = await build_briefing(
        "Narendra Modi", hits, client=client, environ={"HG_LIVE_SOURCES": "1"}
    )

    assert briefing is not None
    subject = briefing.subject
    assert subject is not None
    assert subject.name == "Narendra Modi"
    assert subject.type == "person"
    assert subject.description == "Prime Minister of India since 2014"
    assert subject.image is not None
    assert subject.image.url == MODI_THUMB
    assert subject.image.license == "CC BY-SA 4.0"
    assert subject.image.credit == "A Photographer"
    assert subject.image.file_page_url.startswith("https://commons.wikimedia.org/wiki/File:")
    # Accessible label is built from source text, never from the filename.
    assert "Narendra Modi" in (subject.image.alt_text or "")


async def test_related_topics_carry_identity_type_description_and_query():
    hits = [
        _hit("Narendra Modi", page_id="444222", score=0.95, content=MODI_PAGE),
        _hit("Lalit Modi", page_id="15938793", score=0.7, content=ADMIN_PAGE),
    ]
    client = _transport(
        media_titles={
            "Narendra Modi": _media_page(
                "Narendra Modi",
                page_id=444222,
                description="Prime Minister of India since 2014",
                thumb=MODI_THUMB,
                wikibase="Q1058",
            ),
            "Lalit Modi": _media_page(
                "Lalit Modi",
                page_id=15938793,
                description="Indian cricket administrator and businessman",
                thumb=LALIT_THUMB,
                wikibase="Q3241458",
            ),
        },
        classes={"Q1058": ["Q5"], "Q3241458": ["Q5"]},
    )

    briefing = await build_briefing(
        "Narendra Modi", hits, client=client, environ={"HG_LIVE_SOURCES": "1"}
    )

    assert briefing is not None
    topics = briefing.related_topics
    assert [topic.name for topic in topics] == ["Lalit Modi"]
    topic = topics[0]
    # A stable identity and the exact query that opens it — not a label.
    assert topic.id == "wiki:15938793"
    assert topic.query == "Lalit Modi"
    assert topic.type == "person"
    assert topic.description == "Indian cricket administrator and businessman"
    assert topic.source_url == "https://en.wikipedia.org/wiki/Lalit_Modi"
    assert topic.image is not None and topic.image.url == LALIT_THUMB
    # The legacy title list is unchanged for callers that still read it.
    assert briefing.related == ["Lalit Modi"]


async def test_disambiguation_pages_never_become_topic_cards():
    hits = [
        _hit("Narendra Modi", page_id="444222", score=0.95, content=MODI_PAGE),
        _hit("Modi ministry", page_id="60898293", score=0.6, content=""),
        _hit("Modi (disambiguation)", page_id="123", score=0.5, content=""),
    ]
    client = _transport(
        media_titles={
            "Narendra Modi": _media_page(
                "Narendra Modi",
                page_id=444222,
                description="Prime Minister of India since 2014",
                thumb=MODI_THUMB,
                wikibase="Q1058",
            ),
            "Modi ministry": _media_page(
                "Modi ministry",
                page_id=60898293,
                description="Wikimedia disambiguation page",
                thumb=None,
                wikibase="Q64342025",
            ),
            "Modi (disambiguation)": _media_page(
                "Modi (disambiguation)",
                page_id=123,
                description=None,
                thumb=None,
                wikibase=None,
            ),
        },
        classes={"Q1058": ["Q5"], "Q64342025": ["Q4167410"]},
    )

    briefing = await build_briefing(
        "Narendra Modi", hits, client=client, environ={"HG_LIVE_SOURCES": "1"}
    )

    assert briefing is not None
    assert [topic.name for topic in briefing.related_topics] == []
    # …while the quoted sources still list them, as before.
    assert "Modi ministry" in briefing.related


async def test_topic_without_a_classification_is_untyped_never_guessed():
    hits = [
        _hit("Narendra Modi", page_id="444222", score=0.95, content=MODI_PAGE),
        _hit("Jashodaben Modi", page_id="55", score=0.6, content=""),
    ]
    client = _transport(
        media_titles={
            "Narendra Modi": _media_page(
                "Narendra Modi",
                page_id=444222,
                description="Prime Minister of India since 2014",
                thumb=MODI_THUMB,
                wikibase="Q1058",
            ),
            "Jashodaben Modi": _media_page(
                "Jashodaben Modi",
                page_id=55,
                description=None,
                thumb=None,
                wikibase=None,
            ),
        },
        classes={"Q1058": ["Q5"]},
    )

    briefing = await build_briefing(
        "Narendra Modi", hits, client=client, environ={"HG_LIVE_SOURCES": "1"}
    )

    assert briefing is not None
    topic = briefing.related_topics[0]
    assert topic.type is None
    assert topic.description is None
    assert topic.image is None


async def test_live_sources_off_means_no_media_request_at_all():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    hits = [_hit("Rani ki Vav", page_id="17834167", score=0.9, content=MODI_PAGE)]

    briefing = await build_briefing("Rani ki Vav", hits, client=client, environ={})

    assert briefing is not None
    assert calls == []
    assert briefing.subject is not None
    assert briefing.subject.name == "Rani ki Vav"
    assert briefing.subject.image is None
    assert briefing.related_topics == []


async def test_media_outage_leaves_the_briefing_intact_without_images():
    hits = [
        _hit("Narendra Modi", page_id="444222", score=0.95, content=MODI_PAGE),
        _hit("Lalit Modi", page_id="15938793", score=0.7, content=ADMIN_PAGE),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "wikidata.org" in str(request.url):
            return httpx.Response(429, json={"error": "too many"})
        return httpx.Response(503, text="unavailable")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    briefing = await build_briefing(
        "Narendra Modi", hits, client=client, environ={"HG_LIVE_SOURCES": "1"}
    )

    assert briefing is not None
    assert briefing.summary  # the answer survives an image outage
    assert briefing.subject is not None and briefing.subject.image is None
    assert [topic.name for topic in briefing.related_topics] == ["Lalit Modi"]
    assert briefing.related_topics[0].image is None
    assert briefing.related_topics[0].type is None


async def test_two_subjects_do_not_share_images_or_descriptions():
    """The regression the PDF shows: one subject's data under another name."""

    async def run(query: str, expected_thumb: str, other_thumb: str):
        hits = [
            _hit(query, page_id="1", score=0.9, content=MODI_PAGE),
            _hit("Lalit Modi", page_id="15938793", score=0.5, content=ADMIN_PAGE),
        ]
        client = _transport(
            media_titles={
                query: _media_page(
                    query, page_id=1, description=f"about {query}", thumb=expected_thumb,
                    wikibase="Q1058",
                ),
                "Lalit Modi": _media_page(
                    "Lalit Modi", page_id=15938793, description="cricket administrator",
                    thumb=other_thumb, wikibase="Q3241458",
                ),
            },
            classes={"Q1058": ["Q5"], "Q3241458": ["Q5"]},
        )
        # Both subjects must exist as pages, otherwise the briefing would
        # legitimately fall back to the other retrieved record.
        briefing = await build_briefing(
            query, hits, client=client, environ={"HG_LIVE_SOURCES": "1"}
        )
        return briefing

    first = await run("Narendra Modi", MODI_THUMB, LALIT_THUMB)
    second = await run("Ashoka", "https://upload.wikimedia.org/ashoka.jpg", LALIT_THUMB)

    assert first.subject.image.url == MODI_THUMB
    assert second.subject.image.url.endswith("ashoka.jpg")
    assert first.subject.description == "about Narendra Modi"
    assert second.subject.description == "about Ashoka"
    assert first.subject.name != second.subject.name


def test_index_description_markers_are_recognised():
    from app.integrations.wikidata import is_index_description

    assert is_index_description("Wikimedia disambiguation page")
    assert is_index_description("Wikimedia list article")
    assert not is_index_description("Prime Minister of India since 2014")
    assert not is_index_description(None)


def test_briefing_service_exposes_the_related_topic_cap():
    assert briefing_service.MAX_RELATED_TOPICS >= 1
