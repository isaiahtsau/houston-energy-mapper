"""
Tests for harvest/goose_capital.py — GooseCapitalHarvester.

All tests use the saved HTML fixture (tests/fixtures/goose_capital/portfolio.html).
Zero live HTTP calls. Fixture fetched 2026-05-01.

Tests:
  1. Full parse: 20+ records, each with name and source metadata
  2. Name inference: image src filename → correct title-case name
  3. Website extraction: external href preserved as company website
  4. Description extraction: description text is non-empty for known cards
  5. Selector drift: empty page → warning + empty list
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harvest.goose_capital import GooseCapitalHarvester, _name_from_image_src

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "goose_capital"


def _fixture_html() -> str:
    return (_FIXTURES / "portfolio.html").read_text(encoding="utf-8")


@pytest.fixture
def harvester() -> GooseCapitalHarvester:
    from utils.rate_limiter import RateLimiter
    return GooseCapitalHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


def _fetch_with_fixture(harvester: GooseCapitalHarvester):
    mock_resp = MagicMock()
    mock_resp.text = _fixture_html()
    mock_resp.raise_for_status = MagicMock()
    with patch("harvest.goose_capital.requests.get", return_value=mock_resp):
        return harvester.fetch()


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Full parse — record count and required fields
# ─────────────────────────────────────────────────────────────────────────────

def test_full_parse_count_and_required_fields(harvester: GooseCapitalHarvester) -> None:
    """Should extract ≥20 records; each must have a name and correct source."""
    records = _fetch_with_fixture(harvester)

    assert len(records) >= 20, f"Expected ≥20 records, got {len(records)}"
    for rec in records:
        assert rec.name, f"Record missing name: {rec}"
        assert rec.source == "GOOSE Capital"
        assert rec.source_url == "https://www.goose.capital/portfolio"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Name inference from image src
# ─────────────────────────────────────────────────────────────────────────────

def test_name_from_image_src_strips_hash_and_suffix() -> None:
    """_name_from_image_src should strip Webflow hash prefix and logo suffix."""
    # Standard Webflow CDN URL with 24-char hash prefix
    src = "https://cdn.prod.website-files.com/abc123/6272dc070459e25b4dd52fd8_adhesys_logo.png"
    assert _name_from_image_src(src) == "Adhesys"

    # Hyphen-separated multi-word name
    src2 = "https://cdn.prod.website-files.com/abc/6272aabbccddee0011223344_calyx-global_logo.png"
    result = _name_from_image_src(src2)
    assert result is not None
    assert "Calyx" in result

    # No suffix — just a company slug (24-char Webflow hash)
    src3 = "https://cdn.prod.website-files.com/abc/6272aabbccddee0011223344_zibrio.jpeg"
    assert _name_from_image_src(src3) == "Zibrio"

    # Empty src → None
    assert _name_from_image_src("") is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Website extraction from card href
# ─────────────────────────────────────────────────────────────────────────────

def test_website_is_external_url(harvester: GooseCapitalHarvester) -> None:
    """Each record with a website should have an http(s) URL."""
    records = _fetch_with_fixture(harvester)
    records_with_website = [r for r in records if r.website]

    assert len(records_with_website) >= 15, (
        f"Expected ≥15 records with a website, got {len(records_with_website)}"
    )
    for rec in records_with_website:
        assert rec.website.startswith("http"), (
            f"Expected http(s) URL, got {rec.website!r} for {rec.name!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Description extraction
# ─────────────────────────────────────────────────────────────────────────────

def test_descriptions_are_non_empty(harvester: GooseCapitalHarvester) -> None:
    """Most records should have a non-empty description."""
    records = _fetch_with_fixture(harvester)
    with_desc = [r for r in records if r.description]

    assert len(with_desc) >= 15, (
        f"Expected ≥15 records with description, got {len(with_desc)}"
    )
    for rec in with_desc:
        assert len(rec.description) > 5, (
            f"Description too short for {rec.name!r}: {rec.description!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Selector drift detection
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_page_returns_empty_list(
    harvester: GooseCapitalHarvester,
    caplog,
) -> None:
    """A page with no matching items should return [] and log a warning."""
    mock_resp = MagicMock()
    mock_resp.text = "<html><body><p>Portfolio coming soon.</p></body></html>"
    mock_resp.raise_for_status = MagicMock()

    with patch("harvest.goose_capital.requests.get", return_value=mock_resp):
        import logging
        with caplog.at_level(logging.WARNING, logger="harvest.goose_capital"):
            records = harvester.fetch()

    assert records == []
    assert any(
        "no-items" in r.message or "companies__item" in r.message
        for r in caplog.records
    ), f"Expected no-items warning; got: {[r.message for r in caplog.records]}"
