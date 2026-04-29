"""
Tests for signals/venture_scale.py — apply_hard_exclude_rules only.

Eight synthetic test cases covering all five HX rules (HX-01 through HX-05),
their guard conditions (VS-TC-03, VS-TC-08), and a legitimate pass-through
(VS-TC-07). CompanyRecord objects are constructed inline.

The LLM classifier (classify_venture_scale) is tested separately during
Phase 2 Step 6 after prompts/classifier_v1.md is drafted.
"""
import pytest

from models import CompanyRecord
from signals.venture_scale import HardExcludeResult, apply_hard_exclude_rules


# ---------------------------------------------------------------------------
# VS-TC-01 — HX-01: PF-Debt round type → excluded
# ---------------------------------------------------------------------------


def test_vs_tc01_pf_debt_round_excluded():
    """Round type 'PF-Debt' triggers HX-01; reason contains 'PF-Debt'."""
    company = CompanyRecord(
        company_id="COMPANY_HX_A",
        name="COMPANY_HX_A",
        description="Building a 200 MW green hydrogen production facility on the Gulf Coast.",
        most_recent_round={
            "round_type": "PF-Debt",
            "amount_usd": 300_000_000,
            "language": "non-recourse project debt facility for the Gulf Coast hydrogen plant",
        },
    )
    result = apply_hard_exclude_rules(company)

    assert result.excluded is True
    assert result.rule_id == "HX-01"
    assert result.reason is not None
    assert "PF-Debt" in result.reason


# ---------------------------------------------------------------------------
# VS-TC-02 — HX-02: Pure services, no IP / grants / CVC → excluded
# ---------------------------------------------------------------------------


def test_vs_tc02_pure_services_excluded():
    """Services description + no patents + no grants + no CVC triggers HX-02."""
    company = CompanyRecord(
        company_id="COMPANY_HX_B",
        name="COMPANY_HX_B",
        description="Reservoir engineering consulting for upstream operators.",
        primary_business="consulting",
    )
    result = apply_hard_exclude_rules(company)

    assert result.excluded is True
    assert result.rule_id == "HX-02"
    assert result.reason is not None
    assert "services" in result.reason.lower()


# ---------------------------------------------------------------------------
# VS-TC-03 — HX-02 guard: services language but ARPA-E grant present → NOT excluded
# ---------------------------------------------------------------------------


def test_vs_tc03_consulting_with_arpa_e_not_excluded():
    """HX-02 AND chain breaks when federal grant is present — company passes through."""
    company = CompanyRecord(
        company_id="COMPANY_HX_C",
        name="COMPANY_HX_C",
        description="Energy systems engineering with novel modeling approach.",
        primary_business="consulting",
        federal_grants=[{"program": "ARPA-E", "phase": "Phase II"}],
    )
    result = apply_hard_exclude_rules(company)

    assert result.excluded is False
    assert result.rule_id is None


# ---------------------------------------------------------------------------
# VS-TC-04 — HX-03: Single-asset SPV with DOE participant role → excluded
# ---------------------------------------------------------------------------


def test_vs_tc04_project_spv_excluded():
    """LLC name + 'special purpose vehicle' description + DOE project participant → HX-03."""
    company = CompanyRecord(
        company_id="COMPANY_HX_D",
        name="Bayou Hydrogen LLC",
        description=(
            "Special purpose vehicle for the Bayou Hydrogen Production Facility,"
            " a 50 MW PEM electrolyzer plant."
        ),
        entity_type="LLC",
        doe_oced_hub={"hub": "HyVelocity Gulf Coast", "role": "project participant"},
        form_d={"use_of_proceeds": "Bayou Hydrogen Production Facility"},
        technology_vendor_identity=None,
    )
    result = apply_hard_exclude_rules(company)

    assert result.excluded is True
    assert result.rule_id == "HX-03"
    assert result.reason is not None
    assert "SPV" in result.reason


# ---------------------------------------------------------------------------
# VS-TC-05 — HX-04: <5 employees + IP-licensing model + no products/customers → excluded
# ---------------------------------------------------------------------------


def test_vs_tc05_patent_troll_excluded():
    """IP-licensing entity with 3 employees, no products, no customers triggers HX-04."""
    company = CompanyRecord(
        company_id="COMPANY_HX_E",
        name="COMPANY_HX_E",
        description="Patent licensing and IP monetization for clean energy technologies.",
        employee_count=3,
        business_model="IP licensing",
    )
    result = apply_hard_exclude_rules(company)

    assert result.excluded is True
    assert result.rule_id == "HX-04"
    assert result.reason is not None
    assert "patent" in result.reason.lower()


# ---------------------------------------------------------------------------
# VS-TC-06 — HX-05: Wholly-owned OXY subsidiary → excluded
# ---------------------------------------------------------------------------


def test_vs_tc06_major_subsidiary_excluded():
    """Parent is OXY (in HOUSTON_MAJORS) + is_subsidiary=True triggers HX-05."""
    company = CompanyRecord(
        company_id="1PointFive",
        name="1PointFive",
        description=(
            "Wholly-owned subsidiary of Occidental Petroleum (OXY)"
            " focused on direct air capture."
        ),
        parent_organization="OXY",
        is_subsidiary=True,
    )
    result = apply_hard_exclude_rules(company)

    assert result.excluded is True
    assert result.rule_id == "HX-05"
    assert result.reason is not None
    assert "subsidiary" in result.reason.lower()


# ---------------------------------------------------------------------------
# VS-TC-07 — Legitimate candidate: passes all five HX rules
# ---------------------------------------------------------------------------


def test_vs_tc07_legitimate_passes_all_hard_excludes():
    """Strong venture-scale company clears every hard-exclude rule."""
    company = CompanyRecord(
        company_id="COMPANY_HX_G",
        name="COMPANY_HX_G",
        description=(
            "Plasmonic photocatalysis for ammonia synthesis at low temperature,"
            " licensed from Rice University Halas Lab."
        ),
        employee_count=47,
        patents=[
            {"cpc": "Y02E", "status": "issued", "count": 4},
            {"cpc": "Y02P", "status": "filed", "count": 2},
        ],
        federal_grants=[{"program": "ARPA-E", "phase": "SCALEUP"}],
        investors=["Mercury Fund", "Goose Capital", "Energy Capital Ventures"],
        most_recent_round={"round_type": "Series B", "amount_usd": 45_000_000},
    )
    result = apply_hard_exclude_rules(company)

    assert result.excluded is False
    assert result.rule_id is None


# ---------------------------------------------------------------------------
# VS-TC-08 — HX-03 safe harbor: LLC name + DOE mention but vendor identity set → NOT excluded
# ---------------------------------------------------------------------------


def test_vs_tc08_borderline_spv_with_vendor_identity_passes():
    """technology_vendor_identity safe harbor prevents HX-03 from firing."""
    company = CompanyRecord(
        company_id="COMPANY_HX_H",
        name="COMPANY_HX_H Holdings LLC",
        description=(
            "Develops modular electrolyzer technology;"
            " deployed first 5 MW unit in HyVelocity hub."
        ),
        entity_type="LLC",
        doe_oced_hub={"hub": "HyVelocity Gulf Coast", "role": "technology provider"},
        technology_vendor_identity="COMPANY_HX_H",
        employee_count=28,
        patents=[{"cpc": "Y02E", "status": "issued", "count": 3}],
    )
    result = apply_hard_exclude_rules(company)

    assert result.excluded is False
    assert result.rule_id is None
