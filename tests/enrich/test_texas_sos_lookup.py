"""
Tests for enrich/texas_sos_lookup.py.

All tests use mocked HTTP responses. Zero live API calls.

Tests:
  1. lookup_texas_sos: found=True, correct fields for a Houston result
  2. lookup_texas_sos: is_houston_area=True for Harris County ZIP (770xx)
  3. lookup_texas_sos: is_houston_area=False for out-of-area ZIP
  4. lookup_texas_sos: found=False when API returns success=False (too many results)
  5. lookup_texas_sos: found=False on HTTP error
  6. lookup_texas_sos: found=False for empty company name
  7. _is_houston_zip: ZIP prefix logic (770, 773, 774, 775, 776, 777 accepted)
  8. lookup_texas_sos: is_houston_area=True if ANY result has Houston ZIP
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from enrich.texas_sos_lookup import _is_houston_zip, lookup_texas_sos


def _mock_resp(data: dict, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


def _error_resp() -> MagicMock:
    m = MagicMock()
    m.raise_for_status.side_effect = Exception("HTTP 503")
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Successful lookup — correct fields
# ─────────────────────────────────────────────────────────────────────────────

def test_found_correct_fields() -> None:
    """Found result has correct name, taxpayer_id, mailing_zip, result_count."""
    api_resp = {
        "success": True,
        "data": [
            {
                "name": "GREENTOWN LABS HOUSTON LLC",
                "taxpayerId": "32100693152",
                "mailingAddressZip": "77002",
            }
        ],
        "count": 1,
    }
    with patch("enrich.texas_sos_lookup.requests.get", return_value=_mock_resp(api_resp)):
        result = lookup_texas_sos("Greentown Labs Houston")

    assert result["found"] is True
    assert result["matched_name"] == "GREENTOWN LABS HOUSTON LLC"
    assert result["taxpayer_id"] == "32100693152"
    assert result["mailing_zip"] == "77002"
    assert result["result_count"] == 1
    assert len(result["raw_results"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: is_houston_area True for Harris County ZIP
# ─────────────────────────────────────────────────────────────────────────────

def test_houston_zip_harris_county() -> None:
    """ZIP 77002 (Harris County) → is_houston_area=True."""
    api_resp = {
        "success": True,
        "data": [{"name": "ACME INC", "taxpayerId": "123", "mailingAddressZip": "77002"}],
        "count": 1,
    }
    with patch("enrich.texas_sos_lookup.requests.get", return_value=_mock_resp(api_resp)):
        result = lookup_texas_sos("Acme Inc")
    assert result["is_houston_area"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: is_houston_area False for out-of-area ZIP
# ─────────────────────────────────────────────────────────────────────────────

def test_non_houston_zip() -> None:
    """ZIP 78701 (Austin) → is_houston_area=False."""
    api_resp = {
        "success": True,
        "data": [{"name": "AUSTIN CO", "taxpayerId": "456", "mailingAddressZip": "78701"}],
        "count": 1,
    }
    with patch("enrich.texas_sos_lookup.requests.get", return_value=_mock_resp(api_resp)):
        result = lookup_texas_sos("Austin Co")
    assert result["is_houston_area"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: API returns success=False (too many results)
# ─────────────────────────────────────────────────────────────────────────────

def test_api_error_too_many_results() -> None:
    """success=False (too many results) → found=False, empty result."""
    api_resp = {
        "success": False,
        "error": "Search will return 901 entries. Please refine search.",
    }
    with patch("enrich.texas_sos_lookup.requests.get", return_value=_mock_resp(api_resp)):
        result = lookup_texas_sos("Energy")
    assert result["found"] is False
    assert result["is_houston_area"] is False
    assert result["matched_name"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: HTTP error → empty result
# ─────────────────────────────────────────────────────────────────────────────

def test_http_error_returns_empty() -> None:
    """Network error → found=False."""
    import requests as req_lib
    with patch(
        "enrich.texas_sos_lookup.requests.get",
        side_effect=req_lib.RequestException("timeout"),
    ):
        result = lookup_texas_sos("Some Company")
    assert result["found"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Empty company name
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_company_name() -> None:
    """Empty or whitespace-only name → empty result without HTTP call."""
    with patch("enrich.texas_sos_lookup.requests.get") as mock_get:
        result = lookup_texas_sos("")
        result2 = lookup_texas_sos("   ")
    mock_get.assert_not_called()
    assert result["found"] is False
    assert result2["found"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: _is_houston_zip prefix logic
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("zip_code,expected", [
    ("77002", True),   # Harris County
    ("77030", True),   # Medical Center
    ("77301", True),   # Montgomery County / Conroe
    ("77401", True),   # Fort Bend / Bellaire
    ("77511", True),   # Brazoria
    ("77550", True),   # Galveston Island
    ("77630", True),   # Southeast Harris / Beaumont fringe (776xx)
    ("77701", True),   # 777xx prefix
    ("78701", False),  # Austin
    ("90210", False),  # Beverly Hills
    ("CANADA", False), # Foreign string
    ("N/A", False),    # Missing value string
    (None, False),     # None
    ("", False),       # Empty string
    ("7700", False),   # 4-digit, not a 5-digit ZIP
])
def test_is_houston_zip(zip_code, expected) -> None:
    assert _is_houston_zip(zip_code) is expected, (
        f"_is_houston_zip({zip_code!r}) expected {expected}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: is_houston_area True if ANY result has Houston ZIP
# ─────────────────────────────────────────────────────────────────────────────

def test_houston_area_any_result() -> None:
    """Multiple results: first has Austin ZIP, second has Houston ZIP → is_houston_area=True."""
    api_resp = {
        "success": True,
        "data": [
            {"name": "CORP A", "taxpayerId": "001", "mailingAddressZip": "78701"},
            {"name": "CORP B", "taxpayerId": "002", "mailingAddressZip": "77002"},
        ],
        "count": 2,
    }
    with patch("enrich.texas_sos_lookup.requests.get", return_value=_mock_resp(api_resp)):
        result = lookup_texas_sos("Corp")
    assert result["is_houston_area"] is True
    # First result fields still come from first item
    assert result["mailing_zip"] == "78701"
