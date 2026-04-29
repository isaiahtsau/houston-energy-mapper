"""
Tests for signals/houston_presence.py — v2 spec (docs/houston_presence_signals.md).

Ten synthetic test cases covering every tier, the only_low_signals_present override,
the law-firm false-positive watch flag, and the confidence/review_queue logic.

Fixtures are documented in tests/fixtures/houston_presence_cases.json.
CompanyRecord objects are constructed inline for clarity.
"""
import pytest

from signals.houston_presence import (
    CompanyRecord,
    HoustonPresenceResult,
    TIER_RANK,
    score_houston_presence,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def contributing_ids(result: HoustonPresenceResult) -> list[str]:
    """Signal IDs that contributed points (weight > 0)."""
    return [s.signal_id for s in result.signals_matched if s.weight > 0]


def all_flags(result: HoustonPresenceResult) -> list[str]:
    """All false_positive_flag values set across any signal."""
    return [s.false_positive_flag for s in result.signals_matched if s.false_positive_flag]


# ---------------------------------------------------------------------------
# TC-01 — Houston HQ, Form D + Greentown residency → Tier A
# ---------------------------------------------------------------------------


def test_tc01_houston_hq_strong():
    """Houston-HQ with Form D (77002) + Greentown physical residency → Tier A, HIGH confidence."""
    company = CompanyRecord(
        company_id="COMPANY_A",
        name="COMPANY_A",
        is_houston_hq=True,
        form_d={"address": "1234 Main St, Houston, TX 77002", "zip": "77002", "filed_by_law_firm": False},
        accelerator_membership={"name": "Greentown Houston", "physical": True},
    )
    result = score_houston_presence(company)

    assert result.tier == "A"
    assert result.total_points >= 6
    assert result.high_operational_count >= 2
    assert "form_d_houston_address" in contributing_ids(result)
    assert "houston_accelerator_residency" in contributing_ids(result)
    assert result.confidence == "HIGH"
    assert result.review_queue is False


# ---------------------------------------------------------------------------
# TC-02 — Boston HQ, Halliburton Labs + Phillips 66 pilot → Tier B-high
# ---------------------------------------------------------------------------


def test_tc02_tier_b_high_accelerator_plus_pilot():
    """Non-HQ company with HIGH accelerator + MEDIUM paid pilot at Houston major → B-high."""
    company = CompanyRecord(
        company_id="COMPANY_B",
        name="COMPANY_B",
        is_houston_hq=False,
        hq_city="Boston, MA",
        accelerator_membership={"name": "Halliburton Labs", "physical": True},
        paid_pilots=[
            {
                "partner": "Phillips 66",
                "site_named": "Sweeny Refinery, Houston metro",
                "language": "executed paid pilot",
                "is_mou_loi": False,
            }
        ],
    )
    result = score_houston_presence(company)

    assert result.tier == "B-high"
    assert result.total_points >= 5
    assert result.high_operational_count >= 1
    assert "houston_accelerator_residency" in contributing_ids(result)
    assert "paid_pilot_houston_major" in contributing_ids(result)
    assert result.confidence == "HIGH"
    assert result.review_queue is False


# ---------------------------------------------------------------------------
# TC-03 — Denver HQ, Mercury Fund + 4 Houston jobs → Tier B
# ---------------------------------------------------------------------------


def test_tc03_tier_b_co_investor_plus_jobs():
    """Non-HQ with Houston co-investor (MEDIUM) + substantive job postings (MEDIUM) → Tier B."""
    company = CompanyRecord(
        company_id="COMPANY_C",
        name="COMPANY_C",
        is_houston_hq=False,
        hq_city="Denver, CO",
        investors=["Mercury Fund", "DCVC", "Lowercarbon Capital"],
        houston_job_count=4,
    )
    result = score_houston_presence(company)

    assert result.tier == "B"
    assert 4 <= result.total_points <= 5
    assert result.high_operational_count == 0
    assert "houston_co_investor" in contributing_ids(result)
    assert "houston_job_postings_substantive" in contributing_ids(result)
    assert result.confidence == "MEDIUM"
    assert result.review_queue is False


# ---------------------------------------------------------------------------
# TC-04 — SF HQ, MOU press release only → Tier B-low with mou_loi flag
# ---------------------------------------------------------------------------


def test_tc04_tier_b_low_mou_pr_with_flag():
    """Houston-dateline PR for an MOU → LOW signal with mou_loi_partnership flag, B-low."""
    company = CompanyRecord(
        company_id="COMPANY_D",
        name="COMPANY_D",
        is_houston_hq=False,
        hq_city="San Francisco, CA",
        press_releases=[
            {
                "dateline": "Houston, TX",
                "language": "MOU to explore CCS partnership with OXY",
                "is_mou_loi": True,
            }
        ],
    )
    result = score_houston_presence(company)

    assert result.tier == "B-low"
    assert result.total_points <= 2
    assert result.high_operational_count == 0
    assert "houston_dateline_press_release" in contributing_ids(result)
    assert "mou_loi_partnership" in all_flags(result)
    assert result.confidence == "LOW"
    assert result.review_queue is True


# ---------------------------------------------------------------------------
# TC-05 — Cambridge HQ, no signals → Tier C, HIGH confidence
# ---------------------------------------------------------------------------


def test_tc05_tier_c_no_signals():
    """No Houston signals → Tier C with 0 points, HIGH confidence, notes contain recruiting hint."""
    company = CompanyRecord(
        company_id="COMPANY_E",
        name="COMPANY_E",
        is_houston_hq=False,
        hq_city="Cambridge, MA",
        investors=["Khosla", "Founders Fund"],
    )
    result = score_houston_presence(company)

    assert result.tier == "C"
    assert result.total_points == 0
    assert result.high_operational_count == 0
    assert len(contributing_ids(result)) == 0
    assert result.confidence == "HIGH"
    assert result.review_queue is False
    assert "Tier C — no current Houston presence, sector-fit recruiting candidate" in result.notes


# ---------------------------------------------------------------------------
# TC-06 — Atlanta HQ, V&E law-firm Form D → false positive excluded, review queue
# ---------------------------------------------------------------------------


def test_tc06_form_d_law_firm_false_positive_excluded():
    """Form D filed by Vinson & Elkins → zero-weight entry with flag; no contributing signal."""
    company = CompanyRecord(
        company_id="COMPANY_F",
        name="COMPANY_F",
        is_houston_hq=False,
        hq_city="Atlanta, GA",
        form_d={
            "address": "910 Louisiana St, Houston, TX 77002",
            "zip": "77002",
            "filed_by_law_firm": True,
            "law_firm_name": "Vinson & Elkins LLP",
        },
    )
    result = score_houston_presence(company)

    # Tier must be at most B-low (C is also acceptable — C rank 0 ≤ B-low rank 1)
    assert TIER_RANK[result.tier] <= TIER_RANK["B-low"]
    # Flag must surface via signals_matched
    assert "form_d_law_firm_address" in all_flags(result)
    # form_d_houston_address must NOT be a contributing signal
    assert "form_d_houston_address" not in contributing_ids(result)
    assert result.review_queue is True


# ---------------------------------------------------------------------------
# TC-07 — Chicago HQ, five LOW signals only → B-low regardless of point total
# ---------------------------------------------------------------------------


def test_tc07_only_low_signals_routes_to_review():
    """Five LOW signals (5 pts) but no MEDIUM or HIGH → B-low via only_low_signals override."""
    company = CompanyRecord(
        company_id="COMPANY_G",
        name="COMPANY_G",
        is_houston_hq=False,
        hq_city="Chicago, IL",
        press_releases=[
            {"dateline": "Houston, TX", "language": "general announcement", "is_mou_loi": False}
        ],
        texas_sos_foreign=True,
        founder_alumni=["Rice University"],
        houston_job_count=1,
        event_speaking_slots=[{"event": "CERAWeek 2025"}],
    )
    result = score_houston_presence(company)

    assert result.tier == "B-low"
    assert result.total_points >= 4
    assert result.high_operational_count == 0
    # Every contributing signal must be LOW category
    assert all(s.category == "LOW" for s in result.signals_matched if s.weight > 0)
    assert result.confidence == "LOW"
    assert result.review_queue is True


# ---------------------------------------------------------------------------
# TC-08 — Houston HQ, Texas SOS (Harris) + founder LinkedIn → Tier A-low
# ---------------------------------------------------------------------------


def test_tc08_houston_hq_weak_corroboration_a_low():
    """Houston HQ via Texas SOS (Harris county) + founder LinkedIn: 5 pts, A-low review."""
    company = CompanyRecord(
        company_id="COMPANY_H",
        name="COMPANY_H",
        is_houston_hq=True,
        texas_sos={"county": "Harris", "entity_type": "domestic"},
        founder_linkedin_locations=["Greater Houston Area"],
    )
    result = score_houston_presence(company)

    assert result.tier == "A-low"
    assert result.total_points >= 5
    assert result.high_operational_count >= 1
    assert result.review_queue is True


# ---------------------------------------------------------------------------
# TC-09 — Pittsburgh HQ, DOE OCED sub-awardee alone → at least Tier B
# ---------------------------------------------------------------------------


def test_tc09_doe_oced_single_high_signal_floor_b():
    """DOE OCED HyVelocity sub-awardee (HIGH, 3 pts) alone → tier ≥ B, MEDIUM confidence."""
    company = CompanyRecord(
        company_id="COMPANY_I",
        name="COMPANY_I",
        is_houston_hq=False,
        hq_city="Pittsburgh, PA",
        doe_oced_hub={
            "hub": "HyVelocity Gulf Coast",
            "role": "off-taker",
            "project_location": "La Porte, TX",
        },
    )
    result = score_houston_presence(company)

    assert TIER_RANK[result.tier] >= TIER_RANK["B"]
    assert result.total_points >= 3
    assert result.high_operational_count >= 1
    assert "doe_oced_hub_sub_awardee" in contributing_ids(result)
    assert result.confidence == "MEDIUM"


# ---------------------------------------------------------------------------
# TC-10 — Austin HQ, ERCOT IA-signed + Energy Capital Ventures → Tier B-high
# ---------------------------------------------------------------------------


def test_tc10_ercot_plus_co_investor_b_high():
    """ERCOT IA-signed Houston zone (HIGH) + ECV co-investor (MEDIUM) → B-high, HIGH confidence."""
    company = CompanyRecord(
        company_id="COMPANY_J",
        name="COMPANY_J",
        is_houston_hq=False,
        hq_city="Austin, TX",
        ercot_interconnection={
            "milestone": "IA-signed",
            "load_zone": "Houston",
            "developer_matches_company": True,
        },
        investors=["Energy Capital Ventures"],
    )
    result = score_houston_presence(company)

    assert result.tier == "B-high"
    assert result.total_points >= 5
    assert result.high_operational_count >= 1
    assert "ercot_ia_signed_houston_zone" in contributing_ids(result)
    assert "houston_co_investor" in contributing_ids(result)
    assert result.confidence == "HIGH"
