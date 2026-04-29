# Houston Presence Signals — Composite Scorer Specification

**Module:** `signals/houston_presence.py`
**Tests:** `tests/signals/test_houston_presence.py`
**Fixtures:** `tests/fixtures/houston_presence_cases.json`
**Version:** v2
**Last updated:** 2026-04-28

### Changelog (v1 → v2)

- Added `CompanyRecord` input schema (resolution 1).
- Added `texas_sos_houston_county_formation` to `high_operational_signals` enumeration (resolution 3).
- Lowered B-high threshold from `≥6` to `≥5` points (resolution 5).
- `only_low_signals_present` check now runs **before** point thresholds in non-HQ branch (resolution 8).
- Added `TIER_RANK` canonical ordering for `tier_min` / `tier_max` assertions (resolution 7).
- Simplified and corrected confidence rules (resolution 9).
- Clarified watch-flag semantics: flags live on `SignalContribution.false_positive_flag`; zero-weight entries are included in `signals_matched` for auditability (resolution 6).
- Confirmed HQ status is a routing branch, not a point contributor (resolution 2).
- Confirmed `high_operational_count` tracks only HIGH-category signals in the enumeration (resolution 4).
- Confirmed `tier_min` / `tier_max` use `TIER_RANK` ordering (resolution 10).

---

## Spec review history

This spec was pressure-tested before implementation. The v1 draft was reviewed by the implementing agent, which surfaced 10 distinct ambiguities or judgment calls:

1. CompanyRecord input schema was undefined — required explicit dataclass.
2. TC-01 point arithmetic confirmation — total_points_min:6 semantics correct as ">=".
3. texas_sos_houston_county_formation missing from high_operational_signals enumeration — added.
4. high_operational_count tracking scope clarified — only HIGH-category signals in the enumeration.
5. TC-02 reached B-high at 5 points but spec required 6 — threshold lowered to 5 with rationale that one HIGH operational signal plus corroborating MEDIUM evidence (paid pilot at named Houston site) hits the B-high bar; requiring 6 overweighted accumulation versus signal kind.
6. Watch-flag semantics were ambiguous — clarified: flags live on SignalContribution.false_positive_flag as zero-weight entries; no top-level flag list on HoustonPresenceResult.
7. Tier ordering for tier_min/tier_max needed canonical rank — added TIER_RANK constant.
8. only_low_signals_present override evaluation order was unclear — moved to first check in non-HQ branch, before point thresholds.
9. Confidence rule referenced undefined "source category" — simplified to signal-kind + count.
10. tier_min semantics confirmed as floor assertion using TIER_RANK.

Eight of these were genuine corrections. Two (TC-01 arithmetic and tier_min semantics) were confirmations of existing intent. The implementation passed all 10 unit tests on first run after these revisions, suggesting the v2 spec was implementable without further interpretation.

This review pattern (spec → agent review → spec amendment → implementation) is the protocol used throughout the build for substantive modules.

---

## Purpose

This spec defines the composite signal scorer that assigns each company a Houston presence tier — A (Houston-headquartered), B (meaningful Houston activity, non-Houston HQ), or C (no current Houston presence). The scorer is **deterministic** — given the same input signals, it produces the same tier. It is **auditable** — every tier assignment includes a per-signal contribution trace. And it is **testable** — synthetic test cases exercise every boundary condition before any real classification happens.

Real-company calibration happens during Phase 3 (manual review of pipeline output). This spec does not pre-score named companies as ground truth — that would conflate code correctness with rubric calibration, and we want those validated separately.

---

## Tier definitions

- **Tier A.** Houston-headquartered or primary operations in Houston. Core dataset. The platform's home base.
- **Tier A-low.** Strong Houston-HQ signals but missing one corroborator. Surfaces to manual review.
- **Tier B-high.** Non-Houston HQ but multiple high-confidence operational Houston signals (e.g., active accelerator residency, signed pilot at a named Houston site, ERCOT IA-signed project in Houston load zone). Treated as platform-actionable for partner programming.
- **Tier B.** Non-Houston HQ with meaningful Houston activity. Platform extension zone.
- **Tier B-low.** Single weak signal, or press-release-only signals with no operational corroboration. Surfaces to manual review queue — never auto-promoted.
- **Tier C.** No current Houston presence. Recruiting target for the platform team if sector-fit is strong.

---

## Signal taxonomy

Every signal contributes points. Tier assignment is a function of total points plus the *kind* of signals present (operational vs. associational vs. press-release-only).

### HIGH signals (3 points each)

These are operational, verifiable, and high-confidence. A company with one HIGH signal plus one corroborator is sufficient evidence of meaningful Houston presence.

| Signal ID | Description | Source/method |
|-----------|-------------|---------------|
| `form_d_houston_address` | SEC EDGAR Form D filing with `primaryIssuer.issuerAddress.city = "Houston"` and ZIP in the Houston metro whitelist | EDGAR REST API |
| `texas_sos_houston_county_formation` | Texas SOS domestic entity formation in Harris, Fort Bend, Montgomery, Brazoria, Galveston, or Waller county | Texas Comptroller franchise tax search (free) |
| `ercot_ia_signed_houston_zone` | ERCOT generator interconnection at IA-signed milestone in a Houston load zone, with developer = the company | ERCOT Generator Interconnection Status (monthly XLSX) |
| `houston_accelerator_residency` | Active physical residency in a Houston accelerator: Greentown Houston (4200 San Jacinto), Activate Houston (Ion District), Halliburton Labs (verify physical vs. virtual), Ion residency | Accelerator portfolio pages |
| `doe_oced_hub_sub_awardee` | Listed as off-taker, technology provider, or sub-awardee in DOE OCED Hydrogen or DAC Hub project documents with Texas project location | energy.gov/oced; hyvelocityhub.com |
| `port_houston_lease` | Recorded lease, easement, or license action in Port Houston Commission monthly minutes | porthouston.com/about-us/commission |
| `form_5500_houston_sponsor` | Form 5500 employee benefit plan with Houston ZIP plan sponsor and ≥10 participants | efast.dol.gov/5500Search |

### MEDIUM signals (2 points each)

These are corroborating signals — meaningful but not sufficient on their own. Two MEDIUM signals roughly equal one HIGH signal in scoring weight.

| Signal ID | Description | Source/method |
|-----------|-------------|---------------|
| `founder_linkedin_houston` | Founder LinkedIn location = "Greater Houston Area" with non-Houston company HQ | Direct per-company URL fetch |
| `multiple_houston_employees` | ≥3 employees or ≥30% of team based in Houston | Company team pages, LinkedIn company page |
| `houston_co_investor` | Investor in a recent round is on the Houston-anchored co-investor whitelist (Mercury Fund, Goose Capital, Energy Capital Ventures, Cottonwood Venture Partners, Veriten/Artemis, Houston Angel Network, Texas HALO Fund, HX Venture Fund, Energy Transition Ventures, Genesis Park, Post Oak Energy Capital) | Investor portfolio pages, press releases |
| `paid_pilot_houston_major` | Paid pilot at a named Houston site with a Houston major (ExxonMobil, ConocoPhillips, Phillips 66, OXY, Halliburton, Baker Hughes, SLB, Chevron, NRG, CenterPoint, Cheniere, Williams, Kinder Morgan, EOG, Enterprise Products) | 8-K filings, corporate IR press feeds, BusinessWire |
| `tmci_jlabs_residency` | TMCi cohort with bio/industrial crossover, or JLABS @ TMC tenant | tmc.edu/innovation |
| `houston_job_postings_substantive` | ≥3 jobs posted with Houston location, OR ≥1 site-specific role (Plant Manager Houston, Houston Sales Lead, etc.) | Greenhouse / Lever / Ashby per-company JSON |
| `innovationmap_feature` | Feature coverage in InnovationMap or EnergyCapitalHTX (these outlets pre-filter for Houston relevance) | RSS feeds |
| `houston_university_research_partnership` | Sponsored research agreement with Rice/UH/A&M with named dollar value, or SBIR/STTR Phase I/II with Texas-university subaward | News.rice.edu, university press releases |

### LOW signals (1 point each)

Associational signals. Useful as soft corroborators but never sufficient alone. A company with only LOW signals routes to the B-low review queue regardless of total points.

| Signal ID | Description | Source/method |
|-----------|-------------|---------------|
| `houston_dateline_press_release` | Press release with Houston dateline but no operational specifics | Press wire scanning |
| `texas_sos_foreign_registration` | Texas SOS foreign-entity registration alone (required statewide for any out-of-state company "transacting business" in TX — defensive filing, low signal) | Texas SOS |
| `event_speaking_slot` | Speaking slot, panel appearance, or pitch participation at a Houston event (CERAWeek, OTC, Rice Business Plan Competition, Halliburton Labs Demo Day) without other operational tie | Event programs |
| `single_job_posting_houston` | One Houston-located job posting | Job feeds |
| `founder_alum_houston_university` | Founder is alum of Rice/UH/UT-Austin/A&M but no current operational tie | LinkedIn, biographical sources |

### False-positive watch flags (filter out before scoring)

These patterns *look like* signals but are systematically misleading. The scorer must check for these and either downgrade or exclude.

| Watch flag | Reason | Action |
|------------|--------|--------|
| `form_d_law_firm_address` | Form D filings filed by law firms (Vinson & Elkins at 910 Louisiana; Norton Rose Fulbright at 1301 McKinney; Baker Botts at 910 Louisiana, 1001 Fannin) often list the law firm's Houston address as principal — does NOT mean the issuer is Houston-based | Confirm via website + LinkedIn before treating as `form_d_houston_address` |
| `event_tourism_only` | Presented at CERAWeek / OTC / RBPC with no other operational signal — tens of thousands of foreign attendees do this annually | Excluded from MEDIUM tier; downgrade to LOW (`event_speaking_slot`) |
| `houston_dateline_only_pr` | Press release with Houston dateline because the *major partner* is Houston-HQ, not because the startup is | Treat as LOW signal only; never elevate to MEDIUM |
| `mou_loi_partnership` | Partnership press releases with "exploring," "evaluating," "to consider," "MOU," "LOI" language | Excluded from MEDIUM tier; treat as LOW |
| `single_major_dependence` | ≥80% of the company's revenue or named partnerships are with a single IOC | Flag as vendor-not-venture pattern; surface to manual review |
| `rrc_well_operator_non_houston_county` | Operator of an O&G well in non-Houston Texas county (RRC permits — the *county* field matters; Houston-based operators drill in Permian/Eagle Ford constantly) | Excluded — county field check required |

---

## Houston metro ZIP whitelist

Used by `form_d_houston_address` and any signal that requires Houston-metro location verification.

```
Harris core:           77002–77099
Sugar Land:            77478–77498
Pearland:              77581–77584
The Woodlands:         77380–77389
Pasadena/Baytown:      77501–77520
Katy:                  77449–77494
```

---

## Tier assignment rules

After all signals are evaluated and false-positive watch flags applied, sum the points. Tier is determined by point total *plus* the qualitative pattern of signals present.

```
total_points = sum(signal.weight for signal in present_signals)
                                                           # weight=0 (false-positive) entries excluded
high_operational_count = count of HIGH signals (weight > 0) with signal_id in:
  form_d_houston_address, texas_sos_houston_county_formation,   # ← texas_sos added in v2
  ercot_ia_signed_houston_zone, houston_accelerator_residency,
  doe_oced_hub_sub_awardee, port_houston_lease, form_5500_houston_sponsor

only_low_signals_present = (
    len(contributing_signals) > 0
    AND all(s.category == "LOW" for s in contributing_signals)
)

IF company is Houston-HQ (is_houston_hq = True):
    IF total_points >= 6 AND high_operational_count >= 1:
        tier = "A"
    ELSE:
        tier = "A-low"  (HQ claim with weak corroboration — review queue)

ELIF company is non-Houston HQ (is_houston_hq = False):
    # only_low check runs FIRST — before point thresholds (v2 fix)
    IF only_low_signals_present:
        tier = "B-low"  (review queue regardless of point total)
    ELIF total_points >= 5 AND high_operational_count >= 1:   # threshold 5 in v2 (was 6)
        tier = "B-high"
    ELIF total_points >= 3:
        tier = "B"
    ELIF total_points >= 1:
        tier = "B-low"  (review queue)
    ELSE:
        tier = "C"

ELSE (is_houston_hq = None — unknown):
    tier = "B-low"  (review queue — needs human disambiguation)

# Post-processing: any watch flag triggers review regardless of tier
IF any(s.false_positive_flag for s in signals_matched):
    review_queue = True
```

**Tier rank (canonical ordering for `tier_min` / `tier_max` assertions):**

```python
TIER_RANK = {"C": 0, "B-low": 1, "B": 2, "B-high": 3, "A-low": 4, "A": 5}
```

`tier_min` asserts `TIER_RANK[result.tier] >= TIER_RANK[expected_min]`.
`tier_max` asserts `TIER_RANK[result.tier] <= TIER_RANK[expected_max]`.

**Note on `only_low_signals_present`.** Even if a company accumulates 5 LOW signals (hypothetical 5 points), the absence of any MEDIUM or HIGH signal means none of the evidence is operational. The company gets B-low and routes to manual review. The scorer is biased *against* false confidence built from associational signals.

**Note on HQ as routing branch.** `is_houston_hq` is a branch condition, not a point contributor. Points come only from detected signals.

---

## Input schema

```python
@dataclass
class CompanyRecord:
    company_id: str
    name: str
    canonical_domain: str | None
    is_houston_hq: bool | None  # None = unknown, surfaces to review
    hq_city: str | None
    hq_state: str | None
    form_d: dict | None  # {address, zip, filed_by_law_firm, law_firm_name}
    texas_sos: dict | None  # {county, entity_type}
    ercot_interconnection: dict | None  # {milestone, load_zone, developer_matches_company}
    accelerator_membership: dict | None  # {name, physical}
    doe_oced_hub: dict | None  # {hub, role, project_location}
    port_houston_lease: bool
    form_5500: dict | None  # {zip, participant_count}
    investors: list[str]
    paid_pilots: list[dict]  # [{partner, site_named, language, is_mou_loi}]
    tmci_jlabs: dict | None
    houston_job_count: int
    job_postings: list[dict]  # [{location, title}]
    innovationmap_features: list[str]  # URLs of feature articles
    university_research_partnerships: list[dict]  # [{university, dollar_value}]
    press_releases: list[dict]  # [{dateline, language, is_mou_loi}]
    texas_sos_foreign: bool
    event_speaking_slots: list[dict]
    founder_linkedin_locations: list[str]
    founder_alumni: list[str]
    multiple_houston_employees: bool
    employee_count: int | None
```

Where data is missing for a given company, fields default to `None` or empty list. The scorer must handle absence gracefully.

---

## Output schema

The scorer returns a structured object for every company:

```python
@dataclass
class HoustonPresenceResult:
    company_id: str                # canonical ID
    tier: str                      # "A" | "A-low" | "B-high" | "B" | "B-low" | "C"
    total_points: int
    high_operational_count: int
    signals_matched: list[SignalContribution]  # full audit trail
    confidence: str                # "HIGH" | "MEDIUM" | "LOW"
    review_queue: bool             # True if tier ends in "-low"
    notes: str                     # human-readable reasoning summary

@dataclass
class SignalContribution:
    signal_id: str                 # e.g., "form_d_houston_address"
    weight: int                    # 3, 2, or 1
    category: str                  # "HIGH" | "MEDIUM" | "LOW"
    is_operational: bool
    source: str                    # which harvester/source produced the evidence
    raw_evidence: str              # the actual data point (URL, address, employee count)
    false_positive_flag: str | None  # set if watch flag triggered
```

**Confidence flag rules (simplified per v2):**

- `HIGH`: ≥2 distinct signal IDs matched, AND at least one is HIGH-category.
- `MEDIUM`: 1 HIGH signal alone; OR ≥2 MEDIUM signals with no HIGH.
- `LOW`: only LOW signals matched; OR 1 MEDIUM signal alone; OR 1 LOW signal alone; OR 0 signals (when tier ≠ C).

Special case: when `tier == "C"` (0 points, no signals), confidence is `HIGH` — we are highly confident of absence.

**Watch-flag semantics:** When a watch flag triggers, the scorer includes a `SignalContribution` with `weight=0` and the flag set in `false_positive_flag`. This zero-weight entry appears in `signals_matched` for auditability but does not contribute to `total_points` or `high_operational_count`. There is no top-level `false_positive_flags` field on `HoustonPresenceResult` — callers iterate `signals_matched` and inspect `false_positive_flag` on each entry. Additionally, if any watch flag fires, `review_queue` is set to `True` regardless of tier (a human should confirm the flag was correctly applied).

---

## Synthetic test cases

These are unit tests for code logic. They use placeholder company names (`COMPANY_A`, etc.) — they are NOT a calibration set of real companies. Real-company calibration happens during Phase 3.

Each test case asserts the tier, total_points, and at least one expected signal_id from `signals_matched`. The `evidence` block describes the synthetic input shape.

```json
[
  {
    "case_id": "TC-01_houston_hq_strong",
    "description": "Houston-HQ with Form D + Greentown residency. Strongest Tier A signal.",
    "evidence": {
      "is_houston_hq": true,
      "form_d_address": "1234 Main St, Houston, TX 77002",
      "form_d_filed_by_law_firm": false,
      "accelerator_membership": "Greentown Houston",
      "accelerator_physical": true
    },
    "expected": {
      "tier": "A",
      "total_points_min": 6,
      "high_operational_count_min": 2,
      "signals_required": ["form_d_houston_address", "houston_accelerator_residency"],
      "confidence": "HIGH",
      "review_queue": false
    }
  },
  {
    "case_id": "TC-02_tier_b_high_pilot_plus_accelerator",
    "description": "Boston-HQ company with Halliburton Labs cohort + signed pilot at Phillips 66 Houston refinery.",
    "evidence": {
      "is_houston_hq": false,
      "hq_city": "Boston, MA",
      "accelerator_membership": "Halliburton Labs",
      "accelerator_physical": true,
      "paid_pilot": {
        "partner": "Phillips 66",
        "site_named": "Sweeny Refinery, Houston metro",
        "language": "executed paid pilot"
      }
    },
    "expected": {
      "tier": "B-high",
      "total_points_min": 5,
      "high_operational_count_min": 1,
      "signals_required": ["houston_accelerator_residency", "paid_pilot_houston_major"],
      "confidence": "HIGH",
      "review_queue": false
    }
  },
  {
    "case_id": "TC-03_tier_b_co_investor_plus_jobs",
    "description": "Denver-HQ company with Mercury Fund as co-investor and 4 Houston job postings — clean Tier B.",
    "evidence": {
      "is_houston_hq": false,
      "hq_city": "Denver, CO",
      "investors": ["Mercury Fund", "DCVC", "Lowercarbon Capital"],
      "houston_job_count": 4
    },
    "expected": {
      "tier": "B",
      "total_points_min": 4,
      "total_points_max": 5,
      "high_operational_count_min": 0,
      "signals_required": ["houston_co_investor", "houston_job_postings_substantive"],
      "confidence": "MEDIUM",
      "review_queue": false
    }
  },
  {
    "case_id": "TC-04_tier_b_low_pr_only",
    "description": "Press release tour only — Houston-dateline announcement of MOU with OXY, no operational corroboration.",
    "evidence": {
      "is_houston_hq": false,
      "hq_city": "San Francisco, CA",
      "press_releases": [
        {
          "dateline": "Houston, TX",
          "language": "MOU to explore CCS partnership with OXY",
          "is_mou_loi_only": true
        }
      ]
    },
    "expected": {
      "tier": "B-low",
      "total_points_max": 2,
      "high_operational_count": 0,
      "signals_required": ["houston_dateline_press_release"],
      "false_positive_flags_required": ["mou_loi_partnership"],
      "confidence": "LOW",
      "review_queue": true
    }
  },
  {
    "case_id": "TC-05_tier_c_no_signals",
    "description": "Boston-HQ fusion startup, sector-fit but no Houston signals at all.",
    "evidence": {
      "is_houston_hq": false,
      "hq_city": "Cambridge, MA",
      "sector": "Fusion",
      "investors": ["Khosla", "Founders Fund"],
      "houston_signals": []
    },
    "expected": {
      "tier": "C",
      "total_points_max": 0,
      "high_operational_count": 0,
      "signals_matched_count": 0,
      "confidence": "HIGH",
      "review_queue": false,
      "notes_should_contain": "Tier C — no current Houston presence, sector-fit recruiting candidate"
    }
  },
  {
    "case_id": "TC-06_form_d_law_firm_false_positive",
    "description": "Form D filed by Vinson & Elkins at 910 Louisiana, Houston — looks like Houston address but is the law firm's address. Must be downgraded.",
    "evidence": {
      "is_houston_hq": false,
      "hq_city": "Atlanta, GA",
      "form_d_address": "910 Louisiana St, Houston, TX 77002",
      "form_d_filed_by_law_firm": true,
      "form_d_law_firm": "Vinson & Elkins LLP"
    },
    "expected": {
      "tier_max": "B-low",
      "false_positive_flags_required": ["form_d_law_firm_address"],
      "signals_should_not_include": ["form_d_houston_address"],
      "review_queue": true
    }
  },
  {
    "case_id": "TC-07_only_low_signals_routes_to_review",
    "description": "Five LOW signals total 5 points — but no operational signal present, so routes to B-low review queue regardless of point total.",
    "evidence": {
      "is_houston_hq": false,
      "hq_city": "Chicago, IL",
      "press_releases": [{"dateline": "Houston, TX", "language": "general announcement"}],
      "texas_sos_foreign": true,
      "founder_alumni": ["Rice University"],
      "single_houston_job_posting": true,
      "event_speaking_slots": [{"event": "CERAWeek 2025"}]
    },
    "expected": {
      "tier": "B-low",
      "total_points_min": 4,
      "high_operational_count": 0,
      "only_low_signals": true,
      "confidence": "LOW",
      "review_queue": true
    }
  },
  {
    "case_id": "TC-08_houston_hq_weak_corroboration",
    "description": "Company claims Houston HQ via Texas SOS but only one corroborating signal — A-low review.",
    "evidence": {
      "is_houston_hq": true,
      "texas_sos_county": "Harris",
      "founder_linkedin_houston": true
    },
    "expected": {
      "tier": "A-low",
      "total_points_min": 5,
      "high_operational_count_min": 1,
      "review_queue": true
    }
  },
  {
    "case_id": "TC-09_doe_oced_sub_awardee_high",
    "description": "DOE OCED HyVelocity Hub sub-awardee with named Texas project location — strongest single non-HQ signal for Tier B.",
    "evidence": {
      "is_houston_hq": false,
      "hq_city": "Pittsburgh, PA",
      "doe_oced_hub": {
        "hub": "HyVelocity Gulf Coast",
        "role": "off-taker",
        "project_location": "La Porte, TX"
      },
      "founder_linkedin_houston": false
    },
    "expected": {
      "tier_min": "B",
      "total_points_min": 3,
      "high_operational_count_min": 1,
      "signals_required": ["doe_oced_hub_sub_awardee"],
      "confidence": "MEDIUM"
    }
  },
  {
    "case_id": "TC-10_ercot_ia_signed_houston_zone_high",
    "description": "ERCOT generator interconnection IA-signed at Houston load zone with developer matching the company — strong Tier B-high.",
    "evidence": {
      "is_houston_hq": false,
      "hq_city": "Austin, TX",
      "ercot_interconnection": {
        "milestone": "IA-signed",
        "load_zone": "Houston",
        "developer_matches_company": true
      },
      "investors": ["Energy Capital Ventures"]
    },
    "expected": {
      "tier": "B-high",
      "total_points_min": 5,
      "high_operational_count_min": 1,
      "signals_required": ["ercot_ia_signed_houston_zone", "houston_co_investor"],
      "confidence": "HIGH"
    }
  }
]
```

---

## Implementation notes for `signals/houston_presence.py`

- The scorer is a pure function: `score_houston_presence(company_record: CompanyRecord) -> HoustonPresenceResult`. No side effects, no I/O, no external calls.
- All signal-detection functions live in the same module as private helpers, named `_detect_<signal_id>`. Each returns a `SignalContribution | None`.
- False-positive watch flags run *before* signal aggregation. If a watch flag triggers, the corresponding signal is downgraded or excluded with the reason captured in `false_positive_flag`.
- The composite scoring logic (point sum + tier rules) is the *last* step. Earlier steps are signal detection and false-positive filtering.
- The scorer must be deterministic. No LLM calls in this module. (The classifier in `signals/venture_scale.py` does use the LLM — that's a separate module.)
- The output `notes` field is generated by a small string template — not by an LLM. Format: `"Tier {tier} ({total_points} pts; {high_operational_count} HIGH operational). Signals: {comma-separated signal_ids}. {flag_summary if any}."`

---

## What this spec does NOT cover

- The venture-scale classification rubric (technology defensibility, IP, capital intensity, etc.) — see `docs/venture_scale_rubric.md`.
- Founder pedigree scoring — see `docs/founder_pedigree_taxonomy.md`.
- Source-specific harvesting — see individual `harvest/*.py` modules and `docs/source_inventory.md`.
- Real-company calibration — performed during Phase 3 manual review of pipeline output.

---

## Calibration plan (Phase 3, after first full pipeline run)

After the system produces its first full classification pass, manually review:

1. The top 10 highest-scoring Tier A companies (do they look right at sniff-test level?).
2. The bottom 10 highest-scoring Tier B-high companies (are these defensibly platform-actionable?).
3. The full B-low review queue (override or confirm each, with reasoning).
4. A random sample of 10 Tier C companies (any obvious recruiting targets?).

For every override, write to `data/validated_examples.jsonl` with the company data and reasoning. Future pipeline runs inject these as few-shot examples to the classifier and Houston presence reasoner via the `auto_inject_examples` mechanism.

This is the validated examples bank flywheel in action — judgment compounds without retraining.
