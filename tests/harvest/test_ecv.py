"""
Tests for harvest/ecv.py — EnergyCapitalVenturesHarvester.

All tests use saved HTML fixtures (tests/fixtures/ecv/).
Zero live HTTP calls. Fixtures fetched 2026-05-01.

Tests:
  1. Index parse: 12 slugs extracted across Fund I and Fund II
  2. Fund detection: Fund I and Fund II correctly identified
  3. Full harvest: 12 records, all with name + source_url
  4. Detail fields: graphitic record has expected name, location, founders
  5. Selector drift: empty index page → warning + empty list
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from harvest.ecv import EnergyCapitalVenturesHarvester

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "ecv"


def _html(filename: str) -> str:
    return (_FIXTURES / filename).read_text(encoding="utf-8")


@pytest.fixture
def harvester() -> EnergyCapitalVenturesHarvester:
    from utils.rate_limiter import RateLimiter
    return EnergyCapitalVenturesHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


def _mock_resp(html: str) -> MagicMock:
    m = MagicMock()
    m.text = html
    m.raise_for_status = MagicMock()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Index parse — slug count
# ─────────────────────────────────────────────────────────────────────────────

def test_index_extracts_twelve_slugs(harvester: EnergyCapitalVenturesHarvester) -> None:
    """Portfolio index should yield 12 (slug, fund) pairs."""
    with patch("harvest.ecv.requests.get", return_value=_mock_resp(_html("portfolio.html"))):
        slugs = harvester._fetch_index()

    assert len(slugs) == 12, f"Expected 12 slugs, got {len(slugs)}: {[s for s,_ in slugs]}"
    slug_set = {s for s, _ in slugs}
    assert "graphitic" in slug_set
    assert "capture-6" in slug_set


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Fund detection
# ─────────────────────────────────────────────────────────────────────────────

def test_fund_detection_correct(harvester: EnergyCapitalVenturesHarvester) -> None:
    """Fund I and Fund II blocks should be correctly labelled."""
    with patch("harvest.ecv.requests.get", return_value=_mock_resp(_html("portfolio.html"))):
        slugs = harvester._fetch_index()

    fund_map = {slug: fund for slug, fund in slugs}

    # Graphitic confirmed in Fund I during inspection
    assert fund_map.get("graphitic") == "I", (
        f"Expected graphitic in Fund I, got {fund_map.get('graphitic')!r}"
    )
    # carbonquest, enadyne, capture-6 confirmed in Fund II
    for slug in ("carbonquest", "enadyne", "capture-6"):
        assert fund_map.get(slug) == "II", (
            f"Expected {slug!r} in Fund II, got {fund_map.get(slug)!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Full harvest — record count and required fields
# ─────────────────────────────────────────────────────────────────────────────

def test_full_harvest_twelve_records_with_required_fields(
    harvester: EnergyCapitalVenturesHarvester,
) -> None:
    """Full harvest should return 12 records, each with name and source_url."""
    portfolio_html = _html("portfolio.html")
    # Use the graphitic fixture for all detail pages (structure is identical)
    detail_html = _html("detail_graphitic.html")

    def fake_get(url, **kwargs):
        if url.endswith("/portfolio"):
            return _mock_resp(portfolio_html)
        return _mock_resp(detail_html)

    with patch("harvest.ecv.requests.get", side_effect=fake_get):
        records = harvester.fetch()

    assert len(records) == 12, f"Expected 12 records, got {len(records)}"
    for rec in records:
        assert rec.name, f"Record missing name: {rec}"
        assert rec.source == "Energy Capital Ventures"
        assert rec.source_url and rec.source_url.startswith("https://energycapitalventures.com/portfolio/")


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Detail field extraction — graphitic fixture
# ─────────────────────────────────────────────────────────────────────────────

def test_graphitic_detail_fields(harvester: EnergyCapitalVenturesHarvester) -> None:
    """Graphitic detail page should parse all known fields correctly."""
    record = harvester._fetch_detail("graphitic", "I")
    # _fetch_detail makes a real HTTP call when not patched, so we need to patch here
    # Re-implement by patching requests.get
    with patch(
        "harvest.ecv.requests.get",
        return_value=_mock_resp(_html("detail_graphitic.html")),
    ):
        record = harvester._fetch_detail("graphitic", "I")

    assert record is not None
    assert record.name == "Graphitic Energy"
    assert record.location_raw == "Santa Barbara, CA"
    assert record.extra.get("fund") == "I"
    assert record.extra.get("investment_date") == "August 28, 2024"

    founders = record.extra.get("founders", [])
    assert isinstance(founders, list)
    assert len(founders) >= 2, f"Expected ≥2 founders, got {founders}"
    assert any("Jones" in f for f in founders), f"Expected 'Jones' in founders: {founders}"

    assert record.description is not None and len(record.description) > 20


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Selector drift — empty index returns empty list + warning
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_index_returns_empty_list(
    harvester: EnergyCapitalVenturesHarvester,
    caplog,
) -> None:
    """Empty portfolio index (selector drift) should return [] and log a warning."""
    empty_html = "<html><body><p>Portfolio coming soon.</p></body></html>"

    with patch("harvest.ecv.requests.get", return_value=_mock_resp(empty_html)):
        import logging
        with caplog.at_level(logging.WARNING, logger="harvest.ecv"):
            records = harvester.fetch()

    assert records == []
    assert any(
        "no-slugs" in r.message or "portfolios-hero-link" in r.message
        for r in caplog.records
    ), f"Expected no-slugs warning; got: {[r.message for r in caplog.records]}"
