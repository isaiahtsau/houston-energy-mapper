# Founder Pedigree Taxonomy

**Module:** `enrich/founder_pedigree.py`
**Prompt:** `prompts/founder_pedigree_v1.md`
**Tests:** `tests/enrich/test_founder_pedigree.py`
**Fixtures:** `tests/fixtures/founder_pedigree_cases.json`
**Version:** v3
**Last updated:** 2026-04-29

---

## Purpose

This spec defines how the system scores founder pedigree for companies that pass the venture-scale filter. Output is a per-founder structured object containing tier (HIGH / MEDIUM / LOW), supporting evidence, and Houston-specific multipliers when relevant.

The scorer is a hybrid: deterministic taxonomy lookups for well-defined categories (e.g., "PhD from a known Houston-energy lab"), plus an LLM pass for free-text bio interpretation (e.g., "30 years at Shell upstream"). Output is always structured per the schema below.

This is an **enrichment** module, not a classification module. It runs after a company has been filtered as venture-scale and tier-A/B-classified for Houston presence. Its purpose is to populate the `founder_pedigree` field in the deliverable spreadsheet, not to make pass/fail decisions.

Real-company calibration happens during Phase 3 (manual review of pipeline output). This spec defines the categories, weights, and multipliers — actual founder names and lab affiliations get validated against the dataset, not pre-scored as ground truth.

---

## Why pedigree matters for the Ion's mandate

Founder pedigree is the strongest single predictor of venture-scale success in energy specifically because:

- Energy companies are capital-intensive and pilot-driven. Operator-turned-founder patterns (ex-major executives starting companies) compound advantages from network access, customer relationships, and pilot pathways.
- Hardware/process IP defensibility often traces directly to PhD lab affiliations. Rice ChemE/MSE/Physics, UH TcSUH, UT Austin Petroleum/ChemE, A&M Materials are recurring sources for Houston energy ventures.
- Federal program participation (ARPA-E performer, Activate Fellow, national lab tenure) is a credibility filter that's already been applied by sophisticated reviewers — which is itself a positive signal.

The Ion's platform value depends on knowing not just *which* companies are venture-scale but *who* leads them — for programming, capital introductions, and community-building.

---

## Pedigree categories

Six categories drawn from Research output Section 6, with explicit point weights and triggering conditions.

### B1 — Major company experience

Former roles at integrated majors (Chevron, ExxonMobil, Shell, BP, OXY, ConocoPhillips, Phillips 66, Marathon, TotalEnergies) or service companies (SLB, Halliburton, Baker Hughes, Weatherford, NOV).

| Pattern | Tier | Points |
|---------|------|--------|
| C-suite role (CEO/CFO/CTO/COO) at major | HIGH | 3 |
| BU President/VP/GM at major (owned P&L) | HIGH | 3 |
| Distinguished Member of Technical Staff / Schlumberger Fellow / ExxonMobil Senior Research Associate / Principal Engineer / Chief Scientist | HIGH | 3 |
| Director-level R&D at major | MEDIUM-HIGH | 2.5 |
| Mid-level engineer (5–10 yrs) at major | MEDIUM | 2 |
| Field/asset operator with technical role (≥7 yrs) | MEDIUM | 2 |
| Service-co Principal Engineer/Fellow (SLB/HAL/BHGE) | HIGH | 3 |
| Service-co BD/sales | MEDIUM | 2 |
| BD/commercial/sales at major | MEDIUM | 2 |
| Strategy/corporate development at major | MEDIUM | 2 |
| Trader/commercial analyst at major | LOW | 1 |
| Non-technical foreman | LOW | 1 |

**Encoding rule:** tenure ≥7 years at major in technical role + ≥3 issued patents during tenure → HIGH (regardless of title). Tenure <2 years + non-technical → LOW (regardless of title).

**Deterministic detection boundary:** The deterministic layer fires B1 when a bio contains BOTH a company name from `MAJORS_AND_SERVICE_COS` (resolved via `COMPANY_ALIASES`) AND a title keyword from `B1_TITLE_KEYWORDS`. The LLM layer catches paraphrased patterns ("spent 30 years at Shell upstream") that lack explicit title keywords.

### B2 — PhD program affiliation

Specific PhD programs with high signal density for Houston energy ventures.

**Very high (3.0 pts base; 3.5 pts with co-authorship bump):**

| Program | Notable advisors / labs |
|---------|-------------------------|
| Rice ChemE / MSE / Physics | James Tour (nanomaterials, batteries, Flash Joule heating); Naomi Halas (plasmonics → Syzygy); Boris Yakobson (theory); Michael Wong (catalysis); Pulickel Ajayan (nanomaterials); Peter Nordlander (plasmonics → Syzygy) |
| UH TcSUH | Paul C.W. Chu (founder); Zhifeng Ren (director, batteries/thermoelectrics); Venkat Selvamanickam (2G HTS wires → MetOx); Liangzi Deng |
| UT Austin MSE | Arumugam Manthiram (batteries, Goodenough successor) |

**High (2.5 pts):**

| Program | Notable focus |
|---------|--------------|
| UT Austin Petroleum & Geosystems Engineering | Kamy Sepehrnoori, Matthew Balhoff, David DiCarlo, Larry Lake (subsurface, CO₂ storage, geothermal) |
| UT Austin ChemE | Brian Korgel, Graeme Henkelman, Buddie Mullins |
| Texas A&M Petroleum / MSE | Arroyave (computational); Karaman (alloys) |
| UH ChemE / Cullen Engineering | Various |
| Stanford ERE | Kovscek, Gerritsen |
| MIT MITEI/ChemE/DMSE | Shao-Horn, Yildiz, Chiang, Sadoway legacy |
| Caltech Resnick | Atwater, Lewis |
| Berkeley ChemE/MSE | Bell, McCloskey, Ceder, Persson |
| Princeton Andlinger | Carter |
| Northwestern | Broadbelt, Kanatzidis |
| Georgia Tech | Liu (batteries) |
| CMU, U Michigan ChemE/Energy | Various |

**Encoding rule:** PhD advisor on named-faculty list = HIGH (2.5 pts). Named advisor in Very High list = VERY HIGH base (3.0 pts). Co-authorship bump: when bio contains both a named lab AND publication-output language (`co-author`, `published`, `papers in`, `first author`, `peer-reviewed`), add +0.5 pts. Total capped at 3.5 pts per-category cap. Degree only without advisor mapping = MEDIUM (2.0 pts).

### B3 — Prior startup exits

Serial founder status with verifiable energy or industrial exits.

| Pattern | Tier | Points |
|---------|------|--------|
| ≥1 prior energy/industrial exit (M&A or IPO) | VERY HIGH | 3.5 |
| Sold prior company to a major (IOC, Halliburton, SLB, BHGE, Emerson, Honeywell) | VERY HIGH | 3.5 |
| Failed prior startup with solid team/IP build (no fraud) | MEDIUM-POSITIVE | 1.5 |
| First-time founder, deeply technical PhD | MEDIUM | 2 |
| First-time founder, non-technical, no major employer | LOW | 1 |

**Houston-specific:** founders of acquired oilfield/data-SaaS firms (Quorum, NCS Multistage, Voyager, Sensorfield, Sensytec) relaunching into transition tech → HIGH.

**Detection sources:** Crunchbase `founded_companies` field; LinkedIn experience timeline; PitchBook executive history (used only as PARSING reference, not as data source per assignment constraint); news archive scraping for "founded" / "acquired by" patterns.

**Deterministic detection boundary:** The deterministic layer fires B3 when bio contains the pattern "acquired by [COMPANY]" where COMPANY resolves to a major or service co via `COMPANY_ALIASES`. The LLM layer catches paraphrased patterns ("my prior company was sold to a major OFS player").

### B4 — Fellowship and grant pedigree

Recognition by sophisticated reviewers is itself a venture-scale signal.

**Very high (3.5 pts):**

| Program | Notes |
|---------|-------|
| Activate Fellow (formerly Cyclotron Road) including Activate Houston cohort (launched 2024) | Fellows have raised >$2B follow-on cumulatively |
| Breakthrough Energy Fellows | $500K–$3M non-dilutive; ~168 fellows across 5 cohorts |

**High (3 pts):**

| Program | Notes |
|---------|-------|
| ARPA-E Fellow | 2-year DC role for early-career PhDs (distinct from being an ARPA-E performer) |
| Breakthrough Energy Explorer Grant | |
| DOE Computational Science Graduate Fellowship (CSGF) | |
| Rice Alliance Clean Energy Accelerator (Class 4, 5+) | |
| Elemental Excelerator portfolio | |

**Medium-high (2.5 pts):**

| Program | Notes |
|---------|-------|
| DOE Office of Science Graduate Student Research (SCGSR) | |
| Y Combinator (energy/climate batch) | Strong for software, weaker for hardware capex |
| Halliburton Labs cohort | Houston-specific industrial validation |

**Medium (2 pts):**

| Program | Notes |
|---------|-------|
| NSF GRFP in energy field | |
| Greentown Labs membership | |
| HETI Energy Venture Day pitch winner | |
| MassChallenge Texas | Lower bar than Activate/BE |

### B5 — National lab tenure

Position-type signals matter more than lab name alone.

| Position | Tier | Points |
|----------|------|--------|
| Postdoc only (1–2 yrs) | LOW-MEDIUM | 1.5 |
| Staff Scientist (≥3 yrs) | HIGH | 3 |
| Senior/Principal/Distinguished Scientist | VERY HIGH | 3.5 |
| Lab Director / Group Leader | VERY HIGH | 3.5 |
| Joint appointment (lab + university) | HIGH | 3 |

**Houston-relevant labs (multiplier +0.3):**

| Lab | Focus |
|-----|-------|
| NETL | Fossil, CCS, hydrogen — especially Houston-relevant |
| LBNL | Batteries, Cyclotron Road (now Activate), materials |
| NREL | Solar, wind, biofuels, hydrogen |
| ORNL | Materials, isotopes, neutron source, manufacturing |
| Sandia | Grid, batteries, hydrogen |
| Argonne | Batteries via JCESR, nuclear, materials |
| LANL | Materials, nuclear |
| INL | Nuclear, advanced reactors |
| PNNL | Grid, hydrogen, materials |

### B6 — Other high-signal pedigree

Patterns that don't fit the above categories but still indicate strong founder quality.

**High (3 pts):**

| Pattern | Notes |
|---------|-------|
| Tesla/SpaceX alumni in Senior Engineer+ technical role (batteries, hardware, manufacturing) | |
| Northvolt/QuantumScape/Form Energy/Sila alumni in storage | Very high for storage startups |
| Commonwealth Fusion / TAE / Helion alumni | High for fusion startups |
| Stripe Climate / Frontier buyer alumni | High for carbon removal — procurement-side knowledge |
| Generate Capital / Galvanize / EIP investor alumni | Starting companies, not just employed |
| NASA/DARPA program manager | |
| Ex-Calpine/NRG/Vistra for grid/power markets | Houston-specific |

**Mixed — flag explicitly:**

| Pattern | Notes |
|---------|-------|
| McKinsey/BCG/Bain Energy consulting | Strong industry knowledge, but solo-MBA-no-tech-cofounder is a false-positive risk; require pairing with technical PhD co-founder for HIGH |

**Low alone:**

| Pattern | Notes |
|---------|-------|
| Equity research analyst at major bank (energy coverage) | Pair-with-technical-cofounder rule applies |
| Energy investment banking (Morgan Stanley, Goldman, Citi, Tudor Pickering) | Pair-with-technical-cofounder rule applies |

**Deterministic detection boundary:** The deterministic layer fires B6 when bio contains a known B6 pattern phrase (e.g., "McKinsey", "BCG", "Bain", "Tesla", "SpaceX", "Stripe Climate", "DARPA program manager", "Calpine", "NRG", "Vistra"). When relevant, the false-positive guard fires based on `is_solo_founder` and `has_technical_cofounder` flags.

---

## Houston-specific multipliers

Applied to the founder's composite score after categorical points are summed.

| Multiplier | Trigger | Factor |
|------------|---------|--------|
| Houston university PhD | Founder PhD from Rice / UH / UT-Austin / Texas A&M relevant labs | ×1.3 |
| Houston accelerator/program | Activate Houston / RACEA / Halliburton Labs / Greentown Houston cohort participation | ×1.2 |
| Hub direct involvement | Gulf Coast H2 Hub or DAC Hub direct involvement (named role) | ×1.2 |
| Service-co senior pedigree | SLB/HAL/BHGE Principal+ (resolved via COMPANY_ALIASES) | ×1.15 |
| Lab + IP alignment | Founder's PhD lab (from a B2 CategoryMatch) is in company's `licensed_ip_labs` | ×1.4 |

**Composite score formula:**

```
base_score = sum(category_points) with diminishing returns (cap each category at 3.5 pts)
raw_multiplier_product = product of all applicable Houston multiplier factors
capped_multiplier_factor = min(raw_multiplier_product, 1.8)
final_score = base_score × capped_multiplier_factor
```

The `multipliers_applied` list preserves each individual multiplier entry regardless of cap. The `FounderPedigree` output records both `raw_multiplier_product` and `capped_multiplier_factor` for audit transparency.

**Tier assignment from final score:**

```
final_score >= 4.5 → HIGH (very strong founder)
3.0 <= final_score < 4.5 → MEDIUM-HIGH (strong founder)
2.0 <= final_score < 3.0 → MEDIUM (solid founder)
1.0 <= final_score < 2.0 → LOW-MEDIUM (acceptable founder)
final_score < 1.0 → LOW (limited pedigree signal)
```

> **Calibration note:** The v2 thresholds (≥8.0 for HIGH) were drawn from the abstract formula in the Research output. Synthetic fixture validation revealed that single strong category matches with Houston multipliers (e.g., a Rice ChemE PhD with lab+IP alignment producing 3.5 base × 1.8 capped = 6.3) should register as HIGH at the deterministic baseline. The v2 thresholds would have made HIGH unreachable without LLM augmentation, defeating the purpose of the deterministic baseline producing meaningful tier output. Phase 3 calibration against real founders may push thresholds again.

---

## Output schema

Per founder, the enricher returns a `FounderPedigree` Pydantic model:

```python
class FounderPedigree(BaseModel):
    name: str
    role: Literal["CEO", "CTO", "CSO", "Co-founder", "Founder", "Other"]
    final_score: float                       # base_score × capped_multiplier_factor
    tier: Literal["HIGH", "MEDIUM-HIGH", "MEDIUM", "LOW-MEDIUM", "LOW"]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    categories_matched: list[CategoryMatch]  # full audit trace
    multipliers_applied: list[MultiplierMatch]
    raw_multiplier_product: float            # uncapped product of all applied multiplier factors
    capped_multiplier_factor: float          # min(raw_multiplier_product, 1.8), used in final_score
    reasoning: str                           # 1–3 sentence summary
    review_queue: bool                       # True if tier = LOW or confidence = LOW

class CategoryMatch(BaseModel):
    category: Literal["B1", "B2", "B3", "B4", "B5", "B6"]
    pattern_id: str                          # e.g., "service_co_principal"
    raw_points: float                        # before multipliers, capped at 3.5 per category
    evidence: str                            # specific quote / detail

class MultiplierMatch(BaseModel):
    multiplier_id: str                       # e.g., "houston_university_phd"
    factor: float
    evidence: str
```

**Confidence flag rules:**

- `HIGH`: ≥3 distinct category matches with explicit evidence cited.
- `MEDIUM`: 2 category matches, OR 1 category match with multipliers applied.
- `LOW`: 1 category match without multipliers, OR sparse public profile (LinkedIn restricted, no published bio) — surfaces to review queue regardless of tier.

A note on data sparsity: per assignment constraint (no paid databases), founder pedigree data comes from public team pages, founder personal websites, and per-company LinkedIn URL fetches at low volume. Companies with thin web presence will have incomplete pedigree fields, flagged with LOW confidence. This is acknowledged in the deliverable's data quality section.

---

## Hybrid implementation: deterministic + LLM

This module mixes deterministic pattern matching (for well-enumerated categories like national lab names, university PhD programs, fellowship programs) with LLM interpretation (for free-text bios where category mapping requires judgment).

### Deterministic detection layer

Implemented as a series of pattern-matching functions in `enrich/founder_pedigree.py`. Company name matching uses `COMPANY_ALIASES` and `resolves_to_major()` from `models.py`.

```python
def detect_phd_program(bio_text: str) -> CategoryMatch | None:
    """Match founder bio against the B2 PhD program list. Returns category match if found."""
    ...

def detect_national_lab_tenure(bio_text: str) -> CategoryMatch | None:
    """Match against B5 lab list and position keywords (Staff Scientist, Principal, etc.)."""
    ...

def detect_fellowship(bio_text: str) -> CategoryMatch | None:
    """Match against B4 fellowship/grant programs."""
    ...

def detect_major_company_experience(bio_text: str) -> CategoryMatch | None:
    """Match against B1: company in MAJORS_AND_SERVICE_COS AND title in B1_TITLE_KEYWORDS."""
    ...

def detect_prior_exit(bio_text: str) -> CategoryMatch | None:
    """Match against B3: 'acquired by [COMPANY]' where COMPANY resolves via COMPANY_ALIASES."""
    ...

def detect_b6_pattern(
    bio_text: str,
    is_solo_founder: bool = False,
    has_technical_cofounder: bool = True,
) -> CategoryMatch | None:
    """Match against B6 known phrases; applies false-positive guard for consulting solo founders."""
    ...

def detect_houston_multipliers(
    bio_text: str,
    matches: list[CategoryMatch],
    licensed_ip_labs: list[str] | None = None,
    accelerator_membership: dict | None = None,
    doe_oced_hub: dict | None = None,
) -> list[MultiplierMatch]:
    """Apply Houston-specific multipliers. Uses matches for lab_ip_alignment cross-check."""
    ...
```

These run first. They produce structured matches against well-defined lookups. No LLM needed for the parts of the bio that contain enumerated terms.

### LLM interpretation layer

For category B1 (paraphrased major company experience), B3 (paraphrased prior exits), and B6 (other patterns), the LLM catches what the deterministic baseline misses because the patterns require interpretation of context: "30 years at Shell upstream" requires understanding that the role was technical and senior; "previously founded a methane sensing company acquired by SLB in 2019" requires identifying the acquirer category.

The LLM call uses a focused prompt that takes the founder bio + the deterministic matches already found, and asks for any additional category matches. This keeps the LLM's job narrow: "did the deterministic layer miss anything?" rather than "score this founder from scratch."

The prompt structure (drafted in Step 6 as `prompts/founder_pedigree_v1.md`):

```
[SYSTEM]
You score founder pedigree for the Ion's Houston Energy Mapper.
Given a founder bio and the categories already matched deterministically,
identify additional B1, B3, or B6 patterns that the deterministic layer missed.

[CATEGORIES TO LOOK FOR]
{B1, B3, B6 categories from this rubric}

[ALREADY MATCHED]
{deterministic matches as JSON}

[FOUNDER BIO]
{free text from team page or LinkedIn}

[OUTPUT INSTRUCTIONS]
Return JSON list of additional CategoryMatch objects.
Reference specific phrases from the bio in the evidence field.
If no additional matches, return [].
```

### Cost and determinism

Per founder LLM call: ~1,500 tokens input + ~300 tokens output ≈ $0.009 at Sonnet 4.6 pricing.

Average company has 2–3 named founders. 200 companies → ~500 founder calls → ~$4.50.

Temperature = 0.0; same bio + same deterministic matches produces same output. Reruns are stable.

---

## Synthetic test cases

These test the **DETERMINISTIC BASELINE detection**. The LLM interpretation layer for paraphrased patterns (e.g., "spent 30 years at Shell upstream") is validated separately in Step 8. Tests call `score_founder_pedigree(...)` directly with parameters unpacked from the fixture's `evidence` dict.

```json
[
  {
    "case_id": "FP-TC-01_rice_phd_with_lab_match",
    "description": "Founder with Rice ChemE PhD under Halas lab; company licensed Halas IP. Triggers B2 + lab+IP multiplier.",
    "evidence": {
      "name": "FOUNDER_A",
      "role": "CTO",
      "bio": "PhD in Chemical Engineering from Rice University, Halas Lab, 2019. Co-author on 14 peer-reviewed papers in plasmonic photocatalysis.",
      "company_licensed_ip_lab": "Rice Halas Lab"
    },
    "expected": {
      "categories_required": ["B2"],
      "multipliers_required": ["lab_ip_alignment", "houston_university_phd"],
      "tier_min": "HIGH",
      "confidence": "HIGH"
    }
  },
  {
    "case_id": "FP-TC-02_service_co_principal",
    "description": "Founder with 12 years at Schlumberger as Principal Engineer.",
    "evidence": {
      "name": "FOUNDER_B",
      "role": "CEO",
      "bio": "12 years at Schlumberger, most recently as Principal Engineer for completions technology. Holds 8 issued patents in downhole sensing.",
      "company_licensed_ip_lab": null
    },
    "expected": {
      "categories_required": ["B1"],
      "multipliers_required": ["service_co_senior"],
      "tier_min": "MEDIUM-HIGH",
      "confidence": "MEDIUM"
    }
  },
  {
    "case_id": "FP-TC-03_activate_houston_fellow",
    "description": "Activate Houston Fellow with Berkeley PhD.",
    "evidence": {
      "name": "FOUNDER_C",
      "role": "Co-founder",
      "bio": "Activate Houston Fellow (Cohort 2). PhD Materials Science, UC Berkeley (Ceder group), 2022.",
      "company_licensed_ip_lab": null
    },
    "expected": {
      "categories_required": ["B2", "B4"],
      "multipliers_required": ["houston_accelerator_program"],
      "tier_min": "MEDIUM-HIGH",
      "confidence": "HIGH"
    }
  },
  {
    "case_id": "FP-TC-04_serial_founder_acquired_to_major",
    "description": "Serial founder whose prior company was acquired by Halliburton.",
    "evidence": {
      "name": "FOUNDER_D",
      "role": "CEO",
      "bio": "Founder/CEO of Acme Sensors (acquired by Halliburton 2019). Prior to Acme, founded MethaneTech (still operating, Series B). 8 years at Baker Hughes early career.",
      "company_licensed_ip_lab": null
    },
    "expected": {
      "categories_required": ["B3", "B1"],
      "tier_min": "HIGH",
      "confidence": "HIGH"
    }
  },
  {
    "case_id": "FP-TC-05_lab_director_houston_relevant",
    "description": "Former NETL Lab Director.",
    "evidence": {
      "name": "FOUNDER_E",
      "role": "CSO",
      "bio": "Former Director of CO₂ Capture R&D at NETL (2014–2022). Senior Scientist 2008–2014.",
      "company_licensed_ip_lab": null
    },
    "expected": {
      "categories_required": ["B5"],
      "tier_min": "HIGH",
      "confidence": "HIGH"
    }
  },
  {
    "case_id": "FP-TC-06_consultant_solo_founder_low",
    "description": "Solo non-technical founder with consulting background only.",
    "evidence": {
      "name": "FOUNDER_F",
      "role": "Founder",
      "bio": "10 years at McKinsey Energy Practice, focused on upstream operations strategy. MBA, Wharton, 2014.",
      "company_licensed_ip_lab": null,
      "is_solo_founder": true,
      "has_technical_cofounder": false
    },
    "expected": {
      "categories_required": ["B6"],
      "tier_max": "LOW-MEDIUM",
      "confidence": "MEDIUM",
      "review_queue": true,
      "false_positive_pattern": "consulting_solo_no_technical_cofounder"
    }
  },
  {
    "case_id": "FP-TC-07_sparse_public_profile",
    "description": "Bio is one sentence; LinkedIn locked; no public team page detail.",
    "evidence": {
      "name": "FOUNDER_G",
      "role": "Co-founder",
      "bio": "Co-founder and Head of Engineering.",
      "company_licensed_ip_lab": null
    },
    "expected": {
      "categories_required": [],
      "tier_max": "LOW",
      "confidence": "LOW",
      "review_queue": true
    }
  },
  {
    "case_id": "FP-TC-08_uh_tcsuh_with_houston_multiplier",
    "description": "PhD from UH TcSUH under Selvamanickam.",
    "evidence": {
      "name": "FOUNDER_H",
      "role": "CTO",
      "bio": "PhD Mechanical Engineering, University of Houston, TcSUH (Selvamanickam group), 2020. 4 issued patents in 2G HTS wire manufacturing.",
      "company_licensed_ip_lab": null
    },
    "expected": {
      "categories_required": ["B2"],
      "multipliers_required": ["houston_university_phd"],
      "tier_min": "MEDIUM-HIGH",
      "confidence": "HIGH"
    }
  }
]
```

---

## Implementation notes

### New constants and helpers in models.py

```python
PEDIGREE_TIER_RANK: dict[str, int] = {
    "LOW": 0, "LOW-MEDIUM": 1, "MEDIUM": 2, "MEDIUM-HIGH": 3, "HIGH": 4,
}

COMPANY_ALIASES: dict[str, frozenset[str]] = {
    "SLB": frozenset({"SLB", "Schlumberger"}),
    "HAL": frozenset({"HAL", "Halliburton"}),
    "BHGE": frozenset({"BHGE", "Baker Hughes", "Baker Hughes GE"}),
    "ExxonMobil": frozenset({"ExxonMobil", "Exxon", "Mobil", "ExxonMobil LCS"}),
    "OXY": frozenset({"OXY", "Occidental", "Occidental Petroleum"}),
    "BP": frozenset({"BP", "British Petroleum", "bp"}),
    "Shell": frozenset({"Shell", "Royal Dutch Shell"}),
    "Chevron": frozenset({"Chevron", "Chevron Corporation"}),
}

def resolves_to_major(name: str) -> str | None:
    """Return canonical key if name matches any alias, else None."""
    ...
```

New `CompanyRecord` fields:

```python
licensed_ip_labs: list[str] = field(default_factory=list)
# e.g. ["Rice Halas Lab", "UT Austin Manthiram Lab"]

founders: list[dict] = field(default_factory=list)
# Shape: {"name": str, "role": str, "bio_text": str, "linkedin_url": str | None}
# Populated by enrich/founder.py; consumed by score_company_founders
```

### Module structure

```python
# enrich/founder_pedigree.py

B1_TITLE_KEYWORDS: frozenset[str] = frozenset({
    "Principal Engineer", "Distinguished Member", "Chief Scientist",
    "Senior Research Associate", "Schlumberger Fellow", "VP", "President",
    "Director", "Chief", "Principal",
})

MAJORS_AND_SERVICE_COS: frozenset[str] = frozenset({ ... })  # all canonical keys + aliases flattened

PHD_PROGRAMS_VERY_HIGH: dict[str, list[str]] = { ... }   # program → faculty list
PHD_PROGRAMS_HIGH: dict[str, list[str]] = { ... }
NATIONAL_LABS: dict[str, dict] = { ... }                  # lab → {focus, houston_multiplier_eligible}
FELLOWSHIPS_VERY_HIGH: frozenset[str] = frozenset({ ... })
FELLOWSHIPS_HIGH: frozenset[str] = frozenset({ ... })

# Public API
def score_founder_pedigree(
    founder_name: str,
    bio_text: str,
    role: str,
    company_id: str,
    company_licensed_ip_labs: list[str] | None = None,
    is_solo_founder: bool = False,
    has_technical_cofounder: bool = True,
    accelerator_membership: dict | None = None,
    doe_oced_hub: dict | None = None,
) -> FounderPedigree:
    """Score a single founder. Deterministic-only path implemented here.
    LLM augmentation step: TODO — implemented in Step 8 after
    prompts/founder_pedigree_v1.md is drafted.
    """
    ...

def score_company_founders(
    company: CompanyRecord,
) -> list[FounderPedigree]:
    """Score all founders for a company from company.founders list."""
    ...
```

### Pipeline orchestration

In `pipeline/orchestrator.py`, the founder pedigree stage runs after enrichment has identified founder names:

```python
for company in classified_companies:
    if company.is_excluded:
        continue
    pedigrees = score_company_founders(company)
    write_pedigrees(company.company_id, pedigrees)
```

`company.founders` is populated by `enrich/founder.py` (specced separately).

### Cost discipline

Pedigree scoring runs on companies that already passed the venture-scale filter. Approximate budget:

- Deterministic-only founders (clean PhD/lab/fellowship signal): no LLM call.
- Bio-based founders (B1/B3/B6 needed): one LLM call per founder.
- Estimate: 60% deterministic, 40% LLM → ~$2.50 per 100-company run.

---

## Calibration plan (Phase 3)

After the first full pipeline run:

1. Spot-check the top 10 highest-scored HIGH-tier founders. Confirm reasoning traces cite real evidence in bio text.
2. Spot-check the bottom 10 LOW-tier founders. Confirm sparseness or absence of pedigree signal — not algorithmic blindness.
3. Manually review the LOW-confidence queue. Override or confirm each, write to `data/validated_examples.jsonl`.
4. For miscalibration patterns (e.g., "the LLM consistently misses prior exits when the acquirer name is abbreviated"), iterate the prompt — save as `prompts/founder_pedigree_v2.md`.

The validated examples bank serves the same flywheel role as elsewhere: human corrections become few-shot examples for future runs.

---

## What this spec does NOT cover

- Houston presence tier assignment — see `docs/houston_presence_signals.md`.
- Venture-scale classification — see `docs/venture_scale_rubric.md`.
- Founder *name extraction* from team pages and websites — that's a separate enrichment step in `enrich/founder.py` (different module).
- Per-source harvesting — see `docs/source_inventory.md`.
- The actual LLM prompt — drafted in Step 6 as `prompts/founder_pedigree_v1.md`, versioned independently.
- Real-founder calibration — Phase 3 manual review.

---

## Changelog

- **v3** (2026-04-29):
  1. Recalibrated tier thresholds based on synthetic fixture validation: HIGH≥4.5, MEDIUM-HIGH≥3.0, MEDIUM≥2.0, LOW-MEDIUM≥1.0, LOW<1.0 (was HIGH≥8.0, MEDIUM-HIGH≥6.0, MEDIUM≥4.0, LOW-MEDIUM≥2.0, LOW<2.0).
  2. Added calibration rationale note to "Tier assignment from final score" section.

- **v2** (2026-04-29):
  1. Added `licensed_ip_labs: list[str]` and `founders: list[dict]` to `CompanyRecord` in `models.py`.
  2. Documented B1 deterministic detection boundary: fires on company name (via `COMPANY_ALIASES`) + title keyword (`B1_TITLE_KEYWORDS`); LLM catches paraphrased patterns.
  3. Clarified test invocation: tests call `score_founder_pedigree(...)` directly with fixture evidence unpacked; `score_company_founders` is a Phase 3 integration concern.
  4. Added `PEDIGREE_TIER_RANK` constant to `models.py` for `tier_min`/`tier_max` test assertions.
  5. Added `raw_multiplier_product` and `capped_multiplier_factor` fields to `FounderPedigree` schema; `final_score = base_score × capped_multiplier_factor`; `multipliers_applied` list preserved regardless of cap.
  6. Updated `detect_houston_multipliers` signature: takes `matches`, `licensed_ip_labs`, `accelerator_membership`, `doe_oced_hub` as explicit params (pure function); `lab_ip_alignment` cross-checks B2 match against `licensed_ip_labs`.
  7. Added `COMPANY_ALIASES` dict and `resolves_to_major()` helper to `models.py`; used by B1 detector and `service_co_senior` multiplier.
  8. Clarified B2 "VERY HIGH" notation: base 3.0 pts + 0.5 co-authorship bump when bio contains lab name AND publication-output language; total capped at 3.5.
  9. Added `founders: list[dict]` field to `CompanyRecord`; `score_company_founders` reads from `company.founders`; no ad-hoc `enriched_founders` state.
  10. Documented B1/B3/B6 deterministic baselines; updated fixture section header to clarify deterministic baseline scope; LLM layer validated in Step 8.

## Spec review history

| Version | Date | Reviewed by | Summary |
|---------|------|-------------|---------|
| v1 | 2026-04-28 | Claude (agent) | 10 concerns raised: missing CompanyRecord field for licensed_ip_labs, B1 determinism boundary, test invocation pattern, PEDIGREE_TIER_RANK absence, multiplier cap mechanics, detect_houston_multipliers signature purity, COMPANY_ALIASES alias map, B2 3+0.5 notation ambiguity, founders field placement, B1/B3/B6 test coverage scope |
| v2 | 2026-04-29 | User | All 10 concerns resolved; amendments applied as above |
| v3 | 2026-04-29 | User (post-implementation) | Tier thresholds recalibrated against synthetic fixture ground truth; HIGH≥4.5 replaces HIGH≥8.0 |
