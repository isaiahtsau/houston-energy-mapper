"""
Tests for harvest/bev_portfolio.py — BevPortfolioHarvester.

All tests use a saved HTML fixture (tests/fixtures/bev_portfolio/portfolio.html).
Zero live HTTP calls. Fixture contains 5 company objects in window.__INITIAL_STATE__.

Tests:
  1. _find_companies: 5 company objects found in parsed __INITIAL_STATE__
  2. _to_record: name, description (HTML stripped), website, source, source_url
  3. _to_record: description HTML tags stripped to plain text
  4. _to_record: tags from both sector tags and technologies (deduplicated)
  5. full harvest (mocked): record count, source field, all records have names
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harvest.bev_portfolio import (
    BevPortfolioHarvester,
    _extract_state_json,
    _find_companies,
    _strip_html,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "bev_portfolio"


def _html(filename: str = "portfolio.html") -> str:
    return (_FIXTURES / filename).read_text(encoding="utf-8")


@pytest.fixture
def harvester() -> BevPortfolioHarvester:
    from utils.rate_limiter import RateLimiter
    return BevPortfolioHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


def _mock_resp(html: str, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.text = html
    m.status_code = status
    m.raise_for_status = MagicMock()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Company count — 5 company objects in fixture
# ─────────────────────────────────────────────────────────────────────────────

def test_find_companies_count() -> None:
    """Fixture __INITIAL_STATE__ has 5 company objects; should yield 5 dicts."""
    state = _extract_state_json(_html())
    assert state is not None, "_extract_state_json returned None"
    companies = _find_companies(state)
    assert len(companies) == 5, f"Expected 5 companies, got {len(companies)}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Fields — name, website, source, source_url
# ─────────────────────────────────────────────────────────────────────────────

def test_to_record_fields(harvester: BevPortfolioHarvester) -> None:
    """Antora Energy card should have correct name, website, source, source_url."""
    state = _extract_state_json(_html())
    companies = _find_companies(state)
    antora_obj = next(
        (c for c in companies
         if (c.get("elements") or {}).get("title", {}).get("value") == "Antora Energy"),
        None,
    )
    assert antora_obj is not None, "Antora Energy not found in fixture"

    rec = harvester._to_record(antora_obj)
    assert rec is not None
    assert rec.name == "Antora Energy"
    assert rec.website == "https://www.antoraenergy.com"
    assert rec.source == "Breakthrough Energy Ventures"
    assert rec.source_url == "https://www.breakthroughenergy.org/portfolio"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: HTML stripped from description
# ─────────────────────────────────────────────────────────────────────────────

def test_description_html_stripped(harvester: BevPortfolioHarvester) -> None:
    """44.01 description contains <sub>2</sub>; stripped text must not contain HTML tags."""
    state = _extract_state_json(_html())
    companies = _find_companies(state)
    c4401 = next(
        (c for c in companies
         if (c.get("elements") or {}).get("title", {}).get("value") == "44.01"),
        None,
    )
    assert c4401 is not None, "44.01 not found in fixture"

    rec = harvester._to_record(c4401)
    assert rec is not None
    assert rec.description is not None
    assert "<" not in rec.description, f"HTML tag found in description: {rec.description!r}"
    assert "CO" in rec.description


def test_strip_html_standalone() -> None:
    """_strip_html removes tags and collapses whitespace."""
    result = _strip_html("<b>Hello</b> <i>world</i>")
    assert result == "Hello world"
    assert "<" not in result


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Tags from sectors + technologies, deduplicated
# ─────────────────────────────────────────────────────────────────────────────

def test_tags_combined_and_deduplicated(harvester: BevPortfolioHarvester) -> None:
    """Noon Energy has 2 sector tags + 1 tech tag → 3 unique tags total."""
    state = _extract_state_json(_html())
    companies = _find_companies(state)
    noon = next(
        (c for c in companies
         if (c.get("elements") or {}).get("title", {}).get("value") == "Noon Energy"),
        None,
    )
    assert noon is not None, "Noon Energy not found in fixture"

    rec = harvester._to_record(noon)
    assert rec is not None
    assert "Energy storage" in rec.tags
    assert "Grid" in rec.tags
    assert "Long duration storage" in rec.tags
    assert len(rec.tags) == 3

    # No duplicates
    assert len(rec.tags) == len(set(rec.tags))


def test_tags_sector_plus_tech(harvester: BevPortfolioHarvester) -> None:
    """CFS has 1 sector tag (Fusion) + 1 tech tag (Nuclear fusion) → 2 total."""
    state = _extract_state_json(_html())
    companies = _find_companies(state)
    cfs = next(
        (c for c in companies
         if (c.get("elements") or {}).get("title", {}).get("value") == "Commonwealth Fusion Systems"),
        None,
    )
    assert cfs is not None, "CFS not found in fixture"
    rec = harvester._to_record(cfs)
    assert rec is not None
    assert "Fusion" in rec.tags
    assert "Nuclear fusion" in rec.tags
    assert len(rec.tags) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Full harvest — count, source, all names populated
# ─────────────────────────────────────────────────────────────────────────────

def test_full_harvest_count_source_names(harvester: BevPortfolioHarvester) -> None:
    """Full harvest with fixture: 5 records, source='Breakthrough Energy Ventures'."""
    with patch(
        "harvest.bev_portfolio.requests.get",
        return_value=_mock_resp(_html()),
    ):
        records = harvester.fetch()

    assert len(records) == 5, f"Expected 5 records, got {len(records)}"

    for rec in records:
        assert rec.source == "Breakthrough Energy Ventures"
        assert rec.name, f"Record has empty name: {rec}"
        assert isinstance(rec.tags, list)
        assert rec.extra.get("bev_codename") is not None
