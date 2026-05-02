"""
Tests for harvest/ercot_queue.py — ErcotQueueHarvester.

All tests use a saved XLSX fixture (tests/fixtures/ercot_queue/gis_report.xlsx).
Mocked HTTP responses for doc list and download. Zero live API calls.

Fixture: tests/fixtures/ercot_queue/gis_report.xlsx
  Large Gen sheet (5 data rows):
    - 21INR0012  Alpha Solar LLC     Harris   HOUSTON  IA Signed       SOL  150.0 MW  ← included
    - 22INR0034  Beta Wind Energy    Brazoria HOUSTON  Full IA Executed WIN  200.0 MW  ← included
    - 22INR0099  Dallas Power Co     Dallas   NORTH    IA Executed      SOL  100.0 MW  ← filtered (zone)
    - 23INR0050  Pending Co          Harris   HOUSTON  Phase 2 Study    GAS  500.0 MW  ← filtered (no IA)
  Small Gen sheet (2 data rows):
    - 23INR0101  Gamma Solar         Fort Bend HOUSTON  (IA Date col)   SOL  4.5 MW    ← included
    - 23INR0200  Austin Wind Co      Travis    AUSTIN   (IA Date col)   WIN  5.0 MW    ← filtered (zone)

Expected: 3 records (2 Large Gen + 1 Small Gen)

Tests:
  1. parse_gis_xlsx: correct total record count (3)
  2. parse_gis_xlsx: large gen records have correct entity names
  3. parse_gis_xlsx: small gen record has correct entity name
  4. parse_gis_xlsx: non-Houston records excluded
  5. parse_gis_xlsx: Large Gen record without IA in phase excluded
  6. record fields: description format, tags, extra keys
  7. fetch (mocked HTTP): doc list → download → parse → 3 records
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harvest.ercot_queue import ErcotQueueHarvester, parse_gis_xlsx

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "ercot_queue"


def _xlsx_bytes() -> bytes:
    return (_FIXTURES / "gis_report.xlsx").read_bytes()


def _mock_doc_list_resp() -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.raise_for_status = MagicMock()
    m.json.return_value = {
        "ListDocsByRptTypeRes": {
            "DocumentList": [
                {"Document": {"DocID": "12345", "FriendlyName": "GIS_Report_April2026"}}
            ]
        }
    }
    return m


def _mock_download_resp() -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.raise_for_status = MagicMock()
    m.content = _xlsx_bytes()
    return m


@pytest.fixture
def harvester() -> ErcotQueueHarvester:
    from utils.rate_limiter import RateLimiter
    return ErcotQueueHarvester(rate_limiter=RateLimiter(min_delay_seconds=0))


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Total record count from fixture
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_gis_xlsx_total_count() -> None:
    """Fixture yields 3 Houston IA-signed records (2 Large Gen + 1 Small Gen)."""
    records = parse_gis_xlsx(_xlsx_bytes())
    assert len(records) == 3, f"Expected 3 records, got {len(records)}: {[r.name for r in records]}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Large Gen entities present
# ─────────────────────────────────────────────────────────────────────────────

def test_large_gen_entities_present() -> None:
    """Alpha Solar LLC and Beta Wind Energy are in the result."""
    records = parse_gis_xlsx(_xlsx_bytes())
    names = {r.name for r in records}
    assert "Alpha Solar LLC" in names, f"Alpha Solar LLC not found; names={names}"
    assert "Beta Wind Energy" in names, f"Beta Wind Energy not found; names={names}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Small Gen entity present
# ─────────────────────────────────────────────────────────────────────────────

def test_small_gen_entity_present() -> None:
    """Gamma Solar (Small Gen, HOUSTON zone) is in the result."""
    records = parse_gis_xlsx(_xlsx_bytes())
    names = {r.name for r in records}
    assert "Gamma Solar" in names, f"Gamma Solar not found; names={names}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Non-Houston records excluded
# ─────────────────────────────────────────────────────────────────────────────

def test_non_houston_excluded() -> None:
    """Dallas Power Co (NORTH zone) and Austin Wind Co (AUSTIN zone) are excluded."""
    records = parse_gis_xlsx(_xlsx_bytes())
    names = {r.name for r in records}
    assert "Dallas Power Co" not in names
    assert "Austin Wind Co" not in names


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Large Gen without IA excluded
# ─────────────────────────────────────────────────────────────────────────────

def test_no_ia_large_gen_excluded() -> None:
    """Pending Co (HOUSTON but 'Phase 2 Study', no IA) is excluded from Large Gen."""
    records = parse_gis_xlsx(_xlsx_bytes())
    names = {r.name for r in records}
    assert "Pending Co" not in names


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Record fields
# ─────────────────────────────────────────────────────────────────────────────

def test_record_fields() -> None:
    """Alpha Solar record has correct description format, tags, and extra keys."""
    records = parse_gis_xlsx(_xlsx_bytes())
    alpha = next(r for r in records if r.name == "Alpha Solar LLC")

    assert alpha.source == "ERCOT Interconnection Queue"
    assert "Solar" in alpha.description
    assert "150.0 MW" in alpha.description
    assert "Solar" in alpha.tags
    assert alpha.extra["inr"] == "21INR0012"
    assert alpha.extra["fuel"] == "SOL"
    assert alpha.extra["capacity_mw"] == 150.0
    assert alpha.extra["zone"] == "HOUSTON"


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Full fetch with mocked HTTP
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_mocked_http(harvester: ErcotQueueHarvester) -> None:
    """Full fetch: doc list → download → parse → 3 records, no file write."""
    with patch("harvest.ercot_queue.requests.Session") as mock_session_cls, \
         patch("harvest.ercot_queue._save_xlsx"):
        session = MagicMock()
        session.get.side_effect = [_mock_doc_list_resp(), _mock_download_resp()]
        mock_session_cls.return_value = session

        records = harvester.fetch()

    assert len(records) == 3
    names = {r.name for r in records}
    assert "Alpha Solar LLC" in names
    assert "Beta Wind Energy" in names
    assert "Gamma Solar" in names
