# Rice Alliance / RACEA fixtures (archived)

These HTML fixtures are preserved from the original Rice Alliance investigation
(April 29, 2026) when the source was scoped before being renamed to `rice_etvf`
in Step 5.

## Files

- `etvf_2021_companies.html` — ETVF 2021 cohort listing. Not currently harvested
  (rice_etvf harvester covers 2022-2025). Preserved for potential year expansion.
- `profile_syzygy_plasmonics.html` — Sample profile page from Syzygy Plasmonics.
  Acceleware and Emvolon were chosen as canonical fixture examples in tests.
- `racea_portfolio.html` — Rice Alliance Clean Energy Accelerator portfolio page.
  Static HTML version (limited content; full directory is JS-rendered via React).
- `racea_class5.html` — RACEA Class 5 cohort page. Same JS-rendering issue.

## Why archived (not deleted)

The RACEA files are evidence of Phase 2 work: the RACEA portfolio at
ricecleanenergy.org/portfolio is JavaScript-rendered and requires a Playwright
harvester to scrape. These fixtures save ~30 minutes of re-fetch work when the
RACEA harvester is implemented in Phase 2.

The 2021 and Syzygy fixtures are kept for completeness — they may be useful for
year-coverage expansion or alternative profile examples.

## Date archived

2026-05-02 (Step 9 Batch 2 cleanup; original folder created 2026-04-29).
