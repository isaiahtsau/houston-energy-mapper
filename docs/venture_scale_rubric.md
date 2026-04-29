# Venture-Scale Classification Rubric

**Module (deterministic exclusions):** `signals/venture_scale.py` (`apply_hard_exclude_rules` function)
**Module (LLM classifier):** `signals/venture_scale.py` (`classify_venture_scale` function)
**Prompt:** `prompts/classifier_v1.md`
**Tests:** `tests/signals/test_venture_scale.py`
**Fixtures:** `tests/fixtures/venture_scale_cases.json`
**Version:** v2
**Last updated:** 2026-04-28

### Changelog (v1 → v2)

- CompanyRecord migrated to `models.py` with venture-scale fields added; all modules import from there (resolution 1).
- HX-01 scoped to `round_type` tag only; language scanning is harvester responsibility (resolutions 2 & 3).
- HX-01 third condition (single-asset description) dropped; borderline cases route to classifier (resolution 3).
- `CORPORATE_VC_WHITELIST` defined in `models.py`; HX-02 uses substring matching against it (resolution 4).
- HX-03 `technology_vendor_identity` safe harbor made explicit in rule text (resolution 5).
- Absence convention documented: `None` and `[]` treated identically by all rules (resolution 6).
- All shared constants (`HOUSTON_MAJORS`, `HOUSTON_ZIP_WHITELIST`, etc.) consolidated into `models.py` (resolution 7).
- VS-TC-01 `reason_should_contain` corrected to `"PF-Debt"` (capital D) (resolution 8).
- HX-02 services-language check fires on `description` keywords OR `primary_business` field (resolution 9).
- HX-01 language-path test removed; no additional test case needed (resolution 10).

---

## Spec review history

This spec was pressure-tested before implementation. The v1 draft was reviewed by the implementing agent, which surfaced 10 distinct ambiguities or judgment calls:

1. CompanyRecord schema undefined — required explicit extension of the houston_presence dataclass, migrated to shared models.py.
2. HX-01 trigger ambiguity — language scanning vs. structured tag; resolved: tag-only, harvester normalizes.
3. HX-01 third condition (single-asset description) was not deterministically checkable — dropped; borderline cases route to classifier.
4. CVC whitelist undefined — resolved: CORPORATE_VC_WHITELIST defined in models.py using the rubric's own list.
5. HX-03 technology_vendor_identity safe harbor was implied by test case but not in rule text — made explicit.
6. Absence semantics (None vs. []) needed to be documented — confirmed: treated identically by all rules.
7. HOUSTON_MAJORS and other constants duplicated across modules — resolved: consolidated into models.py.
8. VS-TC-01 reason_should_contain capitalization mismatch ("PF-debt" vs. "PF-Debt") — corrected.
9. HX-02 primary_business vs. description check — confirmed: dual-path (either fires the first AND condition).
10. HX-01 language-path test case — resolved: language-scanning dropped, no test needed.

Eight of these were genuine corrections. Two (dual-path HX-02 check semantics and absence convention) were clarifications of intent. The implementation plan was unblocked after these resolutions.

---

## Purpose

This spec defines how the system decides whether a candidate company is **venture-scale** as defined by the assignment: technology-led, scalable, targeting large markets, with clear competitive advantage from IP, data, or unique insight. The rubric explicitly excludes local services firms, consulting shops, lifestyle businesses, project-finance vehicles, and IP-licensing-only entities.

The classification runs in two passes per company:

1. **Hard-exclude rules.** Deterministic Python checks. Run before any LLM call. If a candidate trips a hard-exclude rule, it is excluded with a structured reason and never sent to the classifier. Saves API cost, prevents the LLM from being argued into wrong answers, and produces clean exclusion traces.

2. **LLM classifier.** For candidates that survive the hard-exclude pass, the classifier (Claude Sonnet 4.6) scores them against the venture-scale rubric and returns a structured JSON object: score (0–10 scale), tier (VENTURE_SCALE / BORDERLINE / NOT_VENTURE_SCALE), confidence flag, reasoning trace, signals matched, and false-positive patterns detected.

Real-company calibration happens during Phase 3 (manual review of pipeline output). This spec does not pre-score named companies — calibration data is produced by the validated examples bank flywheel.

---

## Framework: what makes a company venture-scale in energy

Energy ventures have unique characteristics that distinguish them from software-sector signals. The rubric encodes these explicitly so the classifier doesn't apply software heuristics to hardware companies (which would systematically misclassify them).

### Positive signals (count toward venture-scale score)

**Technology defensibility.**
- IP backed by published patents in relevant CPC codes (Y02E, Y02P, Y02C, Y02B/T/W, Y04S).
- University-licensed foundational IP with exclusive license terms.
- Demonstrated technical performance advantages versus incumbents (peer-reviewed publication, third-party validation, named customer testing).
- Proprietary data moat (e.g., methane emissions dataset, subsurface modeling library, manufacturing process know-how).

**Capital structure.**
- Venture equity rounds with named institutional investors.
- Blended cap stack: equity + DOE/ARPA-E grants + venture debt with ≥30% non-dilutive component.
- Series staging consistent with hardware capital intensity:
  - Pre-seed/seed: $2–8M (e.g., Helix Earth $12M Seed, Syzygy $5.8M Series A).
  - Series A: $15–60M with deep-tech climate fund participation (Lowercarbon, BEV, DCVC, Khosla, EIP, Prelude, Congruent, Clean Energy Ventures, S2G, Galvanize).
  - Series B: $30–150M+.
- Recent valuation step-up indicating real progress between rounds.

**Customer pilot signals (reliability spectrum, highest to lowest).**
- Highest: signed offtake or take-or-pay with named major (8-K-disclosed or press-released).
- High: paid commercial pilot with named Tier-1 IOC (Chevron, ExxonMobil, Shell, BP, OXY, ConocoPhillips, Equinor, TotalEnergies) or Tier-2 industrial (Dow, LyondellBasell, Air Liquide, Linde, Cemex, Nucor — relevant for Houston chemicals corridor).
- High: multiple geographically-spread pilots (≥3 sites, ≥2 countries).
- Medium: unpaid joint study with major.
- Low: LOI/MOU only — flagged as soft signal.

**Federal non-dilutive program participation (hierarchy from strongest to weakest).**
- DOE LPO closed/finalized loan (very high — cleared all conditions precedent).
- ARPA-E SCALEUP awardee (very high).
- DOE OCED Industrial Demonstrations Program selectee (very high).
- DOE Manufacturing & Energy Supply Chain Office award.
- ARPA-E performer (high — "halo effect," 1,590+ projects funded historically, 157 spawned companies).
- DOE OCED Hydrogen / DAC Hub direct selectee.
- DOE LPO conditional commitment (medium-high; discounted in 2025–26 because many Biden-era conditionals were de-obligated under the current administration — verify status).
- SBIR Phase III or Phase II-E with matched investor (high).
- SBIR Phase II ($1.1–1.25M).
- DOE EERE/AMMTO/FECM grant only (low-medium).
- SBIR Phase I (low — table stakes).

**Partnerships with majors (decoded reliability).**
- Highest: equity investment from corporate VC arm (CTV, SLB Ventures, bp Ventures, Shell Ventures, Equinor Ventures, OGCI Climate Investments, Aramco Ventures, ExxonMobil LCS, BHEV).
- Very high: multiple major-arm investors stacked in one round.
- High: JDA (joint development agreement) — co-creation of IP.
- Medium: strategic supply / EPC contract.
- Medium: Halliburton Labs cohort (industry validation, no equity).
- Low / high-noise: press-release-only "exploring," "evaluating," "MOU," "LOI" partnerships.

**Technical team composition.**
- ≥40% of technical leadership (CTO + VP Eng + Chief Scientist) holding relevant STEM PhD → high.
- Founder/CTO former National Lab staff scientist (NREL, LBNL, ORNL, Sandia, Argonne, LANL, INL, PNNL, NETL — NETL especially relevant for Houston) tenure ≥3 yrs → high.
- Former Distinguished Member / Principal / Chief Scientist at major (SLB, Halliburton, Baker Hughes, Chevron, Exxon) → high.
- Lab affiliation with Rice (Tour, Halas, Yakobson, Wong, Ajayan, Nordlander) or UH TcSUH (Chu, Ren, Selvamanickam) → very high for Houston classifier.

**Scale of pilot deployment (TRL-inspired progression).**
- Bench (TRL 3–4, lab data only): low alone, OK at pre-seed.
- Pilot (TRL 5–6, kW–100kW; 1–100 kg/day; 1–10 t/yr CO₂): medium, Series A standard.
- Demo (TRL 7, 0.1–10 MW; 100kg–10t/day; 100–10,000 t/yr CO₂): high, Series B threshold.
- FOAK Commercial (TRL 8, ≥10 MW; ≥10 t/day; ≥10,000 t/yr CO₂; first revenue from saleable product): very high — crosses valley of death.
- N-of-a-kind (TRL 9, multiple deployed units, replicable economics): growth/infra capital appropriate.
- Multiplier: geographic spread ≥2 countries / ≥3 US states with installed units.

### Negative signals / false-positive patterns (count against venture-scale score)

The classifier explicitly looks for and penalizes the following patterns. These are common in Houston specifically because the city has a large incumbent services and consulting industry that has begun layering thin software wrappers in pursuit of venture capital.

| Pattern | Description | Score penalty |
|---------|-------------|--------------|
| `oilfield_services_thin_ai_wrapper` | Founders 15+ yrs at oilfield services, no CS/ML PhDs on team, product is service+software. Pattern: "AI-powered chemical treatment for downhole" with no model patents. | -0.4 |
| `consulting_positioned_as_software` | Revenue model is hours/project; team is "advisors"/"associates"; few engineers. Common Houston pivot pattern. | -0.5 |
| `family_run_industrial_modest_digitization` | Same surname across leadership; private since 1980s+; one new "tech division"; no VC funding. | -0.6 |
| `rollup_disguised_as_platform` | Acquisition press releases, M&A growth, gross margins <30%. | -0.5 |
| `single_major_dependent_vendor` | ≥80% revenue from one IOC; founder is ex-employee of that IOC. Vendor-not-venture pattern. | -0.5 |
| `greenwashed_services` | "Energy efficiency tech" = insulation install crew; team is field techs not engineers. | -0.6 |
| `lifestyle_hardware_shop` | Bespoke fabrication, revenue plateau under $5M, no scaling intent. | -0.4 |
| `ai_powered_chemicals` | Specialty chemical company with new dosing app — chemical is the actual product, app is marketing. | -0.5 |

**Hard-exclude patterns (separate, deterministic — see next section).** A few patterns are *categorical* mismatches rather than soft penalties. Those run as deterministic exclusions before the classifier sees the company.

---

## Hard-exclude rules (deterministic, run before LLM classifier)

These rules execute as Python checks against the harvest-stage company record. If any rule matches, the company is marked excluded with a structured reason, and is not passed to the LLM classifier. Output structure:

```python
@dataclass
class HardExcludeResult:
    excluded: bool
    rule_id: str | None     # which rule matched
    reason: str | None      # human-readable explanation
```

### Rule HX-01: PF-debt-only round

**Triggers if:** `most_recent_round.round_type` is in `{"PF-Debt", "Project Finance Debt", "Project Bond"}`.

The harvester layer is responsible for normalizing source language into the structured `round_type` field before the record reaches the hard-exclude pass. Language scanning is not performed by this deterministic rule — borderline single-asset cases route to the LLM classifier with PF-debt patterns flagged as high-weight negative signals.

**Reason:** Project finance debt is non-recourse capital secured against a single physical asset. This is infrastructure financing, not venture-scale equity formation. Different return profile, different investor universe, different risk structure.

### Rule HX-02: Pure services revenue with no IP

**Triggers if all of:**
- Primary services language detected via EITHER:
  - `description` contains any of: "consulting," "advisory," "managed services," "engineering services," "professional services"; OR
  - `primary_business` field is in: "consulting," "services," "advisory"
- AND no issued or filed patents in any CPC code (Y02 family or otherwise)
- AND no federal non-dilutive grants (no SBIR, ARPA-E, DOE)
- AND no corporate VC investment from a strategic arm (`CORPORATE_VC_WHITELIST` in `models.py`, substring-matched against `company.investors`)

`CORPORATE_VC_WHITELIST` includes: CTV / Chevron Technology Ventures, SLB Ventures, bp Ventures, Shell Ventures, Equinor Ventures, OGCI Climate Investments, Aramco Ventures, ExxonMobil Low Carbon Solutions / ExxonMobil LCS, Baker Hughes Energy Ventures / BHEV, Halliburton Labs.

**Reason:** Services revenue scales linearly with headcount, not with technology. Without IP, federal validation, or strategic capital, there is no defensible non-services thesis to evaluate.

### Rule HX-03: Single-asset project SPV

**Safe harbor (checked first):** If `technology_vendor_identity` is set (non-None, non-empty), the rule does not fire. A company with a distinct technology product identity beyond the project SPV shell routes to the classifier with the SPV pattern flagged, not auto-excluded.

**Triggers if all of (evaluated only when safe harbor does not apply):**
- Entity name contains `"LLC"`, `"LP"`, or `"Holdings"` AND
- Description contains explicit SPV language ("special purpose vehicle," "single purpose entity," or standalone word "spv") AND
- Either: (a) DOE OCED hub `role` is `"project participant"`; OR (b) `form_d.use_of_proceeds` names a specific asset (non-empty string)

**Reason:** SPVs are operational shells for one specific deployment. The technology vendor that built the underlying tech is the venture-scale entity; the SPV is not. This rule is conservative — only fires on explicit SPV language; ambiguous cases route to the classifier.

### Rule HX-04: Patent-troll structure

**Triggers if all of:**
- Employee count under 5 (LinkedIn company page or self-reported) AND
- Stated business model is IP licensing or patent monetization AND
- No product offering described AND
- No customer relationships described.

**Reason:** A patent-licensing entity with three lawyers in a Sugar Land office isn't a venture-scale operating company. The Ion's mandate is to accelerate operating companies; trolls dilute the dataset with non-actionable entries.

### Rule HX-05: Wholly-owned major subsidiary

**Triggers if:**
- Parent organization is named in `HOUSTON_MAJORS` whitelist (ExxonMobil, ConocoPhillips, Phillips 66, OXY, Halliburton, Baker Hughes, SLB, Chevron, NRG, etc.) AND
- Entity is described as a subsidiary, division, or wholly-owned business unit of that major.

**Reason:** Subsidiaries of integrated majors (e.g., 1PointFive as OXY subsidiary, ExxonMobil Low Carbon Solutions as ExxonMobil division) are not standalone venture-scale entities. Their technology partnerships and ecosystem role surface adjacent venture-scale companies (sub-recipients, technology providers, JV co-investments) — those should be captured separately.

---

## LLM classifier scoring rubric

For candidates that survive hard-exclude, the classifier produces a 0–10 venture-scale score. The score is generated by an LLM (Claude Sonnet 4.6) given the company record, this rubric, and the validated examples bank.

### Score interpretation

| Score range | Tier | Interpretation |
|-------------|------|----------------|
| 8–10 | `VENTURE_SCALE` | Strong evidence across multiple positive signals; few or no false-positive patterns. |
| 5–7 | `BORDERLINE` | Mixed evidence; routes to manual review queue. |
| 0–4 | `NOT_VENTURE_SCALE` | Predominantly false-positive patterns or absence of positive signals. |

### Confidence flag

Independent of score:
- `HIGH`: ≥3 positive signals from distinct categories AND no false-positive patterns triggered.
- `MEDIUM`: 2 positive signals OR 1 strong positive signal with minor false-positive flags.
- `LOW`: 1 positive signal OR conflicting signals — surfaces to manual review queue regardless of tier.

### Required output schema

The classifier returns this exact Pydantic model:

```python
class VentureScaleClassification(BaseModel):
    company_id: str
    score: float                  # 0.0–10.0
    tier: Literal["VENTURE_SCALE", "BORDERLINE", "NOT_VENTURE_SCALE"]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    positive_signals: list[str]   # signal IDs from the rubric (e.g., "y02_patents_filed", "arpa_e_performer")
    false_positive_patterns: list[str]  # pattern IDs (e.g., "consulting_positioned_as_software")
    reasoning: str                # 2–4 sentence explanation, references specific evidence
    review_queue: bool            # True if BORDERLINE or LOW confidence
```

The classifier prompt enforces JSON-mode output via Anthropic's structured response feature. Parsing failures route to manual review (per `llm/client.py` retry policy).

### Reasoning trace requirements

The `reasoning` field must:

- Reference specific evidence from the company record (not generic platitudes).
- Name the strongest positive signal that drove the score.
- Name any false-positive pattern that lowered the score.
- Be 2–4 sentences (long enough to audit, short enough to scan in the spreadsheet).

**Acceptable example:** "Company holds 7 issued patents in Y02C with ARPA-E SCALEUP award status (2024). Series B led by Energy Capital Ventures with $45M; lab affiliation with Rice ChemE (Halas group). No false-positive patterns triggered."

**Unacceptable example:** "This is a strong venture-scale candidate with good signals across the board." (Generic, no specific evidence.)

---

## Classifier prompt structure

The classifier prompt (`prompts/classifier_v1.md`) follows this structure. The full prompt text is drafted separately during Step 6 and versioned independently.

```
[SYSTEM]
You are a venture-scale classification specialist for the Ion's Houston Energy Mapper.
Your task: read the company record below and apply the venture-scale rubric.
Return a JSON object matching the VentureScaleClassification schema.

[RUBRIC]
{insert full positive signals + false-positive patterns from this doc}

[VALIDATED EXAMPLES]
{auto-injected from data/validated_examples.jsonl via prompt_loader.py}
{empty on first run; grows over time as the flywheel populates}

[COMPANY RECORD]
{the candidate's normalized record from the harvest stage}

[OUTPUT INSTRUCTIONS]
- Return ONLY valid JSON matching VentureScaleClassification. No prose.
- score: 0.0–10.0 (one decimal place)
- positive_signals: signal IDs from the rubric, not free text
- false_positive_patterns: pattern IDs from the rubric
- reasoning: 2–4 sentences referencing specific evidence
- If you cannot confidently classify, set tier=BORDERLINE and confidence=LOW
```

---

## Synthetic test cases

These test the **deterministic hard-exclude rules**. The LLM classifier itself is tested separately (manually validated against 10 real harvest records during Phase 2 Step 6).

```json
[
  {
    "case_id": "VS-TC-01_pf_debt_only_excluded",
    "description": "Hydrogen developer announces $300M raise — round type is PF-Debt with no equity component.",
    "evidence": {
      "name": "COMPANY_HX_A",
      "description": "Building a 200 MW green hydrogen production facility on the Gulf Coast.",
      "most_recent_round": {
        "round_type": "PF-Debt",
        "amount_usd": 300000000,
        "language": "non-recourse project debt facility for the Gulf Coast hydrogen plant"
      },
      "patents": [],
      "investors": []
    },
    "expected": {
      "excluded": true,
      "rule_id": "HX-01",
      "reason_should_contain": "PF-Debt"
    }
  },
  {
    "case_id": "VS-TC-02_pure_services_excluded",
    "description": "Houston engineering services firm with no IP, no grants, no CVC. Pure services revenue.",
    "evidence": {
      "name": "COMPANY_HX_B",
      "description": "Reservoir engineering consulting for upstream operators.",
      "primary_business": "consulting",
      "patents": [],
      "federal_grants": [],
      "investors": []
    },
    "expected": {
      "excluded": true,
      "rule_id": "HX-02",
      "reason_should_contain": "services"
    }
  },
  {
    "case_id": "VS-TC-03_consulting_with_arpa_e_NOT_excluded",
    "description": "Company appears services-y but has ARPA-E grant — does not trip HX-02 because grant is present.",
    "evidence": {
      "name": "COMPANY_HX_C",
      "description": "Energy systems engineering with novel modeling approach.",
      "primary_business": "consulting",
      "patents": [],
      "federal_grants": [{"program": "ARPA-E", "phase": "Phase II"}],
      "investors": []
    },
    "expected": {
      "excluded": false,
      "rule_id": null
    }
  },
  {
    "case_id": "VS-TC-04_project_spv_excluded",
    "description": "Single-asset SPV named in a DOE OCED hub award.",
    "evidence": {
      "name": "Bayou Hydrogen LLC",
      "entity_type": "LLC",
      "description": "Special purpose vehicle for the Bayou Hydrogen Production Facility, a 50 MW PEM electrolyzer plant.",
      "doe_oced_hub": {"hub": "HyVelocity Gulf Coast", "role": "project participant"},
      "form_d": {"use_of_proceeds": "Bayou Hydrogen Production Facility"},
      "technology_vendor_identity": null
    },
    "expected": {
      "excluded": true,
      "rule_id": "HX-03",
      "reason_should_contain": "SPV"
    }
  },
  {
    "case_id": "VS-TC-05_patent_troll_excluded",
    "description": "3-employee IP-licensing entity, no product, no customers.",
    "evidence": {
      "name": "COMPANY_HX_E",
      "description": "Patent licensing and IP monetization for clean energy technologies.",
      "employee_count": 3,
      "business_model": "IP licensing",
      "products": [],
      "customers": []
    },
    "expected": {
      "excluded": true,
      "rule_id": "HX-04",
      "reason_should_contain": "patent"
    }
  },
  {
    "case_id": "VS-TC-06_major_subsidiary_excluded",
    "description": "1PointFive as wholly-owned OXY subsidiary.",
    "evidence": {
      "name": "1PointFive",
      "description": "Wholly-owned subsidiary of Occidental Petroleum (OXY) focused on direct air capture.",
      "parent_organization": "OXY",
      "is_subsidiary": true
    },
    "expected": {
      "excluded": true,
      "rule_id": "HX-05",
      "reason_should_contain": "subsidiary"
    }
  },
  {
    "case_id": "VS-TC-07_legitimate_passes_to_classifier",
    "description": "Strong venture-scale candidate — passes all hard-exclude rules and routes to LLM classifier.",
    "evidence": {
      "name": "COMPANY_HX_G",
      "description": "Plasmonic photocatalysis for ammonia synthesis at low temperature, licensed from Rice University Halas Lab.",
      "employee_count": 47,
      "patents": [
        {"cpc": "Y02E", "status": "issued", "count": 4},
        {"cpc": "Y02P", "status": "filed", "count": 2}
      ],
      "federal_grants": [{"program": "ARPA-E", "phase": "SCALEUP"}],
      "investors": ["Mercury Fund", "Goose Capital", "Energy Capital Ventures"],
      "most_recent_round": {"round_type": "Series B", "amount_usd": 45000000}
    },
    "expected": {
      "excluded": false,
      "rule_id": null,
      "should_route_to_classifier": true
    }
  },
  {
    "case_id": "VS-TC-08_borderline_spv_routes_to_classifier",
    "description": "Entity name has LLC and DOE hub mention, but also has separate technology vendor identity and team — does NOT auto-exclude; routes to classifier with SPV pattern flagged.",
    "evidence": {
      "name": "COMPANY_HX_H Holdings LLC",
      "entity_type": "LLC",
      "description": "Develops modular electrolyzer technology; deployed first 5 MW unit in HyVelocity hub.",
      "doe_oced_hub": {"hub": "HyVelocity Gulf Coast", "role": "technology provider"},
      "technology_vendor_identity": "COMPANY_HX_H",
      "employee_count": 28,
      "patents": [{"cpc": "Y02E", "status": "issued", "count": 3}]
    },
    "expected": {
      "excluded": false,
      "rule_id": null,
      "should_route_to_classifier": true
    }
  }
]
```

---

## Implementation notes

### `signals/venture_scale.py` structure

Two public functions:

```python
def apply_hard_exclude_rules(company: CompanyRecord) -> HardExcludeResult:
    """Deterministic hard-exclude check. Returns excluded=True with rule_id if any rule matches."""
    ...

def classify_venture_scale(
    company: CompanyRecord,
    examples_bank: list[dict] | None = None,
) -> VentureScaleClassification:
    """LLM-judged classification. Calls the classifier prompt via llm/client.py.
    Examples bank is auto-injected via call_llm's auto_inject_examples flag."""
    ...
```

### Pipeline orchestration

In `pipeline/orchestrator.py`, the venture-scale stage runs as:

```python
for company in staged_companies:
    hx_result = apply_hard_exclude_rules(company)
    if hx_result.excluded:
        write_classification(company, excluded=True, rule_id=hx_result.rule_id, reason=hx_result.reason)
        continue
    classification = classify_venture_scale(company)
    write_classification(company, classification=classification)
```

This keeps cost discipline tight — only candidates that pass deterministic filters incur API cost.

### Cost estimation

At Sonnet 4.6 pricing (~$3 input / $15 output per million tokens) and rough estimates:
- Input per call: ~2,000 tokens (rubric + record + examples) = ~$0.006
- Output per call: ~400 tokens (JSON response) = ~$0.006
- Per-company cost: ~$0.012
- 200-company run: ~$2.40
- 1,000-company run: ~$12

Hard-exclude rules typically eliminate 30–50% of candidates upfront, reducing real-world cost further.

### Determinism

The classifier prompt sets `temperature=0.0`. Same input + same rubric + same examples bank produces the same classification. This is essential for reproducibility — reruns produce stable results, and changes between runs reflect rubric or examples-bank changes (intentional), not LLM nondeterminism.

---

## Calibration plan (Phase 3)

After the first full pipeline run:

1. Spot-check the top 10 highest-scored `VENTURE_SCALE` companies. Confirm the reasoning trace cites real evidence in the company record.
2. Spot-check the bottom 10 lowest-scored `NOT_VENTURE_SCALE` companies. Confirm the false-positive patterns the classifier flagged are present in the source data.
3. Manually review the entire `BORDERLINE` queue. Override or confirm each, with reasoning written to `data/validated_examples.jsonl`.
4. For any miscalibration patterns found (e.g., "the classifier consistently over-rates companies with ARPA-E Phase I grants"), iterate the prompt — save as `prompts/classifier_v2.md` with a header noting what changed and why.

The validated examples bank is the long-term calibration mechanism. Future runs inject these examples as few-shot context, and the classifier internalizes the corrections without retraining.

---

## What this spec does NOT cover

- Houston presence tier assignment — see `docs/houston_presence_signals.md`.
- Founder pedigree scoring — see `docs/founder_pedigree_taxonomy.md`.
- Per-source harvesting — see `docs/source_inventory.md` and individual `harvest/*.py` modules.
- The actual classifier prompt text — drafted in Step 6 as `prompts/classifier_v1.md`, versioned independently.
- Real-company calibration — performed during Phase 3.
