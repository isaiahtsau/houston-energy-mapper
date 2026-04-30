# Source Inventory — Houston Energy Mapper

**Purpose:** Master catalog of harvest sources for the Houston Energy Mapper pipeline.
**Drives:** `harvest/*.py` modules and `pipeline/orchestrator.py` source registry.
**Version:** v3
**Last updated:** 2026-04-30

---

## How to read this catalog

Each source has a one-row metadata block. Field meanings:

- **ID** — Stable identifier referenced by `harvest/<id>.py` modules. Lower-case, underscore-separated.
- **Type** — Category. Canonical values: `accelerator`, `accelerator_innovation_district`, `vc_portfolio`, `corporate_vc`, `government_filing`, `rss_feed`, `fellowship_directory`, `university`, `event`, `patent_search`, `industry_group`, `commercial_api`. Must match `BaseHarvester.SOURCE_TYPE` allowed-values comment exactly.
- **Houston tier reach** — Which Houston presence tiers this source primarily surfaces (A / B / C). Most sources surface multiple tiers; this is the *primary* one.
- **Scrape method** — How we extract. Two top-level categories:
  - *Scraping methods* — parse content designed for humans: `static_html` (requests + BeautifulSoup), `headless_html` (Playwright), `pdf_extract` (pdfplumber)
  - *Non-scraping methods* — consume content designed for machines: `rest_api`, `rss_feed`, `xlsx_download`, `csv_download`
  Mental model: "are we extracting from a page designed for humans, or consuming a feed designed for machines?"
- **Scrape depth** — Shape of each harvest. Canonical values:
  - `single_page` — RSS feeds, simple government APIs; one fetch covers it
  - `listing_plus_detail` — most VC portfolios and accelerators; fetch listing, then walk detail pages
  - `paginated_listing` — large portfolios and directories; walk multiple listing pages, optionally then detail pages
  - `api_query` — REST APIs with structured responses (SEC EDGAR, USPTO PE2E, EFAST)
  - `file_download` — XLSX, CSV, PDF downloads (ERCOT, DOE OCED PDFs)
- **Auth required** — Yes/No. If yes, a brief note on what's needed (free signup, API key, none).
- **Update cadence** — How often the source publishes new candidates. Canonical values: `realtime`, `daily`, `weekly`, `monthly`, `quarterly`, `annual`, `event_driven`, `on_demand`. Prose detail (e.g., cohort schedule) may follow after a semicolon.
- **Expected yield** — Numeric monitoring range (e.g., `60-80`) followed by a prose description in parentheses. The monitoring range maps to `BaseHarvester.EXPECTED_YIELD` (bare integers only). The prose description is doc-only.
- **v1 status** — `implemented` / `deferred` / `stretch`. Deferred sources are flagged with rationale.

**Multi-source harvester pattern (Path B):** `corporate_vc_arms` and `national_climate_vc_portfolios` are config-driven. The orchestrator instantiates one harvester class per YAML entry at run time, injecting `SOURCE_NAME` via `__init__`. This preserves the 1:1 harvester↔`HarvestResult` mapping and per-source run-log granularity. Adding a new source = editing the YAML, not writing Python.

**Important caveat:** Yield estimates and scrape methods are best-guess from public-page inspection. Real harvest implementation will refine these. Per-source quirks (pagination, rate limits, robots.txt, CSS selectors) are documented in each `harvest/<id>.py` docstring once the harvester is built, not here.

---

## Tier 1 — Houston-anchored institutional sources

These are the highest-signal sources for Tier A and Tier B-high companies. Houston-headquartered or Houston-operating companies cycle through these consistently.

### `rice_etvf` — Rice Energy Tech Venture Forum (ETVF)

| Field | Value |
|-------|-------|
| Type | event |
| URL | https://alliance.rice.edu/etvf/past-conferences/{year}-etvf/Companies |
| Houston tier reach | A, A-low, B-high, B |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | annual; ETVF held each September |
| Expected yield | 80-240 (~80-120 per year pre-2025; ~99 in 2025 cohort; year list extensible via `ETVF_YEARS` class constant) |
| v1 status | implemented (Step 5 — first harvester) |

**Notes.** Two-pass harvest: listing pages (all years) then profile pages (2024+). 2022-2023 records are listing-only (no profile page exists on alliance.rice.edu for those cohorts). Includes ETVF participants from Rice Alliance RACEA, Halliburton Labs, Greentown Houston, and international presenters — RACEA-specific membership is not tagged on alliance.rice.edu and is resolved by cross-source dedup at Step 10 against `rice_alliance_racea`. `ETVF_YEARS = [2022, 2023, 2024, 2025]`; extend when future years are published. Year probe: harvester always tries `max(ETVF_YEARS) + 1` to detect new cohorts. Within-harvest dedup: slug-keyed (profile records) or name-keyed (listing-only); same company may appear as both a listing-only record (2022-2023 text list) and a profile record (2024+ grid) — cross-source dedup at Step 10 handles via fuzzy name matching.

---

### `rice_alliance_racea` — Rice Alliance Clean Energy Accelerator (RACEA) portfolio

| Field | Value |
|-------|-------|
| Type | accelerator |
| URL | https://ricecleanenergy.org/portfolio |
| Houston tier reach | A, A-low |
| Scrape method | headless_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | annual; cohort 5 in 2025 |
| Expected yield | 60-80 (~12-15 companies per cohort; ~60-80 alumni total across 5 cohorts) |
| v1 status | deferred (Step 7+ — requires Playwright; pending headless_html harvester pattern) |

**Deferral rationale.** ricecleanenergy.org/portfolio is JavaScript-rendered (React SPA) — static requests return no portfolio content. Requires Playwright. Deferred until Step 7+ when the headless_html harvester pattern is established. RACEA companies are partially surfaced via `rice_etvf` (ETVF participants include RACEA cohort members), but RACEA-specific membership and cohort class tags are only available from ricecleanenergy.org. Pre-implementation inspection: alliance.rice.edu/clean-energy-accelerator returns 404; ricecleanenergy.org is the authoritative RACEA source.

---

### `halliburton_labs` — Halliburton Labs cohort

| Field | Value |
|-------|-------|
| Type | accelerator |
| URL | https://halliburtonlabs.com/portfolio/ |
| Houston tier reach | A, B-high |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | quarterly; rolling cohorts with ~quarterly announcements |
| Expected yield | 30-50 (~30-50 companies cumulative; 5-10 new per quarter) |
| v1 status | implemented (Step 7) |

**Notes.** Halliburton's industrial accelerator. Strong B-high signal — company physical presence at Halliburton Labs Houston facility counts as a HIGH operational Houston presence signal.

---

### `greentown_houston` — Greentown Houston tenant directory

| Field | Value |
|-------|-------|
| Type | accelerator |
| URL | https://greentownlabs.com/houston/ |
| Houston tier reach | A |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | monthly; rolling membership |
| Expected yield | 80-120 (~80-120 active member companies) |
| v1 status | implemented (Step 9 — Batch 2) |

**Notes.** Strongest physical-presence signal for Tier A. 4200 San Jacinto address. Some companies are HQ Houston, some are non-Houston-HQ with Houston pilot operations — distinguish via `is_houston_hq` field during enrichment.

---

### `energytech_nexus` — EnergyTech Nexus (COPILOT, Pilotathon, LiftOff programs)

| Field | Value |
|-------|-------|
| Type | accelerator |
| URL | https://energytechnexus.com |
| Houston tier reach | A, B |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | quarterly; 3 programs on varying schedules |
| Expected yield | 15-25 (~15-25 companies across all programs) |
| v1 status | implemented (Step 9 — Batch 2) |

**Notes.** Founded 2023, Houston-anchored. Covers digital industrial, energy transition, decarbonization. Identified as a v1 gap by Research output Section 1.

---

### `activate_houston` — Activate Houston cohort (formerly Cyclotron Road)

| Field | Value |
|-------|-------|
| Type | fellowship_directory |
| URL | https://activate.org/houston |
| Houston tier reach | A |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | annual; Cohort 1 launched 2024 |
| Expected yield | 8-12 (~8-12 fellows per cohort) |
| v1 status | implemented (Step 9 — Batch 2) |

**Notes.** Activate fellows are HIGH founder pedigree signal (B4). Activate Houston launched 2024 — second-cohort companies may not appear until late 2025.

---

### `ion_district` — Ion District tenant directory

| Field | Value |
|-------|-------|
| Type | accelerator_innovation_district |
| URL | https://iondistrict.com |
| Houston tier reach | A, A-low |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | quarterly; rolling tenant updates |
| Expected yield | 40-60 (~40-60 tenants; mixed startups, corporate offices, support orgs) |
| v1 status | implemented (Step 9 — Batch 3) |

**Notes.** Tenant directory mixes venture-scale startups with corporate offices and support orgs (legal, recruiting). Hard-exclude rules filter the non-startups; remainder routes to classifier.

---

### `innovationmap_rss` — InnovationMap RSS

| Field | Value |
|-------|-------|
| Type | rss_feed |
| URL | https://www.innovationmap.com/feed/ |
| Houston tier reach | A, B-high, B |
| Scrape method | rss_feed |
| Scrape depth | single_page |
| Auth required | No |
| Update cadence | realtime; multiple posts per day |
| Expected yield | 20-40 (~80-150 company mentions per quarter; ~20-40 new venture-scale candidates) |
| v1 status | implemented (Step 7) |

**Notes.** Pre-filtered for Houston relevance — every article is Houston-focused. Strong source for surfacing companies before they appear in accelerator portfolios.

---

### `energycapitalhtx_rss` — EnergyCapitalHTX RSS

| Field | Value |
|-------|-------|
| Type | rss_feed |
| URL | https://energycapitalhtx.com/feed |
| Houston tier reach | A, B-high |
| Scrape method | rss_feed |
| Scrape depth | single_page |
| Auth required | No |
| Update cadence | realtime |
| Expected yield | 15-30 (~40-80 company mentions per quarter; ~15-30 new candidates) |
| v1 status | implemented (Step 7) |

**Notes.** Specifically energy-focused Houston trade press. Tighter signal-to-noise than InnovationMap for the Mapper's mandate.

---

### `rbpc_alumni` — Rice Business Plan Competition alumni

| Field | Value |
|-------|-------|
| Type | event |
| URL | https://rbpc.rice.edu/alumni |
| Houston tier reach | A, B (varies — many out-of-state competitors) |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | annual; March/April competition each year |
| Expected yield | 40-80 (~40 finalists per year; ~80 in energy/industrial subset post-2018) |
| v1 status | implemented (Step 9 — Batch 3) |

**Notes.** RBPC has long history (40+ years). Filters: only finalists post-2018, only energy/cleantech/industrial track. Many alumni are now post-Series A — venture-scale qualified.

---

### `goose_capital` — Goose Capital portfolio

| Field | Value |
|-------|-------|
| Type | vc_portfolio |
| URL | https://goosecapital.com/portfolio |
| Houston tier reach | A, A-low |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | quarterly |
| Expected yield | 30-40 (~30-40 portfolio companies) |
| v1 status | implemented (Step 9 — Batch 1) |

**Notes.** Houston-based VC, strong Houston-energy thesis. Portfolio overlaps with Greentown Houston, Halliburton Labs cohort.

---

### `ecv_portfolio` — Energy Capital Ventures portfolio

| Field | Value |
|-------|-------|
| Type | vc_portfolio |
| URL | https://energycapitalventures.com/portfolio |
| Houston tier reach | A, B-high, B |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | quarterly |
| Expected yield | 25-40 (~25-40 portfolio companies) |
| v1 status | implemented (Step 9 — Batch 1) |

**Notes.** ECV co-investing in Houston-anchored deals is a MEDIUM signal in `houston_co_investor_whitelist`. Portfolio surfaces Tier B-high companies that show up in Houston pilot/customer pipelines.

---

### `etv_portfolio` — Energy Transition Ventures portfolio

| Field | Value |
|-------|-------|
| Type | vc_portfolio |
| URL | https://etv.energy/portfolio (verify URL during implementation) |
| Houston tier reach | A, B-high, B |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | quarterly |
| Expected yield | 20-30 (~20-30 portfolio companies) |
| Pre-implementation check | Verify URL and portfolio page structure before building harvester. Reassign to deferred if portfolio is not yet published. Pre-implementation task assigned to Step 9 Batch 1 lead. |
| v1 status | implemented (Step 9 — Batch 1) |

**Notes.** Identified as a v1 gap by Research output. Houston-affiliated, energy-transition focused. URL/structure to verify during implementation per pre-implementation check above.

---

### `mercury_fund` — Mercury Fund portfolio

| Field | Value |
|-------|-------|
| Type | vc_portfolio |
| URL | https://mercuryfund.com/portfolio |
| Houston tier reach | A, B |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | quarterly |
| Expected yield | 50-80 (~50-80 portfolio companies; energy + non-energy mixed; filter required) |
| v1 status | deferred (Step 11+) |

**Deferral rationale.** Mercury's portfolio is broader than energy. Filtering to energy/industrial subset requires per-company classification, increasing harvest cost. Deferred unless the v1 portfolio yields too few candidates.

---

### `veriten` — Veriten / NexTen Fund

| Field | Value |
|-------|-------|
| Type | vc_portfolio |
| URL | https://veriten.com (portfolio page TBD) |
| Houston tier reach | A, B |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | on_demand; deal-driven, schedule unknown |
| Expected yield | 10-20 (~10-20 portfolio companies) |
| v1 status | deferred (Step 11+) |

**Deferral rationale.** Identified as a v1 gap by Research output. Veriten/NexTen is newer; portfolio may not be fully published yet. Deferred to Phase 2 retest or manual research-mode pickup.

---

## Tier 2 — National sources surfacing Houston-relevant companies

These sources surface Tier B and Tier C candidates: companies operating outside Houston but with operational, capital, or customer ties relevant to the platform.

### `corporate_vc_arms` — Houston-relevant corporate VC portfolios (config-driven)

| Field | Value |
|-------|-------|
| Type | corporate_vc |
| URL | per-VC URLs in `config/corporate_vc_sources.yaml` |
| Houston tier reach | A (some), B-high, B |
| Scrape method | static_html primarily; headless_html for JS-rendered portfolios |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | quarterly; varies per VC |
| Expected yield | 120-180 (~120-180 portfolio companies across all 9 sub-sources) |
| v1 status | partially implemented (top 4 sub-sources in Step 9 — Batch 1; remainder deferred Step 11+) |

**Multi-source pattern (Path B).** The orchestrator instantiates `CorporateVcHarvester` once per YAML entry, injecting `SOURCE_NAME` via `__init__`. Each instance produces one `HarvestResult` with per-source run-log granularity intact. `config/corporate_vc_sources.yaml` exists in the scaffold with template entries — Step 9 Batch 1 work is populating it with the top-4 CVCs, not creating the file from scratch.

**Sub-sources (priority order):**

1. **Chevron Technology Ventures (CTV)** — Houston-HQ corporate VC. Strongest Houston signal among CVCs. *(v1 implemented)*
2. **SLB Ventures (Schlumberger)** — Houston-HQ. *(v1 implemented)*
3. **ExxonMobil Low Carbon Solutions** — Houston-relevant industrial deals. *(v1 implemented)*
4. **Baker Hughes Energy Ventures (BHEV)** — Houston-HQ. *(v1 implemented)*
5. bp Ventures — international, Houston-relevant subset. *(deferred Step 11+)*
6. Shell Ventures — international, Houston-relevant subset. *(deferred Step 11+)*
7. Equinor Ventures — international, narrower Houston relevance. *(deferred Step 11+)*
8. OGCI Climate Investments — multi-LP coalition, broad geography. *(deferred Step 11+)*
9. Aramco Ventures — narrower Houston relevance. *(deferred Step 11+)*

---

### `national_climate_vc_portfolios` — National climate VC portfolios (config-driven)

| Field | Value |
|-------|-------|
| Type | vc_portfolio |
| URL | per-VC URLs in `config/climate_vc_sources.yaml` |
| Houston tier reach | B, B-low, C |
| Scrape method | static_html or headless_html per portfolio |
| Scrape depth | listing_plus_detail or paginated_listing per portfolio |
| Auth required | No |
| Update cadence | quarterly |
| Expected yield | 30-60 (~200-400 portfolio companies total; ~30-60 with Houston operational signal) |
| v1 status | partially implemented (top 3 sub-sources in Step 9 — Batch 4; remainder deferred Step 11+) |

**Multi-source pattern (Path B).** Same pattern as `corporate_vc_arms`: the orchestrator instantiates `ClimateVcHarvester` once per YAML entry in `config/climate_vc_sources.yaml`.

**Sub-sources (priority by Houston-deal-flow density):**

1. **Lowercarbon Capital** *(v1 implemented)*
2. **Breakthrough Energy Ventures** *(v1 implemented)*
3. **DCVC** *(v1 implemented)*
4. Energy Impact Partners (EIP) *(deferred Step 11+)*
5. Prelude Ventures *(deferred Step 11+)*
6. Khosla Ventures (energy track) *(deferred Step 11+)*
7. Congruent Ventures *(deferred Step 11+)*
8. Galvanize Climate Solutions *(deferred Step 11+)*
9. Clean Energy Ventures *(deferred Step 11+)*
10. S2G Ventures *(deferred Step 11+)*

**Notes.** Most companies in these portfolios will be Tier B-low or C — this is *expected*, since the platform's Tier C list is exactly this universe. Surfaces recruiting targets for the platform team.

---

### `activate_fellows_national` — Activate Fellows national directory

| Field | Value |
|-------|-------|
| Type | fellowship_directory |
| URL | https://activate.org/fellows |
| Houston tier reach | B, C |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | annual |
| Expected yield | 40-60 (~250-300 fellows cumulative; ~40-60 with relevant focus) |
| v1 status | implemented (Step 9 — Batch 4) |

**Notes.** National Activate Fellows (Berkeley, Boston, NYC, Houston) — strong B4 founder pedigree signal. Houston cohort is a Tier A subset; remainder is Tier C recruiting universe.

---

### `yc_climate` — Y Combinator climate batch alumni

| Field | Value |
|-------|-------|
| Type | accelerator |
| URL | https://www.ycombinator.com/companies?industry=Climate |
| Houston tier reach | B, C |
| Scrape method | static_html |
| Scrape depth | paginated_listing |
| Auth required | No |
| Update cadence | event_driven; biannual batch announcements |
| Expected yield | 20-40 (~150-250 climate-batch companies; ~20-40 hardware/energy subset) |
| v1 status | deferred (Step 11+) |

**Deferral rationale.** YC's climate batch has strong software representation but weaker hardware/energy density. Houston relevance is sparse. Deferred unless other sources yield too few B/C candidates.

---

### `breakthrough_energy_fellows_directory` — BE Fellows directory

| Field | Value |
|-------|-------|
| Type | fellowship_directory |
| URL | https://breakthroughenergy.org/fellows |
| Houston tier reach | B, C |
| Scrape method | static_html |
| Scrape depth | listing_plus_detail |
| Auth required | No |
| Update cadence | annual |
| Expected yield | 150-180 (~150-180 fellows across ~5 cohorts) |
| v1 status | implemented (Step 9 — Batch 4) |

**Notes.** ~5 cohorts to date, $2B+ follow-on capital cumulative per Research output. Strong B4 founder pedigree signal. Houston-related fellows surface Tier B-C.

---

## Tier 3 — Government filings and operational signals

These are the highest-signal sources for venture-scale validation and Houston operational presence. Most require API rather than scraping.

### `sec_edgar_form_d` — SEC EDGAR Form D filings filtered by Houston ZIP

| Field | Value |
|-------|-------|
| Type | government_filing |
| URL | https://efts.sec.gov/LATEST/search-index?q=&forms=D (filtered by ZIP) + https://data.sec.gov |
| Houston tier reach | A, A-low |
| Scrape method | rest_api |
| Scrape depth | api_query |
| Auth required | No |
| Update cadence | realtime; filings within 15 days of round close |
| Expected yield | 20-30 (~80-120 Houston-ZIP Form D filings per year; ~20-30 venture-scale-relevant) |
| v1 status | implemented (Step 9 — Batch 5) |

**Notes.** Per architecture review v2, EDGAR is REST API not scrape. Watch flag: filings filed by law firms (Vinson & Elkins at 910 Louisiana, Norton Rose Fulbright at 1301 McKinney, Baker Botts at 910 Louisiana) — `form_d_filed_by_law_firm` flag must be set; `form_d_houston_address` does not contribute points in those cases.

---

### `ercot_interconnection_queue` — ERCOT generator interconnection status

| Field | Value |
|-------|-------|
| Type | government_filing |
| URL | https://www.ercot.com/gridinfo/resource (monthly XLSX; verify exact URL pattern during implementation) |
| Houston tier reach | A, B-high |
| Scrape method | xlsx_download |
| Scrape depth | file_download |
| Auth required | No (public download) |
| Update cadence | monthly |
| Expected yield | 100-200 (~2,000-3,000 active queue entries; ~100-200 company-developer rows after load_zone + milestone filter) |
| v1 status | implemented (Step 9 — Batch 5) |

**Notes.** Harvest stage filters by `load_zone == "Houston"` and `milestone == "IA-signed"` only — emits one `RawCompanyRecord` per queue entry. Cross-reference to venture-scale candidates happens during enrichment via the houston_presence scorer. ERCOT queue alone surfaces grid-scale developers — the enrichment cross-reference identifies which are venture-scale companies vs. utility/IPP projects. The harvester programmatically downloads the most recent month's file from the stable ERCOT URL (stored in `data/raw/ercot/`), parses with openpyxl. Not human-refreshed.

---

### `texas_sos_franchise_tax` — Texas Comptroller franchise tax search

| Field | Value |
|-------|-------|
| Type | government_filing |
| URL | https://mycpa.cpa.state.tx.us/coa/Index.html |
| Houston tier reach | A |
| Scrape method | static_html |
| Scrape depth | single_page |
| Auth required | No |
| Update cadence | on_demand; per-company lookup during enrichment |
| Expected yield | N/A (enrichment lookup — not bulk harvest) |
| v1 status | implemented as enrichment lookup (Step 9 — Batch 5) |

**Notes.** Used to verify Houston-county formation (Harris, Fort Bend, Montgomery, Brazoria, Galveston, Waller) for `texas_sos_houston_county_formation` HIGH signal. Per-company lookup during enrichment, not a standalone bulk harvester.

---

### `doe_oced_hub_awards` — DOE OCED Hydrogen Hub and DAC Hub project documents

| Field | Value |
|-------|-------|
| Type | government_filing |
| URL | https://www.energy.gov/oced; https://hyvelocityhub.com |
| Houston tier reach | A (Houston-located), B-high (named partner role), B (off-taker) |
| Scrape method | pdf_extract + static_html (hybrid: PDFs for award docs, static_html for hub websites) |
| Scrape depth | file_download |
| Auth required | No |
| Update cadence | event_driven; award announcements and project milestones |
| Expected yield | 30-60 (~30-60 sub-awardees / partners across Gulf Coast hubs) |
| v1 status | implemented (Step 9 — Batch 5) |

**Notes.** Strongest non-Houston-HQ signal for Tier B-high. DOE OCED documents name technology providers, off-takers, and EPC partners with project locations. Houston-located projects are direct A/A-low candidates; out-of-state companies named in Houston-located projects are Tier B-high.

---

### `doe_arpa_e_performers` — DOE/ARPA-E performer lists

| Field | Value |
|-------|-------|
| Type | government_filing |
| URL | https://arpa-e.energy.gov/projects |
| Houston tier reach | B, C |
| Scrape method | static_html |
| Scrape depth | paginated_listing |
| Auth required | No |
| Update cadence | quarterly; program-cycle announcements |
| Expected yield | 30-50 (~1,500+ historical performers; ~30-50 Houston-affiliated subset) |
| v1 status | deferred (Step 11+) |

**Deferral rationale.** Bulk performer list is high-volume; filtering to Houston subset requires per-company affiliation lookup. Deferred to Phase 2 unless Tier C universe needs expansion.

---

### `uspto_patent_search` — USPTO patent search (Y02 CPC codes, Houston-area assignees)

| Field | Value |
|-------|-------|
| Type | patent_search |
| URL | https://ppubs.uspto.gov/pubwebapp |
| Houston tier reach | A, B-low, C (stealth discovery) |
| Scrape method | rest_api |
| Scrape depth | api_query |
| Auth required | No |
| Update cadence | weekly; new patent publications |
| Expected yield | 20-40 (~200-400 patents per year matching Y02E, Y02P, Y02C, Y02B/T/W, Y04S CPC codes with Houston-area assignees; ~20-40 unique companies) |
| v1 status | stretch (Phase 2 — Step 13+) |

**Deferral rationale.** Stealth discovery via patents is a high-value Phase 2 capability — surfaces companies not yet in any portfolio or trade press. Deferred to Phase 2 Step 13+ because the patent → company resolution requires per-assignee dedup work that depends on the dedup layer being mature. Strong Q&A talking point: "stealth-discovery via USPTO Y02 patent search by Houston assignee is the v2 expansion path."

---

## Tier 4 — Houston-presence signal sources (cross-reference, not bulk harvest)

These are not standalone harvesters — they're enrichment-stage lookups used to validate Houston presence signals on companies surfaced from Tier 1-3.

### `port_houston_minutes` — Port Houston Commission monthly minutes

| Field | Value |
|-------|-------|
| Type | government_filing |
| URL | https://porthouston.com/about-us/commission |
| Houston tier reach | A, B-high (named lease/easement) |
| Scrape method | pdf_extract |
| Scrape depth | file_download |
| Auth required | No |
| Update cadence | monthly |
| Expected yield | 5-10 (~5-10 named entities per month; cumulative ~60-100/year) |
| v1 status | deferred (Step 11+) |

**Deferral rationale.** Per-company port lease/easement is a HIGH operational signal but rare (most candidates won't have one). Deferred unless v1 surfaces companies that warrant verification.

---

### `form_5500_dol` — Form 5500 employee benefit plan filings

| Field | Value |
|-------|-------|
| Type | government_filing |
| URL | https://efast.dol.gov/5500Search |
| Houston tier reach | A (Houston-ZIP plan sponsor with ≥10 participants) |
| Scrape method | rest_api |
| Scrape depth | api_query |
| Auth required | No |
| Update cadence | annual; rolling filing deadlines |
| Expected yield | N/A (enrichment lookup — not bulk harvest) |
| v1 status | implemented as enrichment lookup (Step 9 — Batch 5) |

**Notes.** HIGH signal for Houston-HQ confirmation. Per-company lookup during enrichment, similar to Texas SOS franchise tax.

---

### `job_feeds` — Greenhouse / Lever / Ashby per-company JSON feeds

| Field | Value |
|-------|-------|
| Type | commercial_api |
| URL | varies (e.g., `boards-api.greenhouse.io/v1/boards/<company>/jobs`) |
| Houston tier reach | B-high, B (Houston-located positions) |
| Scrape method | rest_api |
| Scrape depth | api_query |
| Auth required | No |
| Update cadence | realtime; continuously updated |
| Expected yield | N/A (enrichment lookup — not bulk harvest) |
| v1 status | implemented as enrichment lookup (Step 9 — Batch 5) |

**Notes.** MEDIUM signal for Houston operational presence (`houston_job_postings_substantive` requires ≥3 Houston jobs OR ≥1 site-specific role). Per-company API call during enrichment.

---

## Sources considered and excluded from v1

These were considered during scoping and explicitly descoped. Documenting the *why* prevents re-litigation in future iterations.

### Social/forum platforms — Reddit, X (Twitter), LinkedIn search

**Why excluded.** Signal-to-noise is poor for venture-scale identification. ToS friction is high (LinkedIn post-HiQ ruling, Reddit/X paid API). Captured intent (catching stealth companies, surfacing Tier B signals) through institutional substitutes: USPTO patent search, press release wires, DOE/ARPA-E performer lists, trade press.

**Q&A framing.** "We chose institutional sources over social scraping because (a) signal density is higher, (b) ToS friction is lower, (c) reproducibility is better — institutional sources have stable identifiers; social posts don't. The intent of social monitoring is captured via USPTO patent stealth discovery and trade-press RSS feeds."

### Paid databases — PitchBook, Crunchbase Pro, LinkedIn Sales Navigator, PeopleDataLabs, Apollo

**Why excluded.** Per assignment constraint: no paid databases.

**Q&A framing.** "PitchBook and Crunchbase aggregate from the institutional sources we're harvesting directly. The constraint pushes us toward primary-source signal that aggregators flatten — Form D, ERCOT queue, USPTO assignees — which is the more interesting and reproducible artifact for the Ion's mandate."

### TMCi / JLABS @ TMC — Texas Medical Center innovation programs

**Why excluded from v1.** TMC focuses on biotech/medtech. Subset with industrial-bio crossover (Cemvita-type companies) is small enough to surface via other sources (Greentown Houston, Halliburton Labs, EnergyTech Nexus). Including TMC adds noise.

**Q&A framing.** "TMC programs are biotech-anchored. Industrial-bio companies relevant to the Mapper surface through Houston accelerators that explicitly target cleantech. Adding TMC would dilute the dataset with non-target candidates."

### CERAWeek and OTC speaking-slot rosters

**Why excluded as a source.** Speaking-slot data is captured as a LOW signal (`event_speaking_slot`) on companies surfaced from elsewhere. As a standalone harvester, the speaker rosters mix venture-scale companies with majors, service co executives, and academics — high noise, low marginal yield.

**Q&A framing.** "Event speaking is encoded as a corroborating LOW signal during enrichment, not as a discovery source. Standalone roster scraping would surface mostly non-target entities."

---

## Summary by status

**Counting rule:** "Implemented" includes any source with a v1 status of `implemented`, including enrichment lookups. Multi-source harvesters (`corporate_vc_arms`, `national_climate_vc_portfolios`) are counted by the number of sub-sources implemented in v1, not as 1 source. The same rule applies to deferred sub-sources.

| Status | Count | Notes |
|--------|-------|-------|
| Implemented (v1) | 27 | 12 Tier 1 standalone + 2 Tier 2 standalone + 4 corporate VC sub-sources + 3 national climate VC sub-sources + 3 Tier 3 standalone + 3 Tier 3 enrichment lookups |
| Deferred (Phase 2) | 18 | 6 standalone sources + 5 corporate VC remainder + 7 national climate VC remainder |
| Stretch (Phase 2+) | 1 | USPTO stealth discovery (Phase 2 — Step 13+; defer until dedup layer is mature) |
| Considered + excluded | 4 categories | Social, paid databases, TMC, event rosters |

**Total source universe considered:** 27 implemented + 18 deferred + 1 stretch = 46 active sources + 4 excluded categories = 50 distinct decisions documented.

---

## What this inventory does NOT cover

- Per-source CSS selector specifics, pagination quirks, rate limit thresholds — these live in each `harvest/<id>.py` docstring.
- Source-specific data quality issues — discovered during real harvest runs, logged in run reports.
- Order of harvester implementation within a step — that's an orchestration decision, not a source-list decision.
- Calibration of yield estimates — these refine after first full pipeline run.

The inventory is the catalog. The harvesters are the implementations. Two different artifacts.

---

## Changelog

- **v3** (2026-04-30): `rice_alliance` renamed to `rice_etvf` after pre-implementation inspection revealed alliance.rice.edu archive aggregates ETVF participants without RACEA-specific tagging; ricecleanenergy.org portfolio is JavaScript-rendered and requires Playwright. RACEA-specific harvester added as new deferred source `rice_alliance_racea` (Step 7+ pending headless_html pattern). `rice_etvf` Type updated to `event`; URL changed to ETVF year-pattern; Houston tier reach broadened to A/A-low/B-high/B to reflect mixed participant pool; `EXPECTED_YIELD` revised to `80-240` (2025 cohort: 99 records; 2022-2025 combined: 202 records). Summary: deferred count bumped 17→18; total universe 45→46. Dual-record note added: same company may appear as listing-only (2022-2023) and profile (2024+).

- **v2** (2026-04-29):
  1. USPTO renumbered to Phase 2 — Step 13+; v1 status updated to `stretch (Phase 2 — Step 13+)`. Export remains Step 12.
  2. Multi-source harvester architecture: Path B (1:1 `HarvestResult` with `SOURCE_NAME` injected via `__init__`). Pattern documented in "How to read" and per-source notes for `corporate_vc_arms` and `national_climate_vc_portfolios`.
  3. Type vocabulary expanded: added `accelerator_innovation_district`, `fellowship_directory`; `rss_feed` clarified as canonical. Reclassified: `ion_district` → `accelerator_innovation_district`; `innovationmap_rss`, `energycapitalhtx_rss` → `rss_feed`; `activate_houston`, `activate_fellows_national`, `breakthrough_energy_fellows_directory` → `fellowship_directory`. Must match `BaseHarvester.SOURCE_TYPE` comment.
  4. Update cadence vocabulary canonicalized: `realtime`, `daily`, `weekly`, `monthly`, `quarterly`, `annual`, `event_driven`, `on_demand`. All source rows updated; prose detail follows after semicolon.
  5. ERCOT harvest stage clarified: harvest-only; filters by `load_zone` and `milestone` only; enrichment handles venture-scale cross-reference. Scrape method changed to `xlsx_download`; programmatic fetch (not manual export).
  6. `etv_portfolio`: `pre_implementation_check` field added; URL verification required before building harvester.
  7. `EXPECTED_YIELD` format split: numeric monitoring range (bare integers, maps to `BaseHarvester.EXPECTED_YIELD`) + prose description in parentheses. Both in the row; prose is doc-only.
  8. Summary counting rule made explicit; count re-derived as 27 implemented v1 sources (sub-sources counted individually); total source universe updated to 49 distinct decisions.
  9. `config/corporate_vc_sources.yaml` noted as existing in scaffold; Step 9 Batch 1 populates it with top-4 CVCs, not creates it.
  10. Cross-cutting A: `scrape_depth` field added to all source rows with 5 canonical values documented in "How to read this catalog."
  11. Cross-cutting B: Scrape method taxonomy refined into scraping (human-facing content: `static_html`, `headless_html`, `pdf_extract`) vs. non-scraping (machine-facing content: `rest_api`, `rss_feed`, `xlsx_download`, `csv_download`). Distinction documented in "How to read"; all source rows updated.
  12. Type vocabulary added `commercial_api` to handle commercial ATS APIs (Greenhouse, Lever, Ashby) used as per-company enrichment lookups. `job_feeds` reclassified from `government_filing` to `commercial_api`. (Inherited issue from v1; surfaced during v2 cleanup pass.)

## Spec review history

| Version | Date | Reviewed by | Summary |
|---------|------|-------------|---------|
| v1 | 2026-04-29 | Claude (agent) | 10 concerns raised: Step 12 collision (USPTO vs. export), config-driven multi-source harvester architecture, undeclared Type vocabulary members, undeclared Update cadence members, ERCOT stage ambiguity (harvest vs. enrichment filter), ERCOT manual vs. programmatic fetch, unverified `etv_portfolio` URL, `EXPECTED_YIELD` format mismatch with base class, summary count discrepancy (13 vs. actual), `config/corporate_vc_sources.yaml` prerequisite |
| v2 | 2026-04-29 | User | All 10 concerns resolved + 2 cross-cutting additions (scrape_depth field, scrape method taxonomy); amendments applied as changelog above |
| v3 | 2026-04-30 | User | `rice_alliance` renamed to `rice_etvf`; `rice_alliance_racea` added as deferred. Pre-implementation inspection caught alliance.rice.edu aggregation pattern and RACEA URL change before code was written. |
