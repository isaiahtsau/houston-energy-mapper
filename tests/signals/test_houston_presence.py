"""
Tests for signals/houston_presence.py.

This test suite is written against the interface before the implementation
(Step 4). Tests are marked xfail until score_houston_presence() is built.

Gold-standard test cases are loaded from tests/fixtures/sample_companies.json.
Each fixture has a "expected_tier" and "expected_min_points" field that
score_houston_presence() must reproduce to pass.

Test categories:
  1. Tier A companies (unambiguous Houston presence — Form D + accelerator residency)
  2. Tier B companies (moderate signal — website + job postings)
  3. Tier B-low companies (weak signal — press mention only)
  4. Tier C companies (no Houston signal)
  5. Edge cases (exactly 6 points but no HIGH operational signal → B-high, not A)
  6. Signal additivity (multiple LOW signals sum correctly)
"""
import json
import pytest
from pathlib import Path

# Loaded once for the entire test module
FIXTURES_PATH = Path(__file__).parent.parent / "fixtures" / "sample_companies.json"


@pytest.fixture(scope="module")
def sample_companies() -> list[dict]:
    """Load gold-standard company fixtures."""
    if not FIXTURES_PATH.exists():
        pytest.skip(f"Fixtures file not found: {FIXTURES_PATH}")
    with FIXTURES_PATH.open() as f:
        return json.load(f)


@pytest.mark.xfail(reason="score_houston_presence not yet implemented (Step 4)")
def test_tier_a_company(sample_companies):
    """A company with Form D Houston address + accelerator residency should score Tier A."""
    from signals.houston_presence import score_houston_presence, HoustonTier

    tier_a = next((c for c in sample_companies if c["expected_tier"] == "A"), None)
    if tier_a is None:
        pytest.skip("No Tier A fixture in sample_companies.json")

    result = score_houston_presence(tier_a["signals"])
    assert result.tier == HoustonTier.A
    assert result.points >= 6
    assert result.has_high_operational is True


@pytest.mark.xfail(reason="score_houston_presence not yet implemented (Step 4)")
def test_tier_c_company(sample_companies):
    """A company with no Houston signals should score Tier C with 0 points."""
    from signals.houston_presence import score_houston_presence, HoustonTier

    tier_c = next((c for c in sample_companies if c["expected_tier"] == "C"), None)
    if tier_c is None:
        pytest.skip("No Tier C fixture in sample_companies.json")

    result = score_houston_presence(tier_c["signals"])
    assert result.tier == HoustonTier.C
    assert result.points == 0
    assert len(result.contributions) == 0


@pytest.mark.xfail(reason="score_houston_presence not yet implemented (Step 4)")
def test_b_high_requires_no_high_operational():
    """6+ points but no HIGH operational signal → B-high, not A."""
    from signals.houston_presence import score_houston_presence, HoustonTier

    # 6 points from soft signals only (3 MEDIUM + 0 HIGH operational)
    signals = {
        "website_houston_office": True,       # MEDIUM: 2 pts
        "houston_investor_lead": "ECV",       # MEDIUM: 2 pts
        "job_postings_houston": 3,            # MEDIUM: 2 pts
        # No HIGH operational signals
    }
    result = score_houston_presence(signals)
    assert result.tier == HoustonTier.B_HIGH
    assert result.points == 6
    assert result.has_high_operational is False


@pytest.mark.xfail(reason="score_houston_presence not yet implemented (Step 4)")
def test_trace_is_populated():
    """Every scored company should have a non-empty contributions trace."""
    from signals.houston_presence import score_houston_presence

    signals = {"form_d_address": "1234 Main St, Houston, TX 77002"}
    result = score_houston_presence(signals)
    assert len(result.contributions) > 0
    assert all(c.points > 0 for c in result.contributions)


@pytest.mark.xfail(reason="score_houston_presence not yet implemented (Step 4)")
def test_empty_signals_is_tier_c():
    """An empty signals dict should produce Tier C with zero points."""
    from signals.houston_presence import score_houston_presence, HoustonTier

    result = score_houston_presence({})
    assert result.tier == HoustonTier.C
    assert result.points == 0
