"""
Tests for enrich/founder_pedigree.py — deterministic baseline detection only.

Eight synthetic test cases (FP-TC-01 through FP-TC-08) covering B1–B6 category
detection and Houston multipliers. All tests exercise score_founder_pedigree()
called directly with evidence unpacked from fixtures.

The LLM augmentation layer (paraphrased B1/B3/B6) is validated separately in
Step 8 after prompts/founder_pedigree_v1.md is drafted.
"""
import pytest

from enrich.founder_pedigree import FounderPedigree, score_founder_pedigree
from models import PEDIGREE_TIER_RANK


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def _category_ids(result: FounderPedigree) -> set[str]:
    return {m.category for m in result.categories_matched}


def _multiplier_ids(result: FounderPedigree) -> set[str]:
    return {m.multiplier_id for m in result.multipliers_applied}


def _assert_tier_min(result: FounderPedigree, tier_min: str) -> None:
    assert PEDIGREE_TIER_RANK[result.tier] >= PEDIGREE_TIER_RANK[tier_min], (
        f"Expected tier >= {tier_min}, got {result.tier} (score={result.final_score})"
    )


def _assert_tier_max(result: FounderPedigree, tier_max: str) -> None:
    assert PEDIGREE_TIER_RANK[result.tier] <= PEDIGREE_TIER_RANK[tier_max], (
        f"Expected tier <= {tier_max}, got {result.tier} (score={result.final_score})"
    )


# ---------------------------------------------------------------------------
# FP-TC-01 — Rice ChemE PhD under Halas lab + lab/IP alignment
# ---------------------------------------------------------------------------


def test_fp_tc01_rice_phd_with_lab_match():
    """B2 fires (Rice/Halas); lab_ip_alignment and houston_university_phd multipliers fire."""
    result = score_founder_pedigree(
        founder_name="FOUNDER_A",
        bio_text=(
            "PhD in Chemical Engineering from Rice University, Halas Lab, 2019. "
            "Co-author on 14 peer-reviewed papers in plasmonic photocatalysis."
        ),
        role="CTO",
        company_id="COMPANY_HX_G",
        company_licensed_ip_labs=["Rice Halas Lab"],
    )

    assert "B2" in _category_ids(result)
    assert "lab_ip_alignment" in _multiplier_ids(result)
    assert "houston_university_phd" in _multiplier_ids(result)
    _assert_tier_min(result, "HIGH")
    assert result.confidence == "HIGH"


# ---------------------------------------------------------------------------
# FP-TC-02 — Schlumberger Principal Engineer → B1 + service_co_senior multiplier
# ---------------------------------------------------------------------------


def test_fp_tc02_service_co_principal():
    """B1 fires (Schlumberger + Principal Engineer); service_co_senior multiplier fires."""
    result = score_founder_pedigree(
        founder_name="FOUNDER_B",
        bio_text=(
            "12 years at Schlumberger, most recently as Principal Engineer for "
            "completions technology. Holds 8 issued patents in downhole sensing."
        ),
        role="CEO",
        company_id="COMPANY_HX_B",
    )

    assert "B1" in _category_ids(result)
    assert "service_co_senior" in _multiplier_ids(result)
    _assert_tier_min(result, "MEDIUM-HIGH")
    assert result.confidence == "MEDIUM"


# ---------------------------------------------------------------------------
# FP-TC-03 — Activate Houston Fellow + Berkeley PhD → B4 + B2 + multiplier
# ---------------------------------------------------------------------------


def test_fp_tc03_activate_houston_fellow():
    """B4 fires (Activate Houston); B2 fires (Berkeley/Ceder); houston_accelerator_program fires."""
    result = score_founder_pedigree(
        founder_name="FOUNDER_C",
        bio_text="Activate Houston Fellow (Cohort 2). PhD Materials Science, UC Berkeley (Ceder group), 2022.",
        role="Co-founder",
        company_id="COMPANY_HX_C",
    )

    assert "B2" in _category_ids(result)
    assert "B4" in _category_ids(result)
    assert "houston_accelerator_program" in _multiplier_ids(result)
    _assert_tier_min(result, "MEDIUM-HIGH")
    assert result.confidence == "HIGH"


# ---------------------------------------------------------------------------
# FP-TC-04 — Serial founder: acquired by Halliburton + Baker Hughes early career
# ---------------------------------------------------------------------------


def test_fp_tc04_serial_founder_acquired_to_major():
    """B3 fires ('acquired by Halliburton'); B1 fires (Baker Hughes + early career)."""
    result = score_founder_pedigree(
        founder_name="FOUNDER_D",
        bio_text=(
            "Founder/CEO of Acme Sensors (acquired by Halliburton 2019). "
            "Prior to Acme, founded MethaneTech (still operating, Series B). "
            "8 years at Baker Hughes early career."
        ),
        role="CEO",
        company_id="COMPANY_HX_D",
    )

    assert "B3" in _category_ids(result)
    assert "B1" in _category_ids(result)
    _assert_tier_min(result, "HIGH")
    assert result.confidence == "HIGH"


# ---------------------------------------------------------------------------
# FP-TC-05 — Former NETL Lab Director
# ---------------------------------------------------------------------------


def test_fp_tc05_lab_director_houston_relevant():
    """B5 fires (Director at NETL → very high position); tier at least HIGH."""
    result = score_founder_pedigree(
        founder_name="FOUNDER_E",
        bio_text="Former Director of CO2 Capture R&D at NETL (2014-2022). Senior Scientist 2008-2014.",
        role="CSO",
        company_id="COMPANY_HX_E",
    )

    assert "B5" in _category_ids(result)
    _assert_tier_min(result, "HIGH")
    assert result.confidence == "HIGH"


# ---------------------------------------------------------------------------
# FP-TC-06 — McKinsey solo founder, no technical co-founder → LOW-MEDIUM cap
# ---------------------------------------------------------------------------


def test_fp_tc06_consultant_solo_founder_low():
    """B6 fires (McKinsey); consulting_solo_no_technical_cofounder guard downgrades; review_queue=True."""
    result = score_founder_pedigree(
        founder_name="FOUNDER_F",
        bio_text="10 years at McKinsey Energy Practice, focused on upstream operations strategy. MBA, Wharton, 2014.",
        role="Founder",
        company_id="COMPANY_HX_F",
        is_solo_founder=True,
        has_technical_cofounder=False,
    )

    assert "B6" in _category_ids(result)
    # The B6 match should carry the false-positive pattern ID
    b6_match = next(m for m in result.categories_matched if m.category == "B6")
    assert b6_match.pattern_id == "consulting_solo_no_technical_cofounder"
    _assert_tier_max(result, "LOW-MEDIUM")
    assert result.confidence == "MEDIUM"
    assert result.review_queue is True


# ---------------------------------------------------------------------------
# FP-TC-07 — Sparse bio → LOW tier, LOW confidence, review_queue=True
# ---------------------------------------------------------------------------


def test_fp_tc07_sparse_public_profile():
    """One-sentence bio produces no category matches; LOW/LOW; review_queue=True."""
    result = score_founder_pedigree(
        founder_name="FOUNDER_G",
        bio_text="Co-founder and Head of Engineering.",
        role="Co-founder",
        company_id="COMPANY_HX_G2",
    )

    assert result.categories_matched == []
    _assert_tier_max(result, "LOW")
    assert result.confidence == "LOW"
    assert result.review_queue is True


# ---------------------------------------------------------------------------
# FP-TC-08 — UH TcSUH PhD under Selvamanickam → B2 + houston_university_phd
# ---------------------------------------------------------------------------


def test_fp_tc08_uh_tcsuh_with_houston_multiplier():
    """B2 fires (UH/TcSUH/Selvamanickam); houston_university_phd multiplier fires."""
    result = score_founder_pedigree(
        founder_name="FOUNDER_H",
        bio_text=(
            "PhD Mechanical Engineering, University of Houston, TcSUH (Selvamanickam group), 2020. "
            "4 issued patents in 2G HTS wire manufacturing."
        ),
        role="CTO",
        company_id="COMPANY_HX_H",
    )

    assert "B2" in _category_ids(result)
    assert "houston_university_phd" in _multiplier_ids(result)
    _assert_tier_min(result, "MEDIUM-HIGH")
    assert result.confidence == "HIGH"
