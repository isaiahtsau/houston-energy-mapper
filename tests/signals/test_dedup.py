"""
Tests for signals/dedup.py — cross-source deduplication.

All tests use in-memory SQLite; zero live DB or API calls.

Tests:
  1. normalize_name: strips parentheticals, corporate suffixes, lowercases
  2. normalize_domain: strips scheme/www/path; rejects invalid
  3. compute_enrichment_status: all three states
  4. Domain match merge: two records with same domain → merged, canonical selected
  5. Fuzzy name match: near-duplicate names above threshold → merged
  6. No-match: distinct companies stay separate
  7. Multi-source merge: source_ids union across three records
  8. Canonical selection: Rice ETVF wins over Greentown on source priority
  9. is_duplicate flag and canonical_id set correctly on merged records
  10. run_dedup idempotent: re-running resets and re-computes correctly
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

import pytest

from signals.dedup import (
    DedupResult,
    MergeCase,
    UnionFind,
    compute_enrichment_status,
    normalize_domain,
    normalize_name,
    run_dedup,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_conn() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with row_factory."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE companies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            name_normalized TEXT,
            source_ids TEXT,
            canonical_domain TEXT,
            website TEXT,
            venture_scale_score REAL,
            venture_scale_confidence TEXT,
            first_seen_at TEXT,
            sub_sector TEXT,
            summary TEXT,
            last_updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE raw_records (
            id TEXT PRIMARY KEY,
            name_raw TEXT,
            website TEXT
        )
        """
    )
    conn.commit()
    return conn


def _insert(
    conn: sqlite3.Connection,
    name: str,
    *,
    source_ids: list[str] | None = None,
    domain: str | None = None,
    website: str | None = None,
    score: float | None = None,
    confidence: str | None = None,
    first_seen: str = "2024-01-01",
    sub_sector: str | None = None,
    summary: str | None = None,
) -> str:
    cid = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO companies
            (id, name, name_normalized, source_ids, canonical_domain, website,
             venture_scale_score, venture_scale_confidence, first_seen_at,
             sub_sector, summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cid,
            name,
            name.lower(),
            json.dumps(source_ids) if source_ids else None,
            domain,
            website,
            score,
            confidence,
            first_seen,
            sub_sector,
            summary,
        ),
    )
    conn.commit()
    return cid


def _fetch(conn: sqlite3.Connection, cid: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else {}


# ── Test 1: normalize_name ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Aeromine Technologies, Inc.", "aeromine technologies"),
    ("Greentown Labs Houston (web)", "greentown labs houston"),
    ("MCatalysis LLC", "mcatalysis"),
    ("Energy Capital Ventures, LP", "energy capital ventures"),
    ("Ion District", "ion district"),
    # Functional words are NOT stripped
    ("Advanced Energy Technologies", "advanced energy technologies"),
    ("Grid Solutions Holdings, Inc.", "grid solutions"),
])
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected, f"normalize_name({raw!r}) = {normalize_name(raw)!r}"


# ── Test 2: normalize_domain ───────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://www.aerominetechnologies.com/about", "aerominetechnologies.com"),
    ("http://greentown.co/houston", "greentown.co"),
    ("https://aerominetechnologies.com", "aerominetechnologies.com"),
    (None, None),
    ("", None),
    ("example.com", None),     # rejected as generic
    ("localhost", None),       # rejected
    ("not-a-url", None),       # no dot → rejected
])
def test_normalize_domain(url: str | None, expected: str | None) -> None:
    assert normalize_domain(url) == expected


# ── Test 3: compute_enrichment_status ─────────────────────────────────────────

def test_enrichment_status_off_thesis() -> None:
    row = {"sub_sector": "off_thesis", "venture_scale_score": 3.0,
           "venture_scale_confidence": "HIGH", "summary": "Some text"}
    assert compute_enrichment_status(row) == "off_thesis"


def test_enrichment_status_pending_description() -> None:
    row = {"sub_sector": "grid_software", "venture_scale_score": 5.0,
           "venture_scale_confidence": "LOW", "summary": ""}
    assert compute_enrichment_status(row) == "pending_description"


def test_enrichment_status_enriched() -> None:
    row = {"sub_sector": "battery_storage", "venture_scale_score": 8.0,
           "venture_scale_confidence": "HIGH", "summary": "Leading battery company."}
    assert compute_enrichment_status(row) == "enriched"


# ── Test 4: Domain match merge ─────────────────────────────────────────────────

def test_domain_match_merges_two_records() -> None:
    """Two records sharing the same domain → merged; one becomes canonical."""
    conn = _make_conn()
    id_a = _insert(conn, "Aeromine Technologies",
                   source_ids=["Rice Energy Tech Venture Forum (ETVF)"],
                   domain="aerominetechnologies.com", score=8.0, confidence="HIGH",
                   sub_sector="wind_tech", summary="VAWT company")
    id_b = _insert(conn, "Aeromine Technologies, Inc.",
                   source_ids=["Greentown Houston"],
                   domain="aerominetechnologies.com", score=5.0, confidence="LOW")

    result = run_dedup(conn)

    assert result.total_before == 2
    assert result.total_after == 1
    assert result.domain_matches == 1
    assert result.merges == 1
    assert result.duplicates_removed == 1

    row_a = _fetch(conn, id_a)
    row_b = _fetch(conn, id_b)

    # Rice ETVF > Greentown → id_a is canonical
    assert row_a["is_duplicate"] == 0
    assert row_a["canonical_id"] is None
    assert row_b["is_duplicate"] == 1
    assert row_b["canonical_id"] == id_a


# ── Test 5: Fuzzy name match ───────────────────────────────────────────────────

def test_fuzzy_name_match_merges_near_duplicates() -> None:
    """Near-duplicate names (different capitalization / no domain) → merged via fuzzy."""
    conn = _make_conn()
    id_a = _insert(conn, "MCatalysis",
                   source_ids=["Greentown Houston"],
                   score=7.0, confidence="MEDIUM", sub_sector="catalysis",
                   summary="Electrochemical catalyst startup")
    id_b = _insert(conn, "Mcatalysis LLC",
                   source_ids=["Ion District"],
                   score=5.0, confidence="LOW")

    result = run_dedup(conn)

    assert result.merges == 1
    assert result.fuzzy_matches >= 1
    row_b = _fetch(conn, id_b)
    # Greentown > Ion District; id_a is canonical
    assert row_b["is_duplicate"] == 1
    assert row_b["canonical_id"] == id_a


# ── Test 6: No match — distinct companies stay separate ───────────────────────

def test_no_match_distinct_companies() -> None:
    """Companies with different names and no domain overlap remain separate."""
    conn = _make_conn()
    _insert(conn, "Advanced Reactor Technologies",
            source_ids=["Halliburton Labs"], score=9.0)
    _insert(conn, "Advanced Ionics",
            source_ids=["Halliburton Labs"], score=8.0)
    _insert(conn, "BioShield Energy",
            source_ids=["SEC EDGAR Form D"], score=5.0)

    result = run_dedup(conn)

    assert result.merges == 0
    assert result.duplicates_removed == 0
    assert result.total_after == 3


# ── Test 7: Multi-source merge — source_ids union ─────────────────────────────

def test_multi_source_merge_unions_source_ids() -> None:
    """Three records sharing the same domain → source_ids merged into canonical."""
    conn = _make_conn()
    id_a = _insert(conn, "GridPoint Energy",
                   source_ids=["Rice Energy Tech Venture Forum (ETVF)"],
                   domain="gridpointenergy.com", score=9.0, confidence="HIGH",
                   sub_sector="grid_software", summary="Grid optimization platform")
    id_b = _insert(conn, "GridPoint Energy Inc",
                   source_ids=["Greentown Houston"],
                   domain="gridpointenergy.com", score=5.0)
    id_c = _insert(conn, "Gridpoint Energy",
                   source_ids=["InnovationMap Houston RSS"],
                   domain="gridpointenergy.com", score=6.0)

    result = run_dedup(conn)

    assert result.total_before == 3
    assert result.total_after == 1
    assert result.duplicates_removed == 2

    canonical = _fetch(conn, id_a)
    merged_sources = json.loads(canonical["source_ids"])
    assert "Rice Energy Tech Venture Forum (ETVF)" in merged_sources
    assert "Greentown Houston" in merged_sources
    assert "InnovationMap Houston RSS" in merged_sources


# ── Test 8: Canonical selection — source priority ─────────────────────────────

def test_canonical_selection_rice_etvf_wins() -> None:
    """Rice ETVF has highest source priority → selected as canonical over Greentown."""
    conn = _make_conn()
    # Insert Greentown record first (so insertion order doesn't determine winner)
    id_greentown = _insert(conn, "SolarFlow",
                           source_ids=["Greentown Houston"],
                           domain="solarflow.io", score=7.0, confidence="HIGH",
                           first_seen="2023-06-01",
                           sub_sector="solar", summary="Solar tech")
    id_etvf = _insert(conn, "SolarFlow Inc",
                      source_ids=["Rice Energy Tech Venture Forum (ETVF)"],
                      domain="solarflow.io", score=6.0, confidence="MEDIUM",
                      first_seen="2024-01-01")

    result = run_dedup(conn)

    assert result.merges == 1
    row_greentown = _fetch(conn, id_greentown)
    row_etvf = _fetch(conn, id_etvf)

    # Rice ETVF wins despite lower score
    assert row_etvf["is_duplicate"] == 0
    assert row_greentown["is_duplicate"] == 1
    assert row_greentown["canonical_id"] == id_etvf


# ── Test 9: enrichment_status set on non-duplicates after dedup ───────────────

def test_enrichment_status_applied_after_dedup() -> None:
    """Non-duplicate records get enrichment_status set after the merge pass."""
    conn = _make_conn()
    id_enriched = _insert(conn, "Electrocore",
                          source_ids=["Greentown Houston"],
                          score=8.0, confidence="HIGH",
                          sub_sector="industrial_electrification",
                          summary="Industrial electrification platform.")
    id_pending = _insert(conn, "ERCOT Wind SPV LLC",
                         source_ids=["ERCOT Interconnection Queue"],
                         score=5.0, confidence="LOW",
                         sub_sector=None, summary="")
    id_offthesis = _insert(conn, "Houston RE Partners",
                           source_ids=["SEC EDGAR Form D"],
                           score=3.0, confidence="LOW",
                           sub_sector="off_thesis", summary="")

    run_dedup(conn)

    assert _fetch(conn, id_enriched)["enrichment_status"] == "enriched"
    assert _fetch(conn, id_pending)["enrichment_status"] == "pending_description"
    assert _fetch(conn, id_offthesis)["enrichment_status"] == "off_thesis"


# ── Test 10: Same-source fuzzy guard ─────────────────────────────────────────

def test_same_source_fuzzy_guard_prevents_fund_series_merge() -> None:
    """Fund series from the same source (e.g. SEC EDGAR) must NOT fuzzy-merge."""
    conn = _make_conn()
    id_a = _insert(conn, "CAZ Energy Evolution Fund - TE, L.P.",
                   source_ids=["SEC EDGAR Form D"], score=5.0)
    id_b = _insert(conn, "CAZ Energy Evolution Fund, L.P.",
                   source_ids=["SEC EDGAR Form D"], score=5.0)

    result = run_dedup(conn)

    assert result.merges == 0, "Same-source fund series must not merge via fuzzy"
    assert result.fuzzy_matches == 0
    assert _fetch(conn, id_a)["is_duplicate"] == 0
    assert _fetch(conn, id_b)["is_duplicate"] == 0


def test_same_source_guard_does_not_block_domain_match() -> None:
    """Same-source records CAN still merge via exact domain match."""
    conn = _make_conn()
    id_a = _insert(conn, "Aeromine",
                   source_ids=["Rice Energy Tech Venture Forum (ETVF)"],
                   domain="aerominetechnologies.com", score=8.0,
                   sub_sector="wind_tech", summary="VAWT company")
    id_b = _insert(conn, "Aeromine Technologies",
                   source_ids=["Rice Energy Tech Venture Forum (ETVF)"],
                   domain="aerominetechnologies.com", score=5.0)

    result = run_dedup(conn)

    assert result.merges == 1
    assert result.domain_matches == 1
    assert result.fuzzy_matches == 0


def test_cross_source_fuzzy_still_merges() -> None:
    """Cross-source near-duplicates (ETVF + Greentown) still merge via fuzzy."""
    conn = _make_conn()
    id_a = _insert(conn, "Kanin Energy",
                   source_ids=["Rice Energy Tech Venture Forum (ETVF)"],
                   score=8.0, sub_sector="waste_heat", summary="Waste heat recovery")
    id_b = _insert(conn, "Kanin Energy, Inc.",
                   source_ids=["SEC EDGAR Form D"],
                   score=5.0)

    result = run_dedup(conn)

    assert result.merges == 1
    assert result.fuzzy_matches >= 1
    row_b = _fetch(conn, id_b)
    assert row_b["is_duplicate"] == 1
    assert row_b["canonical_id"] == id_a


# ── Test 11: run_dedup idempotent ─────────────────────────────────────────────

def test_run_dedup_idempotent() -> None:
    """Running run_dedup twice produces the same result as running it once."""
    conn = _make_conn()
    id_a = _insert(conn, "WindRiver Energy",
                   source_ids=["Halliburton Labs"],
                   domain="windriverenergy.com", score=8.5, confidence="HIGH",
                   sub_sector="wind_tech", summary="Offshore wind developer.")
    id_b = _insert(conn, "Wind River Energy Inc",
                   source_ids=["Ion District"],
                   domain="windriverenergy.com", score=5.0)

    result1 = run_dedup(conn)
    result2 = run_dedup(conn)

    # Both runs produce the same merge count
    assert result1.merges == result2.merges == 1
    assert result1.duplicates_removed == result2.duplicates_removed == 1

    row_b = _fetch(conn, id_b)
    assert row_b["is_duplicate"] == 1
    assert row_b["canonical_id"] == id_a
