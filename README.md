# Houston Energy Mapper

An AI-powered, repeatable pipeline that maps venture-scale startups across the Houston energy ecosystem — covering traditional energy and industrials, energy transition, and industrial tech.

Built as a take-home assignment for the Platform Associate role at the Ion (Rice University's Venture Advantage Platform).

---

## What it does

The pipeline ingests 25+ structured sources, classifies each company against a venture-scale rubric using Claude, scores Houston presence against a composite signal model, enriches passing companies with founder pedigree and sub-sector tags, deduplicates across sources, and exports a structured spreadsheet with per-field confidence flags and reasoning traces.

It is designed to be **re-run** — each run is idempotent, the flywheel compounds from validated examples, and the run log records every source's success/failure and cost.

---

## Final state (May 4, 2026)

- 13 active source harvesters
- 2,166 raw records harvested
- 113 VENTURE_SCALE / 227 BORDERLINE / 404 NOT_VENTURE_SCALE classified
- 149 records with founder pedigree extracted from company websites
- 514 records flagged for Phase 2 enrichment queue
- Flywheel: 11 validated examples encoded
- Primary deliverable: `data/exports/houston_energy_mapper_v1.xlsx`

---

## Architecture

```
Harvest → Filter (venture-scale) → Enrich → Houston Presence Score → Dedupe → Export
              ↑                                                                    ↓
         Flywheel: validated_examples.jsonl, relationship_graph.db, source_quality.db
```

**Five pipeline stages:**

| Stage | Module | Description |
|---|---|---|
| Harvest | `harvest/` | Per-source modules pull raw candidates and normalize to `RawCompanyRecord` |
| Filter | `signals/venture_scale.py` | Claude-powered classifier scores each company; hard-exclude rules run first |
| Enrich | `enrich/` | Founder pedigree, sub-sector, one-sentence summary for companies that pass |
| Score | `signals/houston_presence.py` | Composite signal scorer assigns tier A/B/C with per-signal trace |
| Export | `storage/export.py` | xlsx + CSV with all fields, confidence flags, and reasoning traces |

**Three flywheel components** (compound across runs):

| Component | File | Purpose |
|---|---|---|
| Examples bank | `data/validated_examples.jsonl` | Grows from manual overrides; injected as few-shot context |
| Relationship graph | `data/db/relationship_graph.db` | Founder/investor/accelerator affiliations; gives prior signal to connected companies |
| Source quality | `data/db/source_quality.db` | Per-source pass rates; surfaces where to focus harvesting effort |

---

## Source inventory

Sources span four tiers:

- **Institutional Houston** (14): Rice Alliance, Halliburton Labs, Greentown Houston, EnergyTech Nexus, Activate Houston, Ion District, ECV, Veriten/NexTen, Goose Capital, EIC, ETV, InnovationMap, EnergyCapitalHTX, RBPC alumni
- **Houston presence signals** (5): SEC EDGAR Form D (Houston ZIPs), ERCOT generator interconnection queue, Texas Comptroller franchise tax, job feeds (Greenhouse/Lever/Ashby), USPTO patents (Houston ZIP + Y02 CPC)
- **National Tier B/C** (8): Corporate VC arms, DOE/ARPA-E performers, Activate Fellows national, YC Climate, Breakthrough Energy, Lowercarbon, DCVC, EIP, Prelude
- **Trade press** (2): InnovationMap RSS, EnergyCapitalHTX RSS

---

## Houston presence scoring

Each company receives a tier (A / A-low / B-high / B / B-low / C) derived from a composite signal score:

| Signal weight | Examples |
|---|---|
| HIGH (3 pts) | Form D Houston address, Texas SOS formation, ERCOT IA signed, Houston accelerator residency, DOE hub sub-awardee |
| MEDIUM (2 pts) | Houston office on website, Houston investor lead, job postings at Houston address |
| LOW (1 pt) | Founder LinkedIn Houston, press mention of Houston operations |

Tier rules: ≥6 pts + ≥1 HIGH operational signal → A or B-high; 3–5 pts → B; 1–2 pts → B-low (review queue); 0 → C.

---

## Quick start

```bash
# 1. Clone and install
git clone <repo-url> && cd houston-energy-mapper
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Install Playwright browsers (only needed for headless harvesters)
playwright install chromium

# 3. Set up environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# 4. Dry run — see what the pipeline would do and estimated LLM cost
python cli.py run --all --dry-run

# 5. Run a single harvester
python cli.py harvest --sources rice_etvf

# 6. Run the full pipeline
python cli.py run --all

# 7. Export deliverable
python cli.py export
```

---

## Re-running

Every run is idempotent. Companies are identified by a canonical slug derived from their domain (or name before domain enrichment). Reruns update existing records rather than appending. Human-validated overrides (in `data/validated_examples.jsonl`) are never silently overwritten — model disagreements surface to the review queue.

---

## Output

```
data/exports/
├── houston_energy_mapper_v1.xlsx  # primary deliverable
└── run_log_YYYYMMDD.md            # per-source success/failure, token counts, cost
```

Each row in the spreadsheet includes: company name, website, primary sector, sub-sector, venture scale tier, score, confidence, summary, source(s), Houston tier, in-review queue flag, founder pedigree, classification reasoning, and human override.

---

## Project layout

```
houston_energy_mapper/
├── cli.py                         # typer CLI entry point
├── config/
│   ├── settings.py                # all tunable parameters (one place)
│   └── corporate_vc_sources.yaml  # config-driven VC portfolio harvester
├── pipeline/
│   └── orchestrator.py            # stage sequencing, browser lifecycle, cost tracking
├── harvest/
│   ├── base.py                    # BaseHarvester ABC + RawCompanyRecord dataclass
│   └── <source>.py                # one file per source
├── signals/
│   ├── houston_presence.py        # composite presence scorer (auditable trace)
│   ├── venture_scale.py           # Claude-powered classifier
│   └── confidence.py              # per-field confidence aggregation
├── enrich/
│   ├── founder_pedigree.py
│   ├── sub_sector.py
│   └── summary.py
├── dedupe/
│   └── matcher.py                 # rapidfuzz fuzzy matching + canonical ID promotion
├── flywheel/
│   ├── examples_bank.py           # validated_examples.jsonl read/write
│   ├── relationship_graph.py      # SQLite affiliation graph
│   └── source_quality.py          # per-source pass-rate tracking
├── llm/
│   ├── client.py                  # single auditable API entry point
│   └── prompt_loader.py           # file loading, Jinja2 rendering, few-shot injection
├── storage/
│   ├── db.py                      # SQLite schema and connection management
│   └── export.py                  # xlsx/csv export
├── utils/
│   ├── rate_limiter.py            # polite scrape delay enforcement
│   ├── slugify.py                 # canonical ID generation
│   └── html_cleaner.py            # HTML stripping and text normalization
├── prompts/                       # versioned prompt files (classifier_v1.md, etc.)
├── docs/                          # rubric, taxonomy, signal spec, source inventory
├── tests/                         # mirrors source tree; fixtures/ has gold-standard cases
└── data/
    ├── db/                        # SQLite databases (committed; part of deliverable)
    ├── exports/                   # xlsx/csv output
    └── validated_examples.jsonl   # human-validated few-shot examples (flywheel)
```

---

## LLM usage

All API calls go through `llm/client.py`. Every call:
- Loads its prompt from a versioned file in `prompts/` (no inline strings)
- Optionally validates output against a Pydantic schema
- Retries on transient errors with exponential backoff (tenacity)
- Logs prompt name, version, model, token counts, estimated cost, and latency
- Automatically injects validated few-shot examples from the examples bank (flywheel)

Default model: `claude-sonnet-4-6`. Opus reserved for final QA on flagged borderline cases.

---

## Prompts

Prompts are versioned files in `prompts/`. Naming convention: `{name}_{version}.md` (e.g. `classifier_v1.md`). Each file has a YAML header:

```markdown
---
name: classifier
version: v1
purpose: Score a company against the venture-scale rubric
input_shape: { name, description, website, tags, signals }
output_shape: { venture_scale_score, confidence, reasoning, hard_excluded, exclude_reason }
changed_from_previous: n/a (initial version)
---
[prompt body]
```

When a prompt is edited, a new file is saved (`classifier_v2.md`). Old versions are never deleted.
