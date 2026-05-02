"""
Tests for harvest/energytech_nexus.py — EnergyTechNexusHarvester.

All tests use saved HTML fixtures (tests/fixtures/energytech_nexus/).
Zero live HTTP calls. Fixtures fetched 2026-05-02.

Tests:
  1. COPILOT article: 14 company records, GeoFuels and PolyQor present
  2. Deduplication: COPILOT companies not re-added from Pilotathon repeats
  3. Nav link filtering: items ending with '›' excluded
  4. Location extraction: "Birmingham, Alabama-based Accelerate Wind" → location_raw set
  5. Full harvest: 23 total unique records (14 COPILOT + 9 Pilotathon-only)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harvest.energytech_nexus import (
    EnergyTechNexusHarvester,
    _parse_company_li,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "energytech_nexus"


def _html(filename: str) -> str:
    return (_FIXTURES / filename).read_text(encoding="utf-8")


@pytest.fixture
def harvester() -> EnergyTechNexusHarvester:
    from utils.rate_limiter import RateLimiter
    return EnergyTechNexusHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


def _mock_resp(html: str) -> MagicMock:
    m = MagicMock()
    m.text = html
    m.raise_for_status = MagicMock()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: COPILOT article yields 14 company records
# ─────────────────────────────────────────────────────────────────────────────

def test_copilot_article_yields_14_records(harvester: EnergyTechNexusHarvester) -> None:
    """COPILOT article fixture should produce exactly 14 company records."""
    seen: set[str] = set()
    with patch(
        "harvest.energytech_nexus.requests.get",
        return_value=_mock_resp(_html("copilot_cohort.html")),
    ):
        records = harvester._fetch_article(
            "https://energycapitalhtx.com/energytech-nexus-copilot-cohort-2025",
            "COPILOT 2025",
            seen,
        )

    assert len(records) == 14, (
        f"Expected 14 COPILOT companies, got {len(records)}: {[r.name for r in records]}"
    )

    names = {r.name for r in records}
    assert "GeoFuels" in names, f"GeoFuels missing from {names}"
    assert "PolyQor" in names, f"PolyQor missing from {names}"
    assert "EarthEn Energy" in names, f"EarthEn Energy missing from {names}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Deduplication — Pilotathon name-only repeats are skipped
# ─────────────────────────────────────────────────────────────────────────────

def test_pilotathon_deduplicates_copilot_names(
    harvester: EnergyTechNexusHarvester,
) -> None:
    """Pilotathon article should add only new companies (not COPILOT repeats)."""
    copilot_html = _html("copilot_cohort.html")
    pilotathon_html = _html("pilotathon.html")

    call_idx = [0]
    fixtures = [copilot_html, pilotathon_html]

    def fake_get(url, **kwargs):
        resp = _mock_resp(fixtures[call_idx[0]])
        call_idx[0] += 1
        return resp

    with patch("harvest.energytech_nexus.requests.get", side_effect=fake_get):
        records = harvester.fetch()

    names = [r.name for r in records]
    # GeoFuels should appear exactly once
    geofuels_count = sum(1 for n in names if "GeoFuels" in n or "geofuels" in n.lower())
    assert geofuels_count == 1, (
        f"GeoFuels should appear exactly once, found {geofuels_count} times in {names}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Nav link filtering
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_company_li_rejects_nav_links() -> None:
    """Items ending with '›' should return None."""
    nav_items = [
        "Greentown Labs adds 6 Texas clean energy startups to Houston incubator ›",
        "Houston cleantech accelerator names 12 startups to 2025 cohort ›",
        "Energy Tech Nexus names startups to pitch at Houston Pilotathon - Energy Capital ›",
    ]
    for item in nav_items:
        assert _parse_company_li(item) is None, (
            f"Expected None for nav link, got a result for: {item!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Location extraction
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_company_li_extracts_location() -> None:
    """Location prefix should be extracted to location_raw."""
    result = _parse_company_li(
        "Birmingham, Alabama-based Accelerate Wind, developer of a wind turbine "
        "for commercial buildings."
    )
    assert result is not None
    name, description, location_raw = result
    assert name == "Accelerate Wind", f"Expected 'Accelerate Wind', got {name!r}"
    assert location_raw is not None, "Expected location_raw to be set"
    assert "Alabama" in location_raw or "Birmingham" in location_raw, (
        f"Expected Alabama or Birmingham in location_raw: {location_raw!r}"
    )
    assert description is not None and len(description) > 10

    # Phoenix-based (single city, no state)
    result2 = _parse_company_li(
        "Phoenix-based EarthEn Energy, a developer of technology for thermo-mechanical "
        "energy storage."
    )
    assert result2 is not None
    name2, desc2, loc2 = result2
    assert name2 == "EarthEn Energy"
    assert loc2 is not None and "Phoenix" in loc2


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Full harvest total count
# ─────────────────────────────────────────────────────────────────────────────

def test_full_harvest_total_record_count(harvester: EnergyTechNexusHarvester) -> None:
    """Full harvest should yield 14 COPILOT + 9 Pilotathon-only = 23 unique records."""
    copilot_html = _html("copilot_cohort.html")
    pilotathon_html = _html("pilotathon.html")

    call_idx = [0]
    fixtures = [copilot_html, pilotathon_html]

    def fake_get(url, **kwargs):
        resp = _mock_resp(fixtures[call_idx[0]])
        call_idx[0] += 1
        return resp

    with patch("harvest.energytech_nexus.requests.get", side_effect=fake_get):
        records = harvester.fetch()

    assert len(records) == 23, (
        f"Expected 23 unique records, got {len(records)}: {[r.name for r in records]}"
    )
    for rec in records:
        assert rec.name
        assert rec.source == "Energytech Nexus"
        assert rec.extra.get("program") in ("COPILOT 2025", "Pilotathon 2025")
