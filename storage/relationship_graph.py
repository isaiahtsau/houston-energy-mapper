"""
Relationship graph storage — schema and initialization.

Maintains a SQLite graph of founder and investor affiliations extracted from
enriched company records. Used to compute cross-company prior signals:
  - Founders who founded other Houston energy companies get a positive prior.
  - Investors already backing Houston energy companies signal legitimacy.

Database: data/db/relationship_graph.db

Tables:
  founder_company_edges  — links a named founder to a company they founded
  investor_company_edges — links a named investor/fund to a company they backed

Phase 2 work (not yet wired):
  - Extract founder names from enriched descriptions via LLM call
  - Populate from BE Fellows lookup (fellowship → founder → company)
  - Populate from ECV detail pages (founders field already harvested in extra JSON)
  - Populate investor edges from VC portfolio harvests (Greentown, ECV, DCVC, etc.)
  - Compute get_prior_signal() using graph traversal: if founder founded ≥1
    known VS company, boost current company's prior by +1.0; if investor backed
    ≥2 known VS companies, boost by +0.5.

Public API:
    init_relationship_graph_db(conn) -> None
    insert_founder_edge(conn, founder_name, company_id, source, evidence, confidence) -> None
    insert_investor_edge(conn, investor_name, company_id, source, evidence, round_size) -> None
    get_founder_companies(conn, founder_name) -> list[dict]
    get_investor_companies(conn, investor_name) -> list[dict]
"""
from __future__ import annotations

import sqlite3


# ── Schema ─────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS founder_company_edges (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    founder_name     TEXT    NOT NULL,       -- normalized: lowercase, stripped
    company_id       TEXT    NOT NULL,       -- FK → companies.id in pipeline.db
    source           TEXT    NOT NULL,       -- where this edge was extracted from
                                             -- e.g. 'enriched_description', 'be_fellows',
                                             --      'ecv_detail_page', 'manual'
    evidence         TEXT,                   -- the sentence or field that produced this edge
    confidence       REAL    DEFAULT 0.8,    -- 0.0–1.0; 1.0 = manually verified
    first_seen_at    TEXT    NOT NULL,       -- ISO 8601 UTC
    UNIQUE(founder_name, company_id)         -- one edge per founder–company pair
);

CREATE TABLE IF NOT EXISTS investor_company_edges (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    investor_name    TEXT    NOT NULL,       -- fund or individual investor name
    company_id       TEXT    NOT NULL,       -- FK → companies.id in pipeline.db
    source           TEXT    NOT NULL,       -- e.g. 'ecv_portfolio', 'greentown_portfolio',
                                             --      'sec_edgar_form_d', 'manual'
    evidence         TEXT,                   -- URL or filing reference
    round_size       REAL,                   -- USD, nullable (not always disclosed)
    first_seen_at    TEXT    NOT NULL,       -- ISO 8601 UTC
    UNIQUE(investor_name, company_id)        -- one edge per investor–company pair
);

CREATE INDEX IF NOT EXISTS idx_founder_name
    ON founder_company_edges(founder_name);

CREATE INDEX IF NOT EXISTS idx_investor_name
    ON investor_company_edges(investor_name);

CREATE INDEX IF NOT EXISTS idx_founder_company
    ON founder_company_edges(company_id);

CREATE INDEX IF NOT EXISTS idx_investor_company
    ON investor_company_edges(company_id);
"""


# ── Init ───────────────────────────────────────────────────────────────────────

def init_relationship_graph_db(conn: sqlite3.Connection) -> None:
    """Create the relationship graph schema if it doesn't exist.

    Idempotent: safe to call on every pipeline run. Uses CREATE TABLE IF NOT
    EXISTS so existing data is never touched.

    Args:
        conn: SQLite connection to relationship_graph.db (or any in-memory DB
              for testing).
    """
    conn.executescript(_DDL)
    conn.commit()


# ── Writers ────────────────────────────────────────────────────────────────────

def insert_founder_edge(
    conn: sqlite3.Connection,
    founder_name: str,
    company_id: str,
    source: str,
    evidence: str | None = None,
    confidence: float = 0.8,
    first_seen_at: str | None = None,
) -> None:
    """Insert or ignore a founder → company edge.

    On conflict (same founder_name + company_id), the existing row is kept.

    Args:
        conn:         Connection to relationship_graph.db.
        founder_name: Normalized (lowercase, stripped) founder name.
        company_id:   companies.id from pipeline.db.
        source:       Origin of this edge (e.g. 'enriched_description').
        evidence:     Raw text or URL that produced this edge.
        confidence:   0.0–1.0; defaults to 0.8 (LLM-extracted, unverified).
        first_seen_at: ISO 8601 UTC string; defaults to now.
    """
    if first_seen_at is None:
        import datetime
        first_seen_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    conn.execute(
        """
        INSERT OR IGNORE INTO founder_company_edges
            (founder_name, company_id, source, evidence, confidence, first_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (founder_name.lower().strip(), company_id, source, evidence, confidence, first_seen_at),
    )


def insert_investor_edge(
    conn: sqlite3.Connection,
    investor_name: str,
    company_id: str,
    source: str,
    evidence: str | None = None,
    round_size: float | None = None,
    first_seen_at: str | None = None,
) -> None:
    """Insert or ignore an investor → company edge.

    On conflict (same investor_name + company_id), the existing row is kept.

    Args:
        conn:          Connection to relationship_graph.db.
        investor_name: Fund or individual investor name.
        company_id:    companies.id from pipeline.db.
        source:        Origin of this edge (e.g. 'ecv_portfolio').
        evidence:      URL or filing reference.
        round_size:    USD amount; None if undisclosed.
        first_seen_at: ISO 8601 UTC string; defaults to now.
    """
    if first_seen_at is None:
        import datetime
        first_seen_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    conn.execute(
        """
        INSERT OR IGNORE INTO investor_company_edges
            (investor_name, company_id, source, evidence, round_size, first_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (investor_name, company_id, source, evidence, round_size, first_seen_at),
    )


# ── Readers ────────────────────────────────────────────────────────────────────

def get_founder_companies(conn: sqlite3.Connection, founder_name: str) -> list[dict]:
    """Return all company_ids associated with a given founder.

    Args:
        conn:         Connection to relationship_graph.db.
        founder_name: Exact string to look up (lowercased internally).

    Returns:
        List of row dicts from founder_company_edges.
    """
    rows = conn.execute(
        "SELECT * FROM founder_company_edges WHERE founder_name = ?",
        (founder_name.lower().strip(),),
    ).fetchall()
    return [dict(r) for r in rows]


def get_investor_companies(conn: sqlite3.Connection, investor_name: str) -> list[dict]:
    """Return all company_ids associated with a given investor.

    Args:
        conn:          Connection to relationship_graph.db.
        investor_name: Exact string to look up.

    Returns:
        List of row dicts from investor_company_edges.
    """
    rows = conn.execute(
        "SELECT * FROM investor_company_edges WHERE investor_name = ?",
        (investor_name,),
    ).fetchall()
    return [dict(r) for r in rows]
