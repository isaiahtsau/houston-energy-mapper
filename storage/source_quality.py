"""
Source quality tracking — schema and initialization.

Records per-source classification statistics after each pipeline run, enabling
long-term tracking of which sources produce the highest signal-to-noise ratio.

Database: data/db/source_quality.db

Table: source_run_stats — one row per (source_name, run_date) pair

Phase 2 work (not yet wired):
  - Wire classify_stage in pipeline/orchestrator.py to call record_source_run_stats()
    after the classification pass completes.
  - Wire enrich_stage to log enrichment hit rates per source.
  - Step 12 manual review produces manually_promoted/demoted counts — wire the
    review queue to call update_manual_review_counts() after each review session.
  - Surface low-quality sources in the run log: sources with pass_rate < 5% or
    false_positive_rate > 40% get a warning in the pipeline report.

Public API:
    init_source_quality_db(conn) -> None
    record_source_run_stats(conn, stats: SourceRunStats) -> None
    get_source_quality_report(conn) -> list[dict]
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


# ── Schema ─────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS source_run_stats (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               TEXT    NOT NULL,      -- UUID4 from pipeline run
    source_name          TEXT    NOT NULL,      -- e.g. 'Rice Energy Tech Venture Forum (ETVF)'
    run_date             TEXT    NOT NULL,       -- ISO 8601 date (YYYY-MM-DD)
    total_records        INTEGER NOT NULL DEFAULT 0,
    hard_excluded        INTEGER NOT NULL DEFAULT 0,  -- failed hard-exclude rules
    not_venture_scale    INTEGER NOT NULL DEFAULT 0,  -- tier = NOT_VENTURE_SCALE
    borderline           INTEGER NOT NULL DEFAULT 0,  -- tier = BORDERLINE
    venture_scale        INTEGER NOT NULL DEFAULT 0,  -- tier = VENTURE_SCALE
    manually_promoted    INTEGER NOT NULL DEFAULT 0,  -- BORDERLINE→VS after human review
    manually_demoted     INTEGER NOT NULL DEFAULT 0,  -- VS→NOT_VS after human review
    pass_rate            REAL,                        -- venture_scale / total_records
    false_positive_rate  REAL,                        -- manually_demoted / venture_scale
    false_negative_rate  REAL,                        -- manually_promoted / (borderline + not_vs)
    UNIQUE(run_id, source_name)
);

CREATE INDEX IF NOT EXISTS idx_source_run_source
    ON source_run_stats(source_name);

CREATE INDEX IF NOT EXISTS idx_source_run_date
    ON source_run_stats(run_date);
"""


# ── Data class ─────────────────────────────────────────────────────────────────

@dataclass
class SourceRunStats:
    """Stats for a single source in a single pipeline run."""
    run_id: str
    source_name: str
    run_date: str               # YYYY-MM-DD
    total_records: int = 0
    hard_excluded: int = 0
    not_venture_scale: int = 0
    borderline: int = 0
    venture_scale: int = 0
    manually_promoted: int = 0
    manually_demoted: int = 0

    @property
    def pass_rate(self) -> float | None:
        if self.total_records == 0:
            return None
        return self.venture_scale / self.total_records

    @property
    def false_positive_rate(self) -> float | None:
        if self.venture_scale == 0:
            return None
        return self.manually_demoted / self.venture_scale

    @property
    def false_negative_rate(self) -> float | None:
        denom = self.borderline + self.not_venture_scale
        if denom == 0:
            return None
        return self.manually_promoted / denom


# ── Init ───────────────────────────────────────────────────────────────────────

def init_source_quality_db(conn: sqlite3.Connection) -> None:
    """Create the source quality schema if it doesn't exist.

    Idempotent: safe to call on every pipeline run.

    Args:
        conn: SQLite connection to source_quality.db (or any in-memory DB
              for testing).
    """
    conn.executescript(_DDL)
    conn.commit()


# ── Writers ────────────────────────────────────────────────────────────────────

def record_source_run_stats(
    conn: sqlite3.Connection,
    stats: SourceRunStats,
) -> None:
    """Insert or replace a source run stats record.

    On conflict (same run_id + source_name), the row is replaced.

    Args:
        conn:  Connection to source_quality.db.
        stats: A SourceRunStats dataclass instance.
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO source_run_stats (
            run_id, source_name, run_date,
            total_records, hard_excluded, not_venture_scale,
            borderline, venture_scale,
            manually_promoted, manually_demoted,
            pass_rate, false_positive_rate, false_negative_rate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stats.run_id,
            stats.source_name,
            stats.run_date,
            stats.total_records,
            stats.hard_excluded,
            stats.not_venture_scale,
            stats.borderline,
            stats.venture_scale,
            stats.manually_promoted,
            stats.manually_demoted,
            stats.pass_rate,
            stats.false_positive_rate,
            stats.false_negative_rate,
        ),
    )
    conn.commit()


# ── Readers ────────────────────────────────────────────────────────────────────

def get_source_quality_report(conn: sqlite3.Connection) -> list[dict]:
    """Return the most recent stats row per source, sorted by pass_rate descending.

    Args:
        conn: Connection to source_quality.db.

    Returns:
        List of row dicts, one per source, ordered best → worst pass_rate.
        Empty list if no data has been recorded yet.
    """
    rows = conn.execute(
        """
        SELECT s.*
          FROM source_run_stats s
         INNER JOIN (
             SELECT source_name, MAX(run_date) AS latest
               FROM source_run_stats
              GROUP BY source_name
         ) latest ON s.source_name = latest.source_name
                  AND s.run_date = latest.latest
         ORDER BY pass_rate DESC NULLS LAST
        """
    ).fetchall()
    return [dict(r) for r in rows]
