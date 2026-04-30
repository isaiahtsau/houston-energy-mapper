"""
Tests for harvest/rice_etvf.py — RiceEtvfHarvester.

All tests are fixture-based (saved HTML snapshots). Zero live HTTP calls.
Fixtures are saved in tests/fixtures/rice_etvf/ from real ETVF pages
(fetched 2026-04-30).

Tests:
  1. 2024 grid card format parsing (_parse_grid_listing)
  2. 2022 text-list format parsing — Pattern A (ul.links-container, direct URLs)
  3. 2023 text-list format parsing — Pattern B (wysiwyg, LinkedIn links)
  4. Profile field extraction (_extract_profile)
  5. Listing-only minimal record handling (_make_listing_only)
"""
from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from harvest.rice_etvf import RiceEtvfHarvester

# ─────────────────────────────────────────────────────────────────────────────
# Fixture helpers
# ─────────────────────────────────────────────────────────────────────────────

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "rice_etvf"


def _load(filename: str) -> BeautifulSoup:
    """Load a saved HTML fixture and return a BeautifulSoup object."""
    html = (_FIXTURES / filename).read_text(encoding="utf-8")
    return BeautifulSoup(html, "lxml")


@pytest.fixture
def harvester() -> RiceEtvfHarvester:
    """Harvester instance with default rate limiter (delay=0 for tests)."""
    from utils.rate_limiter import RateLimiter
    h = RiceEtvfHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))
    return h


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: 2024 grid card format parsing
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_grid_listing_2024(harvester: RiceEtvfHarvester) -> None:
    """_parse_grid_listing should extract slugs and names from 2024 card layout."""
    soup = _load("etvf_2024_companies.html")
    results = harvester._parse_grid_listing(soup, 2024)

    # 83 cards on the 2024 page
    assert len(results) >= 50, f"Expected ≥50 cards, got {len(results)}"

    # Every result must have a slug and a name, and listing_only=False
    for r in results:
        assert r["slug"], f"Missing slug in record: {r}"
        assert r["name"], f"Missing name in record: {r}"
        assert r["listing_only"] is False

    # Spot-check a known company
    slugs = {r["slug"] for r in results}
    assert "acceleware" in slugs, "Expected 'acceleware' slug in 2024 listing"

    names = {r["name"] for r in results}
    assert "Acceleware" in names or any("Acceleware" in n for n in names)

    # Slugs must be clean (no leading/trailing slashes)
    for r in results:
        assert not r["slug"].startswith("/")
        assert not r["slug"].endswith("/")


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: 2022 text-list format — Pattern A (ul.links-container, direct URLs)
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_text_listing_2022(harvester: RiceEtvfHarvester) -> None:
    """_parse_text_listing should extract 10 companies from the 2022 listing (Pattern A)."""
    soup = _load("etvf_2022_companies.html")
    results = harvester._parse_text_listing(soup, 2022)

    assert len(results) == 10, f"Expected 10 companies from 2022, got {len(results)}"

    # All 2022 results: no slug, listing_only=True
    for r in results:
        assert r["slug"] is None
        assert r["listing_only"] is True

    # 2022 companies have direct http:// website links (not LinkedIn)
    for r in results:
        assert r["website"] is not None, (
            f"2022 company '{r['name']}' should have a company website, got None"
        )
        assert r["website"].startswith("http")
        assert "linkedin.com" not in r["website"]

    # Spot-check known companies
    names = {r["name"] for r in results}
    assert "Syzygy Plasmonics" in names
    assert "Arolytics" in names

    # Websites should be company URLs
    websites = {r["website"] for r in results}
    assert "http://plasmonics.tech/" in websites
    assert "http://arolytics.com/" in websites


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: 2023 text-list format — Pattern B (wysiwyg, LinkedIn links)
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_text_listing_2023(harvester: RiceEtvfHarvester) -> None:
    """_parse_text_listing should extract 10 companies from the 2023 listing (Pattern B)."""
    soup = _load("etvf_2023_companies.html")
    results = harvester._parse_text_listing(soup, 2023)

    assert len(results) == 10, f"Expected 10 companies from 2023, got {len(results)}"

    # All 2023 results: no slug, listing_only=True
    for r in results:
        assert r["slug"] is None
        assert r["listing_only"] is True

    # 2023 companies have LinkedIn links → website should be None
    for r in results:
        assert r["website"] is None, (
            f"2023 company '{r['name']}' should have website=None (LinkedIn link), "
            f"got '{r['website']}'"
        )

    # Spot-check known companies
    names = {r["name"] for r in results}
    assert "Polystyvert" in names
    assert "Mirico" in names


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Profile field extraction
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_profile_acceleware(harvester: RiceEtvfHarvester) -> None:
    """_extract_profile should extract name, website, description, and affiliation."""
    soup = _load("profile_acceleware.html")
    cand = {
        "name": "Acceleware",  # listing-supplied fallback name
        "slug": "acceleware",
        "etvf_years": [2024],
        "listing_only": False,
    }
    record = harvester._extract_profile(soup, cand)

    # Name: cleaned from profile page (no trailing ' -')
    assert record.name == "Acceleware", f"Expected 'Acceleware', got '{record.name}'"

    # Website: extracted from button--alt link
    assert record.website is not None, "Expected website to be populated"
    assert "acceleware" in record.website.lower()
    assert record.website.startswith("http")

    # Description: non-empty, contains technology content (not footer)
    assert record.description is not None, "Expected description to be populated"
    assert len(record.description) > 50
    assert "electromagnetic" in record.description.lower()
    assert "Copyright" not in record.description

    # source_url: profile URL with slug
    assert record.source_url is not None
    assert "acceleware" in record.source_url

    # Extra fields
    assert record.extra["listing_only"] is False
    assert record.extra["etvf_years"] == [2024]
    assert record.extra["affiliation_raw"] in ("Presenting Company", "Office Hours Company", None)

    # Fields not available from ETVF pages
    assert record.location_raw is None
    assert record.tags == []
    assert record.extra["cohort_class"] is None


def test_extract_profile_emvolon(harvester: RiceEtvfHarvester) -> None:
    """_extract_profile should extract fields from a second profile fixture."""
    soup = _load("profile_emvolon.html")
    cand = {
        "name": "Emvolon",
        "slug": "emvolon",
        "etvf_years": [2024],
        "listing_only": False,
    }
    record = harvester._extract_profile(soup, cand)

    assert record.name == "Emvolon"
    assert record.website is not None
    assert record.description is not None
    assert "methanol" in record.description.lower() or "greenhouse" in record.description.lower()
    assert record.extra["listing_only"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Listing-only minimal record handling
# ─────────────────────────────────────────────────────────────────────────────

def test_make_listing_only_with_website(harvester: RiceEtvfHarvester) -> None:
    """_make_listing_only with a company website (2022-style) should populate website."""
    cand = {
        "name": "Syzygy Plasmonics",
        "slug": None,
        "website": "http://plasmonics.tech/",
        "etvf_years": [2022],
        "listing_only": True,
    }
    record = harvester._make_listing_only(cand)

    assert record.name == "Syzygy Plasmonics"
    assert record.website == "http://plasmonics.tech/"
    assert record.source_url is None
    assert record.description is None
    assert record.location_raw is None
    assert record.tags == []
    assert record.extra["listing_only"] is True
    assert record.extra["etvf_years"] == [2022]
    assert record.extra["cohort_class"] is None
    assert record.extra["affiliation_raw"] is None
    assert record.source == RiceEtvfHarvester.SOURCE_NAME


def test_make_listing_only_linkedin_only(harvester: RiceEtvfHarvester) -> None:
    """_make_listing_only with no website (2023-style LinkedIn) should have website=None."""
    cand = {
        "name": "Polystyvert",
        "slug": None,
        "website": None,
        "etvf_years": [2023],
        "listing_only": True,
    }
    record = harvester._make_listing_only(cand)

    assert record.name == "Polystyvert"
    assert record.website is None
    assert record.extra["listing_only"] is True
    assert record.extra["etvf_years"] == [2023]
