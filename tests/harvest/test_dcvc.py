"""
Tests for harvest/dcvc.py — DcvcHarvester.

All tests use saved HTML fixtures (tests/fixtures/dcvc/).
Zero live HTTP calls. Fixtures reflect live-site structure as of 2026-05-02.

Tests:
  1. parse_cards: correct count — 5 cards in fixture → 5 records
  2. parse_cards: name from aria-label, description from p.company-card__desc
  3. parse_cards: sectors from data-sector → human-readable tags list
  4. parse_cards: exit status in extra["status"] for exit company
  5. full harvest (mocked): record count, source field, tags populated
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from harvest.dcvc import DcvcHarvester, _parse_sectors, _strip_all_token

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "dcvc"


def _html(filename: str) -> str:
    return (_FIXTURES / filename).read_text(encoding="utf-8")


@pytest.fixture
def harvester() -> DcvcHarvester:
    from utils.rate_limiter import RateLimiter
    return DcvcHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


def _mock_resp(html: str, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.text = html
    m.status_code = status
    m.raise_for_status = MagicMock()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Card count — fixture has 5 articles
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_cards_count(harvester: DcvcHarvester) -> None:
    """Fixture has 5 article.company-card elements; should yield 5 records."""
    soup = BeautifulSoup(_html("companies.html"), "lxml")
    records = harvester._parse_cards(soup)
    assert len(records) == 5, f"Expected 5 records, got {len(records)}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Name from aria-label, description from p.company-card__desc
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_cards_aclima_name_and_description(harvester: DcvcHarvester) -> None:
    """Aclima card should have name from aria-label and description from p.company-card__desc."""
    soup = BeautifulSoup(_html("companies.html"), "lxml")
    records = harvester._parse_cards(soup)
    aclima = next((r for r in records if r.name == "Aclima"), None)

    assert aclima is not None, "Aclima not found"
    assert aclima.description == "Like Google Street View, for air pollution"
    assert aclima.source == "DCVC"
    assert aclima.source_url == "https://www.dcvc.com/companies/aclima"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Sectors from data-sector → human-readable tags
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_sectors_single() -> None:
    """Single sector slug converted to title-cased human-readable tag."""
    tags = _parse_sectors("all,climate-tech")
    assert tags == ["Climate Tech"]


def test_parse_sectors_multi() -> None:
    """Multiple sector slugs all converted."""
    tags = _parse_sectors("all,climate-tech,industrial-transformation")
    assert "Climate Tech" in tags
    assert "Industrial Transformation" in tags
    assert len(tags) == 2


def test_parse_cards_aim_has_two_sectors(harvester: DcvcHarvester) -> None:
    """AIM Intelligent Machines has data-sector with two sectors → two tags."""
    soup = BeautifulSoup(_html("companies.html"), "lxml")
    records = harvester._parse_cards(soup)
    aim = next((r for r in records if "AIM" in r.name), None)

    assert aim is not None, "AIM Intelligent Machines not found"
    assert "Climate Tech" in aim.tags
    assert "Industrial Transformation" in aim.tags


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Exit status stored in extra["status"]
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_cards_exit_status_in_extra(harvester: DcvcHarvester) -> None:
    """AbCellera is an exit; extra['status'] should be 'exits'."""
    soup = BeautifulSoup(_html("companies.html"), "lxml")
    records = harvester._parse_cards(soup)
    abcellera = next((r for r in records if r.name == "AbCellera"), None)

    assert abcellera is not None, "AbCellera not found"
    assert abcellera.extra.get("status") == "exits"
    assert abcellera.extra.get("fund") == "featured,dcvcBio"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Full harvest — count, source field, tags populated
# ─────────────────────────────────────────────────────────────────────────────

def test_full_harvest_count_source_tags(harvester: DcvcHarvester) -> None:
    """Full harvest with fixture: 5 records, source='DCVC', tags populated."""
    with patch(
        "harvest.dcvc.requests.get",
        return_value=_mock_resp(_html("companies.html")),
    ):
        records = harvester.fetch()

    assert len(records) == 5, f"Expected 5 records, got {len(records)}"

    for rec in records:
        assert rec.source == "DCVC"
        assert rec.name, f"Record has empty name: {rec}"
        assert isinstance(rec.tags, list)

    # At least some records should have tags
    records_with_tags = [r for r in records if r.tags]
    assert len(records_with_tags) >= 4, (
        f"Expected most records to have tags, only {len(records_with_tags)}/5 do"
    )
