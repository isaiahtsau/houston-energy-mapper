"""
SQLite schema definition and connection management.

All tables are created with CREATE TABLE IF NOT EXISTS — the schema initializer
is safe to call on every pipeline run. New columns are added with
ALTER TABLE ADD COLUMN IF NOT EXISTS blocks, avoiding the need for a migration tool.

Database files (all under data/db/):
  pipeline.db          — companies, raw_records, harvest_runs (rebuilt each run)
  relationship_graph.db — founder/investor/accelerator affiliations (flywheel)
  source_quality.db    — per-source pass rates (flywheel)

Schema design notes:
  - Companies use a text primary key (slug) rather than an integer autoincrement
    so that IDs are stable across runs and meaningful in spreadsheet exports.
  - JSON columns store arrays and objects as text; use json.loads() to read them.
  - All timestamps are stored as ISO 8601 strings (UTC) for portability.
  - Human-validated fields (human_validated, human_override) are never silently
    overwritten by pipeline reruns — update logic in storage/db.py checks these
    before any UPDATE and routes disagreements to the review queue.

Current schema version: 1
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from config.settings import settings

# Schema version — increment this when columns are added, then add the
# ALTER TABLE ADD COLUMN IF NOT EXISTS statement to _migrate_schema().
_SCHEMA_VERSION = 1


# ─────────────────────────────────────────────────────────────────────────────
# Connection management
# ─────────────────────────────────────────────────────────────────────────────

def get_connection(db_name: str = "pipeline.db") -> sqlite3.Connection:
    """Return a sqlite3 connection to a named database under data/db/.

    Args:
        db_name: Filename within data/db/ (e.g. "pipeline.db", "source_quality.db").

    Returns:
        sqlite3.Connection with row_factory=sqlite3.Row (dict-like row access)
        and WAL mode enabled for better concurrent read performance.
    """
    db_path = settings.db_dir / db_name
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # WAL mode allows reads while a write is in progress (better for logging)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# Schema DDL
# ─────────────────────────────────────────────────────────────────────────────

_COMPANIES_DDL = """
CREATE TABLE IF NOT EXISTS companies (
    -- Identity
    id                      TEXT PRIMARY KEY,   -- provisional: slug(name); canonical: slug(domain)
    canonical_domain        TEXT,               -- set during enrichment; drives ID promotion
    name                    TEXT NOT NULL,       -- display name
    name_normalized         TEXT NOT NULL,       -- lowercased, suffix-stripped; used for dedup

    -- Provenance
    source_ids              TEXT NOT NULL,       -- JSON array of SOURCE_NAMEs
    first_seen_at           TEXT NOT NULL,       -- ISO 8601 UTC
    last_updated_at         TEXT NOT NULL,       -- ISO 8601 UTC

    -- Venture-scale classification
    venture_scale_score     REAL,               -- 0.0–1.0; null = not yet classified
    venture_scale_confidence TEXT,              -- HIGH | MEDIUM | LOW
    venture_scale_reasoning TEXT,              -- free-text reasoning trace from LLM
    venture_scale_prompt_version TEXT,         -- e.g. "v1"; tracks which prompt produced this
    is_excluded             INTEGER DEFAULT 0,  -- 1 if hard-excluded by classifier rules
    exclude_reason          TEXT,               -- e.g. "services firm — no technology IP"

    -- Enrichment
    founder_names           TEXT,               -- JSON array of strings
    founder_pedigree        TEXT,               -- JSON object: {name: {tier, detail}}
    sub_sector              TEXT,               -- e.g. "Carbon Capture & Storage"
    summary                 TEXT,               -- one-sentence human-readable summary

    -- Houston presence score
    houston_tier            TEXT,               -- A | A-low | B-high | B | B-low | C
    houston_points          INTEGER,            -- total composite score
    houston_signals         TEXT,               -- JSON array of {signal, points, source}

    -- Review and validation
    in_review_queue         INTEGER DEFAULT 0,  -- 1 = needs manual review
    review_reason           TEXT,               -- why it was queued (e.g. "LOW confidence")
    human_validated         INTEGER DEFAULT 0,  -- 1 = a human has reviewed and confirmed
    human_override          TEXT                -- JSON: human's override values (never auto-overwritten)
);
"""

_RAW_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS raw_records (
    -- One row per (company, source). Multiple sources may find the same company.
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT,           -- FK to companies.id; null until dedup resolves it
    source          TEXT NOT NULL,  -- BaseHarvester.SOURCE_NAME
    source_url      TEXT,           -- specific page/document URL
    name_raw        TEXT NOT NULL,  -- company name as it appeared in this source
    description     TEXT,
    website         TEXT,
    location_raw    TEXT,
    tags            TEXT,           -- JSON array
    extra           TEXT,           -- JSON object (source-specific fields)
    harvested_at    TEXT NOT NULL   -- ISO 8601 UTC
);
"""

_HARVEST_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS harvest_runs (
    -- Audit log for each harvester execution within a pipeline run.
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL,      -- UUID4 identifying the pipeline run
    source              TEXT NOT NULL,      -- BaseHarvester.SOURCE_NAME
    started_at          TEXT NOT NULL,      -- ISO 8601 UTC
    completed_at        TEXT,
    success             INTEGER NOT NULL,   -- 1 = ok, 0 = failed
    records_harvested   INTEGER DEFAULT 0,
    error               TEXT                -- populated on failure
);
"""

_LLM_CALL_LOG_DDL = """
CREATE TABLE IF NOT EXISTS llm_call_log (
    -- Structured log of every LLM API call. Mirrors the LLMResponse dataclass.
    -- Useful for cost auditing, prompt debugging, and run comparison.
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id         TEXT NOT NULL,          -- UUID4 from LLMResponse
    run_id          TEXT,                   -- pipeline run this call belongs to
    prompt_name     TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    model           TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    cost_usd        REAL NOT NULL,
    latency_ms      REAL NOT NULL,
    parsed_ok       INTEGER NOT NULL,       -- 1 if structured output parsed successfully
    called_at       TEXT NOT NULL           -- ISO 8601 UTC
);
"""

_ALL_DDL = [
    _COMPANIES_DDL,
    _RAW_RECORDS_DDL,
    _HARVEST_RUNS_DDL,
    _LLM_CALL_LOG_DDL,
]


# ─────────────────────────────────────────────────────────────────────────────
# Schema initialization
# ─────────────────────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """Initialize the pipeline database schema.

    Creates all tables if they don't exist. Safe to call on every pipeline run.
    If conn is None, a new connection to pipeline.db is created and returned.

    Args:
        conn: Optional existing sqlite3.Connection. Pass one to initialize
              a specific database (e.g. an in-memory database for tests).

    Returns:
        The connection that was used (same as conn if provided, or a new one).
    """
    if conn is None:
        conn = get_connection("pipeline.db")
    for ddl in _ALL_DDL:
        conn.execute(ddl)
    conn.commit()
    _migrate_schema(conn)
    return conn


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply any ALTER TABLE migrations needed since the initial schema.

    Add new ALTER TABLE ADD COLUMN IF NOT EXISTS statements here when new
    columns are introduced. Existing data is preserved; new columns default to NULL.

    SQLite supports IF NOT EXISTS in ALTER TABLE since version 3.37.0 (2021-11-27).
    """
    # Example (uncomment when adding a new column in schema version 2):
    # conn.execute(
    #     "ALTER TABLE companies ADD COLUMN IF NOT EXISTS crunchbase_url TEXT"
    # )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Helper: JSON column serialization
# ─────────────────────────────────────────────────────────────────────────────

def to_json_column(value: list | dict | None) -> str | None:
    """Serialize a list or dict to a JSON string for storage in a TEXT column.

    Returns None if value is None, so NULL is stored rather than "null".
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json_column(value: str | None) -> Any:
    """Deserialize a JSON TEXT column value.

    Returns None if value is None or empty. Returns the raw string if it is
    not valid JSON (rather than raising) so that malformed stored data does
    not crash the pipeline on read.
    """
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
