"""
Tests for enrich/job_feeds_lookup.py.

All tests use mocked HTTP responses. Zero live API calls.

Tests:
  1. lookup_job_feeds: Greenhouse hit → found=True, correct platform/counts
  2. lookup_job_feeds: Greenhouse 404 → falls through to Lever
  3. lookup_job_feeds: Lever hit → found=True, correct platform/counts
  4. lookup_job_feeds: Lever 404 → falls through to Ashby
  5. lookup_job_feeds: Ashby hit → found=True, correct platform/counts
  6. lookup_job_feeds: all 404 → found=False
  7. lookup_job_feeds: Houston location detection case-insensitive
  8. lookup_job_feeds: remote location counted separately, not as Houston
  9. _slugify: handles punctuation, spaces, mixed case
  10. lookup_job_feeds: empty company name → found=False, no HTTP calls
  11. houston_job_titles: capped at 10 entries
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from enrich.job_feeds_lookup import _slugify, lookup_job_feeds


def _mock_resp(data, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


def _mock_404() -> MagicMock:
    m = MagicMock()
    m.status_code = 404
    m.raise_for_status = MagicMock()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Greenhouse hit
# ─────────────────────────────────────────────────────────────────────────────

def test_greenhouse_hit() -> None:
    """Greenhouse returns 3 jobs: 2 Houston, 1 remote → correct counts."""
    gh_data = {
        "jobs": [
            {"id": 1, "title": "Software Engineer", "location": {"name": "Houston, TX"}},
            {"id": 2, "title": "Field Technician", "location": {"name": "Houston, Texas"}},
            {"id": 3, "title": "Remote Data Analyst", "location": {"name": "Remote"}},
        ]
    }
    with patch("enrich.job_feeds_lookup.requests.get", return_value=_mock_resp(gh_data)):
        result = lookup_job_feeds("SomeCompany")

    assert result["found"] is True
    assert result["platform"] == "greenhouse"
    assert result["total_jobs"] == 3
    assert result["houston_jobs"] == 2
    assert result["remote_jobs"] == 1
    assert "Software Engineer" in result["houston_job_titles"]
    assert "Field Technician" in result["houston_job_titles"]


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Greenhouse 404 → falls through to Lever
# ─────────────────────────────────────────────────────────────────────────────

def test_greenhouse_404_falls_to_lever() -> None:
    """Greenhouse 404 → Lever returns data → platform='lever'."""
    lever_data = [
        {"id": "abc", "text": "Houston Role", "categories": {"location": "Houston, TX"}},
        {"id": "def", "text": "Austin Role", "categories": {"location": "Austin, TX"}},
    ]
    with patch("enrich.job_feeds_lookup.requests.get") as mock_get:
        mock_get.side_effect = [_mock_404(), _mock_resp(lever_data)]
        result = lookup_job_feeds("SomeCompany")

    assert result["found"] is True
    assert result["platform"] == "lever"
    assert result["total_jobs"] == 2
    assert result["houston_jobs"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Lever hit
# ─────────────────────────────────────────────────────────────────────────────

def test_lever_direct_hit() -> None:
    """When Greenhouse returns 404 and Lever returns jobs."""
    lever_data = [
        {"id": "x1", "text": "Product Manager", "categories": {"location": "Houston, TX"}},
    ]
    with patch("enrich.job_feeds_lookup.requests.get") as mock_get:
        mock_get.side_effect = [_mock_404(), _mock_resp(lever_data)]
        result = lookup_job_feeds("Test Corp")

    assert result["platform"] == "lever"
    assert result["houston_jobs"] == 1
    assert "Product Manager" in result["houston_job_titles"]


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Lever 404 → falls through to Ashby
# ─────────────────────────────────────────────────────────────────────────────

def test_lever_404_falls_to_ashby() -> None:
    """GH 404, Lever 404 → Ashby returns data → platform='ashby'."""
    ashby_data = {
        "results": [
            {"id": "aa1", "title": "Operations Lead", "location": "Houston, TX"},
            {"id": "aa2", "title": "Remote Engineer", "location": "Remote - US"},
        ]
    }
    with patch("enrich.job_feeds_lookup.requests.get", return_value=_mock_404()), \
         patch("enrich.job_feeds_lookup.requests.post", return_value=_mock_resp(ashby_data)):
        result = lookup_job_feeds("Ashby Corp")

    assert result["found"] is True
    assert result["platform"] == "ashby"
    assert result["total_jobs"] == 2
    assert result["houston_jobs"] == 1
    assert result["remote_jobs"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Ashby hit — locationName fallback field
# ─────────────────────────────────────────────────────────────────────────────

def test_ashby_location_name_fallback() -> None:
    """Ashby uses 'locationName' when 'location' is absent."""
    ashby_data = {
        "results": [
            {"id": "b1", "title": "Engineer", "locationName": "Houston, TX"},
        ]
    }
    with patch("enrich.job_feeds_lookup.requests.get", return_value=_mock_404()), \
         patch("enrich.job_feeds_lookup.requests.post", return_value=_mock_resp(ashby_data)):
        result = lookup_job_feeds("Ashby Corp 2")

    assert result["houston_jobs"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: All 404 → found=False
# ─────────────────────────────────────────────────────────────────────────────

def test_all_not_found() -> None:
    """All three platforms return 404 → found=False."""
    with patch("enrich.job_feeds_lookup.requests.get", return_value=_mock_404()), \
         patch("enrich.job_feeds_lookup.requests.post") as mock_post:
        mock_post.return_value = _mock_404()
        result = lookup_job_feeds("Unknown Startup")

    assert result["found"] is False
    assert result["platform"] is None
    assert result["total_jobs"] == 0
    assert result["houston_jobs"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Houston location detection — case-insensitive, suburbs
# ─────────────────────────────────────────────────────────────────────────────

def test_houston_location_case_insensitive() -> None:
    """'HOUSTON, TX', 'Houston Texas', 'Katy, TX' all count as Houston."""
    gh_data = {
        "jobs": [
            {"id": 1, "title": "Role A", "location": {"name": "HOUSTON, TX"}},
            {"id": 2, "title": "Role B", "location": {"name": "Houston Texas"}},
            {"id": 3, "title": "Role C", "location": {"name": "Katy, TX"}},
            {"id": 4, "title": "Role D", "location": {"name": "Dallas, TX"}},
        ]
    }
    with patch("enrich.job_feeds_lookup.requests.get", return_value=_mock_resp(gh_data)):
        result = lookup_job_feeds("Company")

    assert result["houston_jobs"] == 3
    assert result["total_jobs"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Remote counted separately, not as Houston
# ─────────────────────────────────────────────────────────────────────────────

def test_remote_not_counted_as_houston() -> None:
    """'Remote - Houston' counts as remote AND houston (it contains both keywords)."""
    gh_data = {
        "jobs": [
            {"id": 1, "title": "Remote", "location": {"name": "Remote - US"}},
            {"id": 2, "title": "Remote Houston", "location": {"name": "Remote - Houston, TX"}},
        ]
    }
    with patch("enrich.job_feeds_lookup.requests.get", return_value=_mock_resp(gh_data)):
        result = lookup_job_feeds("Company")

    # "Remote - US" → remote only
    # "Remote - Houston, TX" → both remote AND houston
    assert result["remote_jobs"] == 2
    assert result["houston_jobs"] == 1  # only the one that says Houston


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: _slugify
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("Ion Energy", "ion-energy"),
    ("Greentown Labs Houston", "greentown-labs-houston"),
    ("44.01", "44-01"),
    ("CommonWealth Fusion Systems", "commonwealth-fusion-systems"),
    ("SomeCompany, Inc.", "somecompany-inc"),
    ("  Spaces  Around  ", "spaces-around"),
    ("Slash/Corp", "slash-corp"),
])
def test_slugify(name, expected) -> None:
    assert _slugify(name) == expected, f"_slugify({name!r}) = {_slugify(name)!r}, expected {expected!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Empty company name
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_company_name() -> None:
    """Empty or whitespace-only → found=False, no HTTP calls."""
    with patch("enrich.job_feeds_lookup.requests.get") as mock_get, \
         patch("enrich.job_feeds_lookup.requests.post") as mock_post:
        r1 = lookup_job_feeds("")
        r2 = lookup_job_feeds("   ")
    mock_get.assert_not_called()
    mock_post.assert_not_called()
    assert r1["found"] is False
    assert r2["found"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: houston_job_titles capped at 10
# ─────────────────────────────────────────────────────────────────────────────

def test_houston_job_titles_capped() -> None:
    """More than 10 Houston jobs → houston_job_titles has at most 10 entries."""
    jobs = [
        {"id": i, "title": f"Role {i}", "location": {"name": "Houston, TX"}}
        for i in range(15)
    ]
    gh_data = {"jobs": jobs}
    with patch("enrich.job_feeds_lookup.requests.get", return_value=_mock_resp(gh_data)):
        result = lookup_job_feeds("BigCo")

    assert result["houston_jobs"] == 15
    assert len(result["houston_job_titles"]) == 10
