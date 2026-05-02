"""
Tests for harvest/rbpc_alumni.py — RbpcAlumniHarvester.

All tests use saved HTML fixtures (tests/fixtures/rbpc_alumni/).
Zero live HTTP calls. Fixtures reflect live-site structure as of 2026-05-02.

Tests:
  1. parse_featured_alumni: 3 mosaic cards → names, years, placements
  2. parse_startups_table: 8 rows from 2025 fixture → 8 records with website
  3. parse_startups_table: row without website link → website=None
  4. full harvest: deduplication — Owlet in featured-alumni + 2025 table → counted once
  5. full harvest: 404 year skipped gracefully — pipeline continues with remaining years
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harvest.rbpc_alumni import RbpcAlumniHarvester, _normalize_name, _parse_year

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "rbpc_alumni"


def _html(filename: str) -> str:
    return (_FIXTURES / filename).read_text(encoding="utf-8")


@pytest.fixture
def harvester() -> RbpcAlumniHarvester:
    from utils.rate_limiter import RateLimiter
    return RbpcAlumniHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


def _mock_resp(html: str, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.text = html
    m.status_code = status
    m.raise_for_status = MagicMock()
    return m


def _mock_404() -> MagicMock:
    import requests
    m = MagicMock()
    m.status_code = 404
    m.raise_for_status.side_effect = requests.HTTPError("404")
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Featured alumni — 3 cards, names/years/placements extracted
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_featured_alumni_names_years_placements(
    harvester: RbpcAlumniHarvester,
) -> None:
    """Fixture has 3 cards: Owlet (2013, Finalist), Lilac Solutions (2017, Competitor),
    Hyliion (2015, Finalist).
    """
    records = harvester._parse_featured_alumni(_html("featured_alumni.html"))

    assert len(records) == 3, f"Expected 3 alumni records, got {len(records)}"

    names = {r.name for r in records}
    assert "Owlet" in names
    assert "Lilac Solutions" in names
    assert "Hyliion" in names

    owlet = next(r for r in records if r.name == "Owlet")
    assert owlet.extra["competition_year"] == 2013
    assert owlet.extra["placement"] == "Finalist"
    assert owlet.source == "RBPC Alumni"
    assert owlet.extra["page"] == "featured-alumni"

    lilac = next(r for r in records if r.name == "Lilac Solutions")
    assert lilac.extra["competition_year"] == 2017
    assert lilac.extra["placement"] == "Competitor"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Startups table — 8 rows, correct names and websites
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_startups_table_count_and_fields(
    harvester: RbpcAlumniHarvester,
) -> None:
    """2025 fixture has 8 startup rows; all should have names, most have websites."""
    records = harvester._parse_startups_table(_html("startups_2025.html"), year=2025)

    assert len(records) == 8, f"Expected 8 records from 2025 table, got {len(records)}"

    names = {r.name for r in records}
    assert "3rd-i" in names
    assert "CarbonQuest" in names
    assert "Xatoms" in names

    third_i = next(r for r in records if r.name == "3rd-i")
    assert third_i.website == "https://3rd-i.org"
    assert third_i.extra["competition_year"] == 2025
    assert third_i.extra["university"] == "University of Miami"
    assert third_i.source_url == "https://rbpc.rice.edu/2025/startups"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Missing website link → website=None
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_startups_table_missing_website(
    harvester: RbpcAlumniHarvester,
) -> None:
    """'Arcticedge Technologies' row has no website link; website should be None."""
    records = harvester._parse_startups_table(_html("startups_2025.html"), year=2025)
    arctic = next(
        (r for r in records if r.name == "Arcticedge Technologies"), None
    )
    assert arctic is not None, "Arcticedge Technologies not found"
    assert arctic.website is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Full harvest deduplicates across pages
# ─────────────────────────────────────────────────────────────────────────────

def test_full_harvest_deduplicates_across_pages(
    harvester: RbpcAlumniHarvester,
) -> None:
    """If 'Owlet' appears in both featured-alumni and a year table, it should
    appear only once in the final records (featured-alumni record takes precedence).
    """
    # Build a 2025 fixture that also contains Owlet
    startups_with_owlet = _html("startups_2025.html").replace(
        "<tr><td>3rd-i</td>",
        "<tr><td>Owlet</td><td>Brigham Young University</td><td></td></tr>\n"
        "<tr><td>3rd-i</td>",
    )
    featured_html = _html("featured_alumni.html")

    def fake_get(url: str, **kwargs):
        if "featured-alumni" in url:
            return _mock_resp(featured_html)
        if "2025/startups" in url:
            return _mock_resp(startups_with_owlet)
        return _mock_404()

    with patch("harvest.rbpc_alumni.requests.get", side_effect=fake_get):
        records = harvester.fetch()

    owlet_records = [r for r in records if r.name == "Owlet"]
    assert len(owlet_records) == 1, (
        f"Expected 1 Owlet record, got {len(owlet_records)}"
    )
    # The featured-alumni record has the page field set to "featured-alumni"
    assert owlet_records[0].extra["page"] == "featured-alumni"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: 404 year skipped gracefully, pipeline continues
# ─────────────────────────────────────────────────────────────────────────────

def test_full_harvest_404_year_skipped_gracefully(
    harvester: RbpcAlumniHarvester,
) -> None:
    """If a year page 404s, that year is skipped; other years still harvested."""
    featured_html = _html("featured_alumni.html")
    startups_html = _html("startups_2025.html")

    def fake_get(url: str, **kwargs):
        if "featured-alumni" in url:
            return _mock_resp(featured_html)
        if "/2025/" in url:
            return _mock_resp(startups_html)
        # All other years return 404
        return _mock_404()

    with patch("harvest.rbpc_alumni.requests.get", side_effect=fake_get):
        records = harvester.fetch()

    # Should have 3 featured alumni + 8 from 2025 = 11 (assuming no overlap)
    assert len(records) >= 3 + 8, (
        f"Expected at least 11 records, got {len(records)}"
    )

    # 2025 records should be present
    names = {r.name for r in records}
    assert "CarbonQuest" in names
    assert "FluxGrid Energy" in names
