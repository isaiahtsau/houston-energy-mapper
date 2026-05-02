"""
Tests for enrich/be_fellows_lookup.py.

All tests use the real data/reference/be_fellows_2026_raw.txt file (static
reference data, never changes between pipeline runs). Zero HTTP calls.

Tests:
  1. parse_raw_file: at least 70 unique companies extracted
  2. parse_raw_file: at least 100 total fellows across all companies
  3. lookup exact: "Molten Industries" → finds Caleb Boyd (CTO) + Kevin Bush (CEO)
  4. lookup fuzzy: slight typo variant → still returns a match (match_type="fuzzy")
  5. lookup no-match: unknown company → empty list
  6. parse excludes Business Fellows: "Business Fellow" company not in result
"""
from __future__ import annotations

import pytest

from enrich.be_fellows_lookup import (
    _reset_cache,
    lookup_company_for_fellow_match,
    parse_raw_file,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset module-level companies cache before each test."""
    _reset_cache()
    yield
    _reset_cache()


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Company count
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_raw_file_company_count() -> None:
    """Raw file contains at least 70 distinct companies (excl. Business Fellows)."""
    companies = parse_raw_file()
    assert len(companies) >= 70, (
        f"Expected ≥70 companies, got {len(companies)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Total fellow count
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_raw_file_fellow_count() -> None:
    """Raw file contains at least 100 total fellows (Innovator Fellows with real companies)."""
    companies = parse_raw_file()
    total = sum(len(v["fellows"]) for v in companies.values())
    assert total >= 100, f"Expected ≥100 fellows, got {total}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Exact match — Molten Industries
# ─────────────────────────────────────────────────────────────────────────────

def test_exact_match_molten_industries() -> None:
    """'Molten Industries' exact match → Caleb Boyd (CTO) + Kevin Bush (CEO & Co-Founder)."""
    results = lookup_company_for_fellow_match("Molten Industries")
    assert len(results) == 2, (
        f"Expected 2 fellows for Molten Industries, got {len(results)}: {results}"
    )

    names = {r["name"] for r in results}
    assert "Caleb Boyd" in names, f"Caleb Boyd not found; got {names}"
    assert "Kevin Bush" in names, f"Kevin Bush not found; got {names}"

    for r in results:
        assert r["match_type"] == "exact"
        assert r["company"] == "Molten Industries"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Fuzzy match — slight typo
# ─────────────────────────────────────────────────────────────────────────────

def test_fuzzy_match_typo() -> None:
    """'Molten Industres' (missing 'i') is close enough for fuzzy match."""
    results = lookup_company_for_fellow_match("Molten Industres")
    assert len(results) >= 1, (
        "Expected at least 1 fuzzy match for 'Molten Industres', got none"
    )
    assert results[0]["match_type"] == "fuzzy"
    # Canonical name should be the real company
    assert "Molten" in results[0]["company"]


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: No match — unknown company
# ─────────────────────────────────────────────────────────────────────────────

def test_no_match_unknown_company() -> None:
    """Lookup for a completely unknown company returns empty list."""
    results = lookup_company_for_fellow_match("Nonexistent Startup XYZ 99999")
    assert results == [], f"Expected [] for unknown company, got {results}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Business Fellows excluded from parsed output
# ─────────────────────────────────────────────────────────────────────────────

def test_business_fellow_excluded() -> None:
    """'Business Fellow' should not appear as a company key in parsed output."""
    from enrich.be_fellows_lookup import _normalize
    companies = parse_raw_file()
    business_fellow_key = _normalize("Business Fellow")
    assert business_fellow_key not in companies, (
        "Found 'Business Fellow' in companies dict — should have been filtered out"
    )
