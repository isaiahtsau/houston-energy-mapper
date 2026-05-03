"""
Tests for storage/relationship_graph.py.

All tests use in-memory SQLite. Zero live DB or network calls.

Tests:
  1. init_relationship_graph_db: both tables and indexes created
  2. insert_founder_edge: round-trips correctly; duplicate ignored
  3. insert_investor_edge: round-trips correctly; duplicate ignored
  4. get_founder_companies: returns correct rows for a given founder
  5. get_investor_companies: returns correct rows for a given investor
"""
from __future__ import annotations

import sqlite3

import pytest

from storage.relationship_graph import (
    get_founder_companies,
    get_investor_companies,
    init_relationship_graph_db,
    insert_founder_edge,
    insert_investor_edge,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_relationship_graph_db(c)
    return c


# ── Test 1: schema init ───────────────────────────────────────────────────────

def test_schema_creates_tables(conn: sqlite3.Connection) -> None:
    """Both tables exist after init."""
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "founder_company_edges" in tables
    assert "investor_company_edges" in tables


def test_schema_creates_indexes(conn: sqlite3.Connection) -> None:
    """Indexes on founder_name and investor_name are created."""
    indexes = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "idx_founder_name" in indexes
    assert "idx_investor_name" in indexes


def test_schema_is_idempotent(conn: sqlite3.Connection) -> None:
    """Calling init twice doesn't raise."""
    init_relationship_graph_db(conn)  # second call
    count = conn.execute("SELECT COUNT(*) FROM founder_company_edges").fetchone()[0]
    assert count == 0


# ── Test 2: founder edge insert ───────────────────────────────────────────────

def test_insert_founder_edge(conn: sqlite3.Connection) -> None:
    """Inserted founder edge is retrievable with correct fields."""
    insert_founder_edge(
        conn,
        founder_name="Jane Doe",
        company_id="kanin-energy",
        source="enriched_description",
        evidence="Jane Doe co-founded Kanin Energy in 2019.",
        confidence=0.9,
        first_seen_at="2025-05-01T00:00:00+00:00",
    )
    rows = get_founder_companies(conn, "jane doe")
    assert len(rows) == 1
    assert rows[0]["company_id"] == "kanin-energy"
    assert rows[0]["confidence"] == 0.9
    assert rows[0]["source"] == "enriched_description"


def test_insert_founder_edge_duplicate_ignored(conn: sqlite3.Connection) -> None:
    """Inserting same (founder_name, company_id) twice keeps only one row."""
    insert_founder_edge(conn, "Jane Doe", "kanin-energy", "enriched_description")
    insert_founder_edge(conn, "Jane Doe", "kanin-energy", "be_fellows")
    rows = get_founder_companies(conn, "jane doe")
    assert len(rows) == 1  # second insert ignored


def test_founder_name_normalized(conn: sqlite3.Connection) -> None:
    """founder_name is stored and looked up case-insensitively (lowercased)."""
    insert_founder_edge(conn, "  ALICE SMITH  ", "aeromine", "manual")
    rows = get_founder_companies(conn, "alice smith")
    assert len(rows) == 1


# ── Test 3: investor edge insert ─────────────────────────────────────────────

def test_insert_investor_edge(conn: sqlite3.Connection) -> None:
    """Inserted investor edge is retrievable with correct fields."""
    insert_investor_edge(
        conn,
        investor_name="Energy Capital Ventures",
        company_id="syzygy-plasmonics",
        source="ecv_portfolio",
        evidence="https://ecv.vc/portfolio/syzygy",
        round_size=5_000_000.0,
        first_seen_at="2025-04-15T00:00:00+00:00",
    )
    rows = get_investor_companies(conn, "Energy Capital Ventures")
    assert len(rows) == 1
    assert rows[0]["company_id"] == "syzygy-plasmonics"
    assert rows[0]["round_size"] == 5_000_000.0


def test_insert_investor_edge_duplicate_ignored(conn: sqlite3.Connection) -> None:
    """Inserting same (investor_name, company_id) twice keeps only one row."""
    insert_investor_edge(conn, "Greentown Houston", "ardent", "greentown_portfolio")
    insert_investor_edge(conn, "Greentown Houston", "ardent", "sec_edgar_form_d")
    rows = get_investor_companies(conn, "Greentown Houston")
    assert len(rows) == 1


# ── Test 4: multiple edges per founder ────────────────────────────────────────

def test_founder_multiple_companies(conn: sqlite3.Connection) -> None:
    """A founder can be linked to multiple companies."""
    insert_founder_edge(conn, "Bob Jones", "company-a", "be_fellows")
    insert_founder_edge(conn, "Bob Jones", "company-b", "be_fellows")
    rows = get_founder_companies(conn, "bob jones")
    assert len(rows) == 2
    company_ids = {r["company_id"] for r in rows}
    assert company_ids == {"company-a", "company-b"}
