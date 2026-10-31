"""Stories & Legends — sourced narratives for curated heritage sites.

Design rules enforced here, mirroring the rest of the app:

* **No invention.** A story exists only when it was curated from cited
  sources or submitted by a signed-in contributor. There is no generative
  fallback: if nothing matches a question, the answer says so.
* **Traditions stay separate.** A story can carry several versions; the
  API never picks one and presents it as the account. Versions have their
  own sources and their own tradition/community fields.
* **Epistemic labels are neutral.** ``category`` says what the story is
  about; ``narrative_type`` says what kind of account it is; neither is a
  truth score and neither is derived automatically.
* **Provenance is a first-class row.** Sources live in their own table,
  linked to the story or to one version, so no narrative is published with
  a JSON blob standing in for citation.
* **Community contributions are not history.** A submission starts
  ``pending``; only a moderator can publish it, and publication marks it
  ``community-account`` / ``community-approved`` — never "verified".
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..db.models import HeritageSite, HeritageStory, HeritageStorySource, HeritageStoryVersion
from ..schemas.story import (
    NARRATIVE_TYPE_LABELS,
    NARRATIVE_TYPES,
    SOURCE_TYPES,
    STORY_CATEGORIES,
    STORY_CATEGORY_LABELS,
    STORY_LANGUAGE_LABELS,
    STORY_LANGUAGES,
    STORY_VERIFICATION_STATUSES,
)

logger = get_logger("stories")

__all__ = [
    "seed_heritage_stories",
    "site_stories",
    "story_detail",
    "story_category_facets",
    "story_counts_by_site",
    "story_categories_by_site",
    "featured_stories_by_site",
    "sites_with_story_category",
    "ask_stories",
    "submit_story",
    "moderate_story",
    "moderator_emails",
    "STORY_SEED_FILE",
    "COMMUNITY_PROVENANCE",
    "STORY_NOTICE_TEXT",
]

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
STORY_SEED_FILE = _REPO_ROOT / "database" / "seeds" / "heritage_stories.json"

COMMUNITY_PROVENANCE = "community-oral-history"
CURATED_DATASET = "curated-demo-india"

STORY_NOTICE_TEXT = (
    "Labels describe the basis of each account — documented history, "
    "religious tradition, folklore, scholarly interpretation — and do not "
    "rank one tradition above another. A published community contribution "
    "is not a verified historical claim."
)

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have how in is it its of on or "
    "that the their they this to was were what when where which who why "
    "with about into than then there these those".split()
)


@dataclass(frozen=True)
class StoryFilters:
    category: Optional[str] = None
    language: Optional[str] = None


def _read_seed() -> list[dict]:
    if not STORY_SEED_FILE.is_file():
        raise FileNotFoundError(f"Story seed file not found: {STORY_SEED_FILE}")
    payload = json.loads(STORY_SEED_FILE.read_text(encoding="utf-8"))
    stories = payload.get("stories")
    if not isinstance(stories, list):
        raise ValueError("Story seed is malformed: 'stories' must be a list.")
    return stories


def _seed_problem(story: dict, site_ids: set[str], seen: set[str]) -> Optional[str]:
    story_id = str(story.get("id") or "")
    if not _SLUG.match(story_id):
        return f"{story_id or '<missing id>'}: id must be a lowercase slug"
    if story_id in seen:
        return f"{story_id}: duplicate id"
    site_id = str(story.get("site_id") or "")
    if site_id not in site_ids:
        return f"{story_id}: unknown site_id {site_id!r}"

    for field in ("title", "summary", "narrative"):
        value = story.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"{story_id}: {field} is required"
    if story.get("category") not in STORY_CATEGORIES:
        return f"{story_id}: category {story.get('category')!r} is not documented"
    if story.get("narrative_type") not in NARRATIVE_TYPES:
        return f"{story_id}: narrative_type {story.get('narrative_type')!r} is not documented"
    if story.get("language") not in STORY_LANGUAGES:
        return f"{story_id}: language {story.get('language')!r} is not supported"
    if story.get("verification_status") not in STORY_VERIFICATION_STATUSES:
        return f"{story_id}: verification_status is not documented"

    sources = story.get("sources") or []
    if not isinstance(sources, list) or not sources:
        return f"{story_id}: at least one source is required"
    problem = _source_problem(story_id, sources, "source")
    if problem:
        return problem

    versions = story.get("versions") or []
    if not isinstance(versions, list):
        return f"{story_id}: versions must be a list"
    if len(versions) == 1:
        return f"{story_id}: a single version should be told as the story, not as a version"
    version_ids: set[str] = set()
    for index, version in enumerate(versions):
        if not isinstance(version, dict):
            return f"{story_id}: version {index} must be an object"
        version_id = str(version.get("id") or "")
        if not _SLUG.match(version_id):
            return f"{story_id}: version {index} needs a slug id"
        if version_id in version_ids:
            return f"{story_id}: duplicate version id {version_id}"
        version_ids.add(version_id)
        if not str(version.get("label") or "").strip():
            return f"{story_id}: version {version_id} needs a label"
        if not str(version.get("summary") or "").strip():
            return f"{story_id}: version {version_id} needs a summary"
        if version.get("language") not in STORY_LANGUAGES:
            return f"{story_id}: version {version_id} has an unsupported language"
        problem = _source_problem(
            f"{story_id}: version {version_id}", version.get("sources") or [], "sources"
        )
        if problem:
            return problem
    return None


def _source_problem(owner: str, sources: object, label: str) -> Optional[str]:
    if not isinstance(sources, list):
        return f"{owner}: {label} must be a list"
    for source in sources:
        if not isinstance(source, dict):
            return f"{owner}: every {label} entry must be an object"
        if not str(source.get("title") or "").strip():
            return f"{owner}: every source needs a title"
        if source.get("source_type") not in SOURCE_TYPES:
            return f"{owner}: source_type {source.get('source_type')!r} is not documented"
        url = source.get("url")
        if url is not None and not str(url).startswith("https://"):
            return f"{owner}: source URLs must be https"
    return None


def _source_payload(source: HeritageStorySource) -> dict:
    return {
        "id": source.id,
        "title": source.title,
        "author": source.author,
        "sourceType": source.source_type,
        "published": source.published,
        "url": source.url,
        "excerpt": source.excerpt,
        "locator": source.locator,
        "licence": source.licence,
    }


def _summary_payload(
    story: HeritageStory,
    version_count: int,
    source_count: int,
) -> dict:
    return {
        "id": story.id,
        "siteId": story.site_id,
        "title": story.title,
        "summary": story.summary,
        "category": story.category,
        "categoryLabel": STORY_CATEGORY_LABELS.get(story.category, story.category),
        "narrativeType": story.narrative_type,
        "narrativeTypeLabel": NARRATIVE_TYPE_LABELS.get(
            story.narrative_type, story.narrative_type
        ),
        "tradition": story.tradition,
        "language": story.language,
        "languageLabel": STORY_LANGUAGE_LABELS.get(story.language, story.language),
        "community": story.community,
        "period": story.period,
        "verificationStatus": story.verification_status,
        "provenance": story.provenance,
        "isCommunity": story.provenance == COMMUNITY_PROVENANCE,
        "versionCount": version_count,
        "sourceCount": source_count,
    }


async def _counts(session: AsyncSession, story_ids: Sequence[str]) -> tuple[dict, dict]:
    versions: dict[str, int] = {}
    sources: dict[str, int] = {}
    if not story_ids:
        return versions, sources
    version_rows = await session.execute(
        select(HeritageStoryVersion.story_id, func.count())
        .where(HeritageStoryVersion.story_id.in_(story_ids))
        .group_by(HeritageStoryVersion.story_id)
    )
    versions = {row[0]: int(row[1]) for row in version_rows}
    source_rows = await session.execute(
        select(HeritageStorySource.story_id, func.count())
        .where(HeritageStorySource.story_id.in_(story_ids))
        .group_by(HeritageStorySource.story_id)
    )
    sources = {row[0]: int(row[1]) for row in source_rows}
    return versions, sources


def _published():
    return HeritageStory.status == "published"


async def seed_heritage_stories(session: AsyncSession) -> int:
    """Validate and upsert the curated story seed (idempotent).

    Curated rows are matched by id; their versions and sources are replaced
    so an edited seed is reflected without duplicating rows. Community
    submissions (different provenance) are never touched.
    """
    stories = _read_seed()
    site_ids = set((await session.execute(select(HeritageSite.id))).scalars())
    seen: set[str] = set()
    problems: list[str] = []
    for story in stories:
        problem = _seed_problem(story, site_ids, seen)
        if problem:
            problems.append(problem)
        seen.add(str(story.get("id") or ""))
    if problems:
        raise ValueError("Story seed is invalid: " + "; ".join(problems[:10]))

    touched = 0
    for item in stories:
        story_id = str(item["id"])
        story = await session.get(HeritageStory, story_id)
        if story is None:
            story = HeritageStory(id=story_id, site_id=str(item["site_id"]))
            session.add(story)
        story.site_id = str(item["site_id"])
        story.title = str(item["title"]).strip()
        story.summary = str(item["summary"]).strip()
        story.narrative = str(item["narrative"]).strip()
        story.category = str(item["category"])
        story.narrative_type = str(item["narrative_type"])
        story.tradition = item.get("tradition") or None
        story.language = str(item.get("language") or "en")
        story.community = item.get("community") or None
        story.period = item.get("period") or None
        story.cultural_background = item.get("cultural_background") or None
        story.verification_status = str(item.get("verification_status") or "unverified")
        story.provenance = str(item.get("provenance") or "curated from cited sources")
        story.dataset = str(item.get("dataset") or CURATED_DATASET)
        story.status = "published"
        story.consent = True

        # Replace this curated story's own provenance rows (cascade handles
        # version sources through the version FK).
        existing_versions = await session.execute(
            select(HeritageStoryVersion).where(HeritageStoryVersion.story_id == story_id)
        )
        for version in existing_versions.scalars():
            await session.delete(version)
        existing_sources = await session.execute(
            select(HeritageStorySource).where(HeritageStorySource.story_id == story_id)
        )
        for source in existing_sources.scalars():
            await session.delete(source)
        await session.flush()

        for index, source in enumerate(item.get("sources") or [], start=1):
            session.add(
                HeritageStorySource(
                    id=f"{story_id}:src:{index}",
                    story_id=story_id,
                    version_id=None,
                    title=str(source["title"]).strip(),
                    author=(source.get("author") or None),
                    source_type=str(source["source_type"]),
                    published=(str(source["published"]) if source.get("published") else None),
                    url=source.get("url"),
                    excerpt=source.get("excerpt"),
                    locator=source.get("locator"),
                    licence=source.get("licence"),
                    position=index,
                )
            )

        for index, version in enumerate(item.get("versions") or [], start=1):
            version_id = str(version["id"])
            session.add(
                HeritageStoryVersion(
                    id=version_id,
                    story_id=story_id,
                    label=str(version["label"]).strip(),
                    summary=str(version["summary"]).strip(),
                    narrative=version.get("narrative"),
                    tradition=version.get("tradition"),
                    language=str(version.get("language") or "en"),
                    community=version.get("community"),
                    differences=version.get("differences"),
                    verification_status=str(
                        version.get("verification_status") or "unverified"
                    ),
                    position=index,
                )
            )
            for source_index, source in enumerate(version.get("sources") or [], start=1):
                session.add(
                    HeritageStorySource(
                        id=f"{version_id}:src:{source_index}",
                        story_id=story_id,
                        version_id=version_id,
                        title=str(source["title"]).strip(),
                        author=(source.get("author") or None),
                        source_type=str(source["source_type"]),
                        published=(
                            str(source["published"]) if source.get("published") else None
                        ),
                        url=source.get("url"),
                        excerpt=source.get("excerpt"),
                        locator=source.get("locator"),
                        licence=source.get("licence"),
                        position=source_index,
                    )
                )
        touched += 1
    await session.flush()
    return touched


async def site_stories(
    session: AsyncSession,
    site_id: str,
    filters: Optional[StoryFilters] = None,
) -> tuple[HeritageSite | None, list[dict], list[dict]]:
    """Published stories for one site plus their category facets."""
    site = await session.get(HeritageSite, site_id)
    if site is None:
        return None, [], []

    query = select(HeritageStory).where(
        HeritageStory.site_id == site_id, _published()
    )
    if filters and filters.category:
        query = query.where(HeritageStory.category == filters.category)
    if filters and filters.language:
        query = query.where(HeritageStory.language == filters.language)
    query = query.order_by(HeritageStory.category, HeritageStory.title)
    rows = list((await session.execute(query)).scalars())

    version_counts, source_counts = await _counts(session, [row.id for row in rows])
    stories = [
        _summary_payload(row, version_counts.get(row.id, 0), source_counts.get(row.id, 0))
        for row in rows
    ]

    facet_rows = await session.execute(
        select(HeritageStory.category, func.count())
        .where(HeritageStory.site_id == site_id, _published())
        .group_by(HeritageStory.category)
    )
    counts = {name: int(count) for name, count in facet_rows}
    facets = [
        {
            "name": category,
            "label": STORY_CATEGORY_LABELS[category],
            "count": counts[category],
        }
        for category in STORY_CATEGORIES
        if counts.get(category)
    ]
    return site, stories, facets


async def story_detail(session: AsyncSession, story_id: str) -> Optional[dict]:
    """One published story with sources, versions and related stories."""
    story = await session.get(HeritageStory, story_id)
    if story is None or story.status != "published":
        return None

    site = await session.get(HeritageSite, story.site_id)
    sources = list(
        (
            await session.execute(
                select(HeritageStorySource)
                .where(
                    HeritageStorySource.story_id == story.id,
                    HeritageStorySource.version_id.is_(None),
                )
                .order_by(HeritageStorySource.position)
            )
        ).scalars()
    )
    versions = list(
        (
            await session.execute(
                select(HeritageStoryVersion)
                .where(HeritageStoryVersion.story_id == story.id)
                .order_by(HeritageStoryVersion.position)
            )
        ).scalars()
    )
    version_sources: dict[str, list[HeritageStorySource]] = {}
    if versions:
        rows = list(
            (
                await session.execute(
                    select(HeritageStorySource)
                    .where(
                        HeritageStorySource.story_id == story.id,
                        HeritageStorySource.version_id.in_([v.id for v in versions]),
                    )
                    .order_by(HeritageStorySource.position)
                )
            ).scalars()
        )
        for row in rows:
            version_sources.setdefault(str(row.version_id), []).append(row)

    related_rows = list(
        (
            await session.execute(
                select(HeritageStory)
                .where(
                    HeritageStory.site_id == story.site_id,
                    HeritageStory.id != story.id,
                    _published(),
                )
                .order_by(HeritageStory.category, HeritageStory.title)
                .limit(6)
            )
        ).scalars()
    )
    related_version_counts, related_source_counts = await _counts(
        session, [row.id for row in related_rows]
    )

    payload = _summary_payload(story, len(versions), len(sources))
    payload.update(
        {
            "siteName": site.name if site else story.site_id,
            "siteState": site.state if site else None,
            "narrative": story.narrative,
            "culturalBackground": story.cultural_background,
            "relatedEntities": [],
            "relatedSites": [],
            "sources": [_source_payload(row) for row in sources],
            "versions": [
                {
                    "id": version.id,
                    "label": version.label,
                    "summary": version.summary,
                    "narrative": version.narrative,
                    "tradition": version.tradition,
                    "language": version.language,
                    "community": version.community,
                    "differences": version.differences,
                    "verificationStatus": version.verification_status,
                    "sources": [
                        _source_payload(row)
                        for row in version_sources.get(version.id, [])
                    ],
                }
                for version in versions
            ],
            "relatedStories": [
                _summary_payload(
                    row,
                    related_version_counts.get(row.id, 0),
                    related_source_counts.get(row.id, 0),
                )
                for row in related_rows
            ],
        }
    )
    return payload


async def story_category_facets(session: AsyncSession, site_id: str) -> list[dict]:
    rows = await session.execute(
        select(HeritageStory.category, func.count())
        .where(HeritageStory.site_id == site_id, _published())
        .group_by(HeritageStory.category)
    )
    counts = {name: int(count) for name, count in rows}
    return [
        {"name": category, "label": STORY_CATEGORY_LABELS[category], "count": counts[category]}
        for category in STORY_CATEGORIES
        if counts.get(category)
    ]


async def story_counts_by_site(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(
        select(HeritageStory.site_id, func.count())
        .where(_published())
        .group_by(HeritageStory.site_id)
    )
    return {site_id: int(count) for site_id, count in rows}


async def story_categories_by_site(session: AsyncSession) -> dict[str, list[str]]:
    """Published story categories per site, for the map's story filter."""
    rows = await session.execute(
        select(HeritageStory.site_id, HeritageStory.category)
        .where(_published())
        .distinct()
    )
    result: dict[str, list[str]] = {}
    for site_id, category in rows:
        result.setdefault(site_id, []).append(category)
    for categories in result.values():
        categories.sort()
    return result


async def featured_stories_by_site(
    session: AsyncSession, site_ids: Iterable[str], per_site: int = 2
) -> dict[str, list[dict]]:
    """Up to ``per_site`` headline stories per site, for the map details panel."""
    ids = [site_id for site_id in site_ids]
    if not ids:
        return {}
    rows = list(
        (
            await session.execute(
                select(HeritageStory)
                .where(HeritageStory.site_id.in_(ids), _published())
                .order_by(HeritageStory.site_id, HeritageStory.category, HeritageStory.title)
            )
        ).scalars()
    )
    result: dict[str, list[dict]] = {}
    for row in rows:
        bucket = result.setdefault(row.site_id, [])
        if len(bucket) < per_site:
            bucket.append(
                {
                    "id": row.id,
                    "title": row.title,
                    "category": row.category,
                    "categoryLabel": STORY_CATEGORY_LABELS.get(
                        row.category, row.category
                    ),
                    "narrativeType": row.narrative_type,
                    "narrativeTypeLabel": NARRATIVE_TYPE_LABELS.get(
                        row.narrative_type, row.narrative_type
                    ),
                    "isCommunity": row.provenance == COMMUNITY_PROVENANCE,
                }
            )
    return result


async def sites_with_story_category(session: AsyncSession, category: str) -> set[str]:
    rows = await session.execute(
        select(HeritageStory.site_id)
        .where(_published(), HeritageStory.category == category)
        .distinct()
    )
    return set(rows.scalars())


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9']+", text.lower())
        if token not in _STOPWORDS and len(token) > 2
    }


async def ask_stories(
    session: AsyncSession, site_id: str, question: str
) -> dict:
    """Answer from stored stories only.

    This is retrieval, not generation: the matching story summaries and
    citations are joined into a short answer and the response states when
    the library does not cover the question. Nothing is invented for a
    question the records cannot support.
    """
    site = await session.get(HeritageSite, site_id)
    if site is None:
        return {
            "answer": "This heritage site is not in the curated dataset.",
            "matchedStories": [],
            "citations": [],
            "limitation": "No site record exists for this id.",
        }

    rows = list(
        (
            await session.execute(
                select(HeritageStory).where(
                    HeritageStory.site_id == site_id, _published()
                )
            )
        ).scalars()
    )
    if not rows:
        return {
            "answer": (
                f"No stories are stored for {site.name} yet, so this question "
                "cannot be answered from the story library."
            ),
            "matchedStories": [],
            "citations": [],
            "limitation": (
                "Absence of a story does not mean no tradition exists; it means "
                "this archive has not collected and sourced one."
            ),
        }

    wanted = _tokens(question)
    scored: list[tuple[int, HeritageStory]] = []
    for row in rows:
        haystack = " ".join(
            filter(
                None,
                [
                    row.title,
                    row.summary,
                    row.narrative,
                    row.category,
                    row.narrative_type,
                    row.tradition,
                    row.community,
                ],
            )
        )
        overlap = len(wanted & _tokens(haystack))
        # A single shared word (often the site's own name or "stepwell") is not
        # evidence that a story answers the question; require at least two.
        if overlap >= 2:
            scored.append((overlap, row))
    scored.sort(key=lambda item: (-item[0], item[1].category, item[1].title))
    matched = scored[:3]

    version_counts, source_counts = await _counts(session, [row.id for _, row in matched])
    matched_payload = [
        _summary_payload(row, version_counts.get(row.id, 0), source_counts.get(row.id, 0))
        for _, row in matched
    ]

    if not matched:
        return {
            "answer": (
                f"The stored stories for {site.name} do not address that question. "
                "I will not compose an answer without a matching, sourced record."
            ),
            "matchedStories": [],
            "citations": [],
            "limitation": (
                "Try asking about a documented story, a tradition, a version, "
                "or the architecture; the library currently holds "
                f"{len(rows)} published stories for this place."
            ),
        }

    sources = list(
        (
            await session.execute(
                select(HeritageStorySource)
                .where(HeritageStorySource.story_id.in_([row.id for _, row in matched]))
                .order_by(HeritageStorySource.position)
                .limit(8)
            )
        ).scalars()
    )
    citations: list[str] = []
    for source in sources:
        label = f"{source.title}"
        if source.author:
            label += f" — {source.author}"
        if source.url:
            label += f" ({source.url})"
        if label not in citations:
            citations.append(label)

    lines = []
    for _, row in matched:
        label = NARRATIVE_TYPE_LABELS.get(row.narrative_type, row.narrative_type)
        # Plain text on purpose: the reader renders answers without a Markdown
        # parser, so emphasis markers would show up literally.
        lines.append(f"{row.title} ({label}): {row.summary}")
    answer = "\n\n".join(lines)
    limitation = None
    if len(rows) > len(matched):
        limitation = (
            f"This answer draws on {len(matched)} of {len(rows)} stored stories; "
            "other traditions at this place are listed in Stories & Legends."
        )
    return {
        "answer": answer,
        "matchedStories": matched_payload,
        "citations": citations,
        "limitation": limitation,
    }


def moderator_emails() -> set[str]:
    """Moderators are an explicit deployment allowlist, never inferred."""
    raw = os.environ.get("STORY_MODERATOR_EMAILS", "")
    return {
        part.strip().lower()
        for part in raw.replace(";", ",").split(",")
        if part.strip()
    }


async def submit_story(
    session: AsyncSession,
    *,
    site_id: str,
    title: str,
    narrative: str,
    language: str,
    community: Optional[str],
    tradition: Optional[str],
    source_note: Optional[str],
    interview_date: Optional[str],
    consent: bool,
    contributor_id: str,
    contributor_display: Optional[str],
) -> Optional[dict]:
    """Store a community submission as ``pending``. Never publishes it."""
    site = await session.get(HeritageSite, site_id)
    if site is None:
        return None
    if not consent:
        raise ValueError("Consent is required before a contribution can be stored.")

    digest = hashlib.sha256(
        f"{contributor_id}\u0000{site_id}\u0000{title}\u0000{narrative}".encode("utf-8")
    ).hexdigest()[:10]
    story_id = f"community-{contributor_id[:8]}-{digest}"
    story = HeritageStory(
        id=story_id,
        site_id=site_id,
        title=title.strip(),
        summary=narrative.strip()[:280],
        narrative=narrative.strip(),
        category="oral-history",
        narrative_type="community-account",
        tradition=(tradition or "Community oral history"),
        language=language,
        community=community,
        period=None,
        cultural_background=None,
        verification_status="pending-review",
        provenance=COMMUNITY_PROVENANCE,
        dataset="community-submissions",
        status="pending",
        contributor_id=contributor_id,
        contributor_display=(contributor_display or "Community contributor"),
        interview_date=interview_date,
        consent=True,
    )
    session.add(story)
    await session.flush()
    if source_note:
        session.add(
            HeritageStorySource(
                id=f"{story_id}:src:1",
                story_id=story_id,
                version_id=None,
                title="Contributor's source note",
                author=None,
                source_type="community-contribution",
                published=None,
                url=None,
                excerpt=source_note.strip(),
                locator=None,
                licence=None,
                position=1,
            )
        )
    await session.flush()
    return _summary_payload(story, 0, 1 if source_note else 0)


async def moderate_story(
    session: AsyncSession, story_id: str, decision: str
) -> tuple[Optional[dict], Optional[str]]:
    """Approve or reject a pending submission. Returns (payload, error)."""
    story = await session.get(HeritageStory, story_id)
    if story is None or story.provenance != COMMUNITY_PROVENANCE:
        return None, "not_found"
    if story.status != "pending":
        return None, "not_pending"
    if decision == "approve":
        story.status = "published"
        story.verification_status = "community-approved"
    else:
        story.status = "rejected"
        story.verification_status = "unverified"
    await session.flush()
    return _summary_payload(story, 0, 0), None
