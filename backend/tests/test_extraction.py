"""D3 tests — structured extraction and entity normalization.

Provider layers are **fixtures** (``httpx.MockTransport``, zero egress —
same seam as ``test_providers.py``); everything else is real: D1's
``prepare_image`` decodes a genuinely generated image, D2's adapters
parse and validate the fixture payloads, and D3 coordinates, canonicalises
and deduplicates on Module C's own normalisation rule.

Covered required cases: empty OCR output, repeated names, alternate
spellings, malformed AI output, ambiguous matching — plus type
canonicalisation (the DB vocabulary), provenance preservation, provider
failure propagation, and a real-image end-to-end extraction example.
"""

from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from PIL import Image, ImageDraw

from app.db.models import ENTITY_TYPES
from app.integrations import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.repositories.entity_repository import normalize_name as module_c_normalize_name
from app.schemas.entity import AnalysisEntity
from app.services.entity_service import (
    EntityCandidate,
    build_candidates,
    canonicalize_entity_type,
    deduplicate,
    to_analysis_entities,
)
from app.services.extraction_service import (
    VISION_MAX_DIMENSION,
    VISION_MAX_PNG_BYTES,
    run_extraction,
    vision_payload,
)
from app.services.image_service import prepare_image
from app.utils.text_normalization import name_in_text, normalize_name, normalize_text

pytestmark = pytest.mark.anyio

#: A real 1x1 PNG (passes D1's magic-byte gate and Pillow decode).
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg=="
)

PROVIDER_ENV = {
    "OCR_API_URL": "https://ocr.example.test/read",
    "OCR_API_KEY": "sk-test-ocr",
    "VISION_API_BASE_URL": "https://vision.example.test/v1",
    "VISION_API_KEY": "sk-test-vision",
    "VISION_MODEL": "vision-model-x",
}


@pytest.fixture(autouse=True)
def provider_env(monkeypatch):
    """Valid dummy config for both adapters; isolated from the host shell."""
    for name in (*PROVIDER_ENV, "OCR_TIMEOUT", "VISION_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in PROVIDER_ENV.items():
        monkeypatch.setenv(name, value)


# ---------------------------------------------------------------------------
# Fixture helpers (provider layer only — the rest of the chain is real)
# ---------------------------------------------------------------------------


def prepared_image(png: bytes = PNG_BYTES):
    return prepare_image(png, filename="document.png", content_type="image/png")


def _ocr(text: str | None = None, *, response: httpx.Response | None = None):
    """OCR fixture client; ``response`` (raw) wins over ``text``."""
    def handler(_request: httpx.Request) -> httpx.Response:
        if response is not None:
            return response
        return httpx.Response(200, json={"text": text or ""})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _vision(payload=None, *, content: str | None = None):
    """Vision fixture: ``content`` (raw message string) wins over ``payload``."""

    def handler(_request: httpx.Request) -> httpx.Response:
        body = content if content is not None else json.dumps(payload or {"entities": []})
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "assistant", "content": body}}]},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _raising(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def entity(name: str, type_: str = "person", confidence=None) -> dict:
    return {"name": name, "type": type_, "confidence": confidence}


async def run(*, ocr_text="", vision=None, ocr_client=None, vision_client=None):
    """Run extraction on a freshly prepared image; fixture clients are
    built lazily so unused clients are never created."""
    return await run_extraction(
        prepared_image(),
        ocr_client=ocr_client if ocr_client is not None else _ocr(ocr_text),
        vision_client=vision_client if vision_client is not None else _vision(vision),
    )


# ---------------------------------------------------------------------------
# Required scenario: empty OCR output
# ---------------------------------------------------------------------------


async def test_empty_ocr_output_still_returns_vision_entities():
    result = await run(ocr_text="", vision={"entities": [entity("Ada Lovelace")]})

    assert result.extracted_text is None
    assert [e.name for e in result.entities] == ["Ada Lovelace"]
    assert any("No text could be recognised" in w for w in result.warnings)
    # ...but the entity list itself carries no fabricated warning.
    assert not any("unusable" in w for w in result.warnings)


async def test_vision_text_is_a_flagged_fallback_when_ocr_is_empty():
    result = await run(
        ocr_text="",
        vision={"text": "Transcribed heading", "entities": [entity("Ada Lovelace")]},
    )

    assert result.extracted_text == "Transcribed heading"
    assert any("comes from the vision model" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Required scenario: repeated names (conservative exact dedupe)
# ---------------------------------------------------------------------------


async def test_repeated_names_are_merged_conservatively():
    result = await run(
        ocr_text="Certificate of Ada Lovelace",
        vision={
            "entities": [
                entity("Ada Lovelace", "person", 0.7),
                entity("  ada   LOVELACE ", "person", 0.9),
            ]
        },
    )

    assert len(result.entities) == 1
    merged = result.entities[0]
    assert merged.name == "Ada Lovelace"  # first extracted spelling preserved
    assert merged.confidence == 0.9  # strongest reported confidence
    assert result.candidates[0].sources == ("vision", "ocr")  # corroborated by OCR
    assert merged.id == "ent-1"


async def test_identical_candidates_without_ocr_text_keep_vision_only_source():
    result = await run(ocr_text="", vision={"entities": [entity("Turing", "person", None)]})

    assert result.candidates[0].sources == ("vision",)
    assert result.entities[0].confidence is None  # unreported stays unreported


# ---------------------------------------------------------------------------
# Required scenario: alternate spellings (never merged)
# ---------------------------------------------------------------------------


async def test_alternate_spellings_are_not_merged():
    result = await run(
        ocr_text="Ada Lovelace and A. Lovelace",
        vision={
            "entities": [
                entity("Ada Lovelace", "person", 0.9),
                entity("A. Lovelace", "person", 0.8),
            ]
        },
    )

    assert [e.name for e in result.entities] == ["Ada Lovelace", "A. Lovelace"]
    assert [e.id for e in result.entities] == ["ent-1", "ent-2"]


async def test_punctuation_variants_are_not_silently_merged():
    result = await run(ocr_text="", vision={"entities": [entity("London."), entity("London")]})

    assert len(result.entities) == 2


# ---------------------------------------------------------------------------
# Required scenario: malformed AI output (degrade loudly, never silently)
# ---------------------------------------------------------------------------


async def test_malformed_vision_output_degrades_with_warning():
    result = await run(
        ocr_text="Legible OCR text",
        vision_client=_vision(content="I cannot analyse that image."),
    )

    assert result.extracted_text == "Legible OCR text"
    assert result.entities == []
    assert result.relationships == []  # D4 receives no proposals from a failed vision step
    assert any("vision model returned an unusable response" in w for w in result.warnings)


async def test_malformed_ocr_output_degrades_with_warning():
    result = await run(
        ocr_client=_ocr(response=httpx.Response(200, json={"nope": 1})),
        vision={"text": "Vision transcription", "entities": [entity("Ada Lovelace")]},
    )

    assert result.extracted_text == "Vision transcription"
    assert len(result.entities) == 1
    assert any("OCR returned an unusable response" in w for w in result.warnings)


async def test_provider_timeout_is_not_swallowed():
    with pytest.raises(ProviderTimeoutError):
        await run(vision_client=_raising(httpx.ConnectTimeout("boom")))


async def test_missing_credentials_are_not_swallowed(monkeypatch):
    monkeypatch.delenv("VISION_API_KEY")
    with pytest.raises(ProviderConfigurationError) as info:
        await run(ocr_text="x")
    assert "VISION_API_KEY" in str(info.value)


# ---------------------------------------------------------------------------
# Required scenario: ambiguous matching
# ---------------------------------------------------------------------------


async def test_same_name_under_two_types_stays_ambiguous_and_separate():
    result = await run(
        ocr_text="Chelsea, 1863",
        vision={
            "entities": [
                entity("Chelsea", "place", 0.8),
                entity("Chelsea", "event", 0.6),
            ]
        },
    )

    assert [e.name for e in result.entities] == ["Chelsea", "Chelsea"]
    assert [e.type for e in result.entities] == ["place", "event"]
    assert any('Ambiguous name "Chelsea"' in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Type canonicalisation (Module C vocabulary) + schema validity
# ---------------------------------------------------------------------------


def test_canonicalize_entity_type_rules():
    assert canonicalize_entity_type("Person") == ("person", None)
    assert canonicalize_entity_type(" place ") == ("place", None)
    assert canonicalize_entity_type("object") == ("other", "object")
    assert canonicalize_entity_type("") == ("other", "")
    # R5 — documented heritage synonyms canonicalise without a warning…
    assert canonicalize_entity_type("Monument") == ("place", "Monument")
    assert canonicalize_entity_type("inscription") == ("other", "inscription")
    assert canonicalize_entity_type("dynasty") == ("other", "dynasty")
    assert canonicalize_entity_type("organisation") == ("other", "organisation")


async def test_unknown_types_map_to_other_with_one_aggregated_warning():
    result = await run(
        ocr_text="",
        vision={
            "entities": [
                entity("Steam Engine", "object", 0.9),
                entity("Birmingham", "relic", 0.9),  # outside every documented vocabulary
                entity("Ada Lovelace", "person", 0.9),
            ]
        },
    )

    assert {e.type for e in result.entities} <= set(ENTITY_TYPES)
    by_name = {e.name: e for e in result.entities}
    assert by_name["Steam Engine"].type == "other"
    assert by_name["Birmingham"].type == "other"
    assert result.candidates[0].original_type == "object"  # source preserved
    remap_warnings = [w for w in result.warnings if "Unrecognised entity types" in w]
    assert len(remap_warnings) == 1
    assert '"object"' in remap_warnings[0] and '"relic"' in remap_warnings[0]


async def test_heritage_synonym_types_canonicalise_without_a_warning():
    """R5 — monument/dynasty/inscription/organisation are documented
    vocabulary, not unknown types: canonicalised, original label kept on
    the candidate, no scary warning in the UI."""
    result = await run(
        ocr_text="",
        vision={
            "entities": [
                entity("Rani ki Vav", "monument", 0.91),
                entity("Chaulukya dynasty", "dynasty", 0.7),
                entity("Stepwell inscription", "inscription", 0.6),
                entity("Archaeological Survey", "organisation", 0.6),
            ]
        },
    )

    by_name = {e.name: e for e in result.entities}
    assert by_name["Rani ki Vav"].type == "place"
    assert by_name["Chaulukya dynasty"].type == "other"
    assert by_name["Stepwell inscription"].type == "other"
    assert by_name["Archaeological Survey"].type == "other"
    # provenance of the reported label survives on the candidate
    assert result.candidates[0].original_type == "monument"
    assert not any("Unrecognised entity types" in w for w in result.warnings)


async def test_blank_names_are_discarded_with_warning():
    result = await run(ocr_text="", vision={"entities": [entity("   "), entity("Ada", "person", 0.5)]})

    assert [e.name for e in result.entities] == ["Ada"]
    assert any("empty name was discarded" in w for w in result.warnings)


async def test_result_entities_are_valid_b2_schema_objects():
    result = await run(
        ocr_text="Ada Lovelace",
        vision={
            "entities": [
                entity("Ada Lovelace", "person", 0.9),
                entity("London", "place", None),
            ]
        },
    )

    for entity_obj in result.entities:
        validated = AnalysisEntity.model_validate(entity_obj.model_dump())
        assert validated == entity_obj
    ids = [e.id for e in result.entities]
    assert len(ids) == len(set(ids))
    # candidates and entities stay positionally aligned (documented contract)
    assert len(result.candidates) == len(result.entities)


async def test_everything_empty_is_schema_valid_not_an_error():
    result = await run(ocr_text="", vision={"entities": []})

    assert result.entities == []
    assert result.extracted_text is None
    assert any("No text could be recognised" in w for w in result.warnings)
    assert any("No entities were detected" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Unit-level: normalization, dedupe, provenance
# ---------------------------------------------------------------------------


def test_normalization_reexports_module_c_rule():
    assert normalize_name is module_c_normalize_name
    assert normalize_name("  Ada   Lovelace ") == "ada lovelace"
    # Punctuation is NOT normalised away — that would over-merge.
    assert normalize_name("London.") == "london."


def test_normalize_text_and_name_in_text_word_boundaries():
    assert normalize_text("  Ada\tLovelace \n") == "ada lovelace"
    assert name_in_text("Ada Lovelace", "Ada Lovelace wrote this") is True
    assert name_in_text("Ada", "Madagascar") is False
    assert name_in_text("Ada", "Ada, 1843") is True
    assert name_in_text("Ada", "") is False


def test_build_candidates_corroborates_only_names_present_in_ocr():
    class FakeEntity:
        def __init__(self, name, type_="person", confidence=None):
            self.name, self.type, self.confidence = name, type_, confidence

    candidates, warnings = build_candidates(
        [FakeEntity("Ada Lovelace"), FakeEntity("Charles Babbage")],
        ocr_text="Ada Lovelace presented the engine",
    )
    sources = {c.name: c.sources for c in candidates}
    assert sources["Ada Lovelace"] == ("vision", "ocr")
    assert sources["Charles Babbage"] == ("vision",)
    assert warnings == []


def test_deduplicate_merges_exact_and_keeps_ambiguous():
    def make(name, type_, confidence, sources=("vision",)):
        return EntityCandidate.create(
            name=name, entity_type=type_, original_type=None,
            confidence=confidence, sources=sources,
        )

    merged, warnings = deduplicate(
        [
            make("ada lovelace", "person", None),
            make("Ada Lovelace", "person", 0.4),
            make("London", "place", 0.9),
            make("London", "event", 0.5),
        ]
    )
    assert [(c.name, c.entity_type, c.confidence) for c in merged] == [
        ("ada lovelace", "person", 0.4),  # first spelling, merged confidence
        ("London", "place", 0.9),
        ("London", "event", 0.5),
    ]
    assert len(warnings) == 1 and "Ambiguous" in warnings[0]

    ids = to_analysis_entities(merged)
    assert [e.id for e in ids] == ["ent-1", "ent-2", "ent-3"]
    assert all(e.type in ENTITY_TYPES for e in ids)


# ---------------------------------------------------------------------------
# Completion evidence: a real extraction example
# ---------------------------------------------------------------------------


def document_image() -> bytes:
    """A real, generated document-like PNG (not a byte stub)."""
    image = Image.new("RGB", (480, 160), "ivory")
    draw = ImageDraw.Draw(image)
    draw.text((24, 24), "Certificate of Ada Lovelace", fill="black")
    draw.text((24, 72), "London, 1843", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def test_real_image_end_to_end_extraction():
    """D1 (real decode) -> D2 (real parsing) -> D3 (coordinate/normalise).

    Example run of the full extraction path on a genuinely generated
    image with fixture provider responses shaped like real ones.
    """
    prepared = prepare_image(
        document_image(), filename="certificate.png", content_type="image/png"
    )
    result = await run_extraction(
        prepared,
        ocr_client=_ocr("Certificate of Ada Lovelace\nLondon, 1843"),
        vision_client=_vision(
            {
                "text": "A typed certificate naming Ada Lovelace, dated 1843.",
                "entities": [
                    entity("Ada Lovelace", "person", 0.96),
                    entity("ada lovelace", "person", 0.72),   # duplicate spelling/case
                    entity("London", "place", 0.91),
                    entity("1843", "date", 0.88),
                    entity("Punch", "object", 0.4),           # outside the vocabulary
                ],
                "relationships": [
                    {"source": "Ada Lovelace", "target": "London", "label": "located in"}
                ],
            }
        ),
    )

    assert prepared.format == "PNG"
    assert result.extracted_text == "Certificate of Ada Lovelace\nLondon, 1843"
    assert [(e.name, e.type, e.id) for e in result.entities] == [
        ("Ada Lovelace", "person", "ent-1"),
        ("London", "place", "ent-2"),
        ("1843", "date", "ent-3"),
        ("Punch", "other", "ent-4"),
    ]
    assert result.entities[0].confidence == 0.96
    assert result.candidates[0].sources == ("vision", "ocr")
    assert result.candidates[0].original_type is None
    assert any("Unrecognised entity types" in w for w in result.warnings)
    # Relationship proposals pass through untouched for D4.
    assert [r.label for r in result.relationships] == ["located in"]
    # Schema-valid for AnalysisResponse.entities.
    for entity_obj in result.entities:
        AnalysisEntity.model_validate(entity_obj.model_dump())


def test_vision_payload_passes_small_images_through_untouched():
    """Images within the size bound reach the provider byte-for-byte."""
    png = document_image()  # 480x160
    data, content_type = vision_payload(png)
    assert data == png
    assert content_type == "image/png"


def test_vision_payload_downscales_oversized_photos():
    """A phone-sized photo is fitted to the provider budget as JPEG.

    Measured on a real 4953x3302 heritage photo: full-resolution PNG =
    4.7 MB (6.4 MB base64) and it blew the request timeout; the fitted
    JPEG is ~750 KB.
    """
    photo = Image.new("RGB", (4000, 3000), (120, 90, 60))
    buffer = io.BytesIO()
    photo.save(buffer, format="PNG")
    original = buffer.getvalue()

    data, content_type = vision_payload(original)

    assert content_type == "image/jpeg"
    assert len(data) < len(original)
    reloaded = Image.open(io.BytesIO(data))
    assert max(reloaded.size) <= VISION_MAX_DIMENSION
    assert reloaded.format == "JPEG"


def test_vision_payload_reencodes_heavy_pngs_within_the_pixel_bound():
    """Incompressible photos can exceed the byte bound while small in
    pixels (measured: 1323x2000 photo -> 2.0 MB PNG); they are re-encoded
    as JPEG instead of being shipped as a multi-megabyte payload."""
    import os

    noisy = Image.frombytes("RGB", (1600, 1600), os.urandom(1600 * 1600 * 3))
    buffer = io.BytesIO()
    noisy.save(buffer, format="PNG")
    original = buffer.getvalue()
    assert max(noisy.size) <= VISION_MAX_DIMENSION  # within the pixel bound
    assert len(original) > VISION_MAX_PNG_BYTES  # but over the byte bound

    data, content_type = vision_payload(original)

    assert content_type == "image/jpeg"
    assert len(data) < len(original)
    assert Image.open(io.BytesIO(data)).format == "JPEG"


# ---------------------------------------------------------------------------
# D5 — processing boundaries and provider-failure handling at the orchestration seam
# ---------------------------------------------------------------------------


def _status_client(status: int, *, headers: dict | None = None, body: bytes = b"{}"):
    """Fixture client answering a raw HTTP status (provider failure)."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_vision_auth_failure_propagates_from_extraction():
    """A 401 fails the extraction — it is never downgraded to a warning."""
    with pytest.raises(ProviderAuthenticationError):
        await run(ocr_text="some text", vision_client=_status_client(401))


async def test_ocr_rate_limit_propagates_from_extraction():
    with pytest.raises(ProviderRateLimitError) as info:
        await run(ocr_client=_status_client(429, headers={"Retry-After": "4"}))

    assert info.value.retry_after == 4.0


async def test_contract_boundary_entity_name_and_confidence_survive():
    """Max-length name (512) and confidence endpoints stay exactly as given."""
    longest = "x" * 512
    result = await run(
        ocr_text="",
        vision={
            "entities": [
                entity(longest, "person", 1.0),
                entity("Ada", "person", 0.0),
            ]
        },
    )

    assert [e.id for e in result.entities] == ["ent-1", "ent-2"]
    assert result.entities[0].name == longest
    assert result.entities[0].confidence == 1.0
    assert result.entities[1].confidence == 0.0  # 0.0 is reported, not "unreported"
    for entity_obj in result.entities:
        AnalysisEntity.model_validate(entity_obj.model_dump())


async def test_over_long_entity_name_is_schema_rejected_and_degrades():
    """513 chars violates the frozen schema: malformed output → warning, not crash."""
    result = await run(
        ocr_text="legible text survives",
        vision={"entities": [entity("y" * 513, "person", 0.5)]},
    )

    assert result.extracted_text == "legible text survives"
    assert result.entities == []
    assert any("unusable response" in w for w in result.warnings)
