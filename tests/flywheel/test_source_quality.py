"""
Tests for storage/source_quality.py.

All tests use in-memory SQLite. Zero live DB or network calls.

Tests:
  1. init_source_quality_db: table and indexes created; idempotent
  2. record_source_run_stats: inserts row and computes pass_rate/false_positive_rate
  3. get_source_quality_report: returns rows ordered by pass_rate descending
"""
from __future__ import annotations

import sqlite3

import pytest

from storage.source_quality import (
    SourceRunStats,
    get_source_quality_report,
    init_source_quality_db,
    record_source_run_stats,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_source_quality_db(c)
    return c


# ── Test 1: schema ────────────────────────────────────────────────────────────

def test_schema_creates_table(conn: sqlite3.Connection) -> None:
    """source_run_stats table exists after init."""
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "source_run_stats" in tables


def test_schema_idempotent(conn: sqlite3.Connection) -> None:
    """Calling init twice doesn't raise or duplicate anything."""
    init_source_quality_db(conn)
    count = conn.execute("SELECT COUNT(*) FROM source_run_stats").fetchone()[0]
    assert count == 0


# ── Test 2: record stats ──────────────────────────────────────────────────────

def test_record_source_run_stats_insert(conn: sqlite3.Connection) -> None:
    """Inserted stats are retrievable and pass_rate is correct."""
    stats = SourceRunStats(
        run_id="run-001",
        source_name="Rice Energy Tech Venture Forum (ETVF)",
        run_date="2025-05-01",
        total_records=50,
        hard_excluded=5,
        not_venture_scale=10,
        borderline=15,
        venture_scale=20,
        manually_promoted=2,
        manually_demoted=1,
    )
    record_source_run_stats(conn, stats)

    row = conn.execute(
        "SELECT * FROM source_run_stats WHERE run_id='run-001'"
    ).fetchone()
    assert row is not None
    assert row["total_records"] == 50
    assert row["venture_scale"] == 20
    assert abs(row["pass_rate"] - 20 / 50) < 1e-6
    assert abs(row["false_positive_rate"] - 1 / 20) < 1e-6
    assert abs(row["false_negative_rate"] - 2 / 25) < 1e-6


def test_record_source_run_stats_replace(conn: sqlite3.Connection) -> None:
    """Inserting same (run_id, source_name) replaces the existing row."""
    stats1 = SourceRunStats(
        run_id="run-001", source_name="SEC EDGAR Form D",
        run_date="2025-05-01", total_records=100, venture_scale=5,
    )
    stats2 = SourceRunStats(
        run_id="run-001", source_name="SEC EDGAR Form D",
        run_date="2025-05-01", total_records=100, venture_scale=10,
    )
    record_source_run_stats(conn, stats1)
    record_source_run_stats(conn, stats2)

    count = conn.execute("SELECT COUNT(*) FROM source_run_stats").fetchone()[0]
    assert count == 1
    row = conn.execute("SELECT venture_scale FROM source_run_stats").fetchone()
    assert row["venture_scale"] == 10  # replaced


# ── Test 3: quality report ────────────────────────────────────────────────────

def test_get_source_quality_report_ordered(conn: sqlite3.Connection) -> None:
    """Report is sorted pass_rate descending, most recent run per source."""
    for source, vs, total, run_date in [
        ("ETVF", 40, 50, "2025-05-01"),
        ("SEC EDGAR Form D", 5, 100, "2025-05-01"),
        ("Greentown Houston", 20, 40, "2025-05-01"),
    ]:
        record_source_run_stats(
            conn,
            SourceRunStats(
                run_id=f"run-{source[:4]}",
                source_name=source,
                run_date=run_date,
                total_records=total,
                venture_scale=vs,
            ),
        )

    report = get_source_quality_report(conn)
    assert len(report) == 3
    pass_rates = [r["pass_rate"] for r in report]
    # ETVF: 0.80, Greentown: 0.50, EDGAR: 0.05
    assert pass_rates[0] > pass_rates[1] > pass_rates[2]


def test_get_source_quality_report_latest_run_only(conn: sqlite3.Connection) -> None:
    """Report returns only the most recent run per source, not all runs."""
    for run_date, vs in [("2025-03-01", 5), ("2025-05-01", 20)]:
        record_source_run_stats(
            conn,
            SourceRunStats(
                run_id=f"run-{run_date}",
                source_name="Greentown Houston",
                run_date=run_date,
                total_records=40,
                venture_scale=vs,
            ),
        )

    report = get_source_quality_report(conn)
    assert len(report) == 1
    assert report[0]["venture_scale"] == 20  # latest run
