"""
Tests for harvest/lowercarbon.py — LowercarbonHarvester.

All tests use saved HTML fixtures (tests/fixtures/lowercarbon/).
Zero live HTTP calls. Fixtures reflect live-site structure as of 2026-05-02.

Tests:
  1. parse_cards: correct count — 6 cards in fixture → 6 records
  2. parse_cards: name and description extracted correctly (Antora spot-check)
  3. parse_cards: source_url contains lowercarbon.com/company/ slug path
  4. parse_cards: duplicate slug skipped — second occurrence of same href not doubled
  5. full harvest (mocked): record count, source field, no website on listing page
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from harvest.lowercarbon import LowercarbonHarvester, _slug_from_href

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "lowercarbon"


def _html(filename: str) -> str:
    return (_FIXTURES / filename).read_text(encoding="utf-8")


@pytest.fixture
def harvester() -> LowercarbonHarvester:
    from utils.rate_limiter import RateLimiter
    return LowercarbonHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


def _mock_resp(html: str, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.text = html
    m.status_code = status
    m.raise_for_status = MagicMock()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Card count — fixture has 6 cards
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_cards_count(harvester: LowercarbonHarvester) -> None:
    """Fixture has 6 a.company-card elements; should yield 6 records."""
    soup = BeautifulSoup(_html("companies.html"), "lxml")
    records = harvester._parse_cards(soup)
    assert len(records) == 6, f"Expected 6 records, got {len(records)}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Name and description extracted correctly
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_cards_antora_name_and_description(
    harvester: LowercarbonHarvester,
) -> None:
    """Antora card should have correct name and tagline as description."""
    soup = BeautifulSoup(_html("companies.html"), "lxml")
    records = harvester._parse_cards(soup)
    antora = next((r for r in records if r.name == "Antora"), None)

    assert antora is not None, "Antora not found in parsed records"
    assert antora.description == "Making solar and wind work 24/7."
    assert antora.source == "Lowercarbon Capital"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: source_url contains lowercarbon.com/company/ path
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_cards_source_url_contains_slug(harvester: LowercarbonHarvester) -> None:
    """Each record's source_url should point to the Lowercarbon detail page."""
    soup = BeautifulSoup(_html("companies.html"), "lxml")
    records = harvester._parse_cards(soup)

    for rec in records:
        assert rec.source_url is not None, f"{rec.name}: source_url is None"
        assert "lowercarbon.com/company/" in rec.source_url, (
            f"{rec.name}: source_url {rec.source_url!r} missing lowercarbon.com/company/"
        )
        slug = _slug_from_href(rec.source_url)
        assert slug, f"{rec.name}: could not extract slug from {rec.source_url!r}"
        assert rec.extra.get("slug") == slug


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Duplicate slug skipped — same card href appearing twice
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_cards_dedup_duplicate_slug(harvester: LowercarbonHarvester) -> None:
    """If the same company slug appears twice in the HTML, only one record emitted."""
    extra_card = """
    <a class="company-card relative my-4 w-full no-underline focus:outline-none"
       href="https://lowercarbon.com/company/antora/">
      <div class="company-card__content-text p-8 h-full flex flex-col">
        <h4 class="title-lg-company flex-grow break-words">Making solar and wind work 24/7.</h4>
        <h5 class="text-base leading-4 font-sans font-bold mt-9 mb-0">Antora</h5>
      </div>
    </a>
    """
    html_with_dupe = _html("companies.html").replace(
        "</div>\n</div>\n</body>",
        extra_card + "</div>\n</div>\n</body>",
    )
    soup = BeautifulSoup(html_with_dupe, "lxml")
    records = harvester._parse_cards(soup)

    antora_records = [r for r in records if r.name == "Antora"]
    assert len(antora_records) == 1, (
        f"Expected 1 Antora record after dedup, got {len(antora_records)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Full harvest — count, source field, no website
# ─────────────────────────────────────────────────────────────────────────────

def test_full_harvest_count_source_no_website(
    harvester: LowercarbonHarvester,
) -> None:
    """Full harvest with fixture: 6 records, source='Lowercarbon Capital', website=None."""
    with patch(
        "harvest.lowercarbon.requests.get",
        return_value=_mock_resp(_html("companies.html")),
    ):
        records = harvester.fetch()

    assert len(records) == 6, f"Expected 6 records, got {len(records)}"
    for rec in records:
        assert rec.source == "Lowercarbon Capital"
        assert rec.website is None, f"{rec.name}: expected website=None, got {rec.website!r}"
        assert rec.name, f"Record has empty name: {rec}"
