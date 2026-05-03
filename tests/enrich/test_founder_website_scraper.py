"""
Tests for enrich/founder_website_scraper.py.

All tests use in-memory SQLite and unittest.mock — zero live network or API calls.

Tests:
  1. fetch_team_page_success — finds first path with sufficient text content
  2. fetch_team_page_skips_short_pages — pages below min_chars threshold are skipped
  3. fetch_team_page_all_fail — returns (None, "") when all paths fail
  4. scrape_no_website_placeholder — no_website records get placeholder, skipped in run
  5. scrape_llm_extracts_founders — LLM response is persisted to DB correctly
  6. scrape_fetch_failure_fallback — fetch fail persists not-accessible placeholder
  7. be_fellows_injected_even_if_llm_misses_on_website — BE Fellows always in result
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
import requests

from enrich.founder_website_scraper import (
    ScrapeSummary,
    _PLACEHOLDER_NOT_ACCESSIBLE,
    _PLACEHOLDER_NO_WEBSITE,
    fetch_team_page,
    run_website_scraper,
    scrape_website_for_founders,
)
from enrich.founder_extraction import FounderExtractionResult, FounderRecord


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


def _make_http_response(status_code: int, text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _make_conn() -> sqlite3.Connection:
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
            name_raw TEXT,
            website TEXT,
            description TEXT,
            harvested_at TEXT
        )
    """)
    conn.commit()
    return conn


# ── Test 1: fetch_team_page success ──────────────────────────────────────────

def test_fetch_team_page_returns_first_valid_path() -> None:
    """fetch_team_page returns the first path returning 200 with sufficient text."""
    long_text = "<p>" + ("This company was founded by Jane Doe. " * 50) + "</p>"

    session = MagicMock()
    session.get.return_value = _make_http_response(200, long_text)

    url, text = fetch_team_page(
        "https://example.com",
        session=session,
        min_chars=100,
    )

    assert url is not None
    assert "example.com" in url
    assert len(text) >= 100
    # Should have stopped after first path
    assert session.get.call_count == 1


# ── Test 2: short pages are skipped ──────────────────────────────────────────

def test_fetch_team_page_skips_short_pages() -> None:
    """Pages below min_chars are skipped; next path is tried."""
    short_html = "<p>Short</p>"
    long_html = "<p>" + ("Founder Jane Doe, CEO. " * 40) + "</p>"

    session = MagicMock()
    # First path returns short content, second returns sufficient content
    session.get.side_effect = [
        _make_http_response(200, short_html),
        _make_http_response(200, long_html),
    ]

    url, text = fetch_team_page(
        "https://example.com",
        session=session,
        min_chars=200,
        paths=["/about", "/team"],
    )

    assert session.get.call_count == 2
    assert url is not None
    assert "team" in url
    assert len(text) >= 200


# ── Test 3: all paths fail → (None, "") ──────────────────────────────────────

def test_fetch_team_page_all_fail_returns_none() -> None:
    """All paths returning 404 results in (None, '')."""
    session = MagicMock()
    session.get.return_value = _make_http_response(404, "not found")

    url, text = fetch_team_page(
        "https://example.com",
        session=session,
        paths=["/about", "/team"],
    )

    assert url is None
    assert text == ""


def test_fetch_team_page_timeout_skips_path() -> None:
    """Timeout on one path falls through to next path."""
    long_html = "<p>" + ("Jane Doe, CEO and Co-founder. " * 30) + "</p>"

    session = MagicMock()
    session.get.side_effect = [
        requests.exceptions.Timeout(),
        _make_http_response(200, long_html),
    ]

    url, text = fetch_team_page(
        "https://example.com",
        session=session,
        min_chars=100,
        paths=["/about", "/team"],
    )

    assert url is not None
    assert "team" in url


# ── Test 4: run_website_scraper skips no-website records ─────────────────────

def test_run_scraper_no_website_writes_placeholder() -> None:
    """Records without a website get placeholder and are counted as no_website."""
    conn = _make_conn()
    conn.execute("""
        INSERT INTO companies (id, name, venture_scale_score, enrichment_status,
            sub_sector, source_ids, is_duplicate, is_excluded)
        VALUES ('co-nw', 'NoWebCo', 8.0, 'enriched', 'solar',
            '["Greentown Houston"]', 0, 0)
    """)
    # No raw_records row → website will be NULL
    conn.commit()

    with patch("enrich.founder_website_scraper.lookup_company_for_fellow_match", return_value=[]):
        summary = run_website_scraper(conn, dry_run=True, force=True)

    assert summary.no_website == 1
    assert summary.fetch_attempted == 0

    row = conn.execute(
        "SELECT founder_names_detail FROM companies WHERE id='co-nw'"
    ).fetchone()
    assert row is not None
    detail = json.loads(row["founder_names_detail"])
    assert "No website available" in detail.get("extraction_notes", "")


# ── Test 5: LLM extraction + DB persistence ──────────────────────────────────

def test_run_scraper_persists_founders_to_db() -> None:
    """Extracted founders are written to founder_names_detail and founder_names."""
    conn = _make_conn()
    conn.execute("""
        INSERT INTO companies (id, name, venture_scale_score, enrichment_status,
            sub_sector, source_ids, is_duplicate, is_excluded)
        VALUES ('co-1', 'Alpha Corp', 7.5, 'enriched', 'advanced_materials',
            '["Rice ETVF"]', 0, 0)
    """)
    conn.execute("""
        INSERT INTO raw_records (company_id, name_raw, website, description, harvested_at)
        VALUES ('co-1', 'Alpha Corp', 'https://alphacorp.io', 'Desc.', '2025-01-01')
    """)
    conn.commit()

    long_html = "<p>" + ("Jane Doe, CEO and co-founder of Alpha Corp. " * 30) + "</p>"

    with patch("enrich.founder_website_scraper.lookup_company_for_fellow_match", return_value=[]):
        with patch("enrich.founder_website_scraper.call_llm") as mock_llm:
            mock_llm.return_value = _make_llm_response(
                founders=[{"name": "Jane Doe", "role": "CEO & Co-founder", "background_signals": ""}],
                confidence="HIGH",
                notes="",
            )
            mock_session = MagicMock()
            mock_session.get.return_value = _make_http_response(200, long_html)

            with patch("enrich.founder_website_scraper.requests.Session", return_value=mock_session):
                summary = run_website_scraper(conn, dry_run=False, force=True)

    assert summary.with_founders == 1
    assert summary.errors == 0

    row = conn.execute(
        "SELECT founder_names, founder_names_detail FROM companies WHERE id='co-1'"
    ).fetchone()
    assert row["founder_names"] == "Jane Doe"
    detail = json.loads(row["founder_names_detail"])
    assert detail["founders"][0]["name"] == "Jane Doe"


# ── Test 6: fetch failure → not-accessible placeholder ───────────────────────

def test_scrape_fetch_failure_returns_not_accessible() -> None:
    """When all paths fail, result notes 'not publicly accessible'."""
    session = MagicMock()
    session.get.return_value = _make_http_response(404, "not found")

    with patch("enrich.founder_website_scraper.lookup_company_for_fellow_match", return_value=[]):
        result, fetched_url = scrape_website_for_founders(
            company_id="co-x",
            name="XCorp",
            base_url="https://xcorp.io",
            be_fellows_matches=[],
            session=session,
        )

    assert fetched_url is None
    assert result.founders == []
    assert _PLACEHOLDER_NOT_ACCESSIBLE in result.extraction_notes


# ── Test 7: BE Fellows injected even if LLM misses them ──────────────────────

def test_be_fellows_injected_even_if_llm_misses_on_website() -> None:
    """If LLM omits a BE Fellow from website text, module inserts them anyway."""
    be_matches = [{"name": "Bob Jones", "role": "CEO", "company": "BetaCo", "match_type": "exact"}]
    long_html = "<p>" + ("BetaCo builds energy storage systems. " * 30) + "</p>"

    session = MagicMock()
    session.get.return_value = _make_http_response(200, long_html)

    with patch("enrich.founder_website_scraper.call_llm") as mock_llm:
        mock_llm.return_value = _make_llm_response(
            founders=[],
            confidence="LOW",
            notes="Founders not listed on company website.",
        )
        result, _ = scrape_website_for_founders(
            company_id="beta",
            name="BetaCo",
            base_url="https://betaco.io",
            be_fellows_matches=be_matches,
            session=session,
        )

    assert any(f.name == "Bob Jones" for f in result.founders)
    assert any("BE Fellow" in f.background_signals for f in result.founders)
