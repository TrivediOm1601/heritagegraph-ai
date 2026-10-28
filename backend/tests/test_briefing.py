"""C3 tests — grounded historical briefings.

Two layers, deliberately separated:

* **pure** — query classification, relevance labels, section/entry
  parsing. No I/O, no database.
* **service** — ``build_briefing`` over real ``RetrievedRecord`` objects.
  The *source* is a fixture (``httpx.MockTransport`` — zero egress, same
  seam as ``test_providers.py``/``test_live_sources.py``), so these tests
  prove the briefing is assembled from the material it was given and
  never from a model's recollection.

The anti-fabrication assertion to look for: every quoted fact must be a
substring of the source document it cites. If a future change starts
paraphrasing or generating, that assertion fails.
"""

from __future__ import annotations

import httpx
import pytest

from app.db.models import HistoricalRecord
from app.services import briefing_service
from app.services.briefing_service import (
    build_briefing,
    classify_query,
    parse_sections,
    query_year,
)
from app.services.retrieval_service import (
    RetrievedRecord,
    normalize_query,
    relevance_label,
    search_historical_records,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

YEAR_PAGE = """1946 (MCMXLVI) was a common year starting on Tuesday of the Gregorian calendar.

== Events ==

=== January ===

January 6 – The first general election ever in Vietnam is held.
January 7 – The Allies of World War II recognize the Austrian republic.
January 10
The first meeting of the United Nations is held in London.
Project Diana bounces radar waves off the Moon.
January 11 – Enver Hoxha declares the People's Republic of Albania.

== Births ==

January 1 – Alfonso Caruana, Italian businessman
January 5 – Diane Keaton, American actress

== Deaths ==

January 9 – Countee Cullen, American poet
"""

PLACE_PAGE = """Rani ki Vav is a stepwell situated in the town of Patan in Gujarat, India.
Its construction is attributed to Udayamati, wife of the 11th-century Chaulukya king Bhima I.
It has been listed as a UNESCO World Heritage Site since 2014.

== History ==

Rani ki Vav was constructed during the rule of the Chaulukya dynasty.
The city was sacked between 1200 and 1210 by Qutb-ud-din Aibak.
"""


def _page_payload(title: str, text: str) -> dict:
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


def _live_hit(
    title: str,
    *,
    page_id: str = "1",
    intro: str = "",
    score: float = 0.98,
) -> RetrievedRecord:
    return RetrievedRecord(
        record=HistoricalRecord(
            id=f"wiki:{page_id}",
            title=title,
            year=None,
            source="Wikipedia",
            url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
            content=intro,
        ),
        excerpt=intro or None,
        score=score,
    )


def _local_hit(record_id: str, title: str, content: str, *, score: float = 0.9) -> RetrievedRecord:
    return RetrievedRecord(
        record=HistoricalRecord(
            id=record_id,
            title=title,
            year="1843",
            source="Science Museum",
            url=f"https://example.org/{record_id}",
            content=content,
        ),
        excerpt=content[:80],
        score=score,
    )


def _mock_client(*pages: tuple[str, str]) -> httpx.AsyncClient:
    """Fixture transport answering one page payload per requested title."""
    lookup = {title.casefold(): text for title, text in pages}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(httpx.QueryParams(request.url.query))
        title = params.get("titles", "")
        text = lookup.get(title.casefold())
        if text is None:
            return httpx.Response(200, json={"query": {"pages": [{"missing": True}]}})
        return httpx.Response(200, json=_page_payload(title, text))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _clear_source_cache():
    """The page cache is process-global; never leak a fixture across tests."""
    from app.integrations.wikipedia import clear_cache

    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# Pure behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "kind"),
    [
        ("1946", "year"),
        ("In 1946", "year"),
        ("1857", "year"),
        ("Rani ki Vav", "named"),
        ("Rani Lakshmibai", "named"),
        ("Indian independence", "named"),
        ("1946-1950", "general"),
        ("123", "general"),  # too short to be a historical year
        ("", "general"),
    ],
)
def test_query_classification(query, kind):
    assert classify_query(query) == kind


def test_year_extraction_and_short_numbers():
    assert query_year("1946") == 1946
    assert query_year("c. 1857") == 1857
    assert query_year("123") is None
    assert query_year("Rani ki Vav") is None


def test_relevance_labels_are_coarse_not_false_precision():
    assert relevance_label(0.98) == "high"
    assert relevance_label(0.75) == "high"
    assert relevance_label(0.5) == "medium"
    assert relevance_label(0.45) == "medium"
    assert relevance_label(0.2) == "low"
    assert relevance_label(None) is None


def test_sections_are_split_at_headings_and_keep_subsections():
    preamble, sections = parse_sections(YEAR_PAGE)

    assert preamble.startswith("1946 (MCMXLVI)")
    headings = [section.heading for section in sections]
    assert headings == ["Events", "January", "Births", "Deaths"]
    events = briefing_service._section_text(sections, {"events"})
    # Subsections belong to their parent section.
    assert "first general election ever in Vietnam" in events
    assert "Enver Hoxha" in events
    assert "Countee Cullen" not in events  # Deaths, not Events


def test_bare_date_line_starts_a_new_event_instead_of_merging():
    """Regression: the first live ``1946`` run glued the events following a
    bare ``January 10`` line onto the previous (``January 7``) entry."""
    _, sections = parse_sections(YEAR_PAGE)
    entries = briefing_service._entries(
        briefing_service._section_text(sections, {"events"}), limit=8
    )

    dates = [date for date, _ in entries]
    assert dates == [
        "January 6",
        "January 7",
        "January 10",
        "January 11",
    ]
    by_date = dict(entries)
    assert "Austrian republic" in by_date["January 7"]
    assert "United Nations" in by_date["January 10"]
    assert "Project Diana" in by_date["January 10"]
    assert "Hoxha" in by_date["January 11"]


# ---------------------------------------------------------------------------
# Briefing over local (offline) records
# ---------------------------------------------------------------------------


async def test_no_hits_means_no_briefing():
    assert await build_briefing("anything", []) is None


async def test_briefing_quotes_local_records_and_cites_them():
    hits = [
        _local_hit(
            "rec-ada-lovelace",
            "Ada Lovelace",
            "Augusta Ada King was an English mathematician. She wrote the first "
            "algorithm intended for the Analytical Engine. She died in 1852.",
        )
    ]

    briefing = await build_briefing("Ada Lovelace", hits)

    assert briefing is not None
    assert briefing.method == "extractive-quotes"
    assert briefing.query_kind == "named"
    assert briefing.summary and "English mathematician" in briefing.summary
    assert briefing.explanation and "Analytical Engine" in briefing.explanation
    assert briefing.key_facts and all(fact.source_index == 1 for fact in briefing.key_facts)
    # Every quoted fact is literally in the cited source.
    for fact in briefing.key_facts:
        assert fact.text in hits[0].record.content
    # Offline retrieval must say so, never pretend to be exhaustive.
    assert any("HG_LIVE_SOURCES" in note for note in briefing.uncertainty)


async def test_unrelated_query_with_no_source_text_is_honest():
    """A hit whose record carries no text cannot become an explanation."""
    hit = RetrievedRecord(
        record=HistoricalRecord(id="rec-empty", title="Untitled", source="Archive", content=""),
        excerpt=None,
        score=0.4,
    )

    briefing = await build_briefing("Untitled", [hit])

    assert briefing is not None
    assert briefing.summary is None
    assert briefing.explanation is None
    assert any("No source text" in note for note in briefing.uncertainty)


# ---------------------------------------------------------------------------
# Briefing over a live source (fixture transport)
# ---------------------------------------------------------------------------


async def test_year_query_builds_a_dated_timeline_and_people_list():
    hits = [_live_hit("1946", page_id="1946")]
    client = _mock_client(("1946", YEAR_PAGE))

    briefing = await build_briefing(
        "1946", hits, client=client, environ={"HG_LIVE_SOURCES": "1"}
    )

    assert briefing is not None
    assert briefing.query_kind == "year"
    assert [entry.date for entry in briefing.timeline] == [
        "January 6",
        "January 7",
        "January 10",
        "January 11",
    ]
    assert all(entry.source_index == 1 for entry in briefing.timeline)
    assert {entry.source_index for entry in briefing.timeline} <= {
        source.index for source in briefing.sources
    }
    # Births + Deaths become the people list, with the source's own dates
    # and an explicit born/died label (the two sections are otherwise
    # indistinguishable in one list).
    assert "January 1 (born): Alfonso Caruana" in briefing.people
    assert "January 9 (died): Countee Cullen" in briefing.people
    # Facts are quoted: after whitespace normalisation (a date line's two
    # source lines are joined), every word still comes from the source.
    normalized_page = " ".join(YEAR_PAGE.split())
    for entry in briefing.timeline:
        assert " ".join(entry.description.split()) in normalized_page
    # The year's own lead is offered as context, not as the whole answer.
    assert briefing.explanation and briefing.explanation.startswith("1946 (MCMXLVI)")


async def test_named_query_explanation_uses_the_lead_and_never_leaks_headings():
    hits = [_live_hit("Rani ki Vav", page_id="2")]
    client = _mock_client(("Rani ki Vav", PLACE_PAGE))

    briefing = await build_briefing(
        "Rani ki Vav", hits, client=client, environ={"HG_LIVE_SOURCES": "1"}
    )

    assert briefing is not None
    assert "stepwell" in (briefing.summary or "")
    assert "UNESCO World Heritage Site" in (briefing.explanation or "")
    assert "== History ==" not in (briefing.explanation or "")
    assert "==" not in (briefing.explanation or "")
    for fact in briefing.key_facts:
        assert fact.text in PLACE_PAGE
    # Dated sentences from the article become a timeline with year labels.
    assert {entry.date for entry in briefing.timeline} >= {"2014", "1200"}
    assert briefing.people == []  # no Births/Deaths section: nothing invented


async def test_query_title_page_wins_over_a_badly_ranked_search_hit():
    """Live defect found in the R6 run: searching "Qutub Minar" ranked
    "Mini Qutub Minar" first, so the briefing described a different tower.
    The page named by the *query* is used (MediaWiki follows redirects)."""
    qutb = (
        "Qutb Minar is a minaret and victory tower that forms part of the "
        "Qutb complex in Delhi, India. It was built in 1193 by Qutb ud-Din Aibak.\n"
    )
    mini = (
        "Mini Qutub Minar is a minaret tower in Hastsal village near Uttam Nagar. "
        "It was built in 1650 by the Mughal emperor Shah Jahan.\n"
    )
    hits = [
        _live_hit("Mini Qutub Minar", page_id="m1", intro="Mini tower", score=0.89),
        _live_hit("Qutb Minar", page_id="q1", intro="Minaret in Delhi", score=0.62),
    ]
    # The provider resolves the query's own title to the canonical article.
    client = _mock_client(("Qutub Minar", qutb), ("Mini Qutub Minar", mini))

    briefing = await build_briefing(
        "Qutub Minar", hits, client=client, environ={"HG_LIVE_SOURCES": "1"}
    )

    assert briefing is not None
    assert briefing.sources[0].index == 1
    assert briefing.sources[0].title == "Qutub Minar"
    assert "victory tower" in (briefing.summary or "")
    assert "Hastsal" not in (briefing.summary or "")
    # The mis-ranked hit is still listed, as context.
    assert "Mini Qutub Minar" in briefing.related


async def test_disambiguation_page_is_skipped_for_a_specific_article():
    """Live defect found in the R6 run: "Indian independence" returned its
    index page ("may refer to: …"), which explains nothing."""
    index_page = (
        "Indian independence may refer to:\n "
        "Indian independence movement, movement to end British rule in India\n"
    )
    movement = (
        "The Indian independence movement was a series of historic events "
        "aimed at ending British rule in India. It lasted until 1947.\n"
    )
    hits = [
        _live_hit("Indian independence", page_id="i1", intro="may refer to", score=0.98),
        _live_hit("Indian independence movement", page_id="i2", intro="historic events", score=0.89),
    ]
    client = _mock_client(
        ("Indian independence", index_page),
        ("Indian independence movement", movement),
    )

    briefing = await build_briefing(
        "Indian independence", hits, client=client, environ={"HG_LIVE_SOURCES": "1"}
    )

    assert briefing is not None
    assert "may refer to" not in (briefing.summary or "").casefold()
    assert briefing.sources[0].title == "Indian independence movement"
    assert "historic events" in (briefing.summary or "")
    # The user is told why the index page was not used.
    assert any("index page" in note for note in briefing.uncertainty)


async def test_source_outage_falls_back_to_stored_text_without_raising():
    hits = [_live_hit("1946", page_id="1946", intro="Stored intro about the year.")]

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))

    briefing = await build_briefing(
        "1946", hits, client=client, environ={"HG_LIVE_SOURCES": "1"}
    )

    assert briefing is not None
    # The stored summary still explains something …
    assert briefing.summary and "Stored intro" in briefing.summary
    # … and nothing is invented to fill the missing page.
    assert briefing.timeline == []


# ---------------------------------------------------------------------------
# Ranking (the "every result is 90%" defect)
# ---------------------------------------------------------------------------


async def test_year_query_does_not_give_every_result_the_same_score(open_db):
    """Regression: querying ``1946`` scored *every* title containing the
    token 0.9, so the UI showed five identical "90%" matches."""
    async with open_db() as sessions:
        async with sessions() as session:
            session.add_all(
                [
                    HistoricalRecord(
                        id="rec-year", title="1946", source="Archive", content="Events of 1946."
                    ),
                    HistoricalRecord(
                        id="rec-june", title="June 1946", source="Archive", content="June 1946 events."
                    ),
                    HistoricalRecord(
                        id="rec-painting",
                        title="Painting 1946",
                        source="Museum",
                        content="A painting completed in 1946.",
                    ),
                ]
            )
            await session.commit()

        async with sessions() as session:
            hits = await search_historical_records(session, "1946")

    assert [hit.record.id for hit in hits][0] == "rec-year"
    scores = [hit.score for hit in hits]
    # Not the old flat "everything is 0.9" ladder.
    assert len(set(scores)) > 1, f"scores must differ, got {scores}"
    by_id = {hit.record.id: hit.score for hit in hits}
    # An article *about* the year outranks titles that merely contain it.
    assert by_id["rec-year"] == 1.0
    assert by_id["rec-june"] < by_id["rec-year"]
    assert by_id["rec-painting"] < by_id["rec-year"]


async def test_title_subject_match_outranks_a_mention(open_db):
    async with open_db() as sessions:
        async with sessions() as session:
            session.add_all(
                [
                    HistoricalRecord(
                        id="rec-babbage",
                        title="Charles Babbage",
                        source="Archive",
                        content="Babbage designed calculating engines.",
                    ),
                    HistoricalRecord(
                        id="rec-ada",
                        title="Ada Lovelace",
                        source="Archive",
                        content="Lovelace worked with Babbage, a mathematician.",
                    ),
                ]
            )
            await session.commit()

        async with sessions() as session:
            hits = await search_historical_records(session, "babbage mathematician")

    assert hits[0].record.id == "rec-babbage"
    assert normalize_query("Babbage   Mathematician") == "babbage mathematician"
