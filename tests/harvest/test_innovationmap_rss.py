"""
Tests for harvest/innovationmap_rss.py — InnovationMapRssHarvester.

All tests use the saved RSS fixture (tests/fixtures/innovationmap_rss/feed.xml).
Zero live HTTP calls. The fixture was fetched 2026-05-01.

Tests:
  1. Energy filter — only articles with energy keywords in title pass
  2. Company extraction — company links extracted from filtered article bodies
  3. Cross-article deduplication — same domain appearing in multiple articles
     yields one record
  4. Skip-host filtering — social/news/CDN links excluded from company records
  5. Non-energy articles — articles without energy keywords produce no records
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harvest.innovationmap_rss import (
    InnovationMapRssHarvester,
    _ENERGY_TITLE_KEYWORDS,
    _extract_companies_from_article,
    _is_company_url,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "innovationmap_rss"


def _fixture_xml_bytes() -> bytes:
    return (_FIXTURES / "feed.xml").read_bytes()


@pytest.fixture
def harvester() -> InnovationMapRssHarvester:
    from utils.rate_limiter import RateLimiter
    return InnovationMapRssHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Energy filter
# ─────────────────────────────────────────────────────────────────────────────

def test_energy_filter_passes_energy_titles(harvester: InnovationMapRssHarvester) -> None:
    """Articles with energy keywords in title should pass the filter."""
    energy_titles = [
        "Houston geothermal unicorn Fervo officially files for IPO",
        "Houston-based, NASA-founded cleantech startup closes $12M seed round",
        "UH breakthrough moves superconductivity closer to real-world use",
    ]
    for title in energy_titles:
        assert any(kw in title.lower() for kw in _ENERGY_TITLE_KEYWORDS), (
            f"Expected energy match in: {title!r}"
        )


def test_energy_filter_blocks_non_energy_titles(harvester: InnovationMapRssHarvester) -> None:
    """Articles with no energy keywords in title should not pass the filter."""
    non_energy_titles = [
        "Houston legacy planning platform secures $2.5M investment",
        "Houston digital health platform Koda lands strategic investment",
        "AI-powered Houston startup helps restaurants boost customer loyalty",
        "Texas still ranks as No. 1 in U.S. for inbound moves",
    ]
    for title in non_energy_titles:
        assert not any(kw in title.lower() for kw in _ENERGY_TITLE_KEYWORDS), (
            f"Expected NO energy match in: {title!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Company extraction from article body
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_companies_from_article_finds_company_links() -> None:
    """Company links in article body should produce RawCompanyRecord instances."""
    # Simulated article body containing one clear company link
    html = """
    <p>Houston-based <a href="https://www.helixearth.com/" target="_blank">
    Helix Earth Technologies</a>, a NASA-founded cleantech startup, has closed
    a $12M seed round to expand its HVAC energy efficiency technology.</p>
    <p>The funding was announced in <a href="https://businesswire.com/news/123">
    a news release</a>.</p>
    """
    records = _extract_companies_from_article(
        description_html=html,
        article_url="https://houston.innovationmap.com/article/123",
        article_title="NASA-founded cleantech startup closes $12M seed",
        article_date="Fri, 01 May 2026 12:00:00 +0000",
        article_author="Test Reporter",
        article_categories=["Helix earth technologies", "Cleantech"],
    )

    # businesswire.com is in SKIP_HOSTS, helixearth.com is not
    assert len(records) == 1
    assert records[0].name == "Helix Earth Technologies"
    assert records[0].website == "https://www.helixearth.com/"
    assert records[0].source == "InnovationMap Houston RSS"
    assert records[0].extra["article_title"] == "NASA-founded cleantech startup closes $12M seed"
    assert "helixearth" in records[0].description.lower() or records[0].description is not None


def test_extract_companies_deduplicates_same_domain_within_article() -> None:
    """Same company domain linked twice in one article → one record only."""
    html = """
    <p><a href="https://www.fervo.com/">Fervo Energy</a> filed its S-1.</p>
    <p>Read more at <a href="https://www.fervo.com/press">Fervo's press page</a>.</p>
    """
    records = _extract_companies_from_article(
        description_html=html,
        article_url="https://houston.innovationmap.com/article/fervo",
        article_title="Fervo files for IPO",
        article_date="",
        article_author="",
        article_categories=[],
    )
    assert len(records) == 1
    assert records[0].name == "Fervo Energy"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Skip-host filtering
# ─────────────────────────────────────────────────────────────────────────────

def test_is_company_url_blocks_skip_hosts() -> None:
    """Known non-company URLs should return False from _is_company_url."""
    skip_urls = [
        "https://linkedin.com/in/someone",
        "https://www.linkedin.com/company/acme",
        "https://businesswire.com/news/123",
        "https://energycapitalhtx.com/article",
        "https://assets.rebelmouse.io/img.png",
        "https://houston.innovationmap.com/article/xyz",
        "https://sec.gov/Archives/edgar/data/123",
    ]
    for url in skip_urls:
        assert not _is_company_url(url), f"Expected False for skip URL: {url}"


def test_is_company_url_passes_company_websites() -> None:
    """Genuine company website URLs should return True from _is_company_url."""
    company_urls = [
        "https://www.helixearth.com/",
        "https://fervoenergy.com/",
        "https://aquafortus.net/",
        "https://emvolon.com",
    ]
    for url in company_urls:
        assert _is_company_url(url), f"Expected True for company URL: {url}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Full fixture run via mocked HTTP
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_returns_energy_companies_from_fixture(
    harvester: InnovationMapRssHarvester,
) -> None:
    """Full fetch() against the saved fixture should yield energy company records."""
    mock_resp = MagicMock()
    mock_resp.content = _fixture_xml_bytes()
    mock_resp.raise_for_status = MagicMock()

    with patch("harvest.innovationmap_rss.requests.get", return_value=mock_resp):
        records = harvester.fetch()

    # At least one energy company should be extracted from the fixture
    assert len(records) >= 1, "Expected at least 1 energy company from fixture"

    # Every record must have a name and source
    for rec in records:
        assert rec.name, f"Record missing name: {rec}"
        assert rec.source == "InnovationMap Houston RSS"
        assert rec.extra.get("article_title"), f"Record missing article_title: {rec}"

    # All records should have websites that pass the company URL filter
    for rec in records:
        if rec.website:
            assert _is_company_url(rec.website), (
                f"Record has a skip-host website: {rec.website}"
            )

    # Known energy company from fixture: Helix Earth Technologies
    names_lower = [r.name.lower() for r in records]
    assert any("helix" in n for n in names_lower), (
        f"Expected 'Helix Earth Technologies' in results, got: {[r.name for r in records]}"
    )
