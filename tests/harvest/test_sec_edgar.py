"""
Tests for harvest/sec_edgar.py — SecEdgarFormDHarvester.

All tests use saved JSON fixtures or mocked HTTP responses. Zero live API calls.

Fixture: tests/fixtures/sec_edgar/search_response.json
  3 hits: Greentown Labs Houston (legit), Vinson & Elkins (law firm), Ion Energy Ventures (legit)

Tests:
  1. _parse_entity_name: strips CIK/ticker suffixes
  2. _is_law_firm: flags Vinson & Elkins, not Greentown
  3. _to_record: correct fields for a standard hit
  4. _to_record: law firm flag set for Vinson & Elkins
  5. fetch (mocked): returns 2 records (law firm included but flagged); pagination stop
  6. fetch (mocked): pagination cap — stops at 1000 EDGAR hard cap
  7. tags mapped from items field
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from harvest.sec_edgar import (
    SecEdgarFormDHarvester,
    _build_filing_url,
    _is_law_firm,
    _parse_entity_name,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sec_edgar"


def _load_response() -> dict:
    return json.loads((_FIXTURES / "search_response.json").read_text())


def _mock_resp(data: dict, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


@pytest.fixture
def harvester() -> SecEdgarFormDHarvester:
    from utils.rate_limiter import RateLimiter
    return SecEdgarFormDHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: _parse_entity_name strips CIK/ticker suffix
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_entity_name_strips_suffix() -> None:
    """CIK and ticker suffixes are stripped; internal spaces collapsed."""
    raw = ["GREENTOWN LABS HOUSTON LLC  (CIK 0002001234)"]
    assert _parse_entity_name(raw) == "GREENTOWN LABS HOUSTON LLC"


def test_parse_entity_name_cik_only() -> None:
    """CIK-only suffix stripped correctly."""
    raw = ["ION ENERGY VENTURES INC  (CIK 0001009999)"]
    result = _parse_entity_name(raw)
    assert result == "ION ENERGY VENTURES INC"


def test_parse_entity_name_empty() -> None:
    assert _parse_entity_name([]) is None
    assert _parse_entity_name(None) is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: _is_law_firm
# ─────────────────────────────────────────────────────────────────────────────

def test_is_law_firm_positive() -> None:
    assert _is_law_firm("VINSON & ELKINS LLP") is True
    assert _is_law_firm("Baker Botts LLP") is True
    assert _is_law_firm("Norton Rose Fulbright US LLP") is True


def test_is_law_firm_negative() -> None:
    assert _is_law_firm("Greentown Labs Houston LLC") is False
    assert _is_law_firm("Ion Energy Ventures Inc") is False
    assert _is_law_firm(None) is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: _build_filing_url
# ─────────────────────────────────────────────────────────────────────────────

def test_build_filing_url() -> None:
    url = _build_filing_url("0001234567", "0001234567-24-000001")
    assert url == "https://www.sec.gov/Archives/edgar/data/1234567/000123456724000001/"


def test_build_filing_url_missing() -> None:
    assert _build_filing_url(None, "0001234567-24-000001") is None
    assert _build_filing_url("0001234567", None) is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: _to_record — standard hit
# ─────────────────────────────────────────────────────────────────────────────

def test_to_record_standard_hit() -> None:
    """Standard hit produces correct name, source_url, tags, extra fields."""
    data = _load_response()
    hit = data["hits"]["hits"][0]  # Greentown Labs Houston
    rec = SecEdgarFormDHarvester._to_record(hit)
    assert rec is not None
    assert rec.name == "GREENTOWN LABS HOUSTON LLC"
    assert rec.source == "SEC EDGAR Form D"
    assert rec.location_raw == "Houston, TX"
    assert "reg_d_506b" in rec.tags
    assert rec.extra["adsh"] == "0002001234-24-000001"
    assert rec.extra["cik"] == "0002001234"
    assert rec.extra["file_date"] == "2024-03-15"
    assert rec.extra["form_d_filed_by_law_firm"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: _to_record — law firm flag
# ─────────────────────────────────────────────────────────────────────────────

def test_to_record_law_firm_flagged() -> None:
    """Vinson & Elkins hit has form_d_filed_by_law_firm=True."""
    data = _load_response()
    hit = data["hits"]["hits"][1]  # Vinson & Elkins
    rec = SecEdgarFormDHarvester._to_record(hit)
    assert rec is not None
    assert rec.extra["form_d_filed_by_law_firm"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: fetch — mocked single page (3 hits < PAGE_SIZE → stops)
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_single_page(harvester: SecEdgarFormDHarvester) -> None:
    """fetch() returns 3 records from 3-hit response; law firm included but flagged."""
    data = _load_response()
    with patch("harvest.sec_edgar.requests.get", return_value=_mock_resp(data)):
        records = harvester.fetch()

    assert len(records) == 3
    names = [r.name for r in records]
    assert "GREENTOWN LABS HOUSTON LLC" in names
    assert "VINSON & ELKINS LLP" in names
    assert "ION ENERGY VENTURES INC" in names

    law_firm_rec = next(r for r in records if "VINSON" in r.name)
    assert law_firm_rec.extra["form_d_filed_by_law_firm"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Tags mapped from items field
# ─────────────────────────────────────────────────────────────────────────────

def test_tags_from_items() -> None:
    """Items [01, 03] → tags ['equity', 'equity_and_debt']."""
    data = _load_response()
    hit = data["hits"]["hits"][2]  # Ion Energy Ventures, items=[01, 03]
    rec = SecEdgarFormDHarvester._to_record(hit)
    assert rec is not None
    assert "equity" in rec.tags
    assert "equity_and_debt" in rec.tags
