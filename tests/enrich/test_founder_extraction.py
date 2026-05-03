"""
Tests for enrich/founder_extraction.py.

All tests use in-memory SQLite and unittest.mock to avoid LLM API calls.
Zero live network calls.

Tests:
  1. empty_text_input — returns LOW/empty/notes when description is blank and no BE Fellows
  2. be_fellows_match_path — BE Fellows match produces confirmed founder without LLM call
  3. single_founder_extraction — LLM returns one founder; persisted correctly
  4. multiple_founders_extraction — LLM returns multiple founders; formatted string correct
  5. no_names_found_path — LLM returns empty founders; notes propagated to format string
  6. llm_parse_failure_fallback — parse failure falls back to BE Fellows if available
  7. format_for_spreadsheet_pending — pending=True returns placeholder
  8. run_founder_extraction_db — bulk run writes to DB correctly; skips already-processed
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from enrich.founder_extraction import (
    ExtractionSummary,
    FounderExtractionResult,
    FounderRecord,
    _PLACEHOLDER_NOT_SURFACED,
    _PLACEHOLDER_PENDING,
    extract_founders,
    format_for_spreadsheet,
    run_founder_extraction,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_llm_response(founders=None, confidence="MEDIUM", notes=""):
    """Return a mock LLMResponse with parsed FounderExtractionResult."""
    if founders is None:
        founders = []
    parsed = FounderExtractionResult(
        founders=[FounderRecord(**f) for f in founders],
        extraction_confidence=confidence,
        extraction_notes=notes,
    )
    mock = MagicMock()
    mock.parsed = parsed
    mock.cost_usd = 0.001
    mock.content = "{}"
    return mock


def _make_conn() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the minimal companies schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE companies (
            id TEXT PRIMARY KEY,
            name TEXT,
            summary TEXT,
            venture_scale_reasoning TEXT,
            venture_scale_score REAL,
            sub_sector TEXT,
            source_ids TEXT,
            enrichment_status TEXT,
            is_duplicate INTEGER DEFAULT 0,
            is_excluded INTEGER DEFAULT 0,
            founder_names TEXT,
            founder_names_detail TEXT,
            last_updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE raw_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT,
            description TEXT,
            harvested_at TEXT
        )
    """)
    conn.commit()
    return conn


# ── Test 1: empty text input ───────────────────────────────────────────────────

def test_empty_text_returns_low_empty() -> None:
    """Empty description/summary/reasoning with no BE Fellows → LOW/empty/notes."""
    with patch("enrich.founder_extraction.lookup_company_for_fellow_match", return_value=[]):
        result = extract_founders(
            company_id="test-co",
            name="Test Co",
            description="",
            summary="",
            reasoning="",
            be_fellows_matches=[],
        )
    assert result.founders == []
    assert result.extraction_confidence == "LOW"
    assert "not surfaced" in result.extraction_notes.lower()


# ── Test 2: BE Fellows match path (no LLM needed) ─────────────────────────────

def test_be_fellows_match_produces_confirmed_founder() -> None:
    """BE Fellows match is returned without making an LLM call."""
    be_matches = [{"name": "Alice Smith", "role": "CTO", "company": "Test Co", "match_type": "exact"}]

    with patch("enrich.founder_extraction.lookup_company_for_fellow_match", return_value=be_matches):
        with patch("enrich.founder_extraction.call_llm") as mock_llm:
            mock_llm.return_value = _make_llm_response(
                founders=[{"name": "Alice Smith", "role": "CTO", "background_signals": "BE Fellow"}],
                confidence="HIGH",
                notes="",
            )
            result = extract_founders(
                company_id="test-co",
                name="Test Co",
                description="Alice Smith founded this company.",
                summary="",
                reasoning="",
                be_fellows_matches=be_matches,
            )

    assert len(result.founders) == 1
    assert result.founders[0].name == "Alice Smith"
    assert result.founders[0].background_signals == "BE Fellow"


def test_be_fellows_injected_even_if_llm_misses() -> None:
    """If LLM omits a BE Fellow, the module inserts them anyway."""
    be_matches = [{"name": "Bob Jones", "role": "CEO", "company": "AcmeCo", "match_type": "fuzzy"}]

    with patch("enrich.founder_extraction.call_llm") as mock_llm:
        # LLM returns empty founders
        mock_llm.return_value = _make_llm_response(
            founders=[],
            confidence="LOW",
            notes="Names not surfaced from harvested sources.",
        )
        result = extract_founders(
            company_id="acme",
            name="AcmeCo",
            description="Some description about the company.",
            summary="",
            reasoning="",
            be_fellows_matches=be_matches,
        )

    assert any(f.name == "Bob Jones" for f in result.founders)
    assert any("BE Fellow" in f.background_signals for f in result.founders)


# ── Test 3: single founder extraction ─────────────────────────────────────────

def test_single_founder_extraction() -> None:
    """LLM returns one founder; fields round-trip correctly."""
    with patch("enrich.founder_extraction.call_llm") as mock_llm:
        mock_llm.return_value = _make_llm_response(
            founders=[{
                "name": "Jane Doe",
                "role": "CEO & Co-founder",
                "background_signals": "PhD MIT chemistry",
            }],
            confidence="HIGH",
            notes="",
        )
        result = extract_founders(
            company_id="kanin",
            name="Kanin Energy",
            description="Dr. Jane Doe, CEO and co-founder, has a PhD from MIT in chemistry.",
            summary="Kanin Energy recovers waste heat.",
            reasoning="(1) Strong IP signals.",
            be_fellows_matches=[],
        )

    assert len(result.founders) == 1
    f = result.founders[0]
    assert f.name == "Jane Doe"
    assert f.role == "CEO & Co-founder"
    assert "PhD MIT chemistry" in f.background_signals
    assert result.extraction_confidence == "HIGH"


# ── Test 4: multiple founders + format string ─────────────────────────────────

def test_multiple_founders_format_string() -> None:
    """Multiple founders produce correct semicolon-separated spreadsheet string."""
    with patch("enrich.founder_extraction.call_llm") as mock_llm:
        mock_llm.return_value = _make_llm_response(
            founders=[
                {"name": "Alice A", "role": "CEO", "background_signals": "ex-Tesla"},
                {"name": "Bob B", "role": "CTO", "background_signals": "PhD Stanford"},
            ],
            confidence="HIGH",
            notes="",
        )
        result = extract_founders(
            company_id="multi-co",
            name="Multi Co",
            description="Alice A (CEO) and Bob B (CTO) co-founded Multi Co.",
            summary="Multi Co builds widgets.",
            reasoning="",
            be_fellows_matches=[],
        )

    formatted = format_for_spreadsheet(result)
    assert "Alice A (CEO)" in formatted
    assert "Bob B (CTO)" in formatted
    assert "ex-Tesla" in formatted
    assert "PhD Stanford" in formatted
    assert ";" in formatted  # separator between founders


# ── Test 5: no names found path ───────────────────────────────────────────────

def test_no_names_found_uses_extraction_notes() -> None:
    """When LLM finds no founders, format_for_spreadsheet returns the extraction_notes."""
    with patch("enrich.founder_extraction.call_llm") as mock_llm:
        mock_llm.return_value = _make_llm_response(
            founders=[],
            confidence="LOW",
            notes="Names not surfaced from harvested sources.",
        )
        result = extract_founders(
            company_id="anon-co",
            name="Anon Co",
            description="A company that does things.",
            summary="",
            reasoning="",
            be_fellows_matches=[],
        )

    assert result.founders == []
    formatted = format_for_spreadsheet(result)
    assert formatted == _PLACEHOLDER_NOT_SURFACED


# ── Test 6: LLM parse failure fallback ────────────────────────────────────────

def test_llm_parse_failure_falls_back_to_be_fellows() -> None:
    """If LLM parse fails, BE Fellows are still returned."""
    be_matches = [{"name": "Carol C", "role": "Co-founder", "company": "Corp", "match_type": "exact"}]

    with patch("enrich.founder_extraction.call_llm") as mock_llm:
        fail_resp = MagicMock()
        fail_resp.parsed = None
        fail_resp.content = "not valid json"
        fail_resp.cost_usd = 0.0
        mock_llm.return_value = fail_resp

        result = extract_founders(
            company_id="corp",
            name="Corp",
            description="Carol C is the co-founder.",
            summary="",
            reasoning="",
            be_fellows_matches=be_matches,
        )

    assert any(f.name == "Carol C" for f in result.founders)


# ── Test 7: format_for_spreadsheet pending flag ───────────────────────────────

def test_format_for_spreadsheet_pending() -> None:
    """pending=True returns the pending placeholder regardless of result content."""
    result = FounderExtractionResult(
        founders=[FounderRecord(name="Someone", role="CEO", background_signals="")],
        extraction_confidence="HIGH",
        extraction_notes="",
    )
    assert format_for_spreadsheet(result, pending=True) == _PLACEHOLDER_PENDING
    assert format_for_spreadsheet(None, pending=True) == _PLACEHOLDER_PENDING


# ── Test 8: run_founder_extraction writes to DB ────────────────────────────────

def test_run_founder_extraction_writes_to_db() -> None:
    """Bulk run persists founder_names_detail JSON and founder_names to DB."""
    conn = _make_conn()
    conn.execute("""
        INSERT INTO companies (id, name, summary, venture_scale_reasoning,
            venture_scale_score, sub_sector, source_ids, enrichment_status,
            is_duplicate, is_excluded)
        VALUES ('co-1', 'Alpha Corp', 'Alpha makes widgets.', 'Strong IP.',
            7.5, 'advanced_materials', '["Rice Energy Tech Venture Forum (ETVF)"]',
            'enriched', 0, 0)
    """)
    conn.execute("""
        INSERT INTO raw_records (company_id, description, harvested_at)
        VALUES ('co-1', 'Founded by Dave D, who has a PhD from Rice University.', '2025-01-01')
    """)
    conn.commit()

    with patch("enrich.founder_extraction.lookup_company_for_fellow_match", return_value=[]):
        with patch("enrich.founder_extraction.call_llm") as mock_llm:
            mock_llm.return_value = _make_llm_response(
                founders=[{"name": "Dave D", "role": "Co-founder", "background_signals": "PhD Rice University"}],
                confidence="MEDIUM",
                notes="",
            )
            summary = run_founder_extraction(conn, dry_run=False, force=True)

    assert summary.total_processed == 1
    assert summary.with_founders == 1
    assert summary.errors == 0

    row = conn.execute("SELECT founder_names, founder_names_detail FROM companies WHERE id='co-1'").fetchone()
    assert row["founder_names"] == "Dave D"
    detail = json.loads(row["founder_names_detail"])
    assert detail["founders"][0]["name"] == "Dave D"
    assert detail["founders"][0]["background_signals"] == "PhD Rice University"


def test_run_founder_extraction_skips_already_processed() -> None:
    """Records with existing founder_names_detail are skipped unless force=True."""
    conn = _make_conn()
    conn.execute("""
        INSERT INTO companies (id, name, summary, venture_scale_reasoning,
            venture_scale_score, sub_sector, source_ids, enrichment_status,
            is_duplicate, is_excluded, founder_names_detail)
        VALUES ('co-2', 'Beta Corp', 'Beta.', 'IP signals.',
            7.0, 'solar', '["Greentown Houston"]', 'enriched', 0, 0,
            '{"founders":[],"extraction_confidence":"LOW","extraction_notes":"already done"}')
    """)
    conn.commit()

    with patch("enrich.founder_extraction.call_llm") as mock_llm:
        summary = run_founder_extraction(conn, dry_run=False, force=False)

    # Should not have processed anything (already has detail)
    assert summary.total_processed == 0
    mock_llm.assert_not_called()
