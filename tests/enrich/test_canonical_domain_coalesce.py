"""
Tests for enrich/canonical_domain_coalesce.py.

All tests use in-memory SQLite. Zero live network or API calls.

Tests:
  1. single_source_match   — company with one raw_record website gets resolved
  2. multi_source_coalesce — picks highest-priority source when multiple rows exist
  3. no_match_case         — company with no raw_records website stays NULL
  4. propagation_to_db     — canonical_domain is actually written to companies table
  5. idempotent            — re-run leaves already-populated canonical_domain unchanged
  6. dry_run               — dry_run=True computes result but does not write
"""
from __future__ import annotations

import sqlite3

import pytest

from enrich.canonical_domain_coalesce import CoalesceSummary, coalesce_domains


# ── Fixture ────────────────────────────────────────────────────────────────────

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE companies (
            id TEXT PRIMARY KEY,
            name TEXT,
            venture_scale_score REAL DEFAULT 7.0,
            sub_sector TEXT,
            enrichment_status TEXT DEFAULT 'enriched',
            is_duplicate INTEGER DEFAULT 0,
            is_excluded  INTEGER DEFAULT 0,
            canonical_domain TEXT,
            last_updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE raw_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT,
            name_raw TEXT,
            source TEXT,
            website TEXT,
            description TEXT,
            harvested_at TEXT DEFAULT '2025-01-01'
        )
    """)
    conn.commit()
    return conn


def _add_company(conn, cid, name, score=7.5, domain=None):
    conn.execute(
        "INSERT INTO companies (id, name, venture_scale_score, canonical_domain) VALUES (?,?,?,?)",
        (cid, name, score, domain),
    )
    conn.commit()


def _add_raw(conn, name_raw, source, website=None):
    conn.execute(
        "INSERT INTO raw_records (name_raw, source, website) VALUES (?,?,?)",
        (name_raw, source, website),
    )
    conn.commit()


# ── Test 1: single source match ────────────────────────────────────────────────

def test_single_source_match_resolves() -> None:
    """Company with one raw_record website gets canonical_domain set."""
    conn = _make_conn()
    _add_company(conn, "co-1", "Alpha Corp")
    _add_raw(conn, "Alpha Corp", "Greentown Houston", "https://alphacorp.io")

    summary = coalesce_domains(conn, dry_run=False, scope_vs_bl_only=False)

    assert summary.resolved == 1
    row = conn.execute("SELECT canonical_domain FROM companies WHERE id='co-1'").fetchone()
    assert row["canonical_domain"] == "https://alphacorp.io"


# ── Test 2: multi-source coalesce picks highest priority ───────────────────────

def test_multi_source_picks_higher_priority() -> None:
    """When multiple sources have websites, the highest-priority source wins."""
    conn = _make_conn()
    _add_company(conn, "co-2", "Beta Inc")
    # RBPC Alumni is lower priority than Greentown Houston
    _add_raw(conn, "Beta Inc", "RBPC Alumni", "http://beta-rbpc.com")
    _add_raw(conn, "Beta Inc", "Greentown Houston", "https://betainc.com")

    summary = coalesce_domains(conn, dry_run=False, scope_vs_bl_only=False)

    assert summary.resolved == 1
    row = conn.execute("SELECT canonical_domain FROM companies WHERE id='co-2'").fetchone()
    # Greentown Houston is higher priority
    assert row["canonical_domain"] == "https://betainc.com"
    assert summary.by_source.get("Greentown Houston", 0) == 1


# ── Test 3: no raw_record website — stays NULL ────────────────────────────────

def test_no_website_in_raw_records_stays_null() -> None:
    """Company with no raw_record website is counted in still_null."""
    conn = _make_conn()
    _add_company(conn, "co-3", "Gamma LLC")
    _add_raw(conn, "Gamma LLC", "SEC EDGAR Form D", None)   # NULL website

    summary = coalesce_domains(conn, dry_run=False, scope_vs_bl_only=False)

    assert summary.resolved == 0
    assert summary.still_null == 1
    row = conn.execute("SELECT canonical_domain FROM companies WHERE id='co-3'").fetchone()
    assert not row["canonical_domain"]


# ── Test 4: DB propagation ─────────────────────────────────────────────────────

def test_canonical_domain_written_to_db() -> None:
    """canonical_domain and last_updated_at are written to the companies row."""
    conn = _make_conn()
    _add_company(conn, "co-4", "Delta Energy")
    _add_raw(conn, "Delta Energy", "Halliburton Labs", "https://deltaenergy.com")

    coalesce_domains(conn, dry_run=False, scope_vs_bl_only=False)

    row = conn.execute(
        "SELECT canonical_domain, last_updated_at FROM companies WHERE id='co-4'"
    ).fetchone()
    assert row["canonical_domain"] == "https://deltaenergy.com"
    assert row["last_updated_at"] is not None


# ── Test 5: idempotent ─────────────────────────────────────────────────────────

def test_idempotent_does_not_overwrite_existing() -> None:
    """Re-running coalesce leaves already-set canonical_domain untouched."""
    conn = _make_conn()
    _add_company(conn, "co-5", "Epsilon Co", domain="https://already-set.com")
    _add_raw(conn, "Epsilon Co", "Greentown Houston", "https://different.com")

    summary = coalesce_domains(conn, dry_run=False, scope_vs_bl_only=False)

    assert summary.resolved == 0  # already set, not in scope
    row = conn.execute("SELECT canonical_domain FROM companies WHERE id='co-5'").fetchone()
    assert row["canonical_domain"] == "https://already-set.com"  # unchanged


# ── Test 6: dry_run ────────────────────────────────────────────────────────────

def test_dry_run_does_not_write() -> None:
    """dry_run=True reports resolved count but does not write to DB."""
    conn = _make_conn()
    _add_company(conn, "co-6", "Zeta Power")
    _add_raw(conn, "Zeta Power", "Rice Energy Tech Venture Forum (ETVF)", "http://zetapower.com")

    summary = coalesce_domains(conn, dry_run=True, scope_vs_bl_only=False)

    assert summary.resolved == 1
    assert summary.dry_run is True
    # DB should NOT have been updated
    row = conn.execute("SELECT canonical_domain FROM companies WHERE id='co-6'").fetchone()
    assert not row["canonical_domain"]
