"""
Shared data models and constants for the Houston Energy Mapper pipeline.

All signal detectors, hard-exclude rules, and enrichment modules import
CompanyRecord and shared constants from this module.

Absence convention: None and empty list are treated identically by all
signal detectors and hard-exclude rules. `not field` is True for both.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Shared constants
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

# HIGH signals that count toward high_operational_count in houston_presence scoring
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

# Founder pedigree tier ordering for tier_min / tier_max assertions
PEDIGREE_TIER_RANK: dict[str, int] = {
    "LOW": 0, "LOW-MEDIUM": 1, "MEDIUM": 2, "MEDIUM-HIGH": 3, "HIGH": 4,
}

# Canonical aliases for major companies and service cos (used by B1 detector and multipliers)
COMPANY_ALIASES: dict[str, frozenset[str]] = {
    "SLB": frozenset({"SLB", "Schlumberger"}),
    "HAL": frozenset({"HAL", "Halliburton"}),
    "BHGE": frozenset({"BHGE", "Baker Hughes", "Baker Hughes GE"}),
    "ExxonMobil": frozenset({"ExxonMobil", "Exxon", "Mobil", "ExxonMobil LCS"}),
    "OXY": frozenset({"OXY", "Occidental", "Occidental Petroleum"}),
    "BP": frozenset({"BP", "British Petroleum", "bp"}),
    "Shell": frozenset({"Shell", "Royal Dutch Shell"}),
    "Chevron": frozenset({"Chevron", "Chevron Corporation"}),
    "ConocoPhillips": frozenset({"ConocoPhillips", "Conoco", "Phillips"}),
    "Phillips 66": frozenset({"Phillips 66"}),
    "Marathon": frozenset({"Marathon", "Marathon Oil", "Marathon Petroleum"}),
    "TotalEnergies": frozenset({"TotalEnergies", "Total"}),
    "Weatherford": frozenset({"Weatherford"}),
    "NOV": frozenset({"NOV", "National Oilwell Varco"}),
}


def resolves_to_major(name: str) -> str | None:
    """Return canonical key if name matches any COMPANY_ALIASES entry, else None.

    Matching is case-insensitive substring: name resolves to a canonical key if
    any alias in that key's frozenset is found (case-insensitively) in name, or
    name is found in any alias.
    """
    name_lower = name.lower()
    for canonical, aliases in COMPANY_ALIASES.items():
        for alias in aliases:
            alias_lower = alias.lower()
            if alias_lower in name_lower or name_lower in alias_lower:
                return canonical
    return None

# Corporate VC arms that signal strategic validation (used by HX-02 hard-exclude)
CORPORATE_VC_WHITELIST: frozenset[str] = frozenset({
    "CTV",
    "Chevron Technology Ventures",
    "SLB Ventures",
    "bp Ventures",
    "Shell Ventures",
    "Equinor Ventures",
    "OGCI Climate Investments",
    "Aramco Ventures",
    "ExxonMobil Low Carbon Solutions",
    "ExxonMobil LCS",
    "Baker Hughes Energy Ventures",
    "BHEV",
    "Halliburton Labs",
})

# ---------------------------------------------------------------------------
# Canonical company record
# ---------------------------------------------------------------------------


@dataclass
class CompanyRecord:
    """Canonical input record passed to all signal detectors and hard-exclude rules.

    Absence convention: None and empty list are treated identically — `not field`
    is True for both and is the preferred absence check throughout the codebase.
    All fields default to None or empty list; only company_id and name are required.
    """

    # ── Core identity ────────────────────────────────────────────────────────
    company_id: str
    name: str
    canonical_domain: str | None = None

    # ── Houston presence fields ──────────────────────────────────────────────
    is_houston_hq: bool | None = None        # None = unknown → review queue
    hq_city: str | None = None
    hq_state: str | None = None
    form_d: dict | None = None               # {address, zip, filed_by_law_firm, law_firm_name,
                                             #  use_of_proceeds}
    texas_sos: dict | None = None            # {county, entity_type}
    ercot_interconnection: dict | None = None  # {milestone, load_zone, developer_matches_company}
    accelerator_membership: dict | None = None  # {name, physical}
    doe_oced_hub: dict | None = None         # {hub, role, project_location}
    port_houston_lease: bool = False
    form_5500: dict | None = None            # {zip, participant_count}
    investors: list[str] = field(default_factory=list)
    paid_pilots: list[dict] = field(default_factory=list)   # [{partner, site_named, language, is_mou_loi}]
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

    # ── Venture-scale fields ─────────────────────────────────────────────────
    description: str = ""
    primary_business: str | None = None      # "consulting" | "services" | "manufacturer" | "software" | etc.
    entity_type: str | None = None           # "LLC" | "LP" | "Corporation" | "Holdings"
    business_model: str | None = None        # free text or controlled vocabulary
    products: list[str] = field(default_factory=list)
    customers: list[str] = field(default_factory=list)
    parent_organization: str | None = None
    is_subsidiary: bool = False
    technology_vendor_identity: str | None = None  # set when entity has a distinct tech product identity
    most_recent_round: dict | None = None    # {round_type, amount_usd, language, use_of_proceeds}
    federal_grants: list[dict] = field(default_factory=list)  # [{program, phase, year}]
    patents: list[dict] = field(default_factory=list)         # [{cpc, status, count}]
    licensed_ip_labs: list[str] = field(default_factory=list) # e.g. ["Rice Halas Lab"]
    founders: list[dict] = field(default_factory=list)        # [{name, role, bio_text, linkedin_url}]
                                                              # populated by enrich/founder.py
