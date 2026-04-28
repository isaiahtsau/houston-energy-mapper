"""
Flywheel component: Source quality tracker.

Records per-source pass rates after the venture-scale classification stage.
A "pass" is a company that clears the venture_scale_high_threshold.

Pass rate is used by the orchestrator to:
  - Surface low-quality sources in the run log (high scrape cost, low signal)
  - Optionally deprioritize low-quality sources on subsequent runs

Database: data/db/source_quality.db

Status: STUB — interface defined, implementation in Step 11.
"""
from __future__ import annotations


def record_source_run(
    source_name: str,
    total_candidates: int,
    passed_candidates: int,
    run_id: str,
) -> None:
    """Record a single harvester run's pass rate.

    Note: STUB — no-op until Step 11.
    """
    pass


def get_source_quality_report() -> list[dict]:
    """Return a per-source quality summary sorted by pass rate descending.

    Note: STUB — returns [] until Step 11.
    """
    return []
