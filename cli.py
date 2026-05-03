"""
Houston Energy Mapper — CLI entry point.

Provides the `hem` command (or `python cli.py`) for running the pipeline
and its individual stages.

Commands:
  harvest   Run one or more source harvesters, write raw records to DB
  classify  Run venture-scale classifier on unclassified staged records
  enrich    Run the enricher on classified companies
  score     Run the Houston presence scorer
  dedupe    Run the deduplication pass
  export    Export deliverable to xlsx and CSV
  run       Run the full pipeline end-to-end (harvest → classify → enrich → score → dedupe → export)
  status    Show pipeline status: record counts, source quality, LLM call totals

Examples:
  python cli.py run --all
  python cli.py run --all --dry-run
  python cli.py harvest --sources rice_etvf,halliburton_labs
  python cli.py run --all --max-llm-calls 50
  python cli.py export

Install as `hem` command:
  pip install -e .
  hem run --all
"""
from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="hem",
    help="Houston Energy Mapper: AI-powered venture-scale startup mapping pipeline.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()


@app.command()
def harvest(
    sources: Optional[str] = typer.Option(
        None,
        "--sources", "-s",
        help="Comma-separated source names to run (e.g. rice_etvf,halliburton_labs). "
             "Omit to run all configured sources.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show which sources would be harvested and expected yield without writing to DB.",
    ),
) -> None:
    """Run source harvesters and write raw company records to the database."""
    from pipeline.orchestrator import run_harvest

    source_list = [s.strip() for s in sources.split(",")] if sources else None
    run_harvest(sources=source_list, dry_run=dry_run, console=console)


@app.command()
def classify(
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Estimate LLM cost without making API calls.",
    ),
    max_llm_calls: Optional[int] = typer.Option(
        None, "--max-llm-calls",
        help="Hard cap on API calls (circuit breaker). 0 = unlimited.",
    ),
) -> None:
    """Run the venture-scale classifier on unclassified staged records."""
    from pipeline.orchestrator import run_classify
    from config.settings import settings

    if max_llm_calls is not None:
        settings.max_llm_calls = max_llm_calls if max_llm_calls > 0 else None
    run_classify(dry_run=dry_run, console=console)


@app.command()
def enrich(
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_llm_calls: Optional[int] = typer.Option(None, "--max-llm-calls"),
) -> None:
    """Run the enricher on classified companies (founder pedigree, sub-sector, summary)."""
    from pipeline.orchestrator import run_enrich
    from config.settings import settings

    if max_llm_calls is not None:
        settings.max_llm_calls = max_llm_calls if max_llm_calls > 0 else None
    run_enrich(dry_run=dry_run, console=console)


@app.command()
def score() -> None:
    """Run the Houston presence scorer on enriched companies."""
    from pipeline.orchestrator import run_score
    run_score(console=console)


@app.command()
def dedupe() -> None:
    """Run the deduplication pass (fuzzy name matching + canonical ID promotion)."""
    from pipeline.orchestrator import run_dedupe
    run_dedupe(console=console)


@app.command()
def export(
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir",
        help="Override the default output directory (data/exports/).",
    ),
) -> None:
    """Export the pipeline output to xlsx and CSV deliverables."""
    from pipeline.orchestrator import run_export
    from pathlib import Path

    out = Path(output_dir) if output_dir else None
    run_export(output_dir=out, console=console)


@app.command()
def run(
    all: bool = typer.Option(False, "--all", help="Run the full pipeline end-to-end."),
    sources: Optional[str] = typer.Option(
        None, "--sources", "-s",
        help="Restrict harvest to specific sources (comma-separated).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Estimate costs and show pipeline plan without writing to DB or calling LLMs.",
    ),
    max_llm_calls: Optional[int] = typer.Option(
        None, "--max-llm-calls",
        help="Hard cap on total LLM API calls across all stages. 0 = unlimited.",
    ),
) -> None:
    """Run the full pipeline: harvest → classify → enrich → score → dedupe → export."""
    from pipeline.orchestrator import run_pipeline
    from config.settings import settings

    if not all and not sources:
        console.print(
            "[yellow]Tip:[/yellow] Pass [bold]--all[/bold] to run the full pipeline, "
            "or [bold]--sources[/bold] to restrict harvesting.",
        )
        raise typer.Exit(1)

    if max_llm_calls is not None:
        settings.max_llm_calls = max_llm_calls if max_llm_calls > 0 else None

    source_list = [s.strip() for s in sources.split(",")] if sources else None
    run_pipeline(sources=source_list, dry_run=dry_run, console=console)


@app.command(name="coalesce-domains")
def coalesce_domains(
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Compute resolutions and report without writing to DB.",
    ),
    all_records: bool = typer.Option(
        False, "--all",
        help="Run against all companies, not just VS+BORDERLINE (score >= 4.0).",
    ),
) -> None:
    """Propagate raw_records.website → companies.canonical_domain via name join.

    Fixes the gap where dedup never populated canonical_domain from harvested
    website fields. Idempotent — only updates NULL/empty canonical_domain values.
    """
    import sqlite3
    from config.settings import settings
    from enrich.canonical_domain_coalesce import coalesce_domains as _coalesce

    conn = sqlite3.connect(str(settings.db_dir / "pipeline.db"))
    conn.row_factory = sqlite3.Row

    scope_vs_bl = not all_records
    console.print(
        f"[bold]Coalescing canonical domains[/bold] "
        f"(scope={'VS+BL only' if scope_vs_bl else 'all'}, dry_run={dry_run})"
    )

    summary = _coalesce(conn, dry_run=dry_run, scope_vs_bl_only=scope_vs_bl)

    from rich.table import Table

    console.print(f"\n[bold]Results:[/bold]")
    console.print(f"  Missing before: {summary.total_missing_before}")
    console.print(f"  Resolved:       [green]{summary.resolved}[/green]")
    console.print(f"  Still NULL:     [yellow]{summary.still_null}[/yellow]")
    if dry_run:
        console.print("  [dim](dry run — no changes written)[/dim]")

    if summary.by_source:
        t = Table(title="Resolutions by Source", show_lines=False)
        t.add_column("Source", style="cyan")
        t.add_column("Resolved", justify="right")
        for src, count in sorted(summary.by_source.items(), key=lambda x: -x[1]):
            t.add_row(src, str(count))
        console.print(t)

    if summary.samples:
        console.print(f"\n[bold]Sample resolutions ({min(10, len(summary.samples))}):[/bold]")
        for s in summary.samples[:10]:
            console.print(
                f"  [green]{s['company']}[/green] → {s['url']}  "
                f"[dim](via {s['source']}, {s['n_candidates']} candidate rows)[/dim]"
            )


@app.command()
def status() -> None:
    """Show pipeline status: record counts, source quality, and LLM usage."""
    from pipeline.orchestrator import get_status
    get_status(console=console)


if __name__ == "__main__":
    app()
