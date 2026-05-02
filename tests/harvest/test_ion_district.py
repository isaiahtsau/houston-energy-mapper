"""
Tests for harvest/ion_district.py — IonDistrictHarvester.

All tests use saved HTML fixtures (tests/fixtures/ion_district/).
Zero live HTTP calls. Fixtures reflect live-site structure as of 2026-05-02.

Tests:
  1. parse_office_listing: only Offices items returned (Building Resources and
     Food & Drink entries excluded)
  2. parse_office_listing: program suffix split correctly ("Aikynetix – Nexus"
     → name="Aikynetix", program="Nexus")
  3. parse_office_listing: (Coming Soon) marker stripped and flagged
     ("Ampla – Nexus (Coming Soon)" → coming_soon=True, name="Ampla")
  4. detail page: name, description, and website extracted from Aikynetix fixture
  5. full harvest (mocked): record count, required fields, source_url contains slug
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harvest.ion_district import (
    IonDistrictHarvester,
    _parse_display_name,
    _split_program_suffix,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "ion_district"


def _html(filename: str) -> str:
    return (_FIXTURES / filename).read_text(encoding="utf-8")


@pytest.fixture
def harvester() -> IonDistrictHarvester:
    from utils.rate_limiter import RateLimiter
    return IonDistrictHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


def _mock_resp(html: str, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.text = html
    m.status_code = status
    m.raise_for_status = MagicMock()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Only Offices entries returned — Building Resources and Food & Drink excluded
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_office_listing_only_returns_offices(
    harvester: IonDistrictHarvester,
) -> None:
    """Fixture has 2 Building Resources + 2 Food & Drink + 8 Offices items.
    Only the 8 Offices items should be returned.
    """
    items = harvester._parse_office_listing(_html("visit_ion.html"))

    slugs = {item["slug"] for item in items}
    assert len(items) == 8, f"Expected 8 office items, got {len(items)}: {slugs}"

    # Building Resources / Food & Drink slugs must NOT appear
    non_office_slugs = {"district-showroom", "fitness-center", "cafe-ion", "late-august"}
    assert not slugs.intersection(non_office_slugs), (
        f"Non-office slugs found: {slugs.intersection(non_office_slugs)}"
    )

    # Spot-check known office entries present
    assert "aikynetix-nexus" in slugs
    assert "solugen" in slugs


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Program suffix split — "Aikynetix – Nexus" → name/program
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_display_name_splits_program_suffix() -> None:
    """'Aikynetix – Nexus' should split into name='Aikynetix', program='Nexus'."""
    name, program, coming_soon = _parse_display_name("Aikynetix \u2013 Nexus")
    assert name == "Aikynetix"
    assert program == "Nexus"
    assert coming_soon is False


def test_parse_display_name_no_suffix() -> None:
    """'Ara Partners' has no suffix — program should be None."""
    name, program, coming_soon = _parse_display_name("Ara Partners")
    assert name == "Ara Partners"
    assert program is None
    assert coming_soon is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: (Coming Soon) stripped and flagged
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_display_name_coming_soon_flagged() -> None:
    """'Ampla – Nexus (Coming Soon)' → name='Ampla', program='Nexus', coming_soon=True."""
    name, program, coming_soon = _parse_display_name(
        "Ampla \u2013 Nexus (Coming Soon)"
    )
    assert name == "Ampla"
    assert program == "Nexus"
    assert coming_soon is True


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Detail page — Aikynetix name, description, website
# ─────────────────────────────────────────────────────────────────────────────

def test_detail_page_aikynetix(harvester: IonDistrictHarvester) -> None:
    """Aikynetix detail fixture should yield name, non-empty description, website."""
    with patch(
        "harvest.ion_district.requests.get",
        return_value=_mock_resp(_html("detail_aikynetix.html")),
    ):
        detail = harvester._fetch_detail(
            "https://iondistrict.com/tenants/aikynetix-nexus/"
        )

    assert detail is not None
    assert detail["name"] == "Aikynetix - Nexus"
    assert detail["description"] is not None
    assert len(detail["description"]) > 40, (
        f"Description too short: {detail['description']!r}"
    )
    assert detail["website"] == "https://aikynetix.com/"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Full harvest with mocked HTTP
# ─────────────────────────────────────────────────────────────────────────────

def test_full_harvest_count_and_required_fields(
    harvester: IonDistrictHarvester,
) -> None:
    """Full harvest with visit_ion fixture (8 offices) should emit 8 records."""
    listing_html = _html("visit_ion.html")
    detail_html = _html("detail_aikynetix.html")

    def fake_get(url: str, **kwargs):
        if url == "https://iondistrict.com/visit/ion/":
            return _mock_resp(listing_html)
        # All detail page requests return the Aikynetix fixture
        return _mock_resp(detail_html)

    with patch("harvest.ion_district.requests.get", side_effect=fake_get):
        records = harvester.fetch()

    assert len(records) == 8, f"Expected 8 records, got {len(records)}"

    for rec in records:
        assert rec.name, f"Record missing name: {rec}"
        assert rec.source == "Ion District"
        assert rec.location_raw == "Houston, TX"
        assert rec.source_url and "iondistrict.com/tenants/" in rec.source_url
        assert rec.extra.get("floor") is not None

    # The Aikynetix record should have its program suffix stripped from name
    aikynetix = next((r for r in records if "Aikynetix" in r.name), None)
    assert aikynetix is not None, "Aikynetix record not found"
    assert aikynetix.name == "Aikynetix", (
        f"Expected 'Aikynetix', got {aikynetix.name!r}"
    )
    assert aikynetix.extra["program"] == "Nexus"
