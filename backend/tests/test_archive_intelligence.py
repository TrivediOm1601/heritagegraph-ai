"""Text intelligence and storage safety for the archive pipeline.

These tests pin the properties that make the archive honest:

* an uncertain date is never turned into an exact year;
* BCE and CE ordering is numerically correct (negative years first);
* an entity candidate always knows the page and sentence it came from;
* a storage key can never escape the storage root.
"""

from __future__ import annotations

import pytest

from app.services.archive_text_service import (
    extract_entity_mentions,
    parse_date_mentions,
)
from app.services.storage_service import (
    StorageError,
    read_bytes,
    reset_storage_root,
    save_bytes,
)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def test_approximate_year_keeps_its_precision_and_signed_year():
    [event] = parse_date_mentions("The stepwell was completed c. 1020 CE.")
    assert event.date_text.startswith("c. 1020")
    assert event.year_start == 1020
    assert event.precision == "approximate"
    assert event.era == "ce"
    assert "c. 1020" in event.sentence


def test_bce_century_is_a_range_not_an_exact_year():
    [event] = parse_date_mentions("The stupa was enlarged in the 3rd century BCE.")
    assert event.precision == "century"
    assert event.era == "bce"
    assert event.year_start == -300
    assert event.year_end == -201


def test_ce_century_uses_the_correct_bounds():
    [event] = parse_date_mentions("Construction began in the 11th century CE.")
    assert (event.year_start, event.year_end) == (1001, 1100)
    assert event.precision == "century"


def test_century_without_an_era_stays_unknown():
    [event] = parse_date_mentions("The temple was rebuilt in the 12th century.")
    assert event.era == "unknown"
    assert event.precision == "century"


def test_range_keeps_both_years():
    [event] = parse_date_mentions("The site flourished between 1000 and 1025 CE.")
    assert event.precision == "range"
    assert (event.year_start, event.year_end) == (1000, 1025)


def test_bare_year_needs_a_dating_verb():
    assert parse_date_mentions("A total of 1485 visitors attended.") == []
    [event] = parse_date_mentions("The stepwell was built in 1485.")
    assert event.year_start == 1485
    assert event.precision == "exact"


def test_events_are_ordered_with_bce_first():
    text = "Built in 1485 CE. An earlier structure of the 3rd century BCE was found below it."
    events = parse_date_mentions(text)
    assert [event.era for event in events] == ["bce", "ce"]
    assert events[0].year_start < events[-1].year_start


def test_a_range_is_not_also_reported_as_two_exact_years():
    events = parse_date_mentions("Between 1000 and 1025 CE the town expanded.")
    assert len(events) == 1
    assert events[0].precision == "range"


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


def test_gazetteer_entity_keeps_page_and_supporting_sentence():
    mentions = extract_entity_mentions(
        [(2, "Rani ki Vav was commissioned in the 11th century.")],
        {"rani ki vav": ("Rani ki Vav", "place")},
    )
    match = next(item for item in mentions if item.name == "Rani ki Vav")
    assert match.entity_type == "place"
    assert match.page_number == 2
    assert match.method == "gazetteer"
    assert match.confidence == pytest.approx(0.9)
    assert "Rani ki Vav" in match.excerpt


def test_dates_become_date_entities():
    mentions = extract_entity_mentions([(1, "Built in 1499 CE by Ruda Devi.")], {})
    dates = [item for item in mentions if item.entity_type == "date"]
    assert dates and dates[0].name.startswith("1499")
    assert dates[0].method == "date"


def test_heuristic_names_are_low_confidence_and_never_duplicated():
    mentions = extract_entity_mentions(
        [(1, "Adalaj Stepwell and Modhera Sun Temple are in Gujarat.")], {}
    )
    names = [(item.name, item.entity_type) for item in mentions]
    assert len(names) == len(set(names))
    heuristic = [item for item in mentions if item.method == "heuristic"]
    assert heuristic
    assert all(item.confidence <= 0.5 for item in heuristic)


def test_entities_are_capped_per_page():
    text = " ".join(f"Name{index} Place" for index in range(200))
    mentions = extract_entity_mentions([(1, text)], {}, max_per_page=10)
    assert len(mentions) <= 10


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("HG_STORAGE_DIR", str(tmp_path / "store"))
    reset_storage_root()
    yield
    reset_storage_root()


def test_saved_bytes_round_trip_and_are_content_addressed(storage):
    first = save_bytes(b"Rani ki Vav")
    second = save_bytes(b"Rani ki Vav")

    assert first.sha256 == second.sha256
    assert first.storage_key == second.storage_key
    assert first.storage_key.endswith(first.sha256)
    assert read_bytes(first.storage_key) == b"Rani ki Vav"


def test_storage_key_cannot_escape_the_root(storage):
    with pytest.raises(StorageError):
        read_bytes("../../etc/passwd")
    with pytest.raises(StorageError):
        read_bytes("C:/Windows/win.ini")
    with pytest.raises(StorageError):
        read_bytes("aa/nothex")
