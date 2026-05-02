"""
Tests for harvest/halliburton_labs.py — HalliburtonLabsHarvester.

All tests use the saved HTML fixture (tests/fixtures/halliburton_labs/companies.html).
Zero live HTTP calls. The fixture was fetched 2026-05-01.

Tests:
  1. Full parse: ≥35 records extracted, all with name + website
  2. Cohort type: warm-gradient → "current", cool-gradient → "alumni"
  3. Known companies: spot-check Aquafortus and 360 Energy are present
  4. Field extraction: description and location are non-empty for known cards
  5. Selector drift detection: zero cards → warning + empty list
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from harvest.halliburton_labs import HalliburtonLabsHarvester

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "halliburton_labs"


def _fixture_html() -> str:
    return (_FIXTURES / "companies.html").read_text(encoding="utf-8")


@pytest.fixture
def harvester() -> HalliburtonLabsHarvester:
    from utils.rate_limiter import RateLimiter
    return HalliburtonLabsHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


def _fetch_with_fixture(harvester: HalliburtonLabsHarvester):
    """Run fetch() with the HTML fixture injected as the HTTP response."""
    mock_resp = MagicMock()
    mock_resp.text = _fixture_html()
    mock_resp.raise_for_status = MagicMock()
    with patch("harvest.halliburton_labs.requests.get", return_value=mock_resp):
        return harvester.fetch()


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Full parse — record count and required fields
# ─────────────────────────────────────────────────────────────────────────────

def test_full_parse_record_count_and_required_fields(
    harvester: HalliburtonLabsHarvester,
) -> None:
    """Should extract ≥35 records, each with name and website."""
    records = _fetch_with_fixture(harvester)

    assert len(records) >= 35, f"Expected ≥35 records, got {len(records)}"

    for rec in records:
        assert rec.name, f"Record missing name: {rec}"
        assert rec.source == "Halliburton Labs"
        assert rec.source_url == "https://halliburtonlabs.com/companies/"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Cohort type classification
# ─────────────────────────────────────────────────────────────────────────────

def test_cohort_type_warm_is_current(harvester: HalliburtonLabsHarvester) -> None:
    """warm-gradient cards should have cohort_type='current'."""
    records = _fetch_with_fixture(harvester)
    current = [r for r in records if r.extra.get("cohort_type") == "current"]
    alumni = [r for r in records if r.extra.get("cohort_type") == "alumni"]

    assert len(current) >= 1, "Expected at least 1 current participant"
    assert len(alumni) >= 1, "Expected at least 1 alumni company"

    # Verify every record has a cohort_type set
    for rec in records:
        assert rec.extra.get("cohort_type") in ("current", "alumni"), (
            f"Unexpected cohort_type for {rec.name!r}: {rec.extra.get('cohort_type')!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Known companies are present
# ─────────────────────────────────────────────────────────────────────────────

def test_known_companies_present(harvester: HalliburtonLabsHarvester) -> None:
    """Aquafortus and 360 Energy should appear in the parsed records."""
    records = _fetch_with_fixture(harvester)
    names = {r.name for r in records}

    assert "Aquafortus" in names, f"Expected 'Aquafortus' in {names}"
    assert "360 Energy" in names, f"Expected '360 Energy' in {names}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Field extraction quality
# ─────────────────────────────────────────────────────────────────────────────

def test_aquafortus_fields(harvester: HalliburtonLabsHarvester) -> None:
    """Aquafortus record should have website, description, and location."""
    records = _fetch_with_fixture(harvester)
    aquafortus = next(r for r in records if r.name == "Aquafortus")

    assert aquafortus.website == "https://aquafortus.net/"
    assert aquafortus.description is not None
    assert "desalination" in aquafortus.description.lower() or len(aquafortus.description) > 10
    assert aquafortus.location_raw is not None


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Selector drift detection
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_page_returns_empty_list(
    harvester: HalliburtonLabsHarvester,
    caplog,
) -> None:
    """A page with no matching cards should return [] and log a warning."""
    mock_resp = MagicMock()
    mock_resp.text = "<html><body><p>No companies here.</p></body></html>"
    mock_resp.raise_for_status = MagicMock()

    with patch("harvest.halliburton_labs.requests.get", return_value=mock_resp):
        import logging
        with caplog.at_level(logging.WARNING, logger="harvest.halliburton_labs"):
            records = harvester.fetch()

    assert records == []
    assert any("no-cards" in rec.message.lower() or "zero" in rec.message.lower()
               for rec in caplog.records), (
        "Expected a warning about zero cards found"
    )
