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

def run_harvest(
    sources: list[str] | None = None,
    dry_run: bool = False,
    console: "Console | None" = None,
) -> None:
    """Run source harvesters and write raw records to the pipeline database.

    Args:
        sources:  List of SOURCE_NAME values to run. None = run all registered sources.
        dry_run:  Log what would run without writing to DB.
        console:  Rich console for progress output.

    Implementation note (Step 5+): This stub will be replaced with a loop over
    the registered harvester registry (a dict mapping source names → harvester classes).
    The orchestrator handles Playwright browser initialization here for all
    harvesters that declare requires_browser=True.
    """
    # Stub — full implementation in Step 5
    if console:
        console.print("[bold blue]Harvest:[/bold blue] (not yet implemented — Step 5)")
    logger.info("[orchestrator:harvest] Stub — not yet implemented")


# ─────────────────────────────────────────────────────────────────────────────
# Stage: Classify
# ─────────────────────────────────────────────────────────────────────────────

def run_classify(
    dry_run: bool = False,
    console: "Console | None" = None,
) -> None:
    """Run the venture-scale classifier on unclassified raw records.

    Stub — full implementation in Step 6.
    """
    if console:
        console.print("[bold blue]Classify:[/bold blue] (not yet implemented — Step 6)")
    logger.info("[orchestrator:classify] Stub — not yet implemented")


# ─────────────────────────────────────────────────────────────────────────────
# Stage: Enrich
# ─────────────────────────────────────────────────────────────────────────────

def run_enrich(
    dry_run: bool = False,
    console: "Console | None" = None,
) -> None:
    """Run the enricher on classified companies. Stub — Step 8."""
    if console:
        console.print("[bold blue]Enrich:[/bold blue] (not yet implemented — Step 8)")
    logger.info("[orchestrator:enrich] Stub — not yet implemented")


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
    """Run the deduplication pass. Stub — Step 10."""
    if console:
        console.print("[bold blue]Dedupe:[/bold blue] (not yet implemented — Step 10)")
    logger.info("[orchestrator:dedupe] Stub — not yet implemented")


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
