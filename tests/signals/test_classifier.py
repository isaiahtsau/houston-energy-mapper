"""
Tests for classify_venture_scale in signals/venture_scale.py.

All LLM calls are mocked — zero live API calls. Tests validate:
  1. Clear VENTURE_SCALE response propagated correctly
  2. Clear NOT_VENTURE_SCALE response propagated correctly
  3. BORDERLINE with MEDIUM confidence
  4. Listing-only record → LLM returns BORDERLINE/LOW (mocked)
  5. Hard-excluded company → call_llm never called

Fixtures: no HTML fixtures needed — pure unit tests on mocked LLMResponse.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from models import CompanyRecord
from signals.venture_scale import (
    VentureScaleClassification,
    apply_hard_exclude_rules,
    classify_venture_scale,
    reset_classify_cost,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _mock_response(classification: VentureScaleClassification) -> MagicMock:
    """Build a mock LLMResponse whose .parsed is the given classification."""
    resp = MagicMock()
    resp.parsed = classification
    resp.cost_usd = 0.001  # nominal cost for accumulator tests
    return resp


def _venture_scale_company() -> CompanyRecord:
    return CompanyRecord(
        company_id="emvolon",
        name="Emvolon",
        description=(
            "Emvolon, an MIT spin off, converts greenhouse gas emissions onsite "
            "into ready-to-use carbon-negative fuels and chemicals like green methanol "
            "via proprietary electrochemical reactor technology."
        ),
        canonical_domain="http://www.emvolon.com",
    )


def _not_venture_scale_company() -> CompanyRecord:
    return CompanyRecord(
        company_id="audubon-energy-group",
        name="Audubon Energy Group",
        description=(
            "Audubon Energy Group is a privately held independent oil and gas "
            "exploration holding company focused on Black Sea energy resources. "
            "We organize and support energy exploration, drilling and production "
            "partnerships where our primary role is risks and opportunities analysis, "
            "prospect generation, lease acquisition and management."
        ),
        canonical_domain="https://www.audubon.energy",
    )


@pytest.fixture(autouse=True)
def reset_cost():
    """Reset cost accumulator before each test."""
    reset_classify_cost()
    yield


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Clear VENTURE_SCALE
# ─────────────────────────────────────────────────────────────────────────────


def test_classify_clear_venture_scale() -> None:
    """LLM returning VENTURE_SCALE/HIGH should propagate correctly."""
    expected = VentureScaleClassification(
        company_id="emvolon",
        score=9.0,
        tier="VENTURE_SCALE",
        confidence="HIGH",
        positive_signals=["university_licensed_ip", "ip_backed_patents"],
        false_positive_patterns=[],
        reasoning=(
            '(1) Strongest positive: university-licensed IP — record states "MIT spin off" '
            'with "proprietary electrochemical reactor technology". '
            "(2) No false-positive patterns observed. "
            "(3) No borderline considerations."
        ),
        review_queue=False,
    )

    with patch("signals.venture_scale.call_llm") as mock_call:
        mock_call.return_value = _mock_response(expected)
        result = classify_venture_scale(
            _venture_scale_company(),
            affiliation="Presenting Company",
            etvf_years="[2024]",
            listing_only=False,
        )

    assert result.tier == "VENTURE_SCALE"
    assert result.confidence == "HIGH"
    assert result.score == 9.0
    assert result.review_queue is False
    assert "university_licensed_ip" in result.positive_signals
    mock_call.assert_called_once()
    # Verify prompt variables were passed
    call_kwargs = mock_call.call_args
    assert call_kwargs.kwargs["prompt_name"] == "classifier"
    assert call_kwargs.kwargs["prompt_version"] == "v1.1"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Clear NOT_VENTURE_SCALE
# ─────────────────────────────────────────────────────────────────────────────


def test_classify_clear_not_venture_scale() -> None:
    """LLM returning NOT_VENTURE_SCALE/HIGH should propagate correctly."""
    expected = VentureScaleClassification(
        company_id="audubon-energy-group",
        score=1.5,
        tier="NOT_VENTURE_SCALE",
        confidence="HIGH",
        positive_signals=[],
        false_positive_patterns=["consulting_positioned_as_software"],
        reasoning=(
            "(1) No positive signals observed. "
            '(2) Strongest negative: consulting_positioned_as_software — record states '
            '"our primary role is risks and opportunities analysis, prospect generation". '
            "(3) No borderline considerations."
        ),
        review_queue=False,
    )

    with patch("signals.venture_scale.call_llm") as mock_call:
        mock_call.return_value = _mock_response(expected)
        result = classify_venture_scale(
            _not_venture_scale_company(),
            affiliation="Office Hours Company",
            etvf_years="[2024]",
            listing_only=False,
        )

    assert result.tier == "NOT_VENTURE_SCALE"
    assert result.confidence == "HIGH"
    assert result.score == 1.5
    assert result.review_queue is False
    assert "consulting_positioned_as_software" in result.false_positive_patterns


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: BORDERLINE with MEDIUM confidence
# ─────────────────────────────────────────────────────────────────────────────


def test_classify_borderline_medium() -> None:
    """LLM returning BORDERLINE/MEDIUM should set review_queue=True."""
    borderline_company = CompanyRecord(
        company_id="teverra",
        name="Teverra",
        description=(
            "Teverra's oil & gas-trained staff applies learnings from that industry "
            "to accelerate clean energy adoption. Our innovative solutions, particularly "
            "around the characterization, confirmation, and development of subsurface "
            "clean energy resources such as next-generation geothermal energy and "
            "carbon sequestration."
        ),
        canonical_domain="https://www.teverra.com",
    )
    expected = VentureScaleClassification(
        company_id="teverra",
        score=4.5,
        tier="BORDERLINE",
        confidence="MEDIUM",
        positive_signals=["trl_progression"],
        false_positive_patterns=["oilfield_services_thin_ai_wrapper"],
        reasoning=(
            "(1) Strongest positive: TRL progression implied — record states "
            '"characterization, confirmation, and development of subsurface clean energy". '
            "(2) Strongest negative: oilfield_services_thin_ai_wrapper — record states "
            '"oil & gas-trained staff applies learnings" without IP claims or product '
            "description. "
            "(3) Borderline: Presenting Company affiliation provides +0.5 prior but "
            "insufficient to overcome absence of HIGH-weight signals."
        ),
        review_queue=True,
    )

    with patch("signals.venture_scale.call_llm") as mock_call:
        mock_call.return_value = _mock_response(expected)
        result = classify_venture_scale(
            borderline_company,
            affiliation="Presenting Company",
            etvf_years="[2025]",
            listing_only=False,
        )

    assert result.tier == "BORDERLINE"
    assert result.confidence == "MEDIUM"
    assert result.review_queue is True
    assert result.score == 4.5


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Listing-only record → BORDERLINE/LOW
# ─────────────────────────────────────────────────────────────────────────────


def test_classify_listing_only_low_confidence() -> None:
    """Listing-only record (no description): LLM should return BORDERLINE/LOW."""
    listing_only_company = CompanyRecord(
        company_id="syzygy-plasmonics",
        name="Syzygy Plasmonics",
        description="",   # no description — listing-only
        canonical_domain="http://plasmonics.tech/",
    )
    expected = VentureScaleClassification(
        company_id="syzygy-plasmonics",
        score=5.0,
        tier="BORDERLINE",
        confidence="LOW",
        positive_signals=[],
        false_positive_patterns=[],
        reasoning=(
            "(1) No positive signals observed — record is listing-only with no description. "
            "(2) No false-positive patterns observed. "
            "(3) Insufficient data: classification requires manual research or "
            "enrichment-stage data."
        ),
        review_queue=True,
    )

    with patch("signals.venture_scale.call_llm") as mock_call:
        mock_call.return_value = _mock_response(expected)
        result = classify_venture_scale(
            listing_only_company,
            affiliation=None,
            etvf_years="[2022]",
            listing_only=True,
        )

    assert result.tier == "BORDERLINE"
    assert result.confidence == "LOW"
    assert result.review_queue is True
    assert result.score == 5.0
    # LLM still called (the prompt instructs it, not pre-empted in code)
    mock_call.assert_called_once()
    # Verify listing_only=true was passed in variables
    call_kwargs = mock_call.call_args
    assert call_kwargs.kwargs["variables"]["listing_only"] == "true"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Hard-excluded company — call_llm must NOT be called
# ─────────────────────────────────────────────────────────────────────────────


def test_hard_excluded_llm_not_called() -> None:
    """Company triggering HX-01 (PF-Debt) is excluded before LLM is called."""
    pf_company = CompanyRecord(
        company_id="project-finance-llc",
        name="Gulf Coast Solar Project LLC",
        description="Single-asset solar project development vehicle.",
        most_recent_round={"round_type": "PF-Debt", "amount_usd": 50_000_000},
    )

    # Confirm the hard-exclude rule fires
    he = apply_hard_exclude_rules(pf_company)
    assert he.excluded is True
    assert he.rule_id == "HX-01"
    assert "PF-Debt" in he.reason

    # Simulate orchestrator: only call classify if not excluded
    with patch("signals.venture_scale.call_llm") as mock_call:
        if not he.excluded:
            classify_venture_scale(pf_company)

    mock_call.assert_not_called()
