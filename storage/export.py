"""
Export pipeline output to xlsx and CSV deliverables.

Reads the companies table from the pipeline database, applies column ordering
and formatting, and writes:
  - data/exports/houston_energy_ventures.xlsx  (primary deliverable)
  - data/exports/houston_energy_ventures.csv   (diff-friendly mirror)
  - data/exports/run_log_YYYYMMDD.md           (per-run audit report)

Column ordering in the spreadsheet follows the rubric evaluation categories:
  1. Identity (name, website, sub_sector)
  2. Houston presence (tier, points, signal_trace)
  3. Venture scale (score, confidence, reasoning)
  4. Founders (names, pedigree)
  5. Provenance (sources, first_seen_at)
  6. Review flags (in_review_queue, human_validated)

Status: STUB — full implementation in Step 12.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Column display names for the xlsx header row
_COLUMN_ORDER = [
    # Identity
    "name", "website", "canonical_domain", "sub_sector", "summary",
    # Houston presence
    "houston_tier", "houston_points", "houston_signals",
    # Venture scale
    "venture_scale_score", "venture_scale_confidence", "venture_scale_reasoning",
    # Founders
    "founder_names", "founder_pedigree",
    # Provenance
    "source_ids", "first_seen_at",
    # Flags
    "in_review_queue", "human_validated",
]


def export_to_xlsx(output_dir: Path | None = None) -> Path:
    """Export the companies table to an xlsx file.

    Args:
        output_dir: Directory to write the file. Defaults to settings.exports_dir.

    Returns:
        Path to the written xlsx file.

    Note:
        STUB — raises NotImplementedError until Step 12.
    """
    raise NotImplementedError("export_to_xlsx — implemented in Step 12")


def export_to_csv(output_dir: Path | None = None) -> Path:
    """Export the companies table to a CSV file.

    Args:
        output_dir: Directory to write the file. Defaults to settings.exports_dir.

    Returns:
        Path to the written CSV file.

    Note:
        STUB — raises NotImplementedError until Step 12.
    """
    raise NotImplementedError("export_to_csv — implemented in Step 12")


def write_run_log(
    run_id: str,
    harvest_results: list,
    llm_call_count: int,
    total_cost_usd: float,
    output_dir: Path | None = None,
) -> Path:
    """Write a Markdown run log for the current pipeline execution.

    Includes per-source success/failure, record counts, LLM call totals,
    and estimated cost. Written to data/exports/run_log_YYYYMMDD.md.

    Note:
        STUB — raises NotImplementedError until Step 12.
    """
    raise NotImplementedError("write_run_log — implemented in Step 12")
