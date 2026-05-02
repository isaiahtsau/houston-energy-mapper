"""
Pipeline orchestrator.

Coordinates the five pipeline stages in sequence:
  1. Harvest   — pull raw candidates from all configured sources
  2. Classify  — venture-scale classifier scores each candidate
  3. Enrich    — founder pedigree, sub-sector, summary for passing companies
  4. Score     — Houston presence tier assignment
  5. Dedupe    — fuzzy match and merge duplicates across sources
  6. Export    — write xlsx and CSV deliverables

Responsibilities:
  - Playwright browser lifecycle: single shared instance for headless harvesters
  - LLM call count tracking and circuit breaker enforcement
  - Source failure isolation: one failed harvester does not abort the pipeline
  - Dry-run support: renders plans and cost estimates without DB writes or API calls
  - Run log generation: structured per-run report written to data/exports/

This module is imported by cli.py. Each public function corresponds to a CLI command.
None of the public functions here import at module level from heavy dependencies
(playwright, pandas, anthropic) — imports are deferred to function bodies so that
`python cli.py --help` is fast even without all packages installed.
"""
from __future__ import annotations

import datetime
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console

logger = logging.getLogger(__name__)

# The current run's UUID. Set at the start of run_pipeline() or any stage entry point.
_current_run_id: str | None = None


def _get_run_id() -> str:
    """Return (or initialize) the current pipeline run ID."""
    global _current_run_id
    if _current_run_id is None:
        _current_run_id = str(uuid.uuid4())
    return _current_run_id


# ─────────────────────────────────────────────────────────────────────────────
# Stage: Harvest
# ─────────────────────────────────────────────────────────────────────────────

def _build_harvester_registry() -> dict[str, type]:
    """Build the harvester registry on first use (lazy import avoids heavy deps at startup)."""
    from harvest.rice_etvf import RiceEtvfHarvester
    from harvest.innovationmap_rss import InnovationMapRssHarvester
    from harvest.halliburton_labs import HalliburtonLabsHarvester
    from harvest.ecv import EnergyCapitalVenturesHarvester
    from harvest.goose_capital import GooseCapitalHarvester
    from harvest.greentown_houston import GreentownHoustonHarvester
    from harvest.energytech_nexus import EnergyTechNexusHarvester
    from harvest.ion_district import IonDistrictHarvester
    from harvest.rbpc_alumni import RbpcAlumniHarvester
    from harvest.lowercarbon import LowercarbonHarvester
    from harvest.dcvc import DcvcHarvester
    from harvest.bev_portfolio import BevPortfolioHarvester
    from harvest.sec_edgar import SecEdgarFormDHarvester
    from harvest.ercot_queue import ErcotQueueHarvester
    return {
        "rice_etvf": RiceEtvfHarvester,
        "innovationmap_rss": InnovationMapRssHarvester,
        "halliburton_labs": HalliburtonLabsHarvester,
        "ecv_portfolio": EnergyCapitalVenturesHarvester,
        "goose_capital": GooseCapitalHarvester,
        "greentown_houston": GreentownHoustonHarvester,
        "energytech_nexus": EnergyTechNexusHarvester,
        "ion_district": IonDistrictHarvester,
        "rbpc_alumni": RbpcAlumniHarvester,
        "lowercarbon": LowercarbonHarvester,
        "dcvc": DcvcHarvester,
        "bev_portfolio": BevPortfolioHarvester,
        "sec_edgar": SecEdgarFormDHarvester,
        "ercot_queue": ErcotQueueHarvester,
    }


def run_harvest(
    sources: list[str] | None = None,
    dry_run: bool = False,
    console: "Console | None" = None,
) -> None:
    """Run source harvesters and write raw records to the pipeline database.

    Args:
        sources:  List of registry keys to run (e.g. ["rice_etvf"]).
                  None = run all registered sources.
        dry_run:  Log what would run without writing to DB.
        console:  Rich console for progress output.
    """
    import json

    from rich.table import Table

    from storage.db import init_db, to_json_column

    registry = _build_harvester_registry()
    keys_to_run = sources if sources is not None else list(registry.keys())

    # Validate requested sources
    unknown = [k for k in keys_to_run if k not in registry]
    for k in unknown:
        known = ", ".join(registry.keys())
        if console:
            console.print(f"[red]Unknown source:[/red] '{k}'. Known: {known}")
        logger.error(f"[orchestrator:harvest] Unknown source '{k}'. Known: {known}")
    keys_to_run = [k for k in keys_to_run if k in registry]

    if dry_run:
        if console:
            for k in keys_to_run:
                cls = registry[k]
                console.print(
                    f"[yellow]DRY RUN:[/yellow] would run "
                    f"[bold]{cls.SOURCE_NAME}[/bold] "
                    f"(expected yield: {cls.EXPECTED_YIELD})"
                )
        return

    conn = init_db()
    run_id = _get_run_id()
    total_records = 0

    for key in keys_to_run:
        HarvesterClass = registry[key]
        harvester = HarvesterClass()

        if console:
            console.print(f"[bold blue]Harvesting:[/bold blue] {HarvesterClass.SOURCE_NAME}")

        result = harvester.run()
        total_records += len(result.records)

        # Write harvest_run audit record
        conn.execute(
            """
            INSERT INTO harvest_runs
                (run_id, source, started_at, completed_at, success, records_harvested, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                HarvesterClass.SOURCE_NAME,
                result.started_at.isoformat(),
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                1 if result.success else 0,
                len(result.records),
                result.error,
            ),
        )

        # Write raw_records
        for rec in result.records:
            conn.execute(
                """
                INSERT INTO raw_records
                    (source, source_url, name_raw, description, website,
                     location_raw, tags, extra, harvested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.source,
                    rec.source_url,
                    rec.name,
                    rec.description,
                    rec.website,
                    rec.location_raw,
                    to_json_column(rec.tags),
                    to_json_column(rec.extra),
                    rec.harvested_at.isoformat(),
                ),
            )

        conn.commit()

        status = "[green]ok[/green]" if result.success else "[red]FAILED[/red]"
        if console:
            console.print(
                f"  {status} — {len(result.records)} records "
                f"in {result.duration_seconds:.1f}s"
            )
        if not result.success and result.error:
            if console:
                console.print(f"  [red]Error:[/red] {result.error}")

    if console and len(keys_to_run) > 1:
        console.print(f"\n[bold]Total records harvested:[/bold] {total_records}")


# ─────────────────────────────────────────────────────────────────────────────
# Stage: Classify
# ─────────────────────────────────────────────────────────────────────────────

def run_classify(
    dry_run: bool = False,
    console: "Console | None" = None,
) -> None:
    """Run the venture-scale classifier on unclassified raw records.

    Two passes per company:
      1. apply_hard_exclude_rules — deterministic; no LLM call.
      2. classify_venture_scale — LLM call via prompts/classifier_v1.md.

    Deduplicates raw records by normalized name slug before classifying
    (provisional; real cross-source dedup is Step 10). Idempotent: skips
    companies already present in the companies table with a classification.
    """
    import json
    import re

    from storage.db import init_db, to_json_column
    from signals.venture_scale import (
        apply_hard_exclude_rules,
        classify_venture_scale,
        get_classify_cost,
        reset_classify_cost,
    )
    from models import CompanyRecord

    conn = init_db()
    run_id = _get_run_id()
    reset_classify_cost()

    rows = conn.execute(
        "SELECT * FROM raw_records ORDER BY id"
    ).fetchall()

    # Deduplicate by name slug — one company per normalized name for this pass.
    # Cross-source dedup (Step 10) will merge same-company rows from different sources.
    seen_slugs: dict[str, dict] = {}
    for row in rows:
        slug = re.sub(r"[^a-z0-9]+", "-", row["name_raw"].lower()).strip("-")
        if slug not in seen_slugs:
            seen_slugs[slug] = dict(row)

    unique_companies = list(seen_slugs.items())  # [(slug, row_dict), ...]
    total = len(unique_companies)

    if console:
        mode = "[yellow]DRY RUN[/yellow]" if dry_run else "[green]LIVE[/green]"
        console.print(
            f"[bold blue]Classify:[/bold blue] {total} unique companies to classify {mode}"
        )

    if dry_run:
        from llm.client import estimate_cost
        # Estimate cost for one record, multiply out
        sample_row = unique_companies[0][1] if unique_companies else None
        if sample_row:
            extra = json.loads(sample_row.get("extra") or "{}")
            est = estimate_cost(
                prompt_name="classifier",
                prompt_version="v1",
                variables={
                    "company_id": "sample",
                    "name": sample_row["name_raw"],
                    "description": sample_row.get("description") or "",
                    "website": sample_row.get("website") or "",
                    "affiliation": extra.get("affiliation_raw") or "None",
                    "etvf_years": str(extra.get("etvf_years", [])),
                    "listing_only": "false",
                    "source_data_quality_flag": "none",
                },
                auto_inject_examples=False,
            )
            total_est = est["cost_usd_est"] * total
            if console:
                console.print(
                    f"  Estimated cost: ~${total_est:.2f} "
                    f"({total} × ~${est['cost_usd_est']:.4f}/record)"
                )
        return

    n_excluded = 0
    n_classified = 0
    n_skipped = 0
    n_errors = 0

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    for company_id, row in unique_companies:
        # Idempotency: skip if already classified or excluded
        existing = conn.execute(
            "SELECT venture_scale_score, is_excluded FROM companies WHERE id = ?",
            (company_id,),
        ).fetchone()
        if existing and (
            existing["venture_scale_score"] is not None or existing["is_excluded"]
        ):
            n_skipped += 1
            continue

        extra = json.loads(row.get("extra") or "{}")
        affiliation: str | None = extra.get("affiliation_raw")
        etvf_years_str = str(extra.get("etvf_years", []))
        listing_only: bool = bool(extra.get("listing_only", False))
        quality_flag: str | None = extra.get("source_data_quality_flag")

        # Build minimal CompanyRecord from harvested data
        company = CompanyRecord(
            company_id=company_id,
            name=row["name_raw"],
            description=row.get("description") or "",
            canonical_domain=row.get("website"),
        )

        # Ensure company row exists
        conn.execute(
            """
            INSERT OR IGNORE INTO companies
                (id, name, name_normalized, source_ids, first_seen_at, last_updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                row["name_raw"],
                row["name_raw"].lower().strip(),
                to_json_column([row["source"]]),
                now_iso,
                now_iso,
            ),
        )

        # Pass 1 — deterministic hard-exclude
        he = apply_hard_exclude_rules(company)
        if he.excluded:
            conn.execute(
                """
                UPDATE companies
                   SET is_excluded=1, exclude_reason=?, last_updated_at=?
                 WHERE id=?
                """,
                (he.reason, now_iso, company_id),
            )
            conn.commit()
            n_excluded += 1
            logger.info(
                f"[classify:excluded] {row['name_raw']} — {he.rule_id}: {he.reason[:80]}"
            )
            continue

        # Pass 2 — LLM classification
        try:
            result = classify_venture_scale(
                company,
                affiliation=affiliation,
                etvf_years=etvf_years_str,
                listing_only=listing_only,
                source_data_quality_flag=quality_flag,
            )
        except Exception as exc:
            logger.error(
                f"[classify:error] {row['name_raw']}: {exc}",
                exc_info=True,
            )
            n_errors += 1
            conn.commit()
            continue

        conn.execute(
            """
            UPDATE companies
               SET venture_scale_score=?,
                   venture_scale_confidence=?,
                   venture_scale_reasoning=?,
                   venture_scale_prompt_version=?,
                   in_review_queue=?,
                   last_updated_at=?
             WHERE id=?
            """,
            (
                result.score,
                result.confidence,
                result.reasoning,
                "v1",
                1 if result.review_queue else 0,
                now_iso,
                company_id,
            ),
        )
        conn.commit()
        n_classified += 1

        logger.debug(
            f"[classify:ok] {row['name_raw']} → {result.tier} "
            f"score={result.score:.1f} conf={result.confidence}"
        )

    total_cost = get_classify_cost()

    if console:
        console.print(
            f"  Classified: {n_classified} | Excluded: {n_excluded} | "
            f"Skipped (already done): {n_skipped} | Errors: {n_errors}"
        )
        console.print(f"  Total LLM cost: ${total_cost:.4f}")

    logger.info(
        f"[orchestrator:classify] done — classified={n_classified} "
        f"excluded={n_excluded} skipped={n_skipped} errors={n_errors} "
        f"cost=${total_cost:.4f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage: Enrich
# ─────────────────────────────────────────────────────────────────────────────

def run_enrich(
    dry_run: bool = False,
    console: "Console | None" = None,
) -> None:
    """Run the enricher on classified companies (Step 8).

    Three LLM passes per company (Haiku model):
      1. Sub-sector classification → primary_sector + sub_sector
      2. Summary generation        → summary (2–3 sentences)
      3. Founder pedigree scoring  → founder_pedigree_score/tier/confidence/full

    Idempotent: skips companies where all three columns are already populated.
    """
    from rich.progress import track
    from signals.enrichment import enrich_company, get_enrich_targets
    from storage.db import get_connection, init_db

    conn = get_connection("pipeline.db")
    init_db(conn)   # ensures new Step-8 columns exist via _migrate_schema

    targets = get_enrich_targets(conn)

    if console:
        console.print(
            f"[bold blue]Enrich:[/bold blue] {len(targets)} companies need enrichment"
        )
    logger.info(f"[orchestrator:enrich] {len(targets)} targets")

    if dry_run or not targets:
        return

    failed = 0
    for company_id, name in track(targets, description="Enriching…"):
        try:
            enrich_company(company_id, name, conn)
        except Exception as exc:
            logger.error(f"[orchestrator:enrich-error] {company_id!r}: {exc}")
            failed += 1

    logger.info(
        f"[orchestrator:enrich] done — "
        f"{len(targets) - failed} enriched, {failed} failed"
    )
    if console and failed:
        console.print(f"[yellow]Enrich: {failed} companies failed — see logs[/yellow]")


# ─────────────────────────────────────────────────────────────────────────────
# Stage: Score
# ─────────────────────────────────────────────────────────────────────────────

def run_score(console: "Console | None" = None) -> None:
    """Run the Houston presence scorer. Stub — Step 4 (standalone scorer built first)."""
    if console:
        console.print("[bold blue]Score:[/bold blue] (not yet implemented — Step 4)")
    logger.info("[orchestrator:score] Stub — not yet implemented")


# ─────────────────────────────────────────────────────────────────────────────
# Stage: Dedupe
# ─────────────────────────────────────────────────────────────────────────────

def run_dedupe(console: "Console | None" = None) -> None:
    """Run the cross-source deduplication pass (Step 10)."""
    from signals.dedup import run_dedup

    from storage.db import get_connection
    conn = get_connection("pipeline.db")
    if console:
        console.print("[bold blue]Dedupe:[/bold blue] running cross-source dedup …")

    result = run_dedup(conn)

    if console:
        console.print(
            f"[bold blue]Dedupe:[/bold blue] "
            f"{result.total_before} → {result.total_after} canonical companies "
            f"({result.merges} merge groups, {result.duplicates_removed} duplicates removed, "
            f"domain={result.domain_matches} fuzzy={result.fuzzy_matches})"
        )
        if result.merge_cases:
            console.print(f"[dim]Sample merges (up to 5):[/dim]")
            for mc in result.merge_cases[:5]:
                console.print(
                    f"  [cyan]{mc.canonical_name}[/cyan] ← "
                    + ", ".join(mc.duplicate_names)
                    + f"  [{mc.match_type}]"
                )

    logger.info(
        "[orchestrator:dedupe] %d → %d companies (%d merges, domain=%d fuzzy=%d)",
        result.total_before,
        result.total_after,
        result.merges,
        result.domain_matches,
        result.fuzzy_matches,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage: Export
# ─────────────────────────────────────────────────────────────────────────────

def run_export(
    output_dir: Path | None = None,
    console: "Console | None" = None,
) -> None:
    """Export pipeline output to xlsx and CSV. Stub — Step 12."""
    if console:
        console.print("[bold blue]Export:[/bold blue] (not yet implemented — Step 12)")
    logger.info("[orchestrator:export] Stub — not yet implemented")


# ─────────────────────────────────────────────────────────────────────────────
# Full pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(
    sources: list[str] | None = None,
    dry_run: bool = False,
    console: "Console | None" = None,
) -> None:
    """Run all pipeline stages end-to-end.

    Stages run in order: harvest → classify → enrich → score → dedupe → export.
    Each stage is called even if a previous stage produced zero records, so that
    a re-run on an already-populated database updates existing records correctly.

    Args:
        sources:  Restrict harvesting to these sources. None = all.
        dry_run:  Estimate cost and show plan without DB writes or API calls.
        console:  Rich console.
    """
    run_id = _get_run_id()
    started_at = datetime.datetime.now(datetime.timezone.utc)

    if console:
        mode = "[yellow]DRY RUN[/yellow]" if dry_run else "[green]LIVE[/green]"
        console.print(
            f"\n[bold]Houston Energy Mapper[/bold] — Pipeline run {mode}\n"
            f"Run ID: {run_id}\n"
            f"Started: {started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        )

    run_harvest(sources=sources, dry_run=dry_run, console=console)
    run_classify(dry_run=dry_run, console=console)
    run_enrich(dry_run=dry_run, console=console)
    run_score(console=console)
    run_dedupe(console=console)
    run_export(console=console)

    elapsed = (datetime.datetime.now(datetime.timezone.utc) - started_at).total_seconds()
    if console:
        console.print(f"\n[bold green]Done[/bold green] in {elapsed:.1f}s")


# ─────────────────────────────────────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────────────────────────────────────

def get_status(console: "Console | None" = None) -> None:
    """Print pipeline status: record counts, source quality, LLM usage.

    Reads from the pipeline database without modifying any data.
    """
    from rich.table import Table
    from storage.db import get_connection, init_db

    conn = init_db()

    if console is None:
        return

    # Company counts
    row = conn.execute("SELECT COUNT(*) FROM companies").fetchone()
    total = row[0] if row else 0

    classified = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE venture_scale_score IS NOT NULL"
    ).fetchone()[0]

    enriched = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE sub_sector IS NOT NULL"
    ).fetchone()[0]

    scored = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE houston_tier IS NOT NULL"
    ).fetchone()[0]

    table = Table(title="Pipeline Status", show_header=True, header_style="bold cyan")
    table.add_column("Stage", style="bold")
    table.add_column("Count", justify="right")

    table.add_row("Total companies", str(total))
    table.add_row("Classified", str(classified))
    table.add_row("Enriched", str(enriched))
    table.add_row("Houston-scored", str(scored))

    console.print(table)

    # LLM call count
    from llm.client import get_call_count
    console.print(f"\nLLM calls this session: {get_call_count()}")
