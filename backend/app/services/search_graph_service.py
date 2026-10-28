"""Knowledge graph for a **historical text search**.

The image-analysis graph is built from entities the vision model extracted
and relationships it proposed. A text search has no vision step, so this
module builds the graph from what retrieval actually returned — and only
from that:

* **subject node** — the queried subject (its type comes from the
  briefing's resolved subject profile when available, otherwise ``other``);
* **record nodes** — one per retrieved record, labelled with that record's
  own title;
* **retrieval edges** — subject → record, labelled ``retrieved for``, each
  carrying the record itself as its evidence (a retrieval, not a
  historical claim, so it is reported ``unverified``);
* **co-mention edges** — record A → record B only when A's title appears
  verbatim in B's own text, with the containing sentence as evidence.

Nothing is inferred, no id is invented that clashes with a record id, and
an entity that shares a name with another is not merged: record nodes keep
their own stable id, and the subject node is namespaced ``subject:``.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..core.logging import get_logger
from ..schemas.graph import (
    GraphEvidence,
    GraphNode,
    SearchGraphEdge,
    SearchGraphResponse,
)
from ..schemas.search import HistoricalBriefing
from .retrieval_service import RetrievedRecord, normalize_query

logger = get_logger("search_graph")

__all__ = ["build_search_graph", "MAX_RECORD_NODES", "MAX_COMENTION_EDGES"]

#: Nodes kept per graph (a readable canvas, not a hairball).
MAX_RECORD_NODES = 12
#: Co-mention edges kept per graph.
MAX_COMENTION_EDGES = 16
#: Characters of surrounding text used as co-mention evidence.
EXCERPT_WINDOW = 220


def _subject_node(
    query: str, briefing: Optional[HistoricalBriefing]
) -> GraphNode:
    subject = briefing.subject if briefing else None
    label = (subject.name if subject and subject.name else query).strip() or query
    return GraphNode(
        id=f"subject:{normalize_query(query)}",
        label=label[:512],
        type=(subject.type if subject and subject.type else "other"),
    )


def _sentence_window(content: str, needle: str) -> str:
    """The sentence around the first occurrence of ``needle`` in ``content``."""
    lowered = content.casefold()
    position = lowered.find(needle.casefold())
    if position < 0:
        return content[:EXCERPT_WINDOW].strip()
    start = max(0, position - EXCERPT_WINDOW // 2)
    end = min(len(content), position + len(needle) + EXCERPT_WINDOW // 2)
    snippet = content[start:end].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{snippet}{suffix}"


def _co_mentions(
    records: Sequence[RetrievedRecord],
) -> list[SearchGraphEdge]:
    """Edges where one retrieved record's title appears in another's text.

    Deliberately conservative: the title must be at least 4 characters and
    appear as a whole phrase, so short/common titles cannot spray edges.
    """
    edges: list[SearchGraphEdge] = []
    for mentioning in records:
        content = mentioning.record.content or ""
        if not content:
            continue
        lowered = content.casefold()
        for mentioned in records:
            if mentioned.record.id == mentioning.record.id:
                continue
            title = mentioned.record.title.strip()
            # Conservative: a title under 4 characters is too generic to
            # support a relationship.
            if len(title) < 4 or title.casefold() not in lowered:
                continue
            edges.append(
                SearchGraphEdge(
                    # Namespaced, deterministic, and impossible to confuse
                    # with a persisted relationship id.
                    id=f"comention:{mentioning.record.id}->{mentioned.record.id}",
                    # Direction follows the text: the record that *names*
                    # the other one points at it.
                    source=mentioning.record.id,
                    target=mentioned.record.id,
                    label=f"mentions {title[:80]}",
                    verification_status="unverified",
                    evidence=[
                        GraphEvidence(
                            source=mentioning.record.source or "Retrieved record",
                            excerpt=_sentence_window(content, title),
                            url=mentioning.record.url,
                        )
                    ],
                )
            )
            if len(edges) >= MAX_COMENTION_EDGES:
                return edges
    return edges


def build_search_graph(
    query: str,
    hits: Sequence[RetrievedRecord],
    *,
    briefing: Optional[HistoricalBriefing] = None,
    notice: Optional[str] = None,
) -> SearchGraphResponse:
    """Assemble the graph of one text search from its real retrieval hits."""
    trimmed = (query or "").strip()
    if not trimmed:
        raise ValueError("build_search_graph requires a non-empty query.")

    records = list(hits)[:MAX_RECORD_NODES]
    limitations: list[str] = []

    if not records:
        limitations.append(
            "No historical records were retrieved for this query, so no graph "
            "can be built. Try a more specific name, place or year."
        )
        return SearchGraphResponse(
            query=trimmed, nodes=[], edges=[], sources=[], limitations=limitations,
            notice=notice,
        )

    subject = _subject_node(trimmed, briefing)
    nodes: list[GraphNode] = [subject]
    edges: list[SearchGraphEdge] = []
    sources: list[GraphEvidence] = []

    for hit in records:
        record = hit.record
        nodes.append(
            GraphNode(
                id=record.id,
                label=record.title[:512],
                type="record",
            )
        )
        evidence = GraphEvidence(
            source=record.source or "Retrieved record",
            excerpt=hit.excerpt or (record.content or "")[:EXCERPT_WINDOW] or None,
            url=record.url,
        )
        sources.append(evidence)
        edges.append(
            SearchGraphEdge(
                id=f"retrieved:{record.id}",
                source=subject.id,
                target=record.id,
                # An honest description of what the edge means: this record
                # was retrieved for the query. Not a historical claim.
                label="retrieved for",
                verification_status="unverified",
                evidence=[evidence],
            )
        )

    co_mentions = _co_mentions(records)
    edges.extend(co_mentions)

    if len(records) == 1:
        limitations.append(
            "Only one record was retrieved, so the graph has no cross-record "
            "relationships. Broaden the query to see more."
        )
    if not co_mentions:
        limitations.append(
            "No retrieved record names another one in its own text, so no "
            "record-to-record relationships could be supported. The graph "
            "shows the retrieved sources only."
        )
    if briefing is None:
        limitations.append(
            "No historical explanation was built for this query, so the "
            "subject node carries no resolved type or description."
        )

    logger.info(
        "search graph for %r: %d nodes, %d edges (%d co-mentions)",
        trimmed,
        len(nodes),
        len(edges),
        len(co_mentions),
    )
    return SearchGraphResponse(
        query=trimmed,
        nodes=nodes,
        edges=edges,
        sources=sources,
        limitations=limitations,
        notice=notice,
    )
