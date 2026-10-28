"""D3 — text normalisation utilities for extraction.

Single source of truth for name matching: this module **re-exports
Module C's** :func:`normalize_name`
(``app.repositories.entity_repository``) instead of reimplementing it,
so three rules can never drift apart:

1. D3's conservative dedupe key (this package),
2. C2's ``find_entities_by_normalized_names`` lookup,
3. C2's ``upsert_entities`` ``(name, entity_type)`` collapse.

The rule itself is deliberately minimal — collapse whitespace and
casefold — because a *stricter* rule here would let extraction merge
entities that Module C keeps apart (silently different historical
people becoming one row), and a *looser* one would leave duplicates
that C then refuses to collapse. Punctuation and spelling differences
are therefore NOT normalised away: alternate spellings stay separate
entities (D3 acceptance criterion: never silently merge different
historical entities).
"""

from __future__ import annotations

import re

from ..repositories.entity_repository import normalize_name

__all__ = ["normalize_name", "normalize_text", "name_in_text"]


def normalize_text(text: str) -> str:
    """The same rule applied to a whole passage (whitespace + casefold).

    Used for containment checks such as OCR corroboration; never as an
    identity key — passages are not identifiers.
    """
    return " ".join(text.split()).casefold()


def name_in_text(name: str, text: str) -> bool:
    """Conservative check: does ``name`` occur in ``text`` as a word?

    Both sides are normalised with the shared rule, and the match is
    bounded by word characters so ``Ada`` does not "corroborate" inside
    ``Madagascar``. This is provenance metadata only — it never merges,
    ranks or verifies anything (verification is Module C4's job).
    """
    needle = normalize_name(name)
    if not needle:
        return False
    haystack = normalize_text(text)
    if not haystack:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None
