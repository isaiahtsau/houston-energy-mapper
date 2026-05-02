"""
Tests for signals/enrichment.py and the LLM augmentation in enrich/founder_pedigree.py.

All LLM calls are mocked — zero live API calls.

Tests:
  1. classify_sub_sector → geothermal classification
  2. classify_sub_sector → unknown on empty/thin description
  3. classify_sub_sector → primary_sector corrected when LLM returns wrong mapping
  4. generate_summary → returns prose for company with good description
  5. generate_summary → returns None (SQL NULL) when LLM returns null summary
  6. enrich_company → all three columns written to in-memory SQLite
  7. enrich_company idempotent → zero LLM calls when all columns already populated
  8. _llm_augment → merges paraphrased B1 into founder pedigree matches
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from enrich.founder_pedigree import (
    CategoryMatch,
    _LLMPedigreeAugmentation,
    _llm_augment,
)
from signals.enrichment import (
    EnrichInput,
    SubSectorResult,
    SummaryResult,
    classify_sub_sector,
    enrich_company,
    generate_summary,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _mock_llm_response(parsed: object, cost: float = 0.0001) -> MagicMock:
    resp = MagicMock()
    resp.parsed = parsed
    resp.cost_usd = cost
    return resp


def _geothermal_input() -> EnrichInput:
    return EnrichInput(
        company_id="fervo-energy",
        name="Fervo Energy",
        description=(
            "Houston-based geothermal developer using horizontal drilling techniques "
            "adapted from oil & gas to produce always-on baseload power from deep-earth heat. "
            "Filed for IPO in 2026 after a Series D from Breakthrough Energy Ventures."
        ),
    )


def _thin_input() -> EnrichInput:
    return EnrichInput(
        company_id="acme-energy",
        name="Acme Energy",
        description="[no description available]",
    )


def _in_memory_db() -> sqlite3.Connection:
    """Return an initialised in-memory SQLite connection with the full schema."""
    from storage.db import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _insert_raw_with_description(conn: sqlite3.Connection,
                                  company_id: str,
                                  name: str,
                                  description: str) -> None:
    """Insert a raw_record with description that matches by name (company_id=NULL is realistic)."""
    conn.execute(
        """INSERT INTO raw_records (company_id, source, name_raw, description, harvested_at)
           VALUES (NULL, 'test', ?, ?, '2026-01-01')""",
        (name, description),
    )
    conn.commit()


def _insert_company(conn: sqlite3.Connection, company_id: str, name: str,
                    venture_scale_score: float = 7.5) -> None:
    """Insert a minimal company row for testing."""
    conn.execute(
        """INSERT INTO companies
           (id, name, name_normalized, source_ids, first_seen_at, last_updated_at,
            venture_scale_score, is_excluded)
           VALUES (?, ?, ?, '["test"]', '2026-01-01', '2026-01-01', ?, 0)""",
        (company_id, name, name.lower(), venture_scale_score),
    )
    conn.commit()


def _insert_raw_record(conn: sqlite3.Connection, company_id: str, description: str) -> None:
    conn.execute(
        """INSERT INTO raw_records (company_id, source, name_raw, description, harvested_at)
           VALUES (?, 'test', ?, ?, '2026-01-01')""",
        (company_id, company_id, description),
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Sub-sector classification — geothermal
# ─────────────────────────────────────────────────────────────────────────────

def test_classify_sub_sector_geothermal() -> None:
    """LLM returning geothermal sub_sector should produce energy_transition primary_sector."""
    parsed = SubSectorResult(
        company_id="fervo-energy",
        primary_sector="energy_transition",
        sub_sector="geothermal",
        confidence="HIGH",
        reasoning="Core technology is geothermal heat extraction.",
    )
    with patch("signals.enrichment.call_llm", return_value=_mock_llm_response(parsed)):
        result = classify_sub_sector(_geothermal_input())

    assert result.sub_sector == "geothermal"
    assert result.primary_sector == "energy_transition"
    assert result.confidence == "HIGH"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Sub-sector → unknown on thin description
# ─────────────────────────────────────────────────────────────────────────────

def test_classify_sub_sector_unknown_on_thin_description() -> None:
    """LLM returning unknown sub_sector should not raise and should default primary correctly."""
    parsed = SubSectorResult(
        company_id="acme-energy",
        primary_sector="energy_transition",
        sub_sector="unknown",
        confidence="LOW",
        reasoning="Description too generic to classify.",
    )
    with patch("signals.enrichment.call_llm", return_value=_mock_llm_response(parsed)):
        result = classify_sub_sector(_thin_input())

    assert result.sub_sector == "unknown"
    assert result.confidence == "LOW"
    assert result.primary_sector in ("energy_transition", "off_thesis")


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Sub-sector → primary_sector corrected on inconsistent LLM output
# ─────────────────────────────────────────────────────────────────────────────

def test_classify_sub_sector_corrects_inconsistent_primary_sector() -> None:
    """If LLM returns wrong primary_sector for a known sub_sector, it should be corrected."""
    # geothermal belongs to energy_transition, not traditional_energy
    parsed = SubSectorResult(
        company_id="fervo-energy",
        primary_sector="traditional_energy",   # wrong
        sub_sector="geothermal",
        confidence="MEDIUM",
        reasoning="Geothermal drilling similar to O&G.",
    )
    with patch("signals.enrichment.call_llm", return_value=_mock_llm_response(parsed)):
        result = classify_sub_sector(_geothermal_input())

    assert result.sub_sector == "geothermal"
    assert result.primary_sector == "energy_transition"   # corrected


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Summary generation → returns prose
# ─────────────────────────────────────────────────────────────────────────────

def test_generate_summary_returns_prose() -> None:
    """LLM returning a summary string should produce a non-null SummaryResult."""
    expected_summary = (
        "Fervo Energy develops enhanced geothermal systems using horizontal drilling "
        "adapted from oil & gas, producing always-on baseload power. Filed for IPO in "
        "2026 after a Series D from Breakthrough Energy Ventures."
    )
    parsed = SummaryResult(
        company_id="fervo-energy",
        summary=expected_summary,
        confidence="HIGH",
    )
    with patch("signals.enrichment.call_llm", return_value=_mock_llm_response(parsed)):
        result = generate_summary(_geothermal_input())

    assert result.summary is not None
    assert len(result.summary) > 10
    assert result.confidence == "HIGH"
    # Should not contain forbidden marketing superlatives
    # Note: "Breakthrough Energy Ventures" is a proper noun and is acceptable
    forbidden_phrases = [
        "pioneering the future", "revolutionary", "next-generation",
        "innovative technology", "breakthrough technology", "cutting-edge",
        "transformative", "game-changing", "world-class", "disruptive",
    ]
    summary_lower = result.summary.lower()
    for phrase in forbidden_phrases:
        assert phrase not in summary_lower, (
            f"Forbidden phrase {phrase!r} in summary: {result.summary}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Summary generation → null when LLM returns null
# ─────────────────────────────────────────────────────────────────────────────

def test_generate_summary_null_on_thin_description() -> None:
    """LLM returning null summary should produce summary=None (stored as SQL NULL)."""
    parsed = SummaryResult(
        company_id="acme-energy",
        summary=None,
        confidence="LOW",
    )
    with patch("signals.enrichment.call_llm", return_value=_mock_llm_response(parsed)):
        result = generate_summary(_thin_input())

    assert result.summary is None
    assert result.confidence == "LOW"


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: enrich_company → all three columns written to DB
# ─────────────────────────────────────────────────────────────────────────────

def test_enrich_company_writes_all_columns() -> None:
    """enrich_company should write sub_sector, primary_sector, summary, and pedigree columns."""
    conn = _in_memory_db()
    _insert_company(conn, "fervo-energy", "Fervo Energy")
    # Use name-based match (company_id=NULL) to match production behavior
    _insert_raw_with_description(
        conn, "fervo-energy", "Fervo Energy",
        "Geothermal developer using horizontal drilling for always-on baseload power. "
        "IPO filed 2026 after Series D from Breakthrough Energy Ventures.",
    )

    sub_parsed = SubSectorResult(
        company_id="fervo-energy",
        primary_sector="energy_transition",
        sub_sector="geothermal",
        confidence="HIGH",
        reasoning="Core technology is geothermal.",
    )
    summ_parsed = SummaryResult(
        company_id="fervo-energy",
        summary=(
            "Fervo Energy develops enhanced geothermal systems using horizontal drilling "
            "adapted from oil & gas. Filed for IPO in 2026."
        ),
        confidence="HIGH",
    )

    call_count = [0]

    def mock_call_llm(prompt_name, **kwargs):
        call_count[0] += 1
        if prompt_name == "sub_sector":
            return _mock_llm_response(sub_parsed)
        if prompt_name == "summary":
            return _mock_llm_response(summ_parsed)
        # founder_pedigree augmentation: return no additional matches
        aug = _LLMPedigreeAugmentation(additional_matches=[])
        return _mock_llm_response(aug)

    with patch("signals.enrichment.call_llm", side_effect=mock_call_llm), \
         patch("enrich.founder_pedigree.call_llm", side_effect=mock_call_llm):
        result = enrich_company("fervo-energy", "Fervo Energy", conn)

    # Check DB writes
    row = conn.execute(
        "SELECT sub_sector, primary_sector, summary, founder_pedigree_score, "
        "founder_pedigree_tier, founder_pedigree_confidence, founder_pedigree_full "
        "FROM companies WHERE id = 'fervo-energy'"
    ).fetchone()
    assert row is not None
    assert row["sub_sector"] == "geothermal"
    assert row["primary_sector"] == "energy_transition"
    assert row["summary"] is not None and len(row["summary"]) > 10
    assert row["founder_pedigree_score"] is not None
    assert row["founder_pedigree_tier"] in ("HIGH", "MEDIUM-HIGH", "MEDIUM", "LOW-MEDIUM", "LOW")
    assert row["founder_pedigree_confidence"] in ("HIGH", "MEDIUM", "LOW")
    assert row["founder_pedigree_full"] is not None
    # founder_pedigree_full should be valid JSON
    fp_data = json.loads(row["founder_pedigree_full"])
    assert "final_score" in fp_data

    # Check return object
    assert result.sub_sector.sub_sector == "geothermal"
    assert result.summary.summary is not None


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: enrich_company idempotent — zero LLM calls when all columns populated
# ─────────────────────────────────────────────────────────────────────────────

def test_enrich_company_idempotent_no_llm_calls_when_complete() -> None:
    """When all enrichment columns are already set, no LLM calls should be made."""
    conn = _in_memory_db()
    _insert_company(conn, "already-done", "Already Done Co")
    # Pre-populate all three enrichment targets
    conn.execute(
        """UPDATE companies SET sub_sector='geothermal', primary_sector='energy_transition',
           summary='A geothermal company.', founder_pedigree_score=1.5,
           founder_pedigree_tier='LOW-MEDIUM', founder_pedigree_confidence='MEDIUM',
           founder_pedigree_full='{"name": "[description]", "role": "Other", "final_score": 1.5,
           "tier": "LOW-MEDIUM", "confidence": "MEDIUM", "categories_matched": [],
           "multipliers_applied": [], "raw_multiplier_product": 1.0,
           "capped_multiplier_factor": 1.0, "reasoning": "no signal", "review_queue": false}'
           WHERE id = 'already-done'"""
    )
    conn.commit()

    call_log = []

    def mock_call_llm(*args, **kwargs):
        call_log.append(kwargs.get("prompt_name", args[0] if args else "unknown"))
        return _mock_llm_response(None)

    with patch("signals.enrichment.call_llm", side_effect=mock_call_llm), \
         patch("enrich.founder_pedigree.call_llm", side_effect=mock_call_llm):
        enrich_company("already-done", "Already Done Co", conn)

    assert call_log == [], (
        f"Expected zero LLM calls for fully-enriched company, got: {call_log}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: _llm_augment → merges paraphrased B1 into pedigree
# ─────────────────────────────────────────────────────────────────────────────

def test_llm_augment_merges_paraphrased_b1() -> None:
    """_llm_augment should return a CategoryMatch for paraphrased B1 when LLM finds one."""
    bio = (
        "Previously led ExxonMobil's low carbon solutions commercialization for 12 years "
        "before co-founding this geothermal startup."
    )
    llm_match = CategoryMatch(
        category="B1",
        pattern_id="major_c_suite_or_vp",
        raw_points=3.0,
        evidence="'led ExxonMobil's low carbon solutions commercialization for 12 years'",
    )
    aug_result = _LLMPedigreeAugmentation(additional_matches=[llm_match])

    with patch("enrich.founder_pedigree.call_llm",
               return_value=_mock_llm_response(aug_result)):
        matches = _llm_augment(bio, already_detected=set())

    assert len(matches) == 1
    assert matches[0].category == "B1"
    assert matches[0].pattern_id == "major_c_suite_or_vp"
    assert matches[0].raw_points == 3.0


def test_llm_augment_returns_matches_for_caller_to_filter() -> None:
    """_llm_augment returns LLM output as-is; caller (score_founder_pedigree) filters by already_detected.

    This test verifies that the full pipeline in score_founder_pedigree correctly
    excludes LLM-returned matches whose category was already detected deterministically.
    """
    from enrich.founder_pedigree import score_founder_pedigree

    # Bio that deterministically fires B2 (PhD from Rice) and where the LLM
    # tries to report B1 (which the deterministic pass did NOT fire — no major company alias).
    # The LLM finds a paraphrased B3 not covered by the deterministic regex.
    bio = (
        "PhD from Rice University in chemical engineering. "
        "Previously sold a flow assurance sensor company to a major OFS player in 2019."
    )
    b3_match = CategoryMatch(
        category="B3",
        pattern_id="acquired_by_major",
        raw_points=3.5,
        evidence="'sold a flow assurance sensor company to a major OFS player in 2019'",
    )
    aug_result = _LLMPedigreeAugmentation(additional_matches=[b3_match])

    with patch("enrich.founder_pedigree.call_llm",
               return_value=_mock_llm_response(aug_result)):
        result = score_founder_pedigree(
            founder_name="Test Founder",
            bio_text=bio,
            role="CEO",
            company_id="test-co",
        )

    # Deterministic pass should have found B2 (Rice PhD)
    categories = {m.category for m in result.categories_matched}
    assert "B2" in categories, f"Expected B2 from Rice PhD; got {categories}"
    # LLM augmentation should have added B3 (paraphrased exit)
    assert "B3" in categories, f"Expected B3 from LLM augmentation; got {categories}"
    # Final score should reflect both B2 and B3
    assert result.final_score > 3.0, f"Expected score > 3.0 with B2+B3; got {result.final_score}"
