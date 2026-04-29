"""
Houston presence scorer — v2 per docs/houston_presence_signals.md.

Assigns each company a Houston presence tier (A / A-low / B-high / B / B-low / C)
based on a composite signal score. Every assignment includes a per-signal trace for
full auditability.

Score is deterministic — no LLM calls in this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HOUSTON_ZIP_WHITELIST: frozenset[str] = frozenset(
    [f"{z:05d}" for z in range(77002, 77100)]    # Harris core
    + [f"{z:05d}" for z in range(77478, 77499)]  # Sugar Land
    + [f"{z:05d}" for z in range(77581, 77585)]  # Pearland
    + [f"{z:05d}" for z in range(77380, 77390)]  # The Woodlands
    + [f"{z:05d}" for z in range(77501, 77521)]  # Pasadena/Baytown
    + [f"{z:05d}" for z in range(77449, 77495)]  # Katy
)

HOUSTON_COUNTIES: frozenset[str] = frozenset({
    "Harris", "Fort Bend", "Montgomery", "Brazoria", "Galveston", "Waller",
})

HOUSTON_ACCELERATORS: frozenset[str] = frozenset({
    "Greentown Houston", "Activate Houston", "Halliburton Labs", "Ion",
})

HOUSTON_CO_INVESTOR_WHITELIST: frozenset[str] = frozenset({
    "Mercury Fund",
    "Goose Capital",
    "Energy Capital Ventures",
    "Cottonwood Venture Partners",
    "Veriten",
    "Artemis",
    "Houston Angel Network",
    "Texas HALO Fund",
    "HX Venture Fund",
    "Energy Transition Ventures",
    "Genesis Park",
    "Post Oak Energy Capital",
})

HOUSTON_MAJORS: frozenset[str] = frozenset({
    "ExxonMobil", "ConocoPhillips", "Phillips 66", "OXY", "Halliburton",
    "Baker Hughes", "SLB", "Chevron", "NRG", "CenterPoint", "Cheniere",
    "Williams", "Kinder Morgan", "EOG", "Enterprise Products",
})

HOUSTON_UNIVERSITIES: frozenset[str] = frozenset({
    "Rice", "Rice University",
    "UH", "University of Houston",
    "A&M", "Texas A&M",
    "UT-Austin", "UT Austin",
})

# HIGH signals that count toward high_operational_count (per spec §Tier assignment rules)
HIGH_OPERATIONAL_SIGNAL_IDS: frozenset[str] = frozenset({
    "form_d_houston_address",
    "texas_sos_houston_county_formation",
    "ercot_ia_signed_houston_zone",
    "houston_accelerator_residency",
    "doe_oced_hub_sub_awardee",
    "port_houston_lease",
    "form_5500_houston_sponsor",
})

# Canonical tier ordering for tier_min / tier_max assertions
TIER_RANK: dict[str, int] = {
    "C": 0, "B-low": 1, "B": 2, "B-high": 3, "A-low": 4, "A": 5,
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CompanyRecord:
    """Input to score_houston_presence(). Missing fields default to None / []."""

    company_id: str
    name: str
    canonical_domain: str | None = None
    is_houston_hq: bool | None = None       # None = unknown → review queue
    hq_city: str | None = None
    hq_state: str | None = None
    form_d: dict | None = None              # {address, zip, filed_by_law_firm, law_firm_name}
    texas_sos: dict | None = None           # {county, entity_type}
    ercot_interconnection: dict | None = None  # {milestone, load_zone, developer_matches_company}
    accelerator_membership: dict | None = None  # {name, physical}
    doe_oced_hub: dict | None = None        # {hub, role, project_location}
    port_houston_lease: bool = False
    form_5500: dict | None = None           # {zip, participant_count}
    investors: list[str] = field(default_factory=list)
    paid_pilots: list[dict] = field(default_factory=list)  # [{partner, site_named, language, is_mou_loi}]
    tmci_jlabs: dict | None = None
    houston_job_count: int = 0
    job_postings: list[dict] = field(default_factory=list)  # [{location, title}]
    innovationmap_features: list[str] = field(default_factory=list)
    university_research_partnerships: list[dict] = field(default_factory=list)  # [{university, dollar_value}]
    press_releases: list[dict] = field(default_factory=list)  # [{dateline, language, is_mou_loi}]
    texas_sos_foreign: bool = False
    event_speaking_slots: list[dict] = field(default_factory=list)
    founder_linkedin_locations: list[str] = field(default_factory=list)
    founder_alumni: list[str] = field(default_factory=list)
    multiple_houston_employees: bool = False
    employee_count: int | None = None


@dataclass
class SignalContribution:
    """A single signal's contribution to the composite score.

    Zero-weight entries (weight=0) are false-positive catches: they appear in
    signals_matched for auditability but do not count toward total_points.
    """

    signal_id: str            # e.g. "form_d_houston_address"
    weight: int               # 3 (HIGH), 2 (MEDIUM), 1 (LOW), 0 (false-positive excluded)
    category: str             # "HIGH" | "MEDIUM" | "LOW"
    is_operational: bool
    source: str               # which harvester / source
    raw_evidence: str         # the actual data point
    false_positive_flag: str | None  # set if a watch flag triggered


@dataclass
class HoustonPresenceResult:
    """Output of score_houston_presence() for a single company."""

    company_id: str
    tier: str                             # "A" | "A-low" | "B-high" | "B" | "B-low" | "C"
    total_points: int
    high_operational_count: int
    signals_matched: list[SignalContribution]  # includes zero-weight false-positive entries
    confidence: str                       # "HIGH" | "MEDIUM" | "LOW"
    review_queue: bool                    # True if tier ends in "-low", HQ unknown, or any flag fired
    notes: str                            # human-readable summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_zip(text: str) -> str:
    """Return the first 5-digit ZIP found in text, or empty string."""
    m = re.search(r"\b(\d{5})\b", text)
    return m.group(1) if m else ""


def _zip_in_houston(zip_str: str) -> bool:
    return zip_str.strip()[:5] in HOUSTON_ZIP_WHITELIST


# ── HIGH signal detectors (3 pts each) ─────────────────────────────────────


def _detect_form_d_houston_address(company: CompanyRecord) -> SignalContribution | None:
    if not company.form_d:
        return None
    fd = company.form_d
    address = fd.get("address", "")
    zip_code = str(fd.get("zip", "")) or _extract_zip(address)
    filed_by_law_firm = fd.get("filed_by_law_firm", False)

    is_houston_zip = _zip_in_houston(zip_code)
    is_houston_city = "houston" in address.lower()
    if not (is_houston_zip or is_houston_city):
        return None

    if filed_by_law_firm:
        law_firm = fd.get("law_firm_name", "law firm")
        return SignalContribution(
            signal_id="form_d_houston_address",
            weight=0,
            category="HIGH",
            is_operational=True,
            source="sec_edgar_form_d",
            raw_evidence=f"{address} (filed by {law_firm})",
            false_positive_flag="form_d_law_firm_address",
        )

    return SignalContribution(
        signal_id="form_d_houston_address",
        weight=3,
        category="HIGH",
        is_operational=True,
        source="sec_edgar_form_d",
        raw_evidence=address,
        false_positive_flag=None,
    )


def _detect_texas_sos_houston_county_formation(
    company: CompanyRecord,
) -> SignalContribution | None:
    if not company.texas_sos:
        return None
    county = company.texas_sos.get("county", "")
    if county not in HOUSTON_COUNTIES:
        return None
    return SignalContribution(
        signal_id="texas_sos_houston_county_formation",
        weight=3,
        category="HIGH",
        is_operational=True,
        source="texas_sos",
        raw_evidence=f"County: {county}",
        false_positive_flag=None,
    )


def _detect_ercot_ia_signed_houston_zone(company: CompanyRecord) -> SignalContribution | None:
    if not company.ercot_interconnection:
        return None
    ei = company.ercot_interconnection
    milestone = ei.get("milestone", "")
    load_zone = ei.get("load_zone", "")
    developer_matches = ei.get("developer_matches_company", False)
    if milestone != "IA-signed" or load_zone.lower() != "houston" or not developer_matches:
        return None
    return SignalContribution(
        signal_id="ercot_ia_signed_houston_zone",
        weight=3,
        category="HIGH",
        is_operational=True,
        source="ercot_interconnection_status",
        raw_evidence=f"IA-signed, load_zone={load_zone}",
        false_positive_flag=None,
    )


def _detect_houston_accelerator_residency(company: CompanyRecord) -> SignalContribution | None:
    if not company.accelerator_membership:
        return None
    am = company.accelerator_membership
    name = am.get("name", "")
    physical = am.get("physical", False)
    if name not in HOUSTON_ACCELERATORS or not physical:
        return None
    return SignalContribution(
        signal_id="houston_accelerator_residency",
        weight=3,
        category="HIGH",
        is_operational=True,
        source="accelerator_portfolio",
        raw_evidence=name,
        false_positive_flag=None,
    )


def _detect_doe_oced_hub_sub_awardee(company: CompanyRecord) -> SignalContribution | None:
    if not company.doe_oced_hub:
        return None
    hub = company.doe_oced_hub
    return SignalContribution(
        signal_id="doe_oced_hub_sub_awardee",
        weight=3,
        category="HIGH",
        is_operational=True,
        source="doe_oced",
        raw_evidence=f"{hub.get('hub')} — {hub.get('role')} — {hub.get('project_location')}",
        false_positive_flag=None,
    )


def _detect_port_houston_lease(company: CompanyRecord) -> SignalContribution | None:
    if not company.port_houston_lease:
        return None
    return SignalContribution(
        signal_id="port_houston_lease",
        weight=3,
        category="HIGH",
        is_operational=True,
        source="port_houston_commission_minutes",
        raw_evidence="Recorded lease/easement/license",
        false_positive_flag=None,
    )


def _detect_form_5500_houston_sponsor(company: CompanyRecord) -> SignalContribution | None:
    if not company.form_5500:
        return None
    f5 = company.form_5500
    zip_code = str(f5.get("zip", ""))
    participant_count = int(f5.get("participant_count", 0))
    if not _zip_in_houston(zip_code) or participant_count < 10:
        return None
    return SignalContribution(
        signal_id="form_5500_houston_sponsor",
        weight=3,
        category="HIGH",
        is_operational=True,
        source="efast_dol",
        raw_evidence=f"ZIP {zip_code}, {participant_count} participants",
        false_positive_flag=None,
    )


# ── MEDIUM signal detectors (2 pts each) ───────────────────────────────────


def _detect_founder_linkedin_houston(company: CompanyRecord) -> SignalContribution | None:
    for loc in company.founder_linkedin_locations:
        if "houston" in loc.lower():
            return SignalContribution(
                signal_id="founder_linkedin_houston",
                weight=2,
                category="MEDIUM",
                is_operational=False,
                source="linkedin",
                raw_evidence=loc,
                false_positive_flag=None,
            )
    return None


def _detect_multiple_houston_employees(company: CompanyRecord) -> SignalContribution | None:
    if not company.multiple_houston_employees:
        return None
    return SignalContribution(
        signal_id="multiple_houston_employees",
        weight=2,
        category="MEDIUM",
        is_operational=False,
        source="company_team_page",
        raw_evidence="≥3 employees or ≥30% of team in Houston",
        false_positive_flag=None,
    )


def _detect_houston_co_investor(company: CompanyRecord) -> SignalContribution | None:
    for investor in company.investors:
        if investor in HOUSTON_CO_INVESTOR_WHITELIST:
            return SignalContribution(
                signal_id="houston_co_investor",
                weight=2,
                category="MEDIUM",
                is_operational=False,
                source="investor_portfolio",
                raw_evidence=investor,
                false_positive_flag=None,
            )
    return None


def _detect_paid_pilot_houston_major(company: CompanyRecord) -> SignalContribution | None:
    for pilot in company.paid_pilots:
        partner = pilot.get("partner", "")
        is_mou_loi = pilot.get("is_mou_loi", False)
        site_named = pilot.get("site_named", "")
        if partner in HOUSTON_MAJORS and not is_mou_loi:
            return SignalContribution(
                signal_id="paid_pilot_houston_major",
                weight=2,
                category="MEDIUM",
                is_operational=False,
                source="sec_8k_ir_feeds",
                raw_evidence=f"{partner} — {site_named}",
                false_positive_flag=None,
            )
    return None


def _detect_tmci_jlabs_residency(company: CompanyRecord) -> SignalContribution | None:
    if not company.tmci_jlabs:
        return None
    return SignalContribution(
        signal_id="tmci_jlabs_residency",
        weight=2,
        category="MEDIUM",
        is_operational=False,
        source="tmc_innovation",
        raw_evidence=str(company.tmci_jlabs),
        false_positive_flag=None,
    )


def _detect_houston_job_postings_substantive(company: CompanyRecord) -> SignalContribution | None:
    if company.houston_job_count >= 3:
        return SignalContribution(
            signal_id="houston_job_postings_substantive",
            weight=2,
            category="MEDIUM",
            is_operational=False,
            source="job_feeds",
            raw_evidence=f"{company.houston_job_count} Houston job postings",
            false_positive_flag=None,
        )
    # Site-specific role keywords signal substantive Houston operations even with fewer postings
    site_kws = {"plant manager", "houston sales", "houston lead", "houston director"}
    for job in company.job_postings:
        title = job.get("title", "").lower()
        location = job.get("location", "").lower()
        if "houston" in location and any(kw in title for kw in site_kws):
            return SignalContribution(
                signal_id="houston_job_postings_substantive",
                weight=2,
                category="MEDIUM",
                is_operational=False,
                source="job_feeds",
                raw_evidence=f"Site-specific role: {job.get('title')}",
                false_positive_flag=None,
            )
    return None


def _detect_innovationmap_feature(company: CompanyRecord) -> SignalContribution | None:
    if not company.innovationmap_features:
        return None
    return SignalContribution(
        signal_id="innovationmap_feature",
        weight=2,
        category="MEDIUM",
        is_operational=False,
        source="innovationmap_rss",
        raw_evidence=company.innovationmap_features[0],
        false_positive_flag=None,
    )


def _detect_houston_university_research_partnership(
    company: CompanyRecord,
) -> SignalContribution | None:
    for p in company.university_research_partnerships:
        university = p.get("university", "")
        dollar_value = p.get("dollar_value")
        if any(u in university for u in HOUSTON_UNIVERSITIES) and dollar_value:
            return SignalContribution(
                signal_id="houston_university_research_partnership",
                weight=2,
                category="MEDIUM",
                is_operational=False,
                source="university_press_releases",
                raw_evidence=f"{university} — {dollar_value}",
                false_positive_flag=None,
            )
    return None


# ── LOW signal detectors (1 pt each) ───────────────────────────────────────


def _detect_houston_dateline_press_releases(
    company: CompanyRecord,
) -> list[SignalContribution]:
    """One entry per qualifying Houston-dateline press release. Always LOW."""
    results = []
    for pr in company.press_releases:
        dateline = pr.get("dateline", "")
        if "houston" not in dateline.lower():
            continue
        is_mou_loi = pr.get("is_mou_loi", False)
        flag = "mou_loi_partnership" if is_mou_loi else None
        results.append(
            SignalContribution(
                signal_id="houston_dateline_press_release",
                weight=1,
                category="LOW",
                is_operational=False,
                source="press_wire",
                raw_evidence=pr.get("language", dateline),
                false_positive_flag=flag,
            )
        )
    return results


def _detect_texas_sos_foreign_registration(company: CompanyRecord) -> SignalContribution | None:
    if not company.texas_sos_foreign:
        return None
    return SignalContribution(
        signal_id="texas_sos_foreign_registration",
        weight=1,
        category="LOW",
        is_operational=False,
        source="texas_sos",
        raw_evidence="Foreign entity registration",
        false_positive_flag=None,
    )


def _detect_event_speaking_slot(company: CompanyRecord) -> SignalContribution | None:
    if not company.event_speaking_slots:
        return None
    first = company.event_speaking_slots[0]
    event_name = first.get("event", "Houston event")
    return SignalContribution(
        signal_id="event_speaking_slot",
        weight=1,
        category="LOW",
        is_operational=False,
        source="event_programs",
        raw_evidence=event_name,
        false_positive_flag=None,
    )


def _detect_single_job_posting_houston(company: CompanyRecord) -> SignalContribution | None:
    """Fires only when houston_job_count is 1–2; ≥3 is handled by the substantive detector."""
    if 1 <= company.houston_job_count <= 2:
        return SignalContribution(
            signal_id="single_job_posting_houston",
            weight=1,
            category="LOW",
            is_operational=False,
            source="job_feeds",
            raw_evidence=f"{company.houston_job_count} Houston job posting(s)",
            false_positive_flag=None,
        )
    return None


def _detect_founder_alum_houston_university(company: CompanyRecord) -> SignalContribution | None:
    for alum in company.founder_alumni:
        if any(u in alum for u in HOUSTON_UNIVERSITIES):
            return SignalContribution(
                signal_id="founder_alum_houston_university",
                weight=1,
                category="LOW",
                is_operational=False,
                source="linkedin_biographical",
                raw_evidence=alum,
                false_positive_flag=None,
            )
    return None


# ---------------------------------------------------------------------------
# Tier, confidence, and notes assignment
# ---------------------------------------------------------------------------


def _assign_tier(
    is_houston_hq: bool | None,
    total_points: int,
    high_operational_count: int,
    only_low_signals_present: bool,
) -> tuple[str, bool]:
    """Return (tier, review_queue). Does not account for watch-flag override."""
    if is_houston_hq is None:
        return "B-low", True

    if is_houston_hq:
        if total_points >= 6 and high_operational_count >= 1:
            return "A", False
        return "A-low", True

    # Non-Houston HQ: only_low check runs first (resolution #8)
    if only_low_signals_present:
        return "B-low", True
    if total_points >= 5 and high_operational_count >= 1:
        return "B-high", False
    if total_points >= 3:
        # Upper bound of 5 in spec applies when B-high condition met; extend beyond 5
        # here to handle edge case of total_points > 5 with no HIGH operational signal.
        return "B", False
    if total_points >= 1:
        return "B-low", True
    return "C", False


def _assign_confidence(
    tier: str,
    signals_matched: list[SignalContribution],
) -> str:
    """Simplified confidence rules per resolution #9."""
    contributing = [s for s in signals_matched if s.weight > 0]

    if tier == "C":
        return "HIGH"  # high confidence of absence

    high_sigs = [s for s in contributing if s.category == "HIGH"]
    medium_sigs = [s for s in contributing if s.category == "MEDIUM"]

    # Only LOW signals → LOW
    if not high_sigs and not medium_sigs:
        return "LOW"

    # ≥2 distinct signals with at least one HIGH → HIGH
    if len(contributing) >= 2 and high_sigs:
        return "HIGH"

    # 1 HIGH signal alone → MEDIUM
    if len(high_sigs) == 1 and not medium_sigs:
        return "MEDIUM"

    # ≥2 MEDIUM signals with no HIGH → MEDIUM
    if not high_sigs and len(medium_sigs) >= 2:
        return "MEDIUM"

    # 1 MEDIUM alone → LOW
    return "LOW"


def _generate_notes(
    tier: str,
    total_points: int,
    high_operational_count: int,
    signals_matched: list[SignalContribution],
) -> str:
    contributing = [s for s in signals_matched if s.weight > 0]
    signal_ids = ", ".join(s.signal_id for s in contributing) or "none"
    flags = [s.false_positive_flag for s in signals_matched if s.false_positive_flag]
    flag_summary = f" Flags: {', '.join(flags)}." if flags else ""

    if tier == "C":
        return (
            f"Tier C — no current Houston presence, sector-fit recruiting candidate."
            f" ({total_points} pts; {high_operational_count} HIGH operational)."
            f" Signals: {signal_ids}.{flag_summary}"
        )

    return (
        f"Tier {tier} ({total_points} pts; {high_operational_count} HIGH operational)."
        f" Signals: {signal_ids}.{flag_summary}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_houston_presence(company: CompanyRecord) -> HoustonPresenceResult:
    """Score a company's Houston presence and return a tiered result with audit trace.

    Pure function — no I/O, no LLM calls, no side effects.

    Args:
        company: CompanyRecord with any combination of signal fields. Missing
                 fields default to None / empty list and are handled gracefully.

    Returns:
        HoustonPresenceResult with tier, total_points, high_operational_count,
        signals_matched (full trace including zero-weight false positives),
        confidence, review_queue, and notes.
    """
    signals: list[SignalContribution] = []

    # HIGH signal detectors (3 pts each)
    for detect_fn in (  # type: ignore[assignment]
        _detect_form_d_houston_address,
        _detect_texas_sos_houston_county_formation,
        _detect_ercot_ia_signed_houston_zone,
        _detect_houston_accelerator_residency,
        _detect_doe_oced_hub_sub_awardee,
        _detect_port_houston_lease,
        _detect_form_5500_houston_sponsor,
    ):
        result = detect_fn(company)
        if result is not None:
            signals.append(result)

    # MEDIUM signal detectors (2 pts each)
    for detect_fn in (  # type: ignore[assignment]
        _detect_founder_linkedin_houston,
        _detect_multiple_houston_employees,
        _detect_houston_co_investor,
        _detect_paid_pilot_houston_major,
        _detect_tmci_jlabs_residency,
        _detect_houston_job_postings_substantive,
        _detect_innovationmap_feature,
        _detect_houston_university_research_partnership,
    ):
        result = detect_fn(company)
        if result is not None:
            signals.append(result)

    # LOW signal detectors (1 pt each)
    signals.extend(_detect_houston_dateline_press_releases(company))
    for detect_fn in (  # type: ignore[assignment]
        _detect_texas_sos_foreign_registration,
        _detect_event_speaking_slot,
        _detect_single_job_posting_houston,
        _detect_founder_alum_houston_university,
    ):
        result = detect_fn(company)
        if result is not None:
            signals.append(result)

    # Aggregate (weight=0 false-positive entries excluded from point sums)
    contributing = [s for s in signals if s.weight > 0]
    total_points = sum(s.weight for s in contributing)
    high_operational_count = sum(
        1 for s in contributing if s.signal_id in HIGH_OPERATIONAL_SIGNAL_IDS
    )
    only_low_signals_present = bool(
        contributing and all(s.category == "LOW" for s in contributing)
    )

    tier, review_queue = _assign_tier(
        company.is_houston_hq,
        total_points,
        high_operational_count,
        only_low_signals_present,
    )

    # Watch-flag override: any flagged entry → review queue
    if any(s.false_positive_flag for s in signals):
        review_queue = True

    confidence = _assign_confidence(tier, signals)
    notes = _generate_notes(tier, total_points, high_operational_count, signals)

    return HoustonPresenceResult(
        company_id=company.company_id,
        tier=tier,
        total_points=total_points,
        high_operational_count=high_operational_count,
        signals_matched=signals,
        confidence=confidence,
        review_queue=review_queue,
        notes=notes,
    )
