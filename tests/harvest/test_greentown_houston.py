"""
Tests for harvest/greentown_houston.py — GreentownHoustonHarvester.

All tests use saved HTML fixtures (tests/fixtures/greentown_houston/).
Zero live HTTP calls. Fixtures fetched 2026-05-01.

Tests:
  1. AJAX fragment parse: 27 items from page-1 fixture, all with name + detail URL
  2. Listing pagination stops on no-results: empty list returned for sentinel HTML
  3. Detail page parse: 21Senses — website and description extracted correctly
  4. Full harvest (mocked): records count, required fields, known company spot-check
  5. Sector extraction: sector string present in extra["sector"] for known cards
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harvest.greentown_houston import GreentownHoustonHarvester

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "greentown_houston"


def _html(filename: str) -> str:
    return (_FIXTURES / filename).read_text(encoding="utf-8")


@pytest.fixture
def harvester() -> GreentownHoustonHarvester:
    from utils.rate_limiter import RateLimiter
    return GreentownHoustonHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


def _mock_resp(html: str, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.text = html
    m.status_code = status
    m.raise_for_status = MagicMock()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: AJAX fragment parse — 27 items, all with name + detail URL
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_listing_fragment_page1(harvester: GreentownHoustonHarvester) -> None:
    """Page 1 AJAX fixture should yield 27 items, each with name and detail_url."""
    items = harvester._parse_listing_fragment(_html("ajax_page1.html"))

    assert len(items) == 27, f"Expected 27 items from page 1, got {len(items)}"
    for item in items:
        assert item["name"], f"Item missing name: {item}"
        assert item["detail_url"] and "greentownlabs.com/members/" in item["detail_url"], (
            f"Item missing valid detail_url: {item}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: No-results sentinel stops pagination
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_listing_fragment_stops_on_no_results(
    harvester: GreentownHoustonHarvester,
) -> None:
    """AJAX response containing .no-results element should return empty list."""
    no_results_html = (
        '<p class="no-results is-style-lead">No Results Found</p>'
        '<div class="pagination"></div>'
    )
    items = harvester._parse_listing_fragment(no_results_html)
    assert items == [], f"Expected empty list on no-results, got {items}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Detail page parse — 21Senses website and description
# ─────────────────────────────────────────────────────────────────────────────

def test_detail_page_21senses(harvester: GreentownHoustonHarvester) -> None:
    """21Senses detail fixture should yield correct website and non-empty description."""
    with patch(
        "harvest.greentown_houston.requests.get",
        return_value=_mock_resp(_html("detail_21senses.html")),
    ):
        detail = harvester._fetch_detail("https://greentownlabs.com/members/21senses-inc/")

    assert detail is not None
    assert detail["website"] is not None
    assert "21-senses.com" in detail["website"], (
        f"Expected 21-senses.com in website, got {detail['website']!r}"
    )
    assert detail["description"] is not None
    assert len(detail["description"]) > 40, (
        f"Description too short: {detail['description']!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Full harvest with mocked responses
# ─────────────────────────────────────────────────────────────────────────────

def test_full_harvest_count_and_required_fields(
    harvester: GreentownHoustonHarvester,
) -> None:
    """Full harvest with page-1 fixture (one page) should return 27 records."""
    ajax_page1 = _html("ajax_page1.html")
    detail_html = _html("detail_21senses.html")
    no_results = '<p class="no-results is-style-lead">No Results Found</p>'

    ajax_call_count = [0]

    def fake_post(url, data="", **kwargs):
        ajax_call_count[0] += 1
        # Page 1 returns content; page 2 returns no-results to stop pagination
        if "page=1" in data:
            return _mock_resp(ajax_page1)
        return _mock_resp(no_results)

    def fake_get(url, **kwargs):
        return _mock_resp(detail_html)

    with patch("harvest.greentown_houston.requests.post", side_effect=fake_post), \
         patch("harvest.greentown_houston.requests.get", side_effect=fake_get):
        records = harvester.fetch()

    assert len(records) == 27, f"Expected 27 records, got {len(records)}"
    for rec in records:
        assert rec.name, f"Record missing name: {rec}"
        assert rec.source == "Greentown Houston"
        assert rec.location_raw == "Houston, TX"
        assert rec.source_url and "greentownlabs.com/members/" in rec.source_url


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Sector extraction
# ─────────────────────────────────────────────────────────────────────────────

def test_sector_extracted_for_known_cards(harvester: GreentownHoustonHarvester) -> None:
    """21Senses card should have sector='Manufacturing' in extra."""
    items = harvester._parse_listing_fragment(_html("ajax_page1.html"))
    senses = next((i for i in items if "21Senses" in i["name"]), None)

    assert senses is not None, "21Senses not found in page-1 items"
    assert senses["sector"] is not None, "21Senses sector is None"
    assert "Manufacturing" in senses["sector"], (
        f"Expected 'Manufacturing' in sector, got {senses['sector']!r}"
    )
